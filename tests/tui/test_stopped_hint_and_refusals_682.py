"""#682 — while aelix is stopped, the shell hint and a refused ``!`` say what happens.

#663 (ADR-0289) stops aelix when a rebuild and the restore after it both fail:
every ``!`` line is refused. Three things still read as if it ran:

- the status row under a ``!`` line said "Enter runs it · the model sees the
  output" (``shell_hint`` knew nothing of the stop);
- a ``!`` refused while a command ran (``/reload``) waited in the pending area
  behind a ``!`` record held for the stop, with nothing of its own to record, and
  reached the transcript only after the ``/reload`` that ended the stop, under
  "aelix is running again";
- Enter on an idle ``!`` line printed the refusal alone: the command was nowhere.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_agent_core.runtime import STOPPED_MESSAGE
from aelix_coding_agent.tui.input_marks import shell_hint
from rich.text import Text
from test_run_tui_smoke import _harness_chrome, _launch, _quit_within, _spy_commits, _wait
from test_shell_mid_turn_596 import _Bash, _HoldingHarness
from test_stopped_shell_lane_663 import _bash_records, _RealLane, _stop, _stop_fakes, _works

from tests.runtime.test_failed_rebuild_663 import _Rig

# === the hint ===================================================================


@pytest.mark.parametrize("sigil", ["!", "!!"])
@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("bare", [False, True])
def test_while_stopped_the_hint_says_enter_is_refused(
    sigil: str, running: bool, bare: bool
) -> None:
    hint = shell_hint(sigil, running=running, width=200, bare=bare, stopped=True)
    assert hint.startswith(f"{sigil} shell · aelix is stopped · Enter is refused until /reload")
    assert "runs it" not in hint and "model" not in hint
    assert ("Esc to clear" in hint) is not running  # Esc interrupts a turn


@pytest.mark.parametrize("width", [50, 80])
def test_the_stopped_hint_fits_and_keeps_the_stop(width: int) -> None:
    hint = shell_hint("!", running=False, width=width, stopped=True)
    assert len(hint) <= width
    assert hint.startswith("! shell · aelix is stopped")


@pytest.mark.parametrize("sigil", ["!", "!!"])
@pytest.mark.parametrize("running", [False, True])
def test_a_narrow_stopped_hint_drops_esc_and_the_mode_before_the_stop(
    sigil: str, running: bool
) -> None:
    # Codex r1 (low): at 24 columns the hint read "! shell · Esc to clear" —
    # the stop clause went first because "! shell · aelix is stopped" did not
    # fit and the shorter Esc clause did. Whatever goes, the stop stays.
    for width in range(16, 120):
        hint = shell_hint(sigil, running=running, width=width, stopped=True)
        assert "aelix is stopped" in hint, (width, hint)
        assert len(hint) <= width
    mode = f"{sigil} shell · aelix is stopped"
    assert shell_hint(sigil, running=running, width=len(mode), stopped=True) == mode
    assert shell_hint(sigil, running=running, width=len(mode) - 1, stopped=True) == (
        "aelix is stopped"
    )
    # Narrower still, the stop is what is left of the row.
    assert shell_hint(sigil, running=running, width=10, stopped=True) == "aelix is s"


def test_the_stop_outranks_a_command_still_running() -> None:
    # A ``!`` started before the stop may still run: Enter is refused either way,
    # and the stop is what the user has to act on.
    hint = shell_hint("!", running=True, width=200, shell_busy=True, stopped=True)
    assert "aelix is stopped" in hint and "already running" not in hint


def test_not_stopped_the_hint_is_as_before() -> None:
    assert shell_hint("!", running=False, width=200) == (
        "! shell · Enter runs it · the model sees the output · Esc to clear"
    )


# === the lane ===================================================================


async def test_a_refused_block_does_not_wait_behind_a_record_held_for_the_stop(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await _Rig.create(tmp_path)
    t = _RealLane(rig, monkeypatch)
    await t.held("echo HELD")
    t.turn["on"] = True
    # A ``!!`` that ran in the same turn: nothing to record either.
    await t.lane.run(t.lane.begin("!!", "echo QUIET", pending=True), cwd=".")
    t.turn["on"] = False
    await _stop(rig)
    # A ``!`` Entered while the failing /reload (a command) ran: refused in its block.
    t.lane.command_running = True
    await t.lane.run(t.lane.begin("!", "echo DURING", pending=True), cwd=".")
    assert "! echo DURING" in t.shown
    t.lane.command_running = False

    await t.lane.settle(from_loop=True)  # the input loop, once the /reload failed
    assert [c.split("\n")[0] for c in t.commits] == [
        "!! echo QUIET  (the model won't see it)",
        "! echo DURING",
    ]
    assert f"✖ {STOPPED_MESSAGE}" in t.commits[1]
    # The held record still waits, alone, and says so.
    assert t.shown == "! echo HELD  (held until aelix runs again)\necho HELD output"
    assert await _bash_records(rig.session) == []

    rig.fail = _works
    await rig.runtime.reload()
    await t.lane.settle(from_loop=True)
    assert "! echo HELD  (the model sees the output next turn)" in t.commits[2]
    [record] = await _bash_records(rig.session)
    assert "echo HELD" in record
    assert t.rows[-1] == []


# === run_tui end to end ===========================================================


def _plain_rows(commits: list[str], needle: str) -> list[str]:
    return [" ".join(c.split()) for c in commits if needle in c]


async def test_an_idle_bang_refused_while_stopped_shows_its_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _HoldingHarness()
    harness.release.set()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        commits = _spy_commits(chrome)
        _stop_fakes(runtime, harness)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("!echo IDLE_X\n")
        await _wait(lambda: bool(_plain_rows(commits, "IDLE_X")), what="the refused block")
        pipe.send_text("!!echo IDLE_Q\n")
        await _wait(lambda: bool(_plain_rows(commits, "IDLE_Q")), what="the refused !! block")
        pipe.send_text("!\n")  # a bare sigil: the sentence alone, as before
        await _wait(
            lambda: [" ".join(c.split()) for c in commits].count(f"✖ {STOPPED_MESSAGE}") == 1,
            what="the bare sigil's refusal",
        )
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert bash.ran == [] and bash.recorded == []
    assert _plain_rows(commits, "IDLE_X") == [f"! echo IDLE_X ✖ {STOPPED_MESSAGE}"]
    assert _plain_rows(commits, "IDLE_Q") == [f"!! echo IDLE_Q ✖ {STOPPED_MESSAGE}"]


async def test_the_status_row_under_a_bang_line_follows_the_stop() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("!echo X")
        await _wait(lambda: chrome.buffer.text == "!echo X", what="typing the line")
        assert "Enter runs it" in Text.from_ansi(chrome._render_status()).plain
        runtime.stopped = STOPPED_MESSAGE  # type: ignore[attr-defined]
        status = Text.from_ansi(chrome._render_status()).plain
        assert "aelix is stopped" in status
        assert "Enter runs it" not in status and "the model sees" not in status
        runtime.stopped = None  # type: ignore[attr-defined]
        assert "Enter runs it" in Text.from_ansi(chrome._render_status()).plain
        chrome.buffer.reset()
        pipe.send_text("/quit\n")
        await _quit_within(task)
