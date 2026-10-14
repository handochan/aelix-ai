"""#446 VERIFY probe (throwaway, never merged; written by the verify pass, not the lane).

Drives ``test_a_holder_that_never_falls_idle_hits_the_drain_cap`` from a chosen
module (the committed one by default; ``V446_TESTMOD`` names e24ea2e8's copy or
a mutated copy) under forced shapes and product sabotage, and records one JSON
line per run: outcome, failure text, every warning the case left, and every
:class:`_TimeoutWitness` record (fields after ``settle``).

Env:
  V446_TESTMOD   module to drive (default tests.tools.test_bash_tool_containment)
  V446_ROOT      none | held | pre   (python root slow after / before announcing)
  V446_SLOW      how many attempts get the slow root (counter file), default 0
  V446_HOLD      seconds the slow root holds, default 30
  V446_LINGER    -1 (off) | N: append a second statement to the command (the
                 shell stays a separate process); its sleeper holds V446_HOLD
                 on the first N attempts
  V446_PRODUCT   none | early | unseen | heat | rootkill | nokill
  V446_OUT       jsonl path
  V446_ROUNDS    rounds for test_rounds (default 1)
  V446_PARAMS    'both' | 'deadline' | 'none'
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import subprocess
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tools import bash as bash_module

from tests.tools import test_bash_tool_containment as A

strays = A.strays

TESTMOD = os.environ.get("V446_TESTMOD", "tests.tools.test_bash_tool_containment")
ROOT = os.environ.get("V446_ROOT", "none")
SLOW = int(os.environ.get("V446_SLOW", "0"))
HOLD = float(os.environ.get("V446_HOLD", "30"))
LINGER = int(os.environ.get("V446_LINGER", "-1"))
PRODUCT = os.environ.get("V446_PRODUCT", "none")
#: How many root spawns the wait sabotage applies to (one per attempt).
_PRODUCT_LEFT = [int(os.environ.get("V446_PRODUCT_N", "99"))]
OUT = os.environ.get("V446_OUT", "")
ROUNDS = int(os.environ.get("V446_ROUNDS", "1"))
PARAMS = os.environ.get("V446_PARAMS", "both")
LOAD = os.environ.get("V446_LOAD", "none")

SLOW_ROOT = """\
import os
import subprocess
import sys
import time

from aelix_ai.utils._process_tree import containment_spawn_kwargs

counter, mode, hold_s = @COUNTER@, @MODE@, @HOLD@
slow = False
try:
    with open(counter, encoding="utf-8") as f:
        n = int(f.read().strip() or "0")
    if n > 0:
        slow = True
        with open(counter, "w", encoding="utf-8") as f:
            f.write(str(n - 1))
except (OSError, ValueError):
    pass
if slow and mode == "pre":
    time.sleep(hold_s)
marker, holder, nap, tick, hold = sys.argv[1:6]
child = subprocess.Popen(
    [sys.executable, holder, nap, tick, "@MARK@"],
    stdin=subprocess.DEVNULL,
    **containment_spawn_kwargs(new_session=True),
)
tmp = marker + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()} {child.pid}\\n")
os.replace(tmp, marker)
if slow and mode == "held":
    time.sleep(hold_s)
time.sleep(float(hold))
"""

SLEEPER = """\
import sys
import time

counter, hold_s = sys.argv[1], float(sys.argv[2])
try:
    with open(counter, encoding="utf-8") as f:
        n = int(f.read().strip() or "0")
    if n > 0:
        with open(counter, "w", encoding="utf-8") as f:
            f.write(str(n - 1))
        time.sleep(hold_s)
except (OSError, ValueError):
    pass
"""


def _is_root(proc: Any) -> bool:
    return "root_with_a_holder" in str(getattr(proc, "args", ""))


def _install(
    monkeypatch: pytest.MonkeyPatch, M: Any, tmp_path: Path, records: list, waits: list
) -> None:
    if ROOT != "none":
        counter = tmp_path / "v446-slow-counter"
        counter.write_text(str(SLOW), encoding="utf-8")
        src = (
            SLOW_ROOT.replace("@COUNTER@", repr(str(counter)))
            .replace("@MODE@", repr(ROOT))
            .replace("@HOLD@", repr(HOLD))
            .replace("@MARK@", A.MARK)
        )
        monkeypatch.setattr(M, "ROOT_WITH_AN_ESCAPED_HOLDER", src)
    if LINGER >= 0:
        lcounter = tmp_path / "v446-linger-counter"
        lcounter.write_text(str(LINGER), encoding="utf-8")
        sleeper = tmp_path / "v446_sleeper.py"
        sleeper.write_text(SLEEPER, encoding="utf-8")
        orig = M._command

        def command(*args: str) -> str:
            return orig(*args) + "; " + orig(str(sleeper), str(lcounter), str(HOLD))

        monkeypatch.setattr(M, "_command", command)

    real_wait = subprocess.Popen.wait

    def stamped(self: Any, timeout: float) -> Any:
        # The root's first timed wait, stamped: how long it ran and how it ended.
        t_in = time.monotonic()
        try:
            code = real_wait(self, timeout)
        except subprocess.TimeoutExpired:
            waits.append({"wait_for": timeout, "dur": round(time.monotonic() - t_in, 4), "timed_out": True})
            raise
        waits.append({"wait_for": timeout, "dur": round(time.monotonic() - t_in, 4), "timed_out": False})
        return code

    def wait(self: Any, timeout: float | None = None) -> Any:
        if timeout is None or not _is_root(self) or getattr(self, "_v446_done", False):
            return real_wait(self, timeout)
        self._v446_done = True
        if PRODUCT not in ("early", "unseen", "heat") or _PRODUCT_LEFT[0] <= 0:
            return stamped(self, timeout)
        _PRODUCT_LEFT[0] -= 1
        if PRODUCT == "early":
            return real_wait(self, timeout * 0.5)
        if PRODUCT == "unseen":
            time.sleep(timeout)
            raise subprocess.TimeoutExpired(self.args, timeout)
        if PRODUCT == "heat":
            try:
                return real_wait(self, timeout)
            except subprocess.TimeoutExpired:
                # The root exits right after the deadline and the worker
                # resumes late: kill it here (no reap), then let the timeout out.
                with contextlib.suppress(OSError):
                    self.kill()
                time.sleep(0.3)
                raise
        return real_wait(self, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait)
    if PRODUCT == "rootkill":

        def end(tree: Any, proc: Any, *, reap: float) -> None:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=reap)

        monkeypatch.setattr(bash_module, "_end_the_tree", end)
    if PRODUCT == "nokill":
        monkeypatch.setattr(bash_module, "_end_the_tree", lambda tree, proc, *, reap: None)

    witness_cls = getattr(M, "_TimeoutWitness", None)
    if witness_cls is not None:
        real_settle = witness_cls.settle

        def settle(self: Any) -> None:
            real_settle(self)
            deadline = (
                None
                if self.waited_at is None or self.waited_for is None
                else self.waited_at + self.waited_for
            )
            records.append(
                {
                    "timed_out": self.timed_out_at is not None,
                    "gave_up_past_wait_deadline": None
                    if deadline is None or self.timed_out_at is None
                    else round(self.timed_out_at - deadline, 4),
                    "root_then": self.root_then,
                    "look_vs_deadline": None
                    if deadline is None or self.looked_at is None
                    else round(self.looked_at - deadline, 4),
                    "looked_saw": self.looked_saw,
                }
            )

        monkeypatch.setattr(witness_cls, "settle", settle)


async def _run(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch, timeout: float | None, tag: str
) -> None:
    M = importlib.import_module(TESTMOD)
    records: list = []
    waits: list = []
    _install(monkeypatch, M, tmp_path, records, waits)
    case = M.test_a_holder_that_never_falls_idle_hits_the_drain_cap
    t0 = time.monotonic()
    outcome, detail = "passed", ""
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        try:
            await case(tmp_path, strays, monkeypatch, timeout)
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome = "failed"
            detail = f"{type(exc).__name__}: {str(exc)[:900]}"
            raise
        finally:
            row = {
                "tag": tag,
                "py": f"{sys.version_info.major}.{sys.version_info.minor}",
                "platform": sys.platform,
                "load": LOAD,
                "testmod": TESTMOD,
                "root": ROOT,
                "slow": SLOW,
                "linger": LINGER,
                "product": PRODUCT,
                "timeout": timeout,
                "outcome": outcome,
                "detail": detail,
                "wall": round(time.monotonic() - t0, 3),
                "warnings": [str(w.message)[:600] for w in seen],
                "witness": records,
                "root_waits": waits,
            }
            line = json.dumps(row, sort_keys=True)
            if OUT:
                with open(OUT, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            print("V446 " + line)


_PARAMS = {
    "both": [A._DEADLINE_ARM_SECONDS, None],
    "deadline": [A._DEADLINE_ARM_SECONDS],
    "none": [None],
}[PARAMS]


@pytest.mark.parametrize("timeout", _PARAMS, ids=lambda t: "deadline" if t else "none")
@pytest.mark.parametrize("round_", range(ROUNDS))
async def test_rounds(
    tmp_path: Path,
    strays: list[int],
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    round_: int,
) -> None:
    await _run(tmp_path, strays, monkeypatch, timeout, f"round{round_}")
