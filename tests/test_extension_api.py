"""Tests for ExtensionAPI, ExtensionContext, and _ExtensionRuntime.

Covers spec E test_extension_api.py cases including stub/bind, stale guard,
and internal attribute allowlist.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallResult
from aelix_agent_core.types import AgentTool
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    ExtensionError,
    ExtensionRuntimeActions,
    _ExtensionRuntime,
)

# === Shared helpers ===


def _make_runtime() -> _ExtensionRuntime:
    return _ExtensionRuntime()


def _make_extension(name: str = "test") -> Extension:
    return Extension(name=name)


def _make_api(
    name: str = "test",
    runtime: _ExtensionRuntime | None = None,
) -> tuple[ExtensionAPI, Extension, _ExtensionRuntime]:
    rt = runtime or _make_runtime()
    ext = _make_extension(name)
    api = ExtensionAPI(ext, rt)
    return api, ext, rt


def _make_ctx(runtime: _ExtensionRuntime | None = None) -> ExtensionContext:
    rt = runtime or _make_runtime()
    return ExtensionContext(
        rt,
        cwd="/tmp",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: ["tool_a"],
        get_system_prompt=lambda: "system",
    )


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[])


# === on() records handlers ===


def test_on_records_handler_in_extension_state() -> None:
    api, ext, _ = _make_api()

    def handler(event: Any, ctx: Any) -> ToolCallResult:
        return ToolCallResult(block=False)

    api.on("tool_call", handler)

    assert "tool_call" in ext.handlers
    assert handler in ext.handlers["tool_call"]


def test_on_unknown_event_raises_keyerror() -> None:
    api, _, _ = _make_api()

    with pytest.raises(KeyError):
        api.on("not_a_real_event", lambda e, c: None)  # type: ignore[arg-type]


def test_on_returns_unsubscribe_callable() -> None:
    api, ext, _ = _make_api()

    def handler(event: Any, ctx: Any) -> None:
        pass

    unsub = api.on("context", handler)
    assert callable(unsub)
    assert handler in ext.handlers["context"]

    unsub()
    assert handler not in ext.handlers.get("context", [])


# === register_tool ===


def test_register_tool_records_tool() -> None:
    api, ext, _ = _make_api()
    tool = AgentTool(name="mytool", execute=_noop_execute)
    api.register_tool(tool)
    assert "mytool" in ext.tools
    assert ext.tools["mytool"] is tool


# === register_flag / get_flag ===


def test_register_flag_stores_default() -> None:
    api, ext, _ = _make_api()
    api.register_flag("verbose", type="bool", default=False, description="Enable verbose mode")
    assert "verbose" in ext.flags
    assert ext.flags["verbose"].default is False
    assert ext.flags["verbose"].type == "bool"


# === P0 #7 item 1 — get_flag reads runtime.flag_values (pi loader.ts:246-267) ===


def test_register_flag_seeds_flag_values_with_default() -> None:
    """``register_flag`` seeds ``runtime.flag_values`` (pi loader.ts:251-253)."""

    api, _, rt = _make_api()
    api.register_flag("verbose", type="bool", default=True)
    assert rt.flag_values["verbose"] is True


def test_get_flag_returns_seeded_default() -> None:
    """``get_flag`` reads the value seeded at registration time."""

    api, _, _ = _make_api()
    api.register_flag("debug", type="bool", default=True)
    assert api.get_flag("debug") is True


def test_get_flag_runtime_value_takes_precedence_over_default() -> None:
    """A ``set_flag_value`` override wins over the registered default (precedence)."""

    api, _, rt = _make_api()
    api.register_flag("debug", type="bool", default=True)
    rt.set_flag_value("debug", False)
    assert api.get_flag("debug") is False


def test_register_flag_does_not_overwrite_existing_flag_value() -> None:
    """Re-registering with a new default does NOT clobber an already-set value.

    Pi ``!flagValues.has(name)`` guard (loader.ts:251).
    """

    api, _, rt = _make_api()
    api.register_flag("debug", type="bool", default=True)
    rt.set_flag_value("debug", False)
    # Second registration with a different default must not re-seed.
    api.register_flag("debug", type="bool", default=True)
    assert rt.flag_values["debug"] is False
    assert api.get_flag("debug") is False


def test_get_flag_no_default_returns_none() -> None:
    """A flag registered without a default is unseeded → ``get_flag`` is ``None``.

    Pi reads ``flagValues.get(name)`` which is ``undefined`` when never seeded.
    """

    api, _, rt = _make_api()
    api.register_flag("plain", type="str")
    assert "plain" not in rt.flag_values
    assert api.get_flag("plain") is None


def test_get_flag_returns_none_for_unknown_flag() -> None:
    api, _, _ = _make_api()
    assert api.get_flag("nonexistent") is None


# === Action stubs raise before bind ===


def test_action_stubs_raise_before_bind() -> None:
    api, _, rt = _make_api()
    # Runtime has throwing stubs by default (no bind_core called).
    with pytest.raises(ExtensionError) as exc_info:
        api.get_active_tools()
    assert exc_info.value.code == "unbound"


def test_set_active_tools_stub_raises_before_bind() -> None:
    api, _, _ = _make_api()
    with pytest.raises(ExtensionError) as exc_info:
        api.set_active_tools(["tool_a"])
    assert exc_info.value.code == "unbound"


def test_get_system_prompt_stub_raises_before_bind() -> None:
    api, _, _ = _make_api()
    with pytest.raises(ExtensionError) as exc_info:
        api.get_system_prompt()
    assert exc_info.value.code == "unbound"


# === Action stubs succeed after bind_core ===


def test_action_stubs_succeed_after_harness_bind() -> None:
    api, _, rt = _make_api()
    active_tools = ["tool_a", "tool_b"]

    from aelix_coding_agent.extensions.api import _make_throwing_stub

    actions = ExtensionRuntimeActions(
        get_active_tools=lambda: list(active_tools),
        set_active_tools=lambda names: active_tools.__setitem__(slice(None), names),
        get_system_prompt=lambda: "bound system prompt",
        # Sprint 5a additions: provide throwing stubs for the 12 new actions
        # so the existing test continues to exercise only the Sprint 3a
        # surface it was written for.
        send_message=_make_throwing_stub("send_message"),
        send_user_message=_make_throwing_stub("send_user_message"),
        append_entry=_make_throwing_stub("append_entry"),
        set_session_name=_make_throwing_stub("set_session_name"),
        get_session_name=_make_throwing_stub("get_session_name"),
        set_label=_make_throwing_stub("set_label"),
        get_all_tools=_make_throwing_stub("get_all_tools"),
        get_commands=_make_throwing_stub("get_commands"),
        set_model=_make_throwing_stub("set_model"),
        get_thinking_level=_make_throwing_stub("get_thinking_level"),
        set_thinking_level=_make_throwing_stub("set_thinking_level"),
        exec=_make_throwing_stub("exec"),
        refresh_tools=lambda: None,
        refresh_hooks=lambda: None,
    )
    rt.bind_core(actions)

    result = api.get_active_tools()
    assert result == ["tool_a", "tool_b"]

    prompt = api.get_system_prompt()
    assert prompt == "bound system prompt"


# === Stale context raises after dispose ===


def test_stale_extension_context_raises_after_dispose() -> None:
    rt = _make_runtime()
    ctx = _make_ctx(rt)

    # Before invalidation: public attributes are accessible.
    _ = ctx.cwd  # should not raise

    rt.invalidate("runtime disposed")

    # After invalidation: any public attribute access raises ExtensionError("stale").
    with pytest.raises(ExtensionError) as exc_info:
        _ = ctx.cwd
    assert exc_info.value.code == "stale"


def test_stale_context_raises_on_method_call() -> None:
    rt = _make_runtime()
    ctx = _make_ctx(rt)
    rt.invalidate("gone")

    with pytest.raises(ExtensionError) as exc_info:
        ctx.get_active_tools()
    assert exc_info.value.code == "stale"


# === Internal attributes skip stale check ===


def test_internal_attribute_access_does_not_trigger_stale_check() -> None:
    """Names starting with _ or in {is_idle, abort, assert_active} skip staleness check."""
    rt = _make_runtime()
    ctx = _make_ctx(rt)
    rt.invalidate("disposed")

    # Internal names should not raise even after invalidation.
    # _runtime is an internal attr (starts with _) — access via ctx._runtime
    # exercises the underscore-bypass branch of __getattribute__ directly.
    _ = ctx._runtime

    # assert_active is in the explicit allowlist — calling it raises stale error
    # (that's what it's supposed to do), but the __getattribute__ guard itself
    # should not raise before delegating to the method.
    # We verify by confirming the ExtensionError comes from assert_active,
    # not from a secondary __getattribute__ call.
    with pytest.raises(ExtensionError) as exc_info:
        ctx.assert_active()
    assert exc_info.value.code == "stale"

    # is_idle and abort are in the INTERNAL_NAMES allowlist — __getattribute__
    # skips the stale check for them, so they are accessible as attributes.
    # Calling them after stale is fine (they delegate to captured lambdas).
    is_idle_attr = object.__getattribute__(ctx, "_is_idle")
    assert callable(is_idle_attr)


def test_dunder_attributes_bypass_stale_check() -> None:
    """Dunder names (start with _) bypass the staleness guard."""
    rt = _make_runtime()
    ctx = _make_ctx(rt)
    rt.invalidate("gone")

    # __class__ is a dunder — starts with _ so it bypasses the guard.
    assert ctx.__class__ is ExtensionContext


# === add_cleanup ===


def test_add_cleanup_registered_on_extension() -> None:
    api, ext, _ = _make_api()
    cleanup_called: list[bool] = []

    def cleanup() -> None:
        cleanup_called.append(True)

    api.add_cleanup(cleanup)
    assert cleanup in ext.cleanups

    # Invoking it directly works.
    cleanup()
    assert cleanup_called == [True]


def test_add_cleanup_returns_unregister_callable() -> None:
    api, ext, _ = _make_api()

    def cleanup() -> None:
        pass

    unreg = api.add_cleanup(cleanup)
    assert cleanup in ext.cleanups

    unreg()
    assert cleanup not in ext.cleanups


# === P0 #7 item 2 — register*/get_flag assert_active on a stale runtime ===


def _noop_handler(event: Any, ctx: Any) -> None:
    return None


def test_on_raises_on_stale_runtime() -> None:
    api, _, rt = _make_api()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc_info:
        api.on("context", _noop_handler)
    assert exc_info.value.code == "stale"


def test_register_tool_raises_on_stale_runtime() -> None:
    api, _, rt = _make_api()
    rt.invalidate("disposed")
    tool = AgentTool(name="late", execute=_noop_execute)
    with pytest.raises(ExtensionError) as exc_info:
        api.register_tool(tool)
    assert exc_info.value.code == "stale"


def test_register_flag_raises_on_stale_runtime() -> None:
    api, _, rt = _make_api()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc_info:
        api.register_flag("x", type="bool", default=True)
    assert exc_info.value.code == "stale"


def test_get_flag_raises_on_stale_runtime() -> None:
    api, _, rt = _make_api()
    api.register_flag("x", type="bool", default=True)
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc_info:
        api.get_flag("x")
    assert exc_info.value.code == "stale"


def test_register_command_raises_on_stale_runtime() -> None:
    api, _, rt = _make_api()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc_info:
        api.register_command("c", handler=_noop_handler)
    assert exc_info.value.code == "stale"


def test_register_shortcut_raises_on_stale_runtime() -> None:
    api, _, rt = _make_api()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc_info:
        api.register_shortcut("ctrl+x", handler=_noop_handler)
    assert exc_info.value.code == "stale"


def test_register_message_renderer_raises_on_stale_runtime() -> None:
    api, _, rt = _make_api()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc_info:
        api.register_message_renderer("custom", _noop_handler)
    assert exc_info.value.code == "stale"


def test_add_cleanup_raises_on_stale_runtime() -> None:
    """Aelix-additive guard (item 2): stale runtime rejects cleanup registration."""

    api, _, rt = _make_api()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc_info:
        api.add_cleanup(lambda: None)
    assert exc_info.value.code == "stale"


# === H-3: stale ctx raises for is_idle() and abort() after dispose ===


def test_stale_ctx_is_idle_raises() -> None:
    """After harness dispose(), ctx.is_idle() raises ExtensionError('stale', ...)."""
    rt = _make_runtime()
    ctx = _make_ctx(rt)
    rt.invalidate("AgentHarness has been disposed")

    with pytest.raises(ExtensionError) as exc_info:
        ctx.is_idle()
    assert exc_info.value.code == "stale"


def test_stale_ctx_abort_raises() -> None:
    """After harness dispose(), ctx.abort() raises ExtensionError('stale', ...)."""
    rt = _make_runtime()
    ctx = _make_ctx(rt)
    rt.invalidate("AgentHarness has been disposed")

    with pytest.raises(ExtensionError) as exc_info:
        ctx.abort()
    assert exc_info.value.code == "stale"


# === H-9: aelix.on() after harness init is a no-op ===


async def test_aelix_on_after_harness_init_is_noop() -> None:
    """Handlers registered via aelix.on() after harness construction are NOT wired in."""
    from collections.abc import AsyncIterator

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.types import AgentTool
    from aelix_ai.messages import (
        AssistantMessage,
        TextContent,
        ToolCallContent,
    )
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantMessageEvent,
        AssistantStartEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )
    from aelix_ai.tools import ToolResult

    captured_api: list[ExtensionAPI] = []

    def my_extension(aelix: ExtensionAPI) -> None:
        captured_api.append(aelix)

    ext = Extension(name="my_ext")
    rt = _make_runtime()
    api = ExtensionAPI(ext, rt)
    my_extension(api)

    # Build harness — wires ext.handlers into the bus at construction time.
    def _make_stream() -> Any:
        idx = {"i": 0}
        turns = [
            AssistantMessage(
                content=[ToolCallContent(tool_call_id="t1", tool_name="noop", input={})],
                stop_reason="tool_use",
            ),
            AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn"),
        ]

        async def fn(
            model: Model,
            context: Context,
            options: SimpleStreamOptions,
        ) -> AsyncIterator[AssistantMessageEvent]:
            i = idx["i"]
            idx["i"] += 1
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantEndEvent(message=turns[i])

        return fn

    async def noop_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    noop_tool = AgentTool(name="noop", execute=noop_execute)

    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext],
            tools=[noop_tool],
            stream_fn=_make_stream(),
            runtime=rt,
        )
    )

    # Register a handler AFTER harness construction — must NOT be wired into the bus.
    late_handler_called: list[bool] = []

    def late_handler(event: Any, ctx: Any) -> None:
        late_handler_called.append(True)

    captured_api[0].on("tool_call", late_handler)

    await h.prompt("do the thing")

    # The late handler must NOT have fired.
    assert late_handler_called == [], (
        "Handler registered after harness init must not fire (H-9 contract)"
    )
