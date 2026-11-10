"""``tests/plugins/coarse_clock.py`` gives the clock Windows py<=3.12 has, and only when asked (#733).

The plugin reproduced #672/#673 on macOS: a test that failed only on the
Windows py3.11 and py3.12 legs failed the same way once ``time.monotonic``
ticked every 15.625 ms. Each case runs a probe test in a child pytest, from the
repository root as the plugin's docstring says to, so this process's clock is
never touched.

The probe spins for 80 ms reading ``time.monotonic()`` and writes what it saw.
With the plugin every value is a whole number of ticks and the resolution says
one tick; without it, two distinct reads come closer together than one tick.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TICK = 0.015625

_PROBE = """
import json, os, time

def test_probe():
    info = time.get_clock_info("monotonic")
    seen = [time.monotonic()]
    start = time.perf_counter()
    while time.perf_counter() - start < 0.08:
        now = time.monotonic()
        if now != seen[-1]:
            seen.append(now)
    with open(os.environ["COARSE_PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump({"resolution": info.resolution, "seen": seen}, fh)
"""


def _run_probe(tmp_path: Path, *plugin_args: str) -> dict:
    probe = tmp_path / "test_coarse_probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    out = tmp_path / "probe.json"
    env = {**os.environ, "COARSE_PROBE_OUT": str(out)}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *plugin_args, "-p", "no:cacheprovider", "-q", str(probe)],
        cwd=REPO,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _whole_ticks(value: float) -> bool:
    ticks = value / TICK
    return abs(ticks - round(ticks)) < 1e-6


def test_with_the_plugin_monotonic_moves_in_whole_ticks(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, "-p", "tests.plugins.coarse_clock")
    assert result["resolution"] == TICK
    seen = result["seen"]
    # 80 ms of spinning crosses at least four tick boundaries; fewer than two
    # distinct values would make the step check below vacuous.
    assert len(seen) >= 2, seen
    assert all(_whole_ticks(v) for v in seen), seen
    steps = [b - a for a, b in pairwise(seen)]
    assert all(step > 0 and _whole_ticks(step) for step in steps), steps


@pytest.mark.skipif(
    sys.platform == "win32" and sys.version_info < (3, 13),
    reason="Windows py<=3.12 has the coarse clock natively; the control needs a fine one",
)
def test_without_the_plugin_the_clock_is_finer_than_a_tick(tmp_path: Path) -> None:
    """The control: the plugin is what makes the probe above coarse, not the probe."""

    result = _run_probe(tmp_path)
    assert result["resolution"] < TICK
    seen = result["seen"]
    steps = [b - a for a, b in pairwise(seen)]
    assert steps, seen
    assert min(steps) < TICK, steps


def test_the_plugin_is_not_collected_and_not_loaded_by_conftest() -> None:
    """Opt-in means no ``test_*.py`` name and no import from any ``conftest.py``."""

    plugin = REPO / "tests" / "plugins" / "coarse_clock.py"
    assert plugin.is_file()
    assert not plugin.name.startswith("test_") and not plugin.stem.endswith("_test")
    for conftest in (REPO / "tests").rglob("conftest.py"):
        text = conftest.read_text(encoding="utf-8")
        assert "coarse_clock" not in text, conftest
