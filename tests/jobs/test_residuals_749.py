"""#749 — the job service's unresolved texts say only what stays true, and a failed re-read gives its slot back.

Run with the synthetic kind only (``tests/jobs/_sleeper.py``), so what these
pin is the service's, whatever kind uses it. Each case names how it fails on
``363accf2``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.utils._process_identity import process_started
from aelix_coding_agent.jobs.contract import Recovered

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for
from tests.jobs.test_restart_611 import _end, _process, _restart, _start

pytestmark = pytest.mark.usefixtures("jobs_active")


async def _live() -> Any:
    return await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")


async def _ran(tmp_path: Path, *fail: str) -> tuple[Any, Any, str]:
    service = fast_service(cleanup_bound=1.0)
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(
        _sleeper.KIND, title="nap", params={"seconds": 0.05, "fail": list(fail)}
    )
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    return service, harness, job_id


async def test_a_settle_error_job_says_nothing_checks_it_again(tmp_path: Path) -> None:
    """A job this process ran whose runner's settle failed is never rechecked (``tick`` skips
    ``settle_error``): its notice and its snapshot say so.

    Fails on ``363accf2``: the notice says "It keeps checking; /jobs shows its
    state." (#722's text), and the snapshot has no ``processes_gone``.
    """

    service, harness, job_id = await _ran(tmp_path, "settle")
    snap = service.snapshot(job_id)
    assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", "settle_error")
    (notice,) = harness.undelivered_notices()
    assert "keeps checking" not in notice.body
    assert "it does not check this job again" in notice.body
    assert "failed to settle its run (settle_error)" in notice.body
    assert snap.rechecking is False
    assert snap.processes_gone is True, "its sleeper exited and the barrier confirmed it"
    await service.tick()
    after = service.snapshot(job_id)
    assert after is not None and (after.state, after.rechecking) == ("unresolved", False)
    assert harness.pending_notices == 1, "and nothing more is posted"
    await service.shutdown()


async def test_a_cleanup_error_job_is_rechecked_and_says_so(tmp_path: Path) -> None:
    """The other side of ``rechecking`` for a job this process runs: a failed cleanup is retried.

    A guard on ``363accf2`` for ``rechecking`` (it passes there); with the
    settle case above it pins ``!= "settle_error"`` in ``_snap`` (mutant R02
    of verify-r3, which no test read before).
    """

    service, harness, job_id = await _ran(tmp_path, "helper_cleanup")
    snap = service.snapshot(job_id)
    assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", "cleanup_error")
    assert snap.rechecking is True
    (notice,) = harness.undelivered_notices()
    assert "aelix retries the cleanup" in notice.body
    await service.shutdown()


async def test_a_kinds_own_unresolved_notice_promises_no_later_notice(tmp_path: Path) -> None:
    """A kind's own ``unresolved`` with a live process is checked only until its process is gone,
    and its kind may still not tell then: the one notice says that, and stays true after.

    Fails on ``363accf2``: the notice says "it keeps checking", and once the
    process is gone nothing checks it and no second notice comes (verify-r3
    SF-A's probe).
    """

    child = await _live()
    try:
        service, harness = await _restart(
            tmp_path,
            _start("j"),
            _process("j", child.pid, process_started(child.pid)),
            recovered=Recovered("unresolved", reason="kind_cannot_tell"),
        )
        (notice,) = harness.undelivered_notices()
        assert "keeps checking" not in notice.body
        assert (
            "it checks the job's processes until they are gone, and promises no later notice"
            in notice.body
        )
        snap = service.snapshot("j")
        assert snap is not None and snap.rechecking and not snap.processes_gone
    finally:
        await _end(child)
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", "kind_cannot_tell")
    assert (snap.rechecking, snap.processes_gone) == (False, True)
    assert [n.notice_id for n in harness.undelivered_notices()] == ["j:unresolved"], "no second notice"
    await service.shutdown()


async def test_an_os_evidence_unresolved_notice_still_says_it_keeps_checking(tmp_path: Path) -> None:
    """A guard on ``363accf2``: OS evidence's ``unresolved`` is rechecked and gets a notice once
    its process is gone, so its notice keeps its words."""

    child = await _live()
    try:
        service, harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
        (notice,) = harness.undelivered_notices()
        assert "it keeps checking, and /jobs shows its state" in notice.body
        assert "promises no later notice" not in notice.body
    finally:
        await _end(child)
    await service.shutdown()


async def test_a_failed_read_during_a_recheck_gives_the_slot_back_and_reads_again(
    tmp_path: Path,
) -> None:
    """Verify-r3 nit 4: its processes are gone and one read of its records fails.

    The slot goes back — no process of it is left — and the recheck stays on,
    so the next pass reads the records and records how it ended.

    Fails on ``363accf2``: ``rechecking False | liveness False | adm 1``, and
    the job stays ``unresolved(unreadable)`` holding its slot until aelix
    exits.
    """

    child = await _live()
    try:
        service, harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
        assert service.admission.count(_sleeper.KIND) == 1
    finally:
        await _end(child)
    session = harness.session
    real = session.get_entries
    calls = {"failed": 0}

    async def failing() -> Any:
        calls["failed"] += 1
        raise OSError("the disk went away for a moment")

    session.get_entries = failing  # type: ignore[method-assign]
    try:
        await service.tick()
    finally:
        session.get_entries = real  # type: ignore[method-assign]
    assert calls["failed"] == 1
    snap = service.snapshot("j")
    assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", "unreadable")
    assert service.admission.count(_sleeper.KIND) == 0, "no process of it is left"
    assert service._needs_liveness(), "read again on the next pass"
    assert (snap.rechecking, snap.processes_gone) == (True, True)
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "lost"
    assert service.admission.count(_sleeper.KIND) == 0
    assert [n.notice_id for n in harness.undelivered_notices()] == ["j:unresolved", "j:final"]
    await service.shutdown()


async def test_processes_gone_is_what_the_evidence_said(tmp_path: Path) -> None:
    """``JobSnapshot.processes_gone``: false while a recorded process lives or nothing was read,
    true once the evidence said gone and for an ended job.

    Fails on ``363accf2``: ``JobSnapshot`` has no ``processes_gone``.
    """

    child = await _live()
    try:
        service, _harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
        assert service.snapshot("j").processes_gone is False  # type: ignore[union-attr]
    finally:
        await _end(child)
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and (snap.state, snap.processes_gone) == ("lost", True)
    await service.shutdown()

    # Never checked against the OS: an adapter that raises before the evidence is read.
    other = tmp_path / "other"
    other.mkdir()
    service, _harness = await _restart(other, _start("k"), fail=frozenset({"recover"}))
    snap = service.snapshot("k")
    assert snap is not None and (snap.unresolved_reason, snap.rechecking) == ("recover_error", False)
    assert snap.processes_gone is False
    await service.shutdown()



async def test_processes_gone_follows_the_barrier_of_a_job_this_process_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """False while its barrier could not confirm its process gone; true once a recheck did.

    Fails on ``363accf2``: ``JobSnapshot`` has no ``processes_gone``.
    """

    from aelix_coding_agent.jobs import evidence

    real = evidence.confirm_gone

    async def not_yet(*_a: Any, **_k: Any) -> tuple[bool, str]:
        return False, "pid 1 not confirmed stopped"

    monkeypatch.setattr(evidence, "confirm_gone", not_yet)
    service = fast_service(cleanup_bound=0.5)
    _sleeper.register(service)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.05})
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    snap = service.snapshot(job_id)
    assert snap is not None and (snap.rechecking, snap.processes_gone) == (True, False)
    monkeypatch.setattr(evidence, "confirm_gone", real)
    await service.tick()
    snap = service.snapshot(job_id)
    assert snap is not None and (snap.state, snap.processes_gone) == ("done", True)
    await service.shutdown()


async def test_an_ended_job_a_restart_found_has_its_processes_gone(tmp_path: Path) -> None:
    """An ``outcome`` fact is written only after its barrier passed: a restart reads it gone.

    Fails on ``363accf2``: ``JobSnapshot`` has no ``processes_gone``.
    """

    from aelix_coding_agent.jobs.records import make_fact

    outcome = make_fact("outcome", "j", status="done", brief="slept", elapsed_ms=50, client_records="ok")
    service, _harness = await _restart(tmp_path, _start("j"), outcome)
    snap = service.snapshot("j")
    assert snap is not None and (snap.state, snap.processes_gone) == ("done", True)
    await service.shutdown()


async def test_a_kinds_own_reason_survives_a_failed_re_read(tmp_path: Path) -> None:
    """The kind's own reason stands through a failed read of its records, as through OS evidence.

    Fails on ``363accf2``: the read failure made it ``unreadable`` and held its slot.
    """

    child = await _live()
    try:
        service, harness = await _restart(
            tmp_path,
            _start("j"),
            _process("j", child.pid, process_started(child.pid)),
            recovered=Recovered("unresolved", reason="kind_cannot_tell"),
        )
    finally:
        await _end(child)
    session = harness.session
    real = session.get_entries

    async def failing() -> Any:
        raise OSError("the disk went away for a moment")

    session.get_entries = failing  # type: ignore[method-assign]
    try:
        await service.tick()
    finally:
        session.get_entries = real  # type: ignore[method-assign]
    snap = service.snapshot("j")
    assert snap is not None and snap.unresolved_reason == "kind_cannot_tell"
    assert service.admission.count(_sleeper.KIND) == 0
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and (snap.unresolved_reason, snap.rechecking) == ("kind_cannot_tell", False)
    await service.shutdown()


async def test_once_its_processes_are_gone_a_failed_recover_never_takes_a_slot_back(
    tmp_path: Path,
) -> None:
    """Codex 5: a failed read gives the slot back; a ``recover`` that then raises keeps the job
    rechecked and takes no slot; a ``recover`` that works records it.

    Fails on ``5390b254``: the failed ``recover`` took a slot again and stopped
    the recheck (``RETRY_RECOVER_FAILED {"slots": 1, "rechecking": false}``),
    for the rest of the process.
    """

    child = await _live()
    try:
        service, harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
    finally:
        await _end(child)
    session = harness.session
    real = session.get_entries

    async def failing() -> Any:
        raise OSError("read failed once")

    session.get_entries = failing  # type: ignore[method-assign]
    try:
        await service.tick()
    finally:
        session.get_entries = real  # type: ignore[method-assign]
    assert service.admission.count(_sleeper.KIND) == 0
    adapter = service.registry.get(_sleeper.KIND).adapter
    original = adapter.recover

    async def recover_fails(*_a: Any) -> Any:
        raise RuntimeError("temporary recover failure")

    adapter.recover = recover_fails
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and snap.unresolved_reason == "recover_error"
    assert service.admission.count(_sleeper.KIND) == 0, "its processes are gone: no slot"
    assert (snap.rechecking, snap.processes_gone) == (True, True)
    adapter.recover = original
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and snap.state == "lost"
    assert service.admission.count(_sleeper.KIND) == 0
    await service.shutdown()


async def test_a_failed_read_warns_once_then_quietly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify F9: a read that keeps failing is tried on every pass, its WARNING logged once."""

    import logging

    child = await _live()
    try:
        service, harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
    finally:
        await _end(child)

    async def failing() -> Any:
        raise OSError("still failing")

    harness.session.get_entries = failing  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG, logger="aelix_coding_agent.jobs.service"):
        for _ in range(3):
            await service.tick()
    tries = [r for r in caplog.records if "could not read the records of job j" in r.getMessage()]
    assert [r.levelno for r in tries] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
    await service.shutdown()


async def test_once_its_processes_are_gone_a_failed_relation_is_tried_again_without_a_slot(
    tmp_path: Path,
) -> None:
    """Verify-r2 mutant S10: ``relation`` raising after the processes are gone is retried, as a
    failed ``recover`` is, with no slot. A guard on ``c06128c8`` (it passes there)."""

    child = await _live()
    try:
        service, harness = await _restart(
            tmp_path, _start("j"), _process("j", child.pid, process_started(child.pid))
        )
    finally:
        await _end(child)
    session = harness.session
    real = session.get_entries

    async def failing() -> Any:
        raise OSError("read failed once")

    session.get_entries = failing  # type: ignore[method-assign]
    try:
        await service.tick()
    finally:
        session.get_entries = real  # type: ignore[method-assign]
    adapter = service.registry.get(_sleeper.KIND).adapter
    adapter.fail = frozenset({"relation"})
    await service.tick()
    snap = service.snapshot("j")
    assert snap is not None and snap.unresolved_reason == "relation_error"
    assert (snap.rechecking, service.admission.count(_sleeper.KIND)) == (True, 0)
    adapter.fail = frozenset()
    await service.tick()
    assert service.snapshot("j").state == "lost"  # type: ignore[union-attr]
    await service.shutdown()
