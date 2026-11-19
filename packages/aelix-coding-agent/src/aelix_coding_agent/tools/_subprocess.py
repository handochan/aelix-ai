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
the turn task is cancelled, and we forward it after ending the child's tree so
no orphan processes are left behind.

WHAT #222 CHANGED, AND WHY IT IS NOT A RENAME. The kill used to be
``kill_process_tree(proc.pid)``, which resolves the group with ``os.getpgid``
AT KILL TIME. At THIS site that moment is after asyncio's child watcher has
already ``waitpid``'d the child — measured in #202's review (posix2/F1), and
the consequence is written into ``kill_process_tree``'s own docstring — so the
number the kill addresses is re-derived from a pid that is no longer ours.
:class:`~aelix_ai.utils._process_tree.ProcessTree` captures the pgid (and on
win32 a Job Object) in the statement AFTER the spawn, while the child is
provably alive and provably ours, and every later kill dispatches on that
capture. Together with the job — Pi #9129: ``taskkill /T`` follows live parent
links only, so a member whose intermediate parent already exited survives it —
that is what the adoption buys here. ADR-0238 has the rest.

Parity notes vs. ``tools/bash.py``, which adopted the same primitive in the
same issue:

- Both spawn through ``containment_spawn_kwargs(new_session=True)`` — on POSIX
  the ``start_new_session=True`` this file always passed, on win32
  ``CREATE_NEW_PROCESS_GROUP`` plus the job the attach adds.
- Both pass ``stdin=DEVNULL`` (#828), as pi's grep and find tools spawn with
  ``stdio: ["ignore", "pipe", "pipe"]``. This file used not to: its only
  callers, ``grep._try_ripgrep`` and ``find._try_fd``, spawn a fixed argv that
  names a path, and ``rg`` given a path reads no stdin. Since #123 (ADR-0306
  D2) a delegated child's stdin is its parent's command inbox, and ``--mode
  rpc``'s is the RPC pipe: MEASURED on ``1c2a4a7c`` (macOS, ``rg`` from
  Homebrew), an ``rg`` spawned here with no path read the inbox line meant for
  aelix, and aelix read nothing. ``bash.py``'s reason is the TUI's keystrokes
  (#222's critique: the child read ``'secret\\n'``, the TUI's own ``read`` got
  ``b''``); this file's is the same pipe, whoever writes it.
- On ``TimeoutError``: end the tree, return ``None`` (matches the previous
  ``except subprocess.TimeoutExpired: return None`` branch in grep/find).
- On ``CancelledError``: end the tree, **re-raise** (so Esc unwinds the turn;
  no silent swallow).
- On ``FileNotFoundError`` (binary absent): return ``None`` (matches previous
  ``except FileNotFoundError: return None`` branch, activates Python fallback).
- On success: return ``(stdout_text, returncode)`` where ``stdout_text`` is the
  decoded stdout (UTF-8, replace errors) matching the old ``text=True`` stdout.
"""

from __future__ import annotations

import asyncio
import contextlib

from aelix_ai.utils._child_output import decode_child_output
from aelix_ai.utils._process_tree import (
    ProcessTree,
    _retained_handle,
    containment_spawn_kwargs,
)


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
        Optional wall-clock timeout in seconds.  When exceeded the child's
        tree is ended and ``None`` is returned (parity with the old
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
        child's tree is ended before re-raising so no orphan processes are
        left behind.
    """

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            **containment_spawn_kwargs(new_session=True),
        )
    except FileNotFoundError:
        return None

    # ATTACH IN THE VERY NEXT STATEMENT, with no ``await`` in between: on win32
    # only descendants created AFTER ``AssignProcessToJobObject`` inherit
    # membership (``ProcessTree``'s class docstring, ADR-0238). The window here
    # is structurally wider than ``bash.py``'s ``Popen`` one — the proactor
    # transport's pipe-connection round trips sit INSIDE the ``await`` above —
    # and is unmeasured; #222 §J item 2 is the measurement the windows leg owes.
    # No belt around this call: ``attach`` swallows ``OSError`` into
    # ``contained=False`` on win32 and past ``_require_pid`` cannot raise on
    # POSIX, and a spawn that returned cannot have handed us a pid <= 0.
    tree = ProcessTree.attach(proc.pid, handle=_retained_handle(proc))

    async def _communicate() -> tuple[bytes, bytes]:
        return await proc.communicate()

    def _kill_tree() -> None:
        # #105 — Windows has neither ``os.killpg`` nor ``signal.SIGKILL``, so
        # the body this replaced raised AttributeError out of the abort/timeout
        # handlers below. #222 — the group is now the tree captured above, which
        # on win32 is a job and therefore reaches a member ``taskkill /T`` walks
        # past. NOT the primitive's ``_end_the_tree``: that helper reaps with
        # ``Popen.wait(timeout)``, and the asyncio reap is the bounded
        # ``wait_for(proc.wait(), 2)`` each leg below already carried.
        tree.hard_kill()
        # The belt is not redundant: ``hard_kill`` is a silent no-op on a win32
        # host where the attach failed AND ``taskkill.exe`` does not resolve
        # (#202 review win-leg/F3). ``Process.kill`` raises ``ProcessLookupError``
        # once the transport has let go of an exited child, which at this site —
        # asyncio reaps behind us — is the ordinary case, not an error.
        #
        # ``OSError`` and not ``ProcessLookupError`` alone, matching
        # :func:`~aelix_ai.utils._process_tree._end_the_tree` (#222 review M-8):
        # ESRCH is the expected failure but not the only possible one, and a
        # ``PermissionError`` on a pid the OS has recycled would come out of the
        # ``except asyncio.CancelledError`` leg below IN PLACE OF the
        # cancellation that leg exists to re-raise.
        with contextlib.suppress(OSError):
            proc.kill()

    try:
        if timeout is not None:
            stdout_bytes, _ = await asyncio.wait_for(_communicate(), timeout=timeout)
        else:
            stdout_bytes, _ = await _communicate()
    except TimeoutError:
        # asyncio.wait_for raises TimeoutError (Python 3.11+) / asyncio.TimeoutError
        # (alias).  End the tree and signal "unavailable" to the caller so it
        # falls through to the Python fallback — matching the old TimeoutExpired
        # handling.
        _kill_tree()
        # Reap the child so the transport is closed and no zombie is left.
        # Guard with a short timeout so we never hang here (SIGKILL is
        # asynchronous; the kernel schedules the delivery).
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2)
        return None
    except asyncio.CancelledError:
        # The turn was cancelled (Esc / harness.abort()).  End the tree so no
        # orphan process is left, then re-raise so the cancellation propagates
        # through the tool-execute → turn_task chain.
        _kill_tree()
        # Reap the child before re-raising.  This deterministically closes the
        # asyncio transport regardless of the active child watcher or interpreter
        # version.  Guarded so the reap itself cannot hang or mask the
        # CancelledError we are about to re-raise.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2)
        raise
    finally:
        # A RELEASE, never a kill (``kill_on_close`` defaults to ``False``), and
        # the outermost step so no leg can skip the ``CloseHandle`` — including
        # the two above, whose ``return``/``raise`` run this first. After it the
        # tree signals nothing at all: ``hard_kill`` early-returns on ``closed``,
        # which is why nothing below this line may try to kill again.
        tree.close()

    # Intentional decode divergence from the old ``subprocess.run(text=True)``
    # behaviour: ``text=True`` uses ``locale.getpreferredencoding()`` with
    # ``errors='strict'`` and raises ``UnicodeDecodeError`` on invalid bytes
    # (e.g. binary rg matches).  Since #239 the answer is the shared decoder:
    # off win32 it is provably the old UTF-8/``replace`` call, and on win32 the
    # console output code page gets a strict try first, so ``rg``/``fd`` output
    # in a legacy page reads as text instead of U+FFFD.  A regression test in
    # ``tests/tools/test_subprocess_helper.py`` pins both halves.
    stdout_text = decode_child_output(stdout_bytes)
    return stdout_text, proc.returncode or 0


_PROTOCOL_STDIN = False
"""Whether this process's stdin carries a protocol (#828): see :func:`claim_stdin_for_protocol`."""


def claim_stdin_for_protocol() -> None:
    """Mark this process's stdin as a protocol's: ``--mode rpc``'s commands or a
    delegated child's command inbox (ADR-0306 D2).

    ``rpc_mode._open_stdin_reader`` calls it for both, the one place either is
    attached. From then on :func:`inherited_stdin` answers ``DEVNULL``.
    """

    global _PROTOCOL_STDIN
    _PROTOCOL_STDIN = True


def inherited_stdin() -> int | None:
    """The ``stdin`` for a child aelix starts on its own terminal (live output, never
    captured): ``None``, inherit, until the stdin carries a protocol, then ``DEVNULL``.

    Its one caller is the extension installer's ``_default_runner`` (pip or uv,
    run with aelix's stdio so its progress shows): run under ``--mode rpc`` or a
    command inbox, by an extension calling the Python API in process, it read the
    line meant for aelix (Codex review of #828, L13). Typed at a terminal
    (``aelix extension install``) it keeps the terminal, where pip may ask for an
    index's credentials.
    """

    import subprocess

    return subprocess.DEVNULL if _PROTOCOL_STDIN else None


__all__ = ["claim_stdin_for_protocol", "inherited_stdin", "run_cancellable"]
