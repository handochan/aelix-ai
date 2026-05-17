"""``SessionStorage`` Protocol + ``SessionError`` taxonomy (Sprint 4a).

Pi source: ``packages/agent/src/harness/types.ts:438-452`` (10 methods,
generic over ``TMetadata``). Pi ``SessionError`` (``types.ts:190-209``) is a
6-code error class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from aelix_agent_core.session.entries import SessionTreeEntry

# Pi 6-code taxonomy (``types.ts:190-196``).
SessionErrorCode = Literal[
    "not_found",
    "invalid_session",
    "invalid_entry",
    "invalid_fork_target",
    "storage",
    "unknown",
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


TMetadata = TypeVar("TMetadata", bound=SessionMetadata)


@runtime_checkable
class SessionStorage(Protocol[TMetadata]):
    """Pi ``SessionStorage<TMetadata>`` (``types.ts:438-452``). 10 methods."""

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


# Re-exported for callers that want to reference Any-bound generic.
SessionStorageAny = SessionStorage[Any]


__all__ = [
    "JsonlSessionMetadata",
    "SessionError",
    "SessionErrorCode",
    "SessionMetadata",
    "SessionStorage",
    "SessionStorageAny",
    "TMetadata",
]
