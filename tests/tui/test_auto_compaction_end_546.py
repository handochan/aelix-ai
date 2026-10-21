"""#546 review round 2 - the TUI shows an automatic compaction that ended without a result.

Since #546 a threshold or overflow compaction that fails is reported on its
``compaction_end`` and not raised out of the prompt. Before #546 the TUI drew the
raise as the turn's error (``✖ Error code: 400 - ...`` after the answer); with the
raise gone and no ``compaction_end`` branch anywhere in the TUI, it drew nothing:
measured in a 140x45 pty on ``c64bec3f`` (five seed turns, then a turn that reports
50000 of a 60000 window, its compaction's summary answered 400, or a
``session_before_compact`` extension that cancels) - the answer and then nothing
(``.omc/probes/b4-a-live/r2/tui546-before-*``).

pi (``interactive-mode.ts:3694-3740`` @ fdce48eb4): an aborted automatic
compaction shows the status line ``Auto-compaction cancelled``; a failed one shows
its ``errorMessage`` in the error colour; a manual one shows its own. These rows
pin ``shell._show_compaction_end`` and, through a running ``run_tui``, the
dispatch that calls it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from _polling import wait_until
from aelix_agent_core.types import CompactionEndEvent, CompactionStartEvent
from aelix_coding_agent.tui import shell as tui_shell
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

_FAILED = "Auto-compaction failed: Error code: 400 - summary refused (probe)"
_OVERFLOW = "Context overflow recovery failed: Error code: 400 - summary refused (probe)"


def _end(**kw: object) -> CompactionEndEvent:
    base: dict[str, object] = dict(
        reason="threshold", result=None, aborted=False, will_retry=False, error_message=None
    )
    base.update(kw)
    return CompactionEndEvent(**base)  # type: ignore[arg-type]


def _shown(event: object) -> list[str]:
    committed: list[object] = []
    assert tui_shell._show_compaction_end(event, committed.append) == getattr(event, "type", None)
    return [str(line) for line in committed]


def test_a_failed_threshold_compaction_shows_its_error_line() -> None:
    assert _shown(_end(error_message=_FAILED)) == [f"✖ {_FAILED}"]


def test_a_failed_overflow_recovery_shows_its_error_line() -> None:
    assert _shown(_end(reason="overflow", will_retry=False, error_message=_OVERFLOW)) == [
        f"✖ {_OVERFLOW}"
    ]


def test_an_aborted_automatic_compaction_shows_the_cancel_status() -> None:
    assert _shown(_end(aborted=True)) == ["Auto-compaction cancelled"]
    assert _shown(_end(reason="overflow", aborted=True, will_retry=True)) == [
        "Auto-compaction cancelled"
    ]


def test_a_manual_compaction_and_nothing_to_compact_show_nothing_here() -> None:
    """``/compact`` prints its own result; "Nothing to compact" is aelix's no-op event."""

    assert _shown(_end(reason="manual", error_message="boom")) == []
    assert _shown(_end(reason="manual", aborted=True)) == []
    assert _shown(_end(error_message="Nothing to compact")) == []
    assert _shown(_end()) == []  # a compaction that succeeded


def test_other_events_pass_through_with_their_type() -> None:
    assert _shown(CompactionStartEvent(reason="threshold")) == []
    assert _shown(_FakeEvent("turn_start")) == []


def test_the_error_line_is_bounded_like_every_error_line() -> None:
    lines = _shown(_end(error_message="Auto-compaction failed: \x1b[2Jboom"))
    assert len(lines) == 1
    assert lines[0].startswith("✖ Auto-compaction failed:")
    assert "\x1b" not in lines[0]


class _FakeEvent:
    def __init__(self, etype: str) -> None:
        self.type = etype


async def test_run_tui_draws_the_failed_and_the_cancelled_compaction() -> None:
    """The dispatch: a ``compaction_end`` the harness emits reaches the transcript."""

    from test_run_tui_smoke import FakeHarness, FakeRuntime, _quit_within  # sibling

    printed: list[str] = []
    harness = FakeHarness()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        runtime = FakeRuntime(harness)
        chrome = AelixChrome()

        async def _capture(renderables: list[object], **_kw: object) -> None:
            printed.extend(str(r) for r in renderables)

        chrome.print_above_many = _capture  # type: ignore[method-assign]
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await wait_until(lambda: chrome.app.is_running, what="the TUI to start")
        await wait_until(lambda: bool(harness.subscribers), what="the renderer to subscribe")
        listener: Callable[[object], None] = harness.subscribers[0]  # type: ignore[assignment]
        listener(CompactionStartEvent(reason="threshold"))
        listener(_end(error_message=_FAILED))
        await wait_until(lambda: f"✖ {_FAILED}" in printed, what="the failure line")
        listener(CompactionStartEvent(reason="threshold"))
        listener(_end(aborted=True))
        await wait_until(lambda: "Auto-compaction cancelled" in printed, what="the cancel line")
        listener(CompactionStartEvent(reason="manual"))
        listener(_end(reason="manual", error_message="manual boom"))
        pipe.send_text("/quit\n")
        assert await _quit_within(task) == 0

    assert not any("manual boom" in line for line in printed)
    assert chrome.get_working_message() != "Compacting context…"
