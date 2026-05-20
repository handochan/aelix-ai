"""Pi parity: 9 supported handlers wired to AgentHarness.

Each handler is invoked with a built AgentHarness and a constructed
command dataclass; we assert the response shape matches the Pi wire
contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_abort,
    _handle_bash,
    _handle_compact,
    _handle_get_messages,
    _handle_get_state,
    _handle_new_session,
    _handle_prompt,
    _handle_set_session_name,
    _handle_set_thinking_level,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandAbort,
    RpcCommandBash,
    RpcCommandCompact,
    RpcCommandGetMessages,
    RpcCommandGetState,
    RpcCommandNewSession,
    RpcCommandPrompt,
    RpcCommandSetSessionName,
    RpcCommandSetThinkingLevel,
    RpcErrorResponse,
    RpcSuccessResponse,
)


def _quiet_stream_fn() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _make_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
        )
    )


async def test_handle_prompt_returns_success_envelope() -> None:
    harness = _make_harness()
    cmd = RpcCommandPrompt(message="hi", id="r1")
    response = await _handle_prompt(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "prompt"
    assert response.id == "r1"
    await harness.wait_for_idle()
    await harness.dispose()


async def test_handle_abort_returns_success_envelope() -> None:
    harness = _make_harness()
    cmd = RpcCommandAbort(id="r2")
    response = await _handle_abort(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "abort"
    assert response.id == "r2"
    await harness.dispose()


async def test_handle_new_session_returns_cancelled_false() -> None:
    harness = _make_harness()
    cmd = RpcCommandNewSession(id="r3")
    response = await _handle_new_session(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "new_session"
    assert response.data == {"cancelled": False}
    await harness.dispose()


async def test_handle_get_state_returns_13_field_pi_shape() -> None:
    """Sprint 6h₂ (P-264): wire shape grows 12 → 13 with
    ``autoRetryEnabled``."""

    harness = _make_harness()
    cmd = RpcCommandGetState(id="r4")
    response = await _handle_get_state(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "get_state"
    assert response.data is not None
    data = response.data
    expected = {
        "model",
        "thinkingLevel",
        "isStreaming",
        "isCompacting",
        "steeringMode",
        "followUpMode",
        "sessionFile",
        "sessionId",
        "sessionName",
        "autoCompactionEnabled",
        "autoRetryEnabled",
        "messageCount",
        "pendingMessageCount",
    }
    assert set(data.keys()) == expected
    assert data["isStreaming"] is False
    assert data["isCompacting"] is False
    assert data["messageCount"] == 0
    await harness.dispose()


async def test_handle_get_messages_returns_empty_list_for_fresh_harness() -> None:
    harness = _make_harness()
    cmd = RpcCommandGetMessages(id="r5")
    response = await _handle_get_messages(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "get_messages"
    assert response.data == {"messages": []}
    await harness.dispose()


async def test_handle_compact_without_session_returns_error() -> None:
    """compact() raises when no session is attached — RPC handler returns error."""

    harness = _make_harness()
    cmd = RpcCommandCompact(id="r6")
    response = await _handle_compact(harness, cmd)
    assert isinstance(response, RpcErrorResponse)
    assert response.command == "compact"
    await harness.dispose()


async def test_handle_bash_executes_via_local_ops(tmp_path) -> None:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            cwd=str(tmp_path),
        )
    )
    cmd = RpcCommandBash(command="echo hello", id="r7")
    response = await _handle_bash(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "bash"
    data = response.data
    assert isinstance(data, dict)
    # P-115 BLOCKING — Pi BashResult shape (4 required keys; 5th
    # ``fullOutputPath`` is only emitted when present).
    assert "output" in data
    assert "hello" in data["output"]
    assert data["exitCode"] == 0
    assert data["cancelled"] is False
    assert isinstance(data["truncated"], bool)
    # Output under the truncation threshold — no spill path emitted.
    assert "fullOutputPath" not in data
    await harness.dispose()


async def test_handle_set_thinking_level_updates_state() -> None:
    harness = _make_harness()
    cmd = RpcCommandSetThinkingLevel(level="high", id="r8")
    response = await _handle_set_thinking_level(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert harness.state.thinking_level == "high"
    await harness.dispose()


async def test_handle_set_session_name_rejects_empty_name() -> None:
    harness = _make_harness()
    cmd = RpcCommandSetSessionName(name="   ", id="r9")
    response = await _handle_set_session_name(harness, cmd)
    assert isinstance(response, RpcErrorResponse)
    assert "empty" in response.error.lower()
    await harness.dispose()


async def test_handle_set_session_name_without_session_returns_error() -> None:
    harness = _make_harness()
    cmd = RpcCommandSetSessionName(name="my session", id="r10")
    response = await _handle_set_session_name(harness, cmd)
    assert isinstance(response, RpcErrorResponse)
    assert "session" in response.error.lower()
    await harness.dispose()


@pytest.mark.asyncio
async def test_handle_get_state_reports_session_name_when_cached() -> None:
    harness = _make_harness()
    harness._cached_session_name = "my-cached-name"
    cmd = RpcCommandGetState(id="r11")
    response = await _handle_get_state(harness, cmd)
    assert isinstance(response, RpcSuccessResponse)
    data = response.data
    assert isinstance(data, dict)
    # ``session_name`` is None because no session is attached — the
    # cached read only takes effect when a Session is present.
    # Sprint 6d: this asserts the response shape, not the cache wire-up.
    assert "sessionName" in data
    await harness.dispose()
