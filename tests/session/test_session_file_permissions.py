"""Track S2 — session files are owner-only.

A session JSONL stores every prompt and every tool result verbatim, so an
agent that runs ``env`` writes an API key into it. These files used to be
created at the umask default (0644, or worse on a permissive umask) while
``auth.json`` next door was already 0600.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
)
from aelix_agent_core.session.entries import MessageEntry
from aelix_agent_core.session.fs import SESSION_DIR_MODE, SESSION_FILE_MODE
from aelix_ai.messages import TextContent, UserMessage


def _mode(path: str | Path) -> int:
    return stat.S_IMODE(Path(path).stat().st_mode)


async def test_write_file_creates_owner_only_file_and_dir(
    tmp_path: Path,
) -> None:
    fs = LocalFileSystem()
    target = tmp_path / "sessions" / "s.jsonl"

    await fs.write_file(str(target), "{}\n")

    assert _mode(target) == SESSION_FILE_MODE
    assert _mode(target.parent) == SESSION_DIR_MODE


async def test_append_file_creates_owner_only_file(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    target = tmp_path / "sessions" / "s.jsonl"

    await fs.append_file(str(target), "{}\n")

    assert _mode(target) == SESSION_FILE_MODE
    assert _mode(target.parent) == SESSION_DIR_MODE


async def test_create_dir_is_owner_only(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    target = tmp_path / "sessions"

    await fs.create_dir(str(target))

    assert _mode(target) == SESSION_DIR_MODE


async def test_preexisting_0644_file_is_tightened_on_append(
    tmp_path: Path,
) -> None:
    """Sessions written before this change migrate on their next write."""

    fs = LocalFileSystem()
    target = tmp_path / "old.jsonl"
    target.write_text("first\n", encoding="utf-8")
    os.chmod(target, 0o644)
    assert _mode(target) == 0o644

    await fs.append_file(str(target), "second\n")

    assert _mode(target) == SESSION_FILE_MODE
    # Tightening must not cost data.
    assert target.read_text(encoding="utf-8") == "first\nsecond\n"


async def test_preexisting_loose_dir_is_tightened(tmp_path: Path) -> None:
    fs = LocalFileSystem()
    session_dir = tmp_path / "loose"
    session_dir.mkdir(mode=0o755)
    os.chmod(session_dir, 0o755)

    await fs.append_file(str(session_dir / "s.jsonl"), "{}\n")

    assert _mode(session_dir) == SESSION_DIR_MODE


async def test_copy_file_does_not_inherit_loose_source_mode(
    tmp_path: Path,
) -> None:
    """``import_from_jsonl`` copies a caller-supplied file into the store.

    ``shutil.copy2`` carries the source's mode across, so a world-readable
    import would stay world-readable inside ``~/.aelix/sessions``.
    """

    fs = LocalFileSystem()
    source = tmp_path / "incoming.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    os.chmod(source, 0o644)
    destination = tmp_path / "sessions" / "imported.jsonl"

    await fs.copy_file(str(source), str(destination))

    assert _mode(destination) == SESSION_FILE_MODE


async def test_real_session_lands_owner_only_end_to_end(tmp_path: Path) -> None:
    """The property that actually matters, through the public repo API."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    metadata = await session.get_metadata()

    await session.get_storage().append_entry(
        MessageEntry(
            id="aaa1",
            parent_id=None,
            timestamp="2026-08-07T00:00:01.000Z",
            message=UserMessage(
                content=[TextContent(text="AWS_SECRET=hunter2")]
            ),
        )
    )

    assert _mode(metadata.path) == SESSION_FILE_MODE
    assert _mode(Path(metadata.path).parent) == SESSION_DIR_MODE
