"""``JsonlSessionStorage`` (Sprint 4a / Phase 2.2.1).

Pi source: ``packages/agent/src/harness/session/jsonl-storage.ts`` —
header version 3 + leaf-tracking + label cache. Aelix port keeps the
on-disk format **identical** (header v3 with camelCase keys) so a Pi
session file round-trips through Aelix and back.

Aelix-additive (per ADR-0022): per-instance ``asyncio.Lock`` around
appends. Pi has no lock; the Aelix lock is a strict superset safety net
that lets concurrent ``append_*`` calls serialize cleanly under asyncio.
The underlying POSIX ``O_APPEND`` write still provides byte-level atomicity
for writes ≤ PIPE_BUF.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aelix_agent_core.session.entries import (
    LeafEntry,
    SessionTreeEntry,
    entry_from_json,
    entry_to_json,
)
from aelix_agent_core.session.fs import FileSystem
from aelix_agent_core.session.storage import (
    JsonlSessionMetadata,
    SessionError,
    SessionStorage,
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRecovery:
    """What was dropped to make a damaged session file loadable.

    Emitted by :func:`_load_jsonl_storage` when a session JSONL contains
    lines it could not parse — most often a single crash-truncated final
    line, because the process died mid-append. ``None`` (not an empty
    instance) is returned for clean files, so ``if recovery is not None``
    is the "this file was damaged" test.
    """

    file_path: str
    skipped_lines: tuple[int, ...]
    orphaned_entries: tuple[str, ...]
    #: The file does not end in a newline, i.e. the last write was cut
    #: off mid-line. The next append has to open a fresh line or it
    #: would be glued onto the fragment and lost with it.
    unterminated_tail: bool = False

    def describe(self) -> str:
        lines = ", ".join(str(n) for n in self.skipped_lines)
        msg = (
            f"Recovered damaged session file {self.file_path}: skipped "
            f"{len(self.skipped_lines)} unparseable line(s) [{lines}]"
        )
        if self.orphaned_entries:
            msg += (
                f" and dropped {len(self.orphaned_entries)} entry/entries "
                f"orphaned by the gap"
            )
        return msg


@dataclass(frozen=True)
class _LoadedSession:
    """Everything :func:`_load_jsonl_storage` learned about a file.

    ``ends_with_newline`` is deliberately independent of ``recovery``: a
    file can be perfectly valid and still lack its final newline, and
    that alone decides whether the next append needs a healing newline.
    """

    header: _SessionHeader
    entries: list[SessionTreeEntry]
    leaf_id: str | None
    recovery: SessionRecovery | None
    ends_with_newline: bool


def _iso_now() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _generate_entry_id(by_id: dict[str, SessionTreeEntry]) -> str:
    """Pi ``generateEntryId`` (``jsonl-storage.ts:35-41``)."""

    for _ in range(100):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in by_id:
            return candidate
    return uuid.uuid4().hex


def _leaf_id_after_entry(entry: SessionTreeEntry) -> str | None:
    """Pi ``leafIdAfterEntry`` (``jsonl-storage.ts:109-111``)."""

    if entry.type == "leaf":
        return entry.target_id  # type: ignore[union-attr]
    return entry.id


def _update_label_cache(
    labels_by_id: dict[str, str], entry: SessionTreeEntry
) -> None:
    """Pi ``updateLabelCache`` (``jsonl-storage.ts:17-25``)."""

    if entry.type != "label":
        return
    raw = entry.label  # type: ignore[union-attr]
    stripped = raw.strip() if raw else None
    if stripped:
        labels_by_id[entry.target_id] = stripped  # type: ignore[union-attr]
    else:
        labels_by_id.pop(entry.target_id, None)  # type: ignore[union-attr]


@dataclass(frozen=True)
class _SessionHeader:
    """Pi ``SessionHeader`` (``jsonl-storage.ts:8-15``)."""

    id: str
    timestamp: str
    cwd: str
    parent_session: str | None = None
    version: int = 3


def _header_to_metadata(
    header: _SessionHeader, path: str
) -> JsonlSessionMetadata:
    return JsonlSessionMetadata(
        id=header.id,
        created_at=header.timestamp,
        cwd=header.cwd,
        path=path,
        parent_session_path=header.parent_session,
    )


def _parse_header_line(line: str, file_path: str) -> _SessionHeader:
    """Pi ``parseHeaderLine`` (``jsonl-storage.ts:59-85``)."""

    try:
        parsed = json.loads(line)
    except Exception as exc:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: first line is not a "
            "valid session header",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc
    if not isinstance(parsed, dict):
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: first line is not a "
            "valid session header",
        )
    if parsed.get("type") != "session":
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: first line is not a "
            "valid session header",
        )
    if parsed.get("version") != 3:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: unsupported session "
            "version",
        )
    sid = parsed.get("id")
    if not isinstance(sid, str) or not sid:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header is "
            "missing id",
        )
    ts = parsed.get("timestamp")
    if not isinstance(ts, str) or not ts:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header is "
            "missing timestamp",
        )
    cwd = parsed.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header is "
            "missing cwd",
        )
    parent = parsed.get("parentSession")
    if parent is not None and not isinstance(parent, str):
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: session header "
            "parentSession must be a string",
        )
    return _SessionHeader(
        id=sid, timestamp=ts, cwd=cwd, parent_session=parent, version=3
    )


def _parse_entry_line(
    line: str, file_path: str, line_number: int
) -> SessionTreeEntry:
    """Pi ``parseEntryLine`` (``jsonl-storage.ts:87-107``)."""

    try:
        parsed = json.loads(line)
    except Exception as exc:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is not valid JSON",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc
    if not isinstance(parsed, dict):
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is not a valid session entry",
        )
    if not isinstance(parsed.get("type"), str):
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is missing entry type",
        )
    if not isinstance(parsed.get("id"), str) or not parsed["id"]:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is missing entry id",
        )
    parent_id = parsed.get("parentId")
    if parent_id is not None and not isinstance(parent_id, str):
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "has invalid parentId",
        )
    if not isinstance(parsed.get("timestamp"), str) or not parsed["timestamp"]:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            "is missing timestamp",
        )
    if parsed["type"] == "leaf":
        target = parsed.get("targetId")
        if target is not None and not isinstance(target, str):
            raise SessionError(
                "invalid_entry",
                f"Invalid JSONL session file {file_path}: line "
                f"{line_number} has invalid targetId",
            )
    try:
        return entry_from_json(parsed)
    except Exception as exc:
        raise SessionError(
            "invalid_entry",
            f"Invalid JSONL session file {file_path}: line {line_number} "
            f"failed to decode: {exc}",
            cause=exc if isinstance(exc, Exception) else None,
        ) from exc


async def load_jsonl_session_metadata(
    fs: FileSystem, file_path: str
) -> JsonlSessionMetadata:
    """Pi ``loadJsonlSessionMetadata`` (``jsonl-storage.ts:123-134``)."""

    try:
        lines = await fs.read_text_lines(file_path, max_lines=1)
    except OSError as exc:
        raise SessionError(
            "storage",
            f"Failed to read session header {file_path}: {exc}",
            cause=exc,
        ) from exc
    if not lines or not lines[0].strip():
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: missing session header",
        )
    header = _parse_header_line(lines[0], file_path)
    return _header_to_metadata(header, file_path)


async def _load_jsonl_storage(
    fs: FileSystem, file_path: str
) -> _LoadedSession:
    """Pi ``loadJsonlStorage`` (``jsonl-storage.ts:136-159``).

    Aelix-additive divergence (Track S1): entry lines that fail to parse
    are **skipped**, not fatal. Pi (and Aelix before this change) raised
    on the first bad line, which bricked the file permanently — a crash
    mid-append truncates the LAST line, and since ``find_most_recent``
    only validates the HEADER it re-selected the same dead file on every
    subsequent ``--continue``, with no way out but deleting it by hand.

    The header check stays strict: a file whose first line is not a v3
    session header is genuinely "not a session file" and still raises
    ``invalid_session``. Only the entry body is recovered.

    Skipping an INTERIOR line can orphan later entries whose ``parentId``
    referred to the dropped one; those would blow up in
    :meth:`JsonlSessionStorage.get_path_to_root`. So when anything was
    skipped, entries with an unresolvable parent are pruned too
    (transitively), and the leaf pointer is rewound to the newest
    surviving entry. Clean files take none of this: they short-circuit
    to the original behaviour with ``recovery=None``.

    Recovery is non-destructive — the damaged bytes stay on disk and are
    re-skipped on every load. TODO (Track S, follow-up): an explicit
    ``aelix session repair <file>`` that rewrites the file without them.
    It is deferred because the CLI has no subcommand group yet, so it is
    a new surface rather than a fix; recover-on-load is what actually
    unbricks ``--continue``, and it lands here.
    """

    try:
        content = await fs.read_text_file(file_path)
    except OSError as exc:
        raise SessionError(
            "storage",
            f"Failed to read session {file_path}: {exc}",
            cause=exc,
        ) from exc
    lines = [line for line in content.split("\n") if line.strip()]
    if not lines:
        raise SessionError(
            "invalid_session",
            f"Invalid JSONL session file {file_path}: missing session header",
        )
    header = _parse_header_line(lines[0], file_path)
    # Whether the last write finished its line. Computed from the BYTES,
    # not from whether anything was skipped: a file can be entirely valid
    # and still be missing its final newline (a pi export, a hand-edit, or
    # `import_from_jsonl`, which copies foreign bytes verbatim). Appending
    # to such a file without healing it first fuses the new entry onto the
    # last one, and the NEXT load then drops the fused line — destroying a
    # turn that was already durable.
    ends_with_newline = content.endswith("\n")
    entries: list[SessionTreeEntry] = []
    leaf_id: str | None = None
    skipped: list[int] = []
    for i in range(1, len(lines)):
        try:
            entry = _parse_entry_line(lines[i], file_path, i + 1)
        except SessionError:
            skipped.append(i + 1)
            continue
        entries.append(entry)
        leaf_id = _leaf_id_after_entry(entry)
    if not skipped:
        return _LoadedSession(
            header=header,
            entries=entries,
            leaf_id=leaf_id,
            recovery=None,
            ends_with_newline=ends_with_newline,
        )

    # A gap was bridged — re-check that the surviving forest is still
    # internally consistent before handing it to the caller.
    kept: list[SessionTreeEntry] = []
    known: set[str] = set()
    orphaned: list[str] = []
    for entry in entries:
        if entry.parent_id is not None and entry.parent_id not in known:
            orphaned.append(entry.id)
            continue
        kept.append(entry)
        known.add(entry.id)

    # Rewind the leaf to the newest entry whose target still resolves; a
    # `leaf` entry pointing at a pruned target would fail `get_leaf_id`.
    leaf_id = None
    for entry in reversed(kept):
        candidate = _leaf_id_after_entry(entry)
        if candidate is None or candidate in known:
            leaf_id = candidate
            break

    recovery = SessionRecovery(
        file_path=file_path,
        skipped_lines=tuple(skipped),
        orphaned_entries=tuple(orphaned),
        unterminated_tail=not ends_with_newline,
    )
    # No logging is configured by the CLI, so `logging.lastResort` puts
    # this on stderr where the user actually sees it.
    _LOG.warning("%s", recovery.describe())
    return _LoadedSession(
        header=header,
        entries=kept,
        leaf_id=leaf_id,
        recovery=recovery,
        ends_with_newline=ends_with_newline,
    )


class JsonlSessionStorage(SessionStorage[JsonlSessionMetadata]):
    """Pi ``JsonlSessionStorage`` (``jsonl-storage.ts:161-293``).

    Per-instance ``asyncio.Lock`` is the Aelix-additive safety net around
    appends — Pi has no lock. POSIX ``O_APPEND`` atomicity for ≤ PIPE_BUF
    writes still provides the underlying byte-level guarantee.
    """

    def __init__(
        self,
        fs: FileSystem,
        file_path: str,
        header: _SessionHeader,
        entries: list[SessionTreeEntry],
        leaf_id: str | None,
        recovery: SessionRecovery | None = None,
        ends_with_newline: bool = True,
    ) -> None:
        self._fs = fs
        self._file_path = file_path
        #: Set when this file had to be repaired on load (Track S1);
        #: ``None`` for a clean file.
        self.recovery = recovery
        self._metadata = _header_to_metadata(header, file_path)
        self._entries: list[SessionTreeEntry] = list(entries)
        self._by_id: dict[str, SessionTreeEntry] = {
            e.id: e for e in self._entries
        }
        self._labels_by_id: dict[str, str] = {}
        for entry in self._entries:
            _update_label_cache(self._labels_by_id, entry)
        self._current_leaf_id: str | None = leaf_id
        self._lock = asyncio.Lock()
        # A file that ends mid-line will fuse the next append onto its last
        # line, and the load after that drops the fused result — losing the
        # appended entry AND the previously durable one it landed on. Keyed
        # only on the trailing byte, NOT on `recovery`: an unterminated file
        # whose lines all happen to parse is still unterminated, and that
        # case loses already-committed data. Cleared once the healing
        # newline has been written.
        self._needs_newline = not ends_with_newline

    async def _append_line(self, payload: dict[str, Any], what: str) -> None:
        """Append one JSON line, healing an unterminated tail first."""

        line = json.dumps(payload) + "\n"
        if self._needs_newline:
            line = "\n" + line
        try:
            await self._fs.append_file(self._file_path, line)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to append session {what}: {exc}",
                cause=exc,
            ) from exc
        self._needs_newline = False

    @classmethod
    async def open(
        cls, fs: FileSystem, file_path: str
    ) -> JsonlSessionStorage:
        loaded = await _load_jsonl_storage(fs, file_path)
        return cls(
            fs,
            file_path,
            loaded.header,
            loaded.entries,
            loaded.leaf_id,
            loaded.recovery,
            ends_with_newline=loaded.ends_with_newline,
        )

    @classmethod
    async def create(
        cls,
        fs: FileSystem,
        file_path: str,
        *,
        cwd: str,
        session_id: str,
        parent_session_path: str | None = None,
    ) -> JsonlSessionStorage:
        header = _SessionHeader(
            id=session_id,
            timestamp=_iso_now(),
            cwd=cwd,
            parent_session=parent_session_path,
        )
        wire: dict[str, Any] = {
            "type": "session",
            "version": 3,
            "id": header.id,
            "timestamp": header.timestamp,
            "cwd": header.cwd,
        }
        if header.parent_session is not None:
            wire["parentSession"] = header.parent_session
        try:
            await fs.write_file(file_path, json.dumps(wire) + "\n")
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to create session {file_path}: {exc}",
                cause=exc,
            ) from exc
        return cls(fs, file_path, header, [], None)

    async def get_metadata(self) -> JsonlSessionMetadata:
        return self._metadata

    async def get_leaf_id(self) -> str | None:
        if self._current_leaf_id is not None and self._current_leaf_id not in self._by_id:
            raise SessionError(
                "invalid_session",
                f"Entry {self._current_leaf_id} not found",
            )
        return self._current_leaf_id

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        if leaf_id is not None and leaf_id not in self._by_id:
            raise SessionError("not_found", f"Entry {leaf_id} not found")
        entry = LeafEntry(
            id=_generate_entry_id(self._by_id),
            parent_id=self._current_leaf_id,
            timestamp=_iso_now(),
            target_id=leaf_id,
        )
        async with self._lock:
            await self._append_line(entry_to_json(entry), f"leaf {entry.id}")
            self._entries.append(entry)
            self._by_id[entry.id] = entry
            self._current_leaf_id = leaf_id

    async def create_entry_id(self) -> str:
        return _generate_entry_id(self._by_id)

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        async with self._lock:
            await self._append_line(entry_to_json(entry), f"entry {entry.id}")
            self._entries.append(entry)
            self._by_id[entry.id] = entry
            _update_label_cache(self._labels_by_id, entry)
            self._current_leaf_id = _leaf_id_after_entry(entry)

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        return self._by_id.get(id)

    async def find_entries(self, type: str) -> list[SessionTreeEntry]:
        return [e for e in self._entries if e.type == type]

    async def get_label(self, id: str) -> str | None:
        return self._labels_by_id.get(id)

    async def get_path_to_root(
        self, leaf_id: str | None
    ) -> list[SessionTreeEntry]:
        if leaf_id is None:
            return []
        path: list[SessionTreeEntry] = []
        current = self._by_id.get(leaf_id)
        if current is None:
            raise SessionError("not_found", f"Entry {leaf_id} not found")
        while current is not None:
            path.insert(0, current)
            if not current.parent_id:
                break
            parent = self._by_id.get(current.parent_id)
            if parent is None:
                raise SessionError(
                    "invalid_session",
                    f"Entry {current.parent_id} not found",
                )
            current = parent
        return path

    async def get_entries(self) -> list[SessionTreeEntry]:
        return list(self._entries)


__all__ = [
    "JsonlSessionStorage",
    "SessionRecovery",
    "load_jsonl_session_metadata",
]
