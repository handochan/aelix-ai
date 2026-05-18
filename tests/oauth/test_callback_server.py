"""Sprint 6c · Phase 4.3 — Local OAuth callback server tests.

We avoid the hard-coded Pi port (53692) so concurrent test runs (or
a developer with that port bound) don't collide. The ``start_callback_server``
public API accepts ``port=`` for exactly this reason.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from aelix_ai.oauth._callback_server import start_callback_server


def _free_port() -> int:
    """Reserve an ephemeral port the OS thinks is free, then release it.

    Tiny race window between close+rebind, but acceptable for tests.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_callback_server_serves_valid_code() -> None:
    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/callback?code=abc&state=expected-state"
            )
        assert response.status_code == 200
        assert "Authentication successful" in response.text
        # Future resolved.
        result = await asyncio.wait_for(server.wait_for_code(), timeout=1.0)
        assert result == ("abc", "expected-state")
    finally:
        server.shutdown()


async def test_callback_server_rejects_state_mismatch() -> None:
    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/callback?code=abc&state=WRONG"
            )
        assert response.status_code == 400
        assert "State mismatch" in response.text
        # Future remains pending (cancel_wait so we can drain it).
        server.cancel_wait()
        result = await asyncio.wait_for(server.wait_for_code(), timeout=1.0)
        assert result is None
    finally:
        server.shutdown()


async def test_callback_server_rejects_missing_code() -> None:
    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/callback?state=expected-state"
            )
        assert response.status_code == 400
        assert "Missing code or state parameter" in response.text
    finally:
        server.shutdown()


async def test_callback_server_returns_404_on_other_path() -> None:
    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{port}/unknown")
        assert response.status_code == 404
        assert "Callback route not found" in response.text
    finally:
        server.shutdown()


async def test_callback_server_returns_400_on_error_param() -> None:
    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{port}/callback?error=access_denied"
            )
        assert response.status_code == 400
        assert "access_denied" in response.text
    finally:
        server.shutdown()


async def test_cancel_wait_resolves_future_to_none() -> None:
    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        server.cancel_wait()
        result = await asyncio.wait_for(server.wait_for_code(), timeout=1.0)
        assert result is None
    finally:
        server.shutdown()


async def test_redirect_uri_uses_localhost() -> None:
    """Pi parity: ``REDIRECT_URI`` always uses ``localhost`` (not host)."""

    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        assert server.redirect_uri == f"http://localhost:{port}/callback"
    finally:
        server.shutdown()


async def test_shutdown_is_idempotent() -> None:
    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    server.shutdown()
    # No exception on second call.
    server.shutdown()


async def test_callback_server_state_mismatch_does_not_resolve_future() -> None:
    """A bad state must NOT resolve ``wait_for_code`` with the bad data."""

    port = _free_port()
    server = await start_callback_server(
        "expected-state", host="127.0.0.1", port=port
    )
    try:
        async with httpx.AsyncClient() as client:
            await client.get(
                f"http://127.0.0.1:{port}/callback?code=abc&state=WRONG"
            )
        # Future should still be pending; wait_for should time out.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(server.wait_for_code(), timeout=0.3)
    finally:
        server.shutdown()


# === W4 m8 — port-in-use friendly error ===


async def test_callback_server_port_in_use_friendly_error() -> None:
    """W4 m8: bind collision raises a ``RuntimeError`` that tells the
    user how to fix it, not a bare ``OSError``.
    """

    port = _free_port()
    # Bind the port ourselves so the OAuth server's bind will collide.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        with pytest.raises(RuntimeError) as ei:
            await start_callback_server("state", host="127.0.0.1", port=port)
        msg = str(ei.value)
        assert f"port {port}" in msg
        assert "in use" in msg
        assert "PI_OAUTH_CALLBACK_HOST" in msg
    finally:
        blocker.close()
