"""§E.3 — JsonlSessionStorage tests (Sprint 4a)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    SessionError,
)
from aelix_agent_core.session.entries import MessageEntry
from aelix_ai.messages import TextContent, UserMessage


async def _new_storage(tmp_path: Path) -> JsonlSessionStorage:
    fs = LocalFileSystem()
    return await JsonlSessionStorage.create(
        fs,
        str(tmp_path / "session.jsonl"),
        cwd="/repo",
        session_id="abc",
    )


async def test_create_writes_pi_v3_header(tmp_path: Path) -> None:
    storage = await _new_storage(tmp_path)
    file_path = (await storage.get_metadata()).path
    text = Path(file_path).read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    header = json.loads(first_line)
    assert header["type"] == "session"
    assert header["version"] == 3
    assert header["id"] == "abc"
    assert header["cwd"] == "/repo"
    assert "timestamp" in header


async def test_append_entry_persists_camelcase(tmp_path: Path) -> None:
    storage = await _new_storage(tmp_path)
    entry = MessageEntry(
        id="aa",
        parent_id=None,
        timestamp="2026-05-17T00:00:00.000Z",
        message=UserMessage(content=[TextContent(text="hi")]),
    )
    await storage.append_entry(entry)
    file_path = (await storage.get_metadata()).path
    lines = Path(file_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    persisted = json.loads(lines[1])
    # camelCase parentId (not snake_case parent_id) per Pi wire format.
    assert "parentId" in persisted
    assert "parent_id" not in persisted
    assert persisted["type"] == "message"
    assert persisted["id"] == "aa"


async def test_open_reads_back_existing_session(tmp_path: Path) -> None:
    storage_a = await _new_storage(tmp_path)
    entry = MessageEntry(
        id="aa",
        parent_id=None,
        timestamp="2026-05-17T00:00:00.000Z",
        message=UserMessage(content=[TextContent(text="hi")]),
    )
    await storage_a.append_entry(entry)
    file_path = (await storage_a.get_metadata()).path

    storage_b = await JsonlSessionStorage.open(LocalFileSystem(), file_path)
    entries = await storage_b.get_entries()
    assert len(entries) == 1
    assert entries[0].id == "aa"
    assert await storage_b.get_leaf_id() == "aa"


async def test_open_malformed_header_raises_invalid_session(tmp_path: Path) -> None:
    file_path = tmp_path / "broken.jsonl"
    file_path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.open(LocalFileSystem(), str(file_path))
    assert exc.value.code == "invalid_session"


async def test_open_wrong_version_raises_invalid_session(tmp_path: Path) -> None:
    file_path = tmp_path / "wrong-version.jsonl"
    file_path.write_text(
        json.dumps(
            {
                "type": "session",
                "version": 2,
                "id": "abc",
                "timestamp": "2026-05-17T00:00:00Z",
                "cwd": "/repo",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.open(LocalFileSystem(), str(file_path))
    assert exc.value.code == "invalid_session"
