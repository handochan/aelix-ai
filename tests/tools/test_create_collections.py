"""Sprint 5b §A.3 — collection factory tests."""

from __future__ import annotations

from aelix_coding_agent.tools import (
    ALL_TOOL_NAMES,
    create_all_tools,
    create_coding_tools,
    create_read_only_tools,
)


def test_create_coding_tools_returns_4():
    tools = create_coding_tools("/tmp")
    names = [t.name for t in tools]
    assert names == ["read", "bash", "edit", "write"]


def test_create_read_only_tools_returns_4():
    tools = create_read_only_tools("/tmp")
    names = [t.name for t in tools]
    assert names == ["read", "grep", "find", "ls"]


def test_create_all_tools_returns_7_dict():
    tools = create_all_tools("/tmp")
    assert set(tools.keys()) == ALL_TOOL_NAMES
    assert len(tools) == 7


def test_all_tool_names_constant():
    assert frozenset(
        {"read", "bash", "edit", "write", "grep", "find", "ls"}
    ) == ALL_TOOL_NAMES


def test_coding_tools_extension_registers_7():
    from aelix_coding_agent.builtin.coding_tools import coding_tools_extension

    ext = coding_tools_extension("/tmp")
    assert set(ext.tools.keys()) == ALL_TOOL_NAMES
