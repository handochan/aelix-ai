"""#546 - an automatic compaction that fails is told on ``compaction_end``, never raised.

pi's ``_runAutoCompaction`` (``agent-session.ts:3127-3274`` @ fdce48eb4) catches every
failure of a threshold or overflow compaction (``:3242-3267``): it emits
``compaction_end`` with ``errorMessage: "Auto-compaction failed: <message>"``
(``Context overflow recovery failed: <message>`` for an overflow), or with
``aborted: true`` and no message when the compaction was aborted or an extension
cancelled it, and returns ``false`` - the turn keeps its result. Before #546 aelix
raised the failure out of ``prompt()`` after the turn had succeeded: the threshold
check re-raised every ``AgentHarnessError`` but "Nothing to compact"
(``harness/core.py`` ``_check_auto_compaction``), and neither it nor the overflow
recovery caught anything else (an auth callback's own raise). ``-p`` then printed
the error instead of the answer and exited 1.

The rows pin: the threshold path past its retries, a non-retried provider error, a
raising auth callback, an extension's cancel and an abort of the prompt (none raises;
each ``compaction_end`` reads as pi's); "Nothing to compact" (still its own words);
both overflow branches with a raising auth callback; a manual compaction (raised and
reported as before); and a ``BaseException`` (still propagates).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.harness.hooks import SessionBeforeCompactResult
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.session.compaction import SUMMARIZATION_SYSTEM_PROMPT
from aelix_agent_core.types import AutoRetryStartEvent, CompactionEndEvent
from aelix_ai import clear_providers, register_provider
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import AssistantDoneEvent, AssistantErrorEvent, Model, TextDeltaEvent

_API = "auto-compaction-failure-546"
#: context_window - the default reserve (16384) = a 3616-token threshold.
_MODEL = Model(api=_API, id="m", provider="p", context_window=20_000)
_502 = "Error code: 502 - Bad Gateway"
_400 = "Error code: 400 - reasoning cannot be turned off for this model"
_OVERFLOW = "prompt is too long: 250000 tokens > 200000 maximum"
_ANSWER = "the answer is 42"
_SUMMARY = "## Goal\nthe summary"
_FAST = {"retry": {"maxRetries": 2, "baseDelayMs": 1}}
#: Long enough that a row which slept through its wait could never pass.
_MINUTE = {"retry": {"maxRetries": 3, "baseDelayMs": 60_000}}


@pytest.fixture(autouse=True)
def _providers() -> Any:
    clear_providers()
    yield
    clear_providers()


class _Provider:
    """Turn requests and summary requests apart, each following its own script.

    A turn step is ``big`` (an answer reporting 19500 tokens, past the threshold),
    ``silent`` (an answer reporting 25000 input tokens, more than the window: a
    silent overflow), ``overflow`` (an overflow error) or ``ok``. A summary step is
    ``ok``, ``502`` or ``400``; past its script a summary answers the script's last
    step, so ``["502"]`` fails every time.
    """

    def __init__(self, turn: list[str], summary: list[str]) -> None:
        self.turn, self.summary = list(turn), list(summary)
        self.calls = {"turn": 0, "summary": 0}
        register_provider(_API, self._stream)

    async def _stream(self, _model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        is_summary = getattr(context, "system_prompt", None) == SUMMARIZATION_SYSTEM_PROMPT
        key = "summary" if is_summary else "turn"
        self.calls[key] += 1
        plan = self.summary if is_summary else self.turn
        step = (plan.pop(0) if len(plan) > 1 else plan[0]) if plan else "ok"
        if step in ("502", "400", "overflow"):
            text = {"502": _502, "400": _400, "overflow": _OVERFLOW}[step]
            message = AssistantMessage(content=[], stop_reason="error", error_message=text)
            yield AssistantErrorEvent(reason="error", error=message, error_message=text)
            return
        usage: dict[str, int] = {"total_tokens": 10}
        if step == "big":
            usage = {"total_tokens": 19_500}
        elif step == "silent":
            usage = {"input": 25_000, "output": 5}
        text = _SUMMARY if is_summary else _ANSWER
        yield TextDeltaEvent(delta=text)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text=text)], stop_reason="end_turn", usage=usage
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


def _ends(events: list[Any]) -> list[tuple[str, bool, str | None]]:
    return [
        (e.reason, e.aborted, e.error_message) for e in events if isinstance(e, CompactionEndEvent)
    ]


def _last_assistant(harness: AgentHarness) -> AssistantMessage:
    return [m for m in harness._state.messages if isinstance(m, AssistantMessage)][-1]


async def _compactions(session: Session) -> int:
    return sum(1 for e in await session.get_entries() if e.type == "compaction")


# === threshold: the turn keeps its answer ======================================


async def test_a_summary_502_past_its_retries_does_not_fail_the_turn() -> None:
    provider = _Provider(["big"], ["502"])
    session = await _compactable()
    harness, events = _rig(session)
    result = await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert provider.calls == {"turn": 1, "summary": 3}  # the request and its two retries
    assert _ends(events) == [("threshold", False, f"Auto-compaction failed: {_502}")]
    last = _last_assistant(harness)
    assert (last.stop_reason, last.content[0].text) == ("end_turn", _ANSWER)  # type: ignore[union-attr]
    assert any(m is last for m in result)
    assert await _compactions(session) == 0
    assert harness.phase == "idle"


async def test_a_summary_error_that_is_not_retried_does_not_fail_the_turn() -> None:
    """#506's shape: a model that refuses the summary request with a 400."""

    provider = _Provider(["big"], ["400"])
    harness, events = _rig(await _compactable())
    await harness.prompt("go")
    assert provider.calls == {"turn": 1, "summary": 1}
    assert _ends(events) == [("threshold", False, f"Auto-compaction failed: {_400}")]
    assert _last_assistant(harness).stop_reason == "end_turn"


async def test_an_auth_callback_that_raises_for_the_summary_does_not_fail_the_turn() -> None:
    """Not an ``AgentHarnessError``: before #546 the threshold check did not catch it at all."""

    _Provider(["big"], ["ok"])
    auth = _Auth(ok=1, failure=RuntimeError("no credential for p (probe)"))
    harness, events = _rig(await _compactable(), auth)
    await harness.prompt("go")
    assert auth.calls == 2  # the turn's request, then the summary's
    assert _ends(events) == [
        ("threshold", False, "Auto-compaction failed: no credential for p (probe)")
    ]
    assert _last_assistant(harness).stop_reason == "end_turn"


async def test_an_extensions_cancel_ends_the_compaction_aborted_and_the_turn_is_kept() -> None:
    """pi: ``cancelledByExtension`` makes it ``aborted`` with no message (its TUI says
    "Auto-compaction cancelled"); aelix raised "Compaction cancelled" out of the turn."""

    provider = _Provider(["big"], ["ok"])
    session = await _compactable()
    harness, events = _rig(session)

    async def cancel(_event: Any, _ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=True)

    harness.hooks.on("session_before_compact", cancel)
    await harness.prompt("go")
    assert provider.calls == {"turn": 1, "summary": 0}
    assert _ends(events) == [("threshold", True, None)]
    assert _last_assistant(harness).stop_reason == "end_turn"
    assert await _compactions(session) == 0


async def test_an_abort_of_the_prompt_during_the_compaction_ends_it_aborted() -> None:
    """The abort lands in the summary's backoff (a minute): the wait ends at once,
    the compaction ends ``aborted`` with no message, and the prompt returns."""

    _Provider(["big"], ["502"])
    harness, events = _rig(await _compactable(), settings=_MINUTE)

    def on_retry(event: Any) -> None:
        if isinstance(event, AutoRetryStartEvent):
            asyncio.get_running_loop().create_task(harness.abort())

    harness.subscribe(on_retry)
    await asyncio.wait_for(harness.prompt("go"), timeout=10)
    assert _ends(events) == [("threshold", True, None)]
    assert _last_assistant(harness).stop_reason == "end_turn"
    assert harness.phase == "idle"


async def test_an_abort_left_by_a_finished_prompt_does_not_make_a_failure_aborted() -> None:
    """``_abort_requested`` is the running prompt's only under its claim: a public
    ``compact(reason="threshold")`` after an aborted prompt returned is not aborted."""

    _Provider([], ["400"])
    harness, events = _rig(await _compactable())
    harness._abort_requested = True  # what an aborted, finished prompt leaves behind
    with pytest.raises(AgentHarnessError):
        await harness.compact(reason="threshold")
    assert _ends(events) == [("threshold", False, f"Auto-compaction failed: {_400}")]


async def test_the_failure_is_logged_at_debug_not_warning(caplog: pytest.LogCaptureFixture) -> None:
    """The event is the report: a WARNING would reach ``logging.lastResort`` (stderr) in
    the CLI, which installs no handler - over a live TUI, onto json mode's stderr."""

    _Provider(["big"], ["400"])
    harness, _ = _rig(await _compactable())
    caplog.set_level(logging.DEBUG, logger="aelix_agent_core.harness.core")
    await harness.prompt("go")
    records = [r for r in caplog.records if "compaction failed" in r.getMessage()]
    assert [(r.levelno, r.exc_info is not None) for r in records] == [(logging.DEBUG, True)]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_nothing_to_compact_keeps_its_own_words_and_is_not_a_failure() -> None:
    """A no-op on every path: no prefix, not aborted (the existing contract)."""

    _Provider(["big"], ["ok"])
    session = Session(MemorySessionStorage())  # one turn: no cut to make
    harness, events = _rig(session)
    await harness.prompt("go")
    assert _ends(events) == [("threshold", False, "Nothing to compact")]


async def test_a_base_exception_from_the_compaction_still_propagates() -> None:
    """Only ``Exception`` is caught: a cancel or an interpreter exit is not a failure."""

    class _Stop(BaseException):
        pass

    _Provider(["big"], ["ok"])
    harness, _ = _rig(await _compactable())

    async def stop(*_args: Any, **_kwargs: Any) -> Any:
        raise _Stop

    harness.compact = stop  # type: ignore[method-assign]
    with pytest.raises(_Stop):
        await harness.prompt("go")
    assert harness.phase == "idle"


# === overflow: both branches with an auth callback that raises ===================


async def test_a_silent_overflow_whose_compaction_raises_keeps_the_completed_answer() -> None:
    """pi #5720's branch (a completed answer, compact without a re-run)."""

    _Provider(["silent"], ["ok"])
    auth = _Auth(ok=1, failure=RuntimeError("no credential for p (probe)"))
    harness, events = _rig(await _compactable(), auth)
    await harness.prompt("go")
    ends = _ends(events)
    assert ends[0] == (
        "overflow",
        False,
        "Context overflow recovery failed: no credential for p (probe)",
    )
    # The threshold check that follows fails the same way, and is told the same way.
    assert ends[1:] == [("threshold", False, "Auto-compaction failed: no credential for p (probe)")]
    assert _last_assistant(harness).content[0].text == _ANSWER  # type: ignore[union-attr]


async def test_an_overflow_whose_compaction_raises_returns_the_failed_turn() -> None:
    """The turn already failed (the overflow); it is returned, not replaced by a raise."""

    provider = _Provider(["overflow"], ["ok"])
    auth = _Auth(ok=1, failure=RuntimeError("no credential for p (probe)"))
    harness, events = _rig(await _compactable(), auth)
    await harness.prompt("go")
    assert provider.calls["turn"] == 1  # no re-run after a failed compaction
    assert _ends(events)[0] == (
        "overflow",
        False,
        "Context overflow recovery failed: no credential for p (probe)",
    )


# === manual: raised and reported as before ======================================


async def test_a_manual_compaction_that_fails_still_raises_with_its_own_words() -> None:
    _Provider([], ["400"])
    harness, events = _rig(await _compactable())
    with pytest.raises(AgentHarnessError) as caught:
        await harness.compact()
    assert (caught.value.code, str(caught.value)) == ("compaction", _400)
    assert _ends(events) == [("manual", False, _400)]


async def test_a_manual_compaction_an_extension_cancels_still_raises_as_before() -> None:
    _Provider([], ["ok"])
    harness, events = _rig(await _compactable())

    async def cancel(_event: Any, _ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=True, reason="not now")

    harness.hooks.on("session_before_compact", cancel)
    with pytest.raises(AgentHarnessError) as caught:
        await harness.compact()
    assert (caught.value.code, str(caught.value)) == ("compaction", "not now")
    assert _ends(events) == [("manual", False, "not now")]
