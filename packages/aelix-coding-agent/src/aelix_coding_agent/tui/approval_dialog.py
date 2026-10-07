"""Purpose-built tool-approval dialog (WP-0 STEP 5, ADR-0157).

Replaces the generic filterable :meth:`AelixTUIContext.select` for the
permission prompt — that select() showed a nonsensical "Type to search" hint on
a yes/no, truncated the command to 120 chars, and offered no diff preview. This
module is a dedicated, purpose-built dialog mirroring the
``model_picker`` / ``thinking_picker`` shape:

- pure, side-effect-free :func:`build_approval_view` renders the dialog body to
  ANSI lines (a bordered Rich Panel with the FULL untruncated command, or the
  whole diff of a write / edit — nothing elided, #389), unit-testable without
  prompt-toolkit;
- a dependency-injected :func:`run_approval_dialog` drives the 3 STATIC options
  (Yes / Yes, for this session / No) with ↑/↓ + Enter + digit + mnemonic key
  bindings, NO type-to-filter, NO truncation, and NO space-confirm (so a stray
  space can't auto-approve the default "Yes"). The modal runner (``show_modal``)
  is injected so the whole flow is testable headlessly. ``NO_REASON`` is a
  fallback-only decision (the generic ``ctx.ui`` path), not a dialog row. Yes
  waits until every line of the body has been on screen (:class:`_BodyViewport`,
  ADR-0253 §9 and §11).

The generic ``AelixTUIContext.select`` keeps its own shape so ``/settings`` /
``/resume`` / ``/model`` / ``/thinking`` keep their behaviour. It does NOT wrap
or hold a long title: an extension's ``select`` / ``confirm`` question is cut at
the screen edge as before, and that surface is issue #399 (ADR-0253 §11).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from typing import IO, TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

    from prompt_toolkit.formatted_text import StyleAndTextTuples

# Bounded render width — matches the ``custom()`` overlay precedent
# (``_RENDER_WIDTH = 80``) so the Panel border never wraps/clips the Float.
_RENDER_WIDTH = 80


class ApprovalDecision(StrEnum):
    """The user's answer to a tool-approval prompt."""

    YES = "yes"
    YES_SESSION = "yes_session"
    NO = "no"
    NO_REASON = "no_reason"
    CANCEL = "cancel"
    # Issue #161 shape 3. NOT a variant of NO: the write is allowed, at the
    # OTHER of the two extension targets. The caller performs the redirect by
    # mutating the tool args — see ``builtin/permission.py``.
    REDIRECT = "redirect"


@dataclass
class ApprovalRequest:
    """A single tool-approval request for the dialog.

    ``kind`` selects the body rendering: ``bash`` shows the full command,
    ``write`` shows an empty→content diff, ``edit`` shows an old→new block per
    edit, ``other`` shows every raw argument (:func:`argument_rows`).

    ``redirect_label`` / ``yes_label`` are issue #161 shape 3. When
    ``redirect_label`` is set the dialog grows a fourth row offering the OTHER
    extension target, and ``yes_label`` renames "Yes" to say where "yes" leads
    — because the whole defect was that the user could not see they were
    answering a question with two right answers. Both default to ``None`` and
    every other approval keeps the three static rows byte-for-byte.
    """

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    kind: str = "other"  # "bash" | "write" | "edit" | "other"
    yes_label: str | None = None
    redirect_label: str | None = None


# Dialog rows (order is the displayed order + the digit shortcut order).
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


def rows_for(request: ApprovalRequest) -> tuple[tuple[ApprovalDecision, str, str], ...]:
    """The rows this request shows. :data:`_ROWS` unless #161 asked for more.

    The redirect sits THIRD, above "No", because it is an approval and not a
    refusal: the user is choosing between two places to say yes, and burying it
    under the denial would read as a way to decline. ``p`` for "project" is the
    mnemonic when the alternative is the project tier; the label carries the
    absolute path either way, so the letter is a shortcut and never the
    explanation.
    """

    if not request.redirect_label:
        return _ROWS
    yes = request.yes_label or "Yes"
    return (
        (ApprovalDecision.YES, "y", yes),
        (ApprovalDecision.YES_SESSION, "s", "Yes, for this session"),
        (ApprovalDecision.REDIRECT, "p", request.redirect_label),
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


#: How a character :func:`safe_for_terminal` would remove is drawn instead.
_NAMED_STYLE = "reverse"


def _control_name(ch: str) -> str:
    """The visible name of a removed character: ``^[`` for ESC, ``<U+202E>``."""

    cp = ord(ch)
    if cp < 0x20:
        return "^" + chr(cp + 0x40)
    if cp == 0x7F:
        return "^?"
    return f"<U+{cp:04X}>"


#: A run of plain text and the spans of it that are names of removed characters.
_Shown = tuple[str, list[tuple[int, int]]]


def _shown(text: str, *, one_row: bool = False) -> _Shown:
    """*text* with every steering character replaced by its visible name.

    #389 review round 2. Round 1 deleted these characters
    (:func:`safe_for_terminal`), so a command with ``ESC [ 8 m`` in it was shown
    as ``[8m`` while the shell ran the ESC: what the user approved was not what
    ran. Now each one is drawn as its caret name (``^[``, ``^M``, ``^?``) or, past
    the C0 range, as ``<U+202E>``, and :func:`_named_text` draws the name in
    reverse video, so it cannot be mistaken for the same letters typed. The
    set is exactly what :func:`safe_for_terminal` removes, so nothing that could
    steer the terminal reaches it, and nothing is dropped.

    In a body (*one_row* false) a newline is a line break and a tab is kept;
    in one row (a path, a tool name, an option label) both are named too, so
    ``a\nb`` and ``a b`` do not look alike. The returned spans index the
    returned string.
    """

    from aelix_ai.utils.terminal_text import contains_steering_chars  # noqa: PLC0415

    keep = "" if one_row else "\n\t"
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    length = 0
    run = 0
    for i, ch in enumerate(text):
        if ch.isprintable() or ch in keep:
            continue
        if not contains_steering_chars(ch, keep_newline=not one_row, keep_tab=not one_row):
            continue
        out.append(text[run:i])
        length += i - run
        name = _control_name(ch)
        out.append(name)
        spans.append((length, length + len(name)))
        length += len(name)
        run = i + 1
    out.append(text[run:])
    return "".join(out), spans


def _shifted(shown: _Shown, prefix: str) -> _Shown:
    text, spans = shown
    return prefix + text, [(a + len(prefix), b + len(prefix)) for a, b in spans]


def _named_text(shown: _Shown, style: str = "") -> Any:
    """A Rich ``Text`` of *shown* — never parsed as markup — names in reverse."""

    from rich.text import Text  # noqa: PLC0415

    text, spans = shown
    out = Text(text, style=style)
    for a, b in spans:
        out.stylize(_NAMED_STYLE, a, b)
    return out


def _body_lines_of(text: str) -> list[str]:
    """*text* split at its newlines only, like the file it becomes.

    ``str.splitlines`` also breaks at CR, VT, FF, FS-RS, NEL and U+2028/9, so
    a CR in a file (or a command) used to read as a line break; here it stays
    on its line and is named (``^M``). A final newline ends the last line
    rather than opening an empty one, as ``splitlines`` had it.
    """

    lines = text.split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return lines


def _write_diff(path: _Shown, content: str) -> list[_Shown]:
    """An empty→content diff for a create/overwrite (no file read), every line."""

    rows = [_shifted(path, "--- "), _shifted(path, "+++ ")]
    for line in _body_lines_of(content):
        rows.append(_shifted(_shown(line), "+"))
    return rows


def _edit_diff(args: dict[str, Any]) -> list[_Shown]:
    """An old→new block for EVERY edit the edit tool will apply; never crashes.

    #389 review round 2. The tool does not apply ``args["edits"]``: it applies
    :func:`~aelix_coding_agent.tools._edit_diff.prepare_edit_arguments` of the
    arguments (pi's ``prepareArguments``), which parses ``edits`` sent as a JSON
    string and APPENDS a top-level ``oldText``/``newText`` pair to the list.
    Round 1 drew only ``edits`` when it was a non-empty list, so a call carrying
    both shapes showed one short edit while the tool applied two (measured: the
    dialog showed ``-hello``/``+HELLO``, ``1`` answered, and the hidden second
    replacement landed). The body is built from that same function's output,
    so it is the list the tool runs, in its order, and nothing else.

    The gate runs PRE-execution and must NOT read the file, so each edit is a
    plain old→new block, headed ``@@ edit i of n @@`` when there is more than
    one. An entry the tool will refuse is shown as what it is; arguments the
    tool cannot read as edits at all are shown raw (:func:`argument_rows`).
    """

    from aelix_coding_agent.tools._edit_diff import prepare_edit_arguments  # noqa: PLC0415

    try:
        edits = prepare_edit_arguments(args).get("edits")
    except Exception:  # noqa: BLE001 — a malformed call is shown raw below
        edits = None
    if not isinstance(edits, list) or not edits:
        rows: list[_Shown] = [("The edit tool will refuse these arguments:", [])]
        rows.extend((row, []) for row in argument_rows(args))
        return rows
    rows = []
    for i, edit in enumerate(edits):
        if len(edits) > 1:
            rows.append((f"@@ edit {i + 1} of {len(edits)} @@", []))
        old = edit.get("oldText") if isinstance(edit, dict) else None
        new = edit.get("newText") if isinstance(edit, dict) else None
        if not isinstance(old, str) or not isinstance(new, str):
            rows.append((f"edits[{i}] is not an edit the tool accepts: {edit!r}", []))
            continue
        for line in _body_lines_of(old) if old else []:
            rows.append(_shifted(_shown(line), "-"))
        for line in _body_lines_of(new) if new else []:
            rows.append(_shifted(_shown(line), "+"))
    return rows


def _path_rows(raw: str) -> list[Any]:
    """The path line(s) of a write or edit: as sent, and as the tool reads it.

    #389 review round 2 (sweep of fix item 1). The write and edit tools pass the
    path through ``expand_path`` (pi's ``expandPath``): NFC, unusual spaces made
    ASCII, ONE leading ``@`` dropped and a leading ``~`` expanded. ``@~/.bashrc``
    is therefore a write to the home directory's ``.bashrc``. When that changes
    the path, the second row says where the write lands.
    """

    from aelix_coding_agent.tools._path_utils import expand_path  # noqa: PLC0415

    rows: list[Any] = []
    try:
        expanded = expand_path(raw)
    except Exception:  # noqa: BLE001 — the tool will fail the same way
        expanded = raw
    if expanded != raw:
        rows.append(
            _named_text(_shifted(_shown(expanded, one_row=True), "The tool writes to: "), "bold")
        )
    return rows


def _panel_to_ansi(title: Any, body: Any, width: int, plain: list[str]) -> list[str]:
    """Render a bordered Rich Panel containing ``body`` to ANSI lines.

    A recording :class:`rich.console.Console` captures the styled output.
    *title* is a ``Text``, never a ``str``: Rich parses a ``str`` panel title as
    markup, so a path or tool name holding ``[/]`` raised ``MarkupError``
    (#389 review round 2, measured on 402a8013 and round 1 alike).

    If rendering fails anyway, the fallback is *plain*: the same rows as text,
    every one of them. It used to be ``str(body)``, and for a ``Group`` that is
    ``<rich.console.Group object at 0x…>`` — one short line, so the dialog saw
    nothing to hold and took Yes on a body that was never drawn.
    """

    try:
        from rich.console import Console  # noqa: PLC0415 — optional in degraded env
        from rich.panel import Panel

        # ``record=True`` means rich buffers and we read it back with
        # ``export_text``; ``file`` only ever sees ``write``/``flush``, which
        # ``_NullFile`` implements. The cast states that narrower contract
        # instead of dressing the stub up as a full ``IO[str]``.
        #
        # ``legacy_windows=False`` is PINNED (issue #206). Left to auto-detect,
        # rich reads the flag off the PROCESS stdout once and caches it
        # (``rich/console.py:562-577``), so on Windows without the VT bit — a
        # legacy conhost, or CI where pytest captures stdout to a pipe — every
        # ROUNDED box here silently becomes SQUARE (``rich/box.py:79`` +
        # ``LEGACY_WINDOWS_SUBSTITUTIONS`` at ``:405``): ``╭╮╰╯`` → ``┌┐└┘``.
        # This console never reaches a console host (it records into
        # ``_NullFile``), and rich's Win32 API renderer is gated on the file
        # having a std-stream ``fileno`` (``console.py:2071-2078``), so pinning
        # the flag here restores the corners (and, on a legacy conhost, changes
        # which SGR bytes land in the buffer) — it cannot emit raw ANSI at a
        # terminal that would not understand it. The live console in
        # ``chrome.py`` writes to the real terminal and is deliberately NOT
        # pinned for exactly that reason.
        console = Console(
            width=width, record=True, file=cast("IO[str]", _NullFile()), legacy_windows=False
        )
        console.print(Panel(body, title=title, expand=False, width=width))
        text = console.export_text(styles=True)
        return text.splitlines()
    except Exception:  # noqa: BLE001 — never let the prompt fail to draw its rows
        return list(plain)


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
    width: int = _RENDER_WIDTH,
) -> list[str]:
    """Build the dialog body as ANSI lines (PURE — no prompt-toolkit / I/O).

    - bash → "Run command:" + the FULL untruncated command.
    - write → "Create/overwrite {path}" + an empty→content diff, every line.
    - edit → "Edit {path}" + an old→new block for every edit the tool will
      apply (:func:`_edit_diff`), every line.
    - other → the tool name, the argument count and EVERY argument, whole, one
      row each (:func:`argument_rows`).

    Nothing in the body is elided (#389). The write/edit diff used to stop at
    40 lines and cut every row at the Panel width with an ``…``, so a file's
    last line, or the end of one long line, was approved without ever being
    drawn; a long row now wraps inside the Panel instead. The body can be far
    taller than the screen; the runner holds Yes until every line of it has
    been on screen (:class:`_BodyViewport`).

    Every string the model or a tool author chose is shown with its steering
    characters NAMED (:func:`_shown`): an escape sequence in the command could
    otherwise conceal its own tail (SGR 8) or write to the terminal while the
    dialog counts the row as shown, and deleting it (round 1) showed a command
    that is not the one that runs. Nothing is parsed as Rich markup: every
    string goes in as a ``Text``.

    ``render_diff`` (default :func:`render._render_diff`) colours the diff so it
    matches the transcript; a ``None`` / raising callback degrades to plain
    diff text — never crashes.
    """

    from rich.console import Group  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    rd = render_diff if render_diff is not None else _default_render_diff()

    rows: list[_Shown]
    if request.kind == "bash":
        command = _bash_command(request.args)
        title = ("Run shell command?", [])
        rows = [("Run command:", [])]
        rows.extend(_shown(line) for line in _body_lines_of(command or "(empty)"))
        body: Any = Group(
            Text("Run command:", style="bold"),
            _named_text(_shown(command), "yellow") if command else Text("(empty)", style="yellow"),
        )
    elif request.kind in ("write", "edit"):
        raw = _path(request.args)
        path = _shown(raw, one_row=True)
        verb = "Create/overwrite" if request.kind == "write" else "Edit"
        title = _shifted(path if path[0] else ("(unknown path)", []), f"{verb} ")
        title = (f"{title[0]}?", title[1])
        diff = (
            _write_diff(path, _content(request.args))
            if request.kind == "write"
            else _edit_diff(request.args)
        )
        head = _shifted(path, f"{verb} ")
        extra = _path_rows(raw)
        rows = [head, *((t.plain, []) for t in extra), *diff]
        body = Group(_named_text(head, "bold"), *extra, _safe_diff(rd, diff))
    else:
        tool = _shown(request.tool_name, one_row=True)
        title = _shifted(tool, "Allow ")
        title = (f"{title[0]}?", title[1])
        arg_rows = argument_rows(request.args)
        rows = [_shifted(tool, "Tool: "), (_argument_count(len(arg_rows)), [])]
        rows.extend((row, []) for row in arg_rows)
        body = Group(
            _named_text(rows[0], "bold"),
            Text(rows[1][0], style="bold"),
            *(Text(row) for row in arg_rows),
        )

    plain = [title[0], *(text for text, _spans in rows)]
    return _panel_to_ansi(_named_text(title), body, width, plain)


def _argument_count(n: int) -> str:
    if n == 0:
        return "(no arguments)"
    return f"{n} argument{'s' if n != 1 else ''}:"


def argument_rows(args: dict[str, Any]) -> list[str]:
    """One ``key=value`` row for EVERY argument, whole, in the order sent.

    #188 round 1. This is the consent surface for every tool aelix did not
    build (``kind="other"``), and it used to print only the first six
    arguments with no sign of the rest — so a model could put six filler keys
    first and the write target seventh, and the user approved a call whose
    target they were never shown (measured: ``hidden.txt`` written after a
    dialog that listed ``label0``…``label5``). The argument dict is the one the
    loop hands to ``execute`` (unknown keys are kept), so nothing short of every
    key describes the call.

    No row is dropped, none is capped by count, and since #389 review round 2
    no value or key is cut either. ADR-0253 §8 cut each value at 200 characters
    and each key at 60, with a marker saying how much was cut, so that one huge
    value could not run on for dozens of rows; but a body that fits the screen
    is answerable at once, so the rest of a 417-character ``content`` was
    approved unseen. The dialog now holds Yes until every row has been on
    screen (:class:`_BodyViewport`), which makes a long value cost paging,
    never consent. Values are ``repr``'d and a key that is not printable is
    too: a newline or an escape sequence in either cannot draw a fake row.
    """

    rows: list[str] = []
    for key, value in args.items():
        name = key if isinstance(key, str) and key.isprintable() else repr(key)
        rows.append(f"{name}={value!r}")
    return rows


def argument_summary(args: dict[str, Any]) -> str:
    """:func:`argument_rows` on one line, with the count, for a one-line prompt.

    The generic ``ctx.ui.select`` fallback (``builtin/permission.py``, a host
    with a UI but no approval dialog) has only a title to show the call in.
    """

    rows = argument_rows(args)
    if not rows:
        return _argument_count(0)
    return f"{_argument_count(len(rows))} {', '.join(rows)}"


def _safe_diff(render_diff: Callable[..., Any], rows: list[_Shown]) -> Any:
    """Render the diff *rows* into the dialog body: every line, none cut short.

    #389. ``max_lines`` is the diff's own line count and ``max_line_width`` is
    unbounded, so ``_render_diff`` drops no line and puts no ``…`` on one; the
    Panel wraps a row wider than itself onto the next. Before this the write
    and edit prompts stopped at 40 lines and cut every row at the Panel's
    width (#166 had only moved that cut from 76 cells to the terminal's), so the
    end of a file, or of one long line, could be approved without being drawn.

    The names of removed characters (:func:`_shown`) are put in reverse video
    on the rows ``render_diff`` returns, where a row's text is the line it was
    given. The fallback for a ``render_diff`` that raises shows the same rows,
    styled the same way. The rows hold no steering character, so neither path
    can steer the terminal.
    """

    from rich.console import Group  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    if not rows:
        return Text("(no changes to preview)", style="dim")
    diff_text = "\n".join(text for text, _spans in rows)
    try:
        rendered = render_diff(diff_text, max_lines=len(rows), max_line_width=sys.maxsize)
    except Exception:  # noqa: BLE001 — never let a diff render break the prompt
        return Group(*(_named_text(row) for row in rows))
    drawn = getattr(rendered, "renderables", None)
    if isinstance(drawn, list) and len(drawn) == len(rows):
        for row, (text, spans) in zip(drawn, rows, strict=True):
            if isinstance(row, Text) and row.plain == text:
                for a, b in spans:
                    row.stylize(_NAMED_STYLE, a, b)
    return rendered


def _default_render_diff() -> Callable[..., Any]:
    from aelix_coding_agent.tui.render import _render_diff  # noqa: PLC0415

    return _render_diff


def build_options_view(
    selected: int,
    rows_spec: tuple[tuple[ApprovalDecision, str, str], ...] = _ROWS,
) -> list[str]:
    """The option rows with a ``→`` marker on ``selected`` (PURE).

    ``rows_spec`` defaults to the three static rows, so every existing caller
    and every existing snapshot is unchanged. Issue #161 passes
    :func:`rows_for` output when the write has a second right answer.

    The hint line is DERIVED from the rows rather than written out: the first
    revision of #161 left it saying "1-3 / y·s·n" while the dialog showed four
    rows, which is the class of stale-prose defect this batch exists to remove.

    A label goes through the same :func:`_shown` as the body (#389 review
    round 2): the redirect row carries the file name the model chose, and these
    rows reach prompt-toolkit's ANSI parser, so an ``ESC [ 8 m`` in that name
    hid the rest of the row the user was approving.
    """

    view: list[str] = []
    for i, (_decision, mnemonic, label) in enumerate(rows_spec):
        marker = "→ " if i == selected else "  "
        view.append(f"{marker}{i + 1}. [{mnemonic}] {_ansi_named(_shown(label, one_row=True))}")
    digits = f"1-{len(rows_spec)}"
    mnemonics = "·".join(mnemonic for _d, mnemonic, _l in rows_spec)
    view.append(f"  ↑/↓ to move · {digits} / {mnemonics} · Enter to confirm · Esc to deny")
    return view


#: Answers the dialog takes whatever is on screen. Every other row approves
#: something, so it is held while the body is not all shown (#188, #389).
_ALWAYS_ANSWERABLE = frozenset({ApprovalDecision.NO, ApprovalDecision.CANCEL})


def _ansi_named(shown: _Shown) -> str:
    """*shown* as ANSI text, each name in reverse video (SGR 7 … 27)."""

    text, spans = shown
    out: list[str] = []
    run = 0
    for a, b in spans:
        out += [text[run:a], "\x1b[7m", text[a:b], "\x1b[27m"]
        run = b
    out.append(text[run:])
    return "".join(out)


#: The footer when the modal had no row left for the body at all.
_NO_ROOM = "The details do not fit on screen. Yes is held. Enlarge the terminal."


_SGR = re.compile(r"\x1b\[[0-9;]*m")

#: One row of the body as drawn: an ANSI line, or prompt-toolkit fragments.
_Row = Any


def _display_rows(lines: list[str], width: int) -> list[_Row]:
    """The body lines as rows no wider on screen than *width*, however counted.

    #389 review round 2 (Codex candidate B, measured). Rich wraps the Panel by
    its own cell widths and prompt-toolkit paints by wcwidth's; they disagree
    about regional indicators and skin-tone modifiers (Rich 1 and 0 cells,
    wcwidth 2), so ``echo`` + 30 x U+1F1E6 + ``; echo TAIL`` fitted one row for
    Rich, painted 30 cells wider, and the body window (``wrap_lines=False``)
    cut it at the border: the viewport counted the row drawn while ``TAIL`` was
    never on screen, and Yes was taken at once.

    Each line is measured with :func:`~aelix_coding_agent.tui.width.cells_at_most`
    (the larger of the two counts per character, zero-width counted as one).
    A line that fits is kept as it is; the common, all-ASCII line costs one
    check. One that does not first gives back the Panel's padding (the spaces
    before its right border, which Rich added by its own smaller count); if it
    still does not fit it is cut into rows that each do, so every character
    lands on a row the viewport counts and the screen shows. The cost is a
    broken right border on such a row.
    """

    from prompt_toolkit.formatted_text import ANSI, to_formatted_text  # noqa: PLC0415

    from aelix_coding_agent.tui.width import cells_at_most  # noqa: PLC0415

    rows: list[_Row] = []
    for line in lines:
        plain = _SGR.sub("", line)
        if plain.isascii() and len(plain) <= width:
            rows.append(line)
            continue
        if cells_at_most(plain) <= width:
            rows.append(line)
            continue
        chars = [
            (style, ch) for style, text, *_ in to_formatted_text(ANSI(line)) for ch in text
        ]
        excess = sum(cells_at_most(ch) for _style, ch in chars) - width
        last = len(chars) - 1
        while last >= 0 and chars[last][1] == " ":
            last -= 1
        if last > 0 and chars[0][1] == "│" and chars[last][1] == "│":
            # Trailing spaces are invisible, so giving them back moves only the
            # border; one is kept as the Panel's own margin.
            first_pad = last
            while first_pad > 1 and chars[first_pad - 1][1] == " ":
                first_pad -= 1
            take = min(excess, max(0, last - first_pad - 1))
            del chars[last - take : last]
            excess -= take
        if excess <= 0:
            rows.append(cast("StyleAndTextTuples", chars))
            continue
        rows.extend(_cut(cast("StyleAndTextTuples", chars), width))
    return rows


def _cut(chars: StyleAndTextTuples, width: int) -> list[StyleAndTextTuples]:
    """One-character fragments cut into rows of at most *width* cells.

    A row breaks after its last space when it has one, as Rich wraps, so a word
    stays whole on the row that shows it; a run with no space is cut where the
    row is full. Every character is kept, the spaces included.

    The split repeats while the next character still does not fit (#389 review
    round 3): a row can open with the space carried over from the last split,
    and when it then fills to *width* and a two-cell character follows, one
    split after that leading space left ``width - 1`` cells plus two, a row one
    cell wider than the screen (measured: rows of 80, 1 and 81 cells at 80
    columns for 30 skin-tone modifiers, 17 letters, two spaces, 30 modifiers,
    19 letters and a Hangul syllable).
    """

    from aelix_coding_agent.tui.width import cells_at_most  # noqa: PLC0415

    rows: list[StyleAndTextTuples] = []
    row: StyleAndTextTuples = []
    used = 0
    for fragment in chars:
        cells = cells_at_most(fragment[1])
        while row and used + cells > width:
            spaces = [i for i, f in enumerate(row) if f[1] == " "]
            split = spaces[-1] + 1 if spaces and spaces[-1] + 1 < len(row) else len(row)
            rows.append(row[:split])
            row = row[split:]
            used = sum(cells_at_most(f[1]) for f in row)
        row.append(fragment)
        used += cells
    rows.append(row)
    return rows


class _BodyViewport:
    """The scrolled body of every approval prompt and the footer under it.

    #188 review round 2 built this for ``kind="other"``; #389 put every kind
    behind it. The body sits in a height-capped modal: at 80x24 six
    198-character argument values filled it and ``path`` and ``content`` were
    below the fold, and a 400-word ``bash`` command showed up to ``arg130`` of
    it, with nothing saying more was hidden, and Yes ran it.

    So the body control draws its own slice of the lines (the height it is
    given by the window it renders into, never a guess), and records every line
    index it actually handed to the screen. :meth:`all_shown` is what Yes waits
    for. The footer is a separate one-row window under the body, outside the
    scrolled slice, that says how many lines are not on screen, how to reach
    them, and that Yes is held until all of them have been shown. When the
    whole body fits, the footer is blank and Yes is answerable from the first
    paint on. A Yes typed before that paint is held like any other (nothing has
    been shown yet): it is dropped, not queued, and has to be pressed again.

    "Shown" means drawn by a real render: scrolling without a repaint in
    between does not count, so keys typed ahead of the screen cannot approve.
    A width change re-wraps the body into different lines, so it forgets what
    was shown. The lines are display rows (:func:`_display_rows`), so a line is
    counted only once it fits the screen by every measure of its width.
    """

    def __init__(self, lines: Callable[[], list[_Row]], width_key: Callable[[], Any]) -> None:
        self._lines = lines
        self._width_key = width_key
        self.top = 0
        # What the last render drew: the first line, how many, out of how many.
        self._frame: tuple[Any, int, int, int] | None = None
        self._seen: set[int] = set()
        self._seen_for: Any = object()

    def all_shown(self) -> bool:
        """Has every line of the CURRENT body been drawn at least once?"""

        lines = self._lines()
        if self._seen_for != self._width_key():
            return False
        return self._seen.issuperset(range(len(lines)))

    def scroll(self, delta: int, chrome: Any) -> None:
        self.top = max(0, self.top + delta)
        if self._frame is not None:
            _stamp, _top, shown, total = self._frame
            self.top = min(self.top, max(0, total - shown))
        chrome.invalidate()

    def page(self, direction: int, chrome: Any) -> None:
        """PgUp / PgDn: one screenful less one line, so a page keeps one row of
        context and no line is skipped (a skipped line would keep Yes held).

        #389. #188 scrolled five lines a press, which was enough for an
        argument list; a 121-line file then took 23 presses at 80x24 (9 now).
        """

        shown = self._frame[2] if self._frame is not None else 0
        self.scroll(direction * max(1, shown - 1), chrome)

    def _draw(self, height: int | None) -> list[_Row]:
        lines = self._lines()
        total = len(lines)
        shown = total if height is None else max(0, min(total, height))
        self.top = max(0, min(self.top, total - shown))
        if self._seen_for != self._width_key():
            self._seen = set()
            self._seen_for = self._width_key()
        self._seen.update(range(self.top, self.top + shown))
        self._frame = (_render_stamp(), self.top, shown, total)
        return lines[self.top : self.top + shown]

    def footer_text(self, width: int | None = None) -> str:
        """The footer row for the frame being drawn (blank when all fits).

        When the full sentence is wider than *width* a shorter one is used, and
        it STARTS with "Yes held", so the footer's own ``…`` (which cuts the
        end) reaches it only below ``len("Yes held") + 1`` = 9 columns (#389: a
        three-digit line count at 80 columns made the full one 81 cells; review
        round 2 measured round 1's short form, which ended in "Yes held",
        drawn as ``… · Ye…`` at 40 columns).
        """

        frame = self._frame
        if frame is None or frame[0] != _render_stamp():
            # The body was not drawn this frame (no room for it at all).
            return _NO_ROOM
        _stamp, top, shown, total = frame
        hidden = total - shown
        if hidden <= 0:
            return ""
        above, below = top, total - top - shown
        held = not self.all_shown()
        text = f"{hidden} of {total} lines hidden (↑{above} ↓{below}) · PgUp/PgDn to scroll"
        if held:
            text += " · Yes held until all seen"
        if width is not None and len(text) > width:
            text = f"{hidden}/{total} hidden ↑{above} ↓{below} · PgUp/PgDn"
            if held:
                text = f"Yes held · {text}"
        return text

    def body_control(self) -> Any:
        from prompt_toolkit.formatted_text import ANSI, to_formatted_text  # noqa: PLC0415
        from prompt_toolkit.layout.controls import UIContent, UIControl  # noqa: PLC0415

        viewport = self

        class _Body(UIControl):
            def preferred_height(
                self,
                width: int,
                max_available_height: int,
                wrap_lines: bool,
                get_line_prefix: Any,
            ) -> int | None:
                return len(viewport._lines())

            def create_content(self, width: int, height: int) -> UIContent:
                fragments = [
                    to_formatted_text(ANSI(row)) if isinstance(row, str) else row
                    for row in viewport._draw(height)
                ]
                return UIContent(
                    get_line=lambda i: fragments[i],
                    line_count=len(fragments),
                    show_cursor=False,
                )

        return _Body()

    def footer_control(self) -> Any:
        from prompt_toolkit.layout.controls import UIContent, UIControl  # noqa: PLC0415

        viewport = self

        class _Footer(UIControl):
            def create_content(self, width: int, height: int) -> UIContent:
                text = viewport.footer_text(width)
                if len(text) > width:
                    text = text[: max(0, width - 1)] + "…"
                fragments: StyleAndTextTuples = [("bold" if text else "", text)]
                return UIContent(get_line=lambda _i: fragments, line_count=1, show_cursor=False)

        return _Footer()


def _render_stamp() -> int:
    """Which paint this is (prompt-toolkit's per-app render counter)."""

    from prompt_toolkit.application.current import get_app  # noqa: PLC0415

    return get_app().render_counter


async def run_approval_dialog(
    *,
    request: ApprovalRequest,
    show_modal: Callable[..., Awaitable[Any]],
    chrome: Any,
    render_diff: Callable[..., Any] | None = None,
    width: int | Callable[[], int] = _RENDER_WIDTH,
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
    the diff body is far taller than the cap.

    #188 review round 2, extended to every kind by #389 — the body is a
    :class:`_BodyViewport` and the row between it and the options is its footer.
    Yes, "Yes, for this session" (and any other approving row) is not taken
    until every line of the body has been drawn; No, Esc and Ctrl+C always are.
    PgUp/PgDn scroll a page, Ctrl+↑/↓ one line.
    """

    from prompt_toolkit.formatted_text import ANSI  # noqa: PLC0415
    from prompt_toolkit.key_binding import KeyBindings  # noqa: PLC0415
    from prompt_toolkit.layout import HSplit, Window  # noqa: PLC0415
    from prompt_toolkit.layout.controls import FormattedTextControl  # noqa: PLC0415
    from prompt_toolkit.layout.dimension import Dimension  # noqa: PLC0415

    # Issue #166 — the body is re-rendered when the WIDTH changes, not baked
    # once. A prompt waits for a human indefinitely, so "the terminal is resized
    # while it is open" is ordinary, and prompt-toolkit repaints on it
    # unconditionally (SIGWINCH, plus a polling fallback in
    # ``Application._poll_output_size`` for terminals that do not deliver it).
    # The body window is ``wrap_lines=False``, so a row wider than the screen is
    # CLIPPED rather than re-wrapped — and because the Panel had wrapped at the
    # OLD width, the clip takes a bite out of the MIDDLE of the command, leaving
    # a string that reads like a different command instead of a visibly
    # truncated one. Baking at open time was strictly worse than the historical
    # fixed 80 on every terminal wider than 80.
    #
    # Keyed on the resolved width, so an ordinary repaint costs one comparison
    # and only a real resize pays for a re-render.
    _width_of = width if callable(width) else (lambda: width)
    _body: dict[str, Any] = {"width": None, "lines": []}

    def _body_lines() -> list[_Row]:
        current = _width_of()
        if current != _body["width"]:
            _body["lines"] = _display_rows(
                build_approval_view(request, render_diff=render_diff, width=current), current
            )
            _body["width"] = current
        return cast("list[_Row]", _body["lines"])

    _body_lines()  # prime, so the first paint does not pay for the render

    # ``idx`` is the highlighted option row.
    state = {"idx": 0}

    # Issue #161 — the rows this request shows, computed ONCE so the view,
    # the key bindings and the wrap-around arithmetic cannot disagree about
    # how many there are.
    rows_spec = rows_for(request)

    # #188 review round 2, every kind since #389: the body is all the user has
    # to judge the call by, so Yes waits until every line of it has been on
    # screen (:class:`_BodyViewport`).
    viewport = _BodyViewport(_body_lines, lambda: _body["width"])

    def _render_options() -> str:
        return "\n".join(build_options_view(state["idx"], rows_spec))

    def build(result: asyncio.Future[Any]) -> HSplit:
        kb = KeyBindings()

        def _resolve(value: ApprovalDecision) -> None:
            if value not in _ALWAYS_ANSWERABLE and not viewport.all_shown():
                # An approval while part of the body has never been on screen
                # is not taken. The footer says how many lines are hidden and
                # how to reach them.
                chrome.invalidate()
                return
            if not result.done():
                result.set_result(value)

        def _confirm(_e: object) -> None:
            _resolve(rows_spec[state["idx"]][0])

        @kb.add("up")
        def _up(_e: object) -> None:
            state["idx"] = (state["idx"] - 1) % len(rows_spec)
            chrome.invalidate()

        @kb.add("down")
        def _down(_e: object) -> None:
            state["idx"] = (state["idx"] + 1) % len(rows_spec)
            chrome.invalidate()

        # PageUp/PageDown (and Ctrl+Up/Ctrl+Down) scroll the body so a body
        # taller than the height cap stays fully reachable; option nav keeps
        # ↑/↓. Bound to the viewport so the clamp follows what the body
        # ACTUALLY draws.
        kb.add("pageup")(lambda _e: viewport.page(-1, chrome))
        kb.add("pagedown")(lambda _e: viewport.page(1, chrome))
        kb.add("c-up")(lambda _e: viewport.scroll(-1, chrome))
        kb.add("c-down")(lambda _e: viewport.scroll(1, chrome))

        kb.add("enter")(_confirm)
        kb.add("c-j")(_confirm)
        # NOTE (nit WP-0): ``space`` is deliberately NOT a confirm key here. The
        # default-highlighted row is "Yes" (allow), so a stray space on a
        # security prompt would auto-approve a mutating tool. Require an explicit
        # Enter / digit / mnemonic instead.

        # Digit shortcuts select + confirm immediately.
        for i, (decision, mnemonic, _label) in enumerate(rows_spec):
            kb.add(str(i + 1))(lambda _e, d=decision: _resolve(d))
            kb.add(mnemonic)(lambda _e, d=decision: _resolve(d))
            kb.add(mnemonic.upper())(lambda _e, d=decision: _resolve(d))

        kb.add("escape")(lambda _e: _resolve(ApprovalDecision.CANCEL))
        kb.add("c-c")(lambda _e: _resolve(ApprovalDecision.CANCEL))

        # The body is FLEXIBLE (shrinks under the cap → scrolls); the options
        # window is FIXED at exactly its row count (it is the cursor/focus owner
        # so the dialog navigates), pinned below the body OUTSIDE the cap's
        # squeeze so Yes/No can never be clipped. The footer separates them: it
        # draws its own slice of the body, so it knows exactly which lines
        # reached the screen.
        n_option_rows = len(rows_spec) + 1  # rows + the hint line
        body_window = Window(viewport.body_control(), wrap_lines=False)
        footer = Window(viewport.footer_control(), height=Dimension.exact(1))
        options_window = Window(
            FormattedTextControl(
                lambda: ANSI(_render_options()), focusable=True, key_bindings=kb
            ),
            height=Dimension.exact(n_option_rows),
            dont_extend_height=True,
        )
        return HSplit([body_window, footer, options_window])

    decision = await show_modal(chrome, build)
    return decision if isinstance(decision, ApprovalDecision) else ApprovalDecision.CANCEL


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "argument_rows",
    "argument_summary",
    "build_approval_view",
    "build_options_view",
    "run_approval_dialog",
]
