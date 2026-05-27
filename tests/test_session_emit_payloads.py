"""§H.4 — Verify the full Pi payload shape on each of the 4 session_* emits.

P-17: SessionBeforeCompactHookEvent → preparation, branch_entries,
custom_instructions, signal.
P-18: SessionBeforeTreeHookEvent → preparation, signal.
P-19: SessionTreeHookEvent → new_leaf_id (str | None), old_leaf_id (str |
None), summary_entry (SummaryEntry | None), from_hook.
P-20 (paired): SessionBeforeCompactResult → cancel, compaction.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    NavigateTreeOptions,
)
from aelix_agent_core.harness.hooks import (
    SessionBeforeCompactHookEvent,
    SessionBeforeTreeHookEvent,
    SessionCompactHookEvent,
    SessionTreeHookEvent,
)
from aelix_agent_core.session import (
    BranchSummaryPreparation,
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage


def _summarizer() -> Any:
    async def fn(_m: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary="sum",
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=0,
        )

    return fn


def _branch_summarizer() -> Any:
    async def fn(_m: Any, _entries: Any, _ci: Any) -> str:
        return "bsum"

    return fn


async def _seed_compactable(session: Session) -> None:
    """Append enough conversation that ``prepare_compaction`` finds a cut.

    Sprint 6h₁₂-compaction: ``prepare_compaction`` now runs the real
    ``findCutPoint`` backward token-budget walk (``KEEP_RECENT_TOKENS`` =
    20000), so a single ``"seed"`` message no longer crosses the budget.
    These emit-shape tests seed several large messages so the compaction
    preparation is non-None and the emit path is exercised.
    """

    chunk = "x" * 30_000  # ~7500 tokens each
    for _ in range(6):
        await session.append_message(
            UserMessage(content=[TextContent(text=chunk)])
        )


async def test_session_before_compact_event_pi_payload_shape() -> None:
    """P-17: preparation + branch_entries + custom_instructions + signal."""

    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    h = AgentHarness(
        AgentHarnessOptions(session=session, _summarizer_override=_summarizer())
    )
    captured: list[SessionBeforeCompactHookEvent] = []

    async def on(ev: SessionBeforeCompactHookEvent, _ctx: Any) -> None:
        captured.append(ev)

    h.hooks.on("session_before_compact", on)
    await h.compact("focus")
    assert captured
    ev = captured[0]
    # All four Pi payload fields present.
    assert isinstance(ev.preparation, CompactionPreparation)
    assert isinstance(ev.branch_entries, list) and len(ev.branch_entries) >= 1
    assert ev.custom_instructions == "focus"
    # signal slot exists (Pi parity placeholder; may be None for now).
    assert hasattr(ev, "signal")


async def test_session_compact_event_pi_payload_shape() -> None:
    """SessionCompactHookEvent: compaction_entry + from_hook."""

    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    h = AgentHarness(
        AgentHarnessOptions(session=session, _summarizer_override=_summarizer())
    )
    captured: list[SessionCompactHookEvent] = []

    async def on(ev: SessionCompactHookEvent, _ctx: Any) -> None:
        captured.append(ev)

    h.hooks.on("session_compact", on)
    await h.compact()
    assert captured
    ev = captured[0]
    assert ev.compaction_entry is not None
    assert ev.compaction_entry.type == "compaction"
    assert isinstance(ev.from_hook, bool)


async def test_session_before_tree_event_has_signal_p18() -> None:
    """P-18: SessionBeforeTreeHookEvent must carry preparation + signal."""

    session = Session(MemorySessionStorage())
    user_id = await session.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    await session.append_message(
        AssistantMessage(content=[TextContent(text="answer")])
    )
    h = AgentHarness(
        AgentHarnessOptions(
            session=session, _branch_summarizer_override=_branch_summarizer()
        )
    )
    captured: list[SessionBeforeTreeHookEvent] = []

    async def on(ev: SessionBeforeTreeHookEvent, _ctx: Any) -> None:
        captured.append(ev)

    h.hooks.on("session_before_tree", on)
    await h.navigate_tree(user_id, NavigateTreeOptions(summarize=True))
    assert captured
    ev = captured[0]
    assert isinstance(ev.preparation, BranchSummaryPreparation)
    assert hasattr(ev, "signal")


async def test_session_tree_event_new_leaf_id_typed_optional_p19() -> None:
    """P-19: ``new_leaf_id`` is ``str | None`` (was ``str = ""``)."""

    session = Session(MemorySessionStorage())
    user_id = await session.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    await session.append_message(
        AssistantMessage(content=[TextContent(text="answer")])
    )
    h = AgentHarness(
        AgentHarnessOptions(
            session=session, _branch_summarizer_override=_branch_summarizer()
        )
    )
    captured: list[SessionTreeHookEvent] = []

    async def on(ev: SessionTreeHookEvent, _ctx: Any) -> None:
        captured.append(ev)

    h.hooks.on("session_tree", on)
    await h.navigate_tree(user_id, NavigateTreeOptions(summarize=True))
    assert captured
    ev = captured[0]
    # All Pi payload fields present and typed correctly.
    assert ev.new_leaf_id is None or isinstance(ev.new_leaf_id, str)
    assert ev.old_leaf_id is None or isinstance(ev.old_leaf_id, str)
    assert isinstance(ev.from_hook, bool)
    # summary_entry may be None or a BranchSummaryEntry.
    if ev.summary_entry is not None:
        assert ev.summary_entry.type == "branch_summary"
