"""JSONL write discipline, and the released behaviour its rules exist for (#294, ADR-0242).

``test_storage_conformance.py`` pins what every backend must do. This module pins
what only the JSONL backend can get wrong, because only it has bytes:

- one storage call is one line in one ``FileSystem.append_file`` call;
- a failed or short write never costs the entry appended after it;
- a torn tail is discarded whole, wherever the cut falls;
- files written before this change — CRLF, from Windows — still load and append;
- a whole file (create, fork, import) appears complete or not at all.

The last two groups are not about new code. They pin how RELEASED Aelix (beta.1,
beta.2) reads a record it does not know. That behaviour can no longer be changed,
and it is the whole reason for the record-evolution rules: a new entry ``type``
costs every entry below it, a fork drops every unknown key outside
``CustomEntry.data``, so a new kind of record rides on ``CustomEntry``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import json
import math
import os
import re
import sys
import types
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import (
    CustomEntry,
    CustomMessageEntry,
    ForkOptions,
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalFileSystem,
    MessageEntry,
    SessionError,
    SessionTreeEntry,
    entry_from_json,
    entry_to_json,
    load_jsonl_session_metadata,
)
from aelix_agent_core.session import fs as fs_mod
from aelix_agent_core.session.fs import SESSION_FILE_MODE

from tests.posix_modes import POSIX_MODES, assert_mode
from tests.session.test_storage_conformance import (
    ENTRY_TYPES,
    TS,
    catalogue,
    fold_leaf,
    user,
)

_ADR = "ADR-0242"

HEADER: dict[str, Any] = {
    "type": "session",
    "version": 3,
    "id": "hand-written",
    "timestamp": TS,
    "cwd": "/repo",
}


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def _write_session(path: Path, *rows: dict[str, Any]) -> None:
    """A session file written by hand, byte for byte (no newline translation)."""

    path.write_bytes(b"".join(_line(row) for row in rows))


def _one_json_line(content: str) -> dict[str, Any]:
    """``content`` is exactly one ``\\n``-terminated JSON object, nothing raw inside."""

    assert content.endswith("\n"), repr(content)
    body = content[:-1]
    assert "\n" not in body and "\r" not in body, repr(content)
    parsed = json.loads(body)
    assert isinstance(parsed, dict), repr(content)
    return parsed


def _leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


#: ``copy_file`` stages in ``<destination>.<12 hex>.tmp``, unique per call.
_UNIQUE_TEMP = re.compile(r"\.[0-9a-f]{12}\.tmp$")


def _temp_shape(name: str) -> str:
    """``name`` with a unique temp suffix replaced by ``.<hex>.tmp``."""

    return _UNIQUE_TEMP.sub(".<hex>.tmp", name)


async def _new_storage(
    path: Path, fs: LocalFileSystem | None = None
) -> JsonlSessionStorage:
    return await JsonlSessionStorage.create(
        fs or LocalFileSystem(), str(path), cwd=str(path.parent), session_id="discipline"
    )


async def _reopen(path: Path) -> JsonlSessionStorage:
    return await JsonlSessionStorage.open(LocalFileSystem(), str(path))


class _OsProxy(types.ModuleType):
    """The ``os`` that ``session/fs.py`` sees, with chosen functions replaced.

    Patched onto the fs module only — never the global ``os.write``, which
    pytest's own output capture writes through.
    """

    def __init__(self, **overrides: Callable[..., Any]) -> None:
        super().__init__("os")
        self._overrides = overrides

    def __getattr__(self, name: str) -> Any:
        overrides = self.__dict__.get("_overrides", {})
        if name in overrides:
            return overrides[name]
        return getattr(os, name)


# === one call, one write, one line ===============================================


class _SpyFs(LocalFileSystem):
    def __init__(self) -> None:
        super().__init__()
        self.appends: list[str] = []

    async def append_file(self, path: str, content: str) -> None:
        self.appends.append(content)
        await super().append_file(path, content)


async def test_every_write_is_one_append_file_call_holding_one_line(tmp_path: Path) -> None:
    fs = _SpyFs()
    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path, fs)
    entries = catalogue()

    for entry in entries:
        calls = len(fs.appends)
        await storage.append_entry(entry)
        assert len(fs.appends) == calls + 1, f"{entry.type}: one append_entry, one append_file"
        # Exactly one object, and it is this entry — even where ``data``
        # carries raw "\n" and "\r" (the catalogue's RICH payload does).
        assert _one_json_line(fs.appends[-1]) == json.loads(json.dumps(entry_to_json(entry)))

    calls = len(fs.appends)
    await storage.set_leaf_id("e01")
    assert len(fs.appends) == calls + 1
    wire = _one_json_line(fs.appends[-1])
    assert (wire["type"], wire["targetId"]) == ("leaf", "e01")

    raw = path.read_bytes()
    assert b"\r" not in raw, "binary descriptors: the bytes are LF-only on every platform"
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1 + len(entries) + 1


async def test_the_healing_newline_rides_in_the_same_call(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    first = user("a", None)
    path.write_bytes(_line(HEADER) + json.dumps(entry_to_json(first)).encode())  # no "\n"
    fs = _SpyFs()
    storage = await JsonlSessionStorage.open(fs, str(path))

    await storage.append_entry(user("b", "a"))

    assert len(fs.appends) == 1
    content = fs.appends[0]
    assert content.startswith("\n")
    assert _one_json_line(content[1:])["id"] == "b"
    assert [e.id for e in await (await _reopen(path)).get_entries()] == ["a", "b"]


async def test_every_descriptor_that_writes_session_bytes_is_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``O_BINARY`` is ``0`` off Windows, so an ``os.open`` that dropped it would
    pass every run here and fail only on windows-latest, where the ``\\r`` checks
    above can see it. A sentinel bit in its place makes this run everywhere
    (``fs.py`` reads ``_O_BINARY`` at call time)."""

    sentinel = 1 << 30
    assert not sentinel & (os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL)
    platform_binary = getattr(os, "O_BINARY", 0)
    opened: list[tuple[str, bool]] = []

    def spy_open(path: str, flags: int, *args: Any) -> int:
        carried = bool(flags & sentinel)
        opened.append((Path(path).name, carried))
        # The real call gets the platform's own flag back, as production passes it.
        return os.open(path, (flags & ~sentinel) | (platform_binary if carried else 0), *args)

    monkeypatch.setattr(fs_mod, "_O_BINARY", sentinel)
    monkeypatch.setattr(fs_mod, "os", _OsProxy(open=spy_open))
    fs = LocalFileSystem()
    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER)

    await fs.write_file(str(tmp_path / "staged.jsonl.tmp"), "{}\n")
    await fs.append_file(str(tmp_path / "appended.jsonl"), "{}\n")
    await fs.copy_file(str(source), str(tmp_path / "store" / "imported.jsonl"))

    assert [(_temp_shape(name), carried) for name, carried in opened] == [
        ("staged.jsonl.tmp", True),
        ("appended.jsonl", True),
        # copy_file: the staged temp's creation, then its reopen for the fsync.
        ("imported.jsonl.<hex>.tmp", True),
        ("imported.jsonl.<hex>.tmp", True),
    ]


# === interleaving is serialized ===================================================


class _YieldingFs(LocalFileSystem):
    """Hands the loop back before, and in the MIDDLE of, every line it writes.

    The mid-line yield is what makes the per-instance lock observable: without
    it, two appends' halves interleave and neither line parses.
    """

    async def append_file(self, path: str, content: str) -> None:
        await asyncio.sleep(0)
        half = len(content) // 2
        await super().append_file(path, content[:half])
        await asyncio.sleep(0)
        await super().append_file(path, content[half:])


async def test_concurrent_appends_are_serialized_whole_lines_in_call_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path, _YieldingFs())
    entries = [user(f"m{i:02d}", f"m{i - 1:02d}" if i else None) for i in range(30)]

    await asyncio.gather(*(storage.append_entry(e) for e in entries))

    lines = path.read_bytes().split(b"\n")[1:-1]
    assert [json.loads(line)["id"] for line in lines] == [e.id for e in entries]
    reopened = await _reopen(path)
    assert reopened.recovery is None
    assert [e.id for e in await reopened.get_entries()] == [e.id for e in entries]


# === short writes ================================================================


async def test_a_short_write_is_continued_not_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.write`` may write less than it was given and say so only in its return
    value. Ignoring it truncated the line with no exception (#294)."""

    writes: list[int] = []

    def seven_bytes_at_most(fd: int, data: Any) -> int:
        writes.append(len(data))
        return os.write(fd, bytes(data[:7]))

    monkeypatch.setattr(fs_mod, "os", _OsProxy(write=seven_bytes_at_most))
    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path)
    entries = catalogue()
    for entry in entries:
        await storage.append_entry(entry)

    size = path.stat().st_size
    assert len(writes) >= size // 7, "the proxy did not see the writes"
    lines = path.read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) == 1 + len(entries) + 1
    for line in lines[:-1]:
        json.loads(line)
    reopened = await _reopen(path)
    assert reopened.recovery is None
    assert await reopened.get_entries() == entries


async def test_a_write_that_makes_no_progress_raises_instead_of_spinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def no_progress(_fd: int, data: Any) -> int:
        calls.append(len(data))
        # A regression has to FAIL here, not spin: there is no pytest-timeout
        # and no job ``timeout-minutes``, so a spinning loop would hold the CI
        # leg until GitHub's six-hour limit.
        if len(calls) > 100:
            raise AssertionError("_write_all kept retrying a write that makes no progress")
        return 0

    monkeypatch.setattr(fs_mod, "os", _OsProxy(write=no_progress))

    with pytest.raises(OSError) as exc:
        await LocalFileSystem().append_file(str(tmp_path / "s.jsonl"), "{}\n")

    assert exc.value.errno == errno.EIO


# === a failed append does not cost the next one ====================================


class _FailOnceFs(LocalFileSystem):
    """The next ``append_file`` writes ``keep(content)`` and then raises ``error``."""

    def __init__(self) -> None:
        super().__init__()
        self._plan: tuple[Callable[[str], str], BaseException] | None = None

    def arm(self, keep: Callable[[str], str], error: BaseException) -> None:
        self._plan = (keep, error)

    async def append_file(self, path: str, content: str) -> None:
        if self._plan is None:
            await super().append_file(path, content)
            return
        keep, error = self._plan
        self._plan = None
        written = keep(content)
        if written:
            await super().append_file(path, written)
        raise error


def _half(content: str) -> str:
    return content[: len(content) // 2]


def _enospc() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device (injected)")


async def _torn_turn(
    tmp_path: Path, keep: Callable[[str], str], error: BaseException
) -> tuple[Path, JsonlSessionStorage]:
    """turn-1 lands; turn-2's append fails as planned; turn-3 and turn-4 follow,
    each parented on whatever leaf the store reports, as ``Session`` does."""

    fs = _FailOnceFs()
    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path, fs)
    await storage.append_entry(user("turn-1", None))

    fs.arm(keep, error)
    # Only an OSError is translated; anything else propagates as itself.
    expected = SessionError if isinstance(error, OSError) else type(error)
    with pytest.raises(expected) as exc:
        await storage.append_entry(user("turn-2", await storage.get_leaf_id()))
    if isinstance(exc.value, SessionError):
        assert exc.value.code == "storage"
    # Nothing in memory saw turn-2.
    assert [e.id for e in await storage.get_entries()] == ["turn-1"]
    assert await storage.get_leaf_id() == "turn-1"

    await storage.append_entry(user("turn-3", await storage.get_leaf_id()))
    await storage.append_entry(user("turn-4", await storage.get_leaf_id()))
    return path, storage


async def _assert_turns_1_3_4(path: Path) -> JsonlSessionStorage:
    reopened = await _reopen(path)
    assert [e.id for e in await reopened.get_entries()] == ["turn-1", "turn-3", "turn-4"]
    leaf = await reopened.get_leaf_id()
    assert [e.id for e in await reopened.get_path_to_root(leaf)] == ["turn-1", "turn-3", "turn-4"]
    return reopened


async def test_a_failed_append_does_not_cost_the_next_one(tmp_path: Path) -> None:
    """The §0 [3] scenario of #294: half of turn-2 reached the disk, then ENOSPC.

    Before, the next append was glued onto the fragment, the fused line failed
    to parse, and turn-3 and turn-4 — parented below it — were pruned on load.
    """

    path, _ = await _torn_turn(tmp_path, _half, _enospc())

    reopened = await _assert_turns_1_3_4(path)
    assert reopened.recovery is not None
    assert reopened.recovery.skipped_lines == (3,), "the fragment's own line, nothing else"
    assert reopened.recovery.orphaned_entries == ()


async def test_a_failure_before_any_byte_lands_costs_nothing(tmp_path: Path) -> None:
    """The heal flag is re-armed anyway; the extra newline is a blank line,
    which the loader filters without counting it as damage."""

    path, _ = await _torn_turn(tmp_path, lambda _content: "", _enospc())

    reopened = await _assert_turns_1_3_4(path)
    assert reopened.recovery is None


@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt(), asyncio.CancelledError()],
    ids=["keyboard-interrupt", "cancelled"],
)
async def test_an_interrupt_mid_line_is_handled_like_a_failed_write(
    tmp_path: Path, interrupt: BaseException
) -> None:
    """A second Ctrl-C between two ``os.write`` calls, or a cancelled awaiting
    ``FileSystem``: not an ``OSError``, so it propagates as itself — but it can
    leave the same fragment, so it must re-arm the heal the same way."""

    path, _ = await _torn_turn(tmp_path, _half, interrupt)

    reopened = await _assert_turns_1_3_4(path)
    assert reopened.recovery is not None
    assert reopened.recovery.skipped_lines == (3,)
    assert reopened.recovery.orphaned_entries == ()


async def test_a_complete_line_whose_newline_was_lost_is_kept_off_the_path(
    tmp_path: Path,
) -> None:
    """The failure hit after the last JSON byte. The entry is on disk although
    the caller was told it failed: at-least-once, and pinned as a decision
    (ADR-0242) so nobody "fixes" it into a truncation. It loads as an off-path
    sibling of the entries appended after it."""

    path, _ = await _torn_turn(tmp_path, lambda content: content[:-1], _enospc())

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    assert reopened.recovery is None
    assert [e.id for e in await reopened.get_entries()] == ["turn-1", "turn-2", "turn-3", "turn-4"]
    leaf = await reopened.get_leaf_id()
    assert [e.id for e in await reopened.get_path_to_root(leaf)] == ["turn-1", "turn-3", "turn-4"]


async def test_a_complete_line_that_was_the_last_write_is_the_resumed_leaf(
    tmp_path: Path,
) -> None:
    fs = _FailOnceFs()
    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path, fs)
    await storage.append_entry(user("turn-1", None))
    fs.arm(lambda content: content[:-1], _enospc())
    with pytest.raises(SessionError):
        await storage.append_entry(user("turn-2", "turn-1"))

    resumed = await _reopen(path)
    assert resumed.recovery is None
    assert await resumed.get_leaf_id() == "turn-2"
    await resumed.append_entry(user("turn-3", "turn-2"))
    assert [e.id for e in await (await _reopen(path)).get_entries()] == [
        "turn-1",
        "turn-2",
        "turn-3",
    ]


# === a payload json.dumps cannot encode, and one it changes =========================


@dataclasses.dataclass
class _NotJson:
    value: int = 1


_UNENCODABLE = pytest.mark.parametrize(
    "payload",
    [_NotJson(), {"tags": {"a", "b"}}, {"raw": b"bytes"}],
    ids=["dataclass", "set", "bytes"],
)


@_UNENCODABLE
async def test_a_payload_json_dumps_cannot_encode_is_refused_before_a_byte_is_written(
    tmp_path: Path, payload: Any
) -> None:
    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path)
    await storage.append_entry(user("a", None))
    before = path.read_bytes()

    bad = CustomEntry(id="bad", parent_id="a", timestamp=TS, custom_type="aelix.bad", data=payload)
    with pytest.raises(SessionError) as exc:
        await storage.append_entry(bad)

    assert exc.value.code == "invalid_entry"
    assert path.read_bytes() == before
    assert [e.id for e in await storage.get_entries()] == ["a"]
    assert await storage.get_leaf_id() == "a"
    # Nothing was armed either: the next append is exactly one clean line.
    await storage.append_entry(user("b", "a"))
    assert path.read_bytes() == before + _line(entry_to_json(user("b", "a")))


@_UNENCODABLE
async def test_create_with_a_payload_json_dumps_cannot_encode_writes_no_file(
    tmp_path: Path, payload: Any
) -> None:
    directory = tmp_path / "sessions"
    bad = CustomEntry(id="bad", parent_id="a", timestamp=TS, custom_type="aelix.bad", data=payload)

    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.create(
            LocalFileSystem(), str(directory / "s.jsonl"), cwd=str(tmp_path),
            session_id="s", entries=[user("a", None), bad],
        )

    assert exc.value.code == "invalid_entry"
    assert _leftovers(directory) == [], "no .jsonl and no .tmp"


def _unconvertible_message() -> MessageEntry:
    """A ``message`` entry ``entry_to_json`` cannot convert: ``asdict`` refuses a
    message that is not a dataclass, before ``json.dumps`` is ever reached."""

    return MessageEntry(
        id="bad", parent_id="a", timestamp=TS,
        message={"role": "user", "content": []},  # type: ignore[arg-type]
    )


async def test_an_entry_entry_to_json_cannot_convert_is_refused_the_same_way(
    tmp_path: Path,
) -> None:
    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path)
    await storage.append_entry(user("a", None))
    before = path.read_bytes()

    with pytest.raises(SessionError) as exc:
        await storage.append_entry(_unconvertible_message())

    assert exc.value.code == "invalid_entry"
    assert isinstance(exc.value.__cause__, TypeError)
    assert path.read_bytes() == before
    assert [e.id for e in await storage.get_entries()] == ["a"]
    await storage.append_entry(user("b", "a"))
    assert path.read_bytes() == before + _line(entry_to_json(user("b", "a")))


async def test_create_with_an_entry_entry_to_json_cannot_convert_writes_no_file(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "sessions"

    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.create(
            LocalFileSystem(), str(directory / "s.jsonl"), cwd=str(tmp_path),
            session_id="s", entries=[user("a", None), _unconvertible_message()],
        )

    assert exc.value.code == "invalid_entry"
    assert _leftovers(directory) == [], "no .jsonl and no .tmp"


async def test_what_json_dumps_can_encode_is_written_even_when_it_comes_back_changed(
    tmp_path: Path,
) -> None:
    """The store refuses only what ``json.dumps`` cannot encode (above). The rest
    is written the way ``json.dumps`` writes it: a tuple as a list, a non-string
    key as a string, ``NaN`` and ``Infinity`` bare, which Aelix reads back and a
    strict JSON reader (pi's among them) does not. Keeping payloads to JSON
    values is the writer's job (ADR-0242): nothing catches these, and refusing
    them now would lose turns that load today."""

    path = tmp_path / "s.jsonl"
    storage = await _new_storage(path)
    data = {"pair": (1, 2), 7: "int key", "nan": math.nan, "inf": math.inf, "-inf": -math.inf}
    record = CustomEntry(id="c1", parent_id=None, timestamp=TS, custom_type="aelix.t", data=data)
    await storage.append_entry(record)

    assert await storage.get_entry("c1") is record, "in memory it is kept as given"
    raw = path.read_bytes()
    assert b"NaN" in raw and b" Infinity" in raw and b"-Infinity" in raw
    back = await (await _reopen(path)).get_entry("c1")
    assert isinstance(back, CustomEntry) and isinstance(back.data, dict)
    reloaded: dict[Any, Any] = back.data
    assert reloaded["pair"] == [1, 2], "a tuple comes back a list"
    assert reloaded["7"] == "int key" and 7 not in reloaded, "a key comes back a string"
    assert math.isnan(reloaded["nan"])
    assert (reloaded["inf"], reloaded["-inf"]) == (math.inf, -math.inf)


# === a torn tail is discarded whole ===============================================


class _TextFs(LocalFileSystem):
    """Hands the loader ``text`` as the file's content without touching the disk."""

    def __init__(self) -> None:
        super().__init__()
        self.text = ""

    async def read_text_file(self, path: str) -> str:
        return self.text


async def test_a_torn_tail_is_discarded_whole_at_every_cut(tmp_path: Path) -> None:
    """Cut the file ``c`` bytes into entry line ``k``, for every ``c`` in
    ``[1, len - 1]`` of every line: the load keeps exactly the entries before
    ``k``, skips that one line, prunes nothing. The files are LF-only, so a
    line's bytes are its JSON bytes and every cut is a proper prefix of one
    JSON object — which never parses.

    The load half runs at every offset (some 7,200 cuts of the catalogue), with
    the cut handed to the loader as text: the file is ASCII and LF-only, so
    that text is exactly what reading the cut file returns. At five offsets per
    line (1, 2, middle, len-2, len-1) the cut is written to disk and loaded
    from there, then appended to and reloaded. Writing every cut to disk meant
    as many small files, which is cheap on APFS and unmeasured on
    windows-latest.
    """

    source = tmp_path / "source.jsonl"
    storage = await _new_storage(source)
    entries = catalogue()
    for entry in entries:
        await storage.append_entry(entry)
    raw = source.read_bytes()
    assert raw.isascii() and b"\r" not in raw, "json.dumps writes ASCII and escapes \\r"
    lines = raw.split(b"\n")[:-1]
    cut = tmp_path / "cut.jsonl"
    fs = LocalFileSystem()
    text_fs = _TextFs()

    for k, body in enumerate(lines[1:]):
        kept = b"".join(line + b"\n" for line in lines[: 1 + k])
        sampled = {1, 2, len(body) // 2, len(body) - 2, len(body) - 1}
        for c in range(1, len(body)):
            if c in sampled:
                cut.write_bytes(kept + body[:c])
                opened = await JsonlSessionStorage.open(fs, str(cut))
            else:
                text_fs.text = (kept + body[:c]).decode("ascii")
                opened = await JsonlSessionStorage.open(text_fs, str(cut))
            assert await opened.get_entries() == entries[:k], (k, c)
            assert opened.recovery is not None, (k, c)
            assert opened.recovery.skipped_lines == (k + 2,), (k, c)
            assert opened.recovery.orphaned_entries == (), (k, c)
            if c not in sampled:
                continue
            leaf = await opened.get_leaf_id()
            assert leaf == fold_leaf(entries[:k])
            await opened.append_entry(user("resumed", leaf))
            again = await JsonlSessionStorage.open(fs, str(cut))
            assert [e.id for e in await again.get_entries()] == [
                *(e.id for e in entries[:k]),
                "resumed",
            ], (k, c)
            assert (await again.get_path_to_root("resumed"))[-1].id == "resumed"


async def test_a_complete_final_line_without_its_newline_is_kept(tmp_path: Path) -> None:
    """``c == len``: the JSON is whole, only the ``\\n`` is missing. Kept (ADR-0208's
    decision, pinned here — pi v4 drops it), and the next append heals it."""

    source = tmp_path / "source.jsonl"
    storage = await _new_storage(source)
    entries = catalogue()
    for entry in entries:
        await storage.append_entry(entry)
    lines = source.read_bytes().split(b"\n")[:-1]
    cut = tmp_path / "cut.jsonl"
    fs = LocalFileSystem()

    for k in range(1, len(lines)):
        cut.write_bytes(b"".join(line + b"\n" for line in lines[:k]) + lines[k])
        opened = await JsonlSessionStorage.open(fs, str(cut))
        assert opened.recovery is None, k
        assert await opened.get_entries() == entries[:k]
        await opened.append_entry(user("next", await opened.get_leaf_id()))
        again = await JsonlSessionStorage.open(fs, str(cut))
        assert again.recovery is None, k
        assert [e.id for e in await again.get_entries()] == [*(e.id for e in entries[:k]), "next"]


# === CRLF files from before this change ============================================


def _crlf_session(path: Path, entries: list[SessionTreeEntry], *, last: bytes) -> None:
    rows = [HEADER, *(entry_to_json(e) for e in entries)]
    encoded = [json.dumps(row).encode("utf-8") for row in rows]
    path.write_bytes(b"\r\n".join(encoded) + last)


@pytest.mark.parametrize("last", [b"\r\n", b"\r"], ids=["crlf", "torn-between-cr-and-lf"])
async def test_a_crlf_session_still_loads_and_appends(tmp_path: Path, last: bytes) -> None:
    """Windows Aelix wrote CRLF until this change (text-mode descriptors). Those
    files must keep working; new lines are LF, so such a file ends up mixed."""

    path = tmp_path / "windows.jsonl"
    entries = catalogue()
    _crlf_session(path, entries, last=last)

    storage = await _reopen(path)
    assert storage.recovery is None
    assert await storage.get_entries() == entries

    appended = user("after-upgrade", await storage.get_leaf_id())
    await storage.append_entry(appended)

    raw = path.read_bytes()
    assert b"\r\n" in raw and raw.endswith(b"}\n"), "old lines untouched, the new one LF-only"
    reopened = await _reopen(path)
    assert reopened.recovery is None
    assert await reopened.get_entries() == [*entries, appended]


# === whole files appear complete or not at all =======================================


class _HalfWriteFs(LocalFileSystem):
    """Staging writes half the bytes, then the disk fills."""

    async def write_file(self, path: str, content: str) -> None:
        await super().write_file(path, content[: len(content) // 2])
        raise OSError(errno.ENOSPC, "No space left on device (injected mid write_file)")


class _FailRenameFs(LocalFileSystem):
    """The staged file is complete; publishing it fails."""

    async def rename_file(self, source: str, destination: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device (injected at rename_file)")


_PUBLISH_FAILURES = pytest.mark.parametrize(
    "failing_fs", [_HalfWriteFs, _FailRenameFs], ids=["half-write", "rename"]
)


#: Short synthetic cwds, as ``tests/test_jsonl_repo_fork.py`` uses. A fork's header
#: carries its cwd AND the source path (which embeds the source cwd again), so under
#: a long ``tmp_path`` a real cwd pushes it past 512 bytes. Until #297 that alone
#: made ``find_most_recent`` drop the fork, which would have made the ``is None``
#: assertions below pass for the wrong reason; the sniff now reads the whole first
#: line, so header length no longer decides. The short cwds stay because
#: ``test_a_fork_is_a_complete_owner_only_copy`` compares against them, and
#: that test proves this setup IS found.
SRC_CWD = "/src"
DST_CWD = "/dst"


async def _source_session(root: Path) -> tuple[JsonlSessionRepo, JsonlSessionStorage]:
    repo = JsonlSessionRepo(sessions_root=str(root / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=SRC_CWD))
    storage = session.get_storage()
    assert isinstance(storage, JsonlSessionStorage)
    for entry in catalogue():
        await storage.append_entry(entry)
    await storage.set_leaf_id("e05")
    return repo, storage


async def test_create_with_entries_writes_the_bytes_the_appends_would_have(
    tmp_path: Path,
) -> None:
    """Publishing in one piece changes WHEN the bytes appear, never which bytes.
    Only the header's creation timestamp may differ between the two files."""

    entries = catalogue()
    published = tmp_path / "published.jsonl"
    appended = tmp_path / "appended.jsonl"
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(published), cwd="/repo", session_id="same", entries=entries
    )
    by_append = await JsonlSessionStorage.create(
        LocalFileSystem(), str(appended), cwd="/repo", session_id="same"
    )
    for entry in entries:
        await by_append.append_entry(entry)

    head_p, *body_p = published.read_bytes().split(b"\n")
    head_a, *body_a = appended.read_bytes().split(b"\n")
    assert body_p == body_a
    assert {**json.loads(head_p), "timestamp": None} == {**json.loads(head_a), "timestamp": None}
    assert await storage.get_entries() == await by_append.get_entries() == entries
    assert await storage.get_leaf_id() == await by_append.get_leaf_id() == fold_leaf(entries)


@_PUBLISH_FAILURES
async def test_a_failed_create_leaves_no_file(
    tmp_path: Path, failing_fs: type[LocalFileSystem]
) -> None:
    directory = tmp_path / "sessions"
    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.create(
            failing_fs(), str(directory / "s.jsonl"), cwd=str(tmp_path),
            session_id="s", entries=catalogue(),
        )
    assert exc.value.code == "storage"
    assert _leftovers(directory) == []


class _InterruptFs(LocalFileSystem):
    """Raises ``interrupt`` at ``where`` in a publish: half-way through staging,
    just before the rename, or just after it."""

    def __init__(self, where: str, interrupt: BaseException) -> None:
        super().__init__()
        self._where = where
        self._interrupt = interrupt

    async def write_file(self, path: str, content: str) -> None:
        if self._where == "mid-write":
            await super().write_file(path, content[: len(content) // 2])
            raise self._interrupt
        await super().write_file(path, content)

    async def rename_file(self, source: str, destination: str) -> None:
        if self._where == "before-rename":
            raise self._interrupt
        await super().rename_file(source, destination)
        if self._where == "after-rename":
            raise self._interrupt


_INTERRUPTS = pytest.mark.parametrize(
    "interrupt", [KeyboardInterrupt, asyncio.CancelledError], ids=["keyboard-interrupt", "cancelled"]
)


@_INTERRUPTS
@pytest.mark.parametrize("where", ["mid-write", "before-rename", "after-rename"])
async def test_an_interrupted_publish_propagates_as_itself_and_leaves_no_temp(
    tmp_path: Path, where: str, interrupt: type[BaseException]
) -> None:
    """The temp is removed on ANY exception, not only an ``OSError``: a Ctrl-C or
    a cancellation half-way through still leaves nothing behind. An interrupt
    that lands after the rename finds the file already published, whole: an
    exception does not prove nothing was written, and a published file is
    never rolled back."""

    directory = tmp_path / "sessions"
    path = directory / "s.jsonl"
    entries = catalogue()

    with pytest.raises(interrupt):
        await JsonlSessionStorage.create(
            _InterruptFs(where, interrupt()), str(path), cwd=str(tmp_path),
            session_id="s", entries=entries,
        )

    if where == "after-rename":
        assert _leftovers(directory) == ["s.jsonl"]
        published = await _reopen(path)
        assert published.recovery is None
        assert await published.get_entries() == entries
    else:
        assert _leftovers(directory) == []


@_PUBLISH_FAILURES
async def test_a_failed_repo_create_leaves_nothing_to_continue(
    tmp_path: Path, failing_fs: type[LocalFileSystem]
) -> None:
    root = str(tmp_path / "sessions")
    cwd = str(tmp_path / "project")
    with pytest.raises(SessionError) as exc:
        await JsonlSessionRepo(fs=failing_fs(), sessions_root=root).create(
            JsonlSessionCreateOptions(cwd=cwd)
        )
    assert exc.value.code == "storage"
    repo = JsonlSessionRepo(sessions_root=root)
    assert await repo.find_most_recent(cwd) is None
    assert _leftovers(Path(await repo._get_session_dir(cwd))) == []


@_PUBLISH_FAILURES
async def test_a_failed_fork_leaves_nothing_to_continue(
    tmp_path: Path, failing_fs: type[LocalFileSystem]
) -> None:
    """The §0 [2] scenario of #294: a fork interrupted part-way used to leave a
    truncated session that ``find_most_recent`` — hence ``--continue`` — picked."""

    repo, source = await _source_session(tmp_path)
    dst = DST_CWD
    failing = JsonlSessionRepo(fs=failing_fs(), sessions_root=str(tmp_path / "sessions"))

    with pytest.raises(SessionError) as exc:
        await failing.fork(await source.get_metadata(), ForkOptions(cwd=dst))

    assert exc.value.code == "storage"
    assert await repo.find_most_recent(dst) is None
    assert _leftovers(Path(await repo._get_session_dir(dst))) == []


@_PUBLISH_FAILURES
async def test_a_failed_fork_from_leaves_nothing_to_continue(
    tmp_path: Path, failing_fs: type[LocalFileSystem]
) -> None:
    repo, source = await _source_session(tmp_path)
    dst = DST_CWD
    failing = JsonlSessionRepo(fs=failing_fs(), sessions_root=str(tmp_path / "sessions"))

    with pytest.raises(SessionError) as exc:
        await failing.fork_from(await source.get_metadata(), dst)

    assert exc.value.code == "storage"
    assert await repo.find_most_recent(dst) is None
    assert _leftovers(Path(await repo._get_session_dir(dst))) == []


class _CountingFs(LocalFileSystem):
    """Records which whole-file and append calls reach the filesystem, by name."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    async def write_file(self, path: str, content: str) -> None:
        self.calls.append(("write_file", Path(path).name))
        await super().write_file(path, content)

    async def rename_file(self, source: str, destination: str) -> None:
        self.calls.append(("rename_file", Path(destination).name))
        await super().rename_file(source, destination)

    async def append_file(self, path: str, content: str) -> None:
        self.calls.append(("append_file", Path(path).name))
        await super().append_file(path, content)


@pytest.mark.parametrize("how", ["fork", "fork_from"])
async def test_a_fork_is_one_publish_never_an_append_loop(tmp_path: Path, how: str) -> None:
    """The injections above fail inside the publish, so they cannot tell an atomic
    fork from the old header-then-append-per-entry loop (which also starts with a
    publish of the header). This can: a fork touches the filesystem exactly
    twice — stage the whole file, rename it into place — and never appends."""

    _, source = await _source_session(tmp_path)
    counting = _CountingFs()
    repo = JsonlSessionRepo(fs=counting, sessions_root=str(tmp_path / "sessions"))
    metadata = await source.get_metadata()

    if how == "fork":
        forked = await repo.fork(metadata, ForkOptions(cwd=DST_CWD))
    else:
        forked = await repo.fork_from(metadata, DST_CWD)

    name = Path((await forked.get_metadata()).path).name
    assert counting.calls == [("write_file", f"{name}.tmp"), ("rename_file", name)]
    assert await forked.get_entries() == await source.get_entries()


async def test_a_fork_is_a_complete_owner_only_copy(tmp_path: Path) -> None:
    repo, source = await _source_session(tmp_path)
    dst = DST_CWD

    forked = await repo.fork(await source.get_metadata(), ForkOptions(cwd=dst))

    path = (await forked.get_metadata()).path
    for view in (forked.get_storage(), await _reopen(Path(path))):
        assert await view.get_entries() == await source.get_entries()
        assert await view.get_leaf_id() == await source.get_leaf_id() == "e05"
    assert (await _reopen(Path(path))).recovery is None
    assert_mode(path, SESSION_FILE_MODE)
    assert _leftovers(Path(path).parent) == [Path(path).name]
    recent = await repo.find_most_recent(dst)
    assert recent is not None and recent.path == path


async def test_a_stale_temp_from_a_crash_does_not_block_the_next_publish(
    tmp_path: Path,
) -> None:
    target = tmp_path / "s.jsonl"
    stale = tmp_path / "s.jsonl.tmp"
    stale.write_bytes(b"half of something from a crash")
    if POSIX_MODES:
        os.chmod(stale, 0o644)

    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(target), cwd=str(tmp_path), session_id="s",
        entries=[user("a", None)],
    )

    assert [e.id for e in await storage.get_entries()] == ["a"]
    assert [e.id for e in await (await _reopen(target)).get_entries()] == ["a"]
    assert _leftovers(tmp_path) == ["s.jsonl"]
    assert_mode(target, SESSION_FILE_MODE)


# --- import: LocalFileSystem.copy_file -------------------------------------------------


def _half_copyfile(src: str, dst: str, *args: Any, **kwargs: Any) -> None:
    data = Path(src).read_bytes()
    Path(dst).write_bytes(data[: len(data) // 2])
    raise OSError(errno.ENOSPC, "No space left on device (injected mid copy)")


def _replace_fails(src: str, dst: str) -> None:
    raise OSError(errno.EXDEV, "injected at os.replace")


def _inject_copy_failure(monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    if where == "half-copy":
        monkeypatch.setattr(fs_mod.shutil, "copyfile", _half_copyfile)
    else:
        monkeypatch.setattr(fs_mod, "os", _OsProxy(replace=_replace_fails))


_COPY_FAILURES = pytest.mark.parametrize("where", ["half-copy", "replace"])


@_COPY_FAILURES
async def test_a_failed_import_copy_leaves_no_half_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER, entry_to_json(user("a", None)))
    destination = tmp_path / "store" / "incoming.jsonl"
    _inject_copy_failure(monkeypatch, where)

    with pytest.raises(OSError):
        await LocalFileSystem().copy_file(str(source), str(destination))

    assert _leftovers(destination.parent) == []


@_COPY_FAILURES
async def test_a_failed_import_copy_keeps_the_session_it_would_have_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Re-importing over a complete session used to truncate it FIRST (``O_TRUNC``)
    and then copy onto it — a failure in between lost the session outright."""

    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER, entry_to_json(user("new", None)))
    destination = tmp_path / "store" / "incoming.jsonl"
    destination.parent.mkdir()
    _write_session(destination, HEADER, entry_to_json(user("old", None)))
    old = destination.read_bytes()
    _inject_copy_failure(monkeypatch, where)

    with pytest.raises(OSError):
        await LocalFileSystem().copy_file(str(source), str(destination))

    assert destination.read_bytes() == old
    assert _leftovers(destination.parent) == ["incoming.jsonl"]


@_INTERRUPTS
async def test_an_interrupted_import_copy_propagates_as_itself_and_keeps_the_old_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
) -> None:
    """The temp is removed on ANY exception here too, not only an ``OSError``."""

    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER, entry_to_json(user("new", None)))
    destination = tmp_path / "store" / "incoming.jsonl"
    destination.parent.mkdir()
    _write_session(destination, HEADER, entry_to_json(user("old", None)))
    old = destination.read_bytes()

    def half_then_interrupt(src: str, dst: str, *args: Any, **kwargs: Any) -> None:
        data = Path(src).read_bytes()
        Path(dst).write_bytes(data[: len(data) // 2])
        raise interrupt()

    monkeypatch.setattr(fs_mod.shutil, "copyfile", half_then_interrupt)

    with pytest.raises(interrupt):
        await LocalFileSystem().copy_file(str(source), str(destination))

    assert destination.read_bytes() == old
    assert _leftovers(destination.parent) == ["incoming.jsonl"]


async def test_import_copy_syncs_the_whole_copy_before_it_replaces_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's one fsync (ADR-0242). ``copy_file`` can replace a complete
    session, and a rename that reaches the disk before the data it points at
    leaves an empty file after a power loss — so the staged copy is synced once
    it is whole, and only then renamed over the destination."""

    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER, entry_to_json(user("a", None)))
    destination = tmp_path / "store" / "incoming.jsonl"
    paths: dict[int, str] = {}
    events: list[tuple[str, str, Any]] = []

    def spy_open(path: str, flags: int, *args: Any) -> int:
        fd = os.open(path, flags, *args)
        paths[fd] = str(path)
        return fd

    def spy_fsync(fd: int) -> None:
        synced = Path(paths[fd])
        events.append(("fsync", synced.name, synced.read_bytes() == source.read_bytes()))
        os.fsync(fd)

    def spy_replace(src: str, dst: str) -> None:
        events.append(("replace", Path(src).name, Path(dst).name))
        os.replace(src, dst)

    monkeypatch.setattr(
        fs_mod, "os", _OsProxy(open=spy_open, fsync=spy_fsync, replace=spy_replace)
    )

    await LocalFileSystem().copy_file(str(source), str(destination))

    assert [(kind, _temp_shape(a), b) for kind, a, b in events] == [
        ("fsync", "incoming.jsonl.<hex>.tmp", True),
        ("replace", "incoming.jsonl.<hex>.tmp", "incoming.jsonl"),
    ]


async def test_import_copy_is_byte_exact_and_owner_only(tmp_path: Path) -> None:
    source = tmp_path / "incoming.jsonl"
    # Foreign bytes, copied verbatim: CRLF and raw UTF-8 as a pi export has them.
    source.write_bytes(b'{"type":"session"}\r\n{"x":"caf\xc3\xa9"}\n')
    os.utime(source, (1_700_000_000, 1_700_000_000))
    destination = tmp_path / "store" / "incoming.jsonl"

    await LocalFileSystem().copy_file(str(source), str(destination))

    assert destination.read_bytes() == source.read_bytes()
    # ``find_most_recent`` sorts on this. No relative tolerance: the default
    # ``approx`` would let a timestamp half an hour off pass.
    assert destination.stat().st_mtime == pytest.approx(source.stat().st_mtime, rel=0, abs=1e-3)
    assert_mode(destination, SESSION_FILE_MODE)
    assert _leftovers(destination.parent) == ["incoming.jsonl"]


async def test_two_imports_of_one_file_never_share_a_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An import's destination is named after its source, so two processes that
    import the same file target the same name. A shared ``<destination>.tmp``
    let one remove or overwrite the other's half-written copy and rename it
    into place (Codex review, #294). Each call stages under its own name, and a
    temp that is not its own — another process's copy in progress — is left
    exactly as it was."""

    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER, entry_to_json(user("a", None)))
    destination = tmp_path / "store" / "incoming.jsonl"
    destination.parent.mkdir()
    theirs = destination.parent / "incoming.jsonl.tmp"
    theirs.write_bytes(b"another import, half way through")
    staged: list[str] = []

    def spy_open(path: str, flags: int, *args: Any) -> int:
        if flags & os.O_EXCL:
            staged.append(Path(path).name)
        return os.open(path, flags, *args)

    monkeypatch.setattr(fs_mod, "os", _OsProxy(open=spy_open))
    fs = LocalFileSystem()
    await fs.copy_file(str(source), str(destination))
    await fs.copy_file(str(source), str(destination))

    assert len(staged) == 2 and staged[0] != staged[1], staged
    assert all(_temp_shape(name) == "incoming.jsonl.<hex>.tmp" for name in staged), staged
    assert theirs.read_bytes() == b"another import, half way through"
    assert _leftovers(destination.parent) == ["incoming.jsonl", "incoming.jsonl.tmp"]
    assert destination.read_bytes() == source.read_bytes()


# --- Windows: a rename refused while the just-closed temp is still held -----------------


def _flaky_replace(refusals: int, calls: list[str]) -> Callable[[str, str], None]:
    """``os.replace`` that answers ``PermissionError`` ``refusals`` times first."""

    def replace(src: str, dst: str) -> None:
        calls.append(Path(dst).name)
        if len(calls) <= refusals:
            raise PermissionError(errno.EACCES, "held by another process (injected)")
        os.replace(src, dst)

    return replace


def _on_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str | None, slept: list[float]
) -> None:
    """Give ``session/fs.py`` alone a sleep that only records, and — unless
    ``platform`` is ``None`` — make it alone see ``platform``.

    Only ever fake ``win32``. Faking POSIX on a Windows runner sends
    ``_tighten_if_loose`` to ``os.fchmod``, which Windows does not have; a POSIX
    case runs on a real POSIX platform instead."""

    async def record(delay: float) -> None:
        slept.append(delay)

    if platform is not None:
        monkeypatch.setattr(fs_mod, "sys", types.SimpleNamespace(platform=platform))
    monkeypatch.setattr(fs_mod, "asyncio", types.SimpleNamespace(sleep=record))


async def test_a_windows_rename_refused_for_a_moment_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every create and every import now ends in a rename, and on Windows a
    scanner or indexer briefly holding the just-closed temp refuses it with
    ``PermissionError``. Session creation must not fail on that: the rename is
    retried for about a second (the temp is complete by then, so waiting can
    never publish anything partial)."""

    calls: list[str] = []
    slept: list[float] = []
    _on_platform(monkeypatch, "win32", slept)
    monkeypatch.setattr(fs_mod, "os", _OsProxy(replace=_flaky_replace(2, calls)))
    path = tmp_path / "sessions" / "s.jsonl"
    entries = catalogue()

    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(path), cwd=str(tmp_path), session_id="s", entries=entries
    )

    assert calls == ["s.jsonl", "s.jsonl", "s.jsonl"]
    assert slept == list(fs_mod._REPLACE_RETRY_DELAYS[:2])
    assert await storage.get_entries() == entries
    assert await (await _reopen(path)).get_entries() == entries
    assert _leftovers(path.parent) == ["s.jsonl"]


async def test_a_windows_import_rename_refused_for_a_moment_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    slept: list[float] = []
    _on_platform(monkeypatch, "win32", slept)
    monkeypatch.setattr(fs_mod, "os", _OsProxy(replace=_flaky_replace(1, calls)))
    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER, entry_to_json(user("a", None)))
    destination = tmp_path / "store" / "incoming.jsonl"

    await LocalFileSystem().copy_file(str(source), str(destination))

    assert calls == ["incoming.jsonl", "incoming.jsonl"]
    assert slept == list(fs_mod._REPLACE_RETRY_DELAYS[:1])
    assert destination.read_bytes() == source.read_bytes()
    assert _leftovers(destination.parent) == ["incoming.jsonl"]


async def test_a_windows_rename_that_stays_refused_gives_up_after_about_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    slept: list[float] = []
    _on_platform(monkeypatch, "win32", slept)
    monkeypatch.setattr(fs_mod, "os", _OsProxy(replace=_flaky_replace(10**6, calls)))
    directory = tmp_path / "sessions"

    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.create(
            LocalFileSystem(), str(directory / "s.jsonl"), cwd=str(tmp_path), session_id="s"
        )

    assert exc.value.code == "storage"
    assert isinstance(exc.value.__cause__, PermissionError)
    assert slept == list(fs_mod._REPLACE_RETRY_DELAYS)
    assert sum(slept) == pytest.approx(1.0)
    assert len(calls) == len(fs_mod._REPLACE_RETRY_DELAYS) + 1
    assert _leftovers(directory) == [], "the staged temp is removed once it gives up"


def _stuck_in_backoff(
    monkeypatch: pytest.MonkeyPatch, slept: list[float], interrupt: type[BaseException]
) -> None:
    """Windows, and a retry sleep that is where the interrupt lands: a
    ``KeyboardInterrupt`` raised from it, or a sleep that never returns so the
    test can cancel the task that is waiting in it."""

    async def sleep(delay: float) -> None:
        slept.append(delay)
        if interrupt is KeyboardInterrupt:
            raise KeyboardInterrupt
        await asyncio.Event().wait()

    monkeypatch.setattr(fs_mod, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(fs_mod, "asyncio", types.SimpleNamespace(sleep=sleep))


async def _interrupt_in_backoff(
    call: typing.Coroutine[Any, Any, Any], slept: list[float], interrupt: type[BaseException]
) -> None:
    if interrupt is KeyboardInterrupt:
        # Awaited in place: a KeyboardInterrupt that escapes a separate Task is
        # re-raised out of the event loop itself, which would end the test run.
        with pytest.raises(KeyboardInterrupt):
            await call
        return
    task = asyncio.ensure_future(call)
    while not slept and not task.done():
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@_INTERRUPTS
async def test_an_interrupt_during_the_rename_backoff_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
) -> None:
    """The retry sleep is a new place for a Ctrl-C or a cancellation to land
    (Codex review of the retry). It propagates as itself, no further rename is
    tried, and the staged temp is removed like on any other failure."""

    calls: list[str] = []
    slept: list[float] = []
    _stuck_in_backoff(monkeypatch, slept, interrupt)
    monkeypatch.setattr(fs_mod, "os", _OsProxy(replace=_flaky_replace(10**6, calls)))
    directory = tmp_path / "sessions"

    await _interrupt_in_backoff(
        JsonlSessionStorage.create(
            LocalFileSystem(), str(directory / "s.jsonl"), cwd=str(tmp_path),
            session_id="s", entries=catalogue(),
        ),
        slept,
        interrupt,
    )

    assert calls == ["s.jsonl"] and slept == [fs_mod._REPLACE_RETRY_DELAYS[0]]
    assert _leftovers(directory) == []


@_INTERRUPTS
async def test_an_interrupt_during_the_import_backoff_keeps_the_old_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException]
) -> None:
    calls: list[str] = []
    slept: list[float] = []
    _stuck_in_backoff(monkeypatch, slept, interrupt)
    monkeypatch.setattr(fs_mod, "os", _OsProxy(replace=_flaky_replace(10**6, calls)))
    source = tmp_path / "incoming.jsonl"
    _write_session(source, HEADER, entry_to_json(user("new", None)))
    destination = tmp_path / "store" / "incoming.jsonl"
    destination.parent.mkdir()
    _write_session(destination, HEADER, entry_to_json(user("old", None)))
    old = destination.read_bytes()

    await _interrupt_in_backoff(
        LocalFileSystem().copy_file(str(source), str(destination)), slept, interrupt
    )

    assert calls == ["incoming.jsonl"]
    assert destination.read_bytes() == old
    assert _leftovers(destination.parent) == ["incoming.jsonl"]


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX branch runs on POSIX")
async def test_a_posix_rename_permission_error_is_final_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing holds a file that way on POSIX, so a refusal there is a real one —
    waiting a second before reporting it would only delay the error."""

    calls: list[str] = []
    slept: list[float] = []
    _on_platform(monkeypatch, None, slept)
    monkeypatch.setattr(fs_mod, "os", _OsProxy(replace=_flaky_replace(1, calls)))
    directory = tmp_path / "sessions"

    with pytest.raises(SessionError) as exc:
        await JsonlSessionStorage.create(
            LocalFileSystem(), str(directory / "s.jsonl"), cwd=str(tmp_path), session_id="s"
        )

    assert exc.value.code == "storage"
    assert calls == ["s.jsonl"]
    assert slept == []
    assert _leftovers(directory) == []


# === why the rules exist: the RELEASED loader and fork ================================


def _mid_record_cases() -> list[Any]:
    def unknown_type(wire: dict[str, Any]) -> dict[str, Any]:
        return {**wire, "type": "future_record"}

    def unknown_role(wire: dict[str, Any]) -> dict[str, Any]:
        return {**wire, "message": {**wire["message"], "role": "futureRole"}}

    def missing_required_key(wire: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in wire.items() if key != "message"}

    def content_as_a_string(wire: dict[str, Any]) -> dict[str, Any]:
        return {**wire, "message": {**wire["message"], "content": "pi's own UserMessage shape"}}

    return [
        pytest.param(unknown_type, id="unknown-entry-type"),
        pytest.param(unknown_role, id="unknown-message-role"),
        pytest.param(missing_required_key, id="known-type-missing-a-required-key"),
        pytest.param(content_as_a_string, id="message-content-as-a-string"),
    ]


@pytest.mark.parametrize("mutate", _mid_record_cases())
async def test_the_released_loader_drops_a_record_it_cannot_decode_and_everything_below(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    """This is what beta.1 and beta.2 do, forever: the line is skipped as damage
    and ADR-0208's orphan pass prunes every entry whose parent chain runs
    through it. A new entry type in the middle of a tree costs an old reader
    the rest of the conversation."""

    path = tmp_path / "s.jsonl"
    root, mid, child, grandchild = (
        user("root", None), user("mid", "root"), user("child", "mid"), user("grandchild", "child"),
    )
    _write_session(
        path, HEADER, entry_to_json(root), mutate(entry_to_json(mid)),
        entry_to_json(child), entry_to_json(grandchild),
    )

    storage = await _reopen(path)

    assert [e.id for e in await storage.get_entries()] == ["root"], (
        f"the released loader changed — the record rules in {_ADR} rest on it"
    )
    assert storage.recovery is not None
    assert storage.recovery.skipped_lines == (3,)
    assert storage.recovery.orphaned_entries == ("child", "grandchild")


def test_display_is_read_as_truthiness_not_as_a_json_bool() -> None:
    """Why ``display`` must stay a JSON bool (ADR-0242): the string ``"false"``
    does not fail to decode, it silently reads as ``True``."""

    wire = entry_to_json(
        CustomMessageEntry(
            id="m", parent_id=None, timestamp=TS, custom_type="x", content="hi", display=False
        )
    )
    decoded = entry_from_json({**wire, "display": "false"})
    assert isinstance(decoded, CustomMessageEntry)
    assert decoded.display is True, f"a stringly-typed bool flips — see {_ADR}"


async def test_a_released_fork_keeps_only_what_is_inside_custom_entry_data(
    tmp_path: Path,
) -> None:
    """``fork``/``fork_from`` decode into dataclasses and re-encode, so every key
    a reader does not know is dropped — except inside ``CustomEntry.data``,
    which is carried verbatim. Everything a record needs to survive goes
    there (ADR-0242)."""

    source = tmp_path / "source.jsonl"
    message = entry_to_json(
        user("m1", None)
    )
    message["futureKey"] = "entry level"
    message["message"]["futureMsgKey"] = "message level"
    message["message"]["content"][0]["futureBlockKey"] = "content-block level"
    record = entry_to_json(
        CustomEntry(id="c1", parent_id="m1", timestamp=TS, custom_type="aelix.future",
                    data={"known": 1})
    )
    record["data"]["futureDataKey"] = "inside data"
    record["futureKey"] = "entry level"
    _write_session(source, {**HEADER, "futureHeaderKey": "header level"}, message, record)
    repo = JsonlSessionRepo(sessions_root=str(tmp_path / "sessions"))
    metadata = await load_jsonl_session_metadata(LocalFileSystem(), str(source))

    for fork in (
        lambda: repo.fork(metadata, ForkOptions(cwd=str(tmp_path / "fork"))),
        lambda: repo.fork_from(metadata, str(tmp_path / "fork-from")),
    ):
        forked = await fork()
        rows = [
            json.loads(line)
            for line in Path((await forked.get_metadata()).path).read_bytes().split(b"\n")
            if line
        ]
        header, forked_message, forked_record = rows
        assert "futureHeaderKey" not in header
        assert "futureKey" not in forked_message
        assert "futureMsgKey" not in forked_message["message"]
        assert "futureBlockKey" not in forked_message["message"]["content"][0]
        assert "futureKey" not in forked_record
        assert forked_record["data"] == {"known": 1, "futureDataKey": "inside data"}, (
            f"CustomEntry.data is the one place a fork carries unknown keys — {_ADR}"
        )


def test_an_unknown_key_is_ignored_on_read() -> None:
    """The positive twin: readers ignore keys they do not know."""

    for entry in catalogue():
        wire = json.loads(json.dumps(entry_to_json(entry)))
        wire["futureKey"] = {"anything": [1, 2, 3]}
        assert entry_from_json(wire) == entry, f"{entry.id} ({entry.type}) — {_ADR}"


async def test_a_header_with_an_unknown_key_opens(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    _write_session(path, {**HEADER, "futureHeaderKey": 1}, entry_to_json(user("a", None)))

    storage = await _reopen(path)

    assert storage.recovery is None
    assert [e.id for e in await storage.get_entries()] == ["a"]


async def test_a_header_version_other_than_3_refuses_the_whole_file(tmp_path: Path) -> None:
    """Why ``version`` stays 3: every released Aelix rejects the file outright."""

    path = tmp_path / "s.jsonl"
    _write_session(path, {**HEADER, "version": 4}, entry_to_json(user("a", None)))

    with pytest.raises(SessionError) as exc:
        await _reopen(path)
    assert exc.value.code == "invalid_session", _ADR


# === the closed set ===============================================================


def test_the_entry_type_set_is_closed() -> None:
    types_on_disk = {
        f.default
        for cls in typing.get_args(SessionTreeEntry)
        for f in dataclasses.fields(cls)
        if f.name == "type"
    }
    assert types_on_disk == set(ENTRY_TYPES), (
        "the SessionTreeEntry union changed. Do not add an entry type: every "
        "released Aelix skips a line whose `type` it does not know AND prunes "
        "every entry below it. Put the record in a CustomEntry with an `aelix.` "
        f"customType and everything durable inside `data` — {_ADR}."
    )
