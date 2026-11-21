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


ALWAYS = {"untracked-tests", "ruff", "types", "citations", "sdist-size"}


def test_with_nothing_changed_only_the_always_gates_run() -> None:
    assert _would_run(tests=()) == ALWAYS


def test_a_product_module_starts_the_product_gates() -> None:
    path = "packages/aelix-agent-core/src/aelix_agent_core/harness/skills.py"
    assert _would_run(path) == ALWAYS | {
        "import-direction", "complexity", "band", "pi-parity", "test-hygiene", "touched-tests", "pyright-windows"
    }


def test_the_umbrella_package_is_product_too() -> None:
    assert {"band", "pi-parity", "import-direction"} <= _would_run("src/aelix/__init__.py")
    assert not pf.is_product_py("scripts/preflight.py") and not pf.is_product_py("tests/test_x.py")


def test_a_test_change_starts_the_touched_tests_and_the_hygiene_scans() -> None:
    assert _would_run("tests/test_agent_skills.py") == ALWAYS | {"test-hygiene", "touched-tests"}


NESTED_EXAMPLE_MANIFEST = (
    "packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter/pyproject.toml"
)


@pytest.mark.parametrize(
    ("path", "unrelated"),
    [
        ("pyproject.toml", "scripts/pyproject.toml"),
        ("packages/aelix-ai/pyproject.toml", NESTED_EXAMPLE_MANIFEST),
        ("packages/aelix-agent-core/pyproject.toml", NESTED_EXAMPLE_MANIFEST),
        ("packages/aelix-coding-agent/pyproject.toml", NESTED_EXAMPLE_MANIFEST),
        ("packages/aelix-server/pyproject.toml", NESTED_EXAMPLE_MANIFEST),
    ],
)
def test_a_workspace_manifest_alone_starts_import_direction(path: str, unrelated: str) -> None:
    assert _would_run(unrelated, tests=()).isdisjoint({"import-direction", "control-v1"})
    assert "import-direction" in _would_run(path, tests=())


@pytest.mark.parametrize(
    ("path", "unrelated"),
    [
        ("scripts/check_control_v1.py", "scripts/preflight.py"),
        ("scripts/check_complexity.py", "docs/guides/getting-started.md"),
    ],
)
def test_a_control_v1_checker_change_alone_starts_control_v1(path: str, unrelated: str) -> None:
    assert _would_run(unrelated, tests=()).isdisjoint({"import-direction", "control-v1"})
    assert "control-v1" in _would_run(path, tests=())


RPC = "packages/aelix-coding-agent/src/aelix_coding_agent/rpc/"


@pytest.mark.parametrize(
    ("path", "runs"),
    [
        (RPC + "control.py", True),
        (RPC + "control_mcp.py", True),
        (RPC + "control_ui.py", True),
        (RPC + "rpc_mode.py", True),
        ("tests/rpc/control_v1/fixtures/permissions.set.json", True),
        ("tests/rpc/control_v1/floor_v1.json", True),
        ("docs/contracts/control-v1.md", True),
        (RPC + "rpc_client.py", False),
        (RPC + "rpc_types.py", False),
        ("tests/rpc/test_runtime_control.py", False),
        ("docs/contracts/README.md", False),
    ],
)
def test_the_control_v1_gate_runs_on_what_can_change_version_1(path: str, runs: bool) -> None:
    """#623 (ADR-0302 D8): R3 and the fixtures run when the control plane, its fixtures or the list move."""

    assert ("control-v1" in _would_run(path, tests=())) is runs


def test_the_control_v1_gate_checks_r3_against_the_changes_base_then_runs_the_fixtures() -> None:
    changes = _changes(RPC + "control.py")
    ctx = pf.Context(REPO, sys.executable, changes, [], _args())
    steps = pf._control_v1_steps(ctx)
    assert not isinstance(steps, str)
    assert steps[0].shown == f"python scripts/check_control_v1.py --check --base {changes.base_sha}"
    assert steps[1].argv == [sys.executable, *pf.PYTEST, *pf.TOUCHED_FLAGS, "tests/rpc/control_v1"]


@pytest.mark.parametrize("path", ["scripts/check_control_v1.py", "scripts/check_complexity.py"])
def test_control_v1_skips_an_already_absent_checker_and_fails_on_deletion(
    scratch: tuple[Path, dict[str, str]], path: str,
) -> None:
    repo, env = scratch
    _write(repo / "docs/contracts/control-v1.md", "contract\n")
    proc = _run(repo, env, "--base", "HEAD", "--only", "control-v1")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipped" in proc.stdout
    assert "scripts/check_control_v1.py is not in this tree" in proc.stdout

    _write(repo / "scripts/check_control_v1.py", "raise SystemExit(0)\n")
    _write(repo / "scripts/check_complexity.py", "raise SystemExit(0)\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", "add control checkers and contract")
    _git(repo, env, "rm", "-q", path)

    proc = _run(repo, env, "--base", "HEAD", "--only", "control-v1")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed   control-v1" in proc.stdout
    assert "can't open file" in proc.stdout and Path(path).name in proc.stdout


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
        "docs/contracts/control-v1.md",
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


# --- #858: the complexity ratchet CI runs on every leg -----------------------------


@pytest.mark.parametrize(
    ("path", "runs"),
    [
        ("packages/aelix-ai/src/aelix_ai/x.py", True),
        ("packages/aelix-ai/src/aelix_ai/x.pyi", True),
        ("packages/aelix-ai/tests/helper.py", True),
        ("src/aelix/__init__.py", True),
        ("packages/aelix-ai/pyproject.toml", True),
        ("packages/aelix-ai/ruff.toml", True),
        ("src/.ruff.toml", True),
        ("pyproject.toml", True),
        ("ruff.toml", True),
        (".ruff.toml", True),
        ("complexity-baseline.json", True),
        ("uv.lock", True),
        ("scripts/check_complexity.py", True),
        ("packages/aelix-ai/README.md", False),
        ("src/aelix/py.typed", False),
        ("tests/test_x.py", False),
        ("tests/fixtures/pyproject.toml", False),
        ("scripts/preflight.py", False),
        ("docs/guides/getting-started.md", False),
        ("docs/decisions/0300-x.md", False),
        ("CHANGELOG.md", False),
        (".github/workflows/ci.yml", False),
    ],
)
def test_the_complexity_gate_runs_on_what_the_ratchet_reads(path: str, runs: bool) -> None:
    """#858: the ``.py``/``.pyi`` it measures, the ruff configs it scans, its baseline, the lock and itself.

    A change only outside ``packages/`` and ``src/`` that is none of the ratchet's own
    inputs does not start it.
    """

    assert ("complexity" in _would_run(path, tests=())) is runs


def _complexity_scratch(repo: Path, env: dict[str, str], listed: int) -> str:
    """The real ratchet in *repo*, with ``f`` listed at *listed*; the base is committed as ``origin/main``."""

    shutil.copyfile(REPO / "scripts" / "check_complexity.py", repo / "scripts" / "check_complexity.py")
    version = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"], capture_output=True, encoding="utf-8", check=True
    ).stdout.split()[1]
    key = "packages/pkg/src/pkg/mod.py::f"
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", _scored("f", listed))
    _write(repo / "complexity-baseline.json", json.dumps({"ruff": version, "functions": {key: listed}}) + "\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", "a listed function")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", "HEAD")
    return key


def _scored(name: str, score: int) -> str:
    """A function Ruff's C901 scores *score*: one ``if`` per point above 1."""

    lines = [f"def {name}(x):"]
    for i in range(score - 1):
        lines += [f"    if x == {i}:", f"        return {i}"]
    return "\n".join([*lines, "    return -1", ""])


def _without_github(env: dict[str, str]) -> dict[str, str]:
    """No ``GITHUB_*``: on a CI runner the ratchet would write to that step's job summary."""

    return {k: v for k, v in env.items() if not k.startswith("GITHUB_")}


def _shown(proc: subprocess.CompletedProcess[str]) -> list[str]:
    """The lines a failed gate's tail printed."""

    return [line[len("    | ") :] for line in proc.stdout.splitlines() if line.startswith("    | ")]


def test_a_lowered_baselined_score_fails_the_complexity_gate_and_shows_stale(
    scratch: tuple[Path, dict[str, str]],
) -> None:
    """#858, PR #840: CI's ratchet failed 8 jobs on two scores that fell, and preflight had no row for it."""

    repo, env = scratch
    env = _without_github(env)
    key = _complexity_scratch(repo, env, 23)
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", _scored("f", 23) + "\nY = 2\n")
    proc = _run(repo, env, "--only", "complexity")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed   complexity" in proc.stdout

    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", _scored("f", 22))
    proc = _run(repo, env, "--only", "complexity")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed   complexity" in proc.stdout
    shown = _shown(proc)
    assert f"STALE   {key}  fell 23 -> 22" in shown, proc.stdout
    assert shown[-1] == f"STALE   {key}  fell 23 -> 22", proc.stdout
    assert any(line.startswith("complexity FAILED") for line in shown), proc.stdout
    assert f"    hint: {pf.COMPLEXITY_HINT}" in proc.stdout


def test_a_raised_score_fails_the_complexity_gate_with_raised(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    env = _without_github(env)
    key = _complexity_scratch(repo, env, 23)
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", _scored("f", 24))
    proc = _run(repo, env, "--only", "complexity")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed   complexity" in proc.stdout
    assert f"RAISED  {key}  23 -> 24" in _shown(proc), proc.stdout


def test_the_claims_block_shows_the_complexity_gate(scratch: tuple[Path, dict[str, str]]) -> None:
    repo, env = scratch
    env = _without_github(env)
    _complexity_scratch(repo, env, 23)
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", _scored("f", 23) + "\nY = 2\n")
    base = _git(repo, env, "rev-parse", "HEAD").strip()
    proc = _run(repo, env, "--only", "complexity", "--claims", "-")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    block = proc.stdout.split('claims (paste under "How it was verified"):\n', 1)[1].split("end of claims", 1)[0]
    assert "| complexity | passed |" in block
    assert f"$ python scripts/check_complexity.py --check --base {base}" in block
    assert f"HISTORY baseline compared with merge base {base[:8]}: nothing added or raised" in block
    assert "complexity OK - " in block


def test_the_complexity_gate_is_skipped_where_the_ratchet_is_not(tmp_path: Path) -> None:
    ctx = pf.Context(tmp_path, sys.executable, _changes("src/x.py"), [], _args())
    assert pf._complexity(ctx) == "scripts/check_complexity.py is not in this tree"


def _ratchet_todo() -> str:
    """``check_complexity.py``'s own repair text, so the test follows the script if it changes."""

    import ast

    tree = ast.parse((REPO / "scripts" / "check_complexity.py").read_text(encoding="utf-8"))
    (value,) = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["_TODO"]
    ]
    return str(ast.literal_eval(value))


def test_the_ratchets_findings_come_last_and_its_repair_text_is_left_out() -> None:
    """Read as one stream, the last 15 lines were the 14-line repair text and a note, and no finding."""

    todo = _ratchet_todo()
    assert todo.startswith(pf.COMPLEXITY_TODO + "\n") and len(todo.splitlines()) == 14
    out = "HISTORY baseline compared with merge base 0123abcd: nothing added or raised\n"
    err = f"complexity FAILED - summary\nSTALE   p.py::f  fell 23 -> 22\n{todo}\n"
    assert pf.complexity_findings_last(out, err) == (
        "HISTORY baseline compared with merge base 0123abcd: nothing added or raised\n"
        "complexity FAILED - summary\nSTALE   p.py::f  fell 23 -> 22"
    )
    assert pf.complexity_findings_last("", "complexity: cannot measure: no git\n") == "complexity: cannot measure: no git"
    assert pf.complexity_findings_last("complexity OK - x\n", "") == "complexity OK - x"


def test_only_a_line_that_opens_the_repair_text_and_its_indented_lines_are_left_out() -> None:
    """Codex C6: ``src/What to do:.py::over`` lost its NEW line, and a traceback after an inline marker was cut."""

    err = (
        "complexity FAILED - summary\n"
        "NEW     src/What to do:.py::over  scores 22 (limit 20)\n"
        "note: What to do: is in this line too\n"
        f"{_ratchet_todo()}\n"
        "Traceback (most recent call last):\n"
        '  File "scripts/check_complexity.py", line 9, in <module>\n'
        "AttributeError: boom\n"
        "What to do: again\n"
        "  an indented line under it\n"
        "\tand a tabbed one\n"
        "the last line\n"
    )
    assert pf.complexity_findings_last("", err).splitlines() == [
        "complexity FAILED - summary",
        "NEW     src/What to do:.py::over  scores 22 (limit 20)",
        "note: What to do: is in this line too",
        "Traceback (most recent call last):",
        '  File "scripts/check_complexity.py", line 9, in <module>',
        "AttributeError: boom",
        "the last line",
    ]


def test_the_complexity_step_spawns_the_check_with_this_changes_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex C7: the claims block showed ``--base <sha>`` while a mutant spawned the script without it."""

    spawned: list[tuple[list[str], bool]] = []

    def fake_spawn(argv: list[str], ctx: Any, timeout: float, env: Any = None, merge: bool = True) -> Any:
        spawned.append((list(argv), merge))
        return 0, "complexity OK - x\n", ""

    monkeypatch.setattr(pf, "_spawn", fake_spawn)
    changes = pf.ChangeSet("origin/main", "ab" * 20, "1" * 40, {"src/x.py": frozenset({"committed"})}, frozenset(), REPO)
    ctx = pf.Context(REPO, sys.executable, changes, [], _args())
    (step,) = pf._complexity(ctx)
    assert step.shown == f"python scripts/check_complexity.py --check --base {'ab' * 20}"
    assert step.run(ctx, 5.0) == (0, "complexity OK - x")
    assert spawned == [([sys.executable, "scripts/check_complexity.py", "--check", "--base", "ab" * 20], False)]


def test_a_complexity_timeout_ends_with_what_it_printed_last_and_the_kill_note(tmp_path: Path) -> None:
    """Codex C6: 16 stderr lines pushed stdout's last line and the kill note out of the 15 a gate shows."""

    _write(
        tmp_path / "scripts" / "check_complexity.py",
        "import sys, time\n"
        "for i in range(16):\n"
        "    print(f'stderr line {i}', file=sys.stderr, flush=True)\n"
        "print('STDOUT latest: phase=history', flush=True)\n"
        "time.sleep(60)\n",
    )
    ctx = pf.Context(tmp_path, sys.executable, _changes("src/x.py"), [], _args())
    (step,) = pf._complexity(ctx)
    rc, out = step.run(ctx, 3.0)
    assert rc is None
    assert pf._tail(out)[-2:] == [
        "STDOUT latest: phase=history",
        "[preflight] timed out after 3 s; its process group was killed",
    ], out
    assert "stderr line 15" in out


def test_a_change_that_deletes_the_ratchet_runs_the_gate_and_fails_it(scratch: tuple[Path, dict[str, str]]) -> None:
    """Codex C4: a staged deletion of the script left the gate skipped, and CI's step fails on it."""

    repo, env = scratch
    env = _without_github(env)
    _complexity_scratch(repo, env, 23)
    _git(repo, env, "rm", "-q", "scripts/check_complexity.py")
    plan = _run(repo, env, "--plan", "--only", "complexity")
    run_part = plan.stdout.split("would run:\n", 1)[1].split("would skip:", 1)[0]
    assert run_part.startswith("  complexity "), plan.stdout
    proc = _run(repo, env, "--only", "complexity")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed   complexity" in proc.stdout
    assert any("check_complexity.py" in line for line in _shown(proc)), proc.stdout
    ctx = pf.Context(repo / "elsewhere", sys.executable, _changes("scripts/check_complexity.py"), [], _args())
    assert isinstance(pf._complexity(ctx), list)


def test_a_passing_complexity_gate_shows_the_untracked_note(scratch: tuple[Path, dict[str, str]]) -> None:
    """Verify F4: an untracked module is not measured, passes here, and fails CI once it is committed."""

    repo, env = scratch
    env = _without_github(env)
    _complexity_scratch(repo, env, 23)
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "new.py", _scored("g", 25))
    proc = _run(repo, env, "--only", "complexity")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _after_result(proc, "complexity") == [
        "    | UNTRACKED 1 .py/.pyi file(s) under packages and src are not measured until `git add`ed, "
        "and CI will measure them: packages/pkg/src/pkg/new.py"
    ], proc.stdout


def test_a_failing_gate_prints_no_shown_lines_and_a_pass_with_no_match_prints_none(
    scratch: tuple[Path, dict[str, str]],
) -> None:
    """Verify F5 (M23, M24): the shown lines belong under a pass that matched, and nowhere else."""

    repo, env = scratch
    env = _without_github(env)
    _complexity_scratch(repo, env, 23)
    proc = _run(repo, env, "--only", "untracked-tests,complexity", "--base", "HEAD~1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _after_result(proc, "untracked-tests") == [], proc.stdout
    assert _after_result(proc, "complexity") == [], proc.stdout

    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", _scored("f", 22))
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "new.py", "X = 1\n")
    proc = _run(repo, env, "--only", "complexity")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    after = _after_result(proc, "complexity")
    assert after[0].startswith("    $ python scripts/check_complexity.py --check --base "), proc.stdout
    assert sum(line.startswith("    | UNTRACKED ") for line in after) == 1, proc.stdout


def test_a_baseline_raised_with_its_score_fails_with_history_and_the_hint_says_what_to_do(
    scratch: tuple[Path, dict[str, str]],
) -> None:
    """Codex C6: a HISTORY-only failure lost the script's instruction to restore the baseline lines."""

    repo, env = scratch
    env = _without_github(env)
    key = _complexity_scratch(repo, env, 23)
    _write(repo / "packages" / "pkg" / "src" / "pkg" / "mod.py", _scored("f", 24))
    baseline = json.loads((repo / "complexity-baseline.json").read_text(encoding="utf-8"))
    baseline["functions"][key] = 24
    _write(repo / "complexity-baseline.json", json.dumps(baseline) + "\n")
    _git(repo, env, "commit", "-q", "-am", "raise both")
    base = _git(repo, env, "rev-parse", "origin/main").strip()
    proc = _run(repo, env, "--only", "complexity")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _shown(proc)[-1] == f"HISTORY {key}  raised in the baseline: 23 at {base[:8]} -> 24", proc.stdout
    assert "HISTORY (the baseline gained an entry or a score against the merge base): restore those baseline lines" in (
        pf.COMPLEXITY_HINT
    )
    assert f"    hint: {pf.COMPLEXITY_HINT}" in proc.stdout


# --- #831: the release sdist's size cap, measured on every change ----------------------


@pytest.mark.parametrize(
    "paths",
    [
        (),
        ("docs/development/ci-hang-diagnostics.md",),
        ("docs/decisions/0303-x.md",),
        ("README.md",),
        ("tests/test_x.py",),
        ("packages/aelix-ai/src/aelix_ai/x.py",),
    ],
)
def test_the_sdist_size_gate_runs_on_every_change(paths: tuple[str, ...]) -> None:
    """#831: every shipped file adds to the sdist, and #623/#624 reached the cap with docs and code only."""

    assert "sdist-size" in _would_run(*paths, tests=())


def test_the_sdist_size_gate_runs_only_the_size_test_and_names_a_report_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ast

    path, _, name = pf.SDIST_SIZE_TEST.partition("::")
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    assert name in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    monkeypatch.setattr(pf.shutil, "which", lambda name, *a, **k: f"/fake/bin/{name}")
    spawned: list[tuple[list[str], dict[str, str]]] = []

    def fake_spawn(argv: list[str], ctx: Any, timeout: float, env: Any = None, merge: bool = True) -> Any:
        spawned.append((list(argv), dict(env)))
        Path(env[pf.SDIST_SIZE_REPORT_ENV]).write_text('{"size": 5, "cap": 10}', encoding="utf-8")
        return 0, "1 passed", ""

    monkeypatch.setattr(pf, "_spawn", fake_spawn)
    (step,) = pf._sdist_size(pf.Context(REPO, sys.executable, _changes(), [], _args()))
    assert step.shown == f"{pf.SDIST_SIZE_REPORT_ENV}=<a temp file> python {' '.join(pf.PYTEST)} {pf.SDIST_SIZE_TEST}"
    assert step.run(pf.Context(REPO, sys.executable, _changes(), [], _args()), 5.0) == (
        0,
        "1 passed\n[preflight] the published sdist is 5 B, 5 B (50%) under the 10 B cap",
    )
    ((argv, env),) = spawned
    assert argv == [sys.executable, *pf.PYTEST, pf.SDIST_SIZE_TEST]
    report = Path(env[pf.SDIST_SIZE_REPORT_ENV])
    assert report.name == "size.json" and not report.parent.exists(), "the report's temp directory is removed"
    assert isinstance(pf._sdist_size(pf.Context(tmp_path, sys.executable, _changes(), [], _args())), str)


def test_the_size_test_writes_the_report_preflight_reads(tmp_path: Path) -> None:
    """The size test and the gate name the report's variable the same way, and the pass line is what the gate shows."""

    import ast
    import re

    tree = ast.parse((REPO / pf.SDIST_SIZE_TEST.split("::", 1)[0]).read_text(encoding="utf-8"))
    assigned = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)
    }
    assert assigned["SIZE_REPORT_ENV"] == pf.SDIST_SIZE_REPORT_ENV
    _rc, out = pf.sdist_size_verdict(0, "1 passed", _report_of(tmp_path, 3_959_664, 6_000_000))
    match = re.search(pf.SDIST_SIZE_SHOWS, out.splitlines()[-1])
    assert match and match.group(1) == "the published sdist is 3,959,664 B, 2,040,336 B (34%) under the 6,000,000 B cap"


def test_the_size_gate_shows_only_its_own_line() -> None:
    """Codex C3: a plugin's warning and pytest's source excerpt held the same words, and both were shown."""

    step = pf.Step(shown="x")
    out = (
        "  (a file, a line): UserWarning: diagnostic sample: the published sdist is 1 B (example only)\n"
        '>       f"[preflight] the published sdist is {size:,} B"\n'
        "[preflight] the published sdist is 5 B, 5 B (50%) under the 10 B cap\n"
    )
    assert pf.shown_lines(pf.SDIST_SIZE_SHOWS, [(step, 0, 0.1, out)]) == [
        "the published sdist is 5 B, 5 B (50%) under the 10 B cap"
    ]


def _report_of(directory: Path, size: object, cap: object = 6_000_000) -> Path:
    report = directory / "size.json"
    report.write_text(json.dumps({"size": size, "cap": cap}), encoding="utf-8")
    return report


@pytest.mark.parametrize(
    ("rc", "size", "expected"),
    [
        (0, 3_959_664, (0, "[preflight] the published sdist is 3,959,664 B, 2,040,336 B (34%) under the 6,000,000 B cap")),
        (0, 6_000_000, (0, "[preflight] the published sdist is 6,000,000 B, 0 B (0%) under the 6,000,000 B cap")),
        (0, 1_500_000, (0, "[preflight] the published sdist is 1,500,000 B, 4,500,000 B (75%) under the 6,000,000 B cap")),
        (3, 3_959_664, (3, "[preflight] the size test measured 3,959,664 B, under the 6,000,000 B cap, and pytest exited 3")),
        (0, 6_000_001, (1, "[preflight] the published sdist is 6,000,001 B, 1 B over the 6,000,000 B cap")),
        (1, 6_059_123, (1, "[preflight] the published sdist is 6,059,123 B, 59,123 B over the 6,000,000 B cap")),
    ],
)
def test_the_size_verdict_compares_the_report_with_its_cap(
    tmp_path: Path, rc: int, size: int, expected: tuple[int, str]
) -> None:
    """Codex C2: an oversize sdist passed once an assertion was optimised away; the gate compares too."""

    got_rc, out = pf.sdist_size_verdict(rc, "pytest output", _report_of(tmp_path, size))
    lines = out.splitlines()
    assert lines[0] == "pytest output"
    over = expected[0] == 1
    assert (got_rc, lines[1]) == expected
    assert lines[2:] == ([f"[preflight] {pf.SDIST_OVER_CAP_HINT}"] if over else [])


@pytest.mark.parametrize(
    ("rc", "out", "why"),
    [
        (
            0,
            "1 skipped in 0.01s",
            "pytest exited 0 and the test wrote no report (a skip, a deselection, --setup-only or --collect-only)",
        ),
        (5, "1 deselected in 0.01s", "pytest exited 5 and the test wrote no report; its output above says why"),
        (4, "ERROR: file or directory not found: x", "pytest exited 4 and the test wrote no report; its output above says why"),
        (
            1,
            "    def f():\nE   first: an earlier frame\n\n(a file, a line): in f\n"
            "E   subprocess.CalledProcessError: Command 'tar' returned 1.\nE   more of it\n"
            "ERROR tests/x.py::test_y\n1 error in 3.2s",
            "pytest exited 1 with an error before it measured one: subprocess.CalledProcessError: Command 'tar' returned 1.",
        ),
        (
            1,
            "E       AssertionError: `uv build --sdist` failed with 2\nE       --- stdout ---\n1 error",
            "pytest exited 1 with an error before it measured one: AssertionError: `uv build --sdist` failed with 2",
        ),
    ],
)
def test_without_a_report_the_size_gate_fails_and_says_nothing_was_measured(
    tmp_path: Path, rc: int, out: str, why: str
) -> None:
    """Codex C1 and verify F1: exit 0 is not proof the size test ran, and an error is not an oversize sdist."""

    got_rc, text = pf.sdist_size_verdict(rc, out, tmp_path / "size.json")
    assert got_rc == 1
    assert text.splitlines()[-1].startswith(f"[preflight] the size test did not measure an sdist: {why}"), text
    assert text.startswith(out)
    assert "over the" not in text and pf.SDIST_OVER_CAP_HINT not in text


@pytest.mark.parametrize(
    ("content", "says"),
    [
        ("{", "the size test's report did not read: "),
        ("[1, 2]", "the size test's report is not a size and a cap: [1, 2]"),
        ('{"size": true, "cap": 6000000}', "the size test's report is not a size and a cap: "),
        ('{"size": 5, "cap": 0}', "the size test's report is not a size and a cap: "),
        ('{"size": 5.5, "cap": 10}', "the size test's report is not a size and a cap: "),
        ('{"size": 5}', "the size test's report is not a size and a cap: "),
    ],
)
def test_a_report_that_is_not_a_size_and_a_cap_fails_the_gate(tmp_path: Path, content: str, says: str) -> None:
    report = tmp_path / "size.json"
    report.write_text(content, encoding="utf-8")
    rc, text = pf.sdist_size_verdict(0, "1 passed", report)
    assert rc == 1 and text.splitlines()[-1].startswith(f"[preflight] {says}"), text


def test_a_size_run_past_its_timeout_is_reported_as_it_was(tmp_path: Path) -> None:
    assert pf.sdist_size_verdict(None, "partial\n[preflight] timed out", _report_of(tmp_path, 5)) == (
        None,
        "partial\n[preflight] timed out",
    )


def test_the_packaging_gate_leaves_the_size_test_to_sdist_size() -> None:
    """Verify F6, Codex C3: the size test ran in both gates, and its old warning filled the packaging tail."""

    ctx = pf.Context(REPO, sys.executable, _changes(".gitignore"), [], _args())
    steps = {gate.name: steps for gate, verdict, _why, steps in pf.plan(ctx) if verdict == "run"}
    (packaging,) = steps["packaging"]
    assert packaging.argv == [sys.executable, *pf.PYTEST, "--deselect", pf.SDIST_SIZE_TEST, "tests/packaging_gate"]
    assert "sdist-size" in steps


def test_without_uv_the_sdist_size_gate_is_skipped_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The size test skips itself without ``uv``, and would write no report."""

    monkeypatch.setattr(pf.shutil, "which", lambda name, *a, **k: None)
    reason = pf._sdist_size(pf.Context(REPO, sys.executable, _changes(), [], _args()))
    assert reason == "uv is not on PATH (the size test builds the sdist with `uv build`)"


STUB_SIZE_TEST = """\
import json
import os
from pathlib import Path

import pytest


def test_the_release_sdist_stays_under_the_size_cap():
    size = {size}
    report = os.environ.get("AELIX_SDIST_SIZE_REPORT")
    if report:
        Path(report).write_text(json.dumps({{"size": size, "cap": 6_000_000}}), encoding="utf-8")
    {check}
"""


def _size_test_stub(repo: Path, env: dict[str, str], size: int, check: str = "") -> None:
    """A stand-in for the size test at its real id that reports *size*, so the gate runs it without building an sdist.

    *check* is its comparison: the real test's ``pytest.fail``, or an ``assert`` that
    ``python -O`` removes.
    """

    check = check or "if size > 6_000_000:\n        pytest.fail(f'the published sdist is {size:,} B, over the cap')"
    _write(repo / "tests" / "packaging_gate" / "test_build_hygiene.py", STUB_SIZE_TEST.format(size=size, check=check))
    _git(repo, env, "add", "-A")


def _uv_on_path() -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH, so the gate is skipped")


def _after_result(proc: subprocess.CompletedProcess[str], gate: str) -> list[str]:
    """The lines printed between *gate*'s result line and the next gate or the summary."""

    lines = proc.stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("[") and f"] {gate} (" in line)
    result = next(i for i in range(start, len(lines)) if lines[i].startswith(("    passed in ", "    failed in ")))
    end = next(i for i in range(result + 1, len(lines)) if lines[i].startswith(("[", "summary:")))
    return lines[result + 1 : end]


def test_plan_lists_the_sdist_size_gate_under_would_run_on_a_docs_only_change(
    scratch: tuple[Path, dict[str, str]],
) -> None:
    _uv_on_path()
    repo, env = scratch
    _write(repo / "docs" / "notes.md", "a page\n")
    _size_test_stub(repo, env, 10)
    _git(repo, env, "commit", "-q", "-m", "docs and the size test")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo / "docs" / "notes.md", "a page, longer\n")
    _write(repo / "README.md", "readme, longer\n")
    proc = _run(repo, env, "--plan")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    run_part = proc.stdout.split("would run:\n", 1)[1].split("would skip:", 1)[0]
    assert "  sdist-size        always\n" in run_part, proc.stdout
    assert f"$ {pf.SDIST_SIZE_REPORT_ENV}=<a temp file> python -m pytest -p no:cacheprovider -q {pf.SDIST_SIZE_TEST}\n" in run_part
    assert not [line for line in run_part.splitlines() if line.split()[0] == "packaging"], run_part


@pytest.mark.parametrize(
    "warnings_env",
    [{}, {"PYTHONWARNINGS": "ignore"}, {"PYTEST_ADDOPTS": "-W error::UserWarning"}, {"PYTEST_ADDOPTS": "--disable-warnings"}],
)
def test_a_passing_sdist_size_gate_prints_the_size_and_the_headroom(
    scratch: tuple[Path, dict[str, str]], warnings_env: dict[str, str]
) -> None:
    """Verify F8, Codex C3: the line came from a warning, which these settings hid or turned into a failure."""

    _uv_on_path()
    repo, env = scratch
    _size_test_stub(repo, env, 3_959_664)
    proc = _run(repo, {**env, **warnings_env}, "--only", "sdist-size")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed   sdist-size" in proc.stdout
    assert _after_result(proc, "sdist-size") == [
        "    | the published sdist is 3,959,664 B, 2,040,336 B (34%) under the 6,000,000 B cap"
    ], proc.stdout


@pytest.mark.parametrize(
    ("check", "env_extra"),
    [
        ("", {}),
        # The bare assert the test had: `python -O` with `--assert=plain` removes it (Codex C2).
        ("assert size <= 6_000_000, 'over'", {"PYTHONOPTIMIZE": "1", "PYTEST_ADDOPTS": "--assert=plain"}),
    ],
)
def test_an_sdist_over_the_cap_fails_the_gate_with_its_size_and_the_cap(
    scratch: tuple[Path, dict[str, str]], check: str, env_extra: dict[str, str]
) -> None:
    _uv_on_path()
    repo, env = scratch
    _size_test_stub(repo, env, 6_059_123, check)
    proc = _run(repo, {**env, **env_extra}, "--only", "sdist-size")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed   sdist-size" in proc.stdout
    shown = _shown(proc)
    assert shown[-2:] == [
        "[preflight] the published sdist is 6,059,123 B, 59,123 B over the 6,000,000 B cap",
        f"[preflight] {pf.SDIST_OVER_CAP_HINT}",
    ], proc.stdout


CONFTEST_SKIP = """\
import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.skip(reason="a plugin skipped it"))
"""

CONFTEST_EXIT_5_IS_0 = """\
def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 5:
        session.exitstatus = 0
"""


@pytest.mark.parametrize(
    ("addopts", "conftest", "why"),
    [
        ("--setup-only", "", "pytest exited 0 and the test wrote no report (a skip, a deselection, --setup-only or --collect-only)"),
        ("--collect-only", "", "pytest exited 0 and the test wrote no report (a skip, a deselection, --setup-only or --collect-only)"),
        ("", CONFTEST_SKIP, "pytest exited 0 and the test wrote no report (a skip, a deselection, --setup-only or --collect-only)"),
        (
            "-k impossible_probe_name",
            CONFTEST_EXIT_5_IS_0,
            "pytest exited 0 and the test wrote no report (a skip, a deselection, --setup-only or --collect-only)",
        ),
        ("-k impossible_probe_name", "", "pytest exited 5 and the test wrote no report; its output above says why"),
    ],
    ids=["setup-only", "collect-only", "skip-marker", "exit-5-normalised", "deselected"],
)
def test_a_size_run_that_measured_nothing_fails_the_gate(
    scratch: tuple[Path, dict[str, str]], addopts: str, conftest: str, why: str
) -> None:
    """Codex C1: each of these exited 0 (or 5, with the over-cap hint) over a 6,961,181 B sdist."""

    _uv_on_path()
    repo, env = scratch
    _size_test_stub(repo, env, 6_961_181)
    if conftest:
        _write(repo / "conftest.py", conftest)
    run_env = {**env, "PYTEST_ADDOPTS": addopts} if addopts else env
    proc = _run(repo, run_env, "--only", "sdist-size")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed   sdist-size" in proc.stdout
    assert _shown(proc)[-1] == f"[preflight] the size test did not measure an sdist: {why}", proc.stdout
    assert "hint:" not in proc.stdout and "over the" not in proc.stdout


def test_an_error_before_the_size_was_measured_is_named_not_called_over_the_cap(
    scratch: tuple[Path, dict[str, str]],
) -> None:
    """Verify F1: a ``tar`` that stopped on a deleted file read as "the release sdist is over MAX_SDIST_BYTES"."""

    _uv_on_path()
    repo, env = scratch
    _write(
        repo / "tests" / "packaging_gate" / "test_build_hygiene.py",
        "import subprocess\n\nimport pytest\n\n\n@pytest.fixture\ndef release_sdist():\n"
        "    raise subprocess.CalledProcessError(1, ['tar', '--null', '-T', '-', '-cf', '-'])\n\n\n"
        "def test_the_release_sdist_stays_under_the_size_cap(release_sdist):\n    pass\n",
    )
    _git(repo, env, "add", "-A")
    proc = _run(repo, env, "--only", "sdist-size")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _shown(proc)[-1] == (
        "[preflight] the size test did not measure an sdist: pytest exited 1 with an error before it measured one: "
        "subprocess.CalledProcessError: Command '['tar', '--null', '-T', '-', '-cf', '-']' returned non-zero exit status 1."
    ), proc.stdout
    assert "hint:" not in proc.stdout and "over the" not in proc.stdout


def test_shown_lines_are_the_matches_each_once_in_order() -> None:
    step = pf.Step(shown="x")
    results = [
        (step, 0, 0.1, "noise\n  (a file, a line): UserWarning: size 2 B\nsize 2 B again\n"),
        (step, 0, 0.1, "size 1 B\n"),
    ]
    assert pf.shown_lines(r"size \d B", results) == ["size 2 B", "size 1 B"]
    assert pf.shown_lines(r"absent", results) == []
    # With a group, the group's text.
    assert pf.shown_lines(r"^size (\d) B", results) == ["2", "1"]
