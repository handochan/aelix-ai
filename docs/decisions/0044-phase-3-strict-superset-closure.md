# ADR-0044 — Phase 3 Strict Superset Closure

Status: **Accepted** (Sprint 5b shipped, 2026-05-17)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## 1. 1st-principle invariant

Aelix Phase 3 is a **strict Pi-parity superset** of Pi's `aelix-coding-agent`
package. Every Pi extension method, hook event, built-in tool, and
ExtensionCommandContext method in Phase 3 scope has a corresponding
binding in Aelix OR an explicit deferred entry citing its owning ADR.

## 2. Phase 3 findings roster (P-21 through P-36)

| Sprint | Findings | Closure ADR |
|---|---|---|
| 5a (Phase 3.1) | P-21~P-30 (ExtensionAPI 48 methods, ExtensionContext 14 fields, 3 events registered) | ADR-0041 |
| 5b (Phase 3.2) | P-31 (tool-typed ToolCallEvent), P-32 (tool catalog), P-33 (factories), P-34 (emit-site correction), P-35 (ExtensionCommandContext 4 of 6), P-36 (input schemas) | This ADR + ADR-0042 + ADR-0043 |

## 3. Closure

Phase 3 ADRs all Accepted:

- ADR-0017 — catalogue v2 (Phase 3.1/3.2 emit-site addendum)
- ADR-0028 — extension auto-discovery (4 stubs now wired)
- ADR-0041 — ExtensionAPI 48-method surface (closure pin shipped)
- ADR-0042 — built-in tools + 3 emit sites
- ADR-0043 — tool-typed ToolCallEvent variants
- ADR-0044 — this closure ADR

## 4. Durable regression guard

`tests/pi_parity/test_phase_3_2_strict_superset.py` is the binding
mechanization. The closure pin asserts:

1. 7 Pi tool names land via `create_all_tools(cwd)`.
2. `create_coding_tools` / `create_read_only_tools` collections match Pi.
3. 8 `ToolCallHookEvent` + 8 `ToolResultHookEvent` typed variants exist.
4. Factory dispatch routes by `tool_name`.
5. `ExtensionCommandContext` exposes all 6 Pi methods (4 bound + 2 raise).
6. `DEFERRED_ALLOWLIST` drops `input` / `user_bash` / `resources_discover`.

## 5. Deferred allowlist (post-5b)

Phase 3 scope: **empty**.

Phase 4 (ADR-0038 provider adapter) still owns 3 entries:

- `before_provider_request`
- `before_provider_payload`
- `after_provider_response`

Phase 5 inheritance: `ExtensionUIContext` (ADR-0033), `ModelRegistry` full
impl (ADR-0038), `new_session` / `switch_session` CLI lifecycle.

## 6. Forward-compat clause

Same as ADR-0039 §"Forward-compat". When Phase 4/5 lands a deferred
binding, the closure pin MUST be updated in the same PR. A deferred
entry without a citing ADR is a contract violation.
