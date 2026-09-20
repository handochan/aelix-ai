"""§H.5 — JsonlSessionRepo.fork tests (Sprint 4b §E)."""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.session import (
    FORK_FROM_ROOT,
    ForkOptions,
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    SessionError,
    fork_at_leaf,
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


# === #300 — "fork at the root" is not "fork everything" =====================


async def test_fork_from_root_yields_an_empty_branch(tmp_path: Path) -> None:
    """``FORK_FROM_ROOT`` publishes a header with a parent link and nothing else."""

    repo, _, _ = await _seed_repo(tmp_path)
    source_meta = (await repo.list())[0]
    forked = await repo.fork(
        source_meta,
        ForkOptions(
            cwd="/forked", entry_id=FORK_FROM_ROOT, position="at"
        ),
    )
    assert await forked.get_entries() == []
    assert await forked.get_storage().get_leaf_id() is None
    forked_meta = await forked.get_metadata()
    assert forked_meta.parent_session_path == source_meta.path
    # The source is untouched.
    assert len(await (await repo.open(source_meta)).get_entries()) == 3


def test_fork_at_leaf_maps_an_empty_branch_to_the_root_sentinel() -> None:
    """The one call every "fork where I am" site makes.

    ``None`` is the leaf of an empty branch, and handing it to
    ``ForkOptions.entry_id`` directly asks for a whole-session copy (#300).
    """

    assert fork_at_leaf(None) is FORK_FROM_ROOT
    assert fork_at_leaf("") is FORK_FROM_ROOT
    assert fork_at_leaf("entry-1") == "entry-1"


async def test_fork_at_the_leaf_of_a_session_rewound_to_the_root_is_empty(
    tmp_path: Path,
) -> None:
    """The open-time shape: the branch is empty, so the fork of it is empty.

    ``Session.move_to(None)`` rewinds the live branch to the root while the
    entries stay in the file. A fork taken at that leaf — which is what
    forking at open time does — must inherit the empty branch, not the
    file's whole contents.
    """

    repo, _, _ = await _seed_repo(tmp_path)
    source_meta = (await repo.list())[0]
    source = await repo.open(source_meta)
    await source.move_to(None)
    assert await source.get_storage().get_leaf_id() is None

    forked = await repo.fork(
        source_meta,
        ForkOptions(
            cwd="/forked",
            entry_id=fork_at_leaf(await source.get_storage().get_leaf_id()),
            position="at",
        ),
    )
    assert await forked.get_entries() == []
    # The source still holds every entry it ever had, plus the leaf marker.
    assert len(await (await repo.open(source_meta)).get_entries()) == 4
