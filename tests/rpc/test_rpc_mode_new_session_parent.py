"""Sprint 6h₄c · §D.5 — regression for the ``_handle_new_session``
Sprint 6d stub removal (ADR-0079, P-330).

Pi parity: ``rpc-mode.ts:277-282`` (new_session handler) →
``runtimeHost.newSession(parent_session)`` (Pi
``agent-session-runtime.ts:200-232``).

The Sprint 6d body at ``rpc_mode.py:309-347`` rejected
``parent_session`` via :class:`RpcErrorResponse`. The new body routes
through :meth:`AgentSessionRuntime.new_session` which persists lineage
via ``repo.create(parent_session_path=...)`` — the ADR-0058
carry-forward CLOSES.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
from aelix_coding_agent.rpc.rpc_mode import (
    _SUPPORTED_HANDLERS_HARNESS_ONLY,
    _SUPPORTED_HANDLERS_RUNTIME_HOST,
    _handle_new_session,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandNewSession,
    RpcSuccessResponse,
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


async def _make_runtime(
    tmp_path: Path,
) -> tuple[AgentSessionRuntime, JsonlSessionRepo, LocalFileSystem]:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    return runtime, repo, fs


async def test_new_session_no_parent_returns_success(tmp_path: Path) -> None:
    """Control: ``parent_session=None`` returns ``{cancelled: false}``."""

    runtime, _, _ = await _make_runtime(tmp_path)
    response = await _handle_new_session(
        runtime, RpcCommandNewSession(parent_session=None, id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "new_session"
    assert response.data == {"cancelled": False}


async def test_new_session_with_parent_persists_lineage(tmp_path: Path) -> None:
    """P-330: ``parent_session`` lineage actually persists. ADR-0058
    carry-forward CLOSES — Sprint 6h₄c replaces the Sprint 6d stub
    that rejected this branch.
    """

    runtime, _, _ = await _make_runtime(tmp_path)
    parent_path = "/some/parent.jsonl"
    response = await _handle_new_session(
        runtime,
        RpcCommandNewSession(parent_session=parent_path, id="r"),
    )
    assert isinstance(response, RpcSuccessResponse)
    assert response.data == {"cancelled": False}

    # The NEW session's metadata header carries the parent path.
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.parent_session_path == parent_path


def test_handle_new_session_in_runtime_host_arity_class() -> None:
    """P-330 arity change: ``_handle_new_session`` MOVED from
    HARNESS_ONLY to RUNTIME_HOST.
    """

    assert "new_session" in _SUPPORTED_HANDLERS_RUNTIME_HOST
    assert "new_session" not in _SUPPORTED_HANDLERS_HARNESS_ONLY
    assert _SUPPORTED_HANDLERS_RUNTIME_HOST["new_session"] is _handle_new_session


def test_sprint_6d_rejection_branch_removed_from_source() -> None:
    """Grep guard: confirm the Sprint 6d ``parent_session lineage
    tracking deferred`` reject-string is GONE from the new handler
    body (the new handler routes through the runtime instead).
    """

    src = inspect.getsource(_handle_new_session)
    assert "parent_session lineage tracking deferred" not in src
    # Sanity: the new handler delegates to the runtime.
    assert "runtime_host.new_session" in src
