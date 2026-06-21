"""Purpose-built tool-approval dialog (WP-0 STEP 5, ADR-0157).

Replaces the generic filterable :meth:`AelixTUIContext.select` for the
permission prompt — that select() showed a nonsensical "Type to search" hint on
a yes/no, truncated the command to 120 chars, and offered no diff preview. This
module is a dedicated, purpose-built dialog mirroring the
``model_picker`` / ``thinking_picker`` shape:

- pure, side-effect-free :func:`build_approval_view` renders the dialog body to
  ANSI lines (a bordered Rich Panel with the FULL untruncated command + a diff
  preview), unit-testable without prompt-toolkit;
- a dependency-injected :func:`run_approval_dialog` drives the 3 STATIC options
  (Yes / Yes, for this session / No) with ↑/↓ + Enter + digit + mnemonic key
  bindings, NO type-to-filter, NO truncation, and NO space-confirm (so a stray
  space can't auto-approve the default "Yes"). The modal runner (``show_modal``)
  is injected so the whole flow is testable headlessly. ``NO_REASON`` is a
  fallback-only decision (the generic ``ctx.ui`` path), not a dialog row.

The generic ``AelixTUIContext.select`` is deliberately left untouched so
``/settings`` / ``/resume`` / ``/model`` / ``/thinking`` keep their behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

# Bounded render width — matches the ``custom()`` overlay precedent
# (``_RENDER_WIDTH = 80``) so the Panel border never wraps/clips the Float.
_RENDER_WIDTH = 80
# Max diff/body lines shown inline before eliding (parity with _render_diff's cap).
_MAX_BODY_LINES = 40


class ApprovalDecision(StrEnum):
    """The user's answer to a tool-approval prompt."""

    YES = "yes"
    YES_SESSION = "yes_session"
    NO = "no"
    NO_REASON = "no_reason"
    CANCEL = "cancel"


@dataclass
class ApprovalRequest:
    """A single tool-approval request for the dialog.

    ``kind`` selects the body rendering: ``bash`` shows the full command,
    ``write`` shows an empty→content diff, ``edit`` shows an old→new block per
    edit, ``other`` shows the raw arg summary.
    """

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    kind: str = "other"  # "bash" | "write" | "edit" | "other"


# Static dialog rows (order is the displayed order + the digit shortcut order).
# NOTE (nit WP-0): :data:`ApprovalDecision.NO_REASON` is deliberately NOT a row
# here — on the purpose-built dialog path the runner resolves immediately and
# never collects a free-text reason, so showing a "No, provide reason" option
# would be a no-op (functionally identical to "No"). Reason-capture is a future
# enhancement (open a follow-up input box); until then NO_REASON exists only as
# a fallback handled by the generic ``ctx.ui`` path in ``permission.py``.
_ROWS: tuple[tuple[ApprovalDecision, str, str], ...] = (
    (ApprovalDecision.YES, "y", "Yes"),
    (ApprovalDecision.YES_SESSION, "s", "Yes, for this session"),
    (ApprovalDecision.NO, "n", "No"),
)


def _bash_command(args: dict[str, Any]) -> str:
    for key in ("command", "cmd", "shell_command", "script"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _path(args: dict[str, Any]) -> str:
    for key in ("path", "file_path", "file", "filename", "filepath", "target"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _content(args: dict[str, Any]) -> str:
    for key in ("content", "contents", "text", "new_content", "data"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _synth_write_diff(path: str, content: str) -> str:
    """An empty→content unified-ish diff for a create/overwrite (no file read)."""

    lines = [f"--- {path}", f"+++ {path}"]
    body = content.splitlines() or [""]
    for line in body:
        lines.append(f"+{line}")
    return "\n".join(lines)


def _synth_edit_diff(args: dict[str, Any]) -> str:
    """An old→new block per edit (``edits[].oldText/newText``); never crashes.

    The gate runs PRE-execution and we must NOT read the file, so this is a
    simple per-edit old→new block. Any malformed edit falls back to a verbatim
    dump of its raw text rather than raising.
    """

    edits = args.get("edits")
    blocks: list[str] = []
    if isinstance(edits, (list, tuple)) and edits:
        for edit in edits:
            old = _edit_field(edit, ("oldText", "old_text", "old"))
            new = _edit_field(edit, ("newText", "new_text", "new"))
            blocks.append(_old_new_block(old, new))
    else:
        # Single-edit shape (oldText/newText directly on args).
        old = _edit_field(args, ("oldText", "old_text", "old", "old_string"))
        new = _edit_field(args, ("newText", "new_text", "new", "new_string"))
        blocks.append(_old_new_block(old, new))
    return "\n".join(b for b in blocks if b)


def _edit_field(obj: Any, keys: tuple[str, ...]) -> str:
    for key in keys:
        try:
            value = obj.get(key) if hasattr(obj, "get") else getattr(obj, key, None)
        except Exception:  # noqa: BLE001 — malformed edit → fall through
            value = None
        if isinstance(value, str):
            return value
    return ""


def _old_new_block(old: str, new: str) -> str:
    lines: list[str] = []
    for line in (old.splitlines() or ([old] if old else [])):
        lines.append(f"-{line}")
    for line in (new.splitlines() or ([new] if new else [])):
        lines.append(f"+{line}")
    return "\n".join(lines)


def _panel_to_ansi(title: str, body: Any, width: int) -> list[str]:
    """Render a bordered Rich Panel containing ``body`` to ANSI lines.

    A recording :class:`rich.console.Console` captures the styled output;
    failure (e.g. Rich missing in a degraded env) falls back to plain text so
    the dialog never crashes.
    """

    try:
        from rich.console import Console  # noqa: PLC0415 — optional in degraded env
        from rich.panel import Panel

        console = Console(width=width, record=True, file=_NullFile())
        console.print(Panel(body, title=title, expand=False, width=width))
        text = console.export_text(styles=True)
        return text.splitlines()
    except Exception:  # noqa: BLE001 — headless / no-rich fallback
        return [title, *(str(body).splitlines())]


class _NullFile:
    """A write sink for the recording Console (it records, never emits)."""

    def write(self, _data: str) -> None:  # noqa: D401
        return None

    def flush(self) -> None:
        return None


def build_approval_view(
    request: ApprovalRequest,
    *,
    render_diff: Callable[..., Any] | None = None,
    max_lines: int = _MAX_BODY_LINES,
    width: int = _RENDER_WIDTH,
) -> list[str]:
    """Build the dialog body as ANSI lines (PURE — no prompt-toolkit / I/O).

    - bash → "Run command:" + the FULL untruncated command.
    - write → "Create/overwrite {path}" + an empty→content diff (capped).
    - edit → "Edit {path}" + an old→new block per edit (verbatim fallback).
    - other → a compact arg summary.

    ``render_diff`` (default :func:`render._render_diff`) colours the diff so it
    matches the transcript; a ``None`` / raising callback degrades to plain
    diff text — never crashes.
    """

    from rich.console import Group  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    rd = render_diff if render_diff is not None else _default_render_diff()

    if request.kind == "bash":
        command = _bash_command(request.args)
        title = "Run shell command?"
        body: Any = Group(
            Text("Run command:", style="bold"),
            Text(command or "(empty)", style="yellow"),
        )
    elif request.kind == "write":
        path = _path(request.args)
        diff_text = _synth_write_diff(path, _content(request.args))
        title = f"Create/overwrite {path or '(unknown path)'}?"
        body = Group(Text(f"Create/overwrite {path}", style="bold"), _safe_diff(rd, diff_text, max_lines))
    elif request.kind == "edit":
        path = _path(request.args)
        diff_text = _synth_edit_diff(request.args)
        title = f"Edit {path or '(unknown path)'}?"
        body = Group(Text(f"Edit {path}", style="bold"), _safe_diff(rd, diff_text, max_lines))
    else:
        title = f"Allow {request.tool_name}?"
        summary = ", ".join(f"{k}={v!r}" for k, v in list(request.args.items())[:6])
        body = Group(Text(f"Tool: {request.tool_name}", style="bold"), Text(summary, style="dim"))

    return _panel_to_ansi(title, body, width)


def _safe_diff(render_diff: Callable[..., Any], diff_text: str, max_lines: int) -> Any:
    from rich.text import Text  # noqa: PLC0415

    if not diff_text:
        return Text("(no changes to preview)", style="dim")
    try:
        return render_diff(diff_text, max_lines=max_lines)
    except Exception:  # noqa: BLE001 — never let a diff render break the prompt
        return Text(diff_text)


def _default_render_diff() -> Callable[..., Any]:
    from aelix_coding_agent.tui.render import _render_diff  # noqa: PLC0415

    return _render_diff


def build_options_view(selected: int) -> list[str]:
    """The 4 static option rows with a ``→`` marker on ``selected`` (PURE)."""

    rows: list[str] = []
    for i, (_decision, mnemonic, label) in enumerate(_ROWS):
        marker = "→ " if i == selected else "  "
        rows.append(f"{marker}{i + 1}. [{mnemonic}] {label}")
    rows.append("  ↑/↓ to move · 1-3 / y·s·n · Enter to confirm · Esc to deny")
    return rows


async def run_approval_dialog(
    *,
    request: ApprovalRequest,
    show_modal: Callable[..., Awaitable[Any]],
    chrome: Any,
    render_diff: Callable[..., Any] | None = None,
    width: int = _RENDER_WIDTH,
) -> ApprovalDecision:
    """Drive the approval dialog and return the chosen :class:`ApprovalDecision`.

    Dependency-injected (``show_modal`` + ``chrome`` are passed in) so the whole
    flow is unit-testable without standing up the prompt-toolkit app. Esc /
    Ctrl+C / an unknown key resolves to :data:`ApprovalDecision.CANCEL`
    (fail-safe deny).

    Sprint 6h₂₈ (ADR-0159, review HIGH) — the dialog is an ``HSplit`` of a
    SCROLLABLE body window over a FIXED-height options window. ``show_modal``
    height-caps the whole modal to the terminal; an HSplit shrinks its flexible
    child (the body) first and keeps the fixed child (the Yes/No option rows) at
    full height, so the security-critical deny option is ALWAYS visible even when
    the diff body is far taller than the cap. The body scrolls (PageUp/PageDown,
    a cursor-tracking control so prompt-toolkit's scroll-to-cursor reaches the
    bottom) instead of clipping its overflow off the terminal.
    """

    from prompt_toolkit.data_structures import Point  # noqa: PLC0415
    from prompt_toolkit.formatted_text import ANSI  # noqa: PLC0415
    from prompt_toolkit.key_binding import KeyBindings  # noqa: PLC0415
    from prompt_toolkit.layout import HSplit, ScrollOffsets, Window  # noqa: PLC0415
    from prompt_toolkit.layout.controls import FormattedTextControl  # noqa: PLC0415
    from prompt_toolkit.layout.dimension import Dimension  # noqa: PLC0415

    body_lines = build_approval_view(request, render_diff=render_diff, width=width)
    # ``scroll`` is the body line the cursor sits on — moving it lets ptk's
    # scroll-to-cursor reveal the rest of an over-tall body. ``idx`` is the
    # highlighted option row.
    state = {"idx": 0, "scroll": 0}
    last_body = max(0, len(body_lines) - 1)

    def _render_body() -> str:
        return "\n".join(body_lines)

    def _body_cursor() -> Point:
        # Track the cursor on the active scroll line so scroll_offsets keep it
        # (and therefore the surrounding lines) within the windowed body region.
        return Point(x=0, y=max(0, min(state["scroll"], last_body)))

    def _render_options() -> str:
        return "\n".join(build_options_view(state["idx"]))

    def build(result: asyncio.Future[Any]) -> HSplit:
        kb = KeyBindings()

        def _resolve(value: ApprovalDecision) -> None:
            if not result.done():
                result.set_result(value)

        def _confirm(_e: object) -> None:
            _resolve(_ROWS[state["idx"]][0])

        @kb.add("up")
        def _up(_e: object) -> None:
            state["idx"] = (state["idx"] - 1) % len(_ROWS)
            chrome.invalidate()

        @kb.add("down")
        def _down(_e: object) -> None:
            state["idx"] = (state["idx"] + 1) % len(_ROWS)
            chrome.invalidate()

        # PageUp/PageDown (and Ctrl+Up/Ctrl+Down) scroll the body so a diff taller
        # than the height cap stays fully reachable; option nav keeps ↑/↓.
        def _scroll(delta: int) -> None:
            state["scroll"] = max(0, min(state["scroll"] + delta, last_body))
            chrome.invalidate()

        kb.add("pageup")(lambda _e: _scroll(-5))
        kb.add("pagedown")(lambda _e: _scroll(5))
        kb.add("c-up")(lambda _e: _scroll(-1))
        kb.add("c-down")(lambda _e: _scroll(1))

        kb.add("enter")(_confirm)
        kb.add("c-j")(_confirm)
        # NOTE (nit WP-0): ``space`` is deliberately NOT a confirm key here. The
        # default-highlighted row is "Yes" (allow), so a stray space on a
        # security prompt would auto-approve a mutating tool. Require an explicit
        # Enter / digit / mnemonic instead.

        # Digit shortcuts select + confirm immediately.
        for i, (decision, mnemonic, _label) in enumerate(_ROWS):
            kb.add(str(i + 1))(lambda _e, d=decision: _resolve(d))
            kb.add(mnemonic)(lambda _e, d=decision: _resolve(d))
            kb.add(mnemonic.upper())(lambda _e, d=decision: _resolve(d))

        kb.add("escape")(lambda _e: _resolve(ApprovalDecision.CANCEL))
        kb.add("c-c")(lambda _e: _resolve(ApprovalDecision.CANCEL))

        # The body is FLEXIBLE (shrinks under the cap → scrolls); the options
        # window is FIXED at exactly its row count (it is the cursor/focus owner
        # so the dialog navigates), pinned below the body OUTSIDE the cap's
        # squeeze so Yes/No can never be clipped. A blank spacer separates them.
        n_option_rows = len(_ROWS) + 1  # rows + the hint line
        body_window = Window(
            FormattedTextControl(
                lambda: ANSI(_render_body()), get_cursor_position=_body_cursor
            ),
            scroll_offsets=ScrollOffsets(top=1, bottom=1),
            wrap_lines=False,
        )
        options_window = Window(
            FormattedTextControl(
                lambda: ANSI(_render_options()), focusable=True, key_bindings=kb
            ),
            height=Dimension.exact(n_option_rows),
            dont_extend_height=True,
        )
        spacer = Window(height=Dimension.exact(1))
        return HSplit([body_window, spacer, options_window])

    decision = await show_modal(chrome, build)
    return decision if isinstance(decision, ApprovalDecision) else ApprovalDecision.CANCEL


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "build_approval_view",
    "build_options_view",
    "run_approval_dialog",
]
