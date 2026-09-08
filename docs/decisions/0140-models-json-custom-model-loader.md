# 0140. models.json Custom-Model Loader + Config-Value Auth Indirection

Status: Accepted (**#227 amendment 2026-09-08** — the `!command` half of this
ADR's `apiKey` contract no longer assumes `sh`: on win32 it resolves a shell
chain, and `resolve_config_value`'s trim becomes `.strip()` on every platform,
matching `resolve_config_value_uncached`. Both are recorded in ADR-0238, which
owns that spawn site; **#240 amendment 2026-09-08** — the registry's `!command`
resolution is cached per `ModelRegistry` (successes only, cleared on reload and
on a failed interactive turn); the env/literal branch and
`get_api_key_for_provider` stay uncached)
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
- `resolve_config_value_or_throw(value, description, *, cache=None)` — Pi
  `resolveConfigValueOrThrow`. Raises `ValueError` with Pi-verbatim
  messages only on the command branch (env/literal always resolves).
  `cache` is the #240 amendment below; keyword-only, so every pre-#240 call
  site keeps its signature.
- `resolve_headers_or_throw(headers, description, *, cache=None)` — Pi
  `resolveHeadersOrThrow`; threads `cache` into each value.
- **#240 amendment (2026-09-08).** `get_api_key_and_headers` is the harness's
  per-request auth callback and re-forked a shell at all three of its
  resolution sites on every request (measured on darwin: 2 spawns, 8.44 ms of
  blocked event loop per call; one PowerShell start per distinct `!command` on
  a box that lands there — 431.8 ms with pwsh 7.6.5 on macOS, 5.1 unmeasured).
  `ModelRegistry` now owns a `dict[str, str]` (`_command_value_cache`) passed
  into all three. The key is the FULL `"!cmd"` string where
  `resolve_config_value` keys on `value[1:]`. **The separation that holds is
  ownership, not the key space**: each family builds its own dict, and nothing
  in the wiring shares one. The keys are not provably disjoint — `value[1:]`
  ranges over every string, so an auth-family `"!!cmd"` writes the strict key
  `"!cmd"`, and a shared dict would let #242's cached `""` be read back as a
  credential (measured in the #240 review; pinned by T5b in
  `tests/oauth/test_resolve_config.py`). An earlier draft of this amendment
  claimed disjointness; that was wrong. Only a success with non-empty output is stored — a failure, a
  timeout, a terminal stop (#226) and empty output all raise and store nothing,
  so Pi's negative caching is NOT adopted. Invalidated by `_load_models`
  (hence `refresh`/`reset`/`register_provider`/`unregister_provider`/`/login`)
  and by a public `clear_config_value_cache()`, which drops the values without
  a `models.json` re-read. No TTL: the trigger is a rejection, not a clock.
  `get_api_key_for_provider` and `get_provider_auth_status` stay uncached.
  Divergence from Pi, whose registry path is uncached and whose auth path
  caches globally including failures (ADR-0235 permits it, no new ADR).
- **#240 scope of the failed-turn clear.** It fires wherever a turn ends, in
  the TUI's `_input_loop` and in print mode, whenever the TERMINAL assistant
  message carries `stop_reason == "error"`. The trigger is that message and not
  a raised exception: every shipping adapter converts a provider failure into an
  `AssistantErrorEvent`, so a 401 returns normally out of `harness.prompt`
  (measured in the #240 review against a real `AgentHarness`). An earlier draft
  keyed the clear off `except Exception`, which made it inert for exactly the
  case it was written for. `"aborted"` is excluded — that is a user's Esc or a
  signal, and says nothing about a credential. A non-Anthropic 401 is never
  typed as an auth error by the time either surface sees it, so the trigger is
  any errored turn rather than an auth-specific one.

  Print mode is NOT "one-turn" in the sense that matters: `aelix -p` is one
  USER turn but many API requests over unbounded wall time, which is precisely
  the window in which a short-lived helper token (`!gcloud auth
  print-access-token`, ~1 h) expires. It gets the same recovery, after the
  initial message and after each residual message; there is no `/reload` and no
  `/login` there. The subagent channels share the registry and remain
  child-owned: they end with their child and rely on the parent's recoveries and
  process exit.
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
