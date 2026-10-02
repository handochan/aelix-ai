"""#367 verify round 6, B1 — ``LateRouteHold.apply`` holds the turns while it runs.

Every path that applies a late-provider hold calls ``apply`` (the launch, a
rebuild's settle, ``/agents use``). Inside its ``set_model(placeholder)`` a
``model_select`` handler can ``set_model`` onto the late provider and trigger a
turn; on 343e75cd ``/agents use`` (and the settle of a rebuild the factory did
not hold) applied the hold with turns open, and that turn went out before the
placeholder was put back. ``apply`` now holds the turns for its duration - the
trigger runs as a refused turn - and waits that turn out before it returns, so
the next prompt does not find the harness busy. A gate the caller already
holds stays up for the caller to lift; while ``apply`` runs its reason is the
hold's (verify round 7), and the caller's is restored on exit.

A real ``AgentHarness`` with a recording ``stream_fn``; the "handler" wraps
``set_model`` as a ``model_select`` handler that answers the placeholder would.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.cli.runtime_bootstrap import HOLD_GATE, LateRouteHold

_REASON = "The launch model names a late provider."
#: A refused turn ends at once; a sabotaged gate (a trigger let through,
#: dropped or queued) leaves ``apply`` waiting, and the row fails by this bound.
_BOUND = 5
_LATE = Model(
    id="m1", provider="late", api="openai-completions", base_url="http://127.0.0.1:9/late"
)


def _harness(seen: list[Context]) -> AgentHarness:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(context)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="mock", provider="mock"), stream_fn=fn)
    )
    real_set_model = harness.set_model

    async def _answering_set_model(model: Any) -> None:
        # What a ``model_select`` handler answering the placeholder does: its
        # own ``set_model`` onto the late provider, then a triggered turn,
        # returning at once (the turn task has not claimed the harness yet).
        await real_set_model(model)
        if getattr(model, "api", "") == "unknown":
            await real_set_model(_LATE)
            action: Any = harness._action_send_message
            action(UserMessage(content=[TextContent(text="ms-prompt")]), trigger_turn=True)

    harness.set_model = _answering_set_model  # type: ignore[method-assign]
    return harness


def _refused_answers(harness: AgentHarness) -> list[str]:
    return [
        m.error_message or ""
        for m in harness.messages
        if isinstance(m, AssistantMessage) and m.stop_reason == "error"
    ]


@pytest.mark.parametrize("caller_gate", [None, "the caller's own gate"])
async def test_apply_holds_turns_and_waits_out_the_refused_turn(caller_gate: str | None) -> None:
    """``None``: no gate up (``/agents use``, a rebuild the factory did not
    hold) - ``apply`` puts its own up (``HOLD_GATE`` + the reason) and lifts it.
    A caller's gate (the launch's, a held rebuild's) is left up for the caller.
    Either way nothing streams, the harness ends on the placeholder, and the
    refused turn has already ended when ``apply`` returns.

    #367 verify round 7, V-B1: the turn refused DURING ``apply`` answers with
    the hold's reason whoever calls it. On f3fd162c a caller's gate kept its
    own reason - the launch's "No turn runs while this session's session_start
    handlers run. The launch route is not decided yet: ...", printed under the
    Warning that had just decided it. ``apply`` now swaps its reason in for its
    duration and restores the caller's on exit, and the gate is never down in
    between (every ``hold_turns`` call while it runs names a reason)."""

    seen: list[Context] = []
    harness = _harness(seen)
    hold = LateRouteHold(Model(id="m1", provider="late"), _REASON)
    if caller_gate is not None:
        harness.hold_turns(caller_gate)
    calls: list[str | None] = []
    real_hold_turns = harness.hold_turns

    def _spy(reason: str | None) -> None:
        calls.append(reason)
        real_hold_turns(reason)

    harness.hold_turns = _spy  # type: ignore[method-assign]
    assert await asyncio.wait_for(hold.apply(harness), timeout=_BOUND) is None
    assert seen == []
    assert hold.holds(harness.state.model)
    assert (harness.turns_held, harness.is_idle) == (caller_gate, True)
    assert _refused_answers(harness) == [f"{HOLD_GATE} {_REASON}"]
    assert calls == [f"{HOLD_GATE} {_REASON}", caller_gate]


async def test_a_set_model_while_apply_waits_is_undone_by_the_check() -> None:
    """The check comes last. A ``model_select`` handler triggers a turn
    (refused: the gate is up) and spawns a task that calls ``set_model`` onto
    the late provider while that refused turn runs - inside ``apply``'s wait.
    fix7's first cut checked before waiting: the harness then left ``apply`` on
    the late provider with the gate coming down (verify5's
    ``o-task-set-trigger-rpc`` moved its ``set_model`` ahead of the launch's
    Warning). Checked after the wait, it ends on the placeholder and nothing
    streams."""

    seen: list[Context] = []
    harness = _harness(seen)
    hold = LateRouteHold(Model(id="m1", provider="late"), _REASON)
    real_set_model = AgentHarness.set_model.__get__(harness)
    action: Any = harness._action_send_message

    async def _later() -> None:
        while harness.is_idle:  # until the refused turn has claimed the harness
            await asyncio.sleep(0)
        await real_set_model(_LATE)
        moved.append(harness.is_idle)

    tasks: list[asyncio.Task[None]] = []
    moved: list[bool] = []

    async def _spawning_set_model(model: Any) -> None:
        await real_set_model(model)
        if getattr(model, "api", "") == "unknown":
            action(UserMessage(content=[TextContent(text="ms-prompt")]), trigger_turn=True)
            tasks.append(asyncio.get_running_loop().create_task(_later()))

    harness.set_model = _spawning_set_model  # type: ignore[method-assign]
    assert await asyncio.wait_for(hold.apply(harness), timeout=_BOUND) is None
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=_BOUND)
    # The task moved the harness while the refused turn still ran: inside
    # ``apply``'s wait, before its check.
    assert moved == [False]
    assert seen == []
    assert hold.holds(harness.state.model)
    assert (harness.turns_held, harness.is_idle) == (None, True)
