"""AbortSignal — Pi parity ``AbortSignal``/``AbortController`` idiom.

Pi uses the browser ``AbortSignal``/``AbortController`` pair to carry
cancellation intent across async boundaries.  Aelix replaces the web API
with an ``asyncio.Event`` following the convention established in
``extensions/ext_ui.py`` (line 59): callers await ``signal.wait()`` for
cancellation, and anything that wants to cancel calls ``signal.abort()``.

This module is the canonical carrier for **RPC-boundary abort intent**.
When an ``abort_bash`` RPC command arrives it runs *outside* any turn
task, so ``turn_task.cancel()`` (used by the Esc path) never reaches the
in-flight bash exec.  The :class:`AbortSignal` is registered on the
harness *before* the exec starts and is aborted by
:meth:`~aelix_agent_core.harness.core.AgentHarness.abort_bash`, which
fires ``signal.abort()`` on every registered signal — causing the watcher
task inside :meth:`~aelix_coding_agent.tools.bash._LocalBashOperations.exec`
to kill the subprocess group.
"""

from __future__ import annotations

import asyncio


class AbortSignal:
    """Asyncio-native abort signal (Pi parity ``AbortSignal``).

    Backed by an :class:`asyncio.Event` so the caller can ``await``
    cancellation without polling.  The signal is one-directional and
    one-shot: once :meth:`abort` is called, :attr:`aborted` stays
    ``True`` and :meth:`wait` returns immediately on every subsequent
    ``await``.
    """

    def __init__(self) -> None:
        self._event: asyncio.Event = asyncio.Event()

    @property
    def aborted(self) -> bool:
        """``True`` once :meth:`abort` has been called (Pi parity ``signal.aborted``)."""
        return self._event.is_set()

    def abort(self) -> None:
        """Fire the signal (Pi parity ``controller.abort()``)."""
        self._event.set()

    async def wait(self) -> None:
        """Await cancellation — returns immediately if already aborted."""
        await self._event.wait()


__all__ = ["AbortSignal"]
