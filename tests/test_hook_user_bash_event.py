"""Sprint 5a (Phase 3.1) — ``user_bash`` hook reducer tests (P-25).

Pi parity (``agent-session.ts:1403``): the CLI loop emits a
``UserBashEvent`` carrying ``command`` + ``exclude_from_context`` + ``cwd``.
The reducer returns the LAST :class:`UserBashResult` produced — Pi picks
``result?`` (full replacement) over ``operations?`` (custom dispatcher).
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.harness.hooks import (
    HookBus,
    UserBashHookEvent,
    UserBashResult,
)
from aelix_coding_agent.extensions.api import (
    ExtensionContext,
    _ExtensionRuntime,
)


def _make_bus() -> HookBus:
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
    return HookBus(ctx_factory=lambda: ctx)


async def test_user_bash_no_handlers_returns_none() -> None:
    bus = _make_bus()
    result = await bus.emit(UserBashHookEvent(command="ls", cwd="/tmp"))
    assert result is None


async def test_user_bash_last_result_wins() -> None:
    bus = _make_bus()

    sentinel_op_a = object()
    sentinel_op_b = object()
    bus.on("user_bash", lambda e, c: UserBashResult(operations=sentinel_op_a))  # type: ignore[arg-type]
    bus.on("user_bash", lambda e, c: UserBashResult(operations=sentinel_op_b))  # type: ignore[arg-type]
    result = await bus.emit(UserBashHookEvent(command="echo hi"))
    assert isinstance(result, UserBashResult)
    assert result.operations is sentinel_op_b


async def test_user_bash_exclude_from_context_is_observed() -> None:
    bus = _make_bus()
    seen: list[bool] = []

    def observer(event: UserBashHookEvent, ctx: Any) -> Any:
        seen.append(event.exclude_from_context)
        return None

    bus.on("user_bash", observer)
    await bus.emit(
        UserBashHookEvent(command="pwd", exclude_from_context=True, cwd="/tmp")
    )
    assert seen == [True]


async def test_user_bash_handler_returning_none_drops_to_passthrough() -> None:
    bus = _make_bus()
    bus.on("user_bash", lambda e, c: None)
    result = await bus.emit(UserBashHookEvent(command="ls"))
    assert result is None
