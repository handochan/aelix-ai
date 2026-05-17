"""§H.5 — JsonlSessionRepo.fork tests (Sprint 4b §E)."""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.session import (
    ForkOptions,
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    SessionError,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage


async def _seed_repo(tmp_path: Path) -> tuple[JsonlSessionRepo, str, str]:
    """Seed a session with user→assistant→user; returns (repo, user1_id,
    user2_id) for use in position tests."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd="/src"))
    user1 = await source.append_message(
        UserMessage(content=[TextContent(text="u1")])
    )
    await source.append_message(
        AssistantMessage(content=[TextContent(text="a1")])
    )
    user2 = await source.append_message(
        UserMessage(content=[TextContent(text="u2")])
    )
    return repo, user1, user2


async def test_fork_full_copy_when_entry_id_none(tmp_path: Path) -> None:
    repo, _, _ = await _seed_repo(tmp_path)
    sources = await repo.list()
    assert len(sources) == 1
    source_meta = sources[0]
    forked = await repo.fork(
        source_meta, ForkOptions(cwd="/forked")
    )
    forked_entries = await forked.get_entries()
    source = await repo.open(source_meta)
    source_entries = await source.get_entries()
    assert len(forked_entries) == len(source_entries)


async def test_fork_position_before_user_message_excludes_target(
    tmp_path: Path,
) -> None:
    repo, _user1, user2 = await _seed_repo(tmp_path)
    source_meta = (await repo.list())[0]
    forked = await repo.fork(
        source_meta,
        ForkOptions(cwd="/forked", entry_id=user2, position="before"),
    )
    forked_entries = await forked.get_entries()
    # The forked session ends RIGHT BEFORE user2, so user2 should not be in
    # the copied tail.
    assert all(e.id != user2 for e in forked_entries)


async def test_fork_position_at_any_entry_includes_target(
    tmp_path: Path,
) -> None:
    repo, _user1, _user2 = await _seed_repo(tmp_path)
    source_meta = (await repo.list())[0]
    # Pick the assistant message — non-user, "before" would raise.
    source = await repo.open(source_meta)
    asst_entry = next(
        e for e in (await source.get_entries()) if e.type == "message"
        and e.message.role == "assistant"  # type: ignore[union-attr]
    )
    forked = await repo.fork(
        source_meta,
        ForkOptions(cwd="/forked", entry_id=asst_entry.id, position="at"),
    )
    forked_entries = await forked.get_entries()
    assert any(e.id == asst_entry.id for e in forked_entries)


async def test_fork_invalid_entry_raises_invalid_fork_target(
    tmp_path: Path,
) -> None:
    repo, _, _ = await _seed_repo(tmp_path)
    source_meta = (await repo.list())[0]
    with pytest.raises(SessionError) as exc:
        await repo.fork(
            source_meta,
            ForkOptions(cwd="/forked", entry_id="nope", position="at"),
        )
    assert exc.value.code == "invalid_fork_target"
