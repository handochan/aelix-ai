"""Sprint 6h₄c · :meth:`AgentSessionRuntime.switch_session` real body
unit tests (ADR-0079, P-325).

Pi parity: ``agent-session-runtime.ts:175-198``.

The body waveform:
  1. ``emit_before_switch()`` → bail if cancelled.
  2. ``repo.open(load_jsonl_session_metadata(fs, path))``.
  3. ``_finish_session_replacement(new_session)``.
  4. Return ``RuntimeReplaceResult(cancelled=False)``.

Companion module:
:mod:`tests.runtime.test_agent_session_runtime_replace_apis` covers
broader scenarios; this module is a focused unit suite for the
``switch_session`` body alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime, RuntimeReplaceResult
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
    SessionError,
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


async def test_returns_runtime_replace_result_with_cancelled_false(
    tmp_path: Path,
) -> None:
    """Pi parity ``:198``: returns ``RuntimeReplaceResult(cancelled=False)``."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_metadata = await target.get_metadata()

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    result = await runtime.switch_session(target_metadata.path)
    assert isinstance(result, RuntimeReplaceResult)
    assert result.cancelled is False
    assert result.selected_text is None


async def test_missing_path_raises_session_error(tmp_path: Path) -> None:
    """Pi parity: missing target file raises SessionError (propagated by
    ``repo.open`` / the metadata loader)."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    with pytest.raises(SessionError):
        await runtime.switch_session(str(tmp_path / "missing.jsonl"))


async def test_options_kwarg_accepted_for_pi_signature_compat(
    tmp_path: Path,
) -> None:
    """Pi signature compat: the ``options`` keyword exists (Pi shape;
    Aelix doesn't consume it yet — defer per ADR-0080)."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_metadata = await target.get_metadata()

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    result = await runtime.switch_session(target_metadata.path, options=None)
    assert result.cancelled is False
