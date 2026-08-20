"""The shipped selfhosted example closes the client it builds (#174).

WHY A TEST FOR AN EXAMPLE. ``examples/selfhosted/selfhosted.py`` is the
repository's worked answer to "how do I point aelix at an OpenAI-compatible
endpoint I run myself", and the extension-authoring guide reproduces it almost
line for line. Before this file it demonstrated the exact defect #174 is about:
a fresh ``AsyncOpenAI`` (wrapping a fresh ``httpx.AsyncClient``) per request,
handed to the built-in adapter through ``replace(opts, client=...)`` and never
closed. The built-in adapter will not close it for you — it only closes clients
it created itself, deliberately, so that an injected client survives for its
owner's next turn — so the example's own ``finally`` is the only thing that
releases it. An example that teaches the bug is worse than no example, and the
``finally`` that fixes it is one line that a later edit can drop in silence.
Hence a gate.

WHAT IS MEASURED, AND WHY NOT FILE DESCRIPTORS. #174's headline number ("FDs
3 -> 23 over 15 turns") is not reproducible: the per-request client is a
reference CYCLE, so the cyclic collector reclaims it on its own schedule and
five independent probes of the same build got five different FD deltas. Any
assertion on an FD count is a coin flip. This file asserts on two things that do
not sawtooth, inside a ``gc.disable()`` window so the collector cannot supply
the answer the code should have supplied:

  1. ``httpx.AsyncClient.is_closed`` on every client the example actually built,
     captured by subclassing the constructor rather than by patching the example.
  2. the connections the SERVER accepted whose peer has not gone away.

MEASURED, both arms, 8 turns each (this is the RED the fix was demonstrated
against — the pre-fix example from git HEAD, same probe):

    completed turns   fixed: 0 clients open, 0 established   unfixed: 8, 8
    aborted turns     fixed: 0 clients open, 0 established   unfixed: 8, 0

Note the second row's zero on the unfixed build: on the abort path the socket
does go away without help (the built-in adapter's own #147 ``finally`` releases
the response), and only the CLIENT survives. So reading (2) alone would call the
unfixed abort arm green. Both readings are asserted, and the abort test is
carried by (1).

CONSTRUCT-ONLY WOULD BE A FALSE GREEN for the connection half: building a client
without issuing a request costs zero connections and zero FDs — the cost is per
CONNECTION. That is why this drives a real request against a real socket instead
of instantiating the client and asserting on it.

THE CA BUNDLE ENV VAR. The example reads the ``verify=`` argument for its own
``httpx.AsyncClient`` from ``SELFHOSTED_CA_BUNDLE`` and falls back to system
trust when that is unset. The autouse fixture below scrubs the variable so this
gate does not depend on the developer's shell: httpx 0.28.1 raises
``FileNotFoundError`` AT CONSTRUCTION for a ``verify=`` path that does not
exist, which would take the whole file down before it measured anything. Unset,
the example constructs ``verify=True``, which costs nothing against the
plain-HTTP 127.0.0.1 server here and leaves the client count unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
from typing import Any

import httpx
import pytest
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.streaming import Context, Model, SimpleStreamOptions
from aelix_coding_agent.examples.selfhosted import selfhosted

#: Turns per arm. Small — the property is "zero survive", not "fewer survive",
#: so a big N buys nothing but wall clock.
N = 4

#: How long the server is given to notice a peer that went away. This is an
#: ABSENCE claim on the fixed build, where the close is synchronous and the
#: handler's ``readuntil`` raises within microseconds.
_SETTLE_S = 2.0

#: The variable the example reads its CA bundle path from.
_CA_BUNDLE_ENV = "SELFHOSTED_CA_BUNDLE"


@pytest.fixture(autouse=True)
def _unset_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the example to ``verify=True`` regardless of the developer's shell.

    See the module docstring: a ``SELFHOSTED_CA_BUNDLE`` left over in the
    environment and pointing at a file that is not there raises
    ``FileNotFoundError`` inside ``httpx.AsyncClient.__init__``, and this gate
    would error out before reaching a single assertion.
    """

    monkeypatch.delenv(_CA_BUNDLE_ENV, raising=False)


def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


#: One COMPLETE keep-alive completions turn. The terminating chunk matters: omit
#: it and every arm sits mid-stream and this file would be measuring #147
#: (stream lifetime) rather than #174 (client lifetime).
_BODY = [
    _sse(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {"content": "tok"}, "finish_reason": None}],
        }
    ),
    _sse(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    ),
    "data: [DONE]\n\n",
]


class _Server:
    """Serves one complete keep-alive turn per request and counts connections.

    The handler LOOPS over requests on the same socket instead of serving one and
    returning: a one-shot handler would close after the first turn and report a
    leaked client as a clean zero.
    """

    def __init__(self) -> None:
        self.accepted = 0
        self.gone = 0
        self.requests = 0
        # Without this list a FAILING case hangs in ``wait_closed()`` instead of
        # reporting RED — the #174 gate lost a 20-minute run to exactly that.
        self.writers: list[asyncio.StreamWriter] = []

    @property
    def established(self) -> int:
        return self.accepted - self.gone

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.accepted += 1
        self.writers.append(writer)
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":")[1])
                if length:
                    await reader.readexactly(length)
                self.requests += 1
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n"
                )
                for piece in _BODY:
                    raw = piece.encode()
                    writer.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
                writer.write(b"0\r\n\r\n")
                await writer.drain()
        except (
            asyncio.IncompleteReadError,
            ConnectionResetError,
            BrokenPipeError,
            asyncio.CancelledError,
        ):
            pass
        finally:
            self.gone += 1
            with contextlib.suppress(Exception):
                writer.close()


@contextlib.asynccontextmanager
async def _serving() -> Any:
    srv = _Server()
    server = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield srv, f"http://127.0.0.1:{port}/v1/gpt5mini"
    finally:
        for w in srv.writers:
            with contextlib.suppress(Exception):
                w.close()
        server.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), _SETTLE_S)


@contextlib.contextmanager
def _watching_httpx_clients() -> Any:
    """Capture every ``httpx.AsyncClient`` built while inside.

    Subclassing the constructor rather than patching the example: the thing under
    test stays the shipped source, byte for byte.
    """

    built: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    class _Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            built.append(self)

    httpx.AsyncClient = _Spy  # type: ignore[misc]
    try:
        yield built
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]


async def _settle(srv: _Server) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + _SETTLE_S
    while loop.time() < end and srv.established > 0:
        await asyncio.sleep(0.02)


def _model(base_url: str) -> Model:
    return Model(
        id="gpt5mini",
        name="t",
        provider="selfhosted",
        api="selfhosted-openai",
        base_url=base_url,
    )


def _context() -> Context:
    return Context(messages=[UserMessage(content=[TextContent(text="hi")])])


def _opts() -> SimpleStreamOptions:
    # ``max_retries=0``: the OpenAI SDK retries internally by default, which
    # would make the request count — and therefore the connection count —
    # something other than N.
    return SimpleStreamOptions(api_key="12345", max_retries=0)


async def test_the_example_closes_each_client_it_builds_over_completed_turns() -> None:
    """N completed turns leave zero live clients and zero live connections."""

    async with _serving() as (srv, base_url):
        with _watching_httpx_clients() as built:
            gc.disable()
            try:
                completed = 0
                for _ in range(N):
                    async for _event in selfhosted._selfhosted_stream(
                        _model(base_url), _context(), _opts()
                    ):
                        pass
                    completed += 1
                await _settle(srv)
            finally:
                gc.enable()

    # Guards the guard: if the example never reached the wire, every assertion
    # below would pass over nothing.
    assert completed == N, completed
    assert srv.requests == N, srv.requests
    assert len(built) == N, (
        f"expected one httpx client per turn, saw {len(built)} — the example no "
        "longer builds its own client, so this file is measuring nothing"
    )

    still_open = [c for c in built if not c.is_closed]
    assert not still_open, (
        f"{len(still_open)}/{N} clients the example built are still open after "
        "their turn completed. The built-in adapter does not close an injected "
        "client — the example's own `finally: await client.close()` is what "
        "releases it (#174)."
    )
    assert srv.established == 0, (
        f"{srv.established} connection(s) still established server-side after "
        f"{N} completed turns (accepted={srv.accepted}, gone={srv.gone})"
    )


async def test_the_example_closes_its_client_when_the_turn_is_aborted() -> None:
    """Abandoned mid-stream, the ``finally`` still runs under GeneratorExit.

    Asserted on the CLIENT, not the connection: measured, the unfixed example
    ends this arm with 8 live clients but zero live connections, because the
    built-in adapter's #147 ``finally`` already released the response. A
    connection-only assertion would call the leak green here.
    """

    async with _serving() as (srv, base_url):
        with _watching_httpx_clients() as built:
            gc.disable()
            try:
                aborted = 0
                for _ in range(N):
                    agen = selfhosted._selfhosted_stream(
                        _model(base_url), _context(), _opts()
                    )
                    async for _event in agen:
                        break  # abandon it parked at the stream_fn's own yield
                    await agen.aclose()
                    aborted += 1
                await _settle(srv)
            finally:
                gc.enable()

    assert aborted == N, aborted
    assert srv.requests == N, srv.requests
    assert len(built) == N, len(built)

    still_open = [c for c in built if not c.is_closed]
    assert not still_open, (
        f"{len(still_open)}/{N} clients survived an aborted turn — the "
        "example's `finally` did not run, or ran the wrong closer"
    )


def test_the_instrument_can_see_an_unclosed_client() -> None:
    """Positive control for ``_watching_httpx_clients`` + ``is_closed``.

    Without this, "0 clients still open" is indistinguishable from "the spy
    captured nothing", and the two tests above would stay green over an example
    that stopped building clients entirely.
    """

    with _watching_httpx_clients() as built:
        leaked = httpx.AsyncClient()
    assert built == [leaked]
    assert not leaked.is_closed, "an unclosed client must read as open"


@pytest.mark.parametrize("closer", ["close", "aclose"])
def test_asyncopenai_has_the_closer_the_example_uses(closer: str) -> None:
    """Pins the SDK fact the example's comment asserts.

    ``AsyncOpenAI`` has no ``aclose`` and its ``close`` is a coroutine — copying
    ``aclose()`` onto it (the spelling ``httpx.AsyncClient`` wants) raises
    ``AttributeError`` and, under a ``contextlib.suppress``, closes nothing at
    all while looking like cleanup. If a future SDK grows ``aclose``, this fails
    and the example's comment gets re-read rather than silently rotting.
    """

    import inspect

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key="x")
    if closer == "aclose":
        assert not hasattr(client, "aclose")
    else:
        assert inspect.iscoroutinefunction(client.close)
