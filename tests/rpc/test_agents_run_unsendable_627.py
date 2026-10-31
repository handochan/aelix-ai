"""#627 — RPC ``agents/run`` refuses a task holding a NUL or half of a
character before it resolves the profile, so a project profile's RPC
confirmation is never asked for a task that cannot run.

An RPC client's JSON carries both (``"\\u0000"``, ``"\\ud83d"``). The runtime
refuses such a task too (``runtime.spawn``), but only after this handler has
resolved the profile and, for a project profile, opened the confirmation:
measured by the round-1 review on ``1fb5d2ae``. The refusal is an invalid
parameter, raised as the handler's other parameter checks are.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.rpc.test_runtime_control_surfaces_594 import _delegating, _Subagents

BAD = {"nul": "\x00", "lone surrogate": "\ud83d"}


def _answering(plane: Any, events: list[dict[str, Any]]) -> None:
    def answer(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("method") == "select":
            asyncio.get_running_loop().call_soon(
                plane.ui.respond, {"id": event["id"], "value": event["options"][0]}
            )

    plane.ui.emit = answer


@pytest.mark.parametrize("project", [False, True], ids=["user", "project"])
@pytest.mark.parametrize("kind", sorted(BAD))
async def test_a_task_that_cannot_be_sent_is_refused_before_the_project_dialog(
    kind: str, project: bool
) -> None:
    subagents = _Subagents(project=project)
    harness, plane, events = _delegating(subagents)
    _answering(plane, events)
    try:
        await plane.handle("ui/enable", {"enabled": True})
        holds = "a NUL character" if kind == "nul" else "half of a character"
        with pytest.raises(ValueError, match=f"^The task holds {holds}") as caught:
            await plane.handle("agents/run", {"name": "rev", "task": f"a{BAD[kind]}b"})
        assert BAD[kind] not in str(caught.value)
        assert [e for e in events if e.get("method") == "select"] == []
        assert subagents.resolved == [], "the profile is not even looked up"
        assert subagents.spawned == []
    finally:
        await harness.dispose()


async def test_a_normal_task_still_reaches_the_project_dialog() -> None:
    subagents = _Subagents(project=True)
    harness, plane, events = _delegating(subagents)
    _answering(plane, events)
    try:
        await plane.handle("ui/enable", {"enabled": True})
        result = await plane.handle("agents/run", {"name": "rev", "task": "검토 🙂"})
        assert result["result"]["ok"] is True
        assert len([e for e in events if e.get("method") == "select"]) == 1
        assert subagents.spawned == ["검토 🙂"]
    finally:
        await harness.dispose()
