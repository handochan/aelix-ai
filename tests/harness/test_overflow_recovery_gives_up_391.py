"""#391 - an overflow recovery that does not re-run the turn keeps its failed answer.

The overflow recovery (``AgentHarness._try_overflow_recovery``) drops the failed answer
from the live context before its compaction, so that the re-run does not send it again
(pi ``agent-session.ts:1862-1867``). When no re-run followed - nothing to compact, the
compaction failed, an extension cancelled it, a hook raised, the prompt was aborted or
cancelled during it - the answer stayed dropped: the live context ended on the user's
message while the session still held the answer, so ``-p`` printed nothing and exited 0
(``print_mode._final_failure`` judges the live context's last message). pi leaves it
omitted too (``_omitRecoveryAttempt`` @ fdce48eb4); aelix now puts it back.

The rows pin: every way out without a re-run ends with the failed answer as the live
context's last message, once, the same object the session holds; a ``BaseException``
still propagates (with the answer back); and the two controls - a recovered re-run and
a re-run that overflows again - do not get the first answer back.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    _drop_failed_answer,
    _restore_failed_answer,
)
from aelix_agent_core.harness.hooks import SessionBeforeCompactResult
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.session.compaction import SUMMARIZATION_SYSTEM_PROMPT
from aelix_agent_core.types import AgentState, CompactionEndEvent
from aelix_ai import clear_providers, register_provider
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import AssistantDoneEvent, AssistantErrorEvent, Model, TextDeltaEvent

_API = "overflow-recovery-gives-up-391"
_MODEL = Model(api=_API, id="m", provider="p", context_window=20_000)
_OVERFLOW = "prompt is too long: 250000 tokens > 200000 maximum"
_502 = "Error code: 502 - Bad Gateway"
_400 = "Error code: 400 - summary refused"
_ANSWER = "the answer is 42"
_SUMMARY = "## Goal\nthe summary"
_FAST = {"retry": {"maxRetries": 2, "baseDelayMs": 1}}


@pytest.fixture(autouse=True)
def _providers() -> Any:
    clear_providers()
    yield
    clear_providers()


class _Provider:
    """Turn and summary requests, each following its own script.

    A turn step is ``overflow`` (the provider refuses the request as too long),
    ``ok`` (an answer) or ``big`` (an answer reporting 15000 tokens); a summary step is ``ok``, ``502``, ``400`` or ``park`` (waits on
    :attr:`release` - a request the test cancels). Past its script a plan repeats its
    last step.
    """

    def __init__(self, turn: list[str], summary: list[str]) -> None:
        self.turn, self.summary = list(turn), list(summary)
        self.calls = {"turn": 0, "summary": 0}
        self.parked = asyncio.Event()
        self.release = asyncio.Event()
        register_provider(_API, self._stream)

    async def _stream(self, _model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        is_summary = getattr(context, "system_prompt", None) == SUMMARIZATION_SYSTEM_PROMPT
        key = "summary" if is_summary else "turn"
        self.calls[key] += 1
        plan = self.summary if is_summary else self.turn
        step = (plan.pop(0) if len(plan) > 1 else plan[0]) if plan else "ok"
        if step == "park":
            self.parked.set()
            await self.release.wait()
            step = "ok"
        if step in ("502", "400", "overflow"):
            text = {"502": _502, "400": _400, "overflow": _OVERFLOW}[step]
            message = AssistantMessage(content=[], stop_reason="error", error_message=text)
            yield AssistantErrorEvent(reason="error", error=message, error_message=text)
            return
        text = _SUMMARY if is_summary else _ANSWER
        tokens = 15_000 if step == "big" else 10
        yield TextDeltaEvent(delta=text)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text=text)],
                stop_reason="end_turn",
                usage={"total_tokens": tokens},
            ),
        )


class _Auth:
    """An auth callback: ``ok`` for the first ``ok`` calls, then raises ``failure``."""

    def __init__(self, ok: int, failure: BaseException | None = None) -> None:
        self.ok, self.failure, self.calls = ok, failure, 0

    async def __call__(self, _model: Any) -> Any:
        self.calls += 1
        if self.failure is not None and self.calls > self.ok:
            raise self.failure
        return {"apiKey": "fake-key", "headers": {}}


async def _compactable() -> Session:
    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return session


def _rig(
    session: Session, auth: Any = None, settings: dict[str, Any] | None = None
) -> tuple[AgentHarness, list[Any]]:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=_MODEL,
            session=session,
            get_api_key_and_headers=auth or _Auth(ok=10**6),
            settings_manager=SettingsManager.in_memory(settings or _FAST),
        )
    )
    events: list[Any] = []
    harness.subscribe(lambda event: events.append(event) or None)
    return harness, events


def _ends(events: list[Any]) -> list[tuple[str, bool, bool, str | None]]:
    return [
        (e.reason, e.aborted, e.will_retry, e.error_message)
        for e in events
        if isinstance(e, CompactionEndEvent)
    ]


def _overflows(harness: AgentHarness) -> list[AssistantMessage]:
    return [
        m
        for m in harness._state.messages
        if isinstance(m, AssistantMessage) and m.error_message == _OVERFLOW
    ]


async def _session_last_message(session: Session) -> Any:
    return [e for e in await session.get_branch() if e.type == "message"][-1].message


async def _assert_failed_answer_is_back(harness: AgentHarness, session: Session) -> None:
    """The live context ends on the overflow answer, once, the object the session holds."""

    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage)
    assert (last.stop_reason, last.error_message) == ("error", _OVERFLOW)
    assert len(_overflows(harness)) == 1
    assert last is await _session_last_message(session)


# === every way out without a re-run =============================================


async def test_nothing_to_compact_keeps_the_failed_answer() -> None:
    """The issue's case: the session's first turn overflows, so there is no cut to make."""

    provider = _Provider(["overflow"], ["ok"])
    session = Session(MemorySessionStorage())
    harness, events = _rig(session)
    await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert provider.calls == {"turn": 1, "summary": 0}
    assert _ends(events) == [("overflow", False, False, "Nothing to compact")]
    await _assert_failed_answer_is_back(harness, session)  # before #391: ends on "go"
    assert harness.phase == "idle"


@pytest.mark.parametrize(
    ("summary", "calls", "end"),
    [
        (["502"], 3, (False, f"Context overflow recovery failed: {_502}")),
        (["400"], 1, (False, f"Context overflow recovery failed: {_400}")),
    ],
    ids=["summary-502-past-its-retries", "summary-400"],
)
async def test_a_compaction_that_fails_keeps_the_failed_answer(
    summary: list[str], calls: int, end: tuple[bool, str]
) -> None:
    provider = _Provider(["overflow"], summary)
    session = await _compactable()
    harness, events = _rig(session)
    await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert provider.calls == {"turn": 1, "summary": calls}
    assert _ends(events) == [("overflow", end[0], False, end[1])]
    await _assert_failed_answer_is_back(harness, session)


async def test_an_auth_callback_that_raises_for_the_summary_keeps_the_failed_answer() -> None:
    _Provider(["overflow"], ["ok"])
    auth = _Auth(ok=1, failure=RuntimeError("no credential for p (probe)"))
    session = await _compactable()
    harness, events = _rig(session, auth)
    await harness.prompt("go")
    assert _ends(events) == [
        ("overflow", False, False, "Context overflow recovery failed: no credential for p (probe)")
    ]
    await _assert_failed_answer_is_back(harness, session)


async def test_an_extensions_cancel_keeps_the_failed_answer() -> None:
    provider = _Provider(["overflow"], ["ok"])
    session = await _compactable()
    harness, events = _rig(session)

    async def cancel(_event: Any, _ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=True)

    harness.hooks.on("session_before_compact", cancel)
    await harness.prompt("go")
    assert provider.calls == {"turn": 1, "summary": 0}
    assert _ends(events) == [("overflow", True, False, None)]
    await _assert_failed_answer_is_back(harness, session)


async def test_a_session_before_compact_hook_that_raises_keeps_the_failed_answer() -> None:
    _Provider(["overflow"], ["ok"])
    session = await _compactable()
    harness, events = _rig(session)

    async def boom(_event: Any, _ctx: Any) -> None:
        raise RuntimeError("hook went wrong (probe)")

    harness.hooks.on("session_before_compact", boom)
    await harness.prompt("go")
    assert [e[3] for e in _ends(events)] == [
        "Context overflow recovery failed: session_before_compact hook handler raised: "
        "hook went wrong (probe)"
    ]
    await _assert_failed_answer_is_back(harness, session)


async def test_a_session_compact_hook_that_raises_after_the_rebuild_adds_no_second_copy() -> None:
    """The compaction was written and the live context rebuilt from the branch, which
    holds the answer, before the hook raised: it is there once, not twice."""

    provider = _Provider(["overflow"], ["ok"])
    session = await _compactable()
    harness, _ = _rig(session)

    async def boom(_event: Any, _ctx: Any) -> None:
        raise RuntimeError("after the write (probe)")

    harness.hooks.on("session_compact", boom)
    await harness.prompt("go")
    assert provider.calls == {"turn": 1, "summary": 1}
    assert any(e.type == "compaction" for e in await session.get_entries())
    await _assert_failed_answer_is_back(harness, session)


async def test_an_abort_during_a_compaction_that_succeeds_keeps_the_failed_answer() -> None:
    """The compaction is written, the abort stops the re-run: before #391 the live
    context ended on the user's message (the answer had been dropped twice)."""

    provider = _Provider(["overflow", "ok"], ["ok"])
    session = await _compactable()
    harness, events = _rig(session)

    async def abort(_event: Any, ctx: Any) -> None:
        ctx.abort()

    harness.hooks.on("session_compact", abort)
    await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert provider.calls == {"turn": 1, "summary": 1}  # no re-run
    assert _ends(events) == [("overflow", False, True, None)]
    await _assert_failed_answer_is_back(harness, session)
    assert harness.phase == "idle"


async def test_a_cancelled_prompt_still_raises_with_the_failed_answer_back() -> None:
    """A ``BaseException`` during the compaction (the prompt task cancelled while the
    summary request runs) propagates as before; the live context has the answer."""

    provider = _Provider(["overflow"], ["park"])
    session = await _compactable()
    harness, _ = _rig(session)
    task = asyncio.ensure_future(harness.prompt("go"))
    await asyncio.wait_for(provider.parked.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    await _assert_failed_answer_is_back(harness, session)
    assert harness.phase == "idle"


async def test_a_base_exception_out_of_the_compaction_still_propagates() -> None:
    class _Stop(BaseException):
        pass

    _Provider(["overflow"], ["ok"])
    session = await _compactable()
    harness, _ = _rig(session)

    async def stop(*_args: Any, **_kwargs: Any) -> Any:
        raise _Stop

    harness.compact = stop  # type: ignore[method-assign]
    with pytest.raises(_Stop):
        await harness.prompt("go")
    await _assert_failed_answer_is_back(harness, session)


# === review round 2: the overflow case owns the answer it could not recover ======

#: The threshold is 20000 - 1000 = 19000: a 15000-token answer is under it, and
#: that answer's usage plus a 20000-character prompt (about 5000 tokens) is over it.
_NEAR = {**_FAST, "compaction": {"reserveTokens": 1_000}}


@pytest.mark.parametrize(
    ("summary", "calls"),
    [(["400"], 1), (["502"], 3)],
    ids=["summary-400", "summary-502-past-its-retries"],
)
async def test_a_failed_overflow_recovery_runs_no_threshold_compaction_after_it(
    summary: list[str], calls: int
) -> None:
    """Review round 2 (the verify and the cross review): with the failed answer back,
    the threshold check that follows in the same prompt estimated the context from the
    last good usage plus the long prompt, tripped and ran a second compaction straight
    away - a second summary request with its own retries, a second
    ``session_before_compact``, and a second failure line before the provider's error.
    pi's ``_checkCompaction`` returns from its overflow case and never reaches its
    threshold case (``agent-session.ts:3040-3077`` @ fdce48eb4)."""

    provider = _Provider(["big", "overflow"], summary)
    session = await _compactable()
    harness, events = _rig(session, settings=_NEAR)
    await asyncio.wait_for(harness.prompt("seed"), timeout=10)
    assert _ends(events) == []  # the seed answer is under the threshold
    await asyncio.wait_for(harness.prompt("x" * 20_000), timeout=10)
    assert provider.calls == {"turn": 2, "summary": calls}  # c64bec3f: 2 * calls
    assert [(e[0], e[3]) for e in _ends(events)] == [
        ("overflow", f"Context overflow recovery failed: {summary[0] == '400' and _400 or _502}")
    ]
    await _assert_failed_answer_is_back(harness, session)


async def test_an_extensions_cancel_of_the_overflow_recovery_is_not_asked_again() -> None:
    """The second compaction asked ``session_before_compact`` a second time."""

    asked: list[str] = []
    _Provider(["big", "overflow"], ["ok"])
    session = await _compactable()
    harness, events = _rig(session, settings=_NEAR)

    async def cancel(event: Any, _ctx: Any) -> SessionBeforeCompactResult:
        asked.append(getattr(event, "reason", "?"))
        return SessionBeforeCompactResult(cancel=True)

    harness.hooks.on("session_before_compact", cancel)
    await harness.prompt("seed")
    await harness.prompt("x" * 20_000)
    assert len(asked) == 1  # c64bec3f: 2 (overflow, then threshold)
    assert _ends(events) == [("overflow", True, False, None)]
    await _assert_failed_answer_is_back(harness, session)


async def test_the_threshold_check_still_runs_when_the_last_answer_is_not_an_overflow() -> None:
    """Control: a good answer past the threshold is compacted in the same prompt."""

    provider = _Provider(["big", "big"], ["ok"])
    session = await _compactable()
    harness, events = _rig(session, settings={**_FAST, "compaction": {"reserveTokens": 6_000}})
    await harness.prompt("seed")  # 15000 > 20000 - 6000
    assert provider.calls["summary"] == 1
    assert [e[0] for e in _ends(events)] == ["threshold"]


# === controls: a re-run happened ==================================================


async def test_a_recovered_turn_ends_on_its_answer_without_the_overflow() -> None:
    provider = _Provider(["overflow", "ok"], ["ok"])
    session = await _compactable()
    harness, events = _rig(session)
    await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert provider.calls == {"turn": 2, "summary": 1}
    assert _ends(events) == [("overflow", False, True, None)]
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.content[0].text == _ANSWER  # type: ignore[union-attr]
    assert _overflows(harness) == []


async def test_a_rerun_that_overflows_again_ends_on_the_second_overflow_only() -> None:
    provider = _Provider(["overflow"], ["ok"])
    session = await _compactable()
    harness, _ = _rig(session)
    await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert provider.calls == {"turn": 2, "summary": 1}
    overflows = _overflows(harness)
    assert len(overflows) == 1  # the first one is not put back beside the second
    assert harness._state.messages[-1] is overflows[0]
    assert overflows[0] is await _session_last_message(session)


# === the two helpers ===============================================================


def _state(*messages: Any) -> AgentState:
    state = AgentState()
    state.messages.extend(messages)
    return state


def _error(text: str = _OVERFLOW) -> AssistantMessage:
    return AssistantMessage(content=[], stop_reason="error", error_message=text)


def test_drop_takes_only_a_trailing_assistant_message() -> None:
    user, failed = UserMessage(content=[TextContent(text="go")]), _error()
    state = _state(user, failed)
    assert _drop_failed_answer(state) is failed
    assert state.messages == [user]
    assert _drop_failed_answer(state) is None
    assert state.messages == [user]
    assert _drop_failed_answer(_state()) is None


def test_restore_puts_it_back_only_when_the_context_does_not_end_on_it() -> None:
    user, failed = UserMessage(content=[TextContent(text="go")]), _error()
    state = _state(user)
    _restore_failed_answer(state, failed, 20_000)
    assert state.messages == [user, failed]
    _restore_failed_answer(state, failed, 20_000)  # already there: no second copy
    assert state.messages == [user, failed]
    copy = _error()  # a rebuild that loaded the answer as another object
    state = _state(user, copy)
    _restore_failed_answer(state, failed, 20_000)
    assert state.messages == [user, copy]
    state = _state(user, _error("Error code: 502 - Bad Gateway"))  # not an overflow
    _restore_failed_answer(state, failed, 20_000)
    assert state.messages[-1] is failed
    state = _state(user)
    _restore_failed_answer(state, None, 20_000)
    assert state.messages == [user]
