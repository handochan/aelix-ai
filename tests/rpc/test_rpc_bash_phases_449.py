"""#449 — the hang guard around the RPC bash cases reports where a slow call spent its time.

``tests/rpc/_bash_phases.py`` replaced a bare ``wait_for(..., 5.0)`` whose only
output, the one time it fired on windows-latest, was a ``TimeoutError`` inside
``asyncio.to_thread``. These cases pin what replaced it: the slowest gap is the
one named, a slow call is reported with the phase the stall was in, a fast one
is not reported, an expired guard fails with the timeline instead of a bare
``TimeoutError``, a ``TimeoutError`` the call raises itself is not reported as
an expired guard, an aborted call's kill is named as the kill, not as the
drain, a slow shell start before an abort is reported too (review round 2), and
a stall inside the kill ladder is named as the ladder, not as the EOF or the
exit the kill released (review round 3). The stalls are injected
(``BashPhases.delays``, or a slowed ``_end_the_tree``) — a real one cannot be
ordered up.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
import warnings
from typing import Any

import aelix_coding_agent.tools.bash as bash_mod
import pytest
from aelix_coding_agent.tools._abort import AbortSignal
from aelix_coding_agent.tools.bash import create_local_bash_operations

from tests.rpc._bash_phases import ABORT_ENDS_CALL_SECONDS, BashPhases

_S = 1_000_000_000  # ns


@pytest.fixture
def phases(monkeypatch: pytest.MonkeyPatch) -> BashPhases:
    return BashPhases().install(monkeypatch)


def _echo(tmp_path, chunks: list[bytes]):
    return create_local_bash_operations().exec("echo phased", str(tmp_path), on_data=chunks.append)


def test_the_report_names_the_slowest_gap_in_time_order() -> None:
    probe = BashPhases()
    probe.stamps = {
        "returned": 6 * _S,
        "call": 0,
        "spawn": _S // 1000,
        "spawned": 3 * _S // 1000,
        "first output": 5 * _S,
        "wait returned": 5 * _S + _S // 10,
    }

    timeline, slowest = probe.report()

    assert slowest == (
        "spawned -> first output (4.997s: "
        "the shell starting and running the command to its first output)"
    )
    assert timeline == (
        "call | +0.001s spawn | +0.002s spawned | +4.997s first output"
        " | +0.100s wait returned | +0.900s returned"
    )


async def test_a_slow_call_is_reported_with_the_phase_its_stall_was_in(
    tmp_path, phases: BashPhases
) -> None:
    # On the proc.wait WORKER, after the shell has been reaped: the shape of
    # the one observe-phase stall run 38439411854 measured (2.18 s).
    phases.delays["wait returned"] = 0.4
    chunks: list[bytes] = []

    with pytest.warns(UserWarning, match=r"^#449 echo phased .*slowest phase") as caught:
        result = await phases.guard(_echo(tmp_path, chunks), "echo phased", slow=0.2)

    assert result.exit_code == 0
    assert b"phased" in b"".join(chunks)
    (message,) = [str(w.message) for w in caught if str(w.message).startswith("#449")]
    gap = re.search(r"\+(\d+\.\d+)s wait returned", message)
    assert gap is not None, message
    # 0.4 s injected; 0.35 leaves the sleep's own granularity a margin.
    assert float(gap.group(1)) >= 0.35, message
    # Every step the call went through is in the timeline, in order.
    names = [part.split("s ", 1)[-1] for part in message.split("timeline: ", 1)[1].split(" | ")]
    for earlier, later in (
        ("call", "spawn"),
        ("spawn", "spawned"),
        ("spawned", "reader started"),
        ("reader started", "wait entered"),
        ("wait entered", "wait returned"),
        ("wait returned", "drain entered"),
        ("drain entered", "returned"),
    ):
        assert names.index(earlier) < names.index(later), message
    if sys.platform == "win32":
        # The kernel's own instants (``GetProcessTimes``) are what split the
        # shell's start from its reap there — the two phases run 38439411854
        # saw stall (5.97 s and 2.18 s). Same clock as the stamps: over 9000
        # calls creation came 0.56 ms or more after the spawn stamp.
        for earlier, later in (
            ("spawn", "child created"),
            ("child created", "first output"),
            ("child exited", "wait returned"),
        ):
            assert names.index(earlier) < names.index(later), message


async def test_a_call_inside_the_slow_line_is_not_reported(tmp_path, phases: BashPhases) -> None:
    chunks: list[bytes] = []

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await phases.guard(_echo(tmp_path, chunks), "echo phased", slow=20.0)

    assert result.exit_code == 0
    assert not [w for w in caught if str(w.message).startswith("#449")]


async def test_an_expired_guard_fails_with_the_timeline_not_a_bare_timeout(
    tmp_path, phases: BashPhases
) -> None:
    # The worker is held past the guard, so the call cannot return inside it
    # however fast the shell is.
    phases.delays["wait returned"] = 1.0
    chunks: list[bytes] = []

    with pytest.raises(AssertionError) as failed:
        await phases.guard(_echo(tmp_path, chunks), "echo phased", bound=0.3)

    message = str(failed.value)
    assert message.startswith("echo phased did not finish within its 0.3s hang guard"), message
    assert "slowest phase " in message
    assert "(guard expired)" in message
    # Stamped in the call's first step, which runs before the guard's timer can.
    assert "reader started" in message


async def test_a_timeout_the_call_raises_is_not_reported_as_an_expired_guard() -> None:
    # Review round 2: the first guard caught every ``TimeoutError`` and called a
    # product budget firing after 0.01 s "no return within the 30.0s hang
    # guard". ``tests.event_waits.within`` tells the two apart.
    async def raises() -> None:
        await asyncio.sleep(0.01)
        raise TimeoutError("the call's own budget")

    with pytest.raises(TimeoutError, match="the call's own budget"):
        await BashPhases().guard(raises(), "a call that times out itself")


async def test_an_aborted_call_names_its_kill_not_the_drain(tmp_path, phases: BashPhases) -> None:
    # Review round 2: on the abort, cancel and timeout legs ``_mark_the_kill``
    # calls ``pin_after`` on the loop thread BEFORE the drain does, and the
    # first guard stamped that as "drain entered". A stall held inside the
    # kill's mark must be named as the kill.
    phases.delays["kill marked"] = 0.4
    sig = AbortSignal()
    chunks: list[bytes] = []
    phases.stamp("call")
    task = asyncio.ensure_future(
        create_local_bash_operations().exec(
            "echo started; sleep 30", str(tmp_path), on_data=chunks.append, signal=sig
        )
    )
    await phases.shell_started()
    phases.stamp("abort")
    sig.abort()

    with pytest.warns(UserWarning, match=r"^#449 aborted .*slowest phase") as caught:
        result = await phases.guard(
            task,
            "aborted echo-then-sleep",
            slow=0.2,
            bound=ABORT_ENDS_CALL_SECONDS,
            kind="abort-to-return bound",
        )

    assert result.exit_code is None
    (message,) = [str(w.message) for w in caught if str(w.message).startswith("#449")]
    # The held 0.4 s lands between the abort and the kill's mark. Read from the
    # stamps, not as "the slowest phase" or the one gap before the mark: on
    # win32 the shell's start-up before the abort can be longer (p99 1.23 s,
    # run 38439411854), and the reader's EOF can fall inside the hold.
    assert "kill marked" in phases.stamps, message
    assert phases.stamps["kill marked"] - phases.stamps["abort"] >= 0.35 * _S, message
    names = [part.split("s ", 1)[-1] for part in message.split("timeline: ", 1)[1].split(" | ")]
    for earlier, later in (
        ("first output", "abort"),
        ("abort", "kill marked"),
        ("kill marked", "drain entered"),
        ("drain entered", "returned"),
    ):
        assert names.index(earlier) < names.index(later), message


async def test_a_slow_start_before_an_abort_is_reported(tmp_path, phases: BashPhases) -> None:
    # An aborted case spends its shell's start-up before the abort, outside the
    # abort's own bound: that wait reports a slow start too, or a stall there
    # would say nothing.
    phases.delays["first output"] = 0.4
    sig = AbortSignal()
    task = asyncio.ensure_future(
        create_local_bash_operations().exec(
            "echo started; sleep 30", str(tmp_path), on_data=lambda _b: None, signal=sig
        )
    )
    try:
        with pytest.warns(UserWarning, match=r"^#449 waiting for the shell's first output") as caught:
            await phases.shell_started(slow=0.2)
    finally:
        sig.abort()
        await phases.guard(task, "the aborted start probe", bound=ABORT_ENDS_CALL_SECONDS)
    (message,) = [str(w.message) for w in caught if str(w.message).startswith("#449")]
    assert "-> first output (" in message, message


async def test_a_stall_inside_the_kill_ladder_is_named_as_the_ladder(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review round 3: "kill marked" is stamped AFTER ``_end_the_tree``, so a
    # stall in the ladder (on win32 a ``taskkill.exe`` spawn, #449's own stall
    # class) ended at whatever the kill released first - EOF on POSIX, the
    # kernel's exit time on win32 - and was glossed as "the pipe's last writer
    # closing" or "the shell finishing and exiting". The ladder is slowed
    # BEFORE the stamps are installed, so the hold is inside them, where a
    # stalled ``hard_kill`` would be.
    real_end_the_tree = bash_mod._end_the_tree

    def slow_end_the_tree(*args: Any, **kwargs: Any) -> None:
        time.sleep(0.4)
        real_end_the_tree(*args, **kwargs)

    monkeypatch.setattr(bash_mod, "_end_the_tree", slow_end_the_tree)
    phases = BashPhases().install(monkeypatch)
    sig = AbortSignal()
    phases.stamp("call")
    task = asyncio.ensure_future(
        create_local_bash_operations().exec(
            "echo started; sleep 30", str(tmp_path), on_data=lambda _b: None, signal=sig
        )
    )
    await phases.shell_started()
    phases.stamp("abort")
    sig.abort()

    with pytest.warns(UserWarning, match=r"^#449 aborted .*slowest phase") as caught:
        result = await phases.guard(
            task,
            "aborted echo-then-sleep",
            slow=0.2,
            bound=ABORT_ENDS_CALL_SECONDS,
            kind="abort-to-return bound",
        )

    assert result.exit_code is None
    (message,) = [str(w.message) for w in caught if str(w.message).startswith("#449")]
    stamps = phases.stamps
    assert stamps["abort"] <= stamps["kill started"] <= stamps["kill ended"], message
    assert stamps["kill ended"] - stamps["kill started"] >= 0.35 * _S, message
    assert stamps["kill ended"] <= stamps["kill marked"], message
    # Every gap between the ladder's two stamps - whichever stamp the kill
    # released ends it - is named as the ladder, and the held 0.4 s is in them.
    inside: list[tuple[str, str, int, str]] = []
    for gap in phases.gaps():
        if gap[0] == "kill started" or (inside and inside[-1][1] != "kill ended"):
            inside.append(gap)
    assert inside, message
    assert all(meaning.startswith("inside the kill ladder") for *_, meaning in inside), inside
    longest = max(inside, key=lambda gap: gap[2])
    assert longest[2] >= 0.35 * _S, inside
    # And the ladder ends at its own stamp: the kill's mark, the drain and the
    # return after it keep their own glosses.
    after = [gap for gap in phases.gaps() if gap[1] in ("kill marked", "drain entered", "returned")]
    assert len(after) == 3, after
    assert not [gap for gap in after if gap[3].startswith("inside the kill ladder")], after
    assert "kill started" in message and "kill ended" in message, message
