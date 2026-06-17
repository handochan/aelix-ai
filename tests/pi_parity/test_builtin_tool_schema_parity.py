"""Pi-parity: built-in tool SCHEMA parity (gap-inventory P0 #2, ADR-0136).

Locks in the Pi-faithful wire schema for the 7 built-in coding tools:

- camelCase parameter names (``oldText``/``newText``/``ignoreCase``) — the
  actual parity break a Pi-aligned model would hit;
- a per-field ``description`` on every property (incl. nested ``edits`` items);
- Pi's ``number`` JSON-schema type for numeric params (Pi's ``z.number()``),
  never ``integer``;
- non-trivial top-level descriptions.

Also guards against regressing to the pre-ADR-0136 snake_case schema, and
asserts the camelCase keys actually BIND in ``execute`` (a snake_case payload
must no longer silently apply).
"""

from __future__ import annotations

import pytest
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_all_tools

# Expected camelCase property names per tool (Pi parity).
EXPECTED_PROPERTIES: dict[str, set[str]] = {
    "read": {"path", "offset", "limit"},
    "bash": {"command", "timeout"},
    "edit": {"path", "edits"},
    "write": {"path", "content"},
    "grep": {"pattern", "path", "glob", "ignoreCase", "literal", "context", "limit"},
    "find": {"pattern", "path", "limit"},
    "ls": {"path", "limit"},
}

# snake_case names that diverged from Pi and MUST NOT reappear.
FORBIDDEN_PROPERTIES = {"old_text", "new_text", "ignore_case"}


def _tools(tmp_path):
    return create_all_tools(str(tmp_path))


def test_all_seven_tools_present(tmp_path):
    assert set(_tools(tmp_path)) == set(EXPECTED_PROPERTIES)


@pytest.mark.parametrize("name", sorted(EXPECTED_PROPERTIES))
def test_property_names_are_camelcase(tmp_path, name):
    tool = _tools(tmp_path)[name]
    assert set(tool.parameters["properties"]) == EXPECTED_PROPERTIES[name]


def test_edit_nested_items_are_camelcase(tmp_path):
    tool = _tools(tmp_path)["edit"]
    items = tool.parameters["properties"]["edits"]["items"]
    assert set(items["properties"]) == {"oldText", "newText"}
    assert items["required"] == ["oldText", "newText"]


def test_no_snake_case_property_names_anywhere(tmp_path):
    for name, tool in _tools(tmp_path).items():
        props = set(tool.parameters.get("properties", {}))
        assert not (props & FORBIDDEN_PROPERTIES), f"{name} leaked snake_case"
        if name == "edit":
            nested = set(
                tool.parameters["properties"]["edits"]["items"]["properties"]
            )
            assert not (nested & FORBIDDEN_PROPERTIES), "edit items leaked snake_case"


def test_every_property_has_a_description(tmp_path):
    for name, tool in _tools(tmp_path).items():
        for pname, schema in tool.parameters["properties"].items():
            desc = schema.get("description")
            assert isinstance(desc, str) and desc.strip(), (
                f"{name}.{pname} missing per-field description"
            )
        if name == "edit":
            items = tool.parameters["properties"]["edits"]["items"]["properties"]
            for pname, schema in items.items():
                desc = schema.get("description")
                assert isinstance(desc, str) and desc.strip(), (
                    f"edit.edits[].{pname} missing per-field description"
                )


def test_numeric_types_use_number_not_integer(tmp_path):
    """Pi parity: Pi's ``z.number()`` emits JSON-schema ``number``."""
    for name, tool in _tools(tmp_path).items():
        for pname, schema in tool.parameters["properties"].items():
            assert schema.get("type") != "integer", (
                f"{name}.{pname} type should be 'number' (Pi parity), not 'integer'"
            )


def test_top_level_descriptions_are_nontrivial(tmp_path):
    for name, tool in _tools(tmp_path).items():
        assert isinstance(tool.description, str)
        assert len(tool.description) >= 40, f"{name} top-level description too terse"


# --- functional: camelCase binds; snake_case no longer binds ----------------


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t"))


async def test_edit_camelcase_payload_binds(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello world\n")
    tool = _tools(tmp_path)["edit"]
    result = await _exec(
        tool,
        {"path": "x.txt", "edits": [{"oldText": "world", "newText": "aelix"}]},
    )
    assert result.is_error is False
    assert f.read_text() == "hello aelix\n"


async def test_edit_snake_case_payload_no_longer_binds(tmp_path):
    """camelCase-only contract: a legacy snake_case payload must not apply."""
    f = tmp_path / "x.txt"
    f.write_text("hello world\n")
    tool = _tools(tmp_path)["edit"]
    result = await _exec(
        tool,
        {"path": "x.txt", "edits": [{"old_text": "world", "new_text": "aelix"}]},
    )
    assert result.is_error is True
    assert f.read_text() == "hello world\n"  # unchanged — no silent write


async def test_grep_ignorecase_payload_binds(tmp_path):
    (tmp_path / "a.txt").write_text("HELLO\n")
    tool = _tools(tmp_path)["grep"]
    result = await _exec(tool, {"pattern": "hello", "ignoreCase": True})
    assert result.is_error is False
    assert "HELLO" in result.content[0].text
