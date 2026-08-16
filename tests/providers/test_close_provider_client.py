"""``close_provider_client`` resolves the right closer on every real SDK client (#174).

WHY THIS FILE EXISTS. ``close_provider_client`` looks like a defensive
``getattr`` chain and is not one. Its order — ``aio.aclose`` -> ``aclose`` ->
``close`` — was derived from the four client objects the adapters actually
build, and getting the order wrong is SILENT: the helper still returns, the
adapter's ``finally`` still runs, and the connection pool stays open. There is no
exception to catch and no log line to notice.

THE LOAD-BEARING CASE is ``google.genai.Client``, which exposes BOTH a
synchronous ``close()`` and an async ``aio.aclose()`` that close DIFFERENT
transports. Measured here, not asserted from the docs — see
``test_google_sync_close_and_async_aclose_close_different_transports``, which
pins the mirror image:

    close_provider_stream(google_client)  -> sync pool CLOSED, async pool OPEN
    close_provider_client(google_client)  -> sync pool OPEN,   async pool CLOSED

So the sibling ``close_provider_stream``, whose ``("close", "aclose")`` order
matches google's SYNC ``close`` first, would return satisfied having closed the
wrong transport. That is why the two helpers must stay two helpers, and that
test is the tripwire against a future reader "simplifying" them into one.

REAL SDK OBJECTS, NOT DOUBLES — deliberately. A double is written from the same
mental model as the helper, so it agrees with the helper by construction and
cannot catch the bug this file exists for. Every client below is the genuine
constructor with a throwaway API key; none of them touch the network (measured
in the #174 investigation: constructing a client costs ZERO file descriptors,
the cost is per CONNECTION). Each assertion pairs the post-close check with a
BEFORE check, so a client that arrived already-closed cannot fake a pass.

MEASURED SHAPES (installed versions: openai 1.109.1, anthropic 0.102.0,
httpx 0.28.1, google-genai 1.75.0):

    client                        aclose      close       what actually closes it
    openai.AsyncOpenAI            absent      coroutine   close()
    anthropic.AsyncAnthropic      absent      coroutine   close()
    httpx.AsyncClient             coroutine   absent      aclose()
    google.genai.Client           absent(*)   **sync**    aio.aclose()

    (*) ``aclose`` lives on ``client.aio``, not on the client.

The shape assertions are not decoration: if a future SDK bump grows an
``AsyncOpenAI.aclose``, the resolution order changes meaning and this file says
so instead of silently exercising a different branch.

ON THE GOOGLE IMPORT. ``google-genai`` is a HARD, non-optional dependency of
``aelix-ai`` (``packages/aelix-ai/pyproject.toml``: ``google-genai>=1.52,<2`` in
``[project].dependencies``, no extra), and the existing
``tests/providers/test_google_client.py`` imports it unguarded. It is therefore
NOT wrapped in ``pytest.importorskip`` here: a skip would silently delete the
load-bearing test above, which is the whole reason the file exists. The import
is done inside each google test rather than at module scope only so that a
broken google install fails those tests loudly while still letting the
openai/anthropic/httpx rows run.

NOT COVERED HERE, on purpose: the ``owns_client`` guard. That flag lives at the
five adapter call sites, not in the helper — only the call site knows whether it
built the client or the caller handed one in via ``options.client`` — so it is
gated by the adapter tests, not by this file.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx
import openai
from aelix_ai.providers._stream_close import close_provider_client, close_provider_stream


def _google_client() -> Any:
    """A real ``google.genai.Client`` with its async surface materialised.

    Touching ``client.aio.models`` forces the async client into existence before
    we look at the transport under it. Measured on google-genai 1.75.0 the
    ``BaseApiClient`` already builds ``_async_httpx_client`` eagerly at
    construction, so the touch is currently a no-op — it stays because the
    assertion below reads a private attribute whose eagerness is not a promise,
    and a lazily-built pool would otherwise make this test assert on an object
    that never existed.
    """

    from google import genai

    client = genai.Client(api_key="x")
    _ = client.aio.models
    return client


def _google_pools(client: Any) -> tuple[Any, Any]:
    """``(sync_httpx_client, async_httpx_client)`` under a genai client."""

    api_client = client._api_client
    return api_client._httpx_client, api_client._async_httpx_client


# === The resolution matrix, one row per real SDK client ===


async def test_openai_async_client_closes_via_its_coroutine_close() -> None:
    client = openai.AsyncOpenAI(api_key="x")

    # The shape the resolution order depends on: no ``aclose`` anywhere, and
    # ``close`` is the coroutine. This is why ``close`` has to stay in the chain
    # at all — dropping it to "async clients use aclose" would close nothing.
    assert getattr(client, "aclose", None) is None
    assert getattr(client, "aio", None) is None
    assert callable(client.close)

    assert client.is_closed() is False  # positive control: not already closed
    await close_provider_client(client)
    assert client.is_closed() is True


async def test_anthropic_async_client_closes_via_its_coroutine_close() -> None:
    client = anthropic.AsyncAnthropic(api_key="x")

    assert getattr(client, "aclose", None) is None
    assert getattr(client, "aio", None) is None
    assert callable(client.close)

    assert client.is_closed() is False
    await close_provider_client(client)
    assert client.is_closed() is True


async def test_httpx_async_client_closes_via_aclose() -> None:
    client = httpx.AsyncClient()

    # The mirror of the two rows above: httpx has ``aclose`` and NO ``close``.
    # Between them these three rows show the chain needs both names.
    assert callable(client.aclose)
    assert getattr(client, "close", None) is None

    assert client.is_closed is False
    await close_provider_client(client)
    assert client.is_closed is True


async def test_google_client_closes_its_async_connection_pool() -> None:
    client = _google_client()
    _sync_pool, async_pool = _google_pools(client)

    assert async_pool.is_closed is False
    await close_provider_client(client)
    assert async_pool.is_closed is True


# === THE LOAD-BEARING TEST — why there are two helpers and not one ===


async def test_google_sync_close_and_async_aclose_close_different_transports() -> None:
    """``close_provider_stream`` closes the WRONG google transport. Pinned.

    ``google.genai.Client`` is the only client in the matrix that answers to
    both names, and the two names are not synonyms: ``close()`` is synchronous
    and takes down ``_api_client._httpx_client``, while ``await
    client.aio.aclose()`` takes down ``_api_client._async_httpx_client``. The
    adapters stream over the ASYNC one.

    ``close_provider_stream`` probes ``("close", "aclose")`` in that order, so on
    a google client it matches the SYNC ``close`` on the first try and returns.
    It does not fail, log, or raise — it does real work on the wrong object and
    leaves the async pool open. That failure mode is invisible to any assertion
    of the form "the helper ran without error", which is why this test asserts
    on the transports directly.

    Both helpers are exercised on FRESH clients and both directions are
    asserted, so this is a discrimination, not a one-sided claim: each helper is
    shown to close exactly one pool and leave the other open.

    WHAT THIS ACTUALLY CATCHES, sabotage-measured rather than claimed:

      close_provider_client's tuple reordered to close-first  -> 3 tests RED
      close_provider_client delegated to close_provider_stream -> 3 tests RED
      close_provider_stream delegated to close_provider_client -> this test RED
                                                                  (the sync-pool
                                                                  control below)

    And what it does NOT catch, so nobody reads more into it: reordering
    ``close_provider_stream``'s OWN ``("close", "aclose")`` tuple leaves all nine
    tests green. A google client carries no direct ``aclose`` — its async closer
    hangs off ``.aio`` — so both orders fall through to the same sync ``close``,
    and no real SDK client in the matrix can tell those two orders apart. That
    tuple's order is the #147 stream contract and belongs to
    ``tests/providers/test_stream_close_on_cancel.py``, not here.
    """

    # --- the sibling helper: closes the sync pool, misses the async one ---
    via_stream = _google_client()
    sync_pool, async_pool = _google_pools(via_stream)
    assert sync_pool.is_closed is False
    assert async_pool.is_closed is False

    await close_provider_stream(via_stream)

    assert sync_pool.is_closed is True, (
        "close_provider_stream must actually have called google's sync close() — "
        "if this is False the test below proves nothing, because a helper that "
        "silently did NOTHING would also leave the async pool open"
    )
    assert async_pool.is_closed is False, (
        "close_provider_stream must NOT be able to close a client's async pool; "
        "if it can, the premise for a separate close_provider_client is gone"
    )

    # --- the client helper: closes the async pool, and stops at the first match ---
    via_client = _google_client()
    sync_pool, async_pool = _google_pools(via_client)
    assert sync_pool.is_closed is False
    assert async_pool.is_closed is False

    await close_provider_client(via_client)

    assert async_pool.is_closed is True
    assert sync_pool.is_closed is False, (
        "close_provider_client returns after the FIRST resolved closer, so it "
        "never reaches google's sync close(); that the sync pool is still open "
        "is the direct evidence that aio.aclose was tried before close"
    )

    # Tidy up the halves each helper left behind.
    await close_provider_client(via_stream)
    via_client.close()


async def test_google_close_is_idempotent() -> None:
    """A second call must not raise — adapters may close on both the normal and
    the error path, and #174's fix lands in a ``finally``."""

    client = _google_client()
    _sync_pool, async_pool = _google_pools(client)

    await close_provider_client(client)
    assert async_pool.is_closed is True

    await close_provider_client(client)
    assert async_pool.is_closed is True


# === No-ops: the helper must not make a valid test double harder to write ===


async def test_none_is_a_noop() -> None:
    assert await close_provider_client(None) is None


class _FakeAsyncOpenAI:
    """The shape the provider tests actually pass as ``options.client``.

    Modelled on ``tests/providers/test_openai_completions_streaming.py``'s
    ``_FakeAsyncOpenAI``: a plain object with the one attribute the adapter
    reaches for and none of ``aio`` / ``aclose`` / ``close``. If the helper ever
    grew a hard requirement for a closer, every one of those doubles would have
    to grow a no-op method to stay valid.
    """

    def __init__(self) -> None:
        self.chat = object()


class _PlainAsyncIterator:
    """The other double shape in the provider suite — no closers either."""

    def __aiter__(self) -> _PlainAsyncIterator:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


async def test_object_with_no_closer_at_all_is_a_noop() -> None:
    for double in (_FakeAsyncOpenAI(), _PlainAsyncIterator()):
        assert getattr(double, "aio", None) is None
        assert getattr(double, "aclose", None) is None
        assert getattr(double, "close", None) is None
        assert await close_provider_client(double) is None


# === Never raises — this runs in a ``finally`` that may be unwinding ===


async def test_a_closer_that_raises_does_not_propagate() -> None:
    """The concrete case the docstring names: ``aclose()`` on an ``AsyncOpenAI``.

    An adapter author who copy-pastes ``await client.aclose()`` from the httpx
    row of the matrix onto an ``AsyncOpenAI`` gets an ``AttributeError`` — the
    error text below is the real one, captured from the real client a few lines
    up rather than invented. This helper runs inside a ``finally`` that on the
    abort path is unwinding under ``GeneratorExit``/``CancelledError``, where a
    raise would replace a clean cancel with a spurious provider error in the
    transcript. So a closer that blows up must be swallowed.
    """

    probe = openai.AsyncOpenAI(api_key="x")
    try:
        await probe.aclose()  # type: ignore[attr-defined]
    except AttributeError as exc:
        real_error = str(exc)
    else:  # pragma: no cover — would mean the SDK grew an aclose
        raise AssertionError("AsyncOpenAI unexpectedly has an aclose(); update the matrix")
    assert real_error == "'AsyncOpenAI' object has no attribute 'aclose'"
    await close_provider_client(probe)

    client = openai.AsyncOpenAI(api_key="x")
    original_close = client.close
    calls: list[str] = []

    async def _exploding_close() -> None:
        calls.append("close")
        raise AttributeError(real_error)

    client.close = _exploding_close  # type: ignore[method-assign]

    assert await close_provider_client(client) is None
    # Positive control. Without this, "did not raise" would also pass if the
    # helper had resolved no closer at all and quietly returned.
    assert calls == ["close"]
    assert client.is_closed() is False  # the exploding closer really did nothing

    await original_close()
