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


def _simulate_312(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python 3.12's default flags, whatever interpreter the suite runs under.

    #192: these rows used to ASSERT the interpreter was pre-3.13 ("guard: this
    suite's interpreter is pre-3.13") and so could only ever fail on the 3.13
    leg. The strict-off half of the contract is a fact about the flag, exactly
    like ``_simulate_313``'s half, so it is forced the same way.
    """

    real = ssl.create_default_context

    def factory(*a: object, **k: object) -> ssl.SSLContext:
        context = real(*a, **k)  # type: ignore[arg-type]
        context.verify_flags &= ~_STRICT
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
    synthetic.verify_code = 85
    outer = httpx.ConnectError("boom", request=httpx.Request("GET", f"https://localhost:{port}/v1"))
    outer.__cause__ = synthetic

    assert maybe_relax_strict_for_session(outer) is None
    assert strict_is_enabled() is True


# ── the guards that stop it doing anything ───────────────────────────────────


def test_nothing_happens_when_strict_is_already_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python 3.12, or a session that already relaxed. No handshake, no change."""

    _simulate_312(monkeypatch)
    assert strict_is_enabled() is False, "guard: the lab must reproduce 3.12's default"
    err = ssl.SSLCertVerificationError("certificate verify failed: x")
    err.verify_code = 85
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
    bare.verify_code = 85
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


def test_the_advice_stops_naming_a_ca_the_user_already_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SABOTAGE: delete the ``_strict_hint`` branch from ``_tls_hint``.

    Measured on the real report: ``Missing Authority Key Identifier`` (verify
    code 85 - this file said 95 until #192 measured a real handshake; 95 is
    ``RPK_UNTRUSTED``) received advice byte-identical to code 20 — "install the corporate CA, set SSL_CERT_FILE" — on a machine where
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
    # the classic advice is the honest fallback there. Forced, not assumed: the
    # 3.13 leg runs this file too (#192).
    _simulate_312(monkeypatch)
    assert strict_is_enabled() is False
    assert advice(85, "Missing Authority Key Identifier") == genuine


def test_with_strict_on_a_non_trust_verify_code_gets_the_strict_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch that fixes the reported message.

    SABOTAGE: delete the ``_strict_hint`` call from ``_tls_hint``. Code 85 falls
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

    strict_advice = advice(85, "Missing Authority Key Identifier")
    assert "RFC 5280" in strict_advice
    assert "Python 3.13" in strict_advice
    assert "--python 3.12" in strict_advice
    assert "SSL_CERT_FILE" not in strict_advice, (
        "the whole point: stop naming a knob the user has already set"
    )

    # NEGATIVE CONTROL — a genuine trust failure keeps the CA advice even with
    # strict on, because there the CA advice is the correct one.
    assert "SSL_CERT_FILE" in advice(20, "unable to get local issuer certificate")


# ── #192: which failures the strict remedy may claim ─────────────────────────
#
# Until the 3.13 leg existed, ``_strict_hint`` was reached only by the two rows
# above that force the flag, and both used a strict-only code. On a real 3.13
# interpreter every other row in tests/providers/test_error_hints.py reached it
# too, because the branch was a DENYLIST - "strict is on and the code is not one
# of {2, 18, 19, 20}" - so a bad signature (7), a CA that is not a CA (79), an
# absent code (a marker-only or synthetic error, the #99 shape) all got "this is
# an RFC 5280 rule, not trust; reinstall on 3.12". At 8f7d98aa the 3.13 suite
# failed nine rows on exactly that (test_error_hints x6, the #186 quoting row,
# test_terminal_text, test_login_wizard), plus the two guards above that
# asserted a pre-3.13 interpreter (ADR-0241 named the defect; #192 measured it
# again). The branch is an ALLOWLIST now, of the codes OpenSSL raises only under
# the strict flag, and these rows hold both sides of it on any interpreter.
# Code 79 is on neither side: OpenSSL raises it with AND without the flag
# depending on the chain, so on a strict interpreter it gets a hedged remedy
# that names both causes and the step that tells them apart.

#: ``include/openssl/x509_vfy.h``'s block "Errors in case a check in
#: X509_V_FLAG_X509_STRICT mode fails", 78-94, minus 79 (measured below: one
#: chain raises it without the flag too, another only with it).
_STRICT_ONLY = [78, *range(80, 95)]

#: OpenSSL's text for each strict-only code (``X509_verify_cert_error_string``,
#: OpenSSL 3.5/3.6), the messages a relaxed session can record.
_STRICT_ONLY_MESSAGES: dict[int, str] = {
    78: "cert info signature and signature algorithm mismatch",
    80: "Path length invalid for non-CA cert",
    81: "Path length given without key usage keyCertSign",
    82: "Key usage keyCertSign invalid for non-CA cert",
    83: "Issuer name empty",
    84: "Subject name empty",
    85: "Missing Authority Key Identifier",
    86: "Missing Subject Key Identifier",
    87: "Empty Subject Alternative Name extension",
    88: "Subject empty and Subject Alt Name extension not critical",
    89: "Basic Constraints of CA cert not marked critical",
    90: "Authority Key Identifier marked critical",
    91: "Subject Key Identifier marked critical",
    92: "CA cert does not include key usage extension",
    93: "Using cert extension requires at least X509v3",
    94: "Certificate public key has explicit ECC parameters",
}
#: Trust, signature, path and anything unknown: the CA advice stands.
_NOT_STRICT_ONLY = [None, 2, 7, 18, 19, 20, 24, 95]


def _remedy(code: int | None, message: str) -> str:
    from aelix_ai.providers._error_hints import describe_provider_error

    err = ssl.SSLCertVerificationError(f"certificate verify failed: {message}")
    if code is not None:
        err.verify_code = code
    return describe_provider_error(err).split("\n\n", 1)[1]


def _kind(remedy: str) -> str:
    """Which of the three remedies this is - each one's text, and none of the others'.

    ``strict``: it asserts strict is the cause ("not on trust") and gives the
    ``openssl s_client`` confirm step, never the CA advice. ``trust``: the CA
    advice, no confirm step. ``hedged``: both causes - the confirm step AND
    the CA advice - while asserting neither.
    """

    asserts_strict = "not on trust" in remedy
    confirm = "openssl s_client" in remedy
    ca_advice = "SSL_CERT_FILE" in remedy
    hedged = "cannot tell which" in remedy
    if asserts_strict and confirm and not ca_advice and not hedged:
        return "strict"
    if ca_advice and not confirm and not asserts_strict and not hedged:
        return "trust"
    if hedged and confirm and ca_advice and not asserts_strict:
        return "hedged"
    return f"unclassified: {remedy!r}"


@pytest.mark.parametrize("code", _STRICT_ONLY)
def test_with_strict_on_every_strict_only_code_gets_the_strict_remedy(
    code: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca, _ = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)
    remedy = _remedy(code, "some strict-only clause")
    assert "RFC 5280" in remedy
    assert _kind(remedy) == "strict"


@pytest.mark.parametrize("code", _NOT_STRICT_ONLY)
def test_with_strict_on_a_code_strict_does_not_explain_keeps_the_ca_advice(
    code: int | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SABOTAGE: put the denylist back (``code in {2, 18, 19, 20}`` -> None).
    7, 24, 95 and an absent code go RED."""

    ca, _ = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)
    assert strict_is_enabled(), "guard: the lab must reproduce 3.13's default"
    remedy = _remedy(code, "unable to get local issuer certificate")
    assert "RFC 5280" not in remedy, f"code {code}: strict claimed a failure OpenSSL raises without it"
    assert _kind(remedy) == "trust", f"code {code}"


@pytest.mark.parametrize("code", [79, None])
def test_with_strict_on_an_invalid_ca_names_both_causes_and_asserts_neither(
    code: int | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """79 (``invalid CA certificate``) is strict's for a root with no
    basicConstraints and not strict's for a ``CA:FALSE`` issuer, and the code
    is the same for both (the real handshakes below measure each). With no
    code, OpenSSL's message for 79 counts the same way.

    SABOTAGE: give 79 the trust advice (round 1's allowlist) - the root
    without basicConstraints is told to install the CA it already trusts, RED.
    Give it the strict remedy - the ``CA:FALSE`` issuer is sent to 3.12 for
    nothing, RED."""

    ca, _ = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)
    remedy = _remedy(code, "invalid CA certificate")
    assert _kind(remedy) == "hedged", remedy
    assert "Basic Constraints" in remedy and "CA:FALSE" in remedy, remedy


@pytest.mark.parametrize("code", [79, None])
def test_with_strict_off_an_invalid_ca_gets_the_ca_advice_only(
    code: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the flag only the ``CA:FALSE`` shape can raise 79, so the
    strict half of the hedge has nothing to explain on 3.12.

    SABOTAGE: hedge 79 whatever the flag - RED."""

    _simulate_312(monkeypatch)
    assert not strict_is_enabled(), "guard: the lab must reproduce 3.12's default"
    assert _kind(_remedy(code, "invalid CA certificate")) == "trust"


def test_with_strict_on_a_codeless_error_naming_a_strict_only_clause_gets_the_strict_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrapper that kept only the TEXT (a string-marker match, an OAuth error
    re-raised as ``RuntimeError``) still names the clause; read it.

    SABOTAGE: drop the message match. The reporter's exact text with no code
    falls back to the CA advice and this goes RED."""

    from aelix_ai.providers._error_hints import describe_provider_error

    ca, _ = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)
    wrapped = RuntimeError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "Missing Authority Key Identifier (_ssl.c:1032)"
    )
    assert "RFC 5280" in describe_provider_error(wrapped)
    plain = RuntimeError("certificate verify failed: unable to get local issuer certificate")
    assert "RFC 5280" not in describe_provider_error(plain)


# ── #192 review round 3: every remedy survives the TUI's error bound ────────
#
# The TUI prints an error through ``safe_error_for_terminal`` (8 lines, each cut
# at 200 characters; render.py and login_wizard.py, every error site). Round 2's
# hedge was 10 lines / 1661 characters, so there - the default UI, on the 3.13
# the installers give users - the ``CA:FALSE`` user lost the line with their
# own action ("... (2 more lines omitted)" where ``SSL_CERT_FILE`` was) and kept
# only the strict branch's. ``aelix -p`` does not bound, which is why only the
# TUI showed it. The hostname, clock, trust and strict remedies each had a line
# past 200 characters, and the hostname and clock ones lost their action there.


def _real_shape(code: int | None, message: str) -> ssl.SSLCertVerificationError:
    """What OpenSSL raises: the bracketed reason, and the code when there is one."""

    err = ssl.SSLCertVerificationError(
        1, f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {message} (_ssl.c:1032)"
    )
    if code is not None:
        err.verify_code = code
        err.verify_message = message
    return err


@pytest.mark.parametrize("os_store", [False, True], ids=["certifi", "os-store"])
@pytest.mark.parametrize("code", [79, None])
def test_the_invalid_ca_hedge_keeps_both_actions_in_the_tui(
    code: int | None, os_store: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both branches' actions are still on screen after the TUI's bound.

    SABOTAGE: put round 2's ten-line hedge back - ``SSL_CERT_FILE`` is in the
    omitted lines and ``--python 3.12`` is cut mid-line, RED. Give the hedge's
    cause-2 line the certifi wording whatever the store - the os-store rows
    lose "system-wide", RED.
    """

    from aelix_ai.providers import _error_hints
    from aelix_ai.providers._error_hints import describe_provider_error
    from aelix_ai.utils.terminal_text import safe_error_for_terminal

    ca, _ = _make_chain(tmp_path, conformant=True)
    _simulate_313(monkeypatch, ca)
    monkeypatch.setattr(_error_hints, "_os_trust_store_active", lambda: os_store)
    full = describe_provider_error(_real_shape(code, "invalid CA certificate"))
    shown = safe_error_for_terminal(full)

    assert _kind(full.split("\n\n", 1)[1]) == "hedged", "guard: this is the hedge"
    for needed in ("openssl s_client", "--python 3.12", "SSL_CERT_FILE"):
        assert needed in shown, f"{needed!r} is gone from what the TUI shows:\n{shown}"
    action_lines = [
        line for line in shown.splitlines() if "--python 3.12" in line or "SSL_CERT_FILE" in line
    ]
    assert len(action_lines) == 2, "each branch's action on a line of its own"
    assert ("system-wide" in action_lines[1]) is os_store, action_lines[1]
    assert shown == full, f"the TUI cut the hedge:\n{shown}"


#: The hosts a relaxed session's note is measured with (#192 review round 4:
#: round 3 measured one 24-character host, and the note's first line was 133
#: fixed characters plus the host plus the verify message - 211 for the TLS
#: guide's own example host with code 89). The last is DNS's longest name,
#: 253 characters in four labels (63 is a label's limit).
_RELAXED_HOSTS: list[tuple[str, str]] = [
    ("short", "llm-gateway.corp.example"),
    ("guide", "api.business.githubcopilot.com"),
    ("vertex", "us-central1-aiplatform.googleapis.com"),
    ("label63", f"{'l' * 63}.openai.azure.com"),
    ("dns253", ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])),
]

#: Every verify message a relaxation can record: each strict-only code's
#: (the longest is 88's), 79's, the longest message OpenSSL 3.6 has for any
#: code (77, 68 characters) and none at all (the note says ``verify code N``).
_RELAXED_MESSAGES: list[tuple[int | None, str | None]] = [
    *_STRICT_ONLY_MESSAGES.items(),
    (79, "invalid CA certificate"),
    (77, "subject signature algorithm and issuer public key algorithm mismatch"),
    (85, None),
]

#: (id, code, OpenSSL's message, strict on?, relaxed host) - every remedy
#: ``_tls_hint`` can give. ``strict`` None is a session already relaxed for
#: ``host``, which answers every later TLS error with its own note.
_EVERY_REMEDY: list[tuple[str, int | None, str | None, bool | None, str | None]] = [
    ("hostname", 62, "Hostname mismatch, certificate is not valid for 'api.openai.com'.", False, None),
    ("expired", 10, "certificate has expired", False, None),
    ("not-yet-valid", 9, "certificate is not yet valid", False, None),
    ("untrusted", 20, "unable to get local issuer certificate", False, None),
    ("untrusted-strict-on", 20, "unable to get local issuer certificate", True, None),
    ("no-code-strict-on", None, "unable to get local issuer certificate", True, None),
    ("bad-signature-strict-on", 7, "certificate signature failure", True, None),
    ("strict-only", 85, "Missing Authority Key Identifier", True, None),
    ("invalid-ca-strict-on", 79, "invalid CA certificate", True, None),
    ("invalid-ca-strict-off", 79, "invalid CA certificate", False, None),
    *(
        (f"relaxed-session-{host_id}-{code}{'' if message else '-no-message'}", code, message, None, host)
        for host_id, host in _RELAXED_HOSTS
        for code, message in _RELAXED_MESSAGES
    ),
]


@pytest.mark.parametrize("os_store", [False, True], ids=["certifi", "os-store"])
@pytest.mark.parametrize(
    ("code", "message", "strict", "host"),
    [case[1:] for case in _EVERY_REMEDY],
    ids=[case[0] for case in _EVERY_REMEDY],
)
def test_every_tls_remedy_fits_the_tui_error_bound(
    code: int | None,
    message: str | None,
    strict: bool | None,
    host: str | None,
    os_store: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TUI shows each remedy whole: at most 8 lines counting the error line
    and the blank line before the remedy, none longer than 200 characters.

    A relaxed session's note is held to it for every host in
    :data:`_RELAXED_HOSTS` and every message in :data:`_RELAXED_MESSAGES`, and
    must still show the host, the message and that verification is still
    enforced.

    SABOTAGE: join any remedy's lines back into one (the hostname, clock, trust
    or strict text as it was before round 3) - that row runs past 200
    characters and the TUI cuts it, RED. Put the relaxed note's host and
    message back into its first sentence (round 3) - the guide/label63/dns253
    rows run past 200, RED. Drop ``STILL_ENFORCED`` from the note - every
    relaxed row, RED.
    """

    from aelix_ai.providers import _error_hints, _tls_strict
    from aelix_ai.providers._error_hints import describe_provider_error
    from aelix_ai.utils.terminal_text import safe_error_for_terminal

    if strict is None:
        assert host is not None
        relaxed = _tls_strict.Relaxation(host=host, verify_code=code, verify_message=message)
        monkeypatch.setattr(_tls_strict, "session_relaxation", lambda: relaxed)
    elif strict:
        ca, _ = _make_chain(tmp_path, conformant=True)
        _simulate_313(monkeypatch, ca)
    else:
        _simulate_312(monkeypatch)
    monkeypatch.setattr(_error_hints, "_os_trust_store_active", lambda: os_store)

    full = describe_provider_error(_real_shape(code, message or "Missing Authority Key Identifier"))
    remedy = full.split("\n\n", 1)[1]
    widest = max(len(line) for line in remedy.split("\n"))
    assert full.count("\n") + 1 <= 8, full
    assert widest <= 200, f"{widest} characters:\n{remedy}"
    shown = safe_error_for_terminal(full)
    assert shown == full

    if strict is None:
        assert host is not None
        # A host longer than a line continues on the next one, never cut.
        for piece in (host[:192], host[192:]):
            assert piece in shown, f"{piece!r} is not on screen:\n{shown}"
        assert (message or f"verify code {code}") in shown
        assert _tls_strict.Relaxation.STILL_ENFORCED in shown
        assert "the cause is not certificate strictness" in shown


@pytest.mark.parametrize(
    ("host_length", "message_length", "whole"),
    [(576, 187, True), (577, 20, False), (20, 188, False)],
    ids=["at-the-bound", "host-one-past", "message-one-past"],
)
def test_the_relaxed_note_fits_exactly_as_far_as_the_docs_say(
    host_length: int, message_length: int, whole: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The condition the docs state: a host of up to 576 characters and a
    verify message of up to 187 show whole in the TUI; one character more of
    either does not (ADR-0241, the CHANGELOG and ``_error_hints``' docstring
    say so).

    SABOTAGE: let the host continue on a fourth line, or move the message onto
    the framing line - the bound moves and a row goes RED.
    """

    from aelix_ai.providers import _tls_strict
    from aelix_ai.providers._error_hints import describe_provider_error
    from aelix_ai.utils.terminal_text import safe_error_for_terminal

    relaxed = _tls_strict.Relaxation(
        host="h" * host_length, verify_code=85, verify_message="m" * message_length
    )
    monkeypatch.setattr(_tls_strict, "session_relaxation", lambda: relaxed)
    full = describe_provider_error(_real_shape(85, "Missing Authority Key Identifier"))
    assert (safe_error_for_terminal(full) == full) is whole, full


def test_aelix_status_reports_a_relaxation_on_one_line_as_before() -> None:
    """``aelix status`` prints :meth:`Relaxation.describe` as one ``!`` line.

    Pinned to its text at dfb4ddcc: the TUI note is laid out for the TUI's
    bound (#192 review rounds 3 and 4); ``aelix status`` does not cut at 200
    and keeps the one sentence pair it always printed.

    SABOTAGE: give ``describe()`` the TUI note's line breaks, or join its two
    sentences with a newline - RED.
    """

    from aelix_ai.providers._tls_strict import Relaxation

    assert Relaxation(
        host="api.openai.com", verify_code=85, verify_message="Missing Authority Key Identifier"
    ).describe() == (
        "RFC-5280 strict verification relaxed for this session after api.openai.com "
        "failed it (Missing Authority Key Identifier) but verified against this "
        "machine's trust store without it. Certificate verification, hostname "
        "checking and expiry are still enforced."
    )
    assert Relaxation(host="h", verify_code=79, verify_message=None).describe() == (
        "RFC-5280 strict verification relaxed for this session after h failed it "
        "(verify code 79) but verified against this machine's trust store without "
        "it. Certificate verification, hostname checking and expiry are still enforced."
    )


def _make_defective_chain(tmp: Path, defect: str) -> tuple[Path, Path]:
    """root -> [intermediate ->] leaf for ``localhost``, broken in ONE way.

    ``no_aki``: the leaf has no Authority Key Identifier (85).
    ``bad_signature``: the leaf is signed by a key that is not the root's (7).
    ``intermediate_not_ca``: the leaf's issuer has ``CA:FALSE`` (79).
    ``root_without_basic_constraints``: the trust anchor has no
    basicConstraints, only keyUsage keyCertSign - an old appliance's root (79).
    ``intermediate_no_ski``: the issuer has no Subject Key Identifier (86).
    ``intermediate_bc_not_critical``: the issuer's basicConstraints is not
    critical (89).
    ``intermediate_no_key_usage``: the issuer has no keyUsage (92).
    """

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.UTC)

    def key() -> rsa.RSAPrivateKey:
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def name(cn: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    usage = x509.KeyUsage(
        digital_signature=True, key_cert_sign=True, crl_sign=True,
        content_commitment=False, key_encipherment=False, data_encipherment=False,
        key_agreement=False, encipher_only=False, decipher_only=False,
    )

    def cert(
        subject: str, issuer: str, public: rsa.RSAPublicKey, signer: rsa.RSAPrivateKey,
        *, ca: bool | None, aki_of: rsa.RSAPublicKey | None,
        bc_critical: bool = True, ski: bool = True, key_usage: bool = True,
    ) -> x509.Certificate:
        builder = (
            x509.CertificateBuilder()
            .subject_name(name(subject))
            .issuer_name(name(issuer))
            .public_key(public)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
        )
        if ca is not None:
            builder = builder.add_extension(
                x509.BasicConstraints(ca=ca, path_length=None), critical=bc_critical
            )
        if ski:
            builder = builder.add_extension(
                x509.SubjectKeyIdentifier.from_public_key(public), critical=False
            )
        if aki_of is not None:
            builder = builder.add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(aki_of), critical=False
            )
        if subject == "localhost":
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
            ).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        elif key_usage:
            builder = builder.add_extension(usage, critical=True)
        return builder.sign(signer, hashes.SHA256())

    root_key, leaf_key = key(), key()
    root_ca = None if defect == "root_without_basic_constraints" else True
    root = cert("root", "root", root_key.public_key(), root_key, ca=root_ca, aki_of=None)
    chain: list[x509.Certificate]
    if defect.startswith("intermediate_"):
        mid_key = key()
        mid = cert(
            "mid", "root", mid_key.public_key(), root_key,
            ca=defect != "intermediate_not_ca", aki_of=root_key.public_key(),
            bc_critical=defect != "intermediate_bc_not_critical",
            ski=defect != "intermediate_no_ski",
            key_usage=defect != "intermediate_no_key_usage",
        )
        leaf = cert("localhost", "mid", leaf_key.public_key(), mid_key, ca=False, aki_of=mid_key.public_key())
        chain = [leaf, mid]
    else:
        signer = key() if defect == "bad_signature" else root_key
        aki = None if defect == "no_aki" else root_key.public_key()
        chain = [cert("localhost", "root", leaf_key.public_key(), signer, ca=False, aki_of=aki)]

    pem = serialization.Encoding.PEM
    ca_pem = tmp / f"{defect}_ca.pem"
    ca_pem.write_bytes(root.public_bytes(pem))
    chain_pem = tmp / f"{defect}_chain.pem"
    chain_pem.write_bytes(
        b"".join(c.public_bytes(pem) for c in chain)
        + leaf_key.private_bytes(
            pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        )
    )
    return ca_pem, chain_pem


@pytest.mark.parametrize(
    ("defect", "code", "fails_without_strict", "with_strict"),
    [
        ("no_aki", 85, False, "strict"),
        ("intermediate_no_ski", 86, False, "strict"),
        ("intermediate_bc_not_critical", 89, False, "strict"),
        ("intermediate_no_key_usage", 92, False, "strict"),
        ("bad_signature", 7, True, "trust"),
        ("intermediate_not_ca", 79, True, "hedged"),
        ("root_without_basic_constraints", 79, False, "hedged"),
    ],
)
def test_a_real_handshake_gets_the_remedy_its_code_earns(
    defect: str, code: int, fails_without_strict: bool, with_strict: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through real TLS, so the codes above are OpenSSL's and not this file's.

    Each chain is handshaken twice under the lab's trust: once with 3.12's
    flags, once with 3.13's. Whether it fails without the flag is MEASURED
    here rather than taken from the header, and the two 79 rows are why 79 is
    hedged: the ``CA:FALSE`` issuer fails both ways, the root without
    basicConstraints only with strict. Where a chain fails without strict, its
    remedy there is the CA advice; with strict, the remedy is read off the
    real exception, the way an adapter's error reaches
    ``describe_provider_error``. And OpenSSL's own message for the code must be
    the one ``_error_hints`` matches a codeless error on.

    SABOTAGE: a typo in ``_STRICT_ONLY[89]``'s text ("Basic constraints") -
    the 89 row goes RED on the message, though every remedy is unchanged.
    """

    from aelix_ai.providers import _error_hints as hints

    ca, chain = _make_defective_chain(tmp_path, defect)
    port = _serve(chain)
    url = f"https://localhost:{port}/v1"

    with monkeypatch.context() as off:
        _simulate_312(off)
        off.setattr(ssl, "create_default_context", _with_cafile(ssl.create_default_context, ca))
        try:
            httpx.Client(timeout=5).get(url)
            failed_without_strict = None
        except httpx.ConnectError as exc:
            failed_without_strict = exc
        assert (failed_without_strict is not None) is fails_without_strict
        if failed_without_strict is not None:
            unstrict = extract_tls_failure(failed_without_strict)
            assert unstrict is not None and unstrict.verify_code == code, unstrict
            remedy = hints.describe_provider_error(failed_without_strict).split("\n\n", 1)[1]
            assert _kind(remedy) == "trust", remedy

    _simulate_313(monkeypatch, ca)
    with pytest.raises(httpx.ConnectError) as caught:
        httpx.Client(timeout=5).get(url)
    failure = extract_tls_failure(caught.value)
    assert failure is not None
    assert failure.verify_code == code, failure
    messages = {**hints._STRICT_ONLY, hints._INVALID_CA_CODE: hints._INVALID_CA_MESSAGE}
    if code in messages:
        assert failure.verify_message == messages[code], (code, failure.verify_message)

    remedy = hints.describe_provider_error(caught.value).split("\n\n", 1)[1]
    assert _kind(remedy) == with_strict, remedy


def _with_cafile(factory: object, cafile: Path) -> object:
    def build(*_a: object, **_k: object) -> ssl.SSLContext:
        return factory(cafile=str(cafile))  # type: ignore[operator]

    return build
