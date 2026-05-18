# Sprint 6c · Phase 4.3 — OAuth Client + Anthropic OAuth Flow + Secrets Storage (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-18
Pi pin (ADR-0034 amended): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint adds the OAuth client framework + the **first** OAuth provider (Anthropic Claude Pro/Max) + the secrets storage layer to Aelix. It also wires OAuth into Sprint 6a's `_AuthError` path so OAuth tokens (`sk-ant-oat…`) no longer eager-reject — they refresh through the new pipeline.

---

## §0 — W0 INVESTIGATION FINDINGS (Pi drift verified at SHA 734e08e)

### P-83 — Pi `oauth.ts` is a 40-byte re-export; real OAuth lives at `utils/oauth/`

W0 measurement against Pi at SHA `734e08e`:
- `packages/ai/src/oauth.ts` = **40 bytes** (`export * from "./utils/oauth/index.js";`)
- `packages/ai/src/utils/oauth/` contains 7 files totaling **~1,200 LOC**
- Real entry points: `loginAnthropic`, `refreshAnthropicToken`, `getOAuthApiKey`, `registerOAuthProvider`

**Decision:** Aelix ports under `packages/aelix-ai/src/aelix_ai/oauth/` (Pi's `utils/oauth/` flattened to the top-level package; the `utils` namespace is JS-specific noise).

### P-84 — Sprint 6c original estimate was 3.5× too low

Sprint 6a §0 sub-sprint split projected:
> | **6c** | OAuth + RPC mode (ADR-0020) + secrets ADR | ~700 prod + ~500 test |

W0 measurement:
| Surface | Pi LOC | Python est |
|---|---|---|
| `oauth/types.ts` | 60 | 50 |
| `oauth/pkce.ts` | 36 | 30 |
| `oauth/oauth-page.ts` | 100 | 50 |
| `oauth/anthropic.ts` | 400 | 350 |
| `oauth/github-copilot.ts` | 410 | (deferred) |
| `oauth/openai-codex.ts` | 460 | (deferred) |
| `oauth/index.ts` | 150 | 150 |
| `coding-agent/core/auth-storage.ts` | 470 | 350 |
| `coding-agent/modes/rpc/*` | 1,300 | (deferred) |
| **Sprint 6c (this sub-sprint)** | **1,216** | **~980 prod + ~700 test** |
| **Deferred to Sprint 6d (RPC)** | 1,300 | ~900 prod + ~600 test |
| **Deferred to Sprint 6e (Copilot/Codex OAuth)** | 870 | ~700 prod + ~400 test |

**Resolution:** Sprint 6c scopes to **OAuth framework + Anthropic provider only + secrets storage**. RPC mode + Copilot/Codex OAuth move to Sprint 6d + 6e.

### P-85 — Pi OAuth registry pattern mirrors ADR-0045 Provider Protocol

Pi `oauth/index.ts:29-95` registers providers in a `Map<string, OAuthProviderInterface>` with:
- `registerOAuthProvider(provider)`
- `unregisterOAuthProvider(id)` — restores built-in if applicable
- `getOAuthProvider(id)`
- `resetOAuthProviders()`

**Decision:** Aelix mirrors with `aelix_ai.oauth._registry`. Same pattern as `api_registry.register_provider_object` (ADR-0045) but a SEPARATE registry (OAuth providers are different from API providers — one provider can use multiple APIs).

### P-86 — Pi `OAuthCredentials` schema is **flat with extensible extra fields**

Pi `oauth/types.ts:3-8`:
```typescript
export type OAuthCredentials = {
    refresh: string;
    access: string;
    expires: number;
    [key: string]: unknown;
};
```

The `[key: string]: unknown` index signature is **load-bearing** — Copilot/Codex add provider-specific fields (`scope`, `endpoint`, etc.). Python equivalent: `@dataclass` with `extra: dict[str, Any] = field(default_factory=dict)` field, plus `to_dict()` / `from_dict()` methods that merge `extra` flat into the JSON.

### P-87 — Pi PKCE uses Web Crypto; Python uses `secrets` + `hashlib`

Pi `oauth/pkce.ts`:
- `crypto.getRandomValues(new Uint8Array(32))` → base64url verifier
- `crypto.subtle.digest("SHA-256", encoder.encode(verifier))` → base64url challenge

Python equivalent (RFC 7636 compliant):
```python
import secrets, hashlib, base64
def generate_pkce() -> tuple[str, str]:
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).decode().rstrip("=")
    challenge_bytes = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).decode().rstrip("=")
    return verifier, challenge
```

### P-88 — Pi Anthropic OAuth requires a local HTTP callback server on port 53692

Pi `oauth/anthropic.ts:97-165`:
- `createServer` (node:http) bound to `127.0.0.1:53692` (`PI_OAUTH_CALLBACK_HOST` overrides)
- Single route `/callback?code=&state=` validates `state == verifier` then resolves `waitForCode`
- Race handling: `onManualCodeInput` can cancel via `server.cancelWait()` so user can paste URL if browser is on another machine

Python port: `aiohttp.web.Application` OR built-in `http.server.HTTPServer` in a thread + asyncio bridge. **Decision:** built-in `http.server.HTTPServer` in a `concurrent.futures.ThreadPoolExecutor`, with `asyncio.get_event_loop().run_in_executor` bridge — adds zero new deps. Future hardening can swap for aiohttp.

### P-89 — `OAuthCredentials.expires` is unix-ms with a 5-min safety margin baked in at exchange time

Pi `oauth/anthropic.ts:221, 375`:
```typescript
expires: Date.now() + tokenData.expires_in * 1000 - 5 * 60 * 1000
```

The 5-min margin (`- 5 * 60 * 1000`) ensures `Date.now() >= creds.expires` triggers a refresh BEFORE the token actually expires server-side. Python ports this verbatim:
```python
expires = int(time.time() * 1000) + expires_in * 1000 - 5 * 60 * 1000
```

### P-90 — Pi has NO explicit "secrets" abstraction; secrets live in `coding-agent/core/auth-storage.ts`

W0 finding: `oauth/index.ts` does NOT manage storage — `getOAuthApiKey(providerId, credentials)` takes the credentials as an already-loaded `Record<id, OAuthCredentials>`. The actual `auth.json` read/write lives in `coding-agent/core/auth-storage.ts`:
- Path: `<agent-dir>/auth.json` (e.g., `~/.pi/agent/auth.json`)
- Parent dir: `0o700`; file: `0o600`
- Schema: `Record<provider-id, {type: "api_key", key: string} | ({type: "oauth"} & OAuthCredentials)>`
- Locking: `proper-lockfile` (npm) — cross-process safe with stale timeout

**Decision:** Aelix ships `aelix_ai.oauth.auth_storage.AuthStorage` (NEW class) at `packages/aelix-ai/src/aelix_ai/oauth/auth_storage.py`. Default path: `~/.config/aelix/agent/auth.json` (matches Aelix's existing config convention; NOT `~/.pi/`). Locking via Python `asyncio.Lock` in-process + `fcntl.flock` cross-process advisory lock (POSIX-only; document Windows TODO).

### P-91 — Sprint 6a's `_AuthError` eager-rejects OAuth tokens; Sprint 6c must replace with real refresh

Sprint 6a `packages/aelix-ai/src/aelix_ai/providers/anthropic.py:62` defines `_AuthError` and at `:???` raises it when the API key starts with `sk-ant-oat`. This was a Sprint 6a placeholder per ADR-0035.

**Decision:** Sprint 6c amends Sprint 6a's behavior:
- The adapter still detects OAuth tokens (`is_oauth_token`).
- When OAuth is detected, instead of raising `_AuthError`, the adapter relies on `get_api_key_and_headers` callback (the harness-side hook) to have ALREADY resolved the token via `AuthStorage.get_oauth_api_key("anthropic")`. The adapter receives a fresh access token in `options.api_key`.
- If `get_api_key_and_headers` is NOT wired (CLI hasn't supplied it) AND the bare API key is an OAuth token, raise `_AuthError("auth", "OAuth token detected but no AuthStorage configured — call AuthStorage.attach_to_harness(...) first")` with a more actionable message.
- The Sprint 6a regression test (`test_adapter_auth_error_translates_to_harness_auth_error`) becomes a regression for the "no AuthStorage" path; a NEW test covers the auto-refresh path.

### P-92 — `getApiKey()` returns the access token, NOT a derived header

Pi `oauth/anthropic.ts:397-399`:
```typescript
getApiKey(credentials): string {
    return credentials.access;
}
```

The wire convention for Anthropic OAuth is: the access token is used directly as the `x-api-key` header value AND/OR `Authorization: Bearer ...` header. Pi's Anthropic adapter at `providers/anthropic.ts` (Sprint 6a body) reads `apiKey` and constructs SDK headers accordingly. For Aelix, the OAuth flow is correct as long as the `get_api_key_and_headers` callback returns `{"apiKey": <oauth-access-token>, "headers": {...}}`.

The Pi SDK distinguishes OAuth-flavored Anthropic requests via the `Authorization: Bearer <token>` header (not `x-api-key`). **Sprint 6c needs to confirm**: when Aelix's Anthropic adapter (Sprint 6a) gets a token starting with `sk-ant-oat`, does it switch the auth header? Per Pi reference, the Anthropic SDK auto-detects this — both `apiKey: "sk-..."` (regular) and `apiKey: "sk-ant-oat..."` (OAuth) are accepted and the SDK routes to the right header. Sprint 6c assumes the Aelix `_anthropic_client.create_async_client` mirrors. **Verify in W2.**

---

## §A — Scope (binding)

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_ai/oauth/__init__.py` (package facade) | ~20 | — |
| `aelix_ai/oauth/types.py` (OAuthCredentials + callbacks + Protocol) | ~80 | ~50 |
| `aelix_ai/oauth/_pkce.py` (PKCE generator) | ~30 | ~40 |
| `aelix_ai/oauth/_oauth_page.py` (HTML constants) | ~50 | ~10 |
| `aelix_ai/oauth/_callback_server.py` (local HTTP callback) | ~150 | ~120 |
| `aelix_ai/oauth/anthropic.py` (Anthropic OAuth flow) | ~250 | ~150 |
| `aelix_ai/oauth/_registry.py` (registry + reset + built-ins) | ~80 | ~60 |
| `aelix_ai/oauth/auth_storage.py` (JSON storage + locking) | ~250 | ~150 |
| `aelix_ai/oauth/_high_level.py` (`get_oauth_api_key` auto-refresh) | ~70 | ~80 |
| Sprint 6a `anthropic.py` integration (replace `_AuthError` early-raise) | ~30 | ~40 |
| Pi parity closure pin (`test_phase_4_3_strict_superset.py`) | — | ~80 |
| **Totals** | **~1,010** | **~780** |

### NOT in scope (deferred per §J)
- GitHub Copilot OAuth (Sprint 6e)
- OpenAI Codex OAuth (Sprint 6e)
- RPC mode + JSONL protocol + rpc-client (Sprint 6d)
- Browser launch automation (`xdg-open` etc.) — Sprint 6c leaves the URL to user/CLI to open
- Windows cross-process file locking (POSIX-only `fcntl.flock` in 6c; Windows fallback in 6d)
- OAuth provider `modifyModels` callback (Pi has it; Aelix defers — no current built-in uses it)
- Encryption at rest (Pi has none; Aelix carries forward)
- Keyring integration (Pi has none; Aelix carries forward to Sprint 7+)
- `coding-agent` CLI subcommand `aelix auth login` (Sprint 6e — needs Copilot + Codex too)

---

## §B — `oauth/types.py` (NEW)

```python
from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class OAuthCredentials:
    """Pi parity: ``utils/oauth/types.ts:3-8``.

    Schema: ``refresh + access + expires + extensible extra``. ``expires``
    is unix-ms with a 5-min safety margin baked in at exchange time.
    """
    refresh: str
    access: str
    expires: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Serialize to flat dict (Pi shape) — extra fields merged at top level."""
        return {"refresh": self.refresh, "access": self.access, "expires": self.expires, **self.extra}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> OAuthCredentials:
        known = {"refresh", "access", "expires"}
        return cls(
            refresh=obj["refresh"],
            access=obj["access"],
            expires=int(obj["expires"]),
            extra={k: v for k, v in obj.items() if k not in known},
        )


@dataclass
class OAuthPrompt:
    """Pi parity: ``types.ts:15-19``."""
    message: str
    placeholder: str | None = None
    allow_empty: bool = False


@dataclass
class OAuthAuthInfo:
    """Pi parity: ``types.ts:21-24``."""
    url: str
    instructions: str | None = None


@dataclass
class OAuthSelectOption:
    id: str
    label: str


@dataclass
class OAuthSelectPrompt:
    message: str
    options: list[OAuthSelectOption]


@dataclass
class OAuthLoginCallbacks:
    """Pi parity: ``types.ts:36-42``.

    All callbacks are sync-or-async; the framework awaits any coroutine
    result via :func:`_maybe_await` (Sprint 6a pattern).
    """
    on_auth: Callable[[OAuthAuthInfo], None | Awaitable[None]]
    on_prompt: Callable[[OAuthPrompt], str | Awaitable[str]]
    on_progress: Callable[[str], None | Awaitable[None]] | None = None
    on_manual_code_input: Callable[[], str | Awaitable[str]] | None = None
    on_select: Callable[[OAuthSelectPrompt], str | None | Awaitable[str | None]] | None = None
    signal: Any | None = None  # AbortSignal-shaped (Sprint 6c uses asyncio.Event or trio.CancelScope-future)


@runtime_checkable
class OAuthProvider(Protocol):
    """Pi parity: ``types.ts:44-51``."""
    id: str
    name: str
    uses_callback_server: bool

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...
    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials: ...
    def get_api_key(self, credentials: OAuthCredentials) -> str: ...
```

---

## §C — `oauth/_pkce.py` (NEW)

```python
import base64, hashlib, secrets

def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def generate_pkce() -> tuple[str, str]:
    """Pi parity: ``utils/oauth/pkce.ts:23-35``. Returns ``(verifier, challenge)``."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = _base64url(verifier_bytes)
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge
```

Tests: known fixed-input vectors (`secrets` patched), assert length, assert characters in `[A-Za-z0-9_-]`, assert deterministic challenge for fixed verifier.

---

## §D — `oauth/_oauth_page.py` (NEW)

```python
def oauth_success_html(message: str) -> str:
    """Pi parity: ``utils/oauth/oauth-page.ts``. Returns minimal styled HTML."""
    return f"""<!doctype html>...{message}..."""

def oauth_error_html(title: str, details: str = "") -> str:
    """Pi parity: ``utils/oauth/oauth-page.ts``. Returns minimal styled HTML."""
    return f"""<!doctype html>...{title}...{details}..."""
```

Aelix may visually diverge from Pi's exact HTML (no Pi parity test for the HTML body); only the call sites matter (200 OK with text/html for success, 400 with text/html for error).

---

## §E — `oauth/_callback_server.py` (NEW)

```python
import asyncio
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import urlparse, parse_qs


@dataclass
class CallbackServerInfo:
    """Pi parity: ``oauth/anthropic.ts:13-18`` ``CallbackServerInfo``."""
    redirect_uri: str
    wait_for_code: Callable[[], "asyncio.Future[tuple[str, str] | None]"]
    cancel_wait: Callable[[], None]
    shutdown: Callable[[], None]


async def start_callback_server(
    expected_state: str,
    *,
    host: str = "127.0.0.1",
    port: int = 53692,
    path: str = "/callback",
) -> CallbackServerInfo:
    """Pi parity: ``oauth/anthropic.ts:97-165``.

    Launches a local HTTP server in a thread, returns:
    - ``redirect_uri`` formatted as ``http://localhost:<port><path>``
    - ``wait_for_code()`` awaitable resolving to ``(code, state)`` or ``None``
    - ``cancel_wait()`` to break out of ``wait_for_code()`` early
    - ``shutdown()`` to close the server (call in ``finally``)

    State validation: incoming ``state`` MUST equal ``expected_state``; otherwise
    serves 400 with ``oauth_error_html("State mismatch.")``.
    """
```

Implementation notes:
- Server runs in a `threading.Thread` (daemon=False)
- `wait_for_code()` returns an `asyncio.Future` (created via `asyncio.get_event_loop().create_future()`) that the request handler resolves via `loop.call_soon_threadsafe(future.set_result, ...)`
- `cancel_wait()` resolves the future with `None`
- `shutdown()` calls `server.shutdown()` + joins the thread (Pi's `server.close()` equivalent)

Tests: hit the callback URL with `httpx`, assert (a) valid code+state → 200 + future resolved; (b) state mismatch → 400; (c) missing code → 400; (d) other path → 404; (e) cancel_wait → future resolves to None.

---

## §F — `oauth/anthropic.py` (NEW)

Direct port of Pi `oauth/anthropic.ts:228-400`. Public API:

```python
ANTHROPIC_OAUTH_ID: str = "anthropic"
AUTHORIZE_URL: str = "https://claude.ai/oauth/authorize"
TOKEN_URL: str = "https://platform.claude.com/v1/oauth/token"
CALLBACK_HOST: str = os.environ.get("PI_OAUTH_CALLBACK_HOST", "127.0.0.1")
CALLBACK_PORT: int = 53692
CALLBACK_PATH: str = "/callback"
REDIRECT_URI: str = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPES: str = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"

# CLIENT_ID is base64-encoded in the Pi source for trivial obfuscation; Aelix mirrors.
_CLIENT_ID_B64: str = "OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl"
CLIENT_ID: str = base64.b64decode(_CLIENT_ID_B64).decode("ascii")

async def login_anthropic(callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...
async def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials: ...
async def _exchange_authorization_code(code: str, state: str, verifier: str, redirect_uri: str) -> OAuthCredentials: ...

class _AnthropicOAuthProvider:
    id = ANTHROPIC_OAUTH_ID
    name = "Anthropic (Claude Pro/Max)"
    uses_callback_server = True
    async def login(self, callbacks): return await login_anthropic(callbacks)
    async def refresh_token(self, credentials): return await refresh_anthropic_token(credentials.refresh)
    def get_api_key(self, credentials): return credentials.access

ANTHROPIC_OAUTH_PROVIDER = _AnthropicOAuthProvider()
```

HTTP client: `httpx.AsyncClient` (already a transitive dep of `openai`/`anthropic` SDKs); `signal=AbortSignal.timeout(30_000)` Pi equivalent is `httpx.Timeout(30.0)`.

The login flow MUST mirror Pi's manual-code-input race (`onManualCodeInput` cancels the server.wait if user pastes URL first). Use `asyncio.wait(..., return_when=FIRST_COMPLETED)` to race the manual input task vs. server.wait_for_code task.

---

## §G — `oauth/_registry.py` (NEW)

```python
from typing import Final
from aelix_ai.oauth.types import OAuthProvider
from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER

_BUILT_IN_OAUTH_PROVIDERS: Final[list[OAuthProvider]] = [ANTHROPIC_OAUTH_PROVIDER]
_oauth_registry: dict[str, OAuthProvider] = {p.id: p for p in _BUILT_IN_OAUTH_PROVIDERS}

def get_oauth_provider(id: str) -> OAuthProvider | None: ...
def register_oauth_provider(provider: OAuthProvider) -> None: ...
def unregister_oauth_provider(id: str) -> None: ...  # restores built-in if applicable
def reset_oauth_providers() -> None: ...
def get_oauth_providers() -> list[OAuthProvider]: ...
```

Pi parity: `oauth/index.ts:28-95`. Built-in list contains ONLY Anthropic in Sprint 6c; Copilot + Codex added in Sprint 6e.

---

## §H — `oauth/auth_storage.py` (NEW)

```python
import asyncio, fcntl, json, os, stat
from dataclasses import dataclass
from pathlib import Path
from aelix_ai.oauth.types import OAuthCredentials
from aelix_ai.oauth._registry import get_oauth_provider


@dataclass
class ApiKeyCredential:
    type: Literal["api_key"] = "api_key"
    key: str = ""

# Stored credential = api_key OR oauth
StoredCredential = ApiKeyCredential | tuple[Literal["oauth"], OAuthCredentials]


def default_auth_path() -> Path:
    """Pi parity: ``cli/config.ts::getAuthPath()`` (``<agent-dir>/auth.json``).
    Aelix default: ``~/.config/aelix/agent/auth.json``.
    """
    return Path(os.environ.get("AELIX_AUTH_PATH", "")) or (
        Path.home() / ".config" / "aelix" / "agent" / "auth.json"
    )


class AuthStorage:
    """Pi parity: ``core/auth-storage.ts`` (~470 LOC condensed to ~250).

    File format::

        {
          "<provider-id>": {"type": "api_key", "key": "sk-..."},
          "anthropic":     {"type": "oauth", "refresh": "...", "access": "...", "expires": 1234567890123, ...}
        }

    Permissions: parent dir 0700, file 0600.
    Locking: ``asyncio.Lock`` in-process + ``fcntl.flock`` advisory POSIX cross-process.
    """

    def __init__(self, path: Path | None = None) -> None: ...
    async def load(self) -> None: ...
    async def save(self) -> None: ...
    async def get_oauth(self, provider_id: str) -> OAuthCredentials | None: ...
    async def set_oauth(self, provider_id: str, credentials: OAuthCredentials) -> None: ...
    async def get_api_key(self, provider_id: str) -> str | None: ...
    async def set_api_key(self, provider_id: str, key: str) -> None: ...
    async def remove(self, provider_id: str) -> None: ...
    async def get_oauth_api_key(self, provider_id: str) -> str | None:
        """Pi parity: ``oauth/index.ts::getOAuthApiKey``.

        Looks up OAuth credentials, refreshes if expired (``time.time()*1000 >= expires``),
        persists updated credentials, returns the access token via
        ``provider.get_api_key(creds)``. Returns ``None`` if no creds stored.
        """
```

Cross-platform note: `fcntl.flock` is POSIX-only. Sprint 6c documents Windows as TODO; on Windows the lock is a no-op (`try/except ImportError`).

---

## §I — Sprint 6a `_AuthError` integration

File: `packages/aelix-ai/src/aelix_ai/providers/anthropic.py`

Sprint 6a path (line 62 region) currently does:
```python
class _AuthError(Exception): ...
# ... later in stream_anthropic ...
if is_oauth_token(api_key):
    raise _AuthError("OAuth not supported in Sprint 6a")
```

Sprint 6c amendment:
```python
class _AuthError(Exception): ...
# ... later in stream_anthropic ...
# Sprint 6c: OAuth tokens are valid when the harness's
# ``get_api_key_and_headers`` callback resolved them through
# ``AuthStorage.get_oauth_api_key("anthropic")``. The token still
# starts with ``sk-ant-oat`` but is now a fresh access token.
# We pass it through to the SDK — the Anthropic SDK auto-detects OAuth
# tokens and routes via ``Authorization: Bearer`` header.
# Eager-rejection is removed; if the caller passed a bare OAuth token
# without wiring AuthStorage, the SDK itself will raise on the auth
# header mismatch (which the harness translates to AgentHarnessError("auth")).
if is_oauth_token(api_key):
    # No-op: SDK handles it. Telemetry/logging only.
    _log_oauth_token_passthrough(api_key)
```

Sprint 6a regression test `test_adapter_auth_error_translates_to_harness_auth_error` is updated to test a DIFFERENT trigger (e.g., HTTP 401 from SDK), not OAuth-token detection. The OAuth detection happy path gets a NEW test.

---

## §J — Forward-compat clause (binding)

After Sprint 6c:
- `_OAUTH_DEFERRED_PROVIDERS` set contains `{"github-copilot", "openai-codex"}` with owning ADR-0053.
- `_PHASE_4_DEFERRED_FEATURES` set contains `{"rpc-mode"}` with owning ADR-0054.
- Any future PR that lands a Copilot/Codex provider MUST drop it from the set in the same PR (enforced by closure pin).
- The OAuth `modifyModels` callback is Pi parity but unused; Aelix Protocol declares it as `modify_models: Callable | None = None`. Sprint 6e wires it for Copilot (which needs base URL injection per copilot subscription tier).

---

## §K — Tests (binding plan, ~780 LOC)

### Unit
- `tests/oauth/test_types.py` — OAuthCredentials round-trip (to_json/from_json with extras preserved); OAuthPrompt/Info dataclass defaults.
- `tests/oauth/test_pkce.py` — known-input vectors, base64url charset, deterministic challenge.
- `tests/oauth/test_oauth_page.py` — HTML contains the message text; valid HTML5.
- `tests/oauth/test_callback_server.py` — start server, hit `/callback?code=X&state=valid` → 200 + future resolves; state mismatch → 400; missing code → 400; other path → 404; cancel_wait → future None; shutdown is clean.
- `tests/oauth/test_anthropic.py` — login flow with mocked HTTP (`respx`/`httpx_mock`):
  - happy path: PKCE → AUTHORIZE_URL → callback server → exchange → OAuthCredentials
  - manual-code-input race wins; server.wait cancelled
  - state mismatch in manual input raises
  - exchange failure raises with formatted error
  - refresh: TOKEN_URL POST → updated credentials
  - refresh failure raises
- `tests/oauth/test_registry.py` — register/get/unregister/reset; built-in restoration; custom provider lifecycle.
- `tests/oauth/test_auth_storage.py` — load missing file (empty dict); save creates 0o700 dir + 0o600 file; round-trip api_key + oauth; remove(); `get_oauth_api_key` auto-refresh when expired; in-process asyncio.Lock serialization; cross-process flock (skip on Windows).

### Integration
- `tests/oauth/test_harness_integration.py` — wire `AuthStorage.get_oauth_api_key("anthropic")` into Sprint 6a `get_api_key_and_headers` callback; verify the Anthropic adapter receives the fresh access token and the SDK call uses it.
- `tests/oauth/test_anthropic_adapter_oauth_passthrough.py` — Sprint 6a adapter accepts `sk-ant-oat…` token without raising (P-91 fix).

### Pi parity closure pin
- `tests/pi_parity/test_phase_4_3_strict_superset.py`:
  - Assert `len(get_oauth_providers()) == 1` (Anthropic only); `{"github-copilot", "openai-codex"} ⊂ _OAUTH_DEFERRED_PROVIDERS` with owning ADR.
  - Assert OAuthCredentials dataclass shape ⊇ Pi `OAuthCredentials` (3 required fields + extra).
  - Assert OAuthProvider Protocol covers 5 of Pi's 6 methods (modify_models is Optional[Callable] but unwired).
  - Assert Anthropic constants match Pi (`AUTHORIZE_URL`, `TOKEN_URL`, `CALLBACK_PORT`, `SCOPES`, decoded `CLIENT_ID`).
  - Assert RPC mode is in `_PHASE_4_DEFERRED_FEATURES`.
  - Behavior assertions: `generate_pkce()` output charset; `_base64url` no padding.

---

## §L — ADRs

### Amend
- **ADR-0034** — add row: "Sprint 6c added OAuth surface (Anthropic only). Copilot + Codex deferred to Sprint 6e per ADR-0053. RPC mode deferred to Sprint 6d per ADR-0054."
- **ADR-0035** — amend "auth" code section: "Sprint 6c removes the eager OAuth-token rejection in Anthropic adapter. `_AuthError` now fires only when the SDK itself returns 401/403."

### NEW
- **ADR-0051** — `0051-oauth-client-framework.md` — OAuth Protocol + types + PKCE + callback server + registry pattern (mirrors ADR-0045).
- **ADR-0052** — `0052-anthropic-oauth-flow.md` — port of Pi Anthropic OAuth flow (PKCE + local callback + token exchange/refresh + constants).
- **ADR-0053** — `0053-auth-storage-and-secrets.md` — `AuthStorage` JSON layer, 0o600 permissions, asyncio + fcntl locking, no encryption (parity with Pi). Documents Copilot + Codex OAuth deferral.
- **ADR-0054** — `0054-rpc-mode-deferred-to-sprint-6d.md` — formal carry-forward record (Phase 4 RPC mode owner).
- **ADR-0055** — `0055-phase-4-3-strict-superset-closure.md` — mirror ADR-0046/0050 pattern. Roster: P-83 ~ P-92.

---

## §M — Sprint workflow (ADR-0032)

- W0 — research (this section's findings) ✓ DONE
- W1 — this spec (binding) — current step
- W2 — executor opus implements §B~§I
- W3 — verification (pytest + ruff + pyright spike — preserve 8-error baseline)
- W4 — code-reviewer opus (parallel with W5)
- W5 — architect opus Pi parity audit (parallel with W4)
- W6 — apply must-fixes + atomic commits + ADRs accepted

**Atomic commit plan (W6, 5 commits):**
1. `feat: oauth — types + PKCE + page + callback server (ADR-0051, P-85~P-89)`
2. `feat: oauth — Anthropic provider + registry + auth_storage (ADR-0052/0053, P-83~P-90)`
3. `feat: oauth — high-level get_oauth_api_key + Sprint 6a adapter integration (P-91/P-92, ADR-0035 amend)`
4. `test: Sprint 6c — N new tests + 1 Pi-parity fixture + Phase 4.3 closure pin`
5. `docs: ADRs 0034 amend + 0035 amend + NEW 0051/0052/0053/0054/0055 + README + spec`

---

## §N — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 814 baseline + ~80 new ≈ 894+; 0 fail |
| ruff check | clean |
| pyright spike | 8 errors (baseline preserved) |
| Sprint 6a tests | NO regressions (specifically `test_adapter_auth_error_translates_to_harness_auth_error` updated for new contract) |
| Pi parity closure | `_OAUTH_DEFERRED_PROVIDERS` populated; 1 OAuth provider live |
| Atomic commit count | exactly 5 |

---

**End of binding spec. Architect READ-ONLY until W6.**
