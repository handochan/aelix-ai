"""#857 — ``children/notices`` says how many notices its turn placed, boundary drains included.

``placed`` stays what it was (ADR-0302 D3: what a field means is frozen): the
notices placed before the turn's first model call, one 64 KiB drain budget. The new
``placedInTurn`` counts every notice the turn placed, from its claim to its end:
the start's drain and each boundary's. It is the harness's own count —
``NoticeInbox.placed_count`` read when the turn claims the harness and again when
its run has settled (``PromptNoticesResult.placed_in_turn``) — so a placement by
another turn, before or after, is not in it (the lead's call between the counter
and the ``placed`` notifications; the counter is the inbox's, bound to the harness
that ran the turn).

Fails on ``0436193c``: the answer has no ``placedInTurn`` and
``PromptNoticesResult`` no ``placed_in_turn``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.inbox import NOTICE_DRAIN_BUDGET, Notice
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent
from aelix_coding_agent.rpc.control import RpcControlPlane

from tests.jobs.conftest import fast_service, make_harness, make_session


def _notice(n: int, body: str = "a job ended") -> Notice:
    return Notice(notice_id=f"n-{n}", custom_type="aelix.test", body=body)


async def _plane(tmp_path: Path, harness: Any = None) -> tuple[Any, RpcControlPlane]:
    service = fast_service()
    harness = harness or make_harness(await make_session(tmp_path))
    await service.bind(harness)
    return harness, RpcControlPlane(SimpleNamespace(harness=harness), jobs=service)


async def _placed_ids(harness: Any) -> list[str]:
    return [
        e.details.get("notice_id")
        for e in await harness.session.get_branch()
        if e.type == "custom_message"
    ]


async def test_two_budgets_in_one_call_answer_two_in_placed_in_turn_and_one_in_placed(
    tmp_path: Path,
) -> None:
    """The issue's case: two notices 100 B short of a budget each, one ``children/notices``."""

    harness, plane = await _plane(tmp_path)
    harness.post_notice(_notice(1, "x" * (NOTICE_DRAIN_BUDGET - 100)))
    harness.post_notice(_notice(2, "y" * (NOTICE_DRAIN_BUDGET - 100)))
    answer = await plane.handle("children/notices", {})
    assert answer == {
        "started": True,
        "placed": 1,
        "placedInTurn": 2,
        "reason": None,
        "remaining": 0,
    }
    assert (await _placed_ids(harness))[-2:] == ["n-1", "n-2"]


async def test_one_notice_is_one_in_both_and_nothing_waiting_is_zero(tmp_path: Path) -> None:
    harness, plane = await _plane(tmp_path)
    harness.post_notice(_notice(1))
    assert await plane.handle("children/notices", {}) == {
        "started": True,
        "placed": 1,
        "placedInTurn": 1,
        "reason": None,
        "remaining": 0,
    }
    assert await plane.handle("children/notices", {}) == {
        "started": False,
        "placed": 0,
        "placedInTurn": 0,
        "reason": "nothing",
        "remaining": 0,
    }


class _PostsDuringTheTurn:
    """A model that posts one notice on its first call: the turn's boundary places it."""

    def __init__(self) -> None:
        self.harness: Any = None
        self.calls = 0

    async def __call__(
        self, _model: Any, _context: Any, _options: Any = None
    ) -> AsyncIterator[Any]:
        self.calls += 1
        if self.calls == 1:
            self.harness.post_notice(_notice(9, "posted while the model answered"))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )


async def test_a_notice_posted_during_the_turn_is_counted_where_the_boundary_placed_it(
    tmp_path: Path,
) -> None:
    model = _PostsDuringTheTurn()
    harness = AgentHarness(
        AgentHarnessOptions(
            stream_fn=model, session=await make_session(tmp_path), notice_inbox=True
        )
    )
    model.harness = harness
    harness, plane = await _plane(tmp_path, harness)
    harness.post_notice(_notice(1))
    answer = await plane.handle("children/notices", {})
    assert (answer["placed"], answer["placedInTurn"], answer["remaining"]) == (1, 2, 0), answer
    assert (await _placed_ids(harness))[-2:] == ["n-1", "n-9"]
    assert model.calls == 2, "the boundary's notice was answered in the same turn"


async def test_the_kernel_counts_only_its_own_turn(tmp_path: Path) -> None:
    """``placed_in_turn`` is the turn's: a prompt's placements before it are not in it."""

    harness = make_harness(await make_session(tmp_path))
    harness.post_notice(_notice(1))
    await harness.prompt("hello")  # places n-1 ahead of the line, outside any notice turn
    assert (await _placed_ids(harness)) == ["n-1"]
    harness.post_notice(_notice(2))
    result = await harness.prompt_notices()
    assert (result.started, result.placed, result.placed_in_turn) == (True, 1, 1)
    refused = await harness.prompt_notices()
    assert (refused.started, refused.reason, refused.placed_in_turn) == (False, "nothing", 0)
