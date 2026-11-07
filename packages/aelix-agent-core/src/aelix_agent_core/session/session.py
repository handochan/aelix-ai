"""``Session`` concrete class (Sprint 4a / Phase 2.2.1).

Pi source: ``packages/agent/src/harness/session/session.ts:78-252``. Pi
``Session`` is a **concrete class**, NOT a Protocol (P-13). The Protocol
is :class:`SessionStorage` (pi's 10 methods plus Aelix's four). ``Session``
wraps storage and owns timestamp generation; ID generation and leaf parenting
happen inside the storage's :meth:`~SessionStorage.append_to_leaf` (#721).

Critical: ``append_compaction`` takes **5 params** per P-13.

EVERY ``append_*`` BUILDS ITS ENTRY INSIDE THE STORE (#721, ADR-0242 rule 5).
They used to read the id and the leaf first and hand a finished entry to
``append_entry``. After an append whose line landed and then raised, that
leaf was the one from before the line, so the next entry became its sibling
and the landed line left the current branch, still in the file. The store now
settles that outcome and then calls the builder with the leaf it settled on.
:meth:`move_to` names its parent: it settles first with ``resolve_pending`` and
keeps the parent it names. :meth:`withdraw_tail` is one store operation.

AND THE LEAF IS READ SETTLED. :meth:`get_leaf_id` and :meth:`get_branch` settle
first too, so a reader that takes the tip — ``/clone`` in the TUI, the RPC
``clone`` and the bridge's branch, the harness's Esc-withdraw point — sees a
line that landed and then raised, not the leaf from before it (Codex review of
#721, finding 5).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from aelix_agent_core.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionInfoEntry,
    SessionTreeEntry,
    ThinkingLevelChangeEntry,
)
from aelix_agent_core.session.storage import (
    Placement,
    SessionError,
    SessionStorage,
)
from aelix_agent_core.types import AgentMessage


def _iso_now() -> str:
    """ISO 8601 timestamp with millisecond precision matching Pi.

    Pi uses ``new Date().toISOString()`` which produces a ``...Z`` form.
    """

    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class SessionContext:
    """Pi ``SessionContext`` (``types.ts:421-425``).

    Output of :func:`buildSessionContext`. Carries the messages list, the
    resolved thinking level, and the resolved model.
    """

    __slots__ = ("messages", "thinking_level", "model")

    def __init__(
        self,
        messages: list[AgentMessage],
        thinking_level: str,
        model: dict[str, str] | None,
    ) -> None:
        self.messages = messages
        self.thinking_level = thinking_level
        self.model = model

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return (
            f"SessionContext(messages={len(self.messages)} msgs, "
            f"thinking_level={self.thinking_level!r}, model={self.model!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SessionContext):
            return NotImplemented
        return (
            self.messages == other.messages
            and self.thinking_level == other.thinking_level
            and self.model == other.model
        )


class Session:
    """Pi ``Session<TMetadata>`` concrete class.

    Wraps a :class:`SessionStorage` instance. Owns timestamp + entry-ID
    generation. Caller never touches storage directly outside of fork /
    repo paths (use :meth:`get_storage`).
    """

    def __init__(self, storage: SessionStorage[Any]) -> None:
        self._storage = storage

    async def get_metadata(self) -> Any:
        return await self._storage.get_metadata()

    def get_storage(self) -> SessionStorage[Any]:
        """Pi `getStorage` (``session.ts:89-91``) — synchronous accessor."""

        return self._storage

    @property
    def session_file(self) -> str | None:
        """Pi parity: ``AgentSession.sessionFile`` (sync getter).

        Sprint 6h₅a (Phase 4.14, ADR-0081, P-336). Reads cached
        ``_metadata.path`` from the underlying storage. Returns ``None``
        when:

          - metadata has not been hydrated yet, OR
          - the metadata subclass does not carry a ``path`` attribute
            (non-JSONL storage backends), OR
          - ``path`` is the empty string (treated as unset).

        Mirrors the cached-metadata access pattern used by
        :attr:`AgentSessionRuntime.cwd`
        (``runtime/agent_session_runtime.py:374-384``).
        """

        storage = self._storage
        metadata = getattr(storage, "_metadata", None)
        if metadata is None:
            return None
        path = getattr(metadata, "path", None)
        return path or None

    async def _settle(self) -> None:
        """Settle what an earlier write left open, for a reader of the leaf (#721).

        Best effort: a read that cannot settle — the file cannot be read back,
        an owed withdrawal's write fails — answers from the store as it stands,
        exactly as before, and the next write raises the reason.
        """

        with contextlib.suppress(SessionError):
            await self._storage.resolve_pending()

    async def get_leaf_id(self) -> str | None:
        """The current leaf, after a pending outcome is settled (#721)."""

        await self._settle()
        return await self._storage.get_leaf_id()

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        return await self._storage.get_entry(id)

    async def get_entries(self) -> list[SessionTreeEntry]:
        return await self._storage.get_entries()

    async def get_branch(
        self, from_id: str | None = None
    ) -> list[SessionTreeEntry]:
        """Pi `getBranch` (``session.ts:105-108``).

        Returns the entry path from root to either the explicit ``from_id``
        or the current leaf when ``from_id is None``, after a pending outcome is
        settled (#721).
        """

        await self._settle()
        leaf_id = from_id if from_id is not None else await self._storage.get_leaf_id()
        return await self._storage.get_path_to_root(leaf_id)

    async def build_context(self) -> SessionContext:
        """Pi `buildContext` (``session.ts:110-112``)."""

        from aelix_agent_core.session.context import build_session_context

        return build_session_context(await self.get_branch())

    async def get_label(self, id: str) -> str | None:
        return await self._storage.get_label(id)

    async def get_session_name(self) -> str | None:
        """Pi `getSessionName` (``session.ts:118-121``).

        Returns the trimmed name from the most-recent ``session_info`` entry.
        """

        entries = await self._storage.find_entries("session_info")
        if not entries:
            return None
        last = entries[-1]
        name = last.name  # type: ignore[union-attr]
        if name is None:
            return None
        stripped = name.strip()
        return stripped or None

    async def _append_typed_entry(self, entry: SessionTreeEntry) -> str:
        """Pi private `appendTypedEntry` (``session.ts:123-126``).

        For an entry whose parent is already decided — :meth:`move_to`'s
        branch summary. Everything that parents on the current leaf goes
        through :meth:`_append_to_leaf` instead.
        """

        await self._storage.append_entry(entry)
        return entry.id

    async def _append_to_leaf(
        self, build: Callable[[str, str | None], SessionTreeEntry]
    ) -> str:
        """Append the entry ``build(id, leaf)`` returns, inside the store's hold (#721)."""

        entry = await self._storage.append_to_leaf(build)
        return entry.id

    async def append_message(self, message: AgentMessage) -> str:
        return await self._append_to_leaf(
            lambda entry_id, parent_id: MessageEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                message=message,
            )
        )

    async def append_thinking_level_change(self, thinking_level: str) -> str:
        return await self._append_to_leaf(
            lambda entry_id, parent_id: ThinkingLevelChangeEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                thinking_level=thinking_level,
            )
        )

    async def append_model_change(self, provider: str, model_id: str) -> str:
        return await self._append_to_leaf(
            lambda entry_id, parent_id: ModelChangeEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                provider=provider,
                model_id=model_id,
            )
        )

    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Any | None = None,
        from_hook: bool | None = None,
    ) -> str:
        """Pi `appendCompaction` (``session.ts:159-177``) — 5 params (P-13)."""

        return await self._append_to_leaf(
            lambda entry_id, parent_id: CompactionEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
                details=details,
                from_hook=from_hook,
            )
        )

    async def append_custom_entry(
        self, custom_type: str, data: Any | None = None
    ) -> str:
        return await self._append_to_leaf(
            lambda entry_id, parent_id: CustomEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                custom_type=custom_type,
                data=data,
            )
        )

    async def append_custom_message_entry(
        self,
        custom_type: str,
        content: Any,
        display: bool,
        details: Any | None = None,
    ) -> str:
        return await self._append_to_leaf(
            lambda entry_id, parent_id: CustomMessageEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                custom_type=custom_type,
                content=content,
                display=display,
                details=details,
            )
        )

    async def place_custom_message(
        self,
        entry_id: str,
        custom_type: str,
        content: Any,
        display: bool,
        details: Any | None = None,
        *,
        same: Callable[[SessionTreeEntry], bool] | None = None,
    ) -> Placement:
        """Append a ``custom_message`` under ``entry_id``, at most once (#721).

        Through :meth:`SessionStorage.place_entry`: when ``entry_id`` is
        already stored and ``same`` accepts it, that entry is returned adopted
        and nothing is written; when ``same`` refuses it,
        :class:`~aelix_agent_core.session.storage.PlacementConflict`. So a
        caller whose placement raised ``outcome_unknown`` places it again with
        the same id. ``same`` defaults to "a ``custom_message`` with this
        exact payload".
        """

        def matches(existing: SessionTreeEntry) -> bool:
            return (
                isinstance(existing, CustomMessageEntry)
                and existing.custom_type == custom_type
                and existing.content == content
                and existing.display == display
                and existing.details == details
            )

        return await self._storage.place_entry(
            entry_id,
            lambda parent_id: CustomMessageEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                custom_type=custom_type,
                content=content,
                display=display,
                details=details,
            ),
            same=same if same is not None else matches,
        )

    async def append_label(self, target_id: str, label: str | None) -> str:
        """Pi `appendLabel` (``session.ts:208-220``).

        Raises :class:`SessionError("not_found")` when ``target_id`` does
        not resolve to an existing entry. Checked after a pending outcome is
        settled, so a target whose line landed and then raised is found.
        """

        await self._storage.resolve_pending()
        if await self._storage.get_entry(target_id) is None:
            raise SessionError("not_found", f"Entry {target_id} not found")
        return await self._append_to_leaf(
            lambda entry_id, parent_id: LabelEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                target_id=target_id,
                label=label,
            )
        )

    async def append_session_name(self, name: str) -> str:
        return await self._append_to_leaf(
            lambda entry_id, parent_id: SessionInfoEntry(
                id=entry_id,
                parent_id=parent_id,
                timestamp=_iso_now(),
                name=name.strip(),
            )
        )

    async def move_to(
        self,
        entry_id: str | None,
        summary: dict[str, Any] | None = None,
    ) -> str | None:
        """Pi `moveTo` (``session.ts:232-251``).

        ``summary`` is a dict shape ``{"summary": str, "details": Any?,
        "from_hook": bool?}``. When omitted, no branch_summary entry is
        appended and the return value is ``None``.

        Raises :class:`SessionError("not_found")` when ``entry_id`` is not
        ``None`` and does not resolve. A pending outcome is settled first
        (#721), and ``entry_id`` stays the parent: the move is never redirected.
        """

        await self._storage.resolve_pending()
        if entry_id is not None and await self._storage.get_entry(entry_id) is None:
            raise SessionError("not_found", f"Entry {entry_id} not found")
        await self._storage.set_leaf_id(entry_id)
        if summary is None:
            return None
        return await self._append_typed_entry(
            BranchSummaryEntry(
                id=await self._storage.create_entry_id(),
                parent_id=entry_id,
                timestamp=_iso_now(),
                from_id=entry_id if entry_id is not None else "root",
                summary=summary["summary"],
                details=summary.get("details"),
                from_hook=summary.get("from_hook"),
            )
        )

    async def withdraw_tail(
        self,
        target_leaf: str | None,
        is_withdrawn: Callable[[SessionTreeEntry], bool],
    ) -> None:
        """Take entries back off the path, keeping the rest of what follows ``target_leaf``.

        #654 (ADR-0286). The leaf returns to ``target_leaf`` and every entry the
        path held after it that ``is_withdrawn`` does not claim is appended again
        with a fresh id, in order. Those entries are a turn's neighbours: a custom
        entry or model change flushed between two attempts. What is withdrawn stays
        in the file on the branch left behind. ``None`` starts a new root.

        Raises :class:`SessionError` when ``target_leaf`` is not on the current
        path, with nothing written.

        One store operation (:meth:`SessionStorage.withdraw_tail`, #721): what
        an earlier write left open is settled first, so an entry whose line
        landed and then raised is on the path; nothing else writes between
        reading the path and the last survivor; and a write that raises part of
        the way leaves the rest owed, finished by the next write.
        """

        await self._storage.withdraw_tail(target_leaf, is_withdrawn)


__all__ = ["Session", "SessionContext"]
