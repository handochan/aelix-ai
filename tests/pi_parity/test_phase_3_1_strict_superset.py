"""Sprint 5a Phase 3.1 §E closure pin (ADR-0041).

Pi parity invariant (ADR-0041): every Pi-verified surface in the Phase 3.1
scope (3 new hook event names + ExtensionAPI 48-method surface +
ExtensionContext 14 fields) has a corresponding registration/binding in
Aelix OR an explicit deferred entry with its owning ADR.

This guard mechanises three claims:

1. Aelix ``HookEventName`` Literal includes the 3 Sprint 5a new event
   names AND those names appear in
   ``tests/pi_parity/test_phase_2_1_strict_superset.py``
   ``DEFERRED_ALLOWLIST`` with the Sprint 5b owner (ADR-0042).
2. Aelix :class:`ExtensionAPI` exposes all 23 Pi non-event members + 31
   ``on()``-accepted event names.
3. Aelix :class:`ExtensionContext` exposes all 14 Pi field names (with
   the ``ui`` field gated behind ADR-0033 deferral).

Closure date: **2026-05-17**; Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import re
from typing import get_args

from aelix_agent_core.harness.hooks import HOOK_RESULT_TYPES, HookEventName
from aelix_coding_agent.extensions.api import (
    ExtensionAPI,
    ExtensionContext,
)

_PHASE_3_1_NEW_EVENTS = {"input", "user_bash", "resources_discover"}


def _camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def test_three_new_events_registered_in_hook_event_name_literal() -> None:
    """P-24/P-25/P-26 closure: input/user_bash/resources_discover landed."""

    names = set(get_args(HookEventName))
    missing = _PHASE_3_1_NEW_EVENTS - names
    assert not missing, f"Sprint 5a events missing from HookEventName: {missing}"


def test_three_new_events_have_result_types_registered() -> None:
    """Each new event has an entry in HOOK_RESULT_TYPES (None or class)."""

    for name in _PHASE_3_1_NEW_EVENTS:
        assert name in HOOK_RESULT_TYPES, (
            f"{name!r} missing from HOOK_RESULT_TYPES"
        )


def test_three_new_events_no_longer_in_deferred_allowlist() -> None:
    """Sprint 5b (ADR-0044) closure: emit sites landed; allowlist purged.

    Pi parity forward-compat clause (ADR-0039) — once an event gains an
    emit site, it MUST be dropped from ``DEFERRED_ALLOWLIST`` in the same
    PR. Sprint 5b §B lands all 3 emit sites.
    """

    import importlib.util as _importlib_util
    from pathlib import Path as _Path

    spec = _importlib_util.spec_from_file_location(
        "_phase_2_1_superset",
        _Path(__file__).parent / "test_phase_2_1_strict_superset.py",
    )
    assert spec is not None and spec.loader is not None
    mod = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    DEFERRED_ALLOWLIST = mod.DEFERRED_ALLOWLIST

    for name in _PHASE_3_1_NEW_EVENTS:
        assert name not in DEFERRED_ALLOWLIST, (
            f"{name!r} still in DEFERRED_ALLOWLIST after Sprint 5b emit-site "
            "landing; drop the entry per ADR-0044 forward-compat clause."
        )


def test_extension_api_surface_covers_pi_non_event_methods() -> None:
    """ExtensionAPI exposes every Pi non-event member (P-22)."""

    pi_non_event = [
        "registerTool",
        "registerCommand",
        "registerShortcut",
        "registerFlag",
        "getFlag",
        "registerMessageRenderer",
        "sendMessage",
        "sendUserMessage",
        "appendEntry",
        "setSessionName",
        "getSessionName",
        "setLabel",
        "exec",
        "getActiveTools",
        "getAllTools",
        "setActiveTools",
        "getCommands",
        "setModel",
        "getThinkingLevel",
        "setThinkingLevel",
        "registerProvider",
        "unregisterProvider",
        "events",
    ]
    members = set(dir(ExtensionAPI))
    missing: list[str] = []
    for pi_name in pi_non_event:
        snake = _camel_to_snake(pi_name)
        if snake not in members:
            missing.append(f"{pi_name} → {snake}")
    assert not missing, f"Pi ExtensionAPI surface gaps: {missing}"


def test_extension_context_surface_covers_pi_14_fields() -> None:
    """ExtensionContext exposes every Pi field (P-23)."""

    pi_fields = [
        "ui",
        "hasUI",
        "cwd",
        "sessionManager",
        "modelRegistry",
        "model",
        "isIdle",
        "signal",
        "abort",
        "hasPendingMessages",
        "shutdown",
        "getContextUsage",
        "compact",
        "getSystemPrompt",
    ]
    members = set(dir(ExtensionContext))
    missing: list[str] = []
    for pi_name in pi_fields:
        snake = {"hasUI": "has_ui"}.get(pi_name, _camel_to_snake(pi_name))
        if snake not in members:
            missing.append(f"{pi_name} → {snake}")
    assert not missing, f"Pi ExtensionContext field gaps: {missing}"


def test_hook_event_name_has_31_entries() -> None:
    """Sprint 5a closure: 31 = Sprint 3a 28 + Sprint 5a 3."""

    assert len(get_args(HookEventName)) == 31


def test_adr_0041_deadline_not_passed() -> None:
    """ADR-0041 specifies 2026-06-14 deadline for Sprint 5b. If passed without 5b ship, demote ADR.

    Skippable via OMC_SKIP_DEADLINE_GUARD=1 for offline development.
    """
    import os

    import pytest

    if os.environ.get("OMC_SKIP_DEADLINE_GUARD"):
        pytest.skip("Deadline guard skipped via env")
    from datetime import date

    deadline = date(2026, 6, 14)
    today = date.today()
    if today > deadline:
        pytest.fail(
            f"ADR-0041 deadline 2026-06-14 has passed (today: {today}). "
            f"Sprint 5b must ship OR ADR-0041 must be demoted from Accepted → Draft."
        )
