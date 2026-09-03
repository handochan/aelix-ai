"""The 0600 system-prompt hand-off file — ADR-0197 §(h).

No ``asyncio``, no ``subprocess``. Just the create/remove primitives for the
one artefact a spawn leaves on disk.

WHY A FILE AND NOT AN ARGUMENT. The profile BODY is the child's system prompt,
and it can be arbitrarily long. Passing it on the command line would put a
user's private prompt into ``/proc/<pid>/cmdline``, readable by every process
of every user on many default configurations, and would risk ``E2BIG`` on a
long profile. ``agents/resolver.py:280``/``:282`` already render
``--system-prompt-file`` / ``--append-system-prompt-file``, so the child needs
only a path.

WHY 0700 + 0600 AND NOT ``NamedTemporaryFile``. The directory is created first
with ``mkdtemp`` (0700, no race), and the file inside it with
``O_CREAT | O_EXCL`` at 0600. That ordering means the mode is correct at
CREATION — there is no instant where the file exists world-readable and is
chmod'ed afterwards. ``O_EXCL`` additionally refuses to follow a symlink
planted at the target path.

LIFECYCLE, and it has more than one owner. :func:`remove_prompt_dir` must be
reachable from three places, because a spawn can end in three ways:

* the normal ``try/finally`` in ``PrintChannel.run``;
* ``stop`` / ``stop_all``, when the owning task was cancelled before its
  ``finally`` ran (a second Ctrl+C can do exactly this — ``tui/chrome.py``'s
  running branch has no debounce, so N presses are N ``cancel()`` calls);
* the extension's ``api.add_cleanup`` teardown, for a session that exits with a
  child still registered.

Hence the directory path is recorded on the child's registry record, and every
one of those three paths calls the same idempotent remover.

AND A FOURTH OWNER, because those three all live inside the parent PROCESS
(P2 review, MEDIUM #5). None of them runs when the parent dies hard — SIGKILL,
OOM, a crash — and ADR-0197 §6.4 already treats a hard parent death as a real
scenario (it is the entire rationale for PDEATHSIG). Measured 4/4 on
parent-SIGKILL probes driving the real :class:`~aelix_agents.print_channel.PrintChannel`::

    leaked_tmpdirs=['/tmp/aelix-subagent-c75oxoxz']
    modes=['0o600'] contents=['SECRET-SYSTEM-PROMPT-BODY']

— the mode and the body exactly as designed, and the file simply never going
away. One per delegation, for the life of the box, since ``/tmp`` cleaners are
configuration-dependent.

So the directory NAME carries the creating process's pid
(``aelix-subagent-<pid>-<random>``) and :func:`sweep_stale_prompt_dirs` reclaims
every directory whose pid is gone. The extension calls it once per harness
build. Per-spawn directories are kept — the reviewer's "reuse ONE per-process
directory" would make concurrent delegations share a removable resource, so one
finishing run's cleanup would yank a live run's prompt file — the pid is stamped
into the name instead, which is all the sweep needs.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Anything outside this set is collapsed to "_". A profile name is validated at
# parse time (``agents/profile.py``), but the filename is derived DEFENSIVELY
# anyway: this string ends up in a path, and a name carrying ``../`` or a NUL
# must not be able to steer where the file lands.
_UNSAFE_NAME_RE = re.compile(r"[^\w.-]+")

_NAME_LIMIT = 64
"""Filename stem budget. Keeps the whole path far below ``PATH_MAX`` even under
a deeply nested ``TMPDIR``."""

_DIR_PREFIX = "aelix-subagent-"

_STALE_DIR_RE = re.compile(rf"^{re.escape(_DIR_PREFIX)}(\d+)-")
"""Matches a pid-stamped directory name and captures the creating pid.

Anchored and digit-only so a directory whose name merely CONTAINS digits — the
un-stamped ``aelix-subagent-<random>`` shape written by builds before this
change, and anything a third party happened to name similarly — never parses
into a pid and is therefore never swept."""


@dataclass(frozen=True)
class PromptFile:
    """One 0600 prompt file and the 0700 directory that owns it.

    :attr:`directory` is what gets removed — the file is never unlinked on its
    own, so a partially written spawn cannot leave an empty temp dir behind.
    """

    directory: Path
    path: Path


def _safe_stem(name: str) -> str:
    """``profile.name`` reduced to a filename-safe stem."""

    # ``strip("._")`` beyond the substitution: a name beginning with "." would
    # otherwise produce a HIDDEN file, which is a poor thing to leave behind if
    # cleanup ever fails and a poor thing to name in an error message.
    stem = _UNSAFE_NAME_RE.sub("_", name).strip("._")[:_NAME_LIMIT]
    # Every character was unsafe, or the name was empty. Never emit a bare
    # "prompt-.md" — the directory is already unique, but the stem should stay
    # self-describing in ``/proc/<pid>/cmdline`` and in a diagnostic.
    return stem or "agent"


def write_prompt_file(name: str, body: str) -> PromptFile:
    """Write ``body`` to a fresh 0600 file in a fresh 0700 directory.

    Takes ``(name, body)`` rather than an ``AgentProfile`` on purpose: this
    module has no reason to import the profile chain, and the caller already
    holds both values. ``body`` is the frontmatter-free profile body, exactly
    as ``parse_profile`` produced it.
    """

    # The pid is what makes :func:`sweep_stale_prompt_dirs` possible; ``mkdtemp``
    # still supplies the uniqueness, so two spawns from one process never
    # collide (MEDIUM #5).
    directory = Path(tempfile.mkdtemp(prefix=f"{_DIR_PREFIX}{os.getpid()}-"))
    path = directory / f"prompt-{_safe_stem(name)}.md"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
    except BaseException:
        # Never leak a half-written 0700 directory when the write itself fails
        # (a full disk, a cancellation landing between open and write).
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return PromptFile(directory=directory, path=path)


def remove_prompt_dir(prompt: PromptFile | None) -> None:
    """Remove the whole temp directory. Idempotent, never raises.

    Called from three independent paths (see the module docstring), so it must
    tolerate having already run, and it must never turn a cleanup into the
    reason a spawn reports failure.
    """

    if prompt is None:
        return
    shutil.rmtree(prompt.directory, ignore_errors=True)


def _pid_is_live(pid: int) -> bool:
    """Best-effort liveness. Errs toward LIVE, i.e. toward not deleting.

    ``os.kill(pid, 0)`` is the POSIX idiom and it is NOT one on Windows.
    ``CTRL_C_EVENT`` is 0, and CPython's ``os_kill_impl`` routes signal 0 to
    ``GenerateConsoleCtrlEvent`` rather than to any liveness check — so the
    "probe" delivers a real console Ctrl+C. Measured on a ``windows-latest``
    runner: ``os.kill(child.pid, 0)`` returned normally and the child, an
    otherwise idle ``time.sleep(30)``, was dead a second later. A predicate
    that asks whether a process is alive must not be the reason it stops
    being alive.

    This runs from ``sweep_stale_prompt_dirs`` on every delegation-parent
    startup, against pids read off directory names in the temp dir. Those pids
    are stale by definition and Windows recycles them, so on that platform the
    old form could interrupt an unrelated process that happened to inherit the
    number and shares our console.

    The Windows branch asks the kernel instead: a process object is signalled
    exactly when the process has exited, so ``WaitForSingleObject(h, 0)``
    returning ``WAIT_OBJECT_0`` means dead. ``ERROR_INVALID_PARAMETER`` from
    ``OpenProcess`` is "no such pid"; anything else (``ERROR_ACCESS_DENIED``
    for another user's process) keeps the errs-toward-LIVE contract.
    """

    if pid <= 0:
        return True
    if sys.platform == "win32":
        return _pid_is_live_win32(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Somebody else's process — alive, and not ours to sweep either way.
        return True
    except OSError:  # pragma: no cover — defensive
        return True
    return True


#: ``OpenProcess`` access mask that needs no privilege beyond "may I wait on
#: this object" — deliberately not ``PROCESS_QUERY_INFORMATION``, which a
#: protected or higher-integrity process would refuse.
_SYNCHRONIZE = 0x0010_0000
_WAIT_OBJECT_0 = 0x0000_0000
_ERROR_INVALID_PARAMETER = 87


def _pid_is_live_win32(pid: int) -> bool:
    """Liveness via the process object, never via a console control event."""

    import ctypes  # noqa: PLC0415 — windows-only, keep it off the POSIX path

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        # Only "no such pid" is proof of death. ACCESS_DENIED and friends mean
        # a process exists and is not ours — LIVE, per the contract above.
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        return kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def sweep_stale_prompt_dirs(root: str | os.PathLike[str] | None = None) -> list[str]:
    """Remove prompt directories left behind by aelix processes that are gone.

    THE FOURTH OWNER (MEDIUM #5). The other three all run inside the parent, so
    a hard parent death — SIGKILL, OOM, a crash — leaks the 0600 file holding the
    user's private agent system prompt into the system temp directory forever.
    This is the one that reclaims it, on the next aelix start.

    Three refusals, and each of them errs toward LEAVING a directory alone:

    * the name must be ``aelix-subagent-<digits>-…`` — an un-stamped directory
      from an older build, or anything else that happens to share the prefix,
      never parses and is never touched;
    * the pid must be dead. A recycled pid reads as live, so the directory
      survives to be swept by some later run — the safe direction;
    * it must not be OUR pid, and it must be owned by our uid. A directory we
      cannot have created is not ours to delete, and ``st_uid`` is checked with
      ``lstat`` so a symlink planted at the path cannot redirect the check.

    Returns the paths actually removed, for tests and diagnostics. Never raises:
    a sweep that cannot run is a leak that persists, not a startup that fails.
    """

    base = Path(root) if root is not None else Path(tempfile.gettempdir())
    me = os.getpid()
    try:
        uid = os.getuid()
    except AttributeError:  # pragma: no cover — non-POSIX
        uid = None
    removed: list[str] = []
    try:
        entries = list(base.iterdir())
    except OSError:
        return removed
    for entry in entries:
        match = _STALE_DIR_RE.match(entry.name)
        if match is None:
            continue
        try:
            pid = int(match.group(1))
        except ValueError:  # pragma: no cover — the regex guarantees digits
            continue
        if pid == me or _pid_is_live(pid):
            continue
        try:
            info = entry.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            continue
        if uid is not None and info.st_uid != uid:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(str(entry))
    return removed


@contextmanager
def prompt_file(name: str, body: str) -> Iterator[PromptFile]:
    """Scoped form of :func:`write_prompt_file`.

    Convenience for callers that do NOT need the registry hand-off — tests, and
    any future dry-run path. ``PrintChannel.run`` uses the explicit
    write/remove pair instead, because it has to hand the directory to the
    child registry before the child starts, so that a cancelled task's cleanup
    still has something to remove.
    """

    prompt = write_prompt_file(name, body)
    try:
        yield prompt
    finally:
        remove_prompt_dir(prompt)


__all__ = [
    "PromptFile",
    "prompt_file",
    "remove_prompt_dir",
    "sweep_stale_prompt_dirs",
    "write_prompt_file",
]
