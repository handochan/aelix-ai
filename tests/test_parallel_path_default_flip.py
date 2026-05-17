"""Sprint 3c G.10 — regression guard: default flip to parallel.

After §A.4 / §A.5, both :class:`AgentLoopConfig` and
:class:`AgentHarnessOptions` default to ``tool_execution="parallel"``. This
is a binding contract — callers that need sequential MUST pass it explicitly.
"""

from __future__ import annotations

from aelix_agent_core.harness.core import AgentHarnessOptions
from aelix_agent_core.types import AgentLoopConfig
from aelix_ai.streaming import Model


def _convert(messages):  # type: ignore[no-untyped-def]
    return messages


def test_agent_loop_config_default_tool_execution_is_parallel() -> None:
    cfg = AgentLoopConfig(
        model=Model(id="m", provider="m"),
        convert_to_llm=_convert,
    )
    assert cfg.tool_execution == "parallel"


def test_agent_harness_options_default_tool_execution_is_parallel() -> None:
    opts = AgentHarnessOptions()
    assert opts.tool_execution == "parallel"


def test_explicit_sequential_still_honored() -> None:
    cfg = AgentLoopConfig(
        model=Model(id="m", provider="m"),
        convert_to_llm=_convert,
        tool_execution="sequential",
    )
    assert cfg.tool_execution == "sequential"
