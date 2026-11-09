"""#709 — ``find_most_recent`` (and so ``aelix -c``) skips a session with no conversation.

aelix writes a session file when a session starts (ADR-0239 decision 6): a
header and the starting model, before anyone types. A launch closed without a
prompt, or a ``/new`` (or RPC ``new_session``) never used, leaves such a file,
and it is the newest one, so on 6caaae09 ``aelix -c`` opened it and the model
was sent only the new prompt (measured in the real TUI: ``["continued?"]``
instead of ``["hello", "continued?"]``). So did a session whose only prompt was
withdrawn with Esc: the prompt stays in the file on a branch left behind.

A conversation is a ``message`` entry from the user or the assistant ON THE
BRANCH — the rule ``/clone`` uses since #654 (ADR-0286), not pi's file-wide
``_hasConversation`` (pi writes no file before one, so its ``--continue`` never
meets the case). Model and thinking-level changes, custom records, custom
messages, labels and names do not count.

A file larger than :data:`_CONVERSATION_SCAN_MAX_BYTES` counts as a
conversation without being read: the wrong answer there is the behaviour
before #709, never a conversation ``-c`` cannot reach.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import JsonlSessionCreateOptions, JsonlSessionRepo
from aelix_agent_core.session import jsonl_repo as jsonl_repo_module
from aelix_ai.messages import AssistantMessage, TextContent, ToolResultMessage, UserMessage

CWD = "/r709"
# Read through the module so the behavioural tests fail on their assertions,
# not on an ImportError, where the constant does not exist (6caaae09).
_CONVERSATION_SCAN_MAX_BYTES: int = getattr(
    jsonl_repo_module, "_CONVERSATION_SCAN_MAX_BYTES", 1024 * 1024
)


def test_the_scan_budget_is_one_mebibyte() -> None:
    assert jsonl_repo_module._CONVERSATION_SCAN_MAX_BYTES == 1024 * 1024


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


async def _conversation(repo: JsonlSessionRepo, *, mtime: int) -> str:
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    await session.append_model_change("scripted", "s-1")
    await session.append_message(_user("hello"))
    await session.append_message(AssistantMessage(content=[TextContent(text="REPLY-1")]))
    path = (await session.get_metadata()).path
    os.utime(path, (mtime, mtime))
    return path


async def _empty(repo: JsonlSessionRepo, *, mtime: int) -> str:
    """What a launch quit without a prompt, or an unused ``/new``, leaves (measured)."""

    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    await session.append_model_change("scripted", "s-1")
    path = (await session.get_metadata()).path
    os.utime(path, (mtime, mtime))
    return path


async def test_a_newer_session_without_a_conversation_is_skipped(tmp_path: Path) -> None:
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    talked = await _conversation(repo, mtime=1000)
    await _empty(repo, mtime=2000)
    await _empty(repo, mtime=3000)

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == talked


async def test_a_header_alone_is_not_a_conversation(tmp_path: Path) -> None:
    """A valid header and nothing else: the file ``repo.create`` writes."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    talked = await _conversation(repo, mtime=1000)
    bare = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    bare_path = (await bare.get_metadata()).path
    os.utime(bare_path, (2000, 2000))
    assert JsonlSessionRepo._is_valid_session_file(Path(bare_path)) is True

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == talked


async def test_setup_records_are_not_a_conversation(tmp_path: Path) -> None:
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    talked = await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    first = await session.append_model_change("scripted", "s-1")
    await session.append_thinking_level_change("high")
    await session.append_custom_entry("ext-state", {"role": "user", "message": "not a turn"})
    await session.append_custom_message_entry("ext-note", "a custom message", display=True)
    await session.append_label(first, "a label")
    await session.append_session_name("named but empty")
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == talked


async def test_only_empty_sessions_find_nothing(tmp_path: Path) -> None:
    """``-c`` then starts a new session, as it does in a folder with none."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    await _empty(repo, mtime=1000)
    await _empty(repo, mtime=2000)
    assert await repo.find_most_recent(CWD) is None


async def test_a_prompt_that_was_never_answered_is_a_conversation(tmp_path: Path) -> None:
    """A user message with no reply (the turn failed or was cut off) counts, as in pi."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    await session.append_model_change("scripted", "s-1")
    await session.append_message(_user("unanswered"))
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == path


async def test_a_prompt_withdrawn_with_esc_is_not_a_conversation(tmp_path: Path) -> None:
    """The file keeps the withdrawn prompt on a branch left behind (ADR-0286).

    Opened, that session shows nothing and the model would be sent nothing of
    it: measured on 6caaae09 in the real TUI, ``-c -p continued?`` after such a
    session sent ``["continued?"]``. pi would count the file (its rule reads
    every entry); aelix asks the branch, as ``/clone`` does since #654.
    """

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    talked = await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    first = await session.append_model_change("scripted", "s-1")
    await session.append_message(_user("withdrawn-prompt"))
    await session.withdraw_tail(first, lambda entry: getattr(entry, "type", "") == "message")
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))
    kinds = [json.loads(line)["type"] for line in Path(path).read_text().splitlines()]
    assert kinds == ["session", "model_change", "message", "leaf"]

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == talked


async def test_a_prompt_typed_again_after_esc_is_a_conversation(tmp_path: Path) -> None:
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    first = await session.append_model_change("scripted", "s-1")
    await session.append_message(_user("withdrawn-prompt"))
    await session.withdraw_tail(first, lambda entry: getattr(entry, "type", "") == "message")
    await session.append_message(_user("asked again"))
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == path


async def test_a_branch_moved_back_to_the_start_is_not_a_conversation(tmp_path: Path) -> None:
    """The branch decides, wherever the leaf was moved (``/tree`` to the first entry)."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    talked = await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    first = await session.append_model_change("scripted", "s-1")
    await session.append_message(_user("q"))
    await session.append_message(AssistantMessage(content=[TextContent(text="a")]))
    await session.move_to(first)
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == talked


async def test_an_assistant_message_alone_counts(tmp_path: Path) -> None:
    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    await session.append_message(AssistantMessage(content=[TextContent(text="hi")]))
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == path


async def test_a_tool_result_alone_does_not_count(tmp_path: Path) -> None:
    """pi's rule names two roles; a tool result is not one of them."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    talked = await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    await session.append_message(
        ToolResultMessage(tool_call_id="c1", tool_name="bash", content=[TextContent(text="x")])
    )
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == talked


def _line(record: dict[str, Any]) -> bytes:
    return json.dumps(record).encode() + b"\n"


_HEADER = {
    "type": "session",
    "version": 3,
    "id": "h",
    "timestamp": "2026-11-09T00:00:00.000Z",
    "cwd": CWD,
}
_T = "2026-11-09T00:00:01.000Z"


def _msg(
    entry_id: str, role: str, parent: str | None = None, kind: str = "message"
) -> dict[str, Any]:
    return {
        "type": kind,
        "id": entry_id,
        "parentId": parent,
        "timestamp": _T,
        "message": {"role": role, "content": [{"type": "text", "text": "x"}]},
    }


def test_the_rule_reads_message_entries_of_two_roles(tmp_path: Path) -> None:
    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"

    p.write_bytes(_line(_HEADER) + _line(_msg("u", "user")))
    assert has(p) is True
    p.write_bytes(_line(_HEADER) + _line(_msg("a", "assistant")))
    assert has(p) is True
    p.write_bytes(_line(_HEADER) + _line(_msg("t", "toolResult")))
    assert has(p) is False
    p.write_bytes(_line(_HEADER) + _line(_msg("c", "user", kind="custom_message")))
    assert has(p) is False
    p.write_bytes(_line(_HEADER))
    assert has(p) is False
    # pi writes ``"type"`` first and no spaces; the loader reads the record, not the bytes.
    p.write_bytes(
        json.dumps(_HEADER, separators=(",", ":")).encode()
        + b'\n{"type":"message","id":"u","parentId":null,"timestamp":"2026-11-09T00:00:01.000Z",'
        + b'"message":{"role":"user","content":[{"type":"text","text":"x"}]}}\n'
    )
    assert has(p) is True


def test_a_torn_line_is_dropped_as_the_loader_drops_it(tmp_path: Path) -> None:
    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    # A crash mid-append cut the last line; the turn before it stands.
    p.write_bytes(
        _line(_HEADER) + _line(_msg("u", "user")) + b'{"type": "message", "id": "a", "mess'
    )
    assert has(p) is True
    # The only turn is the torn line: nothing.
    p.write_bytes(_line(_HEADER) + b'{"type": "message", "id": "u", "mess')
    assert has(p) is False
    # A turn whose parent was torn is pruned on open, so it is pruned here too.
    p.write_bytes(_line(_HEADER) + b'{"type": "model_chan\n' + _line(_msg("u", "user", parent="m")))
    assert has(p) is False
    # An unterminated final line that is whole still counts (ADR-0208).
    p.write_bytes(_line(_HEADER) + _line(_msg("u", "user")).rstrip(b"\n"))
    assert has(p) is True
    p.write_bytes(_line(_HEADER) + b"\xff\xfe not utf-8\n" + b"[" * 100_000 + b"\n")
    assert has(p) is False
    assert has(tmp_path / "missing.jsonl") is False
    p.write_bytes(b'{"type": "session", "version": 2, "id": "h"}\n' + _line(_msg("u", "user")))
    assert has(p) is False  # a header the loader refuses


def test_a_file_larger_than_the_bound_counts_unread(tmp_path: Path) -> None:
    """A first turn carrying a pasted file or an image puts a session past it."""

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    filler = {
        "type": "custom",
        "id": "c",
        "parentId": None,
        "timestamp": _T,
        "customType": "x",
        "data": "y" * 4000,
    }
    p.write_bytes(_line(_HEADER) + _line(filler) * (_CONVERSATION_SCAN_MAX_BYTES // 4000 + 2))
    assert p.stat().st_size > _CONVERSATION_SCAN_MAX_BYTES
    assert has(p) is True  # no message, but too large to read: counted, as before #709
    p.write_bytes(_line(_HEADER) + _line(filler) * 3)
    assert has(p) is False


def test_a_file_exactly_at_the_bound_is_read(tmp_path: Path) -> None:
    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    head = _line(_HEADER)
    pad = (
        b'{"type": "custom", "id": "c", "parentId": null, "timestamp": "'
        + _T.encode()
        + b'", "customType": "x", "data": "'
    )
    room = _CONVERSATION_SCAN_MAX_BYTES - len(head) - len(pad) - len(b'"}\n')
    body = head + pad + b"z" * room + b'"}\n'
    assert len(body) == _CONVERSATION_SCAN_MAX_BYTES
    p.write_bytes(body)
    assert has(p) is False
    p.write_bytes(body + b"\n")
    assert has(p) is True


class _CountingFileIO(io.FileIO):
    """Counts what is read from the disk, under any buffer or text layer above it."""

    counts: dict[str, int] = {}

    def _add(self, n: int) -> None:
        name = str(self.name)
        type(self).counts[name] = type(self).counts.get(name, 0) + n

    def readinto(self, buffer: Any, /) -> int | None:
        n = super().readinto(buffer)
        self._add(n or 0)
        return n

    def readall(self) -> bytes:
        data = super().readall()
        self._add(len(data))
        return data


def _count_session_reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Route every ``open`` of a ``.jsonl`` for reading in the repo and fs modules through the counter.

    The header sniff and the scan open in ``jsonl_repo``; the metadata parse
    (``read_text_lines``) opens in ``fs``. Codex's review found the 843b0c14
    test counted only the first.
    """

    from aelix_agent_core.session import fs as fs_module

    real_open = open
    _CountingFileIO.counts = {}

    def counting_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if str(file).endswith(".jsonl") and set(mode) <= {"r", "b", "t"}:
            buffered = io.BufferedReader(_CountingFileIO(str(file), "rb"))
            if "b" in mode:
                return buffered
            return io.TextIOWrapper(buffered, encoding=k.get("encoding") or "utf-8")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(jsonl_repo_module, "open", counting_open, raising=False)
    monkeypatch.setattr(fs_module, "open", counting_open, raising=False)
    return _CountingFileIO.counts


# What one read of a header costs: the read buffer it fills, which is
# ``io.DEFAULT_BUFFER_SIZE`` under the counter (8 KiB on Python 3.12, 128 KiB
# on 3.14), and far below what reading a long line takes.
_HEADER_READ_ROOM = max(64 * 1024, io.DEFAULT_BUFFER_SIZE)


async def test_a_large_session_is_not_read_to_decide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-c`` reads the start of a long session (about 8 MB here), not the session.

    Measured with the counter below on macOS: 16 KiB of the 8 MB file on Python
    3.12, one 8 KiB buffer for the header sniff and one for the metadata parse
    (136 KiB on 3.14, whose default buffer is 128 KiB); the conversation check
    reads its size only.
    """

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    path = await _conversation(repo, mtime=1000)
    with open(path, "ab") as fh:
        reply = {
            "type": "message",
            "id": "a",
            "message": {"role": "assistant", "content": "r" * 8000},
        }
        for _ in range(1000):
            fh.write(json.dumps(reply).encode() + b"\n")
    assert Path(path).stat().st_size > 8_000_000

    counts = _count_session_reads(monkeypatch)
    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == path
    assert 0 < counts[path] <= 2 * _HEADER_READ_ROOM, counts


async def test_a_long_second_line_is_not_read_for_the_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metadata parse asked for one line and read two (Codex 7, pre-existing).

    A first prompt carrying a 2 MiB paste is the second line of its session;
    ``read_text_lines(max_lines=1)`` read all of it before stopping, on
    6caaae09 and 843b0c14 alike (2,105,636 bytes read for a 134-byte header,
    measured). It now stops after the line it returns.
    """

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    path = (await session.get_metadata()).path
    with open(path, "ab") as fh:
        fh.write(_line(_msg("u", "user")).replace(b'"x"', b'"' + b"x" * (2 * 1024 * 1024) + b'"'))
    assert Path(path).stat().st_size > 2 * 1024 * 1024

    counts = _count_session_reads(monkeypatch)
    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == path
    assert 0 < counts[path] <= 2 * _HEADER_READ_ROOM, counts


async def test_read_text_lines_returns_what_it_returned(tmp_path: Path) -> None:
    from aelix_agent_core.session.fs import LocalFileSystem

    fs = LocalFileSystem()
    p = tmp_path / "f.txt"
    p.write_text("one\ntwo\nthree")
    assert await fs.read_text_lines(str(p), max_lines=1) == ["one"]
    assert await fs.read_text_lines(str(p), max_lines=2) == ["one", "two"]
    assert await fs.read_text_lines(str(p), max_lines=5) == ["one", "two", "three"]
    assert await fs.read_text_lines(str(p), max_lines=0) == []
    assert await fs.read_text_lines(str(p)) == ["one", "two", "three"]
    p.write_text("")
    assert await fs.read_text_lines(str(p), max_lines=1) == []


async def test_read_text_lines_no_longer_raises_on_a_line_it_does_not_return(
    tmp_path: Path,
) -> None:
    """Fix round 2 pins the one change in what ``read_text_lines`` returns.

    On 6caaae09 asking for one line read the whole second line and raised
    ``UnicodeDecodeError`` on a bad byte in it; now the first line comes back
    when the bad byte lies past the first read buffer (3.14's is 128 KiB). A
    bad byte inside that buffer still raises, as it did, and ``max_lines=0``
    opens nothing (6caaae09 raised on a bad first line).
    """

    from aelix_agent_core.session.fs import LocalFileSystem

    fs = LocalFileSystem()
    p = tmp_path / "f.txt"
    p.write_bytes(b"head\n" + b"y" * 300_000 + b"\xff\n")
    assert await fs.read_text_lines(str(p), max_lines=1) == ["head"]
    p.write_bytes(b"head\n" + "한".encode()[:2])
    assert await fs.read_text_lines(str(p), max_lines=1) == ["head"]
    p.write_bytes(b"head\n\xffy\n")
    with pytest.raises(UnicodeDecodeError):
        await fs.read_text_lines(str(p), max_lines=1)
    p.write_bytes(b"\xff\n")
    assert await fs.read_text_lines(str(p), max_lines=0) == []


def test_a_parent_cycle_ends_the_walk(tmp_path: Path) -> None:
    """A corrupt file whose parents loop is read once around the loop, not forever."""

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    a = {"type": "custom", "id": "a", "parentId": "b", "timestamp": _T, "customType": "x"}
    b = {"type": "custom", "id": "b", "parentId": "a", "timestamp": _T, "customType": "x"}
    p.write_bytes(_line(_HEADER) + _line(a) + _line(b))
    assert has(p) is False
    u = _msg("u", "user", parent="b")
    a2 = dict(a, parentId="u")
    p.write_bytes(
        _line(_HEADER) + _line(a2) + _line(b) + _line(u) + _line(dict(b, id="c", parentId="a"))
    )
    assert has(p) is True


def test_a_file_that_grows_past_the_bound_while_read_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size is read first; a writer can grow the file before the read."""

    from types import SimpleNamespace

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    filler = {
        "type": "custom",
        "id": "c",
        "parentId": None,
        "timestamp": _T,
        "customType": "x",
        "data": "y" * 4000,
    }
    p.write_bytes(_line(_HEADER) + _line(filler) * (_CONVERSATION_SCAN_MAX_BYTES // 4000 + 2))
    small = SimpleNamespace(fstat=lambda _fd: SimpleNamespace(st_size=0))
    monkeypatch.setattr(jsonl_repo_module, "os", small)
    assert has(p) is True


# --- Fix round 1 ------------------------------------------------------------


async def test_a_branch_holding_only_a_summary_is_a_conversation(tmp_path: Path) -> None:
    """``navigate_tree`` back to the start with a summary leaves ``model → branch_summary``.

    ``build_session_context`` turns the summary into a user message, so the
    session opens with a turn to continue. On 843b0c14 ``-c`` skipped it for an
    older conversation (hand-built and live, ``.omc/probes/709/verify-r1/``).
    """

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    first = await session.append_model_change("scripted", "s-1")
    await session.append_message(_user("q"))
    await session.append_message(AssistantMessage(content=[TextContent(text="a")]))
    await session.move_to(first, summary={"summary": "We chose blue"})
    branch = [e.type for e in await session.get_branch()]
    assert branch == ["model_change", "branch_summary"]
    roles = [m.role for m in (await session.build_context()).messages]
    assert roles == ["user"]  # the summary, as the model will get it
    path = (await session.get_metadata()).path
    os.utime(path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == path


async def test_a_fork_made_at_a_summary_is_a_conversation(tmp_path: Path) -> None:
    from aelix_agent_core.session import ForkOptions

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    await _conversation(repo, mtime=1000)
    source = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    first = await source.append_model_change("scripted", "s-1")
    await source.append_message(_user("q"))
    summary_id = await source.move_to(first, summary={"summary": "We chose blue"})
    assert summary_id is not None
    source_meta = await source.get_metadata()
    os.utime(source_meta.path, (2000, 2000))
    fork = await repo.fork(source_meta, ForkOptions(cwd=CWD, entry_id=summary_id, position="at"))
    fork_path = (await fork.get_metadata()).path
    os.utime(fork_path, (3000, 3000))
    assert [e.type for e in await fork.get_branch()] == ["model_change", "branch_summary"]

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == fork_path


def test_a_compaction_counts_and_an_empty_summary_does_not(tmp_path: Path) -> None:
    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    compaction = {
        "type": "compaction",
        "id": "c",
        "parentId": None,
        "timestamp": _T,
        "summary": "Remember we chose blue",
        "firstKeptEntryId": "missing",
        "tokensBefore": 100,
    }
    p.write_bytes(_line(_HEADER) + _line(compaction))
    assert has(p) is True
    summary = {
        "type": "branch_summary",
        "id": "b",
        "parentId": None,
        "timestamp": _T,
        "fromId": "root",
        "summary": "We chose blue",
    }
    p.write_bytes(_line(_HEADER) + _line(summary))
    assert has(p) is True
    # ``build_session_context`` adds nothing for an empty summary.
    p.write_bytes(_line(_HEADER) + _line(dict(summary, summary="")))
    assert has(p) is False
    # pi's rule: a custom message is not a conversation, though it reaches the model.
    custom = {
        "type": "custom_message",
        "id": "m",
        "parentId": None,
        "timestamp": _T,
        "customType": "ext-note",
        "content": "a custom message",
        "display": True,
    }
    p.write_bytes(_line(_HEADER) + _line(custom))
    assert has(p) is False


_SUMMARY_RECORDS = {
    "compaction": {
        "type": "compaction",
        "id": "c",
        "parentId": None,
        "timestamp": _T,
        "firstKeptEntryId": "missing",
        "tokensBefore": 100,
    },
    "branch_summary": {
        "type": "branch_summary",
        "id": "b",
        "parentId": None,
        "timestamp": _T,
        "fromId": "root",
    },
}


@pytest.mark.parametrize(
    "text", ["", " ", "  \n\t", "\n"], ids=["empty", "space", "mixed", "newline"]
)
@pytest.mark.parametrize("kind", sorted(_SUMMARY_RECORDS))
def test_a_summary_with_no_text_is_not_a_conversation(tmp_path: Path, kind: str, text: str) -> None:
    """Fix round 2: a summary counts only when its stripped text is not empty.

    Opening sends a compaction's template whatever its summary, and a branch
    summary's when the text is not empty, but with nothing between the
    ``<summary>`` tags there is no earlier conversation in it (on c765dc47 the
    blank compaction and the whitespace branch summary counted).
    """

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    p.write_bytes(_line(_HEADER) + _line(dict(_SUMMARY_RECORDS[kind], summary=text)))
    assert has(p) is False
    p.write_bytes(_line(_HEADER) + _line(dict(_SUMMARY_RECORDS[kind], summary=f"{text}x{text}")))
    assert has(p) is True


@pytest.mark.parametrize("value", [None, 5, True], ids=["null", "number", "bool"])
@pytest.mark.parametrize("kind", sorted(_SUMMARY_RECORDS))
def test_a_summary_that_is_not_text_is_not_a_conversation(
    tmp_path: Path, kind: str, value: object
) -> None:
    """The loader keeps a non-string summary, and building the context from it raises.

    ``build_session_context`` raises ``TypeError`` on each of these but the
    ``null`` branch summary, which it skips, so none is a turn to continue (on
    c765dc47 every compaction and the truthy branch summaries counted).
    """

    from aelix_agent_core.session.context import build_session_context
    from aelix_agent_core.session.jsonl_storage import _parse_jsonl_content

    record = dict(_SUMMARY_RECORDS[kind], summary=value)
    p = tmp_path / "s.jsonl"
    p.write_bytes(_line(_HEADER) + _line(record))
    assert JsonlSessionRepo._has_conversation(p) is False
    entries = _parse_jsonl_content(p.read_text(), str(p)).entries
    assert [e.type for e in entries] == [kind]
    if kind == "branch_summary" and value is None:
        assert build_session_context(entries).messages == []
    else:
        with pytest.raises(TypeError):
            build_session_context(entries)


async def test_a_blank_summary_newer_than_a_conversation_is_skipped(tmp_path: Path) -> None:
    """End to end: ``navigate_tree`` back to the start with a blank summary is no conversation.

    Opening it sends one user message, the template with an empty
    ``<summary>``; ``find_most_recent`` passes over it to the older
    conversation (on c765dc47 it returned the blank one).
    """

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    older = await _conversation(repo, mtime=1000)
    session = await repo.create(JsonlSessionCreateOptions(cwd=CWD))
    first = await session.append_model_change("scripted", "s-1")
    await session.append_message(_user("q"))
    await session.move_to(first, summary={"summary": "  \n"})
    assert [e.type for e in await session.get_branch()] == ["model_change", "branch_summary"]
    assert [m.role for m in (await session.build_context()).messages] == ["user"]
    os.utime((await session.get_metadata()).path, (2000, 2000))

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == older


async def _opened_roles(path: Path) -> list[str]:
    """The roles the loader restores on the branch when ``path`` is opened."""

    from aelix_agent_core.session.fs import LocalFileSystem
    from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage

    storage = await JsonlSessionStorage.open(LocalFileSystem(), str(path))
    leaf = await storage.get_leaf_id()
    entries = await storage.get_path_to_root(leaf) if leaf else []
    return [e.message.role for e in entries if e.type == "message"]  # type: ignore[union-attr]


async def test_line_endings_are_read_as_the_loader_reads_them(tmp_path: Path) -> None:
    """A ``\\r`` ends a line on open (universal newlines); it did not in the 843b0c14 scan.

    Codex's fixture: LF after the header, CR after each record. Opening it
    restores the user and the assistant turn; 843b0c14 skipped the file.
    """

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    model = {
        "type": "model_change",
        "id": "m",
        "parentId": None,
        "timestamp": _T,
        "provider": "x",
        "modelId": "y",
    }
    body = [model, _msg("u", "user", parent="m"), _msg("a", "assistant", parent="u")]
    for sep in (b"\r", b"\r\n", b"\n"):
        p.write_bytes(_line(_HEADER) + b"".join(json.dumps(r).encode() + sep for r in body))
        assert await _opened_roles(p) == ["user", "assistant"], sep
        assert has(p) is True, sep


async def test_a_file_the_loader_cannot_decode_is_not_one_to_continue(tmp_path: Path) -> None:
    """Opening raises on a byte that is not UTF-8, so the scan answers as the loader can."""

    from aelix_agent_core.session.fs import LocalFileSystem
    from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    p.write_bytes(_line(_HEADER) + _line(_msg("u", "user")) + b"\xff\xfe junk\n")
    with pytest.raises(UnicodeDecodeError):
        await JsonlSessionStorage.open(LocalFileSystem(), str(p))
    assert has(p) is False


async def test_a_plain_string_content_answers_as_the_loader_restores_it(tmp_path: Path) -> None:
    """pi accepts ``"content": "hello"``; aelix's loader drops that record (pre-existing).

    Until the loader reads it, ``-c`` must not pick a file that opens with no
    turn: the scan asks the loader's own decoder, so both say no conversation.
    An empty string decodes, so both say yes.
    """

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    plain = _msg("u", "user")
    plain["message"]["content"] = "hello"
    p.write_bytes(_line(_HEADER) + _line(plain))
    assert await _opened_roles(p) == []
    assert has(p) is False
    plain["message"]["content"] = ""
    p.write_bytes(_line(_HEADER) + _line(plain))
    assert await _opened_roles(p) == ["user"]
    assert has(p) is True


async def test_a_large_file_whose_header_the_loader_refuses_falls_through(
    tmp_path: Path,
) -> None:
    """The size counts it unread, the header sniff admits it, the metadata parse refuses it.

    That refusal is the ``except SessionError: continue`` in ``find_most_recent``:
    the next candidate is tried. A file under the bound never reaches it (the
    scan refuses the header first), so this needs one over it.
    """

    repo = JsonlSessionRepo(sessions_root=str(tmp_path))
    talked = await _conversation(repo, mtime=1000)
    newer = Path(talked).with_name("2026-11-09T00-00-00-000Z_v2.jsonl")
    v2 = {"type": "session", "version": 2, "id": "v2", "timestamp": _T, "cwd": CWD}
    filler = _line(
        {"type": "custom", "id": "c", "parentId": None, "timestamp": _T, "data": "y" * 4000}
    )
    newer.write_bytes(_line(v2) + filler * (_CONVERSATION_SCAN_MAX_BYTES // len(filler) + 2))
    os.utime(newer, (2000, 2000))
    assert newer.stat().st_size > _CONVERSATION_SCAN_MAX_BYTES
    assert JsonlSessionRepo._is_valid_session_file(newer) is True
    assert JsonlSessionRepo._has_conversation(newer) is True

    found = await repo.find_most_recent(CWD)
    assert found is not None
    assert found.path == talked


def test_a_prompt_withdrawn_before_413_is_not_a_conversation(tmp_path: Path) -> None:
    """The shape a withdrawal wrote before #413: the prompt with no parent, the leaf at null."""

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    leaf = {"type": "leaf", "id": "l", "parentId": "u", "timestamp": _T, "targetId": None}
    p.write_bytes(_line(_HEADER) + _line(_msg("u", "user")) + _line(leaf))
    assert has(p) is False
    p.write_bytes(_line(_HEADER) + _line(_msg("u", "user")))
    assert has(p) is True  # the same prompt, not withdrawn


async def test_a_duplicate_id_resolves_as_the_loader_resolves_it(tmp_path: Path) -> None:
    """Two records with one id: opening keeps the later one, and so does the scan."""

    has = JsonlSessionRepo._has_conversation
    p = tmp_path / "s.jsonl"
    later = {"type": "custom", "id": "x", "parentId": None, "timestamp": _T, "customType": "c"}
    p.write_bytes(_line(_HEADER) + _line(_msg("x", "user")) + _line(later))
    assert await _opened_roles(p) == []
    assert has(p) is False
    p.write_bytes(_line(_HEADER) + _line(later) + _line(_msg("x", "user")))
    assert await _opened_roles(p) == ["user"]
    assert has(p) is True
