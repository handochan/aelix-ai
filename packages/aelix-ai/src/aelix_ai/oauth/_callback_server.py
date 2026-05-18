"""Local HTTP callback server — Sprint 6c · Phase 4.3 · §E.

Pi parity: ``packages/ai/src/utils/oauth/anthropic.ts:97-167`` (SHA
734e08e) ``startCallbackServer``.

The Anthropic OAuth flow redirects the browser to
``http://localhost:53692/callback?code=...&state=...``. We bind a
stdlib :class:`http.server.HTTPServer` on a daemon thread, expose a
``wait_for_code`` awaitable that resolves to ``(code, state)`` (or
``None`` on cancel), and a ``shutdown`` closer.

The asyncio bridge uses :meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`
so the request handler (running on the server thread) can resolve the
asyncio future without crossing-thread mutations.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from aelix_ai.oauth._oauth_page import oauth_error_html, oauth_success_html


@dataclass
class CallbackServerInfo:
    """Pi parity: ``oauth/anthropic.ts:13-18`` ``CallbackServerInfo``.

    ``wait_for_code`` returns an ``asyncio.Future`` (created in the
    event-loop owning thread). The handler thread resolves it via
    ``loop.call_soon_threadsafe``.

    ``cancel_wait`` resolves the future to :data:`None` so the awaiter
    can break out without an HTTP roundtrip (used by Pi's
    ``onManualCodeInput`` race).

    ``shutdown`` shuts down the underlying HTTP server and joins the
    server thread — call in ``finally``.
    """

    redirect_uri: str
    wait_for_code: Callable[[], asyncio.Future[tuple[str, str] | None]]
    cancel_wait: Callable[[], None]
    shutdown: Callable[[], None]


async def start_callback_server(
    expected_state: str,
    *,
    host: str = "127.0.0.1",
    port: int = 53692,
    path: str = "/callback",
) -> CallbackServerInfo:
    """Start the OAuth callback server and return its info bundle.

    Pi parity: ``oauth/anthropic.ts:97-167`` ``startCallbackServer``.

    State validation: incoming ``state`` MUST equal ``expected_state``;
    otherwise serves 400 with ``oauth_error_html("State mismatch.")``.
    """

    # Sprint 6c W6 (W4 m3 / W5 P-99): ``asyncio.get_event_loop`` is
    # deprecated in 3.12+ and removed in 3.14+. Use ``get_running_loop``
    # which is contract-correct here (we're already inside an async
    # function, so there's always a running loop).
    loop = asyncio.get_running_loop()
    future: asyncio.Future[tuple[str, str] | None] = loop.create_future()

    def _settle(value: tuple[str, str] | None) -> None:
        """Resolve the future from the server thread (idempotent)."""

        def _set() -> None:
            if not future.done():
                future.set_result(value)

        loop.call_soon_threadsafe(_set)

    class _Handler(BaseHTTPRequestHandler):
        # Silence the default stderr access log (Pi server is similarly
        # silent — node's createServer prints nothing by default).
        # Sprint 6c W6 (W4 m4): parameter name MUST match the base class
        # ``log_message(self, format, *args)`` signature for Pyright to
        # accept the override. ``format`` shadows the builtin — suppress
        # with noqa per stdlib convention.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802 — stdlib API
            try:
                parsed = urlparse(self.path or "")
                if parsed.path != path:
                    self._respond_html(
                        404, oauth_error_html("Callback route not found.")
                    )
                    return

                params = parse_qs(parsed.query)
                code = (params.get("code") or [None])[0]
                state = (params.get("state") or [None])[0]
                error = (params.get("error") or [None])[0]

                if error:
                    self._respond_html(
                        400,
                        oauth_error_html(
                            "Anthropic authentication did not complete.",
                            f"Error: {error}",
                        ),
                    )
                    return

                if not code or not state:
                    self._respond_html(
                        400, oauth_error_html("Missing code or state parameter.")
                    )
                    return

                if state != expected_state:
                    self._respond_html(400, oauth_error_html("State mismatch."))
                    return

                self._respond_html(
                    200,
                    oauth_success_html(
                        "Anthropic authentication completed. "
                        "You can close this window."
                    ),
                )
                _settle((code, state))
            except Exception:  # noqa: BLE001
                # Pi parity: catch-all 500 (anthropic.ts:146-149).
                with contextlib.suppress(Exception):
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"Internal error")

        def _respond_html(self, status: int, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    # Sprint 6c W6 (W4 m8): friendlier diagnostic when the OAuth
    # callback port is already in use (e.g., another aelix CLI is
    # mid-login or someone is squatting on 53692).
    try:
        server = HTTPServer((host, port), _Handler)
    except OSError as exc:
        raise RuntimeError(
            f"OAuth callback port {port} is in use. "
            f"Set PI_OAUTH_CALLBACK_HOST or close the other process."
        ) from exc

    # Run server in a daemon thread (Pi's createServer.listen is
    # implicitly async — the JS event loop owns it. Python equivalent
    # is a daemon thread so the server dies with the parent if
    # ``shutdown`` is not called explicitly).
    thread = threading.Thread(
        target=server.serve_forever, name="aelix-oauth-cb", daemon=True
    )
    thread.start()

    def _cancel_wait() -> None:
        _settle(None)

    def _shutdown() -> None:
        # ``server.shutdown()`` stops ``serve_forever``; must be called
        # from a different thread than the server's.
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()
        thread.join(timeout=2.0)

    return CallbackServerInfo(
        redirect_uri=f"http://localhost:{port}{path}",
        wait_for_code=lambda: future,
        cancel_wait=_cancel_wait,
        shutdown=_shutdown,
    )


__all__ = ["CallbackServerInfo", "start_callback_server"]
