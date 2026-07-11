"""Actionable TLS-trust hint for opaque provider "Connection error." failures.

On a corporate network that intercepts HTTPS with a private root CA, the SDKs
raise ``APIConnectionError("Connection error.")`` whose real cause — an
``ssl.SSLCertVerificationError`` — is buried in ``__cause__``. The adapters now
route their error text through :func:`describe_provider_error`, which appends the
``SSL_CERT_FILE`` fix when it detects that case.
"""

from __future__ import annotations

import ssl

from aelix_ai.providers._error_hints import (
    describe_provider_error,
    is_tls_verification_error,
)


def _wrap(outer: str, cause: BaseException) -> BaseException:
    err = Exception(outer)
    err.__cause__ = cause
    return err


def test_plain_error_is_unchanged() -> None:
    exc = ValueError("something mundane")
    assert describe_provider_error(exc) == "something mundane"
    assert is_tls_verification_error(exc) is False


def test_empty_message_falls_back_to_type_name() -> None:
    class _Boom(Exception):
        pass

    assert describe_provider_error(_Boom()) == "_Boom"


def test_direct_ssl_cert_error_gets_hint() -> None:
    exc = ssl.SSLCertVerificationError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self-signed certificate in certificate chain (_ssl.c:1000)"
    )
    out = describe_provider_error(exc)
    assert is_tls_verification_error(exc) is True
    assert "SSL_CERT_FILE" in out
    assert "corporate" in out.lower()
    # base message preserved
    assert "certificate verify failed" in out


def test_wrapped_connection_error_chain_gets_hint() -> None:
    """The real shape: APIConnectionError("Connection error.") → ConnectError → SSLError."""

    inner = ssl.SSLCertVerificationError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self-signed certificate in certificate chain"
    )
    connect = _wrap("", inner)  # httpx.ConnectError stand-in (empty message)
    api = _wrap("Connection error.", connect)  # openai.APIConnectionError stand-in
    out = describe_provider_error(api)
    assert out.startswith("Connection error.")
    assert "SSL_CERT_FILE" in out


def test_string_marker_without_ssl_type_gets_hint() -> None:
    """A wrapper that only carries the marker text (no SSLError type) still triggers."""

    exc = Exception("curl-like: unable to get local issuer certificate")
    assert is_tls_verification_error(exc) is True
    assert "SSL_CERT_FILE" in describe_provider_error(exc)


def test_context_chain_is_followed_and_cycle_safe() -> None:
    inner = ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")
    outer = Exception("boom")
    outer.__context__ = inner  # raised during handling of inner (implicit chaining)
    # self-reference must not hang
    inner.__context__ = inner
    assert is_tls_verification_error(outer) is True


def test_non_tls_connection_error_stays_plain() -> None:
    """A genuine network outage (no cert marker) must NOT get the corporate hint."""

    connect = Exception("[Errno 111] Connection refused")
    api = _wrap("Connection error.", connect)
    out = describe_provider_error(api)
    assert "SSL_CERT_FILE" not in out
    assert out == "Connection error."
