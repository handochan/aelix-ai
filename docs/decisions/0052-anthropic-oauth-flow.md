# 0052. Anthropic OAuth Flow

Status: Accepted (Sprint 6c / Phase 4.3 / W6 shipped)

## Context

Pi `packages/ai/src/utils/oauth/anthropic.ts` (SHA `734e08e`, ~400 LOC)
implements the Anthropic Claude Pro/Max OAuth flow:

1. Generate PKCE verifier + challenge.
2. Start a local HTTP callback server on `127.0.0.1:53692`.
3. Construct the `claude.ai/oauth/authorize` URL and present it to the
   user (via `callbacks.onAuth`).
4. Race the server callback against `callbacks.onManualCodeInput` (so
   users on a different machine can paste the redirect URL).
5. Exchange the authorization code for tokens via
   `platform.claude.com/v1/oauth/token`.
6. Refresh tokens via the same endpoint with `grant_type=refresh_token`.

Sprint 6c is the **first** OAuth provider implementation in Aelix.

## Decision

Aelix ships `aelix_ai.oauth.anthropic` as a verbatim port of Pi's flow.

### Constants — Pi parity verbatim

| Constant | Value |
|---|---|
| `ANTHROPIC_OAUTH_ID` | `"anthropic"` |
| `AUTHORIZE_URL` | `https://claude.ai/oauth/authorize` |
| `TOKEN_URL` | `https://platform.claude.com/v1/oauth/token` |
| `CALLBACK_HOST` | `os.environ.get("PI_OAUTH_CALLBACK_HOST", "127.0.0.1")` |
| `CALLBACK_PORT` | `53692` |
| `CALLBACK_PATH` | `/callback` |
| `REDIRECT_URI` | `http://localhost:53692/callback` |
| `SCOPES` | `org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload` |
| `CLIENT_ID` (base64-decoded) | UUID (36 chars, 4 hyphens) |
| `_TOKEN_TIMEOUT_SECONDS` | `30.0` |
| `_EXPIRES_SAFETY_MARGIN_MS` | `5 * 60 * 1000` (Pi parity for proactive refresh) |

### Bearer header injection (Sprint 6c W6 — P-94)

**Background:** The official Anthropic Python SDK (`>=0.40,<1.0`) does
NOT auto-detect OAuth tokens. Passing a bearer token as `api_key`
results in `x-api-key: sk-ant-oat-…` on the wire, which Anthropic
rejects with 401. The original W2 implementation relied on a Sprint 6c
P-92 verification claim that turned out to be wrong (W5 confirmed the
SDK has no auto-detect path).

**Fix (P-94, W6):** When `is_oauth_token(opts.api_key)` is true, the
Sprint 6a `providers/anthropic.py` adapter builds the SDK client with:

```python
oauth_headers = dict(opts.headers or {})
oauth_headers["Authorization"] = f"Bearer {opts.api_key}"
oauth_headers.setdefault("anthropic-beta", "oauth-2025-04-20")
client = create_async_client(
    api_key="",  # blank — auth comes from Authorization header
    base_url=model.base_url or None,
    default_headers=oauth_headers,
    timeout_ms=opts.timeout_ms,
    max_retries=opts.max_retries,
)
```

`setdefault` for `anthropic-beta` lets callers override the beta flag
without losing the OAuth bearer header. Caller-supplied headers
(`opts.headers`) are merged so traces / experiment flags survive.

Regression-pinned by:

- `tests/oauth/test_anthropic_adapter_oauth_passthrough.py::test_oauth_token_sets_bearer_header`
- `tests/oauth/test_anthropic_adapter_oauth_passthrough.py::test_non_oauth_token_keeps_api_key_path`
- `tests/oauth/test_anthropic_adapter_oauth_passthrough.py::test_oauth_path_preserves_user_supplied_headers`
- `tests/oauth/test_anthropic_adapter_oauth_passthrough.py::test_oauth_path_caller_can_override_anthropic_beta`
- `tests/oauth/test_anthropic_adapter_oauth_refresh_failure.py` (E2E)

### Late-manual-input fallback (Sprint 6c W6 — P-93 / W4 M4)

Pi `anthropic.ts:294-307` runs a three-way race: server callback,
manual-input task, and (only after both fail) `on_prompt` fallback.
When `server.wait_for_code()` resolves to `None` first and
`manual_input` is still empty, Pi awaits the manual task ONE MORE TIME
before falling through to `on_prompt`.

The original W2 implementation cancelled the manual task unconditionally
in a `finally` block, dropping the late-arrival path. The W6 fix
restructures the race per Pi:

1. `await server.wait_for_code()` — race condition primary.
2. If server returned a real code, use it.
3. Else if manual_input arrived, use it.
4. Else if manual_task still running, `await` it one more time (Pi
   parity — late-arrival path).
5. Only THEN cancel any still-running manual task.

Regression: `tests/oauth/test_anthropic.py::test_login_late_manual_input_after_server_cancel_wins`.

### CALLBACK_HOST env override wiring (Sprint 6c W6 — P-98 / W4 M6)

The module-level `CALLBACK_HOST = os.environ.get("PI_OAUTH_CALLBACK_HOST", "127.0.0.1")`
was previously dead code — `start_callback_server` was called WITHOUT
the `host=` kwarg. The W6 fix wires `host=CALLBACK_HOST,
port=CALLBACK_PORT, path=CALLBACK_PATH` explicitly so the env override
actually takes effect.

Regression: `tests/oauth/test_anthropic.py::test_login_anthropic_passes_callback_host_to_server`.

### Scope preservation in extras (Sprint 6c W6 — W4 m7)

`_exchange_authorization_code` and `refresh_anthropic_token` now build
`extras = {k: v for k, v in token_data.items() if k not in {"access_token", "refresh_token", "expires_in"}}`
and pass it to `OAuthCredentials(extra=extras)`. This preserves the
`scope` field (and any future provider-specific fields) Pi-style.

Regressions:
- `tests/oauth/test_anthropic.py::test_exchange_authorization_code_preserves_scope_in_extras`
- `tests/oauth/test_anthropic.py::test_refresh_anthropic_token_preserves_scope_in_extras`

## Consequences

- The Sprint 6a `_AuthError` eager-raise on OAuth-token detection
  (ADR-0035 placeholder) is removed; `_AuthError` now fires only when
  the SDK returns 401/403 (Sprint 6c amendment to ADR-0035).
- OAuth tokens actually work in production — without P-94 every OAuth
  call would 401.
- The OAuth flow integrates with `AuthStorage` (ADR-0053) via
  `get_oauth_api_key_from_credentials`: harness wires
  `AuthStorage.get_oauth_api_key("anthropic")` into the
  `get_api_key_and_headers` callback; the resolved token is passed to
  `providers/anthropic.py` which routes via `Authorization: Bearer`.
- `PI_OAUTH_CALLBACK_HOST` env override is now functional — useful in
  CI where 127.0.0.1 isn't routable from the browser host.

## Related

- ADR-0035 — Error code taxonomy (Sprint 6c amendment: `"auth"` now
  fires on SDK 401/403, not eager OAuth detection).
- ADR-0045 — Provider Adapter Interface (the Anthropic adapter that
  injects the bearer header).
- ADR-0051 — OAuth client framework (the substrate this flow plugs
  into).
- ADR-0053 — AuthStorage + secrets layer (the persistence wrapper).
- ADR-0055 — Phase 4.3 strict superset closure.

## Phase

Sprint 6c / Phase 4.3 (shipped — Anthropic flow live + bearer header
injection + late-manual-input fallback + CALLBACK_HOST wiring).
