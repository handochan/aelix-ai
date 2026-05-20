"""Sprint 6h₄a (ADR-0075, P-297/P-298) — harness
``get_last_assistant_text`` unit tests.

Pi parity: ``session.getLastAssistantText()``
(``agent-session.ts:3059-3081``). Reverse-walk
``self._state.messages`` skipping aborted-empty assistant messages,
concatenate the :class:`TextContent` blocks, return trimmed text or
:data:`None` on empty.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    UserMessage,
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


def _make_harness(initial_messages: list[Any] | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            initial_messages=initial_messages or [],
        )
    )


async def test_no_messages_returns_none() -> None:
    """Pi parity: empty message list → :data:`None`."""

    harness = _make_harness()
    try:
        assert harness.get_last_assistant_text() is None
    finally:
        await harness.dispose()


async def test_only_user_messages_returns_none() -> None:
    """Pi parity: no assistant in history → :data:`None`."""

    harness = _make_harness(
        initial_messages=[
            UserMessage(content=[TextContent(text="u1")]),
            UserMessage(content=[TextContent(text="u2")]),
        ]
    )
    try:
        assert harness.get_last_assistant_text() is None
    finally:
        await harness.dispose()


async def test_single_assistant_text_block() -> None:
    """Pi parity: single ``[TextContent("hi")]`` → ``"hi"``."""

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="hi")],
                stop_reason="end_turn",
            )
        ]
    )
    try:
        assert harness.get_last_assistant_text() == "hi"
    finally:
        await harness.dispose()


async def test_mixed_blocks_only_text_concatenated() -> None:
    """Pi parity: ``[Text("a"), Thinking("..."), Text("b")]`` → ``"ab"``.

    The Pi algorithm filters to ``content.type === "text"`` only —
    thinking blocks and toolCall blocks are skipped.
    """

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[
                    TextContent(text="a"),
                    ThinkingContent(thinking="..."),
                    TextContent(text="b"),
                ],
                stop_reason="end_turn",
            )
        ]
    )
    try:
        assert harness.get_last_assistant_text() == "ab"
    finally:
        await harness.dispose()


async def test_aborted_empty_assistant_skipped() -> None:
    """Pi parity: ``stopReason === "aborted" && content.length === 0`` →
    the entry is skipped; the reverse-walk falls back to the previous
    emittable assistant.
    """

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="earlier")],
                stop_reason="end_turn",
            ),
            UserMessage(content=[TextContent(text="u")]),
            AssistantMessage(
                content=[], stop_reason="aborted"
            ),
        ]
    )
    try:
        # The aborted-empty entry is skipped → "earlier".
        assert harness.get_last_assistant_text() == "earlier"
    finally:
        await harness.dispose()


async def test_aborted_with_content_not_skipped() -> None:
    """Pi parity: aborted assistants with non-empty content ARE returned.

    The Pi guard is ``aborted && content.length === 0`` — both must hold.
    """

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="partial")],
                stop_reason="aborted",
            )
        ]
    )
    try:
        assert harness.get_last_assistant_text() == "partial"
    finally:
        await harness.dispose()


async def test_multiple_assistants_returns_last_emittable() -> None:
    """Pi parity: reverse-walk picks the LAST emittable assistant."""

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="first")],
                stop_reason="end_turn",
            ),
            AssistantMessage(
                content=[TextContent(text="middle")],
                stop_reason="end_turn",
            ),
            AssistantMessage(
                content=[TextContent(text="last")],
                stop_reason="end_turn",
            ),
        ]
    )
    try:
        assert harness.get_last_assistant_text() == "last"
    finally:
        await harness.dispose()


async def test_whitespace_only_returns_none() -> None:
    """Pi parity: ``text.trim() || undefined`` — whitespace trims to ``""``."""

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="   \n  ")],
                stop_reason="end_turn",
            )
        ]
    )
    try:
        assert harness.get_last_assistant_text() is None
    finally:
        await harness.dispose()


async def test_leading_trailing_whitespace_trimmed() -> None:
    """Pi parity: ``"  hi  "`` trims to ``"hi"``."""

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[TextContent(text="  hi  ")],
                stop_reason="end_turn",
            )
        ]
    )
    try:
        assert harness.get_last_assistant_text() == "hi"
    finally:
        await harness.dispose()


async def test_only_tool_call_content_returns_none() -> None:
    """Pi parity: assistant w/ only :class:`ToolCallContent` blocks →
    concatenated text is ``""`` → trimmed-empty → :data:`None`.
    """

    harness = _make_harness(
        initial_messages=[
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="c", tool_name="t", input={}
                    )
                ],
                stop_reason="toolUse",
            )
        ]
    )
    try:
        assert harness.get_last_assistant_text() is None
    finally:
        await harness.dispose()
