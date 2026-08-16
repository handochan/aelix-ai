"""Sprint 6h₂₀ — auto-retry tests (ADR-0128, pi parity for
``agent-session.ts:2414-2511``).
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage


def _build_harness(*, auto_retry: bool = True) -> AgentHarness:
    base = AgentHarnessOptions(session=Session(MemorySessionStorage()))
    h = AgentHarness(base)
    h._state.auto_retry_enabled = auto_retry
    # auto_compaction triggered after retry would also fire on the in-memory
    # state with no model; explicitly disable so tests stay focused.
    h._state.auto_compaction_enabled = False
    return h


def _err(text: str = "overloaded_error: provider returned error") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="")],
        stop_reason="error",
        error_message=text,
    )


# === _is_retryable_error =====================================================


@pytest.mark.parametrize(
    "msg",
    [
        "overloaded_error: provider returned error",
        "rate limit exceeded",
        "rate-limit (HTTP 429)",
        "ratelimit reached",
        "HTTP 429 Too Many Requests",
        "internal server error 500",
        "Bad Gateway (502)",
        "503 Service Unavailable",
        "504 Gateway Timeout",
        "Connection refused",
        "connection lost",
        "fetch failed",
        "socket hang up",
        "websocket closed",
        "stream ended before message_stop",
        "Request timed out",
        "request timeout",
        "terminated",
        "retry delay exceeded",
    ],
)
def test_is_retryable_error_positive(msg: str) -> None:
    h = _build_harness()
    assert h._is_retryable_error(_err(msg)) is True


@pytest.mark.parametrize(
    "msg",
    [
        "permission denied",
        "invalid API key",
        "model not found",
        "tool call malformed",
        "context length exceeded",  # overflow handled by 6h₁₈, NOT retry
    ],
)
def test_is_retryable_error_negative_non_retriable(msg: str) -> None:
    h = _build_harness()
    assert h._is_retryable_error(_err(msg)) is False


def test_is_retryable_error_negative_non_error_stop_reason() -> None:
    h = _build_harness()
    msg = AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason="end_turn",
        error_message=None,
    )
    assert h._is_retryable_error(msg) is False


def test_is_retryable_error_negative_no_message_text() -> None:
    h = _build_harness()
    msg = AssistantMessage(
        content=[TextContent(text="")],
        stop_reason="error",
        error_message=None,
    )
    assert h._is_retryable_error(msg) is False


def test_is_retryable_error_excludes_overflow_in_retryable_envelope() -> None:
    """pi ``:2486`` — overflow wrapped in a retryable envelope is NOT retryable.

    A provider can surface a context overflow inside a retryable-looking
    envelope; this message matches BOTH ``_RETRYABLE_ERROR_PATTERN``
    (``provider returned error``) AND an overflow pattern (``maximum context
    length is N tokens``). The explicit ``isContextOverflow`` short-circuit must
    win so the overflow routes to compact-and-retry instead of burning the
    auto-retry backoff budget re-running the same oversized context.
    """

    from aelix_agent_core.harness.core import _RETRYABLE_ERROR_PATTERN
    from aelix_ai.utils.overflow import is_context_overflow

    h = _build_harness()
    wrapped = (
        "Provider returned error: this model's maximum context length "
        "is 200000 tokens"
    )
    # Precondition: the envelope genuinely matches the retryable regex...
    assert _RETRYABLE_ERROR_PATTERN.search(wrapped) is not None
    # ...and is also a recognizable context overflow (error-message case,
    # context_window irrelevant here).
    assert is_context_overflow(_err(wrapped), 0) is True
    # Therefore it must be excluded from retry (overflow recovery handles it).
    assert h._is_retryable_error(_err(wrapped)) is False


# === _handle_retryable_error =================================================


async def _capture_events(h: AgentHarness) -> list[Any]:
    events: list[Any] = []
    h.subscribe(lambda ev: events.append(ev) or None)
    return events


async def test_handle_retryable_returns_false_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _build_harness(auto_retry=False)
    events = await _capture_events(h)
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1
    )
    assert await h._handle_retryable_error(_err()) is False
    assert events == []  # disabled → no event emitted


async def test_handle_retryable_emits_start_and_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _build_harness()
    h._state.messages = [
        UserMessage(content=[TextContent(text="please retry")]),
        _err("overloaded"),
    ]
    events = await _capture_events(h)
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1
    )
    did = await h._handle_retryable_error(h._state.messages[-1])
    assert did is True
    # The error assistant was removed from state (pi parity :2473-2476).
    assert len(h._state.messages) == 1
    assert isinstance(h._state.messages[0], UserMessage)
    assert h._retry_attempt == 1
    # Single AutoRetryStartEvent emitted.
    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    assert len(starts) == 1
    assert starts[0].attempt == 1
    assert starts[0].max_attempts == 3
    assert starts[0].delay_ms == 1  # base 1ms * 2^0 (monkeypatched base)
    assert "overloaded" in starts[0].error_message


async def test_handle_retryable_backoff_progression(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exponential: base * 2^(attempt-1) → 1, 2, 4 ms.
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1
    )
    h = _build_harness()
    events = await _capture_events(h)
    delays: list[int] = []
    for _ in range(3):
        h._state.messages = [
            UserMessage(content=[TextContent(text="x")]),
            _err("overloaded"),
        ]
        await h._handle_retryable_error(h._state.messages[-1])
    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    delays = [e.delay_ms for e in starts]
    assert delays == [1, 2, 4]


async def test_handle_retryable_returns_false_at_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1
    )
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_MAX_ATTEMPTS", 2
    )
    h = _build_harness()
    events = await _capture_events(h)
    # First two retries succeed (return True)
    h._state.messages = [UserMessage(content=[TextContent(text="x")]), _err()]
    assert await h._handle_retryable_error(h._state.messages[-1]) is True
    h._state.messages.append(_err())
    assert await h._handle_retryable_error(h._state.messages[-1]) is True
    # Third call exceeds max → False + counter resets + auto_retry_end emitted.
    h._state.messages.append(_err())
    assert await h._handle_retryable_error(h._state.messages[-1]) is False
    assert h._retry_attempt == 0  # reset
    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    assert len(ends) == 1
    assert ends[0].success is False
    assert ends[0].attempt == 2  # the last attempt number before exceed


async def test_abort_retry_cancels_mid_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    # Long delay so the abort fires while the sleep is in-flight.
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 5_000
    )
    h = _build_harness()
    h._state.messages = [
        UserMessage(content=[TextContent(text="x")]),
        _err(),
    ]
    events = await _capture_events(h)

    # Schedule an abort 50 ms in.
    async def _abort_soon() -> None:
        await asyncio.sleep(0.05)
        h.abort_retry()

    abort_task = asyncio.create_task(_abort_soon())
    did = await h._handle_retryable_error(h._state.messages[-1])
    await abort_task
    assert did is False  # aborted
    assert h._retry_attempt == 0
    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    assert len(ends) == 1
    assert ends[0].success is False
    assert ends[0].final_error == "Retry cancelled"


# === Integration via prompt() ================================================


async def test_prompt_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock _run: first call emits an error assistant; second call (retry) emits
    # a success assistant. prompt() should retry once + reset the counter +
    # emit success.
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1
    )
    h = _build_harness()
    events = await _capture_events(h)

    run_calls: list[list[Any]] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        # First call: append a retriable-error assistant. Second: a success.
        attempt = len(run_calls)
        if attempt == 1:
            h._state.messages.extend(prompts)
            h._state.messages.append(_err("rate limit"))
        else:
            h._state.messages.append(
                AssistantMessage(
                    content=[TextContent(text="ok")], stop_reason="end_turn"
                )
            )
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("please retry")
    # _run was called twice — first with the user prompt, second with empty (continue).
    assert len(run_calls) == 2
    assert any(isinstance(m, UserMessage) for m in run_calls[0])
    assert run_calls[1] == []  # retry continues from existing context
    # Counter reset after success.
    assert h._retry_attempt == 0
    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    assert len(starts) == 1
    assert len(ends) == 1
    assert ends[0].success is True
    assert ends[0].attempt == 1


async def test_prompt_max_retries_emits_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1
    )
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_MAX_ATTEMPTS", 2
    )
    h = _build_harness()
    events = await _capture_events(h)

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        h._state.messages.extend(prompts)
        h._state.messages.append(_err("503 Service Unavailable"))
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("always fails")
    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    # 2 retries attempted (attempts 1 and 2); 3rd attempt exceeds max → end.
    assert len(starts) == 2
    assert len(ends) == 1
    assert ends[0].success is False
    assert "503" in (ends[0].final_error or "")
    assert h._retry_attempt == 0  # reset after failure


async def test_prompt_no_retry_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1
    )
    h = _build_harness(auto_retry=False)
    events = await _capture_events(h)

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        h._state.messages.extend(prompts)
        h._state.messages.append(_err())
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("fails")
    assert events == []  # no retry events when disabled


# === W-review LOW-3 — structural guarantees =================================


async def test_input_handled_short_circuit_skips_retry_loop() -> None:
    # InputHandled returns [] BEFORE _run; the retry loop must not run.
    from aelix_agent_core.harness.hooks import InputHandled

    h = _build_harness()
    events = await _capture_events(h)
    ran: list[None] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        ran.append(None)
        return []

    async def _handled(_ev: Any, _ctx: Any) -> InputHandled:
        return InputHandled()

    h._run = _fake_run  # type: ignore[method-assign]
    h.hooks.on("input", _handled)
    await h.prompt("never reaches the model")
    assert ran == []  # InputHandled short-circuited _run
    assert events == []  # …and the retry loop never fired


async def test_busy_guard_does_not_trigger_retry_loop() -> None:
    # A concurrent prompt() hits the busy-guard BEFORE _run; the rejected
    # caller must not invoke the retry loop or mutate retry state.
    from aelix_agent_core.harness.core import AgentHarnessError

    h = _build_harness()
    events = await _capture_events(h)
    h._phase = "turn"  # simulate an in-flight turn from another caller
    with pytest.raises(AgentHarnessError) as ei:
        await h.prompt("blocked")
    assert ei.value.code == "busy"
    assert events == []
    assert h._retry_attempt == 0


# === #147 — the retryable → NON-retryable terminal path =======================


async def test_retry_ending_in_a_non_retryable_error_still_emits_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#147 — pi emits ``auto_retry_end`` on BOTH terminal paths; aelix had one.

    pi ``agent-session.ts:671-678`` (success) was ported; ``:1088-1096`` (the
    ``stopReason === "error" && _retryAttempt > 0`` arm) was not. So a retry
    sequence that engaged and then hit a NON-retryable error emitted a start
    with no end.

    That silence is load-bearing. ``auto_retry_end`` is the ONLY event
    ``tui/shell.py::_end_retry_countdown`` listens to, and it is the only line
    that restores ``out_chrome.on_interrupt`` after
    ``_start_retry_countdown`` swapped it for a handler calling
    ``abort_retry()`` — documented "No-op when no retry is in flight". Without
    the end event, **Esc and Ctrl+C stay wired to a no-op for the rest of the
    session**, including turns with no retry in them, and the "Retrying (N/M)…"
    widget never clears.

    The sequence is mundane, which is why it matters: a rate limit (retryable,
    engages the loop) followed by an expired token (not retryable, breaks it).
    Measured before the fix: 1 start, 0 ends, ``_retry_attempt`` left at 1.
    """

    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)
    h = _build_harness()
    events = await _capture_events(h)

    calls: list[None] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        calls.append(None)
        h._state.messages.extend(prompts)
        if len(calls) == 1:
            h._state.messages.append(_err("rate limit exceeded"))  # retryable
        else:
            h._state.messages.append(_err("invalid API key"))  # NOT retryable
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("go")

    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]

    assert len(calls) == 2, "precondition: the retry actually engaged"
    assert len(starts) == 1
    # THE PIN — one start must be answered by exactly one end.
    assert len(ends) == 1
    assert ends[0].success is False
    assert ends[0].attempt == 1
    assert "invalid API key" in (ends[0].final_error or "")
    # The counter must not leak into the next turn.
    assert h._retry_attempt == 0


async def test_a_non_retryable_error_without_a_prior_retry_emits_nothing() -> None:
    """The other half: no retry engaged ⇒ no end event to answer.

    Without this, a fix that emitted ``auto_retry_end`` on every terminal error
    would pass the test above while committing a spurious "✖ Retry failed" line
    on ordinary failures. ``_end_retry_countdown`` guards against that too, but
    the kernel should not be emitting the event in the first place.
    """

    h = _build_harness()
    events = await _capture_events(h)

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        h._state.messages.extend(prompts)
        h._state.messages.append(_err("invalid API key"))
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("go")

    assert [e for e in events if isinstance(e, AutoRetryStartEvent)] == []
    assert [e for e in events if isinstance(e, AutoRetryEndEvent)] == []
    assert h._retry_attempt == 0


async def test_an_aborted_retry_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user who interrupts a retry must not be told it succeeded.

    The terminal-success arm compared ``stop_reason != "error"``, and an
    aborted turn's assistant carries ``stop_reason == "aborted"`` — which is
    not ``"error"``. So the end event claimed ``success=True`` and the TUI
    committed a green "✓ Retry succeeded (attempt 1)" immediately under the
    user's own "✖ Operation aborted".

    Observed in the live tmux gate for #147, which is the only reason it was
    caught: the comparison predates #147, but the fix is what made this path
    reachable often enough to see.
    """

    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)
    h = _build_harness()
    events = await _capture_events(h)

    calls: list[None] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        calls.append(None)
        h._state.messages.extend(prompts)
        if len(calls) == 1:
            h._state.messages.append(_err("rate limit exceeded"))  # retryable
        else:
            h._state.messages.append(
                AssistantMessage(
                    content=[TextContent(text="")],
                    stop_reason="aborted",
                    error_message=None,
                )
            )
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("go")

    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    assert len(ends) == 1, "the sequence must still close out so the TUI recovers"
    assert ends[0].success is False, "an aborted retry is not a successful one"
    assert h._retry_attempt == 0


# === #147 / ADR-0225 — abort() during the BACKOFF SLEEP ======================
#
# Every test above monkeypatches ``h._run``, so no real turn task is ever
# created and the abort path they exercise is not the one the product runs.
# These two use a real ``stream_fn`` for that reason.


def _retryable_stream_fn(calls: list[int]) -> Any:
    """A stream_fn that always fails retryably, and counts its own calls."""

    from aelix_ai.streaming import AssistantErrorEvent, AssistantStartEvent

    async def stream_fn(model: Any, context: Any, options: Any = None) -> Any:
        calls.append(1)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        failed = AssistantMessage(
            content=[],
            stop_reason="error",
            error_message="rate limit exceeded",
        )
        yield AssistantErrorEvent(
            reason="error", error=failed, error_message="rate limit exceeded"
        )

    return stream_fn


async def _run_with_abort_during_backoff(
    monkeypatch: pytest.MonkeyPatch, *, abort: bool
) -> int:
    import asyncio

    monkeypatch.setattr(
        "aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 600
    )
    calls: list[int] = []
    h = AgentHarness(
        AgentHarnessOptions(
            session=Session(MemorySessionStorage()),
            stream_fn=_retryable_stream_fn(calls),
        )
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False

    # EVENT-DRIVEN, NOT TIMED. A first version slept 0.15 s to "land inside" the
    # 600 ms backoff and flaked under load: with the loop starved, 0.15 s of wall
    # clock could already be past the whole backoff, the retry had fired, and the
    # test reported a defect that was not there. ``auto_retry_start`` is emitted
    # immediately before the sleep begins, so waiting on it is exact at any speed.
    in_backoff = asyncio.Event()

    async def _watch(event: object) -> None:
        if isinstance(event, AutoRetryStartEvent):
            in_backoff.set()

    h.subscribe(_watch)

    turn = asyncio.create_task(h.prompt("go"))
    await asyncio.wait_for(in_backoff.wait(), timeout=30)

    # Each arm waits for its OWN outcome, bounded, rather than for a fixed
    # duration. Waiting for the turn to END would not work for the control: with
    # no abort the loop runs every attempt, so ``calls`` reaches 4, not 2.
    if abort:
        await h.abort()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(turn), timeout=30)
    else:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30
        while len(calls) < 2 and loop.time() < deadline:
            await asyncio.sleep(0.01)
    served = len(calls)

    if not turn.done():
        turn.cancel()
    # The abort path raises CancelledError, a BaseException.
    with contextlib.suppress(BaseException):
        await turn
    await h.dispose()
    return served


async def test_abort_during_the_retry_backoff_stops_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SABOTAGE: delete the ``self.abort_retry()`` call from
    :meth:`AgentHarness.abort`. The backoff sleep sits BETWEEN turn tasks, so
    ``_current_turn_task`` is ``None`` and there is nothing to cancel; the next
    attempt's ``_run`` then clears ``_abort_requested`` on entry and the retry
    fires anyway. Measured before the fix: 2 provider calls, identical to no
    abort at all.

    The TUI does not reach this — during the countdown its interrupt is wired to
    ``abort_retry`` for pi parity — but the RPC ``abort`` command and any SDK
    embedder call ``abort()``.
    """

    assert await _run_with_abort_during_backoff(monkeypatch, abort=True) == 1, (
        "abort() during the backoff was discarded and the retry went out anyway"
    )


async def test_a_backoff_that_is_not_aborted_still_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSITIVE CONTROL — do not delete as redundant.

    Same rig, no abort. If this ever reports one call, the retry is not firing
    for some unrelated reason and the test above passes without proving anything.
    """

    assert await _run_with_abort_during_backoff(monkeypatch, abort=False) >= 2, (
        "the rig never produced a retry, so its sibling proves nothing"
    )
