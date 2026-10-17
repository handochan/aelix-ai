"""The guard half of #458: what ``tests/session_root_guard.py`` blocks, and what it leaves alone.

``tests/test_suite_session_isolation_458.py`` runs the real ``conftest.py``
end to end. This file tests the parts. Each case that trips the guard on purpose
points it at roots under ``tmp_path`` (:func:`protecting`) inside its own inner
:func:`guard`, so nothing here comes near the developer's real
``~/.aelix/sessions``. The inner guard collects the violation, and the
autouse guard around the test stays empty. The guard judges in-process
operations only, and only the ones its module lists; children are the pin's.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_agent_core.session.jsonl_repo import JsonlSessionCreateOptions
from aelix_agent_core.session.storage import SessionError
from aelix_coding_agent.cli.config import ENV_SESSION_DIR

from tests import session_root_guard as srg
from tests.env_sandbox import child_env, sandbox_home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A stand-in for ``~/.aelix/sessions``, holding one session file."""

    sessions = tmp_path / "home" / ".aelix" / "sessions"
    (sessions / "--bucket--").mkdir(parents=True)
    (sessions / "--bucket--" / "s.jsonl").write_text("{}\n", encoding="utf-8")
    return sessions


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def _create_session(repo: JsonlSessionRepo, cwd: Path) -> None:
    asyncio.run(repo.create(JsonlSessionCreateOptions(cwd=str(cwd))))


# === what is protected ===========================================================


def test_the_guard_watches_the_products_default_root() -> None:
    """``JsonlSessionRepo``'s default is what a leak writes. If it moves, this
    fails, instead of the guard silently watching the old place."""

    assert JsonlSessionRepo(fs=LocalFileSystem())._sessions_root_input == srg.DEFAULT_SESSIONS_ROOT


def test_the_run_protects_the_home_it_started_with() -> None:
    """Computed by string from the start-of-run home, never by reading the root."""

    roots = srg.install()
    assert srg._norm(str(Path.home() / ".aelix" / "sessions")) in roots


def test_a_configured_session_dir_is_protected_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A developer who exported the variable keeps their sessions there instead.
    Spelled with ``~``, it is expanded as ``get_session_dir`` expands it."""

    mine = tmp_path / "my-sessions"
    assert srg._norm(str(mine)) in srg.protected_roots({ENV_SESSION_DIR: str(mine)})
    home = sandbox_home(monkeypatch, tmp_path / "home")
    assert srg._norm(str(home / "sess")) in srg.protected_roots({ENV_SESSION_DIR: "~/sess"})


def test_a_home_spelled_through_a_symlink_is_protected_where_the_repo_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``JsonlSessionRepo`` resolves its root, so a HOME given through a symlink
    writes the RESOLVED path. The guard protects the resolved home's root too."""

    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "link-home"
    _symlink_or_skip(link, real)
    sandbox_home(monkeypatch, link)
    roots = srg.protected_roots({})
    repo = JsonlSessionRepo(fs=LocalFileSystem())

    with srg.protecting(roots), srg.guard() as found, pytest.raises(SessionError):
        _create_session(repo, tmp_path)

    assert found and found[0].startswith("mkdir "), found
    assert not (real / ".aelix" / "sessions").exists()


def test_an_exported_dir_spelled_through_a_symlink_is_protected_where_the_repo_writes(
    tmp_path: Path,
) -> None:
    """The same for an exported ``AELIX_CODING_AGENT_SESSION_DIR``: its parent
    is resolved too (never the root itself), and the dir stays protected as
    given, which is the spelling code that does not resolve it writes."""

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _symlink_or_skip(link, real)
    exported = link / "sessions"
    roots = srg.protected_roots({ENV_SESSION_DIR: str(exported)})
    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(exported))

    with srg.protecting(roots), srg.guard() as found, pytest.raises(SessionError):
        _create_session(repo, tmp_path)

    assert found, found
    assert not (real / "sessions").exists()

    with (
        srg.protecting(roots),
        srg.guard() as found,
        pytest.raises(PermissionError, match="#458"),
    ):
        exported.mkdir()  # the given spelling, through the link

    assert len(found) == 1, found
    assert not (real / "sessions").exists()


def test_the_password_database_home_is_protected_under_a_sandboxed_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run with a sandboxed HOME still protects the password database's home:
    that is where ``~`` goes when a test unsets HOME, and it is the developer's
    real home. A stand-in password home keeps the real one out of reach."""

    password_home = tmp_path / "pw-home"
    monkeypatch.setattr(srg, "_password_home", lambda: str(password_home))
    sandbox_home(monkeypatch, tmp_path / "home")
    roots = srg.protected_roots({})
    target = password_home / ".aelix" / "sessions"
    target.parent.mkdir(parents=True)

    with (
        srg.protecting(roots),
        srg.guard() as found,
        pytest.raises(PermissionError, match="#458"),
    ):
        target.mkdir()

    assert len(found) == 1, found
    assert not target.exists()


def test_the_real_password_home_is_among_the_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """By string only: the real password home is never touched."""

    if sys.platform == "win32":  # in the body, so pyright narrows the POSIX calls
        pytest.skip("Windows has no password database")
    import pwd

    try:
        password_home = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        pytest.skip("this uid has no password entry")
    sandbox_home(monkeypatch, tmp_path / "home")
    expected = srg._norm(os.path.join(password_home, ".aelix", "sessions"))
    assert expected in srg.protected_roots({})


def test_a_root_that_is_or_contains_the_temp_dir_is_not_protected(tmp_path: Path) -> None:
    """Every ``tmp_path`` and every per-test root lives in the temp dir. A
    developer who exports the temp dir, or an ancestor of it, as the session dir
    must not turn every test that writes a file into an error."""

    temp = tmp_path / "temp"
    temp.mkdir()
    for exported in (temp, tmp_path):
        roots = srg.protected_roots({ENV_SESSION_DIR: str(exported)}, temp_dir=str(temp))
        assert srg._norm(str(exported)) not in roots
    # A dir INSIDE the temp dir does not hold it, and stays protected.
    inside = temp / "sessions"
    roots = srg.protected_roots({ENV_SESSION_DIR: str(inside)}, temp_dir=str(temp))
    assert srg._norm(str(inside)) in roots


def test_a_root_holding_the_temp_dir_is_dropped_in_both_spellings(tmp_path: Path) -> None:
    """``TMPDIR=/tmp/x`` with the same value exported on macOS: the root's
    resolved spelling, ``/private/tmp/x``, holds every ``tmp_path`` too. And the
    other way round: a root exported in its resolved spelling, or through a
    second symlink, holds a temp dir given through the first."""

    real = tmp_path / "real"
    (real / "temp").mkdir(parents=True)
    link = tmp_path / "link"
    _symlink_or_skip(link, real)
    temp = link / "temp"
    other = tmp_path / "other-link"  # a second way to spell the same directory
    _symlink_or_skip(other, real)
    for exported in (temp, link, real / "temp", real, other / "temp"):
        roots = srg.protected_roots({ENV_SESSION_DIR: str(exported)}, temp_dir=str(temp))
        assert srg._norm(str(exported)) not in roots, exported
        assert srg._norm(os.path.realpath(exported)) not in roots, exported


def test_the_run_removes_every_root_it_handed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run_session_root`` calls this at the end of the run, so a run leaves
    no ``aelix-s-*`` in the temp dir. The child pytest in
    ``tests/test_suite_session_isolation_458.py`` checks the wiring."""

    monkeypatch.setattr(srg, "_base", None)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    first, second = srg.next_session_root(), srg.next_session_root()
    assert first.parent == second.parent and first.parent.parent == tmp_path
    for made in (first, second):
        (made / "--bucket--").mkdir(parents=True)
        (made / "--bucket--" / "s.jsonl").write_text("{}\n", encoding="utf-8")

    srg.remove_session_roots()

    assert sorted(tmp_path.glob("aelix-s-*")) == []


def test_a_root_is_not_created_until_a_session_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root and the run's ``aelix-s-*`` are paths only until something writes
    a session. So a run that writes none leaves nothing in the temp dir, even a
    child pytest killed before its teardown (``tests/test_ci_hang_guard_429.py``)."""

    monkeypatch.setattr(srg, "_base", None)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    handed_out = srg.next_session_root()
    assert handed_out.parent.parent == tmp_path
    assert sorted(tmp_path.iterdir()) == []


def test_the_runs_directory_has_a_random_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``aelix-s-`` and 8 random hex digits, so another user of a shared
    ``/tmp`` cannot predict the name (a pid would do) and create it first."""

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    names = set()
    for _ in range(2):
        monkeypatch.setattr(srg, "_base", None)
        names.add(srg.next_session_root().parent.name)
    assert len(names) == 2, names
    assert all(re.fullmatch(r"aelix-s-[0-9a-f]{8}", name) for name in names), names


def test_computing_the_roots_never_resolves_a_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.path.realpath`` stats the path it is given and its ancestors. The
    guard resolves each home, the exported dir's parent and the temp dir, never
    a root or anything in one, so it never stats the developer's sessions.
    Every home here is a stand-in, so even a wrong form stays off the real one."""

    password_home = tmp_path / "pw-home"
    monkeypatch.setattr(srg, "_password_home", lambda: str(password_home))
    sandbox_home(monkeypatch, tmp_path / "home")
    resolved: list[str] = []
    realpath = os.path.realpath

    def recording(path: str, **kwargs: bool) -> str:
        resolved.append(path)
        return realpath(path, **kwargs)

    monkeypatch.setattr(os.path, "realpath", recording)
    roots = srg.protected_roots({ENV_SESSION_DIR: str(tmp_path / "exp" / "sessions")})
    monkeypatch.setattr(os.path, "realpath", realpath)

    assert len(roots) >= 3 and resolved, (roots, resolved)
    assert [path for path in resolved if srg._under(path, roots)] == []


# === in-process writes ===========================================================


@pytest.mark.parametrize(
    "operation",
    [
        "write_text",
        "append",
        "open_exclusive",
        "open_read_plus",
        "os_open_create",
        "os_open_read_only_create",
        "bytes_path",
        "mkdir",
        "rename_into",
        "replace_into",
        "rename_out_of",
        "remove",
        "rmdir",
        "chmod",
        "symlink_into",
        "link_into",
        "truncate",
        "rmtree",
    ],
)
def test_every_kind_of_write_under_the_root_is_blocked(
    root: Path, tmp_path: Path, operation: str
) -> None:
    session = root / "--bucket--" / "s.jsonl"
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    actions = {
        "write_text": lambda: (root / "new.jsonl").write_text("{}", encoding="utf-8"),
        "append": lambda: session.open("a", encoding="utf-8").close(),
        "open_exclusive": lambda: (root / "x.jsonl").open("x", encoding="utf-8").close(),
        "open_read_plus": lambda: session.open("r+", encoding="utf-8").close(),
        "os_open_create": lambda: os.close(os.open(root / "n", os.O_WRONLY | os.O_CREAT, 0o600)),
        "os_open_read_only_create": lambda: os.close(
            os.open(root / "r", os.O_RDONLY | os.O_CREAT, 0o600)
        ),
        "bytes_path": lambda: open(os.fsencode(root / "b.jsonl"), "w").close(),  # noqa: SIM115
        "mkdir": lambda: (root / "--new--").mkdir(),
        "rename_into": lambda: os.rename(outside, root / "moved"),
        "replace_into": lambda: os.replace(outside, session),
        "rename_out_of": lambda: os.rename(session, tmp_path / "stolen"),
        "remove": lambda: session.unlink(),
        "rmdir": lambda: (root / "--bucket--").rmdir(),
        "chmod": lambda: os.chmod(session, 0o644),
        "symlink_into": lambda: os.symlink(outside, root / "link"),
        "link_into": lambda: os.link(outside, root / "hard"),
        "truncate": lambda: os.truncate(session, 0),
        "rmtree": lambda: shutil.rmtree(root),
    }
    before = sorted(p.relative_to(root) for p in root.rglob("*"))

    with (
        srg.protecting([root]),
        srg.guard() as found,
        pytest.raises(PermissionError, match="#458"),
    ):
        actions[operation]()

    assert len(found) == 1, found
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before
    assert session.read_text(encoding="utf-8") == "{}\n"


def test_a_different_case_spelling_is_the_root_where_the_file_system_folds_case(
    root: Path,
) -> None:
    """APFS (macOS) and NTFS (Windows) treat ``.AELIX/Sessions`` as
    ``.aelix/sessions`` by default, so the guard compares without case there.
    Linux file systems do not, and there it is another directory."""

    variant = root.parent.parent / ".AELIX" / "Sessions" / "x.jsonl"
    if sys.platform in ("darwin", "win32"):
        with (
            srg.protecting([root]),
            srg.guard() as found,
            pytest.raises(PermissionError, match="#458"),
        ):
            variant.write_text("{}", encoding="utf-8")
        assert len(found) == 1, found
    else:
        with srg.protecting([root]), srg.guard() as found:
            variant.parent.mkdir(parents=True)
            variant.write_text("{}", encoding="utf-8")
        assert found == []
    assert not (root / "x.jsonl").exists()


@pytest.mark.parametrize("spelling", ["relative", "dot-dot"])
def test_a_path_is_judged_where_it_lands_not_as_spelled(
    root: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    """Paths are made absolute before they are compared: a relative path from a
    cwd above the root, and one that climbs back in through ``..``, are both
    under it."""

    (root.parent / "beside").mkdir()
    monkeypatch.chdir(root.parent)
    target = {
        "relative": os.path.join(root.name, "rel.jsonl"),
        "dot-dot": str(root.parent / "beside" / ".." / root.name / "up.jsonl"),
    }[spelling]

    with (
        srg.protecting([root]),
        srg.guard() as found,
        pytest.raises(PermissionError, match="#458"),
    ):
        open(target, "w").close()  # noqa: SIM115

    assert len(found) == 1, found
    assert sorted(p.name for p in root.iterdir()) == ["--bucket--"]


def test_reads_and_neighbours_are_not_writes(root: Path, tmp_path: Path) -> None:
    """Reading the root is not judged, and neither is a sibling whose name only
    starts like the root's."""

    sibling = root.parent / (root.name + "-other")
    with srg.protecting([root]), srg.guard() as found:
        assert (root / "--bucket--" / "s.jsonl").read_text(encoding="utf-8") == "{}\n"
        assert os.listdir(root) == ["--bucket--"]
        sibling.mkdir()
        (sibling / "f").write_text("ok", encoding="utf-8")
        (tmp_path / "elsewhere").mkdir()
    assert found == []


def test_the_real_repo_writing_its_default_root_is_blocked(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process leak the pin cannot reach: a repo built with no
    ``sessions_root`` ignores the variable and writes under HOME."""

    sandbox_home(monkeypatch, root.parent.parent)
    repo = JsonlSessionRepo(fs=LocalFileSystem())
    with (
        srg.protecting([root]),
        srg.guard() as found,
        pytest.raises(SessionError),  # the repo wraps the OSError
    ):
        _create_session(repo, root.parent)
    assert found and found[0].startswith("mkdir ")
    assert sorted(p.name for p in root.iterdir()) == ["--bucket--"]


def test_between_tests_the_hook_is_inert(root: Path) -> None:
    """Collection and session-scoped fixtures run with no guard active, and the
    hook must not judge them: there is no test to fail."""

    outer = srg._violations  # this test's own autouse guard
    assert outer is not None
    srg._violations = None
    try:
        with srg.protecting([root]):
            (root / "between-tests").mkdir()
    finally:
        srg._violations = outer
    assert (root / "between-tests").is_dir()


def test_the_inner_helpers_put_back_what_they_replaced(root: Path) -> None:
    """:func:`protecting` and :func:`guard` restore the outer roots and the
    outer list on exit. Otherwise every test after this file would protect a
    stand-in only, or the hook would keep collecting into a dead list."""

    outer_roots, outer_found = srg._roots, srg._violations
    assert outer_roots == srg.install() and outer_found is not None
    with srg.protecting([root]), srg.guard():
        pass
    assert srg._roots == outer_roots
    assert srg._violations is outer_found


# === children are the pin's, not the guard's =====================================


@pytest.mark.parametrize(
    "argv_tail",
    [
        pytest.param(("-c", "pass  # aelix_coding_agent"), id="source-naming-aelix"),
        pytest.param(("SCRIPT",), id="script-under-an-aelix_-path"),
    ],
)
def test_the_guard_does_not_judge_children(
    root: Path, tmp_path: Path, argv_tail: tuple[str, ...]
) -> None:
    """Starting a child is never blocked, whatever its argv and environment.

    Review round 1 judged aelix children, and a checkout whose path contained
    ``aelix_`` turned 13 MCP tests red (the MCP SDK starts its stdio servers
    with HOME and no pin). Here HOME leads to the protected root, and the
    child still starts.
    """

    script = tmp_path / "aelix_checkout" / "server.py"
    script.parent.mkdir()
    script.write_text("pass\n", encoding="utf-8")
    argv = [sys.executable, *(str(script) if a == "SCRIPT" else a for a in argv_tail)]
    with srg.protecting([root]), srg.guard() as found:
        child = subprocess.run(argv, env=child_env(root.parent.parent), check=False, timeout=120)
    assert child.returncode == 0
    assert found == []
