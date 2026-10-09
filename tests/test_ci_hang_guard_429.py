"""A hung test or post-summary executor join must fail, with thread stacks."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _run_pytest(tmp_path: Path, test: str, *, during_test: bool) -> subprocess.CompletedProcess[str]:
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    if not during_test:
        # Exercise the real unconfigure hook at a short wall budget. The only
        # altered input is its clock; removing the hook leaves the child stuck.
        conftest += """
import faulthandler
_dump = faulthandler.dump_traceback_later
def _short_exit_timeout(timeout, *args, **kwargs):
    return _dump(1.0 if timeout == 180 else timeout, *args, **kwargs)
faulthandler.dump_traceback_later = _short_exit_timeout
"""
    (tmp_path / "conftest.py").write_text(conftest, encoding="utf-8")
    test_path = tmp_path / "test_hang.py"
    test_path.write_text(test, encoding="utf-8")
    env = os.environ.copy()
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(name, None)
    return subprocess.run(
        [
            sys.executable, "-u", "-m", "pytest", "-c", str(ROOT / "pyproject.toml"),
            "--rootdir", str(tmp_path), "-q", "-p", "no:cacheprovider",
            "-o", f"faulthandler_timeout={0.2 if during_test else 0}", str(test_path),
        ],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=15,
    )


@pytest.mark.parametrize("phase", ["call", "teardown"])
def test_a_hung_test_exits_with_thread_stacks(tmp_path: Path, phase: str) -> None:
    if phase == "call":
        test = "import queue\ndef test_hang():\n    queue.Queue().get()\n"
    else:
        test = """
import queue
import pytest
@pytest.fixture
def stuck_teardown():
    yield
    queue.Queue().get()
def test_hang(stuck_teardown):
    pass
"""
    result = _run_pytest(tmp_path, test, during_test=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Timeout" in result.stderr and "queue.py" in result.stderr, result.stderr


def test_a_green_summary_cannot_hide_a_stranded_thread(tmp_path: Path) -> None:
    result = _run_pytest(tmp_path, """
import queue
import threading
def test_hang():
    worker = threading.Thread(target=queue.Queue().get)
    worker.start()
""", during_test=False)
    assert "1 passed" in result.stdout, result.stdout + result.stderr
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Timeout" in result.stderr and "queue.py" in result.stderr, result.stderr


def test_ci_limits_cover_every_test_leg() -> None:
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["test"]
    assert job["timeout-minutes"] == "${{ matrix.os == 'windows-latest' && 50 || 25 }}"
    test_step = next(step for step in job["steps"] if step.get("name") == "Test gate (pytest)")
    assert test_step["timeout-minutes"] == "${{ matrix.os == 'windows-latest' && 45 || 20 }}"
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    assert options["faulthandler_timeout"] == 240
    assert options["faulthandler_exit_on_timeout"] is True
