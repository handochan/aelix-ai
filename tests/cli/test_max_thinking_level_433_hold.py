"""#433 review round 2 — ``/agents use`` fits a profile's level only to a SETTLED model.

The fix clamps the profile's ``thinking:`` to the live model (``agents/service.py``
``use()``), as the launch clamps ``defaultThinkingLevel``. A held launch's
placeholder (#367: a late provider's ``Model(id, provider)`` with ``api`` ``unknown``)
supports only ``off`` and says nothing about the model the run ends on, so there the
clamp is skipped and a level the placeholder does not list is skipped too, exactly
as before #433. Clamped, ``thinking: high`` would have turned the live level ``off``
on the placeholder. This pins that guard (the sabotage that applies the clamped level
unconditionally stayed green without it; ``.omc/probes/433-live/r2/sabotage.txt``).

Driven through the real ``_async_main`` over #367's held launch, with its fixture.
Hermetic: fake keys, an isolated agent dir and home, no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.cli.test_late_provider_refused_367 import (  # noqa: F401 - env is a fixture
    _HELD,
    _agents_use,
    _openrouter,
    env,
)

_THINKER = "---\nname: plainprof\ndescription: names a level only\nthinking: high\n---\nthinker\n"


async def test_agents_use_on_a_held_placeholder_does_not_fit_the_level_to_it(
    env: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_agent_core.harness.core as core

    _openrouter(monkeypatch, env, "exported")
    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    (agents / "plainprof.md").write_text(_THINKER, encoding="utf-8")

    levels: list[Any] = []
    real_set = core.AgentHarness.set_thinking_level

    async def _spy(self: Any, level: str) -> None:
        levels.append((self.current_model.api, level))
        await real_set(self, level)

    monkeypatch.setattr(core.AgentHarness, "set_thinking_level", _spy)
    seen = await _agents_use(env, monkeypatch, ["--model", "sessext/m1"], "plainprof")

    assert (seen["code"], seen["launch"], seen["use"]) == (0, _HELD, _HELD)
    # Nothing was set on the placeholder: not ``high`` as written (it lists only
    # ``off``), and not ``off``, which the clamp would have answered.
    assert [entry for entry in levels if entry[0] == "unknown"] == []
