"""A local wire recorder for routing tests (#362): where did a request really go?

One ``ThreadingHTTPServer`` on 127.0.0.1 that plays two roles:

* as ``HTTPS_PROXY`` it records each ``CONNECT host:port`` and answers 403, so
  an https request to a real vendor is proven WITHOUT leaving the machine;
* as a plain endpoint (an extension's or ``OPENROUTER_BASE_URL``'s ``base_url``)
  it records ``(path, model)`` of each POST and answers a 400 naming itself.

Nothing is forwarded and no header value is kept. Shared by the real-spawn rows
of ``tests/agents_ext/test_child_provenance_362.py``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class WireRecorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connects: list[str] = []
        self.posts: list[tuple[str, str | None]] = []
        recorder = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:  # quiet
                return None

            def do_CONNECT(self) -> None:  # noqa: N802 — http.server API
                recorder.connects.append(self.path)
                self.send_response(403)
                self.send_header("content-length", "0")
                self.end_headers()
                self.close_connection = True

            def do_POST(self) -> None:  # noqa: N802 — http.server API
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    model = json.loads(raw or b"{}").get("model")
                except ValueError:
                    model = None
                recorder.posts.append((self.path, model))
                body = json.dumps({"error": {"message": f"LISTENER-{name}", "code": 400}}).encode()
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def hosts(self) -> list[str]:
        """The CONNECT targets, host only (``api.anthropic.com``)."""

        return sorted({target.rsplit(":", 1)[0] for target in self.connects})

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
