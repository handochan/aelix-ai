"""One conformance suite for every ``SessionStorage`` backend (#294, ADR-0242).

The ``SessionStorage`` Protocol (``session/storage.py``) is only a list of
signatures; this module is the contract behind them. It is written against the
Protocol alone and runs once per backend through the parametrized ``backend``
fixture — Memory and JSONL today. **A new backend is added by registering its
factory in ``_BACKENDS``, and it must pass every case here unchanged.** pi keeps
the same shape: one runner-independent suite
(``packages/agent/src/harness/session/testing/conformance/storage.ts``) that its
JSONL, memory and SQLite backends all run. A backend is a fixture, not a fork of
the tests.

A backend under test is four things (:class:`Backend`): a fresh ``storage``;
``reopen()``, a new instance built only from what the backend holds (JSONL: the
file on disk; Memory: its entries and metadata handed to the constructor);
``prefix(k)``, a new instance holding only the first ``k`` entries (JSONL: the
durable file cut after the header plus ``k`` lines); and ``build(entries)``,
one constructed from a hand-made entry list, for the states the API refuses to
produce.

Deliberately left backend-specific, and tested where the backend lives:

- what happens to a payload that is not a JSON value. JSONL refuses what
  ``json.dumps`` cannot encode (a dataclass, a set, bytes) with ``invalid_entry``
  before writing a byte, and writes the rest the way ``json.dumps`` does, so a
  tuple comes back a list and a non-string key a string after a reload
  (``test_jsonl_write_discipline.py``). Memory does not serialize and keeps
  every payload as given;
- WHEN a dangling folded leaf is detected — Memory at construction, JSONL at
  ``get_leaf_id``. Both are allowed; the case here pins only "no later than
  ``get_leaf_id``";
- everything about bytes: one line per write, torn tails, CRLF files, atomic
  whole-file publish (``test_jsonl_write_discipline.py`` again).
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    JsonlSessionStorage,
    LabelEntry,
    LeafEntry,
    LocalFileSystem,
    MemorySessionStorage,
    MessageEntry,
    ModelChangeEntry,
    SessionError,
    SessionInfoEntry,
    SessionStorage,
    SessionTreeEntry,
    ThinkingLevelChangeEntry,
    build_display_messages,
    build_session_context,
    entry_to_json,
)
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

TS = "2026-09-19T00:00:00.000Z"

#: The ten entry ``type`` names on disk. The set is closed (ADR-0242).
ENTRY_TYPES = (
    "message",
    "thinking_level_change",
    "model_change",
    "compaction",
    "branch_summary",
    "custom",
    "custom_message",
    "label",
    "session_info",
    "leaf",
)

#: JSON values only (ADR-0242): every JSON type, nesting, and every character a
#: line-oriented file has to keep out of its framing.
RICH: dict[str, Any] = {
    "nested": {"list": [1, [2, [3, {"deep": True}]]], "empty_list": [], "empty_object": {}},
    "null": None,
    "bools": [True, False],
    "ints": [0, -1, 2**53 - 1],
    "floats": [0.5, -1.25, 1e-09, 3.141592653589793],
    "non_ascii": "naïve café — 日本語 ✓ 😀",
    "newline": "line one\nline two",
    "carriage_return": "one\rtwo",
    "crlf": "one\r\ntwo",
    "space": " ",
    "tab_and_nul": "a\tb\x00c",
    "line_separators": "  \x85",
    "quote_and_backslash": 'say "hi" \\ bye',
}

#: Base fields every entry shares; covered once for the whole catalogue.
_COMMON = frozenset({"id", "parent_id", "timestamp", "type"})


def user(entry_id: str, parent_id: str | None, text: str | None = None) -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        parent_id=parent_id,
        timestamp=TS,
        message=UserMessage(content=[TextContent(text=text or entry_id)]),
    )


def label(
    entry_id: str, parent_id: str | None, target_id: str, value: str | None
) -> LabelEntry:
    return LabelEntry(
        id=entry_id, parent_id=parent_id, timestamp=TS, target_id=target_id, label=value
    )


def fold_leaf(entries: list[SessionTreeEntry]) -> str | None:
    """The leaf a store must report after ``entries`` were appended in order."""

    leaf: str | None = None
    for entry in entries:
        leaf = entry.target_id if isinstance(entry, LeafEntry) else entry.id
    return leaf


def catalogue() -> list[SessionTreeEntry]:
    """Every ``SessionTreeEntry`` member, with every optional field set AND unset.

    Parent-chained in order, and every ``leaf``/``label`` target precedes it, so
    the list is a valid session and so is each of its prefixes. The message
    payloads use every content field ``test_message_roundtrip_content.py``
    proves the decoder total over; the free-form payloads are :data:`RICH`.
    ``test_the_catalogue_covers_every_entry_type_and_optional_field`` fails when
    the union gains a member or a member gains an optional field.
    """

    full_user = UserMessage(
        content=[
            TextContent(text="hello\nthere", text_signature='{"v":1,"id":"msg_1"}'),
            ImageContent(source="legacy-source", mime_type="image/png", data="iVBORw0KGgo="),
        ],
        timestamp=1726704000.25,
    )
    full_assistant = AssistantMessage(
        content=[
            TextContent(text="answer\r\nwith crlf", text_signature="text-sig"),
            ThinkingContent(thinking="step by step", thinking_signature="ErEE", redacted=True),
            ToolCallContent(
                tool_call_id="call_1",
                tool_name="read",
                input={"path": "a.txt", "options": RICH["nested"]},
                thought_signature="dGhvdWdodA==",
            ),
        ],
        stop_reason="toolUse",
        error_message="partial",
        usage={"input": 10, "output": 5, "cost": {"total": 0.0125}},
        timestamp=1726704001.5,
        api="openai-responses",
        provider="openai",
        model="gpt-test",
        response_id="resp_1",
    )
    full_tool_result = ToolResultMessage(
        tool_call_id="call_1",
        content=[TextContent(text="file body"), ImageContent(mime_type="image/png", data="AAAA")],
        is_error=True,
        timestamp=1726704002.0,
        tool_name="read",
    )
    return [
        MessageEntry(id="e01", parent_id=None, timestamp=TS, message=full_user),
        MessageEntry(id="e02", parent_id="e01", timestamp=TS, message=full_assistant),
        MessageEntry(id="e03", parent_id="e02", timestamp=TS, message=full_tool_result),
        MessageEntry(
            id="e04",
            parent_id="e03",
            timestamp=TS,
            message=UserMessage(content=[TextContent(text=RICH["non_ascii"])]),
        ),
        MessageEntry(
            id="e05",
            parent_id="e04",
            timestamp=TS,
            message=AssistantMessage(content=[TextContent(text="ok")]),
        ),
        MessageEntry(
            id="e06",
            parent_id="e05",
            timestamp=TS,
            message=ToolResultMessage(tool_call_id="call_2", content=[]),
        ),
        ThinkingLevelChangeEntry(id="e07", parent_id="e06", timestamp=TS, thinking_level="high"),
        ModelChangeEntry(
            id="e08", parent_id="e07", timestamp=TS, provider="openrouter", model_id="vendor/m:free"
        ),
        CompactionEntry(
            id="e09",
            parent_id="e08",
            timestamp=TS,
            summary="summary\nacross lines",
            first_kept_entry_id="e04",
            tokens_before=123456,
            details=RICH,
            from_hook=True,
        ),
        CompactionEntry(
            id="e10", parent_id="e09", timestamp=TS, summary="", first_kept_entry_id="e09",
            tokens_before=0,
        ),
        # Falsy but SET: a writer that tests truthiness instead of ``is None``
        # drops these, and they are the easiest values to lose.
        CompactionEntry(
            id="e11", parent_id="e10", timestamp=TS, summary="s", first_kept_entry_id="e10",
            tokens_before=1, details=[], from_hook=False,
        ),
        BranchSummaryEntry(
            id="e12", parent_id="e11", timestamp=TS, from_id="e05", summary="went elsewhere",
            details=RICH, from_hook=True,
        ),
        BranchSummaryEntry(id="e13", parent_id="e12", timestamp=TS, from_id="e12", summary=""),
        CustomEntry(id="e14", parent_id="e13", timestamp=TS, custom_type="aelix.catalogue", data=RICH),
        CustomEntry(id="e15", parent_id="e14", timestamp=TS, custom_type="ext-plain"),
        CustomEntry(id="e16", parent_id="e15", timestamp=TS, custom_type="aelix.scalar", data=0),
        CustomMessageEntry(
            id="e17", parent_id="e16", timestamp=TS, custom_type="aelix.note",
            content="plain string\nwith a newline", display=True, details=RICH,
        ),
        CustomMessageEntry(
            id="e18", parent_id="e17", timestamp=TS, custom_type="ext.list",
            content=[{"type": "text", "text": "list content"}], display=False,
        ),
        label("e19", "e18", "e01", "  padded label  "),
        label("e20", "e19", "e01", None),
        SessionInfoEntry(id="e21", parent_id="e20", timestamp=TS, name="my session ✓"),
        SessionInfoEntry(id="e22", parent_id="e21", timestamp=TS),
        LeafEntry(id="e23", parent_id="e22", timestamp=TS, target_id="e05"),
        LeafEntry(id="e24", parent_id="e05", timestamp=TS, target_id=None),
        # A second root: ``parent_id`` unset on an entry that is not first.
        user("e25", None, "a second root"),
    ]


# === Backends ===================================================================


@dataclass
class Backend:
    """One storage backend under test. See the module docstring."""

    name: str
    storage: SessionStorage[Any]
    reopen: Callable[[], Awaitable[SessionStorage[Any]]]
    prefix: Callable[[int], Awaitable[SessionStorage[Any]]]
    build: Callable[[list[SessionTreeEntry]], Awaitable[SessionStorage[Any]]]


async def _memory_backend(_root: Path) -> Backend:
    storage = MemorySessionStorage()

    async def reopen() -> SessionStorage[Any]:
        return MemorySessionStorage(
            entries=await storage.get_entries(), metadata=await storage.get_metadata()
        )

    async def prefix(k: int) -> SessionStorage[Any]:
        return MemorySessionStorage(
            entries=(await storage.get_entries())[:k], metadata=await storage.get_metadata()
        )

    async def build(entries: list[SessionTreeEntry]) -> SessionStorage[Any]:
        return MemorySessionStorage(entries=entries)

    return Backend("memory", storage, reopen, prefix, build)


async def _jsonl_backend(root: Path) -> Backend:
    fs = LocalFileSystem()
    path = root / "session.jsonl"
    storage = await JsonlSessionStorage.create(
        fs, str(path), cwd=str(root), session_id="conformance"
    )
    counter = itertools.count()

    async def reopen() -> SessionStorage[Any]:
        return await JsonlSessionStorage.open(fs, str(path))

    async def prefix(k: int) -> SessionStorage[Any]:
        raw = path.read_bytes()
        assert raw.endswith(b"\n"), "a session file this store wrote ends in a newline"
        lines = raw.split(b"\n")[:-1]
        assert len(lines) >= 1 + k, f"{len(lines) - 1} entry line(s) on disk, asked for {k}"
        cut = root / f"prefix-{k}.jsonl"
        cut.write_bytes(b"".join(line + b"\n" for line in lines[: 1 + k]))
        return await JsonlSessionStorage.open(fs, str(cut))

    async def build(entries: list[SessionTreeEntry]) -> SessionStorage[Any]:
        built = root / f"built-{next(counter)}.jsonl"
        header = {"type": "session", "version": 3, "id": built.stem, "timestamp": TS, "cwd": str(root)}
        rows = [header, *(entry_to_json(e) for e in entries)]
        built.write_bytes("".join(json.dumps(row) + "\n" for row in rows).encode("utf-8"))
        return await JsonlSessionStorage.open(fs, str(built))

    return Backend("jsonl", storage, reopen, prefix, build)


#: Every backend under test, keyed by the id its cases run under. A new backend
#: is one entry here (a factory that builds its :class:`Backend`), and nothing
#: else changes. The fixture's params ARE these keys, so a name cannot run
#: without its own factory behind it.
_BACKENDS: dict[str, Callable[[Path], Awaitable[Backend]]] = {
    "memory": _memory_backend,
    "jsonl": _jsonl_backend,
}


@pytest.fixture(params=list(_BACKENDS))
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Backend:
    """Every registered backend, one run of each case per entry in ``_BACKENDS``."""

    built = await _BACKENDS[request.param](tmp_path)
    assert built.name == request.param, f"the {request.param!r} factory built {built.name!r}"
    return built


async def _mixed_script(storage: SessionStorage[Any]) -> None:
    """Two branches, leaf moves (one to ``None``), labels set and cleared, a
    compaction, custom records and a session name — through the Protocol only."""

    await storage.append_entry(user("u1", None))
    await storage.append_entry(
        MessageEntry(
            id="a1", parent_id="u1", timestamp=TS,
            message=AssistantMessage(content=[TextContent(text="a1")], stop_reason="end_turn"),
        )
    )
    await storage.append_entry(user("u2", "a1"))
    await storage.append_entry(
        ThinkingLevelChangeEntry(id="t1", parent_id="u2", timestamp=TS, thinking_level="high")
    )
    await storage.append_entry(label("l1", "t1", "u1", "  checkpoint  "))
    await storage.append_entry(
        CustomEntry(id="c1", parent_id="l1", timestamp=TS, custom_type="aelix.test_record", data=RICH)
    )
    # Back to a1: the second branch.
    await storage.set_leaf_id("a1")
    await storage.append_entry(user("u3", "a1"))
    await storage.append_entry(
        CompactionEntry(
            id="k1", parent_id="u3", timestamp=TS, summary="compacted",
            first_kept_entry_id="a1", tokens_before=4321,
        )
    )
    await storage.append_entry(
        BranchSummaryEntry(id="b1", parent_id="k1", timestamp=TS, from_id="c1", summary="left c1")
    )
    await storage.append_entry(label("l2", "b1", "u1", None))
    await storage.append_entry(label("l3", "l2", "a1", "second"))
    await storage.append_entry(SessionInfoEntry(id="s1", parent_id="l3", timestamp=TS, name="named"))
    await storage.append_entry(
        CustomMessageEntry(
            id="cm1", parent_id="s1", timestamp=TS, custom_type="aelix.note",
            content="shown", display=True,
        )
    )
    await storage.set_leaf_id(None)
    await storage.set_leaf_id("cm1")
    await storage.append_entry(user("u4", "cm1"))


# === protocol & metadata ========================================================


async def test_the_backend_is_a_session_storage(backend: Backend) -> None:
    assert isinstance(backend.storage, SessionStorage)


async def test_metadata_is_non_empty_and_stable_across_calls_and_reopen(backend: Backend) -> None:
    first = await backend.storage.get_metadata()
    assert isinstance(first.id, str) and first.id
    assert isinstance(first.created_at, str) and first.created_at
    assert await backend.storage.get_metadata() == first

    await backend.storage.append_entry(user("u1", None))
    again = await (await backend.reopen()).get_metadata()
    assert (again.id, again.created_at) == (first.id, first.created_at)


# === ids ========================================================================


async def test_create_entry_id_never_repeats_a_stored_id(backend: Backend) -> None:
    storage = backend.storage
    stored: list[str] = []
    for _ in range(50):
        new_id = await storage.create_entry_id()
        assert isinstance(new_id, str) and new_id
        assert new_id not in stored
        await storage.append_entry(user(new_id, stored[-1] if stored else None))
        stored.append(new_id)
    assert [e.id for e in await storage.get_entries()] == stored


# === append / get ===============================================================


async def test_get_entry_returns_the_appended_entry(backend: Backend) -> None:
    entry = user("u1", None)
    await backend.storage.append_entry(entry)
    assert await backend.storage.get_entry("u1") == entry


async def test_get_entry_of_an_unknown_id_is_none(backend: Backend) -> None:
    await backend.storage.append_entry(user("u1", None))
    assert await backend.storage.get_entry("nope") is None


async def test_get_entries_is_insertion_order_and_a_copy(backend: Backend) -> None:
    storage = backend.storage
    # Insertion order, not tree order: the child of r1 comes after r2.
    entries = [user("r1", None), user("r2", None), user("c1", "r1")]
    for entry in entries:
        await storage.append_entry(entry)

    listed = await storage.get_entries()
    assert listed == entries
    listed.clear()
    listed.append(user("intruder", None))
    assert await storage.get_entries() == entries


# === leaf =======================================================================


async def test_an_empty_store_has_no_leaf(backend: Backend) -> None:
    assert await backend.storage.get_leaf_id() is None


async def test_an_append_moves_the_leaf_to_the_entry(backend: Backend) -> None:
    await backend.storage.append_entry(user("u1", None))
    assert await backend.storage.get_leaf_id() == "u1"
    await backend.storage.append_entry(user("u2", "u1"))
    assert await backend.storage.get_leaf_id() == "u2"


async def test_set_leaf_id_moves_the_leaf_and_appends_one_leaf_entry(backend: Backend) -> None:
    storage = backend.storage
    await storage.append_entry(user("a", None))
    await storage.append_entry(user("b", "a"))

    await storage.set_leaf_id("a")

    assert await storage.get_leaf_id() == "a"
    entries = await storage.get_entries()
    assert [e.id for e in entries[:2]] == ["a", "b"] and len(entries) == 3
    moved = entries[2]
    assert isinstance(moved, LeafEntry)
    assert (moved.parent_id, moved.target_id) == ("b", "a")
    assert await storage.find_entries("leaf") == [moved]


async def test_set_leaf_id_none_moves_before_the_first_entry(backend: Backend) -> None:
    storage = backend.storage
    await storage.append_entry(user("a", None))

    await storage.set_leaf_id(None)

    assert await storage.get_leaf_id() is None
    moved = (await storage.get_entries())[-1]
    assert isinstance(moved, LeafEntry)
    assert (moved.parent_id, moved.target_id) == ("a", None)


async def test_set_leaf_id_to_an_unknown_entry_is_not_found_and_appends_nothing(
    backend: Backend,
) -> None:
    storage = backend.storage
    await storage.append_entry(user("a", None))

    with pytest.raises(SessionError) as exc:
        await storage.set_leaf_id("ghost")

    assert exc.value.code == "not_found"
    assert [e.id for e in await storage.get_entries()] == ["a"]
    assert await storage.get_leaf_id() == "a"


async def test_appending_a_leaf_entry_moves_the_leaf_to_its_target(backend: Backend) -> None:
    storage = backend.storage
    await storage.append_entry(user("a", None))
    await storage.append_entry(user("b", "a"))

    await storage.append_entry(LeafEntry(id="l1", parent_id="b", timestamp=TS, target_id="a"))

    assert await storage.get_leaf_id() == "a"


async def test_a_dangling_folded_leaf_is_invalid_session_by_get_leaf_id(backend: Backend) -> None:
    dangling: list[SessionTreeEntry] = [
        user("a", None),
        LeafEntry(id="l1", parent_id="a", timestamp=TS, target_id="ghost"),
    ]
    with pytest.raises(SessionError) as exc:
        built = await backend.build(dangling)
        await built.get_leaf_id()
    assert exc.value.code == "invalid_session"


# === labels =====================================================================


async def test_a_label_is_stripped_and_the_latest_one_wins(backend: Backend) -> None:
    storage = backend.storage
    await storage.append_entry(user("a", None))
    await storage.append_entry(label("l1", "a", "a", "  first  "))
    assert await storage.get_label("a") == "first"
    await storage.append_entry(label("l2", "l1", "a", "second"))
    assert await storage.get_label("a") == "second"


@pytest.mark.parametrize("clearing", [None, "", "   "], ids=["none", "empty", "whitespace"])
async def test_an_empty_label_clears_it(backend: Backend, clearing: str | None) -> None:
    storage = backend.storage
    await storage.append_entry(user("a", None))
    await storage.append_entry(label("l1", "a", "a", "set"))
    await storage.append_entry(label("l2", "l1", "a", clearing))
    assert await storage.get_label("a") is None


async def test_an_unlabeled_entry_has_no_label(backend: Backend) -> None:
    await backend.storage.append_entry(user("a", None))
    assert await backend.storage.get_label("a") is None
    assert await backend.storage.get_label("never-stored") is None


# === find_entries ===============================================================


async def test_find_entries_filters_by_type_in_insertion_order(backend: Backend) -> None:
    storage = backend.storage
    await storage.append_entry(user("a", None))
    await storage.append_entry(CustomEntry(id="c1", parent_id="a", timestamp=TS, custom_type="x"))
    await storage.append_entry(user("b", "c1"))
    await storage.append_entry(CustomEntry(id="c2", parent_id="b", timestamp=TS, custom_type="x"))
    await storage.set_leaf_id("a")

    assert [e.id for e in await storage.find_entries("message")] == ["a", "b"]
    assert [e.id for e in await storage.find_entries("custom")] == ["c1", "c2"]
    leaves = await storage.find_entries("leaf")
    assert len(leaves) == 1 and isinstance(leaves[0], LeafEntry)
    assert leaves[0].target_id == "a"
    assert await storage.find_entries("no_such_type") == []


# === path to root ===============================================================


async def test_the_path_of_no_leaf_is_empty(backend: Backend) -> None:
    assert await backend.storage.get_path_to_root(None) == []


async def test_the_path_is_root_first_and_follows_the_branch(backend: Backend) -> None:
    storage = backend.storage
    for entry in (user("a", None), user("b", "a"), user("c", "b")):
        await storage.append_entry(entry)
    assert [e.id for e in await storage.get_path_to_root("c")] == ["a", "b", "c"]

    await storage.set_leaf_id("a")
    await storage.append_entry(user("d", await storage.get_leaf_id()))
    assert [e.id for e in await storage.get_path_to_root(await storage.get_leaf_id())] == ["a", "d"]


async def test_the_path_of_an_unknown_leaf_is_not_found(backend: Backend) -> None:
    await backend.storage.append_entry(user("a", None))
    with pytest.raises(SessionError) as exc:
        await backend.storage.get_path_to_root("ghost")
    assert exc.value.code == "not_found"


async def test_a_missing_parent_in_the_chain_is_invalid_session(backend: Backend) -> None:
    await backend.storage.append_entry(user("a", None))
    await backend.storage.append_entry(user("orphan", "ghost"))
    with pytest.raises(SessionError) as exc:
        await backend.storage.get_path_to_root("orphan")
    assert exc.value.code == "invalid_session"


# === round trip =================================================================


def test_the_catalogue_covers_every_entry_type_and_optional_field() -> None:
    """The round-trip catalogue grows with the union, or this fails first."""

    entries = catalogue()
    members = set(typing.get_args(SessionTreeEntry))
    assert {type(e) for e in entries} == members, (
        "catalogue() is missing a SessionTreeEntry member — add it, with every "
        "optional field both set and unset"
    )
    for cls in members:
        of_cls = [e for e in entries if type(e) is cls]
        for f in dataclasses.fields(cls):
            if f.name in _COMMON or "None" not in str(f.type):
                continue
            values = [getattr(e, f.name) for e in of_cls]
            assert any(v is None for v in values) and any(v is not None for v in values), (
                f"catalogue() never has {cls.__name__}.{f.name} both set and unset"
            )
    parents = [e.parent_id for e in entries]
    assert None in parents and any(p is not None for p in parents)
    assert len({e.id for e in entries}) == len(entries)


async def test_every_catalogue_entry_round_trips(backend: Backend) -> None:
    entries = catalogue()
    for entry in entries:
        await backend.storage.append_entry(entry)
    for entry in entries:
        assert await backend.storage.get_entry(entry.id) == entry, entry.id

    reopened = await backend.reopen()
    for entry in entries:
        assert await reopened.get_entry(entry.id) == entry, f"{entry.id} ({entry.type})"
    assert await reopened.get_entries() == entries


# === durability =================================================================


async def test_a_reopened_store_answers_every_query_the_same(backend: Backend) -> None:
    storage = backend.storage
    await _mixed_script(storage)

    reopened = await backend.reopen()

    entries = await storage.get_entries()
    assert await reopened.get_entries() == entries
    leaf = await storage.get_leaf_id()
    assert leaf == "u4"
    assert await reopened.get_leaf_id() == leaf
    assert await reopened.get_path_to_root(leaf) == await storage.get_path_to_root(leaf)
    for entry in entries:
        assert await reopened.get_label(entry.id) == await storage.get_label(entry.id), entry.id
    assert await reopened.get_label("a1") == "second"
    assert await reopened.get_label("u1") is None
    for entry_type in ENTRY_TYPES:
        assert await reopened.find_entries(entry_type) == await storage.find_entries(entry_type)


async def test_every_prefix_of_the_log_is_a_session(backend: Backend) -> None:
    """The file can end after any write, so each prefix must stand on its own.

    This is the rule a caller writing several entries for one operation has to
    keep (ADR-0242): there is no transaction around them.
    """

    await _mixed_script(backend.storage)
    entries = await backend.storage.get_entries()

    for k in range(len(entries) + 1):
        cut = await backend.prefix(k)
        assert await cut.get_entries() == entries[:k], k
        leaf = await cut.get_leaf_id()
        assert leaf == fold_leaf(entries[:k]), k
        path = await cut.get_path_to_root(leaf)
        assert (path[-1].id if path else None) == leaf
        if isinstance(cut, JsonlSessionStorage):
            assert cut.recovery is None, k


# === custom entries (the backend-agnostic half of the record rules) ==============


async def test_an_unknown_custom_record_is_carried_and_readers_skip_it(backend: Backend) -> None:
    """A record kind no reader knows is still a node in the tree, and invisible.

    This is why a new kind of record rides on ``CustomEntry`` (ADR-0242): every
    released Aelix decodes it, keeps its descendants, and builds neither the
    model's context nor the transcript from it.
    """

    storage = backend.storage
    before = user("u1", None, "before the record")
    record = CustomEntry(
        id="c1", parent_id="u1", timestamp=TS, custom_type="aelix.never_seen_before", data=RICH
    )
    after = user("u2", "c1", "after the record")
    for entry in (before, record, after):
        await storage.append_entry(entry)

    reopened = await backend.reopen()
    assert await reopened.get_entry("c1") == record
    path = await reopened.get_path_to_root(await reopened.get_leaf_id())
    assert [e.id for e in path] == ["u1", "c1", "u2"]
    assert build_session_context(path).messages == [before.message, after.message]
    assert build_display_messages(path) == [before.message, after.message]


# === many appends ===============================================================


async def test_concurrent_appends_all_land_in_call_order(backend: Backend) -> None:
    entries = [user(f"m{i:02d}", f"m{i - 1:02d}" if i else None) for i in range(30)]

    await asyncio.gather(*(backend.storage.append_entry(e) for e in entries))

    reopened = await backend.reopen()
    assert [e.id for e in await reopened.get_entries()] == [e.id for e in entries]
    assert await reopened.get_leaf_id() == "m29"
