# 0254. A provider SDK client never reads a credential from the environment

Status: Accepted (2026-10-06); review round 1 (2026-10-07, §2.1, §3, §4, §7); review round 2
(2026-10-07, §2.1-§2.3, §3, §8); review round 3 (2026-10-07, §2.2, §5, §6, §9)
Date: 2026-10-06
Amends: **ADR-0045** §A.4 (the `_anthropic_client` wrapper now owns the "no credential of
its own" rule), **ADR-0052** (the OAuth branch's blank `api_key` becomes no `api_key`),
**ADR-0251** §2.1 step 4 (the anthropic adapter's environment step, stated; dated note
there), **ADR-0203** residual risk 1's `ANTHROPIC_AUTH_TOKEN` paragraph (dated note there).
Relates: ADR-0235 (pi is the reference, not a parity mandate), #363 (the key order this
completes; its Codex pass 2 found this).
Issue: #374. Owner direction: 2026-10-02 (the pi direction for the batch); the decisions
in §2 were set by the main loop under it on 2026-10-06.
pi: `packages/ai/src/api/anthropic-messages.ts` and `packages/ai/src/providers/anthropic.ts`
@ `b223082bb`.
Tests: `tests/providers/test_anthropic_no_env_credential_374.py` (102 rows: 23 from review
round 1, 32 from review round 2, 7 from review round 3);
`tests/providers/test_copilot_request_auth_headers.py` and
`tests/oauth/test_anthropic_adapter_oauth_passthrough.py` (one row each re-pinned, §4).

## 1. What was measured

On `aab1f210`, fake keys, an isolated agent dir and home, a scratch cwd, a local recorder
for the gateway and a CONNECT recorder as `HTTPS_PROXY` that refuses everything
(`.omc/probes/374-live/impl/live/`). The real CLI in `--mode rpc` with one prompt:

| Row | Setup | `aab1f210` |
| --- | --- | --- |
| R01 | `models.json` `mygw` (`"api": "anthropic-messages"`, no `apiKey`), `ANTHROPIC_API_KEY` exported | `x-api-key: <ANTHROPIC_API_KEY>` to the gateway |
| R02 | same, only `ANTHROPIC_AUTH_TOKEN` exported | `Authorization: Bearer <ANTHROPIC_AUTH_TOKEN>` to the gateway |
| R03 | `mygw` authenticated by its own `Authorization` header | the gateway's header **and** `x-api-key: <ANTHROPIC_API_KEY>` |
| R04 | built-in `fireworks` re-pointed, `FIREWORKS_API_KEY` unset | `x-api-key: <ANTHROPIC_API_KEY>` |
| R05 | built-in `anthropic`, `ANTHROPIC_API_KEY` | `x-api-key: <ANTHROPIC_API_KEY>` (correct) |
| R06 | built-in `anthropic`, only `ANTHROPIC_AUTH_TOKEN` | `Authorization: Bearer <token>` |

and in print mode, #363 verify's L14 (a valid Anthropic OAuth login, `anthropic`
re-pointed): `authorization: <oauth>` **plus `x-api-key:` with an empty value** (C08).
Print and json mode stopped R01-R04 earlier with "No API key found for …" (C01-C06), a
check the turn path of `--mode rpc`, the TUI and delegated children does not run.

The cause, read and then measured in-process (`inproc/probe_factory.py`):
`create_async_client` left `api_key` out when it was `None`, and the `anthropic` SDK
(0.102.0, the locked version; `>=0.40,<1.0` allowed) then reads `ANTHROPIC_API_KEY` **and**
`ANTHROPIC_AUTH_TOKEN` (`anthropic/_client.py`, `has_explicit_credential`), and with
neither set runs its default credential chain — `ANTHROPIC_PROFILE`, workload identity
federation, the active profile on disk — bound to the client's `base_url`
(`_bind_credentials_base_url`). Every provider whose `api` is `anthropic-messages` builds
its client there: 14 catalog providers (`anthropic`, `cloudflare-ai-gateway`, `fireworks`,
`github-copilot`, `kimi-coding`, `minimax`, `minimax-cn`, `opencode`, `opencode-go`,
`vercel-ai-gateway` and four Xiaomi providers) plus any `models.json` provider. L14's
empty header was ours: the OAuth and Copilot branches passed `api_key=""`, which the SDK
sends verbatim as `X-Api-Key: ` (`_api_key_auth`); the recorder prints an empty value as
`EMPTY`.

## 2. Decision

**No provider SDK client may read a credential from the environment on its own.** The key
a request carries is the one aelix's key order resolved (ADR-0251: `--api-key` →
`auth.json` → `models.json` `apiKey` → the environment, keyed by the provider), or nothing.

### 2.1 The Anthropic client (`providers/_anthropic_client.py`)

- Every client is an `AelixAsyncAnthropic`, a subclass of `AsyncAnthropic` (pi:
  `PiAnthropic`, `anthropic-messages.ts:335-339`). Its constructor always passes the SDK an
  explicit credential (`api_key=key or ""`), so the SDK neither reads the two variables nor
  runs its chain, then stores exactly what the caller gave: `None` or `""` is **no key** —
  no `x-api-key` header at all. Being a subclass also keeps 0.102's chain off on its own
  (`_is_base_client` is true only for the base classes). `copy()` / `with_options()`
  rebuild through `self.__class__`, so a copy reads nothing either. pi passes
  `apiKey: apiKey ?? null, authToken: null` (`:1069-1070`); the Python SDK has no
  "explicit null" (`None` is its default), hence the subclass.
- **Two layers, both kept** (review round 1). Layer 1 is the explicit credential
  argument (`api_key=key or ""`); layer 2 is storing `self.api_key = key` /
  `self.auth_token = token` after `super().__init__`. On 0.98-0.102 each is
  redundant with the other for the two variables. They are not redundant across the
  range `anthropic>=0.40,<1.0` allows: 0.40-0.97 read `ANTHROPIC_AUTH_TOKEN` whenever
  `auth_token` is `None`, whatever `api_key` is, so without layer 2 the factory sends
  that token as a bearer (round-1 verify, real 0.40.0; re-measured here on 0.40.0
  and 0.97.0). Layer 1 is what keeps the SDK's credential chain from running if a
  version runs it for subclasses too (0.98-0.102 run it for the base classes only,
  `_is_base_client`). Each layer has a row that simulates the SDK shape where it
  alone stops the leak, so removing either goes red.
- **`ANTHROPIC_CUSTOM_HEADERS` reaches provider `anthropic` only** (review round 1,
  main-loop decision). 0.98+ parse it into the client's default headers under the
  caller's (`_client.py`, `custom_headers_env`). It can carry `x-api-key` or
  `Authorization`: before this, a lower-case `x-api-key` in it sat next to a
  gateway's own header or key, and a canonical `X-Api-Key` replaced the gateway's
  own key (H01-H03, H05 in §4). `create_async_client(env_custom_headers=False)`, the
  default, keeps exactly the caller's `default_headers`; the adapter passes `True`
  for provider `anthropic` alone, where it is the SDK's documented way to reach your
  own Anthropic proxy: its headers go out as on `aab1f210`, replacing the key included,
  and an auth header among them counts as the request's auth (§2.2, review round 2).
  "Provider `anthropic`" is the name: the built-in one, or a `models.json` entry named
  `anthropic` (an override with its own `baseUrl`); a provider of any other name never
  gets them.
  The choice is a class per value, so `copy()` / `with_options()` keep it.
- Its header check accepts any auth header the adapter accepted, ignoring case. The SDK's
  own `_validate_headers` looks `Authorization` / `X-Api-Key` up in a plain `dict`, so a
  `models.json` header spelled `authorization` would be refused once no key rides along.
- `create_async_client` gains `auth_token` (sent as `Authorization: Bearer`).

### 2.2 The adapter (`providers/anthropic.py`)

- **The credential is resolved before any client exists** (`_resolve_request_auth`): the
  caller's key, else `get_env_api_key(model.provider)` — the provider's own variables, as
  the sibling adapters do (`openai_completions`, `openai_responses`,
  `google_generative_ai`), so library use of `anthropic` without the harness keeps
  working — else, for provider `anthropic` only and only with no auth header
  in the request's headers, `ANTHROPIC_AUTH_TOKEN` as a bearer (one in
  `ANTHROPIC_CUSTOM_HEADERS` does not hold it back, as on `aab1f210`) (pi `providers/anthropic.ts:34-41`, also scoped to
  `anthropic`). The OAuth decision reads the resolved key, so `ANTHROPIC_OAUTH_TOKEN`
  found here takes the OAuth branch.
- **No key and no auth header means no request** (pi `assertRequestAuth`,
  `anthropic-messages.ts:316-326`, called at `:612-613` before any client): the turn ends
  with `No API key for provider: <provider>`. Auth headers are `Authorization`,
  `x-api-key` and `cf-aig-authorization`, any letter case, with a non-blank value, in the
  request's headers, as pi's `hasRequestAuth`. For provider `anthropic` only, the headers
  `ANTHROPIC_CUSTOM_HEADERS` adds count as well (`read_env_custom_headers`, parsed as the
  SDK parses the variable), because its client keeps them (§2.1): a header-only proxy
  for your own Anthropic endpoint — no key, no OAuth token, no `ANTHROPIC_AUTH_TOKEN`,
  its auth in the variable — gets one request with that header in `--mode rpc` and the
  TUI (review round 2, §8), also when the request carries other, non-auth headers of its
  own, such as a `models.json` override's `x-team` (review round 3, §9). On `aab1f210`
  the SDK's own check let through only a non-blank `X-Api-Key` or `Authorization` spelled
  exactly that way; a lower-case one and `cf-aig-authorization` are new here, and pi
  counts none of them (§6). On an SDK before 0.98, which does not read
  the variable, that request carries no auth and the SDK itself refuses it before
  sending (`Could not resolve authentication method`). For every other provider the
  variable never counts, as it never reaches them. `-p` and `--mode json` are unchanged:
  for a header-only setup they keep `aab1f210`'s "No API key found for …" before the turn,
  because `has_configured_auth` does not read the variable. Whether it should is an owner
  question this ADR does not decide (§5 item 5). An injected `options.client` skips the
  check, as in pi.
- The OAuth and Copilot branches pass no `api_key` (was `""`), so the bearer goes alone.
  The Copilot branch adds its bearer only when there is a key (it wrote
  `Bearer None` before; pi `authToken: apiKey ?? null`).

### 2.3 The other SDK clients (sweep, §3)

The OpenAI factory (`_openai_client.create_async_client`) always passes `api_key or ""`
(the `openai` SDK treats `""` as given, `openai/_client.py`, `if api_key is None: …
OPENAI_API_KEY`), so no OpenAI client reads `OPENAI_API_KEY` on its own. What each adapter
does with no key (the caller's, else the provider's own variable), unchanged by this ADR:

- `openai_responses`: `"unused"` when an `authorization` / `cf-aig-authorization` header
  is present, else `No API key for provider: <p>` before any client
  (`_resolve_client_api_key`).
- `openai_completions`: `stream_simple_openai_completions` raises
  `No API key for provider: <p>` before any client; `stream_openai_completions` does not
  check: it builds the client with `""` and sends the request with no auth header at all
  (Codex round 2 measured one `POST /chat/completions` with no `Authorization` for a
  keyless custom provider with `OPENAI_API_KEY` exported). pi throws there too
  (`getClientApiKey`, `openai-completions.ts:87-91`, called at `:341` @ `b223082bb`); the
  difference predates this ADR, sends no credential, and is left for a follow-up. A row
  pins that it never sends `OPENAI_API_KEY`.
- `google_generative_ai`: `No API key for provider: <p>` before any client
  (`_resolve_api_key`).

The Gemini factory has no "no key" value at all (`google-genai` `_api_client.py`:
`self.api_key = api_key or env_api_key`, so `None` and `""` both read `GOOGLE_API_KEY` /
`GEMINI_API_KEY`); `create_client` now refuses a keyless call. The Vertex factory refuses a
keyless call with neither `project` nor `location`: either one alone makes the SDK drop an
env key (`(project or location) and env_api_key`), and a row pins each alone (review
round 2).

## 3. Sweep: every client construction under `packages/aelix-ai/src/aelix_ai/providers`

| Site | Could read another provider's (or nobody's) credential before? | After |
| --- | --- | --- |
| `anthropic.py` API-key branch → `create_async_client` | **Yes**: `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / SDK chain for every `anthropic-messages` provider (R01-R04) | No; provider-keyed env only; no-auth → `No API key` |
| `anthropic.py` OAuth branch | No key leak (explicit `""`), but an empty `x-api-key` beside the bearer (L14) | Bearer only |
| `anthropic.py` Copilot branch | Same empty `x-api-key`; `Bearer None` with no token | Bearer only; no `Bearer None` |
| `openai_completions.py` → `_openai_client.create_async_client` | No: `opts.api_key or get_env_api_key(model.provider)`; `stream_simple_openai_completions` raises with neither, `stream_openai_completions` builds the client with `""` and sends no key; the SDK gets `api_key or ""` either way | Unchanged; a positive row pins the given key against `OPENAI_API_KEY` |
| `openai_responses.py` → same factory | No: `_resolve_client_api_key` (provider-keyed, `"unused"` with a header, else raise) | Unchanged; positive row as above |
| `openai_codex_responses.py` (`httpx`, bearer by hand) | No: no OAuth token → "No OAuth token for openai-codex" | Unchanged |
| `google_generative_ai.py` → `_google_client.create_client` | No in practice (`_resolve_api_key` is provider-keyed or raises); **latent**: the factory reads `GOOGLE_API_KEY` / `GEMINI_API_KEY` when given no key (probe S7) | Factory refuses a keyless call; a positive row pins the given key on the wire against both variables (round 1: dropping it passed every touched file) |
| `google_vertex.py` API-key path → `create_vertex_client` | No: `opts.api_key or get_env_api_key(model.provider)` | Unchanged; positive wire row as for Gemini |
| `google_vertex.py` ADC path | Ambient by design: ADC (`google.auth.default()`, incl. `GOOGLE_APPLICATION_CREDENTIALS`) for ANY provider with `api: google-vertex`, to its `baseUrl`; the env API key is dropped because project+location are explicit (probe S8) | Unchanged (§5.1); factory refuses neither-project-nor-location |
| Bedrock / Azure / Mistral | No adapter in this build (`providers-and-models.md`, "Adapter coverage") | — |
| `examples/selfhosted/selfhosted.py` (`AsyncOpenAI(api_key=access_token or "unused")`) | No: always explicit | Unchanged |

Variables the three SDK clients aelix builds (`AsyncAnthropic`, `AsyncOpenAI`,
google-genai `Client`) still read for themselves, sorted (re-checked in review round 1;
`ANTHROPIC_CUSTOM_HEADERS` was wrongly in this list and is now §2.1's). The list is not
exhaustive (review round 2): it leaves out logging, TLS (`SSL_CERT_FILE` /
`SSL_CERT_DIR`), test and replay switches, and the variables of SDK clients aelix never
constructs (Bedrock, Vertex and Foundry Anthropic clients, Azure OpenAI), and the Google
ADC chain reads more than the one variable named below.

- **Can carry a credential, handled:** `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
  and the chain's variables (§2.1), `ANTHROPIC_CUSTOM_HEADERS` (§2.1, `anthropic`
  only), `GOOGLE_API_KEY` / `GEMINI_API_KEY` (an explicit key or a refusal),
  `OPENAI_API_KEY` (always an explicit argument).
- **Can carry a credential, kept on purpose:** Google ADC (`google.auth.default()`),
  including `GOOGLE_APPLICATION_CREDENTIALS`, for a provider with `api: google-vertex` and no
  key (§5.1).
- **Read but never sent:** `ANTHROPIC_WEBHOOK_SIGNING_KEY`, `OPENAI_WEBHOOK_SECRET`
  (webhook verification only).
- **No credential:** `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`,
  `GOOGLE_GEMINI_BASE_URL`, `GOOGLE_VERTEX_BASE_URL` (only when a model has no base
  URL; the only catalog rows without one are the 42 `azure-openai-responses` rows,
  which have no adapter); `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` (identifiers, sent as
  `OpenAI-Organization` / `OpenAI-Project` to any OpenAI-API host, pi's SDK alike);
  `GOOGLE_GENAI_USE_VERTEXAI` / `GOOGLE_GENAI_USE_ENTERPRISE` (`create_client` does
  not pass `vertexai`, so either switches the `google` provider's client to Vertex
  express mode; the key stays the provider's own, round-1 verify inproc G4);
  `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` (an explicit key wins over them).

A project `.env` cannot set `ANTHROPIC_CUSTOM_HEADERS`, the base-URL variables,
`OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` or the `GOOGLE_GENAI_USE_*` switches (ADR-0203;
measured, `fix2/dotenv_refused.out`); the credential variables it can.

## 4. Before and after

Real CLI (`live/base.out`, `live/base-rpc.out` on `aab1f210`; `live/tree.out` on this
change):

| Row | Before | After |
| --- | --- | --- |
| R01 / R02 | the Anthropic key / token to the gateway | no request; `No API key for provider: mygw` |
| R03 | the gateway's header + the Anthropic key | the gateway's header only |
| R04 | the Anthropic key to `fireworks`' host | no request; `No API key for provider: fireworks` |
| R05 / C07 | `x-api-key: <ANTHROPIC_API_KEY>` | same |
| R06 | `Authorization: Bearer <ANTHROPIC_AUTH_TOKEN>` | same |
| C08 (L14) | bearer + empty `x-api-key` | bearer only |
| C01-C06, C09, C10 | no request ("No API key found for …", print/json) | same |
| C04 | `x-api-key: <models.json apiKey>` | same |

Two existing rows pinned the blank key (`captured.get("api_key") == ""` for OAuth and
Copilot); their comments claimed the blank emits no header, which L14 measured false. They
now pin `None`.

**Rows and sabotage.** The new file's 40 rows: 37 fail on `aab1f210`
(`red_on_aab1f210.out`, with the two re-pinned rows 39 failed / 17 passed over the three
files); the 3 that pass there are controls (a given key reaches the gateway; an injected
client is used; the parametrized provider list covers `fireworks`, `minimax`,
`vercel-ai-gateway`, `github-copilot`). In a throwaway worktree
(`sabkit/sabotage.py` / `.out`), 27 pieces, files restored by md5 after each: 25 red. The
two that survive alone are the two layers of the same guard: the explicit-credential
sentinel (S1) and re-assigning `auth_token` after `super().__init__` (S3). On 0.102 each
is redundant with the other (the sentinel already stops the env read; the re-assignment
overwrites what it would have read, and the subclass alone keeps the chain off); removed
together with either re-assignment (S26, S27) rows go red. The re-assignment is kept for
the older SDKs the range allows, which read `ANTHROPIC_AUTH_TOKEN` independently of
`api_key`; round-1 verify measured it on 0.40.0, and review round 1 on 0.40.0 and 0.97.0
(§7).

## 5. What this does not close, and choices recorded

1. **Vertex ADC stays ambient.** A provider with `api: google-vertex` and no key uses
   Application Default Credentials and sends the Google access token to its `baseUrl`,
   whatever the provider is called. pi does the same (`google-vertex.ts` `createClient`
   always builds `GoogleGenAI({vertexai: true, project, location, googleAuthOptions})`);
   ADC is the Vertex API's own auth, so choosing that `api` for a gateway is choosing
   Google auth. Not changed; an owner may decide to scope ADC to `google-vertex` as this
   ADR scopes `ANTHROPIC_AUTH_TOKEN` to `anthropic`.
2. **The Anthropic SDK's credential chain is no longer used, for `anthropic` too.**
   pi supports workload identity federation for `anthropic` (with `ANTHROPIC_AUTH_TOKEN`
   ahead of the env keys); aelix never documented the chain and it ran for every provider
   on this API. A request to `anthropic` with only federation variables now ends with
   `No API key for provider: anthropic` in the TUI and rpc (print and json already stopped
   at "No API key found"). Porting pi's federation is a follow-up.
3. **`ANTHROPIC_AUTH_TOKEN`'s place.** pi tries it before `ANTHROPIC_OAUTH_TOKEN` /
   `ANTHROPIC_API_KEY`; here it stays where it effectively was on `aab1f210` — last, only
   when no key and no auth header resolved — so a user with both keeps sending the key.
   `-p` / `--mode json` still do not count it as a key (C09, unchanged).
4. **Not measured:** a real model (fake keys only), Windows. SDKs other than 0.102.0 only
   through the client module on its own (0.40.0, 0.97.0; §7), not the whole CLI.
5. **`-p` / `--mode json` and a header-only Anthropic proxy: an open owner question.**
   With no key, no OAuth token and no `ANTHROPIC_AUTH_TOKEN`, and its auth only in
   `ANTHROPIC_CUSTOM_HEADERS`, provider `anthropic` gets its request in `--mode rpc` and
   the TUI (§2.2), while `-p` and `--mode json` stop before the turn with
   "No API key found", as on `aab1f210` (`has_configured_auth` does not read the
   variable; measured on both builds, `fix4/live-{final,base}.out`). Unchanged and not
   widened here; whether those modes should count the variable is for the owner.

The main loop re-confirmed items 1-3 in review round 1 (2026-10-07): unchanged.

## 6. Divergences from pi, stated (ADR-0235)

- pi's `streamSimple` throws `No API key for provider` synchronously; aelix's adapter is
  one async generator for `stream` and `stream_simple` (ADR-0045), so the message arrives
  as the stream's error event, as pi's `stream()` reports it.
- `ANTHROPIC_AUTH_TOKEN` order and federation: §5 items 2-3.
- `ANTHROPIC_CUSTOM_HEADERS` reaches provider `anthropic` only. pi's
  `@anthropic-ai/sdk` (0.129.0 at `b223082bb`, `packages/ai/package.json:70`) merges it
  into every client's default headers (`client.js:116`, read from the npm tarball), so in
  pi it reaches every provider on this API, a custom gateway included.
- **An auth header in `ANTHROPIC_CUSTOM_HEADERS` authenticates provider `anthropic`.**
  pi's `assertRequestAuth` reads only `options.headers` (`anthropic-messages.ts:325-326`,
  called at `:613`), so in pi a request whose only auth is in the variable throws
  `No API key for provider: anthropic` (unless federation is configured). Here, for
  `anthropic` only, an `x-api-key`, `Authorization` or `cf-aig-authorization` header in
  the variable counts, in any letter case, with a non-blank value; a blank value does not
  (as pi's `hasHeader`). This keeps `aab1f210`'s header-only proxy working (§8), and
  widens it: on `aab1f210` the SDK accepted only a non-blank `X-Api-Key` or
  `Authorization` spelled exactly that way, so a lower-case one or `cf-aig-authorization`
  alone was refused before sending (`Could not resolve authentication method`;
  `fix4/live-base.out` Y01, Z02-Z03).

## 7. Review round 1 (2026-10-07)

Verify (FAIL, 1 blocking) and Codex (4 findings) on `07fdc31d`; kits in
`.omc/probes/374-live/{verify,codex}/`, this round's in `.omc/probes/374-live/fix2/`.

1. **Layer 2 was unpinned** (verify, blocking). Dropping `self.auth_token = token`
   passed `tests/providers` + `tests/oauth` on `07fdc31d` (1119 passed,
   `repro_mutants_07fdc31d.out`). Against the real SDK (`sdk_versions.out`, the client
   module alone, `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` exported): without it,
   0.40.0 and 0.97.0 send `ANTHROPIC_AUTH_TOKEN` as a bearer with no key, next to a
   gateway's own header, and (0.97.0) next to a given key; with it, nothing. Kept, and
   pinned with a row that simulates the 0.40-0.97 constructor. Layer 1 measured
   redundant on 0.40.0, 0.97.0 and 0.102.0 alike; it is kept and pinned with a row that
   simulates a credential chain running for subclasses (§2.1).
2. **`ANTHROPIC_CUSTOM_HEADERS`** (Codex P1): §2.1. Real CLI on `07fdc31d`
   (`live-base07f.out`) and on this change (`live-tree.out`), fake values, local
   recorder, 0 CONNECT lines:

   | Row | Setup | `07fdc31d` | After |
   | --- | --- | --- | --- |
   | H01 | `mygw` with its own `Authorization` header, `x-api-key: <ambient>` (rpc, TUI) | both | the gateway's header only |
   | H02 | `mygw` `apiKey`, `X-Api-Key: <ambient>` (print/json/rpc) | the ambient value **instead of** the gateway's key | the gateway's key |
   | H03 | `mygw` `apiKey`, `x-api-key: <ambient>` (print/json/rpc, TUI) | two `x-api-key`: the key and the ambient value | the gateway's key |
   | H04 | `mygw` no key, `Authorization: <ambient>` | no request (`No API key …`) | same |
   | H05 | `fireworks` with its own key, `X-Api-Key: <ambient>` | the ambient value instead of the key | the `fireworks` key |
   | H06 / H07 | `anthropic`, `ANTHROPIC_API_KEY`, `X-Api-Key` / `Authorization: <ambient>` | ambient replaces the key / bearer beside it | same (kept) |
   | H08 | `anthropic`, `ANTHROPIC_API_KEY`, no variable | the key | same |

   H01 in print/json stops at "No API key found" on both builds (§4). In-process,
   `sdk_versions.out` P1-P3 on 0.102.0: the ambient `X-Api-Key` went out from the factory
   with no credential, beside a gateway header and instead of a given key on
   `07fdc31d`; nothing after. 0.40.0 and 0.97.0 do not read the variable.
3. **False text** (Codex): the CHANGELOG entry, the `models-json` paragraph (and its
   bundled copy), §3's list of variables the SDKs still read and the
   `create_async_client` docstring said the SDK reads no credential anywhere and that
   the variable carries none; rewritten for the final code. With verify's notes: the
   `models-json` paragraph now says what `-p` / `--mode json` do; the CHANGELOG no
   longer says every delegated child takes the rpc turn path (one-shot children run
   `--mode json -p`); pi line numbers corrected (`PiAnthropic` `:335-339`, `apiKey` /
   `authToken` `:1069-1070`, `createClient` `:982-1077` — still one short, `:982-1078`
   since round 2, §8); §3's `openai_completions`
   row and the `GOOGLE_GENAI_USE_*` switches.
4. **Explicit keys unpinned on the wire** (Codex): dropping the key in
   `_google_client.create_client` passed every file `07fdc31d` touched (149 passed,
   `repro_mutants_07fdc31d.out`). New rows export `GOOGLE_API_KEY`, `GEMINI_API_KEY`,
   `OPENAI_API_KEY` and the Anthropic variables and assert only the given key on the
   wire for Gemini, Vertex (API-key path), OpenAI completions and OpenAI responses, and
   that a keyless `stream_openai_completions` turn never sends `OPENAI_API_KEY` (the
   factory's `""` sentinel; this round's sabotage S43 survived until that row).

## 8. Review round 2 (2026-10-07)

Verify (FAIL, 2 blocking) and Codex (no credential-isolation defect; its 18-row leak matrix
passed) on `addfd6bf`; kits in `.omc/probes/374-live/{verify2,codex2}/`, this round's in
`.omc/probes/374-live/fix3/`. The owner decisions in §5 and §6 stand as recorded.

1. **A header-only Anthropic proxy was refused** (verify B2, Codex P2; a regression of
   review round 1). §2.1 kept `ANTHROPIC_CUSTOM_HEADERS` for provider `anthropic`, but the
   guard in front of the client looked only at the key and `options.headers`, so
   `anthropic` with its own `baseUrl`, no key, no OAuth token, no `ANTHROPIC_AUTH_TOKEN`
   and its auth in the variable stopped with `No API key for provider: anthropic`. Real
   CLI, fake values, local recorder, 0 CONNECT lines (`fix3/live-*.out`, `--mode rpc`):

   | Row | Variable | `aab1f210` | `addfd6bf` | After |
   | --- | --- | --- | --- | --- |
   | W17 | `X-Api-Key: <v>` | one request, that header | no request | one request, that header |
   | W18 | `Authorization: Bearer <v>` | one request, that header | no request | one request, that header |
   | X01 / X02 | `x-api-key` / `authorization` (lower case) | no request (the SDK's own case-sensitive check: `Could not resolve authentication method`) | no request | one request, that header |
   | X03 / X04 | `x-other: <v>` / a blank `X-Api-Key` | no request (SDK) | no request | no request (`No API key …`) |
   | X05 / X06 | keyless `mygw` / `fireworks`, an auth header in it | one request **with that header** | no request | no request (`No API key …`) |
   | X09 | `ANTHROPIC_AUTH_TOKEN` + `x-api-key: <v>` | bearer + that header | same | same |

   The TUI (pty) agrees: W17 and X02 send that header, X03 and X05 end with
   `✖ No API key for provider: …`. `-p` and `--mode json` stop all of these before the
   turn with "No API key found", on all three builds (`has_configured_auth` does not read
   the variable; unchanged). The guard now counts, for provider `anthropic` only, an auth
   header among the headers `read_env_custom_headers()` parses from the variable exactly
   as the SDK does (§2.2). On real SDKs 0.40.0, 0.97.0, 0.98.0, 0.101.0 and 0.102.0
   (`fix3/sdk_versions_ch.out`, the client module alone): 0.98+ send exactly that header;
   0.40.0 and 0.97.0, which do not read the variable, refuse with
   `Could not resolve authentication method` and send nothing.
2. **The OAuth branch's flag was unpinned** (verify B1). M16 (dropping
   `env_custom_headers=env_custom_headers` from the OAuth client build) passed
   `tests/providers` + `tests/oauth`. New rows: provider `anthropic`, a stored `/login`
   token and `ANTHROPIC_OAUTH_TOKEN`, each with `x-other` and with `X-Api-Key` in the
   variable; the request carries the bearer and that header (W11, W13, X07, X08 live in
   print, json and rpc agree, `fix3/live-tree.out`).
3. **§2.3 said both OpenAI adapters refuse a keyless call before building a client**
   (Codex). `stream_openai_completions` does not: it sends the request with no auth
   header (no ambient key; the parent build did the same). §2.3 now says what each
   adapter does, and that pi throws there (a pre-existing difference, a follow-up).
4. **The Vertex guard's boundary was unpinned** (Codex). Requiring project **and**
   location passed every changed test file. New rows build a keyless Vertex client with
   only a project and with only a location, `GOOGLE_API_KEY` and `GEMINI_API_KEY`
   exported: the SDK holds no key and the request carries only the proxy's header.
5. **Text** (verify): `createClient` is `anthropic-messages.ts:982-1078` (the docstring
   said `:982-1077`, the test module `:982-1069`); §3's variable list is now called what
   it is, a sorted list that is not exhaustive; the `models-json` guide said the variable
   reaches "never a provider you define here", but a `models.json` entry named
   `anthropic` is provider `anthropic` and does get it — the guides now say the name
   decides.

**Rows and sabotage.** The module has 95 rows (32 new). On `addfd6bf` the new rows fail
6 (`fix3/red_on_addfd6bf.out`: the five header-only rows and the parse row); the rest pin
mutants. The three #374 files on `aab1f210`: 79 failed, 32 passed
(`fix3/red_on_aab1f210.out`). Python 3.11.15: 95 passed. Sabotage in a throwaway worktree
(`fix3/sabotage.py` / `.out`), each piece a set of exact replacements, `tests/providers` +
`tests/oauth`, the file restored and its md5 checked after each: 77 pieces, 77 red — review round 1's
S01-S43 (S12, S14-S16 re-anchored on the longer guard), verify round 2's M01-M24 and this
round's N01-N12. M16 alone (no `-x`) fails the four OAuth rows; Codex's
project-and-location mutant (N01) fails both one-of rows; dropping the new guard clause
(N04), counting the variable for every provider (N05) or for any value (N06), and
holding `ANTHROPIC_AUTH_TOKEN` back on it (N11) are each red.

## 9. Review round 3 (2026-10-07)

Verify on `f852df2f` (kit `.omc/probes/374-live/verify3/`); this round's in
`.omc/probes/374-live/fix4/`. Code unchanged apart from a comment; rows and text only.

1. **The guard clause's request-header boundary was unpinned** (verify B). Two plausible
   wrong forms of §8's clause, reading the variable only when the request carries no
   headers of its own — `has_auth_header(opts.headers or <variable's headers>)` (P13) and
   `env_custom_headers and not opts.headers and …` (P08) — passed every row, because no
   header-only row sent `options.headers`. They refuse a header-only Anthropic proxy whose
   `models.json` override adds a non-auth header: verify3 measured P13 and P08 on the real
   CLI (`verify3/sab_live.out`: Z01 / Z02 rpc, no request). New rows: the adapter with
   `headers={"x-team": "team-a"}` and each of the five auth spellings in the variable, and
   the rpc turn path (models.json -> registry -> CLI auth callback -> harness -> adapter)
   with `anthropic` re-pointed and `headers: {x-team: team-a}`, `X-Api-Key` and a
   lower-case `authorization` in the variable: one request carrying the variable's auth
   header and `x-team`. P13 and P08 each fail all seven, and round 2's 77 pieces stay red
   (S15 re-anchored on the rewritten comment): 79 pieces, 79 red (`fix4/sabotage4.out`).
   With this change's test files, `addfd6bf` fails all seven and `aab1f210` the four
   lower-case and `cf-aig-authorization` ones, as its SDK did. Real CLI
   (`fix4/live-final.out`, recorder logging `x-team`): Z01 (`X-Api-Key`), Z02
   (`authorization`) and Z03 (`CF-AIG-Authorization`) in rpc send one request with that
   header and `x-team: team-a`; Z04 (a blank `X-Api-Key`) ends with
   `No API key for provider: anthropic`; `-p` and `--mode json` stop all four with
   "No API key found". On `aab1f210` (`fix4/live-base.out`): Z01 rpc sends the same
   request; Z02-Z04 and Y01 send nothing (the SDK's `Could not resolve authentication
   method`); `-p` / `--mode json` stop with "No API key found".
2. **Text** (verify): §6 now states the divergence §8 made (lower-case and
   `cf-aig-authorization` auth headers in the variable authenticate `anthropic`; blank
   values do not; pi counts none); the CHANGELOG names `cf-aig-authorization` and the
   blank-value rule; §2.2, §5 item 5, the CHANGELOG and both guides say exactly that `-p`
   and `--mode json` keep `aab1f210`'s "No API key found" for a header-only setup
   (`has_configured_auth` unchanged, an owner question).
