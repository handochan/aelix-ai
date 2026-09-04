"""Cross-platform process-tree termination (#105).

Both spawn sites in ``tools/`` (``bash.py`` and ``_subprocess.py``) reaped their
child by SIGKILLing its process group. On Windows that body does not merely
fail to reap — it raises. ``os.killpg`` is not defined off POSIX and
``signal.SIGKILL`` does not exist there either, so the abort (Esc) and timeout
paths would have died with ``AttributeError`` inside the very handler meant to
clean up, leaving the child running.

Nor is there a process group to aim at. ``subprocess`` silently IGNORES
``start_new_session=True`` on Windows — CPython's Windows ``_execute_child``
names the parameter ``unused_start_new_session`` — so the child is not a group
leader and ``proc.kill()`` would terminate it alone and orphan its descendants.
``taskkill /T /F`` walks the real parent/child tree, which is the property the
POSIX ``killpg`` was there for.

The win32 arm runs on the ``windows-latest`` leg — including the case that
deletes ``os.killpg``, ``os.getpgid`` and ``signal.SIGKILL``, which proves the
POSIX body is never reached there. The contained-runner regression also
exercises a real descendant holding an inherited stdout pipe, while the
``taskkill`` invocation remains unit-tested with a stub. See
``SLICE-STATUS.md``.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from typing import Any

__all__ = ["kill_process_tree", "run_contained"]


# A descendant can inherit stdout/stderr from the direct child.  After the
# direct child is killed, an unbounded ``communicate()`` can therefore wait for
# that descendant's pipe handle forever.  Every timeout cleanup below is
# explicitly bounded so a broken or partial containment mechanism cannot turn a
# timeout into a hang.
_REAP_TIMEOUT = 2.0


def run_contained(
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run a command with process-tree containment and bounded timeout cleanup.

    This is the synchronous counterpart to :func:`run_cancellable`.  It keeps
    the ``subprocess.run``-compatible result and exception behaviour needed by
    the synchronous extension catalog and TUI callers, while fixing the
    Windows timeout trap: ``subprocess.run(timeout=...)`` kills only the direct
    child and then performs an unbounded ``communicate()``.

    ``start_new_session`` gives POSIX a private process group.  Windows ignores
    that flag, so :func:`kill_process_tree` uses ``taskkill /T /F`` there.
    ``TimeoutExpired`` is re-raised after the tree has been asked to die and
    the direct child has had only a bounded reap window.
    """

    stdout_pipe = subprocess.PIPE if capture_output else None
    stderr_pipe = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=stdout_pipe,
        stderr=stderr_pipe,
        text=text,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Cleanup must never mask the original timeout.  kill_process_tree is
        # best-effort by contract; the suppress is also useful for unusual test
        # doubles and stripped environments.
        with contextlib.suppress(Exception):
            kill_process_tree(proc.pid)

        stdout = exc.output
        stderr = exc.stderr
        try:
            reaped_stdout, reaped_stderr = proc.communicate(timeout=_REAP_TIMEOUT)
        except subprocess.TimeoutExpired as reap_exc:
            # A descendant may still own a pipe even after the tree kill was
            # attempted.  Kill the direct child as a final bounded fallback,
            # reap its exit status without touching the pipes, and close our
            # handles so this function cannot wait indefinitely.
            if reap_exc.output is not None:
                stdout = reap_exc.output
            if reap_exc.stderr is not None:
                stderr = reap_exc.stderr
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=_REAP_TIMEOUT)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
        else:
            stdout = reaped_stdout if reaped_stdout is not None else stdout
            stderr = reaped_stderr if reaped_stderr is not None else stderr

        raise subprocess.TimeoutExpired(
            args, timeout if timeout is not None else 0.0, output=stdout, stderr=stderr
        ) from exc

    completed = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    if check:
        completed.check_returncode()
    return completed


def kill_process_tree(pid: int, *, platform: str | None = None) -> None:
    """Forcibly terminate ``pid`` and every process descended from it.

    Best-effort and never raises: an already-dead child is the goal state, not
    an error, and callers invoke this from abort/timeout paths where an
    exception would mask the original cause.

    ``platform`` defaults to :data:`sys.platform`; it is injected so the win32
    arm can be exercised from a POSIX box, and — since the windows leg exists —
    so the POSIX arm can be exercised from a Windows one, where the tests must
    lend the interpreter the three names it does not have.
    """

    if (platform if platform is not None else sys.platform) == "win32":
        _taskkill_tree(pid)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        # NOT reachable on Windows, and deliberately NOT guarded a second time.
        # The early return above is the only way here, and with no injected
        # ``platform`` — which is every production caller, ``bash.py`` and
        # ``_subprocess.py`` — it decides on ``sys.platform``. pyright cannot
        # narrow through that indirection, so the windows type gate reports all
        # three POSIX-only names on this line; the suppression below is about
        # the CHECKER. A runtime guard here would read to the next person as a
        # safety check and would be a lie:
        # ``test_win32_never_touches_killpg_or_sigkill`` deletes exactly these
        # three names and passes on the windows runner itself.
        os.killpg(os.getpgid(pid), signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]


def _taskkill_tree(pid: int) -> None:
    """``taskkill /T /F /PID`` — the Windows equivalent of a killpg."""

    # ``check=False``: taskkill exits non-zero when the PID is already gone,
    # which is success for us. ``OSError`` covers taskkill itself being absent
    # from a stripped image.
    with contextlib.suppress(OSError):
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
