"""#409 - compaction summaries, a split turn's prefix summary and branch summaries
retry as the turn does.

pi wraps every compaction and branch-summary request in ``retryAssistantCall`` with
the session's ``settings.retry`` and ``isRetryableAssistantError``
(``compaction/compaction.ts:611-638``, ``agent-session.ts:2749-2772``,
``:3745-3774`` @ fdce48eb4), and its request resolves auth on every call. Before
#409 aelix sent each request once, so a provider's ``502`` or a transient OAuth
refresh failure (#379) failed ``/compact``, the threshold and overflow compactions
and a ``/tree`` summary at once while the turn retried the same failure.

The rows pin: the turn's classification (a provider's error text through
``_is_retryable_error``, an overflow excluded; an auth raise only with a
``retry_reason``; a failed closing auth check never), the turn's settings
(``retry.*`` with the module constants as fallback, ``retry.enabled``, the
harness toggle), the turn's events (``auto_retry_start`` / ``auto_retry_end``),
every entry point (manual, threshold, overflow, split turn, branch summary) and
the aborts (``abort()``, ``abort_retry()``, ``ctx.abort()``) ending a wait at once.
No row waits a real backoff: the delays are a few milliseconds, and the abort
rows configure a minute and finish in well under one second.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    NavigateTreeOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.session.compaction import (
    SUMMARIZATION_SYSTEM_PROMPT,
    TURN_PREFIX_SUMMARIZATION_PROMPT,
    CompactionPreparation,
    prepare_compaction,
)
from aelix_agent_core.session.compaction import compact as session_compact
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai import clear_providers, register_provider
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    Model,
    TextDeltaEvent,
)

_API = "summary-retry-409"
_MODEL = Model(api=_API, id="m", provider="p", context_window=200_000)
_502 = "Error code: 502 - Bad Gateway"
_401 = "Error code: 401 - invalid x-api-key"
_OVERFLOW = "prompt is too long: 250000 tokens > 200000 maximum"
_REFRESH_502 = "OAuth refresh failed for p: (502): Bad Gateway"
_SUMMARY = "## Goal\nthe summary"
#: Long enough that a row which slept through its wait could never pass.
_MINUTE = {"retry": {"maxRetries": 3, "baseDelayMs": 60_000}}


class _AuthFailure(RuntimeError):
    def __init__(self, text: str, retry_reason: Any) -> None:
        super().__init__(text)
        self.retry_reason = retry_reason


@pytest.fixture(autouse=True)
def _providers() -> Any:
    clear_providers()
    yield
    clear_providers()


class _Provider:
    """A registered provider whose every request follows a script.

    ``ok`` answers a summary; ``502`` / ``401`` / ``overflow`` answer an error
    event with that text; ``done-502`` answers a done message with
    ``stop_reason="error"``. A request past the script answers ``ok``. A
    callable step is awaited first (to abort from inside a request), then the
    next step applies.
    """

    def __init__(self, *plan: Any) -> None:
        self.plan = list(plan)
        self.calls = 0
        self.summary_calls = 0
        register_provider(_API, self._stream)

    async def _stream(self, _model: Any, context: Any, options: Any) -> AsyncIterator[Any]:
        self.calls += 1
        if getattr(context, "system_prompt", None) == SUMMARIZATION_SYSTEM_PROMPT:
            self.summary_calls += 1
        step: Any = self.plan.pop(0) if self.plan else "ok"
        while not isinstance(step, str):  # a callable step: await it, then go on
            await step()
            step = self.plan.pop(0) if self.plan else "ok"
        if step == "payload":  # the adapter's own closing check, turned into an error event
            try:
                await options.on_payload({}, _MODEL)
            except Exception as exc:  # noqa: BLE001
                message = AssistantMessage(
                    content=[], stop_reason="error", error_message=f"{_502}: {exc}"
                )
                yield AssistantErrorEvent(reason="error", error=message, error_message=message.error_message)
                return
            step = "ok"
        if step in ("502", "401", "overflow"):
            text = {"502": _502, "401": _401, "overflow": _OVERFLOW}[step]
            message = AssistantMessage(content=[], stop_reason="error", error_message=text)
            yield AssistantErrorEvent(reason="error", error=message, error_message=text)
            return
        if step == "done-502":
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=[], stop_reason="error", error_message=_502),
            )
            return
        assert step == "ok", step
        yield TextDeltaEvent(delta=_SUMMARY)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text=_SUMMARY)], stop_reason="end_turn"),
        )


class _Auth:
    """An auth callback following a script: ``ok``, or an exception to raise."""

    def __init__(self, *plan: Any) -> None:
        self.plan = list(plan)
        self.calls = 0

    async def __call__(self, _model: Any) -> Any:
        self.calls += 1
        step = self.plan.pop(0) if self.plan else "ok"
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, dict):
            return step
        return {"apiKey": "fake-key", "headers": {}}


async def _compactable() -> Session:
    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return session


def _rig(
    session: Session,
    auth: Any = None,
    settings: dict[str, Any] | None = None,
    *,
    manager: bool = True,
    model: Model = _MODEL,
) -> tuple[AgentHarness, list[Any]]:
    sm = SettingsManager.in_memory(
        settings if settings is not None else {"retry": {"baseDelayMs": 1}}
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=model,
            session=session,
            get_api_key_and_headers=auth or _Auth(),
            settings_manager=sm if manager else None,
        )
    )
    events: list[Any] = []
    harness.subscribe(lambda event: events.append(event) or None)
    return harness, events


def _retry_events(events: list[Any]) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for e in events:
        if isinstance(e, AutoRetryStartEvent):
            out.append(("start", e.attempt, e.max_attempts, e.delay_ms, str(e.error_message)))
        elif isinstance(e, AutoRetryEndEvent):
            out.append(("end", e.success, e.attempt, e.final_error))
    return out


async def _compactions(session: Session) -> int:
    return sum(1 for e in await session.get_entries() if e.type == "compaction")


# === manual compaction: the turn's classification ===========================


@pytest.mark.parametrize("failure", ["502", "done-502"])
async def test_a_provider_502_is_retried_and_the_compaction_completes(failure: str) -> None:
    provider = _Provider(failure)
    session = await _compactable()
    harness, events = _rig(session, settings={"retry": {"baseDelayMs": 3}})
    result = await harness.compact()
    assert result.summary.startswith(_SUMMARY)
    assert provider.calls == 2
    assert _retry_events(events) == [("start", 1, 3, 3, _502), ("end", True, 1, None)]
    assert [e.type for e in events if e.type.startswith(("compaction", "auto_retry"))] == [
        "compaction_start",
        "auto_retry_start",
        "auto_retry_end",
        "compaction_end",
    ]
    assert await _compactions(session) == 1


async def test_a_transient_oauth_refresh_failure_is_retried_and_asks_the_callback_again() -> None:
    """#379's raise (``retry_reason`` set) is retried; the retry resolves auth again."""

    provider = _Provider()
    auth = _Auth(_AuthFailure(_REFRESH_502, "the token endpoint answered HTTP 502"))
    harness, events = _rig(await _compactable(), auth)
    await harness.compact()
    assert (auth.calls, provider.calls) == (2, 1)
    assert _retry_events(events) == [("start", 1, 3, 1, _REFRESH_502), ("end", True, 1, None)]


@pytest.mark.parametrize("reason", [None, "", 0, ["x"]], ids=repr)
async def test_an_auth_raise_without_a_retry_reason_still_fails_at_once_unchanged(reason: Any) -> None:
    """The decision is the callback's answer, never the text (#379): ``502`` in a
    refused refresh's words is not retried, and the raise propagates as before."""

    _Provider()
    failure = _AuthFailure("OAuth refresh failed for p: (401): 502 Bad Gateway. Run /login", reason)
    auth = _Auth(failure)
    harness, events = _rig(await _compactable(), auth)
    with pytest.raises(_AuthFailure) as caught:
        await harness.compact()
    assert caught.value is failure
    assert auth.calls == 1
    assert _retry_events(events) == []


@pytest.mark.parametrize(
    ("failure", "text"), [("401", _401), ("overflow", _OVERFLOW)], ids=["401", "overflow"]
)
async def test_a_non_retryable_provider_error_fails_at_once(failure: str, text: str) -> None:
    """A ``401`` is not retried; neither is an overflow, which the turn's classifier
    excludes before its pattern (pi ``_isRetryableError``)."""

    provider = _Provider(failure)
    harness, events = _rig(await _compactable())
    with pytest.raises(AgentHarnessError) as caught:
        await harness.compact()
    assert caught.value.code == "compaction"
    assert str(caught.value) == text
    assert provider.calls == 1
    assert _retry_events(events) == []


async def test_a_failed_closing_auth_check_is_never_retried_whatever_its_text() -> None:
    """#365: the auth snapshot's ``validate`` failing is fatal. An adapter may turn
    that raise into an error event whose text reads retryable; it is not retried."""

    checks = {"n": 0}

    def validate(_model: Any) -> None:
        checks["n"] += 1
        if checks["n"] > 1:  # the up-front check passes, the one before sending fails
            raise RuntimeError("the login changed")

    provider = _Provider("payload")
    auth = _Auth({"apiKey": "fake-key", "headers": {}, "validate": validate})
    harness, events = _rig(await _compactable(), auth)
    with pytest.raises(AgentHarnessError) as caught:
        await harness.compact()
    assert "the login changed" in str(caught.value) and _502 in str(caught.value)
    assert provider.calls == 1
    assert _retry_events(events) == []


async def test_an_empty_summary_is_not_retried() -> None:
    async def empty(_model: Any, _context: Any, _options: Any) -> AsyncIterator[Any]:
        calls.append(1)
        yield AssistantDoneEvent(reason="stop", message=AssistantMessage(content=[], stop_reason="end_turn"))

    calls: list[int] = []
    register_provider(_API, empty)
    harness, events = _rig(await _compactable())
    with pytest.raises(AgentHarnessError, match="empty summary"):
        await harness.compact()
    assert len(calls) == 1 and _retry_events(events) == []


# === the turn's settings ====================================================


async def test_retry_enabled_false_fails_at_once() -> None:
    provider = _Provider("502")
    harness, events = _rig(await _compactable(), settings={"retry": {"enabled": False}})
    with pytest.raises(AgentHarnessError, match="502"):
        await harness.compact()
    assert provider.calls == 1 and _retry_events(events) == []


async def test_the_harness_toggle_governs_when_the_settings_do_not_say() -> None:
    """``set_auto_retry false`` / rpc: ``AgentState.auto_retry_enabled`` (#408)."""

    provider = _Provider("502")
    harness, events = _rig(await _compactable(), manager=False)
    harness._state.auto_retry_enabled = False
    with pytest.raises(AgentHarnessError, match="502"):
        await harness.compact()
    assert provider.calls == 1 and _retry_events(events) == []


async def test_max_retries_bounds_the_sequence_and_the_last_failure_propagates() -> None:
    provider = _Provider("502", "done-502")
    harness, events = _rig(await _compactable(), settings={"retry": {"maxRetries": 1, "baseDelayMs": 2}})
    with pytest.raises(AgentHarnessError, match="502") as caught:
        await harness.compact()
    assert caught.value.code == "compaction"
    assert provider.calls == 2
    assert _retry_events(events) == [("start", 1, 1, 2, _502), ("end", False, 1, _502)]
    assert harness._retry_attempt == 0  # the turn's counter is not the summary's


async def test_max_retries_zero_sends_once_and_emits_nothing() -> None:
    provider = _Provider("502")
    harness, events = _rig(await _compactable(), settings={"retry": {"maxRetries": 0}})
    with pytest.raises(AgentHarnessError):
        await harness.compact()
    assert provider.calls == 1 and _retry_events(events) == []


async def test_the_backoff_doubles_and_is_capped_as_the_turns() -> None:
    _Provider("502", "502", "502")
    harness, events = _rig(
        await _compactable(),
        settings={"retry": {"maxRetries": 3, "baseDelayMs": 2, "maxAgentDelayMs": 5}},
    )
    await harness.compact()
    assert [e[3] for e in _retry_events(events) if e[0] == "start"] == [2, 4, 5]
    assert _retry_events(events)[-1] == ("end", True, 3, None)


async def test_empty_settings_fall_back_to_the_module_constants_read_at_the_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    provider = _Provider("502", "502")
    harness, events = _rig(await _compactable(), settings={})
    with pytest.raises(AgentHarnessError):
        await harness.compact()
    assert provider.calls == 2
    assert _retry_events(events) == [("start", 1, 1, 1, _502), ("end", False, 1, _502)]


# === aborts ===================================================================


def _abort_on_start(harness: AgentHarness, how: str) -> None:
    def listener(event: Any) -> None:
        if isinstance(event, AutoRetryStartEvent):
            loop = asyncio.get_running_loop()
            if how == "abort":
                loop.create_task(harness.abort())
            elif how == "abort_retry":
                loop.call_soon(harness.abort_retry)
            else:  # an extension's ctx.abort()
                loop.call_soon(harness._mark_abort)

    harness.subscribe(listener)


@pytest.mark.parametrize("how", ["abort", "abort_retry", "ctx.abort"])
async def test_an_abort_during_the_wait_ends_it_at_once_and_nothing_is_sent_again(how: str) -> None:
    provider = _Provider("502")
    harness, events = _rig(await _compactable(), settings=_MINUTE)
    _abort_on_start(harness, how)
    started = time.monotonic()
    with pytest.raises(AgentHarnessError, match="502"):
        await asyncio.wait_for(harness.compact(), timeout=10)
    assert time.monotonic() - started < 5  # a minute's wait was not slept through
    assert provider.calls == 1
    assert _retry_events(events) == [
        ("start", 1, 3, 60_000, _502),
        ("end", False, 1, "Retry cancelled"),
    ]
    assert harness._retry_abort_event is None
    assert harness._phase == "idle"


async def test_an_abort_made_while_the_request_was_failing_stops_the_retry() -> None:
    """An abort that lands before the retry is decided is not lost (the turn reads
    ``_abort_requested`` there): the failure propagates, nothing is sent again."""

    session = await _compactable()
    harness, events = _rig(session, settings=_MINUTE)
    provider = _Provider(harness.abort, "502")
    with pytest.raises(AgentHarnessError, match="502"):
        await asyncio.wait_for(harness.compact(), timeout=10)
    assert provider.calls == 1 and _retry_events(events) == []


async def test_under_a_prompts_claim_an_earlier_abort_of_that_prompt_stops_the_retry() -> None:
    """A threshold compaction runs inside the prompt: an abort of THAT prompt made
    before the summary request (here an extension's ``ctx.abort()`` from a
    ``compaction_start`` subscriber) counts, as the turn's retry reads it."""

    provider = _TurnAndSummary(["big"], ["502"])
    harness, events = _rig(await _compactable(), model=_SMALL_WINDOW)

    def on_compaction_start(event: Any) -> None:
        if getattr(event, "type", None) == "compaction_start":
            harness._mark_abort()

    harness.subscribe(on_compaction_start)
    with pytest.raises(AgentHarnessError, match="502"):
        await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert provider.calls == {"turn": 1, "summary": 1}
    assert _retry_events(events) == []


async def test_a_finished_prompts_abort_does_not_cancel_a_later_compactions_retry() -> None:
    """``_abort_requested`` is cleared only by the next ``_run``: an abort of a prompt
    that already returned must not read as aborting this compaction's wait."""

    provider = _Provider("502")
    harness, events = _rig(await _compactable())
    harness._abort_requested = True  # what an aborted, finished prompt leaves behind
    harness._state.retry_aborted = True
    await harness.compact()
    assert provider.calls == 2
    assert _retry_events(events) == [("start", 1, 3, 1, _502), ("end", True, 1, None)]


async def test_the_abort_event_is_released_when_the_compaction_ends() -> None:
    _Provider("502")
    harness, _ = _rig(await _compactable())
    seen: list[Any] = []
    harness.subscribe(lambda e: seen.append(harness._retry_abort_event) if isinstance(e, AutoRetryStartEvent) else None)
    await harness.compact()
    assert seen and seen[0] is not None
    assert harness._retry_abort_event is None


# === entry points =============================================================


class _TurnAndSummary:
    """A provider for prompt() rows: turn requests and summary requests apart."""

    def __init__(self, turn: list[str], summary: list[str]) -> None:
        self.turn, self.summary = list(turn), list(summary)
        self.calls = {"turn": 0, "summary": 0}
        register_provider(_API, self._stream)

    async def _stream(self, _model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        is_summary = getattr(context, "system_prompt", None) == SUMMARIZATION_SYSTEM_PROMPT
        key = "summary" if is_summary else "turn"
        self.calls[key] += 1
        plan = self.summary if is_summary else self.turn
        step = plan.pop(0) if plan else "ok"
        if step in ("502", "overflow"):
            text = _502 if step == "502" else _OVERFLOW
            message = AssistantMessage(content=[], stop_reason="error", error_message=text)
            yield AssistantErrorEvent(reason="error", error=message, error_message=text)
            return
        usage = {"total_tokens": 19_500} if step == "big" else {"total_tokens": 10}
        text = _SUMMARY if is_summary else "turn reply"
        yield TextDeltaEvent(delta=text)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text=text)], stop_reason="end_turn", usage=usage),
        )


_SMALL_WINDOW = Model(api=_API, id="m", provider="p", context_window=20_000)


async def test_threshold_auto_compaction_in_a_prompt_retries_its_summary() -> None:
    provider = _TurnAndSummary(["big"], ["502"])
    session = await _compactable()
    harness, events = _rig(session, model=_SMALL_WINDOW)
    await harness.prompt("go")  # before #409: raised the summary's 502 after a good turn
    assert provider.calls == {"turn": 1, "summary": 2}
    assert await _compactions(session) == 1
    assert _retry_events(events) == [("start", 1, 3, 1, _502), ("end", True, 1, None)]


async def test_overflow_recovery_retries_its_summary_and_re_runs_the_turn() -> None:
    provider = _TurnAndSummary(["overflow", "ok"], ["502"])
    session = await _compactable()
    harness, events = _rig(session, model=_SMALL_WINDOW)
    await harness.prompt("go")  # before #409: no compaction, no re-run
    assert provider.calls == {"turn": 2, "summary": 2}
    assert await _compactions(session) == 1
    last = [m for m in harness._state.messages if isinstance(m, AssistantMessage)][-1]
    assert last.stop_reason == "end_turn"
    assert _retry_events(events) == [("start", 1, 3, 1, _502), ("end", True, 1, None)]


async def _split_turn_session() -> Session:
    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="y" * 30_000)]))
    await session.append_message(
        AssistantMessage(content=[TextContent(text="z" * 30_000)], stop_reason="end_turn")
    )
    await session.append_message(UserMessage(content=[TextContent(text="x" * 4_000)]))
    for _ in range(4):
        await session.append_message(
            AssistantMessage(content=[TextContent(text="w" * 30_000)], stop_reason="end_turn")
        )
    prep = prepare_compaction(await session.get_branch())
    assert prep is not None and prep.is_split_turn and prep.messages_to_summarize
    return session


async def test_a_split_turns_summaries_each_retry() -> None:
    provider = _Provider("502", "502")  # both concurrent first requests fail
    harness, events = _rig(await _split_turn_session())
    result = await harness.compact()
    assert provider.calls == 4
    assert "**Turn Context (split turn):**" in result.summary
    assert sorted(e for e in _retry_events(events) if e[0] == "end") == [
        ("end", True, 1, None),
        ("end", True, 1, None),
    ]


async def test_a_split_turn_fails_at_once_when_one_summary_fails_and_the_other_waits() -> None:
    """The sibling in its wait is cancelled, closes its sequence and sends nothing
    after the compaction failed (plain ``gather`` left it retrying)."""

    provider = _Provider("401", "502")  # history: a 401; turn prefix: a 502, a minute's wait
    harness, events = _rig(await _split_turn_session(), settings=_MINUTE)
    started = time.monotonic()
    with pytest.raises(AgentHarnessError, match="401"):
        await asyncio.wait_for(harness.compact(), timeout=10)
    assert time.monotonic() - started < 5
    await asyncio.sleep(0.05)
    assert provider.calls == 2
    retry = _retry_events(events)
    assert retry[0] == ("start", 1, 3, 60_000, _502)
    assert retry[1][:3] == ("end", False, 1)
    assert len(retry) == 2
    assert [e.type for e in events if e.type.startswith("compaction")][-1] == "compaction_end"


class _SplitProvider:
    """A split turn's two requests told apart by their prompt, each with its own script.

    A step is ``ok``, ``502``, ``401``, ``done-502``, ``hold`` (stay in flight until
    cancelled) or an ``asyncio.Event`` to wait for before taking the next step.
    """

    def __init__(self, history: list[Any], prefix: list[Any]) -> None:
        self.plans = {"history": list(history), "prefix": list(prefix)}
        self.calls = {"history": 0, "prefix": 0}
        self.prefix_retry_started = asyncio.Event()
        register_provider(_API, self._stream)

    async def _stream(self, _model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        text = context.messages[0].content[0].text
        key = "prefix" if TURN_PREFIX_SUMMARIZATION_PROMPT in text else "history"
        self.calls[key] += 1
        if key == "prefix" and self.calls[key] > 1:
            self.prefix_retry_started.set()
        plan = self.plans[key]
        step: Any = plan.pop(0) if plan else "ok"
        while isinstance(step, asyncio.Event):
            await step.wait()
            step = plan.pop(0) if plan else "ok"
        if step == "hold":
            await asyncio.Event().wait()
        if step in ("502", "401"):
            error = _502 if step == "502" else _401
            message = AssistantMessage(content=[], stop_reason="error", error_message=error)
            yield AssistantErrorEvent(reason="error", error=message, error_message=error)
            return
        if step == "done-502":
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=[], stop_reason="error", error_message=_502),
            )
            return
        yield TextDeltaEvent(delta=_SUMMARY)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text=_SUMMARY)], stop_reason="end_turn"),
        )


async def test_a_split_turns_prefix_summary_retries_an_error_in_its_done_message() -> None:
    provider = _SplitProvider(history=["ok"], prefix=["done-502"])
    harness, events = _rig(await _split_turn_session())
    await harness.compact()
    assert provider.calls == {"history": 1, "prefix": 2}
    assert _retry_events(events) == [("start", 1, 3, 1, _502), ("end", True, 1, None)]


async def test_a_sibling_cancelled_while_its_retried_request_is_in_flight_closes_its_sequence() -> None:
    """The cancel lands in the retried REQUEST, not in the wait: the sequence still
    ends with one ``auto_retry_end`` (the TUI's countdown and interrupt handler are
    restored on it), as #379 review round 2 closes the turn's on a raise."""

    provider = _SplitProvider(history=[], prefix=["502", "hold"])
    provider.plans["history"] = [provider.prefix_retry_started, "401"]
    harness, events = _rig(await _split_turn_session())
    with pytest.raises(AgentHarnessError, match="401"):
        await asyncio.wait_for(harness.compact(), timeout=10)
    assert provider.calls == {"history": 1, "prefix": 2}
    retry = _retry_events(events)
    assert retry[0] == ("start", 1, 3, 1, _502)
    assert retry[1][:3] == ("end", False, 1)
    assert len(retry) == 2


async def test_the_session_api_without_a_retry_sends_each_request_once() -> None:
    """``session.compaction.compact`` keeps its old contract for other callers."""

    provider = _Provider("502")
    prep = CompactionPreparation(
        first_kept_entry_id="e",
        messages_to_summarize=[UserMessage(content=[TextContent(text="history")])],
        tokens_before=1,
    )
    with pytest.raises(AgentHarnessError, match="502"):
        await session_compact(_MODEL, _Auth(), prep)
    assert provider.calls == 1


async def _branched() -> tuple[Session, str]:
    session = Session(MemorySessionStorage())
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    await session.append_message(
        AssistantMessage(content=[TextContent(text="branch a reply")], stop_reason="end_turn")
    )
    return session, root


async def test_a_branch_summary_retries_and_is_stored() -> None:
    provider = _Provider("502")
    session, root = await _branched()
    harness, events = _rig(session)
    result = await harness.navigate_tree(root, NavigateTreeOptions(summarize=True))
    assert provider.calls == 2
    assert result.summary_entry is not None and _SUMMARY in result.summary_entry.summary
    assert _retry_events(events) == [("start", 1, 3, 1, _502), ("end", True, 1, None)]
    assert harness._retry_abort_event is None


async def test_a_branch_summary_refresh_failure_is_retried() -> None:
    provider = _Provider()
    auth = _Auth(_AuthFailure(_REFRESH_502, "the token endpoint could not be reached (ConnectError)"))
    session, root = await _branched()
    harness, events = _rig(session, auth)
    await harness.navigate_tree(root, NavigateTreeOptions(summarize=True))
    assert (auth.calls, provider.calls) == (2, 1)
    assert _retry_events(events)[-1] == ("end", True, 1, None)


@pytest.mark.parametrize("how", ["abort", "abort_retry"])
async def test_an_abort_during_a_branch_summarys_wait_ends_it_at_once(how: str) -> None:
    provider = _Provider("502")
    session, root = await _branched()
    harness, events = _rig(session, settings=_MINUTE)
    _abort_on_start(harness, how)
    started = time.monotonic()
    with pytest.raises(AgentHarnessError, match="502"):
        await asyncio.wait_for(harness.navigate_tree(root, NavigateTreeOptions(summarize=True)), timeout=10)
    assert time.monotonic() - started < 5
    assert provider.calls == 1
    assert _retry_events(events)[-1] == ("end", False, 1, "Retry cancelled")
    assert harness._phase == "idle" and harness._retry_abort_event is None


# === review round 2 ===========================================================


async def test_a_split_turns_two_summaries_each_have_their_own_budget() -> None:
    """Each request gets its own retry budget, as pi's ``completeSummarization``
    gives each call its own: with ``maxRetries: 1`` both concurrent summaries
    retry their one ``502`` (a budget shared by the compaction's requests fails
    the second one at once and the compaction with it)."""

    provider = _SplitProvider(history=["502"], prefix=["502"])
    harness, events = _rig(
        await _split_turn_session(), settings={"retry": {"maxRetries": 1, "baseDelayMs": 1}}
    )
    result = await harness.compact()
    assert "**Turn Context (split turn):**" in result.summary
    assert provider.calls == {"history": 2, "prefix": 2}
    retry = _retry_events(events)
    assert sorted(e for e in retry if e[0] == "start") == [("start", 1, 1, 1, _502)] * 2
    assert sorted(e for e in retry if e[0] == "end") == [("end", True, 1, None)] * 2


async def test_a_branch_summary_retries_an_error_in_its_done_message() -> None:
    provider = _Provider("done-502")
    session, root = await _branched()
    harness, events = _rig(session)
    result = await harness.navigate_tree(root, NavigateTreeOptions(summarize=True))
    assert provider.calls == 2
    assert result.summary_entry is not None and _SUMMARY in result.summary_entry.summary
    assert _retry_events(events) == [("start", 1, 3, 1, _502), ("end", True, 1, None)]


async def test_the_policy_is_read_at_each_failure_so_a_settings_change_during_the_wait_applies() -> None:
    """``_auto_retry_policy`` is read at each failure, as the turn reads it (#408: a
    ``/reload`` of an edited settings.json applies to the next retry): ``retry.enabled``
    turned off in the settings during the first wait stops the next retry (a policy
    read once per compaction kept retrying; the harness toggle is left on)."""

    provider = _Provider("502", "502")
    harness, events = _rig(await _compactable())
    settings = harness._settings_manager
    assert settings is not None

    def on_start(event: Any) -> None:
        if isinstance(event, AutoRetryStartEvent):
            settings.set_retry_enabled(False)

    harness.subscribe(on_start)
    with pytest.raises(AgentHarnessError, match="502"):
        await harness.compact()
    assert provider.calls == 2
    assert _retry_events(events) == [("start", 1, 3, 1, _502), ("end", False, 1, _502)]


class _ShapedProvider:
    """Every request's first answer is one error-event shape; the next is a summary
    (or a turn reply). Turn requests and summary requests count apart."""

    def __init__(self, shape: str) -> None:
        self.shape = shape
        self.calls = {"turn": 0, "summary": 0}
        register_provider(_API, self._stream)

    async def _stream(self, _model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        key = "summary" if getattr(context, "system_prompt", None) == SUMMARIZATION_SYSTEM_PROMPT else "turn"
        self.calls[key] += 1
        if self.calls[key] == 1:
            if self.shape == "pi-shaped-502":  # pi's {type: "error", reason, error}: no top-level text
                message = AssistantMessage(content=[], stop_reason="error", error_message=_502)
                yield AssistantErrorEvent(reason="error", error=message)
                return
            if self.shape == "pi-shaped-401":
                message = AssistantMessage(content=[], stop_reason="error", error_message=_401)
                yield AssistantErrorEvent(reason="error", error=message)
                return
            if self.shape == "aborted":  # an aborted stream; "terminated" reads retryable as text
                message = AssistantMessage(content=[], stop_reason="aborted", error_message="terminated")
                yield AssistantErrorEvent(reason="aborted", error=message, error_message="terminated")
                return
        text = _SUMMARY if key == "summary" else "turn reply"
        yield TextDeltaEvent(delta=text)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[TextContent(text=text)], stop_reason="end_turn"),
        )


@pytest.mark.parametrize(
    ("shape", "retried", "text"),
    [("pi-shaped-502", True, _502), ("pi-shaped-401", False, None), ("aborted", False, None)],
    ids=["pi-shaped-502", "pi-shaped-401", "aborted"],
)
async def test_the_summarys_decision_is_the_turns_for_the_same_error_event(
    shape: str, retried: bool, text: str | None
) -> None:
    """The decision reads the message the turn's loop classifies (an error event's
    ``error``), not the event's top-level text: a ``502`` in pi's event shape (no
    top-level ``error_message``) is retried by both, an aborted stream by neither
    (its text, ``terminated``, matches the pattern - only ``stop_reason`` says no).
    A request that is not retried raises what it raised before #409."""

    turn_provider = _ShapedProvider(shape)
    turn_harness, turn_events = _rig(Session(MemorySessionStorage()))
    await turn_harness.prompt("hello")
    turn_retried = any(isinstance(e, AutoRetryStartEvent) for e in turn_events)
    assert turn_provider.calls["turn"] == (2 if retried else 1)
    clear_providers()

    provider = _ShapedProvider(shape)
    harness, events = _rig(await _compactable())
    if retried:
        result = await harness.compact()
        assert result.summary.startswith(_SUMMARY)
        assert _retry_events(events) == [("start", 1, 3, 1, text), ("end", True, 1, None)]
    else:
        expected = "terminated" if shape == "aborted" else "summarization failed"
        with pytest.raises(AgentHarnessError) as caught:
            await harness.compact()
        assert str(caught.value) == expected
        assert _retry_events(events) == []
    summary_retried = any(isinstance(e, AutoRetryStartEvent) for e in events)
    assert summary_retried is turn_retried is retried
    assert provider.calls["summary"] == (2 if retried else 1)


@pytest.mark.parametrize("how", ["abort", "abort_retry", "ctx.abort"])
async def test_a_zero_ms_wait_still_yields_so_an_abort_from_the_start_subscriber_counts(how: str) -> None:
    """``retry.baseDelayMs: 0`` asks for no wait at all; the wait still yields once,
    so an abort a subscriber of ``auto_retry_start`` schedules (as an rpc
    ``abort_retry`` read by its reader task lands) is not lost (#408 round 2)."""

    provider = _Provider("502")
    harness, events = _rig(await _compactable(), settings={"retry": {"maxRetries": 3, "baseDelayMs": 0}})
    _abort_on_start(harness, how)
    with pytest.raises(AgentHarnessError, match="502"):
        await asyncio.wait_for(harness.compact(), timeout=10)
    assert provider.calls == 1
    assert _retry_events(events) == [("start", 1, 3, 0, _502), ("end", False, 1, "Retry cancelled")]
