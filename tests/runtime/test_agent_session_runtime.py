"""Sprint 6h₄b · §E.1 — :class:`AgentSessionRuntime` unit + rebind seam tests.

Pi parity: ``packages/agent/src/core/agent-session-runtime.ts:67-374``.

Covers the FOUNDATION-ONLY scope (ADR-0077 / P-302~P-310):
  - Constructor + getters (``harness`` / ``session`` / ``cwd`` /
    ``diagnostics`` / ``model_fallback_message``).
  - ``set_rebind_session`` / ``set_before_session_invalidate`` callable
    storage + invocation count balance per replace.
  - ``_apply_for_test`` exercising the full
    ``_teardown_current → _apply → _rebind_session`` waveform.
  - Stub returns from ``switch_session`` / ``new_session`` / ``fork`` /
    ``import_from_jsonl`` (``NotImplementedError`` referencing Sprint
    6h₄c / ADR-0078 — P-310).
  - ``dispose()`` calls ``before_session_invalidate`` before disposing
    the harness exactly once (P-307).
  - ``RuntimeReplaceResult`` / :class:`AgentSessionRuntimeDiagnostic`
    frozen-dataclass field locks.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import (
    AgentSessionRuntime,
    AgentSessionRuntimeDiagnostic,
    RuntimeReplaceResult,
)
from aelix_agent_core.session import MemorySessionStorage, Session
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
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _new_session() -> Session:
    return Session(MemorySessionStorage())


def _new_harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _noop_factory(_new_session: Session) -> AgentHarness:
    return _new_harness()


# === §A — Constructor + getters ==============================================


def test_runtime_constructor_stores_harness_identity() -> None:
    h = _new_harness()
    try:
        runtime = AgentSessionRuntime(h, _noop_factory)
        # ``harness`` getter (Pi parity P-304) returns the LIVE harness.
        assert runtime.harness is h
    finally:
        pass


def test_runtime_session_is_read_through_to_harness_session() -> None:
    """P-304 — ``runtime.session`` reads through to ``self._harness._session``."""

    session = _new_session()
    h = _new_harness(session=session)
    runtime = AgentSessionRuntime(h, _noop_factory)
    assert runtime.session is session


def test_runtime_session_is_none_when_harness_has_no_session() -> None:
    h = _new_harness()  # no session attached
    runtime = AgentSessionRuntime(h, _noop_factory)
    assert runtime.session is None


def test_runtime_cwd_returns_none_when_session_is_none() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    assert runtime.cwd is None


def test_runtime_diagnostics_returns_copy_not_internal_list() -> None:
    """Mutation isolation — caller mutations MUST NOT bleed into the
    runtime's internal diagnostics list.
    """

    initial = [AgentSessionRuntimeDiagnostic(code="x", message="hi")]
    h = _new_harness()
    runtime = AgentSessionRuntime(
        h, _noop_factory, diagnostics=initial
    )
    snapshot = runtime.diagnostics
    snapshot.append(
        AgentSessionRuntimeDiagnostic(code="y", message="leaked")
    )
    # Internal list unaffected.
    assert len(runtime.diagnostics) == 1
    assert runtime.diagnostics[0].code == "x"


def test_runtime_model_fallback_message_mirrors_init_value() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(
        h, _noop_factory, model_fallback_message="fallback-x"
    )
    assert runtime.model_fallback_message == "fallback-x"


def test_runtime_model_fallback_message_defaults_to_none() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    assert runtime.model_fallback_message is None


# === §B — Seam setters (P-305) ===============================================


async def test_set_rebind_session_stores_callable() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)

    async def _cb(_new_h: AgentHarness) -> None:
        return None

    runtime.set_rebind_session(_cb)
    # Internal seam asserted through replace path below; here we just
    # confirm storage round-trip via private attribute peek (Aelix-additive
    # — Pi has no introspection but the seam pin needs it).
    assert runtime._rebind_session is _cb


def test_set_before_session_invalidate_stores_callable() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)

    def _cb() -> None:
        return None

    runtime.set_before_session_invalidate(_cb)
    assert runtime._before_session_invalidate is _cb


# === §C — Stub returns (P-308 + P-310) =======================================


async def test_emit_before_switch_stub_returns_false() -> None:
    """P-308 stub: Aelix has no ``session_before_switch`` hook yet."""

    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    assert await runtime._emit_before_switch() is False


async def test_emit_before_fork_stub_returns_false() -> None:
    """P-308 stub: Aelix has no ``session_before_fork`` hook yet."""

    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    assert await runtime._emit_before_fork() is False


async def test_switch_session_raises_not_implemented() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    with pytest.raises(NotImplementedError, match=r"Sprint 6h₄c"):
        await runtime.switch_session("/some/path")


async def test_new_session_raises_not_implemented() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    with pytest.raises(NotImplementedError, match=r"Sprint 6h₄c"):
        await runtime.new_session()


async def test_fork_raises_not_implemented() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    with pytest.raises(NotImplementedError, match=r"Sprint 6h₄c"):
        await runtime.fork("entry-1")


async def test_import_from_jsonl_raises_not_implemented() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory)
    with pytest.raises(NotImplementedError, match=r"Sprint 6h₄c"):
        await runtime.import_from_jsonl("/p")


# === §D — Replace seam exercised via ``_apply_for_test`` =====================


async def test_apply_for_test_invokes_factory_with_new_session() -> None:
    """The factory is called with the supplied ``new_session`` exactly once."""

    h_old = _new_harness()
    received: list[Session] = []
    h_new = _new_harness()

    async def _factory(new_session: Session) -> AgentHarness:
        received.append(new_session)
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)
    new_sess = _new_session()
    await runtime._apply_for_test(new_sess)

    assert len(received) == 1
    assert received[0] is new_sess
    assert runtime.harness is h_new


async def test_apply_for_test_disposes_old_harness_once() -> None:
    """OLD harness ``dispose()`` is called exactly ONCE during replace."""

    h_old = _new_harness()
    h_old.dispose = AsyncMock()  # type: ignore[method-assign]
    h_new = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)
    await runtime._apply_for_test(_new_session())

    h_old.dispose.assert_awaited_once()


async def test_apply_for_test_invokes_before_session_invalidate_before_dispose() -> None:
    """Pi parity ordering (``:149-157``): ``beforeSessionInvalidate?.()``
    runs BEFORE the harness ``dispose()`` during ``_teardown_current``.
    """

    call_order: list[str] = []

    h_old = _new_harness()
    original_dispose = h_old.dispose

    async def _dispose_spy() -> None:
        call_order.append("dispose")
        await original_dispose()

    h_old.dispose = _dispose_spy  # type: ignore[method-assign]

    h_new = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)

    def _before() -> None:
        call_order.append("before")

    runtime.set_before_session_invalidate(_before)
    await runtime._apply_for_test(_new_session())

    assert call_order == ["before", "dispose"]


async def test_apply_for_test_invokes_rebind_session_callback_exactly_once() -> None:
    """The ``set_rebind_session`` cb fires EXACTLY ONCE per successful
    replace (P-305 / P-308 closure pin).
    """

    h_old = _new_harness()
    h_new = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)
    cb = AsyncMock()
    runtime.set_rebind_session(cb)
    await runtime._apply_for_test(_new_session())

    cb.assert_awaited_once_with(h_new)


async def test_apply_for_test_runs_without_rebind_session_registered() -> None:
    """``set_rebind_session`` is optional — replace MUST NOT crash when
    no callback is registered (Pi optional-chaining parity ``?.()``).
    """

    h_old = _new_harness()
    h_new = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)
    # No set_rebind_session() call.
    await runtime._apply_for_test(_new_session())
    assert runtime.harness is h_new


async def test_apply_for_test_runs_without_before_invalidate_registered() -> None:
    """``set_before_session_invalidate`` is optional — replace MUST NOT
    crash when no callback is registered.
    """

    h_old = _new_harness()
    h_new = _new_harness()

    async def _factory(_s: Session) -> AgentHarness:
        return h_new

    runtime = AgentSessionRuntime(h_old, _factory)
    await runtime._apply_for_test(_new_session())
    assert runtime.harness is h_new


async def test_state_session_id_on_new_harness_reflects_new_session_metadata() -> None:
    """P-306 BINDING: harness-rebuild preserves the ``_state.session_id``
    invariant. The factory produces a NEW :class:`AgentHarness` bound to
    the NEW :class:`Session`; the eager metadata read at
    ``harness/core.py:521-524`` resolves to the NEW session's ID, so
    ``runtime.harness.state.session_id`` matches the NEW session's
    storage metadata after the replace.
    """

    h_old = _new_harness(session=_new_session())
    new_sess = _new_session()

    async def _factory(s: Session) -> AgentHarness:
        return _new_harness(session=s)

    runtime = AgentSessionRuntime(h_old, _factory)
    await runtime._apply_for_test(new_sess)

    new_meta = await new_sess.get_metadata()
    assert runtime.harness._state.session_id == new_meta.id


async def test_two_consecutive_replaces_dispose_each_old_harness() -> None:
    """Two replaces → 2 disposes + 2 rebind calls."""

    h_1 = _new_harness()
    h_1.dispose = AsyncMock()  # type: ignore[method-assign]
    h_2 = _new_harness()
    h_2.dispose = AsyncMock()  # type: ignore[method-assign]
    h_3 = _new_harness()

    queue = [h_2, h_3]

    async def _factory(_s: Session) -> AgentHarness:
        return queue.pop(0)

    runtime = AgentSessionRuntime(h_1, _factory)
    cb = AsyncMock()
    runtime.set_rebind_session(cb)

    await runtime._apply_for_test(_new_session())
    await runtime._apply_for_test(_new_session())

    h_1.dispose.assert_awaited_once()
    h_2.dispose.assert_awaited_once()
    assert cb.await_count == 2
    assert runtime.harness is h_3


# === §E — Dispose (P-307) ====================================================


async def test_dispose_calls_harness_dispose_exactly_once() -> None:
    h = _new_harness()
    h.dispose = AsyncMock()  # type: ignore[method-assign]
    runtime = AgentSessionRuntime(h, _noop_factory)
    await runtime.dispose()
    h.dispose.assert_awaited_once()


async def test_dispose_invokes_before_session_invalidate_before_harness_dispose() -> None:
    call_order: list[str] = []
    h = _new_harness()
    original_dispose = h.dispose

    async def _dispose_spy() -> None:
        call_order.append("dispose")
        await original_dispose()

    h.dispose = _dispose_spy  # type: ignore[method-assign]
    runtime = AgentSessionRuntime(h, _noop_factory)

    def _before() -> None:
        call_order.append("before")

    runtime.set_before_session_invalidate(_before)
    await runtime.dispose()
    assert call_order == ["before", "dispose"]


# === §F — Frozen dataclass field locks (P-310) ===============================


def test_runtime_replace_result_default_selected_text_is_none() -> None:
    r = RuntimeReplaceResult(cancelled=False)
    assert r.cancelled is False
    assert r.selected_text is None


def test_runtime_replace_result_with_selected_text() -> None:
    r = RuntimeReplaceResult(cancelled=False, selected_text="hi")
    assert r.selected_text == "hi"


def test_runtime_replace_result_is_frozen() -> None:
    r = RuntimeReplaceResult(cancelled=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.cancelled = False  # type: ignore[misc]


def test_runtime_replace_result_field_lock() -> None:
    """Spec §H gate: dataclass fields are EXACTLY ``{cancelled, selected_text}``."""

    fields = set(RuntimeReplaceResult.__dataclass_fields__.keys())
    assert fields == {"cancelled", "selected_text"}


def test_agent_session_runtime_diagnostic_is_frozen() -> None:
    d = AgentSessionRuntimeDiagnostic(code="c", message="m")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.code = "other"  # type: ignore[misc]


def test_agent_session_runtime_diagnostic_field_lock() -> None:
    fields = set(AgentSessionRuntimeDiagnostic.__dataclass_fields__.keys())
    assert fields == {"code", "message"}
