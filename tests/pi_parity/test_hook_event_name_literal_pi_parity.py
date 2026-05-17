"""Pi-parity drift detector for ``HookEventName`` (Sprint 3a, ADR-0017 v2).

Pins the Pi-verified event set at SHA ``734e08e`` (ADR-0034) and asserts
that Aelix's ``HookEventName`` Literal matches exactly. If Pi adds or
removes an event upstream, this test fails — the fix is to land a new ADR
that documents the divergence, then re-pin or amend the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from aelix_agent_core.harness.hooks import (
    AgentEventName,
    AgentHarnessEventName,
    HookEventName,
)

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "pi_agent_harness_event_names_734e08e.json"
)


def _load_fixture() -> dict[str, list[str]]:
    return json.loads(_FIXTURE.read_text())


def test_hook_event_name_literal_matches_pi_734e08e() -> None:
    """31-name HookEventName == loop(10) ∪ own(18) ∪ coding-agent(3) — Pi-verified.

    Sprint 5a (Phase 3.1, ADR-0017 §"Phase 3.1 event additions" /
    ADR-0041): the AgentHarnessEventName alias intentionally folds 3
    ``coding-agent``-owned events into the harness own bucket so a single
    enum still expresses "the runtime accepts these names". The fixture
    keeps the two Pi source files separate so the drift detector still
    distinguishes "Pi agent-core moved" from "Pi coding-agent moved".
    """

    fixture = _load_fixture()
    pi_loop = set(fixture["agent_event_names"])
    pi_own = set(fixture["harness_own_event_names"])
    pi_coding = set(fixture["coding_agent_event_names"])
    aelix = set(get_args(HookEventName))

    expected = pi_loop | pi_own | pi_coding
    missing = expected - aelix
    extra = aelix - expected
    assert aelix == expected, f"missing={missing}, extra={extra}"
    assert len(aelix) == 31, f"expected 31 names, got {len(aelix)}"


def test_agent_event_name_matches_pi_loop_set() -> None:
    """AgentEventName (loop projection) == Pi AgentEvent union (10 names)."""
    fixture = _load_fixture()
    pi_loop = set(fixture["agent_event_names"])
    aelix_loop = set(get_args(AgentEventName))
    assert aelix_loop == pi_loop
    assert len(aelix_loop) == 10


def test_agent_harness_event_name_matches_pi_own_set() -> None:
    """AgentHarnessEventName == Pi AgentHarnessOwnEvent + coding-agent additions (21).

    Sprint 5a: AgentHarnessEventName folds the 3 ``coding-agent`` events
    (``input``, ``user_bash``, ``resources_discover``) into the same enum
    so extension authors get one source of truth. The fixture splits the
    two Pi source files so drift detection stays precise.
    """

    fixture = _load_fixture()
    pi_own = set(fixture["harness_own_event_names"])
    pi_coding = set(fixture["coding_agent_event_names"])
    aelix_own = set(get_args(AgentHarnessEventName))
    expected = pi_own | pi_coding
    assert aelix_own == expected, (
        f"missing={expected - aelix_own}, extra={aelix_own - expected}"
    )
    assert len(aelix_own) == 21  # 18 harness-own + 3 coding-agent (Sprint 5a)


def test_loop_and_harness_name_sets_disjoint() -> None:
    """ADR-0036: loop and own-event name sets do NOT overlap."""
    loop = set(get_args(AgentEventName))
    own = set(get_args(AgentHarnessEventName))
    overlap = loop & own
    assert overlap == set(), f"unexpected overlap: {overlap}"


def test_hook_event_name_is_union_of_loop_and_own() -> None:
    """HookEventName == AgentEventName ∪ AgentHarnessEventName (31 = 10 + 21)."""
    loop = set(get_args(AgentEventName))
    own = set(get_args(AgentHarnessEventName))
    union = set(get_args(HookEventName))
    assert union == loop | own
    assert len(union) == len(loop) + len(own)
