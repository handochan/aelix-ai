"""Sprint 6h₂₀ — auto-retry tests (ADR-0128, pi parity for
``agent-session.ts:2414-2511``).
"""

from __future__ import annotations

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
