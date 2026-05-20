"""Sprint 6h₂ (ADR-0071, P-249/P-250/P-252) — auto-mode setters.

Pi parity:

- ``session.setAutoCompactionEnabled`` (``rpc-mode.ts:603-606``)
- ``session.setAutoRetryEnabled`` (``rpc-mode.ts:614-617``)
- ``session.abortRetry`` (``rpc-mode.ts:619-622``)
- ``session.abortBash`` (``rpc-mode.ts:632-635``)

These are state-only setters in Sprint 6h₂ — the actual auto-compaction
trigger / retry loop / bash cancellation lands in later sprints. The
:attr:`AgentHarness.auto_compaction_enabled` public property (P-252)
backs the RPC ``_handle_get_state`` real-source read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _make_harness() -> AgentHarness:
    return AgentHarness(AgentHarnessOptions(stream_fn=_stream()))


async def test_auto_compaction_default_is_true() -> None:
    """Pi default per fixture ``pi_rpc_mode_734e08e.json``."""

    h = _make_harness()
    try:
        assert h.auto_compaction_enabled is True
        assert h.state.auto_compaction_enabled is True
    finally:
        await h.dispose()


async def test_set_auto_compaction_enabled_toggles_state_and_property() -> None:
    """Pi parity (P-249/P-252): public property tracks state field."""

    h = _make_harness()
    try:
        h.set_auto_compaction_enabled(False)
        assert h.state.auto_compaction_enabled is False
        assert h.auto_compaction_enabled is False
        h.set_auto_compaction_enabled(True)
        assert h.state.auto_compaction_enabled is True
        assert h.auto_compaction_enabled is True
    finally:
        await h.dispose()


async def test_auto_retry_default_is_true() -> None:
    h = _make_harness()
    try:
        assert h.state.auto_retry_enabled is True
    finally:
        await h.dispose()


async def test_set_auto_retry_enabled_toggles_state() -> None:
    """Pi parity (P-249)."""

    h = _make_harness()
    try:
        h.set_auto_retry_enabled(False)
        assert h.state.auto_retry_enabled is False
        h.set_auto_retry_enabled(True)
        assert h.state.auto_retry_enabled is True
    finally:
        await h.dispose()


async def test_abort_retry_sets_state_flag() -> None:
    """Pi parity (P-250): best-effort cancel intent flag."""

    h = _make_harness()
    try:
        assert h.state.retry_aborted is False
        h.abort_retry()
        assert h.state.retry_aborted is True
    finally:
        await h.dispose()


async def test_abort_bash_sets_state_flag() -> None:
    """Pi parity (P-250): best-effort cancel intent flag."""

    h = _make_harness()
    try:
        assert h.state.bash_aborted is False
        h.abort_bash()
        assert h.state.bash_aborted is True
    finally:
        await h.dispose()


async def test_auto_mode_setters_do_not_emit_events() -> None:
    """Pi parity (P-4): state-only setters with no event emission."""

    h = _make_harness()
    seen: list[Any] = []
    for name in ("queue_update", "save_point", "settled"):
        h.hooks.on(name, lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]
    try:
        h.set_auto_compaction_enabled(False)
        h.set_auto_retry_enabled(False)
        h.abort_retry()
        h.abort_bash()
        assert seen == []
    finally:
        await h.dispose()
