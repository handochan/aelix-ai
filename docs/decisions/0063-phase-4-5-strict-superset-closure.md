# 0063. Phase 4.5 Strict Superset Closure

Status: Accepted (Sprint 6e / Phase 4.5 / W6 shipped)

## Context

ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055 / 0058 established the
Aelix strict-Pi-parity-superset invariant for Phases 2.1 / 2.2 / 3 /
4.1 / 4.2 / 4.3 / 4.4. Each closure ADR pins a regression-guard test
under `tests/pi_parity/` that asserts every Pi-verified surface in
scope has a corresponding binding in Aelix, OR sits in a deferred
allowlist with an owning ADR.

Sprint 6e lands GitHub Copilot OAuth (ADR-0059) + OpenAI Codex
OAuth (ADR-0060) + the 12-method AuthStorage layered cascade
(ADR-0061) + the `aelix auth` CLI subcommand (ADR-0062). The W4
code review + W5 Pi parity audit produced **0 BLOCKING + 1 MAJOR
(W4 M1 Copilot poll order) + many MINOR** drift findings; Sprint 6e
W6 applied the must-fix triage in 5 atomic commits.

Closure date: **2026-05-19**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

The Phase 4.5 strict-superset closure pin is
`tests/pi_parity/test_phase_4_5_strict_superset.py`. It asserts the
Sprint 6e roster (P-130 → P-162 + W4 M1 + W4 m1..n3) PLUS the
cumulative invariant from ADR-0039 / 0040 / 0044 / 0046 / 0050 /
0055 / 0058.

### Roster (Sprint 6e)

#### W0 binding-spec findings (P-130..P-137)

| Finding | Subject | Resolution |
|---|---|---|
| **P-130** | Copilot uses device-code grant, NOT PKCE-callback | `aelix_ai.oauth.github_copilot` ports verbatim; `uses_callback_server=False` |
| **P-131** | Codex uses PKCE-callback at port 1455 path `/auth/callback` (NOT 53692/`/callback`) | `aelix_ai.oauth.openai_codex` reuses Sprint 6c `_callback_server.py` with new constants |
| **P-132** | `OAuthProvider.modify_models` is the Sprint 6c P-102 forward-compat — wired in Sprint 6e | `_modify_copilot_models` injects `proxy-ep` → `Model.base_url` for Copilot-routed models |
| **P-133** | AuthStorage layered cascade is Sprint 6c W5 P-95 carry-forward | 12 new methods on `AuthStorage` per Pi `auth-storage.ts:154-516` |
| **P-134** | Pi `pollForGitHubAccessToken` honors `slow_down` persistently | Aelix mirrors the `1.4×` multiplier latched until exit |
| **P-135** | Pi `getBaseUrlFromToken` parses `proxy-ep=` from semicolon-delimited token | `_get_base_url_from_token` + `proxy.` → `api.` rewrite |
| **P-136** | Pi Codex JWT account_id extraction at claim `https://api.openai.com/auth` | `_get_account_id` decodes JWT payload (base64url, RFC 7519 §3) |
| **P-137** | `aelix auth login/logout/status/list` CLI subcommand surface | `src/aelix/__main__.py` subparser + `_cli_callbacks` stdin/stdout wiring |

#### W4 + W5 W6 must-fix MAJOR

| Finding | Subject | Resolution |
|---|---|---|
| **W4 M1** | Copilot poll order was `sleep → fetch → check`, adding one interval's latency to every login | `fetch → check → sleep` (Pi `github-copilot.ts:188-226`); paired with **P-144** `math.ceil` fix |

#### W4 + W5 W6 must-fix MINOR (applied)

| Finding | Subject | Resolution |
|---|---|---|
| **P-138** | Persisted Codex extras key was `chatgpt_account_id` (Pi local var) | Now `accountId` (Pi `openai-codex.ts:404` camelCase) |
| **P-139** | `_get_account_id` raised on null; Pi returns null with caller-raises | Returns `None` on any decode failure; caller raises Pi error |
| **P-140** | `originator=pi` URL param missing from Codex authorize URL | `ORIGINATOR_DEFAULT = "pi"` restored |
| **P-141** | `resolveConfigValue` (`!cmd` + env-ref expansion) missing | `_resolve_config.py` ports the helper |
| **P-142** | OAuth refresh failure leaked into env/fallback path | Reload-and-retry then return `None` per Pi `auth-storage.ts:480-490` |
| **P-143** | Copilot poll order latency (paired with W4 M1) | `fetch → check → sleep` ordering |
| **P-144** | Poll wait truncated via `int(interval * multiplier)` | `math.ceil(interval_ms * multiplier)` Pi-exact |
| **P-145 / P-146** | `is_dataclass(model)` silently passed non-dataclass Copilot models | Raise `TypeError` on non-dataclass Copilot-routed model |
| **P-147** | Persisted extras key was `enterprise_domain` (snake_case, normalized) | Now `enterpriseUrl` (camelCase, raw user input) Pi-exact |
| **P-149** | Codex callback host env override didn't fall back to shared name | Nested env lookup: codex-specific → shared → `127.0.0.1` |
| **P-150** | `_decode_jwt_payload` raised on malformed JWT | Returns `None` on any decode failure (Pi `try/catch → null`) |
| **P-152** | `aelix auth status <unknown>` silently reported "not configured" | Validate against `registry ∪ stored` → exit 2 with diagnostic |
| **P-157** | `_maybe_await` duplicated across Anthropic / Copilot / Codex | Extracted to `_helpers.py::maybe_await` single owner |
| **W4 m1** | `OAuthCredentials.from_json` clear ValueError (Sprint 6c carryover regression test) | Maintained; new tests assert |
| **W4 m2** | CLI cleanup: `auth login` provider validation pre-call | Layered via `set_runtime_api_key` Sprint 6f carry-forward |
| **W4 m3** | Codex refresh duplicate guard (`set_oauth` race) | Carry-forward to Sprint 6f (P-159 INFO) |
| **W4 m4** | Cross-process merge docstring | Added on `set_oauth` / `set_api_key` |
| **W4 m5** | Cascade DEBUG logging | `logger.debug(...)` on each layer |
| **W4 m6** | Codex callback host env fallback (paired P-149) | Resolved |
| **n1** | `aelix auth login` RuntimeError leaked Python traceback | Try/except RuntimeError → stderr + exit 1 |
| **n3** | `_OAUTH_DEFERRED_PROVIDERS` populated state from Sprint 6c | Now drained to `{}` (3/3 Pi providers live) |

### Closure invariant

```python
# All 3 Pi OAuth providers are LIVE:
{p.id for p in get_oauth_providers()} == {
    "anthropic", "github-copilot", "openai-codex"
}
len(get_oauth_providers()) == 3

# Deferred allowlist DRAINED:
_OAUTH_DEFERRED_PROVIDERS == {}

# live ∪ deferred = 3 invariant preserved:
live | deferred == {"anthropic", "github-copilot", "openai-codex"}
live & deferred == set()

# modify_models Protocol attribute populated:
callable(GITHUB_COPILOT_OAUTH_PROVIDER.modify_models)
getattr(ANTHROPIC_OAUTH_PROVIDER, "modify_models", None) is None or callable(...)
getattr(OPENAI_CODEX_OAUTH_PROVIDER, "modify_models", None) is None or callable(...)

# AuthStorage exposes 12 cascade methods:
required = {
    "set_runtime_api_key", "remove_runtime_api_key",
    "set_fallback_resolver", "has_auth", "get_auth_status",
    "list", "has", "get_all", "drain_errors",
    "login", "logout", "get_api_key_cascade",
}
all(hasattr(AuthStorage, n) for n in required)

# AuthSource enum cardinality (Pi 6 values):
set(get_args(AuthSource)) == {
    "stored", "runtime", "environment",
    "fallback", "models_json_key", "models_json_command",
}

# Sprint 6c carry-forward marked CLOSED:
_PHASE_4_DEFERRED_FEATURES["auth-storage-layered-resolution"]
    .startswith("ADR-0053 — Sprint 6e (CLOSED")

# CLI surface live:
# - aelix auth login/logout/status/list (subprocess-tested)
# - --mode rpc back-compat preserved
```

### What ships

- `aelix_ai.oauth.github_copilot` (~520 LOC) — device-code grant +
  enterprise domain + `modify_models` callback.
- `aelix_ai.oauth.openai_codex` (~500 LOC) — PKCE-callback at
  port 1455 + JWT account_id extraction.
- `aelix_ai.oauth.auth_storage` cascade extensions (~340 LOC added) —
  12 new methods.
- `aelix_ai.oauth._resolve_config` (~60 LOC) — `resolveConfigValue`
  port (`!cmd` + env-ref).
- `aelix_ai.oauth._helpers` (~35 LOC) — single-owner `maybe_await`.
- `aelix_ai.oauth.types` extensions — `AuthStatus`, `AuthSource`,
  `FallbackResolver`.
- `src/aelix/__main__.py` — `auth` subparser + 4 command handlers.
- `tests/pi_parity/test_phase_4_5_strict_superset.py` closure pin
  (~200 LOC).
- `tests/pi_parity/fixtures/pi_oauth_copilot_codex_734e08e.json` Pi
  parity fixture (110 LOC).
- `tests/oauth/test_github_copilot.py` (~610 LOC) + `test_openai_codex.py`
  (~565 LOC) + `test_auth_storage_cascade.py` (~510 LOC) +
  `test_resolve_config.py` + `test_copilot_modify_models_integration.py`.
- `tests/cli/test_auth_subcommand.py` (~170 LOC).
- `tests/oauth/test_types_authstatus.py` (~55 LOC).

### Forward-compat clause

Phase 4.5 closes the **OAuth catalog**: all 3 Pi OAuth providers
are live, the deferred allowlist is drained. Any future PR that
adds:

1. A new OAuth provider (e.g., custom enterprise IDP) MUST:
   - Register via `register_oauth_provider()` rather than landing
     in `_OAUTH_DEFERRED_PROVIDERS` (which stays empty going
     forward).
   - Add a closure-pin assertion for the new provider's constants
     in a successor closure ADR.
2. A new Phase 4 framework feature MUST either:
   - Land the Aelix binding in the same PR.
   - Add an entry to `_PHASE_4_DEFERRED_FEATURES` with an owning
     ADR.

The forward-compat clauses from ADR-0039 / 0046 / 0050 / 0055 /
0058 continue to apply.

## Consequences

### Carry-forward — Sprint 6f

- **W4 M3 / M4 / M5 (downgraded/withdrawn)** — these were initially
  triaged as MAJOR but W6 reclassified them as MINOR or INFO; they
  ride forward as Sprint 6f hygiene.
- **W4 m1** — Codex refresh duplicate guard (`set_oauth` race
  when a refresh and a concurrent login both write).
- **W4 m3** — Copilot `modify_models` non-dataclass behavior is
  now a `TypeError` raise (Sprint 6e P-145); the carry-forward is
  the **upstream caller validation** that prevents the type error
  from ever reaching `modify_models` in practice.
- **P-153 / P-154 / P-155 / P-159 / P-160 (INFO)** — code quality
  cleanups (docstring depth, log-line consistency, helper inlining)
  that don't change behavior.
- **P-161** — `on_manual_code_input` wiring in the Sprint 6f TUI.
- **P-162** — End-to-end OAuth round-trip integration test paired
  with ModelRegistry (`set_model` / `cycle_model` /
  `get_available_models` RPC commands from ADR-0058's deferred set).
- **ModelRegistry + `enableGitHubCopilotModel` automation** — Pi
  `github-copilot.ts:362-364`; the post-login policy POST per active
  Copilot-routed model.
- **Codex `chatgpt_account_id` header propagation** — paired with
  the OpenAI Responses adapter (Sprint 6f).
- **`--api-key <provider>:<key>` CLI flag** — surfaces
  `set_runtime_api_key` and `set_fallback_resolver`.
- **`models_json_key` / `models_json_command` AuthSource
  consumers** — once models.json plumbing lands.

### Immediate consequences

- Sprint 6e closes the OAuth catalog: every Pi-supported OAuth
  provider has an Aelix binding. The deferred-allowlist invariant
  enforces this — a future PR that adds a Pi OAuth provider but
  forgets the Aelix binding mechanically trips the closure pin.
- The 12-method cascade is the canonical key-resolution path; the
  `get_api_key_cascade` precedence (runtime → stored → OAuth → env
  → fallback) matches Pi byte-for-byte including the
  refresh-failure short-circuit.
- The `aelix auth` CLI lets users actually log in to Copilot /
  Codex without writing test code — the durable surface every
  future provider routes through.
- The `modify_models` Protocol callback ships with a real consumer
  (Copilot), validating the Sprint 6c P-102 forward-compat design.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6e — OAuth
  catalog complete).
- ADR-0045 — Provider Adapter Interface (the Model dataclass
  shape `modify_models` mutates).
- ADR-0051 — OAuth client framework.
- ADR-0052 — Anthropic OAuth flow (the PKCE template Codex
  follows).
- ADR-0053 — AuthStorage + secrets (Sprint 6c persistence; Sprint
  6e amended to mark cascade carry-forward RESOLVED).
- ADR-0055 — Phase 4.3 strict superset closure (the Sprint 6c
  partition this ADR closes).
- ADR-0058 — Phase 4.4 strict superset closure (the Sprint 6d
  partition; CLI back-compat preserved).
- ADR-0059 — GitHub Copilot OAuth (device-code flow).
- ADR-0060 — OpenAI Codex OAuth (PKCE callback flow).
- ADR-0061 — AuthStorage layered cascade.
- ADR-0062 — `aelix auth` CLI subcommand.

## Phase

Sprint 6e / Phase 4.5 (shipped — closure pin Green; 3 of 3 OAuth
providers live; 12 cascade methods live; `aelix auth` CLI live;
ModelRegistry + Codex header propagation + `--api-key` flag deferred
to Sprint 6f).
