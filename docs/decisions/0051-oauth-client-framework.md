# 0051. OAuth Client Framework

Status: Accepted (Sprint 6c / Phase 4.3 / W6 shipped)

## Context

Pi `packages/ai/src/utils/oauth/` (SHA `734e08e`) ships a 7-file OAuth
client framework: `types.ts` (Protocol + dataclasses), `pkce.ts` (RFC
7636 PKCE generator), `oauth-page.ts` (success/error HTML for the
local callback server), `anthropic.ts` (the first provider — owned by
ADR-0052), `github-copilot.ts` (deferred to Sprint 6e), `openai-codex.ts`
(deferred to Sprint 6e), and `index.ts` (provider registry + high-level
`getOAuthApiKey`).

W0 verified Pi `oauth.ts` is a 40-byte re-export; the real surface lives
under `utils/oauth/`. Aelix ports under
`packages/aelix-ai/src/aelix_ai/oauth/` (the `utils/` namespace is JS
noise).

The framework is **separate** from `aelix_ai.api_registry` (ADR-0045) —
OAuth providers are not API providers; one OAuth provider can target
multiple APIs (Copilot has its own base URL per subscription tier).

## Decision

Aelix ships the OAuth client framework with the following surface:

### Types — `aelix_ai.oauth.types`

| Aelix name | Pi name | Pi citation |
|---|---|---|
| `OAuthCredentials` (dataclass) | `OAuthCredentials` | `types.ts:3-8` |
| `OAuthPrompt` | `OAuthPrompt` | `types.ts:15-19` |
| `OAuthAuthInfo` | `OAuthAuthInfo` | `types.ts:21-24` |
| `OAuthSelectOption` | `OAuthSelectOption` | `types.ts:26-29` |
| `OAuthSelectPrompt` | `OAuthSelectPrompt` | `types.ts:31-34` |
| `OAuthLoginCallbacks` | `OAuthLoginCallbacks` | `types.ts:36-44` |
| `OAuthProvider` (Protocol) | `OAuthProviderInterface` | `types.ts:46-64` |

`OAuthCredentials` is a flat dataclass with a Pi-equivalent
`extra: dict[str, Any]` field for the `[key: string]: unknown` index
signature; `to_json()` merges extras at the top level (Pi shape),
`from_json()` rehydrates with unknown keys landing in `extra`.

Sprint 6c W6 (W4 m1): `from_json` raises a clear `ValueError` naming
the missing required fields instead of bubbling a bare `KeyError`.

Sprint 6c W6 (W4 m7): `_exchange_authorization_code` and
`refresh_anthropic_token` (ADR-0052) preserve unknown response fields
(notably `scope`) in `OAuthCredentials.extra` so the round-trip is
Pi-equivalent.

### PKCE — `aelix_ai.oauth._pkce`

RFC 7636 PKCE generator using `secrets.token_bytes(32)` +
`hashlib.sha256` + base64url encoding (no padding). Pi parity for
verifier byte length, challenge algorithm, and charset.

### Callback server — `aelix_ai.oauth._callback_server`

A `http.server.HTTPServer` running on a daemon thread, fronted by an
`asyncio.Future` that the request handler resolves via
`loop.call_soon_threadsafe`. Public API:

- `start_callback_server(state, *, host="127.0.0.1", port=53692, path="/callback")`
- Returns `CallbackServerInfo(redirect_uri, wait_for_code, cancel_wait, shutdown)`
- `state` validation matches Pi: incoming `state` MUST equal `state`,
  otherwise 400 with `oauth_error_html("State mismatch.")`.

Sprint 6c W6 fixes layered onto this surface:

- **W4 m3 / W5 P-99** — use `asyncio.get_running_loop()` (deprecated
  `get_event_loop` in 3.12+, removed in 3.14+).
- **W4 m4** — `BaseHTTPRequestHandler.log_message` parameter name
  matches the base class (`format`, not `_format`) so Pyright accepts
  the override.
- **W4 m8** — port-in-use raises a `RuntimeError` with a fix-it message
  ("Set `PI_OAUTH_CALLBACK_HOST` or close the other process") instead
  of a bare `OSError`.

### Registry — `aelix_ai.oauth._registry`

Mirrors Pi `index.ts:29-95` `Map<string, OAuthProviderInterface>` with
`register_oauth_provider` / `unregister_oauth_provider` (restores
built-ins) / `reset_oauth_providers` / `get_oauth_provider` /
`get_oauth_providers`. Built-in list contains ONLY Anthropic in Sprint
6c; Copilot + Codex tracked in `_OAUTH_DEFERRED_PROVIDERS`.

### High-level — `aelix_ai.oauth._high_level`

`get_oauth_api_key_from_credentials(provider_id, credentials)` is the
direct port of Pi `index.ts:127-152` `getOAuthApiKey`: looks up
credentials by id, refreshes when expired
(`time.time() * 1000 >= creds.expires`), returns
`OAuthRefreshResult(new_credentials, api_key)`.

### Forward-compat clause (binding)

- `_OAUTH_DEFERRED_PROVIDERS` contains `{"github-copilot", "openai-codex"}`
  with owning ADR-0053; any PR landing one MUST drop the entry in the
  same PR (enforced by `tests/pi_parity/test_phase_4_3_strict_superset.py`).
- `_PHASE_4_DEFERRED_FEATURES` contains `{"rpc-mode", "auth-storage-layered-resolution"}`
  with owning ADR-0054 / ADR-0053 respectively.
- The `modify_models` OAuth Protocol method is declared `Optional[Callable]`
  but unwired in 6c; Sprint 6e wires it for Copilot.

## Consequences

- Sprint 6c ships 1 of 3 Pi OAuth providers live (Anthropic);
  Copilot + Codex follow in Sprint 6e per ADR-0053.
- The framework is the durable boundary every future OAuth provider
  routes through — Copilot's Sprint 6e implementation lands under the
  same Protocol with no framework change required.
- AuthStorage (ADR-0053) consumes the framework via
  `get_oauth_api_key_from_credentials`, layering JSON persistence on
  top of the in-memory Protocol surface.

## Related

- ADR-0034 — Pi reference version pin.
- ADR-0045 — Provider Adapter Interface (separate registry, same
  pattern).
- ADR-0052 — Anthropic OAuth flow (first provider).
- ADR-0053 — AuthStorage + secrets layer.
- ADR-0055 — Phase 4.3 strict superset closure.

## Phase

Sprint 6c / Phase 4.3 (shipped — framework + 1 of 3 providers live).
