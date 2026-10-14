"""#446 measurement twin. MEASURE BRANCH ONLY - never merged.

Runs, round by round and interleaved, (a) the REAL
``test_a_holder_that_never_falls_idle_hits_the_drain_cap`` body (both params) and
(b) an instrumented TWIN of the same flow that records, per run, where the
deadline arm's 2.0 s (win32) went:

* parent (perf_counter frame, S = the instant before the product's Popen):
  the product's ``proc.wait(timeout)`` entry and return / ``TimeoutExpired``
  (with the root's state probed at that instant), ``_end_the_tree`` in/out with
  the root's state before the ladder, the first and last chunk, the call's
  return; the drain's end kind from the file's own ``_DrainRecord``.
* the ROOT the product waits on (pwsh on win32): kernel creation / exit time and
  CPU (``GetProcessTimes`` on the handle the stdlib retains).
* the python root script: stamps at its first statement, after the stdlib,
  after ``aelix_ai``'s import, after spawning the holder, after announcing, at
  its end (perf_counter + monotonic + process_time), and its kernel creation
  time (win32).
* the holder: its first statement.

perf_counter is QPC on win32 and mach_absolute_time / CLOCK_MONOTONIC on POSIX:
one system-wide clock across processes, which ``time.monotonic`` is not on
py<=3.12 win32 (GetTickCount64, 15.6 ms). One JSON line per run to $M446_OUT.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tools import bash as bash_module

from tests.process_probe import probe_state
from tests.process_tree.test_process_tree_real_processes import _reap
from tests.tools import test_bash_tool_containment as T

strays = T.strays

ROUNDS = int(os.environ.get("M446_ROUNDS", "2"))
OUT = os.environ.get("M446_OUT", "")
LOAD = os.environ.get("M446_LOAD", "none")

ROOT_STAMPED = """\
import time
_first = (time.perf_counter(), time.monotonic(), time.process_time())
import os
import subprocess
import sys

marker, holder, nap, tick, hold = sys.argv[1:6]
_stamps = marker + ".stamps"


def _stamp(name, at=None):
    p, m, c = at if at is not None else (time.perf_counter(), time.monotonic(), time.process_time())
    with open(_stamps, "a", encoding="utf-8") as handle:
        handle.write(f"{name} {p:.6f} {m:.6f} {c:.6f}\\n")


_stamp("first", _first)
_stamp("stdlib")
from aelix_ai.utils._process_tree import containment_spawn_kwargs

_stamp("aelix")
child = subprocess.Popen(
    [sys.executable, holder, nap, tick, "@MARK@", marker],
    stdin=subprocess.DEVNULL,
    **containment_spawn_kwargs(new_session=True),
)
_stamp("spawned")
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
_stamp("announced")
time.sleep(float(hold))
_stamp("end")
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32")
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetCurrentProcess.argtypes = []
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k.GetSystemTimePreciseAsFileTime.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    fts = [wintypes.FILETIME() for _ in range(4)]
    k.GetProcessTimes(k.GetCurrentProcess(), *(ctypes.byref(f) for f in fts))
    now = wintypes.FILETIME()
    p_cal = time.perf_counter()
    k.GetSystemTimePreciseAsFileTime(ctypes.byref(now))
    v = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
    with open(_stamps, "a", encoding="utf-8") as handle:
        handle.write(f"created_ft {v(fts[0])} {p_cal:.6f} {v(now)}\\n")
""".replace("@MARK@", T.MARK)

HOLDER_STAMPED = """\
import sys
import time

_p = time.perf_counter()
nap, tick = float(sys.argv[1]), float(sys.argv[2])
if len(sys.argv) > 4:
    with open(sys.argv[4] + ".holder", "w", encoding="utf-8") as handle:
        handle.write(f"{_p:.6f}\\n")
deadline = time.monotonic() + nap
while time.monotonic() < deadline:
    if tick > 0:
        sys.stdout.buffer.write(b"tick\\n")
        sys.stdout.buffer.flush()
        time.sleep(tick)
    else:
        time.sleep(0.05)
"""


def _perf() -> float:
    return time.perf_counter()


def _win_kernel_times(handle: int) -> dict[str, Any]:
    """``GetProcessTimes`` on ``handle`` plus a (perf, precise FILETIME) pair."""

    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32")
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k.GetSystemTimePreciseAsFileTime.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    fts = [wintypes.FILETIME() for _ in range(4)]
    ok = k.GetProcessTimes(handle, *(ctypes.byref(f) for f in fts))
    now = wintypes.FILETIME()
    p_cal = time.perf_counter()
    k.GetSystemTimePreciseAsFileTime(ctypes.byref(now))

    def v(f: Any) -> int:
        return (f.dwHighDateTime << 32) | f.dwLowDateTime

    return {
        "ok": bool(ok),
        "created_ft": v(fts[0]),
        "exited_ft": v(fts[1]),
        "kernel_s": v(fts[2]) / 1e7,
        "user_s": v(fts[3]) / 1e7,
        "cal_perf": p_cal,
        "cal_ft": v(now),
    }


def _ft_to_perf(ft: int, cal_perf: float, cal_ft: int) -> float:
    return cal_perf + (ft - cal_ft) / 1e7


class _Witness:
    """Wrappers that call straight through and stamp (measurement only)."""

    def __init__(self) -> None:
        self.pre_popen: float | None = None
        self.post_popen: float | None = None
        self.proc: Any = None
        self.wait_in: float | None = None
        self.wait_arg: Any = None
        self.wait_out: float | None = None
        self.wait_timeout_at: float | None = None
        self.state_at_timeout: str | None = None
        self.state_probed_at: float | None = None
        self.kill_in: float | None = None
        self.kill_state: str | None = None
        self.kill_out: float | None = None
        self.first_chunk: float | None = None
        self.last_chunk: float | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        w = self
        real_end = bash_module._end_the_tree

        class Shim:
            def __getattr__(self, name: str) -> Any:
                return getattr(subprocess, name)

            @staticmethod
            def Popen(*args: Any, **kwargs: Any) -> Any:  # noqa: N802
                w.pre_popen = _perf()
                proc = subprocess.Popen(*args, **kwargs)  # noqa: S603
                w.post_popen = _perf()
                w.proc = proc
                orig = proc.wait

                def wait(timeout: float | None = None) -> Any:
                    first = w.wait_in is None
                    if first:
                        w.wait_in, w.wait_arg = _perf(), timeout
                    try:
                        code = orig(timeout)
                    except subprocess.TimeoutExpired:
                        if first:
                            w.wait_timeout_at = _perf()
                            w.state_at_timeout = probe_state(proc.pid)
                            w.state_probed_at = _perf()
                        raise
                    if first:
                        w.wait_out = _perf()
                    return code

                proc.wait = wait
                return proc

        def end_the_tree(tree: Any, proc: Any, *, reap: float) -> None:
            w.kill_in = _perf()
            w.kill_state = probe_state(proc.pid)
            real_end(tree, proc, reap=reap)
            w.kill_out = _perf()

        monkeypatch.setattr(bash_module, "subprocess", Shim())
        monkeypatch.setattr(bash_module, "_end_the_tree", end_the_tree)

    def on_data(self, chunks: list[bytes]) -> Any:
        def deliver(chunk: bytes) -> None:
            now = _perf()
            if self.first_chunk is None:
                self.first_chunk = now
            self.last_chunk = now
            chunks.append(chunk)

        return deliver


def _read_stamps(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with contextlib.suppress(OSError):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if parts[0] == "created_ft":
                out["py_created_ft"] = (int(parts[1]), float(parts[2]), int(parts[3]))
            else:
                out[parts[0]] = (float(parts[1]), float(parts[2]), float(parts[3]))
    return out


async def _twin(tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch, timeout: float | None, round_: int) -> dict[str, Any]:
    marker = tmp_path / "attempt-1" / "pids.txt"
    marker.parent.mkdir()
    registrar = T._registrar(marker, strays)
    record = T._DrainRecord()
    record.install(monkeypatch)
    witness = _Witness()
    witness.install(monkeypatch)
    chunks: list[bytes] = []
    root = T._script(marker.parent, "root_with_a_holder.py", ROOT_STAMPED)
    holder = T._script(marker.parent, "holder.py", HOLDER_STAMPED)
    command = T._command(root, str(marker), holder, str(T.OUTLIVE), "0.05", "0.0")
    ops = bash_module.create_local_bash_operations()
    called = _perf()
    task = asyncio.ensure_future(
        ops.exec(command, str(marker.parent), on_data=witness.on_data(chunks), timeout=timeout)
    )
    started = time.monotonic()
    result: Any = None
    try:
        result = await T._bounded(task, T._bound(T.DRAIN_CAP_SECONDS) + 5.0, "m446 twin")
    finally:
        returned_perf = _perf()
        elapsed = time.monotonic() - started
        pids = registrar.settle()
        end = record.end()
    kernel: dict[str, Any] = {}
    if sys.platform == "win32" and witness.proc is not None:
        with contextlib.suppress(Exception):
            kernel = _win_kernel_times(int(witness.proc._handle))
    stamps = _read_stamps(Path(str(marker) + ".stamps"))
    holder_first = None
    with contextlib.suppress(OSError, ValueError):
        holder_first = float(Path(str(marker) + ".holder").read_text(encoding="utf-8").strip())
    S = witness.pre_popen if witness.pre_popen is not None else called

    def rel(x: float | None) -> float | None:
        return None if x is None else round(x - S, 4)

    row: dict[str, Any] = {
        "py": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
        "load": LOAD,
        "kind": "twin",
        "timeout": timeout,
        "round": round_,
        "call_to_S": round(S - called, 4),
        "post_popen": rel(witness.post_popen),
        "wait_in": rel(witness.wait_in),
        "wait_arg": witness.wait_arg,
        "wait_out": rel(witness.wait_out),
        "wait_timeout_at": rel(witness.wait_timeout_at),
        "state_at_timeout": witness.state_at_timeout,
        "state_probed_at": rel(witness.state_probed_at),
        "kill_in": rel(witness.kill_in),
        "kill_state": witness.kill_state,
        "kill_out": rel(witness.kill_out),
        "first_chunk": rel(witness.first_chunk),
        "last_chunk": rel(witness.last_chunk),
        "returned": rel(returned_perf),
        "elapsed": round(elapsed, 4),
        "ticks": b"".join(chunks).count(b"tick\n"),
        "end": T._label(task, end),
        "exit_code": None if result is None else result.exit_code,
        "timed_out": None if result is None else result.timed_out,
        "output_unconfirmed": None if result is None else result.output_unconfirmed,
        "announced_pids": None if pids is None else list(pids),
        "holder_first": rel(holder_first),
    }
    for name in ("first", "stdlib", "aelix", "spawned", "announced", "end"):
        if name in stamps:
            p, _m, c = stamps[name]
            row[f"py_{name}"] = rel(p)
            row[f"py_{name}_cpu"] = round(c, 4)
    if "py_created_ft" in stamps:
        ft, cal_p, cal_ft = stamps["py_created_ft"]
        row["py_created"] = rel(_ft_to_perf(ft, cal_p, cal_ft))
    if kernel.get("ok"):
        row["root_created"] = rel(_ft_to_perf(kernel["created_ft"], kernel["cal_perf"], kernel["cal_ft"]))
        row["root_exited"] = rel(_ft_to_perf(kernel["exited_ft"], kernel["cal_perf"], kernel["cal_ft"]))
        row["root_cpu"] = round(kernel["kernel_s"] + kernel["user_s"], 4)
        row["root_kernel_s"] = round(kernel["kernel_s"], 4)
    if record.exited_at is not None:
        row["drain_cap_past_exit"] = round(record.cap - record.exited_at, 4) if record.cap else None
    # Cleanup: the escaped holder ticks for OUTLIVE into a detached reader.
    for pid in pids or ():
        _reap(pid)
    return row


def _emit(row: dict[str, Any]) -> None:
    line = json.dumps(row, sort_keys=True)
    if OUT:
        with open(OUT, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    print("M446 " + line)


@pytest.mark.parametrize("timeout", [T._DEADLINE_ARM_SECONDS, None], ids=["deadline-arm", "no-deadline"])
@pytest.mark.parametrize("kind", ["twin", "real"])
@pytest.mark.parametrize("round_", range(ROUNDS))
async def test_m446(
    tmp_path: Path,
    strays: list[int],
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    kind: str,
    round_: int,
) -> None:
    if kind == "twin":
        _emit(await _twin(tmp_path, strays, monkeypatch, timeout, round_))
        return
    t0 = time.monotonic()
    outcome = "passed"
    detail = ""
    try:
        await T.test_a_holder_that_never_falls_idle_hits_the_drain_cap(tmp_path, strays, monkeypatch, timeout)
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
        outcome = "failed"
        detail = f"{type(exc).__name__}: {str(exc)[:300]}"
        raise
    finally:
        _emit(
            {
                "py": f"{sys.version_info.major}.{sys.version_info.minor}",
                "platform": sys.platform,
                "load": LOAD,
                "kind": "real",
                "timeout": timeout,
                "round": round_,
                "outcome": outcome,
                "detail": detail,
                "wall": round(time.monotonic() - t0, 4),
            }
        )
