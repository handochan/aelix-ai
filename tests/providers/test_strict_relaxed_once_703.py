"""#703 - a failure the strict relaxation answered is sent again and shown as one line, never an error.

On Python 3.13 a corporate proxy's certificate can fail RFC 5280 strict checking
alone (Missing Authority Key Identifier, code 85). aelix measures that the
machine trusts the chain without strict and relaxes strict for the session. On
679e5e5c the user then saw the failure as an error with a four-line note
(``Relaxation.remedy``) - the TUI in red, the Codex adapter's ``-p`` on stderr
with exit 1 - and, the relaxation not being persisted, saw it again on every
launch. Now the adapter that failed sends the request again itself, once, on a
new client, and the relaxation is announced once, on one line
(``Relaxation.notice``), to the listeners the CLI registers. A failure strict did
not cause keeps today's error and advice, sent once (#551).

Real TLS against a local server (``ssl.create_default_context`` replaced by one
that trusts only a test CA, strict forced on), through the real httpx, OpenAI and
Anthropic SDKs and the four adapters that relax. Fake keys; 127.0.0.1 only.
"""

from __future__ import annotations

import dataclasses
import io
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

pytest.importorskip("cryptography")

from aelix_agent_core.harness import core as core_mod  # noqa: E402
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent  # noqa: E402
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage  # noqa: E402
from aelix_ai.providers import _tls_strict  # noqa: E402
from aelix_ai.providers._error_hints import _untrusted_issuer_hint  # noqa: E402
from aelix_ai.providers._strict_resend import resend_when_strict_relaxed  # noqa: E402
from aelix_ai.providers._tls_strict import (  # noqa: E402
    Relaxation,
    add_relaxation_listener,
    maybe_relax_strict_for_session,
    reset_strict_relaxation,
)
from aelix_ai.streaming import (  # noqa: E402
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.utils.overflow import retry_verdict, with_classifier_text  # noqa: E402
from aelix_coding_agent.cli.tls_notice import announce_on_stderr, notice_line  # noqa: E402

from tests.providers.test_tls_failure_not_retried_551 import (  # noqa: E402
    _anthropic_answer,
    _anthropic_harness,
    _connect_failure,
    _Pki,
    _TlsServer,
    _trusting,
)
from tests.providers.test_tls_retry_verdict_572 import _codex_answer, _codex_harness  # noqa: E402
from tests.providers.test_tls_strict import _make_chain  # noqa: E402

_HELLO = "hello from the lab"


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    trusted_ca, _ = _make_chain(tmp_path_factory.mktemp("trusted703"), conformant=True)
    _, untrusted_chain = _make_chain(tmp_path_factory.mktemp("untrusted703"), conformant=True)
    sloppy_ca, sloppy_chain = _make_chain(tmp_path_factory.mktemp("sloppy703"), conformant=False)
    return _Pki(trusted_ca, untrusted_chain, sloppy_ca, sloppy_chain)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_strict_relaxation()
    yield
    reset_strict_relaxation()


@pytest.fixture
def notices() -> Iterator[list[Relaxation]]:
    seen: list[Relaxation] = []
    remove = add_relaxation_listener(seen.append)
    yield seen
    remove()


# ── the line, and who hears it ───────────────────────────────────────────────


_NOTICE = "TLS strict relaxed: localhost (Missing Authority Key Identifier) · aelix docs tls"


async def test_the_notice_is_one_line_naming_the_host_the_rule_and_the_guide(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one line, as ``-p`` writes it to stderr for a request the relaxation answered
    and the adapter sent again: that line and the answer, nothing else. It points at the
    TLS guide, not at ``aelix status``, which is a process of its own and shows strict
    ``ON`` (fix round 1)."""

    relaxation = Relaxation(
        host="api.example.com", verify_code=85, verify_message="Missing Authority Key Identifier"
    )
    assert relaxation.notice() == _NOTICE.replace("localhost", "api.example.com")
    assert "\n" not in relaxation.notice() and "aelix status" not in relaxation.notice()
    no_message = Relaxation(host="h", verify_code=89, verify_message=None)
    assert no_message.notice() == "TLS strict relaxed: h (verify code 89) · aelix docs tls"
    steering = Relaxation(host="h\x1b[2J", verify_code=85, verify_message="m\x07")
    assert "\x1b" not in notice_line(steering) and "\x07" not in notice_line(steering)

    stream, model, options, answer = _adapter("openai-completions")
    server = _TlsServer(pki.sloppy_chain, answer)
    model = dataclasses.replace(model, base_url=(model.base_url or "").format(port=server.port))
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    stderr = io.StringIO()
    remove = announce_on_stderr(stderr)
    try:
        events = [e async for e in stream(model, Context(), options)]
    finally:
        remove()
        server.close()

    assert stderr.getvalue() == _NOTICE + "\n"
    assert [e for e in events if isinstance(e, AssistantErrorEvent)] == []
    assert "".join(_texts(events)) == _HELLO


#: The hosts a corporate proxy's certificate is usually met on, and the strict rules
#: such a certificate usually breaks (codes 85, 89 and 92).
_TYPICAL_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "chatgpt.com",
    "api.business.githubcopilot.com",
)
_TYPICAL_RULES = (
    (85, "Missing Authority Key Identifier"),
    (89, "Basic Constraints of CA cert not marked critical"),
    (92, "CA cert does not include key usage extension"),
)


def test_the_notice_fits_a_120_column_terminal_for_the_usual_hosts_and_rules() -> None:
    """Fix round 2: the line was 125 characters for ``localhost`` and up to 162 for these
    hosts, and the TUI wrapped it at 120 columns (``... — aelix docs`` /
    ``tls-and-corporate-ca``). Now 40 fixed characters, the host and the rule: 118 at the
    longest. ``aelix docs tls`` is an alias, so the pointer still opens the guide."""

    from aelix_coding_agent.help import resolve_topic

    lengths = {
        (host, code): len(notice_line(Relaxation(host=host, verify_code=code, verify_message=rule)))
        for host in _TYPICAL_HOSTS
        for code, rule in _TYPICAL_RULES
    }
    assert lengths == {
        ("api.openai.com", 85): 86,
        ("api.openai.com", 89): 102,
        ("api.openai.com", 92): 98,
        ("api.anthropic.com", 85): 89,
        ("api.anthropic.com", 89): 105,
        ("api.anthropic.com", 92): 101,
        ("chatgpt.com", 85): 83,
        ("chatgpt.com", 89): 99,
        ("chatgpt.com", 92): 95,
        ("api.business.githubcopilot.com", 85): 102,
        ("api.business.githubcopilot.com", 89): 118,
        ("api.business.githubcopilot.com", 92): 114,
    }
    assert max(lengths.values()) <= 120
    assert len(_NOTICE) == 81
    pointer = _NOTICE.rsplit("aelix docs ", 1)[1]
    topic = resolve_topic(pointer)
    assert topic is not None and topic.name == "tls-and-corporate-ca"


def test_a_relaxation_is_announced_once_and_a_listener_cannot_break_it(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    server = _TlsServer(pki.sloppy_chain)
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)

    def broken(_relaxation: Relaxation) -> None:
        raise RuntimeError("a listener that fails")

    remove_broken = add_relaxation_listener(broken)
    stderr = io.StringIO()
    remove_stderr = announce_on_stderr(stderr)
    try:
        failure = _connect_failure(server.port)
        first = maybe_relax_strict_for_session(failure)
        # A sibling request that failed the same way before the relaxation took effect.
        again = maybe_relax_strict_for_session(failure)
    finally:
        remove_broken()
        remove_stderr()
        server.close()

    assert first is not None and again is first
    assert notices == [first]
    assert stderr.getvalue() == f"{first.notice()}\n"
    assert "localhost (Missing Authority Key Identifier)" in stderr.getvalue()


def test_nothing_is_announced_when_nothing_is_relaxed(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    server = _TlsServer(pki.untrusted_chain)
    _trusting(monkeypatch, pki.trusted_ca, strict=True)
    try:
        assert maybe_relax_strict_for_session(_connect_failure(server.port)) is None
    finally:
        server.close()
    assert notices == []


# ── each adapter that relaxes: sent again, answered, no error ────────────────


def _completions_answer() -> bytes:
    base = {"id": "chatcmpl-703", "object": "chat.completion.chunk", "created": 1, "model": "m"}
    chunks = [
        {
            **base,
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
            ],
        },
        {**base, "choices": [{"index": 0, "delta": {"content": _HELLO}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    body = (
        b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
    )
    return _http_sse(body)


def _responses_answer() -> bytes:
    events = [
        {"type": "response.created", "response": {"id": "resp_703"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "m1"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": _HELLO},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "m1",
                "content": [{"type": "output_text", "text": _HELLO}],
            },
        },
        {"type": "response.completed", "response": {"id": "resp_703", "status": "completed"}},
    ]
    body = b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)
    return _http_sse(body)


def _http_sse(body: bytes) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


def _adapter(api: str) -> tuple[Any, Model, Any, bytes]:
    """The adapter's stream function, a model pointed at the lab, options, the answer."""

    if api == "openai-completions":
        from aelix_ai.providers.openai_completions import stream_openai_completions

        model = Model(api=api, id="m", provider="lab", base_url="https://localhost:{port}/v1")
        return (
            stream_openai_completions,
            model,
            SimpleStreamOptions(api_key="sk-fake-703", max_retries=0),
            (_completions_answer()),
        )
    if api == "openai-responses":
        from aelix_ai.providers.openai_responses import stream_openai_responses

        model = Model(api=api, id="m", provider="lab", base_url="https://localhost:{port}/v1")
        return (
            stream_openai_responses,
            model,
            SimpleStreamOptions(api_key="sk-fake-703", max_retries=0),
            (_responses_answer()),
        )
    if api == "anthropic-messages":
        from aelix_ai.providers.anthropic import stream_anthropic

        model = Model(
            api=api,
            id="claude-haiku-4-5",
            provider="anthropic",
            base_url="https://localhost:{port}",
        )
        return (
            stream_anthropic,
            model,
            SimpleStreamOptions(api_key="sk-ant-fake-703", max_retries=0),
            (_anthropic_answer()),
        )
    from aelix_ai.providers.openai_codex_responses import stream_openai_codex_responses

    from tests.providers.test_openai_codex_responses_adapter import _make_token, _model

    model = _model(base_url="https://localhost:{port}/backend-api")
    return (
        stream_openai_codex_responses,
        model,
        SimpleStreamOptions(api_key=_make_token()),
        _codex_answer(),
    )


_APIS = ["openai-completions", "openai-responses", "anthropic-messages", "openai-codex-responses"]


def _texts(events: list[Any]) -> list[str]:
    done = [e for e in events if isinstance(e, AssistantDoneEvent)]
    assert len(done) == 1
    return [b.text for b in done[0].message.content if isinstance(b, TextContent)]


@pytest.mark.parametrize("api", _APIS)
async def test_a_request_that_failed_on_strict_alone_is_sent_again_and_answered(
    api: str, pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    """The issue's row, per adapter: no error event, one notice, the answer. On 679e5e5c
    every adapter ended this stream with the error and the four-line note."""

    stream, model, options, answer = _adapter(api)
    server = _TlsServer(pki.sloppy_chain, answer)
    model = dataclasses.replace(model, base_url=(model.base_url or "").format(port=server.port))
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    context = Context(messages=[UserMessage(content=[TextContent(text="hi")])])
    try:
        events = [e async for e in stream(model, context, options)]
    finally:
        server.close()

    assert [e for e in events if isinstance(e, AssistantErrorEvent)] == []
    assert [type(e) for e in events][0] is AssistantStartEvent
    expected = _HELLO if api in ("openai-completions", "openai-responses") else "hello"
    assert "".join(_texts(events)) == expected
    relaxation = _tls_strict.session_relaxation()
    assert relaxation is not None and relaxation.verify_code == 85
    assert notices == [relaxation]
    # The failure, the relaxation's two checks, the request sent again.
    assert server.accepts == 4 and server.requests == 1


async def test_one_prompt_through_the_harness_has_no_error_no_retry_and_one_notice(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    """What the TUI, ``-p``, ``--mode json`` and rpc are given: the answer, and no
    ``message_end`` error, ``auto_retry_*`` event or failed answer in the session."""

    server = _TlsServer(pki.sloppy_chain, _anthropic_answer())
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    events: list[Any] = []
    harness = _anthropic_harness(server.port, events)
    try:
        await harness.prompt("hi")
    finally:
        server.close()

    ends = [e for e in events if getattr(e, "type", None) == "message_end"]
    assert [
        getattr(e.message, "stop_reason", None)
        for e in ends
        if isinstance(e.message, AssistantMessage)
    ] == ["end_turn"]
    assert [e for e in events if isinstance(e, AutoRetryStartEvent | AutoRetryEndEvent)] == []
    entries = await harness.session.get_branch() if harness.session is not None else []
    stops = [getattr(getattr(e, "message", None), "stop_reason", None) for e in entries]
    assert "error" not in stops
    assert len(notices) == 1
    await harness.dispose()


async def test_the_codex_prompt_through_the_harness_is_answered_with_one_notice(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    server = _TlsServer(pki.sloppy_chain, _codex_answer())
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    events: list[Any] = []
    harness = _codex_harness(server.port, events)
    try:
        await harness.prompt("hi")
    finally:
        server.close()

    answers = [m for m in harness._state.messages if isinstance(m, AssistantMessage)]
    assert [m.stop_reason for m in answers] == ["stop"]
    assert len(notices) == 1 and server.requests == 1
    await harness.dispose()


async def test_a_compaction_summary_request_is_sent_again_and_answered(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    """A compaction's summary request goes through the same adapter: the summary is
    written, with no ``auto_retry_*`` row and one notice."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.session import MemorySessionStorage, Session
    from aelix_ai import clear_providers, register_provider
    from aelix_ai.providers.anthropic import stream_anthropic
    from aelix_ai.settings import SettingsManager

    server = _TlsServer(pki.sloppy_chain, _anthropic_answer())
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    api = "tls-703-summary"

    async def provider(model: Any, context: Any, options: Any) -> AsyncIterator[Any]:
        real = dataclasses.replace(
            model, api="anthropic-messages", base_url=f"https://localhost:{server.port}"
        )
        async for event in stream_anthropic(
            real, context, dataclasses.replace(options, max_retries=0)
        ):
            yield event

    clear_providers()
    register_provider(api, provider)
    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(
                api=api, id="claude-haiku-4-5", provider="anthropic", context_window=200_000
            ),
            session=session,
            get_api_key_and_headers=lambda _m: {"apiKey": "sk-ant-fake-703", "headers": {}},
            settings_manager=SettingsManager.in_memory({"retry": {"baseDelayMs": 1}}),
        )
    )
    events: list[Any] = []
    harness.subscribe(lambda event: events.append(event) or None)
    try:
        result = await harness.compact()
    finally:
        server.close()
        clear_providers()

    assert "hello" in result.summary
    assert [e for e in events if isinstance(e, AutoRetryStartEvent | AutoRetryEndEvent)] == []
    assert len(notices) == 1 and server.requests == 1
    await harness.dispose()


# ── a failure strict did not cause: today's error, advice and single attempt ──


@pytest.mark.parametrize("api", _APIS)
async def test_an_untrusted_root_keeps_todays_error_and_advice_and_is_sent_once(
    api: str, pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    stream, model, options, answer = _adapter(api)
    server = _TlsServer(pki.untrusted_chain, answer)
    model = dataclasses.replace(model, base_url=(model.base_url or "").format(port=server.port))
    _trusting(monkeypatch, pki.trusted_ca, strict=True)
    try:
        events = [e async for e in stream(model, Context(), options)]
    finally:
        server.close()

    errors = [e for e in events if isinstance(e, AssistantErrorEvent)]
    assert len(errors) == 1 and events[-1] is errors[0]
    text = errors[0].error_message or ""
    assert text.endswith("\n\n" + _untrusted_issuer_hint())
    assert retry_verdict(text) == "repeats"
    assert notices == [] and _tls_strict.session_relaxation() is None
    assert server.accepts == 3 and server.requests == 0  # the request, the check's two


# ── the wrapper itself ───────────────────────────────────────────────────────


def _failure(verdict: str | None, *, reason: str = "error") -> AssistantErrorEvent:
    text = with_classifier_text(
        "boom", "boom", repeats_on_retry=verdict == "repeats", resend_on_retry=verdict == "resend"
    )
    message = AssistantMessage(content=[], stop_reason=reason, error_message=text)  # type: ignore[arg-type]
    return AssistantErrorEvent(reason=reason, error=message, error_message=text)  # type: ignore[arg-type]


def _ok() -> list[Any]:
    return [
        AssistantStartEvent(partial=AssistantMessage(content=[])),
        AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop"),
        ),
    ]


def _scripted(plans: list[list[Any]], closed: list[int]) -> Any:
    calls = [0]

    @resend_when_strict_relaxed
    async def stream(_model: Any, _context: Any, _options: Any = None) -> AsyncIterator[Any]:
        plan = plans[calls[0]]
        calls[0] += 1
        try:
            for event in plan:
                yield event
        finally:
            closed.append(calls[0])

    stream.calls = calls  # type: ignore[attr-defined]
    return stream


async def _run(stream: Any, options: Any = None) -> list[Any]:
    return [e async for e in stream(Model(api="x", id="m", provider="p"), Context(), options)]


async def test_sent_again_once_and_only_the_answer_comes_out() -> None:
    closed: list[int] = []
    stream = _scripted([[_failure("resend")], _ok()], closed)
    out = await _run(stream)
    assert [e.type for e in out] == ["start", "done"]
    assert stream.calls == [2] and closed == [1, 2]


async def test_a_second_failure_goes_out_as_repeats_and_is_not_sent_a_third_time() -> None:
    closed: list[int] = []
    stream = _scripted([[_failure("resend")], [_failure("resend")], _ok()], closed)
    out = await _run(stream)
    assert len(out) == 1 and isinstance(out[0], AssistantErrorEvent)
    assert retry_verdict(out[0].error_message or "") == "repeats"
    assert retry_verdict(out[0].error.error_message or "") == "repeats"
    assert str(out[0].error_message) == "boom"
    assert stream.calls == [2]


_RESEND = [_failure("resend")]


@pytest.mark.parametrize(
    ("plan", "options", "twin", "twin_options"),
    [
        pytest.param([_failure("repeats")], None, _RESEND, None, id="a-repeats-verdict"),
        pytest.param([_failure(None)], None, _RESEND, None, id="no-verdict"),
        pytest.param([_failure("resend", reason="aborted")], None, _RESEND, None, id="an-abort"),
        pytest.param([*_ok()[:1], _failure("resend")], None, _RESEND, None, id="after-a-start"),
        pytest.param(
            _RESEND,
            SimpleStreamOptions(client=object()),
            _RESEND,
            SimpleStreamOptions(),
            id="a-caller-owned-client",
        ),
    ],
)
async def test_nothing_else_is_sent_again(
    plan: list[Any], options: Any, twin: list[Any], twin_options: Any
) -> None:
    """Each row beside its twin, which differs in the one thing the row is about and IS
    sent again (fix round 1: the rows alone passed with the wrapper sending nothing)."""

    closed: list[int] = []
    stream = _scripted([plan, _ok()], closed)
    out = await _run(stream, options)
    assert out == plan
    assert stream.calls == [1]

    twin_stream = _scripted([twin, _ok()], [])
    assert [e.type for e in await _run(twin_stream, twin_options)] == ["start", "done"]
    assert twin_stream.calls == [2]


async def test_an_aborted_signal_is_not_sent_again() -> None:
    class _Signal:
        def __init__(self, aborted: bool) -> None:
            self.aborted = aborted

    closed: list[int] = []
    stream = _scripted([[_failure("resend")], _ok()], closed)
    out = await _run(stream, SimpleStreamOptions(signal=_Signal(True)))
    assert len(out) == 1 and stream.calls == [1]

    twin = _scripted([[_failure("resend")], _ok()], [])
    assert [e.type for e in await _run(twin, SimpleStreamOptions(signal=_Signal(False)))] == [
        "start",
        "done",
    ]
    assert twin.calls == [2]


async def test_an_abort_while_the_first_attempt_closes_is_not_sent_again() -> None:
    """Codex 703-2: the abort is read again after the first attempt's cleanup, which
    awaits (a client's ``aclose``), and before the request goes out a second time."""

    class _Signal:
        aborted = False

    signal = _Signal()
    calls: list[int] = []
    failure = _failure("resend")

    @resend_when_strict_relaxed
    async def stream(_model: Any, _context: Any, _options: Any = None) -> AsyncIterator[Any]:
        calls.append(1)
        try:
            yield failure if len(calls) == 1 else _ok()[0]
        finally:
            signal.aborted = True  # Esc while the first client was closing

    out = await _run(stream, SimpleStreamOptions(signal=signal))
    assert out == [failure]
    assert calls == [1]


async def test_a_consumer_that_stops_early_closes_the_adapter() -> None:
    """Also after a request was sent again: the consumer's close reaches the second
    attempt, and the first was closed before it went out."""

    closed: list[int] = []
    stream = _scripted([[_failure("resend")], _ok()], closed)
    events = stream(Model(api="x", id="m", provider="p"), Context(), None)
    first = await events.__anext__()
    assert first.type == "start"
    assert closed == [1]
    await events.aclose()
    assert closed == [1, 2]


# ── fix round 1: a failure after the response is never sent again ─────────────


@pytest.mark.parametrize("api", _APIS)
async def test_an_on_response_hook_that_fails_on_strict_does_not_resend_the_answered_request(
    api: str, pki: _Pki, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    """Codex 703-1: the provider answered 200; then an ``on_response`` (the harness's
    ``after_provider_response``) hook's own HTTPS request failed on strict alone and the
    relaxation answered it. That error is the stream's first event - every adapter
    yields ``start`` after ``on_response`` - but it is not this request's handshake:
    measured before, the adapter sent the answered request a second time (2 POSTs)."""

    import httpx

    (tmp_path / "ok").mkdir()
    conformant_ca, conformant_chain = _make_chain(tmp_path / "ok", conformant=True)
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(conformant_ca.read_bytes() + pki.sloppy_ca.read_bytes())
    stream, model, options, answer = _adapter(api)
    provider = _TlsServer(conformant_chain, answer)
    telemetry = _TlsServer(pki.sloppy_chain)
    model = dataclasses.replace(model, base_url=(model.base_url or "").format(port=provider.port))
    _trusting(monkeypatch, bundle, strict=True)
    hook_calls: list[int] = []

    def on_response(_response: Any, _model: Any) -> None:
        hook_calls.append(1)
        with httpx.Client(timeout=10) as client:
            client.get(f"https://localhost:{telemetry.port}/")

    try:
        events = [
            e
            async for e in stream(
                model, Context(), dataclasses.replace(options, on_response=on_response)
            )
        ]
    finally:
        provider.close()
        telemetry.close()

    assert provider.requests == 1
    assert hook_calls == [1]
    errors = [e for e in events if isinstance(e, AssistantErrorEvent)]
    assert len(errors) == 1 and events == errors
    assert retry_verdict(errors[0].error_message or "") != "resend"
    assert len(notices) == 1  # the hook's host was measured and relaxed all the same
    # Fix round 2: the note's last line says this failure is not sent again; it used to
    # say "If the request still fails, the cause is not certificate strictness".
    shown = errors[0].error_message or ""
    assert shown.splitlines()[-1] == (
        f"{Relaxation.STILL_ENFORCED} This failure came after the provider had answered "
        "(a hook's own request), so the relaxation does not send it again."
    )
    assert "the cause is not certificate strictness" not in shown


def test_only_a_failure_the_relaxation_answers_after_the_response_changes_the_last_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix round 2, both sides: after the response, a strict-only failure (85) the
    relaxation answers ends with "not sent again"; an untrusted root (20) under the same
    relaxation is not strictness and keeps "the cause is not certificate strictness", as
    does the strict-only failure of the request itself (sent again; the line is for the
    rare one that fails once more)."""

    from aelix_ai.providers._error_hints import describe_provider_error
    from aelix_ai.utils.terminal_text import safe_error_for_terminal

    from tests.providers.test_tls_strict import _real_shape

    relaxation = Relaxation(
        host="localhost", verify_code=85, verify_message="Missing Authority Key Identifier"
    )
    monkeypatch.setattr(_tls_strict, "_relaxation", relaxation)
    strict_only = _real_shape(85, "Missing Authority Key Identifier")
    untrusted = _real_shape(20, "unable to get local issuer certificate")

    def last(exc: BaseException, *, after_response: bool) -> str:
        return describe_provider_error(exc, after_response=after_response).splitlines()[-1]

    assert (
        last(strict_only, after_response=True)
        == relaxation.remedy(after_response=True).splitlines()[-1]
    )
    assert last(strict_only, after_response=True).endswith("does not send it again.")
    for exc, after_response in ((untrusted, True), (strict_only, False), (untrusted, False)):
        assert last(exc, after_response=after_response).endswith(
            "the cause is not certificate strictness."
        ), (exc, after_response)
    whole = describe_provider_error(strict_only, after_response=True)
    assert safe_error_for_terminal(whole) == whole  # the TUI's 8 lines of 200, uncut


# ── fix round 1: a relaxation made before the CLI listened is still announced ─


def test_a_relaxation_made_before_any_listener_is_announced_to_the_first_one_once(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 703-3: an extension's ``session_start`` sends a request before ``-p``, json,
    rpc or the TUI registers its listener. The registration hands the relaxation over,
    once; a listener registered after that hears nothing."""

    server = _TlsServer(pki.sloppy_chain)
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    try:
        relaxation = maybe_relax_strict_for_session(_connect_failure(server.port))
    finally:
        server.close()
    assert relaxation is not None

    stderr = io.StringIO()
    remove = announce_on_stderr(stderr)
    later: list[Relaxation] = []
    remove_later = add_relaxation_listener(later.append)
    remove()
    remove_later()
    again: list[Relaxation] = []
    add_relaxation_listener(again.append)()

    assert stderr.getvalue() == relaxation.notice() + "\n"
    assert later == [] and again == []


def test_a_relaxation_a_listener_heard_is_not_handed_to_the_next_one(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    server = _TlsServer(pki.sloppy_chain)
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    try:
        relaxation = maybe_relax_strict_for_session(_connect_failure(server.port))
    finally:
        server.close()
    later: list[Relaxation] = []
    add_relaxation_listener(later.append)()
    assert notices == [relaxation] and later == []


# ── fix round 1: the check goes through the proxy the clients use ────────────


class _ConnectProxy:
    """An explicit HTTPS proxy that intercepts TLS, as a corporate one does: it answers
    ``CONNECT`` with 200 and then terminates TLS itself with ``chain`` (a leaf with no
    Authority Key Identifier), answering every request with ``answer``. Nothing
    listens on the CONNECT target: this network has no direct route."""

    def __init__(
        self, chain: Any, answer: bytes, *, status: bytes = b"200 Connection established"
    ) -> None:
        import socket
        import ssl
        import threading

        from tests.providers.test_tls_failure_not_retried_551 import _STDLIB_SSL_CONTEXT

        self.connects: list[bytes] = []
        self.requests = 0
        self._answer, self._status = answer, status
        self._context = _STDLIB_SSL_CONTEXT(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(str(chain))
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = int(self._srv.getsockname()[1])
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        import threading

        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: Any) -> None:
        from tests.providers.test_tls_failure_not_retried_551 import _read_request

        with conn:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            self.connects.append(head)
            conn.sendall(b"HTTP/1.1 " + self._status + b"\r\n\r\n")
            if not self._status.startswith(b"200"):
                return
            try:
                with self._context.wrap_socket(conn, server_side=True) as tls:
                    if _read_request(tls):
                        self.requests += 1
                        tls.sendall(self._answer)
            except OSError:
                return

    def close(self) -> None:
        self._srv.close()


def _no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No proxy in the environment, and none from the system: ``urllib``'s
    ``getproxies``, which httpx reads, falls back to macOS's and Windows' own proxy
    settings when the environment names none, and a developer's machine may have one."""

    import urllib.request

    import httpx._utils

    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setattr(httpx._utils, "getproxies", urllib.request.getproxies_environment)


async def test_behind_an_explicit_proxy_the_check_tunnels_and_the_request_is_answered(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    """explicit-proxy-not-relaxed: ``HTTPS_PROXY`` names an intercepting proxy and there
    is no direct route. The confirmation handshakes used to connect directly, failed
    both ways and relaxed nothing, so the strict advice came on every run; now they go
    through a ``CONNECT`` tunnel, as the request does, within the same 5 s bound."""

    from tests.providers.test_tls_failure_not_retried_551 import _free_port

    stream, model, options, answer = _adapter("openai-completions")
    proxy = _ConnectProxy(pki.sloppy_chain, answer)
    target = _free_port()  # nothing listens here
    model = dataclasses.replace(model, base_url=(model.base_url or "").format(port=target))
    _no_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", f"http://user:p%40ss@127.0.0.1:{proxy.port}")
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    try:
        events = [e async for e in stream(model, Context(), options)]
    finally:
        proxy.close()

    assert [e for e in events if isinstance(e, AssistantErrorEvent)] == []
    assert "".join(_texts(events)) == _HELLO
    relaxation = _tls_strict.session_relaxation()
    assert relaxation is not None and notices == [relaxation]
    tunnels = [c for c in proxy.connects if c.startswith(f"CONNECT localhost:{target} ".encode())]
    # the request, the check's two (through the tunnel), the request sent again
    assert len(tunnels) == len(proxy.connects) == 4 and proxy.requests == 1
    checks = [c for c in tunnels if b"Proxy-Authorization: Basic dXNlcjpwQHNz\r\n" in c]
    assert len(checks) == 4  # the check sends the URL's credentials, decoded, as httpx does


def test_the_check_resolves_the_proxy_as_httpx_does(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_proxy_env(monkeypatch)
    assert _tls_strict._proxy_for("api.example.com", 443) is None
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
    assert str(_tls_strict._proxy_for("api.example.com", 443)) == "http://proxy.corp:3128"
    monkeypatch.setenv("NO_PROXY", "localhost,.example.org")
    assert _tls_strict._proxy_for("localhost", 8443) is None
    assert _tls_strict._proxy_for("api.example.org", 443) is None
    assert _tls_strict._proxy_for("api.example.com", 443) is not None
    monkeypatch.setenv("NO_PROXY", "*")
    assert _tls_strict._proxy_for("api.example.com", 443) is None


def test_a_proxy_that_refuses_the_connect_or_is_not_http_relaxes_nothing(
    pki: _Pki, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.providers.test_tls_failure_not_retried_551 import _free_port

    refusing = _ConnectProxy(pki.sloppy_chain, b"", status=b"407 Proxy Authentication Required")
    _no_proxy_env(monkeypatch)
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    target = _free_port()
    try:
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{refusing.port}")
        assert _tls_strict._confirm_relaxable("localhost", target) is False
        monkeypatch.setenv("HTTPS_PROXY", f"https://127.0.0.1:{refusing.port}")
        assert _tls_strict._handshake("localhost", target, strict=False) is False
    finally:
        refusing.close()
    assert len(refusing.connects) == 2  # the 407 twice; the https:// proxy was never tunnelled


# ── fix round 1: an OAuth token refresh measures, relaxes and asks once more ──


class _RefreshingProvider:
    """An OAuth provider whose refresh is a real HTTPS request, on a new client per call,
    as the built-in Copilot, Codex and Anthropic refreshes are."""

    id = "tls-703-oauth"
    name = "TLS 703 OAuth"

    def __init__(self, port: int) -> None:
        self.port = port
        self.calls = 0

    async def login(self, _callbacks: Any) -> Any:
        raise NotImplementedError

    async def refresh_token(self, credentials: Any) -> Any:
        import httpx
        from aelix_ai.oauth.types import OAuthCredentials

        self.calls += 1
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(f"https://localhost:{self.port}/token")
        return OAuthCredentials(refresh="r2", access="fresh", expires=2**62)

    def get_api_key(self, credentials: Any) -> str:
        return str(credentials.access)


@pytest.mark.parametrize("chain", ["sloppy", "untrusted"])
async def test_an_oauth_refresh_that_failed_on_strict_alone_is_asked_again_and_answers(
    chain: str, pki: _Pki, monkeypatch: pytest.MonkeyPatch, notices: list[Relaxation]
) -> None:
    """oauth-refresh-not-relaxed: an expired login behind a strict-only proxy. The refresh
    never measured, so #551 marked it "not transient" and every launch ended in "OAuth
    refresh failed ... Run /login". Now it relaxes, is announced once and asks once
    more; a chain this machine does not trust still fails, asked once."""

    from aelix_ai.oauth import get_oauth_api_key_from_credentials
    from aelix_ai.oauth._registry import register_oauth_provider, unregister_oauth_provider
    from aelix_ai.oauth.types import OAuthCredentials

    server = _TlsServer(pki.sloppy_chain if chain == "sloppy" else pki.untrusted_chain)
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    provider = _RefreshingProvider(server.port)
    register_oauth_provider(provider)  # type: ignore[arg-type]
    expired = OAuthCredentials(refresh="r1", access="stale", expires=1000)
    try:
        if chain == "sloppy":
            result = await get_oauth_api_key_from_credentials(provider.id, {provider.id: expired})
        else:
            with pytest.raises(RuntimeError, match="Failed to refresh OAuth token"):
                await get_oauth_api_key_from_credentials(provider.id, {provider.id: expired})
    finally:
        unregister_oauth_provider(provider.id)
        server.close()

    if chain == "sloppy":
        assert result is not None and result.api_key == "fresh"
        assert provider.calls == 2 and len(notices) == 1
        assert server.requests == 1  # the failed handshake sent nothing
    else:
        assert provider.calls == 1 and notices == []
        assert _tls_strict.session_relaxation() is None
