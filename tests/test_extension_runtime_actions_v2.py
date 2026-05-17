"""Sprint 5a (Phase 3.1) — ExtensionRuntimeActions 15-field dataclass tests (P-28).

Pi parity (``types.ts:1471-1488``): ``ExtensionActions`` defines 15 action
handlers. Sprint 5a extends the dataclass from 3 fields to 15 and makes
:meth:`_ExtensionRuntime.bind_core` accept the wider table.
"""

from __future__ import annotations

from dataclasses import fields

from aelix_coding_agent.extensions.api import (
    ExtensionRuntimeActions,
    _default_actions,
    _ExtensionRuntime,
    _make_throwing_stub,
)


def test_extension_runtime_actions_has_15_fields() -> None:
    names = {f.name for f in fields(ExtensionRuntimeActions)}
    expected = {
        # Sprint 3a originals.
        "get_active_tools",
        "set_active_tools",
        "get_system_prompt",
        # Sprint 5a additions (12).
        "send_message",
        "send_user_message",
        "append_entry",
        "set_session_name",
        "get_session_name",
        "set_label",
        "get_all_tools",
        "get_commands",
        "set_model",
        "get_thinking_level",
        "set_thinking_level",
        "exec",
    }
    assert names == expected
    assert len(names) == 15


def test_default_actions_returns_all_throwing_stubs() -> None:
    actions = _default_actions()
    # Every field is a callable that raises ExtensionError("unbound")
    # before bind_core lands a real impl. Spot-check the new ones.
    import pytest
    from aelix_coding_agent.extensions.api import ExtensionError

    for name in (
        "send_message",
        "set_session_name",
        "get_thinking_level",
        "exec",
        "get_all_tools",
    ):
        callable_field = getattr(actions, name)
        with pytest.raises(ExtensionError) as exc:
            callable_field()
        assert exc.value.code == "unbound"


def test_bind_core_replaces_full_action_table() -> None:
    rt = _ExtensionRuntime()
    fresh = ExtensionRuntimeActions(
        get_active_tools=lambda: ["a"],
        set_active_tools=lambda names: None,
        get_system_prompt=lambda: "p",
        send_message=_make_throwing_stub("send_message"),
        send_user_message=_make_throwing_stub("send_user_message"),
        append_entry=_make_throwing_stub("append_entry"),
        set_session_name=lambda name: None,
        get_session_name=lambda: "sess",
        set_label=lambda entry, label: None,
        get_all_tools=lambda: [],
        get_commands=_make_throwing_stub("get_commands"),
        set_model=_make_throwing_stub("set_model"),
        get_thinking_level=lambda: "off",
        set_thinking_level=lambda level: None,
        exec=_make_throwing_stub("exec"),
    )
    rt.bind_core(fresh)
    assert rt.actions is fresh
    assert rt.actions.get_session_name() == "sess"
    assert rt.actions.get_thinking_level() == "off"
