"""#379 — the harness's half: an auth callback failure marked transient is retried.

pi's ``lazyStream`` turns a setup failure into an error assistant message
(``packages/ai/src/api/lazy.ts:4-23``, ``:46-60`` @ 1cedd3272) and its
auto-retry decides on that message. In aelix the auth CALLBACK decides: an
exception it raises with a non-empty ``retry_reason`` ends the stream with an
error assistant message the auto-retry loop retries by the answer the harness
recorded for it (``_SetupFailure``, review round 3: never the message's text or
type, which a ``message_end`` hook can rebuild); any other raise is
``AgentHarnessError("auth")`` as before (ADR-0251 §4) - except inside a retry
sequence, where it ends the turn as a NON-retryable error message so the sequence
closes the way a provider's non-retryable error closes it (review round 2,
ADR-0251 §12.5: pi's ``lazyStream`` turns every setup failure into an error
message, and ``agent-session.ts:1874-1882`` @ 1cedd3272 emits
``auto_retry_end(false)`` and resets the counter). These rows pin that contract
without the coding agent.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    _TurnState,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import (
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    MessageEndEvent,
    MessageStartEvent,
)
from aelix_ai import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    clear_providers,
    register_provider,
)
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.utils.overflow import is_context_overflow

_MODEL = Model(id="m", provider="p", api="anthropic-messages", base_url="http://x.invalid")
#: Matches nothing in ``_RETRYABLE_ERROR_PATTERN`` - httpx's text for a refused connection,
#: in pi's words for a transient refresh failure (no /login hint, review round 2).
_UNMATCHED = "OAuth refresh failed for p: All connection attempts failed"
#: A refused refresh, as the CLI's callback raises it (``OAuthRefreshError``'s text).
_REFUSED = 'OAuth refresh failed for p: (401): {"error": "invalid_grant", "id": "502"}. Run /login to sign in to p again.'
_TRANSIENT_REASON = "the token endpoint answered HTTP 502"


class _Transient(RuntimeError):
    def __init__(self, message: str, retry_reason: Any) -> None:
        super().__init__(message)
        self.retry_reason = retry_reason


@pytest.fixture(autouse=True)
def _providers() -> Any:
    clear_providers()
    yield
    clear_providers()


def _hi() -> Context:
    return Context(messages=[UserMessage(content=[TextContent(text="hi")])])


async def _drain(harness: AgentHarness) -> list[AssistantMessageEvent]:
    stream = harness._make_stream_fn(lambda: _TurnState(system_prompt="", model=_MODEL))
    return [e async for e in stream(_MODEL, _hi(), SimpleStreamOptions())]


async def test_a_transient_raise_ends_the_stream_with_an_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(core_mod, "stream_simple", never)

    async def callback(_model: Model) -> Any:
        raise _Transient(_UNMATCHED, "the token endpoint could not be reached (ConnectError)")

    harness = AgentHarness(AgentHarnessOptions(model=_MODEL, get_api_key_and_headers=callback))
    events = await _drain(harness)

    # Review round 2: a start first, so the loop emits message_start before message_end
    # (pi's loop does for a message with no partial, ``agent-loop.ts:451-453``); the
    # partial IS the failure, as pi's message_start carries the final message.
    assert [e.type for e in events] == ["start", "error"]
    message = events[1].error  # type: ignore[union-attr]
    assert events[0].partial is message  # type: ignore[union-attr]
    assert message.stop_reason == "error"
    assert message.content == []
    assert (message.api, message.provider, message.model) == ("anthropic-messages", "p", "m")
    assert message.timestamp is not None
    assert message.error_message == _UNMATCHED  # pi: the error's own message, no prefix
    assert harness._is_retryable_error(message) is True


@pytest.mark.parametrize("reason", [None, "", 0, ["x"]], ids=repr)
async def test_any_other_raise_is_still_an_auth_error(reason: Any) -> None:
    async def callback(_model: Model) -> Any:
        raise _Transient("OAuth refresh failed for p: 401. Run /login to sign in to p again.", reason)

    harness = AgentHarness(AgentHarnessOptions(model=_MODEL, get_api_key_and_headers=callback))
    with pytest.raises(AgentHarnessError) as caught:
        await _drain(harness)
    assert caught.value.code == "auth"
    assert str(caught.value) == (
        "get_api_key_and_headers failed: OAuth refresh failed for p: 401. Run /login to sign in to p again."
    )


def _recorded(harness: AgentHarness, text: str, reason: str | None) -> AssistantMessage:
    """A setup failure as the stream fn leaves it: the message, and the harness's record."""

    message = AssistantMessage(
        content=[], stop_reason="error", error_message=core_mod._setup_error_text(text)
    )
    harness._setup_failure = core_mod._SetupFailure(message=message, retry_reason=reason, ended=True)
    return message


def test_the_harness_record_decides_not_the_text_or_type() -> None:
    """Review round 3: the decision is the record the harness kept, not the message."""

    harness = AgentHarness(AgentHarnessOptions(model=_MODEL))
    recorded = _recorded(harness, _UNMATCHED, _TRANSIENT_REASON)
    assert harness._is_retryable_error(recorded) is True
    # The same words as a plain string (a resumed session, a hook's rebuild of another
    # message) are classified by the regex, as before.
    plain = AssistantMessage(content=[], stop_reason="error", error_message=_UNMATCHED)
    assert harness._is_retryable_error(plain) is False
    # The text's TYPE decides nothing either: the marker class on an unrecorded message
    # is just text.
    typed = AssistantMessage(content=[], stop_reason="error", error_message=core_mod._setup_error_text(_UNMATCHED))
    assert harness._is_retryable_error(typed) is False
    # A refusal recorded with no reason is never retried, whatever its text says.
    refused = _recorded(harness, "OAuth refresh failed for p: (401): 502 Bad Gateway", None)
    assert harness._is_retryable_error(refused) is False


def test_a_token_endpoints_body_cannot_route_the_turn_into_compaction() -> None:
    """The classifiers read aelix's own sentence, not the server's words (#186's carrier)."""

    overflowing = "OAuth refresh failed for p: (502): prompt is too long: maximum context length is 10 tokens."
    as_plain = AssistantMessage(content=[], stop_reason="error", error_message=overflowing)
    assert is_context_overflow(as_plain, 100), "positive control: the words alone read as an overflow"
    assert is_context_overflow(
        AssistantMessage(content=[], stop_reason="error", error_message=core_mod._setup_error_text(overflowing)), 100
    ) is False
    harness = AgentHarness(AgentHarnessOptions(model=_MODEL))
    assert harness._is_retryable_error(_recorded(harness, overflowing, _TRANSIENT_REASON)) is True


@pytest.mark.parametrize("recorded", [False, True], ids=["control-unrecorded", "recorded"])
async def test_a_recorded_setup_failure_is_never_an_overflow_whatever_its_text(
    recorded: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 3: after a hook made the text a plain str, a token endpoint's body that
    reads as an overflow must not start an overflow compaction; the record says what it is."""

    model = dataclasses.replace(_MODEL, context_window=100)
    harness = AgentHarness(AgentHarnessOptions(model=model, session=Session(MemorySessionStorage())))
    overflowing = "OAuth refresh failed for p: (502): prompt is too long: maximum context length is 10 tokens."
    failure = AssistantMessage(content=[], stop_reason="error", error_message=overflowing, timestamp=9e12)
    harness._state.messages = [UserMessage(content=[TextContent(text="hi")]), failure]
    if recorded:
        harness._setup_failure = core_mod._SetupFailure(message=failure, retry_reason=_TRANSIENT_REASON, ended=True)
    compactions: list[Any] = []

    async def compact(**kwargs: Any) -> None:
        compactions.append(kwargs)

    monkeypatch.setattr(harness, "compact", compact)
    await harness._try_overflow_recovery("")
    assert bool(compactions) is (not recorded)


def _json_round_trip_hook(harness: AgentHarness) -> None:
    """Codex r2 cat1: an extension that rebuilds error_message through JSON - the same text,
    a plain ``str`` - and returns the message as a replacement."""

    from aelix_agent_core.harness.hooks import MessageEndEventResult

    async def round_trip(event: Any, _ctx: Any) -> Any:
        message = event.message
        if isinstance(message, AssistantMessage) and message.error_message:
            text = json.loads(json.dumps(message.error_message))
            assert type(text) is str
            return MessageEndEventResult(message=dataclasses.replace(message, error_message=text))
        return None

    harness.hooks.on("message_end", round_trip)


@pytest.mark.parametrize("hook", [False, True], ids=["no-hook", "json-round-trip-hook"])
async def test_a_hook_that_rebuilds_the_text_changes_no_retry_decision(
    hook: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[transient (unmatched text), refused (body names 502), then ok]: one start, one
    end(False, 1), whether or not a message_end hook turned the text into a plain str.
    On 7b0207bb the hook made it [1, 2] and end(True, 2) - the refusal retried by its text."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    _ok_provider(["ok"])
    answers: list[Any] = [_Transient(_UNMATCHED, _TRANSIENT_REASON), _Transient(_REFUSED, None)]

    async def callback(_model: Model) -> Any:
        if answers:
            raise answers.pop(0)
        return {"apiKey": "k-fake", "headers": {}}

    harness, events = _sequenced_harness(callback)
    if hook:
        _json_round_trip_hook(harness)
    await harness.prompt("hi")
    starts, ends = _retry_events(events)
    assert starts == [1]
    assert [(e[0], e[1]) for e in ends] == [(False, 1)]
    assert answers == []
    last = harness.state.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert type(last.error_message) is (str if hook else core_mod._SetupErrorText)


async def test_the_turn_is_retried_and_the_callback_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry loop retries it with its own budget, and each attempt asks the callback anew."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    keys_seen: list[str | None] = []

    async def fake(model: Model, context: Context, options: SimpleStreamOptions) -> AsyncIterator[Any]:
        keys_seen.append(options.api_key)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(reason="stop", message=AssistantMessage(stop_reason="stop"))

    register_provider("anthropic-messages", fake)
    calls = {"n": 0}

    async def callback(_model: Model) -> Any:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Transient(_UNMATCHED, "the token endpoint could not be reached (ConnectError)")
        return {"apiKey": "fresh-fake", "headers": {}}

    harness = AgentHarness(
        AgentHarnessOptions(model=_MODEL, session=Session(MemorySessionStorage()), get_api_key_and_headers=callback)
    )
    harness._state.auto_compaction_enabled = False
    events: list[Any] = []
    harness.subscribe(events.append)

    await harness.prompt("hi")

    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    assert calls["n"] == 3
    assert keys_seen == ["fresh-fake"]
    assert [s.attempt for s in starts] == [1, 2]
    assert all(s.error_message == _UNMATCHED for s in starts)
    assert [(e.success, e.attempt) for e in ends] == [(True, 2)]


# === review round 2: the sequence always closes ===============================


def _sequenced_harness(callback: Any) -> tuple[AgentHarness, list[Any]]:
    harness = AgentHarness(
        AgentHarnessOptions(model=_MODEL, session=Session(MemorySessionStorage()), get_api_key_and_headers=callback)
    )
    harness._state.auto_compaction_enabled = False
    events: list[Any] = []
    harness.subscribe(events.append)
    return harness, events


def _retry_events(events: list[Any]) -> tuple[list[int], list[tuple[bool, int, str | None]]]:
    return (
        [e.attempt for e in events if isinstance(e, AutoRetryStartEvent)],
        [(e.success, e.attempt, e.final_error) for e in events if isinstance(e, AutoRetryEndEvent)],
    )


def _ok_provider(answers: list[str]) -> list[str | None]:
    """A provider answering each call from ``answers`` (``"502"`` or ``"ok"``; the last repeats)."""

    keys: list[str | None] = []

    async def fake(model: Model, context: Context, options: SimpleStreamOptions) -> AsyncIterator[Any]:
        keys.append(options.api_key)
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if answer == "502":
            failed = AssistantMessage(content=[], stop_reason="error", error_message="502 Bad Gateway")
            yield AssistantErrorEvent(reason="error", error=failed, error_message="502 Bad Gateway")
            return
        yield AssistantDoneEvent(reason="stop", message=AssistantMessage(stop_reason="stop"))

    register_provider("anthropic-messages", fake)
    return keys


async def test_a_refused_refresh_after_a_transient_one_closes_the_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[502, 401]: one start, one end(False, 1) carrying the refused text, counter 0, no raise.

    The refused text is the one the first-attempt raise carries, byte for byte, and its body
    naming 502 does not make it retryable."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    _ok_provider(["ok"])
    answers = [_Transient(_UNMATCHED, _TRANSIENT_REASON), _Transient(_REFUSED, None)]

    async def callback(_model: Model) -> Any:
        raise answers.pop(0)

    harness, events = _sequenced_harness(callback)
    await harness.prompt("hi")

    expected = f"get_api_key_and_headers failed: {_REFUSED}"
    assert _retry_events(events) == ([1], [(False, 1, expected)])
    assert harness._retry_attempt == 0
    last = harness.state.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert last.stop_reason == "error"
    assert last.error_message == expected
    assert harness._is_retryable_error(last) is False
    assert answers == []


async def test_the_next_turn_gets_the_full_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex's cat3 row: after [502, 401], a turn whose model answers 502 three times then
    succeeds still succeeds - it starts at attempt 1, not 2."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    answers: list[Any] = [_Transient(_UNMATCHED, _TRANSIENT_REASON), _Transient(_REFUSED, None)]

    async def callback(_model: Model) -> Any:
        if answers:
            raise answers.pop(0)
        return {"apiKey": "relogin-fake", "headers": {}}

    harness, events = _sequenced_harness(callback)
    await harness.prompt("first")
    events.clear()
    keys = _ok_provider(["502", "502", "502", "ok"])

    await harness.prompt("second")

    assert _retry_events(events) == ([1, 2, 3], [(True, 3, None)])
    assert keys == ["relogin-fake"] * 4
    assert harness._retry_attempt == 0


async def test_a_key_less_answer_inside_a_sequence_closes_it_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    _ok_provider(["ok"])
    calls = {"n": 0}

    async def callback(_model: Model) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Transient(_UNMATCHED, _TRANSIENT_REASON)
        return {"apiKey": None, "headers": {}}

    harness, events = _sequenced_harness(callback)
    await harness.prompt("hi")
    text = "get_api_key_and_headers returned neither apiKey nor headers"
    assert _retry_events(events) == ([1], [(False, 1, text)])
    assert harness._retry_attempt == 0


async def test_outside_a_sequence_a_refusal_still_raises() -> None:
    """The first attempt's refusal is today's AgentHarnessError, and opens nothing."""

    async def callback(_model: Model) -> Any:
        raise _Transient(_REFUSED, None)

    harness, events = _sequenced_harness(callback)
    with pytest.raises(AgentHarnessError) as caught:
        await harness.prompt("hi")
    assert str(caught.value) == f"get_api_key_and_headers failed: {_REFUSED}"
    assert _retry_events(events) == ([], [])
    assert harness._retry_attempt == 0


async def test_a_raise_from_the_re_run_closes_the_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any raise out of the retry's re-run (here a provider that throws) still emits
    auto_retry_end(False) once and resets the counter before it propagates."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)

    async def boom(model: Model, context: Context, options: SimpleStreamOptions) -> AsyncIterator[Any]:
        raise RuntimeError("adapter blew up")
        yield  # pragma: no cover - makes this an async generator

    register_provider("anthropic-messages", boom)
    calls = {"n": 0}

    async def callback(_model: Model) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Transient(_UNMATCHED, _TRANSIENT_REASON)
        return {"apiKey": "k-fake", "headers": {}}

    harness, events = _sequenced_harness(callback)
    with pytest.raises(RuntimeError, match="adapter blew up"):
        await harness.prompt("hi")
    assert _retry_events(events) == ([1], [(False, 1, "adapter blew up")])
    assert harness._retry_attempt == 0


async def test_a_cancelled_backoff_closes_the_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """An embedder that cancels ``prompt()`` during the backoff leaves no open sequence."""

    import asyncio

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)

    async def callback(_model: Model) -> Any:
        raise _Transient(_UNMATCHED, _TRANSIENT_REASON)

    harness, events = _sequenced_harness(callback)
    task = asyncio.ensure_future(harness.prompt("hi"))
    for _ in range(200):
        await asyncio.sleep(0)
        if any(isinstance(e, AutoRetryStartEvent) for e in events):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _retry_events(events) == ([1], [(False, 1, "CancelledError")])
    assert harness._retry_attempt == 0


async def test_the_setup_error_message_is_announced_before_it_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review round 2 (event protocol): message_start precedes message_end for the setup
    error, as for a model error - json and rpc print both, the TUI opens and closes it."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    _ok_provider(["ok"])
    calls = {"n": 0}

    async def callback(_model: Model) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Transient(_UNMATCHED, _TRANSIENT_REASON)
        return {"apiKey": "k-fake", "headers": {}}

    harness, events = _sequenced_harness(callback)
    await harness.prompt("hi")
    assistant = [
        (type(e).__name__, getattr(e.message, "stop_reason", None))
        for e in events
        if isinstance(e, MessageStartEvent | MessageEndEvent) and isinstance(e.message, AssistantMessage)
    ]
    assert assistant == [
        ("MessageStartEvent", "error"),
        ("MessageEndEvent", "error"),
        ("MessageStartEvent", None),  # the model's own partial
        ("MessageEndEvent", "stop"),
    ]


# === review round 3: the two guard details verify's V7 and V24 left unpinned =====


async def test_the_counter_reads_zero_when_a_raise_closes_the_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """V24: a subscriber reading the counter during the closing auto_retry_end sees 0 (the
    reset comes before the emit), as the #147 arm guarantees for an end on a message."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)

    async def boom(model: Model, context: Context, options: SimpleStreamOptions) -> AsyncIterator[Any]:
        raise RuntimeError("adapter blew up")
        yield  # pragma: no cover - makes this an async generator

    register_provider("anthropic-messages", boom)
    calls = {"n": 0}

    async def callback(_model: Model) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Transient(_UNMATCHED, _TRANSIENT_REASON)
        return {"apiKey": "k-fake", "headers": {}}

    harness, events = _sequenced_harness(callback)
    seen: list[int] = []
    harness.subscribe(lambda e: seen.append(harness._retry_attempt) if isinstance(e, AutoRetryEndEvent) else None)
    with pytest.raises(RuntimeError, match="adapter blew up"):
        await harness.prompt("hi")
    assert _retry_events(events) == ([1], [(False, 1, "adapter blew up")])
    assert seen == [0]


async def test_a_cancel_during_the_budgets_own_end_emits_no_second_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """V7: the budget ran out, the backoff's own path reset the counter and is emitting its
    auto_retry_end to an async subscriber when the prompt is cancelled. The raise guard finds
    no open sequence and emits nothing: exactly one auto_retry_end."""

    import asyncio

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)

    async def callback(_model: Model) -> Any:
        raise _Transient(_UNMATCHED, _TRANSIENT_REASON)

    harness, events = _sequenced_harness(callback)
    blocked = asyncio.Event()

    async def slow_end_subscriber(event: Any) -> None:
        if isinstance(event, AutoRetryEndEvent) and not blocked.is_set():
            blocked.set()
            await asyncio.Event().wait()  # held until the prompt is cancelled (the first end only)

    harness.subscribe(slow_end_subscriber)
    task = asyncio.ensure_future(harness.prompt("hi"))
    await asyncio.wait_for(blocked.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    starts, ends = _retry_events(events)
    assert starts == [1, 2, 3]
    assert ends == [(False, 3, _UNMATCHED)]
    assert harness._retry_attempt == 0
