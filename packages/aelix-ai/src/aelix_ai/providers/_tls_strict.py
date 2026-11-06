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
persisted. Persisting it, so that the next run starts relaxed, would relax on a
measurement made in another process, against a chain that may since have
changed: it is deliberately not done (#703).

## Never silent, and never an error (#703)

When the relaxation is made, the listeners registered with
:func:`add_relaxation_listener` are called once with it: the CLI shows
:meth:`Relaxation.notice`, one line - dim in the TUI, on stderr in ``-p``,
``--mode json`` and ``--mode rpc``. A relaxation made before any listener was
registered (an extension's ``session_start`` that sent a request) is given to
the first one registered after it, so it is still announced once. The failure
that caused it is not shown as an error at all: it carries the ``resend`` retry
verdict (#572), and the built-in adapters send that request again themselves,
once, on a new client (:mod:`aelix_ai.providers._strict_resend`), as an OAuth
token refresh does (``aelix_ai.oauth._high_level``), so the prompt is answered
as if nothing had failed. Before #703 the user saw the error and a four-line
note (:meth:`Relaxation.remedy`) on every launch, and the turn waited out a
retry. The note is still what a LATER certificate failure gets while the
relaxation is in force: that failure is not strictness, and the note says so.
Only this process knows about the relaxation
(``aelix_ai.providers._trust_store.describe_trust_store`` reports it here): the
``aelix status`` command runs in a process of its own and shows strict ``ON``,
so the notice points at the TLS guide (``aelix docs tls``).

The confirmation handshakes go where the clients' requests go: through the
proxy an ``httpx`` client would use for that host (``HTTPS_PROXY``,
``ALL_PROXY``, ``NO_PROXY``, read as httpx reads them; :func:`_proxy_for`), as
an HTTP ``CONNECT`` tunnel. An explicit corporate proxy that intercepts TLS is
where such a chain comes from, and a direct connection to the host may not
exist at all; measured direct, that proxy's chain was never relaxed. Only an
``http://`` proxy is tunnelled (with the Basic credentials its URL carries, as
httpx sends them); through an ``https://`` or SOCKS proxy the check answers
"no" and nothing is relaxed.

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

import base64
import contextlib
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

__all__ = [
    "Relaxation",
    "TlsFailure",
    "add_relaxation_listener",
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
#: Called once with the relaxation when this process makes it (#703).
_listeners: list[Callable[[Relaxation], object]] = []
#: The relaxation was made while no listener was registered: the next one hears it.
_unannounced = False


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
        """Both sentences on one line, as ``aelix status`` prints it."""

        return f"{self.what_happened()} {self.STILL_ENFORCED}"

    def what_happened(self) -> str:
        return (
            f"RFC-5280 strict verification relaxed for this session after "
            f"{self.host} failed it ({self._detail()}) but verified against this "
            f"machine's trust store without it."
        )

    def notice(self) -> str:
        """The one line the CLI shows when the relaxation is made (#703).

        The host, the strict rule its chain failed, and where to read about it:
        the TLS guide, whose section 4 says what this is (``aelix docs tls``,
        the alias of ``tls-and-corporate-ca``). Not ``aelix status``: that
        command is a process of its own and shows strict ``ON``, never this
        session's relaxation. Not an error and not advice: aelix measured the
        cause and dealt with it, and the request is sent again. One line, so it
        says once what :meth:`remedy` used to say in four after an error, on
        every launch.

        40 fixed characters plus the host and the rule, so it fits a 120-column
        terminal for the hosts a proxy usually meets with the strict rules it
        usually breaks: 118 at the longest, for ``api.business.githubcopilot.com``
        and ``Basic Constraints of CA cert not marked critical`` (fix round 2;
        the line was ``TLS: RFC 5280 strict relaxed for this session (...) —
        aelix docs tls-and-corporate-ca``, 125 for ``localhost`` and 162 there,
        and the TUI wrapped it). What "strict" and "relaxed" mean is the
        guide's to say.
        """

        return f"TLS strict relaxed: {self.host} ({self._detail()}) · aelix docs tls"

    def remedy(self, *, after_response: bool = False) -> str:
        """The note a later TLS error's remedy gives, laid out for the TUI.

        Since #703 the failure that made the relaxation is not shown as an error
        (:meth:`notice` says it, and the request is sent again), so this is what
        a certificate failure gets WHILE the relaxation is in force: one strict
        did not cause, and the last line says so - or the rare one whose request,
        sent again, failed once more.

        ``after_response`` (fix round 2): a failure the relaxation answers that
        came after the provider answered - an ``after_provider_response`` hook's
        own request - never gets the ``resend`` verdict (sending it again would
        send the answered request a second time), and strict is what rejected
        it, so "If the request still fails, the cause is not certificate
        strictness" read as if this failure were not strictness. Its last line
        says what happened instead (190 characters; the default one is 143).

        The TUI keeps 8 lines of an error and cuts each at 200 characters (see
        ``_error_hints``' module docstring); the error line and a blank line
        come first, so the note has 6. :meth:`what_happened` is 133 fixed
        characters plus the host plus the verify message, which ran past 200
        for real hosts (#192 review round 4: 211 for
        ``api.business.githubcopilot.com`` with code 89) and lost its end. So
        the host and the message each get a line of their own: the framing
        line and the last line are fixed (144 and 143 characters), the message
        line is 13 + the message (OpenSSL's longest, in 3.6, is 68), and a host
        longer than 192 characters continues on the next line instead of being
        cut. The note fits whole for a host of up to 576 characters (three
        lines; DNS allows 253) and a verify message of up to 187.
        """

        width = 200  # safe_error_for_terminal's max_chars_per_line
        first, rest = "  host: ", "        "
        room = width - len(first)
        host_lines = [
            f"{first if start == 0 else rest}{self.host[start : start + room]}"
            for start in range(0, max(len(self.host), 1), room)
        ]
        return "\n".join(
            [
                "RFC-5280 strict verification relaxed for this session after the host "
                "below failed it but verified against this machine's trust store "
                "without it.",
                *host_lines,
                f"  failed on: {self._detail()}",
                f"{self.STILL_ENFORCED} "
                + (
                    "This failure came after the provider had answered (a hook's own "
                    "request), so the relaxation does not send it again."
                    if after_response
                    else "If the request still fails, the cause is not certificate strictness."
                ),
            ]
        )

    def _detail(self) -> str:
        return self.verify_message or f"verify code {self.verify_code}"

    #: A sentence of its own so the TLS remedy can give it a line of its own:
    #: the TUI cuts every error line at 200 characters (#192 review round 3).
    STILL_ENFORCED: ClassVar[str] = (
        "Certificate verification, hostname checking and expiry are still enforced."
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
    The connection is the one they would make, too: through the proxy they
    would use (:func:`_connect`, #703 fix round 1), within the same 5 s.
    """

    try:
        context = ssl.create_default_context()
        if strict:
            context.verify_flags |= _STRICT
        else:
            context.verify_flags &= ~_STRICT
        deadline = time.monotonic() + _CONFIRM_TIMEOUT_SECONDS
        with _connect(host, port, deadline) as raw:
            raw.settimeout(_remaining(deadline))
            with context.wrap_socket(raw, server_hostname=host):
                return True
    except Exception:  # noqa: BLE001 — any failure is a "no", never a raise
        return False


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("the confirmation handshake ran out of time")
    return left


def _proxy_for(host: str, port: int) -> Any:
    """The proxy an ``httpx`` client sends ``https://host:port`` through, or ``None``.

    The clients' own reading of the environment (every built-in client is an
    ``httpx`` one with ``trust_env`` on): httpx's ``get_environment_proxies`` -
    ``HTTPS_PROXY``, ``ALL_PROXY`` and their lower-case forms, ``NO_PROXY``, and
    the system's settings where ``urllib`` reads them - matched most specific
    first, as ``httpx.Client`` matches its mounts. A private httpx name, pinned
    by the ``httpx<1.0`` requirement; if it is gone the check connects directly,
    as it did before.
    """

    import httpx

    try:
        from httpx._utils import URLPattern, get_environment_proxies
    except ImportError:  # pragma: no cover - an httpx without them
        return None
    url = httpx.URL(scheme="https", host=host, port=port)
    mounts = sorted(
        ((URLPattern(key), proxy) for key, proxy in get_environment_proxies().items()),
        key=lambda mount: mount[0],
    )
    for pattern, proxy in mounts:
        if pattern.matches(url):
            return None if proxy is None else httpx.URL(proxy)
    return None


def _connect(host: str, port: int, deadline: float) -> socket.socket:
    """A TCP connection to ``host:port``: direct, or an HTTP ``CONNECT`` tunnel."""

    proxy = _proxy_for(host, port)
    if proxy is None:
        return socket.create_connection((host, port), timeout=_remaining(deadline))
    if proxy.scheme != "http":
        raise OSError(f"no tunnel is opened through a {proxy.scheme} proxy")
    raw = socket.create_connection((proxy.host, proxy.port or 80), timeout=_remaining(deadline))
    try:
        target = (f"[{host}]" if ":" in host else host).encode("idna") + f":{port}".encode()
        head = b"CONNECT " + target + b" HTTP/1.1\r\nHost: " + target + b"\r\n"
        if proxy.username:
            basic = base64.b64encode(f"{proxy.username}:{proxy.password}".encode())
            head += b"Proxy-Authorization: Basic " + basic + b"\r\n"
        raw.settimeout(_remaining(deadline))
        raw.sendall(head + b"\r\n")
        answer = b""
        while b"\r\n\r\n" not in answer:
            raw.settimeout(_remaining(deadline))
            chunk = raw.recv(4096)
            if not chunk or len(answer) > 65536:
                raise OSError("the proxy closed or flooded the CONNECT")
            answer += chunk
        status, _, rest = answer.partition(b"\r\n\r\n")
        code = status.split(b"\r\n", 1)[0].split(b" ")
        if rest or len(code) < 2 or not code[1].startswith(b"2"):
            raise OSError("the proxy refused the CONNECT")
        return raw
    except BaseException:
        raw.close()
        raise


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
        _announce(_relaxation)
        return _relaxation
    except Exception:  # noqa: BLE001 — never turn one failure into two
        return None


def add_relaxation_listener(listener: Callable[[Relaxation], object]) -> Callable[[], None]:
    """Call ``listener`` with the relaxation when this process makes it (#703).

    Once per process, as the relaxation is made once: a failure while one is in
    force calls nothing. Called synchronously from
    :func:`maybe_relax_strict_for_session`, on whatever thread made the request
    that failed (the event loop's, for every built-in caller), before the
    request is sent again. A relaxation made while no listener was registered -
    a request an extension's ``session_start`` sent before the CLI registered
    its listener - is given to the first listener registered after it, here, at
    once (fix round 1); every later one hears nothing, so it is announced once.
    A listener that raises is skipped. Returns the function that removes it.
    """

    global _unannounced

    _listeners.append(listener)
    if _unannounced and _relaxation is not None:
        _unannounced = False
        _announce_to(listener, _relaxation)

    def remove() -> None:
        with contextlib.suppress(ValueError):
            _listeners.remove(listener)

    return remove


def _announce(relaxation: Relaxation) -> None:
    global _unannounced

    listeners = list(_listeners)
    _unannounced = not listeners
    for listener in listeners:
        _announce_to(listener, relaxation)


def _announce_to(listener: Callable[[Relaxation], object], relaxation: Relaxation) -> None:
    with contextlib.suppress(Exception):  # a notice must never fail the request
        listener(relaxation)


def session_relaxation() -> Relaxation | None:
    """The relaxation this process made, or ``None`` when nothing was relaxed.

    Only this process knows it: ``describe_trust_store`` reports it, and so
    does every TLS remedy given here. The ``aelix status`` command runs in a
    process of its own, where nothing was relaxed, and shows strict ``ON``
    (#703 fix round 1: this said it was what ``aelix status`` reports).
    """

    return _relaxation


def reset_strict_relaxation() -> None:
    """Undo everything this module did. For tests, which must not leak into each other."""

    global _relaxation, _original_factory, _unannounced
    if _original_factory is not None:
        ssl.create_default_context = _original_factory  # type: ignore[assignment]
        _original_factory = None
    _relaxation = None
    _unannounced = False
