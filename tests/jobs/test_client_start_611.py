"""#611 S2d's kind-agnostic additions to the job service: ``client_start``, ``restored_note``, ``next_offset`` (ADR-0293's note).

Run with the synthetic kind only (``tests/jobs/_sleeper.py``), so what these
pin is the service's, whatever kind uses it (Codex #12, #13).

Fails on ``71f837eb``: ``JobService.start`` takes no ``client_start`` (a
``TypeError``), ``JobSnapshot`` has no ``client_start``, the service never asks
a kind for a restored notice's line, and ``OutputSlice`` has no
``next_offset``, so ``job(action="result")`` always hints ``offset +
len(text)``. ``test_delegation_pages_by_bytes`` was a guard green on both
until #770 made delegation's pager set ``next_offset`` too.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import jobs_client
from aelix_ai.utils._process_identity import process_started
from aelix_coding_agent.jobs import activation, commands, evidence
from aelix_coding_agent.jobs.contract import JobSnapshot, OutputSlice
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE, append_fact, make_fact
from aelix_coding_agent.jobs.tool import create_job_tool

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, reopen, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


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


def _process(job_id: str, pid: int, started: float | None) -> dict[str, Any]:
    return make_fact(
        "process",
        job_id,
        seq=1,
        evidence={"pid": pid, "started": started, "host": evidence.this_host()},
    )


async def _restart(tmp_path: Path, *facts: dict[str, Any], **adapter: Any) -> tuple[Any, Any]:
    session = await make_session(tmp_path)
    for fact in facts:
        await append_fact(session, fact)
    service = fast_service()
    _sleeper.register(service, **adapter)
    harness = make_harness(await reopen(tmp_path))
    await service.bind(harness)
    return service, harness


async def test_client_start_is_in_the_start_fact_and_on_every_snapshot(tmp_path: Path) -> None:
    session = await make_session(tmp_path)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(make_harness(session))
    job_id = await service.start(
        _sleeper.KIND,
        title="nap",
        params={"seconds": 0.05},
        client_start={"v": 1, "output": str(tmp_path / "out.log")},
    )

    starts = [
        e.data
        for e in await session.get_entries()
        if getattr(e, "custom_type", None) == JOB_FACT_TYPE and e.data.get("fact") == "start"
    ]
    assert [s["client"] for s in starts] == [{"v": 1, "output": str(tmp_path / "out.log")}]
    snap = service.snapshot(job_id)
    assert snap is not None and snap.client_start == {"v": 1, "output": str(tmp_path / "out.log")}
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    assert service.snapshot(job_id).client_start["output"] == str(tmp_path / "out.log")  # type: ignore[union-attr]
    await service.shutdown()

    # A restart reads it back from the fact as recorded, not derived again.
    later = fast_service()
    _sleeper.register(later)
    await later.bind(make_harness(await reopen(tmp_path)))
    restored = later.snapshot(job_id)
    assert restored is not None and restored.client_start == {"v": 1, "output": str(tmp_path / "out.log")}
    await later.shutdown()


async def test_the_extension_facade_writes_client_start_too(tmp_path: Path) -> None:
    """verify F4: ``ExtensionAPI.jobs.start(client_start=…)`` reaches the start fact and the snapshot."""

    from aelix_coding_agent.extensions.loader import load_extension_from_factory

    session = await make_session(tmp_path)
    service = fast_service()
    activation.install_service(service)
    try:
        await service.bind(make_harness(session))
        facades: dict[str, Any] = {}

        def ext(aelix: Any) -> None:
            facades["jobs"] = aelix.jobs
            facades["jobs"].register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)

        await load_extension_from_factory(ext, name="ext-a")
        job_id = await facades["jobs"].start(
            _sleeper.KIND, title="nap", params={"seconds": 0.01}, client_start={"output": "/abs/x.log"}
        )
        assert service.snapshot(job_id).client_start == {"output": "/abs/x.log"}  # type: ignore[union-attr]
        starts = [
            e.data
            for e in await session.get_entries()
            if getattr(e, "custom_type", None) == JOB_FACT_TYPE and e.data.get("fact") == "start"
        ]
        assert starts[0]["client"] == {"output": "/abs/x.log"}
    finally:
        await service.shutdown()
        activation.install_service(None)


async def test_without_client_start_the_fact_has_none_and_the_snapshot_is_empty(tmp_path: Path) -> None:
    """Delegation and every existing kind start as before: no ``client`` key at all."""

    session = await make_session(tmp_path)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(make_harness(session))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    starts = [
        e.data
        for e in await session.get_entries()
        if getattr(e, "custom_type", None) == JOB_FACT_TYPE and e.data.get("fact") == "start"
    ]
    assert "client" not in starts[0]
    assert service.snapshot(job_id).client_start == {}  # type: ignore[union-attr]
    await service.shutdown()


async def test_a_restored_lost_notice_carries_the_kinds_line(tmp_path: Path) -> None:
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    started = process_started(child.pid)
    await child.wait()
    service, harness = await _restart(
        tmp_path,
        _start("j", client={"output": "/abs/out.log"}),
        _process("j", child.pid, started),
        note="Its output file: {output}",
    )
    assert service.snapshot("j").state == "lost"  # type: ignore[union-attr]
    (notice,) = harness.undelivered_notices()
    assert "Its output file: /abs/out.log" in notice.body
    await service.shutdown()


async def test_a_restored_unresolved_notice_carries_the_kinds_line(tmp_path: Path) -> None:
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    try:
        service, harness = await _restart(
            tmp_path,
            _start("j", client={"output": "/abs/out.log"}),
            _process("j", child.pid, process_started(child.pid)),
            note="Its output file: {output}",
        )
        assert service.snapshot("j").state == "unresolved"  # type: ignore[union-attr]
        (notice,) = harness.undelivered_notices()
        assert notice.notice_id == "j:unresolved"
        assert "Its output file: /abs/out.log" in notice.body
        assert child.returncode is None, "a restart never signals"
        await service.shutdown()
    finally:
        child.kill()
        await child.wait()


@pytest.mark.parametrize("answer", ["raise", None])
async def test_a_kind_with_no_line_or_a_raising_one_adds_nothing(tmp_path: Path, answer: str | None) -> None:
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    started = process_started(child.pid)
    await child.wait()
    service, harness = await _restart(
        tmp_path,
        _start("j", client={"output": "/abs/out.log"}),
        _process("j", child.pid, started),
        fail=frozenset({"restored_note"}) if answer == "raise" else frozenset(),
    )
    (notice,) = harness.undelivered_notices()
    assert "/abs/out.log" not in notice.body
    assert notice.body.count("</background-job-result>") == 1, "the frame is whole"
    await service.shutdown()


class _BytePager(_sleeper.SleeperAdapter):
    """A kind that pages by bytes and says where the next page starts."""

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        return OutputSlice(text="가", offset=offset, total=9, more=True, next_offset=offset + 3)


async def test_next_offset_is_what_the_job_tool_and_jobs_result_hint(tmp_path: Path) -> None:
    service = fast_service()
    service.register_kind(_BytePager(), _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    tool = create_job_tool(service)

    result = await tool.execute({"action": "result", "id": job_id, "offset": 3})
    text = result.content[0].text  # type: ignore[union-attr]
    assert 'offset=6)' in text, text
    snap = service.snapshot(job_id)
    assert snap is not None
    assert commands.result_text(service, snap, 3).endswith(" 6")
    await service.shutdown()


class _DelegationPager(_sleeper.SleeperAdapter):
    """``output`` as delegation's adapter pages its result text (``jobs_client.page``)."""

    def output(self, job: JobSnapshot, *, offset: int, limit: int) -> OutputSlice | None:
        return jobs_client.page("가" * 20_000, offset=offset, limit=limit)


async def test_delegation_pages_by_bytes(tmp_path: Path) -> None:
    """Delegation's page sets ``next_offset`` as ``bash``'s does (#770, ADR-0297 §4).

    Its text is 60,000 bytes of three-byte characters: the first 16 KiB page
    holds 5,461 of them, 16,383 bytes, and the hint is that byte count. Until
    #770 this was the guard ``test_delegation_paging_is_unchanged``, which
    pinned the hint at 5,461 — characters counted over a byte pager, so the
    second page began 10,922 bytes too early and read them again. Fails on
    ``363accf2``: the hint is ``offset=5461``.
    """

    service = fast_service()
    service.register_kind(_DelegationPager(), _sleeper.Sleeper)
    await service.bind(make_harness(await make_session(tmp_path)))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    result = await create_job_tool(service).execute({"action": "result", "id": job_id})
    text = result.content[0].text  # type: ignore[union-attr]
    body, _, hint = text.rpartition("\n")
    assert body == "가" * 5461
    assert hint == f'… more: job(action="result", id="{job_id}", offset=16383)'
    await service.shutdown()
