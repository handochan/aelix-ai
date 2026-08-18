"""Deterministic teardown for an open provider stream (#147).

WHY THIS EXISTS. An adapter that iterates the SDK's stream object with a bare
``async for`` — no ``async with``, no ``finally`` — releases the underlying
connection only when that object is collected. When the abort arrives while the
adapter is suspended at the NETWORK read, that is invisible: the
``CancelledError`` lands inside httpcore, and
``httpcore/_async/http11.py:330-342`` catches ``BaseException`` and closes the
socket under an ``AsyncShieldCancellation``, so the connection goes in under two
milliseconds with no help from us.

But an adapter is an async generator, and it spends part of its life suspended at
its OWN ``yield`` while the consumer works. Cancel the consumer in THAT state and
the ``CancelledError`` never reaches httpcore's handler at all: it unwinds the
consumer, the generator is closed later, and the socket's release waits for the
object to be reclaimed.

MEASURED (``openai-completions``, local server holding the body open, abort
delivered in each state):

    abort while parked in the network read   socket closed after    1.96 ms, 0 GC passes
    abort while parked at the adapter yield  socket OPEN for the full 3 s window,
                                             closed only after an explicit gc.collect()

The second row is exactly what #147 was left open on — "the underlying request
lingers until the transport gives up" — reached by a route the issue did not
name. Nor is it bounded the way that sentence assumes: once the generator is
abandoned there is no pending read, so the client's 600 s read timeout can never
fire. The connection does come back on the next collection, but CPython's
generational GC is driven by allocation counts and a process idling after an
abort allocates almost nothing.

WHICH CONSUMER CAN ACTUALLY PARK THERE — read this before assuming the TUI does.
It does not, today. ``tui/shell.py``'s subscriber ``_on_agent_event`` is a plain
``def``: while it burns its 226 ms of render work (ADR-0221) the event loop is
blocked, so nothing can be delivered, and by the time it returns the adapter is
back in the network read where httpcore handles the close. A first draft of this
module cited that 226 ms as the reason the state is reachable; it is closer to the
reason it is NOT.

The state is reached by a consumer that AWAITS between events, which the harness
explicitly supports — ``harness/core.py:2008`` does ``raw = listener(event)`` then
``if inspect.isawaitable(raw): await raw`` — and which async extension hooks, the
``aelix_agents`` RPC channel and any SDK embedder use. So this ``finally`` is not
closing a live TUI defect; it is closing the one every awaiting consumer already
has, and stopping the TUI from acquiring it the day a subscriber becomes async.

The fix is one ``finally`` per adapter that lacks a context manager. Adapters
that already own their stream through a context manager need nothing —
``anthropic-messages`` (``async with client.messages.stream(...)``) and
``openai-codex-responses`` (``async with _open_codex_stream(...)``, plus its own
``finally: await client.aclose()``) release the transport as they unwind. That is
READ from their source, not measured here.

"NEED NOTHING" IS SCOPED TO THE STREAM — #174 IS A SECOND, WIDER LEAK. That
sentence is about stream lifetime and it stays true. It is NOT a statement about
the CLIENT the stream was opened on, and reading it as one is the mistake #174
was filed against: ``anthropic-messages`` releases its stream through the
``async with`` at ``anthropic.py:595`` and still returns the connection to a
pool nobody ever closes. MEASURED on the unfixed build, 15 turns that COMPLETE
NORMALLY with no abort anywhere: anthropic ends with 15 live clients and 15
still-established server connections. The client-lifetime half is what
``close_provider_client`` below exists for.

WHAT #174 IS AND IS NOT. The issue's own framing — "never closed", "accumulates
one client per provider request for the life of the process" — did not survive
measurement and is not repeated here. A forced ``gc.collect()`` reclaims every
leaked client and every socket (15 live/15 open -> 0/0), because the per-request
client is a reference CYCLE, not an unreachable orphan. So this is a DETERMINISM
defect, not an FD-exhaustion defect: the release happens, on the collector's
schedule rather than the turn's. The issue's headline "FDs 3 -> 23 over 15 turns"
is likewise not a constant — with the cyclic collector running the count
sawtooths, and five independent probes got five different deltas. Any gate
written against a fixed FD number is a coin flip; the gate this shipped with
asserts on server-observed connections inside a ``gc.disable()`` window instead
(``tests/providers/test_client_lifetime_over_completed_turns.py``).

GOOGLE IS MEASURED, UNFIXED, AND DELIBERATELY UNTOUCHED. ``google-generative-ai``
and ``google-vertex`` have the same yield-parked linger — socket open for the
full observation window, no GC assist — and this helper does NOT close it. Two
handles were tried and MEASURED not to work, so do not re-try them without new
evidence:

  1. ``await chunk_iter.aclose()`` on the generator returned by
     ``client.aio.models.generate_content_stream``. ``google-genai 1.75.0``
     builds it in ``_api_client.async_request_streamed``, whose
     ``async for chunk in response`` sits in no ``finally``, so unwinding it
     releases nothing. An explicit ``aclose()`` in a standalone repro left the
     server holding the connection for the full 2 s window.
  2. ``await client.aio.aclose()`` on a client this adapter created. Returns
     cleanly, socket still does not go: ``_async_request_once`` has an aiohttp
     branch and an httpx branch, and the transport that owns the streaming
     response is not the one field that ``aclose`` closes.

Closing it needs the response object the SDK keeps private, which is a separate
piece of work rather than a third guess. A version of this module that wired both
google adapters was written, measured to change nothing, and reverted — shipping
it would have added fix-shaped code with a docstring claiming a fix it did not
deliver.

DO NOT READ HANDLE 2 ABOVE AS "GOOGLE CLIENTS CANNOT BE CLOSED" — it says the
STREAM's socket survives ``aio.aclose()``, and that is still true (#173). The
client's own connection POOL is a different object and it closes fine. MEASURED
on ``google-genai``: ``client.aio.aclose()`` flips
``_api_client._async_httpx_client.is_closed`` False -> True and is idempotent,
while the SYNC ``client.close()`` leaves it False. That sync/async split is a
live trap for anything that probes for a ``close`` attribute first, which is why
``close_provider_client`` below tries ``aio.aclose`` BEFORE ``close`` and why
``close_provider_stream`` must not be reused for clients — its ``("close",
"aclose")`` order would match google's sync ``close`` and return, leaving the
async pool open while looking like it did the work.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

__all__ = ["close_provider_client", "close_provider_stream"]


async def close_provider_stream(stream: Any) -> None:
    """Best-effort release of an SDK stream handle. Never raises.

    Accepts whatever the adapter got back from the SDK and tries, in order,
    ``close`` then ``aclose``; either may be sync or async. ``None`` and objects
    with neither method are no-ops, which is deliberate — the provider test
    fakes hand back plain async iterators (see
    ``tests/providers/test_openai_completions_streaming.py``), and this must not
    make a fake harder to write than the SDK it stands in for.

    NEVER RAISES, and that is not defensiveness for its own sake: this runs in
    an adapter's ``finally``, which on the abort path is executing during
    ``GeneratorExit``/``CancelledError`` unwinding. An exception raised there
    would replace the abort with a provider error, i.e. turn a clean cancel into
    a spurious "stream failed" in the transcript.
    """

    if stream is None:
        return
    for name in ("close", "aclose"):
        closer = getattr(stream, name, None)
        if closer is None:
            continue
        try:
            result = closer()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:  # noqa: BLE001 — see the docstring
            _log.debug("closing the provider stream raised: %r", exc, exc_info=True)
        return


async def close_provider_client(client: Any) -> None:
    """Best-effort release of an SDK CLIENT's connection pool. Never raises.

    The #174 counterpart to :func:`close_provider_stream`: that one hands back
    the response, this one hands back the pool the response was opened on. An
    adapter that closes only the stream still ends a normally-completed turn
    holding a live client, and the socket under it goes back on the collector's
    schedule rather than the turn's.

    ONLY EVER CALL THIS ON A CLIENT THE ADAPTER BUILT ITSELF. Every adapter
    guards the call with an ``owns_client`` flag so a caller-supplied
    ``options.client`` is never touched — closing an embedder's client out from
    under them would break their next turn, and this is the same line
    :func:`close_provider_stream` already draws. The flag lives at the call
    sites, not here, because only the call site knows where the client came
    from.

    RESOLUTION ORDER IS LOAD-BEARING AND MEASURED, not a defensive chain:

    ==========================  ============  ==========  =================
    client                      ``aclose``    ``close``   what closes it
    ==========================  ============  ==========  =================
    ``openai.AsyncOpenAI``      absent        coroutine   ``close()``
    ``anthropic.AsyncAnthropic``absent        coroutine   ``close()``
    ``httpx.AsyncClient``       coroutine     absent      ``aclose()``
    ``google.genai.Client``     absent(*)     **sync**    ``aio.aclose()``
    ==========================  ============  ==========  =================

    (*) ``aclose`` lives on ``client.aio``, not on the client.

    google is why ``aio.aclose`` is tried FIRST. Its ``Client`` exposes BOTH a
    synchronous ``close()`` and an async ``aio.aclose()``, and they close
    different transports: measured, ``client.close()`` leaves
    ``_api_client._async_httpx_client.is_closed`` at ``False`` while
    ``await client.aio.aclose()`` flips it to ``True`` (and is idempotent). A
    helper that probed for ``close`` first — as
    :func:`close_provider_stream` does — would call the sync one, return
    satisfied, and leave the async pool open. That is a silent half-fix, so the
    order is asserted in ``tests/providers/test_close_provider_client.py``
    against real SDK objects rather than doubles.

    ``None`` and objects with none of the three are no-ops, matching
    :func:`close_provider_stream`'s reasoning: the provider test fakes are not
    obliged to grow a ``close`` method to stay valid doubles.

    NEVER RAISES, for the same reason as :func:`close_provider_stream` — this
    runs in a ``finally`` that may be unwinding under
    ``GeneratorExit``/``CancelledError``, where a raise would replace a clean
    abort with a spurious provider error. Note the failure mode this catches in
    practice: ``aclose()`` copy-pasted onto an ``AsyncOpenAI`` raises
    ``AttributeError``, which a bare ``contextlib.suppress`` would turn into a
    silent non-close.
    """

    if client is None:
        return
    aio = getattr(client, "aio", None)
    for owner, name in ((aio, "aclose"), (client, "aclose"), (client, "close")):
        if owner is None:
            continue
        closer = getattr(owner, name, None)
        if closer is None:
            continue
        try:
            result = closer()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:  # noqa: BLE001 — see the docstring
            _log.debug("closing the provider client raised: %r", exc, exc_info=True)
        return
