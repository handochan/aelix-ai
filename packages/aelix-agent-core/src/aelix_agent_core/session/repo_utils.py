"""Pi-parity ``repo_utils`` (Sprint 4b / Phase 2.2.2 — ADR-0022 §E).

Pi source: ``packages/agent/src/harness/session/repo-utils.ts:27-45``. Sprint
4a deferred ``getEntriesToFork`` because :meth:`JsonlSessionRepo.fork`
hadn't landed; Sprint 4b adds both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from aelix_agent_core.session.entries import SessionTreeEntry
from aelix_agent_core.session.storage import (
    SessionError,
    SessionStorage,
)

ForkPosition = Literal["before", "at"]


@dataclass(frozen=True)
class ForkOptions:
    """Pi ``getEntriesToFork`` options (``repo-utils.ts:33-34``).

    ``cwd`` mirrors :class:`JsonlSessionCreateOptions.cwd` so callers can
    point a fork at a different working directory than the source session.
    ``entry_id=None`` means "fork the entire source session" (full copy);
    ``entry_id`` provided + ``position="before"`` mirrors the Pi user-message
    fork ergonomic.
    """

    cwd: str
    entry_id: str | None = None
    position: ForkPosition = "before"
    parent_session_path: str | None = None
    id: str | None = None


async def get_entries_to_fork(
    storage: SessionStorage[Any],
    entry_id: str | None,
    position: ForkPosition,
) -> list[SessionTreeEntry]:
    """Pi ``getEntriesToFork`` (``repo-utils.ts:32-50``).

    - ``entry_id=None`` → full copy of source entries.
    - ``position="at"`` → return path-to-root ending at ``entry_id``.
    - ``position="before"`` → require ``entry_id`` to point at a user
      ``MessageEntry``; return path-to-root ending at its parent (so the
      forked session resumes RIGHT BEFORE the user message).

    Raises :class:`SessionError("invalid_fork_target")` when ``entry_id`` is
    missing OR when ``position="before"`` is used with a non-user-message
    target.
    """

    if entry_id is None:
        return await storage.get_entries()
    target = await storage.get_entry(entry_id)
    if target is None:
        raise SessionError(
            "invalid_fork_target", f"Entry {entry_id} not found"
        )
    if position == "at":
        effective_leaf_id: str | None = target.id
    else:
        # position == "before"
        if target.type != "message" or target.message.role != "user":  # type: ignore[union-attr]
            raise SessionError(
                "invalid_fork_target",
                f"Entry {entry_id} is not a user message",
            )
        effective_leaf_id = target.parent_id
    return await storage.get_path_to_root(effective_leaf_id)


__all__ = [
    "ForkOptions",
    "ForkPosition",
    "get_entries_to_fork",
]
