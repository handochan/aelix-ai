"""#769 — the job service keeps a session's ended jobs whole only up to a bound, and reads the rest back.

The lead's policy: past :data:`~aelix_coding_agent.jobs.service.SETTLED_KEPT`
ended jobs of one session, the oldest that owes nothing — its outcome written,
its notice placed or never owed — keeps only what its session's records say of
it, and every surface reads it as before. A job that runs, stops, is
``unresolved`` or still owes its notice is never let go.

Run with kinds of this file and the synthetic kind (``tests/jobs/_sleeper.py``),
so what is pinned is the service's, whatever kind uses it; the product kinds'
own proofs are ``tests/tools/test_bash_retention_769.py`` and
``tests/agents_ext/test_agent_retention_769.py``.

Fails on ``363accf2``: ``JobService`` takes no ``settled_kept`` and keeps every
job it ran in ``_jobs`` for the process's life (``JobService.start`` put them in
and nothing took them out).
"""

from __future__ import annotations

import asyncio
import dataclasses
import gc
import weakref
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs import commands
from aelix_coding_agent.jobs.commands import JobsCommand
from aelix_coding_agent.jobs.contract import (
    ClientOutcome,
    HelperCleanup,
    JobSnapshot,
    KindCapabilities,
    NoticeBody,
    OutputSlice,
    RelationSpec,
)
from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.jobs.service import SETTLED_KEPT
from aelix_coding_agent.jobs.tool import create_job_stop_tool, create_job_tool

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")

KIND = "test:quick"


class _QuickAdapter:
    """A kind whose job answers at once and records its answer: what it reads back is what it said."""

    name = KIND
    version = 1
    label = "quick"
    settings_key = "quick"
    # Its reads come from its record: it declares so, as bash and delegation do.
    capabilities = KindCapabilities(default_bound=8, notify=True, reads_persisted=True)

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        fact = client.get("fact", {})
        said = next(
            (str(r.get("said", "")) for r in client.get("records", []) if r.get("custom_type") == "test.quick"),
            "",
        )
        return ClientOutcome(status=fact.get("status", "done"), brief=str(fact.get("brief", "")), payload={"said": said})

    def render_notice(self, outcome: ClientOutcome) -> NoticeBody:
        return NoticeBody(f"it said {outcome.payload.get('said', '')}")

    async def finalize_at_placement(self, outcome: ClientOutcome, branch: list[Any]) -> str | None:
        return None

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        return [f"quick {snapshot.title}: {snapshot.payload.get('said', '?')}"[:width]]

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        text = str(job.payload.get("said", ""))
        return OutputSlice(text[offset : offset + limit], offset, len(text), offset + limit < len(text))

    async def recover(self, facts: Mapping[str, Any], client_records: list[Mapping[str, Any]]) -> Any:
        return None

    def relation(self, facts: Mapping[str, Any]) -> RelationSpec:
        return RelationSpec()

    def restored_note(self, facts: Mapping[str, Any]) -> str | None:
        return None


class _Quick:
    """The runner: no process, its answer is its params' ``said``; ``records`` fails on request."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.said = str(params.get("said", ""))
        self.records_fail = bool(params.get("records_fail", False))
        self.gate: asyncio.Event | None = params.get("gate")
        self.status = params.get("status", "done")

    async def run(self, ctx: Any) -> ClientOutcome:
        ctx.launched()
        if self.gate is not None:
            await self.gate.wait()
        return ClientOutcome(self.status, f"said {len(self.said)} characters", {"said": self.said})

    def request_stop(self, reason: str) -> None:
        pass

    async def settle(self) -> None:
        pass

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        return HelperCleanup("none")

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        if self.records_fail:
            raise RuntimeError("records broke")
        return [("test.quick", {"said": self.said})]


async def _service(tmp_path: Path, **kwargs: Any) -> tuple[Any, Any]:
    service = fast_service(**kwargs)
    service.register_kind(_QuickAdapter(), _Quick)
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    return service, harness


async def _ended(service: Any, said: str, *, notify: bool = False, **params: Any) -> str:
    job_id = await service.start(KIND, title=said[:12], params={"said": said, **params}, notify=notify)
    ended = params.get("status", "done")
    await wait_for(lambda: service.snapshot(job_id).state == ended)  # type: ignore[union-attr]
    return str(job_id)


async def _trimmed(service: Any) -> None:
    """Wait for the trim the last end or placement scheduled."""

    await wait_for(lambda: service._trimming is None or service._trimming.done())


async def test_the_map_holds_the_bound_and_lets_go_of_the_oldest_that_owe_nothing(tmp_path: Path) -> None:
    """``SETTLED_KEPT`` + 6 ended jobs: the map holds the bound, and the 6 that went are the oldest
    that owed nothing — not the three older ones whose notices were still owed.

    Fails on ``363accf2``: ``SETTLED_KEPT`` cannot be imported, and the map
    held all 70.
    """

    service, harness = await _service(tmp_path)
    owed = [await _ended(service, f"owed {i}", notify=True) for i in range(3)]
    quiet = [await _ended(service, f"answer {i}") for i in range(SETTLED_KEPT + 3)]
    await _trimmed(service)
    assert SETTLED_KEPT == 64
    assert len(service._jobs) == SETTLED_KEPT
    assert list(service._settled) == quiet[:6], "the oldest that owed nothing, oldest first"
    assert all(job_id in service._jobs for job_id in owed), "a notice still owed keeps its job whole"

    await harness.prompt_notices()  # the three notices are placed: they owe nothing now
    await _trimmed(service)
    assert len(service._jobs) == SETTLED_KEPT, "at the bound already"
    later = await _ended(service, "one more")
    await _trimmed(service)
    assert len(service._jobs) == SETTLED_KEPT
    assert list(service._settled)[-1] == owed[0], "the oldest of them goes next"
    assert later in service._jobs
    assert [s.job_id for s in service.snapshots()] == [*owed, *quiet, later], "listed in start order"
    await service.shutdown()


async def test_the_bound_is_per_session(tmp_path: Path) -> None:
    """Another session's ended jobs neither count toward this one's bound nor go for it."""

    service, _harness = await _service(tmp_path, settled_kept=2)
    first = [await _ended(service, f"a{i}") for i in range(3)]
    other = await make_session(tmp_path, name="b.jsonl", session_id="b")
    await service.bind(make_harness(other))
    second = [await _ended(service, f"b{i}") for i in range(2)]
    await _trimmed(service)
    assert list(service._settled) == first[:1]
    assert all(job_id in service._jobs for job_id in [*first[1:], *second])
    await service.shutdown()


async def test_a_job_that_runs_is_unresolved_or_owes_its_notice_is_never_let_go(tmp_path: Path) -> None:
    """With a bound of 0 every ended job that owes nothing goes at once — and nothing else does."""

    service, harness = await _service(tmp_path, settled_kept=0, cleanup_bound=1.0)
    running = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    unresolved = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.05, "fail": ["settle"]})
    await wait_for(lambda: service.snapshot(unresolved).state == "unresolved")  # type: ignore[union-attr]
    owed = await _ended(service, "owed", notify=True)
    gone = await _ended(service, "gone")
    await _trimmed(service)
    assert list(service._settled) == [gone]
    assert {running, unresolved, owed} <= set(service._jobs)
    await service.stop(running)
    assert service.snapshot(running).state == "stopped"  # type: ignore[union-attr]
    await _trimmed(service)
    assert {running, unresolved, owed} <= set(service._jobs), "both notices are still owed"
    await harness.prompt_notices()
    await wait_for(lambda: owed in service._settled)
    # The sleeper kind did not declare ``reads_persisted``: its stopped job stays whole (#769).
    assert {running, unresolved} <= set(service._jobs)
    await service.shutdown()


async def test_a_job_whose_records_did_not_all_land_is_kept_whole(tmp_path: Path) -> None:
    """Its kind could not read it back as it reads it now (``client_records: missing``): it stays.

    So no surface reads less of it than before — the honest answer when the
    records cannot back one.
    """

    service, _harness = await _service(tmp_path, settled_kept=0)
    kept = await _ended(service, "not recorded", records_fail=True)
    gone = await _ended(service, "recorded")
    await _trimmed(service)
    assert list(service._settled) == [gone] and kept in service._jobs
    assert service.output(kept, offset=0).text == "not recorded"  # type: ignore[union-attr]
    await service.shutdown()


async def _reads(service: Any, job_id: str) -> dict[str, str]:
    """Every surface a user or the model reads of one job, as text."""

    snap = service.snapshot(job_id)
    assert snap is not None
    jobs = JobsCommand(service)
    job = create_job_tool(service)
    stop = create_job_stop_tool(service)

    async def tool(t: Any, **args: Any) -> str:
        result = await t.execute(args)
        return f"{result.is_error}|{result.content[0].text}"

    return {
        "/jobs": await jobs(""),
        "/jobs <id>": await jobs(job_id[:8]),
        "/jobs result": await jobs(f"result {job_id}"),
        "/jobs stop": await jobs(f"stop {job_id}"),
        "detail": "\n".join(commands.detail(service, snap, width=160)),
        "job list": await tool(job, action="list"),
        "job status": await tool(job, action="status", id=job_id),
        "job result": await tool(job, action="result", id=job_id),
        "job result 2": await tool(job, action="result", id=job_id, offset=2),
        "job_stop": await tool(stop, id=job_id),
        "footer": str(indicator_text(service.snapshots(), width=80)),
        "-p waits": str(service.unsettled()),
        "request_stop()": repr(service.request_stop(job_id)),
    }


def _same_snapshot(before: JobSnapshot, after: JobSnapshot) -> None:
    """Field for field, but ``elapsed``: the outcome fact keeps it to the millisecond.

    ``progress`` is ``None`` here on both sides (this kind reports none); a job
    let go keeps none, as a restart's snapshot has none.
    """

    assert after.elapsed is not None and before.elapsed is not None
    assert abs(after.elapsed - before.elapsed) < 0.001
    assert dataclasses.replace(after, elapsed=None) == dataclasses.replace(before, elapsed=None)


async def test_every_surface_reads_a_job_it_let_go_as_before(tmp_path: Path) -> None:
    """``/jobs`` (list, detail, result, stop), ``job`` (list, status, result), ``job_stop``, the
    footer, the ``-p`` drain's check, ``stop`` and the snapshot itself: the same text, byte for byte,
    before and after the job is kept only as its records read.

    Fails on ``363accf2``: nothing is let go (``settled_kept`` is not a
    parameter).
    """

    service, harness = await _service(tmp_path)
    first = await _ended(service, "the first answer, 가나다", notify=True)
    second = await _ended(service, "the second answer")
    third = await _ended(service, "it failed", notify=True, status="failed")
    ids = (first, second, third)
    await harness.prompt_notices()
    before = {job_id: await _reads(service, job_id) for job_id in ids}
    snaps = {job_id: service.snapshot(job_id) for job_id in ids}
    stopped = {job_id: await service.stop(job_id) for job_id in ids}
    service._settled_kept = 0
    service._trim_later()
    await _trimmed(service)
    assert list(service._settled) == list(ids) and service._jobs == {}
    assert service.snapshot(third).state == "failed"  # type: ignore[union-attr]
    after = {job_id: await _reads(service, job_id) for job_id in ids}
    for job_id in ids:
        for surface, text in before[job_id].items():
            assert after[job_id][surface] == text, (job_id, surface)
        _same_snapshot(snaps[job_id], service.snapshot(job_id))  # type: ignore[arg-type]
        _same_snapshot(stopped[job_id], await service.stop(job_id))  # type: ignore[arg-type]
    assert "the first answer, 가나다" in after[first]["job result"]
    await service.shutdown()


async def test_a_job_let_go_frees_its_runner_context_and_task(tmp_path: Path) -> None:
    """Weak references to the job, its runner, its context and its task are dead once it went.

    Fails on ``363accf2``: the service's map keeps the job, and through it the
    rest, for the process's life.
    """

    service, _harness = await _service(tmp_path, settled_kept=0)
    gate = asyncio.Event()
    job_id = await service.start(KIND, title="watched", params={"said": "watched", "gate": gate}, notify=False)
    held = service._jobs[job_id]
    refs = {
        "job": weakref.ref(held),
        "runner": weakref.ref(held.runner),
        "context": weakref.ref(held.ctx),
        "task": weakref.ref(held.task),
    }
    del held
    gate.set()
    await wait_for(lambda: job_id in service._settled)

    def freed() -> bool:
        gc.collect()
        return all(ref() is None for ref in refs.values())

    await wait_for(freed, 5.0)
    assert service.snapshot(job_id).state == "done"  # type: ignore[union-attr]
    await service.shutdown()


@pytest.mark.parametrize("breaks", ["read", "decode"])
async def test_a_job_whose_records_cannot_be_read_back_is_kept_whole_and_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, breaks: str
) -> None:
    """A session read that fails, or a kind's ``decode`` that raises, keeps the job whole; the
    next trim tries again."""

    service, harness = await _service(tmp_path, settled_kept=0)
    adapter = service.registry.get(KIND).adapter
    session = harness.session
    real_read, real_decode = session.get_entries, adapter.decode

    async def no_read() -> Any:
        raise OSError("the disk went away for a moment")

    def no_decode(_client: Any) -> Any:
        raise RuntimeError("decode broke")

    if breaks == "read":
        session.get_entries = no_read  # type: ignore[method-assign]
    else:
        monkeypatch.setattr(adapter, "decode", no_decode)
    kept = await _ended(service, "kept")
    await _trimmed(service)
    assert kept in service._jobs and service._settled == {}
    session.get_entries = real_read  # type: ignore[method-assign]
    monkeypatch.setattr(adapter, "decode", real_decode)
    gone = await _ended(service, "the next one")
    await _trimmed(service)
    assert list(service._settled) == [kept, gone], "tried again at the next trim"
    await service.shutdown()


@pytest.mark.parametrize("disagree", ["file says not placed", "file says placed"])
async def test_the_records_and_the_map_must_both_say_the_notice_owes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disagree: str
) -> None:
    """Placed in memory but no delivery entry in the file, or the other way round: kept whole."""

    from aelix_coding_agent.jobs import service as service_module

    service, harness = await _service(tmp_path, settled_kept=0)
    job_id = await _ended(service, "a notice", notify=True)
    if disagree == "file says not placed":

        async def no(*_a: Any) -> bool:
            return False

        monkeypatch.setattr(service_module, "delivered", no)
        await harness.prompt_notices()
        assert service.snapshot(job_id).notice_state == "delivered"  # type: ignore[union-attr]
    else:

        async def yes(*_a: Any) -> bool:
            return True

        monkeypatch.setattr(service_module, "delivered", yes)
    service._trim_later()
    await _trimmed(service)
    assert job_id in service._jobs and service._settled == {}
    await service.shutdown()


async def test_a_job_whose_outcome_waits_in_the_outbox_is_never_let_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its outcome landed but the write said it did not: the outbox owes the retry and its
    ``_after_outcome``, so the job stays whole even though the file holds the outcome."""

    from aelix_coding_agent.jobs import service as service_module
    from aelix_coding_agent.jobs.records import RecordWriteFailed

    real = service_module.append_fact
    raised: list[str] = []

    async def lands_but_raises(session: Any, fact: dict[str, Any]) -> None:
        await real(session, fact)
        if fact.get("fact") == "outcome" and not raised:
            raised.append(fact["job_id"])
            raise RecordWriteFailed("outcome unknown")

    monkeypatch.setattr(service_module, "append_fact", lands_but_raises)
    monkeypatch.setattr(service_module, "_OUTBOX_BACKOFF", (60.0,) * 5)
    service, _harness = await _service(tmp_path, settled_kept=0)
    first = await service.start(KIND, title="outbox", params={"said": "x"}, notify=False)
    await wait_for(lambda: bool(raised))
    second = await _ended(service, "next")
    await _trimmed(service)
    assert raised == [first]
    assert first in service._jobs and service._jobs[first].outbox is not None
    assert list(service._settled) == [second]
    await service.shutdown()


async def test_another_sessions_job_let_go_is_not_found(tmp_path: Path) -> None:
    """Codex #5 holds for a job let go: another session's id reads nothing."""

    service, _harness = await _service(tmp_path, settled_kept=0)
    mine = await _ended(service, "mine")
    await _trimmed(service)
    assert mine in service._settled
    other = await make_session(tmp_path, name="b.jsonl", session_id="b")
    await service.bind(make_harness(other))
    assert service.snapshot(mine) is None and service.snapshots() == []
    assert (await JobsCommand(service)(mine)).startswith("no job")
    await service.shutdown()


async def test_a_session_reconciled_after_its_job_was_let_go_lists_it_once(tmp_path: Path) -> None:
    """A job started for a session not yet bound, then let go, then that session bound: the
    restart reading skips it, so it is not restored a second time."""

    service, _harness = await _service(tmp_path, settled_kept=0)
    other = await make_session(tmp_path, name="b.jsonl", session_id="b")
    job_id = await service.start(KIND, title="for b", params={"said": "b"}, notify=False, session=other)
    await wait_for(lambda: job_id in service._settled)
    await service.bind(make_harness(other))
    assert [s.job_id for s in service.snapshots()] == [job_id]
    assert job_id not in service._restored
    await service.shutdown()


class _SlowRead:
    """A session read that waits for ``gate``: a trim is held inside its read-back."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.real = session.get_entries
        self.gate = asyncio.Event()
        self.waiting = 0

    async def __call__(self) -> Any:
        self.waiting += 1
        await self.gate.wait()
        return await self.real()


async def test_a_trim_asked_for_while_one_runs_runs_again(tmp_path: Path) -> None:
    """A job that ends while a trim is inside its read-back is let go by a second pass."""

    service, harness = await _service(tmp_path, settled_kept=0)
    slow = _SlowRead(harness.session)
    harness.session.get_entries = slow  # type: ignore[method-assign]
    first = await _ended(service, "first")
    await wait_for(lambda: slow.waiting == 1)
    second = await _ended(service, "second")
    slow.gate.set()
    await _trimmed(service)
    assert list(service._settled) == [first, second]
    await service.shutdown()


async def test_shutdown_cancels_a_trim_that_is_still_reading(tmp_path: Path) -> None:
    service, harness = await _service(tmp_path, settled_kept=0)
    slow = _SlowRead(harness.session)
    harness.session.get_entries = slow  # type: ignore[method-assign]
    await _ended(service, "held")
    await wait_for(lambda: slow.waiting == 1)
    trimming = service._trimming
    assert trimming is not None and not trimming.done()
    await service.shutdown()
    assert trimming.cancelled()


@pytest.mark.parametrize("still", ["task", "escalation"])
async def test_a_job_whose_task_or_escalation_still_runs_is_kept_whole(
    tmp_path: Path, still: str
) -> None:
    """Owing nothing includes its tasks being over, so a weak reference to it can die once it goes.

    No product path ends a job's run with them still running today (``_drive``
    returns in the step that posted its notice); this pins the rule itself.
    """

    service, harness = await _service(tmp_path, settled_kept=0)
    job_id = await _ended(service, "held", notify=True)  # its notice owed: not let go yet
    job = service._jobs[job_id]
    pending: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    if still == "task":
        job.task = pending  # type: ignore[assignment]
    else:
        job.escalation = pending  # type: ignore[assignment]
    await harness.prompt_notices()
    await _trimmed(service)
    assert job.notice_state == "delivered"
    assert job_id in service._jobs, f"its {still} has not ended"
    pending.set_result(None)
    service._trim_later()
    await _trimmed(service)
    assert job_id in service._settled
    await service.shutdown()


# === fix round 1 (Codex 1-4 and 7, verify F2, F3, F6 and F7) ======================


async def test_a_job_whose_read_back_would_read_otherwise_is_kept_whole(tmp_path: Path) -> None:
    """Codex 1: a kind whose result lives only in memory decodes without raising, but its read
    back is empty. The service compares the read-back with the live reads and keeps it.

    Fails on ``5390b254``: it is let go and ``job result`` reads ``""`` (Codex's
    ``NON_REBUILDABLE`` probe).
    """

    class Ephemeral(_Quick):
        def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
            return []

    service = fast_service(settled_kept=0)
    service.register_kind(_QuickAdapter(), Ephemeral)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await _ended(service, "answer only held live")
    await _trimmed(service)
    assert job_id in service._jobs and service._settled == {}
    assert service.output(job_id, offset=0).text == "answer only held live"  # type: ignore[union-attr]
    await service.shutdown()


async def test_a_job_let_go_keeps_its_last_progress_and_its_elapsed(tmp_path: Path) -> None:
    """Codex 3, verify F7: rows a kind renders from its last progress, and the snapshot an
    extension reads, are the same after it is let go.

    Fails on ``5390b254``: ``progress`` is ``None`` after (rows read "missing
    progress"), and ``elapsed`` is the outcome's milliseconds.
    """

    class Progress(_Quick):
        async def run(self, ctx: Any) -> ClientOutcome:
            ctx.progress({"summary": "processed 23 rows"})
            return await super().run(ctx)

    class Adapter(_QuickAdapter):
        def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
            return [str((snapshot.progress or {}).get("summary", "missing progress"))]

    service = fast_service(settled_kept=0)
    service.register_kind(Adapter(), Progress)
    await service.bind(make_harness(await make_session(tmp_path)))
    gate = asyncio.Event()
    job_id = await service.start(KIND, title="rows", params={"said": "x", "gate": gate}, notify=True)
    gate.set()
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    before = service.snapshot(job_id)
    rows = service.render_rows(before, 100)  # type: ignore[arg-type]
    await service._harness.prompt_notices()  # type: ignore[union-attr]
    await wait_for(lambda: job_id in service._settled)
    after = service.snapshot(job_id)
    assert service.render_rows(after, 100) == rows == ["processed 23 rows"]  # type: ignore[arg-type]
    placed = {"payload": {}, "notice_state": "delivered", "result_waiting": False}
    assert dataclasses.replace(after, **placed) == dataclasses.replace(before, **placed)  # type: ignore[arg-type]
    assert after.elapsed == before.elapsed  # type: ignore[union-attr]
    await service.shutdown()


async def test_a_job_whose_run_ended_with_no_outcome_is_kept_whole(tmp_path: Path) -> None:
    """Verify F2: an outcome that says its kind's records are missing keeps the job whole,
    whatever its run returned — also when it returned nothing.

    Fails on ``5390b254``: a run that raised (no outcome, ``client_records:
    missing``) is let go (Codex's ``MISSING_WITHOUT_OUTCOME``).
    """

    class Broken(_Quick):
        async def run(self, ctx: Any) -> ClientOutcome:
            ctx.launched()
            raise RuntimeError("failed before returning an outcome")

    service = fast_service(settled_kept=0)
    service.register_kind(_QuickAdapter(), Broken)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(KIND, title="error", notify=False)
    await wait_for(lambda: service.snapshot(job_id).state == "failed")  # type: ignore[union-attr]
    await _trimmed(service)
    service._trim_later()
    await _trimmed(service)
    assert job_id in service._jobs
    await service.shutdown()


async def test_a_job_is_not_let_go_while_its_unresolved_notice_is_owed(tmp_path: Path) -> None:
    """Codex 2: a launch that failed after its cleanup failed once posts an ``unresolved``
    notice; its completion then posts none and sets ``notice`` to ``None``. Every notice it
    posted must be placed first.

    Fails on ``5390b254``: it is let go with its ``unresolved`` notice still
    pending in the inbox (``OWED_AFTER``).
    """

    from aelix_coding_agent.jobs.service import JobLaunchFailed

    class LaunchFails(_Quick):
        once = False

        async def run(self, ctx: Any) -> ClientOutcome:
            ctx.launch_failed("launch failed after acquiring a resource")
            return ClientOutcome("failed", "launch failed", {"said": ""})

        async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
            if not LaunchFails.once:
                LaunchFails.once = True
                raise RuntimeError("cleanup failed once")
            return HelperCleanup("none")

    service = fast_service(settled_kept=0, interval=1000)
    service.register_kind(_QuickAdapter(), LaunchFails)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    with pytest.raises(JobLaunchFailed):
        await service.start(KIND, title="failed start")
    (job_id,) = service._jobs
    assert service.snapshot(job_id).state == "unresolved" and harness.pending_notices == 1  # type: ignore[union-attr]
    service._jobs[job_id].cleanup_due = 0
    await service.tick()
    await _trimmed(service)
    assert service.snapshot(job_id).state == "failed"  # type: ignore[union-attr]
    assert job_id in service._jobs, "its unresolved notice is still owed"
    await harness.prompt_notices()
    await wait_for(lambda: job_id in service._settled)
    await service.shutdown()


async def test_a_job_let_go_keeps_no_old_generation_of_its_kind_alive(tmp_path: Path) -> None:
    """Codex 4: four generations of a kind, each from a runtime that ended; once their jobs are
    let go only the registry's current runtime is alive.

    Fails on ``5390b254``: ``_Settled`` kept each job's ``Registration``, and
    with it its runtime's ``assert_active`` (``runtimes_alive`` 4).
    """

    from aelix_coding_agent.extensions.api import _ExtensionRuntime

    service = fast_service(settled_kept=64)
    await service.bind(make_harness(await make_session(tmp_path)))
    refs = []
    for i in range(4):
        runtime = _ExtensionRuntime()
        refs.append(weakref.ref(runtime))
        service.register_kind(_QuickAdapter(), _Quick, lifetime=runtime.assert_active)
        await _ended(service, str(i))
        runtime.invalidate()
    del runtime
    service._settled_kept = 0
    service._trim_later()
    await _trimmed(service)
    assert len(service._settled) == 4 and service._jobs == {}
    gc.collect()
    assert sum(ref() is not None for ref in refs) == 1, "the registry's own, current one"
    assert [s.title for s in service.snapshots()] == ["0", "1", "2", "3"]
    await service.shutdown()


async def test_jobs_that_started_at_the_same_time_keep_their_order(tmp_path: Path) -> None:
    """Codex 7, verify F3: a coarse wall clock gives two jobs one start time; letting go of the
    first does not move it after the second.

    Fails on ``5390b254``: ``["second", "first"]`` (the kept job sorted first).
    """

    service = fast_service(wall=lambda: 1000.0)
    service.register_kind(_QuickAdapter(), _Quick)
    await service.bind(make_harness(await make_session(tmp_path)))
    await _ended(service, "first")
    await _ended(service, "second")
    before = [s.title for s in service.snapshots()]
    service._settled_kept = 1
    service._trim_later()
    await _trimmed(service)
    assert len(service._settled) == 1
    assert [s.title for s in service.snapshots()] == before == ["first", "second"]
    await service.shutdown()


async def test_a_running_job_does_not_count_toward_the_bound(tmp_path: Path) -> None:
    """Verify F6's T08: only ended jobs count; a running one neither counts nor goes.

    A guard on ``5390b254`` (it passes there).
    """

    service, _harness = await _service(tmp_path, settled_kept=1)
    running = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    first = await _ended(service, "first")
    second = await _ended(service, "second")
    await _trimmed(service)
    assert list(service._settled) == [first] and {running, second} <= set(service._jobs)
    await service.stop(running)
    await service.shutdown()


async def test_a_job_that_cannot_be_read_back_does_not_count_as_let_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify F6's T11: the oldest cannot be read back, so the next two go in its place.

    A guard on ``5390b254`` (it passes there).
    """

    service, _harness = await _service(tmp_path, settled_kept=1)
    adapter = service.registry.get(KIND).adapter
    real = adapter.decode

    def decode(client: Mapping[str, Any]) -> ClientOutcome:
        records = client.get("records", [])
        if any(r.get("said") == "unreadable" for r in records):
            raise RuntimeError("decode broke for this one")
        return real(client)

    monkeypatch.setattr(adapter, "decode", decode)
    gate = asyncio.Event()
    kept = await service.start(KIND, title="u", params={"said": "unreadable", "gate": gate}, notify=False)
    others = [
        await service.start(KIND, title=t, params={"said": t, "gate": gate}, notify=False)
        for t in ("second", "third")
    ]
    gate.set()
    await wait_for(lambda: all(service.snapshot(j).state == "done" for j in (kept, *others)))  # type: ignore[union-attr]
    await _trimmed(service)
    service._trim_later()
    await _trimmed(service)
    assert kept in service._jobs and list(service._settled) == others
    await service.shutdown()


async def test_a_notice_lost_with_a_replaced_harness_does_not_hold_its_job(tmp_path: Path) -> None:
    """Its ``unresolved`` notice waited in a harness a session switch replaced; its final notice,
    posted again where its session is current and placed there, is what it owed.

    A guard of fix round 1's own rule (5390b254 had no notice ids to track).
    """

    class CleanupFailsOnce(_Quick):
        once = False

        async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
            if not CleanupFailsOnce.once:
                CleanupFailsOnce.once = True
                raise RuntimeError("cleanup failed once")
            return HelperCleanup("none")

    service = fast_service(settled_kept=0, interval=1000)
    service.register_kind(_QuickAdapter(), CleanupFailsOnce)
    first = await make_session(tmp_path)
    await service.bind(make_harness(first))
    job_id = await service.start(KIND, title="cleanup", params={"said": "x"}, notify=True)
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    await service.bind(make_harness(await make_session(tmp_path, name="b.jsonl", session_id="b")))
    service._jobs[job_id].cleanup_due = 0
    await service.tick()
    assert service._jobs[job_id].state == "done"
    back = make_harness(first)
    await service.bind(back)
    await back.prompt_notices()
    await wait_for(lambda: job_id in service._settled)
    assert service.snapshot(job_id).notice_state == "delivered"  # type: ignore[union-attr]
    await service.shutdown()


# === fix round 1, the comparison's own parts (mutants F02, F04, F07, F18, v-T03) =========


async def test_rows_that_would_read_otherwise_keep_the_job_whole(tmp_path: Path) -> None:
    """A kind whose rows read a payload value only its live payload has: kept whole, though its
    output reads alike."""

    class LiveOnly(_Quick):
        async def run(self, ctx: Any) -> ClientOutcome:
            outcome = await super().run(ctx)
            return ClientOutcome(outcome.status, outcome.brief, {**outcome.payload, "row": "live only"})

    class Adapter(_QuickAdapter):
        def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
            return [str(snapshot.payload.get("row", "no row"))]

    service = fast_service(settled_kept=0)
    service.register_kind(Adapter(), LiveOnly)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await _ended(service, "same output")
    await _trimmed(service)
    assert job_id in service._jobs
    await service.shutdown()


async def test_a_read_back_whose_facts_disagree_with_the_live_job_keeps_it_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its outcome fact's brief — what ``/jobs`` shows of an ended job — read back otherwise."""

    from aelix_coding_agent.jobs import service as service_module

    real = service_module.fold

    def other_brief(entries: list[Any]) -> Any:
        folded = real(entries)
        for job in folded.values():
            if job.outcome is not None:
                job.outcome = {**job.outcome, "brief": "a brief no reader saw"}
        return folded

    service, _harness = await _service(tmp_path, settled_kept=0)
    monkeypatch.setattr(service_module, "fold", other_brief)
    job_id = await _ended(service, "x")
    await _trimmed(service)
    assert job_id in service._jobs
    await service.shutdown()


async def test_records_that_did_not_all_land_keep_a_job_whole_even_when_it_reads_alike(
    tmp_path: Path,
) -> None:
    """``client_records: missing`` keeps a job whole by itself: here no read differs."""

    class Broken(_Quick):
        async def run(self, ctx: Any) -> ClientOutcome:
            ctx.launched()
            raise RuntimeError("no outcome")

    class Plain(_QuickAdapter):
        def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
            return [snapshot.title]

        def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
            return OutputSlice("", offset)

    service = fast_service(settled_kept=0)
    service.register_kind(Plain(), Broken)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(KIND, title="no outcome", notify=False)
    await wait_for(lambda: service.snapshot(job_id).state == "failed")  # type: ignore[union-attr]
    await _trimmed(service)
    service._trim_later()
    await _trimmed(service)
    assert job_id in service._jobs
    await service.shutdown()


async def test_a_job_let_go_reads_its_kinds_current_label(tmp_path: Path) -> None:
    """A later generation of its kind relabels it, as it relabels a job kept whole."""

    service, _harness = await _service(tmp_path, settled_kept=0)
    gone = await _ended(service, "gone")
    await _trimmed(service)
    assert gone in service._settled

    class Relabelled(_QuickAdapter):
        label = "quick, relabelled"

    service.register_kind(Relabelled(), _Quick)
    assert service.snapshot(gone).label == "quick, relabelled"  # type: ignore[union-attr]
    await service.shutdown()


async def test_no_trim_starts_after_shutdown(tmp_path: Path) -> None:
    service, _harness = await _service(tmp_path)
    await service.shutdown()
    service._trim_later()
    assert service._trimming is None


# === fix round 2 (verify-r2 F1, F2) =========================================================


class _CleanupFailsOnce(_Quick):
    """Its first cleanup fails, so it posts an ``unresolved`` notice before its final one."""

    failed: set[str] = set()

    async def helper_cleanup(self, receipt: Mapping[str, Any]) -> HelperCleanup:
        job_id = str(receipt.get("job_id"))
        if job_id not in _CleanupFailsOnce.failed:
            _CleanupFailsOnce.failed.add(job_id)
            raise RuntimeError("cleanup failed once")
        return HelperCleanup("none")


async def _both_notices(tmp_path: Path) -> tuple[Any, Any, Any, str]:
    """A job whose ``unresolved`` notice and final notice both wait for one boundary."""

    service = fast_service(settled_kept=0, interval=1000)
    service.register_kind(_QuickAdapter(), _CleanupFailsOnce)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    await service.bind(harness)
    job_id = await service.start(KIND, title="cleanup", params={"said": "x"}, notify=True)
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    service._jobs[job_id].cleanup_due = 0
    await service.tick()
    assert service.snapshot(job_id).state == "done" and harness.pending_notices == 2  # type: ignore[union-attr]
    return service, harness, session, job_id


async def test_a_job_is_delivered_once_both_its_notices_are_placed_whatever_their_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify-r2 F1: the ``unresolved`` notice's write fails once, so the final one is placed
    first and the ``unresolved`` one at the next boundary. Between the two the job owes a notice
    (it reads waiting, and is not let go); after both it is delivered and let go.

    Fails on ``c06128c8``: it stayed ``pending`` for good ("1 result waiting" in the footer,
    never let go; verifier's ``test_p2b``).
    """

    service, harness, session, job_id = await _both_notices(tmp_path)
    real = session.place_custom_message
    failed: list[str] = []

    async def flaky(entry_id: str, *a: Any, **k: Any) -> Any:
        details = a[3] if len(a) > 3 else {}
        if not failed and any("unresolved" in str(v) for v in (details or {}).values()):
            failed.append(entry_id)
            raise OSError("a transient write failure")
        return await real(entry_id, *a, **k)

    monkeypatch.setattr(session, "place_custom_message", flaky)
    as_placed: list[tuple[str, str]] = []
    let_go_while_waiting: list[list[str]] = []

    def on_placed(notice: Any) -> None:
        snap = service.snapshot(job_id)
        as_placed.append((notice.notice_id.rsplit(":", 1)[1], snap.notice_state if snap else "gone"))

    def on_change() -> None:
        if job_id in service._settled and not let_go_while_waiting:
            let_go_while_waiting.append([n.notice_id for n in harness.undelivered_notices()])

    service.add_placed_listener(on_placed)
    service.add_listener(on_change)
    await harness.prompt_notices()
    await wait_for(lambda: job_id in service._settled)
    assert failed and as_placed == [("final", "pending"), ("unresolved", "delivered")]
    assert let_go_while_waiting == [[]], "let go only once nothing of it waits"
    snap = service.snapshot(job_id)
    assert snap is not None and snap.notice_state == "delivered"
    assert indicator_text(service.snapshots(), width=80) is None
    await service.shutdown()


async def test_a_held_unresolved_notice_does_not_hold_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify-r2 F1: a placement conflict holds the ``unresolved`` notice out of every drain; it
    waits nowhere, so the final notice placed is the job's delivery.

    Fails on ``c06128c8``: ``pending`` for good, and ``/jobs send`` had nothing to send
    (verifier's ``test_p2``).
    """

    from aelix_agent_core.session.storage import PlacementConflict

    service, harness, session, job_id = await _both_notices(tmp_path)
    real = session.place_custom_message

    async def conflict(entry_id: str, *a: Any, **k: Any) -> Any:
        details = a[3] if len(a) > 3 else {}
        if any("unresolved" in str(v) for v in (details or {}).values()):
            raise PlacementConflict("another writer placed it")
        return await real(entry_id, *a, **k)

    monkeypatch.setattr(session, "place_custom_message", conflict)
    await harness.prompt_notices()
    await wait_for(lambda: job_id in service._settled)
    assert service.snapshot(job_id).notice_state == "delivered"  # type: ignore[union-attr]
    await service.shutdown()


async def test_an_earlier_notice_placed_does_not_deliver_a_final_one_not_yet_posted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final notice waits in the outbox (its outcome not written yet) when the ``unresolved``
    one is placed: the job still owes its final notice and does not read delivered.

    A guard on ``c06128c8`` (it passes there).
    """

    from aelix_coding_agent.jobs import service as service_module
    from aelix_coding_agent.jobs.records import RecordWriteFailed

    monkeypatch.setattr(service_module, "_OUTBOX_BACKOFF", (60.0,) * 5)
    service = fast_service(settled_kept=0, interval=1000)
    service.register_kind(_QuickAdapter(), _CleanupFailsOnce)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(KIND, title="cleanup", params={"said": "x"}, notify=True)
    await wait_for(lambda: service.snapshot(job_id).state == "unresolved")  # type: ignore[union-attr]
    real = service_module.append_fact

    async def no_outcome(session: Any, fact: dict[str, Any]) -> None:
        if fact.get("fact") == "outcome":
            raise RecordWriteFailed("not now")
        await real(session, fact)

    monkeypatch.setattr(service_module, "append_fact", no_outcome)
    service._jobs[job_id].cleanup_due = 0
    await service.tick()
    assert service._jobs[job_id].outbox is not None and harness.pending_notices == 1
    await harness.prompt_notices()
    assert service.snapshot(job_id).notice_state == "pending", "its final notice is still owed"  # type: ignore[union-attr]
    await service.shutdown()


class _UndeclaredCapped(_QuickAdapter):
    """An extension kind that did not declare ``reads_persisted``; its record keeps 20,000 of its
    answer's characters, and its first page reads alike (verifier's ``test_p1``)."""

    name = "test:capped"
    label = "capped"
    capabilities = KindCapabilities(default_bound=8, notify=True)

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        fact = client.get("fact", {})
        said = next((str(r.get("said", "")) for r in client.get("records", []) if "said" in r), "")
        return ClientOutcome(status=fact.get("status", "done"), brief=str(fact.get("brief", "")), payload={"said": said})

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        return [f"capped {snapshot.title}"[:width]]

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        text = str(job.payload.get("said", ""))
        return OutputSlice(text[offset : offset + limit], offset, None, offset + limit < len(text))


class _CappedRunner(_Quick):
    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        return [("test.capped", {"said": self.said[:20000]})]


async def test_a_kind_that_did_not_declare_its_reads_persisted_stays_whole(tmp_path: Path) -> None:
    """Verify-r2 F2: only a kind declaring ``reads_persisted`` is let go; this one's later pages
    would read otherwise once read back, though its first page reads alike.

    Fails on ``c06128c8``: it was let go, and ``job(action="result", offset=32768)`` then read
    ``(empty output)``.
    """

    service = fast_service(settled_kept=0)
    service.register_kind(_UndeclaredCapped(), _CappedRunner)
    await service.bind(make_harness(await make_session(tmp_path)))
    said = "".join(f"line {i:05d}\n" for i in range(4000))  # 44,000 characters
    job_id = await service.start("test:capped", title="capped", params={"said": said}, notify=False)
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    tool = create_job_tool(service)
    before = (await tool.execute({"action": "result", "id": job_id, "offset": 32768})).content[0].text  # type: ignore[union-attr]
    await _trimmed(service)
    service._trim_later()
    await _trimmed(service)
    assert job_id in service._jobs and service._settled == {}
    after = (await tool.execute({"action": "result", "id": job_id, "offset": 32768})).content[0].text  # type: ignore[union-attr]
    assert after == before and "line 03999" in after
    await service.shutdown()


class _TailDiffers(_QuickAdapter):
    """A declaring kind whose rows or first page read its record's ``row``/``said`` back."""

    def decode(self, client: Mapping[str, Any]) -> ClientOutcome:
        fact = client.get("fact", {})
        record = next((r for r in client.get("records", []) if r.get("custom_type") == "test.tail"), {})
        payload = {"said": record.get("said", ""), "row": record.get("row", "")}
        return ClientOutcome(status=fact.get("status", "done"), brief=str(fact.get("brief", "")), payload=payload)

    def render_rows(self, snapshot: JobSnapshot, width: int) -> list[str]:
        return [str(snapshot.payload.get("row", ""))[:width]]


class _TailRunner(_Quick):
    """Its live payload and its record agree on their first characters only."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        super().__init__(params)
        self.where = params.get("where", "rows")

    async def run(self, ctx: Any) -> ClientOutcome:
        ctx.launched()
        row = "0123456789-live" if self.where == "rows" else "the same row"
        return ClientOutcome("done", "ok", {"said": self.said, "row": row})

    def records(self, outcome: ClientOutcome) -> list[tuple[str, dict[str, Any]]]:
        # The same length, so a page's ``total`` agrees: only bytes past the 20th differ.
        said = self.said if self.where == "rows" else self.said[:20] + "-the REC! answer"
        row = "0123456789-rec" if self.where == "rows" else "the same row"
        return [("test.tail", {"said": said, "row": row})]


@pytest.mark.parametrize("where", ["rows", "page"])
async def test_a_difference_past_the_first_columns_or_bytes_keeps_the_job_whole(
    tmp_path: Path, where: str
) -> None:
    """Verify-r3 F4 (mutants D10, D11): a row that differs only past column 10, or a first page
    that differs only past byte 20, is a difference: the job stays whole."""

    service = fast_service(settled_kept=0)
    service.register_kind(_TailDiffers(), _TailRunner)
    await service.bind(make_harness(await make_session(tmp_path)))
    said = "01234567890123456789-the live answer"
    job_id = await service.start(KIND, title="tail", params={"said": said, "where": where}, notify=False)
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    await _trimmed(service)
    service._trim_later()
    await _trimmed(service)
    assert job_id in service._jobs and service._settled == {}
    await service.shutdown()
