"""Auth credential storage — Sprint 6c · Phase 4.3 · §H.

Pi parity: ``packages/coding-agent/src/core/auth-storage.ts`` (SHA
734e08e) condensed from 470 LOC to ~250.

File format::

    {
      "<provider-id>": {"type": "api_key", "key": "sk-..."},
      "anthropic":     {"type": "oauth", "refresh": "...", "access": "...", "expires": 1234567890123, ...}
    }

Permissions: parent dir ``0o700``, file ``0o600`` (set on creation +
re-applied on every save for idempotency).

Locking:

- In-process: :class:`asyncio.Lock` on the :class:`AuthStorage`
  instance.
- Cross-process: :func:`fcntl.flock` advisory POSIX lock on the
  ``auth.json`` file descriptor. **POSIX-only** in Sprint 6c —
  Windows fallback (msvcrt.locking) is a TODO for Sprint 6d.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from aelix_ai.oauth._high_level import get_oauth_api_key_from_credentials
from aelix_ai.oauth.types import OAuthCredentials

# TODO(Sprint 6d): Windows cross-process locking via ``msvcrt.locking``.
# For Sprint 6c, Windows users get in-process locking only; concurrent
# Aelix CLI invocations on Windows may race on auth.json writes.
try:
    import fcntl as _fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Windows / non-POSIX
    _fcntl = None  # type: ignore[assignment]

_LOG = logging.getLogger(__name__)


def default_auth_path() -> Path:
    """Pi parity: ``cli/config.ts::getAuthPath()``.

    Aelix default: ``$XDG_CONFIG_HOME/aelix/agent/auth.json`` (matches
    XDG Base Directory spec); falls back to
    ``~/.config/aelix/agent/auth.json`` when ``XDG_CONFIG_HOME`` is
    unset. NOT Pi's ``~/.pi/agent/`` — Aelix is a separate binary, so
    it gets its own config root.

    Resolution order:

    1. ``AELIX_AUTH_PATH`` env var (test fixtures use this).
    2. ``$XDG_CONFIG_HOME/aelix/agent/auth.json``.
    3. ``~/.config/aelix/agent/auth.json``.

    Sprint 6c W6 (W4 m9): added step 2 for XDG compliance.
    """

    override = os.environ.get("AELIX_AUTH_PATH")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "aelix" / "agent" / "auth.json"


class AuthStorage:
    """Pi parity: ``core/auth-storage.ts`` (~470 LOC condensed to ~250).

    Async-only interface (Pi's storage is dual sync+async; Aelix is
    async-first — Sprint 6a's ``get_api_key_and_headers`` callback is
    awaitable, and the harness always awaits its result).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path if path is not None else default_auth_path()
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    # === Internal IO helpers ===

    def _ensure_parent_dir(self) -> None:
        """Create the parent dir at mode 0o700 (idempotent)."""

        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Re-chmod for idempotency on existing dirs created with
        # different umask.
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)

    def _read_raw(self) -> dict[str, dict[str, Any]]:
        """Read + parse the file; empty file or missing file → ``{}``."""

        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        if not text.strip():
            return {}
        try:
            return dict(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            _LOG.warning("auth.json corrupted; resetting to empty")
            return {}

    def _write_raw(self, data: dict[str, dict[str, Any]]) -> None:
        """Write atomically (tmp + fsync + rename) with 0o600 file mode.

        Sprint 6c W6 (W4 M1): writes to ``<path>.tmp`` first, fsyncs to
        guarantee durability, then ``os.replace(tmp, path)`` for atomic
        swap. A crash mid-write leaves the original ``auth.json``
        untouched — partial writes are no longer possible.
        """

        self._ensure_parent_dir()
        payload = json.dumps(data, indent=2)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        # Write to ``<path>.tmp`` with 0o600 at creation.
        fd = os.open(
            tmp_path,
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, payload.encode("utf-8"))
            # fsync the tmp file so the data is durably on disk BEFORE
            # the rename. Without this, a crash between rename + fsync
            # could leave a zero-byte auth.json.
            os.fsync(fd)
        finally:
            os.close(fd)
        # Belt-and-suspenders chmod the tmp file (some umasks override).
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, 0o600)
        # Atomic rename — POSIX guarantees either old-content or
        # new-content visible, never partial.
        os.replace(tmp_path, self._path)
        with contextlib.suppress(OSError):
            os.chmod(self._path, 0o600)

    def _cross_process_lock_fd(self) -> int | None:
        """Open + flock the auth.json file for cross-process locking.

        Returns the held file descriptor (caller must close it to
        release). Returns :data:`None` on Windows / non-POSIX.
        """

        if _fcntl is None:
            return None
        self._ensure_parent_dir()
        # Open the file for read+write (create if missing) so we always
        # have a stable fd to flock against.
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        except BaseException:
            # Sprint 6c W6 (W4 M5): broadened from ``OSError`` to
            # ``BaseException`` so ``KeyboardInterrupt`` / ``SystemExit``
            # also release the file descriptor. Previously a Ctrl-C
            # during ``flock`` (rare but possible on busy systems)
            # leaked an open ``fd``.
            os.close(fd)
            raise
        return fd

    @staticmethod
    def _release_cross_process_lock(fd: int | None) -> None:
        if fd is None:
            return
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    # === Public API ===

    async def load(self) -> None:
        """Load credentials from disk under the lock."""

        async with self._lock:
            fd = self._cross_process_lock_fd()
            try:
                self._data = self._read_raw()
                self._loaded = True
            finally:
                self._release_cross_process_lock(fd)

    async def save(self) -> None:
        """Persist credentials to disk under the lock."""

        async with self._lock:
            fd = self._cross_process_lock_fd()
            try:
                self._write_raw(dict(self._data))
            finally:
                self._release_cross_process_lock(fd)

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    async def get_oauth(self, provider_id: str) -> OAuthCredentials | None:
        """Look up OAuth credentials for a provider; ``None`` if absent."""

        await self._ensure_loaded()
        entry = self._data.get(provider_id)
        if entry is None or entry.get("type") != "oauth":
            return None
        # Strip the ``type`` discriminator before constructing the
        # dataclass; remaining keys hydrate via from_json.
        creds_dict = {k: v for k, v in entry.items() if k != "type"}
        return OAuthCredentials.from_json(creds_dict)

    async def set_oauth(
        self, provider_id: str, credentials: OAuthCredentials
    ) -> None:
        """Store OAuth credentials for a provider (persists immediately)."""

        await self._ensure_loaded()
        async with self._lock:
            fd = self._cross_process_lock_fd()
            try:
                current = self._read_raw()
                current[provider_id] = {"type": "oauth", **credentials.to_json()}
                self._write_raw(current)
                self._data = current
            finally:
                self._release_cross_process_lock(fd)

    async def get_api_key(self, provider_id: str) -> str | None:
        """Look up an API key for a provider; ``None`` if absent or OAuth."""

        await self._ensure_loaded()
        entry = self._data.get(provider_id)
        if entry is None or entry.get("type") != "api_key":
            return None
        return str(entry.get("key", "")) or None

    async def set_api_key(self, provider_id: str, key: str) -> None:
        """Store an API key (persists immediately)."""

        await self._ensure_loaded()
        async with self._lock:
            fd = self._cross_process_lock_fd()
            try:
                current = self._read_raw()
                current[provider_id] = {"type": "api_key", "key": key}
                self._write_raw(current)
                self._data = current
            finally:
                self._release_cross_process_lock(fd)

    async def remove(self, provider_id: str) -> None:
        """Drop the entry for a provider (persists immediately)."""

        await self._ensure_loaded()
        async with self._lock:
            fd = self._cross_process_lock_fd()
            try:
                current = self._read_raw()
                current.pop(provider_id, None)
                self._write_raw(current)
                self._data = current
            finally:
                self._release_cross_process_lock(fd)

    async def get_oauth_api_key(self, provider_id: str) -> str | None:
        """Pi parity: ``oauth/index.ts:127-152`` ``getOAuthApiKey``.

        Resolves OAuth credentials, refreshes if expired, persists the
        refreshed credentials, and returns the access token via
        ``provider.get_api_key(creds)``. Returns :data:`None` if no
        credentials are stored.
        """

        await self._ensure_loaded()
        creds = await self.get_oauth(provider_id)
        if creds is None:
            return None

        # Cheap path: not expired — return immediately without locking
        # for write.
        now_ms = int(time.time() * 1000)
        if now_ms < creds.expires:
            from aelix_ai.oauth._registry import get_oauth_provider

            provider = get_oauth_provider(provider_id)
            if provider is None:
                return None
            return provider.get_api_key(creds)

        # Expired: refresh under the cross-process lock so two CLI
        # instances can't both refresh and race-write.
        async with self._lock:
            fd = self._cross_process_lock_fd()
            try:
                # Re-read in case another process refreshed first.
                current = self._read_raw()
                self._data = current
                entry = current.get(provider_id)
                if entry is None or entry.get("type") != "oauth":
                    return None
                fresh_creds_dict = {
                    k: v for k, v in entry.items() if k != "type"
                }
                fresh_creds = OAuthCredentials.from_json(fresh_creds_dict)
                if int(time.time() * 1000) < fresh_creds.expires:
                    # Another process beat us to it.
                    from aelix_ai.oauth._registry import get_oauth_provider

                    provider = get_oauth_provider(provider_id)
                    if provider is None:
                        return None
                    return provider.get_api_key(fresh_creds)

                # Still expired — refresh + persist.
                result = await get_oauth_api_key_from_credentials(
                    provider_id, {provider_id: fresh_creds}
                )
                if result is None:
                    return None
                current[provider_id] = {
                    "type": "oauth",
                    **result.new_credentials.to_json(),
                }
                self._write_raw(current)
                self._data = current
                return result.api_key
            finally:
                self._release_cross_process_lock(fd)


__all__ = ["AuthStorage", "default_auth_path"]
