"""Sprint 5b / Phase 3.2 §G closure pin (ADR-0044).

Pi parity invariant (ADR-0044): every Pi-verified surface in the Phase 3.2
scope (7 built-in coding tools + 3 emit sites + 8 tool-typed ToolCallEvent
variants + 4 ExtensionCommandContext methods) has a corresponding binding in
Aelix OR an explicit deferred entry.

Closure date: **2026-05-17**; Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from aelix_agent_core.harness.hooks import (
    BUILTIN_TOOL_NAMES,
    BashToolCallHookEvent,
    BashToolResultHookEvent,
    CustomToolCallHookEvent,
    CustomToolResultHookEvent,
    EditToolCallHookEvent,
    EditToolResultHookEvent,
    FindToolCallHookEvent,
    FindToolResultHookEvent,
    GrepToolCallHookEvent,
    GrepToolResultHookEvent,
    LsToolCallHookEvent,
    LsToolResultHookEvent,
    ReadToolCallHookEvent,
    ReadToolResultHookEvent,
    WriteToolCallHookEvent,
    WriteToolResultHookEvent,
    is_tool_call_event_type,
    is_tool_result_event_type,
    make_tool_call_event,
    make_tool_result_event,
)
from aelix_coding_agent.extensions.command_context import (
    ExtensionCommandContext,
)
from aelix_coding_agent.tools import (
    ALL_TOOL_NAMES,
    create_all_tools,
    create_coding_tools,
    create_read_only_tools,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


# === §A — 7 built-in coding tools ===


def test_all_7_pi_tools_have_aelix_factories() -> None:
    fixture = json.loads(
        (_FIXTURES / "pi_coding_tools_734e08e.json").read_text()
    )
    pi_names = set(fixture["tool_names"])
    assert pi_names == ALL_TOOL_NAMES, (
        f"Pi tool names mismatch: pi={pi_names} aelix={ALL_TOOL_NAMES}"
    )


def test_create_coding_tools_returns_4_mutation_tools() -> None:
    tools = create_coding_tools("/tmp")
    assert [t.name for t in tools] == ["read", "bash", "edit", "write"]


def test_create_read_only_tools_returns_4_read_tools() -> None:
    tools = create_read_only_tools("/tmp")
    assert [t.name for t in tools] == ["read", "grep", "find", "ls"]


def test_create_all_tools_returns_7() -> None:
    tools = create_all_tools("/tmp")
    assert set(tools.keys()) == ALL_TOOL_NAMES


# === §B — 3 emit sites (active in Aelix) ===


def test_three_phase_3_2_events_emit_sites_active() -> None:
    """Sprint 5b drops input/user_bash/resources_discover from DEFERRED_ALLOWLIST."""

    import importlib.util as _importlib_util

    spec = _importlib_util.spec_from_file_location(
        "_phase_2_1_superset",
        Path(__file__).parent / "test_phase_2_1_strict_superset.py",
    )
    assert spec is not None and spec.loader is not None
    mod = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("input", "user_bash", "resources_discover"):
        assert name not in mod.DEFERRED_ALLOWLIST, (
            f"{name!r} still deferred — ADR-0044 forward-compat violation."
        )
        assert name in mod._HARNESS_OWN_EMIT_SUBSTRINGS


# === §C — 8 tool-typed ToolCallEvent + 8 ToolResultEvent variants ===


def test_8_tool_typed_call_variants_registered() -> None:
    fixture = json.loads(
        (_FIXTURES / "pi_tool_call_event_variants_734e08e.json").read_text()
    )
    aelix_call = {
        BashToolCallHookEvent,
        ReadToolCallHookEvent,
        EditToolCallHookEvent,
        WriteToolCallHookEvent,
        GrepToolCallHookEvent,
        FindToolCallHookEvent,
        LsToolCallHookEvent,
        CustomToolCallHookEvent,
    }
    assert len(aelix_call) == 8
    assert len(fixture["tool_call_event_variants"]) == 8


def test_8_tool_typed_result_variants_registered() -> None:
    aelix_result = {
        BashToolResultHookEvent,
        ReadToolResultHookEvent,
        EditToolResultHookEvent,
        WriteToolResultHookEvent,
        GrepToolResultHookEvent,
        FindToolResultHookEvent,
        LsToolResultHookEvent,
        CustomToolResultHookEvent,
    }
    assert len(aelix_result) == 8


def test_factory_dispatch_is_correct() -> None:
    for name in BUILTIN_TOOL_NAMES:
        evt = make_tool_call_event(tool_call_id="1", tool_name=name, args={})
        assert evt.tool_name == name
        evt2 = make_tool_result_event(
            tool_call_id="1", tool_name=name, args={}, content=[]
        )
        assert evt2.tool_name == name


def test_narrow_helpers_present() -> None:
    """``isToolCallEventType`` Pi parity helpers are exported."""

    assert is_tool_call_event_type is not None
    assert is_tool_result_event_type is not None


# === §D — ExtensionCommandContext 4 bound + 2 raising ===


def test_ecc_full_pi_surface_6_methods() -> None:
    fixture = json.loads(
        (_FIXTURES / "pi_extension_command_context_methods_734e08e.json").read_text()
    )
    members = set(dir(ExtensionCommandContext))
    for pi_method in fixture["methods"]:
        snake = _camel_to_snake(pi_method)
        assert snake in members, (
            f"ExtensionCommandContext missing {pi_method} → {snake}"
        )


# === §H — durable regression for ADR-0044 closure ===


def test_phase_3_2_closure_summary() -> None:
    """Phase 3 closure assertion — superseded by Phase 4 closure (ADR-0046).

    Originally this test pinned the allowlist to the 3 Phase-4 provider
    entries (the only Pi-verified events not yet emitting in Aelix at the
    Sprint 5b boundary). Sprint 6a (Phase 4.1) lands ``_make_stream_fn``
    which emits all 3 provider events, so the allowlist is now **empty**
    per ADR-0046.

    Forward-compat clause (ADR-0039): once Phase 4 closes, the Phase 3
    closure invariant degenerates to a subset assertion — the 3 provider
    entries MUST be gone, and the remaining allowlist (if any) carries
    only Phase-5+ deferrals.
    """

    import importlib.util as _importlib_util

    spec = _importlib_util.spec_from_file_location(
        "_phase_2_1_superset",
        Path(__file__).parent / "test_phase_2_1_strict_superset.py",
    )
    assert spec is not None and spec.loader is not None
    mod = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    keys = set(mod.DEFERRED_ALLOWLIST.keys())
    for provider_event in (
        "before_provider_request",
        "before_provider_payload",
        "after_provider_response",
    ):
        assert provider_event not in keys, (
            f"Sprint 6a (ADR-0046) closure violation: {provider_event!r} "
            "still in DEFERRED_ALLOWLIST"
        )
