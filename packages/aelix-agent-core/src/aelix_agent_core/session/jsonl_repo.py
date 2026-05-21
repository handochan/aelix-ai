"""``JsonlSessionRepo`` (Sprint 4a / Phase 2.2.1 + Sprint 4b §E).

Pi source: ``packages/agent/src/harness/session/jsonl-repo.ts``. Sprint 4a
shipped **create / open / list / delete**; Sprint 4b adds **fork** backed
by :func:`get_entries_to_fork` from :mod:`repo_utils` (Pi
``repo-utils.ts:32-50``).

Default sessions root: ``~/.aelix/sessions/``. ``cwd`` is encoded via Pi
``encodeCwd``: strip leading ``/`` or ``\\``, replace ``/\\:`` with ``-``,
wrap in ``--...--``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from aelix_agent_core.session.fs import FileSystem, LocalFileSystem
from aelix_agent_core.session.jsonl_storage import (
    JsonlSessionStorage,
    _load_jsonl_storage,
    load_jsonl_session_metadata,
)
from aelix_agent_core.session.repo_utils import ForkOptions, get_entries_to_fork
from aelix_agent_core.session.session import Session
from aelix_agent_core.session.storage import (
    JsonlSessionMetadata,
    SessionError,
)


def _encode_cwd(cwd: str) -> str:
    """Pi ``encodeCwd`` (``jsonl-repo.ts:34-36``).

    ``--{path-with-leading-slash-stripped-and-slashes-colons-replaced}--``.
    """

    stripped = re.sub(r"^[/\\]", "", cwd)
    replaced = re.sub(r"[/\\:]", "-", stripped)
    return f"--{replaced}--"


def _create_session_id() -> str:
    """Aelix-additive divergence: ``uuid4()`` instead of Pi ``uuidv7()``.

    Documented in ADR-0022 §"Aelix-additive divergences". ``uuidv7()`` port
    deferred to Phase 4 (entry IDs are local to the file so cross-runtime
    parse is unaffected).
    """

    return str(uuid.uuid4())


def _create_timestamp() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class JsonlSessionCreateOptions:
    """Pi ``JsonlSessionCreateOptions`` (``types.ts:478-481``)."""

    cwd: str
    parent_session_path: str | None = None
    id: str | None = None


@dataclass(frozen=True)
class JsonlSessionListOptions:
    """Pi ``JsonlSessionListOptions`` (``types.ts:483-485``)."""

    cwd: str | None = None


class JsonlSessionRepo:
    """Pi ``JsonlSessionRepo`` ported (``jsonl-repo.ts:38-177``).

    Sprint 4a shipped ``create`` / ``open`` / ``list`` / ``delete``. Sprint
    4b §E added ``fork`` per Pi ``jsonl-repo.ts:133-159`` via
    :func:`get_entries_to_fork` from :mod:`repo_utils`.
    """

    def __init__(
        self, *, fs: FileSystem | None = None, sessions_root: str | None = None
    ) -> None:
        self._fs = fs or LocalFileSystem()
        self._sessions_root_input = sessions_root or "~/.aelix/sessions"
        self._sessions_root: str | None = None

    async def _get_sessions_root(self) -> str:
        if self._sessions_root is None:
            try:
                self._sessions_root = await self._fs.absolute_path(
                    self._sessions_root_input
                )
            except OSError as exc:
                raise SessionError(
                    "storage",
                    f"Failed to resolve sessions root "
                    f"{self._sessions_root_input}: {exc}",
                    cause=exc,
                ) from exc
        return self._sessions_root

    async def _get_session_dir(self, cwd: str) -> str:
        root = await self._get_sessions_root()
        try:
            return await self._fs.join_path([root, _encode_cwd(cwd)])
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to resolve session directory for {cwd}: {exc}",
                cause=exc,
            ) from exc

    async def _create_session_file_path(
        self, cwd: str, session_id: str, timestamp: str
    ) -> str:
        encoded_ts = re.sub(r"[:.]", "-", timestamp)
        session_dir = await self._get_session_dir(cwd)
        try:
            return await self._fs.join_path(
                [session_dir, f"{encoded_ts}_{session_id}.jsonl"]
            )
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to resolve session file path for {session_id}: {exc}",
                cause=exc,
            ) from exc

    async def create(
        self, options: JsonlSessionCreateOptions
    ) -> Session:
        """Pi `create` (``jsonl-repo.ts:75-90``)."""

        session_id = options.id or _create_session_id()
        created_at = _create_timestamp()
        session_dir = await self._get_session_dir(options.cwd)
        try:
            await self._fs.create_dir(session_dir, recursive=True)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to create session directory {session_dir}: {exc}",
                cause=exc,
            ) from exc
        file_path = await self._create_session_file_path(
            options.cwd, session_id, created_at
        )
        storage = await JsonlSessionStorage.create(
            self._fs,
            file_path,
            cwd=options.cwd,
            session_id=session_id,
            parent_session_path=options.parent_session_path,
        )
        return Session(storage)

    async def open(
        self,
        metadata: JsonlSessionMetadata,
        *,
        cwd_override: str | None = None,
    ) -> Session:
        """Pi `open` (``jsonl-repo.ts:92-100``).

        Sprint 6h₅b W6 (P-367 W5 MINOR fix) — adds optional
        ``cwd_override`` keyword argument so callers like
        :meth:`AgentSessionRuntime.import_from_jsonl` can apply a cwd
        rewrite cleanly at the repo seam instead of mutating
        ``storage._metadata`` directly. Pi parity:
        ``SessionManager.open(path, dir, cwdOverride)`` (Pi threads the
        override into the loaded session's ``cwd`` field).

        When supplied, the override is written back onto the storage's
        metadata via :func:`dataclasses.replace` AFTER the storage opens,
        so a subsequent ``Session.get_metadata()`` sees the overridden
        cwd. The on-disk file is NOT rewritten — the override is
        in-memory only, mirroring Pi where ``cwdOverride`` is a
        session-lifetime field separate from the persisted header.
        """

        try:
            present = await self._fs.exists(metadata.path)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to check session {metadata.path}: {exc}",
                cause=exc,
            ) from exc
        if not present:
            raise SessionError(
                "not_found", f"Session not found: {metadata.path}"
            )
        storage = await JsonlSessionStorage.open(self._fs, metadata.path)
        if cwd_override is not None:
            # Sprint 6h₅b W6 (P-367 W5 MINOR fix) — clean repo seam for
            # the Pi ``cwdOverride`` parameter. The previous Sprint 6h₅b
            # W2 implementation mutated ``storage._metadata`` from
            # outside the repo; centralising the writeback here keeps
            # the private-attribute touch on a single owner.
            from dataclasses import replace as _dc_replace

            storage._metadata = _dc_replace(
                storage._metadata, cwd=cwd_override
            )
        return Session(storage)

    async def list(
        self, options: JsonlSessionListOptions | None = None
    ) -> list[JsonlSessionMetadata]:
        """Pi `list` (``jsonl-repo.ts:102-124``)."""

        opts = options or JsonlSessionListOptions()
        if opts.cwd is not None:
            dirs = [await self._get_session_dir(opts.cwd)]
        else:
            dirs = await self._list_session_dirs()
        sessions: list[JsonlSessionMetadata] = []
        for directory in dirs:
            try:
                present = await self._fs.exists(directory)
            except OSError as exc:
                raise SessionError(
                    "storage",
                    f"Failed to check session directory {directory}: {exc}",
                    cause=exc,
                ) from exc
            if not present:
                continue
            try:
                children = await self._fs.list_dir(directory)
            except OSError as exc:
                raise SessionError(
                    "storage",
                    f"Failed to list sessions in {directory}: {exc}",
                    cause=exc,
                ) from exc
            for child in children:
                if child.kind == "directory" or not child.name.endswith(".jsonl"):
                    continue
                try:
                    sessions.append(
                        await load_jsonl_session_metadata(self._fs, child.path)
                    )
                except SessionError as exc:
                    if exc.code != "invalid_session":
                        raise
        # Sort newest-first by created_at (Pi parity).
        sessions.sort(key=lambda m: m.created_at, reverse=True)
        return sessions

    async def delete(self, metadata: JsonlSessionMetadata) -> None:
        """Pi `delete` (``jsonl-repo.ts:126-131``)."""

        try:
            await self._fs.remove(metadata.path, force=True)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to delete session {metadata.path}: {exc}",
                cause=exc,
            ) from exc

    async def fork(
        self,
        source: JsonlSessionMetadata,
        options: ForkOptions,
    ) -> Session:
        """Pi `fork` (``jsonl-repo.ts:133-159``) — Sprint 4b §E.

        Opens ``source``, computes the fork-cut entry list via
        :func:`get_entries_to_fork`, creates a NEW JSONL session under
        ``options.cwd`` and appends the cut entries to the new file. The
        ``parent_session_path`` defaults to the source path (Pi parity).

        Raises :class:`SessionError("invalid_fork_target")` when
        ``options.entry_id`` is set but does not resolve to a valid target
        per the position rules.
        """

        source_session = await self.open(source)
        forked_entries = await get_entries_to_fork(
            source_session.get_storage(), options.entry_id, options.position
        )
        session_id = options.id or _create_session_id()
        created_at = _create_timestamp()
        session_dir = await self._get_session_dir(options.cwd)
        try:
            await self._fs.create_dir(session_dir, recursive=True)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to create session directory {session_dir}: {exc}",
                cause=exc,
            ) from exc
        file_path = await self._create_session_file_path(
            options.cwd, session_id, created_at
        )
        storage = await JsonlSessionStorage.create(
            self._fs,
            file_path,
            cwd=options.cwd,
            session_id=session_id,
            parent_session_path=options.parent_session_path or source.path,
        )
        for entry in forked_entries:
            await storage.append_entry(entry)
        return Session(storage)

    async def fork_from(
        self,
        source: JsonlSessionMetadata,
        target_cwd: str,
        *,
        session_dir: str | None = None,
    ) -> Session:
        """Pi parity: ``SessionManager.forkFrom`` (``session-manager.ts:1353-1394``).

        Sprint 6h₅b (Phase 4.15, ADR-0083, P-361). Cross-cwd import:
        loads ALL source entries (no leaf truncation, unlike
        :meth:`fork`), creates a NEW JSONL file under ``target_cwd``,
        rewrites the cwd header, and stamps ``parent_session_path`` to
        the source path so lineage round-trips.

        Sprint 6h₅b ships the surface only — there is no internal
        consumer in this sprint. The CLI ``/branch-from`` command lands
        in Phase 5 and will be the first caller. The method is exposed
        now so the wire surface stays Pi-shaped without a follow-up
        breaking-change sprint.

        Sprint 6h₅b W6 (P-368 W5 MINOR fix) — adds optional
        ``session_dir`` keyword argument mirroring Pi's 3rd parameter
        (``forkFrom(source, targetCwd, sessionDir?)``). When supplied,
        the override replaces the default ``_get_session_dir(target_cwd)``
        resolution; useful for callers that already own a resolved
        directory (e.g. tests pinning a tmp_path, or future TUI work
        that pre-computes per-workspace dirs).
        """

        session_id = _create_session_id()
        created_at = _create_timestamp()
        target_session_dir = (
            session_dir
            if session_dir is not None
            else await self._get_session_dir(target_cwd)
        )
        try:
            await self._fs.create_dir(target_session_dir, recursive=True)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to create session directory {target_session_dir}: {exc}",
                cause=exc,
            ) from exc
        if session_dir is not None:
            # Caller-supplied directory: build the file path inside it
            # directly (skip the per-cwd ``_get_session_dir`` resolve).
            encoded_ts = re.sub(r"[:.]", "-", created_at)
            try:
                target_path = await self._fs.join_path(
                    [target_session_dir, f"{encoded_ts}_{session_id}.jsonl"]
                )
            except OSError as exc:
                raise SessionError(
                    "storage",
                    f"Failed to resolve session file path for {session_id}: {exc}",
                    cause=exc,
                ) from exc
        else:
            target_path = await self._create_session_file_path(
                target_cwd, session_id, created_at
            )
        # Load ALL source entries (no leaf truncation — Pi parity vs `fork`).
        _, all_entries, _ = await _load_jsonl_storage(self._fs, source.path)
        storage = await JsonlSessionStorage.create(
            self._fs,
            target_path,
            cwd=target_cwd,
            session_id=session_id,
            parent_session_path=source.path,
        )
        for entry in all_entries:
            await storage.append_entry(entry)
        return Session(storage)

    async def _list_session_dirs(self) -> list[str]:
        root = await self._get_sessions_root()
        try:
            present = await self._fs.exists(root)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to check sessions root {root}: {exc}",
                cause=exc,
            ) from exc
        if not present:
            return []
        try:
            entries = await self._fs.list_dir(root)
        except OSError as exc:
            raise SessionError(
                "storage",
                f"Failed to list sessions root {root}: {exc}",
                cause=exc,
            ) from exc
        return [e.path for e in entries if e.kind == "directory"]


__all__ = [
    "ForkOptions",
    "JsonlSessionCreateOptions",
    "JsonlSessionListOptions",
    "JsonlSessionRepo",
]
