# 0053. AuthStorage and Secrets

Status: Accepted (Sprint 6c / Phase 4.3 / W6 shipped — Anthropic only;
Copilot + Codex + layered cascade deferred to Sprint 6e)

## Context

Pi has no explicit "secrets" abstraction — credentials live in
`packages/coding-agent/src/core/auth-storage.ts` (SHA `734e08e`,
~470 LOC). The file format is a `Record<provider-id, {type, ...}>` map
persisted to `<agent-dir>/auth.json` with `0o700` parent dir + `0o600`
file mode. Pi uses `proper-lockfile` (npm) for cross-process locking
with stale timeout.

Aelix has no `~/.pi/agent/` — Aelix is a separate binary with its own
config root.

## Decision

Aelix ships `aelix_ai.oauth.auth_storage.AuthStorage` (NEW class) at
`packages/aelix-ai/src/aelix_ai/oauth/auth_storage.py`.

### File format (Pi parity)

```json
{
  "anthropic": {"type": "oauth", "refresh": "...", "access": "...", "expires": 1234567890123, "scope": "user:profile"},
  "openai":    {"type": "api_key", "key": "sk-..."}
}
```

### Default path

Resolution order:

1. `AELIX_AUTH_PATH` env var (test fixtures use this).
2. `$XDG_CONFIG_HOME/aelix/agent/auth.json` (Sprint 6c W6 — W4 m9: XDG
   compliance).
3. `~/.config/aelix/agent/auth.json` (fallback).

### Permissions

- Parent dir: `0o700` (set on creation + re-chmod for idempotency)
- File: `0o600` (set at `os.open` creation + re-chmod after rename)

### Atomic write (Sprint 6c W6 — W4 M1)

Write path is:

```
<path>.tmp ← payload (O_CREAT|O_WRONLY|O_TRUNC, 0o600)
fsync(<path>.tmp)
chmod(<path>.tmp, 0o600)
os.replace(<path>.tmp, <path>)
chmod(<path>, 0o600)
```

A crash between `fsync` and `os.replace` leaves the original file
untouched. POSIX `os.replace` is atomic. Regression-pinned by
`tests/oauth/test_auth_storage.py::test_auth_storage_atomic_write_survives_simulated_crash`.

### Locking

- In-process: `asyncio.Lock` on the `AuthStorage` instance.
- Cross-process: `fcntl.flock` advisory POSIX lock on the `auth.json`
  fd. **POSIX-only** in Sprint 6c; Windows fallback (`msvcrt.locking`)
  is a Sprint 6d TODO.

Sprint 6c W6 (W4 M5): the `flock` failure handler is `except BaseException`
(not `except OSError`) so `KeyboardInterrupt` / `SystemExit` also release
the fd. Previously a Ctrl-C during `flock` could leak an open fd.

### Public surface

```python
class AuthStorage:
    async def load(self) -> None: ...
    async def save(self) -> None: ...
    async def get_oauth(self, provider_id: str) -> OAuthCredentials | None: ...
    async def set_oauth(self, provider_id: str, credentials: OAuthCredentials) -> None: ...
    async def get_api_key(self, provider_id: str) -> str | None: ...
    async def set_api_key(self, provider_id: str, key: str) -> None: ...
    async def remove(self, provider_id: str) -> None: ...
    async def get_oauth_api_key(self, provider_id: str) -> str | None: ...
```

`get_oauth_api_key` resolves OAuth credentials, refreshes if expired
(under cross-process lock), persists the refreshed credentials, and
returns the access token via `provider.get_api_key(creds)`.

## Carry-forward — Sprint 6e

### Copilot + Codex OAuth (W5 P-83 / scope partition)

`_OAUTH_DEFERRED_PROVIDERS` contains `{"github-copilot", "openai-codex"}`.
Sprint 6e adds both providers (~870 LOC Pi-side); the closure pin
(`test_pi_oauth_provider_total_equals_3`) enforces drop-from-allowlist
on the same PR.

### Layered AuthStorage cascade (W5 P-95)

Pi `core/auth-storage.ts:455-516` exposes a layered resolver:

```
runtime override (test-only setRuntimeApiKey)
  ↓
env var fallback (per-provider envvar table)
  ↓
auth.json (this Sprint 6c surface)
  ↓
fallback resolver callback (CLI prompt for missing creds)
```

Sprint 6c ships only the `auth.json` layer; the full cascade
(login/logout/setRuntimeApiKey/has_auth/get_auth_status/set_fallback_resolver/drain_errors)
is owned by `_PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]`
and ships in Sprint 6e alongside Copilot/Codex (when more layers
have real callers).

### Encryption at rest

Pi has none; Aelix carries forward. Future Sprint 7+ may add keyring
integration; out of scope for Sprint 6c.

### Connection pool reuse (W5 P-96)

Pi uses global `fetch`; Aelix uses per-call `httpx.AsyncClient`. A
Sprint 6d optimization can switch to a shared client without changing
the public Auth surface.

### Error formatting (W5 P-97 / W4 m2)

Pi `_format_error_details` has the same infinite-recursion risk for
cyclic `__cause__` / `__context__` chains. Both Pi and Aelix accept
this risk; a Sprint 6d hardening pass can add a depth cap and a
`code` / `errno` / stack carrier.

### `_high_level.get_oauth_api_key_from_credentials` callers (W5 P-98 / W4 M6 — non-callback-host)

Callers may leak refreshed tokens if they ignore the returned
`new_credentials`. Pi has the same shape; documentation-only carry-forward.

## Consequences

- The `auth.json` layer is the durable storage boundary every future
  OAuth provider routes through; Sprint 6e Copilot/Codex slot in
  without modifying `AuthStorage`.
- Atomic write guarantees `auth.json` is never half-written even
  under power loss / SIGKILL.
- POSIX cross-process locking prevents concurrent CLI invocations from
  racing on refresh — two `aelix` processes can both attempt a refresh
  and the second one finds the first's result on re-read.
- XDG_CONFIG_HOME compliance means users with non-default config roots
  (Flatpak, Nix home-manager) get the expected path.

## Related

- ADR-0034 — Pi reference version pin.
- ADR-0051 — OAuth client framework.
- ADR-0052 — Anthropic OAuth flow (the producer of credentials this
  storage persists).
- ADR-0055 — Phase 4.3 strict superset closure.

## Phase

Sprint 6c / Phase 4.3 (shipped — Anthropic + atomic write + XDG path;
Copilot/Codex + layered cascade deferred to Sprint 6e).
