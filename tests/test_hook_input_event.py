"""Sprint 5a (Phase 3.1) — ``input`` hook reducer tests (P-24).

Pi parity reducer semantics (``agent-session.ts:987-1015`` region):

- :class:`InputContinue` (or bare ``None``) → passthrough.
- :class:`InputTransform` chains; later handlers see the patched payload.
- :class:`InputHandled` short-circuits the chain.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_agent_core.harness.hooks import (
    HookBus,
    InputContinue,
    InputHandled,
    InputHookEvent,
    InputTransform,
)
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    _ExtensionRuntime,
)


def _make_ctx_factory() -> tuple[HookBus, ExtensionContext]:
    rt = _ExtensionRuntime()
    ctx = ExtensionContext(
        rt,
        cwd="/tmp",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )
    bus = HookBus(ctx_factory=lambda: ctx)
    return bus, ctx


async def test_input_continue_passes_through() -> None:
    bus, _ = _make_ctx_factory()
    bus.on("input", lambda e, c: InputContinue())
    result = await bus.emit(InputHookEvent(text="hi"))
    assert isinstance(result, InputContinue)


async def test_input_transform_chains() -> None:
    bus, _ = _make_ctx_factory()
    seen: list[str] = []

    def t1(event: InputHookEvent, ctx: Any) -> Any:
        seen.append(event.text)
        return InputTransform(text=event.text + "!")

    def t2(event: InputHookEvent, ctx: Any) -> Any:
        seen.append(event.text)
        return InputTransform(text=event.text + "?")

    bus.on("input", t1)
    bus.on("input", t2)
    result = await bus.emit(InputHookEvent(text="hi"))
    # t1 sees raw, t2 sees t1's transform.
    assert seen == ["hi", "hi!"]
    assert isinstance(result, InputTransform)
    assert result.text == "hi!?"


async def test_input_handled_short_circuits() -> None:
    bus, _ = _make_ctx_factory()
    later_called: list[bool] = []

    def h1(event: InputHookEvent, ctx: Any) -> Any:
        return InputHandled()

    def h2(event: InputHookEvent, ctx: Any) -> Any:
        later_called.append(True)
        return None

    bus.on("input", h1)
    bus.on("input", h2)
    result = await bus.emit(InputHookEvent(text="hi"))
    assert isinstance(result, InputHandled)
    assert later_called == []


async def test_input_bare_none_treated_as_continue() -> None:
    bus, _ = _make_ctx_factory()
    bus.on("input", lambda e, c: None)
    result = await bus.emit(InputHookEvent(text="hi"))
    assert result is None


async def test_input_transform_preserves_source() -> None:
    bus, _ = _make_ctx_factory()
    seen_sources: list[str] = []

    def t1(event: InputHookEvent, ctx: Any) -> Any:
        seen_sources.append(event.source)
        return InputTransform(text="new")

    def t2(event: InputHookEvent, ctx: Any) -> Any:
        seen_sources.append(event.source)
        return None

    bus.on("input", t1)
    bus.on("input", t2)
    await bus.emit(InputHookEvent(text="hi", source="rpc"))
    assert seen_sources == ["rpc", "rpc"]  # source carried across chain


async def test_input_registers_via_extension_api() -> None:
    rt = _ExtensionRuntime()
    ext = Extension(name="t")
    api = ExtensionAPI(ext, rt)

    def handler(event: InputHookEvent, ctx: Any) -> Any:
        return InputContinue()

    unsub = api.on("input", handler)
    assert "input" in ext.handlers
    assert handler in ext.handlers["input"]
    unsub()
    assert handler not in ext.handlers.get("input", [])


def test_input_unknown_event_raises() -> None:
    rt = _ExtensionRuntime()
    ext = Extension(name="t")
    api = ExtensionAPI(ext, rt)
    with pytest.raises(KeyError):
        api.on("input_typo", lambda e, c: None)  # type: ignore[arg-type]
