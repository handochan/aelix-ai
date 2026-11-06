"""#596 (ADR-0287) — the input shows what Enter will do with it, while it is typed.

Two marks, both pure functions of the buffer text (no I/O on the render path):

* an ``@path`` file reference — the token :mod:`.completion` completes: an ``@``
  at the start of a line or after whitespace, running to the next whitespace, or
  a quoted ``@"…"`` — takes the theme's ``accent`` colour. It is coloured by its
  shape alone; whether the file exists is not looked up while drawing;
* a line :func:`~.input.parse_input_line` would run as a shell command (``!cmd``
  or ``!!cmd``) puts the input in *shell mode*: the chrome draws the sigil as the
  prompt marker, colours the lines around the input with the theme's ``shell``
  role and says in the status row what Enter does. References are not coloured
  there: the shell gets ``@path`` as literal text.

Colours come from the active theme through the same ``fg(role, text)`` the
extensions use, parsed back into a prompt-toolkit style, so ``/settings`` → Theme
recolours the input with no table of its own. Under ``NO_COLOR`` prompt-toolkit
drops every colour (``ColorDepth.from_env``); a reference is then underlined and
shell mode draws heavy lines, so neither depends on colour alone.

The footer's ``statusline`` role (#596, the owner's "a little dimmer") is read
here too, for the same reason: one place turns a theme role into a style.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.lexers import Lexer

from aelix_coding_agent.tui.input import ParsedInput, parse_input_line

if TYPE_CHECKING:
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import StyleAndTextTuples

    from aelix_coding_agent.extensions.widget_protocols import Theme

# Used when the theme gives a role no colour (no theme wired, or a theme file
# written before the role existed): the default theme's own picks, as
# prompt-toolkit names. ``statusline`` has none on purpose — a theme that does
# not dim its footer keeps it as it was.
_FALLBACK_MENTION = "ansiblue"
_FALLBACK_SHELL = "ansimagenta"
_FALLBACK_RULE = "ansibrightblack"
_FALLBACK_SHELL_SGR = "\x1b[35m"
_SGR_RESET = "\x1b[0m"


def shell_sigil(text: str) -> str:
    """``"!"`` / ``"!!"`` when Enter would run *text* as a shell command, else ``""``.

    Read through :func:`parse_input_line` so the mark and the submit path cannot
    disagree (a leading space makes ``" !ls"`` a prompt in both).
    """

    kind = parse_input_line(text).kind
    if kind == "bash_transient":
        return "!!"
    if kind == "bash":
        return "!"
    return ""


def submitted_input(typed: str, expand: Callable[[str], str]) -> ParsedInput:
    """What Enter does with the *typed* buffer, whose paste placeholders
    *expand* turns back into the text they stand for.

    Shell mode, and ``!`` against ``!!``, are read off the typed buffer — what
    the input draws — and the placeholders are expanded after: a pasted ``!``
    stays literal text. A placeholder at the start whose paste begins with
    ``!`` is a prompt, as drawn (#596 verify V1: it ran every pasted line in
    the shell); ``!`` typed before a paste that begins with ``!`` runs the
    paste as a ``!`` command, not as a ``!!`` one (Codex 596-06).

    A command is read off the typed buffer too (#667): a line that does not
    begin with a typed ``/`` is a prompt whatever its pastes begin with, so a
    paste beginning ``/quit`` or ``/Users/…`` reaches the model as drawn,
    marked :attr:`~.input.ParsedInput.literal` for the input loop. A command
    typed before a paste (``/name [Pasted text …]``) takes the paste as its
    argument: that is :func:`parse_input_line` of the expanded text, as before.
    """

    drawn = parse_input_line(typed)
    if drawn.kind in ("bash", "bash_transient"):
        return ParsedInput(drawn.kind, expand(drawn.text).strip())
    expanded = expand(typed)
    if drawn.kind == "prompt" and not drawn.text.startswith("/") and expanded.strip():
        text = expanded.strip()
        return ParsedInput("prompt", text, literal=text.startswith("/"))
    return parse_input_line(expanded)


class SubmittedLine(str):
    """A submitted line that carries how :func:`submitted_input` classified it.

    The input queue hands lines to the input loop as text; this keeps the
    typed buffer's classification with the expanded text, so the loop runs
    what the input drew. ``after_turn`` marks an Alt+Enter line: a ``!`` line
    queued to run once the turn is over (#596), a template Entered mid-turn
    whose expansion is queued as a follow-up (#701).
    """

    parsed: ParsedInput
    after_turn: bool

    def __new__(cls, text: str, parsed: ParsedInput, *, after_turn: bool = False) -> SubmittedLine:
        line = super().__new__(cls, text)
        line.parsed = parsed
        line.after_turn = after_turn
        return line


def classify_submitted(line: str) -> ParsedInput:
    """The input loop's read of *line*: the chrome's classification when it made one."""

    if isinstance(line, SubmittedLine):
        return line.parsed
    return parse_input_line(line)


def mention_spans(line: str) -> list[tuple[int, int]]:
    """``(start, end)`` of every ``@`` reference on one line.

    The grammar of :func:`.completion._extract_mention`, applied to the whole
    line instead of the text before the cursor: ``foo@bar`` is not a reference,
    ``@"a b"`` is one token, an unclosed ``@"…`` runs to the end of the line. A
    bare ``@`` is left alone until something follows it.
    """

    spans: list[tuple[int, int]] = []
    i, n = 0, len(line)
    while i < n:
        if line[i] == "@" and (i == 0 or line[i - 1].isspace()):
            if i + 1 < n and line[i + 1] == '"':
                close = line.find('"', i + 2)
                end = n if close == -1 else close + 1
            else:
                end = i + 1
                while end < n and not line[end].isspace():
                    end += 1
            if end - i > 1:
                spans.append((i, end))
            i = end
            continue
        i += 1
    return spans


def role_style(theme: Theme | None, role: str) -> str:
    """The prompt-toolkit style *theme* gives *role* (``""`` when it gives none)."""

    if theme is None:
        return ""
    try:
        fragments = to_formatted_text(ANSI(theme.fg(role, "x")))
    except Exception:  # noqa: BLE001 — a faulty theme must not break input
        return ""
    if len(fragments) == 1 and fragments[0][1] == "x":
        return fragments[0][0]
    return ""


def paint_shell(theme: Theme | None, text: str) -> str:
    """*text* in the theme's ``shell`` colour, as SGR (magenta when it has none)."""

    if theme is not None:
        try:
            styled = theme.fg("shell", text)
        except Exception:  # noqa: BLE001 — a faulty theme must not break the chrome
            styled = text
        if styled != text:
            return styled
    return f"{_FALLBACK_SHELL_SGR}{text}{_SGR_RESET}"


def no_color() -> bool:
    """``NO_COLOR`` as prompt-toolkit reads it (any non-empty value)."""

    return bool(os.environ.get("NO_COLOR"))


@dataclass(frozen=True)
class InputStyles:
    """The prompt-toolkit styles the input marks are drawn in, for one theme."""

    mention: str
    shell: str
    rule: str
    no_color: bool = False


def input_styles(theme: Theme | None, *, colorless: bool | None = None) -> InputStyles:
    if colorless is None:
        colorless = no_color()
    mention = role_style(theme, "accent") or _FALLBACK_MENTION
    shell = role_style(theme, "shell") or _FALLBACK_SHELL
    rule = role_style(theme, "dim") or _FALLBACK_RULE
    return InputStyles(
        mention=f"class:aelix.mention {mention}" + (" underline" if colorless else ""),
        shell=f"class:aelix.shell {shell}",
        rule=f"class:aelix.input-rule {rule}",
        no_color=colorless,
    )


def statusline_style(theme: Theme | None) -> str:
    """The base style of the footer row: the theme's ``statusline`` colour.

    ``""`` when the theme gives the role none — a theme file written before it
    existed keeps its footer exactly as it was. Applied as the footer Window's
    style, not wrapped around its text, so a segment that colours itself (the
    permission badge) keeps its colour and the ``\\x1b[0m`` after it falls back to
    this rather than to the terminal's full-strength default.
    """

    colour = role_style(theme, "statusline")
    return f"class:aelix.statusline {colour}" if colour else ""


class InputLexer(Lexer):
    """Colours the ``@`` references in the input buffer (none in shell mode)."""

    def __init__(self, styles: Callable[[], InputStyles]) -> None:
        self._styles = styles

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        lines = document.lines
        shell = bool(shell_sigil(document.text))
        style = self._styles().mention

        def get_line(lineno: int) -> StyleAndTextTuples:
            try:
                line = lines[lineno]
            except IndexError:
                return []
            if shell:
                return [("", line)]
            fragments: StyleAndTextTuples = []
            pos = 0
            for start, end in mention_spans(line):
                if start > pos:
                    fragments.append(("", line[pos:start]))
                fragments.append((style, line[start:end]))
                pos = end
            if pos < len(line) or not fragments:
                fragments.append(("", line[pos:]))
            return fragments

        return get_line

    def invalidation_hash(self) -> object:
        # The text is already part of BufferControl's cache key; a theme switch
        # changes the colour without changing the text, so re-lex on that too.
        return self._styles().mention


#: #596 (ADR-0287) — what the status row says while a ``!`` command runs and
#: Enter on another one is refused (pi's "A bash command is already running").
SHELL_BUSY_NOTICE = "a ! command is already running"


def shell_hint(
    sigil: str,
    *,
    running: bool,
    width: int,
    bare: bool = False,
    shell_busy: bool = False,
    stopped: bool = False,
) -> str:
    """The status-row words for shell mode, fitted to *width* cells by
    importance (plain text; the caller colours it).

    The most needed words are kept first and each next one only if it still
    fits, so what goes at a narrow width is the least needed: the model clause
    outranks the Enter clause, which outranks "Esc to clear" (#596 verify V8 —
    at 50 columns ``!!`` lost "the model won't see the output" first).

    While a turn (or a command) runs, Enter runs the command at once and the
    turn goes on; a ``!`` command's output reaches the model when the turn is
    over, and Alt+Enter queues the line to run after it. Esc there is the
    turn's interrupt key, so the hint does not offer it. A *bare* sigil runs
    nothing, so it makes no promise about Enter; and while another ``!``
    command runs (*shell_busy*), Enter is refused and the hint says why.

    While aelix is *stopped* (#682, ADR-0289 D4) Enter is refused whatever the
    line holds, so the hint says that and nothing about running or what the
    model sees; the stop outranks a command still running, since it is what the
    user must act on. It outranks the mode too, which the sigil drawn as the
    marker still shows: a row too narrow for both keeps "aelix is stopped"
    (Codex r1: at 24 columns it kept "Esc to clear" instead).
    """

    if stopped:
        parts: list[tuple[int, str]] = [
            (1, f"{sigil} shell"),
            (0, "aelix is stopped"),
            (2, "Enter is refused until /reload succeeds"),
        ]
        if not running:
            parts.append((4, "Esc to clear"))
        return _fit(parts, width)
    if sigil == "!":
        output = "the model sees the output next turn" if running else "the model sees the output"
    else:
        output = "the model won't see the output"
    parts = [(0, f"{sigil} shell")]
    if shell_busy:
        parts.append((1, SHELL_BUSY_NOTICE))
    elif not bare:
        parts.append((3, "Enter runs it now" if running else "Enter runs it"))
    parts.append((2, output))
    if running:
        if not bare:
            parts.append((5, "Alt+Enter runs it after the turn"))
    else:
        parts.append((4, "Esc to clear"))
    return _fit(parts, width)


def _fit(parts: list[tuple[int, str]], width: int) -> str:
    """*parts* (importance, words) in their order, the most important first kept
    and each next one only while the row still fits *width*."""

    kept: list[tuple[int, str]] = []
    for part in sorted(parts, key=lambda item: item[0]):
        trial = [p for p in parts if p in kept or p == part]
        if not kept or len(" · ".join(p for _, p in trial)) <= width:
            kept = trial
    text = " · ".join(p for _, p in kept)
    return text[: max(0, width)]


__all__ = [
    "SHELL_BUSY_NOTICE",
    "InputLexer",
    "InputStyles",
    "SubmittedLine",
    "classify_submitted",
    "input_styles",
    "mention_spans",
    "no_color",
    "paint_shell",
    "role_style",
    "shell_hint",
    "shell_sigil",
    "statusline_style",
    "submitted_input",
]
