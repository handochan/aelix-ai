"""§H.1 — AgentHarness.compact() tests (Sprint 4b / Phase 2.2.2)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.harness.hooks import (
    SessionBeforeCompactHookEvent,
    SessionBeforeCompactResult,
    SessionCompactHookEvent,
)
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import TextContent, UserMessage


def _override(summary: str = "TEST_SUMMARY") -> Any:
    async def fn(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary=summary,
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=42,
            details={"src": "override"},
        )

    return fn


async def _attached(opts: AgentHarnessOptions | None = None) -> tuple[AgentHarness, Session]:
    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="seed")]))
    base = opts or AgentHarnessOptions()
    base.session = session
    if base._summarizer_override is None:
        base._summarizer_override = _override()
    h = AgentHarness(base)
    return h, session


async def test_compact_happy_path_with_summarizer_override() -> None:
    h, session = await _attached()
    captured: list[SessionCompactHookEvent] = []

    async def on_compact(ev: Any, _ctx: Any) -> None:
        captured.append(ev)

    h.hooks.on("session_compact", on_compact)
    result = await h.compact("focus on bugs")
    assert result.summary == "TEST_SUMMARY"
    assert result.tokens_before == 42
    entries = await session.get_entries()
    # We started with one user message; now have one compaction entry too.
    compact_entries = [e for e in entries if e.type == "compaction"]
    assert len(compact_entries) == 1
    assert captured and captured[0].compaction_entry.summary == "TEST_SUMMARY"
    assert captured[0].from_hook is False


async def test_compact_cancel_via_hook_raises_compaction() -> None:
    """Pi parity: cancel path raises AgentHarnessError("compaction"), not "invalid_state".

    Pi agent-harness.ts:707-708 throws AgentHarnessError("compaction", ...) on
    hookResult?.cancel. Aelix mirrors the same error code. (W4 finding #14 / Fix 1.)
    """
    h, _ = await _attached()

    async def cancel_hook(_ev: Any, _ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=True, reason="nope")

    h.hooks.on("session_before_compact", cancel_hook)
    with pytest.raises(AgentHarnessError) as exc:
        await h.compact()
    assert exc.value.code == "compaction"
    assert "nope" in str(exc.value)
    # Phase must reset to idle after the cancellation.
    assert h.phase == "idle"


async def test_compact_hook_substitutes_compaction_p20() -> None:
    """P-20: hook may substitute a CompactResult; harness skips LLM call."""

    h, session = await _attached(AgentHarnessOptions(_summarizer_override=None))
    captured: list[SessionCompactHookEvent] = []

    fake_first_kept = (await session.get_entries())[0].id

    async def sub_hook(_ev: Any, _ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(
            compaction=CompactResult(
                summary="HOOK_SUBSTITUTED",
                first_kept_entry_id=fake_first_kept,
                tokens_before=99,
                details=None,
            )
        )

    async def on_compact(ev: Any, _ctx: Any) -> None:
        captured.append(ev)

    h.hooks.on("session_before_compact", sub_hook)
    h.hooks.on("session_compact", on_compact)
    result = await h.compact()
    assert result.summary == "HOOK_SUBSTITUTED"
    assert result.tokens_before == 99
    assert captured and captured[0].from_hook is True


async def test_compact_without_session_raises_invalid_state() -> None:
    h = AgentHarness(AgentHarnessOptions())  # no session
    with pytest.raises(AgentHarnessError) as exc:
        await h.compact()
    assert exc.value.code == "invalid_state"


async def test_compact_no_summarizer_no_auth_raises_invalid_state() -> None:
    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="x")]))
    # Note: no _summarizer_override and no get_api_key_and_headers.
    h = AgentHarness(AgentHarnessOptions(session=session))
    with pytest.raises(AgentHarnessError) as exc:
        await h.compact()
    assert exc.value.code == "invalid_state"
    assert "get_api_key_and_headers" in str(exc.value)


async def test_compact_phase_machine_blocks_concurrent_prompt() -> None:
    """While compact() is in flight, prompt() must raise ``busy``."""

    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="seed")]))

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_override(_m: Any, prep: Any, _ci: Any) -> CompactResult:
        started.set()
        await release.wait()
        return CompactResult(
            summary="s",
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=0,
        )

    h = AgentHarness(
        AgentHarnessOptions(session=session, _summarizer_override=slow_override)
    )
    task = asyncio.create_task(h.compact())
    await started.wait()
    assert h.phase == "compaction"
    with pytest.raises(AgentHarnessError) as exc:
        await h.prompt("ignored")
    assert exc.value.code == "busy"
    release.set()
    await task
    assert h.phase == "idle"


async def test_compact_error_propagates() -> None:
    async def bad_override(_m: Any, _p: Any, _ci: Any) -> CompactResult:
        raise RuntimeError("boom")

    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="x")]))
    h = AgentHarness(
        AgentHarnessOptions(session=session, _summarizer_override=bad_override)
    )
    with pytest.raises(RuntimeError, match="boom"):
        await h.compact()
    # Phase must reset to idle after the error.
    assert h.phase == "idle"


async def test_compact_concurrent_second_raises_busy() -> None:
    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="seed")]))

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(_m: Any, prep: Any, _ci: Any) -> CompactResult:
        started.set()
        await release.wait()
        return CompactResult(
            summary="x", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=0
        )

    h = AgentHarness(
        AgentHarnessOptions(session=session, _summarizer_override=slow)
    )
    task = asyncio.create_task(h.compact())
    await started.wait()
    with pytest.raises(AgentHarnessError) as exc:
        await h.compact()
    assert exc.value.code == "busy"
    release.set()
    await task


async def test_compact_emits_session_before_compact_payload_shape() -> None:
    """The emit event MUST carry preparation + branch_entries + signal."""

    h, session = await _attached()
    captured: list[SessionBeforeCompactHookEvent] = []

    async def on_before(ev: SessionBeforeCompactHookEvent, _ctx: Any) -> None:
        captured.append(ev)

    h.hooks.on("session_before_compact", on_before)
    await h.compact("focus")
    assert captured
    ev = captured[0]
    assert ev.preparation is not None
    assert isinstance(ev.preparation, CompactionPreparation)
    assert ev.custom_instructions == "focus"
    # branch_entries is the full branch list at emit time.
    assert len(ev.branch_entries) >= 1
    # signal slot exists (Pi parity).
    assert hasattr(ev, "signal")
