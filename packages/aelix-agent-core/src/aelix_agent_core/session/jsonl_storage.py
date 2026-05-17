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
) -> tuple[_SessionHeader, list[SessionTreeEntry], str | None]:
    """Pi ``loadJsonlStorage`` (``jsonl-storage.ts:136-159``)."""

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
    entries: list[SessionTreeEntry] = []
    leaf_id: str | None = None
    for i in range(1, len(lines)):
        entry = _parse_entry_line(lines[i], file_path, i + 1)
        entries.append(entry)
        leaf_id = _leaf_id_after_entry(entry)
    return header, entries, leaf_id


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
    ) -> None:
        self._fs = fs
        self._file_path = file_path
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

    @classmethod
    async def open(
        cls, fs: FileSystem, file_path: str
    ) -> JsonlSessionStorage:
        header, entries, leaf_id = await _load_jsonl_storage(fs, file_path)
        return cls(fs, file_path, header, entries, leaf_id)

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
            try:
                await self._fs.append_file(
                    self._file_path, json.dumps(entry_to_json(entry)) + "\n"
                )
            except OSError as exc:
                raise SessionError(
                    "storage",
                    f"Failed to append session leaf {entry.id}: {exc}",
                    cause=exc,
                ) from exc
            self._entries.append(entry)
            self._by_id[entry.id] = entry
            self._current_leaf_id = leaf_id

    async def create_entry_id(self) -> str:
        return _generate_entry_id(self._by_id)

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        async with self._lock:
            try:
                await self._fs.append_file(
                    self._file_path, json.dumps(entry_to_json(entry)) + "\n"
                )
            except OSError as exc:
                raise SessionError(
                    "storage",
                    f"Failed to append session entry {entry.id}: {exc}",
                    cause=exc,
                ) from exc
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


__all__ = ["JsonlSessionStorage", "load_jsonl_session_metadata"]
