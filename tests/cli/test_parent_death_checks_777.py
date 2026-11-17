"""#777 fix round 1: the checks ``test_parent_death_611.py`` reads its processes with, pinned against fakes.

Codex's round-1 review of #777 found five ways the parent-death cases could pass
without measuring what they say. Each case here is its probe's shape, on any
host unless it needs a real SIGKILL, and each fails on ``b69890da``:

* **M1, the bound.** A process first read ended after the bound passed: the
  child ended at 5.3 s, but reaping the parent took 5.7 s, so the first read
  came after the 5 s bound (Codex's ``posix_deadline``). On ``b69890da`` the
  case accepted it ("gone, ended 5.712 s after the kill"); the Windows watcher
  accepted ``ended_after: 11.0`` against 10 s the same way.
* **M2, the cleanup.** A process that announces itself while the cleanup runs
  (Codex's ``cleanup_unannounced``): on ``b69890da`` the cleanup took one
  snapshot of the markers and left it running.
* **M3, the kill.** ``TerminateProcess`` that fails, and handles already
  signalled before it (Codex's ``windows_checks``): on ``b69890da`` both passed.
* **L4, unknown identity.** A start time read as ``None`` on both sides, by the
  positive control and through a handle (``probe_unknown_identity.py``): on
  ``b69890da`` ``None == None`` passed both.
* **L5, a partial construction.** ``WinHandles`` that fails after opening one
  handle closes it (Codex's ``partial_handles``): on ``b69890da`` it leaked.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_ai.utils import _process_identity
from aelix_ai.utils._process_identity import process_started

import tests.cli.test_parent_death_611 as pd
from tests.process_probe import STATE_ALIVE, probe_state

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="a real SIGKILL")


class Clock:
    """``time`` as the parent-death module reads it: ``monotonic`` and a ``sleep`` that moves it."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _on(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    monkeypatch.setattr(pd, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))


# ── M1: a read after the bound fails ──────────────────────────────────────


@POSIX
@pytest.mark.parametrize(
    ("ends_at", "reap_takes", "fails"),
    [(5.3, 5.7, True), (4.0, 0.0, False)],
    ids=["read-after-the-bound", "control"],
)
def test_a_child_first_read_ended_after_the_bound_fails_the_5_s_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ends_at: float, reap_takes: float, fails: bool
) -> None:
    """Codex's ``posix_deadline``: the child ends at 5.3 s and the parent's reap takes 5.7 s."""

    clock = Clock()
    _on(monkeypatch, clock)
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # noqa: S603
    run = pd.Run(tmp_path)

    class SlowReap:
        def wait(self, timeout: float) -> int:
            code = parent.wait(timeout=timeout)
            clock.now += reap_takes  # the monitor descheduled while it reaped the parent
            return code

    run.proc = SlowReap()  # type: ignore[assignment]
    child = pd.Announced("child", 999_999, 1.0)
    run.ids.at[child.pid] = (STATE_ALIVE, 1.0)
    monkeypatch.setattr(
        pd.Run, "state", lambda _self, _pid: "gone" if clock.now >= ends_at else "alive"
    )
    procs = {
        "parent": pd.Announced("parent", parent.pid, process_started(parent.pid)),
        "child": child,
    }
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if fails:
                with pytest.raises(AssertionError, match=r"\['child'\] not read gone within 5.0 s"):
                    pd.sigkill_on_linux(run, procs, label="M1", asserted=("child",))
            else:
                pd.sigkill_on_linux(run, procs, label="M1", asserted=("child",))
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait()


# ── M3 and M1 on Windows: the fake kernel ────────────────────────────────


class FakeKernel:
    """What ``WinHandles`` asks of kernel32, scripted: when each handle turns signalled, what the kill returns."""

    def __init__(
        self,
        clock: Clock,
        *,
        ends: dict[int, float] | None = None,
        ended_before: bool = False,
        kill_ok: bool = True,
        created: float | None = 100.0,
        refuse: frozenset[int] = frozenset(),
    ) -> None:
        self.clock = clock
        self.ends = ends or {}
        self.ended_before = ended_before
        self.kill_ok = kill_ok
        self.created_value = created
        self.refuse = refuse
        self.killed_at: float | None = None
        self.opened: list[int] = []
        self.closed: list[int] = []

    def parents(self) -> dict[int, int]:
        return {}

    def open(self, pid: int) -> int:
        if pid in self.refuse:
            raise OSError(f"OpenProcess(pid {pid}) failed: error 5")
        self.opened.append(pid + 1000)
        return pid + 1000

    def image(self, handle: int) -> str:
        return f"C:\\fake\\{handle}.exe"

    def in_any_job(self, handle: int) -> bool:
        return True

    def created(self, handle: int) -> float | None:
        return self.created_value

    def signalled(self, handle: int) -> bool:
        if self.ended_before:
            return True
        if self.killed_at is None:
            return False
        return self.clock.now - self.killed_at >= self.ends.get(handle - 1000, 0.1)

    def terminate(self, handle: int) -> tuple[bool, int]:
        if self.killed_at is None and self.kill_ok:
            self.killed_at = self.clock.now
        return (True, 0) if self.kill_ok else (False, 5)

    def exit_code(self, handle: int) -> int:
        return 1 if self.signalled(handle) else 259

    def close(self, handle: int) -> None:
        self.closed.append(handle)


def _procs(started: float | None = 100.0) -> dict[str, pd.Announced]:
    return {role: pd.Announced(role, pid, started) for pid, role in ((1, "parent"), (2, "child"))}


@pytest.mark.parametrize(
    ("shape", "match"),
    [
        ("late", r"\['parent', 'child'\] not read ended within 10.0 s"),
        ("failed-kill", "TerminateProcess failed"),
        ("ended-before", "ended before the kill"),
        ("control", None),
    ],
)
def test_the_windows_check_needs_a_kill_that_worked_on_live_processes_inside_the_bound(
    monkeypatch: pytest.MonkeyPatch, shape: str, match: str | None
) -> None:
    """Codex's ``windows_checks`` (M3, and M1's Windows half): each shape but the control fails."""

    clock = Clock()
    _on(monkeypatch, clock)
    kernel = FakeKernel(
        clock,
        ends={1: 10.5, 2: 10.5} if shape == "late" else None,
        ended_before=shape == "ended-before",
        kill_ok=shape != "failed-kill",
    )
    if match is None:
        report = pd.terminate_on_windows(_procs(), kernel=kernel)
        assert report["processes"]["child"]["image"] == "C:\\fake\\1002.exe"
        assert {"parent_pid", "in_any_job", "created"} <= set(report["processes"]["child"])
    else:
        with pytest.raises(AssertionError, match=match):
            pd.terminate_on_windows(_procs(), kernel=kernel)
    assert sorted(kernel.closed) == [1001, 1002], "every handle is closed"


# ── L4 and L5: identity that cannot be read, and a partial construction ──


def test_a_positive_control_with_no_start_time_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``probe_unknown_identity.py``'s first shape: a real live child whose start time reads ``None``."""

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # noqa: S603
    try:
        run = pd.Run(tmp_path)
        monkeypatch.setattr(_process_identity, "process_started", lambda _pid: None)
        with pytest.raises(AssertionError, match="before the kill"):
            run.positive_control({"child": pd.Announced("child", child.pid, None)})
    finally:
        child.kill()
        child.wait()


@pytest.mark.parametrize(
    ("created", "refuse"),
    [(None, frozenset()), (100.0, frozenset({2}))],
    ids=["no-creation-time", "second-open-refused"],
)
def test_win_handles_refuses_an_unknown_identity_and_closes_what_it_opened(
    created: float | None, refuse: frozenset[int]
) -> None:
    """``probe_unknown_identity.py``'s second shape (L4) and ``partial_handles`` (L5)."""

    kernel = FakeKernel(Clock(), created=created, refuse=refuse)
    with pytest.raises((AssertionError, OSError)):
        pd.WinHandles(_procs(None if created is None else 100.0), kernel=kernel)
    assert kernel.closed == kernel.opened and kernel.opened, (kernel.opened, kernel.closed)


# ── M2: the cleanup rescans ──────────────────────────────────────────────


_LATE_CHILD = """\
import json, os, sys, time
from aelix_ai.utils._process_identity import process_started
ready, marks = sys.argv[1], sys.argv[2]
with open(ready + ".tmp", "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))
os.replace(ready + ".tmp", ready)
time.sleep(0.4)  # announced only once the cleanup has begun
path = os.path.join(marks, "late-child-%d.json" % os.getpid())
with open(path + ".tmp", "w", encoding="utf-8") as handle:
    json.dump({"role": "late-child", "pid": os.getpid(), "started": process_started(os.getpid())}, handle)
os.replace(path + ".tmp", path)
time.sleep(60)
"""


def test_the_cleanup_kills_a_process_that_announces_itself_while_it_runs(tmp_path: Path) -> None:
    """Codex's ``cleanup_unannounced``: the child is alive before the cleanup and announces 0.4 s into it."""

    run = pd.Run(tmp_path)
    script = tmp_path / "late_child.py"
    script.write_text(_LATE_CHILD, encoding="utf-8")
    ready = tmp_path / "ready"
    argv = [sys.executable, str(script), str(ready), str(run.marks)]
    spawn = f"import subprocess, time; subprocess.Popen({argv!r}); time.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", spawn])  # noqa: S603
    run.proc = parent  # type: ignore[assignment]
    (run.marks / f"parent-{parent.pid}.json").write_text(
        json.dumps({"role": "parent", "pid": parent.pid, "started": process_started(parent.pid)}),
        encoding="utf-8",
    )
    child = 0
    born: float | None = None
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() or not ready.read_text(encoding="utf-8").strip():
            assert time.monotonic() < deadline, "the child never started"
            time.sleep(0.01)
        child = int(ready.read_text(encoding="utf-8"))
        born = process_started(child)
        assert not list(run.marks.glob("late-child-*.json")), "the premise: not announced yet"
        left = run.clean_up(quiet=1.0)
        assert list(run.marks.glob("late-child-*.json")), "it announced itself during the cleanup"
        assert left == [] and probe_state(child) != STATE_ALIVE, (
            f"left {left}, child {probe_state(child)}"
        )
    finally:
        if child and born is not None and process_started(child) == born:
            pd._kill(child)
        if parent.poll() is None:
            parent.kill()
        parent.wait()
