# 0046. Phase 4 Strict Superset Closure

Status: Accepted (Sprint 6a / Phase 4.1 shipped)

## Context

ADR-0039 / ADR-0040 / ADR-0044 established the Aelix strict-Pi-parity-superset
invariant for Phases 2.1 / 2.2 / 3. Each closure ADR pinned a
regression-guard test under `tests/pi_parity/` that asserts every
Pi-verified surface in scope has a corresponding binding in Aelix, OR
sits in `DEFERRED_ALLOWLIST` with an owning ADR.

At the Sprint 5b boundary the `DEFERRED_ALLOWLIST` carried **3 entries**
(all owned by ADR-0038 / Phase 4 provider work):

- `before_provider_request`
- `before_provider_payload`
- `after_provider_response`

Sprint 6a lands the Phase 4.1 emit sites, closing the allowlist to
**empty `{}`** — full Pi-parity event-name coverage.

## Decision

The Phase 4 strict-superset closure pin is
`tests/pi_parity/test_phase_4_strict_superset.py`. It asserts the
Sprint 6a roster (P-37 → P-43) PLUS the cumulative invariant from
ADR-0039/0040/0044 (every Pi event has an emit site).

### Roster

| Finding | Subject | Resolution |
|---|---|---|
| **P-37** | ADR-0034 cited `badlogic/pi-mono`; canonical slug is `earendil-works/pi` at SHA `734e08e` | ADR-0034 amended |
| **P-38** | Pi `providers/anthropic.ts` uses official SDK | Aelix uses `anthropic>=0.40,<1.0` for byte-level parity |
| **P-39** | ADR-0037 9-class plan off-by-one | 8 new dataclasses + rename `AssistantEndEvent` → `AssistantDoneEvent` (deprecated alias) + 2 backfills |
| **P-39d** | SILENT DRIFT: Aelix `tool_call_delta` ≠ Pi `toolcall_delta` | Spelling fix; `input_delta` legacy property preserved |
| **P-40** | All 3 emit sites belong inside `createStreamFn` closure | `_make_stream_fn` in `harness/core.py` |
| **P-41** | `applyStreamOptionsPatch` shallow vs deep merge | Pi-verbatim port at `harness/hooks.py:_apply_stream_options_patch` |
| **P-42** | Pi has 9 codes; Aelix `aborted` is additive | Literal widened to 10 (9 Pi + 1 Aelix-additive); ADR-0035 Accepted |
| **P-42b** | `network_error`/`tool_error`/`provider_auth` NOT in Pi | dropped from scope; SDK exceptions map to `"auth"` or `AssistantErrorEvent` |
| **P-43** | `getApiKeyAndHeaders` returns `{apiKey?, headers?} | undefined` | `_make_stream_fn` accepts `None` (no opinion) or `{apiKey?, headers?}` |
| **P-44** | Pyright baseline preserved | Sprint 6a introduces no new pyright errors (8-error baseline maintained); narrowing handled via existing `isinstance`/`Literal` guards |
| **P-45** | `test_hook_payload_roundtrip` enshrined shallow-merge bug | Test corrected to use Pi-recognized scalar keys (`transport`/`timeoutMs`); P-41 deep-merge fix is the binding contract |
| **P-46** | Phase 3.2 closure pin obsoleted by Phase 4 closure | Assertion relaxed per ADR-0039 forward-compat clause — Phase 3.2 invariant stays via the strict-superset test in the same module |
| **W6 Fix 1** | Adapter-layer `_AuthError` bubbled untranslated past harness contract | `_make_stream_fn` now wraps `stream_simple` await in `try/except _AuthError: raise AgentHarnessError("auth", ...)`; regression test `test_adapter_auth_error_translates_to_harness_auth_error` pins the contract |

### Closure invariant

```
DEFERRED_ALLOWLIST = {}
```

Every event in `pi_agent_harness_event_names_734e08e.json` (28
own-events + 10 loop projections + 3 coding-agent events = 31 names)
has at least one emit site in `aelix-agent-core` or `aelix-coding-agent`
runtime code.

### What ships

- 12 `AssistantMessageEvent` variants (8 new + 1 rename + 1 alias + 2
  backfills + 1 spelling fix). See ADR-0037.
- `Provider` Protocol + `register_provider_object` +
  `unregister_providers_by_source`. See ADR-0045.
- `SimpleStreamOptions` extensions (`cache_retention`, `transport`,
  `timeout_ms`, `max_retries`, `max_retry_delay_ms`, `reasoning`,
  `session_id`, `on_payload`, `on_response`, `client`). See ADR-0045.
- `ProviderResponse` dataclass. See ADR-0045.
- Anthropic adapter under `aelix_ai.providers.anthropic`. See ADR-0045.
- `_apply_stream_options_patch` deep-merge port (Pi parity). See P-41.
- `_make_stream_fn` closure + 3 emit-site method bridges
  (`_emit_before_provider_request`, `_emit_before_provider_payload`,
  `_emit_after_provider_response`).
- `AgentHarnessError("auth", …)` raise path in `_make_stream_fn`. See
  ADR-0035 amendment.
- New closure pin `tests/pi_parity/test_phase_4_strict_superset.py` +
  3 fixtures.

### Forward-compat clause

Phase 4 emit-site coverage is now complete. Any future Pi sprint that
adds a new Pi-side hook event MUST do one of two things in the PR that
introduces it:

1. Land the corresponding Aelix emit site in the same PR.
2. Add an entry to `DEFERRED_ALLOWLIST` with an owning ADR.

The forward-compat clause from ADR-0039 continues to apply: any deferred
event that subsequently gains an emit site MUST be dropped from
`DEFERRED_ALLOWLIST` in the same PR (enforced by
`test_deferred_allowlist_entries_remain_unemitted`).

## Consequences

- Phase 4.1 closure unblocks Sprint 6b (OpenAI / OpenRouter adapters) —
  the framework is stable, only adapter implementations remain.
- Pyright `match` exhaustiveness across the 12-variant union is now
  enforced by `assert_never` (ADR-0030).
- Sprint 6c (OAuth + RPC mode + secrets ADR) can proceed against the
  ADR-0045 stable surface without further refactor.
- The `"auth"` error code is now wired; ADR-0020 (RPC mode) can rely on
  it for surface mapping when it ships.

## Related

- ADR-0017 — full hook event catalogue (provider events specified here).
- ADR-0034 — Pi reference pin (slug correction shipped).
- ADR-0035 — Error code taxonomy (10 codes accepted).
- ADR-0037 — Streaming event union (12 variants accepted).
- ADR-0038 — `stream_simple` dispatch shell (body shipped).
- ADR-0039 — Phase 2.1 strict superset closure (cumulative invariant).
- ADR-0040 — Phase 2.2 strict superset closure (cumulative invariant).
- ADR-0044 — Phase 3 strict superset closure (cumulative invariant).
- ADR-0045 — Provider Adapter Interface (Sprint 6a NEW).

## Phase

Sprint 6a / Phase 4.1 (shipped — closure pin Green; `DEFERRED_ALLOWLIST` empty).
