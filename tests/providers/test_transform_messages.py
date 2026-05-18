"""Sprint 6b (Phase 4.2, §C) — ``transform_messages`` tests.

Pi parity: ``providers/transform-messages.ts`` (SHA 734e08e). Covers the
five behaviors the helper guarantees:

1. Image downgrade for non-vision models.
2. Tool-call id normalization + map propagation onto subsequent tool
   results.
3. Orphan tool-call synthesis (mid-conversation + at end).
4. Errored / aborted assistant message drop.
5. Cross-vs-same-model thinking handling (the latter is exercised
   defensively — Aelix's content union doesn't yet contain a thinking
   block, so the cross-model TextContent conversion is the path that
   actually fires today).
"""

from __future__ import annotations

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers._transform_messages import (
    NON_VISION_TOOL_IMAGE_PLACEHOLDER,
    NON_VISION_USER_IMAGE_PLACEHOLDER,
    transform_messages,
)
from aelix_ai.streaming import Model


def _model(*, vision: bool = False, provider: str = "openai") -> Model:
    return Model(
        api="openai-completions",
        id="gpt-4",
        provider=provider,
        input=["text", "image"] if vision else ["text"],
    )


def test_passthrough_user_message_when_no_changes_needed() -> None:
    msg = UserMessage(content=[TextContent(text="hi")])
    out = transform_messages([msg], _model(vision=True))
    assert len(out) == 1
    assert out[0] is msg or out[0] == msg


def test_image_downgrade_user_message() -> None:
    msg = UserMessage(
        content=[
            TextContent(text="look"),
            ImageContent(source="ZGF0YQ=="),
            TextContent(text="please"),
        ]
    )
    out = transform_messages([msg], _model(vision=False))
    assert len(out) == 1
    new = out[0]
    assert isinstance(new, UserMessage)
    # Image replaced with placeholder, surrounded by the original text.
    texts = [
        b.text for b in new.content if isinstance(b, TextContent)
    ]
    assert texts == ["look", NON_VISION_USER_IMAGE_PLACEHOLDER, "please"]
    assert not any(isinstance(b, ImageContent) for b in new.content)


def test_image_downgrade_tool_result_uses_tool_placeholder() -> None:
    tool_result = ToolResultMessage(
        tool_call_id="t1",
        content=[ImageContent(source="ZA==")],
    )
    out = transform_messages([tool_result], _model(vision=False))
    new = out[0]
    assert isinstance(new, ToolResultMessage)
    assert any(
        isinstance(b, TextContent)
        and b.text == NON_VISION_TOOL_IMAGE_PLACEHOLDER
        for b in new.content
    )


def test_image_kept_when_model_supports_vision() -> None:
    msg = UserMessage(content=[ImageContent(source="ZA==")])
    out = transform_messages([msg], _model(vision=True))
    assert isinstance(out[0], UserMessage)
    assert any(isinstance(b, ImageContent) for b in out[0].content)


def test_consecutive_images_collapse_to_single_placeholder() -> None:
    msg = UserMessage(
        content=[
            ImageContent(source="a"),
            ImageContent(source="b"),
            ImageContent(source="c"),
        ]
    )
    out = transform_messages([msg], _model(vision=False))
    placeholders = [
        b for b in out[0].content
        if isinstance(b, TextContent)
        and b.text == NON_VISION_USER_IMAGE_PLACEHOLDER
    ]
    assert len(placeholders) == 1


def test_tool_call_id_normalization_with_remap() -> None:
    def normalize(tc_id: str, _m: Model, _src: AssistantMessage) -> str:
        return tc_id[:5]

    assistant = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="call_long_id_456789", tool_name="echo"
            )
        ],
    )
    tool_result = ToolResultMessage(
        tool_call_id="call_long_id_456789",
        content=[TextContent(text="ok")],
    )
    out = transform_messages(
        [assistant, tool_result],
        _model(vision=True),
        normalize_tool_call_id=normalize,
    )
    assistant_out = out[0]
    tool_result_out = out[1]
    assert isinstance(assistant_out, AssistantMessage)
    assert isinstance(tool_result_out, ToolResultMessage)
    assert assistant_out.content[0].tool_call_id == "call_"  # type: ignore[attr-defined]
    # The tool result's id must follow the same remapping.
    assert tool_result_out.tool_call_id == "call_"


def test_tool_call_id_unchanged_when_no_normalizer() -> None:
    assistant = AssistantMessage(
        content=[ToolCallContent(tool_call_id="tc1", tool_name="x")]
    )
    out = transform_messages([assistant], _model())
    assert isinstance(out[0], AssistantMessage)
    assert out[0].content[0].tool_call_id == "tc1"  # type: ignore[attr-defined]


def test_orphan_tool_call_synthesized_before_user_message() -> None:
    assistant = AssistantMessage(
        content=[
            ToolCallContent(tool_call_id="orphan", tool_name="x")
        ],
    )
    user_next = UserMessage(content=[TextContent(text="hello again")])

    out = transform_messages([assistant, user_next], _model())

    assert len(out) == 3
    assert isinstance(out[0], AssistantMessage)
    synthetic = out[1]
    assert isinstance(synthetic, ToolResultMessage)
    assert synthetic.tool_call_id == "orphan"
    assert synthetic.is_error is True
    assert isinstance(synthetic.content[0], TextContent)
    assert "No result provided" in synthetic.content[0].text
    assert isinstance(out[2], UserMessage)


def test_orphan_tool_call_synthesized_at_end_of_conversation() -> None:
    assistant = AssistantMessage(
        content=[ToolCallContent(tool_call_id="t1", tool_name="x")]
    )
    out = transform_messages([assistant], _model())
    assert len(out) == 2
    assert isinstance(out[1], ToolResultMessage)
    assert out[1].tool_call_id == "t1"


def test_no_synthesis_when_tool_result_present() -> None:
    assistant = AssistantMessage(
        content=[ToolCallContent(tool_call_id="t1", tool_name="x")]
    )
    tool_result = ToolResultMessage(
        tool_call_id="t1",
        content=[TextContent(text="ok")],
    )
    out = transform_messages([assistant, tool_result], _model())
    assert len(out) == 2  # No synthetic appended.


def test_errored_assistant_message_dropped() -> None:
    bad = AssistantMessage(
        content=[TextContent(text="partial")], stop_reason="error"
    )
    user_next = UserMessage(content=[TextContent(text="retry")])
    out = transform_messages([bad, user_next], _model())
    # The error assistant message must NOT survive the pass.
    assert all(not isinstance(m, AssistantMessage) for m in out)
    assert len(out) == 1
    assert isinstance(out[0], UserMessage)


def test_aborted_assistant_message_dropped() -> None:
    bad = AssistantMessage(
        content=[TextContent(text="partial")], stop_reason="aborted"
    )
    out = transform_messages([bad], _model())
    assert out == []


def test_text_content_cloned_for_cross_model_replay() -> None:
    """Cross-model text blocks are re-emitted as fresh TextContent.

    Pi's mutation semantics rely on a fresh copy so downstream
    mutations don't ripple back; we assert the produced block at
    minimum equals the original (Aelix dataclasses are frozen).
    """

    assistant = AssistantMessage(
        content=[TextContent(text="hi")],
    )
    out = transform_messages([assistant], _model())
    assert isinstance(out[0], AssistantMessage)
    assert out[0].content[0] == TextContent(text="hi")
