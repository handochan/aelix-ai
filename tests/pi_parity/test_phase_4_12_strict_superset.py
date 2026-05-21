"""Sprint 6h₄b · Phase 4.12 closure pin (ADR-0077 / ADR-0078).

FOUNDATION-only port of Pi ``AgentSessionRuntime`` +
``rebindSession`` closure. Counts STAY at **26 supported /
3 deferred / 29 total** (foundation sprint — no new RPC commands).

The 3 session-tree commands (``switch_session`` / ``fork`` / ``clone``)
remain DEFERRED and still route to :func:`_make_deferred_handler`.
Owner rebrand to ADR-0078 applied per spec §D.5 — the 4.4 / 4.9 / 4.10
/ 4.11 cascade pin allowlists were extended with the ADR-0078 prefix
in W6 so the rebrand stays observably green across cascading pins.
Sprint 6h₄c moves the 3 handlers from DEFERRED → SUPPORTED on top of
this foundation.

Closure date: **2026-05-21**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Roster: P-302 ~ P-310.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest
from aelix_agent_core.runtime import (
    AgentSessionRuntime,
    AgentSessionRuntimeDiagnostic,
    RuntimeReplaceResult,
)
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
    _make_passthrough_runtime,
    build_dispatch_table,
    run_rpc_mode,
)
from aelix_coding_agent.rpc.rpc_types import RPC_COMMAND_TYPES

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads(
        (_FIXTURES / "pi_agent_session_runtime_734e08e.json").read_text()
    )


# === §A — Counts UNCHANGED at 26 / 3 / 29 ====================================


def test_supported_count_unchanged_at_26() -> None:
    """Sprint 6h₄b is FOUNDATION ONLY — supported stays at 26."""

    assert len(SUPPORTED_COMMANDS) == 26


def test_deferred_count_unchanged_at_3() -> None:
    """Sprint 6h₄b is FOUNDATION ONLY — deferred stays at 3."""

    assert len(DEFERRED_COMMANDS) == 3


def test_supported_plus_deferred_is_29() -> None:
    """Invariant — union covers full Pi RpcCommand discriminator set."""

    assert SUPPORTED_COMMANDS.isdisjoint(DEFERRED_COMMANDS)
    assert SUPPORTED_COMMANDS | set(DEFERRED_COMMANDS.keys()) == RPC_COMMAND_TYPES
    assert len(RPC_COMMAND_TYPES) == 29


# === §B — DEFERRED set + owner string ========================================


def test_deferred_set_is_three_session_tree_commands() -> None:
    """The 3 deferred commands are ``switch_session`` / ``fork`` / ``clone``."""

    assert set(DEFERRED_COMMANDS) == {"switch_session", "fork", "clone"}


def test_deferred_owner_strings_cite_session_tree_adr() -> None:
    """Sprint 6h₄b W6 — every DEFERRED owner MUST cite ADR-0078
    (rebrand applied per spec §D.5). The cascade pin allowlists in
    4.4 / 4.9 / 4.10 / 4.11 were extended with the ADR-0078 prefix
    in the same W6 commit so the rebrand stays observable.
    """

    for cmd, owner in DEFERRED_COMMANDS.items():
        assert "ADR-0078" in owner, (
            f"{cmd!r} owner string {owner!r} does not cite ADR-0078"
        )


# === §C — Deferred handlers STILL route through `_make_deferred_handler` =====


def test_three_deferred_still_route_to_deferred_handler() -> None:
    """Sprint 6h₄c will move these 3 to runtime methods — until then,
    each MUST resolve to a stub produced by ``_make_deferred_handler``.
    """

    table = build_dispatch_table()
    for cmd in ("switch_session", "fork", "clone"):
        handler = table.get(cmd)
        assert handler is not None, f"No dispatch entry for {cmd!r}"
        name = getattr(handler, "__qualname__", repr(handler))
        # Stub closure produced by ``_make_deferred_handler`` carries
        # the originating function name in its qualname.
        assert "_make_deferred_handler" in name or "deferred" in name.lower(), (
            f"{cmd!r} dispatcher {name!r} is NOT a deferred-handler stub"
        )


# === §D — AgentSessionRuntime public surface lock ============================


_RUNTIME_PUBLIC_METHODS = {
    "switch_session",
    "new_session",
    "fork",
    "import_from_jsonl",
    "dispose",
    "set_rebind_session",
    "set_before_session_invalidate",
}

_RUNTIME_PROPERTIES = {
    "harness",
    "session",
    "cwd",
    "diagnostics",
    "model_fallback_message",
}


def test_runtime_public_methods_present() -> None:
    """The 7-method Pi surface (P-310 table) is bound on the Aelix port."""

    for name in _RUNTIME_PUBLIC_METHODS:
        assert hasattr(AgentSessionRuntime, name), (
            f"AgentSessionRuntime missing method {name!r}"
        )
        assert callable(getattr(AgentSessionRuntime, name)), (
            f"{name!r} is not callable"
        )


def test_runtime_public_properties_present() -> None:
    """The 5 read-only getters mirror Pi ``:79-97``."""

    for name in _RUNTIME_PROPERTIES:
        attr = inspect.getattr_static(AgentSessionRuntime, name)
        assert isinstance(attr, property), (
            f"{name!r} is not a property on AgentSessionRuntime"
        )


# === §E — Frozen dataclass shape locks =======================================


def test_runtime_replace_result_is_frozen() -> None:
    r = RuntimeReplaceResult(cancelled=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.cancelled = True  # type: ignore[misc]


def test_runtime_replace_result_field_lock() -> None:
    fields = set(RuntimeReplaceResult.__dataclass_fields__.keys())
    assert fields == {"cancelled", "selected_text"}


def test_diagnostic_is_frozen() -> None:
    d = AgentSessionRuntimeDiagnostic(code="x", message="y")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.code = "z"  # type: ignore[misc]


def test_diagnostic_field_lock() -> None:
    fields = set(AgentSessionRuntimeDiagnostic.__dataclass_fields__.keys())
    assert fields == {"code", "message"}


# === §F — Pi line-citation pins ==============================================


def test_runtime_module_docstring_cites_pi_range() -> None:
    """Module docstring cites ``agent-session-runtime.ts:67-374`` (Pi class)."""

    import aelix_agent_core.runtime.agent_session_runtime as mod

    assert mod.__doc__ is not None
    assert "67-374" in mod.__doc__
    assert "agent-session-runtime.ts" in mod.__doc__


def test_set_rebind_session_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime.set_rebind_session.__doc__ is not None
    assert "99-101" in AgentSessionRuntime.set_rebind_session.__doc__


def test_set_before_session_invalidate_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime.set_before_session_invalidate.__doc__ is not None
    assert "111-113" in AgentSessionRuntime.set_before_session_invalidate.__doc__


def test_teardown_current_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime._teardown_current.__doc__ is not None
    assert "149-157" in AgentSessionRuntime._teardown_current.__doc__


def test_apply_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime._apply.__doc__ is not None
    assert "159-164" in AgentSessionRuntime._apply.__doc__


def test_finish_session_replacement_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime._finish_session_replacement.__doc__ is not None
    assert (
        "166-173"
        in AgentSessionRuntime._finish_session_replacement.__doc__
    )


def test_dispose_docstring_cites_pi_lines() -> None:
    assert AgentSessionRuntime.dispose.__doc__ is not None
    assert "366-373" in AgentSessionRuntime.dispose.__doc__


def test_rebind_closure_module_docstring_cites_pi_lines() -> None:
    """``rpc_mode.py`` module docstring cites the Pi rebind closure
    line range ``rpc-mode.ts:310-349`` so reviewers can grep.
    """

    import aelix_coding_agent.rpc.rpc_mode as mod

    assert mod.__doc__ is not None
    assert "310-349" in mod.__doc__


# === §G — `run_rpc_mode` compat shim (P-309) =================================


def test_run_rpc_mode_signature_accepts_runtime_host_kwarg() -> None:
    """P-309: ADDITIVE signature change — ``runtime_host`` kwarg present."""

    sig = inspect.signature(run_rpc_mode)
    params = sig.parameters
    assert "runtime_host" in params
    assert "harness_factory" in params
    # ``runtime_host`` is keyword-only with default None.
    assert params["runtime_host"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["runtime_host"].default is None


def test_make_passthrough_runtime_is_importable() -> None:
    """P-309: shim helper is exported."""

    assert callable(_make_passthrough_runtime)


# === §H — Pi fixture immutability + line citations ===========================


def test_pi_sha_pinned_to_phase_4_12_baseline() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_fixture_command_arithmetic_is_unchanged() -> None:
    fixture = _load_fixture()
    arithmetic = fixture["command_count_arithmetic"]
    assert arithmetic["before_sprint_6h_4_b"].startswith(
        "26 supported / 3 deferred / 29 total"
    )
    assert "UNCHANGED" in arithmetic["after_sprint_6h_4_b"]
    assert set(arithmetic["still_deferred"]) == {
        "switch_session",
        "fork",
        "clone",
    }


def test_fixture_runtime_class_lines_pinned() -> None:
    fixture = _load_fixture()
    runtime = fixture["agent_session_runtime"]
    assert runtime["class_lines"] == "67-374"
    assert runtime["set_rebind_session_lines"] == "99-101"
    assert runtime["set_before_session_invalidate_lines"] == "111-113"
    assert runtime["switch_session_lines"] == "175-198"
    assert runtime["dispose_lines"] == "366-373"


def test_fixture_rebind_closure_lines_pinned() -> None:
    fixture = _load_fixture()
    closure = fixture["rebind_session_closure"]
    assert closure["rpc_mode_lines"] == "310-349"
    assert closure["registration_lines"] == "306-308"


def test_fixture_architecture_decision_is_harness_rebuild() -> None:
    fixture = _load_fixture()
    assert fixture["architecture_decision"] == "harness-rebuild"
