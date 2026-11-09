"""A restart reports and never kills (#722, owner decision 4; Codex r1 #2-#4, r3 #2, #5).

Fails on main ``a385c64a``: no reconciliation. Naive (round 1's): a ``start``
without an ``outcome`` whose recorded pid is alive is killed and marked
``lost``; a session file is authority, so a forged ``process`` record pointing
at a stranger's pid gets that stranger signalled; a free child lock reads as
"the child is gone".
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.utils._process_identity import process_started
from aelix_coding_agent.jobs import evidence
from aelix_coding_agent.jobs.records import append_fact, fold, make_fact

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, reopen

pytestmark = pytest.mark.usefixtures("jobs_active")

_REAL_KILL = os.kill


async def _end(child: Any) -> None:
    """The test's own cleanup, past the ``no_signals`` trap."""

    if child.returncode is None:
        _REAL_KILL(child.pid, 9 if sys.platform != "win32" else 15)
    await child.wait()


def _start(job_id: str, **extra: Any) -> dict[str, Any]:
    return make_fact(
        "start",
        job_id,
        kind=_sleeper.KIND,
        kind_version=1,
        owner="s",
        mode="background",
        notify=True,
        title="nap",
        created=1.0,
        settings_key="sleeper",
        **extra,
    )


def _process(
    job_id: str, pid: int, started: float | None, host: str | None = None
) -> dict[str, Any]:
    return make_fact(
        "process",
        job_id,
        seq=1,
        evidence={"pid": pid, "started": started, "host": host or evidence.this_host()},
    )


@pytest.fixture
def no_signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    sent: list[tuple[str, Any]] = []

    def record(name: str) -> Any:
        def fake(*args: Any) -> None:
            if len(args) > 1 and args[1] == 0:
                return real[name](*args)
            sent.append((name, args))
            raise AssertionError(f"{name}{args} at restart")

        return fake

    real = {"kill": os.kill}
    monkeypatch.setattr(os, "kill", record("kill"))
    if sys.platform != "win32":
        real["killpg"] = os.killpg
        monkeypatch.setattr(os, "killpg", record("killpg"))
    return sent


async def _restart(tmp_path: Path, *facts: dict[str, Any], **adapter: Any) -> tuple[Any, Any]:
    session = await make_session(tmp_path)
    for fact in facts:
        await append_fact(session, fact)
    service = fast_service()
    _sleeper.register(service, **adapter)
    harness = make_harness(await reopen(tmp_path))
    await service.bind(harness)
    return service, harness


async def test_live_match_is_unresolved_not_lost(tmp_path: Path, no_signals: list[Any]) -> None:
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    try:
        service, harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
        snap = service.snapshot("j")
        assert snap is not None and snap.state == "unresolved"
        assert "still running" in (snap.unresolved_reason or "")
        assert child.returncode is None
        # Codex #11: an obligation — the model is told (a restored notice: no
        # TUI turn), and the slot is held while the old process runs.
        (notice,) = harness.undelivered_notices()
        assert notice.notice_id == "j:unresolved"
        # Rechecked while its process may live, and it says so (#611 S1c).
        assert "it keeps checking" in notice.body and snap.rechecking
        assert service.admission.count(_sleeper.KIND) == 1
        await service.tick()
        assert service.snapshot("j").state == "unresolved", "still running: still unresolved"  # type: ignore[union-attr]
        assert no_signals == []
    finally:
        await _end(child)
    # The process it ran is gone now: the liveness loop's report-only recheck
    # makes the job lost and gives its slot back (verify N5).
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "lost"
    assert service.admission.count(_sleeper.KIND) == 0
    assert [n.notice_id for n in harness.undelivered_notices()] == ["j:unresolved", "j:final"]
    assert no_signals == []


async def test_free_child_lock_with_live_child_is_unresolved(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    """A writer lock nobody holds says nothing about the processes: evidence decides."""

    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    lock = tmp_path / "child.jsonl.lock"
    lock.write_text("")  # present and free
    try:
        service, _ = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
        snap = service.snapshot("j")
        assert snap is not None and snap.state == "unresolved"
        assert no_signals == []
    finally:
        await _end(child)


async def test_forged_process_record_never_signals(tmp_path: Path, no_signals: list[Any]) -> None:
    """A record naming this test's own pid (a stranger to the job) with a wrong start time."""

    service, _ = await _restart(tmp_path, _start("j"), _process("j", os.getpid(), 12345.0))
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "lost", "the number belongs to another process now"
    assert no_signals == []


async def test_gone_process_is_lost_and_the_model_is_told(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    started = process_started(child.pid)
    await child.wait()
    service, harness = await _restart(tmp_path, _start("j"), _process("j", child.pid, started))
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "lost"
    assert harness.pending_notices == 1
    (notice,) = harness.undelivered_notices()
    assert "was lost" in notice.body
    entries = await harness.session.get_entries()  # type: ignore[union-attr]
    assert fold(entries)["j"].outcome["status"] == "lost"  # type: ignore[index]
    assert no_signals == []


async def test_restart_never_signals_anything(tmp_path: Path, no_signals: list[Any]) -> None:
    """Every classification path at once: live, gone, unreadable, other host, no evidence."""

    live = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
    try:
        await _restart(
            tmp_path,
            _start("a"),
            _process("a", live.pid, process_started(live.pid)),
            _start("b"),
            _process("b", 999_999_9, 1.0),
            _start("c"),
            _process("c", live.pid, None),
            _start("d"),
            _process("d", live.pid, 1.0, host="elsewhere"),
            _start("e"),
        )
        assert live.returncode is None
        assert no_signals == []
    finally:
        await _end(live)


async def test_a_job_with_no_process_is_judged_by_the_aelix_that_ran_it(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    gone = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    stamp = process_started(gone.pid)
    await gone.wait()
    here = {
        "pid": os.getpid(),
        "started": process_started(os.getpid()),
        "host": evidence.this_host(),
    }
    service, _ = await _restart(
        tmp_path,
        _start(
            "dead", supervisor={"pid": gone.pid, "started": stamp, "host": evidence.this_host()}
        ),
        _start("alive", supervisor=here),
    )
    assert service.snapshot("dead").state == "lost"  # type: ignore[union-attr]
    alive = service.snapshot("alive")
    assert alive is not None and alive.state == "unresolved"
    assert "still running" in (alive.unresolved_reason or "")


async def test_child_with_unsettled_jobs_reports_unverified_descendants(tmp_path: Path) -> None:
    """A job that ended without a ``cleanup`` fact, or with ``unverified``, says so in its notice."""

    session = await make_session(tmp_path)
    service = fast_service()
    _sleeper.register(service)

    class Unverified(_sleeper.Sleeper):
        async def helper_cleanup(self, receipt: Any) -> Any:
            from aelix_coding_agent.jobs.contract import HelperCleanup

            return HelperCleanup("unverified", "a helper left its group")

    service.register_kind(_sleeper.SleeperAdapter(), Unverified)
    harness = make_harness(session)
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    while service.snapshot(job_id).state != "done":  # type: ignore[union-attr]
        await asyncio.sleep(0.02)
    (notice,) = harness.undelivered_notices()
    assert "not confirmed stopped: a helper left its group" in notice.body
    job = fold(await session.get_entries())[job_id]
    assert job.cleanup is not None and job.cleanup["helpers"] == "unverified"
    await service.shutdown()


async def test_an_undelivered_outcome_is_posted_once_and_a_delivered_one_never(
    tmp_path: Path,
) -> None:
    session = await make_session(tmp_path)
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(session)
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    while harness.pending_notices == 0:
        await asyncio.sleep(0.02)
    await service.shutdown()

    second = fast_service()
    _sleeper.register(second)
    h2 = make_harness(await reopen(tmp_path))
    await second.bind(h2)
    assert h2.pending_notices == 1, "outcome on disk, delivery not: posted"
    assert second.restored_waiting() == 1
    await h2.prompt_notices()
    assert second.restored_waiting() == 0

    third = fast_service()
    _sleeper.register(third)
    h3 = make_harness(await reopen(tmp_path))
    await third.bind(h3)
    assert h3.pending_notices == 0, "delivered: nothing posted"
    assert third.snapshot(job_id).state == "done"  # type: ignore[union-attr]


async def test_a_restored_result_is_read_through_its_own_records_after_two_restarts(
    tmp_path: Path,
) -> None:
    """Codex #12: ``decode`` gets the outcome fact AND the kind's own records, so
    ``/jobs result`` after a restart shows what the run left there.

    Fails on ``a80e81fa``: ``decode`` got the generic fact alone and the result
    read ``MISSING-CLIENT-RECORD`` (here: an empty output).
    """

    session = await make_session(tmp_path)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(make_harness(session))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    await service._jobs[job_id].task  # type: ignore[misc]
    live = service.output(job_id)
    await service.shutdown()
    for _restart_n in range(2):
        again = fast_service()
        _sleeper.register(again)
        await again.bind(make_harness(await reopen(tmp_path)))
        out = again.output(job_id)
        assert not isinstance(out, str) and "slept 0.01" in out.text
        assert not isinstance(live, str) and out.text == live.text
        await again.shutdown()


async def test_a_restored_lost_job_without_a_cleanup_fact_says_its_helpers_were_not_confirmed(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    """Contract §2.3 (Codex #21): a job that never ended has no ``cleanup`` fact, so
    its restored notice says what it started outside its tree was not confirmed stopped."""

    service, harness = await _restart(
        tmp_path, _start("j"), _process("j", 2**22 + 12345, 1.0)
    )
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "lost"
    (notice,) = harness.undelivered_notices()
    assert "were not confirmed stopped" in notice.body
    assert no_signals == []


async def test_a_restored_live_job_counts_against_its_kinds_bound(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    """Codex #11: under a bound of one, a process still running from the last
    session holds the slot; a new job of the kind is refused until it is settled.

    Fails on ``a80e81fa``: ``reserved_before_new=0`` and the new job was admitted.
    """

    from aelix_coding_agent.jobs.service import JobStartRefused

    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
    try:
        service, _harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid)), bound=1
        )
        with pytest.raises(JobStartRefused, match="backgroundJobs.limits.sleeper"):
            await service.start(_sleeper.KIND, title="new")
        assert no_signals == []
    finally:
        await _end(child)
    await service.tick()
    assert service.snapshot("j").state == "lost"  # type: ignore[union-attr]
    assert service.admission.count(_sleeper.KIND) == 0


# === #611 S1c: what a kind can only READ of an end is ``recovered`` (ADR-0295) ======


async def _gone_pid() -> tuple[int, float | None]:
    """A process that ran here and is gone: its pid and start time."""

    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    stamp = process_started(child.pid)
    await child.wait()
    return child.pid, stamp


async def test_a_kinds_reading_is_recovered_never_done_and_waits_for_its_process(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    """Codex #4 (generic half): a kind's ``recovered`` is recorded only once every
    recorded process is confirmed gone, and it reads ``recovered`` — never ``done``.

    Fails on ``b387b4b5``: the live root's job read ``done`` at once
    (``state=done admission=0``).
    """

    from aelix_coding_agent.jobs.contract import Recovered

    reading = Recovered("recovered", "read from what it left", {"output": "left"})
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    try:
        service, harness = await _restart(
            tmp_path,
            _start("j"),
            _process("j", child.pid, process_started(child.pid)),
            recovered=reading,
        )
        snap = service.snapshot("j")
        assert snap is not None and snap.state == "unresolved"
        assert "still running" in (snap.unresolved_reason or "")
        assert service.admission.count(_sleeper.KIND) == 1, "it keeps its slot"
        await service.tick()
        assert service.snapshot("j").state == "unresolved"  # type: ignore[union-attr]
        assert no_signals == []
    finally:
        await _end(child)
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "recovered"
    assert service.admission.count(_sleeper.KIND) == 0
    entries = await (await reopen(tmp_path)).get_entries()
    outcome = fold(entries)["j"].outcome
    assert outcome is not None and outcome["status"] == "recovered"
    assert [n.notice_id for n in harness.undelivered_notices()] == ["j:unresolved", "j:final"]
    # A second restart reads the recorded outcome: still ``recovered``.
    again = fast_service()
    _sleeper.register(again)
    await again.bind(make_harness(await reopen(tmp_path)))
    assert again.snapshot("j").state == "recovered"  # type: ignore[union-attr]
    assert no_signals == []


async def test_a_kinds_own_unresolved_reason_survives_os_evidence(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    """Codex #5 (generic half): a kind's ``unresolved`` keeps its reason; a gone
    process never makes it ``lost``, and only gives its slot back. Nothing
    checks it again, and its notice does not say anything does (verify-r2 SF2).

    Fails on ``b387b4b5``: ``state=lost reason=None``; on ``fa742f77`` the
    notice said "it keeps checking".
    """

    from aelix_coding_agent.jobs.contract import Recovered

    pid, stamp = await _gone_pid()
    service, harness = await _restart(
        tmp_path,
        _start("j"),
        _process("j", pid, stamp),
        recovered=Recovered("unresolved", reason="kind_cannot_tell"),
    )
    snap = service.snapshot("j")
    assert snap is not None and (snap.state, snap.unresolved_reason) == (
        "unresolved",
        "kind_cannot_tell",
    )
    assert service.admission.count(_sleeper.KIND) == 0, "nothing of it runs: no slot"
    (notice,) = harness.undelivered_notices()
    assert "keeps checking" not in notice.body and "it is not watching this job" in notice.body
    assert snap.rechecking is False and not service._needs_liveness()
    await service.tick()
    assert service.snapshot("j").unresolved_reason == "kind_cannot_tell"  # type: ignore[union-attr]
    assert no_signals == []


async def test_a_kinds_own_unresolved_with_a_live_process_holds_its_slot_and_reason(
    tmp_path: Path, no_signals: list[Any]
) -> None:
    from aelix_coding_agent.jobs.contract import Recovered

    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    try:
        service, _harness = await _restart(
            tmp_path,
            _start("j"),
            _process("j", child.pid, process_started(child.pid)),
            recovered=Recovered("unresolved", reason="kind_cannot_tell"),
        )
        assert service.admission.count(_sleeper.KIND) == 1
        await service.tick()
        snap = service.snapshot("j")
        assert snap is not None and snap.unresolved_reason == "kind_cannot_tell"
    finally:
        await _end(child)
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and (snap.state, snap.unresolved_reason) == (
        "unresolved",
        "kind_cannot_tell",
    )
    assert service.admission.count(_sleeper.KIND) == 0
    assert no_signals == []
