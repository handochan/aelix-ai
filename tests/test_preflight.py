"""``scripts/preflight.py`` runs the gates a change can fail, and only those (#733, ADR-0303).

What is pinned: the change it reads (commits since the merge base, staged,
unstaged and untracked-not-ignored, deletions included), which gate a path
starts, that no pytest gate runs without paths (that would be the full suite),
the exit status, the ``--claims`` block holding no local path, and that a gate
past its timeout is killed and reported failed.

The end-to-end cases copy the script into a scratch repository, so ``REPO`` is
that repository and the real checkout is never read or changed by a gate.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.env_sandbox import sandbox_home

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "preflight.py"


def _load() -> Any:
    name = "_preflight_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pf = _load()


def _git_env(tmp_path: Path) -> dict[str, str]:
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    return env


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    """A repository with ``origin/main`` at its first commit and the script under ``scripts/``."""

    env = _git_env(tmp_path)
    for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.setenv(key, env[key])
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, env, "init", "-q", "-b", "main")
    _write(repo / ".gitignore", "ignored.txt\n__pycache__/\n")
    _write(repo / "README.md", "readme\n")
    _write(repo / "gone.txt", "will be deleted\n")
    _write(repo / "old_name.py", "".join(f"line_{i} = {i}\n" for i in range(40)))
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", "X = 1\n")
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert True\n")
    (repo / "scripts").mkdir()
    shutil.copyfile(SCRIPT, repo / "scripts" / "preflight.py")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", "base")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, env


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "preflight.py"), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


# --- the change ----------------------------------------------------------------


def test_the_change_is_commits_staged_unstaged_and_untracked(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    _write(repo / "committed.py", "a = 1\n")
    _git(repo, env, "add", "committed.py")
    _git(repo, env, "rm", "-q", "gone.txt")
    _git(repo, env, "mv", "old_name.py", "new_name.py")  # a rename names both sides
    _git(repo, env, "commit", "-q", "-m", "on top")
    _write(repo / "staged.py", "b = 1\n")
    _git(repo, env, "add", "staged.py")
    _write(repo / "README.md", "edited, not staged\n")
    _write(repo / "loose.py", "c = 1\n")
    _write(repo / "ignored.txt", "ignored\n")

    base_ref, base = pf.resolve_base(repo, None)
    assert base_ref == "origin/main"
    paths = pf.changed_paths(repo, base)
    assert paths == {
        "README.md": frozenset({"unstaged"}),
        "committed.py": frozenset({"committed"}),
        "gone.txt": frozenset({"committed"}),
        "loose.py": frozenset({"untracked"}),
        "new_name.py": frozenset({"committed"}),
        "old_name.py": frozenset({"committed"}),
        "staged.py": frozenset({"staged"}),
    }


def test_the_base_is_the_merge_base_not_the_branch_tip(scratch: tuple[Path, dict[str, str]]) -> None:
    """A commit that landed on main after the branch left must not count as this change."""

    repo, env = scratch
    fork = _git(repo, env, "rev-parse", "HEAD").strip()
    _git(repo, env, "checkout", "-q", "-b", "side")
    _write(repo / "mine.py", "m = 1\n")
    _git(repo, env, "add", "mine.py")
    _git(repo, env, "commit", "-q", "-m", "mine")
    _git(repo, env, "checkout", "-q", "main")
    _write(repo / "theirs.py", "t = 1\n")
    _git(repo, env, "add", "theirs.py")
    _git(repo, env, "commit", "-q", "-m", "theirs")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, env, "checkout", "-q", "side")

    ref, base = pf.resolve_base(repo, None)
    assert (ref, base) == ("origin/main", fork)
    assert set(pf.changed_paths(repo, base)) == {"mine.py"}


def test_without_origin_main_the_base_is_main_and_without_either_it_is_a_usage_error(
    scratch: tuple[Path, dict[str, str]],
) -> None:
    repo, env = scratch
    _git(repo, env, "update-ref", "-d", "refs/remotes/origin/main")
    assert pf.resolve_base(repo, None)[0] == "main"
    _git(repo, env, "branch", "-q", "-m", "main", "trunk")
    with pytest.raises(pf.UsageError):
        pf.resolve_base(repo, None)
    assert pf.resolve_base(repo, "trunk")[0] == "trunk"
    proc = _run(repo, env, "--plan")
    assert proc.returncode == 2 and "pass --base REF" in proc.stderr


# --- which gates a path starts --------------------------------------------------


def _changes(*paths: str, top: tuple[str, ...] = ("packages", "src", "tests", "docs", "scripts")) -> Any:
    return pf.ChangeSet(
        "origin/main", "0" * 40, "1" * 40, {p: frozenset({"committed"}) for p in paths}, frozenset(top), REPO
    )


def _args(*argv: str) -> argparse.Namespace:
    return pf._parse(list(argv))


def _would_run(
    *paths: str, argv: tuple[str, ...] = (), tests: tuple[str, ...] = ("tests/test_agent_skills.py",)
) -> set[str]:
    changes = _changes(*paths)
    ctx = pf.Context(REPO, sys.executable, changes, list(tests), _args(*argv))
    return {gate.name for gate, verdict, _why, _steps in pf.plan(ctx) if verdict == "run"}


ALWAYS = {"untracked-tests", "ruff", "types", "citations"}


def test_with_nothing_changed_only_the_always_gates_run() -> None:
    assert _would_run(tests=()) == ALWAYS


def test_a_product_module_starts_the_product_gates() -> None:
    path = "packages/aelix-agent-core/src/aelix_agent_core/harness/skills.py"
    assert _would_run(path) == ALWAYS | {
        "import-direction", "band", "pi-parity", "test-hygiene", "touched-tests", "pyright-windows"
    }


def test_the_umbrella_package_is_product_too() -> None:
    assert {"band", "pi-parity", "import-direction"} <= _would_run("src/aelix/__init__.py")
    assert not pf.is_product_py("scripts/preflight.py") and not pf.is_product_py("tests/test_x.py")


def test_a_test_change_starts_the_touched_tests_and_the_hygiene_scans() -> None:
    assert _would_run("tests/test_agent_skills.py") == ALWAYS | {"test-hygiene", "touched-tests"}


@pytest.mark.parametrize(
    ("path", "runs"),
    [
        ("tests/test_x.py", True),
        ("tests/fixtures/data.json", True),
        ("tests/conftest.py", True),
        ("packages/aelix-ai/src/aelix_ai/x.py", True),
        ("src/aelix/x.py", True),
        ("packages/aelix-ai/pyproject.toml", False),
        ("scripts/preflight.py", False),
        ("docs/guides/getting-started.md", False),
        (".agents/skills/aelix-preflight/SKILL.md", False),
    ],
)
def test_the_hygiene_gate_runs_on_any_test_path_or_product_module(path: str, runs: bool) -> None:
    """Fix round 2: a scan of every test file can fail on any file under ``tests/``, not only a test module."""

    assert ("test-hygiene" in _would_run(path, tests=())) is runs


def test_every_hygiene_entry_names_a_test_file_or_a_test_in_it() -> None:
    """A renamed test would make the gate's pytest exit 4 on an id that is not there."""

    import ast

    assert len(set(pf.HYGIENE_TESTS)) == len(pf.HYGIENE_TESTS)
    for entry in pf.HYGIENE_TESTS:
        path, _, name = entry.partition("::")
        assert pf.is_test_file(path) and (REPO / path).is_file(), entry
        if name:
            tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
            defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name in defs, entry
    ctx = pf.Context(REPO, sys.executable, _changes("tests/test_x.py"), [], _args())
    (step,) = pf._hygiene(ctx)
    assert step.argv[step.argv.index("pytest") + 1 :] == [*pf.PYTEST[2:], *pf.HYGIENE_TESTS]
    assert isinstance(pf._hygiene(pf.Context(REPO / "nowhere", sys.executable, _changes(), [], _args())), str)


def test_a_guide_starts_the_docs_bundle_and_nothing_product() -> None:
    assert _would_run("docs/guides/getting-started.md", tests=()) == ALWAYS | {"docs-bundle"}
    bundled = "packages/aelix-coding-agent/src/aelix_coding_agent/docs/getting-started.md"
    assert "docs-bundle" in _would_run(bundled, tests=())


@pytest.mark.parametrize(
    "path", [".gitignore", ".gitattributes", "pyproject.toml", "packages/aelix-ai/pyproject.toml", "uv.lock", "brand-new/x.txt"]
)
def test_packaging_inputs_and_a_new_top_level_path_start_the_packaging_gate(path: str) -> None:
    assert "packaging" in _would_run(path, tests=())


def test_a_path_under_an_existing_top_level_does_not_start_packaging() -> None:
    assert "packaging" not in _would_run("docs/decisions/0001-x.md", tests=())


def test_the_opt_in_gates_need_their_flag_or_only() -> None:
    path = "tests/test_agent_skills.py"
    assert not {"docker", "pythons", "windows-clock"} & _would_run(path)
    assert "windows-clock" in _would_run(path, argv=("--windows-clock",))
    assert "windows-clock" in _would_run(path, argv=("--only", "windows-clock"))
    assert _would_run(path, argv=("--only", "ruff")) == {"ruff"}
    assert "ruff" not in _would_run(path, argv=("--skip", "ruff"))


def test_no_pytest_step_is_ever_the_full_suite() -> None:
    """A ``python -m pytest`` with no path runs ``testpaths``, which is the full suite."""

    changes = _changes(
        "packages/aelix-agent-core/src/aelix_agent_core/harness/skills.py",
        "docs/guides/getting-started.md",
        "pyproject.toml",
        "tests/test_agent_skills.py",
    )
    argv = ("--windows-clock", "--pythons", "3.11")
    ctx = pf.Context(REPO, sys.executable, changes, ["tests/test_agent_skills.py"], _args(*argv))
    pytest_steps = 0
    for _gate, verdict, _why, steps in pf.plan(ctx):
        for step in steps if verdict == "run" else []:
            if step.argv and "pytest" in step.argv:
                pytest_steps += 1
                after = step.argv[step.argv.index("pytest") + 1 :]
                paths = [a for a in after if a.startswith("tests")]
                assert paths, step.shown
    assert pytest_steps >= 5
    # And a gate whose test files are all missing is skipped, not run bare.
    empty = pf.Context(REPO / "nowhere", sys.executable, changes, [], _args())
    assert isinstance(pf._pytest(empty, "tests/pi_parity"), str)


def test_the_touched_tests_are_changed_tests_and_tests_naming_a_changed_module(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "packages" / "pkg" / "src" / "pkg" / "sub" / "mod.py", "")
    _write(root / "tests" / "test_dotted.py", "from pkg.sub.mod import thing\n")
    _write(root / "tests" / "test_from_parent.py", "from pkg.sub import other, mod\n")
    _write(root / "tests" / "test_paren.py", "from pkg.sub import (\n    other,\n    mod,\n)\n")
    _write(root / "tests" / "test_string.py", 'monkeypatch.setattr("pkg.sub.mod.X", 1)\n')
    _write(root / "tests" / "test_longer.py", "import pkg.sub.module_two\n")
    _write(root / "tests" / "test_unrelated.py", "import os\n")
    _write(root / "tests" / "test_changed.py", "")
    _write(root / "tests" / "test_script.py", 'SCRIPT = REPO / "scripts" / "tool.py"\n')
    _write(root / "tests" / "helper.py", "pkg.sub.mod\n")  # not a test file
    changes = pf.ChangeSet(
        "b",
        "0" * 40,
        "1" * 40,
        {
            "packages/pkg/src/pkg/sub/mod.py": frozenset({"unstaged"}),
            "tests/test_changed.py": frozenset({"unstaged"}),
            "tests/test_deleted.py": frozenset({"committed"}),
            "scripts/tool.py": frozenset({"committed"}),
        },
        frozenset(),
        root,
    )
    assert pf.touched_tests(changes) == [
        "tests/test_changed.py",
        "tests/test_dotted.py",
        "tests/test_from_parent.py",
        "tests/test_paren.py",
        "tests/test_script.py",
        "tests/test_string.py",
    ]


@pytest.mark.parametrize(
    ("path", "dotted"),
    [
        ("packages/aelix-ai/src/aelix_ai/providers/_tls_strict.py", "aelix_ai.providers._tls_strict"),
        ("packages/aelix-ai/src/aelix_ai/providers/__init__.py", "aelix_ai.providers"),
        ("src/aelix/__init__.py", "aelix"),
        ("tests/harness/_cancelled_turn.py", "tests.harness._cancelled_turn"),
        ("tests/conftest.py", None),
        ("tests/test_x.py", None),
        ("docs/guides/x.md", None),
    ],
)
def test_module_names(path: str, dotted: str | None) -> None:
    assert pf.module_name(path) == dotted


def test_pyright_errors_counts_only_the_named_files(tmp_path: Path) -> None:
    named = tmp_path / "changed.py"
    other = tmp_path / "unchanged.py"
    report = json.dumps(
        {
            "generalDiagnostics": [
                {"file": str(named), "severity": "error", "message": "bad\nmore", "range": {"start": {"line": 4}}},
                {"file": str(named), "severity": "warning", "message": "meh", "range": {"start": {"line": 1}}},
                {"file": str(other), "severity": "error", "message": "not ours", "range": {"start": {"line": 0}}},
            ]
        }
    )
    assert pf.pyright_errors(report, [named]) == ["changed.py line 5: bad"]


def test_fix_citations_runs_fix_before_check_and_only_when_asked() -> None:
    ctx = pf.Context(REPO, sys.executable, _changes(), [], _args())
    assert [s.shown for s in pf._citations(ctx)] == ["python scripts/check_citations.py --check"]
    ctx = pf.Context(REPO, sys.executable, _changes(), [], _args("--fix-citations"))
    assert [s.shown for s in pf._citations(ctx)] == [
        "python scripts/check_citations.py --fix",
        "python scripts/check_citations.py --check",
    ]


# --- running, end to end ------------------------------------------------------------


def test_an_untracked_test_fails_the_run_until_it_is_added(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    _write(repo / "tests" / "test_new.py", "def test_new():\n    assert True\n")
    proc = _run(repo, env, "--only", "untracked-tests")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "tests/test_new.py" in proc.stdout and "git add" in proc.stdout
    assert "failed   untracked-tests" in proc.stdout
    _git(repo, env, "add", "tests/test_new.py")
    proc = _run(repo, env, "--only", "untracked-tests")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed   untracked-tests" in proc.stdout


def test_the_hygiene_gate_fails_on_a_home_set_outside_the_helper(scratch: tuple[Path, dict[str, str]]) -> None:
    """Fix round 2: round 1's direct ``HOME`` in this file failed only the full suite.

    The scratch repository gets the real HOME-sandbox scan and a test that sets
    ``HOME`` itself; the gate runs the scan, fails, and shows the offending line.
    """

    repo, env = scratch
    for name in ("env_sandbox.py", "test_env_sandbox_windows.py"):
        shutil.copyfile(REPO / "tests" / name, repo / "tests" / name)
    # Spelled in two parts so this file does not hold the shape the scan looks for.
    offending = "    monkeypatch.set" + 'env("HOME", str(tmp_path))'
    planted = "tests/test_sets_home.py"
    _write(repo / planted, f"def test_x(monkeypatch, tmp_path):\n{offending}\n")
    _git(repo, env, "add", "-A")
    proc = _run(repo, env, "--only", "test-hygiene")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed   test-hygiene" in proc.stdout
    line = 2
    assert f"{planted}:{line}:{offending}" in proc.stdout
    _git(repo, env, "rm", "-q", "-f", planted)
    proc = _run(repo, env, "--only", "test-hygiene")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed   test-hygiene" in proc.stdout


def test_the_summary_names_every_gate_and_the_full_suite_comes_last(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 1 + 1 == 2\n")
    proc = _run(repo, env, "--only", "untracked-tests,touched-tests")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = proc.stdout.split("summary:\n", 1)[1].splitlines()
    named = [line.split()[1] for line in summary if line.startswith("  ")]
    assert named == list(pf.GATE_NAMES)
    assert "passed   touched-tests" in proc.stdout
    assert proc.stdout.rstrip().splitlines()[-1] == f"still owed: the full suite, which preflight never runs: {pf.FULL_SUITE}"


def test_a_failing_gate_makes_the_run_exit_1_and_shows_its_output(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 'left' == 'right'\n")
    proc = _run(repo, env, "--only", "touched-tests")
    assert proc.returncode == 1
    assert "failed   touched-tests" in proc.stdout
    assert "    | " in proc.stdout and "1 failed" in proc.stdout


def test_usage_errors_exit_2(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    assert _run(repo, env, "--only", "no-such-gate").returncode == 2
    assert _run(repo, env, "--pythons", "3.11.4").returncode == 2
    assert _run(repo, env, "--bogus").returncode == 2


def test_plan_runs_nothing_and_exits_0(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    raise SystemExit('would fail if run')\n")
    _write(repo / "tests" / "test_untracked.py", "")
    proc = _run(repo, env, "--plan")
    assert proc.returncode == 0, proc.stderr
    assert "would run:" in proc.stdout and "would skip:" in proc.stdout
    assert "touched-tests     tests/test_old.py" in proc.stdout
    assert "summary:" not in proc.stdout
    assert proc.stdout.rstrip().endswith(pf.FULL_SUITE)


def test_docker_missing_is_a_skip_not_a_failure(scratch: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 2 > 1\n")
    git = shutil.which("git")
    assert git is not None
    env = {**env, "PATH": os.pathsep.join([str(Path(git).parent), str(Path(sys.executable).parent)])}
    if shutil.which("docker", path=env["PATH"]):
        pytest.skip("docker sits beside git on this machine; cannot hide it")
    proc = _run(repo, env, "--only", "docker", "--docker")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipped  docker" in proc.stdout and "docker is not installed" in proc.stdout


def test_claims_record_each_gate_and_no_local_path(scratch: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    print(__file__)\n    assert False\n")
    claims = tmp_path / "claims.md"
    proc = _run(repo, env, "--only", "untracked-tests,touched-tests", "--claims", str(claims))
    assert proc.returncode == 1
    text = claims.read_text(encoding="utf-8")
    assert text.startswith("## How it was verified")
    assert "| untracked-tests | passed |" in text and "| touched-tests | failed |" in text
    assert "| ruff | skipped: not in --only |" in text
    assert "$ python -m pytest -p no:cacheprovider -q -W error::pytest.PytestUnraisableExceptionWarning tests/test_old.py" in text
    assert "**touched-tests**: exit 1" in text
    assert "Not run by preflight: the full suite" in text
    for local in (str(repo), repo.as_posix(), sys.executable, str(Path.home())):
        assert local not in text, local


def test_a_gate_past_its_timeout_is_killed_and_fails(tmp_path: Path) -> None:
    ctx = pf.Context(tmp_path, sys.executable, _changes(), [], _args())
    sleeper = pf.Step(shown="sleep", argv=[sys.executable, "-c", "import time; time.sleep(60)"])
    gate = pf.Gate("slow", None, "always", lambda c: [sleeper], 1.0)
    started = time.monotonic()
    results = pf.run_gate(gate, [sleeper], ctx)
    assert time.monotonic() - started < 20
    (_step, rc, _took, out), = results
    assert rc is None and "timed out" in out
    assert pf._status(results) == "failed"


def test_a_step_that_raises_fails_its_gate_instead_of_ending_the_run(tmp_path: Path) -> None:
    ctx = pf.Context(tmp_path, sys.executable, _changes(), [], _args())

    def broken(c: Any, timeout: float) -> tuple[int | None, str]:
        raise subprocess.CalledProcessError(128, ["git", "archive"])

    step = pf.Step(shown="git archive HEAD", run=broken)
    (_step, rc, _took, out), = pf.run_gate(pf.Gate("boom", None, "always", lambda c: [step], 5.0), [step], ctx)
    assert rc == 1 and "CalledProcessError" in out


# --- fix round 1 (#733): what --plan starts, the one uv gate, empty gates, output -----

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="drives a /bin/sh stand-in for a command")


def _recording_command(bin_dir: Path, name: str, calls: Path, info_rc: int = 0) -> None:
    """A stand-in for *name* that appends its argv to *calls*; ``info`` exits *info_rc*."""

    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(
        f'#!/bin/sh\necho "$@" >> "{calls}"\nif [ "$1" = info ]; then exit {info_rc}; fi\nexit 1\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


def _path_with(bin_dir: Path) -> str:
    git = shutil.which("git")
    assert git is not None
    return os.pathsep.join([str(bin_dir), str(Path(git).parent), str(Path(sys.executable).parent)])


@posix_only
def test_plan_starts_no_docker_and_the_docker_gate_asks_only_when_it_runs(
    scratch: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    """Codex 1: ``--plan --docker`` used to run ``docker info`` while listing the gate."""

    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 2 > 1\n")
    calls = tmp_path / "docker-calls.txt"
    _recording_command(tmp_path / "bin", "docker", calls, info_rc=1)
    env = {**env, "PATH": _path_with(tmp_path / "bin")}

    for argv in (("--plan", "--docker"), ("--plan", "--only", "docker")):
        proc = _run(repo, env, *argv)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        run_part = proc.stdout.split("would run:", 1)[1].split("would skip:", 1)[0]
        assert "docker info (when the gate runs" in run_part
        assert not calls.exists(), calls.read_text(encoding="utf-8")

    proc = _run(repo, env, "--only", "docker")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipped  docker" in proc.stdout and "`docker info` exited 1" in proc.stdout
    assert calls.read_text(encoding="utf-8").split() == ["info"]


@posix_only
def test_a_docker_info_that_does_not_answer_skips_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PF03 (verify r2): ``docker info`` past its timeout skips the gate; it is not a failure."""

    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(pf.shutil, "which", lambda name, *a, **k: str(fake) if name == "docker" else None)
    monkeypatch.setattr(pf, "DOCKER_INFO_TIMEOUT", 0.5)
    ctx = pf.Context(tmp_path, sys.executable, _changes("tests/test_x.py"), ["tests/test_x.py"], _args("--docker"))
    (step,) = pf._docker(ctx)
    started = time.monotonic()
    with pytest.raises(pf.GateSkipped, match="did not answer"):
        step.run(ctx, 60.0)
    assert time.monotonic() - started < 20


def test_claims_naming_an_existing_directory_fails_before_any_gate(
    scratch: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    """PF09 (verify r2): ``--claims <a directory>`` is a usage error, not a traceback after every gate ran."""

    repo, env = scratch
    proc = _run(repo, env, "--only", "untracked-tests", "--claims", str(tmp_path))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--claims" in proc.stderr and "Traceback" not in proc.stderr
    assert "[1/" not in proc.stdout


@posix_only
def test_a_gate_skipped_while_it_runs_does_not_lend_its_number_to_the_next(
    scratch: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    """N3 (verify r2): after ``docker`` was skipped at run time, the next gate was numbered ``[1/2]`` again."""

    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 2 > 1\n")
    _recording_command(tmp_path / "bin", "docker", tmp_path / "docker-calls.txt", info_rc=1)
    _recording_command(tmp_path / "bin", "uv", tmp_path / "uv-calls.txt")
    env = {**env, "PATH": _path_with(tmp_path / "bin")}
    proc = _run(repo, env, "--only", "docker,pythons", "--pythons", "3.11")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    started = [line.split(" (", 1)[0] for line in proc.stdout.splitlines() if line.startswith("[")]
    assert started == ["[1/2] docker", "[2/2] pythons"], proc.stdout
    assert "skipped: docker is not running" in proc.stdout


def test_only_pythons_without_versions_is_a_usage_error(scratch: tuple[Path, dict[str, str]]) -> None:
    """Codex 5: it used to report ``passed   pythons   0.0 s`` having run no interpreter."""

    repo, env = scratch
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 2 > 1\n")
    for argv in (("--only", "pythons"), ("--plan", "--only", "pythons")):
        proc = _run(repo, env, *argv)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "--pythons 3.11,3.14" in proc.stderr
        assert "passed" not in proc.stdout


def test_a_gate_with_no_step_is_skipped_and_an_empty_result_never_passes(tmp_path: Path) -> None:
    ctx = pf.Context(tmp_path, sys.executable, _changes("tests/test_x.py"), ["tests/test_x.py"], _args())
    empty = pf.Gate("empty", None, "always", lambda c: [], 5.0)
    original = pf.GATES
    try:
        pf.GATES = (empty,)
        assert pf.plan(ctx) == [(empty, "skip", "nothing to run", [])]
    finally:
        pf.GATES = original
    assert pf._status([]) == "failed"
    ok = pf.Step(shown="ok")
    assert pf._status([(ok, 0, 0.1, "")]) == "passed"
    assert pf._status([(ok, 0, 0.1, ""), (ok, 1, 0.1, "")]) == "failed"
    # The pythons builder itself has nothing to run without versions.
    ctx = pf.Context(tmp_path, sys.executable, _changes("tests/test_x.py"), ["tests/test_x.py"], _args())
    assert isinstance(pf._pythons(ctx), str)


def test_every_gate_but_pythons_runs_through_this_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex 2: only ``--pythons`` may start ``uv``; every other gate's commands start this interpreter.

    Every gate is built, and every step run with process creation recorded and
    stubbed, so a gate whose command is built when it runs is checked too.
    """

    monkeypatch.setattr(pf.shutil, "which", lambda name, *a, **k: f"/fake/bin/{name}")
    started: list[tuple[str, list[str]]] = []
    current = {"gate": ""}

    def fake_spawn(argv: list[str], ctx: Any, timeout: float, env: Any = None, merge: bool = True) -> Any:
        started.append((current["gate"], list(argv)))
        return 0, json.dumps({"generalDiagnostics": []}), ""

    def fake_run(argv: list[str], *a: Any, **k: Any) -> Any:
        started.append((current["gate"], list(argv)))
        return subprocess.CompletedProcess(argv, 1, b"", b"")

    monkeypatch.setattr(pf, "_spawn", fake_spawn)
    monkeypatch.setattr(pf.subprocess, "run", fake_run)
    changes = _changes(
        "packages/aelix-agent-core/src/aelix_agent_core/harness/skills.py",
        "docs/guides/getting-started.md",
        "pyproject.toml",
        "scripts/preflight.py",
        "tests/test_agent_skills.py",
    )
    argv = ("--docker", "--pythons", "3.11", "--windows-clock", "--fix-citations")
    ctx = pf.Context(REPO, sys.executable, changes, ["tests/test_agent_skills.py"], _args(*argv))
    rows = pf.plan(ctx)
    assert [g.name for g, verdict, _w, _s in rows if verdict == "run"] == list(pf.GATE_NAMES), rows
    for gate, _verdict, _why, steps in rows:
        current["gate"] = gate.name
        for step in steps:
            if step.run is not None:
                with contextlib.suppress(pf.GateSkipped):
                    step.run(ctx, 5.0)
            else:
                assert step.argv is not None
                started.append((gate.name, step.argv))
    assert {name for name, _argv in started} >= set(pf.GATE_NAMES) - {"untracked-tests"}
    for name, argv in started:
        if name == "pythons":
            assert argv[:5] == ["/fake/bin/uv", "run", "--isolated", "--frozen", "--all-packages"], argv
        elif name == "docker":
            assert argv == ["/fake/bin/docker", "info"], argv
        else:
            assert argv[0] == sys.executable, (name, argv)
        assert name == "pythons" or not any(Path(a).name == "uv" for a in argv), (name, argv)


def test_the_pythons_gate_drops_virtual_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "another-checkout" / ".venv"))
    monkeypatch.setattr(pf.shutil, "which", lambda name, *a, **k: f"/fake/bin/{name}")
    ctx = pf.Context(tmp_path, sys.executable, _changes("tests/test_x.py"), ["tests/test_x.py"], _args("--pythons", "3.11,3.14"))
    steps = pf._pythons(ctx)
    assert isinstance(steps, list) and [s.argv[s.argv.index("-p") + 1] for s in steps if s.argv] == ["3.11", "3.14"]
    for step in steps:
        assert step.env is not None and "VIRTUAL_ENV" not in step.env


def _uv_project(repo: Path, env: dict[str, str]) -> bytes:
    """Make *repo* a uv project with no dependencies and a lock, then make that lock stale.

    The stale ``pyproject.toml`` adds a dependency no index has: a ``uv run``
    that re-resolves fails on it (offline), and one that installs ``uv.lock`` as
    it is (``--frozen``) never looks.
    """

    _write(repo / "pyproject.toml", '[project]\nname = "probe"\nversion = "0.0.0"\nrequires-python = ">=3.9"\n')
    subprocess.run(["uv", "lock", "--offline", "-q"], cwd=repo, env=env, capture_output=True, check=True, timeout=120)
    _write(
        repo / "pyproject.toml",
        '[project]\nname = "probe"\nversion = "0.0.1"\nrequires-python = ">=3.9"\n'
        'dependencies = ["aelix-no-such-package-733"]\n',
    )
    return (repo / "uv.lock").read_bytes()


def test_the_pythons_gate_leaves_the_project_venv_and_lock_alone(scratch: tuple[Path, dict[str, str]]) -> None:
    """The one ``uv`` gate, run with the real ``uv``: no ``.venv`` appears and ``uv.lock`` is used as it is.

    A ``uv run`` without ``--isolated`` creates and syncs the project's
    ``.venv``, and one without ``--frozen`` re-resolves a stale ``uv.lock``
    (here it fails on the dependency the lock does not have). The locked
    project has no dependencies, so the throwaway interpreter has no pytest and
    the gate fails; what is measured is that it ran and wrote nothing.
    """

    repo, env = scratch
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")
    env = {**env, "UV_PYTHON_DOWNLOADS": "never", "UV_OFFLINE": "1", "VIRTUAL_ENV": str(repo / ".venv")}
    lock = _uv_project(repo, env)
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 2 > 1\n")
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"

    proc = _run(repo, env, "--only", "pythons", "--pythons", version)
    out = proc.stdout + proc.stderr
    if "No interpreter found" in out or "No Python" in out:
        pytest.skip(f"uv finds no Python {version} here without downloading one")
    assert proc.returncode == 1, out
    assert "No module named pytest" in out, out  # the throwaway interpreter ran
    assert not (repo / ".venv").exists(), sorted(p.name for p in repo.iterdir())
    assert (repo / "uv.lock").read_bytes() == lock


@posix_only
def test_no_gate_but_pythons_starts_uv_end_to_end(scratch: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    repo, env = scratch
    calls = tmp_path / "uv-calls.txt"
    _recording_command(tmp_path / "bin", "uv", calls)
    _recording_command(tmp_path / "bin", "docker", tmp_path / "docker-calls.txt", info_rc=1)
    env = {**env, "PATH": _path_with(tmp_path / "bin")}
    _write(repo / "tests" / "test_old.py", "def test_old():\n    assert 2 > 1\n")
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", "X = 2\n")
    _write(repo / "pyproject.toml", "[project]\nname = 'p'\n")
    _write(repo / "docs" / "guides" / "x.md", "x\n")
    proc = _run(repo, env, "--docker", "--windows-clock", "--skip", "pyright-windows")
    assert "summary:" in proc.stdout, proc.stdout + proc.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8")
    proc = _run(repo, env, "--only", "pythons", "--pythons", "3.11")
    assert calls.read_text(encoding="utf-8").split()[:4] == ["run", "--isolated", "--frozen", "--all-packages"]


def test_a_path_the_console_cannot_encode_is_escaped_not_a_crash(scratch: tuple[Path, dict[str, str]]) -> None:
    """Codex 6: a Korean file name on a cp1252 stdout ended ``--plan`` with ``UnicodeEncodeError``."""

    repo, env = scratch
    _write(repo / "tests" / "test_한글.py", "def test_k():\n    assert True\n")
    env = {**env, "PYTHONIOENCODING": "cp1252"}
    proc = _run(repo, env, "--plan")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UnicodeEncodeError" not in proc.stderr
    assert "tests/test_\\ud55c\\uae00.py" in proc.stdout
    assert proc.stdout.rstrip().endswith(pf.FULL_SUITE)


def test_claims_dash_prints_the_block_and_leaves_no_file(scratch: tuple[Path, dict[str, str]]) -> None:
    """F7: ``--claims claims.md`` from the root left an untracked file that started the packaging gate."""

    repo, env = scratch
    before = _git(repo, env, "status", "--porcelain", "--untracked-files=all")
    proc = _run(repo, env, "--only", "untracked-tests", "--claims", "-")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    block = proc.stdout.split('claims (paste under "How it was verified"):\n', 1)[1].split("end of claims", 1)[0]
    assert block.startswith("## How it was verified") and "| untracked-tests | passed |" in block
    assert proc.stdout.rstrip().splitlines()[-1].startswith("still owed: the full suite")
    assert _git(repo, env, "status", "--porcelain", "--untracked-files=all") == before


def test_claims_into_a_missing_directory_fails_before_any_gate(scratch: tuple[Path, dict[str, str]], tmp_path: Path) -> None:
    """F9: it used to run every gate and then end in a ``FileNotFoundError`` traceback."""

    repo, env = scratch
    proc = _run(repo, env, "--only", "untracked-tests", "--claims", str(tmp_path / "no-such-dir" / "claims.md"))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "no such directory" in proc.stderr and "Traceback" not in proc.stderr
    assert "[1/" not in proc.stdout


def test_help_lists_every_gate_from_the_table(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    proc = _run(repo, env, "--help")
    assert proc.returncode == 0
    table = proc.stdout.split("gates, in the order they run", 1)[1]
    rows = [line.split()[0] for line in table.splitlines()[1:] if line.startswith("  ")]
    assert rows == list(pf.GATE_NAMES)


def test_claims_replace_the_home_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = sandbox_home(monkeypatch, tmp_path / "home")
    assert Path.home() == home
    text = f"cache at {home / 'cache' / 'x.txt'}"
    assert pf._scrub(text, tmp_path / "repo") == f"cache at {Path('~') / 'cache' / 'x.txt'}"


def test_every_git_call_takes_no_optional_lock() -> None:
    """The flag is not observable with the commands preflight runs on git 2.54 (none writes the index), so its presence is pinned."""

    source = SCRIPT.read_text(encoding="utf-8")
    spawned = source.count('["git", ')
    assert spawned >= 2
    assert source.count('["git", "--no-optional-locks", ') == spawned


@pytest.mark.parametrize(
    ("path", "is_test"),
    [
        ("tests/test_x.py", True),
        ("tests/sub/x_test.py", True),
        ("tests/helper.py", False),
        ("tests/test_x.txt", False),
        ("scripts/test_x.py", False),
    ],
)
def test_a_test_file_is_either_pytest_default_name(path: str, is_test: bool) -> None:
    """pytest's default ``python_files`` is ``test_*.py`` and ``*_test.py``; ``pyproject.toml`` sets neither."""

    assert pf.is_test_file(path) is is_test
