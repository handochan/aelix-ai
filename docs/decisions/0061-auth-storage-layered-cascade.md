# 0061. AuthStorage Layered Cascade (12-Method Surface)

Status: Accepted (Sprint 6e / Phase 4.5 / W6 shipped)

## Context

Pi `packages/coding-agent/src/core/auth-storage.ts` (SHA `734e08e`,
lines 154-516) exposes a **layered key-resolution cascade** on top
of the durable `auth.json` JSON layer (Sprint 6c ADR-0053). Sprint 6c
shipped only the persistence layer (`load`/`save`/`get_oauth`/
`set_oauth`/`get_api_key`/`set_api_key`/`remove`/`get_oauth_api_key`)
and deferred the cascade — owner
`_PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]`.

Sprint 6e closes the carry-forward. The cascade adds 12 methods that
together implement Pi's strict precedence:

```
runtime override (set_runtime_api_key)
  ↓
stored api_key (Sprint 6c persistence)
  ↓
OAuth (auto-refresh on expiry)
  ↓
environment variable (Sprint 6b _env_api_keys)
  ↓
fallback resolver (FallbackResolver callback, optional)
```

## Decision

Aelix `AuthStorage` gains 12 new methods plus three supporting
types — `AuthStatus`, `AuthSource`, `FallbackResolver`.

### Supporting types (Pi `auth-storage.ts:36-40` + `:194`)

```python
AuthSource = Literal[
    "stored",
    "runtime",
    "environment",
    "fallback",
    "models_json_key",     # tracked but unused until models.json plumbing
    "models_json_command", # ditto (Sprint 6f+)
]

FallbackResolver = Callable[[str], "str | None"]

@dataclass(frozen=True)
class AuthStatus:
    configured: bool
    source: AuthSource | None = None
    label: str | None = None
```

`AuthStatus` is **frozen** to match Pi's `readonly` discriminated
union shape. `configured` is `True` only for the `"stored"` source
(Pi parity: runtime/env/fallback are reported with `configured=
False` because the value hasn't been persisted).

### 12 cascade methods (Pi parity)

| Pi method | Aelix method | Behavior |
|---|---|---|
| `setRuntimeApiKey(provider, key)` | `set_runtime_api_key(provider, key)` | In-memory `_runtime_overrides` dict (NOT persisted) |
| `removeRuntimeApiKey(provider)` | `remove_runtime_api_key(provider)` | drop from runtime dict |
| `setFallbackResolver(resolver)` | `set_fallback_resolver(resolver)` | last-resort `(provider) → str \| None` callback |
| `hasAuth(provider)` | `has_auth(provider)` | `runtime → stored → OAuth → env → fallback` boolean (per Pi `auth-storage.ts:474-490`) |
| `getAuthStatus(provider)` | `get_auth_status(provider)` | Returns `AuthStatus` populated per-source |
| `list()` | `list()` | Stored provider ids (Pi `Object.keys`) |
| `has(provider)` | `has(provider)` | provider id in stored data |
| `getAll()` | `get_all()` | Shallow copy of stored data dict |
| `drainErrors()` | `drain_errors()` | Returns + clears accumulated load/save errors |
| `login(providerId, callbacks)` | `login(provider_id, callbacks)` | Resolves provider, runs `.login()`, persists |
| `logout(provider)` | `logout(provider)` | Alias for `remove` |
| `getApiKey(provider, {includeFallback})` | `get_api_key_cascade(provider, include_fallback)` | Full layered cascade (see below) |

### `get_api_key_cascade` precedence (Pi `auth-storage.ts:455-516`)

1. `_runtime_overrides[provider]` → return.
2. Stored `api_key` entry → return.
3. Stored `oauth` entry:
   - If expired and refreshable → refresh (under cross-process lock)
     + persist → return access token.
   - On refresh failure → **reload-and-retry** then return `None`
     (P-142 below); do NOT fall through to env/fallback.
4. Sprint 6b `get_env_api_key(provider)` → return env value.
5. If `include_fallback=True` and `_fallback_resolver` is set → call
   resolver(provider) → return.
6. Otherwise return `None`.

#### Aelix strengthenings over Pi raw

| Finding | Subject | Resolution |
|---|---|---|
| **P-141** | Pi `resolveConfigValue` (the `!cmd` + env-ref expansion helper) was missing from the initial port | New `_resolve_config.py` ports `resolveConfigValue` (`!cmd`, `${ENV}`) so models.json entries cascade correctly |
| **P-142** | OAuth refresh failure leaked into env/fallback path | Pi `auth-storage.ts:480-490` returns `undefined` (does NOT fall through). Aelix mirrors: refresh failure returns `None` without consulting env/fallback. Includes Pi's **reload-and-retry** (second process may have refreshed) |
| **W4 m4** | `_fallback_resolver` invocation didn't try/except | Wrapped in `try/except` so a raising resolver doesn't blow up the cascade — Pi `auth-storage.ts:508-514` catches `Error` and emits to `drainErrors` |
| **W4 m5** | Cascade returned without telemetry on which layer hit | Each branch emits a DEBUG log line (`auth_cascade.runtime`, `auth_cascade.stored`, …) for production diagnosability |

### `resolve_config_value` helper (P-141)

Pi `resolveConfigValue(value, env)` expands two notations:

- `!cmd:<shell-command>` → spawn shell, capture stdout (Pi `child_process.execSync`).
- `${ENV_VAR}` → substitute from `os.environ` (or fail loudly with
  `RuntimeError` if undefined).

Aelix `aelix_ai.oauth._resolve_config.resolve_config_value` ports
both — the `!cmd` path uses `subprocess.run` with shell=True (Pi
parity verbatim — yes, this is intentional, models.json is a
trust boundary the user owns).

### Sprint 6e W6 must-fix integration

| Finding | Subject | Resolution |
|---|---|---|
| **W4 m4** | Cross-process merge docstring missing | Documented in `set_oauth` / `set_api_key` — the second process re-reads `auth.json` post-flock and merges if the dict shape diverged |
| **W4 m5** | Debug-logging carrier for cascade misses | `logger.debug(...)` on each cascade branch |
| **W4 m9** | XDG_CONFIG_HOME compliance (already in Sprint 6c W6) | Carried forward to cascade — `default_auth_path` unchanged |

### Closure pin assertions

```python
# tests/pi_parity/test_phase_4_5_strict_superset.py
required = {
    "set_runtime_api_key", "remove_runtime_api_key",
    "set_fallback_resolver", "has_auth", "get_auth_status",
    "list", "has", "get_all", "drain_errors",
    "login", "logout", "get_api_key_cascade",
}
for name in required:
    assert hasattr(AuthStorage, name)

# AuthSource enum cardinality:
set(get_args(AuthSource)) == {
    "stored", "runtime", "environment",
    "fallback", "models_json_key", "models_json_command",
}

# _PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]
# is marked CLOSED (carried for historical traceability)
```

## Consequences

- The cascade is the **canonical** key resolution path going
  forward. New callers MUST use `get_api_key_cascade` unless they
  specifically want to bypass runtime/env/fallback layers (in
  which case the original `get_api_key` persistence-only accessor
  stays available — Pi parity: Pi has the same partition via
  `includeFallback`).
- The runtime override is the test-suite's only way to inject keys
  without persisting them; previously tests had to write/cleanup
  `auth.json` per case.
- The fallback resolver is the foothold for the future `--api-key
  <provider>:<key>` CLI flag (Sprint 6f) — the CLI hands a closure
  to `set_fallback_resolver` and the cascade picks it up.
- The reload-and-retry on refresh failure (P-142) means two
  concurrent `aelix` processes can both attempt a refresh; the
  second one finds the first's result on re-read and avoids a
  spurious "no key" return.
- The `_resolve_config_value` helper unblocks Sprint 6f's
  models.json plumbing — once `models_json_key` / `models_json_command`
  sources land, the cascade already knows how to resolve them.

### Deferred to Sprint 6f

- `models_json_key` and `models_json_command` AuthSource values
  are wired into the Literal but un-consumed until models.json
  plumbing lands.
- `--api-key <provider>:<key>` CLI flag (surfaces
  `set_runtime_api_key` and `set_fallback_resolver` to the command
  line).
- Cross-platform locking (Windows `msvcrt.locking` — inherited from
  ADR-0053).

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6e — cascade
  carry-forward now CLOSED).
- ADR-0048 — Pi shared utilities (the Sprint 6b `_env_api_keys`
  table this cascade consumes).
- ADR-0053 — AuthStorage + secrets (Sprint 6c persistence layer;
  amended Sprint 6e to mark layered-resolution carry-forward
  RESOLVED).
- ADR-0055 — Phase 4.3 strict superset closure (the
  `_PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]`
  entry this ADR closes).
- ADR-0059 — Copilot OAuth (a cascade consumer).
- ADR-0060 — Codex OAuth (a cascade consumer).
- ADR-0062 — `aelix auth` CLI subcommand (the public surface that
  routes through this cascade).
- ADR-0063 — Phase 4.5 strict superset closure.

## Phase

Sprint 6e / Phase 4.5 (shipped — 12-method cascade live; models.json
sources tracked but Sprint 6f-deferred).
