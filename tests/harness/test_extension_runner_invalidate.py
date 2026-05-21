"""Sprint 6h₅b · Phase 4.15 — :meth:`ExtensionRunner.invalidate` + bridge
tests (ADR-0083, P-362 / P-363 — SYNTHESIS per spec §J).

Pi parity: ``ExtensionRunner.invalidate`` (``runner.ts:466-473``).

Synthesis decision: the runtime is the SINGLE SOURCE OF TRUTH for
staleness (``_ExtensionRuntime._stale_message``); the runner is the
Pi-named entry point that delegates through the ``_invalidate_runtime``
callable bridge. The runner has NO ``_stale_message`` field of its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import (
    PI_STALENESS_MESSAGE,
    AgentSessionRuntime,
)
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.extensions.api import (
    ExtensionError,
    _ExtensionRuntime,
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


def test_invalidate_sets_stale_via_runtime() -> None:
    """``runner.invalidate(msg)`` propagates to the runtime's stale flag."""

    rt = _ExtensionRuntime()
    runner = ExtensionRunner(extensions=[], _invalidate_runtime=rt.invalidate)
    assert rt.is_stale is False
    runner.invalidate("my-message")
    assert rt.is_stale is True
    with pytest.raises(ExtensionError) as exc_info:
        rt.assert_active()
    assert exc_info.value.code == "stale"
    assert "my-message" in str(exc_info.value)


def test_invalidate_default_is_pi_staleness_message() -> None:
    """``runner.invalidate()`` (no message) sets the Pi-verbatim string."""

    rt = _ExtensionRuntime()
    runner = ExtensionRunner(extensions=[], _invalidate_runtime=rt.invalidate)
    runner.invalidate()
    assert rt._stale_message == PI_STALENESS_MESSAGE


def test_invalidate_idempotent() -> None:
    """Calling ``invalidate`` twice is safe — last message wins."""

    rt = _ExtensionRuntime()
    runner = ExtensionRunner(extensions=[], _invalidate_runtime=rt.invalidate)
    runner.invalidate("first")
    runner.invalidate("second")
    assert rt._stale_message == "second"
    # Still raises stale after the second call.
    with pytest.raises(ExtensionError):
        rt.assert_active()


def test_invalidate_no_bridge_is_safe_noop() -> None:
    """Without a bridge (default), :meth:`invalidate` is a no-op."""

    runner = ExtensionRunner()
    # Should not raise.
    runner.invalidate()
    runner.invalidate("with-msg")


def test_harness_wires_invalidate_bridge() -> None:
    """:class:`AgentHarness` wires ``_invalidate_runtime`` so the
    delegated invalidate flows through to the harness runtime.
    """

    h = _new_harness()
    runner = h.extension_runner
    assert runner._invalidate_runtime is not None
    runner.invalidate("propagated")
    assert h.runtime._stale_message == "propagated"


def test_extension_runtime_invalidate_default_aligned_with_pi_message() -> None:
    """:meth:`_ExtensionRuntime.invalidate` default argument now aligns
    with :data:`PI_STALENESS_MESSAGE` so callers bypassing the runner
    still see the Pi verbatim string.
    """

    rt = _ExtensionRuntime()
    rt.invalidate()  # no message arg
    assert rt._stale_message == PI_STALENESS_MESSAGE


async def test_teardown_invokes_runner_invalidate(tmp_path: Path) -> None:
    """Pi parity ``runner.ts:466-473`` (P-363): ``_teardown_current``
    calls ``runner.invalidate(PI_STALENESS_MESSAGE)`` between EMIT and
    ``before_session_invalidate``. We verify by observing the OLD
    harness's runtime is stale after a replace.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(old_h, _factory, repo=repo, fs=fs)
    # Capture the OLD runtime BEFORE replace (after teardown the runner
    # bridge becomes a no-op).
    old_runtime = old_h.runtime

    await runtime.switch_session(target_meta.path)
    # OLD runtime was invalidated by _teardown_current.
    assert old_runtime.is_stale is True
    # The new harness's runtime is NOT stale.
    assert runtime.harness.runtime.is_stale is False


async def test_dispose_invokes_runner_invalidate(tmp_path: Path) -> None:
    """Pi parity (P-363): :meth:`AgentSessionRuntime.dispose` invokes
    ``runner.invalidate(PI_STALENESS_MESSAGE)`` between EMIT and
    ``before_session_invalidate``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    h = _new_harness(session=source)
    h_runtime = h.runtime
    runtime = AgentSessionRuntime(h, _factory, repo=repo, fs=fs)
    cb = MagicMock()  # sync callback (before_session_invalidate is sync)
    runtime.set_before_session_invalidate(cb)

    await runtime.dispose()
    # The harness runtime is now stale.
    assert h_runtime.is_stale is True
    cb.assert_called_once()


def test_assert_active_is_no_op_synthesis() -> None:
    """Per spec §J SYNTHESIS: the runner's ``assert_active`` is a no-op
    because the runtime owns the single source of truth. Callers go
    through :meth:`_ExtensionRuntime.assert_active` directly via the
    extension context.
    """

    runner = ExtensionRunner()
    # No exception even when no bridge wired.
    runner.assert_active()
