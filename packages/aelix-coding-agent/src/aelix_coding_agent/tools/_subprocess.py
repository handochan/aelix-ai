"""Async cancellable subprocess helper — Pi parity cooperative-abort fix.

ROOT CAUSE: ``grep._try_ripgrep`` and ``find._try_fd`` previously called
``subprocess.run(..., timeout=30)`` — a BLOCKING synchronous call.  A blocking
call holds the GIL and parks the OS thread, so the asyncio event loop cannot
deliver ``CancelledError`` until the subprocess finishes.  This is exactly why
pressing Esc could not cancel an in-flight grep/find: ``harness.abort()`` calls
``turn_task.cancel()``, but the ``CancelledError`` checkpoint is never reached
while the process is running.

FIX: ``run_cancellable`` spawns via ``asyncio.create_subprocess_exec`` (fully
async I/O) and awaits ``proc.communicate()`` under ``asyncio.wait_for``.  The
event loop stays free throughout — ``CancelledError`` is delivered the moment
the turn task is cancelled, and we forward it after killing the child group so
no orphan processes are left behind.

Parity notes vs. bash.py:
- ``start_new_session=True`` puts the child in its own process group so
  ``os.killpg`` reaps all descendant processes, matching the bash tool's
  ``_kill_group`` pattern.
- On ``TimeoutError``: kill group, return ``None`` (matches the previous
  ``except subprocess.TimeoutExpired: return None`` branch in grep/find).
- On ``CancelledError``: kill group, **re-raise** (so Esc unwinds the turn;
  no silent swallow).
- On ``FileNotFoundError`` (binary absent): return ``None`` (matches previous
  ``except FileNotFoundError: return None`` branch, activates Python fallback).
- On success: return ``(stdout_text, returncode)`` where ``stdout_text`` is the
  decoded stdout (UTF-8, replace errors) matching the old ``text=True`` stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal


async def run_cancellable(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
) -> tuple[str, int] | None:
    """Spawn a subprocess and await it without blocking the event loop.

    Parameters
    ----------
    args:
        Command + arguments passed to ``asyncio.create_subprocess_exec``.
    cwd:
        Working directory for the child process (``None`` = inherit).
    timeout:
        Optional wall-clock timeout in seconds.  When exceeded the process
        group is killed and ``None`` is returned (parity with the old
        ``except subprocess.TimeoutExpired: return None`` handling).

    Returns
    -------
    ``(stdout_text, returncode)`` on success, or ``None`` when:
    - the binary is not found (``FileNotFoundError``), or
    - the timeout expires.

    Raises
    ------
    ``asyncio.CancelledError``
        When the calling asyncio task is cancelled (e.g. Esc → abort).  The
        child process group is killed before re-raising so no orphan processes
        are left behind.
    """

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )
    except FileNotFoundError:
        return None

    async def _communicate() -> tuple[bytes, bytes]:
        return await proc.communicate()

    def _kill_group() -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

    try:
        if timeout is not None:
            stdout_bytes, _ = await asyncio.wait_for(_communicate(), timeout=timeout)
        else:
            stdout_bytes, _ = await _communicate()
    except TimeoutError:
        # asyncio.wait_for raises TimeoutError (Python 3.11+) / asyncio.TimeoutError
        # (alias).  Kill the group and signal "unavailable" to the caller so it
        # falls through to the Python fallback — matching the old TimeoutExpired
        # handling.
        _kill_group()
        # Reap the child so the transport is closed and no zombie is left.
        # Guard with a short timeout so we never hang here (SIGKILL is
        # asynchronous; the kernel schedules the delivery).
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2)
        return None
    except asyncio.CancelledError:
        # The turn was cancelled (Esc / harness.abort()).  Kill the child group
        # so no orphan process is left, then re-raise so the cancellation
        # propagates through the tool-execute → turn_task chain.
        _kill_group()
        # Reap the child before re-raising.  This deterministically closes the
        # asyncio transport regardless of the active child watcher or interpreter
        # version.  Guarded so the reap itself cannot hang or mask the
        # CancelledError we are about to re-raise.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2)
        raise

    # Intentional decode divergence from the old ``subprocess.run(text=True)``
    # behaviour: ``text=True`` uses ``locale.getpreferredencoding()`` with
    # ``errors='strict'`` and raises ``UnicodeDecodeError`` on invalid bytes
    # (e.g. binary rg matches).  We use UTF-8 with ``errors='replace'`` which
    # silently substitutes U+FFFD — more robust and closer to Node's tolerant
    # Buffer decoding used by pi.  A regression test in
    # ``tests/tools/test_subprocess_helper.py`` pins the U+FFFD replacement
    # behaviour so any future change is explicit.
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    return stdout_text, proc.returncode or 0


__all__ = ["run_cancellable"]
