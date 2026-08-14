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
          "cost": { "input": 0.5, "output": 1.5 }
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
`maxTokens`, `headers`, `compat`. `cost` is required on a new model definition
(but optional inside `modelOverrides`).

## `apiKey` indirection

The `apiKey` value is resolved at request time and supports three forms:

- **Environment variable** — `"apiKey": "MY_PROVIDER_API_KEY"` reads
  `$MY_PROVIDER_API_KEY` (and falls back to using the string literally if that
  variable is unset or empty).
- **Shell command** — `"apiKey": "!op read op://vault/key"` runs the command and
  uses its trimmed stdout. Output is bounded (~1 MB / 10 s); a non-zero or empty
  result resolves to no key.
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
        { "id": "fast", "cost": { "input": 0, "output": 0 } }
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
