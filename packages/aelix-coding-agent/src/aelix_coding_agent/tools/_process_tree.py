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

EXPERIMENTAL: the win32 arm has no live coverage. See ``SLICE-STATUS.md``.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys

__all__ = ["kill_process_tree"]


def kill_process_tree(pid: int, *, platform: str | None = None) -> None:
    """Forcibly terminate ``pid`` and every process descended from it.

    Best-effort and never raises: an already-dead child is the goal state, not
    an error, and callers invoke this from abort/timeout paths where an
    exception would mask the original cause.

    ``platform`` defaults to :data:`sys.platform`; it is injected so the win32
    arm can be exercised from a POSIX box.
    """

    if (platform if platform is not None else sys.platform) == "win32":
        _taskkill_tree(pid)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)


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
