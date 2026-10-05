# Custom Models (`models.json`)

Status: Accepted

`models.json` lets you add your own providers and models, point at custom base
URLs, attach per-provider or per-model headers, and override fields on the
built-in catalog. It is the configuration counterpart to the built-in model
catalog (see ADR-0140 for the loader design).

## Location

```text
~/.aelix/agent/models.json
```

The file is optional. When present it is read on startup, validated, and merged
onto the built-in catalog.

## Shape

The top level is a single required `providers` object keyed by provider id:

```json
{
  "providers": {
    "my-provider": {
      "baseUrl": "https://api.example.com/v1",
      "apiKey": "MY_PROVIDER_API_KEY",
      "api": "openai-completions",
      "models": [
        {
          "id": "my-model-large",
          "name": "My Model (large)",
          "contextWindow": 128000,
          "maxTokens": 8192,
          "cost": { "input": 0.5, "output": 1.5, "cacheRead": 0, "cacheWrite": 0 }
        }
      ]
    }
  }
}
```

A custom model reuses the existing API adapters via its provider's `api` field
(e.g. `openai-completions`, `anthropic-messages`) — you do not implement a new
provider in code.

### Provider fields

| Field            | Type    | Notes                                                        |
| ---------------- | ------- | ------------------------------------------------------------ |
| `baseUrl`        | string  | Required for a new provider that defines `models`. API endpoint base URL. |
| `apiKey`         | string  | Required for a new provider that defines `models`. Supports indirection (below). Comes before the provider's environment variable ([which key is sent](#which-key-a-request-carries)). |
| `api`            | string  | Adapter id, e.g. `openai-completions` / `anthropic-messages`.|
| `headers`        | object  | Extra request headers (string → string).                    |
| `authHeader`     | boolean | When `true`, send `Authorization: Bearer <apiKey>`.         |
| `compat`         | object  | Provider compatibility overrides.                            |
| `models`         | array   | Model definitions for this provider.                        |
| `modelOverrides` | object  | Field overrides keyed by model id (see below).              |

A provider id that matches a **built-in** provider extends it; you may omit
`baseUrl`/`apiKey` and just add `models` or `modelOverrides` — or give it only an
`apiKey` (or only `authHeader`), to use your own key for it ahead of the
environment. A **new** provider that defines its own `models` requires both
`baseUrl` and `apiKey`; a new provider that only adds `modelOverrides` (or
`headers`/`compat`) does not. An entry with none of these is refused.

### Re-pointing a built-in provider with `baseUrl`

A provider-level `baseUrl` on a **built-in** provider moves every one of its
catalog models to that endpoint — a corporate gateway or a local proxy in front
of OpenAI, say:

```json
{
  "providers": {
    "openai": { "baseUrl": "https://llm-gateway.corp.example/v1", "apiKey": "CORP_GATEWAY_KEY" }
  }
}
```

Every path that picks a model sends it to that host — `--provider openai --model
gpt-4o-mini`, `--model openai/gpt-4o-mini`, the `/model` picker and a settings
default — with the provider's `headers`. (Until
[ADR-0249](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0249-a-model-openrouter-cannot-serve-is-resolved-before-openrouter-from-env.md)
the launch path returned the static catalog entry and quietly sent those two
commands to `api.openai.com` instead.) Every one of those paths uses the same
model: the catalog's protocol (`api`) is kept, and the entry's `compat` and
`modelOverrides` apply at launch exactly as in the `/model` picker (until
[ADR-0251](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0251-own-api-key-before-environment-and-composed-launch-model.md)
the launch took only the host).

**Your gateway receives this file's `apiKey`** — see
[Which key a request carries](#which-key-a-request-carries). Here that is the value
of `CORP_GATEWAY_KEY`, even with `OPENAI_API_KEY` in your shell or in a project's
`.env`.

A re-pointed built-in also counts as **your** provider in the rules of
[providers-and-models.md](providers-and-models.md#how-a---model-string-becomes-a-provider)
([ADR-0250](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md)):
`--model openai/<id>` then goes to your `baseUrl` and never to OpenRouter —
including an OpenRouter-only spelling such as `openai/gpt-4o:extended`, which your
gateway will have to reject. Use `openrouter/<id>` or `--provider openrouter` for
those. A **bare** id is matched across every provider: a plain
`--model gpt-4o-mini` that several providers list goes to the one you hold a
credential for (this `apiKey` counts), else it is refused naming them; a slashed
id such as `anthropic/claude-sonnet-4.5` that a gateway of yours lists verbatim is
your gateway's when you hold no key of your own for `anthropic`, or when your
gateway is the only one of the authenticated matches that you defined.
`modelOverrides`, `headers` or `compat` **alone** do not re-point anything and do
not make the provider yours; neither does a `baseUrl` on an individual model
definition (only the provider-level one does).

**`{NAME}` placeholders in a `baseUrl`** are filled from the environment when the
request is built, as pi does for Cloudflare's `{CLOUDFLARE_ACCOUNT_ID}` /
`{CLOUDFLARE_GATEWAY_ID}`: `"baseUrl": "https://{TENANT}.gateway.example/v1"` with
`TENANT` exported reaches that tenant's host. A value from a project `.env` fills a
placeholder **only** for those two Cloudflare ids, which the `.env` loader accepts
only as plain ids, and only in the URL's path (after the host, before any `?`
or `#`) — never the host, port, user-info, query or fragment, even in a template
you wrote; any other variable, including one
you admit with `AELIX_DOTENV_ALLOW`, must come from your shell. A credential in a
`.env` authenticates a request but never addresses one: with `TENANT_KEY` only in a
cloned repo's `.env`, the token stays unfilled, the model is not runnable, and
`--model` / `/model` refuse with `TENANT_KEY came from a project .env, which may
authenticate a request but never address one; export it in your shell to use it.`
(otherwise a value such as `attacker.example/v1#` would have replaced your host —
[ADR-0250](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md)
§2.2).

### Model fields

`id` is required on a model definition. Other fields: `name`, `baseUrl`, `api`,
`reasoning` (boolean), `thinkingLevelMap`, `input`, `cost`, `contextWindow`,
`maxTokens`, `headers`, `compat`.

**`cost` is all-or-nothing.** You may omit it entirely, but if you include it,
all four of `input`, `output`, `cacheRead` and `cacheWrite` are required — a
`cost` with only `input` and `output` is a schema error, and a schema error
makes Aelix discard the **whole file**, not just that model. (This page's own
examples got that wrong until it was measured; they are correct above.)

### What you get if you leave a field out

Omitted fields do **not** inherit from a sibling model on the same provider.
`api` and `baseUrl` come from the provider, but everything else falls back to a
flat default. A `models` entry **replaces** a built-in row of the same
`(provider, id)` rather than merging onto it, so `{ "id": "claude-opus-5" }`
under the built-in `anthropic` provider silently downgrades the shipped entry to
this:

| field | you get | the built-in `claude-opus-5` row |
|---|---|---|
| `contextWindow` | 128000 | 1000000 |
| `maxTokens` | 16384 | 128000 |
| `reasoning` | `false` | `true` |
| `input` | `["text"]` | `["text", "image"]` |
| `cost` | all zero | 5.0 / 25.0 / 0.5 / 6.25 |

Nothing warns you. The model appears in `/model` and runs, but the context meter
is wrong by 8×, thinking is off, images are refused, and `/cost` reports nothing.
(If all you wanted was to change one field on a row that already exists, use
`modelOverrides` below — that one does merge.) So when you add a model, spell the
whole entry out:

```json
{
  "providers": {
    "anthropic": {
      "models": [
        {
          "id": "claude-opus-5",
          "name": "Claude Opus 5",
          "reasoning": true,
          "input": ["text", "image"],
          "contextWindow": 1000000,
          "maxTokens": 128000,
          "cost": {
            "input": 5.0,
            "output": 25.0,
            "cacheRead": 0.5,
            "cacheWrite": 6.25
          }
        }
      ]
    }
  }
}
```

Because `anthropic` is a **built-in** provider, `api` and `baseUrl` are
inherited and your existing credentials are reused — you are adding a row to a
catalog, not defining a provider. Copy the numbers from the vendor's own
documentation; `aelix --list-models` will show the model once the file loads.

## `apiKey` indirection

The `apiKey` value is resolved at request time and supports three forms:

- **Environment variable** — `"apiKey": "MY_PROVIDER_API_KEY"` reads
  `$MY_PROVIDER_API_KEY` (and falls back to using the string literally if that
  variable is unset or empty).
- **Shell command** — `"apiKey": "!op read op://vault/key"` runs the command and
  uses its trimmed stdout. Output is bounded (~1 MB / 10 s); a non-zero or empty
  result resolves to no key.

  The value it produces is then kept for the life of the model registry, so the
  command runs once per load and not once per request. Any turn that ends in an
  error drops it — a key your provider has started rejecting costs one bad turn,
  not the session — and in the interactive TUI `/reload`, `/login` and
  restarting Aelix drop it as well.

  In a headless run (`aelix -p …`) there is no `/reload` and no `/login`: an
  errored turn and process exit are the only two things that drop it. That
  matters if your helper mints a **short-lived** token, since one `-p` run is a
  single turn but many API requests over however long the work takes. Pair such
  a helper with a token whose lifetime comfortably exceeds the run.

  A command that fails or prints nothing is never kept, so a helper that starts
  working is picked up on the next request. An `apiKey` that names an
  environment variable is re-read every request; only the `!command` form is
  kept.

  A `!command` cannot prompt you. It runs in a process group of its own — never
  the terminal's foreground group — so the kernel stops it the moment it reads
  the terminal or turns echo off, unless it blocks or ignores that signal, which
  POSIX permits; then it succeeds and your terminal keeps the setting. When the
  stop does arrive, Aelix ends the command and tells you why instead of waiting
  out its ten-second timeout. (The resolver detects the stop in about 0.05 s,
  measured under a real pty on macOS and Linux; what you wait is that plus
  Aelix's own startup.) This is a deliberate difference from Pi, which leaves
  the helper in Pi's own process group — the terminal's foreground group
  whenever Pi is in the foreground — so the helper can prompt.

  So pick a helper that needs no terminal, and which one depends on the family:

  - **`ssh`, `sudo`, `git` and friends** — give them an askpass program
    (`SSH_ASKPASS`, `SUDO_ASKPASS` with `sudo -A`, `GIT_ASKPASS`) or read the
    secret from the OS keychain. These are the helpers that get the fast, named
    failure today: an `ssh` passphrase read, a `sudo` prompt and a `stty`-based
    git credential helper were all stopped and named in about 0.05 s.
  - **`gpg` and `pass`** — askpass is *not* their answer; they do not read
    `SSH_ASKPASS`, they ask `gpg-agent`, and `gpg-agent` forks `pinentry` in its
    own session where none of the above applies. **These still cost the full ten
    seconds with no named cause.** Unlock the key outside Aelix once and let the
    agent's cache answer (measured 0.115–0.118 s warm), preset it with
    `gpg-preset-passphrase`, use `--pinentry-mode loopback` with a passphrase
    file, or configure a GUI pinentry such as `pinentry-mac` (not measured
    here). A bare `gpg --pinentry-mode loopback` that falls through to reading
    the terminal itself *is* detected, at about 0.36 s.

  Two things this does not fix. An askpass program or a GUI prompt that nobody
  answers never reads the terminal, so it never stops — it costs the whole
  ten-second timeout, exactly as it did before. And a `!command` can still
  **write** to the terminal even though it cannot read it: `sudo` and `openssl`
  print their prompt first and fail after. If one of them leaves your terminal
  with echo off, `stty sane` (or `reset`) puts your terminal back.

  The same rule covers a `!command` in `auth.json`'s `key`, not just this file.

  **Which shell runs it.** On macOS and Linux it is `sh -c`, with the `sh`
  looked up on `PATH` and taken only from an absolute directory — before, the
  bare name was handed to the system, and an empty or `.` entry in your `PATH`
  meant a file named `sh` in the directory you started Aelix in could run your
  credential command instead. On Windows Aelix resolves one instead of assuming
  `sh` — `$SHELL` (when it names a file that exists) → `sh` on `PATH` → `pwsh` →
  `powershell` → `%COMSPEC%` → `%SystemRoot%\System32\cmd.exe` → `cmd.exe` — and
  takes the first that starts. The `sh`, `pwsh` and `powershell` steps are the
  ones looked up on `PATH`, and they are looked up on `PATH` alone, never in the
  current directory: an entry that is not absolute is skipped. `%COMSPEC%` is
  taken only when it names an absolute path, and the `%SystemRoot%` step only
  when that file is really there. The two candidates Windows itself still
  resolves are the `$SHELL` you exported — taken verbatim, because that is you
  naming a shell rather than Aelix guessing one — and the last-resort bare
  `cmd.exe`, which is exactly why an existing `%SystemRoot%\System32\cmd.exe`
  goes in front of it.
  **`sh` is no longer required**, so a `!command` works on a stock install; a box
  that has an `sh` (Git for Windows, MSYS2, Cygwin) and no `SHELL` set keeps
  running its `!command`s under it.

  So **write the command for the shell that will run it**. One that only invokes
  a program — `!op read op://vault/key` — is portable. Shell syntax is not: a
  POSIX `$VAR` expansion resolves to empty under PowerShell and stays literal
  under `cmd`, and the resolver takes whatever a command that exited zero
  printed, so the wrong shell fails by handing you a wrong key rather than by
  failing.

  PowerShell is started with `-NoProfile`, because a profile that prints
  anything would otherwise be prepended to your key — measured on PowerShell 7,
  a banner-printing profile turned the resolved value into
  `profile-banner\nsk-KEY`. (`bash` has the same hole through `$BASH_ENV`, on
  the one candidate that can reach it — a `$SHELL` that names a real
  `bash.exe` — and Aelix does not close it. The counterpart flags would not:
  measured on bash 3.2.57, 4.4.20, 5.1.4, 5.2.21 and 5.2.37, `--noprofile
  --norc` STILL sources `$BASH_ENV` and still prepends its output to the
  key; only `-p` stops that, and it drops the rest of the user's environment
  too.) The cost is real: a PowerShell start is about half a
  second against `sh`'s three milliseconds, and the key is resolved once per
  registry load, so the first request after a load pays one PowerShell start —
  about half a second — for *each* distinct `!command` the provider uses (the
  `apiKey`, and each `!command` header value); a provider with a key plus a
  header pays about a second, once. Prefer an environment variable if that pause
  matters, or export `$SHELL` to a real `bash.exe`, **which also tells the bash tool and
  the AUTO permission gate that your shell is bash**, so commands there are read
  with the bash grammar rather than prompted for (see ADR-0237 and
  [#204](https://github.com/handochan/aelix-ai/issues/204)).

  On Windows none of this applies: there is no background process group to be
  stopped for, so a helper that reads the console directly can still put a
  prompt there — and if nobody answers it, the command costs the full ten
  seconds. Nobody has watched that happen.
  PowerShell is also started
  `-NonInteractive`, and what that adds is narrower than it sounds, covers
  PowerShell's own prompts only, and is not about that timeout. A plain
  `Read-Host` never waited: Aelix hands the shell `NUL` for stdin, so the read
  ends at EOF and returns empty — with its prompt text already prepended to
  your key. The flag refuses that read outright, so the text stays out
  (measured on PowerShell 7 on macOS: the prompt text is in the resolved value
  without the flag and gone with it, and both come back in about half a
  second). The PowerShell prompt that *does* cost the full ten seconds is the
  masked kind — `Read-Host -AsSecureString`, `Get-Credential` — which opens the
  console by name, where `NUL` cannot end it; the flag refuses that one too
  (read from PowerShell 7's sources, not watched). Windows PowerShell 5.1,
  which is what a stock box actually resolves, is a different implementation
  nobody has run any of this against. A helper that opens the console itself —
  `git`, `ssh`, `gpg` — is still not covered.
- **Literal** — any other string is used verbatim.

The same indirection applies to each value in a `headers` map.

## Which key a request carries

When several sources hold a key for the same provider, a request uses the first
of these
([ADR-0251](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0251-own-api-key-before-environment-and-composed-launch-model.md),
Pi's order):

1. `--api-key` for this run;
2. a credential stored with `/login` (`auth.json`) — it owns the provider,
   whatever it yields: if a stored OAuth login can no longer refresh, the request
   fails before anything is sent, with `OAuth refresh failed for <provider>: <why>.
   Run /login to sign in to <provider> again.` (it is not retried). An entry that
   gives no key at all — an `api_key` that is empty or whose `!command` prints
   nothing or fails, an OAuth login whose OAuth provider is not available in this session,
   an entry of an unknown type — fails the same way, naming the entry:
   `The auth.json entry for <provider> … Run /login to sign in to <provider> again,
   or remove the <provider> entry from <path>.` No key below is tried in its place;
3. this file's `apiKey` for the provider;
4. the provider's environment variable (`OPENAI_API_KEY`, …), whether you
   exported it or a project's `.env` supplied it;
5. a key an extension registered for the provider (until
   [#365](https://github.com/handochan/aelix-ai/issues/365) — Pi puts it with
   step 3).

This applies to every provider, re-pointed or not: an `apiKey` here beats an
exported vendor key. An extension that registers the same provider name changes
it in one of two ways. If the registration carries its own key, that key replaces
this file's `apiKey` and is tried at step 5, after the environment. If it carries
no key (only models, `headers` or `authHeader`), this file's `apiKey` stays at
step 3. A registration that carries a key, `headers` or `authHeader` — any of
them, even a key alone — replaces both this file's `headers` and its `authHeader`,
with nothing when it carries none. For an extension or a program embedding Aelix,
`ModelRegistry.get_provider_auth_status` reports the source in the same order,
`--api-key` first.

One exception comes from the indirection above: an `apiKey` that **names** an
environment variable reads that variable, so `"apiKey": "OPENAI_API_KEY"` sends
whatever `OPENAI_API_KEY` holds — a project `.env` value included. To keep a
`.env` key away from a gateway, give the gateway's key as a literal, a
`!command`, or the name of a variable of your own. (Pi reads a bare value as a
literal and `$NAME` as a variable; Aelix keeps the bare-name form documented
above.)

## Custom headers and Bearer auth

```json
{
  "providers": {
    "my-gateway": {
      "baseUrl": "https://gateway.internal/v1",
      "apiKey": "GATEWAY_TOKEN",
      "api": "openai-completions",
      "authHeader": true,
      "headers": {
        "X-Org-Id": "ORG_ID_ENV",
        "X-Trace": "on"
      },
      "models": [
        { "id": "fast", "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } }
      ]
    }
  }
}
```

- `authHeader: true` injects `Authorization: Bearer <resolved apiKey>`. If no key
  resolves, auth fails with a clear error.
- `headers` values are merged into every request to this provider, each resolved
  through the same env-var / `!command` indirection. Per-model `headers` win over
  provider `headers`. A `!command` header value is kept exactly the way `apiKey`
  is — per-model and provider-level alike, one run per registry load — while an
  env-var one is re-read every request.

## Overriding a built-in model

Use `modelOverrides` (keyed by model id) to tweak fields on the built-in
catalog without redefining the model:

```json
{
  "providers": {
    "openai": {
      "modelOverrides": {
        "gpt-4o-mini": { "maxTokens": 4096 }
      }
    }
  }
}
```

An override applies wherever the model is chosen — `--model`, `--provider`, an
agent profile, a delegated agent and the `/model` picker alike — and so does a
provider-level `compat`. (Until
[ADR-0251](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0251-own-api-key-before-environment-and-composed-launch-model.md)
only `/model` applied them; a model chosen at launch kept the catalog's values.)
Overrides never change a built-in model's protocol (`api`).

## Verifying

After editing `models.json`, confirm your models appear:

```bash
aelix --list-models my-provider
```

and that a turn reaches your endpoint:

```bash
aelix --model my-provider/my-model -p "hi"
```

That goes to `my-provider`'s `baseUrl` whatever keys you hold — a prefix that
names one of your providers is resolved inside it, and a bare id only one
provider lists (`--model my-model`) is that provider's. The prefix is
matched case-insensitively, and your provider wins over a built-in whose name
differs from it only in case (a provider named `OpenAI` takes `openai/…`). The
same holds for `--provider`, `defaultProvider` in `settings.json` and an agent
profile's `provider:`: they are matched case-insensitively, so `--provider
openai` (or `OPENAI`) with a provider named `OpenAI` is yours — your host and
your `--api-key`, never `api.openai.com` — and a built-in `openai` re-pointed by a
`baseUrl` here is reached whatever the case. A name or prefix two of your
providers share up to case is refused naming both. An id
your provider does not list is backfilled from its own models when they all use
one `api` (so a model you pulled into a local server after writing this file
still works), and refused otherwise — it never falls through to OpenRouter.

An invalid file fails fast at startup with a schema error that names the
offending path (e.g. `providers.my-provider.baseUrl`).
