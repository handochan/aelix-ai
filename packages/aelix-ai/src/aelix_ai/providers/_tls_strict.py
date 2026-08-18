"""RFC-5280 strict verification, and the corporate-proxy wall it puts up on 3.13.

## What this is for

Python **3.13** turned on ``X509_V_FLAG_X509_STRICT`` (and ``PARTIAL_CHAIN``) in
``ssl.create_default_context()``. 3.12 did not. Measured, same box, same certs::

    3.12.1   verify_flags = 32768    strict OFF
    3.13.13  verify_flags = 557088   strict ON

Strict enforces RFC 5280 clauses that essentially nothing else on a developer's
machine enforces — not ``openssl s_client``, not curl, not Node, not browsers,
and not Python 3.12. And a TLS-intercepting corporate proxy mints certificates on
the fly that routinely violate them. A real report::

    certificate verify failed: Missing Authority Key Identifier (_ssl.c:1032)

from a machine whose proxy root (``Paloalto-PA-Global_Root_CA``) is installed in
the OS trust store and where ``openssl verify`` returns ``0 (ok)``. The CA is
trusted. The chain is fine by every other tool's standard. Python 3.13 alone
rejects it.

This mattered *invisibly* because of how aelix is installed: ``install.sh`` uses
``uv tool install``, which consults neither ``.python-version`` (3.12) nor
``uv.lock`` and picks **3.13**, while a contributor's ``uv sync`` gets 3.12. That
is the whole of "it works from a clone and fails when installed".

## What the relaxation is, precisely

**Only ``VERIFY_X509_STRICT`` is cleared.** ``verify_mode=CERT_REQUIRED``,
``check_hostname``, the validity window and chain-to-a-trusted-root are all still
enforced, against the OS trust store. This is not "trust more"; it is "stop being
uniquely stricter than every other tool on the machine".

**And it is never done on assumption.** It happens only after aelix has
*measured* that the exact host that failed verifies once strict is cleared —
i.e. that the user's own operating system already trusts that chain. If the
re-check fails, nothing is relaxed and the original error stands.

## Scope: the whole process, on purpose

``ssl.create_default_context`` is the single seam. Measured: httpx, the OpenAI
SDK and the Anthropic SDK each call it exactly once per client, and none of
aelix's ~13 client-construction sites passes ``verify=``. So relaxing here
reaches every provider, every OAuth flow, MCP over HTTP and any client an
extension opens.

That breadth is deliberate. An intercepting proxy intercepts the whole network;
relaxing only the provider that happened to fail first would leave the next one
broken and produce the same bug report tomorrow.

The relaxation is **session-scoped** — it lasts for this process and is not
persisted — and it is reported by ``aelix status`` so it is never silent.

Per-connection evidence (re-checking every chain rather than flipping once for
the session) is the stricter design and is deliberately left as follow-up: it
needs a retry hook that truststore's OpenSSL backend does not offer, because
there ``_verify_peercerts_impl`` is a no-op and OpenSSL does the verifying.

## Why a function wrapper and not a subclass

Rebinding ``ssl.SSLContext`` to a subclass sends CPython's own property setters
into unbounded recursion — its ``verify_mode`` setter resolves ``SSLContext``
through the **module global**, so a subclass re-enters itself (measured:
``RecursionError`` after 499 frames). Truststore wraps rather than subclasses for
the same reason. Hence: wrap the factory function.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass

__all__ = [
    "Relaxation",
    "TlsFailure",
    "extract_tls_failure",
    "maybe_relax_strict_for_session",
    "reset_strict_relaxation",
    "session_relaxation",
    "strict_is_enabled",
]

#: Seconds for the confirmation handshake. Short on purpose: this runs on a path
#: the user is already waiting on, and a black-holed corporate proxy must not add
#: a minute to an error they are going to see anyway.
_CONFIRM_TIMEOUT_SECONDS: float = 5.0

_STRICT = ssl.VerifyFlags.VERIFY_X509_STRICT

_relaxation: Relaxation | None = None
_original_factory = None  # set when the wrapper is installed, so it is idempotent


@dataclass(frozen=True)
class TlsFailure:
    """The parts of a TLS verification failure worth acting on."""

    host: str | None
    port: int | None
    verify_code: int | None
    verify_message: str | None


@dataclass(frozen=True)
class Relaxation:
    """Why strict verification was relaxed for this session."""

    host: str
    verify_code: int | None
    verify_message: str | None

    def describe(self) -> str:
        detail = self.verify_message or f"verify code {self.verify_code}"
        return (
            f"RFC-5280 strict verification relaxed for this session after "
            f"{self.host} failed it ({detail}) but verified against this machine's "
            f"trust store without it. Certificate verification, hostname checking "
            f"and expiry are still enforced."
        )


def strict_is_enabled() -> bool:
    """Is ``VERIFY_X509_STRICT`` on in the context clients would build right now?

    Read off a freshly built context rather than from a recorded flag, for the
    reason ``_error_hints`` gives about the trust store: a flag set at startup can
    lie about the state that is actually in force.
    """

    try:
        return bool(ssl.create_default_context().verify_flags & _STRICT)
    except Exception:  # noqa: BLE001 — a diagnostic must never raise
        return False


def _causes(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 10 and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def extract_tls_failure(exc: BaseException) -> TlsFailure | None:
    """Pull host, port and the OpenSSL verify code out of a failed request.

    Measured through the real stack, with a negative control that succeeds when
    strict is cleared: both ``httpx`` and the OpenAI SDK wrap the original
    ``ssl.SSLCertVerificationError`` in their own exception AND attach the
    ``httpx.Request``, so one seam recovers everything::

        APIConnectionError -> ConnectError -> ConnectError -> SSLCertVerificationError
        request.url.host = 'localhost'   verify_code = 89

    Returns ``None`` when this is not a certificate-verification failure, so a
    caller can use it as the guard.
    """

    chain = _causes(exc)

    verification = next(
        (e for e in chain if isinstance(e, ssl.SSLCertVerificationError)), None
    )
    if verification is None:
        return None

    host: str | None = None
    port: int | None = None
    for link in chain:
        request = getattr(link, "request", None)
        url = getattr(request, "url", None)
        if url is not None:
            host = getattr(url, "host", None) or None
            port = getattr(url, "port", None)
            break

    return TlsFailure(
        host=host,
        port=port,
        verify_code=getattr(verification, "verify_code", None),
        verify_message=getattr(verification, "verify_message", None),
    )


def _handshake(host: str, port: int, *, strict: bool) -> bool:
    """One bare TLS handshake. **No credentials, no request body, no payload.**

    The context is the one clients would get — i.e. the OS trust store via
    truststore when it is injected — with only the strict flag varied. That is
    what makes a success here mean "this machine already trusts this chain".
    """

    try:
        context = ssl.create_default_context()
        if strict:
            context.verify_flags |= _STRICT
        else:
            context.verify_flags &= ~_STRICT
        with (
            socket.create_connection(
                (host, port), timeout=_CONFIRM_TIMEOUT_SECONDS
            ) as raw,
            context.wrap_socket(raw, server_hostname=host),
        ):
            return True
    except Exception:  # noqa: BLE001 — any failure is a "no", never a raise
        return False


def _confirm_relaxable(host: str, port: int) -> bool:
    """Does this chain fail ONLY because of strict?

    Both directions are measured, and both matter:

    * it must **fail** with strict on — otherwise the original error was
      something else (a flaky network, a host that has since recovered) and
      relaxing would be a change made for no reason;
    * it must **succeed** with strict off — that is the evidence that the user's
      own operating system already trusts this chain.
    """

    if _handshake(host, port, strict=True):
        return False
    return _handshake(host, port, strict=False)


def _install_relaxed_factory() -> None:
    global _original_factory
    if _original_factory is not None:
        return  # already wrapped; wrapping twice would nest for no benefit

    original = ssl.create_default_context

    def relaxed(*args: object, **kwargs: object) -> ssl.SSLContext:
        context = original(*args, **kwargs)  # type: ignore[arg-type]
        context.verify_flags &= ~_STRICT
        return context

    _original_factory = original
    ssl.create_default_context = relaxed  # type: ignore[assignment]


def maybe_relax_strict_for_session(exc: BaseException) -> Relaxation | None:
    """Relax strict verification for this process, if and only if it is warranted.

    Returns the recorded :class:`Relaxation` when this call caused one (or when
    one is already in force and this failure matches its shape), else ``None``.

    Deliberately does nothing when:

    * the failure is not a certificate-verification failure;
    * strict is already off (3.12, or a previous relaxation);
    * the host cannot be recovered from the exception — guessing one and
      handshaking to it would be a network call to somewhere nobody asked for;
    * the confirmation handshake does not show a strict-only rejection.

    Never raises. It runs on a path that is already handling an error, and a
    diagnostic that turns one failure into two is worse than no diagnostic.
    """

    global _relaxation

    try:
        if _relaxation is not None:
            return _relaxation
        if not strict_is_enabled():
            return None

        failure = extract_tls_failure(exc)
        if failure is None or not failure.host:
            return None

        port = failure.port or 443
        if not _confirm_relaxable(failure.host, port):
            return None

        _install_relaxed_factory()
        _relaxation = Relaxation(
            host=failure.host,
            verify_code=failure.verify_code,
            verify_message=failure.verify_message,
        )
        return _relaxation
    except Exception:  # noqa: BLE001 — never turn one failure into two
        return None


def session_relaxation() -> Relaxation | None:
    """What ``aelix status`` reports. ``None`` when nothing was relaxed."""

    return _relaxation


def reset_strict_relaxation() -> None:
    """Undo everything this module did. For tests, which must not leak into each other."""

    global _relaxation, _original_factory
    if _original_factory is not None:
        ssl.create_default_context = _original_factory  # type: ignore[assignment]
        _original_factory = None
    _relaxation = None
