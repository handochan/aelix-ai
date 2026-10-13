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


def _run_pytest(
    tmp_path: Path, test: str, *, during_test: bool, extra: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    """Run ``test`` in a child pytest that loads the real conftest from ``tmp_path``.

    ``--confcutdir`` keeps the child inside ``tmp_path`` (#448). With ``-c``
    naming the repository's pyproject, pytest's default confcutdir is the
    repository root, which is not an ancestor of ``tmp_path``. The child then
    collected every directory above ``tmp_path`` that is not also above the
    repository - on Windows CI, a different drive, so all of them
    (``Session.collect`` adds an argument's parents while ``_is_in_confcutdir``
    holds), listing each one and running the ignore and collect hooks for every
    entry in it. One of those directories is the parent run's basetemp, which
    holds a directory for every test so far: about 20,000 when this gate runs
    in the full suite. On Windows that collection alone took 5-20 s against the
    15 s budget, while the rest of the child took about 1.8 s. Bounding the walk
    at ``tmp_path`` makes the child's work independent of where the parent run is.
    """
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
    # PYTHONSAFEPATH would drop the cwd that ``python -m`` puts on sys.path,
    # and a ``-p`` plugin written next to the test could not be imported.
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONSAFEPATH"):
        env.pop(name, None)
    return subprocess.run(
        [
            sys.executable, "-u", "-m", "pytest", "-c", str(ROOT / "pyproject.toml"),
            "--rootdir", str(tmp_path), "--confcutdir", str(tmp_path), "-q",
            "-p", "no:cacheprovider", *extra,
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


@pytest.mark.parametrize("safepath", [None, "1"], ids=["default", "PYTHONSAFEPATH=1"])
def test_the_child_collects_nothing_above_its_own_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, safepath: str | None
) -> None:
    """The gate's child pytest must not walk the parent run's directories (#448).

    A ``-p`` plugin records every directory the child turns into a collector
    and every path it asks the ignore hook about. ``outer`` stands in for the
    parent's basetemp with a sibling directory beside the child. Without
    ``--confcutdir`` the child visits ``outer``, the sibling and every ancestor
    that is not also an ancestor of the repository, and its cost grows with
    each of their entries. The plugin is imported from the child's cwd, so a
    PYTHONSAFEPATH in the parent's environment must not reach the child.
    """
    if safepath is None:
        monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONSAFEPATH", safepath)
    child = tmp_path / "outer" / "child"
    child.mkdir(parents=True)
    (tmp_path / "outer" / "sibling").mkdir()
    (child / "walk_probe.py").write_text(
        """
from pathlib import Path
_seen = []
def pytest_collect_directory(path, parent):
    _seen.append(str(path))
def pytest_ignore_collect(collection_path, config):
    _seen.append(str(collection_path))
def pytest_collection_finish(session):
    Path(__file__).with_name("walked.txt").write_text("\\n".join(_seen), encoding="utf-8")
""",
        encoding="utf-8",
    )
    result = _run_pytest(
        child, "def test_ok():\n    pass\n", during_test=False, extra=("-p", "walk_probe")
    )
    assert "1 passed" in result.stdout, result.stdout + result.stderr
    walked = [Path(p) for p in (child / "walked.txt").read_text(encoding="utf-8").splitlines()]
    assert walked, "the probe saw no collection at all"
    outside = [p for p in walked if p != child and child not in p.parents]
    assert not outside, f"the child collected above its own directory: {outside[:5]}"


def test_ci_limits_cover_every_test_leg() -> None:
    job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["test"]
    assert job["timeout-minutes"] == "${{ matrix.os == 'windows-latest' && 50 || 25 }}"
    test_step = next(step for step in job["steps"] if step.get("name") == "Test gate (pytest)")
    assert test_step["timeout-minutes"] == "${{ matrix.os == 'windows-latest' && 45 || 20 }}"
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    assert options["faulthandler_timeout"] == 240
    assert options["faulthandler_exit_on_timeout"] is True
