# 0140. models.json Custom-Model Loader + Config-Value Auth Indirection

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Gap-inventory **P0 #4**: the `models.json` custom-model loader was
UNIMPLEMENTED. `model_registry.py` raised `NotImplementedError` for any
non-`None` `models_json_path`, and `entry.py:311` called `create()` with
no path — so a user's `~/.aelix/agent/models.json` did nothing. This
blocked **all** user-defined providers/models, custom base URLs, header
packs, per-provider/model `compat` overrides, and the `apiKey` env-var /
`!command` indirection. The coupled P1 (`getApiKeyAndHeaders` /
`getProviderAuthStatus` config-value indirection) was the same miss.

**Scope boundary (deliberate):** a `models.json` custom provider reuses
the EXISTING API adapters via its `api` field (`openai-completions`,
`anthropic-messages`, …). So the loader does **not** need pi's
`registerApiProvider` / `streamSimple` dynamic-provider subsystem — that
is the separate **P0 #7** (extensions-api) item. This ADR is the loader +
schema/semantic validation + config-value indirection + auth wiring.

**Process note:** per the ADR-0138/0139 lesson (delicate exact ports are
unreliable to delegate), the full pi sources — `model-registry.ts`
(`loadCustomModels`, `parseModels`, `loadBuiltInModels`, `mergeCustomModels`,
`applyModelOverride`, `mergeCompat`, `validateConfig`, `stripJsonComments`,
the TypeBox schema, `storeProviderRequestConfig`/`storeModelHeaders`,
`getApiKeyAndHeaders`/`getProviderAuthStatus`/`hasConfiguredAuth`/
`getApiKeyForProvider`/`getProviderDisplayName`, `refresh`) and the entire
`resolve-config-value.ts` — were fetched into the main context via
`raw.githubusercontent.com` at the pin and ported by hand.

**Reuse-first (memory lesson "check for existing utils before porting"):**
`aelix_ai/oauth/_resolve_config.py` already held a partial port
(`resolve_config_value`). It was EXTENDED additively rather than re-ported.

## Decision

### 1 — `aelix_ai/oauth/_resolve_config.py` (extend, additive)

Added the pi request-time resolvers that the registry needs, leaving the
Sprint 6e `resolve_config_value` (cached, `check=True`) untouched for
`AuthStorage`:

- `resolve_config_value_uncached(value)` — Pi `resolveConfigValueUncached`.
  `!command` → trimmed stdout or `None` (non-raising; non-zero/empty → `None`,
  matching Pi `executeWithDefaultShell`); else `os.environ.get(value) or value`
  (empty env → literal, Pi `process.env[config] || config`).
- `resolve_config_value_or_throw(value, description)` — Pi
  `resolveConfigValueOrThrow`. Raises `ValueError` with Pi-verbatim
  messages only on the command branch (env/literal always resolves).
- `resolve_headers_or_throw(headers, description)` — Pi `resolveHeadersOrThrow`.
- **Review hardening:** both the cached (`resolve_config_value`) and uncached
  shell-exec paths now run through `_run_shell_command`, which bounds output
  to ~1 MB and time to 10 s (mirroring Pi's `execSync` implicit `maxBuffer`
  ENOBUFS guard + `timeout: 10000`). Python's `subprocess` has no `maxBuffer`,
  so a runaway `!command` (`!yes` / `!cat /dev/urandom`) would otherwise
  OOM/hang the host. The cached path still RAISES `CalledProcessError` on
  failure (its `AuthStorage` contract); the uncached path returns `None`.

### 2 — `aelix_coding_agent/models_json.py` (NEW, pure helpers)

Registry-state-free ports, driven by the registry via two callbacks
(`store_provider_request_config` / `store_model_headers`) so the module
never imports the registry:

- `strip_json_comments` — Pi `stripJsonComments` (verbatim two-regex impl:
  `//` line comments + trailing commas, string-literal aware; block
  comments intentionally NOT handled, matching Pi).
- `validate_models_config(parsed) -> list[(path, message)]` — schema
  validation hand-written to mirror Pi's TypeBox `ModelsConfigSchema` +
  `formatValidationPath` (dotted instance paths). Extra keys ignored
  (`Type.Object` has no `additionalProperties: false`); `compat` validated
  permissively as "an object" (Pi's union of all-optional objects).
- `validate_config_semantics(config)` — Pi `validateConfig` verbatim
  (raises `ValueError` with Pi error strings).
- `merge_compat` / `apply_model_override` / `merge_custom_models` /
  `parse_models` / `load_built_in_models` — verbatim Pi.
- `load_custom_models(path, *, callbacks) -> LoadCustomModelsResult` — Pi
  `loadCustomModels` (+ `emptyCustomModelsResult`): existsSync → read →
  strip → parse → schema → semantic → build overrides/modelOverrides +
  parse models. Every failure returns an empty result carrying a
  Pi-verbatim `error` string (built-ins still load; `getError()` surfaces it).

### 3 — `model_registry.py` (wire it in)

- Removed `NotImplementedError`. `create()` now defaults `models_json_path`
  to `<agent-dir>/models.json` (Pi `join(getAgentDir(), "models.json")`);
  `in_memory()` stays `None`; direct `ModelRegistry(s)` stays `None`.
- New `ProviderRequestConfig` dataclass + `_provider_request_configs` /
  `_model_request_headers` maps (Pi `providerRequestConfigs` /
  `modelRequestHeaders`) + `_store_provider_request_config` /
  `_store_model_headers` / `_get_model_request_key`.
- `_load_models` rewritten to Pi `loadModels`: clear the per-load maps →
  `load_custom_models` → `load_built_in_models(overrides)` →
  `merge_custom_models` → re-apply registered providers' request configs →
  OAuth `modify_models` (existing P-175 multi-error accumulation kept).
- `get_api_key_and_headers` — Pi-faithful: AuthStorage cascade →
  `models.json` `apiKey` (config-value indirection) → header layering
  (`model.headers` < provider < per-model, each resolved) → `authHeader`
  → `Authorization: Bearer`. Wrapped in try/except → `ok=False` on a
  resolution failure.
- `get_provider_auth_status` — Pi fallback to `models_json_command` /
  `environment` / `models_json_key` sources.
- `has_configured_auth` (+ `get_api_key_for_provider`) — consult
  `_provider_request_configs.api_key`.
- `get_provider_display_name` — registered/`name` precedence added.
- `ProviderConfigInput` gained `name`.

## Intentional divergences (documented, NOT bugs)

1. **Schema error message TEXT** is not byte-identical to TypeBox (paths
   and the accept/reject set match; TypeBox isn't a Python dependency).
2. **`get_provider_display_name`** keeps the built-in display map ABOVE the
   OAuth-registry name lookup (Pi checks `oauthProvider?.name` first), and
   its final fallback title-cases an unknown id (`my-prov` → `My-Prov`)
   where Pi returns the RAW id — the pre-existing Sprint 6f₁ behavior,
   retained for back-compat. Both preserve bare built-in names ("Anthropic",
   not "Anthropic (Claude Pro/Max)") — no cosmetic UI shift in this sprint;
   the full Pi precedence is a separate P2-cosmetic item.
3. **`get_api_key_and_headers` no-key behavior** changed from `ok=False`
   "No configured auth" to Pi's `ok=True` / `api_key=None` (OAuth-only
   providers attach their bearer via `model.headers`). `ResolvedRequestAuth`
   has zero production consumers outside the registry, so no blast radius;
   the auth-error path remains at the harness request layer.

## Deferred (NOT this sprint)

- Pi's dynamic `registerProvider` rewrite (`registerApiProvider` /
  `streamSimple` / full model replacement) — **P0 #7** extensions-api.
- `models.json` `providers.X.name` as a display-name source (Pi reads
  display name from `registeredProviders`, not the loader path).

## Consequences

- `~/.aelix/agent/models.json` now adds custom providers/models, overrides
  built-ins (baseUrl / compat / per-model), and supplies `apiKey` + headers
  with env-var / `!command` indirection — unblocking user-defined providers.
- Tests: new `tests/model_registry/test_models_json_loader.py` (55 tests) +
  extended `tests/oauth/test_resolve_config.py` + a `tests/model_registry/`
  conftest agent-dir guard (hermetic `create()` default-path). The two
  Sprint 6f₁ tests asserting `NotImplementedError` were rewritten.

Gate: green (see commit). Test root `tests/`, run `uv run pytest`.
