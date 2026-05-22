"""Settings storage backends — Sprint 6h₇b · Phase 5a-iii-β · §C.

Pi parity: ``packages/coding-agent/src/core/settings-manager.ts:146-239``
(SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``).

Two storage backends + a :class:`typing.Protocol` interface mirror Pi's
``SettingsStorage`` interface:

- :class:`FileSettingsStorage` — disk-backed with cross-process
  ``fcntl.flock`` advisory locking + atomic tmp-write-then-replace.
- :class:`InMemorySettingsStorage` — test storage with no I/O.

Aelix-additive divergence from Pi (documented in ADR-0091):

- Pi uses ``proper-lockfile`` directory locks. Aelix uses
  ``fcntl.flock`` mirroring :mod:`aelix_ai.oauth.auth_storage` (Sprint 6c
  precedent). POSIX-only — Windows fallback is a Sprint 6h₇c+ item.
- File permissions ``0o644`` (non-secret config). Auth credentials use
  ``0o600``; the distinction is intentional + documented.
- Pi global path: ``~/.pi/agent/settings.json``. Aelix:
  ``$XDG_CONFIG_HOME/aelix/agent/settings.json`` (fallback
  ``~/.config/aelix/agent/settings.json``) per ADR-0034 namespace
  decision.
- Pi project path: ``./.pi/settings.json``. Aelix:
  ``./.aelix/settings.json``.

The ``with_lock`` Protocol mirrors Pi: the caller hands in a
``fn(current: str | None) -> str | None`` callback that receives the
existing on-disk content (or ``None`` if missing) and returns either
``None`` (no write) or the new JSON content to persist atomically.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from aelix_ai.settings.types import SettingsScope

try:
    import fcntl as _fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Windows / non-POSIX
    _fcntl = None  # type: ignore[assignment]

_LOG = logging.getLogger(__name__)


def default_settings_path() -> Path:
    """Pi parity: ``cli/config.ts::getAgentDir() / settings.json`` global path.

    Sibling of :func:`aelix_ai.oauth.auth_storage.default_auth_path`. Uses
    the XDG Base Directory specification for the default location;
    ``AELIX_SETTINGS_PATH`` overrides for tests.

    Resolution order:

    1. ``AELIX_SETTINGS_PATH`` env var (test fixtures).
    2. ``$XDG_CONFIG_HOME/aelix/agent/settings.json``.
    3. ``~/.config/aelix/agent/settings.json``.

    Aelix-additive divergence: Pi default is ``~/.pi/agent/settings.json``.
    Aelix uses ``aelix/`` per ADR-0034 namespace decision — documented
    in ADR-0091 §"Aelix-additive divergences".
    """

    override = os.environ.get("AELIX_SETTINGS_PATH")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "aelix" / "agent" / "settings.json"


def default_project_settings_path(cwd: str | Path) -> Path:
    """Pi parity: ``./.pi/settings.json`` (Aelix: ``./.aelix/settings.json``).

    Aelix-additive divergence: Pi uses ``./.pi/``; Aelix uses ``./.aelix/``
    per ADR-0034 namespace decision.
    """

    return Path(cwd) / ".aelix" / "settings.json"


# ``fn`` callback signature: receives the current on-disk text (or None
# when the file does not exist) and returns the new JSON string to
# write (or None to skip writing).
SettingsWithLockFn = Callable[[str | None], str | None]


@runtime_checkable
class SettingsStorage(Protocol):
    """Pi parity: ``settings-manager.ts:148-150`` ``SettingsStorage``.

    The single ``with_lock(scope, fn)`` method handles both read and
    write paths. ``fn`` is invoked with the current on-disk content (or
    ``None`` if missing) under the lock and may either return ``None``
    (read-only) or a new JSON string (atomic write).
    """

    def with_lock(self, scope: SettingsScope, fn: SettingsWithLockFn) -> None:
        ...


class FileSettingsStorage:
    """Pi parity: ``settings-manager.ts:157-222`` ``FileSettingsStorage``.

    Disk-backed storage with ``fcntl.flock`` cross-process locking +
    atomic write-rename (tmp + fsync + ``os.replace``). Mirrors the
    :class:`aelix_ai.oauth.auth_storage.AuthStorage` Sprint 6c precedent.

    Per-scope :class:`asyncio.Lock` instances serialize in-process
    writes. ``fcntl.flock`` (LOCK_EX) on the actual settings file handle
    serializes cross-process writes. File permissions: ``0o644`` (non-
    secret config — auth credentials use ``0o600``).
    """

    def __init__(
        self,
        cwd: str | Path,
        agent_dir: str | Path,
        global_path: Path | None = None,
    ) -> None:
        # ``global_path`` overrides the ``agent_dir / "settings.json"``
        # derivation when callers (notably :meth:`SettingsManager.create`
        # honoring ``AELIX_SETTINGS_PATH``) need to anchor the global
        # store at a non-standard filename. Without this hook the env
        # override silently dropped the filename portion of the path
        # (W5 MAJOR-3 — Sprint 6h₇b fold-in).
        self._global_path: Path = (
            global_path if global_path is not None else Path(agent_dir) / "settings.json"
        )
        self._project_path: Path = (
            Path(cwd) / ".aelix" / "settings.json"
        )

    @property
    def global_path(self) -> Path:
        return self._global_path

    @property
    def project_path(self) -> Path:
        return self._project_path

    def _path_for(self, scope: SettingsScope) -> Path:
        return self._global_path if scope == "global" else self._project_path

    def _ensure_parent_dir(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

    def _read_text(self, path: Path) -> str | None:
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            _LOG.warning(
                "settings.json read failed at %s: %s", path, exc
            )
            return None

    def _write_text_atomic(self, path: Path, payload: str) -> None:
        """Atomic write via tmp file + fsync + ``os.replace``.

        Mirrors :meth:`aelix_ai.oauth.auth_storage.AuthStorage._write_raw`
        but uses ``0o644`` (non-secret config).
        """

        self._ensure_parent_dir(path)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(
            tmp_path,
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            0o644,
        )
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o644)

    def _acquire_flock(self, path: Path) -> int | None:
        """Open + flock the settings file for cross-process locking.

        Returns the held fd (caller closes to release) or :data:`None`
        on non-POSIX platforms (Windows — Sprint 6h₇c+ Windows fallback).
        """

        if _fcntl is None:
            return None
        self._ensure_parent_dir(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        except BaseException:
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _release_flock(fd: int | None) -> None:
        if fd is None:
            return
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    def with_lock(self, scope: SettingsScope, fn: SettingsWithLockFn) -> None:
        """Pi parity: ``settings-manager.ts:193-221`` ``FileSettingsStorage.withLock``.

        Synchronous (matches Pi). The Pi semantics:

        - If the file does not exist: skip the lock acquisition for the
          read path (no file to lock). Invoke ``fn(None)`` for the read.
        - If ``fn`` returns a non-None value (we need to write): create
          the parent directory if missing, then acquire the lock + write
          atomically.
        - If ``fn`` returns ``None``: no write (read-only path).
        """

        path = self._path_for(scope)
        file_exists = path.exists()
        fd: int | None = None
        try:
            if file_exists:
                fd = self._acquire_flock(path)
            current = self._read_text(path)
            new_payload = fn(current)
            if new_payload is not None:
                # We need to write — create parent dir + acquire lock
                # if not held yet.
                self._ensure_parent_dir(path)
                if fd is None:
                    fd = self._acquire_flock(path)
                self._write_text_atomic(path, new_payload)
        finally:
            self._release_flock(fd)


class InMemorySettingsStorage:
    """Pi parity: ``settings-manager.ts:224-239`` ``InMemorySettingsStorage``.

    Test storage with no I/O. Holds two ``str | None`` fields for the
    global + project JSON payloads. The ``with_lock`` flow mirrors
    :class:`FileSettingsStorage` minus the disk write.
    """

    def __init__(self) -> None:
        self._global: str | None = None
        self._project: str | None = None

    @property
    def global_content(self) -> str | None:
        return self._global

    @property
    def project_content(self) -> str | None:
        return self._project

    def with_lock(self, scope: SettingsScope, fn: SettingsWithLockFn) -> None:
        current = self._global if scope == "global" else self._project
        new_payload = fn(current)
        if new_payload is not None:
            if scope == "global":
                self._global = new_payload
            else:
                self._project = new_payload


# === Async lock registry (Aelix-additive: in-process serialization) ===
#
# Pi's ``writeQueue: Promise<void>`` chains synchronously inside the
# single-event-loop Node runtime. Aelix exposes ``asyncio.Lock`` at the
# manager level (see :class:`SettingsManager`) — the storage layer
# itself is synchronous to mirror Pi's ``withLock`` signature.
#
# This module exposes a per-scope :class:`asyncio.Lock` accessor that
# the manager uses to serialize concurrent writes from the same event
# loop. Cross-process serialization stays with ``fcntl.flock`` (see
# :meth:`FileSettingsStorage._acquire_flock`).


class _AsyncLockRegistry:
    """Per-scope :class:`asyncio.Lock` registry for in-process serialization."""

    def __init__(self) -> None:
        self._global = asyncio.Lock()
        self._project = asyncio.Lock()

    def for_scope(self, scope: SettingsScope) -> asyncio.Lock:
        return self._global if scope == "global" else self._project


__all__ = [
    "FileSettingsStorage",
    "InMemorySettingsStorage",
    "SettingsStorage",
    "SettingsWithLockFn",
    "default_project_settings_path",
    "default_settings_path",
]
