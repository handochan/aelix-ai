"""#147's residual: does aborting actually tear the provider connection down?

The issue was left open on one unmeasured sentence — that the abort is
"cooperative … it does not forcibly tear down a socket the provider is holding
open, so the underlying request lingers until the transport gives up", needing
"a transport-level cancel (an httpx cancel scope / asyncio task cancellation
reaching the adapter)".

Measured, it does not linger. ``AgentHarness.abort`` already calls
``self._current_turn_task.cancel()`` (``harness/core.py:1516-1518``); the
``CancelledError`` lands in the adapter's ``async for`` over the SDK stream, no
adapter catches it (``except Exception`` does not catch a ``BaseException``),
and httpx closes an incompletely-read response as it unwinds. The socket goes
in single-digit milliseconds — including through a long-lived ``AsyncClient``
whose connection pool, not the cancelled task, owns the connection.

Nothing pinned that, so this file does. THE SERVER IS THE INSTRUMENT: it holds
the response body open and reports the moment the client's socket goes away.
Asserting on the harness instead would prove only that the harness stopped
listening, which is precisely the distinction the issue turned on.

``test_a_held_connection_survives_a_turn_that_is_not_aborted`` is the POSITIVE
CONTROL and is not optional. A reading of "torn down" means nothing from an
instrument that cannot produce "still open" — and this repo has already shipped
one ADR paragraph that was wrong because a live probe's own detector could not
tell "did not happen" from "did not match".
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.providers.openai_completions import stream_openai_completions
from aelix_ai.streaming import Model, SimpleStreamOptions

# One well-formed delta with no ``finish_reason``: enough for the adapter to be
# streaming, not enough for it to finish. The body then just never continues.
_CHUNK = (
    'data: {"id":"c","object":"chat.completion.chunk","created":0,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"tok"},"finish_reason":null}]}\n\n'
)


class _HoldingProvider:
    """Serves a partial SSE body, then holds the connection until the peer goes."""

    def __init__(self) -> None:
        self.requests = 0
        self.streaming = asyncio.Event()  # first chunk delivered, now holding
        self.peer_gone = asyncio.Event()
        # Teardown force-closes these. Without it the FAILING case hangs instead
        # of failing: when the abort does not reach the socket (the defect under
        # test), the handler sits in ``reader.read(1)`` forever and
        # ``server.wait_closed()`` waits for exactly that handler. Measured on a
        # sabotage run of the sibling providers gate, which reported nothing at
        # all for twenty minutes where it should have reported RED.
        self.writers: list[asyncio.StreamWriter] = []

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.writers.append(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            body_len = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    body_len = int(line.split(b":")[1])
            if body_len:
                await reader.readexactly(body_len)
            self.requests += 1
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            payload = _CHUNK.encode()
            writer.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            await writer.drain()
            self.streaming.set()
            # Hold. An empty read is the peer closing its end — the fact under test.
            if await reader.read(1) == b"":
                self.peer_gone.set()
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            # A reset is the peer going away just as violently. Same fact.
            self.peer_gone.set()
        finally:
            with contextlib.suppress(Exception):  # teardown only
                writer.close()


def _stream_fn(model: Model, context: Any, options: Any = None) -> Any:
    return stream_openai_completions(
        model, context, SimpleStreamOptions(api_key="sk-not-a-real-key")
    )


async def _drive(*, abort: bool) -> tuple[_HoldingProvider, bool, str]:
    """Run one turn against the holding provider.

    Returns ``(server, peer_gone, diagnosis)``. The third element exists because
    this file flaked ONCE inside a full-suite run on a heavily loaded box and
    could not be reproduced afterwards — 8/8 in isolation under 4x CPU
    oversubscription, and a clean full suite before and after. Rather than guess
    a cause and call it fixed, every state that could produce a false failure is
    now captured and printed by the assertion, so the next occurrence explains
    itself instead of costing another day.
    """

    provider = _HoldingProvider()
    server = await asyncio.start_server(provider.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(
                api="openai-completions",
                id="held-model",
                provider="openai",
                base_url=f"http://127.0.0.1:{port}/v1",
            ),
            stream_fn=_stream_fn,
        )
    )
    turn = asyncio.create_task(harness.prompt("hello"))
    try:
        await asyncio.wait_for(provider.streaming.wait(), timeout=20)
        if abort:
            await harness.abort()
        # Asymmetric on purpose — see the sibling providers gate. The abort arm
        # asserts PRESENCE and flaked at a short window under load; the control
        # asserts ABSENCE, which load cannot manufacture.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                provider.peer_gone.wait(), timeout=20.0 if abort else 2.5
            )
        exc = turn.exception() if turn.done() and not turn.cancelled() else None
        diagnosis = (
            f"requests={provider.requests} streaming={provider.streaming.is_set()} "
            f"turn_done_before_observation={turn.done()} "
            f"turn_cancelled={turn.cancelled()} turn_exc={exc!r} "
            f"connections_accepted={len(provider.writers)}"
        )
        return provider, provider.peer_gone.is_set(), diagnosis
    finally:
        if not turn.done():
            turn.cancel()
        # The abort path raises CancelledError, a BaseException.
        with contextlib.suppress(BaseException):
            await turn
        await harness.dispose()
        for open_writer in provider.writers:
            with contextlib.suppress(Exception):
                open_writer.close()
        server.close()
        await server.wait_closed()


async def test_aborting_a_turn_closes_the_connection_the_provider_is_holding() -> None:
    """#147 — the socket goes when the user aborts, it does not linger.

    SABOTAGE: remove the ``turn_task.cancel()`` block from
    ``AgentHarness.abort``. The harness still returns and the REPL still frees
    up — which is exactly why this had to be measured server-side — but the
    connection stays established and this fails.
    """

    provider, gone, diagnosis = await _drive(abort=True)
    assert provider.requests == 1, f"setup did not hold: {diagnosis}"
    assert gone, (
        "the provider is still holding a connection aelix walked away from — "
        f"{diagnosis}"
    )


async def test_a_held_connection_survives_a_turn_that_is_not_aborted() -> None:
    """POSITIVE CONTROL for the instrument above — do not delete as redundant.

    Same server, same held body, no abort. If this ever reports the peer gone,
    the detector is firing on something other than the abort and the sibling
    test proves nothing.
    """

    provider, gone, diagnosis = await _drive(abort=False)
    assert provider.requests == 1, f"setup did not hold: {diagnosis}"
    assert not gone, (
        f"the instrument reports a teardown with nothing to tear down — {diagnosis}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
