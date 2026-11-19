"""#612 (contract r5 §6.4; Codex r4-2 7) — a job start the owner's end overtook is refused.

``JobService.start`` awaits twice before it registers a job (the owner read and
the ``start`` fact append). An ``owner_ended`` for that owner — a session swap or
quit, ``session_shutdown`` — that runs inside those awaits took its snapshot of
the owner's live jobs before the job existed, so the job registered after it and
ran for a session that had ended (Codex measured it at ``1c2a4a7c``:
``.omc/probes/612/codex-r4-r2/job-events.txt``, "old-owner job registered after
snapshot: True"). With Q4 opening ``spawn`` and ``run_in_background`` on RPC a
host's swap meets that window more often.

r5: the service raises a per-owner ended epoch synchronously at the top of
``owner_ended`` and never forgets it; a start reads the epoch at entry and refuses
at registration (no await between the check and the insertion) when the owner's
epoch rose since, or ``shutdown`` closed the service, writing its outcome fact
``failed``. Each of the first three tests fails on ``567753c0``: the parked start
registered its job.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs import evidence as ev
from aelix_coding_agent.jobs import service as jobs
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE
from aelix_coding_agent.jobs.service import JobService, JobStartRefused

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session

pytestmark = pytest.mark.usefixtures("jobs_active")


async def _bound(tmp_path: Path, *, detachable: bool = False) -> tuple[JobService, Any]:
    service = fast_service()
    adapter = _sleeper.SleeperAdapter()
    if detachable:
        adapter.capabilities = dataclasses.replace(adapter.capabilities, detachable=True)
    service.register_kind(adapter, _sleeper.Sleeper)
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))
    return service, session


class _Parked:
    """``append_fact`` parked on its FIRST call (the start fact) until released."""

    def __init__(self) -> None:
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self._real = jobs.append_fact

    async def __call__(self, session: Any, fact: dict[str, Any]) -> None:
        self.calls += 1
        if self.calls == 1:
            self.arrived.set()
            await self.release.wait()
        await self._real(session, fact)


async def _facts(session: Any) -> list[dict[str, Any]]:
    entries = await session.get_entries()
    return [e.data for e in entries if e.type == "custom" and e.custom_type == JOB_FACT_TYPE]


async def _park_then(
    service: JobService, monkeypatch: pytest.MonkeyPatch, during: Any
) -> BaseException | str:
    parked = _Parked()
    monkeypatch.setattr(jobs, "append_fact", parked)
    start = asyncio.ensure_future(service.start(_sleeper.KIND, title="raced"))
    await asyncio.wait_for(parked.arrived.wait(), 5)
    await during()
    parked.release.set()
    done, _ = await asyncio.wait({start}, timeout=5)
    assert done, "the start never returned"
    try:
        return start.result()
    except JobStartRefused as exc:
        return exc


async def test_a_start_parked_while_its_owner_with_no_jobs_ended_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``owner_ended`` for an owner with no live job returns at once; the parked start still refuses."""

    service, session = await _bound(tmp_path)
    owner = service.owner
    assert owner is not None
    outcome = await _park_then(service, monkeypatch, lambda: service.owner_ended(owner, "owner_end"))
    assert isinstance(outcome, JobStartRefused), f"registered {outcome!r} after its owner ended"
    assert str(outcome) == jobs.SESSION_ENDED_FIRST
    assert service.snapshots() == [] and service._jobs == {}  # noqa: SLF001
    facts = await _facts(session)
    assert [f["fact"] for f in facts] == ["start", "outcome"]
    assert facts[1]["status"] == "failed" and facts[1]["brief"] == jobs.SESSION_ENDED_FIRST
    assert service.admission.count(_sleeper.KIND) == 0, "the refused start gave its slot back"
    await service.shutdown()


async def test_a_start_parked_across_shutdown_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``shutdown`` sets ``_closed`` after the start passed its entry check; the start re-checks it."""

    service, session = await _bound(tmp_path)
    outcome = await _park_then(service, monkeypatch, service.shutdown)
    assert isinstance(outcome, JobStartRefused), f"registered {outcome!r} after shutdown"
    assert service._jobs == {}  # noqa: SLF001
    facts = await _facts(session)
    assert [f["fact"] for f in facts] == ["start", "outcome"]
    assert facts[1]["status"] == "failed"


async def test_a_reload_of_the_same_owner_refuses_the_parked_start_and_admits_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``/reload`` ends and re-binds the SAME owner: the epoch, not the owner's name, decides."""

    service, session = await _bound(tmp_path)
    owner = service.owner
    assert owner is not None

    async def reload() -> None:
        await service.owner_ended(owner, "reload")
        await service.bind(make_harness(session), reason="reload")

    outcome = await _park_then(service, monkeypatch, reload)
    assert isinstance(outcome, JobStartRefused)
    assert service.owner == owner
    monkeypatch.setattr(jobs, "append_fact", _Parked()._real)
    job_id = await service.start(_sleeper.KIND, title="after the reload")
    assert service.snapshot(job_id) is not None
    await service.shutdown()


async def test_an_owner_ended_after_the_start_registered_stops_its_job(
    tmp_path: Path,
) -> None:
    """Guard (passes on the base): a job registered before the end is stopped by it, as before."""

    service, _session = await _bound(tmp_path)
    owner = service.owner
    assert owner is not None
    job_id = await service.start(_sleeper.KIND, title="ran")
    await service.owner_ended(owner, "owner_end")
    snap = service.snapshot(job_id)
    assert snap is not None and snap.state in ("stopped", "unresolved")
    await service.shutdown()


async def test_ctrl_b_adoption_refuses_an_owner_ended_since_the_bind(tmp_path: Path) -> None:
    """The adoption door checks the same pair: closed, or the bound owner ended since the bind."""

    service, session = await _bound(tmp_path, detachable=True)
    owner = service.owner
    assert owner is not None
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(0.05)", **_sleeper._spawn_kwargs()
    )
    record = ev.ProcessRecord(pid=proc.pid, started=1.0, pgid=None, proc=proc)

    def prepare() -> Any:
        return service.prepare_adopted(
            _sleeper.KIND,
            title="moved",
            runner=object(),  # type: ignore[arg-type] — never run: the door refuses first
            session=session,
            client_start={"v": 1},
            process=record,
        )

    assert prepare() is not None, "admitted while the owner is live"
    await service.owner_ended(owner, "owner_end")
    with pytest.raises(JobStartRefused, match="the session ended"):
        prepare()
    await service.bind(make_harness(session), reason="reload")
    assert prepare() is not None, "a new bind of the owner admits again"
    await proc.wait()
    await service.shutdown()


async def test_a_start_after_its_owner_ended_is_refused_at_entry_until_the_next_bind(tmp_path: Path) -> None:
    """#612 fix round 1, Codex C-H1: an ended binding admits no start, and writes nothing, until a new bind.

    On ``111e02d7`` the start read the already-raised epoch at entry, so its
    registration check passed and a job ran for the session that had ended.
    """

    service, session = await _bound(tmp_path)
    owner = service.owner
    assert owner is not None
    await service.owner_ended(owner, "owner_end")
    with pytest.raises(JobStartRefused) as refused:
        await service.start(_sleeper.KIND, title="late")
    assert str(refused.value) == jobs.SESSION_ENDED_FIRST
    assert service._jobs == {} and await _facts(session) == []  # noqa: SLF001
    assert service.admission.count(_sleeper.KIND) == 0
    with pytest.raises(JobStartRefused, match="the session ended"):
        await service.start(_sleeper.KIND, title="late", session=session)
    assert await _facts(session) == [], "the bound session named explicitly is refused alike"
    await service.bind(make_harness(session), reason="reload")
    job_id = await service.start(_sleeper.KIND, title="after the bind")
    assert service.snapshot(job_id) is not None
    await service.shutdown()


async def test_a_start_for_another_session_named_explicitly_is_not_refused_by_the_ended_binding(
    tmp_path: Path,
) -> None:
    """Fix round 2, F3 (the verify's mutant J5): the entry check is the bound session's only.

    The bound owner ended and no new bind came; a start that names ANOTHER session runs, under that
    session's owner, as it did before fix round 1. ``start`` refusing every session once the binding
    ended survived every test at ``510b186c``: none named another session.
    """

    service, session = await _bound(tmp_path)
    owner = service.owner
    assert owner is not None
    await service.owner_ended(owner, "owner_end")
    other = await make_session(tmp_path, name="other.jsonl", session_id="other")
    job_id = await service.start(_sleeper.KIND, title="for the other session", session=other)
    snapshot = service.snapshot(job_id, any_owner=True)
    assert snapshot is not None and snapshot.owner == "other" and snapshot.state == "running"
    assert [fact["fact"] for fact in await _facts(other)][0] == "start"
    assert await _facts(session) == [], "nothing was written to the ended session"
    with pytest.raises(JobStartRefused, match="the session ended"):
        await service.start(_sleeper.KIND, title="for the ended one")
    await service.shutdown()


async def test_a_session_shutdown_hook_after_the_jobs_own_cannot_start_a_job_for_the_ended_session(
    tmp_path: Path,
) -> None:
    """#612 fix round 1, Codex C-H1 (Codex r1a's RPC ``owner`` probe): a classic ``new_session``.

    The bundled jobs extension's ``session_shutdown`` ends the owner; a later
    extension's hook starts a sleeper through its jobs facade. On ``111e02d7`` the
    sleeper was still ``running`` under the old owner after the swap.
    """

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.runtime import create_agent_session_runtime
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionRepo,
        LocalFileSystem,
    )
    from aelix_coding_agent.extensions.api import _ExtensionRuntime
    from aelix_coding_agent.extensions.loader import load_extension_from_factory
    from aelix_coding_agent.jobs import activation
    from aelix_coding_agent.jobs.extension import JobsExtension
    from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

    from tests.rpc.test_runtime_control_lifecycle_594 import MODEL, Wire, _stream

    service = fast_service()
    activation.install_service(service)
    outcomes: list[object] = []

    def late(aelix: Any) -> None:
        facade = aelix.jobs
        facade.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)

        async def on_end(event: Any, _ctx: Any) -> None:
            if event.reason == "new":
                try:
                    outcomes.append(await facade.start(_sleeper.KIND, title="late", params={"seconds": 20}))
                except JobStartRefused as exc:
                    outcomes.append(exc)

        aelix.on("session_shutdown", on_end)

    async def build(session: Any, **_kwargs: Any) -> AgentHarness:
        runtime = _ExtensionRuntime()
        bridge = await load_extension_from_factory(JobsExtension(service), name="aelix-jobs", runtime=runtime)
        user = await load_extension_from_factory(late, name="late", runtime=runtime)
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL, stream_fn=_stream, session=session, runtime=runtime,
                cwd=str(tmp_path), notice_inbox=True, extensions=[bridge, user],
            )
        )

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    harness = await build(session)
    runtime = await create_agent_session_runtime(harness, build, repo=repo, fs=fs)
    await service.bind(harness)
    # As ``cli/entry.py`` wires it since #612 fix round 2: bound before ``session_start``.
    runtime.set_before_session_start(lambda rebuilt, reason: service.bind(rebuilt, reason=reason))
    before = service.owner
    wire = Wire()
    run = asyncio.create_task(
        run_rpc_mode(harness, runtime_host=runtime, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False)
    )
    try:
        wire.send({"type": "new_session", "id": "swap"})
        reply = await asyncio.wait_for(wire.response("swap"), 5)
        assert reply["success"] is True and service.owner != before
        assert len(outcomes) == 1, outcomes
        assert isinstance(outcomes[0], JobStartRefused), "a job started for the ended session"
        assert str(outcomes[0]) == jobs.SESSION_ENDED_FIRST
        assert [j for j in service._jobs.values() if j.owner == before] == []  # noqa: SLF001
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 5)
        await service.shutdown()
        activation.install_service(None)
