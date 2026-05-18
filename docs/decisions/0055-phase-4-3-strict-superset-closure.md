# 0055. Phase 4.3 Strict Superset Closure

Status: Accepted (Sprint 6c / Phase 4.3 / W6 shipped)

## Context

ADR-0039 / 0040 / 0044 / 0046 / 0050 established the Aelix
strict-Pi-parity-superset invariant for Phases 2.1 / 2.2 / 3 / 4.1 / 4.2.
Each closure ADR pins a regression-guard test under `tests/pi_parity/`
that asserts every Pi-verified surface in scope has a corresponding
binding in Aelix, OR sits in a deferred allowlist with an owning ADR.

Sprint 6c lands the OAuth client framework (ADR-0051) + Anthropic
OAuth flow (ADR-0052) + AuthStorage secrets layer (ADR-0053) + RPC
mode formal carry-forward (ADR-0054). The W4 code review + W5 Pi
parity audit produced **2 BLOCKING + 7 MAJOR + 9 MINOR** drift
findings; Sprint 6c W6 applied the must-fix triage in 5 atomic commits.

Closure date: **2026-05-18**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

The Phase 4.3 strict-superset closure pin is
`tests/pi_parity/test_phase_4_3_strict_superset.py`. It asserts the
Sprint 6c roster (P-83 → P-104 + W4 M1..M6 + W4 m1..m9) PLUS the
cumulative invariant from ADR-0039 / 0040 / 0044 / 0046 / 0050.

### Roster (Sprint 6c)

#### W0 binding-spec findings (P-83..P-92)

| Finding | Subject | Resolution |
|---|---|---|
| **P-83** | Pi `oauth.ts` is a 40-byte re-export; real OAuth lives at `utils/oauth/` | Aelix ports under `aelix_ai.oauth/` (flat) per spec §0 |
| **P-84** | Sprint 6c original LOC estimate 3.5× too low | RPC + Copilot + Codex deferred per ADR-0054 / 0053 |
| **P-85** | Pi OAuth registry pattern mirrors ADR-0045 | Separate `_oauth_registry` matching the Provider Protocol pattern |
| **P-86** | `OAuthCredentials` is flat with extensible extras | Dataclass with `extra: dict[str, Any]` + `to_json` / `from_json` |
| **P-87** | PKCE uses Web Crypto in Pi; Python uses `secrets` + `hashlib` | RFC 7636 compliant port |
| **P-88** | Local HTTP callback server on `127.0.0.1:53692` | `http.server.HTTPServer` in daemon thread + asyncio bridge |
| **P-89** | `expires` is unix-ms with 5-min safety margin baked in at exchange | `_EXPIRES_SAFETY_MARGIN_MS = 5 * 60 * 1000` |
| **P-90** | Pi has no "secrets" abstraction; secrets live in `auth-storage.ts` | `AuthStorage` NEW class at `aelix_ai.oauth.auth_storage` |
| **P-91** | Sprint 6a `_AuthError` eager-rejects OAuth tokens | Replaced — `_AuthError` now fires only on SDK 401/403 (ADR-0035 amend) |
| **P-92** | `getApiKey()` returns access token (not derived header) | Pi parity verbatim |

#### W4 + W5 W6 must-fix BLOCKING

| Finding | Subject | Resolution |
|---|---|---|
| **W5 P-94** | Anthropic SDK does NOT auto-detect OAuth tokens — putting bearer in `x-api-key` → 401 | Sprint 6a adapter builds OAuth-flavored client with `Authorization: Bearer …` + `anthropic-beta: oauth-2025-04-20` headers + blank `api_key` (ADR-0052 §"Bearer header injection") |
| **W4 M4 / W5 P-93** | `login_anthropic` dropped Pi's late-manual-input fallback | Three-way race restructured per `anthropic.ts:294-307`; manual task re-awaited before `on_prompt` fallback |

#### W4 + W5 W6 must-fix MAJOR

| Finding | Subject | Resolution |
|---|---|---|
| **W4 M1** | `AuthStorage` write not atomic | Tmp + fsync + `os.replace` atomic write (ADR-0053 §"Atomic write") |
| **W4 M3** | Sprint 6a regression test didn't exercise new SDK-401 trigger | New `test_sdk_401_translates_to_harness_auth_error` exercises the real adapter path |
| **W4 M5** | `cross_process_lock_fd` fd leak on non-`OSError` | Broadened to `except BaseException` |
| **W4 M6 / W5 P-98** | `CALLBACK_HOST` env override was dead code | `host=CALLBACK_HOST, port=..., path=...` wired into `start_callback_server` |
| **W5 P-95** | AuthStorage layered cascade deferred; closure pin must own it | `_PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]` + ADR-0053 §Carry-forward |
| **W5 P-100** | Closure pin missing live + deferred = 3 sum assertion | `test_pi_oauth_provider_total_equals_3` added |
| **W5 P-103** | OAuth refresh-failure E2E missing | New `test_anthropic_adapter_oauth_refresh_failure.py` |

#### W4 + W5 W6 must-fix MINOR

| Finding | Subject | Resolution |
|---|---|---|
| **W4 m1** | `OAuthCredentials.from_json` raises bare `KeyError` | Raises `ValueError` naming missing fields |
| **W4 m3 / W5 P-99** | `asyncio.get_event_loop()` deprecated | Switched to `asyncio.get_running_loop()` |
| **W4 m4** | `log_message` Pyright override mismatch | Parameter name matches base class signature |
| **W4 m7** | `scope` field lost on refresh response | `extras = {k:v for ... if k not in {...}}` preserves all unknown response fields |
| **W4 m8** | Bare `OSError` on port-in-use | Friendly `RuntimeError` with fix-it message |
| **W4 m9** | XDG_CONFIG_HOME not honored | `default_auth_path` checks `XDG_CONFIG_HOME` before `~/.config` |

### Closure invariant

```python
# All 3 Pi OAuth providers accounted for:
live ∪ deferred == {"anthropic", "github-copilot", "openai-codex"}
len(live ∪ deferred) == 3
live ∩ deferred == ∅

# Live providers (1 of 3):
{p.id for p in get_oauth_providers()} == {"anthropic"}

# Deferred providers (2 of 3) with owning ADRs:
_OAUTH_DEFERRED_PROVIDERS == {
    "github-copilot": "ADR-0053 — Sprint 6e",
    "openai-codex":   "ADR-0053 — Sprint 6e",
}

# Phase 4 features deferred:
_PHASE_4_DEFERRED_FEATURES == {
    "rpc-mode":                          "ADR-0054 — Sprint 6d",
    "auth-storage-layered-resolution":   "ADR-0053 — Sprint 6e",
}
```

### What ships

- `aelix_ai.oauth/` package (types + pkce + page + callback server +
  anthropic flow + registry + auth_storage + high_level) — ~1,000
  prod LOC.
- `providers/anthropic.py` bearer-header injection (P-94) — adapter
  builds OAuth-flavored SDK client when `is_oauth_token()` is true.
- `tests/pi_parity/test_phase_4_3_strict_superset.py` closure pin with
  3 new W6 assertions (P-95 + P-100 + auth-storage-layered carry-forward).
- `tests/oauth/test_anthropic_adapter_oauth_passthrough.py` extended
  with 4 P-94 regressions.
- `tests/oauth/test_anthropic_adapter_oauth_refresh_failure.py` NEW
  (P-103 E2E).
- `tests/oauth/test_anthropic.py` extended with P-93 late-manual
  regression + P-98 CALLBACK_HOST wiring regression + m7 scope-extras
  preservation.
- `tests/oauth/test_auth_storage.py` extended with M1 atomic-write
  regression + m9 XDG path regressions.
- `tests/oauth/test_callback_server.py` extended with m8 port-in-use
  friendly-error regression.
- `tests/oauth/test_types.py` extended with m1 clear-error regression.
- `tests/test_agent_harness_auth_error.py` extended with M3 SDK-401
  regression.

### Forward-compat clause

Phase 4.3 OAuth coverage is now at 1 of 3 providers. Any future Pi
sprint that adds:

1. A new Pi OAuth provider MUST either:
   - Land the corresponding Aelix provider in the same PR.
   - Add an entry to `_OAUTH_DEFERRED_PROVIDERS` with an owning ADR.
2. A new Phase 4 framework feature MUST either:
   - Land the Aelix binding in the same PR.
   - Add an entry to `_PHASE_4_DEFERRED_FEATURES` with an owning ADR.

The forward-compat clauses from ADR-0039 / 0046 / 0050 continue to
apply: any deferred entry that subsequently gains the missing binding
MUST be dropped from the allowlist in the same PR.

## Consequences

### Carry-forward — Sprint 6d (RPC mode + hygiene)

- **W5 P-96** — connection pool reuse (Pi uses global fetch; Aelix
  per-call `httpx.AsyncClient`).
- **W5 P-104** — move inline `aelix_ai.oauth._registry` imports inside
  `auth_storage.py` to module top.
- **W4 m2** — `_format_error_details` depth-cap hardening (cyclic
  exception cause chain). Pi has same risk; not a parity violation.
- **W4 m6** — `_high_level.get_oauth_api_key_from_credentials` callers
  may leak refreshed token if they ignore `new_credentials`. Pi has
  same; documentation-only.
- **W4 N1..N9** — code quality cleanups.

### Carry-forward — Sprint 6e (Copilot + Codex + layered cascade)

- **W5 P-83** — GitHub Copilot OAuth (~410 Pi LOC).
- **W5 P-83** — OpenAI Codex OAuth (~460 Pi LOC).
- **W5 P-95** — Full `AuthStorage` layered cascade (login/logout/
  setRuntimeApiKey/has_auth/get_auth_status/set_fallback_resolver/
  drain_errors) per Pi `auth-storage.ts:455-516`.
- **W5 P-97** — `_format_error_details` add code/errno/stack.
- **W5 P-102** — explicit `on_select` / `signal` drop in provider
  login wrapper (documentation only).

### Immediate consequences

- Sprint 6c ships the durable OAuth boundary every future provider
  routes through; Sprint 6e Copilot/Codex slot in without framework
  change.
- The bearer-header injection fix (P-94) makes Anthropic OAuth
  actually work in production — without it every OAuth call would 401.
- The atomic-write fix (M1) means `auth.json` is never half-written
  even under power loss / SIGKILL.
- The closure-pin strengthening (P-100) means a future PR that adds
  Copilot OR Codex MUST drop the deferred entry in the same PR
  (mechanically enforced).

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6c).
- ADR-0035 — Error code taxonomy (amended Sprint 6c: `"auth"` now
  fires on SDK 401/403, not eager OAuth detection).
- ADR-0046 — Phase 4.1 strict superset closure.
- ADR-0050 — Phase 4.2 strict superset closure.
- ADR-0051 — OAuth client framework.
- ADR-0052 — Anthropic OAuth flow.
- ADR-0053 — AuthStorage + secrets layer.
- ADR-0054 — RPC mode deferred to Sprint 6d.

## Phase

Sprint 6c / Phase 4.3 (shipped — closure pin Green; 1 of 3 OAuth
providers live; 2 deferred with owning ADR-0053; RPC mode deferred
with owning ADR-0054; layered cascade deferred with owning ADR-0053).
