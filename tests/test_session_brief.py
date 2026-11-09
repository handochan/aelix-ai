"""``scripts/session_brief.py`` and the SessionStart hook that runs it (#733, ADR-0303).

The hook runs at the start of every Claude Code session in this repository,
while other sessions commit and run tests in the same checkout. So what is
pinned here is what keeps it out of their way: it writes nothing (the index is
watched while it runs, against a plain ``git status`` that does write it), it
exits 0 outside a repository, when ``git`` hangs and when it raises, it prints
only what exists, and it parses as Python 3.9, the ``python3`` macOS ships.
"""

from __future__ import annotations

import ast
import hashlib
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

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "session_brief.py"
SETTINGS = REPO / ".claude" / "settings.json"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="drives sh and a fake git script")


def _git_env(tmp_path: Path) -> dict[str, str]:
    """A git environment with no user or system config, so the machine's settings cannot leak in."""

    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout


def _repo(tmp_path: Path, env: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, env, "init", "-q", "-b", "work")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, env, "add", "tracked.txt")
    _git(repo, env, "commit", "-q", "-m", "first")
    return repo


def _run(cwd: Path, env: dict[str, str], python: str = sys.executable) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [python, str(SCRIPT)],
        cwd=cwd,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _owner(repo: Path, issue: int, **fields: Any) -> None:
    path = repo / ".git" / "aelix-issue-locks" / str(issue) / "owner.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"issue": issue, **fields}), encoding="utf-8")


def test_a_checkout_with_locks_handoffs_and_a_worktree_is_described(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    _git(repo, env, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(repo, env, "commit", "-q", "-am", "second")
    head = _git(repo, env, "rev-parse", "--short=8", "HEAD").strip()
    main = _git(repo, env, "rev-parse", "--short=8", "origin/main").strip()
    (repo / "tracked.txt").write_text("three\n", encoding="utf-8")  # dirty, tracked
    (repo / "loose.txt").write_text("x\n", encoding="utf-8")  # dirty, untracked
    _git(repo, env, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")

    _owner(repo, 101, state="active", branch="fix/101-thing", phase="impl (lane 1)")
    _owner(repo, 102, state="done", branch="fix/102-old", phase="merged")
    _owner(repo, 103, state="active", branch=None, phase="x" * 300)
    bad = repo / ".git" / "aelix-issue-locks" / "104" / "owner.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")

    specs = repo / ".omc" / "specs"
    specs.mkdir(parents=True)
    old = specs / "handoff-old-2026-01-01.md"
    new = specs / "handoff-new-2026-01-02.md"
    old.write_text("old\n", encoding="utf-8")
    new.write_text("new\n", encoding="utf-8")
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_800_000_000, 1_800_000_000))

    (repo / "scripts").mkdir()
    (repo / "scripts" / "preflight.py").write_text("", encoding="utf-8")
    skill = repo / ".agents" / "skills" / "aelix-demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: aelix-demo\n---\n", encoding="utf-8")

    proc = _run(repo, env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    out = proc.stdout
    lines = out.splitlines()
    assert len(lines) <= 20, out
    assert f"on work at {head}" in out
    assert f"origin/main {main} (local ref, not fetched): HEAD ahead 1, behind 0" in out
    # tracked.txt modified, and four untracked entries: loose.txt, .agents/, .omc/, scripts/
    assert "dirty paths: 5" in out
    assert "worktrees: 2" in out
    assert "active issue locks (2)" in out
    assert "  #101 fix/101-thing impl (lane 1)" in lines
    assert "#102" not in out and "#104" not in out
    lock_103 = next(line for line in lines if line.startswith("  #103 - "))
    assert len(lock_103) <= 110 and lock_103.endswith("...")
    assert "handoff-new-2026-01-02.md" in out and "handoff-old" not in out
    assert "gates: uv run --frozen python scripts/preflight.py --plan" in out
    assert "skills (.agents/skills): aelix-demo" in out

    # A linked worktree reads the same locks and finds the main checkout's handoff.
    wt = _run(tmp_path / "wt", env)
    assert wt.returncode == 0, wt.stderr
    assert "on side at" in wt.stdout
    assert "  #101 fix/101-thing impl (lane 1)" in wt.stdout.splitlines()
    assert "handoff-new-2026-01-02.md" in wt.stdout


def test_a_fresh_clone_gets_only_what_exists(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    proc = _run(repo, env)
    assert proc.returncode == 0
    assert proc.stderr == ""
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("aelix session brief")
    assert lines[1].startswith("checkout ") and lines[1].endswith(" on work at " + lines[1][-8:])
    assert lines[2:] == ["dirty paths: 0", "worktrees: 1"], proc.stdout
    for absent in ("origin/main", "locks", "handoff", "gates:", "skills", "skipped"):
        assert absent not in proc.stdout


def test_outside_a_repository_it_prints_nothing_and_exits_0(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    proc = _run(plain, env)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


def _index_state(index: Path) -> tuple[int, int, str]:
    st = index.stat()
    return st.st_mtime_ns, st.st_ino, hashlib.sha256(index.read_bytes()).hexdigest()


def _stat_dirty_repo(tmp_path: Path, env: dict[str, str]) -> Path:
    """A repo whose index is stale for one file: same content, new mtime, so a refresh would write it."""

    repo = _repo(tmp_path, env)
    later = time.time() + 120
    os.utime(repo / "tracked.txt", (later, later))
    return repo


def test_it_never_writes_the_index_or_takes_index_lock(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    repo = _stat_dirty_repo(tmp_path, env)
    index = repo / ".git" / "index"
    lock = repo / ".git" / "index.lock"
    before = _index_state(index)

    child = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    saw_lock = False
    while child.poll() is None:
        saw_lock = saw_lock or lock.exists()
        time.sleep(0.0005)
    out, _ = child.communicate(timeout=30)
    assert child.returncode == 0
    assert b"dirty paths: 0" in out  # status ran, and saw the file as unchanged
    assert not saw_lock and not lock.exists()
    assert _index_state(index) == before


def test_the_index_check_above_can_fail(tmp_path: Path) -> None:
    """Guards the guard: on the same kind of repo a plain ``git status`` DOES rewrite the index.

    Without this the no-write test could pass on a repo git would never have
    written to, and dropping ``--no-optional-locks`` would go unnoticed.
    """

    env = _git_env(tmp_path)
    repo = _stat_dirty_repo(tmp_path, env)
    index = repo / ".git" / "index"
    before = _index_state(index)
    _git(repo, env, "status", "--porcelain")
    assert _index_state(index) != before


@posix_only
def test_a_hanging_git_is_cut_off_and_still_exits_0(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    fake_git.chmod(0o755)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    started = time.monotonic()
    proc = _run(tmp_path, env)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert elapsed < 6, elapsed  # 2.5 s budget plus interpreter start, with room for a loaded machine


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("_session_brief", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_exception_ends_in_one_line_and_exit_0(capsys: Any, monkeypatch: Any) -> None:
    module = _load_script()

    def boom(cwd: str) -> list[str]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(module, "brief", boom)
    assert module.main() == 0
    out = capsys.readouterr().out
    assert out == "session brief skipped (RuntimeError)\n"


def test_the_script_is_python_3_9_and_standard_library_only() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 9))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported, "no imports found; the walk is broken"
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
    # Calls that exist only from 3.10 on, which ast.parse cannot see.
    for call in ast.walk(tree):
        if isinstance(call, ast.Call):
            assert not any(kw.arg == "strict" for kw in call.keywords), ast.dump(call)
    # Every git call goes through ``_git`` and names a read-only subcommand: no
    # fetch, pull or remote, so no network, and nothing that writes a ref.
    subcommands = {
        call.args[1].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_git"
        and len(call.args) > 1
        and isinstance(call.args[1], ast.Constant)
    }
    assert subcommands == {"rev-parse", "status", "rev-list", "worktree"}, subcommands
    assert source.count('["git", "--no-optional-locks", *args]') == 1
    # ...and git is the only thing it starts: no ``uv run``, no ``gh``.
    assert source.count("subprocess.run(") == 1
    for spawn in ("Popen", "os.system", "os.exec", "os.spawn", "check_output", "check_call"):
        assert spawn not in source, spawn


def _old_python3() -> str | None:
    for candidate in ("python3.9", "/usr/bin/python3"):
        path = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
        if not path:
            continue
        try:
            version = subprocess.run(
                [path, "-c", "import sys; print(sys.version_info[:2] < (3, 10))"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if version == "True":
            return path
    return None


def test_it_runs_on_a_python_older_than_3_10_when_one_is_installed(tmp_path: Path) -> None:
    old = _old_python3()
    if old is None:
        pytest.skip("no Python older than 3.10 on this machine")
    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    proc = _run(repo, env, python=old)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert "worktrees: 1" in proc.stdout


def _hook_commands() -> list[dict[str, Any]]:
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    assert set(data) == {"hooks"}, "the tracked settings carry the SessionStart hook and nothing else"
    assert set(data["hooks"]) == {"SessionStart"}
    commands = [hook for group in data["hooks"]["SessionStart"] for hook in group["hooks"]]
    assert commands
    return commands


def test_the_tracked_settings_hold_only_a_bounded_session_start_hook() -> None:
    for hook in _hook_commands():
        assert hook["type"] == "command"
        assert 0 < hook["timeout"] <= 10
        command = hook["command"]
        assert "uv run" not in command and " uv " not in command
        assert "$CLAUDE_PROJECT_DIR/scripts/session_brief.py" in command
        assert command.rstrip().endswith("|| true")


@posix_only
def test_the_hook_command_exits_0_with_no_python_on_path(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    env["CLAUDE_PROJECT_DIR"] = str(REPO)
    env["PATH"] = str(tmp_path / "nothing-here")
    for hook in _hook_commands():
        proc = subprocess.run(
            ["/bin/sh", "-c", hook["command"]],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert (proc.returncode, proc.stdout) == (0, "")


@posix_only
def test_the_hook_command_prints_the_brief_with_python3_on_path(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").symlink_to(sys.executable)
    git = shutil.which("git")
    assert git is not None
    (fake_bin / "git").symlink_to(git)
    env["CLAUDE_PROJECT_DIR"] = str(REPO)
    env["PATH"] = str(fake_bin)
    for hook in _hook_commands():
        proc = subprocess.run(
            ["/bin/sh", "-c", hook["command"]],
            cwd=repo,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert proc.returncode == 0
        assert proc.stdout.startswith("aelix session brief"), proc.stdout + proc.stderr
        assert "worktrees: 1" in proc.stdout


# --- a partial clone: no lazy fetch (#733 fix round 1, Codex 4) ---------------------


def _git_version(env: dict[str, str]) -> tuple[int, int]:
    out = subprocess.run(["git", "--version"], env=env, capture_output=True, encoding="utf-8", errors="replace").stdout
    numbers = out.split()[2].split(".")
    return int(numbers[0]), int(numbers[1])


def _promisor_checkout(tmp_path: Path, env: dict[str, str]) -> tuple[Path, dict[str, str], Path]:
    """A partial clone whose HEAD tree is missing, with an ``http`` remote served by a stand-in that records its calls.

    ``GIT_EXEC_PATH`` points git at the stand-in ``git-remote-http``, so a
    lazy fetch that got as far as the transport is recorded, and nothing
    reaches a network.
    """

    repo = _repo(tmp_path, env)
    _git(repo, env, "config", "extensions.partialClone", "origin")
    _git(repo, env, "config", "remote.origin.promisor", "true")
    _git(repo, env, "config", "remote.origin.url", "http://review.invalid/repo.git")
    tree = _git(repo, env, "rev-parse", "HEAD^{tree}").strip()
    (repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
    helpers = tmp_path / "exec"
    helpers.mkdir()
    calls = tmp_path / "remote-http-calls.txt"
    helper = helpers / "git-remote-http"
    helper.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\nexit 1\n', encoding="utf-8")
    helper.chmod(0o755)
    trace = tmp_path / "trace.txt"
    return repo, {**env, "GIT_EXEC_PATH": str(helpers), "GIT_TRACE": str(trace)}, calls


def _fetches(env: dict[str, str]) -> list[str]:
    trace = Path(env["GIT_TRACE"])
    lines = trace.read_text(encoding="utf-8", errors="replace").splitlines() if trace.exists() else []
    return [line for line in lines if "run_command" in line and " fetch " in line]


@posix_only
def test_a_partial_clone_starts_no_lazy_fetch(tmp_path: Path) -> None:
    """Codex 4: ``git status`` in a promisor checkout fetched the missing tree from ``origin``."""

    env = _git_env(tmp_path)
    repo, env, calls = _promisor_checkout(tmp_path, env)
    proc = _run(repo, env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("aelix session brief"), proc.stdout
    assert not calls.exists(), calls.read_text(encoding="utf-8")
    if _git_version(env) >= (2, 45):  # GIT_NO_LAZY_FETCH: not even a local fetch process
        assert _fetches(env) == []


@posix_only
def test_a_git_older_than_2_45_still_reaches_no_remote(tmp_path: Path, monkeypatch: Any) -> None:
    """A git without ``GIT_NO_LAZY_FETCH`` starts the fetch; the protocol allow-list alone must stop it.

    The variable is honoured by 2.45 and later and by some earlier maintenance
    releases (2.39.5 measured), so this removes it rather than relying on an old git.
    """

    env = _git_env(tmp_path)
    repo, env, calls = _promisor_checkout(tmp_path, env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module = _load_script()
    as_old_git = {k: v for k, v in module.NO_TRANSPORT_ENV.items() if k != "GIT_NO_LAZY_FETCH"}
    monkeypatch.delenv("GIT_NO_LAZY_FETCH", raising=False)
    monkeypatch.setattr(module, "NO_TRANSPORT_ENV", as_old_git)
    lines = module.brief(str(repo))
    assert lines and lines[0].startswith("aelix session brief")
    assert _fetches(env), "the old-git case must start the fetch, or it measures nothing"
    assert not calls.exists(), calls.read_text(encoding="utf-8")


# --- the newest handoff, and the bounds (#733 fix round 1: F2, H4, H7) -------------


def _handoff(specs: Path, name: str, mtime: float) -> Path:
    specs.mkdir(parents=True, exist_ok=True)
    path = specs / name
    path.write_text(name + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _handoff_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("newest handoff"))


def test_the_newest_handoff_is_the_date_in_its_name_not_its_mtime(tmp_path: Path) -> None:
    """F2: ``.omc/specs`` is tracked, so a clone gives every handoff the checkout's mtime.

    By mtime, the brief named whichever file git wrote last, an old one, as "latest".
    """

    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    specs = repo / ".omc" / "specs"
    _handoff(specs, "handoff-zeta-2025-12-31.md", 1_900_000_000)  # newest mtime, oldest date
    _handoff(specs, "handoff-alpha-2026-01-05.md", 1_600_000_000)  # oldest mtime, newest date
    _handoff(specs, "handoff-mid-2026-01-03.md", 1_700_000_000)
    _handoff(specs, "handoff-undated.md", 2_000_000_000)
    _git(repo, env, "add", ".omc")
    _git(repo, env, "commit", "-q", "-m", "handoffs")
    line = _handoff_line(_run(repo, env).stdout)
    assert line.endswith("handoff-alpha-2026-01-05.md (2026-01-05)"), line
    assert line.startswith("newest handoff by the date in its name: ")

    # A real clone: the checkout writes every tracked handoff now, in its own order.
    clone = tmp_path / "clone"
    _git(tmp_path, env, "clone", "-q", str(repo), str(clone))
    line = _handoff_line(_run(clone, env).stdout)
    assert line.endswith("handoff-alpha-2026-01-05.md (2026-01-05)"), line
    assert str(clone) in line


def test_a_linked_worktree_prefers_the_main_checkouts_handoffs(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    specs = repo / ".omc" / "specs"
    _handoff(specs, "handoff-tracked-2026-01-01.md", 1_600_000_000)
    _git(repo, env, "add", ".omc")
    _git(repo, env, "commit", "-q", "-m", "handoff")
    wt = tmp_path / "wt"
    _git(repo, env, "worktree", "add", "-q", str(wt), "-b", "side")

    # The main checkout holds a newer, untracked handoff the worktree has no copy of.
    _handoff(specs, "handoff-untracked-2026-02-01.md", 1_500_000_000)
    line = _handoff_line(_run(wt, env).stdout)
    assert line.endswith(f"{specs / 'handoff-untracked-2026-02-01.md'} (2026-02-01)"), line

    # On an equal date the main checkout's file wins, even with the older mtime.
    _handoff(wt / ".omc" / "specs", "handoff-wt-2026-02-01.md", 1_900_000_000)
    line = _handoff_line(_run(wt, env).stdout)
    assert line.endswith(f"{specs / 'handoff-untracked-2026-02-01.md'} (2026-02-01)"), line
    # ...and a later date in the worktree wins over it.
    _handoff(wt / ".omc" / "specs", "handoff-wt-2026-02-02.md", 1_000_000_000)
    line = _handoff_line(_run(wt, env).stdout)
    assert line.endswith(f"{wt / '.omc' / 'specs' / 'handoff-wt-2026-02-02.md'} (2026-02-02)"), line


def test_with_no_dated_name_the_newest_mtime_is_named_and_labelled_so(tmp_path: Path) -> None:
    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    _handoff(repo / ".omc" / "specs", "handoff-a.md", 1_600_000_000)
    _handoff(repo / ".omc" / "specs", "handoff-b.md", 1_700_000_000)
    line = _handoff_line(_run(repo, env).stdout)
    assert line == f"newest handoff (no name carries a date; by mtime): {repo / '.omc' / 'specs' / 'handoff-b.md'}"


def test_ten_active_locks_name_eight_and_the_brief_is_at_most_16_lines(tmp_path: Path) -> None:
    """H7: without the cap every lock was listed; ADR-0303 says at most 16 lines."""

    env = _git_env(tmp_path)
    repo = _repo(tmp_path, env)
    _git(repo, env, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, env, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
    for issue in range(201, 211):
        _owner(repo, issue, state="active", branch=f"fix/{issue}", phase="impl")
    _handoff(repo / ".omc" / "specs", "handoff-x-2026-01-01.md", 1_700_000_000)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "preflight.py").write_text("", encoding="utf-8")
    skill_dir = repo / ".agents" / "skills" / "aelix-demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: aelix-demo\n---\n", encoding="utf-8")

    lines = _run(repo, env).stdout.splitlines()
    assert "active issue locks (10): other sessions may be working here" in lines
    named = [line for line in lines if line.startswith("  #")]
    assert len(named) == 8 and "  +2 more" in lines
    assert len(lines) == 16, lines  # every line the brief can print, at its largest


def test_no_git_call_starts_once_the_budget_is_spent(monkeypatch: Any) -> None:
    """H4: with git calls of 1 s each, each call gets what is left of 2.5 s (at most 2 s), and none starts after.

    A clock that advances one second per git call stands in for a slow git, so
    the 2.5 s budget and the 2 s per-call cap are measured exactly.
    """

    module = _load_script()
    clock = {"now": 0.0}
    started: list[tuple[str, float]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        started.append((argv[2], kwargs["timeout"]))
        clock["now"] += 1.0
        replies = {
            "rev-parse": b"/repo\n/repo/.git\n",
            "status": b"# branch.oid 0123456789abcdef\n# branch.head work\n",
        }
        return subprocess.CompletedProcess(argv, 0, replies.get(argv[2], b"1\t0\n"), b"")

    class _Clock:
        @staticmethod
        def monotonic() -> float:
            return clock["now"]

    monkeypatch.setattr(module, "time", _Clock)
    monkeypatch.setattr(module, "_START", 0.0)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.brief("/repo")
    assert started == [("rev-parse", 2.0), ("status", 1.5), ("rev-parse", 0.5)], started
