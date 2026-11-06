"""#572 - the retry verdict for a TLS failure is the harness's record, not the error text's.

#551 put the adapter's verdict ("sending this again fails the same way") on the
error message's string object (``ClassifiedErrorText.repeats_on_retry``), and the
harness read it there. Two things went wrong on 679e5e5c:

1. The verdict had one direction. A request that failed on Python 3.13's strict
   checking alone, which the session's strict relaxation then answers, was not
   marked and fell through to the retry pattern. The OpenAI and Anthropic SDKs
   word it ``Connection error.``, which the pattern retries; the Codex adapter
   words it ``[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Missing
   Authority Key Identifier``, which it does not, so that prompt ended in an error
   and the next prompt was the one that passed (``.omc/probes/b4-a-live/551/``).
2. A ``message_end`` hook that rebuilt the answer (a JSON round trip, a message
   built anew from its fields) turned the text into a plain ``str``: the mark was
   gone, and an untrusted certificate's ``Connection error.`` was retried 2, 4 and
   8 s apart again. #186's ``classifier_text`` went the same way.

Now the adapter's verdict has three values - ``repeats`` (not retried),
``resend`` (retried, whatever the text says) and none (the pattern decides) - and
the harness copies the adapter's error text, verdict and ``classifier_text``
included, into a record of its own at ``message_end``, before the hooks run, and
points it at the message they leave. #379's ``_SetupFailure`` is the precedent.

Real TLS against a local server (``ssl.create_default_context`` replaced by one
that trusts only a test CA, strict forced on or off) through the real httpx,
Anthropic SDK, Anthropic and Codex adapters and the harness. Fake keys; no
network beyond 127.0.0.1.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

pytest.importorskip("cryptography")

from aelix_agent_core.harness import core as core_mod  # noqa: E402
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions  # noqa: E402
from aelix_agent_core.harness.hooks import MessageEndEventResult  # noqa: E402
from aelix_agent_core.session import MemorySessionStorage, Session  # noqa: E402
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent  # noqa: E402
from aelix_ai.messages import AssistantMessage, TextContent  # noqa: E402
from aelix_ai.providers import _tls_strict  # noqa: E402
from aelix_ai.providers._error_hints import describe_provider_error  # noqa: E402
from aelix_ai.providers._tls_strict import Relaxation, reset_strict_relaxation  # noqa: E402
from aelix_ai.streaming import (  # noqa: E402
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    Model,
)
from aelix_ai.utils.overflow import (  # noqa: E402
    classifier_text_of,
    retry_verdict,
    with_classifier_text,
)

from tests.providers.test_tls_failure_not_retried_551 import (  # noqa: E402
    _REMEDY,
    _anthropic_harness,
    _coded,
    _free_port,
    _Pki,
    _sdk_failure,
    _TlsServer,
    _trusting,
)
from tests.providers.test_tls_strict import _make_chain  # noqa: E402


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    """The #551 file's certificates, made for this module."""

    trusted_ca, _ = _make_chain(tmp_path_factory.mktemp("trusted572"), conformant=True)
    _, untrusted_chain = _make_chain(tmp_path_factory.mktemp("untrusted572"), conformant=True)
    sloppy_ca, sloppy_chain = _make_chain(tmp_path_factory.mktemp("sloppy572"), conformant=False)
    return _Pki(trusted_ca, untrusted_chain, sloppy_ca, sloppy_chain)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No strict relaxation crosses a row's edges (see the #551 file's fixture)."""

    reset_strict_relaxation()
    yield
    reset_strict_relaxation()


# ── the adapter's verdict: three values ──────────────────────────────────────


@pytest.mark.parametrize(
    ("relaxed_on", "code", "verdict"),
    [
        pytest.param(None, 85, "repeats", id="no-relaxation-strict-code"),
        pytest.param(None, 20, "repeats", id="no-relaxation-untrusted"),
        pytest.param(85, 85, "resend", id="the-code-it-was-measured-on"),
        pytest.param(85, 89, "resend", id="another-strict-only-code"),
        pytest.param(85, 20, "repeats", id="untrusted-under-a-relaxation"),
        pytest.param(85, None, "repeats", id="no-code"),
    ],
)
def test_a_tls_failure_gets_one_of_two_verdicts(
    relaxed_on: int | None, code: int | None, verdict: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if relaxed_on is not None:
        relaxation = Relaxation(host="localhost", verify_code=relaxed_on, verify_message="x")
        monkeypatch.setattr(_tls_strict, "session_relaxation", lambda: relaxation)

    assert retry_verdict(describe_provider_error(_coded(code))) == verdict


def test_anything_else_gets_none_and_the_pattern_decides() -> None:
    refused = describe_provider_error(_sdk_failure(_free_port()))
    assert retry_verdict(refused) is None
    assert retry_verdict("Connection error.") is None
    marked = with_classifier_text("x", "x", resend_on_retry=True)
    assert marked == "x" and retry_verdict(marked) == "resend"
    assert classifier_text_of(marked) == "x"


# ── the harness: a message_end hook that rebuilds the answer ─────────────────


def _json_round_trip(harness: AgentHarness, seen: list[str] | None = None) -> None:
    """An extension that rebuilds ``error_message`` through JSON and returns the message
    as a replacement: the same words, a plain ``str``."""

    async def round_trip(event: Any, _ctx: Any) -> Any:
        message = event.message
        if isinstance(message, AssistantMessage) and message.error_message:
            text = json.loads(json.dumps(message.error_message))
            assert type(text) is str
            if seen is not None:
                seen.append(text)
            return MessageEndEventResult(message=dataclasses.replace(message, error_message=text))
        return None

    harness.hooks.on("message_end", round_trip)


def _retries(events: list[Any]) -> tuple[list[int], list[tuple[bool, int]]]:
    starts = [e.attempt for e in events if isinstance(e, AutoRetryStartEvent)]
    ends = [(e.success, e.attempt) for e in events if isinstance(e, AutoRetryEndEvent)]
    return starts, ends


async def test_an_untrusted_certificate_is_not_retried_after_a_hook_rebuilds_the_answer(
    pki: _Pki,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The issue's second row. On 679e5e5c the rebuilt ``Connection error.`` was retried
    three times (4 handshakes) and the advice shown once per attempt."""

    server = _TlsServer(pki.untrusted_chain)
    _trusting(monkeypatch, pki.trusted_ca, strict=False)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    events: list[Any] = []
    seen: list[str] = []
    harness = _anthropic_harness(server.port, events)
    _json_round_trip(harness, seen)
    try:
        await harness.prompt("hi")
    finally:
        server.close()

    assert seen and seen[0].startswith("Connection error.")
    assert server.accepts == 1
    assert _retries(events) == ([], [])
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "error"
    assert type(last.error_message) is str and last.error_message.count(_REMEDY) == 1
    await harness.dispose()


async def test_a_resend_verdict_survives_the_rebuild_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure the relaxation answers, in words the pattern does not retry: sent again
    once and answered, with or without a hook that rebuilds the failed answer."""

    relaxation = Relaxation(host="localhost", verify_code=85, verify_message="x")
    monkeypatch.setattr(_tls_strict, "session_relaxation", lambda: relaxation)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    failure = describe_provider_error(_coded(85))
    assert retry_verdict(failure) == "resend"
    assert core_mod._RETRYABLE_ERROR_PATTERN.search(classifier_text_of(failure)) is None
    plan = [failure, None]
    harness, events = _scripted(plan)
    _json_round_trip(harness)

    await harness.prompt("hi")

    assert plan == []
    assert _retries(events) == ([1], [(True, 1)])
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "stop"
    await harness.dispose()


async def test_the_classifier_text_survives_the_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """#186's carrier, the issue's third row: a ``502`` past the 512-character cut is
    still what the retry reads after a hook made the text a plain ``str``. On
    679e5e5c the hook's text had no ``502`` in it and the turn was not retried."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    failure = describe_provider_error(
        RuntimeError("upstream said: " + "x" * 600 + " 502 Bad Gateway")
    )
    assert "502" not in str(failure) and "502" in classifier_text_of(failure)
    plan = [failure, None]
    harness, events = _scripted(plan)
    _json_round_trip(harness)

    await harness.prompt("hi")

    assert plan == []
    assert _retries(events) == ([1], [(True, 1)])
    await harness.dispose()


async def test_an_overflow_past_the_cut_still_compacts_after_the_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The overflow recovery reads the harness's record too: a provider's ``prompt is
    too long`` after a long diagnostic, cut from the display, still starts the
    compaction once a hook rebuilt the answer."""

    failure = describe_provider_error(RuntimeError("400: " + "y" * 600 + " prompt is too long"))
    assert "too long" not in str(failure)
    harness, _events = _scripted([failure])
    _json_round_trip(harness)
    compactions: list[Any] = []

    async def compact(**kwargs: Any) -> None:
        compactions.append(kwargs)
        raise RuntimeError("stop here")

    monkeypatch.setattr(harness, "compact", compact)
    await harness.prompt("hi")

    last = next(m for m in reversed(harness._state.messages) if isinstance(m, AssistantMessage))
    assert type(last.error_message) is str  # the hook's rebuild is what state holds
    assert len(compactions) == 1
    await harness.dispose()


async def test_a_message_the_harness_never_recorded_is_read_by_its_own_mark() -> None:
    """A compaction's summary request never passes ``message_end``: its message is read
    as the adapter described it, so the Codex text the pattern misses is sent again."""

    harness = AgentHarness(AgentHarnessOptions(session=Session(MemorySessionStorage())))
    resend = AssistantMessage(
        content=[],
        stop_reason="error",
        error_message=with_classifier_text("x", "x", resend_on_retry=True),
    )
    assert harness._is_retryable_error(resend) is True
    repeats = AssistantMessage(
        content=[],
        stop_reason="error",
        error_message=with_classifier_text("502", "502", repeats_on_retry=True),
    )
    assert harness._is_retryable_error(repeats) is False
    await harness.dispose()


async def test_a_successful_overflow_compaction_drops_the_rebuilt_answer() -> None:
    """The overflow recovery's SECOND pop reads the record too (fix round 1): after a real
    ``compact()`` rebuilt the live context from the session branch, the failed answer a
    hook rebuilt - ``prompt is too long`` past the cut, a plain ``str`` - is its last
    entry again, and the re-run must not carry it. Before, only the first check read
    the record, and the context ended ``[..., error, stop]``."""

    from tests.harness.test_overflow_recovery import _override, _seed_compactable

    failure = describe_provider_error(RuntimeError("400: " + "y" * 600 + " prompt is too long"))
    assert "too long" not in str(failure)
    plan: list[Any] = [failure, None]
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    harness, events = _scripted(plan, session, _summarizer_override=_override())
    _json_round_trip(harness)

    await harness.prompt("hi")

    assert plan == []
    branch = await session.get_branch()
    assert any(getattr(entry, "type", None) == "compaction" for entry in branch)
    answers = [m for m in harness._state.messages if isinstance(m, AssistantMessage)]
    assert [m.stop_reason for m in answers] == ["stop"]
    assert _retries(events) == ([], [])
    await harness.dispose()


def test_the_put_back_after_a_failed_compaction_reads_the_record_too() -> None:
    """:func:`_restore_failed_answer` (#391) leaves the answer out when the rebuilt
    context ends in an overflow in its place; a hook's plain-``str`` rebuild of one
    past the cut is that overflow when the harness recorded the adapter's text."""

    from aelix_agent_core.types import AgentState

    failure = describe_provider_error(RuntimeError("400: " + "y" * 600 + " prompt is too long"))
    adapter = AssistantMessage(content=[], stop_reason="error", error_message=failure)
    rebuilt = dataclasses.replace(adapter, error_message=json.loads(json.dumps(str(failure))))
    harness = AgentHarness(AgentHarnessOptions(session=Session(MemorySessionStorage())))
    record = core_mod._record_provider_error(adapter)
    assert record is not None
    record.message = rebuilt
    harness._error_record = record
    state = AgentState(messages=[rebuilt])

    core_mod._restore_failed_answer(state, adapter, 200_000, harness=harness)

    assert state.messages == [rebuilt]


def _rewrite_to(harness: AgentHarness, words: str) -> None:
    """A ``message_end`` hook that gives a failed answer NEW words (not a rebuild)."""

    async def rewrite(event: Any, _ctx: Any) -> Any:
        message = event.message
        if isinstance(message, AssistantMessage) and message.error_message:
            return MessageEndEventResult(message=dataclasses.replace(message, error_message=words))
        return None

    harness.hooks.on("message_end", rewrite)


@pytest.mark.parametrize(
    ("adapter_text", "hook_words", "retried"),
    [
        pytest.param(
            "the request was rejected", "overloaded", True, id="to-words-the-pattern-retries"
        ),
        pytest.param(
            "503 Service Unavailable",
            "rejected, do not send again",
            False,
            id="to-words-it-does-not",
        ),
    ],
)
async def test_a_hook_that_rewrites_a_plain_error_is_classified_on_its_new_words(
    adapter_text: str, hook_words: str, retried: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0128 (#572 amendment): an error text that carries nothing beyond its words is
    not recorded, so a hook's rewrite of it is what the retry reads, as pi reads the
    message its hooks leave."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    assert type(adapter_text) is str
    plan: list[Any] = [adapter_text, None]
    harness, events = _scripted(plan)
    _rewrite_to(harness, hook_words)

    await harness.prompt("hi")

    assert _retries(events) == (([1], [(True, 1)]) if retried else ([], []))
    await harness.dispose()


@pytest.mark.parametrize(
    ("relaxed", "hook_words", "retried"),
    [
        pytest.param(
            False, "rejected, do not send again", True, id="a-502-past-the-cut-stays-retried"
        ),
        pytest.param(True, "overloaded", False, id="an-untrusted-root-stays-unretried"),
    ],
)
async def test_a_hook_that_rewrites_an_adapter_described_error_does_not_change_its_verdict(
    relaxed: bool, hook_words: str, retried: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of that rule: an error its adapter described - a
    ``ClassifiedErrorText``, which a TLS verdict and a text the 512-character cut
    shortened are - is classified as the adapter described it whatever words a
    ``message_end`` hook gives it, as #379's ``_SetupFailure`` decides a setup failure
    whatever a hook made of its text. Before #572 the hook's words decided."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    if relaxed:
        failure = describe_provider_error(_coded(20))
        assert retry_verdict(failure) == "repeats"
    else:
        failure = describe_provider_error(
            RuntimeError("upstream said: " + "x" * 600 + " 502 Bad Gateway")
        )
        assert retry_verdict(failure) is None and "502" not in str(failure)
    plan: list[Any] = [failure, None]
    harness, events = _scripted(plan)
    _rewrite_to(harness, hook_words)

    await harness.prompt("hi")

    assert _retries(events) == (([1], [(True, 1)]) if retried else ([], []))
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.stop_reason == ("stop" if retried else "error")
    if not retried:
        assert last.error_message == hook_words  # the hook's words are what is shown
    await harness.dispose()


# ── the Codex adapter, end to end ────────────────────────────────────────────


def _codex_answer() -> bytes:
    events = [
        {"type": "response.created", "response": {"id": "resp_572"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "msg_1"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "hello"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "msg_1",
                "content": [{"type": "output_text", "text": "hello"}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_572",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        },
    ]
    body = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


def _codex_harness(port: int, events: list[Any], **kw: Any) -> AgentHarness:
    from aelix_ai.providers.openai_codex_responses import stream_openai_codex_responses

    from tests.providers.test_openai_codex_responses_adapter import _make_token, _model

    async def stream(model: Any, context: Any, options: Any) -> AsyncIterator[Any]:
        async for event in stream_openai_codex_responses(model, context, options):
            yield event

    harness = AgentHarness(
        AgentHarnessOptions(
            session=Session(MemorySessionStorage()),
            model=_model(base_url=f"https://localhost:{port}/backend-api"),
            stream_fn=stream,
            get_api_key=lambda _provider: _make_token(),
            **kw,
        )
    )
    harness._state.auto_compaction_enabled = False
    harness.subscribe(lambda event: events.append(event) or None)
    return harness


@pytest.mark.parametrize("hook", [False, True], ids=["no-hook", "json-round-trip-hook"])
async def test_a_codex_request_that_failed_on_strict_alone_is_sent_again_and_answered(
    hook: bool,
    pki: _Pki,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The issue's first row. A chain this machine trusts whose leaf has no Authority
    Key Identifier (code 85 under strict only): the relaxation measures it (two
    handshakes) and relaxes, and the request is sent again on a new client, which
    passes. On 679e5e5c the prompt ended in the error (exit 1 in ``-p``). #572 sent
    it again from the turn's retry; since #703 the adapter sends it again itself,
    before the turn - or a hook - sees a failure (ADR-0294)."""

    server = _TlsServer(pki.sloppy_chain, _codex_answer())
    _trusting(monkeypatch, pki.sloppy_ca, strict=True)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    events: list[Any] = []
    harness = _codex_harness(server.port, events)
    if hook:
        _json_round_trip(harness)
    try:
        await harness.prompt("hi")
    finally:
        server.close()

    relaxation = _tls_strict.session_relaxation()
    assert relaxation is not None and relaxation.verify_code == 85
    assert server.accepts == 4 and server.requests == 1
    answers = [m for m in harness._state.messages if isinstance(m, AssistantMessage)]
    assert [m.stop_reason for m in answers] == ["stop"]
    assert [b.text for b in answers[0].content if isinstance(b, TextContent)] == ["hello"]
    assert _retries(events) == ([], [])
    await harness.dispose()


async def test_a_codex_request_to_an_untrusted_server_is_still_sent_once(
    pki: _Pki,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#551's contract stays: with strict on, the relaxation's two handshakes relax
    nothing, the request is not sent again, and the advice is shown once."""

    server = _TlsServer(pki.untrusted_chain, _codex_answer())
    _trusting(monkeypatch, pki.trusted_ca, strict=True)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    events: list[Any] = []
    harness = _codex_harness(server.port, events)
    try:
        await harness.prompt("hi")
    finally:
        server.close()

    assert _tls_strict.session_relaxation() is None
    assert server.accepts == 3 and server.requests == 0
    assert _retries(events) == ([], [])
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "error"
    assert (last.error_message or "").count(_REMEDY) == 1
    assert retry_verdict(last.error_message or "") == "repeats"
    await harness.dispose()


# ── helpers ──────────────────────────────────────────────────────────────────


def _scripted(
    plan: list[Any], session: Session | None = None, **options: Any
) -> tuple[AgentHarness, list[Any]]:
    """A harness whose stream answers from ``plan``: an error text fails the attempt
    (as an adapter's error event, before any start), ``None`` answers ``ok``."""

    async def stream(_model: Any, _context: Any, _options: Any) -> AsyncIterator[Any]:
        step = plan.pop(0) if plan else None
        if step is not None:
            message = AssistantMessage(content=[], stop_reason="error", error_message=step)
            yield AssistantErrorEvent(reason="error", error=message, error_message=step)
            return
        partial = AssistantMessage(content=[])
        yield AssistantStartEvent(partial=partial)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="stop"),
        )

    harness = AgentHarness(
        AgentHarnessOptions(
            session=session if session is not None else Session(MemorySessionStorage()),
            model=Model(api="tls-572", id="m", provider="p", context_window=200_000),
            stream_fn=stream,
            get_api_key=lambda _provider: "fake-key-572",
            **options,
        )
    )
    harness._state.auto_compaction_enabled = True
    events: list[Any] = []
    harness.subscribe(lambda event: events.append(event) or None)
    return harness, events
