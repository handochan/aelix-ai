# Provider Credentials

Status: Accepted

How to give `aelix` a credential for a provider: an environment variable,
`--api-key`, a `models.json` `apiKey`, or `/login`. Picking a model, and how a
`--model` string finds its provider, are in
[providers-and-models.md](providers-and-models.md); defining a provider of
your own is in [models-json.md](models-json.md).

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
   OpenRouter key exported (rules 4 and 5 and the OpenRouter guard in
   [How a `--model` string becomes a provider](providers-and-models.md#how-a---model-string-becomes-a-provider)
   are skipped; pi would send it to OpenRouter with `K` as the bearer). The price of
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

A `/login` subscription's access token expires, and aelix refreshes it before
the request that needs it. If that refresh fails because the token endpoint
answered `429`, `500`, `502`, `503`, `504`, `520` or `524` (the statuses pi
retries), or could not be reached (a dropped connection, a timeout, a proxy
that refused the connection), the turn is retried the way a provider's `502`
is: by default up to three retries, 2 s, 4 s and 8 s apart (your
`settings.json` `retry` block changes both, see
[Automatic retry](providers-and-models.md#automatic-retry)), shown as `Retrying…` in the
TUI and as `auto_retry_start` / `auto_retry_end` in `--mode json` and rpc, and
a delegated agent's run retries the same way. The error then reads `OAuth
refresh failed for <provider>: <cause>`, with no `/login` hint. While your
stored login exists, each retry refreshes again, and no request is sent until
one succeeds — never on an environment variable or a `models.json` key instead
of the login. If another aelix logs you out meanwhile, what the retry does
depends on when the logout lands:

- **During the wait between retries:** the retry sends nothing and ends with
  `get_api_key_and_headers failed: The auth.json entry for <provider> is an
  OAuth login that gave no key. …` (`✖ Retry failed: …` in the TUI,
  `auto_retry_end` with `success: false` in `--mode json` and rpc). That
  message still tells you to remove the entry, which the logout already did.
  The next turn uses the next key in the
  [order models-json.md lists](models-json.md#which-key-a-request-carries):
  the provider's `models.json` `apiKey`, else its environment variable.
- **While the failing refresh request is still under way:** the retry itself
  uses that next key, as a new turn would. With no such key it sends nothing
  and ends with the provider's own error (`No API key for provider:
  anthropic`). An OpenAI Codex request needs a ChatGPT login token, so it
  sends nothing either and ends with `No OAuth token for openai-codex — run
  /login …` (or, with a `models.json` key that is not such a token,
  `openai-codex access token is missing the chatgpt_account_id claim …`).

A refresh the
endpoint refused (`400`, `401`, `403`, such as `invalid_grant` for a revoked
login), and any other status such as `501`, is not retried: the turn fails at
once with `get_api_key_and_headers failed: OAuth refresh failed for
<provider>: … Run /login to sign in to <provider> again.` The one exception
is a `2xx` whose body is cut short or times out: it is retried as a dropped
connection (a `2xx` whose encoding is broken fails at once). A non-`2xx`
answer is decided by its status once it arrives, whatever then happens to its body (a
broken encoding, a body cut short, a read that times out). A refusal after a
retry ends that retry with the same message (`✖ Retry failed: …` in the TUI,
`auto_retry_end` with `success: false` in `--mode json` and rpc). This covers
OpenAI Codex, Anthropic and GitHub Copilot logins (pi retries the same
refreshes;
[ADR-0251](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0251-own-api-key-before-environment-and-composed-launch-model.md)
§4, §12). For an answer whose body fails mid-read, pi's behaviour depends on the provider and the connection framing (its Codex refresh reads a non-2xx answer's body with .catch, its Anthropic and Copilot refreshes do not), so aelix's rule above can differ from pi for these malformed answers.

When an OAuth sign-in or a token refresh fails on an HTTP error answer, the
error keeps the status code and quotes what the provider's server answered: at
most 512 characters per quoted string, with terminal control characters and
line breaks removed, runs of blank space shortened to one, and leading or
trailing blank space not counted. A 200 answer that is not JSON (a captive
portal) carries no status: Anthropic's error says it returned invalid JSON and
quotes the body, OpenAI Codex's is the JSON parser's own message. The bound is
per quoted string, not per message: a message that quotes two strings - a
Copilot device-flow error and its description, or a proxy's connection error
and its cause - carries about 1.2 KB, and the Codex and Anthropic sign-in
errors and Anthropic's refresh error, which repeat every link of a connection
error, up to about 1.8 KB; none of them repeats an exception your own code was
handling when it called them. A
proxy's or captive portal's error page, or a proxy that refuses the connection
with a status line of its own, can no longer clear your screen, switch it to
the alternate screen or write your clipboard. The same holds for a model
request: when a proxy refuses it or answers with an error page, the turn's
error quotes the proxy's words the same way for every built-in provider, except
that a line break becomes a space (an extension's own provider builds its own
message). A model request's error shows the HTTP status only where the
provider's SDK puts it in its message. The automatic retry and the
context-overflow compaction still read the whole error, so a
`context_length_exceeded` code or a `502` past the 512th character is acted on
as before. A token response that is
missing a field is described by its keys, never by the tokens it did carry.
The full body is not kept; if you need it, reproduce the request with `curl`.

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

For `anthropic`, an `ANTHROPIC_OAUTH_TOKEN` takes precedence over a static
`ANTHROPIC_API_KEY` when both are set.
With neither set, the TUI and `--mode rpc` send an exported
`ANTHROPIC_AUTH_TOKEN` as `Authorization: Bearer` to the `anthropic` provider,
as pi does (`-p` and `--mode json` do not count it as a key yet and stop with
"No API key found").

Each variable belongs to its provider only. `ANTHROPIC_API_KEY`,
`ANTHROPIC_OAUTH_TOKEN` and `ANTHROPIC_AUTH_TOKEN` go to `anthropic` and to no
other provider on the Anthropic API — not `fireworks`, `minimax`,
`vercel-ai-gateway` or a `models.json` gateway with `"api":
"anthropic-messages"`. A request to a provider with no key of its own stops
before anything is sent, with `No API key for provider: <provider>`
([#374](https://github.com/handochan/aelix-ai/issues/374)). The Anthropic SDK's
own credential sources (`ANTHROPIC_PROFILE`, workload identity federation) are
not used, and the headers it reads from `ANTHROPIC_CUSTOM_HEADERS` are sent to
the provider named `anthropic` only (a `models.json` override of it included).
For that provider an auth header in the variable (`X-Api-Key`, `Authorization`
or `cf-aig-authorization`, any letter case, with a non-blank value) is enough on
its own, so a proxy you authenticate that way works in the TUI and
`--mode rpc`; `-p` and `--mode json` do not read the variable and still ask for
a key ("No API key found"), as before. The OpenAI SDK's own
`OPENAI_CUSTOM_HEADERS` and `OPENAI_ADMIN_KEY` (read from openai 2.34 on) are
used for no provider, `openai` included
([#278](https://github.com/handochan/aelix-ai/issues/278)).

## When no key is found

If you run `aelix` without a usable key (and without selecting a model), it
prints guidance pointing you at the relevant `<PROVIDER>_API_KEY` variable and
the `--model` flag. In the non-interactive modes (`--print` / `--mode json`)
this is a hard error with a non-zero exit code, so scripts fail fast instead of
hanging.
