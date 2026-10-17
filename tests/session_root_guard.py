"""Keep the suite's sessions out of the developer's real session root (#458).

THE DEFECT. ``aelix`` roots every session at ``AELIX_CODING_AGENT_SESSION_DIR``,
or at ``~/.aelix/sessions`` when that is unset (``cli/config.py``
``get_session_dir``, then ``JsonlSessionRepo``'s default). The suite never set the
variable. The isolation most CLI tests DO apply, ``AELIX_CODING_AGENT_DIR`` and
``AELIX_SETTINGS_PATH``, moves settings and auth but not sessions. So every test
that ran the CLI without also sandboxing HOME left a ``--<encoded pytest tmp
path>--`` directory in the developer's own ``~/.aelix/sessions``. The owner's
machine had about 7,450 of them, 492 from a single day, and the stats dashboard
counts each one as a project. Measured on 529b997e with a scratch HOME, a full
run (16,701 tests) left six, all from ``tests/cli/test_tools_flag_validation.py``.

``tests/conftest.py`` wires two parts, both from this module.

* THE PIN is the fix. ``_isolated_session_root`` sets the variable for every
  test to a directory of that test's own (:func:`next_session_root`). It covers
  in-process code that reads the variable, and every child that inherits the
  test's environment (``env=None``, ``dict(os.environ)``, ``{**os.environ, ...}``).
  Every leaking test had that shape. The pin is a default: it applies only when,
  at the test's start, the variable is unset, empty (``get_session_dir`` reads
  ``''`` as unset) or still the run's own root. So a module-, class- or
  session-scoped fixture that set the variable to a value reaches its tests and
  the children they start. That value stays in effect until the fixture is torn
  down: for the rest of its module or class, and for a session-scoped fixture
  (not autouse) for every test that runs after it, until the run ends, whether
  or not that test requests it. A test that sets it itself wins (the same
  function-scoped ``monkeypatch``, last write wins). A value a test wrote
  straight into ``os.environ`` and left behind would reach later tests the same
  way; no test in the suite writes the variable that way. ``_run_session_root``
  gives the whole run a root as well, for module- and session-scoped fixtures,
  which are set up before any test's pin.

* THE GUARD is a best-effort net, not a proof. It is there for code the pin
  cannot reach because it does not read the variable: ``JsonlSessionRepo()``
  with no ``sessions_root`` (``aelix-server``'s ``/rpc``, run in process by a
  ``TestClient``, builds one), or a test that removes the pin without
  sandboxing the home. In THIS PROCESS, while a test runs, it blocks and
  records these operations when the path the call is given lies under a
  protected root: a write-mode ``open`` (``w``, ``a``, ``x`` or ``+``; for
  ``os.open``, a write or create flag), ``mkdir``, ``rename`` / ``replace``
  (either end), ``remove``, ``rmdir``, ``chmod``, ``link`` and ``symlink`` (the
  new name), ``truncate``, and ``shutil.rmtree`` of the root or of a path inside
  it. The fixture then fails the test in teardown, even when the code under
  test caught the error.

WHAT THE GUARD MISSES, in process. At least these change a protected root
unseen (each measured on macOS with a stand-in root, review round 3 of #458):

* an operation on an ANCESTOR of a root: ``os.rename`` of ``~/.aelix`` or of
  the home and, on POSIX, ``shutil.rmtree`` of either. The rename's paths are
  not under the root, and ``rmtree`` reports only its top, then on POSIX
  deletes through names relative to a ``dir_fd``. On Windows ``rmtree`` deletes
  by full path, so the guard would see it once it reaches the root (reasoned
  from CPython's ``shutil``, not measured);
* any path relative to a ``dir_fd``: the guard does not judge those;
* ``os.utime`` and ``Path.touch()`` of an existing file, ``os.chflags``,
  ``os.mkfifo``, ``sqlite3.connect``, and an ``AF_UNIX`` socket bound there;
* a path that reaches the root through a symlink outside it, and a
  ``~/.aelix`` or root that is itself a symlink: paths are compared as
  strings, and only the homes, the exported dir's parent and the temp dir are
  resolved;
* anything a module- or session-scoped fixture does: it runs outside every
  test's guard (the run's root still pins such a fixture's code and the
  children it starts from ``os.environ``).

NOT COVERED, by either part. The guard does not judge child processes at all. A
child given a hand-built environment without the pin, a child started through a
shell, ``env`` or ``uv run`` that drops the pin, and an ``aelix-server`` child
(its ``/rpc`` builds ``JsonlSessionRepo(fs=fs)`` with no ``sessions_root`` and
ignores the variable, a product bug) all write where their own home says. The
suite's hand-built child environments sandbox the home (``tests/env_sandbox.py``
``child_env``), and that is what keeps them out. Review round 1 of #458 judged
aelix children by their argv and environment, and that was withdrawn: it
misread the CLI's flags, missed wrapped children, and blocked the MCP SDK's
stdio servers whenever the checkout or temp path contained ``aelix_``.

THE PROTECTED ROOTS (:func:`protected_roots`), computed once, from the
environment the run started with, before any test sandboxes anything:

* ``~/.aelix/sessions`` under the start-of-run home (``Path.home()``), as given
  and with the home resolved, because ``JsonlSessionRepo`` resolves its root
  (``/tmp`` is ``/private/tmp`` on macOS) and the operation carries that path;
* the same two under the password database's home (POSIX only): ``~`` falls
  back to it when a test unsets ``HOME``, and it is the developer's real home
  when the run itself has a sandboxed one;
* an exported ``AELIX_CODING_AGENT_SESSION_DIR``, as given and with its parent
  resolved (not resolved when it lies inside a home's root, which covers it).

A root that is the temp dir or contains it is NOT protected, judged on the given
and resolved spellings of both. Every ``tmp_path`` and every per-test root lives
in the temp dir, so protecting it would turn every test that writes a file into
an error. That happens when a developer exports the temp dir, or an ancestor of
it, as the session dir.

Paths are compared after ``os.path.abspath``, and case-insensitively on macOS
and Windows: their default file systems (APFS, NTFS) treat ``.AELIX/Sessions``
as ``.aelix/sessions``, and ``os.path.normcase`` folds case on Windows only.

HOW THE GUARD JUDGES WITHOUT LOOKING. It never opens, lists or stats a protected
root, not even to compare before and after. Doing so would read the developer's
private sessions on every test, and the owner's own aelix runs alongside the
suite and changes that directory. Its only file-system calls are
``os.path.realpath`` of each home, of the exported root's parent (not when that
lies in a home's root) and of the temp dir. ``realpath`` stats a path and its
ancestors, and none of those is a protected root: a root that holds the temp
dir is not protected. An audit hook (``sys.addaudithook``) judges the
OPERATION, from the path its audit event carries. For an operation listed
above, it raises ``PermissionError``, so the operation does not happen, and
records it.

WINDOWS. ``Path.home()`` reads ``%USERPROFILE%`` there, and there is no password
database. The per-test roots sit under ONE short directory in the system temp
dir, not under pytest's basetemp. A session file's path is the root, then a
bucket that encodes the whole cwd, then a 67-character file name (72 with its
``.lock``), and ``MAX_PATH`` is 260 on a runner without long paths.
``C:\\Users\\runneradmin\\.aelix\\sessions`` was 36 characters. This root is at
most 59 on a runner as configured: the 36-character temp dir, ``aelix-s-`` and 8
hex digits, then the test's number. Under basetemp it would be about 80.

PI. pi isolates its whole test run in ``test.sh`` instead (``env -i`` with a
temporary ``HOME`` / ``USERPROFILE``, pi ``e66e3e588``). Aelix runs a bare
``uv run pytest``, so the pin must live in the suite itself. The guard has no pi
counterpart.
"""

from __future__ import annotations

import itertools
import os
import secrets
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from aelix_coding_agent.cli.config import CONFIG_DIR_NAME, ENV_SESSION_DIR

__all__ = [
    "DEFAULT_SESSIONS_ROOT",
    "describe",
    "guard",
    "install",
    "next_session_root",
    "protected_roots",
    "protecting",
    "remove_session_roots",
]

#: ``JsonlSessionRepo``'s default ``sessions_root``, spelled as the product spells
#: it. ``tests/test_session_root_guard_458.py`` asserts the two stay equal, so a
#: moved default fails a test instead of leaving this guard watching the old place.
DEFAULT_SESSIONS_ROOT = f"~/{CONFIG_DIR_NAME}/sessions"

# ``open`` reports an ``os.open`` with ``mode=None`` and the real flags.
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

# APFS and NTFS compare names case-insensitively by default, and
# ``os.path.normcase`` folds case on Windows only.
_FOLD_CASE = sys.platform in ("darwin", "win32")

_roots: tuple[str, ...] = ()
_violations: list[str] | None = None
_installed = False
_base: Path | None = None
_counter = itertools.count()


# === where the developer's sessions are =========================================


def _norm(path: str) -> str:
    normed = os.path.normcase(os.path.abspath(path))
    return normed.lower() if _FOLD_CASE else normed


def _under(path: str, roots: Iterable[str]) -> bool:
    candidate = _norm(path)
    return any(candidate == root or candidate.startswith(root + os.sep) for root in roots)


def _password_home() -> str | None:
    """Where ``~`` goes on POSIX when ``HOME`` is unset (``posixpath.expanduser``)."""

    if sys.platform == "win32":
        return None
    import pwd

    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:  # a uid with no passwd entry (some containers)
        return None


def protected_roots(
    environ: Mapping[str, str] | None = None, *, temp_dir: str | None = None
) -> tuple[str, ...]:
    """The roots a test must never write, normalised for :func:`_under`.

    The homes are ``Path.home()`` and the password database's. ``environ`` is
    where an exported ``AELIX_CODING_AGENT_SESSION_DIR`` is read (default
    ``os.environ``), and ``temp_dir`` defaults to ``tempfile.gettempdir()``. Each
    root comes with its resolved spelling, and a root whose either spelling holds
    either spelling of the temp dir is dropped with both.
    """

    environ = os.environ if environ is None else environ
    homes: list[str] = []
    with suppress(RuntimeError):  # Windows with neither USERPROFILE nor HOMEPATH
        homes.append(str(Path.home()))
    if (password_home := _password_home()) is not None:
        homes.append(password_home)
    groups = [
        [
            _norm(os.path.join(h, CONFIG_DIR_NAME, "sessions"))
            for h in (home, os.path.realpath(home))
        ]
        for home in homes
    ]
    configured = environ.get(ENV_SESSION_DIR)
    if configured:
        exported = os.path.abspath(Path(configured).expanduser())
        group = [_norm(exported)]
        parent = os.path.dirname(exported)
        # The parent, never the root itself. Not when it lies in a home's root,
        # which covers it: resolving it would stat that root.
        if not _under(parent, [root for home_group in groups for root in home_group]):
            real_parent = os.path.realpath(parent)
            group.append(_norm(os.path.join(real_parent, os.path.basename(exported))))
        groups.append(group)
    temp = tempfile.gettempdir() if temp_dir is None else temp_dir
    temps = (temp, os.path.realpath(temp))
    roots = [
        root
        for group in groups
        if not any(_under(spelling, group) for spelling in temps)
        for root in group
    ]
    return tuple(dict.fromkeys(roots))


# === the audit hook ===============================================================


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, int):
        return None  # an fd: no path to judge
    try:
        return os.fsdecode(os.fspath(value))
    except TypeError:
        return None


def _hit(operation: str, value: Any, dir_fd: Any = -1) -> str | None:
    if dir_fd not in (None, -1):
        # Relative to an fd: not judged. ``shutil.rmtree``'s own event covers its
        # walk only when its top is the root or inside it; an rmtree of an
        # ancestor deletes the root through such paths unseen (POSIX).
        return None
    path = _text(value)
    if path is None or not _under(path, _roots):
        return None
    return f"{operation} {path}"


def _check_open(args: tuple[Any, ...]) -> str | None:
    path, mode, flags = args
    if isinstance(mode, str):
        writes = any(ch in mode for ch in "wax+")
    else:
        writes = bool(flags & _WRITE_FLAGS)
    return _hit("open for writing", path) if writes else None


def _check_rename(args: tuple[Any, ...]) -> str | None:
    src, dst, src_dir_fd, dst_dir_fd = args
    return _hit("rename from", src, src_dir_fd) or _hit("rename to", dst, dst_dir_fd)


# In-process operations only. Nothing here judges a child process
# (``subprocess.Popen``, ``os.posix_spawn``, ``os.exec*``): children are the pin's.
_CHECKS: dict[str, Callable[[tuple[Any, ...]], str | None]] = {
    "open": _check_open,
    "os.mkdir": lambda a: _hit("mkdir", a[0], a[2]),
    "os.rename": _check_rename,  # os.replace raises this event too
    "os.remove": lambda a: _hit("remove", a[0], a[1]),
    "os.rmdir": lambda a: _hit("rmdir", a[0], a[1]),
    "os.chmod": lambda a: _hit("chmod", a[0], a[2]),
    "os.link": lambda a: _hit("link to", a[1], a[3]),
    "os.symlink": lambda a: _hit("symlink to", a[1], a[2]),
    "os.truncate": lambda a: _hit("truncate", a[0]),
    "shutil.rmtree": lambda a: _hit("rmtree", a[0], a[1]),
}


def _audit(event: str, args: tuple[Any, ...]) -> None:
    violations = _violations
    if violations is None:
        return
    check = _CHECKS.get(event)
    if check is None:
        return
    try:
        found = check(args)
    except Exception:  # a guard bug must not break the test it watches
        return
    if found is None:
        return
    violations.append(found)
    raise PermissionError(f"#458 guard: a test must not {found}")


def install(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Protect the roots of the environment the run started with (once per process)."""

    global _roots, _installed
    if not _installed:
        _roots = protected_roots(environ)
        sys.addaudithook(_audit)
        _installed = True
    return _roots


@contextmanager
def guard() -> Iterator[list[str]]:
    """Judge every in-process operation until exit, and collect what was blocked.

    Re-entrant: an inner guard collects its own list and restores the outer one,
    which is how the guard's own tests trip it without failing themselves.
    """

    global _violations
    outer = _violations
    found: list[str] = []
    _violations = found
    try:
        yield found
    finally:
        _violations = outer


@contextmanager
def protecting(roots: Iterable[str | os.PathLike[str]]) -> Iterator[tuple[str, ...]]:
    """Replace the protected roots until exit, for tests that trip the guard on purpose."""

    global _roots
    outer = _roots
    _roots = tuple(_norm(os.fspath(root)) for root in roots)
    try:
        yield _roots
    finally:
        _roots = outer


def describe(violations: Sequence[str]) -> str:
    listed = "\n".join(f"  - {item}" for item in violations)
    return (
        "#458: this test wrote under the developer's REAL session root, in this "
        f"process, and the guard blocked it:\n{listed}\n"
        "tests/conftest.py pins AELIX_CODING_AGENT_SESSION_DIR to a root of each test's "
        "own when, at the test's start, the variable is unset, empty or the run's own "
        "root; a module-, class- or session-scoped fixture that set it to a value keeps "
        "that value. So the write came from code that does not read the variable (a "
        "JsonlSessionRepo with no sessions_root), from code handed that root "
        "explicitly, from such a fixture's value, or from a test that removed or "
        "replaced the pin without sandboxing the home. Pass a sessions_root of the "
        "test's own, or sandbox the home (tests.env_sandbox.sandbox_home)."
    )


# === the per-test roots ============================================================


def next_session_root() -> Path:
    """A fresh session root for one test, not created yet.

    ``JsonlSessionRepo`` creates it on the first write, and a root that does not
    exist reads the same as a fresh user's. Its parent, one per run, is not
    created either, so a run that writes no session leaves nothing in the temp
    dir. That includes a child pytest that is killed before its teardown, which
    is how ``tests/test_ci_hang_guard_429.py`` ends. The parent's name is
    ``aelix-s-`` and 8 random hex digits, so another user of a shared ``/tmp``
    cannot predict it and create it first (``mkdtemp`` rules that out by
    creating the directory, which this does not do). Every bucket the repo
    creates inside it is ``0o700``.
    """

    global _base
    if _base is None:
        _base = Path(tempfile.gettempdir()) / f"aelix-s-{secrets.token_hex(4)}"
    return _base / str(next(_counter))


def remove_session_roots() -> None:
    """Delete every per-test root this process handed out (session teardown)."""

    global _base
    if _base is not None:
        shutil.rmtree(_base, ignore_errors=True)
        _base = None
