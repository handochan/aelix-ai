# 0251. A provider's own `apiKey` comes before its environment variable, and the launch model is the one `/model` composes

Status: Accepted (2026-10-06); review round 1 the same day (§8): a failed OAuth refresh now fails the request, the status reports `--api-key` first, the launch is `/model`'s registry copy; review round 2 the same day (§9): every stored `auth.json` entry that gives no key fails the request, and the refresh-retry claim is withdrawn; rebased onto `547099f3` (#367, #370, #369) the same day (§10): a stored `!command` that fails names the entry too; owner decisions after the batch, 2026-10-06 (§7): the empty-stored-key strictness stays, the refresh retry follows pi in #379; step 4 stated for the anthropic adapter by ADR-0254 (2026-10-06, #374, note in §2.1)
Date: 2026-10-06
Supersedes: **ADR-0249 §2.4**'s two closing paragraphs ("The bearer is the auth cascade's,
unchanged" and "At launch only the host moves"). ADR-0249's S (a re-pointed built-in's
catalog hit adopts the models.json host) stands and is widened here.
Amends: **ADR-0140** (the `get_api_key_and_headers` / `get_provider_auth_status` order and
the provider entries the loader accepts), **ADR-0250** §2.2 (the request's key is no longer
"the AuthStorage cascade, unchanged") and §2.8 (the `/model` note that "the key order is
#363"). Dated notes in each.
Relates: ADR-0235 (the divergences in §4 are stated, not parity gaps), #365 (the extension
`api_key`, §2.3), #362 / ADR-0250 guard 1 (one input changes, §2.6).
Issue: #363. Owner decision: 2026-10-02 (issue comment, "pi 방향"); the choices the research
left open were made under it on 2026-10-06 (§7).
Tests: `tests/model_registry/test_key_order_363.py`, `tests/cli/test_launch_compose_363.py`,
`tests/cli/test_refresh_failure_sends_nothing_363.py` (review rounds 1-2),
`tests/cli/test_provider_prefix_rung.py::test_re_pointed_built_in_bearer_is_the_models_json_api_key`
(rewritten from `..._follows_the_auth_cascade`).

A built-in provider re-pointed at a gateway in `models.json`
(`providers.openai = {baseUrl: <corporate gateway>, apiKey: …}`) had two gaps after #344.
The gateway received whatever OpenAI key the auth cascade found first — an exported
`OPENAI_API_KEY`, or one a cloned repo's `.env` supplied — not the `apiKey` written for it.
And the launch took only the gateway's host from the entry: the provider `compat` and the
`modelOverrides` that `/model` applies were ignored at launch, on every built-in.

## 1. What was measured (`5dee21d1`)

Probe `/tmp/363-work/impl/probe/repro.py` (copied to `.omc/probes/363-live/impl/`), fake
keys, an isolated `AELIX_CODING_AGENT_DIR` / `AELIX_SETTINGS_PATH`, a scratch cwd per case;
`models.json` `providers.openai = {baseUrl: http://127.0.0.1:9/corp-gw/v1, apiKey: corp-gateway-fake,
compat: {supportsDeveloperRole: false}, modelOverrides: {gpt-4o-mini: {contextWindow: 1234}}}`
and `providers.anthropic = {modelOverrides: {claude-haiku-4-5: {name: "Haiku (mine)", contextWindow: 4321}}}`.
Outputs `before.out` / `after.out`:

| case | `5dee21d1` | this change |
| --- | --- | --- |
| no OpenAI key anywhere | `auth=corp status=models_json_key` | `auth=corp status=models_json_key` |
| `OPENAI_API_KEY` exported | `auth=vendor status=environment/OPENAI_API_KEY` | `auth=corp status=models_json_key` |
| `OPENAI_API_KEY` from a cwd `.env` | `auth=dotenv status=environment/OPENAI_API_KEY` | `auth=corp status=models_json_key` |
| `--api-key` (runtime) + exported | `auth=runtime status=runtime` | `auth=runtime status=runtime` |
| `auth.json` api key + exported | `auth=stored status=stored` | `auth=stored status=stored` |
| not re-pointed: `openai = {apiKey: <literal>, headers}`, `.env` key, `--model gpt-4o-mini` | `route=auth_tiebreak … host=https://api.openai.com/v1 bearer=dotenv` | `… bearer=own` |
| `openai.apiKey: "OPENAI_API_KEY"` + `.env` key | `bearer=dotenv has_route_auth=False` | `bearer=dotenv has_route_auth=False` |
| launch `openai/gpt-4o-mini` | `compat=None ctx=128000`, `/model` copy `{'supportsDeveloperRole': False}` / 1234 | equal to `/model`'s copy (`same=True`) |
| launch `anthropic/claude-haiku-4-5` (not re-pointed) | `ctx=200000 name='Claude Haiku 4.5 (latest)'`, `/model` 4321 / `'Haiku (mine)'` | `ctx=4321 name='Haiku (mine)'` (`same=True`) |
| `anthropic = {apiKey: "!echo k-fake"}` only | `Failed to load models.json: Provider anthropic: must specify "baseUrl", …` | loads; `bearer=k` |
| extension registers `openai` with models + exported key, `/model` row `openai/gpt-4o-mini` | `host=https://api.openai.com/v1 bearer=vendor` | `bearer=vendor` (unchanged, §2.3) |
| stored OAuth whose refresh raises + `models.json` `apiKey` + exported | `bearer=own` | `ok=False`, `OAuth refresh failed for openai: … Run /login to sign in to openai again.`; nothing is sent (§2.2, §8) |
| stored `api_key` `""` or `"!true"`, an OAuth record with no registered OAuth provider, an unknown entry type + `models.json` `apiKey` + exported | the exported key (`""` for `!true`, which the CLI's callback read as no key, so the adapter read the env) | `ok=False`, `The auth.json entry for openai …`; nothing is sent (§2.2, §9) |

The real CLI (print mode) against a local recorder set as `providers.openai.baseUrl`, a
literal `models.json` `apiKey`, a scratch cwd whose `.env` holds `OPENAI_API_KEY`, HTTPS
through a CONNECT recorder that refuses (`/tmp/363-work/impl/live/`, `recorder.log`; no
CONNECT was recorded in any row):

| row | `5dee21d1` | this change |
| --- | --- | --- |
| `--provider openai --model gpt-4o-mini`, `OPENAI_API_KEY` exported and in the `.env` | `bearer=vendor(exported)` | `bearer=models.json` |
| same, the key only in the `.env` | `bearer=dotenv` | `bearer=models.json` |
| `gpt-5-mini`, `compat: {supportsDeveloperRole: false}` | `system_role=developer` | `system_role=system` |
| `gpt-5-mini`, `modelOverrides: {gpt-5-mini: {reasoning: false}}` | `system_role=developer` | `system_role=system` |

## 2. Decision

### 2.1 The key order is pi's

`ModelRegistry.get_api_key_and_headers`, `get_api_key_for_provider` and
`get_provider_auth_status` follow one order (`ModelRegistry._request_api_key`):

1. `--api-key` (a runtime override);
2. `auth.json` (`/login`; an api key or OAuth);
3. the `models.json` provider `apiKey` (literal, env-var name or `!command`);
4. the environment (`ENV_API_KEYS`);
5. an extension `register_provider` `api_key` (§2.3).

pi, at `b223082bb`: a runtime key first (`packages/ai/src/auth/resolve.ts:56-68`); a stored
credential owns the provider (`:70-87`); ambient credentials only when nothing is stored
(`:89-92`); and `composeApiKeyAuth.resolve` hands a configured `apiKey` to the built-in
resolver as the credential, asking the environment only when there is none
(`packages/coding-agent/src/core/provider-composer.ts:457-479`; `check()` at `:438-456`
follows the same order). pi's docs state it (`docs/models.md:23`, added by `25cc5c7bf`,
2026-09-22).

**History, corrected.** pi changed to this order in `9993c9690` (2026-07-14, "feat(coding-agent):
replace model registry with model runtime"), not in the 2026-06-22 model-registry merge
(`abbd91169` → `732bb1617`) that the #363 owner comment named: at `732bb1617` and at
`9993c9690^`, coding-agent's `getApiKeyAndHeaders` was still `authStorage.getApiKey(...) ??
providerConfig.apiKey` (`model-registry.ts:707-716` @ `732bb1617`, `:727-733` @
`9993c9690^`). ADR-0249 §2.4's "(b) is pi's order" was true of pi before `9993c9690` and of
the `734e08e` pin ADR-0140 ported.

The rule is not limited to re-pointed providers: any provider whose `models.json` entry
carries an `apiKey` sends that key ahead of its environment variable.

> **2026-10-06 note (#374, ADR-0254):** step 4 is the provider's OWN variables and nothing
> else. Until then the Anthropic SDK added a step of its own after it: with no key resolved
> it read `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` (and its credential chain) for every
> `anthropic-messages` provider, so a custom gateway, `fireworks` or `minimax` with no key
> received the Anthropic key. Now a request with no key and no auth header fails with
> `No API key for provider: <p>`; `ANTHROPIC_AUTH_TOKEN` is a last bearer for `anthropic`
> only.

### 2.2 A stored credential owns the provider

When `auth.json` holds an entry for the provider (or `--api-key` set one), the AuthStorage
cascade answers and nothing after it is asked. **A stored OAuth whose refresh fails makes
the request fail before anything is sent**: `get_api_key_and_headers` answers `ok=False`
with `OAuth refresh failed for <provider>: <cause>. Run /login to sign in to <provider>
again.`, the CLI's auth callback raises it, and the harness reports it as an `auth` error.
Before #363 the cascade's `None` fell through to the `models.json` `apiKey`. pi:
`resolve.ts:70-87` and `:142` (`refreshStoredOAuthCredential` throws
`ModelsError("oauth", "OAuth refresh failed for <id>")`; no fall-through).
`get_api_key_for_provider` answers `None` there, as pi's `getApiKeyForProvider` catches the
error (`model-registry.ts:198-204` @ b223082bb).

"No key" alone is not enough (review round 1, R1, §8): a request with no key and no headers
is "no opinion" to the CLI's callback, and the adapter then reads the environment itself, so
the exported vendor key went to the gateway. The cascade therefore takes
`raise_on_refresh_failure=True` from the request path and raises
`aelix_ai.oauth.OAuthRefreshError`. Its message keeps the cause's text so the user sees why
the refresh failed. It is **not retried**: the callback's raise becomes
`AgentHarnessError("auth")` in `_make_stream_fn` before an assistant message exists, and the
harness's auto-retry reads only the last assistant message (`harness/core.py`, the
`_is_retryable_error` loop). pi does retry some (§4). Every other caller of the cascade
keeps its `None`.

**Every stored entry owns its provider, whatever it yields** (review round 2, §9). The flag
is `stored_owns=True` now, and the cascade raises `aelix_ai.oauth.StoredCredentialError`
(`OAuthRefreshError` is one) for each `auth.json` entry that gives no key:

- an `api_key` entry whose key is empty, or resolves empty — `"!true"`, a `!command` that
  prints nothing (Codex pass 2 C1's exact input) — or whose `!command` fails (`"!false"`,
  rebase onto `547099f3`: it already sent nothing, but the raw `CalledProcessError` text
  named neither the entry nor `/login`, and repeated the command line; now the message
  says `is an api_key whose !command failed (exit status <n>)`, with the helper's error
  chained as the cause and neither its command line nor its output in the text);
- an OAuth record whose OAuth provider is not registered in this process, expired or not
  (unexpired, it fell through to the environment; expired, it failed as `Unknown OAuth
  provider`; verify round 2's M9 pinned it);
- an OAuth record whose provider gives no key, and an entry of a type aelix does not know.

The message names the entry, `/login` and the `auth.json` path: `The auth.json entry for
openai is an api_key whose value resolves to an empty key. A stored entry is the only
credential used for openai, so a request has no key. Run /login to sign in to openai again,
or remove the openai entry from <path>.` `get_api_key_for_provider` answers `None` for each
and `get_provider_auth_status` says `stored`. Before, each answered the environment's key
(or `""`, which the CLI's callback reads as "no opinion", so the adapter read the
environment) and the exported vendor key went to the gateway. pi gives no key for the OAuth
and unknown-type entries (`resolve.ts:70-87`: a stored credential with no matching handler
is `return undefined`). For an empty `api_key` it does not (§4).

### 2.3 Scope limit: an extension's `api_key` stays after the environment until #365

pi puts a registration's key in step 3 too (`configuredApiKey = extension?.apiKey ??
config?.apiKey`, `provider-composer.ts:388-393`). aelix does not, yet: a registration that
brings models for a catalogued name still leaves the catalogue's rows in the registry
(#365), and step 3 would put the extension's key on them. Measured (research
`ext_takeover_probe.py`, and `test_an_extension_api_key_stays_after_the_env_until_365`):
an extension registering `openai` with models, plus an exported `OPENAI_API_KEY`, gives the
`/model` row `openai/gpt-4o-mini` `host=https://api.openai.com/v1 bearer=vendor` on
`5dee21d1`, and `bearer=ext` under an unrestricted step 3. So step 3 is the `models.json`
`apiKey` only — `ProviderRequestConfig.from_models_json` records whether the request config's
`api_key` is the `models.json` one, since `_load_models` step 3 replaces the models.json
config of a name an extension registered — and a registration's `api_key` keeps its place
after the environment. A temporary divergence from pi; when #365 replaces a taken-over
provider's rows, the registration's key can move to step 3.

A registration for the same name that carries **no** `api_key` keeps the `models.json`
`apiKey` at step 3, as pi's `extension?.apiKey ?? config?.apiKey` does (review round 1, R5).
One that carries nothing but models leaves the `models.json` request config whole (the
store's early return, as before); one that carries `headers` or `authHeader` (either alone
is enough; review round 2 pins `authHeader` alone, verify M12) replaces those two and keeps
the key. Before, the headers-only case dropped the key and the exported vendor key went out.
One that carries only an `api_key` replaces the `models.json` request config too: its key
replaces the `apiKey` (after the environment, above), and the `models.json` `headers` and
`authHeader` are replaced with nothing (pre-existing; pi merges both, §4).

### 2.4 The status reports the same order

`get_provider_auth_status`: `runtime` (`--api-key`), then `stored`; then a `models.json` `apiKey`
(`models_json_command`, `environment` + the variable it names, or `models_json_key`); then
the environment / fallback answer; then a registration's `api_key` (reported as before).
pi: `model-runtime.ts:638-648` (runtime → stored → `configuredRequestAuthStatus` →
environment). `runtime` is checked by the registry ahead of `AuthStorage.get_auth_status`,
which checks `stored` first (review round 1, R2: with both, the request carried the
`--api-key` key and the status said `stored`); the shape stays `AuthStorage`'s
(`configured=False`, label `--api-key`), where pi reports `configured: true`.
`has_configured_auth` checks its layers in the same order; it is a union, so its answer
cannot change.

### 2.5 `apiKey`-only and `authHeader`-only entries are valid

`validate_config_semantics` accepts a provider entry with no models that carries only
`apiKey` or only `authHeader`, as pi has since `9993c9690` (`provider-composer.ts:310-323`:
`!config.apiKey && … && config.authHeader === undefined` in the must-specify check). The
message for an empty entry is pi's, unchanged. Without this, step 3 could not be used to
pin one's own key ahead of the environment for a built-in unless the entry carried
something else too. Such an entry does not make the provider user-defined: only a
provider-level `baseUrl` or custom models do (ADR-0249 §2.1, ADR-0250).

### 2.6 Guard 1: the order moves no route decision, and one input changes

`has_route_auth` (ADR-0250 guard 1) is a union of layers, so the order cannot move any
route decision, and a `models.json` `apiKey` was already an own credential there (a literal
or `!command` counts; a name a `.env` supplied does not). What changes is the key the
chosen route sends: on `5dee21d1` a route guard 1 chose *because of* the `models.json` key
(`--model gpt-4o-mini`, `auth_tiebreak`) went out on the `.env` key; now it carries the key
that chose it (`test_an_auth_tiebreak_route_carries_the_key_that_chose_it`).

One input to guard 1 does change, with §2.3's second paragraph: an extension registration
that carries `headers` or `authHeader` but no key now leaves the `models.json` `apiKey` in
the request config, so a literal or `!command` one counts for that provider again — as it
does when no extension registers the name, and as pi's `configuredApiKey` counts it.

### 2.7 The launch model is the one `/model` composes

`models_json.compose_built_in_model` is the per-model body `load_built_in_models` already
had — the provider `baseUrl`, `merge_compat(model.compat, provider compat)`, then the
`modelOverrides` entry (pi `applyModelsJson` / `getAllModels`, `provider-composer.ts:325-330`,
`:571-574`). `ModelRegistry.compose_built_in(model)` applies it with the overrides of the
registry's last load, and the launch path calls it on every static catalog hit
(`cli.runtime_bootstrap._compose_catalog_model`, which replaces `_adopt_base_url_override`):
`_resolve_in_provider`, `_launch_shape` and `_find_in`, and the static-sibling backfill. So
`--model`, `--provider`, a profile's `model:`, a delegated child, `/model`'s fallback and
every other caller of `resolve_route` get the model `/model`'s copy is, field for field
(pi's launch reads its composed models, `model-resolver.ts:420`).

Field for field holds because `compose_built_in` answers with the registry's own copy when
that copy has the same `provider`, `id` and `api` (review round 1, R4, §8) — with one
exception: an exported `OPENROUTER_BASE_URL` rewrites the launch's OpenRouter `base_url`
after composition (below), and `/model`'s OpenRouter pick does not apply it, so there the
two differ in `base_url` and nothing else (Codex pass 2 C3; a known follow-up, §6). The rows
assert dataclass equality, not a field list (review round 2, §9). The registry
applies more than the `models.json` composition after `load_built_in_models`: an OAuth
provider's `modify_models` (name, window, compat, headers - Codex measured the launch
without them), a `models.json` `models` entry or an extension model that redefines the id
on the same `api`. Only when there is no such copy, or it is on another `api`, is the
catalog entry composed by `compose_built_in_model`.

What stays:

- **The catalog `api` pin** (ADR-0249 decision 3). Composition never changes `provider`,
  `id` or `api`, and the launch adopts its answer only when all three match;
  `test_resolve_model_catalog_hit_wins_over_registry` is unchanged. A `models.json`
  `models` entry that redefines a catalog id with another `api` is still not what the launch
  uses — the catalog entry is, composed (§4). One on the same `api` is `/model`'s copy, so
  the launch uses it (since round 1).
- **`OPENROUTER_BASE_URL`** still beats `providers.openrouter.baseUrl` at launch:
  `_openrouter_base` runs after composition. `/model`'s registry copy keeps
  `providers.openrouter.baseUrl` (`test_openrouter_base_url_is_the_one_field_the_launch_changes`).
  `enrich_copilot_base_url` runs after it too.
- **Fail-safe.** A registry whose `compose_built_in` raises or answers another
  provider/id/api, and a duck-typed registry without it, get #344's host-only adoption
  (`get_base_url_override`), so a re-pointed provider never falls back to the vendor host.

## 3. Consequences

- **A silent credential switch.** Anyone with both a `models.json` `apiKey` and an exported
  vendor key (or one a `.env` supplies) for the same provider now sends the `models.json`
  key. That is the point for a gateway, and pi's order — but it changes which account a
  request bills without a message. `CHANGELOG.md` calls it out.
- **Launch metadata now follows `modelOverrides` for every built-in** — `contextWindow`
  (compaction thresholds), `maxTokens`, `cost` (the cost display), `name`, `reasoning`, the
  thinking map and `compat` — including entries that only tune a provider
  (`openrouter`, `zai`, `huggingface`, `opencode*` in the owner's own file). `/model` already
  used them; a launch now agrees with it.
- A stored OAuth whose refresh fails now fails the request, naming the refresh and `/login`
  (§2.2). On `5dee21d1` the request went out on the `models.json` `apiKey`, or, with none, on
  the provider's environment variable (an exported `ANTHROPIC_API_KEY` after a refused
  Anthropic refresh: `x-api-key=ant-env`, §8), or on no key with the SDK's
  `Could not resolve authentication method`. Re-run `/login`, or `/logout` to use another
  source.

## 4. Divergences from pi, stated (ADR-0235)

- **Config-value syntax.** aelix reads a bare `apiKey` that names a set variable as that
  variable (`resolve_config_value`: `os.environ.get(name, name)`); pi reads a bare value as a
  literal and `$NAME` / `${NAME}` as the environment (`resolve-config-value.ts:11-78`,
  `docs/models.md:64`). The consequence for §2.1: an `apiKey` naming the `.env` variable
  (`"apiKey": "OPENAI_API_KEY"`) still sends the `.env` value — order (c) detaches a `.env`
  key from the gateway only for a literal or `!command` `apiKey` (pinned:
  `test_an_api_key_naming_the_dotenv_variable_still_sends_its_value`). A name that is not set
  is sent as the literal text, where pi's `$NAME` would count as not configured. Migrating
  would break documented configs; a follow-up issue is proposed.
- **The extension `api_key` stays after the environment** until #365 (§2.3).
- **The launch keeps the catalog `api`** and ignores a `models.json` `models` entry that
  redefines a catalog id with another `api` (one on the same `api` is `/model`'s copy and
  the launch uses it, §2.7); pi's launch takes the composed provider, where such an entry
  replaces the built-in (`provider-composer.ts:331-337`). Pre-existing (ADR-0249 decision 3).
- **`merge_compat` deep-merges two nested keys** (`openRouterRouting`,
  `vercelGatewayRouting`) where pi merges four (also `chatTemplateKwargs`,
  `chatTemplateArgs`; `provider-composer.ts:122-142`). Pre-existing, unchanged; composition
  at launch inherits it.
- **The status of a registration's key** is reported as `models_json_key`; pi reports
  `fallback` for an extension `apiKey` (`provider-composer.ts:718-732`). Pre-existing.
- **A registration's `headers` replace the `models.json` ones**; pi merges them
  (`configuredHeaders`: `{...config?.headers, ...extension?.headers}`, and `authHeader` is
  `extension ?? config`, `provider-composer.ts:395-401`, `:429`). A registration that
  carries only an `api_key` replaces them with nothing — the `models.json` `headers` and
  `authHeader` are dropped. Pre-existing; only the key follows pi now (§2.3).
- **A failed OAuth refresh is not retried.** pi's `lazyStream` turns the refresh's
  `ModelsError` into an error assistant message (`packages/ai/src/api/lazy.ts:4-23`,
  `:52-58` @ b223082bb) whose text keeps the cause (`utils/models-error.ts:9`, `:16-20`), and
  its auto-retry matches that text (`isRetryableAssistantError`, `utils/retry.ts:250`): a
  refresh that failed with `status=502` or `fetch failed` is retried with backoff, a `401` or
  `403` is not (`.omc/probes/363-live/fix3/pi/pi_refresh_retry.out`, run on pi's own
  modules). aelix fails at once: the auth error is raised before an assistant message
  exists. Review round 1 claimed a `502` was retried here; it was not (verify round 2,
  `retry_json.py`: `auto_retry_start=0`). A follow-up: **#379 — the owner decided on
  2026-10-06 to follow pi** (retry a refresh that failed on a transient cause such as a
  `5xx` or a network error; never retry a `401`/`403` or a `StoredCredentialError`, and
  never fall through to the environment while retrying) (§7).
- **An empty stored `api_key` fails the request.** pi's built-in `envApiKeyAuth.resolve`
  reads the environment when the stored `credential.key` is empty
  (`packages/ai/src/auth/helpers.ts:18-28`), and a `models.json` provider inherits that
  resolver when a credential is stored (`provider-composer.ts:459-463`), so pi sends the
  exported key for `{"type": "api_key", "key": ""}` or a `!command` that prints nothing —
  to the gateway, if `models.json` re-pointed the provider
  (`.omc/probes/363-live/fix3/pi/pi_stored_empty.out`). aelix fails the request and names
  the entry (§2.2): a stored entry owns its provider whatever it yields (main-loop decision,
  review round 2; **kept by the owner on 2026-10-06**, because pi's fall-through sends the
  exported vendor key to a re-pointed gateway, the leak this ADR closes) (§7).
- **The `runtime` status reports `configured=False`** (AuthStorage's shape); pi reports
  `configured: true`. Pre-existing.

## 5. Tests and sabotage

Round 1's rows and sabotage are in §8. New rows as first written:
`tests/model_registry/test_key_order_363.py` (19) and
`tests/cli/test_launch_compose_363.py` (9); `test_re_pointed_built_in_bearer_follows_the_auth_cascade`
is rewritten in place as `..._is_the_models_json_api_key` (its rows 2 and 3 pinned the
vendor and `.env` key). On `5dee21d1` (a throwaway worktree with only the test files
copied in), 16 of the 120 rows in the three files fail — every row that states the new
order, status, `apiKey`-only entry or launch composition; the rows that hold before and
after (runtime, `auth.json`, guard 1, the extension's key, the api pin, the fallbacks) pass
(`.omc/probes/363-live/impl/red_on_5dee21d1.out`).

Sabotage, one fix piece reverted at a time in that worktree
(`.omc/probes/363-live/impl/sabotage.py` / `sabotage.out`), every one red:

| piece reverted | failing rows |
| --- | --- |
| S1 the `models.json` `apiKey` before the environment | 8 |
| S2 a stored credential owns the provider | 2 |
| S3 the status reports the `apiKey` ahead of the environment | 3 |
| S4 `get_api_key_for_provider` follows the order | 3 |
| S5 the scope limit (an extension's key not in step 3) | 1 |
| S6 the models.json origin of a request config is recorded | 9 |
| S7 `apiKey`-only / `authHeader`-only entries accepted | 3 |
| S8 the launch composes (host-only instead) | 5 |
| S9 composition must keep the catalog `api` | 1 |
| S10 a failing composition falls back to host-only | 1 |
| S11 the registry keeps the built-in `modelOverrides` | 5 |
| S12 the registry keeps the built-in provider overrides | 12 |

The three files also pass under Python 3.11 (`120 passed`). The research pass, which
patched the order change alone into the whole suite, failed only the two rows rewritten
here (`2 failed, 11668 passed`); the full suite on this change is in the commit message.

## 6. What this does not close

- #365 (an extension's take-over of a built-in name; then §2.3's key can move to step 3).
- The config-value syntax (§4), proposed as its own issue.
- A failed OAuth refresh is not retried, where pi retries a `502` or a network failure (§4).
- `/model`'s OpenRouter pick does not apply `OPENROUTER_BASE_URL`, which the launch applies
  (§2.7, Codex pass 2 C3).
- The `/model` picker's detail line names the provider's environment variable
  (`API Key: OPENAI_API_KEY`) whatever source supplies the key (`tui/model_picker.py`);
  pre-existing, a follow-up.
- A registration's `headers` / `authHeader` still replace the `models.json` ones (§4).

## 7. Owner decisions recorded here

- 2026-10-02 (issue comment): key order (c), applied to every provider with a `models.json`
  `apiKey`, with the status following it; the launch builds `/model`'s composed model,
  keeping the catalog `api` pin.
- 2026-10-06 (made under that direction where the research left a choice): step 3 is the
  `models.json` `apiKey` only until #365 (§2.3); `apiKey`-only / `authHeader`-only entries
  are accepted (§2.5); the stored-OAuth refresh failure follows pi (§2.2); the config-value
  syntax stays and is stated (§4); the ADR-0249 history attribution is corrected (§2.1).
- 2026-10-06, the owner, after the batch: an empty or empty-resolving stored `api_key`
  keeps failing the request — stricter than pi, kept (§4); a stored-OAuth refresh that
  failed on a transient cause is to be retried as pi does — #379 (§4).

## 8. Review round 1 (2026-10-06)

The independent verify of `4cbff14e` failed with three blocking findings, and Codex's first
pass failed categories 1-4; they agree. Kits: `.omc/probes/363-live/verify/`,
`.omc/probes/363-live/codex/`; this round's probes and outputs are in
`.omc/probes/363-live/fix2/`.

**R1 (P1, a regression of `4cbff14e`).** For a stored OAuth whose refresh failed, the registry
answered "no key". The CLI's `_make_auth_callback` turned no key and no headers into `None`
("no opinion"), and the adapter read the environment itself. Measured on the real CLI (print
mode) with a recorder as `providers.anthropic.baseUrl` with an `apiKey`, an expired
anthropic OAuth whose refresh the CONNECT recorder refused, and `ANTHROPIC_API_KEY` exported:
`5dee21d1` sent `x-api-key=models.json`, `4cbff14e` sent `x-api-key=ant-env` (the vendor key,
to the gateway), and this change sends nothing and exits 1 with `OAuth refresh failed for
anthropic: … Run /login to sign in to anthropic again.` Codex reproduced the same on openai
in print, json, rpc, interactive, a delegated child, compaction and the branch summary
(`Bearer env-fake`); now each sends nothing (`fix2/codex/modes-head.out`,
`wire-head.out`). The TUI in a pty shows the message on a `✖` line and the recorder sees no
request. Fix: §2.2.

Entry-point sweep, every place where a missing key lets an adapter or SDK read the
environment:

- `cli/entry.py::_make_auth_callback`: `ok=True` with no key and no headers gives `None`, and
  so the adapter reads the environment. A failed refresh no longer gets here: it is
  `ok=False`, which raises.
- The harness turn (`harness/core.py::_make_stream_fn`) is shared by print, json, rpc, the
  TUI and a delegated child (a child is its own CLI process, with the same `auth.json` and
  `models.json`). A raising callback becomes `AgentHarnessError("auth")` before any adapter
  call.
- The compaction summary (`session/compaction.py`, both summary calls) and the branch
  summary (`session/branch_summarization.py`) call the same callback with no `try`, so the
  error propagates before `stream_simple`.
- The adapters keep their environment fallback for the "no opinion" case:
  `openai_responses.py::_resolve_api_key`, `openai_completions.py` (two sites),
  `google_generative_ai.py` and `google_vertex.py` (`get_env_api_key`, then the Google SDKs'
  own lookup and Vertex's application-default credentials), and the Anthropic SDK's own
  `ANTHROPIC_API_KEY`. A request reaches them only when the registry has no key and no
  headers to give, after step 4 has already asked the provider's `ENV_API_KEYS` names.
- `ModelRegistry.get_api_key_for_provider` (the extension API) answers `None` on a failed
  refresh, as pi does. An extension that then streams with no key reaches an adapter's
  environment fallback; that is the extension's own call, and it is unchanged.
- `aelix-server`'s `rpc_ws` builds a harness with no auth callback and no registry, so it
  is environment-only by design. It does not read `auth.json` and is unaffected.

**R2.** `get_provider_auth_status` puts `--api-key` first (§2.4). Measured with keys.py
`runtime+stored`: `status=stored/None` went to `status=runtime/--api-key`, and the request
still carries `runtime`.

**R3 (test gaps).** Four wrong implementations passed the whole suite on `4cbff14e` (V3,
V12, V13 in the verify kit's `sab_full.out`; the cost/reasoning mutant across all 120 #363
rows in Codex's `mutation_tests.out`; reproduced as `120 passed` each,
`fix2/repro/r3_on_prev.out`). Each now has a row that kills it:

- an extension that registers models but no key for `openai`, a `models.json` `apiKey` and an
  exported key: the request carries the `models.json` key (V3, the scope limit by name);
- the fallback resolver installed: the request carries no key (V12);
- `github-copilot/gpt-4o` with a provider compat `{supportsDeveloperRole: false}` keeps
  `supportsStore: false` and `supportsReasoningEffort: false` at launch and in `/model` (V13);
- `gpt-5-mini` with `modelOverrides` `{reasoning: false, cost {99, 98, 97, 96}, name,
  contextWindow}`: every field is asserted at launch.

**R4 (Codex cat 2).** The launch dropped an OAuth `modify_models` change that `/model` kept.
Codex's composition probe gave the launch `name='GPT-4o mini' context_window=128000
compat=None headers=None` while `/model` had the hook's values; now both have
`'OAuth account model' 8765 {'supportsDeveloperRole': False} {'x-oauth-model': …}`, and the
wire carries the hook's header from the launch as well. Fix: §2.7.

**R5.** A registration that carries no key keeps the `models.json` `apiKey` (§2.3). Measured
with keys.py `ext-headers-only+mj-key+env`: `request=vendor` went to `request=corp`.

**Docs.** The `models-json.md` step 2 and its status sentence, §1's table row, §2.2, §2.4,
§2.7's "field for field", the README row and the docstrings now describe what the code does.
The guide states both extension cases instead of "this applies to every provider" alone.

**Rows and sabotage.** The four #363 files have 139 rows: 32 fail on `5dee21d1` and 14 on
`4cbff14e` (`fix2/repro/red_on_*.out`). The 14 are the R1 wire rows (harness turn,
compaction, branch summary, `_async_main` print and json, each for anthropic and openai),
the registry's refresh row, R2, R5 and R4. Sabotage
(`fix2/sabotage_fix2.py` / `.out`) applies one piece at a time in a throwaway worktree, and
every piece turns red:

| piece | failing rows |
| --- | --- |
| R1a the request path does not ask for the error | 11 |
| R1b the cascade ignores the flag | 11 |
| R1c the registry answers `ok=True` with no key on the error | 11 |
| R1d the callback turns `ok=False` into "no opinion" | 10 |
| R1e the error falls through to `models.json` / the environment | 11 |
| R1f `get_api_key_for_provider` falls back to the environment | 1 |
| R1g the message drops `/login` | 11 |
| R2 the status checks stored before runtime | 1 |
| V3 the scope limit by provider name | 2 |
| V13 compose replaces compat instead of merging | 1 |
| C4a / C4b the launch keeps the catalog cost and reasoning | 1 / 1 |
| V12 the fallback resolver authenticates the request | 1 |
| R4a the registry copy ignored / R4b adopted on another `api` | 1 / 1 |
| R5a the key dropped / R5b kept after the environment / R5c a carry-nothing registration replaces the config | 1 / 1 / 1 |

Round 1's S1-S12, re-anchored, are all red as well. The first run found two survivors, R4b
and S11; the `api` pin row now asserts the composed `compat` and window, and both turn red.


## 9. Review round 2 (2026-10-06)

The independent verify of `a79861ce` failed with two blocking findings, and Codex's second
pass found retained behaviours, a doc gap and a surviving mutant. Kits:
`.omc/probes/363-live/verify2/`, `.omc/probes/363-live/codex2/`; this round's probes and
outputs are in `.omc/probes/363-live/fix3/` (`EVIDENCE.txt`).

**B1 (a false claim, withdrawn).** §2.2, the `OAuthRefreshError` docstring and the round-1
commit message said a `502` from the token endpoint was auto-retried. It is not: verify
measured `auto_retry_start=0`, one refresh CONNECT and `rc=1` in 0.4 s through a proxy that
answers `502` (`verify2/retry_json.py`), because the callback's raise becomes
`AgentHarnessError("auth")` before an assistant message exists. pi, read and run on its own
modules, does retry a refresh whose cause names `502` or `fetch failed` (§4); aelix's
difference is stated, not changed here.

**B2 (verify M12).** A registration carrying only `authHeader` keeps the `models.json`
`apiKey` at step 3 — the code already did; a keep-the-key rule that asked for `headers`
alone passed the whole suite. Now a row pins it, with the key as the `Authorization` header
(`test_an_auth_header_only_registration_keeps_the_models_json_key`).

**Stored owns, every case (Codex pass 2 C1, verify M9; main-loop decision).** §2.2. Reproduced
on `a79861ce` (`fix3/repro/stored_owns.prev.out`, `codex2-edges.prev.out`): `"!true"` answered
`ok=True` with `""` and status `stored`, and the gateway received `Bearer <exported>`; an empty
key, an unexpired OAuth record with no registered OAuth provider and an unknown entry type
answered the exported key. The real CLI (`fix3/live/`, rows L22-L27): each sent the exported key
to the recorder on `a79861ce` and sends nothing on this change, naming the entry; the TUI and
rpc in a pty with Codex's exact input (`pty_stored_true.py`): `a79861ce` sent
`vendor(exported)`, this change sends nothing and shows the `✖ … The auth.json entry for
openai …` line. L01-L21 are unchanged (L21's message now names the entry, not `Unknown OAuth
provider`).

**Full equality (Codex pass 2 C5, round 1's cost/reasoning mutant).** The launch rows assert
dataclass equality with `/model`'s registry copy for five built-ins (`openai`, `anthropic`,
`github-copilot`, `openrouter`, `google`), each with a `modelOverrides` entry that moves
`name`, `reasoning`, `input`, `cost`, `contextWindow`, `maxTokens`, `thinkingLevelMap` and
`compat` (and a provider `compat` / `baseUrl`), an OAuth hook moving `headers`, and the one
exception asserted separately (`OPENROUTER_BASE_URL`, §2.7). Codex's mutant
(`replace(composed, max_tokens=model.max_tokens)`) passed the 475 rows of the 14 changed test
files on `a79861ce` and fails 7 here (`fix3/repro/codex_mutant.*.out`).

**Docs.** §2.2 (the retry claim, the stored-entry rule), §2.3 and §4 (a key-only registration
replaces the `models.json` `headers` and `authHeader` with nothing), §2.6's heading and the
README rows (one input to guard 1 changes), §2.7 (the exception), §4 (`with another api`; the
two new divergences), §6; the guide and its bundled copy; CHANGELOG; the docstrings.

**Rows and sabotage.** The four #363 files have 189 rows (139 + 50); 39 fail on `a79861ce`
(`fix3/repro/red_on_a79861ce.out`): the 32 stored-entry wire rows (turn, the three summaries,
print and json) and the 6 registry rows — the expired-unregistered OAuth ones only on the
message, since `a79861ce` already sent nothing there (L21) — and the refresh-prefix row (its
import of the new class). The equality, M12, M16 and cascade-default rows pass on `a79861ce`:
they pin behaviour that was right and kill the mutants that survived it. Sabotage (`fix3/sab/sabotage_fix3.py` / `.out`), one piece at a time in a
throwaway worktree — round 2's 30 pieces, re-anchored, and 16 new ones — all 46 red:

| piece | failing rows |
| --- | --- |
| M12 the key kept only when the registration carries `headers` | 1 |
| SO1a / SO1b an empty literal / `"!true"` falls through | 1 / 9 |
| SO2a / SO2b (M9) an unregistered OAuth record falls through / gives no key | 6 / 10 |
| SO3 an OAuth login that gives no key falls through | 1 |
| SO4 an unknown entry type falls through | 5 |
| SO5 `get_api_key_for_provider` catches only a refresh error | 6 |
| SO6 the status does not say `stored` for such an entry | 1 |
| MT1 / MT2 the launch / `compose_built_in` keeps the catalog `max_tokens` | 7 / 7 |
| MT3 the catalog `input` and thinking map | 7 |
| MT4 an OAuth hook's `headers` dropped | 2 |
| MT5 the catalog `name` and window | 15 |
| M4 the refresh message repeats its prefix | 1 |
| M16 the split-turn prefix summary swallows the auth error | 10 |


## 10. Rebased onto `547099f3` (2026-10-06)

#367 (`62e2238b`), #370 (`a7435b9f`) and #369 (`547099f3`) merged while this was in review.
Conflicts were textual (ADR-0249/0250 status lines and §6, the README rows, CHANGELOG, the
project-trust guide, and line-number citations); no code hunk conflicted. Checked by hand,
then pinned (`test_the_composition_moves_no_late_or_not_found_decision_and_keeps_the_typed_key`):

- **#367's late re-resolves** (`LateRoute.hold_for`, `late_registered_route`) read the
  resolved launch model's `provider` (and build the hold from its `(id, provider)`). §2.7's
  composition keeps `provider`, `id` and `api` (anything else falls back to host-only), so a
  late decision is the same with and without a `models.json` that composes the launch model.
- **#370's not-found placeholder** under `--api-key` is a bare `Model` (`api` `unknown`)
  that no catalog entry matches, so nothing composes it, and the post-decision `--api-key` attach is a runtime key — step 1 of §2.1 —
  so the typed key is still what the composed launch model carries.
- **#369's trust gate** decides whether a project's `.aelix/settings.json` is read; that can
  change the launch *inputs* (`defaultProvider`/`defaultModel`), never the composition, which
  reads `models.json` from the agent directory only (`ModelRegistry.create`, no project
  `models.json` exists).

**A stored `!command` that fails** (round 3 verify, non-blocking). `"!false"` already sent
nothing — `resolve_config_value` raises — but the message was the raw `CalledProcessError`
text, `Command '['/bin/sh', '-c', 'false']' returned non-zero exit status 1.`, which named
neither the entry, `/login` nor the path §2.2 promises, and repeated the command line, which
may hold a key; `get_api_key_for_provider` (the extension API) raised it instead of answering
`None`. Now it is a `StoredCredentialError`: `The auth.json entry for openai is an api_key
whose !command failed (exit status 1). …` — the helper's error chained as the cause, and
neither its command line nor its output in the text (a terminal stop names its fixed reason;
a timeout, the output cap or no shell say it did not finish). Row:
`test_a_stored_helper_that_fails_names_the_entry_and_sends_nothing`; red with the wrap
removed (the raw text). The real CLI row L28 (`.omc/probes/363-live/rebase/`): no request on
either commit; the message now names the entry.
