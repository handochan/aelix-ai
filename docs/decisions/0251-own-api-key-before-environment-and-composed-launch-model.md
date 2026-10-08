# 0251. A provider's own `apiKey` comes before its environment variable, and the launch model is the one `/model` composes

Status: Accepted (2026-10-06); review round 1 the same day (§8): a failed OAuth refresh now fails the request, the status reports `--api-key` first, the launch is `/model`'s registry copy; review round 2 the same day (§9): every stored `auth.json` entry that gives no key fails the request, and the refresh-retry claim is withdrawn; rebased onto `547099f3` (#367, #370, #369) the same day (§10): a stored `!command` that fails names the entry too; owner decisions after the batch, 2026-10-06 (§7): the empty-stored-key strictness stays, the refresh retry follows pi in #379; 🔴 **amended 2026-10-08 (#379, §2.2, §4, §6, §7, §12): a stored OAuth refresh that failed on a transient cause (the token endpoint answered `429`, `500`, `502`-`504`, `520` or `524` — pi's set — or could not be reached) is retried by the turn's auto-retry, as pi retries it — a refused refresh (`400`/`401`/`403`) and every other stored-entry failure still fail at once with the message they had, and while the stored login exists no retry sends anything but the login's own refreshed token**; #379 review round 2 the same day (§12.5): the transient statuses are exactly pi's (`429`, `500`, `502`-`504`, `520`, `524`; not `501`/`505`), a refusal inside a retry sequence ends it (`auto_retry_end` false, counter reset — it leaked into the next turn) and any raise out of a retry closes it too, the setup error gets its `message_start`, the retried text is pi's (no `/login` hint), and "never on another key" holds while the stored login exists (a logout meanwhile: §12.6); #379 review round 3 the same day (§12.6): a logout during the backoff ends the retry with nothing sent (`… is an OAuth login that gave no key`), one during the failing refresh lets the retry use the next key as a new turn does — the texts that said "the next key" for both are corrected; the retry decision is the harness's own record of the setup failure, never the message's text or type, which a `message_end` hook can rebuild; a `5xx` whose body cannot be decoded keeps its status; #379 review round 4 the same day (§12.7): once a non-2xx status has arrived it decides, whatever then happens to the body (a body cut short or a read timeout too, not only a broken encoding) — a cut-short `401`/`501` was retried as a dropped connection — and an unreadable refusal's text is the same status's with an empty body; #379 review round 5 the same day (§12.8, text only): a `2xx` whose body is cut short or times out is retried as a dropped connection, one with a broken encoding fails at once; #379 review round 6 the same day (§12.9, text and rows only): only pi's Codex refresh reads a failed answer's body with `.catch`, and rows pin a `2xx` cut short or timed out at each site; #379 review round 7 the same day (§12.10, text only): the statements of how each pi refresh treats a body that fails mid-read are withdrawn from every text — aelix's rule is §12.2's: a non-2xx answer is decided by its status once it arrives (`400`/`401`/`403` refused at once; `429`, `500`, `502`-`504`, `520`, `524` retried; any other status fails), a `2xx` whose body is cut short or times out is retried as a dropped connection, a `2xx` with a broken encoding fails at once (For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.); step 4 stated for the anthropic adapter by ADR-0254 (2026-10-06, #374, note in §2.1); 🔴 **amended 2026-10-07 (#375, §11): `OPENROUTER_BASE_URL` now applies to every OpenRouter model the registry hands out, not only the launch's — §2.7's exception is gone, the launch and `/model` are equal field for field**; #375 review round 2 the same day (§11): the docs no longer name Ctrl+P, `--models` or a resumed session as broken surfaces, pi's per-model `baseUrl` is stated, and rows pin that only the provider named exactly `openrouter` moves and that nothing but `base_url` changes; #375 review round 3 (2026-10-08, §11): "M6 is equivalent" withdrawn — the launch's own call is what moves a duck-typed registry's hit, `headers` included, and a row now pins it
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
`tests/cli/test_openrouter_base_url_375.py` (§11),
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

> **Amended 2026-10-06 (#186; review rounds 2 and 3 on 2026-10-07, below).** "Its message keeps the cause's text" below still
> holds, but the text it keeps is no longer the server's raw bytes. Measured on
> `aab1f210` with a real `aelix -p` against a local token endpoint answering 502 with a
> 48 KB page: `OAuth refresh failed for openai-codex: OpenAI Codex token refresh failed
> (502): ESC[2J ESC]52;c;… ESC[?1049h …` reached stderr, and through
> `rich.Console(force_terminal=True)` onto an 80x24 emulator the prior transcript line did
> not survive. Every OAuth raise site that interpolates the server's text now quotes it
> (`aelix_ai.oauth._helpers.quote_server_text`: steering characters deleted, runs of blank
> space collapsed to one, trimmed, then cut at 512 code points - at most 513 characters
> per quoted string). The bound is per quotation, not per message: the pi-shaped
> `details=` of the Anthropic sign-in and refresh wrappers and of the Codex sign-in
> wrapper repeats every link of the exception chain, so a proxy's refusal appears there
> twice (1,138-1,225 characters measured) and a garbled status line three times
> (1,698-1,785). Transport errors are included: a proxy's CONNECT
> refusal puts its own reason phrase into `httpx.ProxyError` before any response exists,
> and h11 quotes a garbled status line in `RemoteProtocolError`, so every OAuth HTTP call
> runs inside `quoting_transport_errors`, which quotes every `httpx.HTTPError` and the
> httpx, httpcore and h11 links of its chain in place and keeps their types (review round
> 1 measured 55,044 characters with `ESC[2J` and OSC 52 from every entry point before
> it). The walk stops at the first link of any other type and at the exception the caller
> was handling when the request started, which is not the request's to rewrite. The Codex
> "missing fields" error names the response's keys and never its values (they are live
> tokens), and `get_oauth_api_key_from_credentials` neuters the cause before its existing
> 300-character cut, which covers an extension's provider too.
> An OAuth error built from an HTTP error answer keeps its status code, so nothing about
> §4's retry discussion moves. A 200 answer that is not JSON carries no status: Anthropic says
> `returned invalid JSON` and quotes the body, Codex raises the JSON parser's own message
> (`Expecting value: ...`), as pi's `response.json()` does.
>
> **Review round 2 (2026-10-07, #186).** Three gaps closed and one surface added. (1)
> `quoting_transport_errors` compared the quote with `text.strip()`, so a reason phrase
> padded with 2,048 tabs counted as unchanged and was re-raised whole (2,063 characters
> from the direct calls, 4,254-4,313 from the Anthropic wrappers, measured on `157c7b73`
> through a real local proxy); it now compares with the original text. (2) The chain walk
> followed `__context__` past the transport chain and rewrote, newlines deleted, an
> exception the caller was handling; it now stops there. It still crosses a suppressed
> context inside the chain, because httpcore's pool re-raises with `raise exc from None`
> and h11's `RemoteProtocolError` - the garbled status line itself - sits behind it. (3)
> Blank space inside the text spent the budget (`Bad Gateway`, 2,000 spaces, then the
> diagnostic kept no diagnostic); runs are collapsed now. And the MODEL request: a proxy
> that refuses the CONNECT of a model request put its reason phrase, whole, into the
> turn's error for every built-in adapter (55,053-55,075 bytes on `aelix -p`'s stderr for
> openai-codex, anthropic, openrouter and google; the TUI blank), because
> `providers/_error_hints.describe_provider_error` passed exception text through.
> That function is the one boundary all six built-in adapters' errors pass on their way
> into an `AssistantErrorEvent` (the two Google adapters now call it too), and it now
> quotes the message and the recovered cause with the same helper; OpenRouter's
> `error.metadata.raw` and the Anthropic adapter's 401/403 `_AuthError` are quoted
> beside it. An extension-registered provider's own stream function builds its own
> message and does not pass this boundary.
>
> **Review round 3 (2026-10-07, #186).** Two gaps closed, and one claim narrowed. (1) The
> pi-shaped `details=` of the Anthropic sign-in and refresh wrappers and the Codex sign-in
> wrapper followed `__context__` past the request's own chain into the exception the caller
> was handling - which the transport walk leaves alone by design - and copied it raw: a
> library caller retrying a refresh inside `except httpx.ProxyError` got 4,278 characters
> with `ESC[2J` and OSC 52 back on `cdeeb166`. `details=` now stops at that exception (read
> with `sys.exc_info()` at the wrapper's entry) and leaves it untouched; pi's `cause` is
> explicit and never reaches a caller's error either. (2) The model request's error string
> is also what the context-overflow patterns and the harness's auto-retry regex read, and
> the 512-character cut decided the class: a valid HTTP 400 putting
> `"code": "context_length_exceeded"` after a 592-character diagnostic lost its
> compact-and-retry, and a proxy's 502 page with "502 Bad Gateway" at character 644 its
> retry. The displayed string now carries, for those classifiers only, the description as
> it was built before the quoting (`aelix_ai.utils.overflow.ClassifiedErrorText`), so they
> give the answer they gave before #186; it is never shown or serialised, and a message
> rebuilt from its fields (a resumed session) is classified on its display text. A model
> request's line break now becomes a space before the quoting, so its words stay apart.
> The status claim above is the OAuth paths': a model request's error carries the status
> only where the provider's SDK puts it in its message - a plain-text 403 from Anthropic or
> an OpenAI-compatible endpoint reads as its body alone, as it did before #186.
>
> pi interpolates the body raw (`auth/oauth/openai-codex.ts:124`, `anthropic.ts:85` @
> b223082bb); this is a divergence stated under ADR-0235, not a parity gap.

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
the refresh failed. ~~It is **not retried**: the callback's raise becomes
`AgentHarnessError("auth")` in `_make_stream_fn` before an assistant message exists, and the
harness's auto-retry reads only the last assistant message (`harness/core.py`, the
`_is_retryable_error` loop). pi does retry some (§4).~~ **Amended 2026-10-08 (#379, §12):** a
refresh that failed on a transient cause is retried by the turn, as pi retries it; a refused
one still fails the turn at once as described here. Every other caller of the cascade keeps
its `None`.

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
that copy has the same `provider`, `id` and `api` (review round 1, R4, §8). Until #375 there
was one exception: an exported `OPENROUTER_BASE_URL` rewrote the launch's OpenRouter
`base_url` after composition and `/model`'s OpenRouter pick did not apply it, so there the
two differed in `base_url` (Codex pass 2 C3). Since 2026-10-07 every registry copy carries
it too (§11), and there is no exception. The rows
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
  `_openrouter_base` runs after composition. ~~`/model`'s registry copy keeps
  `providers.openrouter.baseUrl`~~ — amended 2026-10-07 (§11, #375): the registry applies
  the same function to every copy it hands out, so `/model`'s copy carries the variable too
  (`test_openrouter_base_url_reaches_the_launch_and_the_model_copy_alike`).
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
  source. Since #379 (§12) a refresh the token endpoint answered with `429`, `500`,
  `502`-`504`, `520` or `524` (pi's set, §12.5), or could not be reached for, is retried
  first (up to three retries, 2 s / 4 s / 8 s); while the stored login exists each retry
  refreshes again and sends nothing else.

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
- ~~**A failed OAuth refresh is not retried.**~~ **Closed 2026-10-08 by #379 (§12): a
  refresh that failed on a transient cause is retried, as pi retries it; what remains
  divergent is how the class is decided (§12.3).** As first written: pi's `lazyStream` turns the refresh's
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
- ~~A failed OAuth refresh is not retried, where pi retries a `502` or a network failure (§4).~~
  Closed 2026-10-08 by #379 (§12).
- ~~`/model`'s OpenRouter pick does not apply `OPENROUTER_BASE_URL`, which the launch applies
  (§2.7, Codex pass 2 C3).~~ Closed 2026-10-07 by #375 (§11).
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
  failed on a transient cause is to be retried as pi does — #379 (§4). **Implemented
  2026-10-08 (§12)**; the choices made under it where the decision left one are listed
  there (§12.3).

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


## 11. `OPENROUTER_BASE_URL` on every OpenRouter model (2026-10-07, #375)

**What was wrong.** The providers guide said the variable "applies to every route that lands
on OpenRouter". Only the launch applied it (`_openrouter_base`, §2.7). Every copy the
registry handed out kept `providers.openrouter.baseUrl` from `models.json`, or the catalog's
`https://openrouter.ai/api/v1`. So a user who pointed OpenRouter at a gateway with the
variable sent the prompt, and their OpenRouter key, to openrouter.ai (or to the `models.json`
host) as soon as they picked an OpenRouter model in `/model`. Measured on `402a8013`:

- in-process (`.omc/probes/375-live/impl/probe/repro.402a8013.out`): the launch answered the
  variable's host, and `find`, `get_all`, `get_available` and both `/model` arguments
  (`openrouter/openai/gpt-4o-mini`, `openrouter/newlab/model-x`) answered the `models.json`
  host;
- the real TUI in a pty (`wire/pty-rpc.out`), launched with `--model
  openrouter/openai/gpt-4o-mini`: the first `hi` reached the variable's listener; after
  `/model openrouter/newlab/model-x` the next `hi` went to the HTTPS_PROXY CONNECT recorder as
  `openrouter.ai:443` (the #370 verify r1 observation); with `providers.openrouter.baseUrl`
  set, both `/model` picks went to that host instead.

**Decision.** Make the guide true. One function owns the variable,
`model_registry.with_openrouter_base_url(model)`: when `model.provider` is exactly
`"openrouter"` and the variable is set and not empty, `base_url` becomes its value and no
other field moves. Two callers:

- the registry, for every model it hands out: `get_all`, `get_available` and `find` read
  through `ModelRegistry._served_models`, and `compose_built_in`'s composition branch applies
  it too. The variable is read when a model is handed out, not when `models.json` is loaded,
  so a registry built before a hatched `.env` value arrived still agrees with the launch.
  The copies are cached per (load, value), so a model's identity is stable between reads;
- the launch, `cli.runtime_bootstrap._openrouter_base`, which now calls the same function.
  It covers what the launch builds without a registry copy: a static catalog hit with no
  registry, a duck-typed registry, a backfill.

Precedence is unchanged from the launch's since #344. The variable beats a `models.json`
provider `baseUrl`, a `baseUrl` on one of that provider's `models` entries, and the catalog
host. It never applies to another provider: a custom provider with its own `baseUrl`, or an
OpenRouter-compatible gateway the user named something else, is left alone even when it
serves OpenRouter ids. Admission is unchanged as well. The variable reaches `os.environ`
from the shell, or from a project `.env` only when `AELIX_DOTENV_ALLOW` names it
(`_DOTENV_LOCKED`, ADR-0203). #375 changes where an admitted value applies, never what is
admitted.

pi has no `OPENROUTER_BASE_URL` (`grep` over `/tmp/pi-27c7b6ff4` @ `27c7b6ff4`: none). Its
`models.json` re-points OpenRouter two ways: `providers.openrouter.baseUrl`, which pi applies
to every built-in model it composes (`provider-composer.ts:326-329`), and a `baseUrl` on one
of that provider's `models` entries, which wins over the provider's for that model
(`modelFromJson`, `provider-composer.ts:222`: `definition.baseUrl ?? providerConfig.baseUrl ??
defaults?.baseUrl`; Codex round 1 ran that body: provider `.../provider/v1`, the model
`.../per-model/v1`). The variable is aelix's own addition and beats both — the launch's
precedence since #344, kept. Applying it the way pi applies its provider override (to every
composed copy) is the closest analogue. (Round 1 called `providers.openrouter.baseUrl` pi's
"only override"; corrected in review round 2.)

**Sweep: every place an OpenRouter `Model` is created or copied.** Before means on
`402a8013`:

| site | before | after |
| --- | --- | --- |
| launch `resolve_route` (`--model`, `--provider`, settings default, `/new` / `/fork` / `/resume` / `/reload` rebuilds, the late re-resolve, `-p` / json / rpc launch) | applied (`_openrouter_base`) | applied (same function) |
| `/agents use` profile route (`agents/service.py`, `resolve_route`) and `tui/commands.py` `resolve_model(profile…)` | applied (launch path) | applied |
| a delegated child (its own CLI process; launch path) | applied | applied |
| `ModelRegistry.find` / `get_all` / `get_available` | not applied | applied |
| `ModelRegistry.compose_built_in` (copy, or the composition branch) | not applied | applied |
| `/model <arg>` (`core.model_argument.resolve_model_argument`, a registry hit) | not applied | applied |
| `/model openrouter/<unknown id>` (`_gateway_backfill`, from `get_all` siblings) | not applied | applied |
| `/model` with no usable registry (`cli/model_switch.py` falls back to `resolve_route`) | applied | applied |
| the `/model` picker, also as `/scoped-models` narrows it (`scoped_available` over `get_available`) | not applied | applied |
| a session continued with `-c` (TUI and `-p`): rebuilt through `resolve_route` (review round 2: measured on `402a8013`, both reached the variable's listener) | applied | applied |
| the first model after `/login` (`find_initial_model`, `_RouteAuthView`) | not applied | applied |
| `resolve_model_scope` and `restore_model_from_session` (exported; no product caller: `--models` is not implemented, `-c` uses `resolve_route`) | not applied | applied (`find` / `get_available`) |
| `--models` (prints "Warning: --models (scoped models) is not yet implemented; the patterns were ignored.") and Ctrl+P model cycling (none: ADR-0154 lists the hotkey as deferred; in the TUI Ctrl+P recalls the previous prompt) | n/a | n/a |
| a provider other than `openrouter` at `https://openrouter.ai/api/v1` — a `models.json` custom provider, an extension registration, a user provider spelled `OpenRouter` (review round 2) | its own host | its own host (the match is on the name, exactly) |
| rpc `set_model` / `cycle_model` / `get_available_models` with an embedder's registry | not applied | applied |
| `aelix --mode rpc` `set_model` / `cycle_model` (passes no registry: refused, both commits) | n/a | n/a |
| `--list-models` (`get_available`; prints no host) | no routing | no routing |
| `ModelRegistry.get_base_url_override("openrouter")` (#344's host-only fallback; the launch runs `_openrouter_base` after it) | `models.json` host | unchanged by design |
| compaction and branch summary (use the session's model) | as the session's model | as the session's model |
| print-channel and session-stats cost lookups (`find` / `get_model`, price only) | no routing | no routing |
| `aelix-server` `rpc_ws` (`Model(id, provider)`, `api` unknown, no registry) | no OpenRouter route | unchanged |
| an extension that builds its own `Model` from `aelix_ai.models.get_model` | catalog host | unchanged (the extension's own model, as in pi) |

**Rows** (`tests/cli/test_openrouter_base_url_375.py`, 16 in round 1, 22 after review round 2
and 23 after review round 3 below; the rewritten
`test_openrouter_base_url_reaches_the_launch_and_the_model_copy_alike` in
`test_launch_compose_363.py`). On `402a8013`, 11 of the 16 fail, and so does the rewritten
row (`impl/red_on_402a8013.out`). The five that pass pin behaviour that was already right:
nothing moves without the variable, an empty value counts as unset, a refused `.env` value
moves nothing, and the launch without a registry. Sabotage (`impl/sabkit/sabotage.py` / `.out`),
one piece at a time in a throwaway worktree against these rows plus
`test_launch_compose_363.py`, `test_route_follows_pi_362.py` and `tests/model_registry`
(365 rows): every piece is red.

| piece | failing rows |
| --- | --- |
| P1 no accessor applies it | 11 |
| P2 / P3 / P4 `get_available` / `find` / `get_all` read the raw list | 6 / 5 / 3 |
| P5 `compose_built_in`'s composition branch skips it | 1 |
| P6 the launch's own call does nothing | 2 |
| W1 every provider re-pointed | 1 |
| W2 the provider matched by prefix (`openrouter-proxy`) | 1 |
| W3 only the catalog host moves (a `models.json` host wins) | 12 |
| W4 the cache ignores the value / W5 applied once at load | 2 / 3 |
| W6 a per-model `baseUrl` wins | 3 |

**On the wire** (`impl/wire/`: real CLI, fake key, scratch cwd, isolated agent dir; the
variable at a listener on 23311, `providers.openrouter.baseUrl` at one on 23312, HTTPS_PROXY
a CONNECT recorder on 23310). `-p` and `--mode json` at launch reach the variable's
listener on both commits. In the TUI (pty), `402a8013` sent the `hi` after
`/model openrouter/newlab/model-x` to `openrouter.ai:443` (no `models.json`; six CONNECTs,
and the turn was still retrying when the next `/model` ran). With
`providers.openrouter.baseUrl` set, it sent the `hi` after each of the two picks
(`openrouter/newlab/model-x`, `openrouter/openai/gpt-4o`) to that host. This change sends
each of them to the variable's listener. `aelix --mode rpc` refuses `set_model` on both commits (it passes no registry), and
its prompts go to the launch model at the variable's listener. The embedder path is the
in-process row.

**Seen alongside, not this seam.** `--model openrouter/auto`, then `/agents use <profile>`,
then `/new` moves the session from OpenRouter's `auto` to `openrouter/auto` (#370 verify r1).
The cause is not the base URL. `agents/service.py` completes the launch pair as
`parsed.provider = model.provider` and leaves `parsed.model = "openrouter/auto"`. The next
rebuild resolves `("openrouter/auto", "openrouter")`, and the explicit-provider route finds
the literal catalog id `openrouter/auto`; the launch had stripped the prefix to `auto`.
`openrouter/auto` is the only catalog id where `<provider>/<id>` is itself an id of that
provider (`impl/probe/auto_carry.402a8013.out`). Filed as a follow-up.

**Review round 2 (2026-10-07).** The round-1 verify and Codex found no routing defect left,
and three things wrong around it. Kit `.omc/probes/375-live/r2/`.

1. *Surfaces that do not exist, and one that was never broken.* Round 1's guide, CHANGELOG,
   this section's sweep table, the README row, the `_served_models` docstring and the commit
   title named `--models` scopes, Ctrl+P cycling and a resumed session. Measured on the
   round-1 commit `914ce4cc`: `aelix --models openrouter/openai/gpt-4o-mini,openrouter/openai/gpt-4o -p …`
   prints `Warning: --models (scoped models) is not yet implemented; the patterns were
   ignored.`; in the TUI, Ctrl+P recalls the previous prompt and the next turn stays on
   `openai/gpt-4o-mini` (no model-cycling binding exists; ADR-0154 defers the hotkey). On
   `402a8013`, after `/model openrouter/openai/gpt-4o-mini` (whose own turn went to the
   `models.json` host — the defect), `aelix -c -p` and `aelix -c` in the TUI both reached the
   variable's listener: `-c` rebuilds through `resolve_route`, which applied the variable
   before #375. Corrected everywhere: the surfaces named are `/model`, its picker (also as
   `/scoped-models` narrows it, which exists), `-c`, `/agents use`, a delegated agent and an
   embedder's rpc. `resolve_model_scope` / `restore_model_from_session` are listed as what
   they are, exported functions no product path calls.
2. *pi's per-model `baseUrl`.* Round 1 said pi's only override is
   `providers.openrouter.baseUrl`; a `baseUrl` on a `models` entry beats it in pi (above).
   Corrected in the guide, its bundled copy, the CHANGELOG, this section and the helper's
   docstring.
3. *Two missing rows.* (a) The verifier's sabotage V1 — re-point every model whose host is
   `openrouter.ai` — and V7 — match the provider up to case — passed all 365 rows, though V1
   would send a `models.json` provider `myor`'s own key, an extension provider's, and a user
   `OpenRouter`'s to the variable's host. New rows put each of the three at
   `https://openrouter.ai/api/v1` with its own key and assert, with the variable set, that
   `find`, `get_all`, `get_available`, the launch and `/model` keep that host (the built-in
   `openrouter` in the same registry moves, as the control), and that with a user `OpenRouter`
   the string `openrouter/<id>` and `--provider OpenRouter` land on that provider and its
   host (ADR-0250's case rule). (b) Codex's mutant — the helper returns `headers=None` —
   passed all 36 rows of the two touched files, because the rows compare the launch with
   `/model` and a field both lose is invisible. New rows compare each path with itself: the
   helper on a model whose every field differs from `Model()`'s default (the row fails if a
   field is added to `Model` and not set there), the registry's `find` / `get_all` and the
   launch over a `models.json` entry and an extension model under `openrouter`, and the
   no-registry launch, each read without and then with the variable: the only differing field
   is `base_url`.

Sabotage, in a throwaway worktree of the round-1 commit, over the same 365-row set
(`r2/out/sabotage.r1rows.out`, round 1's rows) and then with round 2's file
(`sabotage.r2final.out`, 371 rows; baseline `371 passed`):

| piece | round 1's rows | round 2's rows |
| --- | --- | --- |
| V1 host-based re-point | 365 passed | 4 failed |
| V7 provider matched up to case | 365 passed | 2 failed |
| M1 helper drops `headers` (Codex) | 365 passed | 2 failed |
| M2 / M3 helper drops `thinking_level_map` / `compat` | 365 passed each | 2 failed each |
| M4 helper resets `cost` | 365 passed | 4 failed |
| V2 copy rebuilt from id, name, provider, api | 1 failed | 5 failed |
| M5 the registry's served copies drop `headers` (helper intact) | — | 1 failed |
| M7 the launch's own call resets `cost` (helper intact) | — | 2 failed |
| M6 the launch's own call drops `headers` | — | 371 passed — not equivalent (corrected in review round 3 below): a duck-typed registry's hit for an id the catalog does not know is that registry's own model, `headers` included, and the launch's call is what re-points it; round 3's row: 1 failed |

On the wire (`r2/kit/drive.py`, the real CLI, fake keys; the variable's listener on 23301, a
user provider's on 23303, an HTTPS_PROXY CONNECT recorder on 23300): with the variable set,
`--model myor/…` and `--model orext/…` (an `-e` extension registering `orext` at
`https://openrouter.ai/api/v1`) went to `CONNECT openrouter.ai:443`, `/model myor/…` and
`/model orext/…` in the TUI too, while `openrouter/…` reached 23301; with a user `OpenRouter`
at 23303, `--model OpenRouter/…`, `--model openrouter/…` and `/model OpenRouter/…` reached
23303 with that provider's key. The same on `914ce4cc` and on this commit: the code was right,
the rows were missing.

**Review round 3 (2026-10-08).** The round-2 verify found one false claim and three
text nits; no routing defect. Kit `.omc/probes/375-live/r3/` (the verifier's:
`r2verify/`).

1. *M6 is not equivalent.* Round 2 called "the launch's own call drops `headers`"
   equivalent because that call would only move a model carrying no `headers`. False:
   `resolve_route` over a duck-typed registry (no `compose_built_in`) finds an id the
   catalog does not know, such as `openrouter/duck/only`, in that registry and takes the
   registry's own model, `headers` included; nothing re-pointed it, so `_openrouter_base`
   does, and under M6 the `headers` are gone. Measured on the round-2 code, rebased onto
   `61f03b67` (`r3/out/repro.m6.round2.out`): the verifier's row `1 passed` as is and
   `1 failed` under M6, while round 2's 371 rows stay `371 passed` under M6. New row
   `test_the_launch_over_a_duck_typed_registry_hit_changes_base_url_and_no_other_field`
   (a duck registry holding a model with every `Model` field set, read without and then
   with the variable: only `base_url` differs). Green on this commit. Sabotage in a
   throwaway worktree over the same row set plus this row (`r3/out/sabotage.r3.out`,
   baseline `372 passed`; the round-2 verifier's harness, 25 pieces): every piece is red,
   and the launch-side ones now include the new row:

   | piece | failing rows (of 372) |
   | --- | --- |
   | M6 the launch's own call drops `headers` | 1 (the new row; round 2's 371: 0) |
   | M7 the launch's own call resets `cost` | 3 |
   | P6 the launch's own call does nothing | 3 |
   | M1 / M2 / M3 helper drops `headers` / `thinking_level_map` / `compat` | 3 each |
   | M4 / V2 helper resets `cost` / rebuilds from four fields | 5 / 6 |
   | V1 / V7 host-based / case-insensitive match | 4 / 2 |
   | M5 / P5 / W2 / E1 / G1 | 1 each |
   | X1 / X2 / X3 (name / window / input+reasoning), C1 / C2 / L1 (cache and read time), F1 / A1 / H1 / PM (`find` / `get_available` raw, catalog host only, per-model wins) | 5 / 6 / 5, 3 / 3 / 3, 6 / 9 / 11 / 3 |
2. *Nits.* The test module docstring cited an ephemeral `/tmp` path; it now cites the
   kit copy `.omc/probes/375-live/impl/probe/repro.py`. The providers guide (and its
   bundled copy) said the variable applies "wherever" an OpenRouter model is picked; it now
   names the exception in the sweep table above — a model an extension builds itself with
   `aelix_ai.models.get_model()` is the catalog entry and keeps
   `https://openrouter.ai/api/v1`, while `ctx.model_registry`'s copies carry the variable
   (`r3/out/ext_probe.out`: the real CLI with an `-e` extension, fake key, the variable at
   a closed local port — in `session_start`, `ctx.model_registry.find` gave the variable's
   host and `get_model` the catalog's). ADR-0203's chain table named only
   `runtime_bootstrap.resolve_model` as the reader; it now names
   `model_registry.with_openrouter_base_url` as well, with a pointer here.

## 12. A transient refresh failure is retried, as pi retries it (2026-10-08, #379)

The owner's decision of 2026-10-06 (§7), implemented. Kit: `.omc/probes/379-live/impl/`.

### 12.1 What was measured on `8f7d98aa`

A real `aelix --mode json -p` with an isolated agent dir, an EXPIRED fake stored OAuth, the
token endpoint pointed at a local fault-injecting server (`probe/sitecustomize.py`,
`probe/token_server.py`) and the model endpoint at a local SSE server; every exported key
(`ANTHROPIC_API_KEY`, `ANTHROPIC_OAUTH_TOKEN`, `OPENAI_API_KEY`) set to a fake
(`logs/live_before_8f7d98aa.out`). A token endpoint answering `502`, `429`, or closing the
connection with no answer, ended the turn in 0.4-0.5 s with `rc=1`, one token request, zero
`auto_retry_start` and nothing sent — exactly as for a `401`. §2.2 and §9 B1 explain why: the
CLI's callback raised, `_make_stream_fn` turned the raise into `AgentHarnessError("auth")`
before an assistant message existed, and the auto-retry loop reads only the last assistant
message.

### 12.2 Decision

**A stored OAuth refresh whose token endpoint answered `429`, `500`, `502`, `503`, `504`,
`520` or `524` (pi's set), or could not be reached, is retried by the turn's auto-retry;
nothing else is.** *(Review round 2, §12.5, narrowed round 1's "`429` or any `5xx`" to pi's
set; `501`, `505` and the other `5xx` are not retried. Review round 3, §12.6: a status whose
body cannot be decoded counts by its status. Review round 4, §12.7: once a non-2xx status has
arrived it decides, whatever then happens to its body. Review round 5, §12.8: "could not be
reached" is a failure before the status and headers arrived, or a `2xx` whose body is cut short or times out
(`terminated` and timeouts are in pi's retry pattern, `retry.ts`); a `2xx` with a broken
encoding fails at once. Review round 7, §12.10: the whole rule, once — a non-2xx answer is
decided by its status once it arrives (`400`/`401`/`403` refused at once; `429`, `500`,
`502`-`504`, `520`, `524` retried; any other status fails); a `2xx` whose body is cut short or
times out is retried as a dropped connection; a `2xx` with a broken encoding fails at once.
For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.)* pi's shape: its `lazyStream`
turns a setup failure into an error assistant message (`packages/ai/src/api/lazy.ts:4-23`,
`:46-60` @ 1cedd3272) and `isRetryableAssistantError` (`packages/ai/src/utils/retry.ts:252`)
decides on it. In aelix:

1. **Classified where the cause is known.** `aelix_ai.oauth._helpers.refresh_retry_reason`
   walks the exception and its `__cause__` links: a token endpoint's answer (the status the
   built-in Codex, Anthropic and Copilot refreshes now attach as data with
   `oauth_http_error`, or an extension's `httpx.HTTPStatusError`) decides by its status, a
   transport error (`httpx.TimeoutException`, `NetworkError`, `RemoteProtocolError`,
   `ProxyError`, `TimeoutError`, `ConnectionError`) is transient, anything else is not.
   `OAuthRefreshError.retry_reason` carries the answer; every other
   `StoredCredentialError` has `None`. The error stays a plain `RuntimeError` at the raise
   site, so the messages are byte-identical (`details=RuntimeError: …` in Anthropic's).
   *(Review round 3, §12.6: the status is read before the body — `StatusBeforeBody`, an
   httpx `response` hook — so an answer whose body cannot be decoded keeps it. Review round 4,
   §12.7: any error after a non-2xx status arrived keeps it, a body cut short or a read timeout
   included. Review round 5, §12.8: the transport set above decides a failure before the status and headers arrived
   and a `2xx` whose body is cut short or times out; a `2xx` with a broken encoding raises
   `httpx.DecodingError`, which is in no set — it fails at once.)*
2. **Carried, not re-derived.** `ResolvedRequestAuth.retry_reason` (aelix-additive) → the
   CLI's callback raises it on (`_RequestAuthError`) → `_make_stream_fn` ends the attempt
   with an error assistant message (pi's `createSetupErrorMessage` shape: no content, the
   model's provenance, the error's own message without the `get_api_key_and_headers failed:`
   prefix). The harness contract is generic: any `get_api_key_and_headers` raise that carries
   a non-empty `str` `retry_reason`.
3. **Retried by the harness's own record** *(review round 3, §12.6; rounds 1 and 2
   decided by the text's type — `_RetryableSetupErrorText`, then
   `_SetupErrorText.retry_reason` — which a `message_end` hook rebuilt into a plain `str`)*.
   `_make_stream_fn` records the callback's answer (`_SetupFailure`) for the error message
   it ends the attempt with, re-points the record at the message the `message_end` hooks
   leave in state, and `_is_retryable_error` and the overflow recovery read the record for
   that message — never its text or type. A token endpoint's body can route the turn
   neither into nor out of a retry, nor into overflow compaction, whatever a hook does. The
   text's classifier text (§2.2, #186) is still aelix's own sentence (`_SetupErrorText`).
4. **The retry is the turn's.** Same budget (3), backoff (2 s / 4 s / 8 s), counter and
   `auto_retry_enabled` (`set_auto_retry`) as a provider's `502`; the TUI, `-p`, json and rpc
   show what they show for that one. Each retry runs the callback again, so the cascade
   refreshes again and a stored credential still owns the provider: nothing is sent until a
   refresh succeeds, and never on `models.json`'s `apiKey` or an environment key. *(Review
   round 2, §12.5: while the stored login exists. Review round 3, §12.6: a login another
   process removes during the backoff makes the retry send nothing and end with `… is an
   OAuth login that gave no key`; one removed while the failing refresh is under way lets
   the retry use the next key, as a new turn would — both measured and pinned.
   And a refusal inside the sequence now ends it; before, it raised past the #147 arm and
   left the sequence open.)*
5. **Everything else is unchanged.** A `400`/`401`/`403` refresh, an empty or failing stored
   `api_key`, an unregistered OAuth provider, an unknown entry type: `AgentHarnessError("auth")`
   at once, with the message they had on `8f7d98aa` (byte-compared live: `codex-401`,
   `codex-403`, `anthropic-401+env`). *(Review round 4, §12.7: one exception, stated — a
   refusal whose body cannot be read gets the text the same status with an empty body gets;
   on `8f7d98aa` it quoted the decoder's or the connection's error.)*

### 12.3 Divergences from pi and choices made under the decision (ADR-0235)

- **The chain decides, not the text.** pi matches its retry pattern against the message, which
  holds the server's body and, in aelix, the `auth.json` path: a `401` whose body names `502`
  would be retried, and httpx's `All connection attempts failed` and `Server disconnected
  without sending a response.` match none of the patterns (row
  `test_the_text_decides_nothing`; sabotage W1: 30 rows red).
- ~~**Every `5xx` is transient**~~ *(withdrawn in review round 2, §12.5: the set is pi's
  exactly)*; pi's pattern lists `500`, `502`-`504`, `520`, `524`. `429` is
  transient in both. `408` is not, in either (its reason phrase does not reach the Codex
  message when the body is non-empty).
- **A proxy that refuses the CONNECT is transient**, whatever status it gives (`httpx.ProxyError`);
  pi's fetch reports every such failure as `fetch failed`, which it retries.
- **An answer whose body fails mid-read** *(review round 7, §12.10; the bullets rounds 5 and 6
  wrote here, which stated pi's outcome per body shape, are withdrawn)*. aelix's rule is
  §12.2's: a non-2xx answer is decided by its status once it arrives (`400`/`401`/`403`
  refused at once — a refusal must not be retried into a sign-in; `429`, `500`, `502`-`504`,
  `520`, `524` retried; any other status fails), a `2xx` whose body is cut short or times out
  is retried as a dropped connection (`RemoteProtocolError`, `ReadTimeout`), and a `2xx` with a
  broken encoding fails at once (`httpx.DecodingError`, round 3's choice). For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.
  The source facts behind it, @ 1cedd3272: pi's Codex refresh reads a failed answer's body
  with `response.text().catch(() => "")` (`openai-codex.ts`); its Anthropic refresh
  (`anthropic.ts` `postJson`) and its Copilot refresh (`github-copilot.ts` `fetchJson`) call
  `await response.text()` without one; `terminated` and timeouts are in pi's retry pattern
  (`retry.ts`).
- **An extension's refresh that raises a bare `RuntimeError("502 …")` is not retried**; pi would
  retry it by its text. `raise_for_status()` and httpx transport errors are classified.
- **The retried message has no `get_api_key_and_headers failed:` prefix** (pi's shape); the
  refused one keeps it (today's message).
- **`--mode json` exits 0 when every retry fails**, with the refresh's error in the last
  message — what it does when a model's `502` outlasts its retries (#363 verify round 2's
  `server-529` control: `rc=0`); before, the raise made it `rc=1`. Text `-p` exits 1 either way.
- Not covered, and unchanged: the compaction and branch summaries call the callback themselves
  and still fail on any refresh failure (they had no retry for a provider `502` either), and
  `settings.json`'s `retry.enabled` / `retry.maxRetries` are not read by the harness for any
  retry (module constants and `set_auto_retry` only) — both are follow-ups.

### 12.4 Tests, sabotage and live runs

`tests/oauth/test_refresh_retry_reason_379.py` (51: the classifier and each built-in refresh's
status), `tests/cli/test_refresh_retry_379.py` (37: the real registry, CLI callback and harness
over a MockTransport for openai-codex, anthropic and github-copilot — six transient causes,
three refusals, the budget, `auto_retry_enabled`, a hostile `502` page, a stored-entry
control, the registry's reason, the real `_async_main` in json mode) and
`tests/test_agent_harness_auth_retry_379.py` (9: the harness contract). On `8f7d98aa` with the
tests copied in, 31 of the 46 rows in the last two files fail and the 15 that pass are the
controls (refusals and the stored-entry error keep today's behaviour); the first file cannot
import there. Under Python 3.11: 97 passed.

Sabotage (`kit/sabotage.py`, `logs/sabotage.out`), one piece at a time in a throwaway worktree,
every one red:

| piece reverted or wrong form | failing rows |
| --- | --- |
| S1 the Codex refresh carries no status | 11 |
| S2 the Anthropic `_post_json` carries no status | 8 |
| S3 the Copilot `_http_error` carries no status | 8 |
| S4 `OAuthRefreshError` never classifies | 26 |
| S5 the registry drops `retry_reason` | 25 |
| S6 the CLI callback raises a plain `RuntimeError` | 24 |
| S7 the harness raises every callback failure | 26 |
| S8 the retry loop classifies by text only | 8 |
| S9 transport errors not transient | 19 |
| S10 `429` not transient | 7 |
| W1 pi's text regex instead of the chain | 30 |
| W2 the walk follows `__context__` | 1 |
| W3 every `4xx`/`5xx` transient | 26 |
| W4 the classifiers read the server text | 1 |
| W5 a transient failure falls through to the environment | 25 |
| W6 the harness retries the callback inline | 25 |
| W7 the status error is a `RuntimeError` subclass | 1 |

Live (`logs/live_after.out`, `live_after_text.out`, `rpc_after.out`, `tui_*.out`): json —
`502`, `429` or a dropped connection then `200`: `rc=0`, one `auto_retry_start` (1/3, 2000 ms),
`auto_retry_end success=True`, two token requests, one model request carrying the refreshed
token (Anthropic with both Anthropic variables exported: the refreshed token too); `401`/`403`:
`rc=1`, one token request, nothing sent; `502` always: four token requests, three retries
(2000/4000/8000 ms), nothing sent. Text `-p`: the answer and `rc=0`, or the refresh's error on
stderr and `rc=1`. rpc: `auto_retry_start` then `auto_retry_end success=True`. TUI (the interactive
TUI in a pty, `--no-session --provider openai-codex --model gpt-5.1-codex-mini`): `✖ OAuth refresh failed …`, `⟳ Retrying (1/3) in 2s… Esc
to cancel`, the answer, `✓ Retry succeeded (attempt 1)`; on `401` one `✖` line and no retry;
on `502` always four `✖` lines and `✖ Retry failed: …`. Every TUI error message gets its `✖`
line whatever its class (`tui/render.py`, `_render_message_error`), so a retried provider error
shows the same.

### 12.5 Review round 2 (2026-10-08)

Kit: `.omc/probes/379-live/r2/` (`probe/`, `kit/sabotage_r2.py`, `logs/`). Each item was
reproduced on `a46092fd` first.

1. **A refusal inside a retry sequence left the sequence open (blocking).** The token endpoint
   answers `502`, the turn retries (`auto_retry_start` 1/3), and the retry's refresh is refused
   (`401`/`400`: a rotated refresh token whose `502`'d answer was lost gets `invalid_grant`).
   `_make_stream_fn` raised `AgentHarnessError("auth")` from inside `prompt()`'s retry loop, past
   the #147 arm that emits `auto_retry_end(success=False)` and resets the counter. Measured on
   `a46092fd` (`logs/repro_rpc_a46092fd.out`, one rpc process): turn 1 `[502, 401]` →
   `auto_retry_start=[(1, 3, 2000)] auto_retry_end=[]`; turn 2 `[502, 502, 502, 200]` →
   starts `(2, 3, 4000), (3, 3, 8000)`, `auto_retry_end=[(False, 3)]`, the `200` never reached —
   the leaked counter cut the next turn's budget to two retries. **Decision, following pi**
   (`lazyStream` turns every setup failure into an error message; `agent-session.ts:1874-1882`
   @ 1cedd3272 emits `auto_retry_end(false)` and resets): inside a retry sequence
   (`_retry_attempt > 0`) a callback failure without a reason — and a callback answer with
   neither key nor headers — ends the attempt as a **non-retryable** error message whose text is
   the one the raise carries, byte for byte (`get_api_key_and_headers failed: OAuth refresh failed
   for <p>: … Run /login to sign in to <p> again.`). The #147 arm then closes the sequence as it
   does for a provider's non-retryable error. `_SetupErrorText.retry_reason=None` (since
   round 3, §12.6: the harness's record with no reason) is never retried, whatever the text (a `401` body naming `502`). Outside a sequence the first refusal
   is still `AgentHarnessError("auth")` (rc 1, unchanged). A second guard closes the sequence on
   any raise out of the backoff or the re-run (a hook, an adapter, a cancelled `prompt()`):
   counter reset, then one `auto_retry_end(False, final_error=str(exc))`, then the raise. After
   (`logs/rpc_after_r2.out`): turn 1 → `auto_retry_end=[(False, 1)]`; turn 2 → starts 1/2/3,
   `auto_retry_end=[(True, 3)]`, the refreshed token sent once. Codex's exact row (anthropic,
   turn 2 with nine model `502`s then `200`, `logs/rpc_after_r2_anthropic_model502.out`): starts
   1/2/3, `(True, 3)`. TUI in a pty (`logs/tui_502_401_then_3x502_r2.out`): turn 1 shows the
   `502` line, the refusal line and `✖ Retry failed: get_api_key_and_headers failed: …` and no
   widget stays up; turn 2 shows `⟳ Retrying (2/3)…`, three `502` lines, the answer and
   `✓ Retry succeeded (attempt 3)`. The refusal text appears in its `✖` line and again in
   `✖ Retry failed:`, as for any provider error that ends a retry (pi's TUI shows the same pair,
   `Retry failed after N attempts: <finalError>`). `--mode json`: rc 0 with the error in the last
   message, as for every turn that ends on an error message; text `-p`: the same stderr line as a
   first-attempt refusal, rc 1 (`logs/live_json_r2.out`, `logs/live_text_r2.out`).
2. **A logout while a retry waits** (Codex cat1/cat2). Another process empties `auth.json` while
   the `502` is pending; the failed refresh re-reads the store (pi parity, P-142), and the retry
   then resolves what a new turn after that logout resolves — the exported key. **Kept**: there is
   no stored login left to own the provider. The texts now say so: "while your stored login
   exists" (CHANGELOG, both guides, §2.2, §12.2 item 4). Row
   `test_a_logout_while_a_retry_waits_sends_what_a_new_turn_would` pins it (one token request,
   start 1, end `(True, 1)`, `x-api-key=ENV`). *(Corrected in review round 3, §12.6: that row's
   logout lands while the failing refresh is under way, and is renamed so; a logout during the
   backoff — "while a retry waits" — sends nothing and ends the retry.)*
3. **The transient set is pi's exactly**: `429`, `500`, `502`, `503`, `504`, `520`, `524`
   (`RETRYABLE_PROVIDER_ERROR_PATTERN`, `packages/ai/src/utils/retry.ts:30-45`) plus the transport
   class (pi's `fetch failed`). `501`, `505`, `507`, `521`, `530`, `599` are not (rows; Codex's
   `501` mutant is now red, R9: 5 rows). Live: `501`/`505` → rc 1, one token request, no retry;
   `520`/`524` then `200` → one retry, the refreshed token sent.
4. **Event protocol.** The setup error message had a `message_end` and no `message_start`. It is
   now emitted as a `start` (whose partial is the failure itself, as pi's `message_start` carries
   the final message) then an `error`, so the loop emits the pair (pi's loop does for a message
   with no partial, `packages/agent/src/agent-loop.ts:451-453`). Live json:
   `message_start, message_end` × 3 for `[502, 200]`. A provider adapter that errors before its
   own `start` (the anthropic `502` above) still emits only `message_end` — pre-existing, not
   this path, a follow-up.
5. **Wording.** The retried (transient) failure reads pi's text, `OAuth refresh failed for <p>:
   <cause>` (`ModelsError` with its cause detail, `utils/models-error.ts:16-21`), with no `Run
   /login` hint; a refused one keeps the hint and its text byte for byte (rows; live
   `openai-codex:401` and `[502, 401]` stderr identical to `8f7d98aa`'s,
   `verify/logs/refused_text_base.out`).

Rows (`tests/test_agent_harness_auth_retry_379.py` 16, `tests/cli/test_refresh_retry_379.py` 61,
`tests/oauth/test_refresh_retry_reason_379.py` 61 — 138): on `a46092fd` with these files copied in,
60 fail (`logs/red_on_a46092fd.out`; the logout row passes there, it pins kept behaviour); Python
3.11: 138 passed (`logs/py311.out`). Sabotage (`logs/sabotage_r2.out`), every piece red:

| piece reverted or wrong form | failing rows |
| --- | --- |
| R1 a refusal inside a sequence raises again | 10 |
| R2 no close on a raise out of the backoff/re-run | 2 |
| R3 R1 + R2 (`a46092fd`'s behaviour) | 12 |
| R4 the guard closes but keeps the counter | 2 |
| R5 every setup error retried | 11 |
| R6 the in-sequence refusal loses its prefix | 27 |
| R7 a key-less answer inside a sequence raises | 1 |
| R8 no `start` before the setup error | 3 |
| R9 `501` transient (Codex's mutant) | 5 |
| R10 every `5xx` transient (`a46092fd`'s set) | 14 |
| R11 `520` not transient | 4 |
| R12 `524` not transient | 5 |
| R13 the transient text keeps `/login` | 28 |
| R14 the refused text loses `/login` | 22 |

§12.3's "every `5xx` is transient" is withdrawn; its other choices stand. Not changed here:
`settings.json` `retry.*` wiring and the compaction/branch-summary retries (follow-ups).

### 12.6 Review round 3 (2026-10-08)

Kit: `.omc/probes/379-live/r3/` (`probe/`, `cross/`, `ext/`, `kit/sabotage_r3.py`, `logs/`).
Each item was reproduced on `7b0207bb` first.

1. **A logout meanwhile, measured per shape (blocking: §12.2 item 4 and the guides said "the
   next key" for both).** Live rpc, an isolated agent dir, a fake expired login, the token
   endpoint answering `502` then `200`, every key a fake (`logs/item1_shapes_base_7b0207bb*.out`
   before, `logs/item1_shapes_tree.out` after — the same, behaviour unchanged):

   | when `auth.json` is emptied | anthropic | openai-codex |
   | --- | --- | --- |
   | during the backoff (on `auto_retry_start`) | token `[502]`, nothing sent, `auto_retry_end(False, 1)` with `get_api_key_and_headers failed: The auth.json entry for anthropic is an OAuth login that gave no key. … remove the anthropic entry from <path>`; the next turn sends the `models.json` key, or with none the exported `ANTHROPIC_API_KEY` | the same text for openai-codex, nothing sent; the next turn reads the `models.json` key, which the Codex adapter refuses (`… missing the chatgpt_account_id claim`) |
   | while the failing refresh is under way (the endpoint empties it, then answers `502`) | the retry sends the `models.json` key, or with none the exported key, `(True, 1)`; with neither, nothing is sent and the retry ends `No API key for provider: anthropic` | nothing sent: `… missing the chatgpt_account_id claim` with a `models.json` key, else `No OAuth token for openai-codex — run /login …`; `(False, 1)` |

   Why: during the backoff the in-memory entry is still the expired login, the retry's refresh
   re-reads the store under its lock, finds nothing and returns no key
   (`auth_storage.py` `get_oauth_api_key`), and the cascade raises "is an OAuth login that
   gave no key"; inside the refresh, the failure's reload (P-142) drops the entry first, so the
   retry reads the steps after it. The texts now say exactly that, a row per shape (the
   CHANGELOG, both guides and their bundled copies, the `OAuthRefreshError` docstring in `auth_storage.py`,
   §12.2 item 4, §12.5 item 2). The message's advice to remove an entry the logout already
   removed is left as it is: the fix is not one line (`StoredCredentialError` builds the whole
   sentence) — a follow-up. Rows `test_a_logout_while_a_retry_waits_ends_the_retry_and_sends_nothing`
   (anthropic, openai-codex; then the next turn's key), the renamed
   `test_a_logout_during_the_failing_refresh_sends_what_a_new_turn_would` (anthropic, ENV) and
   `test_a_logout_during_the_failing_codex_refresh_sends_nothing`.
2. **A status whose body cannot be decoded lost its status (Codex cat1).** A `502` with
   `Content-Encoding: gzip` and a body that is not gzip: `client.post()` read the body before
   returning and raised `httpx.DecodingError`, so the site's status check never ran — no retry
   reason, the turn failed at once with the `/login` hint (live on `7b0207bb`, all three
   providers, `logs/item2_gzip_base_7b0207bb.out`). *(Review round 7, §12.10: the pi
   comparison this item made is withdrawn; §12.3 states aelix's rule. For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.)* Now
   `_helpers.StatusBeforeBody` is passed as an httpx `response` event hook, which httpx runs
   once the status line and headers are in, before it reads the body; `answer(client.post(…))`
   turns a non-2xx `DecodingError` into that status with an empty body, so the site classifies
   it and its message falls back to the reason phrase. A 2xx whose body cannot be decoded still
   raises as before. Applied at every site that attaches the status: the Codex refresh, the
   Anthropic `_post_json` (refresh and sign-in) and the Copilot refresh, device-code request and
   poll. Live after (`logs/item2_gzip_tree.out`): `gz502` then `200` → one retry, the refreshed
   token sent, rc 0, for each provider; the Codex text is pi's, `OpenAI Codex token refresh
   failed (502): Bad Gateway`; `gz200` → rc 1, the old text; `gz401` → rc 1, refused;
   `gz503` then three `gz502` → three retries, nothing sent. TUI in a pty
   (`logs/tui_gz502_then_200_tree.out`): the `✖` line, the answer, `✓ Retry succeeded
   (attempt 1)`.
3. **A `message_end` hook could change the retry decision (Codex cat1).** A hook that rebuilds
   `error_message` through JSON (the same text, a plain `str`) turned `_SetupErrorText` into a
   `str`, and the classifier read the text: a `401` whose body names `502` was retried to
   success and a disconnect was not retried. **Decided:** the harness keeps the decision itself
   (§12.2 item 3): `_SetupFailure` records the callback's answer for the message it builds,
   follows the message the hooks leave, and is cleared when the next attempt starts;
   `_is_retryable_error` and the overflow recovery read it for that message, never its text or
   type. Before/after with Codex's in-process probe (`logs/item3_hook_base_7b0207bb.out`,
   `logs/item3_4_codex_probe_tree.out`) and a real `aelix --mode json -e json_round_trip.py`
   (`logs/item3_hook_extension_live.out`): `[502, 401-naming-502, 200]` was starts `[1, 2]`,
   end `(True, 2)`, the refreshed token sent; now start `[1]`, end `(False, 1)`, nothing sent
   (codex, anthropic). `[drop, 200]` was one token request and no retry; now one retry and the
   refreshed token (codex, copilot). TUI with the extension
   (`logs/tui_hook_502_401-502_tree.out`): the refusal ends the retry with `✖ Retry failed: …`.
4. **`httpx.NetworkError` was pinned only by `ConnectError` and `ReadError` (Codex cat4).**
   Rows for `WriteError` and `CloseError` (each retried to success, three providers, and in the
   classifier). Codex's narrowing mutant is now red (N1: 8 rows); live in-process, `write` →
   retried to success, under the mutant → no retry (`logs/item3_4_codex_probe_tree.out`).
5. **Wording.** The status line, §12.2's headline and the index row name pi's set
   (`429`, `500`, `502`-`504`, `520`, `524`) and "while the stored login exists".

Rows (`tests/test_agent_harness_auth_retry_379.py` 21, `tests/cli/test_refresh_retry_379.py` 88,
`tests/oauth/test_refresh_retry_reason_379.py` 73 — 182): on `7b0207bb` with these files copied in,
23 fail (`logs/red_on_7b0207bb.out`: the gzip rows, the hook rows, the record rows); the logout
rows pass there — they pin behaviour this round keeps and documents. Python 3.11: those files plus
`tests/test_auto_retry.py` and `tests/test_agent_harness_auth_error.py`, 231 passed
(`logs/py311.out`). Sabotage (`kit/sabotage_r3.py`, `logs/sabotage_r3.out`; base 182 passed,
restored 182 passed):

| piece reverted or wrong form | failing rows |
| --- | --- |
| T1 the record is ignored (the text decides) | 79 |
| T2 `7b0207bb`'s type check instead of the record | 11 |
| T3 the record does not follow the hooks' message | 10 |
| T4 the record follows the original message, not the replacement | 10 |
| T5 the overflow recovery ignores the record | 1 |
| T6 the record is never set | 77 |
| T7 the record is not cleared when an attempt starts | 0 (survives) |
| G1 `StatusBeforeBody` keeps nothing (`7b0207bb`) | 10 |
| G2 `StatusBeforeBody` keeps a 2xx too | 3 |
| G3 the Codex refresh does not pass the hook | 3 |
| G4 the Anthropic `_post_json` does not pass the hook | 3 |
| G5 the Copilot refresh does not pass the hook | 3 |
| G6 the Copilot device-code request does not pass the hook | 1 |
| N1 `NetworkError` narrowed to `ConnectError` + `ReadError` | 8 |
| V7 the raise guard without its "no open sequence" return (verify r2) | 1 |
| V24 the raise guard resets the counter after its emit (verify r2) | 1 |

T7 survives because no path observes it: the record is matched by identity, the retry pops its
message, and a later attempt builds a new one; the clear only stops a spent record from
outliving its attempt (a session rebuild that put the same message object back last). Kept as
hygiene, stated here. Follow-ups, not changed: the stale "remove the entry" advice after another
process's logout; the harness's model-error `_RETRYABLE_ERROR_PATTERN` has no `520`/`524`; the
anthropic adapter's own model `502` emits `message_end` without `message_start`; an unexpired
cached token is still sent after another process logs out.

### 12.7 Review round 4 (2026-10-08)

Kit: `.omc/probes/379-live/r4/` (`probe/`, `kit/sabotage_r4.py`, `logs/`). Rebased onto
`dfb4ddcc` (#404) first: two test docstrings conflicted on citation line numbers and were merged
by hand (#404's `tui/shell.py` and `extensions/loader.py` lines, this change's `cli/entry.py`
and `harness/core.py` lines); `check_citations --check` after: 955 gated, none drifted. Each item
was reproduced on `e946bf53` first.

1. **A status whose body broke off lost its status (Codex r3 cat1, blocking).** A `401` (or
   `501`) with `Content-Length: 100` and a body of `{` then EOF: httpx raises
   `RemoteProtocolError` while reading the body, and `StatusBeforeBody.answer` kept the status
   only for `DecodingError`, so the error read as a dropped connection — the REFUSED refresh was
   retried and the next `200` sent a model request. Live on `e946bf53`
   (`logs/item1_base_e946bf53.out`, a raw-socket recorder, all three providers): `tr401,200`,
   `tr501,200` and `tr502,200` each gave `auto_retry_start (1, 2000)`, `auto_retry_end (True,
   1)` and the refreshed token sent, with `… peer closed connection without sending complete
   message body (received 1 bytes, expected 100)`. **Decided:** once a non-2xx status has
   arrived, it decides, whatever happens while reading the body — `answer` turns ANY error
   after the `response` hook ran on a non-2xx into that status with an empty body (it also
   forgets the previous request's status first). *(Review round 7, §12.10: the pi comparison
   this item made is withdrawn; §12.3 states aelix's rule. For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.)* Apart from a 2xx (next),
   only a failure before the status and headers arrived — no connection, a write that timed
   out, a hang-up before the header block completed — is a transport error. A 2xx whose body
   breaks still raises as before: cut short, it is retried as a dropped connection; a broken
   encoding fails at once. Live after
   (`logs/item1_tree.out`): `tr401`/`tr501` → rc 1, one token request, no retry, nothing sent;
   `tr502`/`tr429` then `200` → one retry, the refreshed token; `drop`, `gz502` and `tr200`
   then `200` → one retry, as before. The Copilot device-flow poll follows the same rule: a
   `401` cut short is the poll's refusal (it was polled through as transient), a `502` cut
   short is its transient `HTTP 502`.
2. **Wording (Codex r3 cat2).** The CHANGELOG said a `5xx` whose body cannot be read is
   retried; `501`/`505` are not — it now says "one of those statuses". The refused message for
   a malformed-gzip or cut-short `401` differed from `8f7d98aa`'s (which quoted the decoder's
   or the connection's error). It is now byte-identical to the text the same status with an
   empty body gets, at every site — measured live (`gzS401` and `tr401` against `e401`, the same
   reason phrase) and pinned by rows; the CHANGELOG and §12.2 item 5 state the exception.
3. **Rows for two surviving mutants.** `WriteTimeout` (Codex r3: naming `ConnectTimeout`,
   `ReadTimeout` and `PoolTimeout` instead of `httpx.TimeoutException` was green everywhere)
   — the classifier row, each site, and a retried-to-success row per provider. The Copilot
   poll's hook (verify r3 G7) — `gz502` then a token returns the token, and so does a cut-short
   `502`.

Rows (`tests/oauth/test_refresh_retry_reason_379.py` and `tests/cli/test_refresh_retry_379.py`
gained 102; the three #379 files are 284): on `e946bf53` with these files copied in, 67 fail
(`logs/red_on_e946bf53.out`). Sabotage (`kit/sabotage_r4.py`, `logs/sabotage_r4.out`; base 284
passed, restored 284 passed):

| piece reverted or wrong form | failing rows |
| --- | --- |
| S1 only `DecodingError` keeps the status (`e946bf53`) | 67 |
| S2 only `TransportError` keeps it (a broken encoding loses it) | 26 |
| S3 `WriteTimeout` dropped from the transport set (Codex r3) | 7 |
| S4 the Copilot poll without the hook (verify r3 G7) | 2 |
| S5 `answer` keeps an earlier request's status | 1 |
| S6 a 2xx whose body fails keeps the 2xx too | 4 |
| S7 `RemoteProtocolError` dropped from the transport set | 8 |

### 12.8 Review round 5 (2026-10-08)

Kit: `.omc/probes/379-live/r5/` (`kit/`, `logs/`). Text and rows only — no behaviour changed.
Rebased onto `8428e16c` (#178, #405) first: it applied cleanly (CHANGELOG entries of both
sides kept); `check_citations --check` after: 955 gated, none drifted.

1. **Six texts stated the rule without its `2xx` exception (verify r4, blocking).** The
   CHANGELOG, §12.2's headline and item 1, the decisions index row, the `_TRANSIENT_TRANSPORT`
   comment and the commit message said only a failure before any status arrived counts as
   "could not be reached"; the `OAuthRefreshError` docstring called every "2xx that is not a
   token" non-transient. Reproduced live on `d7f25e1a` (`logs/repro_2xx_base_d7f25e1a.out`,
   the raw-socket recorder, all three providers): `tr200,200` and `sl200,200` (a `2xx` cut
   short, a `2xx` that times out) → `auto_retry_end (True, 1)`, the refreshed token sent, the
   error `… peer closed connection …` / `ReadTimeout`; `gz200,200` → rc 1, no retry.
   **Decided: keep the retry** (`terminated` and timeouts are in pi's retry pattern,
   `retry.ts`). Every text now says: a non-2xx status decides once it arrives; a `2xx` whose
   body is cut short or times out is retried as a dropped connection; a `2xx` with a broken
   encoding fails at once. *(Review round 7, §12.10: the pi comparisons this item added — to
   §12.3 and the `StatusBeforeBody` docstring — are withdrawn. For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.)* The per-site
   comments at the Codex, Anthropic and Copilot hooks say "a non-2xx status".
2. **Rows for verify r4's two surviving mutants.** W2 (`answer` dropping `reason_phrase`): a
   `401 Status` whose body is gzip-broken or cut short reads byte for byte as a `401 Status`
   with an empty body, per site — red under W2 (4 rows: Codex and Copilot quote the phrase,
   Anthropic does not). W1 (`answer` catching `BaseException`): a cancel while a `401`'s or a
   `502`'s body is read stays a cancel, per site — red under W1 (6 rows).
3. `ruff format` on `_helpers.py`: the one 101-character line is wrapped.

Rows: 296 in the three #379 files (12 new). Sabotage (`kit/sabotage_r5.py`,
`logs/sabotage_r5.out`): base 296 passed; W1 6 failed; W2 4 failed; restored 296 passed.
Behaviour unchanged: the verify's live matrix (18 plans × 3 providers) on `d7f25e1a` and on
the tree, normalised for timings, is identical (`logs/matrix_*.out`).

### 12.9 Review round 6 (2026-10-08)

Kit: `.omc/probes/379-live/r6/` (`kit/`, `logs/`). Text and rows only — no behaviour changed.

1. **pi's failed-body handling was stated for all three refreshes; only its Codex refresh
   reads a failed answer's body with `.catch` (verify r5, blocking).** pi @ 1cedd3272:
   `openai-codex.ts` reads it with `response.text().catch(() => "")`; `anthropic.ts`
   `postJson` and `github-copilot.ts` `fetchJson` call `await response.text()` without one.
   §12.6 item 2, §12.7 item 1, the `StatusBeforeBody` docstring and the commit message now
   limit the `.catch` statement to pi's Codex refresh. *(Review round 7, §12.10: the outcomes
   per body shape this round measured against pi and wrote into §12.3, the docstring, §12.8
   and the CHANGELOG are withdrawn; aelix's rule is §12.3's. For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.)*
2. **Text.** The guides' "any other status … fails at once / is not retried" now names the
   one exception, a `2xx` whose body is cut short or times out (retried). *(The CHANGELOG
   sentence this item added about pi is withdrawn in review round 7, §12.10.)*
3. **Rows for verify r5's mutant M5** (a `ReadTimeout` while a `2xx` body is read made a
   plain `RuntimeError`: green on all 296 rows). `_cut_short(200)` and
   `_cut_short(200, httpx.ReadTimeout)` at the Codex, Anthropic and Copilot refreshes each
   give `the token endpoint could not be reached (…)` — six rows in place of the one
   Codex-only control.

Rows: 301 in the three #379 files. Sabotage (`kit/sabotage_r6.py`): with `f1fb0eee`'s rows,
M5 296 passed, M6 and M7 1 failed each (`logs/sabotage_r6_before_HEAD_tests.out`); with the new
rows (`logs/sabotage_r6_after.out`, base 301 passed, restored 301 passed) M5 3 failed, M6 6,
M7 3, and W1 6, W2 4, W2b 4, M4 6, M8 25 still red; M3 (`content-encoding` kept on the rebuilt
empty response) survives, as in round 5. Behaviour unchanged: the `#379` product code is
AST-equal to `f1fb0eee` with docstrings ignored (`logs/ast_eq.out`), and the round-5 live matrix
(18 plans × 3 providers) rerun on the tree is identical to round 5's once timings, the
recorder's port and cancel-scope ids are normalised (`logs/matrix_tree.out`,
`logs/matrix_r5_vs_r6.diff` empty).

### 12.10 Review round 7 (2026-10-08)

Kit: `.omc/probes/379-live/r7/` (`kit/`, `logs/`). Text only — no behaviour and no rows changed.

1. **The texts' statements of how pi treats a body that fails mid-read depended on the
   connection framing (verify r6, blocking).** Reproduced on `d38ad41b` with pi's real refresh
   code for all three providers (Copilot's module with its missing generated catalog stubbed)
   against verify r6's raw-socket recorder (`logs/pi_framing_repro.out`): the same chunked
   bytes cut short get a different pi outcome when the answer says `Connection: close` than on
   a kept-alive connection (`ch200` against `ck200`, `ch401` against `ck401`), so the texts,
   written from the `Connection: close` framing alone, stated a difference from aelix that
   keep-alive does not show and missed one it does (aelix's own outcome is framing-blind:
   verify r6's `logs/aelix_keepalive.out`). **Decided (main loop):**
   stop describing pi's per-framing behaviour. Every per-shape pi sentence about a malformed,
   cut-short or stalled body is removed — from the CHANGELOG, the status line, §12.2, §12.3
   (two bullets become one), §12.6 item 2, §12.7 item 1, §12.8, §12.9, the decisions index
   row, the `StatusBeforeBody` docstring, the `_TRANSIENT_TRANSPORT` comment, one test
   docstring and the commit message (the guides had none; the providers-and-models guide and
   its bundled copy, which say pi retries the same refreshes, gain the sentence below) — and
   where a comparison is needed it is this one sentence, the same everywhere: "For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers."
   aelix's rule is stated once, exactly (§12.3): a non-2xx answer is decided by its status
   once it arrives (`400`/`401`/`403` refused at once; `429`, `500`, `502`-`504`, `520`,
   `524` retried; any other status fails); a `2xx` whose body is cut short or times out is
   retried as a dropped connection; a `2xx` with a broken encoding fails at once. Both guides
   now also say that a non-`2xx` answer is decided by its status whatever then happens to its
   body. What stays about pi is framing-independent and read from source @ 1cedd3272: its
   retryable status set, `terminated` and timeouts in `retry.ts`'s pattern, and that only its
   Codex refresh reads a failed answer's body with `.catch`.
2. **Wording (verify r6, nonblocking N1).** A hang-up after the status line but before the
   header block completed is a transport error (httpx's `response` hook never ran); the
   `StatusBeforeBody` docstring, the `_TRANSIENT_TRANSPORT` comment and §12.7 item 1 now say
   "before the status and headers arrived" instead of "before any status" / "the status line".

No behaviour change: `_helpers.py` and the one test file changed are AST-equal to `d38ad41b`
with docstrings and comments ignored (`logs/ast_eq.out`; the comparer tells a dropped
`RemoteProtocolError` or `524` apart, `logs/ast_eq_sensitivity.out`). The three #379 files:
301 passed. The rule's clauses are each pinned (`kit/sabotage_r7.py`, `logs/sabotage_r7.out`,
a throwaway worktree, base 301 passed, restored 301 passed): a refusal whose body fails
retried 70 failed, a `2xx` cut short kept as a `2xx` 9, a `2xx` broken encoding made
transient 6, `524` dropped 5, `501`/`505` added 28.
