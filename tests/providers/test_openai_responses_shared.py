"""Tests for the OpenAI **Responses** shared engine (pi parity, #15).

Pi parity: ``packages/ai/src/api/openai-responses-shared.ts`` @ SHA
``927e98068cda276bf9188f4774fb927c89823388``.

Fixture-driven: the streaming tests feed fake SSE event traces (lists of
plain dicts) through :func:`process_responses_stream` and assert the
emitted Aelix events + the finalized :class:`ResponsesStreamState`. The
focus is the byte-sensitive behavior — out-of-order slot routing,
encrypted-reasoning roundtrip, composite tool-call ids, the args.done
suffix guard, usage incl. reasoning, the stop-reason override, and the
saw-terminal guard.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers._openai_responses_shared import (
    OPENAI_TOOL_CALL_PROVIDERS,
    ResponsesStreamState,
    convert_responses_messages,
    convert_responses_tools,
    encode_text_signature_v1,
    map_stop_reason,
    parse_text_signature,
    process_responses_stream,
)
from aelix_ai.streaming import (
    Context,
    Model,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

# === helpers ================================================================


def _model(**kw: Any) -> Model:
    base: dict[str, Any] = {
        "id": "gpt-5",
        "name": "gpt-5",
        "api": "openai-responses",
        "provider": "openai",
        "input": ["text", "image"],
        "reasoning": True,
    }
    base.update(kw)
    return Model(**base)


async def _aiter(events: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for ev in events:
        yield ev


async def _run(
    events: list[dict[str, Any]], model: Model | None = None
) -> tuple[ResponsesStreamState, list[Any]]:
    state = ResponsesStreamState()
    out: list[Any] = []
    async for ev in process_responses_stream(
        _aiter(events), state, model or _model()
    ):
        out.append(ev)
    return state, out


# === text-signature codec ===================================================


def test_encode_text_signature_no_phase() -> None:
    assert encode_text_signature_v1("msg_1") == '{"v":1,"id":"msg_1"}'


def test_encode_text_signature_with_phase() -> None:
    assert (
        encode_text_signature_v1("msg_1", "final_answer")
        == '{"v":1,"id":"msg_1","phase":"final_answer"}'
    )


def test_parse_text_signature_roundtrip() -> None:
    for item_id, phase in [
        ("msg_1", None),
        ("msg_2", "commentary"),
        ("msg_3", "final_answer"),
    ]:
        sig = encode_text_signature_v1(item_id, phase)
        parsed = parse_text_signature(sig)
        assert parsed is not None
        assert parsed["id"] == item_id
        if phase:
            assert parsed["phase"] == phase
        else:
            assert "phase" not in parsed


def test_parse_text_signature_invalid_phase_dropped() -> None:
    parsed = parse_text_signature('{"v":1,"id":"x","phase":"bogus"}')
    assert parsed == {"id": "x"}


def test_parse_text_signature_legacy_plain_string() -> None:
    assert parse_text_signature("legacy_id") == {"id": "legacy_id"}


def test_parse_text_signature_malformed_json_is_bare_id() -> None:
    # Starts with "{" but is not valid JSON -> treated as a bare id.
    assert parse_text_signature("{not json") == {"id": "{not json"}


def test_parse_text_signature_empty_returns_none() -> None:
    assert parse_text_signature(None) is None
    assert parse_text_signature("") is None


# === tool conversion ========================================================


class _Tool:
    def __init__(self, name: str, description: str, parameters: dict[str, Any]):
        self.name = name
        self.description = description
        self.parameters = parameters


def test_openai_tool_call_providers_constant() -> None:
    assert sorted(OPENAI_TOOL_CALL_PROVIDERS) == [
        "openai",
        "openai-codex",
        "opencode",
    ]


def test_convert_responses_tools_flat_shape() -> None:
    tools = [_Tool("read", "Read a file", {"type": "object", "properties": {}})]
    out = convert_responses_tools(tools)
    assert out == [
        {
            "type": "function",
            "name": "read",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {}},
            "strict": False,
        }
    ]


def test_convert_responses_tools_strict_override() -> None:
    out = convert_responses_tools([_Tool("t", "d", {})], strict=True)
    assert out[0]["strict"] is True


# === message conversion =====================================================


def test_system_prompt_uses_developer_role_for_reasoning_model() -> None:
    out = convert_responses_messages(
        _model(reasoning=True), Context(system_prompt="be good"),
    )
    assert out[0] == {"role": "developer", "content": "be good"}


def test_system_prompt_uses_system_role_for_non_reasoning_model() -> None:
    out = convert_responses_messages(
        _model(reasoning=False), Context(system_prompt="be good"),
    )
    assert out[0] == {"role": "system", "content": "be good"}


def test_system_prompt_respects_compat_developer_role_false() -> None:
    out = convert_responses_messages(
        _model(reasoning=True, compat={"supportsDeveloperRole": False}),
        Context(system_prompt="be good"),
    )
    assert out[0]["role"] == "system"


def test_user_message_text_and_image() -> None:
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="hello"),
                    ImageContent(mime_type="image/png", data="QUJD"),
                ]
            )
        ]
    )
    out = convert_responses_messages(_model(), ctx)
    assert out == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": "data:image/png;base64,QUJD",
                },
            ],
        }
    ]


def test_assistant_text_with_signature_replays_id_and_phase() -> None:
    sig = encode_text_signature_v1("msg_abc", "final_answer")
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[TextContent(text="hi", text_signature=sig)],
                api="openai-responses",
                provider="openai",
                model="gpt-5",
            )
        ]
    )
    out = convert_responses_messages(_model(), ctx)
    assert out[0] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "hi", "annotations": []}],
        "status": "completed",
        "id": "msg_abc",
        "phase": "final_answer",
    }


def test_assistant_text_without_signature_uses_fallback_id() -> None:
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[TextContent(text="hi")],
                api="openai-responses",
                provider="openai",
                model="gpt-5",
            )
        ]
    )
    out = convert_responses_messages(_model(), ctx)
    assert out[0]["id"] == "msg_pi_0"
    assert "phase" not in out[0]


def test_assistant_reasoning_replayed_verbatim_with_encrypted_content() -> None:
    reasoning_item = {
        "type": "reasoning",
        "id": "rs_123",
        "summary": [{"type": "summary_text", "text": "thought"}],
        "encrypted_content": "ENCRYPTED_BLOB",
    }
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent(
                        thinking="thought",
                        thinking_signature=json.dumps(reasoning_item),
                    )
                ],
                api="openai-responses",
                provider="openai",
                model="gpt-5",
            )
        ]
    )
    out = convert_responses_messages(_model(), ctx)
    # The reasoning item is pushed back verbatim, encrypted_content intact.
    assert out[0] == reasoning_item


def test_assistant_tool_call_composite_id_split() -> None:
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="call_x|fc_abc",
                        tool_name="read",
                        input={"path": "/tmp"},
                    )
                ],
                api="openai-responses",
                provider="openai",
                model="gpt-5",
            )
        ]
    )
    out = convert_responses_messages(_model(), ctx)
    assert out[0] == {
        "type": "function_call",
        "call_id": "call_x",
        "name": "read",
        "arguments": json.dumps({"path": "/tmp"}),
        "id": "fc_abc",
    }


def test_assistant_tool_call_different_model_omits_fc_id() -> None:
    # Same provider/api but a DIFFERENT model id -> fc_ item id dropped to
    # dodge OpenAI's reasoning/function-call pairing validation.
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="call_x|fc_abc",
                        tool_name="read",
                        input={},
                    )
                ],
                api="openai-responses",
                provider="openai",
                model="gpt-4o",
            )
        ]
    )
    out = convert_responses_messages(_model(id="gpt-5"), ctx)
    assert "id" not in out[0]
    assert out[0]["call_id"] == "call_x"


def test_tool_result_becomes_function_call_output() -> None:
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="call_x|fc_abc",
                content=[TextContent(text="file contents")],
            )
        ]
    )
    out = convert_responses_messages(_model(), ctx)
    assert out[0] == {
        "type": "function_call_output",
        "call_id": "call_x",
        "output": "file contents",
    }


def test_tool_call_id_foreign_provider_hashed() -> None:
    # A tool call minted by a different provider gets a stable fc_<hash>
    # item id (deterministic short_hash), not the raw id.
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="call_y|orig_item_99",
                        tool_name="t",
                        input={},
                    )
                ],
                api="anthropic",
                provider="anthropic",
                model="claude",
            ),
            ToolResultMessage(
                tool_call_id="call_y|orig_item_99",
                content=[TextContent(text="ok")],
            ),
        ]
    )
    out = convert_responses_messages(_model(provider="openai"), ctx)
    fc = next(m for m in out if m.get("type") == "function_call")
    assert fc["id"].startswith("fc_")
    # Deterministic — re-running yields the same hash id.
    out2 = convert_responses_messages(_model(provider="openai"), ctx)
    fc2 = next(m for m in out2 if m.get("type") == "function_call")
    assert fc["id"] == fc2["id"]


# === stop-reason mapping ====================================================


def test_map_stop_reason_table() -> None:
    assert map_stop_reason("completed") == "stop"
    assert map_stop_reason("incomplete") == "length"
    assert map_stop_reason("failed") == "error"
    assert map_stop_reason("cancelled") == "error"
    assert map_stop_reason("in_progress") == "stop"
    assert map_stop_reason("queued") == "stop"
    assert map_stop_reason(None) == "stop"


def test_map_stop_reason_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unhandled stop reason"):
        map_stop_reason("wat")


# === streaming: 1:1 event mapping ===========================================


async def test_stream_basic_text_only() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_1"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "msg_1"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Hel"},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "lo"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "msg_1",
                "content": [{"type": "output_text", "text": "Hello"}],
            },
        },
        {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed"},
        },
    ]
    state, out = await _run(events)

    kinds = [type(e) for e in out]
    assert kinds == [
        TextStartEvent,
        TextDeltaEvent,
        TextDeltaEvent,
        TextEndEvent,
    ]
    assert state.response_id == "resp_1"
    assert state.stop_reason == "stop"
    assert len(state.content) == 1
    block = state.content[0]
    assert isinstance(block, TextContent)
    assert block.text == "Hello"
    assert parse_text_signature(block.text_signature) == {"id": "msg_1"}


async def test_stream_reasoning_text_and_summary() -> None:
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning", "id": "rs_1"},
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "delta": "step one",
        },
        {
            "type": "response.reasoning_summary_part.done",
            "output_index": 0,
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "delta": "step two",
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"text": "step one\n\nstep two"}],
                "encrypted_content": "BLOB",
            },
        },
        {
            "type": "response.completed",
            "response": {"id": "r", "status": "completed"},
        },
    ]
    state, out = await _run(events)

    assert [type(e) for e in out] == [
        ThinkingStartEvent,
        ThinkingDeltaEvent,  # "step one"
        ThinkingDeltaEvent,  # "\n\n" from summary_part.done
        ThinkingDeltaEvent,  # "step two"
        ThinkingEndEvent,
    ]
    block = state.content[0]
    assert isinstance(block, ThinkingContent)
    # output_item.done overrides the streamed buffer with the summary join.
    assert block.thinking == "step one\n\nstep two"
    # Full reasoning item (incl encrypted_content) stored as signature.
    stored = json.loads(block.thinking_signature)
    assert stored["encrypted_content"] == "BLOB"
    assert stored["id"] == "rs_1"


# === streaming: out-of-order slot routing ===================================


async def test_stream_out_of_order_reasoning_after_function_call() -> None:
    """Reasoning (index 0) finalizes AFTER a function_call (index 1) starts.

    Slots are keyed by output_index, so the late reasoning deltas/done still
    route to block 0 and the tool call to block 1 — pi #6009.
    """
    events = [
        # function_call opens first at index 1.
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read"},
        },
        # reasoning opens at index 0 AFTER the function_call.
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning", "id": "rs_1"},
        },
        {
            "type": "response.reasoning_text.delta",
            "output_index": 0,
            "delta": "thinking",
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 1,
            "delta": '{"path":"/x"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"type": "reasoning", "id": "rs_1", "content": [{"text": "thinking"}]},
        },
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read", "arguments": '{"path":"/x"}'},
        },
        {
            "type": "response.completed",
            "response": {"id": "r", "status": "completed"},
        },
    ]
    state, out = await _run(events)

    # Block 0 (added second, index 1 in content) is the function_call;
    # block 1 (added... wait) — content order is APPEND order: fc added
    # first -> content[0] = toolCall; reasoning added next -> content[1].
    assert isinstance(state.content[0], ToolCallContent)
    assert isinstance(state.content[1], ThinkingContent)
    assert state.content[0].tool_call_id == "call_1|fc_1"
    assert state.content[0].input == {"path": "/x"}
    assert state.content[1].thinking == "thinking"
    # tool present + completed -> toolUse override.
    assert state.stop_reason == "toolUse"


# === streaming: function-call args + suffix guard ===========================


async def test_stream_function_call_args_done_suffix_guard_emits() -> None:
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "t"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": '{"a":',
        },
        {
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "arguments": '{"a":1}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "t", "arguments": '{"a":1}'},
        },
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    state, out = await _run(events)
    # Full event kind sequence for a tool call.
    assert [type(e) for e in out] == [
        ToolCallStartEvent,
        ToolCallDeltaEvent,
        ToolCallDeltaEvent,  # suffix delta on .done
        ToolCallEndEvent,
    ]
    deltas = [e for e in out if isinstance(e, ToolCallDeltaEvent)]
    # One delta for the streamed partial, one suffix delta on .done.
    assert [d.delta for d in deltas] == ['{"a":', "1}"]
    end = out[-1]
    assert isinstance(end, ToolCallEndEvent)
    assert end.tool_call.tool_call_id == "c1|fc_1"
    assert state.content[0].input == {"a": 1}


async def test_stream_function_call_args_done_suffix_guard_skips_divergent() -> None:
    # When .done arguments DON'T extend the streamed partial, no suffix
    # delta is emitted (pi shared.ts:462).
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "t"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": '{"WRONG":',
        },
        {
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "arguments": '{"a":1}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "t", "arguments": '{"a":1}'},
        },
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    state, out = await _run(events)
    deltas = [e for e in out if isinstance(e, ToolCallDeltaEvent)]
    assert [d.delta for d in deltas] == ['{"WRONG":']  # no suffix delta
    # Final input still reflects the .done arguments.
    assert state.content[0].input == {"a": 1}


# === streaming: encrypted-reasoning roundtrip (end-to-end) ==================


async def test_encrypted_reasoning_roundtrip_end_to_end() -> None:
    """Stream stores the full reasoning item; convert replays it verbatim."""
    full_item = {
        "type": "reasoning",
        "id": "rs_xyz",
        "summary": [{"text": "deduction"}],
        "encrypted_content": "SECRET",
    }
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning", "id": "rs_xyz"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": full_item,
        },
        {"type": "response.completed", "response": {"id": "r", "status": "completed"}},
    ]
    state, _ = await _run(events)
    thinking_block = state.content[0]
    assert isinstance(thinking_block, ThinkingContent)

    # Feed the captured assistant message back through convert.
    assistant = AssistantMessage(
        content=[thinking_block],
        api="openai-responses",
        provider="openai",
        model="gpt-5",
    )
    out = convert_responses_messages(
        _model(), Context(messages=[assistant])
    )
    assert out[0] == full_item


# === streaming: usage incl. reasoning =======================================


async def test_stream_usage_includes_reasoning_and_subtracts_cache() -> None:
    events = [
        {
            "type": "response.completed",
            "response": {
                "id": "r",
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "total_tokens": 140,
                    "input_tokens_details": {"cached_tokens": 30},
                    "output_tokens_details": {"reasoning_tokens": 12},
                },
            },
        },
    ]
    state, _ = await _run(events)
    assert state.usage == {
        "input": 70,  # 100 - 30 cached
        "output": 40,
        "input_tokens": 70,
        "output_tokens": 40,
        "cache_read": 30,
        "cache_write": 0,
        "reasoning": 12,
        "total_tokens": 140,
    }


# === streaming: stop-reason override ========================================


async def test_stream_incomplete_maps_to_length() -> None:
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "m"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "x"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"type": "message", "id": "m", "content": [{"type": "output_text", "text": "x"}]},
        },
        {
            "type": "response.incomplete",
            "response": {"id": "r", "status": "incomplete"},
        },
    ]
    state, _ = await _run(events)
    assert state.stop_reason == "length"


# === streaming: terminal guard + error paths ================================


async def test_stream_without_terminal_event_raises() -> None:
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "m"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "x"},
        # no response.completed / incomplete / failed
    ]
    with pytest.raises(RuntimeError, match="terminal response event"):
        await _run(events)


async def test_stream_error_event_raises() -> None:
    events = [
        {"type": "error", "code": "rate_limit", "message": "slow down"},
    ]
    with pytest.raises(RuntimeError, match="Error Code rate_limit: slow down"):
        await _run(events)


async def test_stream_response_failed_raises_with_error_details() -> None:
    events = [
        {
            "type": "response.failed",
            "response": {
                "error": {"code": "server_error", "message": "boom"},
            },
        },
    ]
    with pytest.raises(RuntimeError, match="server_error: boom"):
        await _run(events)


async def test_stream_response_failed_raises_with_incomplete_details() -> None:
    events = [
        {
            "type": "response.failed",
            "response": {"incomplete_details": {"reason": "content_filter"}},
        },
    ]
    with pytest.raises(RuntimeError, match="incomplete: content_filter"):
        await _run(events)


async def test_stream_output_item_done_without_prior_added_creates_block() -> None:
    # A done event with no preceding added still creates + finalizes the
    # block (pi getOrCreateSlot, shared.ts:352-354).
    events = [
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "m",
                "content": [{"type": "output_text", "text": "late"}],
            },
        },
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    state, out = await _run(events)
    assert isinstance(out[0], TextStartEvent)
    assert isinstance(out[1], TextEndEvent)
    assert state.content[0].text == "late"


# === streaming: partial snapshots carry response_id =========================


async def test_partial_snapshots_are_assistant_messages() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_99"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "m"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "hi"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {"type": "message", "id": "m", "content": [{"type": "output_text", "text": "hi"}]},
        },
        {"type": "response.completed", "response": {"id": "resp_99", "status": "completed"}},
    ]
    _, out = await _run(events)
    for ev in out:
        assert isinstance(ev.partial, AssistantMessage)
        assert ev.partial.response_id == "resp_99"


# === encrypted-reasoning fidelity: real SDK item omits wire-absent keys =====


async def test_real_reasoning_item_serialization_omits_null_keys() -> None:
    """A real ``ResponseReasoningItem`` must serialize like pi's JSON.stringify.

    pi calls ``JSON.stringify(item)`` (shared.ts:481), which OMITS undefined
    keys. The Python OpenAI SDK models wire-absent optionals (``content`` /
    ``status``) as ``None``; a naive ``model_dump(mode="json")`` would emit
    them as explicit JSON ``null``. On replay
    (:func:`convert_responses_messages`) those nulls are pushed verbatim into
    the Responses ``input`` array, and OpenAI 400s ("Invalid value: null").
    This drives a REAL SDK item (not a plain dict) through the finalize path
    to close that blind spot.
    """
    from openai.types.responses import ResponseReasoningItem
    from openai.types.responses.response_reasoning_item import Summary

    item = ResponseReasoningItem(
        id="rs_real",
        type="reasoning",
        summary=[Summary(type="summary_text", text="thought")],
        encrypted_content="ENC",
        content=None,
        status=None,
    )
    assert item.content is None and item.status is None

    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning", "id": "rs_real"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": item,
        },
        {
            "type": "response.completed",
            "response": {"id": "r", "status": "completed"},
        },
    ]
    state, _ = await _run(events)

    block = state.content[0]
    assert isinstance(block, ThinkingContent)
    assert block.thinking == "thought"

    # The stored signature must have NO null content/status keys (pi parity).
    stored = json.loads(block.thinking_signature)
    assert "content" not in stored
    assert "status" not in stored
    assert stored == {
        "id": "rs_real",
        "type": "reasoning",
        "summary": [{"text": "thought", "type": "summary_text"}],
        "encrypted_content": "ENC",
    }

    # And it replays as a clean reasoning input item — no null injection.
    assistant = AssistantMessage(
        content=[
            ThinkingContent(
                thinking="thought",
                thinking_signature=block.thinking_signature,
            )
        ],
        api="openai-responses",
        provider="openai",
        model="gpt-5",
    )
    converted = convert_responses_messages(
        _model(), Context(messages=[assistant])
    )
    reasoning_inputs = [
        m for m in converted if isinstance(m, dict) and m.get("type") == "reasoning"
    ]
    assert len(reasoning_inputs) == 1
    replayed = reasoning_inputs[0]
    assert "content" not in replayed
    assert "status" not in replayed
    assert replayed["encrypted_content"] == "ENC"


async def test_function_call_composite_id_absent_item_id_mirrors_pi() -> None:
    """An absent ``item.id`` yields ``call_id|undefined`` (pi JS coercion).

    pi builds the id with ``${item.call_id}|${item.id}`` (shared.ts:335); a
    missing ``item.id`` stringifies to the literal ``"undefined"`` in JS, so
    the Python port must do the same instead of a trailing-empty ``call_id|``.
    """
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "call_id": "call_1", "name": "f"},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "name": "f",
                "arguments": "{}",
            },
        },
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    state, _ = await _run(events)
    block = state.content[0]
    assert isinstance(block, ToolCallContent)
    assert block.tool_call_id == "call_1|undefined"
