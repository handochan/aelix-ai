"""Track S1 — a crash-truncated session file must not brick ``--continue``.

Before this, ``_load_jsonl_storage`` raised on the first unparseable entry
line while ``_is_valid_session_file`` checked only the HEADER, so
``find_most_recent`` re-selected the same dead file on every run and the
user had no way out but deleting it by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalFileSystem,
    SessionError,
)
from aelix_agent_core.session.entries import MessageEntry
from aelix_ai.messages import TextContent, UserMessage

_HEADER = {
    "type": "session",
    "version": 3,
    "id": "sess-1",
    "timestamp": "2026-08-07T00:00:00.000Z",
    "cwd": "/repo",
}


def _msg(entry_id: str, parent_id: str | None) -> dict:
    return {
        "type": "message",
        "id": entry_id,
        "parentId": parent_id,
        "timestamp": "2026-08-07T00:00:01.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": entry_id}],
        },
    }


def _write(path: Path, *lines: str) -> None:
    path.write_text("".join(lines), encoding="utf-8")


# === (a) truncated tail recovers ==========================================


async def test_truncated_last_line_loads_valid_prefix(tmp_path: Path) -> None:
    """A half-written final line is dropped; everything before it survives."""

    path = tmp_path / "trunc.jsonl"
    _write(
        path,
        json.dumps(_HEADER) + "\n",
        json.dumps(_msg("aaa1", None)) + "\n",
        json.dumps(_msg("bbb2", "aaa1")) + "\n",
        '{"type": "message", "id": "ccc3", "timesta',  # crash mid-append
    )

    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))

    assert [e.id for e in await storage.get_entries()] == ["aaa1", "bbb2"]
    assert await storage.get_leaf_id() == "bbb2"
    assert storage.recovery is not None
    assert storage.recovery.skipped_lines == (4,)


async def test_recovery_warning_names_file_and_count(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The user is told which file was damaged and how much was dropped."""

    path = tmp_path / "trunc.jsonl"
    _write(
        path,
        json.dumps(_HEADER) + "\n",
        json.dumps(_msg("aaa1", None)) + "\n",
        "{oops",
    )

    with caplog.at_level("WARNING"):
        await JsonlSessionStorage.open(LocalFileSystem(), str(path))

    assert "trunc.jsonl" in caplog.text
    assert "skipped 1 unparseable line(s)" in caplog.text


async def test_valid_lines_without_trailing_newline_survive_append(
    tmp_path: Path,
) -> None:
    """A VALID file that merely lacks its final newline must not fuse.

    Every line here parses, so nothing is skipped and ``recovery`` is
    None — the file is not damaged, it is just unterminated (a pi export,
    a hand-edit, or ``import_from_jsonl``, which copies foreign bytes
    verbatim). Appending without healing the tail first fused the new
    entry onto ``bbb2``; the next load then dropped the fused line,
    destroying BOTH the just-typed entry and a turn that was already
    durable on disk. That is worse than the brick this module exists to
    fix, so the healing newline keys on the trailing byte alone.
    """

    path = tmp_path / "unterminated.jsonl"
    _write(
        path,
        json.dumps(_HEADER) + "\n",
        json.dumps(_msg("aaa1", None)) + "\n",
        json.dumps(_msg("bbb2", "aaa1")),  # valid JSON, no trailing "\n"
    )

    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    # Not damaged: nothing was skipped.
    assert storage.recovery is None
    assert [e.id for e in await storage.get_entries()] == ["aaa1", "bbb2"]

    await storage.append_entry(
        MessageEntry(
            id="new1",
            parent_id="bbb2",
            timestamp="2026-08-07T00:00:09.000Z",
            message=UserMessage(content=[TextContent(text="just typed")]),
        )
    )

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    # bbb2 was already durable; new1 was just written. Neither may vanish.
    assert [e.id for e in await reopened.get_entries()] == [
        "aaa1",
        "bbb2",
        "new1",
    ]
    assert reopened.recovery is None


async def test_reopen_is_stable_not_a_brick(tmp_path: Path) -> None:
    """The defining symptom: opening the SAME damaged file twice both work."""

    path = tmp_path / "trunc.jsonl"
    _write(
        path,
        json.dumps(_HEADER) + "\n",
        json.dumps(_msg("aaa1", None)) + "\n",
        '{"type": "mess',
    )

    first = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    second = await JsonlSessionStorage.open(LocalFileSystem(), str(path))

    assert len(await first.get_entries()) == 1
    assert len(await second.get_entries()) == 1


async def test_recovered_session_is_appendable(tmp_path: Path) -> None:
    """Recovery is only useful if the session can then be CONTINUED.

    The truncated line has no trailing newline, so an append that did not
    heal it first would fuse the new entry onto the fragment — making one
    unparseable line that the NEXT load skips, silently losing everything
    the user typed after resuming.
    """

    path = tmp_path / "trunc.jsonl"
    _write(
        path,
        json.dumps(_HEADER) + "\n",
        json.dumps(_msg("aaa1", None)) + "\n",
        "{trunc",
    )
    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert storage.recovery is not None
    assert storage.recovery.unterminated_tail is True

    await storage.append_entry(
        MessageEntry(
            id="new1",
            parent_id=await storage.get_leaf_id(),
            timestamp="2026-08-07T00:00:09.000Z",
            message=UserMessage(content=[TextContent(text="after recovery")]),
        )
    )
    assert await storage.get_leaf_id() == "new1"

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert [e.id for e in await reopened.get_entries()] == ["aaa1", "new1"]


# === (b) a bad HEADER is still a hard reject ==============================


async def test_corrupt_header_still_rejected(tmp_path: Path) -> None:
    """A bad header means "not a session file" — recovery must not mask that."""

    path = tmp_path / "badhdr.jsonl"
    _write(path, "not json at all\n", json.dumps(_msg("aaa1", None)) + "\n")

    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert exc.value.code == "invalid_session"


async def test_wholesale_garbage_still_rejected(tmp_path: Path) -> None:
    path = tmp_path / "garbage.jsonl"
    _write(path, "aaaa\n", "bbbb\n", "cccc\n")

    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert exc.value.code == "invalid_session"


# === interior damage must not leave a broken parent chain =================


async def test_interior_gap_prunes_orphans(tmp_path: Path) -> None:
    """Entries orphaned by a skipped line are dropped transitively.

    ``get_path_to_root`` raises when a ``parentId`` does not resolve, so
    keeping orphans would just move the crash later.
    """

    path = tmp_path / "interior.jsonl"
    _write(
        path,
        json.dumps(_HEADER) + "\n",
        json.dumps(_msg("aaa1", None)) + "\n",
        "{{{CORRUPTED\n",  # would have been bbb2
        json.dumps(_msg("ccc3", "bbb2")) + "\n",  # parent was skipped
        json.dumps(_msg("ddd4", "ccc3")) + "\n",  # orphaned transitively
    )

    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))

    assert [e.id for e in await storage.get_entries()] == ["aaa1"]
    assert storage.recovery is not None
    assert storage.recovery.orphaned_entries == ("ccc3", "ddd4")
    # The surviving tree is walkable — this is what would otherwise blow up.
    leaf = await storage.get_leaf_id()
    assert [e.id for e in await storage.get_path_to_root(leaf)] == ["aaa1"]


async def test_clean_file_reports_no_recovery(tmp_path: Path) -> None:
    """Valid files keep their exact previous behaviour."""

    path = tmp_path / "clean.jsonl"
    _write(
        path,
        json.dumps(_HEADER) + "\n",
        json.dumps(_msg("aaa1", None)) + "\n",
        json.dumps(_msg("bbb2", "aaa1")) + "\n",
    )

    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))

    assert storage.recovery is None
    assert [e.id for e in await storage.get_entries()] == ["aaa1", "bbb2"]
    assert await storage.get_leaf_id() == "bbb2"


# === (c) find_most_recent / --continue no longer dead-ends ================


async def test_find_most_recent_then_open_recovers(tmp_path: Path) -> None:
    """End-to-end ``--continue`` path: select the damaged file AND open it.

    ``find_most_recent`` always could select it (it validates the header
    only); what failed was the ``repo.open`` that follows.
    """

    fs = LocalFileSystem()
    root = tmp_path / "sessions"
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(root))

    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    metadata = await session.get_metadata()
    path = Path(metadata.path)

    # Simulate the crash: a half-written entry line on the end.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "message", "id": "zzz9", "times')

    most_recent = await repo.find_most_recent(str(tmp_path))
    assert most_recent is not None
    assert most_recent.path == metadata.path

    reopened = await repo.open(most_recent)
    assert (await reopened.get_metadata()).id == metadata.id
