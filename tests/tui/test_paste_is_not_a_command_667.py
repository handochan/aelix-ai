"""#667 — a line that begins with a collapsed paste is a prompt, whatever the paste begins with.

A paste of 6 or more lines (or 1000 or more characters) is drawn
``[Pasted text #1 +N lines]``. Before this, Enter expanded it and read the
expanded text as a command: a paste whose first line was ``/quit`` closed the
TUI, ``/new`` swapped the session, ``/export`` wrote a file named after the
pasted lines, and a stack trace beginning ``/Users/…`` was dropped as
"Unknown command". The owner's decision (a): a line whose command name is not on
screen is not run as a command — ADR-0287's ``!`` rule widened to ``/``. A
command typed before a paste (``/name [Pasted text …]``) still takes the paste
as its argument.
"""

from __future__ import annotations

import pytest
from _polling import wait_until
from aelix_coding_agent.tui.input import ParsedInput
from aelix_coding_agent.tui.input_marks import SubmittedLine, submitted_input
from test_run_tui_smoke import (
    FakeHarness,
    _harness_chrome,
    _launch,
    _quit_within,
    _spy_commits,
)

_PASTE = "[Pasted text #1 +7 lines]"
_TAIL = "\n" + "\n".join(f"pasted line {i}" for i in range(6))


def _expand(text: str, paste: str) -> str:
    return text.replace(_PASTE, paste)


# === pure: what Enter does with the typed buffer ===============================

# The first pasted line of every row the issue measured.
_FIRST_LINES = [
    "/quit",
    "/exit",
    "/new",
    "/clear",
    "/reload",
    "/login",
    "/trust",
    "/compact",
    "/name",
    "/model",
    "/export",
    "/kick now",
    "/kick",
    "/tpl x",
    "/tpl",
    "/skill:sk",
    "/Users/me/proj/app.py:12: error: boom",
    "  /quit",
]


@pytest.mark.parametrize("first", _FIRST_LINES)
@pytest.mark.parametrize("typed", [_PASTE, f"  {_PASTE}", f"{_PASTE} and what I typed"])
def test_a_line_that_begins_with_a_paste_is_a_prompt(typed: str, first: str) -> None:
    paste = first + _TAIL
    expanded = _expand(typed, paste).strip()
    assert submitted_input(typed, lambda t: _expand(t, paste)) == ParsedInput(
        "prompt", expanded, literal=True
    )


def test_a_paste_that_begins_with_prose_is_a_prompt_as_before() -> None:
    paste = "hello there" + _TAIL
    assert submitted_input(_PASTE, lambda t: _expand(t, paste)) == ParsedInput(
        "prompt", paste.strip()
    )


@pytest.mark.parametrize(
    ("typed", "expected_kind"),
    [
        # A command typed before a paste takes the paste as its argument.
        (f"/name {_PASTE}", "prompt"),
        (f"/kick {_PASTE}", "prompt"),
        (f"/quit {_PASTE}", "quit"),
        (f"/reload {_PASTE}", "reload"),
    ],
)
def test_a_command_typed_before_a_paste_is_still_a_command(typed: str, expected_kind: str) -> None:
    paste = "first" + _TAIL
    parsed = submitted_input(typed, lambda t: _expand(t, paste))
    assert parsed.kind == expected_kind
    assert not parsed.literal
    if expected_kind == "prompt":
        assert parsed.text == _expand(typed, paste).strip()


def test_a_short_paste_is_typed_text_and_its_command_runs() -> None:
    # Under 6 lines and 1000 characters a paste is inserted as text: the name is
    # on screen, so the line is the command it reads as.
    assert submitted_input("/quit", lambda t: t) == ParsedInput("quit")
    assert submitted_input("/name x", lambda t: t) == ParsedInput("prompt", "/name x")


# === the input loop ==========================================================


def _bracketed(text: str) -> str:
    return f"\x1b[200~{text}\x1b[201~"


async def _paste_and_enter(chrome, pipe, body: str) -> None:  # noqa: ANN001
    pipe.send_text(_bracketed(body))
    await wait_until(lambda: "[Pasted text" in chrome.buffer.text, what="the paste to collapse")
    pipe.send_text("\r")


@pytest.mark.parametrize(
    "first",
    ["/quit", "/new", "/clear", "/name", "/help", "/Users/me/proj/app.py:12: error: boom"],
)
async def test_an_idle_paste_beginning_with_a_slash_reaches_the_model(first: str) -> None:
    # Before: /quit closed the TUI, /clear wiped the transcript, /name named the
    # session after the pasted lines, the stack trace was "Unknown command".
    body = first + _TAIL
    async with _harness_chrome() as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        cleared: list[int] = []
        chrome.clear = lambda: cleared.append(1)  # type: ignore[method-assign]
        task = _launch(runtime, chrome)
        await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
        await _paste_and_enter(chrome, pipe, body)
        await wait_until(
            lambda: bool(runtime.harness.prompts) or task.done(),
            what="the paste to reach the model (or the TUI to end)",
        )
        assert not task.done(), "the paste ran as a command"
        assert runtime.harness.prompts == [(body, "interactive")]
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert cleared == []
    assert not any("Unknown command" in c for c in commits)
    assert not any("Session naming" in c for c in commits)
    # The echo is the whole paste, as the model got it.
    assert any(c.startswith(f"» {first}") and "pasted line 5" in c for c in commits)


class _NamedSession:
    def __init__(self) -> None:
        self.names: list[str] = []

    async def append_session_name(self, name: str) -> None:
        self.names.append(name)


async def test_a_command_typed_before_a_paste_takes_it_as_its_argument() -> None:
    harness = FakeHarness()
    session = _NamedSession()
    harness.session = session  # type: ignore[assignment]
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
        pipe.send_text("/name ")
        await wait_until(lambda: chrome.buffer.text == "/name ", what="typing /name")
        await _paste_and_enter(chrome, pipe, "first" + _TAIL)
        await wait_until(lambda: bool(session.names), what="/name to run")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert session.names == ["first" + _TAIL]
    assert harness.prompts == []


async def test_a_paste_beginning_with_quit_steers_mid_turn_as_drawn() -> None:
    # The control the issue measured: mid-turn the paste was already steered.
    async with _harness_chrome() as (runtime, chrome, pipe):
        steers: list[str] = []
        task = _launch(runtime, chrome)
        await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
        chrome.on_steer = steers.append
        chrome.set_running(True)
        body = "/quit" + _TAIL
        await _paste_and_enter(chrome, pipe, body)
        await wait_until(lambda: steers == [body], what="Enter to steer the paste")
        chrome.set_running(False)
        pipe.send_text("/quit\n")
        await _quit_within(task)


async def test_a_steer_that_lands_after_the_turn_keeps_what_the_input_drew() -> None:
    # The late-steer race: the turn ended between Enter and the steer, which is
    # then submitted as an input line. It was re-read from its text, so a paste
    # beginning with /quit closed the TUI.
    body = "/quit" + _TAIL
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await wait_until(lambda: chrome.app.is_running, what="the chrome to start")
        assert chrome.on_steer is not None
        chrome.on_steer(SubmittedLine(body, ParsedInput("prompt", body, literal=True)))
        await wait_until(
            lambda: bool(runtime.harness.prompts) or task.done(),
            what="the steer to reach the model (or the TUI to end)",
        )
        assert not task.done(), "the late steer ran as /quit"
        assert runtime.harness.prompts == [(body, "interactive")]
        # A typed /quit that lands late is still a quit.
        chrome.on_steer(SubmittedLine("/quit", ParsedInput("quit")))
        await _quit_within(task)
