"""#334 round 3 — Codex cross-review finding C4, and #314's shape beside it.

A prompt cancelled while its release flush awaits a session append goes idle
(#321) with the writes it had not attempted still queued. C4 asked: does a
newer write overtake them, and does ``dispose()`` lose them? #314's shape is
printed to show this change leaves it as it was.

    cd <tree> && uv run --no-sync python .omc/specs/334-c4-probe.py

Run from the tree root (it imports ``tests/``'s helpers). To measure another
``core.py``, put that tree's ``packages/aelix-agent-core/src`` first on
``PYTHONPATH``.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "tests")

import aelix_agent_core.harness.core as core  # noqa: E402
from aelix_agent_core.session import (  # noqa: E402
    JsonlSessionStorage,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage  # noqa: E402
from aelix_ai.streaming import AssistantEndEvent  # noqa: E402
from test_harness_prompt_holds_the_turn_through_its_tail import (  # noqa: E402
    _parked_release_flush,
)


async def _levels(session: Session) -> list[str | None]:
    return [
        e.thinking_level
        for e in await session.get_entries()
        if e.type == "thinking_level_change"
    ]


async def _cancelled() -> tuple[core.AgentHarness, Session]:
    h, session, first, release = await _parked_release_flush()
    await h.set_thinking_level("medium")
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    release.set()
    return h, session


async def c4() -> None:
    h, session = await _cancelled()
    print(
        "C4 after the cancel      : phase",
        repr(h.phase),
        "pending",
        [w.thinking_level for w in h._pending_session_writes],  # type: ignore[union-attr]
    )
    await h.set_thinking_level("low")
    print("C4 (a) idle 'low'        : on disk", await _levels(session))
    await h.prompt("next")
    print(
        "C4 (a) after next prompt : on disk",
        await _levels(session),
        "restored",
        (await session.build_context()).thinking_level,
    )

    h, session = await _cancelled()
    await h.dispose()
    print(
        "C4 (b) dispose()         : pending",
        [type(w).__name__ for w in h._pending_session_writes],
        "on disk",
        await _levels(session),
    )


async def shape_314() -> None:
    """``setter_race`` from the #301 cross-review probe (issue #314)."""

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), str(folder / "s.jsonl"), cwd=str(folder), session_id="p"
        )
        session = Session(storage)
        stream_entered = asyncio.Event()
        hook_entered = asyncio.Event()
        release_hook = asyncio.Event()

        async def slow_stream(*_args: object):  # type: ignore[no-untyped-def]
            stream_entered.set()
            await asyncio.Event().wait()
            yield AssistantEndEvent(
                message=AssistantMessage(content=[], stop_reason="end_turn")
            )

        h = core.AgentHarness(
            core.AgentHarnessOptions(session=session, stream_fn=slow_stream)
        )

        async def hook(event, _ctx):  # type: ignore[no-untyped-def]
            if event.level == "high":
                hook_entered.set()
                await release_hook.wait()

        h.hooks.on("thinking_level_select", hook)  # type: ignore[call-overload]
        turn = asyncio.create_task(h.prompt("prompt"))
        await asyncio.wait_for(stream_entered.wait(), 2)
        setter = asyncio.create_task(h.set_thinking_level("high"))
        await asyncio.wait_for(hook_entered.wait(), 2)
        await h.set_thinking_level("low")
        await h.abort()
        await asyncio.wait_for(turn, 2)
        release_hook.set()
        await asyncio.wait_for(setter, 2)
        print(
            "#314 shape               : on disk",
            await _levels(session),
            "live",
            h._state.thinking_level,
            "restored",
            (await session.build_context()).thinking_level,
        )


async def main() -> None:
    print("core.py:", core.__file__)
    await c4()
    await shape_314()


if __name__ == "__main__":
    asyncio.run(main())
