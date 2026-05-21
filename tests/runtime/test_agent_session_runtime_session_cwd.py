"""Sprint 6h₅a · Phase 4.14 — :func:`assert_session_cwd_exists` wired into
``switch_session`` (P-337).

Pi parity: ``packages/agent/src/harness/session/session-cwd.ts:1-59``
called from ``switchSession`` at ``:186``.

Aelix runs the assertion AFTER ``repo.open`` so the check exercises the
NEW session's cwd, not the OLD one. ``fallback_cwd=self.cwd`` is passed
so the :class:`MissingSessionCwdError` carries actionable diagnostic
context.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_agent_core.session.session_cwd import MissingSessionCwdError
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


def _make_runtime(
    harness: AgentHarness,
    repo: JsonlSessionRepo,
    fs: Any,
    factory: Any | None = None,
) -> AgentSessionRuntime:
    async def _default(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return AgentSessionRuntime(harness, factory or _default, repo=repo, fs=fs)


async def test_switch_session_raises_missing_session_cwd_when_target_cwd_missing(
    tmp_path: Path,
) -> None:
    """When ``fs.exists(new_session.cwd)`` is False, the assertion fires."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    # Source session lives in tmp_path; target session was created with a
    # cwd that the fs.exists wrapper says does not exist.
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    # Monkey-patch fs.exists to claim the cwd is missing.
    original_exists = fs.exists

    async def fake_exists(path: str) -> bool:
        if path == target_meta.cwd:
            return False
        return await original_exists(path)

    fs.exists = fake_exists  # type: ignore[method-assign]

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)

    with pytest.raises(MissingSessionCwdError) as exc_info:
        await runtime.switch_session(target_meta.path)
    assert exc_info.value.issue.session_cwd == target_meta.cwd
    assert exc_info.value.issue.session_file == target_meta.path
    # ``fallback_cwd`` is the OLD session's cwd (Pi parity).
    assert exc_info.value.issue.fallback_cwd == str(tmp_path)


async def test_switch_session_succeeds_when_target_cwd_exists(
    tmp_path: Path,
) -> None:
    """Happy path: the real cwd exists so the assertion is a no-op."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)
    result = await runtime.switch_session(target_meta.path)
    assert result.cancelled is False


async def test_assert_runs_after_repo_open_so_check_is_for_new_session(
    tmp_path: Path,
) -> None:
    """Ordering invariant: ``repo.open()`` must execute BEFORE the
    assertion, so the assertion exercises the NEW session's cwd (Pi
    parity ``:186``).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    open_called: list[str] = []
    exists_called: list[str] = []

    original_open = repo.open

    async def open_spy(metadata: Any) -> Session:
        open_called.append("open")
        # Confirm exists has NOT been called yet for the target cwd.
        assert "exists" not in exists_called
        return await original_open(metadata)

    repo.open = open_spy  # type: ignore[method-assign]

    original_exists = fs.exists

    async def exists_spy(path: str) -> bool:
        if path == target_meta.cwd:
            exists_called.append("exists")
        return await original_exists(path)

    fs.exists = exists_spy  # type: ignore[method-assign]

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)

    # The cancel hook would short-circuit BEFORE both — leave it absent.
    invalidate = AsyncMock()
    runtime.set_rebind_session(invalidate)

    await runtime.switch_session(target_meta.path)
    assert open_called == ["open"]
    assert exists_called == ["exists"]
