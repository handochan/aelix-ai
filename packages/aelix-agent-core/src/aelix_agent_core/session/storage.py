"""``SessionStorage`` Protocol + ``SessionError`` taxonomy (Sprint 4a).

Pi source: ``packages/agent/src/harness/types.ts:438-452`` (10 methods,
generic over ``TMetadata``). Pi ``SessionError`` (``types.ts:190-209``) is a
6-code error class. Aelix adds four methods that choose the parent and
write in one step (#721, ADR-0242 rule 5), and two codes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from aelix_agent_core.session.entries import SessionTreeEntry

# Pi 6-code taxonomy (``types.ts:190-196``), plus one Aelix-only code.
#
# ``read_only`` is the widening ADR-0244 owns (#137). Pi has no equivalent:
# pi's own cross-process ownership work refuses the second opener outright,
# while Aelix asks, and "open it without writing" is one of the three answers.
# The refusal a :class:`ReadOnlySessionStorage` raises therefore needs a code a
# caller can branch on rather than a ``storage`` it would have to string-match.
# ADR-0035's rule is that a Literal widening is recorded by an owning ADR.
#
# ``outcome_unknown`` is the second, and ADR-0242 rule 5 owns it (#721). A
# write that raised after its ``append_file`` call began may or may not have
# reached the file, and a caller has to be able to tell that from
# ``storage``, which now means nothing was written and the write can be
# retried as it stands.
SessionErrorCode = Literal[
    "not_found",
    "invalid_session",
    "invalid_entry",
    "invalid_fork_target",
    "storage",
    "unknown",
    "read_only",
    "outcome_unknown",
]


class SessionError(Exception):
    """Errors raised by session storage / repo / tree operations.

    Pi `SessionError` (``types.ts:198-209``). The ``code`` discriminator is
    the Pi-verified 6-code taxonomy; ``cause`` is kept as a chained
    exception via ``raise ... from`` rather than a field, matching idiomatic
    Python error wrapping.
    """

    def __init__(
        self,
        code: SessionErrorCode,
        message: str,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        if cause is not None:
            self.__cause__ = cause


@dataclass(frozen=True)
class SessionMetadata:
    """Pi ``SessionMetadata`` (``types.ts:427-430``).

    Concrete subclasses (``JsonlSessionMetadata``) widen this with backend
    specifics like ``cwd`` and on-disk ``path``.
    """

    id: str
    created_at: str


@dataclass(frozen=True)
class JsonlSessionMetadata(SessionMetadata):
    """Pi ``JsonlSessionMetadata`` (``types.ts:432-436``)."""

    cwd: str = ""
    path: str = ""
    parent_session_path: str | None = None


class PlacementConflict(SessionError):
    """:meth:`SessionStorage.place_entry` found its id holding a different payload.

    Code ``invalid_entry``: retrying cannot help, because the id is taken. The
    entry already there is kept and nothing is written (#721).
    """

    def __init__(self, entry_id: str) -> None:
        super().__init__(
            "invalid_entry",
            f"Entry {entry_id} already exists with a different payload",
        )
        self.entry_id = entry_id


@dataclass(frozen=True)
class Placement:
    """What :meth:`SessionStorage.place_entry` did with its id.

    ``adopted`` is ``True`` when the id was already in the store holding the
    same payload, so nothing was written and ``entry`` is the stored one.
    """

    entry: SessionTreeEntry
    adopted: bool


TMetadata = TypeVar("TMetadata", bound=SessionMetadata)


@runtime_checkable
class SessionStorage(Protocol[TMetadata]):
    """Pi ``SessionStorage<TMetadata>`` (``types.ts:438-452``), plus four.

    The signatures below are the shape; the contract is executable. Every
    backend must pass ``tests/session/test_storage_conformance.py``
    unchanged — a new backend is added by registering its factory there —
    and ADR-0242 is its prose: what one write is, why every prefix of the
    log must be a session, and how a new kind of record is added.

    The last four are Aelix's (#721, ADR-0242 rule 5). :meth:`append_to_leaf`
    builds the entry from the id and parent it is handed and appends it, as
    one step under the store's own ordering, so the parent is chosen only
    after a write whose outcome was unknown has been settled. ``Session``
    writes every ``append_*`` through it. :meth:`place_entry` writes an entry
    under an id the caller chose, once: the same payload again is adopted, a
    different one is :class:`PlacementConflict`, and what it stores is a copy
    of what it wrote, never the caller's object. :meth:`withdraw_tail` is
    ``Session.withdraw_tail``'s leaf move and re-appends as one step, finished
    by the next write when one of its writes raised. :meth:`resolve_pending`
    settles what an earlier write left open — an unknown outcome, the rest of
    an interrupted :meth:`withdraw_tail` — for an operation that names its
    parent itself (``Session.move_to``) and for a reader of the leaf.
    """

    async def get_metadata(self) -> TMetadata: ...
    async def get_leaf_id(self) -> str | None: ...
    async def set_leaf_id(self, leaf_id: str | None) -> None: ...
    async def create_entry_id(self) -> str: ...
    async def append_entry(self, entry: SessionTreeEntry) -> None: ...
    async def get_entry(self, id: str) -> SessionTreeEntry | None: ...
    async def find_entries(self, type: str) -> list[SessionTreeEntry]: ...
    async def get_label(self, id: str) -> str | None: ...
    async def get_path_to_root(
        self, leaf_id: str | None
    ) -> list[SessionTreeEntry]: ...
    async def get_entries(self) -> list[SessionTreeEntry]: ...
    async def append_to_leaf(
        self, build: Callable[[str, str | None], SessionTreeEntry]
    ) -> SessionTreeEntry: ...
    async def place_entry(
        self,
        entry_id: str,
        build: Callable[[str | None], SessionTreeEntry],
        *,
        same: Callable[[SessionTreeEntry], bool],
    ) -> Placement: ...
    async def withdraw_tail(
        self,
        target_leaf: str | None,
        is_withdrawn: Callable[[SessionTreeEntry], bool],
    ) -> None: ...
    async def resolve_pending(self) -> None: ...


# Re-exported for callers that want to reference Any-bound generic.
SessionStorageAny = SessionStorage[Any]


__all__ = [
    "JsonlSessionMetadata",
    "Placement",
    "PlacementConflict",
    "SessionError",
    "SessionErrorCode",
    "SessionMetadata",
    "SessionStorage",
    "SessionStorageAny",
    "TMetadata",
]
