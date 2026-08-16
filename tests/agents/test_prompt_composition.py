"""ADR-0196 — ``agents/prompt.py`` is pinned to the REAL kernel join.

``compose_system_prompt`` mirrors ``AgentHarness.__init__``
(``aelix_agent_core/harness/core.py:595-602``) because ``/agents use`` applies a
new identity to a LIVE harness — no rebuild, no reload — and there is no
``set_system_prompt`` on the kernel to delegate to. The kernel is off-limits, so
this test is the drift alarm: if ``core.py`` ever changes its join, this fails.

The harness is constructed with a bare ``Model(id="m", provider="p")`` — no
stream function, no session, no network.
"""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.prompt import compose_system_prompt


def _real_harness_prompt(base: str, appends: list[str]) -> str:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p"),
            system_prompt=base,
            append_system_prompt=list(appends),
        )
    )
    return harness.state.system_prompt


@pytest.mark.parametrize(
    ("base", "appends"),
    [
        ("", []),
        ("", ["only-append"]),
        ("", ["first", "second"]),
        ("BASE", []),
        ("BASE", ["only-append"]),
        ("BASE", ["first", "second"]),
        ("BASE", ["with\nnewlines", ""]),
    ],
)
def test_compose_matches_real_harness(base: str, appends: list[str]) -> None:
    assert compose_system_prompt(base, appends) == _real_harness_prompt(base, appends)


def test_empty_base_emits_no_leading_blank_line() -> None:
    """The branch most likely to be "simplified" into a bug: with an empty base
    the kernel does NOT prefix ``"\\n\\n"``."""

    assert compose_system_prompt("", ["a"]) == "a"
    assert compose_system_prompt("", ["a"]) == _real_harness_prompt("", ["a"])
