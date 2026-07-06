"""Sprint 6h₁₀a (ADR-0104) / 6h₁₀b (ADR-0105) — harness-event → output renderer.

:class:`EventRenderer` is the TUI's :meth:`AgentHarness.subscribe` sink. It
mirrors the rpc/print frontends but renders to the TUI instead of JSONL/stdout.

Sprint 6h₁₀b rework (ADR-0105): output no longer goes to a Rich ``Live`` (the
live region is owned by the prompt-toolkit chrome). Instead the renderer emits
through two synchronous sinks the chrome shell wires up:

- ``commit(renderable)`` — a finished Rich renderable → scrollback (queued, then
  flushed above the chrome via ``in_terminal``).
- ``set_tail(ansi)`` — the in-progress streamed-text window → the chrome stream
  widget (``StreamRenderer`` owns the window/throttle logic).

Dispatch is ``match`` on the ``type`` Literal; unknown types are no-ops
(forward-compatible). Terminal failures surface on ``message_end`` via
``stop_reason`` (loop.py path; the streaming ``error`` event is never re-emitted
as a ``MessageUpdateEvent``). Out-of-band prints finalize any open text stream
first so they never interleave with the live tail.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aelix_ai.messages import AssistantMessage
from rich.cells import cell_len, set_cell_size
from rich.console import Group
from rich.text import Text

from .stream import StreamRenderer

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aelix_agent_core.contracts.descriptor import DescriptorEnvelope
    from aelix_agent_core.types import AgentEvent
    from aelix_ai.streaming import AssistantMessageEvent

    from .descriptors import DescriptorRenderer


def _result_text(result: Any) -> str:
    """Extract a printable string from a ``ToolResult`` (or any payload)."""

    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            parts.append(text if isinstance(text, str) else str(block))
        return "\n".join(parts)
    return str(content)


def _block_type(block: Any) -> Any:
    """Type discriminator tolerant of both dataclass blocks and raw dicts.

    ``TextContent``/``ImageContent`` objects expose ``.type`` as an attribute,
    but a ``custom_message`` entry's ``content`` is stored VERBATIM off the
    JSONL wire as ``[{"type": "text", "text": ...}]`` plain dicts
    (``entry_from_json``, no re-materialisation) — where ``type`` is a KEY,
    not an attribute (issue #62 review). Read both.
    """

    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _block_text(block: Any) -> str:
    if isinstance(block, dict):
        return block.get("text", "") or ""
    return getattr(block, "text", "") or ""


def _join_text(content: Any) -> str:
    """Join the ``TextContent`` blocks of a message body into one string.

    Non-text blocks (images) are skipped. Blocks may be dataclass content
    objects OR raw wire dicts (custom messages — see :func:`_block_type`).
    Defensive: a non-iterable / odd payload yields ``""`` rather than raising
    during transcript replay.
    """

    if not isinstance(content, (list, tuple)):
        return ""
    return "\n".join(
        _block_text(b) for b in content if _block_type(b) == "text"
    )


def component_to_text(component: Any, width: int) -> Text:
    """Snapshot a pi-tui ``Component`` (``render(width) -> list[str]`` of raw
    ANSI lines) into a Rich ``Text`` for the scrollback (issue #62, ADR-0183).

    The extension custom-message renderer returns a ``Component``; the shell
    closure converts it here so the (multi-line, ANSI) result is committed
    identically to a live-streamed block. Extracted module-level so the
    conversion is unit-testable (review MEDIUM: it was previously buried in a
    ``run_tui`` closure and never asserted).
    """

    lines = component.render(width)
    return Text.from_ansi("\n".join(str(line) for line in lines))


def _compact_args(args: dict[str, Any]) -> str:
    """One-line, length-capped argument summary for a tool header."""

    if not args:
        return ""
    items = ", ".join(f"{k}={v!r}" for k, v in args.items())
    items = items.replace("\n", " ")
    return items if len(items) <= 80 else items[:77] + "…"


def _truncate_lines(
    text: str, max_lines: int = 12, max_line_width: int = 76
) -> tuple[list[str], int]:
    """Keep the first ``max_lines`` lines, each hard-capped at ``max_line_width``.

    PURE. Returns ``(kept_lines, hidden_count)`` where ``hidden_count`` is the
    number of trimmed trailing lines. Width is measured in **terminal cells**
    (CJK / wide chars count as 2) via ``rich.cells`` so a line of Hangul doesn't
    overflow; a too-wide line is cut to ``max_line_width - 1`` cells plus ``…``.
    The default leaves room for the 2-cell ``│ `` card gutter within an 80-col
    chrome.
    """

    lines = text.split("\n")
    kept = lines[:max_lines]
    hidden = len(lines) - len(kept)
    capped: list[str] = [
        line if cell_len(line) <= max_line_width else set_cell_size(line, max_line_width - 1) + "…"
        for line in kept
    ]
    return capped, hidden


def _tool_header(tool_name: str, args: dict[str, Any]) -> str:
    """Tool-aware one-line argument summary for the ``●`` start header.

    ``read``/``write``/``edit`` show the ``path`` (read appends an
    ``offset:limit`` line range when present); ``bash`` shows the ``command``;
    every other tool falls back to :func:`_compact_args`.
    """

    if tool_name in ("read", "write", "edit"):
        path = args.get("path")
        if isinstance(path, str) and path:
            if tool_name == "read":
                offset = args.get("offset")
                limit = args.get("limit")
                if offset or limit:
                    # Args come from unvalidated model tool-call JSON — a
                    # non-numeric offset/limit must degrade to the bare path,
                    # not raise inside the (unguarded) start-header render.
                    try:
                        start = int(offset) if offset else 0
                        if limit:
                            return f"{path}:{start}-{start + int(limit)}"
                        return f"{path}:{start}-"
                    except (TypeError, ValueError):
                        return path
            return path
    elif tool_name == "bash":
        command = args.get("command")
        if isinstance(command, str) and command:
            one_line = command.replace("\n", " ")
            return one_line if len(one_line) <= 80 else one_line[:77] + "…"
    return _compact_args(args)


def _bash_exit_code(result: Any) -> int | None:
    """Extract a bash exit code from a ``ToolResult`` payload, else ``None``."""

    details = getattr(result, "details", None)
    code = getattr(details, "exit_code", None)
    return code if isinstance(code, int) else None


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


def _looks_like_diff(text: str) -> bool:
    """True for difflib-style unified-diff output.

    Requires a real ``@@ -n,n +n,n @@`` hunk header (not bare ``@@``) so
    ordinary output — markdown with a ``---`` rule, lines starting with
    ``+``/``-`` — is not mis-coloured as a diff.
    """
    return _HUNK_HEADER_RE.search(text) is not None


def _render_diff(text: str, *, max_lines: int = 40, expand_id: int | None = None) -> Group:
    """Colourise a unified diff: +green / -red / @@cyan / ---|+++ bold.

    ``expand_id`` (when the diff is truncated) is appended to the elision footer
    as a ``/expand N`` hint so the user can recover the full diff (ADR-0121).
    """
    kept, hidden = _truncate_lines(text, max_lines=max_lines)
    rows: list[Text] = []
    for line in kept:
        if line.startswith(("+++", "---")):
            style = "bold"
        elif line.startswith("@@"):
            style = "cyan"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        else:
            style = "dim"
        rows.append(Text(line, style=style))
    if hidden > 0:
        suffix = f" · /expand {expand_id}" if expand_id is not None else ""
        hint = f"… (+{hidden} more lines{suffix})"
        rows.append(Text(hint, style="dim"))
    return Group(*rows)


# Sprint 6h₂₅ (ADR-0153, TUI v2 quick-wins WP-6 trivial tier) — shared user-echo
# vocabulary. Human input was the weakest visual element (monochrome ``» text``
# with no separation, buried among colored tool cards / diffs / thinking). The
# trivial-tier lift = ONE helper every human-input site shares: a leading blank
# line for separation + a bold-cyan echo that stands out. (The full-width
# background bubble — WP-6 medium tier — is deferred.)
_USER_MESSAGE_LABELS: dict[str, str] = {
    "prompt": "» ",
    "steer": "Steering: ",
    "follow_up": "Follow-up: ",
}

# Sprint 6h₃₂ — the tool-call header marker. A bold filled ``●`` (replacing the
# thin ``⚙`` gear) reads at a glance against the dim result card below it; the
# tool NAME is bolded so it stands out from its argument summary. Shared by the
# live render path and transcript replay via :func:`render_tool_call_line`.
_TOOL_MARKER = "●"


def render_user_message(text: str, kind: str = "prompt") -> Group:
    """Build the canonical echo for a human turn (Sprint 6h₂₅, ADR-0153).

    Every site that echoes the user's own input — the live prompt, the replayed
    transcript, and steer / follow-up injections — routes through this helper so
    they share ONE visual language: the echo line is wrapped in blank lines ABOVE
    AND BELOW (vertical padding that fences the human turn off from the colored
    tool cards / diffs / streamed answer it sits between) and styled to STAND OUT
    (bold cyan). Sprint 6h₃₂ added the trailing blank — a single LEADING blank
    (the ADR-0153 original) was too subtle when the turn landed mid-stream.

    ``kind`` selects the leading marker: ``"prompt"`` keeps the ``» `` chevron;
    ``"steer"`` / ``"follow_up"`` use a distinct ``Steering: `` / ``Follow-up: ``
    label but the SAME padding + bold-cyan visual language. An unknown kind
    degrades to the prompt chevron.
    """

    label = _USER_MESSAGE_LABELS.get(kind, _USER_MESSAGE_LABELS["prompt"])
    return Group(Text(""), Text(f"{label}{text}", style="bold cyan"), Text(""))


def render_tool_call_line(tool_name: str, summary: str) -> Text:
    """Build the styled one-line header for a tool call (Sprint 6h₃₂).

    A bold ``●`` marker (more visible than the prior thin ``⚙`` gear) + the tool
    NAME in bold, then the argument ``summary`` in the default card weight inside
    parentheses. The whole line keeps the card's cyan hue; only the marker and
    name are bolded so the name reads first and the args stay secondary. Shared
    by the live (:meth:`EventRenderer._render_tool_start`) and replayed
    (:meth:`EventRenderer.replay`) paths so a resumed transcript is pixel-identical
    to a freshly-streamed turn.
    """

    line = Text()
    line.append(f"{_TOOL_MARKER} ", style="bold cyan")
    line.append(tool_name, style="bold cyan")
    if summary:
        line.append(f"({summary})", style="cyan")
    return line


class EventRenderer:
    """Renders the harness :data:`AgentEvent` stream via commit/tail sinks.

    :param commit: sync sink for a finished Rich renderable (→ scrollback).
    :param set_tail: sync sink for the in-progress streamed-text window (→ chrome).
    :param width: render width for streamed text.
    """

    def __init__(
        self,
        *,
        commit: Callable[[object], None],
        set_tail: Callable[[str], None],
        width: int = 80,
    ) -> None:
        self._commit = commit
        self._set_tail = set_tail
        self._width = width
        self._text_stream: StreamRenderer | None = None
        self._text_accum: str = ""
        self._thinking_accum: str = ""
        # /expand support (ADR-0121) — full, untruncated tool-result bodies kept
        # by sequential id so ``/expand N`` can recover the text a truncated card
        # elided. Only TRUNCATED cards get an id (that's when /expand is useful);
        # the id is surfaced on the truncation footer (``… /expand N``). Bounded
        # so a long session can't grow this without limit (oldest dropped first).
        self._expand_store: dict[int, str] = {}
        self._expand_seq: int = 0
        self._expand_max: int = 100
        # Issue #66 (TUI polish) — configurable cap on the NORMAL tool-card output
        # body. Default 12 (unchanged behaviour); run_tui seeds this from the
        # persisted ``get_tool_card_max_lines()`` setting (clamped [3, 40]). Governs
        # ONLY the normal-output card path in _render_tool_end — the separate
        # 40-line diff/error cap is a distinct literal and stays 40.
        self.tool_card_max_lines: int = 12
        # Thinking is buffered during ``thinking_delta`` and flushed (dim
        # italic) BEFORE the text/tool block that follows it — not at the
        # ``thinking_end`` event, which the adapter emits at end-of-stream
        # (after the text already streamed live), causing reasoning to print
        # *after* the answer. This flag makes the flush idempotent so the
        # late ``thinking_end`` does not double-render. (ADR-0115.)
        self._thinking_flushed: bool = False
        # Thinking-block visibility (Sprint 6h₁₅, ADR-0123). Default VISIBLE to
        # match pi's ``hideThinkingBlock`` default (False) — issue #50 reconcile:
        # the run_tui startup seed overwrites this from the persisted setting, so
        # this hardcoded default only governs headless / no-settings contexts and
        # must agree with the settings default (visible). When HIDDEN (persisted
        # setting or live Ctrl+T), pi shows an italic "Thinking…" placeholder to
        # keep reasoning-heavy models from flooding the transcript. Aelix
        # divergence from pi: pi's Ctrl+T rebuilds the whole chat to retroactively
        # toggle PAST blocks; inline scrollback can't, so the toggle affects
        # subsequent renders — but a collapsed block is routed through the /expand
        # store so its full reasoning stays recoverable (``💭 Thinking… (/expand N)``).
        self.hide_thinking: bool = False
        self._hidden_thinking_label: str = "Thinking…"
        # §B — live tool-result interception. Late-bound by run_tui to read the
        # descriptor registry by reference (returns a matching tool-renderer-desc
        # envelope for a tool_name, or None). ``descriptor_renderer`` builds the
        # custom view. Both unset → default Text-dump rendering (unchanged).
        self.get_tool_renderer_desc: Callable[[str], DescriptorEnvelope | None] | None = None
        self.descriptor_renderer: DescriptorRenderer | None = None
        # Issue #62 (ADR-0183) — extension custom-message rendering hook.
        # Late-bound by run_tui: given a DISPLAY-tier custom message (rich
        # ``CustomMessage`` with custom_type/content/details), returns a Rich
        # renderable from the extension's registered MessageRenderer, or None
        # to fall through to the default rendering (the get_tool_renderer_desc
        # idiom). Unset (headless/tests) → default rendering.
        self.render_custom_message: Callable[[Any], object | None] | None = None

    def on_agent_event(self, event: AgentEvent) -> None:
        if event.type == "message_start":
            self._reset_message_state()
        elif event.type == "message_update":
            self._on_stream_event(event.assistant_message_event)
        elif event.type == "message_end":
            # Terminal failures arrive here as a MessageEndEvent whose message
            # carries stop_reason ∈ {"error","aborted"} (loop.py:299-310).
            self._finalize_text()
            self._render_message_error(event.message)
        elif event.type == "turn_end":
            self._finalize_text()
        elif event.type == "tool_execution_start":
            self._render_tool_start(event.tool_name, event.args)
        elif event.type == "tool_execution_end":
            self._render_tool_end(event.tool_name, event.result, event.is_error)
        # tool_execution_update / turn_start / agent_* / unknown → no-op.

    def finalize(self) -> None:
        """Close any open streamed-text window (e.g. after a turn error)."""

        self._finalize_text()

    # === streaming-layer dispatch ==========================================

    def _on_stream_event(self, sev: AssistantMessageEvent) -> None:
        if sev.type == "text_delta":
            if self._text_stream is None:
                # Render buffered reasoning ABOVE the answer it preceded.
                self._flush_thinking(self._thinking_accum)
                self._text_stream = self._new_stream()
            self._text_accum += sev.delta
            self._text_stream.update(self._text_accum)
        elif sev.type == "text_end":
            if sev.content:
                self._text_accum = sev.content
            self._finalize_text()
        elif sev.type == "thinking_delta":
            self._thinking_accum += sev.delta
        elif sev.type == "thinking_end":
            self._flush_thinking(sev.content or self._thinking_accum)
        elif sev.type in ("done", "end"):
            self._finalize_text()
        elif sev.type == "error":
            self._finalize_text()
            message = sev.error_message or f"request {sev.reason}"
            self._commit(Text(f"✖ {message}", style="bold red"))

    # === helpers ===========================================================

    def _new_stream(self) -> StreamRenderer:
        return StreamRenderer(
            commit=lambda ansi: self._commit(Text.from_ansi(ansi)),
            set_tail=self._set_tail,
            width=self._width,
        )

    def _reset_message_state(self) -> None:
        self._finalize_text()
        self._text_accum = ""
        self._thinking_accum = ""
        self._thinking_flushed = False

    def _finalize_text(self) -> None:
        if self._text_stream is not None:
            self._text_stream.update(self._text_accum, final=True)
            self._text_stream = None
            self._text_accum = ""

    def _render_message_error(self, message: object) -> None:
        if isinstance(message, AssistantMessage) and message.stop_reason in (
            "error",
            "aborted",
        ):
            detail = message.error_message or f"request {message.stop_reason}"
            self._commit(Text(f"✖ {detail}", style="bold red"))

    def _flush_thinking(self, content: str) -> None:
        self._finalize_text()
        text = content.strip()
        self._thinking_accum = ""
        if self._thinking_flushed:
            # Already rendered for this message (the late ``thinking_end``
            # carries the same content) — don't print it a second time.
            return
        self._thinking_flushed = True
        if not text:
            return
        if self.hide_thinking:
            # Collapsed: a one-line placeholder; stash the full reasoning in the
            # /expand store so it stays recoverable (``/expand N``).
            n = self._store_expandable(text)
            self._commit(
                Text(f"💭 {self._hidden_thinking_label} (/expand {n})", style="dim italic")
            )
        else:
            self._commit(Text(text, style="dim italic"))

    def _render_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        # Reasoning that preceded a tool call renders above its card too.
        self._flush_thinking(self._thinking_accum)
        self._finalize_text()
        summary = _tool_header(tool_name, args)
        self._commit(render_tool_call_line(tool_name, summary))

    def _render_tool_end(self, tool_name: str, result: Any, is_error: bool) -> None:
        self._finalize_text()
        text = _result_text(result).rstrip()
        # §B — a stored tool-renderer-desc for this tool_name renders a custom view
        # (table/grid/form/text) instead of the default Text dump. The default
        # rendering is unchanged whenever no descriptor matches (or the lookup /
        # build raises — a faulty renderer must not swallow tool output). A
        # matched descriptor keeps full precedence: no truncation is applied.
        if self._render_with_descriptor(tool_name, text):
            return
        exit_code = _bash_exit_code(result) if tool_name == "bash" else None
        if not text:
            return
        # §C′ (ADR-0138) — the edit tool now returns a SUCCESS MESSAGE as content
        # and its diff in ``details`` (Pi parity). Surface the colorized diff from
        # details: pi's diff is a line-numbered +/- format (NOT a ``@@`` unified
        # diff), so _looks_like_diff would miss it, but _render_diff colours by
        # the +/- prefix and renders it correctly.
        if not is_error and tool_name == "edit":
            diff_text = getattr(getattr(result, "details", None), "diff", "")
            if isinstance(diff_text, str) and diff_text.strip():
                _, diff_hidden = _truncate_lines(diff_text, max_lines=40)
                expand_id = (
                    self._store_expandable(diff_text) if diff_hidden > 0 else None
                )
                diff_group = _render_diff(diff_text, expand_id=expand_id)
                self._commit(Group(Text(text, style="green"), *diff_group.renderables))
                return
        # §C (ADR-0116) — diff-shaped tool output (edit/write difflib, or a
        # bash `git diff`) renders with +/- colour instead of flat dim text.
        # Errors keep the red card below (a failed edit isn't a diff to review).
        if not is_error and _looks_like_diff(text):
            _, diff_hidden = _truncate_lines(text, max_lines=40)
            expand_id = self._store_expandable(text) if diff_hidden > 0 else None
            diff_group = _render_diff(text, expand_id=expand_id)
            if exit_code is not None and exit_code != 0:
                # Preserve the bash exit footer for diff-shaped output that
                # still reports a non-zero exit (e.g. `git diff --exit-code`).
                self._commit(
                    Group(*diff_group.renderables, Text(f"exit {exit_code}", style="red"))
                )
            else:
                self._commit(diff_group)
            return
        # §A — truncated, styled card under the ● header: a dim left-gutter block
        # (red when is_error), with a "+N more lines" footer when truncated and an
        # "exit N" footer for non-zero/failed bash. One committed renderable.
        # Error output is head-truncated too, but a Python traceback's diagnostic
        # tail (the exception type/message) lives at the bottom — so give errors a
        # higher cap to keep that visible (full detail still via a future /expand).
        kept, hidden = _truncate_lines(
            text, max_lines=40 if is_error else self.tool_card_max_lines
        )
        body_style = "red" if is_error else "dim"
        rows: list[Text] = [Text(f"│ {line}", style=body_style) for line in kept]
        if hidden > 0:
            expand_id = self._store_expandable(text)
            rows.append(
                Text(f"│ … (+{hidden} more lines · /expand {expand_id})", style="dim")
            )
        if exit_code is not None and exit_code != 0:
            rows.append(Text(f"│ exit {exit_code}", style="red"))
        self._commit(Group(*rows))

    def _store_expandable(self, full_text: str) -> int:
        """Retain ``full_text`` under a fresh id; return the id (for ``/expand``).

        Bounded to ``_expand_max`` entries — the oldest id is dropped when the
        store is full so a long session can't grow it without limit.
        """

        self._expand_seq += 1
        n = self._expand_seq
        self._expand_store[n] = full_text
        if len(self._expand_store) > self._expand_max:
            oldest = min(self._expand_store)
            del self._expand_store[oldest]
        return n

    def get_expanded(self, n: int) -> str | None:
        """Return the full, untruncated body stored for ``/expand N`` (or None)."""

        return self._expand_store.get(n)

    def reset_expand_store(self) -> None:
        """Drop all ``/expand`` ids + thinking flush state (W-review 6h₁₅ MEDIUM).

        The :class:`EventRenderer` is long-lived and reused across a session
        swap (``/new`` / ``/resume``), but ``/expand`` ids are scoped to the
        visible transcript — without this, after a swap ``/expand N`` would still
        return the PREVIOUS session's body (a cross-session leak, now widened by
        collapsed thinking landing in the same store). Called from ``run_tui``'s
        rebind seam so it fires on every swap.
        """

        self._expand_store.clear()
        self._expand_seq = 0
        self._thinking_flushed = False

    def replay(self, messages: list[Any]) -> None:
        """Re-render a loaded session's static messages into scrollback.

        pi ``renderCurrentSessionState`` parity (Sprint 6h₁₄b, ADR-0122): used by
        ``/resume`` after a session hot-swap to repaint the resumed transcript.
        Reuses the live helpers (``_tool_header``, ``_render_tool_end``) so a
        resumed transcript looks identical to a freshly-streamed one; truncated
        tool-result cards are stored too, so ``/expand`` works on them.

        Static (no streaming) — never opens a text-stream window. Each message:
        user → ``» {text}``; assistant → thinking (dim italic) + text + ``●``
        tool-call headers + a terminal-error line; toolResult → the result card.
        """

        self._finalize_text()  # belt-and-braces: no open stream during replay
        for msg in messages:
            role = getattr(msg, "role", None)
            if role == "user":
                text = _join_text(getattr(msg, "content", []))
                if text.strip():
                    # Sprint 6h₂₅ (ADR-0153) — shared user-echo vocabulary so a
                    # replayed transcript echoes input identically to a live turn.
                    self._commit(render_user_message(text))
            elif role == "assistant":
                for block in getattr(msg, "content", []) or []:
                    btype = getattr(block, "type", None)
                    if btype == "thinking":
                        thinking = (getattr(block, "thinking", "") or "").strip()
                        if thinking:
                            self._commit(Text(thinking, style="dim italic"))
                    elif btype == "text":
                        body = getattr(block, "text", "") or ""
                        if body.strip():
                            self._commit(Text(body))
                    elif btype == "toolCall":
                        name = getattr(block, "tool_name", "") or ""
                        summary = _tool_header(name, getattr(block, "input", {}) or {})
                        self._commit(render_tool_call_line(name, summary))
                stop = getattr(msg, "stop_reason", None)
                if stop in ("error", "aborted"):
                    detail = getattr(msg, "error_message", None) or f"request {stop}"
                    self._commit(Text(f"✖ {detail}", style="bold red"))
            elif role == "toolResult":
                # _render_tool_end reads result.content / .is_error and applies
                # the same truncation + /expand-store as a live tool card.
                self._render_tool_end(
                    getattr(msg, "tool_name", "") or "",
                    msg,
                    bool(getattr(msg, "is_error", False)),
                )
            elif role == "custom":
                # Issue #62 (ADR-0183) — DISPLAY-tier custom message (rich
                # ``CustomMessage`` from build_display_messages). The display
                # gate fires BEFORE any renderer lookup (pi
                # interactive-mode.ts:3109-3116): display=False stays in the
                # LLM context but never renders.
                if getattr(msg, "display", False):
                    self._render_custom(msg)

    def _render_custom(self, msg: Any) -> None:
        """Render one custom message: extension hook, else default.

        Pi ``CustomMessageComponent.rebuild()`` parity
        (``custom-message.ts:58-97``): a hook failure or ``None`` falls
        through SILENTLY to the default rendering — a bold ``[custom_type]``
        label + the plain content text (pi draws a themed box + markdown;
        Aelix scrollback is plain-text-first, divergence noted in ADR-0183).
        """

        hook = self.render_custom_message
        if hook is not None:
            try:
                renderable = hook(msg)
                if renderable is not None:
                    self._commit(renderable)
                    return
            except Exception:  # noqa: BLE001 — pi swallows renderer errors and
                # falls back (custom-message.ts:68-70). Aelix keeps the silent
                # FALLBACK (a bad renderer must not break replay) but, per the
                # "every skip/failure logs" convention (ADR-0181), records it at
                # DEBUG so a plugin dev can diagnose — no per-message warning spam.
                logger.debug(
                    "custom-message renderer failed for %r; using default rendering",
                    getattr(msg, "custom_type", None),
                    exc_info=True,
                )
        content = getattr(msg, "content", None)
        text = content if isinstance(content, str) else _join_text(content or [])
        label = Text(f"[{getattr(msg, 'custom_type', '') or 'custom'}]", style="bold magenta")
        if text.strip():
            self._commit(Group(label, Text(text)))
        else:
            self._commit(label)

    def _render_with_descriptor(self, tool_name: str, text: str) -> bool:
        lookup = self.get_tool_renderer_desc
        renderer = self.descriptor_renderer
        if lookup is None or renderer is None or not tool_name:
            return False
        try:
            envelope = lookup(tool_name)
            if envelope is None:
                return False
            rows = renderer.project_tool_result(envelope, text)
            self._commit(renderer.build_tool_renderable(envelope, rows))
        except Exception:  # noqa: BLE001 — fall back to default on any failure
            return False
        return True


__all__ = [
    "EventRenderer",
    "component_to_text",
    "render_tool_call_line",
    "render_user_message",
    "_tool_header",
    "_truncate_lines",
]
