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

## Providing an API key

A key can come from three places. Pick whichever fits your setup:

1. **Environment variable** (simplest) — set the provider's variable before
   running `aelix` (see the table below).
2. **`--api-key <key>`** — an inline key for a single run. This overrides the
   environment for that invocation.
3. **`models.json`** — an `apiKey` field on a provider, which itself may point
   at an environment variable or a `!command` (see
   [models-json.md](models-json.md)).

> There is no `aelix auth login` subcommand. Authentication is configured
> through the environment, `--api-key`, or `models.json`.

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
| `mistral`       | `MISTRAL_API_KEY`                             |
| `together`      | `TOGETHER_API_KEY`                            |
| `fireworks`     | `FIREWORKS_API_KEY`                           |
| `github-copilot`| `COPILOT_GITHUB_TOKEN`                        |
| `huggingface`   | `HF_TOKEN`                                    |
| `vercel-ai-gateway` | `AI_GATEWAY_API_KEY`                      |

This is the most common subset. Other supported providers include
`azure-openai-responses` (`AZURE_OPENAI_API_KEY`), `zai` (`ZAI_API_KEY`),
`minimax` (`MINIMAX_API_KEY`), `moonshotai` (`MOONSHOT_API_KEY`),
`cloudflare-workers-ai` (`CLOUDFLARE_API_KEY`), and the Xiaomi token-plan
providers. The authoritative map lives in
`packages/aelix-ai/src/aelix_ai/providers/_env_api_keys.py`.

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

Valid levels: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`. Inside the
TUI, `/thinking` cycles the level.

## Offline

`--offline` (equivalent to `PI_OFFLINE=1`) is the switch for skipping startup
network operations. Aelix performs no startup network calls today, so it is
currently a no-op reserved for forward compatibility.
