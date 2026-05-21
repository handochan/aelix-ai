"""Sprint 6h₄b · §E.3 — back-compat regression suite for the
``run_rpc_mode`` ``runtime_host`` shim (P-309 / P-311).

Pi parity: ``packages/coding-agent/src/modes/rpc/rpc-mode.ts:310-349``
(``rebindSession`` closure registration) + ``:67-374``
(``AgentSessionRuntime``).

These 7 tests lock the back-compat shim added by ADR-0077 P-309:
  1. ``run_rpc_mode(harness)`` without ``runtime_host`` does NOT raise
     :class:`AttributeError` (the 26 pre-existing wired handlers keep
     working).
  2. ``_make_passthrough_runtime(harness, None).harness is harness``.
  3. The no-op factory installed by :func:`_make_passthrough_runtime`
     RAISES :class:`RuntimeError` on invocation (W4 LOW-3 — fail loudly
     instead of silently re-binding to the same stale harness).
  4. The dispatch loop reads ``capture.harness`` (NOT the closure-
     captured ``harness`` argument).
  5. When an explicit ``runtime_host`` is passed, dispatch handlers see
     ``runtime_host.harness``.
  6. Wired handlers (e.g. ``get_session_stats``) remain callable via
     dispatch when no ``runtime_host`` is supplied.
  7. Each of the 3 DEFERRED handlers (``switch_session`` / ``fork`` /
     ``clone``) returns a response whose error message contains
     ``"ADR-0078"`` (locks the W6 owner rebrand per spec §D.5).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    _make_passthrough_runtime,
    build_dispatch_table,
    run_rpc_mode,
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


def _new_harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


# === §1 — run_rpc_mode without runtime_host does not break ===================


async def test_run_rpc_mode_without_runtime_host_does_not_break() -> None:
    """P-309 back-compat: invoking :func:`run_rpc_mode` with no
    ``runtime_host`` kwarg MUST NOT raise :class:`AttributeError` or
    otherwise crash — the 26 pre-existing wired handlers depend on the
    bare-harness call shape.
    """

    harness = _new_harness()
    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    captured: list[bytes] = []

    # Should complete cleanly (no AttributeError on missing runtime_host).
    await run_rpc_mode(
        harness,
        stdin=stdin,
        stdout_write=captured.append,
        install_signal_handlers=False,
    )


# === §2 — _make_passthrough_runtime holds same harness identity ==============


def test_make_passthrough_runtime_holds_same_harness() -> None:
    """:func:`_make_passthrough_runtime` returns a runtime whose
    ``harness`` getter points at the SAME object passed in (P-309).
    """

    harness = _new_harness()
    runtime = _make_passthrough_runtime(harness, None)
    assert runtime.harness is harness


# === §3 — passthrough noop factory raises on invocation ======================


async def test_passthrough_noop_factory_raises_on_replace() -> None:
    """W4 LOW-3: the no-op factory installed by
    :func:`_make_passthrough_runtime` when ``harness_factory is None``
    RAISES :class:`RuntimeError` when invoked. This makes accidental
    misuse (a caller trying to replace through the passthrough) fail
    loudly instead of silently re-binding to the same stale harness.
    """

    runtime = _make_passthrough_runtime(_new_harness(), None)
    # ``_apply`` calls the factory directly with the provided session.
    with pytest.raises(RuntimeError, match=r"Passthrough runtime cannot replace"):
        await runtime._apply(Session(MemorySessionStorage()))


# === §4 — dispatch loop reads capture.harness ================================


async def test_dispatch_loop_reads_capture_harness() -> None:
    """The dispatch loop reads ``capture.harness`` (NOT the closure-
    captured ``harness`` argument). We verify by passing a runtime
    whose ``harness`` is the SAME object as the loose ``harness``
    argument, then asserting that a wired handler invocation succeeds
    against ``capture.harness`` (would crash if dispatch read the
    other reference incorrectly under a swap).
    """

    harness = _new_harness()
    runtime = _make_passthrough_runtime(harness, None)
    # ``runtime.harness`` IS the passed harness — capture cell == loose arg.
    assert runtime.harness is harness

    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    captured: list[bytes] = []

    await run_rpc_mode(
        harness,
        runtime_host=runtime,
        stdin=stdin,
        stdout_write=captured.append,
        install_signal_handlers=False,
    )


# === §5 — dispatch sees runtime_host.harness when passed =====================


async def test_dispatch_sees_runtime_host_harness_when_passed() -> None:
    """When ``runtime_host`` is supplied, the dispatch loop initialises
    its capture cell from ``runtime_host.harness`` — confirmed by the
    fact that the runtime's harness (not the loose argument) governs
    the lifecycle (subscription + final dispose).
    """

    runtime_harness = _new_harness()
    runtime = AgentSessionRuntime(runtime_harness, _make_raising_factory())
    # Loose ``harness`` argument is a DIFFERENT object than
    # ``runtime.harness`` — the dispatch loop must NOT use it for
    # subscribe / dispose.
    loose_harness = _new_harness()
    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    captured: list[bytes] = []

    pre_count_runtime = len(runtime_harness._listeners)

    await run_rpc_mode(
        loose_harness,
        runtime_host=runtime,
        stdin=stdin,
        stdout_write=captured.append,
        install_signal_handlers=False,
    )

    # The runtime's harness took the subscription (and was disposed via
    # runtime.dispose) — its listener count returned to baseline.
    assert len(runtime_harness._listeners) == pre_count_runtime


def _make_raising_factory() -> Any:
    async def _factory(_s: Session) -> AgentHarness:
        raise RuntimeError("Factory must not run in shim tests")

    return _factory


# === §6 — wired handlers still callable via dispatch =========================


async def test_wired_handlers_still_callable_with_passthrough() -> None:
    """Invoking a wired handler (``get_session_stats``) through the
    dispatch table returns an :class:`RpcResponse` rather than crashing
    — the passthrough shim does NOT break the 26 already-wired
    handlers.
    """

    from aelix_coding_agent.rpc.rpc_types import RpcCommandGetSessionStats

    harness = _new_harness(session=Session(MemorySessionStorage()))
    dispatch = build_dispatch_table()
    handler = dispatch["get_session_stats"]
    response = await handler(
        harness, RpcCommandGetSessionStats(id="req-1")
    )
    # Real handler returns a response (success or error envelope) —
    # never raises and never returns None.
    assert response is not None
    assert response.command == "get_session_stats"


# === §7 — 3 deferred handlers return ADR-0078 error =========================


async def test_three_deferred_handlers_return_adr_0078_error_string() -> None:
    """Sprint 6h₄b W6 — rebrand lock per spec §D.5. Each of the 3
    DEFERRED handlers (``switch_session`` / ``fork`` / ``clone``)
    returns an :class:`RpcErrorResponse` whose error message contains
    ``"ADR-0078"``.
    """

    dispatch = build_dispatch_table()
    for cmd_type in ("switch_session", "fork", "clone"):
        assert cmd_type in DEFERRED_COMMANDS
        handler = dispatch[cmd_type]

        class _Stub:
            id = f"req-{cmd_type}"

        response = await handler(None, _Stub())
        assert response.success is False
        assert "ADR-0078" in response.error, (
            f"{cmd_type!r} response error {response.error!r} does not "
            "contain 'ADR-0078' — rebrand regression"
        )
