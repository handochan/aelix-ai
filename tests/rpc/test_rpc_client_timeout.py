"""Pi parity: RpcClient ``send_timeout`` fires when the server stalls.

Spawns a stub server that reads commands but NEVER responds, so the
30 s default send timeout would fire — for the regression test we drop
the timeout to a few hundred milliseconds via a subclass override.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest
from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions

# Stub: read stdin lines, never write anything. Stays alive so the
# client must time out instead of seeing a premature EOF.
_BLACKHOLE_STUB = textwrap.dedent(
    """
    import sys
    import time

    for line in sys.stdin:
        # Read but never respond. Block forever afterwards if stdin closes.
        pass
    time.sleep(60)
    """
)


class _BlackholeClient(RpcClient):
    """Tiny send timeout so the timeout regression runs in <1 second."""

    DEFAULT_SEND_TIMEOUT_MS = 250
    DEFAULT_WAIT_FOR_IDLE_MS = 250

    def __init__(self) -> None:
        super().__init__(RpcClientOptions())

    def _build_argv(self) -> list[str]:
        return [sys.executable, "-c", _BLACKHOLE_STUB]


async def test_send_timeout_fires_when_server_stalls() -> None:
    client = _BlackholeClient()
    await client.start()
    try:
        with pytest.raises(asyncio.TimeoutError) as excinfo:
            await client.get_state()
        # Pi parity: timeout message references the command name.
        assert "get_state" in str(excinfo.value)
    finally:
        await client.stop()


async def test_wait_for_idle_timeout_when_no_agent_end() -> None:
    client = _BlackholeClient()
    await client.start()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await client.wait_for_idle(timeout_ms=200)
    finally:
        await client.stop()


async def test_pending_request_cleared_after_timeout() -> None:
    """After timeout, the pending_requests map should not leak."""

    client = _BlackholeClient()
    await client.start()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await client.get_state()
        assert client._pending_requests == {}
    finally:
        await client.stop()
