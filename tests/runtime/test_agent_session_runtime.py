"""Sprint 6h₄b · §E.1 — :class:`AgentSessionRuntime` unit + rebind seam tests.

Pi parity: ``packages/agent/src/core/agent-session-runtime.ts:67-374``.

Covers the FOUNDATION-ONLY scope (ADR-0077 / P-302~P-310):
  - Constructor + getters (``harness`` / ``session`` / ``cwd`` /
    ``diagnostics`` / ``model_fallback_message``).
  - ``set_rebind_session`` / ``set_before_session_invalidate`` callable
    storage + invocation count balance per replace.
  - Stub returns from ``_emit_before_switch`` / ``_emit_before_fork``
    (return False — P-308).
  - ``dispose()`` calls ``before_session_invalidate`` before disposing
    the harness exactly once (P-307).
  - ``RuntimeReplaceResult`` / :class:`AgentSessionRuntimeDiagnostic`
    frozen-dataclass field locks.

Sprint 6h₄c (ADR-0079, P-331): the Sprint 6h₄b ``_apply_for_test`` test
seam is REMOVED. Replace-path assertions that previously drove the
private seam through ``_apply_for_test`` now drive the public
``switch_session`` via a tmp-path :class:`JsonlSessionRepo`. The
``test_*_raises_not_implemented`` tests for the public replace APIs are
also retired because Sprint 6h₄c fills the bodies — replacement
coverage lives in :mod:`tests.runtime.test_agent_session_runtime_replace_apis`.
``import_from_jsonl`` STAYS STUBBED — its ``NotImplementedError`` test
remains here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import (
    AgentSessionRuntime,
    AgentSessionRuntimeDiagnostic,
    RuntimeReplaceResult,
)
from aelix_agent_core.session import (
    JsonlSessionRepo,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)
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


def _runtime_kwargs() -> dict[str, Any]:
    """Sprint 6h₄c (ADR-0079, P-324): the required ``repo`` + ``fs``
    keyword args supplied by every test in this module. Tests in this
    file do NOT drive the replace path through real JSONL — they only
    exercise the constructor / seam / dispose surface. A bare
    :class:`JsonlSessionRepo` over :class:`LocalFileSystem` is enough
    to satisfy the new constructor contract.
    """

    fs = LocalFileSystem()
    return {"repo": JsonlSessionRepo(fs=fs), "fs": fs}


# === §A — Constructor + getters ==============================================


def test_runtime_constructor_stores_harness_identity() -> None:
    h = _new_harness()
    try:
        runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
        # ``harness`` getter (Pi parity P-304) returns the LIVE harness.
        assert runtime.harness is h
    finally:
        pass


def test_runtime_session_is_read_through_to_harness_session() -> None:
    """P-304 — ``runtime.session`` reads through to ``self._harness._session``."""

    session = _new_session()
    h = _new_harness(session=session)
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
    assert runtime.session is session


def test_runtime_session_is_none_when_harness_has_no_session() -> None:
    h = _new_harness()  # no session attached
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
    assert runtime.session is None


def test_runtime_cwd_returns_none_when_session_is_none() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
    assert runtime.cwd is None


def test_runtime_diagnostics_returns_copy_not_internal_list() -> None:
    """Mutation isolation — caller mutations MUST NOT bleed into the
    runtime's internal diagnostics list.
    """

    initial = [AgentSessionRuntimeDiagnostic(code="x", message="hi")]
    h = _new_harness()
    runtime = AgentSessionRuntime(
        h, _noop_factory, diagnostics=initial, **_runtime_kwargs()
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
        h, _noop_factory, model_fallback_message="fallback-x",
        **_runtime_kwargs(),
    )
    assert runtime.model_fallback_message == "fallback-x"


def test_runtime_model_fallback_message_defaults_to_none() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
    assert runtime.model_fallback_message is None


# === §B — Seam setters (P-305) ===============================================


async def test_set_rebind_session_stores_callable() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())

    async def _cb(_new_h: AgentHarness) -> None:
        return None

    runtime.set_rebind_session(_cb)
    # Internal seam asserted through replace path elsewhere; here we
    # just confirm storage round-trip via private attribute peek
    # (Aelix-additive — Pi has no introspection but the seam pin needs it).
    assert runtime._rebind_session is _cb


def test_set_before_session_invalidate_stores_callable() -> None:
    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())

    def _cb() -> None:
        return None

    runtime.set_before_session_invalidate(_cb)
    assert runtime._before_session_invalidate is _cb


# === §C — Stub returns (P-308 + carry-forward ADR-0080) ======================


async def test_emit_before_switch_stub_returns_false() -> None:
    """Sprint 6h₅a (P-338) — real body. With no handlers registered,
    ``_emit_before_switch`` returns ``False`` (gated on
    ``has_handlers("session_before_switch")``). W4 MINOR-3: signature now
    requires ``reason`` + ``target_session_file`` (no defaults).
    """

    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
    assert (
        await runtime._emit_before_switch(
            reason="resume", target_session_file=None
        )
        is False
    )


async def test_emit_before_fork_stub_returns_false() -> None:
    """Sprint 6h₅a (P-339) — real body. With no handlers registered,
    ``_emit_before_fork`` returns ``False`` (gated on
    ``has_handlers("session_before_fork")``). W4 MINOR-3: signature now
    requires ``entry_id`` + ``position`` (no defaults).
    """

    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
    assert (
        await runtime._emit_before_fork(entry_id="x", position="before")
        is False
    )


async def test_import_from_jsonl_raises_not_implemented() -> None:
    """Sprint 6h₄c (ADR-0080): ``import_from_jsonl`` STAYS STUBBED —
    no RPC command in the Pi ``RpcCommand`` union maps to it as of SHA
    ``734e08e``. Deferred to Sprint 6h₅+ per ADR-0080.
    """

    h = _new_harness()
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
    with pytest.raises(NotImplementedError, match=r"import_from_jsonl"):
        await runtime.import_from_jsonl("/p")


# === §D — Apply_for_test seam REMOVED (P-331) ================================


def test_apply_for_test_removed_per_p331() -> None:
    """Sprint 6h₄c (ADR-0079, P-331): the ``_apply_for_test`` seam from
    Sprint 6h₄b is REMOVED. Replace-path coverage now drives the real
    public API (``switch_session``) via the JSONL repo — see
    :mod:`tests.runtime.test_agent_session_runtime_replace_apis`.
    """

    assert not hasattr(AgentSessionRuntime, "_apply_for_test")


# === §E — Dispose (P-307) ====================================================


async def test_dispose_calls_harness_dispose_exactly_once() -> None:
    from unittest.mock import AsyncMock

    h = _new_harness()
    h.dispose = AsyncMock()  # type: ignore[method-assign]
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())
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
    runtime = AgentSessionRuntime(h, _noop_factory, **_runtime_kwargs())

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
