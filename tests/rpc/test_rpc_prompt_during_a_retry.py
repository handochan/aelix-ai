"""Issue #334 over RPC: a ``prompt`` command during a retry wait.

``_handle_prompt``'s preflight rejects a non-idle phase (or queues the message
when ``streamingBehavior`` is given) — but during a prompt's retry backoff the
phase used to say ``idle``, so the preflight accepted the command and started a
SECOND turn alongside the first, and ``streamingBehavior`` was ignored (#334
design probes T1/T1s: ``RpcSuccessResponse``, ``provider calls=2``). The phase
now says ``"turn"`` through the whole ``prompt()``, so the preflight is truthful
there with no RPC code change. ``get_state`` reports ``isStreaming`` true through
the wait, as pi's ``_isAgentRunActive`` does (``rpc-mode.ts:449-464`` @
a328aa89a).

"No second provider call" needs a barrier: the base's second turn is a task
that has not started when the response returns. So #1 is driven to completion
(``abort_retry()``) and every task pinned on the harness awaited before the
count; a second call, if any, is scripted to answer at once so the barrier
cannot hang.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.types import AutoRetryStartEvent
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import _handle_get_state, _handle_prompt
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandGetState,
    RpcCommandPrompt,
    RpcErrorResponse,
    RpcSuccessResponse,
)

WAIT = 10.0


def _texts(messages: Any) -> list[str]:
    return [
        c.text for m in messages for c in getattr(m, "content", []) if hasattr(c, "text")
    ]


class _Provider:
    """Call 1 fails retryably (or answers over a 20k threshold with ``big``);
    every later call answers at once."""

    def __init__(self, first: str = "retry") -> None:
        self.first = first
        self.calls = 0

    async def __call__(
        self, model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.calls += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if self.calls == 1 and self.first == "retry":
            failed = AssistantMessage(
                content=[], stop_reason="error", error_message="rate limit exceeded"
            )
            yield AssistantErrorEvent(
                reason="error", error=failed, error_message="rate limit exceeded"
            )
            return
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {self.calls}")],
                stop_reason="end_turn",
                usage={"total_tokens": 5_000}
                if self.calls == 1 and self.first == "big"
                else None,
            )
        )


async def _in_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AgentHarness, _Provider, asyncio.Future[Any]]:
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)
    provider = _Provider()
    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=provider)
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    in_backoff = asyncio.Event()

    def watch(event: object) -> None:
        if isinstance(event, AutoRetryStartEvent):
            in_backoff.set()

    h.subscribe(watch)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_backoff.wait(), WAIT)
    return h, provider, first


async def _barrier(h: AgentHarness, first: asyncio.Future[Any]) -> None:
    h.abort_retry()
    await asyncio.wait_for(first, WAIT)
    for task in list(h._pending_tasks):
        await asyncio.wait_for(task, WAIT)


async def test_a_prompt_during_the_retry_wait_is_rejected_as_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, provider, first = await _in_backoff(monkeypatch)

    response = await _handle_prompt(h, RpcCommandPrompt(message="second", id="p2"))

    await _barrier(h, first)
    assert isinstance(response, RpcErrorResponse)
    assert "busy" in response.error
    assert provider.calls == 1


@pytest.mark.parametrize("behavior", ["steer", "followUp"])
async def test_a_prompt_with_streaming_behavior_during_the_retry_wait_is_queued(
    behavior: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pi's answer to a live run, now reachable in the retry wait: enqueue.
    The retry is given up here, so nothing drains the queue — the message
    waits for the next prompt, as the TUI's steer typed during a countdown
    already did."""

    h, provider, first = await _in_backoff(monkeypatch)

    response = await _handle_prompt(
        h,
        RpcCommandPrompt(message="second", id="p2", streaming_behavior=behavior),
    )

    await _barrier(h, first)
    assert isinstance(response, RpcSuccessResponse)
    assert provider.calls == 1
    queue = h._steering_queue if behavior == "steer" else h._follow_up_queue
    assert _texts(queue._messages) == ["second"]


async def test_get_state_reports_streaming_through_the_retry_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _provider, first = await _in_backoff(monkeypatch)

    state = await _handle_get_state(h, RpcCommandGetState(id="s"))

    await _barrier(h, first)
    data = state.data  # type: ignore[attr-defined]
    assert (data["isStreaming"], data["isCompacting"]) == (True, False)


async def test_get_state_reports_both_flags_in_the_closing_compaction() -> None:
    """The threshold compaction nests under the prompt: pi reports
    ``isStreaming`` and ``isCompacting`` both true there. Green on the base by
    construction (the compaction flipped to ``"compaction"`` there too); it pins
    that nesting keeps reporting the compaction, not only the turn."""

    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))

    async def summarise(_m: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary="S", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=1, details={}
        )

    h = AgentHarness(
        AgentHarnessOptions(
            session=session, stream_fn=_Provider("big"), _summarizer_override=summarise
        )
    )
    h._state.model = Model(id="m20k", context_window=20_000)
    h._state.auto_compaction_enabled = True
    seen: list[tuple[bool, bool]] = []

    async def before_compact(*_args: Any) -> None:
        state = await _handle_get_state(h, RpcCommandGetState(id="s"))
        data = state.data  # type: ignore[attr-defined]
        seen.append((data["isStreaming"], data["isCompacting"]))

    h.hooks.on("session_before_compact", before_compact)  # type: ignore[call-overload]
    await asyncio.wait_for(h.prompt("first"), WAIT)

    assert seen == [(True, True)]
    state = await _handle_get_state(h, RpcCommandGetState(id="s"))
    data = state.data  # type: ignore[attr-defined]
    assert (data["isStreaming"], data["isCompacting"]) == (False, False)
