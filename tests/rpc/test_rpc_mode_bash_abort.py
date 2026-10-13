"""Sprint 3 cooperative-abort — RPC _handle_bash cancellation tests.

Covers:
- ``cancelled=True`` in the response when :meth:`AgentHarness.abort_bash`
  fires while the exec is in flight.
- ``cancelled=False`` on normal (non-aborted) completion.
- The AbortSignal is unregistered from the harness after the exec (no leak).

Every call runs a REAL shell (``pwsh`` on windows-latest). An ordinary call is
awaited under :data:`tests.rpc._bash_phases.HANG_GUARD_SECONDS`, a hang guard
and not a budget. An ABORTED call asserts the one thing about time that is the
property: the abort ENDS it. It waits (under the hang guard) for the shell's first
output, aborts the running ``sleep 30``, and must return within
:data:`tests.rpc._bash_phases.ABORT_ENDS_CALL_SECONDS` of the abort — an abort
that kills nothing returns ~30 s after it, with ``cancelled`` still True, since
that comes from ``sig.aborted``. A slow call reports each phase it went through,
naming the slowest (#449, which also has the distributions both are sized from).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import _handle_bash
from aelix_coding_agent.rpc.rpc_types import RpcCommandBash, RpcSuccessResponse

from tests.rpc._bash_phases import ABORT_ENDS_CALL_SECONDS, BashPhases

#: Says it is running, then outlives any bound here; ``echo`` and ``sleep`` are
#: spelled the same in bash and pwsh (``tests/tools/test_abort_signal.py``'s
#: ``_STARTED_THEN_SLEEPS``). The first output is the event the abort waits for.
_STARTED_THEN_SLEEPS = "echo started; sleep 30"

_ABORT_WHAT = (
    "the aborted rpc bash call (an abort that kills nothing leaves 'sleep 30' "
    "to return ~30 s after it)"
)


async def _abort_once_running(harness: AgentHarness, phases: BashPhases) -> None:
    """Abort when the shell has written its first output, not on a timer."""

    await phases.shell_started()
    phases.stamp("abort")
    harness.abort_bash()


def _stream_fn() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


@pytest.fixture
def phases(monkeypatch: pytest.MonkeyPatch) -> BashPhases:
    return BashPhases().install(monkeypatch)


def _make_harness(tmp_path) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream_fn(),
            cwd=str(tmp_path),
        )
    )


async def test_handle_bash_cancelled_true_when_abort_bash_fires(
    tmp_path, phases: BashPhases
) -> None:
    """``cancelled`` is True when harness.abort_bash() fires during exec."""

    harness = _make_harness(tmp_path)
    cmd = RpcCommandBash(command=_STARTED_THEN_SLEEPS, id="b1")

    phases.stamp("call")
    handle_task = asyncio.create_task(_handle_bash(harness, cmd))

    await _abort_once_running(harness, phases)

    response = await phases.guard(
        handle_task, _ABORT_WHAT, bound=ABORT_ENDS_CALL_SECONDS, kind="abort-to-return bound"
    )
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "bash"
    data = response.data
    assert isinstance(data, dict)
    assert data["cancelled"] is True
    assert data["exitCode"] is None

    await harness.dispose()


async def test_handle_bash_cancelled_false_on_normal_completion(
    tmp_path, phases: BashPhases
) -> None:
    """``cancelled`` is False when the command exits normally."""

    harness = _make_harness(tmp_path)
    cmd = RpcCommandBash(command="echo hello", id="b2")

    response = await phases.guard(_handle_bash(harness, cmd), "rpc bash 'echo hello'")
    assert isinstance(response, RpcSuccessResponse)
    data = response.data
    assert isinstance(data, dict)
    assert data["cancelled"] is False
    assert data["exitCode"] == 0
    assert "hello" in data["output"]

    await harness.dispose()


async def test_handle_bash_signal_unregistered_after_exec(tmp_path, phases: BashPhases) -> None:
    """The AbortSignal is unregistered from the harness after normal exec."""

    harness = _make_harness(tmp_path)
    assert len(harness._active_bash_signals) == 0

    cmd = RpcCommandBash(command="echo check", id="b3")
    await phases.guard(_handle_bash(harness, cmd), "rpc bash 'echo check'")

    # Registry must be empty — no signal leak.
    assert len(harness._active_bash_signals) == 0

    await harness.dispose()


async def test_handle_bash_signal_unregistered_after_abort(tmp_path, phases: BashPhases) -> None:
    """The AbortSignal is unregistered even when the exec is aborted."""

    harness = _make_harness(tmp_path)
    cmd = RpcCommandBash(command=_STARTED_THEN_SLEEPS, id="b4")

    phases.stamp("call")
    handle_task = asyncio.create_task(_handle_bash(harness, cmd))
    await _abort_once_running(harness, phases)

    await phases.guard(
        handle_task, _ABORT_WHAT, bound=ABORT_ENDS_CALL_SECONDS, kind="abort-to-return bound"
    )

    # Signal must be cleaned up regardless of abort path.
    assert len(harness._active_bash_signals) == 0

    await harness.dispose()


async def test_handle_bash_signal_unregistered_after_exec_raises(
    tmp_path, monkeypatch
) -> None:
    """The AbortSignal is unregistered even when ops.exec raises an exception.

    This covers the ``finally: harness.unregister_bash_signal(sig)`` on the
    exception path — a leaked signal after an unexpected exec failure would
    cause a later ``abort_bash`` to fire a stale (already-satisfied) signal.
    """
    class _FailingOps:
        async def exec(self, *args, **kwargs):
            raise RuntimeError("simulated exec failure")

    # Patch create_local_bash_operations inside rpc_mode's local scope.
    # _handle_bash imports it at call time (inside the function body), so
    # we patch the module it imports from.
    import aelix_coding_agent.tools.bash as _bash_mod

    monkeypatch.setattr(_bash_mod, "create_local_bash_operations", lambda **_kw: _FailingOps())

    # Also patch the reference imported inside _handle_bash's function body.
    # Because _handle_bash does a local ``from ... import create_local_bash_operations``
    # at call time we need to patch it in the source module so the import resolves
    # to our fake.
    harness = _make_harness(tmp_path)
    assert len(harness._active_bash_signals) == 0

    cmd = RpcCommandBash(command="echo irrelevant", id="b5")

    # No shell is spawned here, so there is nothing for #449's guard to time:
    # the 5 s stays a plain bound on a call that never leaves this process.
    with pytest.raises(RuntimeError, match="simulated exec failure"):
        await asyncio.wait_for(_handle_bash(harness, cmd), timeout=5.0)

    # The finally-unregister must have run — no signal leak.
    assert len(harness._active_bash_signals) == 0

    await harness.dispose()
