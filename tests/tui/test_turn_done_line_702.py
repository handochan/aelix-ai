"""#702 — a typed turn that is over leaves one dim line: how it ended, how long, when.

``✓ done · 4.2s · 14:05`` / ``stopped · 4.2s · 14:05`` / ``4.2s · 14:05`` (a
failure, under its red ``✖`` line). The working row used to vanish and leave
nothing. The duration shapes are one module (``tui/durations.py``) that
``/stats`` uses too.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.messages import AssistantMessage
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.durations import call_duration, turn_duration, wall_duration
from aelix_coding_agent.tui.render import EventRenderer
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime, _quit_within, _spy_commits, _wait

# --- the shared duration shapes -----------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0.0, "0ms"),
        (0.44, "440ms"),
        (0.84, "840ms"),
        (0.9994, "999ms"),
        (0.9996, "1.0s"),
        (4.2, "4.2s"),
        (9.94, "9.9s"),
        (9.96, "10s"),
        (42.4, "42s"),
        (59.4, "59s"),
        (59.6, "1m 00s"),
        (72, "1m 12s"),
        (3599.6, "1h 00m"),
        (3780, "1h 03m"),
        (-3, "0ms"),
        ("bad", "0s"),
    ],
)
def test_turn_duration(seconds: Any, text: str) -> None:
    assert turn_duration(seconds) == text


def test_the_stats_shapes_moved_unchanged() -> None:
    from aelix_coding_agent.tui import stats_dashboard

    assert stats_dashboard._wall is wall_duration and stats_dashboard._dur is call_duration
    assert [wall_duration(s) for s in (42, 185, 3720, None)] == ["42s", "3m 05s", "1h 02m", "0s"]
    assert [call_duration(s) for s in (None, 0.84, 1.24, 45.4)] == ["—", "840ms", "1.2s", "45s"]


# --- the line ---------------------------------------------------------------------


def _epoch(hour: int, minute: int) -> float:
    return time.mktime((2026, 11, 6, hour, minute, 30, 0, 0, -1))


@pytest.mark.parametrize(
    ("outcome", "text"),
    [
        ("done", "✓ done · 4.2s · 14:05"),
        ("stopped", "stopped · 4.2s · 14:05"),
        ("failed", "4.2s · 14:05"),
    ],
)
def test_turn_end_line(outcome: str, text: str) -> None:
    from aelix_coding_agent.tui.shell import turn_end_line

    line = turn_end_line(outcome, 4.2, _epoch(14, 5))
    assert line.plain == text and str(line.style) == "dim"


# --- the chrome keeps how long the run took -----------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_last_run_seconds_is_the_run_from_rise_to_fall() -> None:
    clock = _Clock()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(time_fn=clock)
        assert chrome.last_run_seconds is None
        chrome.set_running(True)
        clock.t += 3.0
        # A command run mid-turn sets running again and puts it back: the
        # turn's clock goes on (it restarted before #702).
        chrome.set_running(True)
        chrome.set_running(True)
        clock.t += 1.5
        assert chrome.last_run_seconds is None  # still running
        chrome.set_running(False)
        assert chrome.last_run_seconds == pytest.approx(4.5)
        chrome.set_running(True)
        assert chrome.last_run_seconds is None


# --- the renderer says how the run ended ----------------------------------------------


def _assistant(stop: str) -> AssistantMessage:
    return AssistantMessage(content=[], stop_reason=stop)  # type: ignore[arg-type]


def _ev(kind: str, **kw: Any) -> Any:
    return SimpleNamespace(type=kind, **kw)


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([], (False, None)),
        ([_ev("agent_start"), _ev("turn_end", message=_assistant("stop"))], (True, "stop")),
        # an error, then an auto-retry that succeeded: the run ends on the success
        (
            [
                _ev("agent_start"),
                _ev("message_end", message=_assistant("error")),
                _ev("turn_end", message=_assistant("error")),
                _ev("agent_start"),
                _ev("turn_end", message=_assistant("stop")),
            ],
            (True, "stop"),
        ),
        ([_ev("agent_start"), _ev("turn_end", message=_assistant("aborted"))], (True, "aborted")),
        ([_ev("agent_start"), _ev("message_end", message=_assistant("error"))], (True, "error")),
        # Codex 702-retry-cancel: Esc in the retry's backoff — the cancel is the end
        (
            [
                _ev("agent_start"),
                _ev("message_end", message=_assistant("error")),
                _ev("auto_retry_start", attempt=1, max_attempts=3, delay_ms=2000),
                _ev("auto_retry_end", success=False, attempt=1, final_error="Retry cancelled"),
            ],
            (True, "aborted"),
        ),
        # the retries ran out: still the error
        (
            [
                _ev("agent_start"),
                _ev("message_end", message=_assistant("error")),
                _ev("auto_retry_end", success=False, attempt=3, final_error="rate limit exceeded"),
            ],
            (True, "error"),
        ),
    ],
)
def test_take_run_end(events: list[Any], expected: tuple[bool, str | None]) -> None:
    renderer = EventRenderer(commit=lambda _r: None, set_tail=lambda _a: None)
    for event in events:
        renderer.on_agent_event(event)
    assert renderer.take_run_end() == expected
    assert renderer.take_run_end() == (False, None)  # read-and-cleared


# --- through the real input loop ----------------------------------------------------


class _TurnHarness(FakeHarness):
    def __init__(self, clock: _Clock, *, stop: str | None, took: float, raises: bool = False):
        super().__init__()
        self.clock, self.stop, self.took, self.raises = clock, stop, took, raises

    def _emit(self, event: Any) -> None:
        for listener in list(self.subscribers):
            listener(event)  # type: ignore[operator]

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        self.prompts.append((text, source))
        if self.stop is not None:
            self._emit(_ev("agent_start"))
            self._emit(_ev("turn_end", message=_assistant(self.stop)))
            self._emit(_ev("agent_end"))
        self.clock.t += self.took
        if self.raises:
            raise RuntimeError("provider exploded")
        return []


@asynccontextmanager
async def _tui(harness: _TurnHarness) -> AsyncGenerator[tuple[AelixChrome, PipeInput, list[str]]]:
    from aelix_coding_agent.tui.shell import run_tui

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome(time_fn=harness.clock)
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
            pipe.send_text("/quit\n")
            with contextlib.suppress(Exception):
                await _quit_within(task)


_HHMM = r"\d\d:\d\d"


@pytest.mark.parametrize(
    ("stop", "took", "raises", "pattern"),
    [
        ("stop", 4.2, False, rf"✓ done · 4\.2s · {_HHMM}"),
        ("stop", 72.0, False, rf"✓ done · 1m 12s · {_HHMM}"),
        ("aborted", 2.0, False, rf"stopped · 2\.0s · {_HHMM}"),
        ("error", 1.0, False, rf"1\.0s · {_HHMM}"),
        (None, 0.5, True, rf"500ms · {_HHMM}"),
    ],
)
async def test_a_typed_turn_leaves_its_line_last(
    stop: str | None, took: float, raises: bool, pattern: str
) -> None:
    harness = _TurnHarness(_Clock(), stop=stop, took=took, raises=raises)
    async with _tui(harness) as (_chrome, pipe, commits):
        pipe.send_text("hello\n")
        await _wait(lambda: any(re.fullmatch(pattern, c) for c in commits))
        await asyncio.sleep(0.05)
    i = next(i for i, c in enumerate(commits) if re.fullmatch(pattern, c))
    echo = next(i for i, c in enumerate(commits) if "hello" in c)
    assert echo < i
    if raises:
        assert any("provider exploded" in c for c in commits[echo:i])


async def test_a_line_an_input_hook_handled_ran_no_turn_and_leaves_no_line() -> None:
    harness = _TurnHarness(_Clock(), stop=None, took=0.1)
    async with _tui(harness) as (_chrome, pipe, commits):
        pipe.send_text("hello\n")
        await _wait(lambda: harness.prompts)
        for _ in range(20):
            await asyncio.sleep(0)
    assert not any(re.search(rf"· {_HHMM}$", c) for c in commits)


async def test_a_command_leaves_no_line() -> None:
    harness = _TurnHarness(_Clock(), stop="stop", took=1.0)
    async with _tui(harness) as (_chrome, pipe, commits):
        pipe.send_text("/nosuchcommand\n")
        await _wait(lambda: any("Unknown command" in c for c in commits))
        for _ in range(20):
            await asyncio.sleep(0)
    assert not any(re.search(rf"· {_HHMM}$", c) for c in commits)


# --- fix round 1 ---------------------------------------------------------------------


async def test_a_hook_handled_line_after_an_extensions_turn_leaves_no_line() -> None:
    """Verify V8 / Codex 702-hook-handled: an extension's turn (#674 — its
    ``send_message(trigger_turn=True)`` runs ``prompt()`` outside the typed-turn
    wrapper) set what the renderer saw, and the next typed line, which an ``input``
    hook handled, read it as its own run: ``✓ done``."""

    harness = _TurnHarness(_Clock(), stop=None, took=0.3)
    async with _tui(harness) as (_chrome, pipe, commits):
        await _wait(lambda: harness.subscribers)
        # The extension's turn: the renderer is subscribed and sees its run.
        harness._emit(_ev("agent_start"))
        harness._emit(_ev("turn_end", message=_assistant("stop")))
        harness._emit(_ev("agent_end"))
        for _ in range(10):
            await asyncio.sleep(0)
        pipe.send_text("hello\n")  # handled by a hook: prompt() runs no turn
        await _wait(lambda: harness.prompts)
        await asyncio.sleep(0.1)
    assert not any(re.search(rf"· {_HHMM}$", c) for c in commits), commits


async def test_an_extensions_turn_alone_leaves_no_line_and_the_next_typed_turn_its_own() -> None:
    """Codex 702-send-message: the extension's turn leaves none; the typed turn after
    it says how IT ended, not how the extension's did."""

    harness = _TurnHarness(_Clock(), stop="stop", took=1.5)
    async with _tui(harness) as (_chrome, pipe, commits):
        await _wait(lambda: harness.subscribers)
        harness._emit(_ev("agent_start"))
        harness._emit(_ev("turn_end", message=_assistant("aborted")))
        harness._emit(_ev("agent_end"))
        await asyncio.sleep(0.1)
        assert not any(re.search(rf"· {_HHMM}$", c) for c in commits)
        pipe.send_text("hello\n")
        await _wait(lambda: any(re.search(rf"· {_HHMM}$", c) for c in commits))
        await asyncio.sleep(0.05)
    [line] = [c for c in commits if re.search(rf"· {_HHMM}$", c)]
    assert re.fullmatch(rf"✓ done · 1\.5s · {_HHMM}", line), line


class _RetryCancelledHarness(_TurnHarness):
    """A provider error, a retry's backoff, and Esc in it (``abort_retry``)."""

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent

        self.prompts.append((text, source))
        self._emit(_ev("agent_start"))
        self._emit(_ev("message_end", message=_assistant("error")))
        self._emit(_ev("turn_end", message=_assistant("error")))
        self._emit(_ev("agent_end"))
        self._emit(
            AutoRetryStartEvent(
                attempt=1, max_attempts=3, delay_ms=2000, error_message="rate limit exceeded"
            )
        )
        self.clock.t += self.took
        self._emit(AutoRetryEndEvent(success=False, attempt=1, final_error="Retry cancelled"))
        return []


async def test_esc_in_a_retrys_backoff_reads_stopped() -> None:
    harness = _RetryCancelledHarness(_Clock(), stop=None, took=3.1)
    async with _tui(harness) as (_chrome, pipe, commits):
        pipe.send_text("hello\n")
        await _wait(lambda: any(re.search(rf"· {_HHMM}$", c) for c in commits))
        await asyncio.sleep(0.05)
    [line] = [c for c in commits if re.search(rf"· {_HHMM}$", c)]
    assert re.fullmatch(rf"stopped · 3\.1s · {_HHMM}", line), line
