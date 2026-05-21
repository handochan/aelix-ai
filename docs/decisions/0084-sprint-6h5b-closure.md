# 0084. Sprint 6h₅b Phase 4.15 — Strict Superset Closure + 6h₅c Carry-Forward (Runtime callback Pi parity closed + Phase 4 RPC roster STILL CLOSED)

Status: Accepted (Sprint 6h₅b / Phase 4.15 / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Phase 4.15 closes the runtime callback Pi parity sprint shipped under
ADR-0083 with the strict-superset audit lane (W4 code-review opus +
W5 architect opus Pi parity audit per ADR-0032). The W4/W5 audit
returned **3 MAJOR + 2 MINOR (no BLOCKING)** must-fix items. Sprint
6h₅b W6 applied every must-fix item in 5 atomic commits.

This ADR records the closure pin invariants and the **runtime callback
Pi parity milestone**: Aelix now ships the full Pi runtime callback
surface — `with_session` / `setup` 2-stage callbacks on the three
replace APIs (`switch_session` / `new_session` / `fork`), the
:meth:`AgentSessionRuntime.import_from_jsonl` real body replacing the
Sprint 6h₄c stub, :meth:`JsonlSessionRepo.fork_from` cross-cwd import
surface, and :meth:`ExtensionRunner.invalidate` + bridge wired
end-to-end with :data:`PI_STALENESS_MESSAGE` single source of truth.
The Phase 4 RPC roster STAYS CLOSED at 29 / 0 / 29 — extension polish
doesn't change the RPC discriminator union.

## Decision

### Closure pin invariants

ADR-0082 (Sprint 6h₅a) records the existing closure pin invariants
(`tests/pi_parity/test_phase_4_14_extension_events.py` — 35-name
`HookEventName` cascade + reducer + Pi line citations). Sprint 6h₅b
does NOT extend the formal pi_parity closure pin (no new events / no
new RPC commands). Instead, the runtime callback Pi parity invariants
are locked at the unit-test level by the 6 new test files shipped this
sprint:

- `tests/runtime/test_replaced_session_context.py` — Protocol
  conformance + factory `SimpleNamespace` baseline + send_message /
  send_user_message routing + 6 :class:`ExtensionCommandContext`
  method exposure (P-364) + unbound-runtime raise + reload stub.
- `tests/runtime/test_with_session_callback.py` — 3 replace APIs
  accept `with_session` + callback fires AFTER rebind on NEW
  harness + raises propagate + ordering: rebind → with_session.
- `tests/runtime/test_setup_callback_new_session.py` — `setup`
  invoked with NEW session_manager + ordering before rebind +
  message rebuild reflects setup appends + optional path.
- `tests/runtime/test_import_from_jsonl_real.py` — missing path
  raises :class:`SessionImportFileNotFoundError` + Pi-verbatim
  `File not found: {file_path}` message + `file_path` attr (P-366)
  + same-dir skips copy + cwd override rewrites metadata + cancel
  short-circuits + different-dir copies file.
- `tests/session/test_jsonl_fork_from.py` — ALL entries copy (no
  leaf truncation, unlike `fork`) + target cwd matches + new
  `parent_session_path = source.path` + new id/path + round-trip
  through `repo.open` + optional `session_dir` override (P-368) +
  default resolution preserved.
- `tests/harness/test_extension_runner_invalidate.py` — bridge
  propagation + default = :data:`PI_STALENESS_MESSAGE` (P-362)
  + idempotent + no-bridge no-op + harness wires bridge +
  :meth:`_ExtensionRuntime.invalidate` default aligned + teardown
  calls invalidate (P-363) + dispose calls invalidate (P-363) +
  `assert_active` no-op SYNTHESIS.

### W4 / W5 audit triage closure

- **P-364 W5 MAJOR fix CLOSED** — :class:`ReplacedSessionContext`
  Protocol extended with 6 :class:`ExtensionCommandContext`
  methods (Pi `extensions/types.ts:371` extends
  `ExtensionCommandContext`). Factory
  :meth:`create_replaced_session_context` gains optional ``runtime``
  kwarg threaded from :meth:`_finish_session_replacement`.
- **P-365 W5 MAJOR fix CLOSED** — :meth:`ExtensionRunner.assert_active`
  delegates to runtime via bridge (raises plain
  :class:`RuntimeError` to avoid `aelix_agent_core → aelix_coding_agent`
  reverse import; :class:`ExtensionError` continues to fire via
  :meth:`ExtensionContext.__getattribute__`).
- **P-366 W5 MAJOR fix CLOSED** — :class:`SessionImportFileNotFoundError`
  message + attribute Pi-verbatim aligned
  (`File not found: {file_path}` + `file_path` attr per
  `agent-session-runtime.ts:39-47`).
- **P-367 W5 MINOR fix CLOSED** — :meth:`JsonlSessionRepo.open`
  optional `cwd_override` keyword arg replaces the
  `storage._metadata` mutation from outside the repo.
- **P-368 W5 MINOR fix CLOSED** — :meth:`JsonlSessionRepo.fork_from`
  optional `session_dir` keyword arg mirrors Pi 3rd parameter.
- **W4 MINOR-2 self-resolved** — the private-attribute writeback in
  `import_from_jsonl` no longer exists after P-367 lands.

### ADR-0082 carry-forward items now CLOSED

Sprint 6h₅a's ADR-0082 §"Sprint 6h₅b carry-forward" enumerated 5
runtime/extension polish items. ADR-0083 ships them; ADR-0084 records
the closure:

- ✅ `with_session` 2-stage callback for `finishSessionReplacement`
  (P-358).
- ✅ `setup` callback in `new_session` (P-359).
- ✅ `forkFrom` cross-cwd import (P-361).
- ✅ `import_from_jsonl` real runtime body (P-360).
- ✅ P-351 :meth:`ExtensionRunner.invalidate` semantics (P-362 + P-363).
- ✅ P-315 `set_rebind_session` / `set_before_session_invalidate`
  optional-cb signature widening — the W2 implementation already
  ships these as `Optional` per ADR-0077; ADR-0082 erroneously
  listed it as open. Sprint 6h₅b W6 verifies the existing wiring
  matches Pi and marks the item explicitly CLOSED.

### Sprint 6h₅c carry-forward (Pi parity items still open)

> **Sprint 6h₅c update (2026-05-22)** — ALL 5 binding items below are
> **CLOSED** per ADR-0085 (Sprint 6h₅c Phase 4.16 sibling) + ADR-0086
> (A 단계 closure). The 2 lower-priority items (live `session_id` +
> Pi-source-grep tooling) remain as carry-forward to Sprint 6h₅d.
> **A 단계 (Phase 4 strict Pi-parity superset) CLOSED** end-to-end —
> see ADR-0086 §"A 단계 closure ledger" for the full 14-row delivery
> mapping across the 6a → 6h₅c sprint chain. Phase 4 RPC roster STAYS
> CLOSED at 29 / 0 / 29.

The remaining Sprint 6h₅+ carry-forward roster from ADR-0082 splits
into Sprint 6h₅c (the next sprint). **None of these items touch the
RPC dispatch table** — they surface via HTML emitter polish, context
usage tracking, factory bootstrap integration, or TUI commands.

**Sprint 6h₅c — bootstrap + HTML / context polish:**

- ✅ **`session_start` bootstrap emit** (Pi `reason="startup"` /
  `"reload"`). **CLOSED per ADR-0085 P-371** —
  :func:`create_agent_session_runtime` module-level async factory
  emits `SessionStartHookEvent(reason="startup")` at bootstrap
  matching Pi `:326` + `:2050`. The `reload` branch (Pi `:2401`)
  defers to Sprint 6h₅d when Aelix grows a `reload()` primitive.
- ✅ **Factory bootstrap `assertSessionCwdExists` call site** (Pi
  `:391`). **CLOSED per ADR-0085 P-370** — :func:`create_agent_session_runtime`
  factory runs :func:`assert_session_cwd_exists` against
  `harness._session` BEFORE :class:`AgentSessionRuntime`
  construction, matching Pi line `:391`. Skipped silently when
  `harness._session is None` (in-memory factory invocation, e.g.
  tests).
- ✅ **`importFromJsonl` `assertSessionCwdExists` call site** (Pi
  `:352`). **CONFIRMED CLOSED** — Sprint 6h₅b's `import_from_jsonl`
  body wired :func:`assert_session_cwd_exists` after `repo.open`
  matching Pi `:352`; Sprint 6h₅c bootstrap-site wiring (Pi `:391`)
  + the factory closure pin tests confirm both sites match Pi.
- ✅ **Pi HTML visual fidelity** (CSS framework, syntax highlighting,
  responsive layout) — carry-forward from ADR-0074. **CLOSED per
  ADR-0085 P-372** — `_export_html/` directory restructure ships
  3-module package (`__init__.py` + `template.py` + `format.py`)
  with markdown-it-py (commonmark + table + breaks) + Pygments
  fenced-code highlighting + curated dark theme constant. Tool-
  renderer + ANSI pipeline + color-derivation math defer to Sprint
  6h₅d.
- ✅ **`ImageContent` rendering in HTML export** — carry-forward
  from ADR-0074. **CLOSED per ADR-0085 P-373 + P-377** — inline
  base64 `<img>` tag with `data:{mime};base64,{data}` URI mirroring
  Pi `template.js:909`; tool-result variant uses strict
  `class="tool-image"` literal per Pi (P-377 W5 MINOR fix).
- ✅ **`_get_context_usage_safe` real implementation** (P-282 — model
  registry + per-turn token tracking + last-assistant message
  tokens) — carry-forward from ADR-0074. **CLOSED per ADR-0085
  P-369 + P-374** — harness-level method runs full Pi `getContextUsage`
  algorithm (compaction sentinel + post-compaction usage walk +
  heuristic fallback) over async :meth:`Session.get_branch`; the
  extension-context bridge surfaces a real sync :class:`ContextUsage`
  triple via the heuristic estimate path (W6 P-374 W5 MAJOR fix
  replaces W2 stub).
- **Live `session_id` read via session manager** (P-291) —
  carry-forward from ADR-0074 + ADR-0084 → **DEFERRED to Sprint 6h₅d
  per ADR-0085**.
- **Pi-source-grep verification tooling** (P-286) — carry-forward
  from ADR-0074 + ADR-0084 → **DEFERRED to Sprint 6h₅d per
  ADR-0085**.

### Items CONFIRMED dropped (per Sprint 6h₅b)

None this sprint. P-313 `HarnessFactory` 4-field refresh remains
DROPPED per ADR-0080 (harness-rebuild encapsulates services +
diagnostics + model_fallback_message via factory closure).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₅a (start of 6h₅b) | 29 | 0 | 29 |
| Sprint 6h₅b (this ADR) | **29** | **0** | **29** |

**RPC roster UNCHANGED.** Extension polish doesn't change the
dispatch table. Phase 4 RPC remains CLOSED.

| Closure type | Counted | Notes |
|---|---|---|
| HookEventName Literal names | **35** | Unchanged from Sprint 6h₅a |
| `ExtensionAPI.on` overloads | **35** | Unchanged from Sprint 6h₅a |
| `HookBus.on` overloads | **35** | Unchanged from Sprint 6h₅a |
| `AgentHarnessEventName` names | **22** | Unchanged from Sprint 6h₅a |
| `_REDUCERS` registry size | **35** | Unchanged from Sprint 6h₅a |
| `HOOK_RESULT_TYPES` registry | **35** | Unchanged from Sprint 6h₅a |
| `ReplacedSessionContext` Protocol members | **19** | Sprint 6h₅a W2 13 + Sprint 6h₅b W6 P-364 6-method `ExtensionCommandContext` extension |

## Consequences

- **Runtime callback Pi parity wired end-to-end.** The 3 replace
  APIs (`switch_session` / `new_session` / `fork`) now accept
  Pi-shaped `with_session` callbacks; `new_session` additionally
  accepts the Pi `setup` callback that runs against the NEW
  :class:`ReadonlySessionManager` before the wire layer captures
  the new reference. The :class:`ReplacedSessionContext` handle
  bypasses the OLD harness's stale guard exactly as Pi does.

- **ADR-0082 carry-forward roster (Sprint 6h₅b subset) CLOSES.**
  All 5 items + the W2-already-shipped P-315 land this sprint
  with Pi parity. ADR-0082 amendment records the closure.

- **`SessionImportFileNotFoundError` Pi-verbatim wire shape pinned.**
  Closure pin (`tests/runtime/test_import_from_jsonl_real.py::test_session_import_file_not_found_error_pi_message_and_attr`)
  asserts the rendered message equals
  ``f"File not found: {missing}"`` and the attribute is
  ``file_path`` matching Pi `agent-session-runtime.ts:39-47`
  verbatim.

- **:meth:`ExtensionRunner.invalidate` + bridge SYNTHESIS pinned.**
  Closure pin (`tests/harness/test_extension_runner_invalidate.py`)
  asserts the runner has NO `_stale_message` field; the runtime is
  the single source of truth; :data:`PI_STALENESS_MESSAGE` is the
  default propagated when caller omits the message;
  :meth:`_ExtensionRuntime.invalidate` default aligns. Any PR
  reverting the SYNTHESIS trips.

- **`runner.invalidate` ordering in `_teardown_current` + `dispose`
  pinned.** Closure pin asserts both methods invoke `runner.invalidate`
  between EMIT and `before_session_invalidate`, matching Pi
  `runner.ts:466-473` insertion point.

- **`JsonlSessionRepo` repo seam encapsulation.** The `cwd_override`
  + `session_dir` optional kwargs centralize Pi parity for
  `SessionManager.open` (cwd override) + `forkFrom` (sessionDir)
  on a single owner. External callers no longer need to mutate
  `storage._metadata` from outside the repo.

- **`ReplacedSessionContext` Protocol members extended 13 → 19.**
  Sprint 6h₅b W6 P-364 fix widens the Protocol to mirror Pi
  `ReplacedSessionContext extends ExtensionCommandContext` per
  `extensions/types.ts:371`. The factory wires the 6 commands via
  the optional `runtime` kwarg threaded from
  :meth:`_finish_session_replacement`.

- **Phase 4 RPC roster STAYS CLOSED.** Runtime / extension polish
  has no dispatch impact. The forward-compat clause holds.

- **Sprint 6h₅c carry-forward is scoped.** 6h₅c handles HTML visual
  fidelity + `_get_context_usage_safe` + `ImageContent` rendering
  + factory bootstrap `session_start` + factory bootstrap
  `assertSessionCwdExists`. None of these change the RPC dispatch
  table.

## References

- ADR-0083 — Sprint 6h₅b runtime callback Pi parity decisions
  (sibling ADR — 5 W0 binding items + 5 W4/W5 audit triage items).
- `tests/runtime/test_replaced_session_context.py` (NEW — 8 tests)
- `tests/runtime/test_with_session_callback.py` (NEW — 6 tests)
- `tests/runtime/test_setup_callback_new_session.py` (NEW — 4 tests)
- `tests/runtime/test_import_from_jsonl_real.py` (NEW — 6 tests)
- `tests/session/test_jsonl_fork_from.py` (NEW — 7 tests)
- `tests/harness/test_extension_runner_invalidate.py` (NEW — 9
  tests)
- `tests/extensions/test_extension_runner_emit_delegate.py` (AMEND —
  `frozen=True` drop verified per P-362)
- `tests/runtime/test_agent_session_runtime.py` (AMEND —
  `import_from_jsonl` stub coverage test moved to
  `test_import_from_jsonl_real.py`)
- `packages/agent/src/core/agent-session-runtime.ts:39-47` (Pi
  `SessionImportFileNotFoundError` — P-366 verbatim port target)
- `packages/agent/src/core/agent-session-runtime.ts:172-173` (Pi
  `withSession` invocation point — P-358 source)
- `packages/agent/src/core/agent-session-runtime.ts:226-229` (Pi
  `setup` callback position — P-359 source)
- `packages/agent/src/core/agent-session-runtime.ts:329-364` (Pi
  `importFromJsonl` body — P-360 source)
- `packages/agent/src/core/agent-session.ts:3087-3095` (Pi
  `createReplacedSessionContext` factory — P-357 source)
- `packages/agent/src/harness/extensions/types.ts:333-381` (Pi
  `ExtensionCommandContext` + `ReplacedSessionContext` — P-356 +
  P-364 source)
- `packages/agent/src/harness/session/session-manager.ts:1353-1394`
  (Pi `forkFrom` — P-361 + P-368 source)
- `packages/agent/src/harness/extensions/runner.ts:466-478` (Pi
  `ExtensionRunner.invalidate` + `assertActive` — P-362 + P-365
  source)
- `packages/agent/src/harness/extensions/runner.ts:467` (Pi
  staleness verbatim string — :data:`PI_STALENESS_MESSAGE` source)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₅b row this sprint).
- ADR-0083 — Sprint 6h₅b runtime callback Pi parity decisions
  (sibling ADR).
- ADR-0082 — Sprint 6h₅a Phase 4.14 strict-superset closure +
  Sprint 6h₅b carry-forward roster (amended this sprint:
  ADR-0082 §"Sprint 6h₅b carry-forward" items CLOSE per
  ADR-0083; Sprint 6h₅c carry-forward items remain open in
  ADR-0082 + this ADR).
- ADR-0081 — Sprint 6h₅a extension event Pi parity decisions
  (Sprint 6h₅b sits on top of the extension event lifecycle
  wiring).
- ADR-0080 — Sprint 6h₄c Phase 4.13 strict-superset closure +
  Sprint 6h₅+ carry-forward roster (P-314 / P-315 / P-310
  `import_from_jsonl` CLOSE per ADR-0083).
- ADR-0077 — :class:`AgentSessionRuntime` Pi port + `rebindSession`
  seam (Sprint 6h₄b foundation).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₅b / Phase 4.15 / W6 (shipped — **runtime callback Pi
parity CLOSED**; Phase 4 RPC roster STAYS CLOSED).
