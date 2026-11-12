"""#769 — a background agent the job service no longer keeps whole reads as before, from its result file.

What the service keeps of it is read back from the parent's records: the
job's facts, the agent's settle and its result-file record
(``DelegationKindAdapter.decode``). Its whole result text is in none of those —
the notice carries 12 KiB of it, and the child's file only the agent's own last
answer — so the run writes it to a 0600 file beside its child session (the
owner's choice of 2026-11-12, ``aelix_agents/result_file.py``), and once the
job is let go and its runner freed, ``job(action="result")``, ``/jobs result``
and ``agent(action="result")`` read it from there. The REAL ``AgentsExtension``,
job service and ``PrintChannel`` over a stub child
(``test_agent_background_611``'s rig).

Fails on ``5390b254``: no result file is written, and the delegation client
keeps every agent's text in ``DelegationJobs.info`` for the process's life.
"""

from __future__ import annotations

import gc
import os
import re
import stat
import weakref
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.child_session import place_children
from aelix_coding_agent.jobs.commands import JobsCommand
from aelix_coding_agent.jobs.tool import create_job_tool

from tests.agents_ext.test_agent_background_611 import (
    REAL_CHILD,
    _rig,
    _script,
    _settled,
    _spawned_id,
    _text,
    jobs_on,  # noqa: F401 — the fixture
)
from tests.agents_ext.test_child_recovery_611 import _restart
from tests.jobs.conftest import wait_for
from tests.jobs.test_retention_769 import _reads, _trimmed

pytestmark = [REAL_CHILD, pytest.mark.usefixtures("jobs_on")]

# ~45 KB of three- and four-byte characters: several pages on every surface.
ANSWER = "".join(f"줄 {i:05d} of a long answer 🙂\n" for i in range(1500))


async def _agent_reads(rig: Any, agent_id: str, job_id: str) -> dict[str, str]:
    """What ``agent()`` says of one agent, and every generic surface's text."""

    async def call(**args: Any) -> str:
        result = await rig.call(args)
        return f"{result.is_error}|{_text(result)}"

    reads = {
        "agent status": await call(action="status", id=agent_id),
        "agent status (job id)": await call(action="status", id=job_id),
        "agent status (all)": await call(action="status"),
        "agent stop": await call(action="stop", id=agent_id),
        "agent wait": await call(action="wait", id=agent_id),
    }
    return {**reads, **await _reads(rig.service, job_id)}


_AGENT_MORE = re.compile(r"\n\n… more: agent\(action=\"result\", id=\"[^\"]+\", offset=(\d+)\)$")
_JOB_MORE = re.compile(r"\n… more: job\(action=\"result\", id=\"[0-9a-f]+\", offset=(\d+)\)$")
_JOBS_MORE = re.compile(r"\n… more: /jobs result \S+ (\d+)$")


async def _pages(read: Any, more: re.Pattern[str]) -> list[str]:
    """Every page from offset 0, following each page's hint."""

    pages: list[str] = []
    offset: int | None = 0
    while offset is not None:
        assert len(pages) < 40
        text = await read(offset)
        found = more.search(text)
        pages.append(text if found is None else text[: found.start()])
        offset = int(found.group(1)) if found is not None else None
    return pages


async def _results(rig: Any, agent_id: str, job_id: str) -> dict[str, list[str]]:
    """The whole result, page by page, on the three surfaces that read it."""

    tool = create_job_tool(rig.service)
    jobs = JobsCommand(rig.service)

    async def agent(offset: int) -> str:
        return _text(await rig.call({"action": "result", "id": agent_id, "offset": offset}))

    async def job(offset: int) -> str:
        result = await tool.execute({"action": "result", "id": job_id, "offset": offset})
        return result.content[0].text  # type: ignore[union-attr]

    async def slash(offset: int) -> str:
        return await jobs(f"result {job_id} {offset}")

    return {
        "agent(action=result)": await _pages(agent, _AGENT_MORE),
        "job(action=result)": await _pages(job, _JOB_MORE),
        "/jobs result": await _pages(slash, _JOBS_MORE),
    }


def _result_path(rig: Any, job_id: str) -> Path:
    placement = place_children(rig.session.session_file)
    assert placement is not None
    key = rig.ext._delegation_jobs.info[job_id].key
    return Path(placement.directory) / f"{key}.result"


async def _ended_agent(tmp_path: Path) -> tuple[Any, str, str]:
    rig = await _rig(tmp_path, script=_script(ANSWER))
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    snap = await _settled(rig)
    await rig.harness.prompt_notices()
    await wait_for(lambda: rig.service.snapshot(snap.job_id).notice_state == "delivered")  # type: ignore[union-attr]
    return rig, agent_id, snap.job_id


async def _let_go(rig: Any, job_id: str) -> None:
    rig.service._settled_kept = 0
    rig.service._trim_later()
    await _trimmed(rig.service)
    assert job_id in rig.service._settled and job_id not in rig.service._jobs


async def test_an_agent_let_go_reads_as_before_from_its_file_and_holds_no_text(tmp_path: Path) -> None:
    """The run's file is 0600 beside its child session and holds its whole result; once the job
    is let go the runner — and the text with it — is freed, and all three result surfaces read
    every page byte for byte as before, as do status, stop, wait and the generic surfaces.

    Fails on ``5390b254``: no ``<run key>.result`` file exists.
    """

    rig, agent_id, job_id = await _ended_agent(tmp_path)
    try:
        path = _result_path(rig, job_id)
        assert path.is_file(), "written beside the child session file"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        held = rig.service._jobs[job_id]
        runner = weakref.ref(held.runner)
        del held
        before = await _results(rig, agent_id, job_id)
        whole = path.read_text(encoding="utf-8")
        assert "".join(before["agent(action=result)"]) == whole
        assert "줄 01499 of a long answer" in whole
        assert all(len(pages) >= 2 for pages in before.values())
        reads_before = await _agent_reads(rig, agent_id, job_id)
        await _let_go(rig, job_id)
        gc.collect()
        assert runner() is None, "the runner, and its text, are freed"
        assert rig.ext._delegation_jobs.info[job_id].text is None
        assert await _results(rig, agent_id, job_id) == before
        assert await _agent_reads(rig, agent_id, job_id) == reads_before
    finally:
        await rig.service.shutdown()


async def test_a_missing_result_file_is_said_and_never_replaced_by_the_childs_answer(
    tmp_path: Path,
) -> None:
    """Once the job is let go its file is the only whole result: gone, every surface says so.

    Fails on ``5390b254``: the text stayed in ``DelegationJobs.info`` and no
    file existed to be missing.
    """

    rig, agent_id, job_id = await _ended_agent(tmp_path)
    try:
        path = _result_path(rig, job_id)
        await _let_go(rig, job_id)
        gc.collect()
        path.unlink()
        said = "Its result cannot be read: its result file no longer exists."
        agent = await rig.call({"action": "result", "id": agent_id})
        assert agent.is_error and _text(agent) == said
        tool = await create_job_tool(rig.service).execute({"action": "result", "id": job_id})
        assert tool.content[0].text == said  # type: ignore[union-attr]
        assert await JobsCommand(rig.service)(f"result {job_id}") == said
    finally:
        await rig.service.shutdown()


async def test_after_a_restart_result_reads_the_runs_file(tmp_path: Path) -> None:
    """A restart's ``result`` of an agent that settled reads its whole result from its file —
    not the child's last answer under "after a restart".

    Fails on ``5390b254``: no file; ``result`` read the child file's last answer.
    """

    from aelix_agents.jobs_client import result_text

    rig, agent_id, job_id = await _ended_agent(tmp_path)
    try:
        first_page = _text(await rig.call({"action": "result", "id": agent_id, "offset": 0}))
    finally:
        await rig.service.shutdown()
    service, jobs, _harness = await _restart(rig.session)
    try:
        text, failed = await result_text(service, jobs, job_id, offset=0, session=rig.session)
        assert not failed and text == first_page
        assert "after a restart" not in text and text.startswith("줄 00000 of a long answer")
        # ``job(action="result")`` and ``/jobs result`` read it too (verify-r2 F5).
        tool = await create_job_tool(service).execute({"action": "result", "id": job_id})
        assert tool.content[0].text.startswith("줄 00000 of a long answer")  # type: ignore[union-attr]
        assert (await JobsCommand(service)(f"result {job_id}")).startswith("줄 00000 of a long answer")
    finally:
        await service.shutdown()


async def test_a_run_whose_result_file_could_not_be_written_is_kept_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file, so no read back could match: the job service keeps it whole.

    Fails on ``5390b254``: it is let go (its text stayed in the client's map).
    """

    from aelix_agents import jobs_client

    monkeypatch.setattr(jobs_client, "write_result", lambda *_a: None)
    rig, agent_id, job_id = await _ended_agent(tmp_path)
    try:
        assert not _result_path(rig, job_id).exists()
        rig.service._settled_kept = 0
        rig.service._trim_later()
        await _trimmed(rig.service)
        assert job_id in rig.service._jobs and job_id not in rig.service._settled
        assert "줄 00000 of a long answer" in _text(await rig.call({"action": "result", "id": agent_id}))
    finally:
        await rig.service.shutdown()


async def test_job_result_of_an_unresolved_agent_reads_its_runners_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its run returned and its cleanup failed (``unresolved(cleanup_error)``): it has no outcome
    yet, so ``job(action="result")`` reads the text its runner holds (``AgentJob.text``).

    A guard on ``5390b254`` (the client's map held the text there).
    """

    from aelix_agents.jobs_client import DelegationRunner

    async def broken(self: Any, receipt: Any) -> Any:
        raise RuntimeError("cleanup broke")

    monkeypatch.setattr(DelegationRunner, "helper_cleanup", broken)
    rig = await _rig(tmp_path, script=_script(ANSWER))
    try:
        _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
        await wait_for(lambda: any(s.state == "unresolved" for s in rig.jobs()))
        (snap,) = rig.jobs()
        tool = await create_job_tool(rig.service).execute({"action": "result", "id": snap.job_id})
        assert tool.content[0].text.startswith("줄 00000 of a long answer")  # type: ignore[union-attr]
    finally:
        monkeypatch.undo()
        await rig.service.shutdown()


# === fix round 2 (verify-r2 F3, F7) =========================================================


async def test_the_clients_entry_of_an_agent_let_go_is_small(tmp_path: Path) -> None:
    """Verify-r2 F3: ``DelegationJobs.info`` keeps one entry per agent and never removes it, so
    an entry holds no task and no text: here a 20,000-character task leaves under 1,000.

    Fails on ``c06128c8``: each entry kept its 20,000-character ``task`` (verifier's ``test_p7``).
    """

    import dataclasses

    rig = await _rig(tmp_path, script=_script("short answer"))
    try:
        for _ in range(3):
            _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t" * 20_000}))
            await _settled(rig)
            await rig.harness.prompt_notices()
        await wait_for(lambda: all(s.notice_state == "delivered" for s in rig.jobs()))
        rig.service._settled_kept = 0
        rig.service._trim_later()
        await _trimmed(rig.service)
        assert len(rig.service._settled) == 3
        gc.collect()
        info = rig.ext._delegation_jobs.info
        assert len(info) == 3, "one entry per agent, never removed"
        for entry in info.values():
            kept = {f.name: getattr(entry, f.name) for f in dataclasses.fields(entry)}
            assert sum(len(str(v)) for k, v in kept.items() if k != "runner") < 1_000, kept.keys()
            assert entry.text is None
    finally:
        await rig.service.shutdown()


async def test_result_reads_an_agent_let_go_from_its_file_before_any_collection(tmp_path: Path) -> None:
    """Verify-r2 F7: until a collection frees its runner, ``agent(action="result")`` read the
    runner's text while ``job(action="result")`` read the file. Both read the file now: with
    collection off and the file removed, both say it is gone.

    Fails on ``c06128c8``: ``agent(action="result")`` returned the runner's text.
    """

    rig, agent_id, job_id = await _ended_agent(tmp_path)
    gc.disable()
    try:
        path = _result_path(rig, job_id)
        await _let_go(rig, job_id)
        path.unlink()
        said = "Its result cannot be read: its result file no longer exists."
        assert _text(await rig.call({"action": "result", "id": agent_id})) == said
        tool = await create_job_tool(rig.service).execute({"action": "result", "id": job_id})
        assert tool.content[0].text == said  # type: ignore[union-attr]
    finally:
        gc.enable()
        await rig.service.shutdown()
