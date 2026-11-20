"""The stored message projection over native JSONL entry ancestry."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aelix_agent_core.session.entries import (
    LeafEntry,
    MessageEntry,
    SessionTreeEntry,
    entry_to_json,
)
from aelix_agent_core.session.storage import SessionError

ROLES = frozenset({"user", "assistant", "toolResult"})
UNAVAILABLE = "unencodable"


def is_transcript_message(entry: SessionTreeEntry) -> bool:
    return isinstance(entry, MessageEntry) and entry.message.role in ROLES


class EntryOrder:
    """Inspect decoded identities before pruning or collapsing the tree."""

    def __init__(self, entries: Sequence[SessionTreeEntry]) -> None:
        self.entries = entries
        self.positions: dict[str, int] = {}
        for position, entry in enumerate(entries):
            if entry.id in self.positions:
                raise SessionError("invalidTree", "Duplicate session entry ID")
            self.positions[entry.id] = position

    def path(self, tip: str | None, *, allow_missing: bool = False) -> list[SessionTreeEntry]:
        position = len(self.entries)
        path: list[SessionTreeEntry] = []
        while tip is not None:
            parent = self.positions.get(tip)
            if parent is None:
                if allow_missing:
                    break
                if not path:
                    raise SessionError("not_found", "Transcript leaf not found")
                raise SessionError("invalidTree", "Missing session entry parent")
            if parent >= position:
                raise SessionError("invalidTree", "Session ancestry is out of file order")
            position = parent
            entry = self.entries[position]
            if (
                not allow_missing
                and isinstance(entry, LeafEntry)
                and entry.target_id is not None
                and entry.target_id not in self.positions
            ):
                raise SessionError("invalidTree", "Missing session leaf target")
            path.append(entry)
            tip = entry.parent_id
        path.reverse()
        return path


@dataclass(frozen=True)
class TranscriptSnapshot:
    native_session_id: str
    leaf_id: str | None
    recorded_leaf_id: str | None
    decoded: EntryOrder
    retained_ids: frozenset[str]
    skipped_lines: tuple[int, ...]
    pruned_ids: tuple[str, ...]
    unterminated_tail: bool

    @property
    def recovered(self) -> bool:
        return bool(self.skipped_lines or self.pruned_ids)

    def active_leaf(self) -> str | None:
        if self.recorded_leaf_id is not None and self.recorded_leaf_id not in self.decoded.positions:
            if not self.recovered:
                raise SessionError("invalidTree", "Missing session leaf target")
        elif self.recorded_leaf_id != self.leaf_id:
            self.decoded.path(self.recorded_leaf_id, allow_missing=True)
        return self.leaf_id

    def messages(self, leaf_id: str | None) -> list[MessageEntry]:
        path = self.decoded.path(leaf_id, allow_missing=self.recovered)
        if leaf_id is not None and leaf_id not in self.retained_ids:
            raise SessionError("not_found", "Transcript leaf not found")
        return [
            entry
            for entry in path
            if isinstance(entry, MessageEntry) and is_transcript_message(entry)
        ]


@dataclass(frozen=True)
class StoredMessage:
    entry: MessageEntry
    sequence: int

    def coordinates(self) -> dict[str, Any]:
        return {
            "entryId": self.entry.id,
            "parentEntryId": self.entry.parent_id,
            "sequence": self.sequence,
        }

    def encode(self) -> tuple[dict[str, Any], bytes | None]:
        """Return a full row and its canonical payload, or a single placeholder."""

        coordinates = self.coordinates()
        try:
            message = entry_to_json(self.entry)["message"]
            payload = json.dumps(
                message, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            return {
                **coordinates,
                "role": self.entry.message.role,
                "unavailable": UNAVAILABLE,
            }, None
        return {**coordinates, "message": message}, payload


@dataclass(frozen=True)
class MessageAppend:
    entry_id: str
    stored: StoredMessage | None


class MessageAppendError(Exception):
    """The outcome of this occurrence, independent of failures in earlier writes."""

    def __init__(self, *, uncertain: bool) -> None:
        super().__init__(
            "Message append outcome is unknown" if uncertain else "Message was not stored"
        )
        self.uncertain = uncertain
