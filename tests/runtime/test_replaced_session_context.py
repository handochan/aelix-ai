"""Sprint 6h₅b · Phase 4.15 — :class:`ReplacedSessionContext` Protocol +
factory tests (ADR-0083, P-356/P-357).

Pi parity citations:
  - Protocol shape: ``extensions/types.ts:366-381``.
  - Factory:        ``agent-session.ts:3087-3095``.

The factory returns a :class:`types.SimpleNamespace` (Pi
``Object.defineProperties`` clone idiom). Structural conformance to
:class:`ReplacedSessionContext` is enforced via
:data:`typing.runtime_checkable`.
"""

from __future__ import annotations

import types as _types
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import ReplacedSessionContext
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _new_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
        )
    )


def test_factory_returns_simple_namespace() -> None:
    """Pi parity: factory uses ``Object.defineProperties`` clone idiom —
    Aelix mirror is :class:`types.SimpleNamespace`.
    """

    h = _new_harness()
    ctx = h.create_replaced_session_context()
    assert isinstance(ctx, _types.SimpleNamespace)


def test_factory_result_conforms_to_protocol() -> None:
    """Structural conformance: the SimpleNamespace passes the
    runtime-checkable Protocol isinstance probe.
    """

    h = _new_harness()
    ctx = h.create_replaced_session_context()
    assert isinstance(ctx, ReplacedSessionContext)


def test_factory_baseline_fields_match_make_context() -> None:
    """The factory mirrors :meth:`AgentHarness._make_context` for the
    non-action fields (cwd / has_ui / model).
    """

    h = _new_harness()
    ctx = h.create_replaced_session_context()
    assert ctx.cwd == h._options.cwd
    assert ctx.has_ui is False
    assert ctx.model is h._state.model


async def test_send_message_routes_through_action() -> None:
    """``ctx.send_message(...)`` enqueues onto the harness's next-turn
    queue via :meth:`_action_send_message`.
    """

    h = _new_harness()
    ctx = h.create_replaced_session_context()

    user_msg = UserMessage(content=[TextContent(text="hello")])
    await ctx.send_message(user_msg)
    assert len(h._next_turn_queue) == 1
    assert h._next_turn_queue[0] is user_msg


async def test_send_user_message_routes_through_action() -> None:
    """``ctx.send_user_message(text)`` builds a UserMessage and enqueues
    via :meth:`_action_send_user_message`.
    """

    h = _new_harness()
    ctx = h.create_replaced_session_context()

    await ctx.send_user_message("hello world")
    assert len(h._next_turn_queue) == 1
    queued = h._next_turn_queue[0]
    assert getattr(queued, "role", None) == "user"


def test_factory_exposes_six_extension_command_methods() -> None:
    """Sprint 6h₅b W6 (P-364 W5 MAJOR fix): the ``ReplacedSessionContext``
    Protocol extends :class:`ExtensionCommandContext` with 6 methods
    (Pi ``extensions/types.ts:333-364`` + ``:371``). The factory must
    expose all 6 on the returned :class:`types.SimpleNamespace` so
    structural conformance to the widened Protocol holds.
    """

    h = _new_harness()
    ctx = h.create_replaced_session_context()
    for name in (
        "wait_for_idle",
        "new_session",
        "fork",
        "navigate_tree",
        "switch_session",
        "reload",
    ):
        assert hasattr(ctx, name), f"missing {name!r} on ReplacedSessionContext"
        assert callable(getattr(ctx, name))


async def test_unbound_runtime_commands_raise_clear_error() -> None:
    """Sprint 6h₅b W6 (P-364): when ``runtime`` is not supplied to the
    factory (test-only / unattached path), the 3 runtime-bound commands
    raise :class:`RuntimeError` so callers don't silently no-op.
    """

    import pytest

    h = _new_harness()
    ctx = h.create_replaced_session_context()  # no runtime kwarg
    for method_name in ("new_session", "fork", "switch_session"):
        method = getattr(ctx, method_name)
        with pytest.raises(RuntimeError, match=r"not bound to a runtime"):
            # Pass anything; method should reject before reading args.
            await method("dummy") if method_name != "new_session" else await method()


async def test_reload_is_aelix_additive_stub() -> None:
    """Sprint 6h₅b W6 (P-364): ``reload`` is an Aelix-additive stub
    raising :class:`NotImplementedError` (no in-place reload primitive
    today — Pi's is a TUI helper). Exposed for Protocol conformance.
    """

    import pytest

    h = _new_harness()
    ctx = h.create_replaced_session_context()
    with pytest.raises(NotImplementedError, match=r"reload"):
        await ctx.reload()
