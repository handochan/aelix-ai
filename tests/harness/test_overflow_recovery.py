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
