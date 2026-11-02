"""#510 (ADR-0283, owner decision 5) — RPC ``tools/set_active`` REFUSES a name the RUN keeps off.

An extension's ``set_active_tools`` drops such a name silently (it may narrow,
never widen); a client naming it is told why: excluded by ``--exclude-tools``,
or locked off by a flag or a profile. Nothing changes on a refusal, and an
allowed request still works. A name ``defaultTools`` turns off is NOT refused
(owner decision A, 2026-11-01): the client turns it on as the panel can, until
the settings are read again, and nothing is written.
"""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.tool_policy import ToolPolicy
from aelix_agent_core.types import AgentTool
from aelix_ai.streaming import Model
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.rpc_mode import _make_passthrough_runtime


def _harness(policy: ToolPolicy) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="fixture", provider="fixture"),
            tools=[AgentTool(name=n, description=n) for n in ("read", "bash", "edit")],
            tool_policy=policy,
        )
    )


@pytest.mark.parametrize(
    ("policy", "name", "reason"),
    [
        (ToolPolicy(exclude=("bash",)), "bash", "removed for this run by --exclude-tools bash"),
        (
            ToolPolicy(no_tools=True),
            "read",
            "locked off for this run: --no-tools turns every tool off",
        ),
        (
            ToolPolicy(allow=frozenset({"read"})),
            "bash",
            "locked off for this run: not named by --tools",
        ),
        (
            ToolPolicy(allow=frozenset({"read"}), allow_tag="profile"),
            "edit",
            "not named by the agent profile",
        ),
        (
            ToolPolicy(allow=frozenset({"read"}), saved_off=frozenset({"edit"})),
            "edit",
            "locked off for this run: not named by --tools",
        ),
    ],
    ids=["excluded", "no-tools", "tools", "profile", "saved-off-and-locked"],
)
async def test_a_name_the_run_keeps_off_is_refused_with_the_reason(
    policy: ToolPolicy, name: str, reason: str
) -> None:
    harness = _harness(policy)
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    before = harness.state.active_tool_names
    try:
        with pytest.raises(ValueError, match="Cannot turn on") as info:
            await plane.handle(
                "tools/set_active", {"names": ["read", name] if name != "read" else [name]}
            )
        assert reason in str(info.value)
        assert harness.state.active_tool_names == before
    finally:
        await harness.dispose()


async def test_an_allowed_selection_still_applies() -> None:
    harness = _harness(ToolPolicy(exclude=("bash",)))
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        result = await plane.handle("tools/set_active", {"names": ["read"]})
        assert result == {"activeTools": ["read"]}
        with pytest.raises(ValueError, match="loaded tools"):
            await plane.handle("tools/set_active", {"names": ["nope"]})
    finally:
        await harness.dispose()


async def test_a_saved_off_tool_is_turned_on_for_the_session_not_refused() -> None:
    """Owner decision A: refuse locked tools only. Nothing is written; ``/reload`` reads it again."""

    harness = _harness(ToolPolicy(saved_off=frozenset({"edit", "bash"})))
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        assert harness._action_get_active_tools() == ["read"]
        result = await plane.handle("tools/set_active", {"names": ["read", "edit"]})
        assert result == {"activeTools": ["read", "edit"]}
        # Only the name asked for leaves the saved-off set; bash stays off.
        assert harness.tool_policy.saved_off == frozenset({"bash"})
        # An extension can now narrow it and widen it back, as any on tool.
        harness.runtime.actions.set_active_tools(["read"])
        harness.runtime.actions.set_active_tools(["read", "edit", "bash"])
        assert harness._action_get_active_tools() == ["read", "edit"]
    finally:
        await harness.dispose()


async def test_a_saved_off_name_under_a_lock_that_allows_it_is_already_on() -> None:
    """``--tools edit`` beats a saved ``-edit``: on, and the policy is left as it was."""

    policy = ToolPolicy(allow=frozenset({"read", "edit"}), saved_off=frozenset({"edit"}))
    harness = _harness(policy)
    plane = RpcControlPlane(_make_passthrough_runtime(harness, None))
    try:
        result = await plane.handle("tools/set_active", {"names": ["edit"]})
        assert result == {"activeTools": ["edit"]}
        assert harness.tool_policy is policy
    finally:
        await harness.dispose()
