# 0078. Sprint 6h₄b Phase 4.12 — Strict Superset Closure + Sprint 6h₄c Wiring Carry-Forward

Status: Accepted (Sprint 6h₄b / Phase 4.12 / W6 shipped)
Date: 2026-05-21
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 4.12 closes the `AgentSessionRuntime` + `rebindSession` seam
FOUNDATION ported under ADR-0077 with a strict-superset audit lane
(W4 code-review opus + W5 architect Pi parity audit per ADR-0032).
The W4/W5 audit returned **2 MAJOR findings** (rebrand 0076→0078 +
missing back-compat regression suite) which W6 resolved with the
MUST-FIX triage applied in 5 atomic commits.

This ADR records the closure pin invariants + the Sprint 6h₄c wiring
carry-forward roster. **ADR-0078 is the new owner** of the 3 remaining
session-tree commands deferred to Sprint 6h₄c (rebranded from
ADR-0076 per spec §D.5). Sprint 6h₄c will MOVE these 3 to SUPPORTED
on top of the 6h₄b runtime foundation.

## Decision

### Closure pin invariants

`tests/pi_parity/test_phase_4_12_strict_superset.py` locks the
following Pi parity invariants at SHA `734e08e`:

- `SUPPORTED_COMMANDS` len == **26** (UNCHANGED from 6h₄a).
- `DEFERRED_COMMANDS` len == **3** (UNCHANGED).
- `SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved at 29.
- `set(DEFERRED_COMMANDS) == {"switch_session", "fork", "clone"}`.
- Each DEFERRED owner cites **ADR-0078** (rebrand applied per spec
  §D.5 — the cascade pin allowlists in 4.4 / 4.9 / 4.10 / 4.11 were
  extended with the ADR-0078 prefix in the same W6 commit).
- 3 deferred commands STILL route through `_make_deferred_handler`
  (Sprint 6h₄c moves them to runtime methods).
- `AgentSessionRuntime` 7 public methods present: `set_rebind_session`,
  `set_before_session_invalidate`, `switch_session`, `new_session`,
  `fork`, `import_from_jsonl`, `dispose`.
- `AgentSessionRuntime` 5 read-only properties present: `harness`,
  `session`, `cwd`, `diagnostics`, `model_fallback_message`.
- `RuntimeReplaceResult` frozen with fields `{"cancelled",
  "selected_text"}`.
- `AgentSessionRuntimeDiagnostic` frozen with fields `{"code",
  "message"}`.
- Pi line citations present in docstrings: `67-374` (class) /
  `99-101` (setRebindSession) / `111-113`
  (setBeforeSessionInvalidate) / `149-157` (teardownCurrent) /
  `159-164` (apply) / `166-173` (finishSessionReplacement) /
  `366-373` (dispose) / `310-349` (rebind closure in rpc_mode.py).
- `run_rpc_mode` signature accepts the new keyword-only
  `runtime_host: AgentSessionRuntime | None = None` parameter +
  companion `harness_factory: HarnessFactory | None = None`.
- `_make_passthrough_runtime` importable from `rpc_mode`.
- Pi fixture pinned at SHA `734e08e` with `runtime_class_lines ==
  "67-374"` + `rebind_session_lines == "310-349"` +
  `architecture_decision == "harness-rebuild"`.

### Sprint 6h₄c carry-forward roster (binding)

Each runtime method already EXISTS on `AgentSessionRuntime` from
6h₄b but currently raises `NotImplementedError("Sprint 6h₄c —
ADR-0078")`. 6h₄c wires the matching RPC handlers + implements the
4 stubbed methods.

- **`switch_session`** — Pi `rpc-mode.ts:566`. 6h₄c wires
  `_handle_switch_session` calling
  `await runtime_host.switch_session(cmd.session_path)`. Wire shape:
  `{cancelled: bool}` (Pi).
- **`fork`** — Pi `rpc-mode.ts:574`. 6h₄c wires `_handle_fork` calling
  `await runtime_host.fork(cmd.entry_id, opts={})`. Wire shape:
  `{text: str | None, cancelled: bool}` (Pi mapping
  `{selectedText, cancelled}` → snake_case/camelCase wire).
- **`clone`** — Pi `rpc-mode.ts:586`. 6h₄c wires `_handle_clone`
  calling `await runtime_host.fork(leaf_id, opts={"position": "at"})`.
  Needs `leaf_id = await runtime_host.harness._session.get_leaf_id()`.
  Returns `{cancelled: bool}` (Pi drops `selectedText` for clone).
- Real `_emit_before_switch` / `_emit_before_fork` extension cancel
  hooks (P-308 fill-in).
- P-307 `session_shutdown` extension event emit from
  `AgentHarness.dispose()` (or a runtime-level wrapper).
- P-313 widen `HarnessFactory` for full Pi field refresh (`_services`
  / `_diagnostics` / `_modelFallbackMessage`).
- P-314 `with_session: Callable[[ReplacedSessionContext],
  Awaitable[None]] | None = None` 2-stage callback.
- P-315 `set_rebind_session` / `set_before_session_invalidate`
  optional-cb signatures (Pi accepts `undefined` to clear).

### Cumulative carry-forwards from earlier ADRs (still open)

- **From ADR-0076 (Sprint 6h₄a):**
  - Pi HTML visual fidelity (CSS framework, syntax highlighting,
    responsive layout — Sprint 6h₅+).
  - `ImageContent` rendering in HTML export — Sprint 6h₅+.
  - `outputPath` default resolution rules (Pi
    `export-html.ts:273-277` — currently match; any future
    divergence re-opens).
- **From ADR-0074 (Sprint 6h₃):**
  - `_get_context_usage_safe` real implementation (P-282 — model
    registry + per-turn token tracking + last-assistant message
    tokens).
  - Live `session_id` read via session manager (P-291).
  - Pi-source-grep verification tooling (P-286).
- **From ADR-0073 (Sprint 6h₃):**
  - `_get_context_usage_safe` real implementation (same tracking as
    ADR-0074).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₄a (start of 6h₄b) | 26 | 3 | 29 |
| Sprint 6h₄b (this ADR) | **26** | **3** | **29** |

**Counts UNCHANGED** — foundation sprint, no movement.

## Consequences

- **`AgentSessionRuntime` foundation** lives in
  `aelix_agent_core.runtime`. The runtime class, 5 getters, 7 public
  methods, 2 frozen dataclasses, and the private replace seam are
  all live; the 4 public replace APIs raise `NotImplementedError`
  pending 6h₄c wiring.
- **`run_rpc_mode` is backward-compat** — the 26 already-wired RPC
  handlers see zero behavior change, the new `runtime_host` kwarg is
  optional, and the rebind closure is installed against a no-replace
  passthrough when no `runtime_host` is supplied.
- **The 3 deferred commands** remain in `DEFERRED_COMMANDS` with
  owner string `"ADR-0078 — Sprint 6h₄c wires runtime-host bridge"`.
  Each invocation returns an `RpcErrorResponse` with `ADR-0078` in
  the error message (locked by closure pin + shim test #7).
- **The forward-compat clause holds:** any PR landing a deferred
  command MUST drop it from `DEFERRED_COMMANDS` in the same PR
  (enforced by the closure pin).
- **6h₄c has zero runtime work** — the foundation is complete; 6h₄c
  fills in the 4 stubbed replace APIs + wires 3 RPC handlers + lifts
  P-307 / P-308 / P-313 / P-314 carry-forward items.

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₄b row this sprint).
- ADR-0077 — `AgentSessionRuntime` Pi port + `rebindSession` seam
  (FOUNDATION-ONLY).
- ADR-0076 — Sprint 6h₄a Phase 4.11 closure (amended this sprint
  with the foundation update note; ownership of the 3 deferred
  commands rebrands from ADR-0076 → ADR-0078).
- ADR-0073 / ADR-0074 — Sprint 6h₃ carry-forward parent of
  `_get_context_usage_safe` / live `session_id` read /
  Pi-source-grep verification.
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₄b / Phase 4.12 / W6 (shipped).
