"""A consumed final steer ends the ``-p`` run, whatever the child still has running (#123 fix round 1, F5).

``8d7ee999`` ended the kernel run after the wrap-up's tool-less call and then
went on as any ``-p`` run does: it waited in the drain for the child's own
background job and ran a notice turn for it — tool-less too, since the latch
was never lowered (measured on a real child: the wrap-up's call was request 3,
the child waited out its 5 s job, request 4 was the notice turn, exit at
6.38 s; a job that never ends keeps a wrapped-up child alive). Here the run
ends once a final was consumed: the drain is not entered, no prompt and no
notice turn starts, the dispose stops the child's own jobs and the exit code is
the run's own.

The in-process test runs on every platform; the real child is POSIX.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core import AgentTool
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model
from aelix_ai.tools import ToolResult
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.modes import print_mode
from aelix_coding_agent.modes.print_inbox import CommandInbox
from aelix_coding_agent.modes.print_mode import run_print_mode

from tests.cli.test_command_inbox_123 import (
    _child_setup,
    _ModelServer,
    _text_turn,
    _tool_turn,
    posix_only,
)
from tests.cli.test_inbox_eof_123 import _alive, _argv, _kill, _read_pid, _until
from tests.jobs import _sleeper
from tests.jobs.conftest import (
    _reap_sleepers,  # noqa: F401 — autouse: reap the sleeper jobs
    fast_service,
    make_session,
    wait_for,
)

_JOB_SECONDS = 40
_EXIT_BOUND = 20.0


async def test_a_consumed_final_ends_the_run_without_the_drain_or_a_notice_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child has a tracked job of its own; the parent's final steer is read; the run ends.

    Fails on ``8d7ee999``: the run sat in the drain until the job ended (a
    timeout here).
    """

    monkeypatch.setattr(activation, "ACTIVE", True)
    monkeypatch.setenv("AELIX_JOBS_PROTOCOL", "1")
    _sleeper.Sleeper.instances.clear()
    lines: list[str] = []
    monkeypatch.setattr(print_mode, "_write_raw_stdout", lines.append)
    tools: list[int] = []

    async def model(_m: Any, context: Any, _o: Any = None) -> Any:
        tools.append(len(context.tools))
        if len(tools) == 1:  # hold the first answer until the inbox said the final is queued
            await wait_for(lambda: any('"response"' in line for line in lines))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text=f"answer {len(tools)}")], stop_reason="end_turn"))

    async def tool(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=model,
            tools=[AgentTool(name="t", execute=tool)],
            session=await make_session(tmp_path),
            notice_inbox=True,
        )
    )

    async def factory(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    job = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    reader = asyncio.StreamReader()
    inbox = CommandInbox(reader=reader, posture=PermissionPosture(mode=PermissionMode("yolo")))
    reader.feed_data(json.dumps({"type": "steer", "id": "w1", "message": "WRAP UP", "final": True}).encode() + b"\n")
    task = asyncio.ensure_future(
        run_print_mode(runtime, mode="json", messages=["a residual message"], initial_message="go", jobs=service, inbox=inbox)
    )
    try:
        done, _ = await asyncio.wait({task}, timeout=8)
        assert done, f"the run was still going 8 s after its final call (model calls so far, by tool count: {tools})"
        assert tools == [1, 0], "the answer, then the wrap-up's tool-less call: no residual prompt, no notice turn"
        assert task.result() == 0, "the run's own verdict: its last answer was not an error"
        assert '"aelix_print_done"' in lines[-1], lines[-1]
        assert service.snapshot(job).state == "running", "stopping it is the dispose's, not the drain's"  # type: ignore[union-attr]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        await service.owner_ended(service.owner, "owner_end")
        await service.shutdown()


async def test_a_final_read_by_a_notice_turn_ends_the_drain_with_another_job_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final arrives while the run waits in its drain: the next notice turn reads it, and the run ends.

    What is pinned, and its limit: a final steer is read at a turn boundary, and
    a run waiting in the drain has none until one of its jobs ends. The notice
    turn for the short job reads it (its call has no tools); the long job is not
    waited for. Fails on ``8d7ee999``: the drain went on waiting for the long job.
    """

    monkeypatch.setattr(activation, "ACTIVE", True)
    monkeypatch.setenv("AELIX_JOBS_PROTOCOL", "1")
    _sleeper.Sleeper.instances.clear()
    lines: list[str] = []
    monkeypatch.setattr(print_mode, "_write_raw_stdout", lines.append)
    tools: list[int] = []

    async def model(_m: Any, context: Any, _o: Any = None) -> Any:
        tools.append(len(context.tools))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text=f"answer {len(tools)}")], stop_reason="end_turn"))

    async def tool(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=model,
            tools=[AgentTool(name="t", execute=tool)],
            session=await make_session(tmp_path),
            notice_inbox=True,
        )
    )

    async def factory(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    short = await service.start(_sleeper.KIND, title="short", params={"seconds": 1.5})
    long = await service.start(_sleeper.KIND, title="long", params={"seconds": 30})
    reader = asyncio.StreamReader()
    inbox = CommandInbox(reader=reader, posture=PermissionPosture(mode=PermissionMode("yolo")))
    task = asyncio.ensure_future(run_print_mode(runtime, mode="json", messages=[], initial_message="go", jobs=service, inbox=inbox))
    try:
        await wait_for(lambda: any('"agent_end"' in line for line in lines))  # the first run is over: the drain is next
        assert tools == [1]
        reader.feed_data(json.dumps({"type": "steer", "id": "w1", "message": "WRAP UP", "final": True}).encode() + b"\n")
        await wait_for(lambda: any('"response"' in line for line in lines))
        assert not task.done() and tools == [1], "queued: nothing reads it until a turn starts"
        done, _ = await asyncio.wait({task}, timeout=10)
        assert done, f"the run was still draining 10 s on (model calls so far, by tool count: {tools})"
        assert tools == [1, 0], "the short job's notice turn read the final: one call, no tools, and no turn after it"
        assert task.result() == 0
        assert service.snapshot(short).state != "running" and service.snapshot(long).state == "running"  # type: ignore[union-attr]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        await service.owner_ended(service.owner, "owner_end")
        await service.shutdown()


def _wrap_script(pid_file: Path) -> Any:
    """Request 1 starts a background ``bash`` job that writes its pid and runs
    ``_JOB_SECONDS``; request 2 runs a 2 s foreground ``bash`` (the final steer
    is written while it runs); request 3 is the wrap-up's call; a request after
    that is the notice turn the fixed child never makes."""

    command = f"echo $$ > {shlex.quote(str(pid_file))}; for i in $(seq 1 {_JOB_SECONDS * 10}); do sleep 0.1; done"

    def script(n: int) -> list[dict[str, Any]]:
        if n == 1:
            return _tool_turn("bash", {"command": command, "run_in_background": True}, call_id="c1")
        if n == 2:
            return _tool_turn("bash", {"command": "sleep 2"}, call_id="c2")
        return _text_turn("WRAPPED" if n == 3 else f"AFTER {n}")

    return script


@posix_only
async def test_a_real_child_that_read_a_wrap_up_exits_without_waiting_for_its_own_job(tmp_path: Path) -> None:
    """Fails on ``8d7ee999``: the child waited for its job (the bound here), then asked the model again."""

    pid_file = tmp_path / "job.pid"
    with _ModelServer(script=_wrap_script(pid_file)) as server:
        cwd, env = _child_setup(tmp_path, server)
        proc = await asyncio.create_subprocess_exec(
            *_argv("Task: start the job, then work"),
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        reads = asyncio.gather(proc.stdout.read(), proc.stderr.read())
        try:
            await _until(lambda: len(server.requests) >= 2, "the child never made its second request", 60)
            proc.stdin.write(json.dumps({"type": "steer", "id": "w1", "message": "WRAP UP NOW", "final": True}).encode() + b"\n")
            await proc.stdin.drain()
            await _until(lambda: len(server.requests) >= 3, "the wrap-up's call was never made", 30)
            asked = time.monotonic()
            done, _ = await asyncio.wait({asyncio.ensure_future(proc.wait())}, timeout=_EXIT_BOUND)
            assert done, f"the child was still running {_EXIT_BOUND:.0f} s after the wrap-up's call"
            took = time.monotonic() - asked
            out, err = await asyncio.wait_for(reads, 10)
            job_pid = _read_pid(pid_file)
            assert job_pid is not None
            await _until(lambda: not _alive(job_pid), "the child's own job outlived it", 5)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            _kill(_read_pid(pid_file))
        stderr = err.decode("utf-8", errors="replace")
        events = [json.loads(line) for line in out.decode("utf-8", errors="replace").splitlines() if line.strip()]
    assert len(server.requests) == 3, "a model request after the wrap-up's call"
    assert not server.requests[2].get("tools"), "the wrap-up's call carries no tools"
    assert proc.returncode == 0, stderr
    assert took < _EXIT_BOUND
    assert events[-1].get("type") == "agent_end", "the wrap-up's run is the last thing on its stream"
    assert "Traceback" not in stderr, stderr
