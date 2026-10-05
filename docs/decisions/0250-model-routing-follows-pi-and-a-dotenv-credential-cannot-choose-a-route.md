# 0250. Model routing follows pi's `resolveCliModel`, and a `.env` credential cannot choose a route

Status: Accepted (2026-10-02) — §2.11 amended 2026-10-03 (#367): a `session_start` provider is refused as a launch model no registered provider claimed, not switched to, and nothing is sent while that launch's `session_start` runs (round 3; since round 5 a turn gate: no turn of any kind, whatever model a handler sets, there or in a held rebuild's `session_start` — since round 6 a handler's `trigger_turn` there ends as a refused turn, and every hold is checked after it is applied; since round 7 every hold is applied under the turn gate, on every path, and since round 8 a turn refused while a hold is applied says so, on every path); a provider is late wherever it is registered while a session is starting — from the end of the build through its `session_start` and the turns its handlers triggered, which aelix waits out, up to aelix's check after `session_start` (round 8: also in a handler of a turn one triggers; round 9: whatever registered it, also after the handler returned, and the texts say "while a session was starting"); a launch that resolved to a registered provider stays on it, as in pi (round 4); every later implicit re-resolution is held where it lands on a late provider, judged after every `session_start`; §2.1, §2.3, §2.4, §2.7 and §2.12 amended 2026-10-06 (#370): with `--api-key`, a string no provider places is pi's not-found and the key is attached to nothing — guard 2 never carries a typed key; the not-found text names `--model openrouter/<s>` and `--provider openrouter --model <s>`; `/agents use` re-resolves the launch inputs with the key in view, as the launch and every rebuild do, and a profile's own `model:` as before (round 2)
Date: 2026-10-02
Supersedes: **ADR-0249 §2.1** (rung 0 and the OpenRouter-from-env rung), **§2.6**'s split
rule and **§5** (the stated divergence from pi). ADR-0249's X1 (§2.3), S (§2.4) and the
`/model` UNDECIDED registry (§2.7) stand.
Amends: **ADR-0195** Decision 2 (a bare id several providers serve is decided by the one
the user holds a credential for, else pi's error) and Decision 4 (the ladder is pi's
order); **ADR-0203** residual risk 1 (a `.env` credential can no longer choose a route)
and its provider-configuration arm (`OPENROUTER_DEFAULT_MODEL` is shell-only). Dated
notes in each.
Relates: ADR-0067 (the `core/model_resolver.py` port of pi's resolver stays unwired),
ADR-0235 (the divergences in §2.4 are stated, not parity gaps), #363 (the launch
composition and a re-pointed built-in's key order), #365, #367 (the late-registered path
refuses, §2.11), #368.
Issue: #362 (P1, security). Owner decision: 2026-10-02 (issue comment).
Tests: `tests/cli/test_route_follows_pi_362.py`, `tests/cli/test_dotenv_provenance_362.py`,
`tests/cli/test_launch_route_362.py`, `tests/cli/test_route_refusals_in_session_362.py`,
`tests/model_registry/test_route_auth_362.py`, `tests/agents_ext/test_child_provenance_362.py`,
`tests/core/test_model_argument_guard_362.py`, `tests/tui/test_post_login_pick_362.py`,
`tests/rpc/test_rpc_route_guard_362.py`, `tests/providers/test_base_url.py` (§2.2's
placeholder rule), `tests/cli/test_late_provider_refused_367.py`,
`tests/cli/test_late_provider_landing_367.py`,
`tests/cli/test_late_route_hold_apply_367.py`,
`tests/tui/test_post_login_late_hold_367.py` and
`tests/harness/test_harness_turn_gate_367.py` (§2.11, #367);
`tests/cli/test_api_key_unknown_prefix_370.py` (§2.1 step 2, §2.7, #370).
Cross-review: Codex on `854bf319` (§5) — `/model` reworked (§2.8), guard 2's scope and the
hatch stated (§2.3, §2.6); Codex again on `a0edf615` (§5) — the post-`/login` pick's
saved default, RPC `cycle_model` and a held credential with no model rows (§2.8, §2.10).
The fourth verification (of `001ef77d`, §4): settings `defaultProvider` in step 2 counts
only for the user's own credential or endpoint (§2.1). Codex's third pass (of `5e983992`,
§5): a fallback resolver's answer and an installed resolver (§2.2, §2.8), a `.env` value
in a base-URL placeholder (§2.2), a headers-only built-in (§4). Codex on `dcc78170` (#367):
a `session_start` hook's `trigger_turn` and `set_model`, and the late check's tie-break
(§2.11, round 3).

A cloned repo's `.env` was a route switch. With `ANTHROPIC_API_KEY` exported and the
repo's `.env` carrying `OPENROUTER_API_KEY`, `aelix --model anthropic/claude-haiku-4-5`
sent the prompt to `openrouter.ai` — on the file owner's account — because ADR-0249's
OpenRouter-from-env rung fired on the variable's presence, whatever had set it. ADR-0203
had promised that a key the user exported wins over the file; routing around the
provider it protects bypassed that.

## 1. What was measured

The real CLI, fake keys only, an isolated `AELIX_CODING_AGENT_DIR` and a scratch cwd per
row (holding the row's `.env`), HTTPS through a local recording CONNECT proxy that
answers 403, models.json and extension endpoints at local recording listeners. "sh" =
exported, ".env" = admitted from the row's cwd `.env`. Base `aa026d08` (code `9ca53a4f`);
the drivers are `/tmp/362-work/impl/live/matrix.py` and `listeners.sh`, the outputs
`base.out` / `after.out` next to them; the review round re-ran all 46 rows
(`/tmp/362-work/fix/live46/after2.out`), where only A40 moved.

| row | input (sh = exported, .env = from the cwd `.env`) | `aa026d08` | this change |
| --- | --- | --- | --- |
| A01 | models.json prefix · OR sh | RP `/rp/v1/chat/completions` | RP `/rp/v1/chat/completions` |
| A02 | bare models.json id · OR sh | RP `/rp/v1/chat/completions` | RP `/rp/v1/chat/completions` |
| A03 | openai-codex/gpt-5.5 + codex OAuth · OR sh | `chatgpt.com` | `chatgpt.com` |
| A04 | openai/gpt-4o-mini · OR sh | `openrouter.ai` | `openrouter.ai` |
| A05 | openai/gpt-4o-mini · OR sh + OPENAI sh | `openrouter.ai` | `api.openai.com` **(changed)** |
| A06 | M1 openai/gpt-4o-mini · OR sh + OPENAI .env | `openrouter.ai` | `openrouter.ai` |
| A07 | openai/gpt-4o-mini · OPENAI sh + OR .env | `openrouter.ai` | `api.openai.com` **(changed)** |
| A08 | anthropic/claude-new-9 · OR sh | `openrouter.ai` | `openrouter.ai` |
| A09 | anthropic/claude-new-9 · OR sh + ANT sh | `openrouter.ai` | `api.anthropic.com` **(changed)** |
| A10 | planted anthropic/claude-haiku-4.5 · ANT sh + OR .env | `openrouter.ai` | `api.anthropic.com` **(changed)** |
| A11 | #362 anthropic/claude-haiku-4-5 · ANT sh + OR .env | `openrouter.ai` | `api.anthropic.com` **(changed)** |
| A12 | xai/grok-4 · OR sh | refused: No API key found for Xai. | refused: No API key found for Xai., after a custom-id Warning **(changed)** |
| A13 | xai/grok-4 · OR sh + XAI sh | `api.x.ai` | `api.x.ai` |
| A14 | x-ai/grok-4.3 · OR sh | `openrouter.ai` | `openrouter.ai` |
| A15 | bare gpt-4o-mini · OR sh | `openrouter.ai` | refused: Error: Model "gpt-4o-mini" is ambiguous across providers: azure-openai… **(changed)** |
| A16 | bare claude-haiku-4-5 · ANT sh | refused: No model selected. | `api.anthropic.com` **(changed)** |
| A17 | bare claude-haiku-4-5 · ANT sh + OR sh | `openrouter.ai` | `api.anthropic.com` **(changed)** |
| A18 | newlab/model-x · OR sh | `openrouter.ai` | `openrouter.ai` |
| A19 | newlab/model-x · OR .env | `openrouter.ai` | refused: Error: Model "newlab/model-x" not found. Use --list-models to see avai… **(changed)** |
| A20 | openrouter/newlab/model-x · OR sh | `openrouter.ai` | `openrouter.ai` |
| A21 | openrouter/auto · OR sh | `openrouter.ai` | `openrouter.ai` |
| A22 | --provider openrouter newlab/model-x · OR sh | `openrouter.ai` | `openrouter.ai` |
| A23 | OPENROUTER_DEFAULT_MODEL sh, no --model · OR sh | `openrouter.ai` | `openrouter.ai` |
| A24 | OPENROUTER_DEFAULT_MODEL .env, no --model · ANT sh + OR .env | `openrouter.ai` | refused: No model selected. **(changed)** |
| A25 | extension extprov/m1 · OR sh | EXT `/ext/v1/chat/completions` | EXT `/ext/v1/chat/completions` |
| A26 | session_start sessext/m1 · OR sh | EXT `/sess/v1/chat/completions` | EXT `/sess/v1/chat/completions` |
| A27 | session_start sessext/m1 · OR .env | EXT `/sess/v1/chat/completions` | EXT `/sess/v1/chat/completions` |
| A28 | session_start sessext/m1 · no OR | refused: No provider registered for api='unknown' — this build has no adapter f… | EXT `/sess/v1/chat/completions` **(changed)** |
| A29 | models.json 'OpenAI' openai/m1 · OR sh | GW `/gw/v1/chat/completions` | GW `/gw/v1/chat/completions` |
| A30 | re-pointed openai openai/gpt-4o-mini · OR sh | GW `/gw/v1/responses` | GW `/gw/v1/responses` |
| A31 | case RetryProbe/held-model · OR sh | RP `/rp/v1/chat/completions` | RP `/rp/v1/chat/completions` |
| A32 | case --provider OPENROUTER x-ai/grok-4.3 · OR sh | `openrouter.ai` | `openrouter.ai` |
| A33 | bare gpt-4o-mini · OPENAI .env only | refused: No model selected. | refused: Error: Model "gpt-4o-mini" is ambiguous across providers: azure-openai… **(changed)** |
| A34 | openai/gpt-4o-mini · OR .env only | `openrouter.ai` | refused: No API key found for OpenAI. **(changed)** |
| A35 | meta-llama/llama-3.3-70b-instruct · OR sh + HF sh | `openrouter.ai` | `openrouter.ai` |
| A36 | anthropic/claude-haiku-4.5 · OR .env only | `openrouter.ai` | `openrouter.ai` |
| A37 | moonshotai/kimi-k2.6 · OR sh | `openrouter.ai` | `openrouter.ai` |
| A38 | anthropic/claude-haiku-4.5 · ANT sh + OR sh | `openrouter.ai` | `openrouter.ai` |
| A39 | #362 anthropic/claude-haiku-4-5 · ANT sh, no .env | `api.anthropic.com` | `api.anthropic.com` |
| A40 | openai/gpt-4o-mini --api-key K · OR sh | `openrouter.ai` | `api.openai.com` **(changed)** (§2.1 step 3b) |
| C-L1 | anthropic/claude-haiku-4-5 · OR sh + ANT .env | `openrouter.ai` | `openrouter.ai` |
| C-L2 | openai/o1-pro · OR sh + OPENAI .env | `openrouter.ai` | `openrouter.ai` |
| C-L3 | OpenAI/gpt-4o-mini · OR sh + OPENAI .env | `openrouter.ai` | `openrouter.ai` |
| C-MISS | anthropic/claude-new-9 · OR sh + ANT .env | `openrouter.ai` | `openrouter.ai` |
| C-P1 | planted + X,ANTHROPIC_API_KEY · ANT sh + OR .env | `openrouter.ai` | `api.anthropic.com` **(changed)** |
| K1 | -e extprov --model m1 --api-key K · none | refused: Error: --api-key requires a model to be specified via --model, --provi… | EXT `/ext/v1/chat/completions` **(changed)** |

17 of 46 rows changed (`compare.py base.jsonl after2.jsonl`; A40 since the review round). Every request in the table was answered by a local recorder; nothing reached a vendor. The A28 row now switches to the `session_start` provider and reaches it (critique S5); K1 is ADR-0249 §6's last bullet. **#367 (2026-10-03)**: A26-A28 are refused before any request (§2.11).

## 2. Decision

### 2.1 pi's order, exact ids only

`cli/runtime_bootstrap.resolve_route` transcribes pi's `resolveCliModel`
(`packages/coding-agent/src/core/model-resolver.ts:406-606` @ 88ff80b98); `resolve_model`
returns its model, so every caller keeps its signature.

- **E. An explicit provider** (`--provider`, a profile's `provider:`, the seeded settings
  pair) is the route, named with aelix's case rule (ADR-0249 §2.1, kept by the owner: a
  user-defined provider wins a case-only clash, two that differ only in case are refused).
  The id exactly; else pi's `<provider>/` strip (`:506-512`) — exact first, so
  `--provider openrouter --model openrouter/auto` keeps OpenRouter's own id; else a custom
  id under it, built from the stripped id as pi builds it (`:570-597`), with pi's warning.
- **1. A prefix** before the first slash that names a known provider — every provider of
  every model: catalogue, `models.json`, extension registrations (`:429-463`). A
  user-defined provider is matched first and alone (ADR-0249 §2.1).
- **2. No inferred provider**: the whole string as an id or `provider/id` across every
  model (`:465-504`). One hit wins. Several: settings `defaultProvider` when it is one of
  them **and counts** (below; aelix's, ADR-0195 — pi has no such tie-break), else the
  **sole route-authenticated** one (pi), else the sole user-defined one among the
  route-authenticated (aelix, "rule U": keeps a gateway that lists `openai/gpt-4o`
  verbatim), else pi's error. None: a bare id is homed under settings `defaultProvider`
  when it counts (ADR-0195), else guard 2 — never under `--api-key` (#370, 2026-10-06) —
  else pi's not-found.

  **Settings `defaultProvider` is not "the persisted choice".** It is the MERGED value, and
  a project `.aelix/settings.json` sets it — over the user's global one, and in a directory
  that was never trusted. So while the user holds a route-authenticating credential of
  their own anywhere (`holds_route_auth`, §2.2), it counts in both arms only when it names
  a provider that credential authenticates (`route_authenticated`) or the user's own
  endpoint — a models.json provider, an extension's, a re-pointed built-in, `openrouter`
  included (`_own_endpoint_providers`; a project file can name one but cannot define one).
  Otherwise both arms fall through as if it were unset: the tie-break to the sole
  route-authenticated hit, then rule U, then pi's ambiguity error; the home to guard 2
  (never for a bare id) and pi's not-found. The refusal then says why: `Settings
  defaultProvider "<p>" was not used: no credential of your own authenticates it, and a
  project .aelix/settings.json can set it (<NAMES> came from a project .env).` A session
  holding no credential of its own keeps both arms (§6). Found by the fourth verification
  (of `001ef77d`, verify4's sweep, real CLI, fake keys): with the user's own
  `OPENROUTER_API_KEY` exported, a project `{"defaultProvider": "anthropic"}` plus a `.env`
  `ANTHROPIC_API_KEY` sent `--model claude-haiku-4-5` to `api.anthropic.com` on the repo's
  key (S13; without the settings it is pi's ambiguity error, S14), `{"defaultProvider":
  "openai"}` sent `gpt-4o-mini` and the bare `newmodel-x` to `api.openai.com` (S4, S12),
  and it did so over the user's GLOBAL `defaultProvider "openrouter"` (S15). Now every one
  of those is refused with and without the `.env` (§4, round 5). The rule reaches every
  path that resolves a model string: the launch (`--model`, an `--agent` profile's model
  with no provider), each rebuild's harness factory (so `/agents use`'s switch, whose own
  check passes no default and refuses an ambiguous id outright), and a delegated child,
  which reads the same merged settings in the same project (a real-spawn row).
- **3. Inside the inferred provider, the id exactly** (pi `:514-517` without fuzzy matching
  or a `:thinking` suffix — ADR-0249 §5 declined both, and pi's fuzzy match turned
  `xai/grok-4` into `grok-4.6`). A user-defined provider never lets the string leave it:
  a custom id there, or a refusal (the #344 principle; pi would swap).
- **3b. `--api-key`, and the prefix names a catalogued provider** (not user-defined; that is
  step 3): the id it lists, else a custom id there — no swap, no raw match elsewhere, no
  guard 2. The key is attached after resolution to the route's provider (pi `main.ts:476-484`,
  `:827-834`), so nothing at resolve time counted it: with `OPENROUTER_API_KEY` exported,
  `--model anthropic/claude-haiku-4-5 --api-key K` went to OpenRouter by guard 2's widened arm
  with K — typed for Anthropic — as the bearer, over the user's own OpenRouter key, under a
  Note claiming they held no Anthropic credential (the review of `d58cbb3e`, R03); the same
  for guard 2 as decided (`anthropic/claude-new-9`, R02), for pi's swap (`openai/gpt-4o-mini`,
  A40 — pi does this too) and for (5)'s raw match (`anthropic/claude-haiku-4.5`). A prefix no
  provider has (`newlab/model-x`) reached guard 2 until #370 (2026-10-06): with an
  OpenRouter key of the user's own exported, `--model newlab/model-x --api-key K` went to
  `openrouter.ai` with K as the bearer, in print, json, RPC and the TUI (measured on
  `62e2238b`). Under `--api-key` such a string is now pi's not-found (step 2;
  `resolveCliModel` `:599-605` @ b223082bb, where `main.ts:828-837` attaches the key only to
  a model that resolved) and the key is attached to nothing; `openrouter/<id>` and
  `--provider openrouter` name OpenRouter. An id OpenRouter's catalogue lists under a
  prefix that is not a provider (`x-ai/grok-4.3`) is step 2's exact hit, as in pi
  (`:465-504`), and takes the key to OpenRouter (§2.7).
- **4. Found, and the inferred provider is not route-authenticated**: the sole
  route-authenticated model whose raw id is the string (pi's swap, `:519-541`), else the
  sole user-defined one among them, else guard 2 (§2.3), else the hit.
- **5. Not found**: a route-authenticated raw match (`:543-556`, restricted); else, when
  the inferred provider IS route-authenticated, the custom id there; else guard 2; else
  pi's first raw match; else the custom id.

In (4) and (5) the string is also tried with the prefix in its canonical spelling: pi
lower-cases both sides there (`:526-527`); aelix keeps ids case-sensitive, and without the
canonical variant `OpenAI/gpt-4o-mini` lost the swap and stayed on a planted
`OPENAI_API_KEY` (the design critique's M4).

### 2.2 Guard 1 — a `.env` credential authenticates a route, never chooses one

npm pi reads no `.env` (pi #9197, `--env-file`, closed NOT_PLANNED); aelix does
(ADR-0203). In pi's order a credential decides the tie-break, the swap and guard 2, so a
planted key would choose routes. Every such judgement asks
`ModelRegistry.has_route_auth(provider)` instead of `has_configured_auth`: the same
layers minus any credential a cwd `.env` supplied —

1. runtime override (`--api-key`) → yes;
2. `auth.json` (`/login`; an expired OAuth too, as pi's `hasConfiguredAuth` counts it) →
   yes, unless an api-key entry names an environment variable a `.env` supplied;
3. environment → a name of `ENV_API_KEYS[provider]` that is set and not in the record;
4. a `models.json` / registration `apiKey` → a literal or `!command` yes; the name of a set
   variable unless a `.env` supplied it;
5. a registration's `oauth` → yes;
6. the fallback resolver (`AuthStorage.set_fallback_resolver`, an embedder's) → its answer,
   unless it equals the current value of a variable the record names. A resolver is
   opaque, so the answer is compared by value: Codex's third cross-review (C1) installed
   the shipped `get_env_api_key` as the resolver, and layer 3's excluded `.env`
   `OPENAI_API_KEY` came straight back through it — with `OPENROUTER_API_KEY` exported,
   `/model openai/gpt-4o-mini` went to `api.openai.com` on `Bearer repo-openai-fake` with
   the `.env` and to `openrouter.ai` on the user's key without it. Codex's fourth pass
   (F1) measured the same through resolvers that prefixed, suffixed or stripped that
   value or returned it as `bytes` or an `int`, so an answer counts only when it is a
   `str` that does not CARRY a recorded value (`model_registry._derived_from_dotenv`:
   equal after stripping whitespace and case-folding, or - both at least 8 characters -
   one containing the other), checked against every recorded value (F4: an any/all slip
   passed every test), and read through the BUILTIN `str.strip` (the fifth pass: a `str`
   subclass overriding `strip` hid an equal answer; `.lower()` of the value passed too).
   A recorded value under 8 characters is matched by equality only - containment would
   refuse an own vault key that happens to contain a short `.env` value such as `dev` -
   so a resolver that prefixes a short `.env` value, like one that ENCODES a `.env` value
   (a hash, base64), defeats the comparison; that is the embedder's to avoid - a resolver
   must not derive its answer from a variable `aelix_ai.dotenv_record.dotenv_supplied`
   names. Only this predicate applies the rule; `has_configured_auth` and the request's
   bearer are unchanged.

**The record.** `load_dotenv` writes the names it admitted (credentials, configuration
names, hatched names) to `AELIX_DOTENV_ADMITTED`, unioned with an inherited record and
never overwritten — a child started in the same cwd re-reads the `.env`, admits nothing
(the keys are inherited), and an overwrite would erase the mark. Names are recorded and
compared as `os.environ` stores them: on Windows CPython upper-cases every name, so a
line `OpenRouter_API_KEY=…` sets `OPENROUTER_API_KEY`, and that is what is recorded
(critique M2(b)). A `.env` key that is not a plain environment name
(`^[A-Za-z_][A-Za-z0-9_]*$`) is refused first, ahead of the hatch: the record is
comma-joined, and a key `X,ANTHROPIC_API_KEY` was otherwise admitted and read back as the
user's exported `ANTHROPIC_API_KEY` (critique M2(a)). The record has its own refusal
branch: `^AELIX_` alone did not hold — `AELIX_DOTENV_ALLOW=AELIX_DOTENV_ADMITTED` admitted
it on `9ca53a4f`. It reaches every child: `build_child_env` and the bash tool's
`get_shell_env` copy the environment, so a delegated agent and a nested `aelix` decide as
the parent does.

**Where it applies**: (2)'s tie-break and its settings `defaultProvider` (§2.1), (4)'s swap, (5)'s choice among raw matches and the
"inferred provider is the user's" rule, guard 2, `/model`'s pool (§2.8 — not for a prefix
naming a provider the user defined, which has already chosen) and the post-`/login` pick
(§2.10). **Where it does not**: authenticating the chosen route (the
request's key is the AuthStorage cascade, unchanged — a `.env` key included), and the
"can this route run" gates (print/json's #98 gate, `is_runnable`, first-run onboarding).

A duck-typed registry (an embedder's, a test double) without `has_route_auth` is asked
`has_configured_auth` — except when the provider's environment layer holds only `.env`
names, where it fails closed (critique S3). Without a registry only the environment
layer exists.

**A `.env` value never addresses a request** (Codex's third cross-review, C3). A credential
authenticates; it never chooses the host. `aelix_ai.providers._base_url.expand_base_url`
filled any `{NAME}` in a base URL from `os.environ` (pi expands only Cloudflare's two
tokens, `cloudflare-auth.ts` `resolveCloudflareBaseUrl`), so a user's models.json
`{"providers": {"openrouter": {"baseUrl": "https://{TENANT_KEY}.owner-gateway.invalid/v1"}}}`
plus a cloned repo's `.env` `TENANT_KEY=repo-chosen.invalid/v1#` and `OPENROUTER_API_KEY`
sent `/model openrouter/auto` — and `--model openrouter/auto`, and `--model mygw/m1` for
any user-defined provider with such a template — to host `repo-chosen.invalid` with the
repo's key: the `/` and `#` erase the fixed suffix. `TENANT_KEY` came in through the
credential-name rule (its `_KEY` suffix), which has no value-shape rule; ADR-0203 gave
value-shape rules to the configuration it admits for templates. Now a variable the record
names fills a base-URL placeholder only when ADR-0203 admits it as template configuration
with a value-shape rule — `aelix_ai.dotenv_record.DOTENV_TEMPLATE_NAMES`, today
`CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_GATEWAY_ID`, the catalogue's only templated tokens
(a test pins that every upper-case catalogue token is in the set and that each has a shape
rule). Any other `.env` name leaves its token unexpanded: the model is not runnable
(`runnable_models`' placeholder guard), so `/model` and the launch refuse before a
request, saying `TENANT_KEY came from a project .env, which may authenticate a request
but never address one; export it in your shell to use it.` A `.env`-supplied Cloudflare id
fills a placeholder only in the PATH — the span from the `/` that ends the authority (the
first of `/`, `?`, `#` after `scheme://`, when it is a `/`) to the first `?` or `#` after
it — never the scheme, user-info, host, port, query or fragment (`may_fill_placeholder(…,
in_path=…)`; a template that does not OPEN with `scheme://`, or has no path, takes no
`.env` value; Codex's
fourth pass, F3, had a `/` inside `https://owner.invalid?next=/{…}` read as the path), and
only when its VALUE has ADR-0203's plain-id shape, re-checked by the reader
(`dotenv_record.TEMPLATE_VALUE_SHAPE`, pinned equal to `_CF_ID`) rather than trusted from
the admission path (F2: on Windows a hatched `Cloudflare_Account_ID` missed the
case-sensitive configuration dispatch, the record spelled it `CLOUDFLARE_ACCOUNT_ID`, and
`acct?capture=repo` moved the catalogue path into a query): the shape
rule keeps the id to one plain label, which under the catalogue's templates lands below a
fixed host, but the verify round on round 6 measured a user's own
`https://{CLOUDFLARE_ACCOUNT_ID}.owner-gateway.invalid/v1` with a `.env`
`CLOUDFLARE_ACCOUNT_ID=repochosen` reaching `repochosen.owner-gateway.invalid:443`; now no
request, with the same "export it" refusal, while the catalogue's
`cloudflare-ai-gateway` from `.env` ids still reaches `gateway.ai.cloudflare.com:443` (main
loop, real CLI, CONNECT recorder). A name admitted through `AELIX_DOTENV_ALLOW` is in the
record and is not template configuration, so it fills no placeholder either: the hatch
admits a name, not a value that addresses a request (ADR-0203: "the hatch names a KEY; the
redirect lives in the VALUE") — export it to use it in a template. An exported variable is
not in the record and fills every placeholder, as before (the user chose it). The three GCP
names in the configuration arm have shape rules but are not template tokens (the Vertex
client builds its own host from them), so a user's template naming one is filled only
from the shell. The provider layer reads the record without importing
`aelix-coding-agent`: its name lives in `aelix_ai.dotenv_record` (the lowest package),
which `core.dotenv_provenance` re-exports — one spelling, the dependency direction kept,
and every process that inherits the record (a delegated child, a nested `aelix`) decides
the same way, which a predicate passed in by the CLI would not give an embedder or a
child. Measured through the real CLI (CONNECT recorder, fake keys): 5e983992 `--model
openrouter/auto` "CONNECT repo-chosen.invalid:443", now no request and the line above;
`TENANT_KEY=repo-chosen.invalid/v1#` exported: "CONNECT repo-chosen.invalid:443" on both;
Cloudflare ids from a `.env`: "CONNECT gateway.ai.cloudflare.com:443" on both. In the TUI
(a pty), `/model openrouter/auto` on 5e983992 printed "model → auto (openrouter)" and `hi`
went to `repo-chosen.invalid`; now it refuses and `hi` stays on `api.openai.com`.

### 2.3 Guard 2 — ids this build's catalogue cannot place

pi refreshes newer ids from pi.dev every 4 h
(`core/remote-catalog-provider.ts:15` @ 88ff80b98); aelix has only its snapshot, which
misses ~45% of OpenRouter's live ids (#136: 179 of 400).

**Guard 2's exact scope** — a refinement of the owner's wording, which was "an id in no
catalogue": an id **the OpenRouter snapshot does not list**, under an OpenRouter-namespace
prefix whose vendor is **not route-authenticated** (or under a prefix no provider has), goes
to OpenRouter on the user's own OpenRouter key. An id a vendor's catalogue lists can
therefore still go to OpenRouter: with only `OPENROUTER_API_KEY` exported, `openai/o1-pro`
(in OpenAI's catalogue, not in OpenRouter's snapshot) does — Codex's cross-review of
`854bf319` measured it (C4: `probe_guard2.py`, "catalog_match True route guard2
openrouter") and the main loop kept it: that is guard 2's purpose — pi reaches such ids
through pi.dev's remote catalogue, which aelix does not have — and the request goes only to
the provider whose key the user exported. The clauses, all of which must hold:

1. it is `<segment>/<segment>[/…]` with no empty segment (of this build's 356 OpenRouter
   ids, the only one without a slash is pi's `auto` alias) — a bare unknown id is pi's
   not-found;
2. its prefix is not a user-defined provider;
3. its prefix is unknown, or names a catalogued provider that is one of OpenRouter's
   namespaces in this build (`anthropic deepseek google minimax moonshotai nvidia openai
   openrouter xiaomi`, derived by `openrouter_namespaces`) that the user holds no
   route-authenticating credential for — `xai/grok-4` stays a refusal (OpenRouter spells
   xAI `x-ai/`);
4. OpenRouter IS route-authenticated: exported, stored by `/login` in the agent dir's
   `auth.json`, or the user's `models.json`. Both of the first two are the user's own act
   outside the repo — the shell is theirs, and ADR-0203 locks `AELIX_CODING_AGENT_DIR` /
   `AELIX_AUTH_PATH` against a `.env`. A key from a cwd `.env` never enables it;
5. the launch carries no `--api-key` (#370, 2026-10-06): under it guard 2 fires in no step
   (§2.1 steps 2 and 3b), and a string no provider places is pi's not-found.

The model is OpenRouter's catalogue entry when it lists the string, else the unanimous
OpenRouter backfill, with `OPENROUTER_BASE_URL`; a one-line Note says so.

**Widened to step (4) — a divergence from the owner's wording, taken from the critique
(S1/S2) and put to the owner (§7).** Guard 2 as worded fires only when no model matches
the string. But 133 namespace ids are in a vendor's catalogue and not verbatim in
OpenRouter's snapshot (anthropic 25/28, google 21/39, minimax 7/7, moonshotai 5/10,
nvidia 65/65, openai 8/48, deepseek 1/5, xiaomi 1/6 — the critique's count). For those,
pi's order lands on the vendor: an OpenRouter-exported user's `openai/o1-pro` would turn
from OpenRouter into `No API key found for OpenAI`, and with a planted `.env`
`OPENAI_API_KEY` it would run on the planted key — the #344 critique's M1 again, for 133
ids. pi itself reaches OpenRouter here whenever pi.dev's live list carries the id (its
swap); aelix, lacking that list, applies guard 2 when the inferred namespace provider has
no route-authenticating credential and no route-authenticated raw match exists — and
never under `--api-key` (§2.1 steps 2 and 3b).

Guard 2 is also NARROWER than the owner's wording in two ways, both safer: it takes only
`<segment>/<segment>` strings (a bare unknown id is pi's not-found, clause 1), and a
catalogued prefix must be one of OpenRouter's namespaces (`xai/grok-4` stays a refusal,
clause 3).

### 2.4 Divergences from pi, stated (ADR-0235)

- **Guard 1** — pi reads no `.env`; aelix does, so a `.env` credential is kept out of
  route-deciding judgements.
- **Guard 2** — pi overlays pi.dev's catalogue; aelix sends ids it cannot place to
  OpenRouter on the user's own OpenRouter key, including the widening in §2.3 — never
  under `--api-key`, where a string no provider places is pi's not-found (#370): print
  and json exit 1 with it, nothing sent; interactive holds the placeholder with a Warning
  (the "errors are held" divergence below); RPC keeps the unresolved route's shape — no
  message at start, the first prompt refused (#371 is that silent start).
- **No fuzzy matching, no `:thinking` suffix**; **ids compared case-sensitively** (pi
  folds case), with the canonical-prefix variant in (4)/(5).
- **aelix's provider case rule** (the owner's decision): pi has one case-folded map.
- **Rule U** (the sole user-defined provider among route-authenticated matches) and the
  settings `defaultProvider` tie-break and home (ADR-0195), which count only for the
  user's own credential or endpoint while the user holds one (§2.1 step 2).
- **(5)'s "inferred provider is the user's"**: pi takes the first raw match with no auth
  check (`:548-555`). Measured, that is where the planted capture lives — A10,
  `anthropic/claude-haiku-4.5`, `ANTHROPIC_API_KEY` exported and `OPENROUTER_API_KEY` from
  a `.env`: pi's order sends it to OpenRouter on the file's key; here it stays on
  Anthropic as a custom id (Anthropic will answer 404 for that spelling, and the warning
  names the `.env` key and `openrouter/<id>`).
- **`--api-key` keeps a vendor prefix on its vendor** (§2.1 step 3b): pi resolves first and
  attaches the key to wherever the route landed, so with an OpenRouter key exported its
  `--model openai/gpt-4o-mini --api-key K` sends K to OpenRouter.
- **`--provider X --model X/<id>` tries the id unstripped first** (§2.1 E): pi strips first
  (`:506-512`). So `--provider openrouter --model openrouter/auto` and
  `OPENROUTER_DEFAULT_MODEL=openrouter/auto` send `openrouter/auto` (OpenRouter's own id,
  which it accepts), while `--model openrouter/auto` (step 1) sends `auto`.
- **An unknown `--provider`** gets aelix's #98 text (`model 'm1' (provider 'nonexist') could
  not be resolved to a known API protocol…`), not pi's `Unknown provider "X". Use
  --list-models …` (`:436-441`); pre-existing, unchanged here.
- **Errors are held as placeholders** (`api='unknown'`, the prefix kept as the provider)
  so the late-registration check can still recognise them (§2.11); pi returns no model,
  and exits 1 in every mode (`main.ts:915-925` @ b223082bb). Interactive keeps the
  placeholder and a Warning, so `/model` can leave it.
- **An expired stored OAuth counts** as route-authentication — pi's rule; the user's own
  file, and its failure is loud (the #344 critique's S2 asked otherwise; reversed here).

### 2.5 Explicit routes to OpenRouter

`openrouter/<id>` (step 1 infers `openrouter`; the prefix is stripped, as pi does —
`openrouter/newlab/model-x` sends `newlab/model-x`; `openrouter/auto` sends `auto`, pi's
alias; an id that itself begins `openrouter/`, such as `openrouter/free`, is still found
by (5)'s raw match), `--provider openrouter --model <id>`, and `OPENROUTER_DEFAULT_MODEL`.
All three work with a key from any source, a `.env` included: the user named OpenRouter.

### 2.6 `OPENROUTER_DEFAULT_MODEL` is the shell's

Removed from `load_dotenv`'s provider-configuration arm (`_DOTENV_CONFIG_VALUES`): a model
choice is a route choice. Used only with no `--model` (and no seeded settings model) and
`--provider` absent or `openrouter`, as `--provider openrouter --model <it>`, when
OpenRouter has a key from any source — the variable chose OpenRouter, not the key. A
`.env` value is refused with the ordinary notice; `AELIX_DOTENV_ALLOW` still admits it by
name (ADR-0203's criterion: a redirect, not execution). So **"shell-only" means "unless you
hatch it yourself"**: with `AELIX_DOTENV_ALLOW=OPENROUTER_DEFAULT_MODEL` set in the user's
shell, a cwd `.env` value picks the no-model route (Codex's cross-review of `854bf319`, C3,
`probe_default_hatch.py`: `x-ai/grok-4.3` to `openrouter.ai` on the shell's key). Kept: the
opt-in is per name and made in the shell, and a `.env` cannot set `AELIX_DOTENV_ALLOW` or
the record for itself (`probe_controls.py` found no way). The asymmetry is deliberate and
written down in `.env.example`: a hatched CREDENTIAL is recorded and still does not
choose a route, while a hatched `OPENROUTER_DEFAULT_MODEL` or `OPENROUTER_BASE_URL` is
honoured — the user named each of them, and neither is a credential.
`OPENROUTER_BASE_URL` (the shell's, or hatched) now applies to every route that lands on
OpenRouter; before, only the rung applied it, so `--provider openrouter` without the key
ignored it.

### 2.7 The launch's call sites

- The harness build resolves through `resolve_route` (X1's order unchanged).
- After the first build the route's warning (a custom id, guard 2's Note) is printed once
  on stderr, unless the late-route check refused the model (§2.11); rebuilds do not reprint it.
- print/json refuse with the route's error (`Error: Model "…" is ambiguous across
  providers: …` / `… not found. Use --list-models …`) before any request; interactive
  warns with it first.
- `--api-key` is refused early only when there is no model string at all — pi's rule; a
  bare extension id (`-e extprov.py --model m1 --api-key K`) is no longer refused before
  the extension loads (ADR-0249 §6's last bullet). Every launch resolve is told the key is
  there (`typed_key`, §2.1 step 3b). A route that does not resolve (ambiguous, or not
  found — whose placeholder keeps the typed prefix as its provider) gets no key (on
  `d58cbb3e` the not-found placeholder got the key on its prefix, the review's nit). The
  late path used to switch to a `session_start` provider and move the key there; since #367
  it refuses, and a pending launch attaches the key only after the late decision (§2.11,
  D4), so a refused launch attaches it to no provider at all. Since #370 (2026-10-06)
  guard 2 never takes a typed key: a string no provider places (`newlab/model-x`, an
  OpenRouter vendor namespace with an id the snapshot lacks such as `x-ai/grok-4`, a
  provider an extension registered and unregistered in `session_start`) is pi's not-found
  and gets no key. The typed key still goes to OpenRouter by routes that name it or that
  pi takes, none of them guard 2:
  - an explicit OpenRouter route (§2.5, `OPENROUTER_DEFAULT_MODEL` included, §2.6);
  - the settings routes that name OpenRouter: `defaultProvider: openrouter` homing a bare
    id (`--model model-x`; step 2's ADR-0195 home, when it counts, §2.1) and the seeded
    settings pair `openrouter` + `<id>` with no `--model` (an explicit provider, step E) —
    from the global settings or a project `.aelix/settings.json`, which today aelix reads
    whatever the trust answer (§6; #369 makes it a trusted project's only). Measured by the
    round-1 verification on `62e2238b` and the fix alike (`.omc/probes/370-live/verify/kit/
    launch-compare.txt` L20p, L22p, L23p: `TYPED` to `openrouter.ai/api/v1` with the
    custom-id Warning) and again in round 2, with and without `--approve` and with and
    without an OpenRouter key of the user's own (`.omc/probes/370-live/fix2/
    settings-routes.txt`: global pair, global and project `defaultProvider`, project
    pair — 12 of 12 attached to `openrouter`, the typed key sent there);
  - an id OpenRouter's catalogue lists under a prefix that is not a provider
    (`x-ai/grok-4.3`) — step 2's hit, as in pi (`model-resolver.ts:465-504`). This build
    has 195 such ids: 163 are sole exact hits and go there; the 32 another provider also
    lists (`arcee-ai/trinity-large-preview`, `inception/mercury-2`, …) are step 2's
    several hits, which go to OpenRouter by the auth tie-break only when OpenRouter is the
    sole authenticated provider among those listing the id — settings `defaultProvider`
    decides first when it counts, and with none authenticated it is pi's ambiguity error
    (`.omc/probes/370-live/verify/kit/count195.py`, recounted in
    `.omc/probes/370-live/fix2/count195.txt`).

  A provider an extension registers in `setup()` and unregisters in `session_start` (the
  round-1 verification's `sgone`): with `--api-key` in print/json, `62e2238b` sent the typed
  key to that extension's own endpoint (the harness kept the `setup()`-time model); now
  the re-resolve after `session_start`, where no provider has the prefix, is pi's
  not-found, so the run exits 1 with the hinted not-found and sends nothing (L15p). Without `--api-key` (L15n) or without an OpenRouter key (L15o, L15q) it
  already exited 1; RPC (L15r) is unchanged and still sends to the extension's endpoint.
- **In-session, the launch's key goes where the launch put it** (#370's sweep, measured on
  `62e2238b` and after). `/new`, `/fork`, `/resume` and `/reload` re-resolve through the
  harness factory with `typed_key`, so a not-found launch stays held. `/model <s>`'s
  registry path never applies guard 2 (§2.8: `/model newlab/model-x` is refused on either
  side); its UNDECIDED fallback — no registry, which no stock-CLI session lacks (`entry.py`
  always creates one), or an empty one, or one whose introspection raised — re-resolves through `resolve_route` without `typed_key`, so
  guard 2 applies there as on a launch without `--api-key`: on the user's own OpenRouter
  key after a not-found launch, on the typed key only where the launch attached it to
  `openrouter` (round-1 verification, `verify/kit/sweep-tree.txt` rows 20-21 and 43-44;
  on `62e2238b` the not-found launch's fallback carried the typed key, row 21 of
  `sweep-base.txt`). RPC
  `set_model`/`cycle_model` have no registry in `--mode rpc` and select nothing. A
  delegated child never receives `--api-key` (§2.9). The post-`/login` pick chooses from
  `get_available()` (§2.10), never guard 2. `/agents use` re-resolves the launch inputs
  and, until #370, did so without `typed_key`: after a not-found `--api-key` launch it took
  guard 2 to OpenRouter on the user's own key while the next `/new` put the placeholder
  back; it is now told about the key as the factory is, and refuses with the launch's
  text. **Round 2 scoped that**: only a re-resolve of the LAUNCH inputs is told about the
  key — `/agents use --none`, a profile with no `model:`/`provider:` of its own, or one
  whose `model:` an explicit `--model` beat (`profile_named_route` false). A profile's own
  `model:`/`provider:` is a new pick, not the string the key was typed with, and resolves
  as on `62e2238b`: with the settings pair `anthropic`/`claude-haiku-4-5` and `--api-key
  K` (K attached to `anthropic`), `/agents use` of a profile with `model: newlab/model-x`
  takes guard 2 to OpenRouter on the user's own key, through the next `/new` too, and K
  never goes to OpenRouter (round 1 refused it — `.omc/probes/370-live/fix2/
  scope-probe-before.txt`). Where the launch attached the key to `openrouter` (the routes
  above), a later
  guard-2 route in-session carries it there — the key is OpenRouter's by the user's
  choice, their settings or pi's catalogue, not guard 2's.
- `/agents use` and `/model`'s UNDECIDED fallback refuse with the route's error when a
  registry exists (without one the resolver sees no credential and no user-defined
  provider, so its "none authenticated" would be a claim about nothing).

### 2.8 `/model <argument>` — guard 1 in-session

`/model` resolves over `get_available()` (narrowed by `/scoped-models`), which counts a
`.env` key, and persists the switch as the default model. Measured with
`OPENROUTER_API_KEY` exported and a vendor key from a `.env` (the design's M1 row and the
critique's M-a..M-d): `openai/gpt-4o-mini`, `anthropic/claude-new-9`, `openai/gpt-9-new`,
`gpt-4o-mini` and `claude-haiku-4-5` all switched to the vendor on the planted key.

**The rule** (`core/model_argument._route_aware_pool`): when the session holds a
route-authenticating credential of the user's own for any provider
(`runtime_bootstrap.holds_route_auth` — not read off `/scoped-models`' narrowed pool, since
an allow-list must not turn a user-with-their-own-key into a `.env`-only session, and not
read off model rows at all: the candidates are the registry's own sources, model rows,
`auth.json` entries, runtime overrides, models.json providers and extension registrations
with or without models, plus every provider an environment key name belongs to — Codex's
second cross-review, F3, below), every provider only a cwd `.env`
authenticates leaves the pool before anything reads it: the match, the current-provider
tie-break, the #136 backfill licence and the diagnosis. **A cwd `.env` key never chooses
a destination; where it would, `/model` refuses (fail-closed)** — a refusal that set such a
provider aside says so (`(openai: the key came from a project .env, which does not choose a
provider …)`), an id the catalogue does not hold under that provider's prefix included
(`anthropic/claude-new-9`: until the round-3 verification found it, the generic
`<provider>/<id>` refusal left the cause out — rows M-a, M-d). That is usually the answer the same session gives with no `.env` at all, but
not always — and outside a prefix naming a provider the user defined (below), the `.env`
never turns a refusal into a switch: an allow-list naming only models of the `.env`-authenticated provider refuses where, with no
`.env`, OpenRouter's same-named id would match; and a models.json / `auth.json` api key that
NAMES a variable only the `.env` sets resolves, with no `.env`, to the name as a literal key
for that provider (pi's `resolveConfigValue` fallback), while with it the provider leaves
the pool — a bare id is refused, a slashed one goes where the user's own keys route it
(measured, round-2 verification: `auth_names_env`, `scoped`). There is no exemption for the
session's current provider. A session with only `.env` credentials keeps `/model`'s answer:
nothing of the user's own competes (§6). **A runtime override is the user's own**: an
`--api-key` session counts as holding a credential (so a `.env` vendor key still decides
nothing there) and the provider the key was typed for stays in the pool (rows
`test_an_api_key_is_the_users_own_credential_…`, `test_the_provider_you_typed_an_api_key_for_…`;
the round-2 verification's sabotages X14 and X12 had left every row green). **An installed
fallback resolver is the user's own too** (Codex's third cross-review, C2): a resolver is a
function, so the providers it answers for cannot be listed, and `route_auth_candidates`
cannot discover a provider only it authenticates. Codex's embedder resolver answered for
`private-seat` (no rows, registration or stored entry) from an exported variable:
`has_route_auth("private-seat")` was True but `holds_route_auth` False, so with a `.env`
`OPENAI_API_KEY` `/model openai/gpt-4o-mini` went to `api.openai.com` on the file's key and
RPC `cycle_model` rotated to `openai` (without the `.env`: refused, nothing selected). Now
`holds_route_auth` is True whenever `ModelRegistry.has_fallback_resolver()` is — failing
toward the guard: both halves refuse and the rotation selects nothing. **The cost**: an
embedder that installs a resolver and holds only `.env` credentials gets the strict guard
(a `.env`-only provider is set aside, `/model openai/gpt-4o-mini` refuses naming the `.env`)
instead of the residual answer (§6). The stock CLI installs no resolver.

**A prefix naming a provider the user defined is the user's choice, not the guard's**
(round 3; the round-2 verification of `ecb4e0bc`, B1). The guard applies to every
route-DECIDING step — a bare id, a built-in or catalogued prefix, the tie-breaks, the
current-provider preference. When the prefix names a provider the user defined (models.json
or an extension, matched by the case rule below), the destination is already chosen, so that
provider stays in the pool whatever authenticates it and a `.env` key authenticates the
route, as the launch's step 3 does (§2.2: "a `.env` credential still authenticates a
route once chosen"; one shape still differs, an extension that took over a built-in name —
§6, #365). `ecb4e0bc` dropped it with every other `.env`-only provider, and the
case rule then found the pool empty: with `OPENROUTER_API_KEY` exported and a models.json
`MyGw` whose `apiKey` names `MYGW_API_KEY` in the cwd `.env`, `/model mygw/m1` was refused
("names 'MyGw', a provider you defined, which this session does not offer") while `--model
mygw/m1` ran on `https://mygw.invalid/v1` with the `.env` key; and the late switch (§2.11) to
a `session_start` provider keyed the same way was refused (real CLI, `-e sessenv.py --model
sessenv/m1`: `NO REQUEST | rc=1`). Now `/model mygw/m1` → `MyGw/m1 host=mygw.invalid
bearer='mygw-env-fake'`, `MYGW/new-id` → the #136 backfill on the same host and key, and the
late switch posts to the extension's host on `sessenv-dotenv-fake` (since #367 the launch
refuses `sessenv/m1` — §2.11 — and the same `/model sessenv/m1` in the held session posts
there). A re-pointed built-in
counts as defined by the user (`providers.openai.baseUrl`, ADR-0249): with `OPENAI_API_KEY`
only in the `.env`, `/model openai/gpt-4o-mini` goes to the user's gateway on the `.env` key,
as `--model` does (`ecb4e0bc`: refused; the key order is #363) — the one case where the
`.env` turns a `/model` refusal into a switch, the prefix having chosen the destination
(`/tmp/362-work/fix3/probes/after.out`, `/tmp/362-work/fix3/live/matrix.out`). That
includes a re-pointed `openrouter` (`providers.openrouter.baseUrl`): `/model`'s case rule
counts it as user-defined (`_case_rule_providers`), while the launch's
`user_defined_providers()` keeps dropping `openrouter` because at launch `openrouter/<id>`
is an explicit route anyway (§2.5). Codex's second cross-review (F6): with `OPENAI_API_KEY`
exported and the OpenRouter key only in the `.env`, `a0edf615` refused `/model
openrouter/auto` while `--model openrouter/auto` ran on the re-pointed host; now both give
`own-router.invalid /v1/chat/completions Bearer project-or-fake`, and both refuse without the
`.env` (`/tmp/362-work/fix4/probes/after_f6_launch.out`). TUI in a pty
(`/tmp/362-work/fix4/live/drive.py f6-repoint-or`, the re-pointed host at a local
listener): `a0edf615` refused `/model openrouter/auto` and `hi` went to `CONNECT
api.openai.com:443`; now `model → auto (openrouter)`, footer `✱ auto`, and `hi` posts
`/or/v1/chat/completions model=auto` on `or-dotenv-fake`. The built-in `openrouter` in the
same session without the re-point (`f7-builtin-or`) is refused naming the `.env`, and `hi`
stays on `CONNECT api.openai.com:443`. A bare `m1`
or `gpt-4o-mini` in that session is still refused naming the `.env` — it names no provider,
so a `.env`-only one may not be chosen for it (`--model m1` at launch takes it: pi's step 2
one-hit rule has no auth check; §6).

**The provider case rule holds in `/model` too** (`_user_defined_owner`): a
`<prefix>/<id>` whose prefix names a provider the user defined (§2.1 step 1: the exact
spelling, else the one user-defined name equal up to case) stays inside it — its model, the
#136 backfill under it, or a refusal naming it; two user-defined names differing only in
case, the prefix spelling neither, are refused as at launch. A refusal inside such a
provider names the real cause: when the provider lists the id but the pool does not offer
it, it says `/scoped-models` excludes it or it is not runnable here, as the non-owned "IS
logged in to" refusal does (Codex's second cross-review, F5: `MyGw` listing `m1` and `m2` on
one host with the allow-list `["MyGw/m1"]` was told `m2` is unlisted and its siblings
disagree on api and base URL — both false). Before, `/model` used the
pool's case-folded match, so a models.json `OpenAI` lost `openai/gpt-4o-mini` to a
credentialled built-in `openai` or to OpenRouter's verbatim id.

**Codex's cross-review of `854bf319`** found the earlier form unsound. It deferred to
`resolve_route` only when the answer landed on a provider the user held no credential for
**other than the session's current one**. With `OPENROUTER_API_KEY` exported and the session
on `openai`, a cwd `.env` `OPENAI_API_KEY` moved `/model openai/gpt-4o-mini` from
`openrouter.ai` (`Bearer or-shell-fake`) to `api.openai.com` (`Bearer openai-env-fake`) —
C1, `probe_model_simple.py`; and with a models.json `OpenAI` at `custom.invalid`, `/model
openai/o1-pro` moved from the user's endpoint to `api.openai.com` on the `.env` key — C2,
`probe_model_mock.py`. The deferral also let a `.env` key turn a refusal into a guard-2
switch: `anthropic/claude-new-9` was refused without the `.env` and sent to OpenRouter with
it (M-a). Re-run on this change, both probes give the same host and bearer with and without
the `.env` (`/tmp/362-work/fix2/probes/after-model.out`: `openrouter.ai … Bearer
or-shell-fake` twice; `custom.invalid … Bearer custom-fake` twice).

**Why not `resolve_route` for `/model` outright** (the design's deferred G6): a prototype
that answers `/model <arg>` with `resolve_route` alone (`/tmp/362-work/fix2/optionB/`)
turns 24 existing rows red — 19 in `tests/core/test_model_argument.py`, 5 in
`tests/tui/test_commands.py`: the current-provider tie-break for a bare id ("switch within
my seat", #134), the `/scoped-models` allow-list, the de-scoped id that must not be
resurrected and the backfill's unanimity rules (#136), and the refusal texts that name the
cure. The route-aware pool keeps every one of them and closes C1/C2 by construction.

**The no-argument picker is not filtered** (`scoped_available` / `get_available` are not
route-aware): it lists, and switches to, a provider only a `.env` key authenticates. That is
an explicit pick of a named provider — the user, not the key, chooses — so it is not a guard-1
hole. Marking such providers in the picker (a `.env` tag) is left as a follow-up: it is a TUI
rendering change, not a routing one, and is not made in this commit.

**RPC.** `cycle_model` is an implicit chooser — the client names no provider — so its
rotation is route-aware by the same helper (`_route_aware_pool`): while the user holds a
credential of their own, a provider only a cwd `.env` authenticates leaves it, and a
rotation left with one model or none returns `data: None` as before. Codex's second
cross-review (F2): with `OPENROUTER_API_KEY` exported and `OPENAI_API_KEY` only in the
`.env`, cycling from `openrouter/z-ai/glm-5.3-flash` went to `openai/gpt-4` and the request
to `api.openai.com` on `Bearer project-fake`; now `openrouter/ai21/jamba-large-1.7` on
`Bearer own-or-fake`, with and without the `.env`. Both commands reach a registry only
where the program embedding `run_rpc_mode` passes one: the shipped `aelix --mode rpc` and
`aelix-server`'s `/rpc` do not, and answer `cycle_model requires a ModelRegistry — none
configured` (measured in round 4 through the real CLI, `/tmp/362-work/fix4/live/rpc.out`,
on `a0edf615` and now alike) — so this closes the path for embedders; wiring the registry
into the shipped RPC mode is a separate change. **`set_model {"provider","modelId"}`
is kept as it is**: it NAMES the provider, like `--provider` at launch and the no-argument
picker, so a `.env` key may authenticate the route the client named (pinned by
`test_set_model_names_the_provider_so_a_dotenv_key_may_authenticate_it`; changing it is an
owner decision).

What still differs from `--model`, deliberately (pre-existing, #134/#136): a bare id several
of the user's providers serve takes the session's current provider or is refused naming
them (no `defaultProvider` / rule-U tie-break), and an uncatalogued `<namespace>/<id>` the
user holds no vendor key for is refused with the `<provider>/<id>` hint (no guard 2 in
`/model`; `openrouter/<id>` is the route). **And `/model` is stricter than the launch on
`openrouter/<id>`**: the built-in `openrouter` prefix (not re-pointed) is route-deciding in
`/model`, so while only a cwd `.env` authenticates OpenRouter and the user holds a key
elsewhere, `/model openrouter/openai/gpt-4o-mini`, `openrouter/auto` and
`openrouter/newlab/model-x` are refused naming the `.env`. At launch `openrouter/<id>` is an
explicit route the `.env` key authenticates (§2.5; measured on `a0edf615` by the main loop —
own `OPENAI_API_KEY` exported, the OpenRouter key only in the cwd `.env`, `--model
openrouter/openai/gpt-4o-mini -p hi` → `CONNECT openrouter.ai:443`). Codex's second
cross-review (F7) showed a mutant exempting every `openrouter/` reference from guard 1 passed
all 49 rows of the three guard files; the three rows above (each also in the
with-and-without-`.env` invariant) now pin it. Whether `/model` should take the launch's
explicit-route rule is put to the owner (§7 item 10).

### 2.9 Delegated children

`agents.resolver._pin_route` splits `--model` into `--model <id> --provider <p>` only
where the child could not reach the parent's route alone: a user-defined provider (the
child loads no extensions by default; ADR-0249's M3, kept even when the profile brings its
own `extensions:` — a pinned user-defined route can only refuse, never reach OpenRouter).
A route only the parent's `--api-key` decided is NOT pinned (it was on `d58cbb3e`). The key
is the only runtime override and sits on one provider P; when the route differs without
it, the key made P route-authenticated where nothing else does, so the route lands on P
and the child holds no credential of its own there. Pinned, the child authenticated P with
what it had — the review measured a parent `--model openrouter/auto --api-key K` in a repo
whose `.env` plants `OPENROUTER_API_KEY`: a profile's `anthropic/claude-haiku-4-5` was
pinned to `--provider openrouter` and the child posted it on the planted key
(`OR /or/v1/chat/completions … token=or_env`); unpinned it refuses (`No API key found for
Anthropic.`). Everything a credential decided is left to the
child, which inherits the keys and the record and decides it the same way — and guard 2 is
never pinned: the critique measured a profile with `extensions: [childext.py]` and
`model: childext/m1`, where the parent (which never loads `childext`) answers OpenRouter
and the pinned child sent its prompt to `openrouter.ai`; unpinned, the child reaches its
extension (`test_a_real_child_reaches_its_profiles_own_extension_provider`).

### 2.10 The post-`/login` pick

`tui/shell.py` hands pi's `find_initial_model` a view (`_RouteAuthView`) whose
`get_available()` is `_route_aware_pool` over the registry's — while the user holds a
credential of their own anywhere (`holds_route_auth`, not read off model rows), providers
only a cwd `.env` authenticates are left out; a `.env`-only user keeps everything. Measured
on `9ca53a4f`: a stored Codex login plus a `.env` `ANTHROPIC_API_KEY` picked `anthropic
claude-opus-4-7`; now `openai-codex`.

**The view's `find()` offers only what its `get_available()` offers** (`None` otherwise).
The cascade's saved-default arm (settings `defaultProvider`/`defaultModel` — a project
`.aelix/settings.json` is the repo's) reads `find`, which `a0edf615` forwarded to the
registry. Codex's second cross-review (F1, `probe_login.py`, driving the real TUI `/login`):
project settings naming `google-vertex/gemini-3.1-pro-preview`, the user's own OpenRouter key
stored by the login, and a `.env` `GOOGLE_CLOUD_API_KEY` — the pick went to
`aiplatform.googleapis.com` on the file's key, without the `.env` to `openrouter.ai` on
`Bearer own-login-fake`. Now both runs give `openrouter.ai … Bearer own-login-fake`: such a
saved default is skipped exactly as an unrunnable one is, and the cascade falls through.
The real TUI in a pty (`/tmp/362-work/fix4/live/drive.py f1-login`: launched on an
unrunnable `--provider nosuchprov --model x`, a project `.aelix/settings.json` default
`openai/gpt-4o-mini`, `OPENAI_API_KEY` only in the `.env`; `/login` → API key →
`openrouter`): `a0edf615` "model → gpt-4o-mini", the GLOBAL settings then holding
`defaultProvider "openai"` (the repo's choice persisted as the user's), `hi` → `CONNECT
api.openai.com:443`; now "model → openai/gpt-5.4", `defaultProvider "openrouter"`, `hi` →
`CONNECT openrouter.ai:443`. A
saved default the view does offer is still picked. With a credential of the user's own only
on a provider with no model rows (an `auth.json` key for a registered provider without
models, F3), `a0edf615`'s view fell back to everything and picked the `.env`'s vendor; now
it offers nothing and the user is told to `/model`. The call site itself (the view handed
to `find_initial_model`, not the raw registry) is pinned through the real `run_tui` and
`/login` (§4, round 5): the fourth verification replaced it with the registry and every
test stayed green while `probe_login.py` went to `aiplatform.googleapis.com` again.

### 2.11 The late-registered path refuses (#367, 2026-10-03)

A provider registered only in `session_start` is an unknown prefix at launch. With an
OpenRouter key of the user's own, guard 2 puts the string on OpenRouter; without one the
launch holds the placeholder. Until 2026-10-03 every mode then switched to the provider
(#344, and with no OpenRouter key since this ADR's critique S5). **#367 (owner decision
2026-10-02 on the issue: follow pi) refuses it instead.** pi registers an extension's
providers in time for startup model selection only from the extension factory
(`docs/custom-provider.md`: "Pi waits for asynchronous factories before startup
continues, so providers registered there are available to startup model selection");
`session_start` fires in `AgentSession.bindExtensions`, and `main.ts` (~913-925 @
`88ff80b98`) exits 1 on the resolver's error diagnostic in every mode.

- **Which provider is late, and when it counts (round 3, D1; the window, rounds 8-9).** A
  provider is LATE when it was not registered when the launch route was chosen and it
  arrived while a session was starting: from the end of a build (`LateRoute` snapshots the
  registry there) through that session's `session_start` emit and — on a pending launch or
  a held rebuild, where the turn gate refuses them — the turns its handlers triggered,
  which aelix waits out, up to aelix's check after `session_start`
  (`LateRoute.after_session_start`: `cli/entry.py` right after the launch's
  `create_agent_session_runtime`, and in the after-`session_start` seam of every rebuild).
  Whatever registered it in that window counts: a `session_start` handler; the `input` or
  `before_agent_start` handler of a turn one triggered (round 8, Codex pass 7, C-P2) —
  awaited, or fire-and-forget, when that refused turn runs AFTER the emit returned; a task
  a handler spawned with no delay; a task `setup()` spawned that registers then. Measured
  (round 9, `trace9.py`, the real CLI, every request recorded): `ff-input`, `ff-bas`,
  `task0`, `setup-task` print `EMIT_DONE session_start` before `REGISTERED_IN …` and are
  refused in print and held in RPC, no request; `await-input` and `ss-direct` register before
  the emit returns. A registration after that check — a task with a delay (§6), a handler
  of a later turn — is not late. Kept, as pi: pi resolves the launch model before ANY
  handler runs, so a provider any of them registers is unknown to its launch. The texts
  said "registered in a session_start handler" (false for a triggered turn's handler),
  then "registered while session_start handlers ran" (round 8; verify round 8, B1: false
  for the four shapes that register after the handler returned); since round 9 they say
  "registered while a session was starting (for example in a session_start handler)",
  true for each of them and on a later rebuild held for a registration in an earlier
  session's start. `launch_providers` is the user-defined providers the first
  build saw (`cli/entry.py` reads them before `create_agent_session_runtime` runs
  `session_start`), and `runtime_bootstrap.LateRoute` snapshots the registry at the end of
  every build and after its `session_start`, so only what arrived during one counts — a
  provider a `/reload`-ed extension now registers in `setup()` is not late (the #344
  reload row). The rule is where the inputs LAND, not which pair they are: every
  **implicit** re-resolution of the launch-derived inputs that lands on a late provider is
  held. Round 2 keyed the hold by the exact refused `(parsed.model, parsed.provider)`
  pair, and `/agents use --none` (or of a profile naming no route) resets `parsed` to the
  CLI + settings baseline — a different pair when the launch came through `--agent` — which
  re-resolved onto the late provider: the next prompt went there (verify round 2, shapes
  a and b), and from a launch that was never held (shape c, `--agent orprof`
  (`provider: openrouter`) `--model sessext/m1`).
- **The launch is judged by its inputs, not by what a hook did (D2).**
  `late_registered_route` re-resolves `--model`, `--provider`, settings
  `defaultProvider` exactly as the build passes them — the tie-break included (Codex
  round 3, finding 5: a check that dropped it passed every #367 test; two providers serving
  `m1`, the setup one keyed and the late one keyless, with `defaultProvider` naming the
  late one, tell them apart) — and the `--agent` overlay, over the registry as it is after
  `session_start`. It fires when they land on a late provider, on a prefix two late
  providers share up to case, or — the re-resolve refusing them as ambiguous — on ids only
  late providers serve. The model the harness holds after `session_start` is not evidence:
  round 2 returned early when it was runnable and not OpenRouter, so a hook that called
  `set_model` onto the late provider made the refusal pass and print ran there (Codex
  finding 2). (Round 3 also refused a launch that had resolved to a registered provider
  when its inputs now landed on a late one; round 4 withdrew that, next bullet.) It used to
  look only at a `--model` with no `--provider`, so `--provider sessext --model m1` and the
  settings pair `sessext`/`m1` were never caught: they passed the print gate, which
  judged the re-resolve (by then `sessext`), and failed at the first turn with `No
  provider registered for api='unknown'`.
- **Only a launch no registered provider claimed is refused (verify round 3 of `5a1330b5`,
  B1; round 4).** pi resolves the launch model at startup, before any `session_start`, and a
  provider registered later does not touch that launch: `--model gpt-4o-mini` with an
  OpenAI key runs on OpenAI whatever a hook registers afterwards. So the refusal (print and
  json exit 1; the interactive and RPC launch hold) is for a PENDING launch only — the
  routes D4 covers, guard 2's OpenRouter for an unknown prefix or an unresolved
  placeholder. A launch that resolved to a REGISTERED provider (built-in, `models.json`,
  `setup()`) stays on it, its `session_start` may trigger turns there as in pi, and its
  `--api-key` stays where it was attached at launch; the print gate judges that route, not
  the post-`session_start` re-resolve that lands on (or is made ambiguous by) the late
  provider. Round 3 refused it after its `session_start` had already run: a handler's
  `trigger_turn` had sent a turn on the launch route with the user's key or the typed
  `--api-key`, and print/json then said "No prompt was sent." — false — while RPC and
  interactive printed the hold warning (the verifier's `v3-builtin-openai-trigger`,
  `v3-otherlate-trigger-default(-typed, -json, -rpc)`, `v3-otherlate-keyed-trigger`).
  LATER implicit re-resolutions that land on a late provider are still held (below) —
  **also when the session started on a registered provider**: aelix's rebuilds re-resolve
  the launch inputs where pi keeps the session model, so `/new` after such a launch is held
  where its re-resolve lands late (measured below).
- **On a pending launch, nothing is sent while `session_start` runs (D4, and since round 5
  the turn gate).** A launch route no registered provider
  claimed — guard 2 (the string to OpenRouter as written) or an unresolved placeholder — is
  PENDING while the handlers run: the harness sits on `Model(id, provider)` with
  `api='unknown'` (assigned, no `model_select`), which is the model the handlers see, and
  its turns are HELD (`AgentHarness.hold_turns`, Aelix-additive): no turn of any kind
  starts — `prompt`, a handler's `send_message(..., trigger_turn=True)`, a manual
  `compact`, a summarising `navigate_tree` — whatever model is current. A handler's
  `trigger_turn` runs as a REFUSED turn (round 6; verify round 5): the turn a model that
  cannot be reached runs — `agent_start`, its message, an error answer carrying the gate's
  reason, `turn_end`, `agent_end` — with no request, so a handler awaiting that turn's
  `agent_end` (or `ctx.is_idle()`) is woken. Round 5 queued the message as a `next_turn`
  message instead; no turn started or ended, and a handler that awaited its own turn left
  `session_start` — and `aelix -p` with it — hanging with no output, also on a guard-2
  launch that was not late (the verifier's `o-await-forever-notlate`). The message is that
  refused turn's prompt, recorded in the session as any failed turn's is, and not queued
  again: the next prompt that is sent carries it from the conversation (the refusal, an
  error answer, is not sent to a provider); what other handlers queued waits for that
  prompt. The launch decision (and a rebuild's settle) first waits for such a turn to end,
  so its release cannot race the first prompt. Round 2 refused
  after `session_start`, but a hook's `trigger_turn` had already gone to OpenRouter on the
  user's own key — with `--api-key`, on the TYPED key, attached before `session_start` —
  and the refusal then said "No prompt was sent" (Codex finding 1). Round 3's placeholder
  alone covered only turns on the launch model: a handler that called `set_model` (onto
  the `session_start` provider, or onto any other) and then triggered a turn sent it on the
  model it set, and print/json said "No prompt was sent." after it (verify round 4, B1:
  `x-set-trigger-*`, `x-setother-trigger-print`). The gate is lifted only after the late
  decision: late → refused (print/json, the gate never lifted) or held (interactive/RPC,
  lifted once the hold's placeholder is set — it refuses turns itself and `/model` leaves
  it); not late → lifted after the restore below. After `session_start`:
  late → refused / held; not late → the launch route is put back (unless a handler chose a
  model of its own with `set_model`, which stands, as before #367) and only then is
  `--api-key` attached, to that route's provider. A launch route on a registered provider
  is not pending: it keeps its key from the start (a `setup()` provider, unchanged), and a
  turn its `session_start` triggers is sent on it (B1; not gated). "No prompt was sent."
  is printed only for a pending launch, where the gate guarantees it by construction.
- **A hold is applied, then checked (round 6; verify round 5, B1 and B2).** Every path
  that puts the session on a hold's placeholder — the launch hold, every rebuild's re-hold
  in the after-`session_start` seam (D3, below) and `/agents use` — goes through one
  helper, `LateRouteHold.apply`: `set_model(placeholder)`, so `model_select` handlers see
  it, and then the state is checked; a handler that answered the placeholder with a
  `set_model` of its own (onto the late provider, or anywhere) has moved the session to a
  model nobody picked, and the placeholder is put back by assignment (no second
  `model_select`). The launch lifts its turn gate and prints its Warning only after that,
  so "No prompt will be sent for it" is true when printed. Before, only the rebuild
  checked (and no test pinned that check — removing it left every test green while the
  verifier's `v5-msr-rebuild` sent `hi` to `/late/v1` after `/new`); the launch did not,
  and a `model_select` handler's `set_model` onto the late provider released the launch
  hold with the Warning already printed (`o-ms-handler-rpc`: the prompt to `/late/v1`;
  `v5-ms-launch`, `v5-ms-launch-noor` in a pty: `hi` → `/late/v1`). A refusal from a
  `model_select` handler still leaves the state on the placeholder, and each caller decides
  as before (the launch refuses to start; `/agents use` rolls back).
- **A hold is applied under the turn gate, on every path (round 7; verify round 6, B1).**
  The check above comes after `set_model` returns; inside it, a `model_select` handler
  that answers the placeholder can `set_model` onto the late provider, trigger a turn and
  yield, and that turn runs on the model it set before the placeholder is put back. The
  launch and a rebuild the factory held were gated by their own turn gate; `/agents use`
  was not — the session's gate had been lifted long before — and neither was the settle of
  a rebuild whose `session_start` first registered the provider (the factory found nothing
  to hold, so it put up no gate). Measured on `343e75cd`, in a pty: `--agent orprof
  --model late/m1`, then `/agents use --none` → `/late/v1/chat/completions m1 Bearer
  late-fake`, and the Note "No prompt will be sent for it" printed after it; `--model
  late/m1`, `/model other/m1`, `/agents use --none` → the same; `late` registered on
  rebuilds only, `/new` → the same. `LateRouteHold.apply` now holds the turns itself
  while it runs (`AgentHarness.hold_turns`, reason `No turn runs while this session is put
  on hold.` and the hold's), whoever calls it, so no path can apply a hold with turns
  open: the handler's trigger runs as a refused turn (`✖` and that reason in the TUI),
  nothing is sent, and the session ends on the placeholder. **The reason is the hold's on
  every path since round 8** (verify round 7, V-B1): round 7 left a caller's gate as it
  was, so on the launch (interactive and RPC) and on a factory-held rebuild (`/new`
  `/reload` `/fork` `/resume`, also after a registered launch) the handler's refused turn
  printed the caller's reason — `✖ No turn runs while this session's session_start
  handlers run. The launch route is not decided yet: …` right under the Warning that had
  just decided it (verify7's pty `p01`, `p03`–`p06`, `p22`; RPC `r1-launch`: `[error] No
  turn runs while this session's session_start handlers run. …`), while the rebuild-first
  and `/agents use` paths printed the true one. `apply` now swaps its own reason into a
  caller's gate for its duration (`hold_turns` replaces the reason in place; the gate is
  never down in between) and restores the caller's on exit. A turn a `session_start`
  handler triggers WHILE `session_start` runs keeps the `session_start` reason, which is
  true there (verify7's `p23`/`p23b`). A gate the caller already
  holds (the launch's, a held rebuild's) is left for the caller to lift, as before; one
  `apply` put up is lifted when it returns, after the refused turn it produced has ended,
  so the next prompt does not find the session busy. The post-`/login` pick applies no
  hold (it selects a model that is not late, or nothing, leaving the placeholder as it
  is), and the factory's placeholder is set at construction (no `model_select`) under the
  factory's gate.
- **After every `session_start` (D3).** The runtime's after-`session_start` seam
  (`AgentSessionRuntime.set_after_session_start`, Aelix-additive) runs right after each
  rebuild's emit — `/new`, `/fork`, `/resume`, `/import`, `/reload` — and before any turn:
  the factory starts the rebuild held where the inputs land on a late provider, and this
  re-applies the hold, so a hook's `set_model` there does not release it (Codex finding 3:
  `/new` then `hi` reached the late provider). A rebuild the factory holds also has its
  turns held from the factory until this seam has re-applied the hold (round 5; verify
  round 4, B1: a handler's `set_model` then `trigger_turn` in that `session_start` sent the
  turn on the model it set — `/new` → `/late/v1`, or `/other/v1`, `/reload` the same —
  before the re-hold). A rebuild the factory did not hold — its `session_start` registered
  the provider for the first time — has no gate while that `session_start` runs (§6), but
  the seam's re-hold is applied under one (round 7, above). An extension's `set_model` at
  any other time is the extension's business, unchanged.
- **print and json**, on a pending launch, exit 1 before any request: `Error: The launch model "sessext/m1" names
  provider 'sessext', which an extension registered while a session was starting (for
  example in a session_start handler), after the launch model was chosen. Register
  'sessext' in the extension's setup() (its factory) to use it at launch. No prompt was
  sent.` A provider with no model (`--provider sessext`
  alone, or a settings `defaultProvider` alone) is worded as one: `The launch provider
  'sessext' (no model named) was registered by an extension while a session was starting
  (for example in a session_start handler), after the launch route was chosen. Register
  'sessext' …` (verify round 1, N2; pi's own
  "--provider requires --model" is #368). An automatic pick that lands on a late provider
  before any hold was made says `Provider 'sessext' was registered by an extension while a
  session was starting (for example in a session_start handler), after the launch route
  was chosen; it is used only when you pick it. Register 'sessext' …`
  (`LateRoute.landing_reason`'s fallback, pinned since round 9). (Until round 8 these said
  "in a session_start handler", which a registration in a triggered turn's `input` handler
  made false; round 8's "while session_start handlers ran" was false for a registration
  after the handler returned, above.)
- **interactive and RPC**, on a pending launch, start held on `Model(id, provider)` with `api='unknown'` (the
  #98 shape: aelix's interactive warns and `/model` is the cure, a divergence from pi's
  exit) with the same reason. Every turn entry refuses the placeholder before a request.
  Interactive adds `No prompt will be sent for it; run /model to select a model.` RPC
  cannot: the shipped `aelix --mode rpc` wires no model registry, so `set_model` and
  `cycle_model` answer `set_model requires a ModelRegistry — none configured`; it adds
  `No prompt will be sent for it, and this RPC session cannot select another model
  (set_model has no model registry in --mode rpc); restart with another --model.` (verify
  round 1, N1 — the wiring is a pre-existing gap, a follow-up).
- **Every implicit re-resolution of the launch inputs keeps the hold** where it lands on a
  late provider (`LateRoute`, one object `cli/entry.py` hands to each): every rebuild
  (`/new`, `/fork`, `/resume`, `/reload` — the harness factory, then D3's seam), since the
  shared registry keeps the `session_start` registration and a rebuild's re-resolve would
  otherwise run what the launch refused — or, after a launch that stayed on a registered
  provider (B1), move the session onto a provider nobody picked — as an unknown
  `--model`'s rebuilds stay held too;
  **`/agents use`** of a profile whose own `model:` / `provider:` does not apply — one
  naming none, `--none`, or one a CLI `--model` overrides ("CLI flags override model") —
  which resets `parsed` to the CLI baseline and re-resolves it (verify round 1, B1, and
  round 2's shapes); it keeps the placeholder and repeats the reason in its `Note:` lines;
  and **the TUI's post-`/login` pick** (§2.10), whose saved settings default can be the
  refused launch input — it never lands on a late provider: it asks again without the
  saved default, which picks the way a pick with no default does (in the measured row, a
  model of the provider just logged into; in general, whatever that pick selects), never the
  late provider, and selects nothing when even that lands on a late one
  (`Logged in. <reason> Use /model to select a model.`). A prompt on the held placeholder
  is refused with the hold's reason and remedy, not the generic "could not be resolved to
  a known API protocol … models.json" text.
- **An explicit pick switches**: `/model sessext/m1` afterwards is the user's own later
  choice (the provider is in the registry by then; §2.8's case rule keeps a `.env`-keyed
  one in the pool), as is `/agents use` of a profile whose own `model:` **or `provider:`**
  applies — a profile naming only `provider: sessext` names the route too. Unchanged. A
  profile's pick marks the rule explicit, so the rebuilds after it re-resolve the profile's
  choice rather than hold it, until an implicit `/agents use` holds again. A `/model`
  choice lasts until the next rebuild, which re-resolves the launch inputs and is held
  again (aelix's rebuild re-derives the model from `parsed`; pi keeps the session's
  model), and `/model` saves it as the default, so the next launch with no `--model` reads
  it from settings and is refused. Both are stated, not changed here.
- **`--api-key`** typed with such a model gets the same refusal, and is attached to
  nothing: a pending launch attaches it only after the late decision (D4), and a pending
  launch that is not late gets it then, on the restored route (round 4, B3: no test held
  that attach). A launch route on a registered provider is not refused (B1) and keeps the
  key it had from the start. **F4 (§5) is closed**: there is no
  switch left for the key to arrive after. A provider registered and then unregistered
  inside `session_start` is not in the registry afterwards, so the launch's guard-2 route
  stands — with `--api-key`, the typed key to OpenRouter (Codex finding 4): #370
  (`--api-key` with an uncatalogued, non-user-defined prefix should not take guard 2, as
  pi), not this rule. Since #370 (2026-10-06) that launch is pi's not-found under
  `--api-key`: print/json exit 1, no key attached, nothing sent.
- **The print gate also judges the harness's own model**, not only the re-resolve, so a
  placeholder never reaches a turn even if the check above says nothing. After a launch on
  a registered provider (B1) whose re-resolve now lands on a late provider, or is made
  ambiguous by one, the gate judges the launch route instead of the re-resolve — otherwise
  a keyless late provider would stop the run with "No API key found" for a provider it
  does not use.
- **Gone**: `switch_to_late_registered_route`, `switch_model_argument(persist=False)` (the
  `/model` handler is its one caller), and the key following the switch (X'); round 3
  removed the pair-keyed `late_route_hold` dict and `late_registered_route`'s
  `current_model` argument.

Measured (real CLI, fake keys, `HTTPS_PROXY` at a local CONNECT recorder, `OPENROUTER_BASE_URL`
and the extension at local recording listeners; `/tmp/367-work/impl/live/matrix.py`,
`base.out` on `5dee21d1`, `after.out` here):

| Row | `5dee21d1` | #367 |
| --- | --- | --- |
| `-e sessext --model sessext/m1`, OR exported / `.env` / none (print; json the same) | switched, `EXT POST /sess/v1/chat/completions` | NO REQUEST, exit 1, the refusal |
| the same, OR exported, the late check removed (sabotage) | — | `OR POST /or/v1/chat/completions model=sessext/m1` (guard 2) |
| `--provider sessext --model m1`; settings `sessext`/`m1`, no `--model` | NO REQUEST, exit 1, `No provider registered for api='unknown'` (first turn) | NO REQUEST, exit 1, the refusal |
| bare `--model m1`; settings `defaultProvider: sessext` + `--model m1`; `--agent` with `model: sessext/m1`; `sessext/m2`; `--continue` | switched, EXT request | NO REQUEST, exit 1, the refusal |
| `-e late.py --model late/m1 --api-key K` (`late` keyless), OR exported / none | exit 1, "a provider you defined, which this session does not offer" (F4) | NO REQUEST, exit 1, the refusal |
| a delegated child's argv (`--model m1 --provider sessext`, pinned; with `-e sessext.py`) | exit 1, `No provider registered for api='unknown'` | exit 1, the refusal |
| `-e extprov --model extprov/m1` (`setup()`), `--provider extprov --model m1`, `--model m1 --api-key K` | EXT request | EXT request (unchanged) |
| RPC, OR exported / none: `get_state`, `prompt` | `sessext/m1 api=openai-completions`, EXT request | `sessext/m1 api=unknown`, NO REQUEST |
| TUI (pty), OR exported / none: `hi`, `/model sessext/m1`, `hi` | EXT request on the first `hi` | the Warning above the banner; `hi` → `✖ model 'm1' (provider 'sessext') could not be resolved …  Run /model …`, NO REQUEST; `/model sessext/m1` → `model → m1 (sessext)`; `hi` → EXT request |
| TUI: `/new`, `hi` | (EXT request) | still held, NO REQUEST |

Verify round 1 (of `dcc78170`), measured with the verifier's pty driver on own ports
(`/tmp/367-work/fix2/kit/drive2.py`; `repro/` on `dcc78170`, `after/` here):

| Row (TUI, pty) | `dcc78170` | verify round 1 |
| --- | --- | --- |
| `--model sessext/m1`, OR exported / none; `/agents use plainprof` (no `model:`), `hi` | EXT request | NO REQUEST, the `Note:` with the reason |
| bare `--model m1`; `/agents use plainprof`, `hi` | EXT request | NO REQUEST |
| `--model sessext/m1`; `/agents use --none`, `hi` | EXT request | NO REQUEST |
| `--model sessext/m1`; `/agents use lateprof` (`model: sessext/m1`, beaten by the flag), `hi` | EXT request | NO REQUEST, `Note: CLI flags override model` and the reason |
| settings `sessext`/`m1`; `/agents use plainprof`, `hi` | EXT request | NO REQUEST |
| settings `sessext`/`m1`; `/agents use lateprof` (its `model:` applies), `hi` | EXT request | EXT request (unchanged) |
| `--model sessext/m1`; `/agents use plainprof`, `hi`, `/model sessext/m1`, `hi` | — | NO REQUEST, then EXT request |
| settings `sessext`/`m1`; `/login` (API key), `hi` | `model → m1`, EXT request | `model → Ring-2.6-1T` (the provider logged into), `CONNECT api.ant-ling.com:443` |
| RPC, OR exported / none: stderr | `… run /model to select a model.` | `… this RPC session cannot select another model …; restart with another --model.` |
| print, `--provider sessext` alone | `The launch model "--provider sessext" names provider …` | `The launch provider 'sessext' (no model named) was registered …` |

Round 3 (verify round 2 and Codex), each row reproduced on `9e233be9` with the reviewer's own
probe before the change (copied to `/tmp/367-work/fix3/`, own ports; `before/` there, `after/`
here). Codex's probes run the real `main_sync` with every HTTP request recorded by an
`httpx.MockTransport`; round 2's are a pty driver against local recording listeners:

| Row | `9e233be9` | round 3 |
| --- | --- | --- |
| (Codex 1) hook registers `late` and `trigger_turn`s; `--model late/m1 -p hi`, own OR key | `MOCK_REQUEST /or/v1/chat/completions model=late/m1 auth=Bearer or-own-fake`, then "No prompt was sent." | requests=0, exit 1, the refusal (json, RPC the same) |
| the same with `--api-key typed-for-late-fake` | `… /or/v1/chat/completions … auth=Bearer typed-for-late-fake` | requests=0; the key attached to nothing |
| (Codex 2) hook `set_model(late/m1)` in the launch's `session_start`, print / json / RPC | exit 0, `MOCK_REQUEST /late/v1/chat/completions` | print/json exit 1 with the refusal, RPC held; requests=0 |
| (Codex 3) TUI, hook `set_model` on every rebuild: `hi`, `/new`, `hi`, `/reload`, `hi` | requests 0, 0, 1, 1, 2 (both to `/late/v1`) | 0 throughout |
| (Codex 5) `--model lab/m1`, two late providers serving `lab/m1`, settings `defaultProvider: late` — the late check without the tie-break | mutant: exit 0, `/or/v1 … model=lab/m1`; 77 #367 tests green under it | refused with and without the tie-break; the tie-break row that tells them apart is `setup()` `other` (keyed) + late keyless `sessext`, both serving `m1` (`test_the_late_decision_uses_the_settings_default_provider`; round 4 keeps that launch on `other` and the tie-break shows at `/new`: `test_a_rebuild_after_a_registered_launch_is_held_where_it_lands_late`) |
| (round 2, a) settings `sessext`/`m1` + `--agent lateprof`; `hi`, `/agents use --none`, `hi` (OR exported / none) | last `hi`: `EXT POST /sess/v1/chat/completions` | NO REQUEST |
| (round 2, b) `--agent provonly --model m1`; `/agents use --none` or `plainprof`, `hi`; or `/new`, `hi` | `EXT POST …` after `/agents use` | NO REQUEST |
| (round 2, c) `--agent orprof --model sessext/m1` (OpenRouter, never held); `hi`, `/agents use --none`, `hi` | `OR POST … sessext/m1`, then `EXT POST …` | `OR POST … sessext/m1` (unchanged), then NO REQUEST |
| `--model newlab/x`, a hook registering `late` and triggering a turn (not late) | two requests to `/or/v1` (the hook's, then `hi`) | one, `hi`, on the restored guard-2 route (carrying the hook's message — §6) |
| `--model newlab/x`, a hook registering `late` (not late), print / json / RPC | `/or/v1 … newlab/x` | the same; the hook saw `api=unknown` |
| explicit picks: `/model sessext/m1`; `/agents use provonly` (`provider:` only); settings pair + `/agents use lateprof`; `/login` with settings `sessext`/`m1` | EXT request; EXT; EXT; `api.ant-ling.com` | the same (pty, `after/v2-tui-controls.out`) |
| setup() providers: `setup-print`, `-json`, `-split`, `-rpc`, `-typed`, `both`, `models-name` | `/setup/v1` (typed key on `setup-typed`), `/models/v1` | the same |
| the #362 launch matrix (46 rows, `5dee21d1` → here) | — | 3 rows differ, all `session_start` (A26-A28: EXT request → NO REQUEST, the refusal) |

Round 4 (verify round 3 of `5a1330b5`), each row reproduced on `5a1330b5` with the
verifier's own probes first (copied to `/tmp/367-work/fix4/`, `p-before/` there, `p-after/`
here; the real `main_sync`, every HTTP request recorded by an `httpx.MockTransport`). `other`
is a keyed `setup()` provider, `late` a `session_start` one, both serving `m1`:

| Row | `5a1330b5` | round 4 |
| --- | --- | --- |
| (B1) `--model m1`, settings `defaultProvider: late` (keyless), the hook triggers a turn; print / json | the hook's turn to `/other/v1`, then exit 1, "… No prompt was sent." | the hook's turn and `hi` to `/other/v1` (`Bearer setup-fake`), exit 0, no refusal |
| the same with `--api-key typed-v3-fake` | the hook's turn with the typed key, then the refusal | both requests with `Bearer typed-v3-fake`, exit 0 |
| the same in RPC | the hook's turn, then the hold warning | both requests to `/other/v1`, no warning |
| both keyed, no `defaultProvider` (`v3-otherlate-keyed(-trigger)`) | the refusal (after the hook's turn, when it triggers) | `/other/v1`, exit 0 (the post-`session_start` re-resolve is ambiguous; the gate judges the launch route) |
| `--model gpt-4o-mini`, `OPENAI_API_KEY`, `defaultProvider: late`, the hook triggers (`v3-builtin-openai-trigger`) | the hook's turn to `api.openai.com`, then the refusal | both turns to `api.openai.com` with the user's key, no refusal (exit 1 is the mock's chat-completions body on a Responses route) |
| TUI (pty): that launch with `late` keyed; `hi`, `/new`, `hi` (and `/reload`) | launch held: 0, 0, 0 | `hi` → `/other/v1`; after `/new` (or `/reload`) held: `✖ The launch model "m1" names provider 'late' …`, no request |
| the same, no `defaultProvider` (control) | `/other/v1` both times | the same |
| (B2) TUI `--model late/m1`, the hook triggers a turn on rebuilds only; `/new`, `/reload`, `hi` | 0 throughout; with the factory's rebuild hold removed: `/new` 0, `/reload` 2 to `/late/v1` | 0 throughout; with it removed (sabotage on a throwaway worktree of round 4): `/new` 1, `/reload` 2 to `/late/v1` |
| (B3) `--model newlab/x --api-key typed-g2-fake`, a hook registering an unrelated provider | `Bearer typed-g2-fake`; with the post-decision attach removed: `Bearer or-own-fake` | the same (and the same under that sabotage) — now pinned by a test. Since #370 this launch is not found (exit 1, no key, nothing sent); the post-decision attach is pinned by an auth-only `emptyext` from `setup()` (`--model emptyext/x`, attached to `emptyext`) |

Round 5 (verify round 4 of `76055424`), each row reproduced on `76055424` with the
verifier's own probes first (copied to `/tmp/367-work/fix5/`, `extra-base/` and `gaps/`
runs labelled `base`; the real `main_sync`, every HTTP request recorded by an
`httpx.MockTransport`). `late` is registered in `session_start` (keyed), `other` in
`setup()` (keyed):

| Row | `76055424` | round 5 |
| --- | --- | --- |
| (B1) `--model late/m1`, the hook `set_model(late/m1)` then `trigger_turn`; print / json / own OR key or none / `--api-key` (`x-set-trigger-*`) | the hook's turn to `/late/v1` (`Bearer late-fake`), then exit 1, "… No prompt was sent." | requests=0, exit 1, the refusal (now true) |
| the same in RPC (`x-set-trigger-rpc`) | the hook's turn to `/late/v1`, then the hold warning | requests=0 (the `prompt` too), the hold warning |
| the hook `set_model(other/m1)` then `trigger_turn`; print / RPC (`x-setother-trigger-*`) | the hook's turn to `/other/v1` (`Bearer other-fake`), then the refusal / the warning | requests=0, the refusal / the warning |
| TUI (pty): `--model late/m1`, the hook `set_model` + `trigger_turn` on rebuilds only; `hi`, `/new` (or `/reload`), `hi` | `/new` (`/reload`): one request to `/late/v1` (`set_model(other)`: `/other/v1`) | 0 throughout |
| the same after a registered launch on `other` (settings `defaultProvider: late`, `--model m1`) | `hi` → `/other/v1`; `/new` → `/late/v1` | `hi` → `/other/v1`; `/new` → 0, held |
| the same, then `/model late/m1`, `hi` | — | `/late/v1` (the gate is lifted after the re-hold) |
| `--model newlab/x` (not late), the hook `set_model(late/m1)` then `trigger_turn`; print / RPC | two requests to `/late/v1` (the hook's turn during `session_start`, then `hi`) | one, carrying `hook-prompt` and `hi` (the handler's model stands; its turn waited) |
| (B2a) `register` only in a rebuild's `session_start`, `--model late/m1` (OR); `hi`, `/new`, `hi`, `/new`, `hi` (`g1-late-on-rebuild`) | held after the first `/new`; with `_settle_late_route` not recording: the second `/new`'s `hi` → `/late/v1` | the same; that sabotage now fails a test |
| (B2b) `other` keyless (`setup()`), `late` keyed, settings `defaultProvider: other`, `--model m1`; `/agents use plainprof` (or `--none`), `hi` (`g3-use-second-ask`) | held; without the second ask: `hi` → `/late/v1` | the same; pinned |
| (B2c) #344's `/reload` row, the provider first registered in `setup()` after `/reload` (`g2-reload-setup`) | `/extprov/v1`; without the end-of-build snapshot: held, NO REQUEST | the same; pinned through the whole launch |
| (B2d) `--model newlab/x`, the hook `set_model(late/m1)` (`v3-set-notlate`) | `/late/v1`; with the restore overwriting the handler's model: `/or/v1 … newlab/x` | the same; pinned |
| registered launches whose `session_start` triggers turns (`v3-otherlate-trigger-default(-json, -typed, -rpc)`, `v3-otherlate-keyed-trigger`; TUI `v4-registered-trigger-then-new`) | the hook's turn and `hi` to `/other/v1` | the same (not gated) |
| every other row of the verifier's and Codex's probes (`live_probe.py`, 59 rows; `extra_probe.py`'s other rows; `tui_probe.py`, 12; `tui_probe4.py`, 4) | — | identical (rc, every request, the refusal line) |

Round 6 (verify round 5 of `a543754c`; the verifier's probes copied to
`/tmp/367-work/fix6/`, the real `main_sync` with every HTTP request recorded by an
`httpx.MockTransport`; `late` registered in `session_start`, keyed; the pty rows drive the
TUI at 120x40):

| Row | `a543754c` | round 6 |
| --- | --- | --- |
| (B1) `--model late/m1` (OR key), a `model_select` handler answering the placeholder with `set_model(late/m1)`; RPC `get_state`, `prompt` (`o-ms-handler-rpc`) | the Warning "No prompt will be sent for it", then the prompt to `/late/v1` (`Bearer late-fake`) | requests=0; `get_state` `late/m1 api=unknown`; the prompt refused; the Warning true |
| the same, TUI in a pty, OR key / none (`v5-ms-launch`, `-noor`) | `hi` → `/late/v1` | `hi` → no request, the hold's refusal |
| the same, print (`o-ms-handler-print`) | exit 1, the refusal, no request | the same |
| (B2) a held rebuild: the hook `set_model(late/m1)` on rebuilds and a `model_select` handler answering the placeholder with `set_model(late/m1)`; `hi`, `/new`, `hi` (`v5-msr-rebuild`) | no request; with the settle's check removed: `/new`, `hi` → `/late/v1` | no request; that mutation now fails a test |
| a `model_select` handler that answers the launch placeholder with `set_model(sessext/m1)` and triggers a turn there (test row) | — | held, no request; with the gate lifted before the hold is applied, that turn went to `sessext` |
| a handler that triggers a turn and awaits its `agent_end` (no timeout), `--model newlab/x` (not late), print (`o-await-forever-notlate`) | hangs: no output, killed at 18 s (5dee21d1 and 76055424 complete) | rc 0, `WAIT done`, one request to `/or/v1` (`newlab/x`), carrying the handler's message and `hi` |
| the same with a 4 s timeout (`o-await-agent-end-notlate`) | `WAIT timed out`, then the request | `WAIT done`, then the request |
| a task a `session_start` handler spawns sets `late/m1` and triggers a turn after the emit returned; RPC (`o-task-set-trigger-rpc`) | the turn to `/late/v1` after the Warning | the same — the extension's own action after the decision (§6) |
| the other 15 `own_probe.py` rows; the pty rows `g1`–`g6`, `h1`, `h2`, `v5-msr-rebuild`, `v5-type-during-rebuild` (17); `live_probe.py` (59) and `extra_probe.py` (12) with each request's user texts; Codex's `tui_probe.py` (12) and `tui_probe4.py` (4); round 2's pty shapes (23) | — | identical |
| TUI (pty) `h1-held-new-set-late`: after `/new` | no request, nothing shown for the hook's turn | no request; the hook's refused turn shows `✖ No turn runs while this session's session_start handlers run. The launch model "late/m1" names provider 'late', …` |

Round 7 (verify round 6 of `343e75cd`; this round's kit in `/tmp/367-work/fix7/`, copied
to `.omc/probes/367-live/fix7/`; the real CLI with every HTTP request recorded by an
`httpx.MockTransport`, `late` keyed, a `model_select` handler answering the placeholder
with `set_model(late/m1)`, `send_message(..., trigger_turn=True)` and a 50 ms yield; the
pty rows drive the TUI at 120x40):

| Row | `343e75cd` | round 7 |
| --- | --- | --- |
| (B1) `--agent orprof --model late/m1`, `/agents use --none`, `hi` (`v7-mst-agents-use-orprof`) | `/agents use --none` → `/late/v1/chat/completions m1 Bearer late-fake`, then the Note "No prompt will be sent for it" | no request; `✖ No turn runs while this session is put on hold. The launch model "late/m1" …`, then the Note (true) |
| (B1) `--model late/m1`, `/model other/m1`, `/agents use --none`, `hi` (`v7-mst-model-then-use`) | the same request at `/agents use --none` | no request |
| `late` registered on rebuilds only, `--model late/m1` (OR key): `/new`, `hi` (`v7-mst-rebuild-first`; the factory held nothing, so no gate) | `/new` → `/late/v1/chat/completions` | no request; the refused turn shown |
| the launch with the same handler (`v7-mst-launch`; corrected in round 8: that scenario's settle returned without applying a hold, so it measured no factory-held rebuild) | no request (gated by the caller) | the same. The factory-held path was measured by verify round 7 (`p03`–`p06`, `p22`: no request) and is pinned by the unit row `rebuild-held` of `test_a_model_select_handler_turn_while_a_hold_is_applied_sends_nothing` |
| a task a `session_start` handler spawned with no delay sets `late/m1` and triggers a turn; RPC (`o-task-set-trigger-rpc`) | the turn to `/late/v1` after the Warning | no request: the task acts inside the launch's `apply` (its `set_model` is undone by the check, which comes last; its trigger is refused) — with a 0.3 s delay it acts after the decision and both trees send it (§6) |
| verify5's `live_probe.py` (59) and the other 16 `own_probe.py` rows; Codex's `live_probe.py` (37) and `tui_probe.py` (9); the 21 other pty scenarios of verify5's `tdrive.py`; round 2's pty shapes (`drive.py`, 43, fixtures rebuilt); #362's launch matrix (46, `5dee21d1` vs round 7: A26–A28 only) | — | identical (rc, every request with its user texts) |

Round 8 (verify round 7 of `f3fd162c` and Codex pass 7; this round's kit in
`/tmp/367-work/fix8/`, copied to `.omc/probes/367-live/fix8/`; verify7's pty driver
`vdrive.py` and RPC driver `vrpc.py` and Codex's `live_probe.py`, the real CLI with every
HTTP request recorded by an `httpx.MockTransport`; `late` keyed in `session_start`,
`other` keyed in `setup()`, a `model_select` handler answering the placeholder with
`set_model(late/m1)`, a `trigger_turn` and a 50 ms yield unless noted):

| Row | `f3fd162c` | round 8 |
| --- | --- | --- |
| (V-B1) TUI launch `--model late/m1`, `hi` (`p01`, `-noor` `p02`) | no request; `✖ No turn runs while this session's session_start handlers run. The launch route is not decided yet: …` under the Warning | no request; `✖ No turn runs while this session is put on hold. The launch model "late/m1" names provider 'late', …` |
| (V-B1) a factory-held rebuild: `/new`, `/reload`, `/fork`, `/resume` (`p03`–`p06`), and after a registered launch (`p22`) | no request; `✖ No turn runs while this session's session_start handlers run. The launch model …` | no request; `✖ No turn runs while this session is put on hold. …` |
| (V-B1) RPC launch `get_state`, `prompt`, `new_session`, `clone` (`vrpc.py` `r1`, `r2`, `r4`, `r5`) | no request; the handler's refused turn `[error] No turn runs while this session's session_start handlers run. …` | no request, the same states; `[error] No turn runs while this session is put on hold. …` |
| RPC `r3-rebuild-first` (the rebuild whose `session_start` first registered the provider) | no request; the refused turn already `[error] No turn runs while this session is put on hold. …` (the gate `LateRouteHold.apply` puts up itself there, since round 7) | the same |
| a `session_start` handler that triggers a turn, no `model_select` action (`p23`, `p23b`) | `✖ No turn runs while this session's session_start handlers run. The launch route is not decided yet: …` | the same (true there: `session_start` is running) |
| (C-P2) the `session_start` handler triggers a turn and awaits it; that turn's `input` (or `before_agent_start`) handler registers `sessext`; `--model sessext/m1`, print / json / RPC / TUI | `REGISTERED_IN input`; print/json exit 1 "… which an extension registered in a session_start handler …", RPC/TUI held; no request | the same, "… which an extension registered while session_start handlers ran …"; no request |
| (C-P3) `other` in `setup()`, `set_model(other/m1)` in `session_start`, `--model newlab/x -p hi` | the request to `/EXT/v1`; `Note: Model "newlab/x" is not in this build's catalog; sending it to OpenRouter as written.` | the request to `/EXT/v1`; no Note |
| every other pty scenario of `vdrive.py` (32 in all, incl. the controls `c01`–`c07`) and `vrpc.py` (5) | — | every request identical; text differs only in the refused-turn reason above and the late-provider wording |
| #362's launch matrix (46, `5dee21d1` vs round 8) | — | A26–A28 only (EXT request → no request, the refusal); the guard-2 `Note:` (3 rows) and custom-id `Warning:` (7) lines identical |

Round 9 (verify round 8 of `29499345`: no request anywhere, two blocking items; this
round's kit in `/tmp/367-work/fix9/`, copied to `.omc/probes/367-live/fix9/`; `trace9.py`
(from verify8's `cp2_timing_trace*.py`) and verify8's `cp3_probe.py` run the real CLI with
every HTTP request recorded by an `httpx.MockTransport`, `EMIT_START` / `EMIT_DONE`
traced around the `session_start` emit; `--model sessext/m1` with an OpenRouter key):

| Row | `29499345` | round 9 |
| --- | --- | --- |
| (B1) the `session_start` handler triggers a turn and returns at once; that refused turn's `input` / `before_agent_start` handler registers `sessext` (`ff-input`, `ff-bas`), print and RPC | `EMIT_DONE session_start` then `REGISTERED_IN input`; exit 1 (RPC held), no request; "… which an extension registered while session_start handlers ran …" — false: they had returned | the same order, exit 1 (RPC held), no request; "… which an extension registered while a session was starting (for example in a session_start handler), after the launch model was chosen. …" |
| (B1) a task the handler spawned with no delay (`task0`); a task `setup()` spawned that registers once `session_start` fired (`setup-task`); print and RPC | `EMIT_DONE` then `REGISTERED_IN task` / `setup-task`; refused, no request; the round-8 text (false) | the same; the round-9 text |
| the handler awaits its trigger (`await-input`) or registers itself (`ss-direct`) | `REGISTERED_IN` before `EMIT_DONE`; refused; the round-8 text (true there) | the same; the round-9 text (true too) |
| (B2) `set_model(openrouter/vendor/other-model)` with `--model newlab/x`, `set_model(anthropic/claude-other-1)` with `--model anthropic/claude-new-9` (same provider, another id), `set_model(other/newlab/x)`, `set_model(other/claude-new-9)` (same id, another provider) | the request where the handler moved it; no `Note:` / `Warning:` | identical; now pinned: a check of the provider alone, or of the id alone, fails two rows each (before, both passed all 336) |
| `LateRoute.landing_reason`'s fallback (an automatic pick lands on a late provider before any hold was made) | the round-8 text, no row | the round-9 text, pinned by a row |
| fix8's pty driver `vdrive.py` (all 32 scenarios) and RPC driver `vrpc.py` (`r1`–`r5`), `29499345` vs round 9 | — | requests identical in 32 of 32 and 5 of 5 (`cmp9.py`); in the full captures every message kind occurs as often on both trees, the late text in round 9's wording wherever `29499345` printed round 8's (35 of 37 runs; none left in round 9's captures; `cmp9count.py`) |
| #362's launch matrix (46, `5dee21d1` vs round 9) | — | A26–A28 only (EXT request → no request, the refusal in round 9's wording); every row's hits and exit code as in round 8's run; the guard-2 `Note:` (3 rows) and custom-id `Warning:` (7) lines identical |

### 2.12 Messages

- ambiguity (pi `:497-501`): `Model "<s>" is ambiguous across providers: <sorted
  provider/id, …>. <No matching provider is authenticated. | More than one matching
  provider is authenticated.> Use --provider or provider/model.` — with
  ` (<NAMES> came from a project .env, which does not choose between providers.)` before
  `Use` when a `.env` name is why a match counted as configured;
- not found (pi `:599-605`): `Model "<s>" not found. Use --list-models to see available
  models.`, plus why a `.env` OpenRouter key did not send it to OpenRouter; under
  `--api-key` every slashed string whose segments are non-empty (guard 2's shape, §2.3
  clause 1 — `newlab/model-x`, `newlab/org/model-x`) instead gets ` (--api-key does not
  send an id this build does not know to OpenRouter; to send it there with that key, use
  --model openrouter/<s> or --provider openrouter --model <s>.)` — whether or not the user
  holds an OpenRouter credential (the typed key may be one; the text says nothing about
  which credentials exist; #370). A string with an empty segment (`newlab//model-x`,
  `newlab/model-x/`, `/newlab/model-x`, `/`) gets the bare text: it cannot be an OpenRouter
  id, so naming OpenRouter's routes for it would mislead (#370 round 2, Codex's second
  category; pinned by `test_a_string_with_an_empty_segment_gets_the_bare_not_found` and
  the `empty-segment-*` resolver rows);
- after either, when step 2 set settings `defaultProvider` aside (§2.1): ` Settings
  defaultProvider "<p>" was not used: no credential of your own authenticates it, and a
  project .aelix/settings.json can set it (<NAMES> came from a project .env).`;
- custom id (pi `:593-594`): `Model "<id>" not found for provider "<p>". Using custom model
  id.`, plus, in (5), why a raw match on another provider was not taken;
- that hint (`"<s>" is also <p>'s model id, but <NAMES> came from a project .env, which
  does not choose a route; use <p>/<s> or export the key to send it there.`), in (4) and
  (5), names only a provider OTHER than the one the route took. The fourth verification
  measured `--model openrouter/auto` (built-in or re-pointed `openrouter`, the key in the
  `.env`) going to OpenRouter on that key while the hint offered
  `openrouter/openrouter/auto` "to send it there" (verify4 `live/f6f7_launch.out`, L-F6 and
  L-F7); a candidate on the provider taken is now skipped;
- guard 2: `Model "<s>" is not in this build's catalog; sending it to OpenRouter as
  written.`, or, in (4), that the user holds no credential of their own for the vendor.

## 3. Before and after

The launch rows are §1's table (both columns measured through the real CLI).

The TUI in a pty (120×40, fake keys, `/tmp/362-work/impl/live/drive.py`, captures under
`tui/`; row (e): `/tmp/362-work/fix2/live/drive.py`, `tui/{base,fix}.c1-model-on-current.*`;
the visual verdict is the main loop's):

| row | `aa026d08` | this change |
| --- | --- | --- |
| (a) `--model openai/gpt-4o-mini`, OR + OpenAI exported | (not captured) | banner `model: gpt-4o-mini`, `baseurl: https://api.openai.com/v1`, footer `✱ gpt-4o-mini`; `hi` → `CONNECT api.openai.com:443` |
| (b) `--model newlab/model-x`, OR exported | (not captured) | `Note: Model "newlab/model-x" is not in this build's catalog; sending it to OpenRouter as written.` above the banner, `baseurl: https://openrouter.ai/api/v1`, footer `✱ newlab/model-x`; `hi` → `CONNECT openrouter.ai:443` |
| (c) #362: `--model anthropic/claude-haiku-4-5`, ANT exported, OR in the cwd `.env` | banner `model: anthropic/claude-haiku-4-5`, `baseurl: https://openrouter.ai/api/v1`; `hi` → `CONNECT openrouter.ai:443` | banner `model: claude-haiku-4-5`, `baseurl: https://api.anthropic.com`; `hi` → `CONNECT api.anthropic.com:443` |
| (d) `/model openai/gpt-4o-mini`, OR exported, OPENAI in the cwd `.env` | `model → gpt-4o-mini (openai)`, persisted `openai`; `hi` → `CONNECT api.openai.com:443` (the planted key) | `model → openai/gpt-4o-mini (openrouter)`, persisted `openrouter`; `hi` → `CONNECT openrouter.ai:443` |
| (e) Codex C1: launched `--provider openai --model gpt-4o-mini` (the session ON openai, only the `.env` key), then `/model openai/gpt-4o-mini`; `854bf319` in the left column | `model → gpt-4o-mini (openai)`, persisted `openai`; `hi` → `CONNECT api.openai.com:443` | `model → openai/gpt-4o-mini (openrouter)`, persisted `openrouter`; `hi` → `CONNECT openrouter.ai:443` |

Real calls (the owner's OpenRouter key from the repo `.env`, never printed; an isolated
agent dir; `/tmp/362-work/impl/live/real.py`, output `real.out`):

| row | input | answer |
| --- | --- | --- |
| R1 | `--model anthropic/claude-haiku-4.5`, the key exported | `provider=openrouter model=anthropic/claude-haiku-4.5`, `pong` |
| R2 | `--model openrouter/auto`, the key exported | `model=auto`, OpenRouter `400 Reasoning is mandatory for this endpoint and cannot be disabled.` — **the same 400 on `aa026d08`**, which sent `openrouter/auto` (`real.base-auto.out`): OpenRouter takes either id; the refusal is the auto router's reasoning requirement, a pre-existing follow-up |
| R3 | `--model openai/gpt-4o-mini`, nothing exported, cwd `.env` = the repo's | refused before any request: the declined-swap Warning, then `No API key found for OpenAI.` |
| R4 | `--model openrouter/openai/gpt-4o-mini`, same cwd | `provider=openrouter model=openai/gpt-4o-mini`, `pong` — the `.env` key authenticates the explicit route |

## 4. Tests and sabotage

New tests (fake keys, isolated agent dirs, no network; the real-spawn rows use a local
CONNECT recorder, `tests/route_wire.py`): the launch matrix of §1 plus the critique's rows at
the resolver (`test_route_follows_pi_362.py`, with pi's texts), the record
(`test_dotenv_provenance_362.py`, incl. the comma row and an emulated Windows `os.environ`),
`has_route_auth` per layer (`tests/model_registry/test_route_auth_362.py`), the launch's
refusals, warnings, `--api-key` and late path through `_async_main`
(`test_launch_route_362.py`), `/agents use` and `/model`'s fallback
(`test_route_refusals_in_session_362.py`), the `/model` guard
(`tests/core/test_model_argument_guard_362.py`), the post-`/login` pick
(`tests/tui/test_post_login_pick_362.py`), and the child pin with two REAL children
(`tests/agents_ext/test_child_provenance_362.py`: the planted capture one process down, and
the critique's C1 on the wire). Copied onto a throwaway worktree at `aa026d08`: 104 of the
168 are red there; the 64 green are the rows whose route did not change (and the
regression guards against the design's own mistakes — C1, the widened guard 2, A27).
The review round brings the eight files to 186 (19 new or rewritten rows): 14 are red on
`d58cbb3e` — the `--api-key` rows at the resolver and through `_async_main` (the key
landed on `openrouter`), the not-found route that got the key on its prefix, the late
switch that attached it twice, the two unpinned-child rows — and 5 pin clauses that were
already true there, proved by sabotage below.
The cross-review round (§5): `tests/core/test_model_argument_guard_362.py` rewritten around
the invariant "with your own credential, the `.env` changes nothing" (every row resolved
without and then with the `.env`), C1 and C2 with host and bearer, the case rule in `/model`
(a credentialled built-in, a `.env`-only user-defined provider, a case clash, an unplaceable
id inside it) and the several-providers row; guard 2's scope
(`test_guard2_scope_is_the_openrouter_snapshot_not_every_catalogue`) and the record parser
(`test_a_malformed_inherited_record_yields_only_names`). 19 of the `/model` file's 35 are
red on `854bf319` (`/tmp/362-work/fix2/red_on_854.out`); its green ones are the unchanged
rows, the residual rows and the several-providers row, which a sabotage proves. Eight
sabotages of this round's code (`/tmp/362-work/fix2/sab/results.txt`): the route-aware pool
off, the current-provider exemption restored, the held set read from the scoped pool, the
`.env` hint off, the case rule off, its clash, its "not offered" and its "unplaceable"
refusals — all eight red. Guard 2's re-check of a user-defined prefix is removed (step 3
returns inside such a provider, so the first verification's sabotage of it stayed green);
`parse_record`'s name filter stays, with a docstring saying why no launch row reaches it.
Round 3 (the round-2 verification of `ecb4e0bc`): the row that pinned "a provider you
defined but cannot use is refused" is rewritten to the corrected subject (it keeps its
prefix on the `.env` key, never crossing to OpenRouter); new rows — the `ud_env_key` probe
with host and bearer and the launch's answer beside it, a re-pointed built-in keyed from the
`.env`, a bare id still refused, the
allow-list's "not offered" refusal, an `--api-key`-only session with a `.env` vendor key, an
`--api-key` session with another own key, and the late switch through `_async_main` with
the key in the `.env`. On a throwaway worktree at `ecb4e0bc` the B1 rows are red (5 failed,
61 passed over the two files, `/tmp/362-work/fix3/red_on_ecb4.out`); the `--api-key` rows
pin behaviour that already held and are red under the verification's sabotages X14 and X12
respectively (`/tmp/362-work/fix3/sab/results.txt`: 16 sabotages, 16 red, incl. X16 "B1
reverted" and X15 "spare every user-defined provider", which the bare-id row catches).
Round 4 (Codex's second cross-review of `a0edf615`, §5): rows for each finding —
F1 the saved default in the post-`/login` view (with and without the `.env`, plus a default
the view does offer), F2 the RPC rotation (with and without the `.env`, a rotation left with
one model, the held-credential case) and `set_model` pinned as it is, F3 a credential of the
user's own with no model rows (an `auth.json` key in `/model`, the view and RPC; a runtime
override and a models.json literal with `models: []`), F5 the scoped-out listed id's text,
F6 the re-pointed `openrouter` with the launch's answer asserted equal, and F7's three
`openrouter/<id>` rows in the `/model` matrix (each also in the with-and-without invariant).
On a throwaway worktree at `a0edf615` with the files copied in
(`/tmp/362-work/fix4/red_on_a0edf615.out`): 12 failed, 58 passed — the F1, F2, F3 (`.env`
side), F5 and F6 rows; the F3 `without .env` row fails there only on the new
`holds_route_auth` import, and the F7 rows, the `set_model` pin, the offered saved default
and the `without .env` halves pin behaviour that already held. Six sabotages of this
round's fixes on a worktree of the tree (`/tmp/362-work/fix4/sab/results.txt`): the view's
`find` forwarding to the registry (2 red), the rotation over raw `get_available()` (3), the
held set read off model rows again (6), the old owned-branch text (1), the owner rule
without the re-pointed `openrouter` (1), and Codex's F7 mutant (6) — six of six red.
Round 5 (the fourth verification, of `001ef77d`, `/tmp/362-work/verify4/`; this round's kit
`/tmp/362-work/fix5/`). It found the settings-`defaultProvider` hole (§2.1 step 2), a
call site no test pinned, two candidate sources pinned only through each other, and a hint
offering the route already taken (§2.12). Rows: the step-2 matrix at the resolver, each row
with and without the `.env` (`test_settings_default_provider_counts_only_for_your_own_credential_or_endpoint`
— S4, S12, S13 refused; a default the user's own key authenticates and one naming the
user's models.json provider or re-pointed `openrouter` still honoured; the no-credential
residual), the refusal's text, A42 rewritten (SUBJECT CHANGED: now a refusal), the
`test_runtime_bootstrap.py` home row rewritten, a REAL child started in the project
(`test_a_real_child_does_not_let_a_project_default_provider_choose_its_route`), the
post-`/login` call site through the real `run_tui` and `/login`
(`test_the_login_command_picks_through_the_route_auth_view`: the picked provider, its host
and the bearer, with and without the `.env`), one `route_auth_candidates` row per source on
its own (an `auth.json` key for a provider nothing else names; an `oauth`-only registration
— a registration with an `api_key` is also a request config, so only the `oauth` one is the
registered-provider source's alone), and the hint rows. On a throwaway worktree at
`001ef77d` with the files copied in (`/tmp/362-work/fix5/red_on_001ef77d.out`): 13 failed,
25 passed of the selected rows — the S4/S12/S13 halves, A42, the text, the home row, both
child halves (`api.anthropic.com` with the `.env`; `No API key found for Anthropic.`
without) and both hint rows; the honoured rows, the residual, the `/login` and per-source
rows pin behaviour that already held and are proved by sabotage. Ten sabotages on a
worktree of this tree (`/tmp/362-work/fix5/sab/results2.txt`, baseline 792 passed): the
default always counting (11 red), the own-endpoint clause off (4), the route-auth clause off
(3), the re-pointed `openrouter` not an endpoint (2), the residual off (3), only the
tie-break arm guarded (4), the verification's V1c — the call site handing
`find_initial_model` the raw registry (2), V3c — candidates without `auth.json` (1), V3d —
without registered providers (1), and the hint reverted (2) — ten of ten red. Under Python
3.11.15 (a throwaway venv, `/tmp/362-work/fix5/py311.out`) the nine #362 files: 273 passed
(the verification had measured round 4's five files there: 89 passed in 2.42s).
`tests/conftest.py` scrubs `AELIX_DOTENV_ADMITTED` around every test (the loader writes the
real `os.environ`; without it the design measured 27 cross-test failures).

Rewritten because their subject changed (each says so): in
`test_provider_prefix_rung.py` the dual-key `openai/gpt-4o-mini` (now OpenAI), the
re-pointed OpenRouter's `openrouter/auto` (now `auto`), an extension taking over `openai`
(the bare id is now ambiguous, not OpenRouter's), the stale stored credential (now the
user's route), the catalogue walk's `openrouter/` row (stripped), the named provider's
`openai/m1` (stripped), and the settings-`defaultProvider` clash (held with the key too);
`test_runtime_bootstrap.py` / `test_entry_router.py`'s "default provider never disables the
OpenRouter env path" (now guard 2, slashed id); `test_dotenv_admission.py`'s config-arm row
for `OPENROUTER_DEFAULT_MODEL` (now refused); `test_child_route_344.py`'s gateway row (now
`--provider gw` without the key too); `test_launch_route_344.py`'s unregistered-provider row
(guard 2's Note); `tests/tui/test_commands.py`'s persistence row (stubs `resolve_route`).
`tests/pi_parity/` is untouched and green.

Sabotage (`/tmp/362-work/impl/sabotage/sabotage.py` on a copy of the final tree, each
restored by md5): 34 sabotages, one per clause and guard — guard 1 in `has_route_auth`, in
the registry-less path and for duck-typed registries; the record's union, its own branch,
the name-shape refusal and the Windows spelling; `OPENROUTER_DEFAULT_MODEL` back in the
arm; each of pi's steps (tie-break, default-provider clash hold, user-defined containment,
swap, canonical prefix, the (5) rule, the explicit strip, the `openrouter/` strip); each of
guard 2's four clauses and its widening; `OPENROUTER_BASE_URL` on explicit routes; the late
trigger and its held arm; the child pin (pin everything, drop the `--api-key` comparison);
the `/model` guard and its allow-list read; the post-`/login` view; the print/json refusal,
the launch warning, the `--api-key` early rule; `/agents use` and `/model`'s fallback
refusals. All 34 turn at least one named test red.

The review of `d58cbb3e` re-ran 42 one-line sabotages of its own; five route clauses and
one dead check stayed green. The review round added a row for each — rule U in step 2,
`OPENROUTER_DEFAULT_MODEL` on a key from any source, `_attach_api_key`'s empty-provider
return, the late path's "held and still not runnable" return, `/model`'s several-providers
check — and removed the dead check (guard 2's own inferred-provider test: its callers only
call it when that provider is not route-authenticated). Re-run on the final tree with five
new sabotages for the round (step 3b off, 3b off in the harness build only, the attach on
an unresolved route, the `--api-key` pin restored, 3b off in the early resolve): 45
sabotages, 41 red (`/tmp/362-work/fix/sab/results.txt`). Green: the review's SB42 (no
behaviour change); 3b in the early resolve (it only names a fallback provider the harness
model always supersedes); and two clauses that predate this round — the `openrouter`
exclusion in `user_defined_providers` and `_launch_shape`'s catalogue adoption (ADR-0249
S) — which stay green against `tests/cli`, `tests/model_registry`, `tests/agents_ext` and
`tests/core` too (`wide/`); a follow-up.

Codex's third pass (round 6, `/tmp/362-work/fix6/`) added 22 rows: C1 — `/model` with the
shipped `get_env_api_key` as the fallback resolver, host and bearer with and without the
`.env` (2), and the predicate's layer 6 (3: an answer equal to a `.env` value, no record, a
different value); C2 — an installed resolver holds (`/model` refusal and RPC rotation, with
and without the `.env`, 2), its cost (no resolver vs resolver installed, `.env`-only, 2) and
`has_fallback_resolver` (1); C3 — the launch (`_async_main`, the runnable gate: the user's
template re-pointing `openrouter` and a user-defined `mygw`, both refused naming the
`.env`; the same value exported reaches `repo-chosen.invalid`; Cloudflare ids from a `.env`
reach `gateway.ai.cloudflare.com`, 4), `/model` (the switch path, refused / switched, 2),
and `_base_url` (the withheld token, the exported value, Cloudflare from a `.env`, and the
template-name set — every upper-case catalogue token, each with a shape rule, 4); C5 — a
headers-only `openai` entry with a project `defaultProvider` of `openai` and
`brand-new-model`, refused with and without the `.env` (2). On a throwaway worktree at
`5e983992` with the files copied in (`/tmp/362-work/fix6/red_on_5e98.out`): 14 failed —
the behaviour reds are both C3 launch refusals, the C3 `/model` refusal, the C1 `/model`
"with .env" half (`assert not True`: `has_route_auth("openai")`), both C2 halves
(`holds_route_auth` False), the cost row's resolver half, the C1 predicate's equal-value
row (`(True, True) == (True, False)`); the rest fail on the new names
(`has_fallback_resolver`, `dotenv_withheld_placeholder_names`, `aelix_ai.dotenv_record`) -
among them the `_base_url` withheld row, which stops at its `ImportError` there and is red
on behaviour under the C3 sabotages - and pin behaviour that already held; the C5 rows are green there
and proved by the mutant. Four sabotages on a worktree of this round's tree
(`/tmp/362-work/fix6/sab/results.txt`; the `*_362.py` files plus `test_base_url.py`,
baseline 305 passed): C1's equality check reverted (2 red), C2's resolver rule reverted (4),
C3's placeholder rule reverted (4), Codex's C5 mutant (2, the `oai-tuned-home` halves) —
four of four red, each file md5-restored.

**#370 (2026-10-06).** `tests/cli/test_api_key_unknown_prefix_370.py` drives
`_async_main` with every request recorded: print/json with `--api-key` and `newlab/model-x`
(own OpenRouter key, none) and `x-ai/grok-4` exit 1 with the hinted not-found and attach
nothing; the late-gone extension the same; interactive and RPC hold the placeholder through
the first prompt, `/new` and `/reload` (the unknown prefix, the late-gone and the late
extension); `/agents use` refuses with the launch's text; the explicit routes and
`x-ai/grok-4.3` take the typed key to OpenRouter, and guard 2 without `--api-key` the
user's own. `test_route_follows_pi_362.py`'s `TYPED_KEY` rows (`unknown-prefix`, its
no-OpenRouter twin, `xai-unlisted`, `xai-listed`) pin the resolver, and #367's B3 row is
split: the guard-2 launch it pinned is now not found, and the post-decision attach is
pinned with an auth-only `emptyext` registered in `setup()`. On `62e2238b` with the files
copied in: 18 failed, 232 passed (`.omc/probes/370-live/impl/red-on-base.txt`). Four sabotages on
a throwaway worktree of the fix (four route files, 275 passed clean): the `typed_key`
condition on guard 2 removed (15 red), the hint removed (15), #367's post-decision attach
skipped (1, the `emptyext` row), `/agents use`'s `typed_key` removed (2) — each restored
byte-identical (`.omc/probes/370-live/impl/sabotage.txt`).

**#370 round 2 (2026-10-06).** The independent verification of round 1 passed (no
blocking finding); Codex's first pass reproduced no typed-key leak in 33 cases but found two
gaps. (1) Nested ids: a guard-2 condition that still fired for more than one slash
(`(not typed_key or model_flag.count("/") > 1) and _guard2(...)`) and a hint limited to one
slash each passed all 250 tests of the three route files (`.omc/probes/370-live/fix2/
mutants-on-07ff4dae.txt`); `newlab/org/model-x` rows now pin both — print/json (exit 1, the
hinted not-found, nothing attached or sent), interactive/RPC (held through the first
prompt, `/new` and `/reload`) and the resolver (`nested`). (2) Strings with an empty
segment get pi's bare not-found, decided and pinned (§2.12; one launch row,
`newlab//model-x`, and four `empty-segment-*` resolver rows). The verification's notes
moved §2.7: the settings routes that take the typed key to OpenRouter, the `/model`
fallback, the 163/32 split of the 195 ids and the `sgone` shape. And `/agents use`'s
`typed_key` is scoped to a re-resolve of the launch inputs (§2.7, §7 item 13): a
model-less profile row joins `--none` and the flag-beaten profile, and a new row pins a
profile's own `model: newlab/model-x` after a settings-pair `anthropic` launch with
`--api-key` — guard 2 on the user's own OpenRouter key, the typed key never sent there,
as on `62e2238b`. With the round-2 test files on `07ff4dae` only that row fails (1 failed,
261 passed); on `62e2238b`, 24 failed, 238 passed (`fix2/red-before.txt`). Sabotage on a
throwaway worktree (four route files plus `tests/agents`, 403 passed clean): the two nested
mutants (5 and 4 red), the hint given to empty-segment strings (5), `/agents use`'s scope
reverted to round 1's every resolve (1), removed as on `62e2238b` (3), inverted (4); round
1's guard-2 condition (21), hint (20) and post-decision attach (1) — each restored
md5-identical (`fix2/sabotage.txt`).

## 5. Cross-review

Codex on `854bf319` (effort high; transcript `/tmp/362-work/codex/review.out`, probes
`/tmp/362-work/codex/*.py`, all on `httpx.MockTransport` with fake keys). Clean: no way to
forge or clear `AELIX_DOTENV_ADMITTED` (`probe_controls.py`); guard 2 did not fire for a
`.env`-only OpenRouter key, a bare unknown id or `xai/grok-4`; no `--api-key` path attached
the key to another provider. Found, and what this round did:

- **C1** — `/model openai/gpt-4o-mini` on a session already on `openai`: a cwd `.env`
  `OPENAI_API_KEY` moved it from `openrouter.ai` to `api.openai.com` on the file's key.
  **Fixed** (§2.8: the route-aware pool, no current-provider exemption).
- **C2** — the same path took `openai/o1-pro` off a models.json `OpenAI` to
  `api.openai.com` on the `.env` key. **Fixed** (§2.8: the pool, and the case rule in
  `/model`).
- **C3** — a hatched `OPENROUTER_DEFAULT_MODEL` lets a `.env` pick the no-model route.
  **Kept and stated** (§2.6): the user opts in by name, in the shell.
- **C4** — guard 2 fires for `openai/o1-pro`, which OpenAI's catalogue lists. **Kept, scope
  stated** (§2.3): an id the OpenRouter snapshot does not list, under a namespace prefix
  whose vendor is not route-authenticated — a refinement of the owner's "an id in no
  catalogue" (§7 item 1).

The four probes re-run from this tree: C1 and C2 give the same host and bearer with and
without the `.env`; C3 and C4 are unchanged, by decision.

Codex again, on `a0edf615` (transcript `/tmp/362-work/codex2/review.out`, probes
`/tmp/362-work/codex2/probe_*.py`, `httpx.MockTransport`, fake keys). Clean: no untrusted
registration bypass (project models files and `.env` `AELIX_*` names define no provider;
an untrusted project extension does not load), no user-defined-provider crossing or
wrong-case destination, no key-value leak; the matrix probe (405 comparisons over case
variants, multiple slashes, uncatalogued ids, scopes and exported / stored / runtime
credentials) found nothing. Found, and what round 4 did (each probe re-run from this tree,
`/tmp/362-work/fix4/probes/before_*.out` → `after_*.out`):

- **F1 (P1)** — the post-`/login` view filtered `get_available()` but forwarded `find()`, so
  a project settings default picked a provider only a `.env` authenticated. **Fixed**
  (§2.10): `login-wire` with the `.env` `aiplatform.googleapis.com … project-vertex-fake` →
  `openrouter.ai … Bearer own-login-fake`, the same as without it.
- **F2 (P1)** — RPC `cycle_model` rotated over raw `get_available()`. **Fixed** (§2.8, RPC):
  `rpc-cycle` with the `.env` `["openai", "gpt-4"]` → `["openrouter",
  "ai21/jamba-large-1.7"]`, `Bearer own-or-fake`, as without it. `set_model` names the
  provider and is kept (`rpc-set` with the `.env` still `["openai", "gpt-4o-mini"]`).
- **F3 (P1)** — "the user holds a credential of their own" was read off model rows; an
  `auth.json` key for a provider with no models was invisible. **Fixed**
  (`holds_route_auth`, `ModelRegistry.route_auth_candidates`): `held-tui` with the `.env`
  `selected "openai", success true` (`api.openai.com … Bearer project-fake`) → `selected
  "unknown", success false`, as without it.
- **F4 (P2, availability)** — a late provider authenticated only by `--api-key` is refused.
  **Known, handed to #367** (§2.11); `late-typed` unchanged. **Closed by #367 (2026-10-03)**:
  the launch refuses every launch model only a `session_start` provider could serve, as pi
  does, so this input gets the same
  refusal and guidance as any other (`test_api_key_with_a_session_start_provider_is_refused_the_same_way`).
- **F5 (P2)** — the owned-branch refusal named the wrong cause. **Fixed** (§2.8):
  `custom-scoped-refusal` now "model 'MyGw/m2' is one 'MyGw' (a provider you defined) lists,
  but it is not offered here: /scoped-models excludes it, or it is not runnable in this
  environment. …".
- **F6 (P2)** — a re-pointed `openrouter` was not user-defined in `/model`. **Fixed** in
  `/model`'s case rule only (§2.8): `repoint-or` `selected null` → `["openrouter",
  "https://own-router.invalid/v1"]`; the launch gives the same host and bearer.
- **F7 (test gap)** — a mutant exempting `openrouter/` from guard 1 passed all 49 rows.
  **Pinned** (§2.8): three rows; `probe_mutant.py` now reports `6 failed`. `/model` stays
  stricter than the launch here; §7 item 10.

Codex a third time, on `5e983992` (transcript `/tmp/362-work/codex3/review.out`, report
`REPORT.md`, probes `/tmp/362-work/codex3/probe_*.py` on `httpx.MockTransport`, fake keys).
The second pass's login, RPC-cycle and model-less-credential repros are fixed; the settings
matrix (32 resolutions) and the in-session matrix (405 comparisons) found nothing; untrusted
project extensions and models files register no provider. Found, and what round 6 did (each
probe copied to `/tmp/362-work/fix6/probes`, re-pointed at this tree, `before_*.out` →
`after_*.out`):

- **C1 (P1, embedder)** — a fallback resolver laundered a `.env` key into "the user's own".
  **Fixed** (§2.2 layer 6): `env-fallback-wire` with the `.env` `api.openai.com
  /v1/responses Bearer repo-openai-fake` → `openrouter.ai /api/v1/chat/completions Bearer
  own-router-fake`, the same as without it.
- **C2 (P1, embedder)** — a provider only a resolver answers for is in no candidate set.
  **Fixed, failing toward the guard** (§2.8): `fallback-own` `held false` → `held true`;
  `fallback-model` with the `.env` `selected "openai"` (`api.openai.com … Bearer
  repo-openai-fake`) → refused naming the `.env`; `fallback-cycle` `selected "openai"` →
  `"unknown"`; both now as without the `.env`. Its cost is in §6.
- **C3 (P1)** — a `.env` value chose the host through a user's base-URL template. **Fixed**
  (§2.2, "never addresses"): `template-switch` with the `.env` `refusal null` then
  `repo-chosen.invalid /v1/chat/completions Bearer repo-router-fake` → the refusal "model
  'auto' needs configuration before it can run: set the environment variable(s) TENANT_KEY …
  TENANT_KEY came from a project .env, which may authenticate a request but never address
  one; export it in your shell to use it.", and no request; the launch and the TUI measured
  (§2.2).
- **C4 (stated residual)** — `--model gpt-realtime-2.1` (only `openai` serves it) on a `.env`
  `OPENAI_API_KEY` while `OPENROUTER_API_KEY` is exported. **No change**: §6's first bullet
  names it.
- **C5 (test gap)** — a mutant counting every non-`openrouter` models.json entry as the
  user's endpoint passed all 279 `*_362.py` rows. **Pinned** (§4): `oai-tuned-home`; under
  the mutant 2 failed.

**Codex's fourth pass** (of `139c72ca`, the delta `5e983992..139c72ca`;
`/tmp/362-work/codex4/REPORT.md`): no scheme, host, port or user-info redirection by a
`.env` URL value, no record forgery or loss across a delegated child, a nested shell or an
embedder, and no regression across 240 exported-variable comparisons and the 56
catalogue Cloudflare models. Found, and fixed by the main loop in the next amend:
- **F1 (P1, embedder)** - a resolver that transformed the `.env` value (`"namespace:" +
  value`, a suffix, `.strip()`, `bytes`, an `int`) still made it route-deciding. **Fixed**
  (§2.2 layer 6, `_derived_from_dotenv`); rows `prefixed`, `suffixed`, `padded`, `bytes`,
  `int`, and `short and unrelated` (containment needs 8 characters a side).
- **F2 (P2, Windows)** - the hatch-plus-case path to an unchecked Cloudflare value. **Fixed**
  (the reader re-checks the shape); rows for six bad values and the pattern's equality
  with `_CF_ID`.
- **F3 (P2)** - a `/` in a query or fragment read as the path. **Fixed** (`_path_span`);
  rows for a query or fragment with no path, and after a path.
- **F4 (test gap)** - an any/all slip in the equality check passed all 314 tests. **Pinned**:
  `test_every_recorded_value_is_checked_not_just_one`.
Sabotages on the fixed tree (`34612ef0`; later rows change these counts), each restored by
md5 after (`tests/providers/test_base_url.py` and `tests/model_registry/test_route_auth_362.py`,
baseline "57 passed"): F1 reverted to
exact equality "5 failed", Codex's F4 mutant "4 failed", F2's re-check dropped "6
failed", F3 reverted to the first `/` "4 failed".

**Codex's fifth pass** (of `34612ef0`, the delta `139c72ca..34612ef0`; probes and outputs
in `/tmp/362-work/codex5/` - its closing report was cut off by the provider's
content filter, so the main loop read the probe outputs directly): clean on zero-width
characters around the value, `list`/`tuple` answers and a plain `str` subclass; found
a `str` subclass whose own `strip()` hid an equal answer and a case-changed answer
(both now refused: builtin strip, case-folding), the stated short-value limit
(`OPENAI_API_KEY=repo` prefixed), and three mutants passing all 333 rows - the path
ending at the LAST `?`/`#`, the shape checked on the stripped value, containment with
one side's length - each now red ("1 failed" apiece; the case/subclass sabotage "2
failed") on rows `query-then-fragment`, the `" acct1 "` value, and
`test_a_short_dotenv_value_inside_an_own_answer_does_not_refuse_it`. Two of its probe rows
still route and fall under the stated encoding limit: a zero-width character INSIDE the
value and a Cyrillic look-alike.

**An independent check of the fifth pass's fixes** (fresh-context opus, of `f3c898f1`,
`/tmp/362-work/verify8/`): the code holds every rule - fallback fuzz `{"cases": 6000,
"mismatches": 0}`, placeholders `{"rows": 2720, "mismatches_vs_rule_reference": 0}`,
path spans `{"templates": 50000, "span_mismatches": 0}`, no regression across 4032
exported cases and the 336 catalogue Cloudflare cases - but found three test gaps, one
mis-read template and one over-claim, closed in the final amends: casefold dropped on the
RECORDED side only
(a mixed-case planted value, `test_a_mixed_case_dotenv_value_is_case_folded_too`), a
"check only the first recorded value" slip that the hash seed could hide (the F4 test now
plants the value under each name), the template-name condition (a `.env` `TENANT_KEY` with
a valid shape), and `://` inside a query read as a scheme (the scheme must open the
template, `_SCHEME_RE`); the guide and CHANGELOG now state the 8-character floor. Each
sabotage red ("1 failed"; the first-value slip red at `PYTHONHASHSEED` 0, 1, 2 and 3).

Codex's earlier probes re-run from this tree give round 5's answers (`/tmp/362-work/fix6/
earlier/after_*.out` against `/tmp/362-work/fix5/probes/after_*.out`, identical outside
paths and timings): `probe_login`, `probe_edges`, `probe_matrix` (`{"comparisons": 405,
"bad": []}`), `probe_model_simple`, `probe_model_mock`; `probe_mutant` the same six F7 rows
red ("6 failed, 73 passed": the file gained this round's eight rows).

## 6. What this does not close

- **A user with no credential of their own for the chosen route** still sends under a
  `.env` key — ADR-0203 residual risk 1, narrowed: `anthropic/claude-haiku-4.5` with only a
  planted `.env` `OPENROUTER_API_KEY` goes to that account (pi's first raw match, A36;
  nothing the user holds offers another route). The same holds when the user holds a key
  of their own for a DIFFERENT route but the string has only one: `--model
  gpt-realtime-2.1` (only `openai` serves it) with `OPENROUTER_API_KEY` exported and
  `OPENAI_API_KEY` only in a project `.env` makes no request without the `.env` and goes
  to `api.openai.com` on the repo's key with it — also through an agent profile's
  `model: gpt-realtime-2.1` (Codex's third pass, C4: `probe_settings.py`,
  `probe_children.py`). pi's step 2 one-hit rule picks the only provider, and the `.env`
  key then authenticates the route the string chose; no credential of the user's own is
  for that route. `/model gpt-realtime-2.1` in the same session refuses (the `.env`-only
  provider leaves the pool, §2.8; measured with and without the `.env`,
  `/tmp/362-work/fix6/probes/after_c4_model.out`). The `Notice: loaded credentials …` line
  is the signal.
- **An embedder that installs a fallback resolver and holds only `.env` credentials** gets
  the strict guard, not the residual above, even when the resolver answers nothing: the
  installed resolver counts as a credential of the user's own (§2.8, C2), so a `.env`-only
  provider is set aside everywhere a route is decided - `/model openai/gpt-4o-mini` and a
  bare `/model gpt-4o-mini` refuse naming the `.env`, RPC `cycle_model` rotates to nothing,
  and the launch's settings `defaultProvider` no longer homes a bare id (`brand-new-model`
  under `openai` ends "not found ... Settings defaultProvider "openai" was not used"); an
  explicit `--model openai/gpt-4o-mini` still runs on the `.env` key (verify round on round
  6, `v6_resolver.out`). The refusals say "while you hold a credential of your own", which
  there means the installed resolver. The stock CLI installs no resolver.
- **A `.env` value in a base-URL placeholder** fills it only for ADR-0203's template
  configuration (the two Cloudflare ids, §2.2), and only in the path; a user whose own
  template reads another variable from a project `.env` - or one admitted through
  `AELIX_DOTENV_ALLOW`, or a Cloudflare id in the host - must export it; the refusal says so.
- **`.env`-only users lose the routes a `.env` key used to choose**: with the OpenRouter
  key only in a project `.env`, `openai/gpt-4o-mini` is OpenAI's (refused for want of a
  key, with a hint naming `openrouter/openai/gpt-4o-mini`) and an uncatalogued
  `newlab/model-x` is not found. `openrouter/<id>`, `--provider openrouter` or exporting
  the key are the routes (the owner's own repo workflow; §7).
- **`/model` in a session whose only credentials came from a `.env`** still resolves over
  them (§2.8) — nothing of the user's own competes, so it is the residual above.
- **A bare id only a `.env`-authenticated user-defined provider serves** is refused by
  `/model` while the user holds an own key elsewhere, and taken by `--model` (pi's step 2:
  one hit wins, no auth check). The two answers differ; neither lets the `.env` choose
  between destinations, and `<provider>/<id>` works in both.
- **An extension that registers a built-in name with models** (`register_provider("openai",
  models={"m1": …}, api_key="OPENAI_API_KEY")`): `--model openai/gpt-4o-mini` stays inside
  the registration (a custom id at the extension's host), while `/model`'s owned pool is
  every `openai` model, the catalogue's included, so it takes the catalogue's id at
  `api.openai.com`. The split predates this ADR (`ecb4e0bc` gives the same two hosts with no
  `.env`) and guard 1 holds there — the host is the same with and without the `.env` — but
  since round 3 a `.env` `OPENAI_API_KEY` authenticates `/model`'s answer where `ecb4e0bc`
  refused it. pi's `registerProvider` with models replaces the provider's models, so the
  catalogue id resolves in neither. #365 (the round-3 verification's `probe_hijack.py`).
- **The launch's settings default pair from a project `.aelix/settings.json`** is seeded as
  an explicit `--provider`/`--model` pair (step E), so with no `--model` a cloned repo's
  settings naming `openai/gpt-4o-mini` plus its `.env` `OPENAI_API_KEY` send the prompt to
  `api.openai.com` on the file's key while the user's own `OPENROUTER_API_KEY` is exported
  (measured in round 4, `/tmp/362-work/fix4/probes/sweep_launch.out`: `sweep-project-True`
  `api.openai.com … Bearer project-fake`; without the `.env`, `No API key found for
  OpenAI.`). The post-`/login` pick no longer honours such a default (§2.10); the launch
  does, because the pair "rightly behaves like an explicit choice" when the user wrote it —
  and the file does not say who wrote it. Telling the project's pair from the global one is a
  launch-semantics change (the pair also seeds `/agents use`'s baseline and every rebuild),
  so it is put to the owner (§7 item 11) rather than made here. It predates #362: the
  fourth verification measured the same row on `21db8c8a` (verify4
  `live/sweep_base_21db8c8a.out`: `S1 project pair openai/gpt-4o-mini | OR sh + OPENAI .env
  | no --model => PROXY CONNECT api.openai.com:443`; without the `.env`, S2, `No API key
  found for OpenAI.`). The same verification saw it LAUNDERED: in a session holding no
  credential of its own, the post-`/login` pick (§2.10; the view offers everything there)
  took the project's pair and `/model`'s persistence wrote `defaultProvider "openai"`,
  `defaultModel "gpt-4o-mini"` into the GLOBAL settings (verify4 `live/drive.py f1-login`,
  first run, `tmp/tui-f1-login-hm7ei5ly/settings.json`) — after which every launch anywhere
  reads the repo's pair as the user's own explicit choice. Step 2's `defaultProvider` (a
  provider without its model) is closed (§2.1); the pair is not. pi never reaches this
  shape for an untrusted directory: since `89a92207f` (2026-06-05, "add project trust
  gating") its `SettingsManager` reads project settings only when the project is trusted
  (`settings-manager.ts` `fromStorageWithPaths` → `tryLoadFromStorage(storage, "project",
  projectTrusted)`; `setProjectTrusted(false)` empties them, @ `88ff80b98`), while aelix's
  reads `.aelix/settings.json` whatever the trust answer. Porting that is #369. Under
  `--api-key` the typed key rides such a route as well: a project file's
  `defaultProvider: openrouter` (homing a bare `--model model-x`) or its pair
  `openrouter` + `<id>` takes the typed key to `openrouter.ai`, with or without
  `--approve` (§2.7; #370 round 2, `.omc/probes/370-live/fix2/settings-routes.txt`) — not
  guard 2, and unchanged by #370.
- **A trusted project's agent profile naming `provider:`** is an explicit route (§2.1 E)
  that a `.env` key then authenticates: `--agent repo --approve` with a project
  `.aelix/agents/repo.md` carrying `provider: openai` and `model: gpt-4o-mini` reaches
  `api.openai.com` on the `.env` key while the user's own `OPENROUTER_API_KEY` is exported,
  on `21db8c8a` as here (verify5 `live/sweep_head.out`, `sweep_base.out`, row S10a; no
  request without the `.env`). Project trust gates the profile (an untrusted directory's
  is refused, S10/S11), so it sits inside what trusting the project grants.
- **Settings `defaultProvider` in a session holding no credential of its own** still breaks
  step 2's tie and homes a bare id (§2.1): with only a `.env` `OPENAI_API_KEY` and a project
  `{"defaultProvider": "openai"}`, `--model newmodel-x` goes to `api.openai.com` as a custom
  id (round 5's R1 row) — nothing the user holds offers another route, the residual above.
- **RPC `set_model`** names its provider, so a `.env` key authenticates the route the
  client named (§2.8, pinned).
- **A held RPC session cannot leave the hold** (§2.11): `aelix --mode rpc` wires no model
  registry, so `set_model` and `cycle_model` refuse; the warning says to restart with
  another `--model`. Wiring the registry is a follow-up (pre-existing; #367's verify
  round 1, N1).
- **A `/model` to a `session_start` provider lasts until the next rebuild** and is saved
  as the default, so the next launch without `--model` is refused (§2.11) — aelix's
  rebuild re-derives the model from the launch inputs where pi keeps the session's.
- ~~**A late provider authenticated only by `--api-key`** is refused (§2.11, F4) — #367.~~
  Closed 2026-10-03: #367 refuses every launch model only a `session_start` provider
  could serve (§2.11).
- **D4's cost — a turn a `session_start` handler triggers on a pending launch, or in a
  held rebuild, does not run there** (§2.11): while those handlers run, turns are held
  (the turn gate), so `send_message(..., trigger_turn=True)` does not start one — whatever
  model a handler set first — even when the launch turns out not late and its route is
  put back afterwards. Since round 6 the trigger ends as a refused turn (its `agent_end`
  arrives, nothing is sent) and its message, that turn's prompt, goes out with the next
  prompt that is sent (measured, `--model newlab/x` with a triggering handler: 9e233be9
  sent the handler's turn and then `hi`; here one request, carrying `hook-prompt` and
  `hi`; with the handler's own `set_model` first, the one request goes to the model it
  set). An interactive session shows that refused turn like any failed one (`✖` and the
  gate's reason) when it happens in a rebuild; at launch it runs before the TUI starts,
  and the TUI shows it when it starts (`✖ No turn runs while this session's session_start
  handlers run. The launch route is not decided yet: …`, under the Warning; measured in
  round 8 with a `session_start` handler that triggers a turn and no `model_select`
  action — `.omc/probes/367-live/fix8/vprobe/` `p23-launch-ss-trigger` and
  `p23b-launch-ss-trigger-nosession`, as verify round 7's; round 7 cited fix7's
  `v7-mst-launch` here, whose `session_start` triggers nothing: the `✖` line it showed was
  a `model_select` handler's turn refused under the launch's gate with that stale reason,
  V-B1). A turn a `model_select` handler triggers while a hold is applied is refused the
  same way and its message also goes out with the next prompt that is sent; its `✖` line
  carries the hold's reason (`No turn runs while this session is put on hold. …`).
  A handler that waits for the session — or registers its provider in `setup()` — is
  unaffected; a launch on a registered provider is never pending and never gated. A
  handler reading `ctx.model` there sees the placeholder.
- **After the decision, a task a `session_start` handler spawned acts outside the hold.**
  The gate and the hold cover the handlers' run, the decision after it and every
  application of a hold (round 7); a task the
  handler started that, later, calls `set_model` onto the late provider and triggers a
  turn is the extension's own action in a running session, like an extension's
  `set_model` at any other time, and the turn is sent there — in a held interactive or
  RPC session too (measured, `o-task-set-trigger-rpc`: the Warning, then the hook's turn
  to `/late/v1`; print/json had already exited). So is an `input` or
  `before_agent_start` handler's `set_model` before a prompt the user sends in the held
  session (verify round 6 measured it). Stated, not changed.
- ~~**A handler that itself picks a model and triggers a turn inside `session_start`**
  runs that turn before the hold is re-applied.~~ Closed in round 5 (verify round 4, B1):
  measured, it was not only the extension's own request — print/json then said "No prompt
  was sent."; the turn gate holds it (§2.11).
- **The gate covers a pending launch and a HELD rebuild, not every rebuild whose route is
  pending.** A rebuild the factory does not hold (no late provider recorded yet) whose
  `session_start` registers a provider for the first time, `set_model`s onto it and
  triggers a turn sends that turn there; the after-`session_start` seam records the
  provider and holds the rebuild after it (`--model late/m1` with an OpenRouter key, the
  handler doing all three on rebuilds only: `/new` → one request to `/late/v1` on
  `76055424` and here, then held). Gating every guard-2 rebuild would also queue the
  `session_start` turns of sessions with no late provider at all; stated, not changed (an
  owner call). What aelix does after that `session_start` is gated: the seam's re-hold
  runs under the turn gate since round 7, so a `model_select` handler answering its
  placeholder cannot send a turn (`v7-mst-rebuild-first`, §2.11).
- **An RPC client gets no notice when a rebuild holds**: `new_session` and `clone` answer
  `success`, `get_state` then shows `api=unknown`, and each `prompt` fails on stderr with
  `[rpc] prompt task failed: StreamSimpleError("No provider registered for api='unknown' …")`
  (measured, a registered launch on `other` with settings `defaultProvider` naming the
  late provider). The client cannot leave that hold either (no model registry in `--mode
  rpc`, above).
- **An RPC `new_session` (or `clone`) is handled concurrently with the commands sent after
  it**, so a `get_state` sent before `new_session`'s response can show a transient model —
  the rebuild's model before its hold is re-applied (`other/m1` or `late/m1` with a real
  `api`; verify round 7's `vrpc.py` `r2-held-rebuild`, `r3-rebuild-first`) — and a `prompt`
  in that window is refused by the turn gate, nothing sent (verify round 7, the same rows).
  The window is timing-dependent: round 8's re-run of those rows on `f3fd162c` and on
  round 8 answered the held state (`late/m1 api=unknown`) at every `get_state`. A client
  that waits for `new_session`'s response before its next command sees the held state.
  Stated, not changed (pre-existing RPC dispatch: every command runs in a task of its own).
- **A launch that resolved to a registered provider runs there, and its rebuilds are held
  where they land late** (§2.11, B1) — e.g. `--model m1` served by a `setup()` provider,
  with a `session_start` provider serving `m1` too and settings `defaultProvider` naming
  it: the launch and its `session_start`'s turns run on the `setup()` provider, as in pi;
  `/new`, `/fork`, `/resume`, `/reload` and an implicit `/agents use` re-resolve the inputs
  onto the late provider and are held until `/model` picks one. pi keeps the session model
  across a new session; aelix's rebuild re-derives it from the launch inputs, so the
  session the user started on stops at its first rebuild. Stated, not changed here.
  (Round 3 refused the launch itself instead — after its `session_start` had run turns on
  it; withdrawn in round 4.)
- **A provider registered after aelix's check after `session_start`** — in a handler of
  a turn the user sends (`input`, `turn_start`, a tool handler, say), or by a task an
  extension started that registers only after that check (after a delay, say) — is outside the late definition (`LateRoute` records only what arrives
  between the end of a build and that check, §2.11), so a rebuild whose re-resolve lands
  on it switches there, as the rebuild of a launch whose inputs name a `setup()` provider
  does. Measured for a delayed spawned task (the verifier's `g6-deferred-register`,
  `--model late/m1` with an OpenRouter key: `hi` → `/or/v1`, `/new`, `hi` → `/late/v1`, on
  `76055424` and here); pi keeps the session model across a new session, so it would
  stay on OpenRouter. The other hooks by construction, not measured. Inside the window it
  is late whatever registered it (round 9, verify round 8, B1, corrected from round 8's
  wording here): at a pending launch a task a `session_start` handler spawned with no
  delay, and the `input` / `before_agent_start` handler of a turn a handler triggered
  fire-and-forget, register AFTER the emit returned — that refused turn runs after it,
  while aelix waits it out — and before the check, and are refused (`trace9.py`:
  `EMIT_DONE session_start` then `REGISTERED_IN task` / `input`, exit 1, no request).
- ~~**The guard-2 Note can be wrong after `session_start`**~~ — closed in round 8 (Codex
  pass 7, C-P3). `Note: Model "newlab/x" is not in this build's catalog; sending it to
  OpenRouter as written.` was printed whenever the launch was guard 2 and not late, also
  when a `session_start` handler had called `set_model` onto another provider — the
  extension's own action, whose choice stands (D4), so the prompt went there (Codex's
  `note` witness: a `setup()` provider `other`, `set_model(other/m1)` in `session_start`,
  `--model newlab/x -p hi` → the request to `/EXT/v1`, the Note still printed; the
  verifier's `v3-set-notlate` the same). The launch's resolver line (the guard-2 `Note:`,
  the custom-id `Warning:`) is now printed only while the harness, after `session_start`,
  is still on the launch route's model (same provider and id); when an extension moved
  it, nothing is printed — where the prompt goes is then the extension's to say.
- **A provider first registered in `session_start` and later moved to `setup()` by a
  `/reload`-ed extension stays late** for that process (the record is of what arrived in a
  `session_start`; by construction, not measured); a restart clears it. A provider a
  `/reload`-ed extension registers in `setup()` for the first time is not late (#344's
  reload row, `test_a_provider_registered_only_after_reload_is_the_rebuilt_model`, and
  through the whole launch and the after-`session_start` seam,
  `test_a_provider_a_reloaded_extension_registers_in_setup_is_not_late`).
- **The record trusts its inheritance**: a name in an inherited `AELIX_DOTENV_ADMITTED`
  stays "planted" in a nested process even if the user re-exports it there (fail-closed:
  the key authenticates but does not choose).
- **Whether live OpenRouter accepts the ids guard 2 sends** (the 133 vendor-catalogued
  namespace ids, `auto`) is OpenRouter's answer, not aelix's; see §3 for the one measured.
- **Mixed-case control names on Windows** (`Aelix_Future_API_KEY` slips past
  `_DOTENV_NEVER`'s case-sensitive `^AELIX_`) predate this and are a follow-up.
- #363 (the launch composition and a re-pointed built-in's key order), #365, #368, #369 (an
  untrusted project's settings, the pair above). #367 (the late path refuses) is closed
  (§2.11, 2026-10-03).

## 7. Owner decisions recorded here

Decided by the owner (2026-10-02): pi's order; guard 1 and guard 2 as worded (guard 2's
widening and narrowings are items 1 and §2.3); exact ids;
`OPENROUTER_DEFAULT_MODEL` shell-only; `openrouter/` stripped; aelix's provider case rule;
the late path kept until #367 — which refused it (owner decision 2026-10-02 on #367: follow
pi; §2.11, 2026-10-03).

Decided in this lane, each with the recommendation followed and put to the owner:

1. **Guard 2 widened to step (4)** (§2.3; the critique's S1/S2). Alternative: pi verbatim —
   an OpenRouter-only user's `openai/o1-pro` is refused for want of an OpenAI key, and a
   planted `.env` vendor key authenticates the vendor route for the 133 ids. Consequence
   found by the review of `d58cbb3e`: with `--api-key`, the widened arm (and guard 2 as
   decided) sent the typed key to `openrouter.ai`; closed by step 3b (item 8), so the
   widening now applies only without `--api-key`. Codex's C4 read the owner's "an id in no
   catalogue" literally (`openai/o1-pro`, in OpenAI's catalogue, goes to OpenRouter); the
   main loop kept the widening as a **refinement of the owner's guard 2** and §2.3 states
   its exact scope. Guard 2 is also narrower than worded
   (slashed strings only; a catalogued prefix must be an OpenRouter namespace) — §2.3.
2. **Guard 1 strict for `.env`-only users** (§6): their `openai/…` is OpenAI's. Alternative:
   count `.env` keys when no exported credential is involved — which reopens the capture
   for anyone who types a vendor they hold no key for.
3. **(5) keeps an id under the inferred provider when the user holds its key** (§2.4), where
   pi takes the first raw match.
4. **The child pin keeps user-defined routes even when the profile brings extensions**
   (§2.9) — the critique asked for no pin there; a parent extension provider absent from
   the child would otherwise reach OpenRouter by guard 2 in the child (`user-defined
   extension, profile brings others` row).
5. **`/model` drops `.env`-only providers from its pool** whenever the user holds a credential
   of their own, with no exemption for the session's current provider, and applies the
   provider case rule (§2.8). Reversed from `854bf319`, which left the current provider
   alone; Codex's C1/C2 measured what that let through. Alternative: answer `/model <arg>`
   with `resolve_route` alone — 24 #134/#136 rows red in a prototype. `/agents use` /
   `/model`'s UNDECIDED fallback refuse only with a registry (§2.7). Round 3 (the main
   loop's decision on the round-2 verification's B1): a prefix naming a provider the user
   defined keeps that provider in the pool on a `.env` key, as the launch does; the
   alternative — refusing it, as `ecb4e0bc` did — broke `/model` and the late switch for a
   user-defined provider keyed from a project `.env`.
6. **A stored but expired OAuth counts** (§2.4; pi's rule).
7. **`openrouter/auto` sends `auto`** (pi's alias) — see §3 for what OpenRouter answered.
8. **`--api-key` keeps a vendor prefix on its vendor** (§2.1 step 3b, the review's must_fix).
   Alternative: pi's order, where the key goes wherever the route lands — to `openrouter.ai`
   for `openai/gpt-4o-mini` with an OpenRouter key exported (A40). **The price of the
   choice**: an OpenRouter key typed with `--api-key` on a vendor-prefixed id
   (`--model openai/gpt-4o-mini --api-key sk-or-…`) now goes to the vendor's host
   (`api.openai.com`) as the bearer, where pi would put it on OpenRouter; the guide's
   `--api-key` paragraph says to write `openrouter/<id>` or `--provider openrouter` for an
   OpenRouter key.
9. **A route only the parent's `--api-key` decided is not pinned for a child** (§2.9).
   Alternative: the review's narrower fix — pin only when the child holds its own credential
   for that provider — which never fires (§2.9's argument).
10. **`/model` refuses `openrouter/<id>` on a `.env`-only OpenRouter key while the user holds
   a key elsewhere** (§2.8, Codex's second cross-review F7 — the round-2 rule, built-in and
   catalogued prefixes are route-deciding in `/model`). The launch sends the same string to
   OpenRouter on the `.env` key (§2.5). Question for the owner: relax `/model` to the
   launch's explicit-route rule (an `openrouter/` prefix names the route, as `--provider`
   does), or keep `/model` the stricter of the two? A re-pointed `openrouter` already
   counts as user-defined in `/model` (F6).
11. **The launch's settings default pair from a project `.aelix/settings.json`** (§6, found
   by round 4's sweep; pre-existing — `21db8c8a` sends it to `api.openai.com` too): keep it
   an explicit pair (today), or let guard 1 apply to a pair the project file supplied — the
   post-`/login` pick already skips it while the user holds a credential of their own. pi
   does neither for an untrusted directory: it does not read that directory's settings at
   all (§6, `89a92207f`). Recommendation: the pi direction, filed as #369 (stop reading an
   untrusted project's settings), optionally with guard 1 on a trusted project's pair; a
   launch-semantics change, not made here. Note the laundering path (§6): a
   no-credential session's post-`/login` pick can persist the project's pair into the
   global settings, where it then reads as the user's own.
12. **Settings `defaultProvider` in step 2 counts only for the user's own credential or
   endpoint while the user holds one** (§2.1; the main loop's decision on the fourth
   verification's B1). Alternatives: drop the tie-break (pi) — which also drops the
   ADR-0195 home a user's own default gives a bare id; or tell the project file's value from
   the global one — the merged settings do not say which file a value came from, and the
   global one can itself have been laundered (§6). A session with no credential of its own
   keeps both arms.
13. **Guard 2 never carries a typed key** (#370; owner direction on #362, 2026-10-02 — pi,
   plus the guards — and the main loop's decisions of 2026-10-06 on the research's open
   points). With `--api-key`, a string whose prefix is neither catalogued, nor user-defined,
   nor registered by an extension's `setup()` is pi's not-found (§2.1 step 2, §2.3 clause
   5); print/json exit 1 with it, interactive keeps the held placeholder and its Warning
   (§2.4), RPC the unresolved route's shape (its silent start is #371). The hint names both
   explicit routes for every slashed string whose segments are non-empty under
   `--api-key` (nested ids included; a string with an empty segment gets pi's bare text,
   since it cannot be an OpenRouter id — round 2), whether or not the user
   holds an OpenRouter credential (alternative: only when guard 2 would have fired, which
   would make the text reveal that credential). Kept: an id OpenRouter's catalogue lists
   under a prefix that is not a provider (`x-ai/grok-4.3`) takes the typed key to
   OpenRouter by step 2's exact hit, as in pi (alternative: 3b-style protection in step 2,
   a new divergence from pi). **The price**: an OpenRouter key typed with an unlisted
   vendor id (`--model newvendor/model --api-key sk-or-…`) now exits 1 in print/json and is
   held in interactive; `openrouter/<id>` or `--provider openrouter` sends it.
   `/agents use` is told about the key only where it re-resolves the launch inputs
   (`--none`, a profile with no `model:`/`provider:` of its own, or one an explicit flag
   beat); a profile's own route resolves as before #370 (round 2, the main loop's
   decision; alternative: round 1's every resolve, which refused a profile's own new pick
   because of a key typed for the launch's provider).
