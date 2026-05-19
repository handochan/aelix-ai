# 0060. OpenAI Codex OAuth (PKCE Callback Flow)

Status: Accepted (Sprint 6e / Phase 4.5 / W6 shipped)

## Context

Pi `packages/ai/src/utils/oauth/openai-codex.ts` (SHA `734e08e`,
~460 LOC) implements OpenAI Codex (ChatGPT Plus/Pro) OAuth via a
PKCE callback flow — similar to the Sprint 6c Anthropic flow but
with a different port (1455) and callback path (`/auth/callback`),
plus a critical post-exchange step that extracts
`chatgpt_account_id` from the access-token JWT payload at claim path
`https://api.openai.com/auth`.

Sprint 6c left this surface in `_OAUTH_DEFERRED_PROVIDERS` (owner
ADR-0053). Sprint 6e ports it verbatim and accumulates two
**deliberate strengthenings** over the Pi raw implementation: a
JWT base64url decoder that corrects a known Pi bug, and
preservation of the `id_token` extras field that Pi treats as
ephemeral.

## Decision

Aelix ships `aelix_ai.oauth.openai_codex` mirroring Pi's
`openai-codex.ts`:

### Constants (Pi parity verbatim)

| Constant | Pi line | Aelix |
|---|---|---|
| `CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"` | openai-codex.ts:24 | verbatim |
| `AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"` | openai-codex.ts:25 | verbatim |
| `TOKEN_URL = "https://auth.openai.com/oauth/token"` | openai-codex.ts:26 | verbatim |
| `CALLBACK_PORT = 1455` | openai-codex.ts:27 | `1455` |
| `CALLBACK_PATH = "/auth/callback"` | openai-codex.ts:28 | `"/auth/callback"` |
| `SCOPE = "openid profile email offline_access"` | openai-codex.ts:29 | verbatim |
| `JWT_CLAIM_PATH = "https://api.openai.com/auth"` | openai-codex.ts:30 | verbatim |
| `ORIGINATOR_DEFAULT = "pi"` | openai-codex.ts (Sprint 6e P-140) | `"pi"` — Pi advertises the OAuth caller via this param; missing from the initial port |

### Flow (Pi `loginOpenAICodex` parity)

1. Generate PKCE verifier + challenge + 16-byte hex `state`.
2. Start the local callback server (`_callback_server.py` from
   Sprint 6c — already accepts `host`/`port`/`path` kwargs) at
   `127.0.0.1:1455/auth/callback`.
3. Construct the authorize URL with custom params:
   `id_token_add_organizations=true` + `codex_cli_simplified_flow=true`
   + `originator=pi`.
4. Race manual-paste input against the callback server (same
   Sprint 6c late-manual-input pattern as Anthropic).
5. Exchange code → tokens (form-urlencoded POST to TOKEN_URL).
6. Extract `chatgpt_account_id` from JWT at `JWT_CLAIM_PATH`.

### Deliberate deviations from Pi (D.2-authorized strengthenings)

#### Base64url decode of JWT payload

Pi uses `atob` (JS standard-base64 decoder) to decode the JWT
payload segment. **Per RFC 7519 §3 (JSON Web Token spec), the
payload is base64url-encoded** (URL-safe alphabet with `-`/`_`
substitutions and no padding). Pi's `atob` decode happens to work
for payloads whose base64url representation is also valid standard
base64 — but breaks on any payload containing `-` or `_`. Aelix
uses `base64.urlsafe_b64decode` with explicit padding, correcting
the Pi bug:

```python
def _decode_jwt_payload(jwt_token: str) -> dict[str, Any] | None:
    parts = jwt_token.split(".")
    if len(parts) != 3:
        return None
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload_bytes = base64.urlsafe_b64decode(padded)
    return json.loads(payload_bytes)
```

The closure pin asserts a known-`-`/`_`-containing fixture decodes
cleanly through Aelix's path. Spec §D.2 authorizes this as a
"Aelix correctness strengthening over Pi raw"; the deviation is
documented here per ADR-0003 ("ADR 없는 divergence는 버그").

#### `id_token` extras preservation

Pi `openai-codex.ts:404-409` discards `id_token` after extraction —
treating it as ephemeral, used only for `chatgpt_account_id`
lookup. Aelix preserves it in `OAuthCredentials.extra["id_token"]`
so the persisted shape carries the full token-exchange response
(forward-compat for ID-token-based audit trails). Spec §D.2
authorizes this; the `to_json` round-trip flattens `extra` into the
top-level dict (Pi's `[key: string]: unknown` shape).

### `OAuthCredentials.extra` nesting idiom

Aelix programmatic shape uses **nested** `extra: dict[str, Any]`
(per ADR-0051 §B). The persisted JSON shape is **flat** — Pi's
`[key: string]: unknown` is restored by `to_json()` merging
`**self.extra` into the top-level dict:

```python
# Programmatic (Aelix):
OAuthCredentials(refresh="r", access="a", expires=1, extra={"accountId": "x"})

# Persisted JSON (Pi-flat):
{"refresh": "r", "access": "a", "expires": 1, "accountId": "x"}
```

`from_json` re-collects unknown keys into `extra` so the round-trip
is byte-stable.

### Sprint 6e W6 must-fix integration

| Finding | Subject | Resolution |
|---|---|---|
| **P-138** | Persisted key was `chatgpt_account_id` (Pi local var) — Pi persists camelCase `accountId` | `extra["accountId"] = account_id` (Pi `openai-codex.ts:404`) |
| **P-139** | `_get_account_id` raised on null; Pi returns null and caller raises | Returns `None` on any decode/claim failure; caller raises Pi error message verbatim |
| **P-140** | `originator=pi` URL param was missing | `ORIGINATOR_DEFAULT = "pi"` + wired in authorize URL |
| **P-149** | `PI_OAUTH_CODEX_CALLBACK_HOST` env override didn't fall back to shared `PI_OAUTH_CALLBACK_HOST` | Nested env lookup: codex-specific → shared → `"127.0.0.1"` |
| **P-150** | `_decode_jwt_payload` raised on malformed JWT; Pi `try/catch → null` | Catch all decode errors, return `None` |

### `modify_models` callback intentionally absent

Codex's `chatgpt_account_id` flows via **request header** (Sprint 6f
work paired with the OpenAI Responses adapter), not via
`Model.base_url`. Pi `openai-codex.ts:437-458` omits `modifyModels`
on the provider definition; Aelix mirrors this — `_OpenAICodexOAuthProvider`
does NOT define `modify_models`, and the closure pin asserts the
attribute is `None or callable` (the optional-callback Protocol clause).

### Closure pin assertions

```python
# tests/pi_parity/test_phase_4_5_strict_superset.py
OPENAI_CODEX_OAUTH_PROVIDER.id == "openai-codex"
OPENAI_CODEX_OAUTH_PROVIDER.uses_callback_server is True
getattr(OPENAI_CODEX_OAUTH_PROVIDER, "modify_models", None) in (None, ...)
CODEX_CALLBACK_PORT == 1455
CODEX_CALLBACK_PATH == "/auth/callback"
SCOPE == "openid profile email offline_access"
JWT_CLAIM_PATH == "https://api.openai.com/auth"
```

## Consequences

- ChatGPT Plus / Pro subscribers can sign in via `aelix auth login
  openai-codex` against `auth.openai.com`.
- The base64url decode fix means JWTs whose payloads contain `-`/
  `_` (common in production OAuth IDs) decode cleanly — a Pi-side
  bug not present in Aelix.
- The `id_token` preservation gives future ID-token-based features
  (audit trails, signed assertions) a foothold without a second
  OAuth round trip.
- The `originator=pi` restoration aligns Aelix's OAuth caller
  identity with Pi's; OpenAI's auth.openai.com can apply the same
  rate-limit / safety policies to both.

### Deferred to Sprint 6f

- **Codex `chatgpt_account_id` header propagation** — paired with
  the OpenAI Responses adapter so the runtime can dispatch requests
  to ChatGPT-flavored endpoints.
- **End-to-end round-trip integration test** (P-162) — needs the
  ModelRegistry hookup for full coverage.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6e — OAuth
  catalog now complete).
- ADR-0051 — OAuth client framework (provides the Protocol +
  PKCE + callback server that Codex reuses).
- ADR-0052 — Anthropic OAuth flow (the PKCE-callback template
  this ADR mirrors with different port/path).
- ADR-0053 — AuthStorage + secrets (Sprint 6e closes the Codex
  carry-forward).
- ADR-0055 — Phase 4.3 strict superset closure (the closure pin
  this ADR drains an entry from).
- ADR-0063 — Phase 4.5 strict superset closure.

## Phase

Sprint 6e / Phase 4.5 (shipped — Codex PKCE-callback OAuth live;
header propagation deferred to Sprint 6f Responses adapter).
