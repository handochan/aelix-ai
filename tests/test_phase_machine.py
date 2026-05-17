"""§H.3 — Phase machine guard tests (Sprint 4b / Phase 2.2.2).

The four-value ``AgentHarnessPhase`` literal is ``idle | turn | compaction
| branch_summary``. Per ADR-0023 §"Phase guards":

- ``prompt()`` raises ``AgentHarnessError("busy")`` from any non-idle phase.
- ``compact()`` raises busy from any non-idle phase.
- ``navigate_tree()`` raises busy from any non-idle phase.
- ``steer()`` / ``follow_up()`` are enqueue-only and ALWAYS legal — Pi
  parity (``agent-harness.ts`` steer paths). The original spec §A wording
  said "raise busy" for steer/follow_up; Pi behavior is enqueue regardless,
  and the existing Aelix test suite + Pi-parity audit anchor on that. See
  spec-deviation note in implementer report.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    NavigateTreeOptions,
)
from aelix_agent_core.session import (
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage


async def _slow_compact_session() -> tuple[AgentHarness, Session, asyncio.Event, asyncio.Event]:
    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="seed")]))

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(_m: Any, prep: Any, _ci: Any) -> CompactResult:
        started.set()
        await release.wait()
        return CompactResult(
            summary="s",
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=0,
        )

    h = AgentHarness(
        AgentHarnessOptions(session=session, _summarizer_override=slow)
    )
    return h, session, started, release


async def _slow_navigate_session() -> tuple[AgentHarness, Session, str, asyncio.Event, asyncio.Event]:
    session = Session(MemorySessionStorage())
    user_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    await session.append_message(
        AssistantMessage(content=[TextContent(text="answer")])
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_branch(_m: Any, _entries: Any, _ci: Any) -> str:
        started.set()
        await release.wait()
        return "BS"

    h = AgentHarness(
        AgentHarnessOptions(
            session=session, _branch_summarizer_override=slow_branch
        )
    )
    return h, session, user_id, started, release


async def test_prompt_during_compaction_raises_busy() -> None:
    h, _, started, release = await _slow_compact_session()
    task = asyncio.create_task(h.compact())
    await started.wait()
    assert h.phase == "compaction"
    with pytest.raises(AgentHarnessError) as exc:
        await h.prompt("x")
    assert exc.value.code == "busy"
    release.set()
    await task


async def test_compact_during_branch_summary_raises_busy() -> None:
    h, _, user_id, started, release = await _slow_navigate_session()
    task = asyncio.create_task(
        h.navigate_tree(user_id, NavigateTreeOptions(summarize=True))
    )
    await started.wait()
    assert h.phase == "branch_summary"
    with pytest.raises(AgentHarnessError) as exc:
        await h.compact()
    assert exc.value.code == "busy"
    release.set()
    await task


async def test_navigate_tree_during_compaction_raises_busy() -> None:
    h, _, started, release = await _slow_compact_session()
    task = asyncio.create_task(h.compact())
    await started.wait()
    with pytest.raises(AgentHarnessError) as exc:
        await h.navigate_tree("some-id")
    assert exc.value.code == "busy"
    release.set()
    await task


async def test_compact_and_navigate_tree_idle_when_done() -> None:
    h, _, started, release = await _slow_compact_session()
    task = asyncio.create_task(h.compact())
    await started.wait()
    assert not h.is_idle
    release.set()
    await task
    assert h.is_idle
    assert h.phase == "idle"


async def test_steer_and_follow_up_always_legal_pi_parity() -> None:
    """Pi parity: steer()/follow_up() enqueue regardless of phase.

    spec-deviation: spec §A wording lists steer/follow_up under "raise busy"
    guards. The Pi reference (``agent-harness.ts`` steer / follow_up paths)
    enqueues unconditionally — this is the binding behavior per top-level
    "pi agent를 완전 동일하게 완벽하게 구현" principle and the existing
    Aelix Phase 2.1 audit (D.1.10). The implementation surfaces this as the
    actual phase-machine contract.
    """

    h, _, started, release = await _slow_compact_session()
    task = asyncio.create_task(h.compact())
    await started.wait()
    # Both calls succeed without raising; they enqueue for the next prompt().
    await h.steer("steer-1")
    await h.follow_up("follow-1")
    release.set()
    await task
