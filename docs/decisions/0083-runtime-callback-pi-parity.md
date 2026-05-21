# 0083. Sprint 6h₅b Phase 4.15 — Runtime callback Pi parity (`with_session` / `setup` / `import_from_jsonl` / `fork_from` / `ExtensionRunner.invalidate` + W5 P-364~P-368 fixes)

Status: Accepted (Sprint 6h₅b / Phase 4.15 / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₅a (ADR-0081 / ADR-0082) CLOSED the extension session lifecycle
event wiring (4 Pi events + `session_cwd` helper + W5 P-355 dispose
ordering correction) but explicitly carried 5 items forward to
Sprint 6h₅b per ADR-0082 §"Sprint 6h₅b carry-forward":

- `with_session` 2-stage callback for `finishSessionReplacement`
  (Pi `agent-session-runtime.ts:172-173` / `:289-291` / `:316-318`).
- `setup` callback in `new_session` (Pi `:226-229`).
- `set_rebind_session` / `set_before_session_invalidate` optional-cb
  signature widening (P-315 carry-forward).
- `forkFrom` cross-cwd import (no internal consumer yet — surface only
  for Phase 5 CLI `/branch-from`).
- `import_from_jsonl` real runtime body (replaces the Sprint 6h₄c stub).
- `session_start` bootstrap emit (Pi `reason="startup"` / `"reload"` —
  deferred to Sprint 6h₅c per factory pattern integration).
- P-351 `ExtensionRunner.invalidate` semantics (Pi `runner.ts:466-473`
  + `runner.ts:467` verbatim staleness string).

Sprint 6h₅b (Phase 4.15) closes the runtime/extension callback subset
of that carry-forward roster: P-356 ~ P-363 ship the 8 binding W0
items; W4 (code-review opus) + W5 (architect opus Pi parity audit)
returned **3 MAJOR + 2 MINOR (no BLOCKING)** must-fix items that the
W6 closure absorbs:

- P-364 W5 MAJOR — `ReplacedSessionContext` Protocol missing 6
  `ExtensionCommandContext` methods (Pi `extensions/types.ts:371`
  extends `ExtensionCommandContext`).
- P-365 W5 MAJOR — `ExtensionRunner.assert_active()` silent no-op
  diverged from Pi `runner.ts:475-478` (Pi raises `Error(staleMessage)`).
- P-366 W5 MAJOR — `SessionImportFileNotFoundError` message + attribute
  diverged from Pi `agent-session-runtime.ts:39-47`.
- P-367 W5 MINOR — `import_from_jsonl` mutated `storage._metadata` from
  outside the repo seam.
- P-368 W5 MINOR — `fork_from` missing optional 3rd parameter
  `session_dir?` (Pi `forkFrom(source, targetCwd, sessionDir?)`).
- W4 MINOR-2 self-resolves when P-367 lands (private-attr writeback
  drops out of `import_from_jsonl`).

The **Phase 4 RPC roster STAYS CLOSED** at 29 / 0 / 29 — Sprint 6h₅b
extension polish does NOT change the dispatch table.

## Decision

### Pi parity decisions (P-356 ~ P-363)

- **P-356 — `ReplacedSessionContext` placement (BINDING).** Lands as
  :class:`typing.Protocol` in `runtime/_types.py` rather than a concrete
  subclass in `aelix_coding_agent.extensions`. Three rationale points:
  (1) avoids the cross-package import cycle that a concrete class in
  `aelix_coding_agent.extensions` would force on `aelix_agent_core.harness.core`;
  (2) the factory returns a :class:`types.SimpleNamespace` which cannot
  subclass a Protocol — structural conformance via
  :data:`typing.runtime_checkable` is the only path; (3) bypasses
  :class:`ExtensionContext.__getattribute__`'s staleness guard so
  post-replacement work runs against the NEW harness without tripping
  the OLD harness's stale flag.

- **P-357 — `create_replaced_session_context` factory (BINDING).**
  On :class:`AgentHarness`. Returns :class:`types.SimpleNamespace` (Pi
  ``Object.defineProperties`` clone idiom). Wraps `_make_context()`
  baseline + overlays `send_message` / `send_user_message`.

- **P-358 — `with_session` parameter (BINDING).** Plumbed onto
  `switch_session` / `new_session` / `fork` / `_finish_session_replacement`.
  Type: ``Callable[[ReplacedSessionContext], Awaitable[None]] | None = None``.
  Order in `_finish_session_replacement`: teardown → apply → setup →
  rebind → session_start emit → `with_session(create_replaced_session_context())`.

- **P-359 — `setup` callback in `new_session` (BINDING).** Position:
  AFTER `_apply`, BEFORE rebind. After setup, rebuilds
  ``harness._state.messages = list((await new_session.build_context()).messages)``
  so any `session.append_*` calls made inside `setup` reflect in the
  active turn context.

- **P-360 — `import_from_jsonl` real body (BINDING).** Pi
  `agent-session-runtime.ts:329-364` port: resolve → exists probe →
  mkdir → destination → cancel hook → snapshot → copy-if-different →
  load metadata + cwd override → repo.open → assert_session_cwd_exists →
  `_finish_session_replacement(reason="resume")`. NO `with_session`
  plumbing (Pi signature confirms — `(path, cwd?)`, no callbacks).
  Adds :class:`SessionImportFileNotFoundError` to `runtime/_types.py`.

- **P-361 — `JsonlSessionRepo.fork_from` (BINDING).** Cross-cwd import.
  Pi `session-manager.ts:1353-1394`. Loads ALL source entries (no leaf
  truncation, unlike `fork`), rewrites cwd header,
  `parent_session_path=source.path`. No internal consumer in 6h₅b — CLI
  `/branch-from` lands Phase 5.

- **P-362 — `ExtensionRunner.invalidate` + runtime bridge (BINDING —
  SYNTHESIS per spec §J).** Drops `frozen=True` on `ExtensionRunner`
  dataclass; adds `_invalidate_runtime: Callable[[str], None] | None`
  bridge field; adds `invalidate(message=None)` that delegates through
  the bridge to :meth:`_ExtensionRuntime.invalidate`. Adds
  :data:`PI_STALENESS_MESSAGE` constant in `runtime/_types.py` (Pi
  verbatim string from `runner.ts:467`). Aligns
  :meth:`_ExtensionRuntime.invalidate` default to :data:`PI_STALENESS_MESSAGE`.

  **Synthesis decision (spec §J):** single source of truth via runtime —
  `_ExtensionRuntime._stale_message` is the only mutable flag;
  :class:`ExtensionRunner` is the public Pi-named entry point that
  delegates. NO `_stale_message` field on the runner.

- **P-363 — `runner.invalidate` calls in teardown / dispose (BINDING).**
  Inserts `runner.invalidate(PI_STALENESS_MESSAGE)` in both
  `_teardown_current` AND `dispose` between EMIT and
  `before_session_invalidate`. Order: emit_shutdown → runner.invalidate
  → before_session_invalidate → harness.dispose.

### W5 audit triage (P-364 ~ P-368 — W6 closure)

- **P-364 W5 MAJOR fix — `ReplacedSessionContext` Protocol extended
  with 6 `ExtensionCommandContext` methods.** Pi
  `extensions/types.ts:371` declares
  ``ReplacedSessionContext extends ExtensionCommandContext`` —
  Pi `:333-364` defines the 6 command methods (`waitForIdle` /
  `newSession` / `fork` / `navigateTree` / `switchSession` /
  `reload`). W2 shipped the Protocol with only the 13 non-command
  fields; W6 widens to 19. Factory `create_replaced_session_context`
  gains an optional ``runtime`` keyword that
  :meth:`_finish_session_replacement` threads with ``self`` (the
  current runtime) so the 3 runtime-bound commands
  (`new_session` / `fork` / `switch_session`) wire through to the
  SAME runtime that just replaced this harness. `wait_for_idle` /
  `navigate_tree` route to the harness (these methods already exist).
  `reload` is an Aelix-additive stub raising
  :class:`NotImplementedError` for Protocol conformance + future TUI
  integration. When ``runtime=None`` (test-only / unattached path) the
  3 runtime commands become :class:`RuntimeError("not bound to a
  runtime")` so a half-wired ctx fails loud.

- **P-365 W5 MAJOR fix — `ExtensionRunner.assert_active()` delegates
  to the runtime via the bridge.** Pi `runner.ts:475-478` raises
  ``Error(staleMessage)``. W2 shipped a silent no-op rationalised
  as "the runtime owns the single source of truth so the runner has
  nothing to check". W5 audit observed this drops Pi parity for a
  caller routing through `runner.assert_active()` — Pi does NOT
  no-op there. W6 keeps the SYNTHESIS (no `_stale_message` on the
  runner) but `assert_active` now reads through the bridge: when
  `_invalidate_runtime` is wired, the runner reaches via the bridge
  to the underlying staleness state and raises if stale. To avoid
  the `aelix_agent_core → aelix_coding_agent` reverse dependency that
  importing :class:`ExtensionError` would create, the runner raises
  plain :class:`RuntimeError(staleMessage)`; callers wanting the
  rich :class:`ExtensionError` continue to reach via
  :meth:`ExtensionContext.__getattribute__` → underlying
  :meth:`_ExtensionRuntime.assert_active` (which DOES raise
  `ExtensionError`).

- **P-366 W5 MAJOR fix — `SessionImportFileNotFoundError` aligned to
  Pi verbatim.** Pi `agent-session-runtime.ts:39-47`:

  ```typescript
  class SessionImportFileNotFoundError extends Error {
      readonly filePath: string;
      constructor(filePath: string) {
          super(`File not found: ${filePath}`);
          this.name = "SessionImportFileNotFoundError";
          this.filePath = filePath;
      }
  }
  ```

  W2 shipped a non-Pi message (`Session import file not found: ...`)
  and a non-Pi attribute (`path` instead of `filePath` / `file_path`).
  W6 forward-fix: message is verbatim `File not found: {file_path}`;
  attribute is `file_path` (snake_case of Pi `filePath`).

- **P-367 W5 MINOR fix — `import_from_jsonl` repo seam.**
  :meth:`JsonlSessionRepo.open` gains optional ``cwd_override: str |
  None = None`` keyword argument. When supplied, the repo writes back
  ``storage._metadata = dataclasses.replace(metadata, cwd=cwd_override)``
  INSIDE the repo. :meth:`AgentSessionRuntime.import_from_jsonl`
  replaces the post-open private-attribute mutation with the new
  ``cwd_override=`` keyword pass-through. Pi parity:
  ``SessionManager.open(path, dir, cwdOverride)`` threads the override
  into the loaded session's `cwd` field. The private-attribute touch
  is now encapsulated on a single owner.

- **P-368 W5 MINOR fix — `fork_from` optional `session_dir` param.**
  Pi `forkFrom(source, targetCwd, sessionDir?)`. W2 shipped without
  the optional 3rd parameter. W6 adds it as keyword-only ``session_dir:
  str | None = None``; when supplied, replaces the default
  ``_get_session_dir(target_cwd)`` resolution. Useful for callers that
  already own a resolved directory (tests pinning tmp_path,
  future per-workspace TUI work). When omitted, the default Pi-shape
  resolution is preserved (no behavioral change for existing callers).

- **W4 MINOR-2 self-resolved** — the private-attribute writeback in
  `import_from_jsonl` no longer exists after P-367 lands.

### Aelix-additive divergences (documented)

1. **Protocol (not concrete subclass) for `ReplacedSessionContext`.**
   :data:`typing.runtime_checkable` Protocol over
   :class:`types.SimpleNamespace` factory output. Rationale: avoids the
   cross-package import cycle that a concrete subclass would force +
   bypasses :class:`ExtensionContext.__getattribute__`'s stale guard.

2. **`SimpleNamespace` factory.** Pi uses ``Object.defineProperties``
   to clone an :class:`ExtensionContext`; Aelix mirrors with
   :class:`types.SimpleNamespace` (closest Python equivalent + cannot
   subclass a Protocol → structural conformance only).

3. **`session_start` emit between rebind and `with_session` callback.**
   Sprint 6h₅a P-343 lands this Aelix-only — Pi emits `session_start`
   after rebind but BEFORE `with_session` in the same sequence.
   Documented as additive in ADR-0081; closure-pinned in
   Sprint 6h₅a `tests/runtime/test_agent_session_runtime_extension_events.py`.

4. **`ExtensionRunner.assert_active` raises plain
   :class:`RuntimeError` at the runner layer (not
   :class:`ExtensionError`).** Per spec §J SYNTHESIS the runtime owns
   the single source of truth; the runner's `assert_active` reads
   through the bridge but raises :class:`RuntimeError` rather than
   :class:`ExtensionError` to avoid the
   `aelix_agent_core → aelix_coding_agent` reverse import cycle.
   :class:`ExtensionError` continues to fire via
   :meth:`ExtensionContext.__getattribute__` → underlying
   :meth:`_ExtensionRuntime.assert_active`, which is the Pi-shaped
   error surface for extension code.

5. **`reload` is Aelix-additive stub.** Aelix has no in-place reload
   primitive today; Pi's `reload` is a TUI helper. Exposed for Protocol
   conformance + future TUI integration.

## Roster

**W0 (binding spec, P-356 ~ P-363):**

- P-356 — `ReplacedSessionContext` placement (Protocol in
  `runtime/_types.py`).
- P-357 — `create_replaced_session_context` factory on
  :class:`AgentHarness`.
- P-358 — `with_session` parameter plumbed onto 3 replace APIs +
  `_finish_session_replacement`.
- P-359 — `setup` callback in `new_session` (AFTER apply, BEFORE
  rebind, rebuild messages).
- P-360 — `import_from_jsonl` real body + :class:`SessionImportFileNotFoundError`.
- P-361 — :meth:`JsonlSessionRepo.fork_from` cross-cwd import.
- P-362 — :meth:`ExtensionRunner.invalidate` + runtime bridge
  (SYNTHESIS).
- P-363 — `runner.invalidate` calls in teardown / dispose.

**W5 audit triage (P-364 ~ P-368):**

- P-364 — :class:`ReplacedSessionContext` Protocol extended with 6
  :class:`ExtensionCommandContext` methods + factory `runtime` kwarg.
- P-365 — :meth:`ExtensionRunner.assert_active` delegates to runtime
  via bridge.
- P-366 — :class:`SessionImportFileNotFoundError` aligned to Pi
  `File not found: {file_path}` + `file_path` attr.
- P-367 — :meth:`JsonlSessionRepo.open` `cwd_override` keyword arg
  replaces `storage._metadata` mutation in `import_from_jsonl`.
- P-368 — :meth:`JsonlSessionRepo.fork_from` optional `session_dir`
  keyword arg mirroring Pi 3rd parameter.

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₅a (start of 6h₅b) | 29 | 0 | 29 |
| Sprint 6h₅b (this ADR) | **29** | **0** | **29** |

**RPC roster UNCHANGED** — runtime / extension polish doesn't alter
the dispatch table. Phase 4 RPC remains CLOSED.

## Consequences

- **`with_session` / `setup` 2-stage callbacks Pi-parity wired.**
  Extensions can now run post-replacement work via the
  :class:`ReplacedSessionContext` handle that bypasses the OLD
  harness's stale guard. `setup` runs against the NEW
  :class:`ReadonlySessionManager` before the wire layer captures
  the new harness reference.

- **`import_from_jsonl` Pi-parity real body lands.** The Sprint 6h₄c
  `NotImplementedError` stub is replaced; Pi
  `agent-session-runtime.ts:329-364` is ported verbatim with a
  Pi-shaped cancel short-circuit, copy-when-different semantics,
  cwd override via `dataclasses.replace`, and Pi-verbatim
  :class:`SessionImportFileNotFoundError` error surface.

- **`JsonlSessionRepo.fork_from` surface lands.** No internal
  consumer in this sprint; the surface is exposed so the wire
  shape stays Pi-parity ahead of the Phase 5 CLI `/branch-from`
  consumer.

- **`ExtensionRunner.invalidate` + bridge SYNTHESIS.** The runner
  is the Pi-named entry point (callers say `runner.invalidate(msg)`)
  but the runtime is the single source of truth for staleness
  state. :data:`PI_STALENESS_MESSAGE` is shared across both
  packages; the W2 divergent default in
  :meth:`_ExtensionRuntime.invalidate` is corrected.

- **W2 §J SYNTHESIS preserved end-to-end.** Spec §J binding was
  "single source of truth via runtime"; W6 P-365 closure keeps the
  no-`_stale_message`-on-runner invariant while making
  `assert_active` reach through the bridge to honor Pi
  `runner.ts:475-478` (Pi's runner-layer `assert_active` raises;
  Aelix mirrors via :class:`RuntimeError` instead of
  :class:`ExtensionError` to avoid the reverse import cycle).

- **`SessionImportFileNotFoundError` Pi-verbatim wire shape.**
  Message format and attribute name both align to Pi
  `agent-session-runtime.ts:39-47`; extensions and RPC error layers
  that compare the rendered string or destructure the attribute
  round-trip cleanly.

- **`JsonlSessionRepo.open` `cwd_override` seam encapsulates the
  private-attribute mutation.** The `storage._metadata` writeback
  for the import-time cwd override now lives on a single owner
  (`JsonlSessionRepo.open`) rather than scattering across
  `import_from_jsonl`.

- **`JsonlSessionRepo.fork_from` optional `session_dir` mirrors Pi
  3rd parameter.** Default behavior unchanged (existing callers
  see no diff); new callers can pin a directory directly.

## References

- `packages/agent/src/core/agent-session-runtime.ts:39-47` (Pi
  `SessionImportFileNotFoundError` — P-366 verbatim port target)
- `packages/agent/src/core/agent-session-runtime.ts:115-198`
  (Pi runtime emit / teardown / finish_session_replacement / replace
  APIs — `with_session` / `setup` callback positions)
- `packages/agent/src/core/agent-session-runtime.ts:172-173`
  (Pi `withSession` invocation point — P-358 source)
- `packages/agent/src/core/agent-session-runtime.ts:226-229`
  (Pi `setup` callback position — P-359 source)
- `packages/agent/src/core/agent-session-runtime.ts:289-291`
  (Pi `fork` `withSession` plumbing — P-358 source)
- `packages/agent/src/core/agent-session-runtime.ts:316-318`
  (Pi `switch_session` `withSession` plumbing — P-358 source)
- `packages/agent/src/core/agent-session-runtime.ts:329-364`
  (Pi `importFromJsonl` body — P-360 source)
- `packages/agent/src/core/agent-session-runtime.ts:366-373`
  (Pi `dispose` — P-363 source for `runner.invalidate` ordering)
- `packages/agent/src/core/agent-session.ts:3087-3095`
  (Pi `createReplacedSessionContext` factory — P-357 source)
- `packages/agent/src/harness/extensions/types.ts:333-364` (Pi
  `ExtensionCommandContext` 6 command methods — P-364 source)
- `packages/agent/src/harness/extensions/types.ts:366-381` (Pi
  `ReplacedSessionContext` — P-356 source)
- `packages/agent/src/harness/extensions/types.ts:371` (Pi
  `ReplacedSessionContext extends ExtensionCommandContext` — P-364
  W5 MAJOR source)
- `packages/agent/src/harness/session/session-manager.ts:1353-1394`
  (Pi `forkFrom` — P-361 source; P-368 W5 MINOR `sessionDir?` source)
- `packages/agent/src/harness/extensions/runner.ts:466-473` (Pi
  `ExtensionRunner.invalidate` — P-362 source)
- `packages/agent/src/harness/extensions/runner.ts:467` (Pi
  staleness verbatim string — :data:`PI_STALENESS_MESSAGE` source)
- `packages/agent/src/harness/extensions/runner.ts:475-478` (Pi
  `ExtensionRunner.assertActive` — P-365 W5 MAJOR source)
- `aelix-agent-core/src/aelix_agent_core/runtime/_types.py`
  (AMEND — `ReplacedSessionContext` Protocol with 19 members + 6
  `ExtensionCommandContext` extension methods + Pi-verbatim
  :class:`SessionImportFileNotFoundError` + :data:`PI_STALENESS_MESSAGE`)
- `aelix-agent-core/src/aelix_agent_core/runtime/__init__.py`
  (AMEND — re-export :data:`PI_STALENESS_MESSAGE` +
  :class:`ReplacedSessionContext` + :class:`SessionImportFileNotFoundError`)
- `aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py`
  (AMEND — `with_session` / `setup` plumbing on 3 replace APIs +
  `_finish_session_replacement` + real `import_from_jsonl` body +
  `runner.invalidate` calls in `_teardown_current` + `dispose` +
  `repo.open(cwd_override=...)` seam usage)
- `aelix-agent-core/src/aelix_agent_core/harness/core.py`
  (AMEND — `create_replaced_session_context` factory with optional
  `runtime` kwarg wiring 6 ExtensionCommandContext methods +
  `ExtensionRunner` invalidate bridge wired in `__init__`)
- `aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py`
  (AMEND — drop `frozen=True` + add `_invalidate_runtime` bridge +
  `invalidate` delegates + `assert_active` no-op SYNTHESIS — W6
  audit closure)
- `aelix-agent-core/src/aelix_agent_core/session/jsonl_repo.py`
  (AMEND — `open(cwd_override=...)` seam + `fork_from(session_dir=...)`
  optional 3rd parameter + new `fork_from` cross-cwd import body)
- `aelix-agent-core/src/aelix_agent_core/session/fs.py`
  (AMEND — :meth:`FileSystem.copy_file` Protocol method + LocalFileSystem
  impl backed by :func:`shutil.copy2`)
- `aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`
  (AMEND — :meth:`_ExtensionRuntime.invalidate` default-msg aligned
  to :data:`PI_STALENESS_MESSAGE`)
- `tests/runtime/test_replaced_session_context.py` (NEW — Protocol
  conformance + factory `SimpleNamespace` baseline + send_message /
  send_user_message routing + 6 ExtensionCommandContext method
  exposure + unbound-runtime raise + reload stub)
- `tests/runtime/test_with_session_callback.py` (NEW — 3 replace
  APIs accept `with_session` + callback fires AFTER rebind on NEW
  harness + raises propagate)
- `tests/runtime/test_setup_callback_new_session.py` (NEW — `setup`
  invoked with session_manager + ordering before rebind + message
  rebuild reflects setup appends)
- `tests/runtime/test_import_from_jsonl_real.py` (NEW — missing path
  raises + Pi-verbatim message + same-dir skips copy + cwd override
  rewrites metadata + cancel short-circuits + different-dir copies +
  Pi message + file_path attr per P-366)
- `tests/session/test_jsonl_fork_from.py` (NEW — ALL entries copy +
  target cwd matches + parent_session_path + new id/path + round-trip
  through `repo.open` + optional `session_dir` override + default
  resolution preserved per P-368)
- `tests/harness/test_extension_runner_invalidate.py` (NEW — bridge
  propagation + default = :data:`PI_STALENESS_MESSAGE` + idempotent +
  no-bridge no-op + harness wires bridge + runtime default aligned +
  teardown calls invalidate + dispose calls invalidate +
  `assert_active` no-op SYNTHESIS)
- `tests/extensions/test_extension_runner_emit_delegate.py` (AMEND —
  `frozen=True` drop verified per P-362)
- `tests/runtime/test_agent_session_runtime.py` (AMEND —
  `import_from_jsonl` stub coverage test moved to
  `test_import_from_jsonl_real.py`)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₅b row this sprint).
- ADR-0077 — :class:`AgentSessionRuntime` Pi port + `rebindSession`
  seam (Sprint 6h₄b foundation — this ADR fills the remaining
  callback carry-forward).
- ADR-0079 — Sprint 6h₄c wiring decisions (Sprint 6h₅b sits on top
  of the wired runtime replace APIs).
- ADR-0080 — Sprint 6h₄c Phase 4.13 strict-superset closure +
  Sprint 6h₅+ carry-forward roster (P-314 / P-315 / P-310
  `import_from_jsonl` CLOSE per this ADR).
- ADR-0081 — Sprint 6h₅a extension event Pi parity decisions
  (Sprint 6h₅b sibling — fills the runtime callback gap on top of
  the extension event lifecycle wiring).
- ADR-0082 — Sprint 6h₅a Phase 4.14 strict-superset closure +
  Sprint 6h₅b / 6h₅c carry-forward roster (this ADR closes the 6h₅b
  subset of that roster).
- ADR-0084 — Sprint 6h₅b Phase 4.15 strict-superset closure
  (sibling ADR — locks the closure pin invariants and enumerates
  remaining 6h₅c carry-forward).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₅b / Phase 4.15 / W6 (shipped — runtime callback Pi parity +
P-364 ~ P-368 W6 audit closure).
