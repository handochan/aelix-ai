# 0059. GitHub Copilot OAuth (Device-Code Flow)

Status: Accepted (Sprint 6e / Phase 4.5 / W6 shipped)

## Context

Pi `packages/ai/src/utils/oauth/github-copilot.ts` (SHA `734e08e`,
~410 LOC) implements GitHub Copilot OAuth via the **device-code
grant** — distinct from the Anthropic / Codex PKCE-callback flow.
There is no local callback server; the user manually enters a
verification code at a GitHub-provided URL, and the agent polls
GitHub's token endpoint for completion.

After the GitHub access token arrives, a follow-up GET to Copilot's
`/copilot_internal/v2/token` exchanges it for a Copilot-flavored
bearer token that encodes the API proxy endpoint in semicolon-
delimited fields (`tid=…;exp=…;proxy-ep=proxy.individual.githubcopilot.com;…`).

Sprint 6c left this entire surface in `_OAUTH_DEFERRED_PROVIDERS`
(owner ADR-0053). Sprint 6e ports it verbatim and wires the
`modify_models` Protocol callback (Sprint 6c P-102 forward-compat)
so per-credential base URLs reach the Copilot-routed Model registry.

## Decision

Aelix ships `aelix_ai.oauth.github_copilot` mirroring Pi's
`github-copilot.ts` byte-for-byte:

### Constants (Pi parity verbatim)

| Constant | Pi line | Aelix |
|---|---|---|
| `CLIENT_ID` (base64-obfuscated decode) | github-copilot.ts:13 | base64-decoded at module load |
| `COPILOT_HEADERS` (4 keys: User-Agent / Editor-Version / Editor-Plugin-Version / Copilot-Integration-Id) | github-copilot.ts:16-21 | verbatim dict |
| `INITIAL_POLL_INTERVAL_MULTIPLIER = 1.2` | github-copilot.ts:23 | `1.2` |
| `SLOW_DOWN_POLL_INTERVAL_MULTIPLIER = 1.4` | github-copilot.ts:24 | `1.4` |
| `DEFAULT_DOMAIN = "github.com"` | github-copilot.ts:245 | `"github.com"` |
| `_EXPIRES_SAFETY_MARGIN_MS = 5 * 60 * 1000` | github-copilot.ts:270 | `5 * 60 * 1000` |
| `_MIN_INTERVAL_MS = 1000` | github-copilot.ts:179 | `1000` |

### Flow (Pi `loginGitHubCopilot` parity)

1. Prompt for optional GitHub Enterprise URL/domain. Empty = `github.com`.
2. `normalize_domain` accepts both `ghe.example.com` and
   `https://ghe.example.com`; the **raw** user input is preserved
   verbatim in `OAuthCredentials.extra["enterpriseUrl"]` (Pi camelCase,
   per P-147) and the normalized hostname is used solely for URL
   construction.
3. POST `<domain>/login/device/code` → receive `{device_code,
   user_code, verification_uri, interval, expires_in}`.
4. `callbacks.on_auth({url: verification_uri, instructions: "Enter
   code: <user_code>"})`.
5. Poll `<domain>/login/oauth/access_token` until success or timeout
   (Pi order: **fetch → check → sleep**; see W4 M1 below).
6. Exchange GitHub access token for the Copilot-flavored bearer via
   `refresh_github_copilot_token`.

### `modify_models` callback (Sprint 6c P-102 forward-compat — wired)

`_modify_copilot_models(models, credentials)` extracts the
`proxy-ep=` value from the access token, converts `proxy.<host>` →
`api.<host>`, and injects `https://api.<host>` as `Model.base_url`
on every model whose `provider` field equals `"github-copilot"`. Non-
Copilot models pass through unchanged.

```python
class _GithubCopilotOAuthProvider:
    id = "github-copilot"
    name = "GitHub Copilot"
    uses_callback_server = False

    def modify_models(self, models, credentials):
        return _modify_copilot_models(models, credentials)
```

Pi uses `{...m, baseUrl}` (JS object spread, unconditional). Aelix
mirrors via `dataclasses.replace(model, base_url=…)`.

### Sprint 6e W6 must-fix integration

| Finding | Subject | Resolution |
|---|---|---|
| **W4 M1 / P-143** | Poll order was `sleep → fetch → check`, adding one interval's latency to every login | `fetch → check → sleep` (Pi `github-copilot.ts:188-226`) |
| **P-144** | Wait interval used truncating `int(interval_ms * multiplier)` | `math.ceil(interval_ms * multiplier)` matches Pi `Math.ceil` exactly |
| **P-145 / P-146** | `is_dataclass(model)` silently passed non-dataclass Copilot models through untouched | Raise `TypeError` when a Copilot-routed model is NOT a dataclass — matches Pi's invariant that every model is spreadable |
| **P-147** | Persisted extras key was `enterprise_domain` (snake_case, normalized) | Now `enterpriseUrl` (camelCase, raw user input) per Pi `github-copilot.ts:267-272` |

### Closure pin assertions

```python
# tests/pi_parity/test_phase_4_5_strict_superset.py
GITHUB_COPILOT_OAUTH_PROVIDER.id == "github-copilot"
GITHUB_COPILOT_OAUTH_PROVIDER.uses_callback_server is False
callable(GITHUB_COPILOT_OAUTH_PROVIDER.modify_models)
INITIAL_POLL_INTERVAL_MULTIPLIER == 1.2
SLOW_DOWN_POLL_INTERVAL_MULTIPLIER == 1.4
DEFAULT_DOMAIN == "github.com"
COPILOT_HEADERS == {...}  # 4-key dict matches fixture
```

The closure pin's `live ∪ deferred == 3` assertion now resolves with
Copilot in the **live** set.

## Consequences

- Copilot OAuth works end-to-end against `github.com` and against
  GitHub Enterprise instances (verified via `normalize_domain` round-
  trip + URL construction tests).
- The `modify_models` Protocol callback ships with a real consumer
  (Copilot), unblocking future providers that need per-credential
  Model surface mutation (e.g., region-locked Bedrock).
- The `enterpriseUrl` key choice is forward-compat with Pi's
  persisted `auth.json` shape: a Pi-written `auth.json` opens cleanly
  in Aelix and vice versa.
- The poll-order fix (W4 M1) eliminates one full polling interval of
  latency on every Copilot login — measurable user-facing improvement.

### Deferred to Sprint 6f

- **`enableGitHubCopilotModel()` automation** (Pi `github-copilot.ts:
  362-364`) — the post-login `/models/{id}/policy` POST per active
  Copilot-routed model. Aelix needs a populated ModelRegistry first
  (carry-forward from ADR-0058).
- **Windows `msvcrt.locking` fallback** for the auth.json cross-
  process lock (Pi has no Windows reference; carry-forward inherited
  from ADR-0053).

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6e — OAuth
  catalog now complete).
- ADR-0045 — Provider Adapter Interface (Model dataclass shape that
  `modify_models` mutates).
- ADR-0051 — OAuth client framework (provides the Protocol +
  registry that Copilot slots into).
- ADR-0053 — AuthStorage + secrets (Sprint 6e closes the Copilot
  carry-forward).
- ADR-0055 — Phase 4.3 strict superset closure (the closure pin
  this ADR drains an entry from).
- ADR-0063 — Phase 4.5 strict superset closure (the new closure pin
  that asserts this ADR's invariants).

## Phase

Sprint 6e / Phase 4.5 (shipped — Copilot device-code OAuth live;
`enableGitHubCopilotModel` automation deferred to Sprint 6f).
