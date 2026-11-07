"""``ReadOnlySessionStorage`` — the belt behind "Open read-only" (#137, ADR-0244).

The second terminal that chooses to view a session it does not own holds **no**
lock (a reader/writer lock would exclude the legitimate owner, which is the one
situation this option exists for). Nothing but this wrapper then stands between
that terminal and the exact append that #137 loses. Without it "read-only" is a
promise the UI makes and no code keeps.

Fourteen methods in the :class:`SessionStorage` Protocol; five of them write:
``append_entry``, ``set_leaf_id``, ``append_to_leaf``, ``place_entry`` and
``withdraw_tail`` (#721). Those raise ``SessionError("read_only", …)``.
``create_entry_id`` is **not** one of them — it is a pure function over the id set
(``jsonl_storage.py``) that touches no file and no in-memory state, so refusing
it would only move the error to a less informative place. ``resolve_pending``
is not one either: it reads the file back and writes only what an interrupted
``withdraw_tail`` owes, and a store this wrapper holds has written nothing, so
it has nothing pending and owes nothing. The other nine delegate.

Reading stays correct while the owner keeps appending underneath: every append
is one ``O_APPEND`` line (ADR-0242 §2), every line on disk parses, and the load
already skips a torn tail (ADR-0208). The viewer sees the session as of the
moment it opened.

**Why the two private passthroughs.** ``Session.session_file``
(``session/session.py:139-144``), ``AgentSessionRuntime.cwd``
(``runtime/agent_session_runtime.py:382-384``) and
``AgentHarness.session_id``/``session_file`` (``harness/core.py:798,1276``) all
read ``_metadata`` / ``_file_path`` off the storage object directly rather than
through the Protocol, because ``get_metadata`` is async and those are
synchronous properties. A wrapper that implemented only the Protocol would make
every one of them ``None`` — which silently unsets ``parent_path`` on a
delegated child record and drops the previous path from the ``session_start`` /
``session_shutdown`` hook payloads. They are read-only reaches, so forwarding
them is not widening the write surface.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aelix_agent_core.session.entries import SessionTreeEntry
from aelix_agent_core.session.storage import Placement, SessionError, SessionStorage

#: What the user is told, once, wherever the error surfaces. Named so the TUI
#: notice and the error text cannot drift apart.
READ_ONLY_MESSAGE = (
    "this session is open read-only because another terminal owns it"
)


class ReadOnlySessionStorage(SessionStorage[Any]):
    """A :class:`SessionStorage` that answers every read and refuses every write."""

    __slots__ = ("_inner",)

    def __init__(self, inner: SessionStorage[Any]) -> None:
        self._inner = inner

    @property
    def inner(self) -> SessionStorage[Any]:
        """The wrapped storage. For a caller that has just re-taken the lock."""

        return self._inner

    # === Synchronous private passthroughs — see the module docstring ==========

    @property
    def _metadata(self) -> Any:
        return getattr(self._inner, "_metadata", None)

    @property
    def _file_path(self) -> Any:
        return getattr(self._inner, "_file_path", None)

    # === Reads (9 of the 14; ``create_entry_id`` is pure) =====================

    async def get_metadata(self) -> Any:
        return await self._inner.get_metadata()

    async def get_leaf_id(self) -> str | None:
        return await self._inner.get_leaf_id()

    async def create_entry_id(self) -> str:
        return await self._inner.create_entry_id()

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        return await self._inner.get_entry(id)

    async def find_entries(self, type: str) -> list[SessionTreeEntry]:
        return await self._inner.find_entries(type)

    async def get_label(self, id: str) -> str | None:
        return await self._inner.get_label(id)

    async def get_path_to_root(
        self, leaf_id: str | None
    ) -> list[SessionTreeEntry]:
        return await self._inner.get_path_to_root(leaf_id)

    async def get_entries(self) -> list[SessionTreeEntry]:
        return await self._inner.get_entries()

    async def resolve_pending(self) -> None:
        await self._inner.resolve_pending()

    # === The five writes ======================================================

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        raise SessionError("read_only", READ_ONLY_MESSAGE)

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        raise SessionError("read_only", READ_ONLY_MESSAGE)

    async def append_to_leaf(
        self, build: Callable[[str, str | None], SessionTreeEntry]
    ) -> SessionTreeEntry:
        raise SessionError("read_only", READ_ONLY_MESSAGE)

    async def place_entry(
        self,
        entry_id: str,
        build: Callable[[str | None], SessionTreeEntry],
        *,
        same: Callable[[SessionTreeEntry], bool],
    ) -> Placement:
        raise SessionError("read_only", READ_ONLY_MESSAGE)

    async def withdraw_tail(
        self,
        target_leaf: str | None,
        is_withdrawn: Callable[[SessionTreeEntry], bool],
    ) -> None:
        raise SessionError("read_only", READ_ONLY_MESSAGE)


__all__ = ["READ_ONLY_MESSAGE", "ReadOnlySessionStorage"]
