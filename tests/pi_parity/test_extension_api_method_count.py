"""Sprint 5a (Phase 3.1) — ExtensionAPI Pi-parity drift fixture (P-22).

Asserts that every Pi ``ExtensionAPI`` member (29 ``on()`` overload event
names + 23 non-event method/property names at SHA ``734e08e``) has a
matching attribute on Aelix's :class:`ExtensionAPI` after snake_case
conversion. Aelix is allowed to *add* members (additive superset per
ADR-0041 forward-compat clause) but MUST NOT *remove* any.

If Pi adds or removes a method upstream, this test fails — the fix is to
land a new ADR documenting the divergence, then re-pin the fixture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from aelix_coding_agent.extensions.api import ExtensionAPI

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "pi_extension_api_methods_734e08e.json"
)


def _camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _load_fixture() -> dict[str, list[str]]:
    return json.loads(_FIXTURE.read_text())


def test_fixture_pi_sha_pin() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_pi_non_event_methods_present_on_extension_api() -> None:
    fixture = _load_fixture()
    members = set(dir(ExtensionAPI))
    missing: list[str] = []
    for pi_name in fixture["non_event_method_names"]:
        snake = _camel_to_snake(pi_name)
        if snake not in members:
            missing.append(f"{pi_name} → {snake}")
    assert not missing, (
        f"Pi ExtensionAPI methods missing on Aelix ExtensionAPI: {missing}"
    )


# Sprint 3a P-1 (ADR-0017 v2): Pi exposes 4 ``session_*`` wishlist events
# that are NOT yet shipped in Aelix harness/hooks. They are tracked for a
# future ADR (Phase 2.2 / Phase 5 session manager expansion). Excluded from
# the drift check until they ship.
_SPRINT_5A_DEFERRED_PI_ON_EVENTS = frozenset(
    {"session_start", "session_before_switch", "session_before_fork", "session_shutdown"}
)


def test_pi_on_event_names_accepted_by_extension_api() -> None:
    """Every non-deferred Pi ``on()`` event must be accepted by Aelix's ``on()``.

    Aelix's runtime guard is ``if event not in HOOK_RESULT_TYPES: raise``,
    so we check membership in :data:`HOOK_RESULT_TYPES`.

    The 4 ``session_*`` events listed in
    :data:`_SPRINT_5A_DEFERRED_PI_ON_EVENTS` are tracked as Phase 2.2+
    wishlist (Sprint 3a P-1, ADR-0017 v2 §"Deferred to Phase 2.2+"). When
    the owning ADR lands they MUST be removed from the deferred set in
    the same PR that lands their dataclasses.
    """

    from aelix_agent_core.harness.hooks import HOOK_RESULT_TYPES

    fixture = _load_fixture()
    pi_events = set(fixture["on_event_names"])
    aelix_events = set(HOOK_RESULT_TYPES.keys())
    expected = pi_events - _SPRINT_5A_DEFERRED_PI_ON_EVENTS
    missing = expected - aelix_events
    assert not missing, (
        f"Pi ExtensionAPI on() events missing from HOOK_RESULT_TYPES: {missing}"
    )


def test_aelix_extension_api_count_matches_or_exceeds_pi() -> None:
    """Sprint 5a Phase 3.1 closure: Aelix is an additive superset of Pi."""

    fixture = _load_fixture()
    members = set(dir(ExtensionAPI))
    pi_snake = {
        _camel_to_snake(name) for name in fixture["non_event_method_names"]
    }
    pi_covered = pi_snake & members
    # Every Pi non-event member maps to an Aelix attribute.
    assert pi_covered == pi_snake, (
        f"Pi non-event members missing from Aelix: {pi_snake - members}"
    )
