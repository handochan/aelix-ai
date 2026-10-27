"""``<agent dir>/models-store.json``: the model catalog cache (#434, ADR-0264 §4.3 The store).

Pi parity: ``packages/coding-agent/src/core/models-store.ts`` ``FileModelsStore``,
here because the agent dir is coding-agent's (§4.7). The contract is
:class:`aelix_ai.models_store.ModelsStore`.

The file is ``{"version": 1, "salt": "<64 hex>", "entries": {...}}``. It is
never resolved against the cwd: a repository cannot choose what the global
store holds.

- **Writes**: ``<path>.tmp`` at ``0o600``, fsync, ``os.replace``, with the
  directory at ``0o700``, as ``auth.json`` is written. The read-modify-write
  runs under an exclusive ``flock`` on ``models-store.json.lock``, a sidecar
  that is never replaced: a lock on the data file does not exclude once the
  holder has replaced it (ADR-0264 §3.2, measured). The lock is taken with
  ``LOCK_NB`` and retried for at most :data:`LOCK_TIMEOUT` seconds, so a lock
  another process holds cannot stall event-loop shutdown; not taking it in time
  is a failed write. Windows gets an in-process lock only, as ``auth.json``
  does; a lost race there costs a refetch.
- **Reads** take no lock (``os.replace`` is atomic). A missing, empty or
  unparseable file is an empty store, with a warning for the last two. A
  ``version`` this build does not know is neither read nor rewritten: every
  read is empty and every write raises, so an older aelix after a downgrade
  runs on its shipped catalog and cannot clobber a newer store (#383).

Deleting the file and its ``.lock`` loses only the cache.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from aelix_ai.models_store import STORE_VERSION, ModelsStoreError, entry_stamp

try:
    import fcntl as _fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows / non-POSIX
    _fcntl = None  # type: ignore[assignment]

__all__ = ["LOCK_TIMEOUT", "STORE_FILENAME", "FileModelsStore", "default_store_path"]

_LOG = logging.getLogger(__name__)

STORE_FILENAME = "models-store.json"
LOCK_TIMEOUT = 2.0
"""The longest a write waits for another process's lock, in seconds (§4.3)."""

_LOCK_POLL = 0.05

# One in-process lock per store path: the only lock Windows gets, and the
# guard that keeps two threads of one process from interleaving on POSIX.
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def default_store_path() -> Path:
    """``<agent dir>/models-store.json``."""

    from .cli.config import get_agent_dir

    return Path(get_agent_dir()) / STORE_FILENAME


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = _THREAD_LOCKS[key] = threading.Lock()
        return lock


class FileModelsStore:
    """The file-backed :class:`aelix_ai.models_store.ModelsStore`."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_store_path()
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._warned_version = False

    @property
    def path(self) -> Path:
        return self._path

    # ── reading ────────────────────────────────────────────────────

    def _read_document(self) -> dict[str, Any] | None:
        """The parsed file, ``{}`` for an empty store, ``None`` for an unknown version."""

        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            _LOG.warning("%s could not be read (%s); using no cached model catalog", self._path, exc)
            return {}
        if not text.strip():
            _LOG.warning("%s is empty; using no cached model catalog", self._path)
            return {}
        try:
            document = json.loads(text)
        except ValueError:
            _LOG.warning("%s is not valid JSON; using no cached model catalog", self._path)
            return {}
        if not isinstance(document, dict) or not isinstance(document.get("entries", {}), dict):
            _LOG.warning("%s is not a models store; using no cached model catalog", self._path)
            return {}
        if "version" not in document and not document.get("entries"):
            return {}  # ``{}``: an empty store, rewritten on the next write
        if document.get("version") != STORE_VERSION:
            if not self._warned_version:
                self._warned_version = True
                _LOG.warning(
                    "%s has a version this aelix does not know; leaving it untouched "
                    "and using the shipped model catalog",
                    self._path,
                )
            return None
        return document

    def read(self, key: str) -> dict[str, Any] | None:
        document = self._read_document()
        if not document:
            return None
        entry = document.get("entries", {}).get(key)
        return copy.deepcopy(entry) if isinstance(entry, dict) else None

    def readable(self) -> bool:
        """False when the file has a ``version`` this build does not know."""

        return self._read_document() is not None

    # ── writing ────────────────────────────────────────────────────

    def write(
        self, key: str, entry: Mapping[str, Any], *, base: tuple[Any, Any] | None
    ) -> dict[str, Any] | None:
        with self._locked():
            document = self._writable_document()
            entries = document.setdefault("entries", {})
            current = entries.get(key)
            if not isinstance(current, dict):
                current = None
            if entry_stamp(current) != base:
                return copy.deepcopy(current) if current is not None else {}
            entries[key] = copy.deepcopy(dict(entry))
            self._write_document(document)
        return None

    def delete(self, key: str) -> None:
        with self._locked():
            document = self._writable_document()
            entries = document.setdefault("entries", {})
            if key not in entries:
                return
            del entries[key]
            self._write_document(document)

    def _writable_document(self) -> dict[str, Any]:
        document = self._read_document()
        if document is None:
            raise ModelsStoreError("the models store has a version this aelix does not know")
        if not document:
            document = {"version": STORE_VERSION, "entries": {}}
        salt = document.get("salt")
        if not (isinstance(salt, str) and len(salt) == 64):
            document["salt"] = secrets.token_hex(32)
        return document

    def _write_document(self, document: Mapping[str, Any]) -> None:
        payload = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        try:
            fd = os.open(tmp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            with contextlib.suppress(OSError):
                os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise ModelsStoreError(f"the models store could not be written ({exc.strerror or exc})") from exc
        with contextlib.suppress(OSError):
            os.chmod(self._path, 0o600)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        # One deadline for both locks, so a write waits at most LOCK_TIMEOUT.
        deadline = time.monotonic() + LOCK_TIMEOUT
        thread_lock = _thread_lock(self._path)
        if not thread_lock.acquire(timeout=LOCK_TIMEOUT):
            raise ModelsStoreError("the models store is locked by another refresh")
        try:
            self._ensure_dir()
            if _fcntl is None:  # pragma: no cover - Windows: in-process lock only
                yield
                return
            fd = self._take_file_lock(deadline)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    _fcntl.flock(fd, _fcntl.LOCK_UN)  # pyright: ignore[reportAttributeAccessIssue]
                os.close(fd)
        finally:
            thread_lock.release()

    def _ensure_dir(self) -> None:
        parent = self._path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise ModelsStoreError(
                f"the models store's directory could not be made ({exc.strerror or exc})"
            ) from exc
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)

    def _take_file_lock(self, deadline: float) -> int:
        assert _fcntl is not None
        try:
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise ModelsStoreError(
                f"the models store's lock could not be opened ({exc.strerror or exc})"
            ) from exc
        while True:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)  # pyright: ignore[reportAttributeAccessIssue]
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise ModelsStoreError("the models store is locked by another aelix") from None
                time.sleep(_LOCK_POLL)
            except OSError as exc:
                os.close(fd)
                raise ModelsStoreError(
                    f"the models store's lock could not be taken ({exc.strerror or exc})"
                ) from exc
