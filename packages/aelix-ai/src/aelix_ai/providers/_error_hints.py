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

ON MACOS AND WINDOWS THE OS VERIFIES (#411). There truststore checks the chain
with the platform's own verifier, so the failure carries the OS's text
(``'“localhost” certificate is not trusted'`` on macOS) and the OS's code
(a negative OSStatus, a Windows HRESULT) where OpenSSL's would be. Neither
text names an OpenSSL marker, and httpcore re-raises the error ``from None``;
until #411 nothing attributed it and the CLI printed the bare error. TLS
attribution now takes the verification error a link carries from behind a
``from None`` (:func:`_carried_verification_error`), the OS's untrusted-issuer
texts are markers for a wrapper that kept only text, and the OS codes for a
wrong host and for the validity window map to the same remedies as OpenSSL's.
Review round 2 kept every remedy to where it is true: no text attributes a
chain a server answered with an HTTP status (:func:`_answered`), the OS
verdicts no remedy fits get none (:func:`_no_remedy_fits`: macOS rejecting the
certificate itself, and macOS <= 10.13's results, which name no cause), and the
``SSL_CERT_FILE`` example is the platform's own (:func:`_tls_cert_file_escape`).
Its last line names a copy of certifi's bundle, never certifi's own file (#556).

EVERY REMEDY FITS THE TUI (#192 review round 3). The TUI prints an error
through ``safe_error_for_terminal``: 8 lines, each cut at 200 characters. The
error itself is one line (``quote_model_text`` folds its line breaks) and a
blank line follows it, so a remedy has 6 lines of at most 200 characters, and
each action sits on a line of its own. A remedy that ran over lost its action
there while ``aelix -p``, which does not bound, showed all of it.
``tests/providers/test_tls_strict.py`` holds every remedy to that bound. The
one remedy that carries variable text, a relaxed session's note, puts the host
and the verify message on lines of their own (review round 4: with both in its
first sentence it was 211 characters for ``api.business.githubcopilot.com``
with code 89), so it fits whole for a host of up to 576 characters (DNS allows
253) and a verify message of up to 187 (OpenSSL's longest is 68). A longer
host pushes the note's last lines past the TUI's 8; a longer message is cut at
200 on its own line.

A VERIFICATION FAILURE IS NOT RETRIED (#551). The next attempt verifies the
same chain against the same trust store and fails the same way, so the retry
layers ask the attribution below - the one that decides the remedy - before
they send again: :func:`tls_failure_repeats_on_retry` for the harness's turn
and summary retries (through the ``repeats_on_retry`` mark
:func:`describe_provider_error` puts on the message), an OAuth refresh's
``retry_reason`` and the Copilot device-code poll, and
:func:`is_tls_verification_error` for the Codex adapter's connection retries,
which reuse one client. Before #551 the turn retried it on its ``Connection
error.`` text, and the TUI stacked this remedy five times. The one failure a
retry on a new client can pass is one a strict relaxation answers
(:func:`_strict_relaxation_answers`). Not among these layers: the OpenAI and
Anthropic SDKs' own connection retries inside one request (``max_retries``,
default 2), so such a request still makes 3 handshakes before it fails.
"""

from __future__ import annotations

import re
import ssl
import sys
from collections.abc import Callable

# Substrings that mark a TLS trust failure across httpx / OpenSSL / SDK wrappers.
# Cert-specific ONLY: a bare "SSL" / "ConnectError" substring would drag 401s,
# DNS failures and connection-refused into the TLS branch.
#
# The second group is truststore's OS verifiers (#411). On macOS and Windows
# truststore hands the chain to the operating system, whose error names no
# OpenSSL text at all. Only texts that name an untrusted issuer are listed - the
# one failure whose remedy is the default below - so a wrapper that kept only
# the text of a hostname or validity failure gets no remedy rather than the CA
# one; the error itself carries a code that does tell them apart (see the codes
# below), and :func:`_carried_verification_error` reaches it behind httpcore's
# ``from None``. Two truststore texts name no cause and are not listed (review
# round 2): macOS <= 10.13's 'Recoverable trust failure occurred', which an
# untrusted root, a wrong host and an expired leaf all give there (measured with
# that branch forced, see :data:`_MACOS_10_13_RESULTS`), and Windows'
# 'Certificate chain policy error <code>', which is listed only with the two
# untrusted-issuer codes in it.
#
# No marker is matched on a chain a server answered with an HTTP status (review
# round 2, :func:`_answered`): there the text is the server's - a 400 that
# quotes the user's own words, a proxy's page about its upstream - not this
# machine's verifier.
_TLS_MARKERS: tuple[str, ...] = (
    "CERTIFICATE_VERIFY_FAILED",
    "certificate verify failed",
    "self-signed certificate",
    "self signed certificate",
    "unable to get local issuer certificate",
    # macOS, Security framework errSecNotTrusted: '“localhost” certificate is not
    # trusted' (measured, macOS 26.6, truststore 0.10.4; English for a CLI process
    # even with AppleLanguages ko-KR). It is also the end of truststore's macOS
    # <= 10.13 text 'User specified that certificate is not trusted', which this
    # attributes and :func:`_tls_hint` then answers with no remedy.
    "certificate is not trusted",
    # Windows: FormatMessageW of CERT_E_UNTRUSTEDROOT (0x800B0109) and
    # CERT_E_CHAINING (0x800B010A), the en-US system text truststore/_windows.py
    # passes on (read off the table, not measured: no Windows here). The text is
    # localized; the code is not, and the code is what the hint reads.
    "which is not trusted by the trust provider",
    "could not be built to a trusted root authority",
    # Windows: truststore's own text when FormatMessageW has none for the code,
    # f"Certificate chain policy error {code:#x} [{index}]" - for those same two
    # codes only. With any other code in it the text names no untrusted issuer.
    "Certificate chain policy error 0x800b0109",
    "Certificate chain policy error 0x800b010a",
)

# Verify codes, split by what the user must actually DO. The OpenSSL
# X509_V_ERR_* codes are confirmed against a real local handshake — NOT read off
# a table: 62/10/9 all still stringify with "certificate verify failed", so the
# _TLS_MARKERS above cannot tell them apart from an untrusted issuer.
#
# truststore sets ``verify_code`` too (#411), from the OS verifier: a negative
# Security-framework OSStatus on macOS 10.14 and later, the unsigned
# CertVerifyCertificateChainPolicy ``dwError`` (an HRESULT, read as a DWORD) on
# Windows. Neither of those ranges meets OpenSSL's 0-95. On macOS <= 10.13 it
# is the SecTrustResultType instead, 0-7, which does fall inside OpenSSL's
# range: that path is told apart by truststore's own texts
# (:data:`_MACOS_10_13_RESULTS`), never by its code. The macOS codes were
# measured against a probe CA loaded as the only anchor (macOS 26.6): a
# wrong-host leaf is -67602, an expired leaf AND a not-yet-valid one are both
# -67818, an unanchored chain is -67843 (errSecNotTrusted, the default remedy).
# -67819 is Apple's documented not-yet-valid code, never seen here. The Windows
# codes are read off winerror.h, since no Windows box ran them.
_HOSTNAME_MISMATCH_CODE = 62  # X509_V_ERR_HOSTNAME_MISMATCH
_MACOS_HOSTNAME_MISMATCH = -67602  # errSecHostNameMismatch
_WINDOWS_CN_NO_MATCH = 0x800B010F  # CERT_E_CN_NO_MATCH
_HOSTNAME_CODES: frozenset[int] = frozenset(
    {_HOSTNAME_MISMATCH_CODE, _MACOS_HOSTNAME_MISMATCH, _WINDOWS_CN_NO_MATCH}
)

# Both halves of the validity window. OpenSSL raises 10 when the clock is past
# not_valid_after and 9 when it is before not_valid_before; a skewed local clock
# (a VM with a bad RTC, a laptop resuming from suspend) produces EITHER, so they
# share one remedy. 9 must not fall through to the untrusted-issuer default: the
# chain verified fine there, and no CA can help someone whose date is wrong.
# macOS and Windows each give one code for both ends of the window.
_CERT_EXPIRED_CODE = 10  # X509_V_ERR_CERT_HAS_EXPIRED
_CERT_NOT_YET_VALID_CODE = 9  # X509_V_ERR_CERT_NOT_YET_VALID
_MACOS_CERT_EXPIRED = -67818  # errSecCertificateExpired
_MACOS_CERT_NOT_YET_VALID = -67819  # errSecCertificateNotValidYet
_WINDOWS_CERT_EXPIRED = 0x800B0101  # CERT_E_EXPIRED
_CLOCK_CODES: frozenset[int] = frozenset(
    {
        _CERT_EXPIRED_CODE,
        _CERT_NOT_YET_VALID_CODE,
        _MACOS_CERT_EXPIRED,
        _MACOS_CERT_NOT_YET_VALID,
        _WINDOWS_CERT_EXPIRED,
    }
)

# OS verdicts that no remedy here fits, so they get none (#411 review round 2).
#
# macOS rejects these certificates THEMSELVES, whatever their issuer - a rule no
# CA install can satisfy, so the CA remedy would name the wrong lever (the #99
# failure mode: the user installs the CA, or already has, and nothing changes).
# Measured on macOS 26.6 against a probe CA: a leaf valid for 900 days and a
# leaf with an RSA-1024 key are rejected with these codes whether or not the CA
# is an anchor, so "the chain is fine" would not be true either; a leaf whose
# EKU lacks serverAuth gives -67609 once the chain is anchored (unanchored, the
# chain failure -25318 comes first).
_MACOS_REJECTS_THE_CERTIFICATE: frozenset[int] = frozenset(
    {
        -67901,  # errSecCertificateValidityPeriodTooLong: 'is not standards compliant'
        -67735,  # errSecUnsupportedKeySize: 'is using a broken key size'
        -67609,  # errSecInvalidExtendedKeyUsage: 'is not permitted for this usage'
    }
)

#: truststore's own text for each SecTrustResultType on macOS <= 10.13
#: (``_verify_peercerts_impl_macos_10_13``), where ``verify_code`` is that result,
#: 0-7. None of them names a cause: with the branch forced on macOS 26.6 an
#: untrusted root, a wrong host, an expired and a not-yet-valid leaf ALL gave
#: 'Recoverable trust failure occurred' and 5; 3 is a user's explicit "never
#: trust". So none gets a remedy, by code or by text alone.
_MACOS_10_13_RESULTS: tuple[str, ...] = (
    "Invalid trust result type",
    "User confirmation required",
    "User specified that certificate is not trusted",
    "Recoverable trust failure occurred",
    "Fatal trust failure occurred",
    "Other error occurred, certificate may be revoked",
    "Unknown trust result: ",
)


def _no_remedy_fits(code: int | None, text: str) -> bool:
    """Is this an OS verdict none of the remedies here is true for?"""

    if code in _MACOS_REJECTS_THE_CERTIFICATE:
        return True
    return any(result in text for result in _MACOS_10_13_RESULTS)


# The two halves of the untrusted-issuer remedy that hold regardless of which
# trust store is live. Assembled by :func:`_untrusted_issuer_hint`, which picks
# the middle sentence off the binding rather than asserting one.
_TLS_INTERCEPT_INTRO: str = (
    "TLS certificate verification failed — a proxy or firewall is likely "
    "intercepting HTTPS with a private root CA (common on corporate networks).\n"
)


def _tls_cert_file_escape() -> str:
    """Point ``SSL_CERT_FILE`` at a bundle, with a command for THIS platform.

    Read off :data:`sys.platform` at call time (#411 review round 2). The one
    example used to be ``export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt``
    everywhere, and since #411 this remedy reaches macOS and Windows users too:
    on macOS that directory is empty (following the line literally failed with
    ``[Errno 2] No such file or directory``), and ``export`` is not a command in
    PowerShell. Linux keeps the Debian/Ubuntu bundle, which holds a CA installed
    with ``update-ca-certificates``; macOS, Windows and other systems name the
    bundle the TLS guide's recipe makes (``~/.aelix/ca-bundle.pem``: certifi's
    roots plus the CA), Windows with PowerShell's ``$env:``. No example carries a
    shell comment: zsh, macOS's default shell, does not take ``#`` as one at the
    prompt.

    The last line names a COPY of certifi's bundle (#556). It used to say
    "append the corporate CA to the bundle printed by ``python -m certifi``",
    which reads as editing the ``cacert.pem`` inside the installed certifi
    package - the step the TLS guide warns against, because reinstalling or
    upgrading certifi replaces that file and the failure comes back with no
    clue (measured: a CA appended in place was gone after
    ``uv pip install --reinstall certifi``). Where the example names
    ``ca-bundle.pem`` the line says what that file is, the guide's recipe; on
    Linux, whose example is the system's own bundle, the copy is the
    alternative. Either way the remedy stays five lines.
    """

    copy = (
        "a copy of the bundle `python -m certifi` prints with the corporate CA "
        "appended (not certifi's own file, which an upgrade silently reverts), "
        "then retry."
    )
    if sys.platform == "win32":
        lead = "point SSL_CERT_FILE at a bundle that includes it, in PowerShell:\n"
        example = '  $env:SSL_CERT_FILE = "$HOME\\.aelix\\ca-bundle.pem"\n'
        tail = f"where ca-bundle.pem is {copy}"
    elif sys.platform.startswith("linux"):
        lead = "point SSL_CERT_FILE at a bundle that includes it:\n"
        example = "  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt\n"
        tail = f"or at {copy}"
    else:
        lead = "point SSL_CERT_FILE at a bundle that includes it:\n"
        example = '  export SSL_CERT_FILE="$HOME/.aelix/ca-bundle.pem"\n'
        tail = f"where ca-bundle.pem is {copy}"
    return f"{lead}{example}{tail}"


# Deliberately never mentions SSL_CERT_FILE: the chain verified fine, so a CA
# bundle is the wrong lever and offering it sends the user down a dead end.
_TLS_HOSTNAME_HINT: str = (
    "TLS certificate verification failed — the server presented a certificate "
    "that is not valid for the hostname aelix connected to.\nThe trust chain "
    "itself is fine, so adding a CA will not help: check this provider's base "
    "URL for a typo, or a proxy/gateway answering for a different host."
)

# Covers BOTH clock codes: the recovered cause text already names which end of
# the window was violated ("certificate has expired" / "certificate is not yet
# valid"), so this states the shared remedy instead of guessing a direction.
_TLS_CLOCK_HINT: str = (
    "TLS certificate verification failed — the server's certificate is outside "
    "its validity window (expired, or not yet valid).\nThe trust chain itself is "
    "fine, so adding a CA will not help: check this machine's clock first (a "
    "wrong system date puts a valid certificate outside its window either way).\n"
    "Otherwise the endpoint's operator must renew it."
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
            "this network.\nIf it cannot be installed system-wide (e.g. inside a "
            f"container), {_tls_cert_file_escape()}"
        )
    return (
        f"{_TLS_INTERCEPT_INTRO}This process is verifying against certifi's "
        "public-root bundle ONLY — the operating system's certificate store is "
        "NOT being consulted,\nso a root CA installed system-wide (the reason VS "
        "Code and your browser work on this network) cannot help by itself. "
        f"Instead, {_tls_cert_file_escape()}"
    )


#: OpenSSL verify codes raised ONLY under ``X509_V_FLAG_X509_STRICT``, with the
#: text OpenSSL gives each (``X509_verify_cert_error_string``). The strict remedy
#: below may claim these and nothing else.
#:
#: An ALLOWLIST since #192. It was a denylist - "strict is on and the code is not
#: 2/18/19/20" - and on a 3.13 interpreter that sent every other failure here: a
#: certificate whose signature is genuinely bad (7), an issuer that is not a CA
#: (79), and every error with no code at all (a marker-only match, the #99
#: shape) were all told "an RFC 5280 rule, not trust; reinstall on 3.12". No CI
#: leg ran 3.13, so nothing saw it until the matrix grew one (ADR-0241 had named
#: code 7; at 8f7d98aa the 3.13 suite failed nine rows on it, plus two guards
#: in test_tls_strict.py that assumed a pre-3.13 interpreter).
#:
#: The source is ``include/openssl/x509_vfy.h``'s block "Errors in case a check
#: in X509_V_FLAG_X509_STRICT mode fails" (78-94, numbering stable across 3.x),
#: MINUS 79, which is neither strict-only nor never strict's and gets its own
#: hedged remedy (:func:`_invalid_ca_hint`). 85, 86, 89 and 92 were measured
#: strict-only on 3.13.13 / OpenSSL 3.5.6 (fail with the flag, pass without);
#: 7 and 20 fail both ways. ``tests/providers/test_tls_strict.py`` repeats
#: those handshakes and holds these messages equal to the real ones.
_STRICT_ONLY: dict[int, str] = {
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

#: ``X509_V_ERR_INVALID_CA``. OpenSSL raises it for two different chains and
#: the code cannot say which (measured on 3.12.13 and 3.13.13 / OpenSSL 3.5.6,
#: the flag forced both ways):
#:
#: * an issuer marked ``CA:FALSE`` - 79 with strict on AND off, so strict is
#:   not the cause and the trust advice is right;
#: * a trust anchor with no basicConstraints whose keyUsage has keyCertSign
#:   (an old appliance's root) - 79 ONLY with strict on, passing without it,
#:   so strict IS the cause.
#:
#: So neither remedy alone may claim it: on a strict interpreter it gets both
#: causes and the ``openssl s_client`` step that tells them apart.
_INVALID_CA_CODE = 79
_INVALID_CA_MESSAGE = "invalid CA certificate"

#: The confirm step both strict remedies give: ``openssl`` does not enforce the
#: strict clauses, so ``0 (ok)`` there means the chain is trusted and only
#: strict rejected it.
_STRICT_CONFIRM_COMMAND: str = (
    "  openssl s_client -connect <host>:443 -servername <host> </dev/null "
    "2>&1 | grep 'Verify return code'\n"
)
_STRICT_WAY_OUT: str = (
    "reinstall aelix on Python 3.12 (`uv tool install --python 3.12 "
    "--force …`) or report this host so the check can be relaxed."
)


def _is_strict_only(code: int | None, text: str) -> bool:
    """Did OpenSSL reject this on a clause only the strict flag enforces?

    By code when there is one. Without one - a wrapper that kept only the text -
    by OpenSSL's own message for a strict-only code, so the reporter's
    ``Missing Authority Key Identifier`` re-raised as a bare ``RuntimeError``
    still reads as what it is. Anything else is not strict's to claim.
    """

    if code is not None:
        return code in _STRICT_ONLY
    return any(message in text for message in _STRICT_ONLY.values())


def _strict_hint(code: int | None, text: str) -> str | None:
    """The remedy when RFC-5280 strictness — not trust — is what rejected the chain.

    WHY THIS BRANCH EXISTS. Without it, a strict-only rejection fell through to
    :func:`_untrusted_issuer_hint`, which tells the user to install the corporate
    CA and to set ``SSL_CERT_FILE``. Measured against a real report: verify code
    **85** (``Missing Authority Key Identifier``) received *byte-identical* advice
    to code 20 — on a machine where ``SSL_CERT_FILE`` was ALREADY set to the very
    path suggested and ``openssl verify`` returned ``0 (ok)``. The advice named
    the two things the user had already done. That is the issue-#99 failure mode
    repeating one level up: a remedy that cannot work reads as "you did it wrong".

    Two shapes:

    * aelix already **measured** the rejection (it re-verified the same host with
      strict cleared and it passed) — then say so, and say what was done about it.
    * aelix could not measure it (no host on the exception, so no re-check) — then
      say strict is a *likely* cause and how to confirm, without claiming it.

    The second shape is offered only for a code in :data:`_STRICT_ONLY` (or, with
    no code, its message): a failure strict cannot have caused gets ``None`` here
    and the trust remedy, whichever interpreter is running (#192). Code 79 (or,
    with no code, its message) gets :func:`_invalid_ca_hint`, which names both
    of its causes instead of choosing one.
    """

    from aelix_ai.providers._tls_strict import session_relaxation, strict_is_enabled

    relaxation = session_relaxation()
    if relaxation is not None:
        return relaxation.remedy()

    if not strict_is_enabled():
        return None
    if _is_invalid_ca(code, text):
        return _invalid_ca_hint()
    if not _is_strict_only(code, text):
        return None

    return (
        "TLS certificate verification failed on an RFC 5280 conformance rule, not "
        "on trust. Python 3.13 turned on strict certificate checking by default.\n"
        "Python 3.12, `openssl`, curl and browsers do not enforce it, which is why "
        "the same host works in every other tool on this machine.\n"
        "Certificates minted on the fly by an intercepting proxy commonly fail "
        "these rules even when their root CA is correctly installed. Confirm with:\n"
        f"{_STRICT_CONFIRM_COMMAND}"
        f"If that reports `0 (ok)`, the CA is trusted and adding one will not help: {_STRICT_WAY_OUT}"
    )


def _is_invalid_ca(code: int | None, text: str) -> bool:
    """Is this OpenSSL's 79, by code or - with none - by its message?"""

    if code is not None:
        return code == _INVALID_CA_CODE
    return _INVALID_CA_MESSAGE in text


def _invalid_ca_hint() -> str:
    """Code 79 on a strict interpreter: two causes, and how to tell them apart.

    Never asserts either. The ``CA:FALSE`` issuer fails without strict too, so
    the strict remedy alone would send that user to Python 3.12 for nothing; a
    root with no basicConstraints fails ONLY with strict, so the trust remedy
    alone would tell that user to install the CA they already trust (the
    review-round-2 regression of #192). ``openssl s_client`` enforces neither
    strict clause, so its verdict picks the branch.

    Six lines of at most 200 characters, each branch's action on a line of its
    own (see the module docstring): round 2's ten lines lost cause 2's action
    to the TUI's bound, which showed ``... (2 more lines omitted)`` there.
    """

    return (
        "TLS certificate verification failed: a certificate in the chain is not "
        "a valid CA (`invalid CA certificate`). Two problems give that error, and "
        "it alone cannot tell which:\n"
        "  1. Python 3.13's strict checking (on here) also rejects a root CA with "
        "no Basic Constraints extension, an older shape Python 3.12, `openssl`, "
        "curl and browsers accept.\n"
        "  2. A certificate used as a CA is not one (`CA:FALSE`): a broken chain "
        "from the server or proxy, or the wrong certificate trusted as the CA. "
        "Strict is not the cause.\n"
        f"Tell them apart with:{_STRICT_CONFIRM_COMMAND}"
        f"`0 (ok)` means 1: the CA is already trusted — {_STRICT_WAY_OUT}\n"
        "Anything else means 2: get the proxy's root CA from whoever runs it and "
        f"{_trust_store_action()}"
    )


def _trust_store_action() -> str:
    """The trust remedy's action in one line, for :func:`_invalid_ca_hint`.

    The full remedy is five lines on its own and the hedge has no room for it:
    the TUI keeps 8 lines of an error, the first two are the error and a blank
    line, and the hedge's other five lines are its framing, the two causes, the
    check and cause 1's action.
    """

    if _os_trust_store_active():
        return (
            "install it system-wide (aelix trusts the OS certificate store) or "
            "point SSL_CERT_FILE at a bundle that includes it."
        )
    return (
        "point SSL_CERT_FILE at a bundle that includes it (this process does not "
        "read the OS certificate store)."
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
    uses :func:`_raw_chain`, and attribution looks behind the cut only for the
    verification error the exception itself carries (:func:`_carried_verification_error`).
    The cause TEXT keeps this walk (#411): crossing the cut for every error
    turned a refused connection's ``All connection attempts failed`` into anyio's
    ``multiple connection attempts failed (2 sub-exceptions)``, measured.
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


def _carries(exc: BaseException, inner: BaseException) -> bool:
    """Does ``exc`` re-raise ``inner`` rather than replace it? (#411)

    httpcore's ``map_exceptions`` builds ``ConnectError(exc)`` - the error it
    maps is its argument, and its text is that error's text - and
    ``connection_pool`` then re-raises it ``from None``, which clears its
    ``__cause__`` and cuts its ``__context__``: the very error it carries. An
    unrelated error raised ``from None`` inside an ``except`` holds neither the
    error behind the cut as an argument nor exactly its text.
    """

    if any(arg is inner for arg in exc.args):
        return True
    text = str(inner)
    return bool(text) and str(exc) == text


def _carried_verification_error(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    """The verification error ``exc`` re-raises from behind a ``from None`` (#411).

    httpcore's ``ConnectError`` is the case: its ``__cause__`` is cleared, its
    ``__context__`` is cut, and the error behind the cut is its own argument.
    Until #411 a TLS failure there was attributed only when the wrapper's TEXT
    named an OpenSSL marker, and truststore's OS verifiers on macOS and Windows
    put none in it: the CLI printed ``Connection error. — ('“localhost”
    certificate is not trusted',)`` and no remedy (measured, macOS 26.6,
    truststore 0.10.4).
    """

    if exc.__cause__ is not None or not exc.__suppress_context__:
        return None
    context = exc.__context__
    if isinstance(context, ssl.SSLCertVerificationError) and _carries(exc, context):
        return context
    return None


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


def _attribute(obj: object, name: str) -> object:
    """``getattr`` that also survives a property that raises (httpx's ``request``)."""

    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 - any failure means "not there"
        return None


def _answered(exc: BaseException) -> bool:
    """Did a server answer this request with an HTTP status? (#411 review round 2)

    The SDKs' status errors carry it as an int ``status_code`` (openai,
    anthropic), ``code`` (google-genai's ``APIError``, whose ``response`` may be
    a wrapper with neither), ``status`` (the Codex adapter's
    ``_CodexHTTPError``), or on their ``response`` (httpx's
    ``HTTPStatusError``: ``status_code``; an aiohttp response: ``status``).
    aelix's own OAuth sign-in and refresh errors carry it as
    ``oauth_status_code`` (:func:`aelix_ai.oauth._helpers.oauth_http_error`,
    which ``/login`` passes to :func:`describe_provider_error`).
    None of them is an int on the transport errors under a TLS failure here
    (``ssl``'s, httpcore's, httpx's, the SDKs' ``APIConnectionError``,
    requests' ``SSLError`` with its ``response`` None). A status means a server read
    the request and answered it - over a TLS connection this machine had
    already verified, or over none at all - so a certificate phrase in such an
    error's text is the server's words, never this machine's verifier:
    measured, a plain-``http://`` 400 that quoted the user's prompt
    ``... certificate is not trusted ...`` was given the corporate-CA remedy.

    Known limit: an error that carries no int status is still read by its
    text. An openai ``APIError`` raised mid-stream whose body ``code`` is a
    string (``'server_error'``) is one: certificate words in it still get the
    CA remedy (measured on an error built in-process).
    """

    holders = (
        (exc, ("status_code", "code", "status", "oauth_status_code")),
        (_attribute(exc, "response"), ("status_code", "status")),
    )
    for holder, names in holders:
        if holder is None:
            continue
        for name in names:
            value = _attribute(holder, name)
            if isinstance(value, int) and not isinstance(value, bool):
                return True
    return False


def _tls_error(exc: BaseException) -> BaseException | None:
    """The TLS trust failure in ``exc``'s chain, or :data:`None`.

    Two chains, because attribution and diagnosis need different ones. The
    attribution pass respects ``__suppress_context__`` so an unrelated nearby
    error cannot claim a TLS cause. Only once a TLS failure IS attributed does
    the second pass cross a suppressed link, to reach the OpenSSL error carrying
    the ``verify_code`` that :func:`_tls_hint` branches on.

    That second pass is what made the code branches reachable in production:
    the real chain is ``APIConnectionError → httpx.ConnectError →
    httpcore.ConnectError → ssl.SSLCertVerificationError``, and the first three
    carry no code. Returning the wrapper would pin every real request's hint to
    the untrusted-issuer default — telling a user with a hostname mismatch or a
    skewed clock to install a corporate root CA. Since #411 the attribution pass
    also takes the verification error a link carries from behind the cut
    (:func:`_carried_verification_error`), which is what attributes a truststore
    OS-verifier failure whose text names no marker.

    TEXT attributes only on a chain no server answered (#411 review round 2,
    :func:`_answered`): with an HTTP status anywhere in the attribution chain -
    the status error itself, or a wrapper raised from it that repeats its text -
    no link's text counts, the long-standing ``certificate verify failed``
    marker included. The verification error itself, or one a link carries, still
    counts: it is an object only a verifier raises, never a server's text.
    """

    chain = _causes(exc)
    answered = any(_answered(e) for e in chain)
    for e in chain:
        if isinstance(e, ssl.SSLCertVerificationError):
            return e
        if not answered and any(m in str(e) for m in _TLS_MARKERS):
            for inner in _raw_chain(e):
                if isinstance(inner, ssl.SSLCertVerificationError):
                    return inner
            return e
        carried = _carried_verification_error(e)
        if carried is not None:
            return carried
    return None


def is_tls_verification_error(exc: BaseException) -> bool:
    """True when ``exc`` (or anything in its cause chain) is a TLS trust failure."""

    return _tls_error(exc) is not None


def _strict_relaxation_answers(tls: BaseException) -> bool:
    """Does the strict relaxation in force change how the next attempt verifies? (#551)

    ``tls`` is the verification failure :func:`_tls_error` attributed. A
    relaxation (:func:`aelix_ai.providers._tls_strict.maybe_relax_strict_for_session`)
    clears ``VERIFY_X509_STRICT`` from every context built after it, so a
    request that failed on strict passes on its next attempt, which builds a
    new client - the turn's retry has been that next attempt since #184 ("the
    harness already retries 'Connection error.' on its own"). It answers a
    failure whose code is the one it was measured on (the failure that caused
    it, or a sibling request that failed the same way before it took effect),
    or a code only strict raises (:data:`_STRICT_ONLY`: a context built without
    strict cannot have raised it). Every other failure verifies the same way
    the next time, relaxation or not; so does every failure while no relaxation
    is in force, and one with no code (a text-only match, which no relaxation
    was measured on).

    Known limit: code 79 under a relaxation measured on 79 is answered even
    when it came from a relaxed context (a ``CA:FALSE`` issuer gives 79 both
    ways), so that failure is retried as before #551.
    """

    from aelix_ai.providers._tls_strict import session_relaxation

    relaxation = session_relaxation()
    if relaxation is None:
        return False
    code = getattr(tls, "verify_code", None)
    if not isinstance(code, int) or isinstance(code, bool):
        return False
    return code == relaxation.verify_code or code in _STRICT_ONLY


def tls_failure_repeats_on_retry(exc: BaseException) -> bool:
    """Would sending this request again fail TLS verification the same way? (#551)

    True when ``exc`` is a TLS certificate verification failure on this
    machine - :func:`is_tls_verification_error`'s attribution, the one that
    decides the remedy, so a failure is not retried exactly when it is told
    what to fix - and no strict relaxation in force answers it
    (:func:`_strict_relaxation_answers`). An HTTP error a server answered is
    never one, whatever certificate words it carries (:func:`_answered`).

    For a retry that opens a NEW client: the Copilot device-code poll, an
    OAuth refresh's ``retry_reason``, and the turn (through
    :func:`describe_provider_error`'s mark). A retry on the SAME client - the
    Codex adapter's connection retries - can never pass a verification
    failure, since that client's context is fixed, and asks
    :func:`is_tls_verification_error` instead.
    """

    tls = _tls_error(exc)
    return tls is not None and not _strict_relaxation_answers(tls)


def _tls_hint(err: BaseException) -> str | None:
    """The remedy matching ``err``'s verify code, or :data:`None` when none fits.

    ``verify_code`` / ``verify_message`` exist ONLY on a verifier-raised error:
    OpenSSL's, or truststore's OS verifier on macOS and Windows (#411, its codes
    beside :data:`_HOSTNAME_CODES`). A synthetic
    ``ssl.SSLCertVerificationError("...")`` and a string-marker match on a
    non-ssl wrapper both carry NEITHER, so the read must tolerate their absence
    and fall back to the untrusted-issuer remedy — that is the shape #99
    reported, and the shape every synthetic test constructs.

    An OS-verifier code never reaches the strict remedy: truststore verifies
    there with OpenSSL's own verification switched off, so OpenSSL's strict flag
    cannot have caused it, and no OS code is in :data:`_STRICT_ONLY` or 79. The
    OS verdicts :func:`_no_remedy_fits` names get no remedy at all (#411 review
    round 2): the failure is still a TLS verification failure, and its own text
    is all that is shown.
    """

    code = getattr(err, "verify_code", None)
    if _no_remedy_fits(code, str(err)):
        return None
    if code in _HOSTNAME_CODES:
        return _TLS_HOSTNAME_HINT
    if code in _CLOCK_CODES:
        return _TLS_CLOCK_HINT

    strict = _strict_hint(code, str(err))
    if strict is not None:
        return strict

    return _untrusted_issuer_hint()


#: Every line boundary :meth:`str.splitlines` knows, ``\r\n`` as one.
_LINE_BREAK = re.compile("\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


def quote_model_text(text: str) -> str:
    """:func:`~aelix_ai.oauth._helpers.quote_server_text` for a MODEL request's error.

    The same quoting - steering characters deleted, blank runs collapsed,
    trimmed, cut at 512 code points - except that a line break becomes a space
    first, so a multi-line error keeps its word boundaries: ``upstream failed``
    and ``retry later`` on two lines read ``upstream failed retry later``, not
    ``upstream failedretry later`` (review round 3, #186). The OAuth sites keep
    deleting them.
    """

    # Imported here: see describe_provider_error.
    from aelix_ai.oauth._helpers import quote_server_text

    return quote_server_text(_LINE_BREAK.sub(" ", text))


def _raw_text(exc: BaseException) -> str:
    """The text of ``exc`` as the classifiers read it before #186 quoted it.

    An exception that is bounded where it is raised (``_CodexHTTPError``)
    carries that text as ``classifier_text``; any other is its ``str()``.
    """

    carried = getattr(exc, "classifier_text", None)
    return carried if isinstance(carried, str) else str(exc)


def _cause_text(
    exc: BaseException, base: str, *, text_of: Callable[[BaseException], str]
) -> str | None:
    """The innermost cause message ``base`` does not already carry.

    Innermost-first because the outer wrappers are the uninformative ones
    ("Connection error.", or an empty ``httpx.ConnectError``); the OpenSSL error
    at the bottom is what names the reason AND the host. Substring-checked
    against ``base`` so a directly-raised error is not repeated back to itself.
    ``text_of`` is the quoting for the displayed message and the unquoted text
    for the classifiers' copy.
    """

    for e in reversed(_causes(exc)[1:]):
        text = text_of(e)
        if text and text not in base:
            return text
    return None


def _quoted(exc: BaseException) -> str:
    return quote_model_text(str(exc))


def _unquoted(exc: BaseException) -> str:
    return _raw_text(exc).strip()


def describe_provider_error(exc: BaseException, *, classifier_text: str | None = None) -> str:
    """Base message + the innermost real cause + a TLS remedy when relevant.

    Non-TLS errors keep their base message (plus the recovered cause), so this
    is a safe drop-in wherever an adapter builds
    ``err_msg = str(exc) if str(exc) else type(exc).__name__``.

    The cause is appended, never prepended: callers/tests anchor on the SDK's
    own leading text (e.g. ``startswith("Connection error.")``).

    THE ONE BOUNDARY a built-in adapter's error passes on its way into an
    :class:`~aelix_ai.streaming.AssistantErrorEvent` (#186, review round 2):
    the base message and the recovered cause are each quoted with
    :func:`quote_model_text` - line breaks to spaces, steering characters
    deleted, blank runs collapsed, trimmed, cut at 512 code points. Exception
    text is the other end's text here too: a proxy that refuses the CONNECT
    puts its own reason phrase into ``httpx.ProxyError`` before any response
    exists, and every adapter passed it on whole (55,000 characters with
    ``ESC[2J``, OSC 52 and ``ESC[?1049h`` measured on ``aelix -p``'s stderr for
    openai-codex, anthropic, openrouter and google). The TLS remedy below is
    aelix's own text and keeps its lines. Two server strings travel beside this
    boundary rather than through it, and their sites quote them with the same
    helper: OpenRouter's ``error.metadata.raw``, which ``openai_completions``
    appends on a line of its own, and the 401/403 text the Anthropic adapter
    raises as ``_AuthError``. An extension-registered provider builds its own
    message and does not pass here.

    The cut must not decide what the error IS (review round 3, #186): the
    overflow patterns and the harness's auto-retry regex read this string, and
    a ``context_length_exceeded`` code serialised after a long diagnostic, or a
    ``502`` deep in a proxy's page, fell past it. The returned text therefore
    carries the description as it was built before #186 quoted it - the same
    algorithm on the unquoted text - for
    :func:`aelix_ai.utils.overflow.classifier_text_of`, or ``classifier_text``
    when the caller passes the text its classifiers read before (the two Google
    adapters used ``str(exc)``).

    A TLS verification failure no strict relaxation answers is marked
    ``repeats_on_retry`` (#551, :func:`tls_failure_repeats_on_retry`), and the
    harness does not retry it: before #551 its ``Connection error.`` matched the
    retry pattern and the TUI showed the remedy once per attempt. The mark is
    read after any strict relaxation this failure caused - every adapter that
    relaxes calls :func:`~aelix_ai.providers._tls_strict.
    maybe_relax_strict_for_session` first - so the failure that caused one
    stays retried. Like :attr:`classifier_text`, it lives as long as the
    message object does.
    """

    from aelix_ai.utils.overflow import with_classifier_text

    base = _quoted(exc) or type(exc).__name__
    cause = _cause_text(exc, base, text_of=_quoted)
    if cause is not None:
        base = f"{base} — {cause}"
    tls = _tls_error(exc)
    remedy = None if tls is None else _tls_hint(tls)
    hint = "" if remedy is None else f"\n\n{remedy}"
    if classifier_text is None:
        raw = _raw_text(exc) or type(exc).__name__
        raw_cause = _cause_text(exc, raw, text_of=_unquoted)
        if raw_cause is not None:
            raw = f"{raw} — {raw_cause}"
        classifier_text = raw + hint
    repeats = tls is not None and not _strict_relaxation_answers(tls)
    return with_classifier_text(base + hint, classifier_text, repeats_on_retry=repeats)


__all__ = [
    "describe_provider_error",
    "is_tls_verification_error",
    "quote_model_text",
    "tls_failure_repeats_on_retry",
]
