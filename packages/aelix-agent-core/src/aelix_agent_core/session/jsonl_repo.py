"""``JsonlSessionRepo`` (Sprint 4a / Phase 2.2.1).

Pi source: ``packages/agent/src/harness/session/jsonl-repo.ts``. Sprint 4a
ships **create / open / list / delete**. ``fork`` is deferred to Sprint 4b
because it depends on ``getEntriesToFork`` from compaction utilities.

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
    load_jsonl_session_metadata,
)
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

    Sprint 4a ships ``create`` / ``open`` / ``list`` / ``delete``. ``fork``
    is intentionally absent — Sprint 4b adds it once
    ``getEntriesToFork`` (compaction utilities) lands.
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

    async def open(self, metadata: JsonlSessionMetadata) -> Session:
        """Pi `open` (``jsonl-repo.ts:92-100``)."""

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
    "JsonlSessionCreateOptions",
    "JsonlSessionListOptions",
    "JsonlSessionRepo",
]
