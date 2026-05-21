"""Sprint 6h₄c · §D.2 — real ``switch_session`` / ``new_session`` / ``fork``
unit suite over tmp-path :class:`JsonlSessionRepo` (ADR-0079, P-325).

Pi parity: ``packages/agent/src/core/agent-session-runtime.ts:175-320``
(``switchSession`` / ``newSession`` / ``fork`` bodies — the Pi line ranges
preserved in the runtime method docstrings).

Each test constructs a real :class:`JsonlSessionRepo` over
:class:`LocalFileSystem` rooted at ``tmp_path``, writes a JSONL source
session, and exercises the public replace API on the runtime. The
factory passed to :class:`AgentSessionRuntime` constructs a fresh
:class:`AgentHarness` bound to the supplied new :class:`Session` — Pi
parity P-302 (harness-rebuild instead of session-swap).

P-329 closure pin (replicated here for full coverage): every successful
replace invokes the registered ``rebind_session`` callback EXACTLY once
— Aelix handlers do NOT add a second rebind. See
:mod:`tests.pi_parity.test_phase_4_13_strict_superset` for the
authoritative invariant.
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
    JsonlSessionStorage,
    LocalFileSystem,
    Session,
    SessionError,
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


async def _create_repo_session(
    repo: JsonlSessionRepo, cwd: str
) -> Session:
    return await repo.create(JsonlSessionCreateOptions(cwd=cwd))


def _make_runtime(
    harness: AgentHarness,
    repo: JsonlSessionRepo,
    fs: LocalFileSystem,
    factory: Any | None = None,
) -> AgentSessionRuntime:
    async def _default_factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return AgentSessionRuntime(
        harness, factory or _default_factory, repo=repo, fs=fs
    )


# === §A — switch_session real body ===========================================


async def test_switch_session_opens_existing_jsonl_and_rebinds(
    tmp_path: Path,
) -> None:
    """Pi parity ``:175-198``: ``switch_session(path)`` opens the JSONL,
    swaps the harness via the factory, and the runtime's harness identity
    is the NEW harness bound to the NEW session.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    source_metadata = await source.get_metadata()

    target = await _create_repo_session(repo, str(tmp_path))
    target_metadata = await target.get_metadata()

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)

    result = await runtime.switch_session(target_metadata.path)
    assert result.cancelled is False
    assert result.selected_text is None
    assert runtime.harness is not old_h
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.id == target_metadata.id
    assert new_meta.id != source_metadata.id


async def test_switch_session_missing_path_raises_session_error(
    tmp_path: Path,
) -> None:
    """Pi parity: ``repo.open(missing)`` raises
    :class:`SessionError("storage")` from the header probe (the file
    doesn't exist; ``read_text_lines`` cannot read it). The RPC handler
    wraps the error into an :class:`RpcErrorResponse`.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    with pytest.raises(SessionError):
        await runtime.switch_session(str(tmp_path / "does-not-exist.jsonl"))


async def test_switch_session_invokes_rebind_callback_exactly_once(
    tmp_path: Path,
) -> None:
    """P-329 invariant: ``_finish_session_replacement`` auto-invokes the
    rebind callback EXACTLY ONCE per successful replace.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    target = await _create_repo_session(repo, str(tmp_path))
    target_metadata = await target.get_metadata()

    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    cb = AsyncMock()
    runtime.set_rebind_session(cb)

    await runtime.switch_session(target_metadata.path)
    cb.assert_awaited_once()


async def test_switch_session_disposes_old_harness_once(
    tmp_path: Path,
) -> None:
    """Pi parity ``:149-157``: ``_teardown_current`` disposes the OLD
    harness exactly once during the replace.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    target = await _create_repo_session(repo, str(tmp_path))
    target_metadata = await target.get_metadata()

    old_h = _new_harness(session=source)
    old_h.dispose = AsyncMock()  # type: ignore[method-assign]
    runtime = _make_runtime(old_h, repo, fs)

    await runtime.switch_session(target_metadata.path)
    old_h.dispose.assert_awaited_once()


# === §B — new_session real body ==============================================


async def test_new_session_without_parent_creates_fresh_session(
    tmp_path: Path,
) -> None:
    """Pi parity ``:200-232``: ``new_session(parent_session=None)``
    creates a brand-new JSONL under the current cwd.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    source_metadata = await source.get_metadata()

    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    result = await runtime.new_session()
    assert result.cancelled is False

    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.id != source_metadata.id
    assert new_meta.parent_session_path is None
    assert new_meta.cwd == str(tmp_path)


async def test_new_session_with_parent_writes_lineage_header(
    tmp_path: Path,
) -> None:
    """P-330 — ``parent_session`` lineage actually persists via
    ``repo.create(parent_session_path=...)``. The Sprint 6d ADR-0058
    carry-forward CLOSES.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    parent_path = "/some/parent.jsonl"
    result = await runtime.new_session(parent_session=parent_path)
    assert result.cancelled is False

    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.parent_session_path == parent_path


async def test_new_session_invokes_rebind_callback_exactly_once(
    tmp_path: Path,
) -> None:
    """P-329 invariant."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    cb = AsyncMock()
    runtime.set_rebind_session(cb)

    await runtime.new_session()
    cb.assert_awaited_once()


async def test_new_session_raises_runtime_error_when_cwd_is_none() -> None:
    """Defensive: ``new_session`` requires a current session with a cwd."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs)
    runtime = AgentSessionRuntime(
        _new_harness(session=None),  # no session → cwd is None
        _make_factory(),
        repo=repo,
        fs=fs,
    )
    with pytest.raises(RuntimeError, match=r"cwd"):
        await runtime.new_session()


def _make_factory() -> Any:
    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return _factory


# === §C — fork real body =====================================================


async def _seed_user_message(session: Session) -> str:
    """Append a user message to ``session`` and return its entry id."""

    return await session.append_message(
        UserMessage(content=[TextContent(text="hello world")])
    )


async def test_fork_at_position_returns_no_selected_text(
    tmp_path: Path,
) -> None:
    """``position="at"`` MUST NOT extract a selected_text — Pi parity."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    entry_id = await _seed_user_message(source)

    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    result = await runtime.fork(entry_id, position="at")
    assert result.cancelled is False
    assert result.selected_text is None


async def test_fork_before_user_message_extracts_selected_text(
    tmp_path: Path,
) -> None:
    """``position="before"`` over a user message extracts the
    user-message text into ``selected_text`` for the wire (Pi parity).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    entry_id = await _seed_user_message(source)

    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    result = await runtime.fork(entry_id, position="before")
    assert result.cancelled is False
    assert result.selected_text == "hello world"


async def test_fork_invalid_entry_id_raises_value_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:247`` — invalid entry id raises ValueError."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    with pytest.raises(ValueError, match=r"Invalid entry ID for forking"):
        await runtime.fork("nonexistent-entry-id")


async def test_fork_non_user_message_before_raises_value_error(
    tmp_path: Path,
) -> None:
    """Pi parity ``:254-255``: ``position="before"`` requires the entry
    to be a user message — assistant messages must raise ValueError.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    asst_id = await source.append_message(
        AssistantMessage(
            content=[TextContent(text="reply")], stop_reason="end_turn"
        )
    )

    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    with pytest.raises(ValueError, match=r"Invalid entry ID for forking"):
        await runtime.fork(asst_id, position="before")


async def test_fork_persists_parent_session_path_to_new_jsonl(
    tmp_path: Path,
) -> None:
    """P-325 persisted-only branch: the new JSONL has
    ``parentSession=<source.path>``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    source_metadata = await source.get_metadata()
    entry_id = await _seed_user_message(source)

    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    await runtime.fork(entry_id, position="at")
    new_meta = await runtime.session.get_metadata()  # type: ignore[union-attr]
    assert new_meta.parent_session_path == source_metadata.path


async def test_state_session_id_on_new_harness_reflects_new_session(
    tmp_path: Path,
) -> None:
    """P-306 BINDING: harness-rebuild preserves the ``_state.session_id``
    invariant. After replace, ``runtime.harness._state.session_id``
    matches the NEW session's metadata id. Replacement for the 6h₄b
    test removed in P-331 cleanup.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    target = await _create_repo_session(repo, str(tmp_path))
    target_metadata = await target.get_metadata()

    runtime = _make_runtime(_new_harness(session=source), repo, fs)
    await runtime.switch_session(target_metadata.path)
    assert runtime.harness._state.session_id == target_metadata.id


async def test_two_consecutive_switch_session_dispose_each_old_harness(
    tmp_path: Path,
) -> None:
    """Two consecutive replaces → 2 disposes + 2 rebind invocations.
    Replacement for the 6h₄b test removed in P-331 cleanup.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    t1 = await _create_repo_session(repo, str(tmp_path))
    t2 = await _create_repo_session(repo, str(tmp_path))
    t1_meta = await t1.get_metadata()
    t2_meta = await t2.get_metadata()

    h_old = _new_harness(session=source)
    h_old.dispose = AsyncMock()  # type: ignore[method-assign]

    h_mid_holder: dict[str, AgentHarness] = {}

    async def _factory(new_sess: Session) -> AgentHarness:
        h = _new_harness(session=new_sess)
        if "mid" not in h_mid_holder:
            h.dispose = AsyncMock()  # type: ignore[method-assign]
            h_mid_holder["mid"] = h
        return h

    runtime = _make_runtime(h_old, repo, fs, factory=_factory)
    cb = AsyncMock()
    runtime.set_rebind_session(cb)

    await runtime.switch_session(t1_meta.path)
    await runtime.switch_session(t2_meta.path)

    h_old.dispose.assert_awaited_once()
    h_mid_holder["mid"].dispose.assert_awaited_once()  # type: ignore[union-attr]
    assert cb.await_count == 2


# === §D — JSONL storage round-trip sanity ====================================


async def test_jsonl_storage_round_trip_for_repo_smoke(
    tmp_path: Path,
) -> None:
    """Defensive: confirm the tmp-path session round-trips through
    :class:`JsonlSessionStorage.open` so failures in §A/§B/§C are not
    masked by storage layer breakage.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_repo_session(repo, str(tmp_path))
    source_metadata = await source.get_metadata()

    reopened_storage = await JsonlSessionStorage.open(fs, source_metadata.path)
    assert (await reopened_storage.get_metadata()).id == source_metadata.id
