"""Pi parity: RpcClient shutdown — SIGTERM → 1s grace → SIGKILL.

Verifies the constants on the class, the stderr capture, and the
escalation behavior. We spawn a stub that ignores SIGTERM so the SIGKILL
escalation path is exercised.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import time

from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions


def test_rpc_client_default_constants_match_pi() -> None:
    """Pi parity invariants (rpc-client.ts:79, :107, :262, :332)."""

    assert RpcClient.DEFAULT_SEND_TIMEOUT_MS == 30_000
    assert RpcClient.DEFAULT_WAIT_FOR_IDLE_MS == 60_000
    assert RpcClient.STARTUP_GRACE_MS == 100
    assert RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS == 1_000


# Stub server: traps SIGTERM and stays alive until SIGKILL, emits a
# stderr breadcrumb so the regression can verify stderr capture works.
#
# TWO breadcrumbs, and the second one is the whole fix for a 40% flake. The
# stub is not armed against SIGTERM until ``signal.signal`` has run, and the
# 100 ms startup grace is not a readiness signal — measured over 10 runs, the
# child had not reached that line by the time ``stop()`` fired in 2 of them, so
# it died on the FIRST SIGTERM and the SIGKILL escalation the test claims to
# exercise never ran. The failing runs returned from ``stop()`` in ~110 ms; the
# passing ones took ~415 ms. Waiting for ``stub ready`` is what makes the test
# about the escalation instead of about the scheduler.
_SIGTERM_IGNORE_STUB = textwrap.dedent(
    """
    import signal
    import sys
    import time

    sys.stderr.write("stub starting\\n")
    sys.stderr.flush()

    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    sys.stderr.write("stub ready\\n")
    sys.stderr.flush()

    while True:
        time.sleep(0.5)
    """
)


async def _await_breadcrumb(client: RpcClient, needle: str) -> None:
    """Block until ``needle`` shows up on the child's stderr."""

    for _ in range(200):
        if needle in client.get_stderr():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"stub never wrote {needle!r}; got {client.get_stderr()!r}")


class _SigtermIgnoreClient(RpcClient):
    """Reduce the SIGTERM grace to keep the regression fast."""

    SHUTDOWN_SIGTERM_TIMEOUT_MS = 300

    def __init__(self) -> None:
        super().__init__(RpcClientOptions())

    def _build_argv(self) -> list[str]:
        return [sys.executable, "-c", _SIGTERM_IGNORE_STUB]


async def test_stop_escalates_to_sigkill_when_sigterm_ignored() -> None:
    """A server that ignores SIGTERM is killed via SIGKILL after the grace."""

    client = _SigtermIgnoreClient()
    await client.start()
    # The stub is only armed once ``signal.signal`` has run. Signalling before
    # that measures the scheduler, not the escalation.
    await _await_breadcrumb(client, "stub ready")

    started = time.monotonic()
    await client.stop()
    elapsed = time.monotonic() - started

    # THE SUBJECT OF THIS TEST IS THE ESCALATION, so assert it happened. The
    # previous assertion was a stderr substring, which would also have passed
    # had SIGTERM alone killed the child — i.e. in exactly the runs where the
    # escalation did NOT happen.
    grace = client.SHUTDOWN_SIGTERM_TIMEOUT_MS / 1000.0
    assert elapsed >= grace, (
        f"stop() returned in {elapsed:.3f}s, under the {grace:.3f}s SIGTERM "
        "grace — the child died on SIGTERM, so SIGKILL was never reached"
    )
    assert "stub starting" in client.get_stderr()


async def test_get_stderr_returns_captured_output() -> None:
    """``get_stderr()`` accumulates server stderr across the lifecycle."""

    client = _SigtermIgnoreClient()
    await client.start()
    try:
        await _await_breadcrumb(client, "stub starting")
    finally:
        await client.stop()


async def test_stop_is_idempotent() -> None:
    """Calling stop() on an already-stopped client is a no-op."""

    client = _SigtermIgnoreClient()
    await client.start()
    await client.stop()
    # Second stop should not raise.
    await client.stop()
