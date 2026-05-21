"""Sprint 6h₄c · §D.4 — handler integration tests for ``switch_session``
/ ``fork`` / ``clone`` (ADR-0079, P-326/P-327/P-328/P-329).

Pi parity:
  - ``rpc-mode.ts:563-569`` (switch_session) → ``runtimeHost.switchSession``.
  - ``rpc-mode.ts:571-577`` (fork) → ``runtimeHost.fork`` + wire shape
    ``{cancelled, text?}`` (P-327 ``selectedText → text`` rename +
    key-omission per P-298 pattern).
  - ``rpc-mode.ts:579-589`` (clone) → captures ``leaf_id`` BEFORE
    ``runtimeHost.fork(leaf_id, position="at")`` (P-328 pre-capture
    ordering invariant).

P-329 deliberate convergence: Aelix handlers MUST NOT call rebind
manually. The runtime's ``_finish_session_replacement`` auto-invokes
the registered callback as single source of truth.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
from aelix_coding_agent.rpc.rpc_mode import (
    _bind_runtime_host,
    _handle_clone,
    _handle_fork,
    _handle_switch_session,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandClone,
    RpcCommandFork,
    RpcCommandSwitchSession,
    RpcErrorResponse,
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


async def _runtime_with_source(
    tmp_path: Path,
) -> tuple[AgentSessionRuntime, JsonlSessionRepo, LocalFileSystem, Session]:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    return runtime, repo, fs, source


# === §A — switch_session handler =============================================


async def test_handle_switch_session_returns_pi_wire_shape(
    tmp_path: Path,
) -> None:
    """Pi parity ``:568`` wire shape: ``{cancelled}``."""

    runtime, repo, fs, _ = await _runtime_with_source(tmp_path)
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_metadata = await target.get_metadata()

    cmd = RpcCommandSwitchSession(session_path=target_metadata.path, id="r1")
    response = await _handle_switch_session(runtime, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "switch_session"
    assert response.data == {"cancelled": False}


async def test_handle_switch_session_missing_path_returns_error(
    tmp_path: Path,
) -> None:
    """Pi parity: SessionError from ``repo.open`` surfaces as
    :class:`RpcErrorResponse` via the outer :func:`_handle_command`
    wrapper.

    W4 MINOR-2 (Sprint 6h₄c W6): with the handler's redundant blanket
    ``except Exception`` removed, the SessionError propagates to the
    outer :func:`_handle_command` arm which produces the same Pi-shape
    error envelope. The test asserts that path end-to-end via
    :func:`_handle_command`.
    """

    from aelix_coding_agent.rpc.rpc_mode import _handle_command

    runtime, _, _, _ = await _runtime_with_source(tmp_path)
    dispatch = build_dispatch_table(runtime_host=runtime)
    payload = {
        "type": "switch_session",
        "sessionPath": str(tmp_path / "missing.jsonl"),
        "id": "r2",
    }
    response = await _handle_command(runtime.harness, payload, dispatch)
    assert isinstance(response, RpcErrorResponse)
    assert response.command == "switch_session"


async def test_handle_switch_session_does_not_manually_rebind(
    tmp_path: Path,
) -> None:
    """P-329 invariant: handler does NOT call rebind — runtime's
    ``_finish_session_replacement`` auto-invokes the callback exactly
    once.
    """

    runtime, repo, _, _ = await _runtime_with_source(tmp_path)
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_metadata = await target.get_metadata()
    cb = AsyncMock()
    runtime.set_rebind_session(cb)

    await _handle_switch_session(
        runtime,
        RpcCommandSwitchSession(session_path=target_metadata.path, id="r"),
    )
    # P-329: EXACTLY one rebind invocation (NOT 2 per Pi belt-and-
    # braces).
    cb.assert_awaited_once()


# === §B — fork handler =======================================================


async def _seed_user_message(session: Session, text: str = "hello") -> str:
    return await session.append_message(
        UserMessage(content=[TextContent(text=text)])
    )


async def test_handle_fork_wire_shape_with_selected_text(
    tmp_path: Path,
) -> None:
    """P-327 wire shape: when ``position="before"`` over a user
    message, fork extracts the user-message text into ``text`` key.
    """

    runtime, _, _, source = await _runtime_with_source(tmp_path)
    entry_id = await _seed_user_message(source, "hello world")

    response = await _handle_fork(
        runtime, RpcCommandFork(entry_id=entry_id, id="r1")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "fork"
    assert response.data == {"cancelled": False, "text": "hello world"}


async def test_handle_fork_wire_shape_omits_text_when_none() -> None:
    """P-327 / P-298 key-omission: when ``selected_text is None`` the
    ``text`` key is omitted from the wire (Pi
    ``JSON.stringify({text: undefined})`` → ``{cancelled}``).

    W4 MINOR-3 (Sprint 6h₄c W6 rewrite): drive ``_handle_fork`` directly
    with a mocked runtime that returns
    ``RuntimeReplaceResult(cancelled=False, selected_text=None)`` so the
    handler-layer key-omission is asserted end-to-end (not just the
    inline envelope construction).
    """

    from aelix_agent_core.runtime._types import RuntimeReplaceResult

    runtime = MagicMock(spec=AgentSessionRuntime)
    runtime.fork = AsyncMock(
        return_value=RuntimeReplaceResult(
            cancelled=False, selected_text=None
        )
    )
    cmd = RpcCommandFork(id="test-1", entry_id="entry-abc")
    response = await _handle_fork(runtime, cmd)
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "fork"
    assert response.data == {"cancelled": False}
    assert "text" not in response.data


async def test_handle_fork_invalid_entry_id_returns_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:247``: invalid entry id → RpcErrorResponse."""

    runtime, _, _, _ = await _runtime_with_source(tmp_path)
    response = await _handle_fork(
        runtime, RpcCommandFork(entry_id="nonexistent", id="r")
    )
    assert isinstance(response, RpcErrorResponse)
    assert response.command == "fork"
    assert "Invalid entry ID for forking" in response.error


async def test_handle_fork_does_not_manually_rebind(
    tmp_path: Path,
) -> None:
    """P-329 invariant."""

    runtime, _, _, source = await _runtime_with_source(tmp_path)
    entry_id = await _seed_user_message(source, "ping")
    cb = AsyncMock()
    runtime.set_rebind_session(cb)

    await _handle_fork(
        runtime, RpcCommandFork(entry_id=entry_id, id="r")
    )
    cb.assert_awaited_once()


# === §C — clone handler ======================================================


async def test_handle_clone_wire_shape_drops_selected_text(
    tmp_path: Path,
) -> None:
    """P-328 wire: clone returns ``{cancelled}`` only (Pi DROPS
    ``selectedText`` for clone — Pi line 588).
    """

    runtime, _, _, source = await _runtime_with_source(tmp_path)
    # Seed a user message so the session has a leaf.
    await _seed_user_message(source, "leaf")

    response = await _handle_clone(runtime, RpcCommandClone(id="r1"))
    assert isinstance(response, RpcSuccessResponse)
    assert response.command == "clone"
    assert response.data == {"cancelled": False}
    assert "text" not in response.data


async def test_handle_clone_when_session_is_none_returns_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:582``: error envelope when no leaf is selected.
    Aelix triggers this branch when ``runtime.session is None``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=None), _factory, repo=repo, fs=fs
    )
    response = await _handle_clone(runtime, RpcCommandClone(id="r"))
    assert isinstance(response, RpcErrorResponse)
    assert response.error == (
        "Cannot clone session: no current entry selected"
    )


async def test_handle_clone_when_leaf_id_is_none_returns_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:582``: a session with no entries has leaf_id=None
    → error envelope.
    """

    runtime, _, _, _ = await _runtime_with_source(tmp_path)
    # Source session is freshly created with no entries → leaf_id None.
    response = await _handle_clone(runtime, RpcCommandClone(id="r"))
    assert isinstance(response, RpcErrorResponse)
    assert response.error == (
        "Cannot clone session: no current entry selected"
    )


async def test_handle_clone_pre_captures_leaf_id_before_dispose(
    tmp_path: Path,
) -> None:
    """P-328 ordering invariant: ``leaf_id`` resolves via
    ``session.get_leaf_id()`` BEFORE the OLD harness is disposed.

    Spy on ``session.get_leaf_id`` + ``harness.dispose`` and check
    the get_leaf_id call completes before dispose() is awaited.
    """

    runtime, _, _, source = await _runtime_with_source(tmp_path)
    await _seed_user_message(source, "leaf")
    order: list[str] = []

    original_get_leaf_id = source.get_leaf_id

    async def _get_leaf_id_spy() -> str | None:
        result = await original_get_leaf_id()
        order.append("get_leaf_id")
        return result

    source.get_leaf_id = _get_leaf_id_spy  # type: ignore[method-assign]

    old_harness = runtime.harness
    original_dispose = old_harness.dispose

    async def _dispose_spy() -> None:
        order.append("dispose")
        await original_dispose()

    old_harness.dispose = _dispose_spy  # type: ignore[method-assign]

    await _handle_clone(runtime, RpcCommandClone(id="r"))
    # get_leaf_id resolved BEFORE dispose() was invoked.
    assert order[:2] == ["get_leaf_id", "dispose"]


async def test_handle_clone_does_not_manually_rebind(
    tmp_path: Path,
) -> None:
    """P-329 invariant."""

    runtime, _, _, source = await _runtime_with_source(tmp_path)
    await _seed_user_message(source, "leaf")
    cb = AsyncMock()
    runtime.set_rebind_session(cb)

    await _handle_clone(runtime, RpcCommandClone(id="r"))
    cb.assert_awaited_once()


# === §D — arity / dispatch wiring (P-326) ====================================


async def test_dispatch_routes_three_through_bind_runtime_host(
    tmp_path: Path,
) -> None:
    """P-326: each of the 3 session-tree handlers in the dispatch
    table is the :func:`_bind_runtime_host` adapter.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))

    async def _factory(_s: Session) -> AgentHarness:
        return _new_harness()

    runtime = AgentSessionRuntime(
        _new_harness(), _factory, repo=repo, fs=fs
    )
    table = build_dispatch_table(runtime_host=runtime)
    for cmd in ("switch_session", "fork", "clone"):
        handler = table[cmd]
        name = getattr(handler, "__qualname__", repr(handler))
        assert "_bind_runtime_host" in name or "_adapted" in name


def test_bind_runtime_host_is_callable() -> None:
    """P-326 sanity: ``_bind_runtime_host`` is a public-internal helper."""

    assert callable(_bind_runtime_host)


@pytest.mark.parametrize(
    "cmd_type", ["switch_session", "fork", "clone"]
)
async def test_dispatch_with_no_runtime_returns_missing_runtime_stub(
    cmd_type: str,
) -> None:
    """When the dispatch table is built without ``runtime_host``, the
    runtime-host handlers route to a Pi-shape "missing runtime" stub
    (preserves the test-helper paths that don't construct a runtime).
    """

    table = build_dispatch_table()  # no runtime_host
    handler = table[cmd_type]

    class _Stub:
        id = f"req-{cmd_type}"

    response = await handler(None, _Stub())
    assert isinstance(response, RpcErrorResponse)
    assert response.command == cmd_type
    assert "AgentSessionRuntime" in response.error
