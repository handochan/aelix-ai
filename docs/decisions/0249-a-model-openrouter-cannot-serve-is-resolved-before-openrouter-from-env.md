# 0249. A `--model` OpenRouter cannot serve is resolved before OpenRouter-from-env

Status: Accepted (2026-09-30) — §2.1's rung 0 and OpenRouter-from-env rung, §2.6's split rule and §5 superseded by ADR-0250 (2026-10-02, #362)
Superseded by: [ADR-0250](0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md) (§2.1, §2.6, §5; X1 §2.3, S §2.4 and §2.7 stand)
Date: 2026-09-30
Amends: **ADR-0195 §Decision 4** (the precedence ladder `resolve_model` owns — a rung 0
now runs before the OpenRouter-env path) and **ADR-0195 §"Known limitations" bullet 1**
(extension providers no longer reach the registry only after the harness is built). See
the dated `#344 amendment 2026-09-30` notes in ADR-0195. Nothing is superseded: registry
lookup, unanimous backfill, the `default_provider` slot and the `is_runnable` gates stand.
Relates: ADR-0067 (the `core/model_resolver.py` port of pi's `resolveCliModel`, still
without a production caller — not adopted here, see §5), ADR-0203 (a cwd `.env` may hand
aelix a credential; this decision consults none, and the `OPENROUTER_API_KEY` half of that
vector stays open as #362), ADR-0196 / ADR-0197 (a profile's `model:` and the delegated
child's argv), ADR-0235 (the divergence from pi in §5 is deliberate and needs no parity
ADR).
Issue: #344 (P0, milestone `v0.1.0-beta.3`). Owner decisions: 2026-09-30.
Tests: `tests/cli/test_provider_prefix_rung.py`, `tests/cli/test_launch_route_344.py`,
`tests/agents_ext/test_child_route_344.py`, `tests/tui/test_model_undecided_registry_344.py`.
Follow-ups: #362, #363, #364, #365 (§6).

With `OPENROUTER_API_KEY` in the environment, `resolve_model` turned every `--model`
string into an OpenRouter model id — including `<provider>/<id>` for a provider the user
had defined in `models.json` or through an extension, whose whole point is that it is
*their* endpoint. The prompt went to a third party; so did an `--api-key` meant for the
user's endpoint. The owner's own configuration hit it.

## 1. What was measured

Base `fbead6e0` (the last code commit is `6b6c8b93`), the real CLI, fake keys only.
HTTPS egress went to a local recording CONNECT proxy (it answers 403, so nothing reached a
vendor), `models.json` and extension endpoints were local recording listeners, and a
`OPENROUTER_BASE_URL` pointed at a local listener where the bearer mattered. Each row is
one `aelix … -p hi` from a scratch cwd with an isolated `AELIX_CODING_AGENT_DIR`.

| row | input (OR = `OPENROUTER_API_KEY` exported) | where the request went |
| --- | --- | --- |
| C1 | `--model retryprobe/held-model` (models.json custom), OR | `CONNECT openrouter.ai:443` |
| C4 | `-e extprov.py --model extprov/m1`, OR | `CONNECT openrouter.ai:443` |
| C5 / C6 | same without OR; `--provider extprov --model m1` | nowhere — `No provider registered for api='unknown'` |
| C13 | bare `--model held-model` (only retryprobe serves it), OR | `CONNECT openrouter.ai:443` |
| C15 | `--agent probe` with `model: retryprobe/held-model`, OR | `CONNECT openrouter.ai:443` |
| C15-child | a real delegated print child of a profile `model: retryprobe/held-model` / `extprov/m1` | both `CONNECT openrouter.ai:443` (argv `--model retryprobe/held-model`, `--no-extensions`) |
| C17 | `settings.json` `defaultModel: "retryprobe/held-model"` (no provider), OR | `CONNECT openrouter.ai:443` |
| C20 | `--model RetryProbe/held-model`, no OR | refused: `provider 'RetryProbe' could not be resolved to a known API protocol` |
| C21 | `--model retryprobe/held-model --api-key <probe>`, OR | the OpenRouter listener, **bearer = the probe key** |
| C21x | `-e extprov.py --model extprov/m1 --api-key <probe>`, OR | the OpenRouter listener, **bearer = the probe key** |
| C7 | models.json `providers.openai.baseUrl` → local proxy; `--provider openai --model gpt-4o-mini` | `CONNECT api.openai.com:443` — the proxy saw nothing |
| R4 | `--model openai-codex/gpt-5.1`, OR | `CONNECT openrouter.ai:443` (OpenRouter answers 400) |
| owner | the owner's real `~/.aelix/agent` (`--no-session`; not read-only in effect — the base run wrote its `stats-history.jsonl`, see below), `--model ollama/qwen3.6:35b-a3b`, OR (fake) | 12 × `CONNECT openrouter.ai:443` (auto-retry), never `127.0.0.1:11434` |

Two pre-existing defects sat next to the leak and are fixed with it, because the fix does
not work without them: an extension provider could not be selected at launch at all
(C5/C6 — the model was resolved before `discover_and_load_extensions` ran, ADR-0195's own
Known limitation), and a `models.json` `baseUrl` on a BUILT-IN provider was ignored at
launch (C7 — the static catalog entry is returned before the registry copy, which is the
only one carrying the override).

## 2. Decision

### 2.1 Rung 0, before the OpenRouter-from-env rung — configuration only

> **Superseded 2026-10-02 by [ADR-0250](0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md) (#362).** Both rungs are gone:
> the launch follows pi's `resolveCliModel` order, and an OpenRouter key reaches an id
> this build cannot place only through ADR-0250's guard 2 — an OpenRouter credential of
> the user's own, never a cwd `.env`'s. What this section established about user-defined
> providers (matched first and alone, case-insensitively; never left for OpenRouter) and
> about named providers' case rule is kept there verbatim.

When `OPENROUTER_API_KEY` is set, `--model` is non-empty and `--provider` is absent (an
explicit `--provider X` keeps its meaning, `--provider openrouter` is still OpenRouter),
`resolve_model` first asks whether OpenRouter can serve the string at all
(`runtime_bootstrap._openrouter_cannot_serve`):

- **0a** — the first-slash prefix, matched case-insensitively, names a **user-defined**
  provider → resolved inside it, and never handed to OpenRouter, even for an id it does
  not list (§2.2).
- **0b** — otherwise, the whole string — bare, or slashed without a user-defined prefix —
  is an id exactly **one** user-defined provider serves → that model. The owner's bare
  `qwen3.6:35b-a3b` reaches their ollama; a gateway listing `openai/gpt-4o` verbatim
  serves `openai/gpt-4o`, and one listing `anthropic/claude-sonnet-4.5` serves that
  slashed id although `anthropic` is an OpenRouter namespace. Ids are compared exactly.
  A built-in re-pointed by a provider-level `baseUrl` serves its **whole catalog**, so
  with `providers.openai.baseUrl` set a bare `--model gpt-4o-mini` goes to the gateway —
  on `fbead6e0` it went to OpenRouter (measured, `/tmp/344-work/fix2/n3_probe.py`: base
  `openrouter gpt-4o-mini https://openrouter.ai/api/v1`, this branch `openai gpt-4o-mini
  <the gateway>`; `gw anthropic/claude-sonnet-4.5 <its baseUrl>` for the verbatim row).
- **0c** — the prefix names a **catalogued** provider that is **not an OpenRouter
  namespace** → resolved inside it (catalog hit, else the unanimous-sibling backfill of
  ADR-0195 §3). `openai-codex/gpt-5.1` goes to Codex.

Everything else reaches the OpenRouter rung exactly as before: the 9 prefixes that are
both a catalogued provider and an OpenRouter namespace, prefixes that are not providers
(`meta-llama/…`), bare ids no user-defined provider serves, OpenRouter ids no catalog
knows. `OPENROUTER_DEFAULT_MODEL` — an id taken from that variable because no `--model`
was given — is named for OpenRouter and skips rung 0.

**User-defined** (`ModelRegistry.get_user_defined_providers`, recomputed on every
`_load_models`): a `models.json` provider this build's catalog does not know; a
catalogued provider whose `models.json` entry sets a **provider-level** `baseUrl`; an
extension `register_provider` provider — every one with an uncatalogued name, and a
catalogued name only when the registration brings `models`. `modelOverrides`, `headers`
or `compat` alone do not count: they tune a provider without moving it (the owner's
`models.json` has such entries for `openrouter`, `zai`, `zai-coding-cn`, `huggingface`,
`opencode`, `opencode-go`, and none of them becomes user-defined). `openrouter` itself
never counts, even re-pointed: `openrouter/auto` is an OpenRouter id, and the OpenRouter
rung adopts that `baseUrl` (§2.4). All three "does not count" rules are pinned
(`test_model_overrides_alone_…`, `test_compat_or_headers_alone_…` on two overlapping
namespaces, `test_a_re_pointed_openrouter_keeps_openrouter_ids`).

A catalogued name an extension registers **with models** is merged into the registry
next to the catalog's own models, and the registration's `api_key` becomes the whole
provider's. For rung 0 such a provider "serves" only what the registration brought: 0a
resolves `openai/<id>` among the registration's models (§2.2), and 0b counts only their
ids — so a bare catalog id such as `gpt-4o-mini` is not claimed for it.

**OpenRouter namespaces** are derived in code (`runtime_bootstrap.openrouter_namespaces`):
the first path segment of every OpenRouter id in the bundled catalog, lower-cased.
Measured on `fbead6e0`: 35 catalogued providers, 50 namespaces, 9 overlapping (`anthropic
deepseek google minimax moonshotai nvidia openai openrouter xiaomi`) — those keep going to
OpenRouter — and 26 not (`amazon-bedrock ant-ling azure-openai-responses cerebras
cloudflare-ai-gateway cloudflare-workers-ai fireworks github-copilot google-vertex groq
huggingface kimi-coding minimax-cn mistral moonshotai-cn openai-codex opencode opencode-go
together vercel-ai-gateway xai xiaomi-token-plan-ams xiaomi-token-plan-cn
xiaomi-token-plan-sgp zai zai-coding-cn`) — those go to their provider. **When OpenRouter
later adds a namespace equal to a provider name**, the catalog regeneration that ships it
moves that prefix to OpenRouter for OpenRouter-key users, with no code change; until that
regeneration the prefix goes to the provider, and `--provider openrouter` reaches
OpenRouter's new ids. The regeneration's release notes are where that flip has to be said.

**Why no credential is consulted.** The design lane's recommended policy decided a
catalogued prefix by whether the user could authenticate to that vendor. The critique
measured what that means: a cloned repo's `.env` planting `OPENAI_API_KEY` moved an
OpenRouter-key user's `openai/gpt-4o-mini` to `api.openai.com` under the repo's key (M1),
and an expired Anthropic OAuth record in `auth.json` still counted as "can authenticate"
(S2). With rung 0 reading only the catalog, `models.json` and extension registrations, a
vendor key — wherever it came from — cannot change a route: `openai/gpt-4o-mini` goes to
OpenRouter with no OpenAI key, with an exported one and with one `load_dotenv` admitted
(`test_overlapping_namespace_stays_openrouter_whatever_vendor_key`, three cases).

The prefix is matched case-insensitively everywhere the slash shorthand is split, with or
without an OpenRouter key (C20) — pi's `providerMap` (`packages/coding-agent/src/core/model-resolver.ts:430-433`
@ 1ff5b6fdd) and the in-session `/model` already did. A **user-defined provider is matched
first and on its own** — its exact spelling, else the one user-defined name that differs
only in case — and only then the catalogue and the registry. Codex's cross-review of
`8c9d6397` (C1) found the combined match preferring the catalogue's exact spelling: with a
`models.json` provider named `OpenAI`, `openai/m1` matched the catalogue's `openai` — not
user-defined, and an OpenRouter namespace — so with `OPENROUTER_API_KEY` set it went to
OpenRouter with `--api-key` as the bearer (Codex's probe through the real completions
adapter and a mock transport: `('http://127.0.0.1:18081/or/v1/chat/completions', 'Bearer
fake-custom-cli', True)`; after the fix `('http://127.0.0.1:18082/custom/v1/chat/completions',
'Bearer fake-custom-cli', True)`, and the real CLI reaches the custom listener only), and
without the key it went to the catalogue's `openai`, `api.openai.com`. pi lands where this
does: its `providerMap` is filled built-ins first, so the custom entry owns the lower-cased
key. Two user-defined names that differ only in case, with a prefix spelling neither, are
refused (`api='unknown'`) — never guessed, never resolved in a same-spelled catalogue
provider; `runtime_bootstrap.ambiguous_provider_message` names both in the print/json
refusal and the interactive startup warning. Two catalogue-only names that differ only in
case still match neither.

The same rule covers a provider that is **named** rather than prefixed: `--provider`,
settings `defaultProvider` and an agent profile's `provider:`
(`runtime_bootstrap._named_provider`, used by `resolve_model` for the first two and so for
every profile path, and by `child_model_flags` for a delegated child's `--provider`).
Codex's second pass on `ebfe411a` (F1, §7) found it missing there: with the `models.json`
`OpenAI` above, `--model openai/m1` reached the user's endpoint but `--model m1 --provider
openai --api-key K -p …` resolved the catalogue's `openai` and sent the prompt, with `K`,
to `https://api.openai.com/v1/responses` — the shadowing the prefix rule prevents — and
`--provider OPENAI` was `api='unknown'`; a profile with `model: m1` / `provider: openai`
gave its child `--provider openai`. Now a named provider is matched case-insensitively, a
user-defined one first and on its own (an exact user-defined spelling always selects it),
then the catalogue and the registry, else it stays as typed; a spelling two user-defined
providers share only up to case is held on `api='unknown'` and refused naming both
(`ambiguous_provider_name_message`: `--provider openai matches the providers you defined
'OPENAI' and 'OpenAI', which differ only in case. …`; `settings defaultProvider …` for the
settings value). This is about spelling, not routing: a named provider still switches
rung 0 and the OpenRouter rung off exactly as before unless it names `openrouter` — which
`--provider OpenRouter` now does (it used to be an unknown provider). A re-pointed
built-in (`providers.openai.baseUrl`) is reached for any case of `--provider openai`.

### 2.2 An id a user-defined provider does not list

Rung 0a claims the prefix whatever follows it. Resolution inside the provider is today's
explicit-provider tail plus one step: catalog hit (base URL adopted, §2.4) → the
registry's own entry → **a backfill from that provider's own registry models**, when they
all declare one `api` (their `base_url` and `compat` carried only when unanimous) → the
catalog's unanimous siblings → a bare `Model` whose `api` stays `"unknown"`, which
`is_runnable` refuses with a message naming the provider. So `ollama/<a model pulled after
models.json was written>` reaches the user's ollama, and an extension provider registered
with no models is a clear refusal — neither falls through to OpenRouter. pi builds the
same fallback from the provider's first model (`buildFallbackModel`,
`model-resolver.ts:175-189` @ 1ff5b6fdd); the unanimity guard is ADR-0195 §3's, for the
reason given there.

`_resolve_in_provider` is the explicit-provider tail for **every** caller, not only rung
0: `--provider X --model <id>`, and the slash shorthand with no OpenRouter key, reach
it too. So the backfill step applies without an OpenRouter key as well — `--provider
retryprobe --model unlisted-model` and `--model retryprobe/unlisted-model` take
`retryprobe`'s own `api`/`base_url`, where `fbead6e0` returned `api="unknown"` and the
run was refused (measured, `/tmp/344-work/fix2/n2_probe.py`;
`test_unlisted_id_is_backfilled_from_the_provider_without_openrouter_too`).

For a catalogued name an extension took over (§2.1), "inside the provider" is inside the
registration: its own entry, else a unanimous backfill from the models it registered,
else the same refusal — never the catalog step, which would send
`openai/gpt-4o-mini` to `api.openai.com` with the extension's key (measured on the
first cut of this branch, `0fcc3333`; `fbead6e0` sent it to OpenRouter under the user's
key). pi reaches the same place differently: its `registerProvider` with models replaces
the provider's models, so the catalog id is not found there.

### 2.3 X1 — the launch model is resolved after the extensions load

`_build_harness_options` now calls `loaded.runtime.bind_model_registry(model_registry)`
right after `discover_and_load_extensions`, and resolves the model after that — on every
build: first launch, `/new`, `/fork`, `/resume`, `/reload`. This is pi's order:
`createAgentSessionServices` flushes `pendingProviderRegistrations` into the model runtime
(`packages/coding-agent/src/core/agent-session-services.ts:158-169` @ 1ff5b6fdd) before
`main.ts` resolves the CLI model. The later bind after `AgentHarness(opts)` stays; it is
idempotent (the queue was drained) and still flushes anything queued during construction.
A registration made in a `session_start` handler reaches the bound registry immediately
(`ExtensionAPI.register_provider` fans out) but after the launch resolve — pi resolves at
the same point, and pi, with no OpenRouter rung, then errors. Here, with
`OPENROUTER_API_KEY` set, the OpenRouter rung had already taken `--model sessext/m1`, and
the print-mode #98 gate — which re-resolves after `session_start` — judged `sessext`
and let the run go to `openrouter.ai` (measured on `0fcc3333`: repeated `CONNECT
openrouter.ai:443` while retries ran). `runtime_bootstrap.late_registered_route`, called from `cli/entry.py`
once the runtime exists, now recognises that case — the harness is on `openrouter`, the
string came from `--model` with no `--provider`, and a re-resolve over the registry as it
is now lands on a user-defined provider — or is held because the prefix now matches two
providers `session_start` registered that differ only in case (below).

Every mode then switches the running harness onto that provider before the first prompt,
through `cli/model_switch.switch_to_late_registered_route`, which calls the function the
`/model <argument>` handler itself calls (`switch_model_argument`, extracted from
`tui/commands.py`): the same resolution over the live registry, the same runnability
refusal, the same `set_model` (so the same `model_select` event). How it got here: the
first cut refused `-p` / `--mode json` and only warned in interactive and RPC, where the
first prompt still went to OpenRouter (measured on `8c9d6397` in a pty: the warning at the
top of the first frame, then `hi` → repeated `CONNECT openrouter.ai:443` while retries
ran; an RPC `prompt` the same); fix round 2 switched interactive and RPC; round 3 switches
print and json too, so every mode honours `-e ext --model <its provider>/<id>` alike.

It does **not** persist. `/model` saves the pair as `defaultProvider`/`defaultModel`; the
launch passes `persist=False`. A `--model` launch never writes settings, and round 2's
persisted pair made the next plain launch — which resolves before its `session_start` —
start on `Model(m1, sessext, api='unknown')` until `/model`. One line on stderr names the
provider (`Note: switched to sessext/m1 as /model sessext/m1 would (not saved as the
default model): …`), followed, as `/model` shows them, by the switch's allow-list warnings
and its #136 caution for an id the provider does not list (`⚠ 'm2' is not in this build's
catalog for sessext — …`). `--api-key` moves from `openrouter` to the switched provider
(§2.5).

If the `/model` path refuses (the provider has no credential, `/scoped-models` excludes it
…), the harness is put on `Model(id, provider)` with `api="unknown"`. Interactive and RPC
start and say to run `/model` (the TUI's #189 turn gate refuses the turn naming `/model`;
an RPC `prompt` fails at the adapter lookup); `-p` and `--mode json`, which have no
`/model`, refuse the run (`Error: --model sessext/m1 names provider 'sessext', … switching
to it failed (…). No prompt was sent.`). No request is made either way (measured in round
2: TUI and RPC with `enabledModels: ["retryprobe/held-model"]` — no request at all). Only
if even that placeholder is rejected (a `model_select` handler raises) is the launch
refused in every mode.

**A case clash among late registrations** (Codex second pass on `ebfe411a`, F3):
`session_start` registers both `SessExt` and `SESSEXT`, and `--model sessext/m1` matches
neither exactly. `-p` was refused at its #98 gate, naming both; but the late check
returned nothing — the held spelling `sessext` is in neither set — so RPC started on
`openrouter sessext/m1` and sent the prompt to OpenRouter (Codex's probe:
`ambiguous_rpc_mock_requests [('http://127.0.0.1:18081/or/v1/chat/completions', True)]`),
and the TUI did the same. `late_registered_route` now returns the ambiguity too, and
`switch_to_late_registered_route` holds the harness on `Model('m1', 'sessext')` without
asking `/model` — whose credential-filtered pool could hold only one of the two and switch
to it. Every mode lands the same way: print and json refuse naming both (`… No prompt was
sent.`), interactive and RPC start held with one `Warning:` naming both. Measured after
(real CLI, fake keys, `/tmp/344-work/fix4/e2e`): RPC `prompt` → `No provider registered
for api='unknown'`, nothing at the listener (on `ebfe411a`: `POST /or/v1/chat/completions
model=sessext/m1`); the TUI in a 120x40 pty, `hi` → `✖ model 'm1' (provider 'sessext')
could not be resolved …`, nothing at the listener but the update check's CONNECT (on
`ebfe411a`: `✖ Error code: 400 … /or/v1/chat/completions model=sessext/m1`).

**A provider registered and unregistered in `session_start`** (Codex second pass, F2) is
not in the registry as it is when the launch looks, so `sessext/m1` is an unknown prefix
and goes to OpenRouter by the owner's rule — the same as a launch that never registered
it (Codex's probe: `mock_requests [('http://127.0.0.1:18081/or/v1/chat/completions',
True)]`). That is the rule, not a leak: the launch decides on the registry's final state,
and an extension that unregisters its provider has said it is not there.
`test_a_provider_unregistered_before_session_start_returns_is_unknown` pins it.

Measured in round 3 (real CLI, fake keys, `OPENROUTER_BASE_URL` at a local recording
listener, https through a recording CONNECT proxy, `/tmp/344-work/fix3/e2e`): the TUI in a
120x40 pty with `-e sessext.py --model sessext/m1` — the `Note:` line above the banner,
the banner `model: m1` / `baseurl: http://127.0.0.1:18864/sess/v1` (on `8c9d6397`:
`sessext/m1` / `https://openrouter.ai/api/v1`), the footer `✱ m1`, `hi` → `EXT POST
/sess/v1/chat/completions model=m1 auth=ext`, nothing to the OpenRouter listener, no
CONNECT to openrouter.ai, and no `settings.json` in the agent dir; `--model sessext/m2`
adds the `⚠` caution under the Note. `-p hi` and `--mode json -p hi` → the same `EXT`
request (on `62ec77d2`: refused). Nothing between the old resolve site and the new one
read `model`.

**Rebuilds.** `/new`, `/reload`, `/fork` and `/resume` re-resolve `--model` in
`_build_harness_options`, before their own `session_start`, and still land on the
session_start provider — because the shared registry keeps the registration the previous
runtime's `session_start` made. Measured with real turns against local listeners
(entry-level harness, `/tmp/344-work/fix3/r4d`): launch, `/new`, `/reload`, `/fork`,
`/resume` → `EXT /sess/v1/chat/completions model=m1` each, nothing to the OpenRouter
listener (the same on `62ec77d2`).
`test_rebuilds_after_a_late_switch_stay_on_the_session_start_provider` pins it; a registry
that forgot extension registrations between builds turns it red.

That reuse is also why **a failed `/reload` keeps an extension's earlier registration**
(Codex C4, `/tmp/344-work/codex/probe_reload_stale.py`: an extension replaced by one that
raises on load, then `/reload` → the harness still on `extprov` at
`http://127.0.0.1:9/ext/v1`, `extprov` still registered, its key still `fake-ext`). This is
pi's behaviour, kept knowingly: `ModelRuntime.extensionProviders` survives a reload and is
deleted only by an explicit `unregisterProvider`
(`packages/coding-agent/src/core/model-runtime.ts:177` and `:929-935` @ 1ff5b6fdd). An
extension that wants its provider gone says so with `unregister_provider`.
Re-verified: `tests/cli/test_register_provider_bootstrap.py`, the extension suites, and a
`/reload` that makes a provider appear only after the first build
(`test_a_provider_registered_only_after_reload_is_the_rebuilt_model` — a bind limited to
the first build turns it red).

### 2.4 S — a `models.json` `baseUrl` on a built-in provider is honoured at launch

A catalog hit for a provider whose `models.json` entry sets a provider-level `baseUrl`
adopts that URL, keeping the catalog's `api`, window, cost and thinking map — the
`enrich_copilot_base_url` shape. The static-sibling backfill adopts it too, and so does the
OpenRouter rung for `providers.openrouter.baseUrl`, after an exported
`OPENROUTER_BASE_URL` (which a `.env` cannot set, ADR-0203). Such a provider is
user-defined (§2.1), so `openai/gpt-4o-mini` with a corporate gateway in `models.json`
goes to the gateway, with the gateway's headers — not to OpenRouter, and not (the
critique's M2) to `api.openai.com` with the gateway key.

**The bearer is the auth cascade's, unchanged.** `ModelRegistry.get_api_key_and_headers`
asks the AuthStorage cascade (runtime `--api-key`, `auth.json`, the provider's env var)
first and uses the `models.json` `apiKey` only when that finds nothing — pi's order. So an
`OPENAI_API_KEY` in the environment, including one `load_dotenv` admitted from a cloned
repo's `.env`, is what the re-pointed `openai` sends to the gateway (review of
`0fcc3333`: `auth=corp` with no env key, `auth=vendor` exported, `auth=dotenv` planted;
`test_re_pointed_built_in_bearer_follows_the_auth_cascade` pins all three). The `/model`
picker already did this on `fbead6e0`; #344 makes the launch path reach the gateway too,
so it now applies there. No credential changes the *route* (§2.1). This is decided
(option (b), the cascade, as `/model` already did on `fbead6e0`); the owner's
alternative (a) — the `models.json` `apiKey` of a provider re-pointed by a
provider-level `baseUrl` outranks the cascade, a stated pi divergence — is filed as
**#363**.

At launch only the host moves: the `models.json` provider-level `compat` and
`modelOverrides` that `/model` applies (it hands over the registry copy) are not adopted —
decision 3 says "keep the catalog api and metadata". `tests/cli/test_runtime_bootstrap.py::test_resolve_model_catalog_hit_wins_over_registry`
pins the `api` only and stays green unchanged. That launch/`/model` difference is part
of **#363**.

### 2.5 What `--api-key` means

The key is attached to the provider of the model the first harness resolved — after the
extensions loaded — not to the result of a pre-extension resolve. The early resolve stays
only to refuse `--api-key` with no model at all. Per rung, with `OPENROUTER_API_KEY` set:

| the model | `--api-key K` goes to |
| --- | --- |
| 0a `retryprobe/held-model` (models.json) | `retryprobe` |
| 0a `extprov/m1` (extension) | `extprov` — before this, `openrouter`: the pre-extension resolve could not see it (X') |
| 0b bare `held-model` | `retryprobe` |
| 0c `xai/grok-4.3` | `xai` |
| OpenRouter rung `openai/gpt-4o-mini` | `openrouter` — so K must be an OpenRouter key; `--provider openai` if it is an OpenAI one |
| explicit `--provider openai --model gpt-4o-mini` | `openai` |

Without an OpenRouter key every row is the provider the prefix names — a user-defined one
first (§2.1: with a custom `OpenAI`, `--model openai/m1 --api-key K` attaches K to
`OpenAI`, `test_api_key_for_a_capitalised_custom_provider_never_goes_to_openrouter`). A
launch switched onto a `session_start` provider (§2.3) moves the key from `openrouter` to
that provider (`test_api_key_follows_the_late_switch`). The key is still not forwarded to delegated
children (unchanged).

### 2.6 Delegated children get the route, not the string (M3)

> **Amended 2026-10-02 by [ADR-0250](0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md) §2.9.** Still split: user-defined routes.
> Not split: a route only the parent's `--api-key` decided (the child would authenticate it
> with a `.env` key or nothing), anything a credential decided — the child inherits the same
> keys and the `.env` record and decides it the same way — and guard 2, which a profile's own `extensions:` may answer differently in the
> child. The last paragraph's gateway row (`openai/gpt-4o`, no OpenRouter key) is the
> gateway's under pi's swap now, so that child gets `--provider gw`.

A child is a fresh process that inherits `OPENROUTER_API_KEY` and loads no extensions by
default. `agents.resolver.child_model_flags` now takes the parent's live registry and
resolves the profile's `model:` — or the parent's own model, forwarded when the profile
names none — with `resolve_model`, exactly as the parent's own `--model` would be; when
that lands on a user-defined provider, the child is launched with `--model <id>
--provider <provider>`. The child then reaches that provider or, for an extension it did
not load, refuses it by name; it never re-derives the route through OpenRouter. An
explicit `provider:` keeps its meaning, spelled as the parent matches it (§2.1, F1): with
a `models.json` `OpenAI`, `provider: openai` reaches the child as `--provider OpenAI`, and
an extension's `Groq` picked for `provider: groq` as `--provider Groq` — a child that
loads no extensions would otherwise take `groq` for the catalogue's vendor host. A
spelling two of the user's providers share up to case is passed as typed: the parent's
own resolve holds it, but a child without the extensions that registered those two sees
no clash (§6). `/agents show`, the consent dialog's model row and the
delegation row's model id render with the same registry.

Asking `resolve_model` (not the rung-0 helper) matters without an OpenRouter key: rung 0
does not run there, so a gateway listing `openai/gpt-4o` verbatim does not capture that
string — the parent resolves it to `openai` by the slash shorthand — and the child must
not be sent to the gateway either (the first cut did; `test_child_flags_follow_the_route_the_parent_resolves`).
One case cannot be made to agree: a catalogued name an extension took over (§2.1). The
child, without the extension, still knows `openai` from the catalog and reaches the
vendor's host with its own credentials; `inherit_extensions: true` is the cure.

### 2.7 `/model`'s UNDECIDED fallback keeps its registry (S5)

`resolve_model_argument` answers UNDECIDED on introspection failure too, not only without a
registry; the fallback passed `None` and so could not see a `models.json` provider. It now
passes `ctx.model_registry`.

## 3. The same rows, after

Same drivers, this branch:

| row | before | after |
| --- | --- | --- |
| C1 | `openrouter.ai:443` | `RP:/v1/chat/completions model=held-model` |
| C4 | `openrouter.ai:443` | `EXT:/v1/chat/completions model=m1` |
| C5 / C6 | `api='unknown'` refusal | `EXT … model=m1` (both) |
| C13 | `openrouter.ai:443` | `RP … model=held-model` |
| C15 | `openrouter.ai:443` | `RP … model=held-model` |
| C15-child (models.json / extension) | `openrouter.ai:443` / `openrouter.ai:443` | argv `--model held-model --provider retryprobe` → `RP` / argv `--model m1 --provider extprov` → no request, the child refuses `extprov` |
| C17 | `openrouter.ai:443` | `RP … model=held-model` |
| C19 `OPENROUTER_DEFAULT_MODEL=retryprobe/held-model` | `openrouter.ai:443` | `openrouter.ai:443` (unchanged, by design) |
| C20 | refused | `RP … model=held-model` |
| C21 / C21x | OpenRouter listener, bearer = probe | `RP` / `EXT`, bearer = probe |
| C7 / C8 (openai `baseUrl` override) | `api.openai.com:443` / `openrouter.ai:443` | the proxy listener `/v1/responses model=gpt-4o-mini` (both) |
| K1 `openai/gpt-4o-mini`, OR + cwd `.env` `OPENAI_API_KEY` | `openrouter.ai:443` | `openrouter.ai:443` (unchanged: no credential consulted) |
| C10 `anthropic/claude-haiku-4.5`, OR + `ANTHROPIC_API_KEY` | `openrouter.ai:443` | `openrouter.ai:443` (unchanged) |
| R4 `openai-codex/gpt-5.1`, OR, no Codex login | `openrouter.ai:443` | Codex: `No API key found for OpenAI Codex` before any request |
| `xai/grok-4.3`, OR + `XAI_API_KEY` | `openrouter.ai:443` | `api.x.ai:443` |
| R14 `--provider openrouter --model retryprobe/held-model` | `openrouter.ai:443` | `openrouter.ai:443` (unchanged) |
| owner `ollama/qwen3.6:35b-a3b`, fake OR | 12 × `openrouter.ai:443` | `provider=ollama`, the local model answered `pong`, 0 CONNECTs |

Real vendors, keys from the repo `.env` (CLAUDE.md 10): `--model
anthropic/claude-haiku-4.5` → `provider=openrouter`, `pong` (the OpenRouter route still
works); with the owner's agent dir, `--model ollama/qwen3.6:35b-a3b` with the real
OpenRouter key exported → `provider=ollama`, `pong`; `--model openai-codex/gpt-5.5` with
the OpenRouter key exported → `provider=openai-codex`, `pong`. Those two runs, and the
fake-key `owner` row, used the owner's **real** `~/.aelix/agent`, not an isolated one, and
were not read-only: the product's OAuth refresh rewrote `auth.json` there (the
`openai-codex` record, 2026-09-30 23:42:37), and the base run of the `owner` row appended
to `stats-history.jsonl`. Every later run used an isolated `AELIX_CODING_AGENT_DIR`. The TUI (CLAUDE.md 9, 120x40
over a pty): the banner's `model:`/`baseurl:` read `retryprobe/held-model` /
`https://openrouter.ai/api/v1` before and `held-model` / the local endpoint after, the
footer `✱ retryprobe/held-model` → `✱ held-model`; for `-e extprov.py --model extprov/m1`,
`extprov/m1` / OpenRouter → `m1` / the extension's endpoint.

`scripts/diag_retry_abort_live.py` (#147's rig, which the #334 lane found building no
precondition) now launches its child with `--provider retryprobe --model held-model`, so
its premise holds whatever routing policy is in force; with a fake `OPENROUTER_API_KEY`
exported, base `provider requests served : 0` → this branch `2`, `-> PASS`.

## 4. Tests

135 tests across the four files, measured on the final commit
(`pytest tests/cli/test_provider_prefix_rung.py tests/cli/test_launch_route_344.py
tests/agents_ext/test_child_route_344.py tests/tui/test_model_undecided_registry_344.py`
→ `135 passed`; round 4's 20 are at the end of this section). Round 3 added 52: the case collision (§2.1; 8 resolve rows, 2 launch
rows), a walk over **every** catalogued provider (§2.1 — 35 rows, each resolving
`<provider>/<an id from its own catalogue>` with the OpenRouter key set: the 26 that are
not an OpenRouter namespace to themselves, the 9 that are to OpenRouter, the partition
derived in the test from the catalogue rather than from `openrouter_namespaces`, plus one
row pinning the sizes 35/9/26), print/json switching and refusing (4 rows, replacing the
2 that pinned the refusal), the caution (1) and the rebuilds (1). On `62ec77d2` (the
branch before round 3): `15 failed, 100 passed` — the 100 include the 36 catalogue rows
and the rebuild row, guards whose red is shown by sabotage below (Codex's own sabotage,
`groq/` dropped from rung 0c, turns exactly `…[groq]` red; before round 3 it passed every
test). The earlier 63, copied onto `fbead6e0`: `47 failed, 16 passed`. The
16 are guards green on both — the rows that must stay on OpenRouter (the three vendor-key
states, a stale stored credential, an unknown id under an overlapping namespace,
`OPENROUTER_DEFAULT_MODEL`, `--provider openrouter`, the `--api-key` rows for the
OpenRouter rung and an explicit provider), the three "does not make it user-defined"
rows, the no-registry case, and the three explicit-provider rows. Two of those three
(`test_explicit_provider_is_never_overridden_by_rung_0`) are strings a rung 0 that ran
under `--provider` would move, and they are red under exactly that sabotage (below). On `8c9d6397` (the branch before fix round 2): `6 failed, 57
passed` — the six late-route rows of §2.3 (three of them fail there by the missing
`cli/model_switch` import, not by a route; the refused-switch sabotage below shows those
three red on the route).

Sabotage — each a one-line break of one mechanism on a copy of the final tree, the four
files plus `test_register_provider_bootstrap.py` and `test_runtime_bootstrap.py`, every
one red: dropping 0a → 28; 0b → 5; 0c → 4 (Codex, xai, the case row, the `0c` recipient);
no case fold → 4; no base-URL adoption → 6; `child_model_flags` ignoring the registry →
5; no early bind → 7; the bind on the first build only → the `/reload` test; no
late-route check → 8; `--api-key` attached at the pre-extension resolve → the
`0a-extension` recipient row; `/model`'s UNDECIDED fallback without the registry → 1; no
registration scope → 2; `overrides` counting without a `baseUrl` → 2; `openrouter`
counted as user-defined → 1; a catalogued registration counted without `models` → 1; the
child pinned by the rung-0 helper instead of `resolve_model` → 1; rung 0 reading
`OPENROUTER_DEFAULT_MODEL` → 1; rung 0 running under any `--provider` but `openrouter` →
the two explicit-provider rows; the late route ignored in interactive/RPC → 6; the
refused switch not held on the placeholder → 3. Round 3 re-ran those 20 on the rebased
tree, every one red (counts that moved with the new rows: 0a → 37, 0b → 6, 0c → 30, no
case fold → 2 — the user-defined fold is its own mechanism now, below — no late-route
check → 12, the late route ignored → 12; the rest as above), and added 11,
every one red: the pre-fix combined case match → 5; the same in the no-key slash split →
3; no case fold among user-defined names → 10; an ambiguous prefix guessed → 3; an
ambiguous prefix resolved in the same-spelled catalogue provider → 1; `groq/` dropped from
0c → 1; the launch switch persisting again → 4; print/json left out of the switch → 4; a
held print/json run not refused → 2; the caution dropped → 1; a registry that forgets
extension registrations between builds → 1. The real-child test spawns `python -m
aelix_coding_agent` against two local listeners and asserts on the wire, not the argv.
Under Python 3.11 (3.11.15) the four files, `test_register_provider_bootstrap.py` and
`tests/tui/test_commands.py`: `242 passed` (round 2: `190 passed`).

Round 4 (§7) added 20. On `ebfe411a`: `15
failed, 5 passed`; the 5 are guards green on both (the exact-spelling rows with and without
the OpenRouter key, the lower-case re-pointed row, the late case clash in print — already
refused there — and F2's unregistered provider). Sabotage on a copy of the final tree,
every one red: the named provider not matched → 12; the catalogue matched before the
user's providers → 7; a named case clash guessed → 4; `defaultProvider` not matched → 3;
the child's `--provider` passed as typed → 1; the late check ignoring a case clash → 2;
the late switch asking `/model` instead of holding → 3.

## 5. The divergence from pi, stated

> **Superseded 2026-10-02 by [ADR-0250](0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md) §2.4**: aelix now follows pi's order;
> the dual-key `openai/gpt-4o-mini` goes to OpenAI, as in pi.

pi has no OpenRouter-from-env rung at all; its `resolveCliModel` infers a known provider
from the prefix and, when that match is unauthenticated, prefers a single authenticated
exact raw-id match (`model-resolver.ts:520-541` @ 1ff5b6fdd). aelix keeps its
OpenRouter-from-env convenience and decides rung 0 without reading credentials, so for a
user holding both an OpenRouter key and, say, an OpenAI key, `openai/gpt-4o-mini` goes to
OpenRouter here and to OpenAI in pi. That is the owner's choice (a repo `.env` must not be
able to pick the vendor, §2.1). Also not adopted: fuzzy matching, the `:thinking` suffix,
and the bare-id "ambiguous across providers" error. ADR-0235: a known divergence, recorded
here, not a parity gap. The `core/model_resolver.py:resolve_cli_model` port (ADR-0067)
still has no production caller.

## 6. What this does not close

- **#362** — a cwd `.env` `OPENROUTER_API_KEY` is still a routing switch for the 9
  overlapping prefixes, bare ids no user-defined provider serves, and
  `OPENROUTER_DEFAULT_MODEL` (the design's D1). Rung 0 narrows what it can capture; it
  does not remove the rung. **Closed 2026-10-02 by [ADR-0250](0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md).**
- **Launch and in-session `/model` still disagree for dual-key users on the overlapping
  prefixes.** **Closed 2026-10-02 by ADR-0250** (both go to the vendor, as pi does). `/model openai/gpt-4o-mini` resolves over the auth-filtered pool (#134/#136)
  and picks OpenAI when an OpenAI key is configured; `--model openai/gpt-4o-mini` goes to
  OpenRouter. Aligning them either way is a policy change for `/model`, not part of this.
- **A re-pointed built-in takes its whole prefix.** With `providers.openai.baseUrl` set,
  an OpenRouter-only spelling such as `openai/gpt-4o:extended` goes to the gateway, which
  will reject it; `--provider openrouter` reaches OpenRouter. This is 0a's "never fall
  through", applied as the owner decided.
- **A `baseUrl` on a single model definition under a built-in provider does not make the
  provider user-defined** (decision 3 names the provider-level `baseUrl`). With an
  OpenRouter key, `openai/<that custom id>` still goes to OpenRouter.
- **A profile `provider:` two extension providers share up to case** is held in the
  parent (§2.1) but passed to a `--no-extensions` child as typed, where it names the
  catalogue's provider of that spelling if there is one (§2.6). Two extensions whose
  provider names differ only in case, and a profile spelling neither, is the whole
  precondition.
- **A provider registered in `session_start`** is in the registry but not seen by the
  launch resolve (§2.3): every mode switches onto it afterwards, without persisting it.
  **A case clash between a provider registered in `setup()` and one registered in
  `session_start`** (`SessExt` in `setup()`, `SESSEXT` in `session_start`, `--model
  sessext/m1`) is not held the way F3's all-late clash is: the launch resolve sees only
  `SessExt` and goes onto it, so the late check never fires; after `session_start`,
  `-p` and `--mode json` refuse naming both, while interactive and RPC keep sending to
  `SessExt`'s host with that extension's key (round-4 verification, measured; the same
  on `ebfe411a`). Both providers are the user's own and nothing reaches OpenRouter or a
  vendor, so it is recorded here rather than fixed. **Naming a user-defined provider
  `OpenRouter`** (any case) makes `--provider openrouter` select it (§2.1's case rule),
  so the guide's "force OpenRouter" example no longer reaches OpenRouter for that user;
  rename the provider to keep it. **A failed `/reload` keeps the earlier registration**
  (§2.3, pi's behaviour). **An
  OpenRouter key stored only in `auth.json`** does not turn on the OpenRouter rung
  (unchanged; the design's R25).
- **RPC's `prompt` does not preflight runnability.** A session held on the placeholder
  answers a `prompt` with the kernel's `No provider registered for api='unknown'` rather
  than a `set_model` hint. A preflight in the RPC handler would also refuse a prompt an
  extension's `input` hook answers without any model (`harness.prompt` dispatches `input`
  first), so it was not added here.
- **#363 — a re-pointed built-in.** Its gateway receives the cascade's key (§2.4): a
  vendor env var, a `/login` credential or `--api-key` outranks the `models.json`
  `apiKey` — option (b), decided; the alternative (a), the `apiKey` winning, is #363. And
  the launch adopts only the host, not the provider-level `compat` / `modelOverrides`
  that `/model` applies (§2.4).
- **#365 — rung 0 is the only place that scopes an extension's take-over of a built-in
  name** (§2.2). An extension registering `openai` with models: without an OpenRouter
  key, or with `--provider openai`, a catalog id it did not register
  (`openai/gpt-4o-mini`) still resolves from the catalog to `api.openai.com` with the
  extension's provider-wide key (when no vendor key comes first in the cascade), as on
  `fbead6e0`.
- **#364 — citations that already pointed at the wrong text on `fbead6e0`**, which this
  branch's `--fix` relocated faithfully (the same wrong text, new lines):
  `agent_context.py`'s `no_project_local` / `no_discovery` lines, `profile.py`'s #98-gate
  range, `shell.py`'s `run_tui` caller, `agents/profile.py`'s `--no-extensions` claim and
  `aelix_agents/batch.py`'s Guardrail-first range.
- **`--api-key` with no model at all** (closed 2026-10-02 by ADR-0250 §2.7: the
  early check refuses only when there is no model string) is still judged before extensions load, so a bare id
  served only by an extension provider, with no OpenRouter key, is refused as "requires a
  model" although the launch would resolve it (measured: `-e extprov.py --model m1
  --api-key K` → `Error: --api-key requires a model …`; the same without `--api-key`
  resolves `m1` inside `extprov`).

## 7. Cross-review, second pass (Codex, `ebfe411a`)

Codex (effort high) re-read the branch after round 3 with probes of its own
(`/tmp/344-work/codex2`, mock transports, fake keys). Clean: the `/model` extraction —
the pre-branch handler and `switch_model_argument` compared on a normal switch, the #136
caution, an allow-list exclusion and a rejecting `model_select`, `equal True` for
messages, events, persistence and the footer — a `session_start` handler that raises after
registering, two registrations under one exact name, an excluded allow-list and a
rejecting `model_select` (no request). Found, and what round 4 did:

- **F1 — an explicit `--provider` did not get the case rule** (a request to
  `api.openai.com` with the user's `--api-key`). Fixed: §2.1's last paragraph, §2.6.
  Codex's probe after: `--provider openai`, `OPENAI` and `OpenAI` →
  `('http://127.0.0.1:18082/custom/v1/chat/completions', 'Bearer fake-custom-cli', True)`;
  the delegated flags `['--model', 'm1', '--provider', 'OpenAI']`; the re-pointed `openai`
  at its override host for all three spellings.
- **F2 — a provider unregistered before `session_start` returns goes to OpenRouter.**
  Documented as the rule (§2.3), pinned by a guard.
- **F3 — a case-ambiguous late registration reached OpenRouter in RPC.** Fixed: §2.3.
  Codex's probe after: `ambiguous_rpc_start_route sessext m1`,
  `ambiguous_rpc_mock_requests []`; print unchanged (refused, no request).
- **F4 — the tests did not cover these.** 20 rows added (§4), red on `ebfe411a` where they
  pin a fix.
