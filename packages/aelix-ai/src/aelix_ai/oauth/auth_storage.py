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

from aelix_ai.oauth._helpers import refresh_retry_reason
from aelix_ai.oauth._high_level import get_oauth_api_key_from_credentials
from aelix_ai.oauth.types import (
    AuthStatus,
    FallbackResolver,
    OAuthCredentials,
    OAuthLoginCallbacks,
)

# TODO(Sprint 6d): Windows cross-process locking via ``msvcrt.locking``.
# For Sprint 6c, Windows users get in-process locking only; concurrent
# Aelix CLI invocations on Windows may race on auth.json writes.
#
# The ``# pyright: ignore[reportAttributeAccessIssue]`` on every ``_fcntl.*``
# use below is about the CHECKER, not about doubt at runtime: on a windows
# host pyright analyses this file as Windows, where ``fcntl`` carries no
# ``flock``/``LOCK_EX``/``LOCK_UN``. This try/except is the runtime guard —
# ``_fcntl`` is :data:`None` there and every call site returns before touching
# it — but pyright cannot narrow a module-level ``None`` across functions.
try:
    import fcntl as _fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — Windows / non-POSIX
    _fcntl = None  # type: ignore[assignment]

_LOG = logging.getLogger(__name__)


class StoredCredentialError(RuntimeError):
    """A stored ``auth.json`` entry that gives no key (#363 / ADR-0251 §2.2).

    A stored credential owns its provider: whatever the entry yields is the
    answer, and a request must not go out on anything else. Raised by
    :meth:`AuthStorage.get_api_key_cascade` only when the caller passes
    ``stored_owns=True`` (the request path,
    ``ModelRegistry._request_api_key``), for an entry that cannot become a key:
    an ``api_key`` entry whose key is empty or resolves empty (a ``!command``
    that prints nothing) or whose ``!command`` fails (``"!false"``: the
    helper's error is chained as the cause, its command line and output are not
    in the message), an OAuth record whose OAuth provider is not
    registered in this process (expired or not), and an entry of a type aelix
    does not know. The message names the entry, ``/login`` and ``auth.json``.

    pi @ b223082bb gives no key for the last two (``resolve.ts:70-87``: a stored
    credential with no matching handler is ``return undefined``, never the
    environment). For an empty ``api_key`` it does not: the built-in
    ``envApiKeyAuth.resolve`` (``packages/ai/src/auth/helpers.ts:18-28``) reads
    the environment when ``credential.key`` is empty, and a ``models.json``
    provider inherits it (``provider-composer.ts:459-463``). aelix is stricter
    there (ADR-0251 §4).

    :attr:`retry_reason` is ``None`` for every entry of this class: none of them
    changes by asking again, so the turn fails at once (#379, ADR-0251 §4). Only
    :class:`OAuthRefreshError` sets it, for a transient refresh failure.
    """

    #: Why asking again may succeed (#379); ``None`` means fail at once.
    retry_reason: str | None = None

    def __init__(self, provider_id: str, reason: str, auth_path: Path | None = None) -> None:
        where = str(auth_path) if auth_path is not None else "auth.json"
        super().__init__(
            f"The auth.json entry for {provider_id} {reason}. A stored entry is the only "
            f"credential used for {provider_id}, so a request has no key. Run /login to sign "
            f"in to {provider_id} again, or remove the {provider_id} entry from {where}."
        )
        self.provider_id = provider_id


class OAuthRefreshError(StoredCredentialError):
    """A stored OAuth credential's refresh failed (#363 / ADR-0251 §2.2).

    Raised by :meth:`AuthStorage.get_api_key_cascade` only when the caller
    passes ``stored_owns=True``. A stored credential owns its provider, so a
    request must not go out on anything else - pi throws
    ``ModelsError("oauth", "OAuth refresh failed for <id>")``
    (``packages/ai/src/auth/resolve.ts:142`` @ 1cedd3272). The message keeps
    the cause's text so the user sees why the refresh failed.

    :attr:`retry_reason` (#379, ADR-0251 §4) says whether the failure is
    transient - the token endpoint answered ``429``, ``500``, ``502``-``504``,
    ``520`` or ``524`` (pi's set), or could not be reached, a 2xx whose body
    was cut short or timed out included
    (:func:`aelix_ai.oauth._helpers.refresh_retry_reason`) - and is ``None``
    for anything else (``400``/``401``/``403``, ``invalid_grant``, ``501``, a
    2xx whose whole body is not a token or whose encoding is broken). A
    transient one's message is pi's (no ``/login`` hint, since the turn
    retries it); a refused one keeps the hint. A transient one is retried by
    the turn as pi retries it: ``ModelRegistry.get_api_key_and_headers`` carries the reason
    to the CLI's auth callback, which raises it, and the harness's
    ``_make_stream_fn`` turns that raise into an error assistant message the
    auto-retry loop retries with its usual budget and backoff - pi's
    ``lazyStream`` (``packages/ai/src/api/lazy.ts:46-60``) and
    ``isRetryableAssistantError`` (``packages/ai/src/utils/retry.ts:252``). A
    non-transient one still becomes ``AgentHarnessError("auth")`` and fails
    the turn at once (inside a retry sequence: ends it, ADR-0251 §12.5).
    Every retry asks this cascade again, so while the stored login exists a
    retried request carries the refreshed token or nothing - never the
    environment's. A login another process removes meanwhile (ADR-0251
    §12.6): during the backoff, the retry's refresh re-reads the store and
    finds no login, the in-memory entry is still the expired one, and the
    cascade raises "is an OAuth login that gave no key" - nothing is sent and
    the retry ends; while the failing refresh is under way, the reload below
    drops the entry and the retry reads the steps after it, as a new turn does.
    """

    def __init__(self, provider_id: str, cause: BaseException) -> None:
        self.retry_reason = refresh_retry_reason(cause)
        detail = str(cause).strip() or type(cause).__name__
        prefix = f"Failed to refresh OAuth token for {provider_id}: "
        if detail.startswith(prefix):
            detail = detail[len(prefix) :]
        if self.retry_reason is not None:
            # #379 review round 2: the turn retries this one, so it does not send
            # the user to /login - pi's text (``ModelsError("oauth", "OAuth
            # refresh failed for <id>", {cause})`` with its cause detail,
            # ``packages/ai/src/auth/resolve.ts:142``,
            # ``utils/models-error.ts:16-21`` @ 1cedd3272).
            message = f"OAuth refresh failed for {provider_id}: {detail}"
        else:
            message = (
                f"OAuth refresh failed for {provider_id}: {detail}. "
                f"Run /login to sign in to {provider_id} again."
            )
        RuntimeError.__init__(self, message)
        self.provider_id = provider_id


def _helper_failure(exc: BaseException) -> str:
    """How a stored ``!command`` failed, without its command line or output (#363).

    ``CalledProcessError.__str__`` repeats the command (``Command '['/bin/sh',
    '-c', ...]'``), which may carry a literal key, so only the exit status is
    named - ``-1`` is ``resolve_config_value``'s "never finished" (a timeout, the
    output cap, no shell) - or the fixed text of a terminal stop
    (``_StoppedByTerminal.reason``); any other error by its type.
    """

    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason:
        return reason.rstrip(".")
    code = getattr(exc, "returncode", None)
    if code == -1:
        return "it did not finish: a timeout, too much output, or no shell to run it"
    if isinstance(code, int):
        return f"killed by signal {-code}" if code < 0 else f"exit status {code}"
    return type(exc).__name__


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
        # Sprint 6e (P-133): layered cascade state.
        # Pi parity: auth-storage.ts:193-196.
        self._runtime_overrides: dict[str, str] = {}
        self._fallback_resolver: FallbackResolver | None = None
        self._errors: list[Exception] = []
        # Sprint 6e W6 (P-141): cache for ``resolve_config_value`` so a
        # ``!<cmd>`` shell indirection is invoked at most once per
        # storage instance per unique command. Pi caches per process;
        # Aelix scopes to the storage instance for testability + per-CLI
        # invocation isolation.
        self._resolve_cache: dict[str, str] = {}

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
            _fcntl.flock(fd, _fcntl.LOCK_EX)  # pyright: ignore[reportAttributeAccessIssue]
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
                _fcntl.flock(fd, _fcntl.LOCK_UN)  # pyright: ignore[reportAttributeAccessIssue]
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
        """Store OAuth credentials for a provider (persists immediately).

        Sprint 6e W6 (W4 m4): cross-process merge note — the write path
        re-reads ``auth.json`` under the file lock before writing, so a
        sibling Aelix process that updated a DIFFERENT provider entry
        between this instance's load and this write does NOT lose its
        changes. The merge is shallow per top-level provider id.
        """

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
        """Look up an API key for a provider; ``None`` if absent or OAuth.

        Sprint 6e W6 (P-141): the stored ``key`` flows through
        :func:`resolve_config_value` so Pi's ``!<cmd>`` shell-indirection
        and env-var-name indirection are honored. Without this, a
        Pi-style ``auth.json`` entry like ``"key": "OPENAI_API_KEY"``
        would have leaked the env-var NAME as the API key.
        """

        from aelix_ai.oauth._resolve_config import resolve_config_value

        await self._ensure_loaded()
        entry = self._data.get(provider_id)
        if entry is None or entry.get("type") != "api_key":
            return None
        raw_key = str(entry.get("key", ""))
        if not raw_key:
            return None
        return resolve_config_value(raw_key, self._resolve_cache)

    async def set_api_key(self, provider_id: str, key: str) -> None:
        """Store an API key (persists immediately).

        Sprint 6e W6 (W4 m4): cross-process merge note — the write path
        re-reads ``auth.json`` under the file lock before writing, so a
        sibling Aelix process that updated a DIFFERENT provider entry
        between this instance's load and this write does NOT lose its
        changes. The merge is shallow per top-level provider id.
        """

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
        """Drop the entry for a provider (persists immediately).

        Sprint 6e W6 (W4 m4): cross-process merge note — the write path
        re-reads ``auth.json`` under the file lock before writing, so a
        sibling Aelix process that updated a DIFFERENT provider entry
        between this instance's load and this write does NOT lose its
        changes. The merge is shallow per top-level provider id.
        """

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


    # === Sprint 6e (P-133) — layered cascade ===
    # Pi parity: ``coding-agent/src/core/auth-storage.ts:191-516``.

    # ── Runtime override layer ─────────────────────────────────────────
    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        """Pi parity: ``auth-storage.ts:220-222`` ``setRuntimeApiKey``.

        In-memory only — NOT persisted to ``auth.json``. Used by the
        future ``--api-key <provider>:<key>`` CLI flag (Sprint 6f).
        """

        self._runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        """Pi parity: ``auth-storage.ts:227-229`` ``removeRuntimeApiKey``."""

        self._runtime_overrides.pop(provider, None)

    # ── Fallback resolver ──────────────────────────────────────────────
    def set_fallback_resolver(self, resolver: FallbackResolver) -> None:
        """Pi parity: ``auth-storage.ts:235-237`` ``setFallbackResolver``.

        Last-resort ``(provider) -> str | None`` callback. Sprint 7+
        uses it for models.json custom-provider keys.
        """

        self._fallback_resolver = resolver

    # ── Existence + status checks ──────────────────────────────────────
    async def has_auth(self, provider: str) -> bool:
        """Pi parity: ``auth-storage.ts:331-337`` ``hasAuth``.

        Returns :data:`True` if ANY of these layers has a key for
        ``provider``: runtime override, stored credentials (api_key OR
        oauth), env var, or fallback resolver. Does NOT refresh OAuth
        tokens.
        """

        await self._ensure_loaded()
        if provider in self._runtime_overrides:
            return True
        if self._data.get(provider) is not None:
            return True
        # Env layer — Sprint 6b ``get_env_api_key``.
        from aelix_ai.providers._env_api_keys import get_env_api_key

        if get_env_api_key(provider):
            return True
        if self._fallback_resolver is not None:
            try:
                if self._fallback_resolver(provider):
                    return True
            except Exception as exc:  # noqa: BLE001
                self._errors.append(exc)
        return False

    async def get_auth_status(self, provider: str) -> AuthStatus:
        """Pi parity: ``auth-storage.ts:342-361`` ``getAuthStatus``.

        Reports the source of auth for ``provider`` WITHOUT exposing
        the credential value or refreshing OAuth tokens. Precedence:

        1. stored (``configured=True``) — auth.json
        2. runtime (``configured=False``, label="--api-key") — runtime override
        3. environment (``configured=False``, label=ENV_VAR_NAME) — env var
        4. fallback (``configured=False``, label="custom provider config")
        5. unconfigured (``configured=False``, no source)

        Pi only reports ``configured=True`` for the ``stored`` source —
        the other sources mean a key is available but not persisted.
        """

        await self._ensure_loaded()
        if self._data.get(provider) is not None:
            return AuthStatus(configured=True, source="stored")
        if provider in self._runtime_overrides:
            return AuthStatus(configured=False, source="runtime", label="--api-key")

        from aelix_ai.providers._env_api_keys import find_env_keys

        env_keys = find_env_keys(provider)
        if env_keys:
            return AuthStatus(
                configured=False, source="environment", label=env_keys[0]
            )
        if self._fallback_resolver is not None:
            try:
                if self._fallback_resolver(provider):
                    return AuthStatus(
                        configured=False,
                        source="fallback",
                        label="custom provider config",
                    )
            except Exception as exc:  # noqa: BLE001
                self._errors.append(exc)
        return AuthStatus(configured=False)

    # ── Stored data accessors ──────────────────────────────────────────
    def list(self) -> list[str]:
        """Pi parity: ``auth-storage.ts:316-318`` ``list``.

        Returns the keys of the in-memory stored data (call :meth:`load`
        first to populate). Does NOT include runtime overrides or env
        vars.
        """

        return list(self._data.keys())

    def has(self, provider: str) -> bool:
        """Pi parity: ``auth-storage.ts:323-325`` ``has``.

        Returns :data:`True` if ``provider`` has stored credentials.
        Does NOT consult runtime/env/fallback layers (use
        :meth:`has_auth` for that).
        """

        return provider in self._data

    def get_all(self) -> dict[str, dict[str, Any]]:
        """Pi parity: ``auth-storage.ts:366-368`` ``getAll``.

        Returns a SHALLOW copy of the stored data so callers can iterate
        without holding the storage lock.
        """

        return dict(self._data)

    # ── Error accumulator ──────────────────────────────────────────────
    def drain_errors(self) -> list[Exception]:
        """Pi parity: ``auth-storage.ts:370-374`` ``drainErrors``.

        Returns + clears the accumulated load/save/cascade errors.
        Sprint 6e records errors from: failed fallback resolver
        invocations, failed OAuth refresh, and failed file IO.
        """

        drained = list(self._errors)
        self._errors = []
        return drained

    # ── High-level login/logout ────────────────────────────────────────
    async def login(
        self, provider_id: str, callbacks: OAuthLoginCallbacks
    ) -> None:
        """Pi parity: ``auth-storage.ts:379-387`` ``login``.

        Looks up the OAuth provider, calls ``provider.login(callbacks)``,
        and persists the result via :meth:`set_oauth`. Raises
        :class:`RuntimeError` if ``provider_id`` is unknown.
        """

        from aelix_ai.oauth._registry import get_oauth_provider

        provider = get_oauth_provider(provider_id)
        if provider is None:
            raise RuntimeError(f"Unknown OAuth provider: {provider_id}")

        credentials = await provider.login(callbacks)
        await self.set_oauth(provider_id, credentials)

    async def logout(self, provider: str) -> None:
        """Remove ALL of a provider's auth that this instance controls.

        Drops the persisted ``auth.json`` entry (:meth:`remove`) AND the
        in-memory **runtime override** (:meth:`remove_runtime_api_key`). The
        runtime override is checked FIRST by :meth:`get_api_key_cascade` /
        ``has_configured_auth``, so leaving it behind kept a logged-out
        provider's models available for the rest of the session (they never
        disappeared from ``/model`` / ``/scoped-models``) — clearing it makes a
        user-driven ``/logout`` fully de-authorize the provider now.

        Out of scope (``logout`` cannot delete these): an API key in the
        **environment** / ``.env`` (:mod:`aelix_ai.providers._env_api_keys`)
        and a ``models.json`` / dynamically-registered provider config are
        separate auth sources — the ``/logout`` flow warns when an env key
        survives. Pi parity: ``auth-storage.ts:392-394`` ``logout`` removes only
        the persisted entry; also clearing the runtime override (the layer the
        cascade checks first) is an additive correctness fix — the runtime
        override mechanism itself is pi-parity, only clearing it on logout
        diverges. :meth:`remove` stays a pure persisted-drop.
        """

        await self.remove(provider)
        self.remove_runtime_api_key(provider)

    # ── Layered cascade ────────────────────────────────────────────────
    async def get_api_key_cascade(
        self,
        provider_id: str,
        *,
        include_fallback: bool = True,
        stored_owns: bool = False,
    ) -> str | None:
        """Pi parity: ``auth-storage.ts:455-516`` ``getApiKey`` with
        ``includeFallback`` flag.

        Layered resolution (Pi-strict precedence):

        1. Runtime override (:meth:`set_runtime_api_key`).
        2. Stored API key (``auth.json`` ``type=api_key``).
        3. Stored OAuth (``auth.json`` ``type=oauth``) — auto-refreshes
           via :meth:`get_oauth_api_key` if expired.
        4. Environment variable (Sprint 6b ``get_env_api_key``).
        5. Fallback resolver (if ``include_fallback`` is True).

        Returns :data:`None` if no layer has a key.

        ``stored_owns`` (#363 / ADR-0251 §2.2): an ``auth.json`` entry owns
        its provider whatever it yields, so steps 4-5 are never asked when one
        exists. An entry that gives no key RAISES instead of returning
        :data:`None` or an empty string: a failed OAuth refresh raises
        :class:`OAuthRefreshError`; an ``api_key`` entry whose key is empty or
        resolves empty (``"!true"``) or whose ``!command`` fails (``"!false"``,
        the error chained), an OAuth record whose OAuth provider is
        not registered in this process (expired or not) and an entry of an
        unknown type raise :class:`StoredCredentialError`. "No key" is not
        enough: a request with no key lets an adapter read the environment
        itself (``openai_responses.py``, ``openai_completions.py``, the google
        adapters, the Anthropic SDK) - which sent an exported vendor key to a
        gateway ``models.json`` re-pointed. The request path
        (``ModelRegistry._request_api_key``) passes it; the default keeps every
        other caller's answer (these entries fall through to the environment).

        NB: This is a NEW method (Sprint 6e). The existing
        :meth:`get_api_key` is the persistence-only accessor (layer 2
        only) and stays unchanged for back-compat.
        """

        from aelix_ai.oauth._resolve_config import resolve_config_value

        await self._ensure_loaded()

        # 1. Runtime override.
        runtime_key = self._runtime_overrides.get(provider_id)
        if runtime_key:
            return runtime_key

        # 2. Stored API key (Sprint 6e W6 P-141: resolve_config_value
        # honors Pi's ``!<cmd>`` shell + env-var-name indirection).
        entry = self._data.get(provider_id)
        if entry is not None and entry.get("type") == "api_key":
            key = entry.get("key")
            if isinstance(key, str) and key:
                if not stored_owns:
                    return resolve_config_value(key, self._resolve_cache)
                try:
                    resolved = resolve_config_value(key, self._resolve_cache)
                except Exception as exc:
                    # #363: a ``!command`` that FAILS ("!false") sent nothing, but
                    # the raw subprocess text named neither the entry nor /login.
                    # The cause stays chained; the message names only how it
                    # failed - never the command line (it may hold a key) or output.
                    raise StoredCredentialError(
                        provider_id,
                        f"is an api_key whose !command failed ({_helper_failure(exc)})",
                        self._path,
                    ) from exc
                if not resolved:
                    # #363: "" would read as "no key" and the adapter would
                    # send the environment's (Codex pass 2: ``"!true"``).
                    raise StoredCredentialError(
                        provider_id,
                        "is an api_key whose value resolves to an empty key",
                        self._path,
                    )
                return resolved
            if stored_owns:
                raise StoredCredentialError(provider_id, "is an api_key with no key", self._path)
            # Sprint 6e W6 (W4 m5): log when a stored ``api_key`` entry
            # has an empty/non-string key field — silent ``None`` here
            # indicates a malformed auth.json that the operator should
            # see.
            _LOG.debug(
                "auth.json entry for %r has type=api_key but empty/non-string key",
                provider_id,
            )

        # 3. Stored OAuth (auto-refresh).
        if entry is not None and entry.get("type") == "oauth":
            if stored_owns:
                from aelix_ai.oauth._registry import get_oauth_provider as _registered

                if _registered(provider_id) is None:
                    # #363: unexpired, the cascade fell through to the env; expired,
                    # the refresh failed as "Unknown OAuth provider". Neither is a key.
                    raise StoredCredentialError(
                        provider_id,
                        f"is an OAuth login, but no OAuth provider {provider_id!r} is "
                        "registered in this session",
                        self._path,
                    )
            try:
                oauth_key = await self.get_oauth_api_key(provider_id)
                if oauth_key:
                    return oauth_key
            except Exception as exc:  # noqa: BLE001
                self._errors.append(exc)
                # Sprint 6e W6 (P-142): Pi parity (auth-storage.ts:487-498).
                # On refresh failure, re-read auth.json in case a sibling
                # process refreshed successfully (cross-process race
                # recovery). If the sibling-written creds are now
                # non-expired, return THAT token. Otherwise return
                # ``None`` immediately — Pi-strict does NOT fall through
                # to env/fallback on refresh failure.
                try:
                    await self.load()
                    re_entry = self._data.get(provider_id)
                    if re_entry is not None and re_entry.get("type") == "oauth":
                        from aelix_ai.oauth.types import OAuthCredentials

                        creds = OAuthCredentials.from_json(
                            {k: v for k, v in re_entry.items() if k != "type"}
                        )
                        if int(time.time() * 1000) < creds.expires:
                            from aelix_ai.oauth._registry import (
                                get_oauth_provider,
                            )

                            provider = get_oauth_provider(provider_id)
                            if provider is not None:
                                return provider.get_api_key(creds)
                except Exception as reload_exc:  # noqa: BLE001
                    self._errors.append(reload_exc)
                # Pi parity: auth-storage.ts:498 — returns undefined
                # here, does NOT fall through to env/fallback on
                # refresh failure. The request path asks for the error
                # instead (#363): "no key" would let the adapter read the env.
                if stored_owns:
                    raise OAuthRefreshError(provider_id, exc) from exc
                return None
            if stored_owns:
                raise StoredCredentialError(
                    provider_id, "is an OAuth login that gave no key", self._path
                )

        if stored_owns and entry is not None:
            raise StoredCredentialError(
                provider_id, f"has type {entry.get('type')!r}, which aelix cannot use", self._path
            )

        # 4. Environment variable.
        from aelix_ai.providers._env_api_keys import get_env_api_key

        env_key = get_env_api_key(provider_id)
        if env_key:
            return env_key

        # 5. Fallback resolver (gated by include_fallback).
        if include_fallback and self._fallback_resolver is not None:
            try:
                fallback_key = self._fallback_resolver(provider_id)
                if fallback_key:
                    return fallback_key
            except Exception as exc:  # noqa: BLE001
                self._errors.append(exc)

        # Sprint 6e W6 (W4 m5): debug log when no layer resolved a key
        # to aid diagnosis of misconfigured deployments.
        _LOG.debug(
            "get_api_key_cascade(%r) resolved to None (no layer provided a key)",
            provider_id,
        )
        return None


__all__ = ["AuthStorage", "OAuthRefreshError", "StoredCredentialError", "default_auth_path"]
