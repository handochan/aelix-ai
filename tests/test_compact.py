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


async def _seed_compactable(session: Session) -> None:
    """Append enough conversation that ``prepare_compaction`` finds a cut.

    Sprint 6h₁₂-compaction: ``prepare_compaction`` now runs the real
    ``findCutPoint`` backward token-budget walk (``KEEP_RECENT_TOKENS`` =
    20000, ``estimate_tokens`` = chars // 4). A tiny ``"seed"`` message no
    longer exceeds the budget, so these harness-orchestration tests seed
    several large messages so a non-empty ``messages_to_summarize`` prefix
    exists and the emit/persist path is still exercised end-to-end.
    """

    chunk = "x" * 30_000  # ~7500 tokens each
    for _ in range(6):
        await session.append_message(
            UserMessage(content=[TextContent(text=chunk)])
        )


async def _attached(opts: AgentHarnessOptions | None = None) -> tuple[AgentHarness, Session]:
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
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


async def test_compact_rebuilds_live_state_messages() -> None:
    """ADR-0117 / pi agent-session.ts:1693-1695: compact() must rebuild the
    live ``_state.messages`` from the post-compaction branch so the reduction
    takes effect on the NEXT turn (drop the summarized prefix + prepend the
    summary) — not merely persist to the session."""
    h, _session = await _attached()  # 6 large user messages seeded (~45k tokens)
    await h.compact()
    msgs = h._state.messages
    # Rebuilt + reduced: summary message + kept tail, fewer than the 6 seeded.
    assert 0 < len(msgs) < 6
    # The first message is the compaction summary (carries the summary text).
    first_text = "".join(
        getattr(b, "text", "") for b in getattr(msgs[0], "content", [])
    )
    assert "TEST_SUMMARY" in first_text


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
    # Seed enough that prepare_compaction yields a non-None preparation so the
    # auth guard (not the "Nothing to compact" short-circuit) is reached.
    await _seed_compactable(session)
    # Note: no _summarizer_override and no get_api_key_and_headers.
    h = AgentHarness(AgentHarnessOptions(session=session))
    with pytest.raises(AgentHarnessError) as exc:
        await h.compact()
    assert exc.value.code == "invalid_state"
    assert "get_api_key_and_headers" in str(exc.value)


async def test_compact_phase_machine_blocks_concurrent_prompt() -> None:
    """While compact() is in flight, prompt() must raise ``busy``."""

    session = Session(MemorySessionStorage())
    await _seed_compactable(session)

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
    await _seed_compactable(session)
    h = AgentHarness(
        AgentHarnessOptions(session=session, _summarizer_override=bad_override)
    )
    with pytest.raises(RuntimeError, match="boom"):
        await h.compact()
    # Phase must reset to idle after the error.
    assert h.phase == "idle"


async def test_compact_concurrent_second_raises_busy() -> None:
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)

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


# ============================================================================
# Sprint 6h₁₈ (ADR-0126) — _check_auto_compaction (auto-compaction trigger)
# ============================================================================


from types import SimpleNamespace  # noqa: E402

from aelix_ai.messages import AssistantMessage  # noqa: E402


async def _harness_with_model(
    *,
    context_window: int = 20_000,
    enabled: bool = True,
) -> AgentHarness:
    """Build a harness wired with a stub model + a spy on ``compact()``.

    The spy records calls and returns a stand-in :class:`CompactResult`, so the
    test asserts ``_check_auto_compaction`` *decided to compact* without running
    the real summarization pipeline (which is covered by the tests above).
    """

    h, _ = await _attached()
    # Stub model exposing only ``context_window`` (all _check_auto_compaction
    # reads via getattr — SimpleNamespace is intentionally minimal).
    h._state.model = SimpleNamespace(context_window=context_window)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = enabled
    return h


def _install_compact_spy(h: AgentHarness) -> list[None]:
    """Replace ``h.compact`` with an async spy and return a call-counter list."""

    calls: list[None] = []

    async def _spy(_ci: object = None, **_kw: object) -> Any:
        # ``**_kw`` absorbs the keyword-only ``reason`` the threshold path now
        # passes (``self.compact(reason="threshold")``).
        calls.append(None)
        return CompactResult(
            summary="(spy)", first_kept_entry_id="x", tokens_before=0, details={}
        )

    h.compact = _spy  # type: ignore[method-assign]
    return calls


async def test_check_auto_compaction_no_op_when_flag_disabled() -> None:
    h = await _harness_with_model(enabled=False)
    calls = _install_compact_spy(h)
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 19_000})
    ]
    await h._check_auto_compaction()
    assert calls == []  # disabled flag short-circuits before any token math


async def test_check_auto_compaction_no_op_when_no_model() -> None:
    h = await _harness_with_model()
    h._state.model = None  # type: ignore[assignment]
    calls = _install_compact_spy(h)
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 19_000})
    ]
    await h._check_auto_compaction()
    assert calls == []


async def test_check_auto_compaction_no_op_when_context_window_zero() -> None:
    h = await _harness_with_model(context_window=0)
    calls = _install_compact_spy(h)
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 19_000})
    ]
    await h._check_auto_compaction()
    assert calls == []


async def test_check_auto_compaction_no_op_below_threshold() -> None:
    # context_window 20_000 − reserve 16_384 = threshold 3_616 tokens; 3_000 is
    # under, so pi ``shouldCompact`` returns False → no compact call.
    h = await _harness_with_model(context_window=20_000)
    calls = _install_compact_spy(h)
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 3_000})
    ]
    await h._check_auto_compaction()
    assert calls == []


async def test_check_auto_compaction_compacts_above_threshold_via_usage() -> None:
    # Same window; 5_000 > 3_616 → pi ``shouldCompact`` True → compact() called.
    h = await _harness_with_model(context_window=20_000)
    calls = _install_compact_spy(h)
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 5_000})
    ]
    await h._check_auto_compaction()
    assert len(calls) == 1


async def test_check_auto_compaction_uses_estimate_on_error_turn() -> None:
    # pi ``agent-session.ts:1824-1840`` switches from ``calculate_context_tokens``
    # to ``estimate_context_tokens`` when the last assistant turn is error/aborted
    # (its usage is not trustworthy). Seed a large user message so the heuristic
    # estimate clears the threshold.
    from aelix_ai.messages import TextContent, UserMessage

    h = await _harness_with_model(context_window=20_000)
    calls = _install_compact_spy(h)
    h._state.messages = [
        UserMessage(content=[TextContent(text="x" * 30_000)]),  # heuristic: chars/4
        AssistantMessage(content=[], stop_reason="error", usage=None),
    ]
    await h._check_auto_compaction()
    assert len(calls) == 1


async def test_check_auto_compaction_invoked_from_prompt() -> None:
    # Integration: prompt() must invoke _check_auto_compaction after _run.
    # Replace both compact + _run with spies so this exercises only the
    # call-site wiring in prompt().
    h, _ = await _attached()
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    compact_calls = _install_compact_spy(h)

    run_called: list[None] = []

    async def _fake_run(_prompts: object, *, system_prompt: object = None) -> list[Any]:
        run_called.append(None)
        # populate a high-usage assistant message so the threshold check fires
        h._state.messages.append(
            AssistantMessage(content=[], usage={"total_tokens": 5_000})
        )
        return []

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("hi")
    assert run_called == [None]
    assert len(compact_calls) == 1  # auto-trigger fired after _run


# === W-review fixes — HIGH-1, HIGH-2, MEDIUM tests =========================


async def test_check_auto_compaction_no_op_when_no_session() -> None:
    # W-review HIGH-1: backward-compat in-memory mode (no Session) must not
    # propagate ``compact() requires options.session to be attached``.
    base = AgentHarnessOptions()
    base._summarizer_override = _override()
    h = AgentHarness(base)
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 19_000})  # way above
    ]
    # Must not raise (compact() would, but the no-session short-circuit catches it).
    await h._check_auto_compaction()


async def test_check_auto_compaction_swallows_nothing_to_compact() -> None:
    # W-review HIGH-2: ``Nothing to compact`` from prepare_compaction must not
    # turn a successful turn into a propagated exception.
    h = await _harness_with_model(context_window=20_000)
    raised: list[Exception] = []

    async def _raises_nothing_to_compact(_ci: object = None, **_kw: object) -> Any:
        raise AgentHarnessError("invalid_state", "Nothing to compact")

    h.compact = _raises_nothing_to_compact  # type: ignore[method-assign]
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 19_000})
    ]
    try:
        await h._check_auto_compaction()
    except Exception as exc:
        raised.append(exc)
    assert raised == []  # swallowed


async def test_check_auto_compaction_propagates_other_invalid_state_errors() -> None:
    # W-review HIGH-2 follow-up: OTHER invalid_state errors must still bubble
    # (silent failure of a non-no-op compaction would mask real bugs).
    h = await _harness_with_model(context_window=20_000)

    async def _raises_other(_ci: object = None, **_kw: object) -> Any:
        raise AgentHarnessError("invalid_state", "Some other failure")

    h.compact = _raises_other  # type: ignore[method-assign]
    h._state.messages = [
        AssistantMessage(content=[], usage={"total_tokens": 19_000})
    ]
    with pytest.raises(AgentHarnessError) as ei:
        await h._check_auto_compaction()
    assert "Some other failure" in str(ei.value)


async def test_check_auto_compaction_real_compact_integration() -> None:
    # W-review MEDIUM: at least one test must let the REAL compact() path run
    # (the spy tests mask call-chain failures). _attached() seeds the SESSION
    # with 6 large user messages; trip the threshold and assert the real
    # compact() pipeline runs end-to-end, leaving the pi-shape compaction
    # summary marker in _state.messages (which the compact() finally rebuilds
    # from the compacted branch).
    h, session = await _attached()
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    h._state.messages.append(
        AssistantMessage(content=[], usage={"total_tokens": 19_000})
    )
    await h._check_auto_compaction()  # no spy → real compact() runs end-to-end
    # The pi-shape summary header from the summarization prompt (ADR-0117).
    text = "".join(
        getattr(b, "text", "") or ""
        for m in h._state.messages
        for b in (getattr(m, "content", []) or [])
    )
    assert "compacted into" in text.lower()
    # And a compaction entry was appended to the session.
    entries = await session.get_entries()
    assert any(getattr(e, "type", None) == "compaction" for e in entries)


async def test_check_auto_compaction_not_invoked_on_input_handled() -> None:
    # W-review MEDIUM: an InputHandled hook short-circuits prompt() before
    # _run — auto-compact must NOT fire on that path.
    from aelix_agent_core.harness.hooks import InputHandled

    h, _ = await _attached()
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    compact_calls = _install_compact_spy(h)

    async def _handled(_ev: Any, _ctx: Any) -> InputHandled:
        return InputHandled()

    h.hooks.on("input", _handled)
    await h.prompt("never reaches the model")
    assert compact_calls == []  # InputHandled short-circuit skipped _run + auto-compact


async def test_check_auto_compaction_not_invoked_on_busy_raise() -> None:
    # W-review MEDIUM: a concurrent prompt() that hits the phase-busy guard
    # raises BEFORE _run, so the rejected caller must not trigger auto-compact.
    h, _ = await _attached()
    h._state.model = SimpleNamespace(context_window=20_000)  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    compact_calls = _install_compact_spy(h)
    h._phase = "turn"  # simulate an in-flight turn from another caller
    with pytest.raises(AgentHarnessError) as ei:
        await h.prompt("blocked")
    assert ei.value.code == "busy"
    assert compact_calls == []
