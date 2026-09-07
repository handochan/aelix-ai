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
| `apiKey`         | string  | Required for a new provider that defines `models`. Supports indirection (below). |
| `api`            | string  | Adapter id, e.g. `openai-completions` / `anthropic-messages`.|
| `headers`        | object  | Extra request headers (string → string).                    |
| `authHeader`     | boolean | When `true`, send `Authorization: Bearer <apiKey>`.         |
| `compat`         | object  | Provider compatibility overrides.                            |
| `models`         | array   | Model definitions for this provider.                        |
| `modelOverrides` | object  | Field overrides keyed by model id (see below).              |

A provider id that matches a **built-in** provider extends it; you may omit
`baseUrl`/`apiKey` and just add `models` or `modelOverrides`. A **new** provider
that defines its own `models` requires both `baseUrl` and `apiKey`; a new
provider that only adds `modelOverrides` (or `headers`/`compat`) does not.

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
flat default — which is how `{ "id": "claude-opus-5" }` on the built-in
`anthropic` provider silently gives you this:

| field | you get | the real Claude Opus 4.8 entry |
|---|---|---|
| `contextWindow` | 128000 | 1000000 |
| `maxTokens` | 16384 | 128000 |
| `reasoning` | `false` | `true` |
| `input` | `["text"]` | `["text", "image"]` |
| `cost` | all zero | 5.0 / 25.0 / 0.5 / 6.25 |

Nothing warns you. The model appears in `/model` and runs, but the context meter
is wrong by 8×, thinking is off, images are refused, and `/cost` reports nothing.
So when you add a current flagship, spell the whole entry out:

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

  **Which shell runs it.** On macOS and Linux it is `sh -c`, as it always was.
  On Windows Aelix now resolves one instead of assuming `sh` — `$SHELL` (when it
  names a file that exists) → `sh` on `PATH` → `pwsh` → `powershell` →
  `%COMSPEC%` → `cmd.exe` — and takes the first that starts. **`sh` is no longer
  required**, so a `!command` works on a stock install; a box that has an `sh`
  (Git for Windows, MSYS2, Cygwin) and no `SHELL` set keeps running its
  `!command`s under it.

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
  second against `sh`'s three milliseconds, and the key is re-resolved on every
  request, so on a box that lands on PowerShell prefer an environment variable —
  or export `$SHELL` to a real `bash.exe`, **which also tells the bash tool and
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
  provider `headers`.

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

## Verifying

After editing `models.json`, confirm your models appear:

```bash
aelix --list-models my-provider
```

An invalid file fails fast at startup with a schema error that names the
offending path (e.g. `providers.my-provider.baseUrl`).
