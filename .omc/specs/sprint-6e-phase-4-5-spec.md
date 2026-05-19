# Sprint 6e · Phase 4.5 — Copilot + Codex OAuth + AuthStorage Cascade + `aelix auth` CLI (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-19
Pi pin (ADR-0034): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint closes the OAuth provider catalog (Anthropic + Copilot + Codex = full Pi 3-provider built-in set), wires the Pi-parity AuthStorage layered cascade (`setRuntimeApiKey` + `hasAuth` + `getAuthStatus` + `setFallbackResolver` + `drainErrors` + `login` + `logout` + `getApiKey(includeFallback)`), activates the `modify_models` Protocol callback (Sprint 6c P-102 forward-compat), and ships the `aelix auth login/logout/status` CLI subcommands.

Sprint 6f remains the ModelRegistry + 4 RPC commands (`set_model`/`cycle_model`/`get_available_models`/`get_commands`) sprint.

---

## §0 — W0 INVESTIGATION FINDINGS (Pi drift verified at SHA 734e08e)

### P-130 — Copilot OAuth uses device-code grant (NOT PKCE-callback like Anthropic/Codex)

Pi `oauth/github-copilot.ts:167-280`:
- Step 1: prompt for enterprise domain (optional) → `normalizeDomain(input)` → defaults to `"github.com"`
- Step 2: POST `<domain>/login/device/code` → receive `{device_code, user_code, verification_uri, interval, expires_in}`
- Step 3: `onAuth({url: verification_uri, instructions: "Enter code: <user_code>"})` — user manually navigates
- Step 4: poll `<domain>/login/oauth/access_token` every `interval` seconds (Pi multiplies by 1.2 initially, 1.4 on `slow_down`)
- Step 5: on `access_token` received → POST `/copilot_internal/v2/token` to get Copilot-flavored bearer (with `proxy-ep=<host>` field)
- Step 6: `enableGitHubCopilotModel()` POST to `/models/{id}/policy` per active model (deferred per §J)

**Decision:** Aelix mirrors device-code flow verbatim. NO callback server (unlike Anthropic). The `OAuthProvider.uses_callback_server` field is `False` for Copilot. Poll loop uses `asyncio.sleep(interval * INITIAL_MULTIPLIER)` with `slow_down` backoff to `interval * SLOW_DOWN_MULTIPLIER`.

### P-131 — Codex OAuth is PKCE-callback like Anthropic but with DIFFERENT port (1455) + path (`/auth/callback`)

Pi `oauth/openai-codex.ts:154-220`:
- `CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"`
- `AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"`
- `TOKEN_URL = "https://auth.openai.com/oauth/token"`
- `REDIRECT_URI = "http://localhost:1455/auth/callback"`
- `SCOPE = "openid profile email offline_access"`
- Custom auth params: `id_token_add_organizations=true` + `codex_cli_simplified_flow=true`
- After token exchange: extract `chatgpt_account_id` from JWT payload at claim path `https://api.openai.com/auth`; throw if missing

**Decision:** Aelix reuses the `_callback_server.py` helper from Sprint 6c (it already accepts `host`/`port`/`path` kwargs). Constants: `CALLBACK_PORT=1455`, `CALLBACK_PATH="/auth/callback"`. The `chatgpt_account_id` is stored in `OAuthCredentials.extra` (per P-86 extensible field).

### P-132 — Pi `OAuthProvider.modify_models` exists in types but is unwired in Sprint 6c

Pi `oauth/types.ts:51`:
```typescript
modifyModels?(models: Model<Api>[], credentials: OAuthCredentials): Model<Api>[];
```

Aelix Sprint 6c `OAuthProvider` Protocol carries the field as **optional** (per Sprint 6c P-102 forward-compat clause). Sprint 6e is the first sprint that wires it: Copilot needs `modify_models` to inject the `proxy-ep=<host>` value from the token into every Copilot-routed `Model.base_url`.

**Decision:** Sprint 6e:
- Updates `OAuthProvider` Protocol with optional `modify_models: Callable | None = None` (the field already exists per Sprint 6c)
- Wires `_AnthropicOAuthProvider.modify_models = None` (unchanged)
- Wires `_GithubCopilotOAuthProvider.modify_models = _modify_copilot_models` to inject base_url
- Wires `_OpenAICodexOAuthProvider.modify_models = None` (codex routes via standard openai-responses adapter; account_id flows via header, not base_url)

### P-133 — AuthStorage layered cascade (Pi `auth-storage.ts:154-375`) is the spec §J carry-forward from Sprint 6c

Pi exposes 8 methods Aelix Sprint 6c skipped. Per Sprint 6c W5 P-95 (deferred to Sprint 6e):

| Pi method | Behavior |
|---|---|
| `setRuntimeApiKey(provider, key)` | In-memory `runtimeOverrides` Map (NOT persisted to auth.json) |
| `removeRuntimeApiKey(provider)` | drop from map |
| `setFallbackResolver(resolver)` | last-resort `(provider) => string | undefined` callback |
| `hasAuth(provider)` | `runtime → stored → env → fallback` boolean |
| `getAuthStatus(provider)` | returns `{configured: bool, source?: AuthSource, label?: str}` |
| `list()` | Object.keys of stored data |
| `has(provider)` | `provider in stored data` |
| `getAll()` | shallow copy of stored data |
| `drainErrors()` | returns + clears accumulated load/save errors |
| `login(providerId, callbacks)` | calls `provider.login()` + persists via `set_oauth` |
| `logout(provider)` | alias for `remove` |
| `getApiKey(provider, {includeFallback?})` | layered cascade: runtime → stored api_key → OAuth (auto-refresh) → env → fallback (if includeFallback) |

**Decision:** Aelix adds all 12 methods. The cascade in `getApiKey()` strictly follows Pi precedence. The `AuthSource` enum mirrors Pi: `"stored" | "runtime" | "environment" | "fallback" | "models_json_key" | "models_json_command"`. The last two (`models_json_*`) are tracked but unused in Sprint 6e (no models.json plumbing yet).

### P-134 — Pi `pollForGitHubAccessToken` honors `slow_down` returned by GitHub

Pi `github-copilot.ts:188-226`:
```typescript
while (Date.now() < deadline) {
  const response = await fetch(accessTokenUrl, {body: {client_id, device_code, grant_type: "urn:ietf:params:oauth:grant-type:device_code"}});
  if (data.access_token) return data;
  if (data.error === "authorization_pending") { await sleep(currentInterval); continue; }
  if (data.error === "slow_down") { currentInterval *= 1.4; slowDownCount++; await sleep(currentInterval); continue; }
  throw new Error(`Unexpected: ${data.error}`);
}
throw new Error("Login timeout");
```

**Decision:** Aelix port mirrors verbatim using `httpx.AsyncClient`. The `slow_down` backoff multiplies the wait interval persistently (not just one tick). Includes the `clock-drift diagnostics` slow_down count tracking.

### P-135 — Pi Copilot `getBaseUrlFromToken(token)` parses `proxy-ep=<host>;...` from token

Pi `github-copilot.ts:70-80`: Copilot tokens encode the API proxy endpoint in semicolon-delimited fields (`tid=...;exp=...;sku=...;proxy-ep=copilot-proxy.githubusercontent.com;...`). The `proxy-ep` field becomes the API base URL for all Copilot requests.

**Decision:** Aelix port: `_get_copilot_base_url_from_token(token: str) -> str` — split on `;`, find `proxy-ep=`, prepend `https://` and append `/v1` (or whatever Pi appends). Used by `modify_models` callback.

### P-136 — Pi Codex JWT account_id extraction is critical

Pi `openai-codex.ts:18-22, 281`:
```typescript
const JWT_CLAIM_PATH = "https://api.openai.com/auth";
function getAccountId(accessToken: string): string {
  const payload = decodeJwtPayload(accessToken);
  const accountId = payload[JWT_CLAIM_PATH]?.["chatgpt_account_id"];
  if (!accountId) throw new Error("Missing chatgpt_account_id");
  return accountId;
}
```

After token exchange, Pi extracts and stores `chatgpt_account_id` in credentials. If missing, the OAuth flow FAILS (the account ID is required for subsequent ChatGPT API calls).

**Decision:** Aelix port:
- Decode JWT payload via `base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))` then `json.loads`
- Extract `payload["https://api.openai.com/auth"]["chatgpt_account_id"]`
- Store in `OAuthCredentials.extra["chatgpt_account_id"]`
- Raise `RuntimeError("Missing chatgpt_account_id in Codex OAuth response")` if missing

### P-137 — `aelix auth` CLI subcommand surface

Pi's CLI has interactive login flow (pi-binary). Aelix CLI gets these subcommands:
- `aelix auth login <provider>` — invoke `AuthStorage.login(provider, callbacks)` with stdin-based prompt callbacks
- `aelix auth logout <provider>` — `AuthStorage.logout(provider)`
- `aelix auth status [provider]` — `AuthStorage.get_auth_status(provider)` (omitted provider → list all)
- `aelix auth list` — `AuthStorage.list()` + status per entry

The CLI invocation flow uses stdin/stdout for `onPrompt` callbacks (paste authorization code, enter enterprise domain, etc.) and stdout for `onAuth({url})` user-facing instructions.

---

## §A — Scope (binding)

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_ai/oauth/github_copilot.py` (NEW — Copilot device-code provider) | ~350 | ~200 |
| `aelix_ai/oauth/openai_codex.py` (NEW — Codex PKCE provider) | ~280 | ~180 |
| `aelix_ai/oauth/auth_storage.py` (EXTENDED — 12 cascade methods + AuthStatus) | ~200 | ~150 |
| `aelix_ai/oauth/types.py` (AMEND — AuthSource enum + AuthStatus dataclass) | ~30 | ~20 |
| `aelix_ai/oauth/_registry.py` (AMEND — add Copilot + Codex to built-ins, drop from `_OAUTH_DEFERRED_PROVIDERS`) | ~15 | ~30 |
| `src/aelix/__main__.py` (AMEND — `aelix auth login/logout/status/list` subcommands) | ~150 | ~100 |
| Pi parity closure pin (`test_phase_4_5_strict_superset.py`) | — | ~80 |
| **Totals** | **~1,025** | **~760** |

**Total ~1,785 LOC** — fits the Sprint 6c size envelope.

### NOT in scope (deferred per §J)

- **ModelRegistry** + model catalog port + `set_model`/`cycle_model`/`get_available_models` RPC commands — Sprint 6f
- **`get_commands` RPC command** (extension/skill/template aggregation) — Sprint 6f (needs ModelRegistry + extension surface)
- **`enableGitHubCopilotModel()` automatic POST** after login — Sprint 6f (needs ModelRegistry for per-model policy)
- **AuthStorage `models_json_key` + `models_json_command` sources** — Sprint 7+ (models.json plumbing not yet ported)
- **Codex `chatgpt_account_id` propagation into request headers** — Sprint 6f (paired with OpenAI Responses adapter for Codex API)
- **Pi `--api-key <provider>:<key>` CLI flag** — Sprint 6f (paired with ModelRegistry; surfaces `setRuntimeApiKey`)

---

## §B — `aelix_ai/oauth/types.py` (AMEND)

Add:

```python
@dataclass(frozen=True)
class AuthStatus:
    """Pi parity: ``auth-storage.ts::AuthStatus`` (lines 31-35)."""
    configured: bool
    source: Literal[
        "stored", "runtime", "environment", "fallback",
        "models_json_key", "models_json_command",
    ] | None = None
    label: str | None = None


# Type alias for fallback resolver callback (Pi: ``(provider) => string | undefined``)
FallbackResolver = Callable[[str], str | None]
```

The `OAuthProvider` Protocol already declares `modify_models: Callable | None = None` per Sprint 6c P-102 — Sprint 6e doesn't need to amend the Protocol.

---

## §C — `aelix_ai/oauth/github_copilot.py` (NEW)

Port Pi `oauth/github-copilot.ts:1-314` verbatim:

```python
"""GitHub Copilot OAuth flow (device-code grant).

Pi parity: ``packages/ai/src/utils/oauth/github-copilot.ts`` (SHA 734e08e).

Unlike Anthropic's PKCE-callback flow, Copilot uses GitHub's device-code
grant: user manually enters a code at a URL, agent polls for token.
"""

import base64
from typing import Any
import httpx

# Pi parity: github-copilot.ts:13
_CLIENT_ID_B64: str = "SXYxLmI1MDdhMDhjODdlY2ZlOTg="
CLIENT_ID: str = base64.b64decode(_CLIENT_ID_B64).decode("ascii")

# Pi parity: github-copilot.ts:15-20
COPILOT_HEADERS: dict[str, str] = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}

# Pi parity: github-copilot.ts:22-23
POLL_INITIAL_MULTIPLIER: float = 1.2
POLL_SLOW_DOWN_MULTIPLIER: float = 1.4
DEFAULT_DOMAIN: str = "github.com"


def normalize_domain(input_str: str) -> str | None:
    """Pi parity: github-copilot.ts:47-55."""
    # Parse URL, return hostname or None.


def _get_urls(domain: str) -> dict[str, str]:
    """Pi parity: github-copilot.ts:57-65."""
    return {
        "device_code_url": f"https://{domain}/login/device/code",
        "access_token_url": f"https://{domain}/login/oauth/access_token",
        "copilot_token_url": f"https://api.{domain}/copilot_internal/v2/token",
    }


def _get_copilot_base_url_from_token(token: str) -> str | None:
    """Pi parity: github-copilot.ts:70-80.

    Copilot tokens encode the API proxy endpoint:
    ``tid=...;exp=...;proxy-ep=<host>;...``
    """


async def _start_device_flow(domain: str) -> dict[str, Any]:
    """Pi parity: github-copilot.ts:84-97."""


async def _poll_for_github_access_token(
    domain: str, device_code: str, interval: int, expires_in: int,
) -> dict[str, Any]:
    """Pi parity: github-copilot.ts:188-226.

    Handles authorization_pending (continue), slow_down (backoff),
    timeout (after expires_in seconds).
    """


async def login_github_copilot(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Pi parity: github-copilot.ts:167-280."""


async def refresh_github_copilot_token(refresh_token: str, enterprise_domain: str | None = None) -> OAuthCredentials:
    """Pi parity: github-copilot.ts:142-165."""


def _modify_copilot_models(models: list, credentials: OAuthCredentials) -> list:
    """Pi parity: github-copilot.ts modifyModels.

    Inject the proxy-ep base URL from the Copilot token into every
    Copilot-routed Model.base_url. Sprint 6e wires this as the first
    use of the modify_models Protocol callback (P-132).
    """


class _GithubCopilotOAuthProvider:
    id = "github-copilot"
    name = "GitHub Copilot"
    uses_callback_server = False  # device-code flow has no callback

    async def login(self, callbacks): return await login_github_copilot(callbacks)
    async def refresh_token(self, credentials): return await refresh_github_copilot_token(credentials.refresh, credentials.extra.get("enterprise_domain"))
    def get_api_key(self, credentials): return credentials.access
    def modify_models(self, models, credentials): return _modify_copilot_models(models, credentials)


GITHUB_COPILOT_OAUTH_PROVIDER = _GithubCopilotOAuthProvider()
```

---

## §D — `aelix_ai/oauth/openai_codex.py` (NEW)

Port Pi `oauth/openai-codex.ts:1-346` verbatim:

```python
"""OpenAI Codex (ChatGPT Plus/Pro) OAuth flow.

Pi parity: ``packages/ai/src/utils/oauth/openai-codex.ts`` (SHA 734e08e).

PKCE callback flow similar to Anthropic, but at port 1455 path
``/auth/callback`` (NOT 53692/``/callback``), with ChatGPT-specific
JWT account_id extraction post-exchange.
"""

import base64
import json
import os
from typing import Any
import httpx
from aelix_ai.oauth._callback_server import start_callback_server
from aelix_ai.oauth._pkce import generate_pkce
from aelix_ai.oauth.types import (
    OAuthCredentials, OAuthLoginCallbacks, OAuthAuthInfo,
)

# Pi parity: openai-codex.ts:13-22
CLIENT_ID: str = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL: str = "https://auth.openai.com/oauth/authorize"
TOKEN_URL: str = "https://auth.openai.com/oauth/token"
CALLBACK_HOST: str = os.environ.get("PI_OAUTH_CODEX_CALLBACK_HOST", "127.0.0.1")
CALLBACK_PORT: int = 1455
CALLBACK_PATH: str = "/auth/callback"
REDIRECT_URI: str = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPE: str = "openid profile email offline_access"
JWT_CLAIM_PATH: str = "https://api.openai.com/auth"


def _decode_jwt_payload(jwt_token: str) -> dict[str, Any]:
    """Decode the payload of a JWT WITHOUT signature verification.
    Used to extract chatgpt_account_id from access_token.
    """
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise RuntimeError("Invalid JWT structure (expected 3 parts)")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload_bytes = base64.urlsafe_b64decode(padded)
    return json.loads(payload_bytes)


def _get_account_id(access_token: str) -> str:
    """Pi parity: openai-codex.ts:18-22, :281.

    Extracts chatgpt_account_id from JWT at claim path. Raises if missing.
    """
    payload = _decode_jwt_payload(access_token)
    auth_claims = payload.get(JWT_CLAIM_PATH, {})
    account_id = auth_claims.get("chatgpt_account_id")
    if not account_id:
        raise RuntimeError("Missing chatgpt_account_id in Codex OAuth token")
    return account_id


async def login_openai_codex(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Pi parity: openai-codex.ts:198-310 (loginOpenAICodex)."""


async def refresh_openai_codex_token(refresh_token: str) -> OAuthCredentials:
    """Pi parity: openai-codex.ts:315-327."""


class _OpenAICodexOAuthProvider:
    id = "openai-codex"
    name = "ChatGPT Plus/Pro (Codex Subscription)"
    uses_callback_server = True

    async def login(self, callbacks): return await login_openai_codex(callbacks)
    async def refresh_token(self, credentials): return await refresh_openai_codex_token(credentials.refresh)
    def get_api_key(self, credentials): return credentials.access
    # modify_models intentionally None — account_id flows via header, not base_url


OPENAI_CODEX_OAUTH_PROVIDER = _OpenAICodexOAuthProvider()
```

### D.1 Custom auth params (Pi `:170`)

When building the AUTHORIZE_URL query:
```python
params = {
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "scope": SCOPE,
    "code_challenge": challenge,
    "code_challenge_method": "S256",
    "state": verifier,
    "id_token_add_organizations": "true",
    "codex_cli_simplified_flow": "true",
}
```

### D.2 Token exchange extras

After token exchange, store in `OAuthCredentials.extra`:
- `chatgpt_account_id` (extracted via `_get_account_id`)
- `id_token` (if present in token response — needed for some ChatGPT endpoints)

---

## §E — `aelix_ai/oauth/auth_storage.py` (EXTEND)

Add 12 cascade methods to `AuthStorage`:

```python
class AuthStorage:
    # ... existing __init__ + load/save/get_oauth/set_oauth/get_api_key/set_api_key/remove/get_oauth_api_key ...

    # NEW Sprint 6e attributes
    _runtime_overrides: dict[str, str] = field(default_factory=dict)
    _fallback_resolver: FallbackResolver | None = None
    _errors: list[Exception] = field(default_factory=list)

    # ── Runtime override layer ─────────────────────────────────────
    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        """Pi parity: auth-storage.ts:154-156."""
        self._runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        """Pi parity: auth-storage.ts:161-163."""
        self._runtime_overrides.pop(provider, None)

    # ── Fallback resolver ───────────────────────────────────────────
    def set_fallback_resolver(self, resolver: FallbackResolver) -> None:
        """Pi parity: auth-storage.ts:168-170."""
        self._fallback_resolver = resolver

    # ── Existence + status checks ───────────────────────────────────
    async def has_auth(self, provider: str) -> bool:
        """Pi parity: auth-storage.ts:220-226.
        runtime → stored → env → fallback.
        """

    async def get_auth_status(self, provider: str) -> AuthStatus:
        """Pi parity: auth-storage.ts:231-249.
        Returns {configured, source, label} — does NOT expose the key itself.
        """

    # ── Stored data accessors ───────────────────────────────────────
    def list(self) -> list[str]:
        """Pi parity: auth-storage.ts:261-263."""

    def has(self, provider: str) -> bool:
        """Pi parity: auth-storage.ts:268-270."""

    def get_all(self) -> dict[str, Any]:
        """Pi parity: auth-storage.ts:269-271 (shallow copy)."""

    # ── Error accumulator ───────────────────────────────────────────
    def drain_errors(self) -> list[Exception]:
        """Pi parity: auth-storage.ts:273-277."""

    # ── High-level login/logout ─────────────────────────────────────
    async def login(self, provider_id: str, callbacks: OAuthLoginCallbacks) -> None:
        """Pi parity: auth-storage.ts:282-290.
        Calls provider.login(callbacks) then persists via set_oauth.
        """

    async def logout(self, provider: str) -> None:
        """Pi parity: auth-storage.ts:295-297. Alias for remove."""

    # ── Layered cascade ─────────────────────────────────────────────
    async def get_api_key_cascade(
        self,
        provider_id: str,
        *,
        include_fallback: bool = True,
    ) -> str | None:
        """Pi parity: auth-storage.ts:323-375 (``getApiKey`` with includeFallback).

        Precedence (Pi-strict):
        1. runtime override (`_runtime_overrides[provider_id]`)
        2. stored api_key (existing get_api_key)
        3. stored OAuth (auto-refresh via get_oauth_api_key)
        4. env var (get_env_api_key from Sprint 6b _env_api_keys)
        5. fallback resolver (if include_fallback)
        """
```

**NB:** The existing `get_api_key` method is the PERSISTENCE layer (stored api_key only). The new `get_api_key_cascade` is the LAYERED layer. To preserve back-compat, the existing `get_api_key` stays unchanged; the new method has a distinct name. (Pi has `getApiKey` for both with a flag — Aelix opts for explicit method names.)

---

## §F — `aelix_ai/oauth/_registry.py` (AMEND)

Drop `github-copilot` and `openai-codex` from `_OAUTH_DEFERRED_PROVIDERS`. Add to built-ins:

```python
from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER
from aelix_ai.oauth.github_copilot import GITHUB_COPILOT_OAUTH_PROVIDER
from aelix_ai.oauth.openai_codex import OPENAI_CODEX_OAUTH_PROVIDER

_BUILT_IN_OAUTH_PROVIDERS: Final[list[OAuthProvider]] = [
    ANTHROPIC_OAUTH_PROVIDER,
    GITHUB_COPILOT_OAUTH_PROVIDER,
    OPENAI_CODEX_OAUTH_PROVIDER,
]
_OAUTH_DEFERRED_PROVIDERS: Final[dict[str, str]] = {}  # empty after 6e
```

The Sprint 6c closure pin `test_phase_4_3_strict_superset.py::test_pi_oauth_provider_total_equals_3` continues to pass: live (3) + deferred (0) = 3.

**Closure pin update (Sprint 6e):**
- Assert `len(get_oauth_providers()) == 3` (live)
- Assert `_OAUTH_DEFERRED_PROVIDERS == {}` (drained)
- Assert each of the 3 providers exposes `modify_models` Protocol attribute (None or callable)

---

## §G — `src/aelix/__main__.py` (AMEND)

Add `auth` subcommand parser. Existing `--mode {interactive,rpc}` flag is preserved at top level. New surface:

```python
# Top-level: aelix <subcommand> [flags]
#   <none>             — interactive REPL (default behavior preserved)
#   --mode rpc         — RPC mode (Sprint 6d behavior preserved)
#   auth login <id>    — invoke AuthStorage.login(id, cli_callbacks)
#   auth logout <id>   — invoke AuthStorage.logout(id)
#   auth status [id]   — print AuthStatus for id (or all if omitted)
#   auth list          — list all stored providers + status

import argparse, asyncio, sys
from aelix_ai.oauth import AuthStorage, OAuthAuthInfo, OAuthPrompt

def _cli_callbacks() -> OAuthLoginCallbacks:
    def on_auth(info: OAuthAuthInfo) -> None:
        print(f"\nOpen this URL in your browser:\n  {info.url}")
        if info.instructions:
            print(f"\n{info.instructions}")
    def on_prompt(prompt: OAuthPrompt) -> str:
        ph = f" [{prompt.placeholder}]" if prompt.placeholder else ""
        return input(f"\n{prompt.message}{ph}: ")
    def on_progress(msg: str) -> None:
        print(f"... {msg}", flush=True)
    return OAuthLoginCallbacks(
        on_auth=on_auth, on_prompt=on_prompt, on_progress=on_progress,
    )

async def _cmd_auth_login(provider_id: str) -> int:
    storage = AuthStorage()
    await storage.load()
    await storage.login(provider_id, _cli_callbacks())
    print(f"✓ Logged in: {provider_id}")
    return 0

async def _cmd_auth_logout(provider_id: str) -> int: ...
async def _cmd_auth_status(provider_id: str | None) -> int: ...
async def _cmd_auth_list() -> int: ...
```

### G.1 CLI test strategy

Tests use `subprocess.run([sys.executable, "-m", "aelix", "auth", "status"])` against a temp `AELIX_AUTH_PATH` to verify the wire. The `auth login` path is heavier (needs to mock OAuth providers); skip end-to-end and unit-test `_cmd_auth_login` with patched `AuthStorage.login`.

---

## §H — Tests (binding plan, ~760 LOC)

### Unit
- `tests/oauth/test_github_copilot.py` (~200 LOC):
  - `normalize_domain` valid + invalid inputs
  - `_get_urls` builds correct paths per domain
  - `_get_copilot_base_url_from_token` parses `proxy-ep=` correctly + handles missing field
  - `_start_device_flow` with mocked httpx → returns expected shape
  - `_poll_for_github_access_token` handles `authorization_pending` (continues), `slow_down` (backs off 1.4×), success, timeout
  - `login_github_copilot` full flow with mocked HTTP + callbacks
  - `refresh_github_copilot_token` happy path + error
  - `_modify_copilot_models` injects base_url for Copilot-routed models, leaves others untouched
- `tests/oauth/test_openai_codex.py` (~180 LOC):
  - `_decode_jwt_payload` decodes valid JWT, raises on malformed
  - `_get_account_id` extracts `chatgpt_account_id`, raises if missing
  - `login_openai_codex` full PKCE flow with mocked HTTP + callback server (use `_callback_server` infra from Sprint 6c with port=1455, path="/auth/callback")
  - `refresh_openai_codex_token` happy path + error
- `tests/oauth/test_auth_storage_cascade.py` (~150 LOC):
  - `set_runtime_api_key` + `remove_runtime_api_key` round-trip
  - `set_fallback_resolver` wires custom resolver
  - `has_auth` checks all 4 layers (runtime / stored / env / fallback)
  - `get_auth_status` returns correct source per layer
  - `list()` / `has()` / `get_all()` consistent with stored data
  - `drain_errors` returns + clears
  - `login(provider_id, callbacks)` calls provider.login + persists
  - `logout(provider)` aliases remove
  - `get_api_key_cascade` precedence order verified (runtime > stored > OAuth > env > fallback)
  - `include_fallback=False` skips fallback layer
- `tests/oauth/test_types_authstatus.py` (~20 LOC):
  - `AuthStatus` round-trip; source enum

### Integration
- `tests/oauth/test_copilot_modify_models_integration.py` (~30 LOC):
  - Wire `GITHUB_COPILOT_OAUTH_PROVIDER.modify_models` and assert Model.base_url is injected
- `tests/cli/test_auth_subcommand.py` (~100 LOC):
  - `aelix auth status` (no provider) → lists configured + unconfigured
  - `aelix auth status anthropic` → single-provider report
  - `aelix auth list` → JSON-shaped list
  - `aelix auth logout anthropic` → removes entry; verify file content

### Pi parity closure pin
- `tests/pi_parity/test_phase_4_5_strict_superset.py` (~80 LOC):
  - 3 OAuth providers live (Anthropic + Copilot + Codex)
  - `_OAUTH_DEFERRED_PROVIDERS == {}` (drained)
  - Each provider's `modify_models` attribute is `None` or callable (Copilot has callable, Anthropic+Codex have None)
  - AuthStorage exposes 12 cascade methods by name
  - `AuthStatus.source` Literal values match Pi's 6-value enum
  - Copilot constants match fixture (CLIENT_ID, COPILOT_HEADERS, poll multipliers, default domain)
  - Codex constants match fixture (CLIENT_ID, CALLBACK_PORT=1455, CALLBACK_PATH="/auth/callback", JWT_CLAIM_PATH)

---

## §I — ADRs

### Amend
- **ADR-0034** — add row: "Sprint 6e shipped Copilot + Codex OAuth providers + AuthStorage layered cascade (12 methods) + `aelix auth` CLI. OAuth catalog complete (3/3 Pi providers)."
- **ADR-0053** — `0053-auth-storage-and-secrets.md` (Sprint 6c) — amend to mark layered cascade carry-forward as RESOLVED in Sprint 6e.

### NEW
- **ADR-0059** — `0059-copilot-oauth-device-flow.md` — Pi parity port of Copilot device-code grant + enterprise domain support + proxy-ep base URL extraction + modify_models callback.
- **ADR-0060** — `0060-codex-oauth-pkce-flow.md` — Pi parity port of Codex PKCE callback at port 1455 + JWT account_id extraction.
- **ADR-0061** — `0061-auth-storage-layered-cascade.md` — Pi parity port of 12 AuthStorage cascade methods + AuthStatus type + FallbackResolver protocol.
- **ADR-0062** — `0062-aelix-auth-cli-subcommand.md` — `aelix auth login/logout/status/list` CLI surface.
- **ADR-0063** — `0063-phase-4-5-strict-superset-closure.md` — closure pin. Roster: P-130 ~ P-137. Carry-forward to Sprint 6f (ModelRegistry + 4 RPC commands + `enableGitHubCopilotModel` automation).

### README
Update `docs/decisions/README.md` with 5 new ADR rows + Sprint 6e sub-table.

---

## §J — Forward-compat clause (binding)

After Sprint 6e:
- `_OAUTH_DEFERRED_PROVIDERS` is **EMPTY** — full Pi 3-provider built-in set live.
- Any future PR adding a new OAuth provider (e.g., custom enterprise IDP) MUST register via `register_oauth_provider()` AND add a closure pin assertion for the new provider's constants.
- `AuthStorage.get_api_key_cascade` is the **canonical** key resolution path going forward. New callers MUST use it (not `get_api_key` which is the persistence-only accessor) UNLESS they specifically want to bypass runtime/env/fallback layers.
- `modify_models` Protocol callback is **wired and tested** for Copilot. Future providers needing model surface modification (e.g., per-credential base URL) should implement it.

Sprint 6f scope (carry-forward owners):
- **ModelRegistry + model catalog port** — owns `set_model`/`cycle_model`/`get_available_models` RPC commands
- **`enableGitHubCopilotModel()` automation** — owns per-model `/models/{id}/policy` POST after Copilot login
- **`--api-key <provider>:<key>` CLI flag** — surfaces `setRuntimeApiKey`
- **`models_json_key` + `models_json_command` AuthStorage sources** — owns models.json plumbing
- **Codex `chatgpt_account_id` header propagation** — paired with OpenAI Responses adapter for Codex API

---

## §K — Sprint workflow (ADR-0032)

- W0 — research ✓ DONE
- W1 — this spec (binding)
- W2 — executor opus implements §B~§G
- W3 — verification (pytest + ruff + pyright spike preserve 8-error baseline)
- W4 — code-reviewer opus (parallel with W5)
- W5 — architect opus Pi parity audit (parallel with W4)
- W6 — apply must-fixes + atomic commits + ADRs accepted

**Atomic commit plan (W6, 5 commits):**
1. `feat: oauth — Copilot device-code provider + proxy-ep base URL + modify_models callback (ADR-0059, P-130/P-134/P-135)`
2. `feat: oauth — Codex PKCE provider (port 1455) + JWT account_id extraction (ADR-0060, P-131/P-136)`
3. `feat: oauth/auth_storage — 12 cascade methods + AuthStatus + FallbackResolver (ADR-0061, P-133)`
4. `feat: cli — aelix auth login/logout/status/list subcommands (ADR-0062, P-137)`
5. `test: Sprint 6e — N new tests + 1 Pi-parity fixture + Phase 4.5 closure pin + docs (ADRs 0034 amend + 0053 amend + NEW 0059-0063 + README + spec)`

---

## §L — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 1051 baseline + ~100 new ≈ 1151+; 0 fail |
| ruff check | clean |
| pyright spike | 8 errors (baseline preserved) |
| Sprint 6c closure pin | NO regressions — Anthropic OAuth + AuthStorage unchanged |
| Pi parity closure | `_OAUTH_DEFERRED_PROVIDERS == {}`; 12 AuthStorage cascade methods present |
| Atomic commit count | exactly 5 |

---

**End of binding spec. Architect READ-ONLY until W6.**
