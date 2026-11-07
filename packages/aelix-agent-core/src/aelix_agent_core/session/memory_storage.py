"""In-memory ``SessionStorage`` (Pi parity, Sprint 4a).

Pi source: ``packages/agent/src/harness/session/memory-storage.ts:40-131``
(``InMemorySessionStorage``). Aelix exposes the same Protocol surface with
``MemorySessionStorage``.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC

from aelix_agent_core.session.entries import LeafEntry, SessionTreeEntry
from aelix_agent_core.session.storage import (
    Placement,
    PlacementConflict,
    SessionError,
    SessionMetadata,
    SessionStorage,
)


def _generate_entry_id(existing: dict[str, SessionTreeEntry]) -> str:
    """Pi ``generateEntryId`` (``memory-storage.ts:28-34``).

    100 tries of ``uuid4().hex[:8]``; fall back to the full hex if every
    short ID happens to collide.
    """

    for _ in range(100):
        candidate = uuid.uuid4().hex[:8]
        if candidate not in existing:
            return candidate
    return uuid.uuid4().hex


def _leaf_id_after_entry(entry: SessionTreeEntry) -> str | None:
    """Pi ``leafIdAfterEntry`` (``memory-storage.ts:36-38``).

    ``leaf`` entries delegate to their ``target_id``; every other entry IS
    the new leaf.
    """

    if entry.type == "leaf":
        return entry.target_id  # type: ignore[union-attr]
    return entry.id


def _update_label_cache(
    labels_by_id: dict[str, str], entry: SessionTreeEntry
) -> None:
    """Pi ``updateLabelCache`` (``memory-storage.ts:10-18``)."""

    if entry.type != "label":
        return
    label_value = entry.label.strip() if entry.label else None  # type: ignore[union-attr]
    if label_value:
        labels_by_id[entry.target_id] = label_value  # type: ignore[union-attr]
    else:
        labels_by_id.pop(entry.target_id, None)  # type: ignore[union-attr]


class MemorySessionStorage(SessionStorage[SessionMetadata]):
    """Pi ``InMemorySessionStorage`` ported to Python.

    Bound to the base :class:`SessionMetadata`. Callers needing extended
    metadata should pass a subclass via the ``metadata`` kwarg.

    Never has a pending outcome (#721): a write here either changed memory
    or raised before it did, so :meth:`resolve_pending` has nothing to do.
    :meth:`append_to_leaf`, :meth:`place_entry` and :meth:`withdraw_tail`
    write through :meth:`append_entry`, so a subclass that overrides it to fail
    a write fails these too — and a :meth:`withdraw_tail` cut that way is left
    as it was cut: there is no file to settle it from.
    """

    def __init__(
        self,
        *,
        entries: list[SessionTreeEntry] | None = None,
        metadata: SessionMetadata | None = None,
    ) -> None:
        self._entries: list[SessionTreeEntry] = list(entries) if entries else []
        self._by_id: dict[str, SessionTreeEntry] = {
            e.id: e for e in self._entries
        }
        self._labels_by_id: dict[str, str] = {}
        for entry in self._entries:
            _update_label_cache(self._labels_by_id, entry)
        self._leaf_id: str | None = None
        for entry in self._entries:
            self._leaf_id = _leaf_id_after_entry(entry)
        if self._leaf_id is not None and self._leaf_id not in self._by_id:
            raise SessionError(
                "invalid_session", f"Entry {self._leaf_id} not found"
            )
        self._metadata = metadata or SessionMetadata(
            id=str(uuid.uuid4()),
            # ISO 8601 timestamp w/ Z suffix to match Pi `new Date().toISOString()`.
            created_at=_iso_now(),
        )

    async def get_metadata(self) -> SessionMetadata:
        return self._metadata

    async def get_leaf_id(self) -> str | None:
        if self._leaf_id is not None and self._leaf_id not in self._by_id:
            raise SessionError(
                "invalid_session", f"Entry {self._leaf_id} not found"
            )
        return self._leaf_id

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        if leaf_id is not None and leaf_id not in self._by_id:
            raise SessionError("not_found", f"Entry {leaf_id} not found")
        entry = LeafEntry(
            id=_generate_entry_id(self._by_id),
            parent_id=self._leaf_id,
            timestamp=_iso_now(),
            target_id=leaf_id,
        )
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        self._leaf_id = leaf_id

    async def create_entry_id(self) -> str:
        return _generate_entry_id(self._by_id)

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        _update_label_cache(self._labels_by_id, entry)
        self._leaf_id = _leaf_id_after_entry(entry)

    async def append_to_leaf(
        self, build: Callable[[str, str | None], SessionTreeEntry]
    ) -> SessionTreeEntry:
        entry = build(_generate_entry_id(self._by_id), await self.get_leaf_id())
        await self.append_entry(entry)
        return entry

    async def place_entry(
        self,
        entry_id: str,
        build: Callable[[str | None], SessionTreeEntry],
        *,
        same: Callable[[SessionTreeEntry], bool],
    ) -> Placement:
        existing = self._by_id.get(entry_id)
        if existing is not None:
            if same(existing):
                return Placement(existing, adopted=True)
            raise PlacementConflict(entry_id)
        entry = build(await self.get_leaf_id())
        if entry.id != entry_id:
            raise ValueError(f"place_entry built {entry.id!r} for id {entry_id!r}")
        # A copy, as the JSONL store keeps the line it wrote: the caller still
        # holds the payload, and ``same`` must compare against what was placed.
        stored = copy.deepcopy(entry)
        await self.append_entry(stored)
        return Placement(stored, adopted=False)

    async def withdraw_tail(
        self,
        target_leaf: str | None,
        is_withdrawn: Callable[[SessionTreeEntry], bool],
    ) -> None:
        path = await self.get_path_to_root(await self.get_leaf_id())
        if target_leaf is None:
            tail = path
        else:
            ids = [entry.id for entry in path]
            if target_leaf not in ids:
                raise SessionError(
                    "not_found", f"Entry {target_leaf} is not on the current path"
                )
            tail = path[ids.index(target_leaf) + 1 :]
        survivors = [entry for entry in tail if not is_withdrawn(entry)]
        await self.set_leaf_id(target_leaf)
        for entry in survivors:
            await self.append_to_leaf(
                lambda entry_id, parent_id, entry=entry: replace(
                    entry, id=entry_id, parent_id=parent_id
                )
            )

    async def resolve_pending(self) -> None:
        return None

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


def _iso_now() -> str:
    """ISO 8601 timestamp matching Pi ``new Date().toISOString()``."""

    from datetime import datetime

    # Pi format: "YYYY-MM-DDTHH:MM:SS.sssZ"
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


__all__ = ["MemorySessionStorage"]
