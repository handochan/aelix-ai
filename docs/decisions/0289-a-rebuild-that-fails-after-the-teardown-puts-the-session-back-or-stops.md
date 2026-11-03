# 0289. A rebuild that fails after the teardown puts the session back on its previous settings, or stops

Status: Accepted (2026-11-03; owner decision (b) settled 2026-11-03)
Date: 2026-11-03
Issue: #663 (a #25 follow-up). One lane, one commit.
Amends: ADR-0284 D6 (the failure line — dated note) and Limits (the torn-down
harness — dated note); ADR-0177 (a failed `/reload` no longer leaves the
disposed harness installed — dated note).
Relates: ADR-0093 (P-380, `AgentHarness.reload`, untouched: it tears nothing
down), ADR-0081 (`_teardown_current`'s emit → invalidate → dispose order, kept),
ADR-0244 (#137, the writer lock a swap moves — unchanged by a restore),
ADR-0250 / #367 (`hold_turns`, the turn gate this reuses), #594 (an RPC
transition is not cancellable), #658 (the two measured triggers; this does not
depend on it), #664 (an extension's `SystemExit` is a load error).
pi: `earendil-works/pi` `origin/main` `90b42bca6`,
`packages/coding-agent/src/core/agent-session.ts:3741-3780` `reload` — the old
runner is invalidated, not disposed, so a `settingsManager.reload()` or
`_resourceLoader.reload()` that throws leaves its handlers in place; pi has no
built-in approval gate to lose. `core/settings-manager.ts:500-526` assigns as it
goes, as aelix did. aelix keeps pi's order and diverges on the failure path
(ADR-0235).
Tests: `tests/runtime/test_failed_rebuild_663.py`,
`tests/settings_manager/test_reload_transaction_663.py`,
`tests/rpc/test_failed_reload_gate_663.py`,
`tests/tui/test_failed_reload_tui_663.py`,
`tests/cli/test_failed_reload_factory_663.py`.

## Context

`AgentSessionRuntime.reload()` follows pi's order: tear the old harness down
(`session_shutdown(reload)`, invalidate, `harness.dispose()`), re-read the
settings, build the new harness. `harness.dispose()` ends in `HookBus.dispose()`,
which clears every handler — Guardrail's and Permission's `tool_call` among
them. When the re-read or the build raised, the new harness was never installed
and `runtime.harness` stayed the disposed one.

Measured on `main` `3a7ffded` (`.omc/specs/25-followups-evidence/i2a/`, scratch
HOME, a scripted model asking for `echo AFTER > after.txt` and
`rm -rf ./no-such-dir-25fu && echo GUARD > guard.txt`, permission mode
`default`): after a failed `/reload` the TUI printed "✖ /reload failed during …
— nothing applied; /reload again or restart" and the model's bash ran with no
approval dialog and no guardrail; `after.txt` and `guard.txt` were written.
RPC `resources/reload` answered an error and did the same. Before #25 the TUI
died on the exception instead (its catch is ADR-0284 D6); RPC had always kept
going. `/new` after a settings-stage failure failed too, because the manager
kept the half-read settings in memory. The triggers were #658's two bugs (a
deeply nested `settings.json`, an unreadable folder under a `skills` path); any
other exception in either step makes the same state.

`/new`, `/resume`, `/fork`, `/clone` and `/import` (and RPC `new_session`,
`fork`, `switch_session`) go through `_finish_session_replacement`, which has
the same teardown-then-build shape.

## Decision

### D1. The order stays; the failure path builds the session again

The teardown still comes first (pi's order; `session_shutdown` handlers see the
old settings). When the step between the teardown and the install raises —
`settings_manager.reload()` or the factory — the runtime:

1. puts the settings in memory back as they were before the re-read (D2);
2. builds a harness again on the session it was on, through the same factory,
   seeded as a reload (`ReloadSeed` with the flag values and model it carried)
   and marked `restore=True` (D3) — a factory that never declared
   `reload_seed` is called without it, as `HarnessFactory` promises;
3. settles it as a successful `/reload` settles its own — the active-tool round
   trip, the flag round trip, the rebind, `session_start(reload)`, the
   after-start callback, `reload_resources()` — and rebuilds its transcript and
   thinking level from the session and the harness it replaced;
4. raises `RebuildFailedError(state="kept")` carrying the cause.

A swap that fails goes back to the session it was leaving, never the target:
the target's writer lock is released by the caller as before, and the lock this
terminal holds on its own session is still installed.

### D2. "The previous settings" are the in-memory snapshot

`AgentSessionRuntime` takes `SettingsManager.snapshot()` before the teardown
and `restore()`s it on failure. The snapshot holds references (`reload` and
`set_project_trusted` replace those objects rather than change them), the
modification sets, the load errors and project trust; nothing is written, so
the files may still hold what made the rebuild fail and the next `/reload`
reads them again.

`SettingsManager.reload()` is all or nothing: both loads and the merge are
computed before anything is assigned, so a merge that raises (#658's
`copy.deepcopy` on a deeply nested value) leaves every field as it was. This
holds whatever #658 does about the depth.

### D3. The restore build reuses what the session ran with

The CLI factory rescans the skill and prompt directories on a reload (ADR-0279
D10). A build that raises now leaves those catalog holders as the running
session has them (`_harness_factory`), and a `restore` build does not rescan,
so it does not trip over what the failed build tripped over (an unreadable
folder under a new `skills` path). Extensions are discovered again as on any
build; the loader isolates a failing one (AGENTS.md §1), so they cannot fail
the build.

### D4. When that build fails too, aelix stops (fail closed)

`RebuildFailedError(state="stopped", restore_error=…)`. The disposed harness
stays installed and refuses everything that would run without its gates:

- it holds its turns (`hold_turns(STOPPED_MESSAGE)`, #367's gate): `prompt`,
  `compact`, a summarising `navigate_tree` and an extension's `trigger_turn` are
  refused before anything is sent or written — every surface, RPC and `-p`
  included;
- `AgentHarness.dispose()` now marks it `disposed`, and a disposed harness
  blocks every tool call (`_before_tool_call_bridge`) instead of running it with
  an empty bus — below the turn gate, for any turn that reaches a stale
  reference;
- the TUI refuses a prompt, a `!` and a `!!` line with the same sentence; its
  `/`-commands keep working.

`AgentSessionRuntime.stopped` is the sentence while this lasts; the next build
that succeeds — a `/reload` once the cause is fixed, or a `/new` — clears it.
A cancel or a `KeyboardInterrupt` between the teardown and the install stops
the same way, without trying the restore.

`STOPPED_MESSAGE`: "aelix is stopped until /reload succeeds. Fix the cause,
then /reload, or restart." A `/reload` that ends a stop says "✓ /reload
succeeded — aelix is running again".

### D5. Every path that tears down

The state machine lives in the runtime, so every caller gets it: the TUI
`/reload`, RPC `resources/reload`, an extension's `ctx.reload()`, the TUI
`/new`, `/resume`, `/fork`, `/clone`, `/import` and RPC `new_session`, `fork`,
`switch_session`. The kill-switch `/reload` (`AELIX_RELOAD_REBUILD=0`) tears
nothing down; a `reload_resources()` that raises there is now said instead of
ending the input loop.

### D6. What the surfaces say

The line states what runs now and never says "nothing applied" after a
teardown:

```
✖ /reload failed during the rebuild: PermissionError: … — kept the previous settings; fix the cause, then /reload again
✖ /reload failed during re-reading the settings: RecursionError: …; rebuilding with the previous settings failed too (…) — aelix is stopped until /reload succeeds. Fix the cause, then /reload, or restart.
⚠ /reload applied the new extensions, but re-discovering resources failed: …
```

`RebuildFailedError`'s own message is the part after the step ("RuntimeError:
boom — kept the previous settings", "… — stayed on this session and kept the
previous settings" for a swap), so RPC's error, an extension command's failure
line and the TUI's "✖ new session failed: …" say it without changes of their
own. The `/extension` banner repeats the line until a build succeeds.

## Consequences

- After a failed rebuild every tool call goes through Guardrail, Permission and
  every extension's hooks again, or nothing runs at all.
- A `/reload` that fails no longer leaves the in-memory settings half-read, so
  a `/new` after it works.
- RPC clients see the state in `resources/reload`'s error text; a prompt while
  stopped answers `success: false` with `STOPPED_MESSAGE`.

## Limits

- The restore re-imports extensions from disk: an extension file edited since
  the session started runs in its edited form, and the line that follows the
  failure names what that build changed.
- A swap's restore is reload-shaped: extensions saw `session_shutdown(new)` (or
  `resume`, `fork`) and then `session_start(reload)` on the session they were
  already on.
- While stopped, RPC's `bash` command (the client's own command, which the
  built-ins never gate) still runs; the TUI refuses `!`. Extension commands
  fail on the disposed runtime ("has been disposed").
- The restore can only fail closed when the factory itself cannot build; a
  restore build whose post-install steps raise (rebind, `session_start`) is
  logged and reported as kept — its gates are installed.
