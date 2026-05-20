"""Sprint 6h₄a (ADR-0075, P-294/P-295/P-296) — harness
``get_user_messages_for_forking`` unit tests.

Pi parity: ``session.getUserMessagesForForking()``
(``agent-session.ts:2870-2885``). The Aelix port is async because
:meth:`Session.get_entries` is async (Pi sync ``sessionManager.getEntries``
→ Aelix ``await self._session.get_entries()``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness._fork_point import ForkPointInfo
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
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


def _make_harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            session=session,
        )
    )


async def test_session_none_returns_empty_list() -> None:
    """No session attached → ``[]``."""

    harness = _make_harness()
    try:
        result = await harness.get_user_messages_for_forking()
        assert result == []
    finally:
        await harness.dispose()


async def test_empty_entries_returns_empty_list() -> None:
    """Session with no entries → ``[]``."""

    session = Session(MemorySessionStorage())
    harness = _make_harness(session=session)
    try:
        result = await harness.get_user_messages_for_forking()
        assert result == []
    finally:
        await harness.dispose()


async def test_single_user_message_returns_one_fork_point() -> None:
    """Pi parity: a single user MessageEntry → 1 ForkPointInfo record."""

    session = Session(MemorySessionStorage())
    entry_id = await session.append_message(
        UserMessage(content=[TextContent(text="hello")])
    )
    harness = _make_harness(session=session)
    try:
        result = await harness.get_user_messages_for_forking()
        assert len(result) == 1
        assert isinstance(result[0], ForkPointInfo)
        assert result[0].entry_id == entry_id
        assert result[0].text == "hello"
    finally:
        await harness.dispose()


async def test_mixed_roles_only_user_kept() -> None:
    """Pi parity: assistant + toolResult messages are filtered out."""

    session = Session(MemorySessionStorage())
    u_id = await session.append_message(
        UserMessage(content=[TextContent(text="u")])
    )
    await session.append_message(
        AssistantMessage(content=[TextContent(text="a")])
    )
    await session.append_message(
        ToolResultMessage(tool_call_id="t", content=[TextContent(text="r")])
    )
    harness = _make_harness(session=session)
    try:
        result = await harness.get_user_messages_for_forking()
        assert len(result) == 1
        assert result[0].entry_id == u_id
        assert result[0].text == "u"
    finally:
        await harness.dispose()


async def test_non_message_entries_filtered_out() -> None:
    """Pi parity: ``entry.type !== 'message'`` entries are skipped."""

    session = Session(MemorySessionStorage())
    u_id = await session.append_message(
        UserMessage(content=[TextContent(text="u1")])
    )
    # LabelEntry + SessionInfoEntry interleaved — only MessageEntry counts.
    await session.append_label(u_id, "label")
    await session.append_session_name("name")
    u2_id = await session.append_message(
        UserMessage(content=[TextContent(text="u2")])
    )
    harness = _make_harness(session=session)
    try:
        result = await harness.get_user_messages_for_forking()
        # Only the 2 user MessageEntry records survive.
        assert [p.entry_id for p in result] == [u_id, u2_id]
        assert [p.text for p in result] == ["u1", "u2"]
    finally:
        await harness.dispose()


async def test_empty_user_text_is_skipped() -> None:
    """Pi parity: ``if (text)`` skips entries whose extracted text is ``""``.

    A user message with only an :class:`ImageContent` block produces
    empty text → not emitted as a fork point.
    """

    session = Session(MemorySessionStorage())
    # User message with ONLY an image (no text) → empty extracted text.
    await session.append_message(
        UserMessage(
            content=[ImageContent(mime_type="image/png", data="AAA")]
        )
    )
    # Normal user message with text → kept.
    u_id = await session.append_message(
        UserMessage(content=[TextContent(text="kept")])
    )
    harness = _make_harness(session=session)
    try:
        result = await harness.get_user_messages_for_forking()
        assert len(result) == 1
        assert result[0].entry_id == u_id
        assert result[0].text == "kept"
    finally:
        await harness.dispose()


async def test_multi_block_text_concatenated() -> None:
    """Pi parity: multi-block content collapses to concatenated TextContent."""

    session = Session(MemorySessionStorage())
    await session.append_message(
        UserMessage(
            content=[
                TextContent(text="a"),
                ImageContent(mime_type="image/png", data="AAA"),
                TextContent(text="b"),
            ]
        )
    )
    harness = _make_harness(session=session)
    try:
        result = await harness.get_user_messages_for_forking()
        assert len(result) == 1
        assert result[0].text == "ab"
    finally:
        await harness.dispose()


async def test_entries_iteration_order_preserved() -> None:
    """Pi parity: the result preserves the source entry order."""

    session = Session(MemorySessionStorage())
    ids: list[str] = []
    for i in range(3):
        ids.append(
            await session.append_message(
                UserMessage(content=[TextContent(text=f"u{i}")])
            )
        )
    harness = _make_harness(session=session)
    try:
        result = await harness.get_user_messages_for_forking()
        assert [p.entry_id for p in result] == ids
        assert [p.text for p in result] == ["u0", "u1", "u2"]
    finally:
        await harness.dispose()
