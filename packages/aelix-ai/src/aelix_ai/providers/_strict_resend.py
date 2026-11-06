"""Send a request again, once, when the session's strict relaxation answered its failure (#703).

## What this is for

On Python 3.13 a TLS-intercepting proxy's certificate can fail RFC 5280 strict
checking alone, which nothing else on the machine enforces
(:mod:`aelix_ai.providers._tls_strict`). The adapter that sees the failure
measures that the machine trusts the chain without strict and relaxes strict
for the session; its error text then carries the ``resend`` retry verdict
(#572, :func:`aelix_ai.utils.overflow.retry_verdict`). Before #703 that failure
went on to the turn as an error: the TUI drew it in red with a four-line note,
the turn waited out a 2 s retry, and the relaxation, which is not persisted,
made that happen again on every launch.

The failure is not one the user can act on: aelix measured the cause and dealt
with it. So the adapter sends the request again itself, at once, on a new
client - which builds its SSL context without ``VERIFY_X509_STRICT`` - and the
turn, a compaction's summary request or anything else that reads the stream
sees only that request's answer. The relaxation says so once, in one line
(:meth:`aelix_ai.providers._tls_strict.Relaxation.notice`).

## When it does, and does not

Sent again only when the stream's FIRST event is an ``error`` with that
verdict, not an abort, and no caller-owned client (``options.client``: its SSL
context was built when the caller built it, so the same client would fail the
same way; the turn's retry, which reads the same verdict, is the one left to
it). An error that is the first event is not by itself one before any
response: every built-in adapter calls ``on_response`` (the harness's
``after_provider_response``) once the provider has answered and yields
``start`` only after it, so a hook that fails there - its own HTTPS request
failing on strict, say - is the first event too. The verdict is what tells
them apart: the adapter describes a failure raised after the response with
``after_response`` (``describe_provider_error``), and such a failure never
carries ``resend`` - sending it again would send a request the provider had
already answered (fix round 1; measured before: two POSTs). An abort made while
the first attempt was being closed is read again before the request is sent
again, and ends it with the first failure. At most once: if the request sent
again fails with the same verdict - a code 79 under a relaxation measured on 79,
which a ``CA:FALSE`` issuer gives with or without strict - the relaxation did
not answer it after all, and that failure goes out marked ``repeats``, so
nothing retries it and its advice is shown once (#551).

## What sending it again means for hooks

The adapter runs again from the top, so ``on_payload`` (the harness's
``before_provider_payload``) runs a second time for the request sent again, as
it does for the turn's retry; the first payload never left this machine - the
failure that is sent again is the request's own TLS handshake, before any
response, and no byte of the request was written. ``before_provider_request``,
which the harness emits around the stream, runs once; ``on_response`` once, for
the answer.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any, TypeVar, cast

from aelix_ai.streaming import AssistantErrorEvent
from aelix_ai.utils.overflow import classifier_text_of, retry_verdict, with_classifier_text

__all__ = ["resend_when_strict_relaxed"]

_Stream = TypeVar("_Stream", bound=Callable[..., AsyncIterator[Any]])


def _answered_by_relaxation(event: object) -> bool:
    return (
        isinstance(event, AssistantErrorEvent)
        and event.reason == "error"
        and retry_verdict(event.error_message or event.error.error_message or "") == "resend"
    )


def _may_send_again(options: object) -> bool:
    if getattr(options, "client", None) is not None:
        return False
    signal = getattr(options, "signal", None)
    return not getattr(signal, "aborted", False)


def _repeats(event: AssistantErrorEvent) -> AssistantErrorEvent:
    """``event`` with its verdict turned to ``repeats``: sent again, it failed again."""

    text = event.error_message or event.error.error_message or ""
    marked = with_classifier_text(str(text), classifier_text_of(text), repeats_on_retry=True)
    return replace(event, error=replace(event.error, error_message=marked), error_message=marked)


def resend_when_strict_relaxed(stream: _Stream) -> _Stream:
    """Wrap a built-in adapter's stream function: see the module docstring."""

    @functools.wraps(stream)
    async def resending(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        options = args[2] if len(args) > 2 else kwargs.get("options")
        dropped: AssistantErrorEvent | None = None  # the first attempt's failure
        while True:
            if dropped is not None and not _may_send_again(options):
                # Aborted while the first attempt was being closed: not sent again.
                yield dropped
                return
            events = stream(*args, **kwargs)
            first = True
            try:
                async for event in events:
                    out = event
                    if first and _answered_by_relaxation(event):
                        if dropped is None and _may_send_again(options):
                            dropped = cast(AssistantErrorEvent, event)
                            break
                        if dropped is not None:
                            out = _repeats(event)
                    first = False
                    yield out
                else:
                    return
            finally:
                close = getattr(events, "aclose", None)
                if close is not None:
                    await close()

    return cast(_Stream, resending)
