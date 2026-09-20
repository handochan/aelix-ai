"""Pi-parity ``repo_utils`` (Sprint 4b / Phase 2.2.2 — ADR-0022 §E).

Pi source: ``packages/agent/src/harness/session/repo-utils.ts:27-45``. Sprint
4a deferred ``getEntriesToFork`` because :meth:`JsonlSessionRepo.fork`
hadn't landed; Sprint 4b adds both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal, TypeAlias, final

from aelix_agent_core.session.entries import SessionTreeEntry
from aelix_agent_core.session.storage import (
    SessionError,
    SessionStorage,
)

ForkPosition = Literal["before", "at"]


@final
class _ForkFromRoot:
    """The type of :data:`FORK_FROM_ROOT`. Not instantiated anywhere else."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "FORK_FROM_ROOT"


#: "Fork at the root": the new session inherits the parent link and **no
#: entries**. Distinct from ``entry_id=None``, which means "copy the whole
#: source session" — the two collided on one ``None`` and the collision was
#: #300: forking before the first user message copied everything, because
#: that message's ``parent_id`` is ``None``.
FORK_FROM_ROOT: Final = _ForkFromRoot()

#: What :attr:`ForkOptions.entry_id` accepts: an entry id, the
#: :data:`FORK_FROM_ROOT` sentinel, or ``None`` for a whole-session copy.
ForkEntryId: TypeAlias = str | _ForkFromRoot | None


def fork_at_leaf(leaf_id: str | None) -> ForkEntryId:
    """Turn a leaf id into a fork target, with ``None`` meaning *the root*.

    A leaf pointer is ``None`` whenever the branch is empty — a session with
    no entries, one rewound to the root by :meth:`Session.move_to`, or the
    parent of a file's first entry. Handing that ``None`` straight to
    :attr:`ForkOptions.entry_id` asks for a **whole-session copy** instead of
    the empty branch the leaf describes, which is #300. Every caller that has
    a leaf id and wants "fork here" goes through this instead::

        ForkOptions(cwd=cwd, entry_id=fork_at_leaf(leaf_id), position="at")

    Callers that genuinely want the whole session pass ``entry_id=None``
    directly and say so.
    """

    return leaf_id or FORK_FROM_ROOT


@dataclass(frozen=True)
class ForkOptions:
    """Pi ``getEntriesToFork`` options (``repo-utils.ts:33-34``).

    ``cwd`` mirrors :class:`JsonlSessionCreateOptions.cwd` so callers can
    point a fork at a different working directory than the source session.
    ``entry_id=None`` means "fork the entire source session" (full copy);
    ``entry_id=FORK_FROM_ROOT`` means the opposite extreme, an empty branch;
    ``entry_id`` provided + ``position="before"`` mirrors the Pi user-message
    fork ergonomic.
    """

    cwd: str
    entry_id: ForkEntryId = None
    position: ForkPosition = "before"
    parent_session_path: str | None = None
    id: str | None = None


async def get_entries_to_fork(
    storage: SessionStorage[Any],
    entry_id: ForkEntryId,
    position: ForkPosition,
) -> list[SessionTreeEntry]:
    """Pi ``getEntriesToFork`` (``repo-utils.ts:32-50``).

    - ``entry_id=FORK_FROM_ROOT`` → no entries: the fork starts empty.
    - ``entry_id=None`` → full copy of source entries.
    - ``position="at"`` → return path-to-root ending at ``entry_id``.
    - ``position="before"`` → require ``entry_id`` to point at a user
      ``MessageEntry``; return path-to-root ending at its parent (so the
      forked session resumes RIGHT BEFORE the user message).

    Raises :class:`SessionError("invalid_fork_target")` when ``entry_id`` is
    missing OR when ``position="before"`` is used with a non-user-message
    target.

    The sentinel is checked first and on purpose (#300). "The branch ends
    here, and here is the root" and "copy the whole file" are different
    answers; before the sentinel existed both were spelled ``None`` and the
    whole-copy branch won, so a fork taken before a session's first user
    message reproduced the session instead of starting a fresh one. Pi's
    current tree separates the same two meanings on a different axis —
    ``scope: "branch"`` vs ``scope: "tree"`` (``session/fork-policy.ts``) —
    and its conformance suite pins the same outcome we do
    (``testing/conformance/session-repo.ts``, case ``before-root``: forking
    before the root entry yields a null tip and zero entries).
    """

    if isinstance(entry_id, _ForkFromRoot):
        return []
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
    "FORK_FROM_ROOT",
    "ForkEntryId",
    "ForkOptions",
    "ForkPosition",
    "fork_at_leaf",
    "get_entries_to_fork",
]
