"""#596 (ADR-0287) — the input shows what Enter will do: ``@`` references, shell mode.

Three layers:

- pure: :mod:`aelix_coding_agent.tui.input_marks` (the reference grammar against
  the completer's, shell detection against ``parse_input_line``, theme roles,
  the status hint's fitting);
- the chrome headless: the marker, the hidden sigil and the cursor clamp, the
  three Esc bindings, mid-turn Enter, the status hint, the footer's base style;
- pyte: the frame lines, the marker and the colours as a terminal paints them.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

import pytest
from _polling import wait_until
from _pyte import render_chrome_to_pyte
from aelix_coding_agent.tui import themes
from aelix_coding_agent.tui.chrome import (
    _INPUT_PLACEHOLDER,
    _SHELL_PLACEHOLDER,
    AelixChrome,
    _ModePrompt,
)
from aelix_coding_agent.tui.completion import _extract_mention
from aelix_coding_agent.tui.input import parse_input_line
from aelix_coding_agent.tui.input_marks import (
    InputLexer,
    input_styles,
    mention_spans,
    paint_shell,
    role_style,
    shell_hint,
    shell_sigil,
    statusline_style,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from rich.text import Text

# === pure: the reference grammar ============================================

_LINES = [
    "look at @src/foo.py and @README.md",
    'see @"src/my file.py" and foo@bar.com',
    '@"unclosed and more',
    "@ bare and @x",
    "a@b @c\t@d",
    "@@double",
    "",
    "no mentions here",
]


@pytest.mark.parametrize("line", _LINES)
def test_mention_spans_are_the_tokens_the_completer_completes(line: str) -> None:
    # Each span is exactly what ``_extract_mention`` replaces with the cursor at
    # its end: unquoted at the span's end, a closed quote just before its quote
    # (closed, it is no longer active), an unclosed one at the line's end.
    for start, end in mention_spans(line):
        closed_quote = line[start + 1 : start + 2] == '"' and end - start > 2 and line[end - 1] == '"'
        cursor = end - 1 if closed_quote else end
        mention = _extract_mention(line[:cursor])
        assert mention is not None, (line, start, end)
        assert mention.replace_len == cursor - start, (line, start, end)
    # And the other way round: wherever the completer sees a token longer than
    # a bare ``@``, that token lies inside a span.
    spans = mention_spans(line)
    for i in range(len(line) + 1):
        mention = _extract_mention(line[:i])
        if mention is None or mention.replace_len <= 1:
            continue
        begin = i - mention.replace_len
        assert any(s <= begin and i <= e for s, e in spans), (line, i)


def test_mention_spans_skip_emails_and_a_bare_at() -> None:
    assert mention_spans("mail foo@bar.com") == []
    assert mention_spans("@") == []
    assert mention_spans("@ x") == []
    assert mention_spans('@"a b" c') == [(0, 6)]


# === pure: shell detection agrees with the submit path =======================


@pytest.mark.parametrize(
    "text",
    ["", "!", "!!", "!ls", "!!ls", " !ls", "!!!x", "!echo a \\\nb", "hello", "/quit", "!/quit"],
)
def test_shell_sigil_is_what_parse_input_line_runs(text: str) -> None:
    kind = parse_input_line(text).kind
    expected = {"bash": "!", "bash_transient": "!!"}.get(kind, "")
    assert shell_sigil(text) == expected


# === pure: theme roles ========================================================


def test_built_in_themes_colour_shell_mode_and_the_statusline() -> None:
    assert role_style(themes.default, "shell") == "ansimagenta"
    assert role_style(themes.dark, "shell") == "ansibrightmagenta"
    assert role_style(themes.light, "shell") == "#af0087"
    assert role_style(themes.default, "statusline") == "#b2b2b2"
    assert role_style(themes.dark, "statusline") == "#b2b2b2"
    assert role_style(themes.light, "statusline") == "#4e4e4e"
    assert "shell" in themes.THEME_ROLES and "statusline" in themes.THEME_ROLES


def test_a_theme_file_without_the_new_roles_keeps_its_footer_and_gets_magenta() -> None:
    old = themes.build_theme_from_data("old", {"accent": "green", "dim": "grey50"})
    styles = input_styles(old, colorless=False)
    assert styles.mention.endswith("ansigreen")
    assert styles.shell.endswith("ansimagenta")  # the mode must still show
    assert statusline_style(old) == ""  # no dimming the theme did not ask for
    assert paint_shell(old, "x") == "\x1b[35mx\x1b[0m"
    assert paint_shell(themes.light, "x") != "\x1b[35mx\x1b[0m"


def test_a_theme_file_may_set_the_new_roles() -> None:
    mine = themes.build_theme_from_data("mine", {"shell": "#ff00ff", "statusline": "#999999"})
    assert role_style(mine, "shell") == "#ff00ff"
    assert statusline_style(mine) == "class:aelix.statusline #999999"


def test_no_color_underlines_references() -> None:
    assert "underline" in input_styles(themes.default, colorless=True).mention
    assert "underline" not in input_styles(themes.default, colorless=False).mention


# === pure: the status hint ===================================================


@pytest.mark.parametrize("width", [20, 30, 50, 80, 120])
@pytest.mark.parametrize("sigil", ["!", "!!"])
@pytest.mark.parametrize("running", [False, True])
def test_shell_hint_fits_and_keeps_the_mode_first(width: int, sigil: str, running: bool) -> None:
    hint = shell_hint(sigil, running=running, width=width)
    assert len(hint) <= width
    assert hint.startswith(f"{sigil} shell"[:width])


def test_shell_hint_words() -> None:
    assert shell_hint("!", running=False, width=200) == (
        "! shell · Enter runs it · the model sees the output · Esc to clear"
    )
    assert shell_hint("!!", running=False, width=200) == (
        "!! shell · Enter runs it · the model won't see the output · Esc to clear"
    )
    # Mid-turn Enter runs it at once; the output reaches the model next turn,
    # and Esc is the turn's interrupt key, so it is not offered.
    assert shell_hint("!", running=True, width=200) == (
        "! shell · Enter runs it now · the model sees the output next turn"
    )
    assert "Esc" not in shell_hint("!!", running=True, width=200)
    # Narrow: the output clause goes first, Esc last.
    assert shell_hint("!", running=False, width=50) == "! shell · Enter runs it · Esc to clear"
    assert shell_hint("!", running=False, width=25) == "! shell · Esc to clear"


# === pure: the lexer ===========================================================


def test_lexer_colours_references_and_not_in_shell_mode() -> None:
    styles = input_styles(themes.default, colorless=False)
    lexer = InputLexer(lambda: styles)
    line = lexer.lex_document(Document("see @a.py and x@y"))(0)
    assert (styles.mention, "@a.py") in line
    assert "".join(item[1] for item in line) == "see @a.py and x@y"
    shell = lexer.lex_document(Document("!cat @a.py\nmore @b"))
    assert shell(0) == [("", "!cat @a.py")]
    assert shell(1) == [("", "more @b")]


# === the chrome, headless ====================================================


@asynccontextmanager
async def _chrome(*, run_app: bool = False) -> AsyncGenerator[tuple[AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(console=Console(file=io.StringIO(), force_terminal=True, width=80))
        task = asyncio.create_task(chrome.run()) if run_app else None
        try:
            yield chrome, pipe
        finally:
            if task is not None:
                chrome.exit()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, timeout=3)


def _control(chrome: AelixChrome) -> BufferControl:
    assert chrome._input_window is not None
    return cast("BufferControl", chrome._input_window.content)


def _prompt(chrome: AelixChrome) -> _ModePrompt:
    return next(p for p in _control(chrome).input_processors or [] if isinstance(p, _ModePrompt))


def _line0(chrome: AelixChrome) -> str:
    """Line 0 as the BufferControl draws it (all processors applied), less the
    one blank cell BufferControl adds at the end of a line for the cursor."""

    with set_app(chrome.app):
        content = _control(chrome).create_content(80, 5)
        return "".join(item[1] for item in content.get_line(0)).removesuffix(" ")


def _escape(chrome: AelixChrome, name: str):  # noqa: ANN202
    kb = chrome.app.key_bindings
    assert kb is not None
    for binding in kb.bindings:
        keys = tuple(getattr(k, "value", str(k)) for k in binding.keys)
        if keys == ("escape",) and binding.handler.__name__ == name:
            return binding
    raise AssertionError(name)


async def test_the_sigil_becomes_the_marker_and_the_text_keeps_it() -> None:
    async with _chrome() as (chrome, _pipe):
        prompt = _prompt(chrome)
        chrome.prompt_style_provider = lambda: "class:aelix.prompt bold yellow"
        assert prompt.text == "❯ " and prompt.style.endswith("yellow")
        assert _line0(chrome) == f"❯ {_INPUT_PLACEHOLDER}"

        chrome.buffer.document = Document("!ls -la")
        assert prompt.text == "! "
        # Shell mode wins over the permission colour: the gate never sees a ``!``.
        assert prompt.style == "class:aelix.shell ansimagenta bold"
        assert _line0(chrome) == "! ls -la"
        assert chrome.buffer.text == "!ls -la"  # history / submit see the sigil

        chrome.buffer.document = Document("!!git status")
        assert prompt.text == "!! "
        assert _line0(chrome) == "!! git status"

        chrome.buffer.document = Document("!")
        assert _line0(chrome) == f"! {_SHELL_PLACEHOLDER}"

        # Shell mode is drawn mid-turn too: Enter runs the command there as well.
        chrome.set_running(True)
        chrome.buffer.document = Document("!ls")
        assert prompt.text == "! "


async def test_the_cursor_never_stands_in_front_of_the_sigil() -> None:
    async with _chrome() as (chrome, _pipe):
        buf = chrome.buffer
        buf.document = Document("!!ls", 4)
        buf.cursor_position = 0  # Home / Ctrl+A / a click on the marker
        assert buf.cursor_position == 2
        buf.cursor_position = 1
        assert buf.cursor_position == 2
        # Backspace right after the marker: ``!!`` steps down to ``!``, then out.
        buf.delete_before_cursor(1)
        assert buf.text == "!ls" and buf.cursor_position == 1
        buf.delete_before_cursor(1)
        assert buf.text == "ls" and buf.cursor_position == 0
        assert _prompt(chrome).text == "❯ "


async def test_home_ctrl_a_and_backspace_through_the_key_processor() -> None:
    async with _chrome(run_app=True) as (chrome, pipe):
        await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
        pipe.send_text("!!ls")
        await wait_until(lambda: chrome.buffer.text == "!!ls", what="typing")
        pipe.send_text("\x01")  # Ctrl+A
        await wait_until(lambda: chrome.buffer.cursor_position == 2, what="Ctrl+A to stop after !!")
        pipe.send_text("\x1b[H")  # Home
        await asyncio.sleep(0.1)
        assert chrome.buffer.cursor_position == 2
        pipe.send_text("\x7f")  # Backspace
        await wait_until(lambda: chrome.buffer.text == "!ls", what="Backspace to drop one !")
        pipe.send_text("\x7f")
        await wait_until(lambda: chrome.buffer.text == "ls", what="Backspace to leave shell mode")


async def test_the_three_esc_bindings_never_overlap() -> None:
    async with _chrome() as (chrome, _pipe):
        dismiss = _escape(chrome, "_escape_dismiss_completions")
        interrupt = _escape(chrome, "_escape_interrupt")
        clear = _escape(chrome, "_escape_clear_shell")

        def live() -> list[str]:
            with set_app(chrome.app):
                return [n for n, b in (("dismiss", dismiss), ("interrupt", interrupt), ("clear", clear)) if b.filter()]

        assert live() == []  # idle, not shell: Esc stays inert while editing
        chrome.buffer.document = Document("!ls")
        assert live() == ["clear"]
        chrome.set_running(True)
        assert live() == ["interrupt"]  # mid-turn Esc interrupts, as pi's does
        chrome.set_running(False)
        with set_app(chrome.app):
            chrome.buffer.complete_state = CompletionState(
                original_document=chrome.buffer.document, completions=[Completion("x")]
            )
            assert live() == ["dismiss"]
            chrome.buffer.complete_state = None

        clear.handler(None)  # type: ignore[arg-type]  # the handler ignores its event
        assert chrome.buffer.text == ""  # the WHOLE input, not just the sigil


async def test_esc_clears_a_shell_line_end_to_end() -> None:
    async with _chrome(run_app=True) as (chrome, pipe):
        await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
        pipe.send_text("!rm -rf build")
        await wait_until(lambda: chrome.buffer.text == "!rm -rf build", what="typing")
        pipe.send_text("\x1b")
        await wait_until(lambda: chrome.buffer.text == "", what="Esc to clear the shell line")


async def test_enter_mid_turn_runs_a_shell_line_and_steers_the_rest() -> None:
    async with _chrome(run_app=True) as (chrome, pipe):
        shells: list[str] = []
        steers: list[str] = []
        chrome.on_shell = shells.append
        chrome.on_steer = steers.append
        await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
        chrome.set_running(True)

        pipe.send_text("!echo hi\r")
        await wait_until(lambda: shells == ["!echo hi"], what="Enter to run the ! line")
        pipe.send_text("!!git status\r")
        await wait_until(lambda: shells[-1:] == ["!!git status"], what="Enter to run the !! line")
        pipe.send_text("keep going\r")
        await wait_until(lambda: steers == ["keep going"], what="Enter to steer prose")
        assert chrome.buffer.text == ""

        # Idle, a ``!`` line is an ordinary submit: the input loop runs it.
        chrome.set_running(False)
        pipe.send_text("!pwd\r")
        assert await asyncio.wait_for(chrome.get_input(), timeout=5) == "!pwd"
        assert shells == ["!echo hi", "!!git status"]


async def test_status_row_says_what_enter_does_ahead_of_extension_statuses() -> None:
    async with _chrome() as (chrome, _pipe):
        chrome.set_status("ext", "EXT-STATUS")
        assert chrome._render_status() == "EXT-STATUS"
        chrome.buffer.document = Document("!ls")
        status = chrome._render_status()
        assert "! shell · Enter runs it" in status
        assert status.index("! shell") < status.index("EXT-STATUS")
        chrome.set_running(True)
        assert "Enter runs it now" in chrome._render_status()


async def test_footer_style_comes_from_the_provider_and_survives_its_failure() -> None:
    async with _chrome() as (chrome, _pipe):
        assert chrome._footer_style() == "class:aelix.chrome"
        chrome.footer_style_provider = lambda: statusline_style(themes.default)
        assert chrome._footer_style() == "class:aelix.chrome class:aelix.statusline #b2b2b2"

        def broken() -> str:
            raise RuntimeError("boom")

        chrome.footer_style_provider = broken
        assert chrome._footer_style() == "class:aelix.chrome"


async def test_no_color_draws_shell_mode_with_heavy_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _chrome() as (chrome, _pipe):
        chrome.buffer.document = Document("!ls")
        assert chrome._frame_line_char() == "─"
        monkeypatch.setenv("NO_COLOR", "1")
        assert chrome._frame_line_char() == "━"
        chrome.buffer.document = Document("hello")
        assert chrome._frame_line_char() == "─"


# === pyte: what a terminal paints =============================================


def _row(screen, needle: str) -> int:  # noqa: ANN001
    for y, line in enumerate(screen.display):
        if needle in line:
            return y
    raise AssertionError(f"{needle!r} not on screen:\n" + "\n".join(screen.display))


async def test_pyte_frame_marker_and_colours_in_shell_mode() -> None:
    def build(chrome: AelixChrome) -> None:
        chrome.input_theme = lambda: themes.default
        chrome.footer_style_provider = lambda: statusline_style(themes.default)
        chrome.set_footer_line("FOOTER \x1b[31mRED\x1b[0m tail")
        chrome.buffer.document = Document("!ls -la")

    screen = await render_chrome_to_pyte(build_state=build)
    y = _row(screen, "! ls -la")
    assert screen.display[y].startswith("! ls -la")
    assert set(screen.display[y - 1].rstrip()) == {"─"}
    assert set(screen.display[y + 1].rstrip()) == {"─"}
    marker = screen.buffer[y][0]
    assert marker.fg == "magenta" and marker.bold
    assert screen.buffer[y - 1][0].fg == "magenta"  # the frame takes the shell colour
    hint = _row(screen, "! shell · Enter runs it")
    assert screen.buffer[hint][0].fg == "magenta"
    footer = _row(screen, "FOOTER")
    assert screen.buffer[footer][0].fg == "b2b2b2"  # the statusline layer
    red = screen.display[footer].index("RED")
    assert screen.buffer[footer][red].fg == "red"  # a segment's own colour wins
    tail = screen.display[footer].index("tail")
    assert screen.buffer[footer][tail].fg == "b2b2b2"  # and its reset falls back to it


async def test_pyte_references_take_the_accent_and_the_frame_is_dim() -> None:
    def build(chrome: AelixChrome) -> None:
        chrome.input_theme = lambda: themes.default
        chrome.buffer.document = Document("look at @src/foo.py and foo@bar.com")

    screen = await render_chrome_to_pyte(build_state=build)
    y = _row(screen, "look at @src/foo.py")
    line = screen.display[y]
    assert screen.buffer[y][line.index("@src")].fg == "blue"
    assert screen.buffer[y][line.index("foo@bar")].fg == "default"
    # ``❯`` keeps the permission colour (the CSS ``cyan`` of ``_DEFAULT_PROMPT_STYLE``).
    assert screen.buffer[y][0].fg == "00ffff"
    assert screen.buffer[y - 1][0].fg == "brightblack"  # the frame: the theme's ``dim``


async def test_pyte_the_frame_stops_at_the_render_width() -> None:
    long = "!" + "x" * 70

    def build(chrome: AelixChrome) -> None:
        chrome.render_width = lambda: 40
        chrome.buffer.document = Document(long)

    screen = await render_chrome_to_pyte(cols=80, build_state=build)
    y = _row(screen, "! xxxx")
    assert screen.display[y].rstrip() == "! " + "x" * 38  # wraps at 40, not 80
    assert screen.display[y - 1].rstrip() == "─" * 40
    assert screen.display[y + 2].rstrip() == "─" * 40  # below the wrapped second row
    assert screen.display[y + 1].rstrip() == "x" * 32


# === the completion menu has rows of its own (#508 verify V5) ==================
#
# The menu used to hang under the cursor as a Float, and a Float adds no height
# to the inline app: pinned on 24 rows it covered the four rows under the input
# (the frame line, the status row with the shell hint, the footer) with four of
# its entries. It is now drawn over rows the layout reserves for it.


def _menu_open(chrome: AelixChrome, n: int) -> None:
    with set_app(chrome.app):
        chrome.buffer.complete_state = CompletionState(
            original_document=chrome.buffer.document,
            completions=[Completion(f"/cmd-{i:02d}", start_position=-len(chrome.buffer.text)) for i in range(n)],
        )


async def test_pinned_the_menu_opens_upward_and_leaves_the_status_and_footer() -> None:
    from test_bottom_pin_632 import _FOOTER, _fill_screen, _row_of, _running, _settled

    async with _running(24, 80) as (chrome, term):
        await _fill_screen(chrome, term)
        chrome.set_status("hint", "STATUS-596")
        grid = await _settled(chrome, term, why="pinned")
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert input_row is not None, term.dump()

        chrome.buffer.text = "/cmd"
        _menu_open(chrome, 13)
        grid = await _settled(chrome, term, why="a 13-entry menu")
        rows = [y for y, line in enumerate(grid) if "/cmd-" in line]
        assert len(rows) == 8, term.dump()  # its full height, not three or four
        typed = _row_of(grid, "❯ /cmd")
        assert typed == input_row, term.dump()  # the input keeps its row
        assert max(rows) == input_row - 2, term.dump()  # just above the frame line
        assert set(grid[input_row - 1].strip()) == {"─"}, term.dump()
        assert _row_of(grid, "STATUS-596") == 22 and _row_of(grid, _FOOTER) == 23, term.dump()
        assert chrome._pinned


async def test_on_a_screen_not_yet_full_the_menu_drops_down_and_does_not_pin() -> None:
    from test_bottom_pin_632 import _FOOTER, _row_of, _running, _settled

    async with _running(40, 80) as (chrome, term):
        # 27 lines and the 7-row chrome leave the screen unfilled; the 8-row menu
        # would fill it, and scrolls it by two, which must not pin it.
        await chrome.print_above_many([Text(f"banner-{i}") for i in range(27)])
        grid = await _settled(chrome, term, why="a transcript short of the bottom")
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert input_row is not None and not chrome._pinned, term.dump()

        chrome.buffer.text = "/cmd"
        _menu_open(chrome, 13)
        grid = await _settled(chrome, term, why="a 13-entry menu")
        rows = [y for y, line in enumerate(grid) if "/cmd-" in line]
        typed = _row_of(grid, "❯ /cmd")
        footer = _row_of(grid, _FOOTER)
        assert len(rows) == 8 and typed is not None and footer is not None, term.dump()
        assert min(rows) == typed + 2, term.dump()  # a dropdown under the frame line
        assert footer == 39 and footer > max(rows) + 1, term.dump()  # pushed down, not covered
        assert not chrome._pinned  # an open menu alone does not pin the screen

        chrome.buffer.reset()
        grid = await _settled(chrome, term, why="the menu closed")
        assert not chrome._pinned, term.dump()
        last = _row_of(grid, "banner-26")
        input_row = _row_of(grid, _INPUT_PLACEHOLDER)
        assert last is not None and input_row is not None, term.dump()
        assert input_row - last <= 3, term.dump()  # the chrome follows the transcript
