"""#446 r3: the amended deadline arm on windows-latest, round by round. MEASURE BRANCH ONLY - never merged.

Each round runs ``test_a_holder_that_never_falls_idle_hits_the_drain_cap[deadline-arm]`` exactly as committed
(``tests/tools/test_bash_tool_containment.py``) and writes one JSON line to $M446_OUT: the outcome, the
failure's first lines, every attempt line and premise line the case left (classified), every
``_TimeoutWitness`` record after ``settle`` (instants relative to the wait's own deadline), what
``_why_the_root_timed_out`` was asked (the wait's start after the case's), and every
``_the_timeout_leg_left`` answer (when it looked after the kill, how long it took, what it found).
The wrappers only record; each calls the committed function and returns its answer.
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import pytest

from tests.tools import test_bash_tool_containment as A

strays = A.strays

ROUNDS = int(os.environ.get("M446_ROUNDS", "2"))
OUT = os.environ.get("M446_OUT", "")
LOAD = os.environ.get("M446_LOAD", "none")

WITNESSES: list[Any] = []
ASKED: list[dict[str, Any]] = []
LEFT: list[dict[str, Any]] = []
_REAL_SETTLE = A._TimeoutWitness.settle
_REAL_WHY = A._why_the_root_timed_out
_REAL_LEFT = A._the_timeout_leg_left


def _settle(self: Any) -> None:
    _REAL_SETTLE(self)
    WITNESSES.append(self)


def _why(witness: Any, *, started: float, timeout: float) -> str | None:
    answer = _REAL_WHY(witness, started=started, timeout=timeout)
    ASKED.append(
        {
            "wait_began_after_start": None if witness.waited_at is None else round(witness.waited_at - started, 4),
            "gave_up_after_start": None if witness.timed_out_at is None else round(witness.timed_out_at - started, 4),
            "answer": answer,
        }
    )
    return answer


def _left(witness: Any, pids: Any, marker: Path, strays: list[int]) -> tuple[list[str], list[str]]:
    began = time.monotonic()
    left, checked = _REAL_LEFT(witness, pids, marker, strays)
    LEFT.append(
        {
            "looked_after_kill": None if witness.reap_began is None else round(began - witness.reap_began, 4),
            "took": round(time.monotonic() - began, 4),
            "left": left,
            "checked": checked,
        }
    )
    return left, checked


def _record(w: Any) -> dict[str, Any]:
    deadline = None if w.waited_at is None or w.waited_for is None else w.waited_at + w.waited_for
    rel = (lambda t: None if t is None or deadline is None else round(t - deadline, 4))
    code = None if w.proc is None else w.proc.returncode
    return {
        "timed_out": w.timed_out_at is not None,
        "waited_for": w.waited_for,
        "gave_up": rel(w.timed_out_at),
        "root_then": w.root_then,
        "look_began": rel(w.look_began),
        "looked_at": rel(w.looked_at),
        "looked_saw": w.looked_saw,
        "root_at_reap": w.root_at_reap,
        "reap_began": rel(w.reap_began),
        "reap_took": None if w.reap_began is None or w.reap_back is None else round(w.reap_back - w.reap_began, 4),
        "returncode": code,
        "group": w.group is not None,
        "job": w.job is not None,
    }


def _kind(message: str) -> str:
    if message.startswith("bash exec drain cap:"):
        return "attempt"
    if "THE PREMISE FAILED" not in message:
        return "other"
    if "had not exited by the caller's" in message:
        heat = "deadheat" if "a dead heat" in message else "alive"
        stage = "announced" if "had announced its tree" in message else "unannounced"
        return f"premise-deadline-{heat}-{stage}"
    if "idle rule" in message:
        return "premise-idle"
    return "premise-stall"


@pytest.mark.parametrize("round_", range(ROUNDS))
async def test_rounds(tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch, round_: int) -> None:
    monkeypatch.setattr(A._TimeoutWitness, "settle", _settle)
    monkeypatch.setattr(A, "_why_the_root_timed_out", _why)
    monkeypatch.setattr(A, "_the_timeout_leg_left", _left)
    WITNESSES.clear()
    ASKED.clear()
    LEFT.clear()
    t0 = time.monotonic()
    outcome, detail = "passed", ""
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        try:
            await A.test_a_holder_that_never_falls_idle_hits_the_drain_cap(
                tmp_path, strays, monkeypatch, A._DEADLINE_ARM_SECONDS
            )
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome = "failed"
            detail = f"{type(exc).__name__}: {str(exc)[:900]}"
            raise
        finally:
            msgs = [str(w.message) for w in seen]
            row = {
                "py": f"{sys.version_info.major}.{sys.version_info.minor}",
                "load": LOAD,
                "round": round_,
                "outcome": outcome,
                "detail": detail,
                "wall": round(time.monotonic() - t0, 4),
                "attempts": [m for m in msgs if _kind(m) == "attempt"],
                "premises": [[_kind(m), m[:900]] for m in msgs if _kind(m).startswith("premise")],
                "witness": [_record(w) for w in WITNESSES],
                "asked": list(ASKED),
                "left": list(LEFT),
            }
            line = json.dumps(row, sort_keys=True)
            if OUT:
                with open(OUT, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            print("M446R3 " + line)
