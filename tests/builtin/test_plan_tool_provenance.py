"""PLAN cannot authorize extension tools by a borrowed built-in name."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_agent_core.types import AgentContext, AgentTool
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.tools import create_all_tools


async def execute(*args, **kwargs):
    raise AssertionError("A permission check must not execute a tool")


def unknown(name):
    return AgentTool(name=name, description="External tool", parameters={}, execute=execute)


async def verdict(name, tools, *, has_ui=False):
    gate = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.PLAN))
    event = ToolCallHookEvent(
        tool_call_id="plan", tool_name=name, args={},
        context=AgentContext(tools=tools) if tools is not None else None,
    )
    return await gate._on_tool_call(event, SimpleNamespace(has_ui=has_ui))


@pytest.mark.parametrize("has_ui", [False, True])
@pytest.mark.parametrize("name", ["memory_recall", "read", "grep", "agent", "server__read"])
async def test_unknown_and_borrowed_names_are_blocked(name, has_ui):
    result = await verdict(name, [unknown(name)], has_ui=has_ui)
    assert result is not None and result.block and "plan" in result.reason.lower()


@pytest.mark.parametrize("name", ["read", "grep", "find", "ls"])
async def test_actual_builtin_read_objects_are_allowed(name):
    assert await verdict(name, list(create_all_tools("/project").values())) is None


@pytest.mark.parametrize("external_last", [False, True])
async def test_the_gate_classifies_the_same_last_object_the_loop_executes(external_last):
    builtin = create_all_tools("/project")["read"]
    tools = [builtin, unknown("read")] if external_last else [unknown("read"), builtin]
    result = await verdict("read", tools)
    assert (result is not None and result.block) is external_last


async def test_missing_context_and_unmarked_copies_do_not_inherit_permission():
    assert (await verdict("read", None)).block
    builtin = create_all_tools("/project")["read"]
    assert (await verdict("read", [replace(builtin, description="Copy")])).block


async def test_delegation_description_updates_preserve_only_existing_provenance():
    from aelix_agents.tool import create_agent_tool, with_description

    builtin = create_agent_tool(description="Delegate", execute=execute)
    assert await verdict("agent", [with_description(builtin, "Updated")]) is None
    assert (await verdict("agent", [with_description(unknown("agent"), "Updated")])).block
