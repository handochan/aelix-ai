"""Sprint 5b §C — tool-typed ToolCallEvent variants (ADR-0043)."""

from __future__ import annotations

from aelix_agent_core.harness.hooks import (
    BUILTIN_TOOL_NAMES,
    BashToolCallHookEvent,
    CustomToolCallHookEvent,
    CustomToolResultHookEvent,
    EditToolCallHookEvent,
    FindToolCallHookEvent,
    GrepToolCallHookEvent,
    LsToolCallHookEvent,
    ReadToolCallHookEvent,
    ToolCallHookEvent,
    ToolResultHookEvent,
    WriteToolCallHookEvent,
    is_tool_call_event_type,
    is_tool_result_event_type,
    make_tool_call_event,
    make_tool_result_event,
)


def test_factory_dispatches_to_known_variants():
    for name, cls in (
        ("bash", BashToolCallHookEvent),
        ("read", ReadToolCallHookEvent),
        ("edit", EditToolCallHookEvent),
        ("write", WriteToolCallHookEvent),
        ("grep", GrepToolCallHookEvent),
        ("find", FindToolCallHookEvent),
        ("ls", LsToolCallHookEvent),
    ):
        evt = make_tool_call_event(tool_call_id="1", tool_name=name, args={})
        assert type(evt) is cls
        assert evt.tool_name == name
        # Pi parity: subclass IS-A ToolCallHookEvent.
        assert isinstance(evt, ToolCallHookEvent)


def test_factory_unknown_falls_back_to_custom():
    evt = make_tool_call_event(
        tool_call_id="1", tool_name="my_custom_tool", args={"x": 1}
    )
    assert type(evt) is CustomToolCallHookEvent
    assert evt.tool_name == "my_custom_tool"


def test_is_tool_call_event_type_narrow():
    evt = make_tool_call_event(tool_call_id="1", tool_name="bash", args={})
    assert is_tool_call_event_type("bash", evt) is True
    assert is_tool_call_event_type("read", evt) is False


def test_tool_result_factory_symmetric():
    for name in ("bash", "read", "edit", "write", "grep", "find", "ls"):
        evt = make_tool_result_event(
            tool_call_id="1", tool_name=name, args={}, content=[]
        )
        assert evt.tool_name == name
        assert isinstance(evt, ToolResultHookEvent)


def test_tool_result_unknown_falls_back_to_custom():
    evt = make_tool_result_event(
        tool_call_id="1", tool_name="x", args={}, content=[]
    )
    assert type(evt) is CustomToolResultHookEvent


def test_is_tool_result_event_type_narrow():
    evt = make_tool_result_event(
        tool_call_id="1", tool_name="bash", args={}, content=[]
    )
    assert is_tool_result_event_type("bash", evt) is True
    assert is_tool_result_event_type("ls", evt) is False


def test_builtin_tool_names_constant():
    assert frozenset(
        {"bash", "read", "edit", "write", "grep", "find", "ls"}
    ) == BUILTIN_TOOL_NAMES


def test_base_class_still_constructible():
    """Backward compat: ``ToolCallHookEvent(...)`` still works."""

    evt = ToolCallHookEvent(tool_call_id="x", tool_name="other", args={})
    assert evt.tool_name == "other"


def test_args_dict_identity_preserved():
    """Pi parity: factory preserves the dict reference for handler mutation."""

    shared: dict = {"command": "ls"}
    evt = make_tool_call_event(
        tool_call_id="1", tool_name="bash", args=shared
    )
    evt.args["command"] = "pwd"
    assert shared["command"] == "pwd"
