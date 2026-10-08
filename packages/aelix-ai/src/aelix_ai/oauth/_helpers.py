"""Shared OAuth-internal helpers — Sprint 6e W6 (P-157).

Extracted from the duplicate definitions in ``anthropic.py`` /
``openai_codex.py`` / ``github_copilot.py``. Each provider previously
held its own copy of ``_maybe_await``; this module is the single owner.

Pi parity: Pi's OAuth flows use top-level ``await`` plus
``Promise.resolve()`` for sync-or-async callback invocation. Aelix
mirrors that pattern with :func:`_maybe_await`: a function that awaits
a value only when it's a coroutine/awaitable, otherwise returns it
verbatim.
"""

from __future__ import annotations

import inspect
import re
import sys
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from aelix_ai.utils.terminal_text import safe_for_terminal


async def maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's a coroutine/awaitable.

    The Sprint 6a pattern from ``providers/anthropic.py`` —
    sync-or-async callbacks invoked through this helper return their
    final value regardless of whether the implementation is sync or
    async.
    """

    if inspect.isawaitable(value):
        return await value
    return value


# How much of a server's text may travel inside an exception message (#186).
#
# Not a taste call. The body #184's reporter actually received was **54,889
# characters over 164 lines** — GitHub's "Unicorn" page, most of it a single
# 45,736-character base64 PNG data URI — and every byte was interpolated raw into
# a ``RuntimeError`` the TUI printed. At 80 columns that is ~815 rendered rows:
# the entire transcript, gone. The Codex and Anthropic token endpoints had the
# same raw interpolation, and the Codex refresh runs on every turn once the
# access token expires.
#
# 512 keeps a whole one-line JSON OAuth error (``{"error": "invalid_grant",
# "error_description": ...}`` is under 200) and the opening of an HTML error
# page, which is enough to tell a proxy's page from the provider's.
#
# Bounded HERE, at the raise site, as well as at the render site, and that is
# not belt-and-braces: these exceptions also reach ``-p`` stderr, logs and the
# JSON/RPC modes, none of which pass through the TUI's own bound. An unbounded
# server body inside an exception message makes every future consumer
# responsible for remembering.
SERVER_TEXT_MAX_CHARS: int = 512


#: A run of blank space, collapsed to one space before the cut (review round 2, #186).
_BLANK_RUN = re.compile(r"\s{2,}")


def quote_server_text(text: str) -> str:
    """A server's words, made fit to sit inside an exception message (#186).

    For every string the OTHER end of an HTTP exchange chose: a response body,
    a JSON ``error``/``error_description``, the keys of a token response, the
    reason phrase - which h11 admits with ESC in it (its grammar is
    ``[^\\x00\\s]``) and httpx decodes as ASCII, so ``ESC[2J`` survives there
    too even with an empty body - and the text of a transport error, which
    quotes a proxy's CONNECT reason phrase (:func:`quoting_transport_errors`,
    and :func:`aelix_ai.providers._error_hints.describe_provider_error` on the
    model request path).

    Steering characters are deleted (:func:`safe_for_terminal`: C0 including
    ESC, tab and newline, C1, BiDi, ZWSP), every run of blank space is collapsed
    to one space, the result is trimmed, and only then cut at
    :data:`SERVER_TEXT_MAX_CHARS` code points plus a ``…`` marker. Delete,
    collapse, trim, cut - in that order, so the budget is spent on visible
    text: 60,000 ESC bytes in front of a one-line JSON error keep it whole, and
    2,000 blank characters in front of it, behind it or between it and the
    words before it cost one space at most (measured in review, #186). The
    result is therefore at most 513 characters, whatever the input. Newlines
    are deleted rather than kept: the caller's message is one line, and a body
    cannot add rows of its own. (A model request's error turns each line break
    into a space first, so its words stay apart:
    :func:`aelix_ai.providers._error_hints.quote_model_text`.)

    The bound is per quotation, not per message: a message that quotes two
    server strings (the Copilot device-flow ``error`` and its description, or
    a reason phrase and a body) can carry about twice as much.

    An empty result means the server said nothing visible, and each caller
    decides what stands in for it: the Codex token errors quote the reason
    phrase instead, Copilot's ``_http_error`` falls back to httpx's own reason
    table and leaves out ``server said:``, and the device-flow error says
    ``(unnamed error)``. Anthropic's pi-shaped ``body=`` stays empty, as pi's
    does for an empty body - the status is already in that message.
    """

    visible = _BLANK_RUN.sub(" ", safe_for_terminal(text)).strip()
    return safe_for_terminal(visible, max_chars=SERVER_TEXT_MAX_CHARS)


#: The packages whose exceptions make up an httpx request's own failure chain:
#: httpx wraps httpcore (``raise ... from exc``), and httpcore wraps h11, whose
#: ``RemoteProtocolError`` quotes the bytes the other end sent.
_TRANSPORT_PACKAGES: frozenset[str] = frozenset({"httpx", "httpcore", "h11"})


def _is_transport_error(exc: BaseException) -> bool:
    return type(exc).__module__.partition(".")[0] in _TRANSPORT_PACKAGES


def _next_link(link: BaseException) -> BaseException | None:
    """The link below ``link``, crossing a suppressed context on purpose.

    httpcore's pool re-raises every request error with ``raise exc from None``,
    which clears the ``__cause__`` its ``map_exceptions`` set and hides the
    exception underneath behind ``__suppress_context__`` - and on a garbled
    status line that exception is h11's ``RemoteProtocolError``, which quotes
    the bytes the other end sent. A walk that stopped at the suppression (or
    followed ``__cause__`` alone) left them raw. The stop that matters is
    :func:`_quote_exception_chain`'s, not the suppression flag.
    """

    return link.__cause__ if link.__cause__ is not None else link.__context__


def _quote_exception_chain(exc: BaseException, *, handled: BaseException | None) -> None:
    """Quote, in place, every link of the request's own failure chain that needs it.

    The walk stays inside the transport chain (review round 2, #186). It stops
    at the first link that is not an httpx, httpcore or h11 exception, and at
    ``handled`` - the exception the caller was already handling when the
    request started. Python records that one as the ``__context__`` of the
    first error raised inside the request (httpcore raises ``ProxyError``
    plainly), so a walk that only followed the links rewrote an unrelated
    error the caller was in the middle of handling, newlines and all.
    """

    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        if link is handled or not _is_transport_error(link):
            return
        seen.add(id(link))
        text = str(link)
        quoted = quote_server_text(text)
        # Only a link whose text changes is rewritten, so an ordinary error - an
        # OpenSSL verify failure the TLS hint reads, a short ConnectError - keeps
        # its args, and with them everything else that reads them. Compared with
        # the ORIGINAL text, not a trimmed copy: 2,048 tabs or spaces behind a
        # reason phrase are a change too, and skipping them re-raised the
        # unbounded text (review round 2, #186).
        if quoted != text:
            link.args = (quoted,)
        link = _next_link(link)


@contextmanager
def quoting_transport_errors() -> Iterator[None]:
    """Re-raise an httpx error from an OAuth request with its text quoted (#186).

    A proxy that refuses the CONNECT answers with a status line of its own
    choosing, and httpcore puts that reason phrase into ``ProxyError`` -
    ``ESC[2J``, an OSC 52 clipboard write and 55,000 characters measured
    intact - before any response exists for :func:`quote_server_text` to
    quote. The Codex and Anthropic login wrappers then interpolated it, and its
    cause, into their own message, and Copilot let it escape as it was.

    Quoted IN PLACE, the exception and its cause chain, rather than wrapped:
    callers tell a transport failure from an answer by its type (Copilot's
    reachability check after /login, #99; the poll's transient set), and the
    TLS hint walks the chain to the OpenSSL error at its bottom. Only the text
    changes, only where it has to, and only in the request's own chain: every
    ``httpx.HTTPError`` is quoted, not just ``ProxyError`` - an h11
    ``RemoteProtocolError`` quotes the status line the other end sent - and the
    walk stops where the transport chain ends (:func:`_quote_exception_chain`).
    """

    # What the caller is handling as the request starts: the bottom of the
    # request's own chain points at it, and it is not ours to rewrite.
    handled = sys.exc_info()[1]
    try:
        yield
    except httpx.HTTPError as exc:
        _quote_exception_chain(exc, handled=handled)
        raise


def format_error_details(error: BaseException, *, handled: BaseException | None) -> str:
    """Pi ``formatErrorDetails`` (``anthropic.ts:81-96``): ``Type: text`` per link.

    The links are joined by ``; cause=``, outermost first, as pi's recursion
    builds them. The walk stops at ``handled`` - the exception the caller was
    handling when the OAuth call started, read with ``sys.exc_info()`` at the
    wrapper's entry (review round 3, #186). Python makes it the ``__context__``
    of the first error raised inside the call, so a walk that only followed the
    links copied the caller's own exception into ``details=``: a library caller
    retrying a refresh inside ``except httpx.ProxyError`` got back that proxy's
    reason phrase raw - 4,278 characters with ``ESC[2J`` and OSC 52 measured
    from the Anthropic refresh wrapper - because :func:`quoting_transport_errors`
    leaves the caller's exception alone by design. pi's ``cause`` is explicit
    and never reaches a caller's error; leaving it out is what pi prints. The
    caller's exception itself is not touched.
    """

    parts: list[str] = []
    seen: set[int] = set()
    link: BaseException | None = error
    while link is not None and link is not handled and id(link) not in seen:
        seen.add(id(link))
        parts.append(f"{type(link).__name__}: {link}")
        link = link.__cause__ or link.__context__
    return "; cause=".join(parts)


#: The attribute :func:`oauth_http_error` sets and :func:`refresh_retry_reason` reads.
_STATUS_ATTR = "oauth_status_code"


def oauth_http_error(message: str, status_code: int) -> RuntimeError:
    """The error for a token endpoint that answered, and not with success (#379).

    The message is the one the raise site always built (the status, the URL,
    the quoted body); the status code also travels as data, so
    :func:`refresh_retry_reason` can tell a ``502`` from a ``401`` without
    reading the text. A plain :class:`RuntimeError`, not a subclass: the
    Anthropic wrappers' ``details=`` names each link's type (pi's
    ``formatErrorDetails``), and a refused refresh must keep the message it had.
    """

    error = RuntimeError(message)
    setattr(error, _STATUS_ATTR, status_code)
    return error


class StatusBeforeBody:
    """An OAuth request's non-2xx answer keeps its status whatever happens to its body (#379).

    ``client.post()`` reads the body before it returns, so an error while
    reading it left the caller with no response and no status, and the
    caller's status check - the one that attaches the status with
    :func:`oauth_http_error` - never ran. A ``502`` whose ``Content-Encoding:
    gzip`` body is not gzip raised ``httpx.DecodingError`` and failed at once
    with the ``/login`` hint (review round 3, Codex); a ``401`` whose body
    stopped short of its ``Content-Length`` raised ``httpx.RemoteProtocolError``,
    which reads as a dropped connection, so the REFUSED refresh was retried
    and the next ``200`` signed the user back in (review round 4, Codex).

    So: once a non-2xx status has arrived, that status decides. Pass
    :attr:`event_hooks` to the ``httpx.AsyncClient``: httpx runs a
    ``response`` hook once the status line and headers are in, before it reads
    the body. Then ``await answer(client.post(...))``: a non-2xx answer whose
    body cannot be read - ANY error after the status arrived, a broken
    encoding, a body cut short, a read timeout - comes back as that status
    with an empty body. The caller classifies it by its status (``401``
    refused, ``501`` not retried, ``502`` retried) and builds the message it
    builds for that status with an empty body - byte for byte the text a
    ``401`` with an empty body gets. An error before the status and headers
    arrived (no connection, a write that timed out, a hang-up before the
    header block completed) leaves as the transport error it is. So does an
    error while reading a 2xx's body, which has no failure status to keep: a
    body cut short or a read timeout is retried as a dropped connection
    (``terminated`` and timeouts are in pi's retry pattern, ``retry.ts``); a
    broken encoding (``httpx.DecodingError``) fails at once. In short (#379
    review round 7, ADR-0251 §12.3): a non-2xx answer is decided by its
    status once it arrives (``400``/``401``/``403`` refused at once; ``429``,
    ``500``, ``502``-``504``, ``520``, ``524`` retried; any other status
    fails); a 2xx whose body is cut short or times out is retried as a
    dropped connection; a 2xx with a broken encoding fails at once. For an
    answer whose body fails mid-read, pi's behaviour depends on the provider
    and the connection framing (its Codex refresh reads a non-2xx answer's body with
    .catch, its Anthropic and Copilot refreshes do not), so aelix's rule
    above can differ from pi for these malformed answers.
    """

    def __init__(self) -> None:
        self.response: httpx.Response | None = None
        self.event_hooks: dict[str, list[Any]] = {"response": [self._seen]}

    async def _seen(self, response: httpx.Response) -> None:
        self.response = response

    def _arrived(self) -> httpx.Response | None:
        """What the hook saw for this request - read through a call, which the
        ``self.response = None`` reset in :meth:`answer` does not narrow."""

        return self.response

    async def answer(self, sent: Awaitable[httpx.Response]) -> httpx.Response:
        # A status from an earlier request on this instance says nothing about
        # this one: an error before this request's status must stay a transport error.
        self.response = None
        try:
            return await sent
        except Exception:
            seen = self._arrived()
            if seen is None or 200 <= seen.status_code < 300:
                raise
            headers = [
                (k, v)
                for k, v in seen.headers.multi_items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
            ]
            kept = {
                k: v for k, v in seen.extensions.items() if k in ("http_version", "reason_phrase")
            }
            return httpx.Response(
                seen.status_code,
                headers=headers,
                content=b"",
                request=seen.request,
                extensions=kept,
            )


#: Transport failures that say nothing about whether the refresh request was
#: valid: the endpoint was not reached, did not answer in time, or hung up -
#: before the status and headers arrived, or while a 2xx's body was read (a body cut short
#: or a read timeout: retried as a dropped connection; ``terminated`` and
#: timeouts are in pi's retry pattern - for how pi treats a body that fails
#: mid-read, see :class:`StatusBeforeBody` and ADR-0251 §12.3). A failure
#: after a non-2xx status never gets here: :class:`StatusBeforeBody` turns it
#: into that status, which decides. ``httpx.TimeoutException`` is the whole
#: family on purpose: a ``WriteTimeout`` (the request could not be sent in
#: time) is as transient as a ``ConnectTimeout`` or a ``ReadTimeout``.
#: ``httpx.ProxyError`` is one (a proxy that refused the CONNECT): pi's fetch
#: reports it as ``fetch failed``, which its retry matches. Not here:
#: ``httpx.UnsupportedProtocol`` and ``httpx.LocalProtocolError`` (a request
#: this process built wrong fails the same way every time).
_TRANSIENT_TRANSPORT: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
    TimeoutError,
    ConnectionError,
)


#: The statuses pi's retry pattern names (``RETRYABLE_PROVIDER_ERROR_PATTERN``,
#: ``packages/ai/src/utils/retry.ts:30-45`` @ 1cedd3272: ``429``, ``500``,
#: ``502``, ``503``, ``504``, ``520``, ``524``) - exactly those (#379 review
#: round 2, the owner's "decide in pi's direction"). ``501``, ``505`` and every
#: other ``5xx`` are not retried, as in pi.
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504, 520, 524})


def _transient_status(status: int) -> bool:
    return status in _TRANSIENT_STATUSES


def refresh_retry_reason(exc: BaseException) -> str | None:
    """Why a failed OAuth refresh is worth retrying, or ``None`` (#379, ADR-0251 §4).

    The owner's decision (2026-10-06) follows pi: a refresh that failed on a
    transient cause is retried by the turn's auto-retry, anything else fails at
    once. pi decides on the error's text (``isRetryableAssistantError``,
    ``packages/ai/src/utils/retry.ts`` @ 1cedd3272); this decides on the
    exception chain instead, because the text holds the server's own words and
    the ``auth.json`` path - a ``401`` body that mentions ``502`` must not be
    retried, and httpx's ``All connection attempts failed`` or ``Server
    disconnected without sending a response.`` match none of pi's patterns.

    Walks ``exc`` and its ``__cause__`` links (every wrapper on the refresh
    path chains with ``raise ... from``; ``__context__`` is not followed, so an
    exception the caller was handling is never read). The first link that
    decides answers:

    - an HTTP answer - :func:`oauth_http_error` (the built-in Codex, Anthropic
      and Copilot refreshes) or ``httpx.HTTPStatusError`` (an extension's
      ``raise_for_status()``): a status pi's pattern names
      (:data:`_TRANSIENT_STATUSES`: ``429``, ``500``, ``502``, ``503``,
      ``504``, ``520``, ``524``) is transient, any other status
      (``400``/``401``/``403``: ``invalid_grant``, a revoked login; ``501``,
      ``505``) is not;
    - a transport failure (:data:`_TRANSIENT_TRANSPORT`, pi's ``fetch failed``
      class): transient.

    A chain with neither - a 2xx whose whole body was read and is not JSON, a
    2xx whose encoding is broken, a token response missing fields, an unknown
    OAuth provider - is not transient.
    """

    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        status = getattr(link, _STATUS_ATTR, None)
        if isinstance(link, httpx.HTTPStatusError):
            status = link.response.status_code
        if isinstance(status, int):
            if _transient_status(status):
                return f"the token endpoint answered HTTP {status}"
            return None
        if isinstance(link, _TRANSIENT_TRANSPORT):
            return f"the token endpoint could not be reached ({type(link).__name__})"
        link = link.__cause__
    return None


def describe_token_response_keys(data: Any) -> str:
    """Name what a 2xx token response carried — its KEYS, never its values (#186).

    A token response that lacks a field still carries the others, and those are
    live credentials: a response with an ``access_token`` but no
    ``refresh_token`` used to put that access token into the error message,
    which reaches the screen, ``-p`` stderr and logs. The keys are enough to
    diagnose a proxy or a changed API; they are the server's words, so they are
    quoted like any other.
    """

    if not isinstance(data, dict):
        return f"a JSON {type(data).__name__}, not an object"
    keys = ", ".join(str(key) for key in data)
    return f"received keys: {quote_server_text(keys) or '(none)'}"


__all__ = [
    "StatusBeforeBody",
    "SERVER_TEXT_MAX_CHARS",
    "describe_token_response_keys",
    "format_error_details",
    "maybe_await",
    "oauth_http_error",
    "quote_server_text",
    "quoting_transport_errors",
    "refresh_retry_reason",
]
