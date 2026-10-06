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
from collections.abc import Iterator
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
    "SERVER_TEXT_MAX_CHARS",
    "describe_token_response_keys",
    "format_error_details",
    "maybe_await",
    "quote_server_text",
    "quoting_transport_errors",
]
