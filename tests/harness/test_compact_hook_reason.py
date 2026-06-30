"""#4 (partial) — pi #5962: ``reason`` + ``will_retry`` on the compaction
hook events, populated at the manual vs threshold emit sites.

- :class:`SessionBeforeCompactHookEvent` and :class:`SessionCompactHookEvent`
  gain ``reason: Literal['manual','threshold','overflow']`` (default ``manual``)
  and ``will_retry: bool`` (default ``False``) — additive frozen-dataclass
  fields so existing constructors keep working.
- A manual ``compact()`` emits both events with ``reason == "manual"``.
- Threshold auto-compaction (``_check_auto_compaction``) emits both events
  with ``reason == "threshold"``.
- ``will_retry`` is always ``False`` today (overflow re-run path deferred).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    SessionBeforeCompactHookEvent,
    SessionCompactHookEvent,
)
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage

# ── field defaults ─────────────────────────────────────────────────────────


def test_before_compact_event_has_reason_and_will_retry_defaults() -> None:
    ev = SessionBeforeCompactHookEvent()
    assert ev.reason == "manual"
    assert ev.will_retry is False
    assert ev.type == "session_before_compact"


def test_session_compact_event_has_reason_and_will_retry_defaults() -> None:
    ev = SessionCompactHookEvent()
    assert ev.reason == "manual"
    assert ev.will_retry is False
    assert ev.type == "session_compact"


def test_reason_can_be_set_explicitly() -> None:
    ev = SessionBeforeCompactHookEvent(reason="threshold")
    assert ev.reason == "threshold"
    assert ev.will_retry is False


# ── emit-site reason population (manual vs threshold) ───────────────────────


def _override(summary: str = "TEST_SUMMARY") -> Any:
    async def fn(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary=summary,
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=42,
            details={"src": "override"},
        )

    return fn


async def _seed_compactable(session: Session) -> None:
    chunk = "x" * 30_000  # ~7500 tokens each → prepare_compaction finds a cut
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text=chunk)]))


async def _attached() -> tuple[AgentHarness, Session]:
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    opts = AgentHarnessOptions(session=session, _summarizer_override=_override())
    return AgentHarness(opts), session


async def test_manual_compact_emits_reason_manual() -> None:
    h, _session = await _attached()
    before: list[SessionBeforeCompactHookEvent] = []
    after: list[SessionCompactHookEvent] = []

    async def on_before(ev: Any, _ctx: Any) -> None:
        before.append(ev)

    async def on_compact(ev: Any, _ctx: Any) -> None:
        after.append(ev)

    h.hooks.on("session_before_compact", on_before)
    h.hooks.on("session_compact", on_compact)

    await h.compact()

    assert before and before[0].reason == "manual"
    assert before[0].will_retry is False
    assert after and after[0].reason == "manual"
    assert after[0].will_retry is False


async def test_threshold_auto_compaction_emits_reason_threshold() -> None:
    h, _session = await _attached()
    # Stub model so the threshold check trips on the seeded high usage.
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    h._state.messages.append(
        AssistantMessage(content=[], usage={"total_tokens": 19_000})
    )

    before: list[SessionBeforeCompactHookEvent] = []
    after: list[SessionCompactHookEvent] = []

    async def on_before(ev: Any, _ctx: Any) -> None:
        before.append(ev)

    async def on_compact(ev: Any, _ctx: Any) -> None:
        after.append(ev)

    h.hooks.on("session_before_compact", on_before)
    h.hooks.on("session_compact", on_compact)

    await h._check_auto_compaction()  # real compact() runs end-to-end

    assert before and before[0].reason == "threshold"
    assert before[0].will_retry is False
    assert after and after[0].reason == "threshold"
    assert after[0].will_retry is False
