"""A hung test or post-summary executor join must fail, with thread stacks."""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

#: #482 (ADR-0274): the slowest green ``Test gate (pytest)`` step per runner over
#: 129 ci.yml runs from 2026-10-08 to 2026-10-19 (627 green steps; a job that a
#: re-run carried into a later attempt counted once), and the slowest rest of a
#: green job (checkout, uv, sync, ruff, pyright and post steps). A re-measurement
#: moves these, the limits, ci.yml's comment, docs/development/ci-hang-diagnostics.md
#: and a CHANGELOG entry together.
SLOWEST_GREEN_PYTEST_MIN = {"ubuntu-latest": 19.5, "windows-latest": 40.0}
#: A green step can never show more than the limit it ran under, so a step that
#: the old limit killed with no test failed or stuck counts at its projected full
#: time. Run 38610624957 attempt 1 (Ubuntu, py3.12) was killed at #429's 20 min at
#: 85%; aligned line by line with the same commit's two passes, its progress lines
#: project 25.3 min (23.8 to 26.1 by the other projections). No Windows step ended
#: on its limit.
PROJECTED_KILLED_PYTEST_MIN = {"ubuntu-latest": 25.3}
SLOWEST_GREEN_REST_OF_JOB_MIN = 3.2
MARGIN_FACTOR = 1.3
#: The CHANGELOG entries that state the current limits. A re-measurement adds its
#: own entry and names it here instead.
LIMITS_CHANGELOG_ISSUES = (429, 482)
#: #429's limits before #482. Stated as current in ci.yml's comment, the
#: diagnostics page or #429's CHANGELOG entry, they fail even beside the new ones.
OLD_429_LIMITS = {
    "job limit": {"ubuntu-latest": 25, "windows-latest": 50},
    "step limit": {"ubuntu-latest": 20, "windows-latest": 45},
}
_PER_OS_LIMIT = re.compile(r"\$\{\{ matrix\.os == 'windows-latest' && (\d+) \|\| (\d+) \}\}")
_OS_NAMES = {"ubuntu-latest": "Ubuntu", "windows-latest": "Windows"}
_COUNT_WORDS = {4: "four", 6: "six", 8: "eight", 10: "ten", 12: "twelve"}


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


def _per_os(expression: str) -> dict[str, int]:
    match = _PER_OS_LIMIT.fullmatch(expression)
    assert match, expression
    return {"windows-latest": int(match[1]), "ubuntu-latest": int(match[2])}


def _on(minutes: Mapping[str, float]) -> str:
    """How every pinned sentence states a per-OS pair: '35 min on Ubuntu and 55 min on Windows'."""
    return " and ".join(f"{minutes[runner]} min on {name}" for runner, name in _OS_NAMES.items())


def _prose(text: str) -> str:
    return " ".join(text.split())


def _comment_above(ci_text: str, line: str) -> str:
    """The ``#`` comment block directly above ``line`` in ci.yml, as one line of prose."""
    lines = [text.strip() for text in ci_text.splitlines()]
    assert lines.count(line) == 1, f"ci.yml has {lines.count(line)} lines {line!r}"
    block: list[str] = []
    for text in reversed(lines[: lines.index(line)]):
        if not text.startswith("#"):
            break
        block.insert(0, text.lstrip("#"))
    return _prose(" ".join(block))


def _changelog_entry(changelog: str, issue: int) -> str:
    entries = [_prose(entry) for entry in re.split(r"(?m)^(?=- \*\*|#)", changelog)]
    found = [entry for entry in entries if re.match(rf"- \*\*[^*]*\(#{issue}\)\.\*\*", entry)]
    assert len(found) == 1, f"CHANGELOG.md has {len(found)} entries titled with #{issue}"
    return found[0]


def test_ci_limits_cover_every_test_leg() -> None:
    """The limits are ADR-0274's rule over the measured runs (#482).

    The pytest step's limit is the slowest step x 1.3, rounded up to five
    minutes, where the slowest step is the larger of the slowest green step and
    the projected time of a step the old limit killed while it was still green.
    #429's 20/45 left 0.5 min over a green Ubuntu step, and run 38610624957 was
    killed on it with no test failed or stuck. The job's limit is five minutes
    above the step's, more than everything else a green job spends, so a hang
    inside pytest is reported by its step first. The per-test watchdog that
    catches a single stuck test stays at 240 s. The sentences that state these
    numbers in ci.yml's comment, docs/development/ci-hang-diagnostics.md and the
    CHANGELOG are pinned, per OS, to the same values, and #429's old limits
    stated as current fail even next to them. Neither the job nor its pytest
    step may continue on error, which would let a red or hung leg through.
    """
    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = yaml.safe_load(ci_text)["jobs"]["test"]
    assert job["timeout-minutes"] == "${{ matrix.os == 'windows-latest' && 60 || 40 }}"
    test_step = next(step for step in job["steps"] if step.get("name") == "Test gate (pytest)")
    assert test_step["timeout-minutes"] == "${{ matrix.os == 'windows-latest' && 55 || 35 }}"
    assert "continue-on-error" not in job, "the lint + test job has continue-on-error"
    assert "continue-on-error" not in test_step, "the 'Test gate (pytest)' step has continue-on-error"
    matrix = job["strategy"]["matrix"]
    assert sorted(matrix["os"]) == sorted(SLOWEST_GREEN_PYTEST_MIN) == sorted(_OS_NAMES)
    assert set(PROJECTED_KILLED_PYTEST_MIN) <= set(SLOWEST_GREEN_PYTEST_MIN)
    job_limit = _per_os(job["timeout-minutes"])
    step_limit = _per_os(test_step["timeout-minutes"])
    for runner, slowest in SLOWEST_GREEN_PYTEST_MIN.items():
        step, whole_job = step_limit[runner], job_limit[runner]
        basis = max(slowest, PROJECTED_KILLED_PYTEST_MIN.get(runner, 0.0))
        rule = math.ceil(basis * MARGIN_FACTOR / 5) * 5
        assert step == rule, f"{runner}: step limit {step}, ADR-0274 gives ceil5({basis} x {MARGIN_FACTOR}) = {rule}"
        assert whole_job == step + 5, f"{runner}: job limit {whole_job} is not the step limit {step} + 5"
        # Holds while the +5 does; a re-measured rest of the job above 5 min must widen the gap.
        assert whole_job - step > SLOWEST_GREEN_REST_OF_JOB_MIN, f"{runner}: the job limit would fire first"

    # The sentences that state the numbers move with them. Each one is pinned per
    # OS, so a swapped pair or another factor in place of the right one fails
    # here; a stale sentence beside the right one is caught only for #429's old
    # limits, below.
    sentences = {
        "the slowest green steps": f"step took {_on(SLOWEST_GREEN_PYTEST_MIN)}",
        **{
            f"the killed {_OS_NAMES[runner]} step": f"would have taken about {minutes} min on {_OS_NAMES[runner]}"
            for runner, minutes in PROJECTED_KILLED_PYTEST_MIN.items()
        },
        "the rule": f"x {MARGIN_FACTOR}, rounded up to 5 min",
        "the step limits": f"step limit is {_on(step_limit)}",
        "the job limits": f"job limit is {_on(job_limit)}",
        "the rest of a green job": f"at most {SLOWEST_GREEN_REST_OF_JOB_MIN} min",
    }
    jobs = len(matrix["os"]) * len(matrix["python-version"])
    doc = _prose((ROOT / "docs/development/ci-hang-diagnostics.md").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    limits_only = ("the step limits", "the job limits")
    job_comment = _comment_above(ci_text, f"timeout-minutes: {job['timeout-minutes']}")
    sources: dict[str, tuple[str, dict[str, str]]] = {
        "ci.yml's comment on the job's limit": (job_comment, sentences),
        "ci-hang-diagnostics.md": (
            doc, {**sentences, "the job count": f"The {_COUNT_WORDS[jobs]} lint/test matrix jobs"}
        ),
    }
    *older, latest = LIMITS_CHANGELOG_ISSUES
    for issue in older:
        sources[f"the CHANGELOG entry for #{issue}"] = (
            _changelog_entry(changelog, issue), {what: sentences[what] for what in limits_only}
        )
    sources[f"the CHANGELOG entry for #{latest}"] = (
        _changelog_entry(changelog, latest),
        {what: sentence for what, sentence in sentences.items() if what != "the rest of a green job"},
    )
    for source, (text, wanted) in sources.items():
        for what, sentence in wanted.items():
            assert sentence in text, f"{source} does not state {what} as {sentence!r}"

    # #429's old limits stated as current, in #429's own wording or in the pinned
    # one, fail in the three places that were #429's record of them.
    stale: list[str] = []
    for kind, old in OLD_429_LIMITS.items():
        ubuntu, windows = old["ubuntu-latest"], old["windows-latest"]
        stale += [
            f"{kind} is {_on(old)}",
            f"a {ubuntu} minute limit on Ubuntu and a {windows} minute limit on Windows",
            f"{ubuntu} and {windows} minute limits",
            f"limits of {ubuntu}/{windows} minutes",
            f"{ubuntu}/{windows} minute limits",
        ]
    old_records = {
        "ci.yml's comment on the job's limit": job_comment,
        "ci-hang-diagnostics.md": doc,
        "the CHANGELOG entry for #429": _changelog_entry(changelog, 429),
    }
    for source, text in old_records.items():
        for sentence in stale:
            assert sentence not in text, f"{source} still states #429's old limits: {sentence!r}"

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    assert options["faulthandler_timeout"] == 240
    assert options["faulthandler_exit_on_timeout"] is True


def test_every_ci_job_is_bounded() -> None:
    """#279 added a job and found the e2e one beside it unbounded: a job with no
    ``timeout-minutes`` spends GitHub's default six hours when a download or an
    installer stalls (#428). Every job in ci.yml states its own bound."""

    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    unbounded = [job_id for job_id, job in jobs.items() if "timeout-minutes" not in job]
    assert not unbounded, unbounded
    for job_id in ("install-ps1-e2e", "install-sh-e2e"):
        assert isinstance(jobs[job_id]["timeout-minutes"], int), job_id
        assert jobs[job_id]["timeout-minutes"] <= 30, job_id
