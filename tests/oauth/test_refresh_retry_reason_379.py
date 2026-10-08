"""#379 — which failed OAuth refreshes are transient (ADR-0251 §4, the owner's decision).

The owner decided on 2026-10-06 to retry a stored OAuth refresh as pi does: a
token endpoint that answered a status pi's retry pattern names (``429``,
``500``, ``502``, ``503``, ``504``, ``520``, ``524`` -
``packages/ai/src/utils/retry.ts:30-45`` @ 1cedd3272; review round 2 made the
set exactly pi's), or could not be reached, is retried by the turn;
``400``/``401``/``403`` (``invalid_grant``, a revoked login), ``501``, ``505``
and every other failure fail at once. pi decides on the message text
(``isRetryableAssistantError``, ``packages/ai/src/utils/retry.ts:252`` @
1cedd3272); aelix decides on the exception chain
(:func:`aelix_ai.oauth._helpers.refresh_retry_reason`), so the server's words
decide nothing. These rows pin the classifier and that each built-in refresh
(Codex, Anthropic, Copilot) raises with the status as data.

Hermetic: ``httpx.MockTransport``, fake tokens, no socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from aelix_ai.oauth import anthropic, github_copilot, openai_codex
from aelix_ai.oauth._helpers import StatusBeforeBody, oauth_http_error, refresh_retry_reason
from aelix_ai.oauth.auth_storage import OAuthRefreshError, StoredCredentialError

_REAL_ASYNC_CLIENT = httpx.AsyncClient
_REQ = httpx.Request("POST", "https://auth.example.invalid/oauth/token")


def _chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


# === the classifier ==========================================================


#: pi's ``RETRYABLE_PROVIDER_ERROR_PATTERN`` statuses (``utils/retry.ts:38-44`` @ 1cedd3272).
_PI_STATUSES = [429, 500, 502, 503, 504, 520, 524]


@pytest.mark.parametrize("status", _PI_STATUSES)
def test_each_status_pis_pattern_names_is_transient(status: int) -> None:
    reason = refresh_retry_reason(oauth_http_error(f"failed ({status})", status))
    assert reason == f"the token endpoint answered HTTP {status}"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 418, 501, 505, 507, 521, 530, 599])
def test_any_other_answer_is_not(status: int) -> None:
    """``501`` (review round 2's Codex mutant), ``505`` and the other ``5xx`` pi does not name."""

    assert refresh_retry_reason(oauth_http_error(f"failed ({status})", status)) is None


@pytest.mark.parametrize(("status", "transient"), [(524, True), (501, False), (505, False)])
def test_an_extensions_raise_for_status_uses_the_same_set(status: int, transient: bool) -> None:
    response = httpx.Response(status, request=_REQ)
    exc = httpx.HTTPStatusError("x", request=_REQ, response=response)
    assert (refresh_retry_reason(exc) is not None) is transient


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("All connection attempts failed", request=_REQ),
        httpx.ConnectTimeout("timed out", request=_REQ),
        httpx.ReadTimeout("", request=_REQ),
        httpx.PoolTimeout("", request=_REQ),
        # Review round 4 (Codex mutant): the whole TimeoutException family, so a set that
        # names ConnectTimeout, ReadTimeout and PoolTimeout alone is red.
        httpx.WriteTimeout("write timed out", request=_REQ),
        httpx.ReadError("", request=_REQ),
        # Review round 3 (Codex cat4): the rest of httpx.NetworkError, so a set narrowed to
        # ConnectError + ReadError is red.
        httpx.WriteError("Connection reset by peer", request=_REQ),
        httpx.CloseError("", request=_REQ),
        httpx.RemoteProtocolError("Server disconnected without sending a response.", request=_REQ),
        httpx.ProxyError("502 Bad Gateway"),
        TimeoutError(),
        ConnectionRefusedError(61, "Connection refused"),
        ConnectionResetError(),
    ],
    ids=lambda e: type(e).__name__,
)
def test_an_unreachable_endpoint_is_transient(exc: BaseException) -> None:
    reason = refresh_retry_reason(exc)
    assert reason == f"the token endpoint could not be reached ({type(exc).__name__})"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol."),
        httpx.LocalProtocolError("Illegal header value"),
        json.JSONDecodeError("Expecting value", "<html>", 0),
        RuntimeError("OpenAI Codex token refresh response missing fields: received keys: x"),
        RuntimeError("Unknown OAuth provider: x"),
        KeyError("refresh_token"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_anything_else_is_not(exc: BaseException) -> None:
    assert refresh_retry_reason(exc) is None


def test_the_text_decides_nothing() -> None:
    """A status in the TEXT is not a status: pi's regex would retry both of these."""

    assert refresh_retry_reason(RuntimeError("502 Bad Gateway from the token endpoint")) is None
    body_names_502 = oauth_http_error('failed (401): {"error": "invalid_grant", "id": "502"}', 401)
    assert refresh_retry_reason(body_names_502) is None


def test_the_cause_chain_decides_and_the_first_decisive_link_wins() -> None:
    # The Anthropic wrapper and _high_level's wrapper chain with ``raise ... from``.
    outer = _chained(RuntimeError("Failed to refresh"), _chained(RuntimeError("wrap"), oauth_http_error("x", 503)))
    assert refresh_retry_reason(outer) == "the token endpoint answered HTTP 503"
    # A 401 above a transport error: the answer decides.
    assert refresh_retry_reason(_chained(oauth_http_error("x", 401), httpx.ReadError(""))) is None


def test_an_exception_the_caller_was_handling_is_not_read() -> None:
    """``__context__`` is not followed: only ``raise ... from`` links count."""

    exc = RuntimeError("Unknown OAuth provider: x")
    exc.__context__ = httpx.ConnectError("unrelated")
    assert refresh_retry_reason(exc) is None


@pytest.mark.parametrize(("status", "transient"), [(503, True), (401, False)])
def test_an_extensions_raise_for_status_is_classified(status: int, transient: bool) -> None:
    response = httpx.Response(status, request=_REQ)
    exc = httpx.HTTPStatusError("x", request=_REQ, response=response)
    assert (refresh_retry_reason(exc) is not None) is transient


def test_the_error_classes_carry_it() -> None:
    cause = _chained(RuntimeError("Failed to refresh OAuth token for p: (502)"), oauth_http_error("(502)", 502))
    assert OAuthRefreshError("p", cause).retry_reason == "the token endpoint answered HTTP 502"
    assert OAuthRefreshError("p", RuntimeError("(401)")).retry_reason is None
    # Every other stored-entry failure: none.
    assert StoredCredentialError("p", "is an api_key with no key").retry_reason is None
    # A refused one keeps the message it had on 8f7d98aa, /login hint and all.
    assert str(OAuthRefreshError("p", RuntimeError("(401)"))) == (
        "OAuth refresh failed for p: (401). Run /login to sign in to p again."
    )


def test_a_transient_ones_text_is_pis_and_sends_nobody_to_login() -> None:
    """Review round 2: the turn retries it, so it does not tell the user to /login - pi's text
    (``ModelsError("oauth", "OAuth refresh failed for <id>", {cause})`` with its cause detail,
    ``auth/resolve.ts:142``, ``utils/models-error.ts:16-21`` @ 1cedd3272)."""

    cause = _chained(RuntimeError("Failed to refresh OAuth token for p: (502)"), oauth_http_error("(502)", 502))
    assert str(OAuthRefreshError("p", cause)) == "OAuth refresh failed for p: (502)"
    unreachable = httpx.ConnectError("All connection attempts failed", request=_REQ)
    text = str(OAuthRefreshError("p", unreachable))
    assert text == "OAuth refresh failed for p: All connection attempts failed"
    assert "/login" not in text


# === the raise sites carry the status ========================================


def _serve(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    return patch.object(httpx, "AsyncClient", side_effect=factory)


def _answer(status: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(status, json={"error": "x"})


_REFRESHES: dict[str, Callable[[], Any]] = {
    "codex": lambda: openai_codex.refresh_openai_codex_token("r-fake"),
    "anthropic": lambda: anthropic.refresh_anthropic_token("r-fake"),
    "copilot": lambda: github_copilot.refresh_github_copilot_token("gh-fake"),
}


@pytest.mark.parametrize("site", list(_REFRESHES))
@pytest.mark.parametrize(("status", "transient"), [(502, True), (429, True), (401, False), (403, False)])
async def test_each_built_in_refresh_raises_with_its_status(
    site: str, status: int, transient: bool
) -> None:
    with _serve(_answer(status)), pytest.raises(RuntimeError) as caught:
        await _REFRESHES[site]()
    assert (refresh_retry_reason(caught.value) is not None) is transient
    assert str(status) in str(caught.value)  # the message is the one it always was


async def test_the_anthropic_details_still_name_a_runtime_error() -> None:
    """The status is data on a plain ``RuntimeError``: ``details=`` names each link's type,
    and a refused refresh keeps the message it had on 8f7d98aa."""

    with _serve(_answer(401)), pytest.raises(RuntimeError) as caught:
        await anthropic.refresh_anthropic_token("r-fake")
    assert "; details=RuntimeError: HTTP request failed. status=401; " in str(caught.value)
    assert type(caught.value.__cause__) is RuntimeError


@pytest.mark.parametrize("site", list(_REFRESHES))
async def test_each_built_in_refresh_that_cannot_connect_is_transient(site: str) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed", request=request)

    with _serve(refuse), pytest.raises(Exception) as caught:  # noqa: PT011 - the type varies per site
        await _REFRESHES[site]()
    assert refresh_retry_reason(caught.value) is not None


# === review round 3: a status whose body cannot be decoded keeps its status ======


class _NotGzip(httpx.AsyncByteStream):
    """A body that says ``Content-Encoding: gzip`` and is not (Codex r2 cat1)."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"not-gzip"


def _undecodable(status: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(
        status,
        headers={"content-encoding": "gzip", "content-type": "application/json"},
        stream=_NotGzip(),
    )


#: What each site's message says once the status survives (the reason phrase where the
#: site has a fallback for an empty body; Anthropic's ``body=`` is empty).
_UNDECODABLE_TEXT = {
    "codex": "OpenAI Codex token refresh failed (502): Bad Gateway",
    "anthropic": "HTTP request failed. status=502; url=https://platform.claude.com/v1/oauth/token; body=",
    "copilot": "502 Bad Gateway from https://api.github.com/copilot_internal/v2/token",
}


@pytest.mark.parametrize("site", list(_REFRESHES))
async def test_a_transient_status_whose_body_cannot_be_decoded_keeps_its_status(site: str) -> None:
    """On 7b0207bb ``client.post()`` raised ``httpx.DecodingError`` before the status check,
    so the 502 had no status, no retry reason, and the turn failed with the /login hint."""

    with _serve(_undecodable(502)), pytest.raises(RuntimeError) as caught:
        await _REFRESHES[site]()
    assert refresh_retry_reason(caught.value) == "the token endpoint answered HTTP 502"
    assert _UNDECODABLE_TEXT[site] in str(caught.value)
    assert "decompress" not in str(caught.value)


@pytest.mark.parametrize("site", list(_REFRESHES))
async def test_a_refused_status_whose_body_cannot_be_decoded_is_still_refused(site: str) -> None:
    with _serve(_undecodable(401)), pytest.raises(RuntimeError) as caught:
        await _REFRESHES[site]()
    assert refresh_retry_reason(caught.value) is None
    assert "401" in str(caught.value)


@pytest.mark.parametrize("site", list(_REFRESHES))
async def test_a_2xx_whose_body_cannot_be_decoded_still_fails_as_before(site: str) -> None:
    """Control: no status to keep - a 200 that is not a token is not retried (the
    DecodingError leaves as it did)."""

    with _serve(_undecodable(200)), pytest.raises(Exception) as caught:  # noqa: PT011 - the type varies per site
        await _REFRESHES[site]()
    assert refresh_retry_reason(caught.value) is None
    chain: list[BaseException] = []
    link: BaseException | None = caught.value
    while link is not None:
        chain.append(link)
        link = link.__cause__
    assert any(isinstance(e, httpx.DecodingError) for e in chain) or "decompress" in str(caught.value)


async def test_the_copilot_device_flow_keeps_the_status_too() -> None:
    """The other two ``_http_error`` sites (the device-code request and the poll) read the
    status the same way; the device-code one is a sign-in, not retried, so only the text."""

    with _serve(_undecodable(503)), pytest.raises(RuntimeError) as caught:
        await github_copilot._start_device_flow("github.com")
    assert "503 Service Unavailable from https://github.com/login/device/code" in str(caught.value)


# === review round 4: once a non-2xx status arrived, that status decides ===========


class _CutShort(httpx.AsyncByteStream):
    """The status and headers arrived, then the body broke (Codex r4 cat1): ``{`` and EOF
    before ``Content-Length: 100`` (httpcore's text), or a read that timed out."""

    def __init__(self, error: type[httpx.TransportError]) -> None:
        self._error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{"
        raise self._error(
            "peer closed connection without sending complete message body"
            " (received 1 bytes, expected 100)"
        )


def _cut_short(
    status: int, error: type[httpx.TransportError] = httpx.RemoteProtocolError
) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(
        status,
        headers={"content-type": "application/json", "content-length": "100"},
        stream=_CutShort(error),
    )


@pytest.mark.parametrize("site", list(_REFRESHES))
@pytest.mark.parametrize(
    "error",
    [httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ReadError],
    ids=lambda e: e.__name__,
)
@pytest.mark.parametrize(
    ("status", "transient"), [(401, False), (400, False), (501, False), (502, True), (429, True)]
)
async def test_a_status_whose_body_breaks_is_decided_by_its_status(
    site: str, error: type[httpx.TransportError], status: int, transient: bool
) -> None:
    """On e946bf53 only a ``DecodingError`` kept the status: a ``401`` cut short read as a
    dropped connection and the refused refresh was retried."""

    with _serve(_cut_short(status, error)), pytest.raises(RuntimeError) as caught:
        await _REFRESHES[site]()
    reason = refresh_retry_reason(caught.value)
    assert reason == (f"the token endpoint answered HTTP {status}" if transient else None)
    assert str(status) in str(caught.value)
    assert "peer closed" not in str(caught.value)


@pytest.mark.parametrize("site", list(_REFRESHES))
@pytest.mark.parametrize("status", [401, 403, 501])
@pytest.mark.parametrize("shape", ["gzip", "cut-short"])
async def test_an_unreadable_refusal_reads_as_the_same_status_with_an_empty_body(
    site: str, status: int, shape: str
) -> None:
    """Codex r4 cat2: the refusal's text is the one the same status with an empty body gets,
    byte for byte - the body could not be read, so none is quoted."""

    broken = _undecodable(status) if shape == "gzip" else _cut_short(status)
    with _serve(broken), pytest.raises(RuntimeError) as unreadable:
        await _REFRESHES[site]()
    empty_body = lambda _r: httpx.Response(status, content=b"")  # noqa: E731
    with _serve(empty_body), pytest.raises(RuntimeError) as empty:
        await _REFRESHES[site]()
    assert str(unreadable.value) == str(empty.value)
    wrapped = str(OAuthRefreshError("p", unreadable.value))
    assert wrapped == str(OAuthRefreshError("p", empty.value))
    assert wrapped.endswith("Run /login to sign in to p again.")


@pytest.mark.parametrize("site", list(_REFRESHES))
async def test_a_refresh_whose_request_write_times_out_is_transient(site: str) -> None:
    """Codex r3 mutant: dropping ``WriteTimeout`` from the set was green everywhere."""

    def stall(request: httpx.Request) -> httpx.Response:
        raise httpx.WriteTimeout("write timed out", request=request)

    with _serve(stall), pytest.raises(Exception) as caught:  # noqa: PT011 - varies per site
        await _REFRESHES[site]()
    reason = refresh_retry_reason(caught.value)
    assert reason == "the token endpoint could not be reached (WriteTimeout)"


@pytest.mark.parametrize("site", list(_REFRESHES))
@pytest.mark.parametrize(
    "error", [httpx.RemoteProtocolError, httpx.ReadTimeout], ids=lambda e: e.__name__
)
async def test_a_2xx_cut_short_or_timed_out_is_still_a_dropped_connection(
    site: str, error: type[httpx.TransportError]
) -> None:
    """Control: a 2xx carries no failure status to keep - a token body cut short, or one
    whose read times out, raises as before and is retried as a dropped connection
    (``terminated`` and timeouts are in pi's retry pattern), at each site. Review round 6:
    verify r5's mutant M5 (a ``ReadTimeout`` during a 2xx body read made a plain
    ``RuntimeError``) was green on every row, and a cut-short 2xx was pinned at the Codex
    site only."""

    with _serve(_cut_short(200, error)), pytest.raises(Exception) as caught:  # noqa: PT011
        await _REFRESHES[site]()
    reason = refresh_retry_reason(caught.value)
    assert reason == f"the token endpoint could not be reached ({error.__name__})"


async def test_a_status_from_an_earlier_request_is_not_this_ones() -> None:
    """``answer`` forgets the last status: an error before THIS request's status arrived
    stays a transport error, whatever an earlier request on the same instance answered."""

    status = StatusBeforeBody()
    await status.event_hooks["response"][0](httpx.Response(401, request=_REQ))

    async def unreachable() -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed", request=_REQ)

    with pytest.raises(httpx.ConnectError):
        await status.answer(unreachable())


@pytest.mark.parametrize("first", ["gz502", "cut502"])
async def test_the_copilot_poll_keeps_polling_after_an_unreadable_502(
    first: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r3 G7: the device-flow poll's hook had no row. A ``502`` whose body cannot be
    read is the poll's transient ``HTTP 502`` - it polls again and returns the token. On
    8f7d98aa (and with the hook removed) the gzip one aborted the sign-in with
    ``DecodingError``."""

    plan = [first, "token"]
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        answer = plan.pop(0) if len(plan) > 1 else plan[0]
        hits.append(answer)
        if answer == "gz502":
            return _undecodable(502)(request)
        if answer == "cut502":
            return _cut_short(502)(request)
        return httpx.Response(200, json={"access_token": "gho_fake"})

    async def no_sleep(_seconds: float) -> None:
        return None

    # The module's own name only: the event loop keeps the real asyncio.sleep.
    monkeypatch.setattr(github_copilot, "asyncio", SimpleNamespace(sleep=no_sleep))
    with _serve(handler):
        token = await github_copilot._poll_for_github_access_token("github.com", "dc-fake", 1, 900)
    assert token == "gho_fake"
    assert hits == [first, "token"]


async def test_the_copilot_poll_stops_on_a_refusal_cut_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll's status decides too: a ``401`` cut short is the poll's refusal (not a
    transient transport error to poll through)."""

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(github_copilot, "asyncio", SimpleNamespace(sleep=no_sleep))
    with _serve(_cut_short(401)), pytest.raises(RuntimeError) as caught:
        await github_copilot._poll_for_github_access_token("github.com", "dc-fake", 1, 900)
    assert "401 Unauthorized from https://github.com/login/oauth/access_token" in str(caught.value)


# === review round 5: rows for the two mutants verify r4 left green =============


def _with_phrase(
    handler: Callable[[httpx.Request], httpx.Response], phrase: bytes
) -> Callable[[httpx.Request], httpx.Response]:
    """The same answer, with the server's own reason phrase on its status line."""

    def answer(request: httpx.Request) -> httpx.Response:
        response = handler(request)
        response.extensions["reason_phrase"] = phrase
        return response

    return answer


@pytest.mark.parametrize("site", list(_REFRESHES))
@pytest.mark.parametrize("shape", ["gzip", "cut-short"])
async def test_an_unreadable_refusal_keeps_the_servers_own_reason_phrase(
    site: str, shape: str
) -> None:
    """Verify r4 mutant W2: ``answer`` dropping ``reason_phrase`` from the extensions it keeps
    was green everywhere - httpx's MockTransport answers carry the default phrase. A server
    that says ``401 Status`` gets the same text whether or not its body could be read (Codex
    ``(401): Status``, Copilot ``401 Status from``; Anthropic quotes no phrase)."""

    broken = _undecodable(401) if shape == "gzip" else _cut_short(401)
    with _serve(_with_phrase(broken, b"Status")), pytest.raises(RuntimeError) as unreadable:
        await _REFRESHES[site]()
    empty_body = _with_phrase(lambda _r: httpx.Response(401, content=b""), b"Status")
    with _serve(empty_body), pytest.raises(RuntimeError) as empty:
        await _REFRESHES[site]()
    assert str(unreadable.value) == str(empty.value)
    if site != "anthropic":
        assert "Status" in str(unreadable.value)
        assert "Unauthorized" not in str(unreadable.value)


class _Stalled(httpx.AsyncByteStream):
    """The status and headers arrived, then the body read waits until it is cancelled."""

    def __init__(self, reading: asyncio.Event) -> None:
        self._reading = reading

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._reading.set()
        await asyncio.Event().wait()
        yield b""  # pragma: no cover - never reached


@pytest.mark.parametrize("site", list(_REFRESHES))
@pytest.mark.parametrize("status", [401, 502])
async def test_a_cancel_while_a_status_body_is_read_is_still_a_cancel(
    site: str, status: int
) -> None:
    """Verify r4 mutant W1: ``answer`` catching ``BaseException`` turned a cancel (Esc)
    during a non-2xx body read into that status - a ``401`` refused the login, a ``502``
    started a retry - instead of cancelling. Only an ``Exception`` becomes the status."""

    reading = asyncio.Event()

    def stalled(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"content-type": "application/json", "content-length": "100"},
            stream=_Stalled(reading),
        )

    with _serve(stalled):
        task = asyncio.ensure_future(_REFRESHES[site]())
        await asyncio.wait_for(reading.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert task.cancelled()
