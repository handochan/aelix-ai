"""A job is its session's: no surface reads or stops another session's job by id (#722, Codex #5).

And the model's control is gated as any mutating tool's (verify S5): the
read-only ``job`` tool lists, reads and inspects; ``job_stop`` carries no
read-only marking, so the permission gate treats it as it treats any tool aelix
did not mark (ADR-0253) — with an approval UI it asks in ``default``,
``auto-accept-edits`` and ``auto``; ``plan`` blocks it everywhere; ``yolo`` and a
host with no approval UI (``-p``, json, RPC without a host approval hook) allow
it, as they allow ``bash``. On ``a80e81fa`` an exact id of session A's job, asked
from session B, returned A's output and detail through ``job(action="result")``
and ``status``, and ``job(action="stop")`` was marked read-only — allowed
silently in every posture, plan included.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_agent_core.types import AgentContext, AgentTool
from aelix_coding_agent.builtin.permission import PermissionExtension, evaluate_tool_call
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.jobs.commands import JobsCommand
from aelix_coding_agent.jobs.service import JobNotFound
from aelix_coding_agent.jobs.tool import create_job_stop_tool, create_job_tool
from aelix_coding_agent.tools.provenance import builtin_provenance

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session


async def test_an_id_of_another_sessions_job_reads_and_stops_nothing(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    a = make_harness(await make_session(tmp_path, "a.jsonl", session_id="A"))
    await service.bind(a)
    job_a = await service.start(_sleeper.KIND, title="private A job", params={"seconds": 30})

    b = make_harness(await make_session(tmp_path, "b.jsonl", session_id="B"))
    await service.bind(b)
    assert service.owner == "B"
    assert service.snapshot(job_a) is None
    assert service.snapshot(job_a, any_owner=True) is not None, "the service's own view"
    assert service.snapshots() == []
    assert service.output(job_a) == f"no job {job_a}"

    tool = create_job_tool(service)
    for action in ("status", "result"):
        result = await tool.execute({"action": action, "id": job_a}, None)
        assert result.is_error and "no job" in result.content[0].text, action
    stop = await create_job_stop_tool(service).execute({"id": job_a}, None)
    assert stop.is_error and "no job" in stop.content[0].text
    command = JobsCommand(service)
    for words in (job_a, f"result {job_a}", f"stop {job_a}"):
        assert "no job" in await command(words), words
    try:
        await service.stop(job_a)
    except JobNotFound:
        pass
    else:
        raise AssertionError("stop by id crossed sessions")
    try:
        service.request_stop(job_a)
    except JobNotFound:
        pass
    else:
        raise AssertionError("request_stop by id crossed sessions")
    assert service.snapshot(job_a, any_owner=True).state == "running"

    await service.bind(a)
    assert service.snapshot(job_a) is not None
    await service.stop(job_a)
    await service.shutdown()


def test_job_reads_without_asking_and_job_stop_is_gated_like_any_mutating_tool(tmp_path: Path) -> None:
    service = fast_service()
    read, stop = create_job_tool(service), create_job_stop_tool(service)
    assert builtin_provenance(read) == "read_only"
    assert builtin_provenance(stop) is None
    cwd = str(tmp_path)
    for mode in PermissionMode:
        assert evaluate_tool_call(mode, builtin_provenance(read), {"action": "list"}, cwd) == "allow"
    assert evaluate_tool_call(PermissionMode.PLAN, builtin_provenance(stop), {"id": "x"}, cwd) == "deny"
    assert evaluate_tool_call(PermissionMode.DEFAULT, builtin_provenance(stop), {"id": "x"}, cwd) == "ask"
    assert "stop" not in read.parameters["properties"]["action"]["enum"]


async def test_job_stop_meets_the_gate_any_unmarked_tool_meets_in_every_posture_and_host() -> None:
    """Verify SF1: the whole posture matrix, through the gate's own ``tool_call`` handler.

    ``job_stop`` is unmarked, so it lands exactly where an extension's tool
    lands (ADR-0253) and, outside ``auto``'s bash classifier, where aelix's own
    ``bash`` lands. "asked" is a call the approval runner saw (here it declines).
    ADR-0293 §10 said "never auto-allowed"; ``yolo`` and a host with no approval
    UI do allow it.
    """

    from aelix_coding_agent.tui.approval_dialog import ApprovalDecision

    from tests.builtin.gate_tools import BUILTIN_TOOLS

    service = fast_service()
    stop = create_job_stop_tool(service)

    async def _noop(_args: Any, _ctx: Any = None) -> Any:
        return None

    other = AgentTool(name="ext_tool", description="", parameters={}, execute=_noop)
    tools = [*BUILTIN_TOOLS.values(), create_job_tool(service), stop, other]

    async def verdict(tool_name: str, args: dict[str, Any], mode: PermissionMode, host: str) -> str:
        asked: list[str] = []

        async def runner(request: Any) -> ApprovalDecision:
            asked.append(request.tool_name)
            return ApprovalDecision.NO

        gate = PermissionExtension(
            posture=PermissionPosture(mode=mode),
            approval_runner=runner,
            headless_default="block" if host == "child" else "allow",
        )
        ui = SimpleNamespace(provides_tool_approval=host != "rpc-ui")
        ctx = SimpleNamespace(has_ui=host in ("tui", "rpc-ui"), cwd="/proj", ui=ui)
        event = ToolCallHookEvent(
            tool_call_id="c1", tool_name=tool_name, args=args, context=AgentContext(tools=tools)
        )
        result = await gate._on_tool_call(event, ctx)  # type: ignore[arg-type]
        if asked:
            return "ask"
        return "allow" if result is None else "block"

    expected = {
        # An approval UI: the TUI.
        "tui": {"default": "ask", "auto-accept-edits": "ask", "auto": "ask", "plan": "block", "yolo": "allow"},
        # No approval UI: -p, json, and RPC with no host hook; an RPC command dialog is not one (#594).
        "headless": {"default": "allow", "auto-accept-edits": "allow", "auto": "allow", "plan": "block", "yolo": "allow"},
        "rpc-ui": {"default": "allow", "auto-accept-edits": "allow", "auto": "allow", "plan": "block", "yolo": "allow"},
        # A delegated child (ADR-0197 §(e)).
        "child": {"default": "block", "auto-accept-edits": "block", "auto": "block", "plan": "block", "yolo": "allow"},
    }
    seen = {
        host: {mode.value: await verdict("job_stop", {"id": "x"}, mode, host) for mode in PermissionMode}
        for host in expected
    }
    assert seen == expected
    for host in expected:
        for mode in PermissionMode:
            assert await verdict("ext_tool", {"id": "x"}, mode, host) == seen[host][mode.value], (host, mode)
            if mode != PermissionMode.AUTO:  # auto reads bash through its classifier
                assert (
                    await verdict("bash", {"command": "echo hi"}, mode, host) == seen[host][mode.value]
                ), (host, mode)
            assert await verdict("job", {"action": "list"}, mode, host) == "allow", (host, mode)


async def test_a_notice_waits_for_its_own_session(tmp_path: Path) -> None:
    """Verify V15: a job of session A that ends while B is current leaves its
    notice for A — B's harness never gets it; binding A again posts it."""

    from tests.jobs.conftest import wait_for

    service = fast_service()
    _sleeper.register(service)
    a = make_harness(await make_session(tmp_path, "a.jsonl", session_id="A"))
    await service.bind(a)
    job_a = await service.start(_sleeper.KIND, title="A's nap", params={"seconds": 0.2})
    b = make_harness(await make_session(tmp_path, "b.jsonl", session_id="B"))
    await service.bind(b)
    await wait_for(lambda: service.snapshot(job_a, any_owner=True).state == "done")
    assert b.pending_notices == 0
    a2 = make_harness(await make_session(tmp_path, "a2.jsonl", session_id="A"))
    await service.bind(a2)
    assert a2.pending_notices == 1
    await service.shutdown()
