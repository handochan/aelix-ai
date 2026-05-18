"""Sprint 5b §D — ExtensionCommandContext (4 bound + 2 raise)."""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.streaming import Model
from aelix_coding_agent.extensions.api import (
    ExtensionError,
)
from aelix_coding_agent.extensions.command_context import (
    ExtensionCommandContext,
)


def _ctx(harness, repo=None):
    return ExtensionCommandContext(
        harness.runtime,
        harness=harness,
        repo=repo,
        cwd="/tmp",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )


async def test_wait_for_idle_delegates_to_harness():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    await ctx.wait_for_idle()  # idle by default; returns immediately.


async def test_navigate_tree_no_session_raises_invalid_state():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    # navigate_tree requires session; we expect harness to raise AgentHarnessError.
    from aelix_agent_core.harness.core import AgentHarnessError

    with pytest.raises(AgentHarnessError) as exc_info:
        await ctx.navigate_tree("target")
    assert exc_info.value.code == "invalid_state"


async def test_reload_delegates_to_reload_resources():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    await ctx.reload()  # no handlers → noop.


async def test_fork_raises_when_repo_missing():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    with pytest.raises(ExtensionError) as exc_info:
        await ctx.fork(None, None)  # type: ignore[arg-type]
    assert exc_info.value.code == "invalid_state"


async def test_new_session_raises_invalid_state():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    with pytest.raises(ExtensionError) as exc_info:
        await ctx.new_session()
    assert exc_info.value.code == "invalid_state"


async def test_switch_session_raises_invalid_state():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    with pytest.raises(ExtensionError) as exc_info:
        await ctx.switch_session("target")
    assert exc_info.value.code == "invalid_state"


def test_ecc_full_surface_6_methods():
    """Pi parity ``dir(ExtensionCommandContext)`` closure (P-35)."""

    members = set(dir(ExtensionCommandContext))
    for name in (
        "wait_for_idle",
        "fork",
        "navigate_tree",
        "reload",
        "new_session",
        "switch_session",
    ):
        assert name in members, f"ExtensionCommandContext missing {name}"
