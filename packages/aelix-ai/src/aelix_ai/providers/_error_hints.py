"""Actionable hints for opaque provider transport errors.

The OpenAI / Anthropic SDKs surface a bare ``APIConnectionError("Connection
error.")`` whose real cause (an ``ssl.SSLCertVerificationError``) is buried in
the ``__cause__`` chain (``openai/_base_client.py`` raises
``APIConnectionError(request=request) from err``). On a corporate network that
intercepts HTTPS with a private root CA, EVERY request fails this way — and
"Connection error." on its own names neither the reason nor the host.
:func:`describe_provider_error` digs the innermost cause back out and, for a TLS
trust failure, appends the remedy that matches the OpenSSL verify code.

Trust configuration (issue #99): ``cli/entry.py`` injects ``truststore`` at CLI
startup, so the OS certificate store — where a corporate root CA is installed
system-wide, and what already makes VS Code / browsers work on the same network
— is trusted. certifi's bundle does NOT include such a CA, which is why a
Python-based agent fails where an Electron one succeeds. ``SSL_CERT_FILE`` /
``SSL_CERT_DIR`` (honored by httpx) stay the escape hatch when the CA cannot be
installed system-wide, e.g. inside a container. That injection is best-effort
and embedders never run it, so the hint asks :func:`_os_trust_store_active` which
store is live instead of asserting one.
"""

from __future__ import annotations

import ssl

# Substrings that mark a TLS trust failure across httpx / OpenSSL / SDK wrappers.
# Cert-specific ONLY: a bare "SSL" / "ConnectError" substring would drag 401s,
# DNS failures and connection-refused into the TLS branch.
_TLS_MARKERS: tuple[str, ...] = (
    "CERTIFICATE_VERIFY_FAILED",
    "certificate verify failed",
    "self-signed certificate",
    "self signed certificate",
    "unable to get local issuer certificate",
)

# OpenSSL X509_V_ERR_* verify codes, split by what the user must actually DO.
# Every code below is confirmed against a real local handshake — NOT read off a
# table: 62/10/9 all still stringify with "certificate verify failed", so the
# _TLS_MARKERS above cannot tell them apart from an untrusted issuer.
_HOSTNAME_MISMATCH_CODE = 62  # X509_V_ERR_HOSTNAME_MISMATCH

# Both halves of the validity window. OpenSSL raises 10 when the clock is past
# not_valid_after and 9 when it is before not_valid_before; a skewed local clock
# (a VM with a bad RTC, a laptop resuming from suspend) produces EITHER, so they
# share one remedy. 9 must not fall through to the untrusted-issuer default: the
# chain verified fine there, and no CA can help someone whose date is wrong.
_CERT_EXPIRED_CODE = 10  # X509_V_ERR_CERT_HAS_EXPIRED
_CERT_NOT_YET_VALID_CODE = 9  # X509_V_ERR_CERT_NOT_YET_VALID
_CLOCK_CODES: frozenset[int] = frozenset({_CERT_EXPIRED_CODE, _CERT_NOT_YET_VALID_CODE})


# The two halves of the untrusted-issuer remedy that hold regardless of which
# trust store is live. Assembled by :func:`_untrusted_issuer_hint`, which picks
# the middle sentence off the binding rather than asserting one.
_TLS_INTERCEPT_INTRO: str = (
    "TLS certificate verification failed — a proxy or firewall is likely "
    "intercepting HTTPS with a private root CA (common on corporate networks). "
)
_TLS_CERT_FILE_ESCAPE: str = (
    "point SSL_CERT_FILE at a bundle that includes it:\n"
    "  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt\n"
    "or append the corporate CA to the bundle printed by `python -m certifi`, "
    "then retry."
)

# Deliberately never mentions SSL_CERT_FILE: the chain verified fine, so a CA
# bundle is the wrong lever and offering it sends the user down a dead end.
_TLS_HOSTNAME_HINT: str = (
    "TLS certificate verification failed — the server presented a certificate "
    "that is not valid for the hostname aelix connected to. The trust chain "
    "itself is fine, so adding a CA will not help: check this provider's base "
    "URL for a typo, or a proxy/gateway answering for a different host."
)

# Covers BOTH clock codes: the recovered cause text already names which end of
# the window was violated ("certificate has expired" / "certificate is not yet
# valid"), so this states the shared remedy instead of guessing a direction.
_TLS_CLOCK_HINT: str = (
    "TLS certificate verification failed — the server's certificate is outside "
    "its validity window (expired, or not yet valid). The trust chain itself is "
    "fine, so adding a CA will not help: check this machine's clock first (a "
    "wrong system date puts a perfectly valid certificate outside its window in "
    "either direction), otherwise the endpoint's operator must renew it."
)


def _os_trust_store_active() -> bool:
    """True when ``truststore`` has rebound :class:`ssl.SSLContext` process-wide.

    Read LIVE off the binding rather than recorded when injection ran: the CLI's
    injection is best-effort (a missing wheel, an unsupported platform, or a
    backend that cannot reach the platform store all degrade to certifi
    silently), and embedders importing this library never inject at all. Only the
    binding itself knows which happened, so a flag set at startup can lie; class
    identity cannot. ``truststore.extract_from_ssl()`` restores the original
    class, and this follows it back.
    """

    return ssl.SSLContext.__module__.startswith("truststore")


def _untrusted_issuer_hint() -> str:
    """The private-CA remedy, worded for the trust store REALLY in effect.

    The OS-store sentence is derived from :func:`_os_trust_store_active`, never
    asserted as fact. Claiming "aelix trusts your OS certificate store" while the
    process is certifi-only dead-ends exactly the #99 user: they have ALREADY
    installed the CA system-wide — that is precisely why VS Code works for them —
    so they would be told the fix is the thing they already did, while
    SSL_CERT_FILE (the one remedy that works certifi-only) sits behind an "if it
    cannot be installed system-wide" conditional they will read as not applying.

    Untrusted issuer = X509_V_ERR_ 18 DEPTH_ZERO_SELF_SIGNED_CERT, 19
    SELF_SIGNED_CERT_IN_CHAIN, 20 UNABLE_TO_GET_ISSUER_CERT_LOCALLY — the #99
    shape. Also the default for an absent/unclassified verify code, so a
    synthetic error or a string-marker-only match keeps this advice.
    """

    if _os_trust_store_active():
        return (
            f"{_TLS_INTERCEPT_INTRO}aelix is trusting your operating system's "
            "certificate store, so installing that root CA system-wide is the "
            "fix — it is what already makes VS Code and your browser work on "
            "this network. If it cannot be installed system-wide (e.g. inside a "
            f"container), {_TLS_CERT_FILE_ESCAPE}"
        )
    return (
        f"{_TLS_INTERCEPT_INTRO}This process is verifying against certifi's "
        "public-root bundle ONLY — the operating system's certificate store is "
        "NOT being consulted, so a root CA installed system-wide (the reason VS "
        "Code and your browser work on this network) cannot help by itself. "
        f"Instead, {_TLS_CERT_FILE_ESCAPE}"
    )


def _causes(exc: BaseException) -> list[BaseException]:
    """The ATTRIBUTION chain: what this error is *about* (cycle-safe).

    Walks the chain the way :mod:`traceback` renders it: an explicit
    ``__cause__`` wins, and ``__suppress_context__`` (set by ``raise X from
    None``, and implicitly by any ``raise X from Y``) cuts the ``__context__``
    link. Without that cut, an unrelated error raised inside an ``except
    ssl.SSLCertVerificationError`` block inherits the TLS hint purely because it
    was handled nearby.

    NOT sufficient to locate the OpenSSL error itself: httpcore re-raises with
    ``raise exc from None`` (``_sync/connection_pool.py:256``, mirrored in
    ``_async``), which parks the real :class:`ssl.SSLCertVerificationError`
    behind exactly this cut on EVERY httpx-backed request. Diagnosis therefore
    uses :func:`_raw_chain`; only attribution uses this.
    """

    out: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        if cur.__cause__ is not None:
            cur = cur.__cause__
        elif cur.__suppress_context__:
            cur = None
        else:
            cur = cur.__context__
    return out


def _raw_chain(exc: BaseException) -> list[BaseException]:
    """Every linked exception, IGNORING ``__suppress_context__`` (cycle-safe).

    Only ever walked from an exception already attributed to a TLS failure by
    :func:`_causes`, where the question has narrowed from "what is this error
    about" to "which OpenSSL error is this same failure". ``__suppress_context__``
    answers the first question, not the second: httpcore sets it while
    re-raising the very error the context describes.
    """

    out: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        # ``is not None``, never ``a or b``: an exception subclass that defines
        # __len__/__bool__ can be falsy, which would silently skip a real cause.
        cur = cur.__cause__ if cur.__cause__ is not None else cur.__context__
    return out


def _tls_error(exc: BaseException) -> BaseException | None:
    """The TLS trust failure in ``exc``'s chain, or :data:`None`.

    Two chains, because attribution and diagnosis need different ones. The
    attribution pass respects ``__suppress_context__`` so an unrelated nearby
    error cannot claim a TLS cause. Only once a TLS failure IS attributed does
    the second pass cross a suppressed link, to reach the OpenSSL error carrying
    the ``verify_code`` that :func:`_tls_hint` branches on.

    That second pass is what makes the code branches reachable in production at
    all: the real chain is ``APIConnectionError → httpx.ConnectError →
    httpcore.ConnectError → ssl.SSLCertVerificationError``, and the first three
    match only on marker TEXT and carry no code. Returning the wrapper would pin
    every real request's hint to the untrusted-issuer default — telling a user
    with a hostname mismatch or a skewed clock to install a corporate root CA.
    """

    for e in _causes(exc):
        if isinstance(e, ssl.SSLCertVerificationError):
            return e
        if any(m in str(e) for m in _TLS_MARKERS):
            for inner in _raw_chain(e):
                if isinstance(inner, ssl.SSLCertVerificationError):
                    return inner
            return e
    return None


def is_tls_verification_error(exc: BaseException) -> bool:
    """True when ``exc`` (or anything in its cause chain) is a TLS trust failure."""

    return _tls_error(exc) is not None


def _tls_hint(err: BaseException) -> str:
    """The remedy matching ``err``'s OpenSSL verify code.

    ``verify_code`` / ``verify_message`` exist ONLY on OpenSSL-raised errors: a
    synthetic ``ssl.SSLCertVerificationError("...")`` and a string-marker match
    on a non-ssl wrapper both carry NEITHER, so the read must tolerate their
    absence and fall back to the untrusted-issuer remedy — that is the shape #99
    reported, and the shape every synthetic test constructs.
    """

    code = getattr(err, "verify_code", None)
    if code == _HOSTNAME_MISMATCH_CODE:
        return _TLS_HOSTNAME_HINT
    if code in _CLOCK_CODES:
        return _TLS_CLOCK_HINT
    return _untrusted_issuer_hint()


def _cause_text(exc: BaseException, base: str) -> str | None:
    """The innermost cause message ``base`` does not already carry.

    Innermost-first because the outer wrappers are the uninformative ones
    ("Connection error.", or an empty ``httpx.ConnectError``); the OpenSSL error
    at the bottom is what names the reason AND the host. Substring-checked
    against ``base`` so a directly-raised error is not repeated back to itself.
    """

    for e in reversed(_causes(exc)[1:]):
        text = str(e).strip()
        if text and text not in base:
            return text
    return None


def describe_provider_error(exc: BaseException) -> str:
    """Base message + the innermost real cause + a TLS remedy when relevant.

    Non-TLS errors keep their base message (plus the recovered cause), so this
    is a safe drop-in wherever an adapter builds
    ``err_msg = str(exc) if str(exc) else type(exc).__name__``.

    The cause is appended, never prepended: callers/tests anchor on the SDK's
    own leading text (e.g. ``startswith("Connection error.")``).
    """

    base = str(exc) if str(exc) else type(exc).__name__
    cause = _cause_text(exc, base)
    if cause is not None:
        base = f"{base} — {cause}"
    tls = _tls_error(exc)
    if tls is not None:
        return f"{base}\n\n{_tls_hint(tls)}"
    return base


__all__ = ["describe_provider_error", "is_tls_verification_error"]
