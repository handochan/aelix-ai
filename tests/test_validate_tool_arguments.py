"""Issue #13 — real JSON-Schema validation for ``validate_tool_arguments``.

Pi parity ``validateToolArguments`` (``packages/ai/src/utils/validation.ts``):
a lenient coerce-then-validate gate. These tests pin the contract that matters
for aelix's weak-local-model-to-frontier range:

- obvious string→scalar mistakes are coerced (``"5"`` → ``5``, ``"true"`` →
  ``True``) so loosely-typed models still dispatch;
- unknown keys are PRESERVED (additive — never stripped);
- required + type violations raise :class:`ToolArgumentValidationError` with a
  structured, path-qualified, model-readable message echoing the args;
- malformed / empty schemas (MCP- / extension-registered tools) pass through
  rather than crash.
"""

from __future__ import annotations

import asyncio

import pytest
from aelix_ai.tools import (
    Tool,
    ToolArgumentValidationError,
    validate_tool_arguments,
)

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "offset": {"type": "number"},
        "limit": {"type": "number"},
    },
    "required": ["path"],
}

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "oldText": {"type": "string"},
                    "newText": {"type": "string"},
                },
                "required": ["oldText", "newText"],
            },
        },
    },
    "required": ["path", "edits"],
}


def _validate(tool: Tool, args: dict) -> dict:
    return asyncio.run(validate_tool_arguments(tool, args))


def test_valid_args_pass_through_unchanged() -> None:
    tool = Tool(name="read", parameters=READ_SCHEMA)
    assert _validate(tool, {"path": "a.py", "offset": 5}) == {
        "path": "a.py",
        "offset": 5,
    }


def test_string_to_number_coercion() -> None:
    tool = Tool(name="read", parameters=READ_SCHEMA)
    out = _validate(tool, {"path": "a.py", "offset": "5"})
    assert out["offset"] == 5
    assert isinstance(out["offset"], int)


def test_integer_field_keeps_fractional_string_for_rejection() -> None:
    tool = Tool(
        name="t",
        parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
    )
    # "5" -> 5 (valid); "5.5" can't be an integer, stays a string and fails.
    assert _validate(tool, {"n": "5"}) == {"n": 5}
    with pytest.raises(ToolArgumentValidationError):
        _validate(tool, {"n": "5.5"})


def test_boolean_coercion_case_insensitive() -> None:
    tool = Tool(
        name="g",
        parameters={"type": "object", "properties": {"flag": {"type": "boolean"}}},
    )
    assert _validate(tool, {"flag": "true"}) == {"flag": True}
    assert _validate(tool, {"flag": "False"}) == {"flag": False}


def test_pi_full_coercion_table() -> None:
    """Issue #13 / review fix: mirror pi's coercePrimitiveByType for ALL source
    types, not just strings — weak models emit `1` for bools, `null` for
    numbers, bare numbers for strings, etc., and pi coerces-and-passes them."""
    num = Tool(
        name="n",
        parameters={"type": "object", "properties": {"x": {"type": "number"}}},
    )
    boolt = Tool(
        name="b",
        parameters={"type": "object", "properties": {"x": {"type": "boolean"}}},
    )
    strt = Tool(
        name="s",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    # number: null -> 0; bool -> 1/0
    assert _validate(num, {"x": None}) == {"x": 0}
    assert _validate(num, {"x": True}) == {"x": 1}
    # boolean: null -> False; numeric 1/0 -> True/False
    assert _validate(boolt, {"x": None}) == {"x": False}
    assert _validate(boolt, {"x": 1}) == {"x": True}
    assert _validate(boolt, {"x": 0}) == {"x": False}
    # string: null -> ""; bool -> "true"/"false"; number -> its string form
    assert _validate(strt, {"x": None}) == {"x": ""}
    assert _validate(strt, {"x": True}) == {"x": "true"}
    assert _validate(strt, {"x": 5}) == {"x": "5"}
    assert _validate(strt, {"x": 5.0}) == {"x": "5"}


def test_ambiguous_value_left_for_validator_to_reject() -> None:
    """A non-1/0 number against a boolean is NOT coerced (stays for rejection)."""
    boolt = Tool(
        name="b",
        parameters={"type": "object", "properties": {"x": {"type": "boolean"}}},
    )
    with pytest.raises(ToolArgumentValidationError):
        _validate(boolt, {"x": 7})


def test_recursive_ref_schema_does_not_crash() -> None:
    """Review fix: a recursive $ref + deep instance must NOT crash the turn
    (jsonschema raises RecursionError, not SchemaError) — pass through."""
    tool = Tool(
        name="rec",
        parameters={
            "type": "object",
            "properties": {"x": {"$ref": "#"}},
        },
    )
    deep: dict = {}
    cur = deep
    for _ in range(400):
        cur["x"] = {}
        cur = cur["x"]
    # Must not raise (RecursionError is caught → pass through).
    out = _validate(tool, {"x": deep})
    assert out == {"x": deep}


def test_unresolvable_ref_schema_does_not_crash() -> None:
    """Review fix: an unresolvable $ref (referencing.Unresolvable) must pass
    through, not crash the turn."""
    tool = Tool(
        name="badref",
        parameters={
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/missing"}},
        },
    )
    out = _validate(tool, {"x": 1})
    assert out == {"x": 1}


def test_unknown_keys_are_preserved_not_stripped() -> None:
    tool = Tool(name="read", parameters=READ_SCHEMA)
    out = _validate(tool, {"path": "a.py", "extra": "kept"})
    assert out["extra"] == "kept"


def test_missing_required_raises_with_field_name() -> None:
    tool = Tool(name="read", parameters=READ_SCHEMA)
    with pytest.raises(ToolArgumentValidationError) as exc:
        _validate(tool, {"offset": 5})
    msg = str(exc.value)
    assert 'Validation failed for tool "read"' in msg
    assert "path" in msg
    assert "Received arguments:" in msg


def test_bad_type_raises_path_qualified() -> None:
    tool = Tool(name="read", parameters=READ_SCHEMA)
    with pytest.raises(ToolArgumentValidationError) as exc:
        _validate(tool, {"path": "a.py", "offset": "not-a-number"})
    assert "$.offset" in str(exc.value)


def test_nested_array_of_objects_error_is_path_qualified() -> None:
    tool = Tool(name="edit", parameters=EDIT_SCHEMA)
    with pytest.raises(ToolArgumentValidationError) as exc:
        _validate(tool, {"path": "a.py", "edits": [{"newText": "x"}]})
    assert "$.edits[0]" in str(exc.value)
    assert "oldText" in str(exc.value)


def test_empty_schema_is_noop() -> None:
    tool = Tool(name="mcp_thing", parameters={})
    assert _validate(tool, {"anything": 1, "nested": {"x": "y"}}) == {
        "anything": 1,
        "nested": {"x": "y"},
    }


def test_malformed_schema_does_not_block_execution() -> None:
    # A non-dict / nonsense schema must not crash the dispatch path.
    tool = Tool(name="weird", parameters={"type": "not-a-real-type"})
    # Should pass through (we cannot validate it) rather than raise.
    assert _validate(tool, {"a": 1}) == {"a": 1}


def test_args_echo_is_truncated_for_huge_payloads() -> None:
    tool = Tool(name="read", parameters=READ_SCHEMA)
    huge = {"offset": 1, "blob": "x" * 10000}  # missing required 'path'
    with pytest.raises(ToolArgumentValidationError) as exc:
        _validate(tool, huge)
    assert "… (truncated)" in str(exc.value)


def test_original_args_not_mutated() -> None:
    tool = Tool(name="read", parameters=READ_SCHEMA)
    args = {"path": "a.py", "offset": "5"}
    _validate(tool, args)
    # Coercion happens on a deep copy — the caller's dict is untouched.
    assert args["offset"] == "5"
