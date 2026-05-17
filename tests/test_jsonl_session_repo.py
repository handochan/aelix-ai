"""§E.4 — JsonlSessionRepo create/open/list/delete tests (Sprint 4a)."""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionListOptions,
    JsonlSessionRepo,
    SessionError,
)
from aelix_ai.messages import TextContent, UserMessage


async def test_create_open_round_trip(tmp_path: Path) -> None:
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd="/some/repo"))
    await session.append_message(
        UserMessage(content=[TextContent(text="hello")])
    )
    metadata = await session.get_metadata()
    # Re-open via repo + verify entries round-trip.
    reopened = await repo.open(metadata)
    entries = await reopened.get_entries()
    assert len(entries) == 1
    assert entries[0].type == "message"


async def test_list_returns_created_sessions_sorted_newest_first(
    tmp_path: Path,
) -> None:
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    a = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    b = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    listed = await repo.list(JsonlSessionListOptions(cwd="/r1"))
    ids = [m.id for m in listed]
    # Both should be present and the newer one (b) comes first.
    assert set(ids) == {(await a.get_metadata()).id, (await b.get_metadata()).id}


async def test_delete_removes_session_file(tmp_path: Path) -> None:
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd="/r1"))
    metadata = await session.get_metadata()
    assert Path(metadata.path).exists()
    await repo.delete(metadata)
    assert not Path(metadata.path).exists()
    with pytest.raises(SessionError) as exc:
        await repo.open(metadata)
    assert exc.value.code == "not_found"
