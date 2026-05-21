"""Sprint 6h₄c · :meth:`AgentSessionRuntime.new_session` real body
unit tests (ADR-0079, P-325 / P-330).

Pi parity: ``agent-session-runtime.ts:200-232``.

The body waveform:
  1. ``emit_before_switch()`` → bail if cancelled.
  2. ``repo.create(JsonlSessionCreateOptions(cwd, parent_session_path))``.
  3. ``_finish_session_replacement(new_session)``.
  4. Return ``RuntimeReplaceResult(cancelled=False)``.

The Sprint 6d ``_handle_new_session`` stub at ``rpc_mode.py:309-347``
rejected ``parent_session`` — this body persists it via
``repo.create(parent_session_path=...)``. ADR-0058 carry-forward CLOSES
per P-330.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
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


async def test_new_session_creates_fresh_session_under_current_cwd(
    tmp_path: Path,
) -> None:
    """Pi parity ``:213-215``: ``repo.create(cwd=current_cwd)`` builds a
    new session in the same cwd as the source.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    result = await runtime.new_session()
    assert result.cancelled is False
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.cwd == str(tmp_path)


async def test_new_session_persists_parent_session_lineage(
    tmp_path: Path,
) -> None:
    """P-330: lineage is persisted to the JSONL header via
    ``repo.create(parent_session_path=...)``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    parent_path = "/some/parent.jsonl"
    await runtime.new_session(parent_session=parent_path)
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.parent_session_path == parent_path


async def test_new_session_raises_runtime_error_when_cwd_is_none() -> None:
    """Defensive: requires the current harness session to have a cwd."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs)

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=None), _factory, repo=repo, fs=fs
    )
    with pytest.raises(RuntimeError, match=r"cwd"):
        await runtime.new_session()


async def test_new_session_returns_runtime_replace_result(
    tmp_path: Path,
) -> None:
    """Pi parity ``:232``: return shape matches the dataclass contract."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    result = await runtime.new_session()
    assert result.cancelled is False
    # ``new_session`` never extracts user-message text.
    assert result.selected_text is None
