# 0250. Model routing follows pi's `resolveCliModel`, and a `.env` credential cannot choose a route

Status: Accepted (2026-10-02)
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
will refuse), #368.
Issue: #362 (P1, security). Owner decision: 2026-10-02 (issue comment).
Tests: `tests/cli/test_route_follows_pi_362.py`, `tests/cli/test_dotenv_provenance_362.py`,
`tests/cli/test_launch_route_362.py`, `tests/cli/test_route_refusals_in_session_362.py`,
`tests/model_registry/test_route_auth_362.py`, `tests/agents_ext/test_child_provenance_362.py`,
`tests/core/test_model_argument_guard_362.py`, `tests/tui/test_post_login_pick_362.py`,
`tests/rpc/test_rpc_route_guard_362.py`, `tests/providers/test_base_url.py` (§2.2's
placeholder rule).
Cross-review: Codex on `854bf319` (§5) — `/model` reworked (§2.8), guard 2's scope and the
hatch stated (§2.3, §2.6); Codex again on `a0edf615` (§5) — the post-`/login` pick's
saved default, RPC `cycle_model` and a held credential with no model rows (§2.8, §2.10).
The fourth verification (of `001ef77d`, §4): settings `defaultProvider` in step 2 counts
only for the user's own credential or endpoint (§2.1). Codex's third pass (of `5e983992`,
§5): a fallback resolver's answer and an installed resolver (§2.2, §2.8), a `.env` value
in a base-URL placeholder (§2.2), a headers-only built-in (§4).

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

17 of 46 rows changed (`compare.py base.jsonl after2.jsonl`; A40 since the review round). Every request in the table was answered by a local recorder; nothing reached a vendor. The A28 row now switches to the `session_start` provider and reaches it (critique S5); K1 is ADR-0249 §6's last bullet.

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
  when it counts (ADR-0195), else guard 2, else pi's not-found.

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
  provider has (`newlab/model-x`) still reaches guard 2; `openrouter/<id>` and
  `--provider openrouter` name OpenRouter.
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
   `AELIX_AUTH_PATH` against a `.env`. A key from a cwd `.env` never enables it.

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
never under `--api-key` (§2.1 step 3b).

Guard 2 is also NARROWER than the owner's wording in two ways, both safer: it takes only
`<segment>/<segment>` strings (a bare unknown id is pi's not-found, clause 1), and a
catalogued prefix must be one of OpenRouter's namespaces (`xai/grok-4` stays a refusal,
clause 3).

### 2.4 Divergences from pi, stated (ADR-0235)

- **Guard 1** — pi reads no `.env`; aelix does, so a `.env` credential is kept out of
  route-deciding judgements.
- **Guard 2** — pi overlays pi.dev's catalogue; aelix sends ids it cannot place to
  OpenRouter on the user's own OpenRouter key, including the widening in §2.3.
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
  so the late-registration path can still switch (§2.11); pi returns no model.
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
  on stderr, unless the late switch replaced the model; rebuilds do not reprint it.
- print/json refuse with the route's error (`Error: Model "…" is ambiguous across
  providers: …` / `… not found. Use --list-models …`) before any request; interactive
  warns with it first.
- `--api-key` is refused early only when there is no model string at all — pi's rule; a
  bare extension id (`-e extprov.py --model m1 --api-key K`) is no longer refused before
  the extension loads (ADR-0249 §6's last bullet). Every launch resolve is told the key is
  there (`typed_key`, §2.1 step 3b). A route that does not resolve (ambiguous, or not
  found — whose placeholder keeps the typed prefix as its provider) gets no key; if the late
  path then switches to a `session_start` provider, the key follows it there (on `d58cbb3e`
  the not-found placeholder got the key on its prefix, the review's nit).
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
late switch posts to the extension's host on `sessenv-dotenv-fake`. A re-pointed built-in
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

### 2.11 The late-registered path (kept; #367 changes it to refuse)

A provider registered only in `session_start` is an unknown prefix at launch. With an
OpenRouter key of the user's own, guard 2 puts it on OpenRouter; without one the launch
holds the not-found placeholder. `late_registered_route` triggers on either — the harness
on `openrouter`, or on the held placeholder with the re-resolve now landing on a
user-defined provider a turn can run — and every mode switches as before, without
persisting it. On `aa026d08` the `.env`-key case switched only because the `.env` key had
put the string on OpenRouter first, and the no-key case failed at the first turn with
`No provider registered for api='unknown'`; both now switch, and no request goes to
OpenRouter in either (critique S5). A provider whose key comes from the cwd `.env` is
switched to as well, with or without a key of the user's own elsewhere: the switch is
`/model <name>/<id>`, and the prefix names a provider the user defined (§2.8). `ecb4e0bc`
refused it whenever the user also held an own key (round-2 verification, B1).

**Known, and handed to #367**: a late provider authenticated ONLY by `--api-key`
(`-e late.py --model late/m1 --api-key K`, `late` registered in `session_start` with no key
of its own) is refused — the shared switch runs before the runtime key is moved to `late`,
so the pool does not offer it, and the key is attached after the refusal (Codex's second
cross-review, F4, `probe_launch.py late-typed`: `exit 1`, `attached ["late"]`, no turn). An
availability failure, no credential egress. No code change here: #367 makes the
late-registered path refuse outright, which retires this ordering.

### 2.12 Messages

- ambiguity (pi `:497-501`): `Model "<s>" is ambiguous across providers: <sorted
  provider/id, …>. <No matching provider is authenticated. | More than one matching
  provider is authenticated.> Use --provider or provider/model.` — with
  ` (<NAMES> came from a project .env, which does not choose between providers.)` before
  `Use` when a `.env` name is why a match counted as configured;
- not found (pi `:599-605`): `Model "<s>" not found. Use --list-models to see available
  models.`, plus why a `.env` OpenRouter key did not send it to OpenRouter;
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
  **Known, handed to #367** (§2.11); `late-typed` unchanged.
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
  reads `.aelix/settings.json` whatever the trust answer. Porting that is #369.
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
- **A late provider authenticated only by `--api-key`** is refused (§2.11, F4) — #367.
- **The record trusts its inheritance**: a name in an inherited `AELIX_DOTENV_ADMITTED`
  stays "planted" in a nested process even if the user re-exports it there (fail-closed:
  the key authenticates but does not choose).
- **Whether live OpenRouter accepts the ids guard 2 sends** (the 133 vendor-catalogued
  namespace ids, `auto`) is OpenRouter's answer, not aelix's; see §3 for the one measured.
- **Mixed-case control names on Windows** (`Aelix_Future_API_KEY` slips past
  `_DOTENV_NEVER`'s case-sensitive `^AELIX_`) predate this and are a follow-up.
- #363 (the launch composition and a re-pointed built-in's key order), #365, #367 (the late
  path refuses), #368, #369 (an untrusted project's settings, the pair above).

## 7. Owner decisions recorded here

Decided by the owner (2026-10-02): pi's order; guard 1 and guard 2 as worded (guard 2's
widening and narrowings are items 1 and §2.3); exact ids;
`OPENROUTER_DEFAULT_MODEL` shell-only; `openrouter/` stripped; aelix's provider case rule;
the late path kept until #367.

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
