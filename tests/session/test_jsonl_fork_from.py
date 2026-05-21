"""Sprint 6h₅b · Phase 4.15 — :meth:`JsonlSessionRepo.fork_from` tests
(ADR-0083, P-361).

Pi parity: ``SessionManager.forkFrom`` (``session-manager.ts:1353-1394``).

Cross-cwd import variant: loads **ALL** source entries (no leaf
truncation — unlike :meth:`fork`), creates a NEW file under the target
cwd, rewrites the cwd header, and stamps
``parent_session_path=source.path``.

Sprint 6h₅b ships the surface only — the CLI ``/branch-from`` consumer
lands in Phase 5 per ADR-0083 §A "carry-forward".
"""

from __future__ import annotations

from pathlib import Path

from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    UserMessage,
)


async def test_fork_from_copies_all_entries_no_truncation(
    tmp_path: Path,
) -> None:
    """Pi parity ``:1353-1394``: ALL entries are copied — unlike
    :meth:`fork` which truncates to the leaf entry.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    # Append a chain of three messages so we can distinguish "all" from
    # "leaf only" semantics.
    await source.append_message(
        UserMessage(content=[TextContent(text="m1")])
    )
    await source.append_message(
        AssistantMessage(
            content=[TextContent(text="m2")], stop_reason="end_turn"
        )
    )
    await source.append_message(
        UserMessage(content=[TextContent(text="m3")])
    )

    src_meta = await source.get_metadata()

    target_cwd = str(tmp_path / "target")
    Path(target_cwd).mkdir(parents=True, exist_ok=True)

    forked = await repo.fork_from(src_meta, target_cwd)
    forked_entries = await forked.get_entries()
    # All 3 user/assistant messages copied (no leaf truncation).
    assert len(forked_entries) == 3


async def test_fork_from_target_cwd_matches(tmp_path: Path) -> None:
    """The new session's metadata reports the target cwd, not the source's."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source_cwd = str(tmp_path / "src")
    target_cwd = str(tmp_path / "target")
    Path(source_cwd).mkdir(parents=True, exist_ok=True)
    Path(target_cwd).mkdir(parents=True, exist_ok=True)

    source = await repo.create(JsonlSessionCreateOptions(cwd=source_cwd))
    src_meta = await source.get_metadata()

    forked = await repo.fork_from(src_meta, target_cwd)
    forked_meta = await forked.get_metadata()
    assert forked_meta.cwd == target_cwd
    assert forked_meta.cwd != src_meta.cwd


async def test_fork_from_sets_parent_session_path(tmp_path: Path) -> None:
    """``parent_session_path`` is set to the source file path so lineage
    round-trips (Pi parity ``:1353-1394``).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    src_meta = await source.get_metadata()
    target_cwd = str(tmp_path / "target")
    Path(target_cwd).mkdir(parents=True, exist_ok=True)

    forked = await repo.fork_from(src_meta, target_cwd)
    forked_meta = await forked.get_metadata()
    assert forked_meta.parent_session_path == src_meta.path


async def test_fork_from_new_session_is_distinct(tmp_path: Path) -> None:
    """The forked session has a NEW id and a NEW file path."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    src_meta = await source.get_metadata()
    target_cwd = str(tmp_path / "target")
    Path(target_cwd).mkdir(parents=True, exist_ok=True)

    forked = await repo.fork_from(src_meta, target_cwd)
    forked_meta = await forked.get_metadata()
    assert forked_meta.id != src_meta.id
    assert forked_meta.path != src_meta.path
    assert Path(src_meta.path).exists()  # source NOT moved.


async def test_fork_from_round_trips_through_repo_open(tmp_path: Path) -> None:
    """The forked file is a valid JSONL session — :meth:`open` round-trips it."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    await source.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    src_meta = await source.get_metadata()
    target_cwd = str(tmp_path / "target")
    Path(target_cwd).mkdir(parents=True, exist_ok=True)

    forked = await repo.fork_from(src_meta, target_cwd)
    forked_meta = await forked.get_metadata()

    reopened = await repo.open(forked_meta)
    reopened_meta = await reopened.get_metadata()
    assert reopened_meta.id == forked_meta.id
    assert reopened_meta.cwd == target_cwd
    assert reopened_meta.parent_session_path == src_meta.path


async def test_fork_from_optional_session_dir_override(tmp_path: Path) -> None:
    """Sprint 6h₅b W6 (P-368 W5 MINOR fix): the optional ``session_dir``
    keyword argument mirrors Pi's 3rd parameter
    (``forkFrom(source, targetCwd, sessionDir?)``). When supplied, the
    override replaces the default ``_get_session_dir(target_cwd)``
    resolution.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    src_meta = await source.get_metadata()
    target_cwd = str(tmp_path / "target")
    Path(target_cwd).mkdir(parents=True, exist_ok=True)

    custom_dir = str(tmp_path / "custom_session_dir")
    Path(custom_dir).mkdir(parents=True, exist_ok=True)

    forked = await repo.fork_from(src_meta, target_cwd, session_dir=custom_dir)
    forked_meta = await forked.get_metadata()
    # The new file path lives inside the caller-supplied directory, not
    # the default ``_get_session_dir(target_cwd)`` resolution.
    assert forked_meta.path.startswith(custom_dir)
    # cwd header still rewritten to target_cwd.
    assert forked_meta.cwd == target_cwd


async def test_fork_from_default_session_dir_unchanged(tmp_path: Path) -> None:
    """Without ``session_dir``, the default ``_get_session_dir(target_cwd)``
    resolution kicks in (regression — Sprint 6h₅b W6 P-368 fix must
    preserve the no-kw call shape).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    src_meta = await source.get_metadata()
    target_cwd = str(tmp_path / "target")
    Path(target_cwd).mkdir(parents=True, exist_ok=True)

    forked = await repo.fork_from(src_meta, target_cwd)
    forked_meta = await forked.get_metadata()
    # Default path resolution: still lives under the sessions root.
    assert str(tmp_path) in forked_meta.path
