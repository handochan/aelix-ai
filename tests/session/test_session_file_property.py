"""Sprint 6h₅a · Phase 4.14 — :attr:`Session.session_file` sync property
(P-336).

Pi parity: ``AgentSession.sessionFile`` sync getter
(``packages/agent/src/harness/session/session.ts``). Aelix reads cached
``_metadata.path`` from the underlying storage via the same pattern as
:attr:`AgentSessionRuntime.cwd`
(``runtime/agent_session_runtime.py:171-173``).
"""

from __future__ import annotations

from pathlib import Path

from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)


async def test_session_file_returns_jsonl_metadata_path(tmp_path: Path) -> None:
    """JSONL-backed sessions expose the on-disk path."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    path = session.session_file
    assert path is not None
    metadata = await session.get_metadata()
    assert path == metadata.path


async def test_session_file_is_none_for_memory_storage() -> None:
    """:class:`MemorySessionStorage` carries no ``_metadata.path`` → None."""

    session = Session(MemorySessionStorage())
    assert session.session_file is None


async def test_session_file_round_trips_after_reopen(tmp_path: Path) -> None:
    """Re-opening a session round-trips the same path."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    created = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    metadata = await created.get_metadata()

    reopened = await repo.open(metadata)
    assert reopened.session_file == metadata.path
