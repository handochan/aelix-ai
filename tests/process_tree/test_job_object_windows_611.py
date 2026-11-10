"""A real Windows Job: preserve, terminate, the suspended spawn and #731 item 3 (#611 S2b, #759).

windows-latest only: these drive ``kernel32`` and ``ntdll`` for real, which no
other host has. The same entries are pinned on every host through an injected
api in ``test_job_state_machine_611.py``; this file is what shows the real Job
does what that api stands for. None of it has run anywhere but CI.

* ``test_normal_exit_preserves_descendants`` follows codex-rs
  ``normal_exit_preserves_descendants_for_pipe_and_conpty``
  (``utils/pty/src/windows_tests.rs``): a job created with kill-on-close whose
  root exits normally, preserved, then closed, leaves the root's child running.
* #731 item 3 measures #722's win32 evidence legs with real processes: member
  handles collected before the kill, ``kill_now`` (the leg a registration after
  the stop latch takes, driven directly, not through a service), the members a
  normal exit leaves, and the restart classification.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from aelix_ai.utils import _process_tree
from aelix_ai.utils._process_identity import process_started
from aelix_ai.utils._process_tree import (
    CREATE_NEW_PROCESS_GROUP,
    Confirmed,
    ProcessTree,
    _retained_handle,
)

from tests.process_probe import STATE_ALIVE, await_dead_or_zombie, probe_state

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="a real Windows Job")

#: Waits for one byte on stdin, so the job is attached before it spawns; then
#: starts one grandchild, prints its pid, and either exits or stays.
PARENT = """\
import subprocess, sys, time
sys.stdin.buffer.read(1)
kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sys.stdout.write(f"{kid.pid}\\n")
sys.stdout.flush()
if sys.argv[1] == "stay":
    time.sleep(120)
"""

#: Starts its grandchild at once, the first thing it does, then prints its pid.
AT_ONCE = """\
import subprocess, sys, time
kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sys.stdout.write(f"{kid.pid}\\n")
sys.stdout.flush()
time.sleep(120)
"""


@pytest.fixture
def leftovers() -> Iterator[list[int]]:
    pids: list[int] = []
    yield pids
    for pid in pids:
        with contextlib.suppress(OSError):
            # ``TerminateProcess`` on Windows; never ``os.kill(pid, 0)`` there.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )


def _spawn(mode: str, leftovers: list[int], *, flags: int = 0) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, "-c", PARENT if mode in ("exit", "stay") else AT_ONCE, mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NEW_PROCESS_GROUP | flags,
    )
    leftovers.append(proc.pid)
    return proc


def _attach(
    proc: subprocess.Popen[bytes],
    *,
    suspended: bool = False,
    stop_latched: Callable[[], bool] | None = None,
) -> ProcessTree:
    return ProcessTree.attach(
        proc.pid,
        kill_on_close=True,
        handle=_retained_handle(proc),
        suspended=suspended,
        stop_latched=stop_latched,
    )


def _grandchild(proc: subprocess.Popen[bytes], leftovers: list[int], *, go: bool = True) -> int:
    assert proc.stdin is not None and proc.stdout is not None
    if go:
        proc.stdin.write(b"x")
        proc.stdin.flush()
    pid = int(proc.stdout.readline())
    leftovers.append(pid)
    return pid


def _in_job(pid: int, tree: ProcessTree) -> bool:
    api = _process_tree._KernelApi()
    handle = api.open_member(pid)
    try:
        assert tree.job is not None
        return api.in_job(handle, tree.job)
    finally:
        api.close_process(handle)


def test_normal_exit_preserves_descendants(leftovers: list[int]) -> None:
    proc = _spawn("exit", leftovers)
    tree = _attach(proc)
    grandchild = _grandchild(proc, leftovers)
    assert proc.wait(30) == 0
    assert tree.preserve_descendants() is True
    tree.close()
    time.sleep(1.0)
    assert probe_state(grandchild) == STATE_ALIVE, "a preserved job's close ended its member"


async def test_terminate_wins_over_a_later_preserve(leftovers: list[int]) -> None:
    proc = _spawn("stay", leftovers)
    tree = _attach(proc)
    grandchild = _grandchild(proc, leftovers)
    tree.hard_kill()
    assert tree.preserve_descendants() is False
    tree.close()
    assert await await_dead_or_zombie(grandchild, timeout=10) != STATE_ALIVE


async def test_a_suspended_spawn_holds_a_grandchild_born_at_once(leftovers: list[int]) -> None:
    """``CREATE_SUSPENDED``, assigned, resumed with ``NtResumeProcess``: its first child is in the Job.

    It shows the resume runs the process and its child is contained; a child of
    this python is born well after the ~1 ms assignment window either way, so it
    does not show that suspension was needed.
    """

    flags = ProcessTree.suspend_flags()
    assert flags == _process_tree.CREATE_SUSPENDED, "NtResumeProcess did not resolve"
    proc = _spawn("at-once", leftovers, flags=flags)
    tree = _attach(proc, suspended=True, stop_latched=lambda: False)
    grandchild = _grandchild(proc, leftovers, go=False)
    assert _in_job(grandchild, tree)
    tree.hard_kill()
    tree.close()
    assert await await_dead_or_zombie(grandchild, timeout=10) != STATE_ALIVE


def test_a_latched_suspended_spawn_never_runs(leftovers: list[int], tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    proc = subprocess.Popen(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
        creationflags=CREATE_NEW_PROCESS_GROUP | ProcessTree.suspend_flags(),
    )
    leftovers.append(proc.pid)
    with pytest.raises(_process_tree.SuspendedLaunchError) as caught:
        _attach(proc, suspended=True, stop_latched=lambda: True)
    assert caught.value.tree is None
    assert proc.wait(10) is not None
    assert not marker.exists(), "a latched launch ran"


# === #731 item 3 ==============================================================


async def test_member_handles_are_collected_on_a_real_job(leftovers: list[int]) -> None:
    proc = _spawn("stay", leftovers)
    tree = _attach(proc)
    grandchild = _grandchild(proc, leftovers)
    receipt = tree.stop_and_collect(policy="stop")
    assert {proc.pid, grandchild} <= {pid for pid, _handle in receipt.handles}, receipt
    verdict = await tree.confirm(receipt, time.monotonic() + 20)
    assert isinstance(verdict, Confirmed), verdict
    assert proc.wait(10) is not None
    assert await await_dead_or_zombie(grandchild, timeout=10) != STATE_ALIVE
    tree.close()


async def test_kill_now_collects_a_real_jobs_members_before_it_kills(leftovers: list[int]) -> None:
    """``evidence.kill_now`` on a real Job: the leg a registration after the stop
    latch takes, driven directly — the service's latch itself is not exercised
    here. Its receipt holds the root and its child, and the barrier confirms both."""

    from aelix_coding_agent.jobs import evidence

    proc = _spawn("stay", leftovers)
    tree = _attach(proc)
    grandchild = _grandchild(proc, leftovers)
    record = evidence.ProcessRecord(proc.pid, process_started(proc.pid), tree=tree)
    evidence.kill_now(record)
    assert record.receipt is not None
    assert {proc.pid, grandchild} <= {pid for pid, _h in record.receipt.handles}
    confirmed, why = await evidence.confirm_gone([record], time.monotonic() + 20)
    assert confirmed, why
    tree.close()


async def test_the_members_left_after_a_normal_exit_are_stopped(leftovers: list[int]) -> None:
    proc = _spawn("exit", leftovers)
    tree = _attach(proc)
    grandchild = _grandchild(proc, leftovers)
    assert proc.wait(30) == 0
    receipt = tree.stop_and_collect(policy="stop_descendants")
    assert grandchild in {pid for pid, _h in receipt.handles}
    verdict = await tree.confirm(receipt, time.monotonic() + 20)
    assert isinstance(verdict, Confirmed), verdict
    assert await await_dead_or_zombie(grandchild, timeout=10) != STATE_ALIVE
    tree.close()


def test_restart_classification_reads_a_real_process(leftovers: list[int]) -> None:
    from aelix_coding_agent.jobs import evidence

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], creationflags=CREATE_NEW_PROCESS_GROUP
    )
    leftovers.append(proc.pid)
    end = time.monotonic() + 10
    while (started := process_started(proc.pid)) is None:
        assert time.monotonic() < end
        time.sleep(0.05)
    recorded = [{"evidence": {"pid": proc.pid, "started": started, "host": evidence.this_host()}}]
    state, why = evidence.classify_restart(recorded, None)
    assert state == "unresolved" and "still running" in why
    proc.send_signal(signal.SIGTERM)
    proc.wait(10)
    state, why = evidence.classify_restart(recorded, None)
    assert state == "lost", why

