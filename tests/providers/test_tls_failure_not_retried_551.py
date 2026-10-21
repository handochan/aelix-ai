"""#551 - aelix does not retry a TLS certificate verification failure, so its remedy is shown once.

On 27fa21c5 a prompt to a server whose certificate this machine does not trust
was sent again and again: the OpenAI and Anthropic SDKs wrap the failure as
``APIConnectionError("Connection error.")``, ``Connection error`` is in the
turn's retry pattern (``harness/core.py`` ``_RETRYABLE_ERROR_PATTERN``, pi's
``connection.?error``), and every attempt verified the same chain against the
same trust store. Measured with the real CLI against a local self-signed
server (``.omc/probes/b4-a-live/551/``): 4 attempts and 20 s for one prompt,
and the TUI stacked #411's 7-line remedy 5 times (``✖ Connection error.`` per
attempt, then ``✖ Retry failed:``). The Codex adapter's connection retries sent
it 4 times, the Copilot device-code poll 6 times, and an OAuth refresh that
failed verification was retried by the turn like a refused connection (#379).

Now each of those asks the attribution that decides the remedy
(``providers/_error_hints``): a failure it attributes is not retried unless the
session's strict relaxation answers it - the case #184 built the turn's retry
for, where the next request verifies without ``VERIFY_X509_STRICT`` and passes.
The OpenAI and Anthropic SDKs' own connection retries inside one request
(``max_retries`` 2) are not aelix's and still run: 3 handshakes a request.

The rows drive real TLS (a local server, ``ssl.create_default_context``
replaced by one that trusts only a test CA) through the real httpx, httpcore,
OpenAI and Anthropic SDKs and the real anthropic adapter and harness, or raise
the real failure such a handshake produced from an ``httpx.MockTransport``.
Fake keys; no network beyond 127.0.0.1.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import socket
import ssl
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import openai
import pytest

pytest.importorskip("cryptography")

from aelix_agent_core.harness import core as core_mod  # noqa: E402
from aelix_agent_core.harness.core import (  # noqa: E402
    _RETRYABLE_ERROR_PATTERN,
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session  # noqa: E402
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent  # noqa: E402
from aelix_ai import clear_providers, register_provider  # noqa: E402
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage  # noqa: E402
from aelix_ai.oauth import github_copilot  # noqa: E402
from aelix_ai.oauth._helpers import refresh_retry_reason  # noqa: E402
from aelix_ai.providers import _tls_strict  # noqa: E402
from aelix_ai.providers._error_hints import (  # noqa: E402
    describe_provider_error,
    is_tls_verification_error,
    tls_failure_repeats_on_retry,
)
from aelix_ai.providers._tls_strict import Relaxation, reset_strict_relaxation  # noqa: E402
from aelix_ai.streaming import AssistantErrorEvent, Model  # noqa: E402
from aelix_ai.utils.overflow import (  # noqa: E402
    ClassifiedErrorText,
    classifier_text_of,
    repeats_on_retry,
    with_classifier_text,
)

from tests.cli.test_refresh_retry_379 import _anthropic_sse  # noqa: E402
from tests.oauth.test_refresh_retry_reason_379 import _REFRESHES  # noqa: E402
from tests.providers.test_tls_strict import _make_chain  # noqa: E402

# Captured at import, before anything can inject truststore process-wide.
_STDLIB_SSL_CONTEXT = ssl.SSLContext
_STRICT = ssl.VerifyFlags.VERIFY_X509_STRICT
#: The first words of every TLS remedy; #411's untrusted-issuer one starts so.
_REMEDY = "TLS certificate verification failed"
_ANTHROPIC_MODEL = "claude-haiku-4-5"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No strict relaxation crosses a row's edges.

    Requesting ``monkeypatch`` makes this teardown run before ``monkeypatch``'s,
    so undoing a relaxation (which puts back the factory it wrapped) always
    comes before ``monkeypatch`` puts back the real ``ssl.create_default_context``.
    """

    reset_strict_relaxation()
    yield
    reset_strict_relaxation()


# ── certificates, a counting server, and a client that trusts one CA ─────────


@dataclasses.dataclass(frozen=True)
class _Pki:
    trusted_ca: Path
    #: a conformant chain from a CA the client never trusts: verify code 20
    untrusted_chain: Path
    #: a chain from ``sloppy_ca`` whose leaf lacks the Authority Key
    #: Identifier: verify code 85 under strict only
    sloppy_ca: Path
    sloppy_chain: Path


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    trusted_ca, _ = _make_chain(tmp_path_factory.mktemp("trusted551"), conformant=True)
    _, untrusted_chain = _make_chain(tmp_path_factory.mktemp("untrusted551"), conformant=True)
    sloppy_ca, sloppy_chain = _make_chain(tmp_path_factory.mktemp("sloppy551"), conformant=False)
    return _Pki(trusted_ca, untrusted_chain, sloppy_ca, sloppy_chain)


def _trusting(monkeypatch: pytest.MonkeyPatch, ca: Path, *, strict: bool) -> None:
    """Every default context trusts ``ca`` alone, strict on or off (3.13's or 3.12's flag).

    httpx builds its context with ``ssl.create_default_context``, and so does the
    strict relaxation's confirmation handshake: one switch drives both, as in
    ``tests/providers/test_tls_strict.py``.
    """

    real = ssl.create_default_context

    def factory(*_a: object, **_k: object) -> ssl.SSLContext:
        context = real(cafile=str(ca))
        if strict:
            context.verify_flags |= _STRICT
        else:
            context.verify_flags &= ~_STRICT
        return context

    monkeypatch.setattr(ssl, "create_default_context", factory)


def _read_request(conn: Any) -> bool:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return False
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            break
        body += chunk
    return True


class _TlsServer:
    """A TLS server on 127.0.0.1 that counts what reaches it.

    ``accepts`` counts TCP connections (each one a handshake the client tried),
    ``requests`` the HTTP requests that arrived after a handshake succeeded;
    each is answered with ``answer``.
    """

    def __init__(self, chain: Path, answer: bytes = b"") -> None:
        self.accepts = 0
        self.requests = 0
        self._lock = threading.Lock()
        self._answer = answer or (
            b"HTTP/1.1 500 X\r\nContent-Type: application/json\r\n"
            b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
        )
        self._context = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(str(chain))
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = int(self._srv.getsockname()[1])
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with self._lock:
                self.accepts += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            with conn, self._context.wrap_socket(conn, server_side=True) as tls:
                if _read_request(tls):
                    with self._lock:
                        self.requests += 1
                    tls.sendall(self._answer)
        except OSError:
            pass  # the client refused the handshake: what most rows test

    def close(self) -> None:
        self._srv.close()


@pytest.fixture
def untrusted(pki: _Pki) -> Iterator[_TlsServer]:
    server = _TlsServer(pki.untrusted_chain)
    yield server
    server.close()


def _anthropic_answer() -> bytes:
    body = _anthropic_sse(_ANTHROPIC_MODEL)
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


def _connect_failure(port: int) -> BaseException:
    """What a real httpx request raises against ``port``: httpx → httpcore → the ssl error."""

    with httpx.Client(timeout=10) as client, pytest.raises(httpx.ConnectError) as caught:
        client.get(f"https://localhost:{port}/")
    return caught.value


def _sdk_failure(port: int) -> BaseException:
    """What the real OpenAI SDK (its own retries off) raises against ``port``."""

    client = openai.OpenAI(
        api_key="sk-fake-551", base_url=f"https://localhost:{port}/v1", max_retries=0
    )
    with pytest.raises(openai.APIConnectionError) as caught:
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    return caught.value


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _error(text: str) -> AssistantMessage:
    return AssistantMessage(content=[], stop_reason="error", error_message=text)


def _harness(**kw: Any) -> AgentHarness:
    harness = AgentHarness(AgentHarnessOptions(session=Session(MemorySessionStorage()), **kw))
    harness._state.auto_compaction_enabled = False
    return harness


# ── the classification ───────────────────────────────────────────────────────


def test_a_verification_failure_is_marked_and_the_turn_does_not_retry_it(
    pki: _Pki, untrusted: _TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    exc = _sdk_failure(untrusted.port)
    described = describe_provider_error(exc)

    assert str(exc) == "Connection error."
    assert is_tls_verification_error(exc) and tls_failure_repeats_on_retry(exc)
    assert repeats_on_retry(described) is True
    assert described.count(_REMEDY) == 1
    # Positive control: the text alone is what the pattern retried on 27fa21c5.
    assert _RETRYABLE_ERROR_PATTERN.search(classifier_text_of(described)) is not None
    assert _harness()._is_retryable_error(_error(described)) is False


def test_a_refused_connection_is_still_retried() -> None:
    exc = _sdk_failure(_free_port())
    described = describe_provider_error(exc)

    assert not is_tls_verification_error(exc) and not tls_failure_repeats_on_retry(exc)
    assert repeats_on_retry(described) is False
    assert _harness()._is_retryable_error(_error(described)) is True


def test_a_502_that_quotes_certificate_words_is_still_retried() -> None:
    """A server's words are not this machine's verifier (#411 review round 2)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"error": {"message": "upstream: certificate verify failed: self-signed"}}
        )

    client = openai.OpenAI(
        api_key="sk-fake-551",
        base_url="https://local.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(openai.APIStatusError) as caught:
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    described = describe_provider_error(caught.value)

    assert described.startswith("Error code: 502")
    assert "certificate verify failed" in described
    assert repeats_on_retry(described) is False
    assert _harness()._is_retryable_error(_error(described)) is True


def test_a_message_rebuilt_from_its_text_is_classified_on_its_text(
    pki: _Pki, untrusted: _TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The known limit (ADR-0128 #551 amendment): the mark travels with the message
    object, as #186's ``classifier_text`` does; an extension that rebuilds the
    message hands the harness a plain ``str``, retried on its words as before."""

    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    described = describe_provider_error(_sdk_failure(untrusted.port))
    rebuilt = str(described)

    assert type(rebuilt) is str and repeats_on_retry(rebuilt) is False
    assert _harness()._is_retryable_error(_error(rebuilt)) is True


def test_the_mark_survives_what_the_loop_does_and_never_reaches_json(
    pki: _Pki, untrusted: _TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    described = describe_provider_error(_connect_failure(untrusted.port))
    message = _error(described)

    assert repeats_on_retry(dataclasses.replace(message, timestamp=1.0).error_message or "")
    assert repeats_on_retry(copy.deepcopy(described))
    written = json.loads(json.dumps(dataclasses.asdict(message)))["error_message"]
    assert written == described and type(written) is str


def test_a_mark_is_carried_even_when_nothing_else_is() -> None:
    assert type(with_classifier_text("x", "x")) is str
    marked = with_classifier_text("x", "x", repeats_on_retry=True)
    assert isinstance(marked, ClassifiedErrorText) and marked == "x"
    assert classifier_text_of(marked) == "x" and repeats_on_retry(marked) is True


def _coded(code: int | None) -> BaseException:
    """A ConnectError carrying an OpenSSL-shaped verification error with ``code``."""

    inner = ssl.SSLCertVerificationError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: probe (_ssl.c:1000)"
    )
    if code is not None:
        inner.verify_code = code  # type: ignore[attr-defined]
        inner.verify_message = "probe"  # type: ignore[attr-defined]
    outer = httpx.ConnectError(str(inner))
    outer.__cause__ = inner
    return outer


@pytest.mark.parametrize(
    ("relaxed_on", "code", "repeats"),
    [
        pytest.param(None, 85, True, id="no-relaxation-strict-code"),
        pytest.param(None, 20, True, id="no-relaxation-untrusted"),
        pytest.param(85, 85, False, id="the-code-it-was-measured-on"),
        pytest.param(85, 89, False, id="another-strict-only-code"),
        pytest.param(85, 20, True, id="untrusted-under-a-relaxation"),
        pytest.param(85, 18, True, id="self-signed-under-a-relaxation"),
        pytest.param(79, 79, False, id="79-it-was-measured-on"),
        pytest.param(85, 79, True, id="79-is-not-strict-only"),
        pytest.param(85, None, True, id="no-code"),
    ],
)
def test_only_a_failure_the_strict_relaxation_answers_is_retried(
    relaxed_on: int | None, code: int | None, repeats: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if relaxed_on is not None:
        relaxation = Relaxation(host="localhost", verify_code=relaxed_on, verify_message="x")
        monkeypatch.setattr(_tls_strict, "session_relaxation", lambda: relaxation)
    exc = _coded(code)

    assert is_tls_verification_error(exc)
    assert tls_failure_repeats_on_retry(exc) is repeats
    assert repeats_on_retry(describe_provider_error(exc)) is repeats


# ── one prompt, end to end: the real anthropic adapter, SDK and harness ──────


def _anthropic_harness(port: int, events: list[Any]) -> AgentHarness:
    from aelix_ai.providers.anthropic import stream_anthropic

    async def stream(model: Any, context: Any, options: Any) -> AsyncIterator[Any]:
        # The SDK's own connection retries off: this counts the turn's attempts.
        async for event in stream_anthropic(model, context, dataclasses.replace(options, max_retries=0)):
            yield event

    harness = _harness(
        model=Model(
            api="anthropic-messages",
            id=_ANTHROPIC_MODEL,
            provider="anthropic",
            base_url=f"https://localhost:{port}",
        ),
        stream_fn=stream,
        get_api_key=lambda _provider: "sk-ant-fake-551",
    )
    harness.subscribe(lambda event: events.append(event) or None)
    return harness


@pytest.mark.parametrize("strict", [False, True], ids=["strict-off", "strict-on"])
async def test_one_prompt_to_an_untrusted_server_is_sent_once_and_shows_the_remedy_once(
    strict: bool, pki: _Pki, untrusted: _TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's row. On 27fa21c5: 4 attempts, 3 ``auto_retry_start`` events each
    carrying the remedy, and the TUI printed it once per event and once more. With
    strict on, the relaxation's check handshakes twice per attempt and relaxes
    nothing (the chain is untrusted with or without strict)."""

    _trusting(monkeypatch, pki.trusted_ca, strict=strict)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    events: list[Any] = []
    harness = _anthropic_harness(untrusted.port, events)

    await harness.prompt("hi")

    assert untrusted.accepts == (3 if strict else 1)
    assert untrusted.requests == 0
    assert [e for e in events if isinstance(e, AutoRetryStartEvent | AutoRetryEndEvent)] == []
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "error"
    assert (last.error_message or "").startswith("Connection error.")
    assert (last.error_message or "").count(_REMEDY) == 1
    assert _tls_strict.session_relaxation() is None
    await harness.dispose()


async def test_a_failure_the_strict_relaxation_answers_is_retried_and_answers(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#184's flow stays: a strict-only rejection (code 85) of a chain this machine
    trusts relaxes strict for the session, and the turn's retry - a new client -
    is the request that passes."""

    server = _TlsServer(pki.sloppy_chain, _anthropic_answer())
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    events: list[Any] = []
    harness = _anthropic_harness(server.port, events)
    try:
        await harness.prompt("hi")
    finally:
        server.close()

    relaxation = _tls_strict.session_relaxation()
    assert relaxation is not None and relaxation.verify_code == 85
    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    assert [s.attempt for s in starts] == [1]
    assert "RFC-5280 strict verification relaxed" in str(starts[0].error_message)
    ends = [(e.success, e.attempt) for e in events if isinstance(e, AutoRetryEndEvent)]
    assert ends == [(True, 1)]
    assert server.requests == 1
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "end_turn"
    await harness.dispose()


# ── #409: a compaction's summary request decides the same way ────────────────

_SUMMARY_API = "tls-551-summary"


@pytest.fixture
def summary_provider() -> Iterator[Callable[[list[str]], list[int]]]:
    """Register a provider for :data:`_SUMMARY_API` answering from a plan; the
    returned list counts its calls."""

    clear_providers()

    def install(plan: list[str]) -> list[int]:
        calls = [0]

        async def stream(_model: Any, _context: Any, _options: Any) -> AsyncIterator[Any]:
            from aelix_ai.streaming import AssistantDoneEvent, TextDeltaEvent

            calls[0] += 1
            step = plan.pop(0) if plan else "ok"
            if step != "ok":
                message = _error(step)
                yield AssistantErrorEvent(reason="error", error=message, error_message=step)
                return
            yield TextDeltaEvent(delta="## Goal\nthe summary")
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    content=[TextContent(text="## Goal\nthe summary")], stop_reason="end_turn"
                ),
            )

        register_provider(_SUMMARY_API, stream)
        return calls

    yield install
    clear_providers()


async def _compactable() -> tuple[AgentHarness, list[Any]]:
    from aelix_ai.settings import SettingsManager

    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api=_SUMMARY_API, id="m", provider="p", context_window=200_000),
            session=session,
            get_api_key_and_headers=lambda _m: {"apiKey": "fake-key-551", "headers": {}},
            settings_manager=SettingsManager.in_memory({"retry": {"baseDelayMs": 1}}),
        )
    )
    events: list[Any] = []
    harness.subscribe(lambda event: events.append(event) or None)
    return harness, events


async def test_a_compaction_summary_that_fails_verification_is_not_retried(
    pki: _Pki,
    untrusted: _TlsServer,
    monkeypatch: pytest.MonkeyPatch,
    summary_provider: Callable[[list[str]], list[int]],
) -> None:
    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    described = describe_provider_error(_sdk_failure(untrusted.port))
    calls = summary_provider([described])
    harness, events = await _compactable()

    with pytest.raises(AgentHarnessError) as caught:
        await harness.compact()

    assert caught.value.code == "compaction"
    assert calls == [1]
    assert [e for e in events if isinstance(e, AutoRetryStartEvent)] == []


async def test_the_same_words_without_the_mark_are_still_retried(
    pki: _Pki,
    untrusted: _TlsServer,
    monkeypatch: pytest.MonkeyPatch,
    summary_provider: Callable[[list[str]], list[int]],
) -> None:
    """Positive control for the row above: the summary retry reads the mark, not the words."""

    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    rebuilt = str(describe_provider_error(_sdk_failure(untrusted.port)))
    calls = summary_provider([rebuilt])
    harness, events = await _compactable()

    result = await harness.compact()

    assert result.summary.startswith("## Goal")
    assert calls == [2]
    assert len([e for e in events if isinstance(e, AutoRetryStartEvent)]) == 1


# ── an OAuth refresh (#379), the Copilot device-code poll, the Codex adapter ──


def _serve(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch.object(httpx, "AsyncClient", side_effect=factory)


def _raising(failures: list[BaseException], hits: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        hits.append("x")
        failure = failures.pop(0) if len(failures) > 1 else failures[0]
        if isinstance(failure, BaseException):
            raise failure
        return httpx.Response(200, json={"access_token": "gho_fake"})

    return handler


@pytest.mark.parametrize("site", list(_REFRESHES))
async def test_an_oauth_refresh_that_fails_verification_is_not_transient(
    site: str, pki: _Pki, untrusted: _TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#379 retries a refresh whose token endpoint could not be reached; a refresh that
    failed verification is not one of those (control: a refused connection still is)."""

    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    failure = _connect_failure(untrusted.port)
    hits: list[str] = []
    with _serve(_raising([failure], hits)), pytest.raises(Exception) as caught:
        await _REFRESHES[site]()

    assert is_tls_verification_error(caught.value)
    assert refresh_retry_reason(caught.value) is None

    refused = httpx.ConnectError("All connection attempts failed")
    with _serve(_raising([refused], hits)), pytest.raises(Exception) as control:
        await _REFRESHES[site]()
    assert refresh_retry_reason(control.value) == (
        "the token endpoint could not be reached (ConnectError)"
    )


def test_an_oauth_refresh_the_strict_relaxation_answers_stays_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh that failed on strict before the session relaxed it passes on the
    turn's retry, which builds a new client: still #379's transient failure."""

    relaxation = Relaxation(host="localhost", verify_code=85, verify_message="x")
    monkeypatch.setattr(_tls_strict, "session_relaxation", lambda: relaxation)
    refresh = RuntimeError("OAuth refresh failed")
    refresh.__cause__ = _coded(85)

    assert refresh_retry_reason(refresh) == "the token endpoint could not be reached (ConnectError)"
    refresh.__cause__ = _coded(20)
    assert refresh_retry_reason(refresh) is None


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    # The module's own name only: the event loop keeps the real asyncio.sleep.
    monkeypatch.setattr(github_copilot, "asyncio", SimpleNamespace(sleep=no_sleep))


async def test_the_copilot_poll_stops_at_the_first_verification_failure(
    pki: _Pki, untrusted: _TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    _no_sleep(monkeypatch)
    failure = _connect_failure(untrusted.port)
    hits: list[str] = []

    with _serve(_raising([failure], hits)), pytest.raises(RuntimeError) as caught:
        await github_copilot._poll_for_github_access_token("github.com", "dc-fake", 1, 900)

    assert hits == ["x"]
    assert str(caught.value).startswith("Could not reach https://github.com/login/oauth/access_token")
    assert caught.value.__cause__ is failure
    assert describe_provider_error(caught.value).count(_REMEDY) == 1


async def test_the_copilot_poll_still_polls_through_a_refused_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_sleep(monkeypatch)
    hits: list[str] = []
    refused = httpx.ConnectError("All connection attempts failed")

    with _serve(_raising([refused], hits)), pytest.raises(RuntimeError):
        await github_copilot._poll_for_github_access_token("github.com", "dc-fake", 1, 900)

    assert len(hits) == github_copilot._MAX_CONSECUTIVE_TRANSIENT + 1


async def test_the_copilot_poll_polls_again_when_the_strict_relaxation_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll's next request is a new client: after a relaxation measured on this
    failure's code, it is the retry (the poll's own comment, #184)."""

    _no_sleep(monkeypatch)
    relaxation = Relaxation(host="github.com", verify_code=85, verify_message="x")
    monkeypatch.setattr(_tls_strict, "session_relaxation", lambda: relaxation)
    monkeypatch.setattr(_tls_strict, "maybe_relax_strict_for_session", lambda _exc: relaxation)
    hits: list[str] = []

    with _serve(_raising([_coded(85), None], hits)):  # type: ignore[list-item]
        token = await github_copilot._poll_for_github_access_token("github.com", "dc-fake", 1, 900)

    assert token == "gho_fake"
    assert hits == ["x", "x"]


async def test_the_codex_adapter_does_not_resend_a_verification_failure(
    pki: _Pki, untrusted: _TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx
    from aelix_ai.providers.openai_codex_responses import stream_openai_codex_responses
    from aelix_ai.providers.openai_responses import OpenAIResponsesOptions
    from aelix_ai.streaming import Context

    from tests.providers.test_openai_codex_responses_adapter import (
        _collect,
        _FakeClient,
        _make_token,
        _model,
    )

    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    waits: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(cx.asyncio, "sleep", no_sleep)
    failure = _connect_failure(untrusted.port)
    client = _FakeClient([failure] * (cx._MAX_RETRIES + 1))
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)

    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))

    assert client.calls == 1 and waits == []
    assert isinstance(out[-1], AssistantErrorEvent)
    shown = out[-1].error_message or ""
    assert shown.count(_REMEDY) == 1
    assert repeats_on_retry(shown)


async def test_the_codex_adapter_still_resends_a_refused_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx
    from aelix_ai.providers.openai_codex_responses import stream_openai_codex_responses
    from aelix_ai.providers.openai_responses import OpenAIResponsesOptions
    from aelix_ai.streaming import Context

    from tests.providers.test_openai_codex_responses_adapter import (
        _collect,
        _FakeClient,
        _make_token,
        _model,
    )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", no_sleep)
    refused = [httpx.ConnectError("All connection attempts failed") for _ in range(cx._MAX_RETRIES + 1)]
    client = _FakeClient(refused)  # type: ignore[arg-type]
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)

    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))

    assert client.calls == cx._MAX_RETRIES + 1
    assert isinstance(out[-1], AssistantErrorEvent)
