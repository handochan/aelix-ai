"""Real processes, real pipes, real signals — ADR-0197 §(j)/(l), findings B1-B3, I1-I2.

Nothing here is mocked below the process boundary. Every test spawns an actual
child, because all three defects this layer exists to not have were reproduced
by EXECUTION and none of them is observable in-process:

* **B2** — the two-pipe deadlock. A probe that drained stdout to EOF and then
  read stderr hung for a full two-minute tool timeout on 1 MiB of stderr and
  could not be unwedged even by ``proc.kill(); await proc.wait()``. In-process
  the same code is a couple of coroutine awaits that always succeed.
* **B3** — ``readline()``'s 64 KiB ceiling. Feeding a 5 MiB ``str`` to
  ``reduce_line`` passes trivially; only the real ``StreamReader`` raises
  ``ValueError: Separator is not found, and chunk exceed the limit`` and then
  raises it again on every subsequent call.
* **B1** — the reaper cancelled during its grace. Only a real, SIGTERM-ignoring
  session leader shows the difference between "we escalated" and "we left an
  orphan holding the API keys".

MOST CHILDREN HERE ARE STUBS, and that is deliberate. ``PrintChannel`` takes its
argv from an injectable builder, so these tests drive the REAL pump, reaper,
timeout and envelope machinery against a child purpose-built to misbehave — a
1 MiB stderr burst, a 5 MiB line, a process that ignores SIGTERM, a process that
forks a session-leader grandchild. A real aelix child cannot be made to produce
any of those on demand. The four tests that DO launch ``-m aelix_coding_agent``
are the ones whose subject is aelix's own behaviour (project trust, skills).

HERMETICITY (finding I10): ``tests/conftest.py:25-34``'s download guard is an
in-process ``monkeypatch.setattr`` and does NOT reach a child interpreter. Every
real-aelix child below therefore gets an EXPLICIT environment — ``HOME``,
``XDG_CONFIG_HOME`` and ``AELIX_CODING_AGENT_DIR`` under ``tmp_path``,
``PI_OFFLINE=1``, and no provider API key at all, so it fails fast at "No model
selected" instead of reaching the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import print_channel as pc
from aelix_agents.consent import SpawnGrant
from aelix_agents.envelope import STREAM_ENDED_EARLY
from aelix_agents.print_channel import (
    PrintChannel,
    RunningChild,
    SpawnPlan,
    StderrRing,
    build_child_env,
    resolve_child_cwd,
)
from aelix_agents.reaper import descendant_pids, kill_tree, pdeathsig, reap
from aelix_agents.runtime import _TERMINAL_STATES as _RUNTIME_TERMINAL
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import DEPTH_ENV_VAR, ResolvedProfile

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="process-group / PDEATHSIG semantics are Linux"
)

# === Stub children ============================================================

_PRELUDE = textwrap.dedent(
    """
    import json, os, signal, subprocess, sys, time

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


def _stub(body: str) -> str:
    return _PRELUDE + textwrap.dedent(body)


_HAPPY = _stub(
    """
    start()
    emit({"type": "tool_execution_start", "tool_name": "read"})
    say("first", input=10, output=5, total_tokens=15)
    emit({"type": "turn_start"})
    say("the answer", input=7, output=3, total_tokens=25)
    done()
    """
)

_STDIN_ECHO = _stub(
    """
    start()
    say("stdin=%r" % sys.stdin.read())
    done()
    """
)

_PGID = _stub(
    """
    start()
    say("pgid=%d" % os.getpgid(0))
    done()
    """
)

_STDERR_ONLY = """
import sys
sys.stderr.write("boom: the child could not start\\n")
sys.stderr.flush()
sys.exit(1)
"""

_SLEEPER = _stub(
    """
    start()
    say("partial work done", input=4, output=2, total_tokens=6)
    time.sleep(60)
    done()
    """
)

# Deliberately writes NOTHING to stderr: on a failure outcome the stderr rung of
# the fallback chain outranks the child's own partial summary, so a chatty stub
# would silently turn these into stderr assertions.
_SIGTERM_IGNORING = _stub(
    """
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    start()
    say("i will not die politely")
    time.sleep(120)
    """
)

_BIG_STDERR = _stub(
    """
    start()
    say("before the flood")
    sys.stderr.write("x" * (1024 * 1024))
    sys.stderr.flush()
    say("after the flood", input=1, output=1, total_tokens=2)
    done()
    """
)


def _oversize_line_stub(payload_bytes: int) -> str:
    return _stub(
        f"""
        start()
        emit({{"type": "message_end", "message": {{
            "role": "assistant",
            "content": [{{"type": "text", "text": "x" * {payload_bytes}}}],
            "stop_reason": "end_turn",
        }}}})
        say("recovered", input=2, output=2, total_tokens=4)
        done()
        """
    )


# A well-formed line that the reducer used to raise on. ``json.dumps`` produces
# exactly this — ``json.dumps({"input": float("inf")})`` is ``{"input":
# Infinity}`` — so it is written literally rather than through ``emit`` to keep
# the shape visible. The child then keeps running, which is what turns a raise
# in the pump into a SURVIVING process rather than a merely wrong envelope.
_POISONED_USAGE = _stub(
    """
    start()
    sys.stdout.write('{"type":"message_end","message":{"role":"assistant",'
                     '"content":[{"type":"text","text":"poisoned"}],'
                     '"usage":{"input":Infinity,"output":NaN}}}\\n')
    sys.stdout.flush()
    say("still here", input=3, output=1, total_tokens=4)
    time.sleep(120)
    """
)


def _pipe_holding_stub(marker: Path) -> str:
    """A child that leaks a descendant holding its stdout AND stderr.

    Deliberately does NOT redirect the grandchild's stdio, so it inherits fd 1
    and fd 2 — the exact shape ``mcp/client.py:232``'s ``stdio_client(params)``
    produces for every stdio MCP server a child launches (the SDK's ``errlog``
    defaults to ``sys.stderr``). The child answers and exits 0 immediately; the
    pipes stay open regardless.
    """

    return _stub(
        f"""
        kid = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            start_new_session=True,
        )
        open({str(marker)!r}, "w").write(str(kid.pid))
        start()
        say("the answer", input=5, output=2, total_tokens=7)
        done()
        sys.exit(0)
        """
    )


def _grandchild_stub(marker: Path) -> str:
    """A child that forks a SESSION-LEADER grandchild, exactly like ``bash``.

    ``tools/bash.py:278`` uses ``start_new_session=True``, so a grandchild is
    the leader of its OWN process group and ``os.killpg`` on the child's group
    cannot reach it (finding I2). The grandchild records its pid so the test can
    check it independently.
    """

    return _stub(
        f"""
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        kid = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            start_new_session=True,
        )
        open({str(marker)!r}, "w").write(str(kid.pid))
        start()
        say("grandchild spawned")
        time.sleep(120)
        """
    )


# === Fixtures / helpers =======================================================


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


def _stub_channel(
    script: str, *, grace: float = 5.0, argv: list[str] | None = None
) -> PrintChannel:
    """A channel whose child is an inline script rather than a real aelix."""

    command = argv if argv is not None else [sys.executable, "-c", script]

    def _build(*_args: Any, **_kwargs: Any) -> list[str]:
        return list(command)

    return PrintChannel(grace=grace, argv_builder=_build)


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    """A child environment with no credentials and no shared state (I10)."""

    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    (home / "agent").mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "AELIX_CODING_AGENT_DIR": str(home / "agent"),
        "PI_OFFLINE": "1",
    }


async def _wait_for_pid(row: RunningChild, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = row.proc
        if proc is not None:
            return int(proc.pid)
        await asyncio.sleep(0.01)
    raise AssertionError("the child never started")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover — recycled into another uid
        return True
    return True


async def _await_death(pid: int, timeout: float) -> float:
    """Seconds until ``pid`` is gone; raises if it outlives ``timeout``."""

    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return time.monotonic() - started
        await asyncio.sleep(0.02)
    raise AssertionError(f"pid {pid} survived {timeout}s")


# === Happy path + stdio contract ==============================================


async def test_happy_path_envelope(tmp_path: Path) -> None:
    result = await _stub_channel(_HAPPY).run(_plan(tmp_path))
    assert result.ok is True
    assert result.status == "ok"
    assert result.summary == "the answer"
    assert result.exit_code == 0
    # Two assistant messages → two turns; token counters SUM while ``tokens``
    # (a context LEVEL) takes the last value.
    assert result.usage.turns == 2
    assert result.usage.input == 17
    assert result.usage.output == 8
    assert result.usage.tokens == 25
    assert result.permission_mode == "plan"
    assert result.dropped_lines == 0


async def test_stdin_is_devnull(tmp_path: Path) -> None:
    """Pins the +30 s landmine.

    An INHERITED stdin sends the child into ``_read_piped_stdin``
    (``cli/entry.py:247-357``), which blocks for the whole
    ``AELIX_STDIN_TIMEOUT`` on a pipe nobody will write to — and any bytes that
    DO arrive are prepended to the task message.
    """

    started = time.monotonic()
    result = await _stub_channel(_STDIN_ECHO).run(_plan(tmp_path))
    assert result.ok is True
    assert result.summary == "stdin=''"
    assert time.monotonic() - started < 10


@linux_only
async def test_child_is_in_its_own_process_group(tmp_path: Path) -> None:
    """``start_new_session=True`` — one Ctrl+C must not SIGINT every subagent.

    The default puts the child in the PARENT's group, and neither parent
    (``tui/shell.py:1713-1730``) nor child (``modes/print_mode.py:114-131``)
    installs a SIGINT handler, so a group-wide SIGINT kills every delegation at
    once with no envelope and no partial summary.
    """

    result = await _stub_channel(_PGID).run(_plan(tmp_path))
    assert result.ok is True
    assert result.summary != f"pgid={os.getpgid(0)}"


async def test_startup_death_zero_stdout(tmp_path: Path) -> None:
    """A child with no API key exits 1 having written ZERO stdout bytes.

    Not even the session header. ``state`` is completely empty, so stderr is the
    only rung of the fallback chain that can explain anything — which is why
    that rung is mandatory.
    """

    result = await _stub_channel(_STDERR_ONLY).run(_plan(tmp_path))
    assert result.ok is False
    assert result.status == "error"
    assert result.exit_code == 1
    assert "boom: the child could not start" in result.summary
    assert result.details is not None
    assert "boom" in result.details


async def test_spawn_failure_is_a_result_not_a_raise(tmp_path: Path) -> None:
    channel = _stub_channel("", argv=[str(tmp_path / "no-such-interpreter"), "-c", ""])
    result = await channel.run(_plan(tmp_path))
    assert result.ok is False
    assert result.status == "error"
    assert result.exit_code is None
    assert result.error is not None


# === B2 — the two-pipe deadlock ===============================================


async def test_large_stderr_does_not_deadlock(tmp_path: Path) -> None:
    """1 MiB to stderr BETWEEN two stdout events (finding B2).

    Drain-stdout-then-stderr wedges here forever: the child blocks in
    ``write(2)`` on a full 64 KiB stderr pipe and never emits the stdout line the
    parent is waiting for. Neither side can make progress and the child cannot
    even reach a signal handler.
    """

    started = time.monotonic()
    result = await asyncio.wait_for(
        _stub_channel(_BIG_STDERR).run(_plan(tmp_path)), 30
    )
    elapsed = time.monotonic() - started
    assert result.ok is True
    assert result.summary == "after the flood"
    assert elapsed < 15


async def test_stderr_is_bounded_to_a_ring() -> None:
    """A chatty child must not be able to balloon parent memory."""

    ring = StderrRing(max_bytes=1024)
    ring.feed(b"a" * 4096)
    ring.feed(b"tail")
    text = ring.text()
    assert len(text) == 1024
    assert text.endswith("tail")
    assert ring.total_bytes == 4100


# === B3 — line assembly over a real StreamReader ==============================


async def test_a_200kb_line_survives_where_readline_would_raise(
    tmp_path: Path,
) -> None:
    """The routine case, not the exotic one.

    One ``read`` of a large source file serialises into a ~207 KB
    ``message_end`` — and ``message_end`` is the SOLE source of the summary and
    the usage. ``readline()`` raises past 64 KiB and then keeps raising, so the
    terminating ``agent_end`` would be lost too.
    """

    result = await _stub_channel(_oversize_line_stub(200_000)).run(_plan(tmp_path))
    assert result.ok is True
    assert result.dropped_lines == 0
    # ``turns`` counts assistant ``message_end`` events, so 2 is the proof that
    # the 200 KB line PARSED rather than being lost or raising — and
    # "recovered" (the line after it) proves the stream kept going.
    assert result.usage.turns == 2
    assert result.summary == "recovered"
    # The giant text survived into the uncapped details channel.
    assert result.details is not None
    assert "recovered" in result.details


async def test_oversize_line_dropped_and_stream_recovers(tmp_path: Path) -> None:
    """Over the 4 MiB per-line budget → dropped, counted, and resynced.

    The budget DROPS rather than raises, so the terminating ``agent_end`` still
    parses and the delegation still returns an envelope. ``dropped_lines`` is how
    the truncation becomes visible instead of silent.
    """

    result = await asyncio.wait_for(
        _stub_channel(_oversize_line_stub(5 * 1024 * 1024)).run(_plan(tmp_path)), 60
    )
    assert result.ok is True
    assert result.dropped_lines == 1
    assert result.summary == "recovered"
    # ONE turn, not two: the oversize message never reached the reducer, which
    # is the difference between "dropped" and "silently truncated".
    assert result.usage.turns == 1


# === The terminator, against real processes ==================================
#
# ``agent_end`` is the child's own end-of-run marker. The exit code cannot
# substitute for it: a JSON-mode child that dies mid-turn exits **0**
# (``print_mode.py`` maps ``stop_reason`` to a non-zero code only ``if mode ==
# "text"``). These two children differ ONLY in whether that marker arrived
# intact, and they must land on opposite verdicts.

_DIED_MID_TURN = _stub(
    """
    start()
    say("the complete answer", input=7, output=3, total_tokens=10)
    # No done(): the child stops mid-turn. Exit 0, exactly as a child whose
    # harness was torn down under it does.
    sys.exit(0)
    """
)

_OVERSIZE_TERMINATOR = _stub(
    """
    start()
    say("the complete answer", input=7, output=3, total_tokens=10)
    # ``agent_end`` carries the whole message array on ONE line
    # (``stream.py:535-541``), so a child that read a multi-megabyte file emits a
    # terminator over the 4 MiB budget — on a run that finished PERFECTLY.
    emit({"type": "agent_end", "messages": [{"role": "assistant",
          "content": [{"type": "text", "text": "x" * (5 * 1024 * 1024)}]}]})
    sys.exit(0)
    """
)


async def test_child_that_dies_mid_turn_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    """Exit 0 + a plausible answer + no terminator is a FAILED delegation.

    Before ``build_result`` read ``saw_agent_end`` this returned
    ``ok=True status='ok' summary='the complete answer'`` — the parent model was
    handed a truncated answer as a finished one, with nothing marking it.
    """

    result = await asyncio.wait_for(
        _stub_channel(_DIED_MID_TURN).run(_plan(tmp_path)), 30
    )
    assert result.exit_code == 0
    assert result.dropped_lines == 0
    assert result.ok is False
    assert result.status == "error"
    # The partial SURVIVES — it is the only work the child did, and destroying
    # it is the pi behaviour this package deliberately diverges from.
    assert result.summary == "the complete answer"
    assert result.error == STREAM_ENDED_EARLY


async def test_a_terminator_over_the_line_budget_does_not_fail_a_good_run(
    tmp_path: Path,
) -> None:
    """THE TRAP. Same missing flag, opposite verdict, because a line was dropped.

    A bare ``or not state.saw_agent_end`` predicate fails this delegation, and
    this delegation SUCCEEDED. Once ``LineAssembler`` has dropped a line we
    cannot know whether the terminator was among them, so its absence stops
    being evidence.
    """

    result = await asyncio.wait_for(
        _stub_channel(_OVERSIZE_TERMINATOR).run(_plan(tmp_path)), 60
    )
    assert result.exit_code == 0
    assert result.dropped_lines == 1
    assert result.ok is True
    assert result.status == "ok"
    assert result.summary == "the complete answer"


# === HIGH #3 — a poisoned line must not become an orphan ======================


async def test_a_poisoned_usage_line_does_not_orphan_the_child(
    tmp_path: Path,
) -> None:
    """One stdout line used to leave an un-reapable session leader.

    CPython's ``json`` accepts IEEE specials by default, so
    ``{"usage": {"input": Infinity}}`` is an ordinary line to receive.
    ``_usage_field`` did ``int(value)`` on it, ``OverflowError`` escaped
    ``reduce_line`` (documented "NEVER raises"), escaped ``_pump_stdout``,
    escaped the pump gather, and sailed past a ``wait_for`` that catches only
    ``TimeoutError`` — so ``PrintChannel.run`` propagated with NO reaper ever
    started, while ``runtime._run``'s ``finally`` had already popped the
    registry row, putting the child beyond ``stop`` / ``stop_all`` / teardown
    as well. Measured before the fix::

        run() RAISED OverflowError: cannot convert float infinity to integer
        child_after = 'S (sleeping)'

    ``start_new_session=True`` makes that survivor a session leader no terminal
    signal and no parent-side timeout can reach again, holding the parent's
    provider API keys. Two things must hold: an ENVELOPE comes back, and the
    pid is gone.
    """

    row = RunningChild(id="sub-poison", profile="scout")
    channel = _stub_channel(_POISONED_USAGE, grace=0.5)
    result = await asyncio.wait_for(
        channel.run(_plan(tmp_path, timeout_ms=1500), child=row), 30
    )

    assert result.status == "timeout"
    assert result.ok is False
    # The line AFTER the poisoned one still reduced — the pump kept going.
    assert result.summary == "still here"
    assert row.proc is not None
    await _await_death(int(row.proc.pid), 5.0)


async def test_an_unexpected_failure_still_returns_an_envelope_and_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The belt to the reducer's braces — ``run`` NEVER raises but on cancellation.

    The reducer is now total and the pump swallows, so a poisoned line can no
    longer reach the outer machinery. This test injects a failure BELOW both
    guards to pin the contract itself: whatever any future waiter, reaper or
    reducer does, the caller gets an ``error`` envelope and the child dies. The
    alternative — the shape measured before the fix — is a live session leader
    with no reaper task and no registry row, unreachable by ``stop``,
    ``stop_all`` and the extension teardown alike.
    """

    async def _boom(proc: Any, **_kwargs: Any) -> int:
        raise RuntimeError("synthetic waiter failure")

    monkeypatch.setattr(pc, "_wait_for_exit", _boom)

    row = RunningChild(id="sub-boom", profile="scout")
    result = await asyncio.wait_for(
        _stub_channel(_SLEEPER, grace=0.5).run(
            _plan(tmp_path, timeout_ms=30_000), child=row
        ),
        30,
    )

    assert result.status == "error"
    assert "synthetic waiter failure" in (result.error or "")
    assert row.proc is not None
    await _await_death(int(row.proc.pid), 5.0)


# === HIGH #4 — completion is raced against the process, not pipe EOF ==========


@linux_only
async def test_a_pipe_holding_descendant_does_not_hang_the_delegation(
    tmp_path: Path,
) -> None:
    """A grandchild that inherited fd 1/2 used to cost the FULL timeout.

    ``_stream_and_reap`` awaited both pumps and only then ``proc.wait()``, which
    silently redefined "the child finished" as "no writer holds fd 1/2 any
    more". Every stdio MCP server a child launches holds them:
    ``mcp/client.py:232`` calls ``stdio_client`` with no ``errlog`` and the SDK
    defaults to ``sys.stderr``. Measured before the fix, with a child that
    answers correctly and exits 0 in ~50 ms at ``timeout_ms=3000``::

        status=timeout took=8.04s child='GONE' orphan='S (sleeping)' exit=0

    — the parent blocked for the entire budget (600 s in production), the
    post-kill drain added its own 5 s on the same held pipe, a correct answer
    was thrown away as ``ok=False``, AND the holder survived, because the
    descendant walk ran only at the deadline, by which time the grandchild had
    been reparented out of the child's subtree and the ``/proc`` walk returned
    nothing.
    """

    marker = tmp_path / "grandchild.pid"
    started = time.monotonic()
    result = await asyncio.wait_for(
        _stub_channel(_pipe_holding_stub(marker), grace=0.5).run(
            _plan(tmp_path, timeout_ms=30_000)
        ),
        30,
    )
    took = time.monotonic() - started

    assert result.status == "ok"
    assert result.ok is True
    assert result.summary == "the answer"
    assert result.exit_code == 0
    # Well inside the budget: the child's own exit is what ends the run now.
    assert took < 15.0

    kid = int(marker.read_text())
    await _await_death(kid, 5.0)


# === Timeout + the kill legs ==================================================


async def test_timeout_kills_and_returns_partial(tmp_path: Path) -> None:
    """§(j): a timeout is an OUTCOME carrying the partial work, not an exception.

    pi throws on abort (``index.ts:413``) and discards everything the child
    streamed first, so the user loses the work AND the diagnosis.
    """

    started = time.monotonic()
    result = await _stub_channel(_SLEEPER, grace=0.5).run(
        _plan(tmp_path, timeout_ms=1500)
    )
    elapsed = time.monotonic() - started
    assert result.status == "timeout"
    assert result.ok is False
    assert result.summary == "partial work done"
    assert result.usage.turns == 1
    assert 1.0 < elapsed < 15


async def test_a_child_that_finishes_at_the_deadline_is_not_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM #3 — pipe EOF and ``returncode`` are not the same instant.

    Both pumps reaching EOF means every writer closed fd 1 and fd 2, i.e. the
    child is exiting. But the status only appears when the loop's child watcher
    delivers it, milliseconds to tens of milliseconds later, and that gap used to
    be charged against the caller's deadline: any deadline landing inside it
    produced ``status="timeout", ok=False`` with the COMPLETE answer sitting in
    ``summary``. Measured on a child answering at ~679 ms, sweeping ``timeout_ms``
    over the band above it: ``686 ms -> timeout / ok=False / summary='the
    complete answer'``, 4/30 runs in the reviewer's own sweep.

    The race is made DETERMINISTIC here by slowing the exit-status poll rather
    than by sweeping timeouts and hoping: the poll interval stands in for the
    watcher latency, and the budget guarantees the deadline lands inside the gap.
    Before the fix that is a ``timeout``; after it, the floor covers the gap and
    the run is what it actually was.

    THE TWO NUMBERS BELOW ARE A LOAD-TOLERANCE FIX, and both halves matter.
    The budget must cover the CHILD'S BOOT, because the channel starts its clock
    at ``run()`` and a real interpreter has to start before it can write
    anything. At the original 300 ms this test was load-sensitive: 6/6 in
    isolation but an intermittent failure inside a full-suite run on a 2-core
    box, where boot alone can exceed the whole budget and the pumps never reach
    EOF in time — a scheduler measurement wearing this test's name.

    Widening it does NOT weaken the subject, because the floor stays the only
    thing that can save the run. Two inequalities have to hold and both still
    do: the pumps must finish before the deadline (``boot < 1.5 s``, five times
    the old headroom), and the remaining budget at that moment must be SHORTER
    than the exit gap (``1.5 s − boot < 1.9 s``, true for any boot at all) while
    the gap stays under :data:`~aelix_agents.print_channel.POST_EOF_EXIT_GRACE_SECONDS`
    so the floor can cover it (``1.9 s < 2.0 s``). Take the floor away and the
    wait is the remaining budget alone, which expires before the status lands.
    """

    original = pc._wait_for_exit

    async def _slow_status(proc: Any, **_kwargs: Any) -> int:
        return await original(proc, poll=1.9)

    monkeypatch.setattr(pc, "_wait_for_exit", _slow_status)

    child = _stub(
        """
        start()
        say("the complete answer", input=1, output=1)
        done()
        """
    )
    result = await asyncio.wait_for(
        _stub_channel(child, grace=0.5).run(_plan(tmp_path, timeout_ms=1500)), 30
    )

    assert result.status == "ok"
    assert result.ok is True
    assert result.summary == "the complete answer"
    assert result.exit_code == 0


def _wedged_stub(ready: Path) -> str:
    """Emit, close both pipes, ANNOUNCE READINESS, then wedge.

    The SIGTERM disposition is deliberately NOT set here. It is set by the shell
    in :func:`_wedged_argv` before this interpreter exists — see there for why.
    """

    return _stub(
        f"""
        start()
        say("closing my pipes now")
        done()
        os.close(1)
        os.close(2)
        # THE LAST set-up action, and every earlier line is part of the state it
        # attests to: the disposition is already inherited, the answer is
        # flushed into the pipe, and both pipes are at EOF. So the file
        # appearing means the parent's whole precondition holds — it is the
        # observation that replaces "assume a fresh interpreter got here in
        # 300 ms" (S13, the same move as ``_await_zombie``'s ``waitid``).
        # ``os.close``d at once: fd 1 is free, so this open() lands on it, and a
        # lingering fd 1 would make a stray write go somewhere surprising.
        _ready_fd = os.open({str(ready)!r}, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(_ready_fd)
        time.sleep(120)
        """
    )


def _wedged_argv(script: str) -> list[str]:
    """Launch the stub with SIGTERM already ignored, BEFORE Python exists.

    POSIX: a signal set to *ignore* survives ``exec`` (a HANDLER is reset, an
    ignore is not), so ``trap '' TERM`` in the shell is the child's disposition
    from its very first instruction. That deletes CPython start-up — ~40 ms,
    the dominant term — from the window in which a SIGTERM could still take its
    default action. It is the belt; :func:`_await_readiness` is the braces.

    ``exec`` REPLACES the shell, so there is still exactly ONE process: the
    process group, ``pdeathsig`` and ``descendant_pids`` behave exactly as they
    do for every other stub in this file.
    """

    inner = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
    return ["/bin/sh", "-c", f"trap '' TERM; exec {inner}"]


async def _await_readiness(
    marker: Path, run: asyncio.Future[Any], *, timeout: float = 10.0
) -> None:
    """Block until the wedged child says it is wedged, or fail LEGIBLY.

    The bound (10 s) is deliberately larger than the run's own 5 s deadline: if
    a machine really is too loaded to start an interpreter in ten seconds, the
    test must say *that*, not ``-15 != -SIGKILL``. Also fails fast if the run
    finishes first, which is the other way the precondition can be false.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return
        if run.done():
            outcome = run.exception() or run.result()
            raise AssertionError(
                "the run finished before the child signalled readiness, so the "
                f"wedged state was never reached: {outcome!r}"
            )
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"the child never wrote {marker} within {timeout}s: it never reached "
        "the state under test (SIGTERM ignored, answer emitted, pipes at EOF, "
        "process wedged). This is a machine/environment failure, not a "
        "regression in the timeout path."
    )


async def test_a_wedged_child_that_closed_its_stdio_still_times_out(
    tmp_path: Path,
) -> None:
    """The other half of MEDIUM #3 — the grace is a FLOOR, not an amnesty.

    ``POST_EOF_EXIT_GRACE_SECONDS`` must not turn "the pipes closed" into "wait
    forever". A child that closes stdout/stderr and then refuses to exit is
    still killed, just two seconds after the deadline rather than at it.

    DETERMINISM (S13). This test used to assume a fresh CPython reached
    ``signal.signal(SIGTERM, SIG_IGN)`` inside a 300 ms budget; under load it
    did not, SIGTERM took its default action, and the test failed with
    ``assert -15 == -SIGKILL`` and ``summary='(no output)'``. It no longer makes
    any assumption about interpreter start-up: the disposition is set
    pre-``exec`` (:func:`_wedged_argv`) and the run's deadline is not allowed to
    matter until the child has been OBSERVED to be wedged
    (:func:`_await_readiness`).
    """

    ready = tmp_path / "wedged.ready"
    # The script rides in via ``argv`` (wrapped in the shell), so the positional
    # ``script`` argument is unused — ``_stub_channel`` ignores it when ``argv``
    # is given.
    channel = _stub_channel("", grace=0.5, argv=_wedged_argv(_wedged_stub(ready)))
    started = time.monotonic()
    # ``timeout_ms`` is 5 000, not 300, and that is SAFE here only because the
    # readiness gate below observes the precondition first: raising the budget
    # on its own would move the race rather than remove it (S13). What the pair
    # buys is that the deadline is not allowed to fire until the state under
    # test — SIGTERM ignored, answer emitted, pipes at EOF, process wedged — has
    # been OBSERVED to exist, on any machine at any load.
    run = asyncio.ensure_future(
        channel.run(_plan(tmp_path, timeout_ms=5_000)),
    )
    try:
        await _await_readiness(ready, run)
    except BaseException:
        # A failed precondition must not leak the child: cancel the run, which
        # takes ``run``'s ``except asyncio.CancelledError`` abort leg
        # (print_channel.py:1102-1109) and kills the tree.
        run.cancel()
        with contextlib.suppress(BaseException):
            await run
        raise

    result = await asyncio.wait_for(run, 40)
    elapsed = time.monotonic() - started

    assert result.status == "timeout"
    assert result.ok is False
    assert result.exit_code == -signal.SIGKILL
    # Safe ONLY because readiness was observed: the emit demonstrably happened
    # before the deadline, so this is an assertion about the reducer and not a
    # second bet on interpreter start-up latency (the bet that produced the
    # observed ``summary='(no output)'``).
    assert result.summary == "closing my pipes now"
    # 5 s deadline + 0.5 s SIGTERM grace + reap, with headroom for a loaded box;
    # the outer ``wait_for(..., 40)`` is the real hang detector.
    assert elapsed < 30, "the floor is a grace, not an unbounded wait"


def _await_zombie(pid: int, *, timeout: float = 10.0) -> None:
    """Block until ``pid`` has exited, WITHOUT reaping it.

    ``waitid(WEXITED | WNOWAIT)`` returns as soon as the child is a zombie and
    leaves the status queued for whoever waits next — here, the event loop's
    child watcher. That is precisely the state the caller needs to set up, and
    unlike a fixed sleep it does not assume how long a fresh interpreter takes
    to start and exit on a loaded machine.

    Falls back to a short sleep where ``waitid``/``WNOWAIT`` is unavailable
    (Windows); the assertion there degrades to the old timing assumption rather
    than failing outright.
    """

    waitid = getattr(os, "waitid", None)
    if waitid is None or not hasattr(os, "WNOWAIT"):  # pragma: no cover - POSIX only
        time.sleep(0.05)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            info = waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        except ChildProcessError:  # pragma: no cover - already collected
            return
        if info is not None:
            return
        time.sleep(0.005)
    raise AssertionError(f"child {pid} did not exit within {timeout}s")


async def test_reaping_an_already_exited_child_reports_its_real_exit_code(
    tmp_path: Path,
) -> None:
    """MEDIUM #4 — ``proc.terminate()`` reaps behind the event loop's back.

    ``asyncio.subprocess.Process.terminate`` reaches
    ``subprocess.Popen.send_signal``, whose first statement is ``self.poll()`` —
    a ``waitpid(pid, WNOHANG)``. If the child has exited but the loop's watcher
    has not yet delivered the status, that ``poll()`` reaps the zombie and the
    watcher then substitutes 255. Measured over 60 reaps of a child that really
    exited 0, with the loop briefly blocked so the watcher could not win the
    race: ``{255: 60}``, plus one ``"exit status already read: will report
    returncode 255"`` warning apiece.

    ``build_result`` computes ``failed = … or exit_code != 0``, so on any leg
    whose outcome is not already a failure — ``stop_all`` racing a child that
    just finished, which ``abort_child`` always drives with ``eager_kill=True`` —
    that flipped a SUCCESSFUL delegation to ``ok=False, status="error",
    exit_code=255``. :func:`~aelix_agents.reaper._signal_child` uses ``os.kill``,
    which signals a zombie harmlessly and leaves the status for the watcher.
    """

    codes: list[int] = []
    for _ in range(12):
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "pass",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # BLOCKING, on purpose: it lets the child exit while denying the loop
        # the chance to collect the status, which is the exact race.
        #
        # ``waitid(WNOWAIT)`` returns the moment the child becomes a zombie and
        # deliberately LEAVES the status unreaped, so the loop's watcher still
        # has not collected it — the precondition this test needs. A fixed sleep
        # cannot express that: on a loaded machine a fresh interpreter needs
        # more than the timeout to start and exit, the child is still RUNNING
        # when ``reap`` fires, and the assertion sees ``-SIGTERM`` instead of a
        # lost status. That is a false failure about the test's own timing, not
        # about the behaviour under test (observed once at ``-15``).
        _await_zombie(proc.pid)
        codes.append(await reap(proc, grace=2.0))

    assert codes == [0] * 12, f"exit statuses lost to a behind-the-back reap: {codes}"


async def test_reap_still_escalates_after_the_signal_change(tmp_path: Path) -> None:
    """The kill path must not have been softened by the ``os.kill`` switch."""

    channel = _stub_channel(_SIGTERM_IGNORING, grace=0.4)
    result = await asyncio.wait_for(channel.run(_plan(tmp_path, timeout_ms=800)), 40)
    assert result.exit_code == -signal.SIGKILL


@linux_only
def test_pdeathsig_is_sigterm_and_that_is_a_measured_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MEDIUM #12 / MEDIUM #6 — REJECTING "prefer SIGKILL", with the numbers.

    The review proposed ``PR_SET_PDEATHSIG, SIGKILL`` because a SIGTERM-blocking
    child outlives the parent's hard death with no backstop (measured: ``child
    that BLOCKS SIGTERM (pthread_sigmask) -> state='S (sleeping)'`` three seconds
    after the parent was SIGKILLed). That half is real. The proposed fix is not:
    SIGKILL denies the child its own cleanup, and the child's cleanup is the
    ONLY thing that reaches its ``bash`` grandchildren — ``tools/bash.py:278``
    and ``tools/_subprocess.py:78`` both pass ``start_new_session=True``, so each
    grandchild leads its own group and nothing outside the child can find them
    once the child is gone.

    Measured directly, parent SIGKILLed, child forks a session-leader grandchild
    and cleans it up on SIGTERM::

        pdeathsig=SIGTERM   child_alive=False  grandchild_alive=False
        pdeathsig=SIGKILL   child_alive=False  grandchild_alive=True

    So SIGKILL trades a rare severe residual (a child that BLOCKS SIGTERM — the
    real aelix child does not; it installs a loop handler) for a universal one
    (every parent death orphans every bash grandchild). SIGTERM stays. Both
    residuals are recorded in ADR-0197 §(j); full containment is the
    cgroup/pidfd item already deferred to P3, and this test exists so the
    trade-off cannot be flipped by accident.
    """

    import ctypes

    calls: list[tuple[Any, ...]] = []

    class _FakeLibc:
        def prctl(self, *args: Any) -> int:
            calls.append(args)
            return 0

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: _FakeLibc())
    pdeathsig()

    assert calls, "pdeathsig must actually call prctl"
    option, sig, *_rest = calls[0]
    assert option == 1, "PR_SET_PDEATHSIG"
    assert sig == signal.SIGTERM
    assert sig != signal.SIGKILL


_PDEATH_CHILD = textwrap.dedent(
    """
    import os, signal, subprocess, sys, time

    kid = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    open(sys.argv[1], "w").write(str(kid.pid))

    def _bye(*_a):
        # What a REAL aelix child does on SIGTERM:
        # _signal_cleanup_and_exit -> dispose() -> abort() -> bash.py _kill_group.
        try:
            os.killpg(os.getpgid(kid.pid), signal.SIGKILL)
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _bye)
    sys.stdout.write("READY\\n")
    sys.stdout.flush()
    time.sleep(120)
    """
)

_PDEATH_PARENT = textwrap.dedent(
    """
    import asyncio, ctypes, sys

    SIG = int(sys.argv[2])

    def _pdeath():
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, SIG, 0, 0, 0)

    async def main():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, sys.argv[3], sys.argv[1],
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_pdeath,
        )
        await proc.stdout.readline()
        print("CHILD", proc.pid, flush=True)
        await asyncio.sleep(120)

    asyncio.run(main())
    """
)


@linux_only
def test_pdeathsig_sigkill_would_orphan_every_bash_grandchild(tmp_path: Path) -> None:
    """THE MEASUREMENT BEHIND THE REJECTION — MEDIUM #12 / MEDIUM #6.

    The review's proposed fix for the un-escalated PDEATHSIG was
    ``PR_SET_PDEATHSIG, signal.SIGKILL``. This runs both variants for real and
    shows why that is the wrong trade: with SIGTERM the child's own cleanup
    reaches its session-leader grandchild, and with SIGKILL nothing does — the
    grandchild is reparented to init and unreachable by anything, because the
    parent is dead and ``tools/bash.py`` / ``tools/_subprocess.py`` both pass
    ``start_new_session=True`` so no group or ``PPid`` walk can find it.

    It also makes the RESIDUAL visible in the suite rather than only in an ADR
    paragraph: nothing in P2 contains a child that does NOT cooperate with
    SIGTERM once its parent is gone. That is the cgroup/pidfd item deferred to
    P3, and ADR-0197 §(j) now says so.
    """

    child_script = tmp_path / "pdeath_child.py"
    parent_script = tmp_path / "pdeath_parent.py"
    child_script.write_text(_PDEATH_CHILD, encoding="utf-8")
    parent_script.write_text(_PDEATH_PARENT, encoding="utf-8")

    outcomes: dict[str, bool] = {}
    for name, sig in (("SIGTERM", signal.SIGTERM), ("SIGKILL", signal.SIGKILL)):
        marker = tmp_path / f"kid-{name}.pid"
        parent = subprocess.Popen(
            [
                sys.executable,
                str(parent_script),
                str(marker),
                str(int(sig)),
                str(child_script),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert parent.stdout is not None
            parent.stdout.readline()  # "CHILD <pid>"
            time.sleep(0.3)
            grandchild = int(marker.read_text())
            parent.kill()
            parent.wait(timeout=10)
            time.sleep(1.5)
            outcomes[name] = _alive(grandchild)
        finally:
            with contextlib.suppress(Exception):
                os.kill(int(marker.read_text()), signal.SIGKILL)

    assert outcomes["SIGTERM"] is False, (
        "the cooperative child must still reap its grandchild on parent death"
    )
    assert outcomes["SIGKILL"] is True, (
        "SIGKILL pdeathsig orphans the grandchild — this is why SIGTERM stays"
    )


async def test_sigterm_then_sigkill(tmp_path: Path) -> None:
    """A child that ignores SIGTERM is escalated after the grace.

    Do NOT port pi's predicate: Node's ``subprocess.killed`` means "a signal was
    sent", so ``if (!proc.killed) proc.kill("SIGKILL")`` is false five seconds
    later in every case and the SIGKILL is never sent. The Python predicate is
    LIVENESS.
    """

    channel = _stub_channel(_SIGTERM_IGNORING, grace=0.5)
    result = await asyncio.wait_for(
        channel.run(_plan(tmp_path, timeout_ms=1500)), 40
    )
    assert result.status == "timeout"
    assert result.exit_code == -signal.SIGKILL
    assert result.summary == "i will not die politely"


@linux_only
async def test_double_cancellation_still_kills(tmp_path: Path) -> None:
    """FINDING B1 — the whole reason the reaper is written the way it is.

    ``tui/chrome.py::_interrupt`` (``:854``) has NO debounce on its running
    branch (``:863-866``), so N Ctrl+C presses are N ``cancel()`` calls on the
    same turn task. A plain coroutine reaper takes the second one at its
    ``wait_for`` and never reaches the SIGKILL leg — leaving a session leader
    (``start_new_session=True``) that no terminal signal and no parent-side
    timeout can ever reach again.

    Here the grace is 5 s and the child ignores SIGTERM, so a reaper that merely
    "waited it out" would leave the pid alive well past the assertion window.
    """

    channel = _stub_channel(_SIGTERM_IGNORING, grace=5.0)
    row = RunningChild(id="sub-cancel", profile="scout")
    task = asyncio.ensure_future(
        channel.run(_plan(tmp_path, timeout_ms=1200), child=row)
    )
    pid = await _wait_for_pid(row)
    # Wait past the 1.2 s deadline so the run is INSIDE the reaper's grace when
    # the cancellations land — that is the exact window finding B1 is about, and
    # cancelling earlier would only exercise the pump-phase abort. Asserted
    # rather than assumed: the reaper task must exist before the first cancel.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and row.reaper_task is None:
        await asyncio.sleep(0.05)
    assert row.reaper_task is not None, "the deadline never started the reaper"
    assert _alive(pid), "SIGTERM was supposed to be ignored"

    task.cancel()
    await asyncio.sleep(0.4)
    task.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled() or task.done()

    # Well inside the 5 s grace: the escalation happened because we were
    # cancelled, not because the grace expired.
    assert await _await_death(pid, 3.0) < 3.0
    assert row.reaper_task is not None
    with contextlib.suppress(Exception):
        await asyncio.wait_for(asyncio.shield(row.reaper_task), 5)
    assert row.reaper_task.done()


@linux_only
async def test_bash_grandchild_killed_on_sigkill_leg(tmp_path: Path) -> None:
    """FINDING I2 — ``os.killpg`` cannot reach a session-leader grandchild.

    ``tools/bash.py:278`` and ``tools/_subprocess.py:78`` both pass
    ``start_new_session=True``, so every tool subprocess the child starts is the
    leader of its own group. On the COOPERATIVE leg the child's own
    ``_signal_cleanup_and_exit`` reaps them; this test covers the child that does
    NOT cooperate, which is also the child whose grandchildren nobody else will
    clean up.
    """

    marker = tmp_path / "grandchild.pid"
    channel = _stub_channel(_grandchild_stub(marker), grace=0.5)
    result = await asyncio.wait_for(
        channel.run(_plan(tmp_path, timeout_ms=1500)), 40
    )
    assert result.status == "timeout"
    grandchild = int(marker.read_text())
    assert await _await_death(grandchild, 5.0) < 5.0


@linux_only
def test_child_dies_with_parent(tmp_path: Path) -> None:
    """FINDING I1 — ``PR_SET_PDEATHSIG`` on a hard parent death.

    Without it the child runs every remaining turn, every LLM call and every
    tool to completion, reparented to init: ``print_mode.py:158-165``'s ``_emit``
    only RECORDS ``stdout_dead``, and the acting ``break`` (``:198-205``) plus
    the ``raise BrokenPipeError`` (``:208-211``) are both strictly AFTER
    ``await runtime_host.harness.prompt(initial_message)`` (``:189-193``) —
    which, since ``agents/resolver.py:314-315`` makes the whole task the initial
    prompt, is the only thing a subagent ever does.

    Driven through a HELPER parent so the test process is not the one killed.
    """

    pidfile = tmp_path / "sleeper.pid"
    helper = textwrap.dedent(
        f"""
        import subprocess, sys, time
        from aelix_agents.reaper import pdeathsig
        kid = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            preexec_fn=pdeathsig,
        )
        open({str(pidfile)!r}, "w").write(str(kid.pid))
        time.sleep(120)
        """
    )
    parent = subprocess.Popen([sys.executable, "-c", helper])
    try:
        # READINESS, NOT THE SUBJECT — so the budget is generous. The helper is
        # a cold interpreter that must import ``aelix_agents`` before it can
        # spawn anything: measured at ~2.0 s idle, which left the old 15 s only
        # 7.5x of headroom and made this test fail intermittently inside a
        # full-suite run on a 2-core box while passing 8/8 in isolation. That is
        # the third flake of this exact shape in this file's neighbourhood
        # (see the rpc sprint log's 2.5 / 2.5b): a real subprocess, a fixed
        # budget, and a machine that is busy precisely when the whole suite runs.
        #
        # The ASSERTION below stays tight, because PDEATHSIG firing IS the
        # subject and a slow kill is a real defect.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not pidfile.exists():
            time.sleep(0.05)
        assert pidfile.exists(), "the helper never started its child"
        sleeper = int(pidfile.read_text())
        assert _alive(sleeper)
        parent.kill()
        parent.wait(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(sleeper):
            time.sleep(0.05)
        assert not _alive(sleeper), "PDEATHSIG did not fire"
    finally:
        with contextlib.suppress(Exception):
            parent.kill()
        with contextlib.suppress(Exception):
            parent.wait(timeout=5)


async def test_reap_of_an_already_dead_child_is_a_no_op() -> None:
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    await proc.wait()
    assert await reap(proc, grace=0.1) == 0


@linux_only
async def test_reap_escalates_when_the_reaper_task_itself_is_cancelled() -> None:
    """The SECOND defence of finding B1, pinned on its own.

    ``test_double_cancellation_still_kills`` exercises the caller's half (the
    detached task + ``asyncio.shield`` + eager escalation). This one cancels the
    reaper TASK directly, which is what would happen if any future caller
    awaited it without shielding — and asserts the ``except BaseException``
    inside :func:`reap` still carries it to SIGKILL rather than returning to a
    live session leader.

    Note it swallows the cancellation and RETURNS: a reaper that propagated here
    would leave the process alive, which is the whole failure mode.
    """

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _stub(
            """
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            sys.stderr.write("ARMED\\n")
            sys.stderr.flush()
            time.sleep(60)
            """
        ),
        start_new_session=True,
        stderr=asyncio.subprocess.PIPE,
    )
    # ``reap`` sends SIGTERM the instant it starts, which can land BEFORE the
    # child has finished importing ``signal`` — the handler would then never be
    # installed and the child would die politely, hiding the escalation. Waiting
    # for the marker makes the test about the reaper rather than about how fast
    # a Python interpreter boots.
    assert proc.stderr is not None
    assert await asyncio.wait_for(proc.stderr.readline(), 20) == b"ARMED\n"

    task = asyncio.ensure_future(reap(proc, grace=30.0))
    await asyncio.sleep(0.3)
    task.cancel()
    # 30 s of grace remained; the escalation happened because of the cancel.
    assert await asyncio.wait_for(task, 15) == -signal.SIGKILL
    assert not _alive(proc.pid)


def test_kill_tree_tolerates_a_dead_pid() -> None:
    """Every signal is best-effort: a recycled or dead pid must not raise."""

    class _Dead:
        returncode = 0

        def kill(self) -> None:  # pragma: no cover — guarded by returncode
            raise AssertionError("must not signal an exited process")

    kill_tree(_Dead(), [2**22 - 1])


# === stop / stop_all + the registry ===========================================


def _runtime(tmp_path: Path, script: str, *, grace: float = 0.5) -> _SubagentRuntimeImpl:
    return _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=_stub_channel(script, grace=grace),
    )


async def test_stop_by_id_kills_child(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _SLEEPER)
    task = asyncio.ensure_future(
        runtime.spawn(_resolved(), "go", timeout_ms=60_000)
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not runtime.list():
        await asyncio.sleep(0.01)
    live = runtime.list()
    assert len(live) == 1
    await asyncio.sleep(0.4)  # let the stub emit its partial

    await runtime.stop(live[0]["id"])
    result = await asyncio.wait_for(task, 30)
    assert result.status == "aborted"
    assert result.ok is False
    assert result.summary == "partial work done"
    assert runtime.list() == []


async def test_stop_all_kills_every_child(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _SLEEPER)
    tasks = [
        asyncio.ensure_future(runtime.spawn(_resolved(), f"go {n}", timeout_ms=60_000))
        for n in range(2)
    ]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(runtime.list()) < 2:
        await asyncio.sleep(0.01)
    assert len(runtime.list()) == 2

    await runtime.stop_all()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 30)
    assert [r.status for r in results] == ["aborted", "aborted"]
    assert runtime.list() == []


def _consented_grant() -> SpawnGrant:
    """What the extension's ``tool_call`` hook hands the model-driven door."""

    return SpawnGrant(
        profile="scout",
        source_path="/home/u/.aelix/agent/agents/scout.md",
        scope="user",
        mode=PermissionMode.PLAN,
        widened=False,
        consented=True,
    )


async def test_stop_all_shuts_the_delegation_door_and_the_session_reopens_it(
    tmp_path: Path,
) -> None:
    """``stop_all`` is a teardown, so it must not race its own aborts (ADR-0197).

    A batch's queued members are released by exactly the aborts ``stop_all``
    performs, and a member's permit is freed when its ``PrintChannel.run``
    returns — measurably AFTER ``stop_all``'s last ``await``. A flag scoped to
    the drain therefore still lets a child start on the far side of a completed
    teardown, which is the orphan ADR-0197 forbids. So the door stays shut.

    It cannot stay shut forever either: the SAME runtime instance survives
    ``/new`` / ``/fork`` / ``/resume`` (``extension.py:171``) and every one of
    those emits ``session_shutdown`` first, so a permanently-shut door would
    silently kill delegation for the rest of the process. Both reopen doors are
    pinned here, because they are the two things a live session can do next: send
    a prompt, or type ``/agents run``.
    """

    runtime = _runtime(tmp_path, _HAPPY)
    grant = _consented_grant()

    await runtime.stop_all()

    refused = await runtime.spawn_granted(grant, _resolved(), "go")
    assert refused.ok is False
    assert "stop_all" in (refused.error or "")
    assert runtime.list() == []

    # Door 1 — the next user prompt.
    runtime.reset_delegation_budget()
    allowed = await runtime.spawn_granted(grant, _resolved(), "go")
    assert allowed.ok is True

    # Door 2 — a human typing ``/agents run``, with no prompt in between.
    await runtime.stop_all()
    typed = await runtime.spawn(_resolved(), "go")
    assert typed.ok is True


async def test_stop_all_aborts_a_row_that_appears_while_it_is_draining(
    tmp_path: Path,
) -> None:
    """Nothing may leave the registry un-aborted — including a late arrival.

    A batch member that got through admission just before the door shut
    registers its row DURING the drain, and the thing that releases it is one of
    the aborts ``stop_all`` is performing. ``self._children.clear()`` — the P2
    shape, and the tempting simplification of the loop below it — drops that row
    without ever signalling the child, which is exactly how a delegation ends up
    with no owner and no registry entry.

    The reaper join is used as the injection point because it IS the suspension
    point that releases a queued member in production: ``abort_child`` awaits
    ``asyncio.shield(reaper_task)`` (``print_channel.py:816-820``).
    """

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=_stub_channel(_HAPPY),
    )
    first = RunningChild(id="first", profile="scout")
    late = RunningChild(id="late", profile="scout")

    async def _releases_a_queued_member() -> int:
        runtime._children["late"] = late
        return 0

    first.reaper_task = asyncio.ensure_future(_releases_a_queued_member())
    runtime._children["first"] = first

    await runtime.stop_all()

    assert late.stopped is True, "stop_all dropped a row it had never aborted"
    assert runtime._children == {}


async def test_a_reopen_cannot_race_a_teardown_that_is_still_running(
    tmp_path: Path,
) -> None:
    """The reopen doors decline while a ``stop_all`` is mid-flight.

    A ``/agents run`` or a prompt arriving DURING the drain is not evidence that
    a new child is wanted — the members that teardown is racing are exactly the
    ones it exists to stop. The guard is one line and the only way to observe it
    is from inside, so this test reaches in.
    """

    runtime = _runtime(tmp_path, _HAPPY)
    runtime._draining = True
    runtime._closed = True

    runtime.reset_delegation_budget()

    assert runtime._closed is True, "a reopen escaped a teardown in flight"
    refused = await runtime.spawn_granted(_consented_grant(), _resolved(), "go")
    assert refused.ok is False
    assert "stop_all" in (refused.error or "")


async def test_the_last_snapshot_of_a_delegation_is_always_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A statusline row that outlives its delegation is undismissable.

    ``PrintChannel.run`` writes the prompt file OUTSIDE its own ``try``
    (``print_channel.py:930``) and ``write_prompt_file`` does ``mkdtemp`` +
    ``os.open``, so a full ``/tmp``, an ``EMFILE`` or a yanked ``TMPDIR`` raises
    straight out of a method that otherwise never raises — before
    ``RunningChild.state`` has moved off its ``"starting"`` default
    (``print_channel.py:187``). P3 multiplies the trigger by eight: eight
    concurrent members each writing a prompt directory is exactly the load that
    fires it.

    Published non-terminal, that last snapshot makes
    ``SubagentProgressBridge.__call__`` take its LIVE branch — it WRITES a
    per-child row nothing will ever clear and leaks the id in ``_tools``
    (``progress.py:287-291``). The registry row is already gone by then, so
    ``stop`` / ``status`` cannot clear it either.

    The real ``write_prompt_file`` is what is patched, rather than a fake channel
    substituted, because the whole point is that this call sits OUTSIDE ``run``'s
    own guard — a fake would not have that shape.
    """

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pc, "write_prompt_file", _boom)
    seen: list[str] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(tmp_path),
            on_progress=lambda p: seen.append(p.state),
        ),
        channel=_stub_channel(_HAPPY),
    )

    with pytest.raises(OSError, match="No space left"):
        await runtime.spawn(_resolved(), "go")

    assert seen, "the delegation published nothing at all"
    assert seen[-1] in _RUNTIME_TERMINAL, (
        f"the delegation's last word was {seen[-1]!r}; the bridge writes a "
        "statusline row for that and nothing will ever clear it"
    )
    assert runtime.list() == []


def test_the_runtime_and_the_renderer_agree_on_what_terminal_means() -> None:
    """Two copies of the same three-element set, pinned equal.

    ``runtime`` owns the row lifecycle and ``progress`` owns the surfaces, and
    neither may import the other's private name — but a runtime that promotes a
    state the RENDERER does not consider terminal publishes a row the renderer
    will happily write and never clear, which is the exact failure the test above
    exists to prevent. Equality is the cheapest way to say so.
    """

    from aelix_agents.progress import _TERMINAL_STATES as rendered

    assert rendered == _RUNTIME_TERMINAL


async def test_stop_of_an_unknown_id_is_silent(tmp_path: Path) -> None:
    """``stop`` races a run that just finished; that race is not an error."""

    await _runtime(tmp_path, _HAPPY).stop("sub-nope")


async def test_status_reports_the_live_tool(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _SLEEPER)
    task = asyncio.ensure_future(
        runtime.spawn(_resolved(), "go", timeout_ms=60_000)
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not runtime.list():
        await asyncio.sleep(0.01)
    row = runtime.list()[0]
    assert row["profile"] == "scout"
    assert row["state"] in ("starting", "running")
    assert runtime.status(row["id"])["id"] == row["id"]
    with pytest.raises(KeyError):
        runtime.status("sub-nope")
    await runtime.stop(row["id"])
    await asyncio.wait_for(task, 30)


async def test_progress_is_published_to_both_taps(tmp_path: Path) -> None:
    """The per-spawn callback and the session-wide bridge, never one instead
    of the other."""

    host_seen: list[str] = []
    call_seen: list[str] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(tmp_path),
            on_progress=lambda p: host_seen.append(p.state),
        ),
        channel=_stub_channel(_HAPPY),
    )
    result = await runtime.spawn(
        _resolved(), "go", on_event=lambda p: call_seen.append(p.state)
    )
    assert result.ok is True
    assert host_seen[0] == "starting"
    assert host_seen[-1] == "done"
    assert call_seen == host_seen


async def test_a_broken_progress_subscriber_does_not_break_the_spawn(
    tmp_path: Path,
) -> None:
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(tmp_path),
            on_progress=lambda _p: (_ for _ in ()).throw(RuntimeError("boom")),
        ),
        channel=_stub_channel(_HAPPY),
    )
    result = await runtime.spawn(_resolved(), "go")
    assert result.ok is True


# === Cost fallback (ADR-0198 rule 9) ==========================================


class _FakeRegistry:
    """One model, priced. Stands in for the live ``ModelRegistry`` lookup."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def find(self, provider: str, model_id: str) -> Any:
        from aelix_ai.streaming import Model, ModelCost

        self.asked.append((provider, model_id))
        if (provider, model_id) != ("stub", "stub-1"):
            return None
        return Model(
            id=model_id,
            provider=provider,
            cost=ModelCost(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
        )


async def test_cost_is_computed_from_provenance_when_the_adapter_reports_none(
    tmp_path: Path,
) -> None:
    """openrouter and openai-completions emit no ``cost`` key at ALL.

    So this is the COMMON path, not the exotic one. It lives outside
    ``stream.py`` because it needs a model-registry lookup — disk I/O, which a
    pure reducer may not do — which is exactly why ``_StreamState`` records
    ``provider`` and ``model`` in the first place.
    """

    registry = _FakeRegistry()
    channel = PrintChannel(
        argv_builder=lambda *a, **k: [sys.executable, "-c", _HAPPY],
        model_registry=lambda: registry,
    )
    result = await channel.run(_plan(tmp_path))
    assert registry.asked == [("stub", "stub-1")]
    # input 17 @ $3/M + output 8 @ $15/M
    assert result.usage.cost == pytest.approx((17 * 3.0 + 8 * 15.0) / 1_000_000)


async def test_a_cost_the_child_reported_is_never_recomputed(tmp_path: Path) -> None:
    priced = _stub(
        """
        start()
        emit({"type": "message_end", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
            "usage": {"input": 1, "output": 1, "cost": {"total": 0.5}},
            "provider": "stub",
            "model": "stub-1",
        }})
        done()
        """
    )
    registry = _FakeRegistry()
    channel = PrintChannel(
        argv_builder=lambda *a, **k: [sys.executable, "-c", priced],
        model_registry=lambda: registry,
    )
    result = await channel.run(_plan(tmp_path))
    assert result.usage.cost == 0.5
    assert registry.asked == []


async def test_a_registry_that_raises_never_fails_the_delegation(
    tmp_path: Path,
) -> None:
    class _Broken:
        def find(self, *_args: Any) -> Any:
            raise RuntimeError("registry exploded")

    channel = PrintChannel(
        argv_builder=lambda *a, **k: [sys.executable, "-c", _HAPPY],
        model_registry=_Broken,
    )
    result = await channel.run(_plan(tmp_path))
    assert result.ok is True
    assert result.usage.cost == 0.0


# === The registry has to REACH the channel ===================================
#
# Every test above hands ``PrintChannel`` a registry explicitly. Production did
# not: ``_SubagentRuntimeImpl.channel`` was ``field(default_factory=PrintChannel)``
# — ``PrintChannel()`` with no arguments, so ``model_registry=None`` — and
# ``apply_cost_fallback`` returned at its first guard for EVERY delegation. The
# fallback was fully tested and completely inert. These two pin the wiring
# itself, and they pin it by DELIVERY (a priced envelope), not by asserting an
# attribute is non-None.


async def test_the_default_runtime_channel_prices_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runtime built the way production builds it produces a PRICED envelope.

    Measured before the fix, against a real OpenRouter delegation: the envelope
    read ``2940 in / 20 out`` and carried no ``$`` at all — ``tool.py:748`` and
    ``aggregate.py:290`` both gate on ``if usage.cost:``, so a structurally-zero
    cost prints nothing rather than ``$0.0000``.

    ``build_child_argv`` is patched BEFORE the runtime is constructed because
    ``PrintChannel.__init__`` resolves ``argv_builder or build_child_argv`` at
    construction time — that is the only seam a caller who does not own the
    channel has, and using it is what keeps this a test of the DEFAULT channel.
    """

    registry = _FakeRegistry()
    monkeypatch.setattr(
        pc, "build_child_argv", lambda *a, **k: [sys.executable, "-c", _HAPPY]
    )
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), model_registry=lambda: registry)
    )

    assert runtime.channel is not None
    result = await runtime.channel.run(_plan(tmp_path))

    assert registry.asked == [("stub", "stub-1")]
    assert result.usage.cost == pytest.approx((17 * 3.0 + 8 * 15.0) / 1_000_000)


async def test_an_injected_channel_still_wins(tmp_path: Path) -> None:
    """The seam every other test in this package drives must keep working.

    ``__post_init__`` only fills a channel nobody supplied; it never replaces
    one. Without this, "build the default in ``__post_init__``" would be
    indistinguishable from "overwrite whatever the caller passed".
    """

    injected = PrintChannel(
        argv_builder=lambda *a, **k: [sys.executable, "-c", _HAPPY]
    )
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(tmp_path), model_registry=lambda: _FakeRegistry()
        ),
        channel=injected,
    )
    assert runtime.channel is injected
    # And it is genuinely still the one that runs — it has no registry, so the
    # cost stays 0 rather than picking up the host's.
    result = await runtime.channel.run(_plan(tmp_path))
    assert result.usage.cost == 0.0


# === Host defaults ============================================================


async def test_an_unwired_host_produces_a_read_only_child(tmp_path: Path) -> None:
    """Every :class:`SubagentHost` default leans the conservative way.

    A host that forgot to wire the parent's posture gets ``DEFAULT``, whose
    clamp is ``PLAN``; a host that forgot the consent context gets a headless
    one, which never prompts and never widens. Neither omission can produce an
    unattended writable child.
    """

    seen: dict[str, Any] = {}

    def _capture(*_args: Any, permission_mode: Any, **_kwargs: Any) -> list[str]:
        seen["mode"] = permission_mode
        return [sys.executable, "-c", _HAPPY]

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=PrintChannel(argv_builder=_capture),
    )
    result = await runtime.spawn(_resolved(), "go")
    assert seen["mode"] is PermissionMode.PLAN
    assert result.permission_mode == "plan"


# === The 0600 prompt file (§(h)) ==============================================


@pytest.fixture
def _isolated_tmpdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``tempfile`` at a directory only this test writes to.

    Lets each case below assert on the ABSENCE of a leaked
    ``aelix-subagent-*`` directory, which is the only way to prove the unlink
    ran on a path that returns nothing.
    """

    root = tmp_path / "spool"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _leaked(root: Path) -> list[Path]:
    return list(root.glob("aelix-subagent-*"))


async def test_prompt_file_mode_is_0600(tmp_path: Path) -> None:
    """0700 dir first, then ``O_CREAT|O_EXCL`` 0600 inside it.

    The ordering means the mode is correct at CREATION — there is no instant
    where a user's private system prompt exists world-readable, and ``O_EXCL``
    refuses to follow a symlink planted at the target path.
    """

    seen: dict[str, Path] = {}

    def _build(*_args: Any, prompt_path: str, **_kwargs: Any) -> list[str]:
        seen["path"] = Path(prompt_path)
        seen["mode"] = Path(prompt_path).stat().st_mode  # pyright: ignore[reportArgumentType]
        seen["dir_mode"] = Path(prompt_path).parent.stat().st_mode  # pyright: ignore[reportArgumentType]
        seen["body"] = Path(prompt_path).read_text(encoding="utf-8")  # pyright: ignore[reportArgumentType]
        return [sys.executable, "-c", _HAPPY]

    await PrintChannel(argv_builder=_build).run(_plan(tmp_path))
    assert stat.S_IMODE(seen["mode"]) == 0o600  # pyright: ignore[reportArgumentType]
    assert stat.S_IMODE(seen["dir_mode"]) == 0o700  # pyright: ignore[reportArgumentType]
    assert seen["body"] == _profile().body
    assert not seen["path"].exists()


@pytest.mark.parametrize(
    ("case", "script", "timeout_ms"),
    [
        ("ok", _HAPPY, None),
        ("error", _STDERR_ONLY, None),
        ("timeout", _SLEEPER, 600),
        ("killed", _SIGTERM_IGNORING, 600),
    ],
)
async def test_prompt_file_unlinked_on_every_path(
    _isolated_tmpdir: Path, tmp_path: Path, case: str, script: str, timeout_ms: int | None
) -> None:
    channel = _stub_channel(script, grace=0.3)
    await asyncio.wait_for(channel.run(_plan(tmp_path, timeout_ms=timeout_ms)), 40)
    assert _leaked(_isolated_tmpdir) == [], case


def _a_dead_pid() -> int:
    """A pid that is certainly not running: spawn one and wait for it."""

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


async def test_a_parent_killed_run_leaks_the_prompt_dir_and_the_sweep_reclaims_it(
    _isolated_tmpdir: Path,
) -> None:
    """THE PLAN'S ``parent-killed`` CASE (§6.4) — P2 review MEDIUM #5 / #14.

    It was missing from ``test_prompt_file_unlinked_on_every_path`` and could
    not have been added there as written: a SIGKILLed parent runs no ``finally``,
    so the 0700 directory holding the user's private system prompt genuinely
    survived. Measured 4/4 on real parent-SIGKILL probes driving the real
    ``PrintChannel``, every one leaving e.g.::

        leaked_tmpdirs=['/tmp/aelix-subagent-c75oxoxz']
        modes=['0o600'] contents=['SECRET-SYSTEM-PROMPT-BODY']

    — the mode and the body exactly as designed, and the file simply never going
    away. One per delegation, for the life of the box.

    So the case is asserted in the shape it actually has: the three in-process
    owners cannot reclaim it, and :func:`sweep_stale_prompt_dirs` — the fourth
    owner, run by the next aelix start — can. The directory name carries the
    creating pid precisely so that sweep is possible.

    The dead parent is SIMULATED (a directory stamped with a pid that has
    exited) rather than forked: that is byte-for-byte the on-disk state the real
    probe produced, and it keeps a second interpreter and its timing out of the
    suite. The real end-to-end version is the probe recorded above.
    """

    from aelix_agents.prompt_file import sweep_stale_prompt_dirs, write_prompt_file

    # A LIVE delegation's directory, stamped with our own pid.
    mine = write_prompt_file("scout", "MY-LIVE-PROMPT")
    assert str(os.getpid()) in mine.directory.name

    # What a parent that died hard leaves behind: same shape, dead pid.
    orphan = _isolated_tmpdir / f"aelix-subagent-{_a_dead_pid()}-orphaned"
    orphan.mkdir(mode=0o700)
    (orphan / "prompt-scout.md").write_text("SECRET-SYSTEM-PROMPT-BODY")

    swept = sweep_stale_prompt_dirs(_isolated_tmpdir)
    assert str(orphan) in swept
    assert not orphan.exists()
    # The live delegation's directory is untouched.
    assert mine.path.read_text(encoding="utf-8") == "MY-LIVE-PROMPT"


async def test_the_sweep_never_touches_a_live_parents_directory(
    _isolated_tmpdir: Path,
) -> None:
    """The sweep's refusals, which all err toward LEAVING a directory alone.

    A wrong deletion yanks a running delegation's system prompt out from under a
    child that has not finished reading it, so every ambiguous case resolves to
    "leave it": our own pid, a live pid, and — the one a pid check cannot see —
    a name from an older build that carries no pid at all.
    """

    from aelix_agents.prompt_file import sweep_stale_prompt_dirs

    mine = _isolated_tmpdir / f"aelix-subagent-{os.getpid()}-live"
    unstamped = _isolated_tmpdir / "aelix-subagent-oldbuildstyle"
    unrelated = _isolated_tmpdir / "something-else"
    for path in (mine, unstamped, unrelated):
        path.mkdir(mode=0o700)

    assert sweep_stale_prompt_dirs(_isolated_tmpdir) == []
    for path in (mine, unstamped, unrelated):
        assert path.exists()


async def test_prompt_file_unlinked_on_a_spawn_failure(
    _isolated_tmpdir: Path, tmp_path: Path
) -> None:
    channel = _stub_channel("", argv=[str(tmp_path / "nope"), "-c", ""])
    await channel.run(_plan(tmp_path))
    assert _leaked(_isolated_tmpdir) == []


@linux_only
async def test_prompt_file_unlinked_when_the_owner_is_cancelled(
    _isolated_tmpdir: Path, tmp_path: Path
) -> None:
    """The cancellation path still runs its ``finally``.

    The removal is SYNCHRONOUS and never raises, so once the ``finally`` is
    entered nothing can interrupt it — which is why the temp dir survives a
    second Ctrl+C that the awaiting coroutine does not.
    """

    channel = _stub_channel(_SIGTERM_IGNORING, grace=5.0)
    row = RunningChild(id="sub-cancel", profile="scout")
    task = asyncio.ensure_future(
        channel.run(_plan(tmp_path, timeout_ms=60_000), child=row)
    )
    pid = await _wait_for_pid(row)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert _leaked(_isolated_tmpdir) == []
    assert row.prompt is None
    await _await_death(pid, 5.0)


async def test_a_declined_spawn_never_creates_a_temp_dir(
    _isolated_tmpdir: Path, tmp_path: Path
) -> None:
    """§(i): a decline must leave nothing behind — including no 0700 directory.

    Achieved by construction rather than by cleanup: consent is taken BEFORE
    ``write_prompt_file`` is ever called, so there is nothing to unlink.
    """

    class _DecliningUI:
        async def select(self, _title: str, _options: list[str]) -> None:
            return None

    class _Ctx:
        has_ui = True
        ui = _DecliningUI()

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(tmp_path),
            posture=lambda: PermissionMode.AUTO_ACCEPT,
            consent_context=_Ctx,
        ),
        channel=_stub_channel(_HAPPY),
    )
    result = await runtime.spawn(_resolved(), "go")
    assert result.status == "declined"
    assert result.ok is False
    assert _leaked(_isolated_tmpdir) == []


# === Environment (§(l)) =======================================================


def test_env_depth_is_set_for_a_leaf() -> None:
    env = build_child_env(_profile(), base={})
    assert env[DEPTH_ENV_VAR] == "1"


def test_orchestrator_and_leaf_produce_different_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closes finding I3 — ``role`` is arithmetically INERT at MAX == 1.

    Both branches yield 1 there, and ``agents/profile.py:220`` defaults ``role``
    to ``"leaf"``, so the shipped test would pass vacuously. Raising the cap is
    what makes the two branches distinguishable, and it is exactly what P3 does.
    """

    monkeypatch.setattr(pc, "MAX_SUBAGENT_DEPTH", 2)
    leaf = build_child_env(_profile(role="leaf"), base={})
    orchestrator = build_child_env(_profile(role="orchestrator"), base={})
    assert leaf[DEPTH_ENV_VAR] == "2"
    assert orchestrator[DEPTH_ENV_VAR] == "1"


def test_env_mcp_config_cleared() -> None:
    """Otherwise N delegations become N x M untracked MCP subprocesses."""

    env = build_child_env(_profile(), base={"AELIX_MCP_CONFIG": "/etc/mcp.json"})
    assert "AELIX_MCP_CONFIG" not in env


def test_env_pins_the_stdin_timeout() -> None:
    """An INHERITED ``"0"`` means WAIT FOREVER (``cli/entry.py:302-310``)."""

    env = build_child_env(_profile(), base={"AELIX_STDIN_TIMEOUT": "0"})
    assert env["AELIX_STDIN_TIMEOUT"] == "1"


def test_pythonpath_propagated() -> None:
    env = build_child_env(_profile(), base={})
    root = str(Path(__import__("aelix_coding_agent").__file__).resolve().parents[1])
    assert env["PYTHONPATH"].split(os.pathsep)[0] == root


def test_pythonpath_is_not_duplicated() -> None:
    root = str(Path(__import__("aelix_coding_agent").__file__).resolve().parents[1])
    env = build_child_env(_profile(), base={"PYTHONPATH": f"/other{os.pathsep}{root}"})
    assert env["PYTHONPATH"].split(os.pathsep).count(root) == 1


def test_credentials_and_agent_dir_are_inherited_on_purpose() -> None:
    """A child that cannot authenticate cannot do the work it was delegated."""

    base = {
        "ANTHROPIC_API_KEY": "sk-test",
        "AELIX_CODING_AGENT_DIR": "/custom/agent",
        "XDG_CONFIG_HOME": "/custom/config",
    }
    env = build_child_env(_profile(), base=base)
    for key, value in base.items():
        assert env[key] == value


# === cwd containment (§(l)) ===================================================


def test_cwd_defaults_to_the_parents(tmp_path: Path) -> None:
    assert resolve_child_cwd(None, str(tmp_path)) == str(tmp_path.resolve())


def test_a_subdirectory_is_allowed(tmp_path: Path) -> None:
    nested = tmp_path / "pkg"
    nested.mkdir()
    assert resolve_child_cwd("pkg", str(tmp_path)) == str(nested.resolve())


def test_an_out_of_tree_cwd_is_an_error_not_a_silent_fallback(
    tmp_path: Path,
) -> None:
    """A silent fallback would run the task somewhere the model did not ask for
    and tell nobody."""

    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="outside"):
        resolve_child_cwd(str(outside), str(tmp_path))


def test_a_traversal_cwd_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        resolve_child_cwd("../..", str(tmp_path))


def test_a_nonexistent_cwd_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        resolve_child_cwd("ghost", str(tmp_path))


def test_the_runtime_returns_an_error_envelope_for_a_bad_cwd(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, _HAPPY)
    result = asyncio.run(runtime.spawn(_resolved(), "go", cwd="/etc"))
    assert result.ok is False
    assert result.status == "error"
    assert "outside" in result.summary


# === Single mode only (non-negotiable 4) ======================================


@pytest.mark.parametrize("mode", ["parallel", "chain"])
async def test_parallel_and_chain_modes_are_rejected(tmp_path: Path, mode: str) -> None:
    """Still a raise after P3 — but for a different reason (§3.8).

    P3 ships parallel and chain, so "mode is P3" is no longer true. The seam
    keeps refusing them because a topology is not a per-spawn property: the
    extension's batch executor composes them by calling this method once per
    member with ``mode="single"``. Passing ``mode="parallel"`` here is a
    programming error in a runtime author's code, and it must stay loud.
    """

    runtime = _runtime(tmp_path, _HAPPY)
    with pytest.raises(ValueError, match="one spawn is one child"):
        await runtime.spawn(_resolved(), "go", mode=mode)  # pyright: ignore[reportArgumentType]


async def test_background_true_is_rejected_in_p2(tmp_path: Path) -> None:
    """The Protocol carries ``background`` for P3 shape stability only.

    A background child has no owner to reap it and no channel to report
    through, which is the same "task that outlives the session" ADR-0197 bans.
    """

    runtime = _runtime(tmp_path, _HAPPY)
    with pytest.raises(ValueError, match="background"):
        await runtime.spawn(_resolved(), "go", background=True)


async def test_spawn_granted_fails_closed_without_a_grant(tmp_path: Path) -> None:
    """The anti-bypass invariant (§(i)).

    ``execute()`` pops the grant with a ``None`` default. A call that reached the
    runtime without one skipped the only gate there is, so the correct response
    is a refusal — never a spawn under the clamp, which would make the gate
    optional.
    """

    runtime = _runtime(tmp_path, _HAPPY)
    result = await runtime.spawn_granted(None, _resolved(), "go")  # pyright: ignore[reportArgumentType]
    assert result.status == "declined"
    assert result.ok is False
    assert runtime.list() == []


# === Real aelix children — project trust (§(g)) ===============================


def _write_profile(tmp_path: Path, **kwargs: Any) -> ResolvedProfile:
    path = tmp_path / "scout.md"
    path.write_text("You are a scout.", encoding="utf-8")
    return _resolved(_profile(file_path=str(path), tools=("read",), **kwargs))


async def _run_real_child(
    tmp_path: Path,
    *,
    child_cwd: Path,
    parent_cwd: Path,
    argv_builder: Any = None,
    resolved: ResolvedProfile | None = None,
) -> Any:
    env = _hermetic_env(tmp_path)
    channel = PrintChannel(
        grace=1.0,
        argv_builder=argv_builder,
        env_builder=lambda profile: build_child_env(profile, base=env),
    )
    plan = _plan(
        tmp_path,
        resolved=resolved if resolved is not None else _write_profile(tmp_path),
        cwd=str(child_cwd),
        parent_cwd=str(parent_cwd),
        timeout_ms=90_000,
    )
    return await asyncio.wait_for(channel.run(plan), 120)


def _monorepo(tmp_path: Path, marker: Path) -> tuple[Path, Path]:
    """A repo root with NOTHING executable, and a vendored tree that has one.

    This is the ONE measured escalation ``--no-approve`` exists for: the parent
    at the root has never loaded ``vendor/sdk/.aelix/extensions/*.py``
    (``extensions/loader.py`` scans ``cwd/.aelix/extensions`` only), the root's
    persisted trust says ``True``, and ``ProjectTrustStore``'s nearest-ancestor
    walk hands that ``True`` to a child started three directories down.
    """

    root = tmp_path / "repo"
    nested = root / "vendor" / "sdk"
    ext_dir = nested / ".aelix" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "telemetry.py").write_text(
        f"open({str(marker)!r}, 'w').write('executed')\n"
        "def setup(api):\n    return None\n",
        encoding="utf-8",
    )
    agent_dir = tmp_path / "home" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "trust.json").write_text(
        json.dumps({str(root.resolve()): True}), encoding="utf-8"
    )
    return root, nested


async def test_nested_project_extension_not_executed_in_relocated_child(
    tmp_path: Path,
) -> None:
    """§(g) clause 2, against a REAL child.

    ``inherit_extensions: true`` on purpose: with the shipped default
    (``False``, ``agents/profile.py:199-203``) ``profile_to_flags`` already
    emits ``--no-extensions`` and NO project extension is discovered at all, so
    the trust flag would be untestable. This is the one profile shape where
    clause 2 is the thing standing between the child and the vendored code —
    and it is exactly the residual the ADR names.
    """

    marker = tmp_path / "executed.txt"
    root, nested = _monorepo(tmp_path, marker)
    await _run_real_child(
        tmp_path,
        child_cwd=nested,
        parent_cwd=root,
        resolved=_write_profile(tmp_path, inherit_extensions=True),
    )
    assert not marker.exists()


async def test_the_relocated_child_would_execute_it_without_the_flag(
    tmp_path: Path,
) -> None:
    """The control that makes the test above non-vacuous.

    Strips ``--no-approve`` from the very same argv. If the nested extension
    does not run here either, then the previous test proves nothing about the
    flag — it would only prove that nested extensions never load.

    This is the measured escalation itself: the parent at the root has never
    executed ``vendor/sdk/.aelix/extensions/telemetry.py``
    (``extensions/loader.py`` scans ``cwd/.aelix/extensions`` only), and yet a
    child started three directories down inherits the root's persisted trust and
    runs it.
    """

    marker = tmp_path / "executed.txt"
    root, nested = _monorepo(tmp_path, marker)

    def _without_the_flag(*args: Any, **kwargs: Any) -> list[str]:
        return [a for a in pc.build_child_argv(*args, **kwargs) if a != "--no-approve"]

    await _run_real_child(
        tmp_path,
        child_cwd=nested,
        parent_cwd=root,
        argv_builder=_without_the_flag,
        resolved=_write_profile(tmp_path, inherit_extensions=True),
    )
    assert marker.exists(), "the escalation this flag exists for did not reproduce"


async def test_same_cwd_child_no_longer_loads_untrusted_project_skills(
    tmp_path: Path,
) -> None:
    """#115 INVERTED THIS TEST, against a real child, and the inversion IS the fix.

    It used to assert ``"repoSkill" in haystack`` — that a skills-only repo
    loads its skills in a child that made no trust decision — because
    ``has_trust_requiring_project_resources`` did not count ``.aelix/skills``.
    #115 gave skills a model-facing channel, so an unprompted load is an
    ungated system-prompt injection. The predicate counts them now, §(g)
    clause 1 therefore stops exempting this case, the child carries
    ``--no-approve``, and the directory is never scanned.

    Observed through a deliberately MALFORMED skill: the child emits a
    ``Warning: skill load:`` naming it if and only if the directory was
    scanned. A bare "the name is absent" assertion would ALSO pass if the child
    died before it got that far, so the trusted rerun below is the positive
    control — same repo, same malformed skill, only the trust decision differs.
    """

    repo = tmp_path / "repo"
    skill = repo / ".aelix" / "skills" / "repoSkill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")

    denied = await _run_real_child(tmp_path, child_cwd=repo, parent_cwd=repo)
    assert "repoSkill" not in f"{denied.summary}\n{denied.details or ''}"

    # POSITIVE CONTROL — the scan must be reachable at all, or "the name is
    # absent" above would be satisfied by any early failure in the child.
    #
    # It takes BOTH levers, and each one alone was measured failing:
    #   - stripping ``--no-approve`` alone still denies. It only moves the
    #     decision from step 1 (explicit override) to step 6, the headless
    #     deny-by-default a child always hits (pi ``project-trust.ts:86-88``).
    #   - a persisted ``trust.json`` alone still denies, because step 1
    #     short-circuits before the store is read at step 4.
    # Together, step 1 is skipped and step 4 answers ``True``, so the same
    # malformed skill IS reported. That pair is also the honest statement of
    # what this fix costs: a headless child cannot load project skills without
    # a trust decision the user already persisted.
    agent_dir = tmp_path / "home" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "trust.json").write_text(
        json.dumps({str(repo.resolve()): True}), encoding="utf-8"
    )

    def _without_the_flag(*args: Any, **kwargs: Any) -> list[str]:
        return [a for a in pc.build_child_argv(*args, **kwargs) if a != "--no-approve"]

    allowed = await _run_real_child(
        tmp_path, child_cwd=repo, parent_cwd=repo, argv_builder=_without_the_flag
    )
    assert "repoSkill" in f"{allowed.summary}\n{allowed.details or ''}"


async def test_a_real_child_without_a_key_is_an_error_envelope_not_a_hang(
    tmp_path: Path,
) -> None:
    """The shape every other real-child test here depends on.

    No provider key → exit 1 with ZERO stdout bytes, so the whole envelope comes
    from the stderr rung. It is also the cheapest end-to-end proof that the
    spawner's argv is accepted by a real ``-m aelix_coding_agent``.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    result = await _run_real_child(tmp_path, child_cwd=repo, parent_cwd=repo)
    assert result.ok is False
    assert result.exit_code == 1
    assert "model" in result.summary.lower()
    assert result.permission_mode == "plan"


# === descendant walk ==========================================================


@linux_only
async def test_descendant_pids_finds_a_grandchild() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import subprocess, sys, time;"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],"
        " start_new_session=True); time.sleep(30)",
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        found: list[int] = []
        while time.monotonic() < deadline:
            found = descendant_pids(proc.pid)
            if found:
                break
            await asyncio.sleep(0.05)
        assert found, "the grandchild was never seen"
        # Deepest first: the grandchild precedes anything it spawned under it.
        assert all(pid != proc.pid for pid in found)
    finally:
        kill_tree(proc, descendant_pids(proc.pid))
        await proc.wait()


def test_descendant_pids_of_an_unknown_pid_is_empty() -> None:
    assert descendant_pids(2**22 - 1) == []


# === The parent's model reaches the child (M1) ================================


async def test_the_default_channel_forwards_the_parents_live_model(
    tmp_path: Path,
) -> None:
    """The seam, end to end: ``SubagentHost.model`` → the channel the runtime
    builds for itself → the child's argv.

    Deliberately does NOT inject a channel. The wiring under test is the one
    ``__post_init__`` does, and every other test in this file bypasses it by
    passing ``channel=``; a pure ``build_child_argv`` test would stay green with
    the host getter connected to nothing at all.

    The getter is called PER SPAWN rather than captured, which is what makes a
    mid-session ``/model`` reach the next delegation.
    """

    seen: list[Any] = []
    models = [
        Model(id="claude-haiku-4-5", provider="anthropic"),
        Model(id="claude-opus-5", provider="anthropic"),
    ]

    def _capture(*_args: Any, parent_model: Any = None, **_kwargs: Any) -> list[str]:
        seen.append(parent_model)
        return [sys.executable, "-c", _HAPPY]

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), model=lambda: models[len(seen)])
    )
    assert runtime.channel is not None, "__post_init__ must build the channel"
    runtime.channel._argv_builder = _capture  # pyright: ignore[reportAttributeAccessIssue]

    await runtime.spawn(_resolved(), "go")
    await runtime.spawn(_resolved(), "again")

    assert [m.id for m in seen] == ["claude-haiku-4-5", "claude-opus-5"]


async def test_an_unwired_host_forwards_nothing(tmp_path: Path) -> None:
    """``SubagentHost.model`` defaults to ``lambda: None`` — a host that never
    wired it leaves the child to its own cascade, exactly as before."""

    seen: list[Any] = []

    def _capture(*_args: Any, parent_model: Any = None, **_kwargs: Any) -> list[str]:
        seen.append(parent_model)
        return [sys.executable, "-c", _HAPPY]

    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: str(tmp_path)))
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture  # pyright: ignore[reportAttributeAccessIssue]

    await runtime.spawn(_resolved(), "go")
    assert seen == [None]
