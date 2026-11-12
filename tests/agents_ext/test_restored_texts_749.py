"""#749 — what ``agent(action="result"|"stop")`` and ``job result`` say of an agent aelix cannot finish reading.

Verify-r3 of #723 left four shapes that said more than was true, a slot held
after one failed read, and five mutants no test killed (R02, R06, R07, R08,
R10). Each case below names how it fails on ``363accf2``.

AT WHAT LEVEL. The parent's records are written by the product's own
``SpawnReceipt`` into a parent session inside a real bucket, the child's file
is the one it allocated, and a restart is a fresh job service that registers
the REAL delegation kind (``tests/agents_ext/test_child_recovery_611.py``'s
fixtures, reused).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import child_session, jobs_client
from aelix_agents.child_session import CANNOT_BIND_RUN
from aelix_agents.jobs_client import (
    KIND,
    AgentJob,
    DelegationJobs,
    DelegationKindAdapter,
    result_text,
    stop_text,
)
from aelix_ai.utils._process_identity import process_started
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.contract import ClientOutcome, HelperCleanup
from aelix_coding_agent.jobs.records import append_fact, make_fact

from tests.agents_ext.test_child_recovery_611 import (
    _JOB,
    _child_says,
    _gone_process_fact,
    _process_fact,
    _receipt,
    _restart,
    _settle,
    _spawned,
    _start_fact,
)
from tests.agents_ext.test_child_session_records import _parent
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_on")


@pytest.fixture
def jobs_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(activation, "ACTIVE", True)
    yield
    activation.install_service(None)


async def _live_child() -> Any:
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", "aelix_coding_agent"
    )


async def _texts(service: Any, jobs: Any, session: Any) -> dict[str, str]:
    snap = service.snapshot(_JOB)
    output = service.output(_JOB, offset=0)
    return {
        "job result": output if isinstance(output, str) else output.text,
        "result": (await result_text(service, jobs, _JOB, offset=0, session=session))[0],
        "stop": stop_text(snap, await service.stop(_JOB, reason="model"), jobs),
    }


# === 1. cannot_bind_run with a live process ======================================


async def test_cannot_bind_run_with_a_live_process_promises_no_notice(tmp_path: Path) -> None:
    """The kind's own ``unresolved(cannot_bind_run)`` is checked until its process is gone,
    and its end cannot be read then either: nothing promises a notice, before or after.

    Fails on ``363accf2``: ``result``, ``stop`` and ``job result`` say "what it
    can read of its end comes as a notice with a later message", the restored
    notice "it keeps checking", and after the process died no notice came
    (verify-r3 SF-A's ``test_kind_unresolved_live_then_gone`` probe).
    """

    session, receipt = await _spawned(tmp_path, "the first run's answer")
    await _start_fact(session)
    assert receipt.child is not None
    later = _receipt("sub-bbbbbbbbbbbb", resumes=receipt.key)
    await later.attach(session, child=receipt.child)
    _child_says(receipt.child.path, "the resume's answer")
    child = await _live_child()
    try:
        await _process_fact(session, child.pid, process_started(child.pid))
        service, jobs, harness = await _restart(session)
        (notice,) = harness.undelivered_notices()
        texts = {"notice": notice.body, **await _texts(service, jobs, session)}
        for name, text in texts.items():
            assert "comes as a notice" not in text and "keeps checking" not in text, (name, text)
        assert "promises no later notice" in texts["notice"]
        for name in ("job result", "result", "stop"):
            assert "so it may still be running" in texts[name], name
            assert "aelix checks its processes until they are gone" in texts[name], name
            assert "cannot read this run's end from it: no notice is promised" in texts[name], name
        snap = service.snapshot(_JOB)
        assert snap is not None and (snap.rechecking, snap.processes_gone) == (True, False)
    finally:
        child.kill()
        await child.wait()
    for _ in range(3):
        await service.tick()
    snap = service.snapshot(_JOB)
    assert snap is not None and (snap.state, snap.unresolved_reason) == ("unresolved", CANNOT_BIND_RUN)
    assert (snap.rechecking, snap.processes_gone) == (False, True)
    assert len(harness.undelivered_notices()) == 1, "no second notice"
    text = (await result_text(service, jobs, _JOB, offset=0, session=session))[0]
    assert "and its processes are gone; it is not watching this agent" in text
    await service.shutdown()


# === 2. rechecking false for states never checked against the OS ===================


async def _boom(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("boom")


def _raise(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("boom")


async def _nothing(*_a: Any, **_k: Any) -> None:
    return None


@pytest.mark.parametrize("why", ["recover_error", "relation_error", "adapter_too_old"])
async def test_a_state_never_checked_against_the_os_still_says_it_may_be_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    """``rechecking`` is false, but nobody read its processes: it may still run.

    Fails on ``363accf2``: with ``rechecking`` false every text dropped "it may
    still be running" and read "…and it is not watching this agent" (verify-r3
    nit 5).
    """

    session, _receipt_ = await _spawned(tmp_path, "an answer")
    if why == "adapter_too_old":
        meta = await session.get_metadata()
        await append_fact(
            session,
            make_fact("start", _JOB, kind=KIND, kind_version=99, owner=meta.id, mode="background",
                      notify=True, title="scout: count files", created=0.0, settings_key="agents"),
        )
    else:
        await _start_fact(session)
    await _gone_process_fact(session)
    if why == "recover_error":
        monkeypatch.setattr(DelegationKindAdapter, "recover", _boom)
    elif why == "relation_error":
        monkeypatch.setattr(DelegationKindAdapter, "recover", _nothing)
        monkeypatch.setattr(DelegationKindAdapter, "relation", _raise)
    service, jobs, _harness = await _restart(session)
    snap = service.snapshot(_JOB)
    assert snap is not None and (snap.unresolved_reason, snap.rechecking) == (why, False)
    for name, text in (await _texts(service, jobs, session)).items():
        assert "so it may still be running; it is not watching this agent" in text, (name, text)
        assert "its processes are gone" not in text, name
    await service.shutdown()


# === 3. a failed read during a recheck ===========================================


async def test_a_failed_read_during_a_recheck_gives_the_slot_back_and_says_what_is_true(
    tmp_path: Path,
) -> None:
    """Verify-r3 nit 4, through the delegation kind: its process is gone, one read fails.

    Fails on ``363accf2``: ``rechecking False | liveness False | adm 1`` — the
    slot held until aelix exits — and ``result`` says "it is not watching this
    agent".
    """

    session, _receipt_ = await _spawned(tmp_path)
    await _start_fact(session)
    child = await _live_child()
    try:
        await _process_fact(session, child.pid, process_started(child.pid))
        service, jobs, harness = await _restart(session)
        assert service.admission.count(KIND) == 1
    finally:
        child.kill()
        await child.wait()
    real = session.get_entries

    async def failing() -> Any:
        raise OSError("disk")

    session.get_entries = failing  # type: ignore[method-assign]
    try:
        await service.tick()
    finally:
        session.get_entries = real  # type: ignore[method-assign]
    assert service.admission.count(KIND) == 0, "its slot is back"
    text = (await result_text(service, jobs, _JOB, offset=0, session=session))[0]
    assert "and its processes are gone; aelix reads its records again" in text, text
    snap = service.snapshot(_JOB)
    assert snap is not None and (snap.state, snap.rechecking) == ("unresolved", True)
    await service.tick()
    snap = service.snapshot(_JOB)
    assert snap is not None and snap.state == "lost", "read again: no answer, its process gone"
    assert [n.notice_id.rsplit(":", 1)[1] for n in harness.undelivered_notices()] == [
        "unresolved",
        "final",
    ]
    await service.shutdown()


# === 4. what result says when the file cannot be read: a sentence, never a code ======


async def _restarted_with_a_settle(tmp_path: Path, *answers: str) -> tuple[Any, Any, Any, Any]:
    session, receipt = await _spawned(tmp_path, *answers)
    await _start_fact(session)
    await _settle(session, receipt, "ok")
    return session, receipt, *(await _restart(session))[:2]


async def test_no_answer_reads_as_a_sentence(tmp_path: Path) -> None:
    """Fails on ``363accf2``: "Its result cannot be read: no_answer." (verify-r3 nit 8)."""

    session, _receipt_, service, jobs = await _restarted_with_a_settle(tmp_path)
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert failed and text == "Its result cannot be read: its session file holds no answer from it."
    await service.shutdown()


async def test_a_gone_child_file_reads_as_gone(tmp_path: Path) -> None:
    """The ``os.open`` failure branch of ``_checked_answer`` and ``_READ_REFUSALS``' first row.

    Fails on ``363accf2`` under verify-r3's mutants R06 (the row removed:
    "…did not pass aelix's checks") and R10 (``_as_read`` removed from that
    branch: "…Resume refused: that agent's session file no longer exists.
    Delegate the task anew."); on ``363accf2`` itself it passes, the guard of
    the right wording.
    """

    session, receipt, service, jobs = await _restarted_with_a_settle(tmp_path, "an answer")
    assert receipt.child is not None
    os.unlink(receipt.child.path)
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert failed and text == "Its result cannot be read: its session file no longer exists."
    await service.shutdown()


async def test_a_forked_child_file_reads_as_the_other_conversations(tmp_path: Path) -> None:
    """``_READ_REFUSALS``' second row: the child's origin names another parent.

    A guard on ``363accf2``; fails under verify-r3's mutant R07 ("…did not pass
    aelix's checks").
    """

    session, receipt, service, jobs = await _restarted_with_a_settle(tmp_path, "an answer")
    assert receipt.child is not None
    path = receipt.child.path
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    origin = json.loads(lines[1])
    origin["data"]["parent"]["session_id"] = "another-parent-session"
    lines[1] = json.dumps(origin)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert failed and text == (
        "Its result cannot be read: it belongs to the conversation this one was forked from."
    )
    await service.shutdown()


async def test_a_child_that_never_started_reads_so(tmp_path: Path) -> None:
    """``_READ_REFUSALS``' third row: the child's file holds no task.

    A guard on ``363accf2``; fails under verify-r3's mutant R08 ("…did not pass
    aelix's checks").
    """

    session, _repo, _path = await _parent(tmp_path)
    receipt = _receipt("sub-aaaaaaaaaaaa", job_id=_JOB)
    await receipt.open(session, cwd=str(tmp_path / "p"))
    await _start_fact(session)
    await _settle(session, receipt, "ok")
    service, jobs, _harness = await _restart(session)
    text, failed = await result_text(service, jobs, _JOB, offset=0, session=session)
    assert failed and text == "Its result cannot be read: it never started; its session holds no task."
    await service.shutdown()


def _codes_returned() -> set[str]:
    """Every code ``recover_run_answer`` returns as a literal or a module constant."""

    tree = ast.parse(inspect.getsource(child_session.recover_run_answer))
    codes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                codes.add(node.value.value)
            elif isinstance(node.value, ast.Name) and node.value.id.isupper():
                codes.add(getattr(child_session, node.value.id))
    return codes


def test_every_code_recover_run_answer_returns_has_a_sentence() -> None:
    """Fails on ``363accf2``: no sentence exists for any of them (``jobs_client`` printed the code).

    The scan finds what it looks for: the seven codes below, ``cannot_bind_run``
    through its constant.
    """

    codes = _codes_returned()
    assert codes == {
        "no_session_file",
        "unreadable",
        "no_start_record",
        "no_child_file",
        CANNOT_BIND_RUN,
        "damaged",
        "no_answer",
    }
    sentences = jobs_client._READ_REASONS
    assert codes <= set(sentences)
    for code in codes:
        assert "_" not in sentences[code] and sentences[code][0].islower(), code


# === 5. a job this process runs that nothing rechecks: settle_error ================


class _SettleFails:
    """A runner whose agent answered and whose settle fails, so the job stays ``unresolved(settle_error)``."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = params

    async def run(self, ctx: Any) -> ClientOutcome:
        ctx.launched()
        return ClientOutcome("done", "answered", {"text": "the answer"})

    def request_stop(self, reason: str) -> None:
        pass

    async def settle(self) -> None:
        raise RuntimeError("settle broke")

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return []


async def test_a_settle_error_agent_promises_no_notice(tmp_path: Path) -> None:
    """``result`` and ``stop`` of an agent this process runs whose settle failed: nothing checks
    it again (``rechecking`` false, ``tick`` skips it), so no notice of its end is coming.

    Fails on ``363accf2``: ``result`` said "its result arrives as a notice when
    it finishes" and ``stop`` "aelix keeps stopping it. Its result comes back as
    a notice once it has stopped" — neither happens.
    """

    service = fast_service(cleanup_bound=1.0)
    jobs = DelegationJobs()
    service.register_kind(DelegationKindAdapter(jobs, session=lambda: None), _SettleFails)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(KIND, title="scout: count files")
    jobs.info[job_id] = AgentJob(job_id=job_id, profile="scout")
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    text, failed = await result_text(service, jobs, job_id, offset=0, session=None)
    assert failed and "arrives as a notice" not in text
    assert text == (
        "It is unresolved (settle_error): aelix cannot tell how it ended and does not check "
        "it again, so no notice of its end is coming."
    )
    before = service.snapshot(job_id)
    stopped = stop_text(before, await service.stop(job_id, reason="model"), jobs)
    assert "keeps stopping" not in stopped and "comes back as a notice" not in stopped
    assert stopped.startswith("Asked to stop. It is unresolved (settle_error)")
    await service.shutdown()


# === fix round 1: the generic result surfaces and the branches no test read (verify F1, F6) ===


class _Agent:
    """A runner for the real delegation adapter, with no text of its own (none in ``jobs.info``).

    ``params``: ``gate`` (an ``asyncio.Event`` its run waits for), ``settle`` and
    ``cleanup`` (``"raise"`` to fail that step).
    """

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.gate: asyncio.Event | None = params.get("gate")
        self.settle_raises = params.get("settle") == "raise"
        self.cleanup_raises = params.get("cleanup") == "raise"

    async def run(self, ctx: Any) -> ClientOutcome:
        ctx.launched()
        if self.gate is not None:
            await self.gate.wait()
        return ClientOutcome("done", "answered", {})

    def request_stop(self, reason: str) -> None:
        pass

    async def settle(self) -> None:
        if self.settle_raises:
            raise RuntimeError("settle broke")

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        if self.cleanup_raises:
            raise RuntimeError("cleanup broke")
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return []


async def _this_process_agent(tmp_path: Path, **params: Any) -> tuple[Any, Any, str]:
    from aelix_agents.jobs_client import AgentJob

    service = fast_service(cleanup_bound=0.5, stop_grace=0.5)
    jobs = DelegationJobs()
    service.register_kind(DelegationKindAdapter(jobs, session=lambda: None), _Agent)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(KIND, title="scout: count files", params=params)
    jobs.info[job_id] = AgentJob(job_id=job_id, profile="scout")
    return service, jobs, job_id


async def _generic_results(service: Any, job_id: str) -> list[str]:
    from aelix_coding_agent.jobs.commands import JobsCommand
    from aelix_coding_agent.jobs.tool import create_job_tool

    tool = await create_job_tool(service).execute({"action": "result", "id": job_id})
    return [tool.content[0].text, await JobsCommand(service)(f"result {job_id}")]  # type: ignore[union-attr]


async def test_job_result_of_a_settle_error_agent_with_no_text_promises_no_notice(tmp_path: Path) -> None:
    """Verify F1, Codex 6: ``job(action="result")`` and ``/jobs result`` say what ``result`` says.

    Fails on ``5390b254``: both said "It is still running; its result arrives
    as a notice." (verify's ``test_zz_v749_jobresult.py``).
    """

    service, jobs, job_id = await _this_process_agent(tmp_path, settle="raise")
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    said = (
        "It is unresolved (settle_error): aelix cannot tell how it ended and does not check "
        "it again, so no notice of its end is coming."
    )
    assert await _generic_results(service, job_id) == [said, said]
    assert (await result_text(service, jobs, job_id, offset=0, session=None))[0] == said
    await service.shutdown()


async def test_a_running_agent_with_no_text_is_said_to_be_running_on_every_result_surface(
    tmp_path: Path,
) -> None:
    """Verify F6's J08 and J10: ``running`` and ``stopping`` are not ``unresolved``.

    Fails on ``5390b254`` for the generic surfaces, which said "It is still
    running; its result arrives as a notice." (no ``when it finishes``).
    """

    gate = asyncio.Event()
    service, jobs, job_id = await _this_process_agent(tmp_path, gate=gate)
    said = "It is running; its result arrives as a notice when it finishes."
    assert (await result_text(service, jobs, job_id, offset=0, session=None))[0] == said
    assert await _generic_results(service, job_id) == [said, said]
    before = service.snapshot(job_id)
    after = await service.stop(job_id, reason="model", wait=0.05)
    assert after is not None and after.state == "stopping"
    assert stop_text(before, after, jobs) == (
        "Asked to stop; it is stopping, and aelix keeps stopping it. Its result comes back as "
        "a notice once it has stopped."
    )
    gate.set()
    await service.shutdown()


async def test_an_unresolved_agent_that_is_rechecked_is_said_to_come_back_as_a_notice(
    tmp_path: Path,
) -> None:
    """Verify F6's J07 and J09: a failed cleanup is retried and its notice follows.

    A guard on ``5390b254`` for ``result`` and ``stop``; the generic surfaces
    said "It is still running" there.
    """

    service, jobs, job_id = await _this_process_agent(tmp_path, cleanup="raise")
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    snap = service.snapshot(job_id)
    assert snap is not None and (snap.unresolved_reason, snap.rechecking) == ("cleanup_error", True)
    said = "It is unresolved; its result arrives as a notice when it finishes."
    assert (await result_text(service, jobs, job_id, offset=0, session=None))[0] == said
    assert await _generic_results(service, job_id) == [said, said]
    after = await service.stop(job_id, reason="model", wait=0.05)
    assert stop_text(snap, after, jobs) == (  # type: ignore[arg-type]
        "Asked to stop; it is unresolved, and aelix keeps stopping it. Its result comes back as "
        "a notice once it has stopped."
    )
    await service.shutdown()


async def test_cannot_bind_run_whose_process_is_gone_and_read_failed_does_not_say_aelix_checks_it(
    tmp_path: Path,
) -> None:
    """Verify F6's J04: its processes are gone and a read of its records failed, so it is
    rechecked for its records only; the text does not say aelix checks its processes.

    A guard on ``5390b254`` (the branch is there and right).
    """

    session, receipt = await _spawned(tmp_path, "the first run's answer")
    await _start_fact(session)
    assert receipt.child is not None
    later = _receipt("sub-bbbbbbbbbbbb", resumes=receipt.key)
    await later.attach(session, child=receipt.child)
    _child_says(receipt.child.path, "the resume's answer")
    child = await _live_child()
    try:
        await _process_fact(session, child.pid, process_started(child.pid))
        service, jobs, _harness = await _restart(session)
    finally:
        child.kill()
        await child.wait()
    real = session.get_entries

    async def failing() -> Any:
        raise OSError("disk")

    session.get_entries = failing  # type: ignore[method-assign]
    try:
        await service.tick()
    finally:
        session.get_entries = real  # type: ignore[method-assign]
    snap = service.snapshot(_JOB)
    assert snap is not None and (snap.unresolved_reason, snap.rechecking, snap.processes_gone) == (
        CANNOT_BIND_RUN,
        True,
        True,
    )
    text = (await result_text(service, jobs, _JOB, offset=0, session=session))[0]
    assert "and its processes are gone. A later run of this agent wrote" in text
    assert "checks its processes" not in text
    await service.shutdown()
