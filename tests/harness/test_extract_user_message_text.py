"""Sprint 6h₄a (ADR-0075, P-296) — harness ``_extract_user_message_text`` unit tests.

Pi parity: ``_extractUserMessageText`` (``agent-session.ts:2887-2896``).
Pi accepts ``string | Array<{type, text?}>``. Aelix's
:class:`UserMessage.content` is always
``list[TextContent | ImageContent]``; the string branch stays as
defensive parity (unreachable in production).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
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
                content=[TextContent(text="ok")], stop_reason="end_turn"
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


async def test_empty_list_returns_empty_string() -> None:
    """Pi parity: ``[]`` → ``""``."""

    harness = _make_harness()
    try:
        assert harness._extract_user_message_text([]) == ""
    finally:
        await harness.dispose()


async def test_two_text_blocks_concatenated() -> None:
    """Pi parity: ``[Text("a"), Text("b")]`` → ``"ab"``."""

    harness = _make_harness()
    try:
        content = [TextContent(text="a"), TextContent(text="b")]
        assert harness._extract_user_message_text(content) == "ab"
    finally:
        await harness.dispose()


async def test_text_interleaved_with_image_only_text_kept() -> None:
    """Pi parity: image blocks filtered out, text blocks concatenated."""

    harness = _make_harness()
    try:
        content = [
            TextContent(text="a"),
            ImageContent(mime_type="image/png", data="AAA"),
            TextContent(text="b"),
        ]
        assert harness._extract_user_message_text(content) == "ab"
    finally:
        await harness.dispose()


async def test_all_image_blocks_returns_empty_string() -> None:
    """Pi parity: a list of only image blocks → ``""``."""

    harness = _make_harness()
    try:
        content = [
            ImageContent(mime_type="image/png", data="A"),
            ImageContent(mime_type="image/jpeg", data="B"),
        ]
        assert harness._extract_user_message_text(content) == ""
    finally:
        await harness.dispose()


async def test_string_input_returned_as_is() -> None:
    """Pi parity (defensive): ``typeof content === "string" → content``.

    Pi accepts string-or-array; Aelix's :class:`UserMessage.content` is
    always a list, but the string branch is preserved as Pi-byte-for-byte
    defense in case the underlying message shape ever drifts.
    """

    harness = _make_harness()
    try:
        assert harness._extract_user_message_text("hello") == "hello"
    finally:
        await harness.dispose()


async def test_non_list_non_string_returns_empty_string() -> None:
    """Aelix-additive defense: an unexpected type → ``""`` (no exception)."""

    harness = _make_harness()
    try:
        assert harness._extract_user_message_text(None) == ""
        assert harness._extract_user_message_text(42) == ""
        assert harness._extract_user_message_text({"text": "no"}) == ""
    finally:
        await harness.dispose()
