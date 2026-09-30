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
mid-session.

### How `--model <provider>/<id>` is resolved when `OPENROUTER_API_KEY` is set

With `OPENROUTER_API_KEY` in the environment and no `--provider`, a `--model`
string can mean two things: `openai/gpt-4o-mini` is both OpenAI's model and an
OpenRouter id. Aelix decides it from **configuration only** — which provider
names exist, which ones *you* defined — never from which credentials happen to be
present ([ADR-0249](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0249-a-model-openrouter-cannot-serve-is-resolved-before-openrouter-from-env.md)):

1. **A provider you defined** — a `models.json` custom provider, a built-in
   provider you re-pointed with a `models.json` `baseUrl`, or a provider an
   extension registered with `register_provider` (in its `setup()` — see
   below). `ollama/qwen3.6:35b-a3b`, `mygateway/some-model` and `extprov/m1` go
   to that endpoint, and never to OpenRouter — an id that provider does not list
   is either backfilled from its own models (when they all speak one protocol)
   or refused with an error that names the provider. An extension that
   registers a **built-in** name with models (`openai`, say) owns that prefix
   the same way: `openai/<id>` resolves among the models it registered, never
   to `api.openai.com` with the extension's key.
2. **An id only one of your providers lists**, spelled exactly as it lists it:
   `--model qwen3.6:35b-a3b` reaches the one `models.json` provider that lists
   it. This covers slashed ids too: `anthropic/claude-sonnet-4.5` goes to a
   gateway of yours that lists it verbatim, not to OpenRouter (this rule is
   checked before rule 4 below). A built-in provider you re-pointed with a
   `baseUrl` lists every catalog id it has, so with `providers.openai.baseUrl`
   set a bare `--model gpt-4o-mini` goes to your gateway — without that
   `baseUrl` it goes to OpenRouter.
3. **A built-in provider OpenRouter has no namespace for** — `openai-codex`,
   `xai`, `groq`, `mistral`, `github-copilot`, `zai` and the other catalogued
   providers whose name is not the first segment of any OpenRouter id in this
   build's catalog. `openai-codex/gpt-5.1` goes to Codex.
4. **Everything else goes to OpenRouter**, exactly as before: the nine prefixes
   that are both a provider and an OpenRouter namespace (`openai`, `anthropic`,
   `google`, `deepseek`, `minimax`, `moonshotai`, `nvidia`, `xiaomi`,
   `openrouter`), prefixes that are not providers (`meta-llama/…`, `qwen/…`) and
   bare ids none of your providers serve. `--model anthropic/claude-haiku-4-5`
   goes to OpenRouter even if you also hold an Anthropic key.

Without `OPENROUTER_API_KEY` the prefix simply names the provider. Either way the
prefix is matched case-insensitively (`RetryProbe/held-model` works), and a
provider you defined wins over a built-in spelled the same up to case: with a
`models.json` provider named `OpenAI`, `openai/m1` is yours, never OpenRouter's
or `api.openai.com`'s. If two of your providers differ only in case (`OpenAI`
and `OPENAI`), a prefix that spells neither exactly is refused with an error
naming both.

A provider you **name** is matched the same way — `--provider`, `defaultProvider`
in `settings.json` and an agent profile's `provider:`. `--provider OPENAI` works,
and with that `models.json` `OpenAI`, `--model m1 --provider openai` is yours too
(your host, your `--api-key`); a built-in whose name differs from one of your
providers only in case cannot be reached that way — rename your provider if you
need both. A name two of your providers share up to case is refused naming both.
Spelling aside, `--provider` keeps its meaning: it turns the OpenRouter rule off
unless it names `openrouter`.

A provider an extension registers in a `session_start` handler exists only after
the launch model was chosen, so with `OPENROUTER_API_KEY` set its prefix would
have been read as an OpenRouter id. Every mode — interactive, RPC, `-p` and
`--mode json` — switches to the provider before the first prompt, exactly as
`/model <name>/<id>` would, and prints one line on stderr saying so (with
`/model`'s caution when the id is one the provider does not list). Unlike
`/model`, it does not save the choice as your default model. If `/model` would
refuse it, no prompt is sent: the interactive and RPC modes start and tell you
to run `/model`; `-p` and `--mode json` stop with an error. The same happens when
the handler registers two providers whose names differ only in case and your
prefix spells neither. A provider the handler registers and then unregisters is
simply unknown, so with `OPENROUTER_API_KEY` set its prefix goes to OpenRouter
like any other unknown prefix. Register providers in the extension's `setup()` to
avoid all of this.

To override the rule, say what you mean:

```bash
aelix --provider openrouter --model openai/gpt-4o-mini "..."   # force OpenRouter
aelix --provider anthropic  --model claude-haiku-4-5 "..."     # force the vendor
```

(`--provider anthropic` is the vendor unless you defined a provider of that name
in some case — then it is yours, as above. The same goes for `--provider
openrouter`: if you named one of your providers `OpenRouter` in any case, that
flag selects yours, so rename it if you also want to force OpenRouter.)

`OPENROUTER_DEFAULT_MODEL` (used when no `--model` is given) is always an
OpenRouter id. The same rule applies to an agent profile's `model:`, to a
hand-written `defaultModel` in `settings.json`, and — with the parent's view of
your providers spelled out as `--provider`/`--model` — to delegated children.

## Providing an API key

A credential can come from four places. Pick whichever fits your setup:

1. **Environment variable** (simplest) — set the provider's variable before
   running `aelix` (see the table below).
2. **`--api-key <key>`** — an inline key for a single run, attached to the
   provider the run actually resolved (after extensions have loaded), and it
   outranks every other source for that provider in that invocation. So
   `--model ollama/<id> --api-key K` sends `K` to your ollama endpoint;
   `--model openai/gpt-4o-mini --api-key K` with `OPENROUTER_API_KEY` set sends
   `K` to **OpenRouter** (rule 4 above) — add `--provider openai` if `K` is an
   OpenAI key. The key is not forwarded to delegated children.
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
