"""§E.1 — MemorySessionStorage Protocol contract tests (Sprint 4a)."""

from __future__ import annotations

import pytest
from aelix_agent_core.session import (
    MemorySessionStorage,
    MessageEntry,
    SessionError,
    SessionStorage,
    ThinkingLevelChangeEntry,
)
from aelix_ai.messages import TextContent, UserMessage


def _make_message_entry(
    storage_id: str, parent: str | None, text: str
) -> MessageEntry:
    return MessageEntry(
        id=storage_id,
        parent_id=parent,
        timestamp="2026-05-17T00:00:00.000Z",
        message=UserMessage(content=[TextContent(text=text)]),
    )


async def test_memory_storage_implements_protocol() -> None:
    storage = MemorySessionStorage()
    assert isinstance(storage, SessionStorage)


async def test_memory_storage_append_then_get() -> None:
    storage = MemorySessionStorage()
    entry = _make_message_entry("aa", None, "hi")
    await storage.append_entry(entry)
    assert await storage.get_entry("aa") is entry
    assert await storage.get_entries() == [entry]


async def test_memory_storage_leaf_id_tracking() -> None:
    storage = MemorySessionStorage()
    assert await storage.get_leaf_id() is None
    entry = _make_message_entry("aa", None, "hi")
    await storage.append_entry(entry)
    assert await storage.get_leaf_id() == "aa"


async def test_memory_storage_create_entry_id_unique() -> None:
    storage = MemorySessionStorage()
    a = await storage.create_entry_id()
    # Inject the id to force the next id to be different.
    await storage.append_entry(_make_message_entry(a, None, "x"))
    b = await storage.create_entry_id()
    assert a != b


async def test_memory_storage_path_to_root() -> None:
    storage = MemorySessionStorage()
    root = _make_message_entry("aa", None, "root")
    mid = _make_message_entry("bb", "aa", "mid")
    leaf = _make_message_entry("cc", "bb", "leaf")
    for e in (root, mid, leaf):
        await storage.append_entry(e)
    path = await storage.get_path_to_root("cc")
    assert [e.id for e in path] == ["aa", "bb", "cc"]


async def test_memory_storage_set_leaf_id_unknown_raises_not_found() -> None:
    storage = MemorySessionStorage()
    with pytest.raises(SessionError) as exc:
        await storage.set_leaf_id("nope")
    assert exc.value.code == "not_found"


async def test_memory_storage_find_entries_by_type() -> None:
    storage = MemorySessionStorage()
    msg = _make_message_entry("aa", None, "hi")
    tlc = ThinkingLevelChangeEntry(
        id="bb",
        parent_id="aa",
        timestamp="2026-05-17T00:00:00.001Z",
        thinking_level="high",
    )
    await storage.append_entry(msg)
    await storage.append_entry(tlc)
    found = await storage.find_entries("thinking_level_change")
    assert found == [tlc]


async def test_memory_storage_label_cache_round_trip() -> None:
    """Pi parity: ``label`` entries populate the labels-by-id cache."""

    from aelix_agent_core.session import LabelEntry

    storage = MemorySessionStorage()
    target = _make_message_entry("aa", None, "hi")
    await storage.append_entry(target)
    label = LabelEntry(
        id="bb",
        parent_id="aa",
        timestamp="2026-05-17T00:00:00.001Z",
        target_id="aa",
        label="checkpoint",
    )
    await storage.append_entry(label)
    assert await storage.get_label("aa") == "checkpoint"
    # Empty label clears the cache.
    clear = LabelEntry(
        id="cc",
        parent_id="bb",
        timestamp="2026-05-17T00:00:00.002Z",
        target_id="aa",
        label=None,
    )
    await storage.append_entry(clear)
    assert await storage.get_label("aa") is None
