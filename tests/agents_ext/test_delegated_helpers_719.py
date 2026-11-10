"""#719 against a REAL child: what a delegated agent's finished ``bash`` call left stops with it.

THE DECISION (owner, 2026-11-07): helpers that a delegated child's ``bash``
detached with ``cmd &``, ``(cmd &)`` or ``nohup cmd &`` stop when that child
ends or is stopped; a top-level session's stay released (#232). Shipped ungated
(owner OQ-A, 2026-11-10), so every POSIX delegation here is armed.

The flow is the product's, as in ``test_delegated_child_orphans_691.py``: a real
parent session in a bucket, the real runtime and ``PrintChannel``, a real
``-m aelix_coding_agent`` child on a built-in adapter against a local
chat-completions server, never a real model. The child's FIRST ``bash`` call
leaves a helper and returns; its second turn either answers (door ``end``) or
starts ``sleep 120`` so the agent is busy when a human ends it (``cancel`` is
Esc / a signal to ``-p``, ``stop`` is ``SubagentRuntime.stop``, ``stop_all`` the
session teardown).

Every shape × door case leaves the helper running on ``478ffd13`` (M1 on the
#611 S2 contract measured a stand-in through cancel, stop and end; the real
child and ``stop_all`` are first measured here). POSIX only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.consent import SpawnGrant
from aelix_agents.print_channel import PrintChannel, build_child_env
from aelix_agents.prompt_file import _pid_is_live
from aelix_agents.runtime import SpawnRecordMeta, SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.agents_ext.test_agent_resume_real_child_610 import _chunk, _ModelServer
from tests.agents_ext.test_child_session_records import _parent
from tests.agents_ext.test_delegated_child_orphans_691 import _models_json
from tests.env_sandbox import child_env
from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="real-child spawn semantics are POSIX"
)
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="a Linux subreaper")
darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS's stated limits")

_PY = shlex.quote(sys.executable)

#: A double fork into a session of its own, its stdio released, its pid written
#: whole; then a long sleep — or, with ``envclear``, ``/bin/sleep`` exec'd with
#: an EMPTY environment, so neither its group nor the marker names it.
_DFORK = (
    "import os, shutil, sys, time\n"
    "f, clear = sys.argv[1], sys.argv[2] == '1'\n"
    "if os.fork(): os._exit(0)\n"
    "os.setsid()\n"
    "if os.fork(): os._exit(0)\n"
    "null = os.open(os.devnull, os.O_RDWR)\n"
    "for fd in (0, 1, 2): os.dup2(null, fd)\n"
    "open(f + '.tmp', 'w').write(str(os.getpid()))\n"
    "os.rename(f + '.tmp', f)\n"
    "if clear: os.execve(shutil.which('sleep'), ['sleep', '300'], dict())\n"
    "time.sleep(300)\n"
)

_WRITE = "echo $! > {f}.tmp && mv {f}.tmp {f}"

_SHAPES: dict[str, str] = {
    # ``(cmd &)``: the subshell exits, ``cmd`` keeps the shell's group, PPID 1.
    "subshell-bg": "(sleep 300 >/dev/null 2>&1 & " + _WRITE + ")",
    # ``cmd &`` and ``nohup cmd &``, then the call returns.
    "bg-exit": "sleep 300 >/dev/null 2>&1 & " + _WRITE + "; exit 0",
    "nohup-exit": "nohup sleep 300 >/dev/null 2>&1 & " + _WRITE + "; exit 0",
    # Linux only: out of the group; with ``envclear`` out of the marker too.
    "dfork-setsid": f"{_PY} -c {shlex.quote(_DFORK)} {{f}} 0",
    "dfork-setsid-envclear": f"{_PY} -c {shlex.quote(_DFORK)} {{f}} 1",
}
_POSIX_SHAPES = ("subshell-bg", "bg-exit", "nohup-exit")
_LINUX_SHAPES = ("dfork-setsid", "dfork-setsid-envclear")


class _HelperServer(_ModelServer):
    """The first request gets ``bash(<shape>)``; the next an answer (``end``) or ``bash(<busy>)``."""

    def __init__(self, first: str, then: str | None) -> None:
        super().__init__()
        self.first = first
        self.then = then

    def turn(self, body: dict[str, Any]) -> list[dict[str, Any]]:  # type: ignore[override]
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        calls = sum(1 for m in body.get("messages", []) if m.get("role") == "tool")
        command = self.first if calls == 0 else self.then
        if command is None:
            return [
                _chunk({"role": "assistant", "content": "the helper is running"}),
                _chunk({}, finish="stop", usage=usage),
            ]
        call = {
            "index": 0,
            "id": f"call_bash_719_{calls}",
            "type": "function",
            "function": {"name": "bash", "arguments": json.dumps({"command": command})},
        }
        return [
            _chunk({"role": "assistant", "content": None, "tool_calls": [call]}),
            _chunk({}, finish="tool_calls", usage=usage),
        ]


def _pid_in(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _ppid(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    return int(stat.rpartition(")")[2].split()[1])


def _state(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    return stat.rpartition(")")[2].split()[0]


class _Run:
    """One delegation of the ``runner`` profile through the real runtime, with its helper's pid file."""

    def __init__(self, tmp_path: Path, first: str, then: str | None) -> None:
        self.tmp_path = tmp_path
        self.helper_file = tmp_path / "helper.pid"
        self.busy_file = tmp_path / "busy.pid"
        self.server = _HelperServer(first.format(f=self.helper_file), then)
        self.runtime: _SubagentRuntimeImpl | None = None
        self.delegation: asyncio.Future[Any] | None = None

    async def start(self) -> None:
        home = self.tmp_path / "home"
        agent_dir = home / "agent"
        cwd = self.tmp_path / "repo"
        for directory in (home / ".config", agent_dir, cwd):
            directory.mkdir(parents=True, exist_ok=True)
        self.server.__enter__()
        _models_json(agent_dir, self.server.url)
        env = child_env(
            home,
            XDG_CONFIG_HOME=str(home / ".config"),
            AELIX_CODING_AGENT_DIR=str(agent_dir),
            PI_OFFLINE="1",
            MOCK_KEY="not-a-real-key",
        )
        session, _repo, _parent_path = await _parent(self.tmp_path, cwd=cwd)
        self.runtime = _SubagentRuntimeImpl(
            host=SubagentHost(
                cwd=lambda: str(cwd),
                session=lambda: session,
                active_tools=lambda: ["bash", "read"],
            ),
            channel=PrintChannel(grace=1.0, env_builder=lambda p: build_child_env(p, base=env)),
        )
        profile = AgentProfile(
            name="runner",
            description="Runs one command.",
            body="You are a runner.",
            file_path=str(self.tmp_path / "runner.md"),
            scope="user",
            model="mockm",
            provider="mockp",
        )
        resolved = ResolvedProfile(
            name=profile.name, profile=profile, source_path=profile.file_path, scope=profile.scope
        )
        grant = SpawnGrant(
            profile=profile.name,
            source_path=profile.file_path,
            scope=profile.scope,
            mode=PermissionMode.YOLO,
            widened=False,
            consented=True,
        )
        self.delegation = asyncio.ensure_future(
            self.runtime.spawn_granted(
                grant,
                resolved,
                "run the command",
                record=SpawnRecordMeta(tool_call_id="tc-719", mode="single", index=0),
            )
        )

    async def helper(self) -> int:
        await wait_until(
            lambda: _pid_in(self.helper_file) is not None,
            timeout=90,
            what="the child's first bash call to leave its helper",
        )
        pid = _pid_in(self.helper_file)
        assert pid is not None and _pid_is_live(pid), pid
        return pid

    async def busy(self) -> None:
        await wait_until(
            lambda: _pid_in(self.busy_file) is not None,
            timeout=60,
            what="the child's second bash call to start",
        )

    def agent_pid(self) -> int:
        assert self.runtime is not None
        (row,) = self.runtime._children.values()
        assert row.proc is not None
        return int(row.proc.pid)

    async def end(self, door: str) -> None:
        runtime, delegation = self.runtime, self.delegation
        assert runtime is not None and delegation is not None
        if door == "end":
            result = await asyncio.wait_for(delegation, 60)
            assert result.ok, result
            return
        (row,) = runtime._children.values()
        if door == "cancel":
            delegation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await delegation
        elif door == "stop":
            await runtime.stop(row.id)
            result = await asyncio.wait_for(delegation, 30)
            assert result.status == "aborted", result
        else:
            await runtime.stop_all()
            result = await asyncio.wait_for(delegation, 30)
            assert result.status == "aborted", result

    async def close(self, *extra: int | None) -> None:
        if self.delegation is not None:
            self.delegation.cancel()
            with contextlib.suppress(BaseException):
                await self.delegation
        if self.runtime is not None:
            await self.runtime.stop_all()
        for pid in (_pid_in(self.helper_file), _pid_in(self.busy_file), *extra):
            if pid is not None:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.server.__exit__(None, None, None)


def _busy(run_file: Path) -> str:
    return f"echo $$ > {run_file}.tmp && mv {run_file}.tmp {run_file}; sleep 120"


@pytest.mark.parametrize("door", ["end", "cancel", "stop", "stop_all"])
@pytest.mark.parametrize(
    "shape",
    [
        *_POSIX_SHAPES,
        *(pytest.param(s, marks=linux_only) for s in _LINUX_SHAPES),
    ],
)
async def test_a_finished_calls_helper_stops_with_the_delegation(
    tmp_path: Path, shape: str, door: str
) -> None:
    """N, then I until run (r3 §7 S2c). The helper is left by a ``bash`` call that RETURNED.

    Red on ``478ffd13`` on every row: the helper is still alive 10 s after the
    delegation ended (M1's shape, here through a real child): nothing walked
    names it once its shell exited, and no kill reaches its group.
    """

    then = None if door == "end" else _busy(tmp_path / "busy.pid")
    run = _Run(tmp_path, _SHAPES[shape], then)
    helper: int | None = None
    try:
        await run.start()
        helper = await run.helper()
        if door != "end":
            await run.busy()
        await run.end(door)
        await wait_until(
            lambda: not _pid_is_live(helper),
            timeout=10,
            what=f"the {shape} helper {helper} to stop with the delegation ({door})",
        )
    finally:
        await run.close(helper)


async def test_a_top_level_bash_calls_helper_is_still_released(tmp_path: Path) -> None:
    """G (#232): an unarmed process — the top-level aelix, REPL ``!``, RPC ``exec`` — releases it.

    The same ``bash`` backend, in this (unarmed) process: the call returns and
    its ``(cmd &)`` helper is still running afterwards. Green on ``478ffd13``.
    """

    from aelix_coding_agent.tools.bash import create_local_bash_operations

    f = tmp_path / "top.pid"
    chunks: list[bytes] = []
    result = await create_local_bash_operations().exec(
        _SHAPES["subshell-bg"].format(f=f), str(tmp_path), on_data=chunks.append
    )
    helper = _pid_in(f)
    try:
        assert result.exit_code == 0, chunks
        assert helper is not None
        await asyncio.sleep(0.5)
        assert _pid_is_live(helper), "a top-level call's helper is released, not stopped"
    finally:
        if helper is not None:
            with contextlib.suppress(OSError):
                os.kill(helper, signal.SIGKILL)


@darwin_only
async def test_macos_an_in_group_platform_binary_is_killed_by_the_oq_d_rule(tmp_path: Path) -> None:
    """N: ``/bin/sleep`` left in its finished shell's group is killed, though nothing verifies it.

    M4's premise, re-read here: a macOS platform binary's environment reads
    empty to its owner, so the marker cannot name it. Its shell has exited, so
    no live leader gives authority either. The ledger group is killed by the
    owner's OQ-D answer (2026-11-10): a dead leader's group, not retired, still
    non-empty, with no verifiable member. Fails on ``478ffd13``: there is no
    ``aelix_coding_agent.tools.launch_ledger`` (``ModuleNotFoundError``), and a
    delegation there leaves this helper running (the row above).
    """

    from aelix_ai.utils import _process_env
    from aelix_coding_agent.tools import launch_ledger

    f = tmp_path / "plat.pid"
    env = {**os.environ, launch_ledger.MARKER_ENV: "tok-plat"}
    shell = subprocess.Popen(  # noqa: S603
        ["/bin/bash", "-c", f"(/bin/sleep 300 >/dev/null 2>&1 & echo $! > {f})"],
        env=env,
        start_new_session=True,
    )
    from aelix_ai.utils._process_identity import process_started

    started = process_started(shell.pid)
    shell.wait()
    helper = _pid_in(f)
    try:
        assert helper is not None and _pid_is_live(helper)
        assert _process_env.environ_value(helper, launch_ledger.MARKER_ENV) in (None, "")
        assert launch_ledger.marker_hits("tok-plat", since=None, exclude=set()) == []
        launch = launch_ledger.Launch(1, shell.pid, shell.pid, started)
        done = launch_ledger.sweep_groups([launch], retired=())
        assert done.groups == [shell.pid], done
        await wait_until(lambda: not _pid_is_live(helper), timeout=5, what="the platform binary to die")
    finally:
        if helper is not None:
            with contextlib.suppress(OSError):
                os.kill(helper, signal.SIGKILL)


@darwin_only
async def test_macos_an_escaped_env_cleared_helper_is_the_stated_residual(tmp_path: Path) -> None:
    """G: on macOS a helper that left its group AND cleared its environment outlives the delegation.

    The decided limit (owner, 2026-11-07; ADR-0296): no subreaper there, so it is
    reparented to launchd; its group is not the ledger's and its environment
    holds no marker. Asserts that it SURVIVES — green on ``478ffd13`` and after.
    """

    run = _Run(tmp_path, _SHAPES["dfork-setsid-envclear"], None)
    helper: int | None = None
    try:
        await run.start()
        helper = await run.helper()
        await run.end("end")
        await asyncio.sleep(1.0)
        assert _pid_is_live(helper), "the stated macOS residual: it is not found"
    finally:
        await run.close(helper)


@linux_only
async def test_a_delegated_agent_is_a_subreaper_and_its_stop_takes_an_escaped_env_cleared_helper(
    tmp_path: Path,
) -> None:
    """N (Linux): the double-forked, ``setsid``, env-cleared helper is the AGENT's child, and its stop takes it.

    Red on ``478ffd13``: the helper's parent is init (PPID 1 in docker
    ``--init``), not the agent, and it is alive after the stop (M1, M2).
    """

    run = _Run(tmp_path, _SHAPES["dfork-setsid-envclear"], _busy(tmp_path / "busy.pid"))
    helper: int | None = None
    try:
        await run.start()
        helper = await run.helper()
        await run.busy()
        agent = run.agent_pid()
        assert _ppid(helper) == agent, (helper, _ppid(helper), agent)
        await run.end("stop")
        await wait_until(lambda: not _pid_is_live(helper), timeout=10, what="the escaped helper to die")
    finally:
        await run.close(helper)


_SHORT_LIVED = (
    "import os, sys\n"
    "f = sys.argv[1]\n"
    "if os.fork(): os._exit(0)\n"
    "os.setsid()\n"
    "pid = os.fork()\n"
    "if pid:\n"
    "    open(f + '.tmp', 'w').write(str(pid))\n"
    "    os.rename(f + '.tmp', f)\n"
    "    os._exit(0)\n"
    "os._exit(0)\n"
)


@linux_only
async def test_adopted_zombies_are_held_until_the_agent_exits_and_then_gone(tmp_path: Path) -> None:
    """N (Linux), asserting the stated residual (the lead's call: no reaping during the run).

    A double fork whose grandchild exits at once: the agent, a subreaper, adopts
    it and reaps nothing it did not spawn, so it stays a zombie under the agent
    (``Z``, PPID = the agent) while the agent runs, and is gone once the agent
    has been stopped. Red on ``478ffd13``: the grandchild goes to init, which
    reaps it — ``/proc/<pid>`` is gone before the first read.
    """

    first = f"{_PY} -c {shlex.quote(_SHORT_LIVED)} {{f}}"
    run = _Run(tmp_path, first, _busy(tmp_path / "busy.pid"))
    try:
        await run.start()
        zombie = await _written(run.helper_file)
        await run.busy()
        agent = run.agent_pid()
        await asyncio.sleep(0.5)
        assert _state(zombie) == "Z" and _ppid(zombie) == agent, (_state(zombie), _ppid(zombie), agent)
        await run.end("stop")
        await wait_until(lambda: _state(zombie) is None, timeout=10, what="the held zombie to be reaped")
    finally:
        await run.close()


async def _written(path: Path) -> int:
    await wait_until(lambda: _pid_in(path) is not None, timeout=90, what=f"{path.name} to be written")
    pid = _pid_in(path)
    assert pid is not None
    return pid


_FORK_LOOP = (
    "import os, sys, time\n"
    "while True:\n"
    "    if os.fork() == 0:\n"
    "        os.setsid()\n"
    "        os.execve('/bin/sleep', ['sleep', sys.argv[1]], dict())\n"
    "    time.sleep(0.001)\n"
)

_EXIT_PASSES_STANDIN = (
    "import asyncio, subprocess, sys, time\n"
    "from aelix_coding_agent.tools import launch_ledger as ll\n"
    "armed = ll.arm_from_environment()\n"
    "assert armed is not None and armed.subreaper is True, armed\n"
    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]], start_new_session=True,\n"
    "                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "time.sleep(0.5)\n"
    "asyncio.run(ll.exit_cleanup())\n"
    "print('cleaned', flush=True)\n"
)


def _tagged(tag: str) -> list[int]:
    """Live (non-zombie) ``sleep <tag>`` processes, read from ``/proc``."""

    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if argv[:2] == [b"sleep", tag.encode()]:
            found.append(int(entry.name))
    return found


@linux_only
def test_the_exit_cleanup_takes_a_child_forked_by_a_process_killed_in_its_first_pass(tmp_path: Path) -> None:
    """N (Linux): a helper forking a ``setsid``, env-cleared child every 1 ms; the agent's exit leaves none.

    M5's fork loop (the #611 S2 contract) under an armed stand-in agent: what
    the helper forks between pass 1's walk and its kill is orphaned to the
    agent — a subreaper — and named by pass 2 (r3 §3.3; at most 3 passes, the
    lead's call). Fails on ``478ffd13``: no ``aelix_coding_agent.tools.launch_ledger``
    (``ModuleNotFoundError`` before anything is started).
    """

    from aelix_coding_agent.tools import launch_ledger

    tag = str(7000 + os.getpid() % 1000)
    ledger = tmp_path / "loop.launch-ledger"
    assert launch_ledger.create(str(ledger), run="sub-loop")
    env = {**os.environ, launch_ledger.LEDGER_ENV: str(ledger)}
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _EXIT_PASSES_STANDIN, _FORK_LOOP, tag],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        exit_line = json.loads(ledger.read_text().splitlines()[-1])
        assert exit_line["t"] == "exit" and exit_line["killed"] > 0, exit_line
        survivors = _tagged(tag)
        assert survivors == [], f"survivors {survivors}; exit {exit_line}"
    finally:
        for pid in _tagged(tag):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


_NO_STRAY_WAIT = (
    "import asyncio, ctypes, json, os, sys\n"
    "waited = []\n"
    "real = os.waitpid\n"
    "def spy(pid, options):\n"
    "    waited.append(pid)\n"
    "    return real(pid, options)\n"
    "os.waitpid = spy\n"
    "try:\n"
    "    from aelix_coding_agent.tools import launch_ledger as ll\n"
    "    armed = ll.arm_from_environment()\n"
    "except ImportError:\n"
    "    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)\n"
    "from aelix_coding_agent.tools.bash import create_local_bash_operations\n"
    "async def main():\n"
    "    out = []\n"
    "    await create_local_bash_operations().exec(sys.argv[1], '/', on_data=out.append)\n"
    "    await asyncio.sleep(2.5)\n"
    "    shell, orphan = (int(x) for x in b''.join(out).split())\n"
    "    stat = open(f'/proc/{orphan}/stat').read().rpartition(')')[2].split()\n"
    "    print(json.dumps({'me': os.getpid(), 'shell': shell, 'orphan': orphan,\n"
    "                      'state': stat[0], 'ppid': int(stat[1]), 'waited': waited}))\n"
    "asyncio.run(main())\n"
)


@linux_only
def test_no_pid_the_agent_did_not_spawn_is_ever_waited(tmp_path: Path) -> None:
    """G (the lead's call: no reaping during the run). A subreaper agent's adopted orphan stays a zombie.

    A stand-in agent — armed where the ledger exists, a bare subreaper on
    ``478ffd13`` — runs one ``bash`` call that leaves an orphan which exits at
    once, then lets a retire tick pass. The orphan is still ``Z`` under it, and
    the only pid it waited for is the shell it spawned. Green on ``478ffd13``
    and after.
    """

    ledger = tmp_path / "wait.launch-ledger"
    ledger.write_text('{"v":1,"t":"open","run":"sub-wait","parent":1}\n')
    command = (
        f"echo $$; {_PY} -c "
        + shlex.quote("import os\nif os.fork() == 0:\n    print(os.getpid(), flush=True)\n    os._exit(0)\n")
        + "; sleep 0.3"
    )
    env = {**os.environ, "AELIX_LAUNCH_LEDGER": str(ledger)}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _NO_STRAY_WAIT, command],
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    seen = json.loads(proc.stdout.decode("utf-8", errors="replace").splitlines()[-1])
    assert seen["state"] == "Z" and seen["ppid"] == seen["me"], seen
    assert set(seen["waited"]) <= {seen["shell"]}, seen


# ── fix round 1 ───────────────────────────────────────────────────


def test_an_armed_child_that_returns_early_still_writes_its_exit_line(tmp_path: Path) -> None:
    """N (fix round 1, item 10): an ``Error:`` route failure between arming and print mode.

    A real ``-m aelix_coding_agent`` armed with a ledger, asked for a model no
    adapter can run: it prints ``Error:`` and returns 1 from one of
    ``cli/entry.py``'s early returns. Its exit cleanup still runs (the hook
    arming registers), so the parent reads an ``exit`` line, not "the agent
    ended without its own cleanup". Fails on ``7d298a8d``: no ``exit`` line.
    """

    from aelix_coding_agent.tools import launch_ledger

    home = tmp_path / "home"
    (home / "agent").mkdir(parents=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    ledger = tmp_path / "early.launch-ledger"
    assert launch_ledger.create(str(ledger), run="sub-early")
    env = child_env(
        home,
        AELIX_CODING_AGENT_DIR=str(home / "agent"),
        PI_OFFLINE="1",
        AELIX_LAUNCH_LEDGER=str(ledger),
        AELIX_DELEGATION_MARKER="tok-early",
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "aelix_coding_agent", "--provider", "nosuchprovider", "--model", "x", "-p", "hi"],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 1 and b"Error:" in proc.stderr, proc.stderr[-2000:]
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [r["t"] for r in records][-1] == "exit", records
    assert records[-1]["cleanup"] == "confirmed", records[-1]


async def _kept_ledger(tmp_path: Path) -> tuple[Path, str, _Run]:
    """A real child's session-side ledger, left unverified (a damaged line) by a stop, so it is kept."""

    run = _Run(tmp_path, _SHAPES["subshell-bg"], _busy(tmp_path / "busy.pid"))
    await run.start()
    await run.helper()
    await run.busy()
    assert run.runtime is not None
    (row,) = run.runtime._children.values()
    assert row.tracking is not None and row.tracking.beside_session and row.tracking.ledger
    ledger = Path(row.tracking.ledger)
    token = row.tracking.token
    assert token.encode() not in ledger.read_bytes(), "the token, mid-run"
    with ledger.open("ab") as handle:
        handle.write(b'{"v":1,"t":"lau\n')
    await run.end("stop")
    return ledger, token, run


async def test_the_marker_is_never_written(tmp_path: Path) -> None:
    """N (contract r2 §3.2, verify F8): the run's token is in no ledger byte — mid-run, or as kept after a sweep."""

    ledger, token, run = await _kept_ledger(tmp_path)
    try:
        body = ledger.read_bytes()
        assert b'"t":"swept"' in body and token.encode() not in body
    finally:
        await run.close()


async def test_a_session_ledger_outlives_every_remover_and_is_kept_when_unverified(tmp_path: Path) -> None:
    """N (contract r2 §3.1, verify F8): kept beside the session through ``stop_all`` and the stale sweep.

    Its ``swept`` line says unverified and why; nothing kills from it. The
    prompt directory, which the stale sweep and ``stop_all`` remove, never held it.
    """

    from aelix_agents.prompt_file import sweep_stale_prompt_dirs

    ledger, _token, run = await _kept_ledger(tmp_path)
    try:
        assert run.runtime is not None
        await run.runtime.stop_all()
        sweep_stale_prompt_dirs()
        assert ledger.is_file()
        swept = json.loads(ledger.read_text().splitlines()[-1])
        assert swept["t"] == "swept" and swept["confirmed"] is False
        assert "the launch ledger is damaged" in swept["reason"], swept
    finally:
        await run.close()
