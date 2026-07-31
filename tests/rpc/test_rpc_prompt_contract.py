"""The ``prompt`` turn contract: acceptance, rejection, and what carries images.

Pins the RPC-sprint repairs to ``_handle_prompt``. Before them:

* a prompt the harness rejected as busy was answered ``success: true`` and then
  never terminated, so a client waited out its full 60 s ``agent_end`` timeout
  for a turn that had never begun — pi documents the opposite contract
  (``rpc.md:76``: *"``success: false`` means the prompt was rejected before
  acceptance"*);
* ``cmd.images`` was dropped on the floor while ``steer``/``follow_up`` decoded
  theirs, though pi forwards images on all three (``rpc-mode.ts:382-386``);
* ``streamingBehavior`` was ignored entirely, so pi's own answer to a live turn
  — enqueue rather than reject — was unreachable over rpc.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

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
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandPrompt,
    RpcErrorResponse,
    RpcSuccessResponse,
)

# A 1x1 transparent PNG, base64. Small enough to inline, real enough to decode.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _busy_harness() -> tuple[AgentHarness, asyncio.Event]:
    """A harness whose turn hangs until the returned gate is set."""

    gate = asyncio.Event()

    async def slow_stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        await gate.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    return AgentHarness(AgentHarnessOptions(stream_fn=slow_stream)), gate


async def test_a_prompt_rejected_as_busy_is_reported_as_a_failure() -> None:
    """The headline repair: a rejected prompt must not be acked ``success``."""

    harness, gate = _busy_harness()
    try:
        accepted = await _handle_prompt(
            harness, RpcCommandPrompt(message="one", id="p1")
        )
        assert isinstance(accepted, RpcSuccessResponse)

        # Let the turn actually begin, so the harness is genuinely non-idle.
        await asyncio.sleep(0.05)
        assert harness.phase != "idle"

        rejected = await _handle_prompt(
            harness, RpcCommandPrompt(message="two", id="p2")
        )
    finally:
        gate.set()
        await harness.abort()

    assert isinstance(rejected, RpcErrorResponse), (
        "a prompt the harness will refuse must be answered success=false; "
        "acking it leaves the client waiting for an agent_end that never comes"
    )
    payload = rejected.to_json()
    assert payload["success"] is False
    # Correlated to the request that failed, not to the one that succeeded.
    assert payload["id"] == "p2"
    assert "busy" in payload["error"]


async def test_streaming_behavior_enqueues_instead_of_rejecting() -> None:
    """``streamingBehavior`` is pi's answer to a live turn: route, don't refuse."""

    harness, gate = _busy_harness()
    try:
        await _handle_prompt(harness, RpcCommandPrompt(message="one", id="p1"))
        await asyncio.sleep(0.05)
        assert harness.phase != "idle"

        steered = await _handle_prompt(
            harness,
            RpcCommandPrompt(message="steer me", id="p2", streaming_behavior="steer"),
        )
        followed = await _handle_prompt(
            harness,
            RpcCommandPrompt(message="later", id="p3", streaming_behavior="followUp"),
        )
        # Snapshot BEFORE abort(), which clears both queues by design.
        steer_queue = list(harness._steering_queue._messages)
        follow_queue = list(harness._follow_up_queue._messages)
    finally:
        gate.set()
        await harness.abort()

    assert isinstance(steered, RpcSuccessResponse)
    assert isinstance(followed, RpcSuccessResponse)

    # The response type alone pins NOTHING here: the pre-repair handler also
    # answered ``success`` for these, by dropping them into a fire-and-forget
    # task that the harness then refused as busy. What distinguishes the repair
    # is that the messages actually LANDED IN THE QUEUES, so assert that.
    def _texts(queue: list[Any]) -> list[str]:
        # ``content`` is a union; only TextContent carries ``.text``.
        return [
            getattr(block, "text", "")
            for message in queue
            for block in message.content
        ]

    steered_texts = _texts(steer_queue)
    follow_texts = _texts(follow_queue)
    assert "steer me" in steered_texts, (
        f"streamingBehavior='steer' must enqueue; steer queue held {steered_texts}"
    )
    assert "later" in follow_texts, (
        f"streamingBehavior='followUp' must enqueue; queue held {follow_texts}"
    )


async def test_prompt_forwards_images_to_the_harness() -> None:
    """``prompt`` carries images, as ``steer``/``follow_up`` already did."""

    harness, gate = _busy_harness()
    seen: dict[str, Any] = {}

    async def _capture(
        message: str, *, images: Any = None, source: str = "interactive"
    ) -> list[Any]:
        seen["message"] = message
        seen["images"] = images
        seen["source"] = source
        return []

    harness.prompt = _capture  # type: ignore[assignment]
    try:
        response = await _handle_prompt(
            harness,
            RpcCommandPrompt(
                message="look at this",
                id="p1",
                images=[{"mimeType": "image/png", "data": _PNG_B64}],
            ),
        )
        for task in list(harness._pending_tasks):
            await task
    finally:
        gate.set()

    assert isinstance(response, RpcSuccessResponse)
    assert seen["message"] == "look at this"
    assert seen["source"] == "rpc"
    assert seen["images"], "cmd.images must reach the harness, not be dropped"
    assert len(seen["images"]) == 1


async def test_a_malformed_image_is_rejected_before_acceptance() -> None:
    """A malformed image is a pre-acceptance rejection, and starts no turn.

    ``_decode_images`` validates the wire SHAPE, not the base64 payload — a
    missing ``mimeType``/``data`` raises ``ValueError`` (P-262), which the
    dispatcher renders as the pi-shaped error envelope. (It does not decode the
    data, so a bogus base64 string is accepted; that is the documented contract,
    not an oversight of this test.)

    The property worth pinning is the ORDERING: decoding happens before the task
    is created, so a rejected prompt starts no turn and therefore owes no
    terminator. Were the decode moved inside the task, this would surface as an
    acked prompt that never terminates — the very defect this module exists for.
    """

    harness, gate = _busy_harness()
    try:
        raised = False
        try:
            await _handle_prompt(
                harness,
                RpcCommandPrompt(
                    message="broken",
                    id="p1",
                    images=[{"data": _PNG_B64}],  # no 'mimeType'
                ),
            )
        except ValueError:
            raised = True
        assert raised, "a malformed image must not be silently accepted"
        assert harness.phase == "idle", "no turn may have been started"
        assert not harness._pending_tasks, "no prompt task may have been created"
    finally:
        gate.set()
        await harness.dispose()
