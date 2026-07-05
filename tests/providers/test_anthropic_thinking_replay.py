"""ADR-0190 — Anthropic thinking-block replay serialization + shared routing.

Covers the *replay* half of the thinking-replay core (the capture half lives
in ``test_anthropic_stream_events.py``):

* (C) :func:`_content_blocks_to_anthropic`'s exact 4-way ``ThinkingContent``
  order — redacted / empty-skip / unsigned-downgrade / signed-keep
  (pi ``anthropic.ts:1056-1080``).
* (D) routing round-trip through :func:`build_params` → the shared
  ``_transform_messages.transform_messages`` pass: same-model signed thinking
  survives as ``{type:"thinking",signature}``; a cross-model (provenance
  mismatched) block is downgraded to text; errored turns drop and orphan
  tool-calls synthesize a user tool_result on the Anthropic path.
"""

from __future__ import annotations

from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    UserMessage,
)
from aelix_ai.providers._anthropic_transforms import (
    _content_blocks_to_anthropic,
    build_params,
)
from aelix_ai.streaming import Model


def _model() -> Model:
    return Model(api="anthropic-messages", id="claude-x", provider="anthropic")


# === (C) _content_blocks_to_anthropic — 4-way ThinkingContent order ===


def test_replay_redacted_thinking_serializes_as_redacted_thinking() -> None:
    out = _content_blocks_to_anthropic(
        [
            ThinkingContent(
                thinking="[Reasoning redacted]",
                thinking_signature="OPAQUE_PAYLOAD",
                redacted=True,
            )
        ]
    )
    assert out == [{"type": "redacted_thinking", "data": "OPAQUE_PAYLOAD"}]


def test_replay_empty_thinking_is_skipped() -> None:
    out = _content_blocks_to_anthropic(
        [ThinkingContent(thinking="   ", thinking_signature="")]
    )
    assert out == []


def test_replay_unsigned_nonempty_thinking_downgrades_to_text() -> None:
    # Unconditional "allowEmptySignature" downgrade — NOT compat-gated.
    out = _content_blocks_to_anthropic(
        [ThinkingContent(thinking="unsigned musing", thinking_signature="")]
    )
    assert out == [{"type": "text", "text": "unsigned musing"}]


def test_replay_signed_thinking_serializes_as_thinking() -> None:
    out = _content_blocks_to_anthropic(
        [ThinkingContent(thinking="deep thought", thinking_signature="SIG")]
    )
    assert out == [
        {"type": "thinking", "thinking": "deep thought", "signature": "SIG"}
    ]


# === (D) build_params routing round-trip through the shared transform ===


def test_same_model_signed_thinking_survives_as_thinking() -> None:
    """A same-model signed thinking block round-trips as ``{type:thinking}``."""

    model = _model()
    same = AssistantMessage(
        content=[
            ThinkingContent(thinking="reasoned", thinking_signature="SIGX"),
            TextContent(text="answer"),
        ],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-x",
        stop_reason="end_turn",
    )
    params = build_params(model, "", [same], [])
    msgs = params["messages"]
    assert msgs[0]["role"] == "assistant"
    contents = msgs[0]["content"]
    assert {
        "type": "thinking",
        "thinking": "reasoned",
        "signature": "SIGX",
    } in contents


def test_same_model_redacted_thinking_survives_as_redacted_thinking() -> None:
    """(b) A same-model redacted block round-trips as ``{type:redacted_thinking}``.

    ADR-0190: redacted thinking survives only on same-model
    (``_transform_messages`` line 175-178), then
    :func:`_content_blocks_to_anthropic` serializes it back to
    ``{type:redacted_thinking, data}``.
    """

    model = _model()
    same = AssistantMessage(
        content=[
            ThinkingContent(
                thinking="[Reasoning redacted]",
                thinking_signature="OPAQUE",
                redacted=True,
            ),
            TextContent(text="answer"),
        ],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-x",
        stop_reason="end_turn",
    )
    params = build_params(model, "", [same], [])
    contents = params["messages"][0]["content"]
    assert {"type": "redacted_thinking", "data": "OPAQUE"} in contents


def test_cross_model_redacted_thinking_is_dropped() -> None:
    """(c) A cross-model redacted block is DROPPED (not downgraded to text).

    ADR-0190: the shared transform keeps redacted blocks only on same-model;
    a provenance mismatch drops them entirely (``_transform_messages`` line
    175-178 — no ``else`` text fallback for the redacted arm). Assert neither a
    ``redacted_thinking`` nor a ``thinking`` dict survives for that turn.
    """

    model = _model()
    cross = AssistantMessage(
        content=[
            ThinkingContent(
                thinking="[Reasoning redacted]",
                thinking_signature="OPAQUE",
                redacted=True,
            ),
            TextContent(text="answer"),
        ],
        api="openai-completions",
        provider="openai",
        model="gpt-4",
        stop_reason="end_turn",
    )
    params = build_params(model, "", [cross], [])
    contents = params["messages"][0]["content"]
    assert all(
        not (
            isinstance(b, dict)
            and b.get("type") in ("redacted_thinking", "thinking")
        )
        for b in contents
    )


def test_same_model_unsigned_thinking_downgrades_to_text() -> None:
    """(a) A same-model UNSIGNED non-empty block downgrades to a text block.

    ADR-0190: the unconditional ``allowEmptySignature`` downgrade in
    :func:`_content_blocks_to_anthropic` rewrites a signature-less thinking
    block to ``{type:text}`` — no ``thinking`` dict survives.
    """

    model = _model()
    same = AssistantMessage(
        content=[
            ThinkingContent(thinking="unsigned musing", thinking_signature=""),
            TextContent(text="answer"),
        ],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-x",
        stop_reason="end_turn",
    )
    params = build_params(model, "", [same], [])
    contents = params["messages"][0]["content"]
    assert {"type": "text", "text": "unsigned musing"} in contents
    assert all(
        not (isinstance(b, dict) and b.get("type") == "thinking")
        for b in contents
    )


def test_cross_model_signed_thinking_downgraded_to_text() -> None:
    """A cross-model (provenance mismatch) thinking block converts to text."""

    model = _model()
    cross = AssistantMessage(
        content=[
            ThinkingContent(thinking="reasoned", thinking_signature="SIGX")
        ],
        api="openai-completions",
        provider="openai",
        model="gpt-4",
        stop_reason="end_turn",
    )
    params = build_params(model, "", [cross], [])
    contents = params["messages"][0]["content"]
    # Shared cross-model pass rewrote thinking → TextContent; no thinking dict.
    assert {"type": "text", "text": "reasoned"} in contents
    assert all(b["type"] != "thinking" for b in contents)


def test_errored_assistant_turn_is_dropped_on_anthropic_path() -> None:
    """An ``error`` stop_reason assistant turn is dropped by the shared pass."""

    model = _model()
    errored = AssistantMessage(
        content=[TextContent(text="partial")],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-x",
        stop_reason="error",
    )
    user = UserMessage(content=[TextContent(text="hi")])
    params = build_params(model, "", [errored, user], [])
    msgs = params["messages"]
    assert all(m["role"] != "assistant" for m in msgs)
    assert len(msgs) == 1 and msgs[0]["role"] == "user"


def test_orphan_tool_call_synthesizes_user_tool_result() -> None:
    """An unanswered tool call gets a synthetic user ``tool_result`` block."""

    model = _model()
    tool_turn = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="call_1", tool_name="run", input={"a": 1}
            )
        ],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-x",
        stop_reason="toolUse",
    )
    params = build_params(model, "", [tool_turn], [])
    msgs = params["messages"]
    assert any(m["role"] == "assistant" for m in msgs)
    tool_results = [
        b
        for m in msgs
        if m["role"] == "user"
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert tool_results, "orphan tool call should synthesize a tool_result"
    assert tool_results[0]["tool_use_id"] == "call_1"
    assert tool_results[0]["is_error"] is True
