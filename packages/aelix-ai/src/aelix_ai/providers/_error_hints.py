"""Actionable hints for opaque provider transport errors.

The OpenAI / Anthropic SDKs surface a bare ``APIConnectionError("Connection
error.")`` whose real cause (an ``ssl.SSLCertVerificationError``) is buried in
the ``__cause__`` chain. On a corporate network that intercepts HTTPS with a
private root CA, EVERY request fails this way — and the user just sees
"Connection error." with no path forward. :func:`describe_provider_error`
detects that TLS-verification case and appends the concrete fix.

httpx (the transport under both SDKs and the OAuth flows) honors the standard
``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` OpenSSL environment variables, so pointing
``SSL_CERT_FILE`` at a CA bundle that includes the corporate root CA is the
no-code-change remedy.
"""

from __future__ import annotations

import ssl

# Substrings that mark a TLS trust failure across httpx / OpenSSL / SDK wrappers.
_TLS_MARKERS: tuple[str, ...] = (
    "CERTIFICATE_VERIFY_FAILED",
    "certificate verify failed",
    "self-signed certificate",
    "self signed certificate",
    "unable to get local issuer certificate",
)

_TLS_HINT: str = (
    "TLS certificate verification failed — a proxy or firewall is likely "
    "intercepting HTTPS with a private root CA (common on corporate networks). "
    "Point SSL_CERT_FILE at a CA bundle that includes that root CA, e.g.\n"
    "  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt\n"
    "or append the corporate CA to the bundle printed by "
    "`python -m certifi`, then retry."
)


def _causes(exc: BaseException) -> list[BaseException]:
    """The exception plus its ``__cause__`` / ``__context__`` chain (cycle-safe)."""

    out: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def is_tls_verification_error(exc: BaseException) -> bool:
    """True when ``exc`` (or anything in its cause chain) is a TLS trust failure."""

    for e in _causes(exc):
        if isinstance(e, ssl.SSLCertVerificationError):
            return True
        text = str(e)
        if any(marker in text for marker in _TLS_MARKERS):
            return True
    return False


def describe_provider_error(exc: BaseException) -> str:
    """Base message (``str(exc)`` or the type name) + the TLS hint when relevant.

    Non-TLS errors are returned unchanged, so this is a safe drop-in wherever an
    adapter builds ``err_msg = str(exc) if str(exc) else type(exc).__name__``.
    """

    base = str(exc) if str(exc) else type(exc).__name__
    if is_tls_verification_error(exc):
        return f"{base}\n\n{_TLS_HINT}"
    return base


__all__ = ["describe_provider_error", "is_tls_verification_error"]
