"""#411: a TLS failure the OS verifier reports gets the remedy an OpenSSL one gets.

On macOS and Windows ``truststore`` verifies the server's chain with the
operating system's verifier, not OpenSSL. The error it raises is still an
``ssl.SSLCertVerificationError``, but its text is the OS's
(``'“localhost” certificate is not trusted'`` on macOS) and its
``verify_code`` is the OS's (a negative OSStatus on macOS, an HRESULT read as
a DWORD on Windows). Through httpx that error sits behind httpcore's
``raise exc from None``, and the wrappers above it repeat its text, which names
none of OpenSSL's markers. So nothing attributed it: ``aelix -p`` printed
``Connection error. — ('“localhost” certificate is not trusted',)`` and no
remedy (measured on macOS 26.6 with truststore 0.10.4 against a local server
whose self-signed certificate no keychain holds).

The rows below drive the real stack: httpx, httpcore and the OpenAI SDK, and
truststore's own ``SSLContext``. The OS verifier itself only exists on macOS
and Windows, so the rows that need it run there; everywhere else, including
the Linux CI legs, truststore's ``_verify_peercerts_impl`` - the one function
each backend implements - is replaced by one that raises exactly what the
macOS or Windows backend raises (``truststore/_macos.py``,
``truststore/_windows.py``), so the chain above it is the real one on every
runner. The Windows texts are FormatMessageW's en-US text for each code, read
off the table: no Windows box ran them, and they are localized there, which is
why the code - not the text - decides the remedy.

Review round 2 holds every remedy to where it is true: an HTTP status error's
body is never read as a TLS failure, an OS verdict no remedy fits gets none
(macOS rejecting the certificate itself; macOS <= 10.13's result codes, which
name no cause), a server's alert about OUR certificate is not a verification
failure, and the ``SSL_CERT_FILE`` example is the platform's own.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import socket
import ssl
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from aelix_ai.providers._error_hints import describe_provider_error, is_tls_verification_error
from aelix_ai.providers._tls_strict import reset_strict_relaxation, strict_is_enabled

pytest.importorskip("cryptography")
httpx = pytest.importorskip("httpx")
openai = pytest.importorskip("openai")
truststore = pytest.importorskip("truststore")

# Captured at import, before any test can inject truststore process-wide.
_STDLIB_SSL_CONTEXT = ssl.SSLContext
_STRICT = ssl.VerifyFlags.VERIFY_X509_STRICT

#: A sentence each remedy has and no other remedy has.
_REMEDY_TEXT: dict[str, str] = {
    "ca": "a proxy or firewall is likely intercepting HTTPS with a private root CA",
    "hostname": "not valid for the hostname aelix connected to",
    "clock": "outside its validity window",
    "strict": "on an RFC 5280 conformance rule, not on trust",
    "hedged": "it alone cannot tell which",
}


def _remedy(text: str) -> str:
    """Which remedy ``text`` carries: one of :data:`_REMEDY_TEXT`, or ``none``."""

    found = [kind for kind, sentence in _REMEDY_TEXT.items() if sentence in text]
    if not found:
        assert "TLS certificate verification failed" not in text, text
        return "none"
    assert len(found) == 1, f"two remedies at once: {found}\n{text}"
    return found[0]


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_strict_relaxation()
    yield
    reset_strict_relaxation()


# ── certificates and servers ────────────────────────────────────────────────


@dataclass(frozen=True)
class _Pki:
    ca: Path
    #: leaf (+ key) for each defect, signed by ``ca``; the server sends no CA.
    chains: dict[str, Path]
    #: leaves macOS rejects on their own, whatever the issuer (review round 2);
    #: served only where the real macOS verifier runs.
    rejected: dict[str, Path]


def _write_pki(tmp: Path) -> _Pki:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "aelix-411 test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = tmp / "ca.pem"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    def leaf_chain(
        label: str,
        host: str,
        not_before: datetime.datetime,
        not_after: datetime.datetime,
        *,
        key_size: int = 2048,
        usage: x509.ObjectIdentifier = ExtendedKeyUsageOID.SERVER_AUTH,
    ) -> Path:
        key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        san: list[x509.GeneralName] = [x509.DNSName(host)]
        leaf = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        chain = tmp / f"{label}.pem"
        chain.write_bytes(
            leaf.public_bytes(serialization.Encoding.PEM)
            + key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        return chain

    # macOS refuses a leaf with no serverAuth EKU ('not permitted for this
    # usage', -67609, measured), so every leaf here but that one carries one.
    day = datetime.timedelta(days=1)
    chains = {
        "ok": leaf_chain("ok", "localhost", now - day, now + 30 * day),
        "hostname": leaf_chain("hostname", "other.example", now - day, now + 30 * day),
        "expired": leaf_chain("expired", "localhost", now - 60 * day, now - 30 * day),
    }
    rejected = {
        "validity-900-days": leaf_chain(
            "validity-900-days", "localhost", now - day, now + 900 * day
        ),
        "rsa-1024": leaf_chain("rsa-1024", "localhost", now - day, now + 30 * day, key_size=1024),
        "client-auth-only": leaf_chain(
            "client-auth-only",
            "localhost",
            now - day,
            now + 30 * day,
            usage=ExtendedKeyUsageOID.CLIENT_AUTH,
        ),
    }
    return _Pki(ca=ca_pem, chains=chains, rejected=rejected)


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    return _write_pki(tmp_path_factory.mktemp("pki411"))


def _listen() -> socket.socket:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    return srv


def _serve(srv: socket.socket, handle: Callable[[socket.socket], None]) -> None:
    def loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=_guarded, args=(handle, conn), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()


def _guarded(handle: Callable[[socket.socket], None], conn: socket.socket) -> None:
    try:
        with conn:
            handle(conn)
    except OSError:
        pass  # the client gave up on the handshake: that is what most rows test


def _tls_handler(chain: Path, *, any_key_size: bool = False) -> Callable[[socket.socket], None]:
    context = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_SERVER)
    if any_key_size:
        # OpenSSL's default security level refuses to load an RSA-1024 key.
        context.set_ciphers("ALL:@SECLEVEL=0")
    context.load_cert_chain(str(chain))

    def handle(conn: socket.socket) -> None:
        with context.wrap_socket(conn, server_side=True) as tls:
            tls.recv(4096)
            tls.sendall(
                b"HTTP/1.1 500 X\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
            )

    return handle


@pytest.fixture(scope="module")
def tls_servers(pki: _Pki) -> Iterator[dict[str, int]]:
    """A TLS server per leaf in :class:`_Pki`, answering any request with a 500."""

    listeners = {label: _listen() for label in pki.chains}
    for label, srv in listeners.items():
        _serve(srv, _tls_handler(pki.chains[label]))
    yield {label: int(srv.getsockname()[1]) for label, srv in listeners.items()}
    for srv in listeners.values():
        srv.close()


def _openai_error(port: int, http_client: object, *, host: str = "localhost") -> BaseException:
    """The exception a real OpenAI SDK request raises against ``host:port``."""

    client = openai.OpenAI(
        api_key="sk-fake-411",
        base_url=f"https://{host}:{port}/v1",
        http_client=http_client,  # type: ignore[arg-type]
        max_retries=0,
    )
    with pytest.raises(openai.APIConnectionError) as excinfo:
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    return excinfo.value


async def _async_openai_error(port: int, context: ssl.SSLContext) -> BaseException:
    """The same through the async client, which is what the adapters use."""

    async with httpx.AsyncClient(verify=context, timeout=10) as http_client:
        client = openai.AsyncOpenAI(
            api_key="sk-fake-411",
            base_url=f"https://localhost:{port}/v1",
            http_client=http_client,
            max_retries=0,
        )
        try:
            await client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "hi"}]
            )
        except openai.APIConnectionError as exc:
            return exc
    raise AssertionError("the request unexpectedly succeeded")


def _verification_error(caught: BaseException) -> BaseException | None:
    """The error behind httpcore's ``from None``: APIConnectionError → httpx → httpcore → it."""

    mapped = caught.__cause__
    transport = mapped.__cause__ if mapped is not None else None
    return transport.__context__ if transport is not None else None


# ── the premise: httpcore carries the error it re-raises from None ─────────


def test_httpcore_re_raises_the_error_it_carries_from_none(
    pki: _Pki, tls_servers: dict[str, int]
) -> None:
    """The shape :func:`_carries` reads, measured on whatever OS runs this.

    An OpenSSL failure is enough to measure it - the shape is httpcore's, not
    the verifier's. If httpcore stopped passing the error it maps as its
    argument, the OS-verifier rows below would go red here first, by name.
    """

    context = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_CLIENT)  # trusts nothing
    caught = _openai_error(tls_servers["ok"], httpx.Client(verify=context, timeout=10))

    mapped = caught.__cause__
    assert mapped is not None
    assert isinstance(mapped, httpx.ConnectError)
    transport = mapped.__cause__
    assert transport is not None
    assert transport.__cause__ is None
    assert transport.__suppress_context__ is True
    inner = transport.__context__
    assert isinstance(inner, ssl.SSLCertVerificationError)
    assert transport.args[0] is inner

    from aelix_ai.providers._error_hints import _carries

    assert _carries(transport, inner)


# ── the OS verifier's own errors, through the real stack, on every OS ───────


@dataclass(frozen=True)
class _OsFailure:
    platform: str
    code: int
    message: str
    remedy: str


#: What each backend raises, and the remedy it earns. The macOS rows are
#: measured (macOS 26.6, truststore 0.10.4, the probe CA as the only anchor);
#: the Windows rows are read off winerror.h and truststore/_windows.py.
_OS_FAILURES: dict[str, _OsFailure] = {
    "macos-untrusted": _OsFailure("macos", -67843, "“localhost” certificate is not trusted", "ca"),
    # The server sent no CA: errSecCreateChainFailed, the same text (measured).
    "macos-chain-incomplete": _OsFailure(
        "macos", -25318, "“localhost” certificate is not trusted", "ca"
    ),
    "macos-hostname": _OsFailure(
        "macos", -67602, "“other.example” certificate name does not match input", "hostname"
    ),
    # macOS reports a certificate that is not yet valid as expired: same code,
    # same text (measured).
    "macos-expired": _OsFailure("macos", -67818, "“localhost” certificate is expired", "clock"),
    "macos-not-yet-valid": _OsFailure(
        "macos", -67819, "“localhost” certificate is not yet valid", "clock"
    ),
    "windows-untrusted-root": _OsFailure(
        "windows",
        0x800B0109,
        "A certificate chain processed, but terminated in a root certificate which is not "
        "trusted by the trust provider.",
        "ca",
    ),
    "windows-chaining": _OsFailure(
        "windows",
        0x800B010A,
        "A certificate chain could not be built to a trusted root authority.",
        "ca",
    ),
    "windows-cn-no-match": _OsFailure(
        "windows",
        0x800B010F,
        "The certificate's CN name does not match the passed value.",
        "hostname",
    ),
    "windows-expired": _OsFailure(
        "windows",
        0x800B0101,
        "A required certificate is not within its validity period when verifying against the "
        "current system clock or the timestamp in the signed file.",
        "clock",
    ),
    # truststore's own text when FormatMessageW has none for the code.
    "windows-no-system-text": _OsFailure(
        "windows", 0x800B0109, "Certificate chain policy error 0x800b0109 [0]", "ca"
    ),
    # truststore's fallback text names the code; for a wrong host or a wrong
    # date it is no untrusted issuer, and the code still decides (round 2).
    "windows-cn-no-match-no-system-text": _OsFailure(
        "windows", 0x800B010F, "Certificate chain policy error 0x800b010f [0]", "hostname"
    ),
    "windows-expired-no-system-text": _OsFailure(
        "windows", 0x800B0101, "Certificate chain policy error 0x800b0101 [0]", "clock"
    ),
    # A localized system: no marker in the text at all - the code decides.
    "windows-localized-root": _OsFailure(
        "windows",
        0x800B0109,
        "Eine Zertifikatkette wurde verarbeitet, endete jedoch mit einem Stammzertifikat, "
        "das vom Vertrauensanbieter nicht als vertrauenswürdig angesehen wird.",
        "ca",
    ),
    "windows-localized-name": _OsFailure(
        "windows",
        0x800B010F,
        "Der CN-Name des Zertifikats stimmt nicht mit dem Wert überein.",
        "hostname",
    ),
    # Review round 2: macOS rejects the certificate ITSELF - no CA install fixes
    # these, so no remedy (measured codes and texts, macOS 26.6).
    "macos-validity-too-long": _OsFailure(
        "macos", -67901, "“localhost” certificate is not standards compliant", "none"
    ),
    "macos-key-size": _OsFailure(
        "macos", -67735, "“localhost” certificate is using a broken key size", "none"
    ),
    "macos-no-server-auth": _OsFailure(
        "macos", -67609, "“localhost” certificate is not permitted for this usage", "none"
    ),
    # Review round 2: macOS <= 10.13, where the code is the SecTrustResultType
    # and names no cause - 5 for an untrusted root, a wrong host and an expired
    # leaf alike (measured with the branch forced), 3 for a user's "never
    # trust", 6 a fatal failure (3 and 6 read off truststore's table).
    "macos-10.13-recoverable": _OsFailure("macos", 5, "Recoverable trust failure occurred", "none"),
    "macos-10.13-deny": _OsFailure(
        "macos", 3, "User specified that certificate is not trusted", "none"
    ),
    "macos-10.13-fatal": _OsFailure("macos", 6, "Fatal trust failure occurred", "none"),
}


def _raise_like_the_backend(failure: _OsFailure) -> Callable[..., None]:
    """A ``_verify_peercerts_impl`` raising what the named backend raises."""

    def verify(
        _ctx: ssl.SSLContext, _chain: list[bytes], server_hostname: str | None = None
    ) -> None:
        err = ssl.SSLCertVerificationError(failure.message)
        err.verify_message = failure.message  # type: ignore[attr-defined]
        err.verify_code = failure.code  # type: ignore[attr-defined]
        if failure.platform == "windows":
            raise err from None  # _windows.py:_get_and_verify_cert_chain
        raise err  # _macos.py:_verify_peercerts_impl_macos_10_14

    return verify


def _os_verifier_context(
    monkeypatch: pytest.MonkeyPatch, pki: _Pki, failure: _OsFailure
) -> ssl.SSLContext:
    """truststore's real context whose backend reports ``failure``.

    The CA is loaded so the OpenSSL handshake under truststore's Linux backend
    passes and the verify step - the one each backend implements - runs; on
    macOS and Windows truststore turns OpenSSL's verification off itself.
    """

    import truststore._api as api

    monkeypatch.setattr(api, "_verify_peercerts_impl", _raise_like_the_backend(failure))
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(pki.ca))
    return context


@pytest.mark.parametrize("name", list(_OS_FAILURES))
def test_an_os_verifier_failure_gets_the_remedy_its_code_earns(
    name: str, pki: _Pki, tls_servers: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED before #411: every row printed the bare error and no remedy. The
    ``none`` rows (review round 2) were green there and RED at round 1, which
    gave every one of them the corporate-CA remedy."""

    failure = _OS_FAILURES[name]
    context = _os_verifier_context(monkeypatch, pki, failure)
    caught = _openai_error(tls_servers["ok"], httpx.Client(verify=context, timeout=10))

    out = describe_provider_error(caught)
    assert is_tls_verification_error(caught) is True
    assert out.startswith("Connection error.")
    assert failure.message in out
    assert _remedy(out) == failure.remedy, out


@pytest.mark.parametrize(
    "name", ["macos-untrusted", "windows-untrusted-root", "windows-cn-no-match"]
)
def test_the_async_client_the_adapters_use_gets_it_too(
    name: str, pki: _Pki, tls_servers: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """anyio's TLS stream (``wrap_bio``) is a different path to the same verify step."""

    failure = _OS_FAILURES[name]
    context = _os_verifier_context(monkeypatch, pki, failure)
    caught = asyncio.run(_async_openai_error(tls_servers["ok"], context))

    out = describe_provider_error(caught)
    assert failure.message in out
    assert _remedy(out) == failure.remedy, out


@pytest.mark.parametrize("name", list(_OS_FAILURES))
def test_with_strict_on_an_os_verifier_failure_never_gets_the_strict_remedy(
    name: str, pki: _Pki, tls_servers: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#192's split holds: strict is OpenSSL's flag, and truststore verifies with
    the OS there - OpenSSL's verification is off, so strict cannot be the cause."""

    failure = _OS_FAILURES[name]
    context = _os_verifier_context(monkeypatch, pki, failure)
    caught = _openai_error(tls_servers["ok"], httpx.Client(verify=context, timeout=10))

    real = ssl.create_default_context

    def strict_factory(*a: object, **k: object) -> ssl.SSLContext:
        made = real(*a, **k)  # type: ignore[arg-type]
        made.verify_flags |= _STRICT
        return made

    monkeypatch.setattr(ssl, "create_default_context", strict_factory)
    assert strict_is_enabled() is True
    out = describe_provider_error(caught)
    assert _remedy(out) == failure.remedy, out


# ── the OS verifier itself, where there is one ─────────────────────────────


_NO_OS_VERIFIER = pytest.mark.skipif(
    sys.platform not in ("darwin", "win32"),
    reason="truststore verifies with OpenSSL on this platform; the OS verifier exists on macOS and Windows",
)


@_NO_OS_VERIFIER
def test_the_real_os_verifier_s_untrusted_chain_gets_the_ca_remedy(
    tls_servers: dict[str, int],
) -> None:
    """The reproduction itself: no CA anywhere trusts the server's chain."""

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    caught = _openai_error(tls_servers["ok"], httpx.Client(verify=context, timeout=10))

    inner = _verification_error(caught)
    assert isinstance(inner, ssl.SSLCertVerificationError)
    code = getattr(inner, "verify_code", None)
    out = describe_provider_error(caught)
    assert is_tls_verification_error(caught) is True, (code, out)
    assert _remedy(out) == "ca", (code, out)
    if sys.platform == "darwin":
        # The server sends no CA, so macOS cannot build the chain at all:
        # errSecCreateChainFailed (measured). With the CA sent it is -67843.
        assert code == -25318, out


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "needs the OS verifier to honour a CA the context loads; on Windows "
        "truststore re-raises the system engine's untrusted-root error when the "
        "loaded CA's engine also fails (truststore/_windows.py), so a wrong host "
        "or an expired leaf under a loaded CA reads as untrusted there"
    ),
)
@pytest.mark.parametrize(
    ("leaf", "code", "remedy"), [("hostname", -67602, "hostname"), ("expired", -67818, "clock")]
)
def test_the_real_macos_verifier_s_codes_get_their_remedies(
    leaf: str, code: int, remedy: str, pki: _Pki, tls_servers: dict[str, int]
) -> None:
    """The probe CA is the only anchor, so the named defect is the only one."""

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(pki.ca))
    caught = _openai_error(tls_servers[leaf], httpx.Client(verify=context, timeout=10))

    inner = _verification_error(caught)
    assert getattr(inner, "verify_code", None) == code
    out = describe_provider_error(caught)
    assert _remedy(out) == remedy, out


_MACOS_ONLY = pytest.mark.skipif(
    sys.platform != "darwin", reason="the real macOS verifier exists only on macOS"
)


@pytest.fixture(scope="module")
def rejected_servers(pki: _Pki) -> Iterator[dict[str, int]]:
    """A TLS server per leaf in :attr:`_Pki.rejected` (macOS rows only)."""

    listeners = {label: _listen() for label in pki.rejected}
    for label, srv in listeners.items():
        _serve(srv, _tls_handler(pki.rejected[label], any_key_size=True))
    yield {label: int(srv.getsockname()[1]) for label, srv in listeners.items()}
    for srv in listeners.values():
        srv.close()


@_MACOS_ONLY
@pytest.mark.parametrize(
    ("leaf", "anchored", "code"),
    [
        ("validity-900-days", True, -67901),
        ("validity-900-days", False, -67901),
        ("rsa-1024", True, -67735),
        ("rsa-1024", False, -67735),
        ("client-auth-only", True, -67609),
    ],
)
def test_a_certificate_macos_rejects_on_its_own_gets_no_remedy(
    leaf: str, anchored: bool, code: int, pki: _Pki, rejected_servers: dict[str, int]
) -> None:
    """Review round 2: no CA install satisfies these rules. RED at round 1, which
    gave each the corporate-CA remedy - for the anchored rows, a CA the request
    had already trusted. Unanchored, macOS still reports the certificate's own
    defect first (measured), so the chain is not known to be fine either."""

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if anchored:
        context.load_verify_locations(cafile=str(pki.ca))
    caught = _openai_error(rejected_servers[leaf], httpx.Client(verify=context, timeout=10))

    inner = _verification_error(caught)
    assert getattr(inner, "verify_code", None) == code
    out = describe_provider_error(caught)
    assert is_tls_verification_error(caught) is True
    assert str(inner) in out
    assert _remedy(out) == "none", out


@_MACOS_ONLY
@pytest.mark.parametrize(
    ("leaf", "anchored"),
    [("ok", False), ("hostname", True), ("expired", True)],
    ids=["untrusted", "hostname", "expired"],
)
def test_macos_10_13_s_verifier_names_no_cause_and_gets_no_remedy(
    leaf: str,
    anchored: bool,
    pki: _Pki,
    tls_servers: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2: truststore's SecTrustEvaluate branch (macOS <= 10.13),
    forced on this Mac. An untrusted root, a wrong host and an expired leaf all
    come back as 'Recoverable trust failure occurred', code 5 - which also is
    OpenSSL's code for another failure, so only the text says which path this
    is. RED at round 1: the CA remedy for all three."""

    import truststore._macos as macos_backend

    monkeypatch.setattr(macos_backend, "_is_macos_version_10_14_or_later", False)
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if anchored:
        context.load_verify_locations(cafile=str(pki.ca))
    caught = _openai_error(tls_servers[leaf], httpx.Client(verify=context, timeout=10))

    inner = _verification_error(caught)
    assert getattr(inner, "verify_code", None) == 5
    assert getattr(inner, "verify_message", None) == "Recoverable trust failure occurred"
    out = describe_provider_error(caught)
    assert is_tls_verification_error(caught) is True
    assert _remedy(out) == "none", out


# ── no false positives: a connection that fails for another reason ─────────


def _plain_context() -> ssl.SSLContext:
    return _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_CLIENT)


def _assert_no_remedy(caught: BaseException) -> str:
    out = describe_provider_error(caught)
    assert is_tls_verification_error(caught) is False, out
    assert _remedy(out) == "none", out
    return out


@pytest.mark.parametrize("client", ["sync", "async"])
def test_a_refused_connection_gets_no_tls_remedy_and_the_same_words(client: str) -> None:
    """Only TLS attribution looks behind httpcore's ``from None``.

    The cause text does not: crossing that cut for every error made the async
    client's refusal read ``multiple connection attempts failed (2
    sub-exceptions)`` - anyio's group behind its ``All connection attempts
    failed`` - instead of what httpx says (measured on the first draft).
    """

    srv = _listen()
    port = int(srv.getsockname()[1])
    srv.close()  # nothing listens there now
    if client == "sync":
        caught = _openai_error(port, httpx.Client(verify=_plain_context(), timeout=10))
    else:
        caught = asyncio.run(_async_openai_error(port, _plain_context()))
    out = _assert_no_remedy(caught)
    assert out == f"Connection error. — {caught.__cause__}"


def test_a_failed_name_lookup_gets_no_tls_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_such_host(*_a: object, **_k: object) -> list[object]:
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")

    monkeypatch.setattr(socket, "getaddrinfo", no_such_host)
    caught = _openai_error(
        443, httpx.Client(verify=_plain_context(), timeout=10), host="api.example"
    )
    out = _assert_no_remedy(caught)
    assert "nodename nor servname" in out


def test_a_handshake_that_times_out_gets_no_tls_remedy() -> None:
    srv = _listen()
    release = threading.Event()

    def hold(_conn: socket.socket) -> None:
        release.wait(10)  # accepts, never answers

    _serve(srv, hold)
    try:
        with pytest.raises(openai.APITimeoutError) as excinfo:
            openai.OpenAI(
                api_key="sk-fake-411",
                base_url=f"https://localhost:{srv.getsockname()[1]}/v1",
                http_client=httpx.Client(verify=_plain_context(), timeout=0.5),
                max_retries=0,
            ).chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    finally:
        release.set()
        srv.close()
    _assert_no_remedy(excinfo.value)


def test_a_proxy_that_answers_407_gets_no_tls_remedy() -> None:
    srv = _listen()

    def refuse(conn: socket.socket) -> None:
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")

    _serve(srv, refuse)
    try:
        client = httpx.Client(
            verify=_plain_context(), timeout=10, proxy=f"http://127.0.0.1:{srv.getsockname()[1]}"
        )
        caught = _openai_error(443, client, host="api.example")
    finally:
        srv.close()
    out = _assert_no_remedy(caught)
    assert "407" in out


def test_tls_to_a_plain_http_server_gets_no_tls_remedy() -> None:
    """An ``ssl.SSLError`` that is not a verification failure."""

    srv = _listen()

    def plain(conn: socket.socket) -> None:
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")

    _serve(srv, plain)
    try:
        caught = _openai_error(
            srv.getsockname()[1], httpx.Client(verify=_plain_context(), timeout=10)
        )
    finally:
        srv.close()
    _assert_no_remedy(caught)


# ── a server that answered: its words are not this machine's verifier ──────

#: Phrases a status error's body can carry - a user quoting what their Mac or
#: curl said, or a proxy reporting on its own upstream - one per marker family,
#: the long-standing OpenSSL ones included.
_ANSWERED_PHRASES: list[str] = [
    "my Mac says the api.example.com certificate is not trusted, what now?",
    "why does curl say certificate verify failed",
    "upstream: [SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in chain",
    "Windows: terminated in a root certificate which is not trusted by the trust provider",
]


def _echo_400_server() -> socket.socket:
    """Plain HTTP: every request gets a 400 that quotes its whole JSON body."""

    srv = _listen()

    def echo(conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        length = next(
            (
                int(line.split(b":", 1)[1])
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            ),
            0,
        )
        while len(body) < length:
            chunk = conn.recv(65536)
            if not chunk:
                break
            body += chunk
        quoted = body.decode("utf-8", "replace")
        payload = json.dumps(
            {"error": {"message": f"Invalid request: {quoted}", "type": "invalid_request_error"}}
        ).encode()
        conn.sendall(
            b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )

    _serve(srv, echo)
    return srv


@pytest.mark.parametrize("phrase", _ANSWERED_PHRASES)
@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
def test_an_http_error_that_quotes_a_certificate_phrase_gets_no_tls_remedy(
    sdk: str, phrase: str
) -> None:
    """Review round 2: a plain-``http://`` 400 that quotes the user's prompt.

    No TLS anywhere, yet round 1 gave the ``certificate is not trusted`` row
    the corporate-CA remedy, and every build before it gave the ``certificate
    verify failed`` rows the same (measured through ``aelix -p``). RED at round 1
    for every row: the body's text was read as a TLS failure."""

    import anthropic

    srv = _echo_400_server()
    base = f"http://127.0.0.1:{srv.getsockname()[1]}"
    try:
        if sdk == "openai":
            with pytest.raises(openai.APIStatusError) as excinfo:
                openai.OpenAI(
                    api_key="sk-fake-411",
                    base_url=f"{base}/v1",
                    http_client=httpx.Client(timeout=10),
                    max_retries=0,
                ).chat.completions.create(model="m", messages=[{"role": "user", "content": phrase}])
        else:
            with pytest.raises(anthropic.APIStatusError) as excinfo:
                anthropic.Anthropic(
                    api_key="sk-ant-fake-411",
                    base_url=base,
                    http_client=httpx.Client(timeout=10),
                    max_retries=0,
                ).messages.create(
                    model="m", max_tokens=8, messages=[{"role": "user", "content": phrase}]
                )
    finally:
        srv.close()

    caught = excinfo.value
    assert caught.status_code == 400
    out = _assert_no_remedy(caught)
    assert phrase in out, out  # the body did carry the phrase


def _status_errors(text: str) -> list[BaseException]:
    """The status error of every SDK an adapter here uses, carrying ``text``."""

    from aelix_ai.providers.openai_codex_responses import _CodexHTTPError
    from google.genai import errors as genai_errors

    request = httpx.Request("POST", "https://llm-gateway.example/v1/chat/completions")
    return [
        httpx.HTTPStatusError(text, request=request, response=httpx.Response(502, request=request)),
        _CodexHTTPError(502, text),
        # The response left out: google-genai's may be a wrapper with no status.
        genai_errors.ServerError(502, {"error": {"message": text}}, None),
    ]


@pytest.mark.parametrize("phrase", _ANSWERED_PHRASES)
def test_every_sdk_s_status_error_is_an_answer_not_a_tls_failure(phrase: str) -> None:
    """httpx's, the Codex adapter's and google-genai's status errors carry the
    status three different ways (``response.status_code``, ``status``,
    ``code``); each is read. RED at round 1."""

    for exc in _status_errors(f"Bad Gateway: {phrase}"):
        out = describe_provider_error(exc)
        assert is_tls_verification_error(exc) is False, (type(exc).__name__, out)
        assert _remedy(out) == "none", (type(exc).__name__, out)


def test_a_wrapper_raised_from_a_status_error_is_an_answer_too() -> None:
    """The anthropic adapter raises its ``_AuthError`` from the SDK's 401/403
    with the same text: the chain holds the status, so neither link's words
    count. RED at round 1."""

    request = httpx.Request("POST", "https://api.example/v1/messages")
    status = openai.AuthenticationError(
        "Error code: 401 - certificate verify failed for client cert",
        response=httpx.Response(401, request=request),
        body=None,
    )
    try:
        try:
            raise status
        except openai.AuthenticationError as err:
            raise RuntimeError(str(err)) from err
    except RuntimeError as exc:
        assert _remedy(describe_provider_error(exc)) == "none"
        assert is_tls_verification_error(exc) is False


@pytest.mark.parametrize("phrase", _ANSWERED_PHRASES)
def test_an_oauth_endpoint_s_http_error_is_an_answer_too(phrase: str) -> None:
    """The narrowed finish (round 2's verify [EDGE]): aelix's OAuth sign-in and
    refresh errors carry their status as ``oauth_status_code``
    (``oauth_http_error``, #379), and ``/login`` passes them to
    ``describe_provider_error``. RED without that attribute in ``_answered``:
    a token endpoint's 502 quoting a certificate phrase got the CA remedy."""

    from aelix_ai.oauth._helpers import oauth_http_error

    exc = oauth_http_error(
        f"HTTP request failed. status=502; url=https://auth.example/v1/oauth/token; body={phrase}",
        502,
    )
    out = _assert_no_remedy(exc)
    assert phrase in out, out


def test_a_sign_in_endpoint_that_answers_503_gets_no_tls_remedy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real Copilot device-code request against a plain-``http://`` 503
    whose body is Envoy's report on ITS upstream's TLS (the verify round's
    probe): no TLS anywhere on this machine's side. RED without
    ``oauth_status_code`` in ``_answered``."""

    from aelix_ai.oauth import github_copilot

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    body = (
        b"upstream connect error or disconnect/reset before headers. reset reason: remote "
        b"connection failure, transport failure reason: TLS_error:|268435581:SSL routines:"
        b"OPENSSL_internal:CERTIFICATE_VERIFY_FAILED:TLS_error_end"
    )

    def answer_503(conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return
            data += chunk
        conn.sendall(
            b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )

    srv = _listen()
    _serve(srv, answer_503)
    base = f"http://127.0.0.1:{srv.getsockname()[1]}"
    monkeypatch.setattr(
        github_copilot,
        "_get_urls",
        lambda _domain: {
            "device_code_url": f"{base}/login/device/code",
            "access_token_url": f"{base}/login/oauth/access_token",
            "copilot_token_url": f"{base}/copilot_internal/v2/token",
        },
    )
    try:
        with pytest.raises(RuntimeError) as excinfo:
            asyncio.run(github_copilot._start_device_flow("ghe.example.com"))
    finally:
        srv.close()

    caught = excinfo.value
    assert getattr(caught, "oauth_status_code", None) == 503
    out = _assert_no_remedy(caught)
    assert "CERTIFICATE_VERIFY_FAILED" in out, out  # the body did carry the marker


# ── a server's alert about OUR certificate is not a verification failure ────


def test_a_server_that_demands_a_client_certificate_gets_no_tls_remedy(
    pki: _Pki,
) -> None:
    """Review round 2 (the cross-review's S1): a TLS 1.3 server that requires a
    client certificate sends ``certificate_required`` after the handshake, and
    httpcore carries that ``ssl.SSLError`` behind ``from None`` exactly as it
    carries a verification error. Its reason names CERTIFICATE, yet it is about
    the certificate this client did not send: no CA helps. Only a
    :class:`ssl.SSLCertVerificationError` behind the cut is attributed.

    SABOTAGE S1: let :func:`_carried_verification_error` take any carried
    ``ssl.SSLError`` whose reason names CERTIFICATE - RED (the CA remedy)."""

    server = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_3
    server.load_cert_chain(str(pki.chains["ok"]))
    server.verify_mode = ssl.CERT_REQUIRED
    server.load_verify_locations(cafile=str(pki.ca))

    def demand_a_client_certificate(conn: socket.socket) -> None:
        tls = server.wrap_socket(conn, server_side=True, do_handshake_on_connect=False)
        with contextlib.suppress(OSError):  # ssl.SSLError is an OSError
            tls.do_handshake()  # fails: no client certificate; the alert is sent
        # Read the client's request to its end before closing, so the close
        # sends no RST that could overtake the alert on the way to the client.
        raw = socket.socket(fileno=tls.detach())
        raw.settimeout(5)
        with raw, contextlib.suppress(OSError):
            while raw.recv(65536):
                pass

    srv = _listen()
    _serve(srv, demand_a_client_certificate)
    client = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_CLIENT)  # trusts the server's CA
    client.load_verify_locations(cafile=str(pki.ca))
    try:
        caught = _openai_error(srv.getsockname()[1], httpx.Client(verify=client, timeout=10))
    finally:
        srv.close()

    inner = _verification_error(caught)
    assert isinstance(inner, ssl.SSLError), repr(inner)
    assert not isinstance(inner, ssl.SSLCertVerificationError), repr(inner)
    assert "CERTIFICATE" in (inner.reason or ""), repr(inner)
    _assert_no_remedy(caught)


# ── the SSL_CERT_FILE example is the platform's own ─────────────────────────


#: (``sys.platform``, the example line the remedy must carry).
_CERT_FILE_EXAMPLES: list[tuple[str, str]] = [
    ("linux", "  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"),
    ("darwin", '  export SSL_CERT_FILE="$HOME/.aelix/ca-bundle.pem"'),
    ("win32", '  $env:SSL_CERT_FILE = "$HOME\\.aelix\\ca-bundle.pem"'),
    ("freebsd14", '  export SSL_CERT_FILE="$HOME/.aelix/ca-bundle.pem"'),
]


@pytest.mark.parametrize("os_store", [True, False], ids=["os-store", "certifi"])
@pytest.mark.parametrize(("platform", "example"), _CERT_FILE_EXAMPLES)
def test_the_ssl_cert_file_example_is_the_platform_s_own(
    platform: str, example: str, os_store: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2: the one example was Linux's on every platform, and since
    #411 the CA remedy reaches macOS and Windows: ``/etc/ssl/certs`` is empty on
    macOS (following it literally failed with ``[Errno 2]``), and ``export`` is
    not a PowerShell command. Each platform's remedy still fits the TUI whole.
    RED at round 1 for darwin, win32 and freebsd14."""

    from aelix_ai.providers import _error_hints
    from aelix_ai.utils.terminal_text import safe_error_for_terminal

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(_error_hints, "_os_trust_store_active", lambda: os_store)
    exc = ssl.SSLCertVerificationError("unable to get local issuer certificate")
    exc.verify_code = 20  # type: ignore[attr-defined]
    full = describe_provider_error(exc)

    lines = full.split("\n")
    assert example in lines, full
    assert [line for line in lines if line.startswith("  ")] == [example], full
    if platform != "linux":
        assert "/etc/ssl/certs" not in full, full
    if platform == "win32":
        assert "export " not in full and "PowerShell" in full, full
    assert _remedy(full) == "ca"
    assert len(lines) <= 8 and max(len(line) for line in lines) <= 200, full
    assert safe_error_for_terminal(full) == full


# ── the rule the walk follows ───────────────────────────────────────────────


def test_an_unrelated_error_raised_from_none_still_claims_no_tls_cause() -> None:
    """It carries another error, not the one behind the cut."""

    try:
        try:
            raise ssl.SSLCertVerificationError("“localhost” certificate is not trusted")
        except ssl.SSLCertVerificationError:
            raise RuntimeError(ValueError("unrelated bug")) from None
    except RuntimeError as exc:
        assert is_tls_verification_error(exc) is False
        assert describe_provider_error(exc) == "unrelated bug"


def test_a_wrapper_that_repeats_the_error_word_for_word_is_followed() -> None:
    """The other half of :func:`_carries`: the same text, nothing added."""

    inner = ssl.SSLCertVerificationError("“localhost” certificate is name-mangled")
    inner.verify_code = -67602  # type: ignore[attr-defined]
    try:
        try:
            raise inner
        except ssl.SSLCertVerificationError as err:
            raise ConnectionError(str(err)) from None
    except ConnectionError as exc:
        assert _remedy(describe_provider_error(exc)) == "hostname"


@pytest.mark.parametrize(
    ("outer", "inner", "carried"),
    [
        (lambda e: Exception(e), ValueError("x"), True),
        # Held as an argument beside another one: its text is the tuple's, so
        # only holding it says it is carried.
        (lambda e: Exception(e, "while connecting"), ValueError("x"), True),
        (lambda e: Exception(str(e)), ValueError("x"), True),
        (lambda e: Exception(f"while handling: {e}"), ValueError("x"), False),
        (lambda e: Exception(ValueError("y")), ValueError("x"), False),
        (lambda e: Exception(), Exception(), False),  # both say nothing
    ],
    ids=[
        "as-argument",
        "as-one-of-two-arguments",
        "same-text",
        "text-plus-more",
        "another-error",
        "both-empty",
    ],
)
def test_carries(
    outer: Callable[[BaseException], BaseException], inner: BaseException, carried: bool
) -> None:
    from aelix_ai.providers._error_hints import _carries

    assert _carries(outer(inner), inner) is carried


# ── text alone: a wrapper that kept only the OS verifier's words ────────────


@pytest.mark.parametrize(
    "text",
    [
        "“localhost” certificate is not trusted",
        "A certificate chain processed, but terminated in a root certificate which is not "
        "trusted by the trust provider.",
        "A certificate chain could not be built to a trusted root authority.",
        "Certificate chain policy error 0x800b0109 [0]",
        "Certificate chain policy error 0x800b010a [0]",
    ],
)
def test_the_os_verifier_s_untrusted_issuer_text_alone_gets_the_ca_remedy(text: str) -> None:
    exc = RuntimeError(f"request failed: {text}")
    assert is_tls_verification_error(exc) is True
    assert _remedy(describe_provider_error(exc)) == "ca"


@pytest.mark.parametrize(
    ("text", "attributed"),
    [
        ("“other.example” certificate name does not match input", False),
        ("“localhost” certificate is expired", False),
        ("The certificate's CN name does not match the passed value.", False),
        (
            "A required certificate is not within its validity period when verifying against "
            "the current system clock or the timestamp in the signed file.",
            False,
        ),
        # Review round 2: truststore texts that name no cause, no longer markers.
        # macOS <= 10.13 gives the first for an untrusted root, a wrong host and
        # an expired leaf alike; Windows' fallback text names its code, here a
        # wrong host and a wrong date.
        ("Recoverable trust failure occurred", False),
        ("Certificate chain policy error 0x800b010f [0]", False),
        ("Certificate chain policy error 0x800b0101 [0]", False),
        # macOS <= 10.13's "never trust" ends in the macOS marker, so it IS a TLS
        # failure - one no remedy here fits.
        ("User specified that certificate is not trusted", True),
        # Review round 2: macOS rejecting the certificate itself.
        ("“localhost” certificate is not standards compliant", False),
        ("“localhost” certificate is using a broken key size", False),
        ("“localhost” certificate is not permitted for this usage", False),
    ],
)
def test_a_text_alone_that_names_no_untrusted_issuer_gets_no_remedy(
    text: str, attributed: bool
) -> None:
    """Without the code these texts say nothing a remedy here fits (round 1 asked
    only that they not get the CA remedy; none is what each of them gets).

    SABOTAGE: put back the generic marker 'Recoverable trust failure occurred'
    or 'Certificate chain policy error' - their rows, RED."""

    exc = RuntimeError(f"request failed: {text}")
    assert is_tls_verification_error(exc) is attributed
    assert _remedy(describe_provider_error(exc)) == "none"


@pytest.mark.parametrize(
    "text",
    [
        "Project is not trusted; refusing to write project settings",
        "this directory is not trusted; pass --approve to trust this project",
        "407 Proxy Authentication Required",
        "Error code: 401 - Bad credentials",
        "[Errno 61] Connection refused",
        "[Errno 8] nodename nor servname provided, or not known",
        "_ssl.c:989: The handshake operation timed out",
        "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)",
        "the trust provider is unavailable",
    ],
)
def test_text_that_is_not_a_certificate_failure_gets_no_remedy(text: str) -> None:
    exc = RuntimeError(text)
    assert is_tls_verification_error(exc) is False
    assert describe_provider_error(exc) == text
