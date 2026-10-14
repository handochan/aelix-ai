"""#446 r2: A/B rounds on windows-latest. MEASURE BRANCH ONLY - never merged.

Per round, interleaved, both params of
``test_a_holder_that_never_falls_idle_hits_the_drain_cap``: BEFORE is the test
file exactly as it is at e24ea2e8 (``tests/tools/_m446_before.py``, imported,
never collected), AFTER is the fixed one (``tests/tools/test_bash_tool_containment.py``).
One JSON line per run to $M446_OUT: the version, the param, the outcome, the
failure's first lines, every attempt's ``bash exec drain cap`` line and every
premise line the case left (classified), and the wall time - so what the fix
retried is counted per run rather than inferred from a green.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import warnings
from pathlib import Path

import pytest

from tests.tools import test_bash_tool_containment as A

B = importlib.import_module("tests.tools._m446_before")

strays = A.strays

ROUNDS = int(os.environ.get("M446_ROUNDS", "2"))
OUT = os.environ.get("M446_OUT", "")
LOAD = os.environ.get("M446_LOAD", "none")


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


@pytest.mark.parametrize("timeout", [A._DEADLINE_ARM_SECONDS, None], ids=["deadline-arm", "no-deadline"])
@pytest.mark.parametrize("version", ["before", "after"])
@pytest.mark.parametrize("round_", range(ROUNDS))
async def test_ab(
    tmp_path: Path,
    strays: list[int],
    monkeypatch: pytest.MonkeyPatch,
    timeout: float | None,
    version: str,
    round_: int,
) -> None:
    case = (B if version == "before" else A).test_a_holder_that_never_falls_idle_hits_the_drain_cap
    t0 = time.monotonic()
    outcome, detail = "passed", ""
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        try:
            await case(tmp_path, strays, monkeypatch, timeout)
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome = "failed"
            detail = f"{type(exc).__name__}: {str(exc)[:700]}"
            raise
        finally:
            msgs = [str(w.message) for w in seen]
            row = {
                "py": f"{sys.version_info.major}.{sys.version_info.minor}",
                "load": LOAD,
                "version": version,
                "timeout": timeout,
                "round": round_,
                "outcome": outcome,
                "detail": detail,
                "wall": round(time.monotonic() - t0, 4),
                "attempts": [m for m in msgs if _kind(m) == "attempt"],
                "premises": [[_kind(m), m[:520]] for m in msgs if _kind(m).startswith("premise")],
            }
            line = json.dumps(row, sort_keys=True)
            if OUT:
                with open(OUT, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            print("M446AB " + line)


@pytest.mark.parametrize("round_", range(ROUNDS))
async def test_sibling_exposure(
    tmp_path: Path, strays: list[int], monkeypatch: pytest.MonkeyPatch, round_: int
) -> None:
    """The sweep's sibling: the drain-asks-the-pipe file's deadline case (same pwsh root, same 2.0 s, no retry)."""

    from tests.tools import test_bash_drain_asks_the_pipe as S

    t0 = time.monotonic()
    outcome, detail = "passed", ""
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        try:
            await S.test_a_pipe_that_never_empties_ends_at_the_deadline_on_the_second_proof(
                tmp_path, strays, monkeypatch
            )
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome = "failed"
            detail = f"{type(exc).__name__}: {str(exc)[:700]}"
            raise
        finally:
            row = {
                "py": f"{sys.version_info.major}.{sys.version_info.minor}",
                "load": LOAD,
                "version": "sibling",
                "timeout": A._DEADLINE_ARM_SECONDS,
                "round": round_,
                "outcome": outcome,
                "detail": detail,
                "wall": round(time.monotonic() - t0, 4),
                "attempts": [str(w.message)[:300] for w in seen if "#260 full pipe" in str(w.message)],
                "premises": [],
            }
            line = json.dumps(row, sort_keys=True)
            if OUT:
                with open(OUT, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            print("M446AB " + line)
