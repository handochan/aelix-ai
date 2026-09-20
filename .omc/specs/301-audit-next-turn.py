#!/usr/bin/env python3
"""#301's written audit: where ELSE does the detach-then-await shape live?

ADR-0245 originally claimed the shape appeared nowhere else in
``harness/core.py``. It does. ``prompt()`` detaches the next-turn queue and
then awaits before it uses what it detached::

    core.py:1289-1290   drained_next = self._next_turn_queue
                        self._next_turn_queue = []
    core.py:1295-1296   if drained_next:
                            await self._emit_queue_update()
    core.py:1303        prompts.extend(drained_next)

``_emit_queue_update`` (``core.py:2294-2309``) converts any ``queue_update``
handler exception into a raised ``AgentHarnessError``, and a handler's
``error_mode`` defaults to ``"throw"`` (``harness/hooks.py:2082``). So a
throwing observer between :1296 and :1303 loses the drained messages exactly
the way #301 lost the pending writes.

This probe demonstrates it. It is an AUDIT, not a regression test: #301 does
not fix this queue (different queue, different lifetime, different remedy —
emit after :1303, or restore the queue on the raise). Filed separately.

Run from a worktree root::

    uv run --no-sync python .omc/specs/301-audit-next-turn.py

Expected output (measured 2026-09-21 on ``fix/301-pending-writes``)::

    queued           : ['QUEUED-BY-NEXT-TURN']
    prompt() raised  : AgentHarnessError('queue_update hook handler raised: observer blew up')
    queue after      : []
    reached stream_fn: 0 turns
    in state.messages: []
    VERDICT: the next_turn message exists NOWHERE after the raise: True
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

MARKER = "QUEUED-BY-NEXT-TURN"
seen_prompts: list[list[Any]] = []


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen_prompts.append(list(context.messages))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    return fn


def _texts(messages: list[Any]) -> list[str]:
    return [
        c.text
        for m in messages
        for c in getattr(m, "content", [])
        if hasattr(c, "text")
    ]


async def main() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))

    # Queue while idle. ``next_turn()`` emits ``queue_update`` itself, so the
    # faulty handler is registered afterwards — an extension that loads late,
    # or any observer that starts failing between two turns.
    await h.next_turn(MARKER)
    print("queued           :", _texts(h._next_turn_queue))

    def boom(event: Any, _ctx: Any) -> None:
        raise RuntimeError("observer blew up")

    h.hooks.on("queue_update", boom)  # type: ignore[arg-type]

    raised: BaseException | None = None
    try:
        await h.prompt("live user text")
    except BaseException as exc:  # noqa: BLE001
        raised = exc

    print("prompt() raised  :", repr(raised))
    print("queue after      :", _texts(h._next_turn_queue))
    print("reached stream_fn:", len(seen_prompts), "turns")
    print("in state.messages:", _texts(h.state.messages))

    nowhere = (
        raised is not None
        and not h._next_turn_queue
        and MARKER not in _texts(h.state.messages)
        and not any(MARKER in _texts(p) for p in seen_prompts)
    )
    print("VERDICT: the next_turn message exists NOWHERE after the raise:", nowhere)


if __name__ == "__main__":
    asyncio.run(main())
