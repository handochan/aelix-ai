"""Auto-relaxing RFC-5280 strict verification — and refusing to, when it matters.

WHY THIS EXISTS. Python 3.13 turned on ``X509_V_FLAG_X509_STRICT`` in
``ssl.create_default_context()``; 3.12 did not. Measured on one box::

    3.12.1   verify_flags = 32768    strict OFF
    3.13.13  verify_flags = 557088   strict ON

A TLS-intercepting corporate proxy mints certificates that fail those clauses
even when its root CA is correctly installed, so aelix on 3.13 refuses a chain
that ``openssl``, curl, browsers and Python 3.12 all accept. A real report::

    certificate verify failed: Missing Authority Key Identifier (_ssl.c:1032)

on a machine where ``openssl verify`` returned ``0 (ok)``. And because
``install.sh`` uses ``uv tool install`` (which ignores ``.python-version``) while
contributors use ``uv sync``, this presented as "works from a clone, fails when
installed".

THE ONE PROPERTY EVERYTHING RESTS ON, and the reason the negative control below
is not optional: relaxation happens only after aelix has MEASURED that the exact
failing host verifies once strict is cleared — i.e. that this machine already
trusts that chain. A chain it does not trust must still be refused. If that test
ever goes green while the code has stopped checking, the feature has become
"disable certificate verification", which is not what it is.

THE LAB. Real certificates, a real TLS server, real ``httpx`` requests. Python
3.12 is made to behave like 3.13 by forcing the flag — the flag IS the whole
difference, and forcing it is how the same lab runs on either interpreter.
"""

from __future__ import annotations

import datetime
import socket
import ssl
import threading
from typing import TYPE_CHECKING

import httpx
import pytest
from aelix_ai.providers._tls_strict import (
    extract_tls_failure,
    maybe_relax_strict_for_session,
    reset_strict_relaxation,
    session_relaxation,
    strict_is_enabled,
)

if TYPE_CHECKING:
    from pathlib import Path

_STRICT = ssl.VerifyFlags.VERIFY_X509_STRICT


def _make_chain(tmp: Path, *, conformant: bool) -> tuple[Path, Path]:
    """A root + leaf. ``conformant=False`` omits the Authority Key Identifier.

    That omission is exactly what the reporter's proxy produced
    (``Missing Authority Key Identifier``) and it is invisible to every verifier
    that does not set the strict flag.
    """

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)
    tag = "conformant" if conformant else "sloppy"

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{tag}-root")])
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
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
    )
    if conformant:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
    leaf = builder.sign(ca_key, hashes.SHA256())

    ca_pem = tmp / f"{tag}_ca.pem"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    chain_pem = tmp / f"{tag}_chain.pem"
    chain_pem.write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM)
        + leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return ca_pem, chain_pem


def _serve(chain: Path) -> int:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(chain))
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    port = int(srv.getsockname()[1])

    def loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(256)
                    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
            except Exception:  # noqa: BLE001, S110 — a rejected handshake is the point
                pass

    threading.Thread(target=loop, daemon=True).start()
    return port


def _simulate_313(monkeypatch: pytest.MonkeyPatch, trusted_ca: Path) -> None:
    """Python 3.13's default flags, with ``trusted_ca`` standing in for the OS store.

    Forcing the flag rather than requiring a 3.13 interpreter is deliberate: the
    flag IS the entire difference between the two versions (measured), so this
    lab reproduces the defect on whichever interpreter the suite runs under.
    """

    real = ssl.create_default_context

    def factory(*_a: object, **_k: object) -> ssl.SSLContext:
        context = real(cafile=str(trusted_ca))
        context.verify_flags |= _STRICT
        return context

    monkeypatch.setattr(ssl, "create_default_context", factory)


@pytest.fixture(autouse=True)
def _clean() -> object:
    reset_strict_relaxation()
    yield
    reset_strict_relaxation()


# ── the reporter's shape ─────────────────────────────────────────────────────


def test_a_strict_only_rejection_is_measured_then_relaxed_and_the_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole feature, end to end, through real TLS.

    SABOTAGE: make ``_confirm_relaxable`` return ``True`` without handshaking.
    This still passes — which is why the negative control below is the test that
    actually guards the behaviour, and why it must be read as a pair with this one.
    """

    ca, chain = _make_chain(tmp_path, conformant=False)
    _simulate_313(monkeypatch, ca)
    port = _serve(chain)
    url = f"https://localhost:{port}/v1"

    assert strict_is_enabled(), "guard: the lab must reproduce 3.13's default"

    with pytest.raises(httpx.ConnectError) as first:
        httpx.Client(timeout=5).get(url)

    failure = extract_tls_failure(first.value)
    assert failure is not None
    assert failure.host == "localhost"
    assert failure.port == port
    assert failure.verify_message == "Missing Authority Key Identifier"

    relaxation = maybe_relax_strict_for_session(first.value)
    assert relaxation is not None
    assert relaxation.host == "localhost"

    # The point of the whole exercise: the next attempt works.
    assert httpx.Client(timeout=5).get(url).status_code == 200
    assert strict_is_enabled() is False


def test_a_chain_this_machine_does_not_trust_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 THE SAFETY PROPERTY. If this ever goes green wrongly, the feature has
    become "turn off certificate verification".

    The trust store holds a DIFFERENT root, so the presented chain is genuinely
    untrusted — verify code 20, not a strict clause. Nothing may be relaxed, and
    the retry must still fail.

    SABOTAGE: drop the ``_handshake(..., strict=False)`` check from
    ``_confirm_relaxable`` (i.e. relax whenever strict is on and a cert failed).
    This must go RED.
    """

    trusted_ca, _ = _make_chain(tmp_path, conformant=True)
    _, untrusted_chain = _make_chain(tmp_path, conformant=False)
    _simulate_313(monkeypatch, trusted_ca)
    port = _serve(untrusted_chain)
    url = f"https://localhost:{port}/v1"

    with pytest.raises(httpx.ConnectError) as first:
        httpx.Client(timeout=5).get(url)

    failure = extract_tls_failure(first.value)
    assert failure is not None
    assert failure.verify_code == 20, "guard: this must be a genuine trust failure"

    assert maybe_relax_strict_for_session(first.value) is None
    assert session_relaxation() is None
    assert strict_is_enabled() is True

    with pytest.raises(httpx.ConnectError):
        httpx.Client(timeout=5).get(url)


def test_a_transient_failure_that_has_since_healed_relaxes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both directions are measured, and this is why the ``strict=True`` half matters.

    If the host verifies WITH strict, the original error was something else and
    there is nothing to relax.

    SABOTAGE: drop the ``_handshake(..., strict=True)`` check. The conformant
    chain then relaxes strict for no reason, and this goes RED.
    """

    ca, chain = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)
    port = _serve(chain)

    # Hand it a cert-verification failure whose host is healthy right now.
    synthetic = ssl.SSLCertVerificationError("certificate verify failed: whatever")
    synthetic.verify_code = 95
    outer = httpx.ConnectError("boom", request=httpx.Request("GET", f"https://localhost:{port}/v1"))
    outer.__cause__ = synthetic

    assert maybe_relax_strict_for_session(outer) is None
    assert strict_is_enabled() is True


# ── the guards that stop it doing anything ───────────────────────────────────


def test_nothing_happens_when_strict_is_already_off() -> None:
    """Python 3.12, or a session that already relaxed. No handshake, no change."""

    assert strict_is_enabled() is False, "guard: this suite's interpreter is pre-3.13"
    err = ssl.SSLCertVerificationError("certificate verify failed: x")
    err.verify_code = 95
    outer = httpx.ConnectError("x", request=httpx.Request("GET", "https://example.invalid/"))
    outer.__cause__ = err

    assert maybe_relax_strict_for_session(outer) is None


def test_a_non_certificate_failure_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """SABOTAGE: drop the ``SSLCertVerificationError`` guard in ``extract_tls_failure``.

    A plain connection refusal would then trigger a handshake to whatever host
    happened to be on the exception. This must go RED.
    """

    assert extract_tls_failure(httpx.ConnectError("connection refused")) is None
    assert extract_tls_failure(RuntimeError("nothing to do with TLS")) is None
    assert maybe_relax_strict_for_session(ValueError("x")) is None


def test_a_failure_with_no_recoverable_host_relaxes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guessing a host and handshaking to it is a network call nobody asked for.

    🔴 ASSERTING ON THE OUTCOME IS NOT ENOUGH, and a sabotage run proved it:
    substituting a placeholder host still returned ``None``, because the
    handshake to it simply fails too. The outcome is safe either way — the claim
    this test makes is about the CONNECTION, so that is what it has to watch.

    SABOTAGE: drop the ``not failure.host`` guard. A connection is attempted to
    whatever host was substituted, and this goes RED on the call count.
    """

    ca, _ = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)

    attempts: list[object] = []
    real = socket.create_connection

    def watched(address: object, *a: object, **k: object) -> object:
        attempts.append(address)
        return real(address, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "create_connection", watched)

    bare = ssl.SSLCertVerificationError("certificate verify failed: x")
    bare.verify_code = 95
    failure = extract_tls_failure(bare)
    assert failure is not None
    assert failure.host is None

    assert maybe_relax_strict_for_session(bare) is None
    assert attempts == [], f"a connection was attempted to {attempts}"


# ── what the relaxation does and does not give up ────────────────────────────


def test_only_the_strict_flag_is_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SABOTAGE: clear ``verify_mode`` or ``check_hostname`` alongside the flag.

    Everything that makes verification *verification* has to survive; the only
    thing given up is being stricter than every other tool on the machine.
    """

    ca, chain = _make_chain(tmp_path, conformant=False)
    _simulate_313(monkeypatch, ca)
    port = _serve(chain)

    with pytest.raises(httpx.ConnectError) as first:
        httpx.Client(timeout=5).get(f"https://localhost:{port}/v1")
    assert maybe_relax_strict_for_session(first.value) is not None

    after = ssl.create_default_context()
    assert after.verify_mode == ssl.CERT_REQUIRED
    assert after.check_hostname is True
    assert not (after.verify_flags & _STRICT)


def test_the_relaxation_is_reported_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It changes verification behaviour, so it must be visible in ``aelix status``."""

    from aelix_ai.providers._trust_store import describe_trust_store

    ca, chain = _make_chain(tmp_path, conformant=False)
    _simulate_313(monkeypatch, ca)
    port = _serve(chain)
    with pytest.raises(httpx.ConnectError) as first:
        httpx.Client(timeout=5).get(f"https://localhost:{port}/v1")
    maybe_relax_strict_for_session(first.value)

    report = describe_trust_store().as_dict()
    assert report["strict_relaxed_for"] == "localhost"
    assert "still enforced" in (report["strict_relaxed_reason"] or "")


def test_the_advice_stops_naming_a_ca_the_user_already_installed() -> None:
    """SABOTAGE: delete the ``_strict_hint`` branch from ``_tls_hint``.

    Measured on the real report: verify code 95 received advice byte-identical to
    code 20 — "install the corporate CA, set SSL_CERT_FILE" — on a machine where
    ``SSL_CERT_FILE`` was already set to the suggested path and ``openssl verify``
    returned ``0 (ok)``. A remedy that cannot work reads as "you did it wrong".
    """

    from aelix_ai.providers._error_hints import describe_provider_error

    def advice(code: int, message: str) -> str:
        """The REMEDY only — the base message differs by construction (it is
        ``str(exc)``), and comparing whole strings would test that instead."""

        err = ssl.SSLCertVerificationError(f"certificate verify failed: {message}")
        err.verify_code = code
        return describe_provider_error(err).split("\n\n", 1)[1]

    genuine = advice(20, "unable to get local issuer certificate")
    assert "SSL_CERT_FILE" in genuine, "guard: the classic advice must survive for its own case"

    # Strict OFF (3.12): aelix cannot claim strict is the cause, and does not —
    # the classic advice is the honest fallback there.
    assert strict_is_enabled() is False
    assert advice(95, "Missing Authority Key Identifier") == genuine


def test_with_strict_on_a_non_trust_verify_code_gets_the_strict_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch that fixes the reported message.

    SABOTAGE: delete the ``_strict_hint`` call from ``_tls_hint``. Code 95 falls
    back to "install the corporate CA / set SSL_CERT_FILE" — the two things the
    reporter had already done — and this goes RED.
    """

    from aelix_ai.providers._error_hints import describe_provider_error

    ca, _ = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)
    assert strict_is_enabled(), "guard: the lab must reproduce 3.13's default"

    def advice(code: int, message: str) -> str:
        err = ssl.SSLCertVerificationError(f"certificate verify failed: {message}")
        err.verify_code = code
        return describe_provider_error(err).split("\n\n", 1)[1]

    strict_advice = advice(95, "Missing Authority Key Identifier")
    assert "RFC 5280" in strict_advice
    assert "Python 3.13" in strict_advice
    assert "--python 3.12" in strict_advice
    assert "SSL_CERT_FILE" not in strict_advice, (
        "the whole point: stop naming a knob the user has already set"
    )

    # NEGATIVE CONTROL — a genuine trust failure keeps the CA advice even with
    # strict on, because there the CA advice is the correct one.
    assert "SSL_CERT_FILE" in advice(20, "unable to get local issuer certificate")
