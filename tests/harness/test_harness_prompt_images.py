"""``prompt(images=...)`` must reach the MODEL, not merely be accepted.

Pi parity: ``agent-harness.ts:43-45`` builds every user message through
``createUserMessage(text, images)``, whose body is ``content.push(...images)``,
and ``prompt`` uses it via ``executeTurn`` (``:532``) exactly as ``steer``
(``:654``), ``followUp`` (``:660``) and ``nextTurn`` (``:665``) do.

WHY THIS FILE ASSERTS AT THE ``stream_fn`` BOUNDARY. Aelix's ``prompt`` accepted
``images``, threaded it through the input hook, and then built its
``UserMessage`` from the text alone — so the image was dropped one line after
the hook that is allowed to replace it. That is a parity regression, and it
survived a sprint whose whole subject was the rpc prompt path, because every
test in reach asserted that ``prompt`` had been CALLED with ``images=`` rather
than that the provider ever SAW them. Forwarding and delivery are different
claims; only the second one is the feature.

The sibling file ``test_harness_steer_follow_up_images.py`` can legitimately
assert on the queue, because for ``steer`` / ``follow_up`` the queue IS the
delivery. For ``prompt`` there is no queue, so the provider's context is the
only honest place to look.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
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


def _capturing_stream(seen: list[Context]) -> Any:
    """A provider stub that records the context it was handed."""

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(context)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _make_harness(seen: list[Context]) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_capturing_stream(seen),
        )
    )


def _user_messages(context: Context) -> list[UserMessage]:
    return [m for m in context.messages if isinstance(m, UserMessage)]


async def test_prompt_images_reach_the_provider() -> None:
    """THE REGRESSION TEST. The image must be in the context the model sees."""

    seen: list[Context] = []
    h = _make_harness(seen)
    try:
        img1 = ImageContent(mime_type="image/png", data="aaaa")
        img2 = ImageContent(mime_type="image/jpeg", data="bbbb")
        await h.prompt("look at these", images=[img1, img2])

        assert seen, "the provider was never called"
        users = _user_messages(seen[0])
        assert users, "no user message reached the provider"
        content = users[-1].content
        assert len(content) == 3, (
            "the image blocks never reached the model; content was "
            f"{[type(c).__name__ for c in content]}"
        )
        assert isinstance(content[0], TextContent)
        assert content[0].text == "look at these"
        # Appended AFTER the text, matching pi's ``content.push(...images)``
        # and aelix's own ``steer`` / ``follow_up``.
        assert content[1] is img1
        assert content[2] is img2
    finally:
        await h.dispose()


async def test_prompt_without_images_is_text_only() -> None:
    """The default path is unchanged — one block, no empty image list."""

    seen: list[Context] = []
    h = _make_harness(seen)
    try:
        await h.prompt("plain text")
        users = _user_messages(seen[0])
        assert len(users[-1].content) == 1
        assert isinstance(users[-1].content[0], TextContent)
    finally:
        await h.dispose()


async def test_prompt_with_empty_image_list_is_text_only() -> None:
    """``images=[]`` is falsy and must not append anything — same as ``steer``."""

    seen: list[Context] = []
    h = _make_harness(seen)
    try:
        await h.prompt("plain text", images=[])
        users = _user_messages(seen[0])
        assert len(users[-1].content) == 1
    finally:
        await h.dispose()


async def test_prompt_and_steer_agree_on_message_shape() -> None:
    """One helper in pi, so one shape here: text first, images appended.

    A structural cross-check, because the two paths are built separately in
    aelix and drifted apart once already.
    """

    img = ImageContent(mime_type="image/png", data="zzzz")

    seen: list[Context] = []
    h = _make_harness(seen)
    try:
        await h.prompt("same words", images=[img])
        prompt_content = _user_messages(seen[0])[-1].content

        await h.steer("same words", images=[img])
        steer_content = h._steering_queue._messages[0].content
    finally:
        await h.dispose()

    assert [type(c).__name__ for c in prompt_content] == [
        type(c).__name__ for c in steer_content
    ]
    assert prompt_content[1] is steer_content[1] is img
