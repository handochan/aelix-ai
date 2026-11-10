"""#611 S1c — a restart's recovery of a background agent is CONSERVATIVE (contract §2.4, Codex r5 finding 5).

A delegated child's session file records no run boundaries (Codex probe 4:
``persisted_agent_start_or_end: false``), so a restart that finds a job started
and not ended:

* calls it ``done`` ONLY from the parent's own confirmed settle for that job;
* otherwise reads the child's file for its last answer — reported as
  ``recovered``, "aelix could not confirm it finished" — only when the run is
  the LATEST run of that file (no later start for the same child session
  anywhere in the parent's records) and nothing holds the file's writer lock;
* otherwise ``unresolved(cannot_bind_run)``, or nothing, and the service
  classifies from OS evidence (report-only, never a signal).

``agent(action="result")`` after a restart follows the same rules.

AT WHAT LEVEL. The parent's records are written by the product's own
``SpawnReceipt`` into a parent session inside a real bucket, the child's file
is allocated by it, and the child's transcript lines are the session's JSONL
shape — the stub children of the other files write no session.

Fails on ``6caaae09``: there is no delegation kind and no child-file reader.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import Session
from aelix_agent_core.session.session_lock import SessionWriterLock
from aelix_agents.child_session import (
    CANNOT_BIND_RUN,
    CHILD_SESSION_TYPE,
    USAGE_TYPE,
    RecoveredAnswer,
    SpawnReceipt,
    append_records,
    recover_run_answer,
)
from aelix_agents.jobs_client import (
    KIND,
    RECOVERED_CAVEAT,
    DelegationJobs,
    DelegationKindAdapter,
    result_text,
    status_text,
    stop_text,
    wait_for_agents,
)
from aelix_agents.stream import _StreamState
from aelix_ai.utils._process_identity import process_started
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs import evidence as ev
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE, append_fact, client_records_of, make_fact
from aelix_coding_agent.subagent_contract import SubagentResult

from tests.agents_ext.test_child_session_records import _parent
from tests.jobs.conftest import fast_service, make_harness

_JOB = "a1b2c3d4e5f60718"


def _receipt(key: str, **kwargs: Any) -> SpawnReceipt:
    return SpawnReceipt(
        key=key,
        tool_call_id="tc-1",
        index=0,
        mode="single",
        profile="scout",
        task_preview="count files",
        requested_model=None,
        permission_mode="plan",
        **kwargs,
    )


def _line(entry_type: str, role: str, text: str) -> str:
    return json.dumps(
        {
            "type": entry_type,
            "id": uuid.uuid4().hex[:8],
            "parentId": None,
            "timestamp": "2026-11-08T00:00:00Z",
            "message": {"role": role, "content": [{"type": "text", "text": text}]},
        }
    )


def _child_says(path: str, *answers: str) -> None:
    """What the child would have written: its task, then each answer."""

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(_line("message", "user", "count files") + "\n")
        for answer in answers:
            handle.write(_line("message", "assistant", answer) + "\n")


async def _spawned(tmp_path: Path, *answers: str) -> tuple[Session, SpawnReceipt]:
    """A parent in a bucket, a background run's opening records with its job id, and its child's file."""

    session, _repo, _path = await _parent(tmp_path)
    receipt = _receipt("sub-aaaaaaaaaaaa", job_id=_JOB)
    await receipt.open(session, cwd=str(tmp_path / "p"))
    assert receipt.child is not None
    _child_says(receipt.child.path, *answers)
    return session, receipt


async def _client_records(session: Session) -> list[Any]:
    return client_records_of(list(await session.get_entries()), _JOB)


async def _settle(session: Session, receipt: SpawnReceipt, status: str = "ok") -> None:
    result = SubagentResult(id=receipt.key, profile="scout", ok=status == "ok", status=status, summary="x")  # type: ignore[arg-type]
    records = receipt.outcome_records(result, state=_StreamState())
    await append_records(session, [(t, {**d, "job_id": _JOB}) for t, d in records])


async def test_child_file_never_yields_done_without_parent_settle(tmp_path: Path) -> None:
    session, _receipt_ = await _spawned(tmp_path, "an early answer", "its last answer")
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: session)

    recovered = await adapter.recover({}, await _client_records(session))

    assert recovered is not None
    assert recovered.status == "recovered", "never done without the parent's settle"
    assert recovered.payload["text"] == "its last answer"
    assert "could not confirm it finished" in recovered.brief
    body = adapter.render_notice(
        _outcome("done", recovered.payload)  # how the service renders a recovered job
    ).body
    assert "aelix could not confirm it finished" in body and "its last answer" in body


async def test_confirmed_client_settle_wins_over_the_child_file(tmp_path: Path) -> None:
    session, receipt = await _spawned(tmp_path, "its last answer")
    await _settle(session, receipt, "ok")
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: session)

    recovered = await adapter.recover({}, await _client_records(session))

    assert recovered is not None and recovered.status == "done"
    assert recovered.payload["settle_status"] == "ok"
    assert "text" not in recovered.payload, "the settle says how it ended; the answer is read on request"


@pytest.mark.parametrize(("settled", "status"), [("aborted", "stopped"), ("error", "failed")])
async def test_a_settle_that_did_not_finish_is_not_done(tmp_path: Path, settled: str, status: str) -> None:
    session, receipt = await _spawned(tmp_path, "partial")
    await _settle(session, receipt, settled)
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: session)
    recovered = await adapter.recover({}, await _client_records(session))
    assert recovered is not None and recovered.status == status


async def test_recovery_refuses_a_run_that_is_not_the_latest_of_its_child_file(tmp_path: Path) -> None:
    """A later start for the same child session — a resume, on any branch — owns the file's last answer."""

    session, receipt = await _spawned(tmp_path, "the first run's answer")
    assert receipt.child is not None
    later = _receipt("sub-bbbbbbbbbbbb", resumes=receipt.key)
    await later.attach(session, child=receipt.child)
    _child_says(receipt.child.path, "the resume's answer")
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: session)

    recovered = await adapter.recover({}, await _client_records(session))

    assert recovered is not None
    assert (recovered.status, recovered.reason) == ("unresolved", CANNOT_BIND_RUN)
    assert await recover_run_answer(session, receipt.key) == CANNOT_BIND_RUN
    # The resume itself IS the latest run of the file.
    answer = await recover_run_answer(session, later.key)
    assert isinstance(answer, RecoveredAnswer) and answer.text == "the resume's answer"


async def test_a_held_child_lock_is_left_to_os_evidence(tmp_path: Path) -> None:
    """Something still holds the child's file: it may still be running, so nothing is read as its end."""

    session, receipt = await _spawned(tmp_path, "an answer so far")
    assert receipt.child is not None
    lock = SessionWriterLock(receipt.child.path, timeout=0)
    assert lock.try_acquire()
    try:
        adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: session)
        assert await adapter.recover({}, await _client_records(session)) is None
    finally:
        lock.release()


async def test_no_answer_yet_is_left_to_os_evidence(tmp_path: Path) -> None:
    session, _receipt_ = await _spawned(tmp_path)
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: session)
    assert await adapter.recover({}, await _client_records(session)) is None


# === through the service: a restart, and ``agent(action="result")`` after it ==========


@pytest.fixture
def jobs_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(activation, "ACTIVE", True)
    yield
    activation.install_service(None)


async def _restart(session: Session) -> Any:
    """A fresh service — another aelix process — that registers the kind and binds the session."""

    service = fast_service()
    jobs = DelegationJobs()
    service.register_kind(
        DelegationKindAdapter(jobs, session=lambda: session), lambda _p: None  # type: ignore[arg-type,return-value]
    )
    harness = make_harness(session)
    await service.bind(harness)
    return service, jobs, harness


async def _start_fact(session: Session) -> None:
    meta = await session.get_metadata()
    await append_fact(
        session,
        make_fact(
            "start",
            _JOB,
            kind=KIND,
            kind_version=1,
            owner=meta.id,
            mode="background",
            notify=True,
            title="scout: count files",
            created=0.0,
            settings_key="agents",
        ),
    )


async def _process_fact(session: Session, pid: int, stamp: float | None) -> None:
    await append_fact(
        session,
        make_fact("process", _JOB, seq=1, evidence={"pid": pid, "started": stamp, "host": ev.this_host()}),
    )


async def _gone_process_fact(session: Session) -> None:
    """The job's recorded process: one that ran here and is gone."""

    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    stamp = process_started(child.pid)
    await child.wait()
    await _process_fact(session, child.pid, stamp)


def _outcomes(entries: list[Any]) -> list[str]:
    return [
        e.data["status"]
        for e in entries
        if e.type == "custom" and e.custom_type == JOB_FACT_TYPE and e.data["fact"] == "outcome"
    ]


async def test_a_restart_records_recovered_not_done_and_result_reads_it(tmp_path: Path, jobs_on: Any) -> None:
    """Verify B1, Codex #4 and #6: a reading from the child's file is ``recovered`` on every surface.

    The answer is not the caveat's own words (the old fixture's "its last
    answer" passed whatever the code said). Fails on ``b387b4b5``:
    ``state=done``, status ``done — …``, and ``result`` without the caveat; and
    after a second restart ``No result was recorded for it.``
    """

    session, _receipt_ = await _spawned(tmp_path, "CHILD SAID THIS")
    await _start_fact(session)
    await _gone_process_fact(session)
    service, jobs, harness = await _restart(session)

    snap = service.snapshot(_JOB)
    assert snap is not None and snap.state == "recovered"
    assert _outcomes(await session.get_entries()) == ["recovered"], "the record says recovered, never done"
    (notice,) = harness.undelivered_notices()
    assert RECOVERED_CAVEAT in notice.body and "CHILD SAID THIS" in notice.body
    status = status_text(service, jobs, [_JOB])
    assert f"(id {_JOB}): recovered" in status and "done" not in status
    waited, _empty = await wait_for_agents(service, jobs, [_JOB], requested=None)
    assert ": recovered" in waited and "done" not in waited.split("\n")[0]
    stopped = stop_text(service.snapshot(_JOB), await service.stop(_JOB), jobs)  # type: ignore[arg-type]
    assert "already ended (recovered)" in stopped and "done" not in stopped
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert not failed and text.startswith(RECOVERED_CAVEAT) and "CHILD SAID THIS" in text
    await service.shutdown()

    # A SECOND restart: the outcome is on record, the answer is read again from
    # the child's file under the same rules, and it still leads with the caveat.
    service, jobs, _harness = await _restart(session)
    snap = service.snapshot(_JOB)
    assert snap is not None and snap.state == "recovered"
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert not failed and text.startswith(RECOVERED_CAVEAT) and "CHILD SAID THIS" in text
    assert "done" not in status_text(service, jobs, [_JOB])
    assert _outcomes(await session.get_entries()) == ["recovered"]
    await service.shutdown()


async def test_a_live_recorded_root_is_never_recovered_and_keeps_its_slot(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex #4: the child's file has an answer and its lock is free, but the
    recorded process still runs — ``unresolved``, its slot held, nothing
    signalled; once it is gone the liveness recheck records ``recovered``.

    Fails on ``b387b4b5``: ``root_alive=true state=done admission=0``.
    """

    session, _receipt_ = await _spawned(tmp_path, "CHILD SAID THIS")
    await _start_fact(session)
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", "aelix_coding_agent"
    )
    real_kill = os.kill
    signalled: list[Any] = []
    monkeypatch.setattr(os, "kill", lambda *a: signalled.append(a) if a[1] else real_kill(*a))
    try:
        await _process_fact(session, child.pid, process_started(child.pid))
        service, jobs, _harness = await _restart(session)
        snap = service.snapshot(_JOB)
        assert snap is not None and snap.state == "unresolved"
        assert "still running" in (snap.unresolved_reason or "")
        assert service.admission.count(KIND) == 1
        assert _outcomes(await session.get_entries()) == []
        await service.tick()
        assert service.snapshot(_JOB).state == "unresolved"  # type: ignore[union-attr]
        assert signalled == [] and child.returncode is None
    finally:
        monkeypatch.undo()
        if child.returncode is None:
            child.kill()
        await child.wait()
    await service.tick()
    snap = service.snapshot(_JOB)
    assert snap is not None and snap.state == "recovered"
    assert service.admission.count(KIND) == 0
    assert _outcomes(await session.get_entries()) == ["recovered"]
    await service.shutdown()


async def test_cannot_bind_run_survives_the_service(tmp_path: Path, jobs_on: Any) -> None:
    """Codex #5: the adapter's ``unresolved(cannot_bind_run)`` is what the service shows.

    A later run of the same child file, and a recorded process confirmed gone.
    Fails on ``b387b4b5``: ``service_state=lost service_reason=None``.
    """

    session, receipt = await _spawned(tmp_path, "the first run's answer")
    await _start_fact(session)
    assert receipt.child is not None
    later = _receipt("sub-bbbbbbbbbbbb", resumes=receipt.key)
    await later.attach(session, child=receipt.child)
    _child_says(receipt.child.path, "the resume's answer")
    await _gone_process_fact(session)
    service, jobs, _harness = await _restart(session)
    snap = service.snapshot(_JOB)
    assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", CANNOT_BIND_RUN)
    assert service.admission.count(KIND) == 0, "its process is gone: no slot held"
    await service.tick()
    assert service.snapshot(_JOB).unresolved_reason == CANNOT_BIND_RUN  # type: ignore[union-attr]
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert failed and "the resume's answer" not in text
    await service.shutdown()


async def test_a_kinds_own_unresolved_with_its_processes_gone_promises_no_check_and_no_notice(
    tmp_path: Path, jobs_on: Any
) -> None:
    """Verify-r2 SF2: ``cannot_bind_run`` with its recorded process confirmed
    gone. The service gives the slot back and checks it no more, so ``result``,
    ``stop``, ``job result`` and the restored notice say that and promise no
    notice.

    Fails on ``fa742f77``: each said aelix "keeps checking", and all but the
    notice that what it reads of the agent's end "comes as a notice with a
    later message"; now each says aelix is not watching it.
    """

    session, receipt = await _spawned(tmp_path, "the first run's answer")
    await _start_fact(session)
    assert receipt.child is not None
    later = _receipt("sub-bbbbbbbbbbbb", resumes=receipt.key)
    await later.attach(session, child=receipt.child)
    _child_says(receipt.child.path, "the resume's answer")
    await _gone_process_fact(session)
    service, jobs, harness = await _restart(session)
    snap = service.snapshot(_JOB)
    assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", CANNOT_BIND_RUN)
    assert not service._needs_liveness(), "nothing checks it again"

    (notice,) = harness.undelivered_notices()
    output = service.output(_JOB, offset=0)
    assert not isinstance(output, str)
    texts = {
        "notice": notice.body,
        "job result": output.text,
        "result": (await result_text(service, jobs, _JOB, offset=0, session=session))[0],
        "stop": stop_text(snap, await service.stop(_JOB, reason="model"), jobs),
    }
    for name, text in texts.items():
        assert "keeps checking" not in text and "comes as a notice" not in text, name
        assert "is not watching this" in text, name
    assert texts["stop"].startswith("Not stopped.") and snap.rechecking is False
    await service.shutdown()


async def test_result_after_a_restart_follows_the_same_rules(tmp_path: Path, jobs_on: Any) -> None:
    """With the parent's settle: read from the file while it is the latest run; refused once it is not."""

    session, receipt = await _spawned(tmp_path, "the answer it settled with")
    await _start_fact(session)
    await _settle(session, receipt, "ok")
    service, jobs, _harness = await _restart(session)
    snap = service.snapshot(_JOB)
    assert snap is not None and snap.state == "done"

    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert not failed and "the answer it settled with" in text and "after a restart" in text

    assert receipt.child is not None
    later = _receipt("sub-cccccccccccc", resumes=receipt.key)
    await later.attach(session, child=receipt.child)
    _child_says(receipt.child.path, "somebody else's answer")
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert failed and CANNOT_BIND_RUN in text
    assert "somebody else's answer" not in text
    await service.shutdown()


async def test_a_restart_with_no_settle_and_no_answer_is_judged_by_evidence(tmp_path: Path, jobs_on: Any) -> None:
    """No settle, no answer, no process recorded: ``unresolved`` from the service's evidence — never ``done``."""

    session, _receipt_ = await _spawned(tmp_path)
    await _start_fact(session)
    service, _jobs, _harness = await _restart(session)
    snap = service.snapshot(_JOB)
    assert snap is not None and snap.state in ("unresolved", "lost")
    assert snap.state != "done"
    await service.shutdown()


def _outcome(status: str, payload: Any) -> Any:
    from aelix_coding_agent.jobs.contract import ClientOutcome

    return ClientOutcome(status, payload=payload)  # type: ignore[arg-type]


def test_the_records_this_file_writes_are_the_products() -> None:
    """A guard on the fixture: the receipt writes start + pending, linked to the job."""

    receipt = _receipt("sub-dddddddddddd", job_id=_JOB)
    (start, pending) = receipt.opening_records()
    assert start[0] == CHILD_SESSION_TYPE and start[1]["job_id"] == _JOB
    assert pending[0] == USAGE_TYPE


async def test_after_a_restart_stop_and_result_say_only_what_is_true(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify S1: a restart kills nothing (owner, 2026-11-07) and this process
    has no notice coming for a job it did not start — stop and result say so.

    Fails on ``b387b4b5``: stop said "aelix keeps stopping it. Its result comes
    back as a notice once it has stopped", result "its result arrives as a
    notice when it finishes".
    """

    session, _receipt_ = await _spawned(tmp_path, "an answer so far")
    await _start_fact(session)
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", "aelix_coding_agent"
    )
    real_kill, signalled = os.kill, []
    monkeypatch.setattr(os, "kill", lambda *a: signalled.append(a) if a[1] else real_kill(*a))
    if sys.platform != "win32":
        real_killpg = os.killpg
        monkeypatch.setattr(os, "killpg", lambda *a: signalled.append(a) if a[1] else real_killpg(*a))
    try:
        await _process_fact(session, child.pid, process_started(child.pid))
        service, jobs, _harness = await _restart(session)
        before = service.snapshot(_JOB)
        assert before is not None and before.state == "unresolved"
        stopped = stop_text(before, await service.stop(_JOB, reason="model"), jobs)  # type: ignore[arg-type]
        assert stopped.startswith("Not stopped.") and "stops nothing it finds at a restart" in stopped
        assert "keeps stopping" not in stopped and "comes back as a notice" not in stopped
        text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
        assert failed and "arrives as a notice when it finishes" not in text
        assert "stops nothing it finds at a restart" in text
        # Its process lives, so the liveness loop checks it again, and both say
        # so (the other side of verify-r2 SF2).
        assert before.rechecking and "keeps checking" in stopped and "keeps checking" in text
        await asyncio.sleep(0.2)
        assert signalled == [] and child.returncode is None
    finally:
        monkeypatch.undo()
        if child.returncode is None:
            child.kill()
        await child.wait()
    await service.shutdown()


async def test_a_notices_job_id_resolves_after_a_restart(tmp_path: Path, jobs_on: Any) -> None:
    """Codex #7: the id a notice's header names — the job's — resolves after a
    restart, as the child's session id and the spawn key do.

    Fails on ``b387b4b5``: ``resolve_job_id=None``.
    """

    session, receipt = await _spawned(tmp_path, "CHILD SAID THIS")
    await _start_fact(session)
    await _gone_process_fact(session)
    service, jobs, _harness = await _restart(session)
    assert receipt.child is not None
    for name in (_JOB, receipt.child.session_id, receipt.key):
        assert await jobs.resolve(service, name, session=session) == _JOB, name
    assert await jobs.resolve(service, "0123456789abcdef", session=session) is None
    await service.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="os.link over an open file is POSIX-only here")
@pytest.mark.parametrize("swap", ["after_the_check", "before_the_check"])
async def test_result_reads_the_file_it_checked_never_one_swapped_in(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:
    """Codex #3: the child's name swapped for a hard link to another file is never read.

    The check and the read are of ONE open descriptor: a swap after the check
    still reads the checked file, and one before it is refused. Fails on
    ``b387b4b5`` (``after_the_check``): ``failed=False`` and the text is the
    outside file's ``OUTSIDE CHILD SESSION SECRET``.
    """

    import aelix_agents.child_session as cs

    session, receipt = await _spawned(tmp_path, "the owned child answer")
    await _start_fact(session)
    await _settle(session, receipt, "ok")
    assert receipt.child is not None
    outside = tmp_path / "outside-session.jsonl"
    outside.write_text(_line("message", "assistant", "OUTSIDE CHILD SESSION SECRET") + "\n")
    child_path = receipt.child.path
    original = cs._check_child_file

    def swapped() -> None:
        os.unlink(child_path)
        os.link(outside, child_path)

    def checking(*args: Any, **kwargs: Any) -> Any:
        if swap == "before_the_check":
            swapped()
        verdict = original(*args, **kwargs)
        if swap == "after_the_check":
            swapped()
        return verdict

    monkeypatch.setattr(cs, "_check_child_file", checking)
    service, jobs, _harness = await _restart(session)
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert "OUTSIDE CHILD SESSION SECRET" not in text
    if swap == "after_the_check":
        assert not failed and "the owned child answer" in text
    else:
        # A read's own words (verify-r2 N7): not a resume's refusal, and one period.
        assert failed and text == (
            "Its result cannot be read: its session record is damaged "
            "(its session file has another name (a hard link))."
        )
    await service.shutdown()


async def _settled_child(tmp_path: Path, answer: str) -> tuple[Session, SpawnReceipt]:
    session, receipt = await _spawned(tmp_path, answer)
    await _start_fact(session)
    await _settle(session, receipt, "ok")
    assert receipt.child is not None
    return session, receipt


@pytest.mark.skipif(sys.platform == "win32", reason="renaming over a name held open is POSIX-only here")
async def test_a_descriptor_opened_on_a_file_swapped_out_again_is_refused(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify-r2 N1: the descriptor is opened on a file swapped in under the child's
    name — a copy of the child's file, header and origin intact, ending in another
    answer — and the child's own file is renamed back before the check. Every
    check of the NAME then passes; only the descriptor's inode, compared with the
    name's, tells the two apart.

    On ``fa742f77`` the copy's answer is not read either; the test fails there
    only on N7's exact refusal wording. Kills verify-r2's X02 and C02 (no inode
    comparison), under which the copy's answer is read as the result.
    """

    import aelix_agents.child_session as cs

    session, receipt = await _settled_child(tmp_path, "the owned child answer")
    child_path = receipt.child.path  # type: ignore[union-attr]
    saved, copy = tmp_path / "saved-child.jsonl", tmp_path / "copy.jsonl"
    copy.write_bytes(
        Path(child_path).read_bytes()
        + (_line("message", "assistant", "A COPY'S ANSWER") + "\n").encode()
    )
    os.rename(child_path, saved)
    os.rename(copy, child_path)
    original = cs._check_child_file

    def swapped_back(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("fd") is not None and saved.exists():
            os.rename(child_path, copy)
            os.rename(saved, child_path)
        return original(*args, **kwargs)

    monkeypatch.setattr(cs, "_check_child_file", swapped_back)
    service, jobs, _harness = await _restart(session)
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert not saved.exists(), "the child's own file was back under its name before the check"
    assert "A COPY'S ANSWER" not in text
    assert failed and text == (
        "Its result cannot be read: its session record is damaged "
        "(its session file was replaced while it was read)."
    )
    await service.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="os.link over an open file is POSIX-only here")
async def test_a_second_name_made_after_the_names_check_is_refused_by_the_descriptors(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify-r2 N1: a hard link made between the name's ``lstat`` and the
    descriptor's ``fstat`` — the same file, so the inode comparison passes — is
    caught by the descriptor's own link count.

    On ``fa742f77`` the answer is not read either; the test fails there only on
    N7's exact refusal wording. Kills verify-r2's C01 and X02 (no link count read
    from the descriptor), under which the answer is read.
    """

    import aelix_agents.child_session as cs

    session, receipt = await _settled_child(tmp_path, "the owned child answer")
    child_path = receipt.child.path  # type: ignore[union-attr]
    second = tmp_path / "second-name.jsonl"
    original_check, real_fstat = cs._check_child_file, os.fstat
    armed = {"on": False}

    def linking_fstat(fd: int) -> Any:
        if armed["on"] and not second.exists():
            os.link(child_path, second)
        return real_fstat(fd)

    def checking(*args: Any, **kwargs: Any) -> Any:
        armed["on"] = kwargs.get("fd") is not None
        try:
            return original_check(*args, **kwargs)
        finally:
            armed["on"] = False

    monkeypatch.setattr(cs, "_check_child_file", checking)
    monkeypatch.setattr(cs.os, "fstat", linking_fstat)
    service, jobs, _harness = await _restart(session)
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    monkeypatch.undo()
    assert second.exists(), "the second name was made inside the check"
    assert "the owned child answer" not in text
    assert failed and text == (
        "Its result cannot be read: its session record is damaged "
        "(its session file has another name (a hard link))."
    )
    await service.shutdown()


async def test_a_recovered_result_read_again_after_a_second_restart_waits_for_a_free_file(
    tmp_path: Path, jobs_on: Any
) -> None:
    """Verify-r2 J13: after a second restart a ``recovered`` agent's answer is read
    from its file again, under the restart's own rule — a held writer lock may
    be a live writer, so nothing is read while one is held. Once it is free the
    answer reads, with its caveat.

    A guard on ``fa742f77``: kills verify-r2's J13 (no lock check on that read),
    under which the held file's answer is the result.
    """

    session, receipt = await _spawned(tmp_path, "CHILD SAID THIS")
    await _start_fact(session)
    await _gone_process_fact(session)
    service, _jobs, _harness = await _restart(session)
    assert service.snapshot(_JOB).state == "recovered"  # type: ignore[union-attr]
    await service.shutdown()
    service, jobs, _harness = await _restart(session)
    assert receipt.child is not None
    lock = SessionWriterLock(receipt.child.path, timeout=0)
    assert lock.try_acquire()
    try:
        text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    finally:
        lock.release()
    assert failed and "something holds its session file" in text
    assert "CHILD SAID THIS" not in text
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert not failed and text.startswith(RECOVERED_CAVEAT) and "CHILD SAID THIS" in text
    await service.shutdown()


@pytest.mark.parametrize("run", ["the-models", "before-limits", "not-the-models"])
async def test_a_resume_hint_is_placed_only_for_a_resume_the_branch_would_accept(
    tmp_path: Path, run: str
) -> None:
    """Verify-r2 J01: the branch holds the run's start, but its own lookup would
    refuse the resume — the run recorded no limits, or ``/agents run`` started
    it — so no hint is placed, neither the resume nor "not on the branch".
    ``the-models`` is the guard: a run the resume would take gets its hint.

    Kills verify-r2's J01 (any other refusal falls through to the hint): the
    refused cases name ``agent(action="resume", …)``.
    """

    from aelix_agents.child_session import (
        RESUME_BEFORE_LIMITS,
        RESUME_NOT_YOURS,
        resume_lineage_refusal,
    )

    from tests.agents_ext.test_agent_resume_610 import _limits

    session, _repo, _path = await _parent(tmp_path)
    receipt = SpawnReceipt(
        key="sub-aaaaaaaaaaaa",
        tool_call_id=None if run == "not-the-models" else "tc-1",
        index=0,
        mode="single",
        profile="scout",
        task_preview="count files",
        requested_model=None,
        permission_mode="plan",
        # Paths from ``tmp_path``: ``"/"`` is not absolute on win32 (py3.13+).
        limits=None
        if run == "before-limits"
        else _limits(cwd=str(tmp_path / "p"), profile_source=str(tmp_path / "scout.md")).to_record(),
        job_id=_JOB,
    )
    await receipt.open(session, cwd=str(tmp_path / "p"))
    assert receipt.child is not None
    branch = list(await session.get_branch())
    refusal = resume_lineage_refusal(branch, receipt.child.session_id)
    expected = {"the-models": None, "before-limits": RESUME_BEFORE_LIMITS, "not-the-models": RESUME_NOT_YOURS}
    assert refusal == expected[run]
    payload = {"session_id": receipt.child.session_id, "key": receipt.key, "profile": "scout"}
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: session)
    hint = await adapter.finalize_at_placement(_outcome("done", payload), branch)
    if run == "the-models":
        assert hint is not None and 'agent(action="resume"' in hint
    else:
        assert hint is None


async def test_result_never_reads_the_child_file_without_a_confirmed_end(tmp_path: Path) -> None:
    """Verify V11: an ended agent whose payload names its run but no settle and no
    envelope — and that no restart read as ``recovered`` — is not answered from
    the child's file."""

    from aelix_coding_agent.jobs.contract import JobSnapshot

    session, receipt = await _spawned(tmp_path, "an answer nobody confirmed")

    class _Service:
        def snapshot(self, job_id: str) -> Any:
            return JobSnapshot(
                job_id=job_id,
                kind=KIND,
                label="agent",
                title="t",
                state="failed",
                owner="me",
                created=0.0,
                elapsed=None,
                last_activity_ago=None,
                quiet=False,
                result_waiting=False,
                notice_state="delivered",
                payload={"key": receipt.key},
            )

    text, failed = await result_text(_Service(), DelegationJobs(), _JOB, offset=0, session=session)
    assert failed and "no confirmed end of this run" in text
    assert "an answer nobody confirmed" not in text
