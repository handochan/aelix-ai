"""Sprint 4b / Phase 2.2.2 §E.5-equivalent — Phase 2.2 closure pin (ADR-0040).

Pi parity invariant (ADR-0040): every Pi-verified session_* event landed in
Sprint 4b MUST have an emit site in ``harness/core.py``, and the
Phase 2.1/2.2 ``DEFERRED_ALLOWLIST`` MUST hold ZERO Phase 2.2 entries.

This complements ``tests/pi_parity/test_phase_2_1_strict_superset.py``
(Phase 2.1 closure pin) — together they cover the cumulative Phase 2.1 +
Phase 2.2 binding scope.

The closure date is **2026-05-17**; the Pi SHA pinned by ADR-0034 is
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

# Pytest's rootdir doesn't expose ``tests`` as a package — load the
# Phase 2.1 closure pin module via its file path so we can reuse its
# ``DEFERRED_ALLOWLIST`` as the single source of truth across both pins.
_PHASE_2_1_PATH = Path(__file__).parent / "test_phase_2_1_strict_superset.py"
_spec = importlib.util.spec_from_file_location(
    "_phase_2_1_strict_superset", _PHASE_2_1_PATH
)
assert _spec is not None and _spec.loader is not None
_phase_2_1 = importlib.util.module_from_spec(_spec)
sys.modules["_phase_2_1_strict_superset"] = _phase_2_1
_spec.loader.exec_module(_phase_2_1)
DEFERRED_ALLOWLIST = _phase_2_1.DEFERRED_ALLOWLIST

_RUNTIME_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "packages"
    / "aelix-agent-core"
    / "src"
    / "aelix_agent_core"
)


_PHASE_2_2_SESSION_EVENT_CLASSES = (
    "SessionBeforeCompactHookEvent",
    "SessionCompactHookEvent",
    "SessionBeforeTreeHookEvent",
    "SessionTreeHookEvent",
)


def _harness_core_text() -> str:
    return (_RUNTIME_ROOT / "harness" / "core.py").read_text()


def test_deferred_allowlist_contains_zero_phase_2_2_entries() -> None:
    """The 4 session_* names must NOT appear in the Phase 2.1 allowlist."""

    phase_2_2_names = {
        "session_before_compact",
        "session_compact",
        "session_before_tree",
        "session_tree",
    }
    leaked = phase_2_2_names & set(DEFERRED_ALLOWLIST.keys())
    assert leaked == set(), (
        f"Phase 2.2 events still in DEFERRED_ALLOWLIST after Sprint 4b: "
        f"{leaked} — drop them per ADR-0039 forward-compat clause."
    )


def test_all_4_session_emit_sites_present_in_harness_core() -> None:
    """All 4 session_* HookEvent classes must be constructed in
    ``harness/core.py`` (the emit owner per ADR-0023)."""

    source = _harness_core_text()
    missing: list[str] = []
    for klass in _PHASE_2_2_SESSION_EVENT_CLASSES:
        pattern = re.compile(rf"\b{re.escape(klass)}\s*\(")
        if not pattern.search(source):
            missing.append(klass)
    assert not missing, (
        f"Phase 2.2 session_* events without an emit site in harness/core.py: "
        f"{missing}"
    )
