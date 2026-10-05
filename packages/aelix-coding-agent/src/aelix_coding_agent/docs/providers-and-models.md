# Providers and Models

Status: Accepted

How to give `aelix` a provider key, pick a model, and switch between them. For
adding **custom** providers/models (custom base URLs, header packs, per-model
overrides) see [models-json.md](models-json.md).

## Selecting a model

Model ids use the `<provider>/<model>` form:

```bash
aelix --model openai/gpt-4o-mini "..."
aelix --provider anthropic --model claude-sonnet-4-6 "..."
```

Discover what is available:

```bash
aelix --list-models            # every model in the catalog
aelix --list-models claude     # filter by substring
```

Inside the interactive TUI, `/model` opens a picker to switch the active model
mid-session. The picker lists every provider you have a key for, a project
`.env` key included — choosing one there is your own pick. `/model <id>` and
`/model <provider>/<id>` follow the same two rules as the launch below. While
you hold a credential of your own anywhere (an `--api-key` counts, and so does
a key for a provider that lists no models), a project `.env` key never decides
where the string goes: a provider only the `.env` authenticates is left out of
the match — even when the session is already on it — and where only such a
provider could serve the string, `/model` refuses and says so rather than
switch (export the key or `/login` to use it). A prefix that names a provider
you defined (in `models.json`, including a re-pointed built-in — a re-pointed
`openrouter` too — or by an extension) stays inside it, and its key may come
from the `.env`: the prefix chose the destination, the file only authenticates
it — as with `--model`. One difference from `--model`: the built-in
`openrouter/` prefix is not an exemption in `/model`, so with the OpenRouter
key only in a project `.env` and a key of your own elsewhere,
`/model openrouter/auto` is refused, while `--model openrouter/auto` runs on
the `.env` key.

The model picked for you after `/login` (when the current one cannot run)
follows the same rule: your saved `defaultProvider`/`defaultModel` (global
settings only — a project's `.aelix/settings.json` is never read there, #369)
that only a project `.env` key authenticates is skipped while you hold a key
of your own. A program
that runs RPC mode with a model registry gets the same from `cycle_model`,
which rotates only through models the rule leaves; `set_model` names its
provider, so a `.env` key may authenticate it, as with `--provider`.
(`aelix --mode rpc` itself wires no registry, and answers both with "requires
a ModelRegistry".)

### How a `--model` string becomes a provider

Aelix follows pi's order (`resolveCliModel`), with exact ids only and two guards
of its own ([ADR-0250](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md)):

1. **`--provider` names the provider.** The id is looked up inside it; a repeated
   prefix is tolerated (`--provider anthropic --model anthropic/claude-haiku-4-5`
   is `claude-haiku-4-5`), and an id the provider does not list is sent as a
   custom id, with a one-line warning.
2. **A prefix that names a provider** — any provider aelix knows: the catalog,
   your `models.json`, an extension's `register_provider` — picks it,
   case-insensitively (`RetryProbe/held-model` works). A provider **you defined**
   wins over a built-in spelled the same up to case, and never lets the string go
   anywhere else: `ollama/qwen3.6:35b-a3b`, `mygateway/some-model` and
   `extprov/m1` reach that endpoint, and an id it does not list is backfilled from
   its own models (when they all speak one protocol) or refused naming the
   provider. Two of your providers that differ only in case (`OpenAI`, `OPENAI`)
   and a prefix that spells neither are refused naming both.
3. **Otherwise the whole string is matched as an id** across every provider.
   One match wins (`x-ai/grok-4.3` is OpenRouter's, `held-model` is your
   `models.json` provider's). Several matches: the settings `defaultProvider`
   decides if it is one of them, else the **one** matching provider you hold a
   credential for; otherwise it is an error that lists them —
   `Model "gpt-4o-mini" is ambiguous across providers: azure-openai-responses/gpt-4o-mini,
   cloudflare-ai-gateway/gpt-4o-mini, openai/gpt-4o-mini. … Use --provider or
   provider/model.` A bare id nothing lists is sent to the `defaultProvider` as a
   custom id. In both cases `defaultProvider` is the merged setting — a trusted
   project's `.aelix/settings.json` can set it, over yours (an untrusted one is
   not read, [project-trust.md](project-trust.md)) — so while you hold a credential
   of your own it counts only when it names a provider your own credential
   authenticates, or a provider you defined (`models.json`, an extension, a
   re-pointed built-in); otherwise it is ignored and the error says so
   (`Settings defaultProvider "anthropic" was not used: …`).
4. **Inside the provider a prefix named, the id must match exactly** (no fuzzy
   matching). When it does, but you hold no credential for that provider and
   exactly one provider you do hold a credential for lists the whole string as
   its id, that one is used: with only `OPENROUTER_API_KEY`,
   `openai/gpt-4o-mini` goes to OpenRouter; with an OpenAI key as well, to
   OpenAI.
5. **When it does not match**, the whole string is tried as an id elsewhere — a
   provider you hold a credential for first; if you hold one for the provider
   the prefix named, the id stays there as a custom id.

Then the two guards:

- **A key from a project `.env` never chooses where a prompt goes.** It still
  authenticates the route your flags, your `models.json` and your own keys
  chose — but the "provider you hold a credential for" questions above count
  only credentials you exported in your shell, stored with `/login` (in your
  agent dir's `auth.json`) or wrote into your `models.json`. So a cloned repo's
  `OPENROUTER_API_KEY` cannot pull `anthropic/claude-haiku-4-5` off your own
  Anthropic key, and its `OPENAI_API_KEY` cannot pull `openai/gpt-4o-mini` off
  OpenRouter. Delegated agents and an `aelix` the bash tool starts inherit the
  same judgement (aelix passes them the list of names the `.env` supplied, in
  `AELIX_DOTENV_ADMITTED`). Nor does a `.env` value choose a **host**: a
  `{NAME}` placeholder in a base URL is filled from a project `.env` only for
  Cloudflare's two ids; for anything else (a `{TENANT_KEY}` in your own
  `models.json` template) export the variable — until then the model is not
  runnable and the refusal says so. An embedder that installs an
  `AuthStorage.set_fallback_resolver` counts as holding a credential of its
  own (the providers a resolver answers for cannot be listed), and a resolver
  answer that carries a `.env` value (equal to it ignoring case and
  surrounding spaces, or - both 8 characters or longer - with it inside) does
  not count as one - a resolver must not derive its answer from a variable a
  project `.env` supplied (`aelix_ai.dotenv_record.dotenv_supplied`).
- **New OpenRouter ids.** This build's catalog lacks many of OpenRouter's live
  ids (pi refreshes its catalog from pi.dev; aelix does not). So a
  `<vendor>/<model>` string **that OpenRouter's part of this build's catalog does
  not list** goes to OpenRouter as written, with a one-line note, **only when you
  hold an OpenRouter key of your own** (exported, `/login`, or `models.json`) —
  when its prefix is one no provider has (`newlab/model-x`), or one of the
  prefixes OpenRouter shares with a provider (`anthropic/…`, `openai/…`, …) and
  you hold no key of your own for that provider. That includes an id the
  vendor's own catalog lists: with only `OPENROUTER_API_KEY`, `openai/o1-pro`
  goes to OpenRouter, because OpenRouter is the only provider you can reach it
  through. It never happens under a provider you defined, nor under a catalogued
  provider OpenRouter has no ids for (`xai/…`, `openai-codex/…`, `groq/…`), nor
  for a bare id, nor **with `--api-key`**: those stay pi's errors.

The explicit routes to OpenRouter are `openrouter/<id>` (the prefix is stripped,
as in pi: `openrouter/newlab/model-x` sends `newlab/model-x`) and
`--provider openrouter --model <id>`; both work with a key from anywhere,
a project `.env` included. `OPENROUTER_DEFAULT_MODEL`, read from your **shell**
only, is the id used when no `--model` is given, as
`--provider openrouter --model <it>` — "shell only" unless you hatch it yourself:
with `AELIX_DOTENV_ALLOW=OPENROUTER_DEFAULT_MODEL` in your shell, a project
`.env`'s value is admitted and picks that model. `OPENROUTER_BASE_URL` (your shell, or a
`.env` name you listed in `AELIX_DOTENV_ALLOW`) applies to every route that lands
on OpenRouter.

```bash
aelix --model openrouter/openai/gpt-4o-mini "..."              # OpenRouter, whatever keys you hold
aelix --provider openrouter --model openai/gpt-4o-mini "..."   # the same
aelix --provider anthropic  --model claude-haiku-4-5 "..."     # the vendor
```

(`--provider anthropic` is the vendor unless you defined a provider of that name
in some case — then it is yours. The same goes for `--provider openrouter`: if
you named one of your providers `OpenRouter` in any case, that flag selects
yours, so rename it if you also want to force OpenRouter.)

A provider an extension registers while a session is starting — from the end of the
build through its `session_start` handlers and the turns they trigger, which aelix waits
out, up to aelix's check after `session_start`: in a `session_start` handler, in a
handler of a turn one triggers, such as its `input` or `before_agent_start` handler
(that turn itself is refused, below; it may run after the handler returned), or in a
task a handler or `setup()` started that registers then —
exists only after the launch model was chosen, so at launch its name is unknown, and a launch model
that no provider registered at launch could take — `--model <name>/<id>`,
`--provider <name> --model <id>`, a bare id only it lists, a
`defaultProvider`/`defaultModel` pair in `settings.json`, an agent profile's
`model:`, or `--provider <name>` alone — is refused, as in pi. A launch model a
provider registered at launch does serve (a built-in, one in `models.json`, one
registered in `setup()`) stays on that provider, as pi's launch model does, even
if the new provider serves the same id or your settings `defaultProvider` names
it — and a turn a `session_start` handler triggers runs there too. The refusal: `-p` and
`--mode json` stop with an error before any request ("… names provider '<name>',
which an extension registered while a session was starting (for example in a
session_start handler) … Register '<name>' in
the extension's setup() (its factory) to use it at launch."), and the interactive
mode starts with that warning and sends nothing until you pick a model with
`/model`. What decides is where your launch inputs land once the handler has run,
not what the handler did: a `set_model` in it does not make the launch pass. And
whatever re-derives the model from those inputs without you naming one holds the
session again wherever it lands on that provider — also when the session started on
a registered provider, since aelix's rebuilds re-derive the model from the launch
inputs where pi keeps the session's: `/new` and the other rebuilds
(also when a `session_start` handler of the rebuild sets a model), `/agents use`
of a profile that names no model or provider of its own (or `--none`, or one whose
`model:` your `--model` overrides) — also after a launch that went elsewhere, such
as `--agent` with `provider: openrouter` and `--model <name>/<id>` — and the model
picked for you after `/login`, which picks as it would with no saved default and
never that provider.
For a refused launch nothing is sent in the meantime, whatever keys you hold:
while the extensions' `session_start` handlers run, a launch model no registered
provider has claimed is not runnable yet, and no turn starts at all — not even
one a handler triggers after setting a model of its own with `set_model` — so a
handler's `trigger_turn` then ends as a refused turn (nothing sent; its message
stays in the conversation and goes out with the next prompt that is sent), a
`model_select` handler cannot move the session off the hold — nor send a turn
while aelix puts the session on hold, at launch, on a rebuild or at `/agents use`
(that turn is refused too, and nothing is sent; its message stays in the
conversation and goes out with the next prompt that is sent) —
and an `--api-key` is attached only after that decision — typed with a refused
model it is not used at all, and no provider holds it. The same holds while a
rebuild's `session_start` handlers run in a session that is held. Once the session is running the provider is there,
so `/model <name>/<id>` switches to it like any other, and so does `/agents use`
of a profile whose own `model:` or `provider:` names it (`provider:` alone names
the route too) — a `/model` choice lasts until the next `/new` or other rebuild,
which re-derives the launch model and holds again (pi keeps the session's model;
aelix rebuilds from the launch inputs), a profile's until an `/agents use` that
names no route. `/model` also saves it as your default, so the next launch without
`--model` reads it from `settings.json` and is refused the same way. The RPC mode
starts held too but cannot pick another model (`aelix --mode rpc` has no model
registry for `set_model`): restart it with another `--model`. Register providers
in the extension's `setup()` and they work at launch. A provider the handler
registers and then unregisters is simply unknown.

The same rules apply to an agent profile's `model:`, to a hand-written
`defaultModel` in `settings.json`, and to delegated children — which are told
`--provider` too wherever they could not reach the parent's route alone (a
provider you defined, which a child without your extensions does not have). A
route only your `--api-key` would decide is left to the child, which never
receives the key.

## Providing an API key

A credential can come from four places. Pick whichever fits your setup:

1. **Environment variable** (simplest) — set the provider's variable before
   running `aelix` (see the table below).
2. **`--api-key <key>`** — an inline key for a single run, attached to the
   provider the run actually resolved (after extensions have loaded), and it
   outranks every other source for that provider in that invocation. So
   `--model ollama/<id> --api-key K` sends `K` to your ollama endpoint. With
   `--api-key`, a prefix that names a provider keeps the string there — the id
   it lists, else a custom id — whatever else you hold:
   `--model openai/gpt-4o-mini --api-key K` sends `K` to **OpenAI** even with an
   OpenRouter key exported (rules 4 and 5 and the OpenRouter guard below are
   skipped; pi would send it to OpenRouter with `K` as the bearer). The price of
   that choice: if `K` is an **OpenRouter** key, `--model openai/gpt-4o-mini
   --api-key K` sends it to `api.openai.com` as the bearer, where pi would have
   put it on OpenRouter — write `openrouter/openai/gpt-4o-mini` or
   `--provider openrouter` for an OpenRouter key. A prefix no provider has (`newlab/model-x`) is
   refused as not found, as in pi — write `openrouter/newlab/model-x` or
   `--provider openrouter --model newlab/model-x` to send it to OpenRouter with
   that key (an id OpenRouter's part of the catalog lists, such as
   `x-ai/grok-4.3`, goes there with it, as in pi). A string that does not resolve
   (ambiguous, not found) gets no key. It is not forwarded to delegated children.
3. **`models.json`** — an `apiKey` field on a provider, which itself may point
   at an environment variable or a `!command` (see
   [models-json.md](models-json.md)).
4. **`/login` inside the interactive TUI** — the OAuth path (GitHub Copilot and
   other subscription logins). It stores the result in `auth.json`, so it
   persists across runs and is the one option a delegated child process can
   also pick up.

> There is no `aelix auth login` **command-line** subcommand — the shipped
> `aelix` console script has no `auth` verb, so OAuth login goes through `/login`
> in the TUI.

## Provider environment variables

Set the variable for the provider you use:

| Provider        | Environment variable                          |
| --------------- | --------------------------------------------- |
| `anthropic`     | `ANTHROPIC_API_KEY` (or `ANTHROPIC_OAUTH_TOKEN`) |
| `openai`        | `OPENAI_API_KEY`                              |
| `openrouter`    | `OPENROUTER_API_KEY`                          |
| `google`        | `GEMINI_API_KEY`                              |
| `deepseek`      | `DEEPSEEK_API_KEY`                            |
| `groq`          | `GROQ_API_KEY`                                |
| `cerebras`      | `CEREBRAS_API_KEY`                            |
| `xai`           | `XAI_API_KEY`                                 |
| `together`      | `TOGETHER_API_KEY`                            |
| `fireworks`     | `FIREWORKS_API_KEY`                           |
| `github-copilot`| `COPILOT_GITHUB_TOKEN`                        |
| `huggingface`   | `HF_TOKEN`                                    |
| `vercel-ai-gateway` | `AI_GATEWAY_API_KEY`                      |

This is the most common subset. Other supported providers include `zai`
(`ZAI_API_KEY`), `minimax` (`MINIMAX_API_KEY`), `moonshotai`
(`MOONSHOT_API_KEY`), `cloudflare-workers-ai` (`CLOUDFLARE_API_KEY`), and the
Xiaomi token-plan providers. The authoritative map lives in
`packages/aelix-ai/src/aelix_ai/providers/_env_api_keys.py`.

## Adapter coverage — what this build can actually run

The bundled model catalog spans nine wire protocols; this build ships adapters
for six of them. A provider is usable only if its protocol has an adapter.

| Wire protocol (`api`)     | Adapter | Status | Notes |
| ------------------------- | ------- | ------ | ----- |
| `openai-completions`      | ✅ | verified | Also carries `openrouter`, `cloudflare-workers-ai`, `groq`, `deepseek`, `xai`, … |
| `anthropic-messages`      | ✅ | verified | Also carries `minimax` and the Xiaomi token-plan providers |
| `openai-responses`        | ✅ | 🧪 experimental | |
| `openai-codex-responses`  | ✅ | 🧪 experimental | ChatGPT Plus/Pro OAuth |
| `google-generative-ai`    | ✅ | 🧪 experimental | Hidden until `GEMINI_API_KEY` resolves |
| `google-vertex`           | ✅ | 🧪 experimental | Hidden until GCP auth resolves |
| `bedrock-converse-stream` | ❌ | **not supported** | `amazon-bedrock` |
| `azure-openai-responses`  | ❌ | **not supported** | `azure-openai-responses` |
| `mistral-conversations`   | ❌ | **not supported** | `mistral` |

The three unsupported protocols have no adapter in this build, so
`amazon-bedrock`, `azure-openai-responses` and `mistral` cannot run. Rather than
let them fail at the first turn, Aelix hides them: they do not appear in
`--list-models` or the `/model` picker. `MISTRAL_API_KEY` and
`AZURE_OPENAI_API_KEY` are still listed in `_env_api_keys.py`, but there is
nothing for them to authenticate. Mistral and Azure models reached **through** a
supported gateway — `openrouter`, say — work normally, because those ride
`openai-completions`.

`github-copilot` is the one provider that spans three protocols
(`openai-completions`, `anthropic-messages`, `openai-responses`) and routes
per-model, which is why its models behave differently from one another.

To reproduce this table, enumerate the registered protocols after
`runtime_bootstrap.register_providers()` and compare against the `api` field in
`packages/aelix-ai/src/aelix_ai/models_generated.json`.

For `anthropic`, an `ANTHROPIC_OAUTH_TOKEN` takes precedence over a static
`ANTHROPIC_API_KEY` when both are set.

## When no key is found

If you run `aelix` without a usable key (and without selecting a model), it
prints guidance pointing you at the relevant `<PROVIDER>_API_KEY` variable and
the `--model` flag. In the non-interactive modes (`--print` / `--mode json`)
this is a hard error with a non-zero exit code, so scripts fail fast instead of
hanging.

## Reasoning / thinking level

Models that support extended reasoning accept a thinking level:

```bash
aelix --thinking medium --model anthropic/claude-sonnet-4-6 "..."
```

Valid levels: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`. Inside the TUI,
`/thinking` opens a picker over the levels the current model supports
(`/settings` → **Thinking level** cycles them instead).

Not every model calls those levels by those names, and not every model supports
all of them. Where the two differ, the picker rows, the confirmation line and the
🧠 statusline segment show both: `xhigh (max)` on a model whose catalog entry maps
`xhigh` to `max`, `xhigh (high)` on one that has no `xhigh` and is clamped down to
`high`, `low (high)` on one that supports neither `low` nor `medium` and is
clamped up. **The value in parentheses is the tier the request carries**; a level
shown as one bare word is one the model calls by the same name. `off` is always
bare — it is folded into "no reasoning requested" before a provider adapter sees
it, so there is no tier to name.

One place that reading is currently too generous: on the thirteen non-reasoning
Google entries (Gemini 1.5 / 2.0, Gemma, and the Vertex-hosted Llamas) the Google
adapter turns that disabled level back into a thinking request, so a `high (off)`
there understates what actually goes out. That is an adapter defect, tracked as
[#256](https://github.com/handochan/aelix-ai/issues/256).

The level is recorded in the session and restored when you come back to it —
`--continue`, `--resume`, `--session`, `--fork` and the in-session `/resume`.
It is clamped to whatever the resumed model can do, so a session left at `xhigh`
opens at `high` on a model that tops out there rather than falling back to `off`.
An explicit `off` is a decision and is restored as `off`. `--thinking` (and an
agent profile's `thinking:`) outranks the session at launch, and is itself
recorded into a session that has not chosen a level yet — so the next
`--continue` comes back at it. The session in turn outranks the
`defaultThinkingLevel` setting. One exception to the ladder: after an in-session
`/resume` the target session's own recorded level wins, launch flag included —
you asked for that session, not for a re-run of the command line.

What `xhigh` means depends on how the model thinks. On a model that takes a
Claude-style token budget it sends a larger budget than `high` — 32768 against
16384 — except where the model's output cap is tighter than the budget, in
which case the budget shrinks to leave 1024 tokens for the answer and the two
levels can come out equal (`openai/gpt-5.2-chat` and `openai/gpt-5.3-chat`,
output cap 16384, are the shipped examples). Equal is as close as they get: a
higher level never sends a *smaller* budget than a lower one, whatever you
override `maxTokens` to. On an adaptive model — Opus 4.6 and everything after
it (4.7, 4.8, Opus 5, Sonnet 5, Fable 5), plus any row the catalog marks
`compat.forceAdaptiveThinking` — it selects the model's own top effort rather
than a token count. On Gemini 2.x there is nothing above `high`: that level is
already the API's `thinkingBudget` ceiling.

A model that does not list `xhigh` gets `high` instead — `--thinking` accepts
the level on any model, and each provider clamps it to what the model offers.

Override the `maxTokens` of a Claude-style row that thinks with a *budget*
below 2048 and thinking is **off** for it, whatever level you pick: the API
wants a thinking budget of at least 1024 that is still smaller than the
request, and below 2048 no such number also leaves the answer its 1024 tokens.
An adaptive row is not affected — it carries no budget for the cap to squeeze,
so the override bounds the answer and nothing else.

Two adaptive rows cannot be asked to stop thinking at all: `claude-fable-5`
and `claude-fable-5-1` reject the "thinking off" request outright (measured
2026-09-09), so `off` there buys the least thinking they offer and asks the API
not to show it. `anthropic/claude-sonnet-5` behaves the same way because its
catalog row says it has no `off` level — which is also why the picker does not
offer you one.

## Offline

`--offline` (equivalent to `PI_OFFLINE=1`) skips the network operations aelix
starts on its own:

- the `rg` / `fd` binary auto-download. Measured with `PI_OFFLINE=1` and neither
  binary on `PATH`: `ensure_tool("rg")` printed
  `ripgrep not found. Offline mode enabled, skipping download.` and returned
  nothing, instead of fetching a release archive from GitHub.
- the extension catalog fetch and index-less pip installs. Note the spelling
  here: subcommands are routed before the flag parser runs, so
  `aelix --offline extension …` does **not** reach them. Use the subcommand's
  own flag (`aelix extension discover --refresh --offline`) or export
  `PI_OFFLINE=1`. With it on, a catalog on a network transport is skipped with a
  per-source notice, and a bare `aelix extension install <pkg>` with no
  `--index-url` is refused rather than silently reaching PyPI.

It does **not** touch provider/LLM calls — those are the request you made, not
something aelix decided to do. A turn still goes to the network under
`--offline`.

This section used to say `--offline` was "currently a no-op reserved for forward
compatibility". That was wrong on both halves.
