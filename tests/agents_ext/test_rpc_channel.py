"""The rpc delegation channel — real children, real wire, real teardown.

Nothing below the process boundary is mocked, for the same reason
``test_print_channel_spawn`` gives: every defect this layer exists to not have
was reproduced by EXECUTION and none of them is observable in-process.

THE PARITY TEST AT THE BOTTOM IS THE POINT OF THE FILE. One scripted agent
body, emitted through two completely different transports, must produce the
same envelope. It needs no model and no network: the events are scripted, so
what is under test is the CHANNEL, not an agent. If the two envelopes ever
diverge, one of the channels is lying about what the child did.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import sys
import textwrap
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import reaper
from aelix_agents.consent import SpawnGrant
from aelix_agents.print_channel import (
    PrintChannel,
    RunningChild,
    SpawnPlan,
    abort_child,
    build_child_env,
)
from aelix_agents.progress import SubagentProgressBridge
from aelix_agents.prompt_file import _pid_is_live
from aelix_agents.rpc_channel import (
    RpcChannel,
    build_rpc_child_argv,
    build_rpc_child_env,
)
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_ai.utils._process_tree import ProcessTree
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.rpc.rpc_client import RpcClient
from aelix_coding_agent.subagent_contract import DEPTH_ENV_VAR, ResolvedProfile

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="process-group / PDEATHSIG semantics are Linux"
)

# === The scripted agent, in two transports ==================================
#
# ONE vocabulary, so the parity test compares transports and not scripts.

_EMIT = textwrap.dedent(
    """
    import json, os, sys, time

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    def say(text, **usage):
        emit({"type": "message_end", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": usage or None,
            "provider": "stub",
            "model": "stub-1",
        }})

    def start():
        emit({"id": "stub-session", "created_at": "now"})
        emit({"type": "agent_start"})
        emit({"type": "turn_start"})

    def done():
        emit({"type": "agent_end"})
    """
)

# The rpc form: the same emitter, wrapped in a minimal command server. It acks
# ``prompt`` by request id and then runs the script, and it exits when its
# stdin reaches EOF — which is exactly how ``run_rpc_mode`` behaves and what
# ``RpcChannel._shutdown`` relies on for a graceful stop.
_SERVE = textwrap.dedent(
    """
    def serve(script):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            cmd = json.loads(line)
            rid = cmd.get("id")
            kind = cmd.get("type")
            if kind == "prompt":
                emit({"type": "response", "command": "prompt",
                      "success": True, "id": rid})
                script(cmd.get("message", ""))
            else:
                emit({"type": "response", "command": kind, "success": True,
                      "id": rid, "data": {}})
    """
)

# The one scripted agent both transports run. Two turns and a tool, so the
# accumulator fields (input/output/turns) and the last-write-wins fields
# (summary/tokens) are both exercised.
_SCRIPT_BODY = textwrap.dedent(
    """
    def script(task):
        start()
        emit({"type": "tool_execution_start", "tool_name": "read"})
        say("first", input=10, output=5, total_tokens=15)
        emit({"type": "turn_start"})
        say("the answer", input=7, output=3, total_tokens=25)
        done()
    """
)


def _rpc_stub(body: str = _SCRIPT_BODY, *, tail: str = "serve(script)") -> str:
    return _EMIT + _SERVE + body + "\n" + tail + "\n"


def _print_stub(body: str = _SCRIPT_BODY) -> str:
    return _EMIT + body + "\nscript(sys.argv[-1])\n"


def _profile(**kwargs: Any) -> AgentProfile:
    base: dict[str, Any] = {
        "name": "scout",
        "description": "Reads things.",
        "body": "You are a scout. Answer briefly.",
        "file_path": "/home/u/.aelix/agent/agents/scout.md",
        "scope": "user",
        "tools": ("read",),
    }
    base.update(kwargs)
    return AgentProfile(**base)


def _resolved(profile: AgentProfile | None = None) -> ResolvedProfile:
    p = profile if profile is not None else _profile()
    return ResolvedProfile(
        name=p.name, profile=p, source_path=p.file_path, scope=p.scope
    )


def _plan(tmp_path: Path, **kwargs: Any) -> SpawnPlan:
    base: dict[str, Any] = {
        "id": "sub-test",
        "resolved": _resolved(),
        "task": "do the thing",
        "cwd": str(tmp_path),
        "parent_cwd": str(tmp_path),
        "permission_mode": PermissionMode.PLAN,
    }
    base.update(kwargs)
    return SpawnPlan(**base)


def _channel(script: str, *, grace: float = 5.0) -> RpcChannel:
    """A channel whose child is an inline script rather than a real aelix."""

    def _build(*_args: Any, **_kwargs: Any) -> list[str]:
        return [sys.executable, "-c", script]

    return RpcChannel(grace=grace, argv_builder=_build)


async def _alive(pid: int) -> bool:
    """Liveness WITHOUT ``os.kill(pid, 0)`` — on windows that is not a probe.

    Signal 0 is ``CTRL_C_EVENT`` and CPython routes it to
    ``GenerateConsoleCtrlEvent``, so the "check" delivers a real console Ctrl+C
    to a process sharing our console. Measured on a runner: the call returns
    normally and the target dies. Delegating to the product helper keeps one
    correct answer in the repo instead of a fourth copy of the wrong one.
    """

    return _pid_is_live(pid)


# === argv: what the child is actually launched with =========================


def test_the_task_is_NOT_on_the_argv() -> None:
    """It goes over the wire as a ``prompt`` command.

    ``profile_to_argv(oneshot=False)`` silently drops its ``task`` argument, so
    an author who "fixed" the builder to pass it would change nothing while
    believing they had. Worse, it would put the user's prompt on a command line
    visible to every process on the box.
    """

    argv = build_rpc_child_argv(
        _profile(),
        prompt_path="/tmp/p.md",
        task="SECRET-TASK-TEXT",
        permission_mode=PermissionMode.PLAN,
        child_cwd="/tmp",
        parent_cwd="/tmp",
    )
    assert "SECRET-TASK-TEXT" not in " ".join(argv)
    assert not any(a.startswith("Task:") for a in argv)


def test_the_argv_carries_rpc_mode_no_session_and_the_anti_nesting_flags() -> None:
    """``--no-session`` is the one the rpc prefix does NOT supply.

    Without it every delegated child writes a real session file the user never
    started and then finds in their ``/resume`` picker — up to twelve per
    prompt under P3's cap.
    """

    argv = build_rpc_child_argv(
        _profile(),
        prompt_path="/tmp/p.md",
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd="/tmp",
        parent_cwd="/tmp",
    )
    assert argv[:3] == [sys.executable, "-m", "aelix_coding_agent"]
    assert argv[3:5] == ["--mode", "rpc"]
    assert "--no-session" in argv
    assert "--no-agents" in argv
    assert argv[argv.index("--permission-mode") + 1] == PermissionMode.PLAN.value
    assert "--append-system-prompt-file" in argv
    # NOT the umbrella mock-echo demo, and not the console script.
    assert "-m" in argv and "aelix" not in argv


def test_the_env_drops_the_stdin_timeout_because_stdin_is_the_transport() -> None:
    base = dict(os.environ)
    base["AELIX_MCP_CONFIG"] = "/some/mcp.json"
    env = build_rpc_child_env(_profile(), base=base)

    assert "AELIX_STDIN_TIMEOUT" not in env
    # The shared recipe's other three amendments must survive.
    assert "AELIX_MCP_CONFIG" not in env
    assert env[DEPTH_ENV_VAR] == "1"
    assert env["PYTHONPATH"]
    # And the print channel still sets it, so this is a deliberate divergence
    # rather than a regression in the shared builder.
    assert build_child_env(_profile(), base=base)["AELIX_STDIN_TIMEOUT"] == "1"


# === The happy path =========================================================


async def test_a_scripted_turn_produces_an_ok_envelope(tmp_path: Path) -> None:
    channel = _channel(_rpc_stub())
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path), child=row)

    assert result.ok is True
    assert result.status == "ok"
    assert result.summary == "the answer"
    assert result.usage.turns == 2
    assert result.usage.input == 17
    assert result.usage.output == 8
    assert result.usage.tokens == 25
    assert row.state == "done"


async def test_the_task_reaches_the_child_over_the_wire(tmp_path: Path) -> None:
    """The other half of ``test_the_task_is_NOT_on_the_argv``.

    Without this pair one could delete the ``prompt`` send entirely and the
    argv test would still be green.
    """

    echo = textwrap.dedent(
        """
        def script(task):
            start()
            say("got:" + task, input=1, output=1, total_tokens=2)
            done()
        """
    )
    channel = _channel(_rpc_stub(echo))
    result = await channel.run(_plan(tmp_path, task="carry me"), child=RunningChild(
        id="sub-test", profile="scout"
    ))
    assert result.summary == "got:carry me"


async def test_progress_is_published_while_the_turn_runs(tmp_path: Path) -> None:
    seen: list[tuple[str | None, int]] = []

    def _tap(state: Any) -> None:
        seen.append((state.current_tool, state.turns))

    channel = _channel(_rpc_stub())
    await channel.run(_plan(tmp_path), child=RunningChild(id="s", profile="scout"),
                      on_stream=_tap)

    # The reducer really ran on the parsed dicts. If it had not — the trap
    # ``reduce_event`` exists to close — every event would be swallowed by the
    # client's listener guard and this list would hold only zeroes.
    assert ("read", 0) in seen
    assert seen[-1][1] == 2


# === One child per task =====================================================


async def test_the_child_is_dead_when_the_delegation_returns(tmp_path: Path) -> None:
    """One child per task, so a surviving child is an orphan by definition.

    The runtime pops the registry row in a ``finally``, so anything still alive
    here is invisible to ``list`` / ``status`` / ``stop`` / ``stop_all`` and to
    session teardown — verbatim the failure ADR-0197 forbids.
    """

    channel = _channel(_rpc_stub())
    row = RunningChild(id="sub-test", profile="scout")
    await channel.run(_plan(tmp_path), child=row)

    assert row.proc is not None, "row.proc must hold the real process"
    assert row.proc.returncode is not None, "the child outlived its delegation"
    assert not await _alive(row.proc.pid)


async def test_a_child_that_ignores_stdin_eof_is_reaped(tmp_path: Path) -> None:
    """The graceful request is a request. The reaper is what makes it true."""

    stubborn = _rpc_stub(
        _SCRIPT_BODY,
        # Answer, then refuse to notice EOF.
        tail="serve(script)\ntime.sleep(120)",
    )
    channel = _channel(stubborn, grace=0.3)
    row = RunningChild(id="sub-test", profile="scout")
    started = time.monotonic()
    result = await channel.run(_plan(tmp_path), child=row)
    elapsed = time.monotonic() - started

    assert result.summary == "the answer"
    assert row.proc is not None
    assert row.proc.returncode is not None
    # STDIN_EOF_EXIT_SECONDS + the reaper grace, nowhere near the 120 s sleep.
    assert elapsed < 30, f"teardown took {elapsed:.1f}s"


@linux_only
async def test_the_child_gets_its_own_process_group(tmp_path: Path) -> None:
    """Otherwise one Ctrl+C SIGINTs every delegated child at once."""

    seen: dict[str, int] = {}
    reporter = textwrap.dedent(
        """
        def script(task):
            start()
            say("pgid=" + str(os.getpgid(0)), input=1, output=1, total_tokens=1)
            done()
        """
    )
    channel = _channel(_rpc_stub(reporter))
    result = await channel.run(_plan(tmp_path), child=RunningChild(id="s", profile="p"))
    seen["child"] = int(result.summary.split("=")[1])
    assert seen["child"] != os.getpgid(0)


# === Failure legs ===========================================================


async def test_a_child_that_dies_mid_turn_becomes_an_envelope(tmp_path: Path) -> None:
    """Not a raise, and not a full-budget wait."""

    dying = _rpc_stub(
        textwrap.dedent(
            """
            def script(task):
                start()
                say("partial", input=1, output=1, total_tokens=1)
                sys.stderr.write("child exploded\\n")
                sys.stderr.flush()
                os._exit(4)
            """
        )
    )
    channel = _channel(dying)
    row = RunningChild(id="sub-test", profile="scout")
    started = time.monotonic()
    result = await channel.run(
        _plan(tmp_path, timeout_ms=30_000), child=row
    )
    elapsed = time.monotonic() - started

    assert result.ok is False
    assert result.status == "error"
    assert result.exit_code == 4
    assert row.state == "error"
    assert elapsed < 10, f"waited {elapsed:.1f}s on a child that died immediately"
    # The stderr rung: an rpc child that dies writes its diagnosis there and
    # nothing to stdout.
    assert result.details is not None and "child exploded" in result.details


async def test_a_turn_that_never_terminates_times_out_and_keeps_the_partial(
    tmp_path: Path,
) -> None:
    """§(j)'s promise: a timed-out delegation still reports what it got."""

    hang = _rpc_stub(
        textwrap.dedent(
            """
            def script(task):
                start()
                say("partial answer", input=2, output=2, total_tokens=4)
                time.sleep(120)
            """
        )
    )
    channel = _channel(hang, grace=0.3)
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path, timeout_ms=1500), child=row)

    assert result.status == "timeout"
    assert result.ok is False
    assert result.summary == "partial answer"
    assert row.proc is not None and row.proc.returncode is not None


async def test_a_spawn_that_cannot_exec_becomes_an_envelope(tmp_path: Path) -> None:
    """A failed spawn is a RESULT, never a raise."""

    channel = RpcChannel(argv_builder=lambda *a, **k: ["/nonexistent/aelix-binary"])
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path), child=row)

    assert result.ok is False
    assert result.status == "error"
    assert row.state == "error"
    assert result.error


async def test_a_server_that_refuses_the_prompt_becomes_an_envelope(
    tmp_path: Path,
) -> None:
    """The busy preflight's failure shape, correlated by request id.

    This is the leg that makes the uncorrelated-``agent_end`` cross-talk
    unreachable in practice: a second turn is REFUSED rather than silently
    resolved by the first turn's terminator.
    """

    refusing = _EMIT + textwrap.dedent(
        """
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            cmd = json.loads(line)
            emit({"type": "response", "command": cmd.get("type"), "success": False,
                  "error": "busy: a turn is already running", "id": cmd.get("id")})
        """
    )
    channel = _channel(refusing)
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path, timeout_ms=20_000), child=row)

    assert result.ok is False
    assert result.status == "error"
    assert "busy" in (result.error or "")


async def test_a_stop_before_the_spawn_yields_aborted_and_no_process(
    tmp_path: Path,
) -> None:
    channel = _channel(_rpc_stub())
    row = RunningChild(id="sub-test", profile="scout")
    row.stopped = True
    result = await channel.run(_plan(tmp_path), child=row)

    assert result.status == "aborted"
    assert row.state == "stopped"
    assert row.proc is None


async def test_a_stop_mid_turn_yields_aborted(tmp_path: Path) -> None:
    from aelix_agents.print_channel import abort_child

    hang = _rpc_stub(
        textwrap.dedent(
            """
            def script(task):
                start()
                say("partial", input=1, output=1, total_tokens=1)
                time.sleep(120)
            """
        )
    )
    channel = _channel(hang, grace=0.3)
    row = RunningChild(id="sub-test", profile="scout")

    async def _stop_soon() -> None:
        for _ in range(200):
            if row.proc is not None:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        await abort_child(row, grace=0.3)

    stopper = asyncio.ensure_future(_stop_soon())
    result = await channel.run(_plan(tmp_path, timeout_ms=30_000), child=row)
    await stopper

    assert result.status == "aborted"
    assert row.state == "stopped"


async def test_cancellation_kills_the_child_and_propagates(tmp_path: Path) -> None:
    """The ONE thing that escapes ``run``, and only after the child is dead."""

    hang = _rpc_stub(
        textwrap.dedent(
            """
            def script(task):
                start()
                time.sleep(120)
            """
        )
    )
    channel = _channel(hang, grace=0.3)
    row = RunningChild(id="sub-test", profile="scout")
    task = asyncio.ensure_future(
        channel.run(_plan(tmp_path, timeout_ms=60_000), child=row)
    )
    for _ in range(200):
        if row.proc is not None:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(100):
        if row.proc is not None and row.proc.returncode is not None:
            break
        await asyncio.sleep(0.05)
    assert row.proc is not None and row.proc.returncode is not None


async def test_an_oversize_line_is_dropped_counted_and_the_turn_survives(
    tmp_path: Path,
) -> None:
    """The framing budget, end to end into the envelope."""

    noisy = _rpc_stub(
        textwrap.dedent(
            """
            def script(task):
                start()
                sys.stdout.write("Q" * (5 * 1024 * 1024) + "\\n")
                sys.stdout.flush()
                say("survived", input=1, output=1, total_tokens=1)
                done()
            """
        )
    )
    channel = _channel(noisy)
    result = await channel.run(
        _plan(tmp_path, timeout_ms=30_000), child=RunningChild(id="s", profile="p")
    )

    assert result.summary == "survived", "the reader did not resync after the drop"
    assert result.dropped_lines == 1


# === What survives the moment the parent gives up ===========================
#
# Three separate windows in which the child is talking and the parent used to
# not be listening. All three assert on the ENVELOPE, because the reducer being
# CALLED is not the same fact as the answer reaching the caller.


# The report arrives only once the parent has closed stdin — i.e. from inside
# ``_shutdown``'s drain. Gating on EOF rather than on a sleep is what makes the
# window deterministic; it is NOT a claim that a real child behaves this way.
# Measured against the real ``run_rpc_mode``, a child writes NOTHING after its
# stdin EOF: it drops its own event pipe before it aborts the turn. What this
# stub reproduces is the parent-side window those bytes actually arrive in —
# lines still in the pipe when the deadline fired, and a turn that finished
# just inside the drain — for which the parent cannot tell the two apart.
_LATE_REPORT = _rpc_stub(
    textwrap.dedent(
        """
        def script(task):
            start()
            emit({"type": "tool_execution_start", "tool_name": "read",
                  "tool_call_id": "c1", "args": {"path": "/etc/hosts"}})
            emit({"type": "tool_execution_end", "tool_call_id": "c1",
                  "is_error": False})
        """
    ),
    # No ``done()`` inside the turn, so the parent times out waiting for a
    # terminator that only comes after it has stopped waiting.
    tail=(
        "serve(script)\n"
        'emit({"type": "message_end", "message": {'
        '"role": "assistant",'
        '"content": [{"type": "text", "text": "half the answer"}],'
        '"stop_reason": "aborted",'
        '"usage": {"input": 111, "output": 22, "total_tokens": 133},'
        '"provider": "stub", "model": "stub-1"}})\n'
        "done()\n"
    ),
)


async def test_a_timed_out_delegation_reports_what_arrived_during_the_drain(
    tmp_path: Path,
) -> None:
    """The intervention path IS the kill path, so the drain is the payload.

    ``_shutdown`` closes stdin, waits out the exit, then spends
    ``POST_EXIT_DRAIN_SECONDS`` letting the pumps reach EOF — and every line in
    that window is parsed and correlated whether or not anyone is subscribed.
    Unsubscribing before it threw the result away at the fan-out, which is how
    a delegation that HAD a partial answer, a stop reason and a full usage block
    came back as ``(no output)`` with zero tokens.

    ``status`` deliberately stays ``timeout``: ``build_result`` gives the
    caller's outcome precedence over the stream's ``stop_reason``, so this
    recovers the ANSWER, not a different verdict.
    """

    channel = _channel(_LATE_REPORT, grace=0.5)
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path, timeout_ms=2_500), child=row)

    assert result.status == "timeout"
    assert result.summary == "half the answer"
    assert result.stop_reason == "aborted"
    assert result.usage.input == 111
    assert result.usage.output == 22
    assert result.usage.tokens == 133
    assert result.usage.turns == 1
    # The tool events landed BEFORE the deadline, so the trail is the control:
    # it is green either way, and it is what proves the drain added a turn
    # rather than the whole stream having been replayed.
    assert result.tool_trail == "read(/etc/hosts) ok"


# === …and what must NOT survive it ==========================================
#
# The other side of the drain. Keeping the reducer live through teardown is
# what recovers a timed-out child's partial answer (above), and it is also what
# lets a line arriving after the STREAM'S TERMINATOR overwrite a finished turn's
# answer. ``reduce_event`` is last-write-wins on ``summary`` / ``stop_reason`` /
# ``error_message``, ``turns`` is an unconditional increment and ``tokens`` is a
# level, so one late ``message_end`` replaces all of them — and
# ``build_result``'s ``stop_reason in ("error", "aborted")`` disjunct is not
# gated on the caller's outcome, so it flips ``ok`` too.
#
# Both tests below pass ``on_stream``, which is the production shape: the
# runtime always wires one (``runtime.py``'s ``_emit``), and without it the
# listener's second half never runs at all.

_POISON = (
    '{"type": "message_end", "message": {'
    '"role": "assistant",'
    '"content": [{"type": "text", "text": "%s"}],'
    '"stop_reason": "error",'
    '"error_message": "%s",'
    '"usage": {"input": 1, "output": 1, "total_tokens": 1},'
    '"provider": "stub", "model": "stub-1"}}'
)

_CLEAN_TURN = textwrap.dedent(
    """
    def script(task):
        start()
        say("the real answer", input=6, output=4, total_tokens=10)
        done()
    """
)

# One line, strictly AFTER stdin EOF — ``serve`` returns when its loop ends, so
# by construction this is written into the window ``_shutdown`` drains.
_ONE_LATE_LINE = _rpc_stub(
    _CLEAN_TURN,
    tail=(
        "serve(script)\n"
        + "emit(json.loads('''"
        + (_POISON % ("shutdown hook noise", "shutdown hook noise"))
        + "'''))\n"
        + "sys.exit(0)\n"
    ),
)

# 4 000 lines, written IMMEDIATELY after the terminator and without waiting for
# anything. This is the shape a gate on the caller's outcome does not catch:
# ``agent_end`` resolves ``prompt_and_wait``'s future from inside the listener
# fan-out, so ``_run_turn`` only resumes a loop iteration later while the stdout
# pump keeps delivering. 400 lines per ``write`` so the 64 KiB pipe genuinely
# backs up — one write per line and the parent's pump keeps pace, the backlog
# never exceeds a line or two, and the test is green against the bug.
_FLOOD_AFTER_THE_TERMINATOR = _rpc_stub(
    textwrap.dedent(
        """
        def script(task):
            start()
            say("the real answer", input=6, output=4, total_tokens=10)
            done()
            line = %r + "\\n"
            for _ in range(10):
                sys.stdout.write(line * 400)
                sys.stdout.flush()
        """
    )
    % (_POISON % ("post-terminator noise", "post-terminator noise"))
)


async def test_output_after_the_terminator_cannot_fail_a_finished_turn(
    tmp_path: Path,
) -> None:
    """``agent_end`` ends the turn's DATA, not just the turn's wait.

    The drain above is kept for a child that never terminated. This one did, so
    everything after its ``agent_end`` belongs to no turn at all — and folding
    one such line in reports a finished, exit-0 delegation as a failure and
    hands the parent model the noise instead of the answer. Measured before the
    gate: ``ok=False summary='shutdown hook noise' turns=2 tokens=1``.

    Every last-write-wins field is asserted, not just the one ``build_result``
    reads: ``tokens`` is a LEVEL, so a late ``total_tokens: 1`` replaces the
    real 10 outright and a test that only checked ``ok`` would miss it.
    """

    channel = _channel(_ONE_LATE_LINE, grace=0.5)
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(
        _plan(tmp_path, timeout_ms=30_000), child=row, on_stream=lambda _s: None
    )

    assert result.ok is True
    assert result.status == "ok"
    assert result.summary == "the real answer"
    assert result.stop_reason == "end_turn"
    assert result.error is None
    assert result.usage.turns == 1
    assert result.usage.tokens == 10


async def test_a_flood_after_the_terminator_cannot_fail_a_finished_turn(
    tmp_path: Path,
) -> None:
    """The same corruption from a child that does NOT wait for its stdin EOF.

    THIS IS THE TEST THAT PICKS THE BOUNDARY. Unsubscribing when ``_run_turn``
    returns closes the one-late-line case above and leaves this one wide open:
    measured, 1 145 of these 4 000 lines were still folded in, because the
    terminator resolves the waiter's future inside the listener fan-out and the
    pump keeps delivering while the coroutine is rescheduled. Gating on the
    stream's own terminator — the same event ``prompt_and_wait`` waits on —
    makes the window unreachable by construction.

    Not a hypothetical child: ``argv_builder`` is the seam by which a
    non-bundled rpc server is reached, which is the whole direction of the
    rpc-as-standard-transport track.
    """

    channel = _channel(_FLOOD_AFTER_THE_TERMINATOR, grace=0.5)
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(
        _plan(tmp_path, timeout_ms=60_000), child=row, on_stream=lambda _s: None
    )

    assert result.ok is True
    assert result.status == "ok"
    assert result.summary == "the real answer"
    assert result.stop_reason == "end_turn"
    assert result.error is None
    assert result.usage.turns == 1
    assert result.usage.tokens == 10


# 5 000 deltas of 4 KB. ``message_update`` is the shape that matters: the
# reducer ignores it (``stream.py``'s consumed set), and every one of them
# repeats the WHOLE assistant message, so a buffer of them is the turn's length
# times the answer's with no ceiling on either factor.
_FAT_TURN = _rpc_stub(
    textwrap.dedent(
        """
        def script(task):
            start()
            blob = "x" * 4000
            for _ in range(5000):
                emit({"type": "message_update", "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": blob}]}})
            say("done", input=1, output=1, total_tokens=2)
            done()
        """
    )
)


async def test_a_long_turn_is_not_buffered_for_a_caller_that_discards_it(
    tmp_path: Path,
) -> None:
    """PEAK, not retained — and that distinction is the whole test.

    ``prompt_and_wait`` appended every event of the turn to a list that
    ``_run_turn`` drops on the floor. The list is unreachable the instant the
    call returns, so a measurement taken after ``run`` reads ~0.04 MB whether or
    not the buffer existed and would pin nothing at all. Against the peak the
    same run measures 25.6 MB with the buffer and 0.36 MB without.

    Asserting ``prompt_and_wait(..., collect=False) == []`` would be a call-site
    assertion, and an implementation that still buffered and returned a fresh
    empty list would satisfy it. The summary and usage assertions below are the
    other half: the events must still be PARSED and still reach the channel's
    own reducer, so this cannot be made green by starving the listener.
    """

    channel = _channel(_FAT_TURN)
    tracing = tracemalloc.is_tracing()
    if not tracing:
        tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        result = await channel.run(
            _plan(tmp_path, timeout_ms=90_000),
            child=RunningChild(id="sub-test", profile="scout"),
        )
        _retained, peak = tracemalloc.get_traced_memory()
    finally:
        if not tracing:
            tracemalloc.stop()

    # The reducer was not starved: it saw the terminator and the one real
    # message behind the 5 000 deltas.
    assert result.status == "ok"
    assert result.summary == "done"
    assert result.usage.turns == 1
    assert result.usage.tokens == 2
    assert peak < 6 * 1024 * 1024, (
        f"peak parent memory {peak / 1e6:.1f} MB for one turn — the client is "
        "buffering events the channel discards"
    )


# Speaks, then dies, inside the startup grace. ``sys.executable`` and not
# ``/bin/sh``: Windows has no POSIX shell at that path, so the spawn failed
# outright there and ``exit_code`` came back ``None`` instead of 9 (#212). A
# Python child's ~25 ms boot is a coin flip against pi's 100 ms window on a
# loaded box — the exact race that makes
# ``test_start_raises_a_typed_error_when_the_child_dies_in_the_grace`` a
# pre-existing flake — so the grace is widened to 2 s below and ``start`` is
# still guaranteed to be the thing that observes the death.
_GRACE_EVENTS = (
    {"type": "agent_start"},
    {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "partial child text"}],
            "stop_reason": "error",
            "error_message": "rate limited",
            "usage": {"input": 11, "output": 22, "total_tokens": 33},
            "provider": "stub",
            "model": "stub-1",
        },
    },
)
# The leading 2 000-byte line is what makes ``dropped_lines`` observable on this
# path (the framing budget is patched down to 1 000 in the test). It is FIRST so
# the drop provably happens inside the grace, and the two real events behind it
# prove the reader resynced at the next newline rather than losing the turn.
_GRACE_LINES = ("Q" * 2000, *(json.dumps(event) for event in _GRACE_EVENTS))
_BOOT_AND_DIE = (
    "import sys\n"
    "sys.stdout.write("
    + repr("".join(f"{line}\n" for line in _GRACE_LINES))
    + ")\n"
    "sys.stdout.flush()\n"
    'sys.stderr.write("boot traceback\\n")\n'
    "sys.stderr.flush()\n"
    "sys.exit(9)\n"
)


async def test_the_child_that_dies_in_the_startup_grace_is_still_heard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``start`` spawns, pumps, and only THEN waits — so subscribe first.

    Everything the child emitted inside the grace used to be broadcast to an
    empty listener list, because the reducer was wired after ``start`` returned.
    That made "the child said nothing" and "we were not listening yet"
    indistinguishable on the one path where the child's own words are the only
    diagnosis there will ever be.

    The summary shift is a PROMOTION, not a substitution. ``_select_summary``
    gates its stderr rung on ``not ok`` and this path is always ``ok=False``, so
    a traceback can only be displaced by the child's own ``error_message`` — and
    the traceback still travels in ``details``, asserted below.

    ``STARTUP_GRACE_MS`` is widened rather than left at pi's 100 ms because the
    child must provably die INSIDE the window; at 100 ms a loaded box lets it
    die just outside and the run silently takes the ordinary ``_drive`` path.

    ``dropped_lines`` HAS TWO PRODUCTION ASSIGNMENTS AND THIS PINS ONE OF THEM.
    ``captured["dropped"]`` is written in this early return and, separately, in
    ``_drive``'s ``finally``; a failed ``start`` never reaches the second, so
    before this one existed the envelope reported ``dropped_lines: 0`` for a
    child whose very first line blew the budget — and deleting it again leaves
    the whole file green, which is why it needed an assertion of its own. The
    other assignment has its own single pin,
    ``test_an_oversize_line_is_dropped_counted_and_the_turn_survives``, which
    reaches it on the SUCCESSFUL-turn path. Neither is redundant with the other:
    delete either assertion and a production line stops being pinned.

    ``MAX_LINE_BYTES`` is patched down rather than the child emitting a real
    4 MiB line: at production settings the child blocks on the 64 KiB pipe until
    the parent has read the whole thing, so the parent's pump is the bottleneck
    and the cheapest possible drop takes 162 ms to observe against a 100 ms
    grace. The branch is unreachable from the outside on this hardware; the
    budget is what is being varied, not the mechanism.
    """

    monkeypatch.setattr(RpcClient, "STARTUP_GRACE_MS", 2_000)
    monkeypatch.setattr("aelix_agents.rpc_channel.MAX_LINE_BYTES", 1_000)
    channel = RpcChannel(
        grace=0.5, argv_builder=lambda *a, **k: [sys.executable, "-c", _BOOT_AND_DIE]
    )
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path, timeout_ms=10_000), child=row)

    assert result.status == "error"
    assert row.state == "error"
    assert result.exit_code == 9
    assert result.summary == "rate limited"
    assert result.usage.input == 11
    assert result.usage.output == 22
    assert result.usage.tokens == 33
    # The drop happened inside the grace AND the events behind it still arrived:
    # the reader resynced at the next newline instead of losing the turn.
    assert result.dropped_lines == 1
    # Nothing was traded away for it: the process-layer diagnosis is still here.
    assert result.details is not None and "boot traceback" in result.details
    assert "boot traceback" in (result.error or "")


# === The progress tap, at the surface that consumes it ======================


class _ProbeUi:
    """Something ``_ui()`` will not identity-test away as the headless singleton."""

    def __init__(self) -> None:
        self.status_calls: list[tuple[str, str | None]] = []

    def set_status(self, key: str, text: str | None) -> None:
        self.status_calls.append((key, text))


class _ProbeApi:
    """The three attributes ``SubagentProgressBridge`` actually reaches for.

    Hand-written rather than a ``MagicMock`` on purpose: ``_ui()`` compares
    ``api.runtime.ui`` by IDENTITY against ``HEADLESS_UI_CONTEXT``, and a mock
    satisfies that test silently — the statusline half would then be exercised
    by the test and dead in production, or vice versa.
    """

    def __init__(self) -> None:
        self.channels: list[str] = []
        self.ui = _ProbeUi()
        api = self

        class _Events:
            def emit(self, channel: str, _payload: Any) -> None:
                api.channels.append(channel)

        class _Runtime:
            ui = api.ui

        self.events = _Events()
        self.runtime = _Runtime()


# Streams flat out and never terminates, so the parent is still waiting when the
# cancellation lands. 400 lines per ``write`` because the defect needs a real
# BACKLOG: one write per line and the parent's pump keeps pace, the pipe holds
# a line or two at abort, and the storm never forms.
_FLOODING_CHILD = _rpc_stub(
    textwrap.dedent(
        """
        def script(task):
            start()
            line = json.dumps({"type": "message_end", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "flood"}],
                "stop_reason": "end_turn",
                "usage": {"input": 1, "output": 1, "total_tokens": 1},
                "provider": "stub", "model": "stub-1"}}) + "\\n"
            while True:
                sys.stdout.write(line * 400)
                sys.stdout.flush()
        """
    )
)


async def test_a_cancelled_delegation_publishes_no_phantom_delegations(
    tmp_path: Path,
) -> None:
    """One delegation is one ``subagent_start`` and one ``subagent_end``. Always.

    ``_eager_abort`` makes ``row.state`` terminal BEFORE ``_shutdown`` drains,
    and ``runtime._publish`` stamps that state onto every snapshot — so a
    progress tap left live through the teardown publishes a TERMINAL snapshot
    per drained line. ``SubagentProgressBridge`` ends the row and pops its
    ``_tools`` entry on each one, which makes the next line ``first=True``
    again: measured, 2 325 ``subagent_start``/``subagent_end`` pairs for a
    single cancelled child, 2 324 of the starts after the first end.

    ASSERTED AT THE BRIDGE, NOT AT ``on_stream``. Forwarding is not delivery —
    the channel handing the runtime a snapshot is not a defect until something
    consumes it, and ``api.events`` is the surface ``subagent_contract``
    documents a dashboard or a third-party extension subscribing to.

    The one terminal snapshot a delegation is entitled to is published by
    ``runtime._run``'s own ``finally``, after the row is gone from the registry.
    That is where the single ``subagent_end`` below comes from.
    """

    api = _ProbeApi()
    bridge = SubagentProgressBridge(api)
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), on_progress=bridge),
        channel=_channel(_FLOODING_CHILD, grace=0.3),
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(_grant(), _resolved(), "flood forever")
    )
    await asyncio.sleep(1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert api.channels.count("subagent_start") == 1, (
        f"{api.channels.count('subagent_start')} starts for one delegation — "
        "the tap published terminal snapshots through the teardown drain"
    )
    assert api.channels.count("subagent_end") == 1


# === Through the REAL runtime ===============================================


def _grant() -> SpawnGrant:
    return SpawnGrant(
        consented=True,
        mode=PermissionMode.PLAN,
        profile="scout",
        source_path="/home/u/.aelix/agent/agents/scout.md",
        scope="user",
        widened=False,
    )


async def test_the_runtime_drives_the_channel_and_deregisters(tmp_path: Path) -> None:
    """The seam holds: no runtime change is needed to swap the channel in."""

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)), channel=_channel(_rpc_stub())
    )
    result = await runtime.spawn_granted(_grant(), _resolved(), "do the thing")

    assert result.ok is True
    assert result.summary == "the answer"
    assert runtime.list() == [], "the registry row outlived the delegation"


async def test_stop_all_reaches_an_rpc_child(tmp_path: Path) -> None:
    """``stop_all`` bypasses the channel entirely and reaches ``child.proc``.

    Which is why ``RpcChannel`` publishes the real process on the row rather
    than keeping it inside the client — a channel that left ``proc`` as ``None``
    would make ``stop_all`` return claiming success while the child lived on.
    """

    hang = _rpc_stub(
        textwrap.dedent(
            """
            def script(task):
                start()
                time.sleep(120)
            """
        )
    )
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=_channel(hang, grace=0.3),
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(_grant(), _resolved(), "hang forever")
    )

    rows: list[Any] = []
    for _ in range(200):
        # ``list()`` returns SubagentStatus MAPPINGS, not objects.
        live = runtime.list()
        if live:
            rows = live
            break
        await asyncio.sleep(0.05)
    assert rows, "the delegation never registered"
    assert rows[0]["profile"] == "scout"

    await asyncio.sleep(0.5)
    await runtime.stop_all()
    result = await task

    assert result.status == "aborted"
    assert runtime.list() == []


# === #220 — the tree the CLIENT attached is the tree the reaper reaches ======
#
# WHY THE PLATFORM IS INJECTED AT ``reaper._is_win32`` AND NOT AT
# ``reaper.sys.platform``, which is this repo's usual lever
# (``test_reaper_kill_signal_win32.py``). ``reaper.sys is sys``, so that lever is
# process-GLOBAL, and every test below needs a real child inside a real
# ``ProcessTree``: under a global ``sys.platform == "win32"``,
# ``containment_spawn_kwargs()`` returns ``creationflags=`` — which POSIX
# ``subprocess`` rejects with ``ValueError`` — and ``ProcessTree.attach``
# constructs ``_KernelApi()`` outside ``_attach_win32``'s ``except``, raising
# ``AttributeError: module 'ctypes' has no attribute 'WinDLL'`` (#220 spec,
# finding R1, measured). ``_is_win32`` is a FUNCTION precisely so the read can be
# lent late; the product never patches it, and on the windows leg patching it to
# ``True`` is a no-op, so these run for real there.
#
# The kills are NOT stubbed out. ``hard_kill`` is ``killpg(pgid, SIGKILL)`` on
# POSIX and ``taskkill`` + ``TerminateJobObject`` on win32, and the spy calls
# through, so a green assertion means the child really died to the tree. Only
# ``test_a_pre_created_reaper_task_still_kills_through_the_tree`` needs a deaf
# tree — a reaper task has to stay PENDING for the reuse branch to exist at all
# — and it says so and kills the child itself afterwards.


_SPEAKS_THEN_HANGS = _rpc_stub(
    textwrap.dedent(
        """
        def script(task):
            start()
            say("partial answer", input=2, output=2, total_tokens=4)
            time.sleep(120)
        """
    )
)
"""Answers, then never terminates. The turn is provably in flight, which is when
``row.tree`` has to be readable and when a human's ``stop`` actually lands."""


class _TreeSpy:
    """Records what the reaper did to the tree, wrapping the BOUND methods.

    Instance-level on purpose. The obvious alternative — spying on ``reap``'s
    kwargs — stays green for every way the plumbing can be right and the
    behaviour wrong: a reaper task reused without a tree, a tree that was
    already closed, ``abort_child`` never getting one (#220 rpc/RPC-8). And it
    must be the INSTANCE, not the class: a run that replaced ``row.tree`` after
    a reaper captured the old one would be a silent miss for a class-level spy
    (tests/T13).

    ``call_through`` defaults to True so the child really dies and the
    assertions are about behaviour. ``close`` records its CALLER's module, not
    merely that it happened: §A.6 gives every tree exactly one owner, and what
    this file has to prove is that the owner is not the channel.
    """

    def __init__(self, tree: ProcessTree, *, call_through: bool = True) -> None:
        self.tree = tree
        self.pid = tree.pid
        self.soft: list[bool] = []
        self.hard: list[float] = []
        self.closed_by: list[str] = []
        real_soft = tree.soft_kill
        real_hard = tree.hard_kill
        real_close = tree.close

        def _soft(**kwargs: Any) -> bool:
            sent = real_soft(**kwargs) if call_through else True
            self.soft.append(sent)
            return sent

        def _hard() -> None:
            self.hard.append(time.monotonic())
            if call_through:
                real_hard()

        def _close() -> None:
            self.closed_by.append(sys._getframe(1).f_globals.get("__name__", "?"))
            real_close()

        tree.soft_kill = _soft  # type: ignore[method-assign]
        tree.hard_kill = _hard  # type: ignore[method-assign]
        tree.close = _close  # type: ignore[method-assign]


def _spy_the_clients_tree(
    monkeypatch: pytest.MonkeyPatch, *, call_through: bool = True
) -> tuple[list[RpcClient], list[_TreeSpy]]:
    """Hand back the client the channel built and a spy on the tree it attached.

    The channel constructs its own :class:`RpcClient` and never publishes it, so
    the seam is the name ``rpc_channel`` imported. Subclassing rather than
    wrapping keeps every other attribute real — the client under test is the
    product one.
    """

    clients: list[RpcClient] = []
    spies: list[_TreeSpy] = []

    class _SpiedClient(RpcClient):
        async def start(self) -> None:
            await super().start()
            tree = self.tree
            assert tree is not None, "RpcClient.start attached no tree (#202)"
            clients.append(self)
            spies.append(_TreeSpy(tree, call_through=call_through))

    monkeypatch.setattr("aelix_agents.rpc_channel.RpcClient", _SpiedClient)
    return clients, spies


def _record_os_kill(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    """Every ``os.kill``, tagged with the module that made it. DELEGATES.

    ``reaper.os is os``, so there is no way to patch "the reaper's ``os.kill``"
    alone — the attribute is the global one and ``ProcessTree.soft_kill`` reaches
    it too (``_process_tree.py``: POSIX leg 1 is ``os.kill(pid, SIGTERM)``).
    Recording the CALLER is what makes the assertion say what it means: after
    #220 the win32 arm must not signal from ``aelix_agents.reaper`` at all,
    while a send from ``aelix_ai.utils._process_tree`` is the tree doing its job.
    Delegating to the real call is what keeps this an observation.
    """

    calls: list[tuple[str, int, int]] = []
    real = reaper.os.kill

    def _kill(pid: int, sig: int) -> None:
        calls.append((sys._getframe(1).f_globals.get("__name__", "?"), pid, sig))
        real(pid, sig)

    monkeypatch.setattr(reaper.os, "kill", _kill)
    return calls


async def _wait_for(probe: Any, what: str, *, timeout: float = 20.0) -> Any:
    """Poll until ``probe()`` is truthy. Nothing here is event-driven."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError(f"{what} never appeared within {timeout:.0f}s")


async def test_the_rpc_row_carries_the_clients_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``row.tree`` IS ``client.tree`` — borrowed, and marked as borrowed.

    The ROW is the only carrier the three kill doors share (#220 §A.4):
    :class:`RunningChild` has no ``client`` field, ``RpcChannel._eager_abort``
    is a ``@staticmethod`` taking only the row, and ``abort_child`` — the door
    ``runtime.stop`` / ``stop_all`` and the stop-during-spawn race all use, and
    the one that CREATES the reaper task ``_reap`` later reuses — sees only the
    row. Delete ``row.tree = client.tree`` from ``run`` and the first assertion
    goes red (verified).

    ``tree_owned`` is asserted as a contract rather than as a mutation target:
    ``False`` is also the dataclass default, so the explicit assignment in
    ``run`` is documentation. What it pins is the DEFAULT — flip that to ``True``
    and §A.6's closing rules would close a tree ``RpcClient.stop()`` still owes a
    soft -> grace -> hard sequence.
    """

    clients, _ = _spy_the_clients_tree(monkeypatch)
    channel = _channel(_SPEAKS_THEN_HANGS, grace=0.3)
    row = RunningChild(id="sub-test", profile="scout")
    task = asyncio.ensure_future(
        channel.run(_plan(tmp_path, timeout_ms=30_000), child=row)
    )
    try:
        tree = await _wait_for(lambda: row.tree, "row.tree")
        client = clients[0]
        assert client.tree is not None, "the client let go of its own tree"
        assert tree is client.tree, "the row carries a DIFFERENT tree"
        assert row.tree_owned is False
        # Usable, which is what ``reaper._usable`` asks — not merely present.
        assert tree.closed is False
    finally:
        await abort_child(row, grace=0.3)
        await task


async def test_stop_reaches_the_clients_tree_on_win32(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A human's ``stop`` kills through the JOB, not through the root pid.

    This is the test that goes red on ``main``. There ``abort_child`` gets no
    tree — the row never carried one — so on win32 the reaper falls back to
    ``os.kill(pid, SIGTERM)``, which Windows implements as ``TerminateProcess``:
    uncatchable, root-only, and it leaves the child's descendants running with
    no ``/proc`` to walk to them (#220 §1).

    Q4 is pinned here too: ``abort_child`` always passes ``eager_kill=True``, so
    the console event is NOT sent — with zero grace it would only start a
    ``dispose()`` that ``hard_kill`` truncates microseconds later, while doubling
    the latency of the one path a human is waiting on (§A.7).
    """

    monkeypatch.setattr(reaper, "_is_win32", lambda: True)
    kills = _record_os_kill(monkeypatch)
    clients, spies = _spy_the_clients_tree(monkeypatch)
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=_channel(_SPEAKS_THEN_HANGS, grace=0.3),
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(_grant(), _resolved(), "hang forever")
    )
    spy: _TreeSpy = await _wait_for(lambda: spies[0] if spies else None, "the tree")
    rows = await _wait_for(runtime.list, "the registry row")
    # Captured BEFORE the stop. After ``runtime.stop`` returns, the channel's own
    # teardown (``_shutdown`` -> ``client.stop()``) may already have nulled
    # ``client.process`` — it races the reaper's return, and lost that race on
    # the windows-latest py3.12 leg (run 33940861199) while winning it on
    # py3.11. The process object itself is what the assertion below needs.
    proc = clients[0].process
    assert proc is not None, "the client was not started when the row appeared"

    await runtime.stop(rows[0]["id"])

    assert len(spy.hard) == 1, f"the job was not the kill: hard_kill x{len(spy.hard)}"
    assert spy.soft == [], f"Q4: no console event on the abort path, got {spy.soft}"
    from_reaper = [c for c in kills if c[0] == "aelix_agents.reaper"]
    assert from_reaper == [], (
        f"the reaper signalled by pid instead of through the tree: {from_reaper}"
    )
    # ``abort_child`` awaits the reaper, and the reaper returns only after its
    # shielded ``proc.wait()`` — so by here the child it killed through the tree
    # has an exit status. Race-free on every leg, unlike ``client.process``.
    assert proc.returncode is not None, "stop() returned before the child was reaped"

    result = await task
    assert result.status == "aborted"
    assert runtime.list() == []


async def test_a_pre_created_reaper_task_still_kills_through_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_reap`` REUSES ``row.reaper_task``, so the tree has to be on the row.

    The reuse branch (``rpc_channel._reap``: ``if task is None or task.done()``)
    is why a ``tree=client.tree`` threaded into ``_reap`` alone would be thrown
    away on the path a human drives: ``abort_child`` built the task, and
    ``_reap`` never gets to pass anything to the reaper it did not create
    (#220 rpc/RPC-2). What makes the kill still land is that ``abort_child``
    read the tree off the ROW.

    The tree is DEAF here (``call_through=False``) for one reason only: the
    reuse branch needs a reaper task that is still PENDING, and a reaper whose
    kill worked resolves. The child is killed for real in the ``finally``.
    """

    monkeypatch.setattr(reaper, "_is_win32", lambda: True)
    kills = _record_os_kill(monkeypatch)
    _clients, spies = _spy_the_clients_tree(monkeypatch, call_through=False)
    channel = _channel(_SPEAKS_THEN_HANGS, grace=0.3)
    row = RunningChild(id="sub-test", profile="scout")
    run_task = asyncio.ensure_future(
        channel.run(_plan(tmp_path, timeout_ms=60_000), child=row)
    )
    tree: ProcessTree | None = None
    abort_task: asyncio.Task[None] | None = None
    reap_task: asyncio.Task[int] | None = None
    try:
        tree = await _wait_for(lambda: row.tree, "row.tree")
        spy = spies[0]
        abort_task = asyncio.ensure_future(abort_child(row, grace=0.3))
        first = await _wait_for(lambda: row.reaper_task, "row.reaper_task")
        assert len(spy.hard) == 1, "abort_child did not kill through the tree"
        assert not first.done(), "the deaf tree was meant to keep the reaper pending"

        reap_task = asyncio.ensure_future(channel._reap(row.proc, row))
        await asyncio.sleep(0.2)
        assert row.reaper_task is first, "_reap built a SECOND reaper for one child"
        assert len(spy.hard) == 1, "the reused reaper re-issued its kill"
        from_reaper = [c for c in kills if c[0] == "aelix_agents.reaper"]
        assert from_reaper == [], f"a pid signal escaped the tree arm: {from_reaper}"
    finally:
        if tree is not None:
            # The REAL bound method — the instance attribute above is the deaf
            # one — so the child dies and every waiter unwinds.
            ProcessTree.hard_kill(tree)
        for pending in (abort_task, reap_task, run_task):
            if pending is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(pending, 30)


async def test_the_channel_never_closes_the_clients_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly one owner, and it is ``RpcClient.stop()`` (#220 §A.6 rule 1).

    ``close()`` disarms BOTH legs permanently (``soft_kill`` -> ``False``,
    ``hard_kill`` -> no-op), and for a ``kill_on_close=True`` tree on win32 it is
    itself an end-of-everything-left. A channel that closed it would either
    disarm a sequence the client still owes or fire the job kill before
    ``client.drain(POST_EXIT_DRAIN_SECONDS)`` — which is why revision 1's belt
    ``tree.close()`` in ``stop_all`` was dropped (rpc/RPC-4).

    The last assertion is the one that keeps that safe rather than merely
    tidy: the channel does NOT null ``row.tree`` (it never owned it), so a
    ``stop`` arriving after teardown hands the reaper a CLOSED tree — and what
    makes that harmless is ``reaper._usable`` reading ``closed``, not the row
    being empty.
    """

    clients, spies = _spy_the_clients_tree(monkeypatch)
    channel = _channel(_rpc_stub())
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path), child=row)

    assert result.ok is True
    spy = spies[0]
    assert spy.closed_by == ["aelix_coding_agent.rpc.rpc_client"], (
        f"the tree was closed by {spy.closed_by}, not by RpcClient.stop alone"
    )
    assert spy.soft == [] and spy.hard == [], (
        f"a clean run killed something: soft={spy.soft} hard={spy.hard}"
    )
    assert row.tree_owned is False
    assert clients[0].tree is None, "RpcClient.stop must null its own reference"
    assert row.tree is not None and row.tree.closed is True


async def test_the_reaper_the_channel_builds_carries_the_rows_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OTHER half of the reuse story: when ``_reap`` builds the task itself.

    Not in the spec's §D.5 list, and added because ``_reap``'s own
    ``tree=row.tree`` would otherwise have no test that goes red when it is
    deleted — the three tests above all reach the tree through ``abort_child``'s
    reaper. A child that answers and then ignores stdin EOF is the one bed where
    ``_shutdown``'s ``wait_for_exit`` returns ``None`` and ``_reap`` reaches a
    row with no reaper task at all.

    With the tree, leg 1 on win32 is ``tree.soft_kill()`` and the child gets to
    run its own shutdown; without it the reaper falls back to
    ``_signal_child`` -> ``os.kill``, which on Windows is ``TerminateProcess``.
    Both assertions below flip on that deletion (verified).
    """

    monkeypatch.setattr(reaper, "_is_win32", lambda: True)
    kills = _record_os_kill(monkeypatch)
    _clients, spies = _spy_the_clients_tree(monkeypatch)
    stubborn = _rpc_stub(_SCRIPT_BODY, tail="serve(script)\ntime.sleep(120)")
    channel = _channel(stubborn, grace=1.0)
    row = RunningChild(id="sub-test", profile="scout")
    result = await channel.run(_plan(tmp_path), child=row)

    assert result.summary == "the answer"
    spy = spies[0]
    assert spy.soft == [True], f"leg 1 did not go through the tree: {spy.soft}"
    assert spy.hard == [], f"the cooperative leg should have sufficed: {spy.hard}"
    from_reaper = [c for c in kills if c[0] == "aelix_agents.reaper"]
    assert from_reaper == [], (
        f"the reaper signalled by pid instead of through the tree: {from_reaper}"
    )
    assert row.proc is not None and row.proc.returncode is not None


async def test_a_cancelled_delegation_kills_through_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second Ctrl+C door: ``_eager_abort``, which is not a reaper at all.

    Also not in the spec's §D.5 list, and added for the same reason as the test
    above: ``_eager_abort``'s ``tree=row.tree`` is a product line #220 adds and
    nothing else here goes red without it. It is a synchronous
    ``@staticmethod`` called from inside ``except asyncio.CancelledError``
    (``_drive`` twice, ``_reap`` once), so it cannot reach the client — only the
    row — which is finding RPC-1's whole point.

    ``>= 1``, not ``== 1``: a cancelled run reaches ``kill_tree`` from
    ``_eager_abort`` on more than one door plus the reaper's own escalation, so
    the exact count is a property of the cancellation's timing. "Exactly once"
    is asserted only on the timeout tests (§A.8, posix/P7); the behavioural
    evidence here is the child's exit status.
    """

    monkeypatch.setattr(reaper, "_is_win32", lambda: True)
    kills = _record_os_kill(monkeypatch)
    _clients, spies = _spy_the_clients_tree(monkeypatch)
    channel = _channel(_SPEAKS_THEN_HANGS, grace=0.3)
    row = RunningChild(id="sub-test", profile="scout")
    task = asyncio.ensure_future(
        channel.run(_plan(tmp_path, timeout_ms=60_000), child=row)
    )
    spy: _TreeSpy = await _wait_for(lambda: spies[0] if spies else None, "the tree")
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(spy.hard) >= 1, "the cancellation never reached the tree"
    from_reaper = [c for c in kills if c[0] == "aelix_agents.reaper"]
    assert from_reaper == [], (
        f"the reaper signalled by pid instead of through the tree: {from_reaper}"
    )
    assert row.proc is not None
    await _wait_for(lambda: row.proc.returncode is not None, "the child's exit")


# === THE PARITY TEST ========================================================


def _comparable(result: Any) -> dict[str, Any]:
    """Everything about an envelope that must NOT depend on the transport.

    Excluded on purpose: ``id`` (minted per delegation), ``elapsed_ms``
    (an rpc child additionally pays a handshake), ``exit_code`` (a one-shot
    child exits when it is done; an rpc child exits when it is told to), and
    ``details`` (carries stderr, which the two children write differently).
    """

    d = dataclasses.asdict(result)
    for key in ("id", "elapsed_ms", "exit_code", "details"):
        d.pop(key, None)
    return d


async def test_both_channels_report_the_same_scripted_agent_identically(
    tmp_path: Path,
) -> None:
    """THE POINT OF THIS FILE.

    One scripted agent body, two transports — a ``--mode json -p`` one-shot
    reading its task from argv, and a ``--mode rpc`` server reading it off the
    wire — must produce the same :class:`SubagentResult`. No model, no network:
    the events are scripted, so what is under test is the channel.

    A divergence here means one channel is lying about what the child did, and
    it is the only assertion in the sprint that can catch that class of bug at
    all.
    """

    print_script = _print_stub()
    rpc_script = _rpc_stub()

    def _print_argv(*_a: Any, **_k: Any) -> list[str]:
        return [sys.executable, "-c", print_script, "Task: do the thing"]

    def _rpc_argv(*_a: Any, **_k: Any) -> list[str]:
        return [sys.executable, "-c", rpc_script]

    print_result = await PrintChannel(argv_builder=_print_argv).run(
        _plan(tmp_path), child=RunningChild(id="sub-test", profile="scout")
    )
    rpc_result = await RpcChannel(argv_builder=_rpc_argv).run(
        _plan(tmp_path), child=RunningChild(id="sub-test", profile="scout")
    )

    assert _comparable(print_result) == _comparable(rpc_result)
    # And the shared shape is the RIGHT one, not two matching wrongs.
    assert rpc_result.ok is True
    assert rpc_result.summary == "the answer"
    assert rpc_result.usage.turns == 2
    assert rpc_result.usage.input == 17


async def test_both_channels_report_a_failing_agent_identically(
    tmp_path: Path,
) -> None:
    """The second form: parity has to hold on the unhappy path too.

    A child whose model errored exits 0 with empty stderr while the stream
    carries ``stop_reason: "error"`` — the case that proves neither channel
    trusts the exit status alone.
    """

    failing = textwrap.dedent(
        """
        def script(task):
            start()
            emit({"type": "message_end", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "half an answer"}],
                "stop_reason": "error",
                "error_message": "provider exploded",
                "usage": {"input": 3, "output": 1, "total_tokens": 4},
                "provider": "stub",
                "model": "stub-1",
            }})
            done()
        """
    )
    print_script = _EMIT + failing + "\nscript(sys.argv[-1])\n"
    rpc_script = _rpc_stub(failing)

    print_result = await PrintChannel(
        argv_builder=lambda *a, **k: [sys.executable, "-c", print_script, "Task: t"]
    ).run(_plan(tmp_path), child=RunningChild(id="sub-test", profile="scout"))
    rpc_result = await RpcChannel(
        argv_builder=lambda *a, **k: [sys.executable, "-c", rpc_script]
    ).run(_plan(tmp_path), child=RunningChild(id="sub-test", profile="scout"))

    assert _comparable(print_result) == _comparable(rpc_result)
    assert rpc_result.ok is False
    assert rpc_result.status == "error"
    assert rpc_result.stop_reason == "error"
    assert rpc_result.error == "provider exploded"


async def test_both_channels_narrow_tools_and_report_the_drop_identically(
    tmp_path: Path,
) -> None:
    """``dropped_tools`` is a security-relevant field, not a diagnostic."""

    profile = _profile(tools=("read", "bash", "agent"))
    plan = _plan(tmp_path, resolved=_resolved(profile), parent_tools=("read", "write"))

    print_script = _print_stub()
    rpc_script = _rpc_stub()
    print_result = await PrintChannel(
        argv_builder=lambda *a, **k: [
            sys.executable, "-c", print_script, "Task: do the thing"
        ]
    ).run(plan, child=RunningChild(id="sub-test", profile="scout"))
    rpc_result = await RpcChannel(
        argv_builder=lambda *a, **k: [sys.executable, "-c", rpc_script]
    ).run(plan, child=RunningChild(id="sub-test", profile="scout"))

    assert print_result.dropped_tools == rpc_result.dropped_tools
    # ``agent`` is subtracted as the anti-nesting layer; ``bash`` is not in the
    # parent's live grant.
    assert set(rpc_result.dropped_tools) == {"agent", "bash"}


def test_the_rpc_argv_builder_narrows_tools_the_same_way() -> None:
    """The narrowing must reach the CHILD, not just the envelope."""

    from aelix_agents.print_channel import narrow_tools

    narrowed = narrow_tools(_profile(tools=("read", "bash", "agent")), ("read", "write"))
    argv = build_rpc_child_argv(
        narrowed.profile,
        prompt_path="/tmp/p.md",
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd="/tmp",
        parent_cwd="/tmp",
    )
    assert argv[argv.index("--tools") + 1] == "read"


# === The seam ===============================================================


def test_the_three_copies_of_the_terminal_set_agree() -> None:
    """A third copy now exists, so the pair-agreement gate is no longer enough.

    ``progress`` owns the surfaces, ``runtime`` owns the row lifecycle, and this
    channel READS that lifecycle to decide when to stop publishing. None of the
    three may import another's private name — but if this channel's idea of
    terminal ever narrows, it resumes publishing snapshots the renderer treats
    as ends, which is the storm
    ``test_a_cancelled_delegation_publishes_no_phantom_delegations`` pins. The
    pre-existing pair test at ``test_print_channel_spawn.py`` stays exactly as it
    is; the third copy is THIS module's debt, so its gate lives here.
    """

    from aelix_agents.progress import _TERMINAL_STATES as rendered
    from aelix_agents.rpc_channel import _TERMINAL_STATES as channel_gate
    from aelix_agents.runtime import _TERMINAL_STATES as lifecycle

    assert channel_gate == lifecycle == rendered


def test_both_channels_satisfy_the_declared_protocol() -> None:
    """A structural check, so the Protocol cannot drift from its implementers."""

    import inspect

    from aelix_agents.print_channel import SubagentChannel

    expected = inspect.signature(SubagentChannel.run)
    for channel in (PrintChannel, RpcChannel):
        actual = inspect.signature(channel.run)
        assert list(actual.parameters) == list(expected.parameters), channel.__name__


def test_the_runtime_and_the_extension_name_the_protocol_not_an_implementation(
) -> None:
    """Guards the seam against silently narrowing back to ``PrintChannel``."""

    import aelix_agents.extension as ext_mod
    import aelix_agents.runtime as rt_mod

    for module in (rt_mod, ext_mod):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert "channel: SubagentChannel" in src, module.__name__
        assert "channel: PrintChannel" not in src, module.__name__
