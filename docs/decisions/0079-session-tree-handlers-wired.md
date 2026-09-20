# 0079. Sprint 6h₄c Phase 4.13 — Session-tree handlers wired (`switch_session` / `fork` / `clone`) + runtime body fills

Status: Accepted (Sprint 6h₄c / Phase 4.13 / W6 shipped)
Date: 2026-05-21
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₄b (ADR-0077 / ADR-0078) landed the `AgentSessionRuntime`
foundation + `rebindSession` seam but left the 4 public replace APIs
(`switch_session` / `new_session` / `fork` / `import_from_jsonl`) as
`NotImplementedError` stubs and the 3 session-tree RPC handlers
(`switch_session` / `fork` / `clone`) DEFERRED. Sprint 6h₄c is the
**wiring sprint** — it fills 3 of the 4 stub bodies on top of the
6h₄b foundation, wires the 3 RPC handlers, and CLOSES the Phase 4
RPC roster at **29 supported / 0 deferred / 29 total** = full Pi
parity for the `RpcCommand` discriminator union.

The 4th stub (`import_from_jsonl`) STAYS STUBBED — no Pi `RpcCommand`
discriminator maps to it at SHA `734e08e` (Pi reality: the `/import`
TUI command does not go through the RPC dispatch table). Real body
defers to Sprint 6h₅+ per ADR-0080 carry-forward.

## Decision

### Pi parity decisions (P-323 ~ P-331)

- **P-323** — Pi line-drift discovery (mirror of Sprint 6h₄a P-293).
  ADR-0076 carry-forward roster estimated the 3 session-tree case
  sites at `rpc-mode.ts:528-557`. ADR-0078 inherited the drift and
  cited `:566` / `:574` / `:586`. W0 verification at SHA `734e08e`
  puts the actual sites at `:563-569` (`switch_session`) / `:571-577`
  (`fork`) / `:579-589` (`clone`). Forward-fix: ADR-0076 amend and
  ADR-0078 amend record the supersession; new closure pin locks the
  verified ranges.

- **P-324 (BINDING)** — `AgentSessionRuntime.__init__` constructor
  extended with required keyword-only `repo: JsonlSessionRepo` and
  `fs: FileSystem`. Both consumed by the 4 replace bodies. Rejecting
  the `Optional` shape was deliberate: an `Optional` repo would
  silently re-raise the existing `NotImplementedError` from the
  replace bodies and mask the wiring gap. Compile-time enforcement
  forces every callsite (passthrough constructor, 6h₄b unit tests,
  closure pin) to thread the repo + fs explicitly. The constructor
  signature:

  ```python
  def __init__(
      self,
      harness: AgentHarness,
      create_harness: HarnessFactory,
      *,
      repo: JsonlSessionRepo,                  # NEW (P-324)
      fs: FileSystem,                          # NEW (P-324)
      diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
      model_fallback_message: str | None = None,
  ) -> None:
  ```

- **P-325** — Runtime replace body fills routed through
  `JsonlSessionRepo`. `switch_session` resolves via
  `repo.open(load_jsonl_session_metadata(fs, path))` →
  `_finish_session_replacement(new_session)`. `new_session` calls
  `repo.create(JsonlSessionCreateOptions(cwd, parent_session_path))`.
  `fork` is **persisted-only** — the Pi in-memory branch
  (`agent-session-runtime.ts:303-319`) is dropped because Aelix
  funnels all replacement through `JsonlSessionRepo.fork`. The
  3-Pi-branch logic collapses into a single `repo.fork(source,
  ForkOptions(cwd, entry_id=target_leaf_id, position="at",
  parent_session_path))` call — `JsonlSessionRepo.fork` itself
  internally handles the "no targetLeafId" case via
  `get_entries_to_fork` (`repo_utils.py:58`).

- **P-326** — NEW `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class for
  handlers operating on `AgentSessionRuntime` instead of
  `AgentHarness`. `_bind_runtime_host(handler, runtime_host)`
  adapter closes over the runtime so the dispatch table stays
  uniform (2-arg `(harness, cmd)` shape). `build_dispatch_table`
  accepts a NEW required keyword-only `runtime_host` parameter; when
  callers pass `None`, the 4 runtime-host handlers route through a
  `_make_missing_runtime_handler` Pi-shape error stub (preserves
  test paths that don't construct a runtime).

- **P-327** — Wire shape `selectedText → text` rename + key-omission
  for `fork`. Pi `rpc-mode.ts:576` returns `success(id, "fork",
  {text: result.selectedText, cancelled: result.cancelled})` — the
  Pi wire key is `text`, NOT `selectedText`. Aelix mirrors with
  key-omission per Sprint 6h₄a P-298: when `result.selected_text is
  None` the `text` key is omitted entirely (Pi
  `JSON.stringify({text: undefined})` → `{cancelled: false}`). The
  pattern is identical to `_handle_get_last_assistant_text`.

- **P-328** — `clone` leaf_id capture BEFORE `runtime_host.fork()`
  enters its replace waveform. Pi line 580 captures
  `session.sessionManager.getLeafId()` BEFORE the `:584`
  `runtimeHost.fork(leafId, {position: "at"})` call because the
  fork waveform disposes the old harness, invalidating the session.
  Aelix mirrors verbatim: `leaf_id = await session.get_leaf_id()`
  resolves BEFORE `runtime_host.fork(leaf_id, position="at")`. The
  closure pin asserts the ordering by spying on `get_leaf_id` +
  `harness.dispose` and checking `order[:2] == ["get_leaf_id",
  "dispose"]`. Aelix-divergence acknowledged: Pi
  `Session.get_leaf_id()` is sync; Aelix `Session.get_leaf_id()` is
  `async def` — the `await` is necessary but the pre-capture
  ordering is preserved.

- **P-329 (deliberate convergence)** — Aelix handlers DO NOT call
  rebind manually. Pi `rpc-mode.ts:565-567` / `:573-575` / `:585-587`
  contains `if (!result.cancelled) { await rebindSession(); }`,
  which is Pi belt-and-braces (Pi's runtime
  `finishSessionReplacement` ALSO awaits the rebind). Aelix's 6h₄b
  `_finish_session_replacement` auto-invokes the registered
  `rebind_session` callback as the third step of the 3-step
  waveform. Adding a second handler-side rebind would invoke the
  closure twice per replace, breaking the closure-pin invariant
  `test_apply_for_test_invokes_rebind_session_callback_exactly_once`.
  Aelix-handler docstrings + module docstrings + the closure pin
  all enforce the single-source-of-truth contract.

- **P-330** — `_handle_new_session` Sprint 6d stub REPLACED via
  `runtime_host.new_session(parent_session=cmd.parent_session)`.
  The old body at `rpc_mode.py:309-347` rejected `parent_session`
  with an `RpcErrorResponse("parent_session lineage tracking
  deferred to Sprint 6f (ADR-0058)")`; the new body persists
  lineage through `repo.create(parent_session_path=...)`. The
  ADR-0058 carry-forward CLOSES. Handler arity MOVED from
  `_SUPPORTED_HANDLERS_HARNESS_ONLY` to
  `_SUPPORTED_HANDLERS_RUNTIME_HOST` (4 entries: `new_session` +
  `switch_session` + `fork` + `clone`).

- **P-331** — Test seam `_apply_for_test` REMOVED from
  `AgentSessionRuntime`. The 6h₄b unit tests that drove the rebind
  seam through `_apply_for_test` migrate to drive `switch_session`
  via the real public API. Closure pin asserts `not hasattr(
  AgentSessionRuntime, "_apply_for_test")` so any re-add trips
  mechanically.

### W5 documented divergences (P-324-DRIFT / P-326-DRIFT — ratified)

- **P-324-DRIFT (RATIFIED)** —
  `_make_passthrough_runtime(harness, runtime_host=None, *,
  repo=None, fs=None)` uses Pi defaults (`LocalFileSystem()` +
  `JsonlSessionRepo(fs=...)`) when `repo` / `fs` are not threaded
  in by the caller. The architectural enforcement boundary
  (`AgentSessionRuntime.__init__`) is strict — repo / fs ARE
  required there — but the construction-site convenience shim
  supplies the Pi defaults so `run_rpc_mode(harness)` continues to
  work without any keyword overrides. Spec §C.7 ratified to match
  impl.

- **P-326-DRIFT (RATIFIED)** — `build_dispatch_table(model_registry,
  runtime_host=None)` accepts `runtime_host` as Optional, with
  `_make_missing_runtime_handler` fallback for test fixtures that
  don't construct a runtime. The production callsite
  (`run_rpc_mode`) always supplies non-None runtime_host. Spec §C.6
  and §G ratified to match impl.

### W4 MINOR cleanups applied this sprint

- **W4 MINOR-1** — Collapsed double-catch (`except ValueError ...
  except Exception`) in `_handle_fork` + `_handle_clone`. Kept the
  Pi-documented `ValueError` arm ("Invalid entry ID for forking" at
  Pi `:247`); dropped the redundant blanket `except Exception`
  since `_handle_command` already wraps non-ValueError exceptions
  into the same Pi-shape envelope.
- **W4 MINOR-2** — Dropped redundant blanket `except Exception` in
  `_handle_switch_session`. Handler is now pure — `SessionError`
  from `repo.open` propagates to the outer `_handle_command`
  wrapper which produces an identical wire envelope.
- **W4 MINOR-3** — Rewrote `test_handle_fork_wire_shape_omits_text_
  when_none` (both in `tests/rpc/` and the closure pin) to drive
  `_handle_fork` through a mocked `AgentSessionRuntime` returning
  `RuntimeReplaceResult(cancelled=False, selected_text=None)` so
  the handler-layer key-omission invariant is asserted end-to-end.
- **W4 MINOR-4** — Dropped the bare `except RuntimeError` catch in
  `_handle_new_session`. The outer `_handle_command` already wraps
  any error into a Pi-shape envelope; the inner catch was masking
  noop-factory leaks (`Passthrough runtime cannot replace harness`)
  which should fail loud.

### W5 INFO acknowledgments

Every P-323-VERIFY ~ P-331-VERIFY INFO finding from W5 matches Pi
byte-for-byte:

- P-323-VERIFY — line citations verified at SHA `734e08e`.
- P-324-VERIFY — required keyword-only `repo` / `fs` enforced.
- P-325-VERIFY — `JsonlSessionRepo.open/create/fork` route confirmed.
- P-326-VERIFY — `_bind_runtime_host` adapter type confirmed.
- P-327-VERIFY — `text` key wire emission confirmed.
- P-328-VERIFY — leaf_id pre-capture ordering confirmed.
- P-329-VERIFY — single rebind invocation per replace confirmed.
- P-330-VERIFY — Sprint 6d rejection branch deleted.
- P-331-VERIFY — `_apply_for_test` removed from public surface.

## Roster

**W0 (binding spec, P-323..P-331):**

- P-323 — Pi line-drift forward-fix.
- P-324 — Runtime constructor `repo` + `fs` extension (BINDING).
- P-325 — Runtime body fills via `JsonlSessionRepo`.
- P-326 — `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class.
- P-327 — `selectedText → text` wire rename + key-omission.
- P-328 — `clone` leaf_id pre-capture ordering.
- P-329 — Aelix handlers MUST NOT call rebind manually
  (deliberate convergence).
- P-330 — `_handle_new_session` Sprint 6d stub replaced.
- P-331 — `_apply_for_test` test seam removed.

**W4 MINOR cleanups (W6 applied):**

- W4 MINOR-1 — Double-catch collapse in `_handle_fork` +
  `_handle_clone`.
- W4 MINOR-2 — Blanket Exception catch dropped from
  `_handle_switch_session`.
- W4 MINOR-3 — `test_handle_fork_wire_shape_omits_text_when_none`
  rewritten to mock the runtime.
- W4 MINOR-4 — `_handle_new_session` blanket RuntimeError catch
  dropped.

**W5 documented divergences (RATIFIED):**

- P-324-DRIFT — Passthrough constructor accepts optional
  `repo` / `fs`, defaulting to Pi shape.
- P-326-DRIFT — `build_dispatch_table` accepts optional
  `runtime_host` with missing-runtime stub fallback.

## Sprint 6h₅+ carry-forward (per ADR-0080)

- P-307 — `session_shutdown` extension event emit from
  `AgentHarness.dispose()` (runtime → extension API).
- P-308 — Real `session_before_switch` / `session_before_fork`
  extension cancel hooks (currently no-op `False`).
- P-314 — `with_session: Callable[[ReplacedSessionContext],
  Awaitable[None]] | None` 2-stage callback for
  `finishSessionReplacement`.
- P-315 — `set_rebind_session` / `set_before_session_invalidate`
  optional-cb signature widening (Pi accepts `undefined` to clear).
- `assertSessionCwdExists` Pi parity (cwd-on-disk validation).
- `previousSessionFile` / `sessionStartEvent` tracking (extension
  event payload).
- Pi `forkFrom` cross-cwd import (no RPC wire today).
- Pi `setup` callback for `new_session`.
- `import_from_jsonl` real runtime body (no RPC wire today — Pi
  TUI `/import` doesn't go through dispatch).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₄b (start of 6h₄c) | 26 | 3 | 29 |
| Sprint 6h₄c (this ADR) | **29** | **0** | **29** |

**PHASE 4 RPC ROSTER CLOSED** — full Pi parity for `RpcCommand`.

## Consequences

- **Full Pi parity for the RPC discriminator union** —
  `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` at SHA `734e08e`. Any
  Pi RPC client can dispatch any of the 29 commands against the
  Aelix coding-agent and get a Pi-shape wire response (or a
  Pi-shape error envelope for documented error paths).

- **3 new RPC handlers** — `_handle_switch_session` /
  `_handle_fork` / `_handle_clone` route through the
  `AgentSessionRuntime` which owns the LIVE harness reference and
  the rebind callback. The session-tree write surface is now
  observable to RPC clients.

- **Sprint 6d's `parent_session` rejection branch deleted** —
  `new_session` finally persists lineage to the parent session
  through `repo.create(parent_session_path=...)`. The ADR-0058
  carry-forward CLOSES.

- **4 runtime body stubs filled** (3 of 4 — `import_from_jsonl`
  stays stubbed per ADR-0080). `JsonlSessionRepo` is the single
  storage path for all session replacement; Aelix-additive
  simplification vs. Pi's 3-branch fork (top + persisted +
  in-memory) — Aelix is persisted-only.

- **Test seam `_apply_for_test` removed** — 6h₄b unit tests
  migrated to the real `switch_session` public API. The closure
  pin asserts the seam's permanent absence.

- **The forward-compat clause holds** — any PR introducing a new
  Pi RPC discriminator MUST drop it from `RPC_COMMAND_TYPES` OR
  add it to `SUPPORTED_COMMANDS` in the same PR (enforced by
  closure pins across Phases 4.4 through 4.13).

- **Carry-forward roster is non-RPC** — every remaining Pi-parity
  gap is in runtime/extension polish (P-307/P-308/P-314/P-315 +
  cwd validation + cross-cwd import + session-start event +
  TUI `/import` body), not the RPC dispatch table. Sprint 6h₅+
  picks them up incrementally.

## References

- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:563-569`
  (Pi `switch_session` handler — verified at SHA `734e08e`)
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:571-577`
  (Pi `fork` handler — verified at SHA `734e08e`)
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:579-589`
  (Pi `clone` handler — verified at SHA `734e08e`)
- `packages/coding-agent/src/modes/rpc/rpc-mode.ts:277-282`
  (Pi `new_session` handler — verified at SHA `734e08e`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:175-198`
  (Pi `switchSession`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:200-232`
  (Pi `newSession`)
- `packages/coding-agent/src/core/agent-session-runtime.ts:234-320`
  (Pi `fork` — 3-branch waveform)
- `packages/coding-agent/src/core/agent-session-runtime.ts:49-58`
  (Pi `extractUserMessageText`)
- `aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py`
  (AMEND — constructor `repo` + `fs` + 4 replace body fills +
  `_extract_user_message_text` helper + `_apply_for_test` removed)
- `aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py`
  (AMEND — 3 new handlers + `_handle_new_session` rewritten +
  `_SUPPORTED_HANDLERS_RUNTIME_HOST` + `_bind_runtime_host` +
  `_make_missing_runtime_handler` + `build_dispatch_table`
  `runtime_host` param + `run_rpc_mode` `repo` / `fs` params +
  DEFERRED 3 → 0 + W4 MINOR cleanups)
- `tests/runtime/test_agent_session_runtime_replace_apis.py`
  (NEW — real `switch_session` / `new_session` / `fork` over
  tmp-path `JsonlSessionRepo`)
- `tests/runtime/test_switch_session.py` (NEW)
- `tests/runtime/test_fork.py` (NEW)
- `tests/runtime/test_new_session_real.py` (NEW)
- `tests/rpc/test_rpc_mode_switch_fork_clone.py`
  (NEW — 3 handler integration tests + arity / dispatch wiring +
  rebind invocation count + leaf_id pre-capture ordering)
- `tests/rpc/test_rpc_mode_new_session_parent.py`
  (NEW — Sprint 6d stub removal regression)
- `tests/pi_parity/test_phase_4_13_strict_superset.py`
  (NEW closure pin — 29 / 0 / 29 + handler invariants + W4 MINOR
  rewrites + P-329 rebind exactly-once)
- `tests/pi_parity/fixtures/pi_runtime_wire_734e08e.json`
  (NEW W0 fixture)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₄c row this sprint).
- ADR-0077 — `AgentSessionRuntime` Pi port + `rebindSession` seam
  (FOUNDATION-ONLY; this ADR fills the bodies).
- ADR-0078 — Sprint 6h₄b Phase 4.12 closure pin + Sprint 6h₄c
  wiring carry-forward (amended this sprint with foundation →
  wiring complete note + P-323 line-citation correction).
- ADR-0076 — Sprint 6h₄a Phase 4.11 closure (amended this sprint
  with PHASE 4 RPC CLOSURE note + P-323 line-citation correction).
- ADR-0080 — Sprint 6h₄c Phase 4.13 strict-superset closure +
  Sprint 6h₅+ carry-forward roster.
- ADR-0058 — Sprint 6d RPC mode + DEFERRED_COMMANDS introduction
  (`parent_session` rejection branch CLOSES here).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₄c / Phase 4.13 / W6 (shipped — **PHASE 4 RPC CLOSURE**).

## Amendment (2026-09-20, #300) — "fork at the root" is not "fork everything"

P-325 above records the fork body as a single
`repo.fork(source, ForkOptions(cwd, entry_id=target_leaf_id, position="at",
parent_session_path))` call, and adds that `JsonlSessionRepo.fork` "internally
handles the 'no targetLeafId' case via `get_entries_to_fork`". It does handle
it — as a **whole-session copy**, which is the wrong answer for the way the
runtime produces that value. **The waveform stands. The overloaded `None` is
struck.**

`position="before"` sets `target_leaf_id = selected_entry.parent_id`: the entry
the forked session should resume at. The first entry in a file has no parent, so
that is `None` — and `None` on `ForkOptions.entry_id` already meant "copy the
entire source session" (`repo_utils.py`, ported from
`repo-utils.ts:32-50`). Two opposite intents met on one value and the copy won.

A session written by print mode (`-p`) begins `header → user → assistant`, so its
first user message *is* the first entry. `/fork` there — and the TUI's `/fork`
targets the most recent user message, which in a one-turn session is that
one — handed back the whole conversation instead of an empty branch. Measured on
`db796b2` through `AgentSessionRuntime.fork(first_id, position="before")`: 2
entries in the "empty" fork, expected 0.

What ships:

- `FORK_FROM_ROOT`, a module-level sentinel, is the empty branch:
  `get_entries_to_fork` returns `[]` for it, checked **before** the `None` case.
  `ForkOptions.entry_id` is now `ForkEntryId = str | _ForkFromRoot | None`.
- `fork_at_leaf(leaf_id)` maps a leaf pointer to a fork target — `None` (and
  `""`) to `FORK_FROM_ROOT`, anything else through unchanged. This is the
  conversion every "fork where I am" caller makes, and it is where the trap is
  documented, because a leaf is `None` for *any* empty branch: a fresh session,
  one rewound by `Session.move_to(None)`, or the parent of a file's first entry.
  The distinction matters most at open time, where a fork is taken at the
  current leaf and nothing has been typed yet.
- `entry_id=None` keeps meaning the whole source session. It is the right
  spelling for "inherit this conversation", and it is what a second terminal
  forking an owned session (#137) asks for — that request must not become an
  empty file by accident either.
- `ForkPosition` is untouched. A fork at the root is still `position="before"`
  from the user's side, so `session_before_fork`'s payload
  (`harness/hooks.py`, `Literal["before", "at"]`) keeps telling the truth; the
  ambiguity was in `entry_id`, and that is where the discriminator went.

Pi arrived at the same outcome by a different route. Its current tree drops
`repo-utils.ts` and splits the two meanings onto a separate axis —
`scope: "branch"` (with `entryId ?? tip`) versus `scope: "tree"` — in
`session/fork-policy.ts`, whose `selectBranchFork` sets
`destinationTip = parentId` for `position === "before"` and selects no entries
when the target is the root. Its conformance suite pins exactly the assertion
this amendment adds (`session/testing/conformance/session-repo.ts`, case
`before-root`: null tip, zero entries). So this moves toward Pi and the pin
(ADR-0034) is not advanced.

Tests: `tests/runtime/test_fork.py` —
`test_fork_before_the_first_user_message_is_empty` is the gate and was red on
`db796b2`; `test_fork_before_the_first_user_message_still_returns_its_text` was
green before and guards the rewrite (`selected_text` still refills the
composer). `tests/test_jsonl_repo_fork.py` —
`test_fork_from_root_yields_an_empty_branch`,
`test_fork_at_leaf_maps_an_empty_branch_to_the_root_sentinel`, and
`test_fork_at_the_leaf_of_a_session_rewound_to_the_root_is_empty` (the
open-time shape). The pre-existing `test_fork_full_copy_when_entry_id_none`
already pins the other half and was left alone.
