"""Sprint 6h₅a · Phase 4.14 closure pin (ADR-0081 / ADR-0082).

PHASE 4.14 CLOSURE — wires the 4 Pi extension session lifecycle events
on top of the Sprint 6h₄c full RPC roster (W5 P-344 line citation
corrections — Pi line ranges verified at SHA ``734e08e``):

  - ``session_start``           (extensions/types.ts:513-519)
  - ``session_before_switch``   (extensions/types.ts:522-526)
  - ``session_before_fork``     (extensions/types.ts:529-533)
  - ``session_shutdown``        (extensions/types.ts:552-557)

Pi parity invariants verified together as anti-regression closure:

  1. HookEventName includes the 4 new entries.
  2. HOOK_RESULT_TYPES maps cancellable events to their result types.
  3. _REDUCERS shares :func:`_reducer_session_before` across compact /
     tree / switch / fork.
  4. ExtensionAPI.on / HookBus.on overload counts == 35.
  5. Pi line citations present in docstrings (drift detector).
  6. Cancel-aggregation semantics: first-cancel-wins short-circuit.
  7. Exception isolation (error_mode="continue"): chain survives.
  8. Reducer accepts both ``SessionBeforeSwitchResult`` and
     ``SessionBeforeForkResult`` types.

Closure date: **2026-05-21**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Roster: P-332 ~ P-343.
"""

from __future__ import annotations

import inspect
import json
import typing
from pathlib import Path
from typing import Any, get_args

from aelix_agent_core.harness.hooks import (
    _REDUCERS,
    HOOK_RESULT_TYPES,
    HookBus,
    HookEventName,
    SessionBeforeCompactResult,
    SessionBeforeForkHookEvent,
    SessionBeforeForkResult,
    SessionBeforeSwitchHookEvent,
    SessionBeforeSwitchResult,
    _reducer_observational,
    _reducer_session_before,
)
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "pi_extension_events_734e08e.json"
)


def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text())


# === Closure invariant 1 — HookEventName widening ===============================


def test_hook_event_name_includes_4_new_extension_events() -> None:
    """HookEventName Literal must include all 4 Pi extension events."""

    names = set(get_args(HookEventName))
    assert "session_start" in names
    assert "session_before_switch" in names
    assert "session_before_fork" in names
    assert "session_shutdown" in names
    # 36 = 35 (Phase 4.14 closure) + 1 (Issue #5 Lane C — project_trust).
    assert len(names) == 36


# === Closure invariant 2 — HOOK_RESULT_TYPES registry ============================


def test_hook_result_types_maps_cancellable_events_to_their_result_types() -> None:
    """Cancellable events map to their result class; observational ⇒ None."""

    assert HOOK_RESULT_TYPES["session_before_switch"] is SessionBeforeSwitchResult
    assert HOOK_RESULT_TYPES["session_before_fork"] is SessionBeforeForkResult
    assert HOOK_RESULT_TYPES["session_start"] is None
    assert HOOK_RESULT_TYPES["session_shutdown"] is None


# === Closure invariant 3 — _REDUCERS shared aggregation =========================


def test_reducers_share_session_before_aggregator() -> None:
    """``session_before_switch`` and ``session_before_fork`` both route
    through :func:`_reducer_session_before` so first-cancel-wins is
    inherited uniformly with the existing ``session_before_compact`` /
    ``session_before_tree`` reducers.
    """

    assert _REDUCERS["session_before_switch"] is _reducer_session_before
    assert _REDUCERS["session_before_fork"] is _reducer_session_before
    assert _REDUCERS["session_before_compact"] is _reducer_session_before
    assert _REDUCERS["session_before_tree"] is _reducer_session_before


def test_reducers_session_start_and_shutdown_are_observational() -> None:
    assert _REDUCERS["session_start"] is _reducer_observational
    assert _REDUCERS["session_shutdown"] is _reducer_observational


# === Closure invariant 4 — overload counts ======================================


def test_extension_api_on_has_36_overloads() -> None:
    """Pyright runtime introspection: ``ExtensionAPI.on`` carries 36
    ``@overload`` decls (28 baseline + 3 Sprint 5a + 4 Sprint 6h₅a +
    1 Issue #5 Lane C ``project_trust``).
    """

    overloads = typing.get_overloads(ExtensionAPI.on)
    assert len(overloads) == 36


def test_hook_bus_on_has_36_overloads() -> None:
    overloads = typing.get_overloads(HookBus.on)
    assert len(overloads) == 36


# === Closure invariant 5 — Pi line citation drift detector =====================


def test_pi_line_citations_present_in_runtime_docstring() -> None:
    """Drift detector: AgentSessionRuntime docstrings must reference the
    Pi line ranges so future PRs cannot silently lose the binding.
    """

    from aelix_agent_core.runtime.agent_session_runtime import (
        AgentSessionRuntime,
        _emit_session_shutdown_event,
    )

    # _teardown_current — Pi ``:149-157``
    doc = AgentSessionRuntime._teardown_current.__doc__ or ""
    assert "149-157" in doc

    # dispose — Pi ``:366-373``
    doc = AgentSessionRuntime.dispose.__doc__ or ""
    assert "366-373" in doc

    # _finish_session_replacement — Pi ``:166-173``
    doc = AgentSessionRuntime._finish_session_replacement.__doc__ or ""
    assert "166-173" in doc

    # _emit_before_switch — Pi ``:115-130``
    doc = AgentSessionRuntime._emit_before_switch.__doc__ or ""
    assert "115-130" in doc

    # _emit_before_fork — Pi ``:132-147``
    doc = AgentSessionRuntime._emit_before_fork.__doc__ or ""
    assert "132-147" in doc

    # _emit_session_shutdown_event helper — Pi ``runner.ts:177-189``
    doc = _emit_session_shutdown_event.__doc__ or ""
    assert "177-189" in doc


def test_pi_line_citations_present_in_session_cwd_module() -> None:
    """``session/session_cwd.py`` references Pi ``session-cwd.ts:1-59``."""

    import aelix_agent_core.session.session_cwd as cwd_mod

    src = inspect.getsource(cwd_mod)
    assert "session-cwd.ts:1-59" in src


def test_pi_line_citations_present_in_extension_runner_module() -> None:
    """``harness/_extension_runner.py`` references Pi
    ``runner.ts:680-712`` for the cancel-aggregation semantics.
    """

    import aelix_agent_core.harness._extension_runner as runner_mod

    src = inspect.getsource(runner_mod)
    assert "runner.ts:680-712" in src


# === Closure invariant 6 — cancel-aggregation semantics =========================


async def test_first_cancel_wins_short_circuits_subsequent_handlers() -> None:
    """Register 3 handlers; the 2nd returns ``cancel=True``; the 3rd
    MUST NOT run (Pi parity: ``runner.ts:680-712`` first-cancel-wins).
    """

    ext = Extension(name="t")
    runtime = _ExtensionRuntime()
    api = ExtensionAPI(ext, runtime)

    call_log: list[int] = []

    def h1(event: Any, ctx: Any) -> None:
        call_log.append(1)
        return None  # type: ignore[return-value]

    def h2(event: Any, ctx: Any) -> SessionBeforeSwitchResult:
        call_log.append(2)
        return SessionBeforeSwitchResult(cancel=True)

    def h3(event: Any, ctx: Any) -> None:
        call_log.append(3)
        return None  # type: ignore[return-value]

    api.on("session_before_switch", h1)  # type: ignore[arg-type]
    api.on("session_before_switch", h2)  # type: ignore[arg-type]
    api.on("session_before_switch", h3)  # type: ignore[arg-type]

    # Drive the reducer directly through HookBus.
    bus = HookBus(ctx_factory=lambda: None)  # type: ignore[arg-type]
    bus.on("session_before_switch", h1)
    bus.on("session_before_switch", h2)
    bus.on("session_before_switch", h3)
    result = await bus.emit(
        SessionBeforeSwitchHookEvent(
            type="session_before_switch", reason="resume"
        )
    )
    assert isinstance(result, SessionBeforeSwitchResult)
    assert result.cancel is True
    assert call_log == [1, 2]  # h3 NEVER ran


# === Closure invariant 7 — exception isolation (error_mode="continue") =========


async def test_error_mode_continue_isolates_handler_exception_chain_survives() -> None:
    """Handler 1 raises with ``error_mode="continue"``; the chain
    continues and handler 2's cancel wins.
    """

    call_log: list[str] = []

    def raising_handler(event: Any, ctx: Any) -> None:
        call_log.append("raised")
        raise RuntimeError("boom")

    def cancel_handler(event: Any, ctx: Any) -> SessionBeforeSwitchResult:
        call_log.append("cancel")
        return SessionBeforeSwitchResult(cancel=True)

    bus = HookBus(ctx_factory=lambda: None)  # type: ignore[arg-type]
    bus.on(
        "session_before_switch", raising_handler, error_mode="continue"
    )
    bus.on("session_before_switch", cancel_handler)

    result = await bus.emit(
        SessionBeforeSwitchHookEvent(
            type="session_before_switch", reason="resume"
        )
    )
    assert isinstance(result, SessionBeforeSwitchResult)
    assert result.cancel is True
    assert call_log == ["raised", "cancel"]


# === Closure invariant 8 — reducer return type union widened ====================


async def test_reducer_accepts_both_switch_and_fork_result_types() -> None:
    """The shared reducer accepts BOTH new cancellable result types
    (widened from the Sprint 4b 2-type union).
    """

    bus = HookBus(ctx_factory=lambda: None)  # type: ignore[arg-type]
    bus.on(
        "session_before_fork",
        lambda e, c: SessionBeforeForkResult(cancel=True),
    )
    result = await bus.emit(
        SessionBeforeForkHookEvent(
            type="session_before_fork", entry_id="x", position="before"
        )
    )
    assert isinstance(result, SessionBeforeForkResult)
    assert result.cancel is True


async def test_reducer_still_accepts_legacy_compact_result() -> None:
    """Sanity: the type widening did NOT break the legacy two arms."""

    bus = HookBus(ctx_factory=lambda: None)  # type: ignore[arg-type]
    bus.on(
        "session_before_compact",
        lambda e, c: SessionBeforeCompactResult(cancel=True, reason="x"),
    )
    from aelix_agent_core.harness.hooks import SessionBeforeCompactHookEvent

    result = await bus.emit(
        SessionBeforeCompactHookEvent(type="session_before_compact")
    )
    assert isinstance(result, SessionBeforeCompactResult)
    assert result.cancel is True


# === Closure invariant — fixture pin (Pi source line citations) =================


def test_fixture_pin_pi_sha_and_line_citations() -> None:
    """The fixture pins Pi SHA + the 4 event line ranges so any Pi-side
    move triggers this test (drift detector).
    """

    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"
    citations = fixture["event_line_citations"]
    assert citations["session_start"] == "513-519"
    assert citations["session_before_switch"] == "522-526"
    assert citations["session_before_fork"] == "529-533"
    assert citations["session_shutdown"] == "552-557"
