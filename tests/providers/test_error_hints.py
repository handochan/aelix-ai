"""Actionable TLS-trust hint for opaque provider "Connection error." failures.

On a corporate network that intercepts HTTPS with a private root CA, the SDKs
raise ``APIConnectionError("Connection error.")`` whose real cause — an
``ssl.SSLCertVerificationError`` — is buried in ``__cause__``. The adapters route
their error text through :func:`describe_provider_error`, which recovers the real
reason and appends the remedy matching the OpenSSL verify code (issue #99).

``verify_code`` / ``verify_message`` exist ONLY on OpenSSL-RAISED errors, so a
synthetic ``ssl.SSLCertVerificationError("...")`` carries neither. Tests that
exercise a verify-code branch must therefore either inject the attributes
(:func:`_openssl_error`) or drive a real handshake
(:func:`test_real_handshake_untrusted_issuer_reports_private_ca`) — asserting on
a synthetic error alone would let a never-firing branch pass.
"""

from __future__ import annotations

import socket
import ssl
import threading
from typing import Any

import pytest
from aelix_ai.providers._error_hints import (
    describe_provider_error,
    is_tls_verification_error,
)

# The stdlib class, captured at IMPORT time — i.e. during collection, before any
# test body can run ``truststore.inject_into_ssl()``. ``tests/cli/test_truststore
# .py`` injects for real and extracts in a ``finally``, so reading the binding
# inside a test body could see either class depending on ordering; this cannot.
_ORIGINAL_SSL_CONTEXT = ssl.SSLContext


def _wrap(outer: str, cause: BaseException) -> BaseException:
    """A plain one-hop ``__cause__`` link.

    Fine for the chain-agnostic assertions below, but NOT the shape a real
    request produces — use :func:`_sdk_chain` for anything that depends on
    reaching the OpenSSL error.
    """

    err = Exception(outer)
    err.__cause__ = cause
    return err


def _sdk_chain(inner: ssl.SSLCertVerificationError) -> BaseException:
    """The EXACT chain a real openai/httpx request produces for a TLS failure.

    Verified against a live handshake (see
    :func:`test_real_handshake_hostname_mismatch_through_the_sdk`)::

        openai.APIConnectionError("Connection error.")   cause=httpx.ConnectError
        └ httpx.ConnectError(<ssl text>)                 cause=httpcore.ConnectError
          └ httpcore.ConnectError(<ssl text>)            cause=None
                                                         context=<ssl error>
                                                         suppress_context=True
            └ ssl.SSLCertVerificationError               verify_code=<code>

    That third link is the whole point: httpcore's ``raise exc from None``
    (``_sync/connection_pool.py:256``) puts the ONLY ``verify_code`` carrier
    behind a ``__suppress_context__`` boundary, with ``__cause__`` cleared. A
    helper that chains ``__cause__`` straight through tests a shape no SDK emits,
    so a walker that stops at that boundary passes every ``_wrap``-based test
    while reaching NO verify code in production.
    """

    text = str(inner)
    # httpcore.ConnectError — re-raised via ``raise exc from None``.
    httpcore_like = Exception(text)
    httpcore_like.__context__ = inner
    httpcore_like.__suppress_context__ = True
    # httpx.ConnectError — ``raise mapped from exc`` (sets cause AND suppress).
    httpx_like = Exception(text)
    httpx_like.__cause__ = httpcore_like
    httpx_like.__suppress_context__ = True
    # openai.APIConnectionError — ``raise APIConnectionError(...) from err``.
    api_like = Exception("Connection error.")
    api_like.__cause__ = httpx_like
    api_like.__suppress_context__ = True
    return api_like


def _openssl_error(verify_code: int, verify_message: str) -> ssl.SSLCertVerificationError:
    """An ``SSLCertVerificationError`` shaped like one OpenSSL really raised.

    Mirrors the observed shape exactly: ``str(e)`` embeds ``verify_message``, and
    both attributes are present (a bare constructor sets NEITHER).
    """

    err = ssl.SSLCertVerificationError(
        f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        f"{verify_message} (_ssl.c:1000)"
    )
    err.verify_code = verify_code  # type: ignore[attr-defined]
    err.verify_message = verify_message  # type: ignore[attr-defined]
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


# === the cause chain is surfaced, never discarded (issue #99) ================


def test_non_tls_connection_error_names_its_cause_without_the_ca_hint() -> None:
    """A genuine network outage must NOT get the corporate hint — but must still
    say WHY. "Connection error." alone is the #99 bug: it names neither reason
    nor host, and the SDK's own text never carries either."""

    connect = Exception("[Errno 111] Connection refused")
    api = _wrap("Connection error.", connect)
    out = describe_provider_error(api)
    assert "SSL_CERT_FILE" not in out
    assert out.startswith("Connection error.")
    assert "Connection refused" in out


def test_bare_connection_error_never_appears_alone() -> None:
    """The exact string the #99 reporter saw must never be the whole message."""

    inner = ssl.SSLCertVerificationError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "unable to get local issuer certificate (_ssl.c:1000)"
    )
    api = _wrap("Connection error.", _wrap("", inner))
    out = describe_provider_error(api)
    assert out != "Connection error."
    assert "unable to get local issuer certificate" in out


def test_cause_not_duplicated_when_base_already_carries_it() -> None:
    """A directly-raised error must not have its own text appended back to it."""

    exc = ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")
    out = describe_provider_error(exc)
    assert out.split("\n\n")[0].count("self-signed certificate") == 1


def test_innermost_informative_cause_wins_over_empty_wrappers() -> None:
    """Empty wrappers (httpx.ConnectError) are skipped for the real reason."""

    inner = Exception("[Errno -2] Name or service not known")
    api = _wrap("Connection error.", _wrap("", _wrap("", inner)))
    out = describe_provider_error(api)
    assert "Name or service not known" in out


# === verify-code-aware hints (issue #99) ====================================


@pytest.mark.parametrize(
    "code,message",
    [
        (18, "self-signed certificate"),
        (19, "self-signed certificate in certificate chain"),
        (20, "unable to get local issuer certificate"),
    ],
)
def test_untrusted_issuer_codes_keep_private_ca_advice(code: int, message: str) -> None:
    """18/19/20 are the #99 shape: a private root CA is genuinely missing."""

    out = describe_provider_error(_sdk_chain(_openssl_error(code, message)))
    assert "SSL_CERT_FILE" in out
    assert "corporate" in out.lower()
    assert message in out


def test_hostname_mismatch_reports_real_cause_and_omits_ca_advice() -> None:
    """62 verifies the chain fine — the cert is for the wrong host. Advising
    SSL_CERT_FILE here sends the user to fix a CA that is not broken."""

    err = _openssl_error(
        62, "Hostname mismatch, certificate is not valid for 'api.openai.com'."
    )
    out = describe_provider_error(_sdk_chain(err))
    assert "SSL_CERT_FILE" not in out
    assert "corporate" not in out.lower()
    # the ACTUAL cause, and the host, must both reach the user
    assert "hostname" in out.lower()
    assert "api.openai.com" in out
    assert "base URL" in out


@pytest.mark.parametrize(
    "code,message",
    [
        (10, "certificate has expired"),
        # 9 is the clock-skew TWIN of 10 (a laptop resuming from suspend, a VM
        # with a bad RTC): the chain verified fine, so the untrusted-issuer
        # default would tell someone whose date is simply behind to install a
        # corporate root CA.
        (9, "certificate is not yet valid"),
    ],
)
def test_validity_window_codes_report_clock_and_omit_ca_advice(
    code: int, message: str
) -> None:
    """9/10 both verify the chain fine; a wrong local clock is the usual cause."""

    out = describe_provider_error(_sdk_chain(_openssl_error(code, message)))
    assert "SSL_CERT_FILE" not in out
    assert "corporate" not in out.lower()
    assert "clock" in out.lower()
    # the recovered cause names WHICH end of the window was violated
    assert message in out


def test_synthetic_error_without_verify_code_keeps_generic_hint() -> None:
    """The trap: a bare SSLCertVerificationError carries NO verify_code, so the
    attribute read must tolerate absence and keep today's behaviour."""

    exc = ssl.SSLCertVerificationError("certificate verify failed: some new reason")
    assert not hasattr(exc, "verify_code")
    out = describe_provider_error(exc)
    assert "SSL_CERT_FILE" in out
    assert "corporate" in out.lower()


def test_unclassified_verify_code_keeps_generic_hint() -> None:
    """An unmapped code is still a trust-chain problem — no regression.

    7 = certificate signature failure, which genuinely IS a trust-chain fault, so
    the untrusted-issuer default is right for it. Codes that verify the chain
    fine (62, 10, 9) are mapped explicitly above precisely because this default
    would mislead them.
    """

    out = describe_provider_error(_openssl_error(7, "certificate signature failure"))
    assert "SSL_CERT_FILE" in out


# === the CA hint describes the trust store really in effect (issue #99) ======


def test_ca_hint_promises_the_os_store_only_when_truststore_is_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With truststore live, installing the CA system-wide IS the fix."""

    truststore = pytest.importorskip("truststore")
    monkeypatch.setattr(ssl, "SSLContext", truststore.SSLContext)
    out = describe_provider_error(_sdk_chain(_openssl_error(18, "self-signed certificate")))
    assert "operating system's certificate store" in out
    assert "installing that root CA system-wide is the fix" in out


def test_ca_hint_admits_certifi_only_when_injection_did_not_happen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The degraded state must not tell the #99 reporter to redo what they did.

    ``_inject_truststore`` is best-effort (missing wheel / unsupported platform)
    and embedders never call it. Promising the OS store there sends the user who
    has ALREADY installed the CA system-wide — the reason VS Code works for them
    — to install it again, and buries the one remedy that works certifi-only
    behind an "if it cannot be installed system-wide" conditional they will read
    as not applying to them.
    """

    monkeypatch.setattr(ssl, "SSLContext", _ORIGINAL_SSL_CONTEXT)
    out = describe_provider_error(_sdk_chain(_openssl_error(18, "self-signed certificate")))
    assert "NOT being consulted" in out
    assert "installing that root CA system-wide is the fix" not in out
    # the remedy that DOES work certifi-only must be the unconditional one
    assert "SSL_CERT_FILE" in out
    assert "If it cannot be installed system-wide" not in out


# === __suppress_context__ (issue #99) =======================================


def test_suppressed_context_does_not_inherit_the_tls_hint() -> None:
    """``raise X from None`` inside an ``except ssl.SSLCertVerificationError``
    block severs the chain: the new error is unrelated and must not claim a TLS
    cause just because it was raised nearby."""

    try:
        try:
            raise ssl.SSLCertVerificationError(
                "certificate verify failed: self-signed certificate"
            )
        except ssl.SSLCertVerificationError:
            raise RuntimeError("unrelated bug") from None
    except RuntimeError as exc:
        assert is_tls_verification_error(exc) is False
        out = describe_provider_error(exc)
        assert out == "unrelated bug"
        assert "SSL_CERT_FILE" not in out


def test_explicit_cause_still_followed_through_a_raise_from() -> None:
    """``raise X from err`` (what openai's _base_client does for connection
    errors) sets __cause__ AND __suppress_context__ — the cause must still win,
    or #99 detection dies."""

    try:
        try:
            raise ssl.SSLCertVerificationError(
                "certificate verify failed: unable to get local issuer certificate"
            )
        except ssl.SSLCertVerificationError as err:
            raise RuntimeError("Connection error.") from err
    except RuntimeError as exc:
        assert exc.__suppress_context__ is True  # guards the traversal order
        assert is_tls_verification_error(exc) is True
        assert "SSL_CERT_FILE" in describe_provider_error(exc)


# === real handshake: proves verify_code fires outside a fixture =============


def _serve_tls_once(certfile: str, port_holder: list[int], ready: threading.Event) -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port_holder.append(sock.getsockname()[1])
    sock.listen(1)
    sock.settimeout(10)
    ready.set()
    try:
        conn, _ = sock.accept()
        try:
            with ctx.wrap_socket(conn, server_side=True):
                pass
        except OSError:
            pass  # the client aborts on verify failure — that IS the test
    except (TimeoutError, OSError):
        pass
    finally:
        sock.close()


def test_real_handshake_untrusted_issuer_reports_private_ca(tmp_path: Any) -> None:
    """Drive a REAL failing handshake end to end.

    Every other verify-code test injects the attributes, so all of them would
    still pass if OpenSSL never populated ``verify_code`` in production. This one
    would not: it asserts the attribute arrives from a genuine handshake, which
    is what makes the branch in :func:`_tls_hint` more than decoration.
    """

    cryptography = pytest.importorskip("cryptography")
    assert cryptography  # importorskip returns the module; keep ruff quiet
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "aelix.test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("aelix.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = tmp_path / "server.pem"
    pem.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    port_holder: list[int] = []
    ready = threading.Event()
    server = threading.Thread(
        target=_serve_tls_once, args=(str(pem), port_holder, ready), daemon=True
    )
    server.start()
    assert ready.wait(timeout=10), "TLS test server did not start"

    caught: ssl.SSLCertVerificationError | None = None
    try:
        # A default client context = what httpx/the SDKs build for a turn.
        with socket.create_connection(
            ("127.0.0.1", port_holder[0]), timeout=10
        ) as raw, ssl.create_default_context().wrap_socket(
            raw, server_hostname="aelix.test"
        ):
            pass
    except ssl.SSLCertVerificationError as exc:
        caught = exc
    finally:
        server.join(timeout=10)

    assert caught is not None, "self-signed cert unexpectedly verified"
    # OpenSSL populated these; no fixture did.
    assert caught.verify_code == 18
    assert caught.verify_message == "self-signed certificate"

    # And the production entry point turns that into the private-CA remedy.
    out = describe_provider_error(_wrap("Connection error.", caught))
    assert out.startswith("Connection error.")
    assert "self-signed certificate" in out
    assert "SSL_CERT_FILE" in out
    assert "corporate" in out.lower()


# === real SDK request: proves the chain walk reaches verify_code in PRODUCTION =


def test_real_handshake_hostname_mismatch_through_the_sdk(tmp_path: Any) -> None:
    """Drive a REAL openai SDK request at a REAL TLS server and assert the hint.

    The one test that binds the walk in :func:`_causes` / :func:`_tls_error` to
    reality. Everything above either injects ``verify_code`` or catches the ssl
    error straight off ``wrap_socket``, bypassing httpx/httpcore/the SDK — so all
    of it passes even when the OpenSSL error is unreachable through a real
    request, because httpcore's ``raise exc from None`` parks it behind a
    ``__suppress_context__`` boundary and the wrappers still repeat the marker
    text.

    The server's CA is trusted explicitly, so a hostname mismatch is the ONLY
    defect: the chain verifies fine, and any answer mentioning a CA is wrong.
    """

    pytest.importorskip("cryptography")
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    import datetime
    import http.server

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # CN/SAN deliberately != the "localhost" the client will dial.
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wrong.example")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("wrong.example")]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = tmp_path / "server.pem"
    pem.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    ca_pem = tmp_path / "ca.pem"
    ca_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(pem))
    server = http.server.HTTPServer(
        ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler
    )
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Trust the server's own cert as a CA, so the chain verifies and the
        # hostname is the ONLY thing wrong.
        client_ctx = ssl.create_default_context(cafile=str(ca_pem))
        client = openai.OpenAI(
            api_key="unused",
            base_url=f"https://localhost:{server.server_address[1]}/v1",
            http_client=httpx.Client(verify=client_ctx),
            max_retries=0,
        )
        with pytest.raises(openai.APIConnectionError) as excinfo:
            client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "hi"}]
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    caught = excinfo.value
    # The shape _sdk_chain models — asserted here so that helper cannot drift
    # from the SDK without this failing.
    assert isinstance(caught.__cause__, httpx.ConnectError)
    transport = caught.__cause__.__cause__
    assert transport is not None
    assert transport.__cause__ is None
    assert transport.__suppress_context__ is True
    assert isinstance(transport.__context__, ssl.SSLCertVerificationError)
    assert transport.__context__.verify_code == 62

    out = describe_provider_error(caught)
    assert is_tls_verification_error(caught) is True
    assert out.startswith("Connection error.")
    # The trust chain verified — CA advice here is the #99 defect inverted.
    assert "SSL_CERT_FILE" not in out
    assert "corporate" not in out.lower()
    assert "base URL" in out
    assert "hostname" in out.lower()
