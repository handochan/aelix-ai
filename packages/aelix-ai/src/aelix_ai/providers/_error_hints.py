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
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Callable

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
    "intercepting HTTPS with a private root CA (common on corporate networks).\n"
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
            f"container), {_TLS_CERT_FILE_ESCAPE}"
        )
    return (
        f"{_TLS_INTERCEPT_INTRO}This process is verifying against certifi's "
        "public-root bundle ONLY — the operating system's certificate store is "
        "NOT being consulted,\nso a root CA installed system-wide (the reason VS "
        "Code and your browser work on this network) cannot help by itself. "
        f"Instead, {_TLS_CERT_FILE_ESCAPE}"
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
    """

    from aelix_ai.utils.overflow import with_classifier_text

    base = _quoted(exc) or type(exc).__name__
    cause = _cause_text(exc, base, text_of=_quoted)
    if cause is not None:
        base = f"{base} — {cause}"
    tls = _tls_error(exc)
    hint = "" if tls is None else f"\n\n{_tls_hint(tls)}"
    if classifier_text is None:
        raw = _raw_text(exc) or type(exc).__name__
        raw_cause = _cause_text(exc, raw, text_of=_unquoted)
        if raw_cause is not None:
            raw = f"{raw} — {raw_cause}"
        classifier_text = raw + hint
    return with_classifier_text(base + hint, classifier_text)


__all__ = ["describe_provider_error", "is_tls_verification_error", "quote_model_text"]
