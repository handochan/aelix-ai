# 0040. Phase 2.2 Strict Superset Closure

Status: Accepted (Sprint 4b / Phase 2.2.2 shipped — 2026-05-17)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 2.1 closure ADR-0039 documented the 1st-principle invariant that Aelix is a strict Pi-parity superset, with `tests/pi_parity/test_phase_2_1_strict_superset.py` as the durable regression guard. Phase 2.2 (Session Manager + JSONL Persistence + compact + navigate_tree) extends the scope.

## Decision

Phase 2.2 closes with **zero deferred Phase 2.2 Pi events**. The DEFERRED_ALLOWLIST contains only 3 Phase 4 entries (`before_provider_request`, `before_provider_payload`, `after_provider_response`).

### Findings roster (P-11 through P-20)

Inherited from Sprint 4a (Phase 2.2.1):
- **P-11** (Sprint 4a W1): `PendingActiveToolsChangeWrite` fabricated by Sprint 3b W4 MAJOR-1. Pi `setActiveTools` (`agent-harness.ts:875-882`) does NOT push pending writes. RESOLVED: variant + push site deleted; 3-layer regression lockdown.
- **P-12** (Sprint 4a W1): Pi PendingSessionWrite is 8 flush arms + 3 push sites only. RESOLVED: 8-variant union + 8-arm match dispatcher with assert_never.
- **P-13** (Sprint 4a W1): Pi `Session` is concrete class (17+1 methods), `SessionStorage` is the Protocol (10 methods), `appendCompaction` takes 5 params. RESOLVED: Aelix Session class + SessionStorage Protocol + 5-param signature.

New in Sprint 4b (Phase 2.2.2):
- **P-14** (Sprint 4b W1): Pi has NO `compactSummarizer`/`branchSummarizer` callbacks in AgentHarnessOptions. RESOLVED: Aelix uses `get_api_key_and_headers` inline + raises AgentHarnessError("invalid_state") if missing.
- **P-15** (Sprint 4b W1): Pi Phase Literal has 5 values (`"idle" | "turn" | "compaction" | "branch_summary" | "retry"`). Aelix ships 4 (omits "retry" — Pi declared-but-unused). RESOLVED: Aelix-additive omission documented in ADR-0023.
- **P-16** (Sprint 4b W1): Pi `compact(customInstructions?)` 1 param; Pi `navigateTree(target, options?)` with NavigateTreeOptions + NavigateTreeResult. RESOLVED: Aelix matches Pi signatures exactly.
- **P-17** (Sprint 4b W1): `SessionBeforeCompactHookEvent` payload was empty stub; Pi has `{preparation, branch_entries, custom_instructions, signal}`. RESOLVED: payload extended Sprint 4b.
- **P-18** (Sprint 4b W1): `SessionBeforeTreeHookEvent` missing `signal` field. RESOLVED: field added Sprint 4b.
- **P-19** (Sprint 4b W1): `SessionTreeHookEvent.new_leaf_id` should be `str | None` (was `str=""`). RESOLVED: narrowed Sprint 4b.
- **P-20** (Sprint 4b W1): `SessionBeforeCompactResult` 2-field minimal stub; Pi `{cancel?, compaction?}` allows hook to substitute LLM call. RESOLVED: extended Sprint 4b.

## Consequences

- Phase 2.2 ADRs all Accepted: 0017, 0022, 0023, 0025, 0040.
- 4 session_* emit sites active; `tests/pi_parity/test_phase_2_2_strict_superset.py` is the closure regression guard.
- DEFERRED_ALLOWLIST post-4b: 3 Phase 4 entries only (`before_provider_request`, `before_provider_payload`, `after_provider_response`) — these land with Phase 4 ADR-0038 provider adapter work.

## Forward-compat clause

Any new Pi event added to Pi after SHA `734e08e` MUST either (a) ship an Aelix emit site in the same sprint, OR (b) be explicitly added to `DEFERRED_ALLOWLIST` in `test_phase_2_2_strict_superset.py` with an owning ADR reference. The closure pin test enforces this contract.

## Related

- ADR-0039 (Phase 2.1 closure — parent pattern)
- ADR-0022 (Session class + JsonlSessionRepo — Sprint 4a)
- ADR-0023 (Compaction + Branch Summary — Sprint 4b)
- ADR-0017 (Hook event catalogue v2 — payload extensions Sprint 4b §"Session emit sites + payload extensions landed Sprint 4b")
