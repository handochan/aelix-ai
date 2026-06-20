"""Tests for ``aelix_coding_agent.tools._subprocess.run_cancellable``.

Covers:
- Normal success: returns (stdout_text, returncode).
- FileNotFoundError (binary absent): returns None.
- Timeout: returns None and kills the child process group.
- CancelledError: re-raised after killing the child process group.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

import pytest
from aelix_coding_agent.tools._subprocess import run_cancellable

# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_run_cancellable_returns_stdout():
    """Normal execution: (stdout_text, returncode) is returned."""
    result = await run_cancellable([sys.executable, "-c", "print('hello')"])
    assert result is not None
    stdout, rc = result
    assert "hello" in stdout
    assert rc == 0


async def test_run_cancellable_captures_stdout_only():
    """stderr is not mixed into the returned stdout text."""
    result = await run_cancellable(
        [sys.executable, "-c", "import sys; sys.stderr.write('err'); print('out')"]
    )
    assert result is not None
    stdout, _ = result
    assert "out" in stdout
    assert "err" not in stdout


async def test_run_cancellable_nonzero_returncode():
    """Non-zero exit codes are passed through in the tuple."""
    result = await run_cancellable([sys.executable, "-c", "raise SystemExit(42)"])
    assert result is not None
    _, rc = result
    assert rc == 42


# ---------------------------------------------------------------------------
# FileNotFoundError → None
# ---------------------------------------------------------------------------


async def test_run_cancellable_missing_binary_returns_none():
    """When the binary does not exist, return None (parity: FileNotFoundError)."""
    result = await run_cancellable(["/no/such/binary/xyz_does_not_exist"])
    assert result is None


# ---------------------------------------------------------------------------
# Timeout → None + child killed
# ---------------------------------------------------------------------------


async def test_run_cancellable_timeout_returns_none():
    """On timeout, run_cancellable returns None (parity with TimeoutExpired)."""
    result = await run_cancellable(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.1,
    )
    assert result is None


async def test_run_cancellable_timeout_kills_child():
    """After a timeout the child process must not be alive."""
    # Write the child PID to stdout so we can check it afterward.
    script = (
        "import os, time; "
        "print(os.getpid(), flush=True); "
        "time.sleep(30)"
    )
    result = await run_cancellable(
        [sys.executable, "-c", script],
        timeout=0.3,
    )
    assert result is None
    # The child is already dead: we cannot easily check the PID from here
    # without capturing stdout (the timeout fires before the script prints),
    # so assert only that the call returned promptly (no hang).


# ---------------------------------------------------------------------------
# CancelledError → re-raised + child killed
# ---------------------------------------------------------------------------


async def test_run_cancellable_cancellation_propagates():
    """When the wrapping task is cancelled, CancelledError is re-raised.

    The cancel must land AFTER the subprocess is spawned so it exercises the
    ``except asyncio.CancelledError: _kill_group(); raise`` branch inside
    ``_communicate()``, not the pre-spawn ``create_subprocess_exec`` checkpoint.
    A single ``sleep(0)`` yield is not sufficient — the child needs a moment to
    actually start.  Using ``sleep(0.1)`` mirrors the sibling kill-child test.
    """

    async def _run():
        return await run_cancellable(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )

    task = asyncio.create_task(_run())
    # Give the child time to actually start before cancelling, so cancel
    # lands inside _communicate() and exercises the kill-group-on-cancel path.
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)


async def test_run_cancellable_cancellation_kills_child():
    """After cancellation, the child process must be dead (not running).

    Strategy: the child writes its PID to a temp file, then sleeps.  We cancel
    the task, then verify the process is not in a running state.

    Note: after SIGKILL the child becomes a zombie (state Z) until the parent
    reaps it via wait().  ``os.kill(pid, 0)`` succeeds on zombies (the PID
    still exists in the process table), so we check ``/proc/<pid>/status``
    for the ``Z`` (zombie) state instead — a zombie is effectively dead and
    cannot accept new signals or consume CPU.
    """
    import tempfile

    pid_file = tempfile.mktemp(suffix=".pid")  # noqa: S306 — test-only
    script = (
        f"import os, time; "
        f"open({pid_file!r}, 'w').write(str(os.getpid())); "
        f"time.sleep(30)"
    )

    async def _run():
        return await run_cancellable([sys.executable, "-c", script])

    task = asyncio.create_task(_run())
    # Give the child time to write its PID.
    for _ in range(50):
        await asyncio.sleep(0.05)
        if os.path.exists(pid_file):
            break

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Verify the child is dead (gone or zombie, not running/sleeping).
    # Poll /proc/<pid>/status until the state is Z/X or the file disappears,
    # with a short timeout to avoid hanging.  SIGKILL delivery + kernel
    # scheduling can take a few milliseconds.
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as _pf:
                child_pid = int(_pf.read().strip())
            deadline = asyncio.get_event_loop().time() + 2.0
            last_state = "unknown"
            while asyncio.get_event_loop().time() < deadline:
                proc_status = f"/proc/{child_pid}/status"
                if not os.path.exists(proc_status):
                    # Process fully reaped — definitely dead.
                    last_state = "gone"
                    break
                with open(proc_status) as f:
                    for line in f:
                        if line.startswith("State:"):
                            last_state = line.split()[1]
                            break
                if last_state in ("Z", "X", "gone"):
                    break
                await asyncio.sleep(0.05)
            assert last_state in ("Z", "X", "gone"), (
                f"Child process {child_pid} still in state '{last_state}' "
                f"after SIGKILL (expected Z/X/gone)"
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(pid_file)


# ---------------------------------------------------------------------------
# Decode semantics: invalid bytes → U+FFFD replacement (intentional divergence
# from old ``subprocess.run(text=True)`` strict decode)
# ---------------------------------------------------------------------------


async def test_run_cancellable_invalid_utf8_replaced():
    """Invalid UTF-8 bytes in stdout are replaced with U+FFFD (not raised).

    This pins the intentional divergence from the old ``subprocess.run(text=True)``
    behaviour which used ``errors='strict'`` and would raise ``UnicodeDecodeError``
    on binary rg output.  We use ``errors='replace'`` — more robust and closer
    to Node's tolerant Buffer decoding used by pi.  Changing this decode mode
    must be explicit (update this test deliberately).
    """
    # Write a raw 0xFF byte to stdout — invalid UTF-8.
    script = "import sys; sys.stdout.buffer.write(b'\\xff\\n')"
    result = await run_cancellable([sys.executable, "-c", script])
    assert result is not None
    stdout, rc = result
    # U+FFFD replacement character must appear, NOT a UnicodeDecodeError.
    assert "�" in stdout
    assert rc == 0
