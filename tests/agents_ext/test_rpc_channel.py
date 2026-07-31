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
import dataclasses
import json
import os
import shlex
import sys
import textwrap
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.consent import SpawnGrant
from aelix_agents.print_channel import (
    PrintChannel,
    RunningChild,
    SpawnPlan,
    build_child_env,
)
from aelix_agents.rpc_channel import (
    RpcChannel,
    build_rpc_child_argv,
    build_rpc_child_env,
)
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
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
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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


# Speaks, then dies, inside the startup grace. ``/bin/sh`` and not
# ``sys.executable``: a Python child's ~25 ms boot is a coin flip against the
# 100 ms window on a loaded box, which is the exact race that makes
# ``test_start_raises_a_typed_error_when_the_child_dies_in_the_grace`` a
# pre-existing flake. This one boots in ~2 ms and the grace is widened below,
# so ``start`` is guaranteed to be the thing that observes the death.
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
_BOOT_AND_DIE = (
    "printf '%s\\n' "
    + " ".join(shlex.quote(json.dumps(event)) for event in _GRACE_EVENTS)
    + "; printf 'boot traceback\\n' >&2; exit 9"
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
    """

    monkeypatch.setattr(RpcClient, "STARTUP_GRACE_MS", 2_000)
    channel = RpcChannel(
        grace=0.5, argv_builder=lambda *a, **k: ["/bin/sh", "-c", _BOOT_AND_DIE]
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
    # Nothing was traded away for it: the process-layer diagnosis is still here.
    assert result.details is not None and "boot traceback" in result.details
    assert "boot traceback" in (result.error or "")


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
