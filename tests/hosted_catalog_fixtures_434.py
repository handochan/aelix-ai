"""Shared pieces for #434's hosted catalog tests: a loopback Pages stand-in and revisions.

The server is a real HTTP server on ``127.0.0.1`` (the loopback ``http`` rule of
ADR-0264 §4.3 lets the client talk to it), so the client's real httpx path runs:
redirects, streaming, validators. Revisions are built the way the publisher
builds them (:func:`aelix_ai.hosted_catalog.build_revision` over this build's
shipped catalog), with a lineage that holds this build's digest unless a test
says otherwise.
"""

from __future__ import annotations

import copy
import http.server
import json
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_ai import hosted_catalog as hc
from aelix_ai.models import shipped_catalog, shipped_digest

INDEX_PATH = "/v1/index.json"

#: A row OpenRouter's shipped transport carries over to: the ladder inherits
#: ``openai-completions`` at openrouter.ai from the provider's own rows.
NEW_OR_ID = "acme/new-model-1"


def revision_path(name: str) -> str:
    return f"/v1/revisions/{name}.json"


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass
class CatalogServer:
    """``routes[path]`` answers GETs; ``log`` records each request's path and headers."""

    port: int = 0
    routes: dict[str, Response | Callable[[Mapping[str, str]], Response]] = field(default_factory=dict)
    log: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def requests(self, path: str) -> list[dict[str, str]]:
        return [headers for logged, headers in self.log if logged == path]

    def publish(
        self,
        *revisions: bytes,
        index: Mapping[str, Any] | None = None,
        etag: str | None = '"v1"',
    ) -> dict[str, str]:
        """Serve ``revisions`` and an index naming the first for ``0.1.0b2`` (or ``index``)."""

        names = {}
        for raw in revisions:
            name = hc.revision_name(raw)
            names[name] = name
            self.routes[revision_path(name)] = Response(200, raw)
        document = index if index is not None else index_for(hc.revision_name(revisions[0]))
        headers = {"ETag": etag} if etag else {}

        def _index(request_headers: Mapping[str, str]) -> Response:
            if etag and request_headers.get("If-None-Match") == etag:
                return Response(304)
            return Response(200, json.dumps(document).encode(), headers)

        self.routes[INDEX_PATH] = _index
        return names


def index_for(*entries: str | tuple[str, str], hold: str | None = None) -> dict[str, Any]:
    """An index document: ``name`` (for ``0.1.0b2``) or ``(minimum, name)`` entries."""

    catalogs = []
    for entry in entries:
        minimum, name = ("0.1.0b2", entry) if isinstance(entry, str) else entry
        catalogs.append({"minimumAelixVersion": minimum, "revision": name})
    document: dict[str, Any] = {"schema": hc.INDEX_SCHEMA, "catalogs": catalogs}
    if hold:
        document["hold"] = {"action": hold, "at": "2026-10-27T00:00:00Z"}
    return document


def revision(
    *,
    add: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    lineage: list[str] | None = None,
    edit: Callable[[dict[str, Any]], None] | None = None,
) -> bytes:
    """A revision of this build's catalog, as the publisher writes one.

    ``add[provider][id]`` is a new row's published facts (plus ``family``);
    ``lineage`` defaults to this build's digest followed by a newer one;
    ``edit`` may change the document before it is serialized.
    """

    catalog = copy.deepcopy({p: dict(rows) for p, rows in shipped_catalog().items()})
    inputs: dict[str, dict[str, dict[str, Any]]] = {}
    for provider, rows in (add or {}).items():
        for model_id, facts in rows.items():
            row = dict(facts)
            family = row.pop("family", None)
            row.setdefault("id", model_id)
            catalog[provider][model_id] = row
            if family:
                inputs.setdefault(provider, {})[model_id] = {"family": family}
    document = hc.build_revision(
        catalog, lineage if lineage is not None else [shipped_digest(), "f" * 32], inputs
    )
    if edit is not None:
        edit(document)
    return hc.serialize_revision(document)


def new_row(name: str = "Acme New Model 1", **extra: Any) -> dict[str, Any]:
    row = {
        "name": name,
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0.1, "output": 0.2, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 131072,
        "maxTokens": 8192,
        "family": "acme",
    }
    row.update(extra)
    return row


@pytest.fixture
def catalog_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[CatalogServer]:
    """A loopback catalog the client is pointed at (``AELIX_MODEL_CATALOG_URL``)."""

    server = CatalogServer()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            headers = {key: value for key, value in self.headers.items()}
            server.log.append((self.path, headers))
            route = server.routes.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            answer = route(headers) if callable(route) else route
            self.send_response(answer.status)
            for key, value in answer.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(answer.body)))
            self.end_headers()
            if answer.body:
                self.wfile.write(answer.body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib's
            return

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    monkeypatch.setenv("AELIX_MODEL_CATALOG_URL", server.base)
    try:
        yield server
    finally:
        httpd.shutdown()
        httpd.server_close()
