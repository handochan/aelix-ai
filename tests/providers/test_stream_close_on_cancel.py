"""An aborted stream releases its connection without waiting for a GC pass (#147).

The sibling gate ``tests/harness/test_abort_tears_down_the_provider_socket.py``
covers the abort that lands while the adapter is parked in the NETWORK read;
httpcore handles that one for us (``_async/http11.py``: ``except BaseException``
→ ``aclose()`` under an ``AsyncShieldCancellation``).

This file covers the other half, which nothing handled: the abort that lands
while the adapter generator is parked at its OWN ``yield``, with the consumer
busy. WHICH CONSUMER — not today's TUI: ``tui/shell.py``'s ``_on_agent_event`` is
a plain ``def``, so while it renders, the loop is blocked and no cancellation can
be delivered at all. It is a consumer that AWAITS between events, which the
harness explicitly supports (``harness/core.py``: ``raw = listener(event)`` then
``if inspect.isawaitable(raw): await raw``) and which async extension hooks, the
``aelix_agents`` RPC channel and any SDK embedder use.

MEASURED on 7815cc6, ``openai-completions``, abort delivered at the yield: the
server still held the connection at the end of a 3 s window, and it only went
after an explicit ``gc.collect()``. That is "the underlying request lingers", the
sentence #147 was left open on, reached by a route the issue did not name.

THIS TEST NEVER CALLS ``gc.collect()``, and that is the whole point. Letting it
would turn the assertion into "the object is eventually reclaimable", which was
already true and is not what a user waiting at an idle prompt gets: CPython's
generational GC is driven by allocation counts, and an aborted TUI allocates
almost nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from aelix_ai.providers.openai_completions import stream_openai_completions
from aelix_ai.providers.openai_responses import stream_openai_responses
from aelix_ai.streaming import Context, Model, SimpleStreamOptions

_COMPLETIONS_BODY = [
    'data: {"id":"c","object":"chat.completion.chunk","created":0,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"tok"},"finish_reason":null}]}\n\n'
]
_RESPONSES_BODY = [
    "event: response.created\ndata: "
    + json.dumps(
        {
            "type": "response.created",
            "response": {"id": "resp_1", "status": "in_progress", "output": []},
        }
    )
    + "\n\n",
    "event: response.output_text.delta\ndata: "
    + json.dumps(
        {
            "type": "response.output_text.delta",
            "item_id": "m1",
            "output_index": 0,
            "content_index": 0,
            "delta": "tok",
        }
    )
    + "\n\n",
]


class _Server:
    """Streams a few events, then holds, and reports when the peer goes."""

    def __init__(self, body: list[str]) -> None:
        self._body = body
        self.peer_gone = asyncio.Event()
        # Every connection this server accepted. Teardown force-closes them.
        #
        # WITHOUT THIS THE FAILING CASE HANGS INSTEAD OF FAILING, which is worse
        # than no gate at all — measured, on the first sabotage run. When the
        # adapter does NOT release its socket (the defect under test), the
        # handler below sits in ``reader.read(1)`` forever, and
        # ``server.wait_closed()`` waits for exactly those handlers. So the one
        # run that was supposed to report RED reported nothing, for 20 minutes.
        self.writers: list[asyncio.StreamWriter] = []

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.writers.append(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
            if length:
                await reader.readexactly(length)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            for _ in range(4):
                for piece in self._body:
                    raw = piece.encode()
                    writer.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
            await writer.drain()
            if await reader.read(1) == b"":
                self.peer_gone.set()
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            self.peer_gone.set()
        finally:
            with contextlib.suppress(Exception):
                writer.close()


async def _park_then_cancel(api: str, *, cancel: bool) -> bool:
    """Park the consumer at the adapter's yield, optionally cancel, report EOF."""

    if api == "openai-completions":
        server_obj = _Server(_COMPLETIONS_BODY)
        stream_fn: Any = stream_openai_completions
    else:
        server_obj = _Server(_RESPONSES_BODY)
        stream_fn = stream_openai_responses

    server = await asyncio.start_server(server_obj.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    model = Model(
        api=api, id="m", provider="openai", base_url=f"http://127.0.0.1:{port}/v1"
    )
    parked = asyncio.Event()

    async def consume() -> None:
        async for _event in stream_fn(
            model, Context(), SimpleStreamOptions(api_key="sk-not-a-real-key")
        ):
            parked.set()
            await asyncio.sleep(3600)  # the generator is now suspended at its yield
        parked.set()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(parked.wait(), timeout=20)
        if cancel:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # No gc.collect() here. See the module docstring.
        #
        # The two arms get different windows on purpose. The cancel arm asserts
        # PRESENCE, so a long window only removes false failures — it flaked at
        # 2 s on a loaded box where the release itself takes under a millisecond.
        # The control asserts ABSENCE, where a long window would only slow the
        # suite down: load cannot make a connection close.
        window = 20.0 if cancel else 2.0
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server_obj.peer_gone.wait(), timeout=window)
        return server_obj.peer_gone.is_set()
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(BaseException):
            await task
        for open_writer in server_obj.writers:
            with contextlib.suppress(Exception):
                open_writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
async def test_cancelling_at_the_adapters_yield_releases_the_connection(
    api: str,
) -> None:
    """SABOTAGE: delete the ``finally: await close_provider_stream(open_stream)``
    from the adapter under test. The SDK stream is then released only when the
    object is collected, no collection happens inside this test, and the server
    still holds the connection when the window closes.
    """

    assert await _park_then_cancel(api, cancel=True), (
        f"{api} left the provider holding a connection after the abort"
    )


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
async def test_a_parked_stream_that_is_never_cancelled_keeps_its_connection(
    api: str,
) -> None:
    """POSITIVE CONTROL — do not delete as redundant.

    Same server, same parked consumer, no cancel. If this ever reports the peer
    gone, the detector is firing on something other than the cancellation and
    the sibling test above proves nothing.
    """

    assert not await _park_then_cancel(api, cancel=False), (
        f"{api}: the instrument reports a teardown with nothing to tear down"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
