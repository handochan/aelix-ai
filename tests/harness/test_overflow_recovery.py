"""Issue #4 Lane B — overflow-driven auto-compaction RECOVERY path.

Pi parity: ``agent-session.ts:_checkCompaction`` Case 1 +
``_runAutoCompaction("overflow", will_retry)`` (SHA 927e980). A turn that
fails with a provider context-overflow error is recovered by compacting with
``reason="overflow"`` and re-running the failed turn once
(``AgentHarness._try_overflow_recovery``), with guards against an infinite
retry loop (pi #5720) and against compacting when nothing is eligible
(pi #4811).

Most tests mock ``_run`` (like ``tests/test_auto_retry.py``) so the recovery
control-flow is exercised deterministically; one test drives the REAL
``compact()`` end-to-end to assert ``reason``/``will_retry`` reach the hook
events.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
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
from aelix_agent_core.session.compaction import get_latest_compaction_boundary_ms
from aelix_agent_core.session.context import build_session_context
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage

_OVERFLOW_MSG = "prompt is too long: 213462 tokens > 200000 maximum"


def _model() -> Any:
    return SimpleNamespace(context_window=200_000, provider="anthropic", id="claude")


def _build_harness(*, session: Session | None = None, **opt_kwargs: Any) -> AgentHarness:
    sess = session if session is not None else Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=sess, **opt_kwargs))
    h._state.model = _model()  # type: ignore[assignment]
    h._state.auto_compaction_enabled = True
    # Overflow errors are not retriable; disable auto-retry so its backoff
    # never interferes with the overflow path under test.
    h._state.auto_retry_enabled = False
    return h


def _overflow_err() -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="")],
        stop_reason="error",
        error_message=_OVERFLOW_MSG,
    )


def _length_overflow() -> AssistantMessage:
    # is_context_overflow Case 3 (overflow.py): stop_reason="length" + output == 0
    # + input >= 99% of the 200k window (a server-truncated oversized input).
    return AssistantMessage(
        content=[TextContent(text="")],
        stop_reason="length",
        usage={"input": 199_000, "output": 0},
    )


def _success() -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason="end_turn",
        usage={"input": 10},
    )


def _capture_compact(h: AgentHarness) -> list[tuple[str, bool]]:
    """Replace ``h.compact`` with a no-op that records ``(reason, will_retry)``."""

    calls: list[tuple[str, bool]] = []

    async def _fake_compact(
        custom_instructions: Any = None,
        *,
        reason: str = "manual",
        will_retry: bool = False,
    ) -> Any:
        calls.append((reason, will_retry))
        return SimpleNamespace(summary="", first_kept_entry_id="", tokens_before=0)

    h.compact = _fake_compact  # type: ignore[method-assign]
    return calls


# ── recovery happy path ─────────────────────────────────────────────────────


async def test_overflow_compacts_with_reason_and_reruns() -> None:
    h = _build_harness()
    compact_calls = _capture_compact(h)
    run_calls: list[list[Any]] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        if len(run_calls) == 1:
            h._state.messages.extend(prompts)
            h._state.messages.append(_overflow_err())
        else:
            h._state.messages.append(_success())
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("do a big thing")

    # Initial turn + exactly one recovery re-run.
    assert len(run_calls) == 2
    assert run_calls[1] == []  # re-run continues from compacted context
    # Compaction ran once, tagged overflow + will_retry.
    assert compact_calls == [("overflow", True)]
    assert h._overflow_recovery_attempted is True


async def test_overflow_pops_error_before_rerun() -> None:
    # pi :1862-1867 — the error assistant is dropped from live context so the
    # retry does not re-send it.
    h = _build_harness()
    _capture_compact(h)
    run_calls: list[list[Any]] = []
    messages_at_rerun: list[Any] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        if len(run_calls) == 1:
            h._state.messages.append(UserMessage(content=[TextContent(text="hi")]))
            h._state.messages.append(_overflow_err())
        else:
            messages_at_rerun.extend(h._state.messages)
            h._state.messages.append(_success())
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("hi")

    assert len(run_calls) == 2
    # At the re-run, the trailing error assistant has been popped.
    assert not any(
        isinstance(m, AssistantMessage) and m.stop_reason == "error"
        for m in messages_at_rerun
    )


# ── no infinite retry ───────────────────────────────────────────────────────


async def test_overflow_never_loops_forever() -> None:
    # Every turn overflows; recovery must fire at most once then give up.
    h = _build_harness()
    compact_calls = _capture_compact(h)
    run_calls: list[list[Any]] = []

    async def _always_overflow(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        h._state.messages.extend(prompts)
        h._state.messages.append(_overflow_err())
        return list(h._state.messages)

    h._run = _always_overflow  # type: ignore[method-assign]
    await h.prompt("always overflows")

    # Initial turn + ONE recovery re-run only — the second overflow is refused.
    assert len(run_calls) == 2
    assert len(compact_calls) == 1
    assert compact_calls[0] == ("overflow", True)
    assert h._overflow_recovery_attempted is True


# ── successful (silent) overflow does NOT retry (pi #5720) ───────────────────


async def test_silent_overflow_on_success_compacts_without_retry() -> None:
    h = _build_harness()
    compact_calls = _capture_compact(h)
    run_calls: list[list[Any]] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        # A completed answer whose usage exceeded the window (z.ai style).
        h._state.messages.append(
            AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
                usage={"input": 250_000},
            )
        )
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("big input")

    # Compacted to shrink the NEXT turn, but the completed turn is NOT re-run.
    # (A trailing threshold pass may also fire — the same oversized usage trips
    # ``_check_auto_compaction`` — so assert on the overflow call specifically.)
    assert len(run_calls) == 1
    assert ("overflow", False) in compact_calls
    assert ("overflow", True) not in compact_calls


# ── nothing-to-compact is safe (pi #4811) ───────────────────────────────────


async def test_overflow_nothing_to_compact_is_safe() -> None:
    # Empty session → real compact() raises "Nothing to compact"; recovery must
    # swallow it (no crash, no re-run).
    h = _build_harness()  # session is empty
    run_calls: list[list[Any]] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        h._state.messages.extend(prompts)
        h._state.messages.append(_overflow_err())
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    # Must not raise.
    await h.prompt("overflow with empty session")

    # Real compact() failed with "Nothing to compact" → no re-run.
    assert len(run_calls) == 1
    # The attempt flag was set before the failed compaction (guards re-entry).
    assert h._overflow_recovery_attempted is True


async def test_overflow_disabled_when_auto_compaction_off() -> None:
    h = _build_harness()
    h._state.auto_compaction_enabled = False
    compact_calls = _capture_compact(h)
    run_calls: list[list[Any]] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        h._state.messages.extend(prompts)
        h._state.messages.append(_overflow_err())
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("overflow but compaction off")

    assert len(run_calls) == 1
    assert compact_calls == []


# ── REAL compact() — reason/will_retry reach the hook events ─────────────────


def _override(summary: str = "OVERFLOW_SUMMARY") -> Any:
    async def fn(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary=summary,
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=99,
            details={"src": "override"},
        )

    return fn


async def _seed_compactable(session: Session) -> None:
    chunk = "x" * 30_000  # ~7500 tokens each → prepare_compaction finds a cut
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text=chunk)]))


async def test_real_compact_emits_overflow_reason_and_will_retry() -> None:
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    h = _build_harness(session=session, _summarizer_override=_override())

    before: list[SessionBeforeCompactHookEvent] = []
    after: list[SessionCompactHookEvent] = []

    async def on_before(ev: Any, _ctx: Any) -> None:
        before.append(ev)

    async def on_compact(ev: Any, _ctx: Any) -> None:
        after.append(ev)

    h.hooks.on("session_before_compact", on_before)
    h.hooks.on("session_compact", on_compact)

    run_calls: list[list[Any]] = []

    async def _fake_run(prompts: Any, *, system_prompt: Any = None) -> list[Any]:
        run_calls.append(list(prompts))
        if len(run_calls) == 1:
            h._state.messages.append(_overflow_err())
        else:
            h._state.messages.append(_success())
        # Real ``_run`` returns the harness to idle (its ``finally``); replicate
        # so the subsequent real ``compact()`` passes its idle busy-guard.
        h._phase = "idle"
        h._idle_event.set()
        return list(h._state.messages)

    h._run = _fake_run  # type: ignore[method-assign]
    await h.prompt("trigger real overflow compaction")

    assert len(run_calls) == 2  # compacted, then re-ran the failed turn
    # Find the overflow-tagged events (a trailing threshold pass must not mask).
    overflow_before = [e for e in before if e.reason == "overflow"]
    overflow_after = [e for e in after if e.reason == "overflow"]
    assert overflow_before and overflow_before[0].will_retry is True
    assert overflow_after and overflow_after[0].will_retry is True


# ── FU1: trailing overflow-error excluded from the rebuilt re-run context ────


async def test_real_compact_excludes_trailing_overflow_error_from_rerun_context() -> None:
    # pi ``agent-session.ts:1988-1994`` — ``compact()`` rebuilds ``_state.messages``
    # from the session branch, which RE-ADDS the persisted trailing overflow error
    # (the first pop only cleared the LIVE list). The SECOND pop must drop it from
    # the re-run context while history keeps it. The existing pop test stubs
    # ``compact()`` so it never exercises this rebuild.
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    await session.append_message(_overflow_err())  # persisted trailing error
    h = _build_harness(session=session, _summarizer_override=_override())
    # Live context as the failed overflow turn leaves it (error is the last msg).
    h._state.messages = build_session_context(await session.get_branch()).messages
    assert isinstance(h._state.messages[-1], AssistantMessage)
    assert h._state.messages[-1].stop_reason == "error"

    did_retry = await h._try_overflow_recovery("sys")
    assert did_retry is True
    # History still ends with the error (the rebuild re-adds it — proving the
    # second pop is necessary)...
    rebuilt = build_session_context(await session.get_branch()).messages
    assert isinstance(rebuilt[-1], AssistantMessage)
    assert rebuilt[-1].stop_reason == "error"
    # ...but FU1 removed it from the LIVE re-run context.
    assert not (
        h._state.messages
        and isinstance(h._state.messages[-1], AssistantMessage)
        and h._state.messages[-1].stop_reason == "error"
    )


async def test_real_compact_excludes_trailing_length_overflow_from_rerun_context() -> None:
    # FU1 refinement (review #5): aelix's ``is_context_overflow`` Case 3 detects a
    # ``stop_reason == "length"`` overflow, which also reaches the will_retry path.
    # The second pop guards on ``is_context_overflow`` (not pi's narrow "error"),
    # so a length-overflow is excluded from the re-run context too.
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    await session.append_message(_length_overflow())
    h = _build_harness(session=session, _summarizer_override=_override())
    h._state.messages = build_session_context(await session.get_branch()).messages
    assert isinstance(h._state.messages[-1], AssistantMessage)
    assert h._state.messages[-1].stop_reason == "length"

    did_retry = await h._try_overflow_recovery("sys")
    assert did_retry is True
    # The length-overflow was popped from the LIVE re-run context (a "stop_reason
    # == 'error'" guard would have left it).
    assert not (
        h._state.messages
        and isinstance(h._state.messages[-1], AssistantMessage)
        and h._state.messages[-1].stop_reason == "length"
    )


# ── FU2: compaction-boundary staleness guards ───────────────────────────────


async def test_threshold_guard_a_skips_stale_pre_compaction_assistant() -> None:
    # Guard A (pi :1781-1789): a kept-tail assistant whose timestamp predates the
    # latest compaction is stale — its usage reflects pre-compaction context and
    # must not re-trigger threshold compaction.
    session = Session(MemorySessionStorage())
    await session.append_compaction("summary", "", 0)
    boundary = get_latest_compaction_boundary_ms(await session.get_branch())
    assert boundary is not None
    h = _build_harness(session=session)
    compact_calls = _capture_compact(h)
    h._state.messages = [
        AssistantMessage(
            content=[TextContent(text="stale")],
            stop_reason="end_turn",
            usage={"input": 500_000},  # well over the threshold
            timestamp=boundary - 1000.0,  # BEFORE the compaction boundary
        )
    ]
    await h._check_auto_compaction()
    assert compact_calls == []  # skipped despite the over-threshold usage


async def test_threshold_fires_for_fresh_assistant_after_compaction() -> None:
    # Contrast: a freshly-generated assistant (timestamp None = current) with the
    # same over-threshold usage IS compacted — Guard A only skips stale messages.
    session = Session(MemorySessionStorage())
    await session.append_compaction("summary", "", 0)
    h = _build_harness(session=session)
    compact_calls = _capture_compact(h)
    h._state.messages = [
        AssistantMessage(
            content=[TextContent(text="fresh")],
            stop_reason="end_turn",
            usage={"input": 500_000},
            timestamp=None,  # live message — never stale
        )
    ]
    await h._check_auto_compaction()
    assert compact_calls == [("threshold", False)]


async def test_threshold_guard_b_skips_stale_usage_source() -> None:
    # Guard B (pi :1827-1835): on the error/aborted estimate path the usage source
    # is an EARLIER assistant; skip if THAT message predates the compaction
    # boundary (its usage is stale).
    session = Session(MemorySessionStorage())
    await session.append_compaction("summary", "", 0)
    boundary = get_latest_compaction_boundary_ms(await session.get_branch())
    assert boundary is not None
    h = _build_harness(session=session)
    compact_calls = _capture_compact(h)
    h._state.messages = [
        AssistantMessage(
            content=[TextContent(text="usage-src")],
            stop_reason="end_turn",
            usage={"input": 500_000},
            timestamp=boundary - 1000.0,  # stale usage source
        ),
        AssistantMessage(
            content=[TextContent(text="")],
            stop_reason="error",  # most-recent error → forces the estimate path
            error_message="boom",
            timestamp=None,
        ),
    ]
    await h._check_auto_compaction()
    assert compact_calls == []  # Guard B skipped (usage source is pre-compaction)


async def test_threshold_error_branch_fires_when_usage_source_is_fresh() -> None:
    # Contrast: when the estimate's usage source is post-compaction (fresh, no
    # timestamp), the error-branch threshold compaction still fires.
    session = Session(MemorySessionStorage())
    await session.append_compaction("summary", "", 0)
    h = _build_harness(session=session)
    compact_calls = _capture_compact(h)
    h._state.messages = [
        AssistantMessage(
            content=[TextContent(text="usage-src")],
            stop_reason="end_turn",
            usage={"input": 500_000},
            timestamp=None,  # fresh usage source
        ),
        AssistantMessage(
            content=[TextContent(text="")],
            stop_reason="error",
            error_message="boom",
            timestamp=None,
        ),
    ]
    await h._check_auto_compaction()
    assert compact_calls == [("threshold", False)]


# ── FU3: compaction_start / compaction_end subscriber events ─────────────────


async def test_compaction_emits_start_and_end_on_success() -> None:
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    h = _build_harness(session=session, _summarizer_override=_override())
    events: list[Any] = []
    h.subscribe(lambda e: events.append(e))
    await h.compact(reason="manual")
    kinds = [getattr(e, "type", None) for e in events]
    assert kinds == ["compaction_start", "compaction_end"]
    start, end = events[0], events[1]
    assert start.reason == "manual"
    assert end.reason == "manual"
    assert end.aborted is False
    assert end.will_retry is False
    assert end.result is not None
    assert end.error_message is None


async def test_compaction_emits_end_event_on_nothing_to_compact() -> None:
    # pi emits compaction_end even on the swallowed "Nothing to compact" raise so
    # a subscriber always sees a matched start/end pair.
    h = _build_harness()  # empty session → "Nothing to compact"
    events: list[Any] = []
    h.subscribe(lambda e: events.append(e))
    with contextlib.suppress(AgentHarnessError):
        await h.compact(reason="threshold")
    kinds = [getattr(e, "type", None) for e in events]
    assert kinds == ["compaction_start", "compaction_end"]
    end = events[1]
    assert end.reason == "threshold"
    assert end.result is None
    assert end.error_message is not None
    assert "Nothing to compact" in end.error_message


async def test_compaction_end_carries_will_retry_on_overflow() -> None:
    session = Session(MemorySessionStorage())
    await _seed_compactable(session)
    h = _build_harness(session=session, _summarizer_override=_override())
    events: list[Any] = []
    h.subscribe(lambda e: events.append(e))
    await h.compact(reason="overflow", will_retry=True)
    end = next(e for e in events if getattr(e, "type", None) == "compaction_end")
    assert end.reason == "overflow"
    assert end.will_retry is True
    assert end.aborted is False
