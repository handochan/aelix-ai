# 0035. Error Code Taxonomy

Status: Draft (taxonomy doc only — Literal widening lands with owning ADR per code)

Phase 1.4 ships the **taxonomy map** documented here; it does **not** widen
`AgentHarnessError.code`'s `Literal` union (still the original 5 codes at
`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:102`). The
Pi-parity widening lands with the owning ADR for each placeholder code:
`"aborted"` with ADR-0017 (Phase 2.1), `"session"` with ADR-0022 (Phase 2.2),
`"compaction"` / `"branch_summary"` with ADR-0023 (Phase 2.2), and
`"auth"` with the Phase 4 provider work (ADR-0020-adjacent). This matches
the ADR-0025 minimal-shell cadence: documentation first, code on demand.

## Context

Aelix `AgentHarnessError.code` is currently
`Literal["busy", "invalid_state", "invalid_argument", "hook", "unknown"]`
(5 codes; `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:102`).
Pi (`packages/agent/src/harness/agent-harness.ts`, SHA `734e08e…`) uses 10
codes inferred from string-literal usage at the cited lines.

### Pi codes (research)

| Pi code | Pi citation (SHA `734e08e…`) | Used for |
|---------|------------------------------|----------|
| `"busy"` | agent-harness.ts:369,381,391 | phase != idle when prompt/abort entered |
| `"invalid_state"` | agent-harness.ts:356,409,461 | wrong phase for compaction / navigateTree |
| `"invalid_argument"` | agent-harness.ts:292,393,549 | bad activeToolNames / branch IDs / etc. |
| `"hook"` | agent-harness.ts:282 | hook handler threw |
| `"unknown"` | agent-harness.ts:354,407 | unclassified internal |
| `"session"` | agent-harness.ts:319,543 | session persistence failure |
| `"compaction"` | agent-harness.ts:461,466,476 | compact() failure |
| `"auth"` | agent-harness.ts:458,616 | getApiKey / getApiKeyAndHeaders failure |
| `"branch_summary"` | agent-harness.ts:319,633 | navigateTree summary failure |
| `"aborted"` | agent-harness.ts:630 | cooperative abort surfaced as error |

## Decision

Aelix today retains its 5 codes (Pi parity 1:1 for the codes that map). Add
**5 placeholder codes** to the `Literal` union as Phase 2.x land-them-as-they-arrive:

| Aelix code (Phase 1.4) | Pi code | Phase when wired |
|------------------------|---------|------------------|
| `"busy"` | `"busy"` | Already wired |
| `"invalid_state"` | `"invalid_state"` | Already wired |
| `"invalid_argument"` | `"invalid_argument"` | Already wired |
| `"hook"` | `"hook"` | Already wired |
| `"unknown"` | `"unknown"` | Already wired |
| `"session"` (new) | `"session"` | Phase 2.2 (ADR-0022) |
| `"compaction"` (new) | `"compaction"` | Phase 2.2 (ADR-0023) |
| `"auth"` (new) | `"auth"` | Phase 4 (ADR-0020 / provider work) |
| `"branch_summary"` (new) | `"branch_summary"` | Phase 2.2 (ADR-0023) |
| `"aborted"` (new) | `"aborted"` | Phase 2.1 (ADR-0017) |

**Phase 1.4 work (deferred to its owning phase per ADR-0025 minimal-shell
pattern):** the Literal union widening is staged for the same PR that lands
the first emitting codepath. Phase 1.4 ships the taxonomy as **documentation**;
no codepath is widened in Phase 1.4 itself. The W2 implementation of Phase
1.4 explicitly excludes touching `harness/core.py`'s `Literal` to preserve
exhaustive `match err.code:` callers that have shipped against the 5-code
union.

When Phase 2.x wires a code, the owning ADR (per the table) is responsible
for the Literal widening + the raise site + a regression test.

## Consequences

- `match err.code:` exhaustive matches stay sound today (every Phase 1.4
  code path still emits one of the original 5).
- ADR-0030 (assert_never) integration: when Phase 2.1 enables exhaustive
  checks on harness errors, the 5 new codes will need handlers or explicit
  "unreachable in Phase 1.4" stubs.
- Third-party error mapping (e.g. CLI exit-code translation) can now
  anticipate all 10 codes by reading this ADR — even before they appear in
  the Python `Literal`.
- The error taxonomy is decoupled from the union widening; this is the
  same "documentation first, code on demand" cadence ADR-0011/0017
  established for hook events.

## Related

- ADR-0017 — Full Hook Event Catalogue v2 (owns `"aborted"` wiring).
- ADR-0020 — RPC Mode (owns CLI exit-code mapping that consumes this set).
- ADR-0022 — Session Manager (owns `"session"` wiring).
- ADR-0023 — Compaction + Branch Summary (owns `"compaction"` and
  `"branch_summary"` wiring).
- ADR-0025 — Minimal-shell pattern (this ADR follows the same cadence).
- ADR-0030 — assert_never exhaustiveness (downstream consumer of this set).

## Phase

Sprint 2.5 / Phase 1.4 (documentation shipped; widening land per owning ADR).
