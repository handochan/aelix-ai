"""Pi parity: RpcClient shutdown — SIGTERM → 1s grace → SIGKILL.

Verifies the constants on the class, the stderr capture, and the
escalation behavior. We spawn a stub that ignores SIGTERM so the SIGKILL
escalation path is exercised.
"""

from __future__ import annotations

import sys
import textwrap

from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions


def test_rpc_client_default_constants_match_pi() -> None:
    """Pi parity invariants (rpc-client.ts:79, :107, :262, :332)."""

    assert RpcClient.DEFAULT_SEND_TIMEOUT_MS == 30_000
    assert RpcClient.DEFAULT_WAIT_FOR_IDLE_MS == 60_000
    assert RpcClient.STARTUP_GRACE_MS == 100
    assert RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS == 1_000


# Stub server: traps SIGTERM and stays alive until SIGKILL, emits a
# stderr breadcrumb so the regression can verify stderr capture works.
_SIGTERM_IGNORE_STUB = textwrap.dedent(
    """
    import signal
    import sys
    import time

    sys.stderr.write("stub starting\\n")
    sys.stderr.flush()

    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    while True:
        time.sleep(0.5)
    """
)


class _SigtermIgnoreClient(RpcClient):
    """Reduce the SIGTERM grace to keep the regression fast."""

    SHUTDOWN_SIGTERM_TIMEOUT_MS = 300

    def __init__(self) -> None:
        super().__init__(RpcClientOptions())

    def _build_argv(self) -> list[str]:
        return [sys.executable, "-c", _SIGTERM_IGNORE_STUB]


async def test_stop_escalates_to_sigkill_when_sigterm_ignored() -> None:
    """A server that ignores SIGTERM is killed via SIGKILL after 1 s."""

    client = _SigtermIgnoreClient()
    await client.start()
    # Server is alive after grace period; stop() should escalate to SIGKILL.
    await client.stop()
    # If we got here without hanging, SIGKILL fired. Stderr breadcrumb
    # should also be captured.
    assert "stub starting" in client.get_stderr()


async def test_get_stderr_returns_captured_output() -> None:
    """``get_stderr()`` accumulates server stderr across the lifecycle."""

    client = _SigtermIgnoreClient()
    await client.start()
    try:
        # The stub writes ``stub starting`` to stderr at boot; with a
        # small await loop we let the reader drain it.
        import asyncio

        for _ in range(20):
            if "stub starting" in client.get_stderr():
                break
            await asyncio.sleep(0.05)
        assert "stub starting" in client.get_stderr()
    finally:
        await client.stop()


async def test_stop_is_idempotent() -> None:
    """Calling stop() on an already-stopped client is a no-op."""

    client = _SigtermIgnoreClient()
    await client.start()
    await client.stop()
    # Second stop should not raise.
    await client.stop()
