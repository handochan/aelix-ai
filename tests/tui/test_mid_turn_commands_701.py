"""#701 (ADR-0292) — a ``/`` line Entered while the model answers is a command, not a steer.

Before: Enter (and Alt+Enter) on ``/thinking high`` mid-turn sent the text to the
model as a steering (or follow-up) message, with no word on screen; the command
dispatch lives in the input loop, which is parked in ``harness.prompt`` for the
whole turn. Now the chrome asks the loop first: a read-only or setting command
runs at once (``MID_TURN_RUNS``), anything else is refused with the line kept in
the input and the reason on the row under it, and plain text steers as before.
A dialog the turn opens over a picker the user opened mid-turn no longer leaves
the picker unanswered: the modal slot stacks.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.commands import (
    _MID_TURN_REFUSED,
    BUILTIN_COMMANDS,
    MID_TURN_RUNS,
    match_command,
    mid_turn_refusal,
)
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import DummyOutput

from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime, _quit_within, _spy_commits, _wait

# --- the table --------------------------------------------------------------------


def test_every_builtin_is_placed() -> None:
    """A new built-in must say whether it runs mid-turn, or it is refused by default."""

    names = {c.name for c in BUILTIN_COMMANDS}
    assert names <= MID_TURN_RUNS | set(_MID_TURN_REFUSED), (
        names - MID_TURN_RUNS - set(_MID_TURN_REFUSED)
    )
    assert not MID_TURN_RUNS & set(_MID_TURN_REFUSED)


@pytest.mark.parametrize(
    ("line", "runs"),
    [
        ("/thinking high", True),
        ("/thinking", True),
        ("/model", True),
        ("/model gpt-4o", True),
        ("/settings", True),
        ("/skill", True),
        ("/tools", True),
        ("/help", True),
        ("/name", True),
        ("/name new title", False),
        ("/agents", True),
        ("/agents show scout", True),
        ("/agents use scout", False),
        ("/agents run scout task", False),
        ("/new", False),
        ("/resume", False),
        ("/fork", False),
        ("/clone", False),
        ("/import x.jsonl", False),
        ("/compact", False),
        ("/clear", False),
        ("/login", False),
        ("/logout", False),
        ("/extension", False),
        ("/reload", False),
        ("/quit", True),  # verify V1: it stops the answer and quits
        ("/exit", True),
    ],
)
def test_mid_turn_refusal(line: str, runs: bool) -> None:
    command = match_command(line, BUILTIN_COMMANDS)
    assert command is not None
    args = line.split(maxsplit=1)[1] if " " in line else ""
    assert (mid_turn_refusal(command, args) is None) is runs


# --- the chrome asks before it steers ------------------------------------------------


@asynccontextmanager
async def _chrome() -> AsyncGenerator[tuple[AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        task = asyncio.create_task(chrome.run())
        try:
            await _wait(lambda: chrome.app.is_running)
            yield chrome, pipe
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)


@pytest.mark.parametrize("key", ["\r", "\x1b\r"])  # Enter, Alt+Enter
async def test_a_command_the_host_ran_clears_the_input_and_steers_nothing(key: str) -> None:
    async with _chrome() as (chrome, pipe):
        sent: list[str] = []
        asked: list[str] = []
        chrome.on_steer = lambda line: sent.append(str(line))
        chrome.on_follow_up = lambda line: sent.append(str(line))
        chrome.on_mid_turn_command = lambda line: (asked.append(str(line)), ("ran", ""))[1]
        chrome.set_running(True)
        pipe.send_text("/thinking high")
        await _wait(lambda: chrome.buffer.text == "/thinking high")
        pipe.send_text(key)
        await _wait(lambda: asked)
        await _wait(lambda: chrome.buffer.text == "")
        assert asked == ["/thinking high"] and sent == []


@pytest.mark.parametrize("key", ["\r", "\x1b\r"])
async def test_a_refused_command_keeps_the_draft_and_says_why_under_it(key: str) -> None:
    async with _chrome() as (chrome, pipe):
        sent: list[str] = []
        chrome.on_steer = lambda line: sent.append(str(line))
        chrome.on_follow_up = lambda line: sent.append(str(line))
        chrome.on_mid_turn_command = lambda _line: (
            "refused",
            "/new can't run until the answer ends — x",
        )
        chrome.set_running(True)
        pipe.send_text("/new")
        await _wait(lambda: chrome.buffer.text == "/new")
        pipe.send_text(key)
        await _wait(lambda: "can't run" in chrome._render_status())
        assert chrome.buffer.text == "/new" and sent == []
        # An edit takes the reason away; the turn's end does too.
        pipe.send_text(" ")
        await _wait(lambda: "can't run" not in chrome._render_status())
        pipe.send_text("\x7f")
        await _wait(lambda: chrome.buffer.text == "/new")
        assert "can't run" not in chrome._render_status()


async def test_plain_text_still_steers() -> None:
    async with _chrome() as (chrome, pipe):
        sent: list[str] = []
        chrome.on_steer = lambda line: sent.append(str(line))
        chrome.on_mid_turn_command = lambda _line: None
        chrome.set_running(True)
        pipe.send_text("keep going\r")
        await _wait(lambda: sent)
        assert sent == ["keep going"]


# --- the modal slot stacks -----------------------------------------------------------


def _modal(text: str) -> Window:
    return Window(FormattedTextControl(text, focusable=True), height=1)


async def test_a_dialog_over_a_picker_hands_the_slot_back() -> None:
    async with _chrome() as (chrome, _pipe):
        picker, dialog = _modal("PICKER"), _modal("DIALOG")
        chrome.mount_modal(picker, focus=picker)
        chrome.focus(picker)
        chrome.mount_modal(dialog, focus=dialog)
        assert chrome._modal is dialog
        chrome.unmount_modal(dialog)
        assert chrome._modal is picker and chrome.app.layout.has_focus(picker)
        chrome.unmount_modal(picker)
        assert not chrome.is_modal_open()
        chrome.unmount_modal(picker)  # a second unmount of the same modal is nothing
        assert not chrome.is_modal_open()


async def test_a_covered_modal_that_leaves_does_not_take_the_one_on_top() -> None:
    async with _chrome() as (chrome, _pipe):
        picker, dialog = _modal("PICKER"), _modal("DIALOG")
        chrome.mount_modal(picker, focus=picker)
        chrome.mount_modal(dialog, focus=dialog)
        chrome.unmount_modal(picker)  # the picker's task was cancelled under the dialog
        assert chrome._modal is dialog
        chrome.unmount_modal(dialog)
        assert not chrome.is_modal_open()


async def test_show_modal_over_show_modal_resolves_both_in_turn() -> None:
    from aelix_coding_agent.tui.overlay import show_modal

    async with _chrome() as (chrome, _pipe):
        futures: dict[str, asyncio.Future[Any]] = {}

        def build(name: str):
            def _build(result: asyncio.Future[Any]) -> Window:
                futures[name] = result
                return _modal(name)

            return _build

        first = asyncio.ensure_future(show_modal(chrome, build("PICKER")))
        await _wait(lambda: "PICKER" in futures)
        second = asyncio.ensure_future(show_modal(chrome, build("DIALOG")))
        await _wait(lambda: "DIALOG" in futures)
        futures["DIALOG"].set_result("yes")
        assert await second == "yes"
        assert chrome.is_modal_open()  # the picker is back, not left unanswered
        futures["PICKER"].set_result("high")
        assert await first == "high"
        assert not chrome.is_modal_open()


# --- through the real input loop ----------------------------------------------------


class _TurnHarness(FakeHarness):
    """A turn that runs until the test releases it; steer / follow-up recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.steered: list[str] = []
        self.followed: list[str] = []
        self.levels: list[str] = []
        self.state = SimpleNamespace(thinking_level="medium")
        self._state = self.state
        self.current_model = SimpleNamespace(id="m-1", provider="p", reasoning=True)

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        self.prompts.append((text, source))
        self.started.set()
        await self.release.wait()
        self.release.clear()
        return []

    async def steer(self, text: str) -> None:
        self.steered.append(text)

    async def follow_up(self, text: str) -> None:
        self.followed.append(text)

    async def set_thinking_level(self, level: str) -> None:
        self.levels.append(level)
        self.state.thinking_level = level


@asynccontextmanager
async def _tui(harness: _TurnHarness) -> AsyncGenerator[tuple[AelixChrome, PipeInput, list[str]]]:
    from aelix_coding_agent.tui.shell import run_tui

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                FakeRuntime(harness),  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
            )
        )
        try:
            await _wait(lambda: chrome.app.is_running)
            yield chrome, pipe, commits
        finally:
            harness.release.set()
            pipe.send_text("\x15/quit\n")
            with contextlib.suppress(Exception):
                await _quit_within(task)


async def _type(chrome: AelixChrome, pipe: PipeInput, text: str, key: str = "\r") -> None:
    pipe.send_text(text)
    await _wait(lambda: chrome.buffer.text == text)
    pipe.send_text(key)


_APPLIES = " · applies from your next message (or a retry of this one)"


def _spy_texts(chrome: AelixChrome) -> list[Any]:
    """The Rich ``Text`` lines committed, with their styles (``_spy_commits`` keeps the plain text)."""

    from rich.text import Text

    texts: list[Any] = []
    inner = chrome.print_above_many

    async def many(renderables: Any, **kw: Any) -> Any:
        texts.extend(r for r in renderables if isinstance(r, Text))
        return await inner(renderables, **kw)

    chrome.print_above_many = many  # type: ignore[method-assign]
    return texts


async def test_thinking_mid_turn_runs_and_says_when_it_applies() -> None:
    harness = _TurnHarness()
    async with _tui(harness) as (chrome, pipe, commits):
        texts = _spy_texts(chrome)
        pipe.send_text("write a long answer\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/thinking high")
        await _wait(lambda: harness.levels)
        await _wait(lambda: any("applies from your next message" in c for c in commits))
        harness.release.set()
        await _wait(lambda: not chrome.running)
    assert harness.levels == ["high"] and harness.steered == [] and harness.followed == []
    # Fix round 2: the setter's own line says when it applies, in one dim line;
    # "thinking → high" and then "Applies from your next message, or a retry of
    # this one: thinking high." said it twice. Codex 701-thinking-retry / verify
    # V9: true whether it lands under a finished answer or a retry reads it.
    assert [c for c in commits if "thinking" in c] == ["thinking → high" + _APPLIES]
    [line] = [t for t in texts if "thinking" in t.plain]
    assert str(line.style) == "dim"


@pytest.mark.parametrize(
    ("caution", "line", "style"),
    [
        (None, "model → m-2 (p)", "dim"),
        ("not in the catalog", "⚠ switched to p/m-2 — not in the catalog", "yellow"),
    ],
)
async def test_model_mid_turn_says_when_it_applies_on_its_own_line(
    monkeypatch: pytest.MonkeyPatch, caution: str | None, line: str, style: str
) -> None:
    from aelix_coding_agent.cli import model_switch

    harness = _TurnHarness()
    new = SimpleNamespace(id="m-2", provider="p", reasoning=True)

    async def switch(_args: str, *, harness: Any, **_k: Any) -> Any:
        harness.current_model = new
        return SimpleNamespace(model=new, caution=caution, refusal=None)

    async def set_model(_model: Any) -> None:  # the handler's hasattr gate; never called
        raise AssertionError("switch_model_argument switches")

    monkeypatch.setattr(model_switch, "switch_model_argument", switch)
    harness.set_model = set_model  # type: ignore[attr-defined]
    async with _tui(harness) as (chrome, pipe, commits):
        texts = _spy_texts(chrome)
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/model p/m-2")
        await _wait(lambda: any("m-2" in c for c in commits))
        harness.release.set()
        await _wait(lambda: not chrome.running)
    assert [c for c in commits if "m-2" in c] == [line + _APPLIES]
    [text] = [t for t in texts if "m-2" in t.plain]
    assert str(text.style) == style  # a warning keeps its colour
    assert not any("Applies from" in c for c in commits)


async def test_a_setter_that_ends_after_the_turn_says_the_idle_line() -> None:
    """The answer was over before the setter's line was said: nothing applies
    "from your next message" that does not at idle (``MID_TURN`` reads the turn)."""

    harness = _TurnHarness()
    gate = asyncio.Event()
    real = harness.set_thinking_level

    async def slow(level: str) -> None:
        await gate.wait()
        await real(level)

    harness.set_thinking_level = slow  # type: ignore[method-assign]
    async with _tui(harness) as (chrome, pipe, commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/thinking high")
        await _wait(lambda: chrome.buffer.text == "")
        harness.release.set()
        await _wait(lambda: not chrome.running)
        gate.set()
        await _wait(lambda: any("thinking →" in c for c in commits))
    assert [c for c in commits if "thinking" in c] == ["thinking → high"]


@pytest.mark.parametrize("picker", ["thinking", "model"])
async def test_the_pickers_say_it_on_their_line_while_a_turn_runs(picker: str) -> None:
    """A picker commits from the task the mid-turn command runs in (``MID_TURN``)."""

    import contextvars

    from aelix_ai.streaming import Model
    from aelix_coding_agent.tui.mid_turn import MID_TURN
    from aelix_coding_agent.tui.model_picker import run_model_picker
    from aelix_coding_agent.tui.thinking_picker import run_thinking_picker

    from tests.tui.test_model_picker import _FakeHarness, _FakeRegistry

    committed: list[Any] = []
    if picker == "thinking":
        harness: Any = _TurnHarness()
        harness.current_model = SimpleNamespace(id="m-1", reasoning=True, thinking_level_map={})
        idle = "thinking → high"

        async def pick() -> None:
            async def select(_title: str, options: list[str]) -> str | None:
                return next(o for o in options if o.endswith("high"))

            await run_thinking_picker(harness=harness, select=select, commit=committed.append)

    else:
        models = [Model(id="a", provider="p"), Model(id="b", provider="p")]
        harness = _FakeHarness(models[0])
        idle = "model → b"

        async def pick() -> None:
            async def select(_title: str, options: list[str], detail: Any = None) -> str | None:
                return options[1]

            await run_model_picker(
                registry=_FakeRegistry(models),
                harness=harness,
                select=select,
                commit=committed.append,
            )

    async def run(running: bool | None) -> Any:
        if running is not None:
            MID_TURN.set(lambda: running)
        committed.clear()
        await pick()
        [line] = committed
        return line

    loop = asyncio.get_running_loop()
    for running, plain, style in [
        (True, idle + _APPLIES, "dim"),
        (False, idle, "green"),  # the turn is over
        (None, idle, "green"),  # idle
    ]:
        line = await loop.create_task(run(running), context=contextvars.copy_context())
        assert (line.plain, str(line.style)) == (plain, style)


async def test_the_settings_thinking_row_mid_turn_says_it_on_its_line(tmp_path: Any) -> None:
    from aelix_ai.settings import SettingsManager
    from aelix_coding_agent.tui.shell import run_tui

    harness = _TurnHarness()
    harness.current_model = SimpleNamespace(id="m-1", reasoning=True, thinking_level_map={})

    async def cycle() -> str:
        harness.state.thinking_level = "high"
        return "high"

    harness.cycle_thinking_level = cycle  # type: ignore[attr-defined]
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                FakeRuntime(harness),  # type: ignore[arg-type]
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=SettingsManager.in_memory({}),
            )
        )
        try:
            await _wait(lambda: chrome.app.is_running)
            await _wait(lambda: bool(harness.runtime.bound))
            picks = {"n": 0}

            async def select(_title: str, options: list[str], **_kw: Any) -> str | None:
                picks["n"] += 1  # the second pass closes the menu
                return (
                    next(o for o in options if o.startswith("Thinking level"))
                    if picks["n"] == 1
                    else None
                )

            harness.runtime.bound[0].select = select
            pipe.send_text("go\n")
            await asyncio.wait_for(harness.started.wait(), 5)
            await _type(chrome, pipe, "/settings")
            await _wait(lambda: any("thinking level →" in c for c in commits))
        finally:
            harness.release.set()
            pipe.send_text("\x15/quit\n")
            with contextlib.suppress(Exception):
                await _quit_within(task)
    assert [c for c in commits if "thinking level →" in c] == [
        "thinking level → high (persisted as default)" + _APPLIES
    ]


async def test_alt_enter_on_a_command_runs_it_too() -> None:
    harness = _TurnHarness()
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/thinking low", key="\x1b\r")
        await _wait(lambda: harness.levels)
    assert harness.levels == ["low"] and harness.followed == [] and harness.steered == []


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("/new", "/new can't run until the answer ends — it replaces the session"),
        ("/reload", "/reload can't run until the answer ends — it rebuilds the session"),
        ("/compact", "/compact can't run until the answer ends"),
        ("/nosuch", "Unknown command: /nosuch — type /help"),
    ],
)
async def test_an_unsafe_or_unknown_command_is_refused_and_kept(line: str, why: str) -> None:
    harness = _TurnHarness()
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, line)
        await _wait(lambda: why in chrome._render_status())
        assert chrome.buffer.text == line
        for _ in range(10):
            await asyncio.sleep(0)
        assert harness.steered == [] and harness.levels == []
        # The turn ends: the reason goes, the draft stays, and Enter runs it as at idle.
        harness.release.set()
        await _wait(lambda: not chrome.running)
        assert why not in chrome._render_status() and chrome.buffer.text == line
        pipe.send_text("\x15")  # clear it before the context exits
        await _wait(lambda: chrome.buffer.text == "")


async def test_plain_text_mid_turn_still_steers() -> None:
    harness = _TurnHarness()
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "also mention rivers")
        await _wait(lambda: harness.steered)
    assert harness.steered == ["also mention rivers"]


async def test_a_line_typed_under_a_running_command_is_queued_as_before() -> None:
    """Not a turn: the loop is in a handler, and the line is its next input (#604 D15)."""

    harness = _TurnHarness()
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("/thinking high\n")
        await _wait(lambda: harness.levels == ["high"])
        assert harness.steered == [] and harness.prompts == []


# --- fix round 1 -------------------------------------------------------------------


class _QuitHarness(_TurnHarness):
    """An abort ends the turn, as the real harness's does."""

    async def abort(self, *, withdraw_unanswered: bool = False) -> None:
        self.aborts += 1
        self.release.set()


async def test_quit_mid_turn_stops_the_answer_and_quits() -> None:
    """Verify V1 — pi runs ``/quit`` ahead of its streaming branch; it was refused."""

    from aelix_coding_agent.tui.shell import run_tui

    harness = _QuitHarness()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                FakeRuntime(harness),  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
            )
        )
        try:
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("write a long answer\n")
            await asyncio.wait_for(harness.started.wait(), 5)
            await _type(chrome, pipe, "/quit")
            code = await asyncio.wait_for(task, 10)
        finally:
            harness.release.set()
            if not task.done():
                pipe.send_text("\x15/quit\n")
                with contextlib.suppress(Exception):
                    await _quit_within(task)
    assert code == 0 and harness.aborts >= 1
    assert harness.steered == [] and harness.followed == []
    assert harness.prompts == [("write a long answer", "interactive")]
    assert not any("done ·" in c or c.startswith("stopped ·") for c in commits)


class _CommandHarness(_TurnHarness):
    """``/probe`` is an extension's command; ``/review`` a prompt template; ``deploy`` a skill."""

    def __init__(self) -> None:
        super().__init__()
        self.ran: list[tuple[str, bool]] = []
        self.prompt_templates = [
            SimpleNamespace(name="review", description="", content="Review $1 carefully.")
        ]
        self.skills = []

        async def handler(args: str, _ctx: Any) -> str:
            self.ran.append((args, not self.release.is_set()))
            return "probe ran"

        resolved = SimpleNamespace(
            invocation_name="probe", command=SimpleNamespace(handler=handler, description="")
        )
        self.extension_runner = SimpleNamespace(
            extensions=[],
            get_command=lambda name: resolved if name == "probe" else None,
            get_registered_commands=lambda: [resolved],
        )

    def make_command_context(self, *, repo: Any = None, session_runtime: Any = None) -> Any:
        return SimpleNamespace()


async def test_an_extension_command_mid_turn_runs_now_and_reaches_no_model() -> None:
    """Verify V2 — pi ``agent-session.ts:2144-2153``: run at once, never sent."""

    harness = _CommandHarness()
    async with _tui(harness) as (chrome, pipe, commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/probe now")
        await _wait(lambda: harness.ran)
        await _wait(lambda: chrome.buffer.text == "")
        assert harness.ran == [("now", True)]  # while the turn still runs
        assert harness.steered == [] and harness.followed == []
        harness.release.set()
        await _wait(lambda: any(c == "probe ran" for c in commits))
    assert harness.prompts == [("go", "interactive")]


@pytest.mark.parametrize(("key", "queue"), [("\r", "steered"), ("\x1b\r", "followed")])
async def test_a_template_mid_turn_is_expanded_and_its_text_queued(key: str, queue: str) -> None:
    """Verify V2 — pi ``agent-session.ts:2196-2198``: the expansion is steered
    (Alt+Enter: a follow-up), never the ``/review`` text."""

    harness = _CommandHarness()
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/review auth.py", key=key)
        await _wait(lambda: getattr(harness, queue))
        await _wait(lambda: chrome.buffer.text == "")
    assert getattr(harness, queue) == ["Review auth.py carefully."]
    assert not any("/review" in t for t in harness.steered + harness.followed)


async def test_a_skill_mid_turn_is_expanded_and_steered(tmp_path: Any) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: deploy\ndescription: d\n---\nShip it with care.\n")
    harness = _CommandHarness()
    harness.skills = [
        SimpleNamespace(
            name="deploy",
            description="d",
            file_path=str(skill_file),
            base_dir=str(tmp_path),
            disable_model_invocation=False,
            content="Ship it with care.",
        )
    ]
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/skill:deploy now")
        await _wait(lambda: harness.steered)
    [text] = harness.steered
    assert "Ship it with care." in text and text.rstrip().endswith("now")
    assert not text.startswith("/skill:")


def test_the_queued_row_and_echo_show_the_typed_line() -> None:
    """The harness queues the expansion; the pending row and the echo say what was typed."""

    from aelix_coding_agent.tui.queued_messages import QueuedMessages

    queued: list[Any] = []
    harness = SimpleNamespace(_steering_queue=SimpleNamespace(_messages=queued))
    echoes: list[tuple[str, str]] = []
    lines = QueuedMessages(
        current_harness=lambda: harness,
        commit_echo=lambda kind, text: echoes.append((kind, text)),
        show=lambda _rows, _spacer, _hint: None,
        width=lambda: 80,
    )
    message = SimpleNamespace(role="user", content=[SimpleNamespace(type="text", text="EXPANDED")])
    queued.append(message)
    line = lines.taken(harness, "steer", "EXPANDED", [], shown="/review auth.py")
    assert line is not None and line.text == "/review auth.py"
    assert any("/review auth.py" in str(r) for r in lines.rows())
    assert lines.delivered(message) and echoes == [("steer", "/review auth.py")]


@pytest.mark.parametrize(("key", "label"), [("\r", "Steering"), ("\x1b\r", "Follow-up")])
async def test_a_template_mid_turn_shows_the_typed_line_through_run_tui(
    key: str, label: str
) -> None:
    """Verify F2 — the test above drives ``QueuedMessages.taken`` alone. Through
    ``run_tui`` and a real harness: the pending row and the echo say
    ``/review auth.py`` and the model gets the expansion; with ``shown=`` dropped
    from ``_enqueue``'s call both showed the expansion."""

    from aelix_agent_core.harness.prompt_templates import PromptTemplate

    from tests.tui.test_queued_echo_at_handoff_668 import _Provider, _Tui

    provider = _Provider(
        [("text", "Working on it."), ("gate", "mid"), ("text", " Still working.")],
        [("text", "reply 2")],
    )
    async with _Tui(provider) as tui:
        tui.harness.set_prompt_templates([PromptTemplate(name="review", content="Review $1 now.")])
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        await _type(tui.chrome, tui.pipe, "/review auth.py", key=key)
        await _wait(lambda: f"{label}: /review auth.py" in tui.pending())
        assert "Review auth.py now." not in tui.pending()
        assert not any("/review" in c for c in tui.commits)  # queued, not yet delivered
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 2" in c for c in tui.commits))
    assert tui.index(f"{label}: /review auth.py") < tui.index("reply 2")
    assert not any("Review auth.py now." in c for c in tui.commits)
    assert provider.seen[1] == ["T1 go", "Review auth.py now."]
    assert tui.pending() == ""


class _SlowSetter(_TurnHarness):
    """A ``/thinking`` whose setter is still awaiting when the answer ends."""

    def __init__(self) -> None:
        super().__init__()
        self.setter_cancelled = False
        self.setter_entered = asyncio.Event()

    async def set_thinking_level(self, level: str) -> None:
        self.setter_entered.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.setter_cancelled = True
            raise


async def test_a_mid_turn_command_still_open_ends_with_the_tui() -> None:
    """Codex 701-handler-survives-quit — it was neither joined nor cancelled."""

    harness = _SlowSetter()
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/thinking high")
        await asyncio.wait_for(harness.setter_entered.wait(), 5)
        harness.release.set()
        await _wait(lambda: not chrome.running)
    # ``_tui`` sent /quit and waited for run_tui to return.
    assert harness.setter_cancelled


async def test_the_host_asks_through_run_tui() -> None:
    """V6 / Codex 701-tests — through ``run_tui``, not an injected callback: the
    host's ``on_mid_turn_command`` is what keeps ``/thinking high`` off the model."""

    harness = _TurnHarness()
    async with _tui(harness) as (chrome, pipe, _commits):
        await _wait(lambda: chrome.on_mid_turn_command is not None)
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/thinking high")
        await _wait(lambda: harness.levels)
        await _wait(lambda: chrome.buffer.text == "")
    assert harness.steered == [] and harness.levels == ["high"]


# --- the output pump holds a mid-turn command's lines under the answer -------------


class _PumpChrome:
    def __init__(self) -> None:
        self.printed: list[str] = []
        self.widgets: list[Any] = []

    async def print_above_many(self, renderables: Any, *, apply_before_redraw: Any = None) -> None:
        self.printed.extend(str(r) for r in renderables)
        if apply_before_redraw is not None:
            apply_before_redraw()

    def set_widget(self, key: str, lines: Any, *, above: bool = True) -> None:
        self.widgets.append((key, lines))

    def set_pending_rows(self, *_a: Any, **_k: Any) -> None:
        return None


async def test_the_pump_holds_after_stream_while_the_window_is_up() -> None:
    """V6 — the mutant ``(held if stream_live else pending)`` -> ``pending`` printed
    the command's line above the answer still being written."""

    from aelix_coding_agent.tui.shell import _output_pump

    chrome = _PumpChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    pump = asyncio.ensure_future(_output_pump(queue, chrome))  # type: ignore[arg-type]

    async def drained() -> None:
        for _ in range(20):
            await asyncio.sleep(0)

    try:
        queue.put_nowait(("tail", "the answer so far"))
        await drained()
        queue.put_nowait(("after_stream", "thinking → high"))
        await drained()
        assert chrome.printed == []  # held: the window is up
        queue.put_nowait(("commit", "the answer, whole"))
        queue.put_nowait(("tail", ""))
        await drained()
        assert chrome.printed == ["the answer, whole", "thinking → high"]
        # With no window up it goes straight through; ``release`` lets out the rest.
        queue.put_nowait(("after_stream", "idle line"))
        await drained()
        queue.put_nowait(("tail", "next message"))
        queue.put_nowait(("after_stream", "held again"))
        await drained()
        queue.put_nowait(("release", None))
        await drained()
        assert chrome.printed[2:] == ["idle line", "held again"]
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump


# --- a panel's own close leaves a dialog over it in place ---------------------------


async def test_a_panel_save_under_a_turns_dialog_leaves_the_dialog_up(tmp_path: Any) -> None:
    """Verify V3 / Codex 701-approval-lost — the panel's argumentless
    ``unmount_modal()`` took the approval on top down, its future left pending."""

    from aelix_coding_agent.tui.overlay import show_modal
    from aelix_coding_agent.tui.selection_panel import run_selection_panel

    from tests.tui.test_selection_panel_lifecycle_511 import _setup

    selection, kind, _storage = _setup(tmp_path, "{}")
    gate = asyncio.Event()
    real_save = selection.save

    async def slow_save(*a: Any, **k: Any) -> Any:
        await gate.wait()
        return await real_save(*a, **k)

    selection.save = slow_save  # type: ignore[method-assign]
    async with _chrome() as (chrome, pipe):
        ctx = SimpleNamespace(chrome=chrome, commit=lambda _line: None)
        panel = asyncio.ensure_future(run_selection_panel(ctx, selection, kind, title="Skills"))
        await _wait(lambda: chrome.is_modal_open())
        panel_slot = chrome._modal
        pipe.send_text(" ")  # toggle
        await asyncio.sleep(0.1)
        pipe.send_text("\r")  # save: waits on the gate
        await _wait(
            lambda: (
                "Saving"
                in str(
                    getattr(chrome._modal_focus.content, "model", None)
                    and chrome._modal_focus.content.model.message
                )
            )
        )
        futures: dict[str, asyncio.Future[Any]] = {}

        def build(result: asyncio.Future[Any]) -> Window:
            futures["dialog"] = result
            return _modal("APPROVE? 1 yes 2 no")

        dialog = asyncio.ensure_future(show_modal(chrome, build))
        await _wait(lambda: "dialog" in futures)
        assert chrome._modal is not panel_slot
        gate.set()
        await asyncio.wait_for(panel, 5)
        # The panel is gone; the dialog is on screen, focused, and still unanswered.
        assert chrome.is_modal_open() and chrome._modal is not panel_slot
        assert not futures["dialog"].done()
        assert not chrome.app.layout.has_focus(chrome.buffer)
        assert all(e[0] is not panel_slot for e in chrome._modal_stack)
        futures["dialog"].set_result("1")
        assert await asyncio.wait_for(dialog, 5) == "1"
        assert not chrome.is_modal_open()


async def test_unmount_by_the_focused_window_leaves_the_modal_on_top() -> None:
    """The shared mechanism both panel hosts use: a close names its own window."""

    async with _chrome() as (chrome, _pipe):
        inner, wrapper, dialog = _modal("PANEL"), _modal("WRAP"), _modal("DIALOG")
        chrome.mount_modal(wrapper, focus=inner)
        chrome.mount_modal(dialog, focus=dialog)
        chrome.unmount_modal(inner)  # the covered panel's close
        assert chrome._modal is dialog and chrome._modal_stack == []
        chrome.unmount_modal(wrapper)  # show_modal's cleanup after it: nothing
        assert chrome._modal is dialog
        chrome.unmount_modal(dialog)
        assert not chrome.is_modal_open()
        chrome.mount_modal(wrapper, focus=inner)
        chrome.unmount_modal(inner)  # on top: it leaves by its window too
        assert not chrome.is_modal_open()


class _TopCoveredChrome:
    """A recording chrome with a dialog on top of whatever the host mounted."""

    scrollback_columns = 80
    buffer = None

    def __init__(self) -> None:
        self.unmounted: list[Any] = []
        self.focused_input = 0
        self.content: Any = None
        size = SimpleNamespace(rows=24, columns=80)
        self.app = SimpleNamespace(output=SimpleNamespace(get_size=lambda: size))

    def mount_modal(self, _content: Any, *, focus: Any = None) -> None:
        return None

    def unmount_modal(self, content: Any = None) -> None:
        self.unmounted.append(content)

    def is_modal_open(self) -> bool:
        return True  # the turn's dialog

    def focus(self, target: Any) -> None:
        self.content = target

    def focus_input(self) -> None:
        self.focused_input += 1

    def invalidate(self) -> None:
        return None

    def set_footer_line(self, *_a: Any, **_k: Any) -> None:
        return None

    async def print_above_many(self, renderables: Any, *, apply_before_redraw: Any = None) -> None:
        if apply_before_redraw is not None:
            apply_before_redraw()


async def test_the_tabbed_host_closes_by_its_own_window(tmp_path: Any) -> None:
    """Verify V3's sibling (``tui/context.py``): the tabbed panel host had the same
    argumentless ``unmount_modal()`` and ``focus_input()``."""

    from aelix_coding_agent.tui.context import AelixTUIContext
    from aelix_coding_agent.tui.footer_data import AelixFooterData
    from aelix_coding_agent.tui.selection_panel import SelectionPanelBody

    from tests.tui.test_selection_panel_lifecycle_511 import _setup

    selection, kind, _storage = _setup(tmp_path, "{}")
    chrome = _TopCoveredChrome()
    ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))  # type: ignore[arg-type]
    body = SelectionPanelBody(selection, kind, title="Skills")
    task = asyncio.ensure_future(
        ctx.tabbed("X", [("Skills", lambda: []), ("Info", lambda: ["i"])], interactive={0: body})
    )
    await _wait(lambda: chrome.content is not None)
    window = chrome.content
    bindings = window.content.get_key_bindings()
    from prompt_toolkit.key_binding.key_bindings import _parse_key

    [esc] = [b.handler for b in bindings.bindings if b.keys == (_parse_key("escape"),)]
    esc(SimpleNamespace(data="\x1b"))
    await asyncio.wait_for(task, 5)
    assert chrome.unmounted and chrome.unmounted[0] is window
    assert None not in chrome.unmounted
    assert chrome.focused_input == 0  # the dialog on top keeps the keys


# --- fix round 2: an extension's panel opens mid-turn --------------------------------


class _PanelHarness(_TurnHarness):
    """A turn that runs until released, with an extension's management modal, ``/deploy``."""

    def __init__(self) -> None:
        super().__init__()
        from tests.tui.test_run_tui_smoke import _BusExtRuntime

        self.runtime = _BusExtRuntime()


async def test_an_extensions_panel_opens_mid_turn_under_a_dialog_and_closes_by_its_keys() -> None:
    """Verify F4 — it was refused with "it opens an extension's panel". It opens
    as the ``/skill`` and ``/tool`` panels do (ADR-0292 D2, D7): a dialog the turn
    opens goes over it and hands it back, and its Esc closes it, not the turn."""

    from aelix_coding_agent.tui.overlay import show_modal

    harness = _PanelHarness()
    async with _tui(harness) as (chrome, pipe, _commits):
        pipe.send_text("go\n")
        await asyncio.wait_for(harness.started.wait(), 5)
        await _type(chrome, pipe, "/deploy")
        await _wait(lambda: chrome.is_modal_open())
        await _wait(lambda: chrome.buffer.text == "")
        assert "can't run" not in chrome._render_status()
        panel = chrome._modal
        panel_window = chrome.app.layout.current_window
        assert not chrome.app.layout.has_focus(chrome.buffer)
        # The turn asks to run a tool: its dialog goes over the panel.
        futures: dict[str, asyncio.Future[Any]] = {}

        def build(result: asyncio.Future[Any]) -> Window:
            futures["dialog"] = result
            return _modal("APPROVE? 1 yes 2 no")

        dialog = asyncio.ensure_future(show_modal(chrome, build))
        await _wait(lambda: "dialog" in futures and chrome._modal is not panel)
        futures["dialog"].set_result("1")
        assert await asyncio.wait_for(dialog, 5) == "1"
        # The panel is back on top, with the keys; its Esc closes it.
        assert chrome._modal is panel
        await _wait(lambda: chrome.app.layout.current_window is panel_window)
        pipe.send_text("\x1b")
        await _wait(lambda: not chrome.is_modal_open())
        await _wait(lambda: chrome.app.layout.has_focus(chrome.buffer))
        assert chrome.running and harness.aborts == 0  # the turn goes on
        harness.release.set()
        await _wait(lambda: not chrome.running)
    assert harness.steered == [] and harness.followed == []
    assert harness.prompts == [("go", "interactive")]
