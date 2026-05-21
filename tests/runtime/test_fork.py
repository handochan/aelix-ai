"""Sprint 6h₄c · :meth:`AgentSessionRuntime.fork` real body unit tests
(ADR-0079, P-325).

Pi parity: ``agent-session-runtime.ts:234-320`` (Aelix drops the
in-memory branch ``:303-319`` per P-325 SYNTHESIS).

The body waveform:
  1. ``emit_before_fork()`` → bail if cancelled.
  2. ``session.get_entry(entry_id)`` — raise if missing.
  3. Resolve ``target_leaf_id`` + ``selected_text`` based on position.
  4. ``repo.fork(...)`` writes the new JSONL with parentSession header.
  5. ``_finish_session_replacement(new_session)``.
  6. Return ``RuntimeReplaceResult(cancelled, selected_text)``.
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
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
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


async def _runtime_with_user_msg(
    tmp_path: Path, text: str = "hello"
) -> tuple[AgentSessionRuntime, str, Session]:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    entry_id = await source.append_message(
        UserMessage(content=[TextContent(text=text)])
    )

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    return runtime, entry_id, source


async def test_fork_position_before_extracts_user_message_text(
    tmp_path: Path,
) -> None:
    """Pi parity ``:262-265``: position="before" over a user message
    extracts the message text into ``selected_text``.
    """

    runtime, entry_id, _ = await _runtime_with_user_msg(tmp_path, "ping")
    result = await runtime.fork(entry_id, position="before")
    assert result.cancelled is False
    assert result.selected_text == "ping"


async def test_fork_position_at_returns_no_selected_text(
    tmp_path: Path,
) -> None:
    """Pi parity: position="at" returns ``selected_text=None``."""

    runtime, entry_id, _ = await _runtime_with_user_msg(tmp_path)
    result = await runtime.fork(entry_id, position="at")
    assert result.cancelled is False
    assert result.selected_text is None


async def test_fork_invalid_entry_id_raises_value_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:247``: invalid entry id raises ValueError with the
    canonical Pi message.
    """

    runtime, _, _ = await _runtime_with_user_msg(tmp_path)
    with pytest.raises(ValueError, match=r"Invalid entry ID for forking"):
        await runtime.fork("does-not-exist")


async def test_fork_assistant_message_before_raises_value_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:254-255``: position="before" requires a user
    message — assistant messages raise ValueError.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    asst_id = await source.append_message(
        AssistantMessage(
            content=[TextContent(text="reply")], stop_reason="end_turn"
        )
    )

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    with pytest.raises(ValueError, match=r"Invalid entry ID for forking"):
        await runtime.fork(asst_id, position="before")


async def test_fork_writes_new_jsonl_with_parent_session_header(
    tmp_path: Path,
) -> None:
    """P-325 persisted-only branch: the new JSONL header carries
    ``parentSession=<source.path>``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    source_metadata = await source.get_metadata()
    entry_id = await source.append_message(
        UserMessage(content=[TextContent(text="x")])
    )

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    await runtime.fork(entry_id, position="at")
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.parent_session_path == source_metadata.path
