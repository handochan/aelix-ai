"""Sprint 6h₂ (ADR-0071, P-246/P-251) — harness ``steer`` / ``follow_up``
with optional ``images`` parameter.

Pi parity: ``rpc-mode.ts:528-536`` ``session.steer(message, images)`` and
``session.followUp(message, images)``. The Aelix harness amends the
Sprint 3b ``steer(text)`` / ``follow_up(text)`` signatures to accept
``images: list[ImageContent] | None = None`` so the enqueued
:class:`UserMessage` content carries both the :class:`TextContent` and
the supplied :class:`ImageContent` blocks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import QueueUpdateHookEvent
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
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


def _stream() -> Any:
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
            stream_fn=_stream(),
        )
    )


async def test_steer_text_only_enqueues_user_message() -> None:
    """Existing ``steer(text)`` callers still work — content is a single
    :class:`TextContent` block.
    """

    h = _make_harness()
    try:
        await h.steer("hello")
        queued = h._steering_queue._messages
        assert len(queued) == 1
        msg = queued[0]
        assert isinstance(msg, UserMessage)
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)
        assert msg.content[0].text == "hello"
    finally:
        await h.dispose()


async def test_steer_with_images_appends_image_blocks() -> None:
    """Sprint 6h₂ (P-246): ``images`` blocks are appended after the text."""

    h = _make_harness()
    try:
        img1 = ImageContent(mime_type="image/png", data="aaaa")
        img2 = ImageContent(mime_type="image/jpeg", data="bbbb")
        await h.steer("look at these", images=[img1, img2])
        msg = h._steering_queue._messages[0]
        assert isinstance(msg, UserMessage)
        assert len(msg.content) == 3
        assert isinstance(msg.content[0], TextContent)
        assert msg.content[0].text == "look at these"
        assert msg.content[1] is img1
        assert msg.content[2] is img2
    finally:
        await h.dispose()


async def test_steer_with_none_images_falls_back_to_text_only() -> None:
    """Sprint 6h₂ (P-246): ``images=None`` is the text-only path."""

    h = _make_harness()
    try:
        await h.steer("plain", images=None)
        msg = h._steering_queue._messages[0]
        assert isinstance(msg, UserMessage)
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)
    finally:
        await h.dispose()


async def test_follow_up_text_only_enqueues_user_message() -> None:
    h = _make_harness()
    try:
        await h.follow_up("hi")
        queued = h._follow_up_queue._messages
        assert len(queued) == 1
        msg = queued[0]
        assert isinstance(msg, UserMessage)
        assert msg.content[0].text == "hi"  # type: ignore[union-attr]
    finally:
        await h.dispose()


async def test_follow_up_with_images_appends_image_blocks() -> None:
    h = _make_harness()
    try:
        img = ImageContent(mime_type="image/png", data="cccc")
        await h.follow_up("see this", images=[img])
        msg = h._follow_up_queue._messages[0]
        assert isinstance(msg, UserMessage)
        assert len(msg.content) == 2
        assert msg.content[1] is img
    finally:
        await h.dispose()


async def test_steer_emits_queue_update() -> None:
    """Pi parity (P-4): enqueue paths emit ``queue_update``."""

    h = _make_harness()
    seen: list[QueueUpdateHookEvent] = []
    h.hooks.on("queue_update", lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]
    try:
        await h.steer("hi", images=[ImageContent(mime_type="image/png", data="zz")])
        assert len(seen) == 1
        assert len(seen[0].steer) == 1
    finally:
        await h.dispose()


async def test_follow_up_emits_queue_update() -> None:
    h = _make_harness()
    seen: list[QueueUpdateHookEvent] = []
    h.hooks.on("queue_update", lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]
    try:
        await h.follow_up("hi")
        assert len(seen) == 1
        assert len(seen[0].follow_up) == 1
    finally:
        await h.dispose()
