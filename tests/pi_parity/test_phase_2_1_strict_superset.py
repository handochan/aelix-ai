"""Sprint 3d / Phase 2.1.4 §E.5 — Phase 2.1 strict superset closure pin.

Pi parity invariant (ADR-0039): every Pi-verified event in the
Phase 2.1 scope MUST have at least one emit site in the Aelix runtime
(``packages/aelix-agent-core/src/aelix_agent_core/**``). Any Pi event whose
owning emit site belongs to a deferred phase MUST appear in the explicit
``DEFERRED_ALLOWLIST`` below with the ADR that owns it.

This guard fails when:

1. A Pi event in the Phase 2.1 scope has no emit site in code → either land
   the emit site or move the event into ``DEFERRED_ALLOWLIST`` with an ADR.
2. A Pi event listed in ``DEFERRED_ALLOWLIST`` gains an emit site → drop it
   from the allowlist (the deferred contract was just satisfied).

The closure date is **2026-05-17**; the Pi SHA pinned by ADR-0034 is
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_RUNTIME_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "packages"
    / "aelix-agent-core"
    / "src"
    / "aelix_agent_core"
)
_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "pi_agent_harness_event_names_734e08e.json"
)


# Phase 2.1 binding scope = the 10 loop ``AgentEvent`` projections plus the
# harness-own events that land their emit site in Phase 2.1.x sprints
# (Sprint 3a setup + Sprint 3b setter emit sites + Sprint 3c parallel-mode
# tool exec ordering + Sprint 3d carry-over closure).
#
# DEFERRED_ALLOWLIST captures harness-own events whose emit owner lives in a
# later phase. Each entry MUST cite the owning ADR so future sprints have a
# straight line of accountability — adding a name here without an ADR ref is
# a contract violation.
DEFERRED_ALLOWLIST: dict[str, str] = {
    # Provider chain — Phase 4 provider adapter emits.
    "before_provider_request": "ADR-0038 (Phase 4 provider adapter)",
    "before_provider_payload": "ADR-0038 (Phase 4 provider adapter)",
    "after_provider_response": "ADR-0038 (Phase 4 provider adapter)",
    # Session lifecycle — Phase 2.2 Session Manager emits.
    "session_before_compact": "ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)",
    "session_compact": "ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)",
    "session_before_tree": "ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)",
    "session_tree": "ADR-0022 / ADR-0023 (Phase 2.2 Session Manager + Compaction)",
    # NOTE: P-10 (``abort`` emit site) closed in Sprint 3d W6 — see ADR-0039
    # P-10 row. ``AbortHookEvent`` is now emitted from
    # ``AgentHarness.abort()`` in ``harness/core.py`` with pre-clear
    # ``cleared_steer`` / ``cleared_follow_up`` snapshots. Phase 2.1 is now
    # 100% strict Pi-parity superset; only Phase 2.2 / Phase 4 owned
    # emit sites remain deferred.
}


# Camel-case names for the loop AgentEvent dataclasses; pair them with the
# Pi snake_case event names from the fixture.
_LOOP_EVENT_CLASS_BY_NAME: dict[str, str] = {
    "agent_start": "AgentStartEvent",
    "turn_start": "TurnStartEvent",
    "message_start": "MessageStartEvent",
    "message_update": "MessageUpdateEvent",
    "message_end": "MessageEndEvent",
    "tool_execution_start": "ToolExecutionStartEvent",
    "tool_execution_update": "ToolExecutionUpdateEvent",
    "tool_execution_end": "ToolExecutionEndEvent",
    "turn_end": "TurnEndEvent",
    "agent_end": "AgentEndEvent",
}


# Sprint 3a/3b/3c subset of harness-own events that MUST already have an emit
# site in code (Phase 2.1 binding scope minus DEFERRED_ALLOWLIST). Aelix names
# harness-own dataclasses with a ``HookEvent`` suffix; the runtime constructs
# them at the emit site (e.g. ``QueueUpdateHookEvent(...)``).
_HARNESS_OWN_EMIT_SUBSTRINGS: dict[str, tuple[str, ...]] = {
    # Queue / lifecycle (Sprint 3a).
    "queue_update": ("QueueUpdateHookEvent",),
    "save_point": ("SavePointHookEvent",),
    "settled": ("SettledHookEvent",),
    "before_agent_start": ("BeforeAgentStartHookEvent",),
    "context": ("ContextHookEvent",),
    # Hook bridges (Sprint 3a — emit lives in harness/core via the
    # ``emit(...)`` path with the same camel-case event name).
    "tool_call": ("ToolCallHookEvent",),
    "tool_result": ("ToolResultHookEvent",),
    # Setter emit sites (Sprint 3b).
    "model_select": ("ModelSelectHookEvent",),
    "thinking_level_select": ("ThinkingLevelSelectHookEvent",),
    "resources_update": ("ResourcesUpdateHookEvent",),
    # Abort lifecycle (Sprint 3d P-10 closure) — emit site is
    # ``AgentHarness.abort()`` in ``harness/core.py``.
    "abort": ("AbortHookEvent",),
}


# Emit-site detection scope: ``loop.py`` (loop AgentEvent emits) and
# ``harness/core.py`` (harness-own emits via setters, queue helpers, hook
# bridges). Reducer chain rebuilds in ``harness/hooks.py`` are NOT emit
# sites — they only reshape an in-flight event for the next reducer in the
# chain and never reach ``await emit(...)``. Excluding them keeps the
# closure pin honest about which events are observable to extensions.
_EMIT_SCOPE_FILES: tuple[Path, ...] = (
    _RUNTIME_ROOT / "loop.py",
    _RUNTIME_ROOT / "harness" / "core.py",
)


def _emit_scope_text() -> str:
    """Concatenated source text of the files that actually call ``emit``."""

    return "\n".join(path.read_text() for path in _EMIT_SCOPE_FILES)


def _has_emit_site(source: str, class_name: str) -> bool:
    # A "use site" is a ``ClassName(`` constructor invocation in one of the
    # emit-scope files. The runtime root excludes tests, and we deliberately
    # exclude ``harness/hooks.py`` (reducer chain rebuilds are not emits).
    pattern = re.compile(rf"\b{re.escape(class_name)}\s*\(")
    return bool(pattern.search(source))


def test_fixture_pi_sha_pin() -> None:
    fixture = json.loads(_FIXTURE.read_text())
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_all_loop_agent_events_have_emit_site() -> None:
    """All 10 Pi loop ``AgentEvent`` projections must emit in runtime code."""

    fixture = json.loads(_FIXTURE.read_text())
    loop_names = list(fixture["agent_event_names"])
    assert len(loop_names) == 10
    source = _emit_scope_text()

    missing: list[str] = []
    for name in loop_names:
        klass = _LOOP_EVENT_CLASS_BY_NAME.get(name)
        assert klass is not None, f"unmapped Pi loop event name: {name}"
        if not _has_emit_site(source, klass):
            missing.append(f"{name} ({klass})")

    assert not missing, (
        f"Phase 2.1 loop events without an emit site in runtime: {missing}"
    )


def test_phase_2_1_harness_own_events_have_emit_site() -> None:
    """Sprint 3a/3b/3c harness-own events must emit in runtime code."""

    source = _emit_scope_text()
    missing: list[str] = []
    for pi_name, candidate_classes in _HARNESS_OWN_EMIT_SUBSTRINGS.items():
        found = any(
            _has_emit_site(source, klass) for klass in candidate_classes
        )
        if not found:
            missing.append(f"{pi_name} (candidates: {candidate_classes})")

    assert not missing, (
        f"Phase 2.1 harness-own events without an emit site: {missing}"
    )


def test_deferred_allowlist_covers_all_remaining_pi_own_events() -> None:
    """Every Pi own-event is EITHER emitted today OR explicitly deferred."""

    fixture = json.loads(_FIXTURE.read_text())
    pi_own = set(fixture["harness_own_event_names"])
    emitted = set(_HARNESS_OWN_EMIT_SUBSTRINGS.keys())
    deferred = set(DEFERRED_ALLOWLIST.keys())

    covered = emitted | deferred
    gap = pi_own - covered
    assert gap == set(), (
        f"Pi own-events with neither emit site nor deferred allowlist entry: {gap}"
    )

    # Mutual exclusion: no event may live in both buckets — that would mask
    # an emit-site landing without dropping the deferred entry.
    assert emitted.isdisjoint(deferred), (
        f"events in both buckets: {emitted & deferred}"
    )


def test_deferred_allowlist_entries_remain_unemitted() -> None:
    """If a deferred event has gained an emit site, drop it from the allowlist.

    Forward-compat clause (ADR-0039): future sprints that land an emit
    site MUST move the event out of ``DEFERRED_ALLOWLIST`` in the same PR
    that introduces the emit site. This test enforces that contract.
    """

    source = _emit_scope_text()
    leaked: list[str] = []
    for pi_name in DEFERRED_ALLOWLIST:
        # Pi snake_case → CamelCase + HookEvent suffix; e.g.
        # ``session_compact`` → ``SessionCompactHookEvent``.
        camel = "".join(part.capitalize() for part in pi_name.split("_"))
        candidate = f"{camel}HookEvent"
        if _has_emit_site(source, candidate):
            leaked.append(f"{pi_name} → {candidate}")

    assert not leaked, (
        "Deferred events now have emit sites; drop them from "
        f"DEFERRED_ALLOWLIST: {leaked}"
    )


def test_p11_lockdown_no_active_tools_change_references() -> None:
    """Sprint 4a P-11 LOCKDOWN: the fabricated
    ``PendingActiveToolsChangeWrite`` variant + ``active_tools_change``
    Literal type discriminator must NOT appear in **executable** runtime
    code.

    Pi ``setActiveTools`` (``agent-harness.ts:875-882``) does NOT push to
    ``pendingSessionWrites`` and Pi ``flushPendingSessionWrites``
    (``agent-harness.ts:459-481``) has NO ``active_tools_change`` case.
    The variant was introduced by Sprint 3b W4 MAJOR-1 based on a
    fabricated Pi claim and has been removed in Sprint 4a (ADR-0022
    §"Removed claims"). This regression guard parses each runtime file
    with ``ast`` so explanatory comments / docstrings documenting the
    reversal do NOT trip the lockdown — only executable class names and
    string literals do.
    """

    import ast

    forbidden_class = "PendingActiveToolsChangeWrite"
    forbidden_literal = "active_tools_change"

    for source_file in _RUNTIME_ROOT.rglob("*.py"):
        text = source_file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(source_file))
        for node in ast.walk(tree):
            # No `class PendingActiveToolsChangeWrite:` definitions.
            if isinstance(node, ast.ClassDef):
                assert node.name != forbidden_class, (
                    f"P-11 LOCKDOWN: class {forbidden_class!r} defined in "
                    f"{source_file} — removed in Sprint 4a (ADR-0022)."
                )
            # No `PendingActiveToolsChangeWrite` identifier references in
            # imports / annotations / call sites.
            if isinstance(node, ast.Name):
                assert node.id != forbidden_class, (
                    f"P-11 LOCKDOWN: name {forbidden_class!r} referenced "
                    f"in {source_file} — removed in Sprint 4a (ADR-0022)."
                )
            if isinstance(node, ast.alias):
                assert node.name != forbidden_class, (
                    f"P-11 LOCKDOWN: import alias {forbidden_class!r} in "
                    f"{source_file} — removed in Sprint 4a (ADR-0022)."
                )
            # No `"active_tools_change"` string literal in executable
            # code. This catches `Literal["active_tools_change"]` /
            # `type: Literal[...] = "active_tools_change"` constructs.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != forbidden_literal, (
                    f"P-11 LOCKDOWN: string literal {forbidden_literal!r} "
                    f"found in {source_file} — removed in Sprint 4a "
                    "(ADR-0022)."
                )
