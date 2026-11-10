"""#719's launch ledger, its writer, its authority rule and its marker scan (r2 §3.1–3.2, r3 §3.2–3.5; ADR-0296).

Each test drives the real module — ``aelix_coding_agent.tools.launch_ledger`` —
and real processes. None of it exists on ``478ffd13``: every test here fails
there with ``ModuleNotFoundError`` on that import, except the one guard that
says so (``test_an_unarmed_command_still_inherits_the_protocol_variable``,
green there). The behaviour each N test asserts is named in its docstring.

An armed writer in THIS process is armed with the subreaper off
(``_no_subreaper``): a pytest process made a subreaper would adopt every orphan
of the session's other tests and reap none of them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no launch ledger on win32 (r2 §3.6)")


def _ll() -> Any:
    from aelix_coding_agent.tools import launch_ledger

    return launch_ledger


@pytest.fixture
def armed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """This process armed against a fresh ledger, its subreaper off; disarmed after."""

    ll = _ll()
    path = tmp_path / "run.launch-ledger"
    assert ll.create(str(path), run="sub-test")
    monkeypatch.setattr(ll, "_set_subreaper", lambda: None)
    env = {ll.LEDGER_ENV: str(path)}
    writer = ll.arm_from_environment(env)
    assert writer is not None and ll.LEDGER_ENV not in env
    try:
        yield writer
    finally:
        ll.disarm()


def _records(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _alive(pid: int) -> bool:
    from aelix_agents.prompt_file import _pid_is_live

    return _pid_is_live(pid)


def _kill(*pids: int | None) -> None:
    for pid in pids:
        if pid is not None:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


async def _exec(command: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    from aelix_coding_agent.tools.bash import create_local_bash_operations

    chunks: list[bytes] = []
    await create_local_bash_operations().exec(command, str(cwd), on_data=chunks.append, env=env)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _leader(command: str, **kwargs: Any) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", command], start_new_session=True, **kwargs
    )


# ── the writer ──────────────────────────────────────────────────


async def test_intent_is_complete_before_the_spawn(
    armed: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A/N: the ``intent`` line is whole in the file when ``Popen`` runs; ``launch`` follows it.

    Mutant: write ``intent`` after the spawn — the read inside the spawn then
    finds no ``intent``.
    """

    import aelix_coding_agent.tools.bash as bash

    seen: list[list[dict[str, Any]]] = []
    real = bash._popen_shell

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(_records(armed.path))
        return real(*args, **kwargs)

    monkeypatch.setattr(bash, "_popen_shell", _spy)
    await _exec("true", tmp_path)
    (at_spawn,) = seen
    assert [r["t"] for r in at_spawn] == ["open", "armed", "intent"]
    after = _records(armed.path)
    launch = next(r for r in after if r["t"] == "launch")
    assert launch["n"] == at_spawn[-1]["n"] and launch["pgid"] == launch["pid"]


@pytest.mark.parametrize("fault", [False, True])
async def test_a_launch_write_failure_keeps_the_process_owned(
    armed: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: bool
) -> None:
    """N (Codex 4; the lead's call): a group whose ``launch`` line is lost is stopped when its call returns.

    Healthy control (``fault=False``): the ``launch`` line is written and the
    ``cmd &`` helper is released with its call, the ledger's to stop at the
    agent's end. Faulted: the line cannot be written, so only this process
    would know the group and a stop kills this process first; the call does
    not release it — the helper is dead when ``exec`` returns, and the ledger
    reads damaged. Fails on ``7d298a8d`` on its first assertion: "waited 5s
    for the unrecorded group's helper to die" — the faulted helper outlives
    the call (and then a stop, Codex's native control).
    """

    ll = _ll()
    real = ll._write_all

    def _fail_launch(fd: int, data: bytes) -> None:
        if fault and b'"t":"launch"' in data:
            raise OSError(28, "No space left on device")
        real(fd, data)

    monkeypatch.setattr(ll, "_write_all", _fail_launch)
    out = await _exec("sleep 300 >/dev/null 2>&1 & echo $!", tmp_path)
    helper = int(out.split()[-1])
    try:
        kinds = [r["t"] for r in _records(armed.path)]
        if not fault:
            assert _alive(helper) and "launch" in kinds and not armed.damaged
            return
        # The behaviour first: on ``7d298a8d`` this is what fails — the helper
        # outlives the call. The writer's bookkeeping is read after it.
        await wait_until(lambda: not _alive(helper), timeout=5, what="the unrecorded group's helper to die")
        assert armed.damaged and "launch" not in kinds and kinds[-1] == "damaged", kinds
        assert ll.read(armed.path).damaged
        assert armed.held == {}, "a held group is stopped and dropped at its call's end"
    finally:
        _kill(helper)


async def test_an_intent_write_failure_still_runs_the_command_unverified(
    armed: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N: no ``intent`` could be written; the command runs anyway and the parent's reading is unverified."""

    from aelix_agents.print_channel import _reading_reasons

    ll = _ll()
    real = ll._write_all

    def _fail_intent(fd: int, data: bytes) -> None:
        if b'"t":"intent"' in data:
            raise OSError(5, "I/O error")
        real(fd, data)

    monkeypatch.setattr(ll, "_write_all", _fail_intent)
    assert (await _exec("echo ran", tmp_path)).strip() == "ran"
    reasons = _reading_reasons(ll.read(armed.path), parent_stopped=True)
    assert ll.DAMAGED in reasons, reasons


def test_a_damaged_middle_line_reads_unverified(tmp_path: Path) -> None:
    """N: a line that does not parse makes the reading damaged; the lines around it are still acted on."""

    ll = _ll()
    path = tmp_path / "x.launch-ledger"
    lines = [
        {"v": 1, "t": "open", "run": "k", "parent": 1},
        {"v": 1, "t": "armed", "pid": 2, "subreaper": True},
        {"v": 1, "t": "intent", "n": 1},
    ]
    body = "".join(json.dumps(r) + "\n" for r in lines)
    body += '{"v":1,"t":"lau\n'
    body += json.dumps({"v": 1, "t": "launch", "n": 1, "pid": 9, "pgid": 9, "started": 1.0}) + "\n"
    path.write_text(body)
    reading = ll.read(str(path))
    assert reading.damaged
    assert reading.launches[1].pgid == 9 and not reading.in_flight


# ── authority (r3 §3.5) ───────────────────────────────────────────


def test_a_retired_group_is_never_signalled(tmp_path: Path) -> None:
    """N: a group the agent marked retired is skipped even when a process holds its number again."""

    from aelix_ai.utils._process_identity import process_started

    ll = _ll()
    proc = _leader("sleep 300")
    try:
        launch = ll.Launch(1, proc.pid, proc.pid, process_started(proc.pid))
        done = ll.sweep_groups([launch], retired={1})
        assert done.groups == [] and proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("stamp", ["null", "moved"])
def test_a_null_stamp_live_leader_in_the_ledger_is_never_signalled(tmp_path: Path, stamp: str) -> None:
    """N: a live leader whose recorded start time is unknown, or another one, gives no authority."""

    from aelix_ai.utils._process_identity import process_started

    ll = _ll()
    proc = _leader("sleep 300")
    try:
        now = process_started(proc.pid)
        assert now is not None
        launch = ll.Launch(1, proc.pid, proc.pid, None if stamp == "null" else now + 1)
        done = ll.sweep_groups([launch], retired=())
        assert done.groups == [] and proc.poll() is None
        want = ll.LEADER_UNKNOWN if stamp == "null" else ll.LEADER_MOVED
        assert done.reasons == [f"process group {proc.pid}: {want}"]
    finally:
        proc.kill()
        proc.wait()


async def test_a_dead_leaders_group_with_no_verifiable_member_is_killed(tmp_path: Path) -> None:
    """N (owner OQ-D, 2026-11-10): the shell exited; what it left in its group is killed, unverified.

    The residual ADR-0296 states: had that group emptied after its last retire
    check and its number gone to a new group leader with members, that group
    would be signalled.
    """

    from aelix_ai.utils._process_identity import process_started

    ll = _ll()
    f = tmp_path / "left.pid"
    shell = _leader(f"(sleep 300 >/dev/null 2>&1 & echo $! > {f})")
    started = process_started(shell.pid)
    shell.wait()
    left = int(f.read_text())
    try:
        assert _alive(left)
        done = ll.sweep_groups([ll.Launch(1, shell.pid, shell.pid, started)], retired=())
        assert done.groups == [shell.pid]
        assert await ll.confirm_stopped(done) is None
    finally:
        _kill(left)


# ── the environment ──────────────────────────────────────────────


async def test_an_armed_agent_strips_the_ledger_and_protocol_variables(armed: Any, tmp_path: Path) -> None:
    """N (r2 §3.2): an armed agent's bash commands lose the ledger and the jobs protocol; the marker passes."""

    ll = _ll()
    env = {
        "PATH": os.environ.get("PATH", ""),
        ll.LEDGER_ENV: "/somewhere",
        ll.PROTOCOL_ENV: "1",
        ll.MARKER_ENV: "tok",
    }
    out = await _exec(
        f'echo "L=${{{ll.LEDGER_ENV}-unset}} P=${{{ll.PROTOCOL_ENV}-unset}} M=${{{ll.MARKER_ENV}-unset}}"',
        tmp_path,
        env=env,
    )
    assert out.strip() == "L=unset P=unset M=tok", out


async def test_an_unarmed_command_still_inherits_the_protocol_variable(tmp_path: Path) -> None:
    """G (Codex #11's probe): an unarmed process passes ``AELIX_JOBS_PROTOCOL`` on, as on ``478ffd13``."""

    env = {"PATH": os.environ.get("PATH", ""), "AELIX_JOBS_PROTOCOL": "1"}
    out = await _exec('echo "P=${AELIX_JOBS_PROTOCOL-unset}"', tmp_path, env=env)
    assert out.strip() == "P=1", out


# ── the marker scan (Codex N1) ────────────────────────────────────

_SCANNER = textwrap.dedent(
    """
    import asyncio, os, sys
    from aelix_coding_agent.tools import launch_ledger as ll
    ll._set_subreaper = lambda: None
    if sys.argv[1:] == ["no-own-start-time"]:
        # The agent's own start time unreadable: no age filter, so only the
        # veto of its ancestors keeps them out of the scan.
        real_started = ll.process_started
        ll.process_started = lambda pid: None if pid == os.getpid() else real_started(pid)
    ll.arm_from_environment()
    asyncio.run(ll.exit_cleanup())
    print("survived", os.getpid(), flush=True)
    """
)


@pytest.mark.parametrize("own_start", ["read", "no-own-start-time"])
def test_the_exit_scan_never_signals_the_agent_or_its_ancestors(tmp_path: Path, own_start: str) -> None:
    """N (Codex N1, ``marker_self``): the agent, its parent and its grandparent hold the token; all are spared.

    A Python grandparent starts a Python parent, which starts the armed
    scanner, all with the run's token in their environment; the scanner runs
    its exit cleanup and must live to print after it, and each ancestor must
    live to report its child's exit. Mutants: drop the exclusion — the
    scanner SIGKILLs itself and its ancestors (exit -9); walk no further than
    the parent — the grandparent is killed.
    """

    ll = _ll()
    path = tmp_path / "self.launch-ledger"
    assert ll.create(str(path), run="sub-self")
    token = "tok-self-" + os.urandom(4).hex()
    env = {**os.environ, ll.LEDGER_ENV: str(path), ll.MARKER_ENV: token}
    parent = (
        "import subprocess, sys\n"
        "r = subprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2]])\n"
        "print('parent', r.returncode, flush=True)\n"
    )
    # A grandparent holding the token too: an ancestor two levels up.
    grandparent = (
        "import subprocess, sys\n"
        "r = subprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]])\n"
        "print('grandparent', r.returncode, flush=True)\n"
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", grandparent, parent, _SCANNER, own_start],
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    out = proc.stdout.decode("utf-8", errors="replace").split()
    assert proc.returncode == 0, (proc.returncode, out, proc.stderr[-2000:])
    assert out[0] == "survived" and out[-4:] == ["parent", "0", "grandparent", "0"], out
    exit_line = _records(path)[-1]
    assert exit_line["t"] == "exit" and exit_line["cleanup"] == "confirmed", exit_line


async def test_the_parent_scan_matches_only_its_own_run_token(tmp_path: Path) -> None:
    """N: two runs' helpers; the sweep for one kills its own and leaves the other's (r3 §3.5)."""

    ll = _ll()
    sleeper = [sys.executable, "-c", "import time; time.sleep(300)"]
    mine = subprocess.Popen(sleeper, env={**os.environ, ll.MARKER_ENV: "other,tok-mine"})  # noqa: S603
    theirs = subprocess.Popen(sleeper, env={**os.environ, ll.MARKER_ENV: "tok-theirs"})  # noqa: S603
    # A token that merely CONTAINS this run's is another run's.
    longer = subprocess.Popen(sleeper, env={**os.environ, ll.MARKER_ENV: "tok-mine2"})  # noqa: S603
    try:
        await asyncio.sleep(0.3)
        hits = ll.marker_hits("tok-mine", since=None, exclude=ll.ancestry())
        assert [h.pid for h in hits] == [mine.pid]
        done = ll.kill_hits(hits)
        assert await ll.confirm_stopped(done) is None
        assert mine.wait(timeout=5) == -signal.SIGKILL
        assert theirs.poll() is None and longer.poll() is None
    finally:
        for proc in (mine, theirs, longer):
            proc.kill()
            proc.wait()


# ── the parent's sweep (r2 §3.1, r3 §3.4) ─────────────────────────


async def test_an_agent_that_crashes_reads_unverified(tmp_path: Path) -> None:
    """N (the stated residual): an armed ledger with no ``exit`` line, the agent not stopped by aelix."""

    from aelix_agents.print_channel import HelperTracking, sweep_helpers

    ll = _ll()
    path = tmp_path / "crash.launch-ledger"
    assert ll.create(str(path), run="sub-crash")
    ll.append(str(path), {"t": "armed", "pid": 1, "subreaper": True})
    tracking = HelperTracking(token="tok-crash", ledger=str(path), beside_session=True)
    answer = await sweep_helpers(tracking, parent_stopped=False)
    assert not answer.confirmed
    assert ll.NO_EXIT_CLEANUP in (answer.reason or "")
    kept = _records(path)
    assert kept[-1]["t"] == "swept" and kept[-1]["confirmed"] is False


async def test_a_confirmed_sweep_deletes_the_session_ledger(tmp_path: Path) -> None:
    """N (r2 §3.1): nothing left, the agent cleaned up — the file beside the session is removed."""

    from aelix_agents.print_channel import HelperTracking, sweep_helpers

    ll = _ll()
    path = tmp_path / "ok.launch-ledger"
    assert ll.create(str(path), run="sub-ok")
    ll.append(str(path), {"t": "armed", "pid": 1, "subreaper": True if sys.platform == "linux" else None})
    ll.append(str(path), {"t": "exit", "cleanup": "confirmed", "reason": None, "killed": 0})
    tracking = HelperTracking(token="tok-ok-" + os.urandom(4).hex(), ledger=str(path), beside_session=True)
    answer = await sweep_helpers(tracking, parent_stopped=False)
    assert answer.confirmed, answer
    assert not path.exists()


# ── the smaller rules each sweep reads ────────────────────────────


def test_the_run_token_is_the_last_one_a_marker_holds() -> None:
    """N: each delegation APPENDS its token, so the run's own is the last (r2 §3.2)."""

    ll = _ll()
    assert ll.run_token("outer,inner") == "inner"
    assert ll.run_token("") is None and ll.run_token(None) is None


def test_the_parent_appends_its_token_and_drops_an_inherited_ledger() -> None:
    """N: an inherited marker is appended to, never replaced; an inherited ledger path never reaches the child."""

    from aelix_agents.print_channel import HelperTracking, _track_in_env

    ll = _ll()
    env = {ll.MARKER_ENV: "outer", ll.LEDGER_ENV: "/inherited"}
    _track_in_env(env, HelperTracking(token="mine", ledger=None, beside_session=False))
    assert env == {ll.MARKER_ENV: "outer,mine"}
    env = {ll.LEDGER_ENV: "/inherited"}
    _track_in_env(env, HelperTracking(token="mine", ledger="/run.launch-ledger", beside_session=True))
    assert env == {ll.MARKER_ENV: "mine", ll.LEDGER_ENV: "/run.launch-ledger"}


async def test_a_marker_hit_older_than_the_agent_is_not_signalled(tmp_path: Path) -> None:
    """N (r3 §3.5): a process carrying the token that started BEFORE the agent is not this run's."""

    from aelix_ai.utils._process_identity import process_started

    ll = _ll()
    sleeper = [sys.executable, "-c", "import time; time.sleep(300)"]
    old = subprocess.Popen(sleeper, env={**os.environ, ll.MARKER_ENV: "tok-age"})  # noqa: S603
    try:
        await asyncio.sleep(1.1)
        young = subprocess.Popen(sleeper, env={**os.environ, ll.MARKER_ENV: "tok-age"})  # noqa: S603
        try:
            await asyncio.sleep(0.3)
            since = process_started(young.pid)
            hits = ll.marker_hits("tok-age", since=since, exclude=set())
            assert [h.pid for h in hits] == [young.pid]
        finally:
            young.kill()
            young.wait()
    finally:
        old.kill()
        old.wait()


async def test_a_spawn_that_raises_writes_failed(armed: Any, tmp_path: Path) -> None:
    """N (r2 §3.2): a spawn that raises closes its ``intent`` with ``failed``; nothing is left in flight."""

    await _exec("true", tmp_path / "no-such-directory")
    kinds = [r["t"] for r in _records(armed.path)]
    assert kinds[-2:] == ["intent", "failed"], kinds
    assert not _ll().read(armed.path).in_flight


def _reading(*records: dict[str, Any]) -> Any:
    ll = _ll()
    reading = ll.Reading(opened=True)
    for record in records:
        assert ll._fold(reading, json.dumps({"v": 1, **record}).encode())
    return reading


def test_the_parents_reading_names_each_thing_that_keeps_it_unverified() -> None:
    """N (r3 §3.4): a launch in flight, an agent's own unverified exit, and a stop with no ``exit`` line."""

    from aelix_agents.print_channel import _reading_reasons

    ll = _ll()
    sub = True if sys.platform == "linux" else None
    armed = {"t": "armed", "pid": 1, "subreaper": sub}
    flight = _reading(armed, {"t": "intent", "n": 1}, {"t": "exit", "cleanup": "confirmed"})
    assert _reading_reasons(flight, parent_stopped=True) == [ll.IN_FLIGHT]
    own = _reading(armed, {"t": "exit", "cleanup": "unverified", "reason": "pid 9 not confirmed stopped"})
    assert _reading_reasons(own, parent_stopped=False) == ["pid 9 not confirmed stopped"]
    stopped = _reading(armed)
    assert _reading_reasons(stopped, parent_stopped=True) == []
    assert _reading_reasons(stopped, parent_stopped=False) == [ll.NO_EXIT_CLEANUP]
    assert _reading_reasons(_reading(), parent_stopped=True) == [ll.NOT_ARMED]


@pytest.mark.skipif(sys.platform != "linux", reason="the subreaper is Linux's")
def test_on_linux_an_agent_that_is_not_a_subreaper_reads_unverified() -> None:
    """N (r3 §3.4): ``confirmed`` on Linux needs ``armed.subreaper == true``."""

    from aelix_agents.print_channel import _reading_reasons

    ll = _ll()
    reading = _reading({"t": "armed", "pid": 1, "subreaper": False}, {"t": "exit", "cleanup": "confirmed"})
    assert _reading_reasons(reading, parent_stopped=False) == [ll.NOT_SUBREAPER]


def test_the_parent_counts_its_own_stop_legs_as_the_agents_end() -> None:
    """N: a stop, an Esc's eager kill, or a reaper that ran — the parent walked the agent before killing it."""

    from aelix_agents.print_channel import RunningChild, _parent_stopped

    assert not _parent_stopped(RunningChild(id="a", profile="p"))
    assert _parent_stopped(RunningChild(id="b", profile="p", stopped=True))
    assert _parent_stopped(RunningChild(id="c", profile="p", eager_kill=True))
    reaped = RunningChild(id="d", profile="p")
    reaped.reaper_task = object()  # type: ignore[assignment] — only its presence is read
    assert _parent_stopped(reaped), "a reaper that ran is aelix's stop leg too"


async def test_a_group_that_does_not_empty_is_not_confirmed(tmp_path: Path) -> None:
    """N: ``confirm_stopped`` names a group still holding a live member when its bound passes."""

    ll = _ll()
    proc = _leader("sleep 300")
    try:
        why = await ll.confirm_stopped(ll.Sweep(groups=[proc.pid]), bound=0.3)
        assert why == f"process group {proc.pid} not confirmed empty"
    finally:
        proc.kill()
        proc.wait()


def test_a_record_of_another_version_reads_damaged(tmp_path: Path) -> None:
    """N: a line whose ``v`` is not this writer's is not acted on, and the reading says so."""

    ll = _ll()
    path = tmp_path / "v.launch-ledger"
    path.write_text(
        json.dumps({"v": 1, "t": "open", "run": "k", "parent": 1})
        + "\n"
        + json.dumps({"v": 2, "t": "launch", "n": 1, "pid": 9, "pgid": 9, "started": 1.0})
        + "\n"
    )
    reading = ll.read(str(path))
    assert reading.damaged and reading.launches == {}


async def test_a_bash_call_that_left_nothing_is_retired_when_it_returns(armed: Any, tmp_path: Path) -> None:
    """N (r3 §3.3): the end of a ``bash`` call retires its group at once, not at the next 2 s tick."""

    await _exec("true", tmp_path)
    kinds = [r["t"] for r in _records(armed.path)]
    assert kinds[-1] == "retired", kinds
    assert armed.launches == {} and len(armed.retired) == 1


# ── fix round 1: the marker scan's inputs (Codex 1, 2, 7) ─────────

_SELF_SCAN = textwrap.dedent(
    """
    import os
    from aelix_coding_agent.tools import launch_ledger as ll
    from aelix_ai.utils._process_walk import WalkEntry
    identity, started = ll.process_identity, ll.process_started
    calls = {"identity": 0, "started": 0}
    def ident(pid):
        calls["identity"] += 1
        return None if calls["identity"] == 1 else identity(pid)
    def stamp(pid):
        calls["started"] += 1
        return None if calls["started"] == 1 else started(pid)
    ll.process_identity, ll.process_started = ident, stamp
    ll._process_env.own_pids = lambda: [os.getpid()]
    ll._process_env.environ_value = lambda pid, key: "token"
    excluded = ll.ancestry()
    hits = ll.marker_hits("token", since=None, exclude=excluded)
    print("hits", len(hits), flush=True)
    me = WalkEntry(os.getpid(), started(os.getpid()), os.getppid())
    ll.kill_hits([me])  # no exclusion handed in: the veto reads this pid itself
    print("survived", flush=True)
    """
)


def test_the_exit_scan_never_signals_itself_when_its_own_identity_could_not_be_read(tmp_path: Path) -> None:
    """N (Codex 1, its native shape): the first identity reads fail, so ancestry records ``(self, None)``.

    The scan and the kill still never reach this process: it is vetoed by
    NUMBER, and the veto is read again before the signal (a hit naming it
    directly is dropped too). Fails on ``7d298a8d``: the scanner is a hit and
    SIGKILLs itself (``exit=-9``).
    """

    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-B", "-c", _SELF_SCAN],
        cwd=tmp_path,
        start_new_session=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    out = proc.stdout.decode("utf-8", errors="replace").split()
    assert proc.returncode == 0, (proc.returncode, out, proc.stderr[-2000:])
    assert out == ["hits", "0", "survived"], out


def test_a_marker_read_belongs_to_the_identity_it_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """N (Codex 2, first shape): H's environment read, then U holds the number — no hit.

    The identity is read before the environment and again after it; two
    different readings mean the token read may be another process's. Fails
    on ``7d298a8d``: the hit is ``WalkEntry(900001, 20.0, 1)``, U's identity
    with H's token.
    """

    ll = _ll()
    readings = iter([(1, 10.0), (1, 20.0)])
    monkeypatch.setattr(ll._process_env, "own_pids", lambda: [900001])
    monkeypatch.setattr(ll._process_env, "environ_value", lambda pid, key: "run-token")
    monkeypatch.setattr(ll, "process_identity", lambda pid: next(readings))
    assert ll.marker_hits("run-token", since=None, exclude=set()) == []


def test_a_moved_marker_root_contributes_no_children(tmp_path: Path) -> None:
    """N (Codex 2, second shape, its native probe): a hit whose number no longer verifies walks nothing.

    The root is a real process with a real child; the hit names it with a
    start time 100 s off. Both live. Fails on ``7d298a8d``: the root is walked
    before it is verified and its child is SIGKILLed (``-9``).
    """

    from aelix_ai.utils._process_walk import WalkEntry

    ll = _ll()
    script = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'])\n"
        "print(p.pid, flush=True)\n"
        "print(p.wait(), flush=True)\n"
        "time.sleep(30)\n"
    )
    root = subprocess.Popen(  # noqa: S603
        [sys.executable, "-B", "-c", script],
        start_new_session=True,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    assert root.stdout is not None
    child = int(root.stdout.readline())
    try:
        stamp = ll.process_started(root.pid)
        assert stamp is not None
        ll.kill_hits([WalkEntry(root.pid, stamp - 100.0, 1)])
        # The root reaps its child and prints its status if it died; a
        # zombie still reads as alive, so the root's report is the measure.
        ready, _, _ = select.select([root.stdout], [], [], 1.0)
        assert not ready, f"the moved root's child died: {root.stdout.readline().strip()}"
        assert root.poll() is None
    finally:
        root.kill()
        root.wait()
        _kill(child)


async def test_a_scan_that_could_not_list_processes_is_unverified_and_keeps_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex 7): ``own_pids()`` answers ``None``; the sweep is unverified and the evidence stays.

    Fails on ``7d298a8d``: ``HelperAnswer(confirmed=True)`` and the ledger deleted.
    """

    from aelix_agents.print_channel import HelperTracking, sweep_helpers

    ll = _ll()
    path = tmp_path / "scan.launch-ledger"
    assert ll.create(str(path), run="sub-scan")
    ll.append(str(path), {"t": "armed", "pid": 1, "subreaper": True if sys.platform == "linux" else None})
    ll.append(str(path), {"t": "exit", "cleanup": "confirmed", "reason": None, "killed": 0})
    monkeypatch.setattr(ll._process_env, "own_pids", lambda: None)
    answer = await sweep_helpers(HelperTracking("tok-scan", str(path), True), parent_stopped=False)
    assert not answer.confirmed and ll.SCAN_FAILED in (answer.reason or "")
    assert path.exists() and _records(path)[-1]["t"] == "swept"


# ── fix round 1: bounds (Codex 8) ─────────────────────────────────


def test_ten_thousand_retired_calls_keep_the_ledger_and_its_indexes_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex 8, its 10,000-call shape through the writer's own API): bytes and indexes stay bounded.

    Each call writes ``intent`` and ``launch``, then retires; the writer
    compacts once ``COMPACT_AFTER`` launches have closed. Fails on
    ``7d298a8d``: 1,326,731 bytes and 10,000 entries in every index.
    """

    ll = _ll()
    path = tmp_path / "growth.launch-ledger"
    assert ll.create(str(path), run="sub-growth")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    w = ll.LaunchWriter(fd, str(path))
    w.armed_record = {"t": "armed", "pid": 1, "subreaper": None}
    monkeypatch.setattr(ll, "_group_empty", lambda pgid: True)
    monkeypatch.setattr(ll, "process_started", lambda pid: 10.0)
    for i in range(10_000):
        n = w.intent()
        w.launched(n, 4_000_000 + i)
        w.retire()
    w.close()
    size = path.stat().st_size
    assert size < 64 * 1024, size
    assert w.launches == {} and len(w.retired) < ll.COMPACT_AFTER and w.held == {}
    reading = ll.read(str(path))
    assert reading is not None and not reading.damaged and reading.opened and reading.armed
    assert reading.launches == {} and reading.intents == set()


def test_a_ledger_past_the_read_bound_is_damaged_not_read_whole(tmp_path: Path) -> None:
    """N (Codex 8): the parent's read is streamed and stops at ``READ_BOUND``; what was read still counts."""

    ll = _ll()
    path = tmp_path / "big.launch-ledger"
    line = (json.dumps({"v": 1, "t": "intent", "n": 1}) + "\n").encode()
    with path.open("wb") as handle:
        handle.write((json.dumps({"v": 1, "t": "open", "run": "k", "parent": 1}) + "\n").encode())
        handle.write(line * (ll.READ_BOUND // len(line) + 10))
    reading = ll.read(str(path))
    assert reading is not None and reading.damaged and reading.intents == {1}


def test_a_live_group_past_the_active_bound_is_held_for_its_calls_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex 8): past ``ACTIVE_BOUND`` live groups, a new launch is held, not released."""

    ll = _ll()
    path = tmp_path / "active.launch-ledger"
    assert ll.create(str(path), run="sub-active")
    w = ll.LaunchWriter(os.open(path, os.O_WRONLY | os.O_APPEND), str(path))
    monkeypatch.setattr(ll, "ACTIVE_BOUND", 3)
    monkeypatch.setattr(ll, "process_started", lambda pid: 10.0)
    for i in range(4):
        w.launched(w.intent(), 4_100_000 + i)
    w.close()
    assert list(w.held) == [4] and len(w.launches) == 4


# ── fix round 1: the rules verify's mutants showed unpinned (F5) ───


def test_a_last_line_with_no_newline_reads_damaged(tmp_path: Path) -> None:
    """A write cut short leaves a last line with no newline; the reading is damaged (L02)."""

    ll = _ll()
    path = tmp_path / "cut.launch-ledger"
    whole = json.dumps({"v": 1, "t": "intent", "n": 5}).encode() + b" "
    path.write_bytes(json.dumps({"v": 1, "t": "open", "run": "k", "parent": 1}).encode() + b"\n" + whole)
    reading = ll.read(str(path))
    # The cut record would parse; only the missing newline says it was cut.
    assert reading is not None and reading.damaged and reading.opened


def test_a_damaged_writer_writes_nothing_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After one failed write, the writer adds nothing — a record after a cut one would fuse with it (L21)."""

    ll = _ll()
    path = tmp_path / "dmg.launch-ledger"
    assert ll.create(str(path), run="sub-dmg")
    w = ll.LaunchWriter(os.open(path, os.O_WRONLY | os.O_APPEND), str(path))
    real = ll._write_all
    fail = {"once": True}

    def _flaky(fd: int, data: bytes) -> None:
        if fail["once"]:
            fail["once"] = False
            raise OSError(5, "I/O error")
        real(fd, data)

    monkeypatch.setattr(ll, "_write_all", _flaky)
    assert w.write({"t": "intent", "n": 1}) is False
    size = path.stat().st_size
    assert w.write({"t": "intent", "n": 2}) is False and path.stat().st_size == size
    w.close()


async def test_a_live_pid_is_not_confirmed_stopped(tmp_path: Path) -> None:
    """``confirm_stopped`` names a signalled pid still running when its bound passes (L19)."""

    from aelix_ai.utils._process_identity import process_started
    from aelix_ai.utils._process_walk import WalkEntry

    ll = _ll()
    proc = _leader("sleep 300")
    try:
        entry = WalkEntry(proc.pid, process_started(proc.pid), None)
        why = await ll.confirm_stopped(ll.Sweep(pids=[entry]), bound=0.3)
        assert why == f"pid {proc.pid} not confirmed stopped"
    finally:
        proc.kill()
        proc.wait()


async def test_a_group_of_only_zombies_is_confirmed_empty(tmp_path: Path) -> None:
    """A group whose one member exited and is not yet reaped runs nothing: confirmed (L17).

    ``killpg(g, 0)`` answers as if it ran — success on Linux, ``EPERM`` on
    macOS — so only the zombie rule settles it. This test spawned the member,
    so it reaps it afterwards.
    """

    ll = _ll()
    proc = _leader("exit 0")
    try:
        await wait_until(lambda: _zombie(proc.pid), timeout=5, what="the member to become a zombie")
        assert await ll.confirm_stopped(ll.Sweep(groups=[proc.pid]), bound=0.5) is None
    finally:
        proc.wait()


def _zombie(pid: int) -> bool:
    from aelix_ai.utils._process_identity import pid_is_zombie

    return pid_is_zombie(pid) is True


async def test_the_launch_line_names_the_shell_its_group_and_its_start_time(armed: Any, tmp_path: Path) -> None:
    """The ``launch`` record carries the shell's pid, the group it leads and its start time (L24)."""

    out = await _exec("echo $$; sleep 0.3", tmp_path)
    shell = int(out.split()[0])
    launch = next(r for r in _records(armed.path) if r["t"] == "launch")
    assert launch["pid"] == shell and launch["pgid"] == shell
    assert isinstance(launch["started"], float)


async def test_the_retire_tick_retires_a_group_that_emptied_after_its_call(
    armed: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group still holding a helper when its call returned is retired by the tick, no call needed (L26)."""

    ll = _ll()
    monkeypatch.setattr(ll, "RETIRE_SECONDS", 0.1)
    await _exec("sleep 0.4 >/dev/null 2>&1 &", tmp_path)
    assert [r["t"] for r in _records(armed.path)][-1] == "launch"
    await wait_until(
        lambda: _records(armed.path)[-1]["t"] == "retired",
        timeout=5,
        what="the tick to retire the emptied group",
    )


async def test_the_parent_scan_spares_a_carrier_older_than_the_agent(tmp_path: Path) -> None:
    """The parent's sweep reads the agent's start time: an older process with the token is not its (P09)."""

    from aelix_agents.print_channel import HelperTracking, sweep_helpers
    from aelix_ai.utils._process_identity import process_started

    ll = _ll()
    token = "tok-old-" + os.urandom(4).hex()
    sleeper = [sys.executable, "-c", "import time; time.sleep(300)"]
    old = subprocess.Popen(sleeper, env={**os.environ, ll.MARKER_ENV: token})  # noqa: S603
    try:
        await asyncio.sleep(1.1)
        agent = subprocess.Popen(sleeper)  # noqa: S603
        try:
            await asyncio.sleep(0.2)
            tracking = HelperTracking(token, None, False, agent_stamp=process_started(agent.pid))
            await sweep_helpers(tracking, parent_stopped=True)
            assert old.poll() is None, "older than the agent: not this run's"
        finally:
            agent.kill()
            agent.wait()
    finally:
        old.kill()
        old.wait()


def test_a_run_that_never_spawned_drops_its_session_ledger_but_not_a_jobs_directory(tmp_path: Path) -> None:
    """``run``'s end with no process: the unused ledger beside the session goes (P22); an owned row's directory stays (P21)."""

    from aelix_agents.print_channel import HelperTracking, RunningChild, _end_of_run
    from aelix_agents.prompt_file import write_prompt_file

    ll = _ll()
    ledger = tmp_path / "unused.launch-ledger"
    assert ll.create(str(ledger), run="sub-unused")
    row = RunningChild(id="sub-unused", profile="p")
    row.prompt = write_prompt_file("p", "body")
    row.tracking = HelperTracking("tok", str(ledger), True)
    _end_of_run(row, 1.0)
    assert not ledger.exists() and row.prompt is None

    owned = RunningChild(id="sub-owned", profile="p", owner=object())  # type: ignore[arg-type]
    owned.prompt = write_prompt_file("p", "body")
    directory = owned.prompt.directory
    try:
        _end_of_run(owned, 1.0)
        assert directory.exists() and owned.prompt is not None, "the job's, not run's"
    finally:
        import shutil

        shutil.rmtree(directory, ignore_errors=True)


_EXIT_STANDIN = textwrap.dedent(
    """
    import asyncio, os, subprocess, sys
    from aelix_coding_agent.tools import launch_ledger as ll
    from aelix_coding_agent.tools.bash import create_local_bash_operations
    ll._set_subreaper = lambda: None
    ll.arm_from_environment()
    if sys.argv[3] == "confirm":
        ll.CONFIRM_SECONDS = 0.2
        ll._group_settled = lambda pgid: False
    if sys.argv[3] == "scan":
        ll._process_env.own_pids = lambda: None
    group_file, escaped_file = sys.argv[1], sys.argv[2]
    chunks = []
    asyncio.run(create_local_bash_operations().exec(
        "(sleep 300 >/dev/null 2>&1 & echo $! > " + group_file + ")", "/", on_data=chunks.append))
    escaped = (
        "import os, sys, time\\n"
        "if os.fork(): os._exit(0)\\n"
        "os.setsid()\\n"
        "if os.fork(): os._exit(0)\\n"
        "null = os.open(os.devnull, os.O_RDWR)\\n"
        "for fd in (0, 1, 2): os.dup2(null, fd)\\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\\n"
        "time.sleep(300)\\n"
    )
    subprocess.run([sys.executable, "-c", escaped, escaped_file], check=True)
    while not os.path.exists(escaped_file):
        pass
    asyncio.run(ll.exit_cleanup())
    print("done", flush=True)
    """
)


@pytest.mark.parametrize("inject", ["none", "confirm", "scan"])
def test_the_agents_exit_cleanup_sweeps_its_ledger_and_its_marker_and_says_what_it_found(
    tmp_path: Path, inject: str
) -> None:
    """The agent's own exit cleanup, in a stand-in armed process (L35–L38).

    It leaves ``(sleep 300 &)`` in a finished shell's group (on macOS a
    platform binary outside its tree: only the ledger's group names it) and a
    double-forked, ``setsid`` helper that keeps the marker (on macOS only the
    token names it), then runs ``exit_cleanup``: both die and the ``exit`` line
    says ``confirmed``. With the confirm step made to fail, the line says
    ``unverified`` and why. The subreaper is off, so Linux names them the same
    way; there the walk alone would not.
    """

    ll = _ll()
    path = tmp_path / "standin.launch-ledger"
    assert ll.create(str(path), run="sub-standin")
    group_file, escaped_file = tmp_path / "group.pid", tmp_path / "escaped.pid"
    env = {**os.environ, ll.LEDGER_ENV: str(path), ll.MARKER_ENV: "tok-standin-" + os.urandom(4).hex()}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _EXIT_STANDIN, str(group_file), str(escaped_file), inject],
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    helpers = [int(f.read_text().strip()) for f in (group_file, escaped_file) if f.exists()]
    try:
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert len(helpers) == 2
        exit_line = _records(path)[-1]
        assert exit_line["t"] == "exit", exit_line
        if inject == "none":
            assert exit_line["cleanup"] == "confirmed", exit_line
            for pid in helpers:
                assert not _alive(pid), f"{pid} outlived the agent's exit cleanup"
        else:
            want = "not confirmed empty" if inject == "confirm" else ll.SCAN_FAILED
            assert exit_line["cleanup"] == "unverified", exit_line
            assert want in exit_line["reason"], exit_line
    finally:
        _kill(*helpers)


def test_a_hits_env_cleared_session_leader_child_goes_with_it(tmp_path: Path) -> None:
    """What a verified hit started in a session of its own, with no token left to name it, is walked and killed (L15)."""

    ll = _ll()
    token = "tok-tree-" + os.urandom(4).hex()
    script = (
        "import os, subprocess, sys, time\n"
        "c = subprocess.Popen(['/bin/sleep', '300'], env={}, start_new_session=True)\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(300)\n"
    )
    hit = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        env={**os.environ, ll.MARKER_ENV: token},
        stdout=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    assert hit.stdout is not None
    child = int(hit.stdout.readline())
    try:
        hits = ll.marker_hits(token, since=None, exclude=ll.ancestry())
        assert hits is not None and [h.pid for h in hits] == [hit.pid]
        ll.kill_hits(hits)
        assert hit.wait(timeout=5) == -signal.SIGKILL
        wait_until_sync(lambda: not _alive(child), timeout=5, what="the hit's session-leader child to die")
    finally:
        hit.kill()
        hit.wait()
        _kill(child)


def wait_until_sync(pred: Any, *, timeout: float, what: str) -> None:
    import time as _time

    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if pred():
            return
        _time.sleep(0.05)
    raise AssertionError(f"waited {timeout}s for: {what}")


def test_an_unverified_answer_names_sixteen_targets_then_a_count() -> None:
    """At most 16 reasons, then how many more; repeats once (contract r2 §3.1) (L20)."""

    ll = _ll()
    reasons = [f"process group {n}: leader identity unknown" for n in range(20)] + ["dup", "dup"]
    text = ll.summarize(reasons)
    assert text.count("process group") == 16 and text.endswith("; and 5 more"), text


def test_a_ledger_is_never_created_over_an_existing_file(tmp_path: Path) -> None:
    """``O_EXCL``: a path that exists — another run's ledger, or a planted file — is refused (L42)."""

    ll = _ll()
    path = tmp_path / "taken.launch-ledger"
    path.write_text("someone else's\n")
    assert ll.create(str(path), run="sub-taken") is False
    assert path.read_text() == "someone else's\n"


async def test_a_held_group_is_not_stopped_while_its_own_call_still_runs(
    armed: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call whose ``launch`` line was lost runs to its end; another call's end does not stop it (F08)."""

    ll = _ll()
    real = ll._write_all
    first = {"done": False}

    def _fail_first_launch(fd: int, data: bytes) -> None:
        if b'"t":"launch"' in data and not first["done"]:
            first["done"] = True
            raise OSError(28, "No space left on device")
        real(fd, data)

    monkeypatch.setattr(ll, "_write_all", _fail_first_launch)
    slow = asyncio.ensure_future(_exec("sleep 0.8; echo finished", tmp_path))
    await asyncio.sleep(0.3)
    armed.damaged = False  # let the second call's records through, as a later healthy write would
    await _exec("true", tmp_path)
    assert (await slow).strip() == "finished"


async def test_a_compaction_left_half_done_is_cleared_when_the_parent_settles(tmp_path: Path) -> None:
    """A ``.compact`` file an agent killed mid-compaction left beside the session goes with the sweep (F22)."""

    from aelix_agents.print_channel import HelperTracking, sweep_helpers

    ll = _ll()
    path = tmp_path / "half.launch-ledger"
    assert ll.create(str(path), run="sub-half")
    ll.append(str(path), {"t": "armed", "pid": 1, "subreaper": True if sys.platform == "linux" else None})
    ll.append(str(path), {"t": "exit", "cleanup": "confirmed", "reason": None, "killed": 0})
    leftover = Path(str(path) + ll.COMPACT_SUFFIX)
    leftover.write_text("partial\n")
    answer = await sweep_helpers(HelperTracking("tok-half-" + os.urandom(4).hex(), str(path), True), parent_stopped=False)
    assert answer.confirmed and not leftover.exists()


# ── the narrow finish ─────────────────────────────────────────────

_FORKED = textwrap.dedent(
    """
    import asyncio, json, os, sys, time
    from aelix_coding_agent.tools import launch_ledger as ll
    from aelix_coding_agent.tools.bash import create_local_bash_operations
    path, pidfile = sys.argv[1], sys.argv[2]
    ll._set_subreaper = lambda: None
    os.environ[ll.LEDGER_ENV] = path
    assert ll.arm_from_environment() is not None
    out = []
    asyncio.run(create_local_bash_operations().exec(
        "(sleep 301 >/dev/null 2>&1 & echo $! > " + pidfile + ")", "/", on_data=out.append))
    helper = int(open(pidfile).read())
    pid = os.fork()
    if pid == 0:
        sys.exit(0)  # a forked child's normal exit runs the atexit hooks it inherited
    os.waitpid(pid, 0)
    time.sleep(0.5)
    try:
        os.kill(helper, 0)
        alive = True
    except OSError:
        alive = False
    print(json.dumps({"helper": helper, "alive": alive}), flush=True)
    os._exit(0)  # the armed process itself skips its own hook here: the test reads its ledger
    """
)


def test_a_forked_child_of_the_agent_does_not_run_its_exit_cleanup(tmp_path: Path) -> None:
    """G1 (the verifier's ``fork_atexit_probe.py`` shape): the hook runs only in the process that armed.

    The armed agent forks and the fork exits normally; the agent's live
    ``(sleep 301 &)`` group stays alive and no ``exit`` line is written.
    Fails on ``7b28b44e``: the fork's ``sys.exit`` runs the inherited hook,
    kills the helper and writes ``exit``.
    """

    ll = _ll()
    path, pidfile = tmp_path / "fork.launch-ledger", tmp_path / "helper.pid"
    assert ll.create(str(path), run="sub-fork")
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _FORKED, str(path), str(pidfile)],
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        check=False,
    )
    seen = json.loads(proc.stdout.decode("utf-8", errors="replace").splitlines()[-1])
    try:
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert seen["alive"], "the fork's exit swept the agent's live group"
        assert "exit" not in [r["t"] for r in _records(path)]
    finally:
        _kill(seen["helper"])


def test_a_line_longer_than_the_line_bound_reads_damaged(tmp_path: Path) -> None:
    """N14: a record past ``LINE_BOUND`` is not read whole; the reading is damaged there and stops."""

    ll = _ll()
    path = tmp_path / "long.launch-ledger"
    long = json.dumps({"v": 1, "t": "swept", "confirmed": False, "reason": "x" * (ll.LINE_BOUND + 100)})
    path.write_text(
        json.dumps({"v": 1, "t": "open", "run": "k", "parent": 1}) + "\n" + long + "\n"
        + json.dumps({"v": 1, "t": "intent", "n": 7}) + "\n"
    )
    reading = ll.read(str(path))
    assert reading is not None and reading.damaged and reading.intents == set()


def test_compaction_keeps_the_intents_still_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N25: an ``intent`` with no ``launch`` yet survives a compaction — its group is still unknown."""

    ll = _ll()
    path = tmp_path / "inflight.launch-ledger"
    assert ll.create(str(path), run="sub-inflight")
    w = ll.LaunchWriter(os.open(path, os.O_WRONLY | os.O_APPEND), str(path))
    w.armed_record = {"t": "armed", "pid": 1, "subreaper": None}
    monkeypatch.setattr(ll, "process_started", lambda pid: 10.0)
    w.launched(w.intent(), 4_200_000)
    pending = w.intent()
    w.compact()
    w.close()
    reading = ll.read(str(path))
    assert reading is not None and not reading.damaged
    assert reading.in_flight == {pending} and set(reading.launches) == {pending - 1}


def test_a_damaged_ledger_is_never_compacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N28: a compaction would replace the ``damaged`` mark with a clean file; a damaged writer does not compact."""

    ll = _ll()
    path = tmp_path / "damaged.launch-ledger"
    assert ll.create(str(path), run="sub-damaged")
    w = ll.LaunchWriter(os.open(path, os.O_WRONLY | os.O_APPEND), str(path))
    w.armed_record = {"t": "armed", "pid": 1, "subreaper": None}
    real = ll._write_all
    fail = {"once": True}

    def _flaky(fd: int, data: bytes) -> None:
        if fail["once"] and b'"t":"intent"' in data:
            fail["once"] = False
            raise OSError(5, "I/O error")
        real(fd, data)

    monkeypatch.setattr(ll, "_write_all", _flaky)
    w.intent()
    assert w.damaged
    w.compact()
    w.close()
    reading = ll.read(str(path))
    assert reading is not None and reading.damaged
