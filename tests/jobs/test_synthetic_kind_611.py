"""The gate's positive half: a third, synthetic kind goes through every surface (#722, ADR-0293).

``test:sleeper`` is registered by an ordinary extension through
``ExtensionAPI.jobs``; the product-core bridge extension (``aelix-jobs``) is
loaded beside it, as the CLI loads it while jobs are active. It goes through:
start (a tool the model calls), background run, notice, ``/jobs``, the
indicator, the ``job`` tool, stop, the session's end (``/new``) and a
``/reload`` (both through ``session_shutdown``), the ``-p`` drain, a restart's
classification, its reservation, a re-registration and a missing adapter. It
imports no delegation and no ``bash`` module (the negative half,
``test_core_is_kind_agnostic_611.py``, proves the core never names it).

Fails on main ``a385c64a``: ``ExtensionAPI`` has no ``jobs``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.runtime.agent_session_runtime import _emit_session_shutdown_event
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.streaming import AssistantEndEvent, Model
from aelix_ai.tools import ToolResult
from aelix_coding_agent.extensions.api import _ExtensionRuntime
from aelix_coding_agent.extensions.loader import load_extension_from_factory
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.activation import JobsInactive
from aelix_coding_agent.jobs.extension import JobsExtension
from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.modes.print_mode import run_print_mode

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_session, reopen, wait_for, wait_settled


class _Model:
    """Calls ``nap`` once per user message, then answers; records what it saw."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.seen: list[list[str]] = []

    async def __call__(self, _model: Any, context: Any, _options: Any = None) -> AsyncIterator[Any]:
        texts = [b.text for m in context.messages for b in (getattr(m, "content", []) or []) if isinstance(b, TextContent)]
        self.seen.append(texts)
        last = context.messages[-1]
        if getattr(last, "role", "") == "user" and texts and texts[-1].startswith("nap"):
            call = ToolCallContent(tool_call_id=f"c{len(self.seen)}", tool_name="nap", input={"seconds": self.seconds})
            yield AssistantEndEvent(message=AssistantMessage(content=[call], stop_reason="tool_use"))
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="noted")]))


def _sleeper_extension(started: list[str]) -> Any:
    def factory(aelix: Any) -> None:
        jobs = aelix.jobs
        jobs.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)

        async def nap(args: dict[str, Any], _ctx: Any) -> ToolResult:
            job_id = await jobs.start(_sleeper.KIND, title="nap", params={"seconds": args.get("seconds", 0.05)})
            started.append(job_id)
            return ToolResult(content=[TextContent(text=f"started job {job_id}")])

        aelix.register_tool(AgentTool(name="nap", description="sleep in the background", parameters={"type": "object", "properties": {}}, execute=nap))

    return factory


async def _build(tmp_path: Path, service: Any, model: _Model, started: list[str], *, session: Any = None) -> AgentHarness:
    runtime = _ExtensionRuntime()
    bridge = await load_extension_from_factory(JobsExtension(service), name="aelix-jobs", runtime=runtime)
    user = await load_extension_from_factory(_sleeper_extension(started), name="sleeper-ext", runtime=runtime)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p"),
            stream_fn=model,
            session=session if session is not None else await make_session(tmp_path),
            extensions=[bridge, user],
            runtime=runtime,
            notice_inbox=True,
        )
    )
    await harness.bootstrap()
    await service.bind(harness)
    return harness


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(activation, "ACTIVE", True)
    _sleeper.Sleeper.instances.clear()
    svc = fast_service()
    activation.install_service(svc)
    yield svc
    activation.install_service(None)


def test_the_shipped_gate_is_off() -> None:
    """Off on main until #611 S3 (ADR-0293 §12): this line is what S3 changes."""

    import ast
    import inspect

    tree = ast.parse(inspect.getsource(activation))
    shipped = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and [getattr(t, "id", None) for t in node.targets] == ["ACTIVE"]
        and isinstance(node.value, ast.Constant)
    ]
    assert shipped == [False]


async def test_the_gate_off_refuses_the_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", False)
    activation.install_service(fast_service())  # even with a service built, off is off
    try:
        with pytest.raises(AttributeError, match="has no attribute 'jobs'"):
            await load_extension_from_factory(_sleeper_extension([]), name="x")
        with pytest.raises(JobsInactive):
            activation.current_service()
        with pytest.raises(JobsInactive):
            activation.require_active()
    finally:
        activation.install_service(None)


async def test_a_synthetic_kind_through_every_surface(tmp_path: Path, service: Any) -> None:
    started: list[str] = []
    model = _Model(seconds=0.1)
    harness = await _build(tmp_path, service, model, started)

    # start, background, notice — through a tool the model called
    await harness.prompt("nap please")
    (first,) = started
    assert service.snapshot(first).kind == _sleeper.KIND
    await wait_for(lambda: harness.pending_notices == 1)
    assert indicator_text(service.snapshots()) == "⚙ 1 result waiting"

    # /jobs and the job tool (registered by the bridge), the same table
    jobs_cmd = next(ext.commands["jobs"].handler for ext in harness._extensions if "jobs" in ext.commands)
    listing = await jobs_cmd("", None)
    assert first[:6] + "…" in listing and "done ◆" in listing
    job_tool = next(t for t in harness.state.tools if t.name == "job")
    tool_list = await job_tool.execute({"action": "list"}, None)
    assert tool_list.content[0].text.splitlines() == listing.splitlines()[:-1]

    # the notice turn
    result = await harness.prompt_notices()
    assert result.started and any("sleeper said" in t for t in model.seen[-1])
    assert indicator_text(service.snapshots()) is None

    # stop, from /jobs
    model.seconds = 30
    await harness.prompt("nap long")
    long_id = started[-1]
    since = time.monotonic()
    assert "Stopped" in await jobs_cmd(f"stop {long_id}", None)
    await wait_settled(service, (long_id,), since=since)  # #751

    # the session ends (/new): its running jobs stop through session_shutdown
    await harness.prompt("nap again")
    third = started[-1]
    since = time.monotonic()
    await _emit_session_shutdown_event(harness.extension_runner, "new")
    await wait_settled(service, (third,), since=since)  # #751

    await service.shutdown()


async def test_reservation_reload_restart_reregistration_and_missing_adapter(tmp_path: Path, service: Any) -> None:
    started: list[str] = []
    model = _Model(seconds=30)
    harness = await _build(tmp_path, service, model, started)
    # Registered again by the extension that owns the kind (another source is refused).
    _sleeper.register(service, bound=1, source="sleeper-ext")
    await harness.prompt("nap")
    assert len(started) == 1
    from aelix_coding_agent.jobs.service import JobStartRefused

    with pytest.raises(JobStartRefused, match="backgroundJobs.limits.sleeper"):
        await service.start(_sleeper.KIND, title="second")

    # /reload: session_shutdown("reload") stops the session's jobs; the slot comes back
    since = time.monotonic()
    await _emit_session_shutdown_event(harness.extension_runner, "reload")
    await wait_settled(service, (started[0],), since=since, slots=(_sleeper.KIND, 0))  # #751

    # a job left unfinished in the file: a restart with no adapter keeps it
    # unresolved, and the kind's registration re-evaluates it
    from aelix_coding_agent.jobs.records import append_fact, make_fact

    session = harness.session
    await append_fact(session, make_fact("start", "orphan", kind=_sleeper.KIND, kind_version=1, owner=service.owner, mode="background", notify=True, title="orphan", created=1.0, settings_key="sleeper"))
    await session.append_custom_entry("test.sleeper", {"job_id": "orphan", "status": "done"})
    await service.shutdown()

    fresh = fast_service()
    activation.install_service(fresh)
    h2 = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p"),
            stream_fn=_Model(0.01),
            session=await reopen(tmp_path),
            notice_inbox=True,
        )
    )
    await fresh.bind(h2)
    assert fresh.snapshot("orphan").unresolved_reason == "adapter_missing"
    fresh.register_kind(_sleeper.SleeperAdapter(), _sleeper.Sleeper)
    await wait_for(lambda: fresh.snapshot("orphan").state == "done")
    assert h2.pending_notices >= 1


async def test_the_print_drain_with_a_synthetic_kind(tmp_path: Path, service: Any) -> None:
    started: list[str] = []
    model = _Model(seconds=0.2)
    harness = await _build(tmp_path, service, model, started)

    async def same(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(harness, same, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="nap", jobs=service)
    assert code == 0
    assert service.snapshot(started[0]).state == "done"
    assert any("sleeper said" in t for t in model.seen[-1]), "-p delivered the notice before exiting"
    await asyncio.sleep(0)
