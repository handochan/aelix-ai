# 0289. A rebuild that fails after the teardown puts the session back on its previous settings, or stops

Status: Accepted (2026-11-03; owner decision (b) settled 2026-11-03)
Date: 2026-11-03
Issue: #663 (a #25 follow-up). One lane, one commit.
Amends: ADR-0284 D6 (the failure line — dated note) and Limits (the torn-down
harness — dated note); ADR-0177 (a failed `/reload` no longer leaves the
disposed harness installed — dated note); ADR-0287 (a stop holds on its `!`
paths — dated note).
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
`tests/runtime/test_rebuild_transitions_663.py`,
`tests/settings_manager/test_reload_transaction_663.py`,
`tests/rpc/test_failed_reload_gate_663.py`,
`tests/tui/test_failed_reload_tui_663.py`,
`tests/tui/test_stopped_shell_lane_663.py`,
`tests/cli/test_failed_reload_factory_663.py`.
Review round 1 (2026-11-03, Codex R01–R09 and verify V1–V8): D2, D3, D4 and D5
amended in place, D7 added; the measured inputs are in
`.omc/specs/663-impl-codex-review.md` and `.omc/specs/663-verify-report.md`.

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
3. settles it as a successful `/reload` settles its own — the flag round trip,
   the rebind, `session_start(reload)`, the after-start callback,
   `reload_resources()` — and rebuilds its transcript from the session. Three
   things are put back exactly rather than as a reload would pick them: the
   active-tool set and the tool policy in force with it (a session's own
   turning on of a tool `defaultTools` keeps off included; no union with the
   extension tools — Codex R05), the model (D3), and the thinking level,
   clamped to the model the build runs (verify V4);
4. raises `RebuildFailedError(state="kept")` carrying the cause — only once all
   of step 3 has succeeded. A raise in any of it, a cancel included, is D4's
   stop (Codex R04).

A swap that fails goes back to the session it was leaving, never the target:
the target's writer lock is released by the caller as before, and the lock this
terminal holds on its own session is still installed.

### D2. "The previous settings" are the in-memory snapshot

`AgentSessionRuntime` takes `SettingsManager.snapshot()` before the teardown
and `restore()`s it on failure. The snapshot holds copies of the two scopes (a
setter changes the global scope in place, so a reference followed every later
write — Codex R07, verify V5), the modification sets, the load errors, project
trust and the manager's write count. Nothing is written, so the files may still
hold what made the rebuild fail and the next `/reload` reads them again.

The order a restore keeps (Codex R08). The manager counts every write to a scope
in memory — a setter, an `edit_list`, a trust change — and remembers the last
count that wrote each field. `restore()`:

1. starts from the snapshot's two scopes;
2. applies again every setter write and list edit made after the snapshot, with
   the value it left in memory — a nested write (`retry.enabled`) its key alone.
   Each of those has completed: it is on disk, or queued for it;
3. keeps a trust decision made since, with the project scope it read;
4. rebuilds the merged view from the two scopes, and keeps a field marked
   modified when either side had it so.

What the failed `reload()` alone brought in is dropped. So a write made while
the rebuild ran — a `session_shutdown` handler's, or a `/settings` change in
another task (measured: memory said "before" while the file said "user-write")
— wins over the snapshot, and the re-read that the rebuild failed on loses.

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

The seed's `model` is the model the session was on, and a `restore` build runs
on it exactly (`_build_harness_options(keep_exactly=True)`), before any
`setup()` or `session_start`, as a restored route — so the late-provider hold
is not applied after it either. Before, only a removed model was kept and a
`set_model` choice came back on the launch `--model` (Codex R06).

### D4. When that build fails too, aelix stops (fail closed)

`RebuildFailedError(state="stopped", restore_error=…)` — when the restore's
build fails, and when its settlement or rebind does (D1 step 4). Every harness
that leaves behind is stopped (`AgentHarness.stop(STOPPED_MESSAGE)`): the
installed one — the torn-down harness, or the restored one — and the torn-down
one a surface that was never rebound still holds. A stopped harness refuses
everything that would run without its gates:

- it holds its turns (#367's gate): `prompt`, `compact` and a summarising
  `navigate_tree` are refused before anything is sent or written — every
  surface, RPC and `-p` included. An extension's `trigger_turn` runs as the
  gate's refused turn — no request; the message is recorded with its error
  answer — though an extension of a torn-down harness is stale by then and its
  `send_message` raises first (verify V8);
- it reads `disposed` (`AgentHarness.dispose()` sets it as it begins; `stop()`
  sets it too), and a disposed harness blocks every tool call
  (`_before_tool_call_bridge`) instead of running it with an empty bus, and
  refuses `prompt`, `steer` and `follow_up` (`AgentHarnessError
  ("invalid_state")`; a steer or follow-up was answered `success: true` over RPC
  and then lost — verify V1);
- RPC `bash`, the client's own command, is refused with the sentence (Codex R03;
  it ran before, as an exception this ADR named). `/agents run` and RPC
  `agents/run` say the sentence instead of "Delegation is unavailable" /
  "Subagents are disabled" (verify V7);
- the TUI refuses a prompt, a `!` and a `!!` line with the same sentence; its
  `/`-commands keep working. So does every `!` path ADR-0287 added (2026-11-03,
  the rebase onto #596): a `!` / `!!` Entered while a turn or a command holds
  the session is refused in its block (`ShellLane.run` reads `disposed`, before
  the `user_bash` emit the torn-down HookBus would have answered with nothing),
  an Alt+Enter `!` queued before the stop is refused in its block when its line
  is read, and a collapsed paste beginning with `!` is a refused prompt. A `!`
  record the lane holds is not written into a disposed harness: it waits for the
  build that replaces it — the `/reload` that ends the stop, which writes it —
  or is dropped with a session the user leaves. `AgentHarness.idle_write`
  refuses a disposed harness, before it waits and again once it holds the lock.

`AgentSessionRuntime.stopped` is the sentence while this lasts; the next build
that succeeds — a `/reload` once the cause is fixed, or a `/new` — clears it,
and the TUI says "✓ aelix is running again" whether that build was the new one
or the one on the previous settings (verify V6). A cancel or a
`KeyboardInterrupt` between the teardown and the install stops the same way,
without trying the restore, and so does an interruption — `CancelledError`,
`KeyboardInterrupt`, `SystemExit`, `GeneratorExit` — that escapes the teardown
once it has begun to take the harness apart (after the `session_shutdown` emit;
Codex R02, verify V3: the harness was disposed, not stopped, and the next
prompt reached the model).

`STOPPED_MESSAGE`: "aelix is stopped until /reload succeeds. Fix the cause,
then /reload, or restart." A `/reload` that ends a stop says "✓ /reload
succeeded — aelix is running again".

### D5. Every path that tears down

The state machine lives in the runtime, so every caller gets it: the TUI
`/reload`, RPC `resources/reload`, an extension's `ctx.reload()`, the TUI
`/new`, `/resume`, `/fork`, `/clone`, `/import`, RPC `new_session`, `fork`,
`clone`, `switch_session` and `sessions/branch`, and an extension's
`ctx.new_session()`, `ctx.fork()` and `ctx.switch_session()` (verify V8). The
`ctx` a `with_session` callback receives reloads through the runtime too: it
called the legacy `AgentHarness.reload()`, which rebuilt nothing and cleared
the process-wide provider registry (Codex R09). The kill-switch `/reload`
(`AELIX_RELOAD_REBUILD=0`) tears nothing down; a `reload_resources()` that
raises there is now said instead of ending the input loop.

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

### D7. One transition at a time (Codex R01, verify V2)

Two reloads that interleaved inside the factory left the first one's build
installed over the second's, which nobody disposed; measured, that orphan made
two model requests and ran a tool while `runtime.stopped` was set.

`reload`, `new_session`, `switch_session`, `fork` and `import_from_jsonl` each
run inside `AgentSessionRuntime._transition` — an `asyncio.Lock` held from their
first read of the live harness through the teardown, the install, the
writer-lock move, the settlement and the restore or the stop. A second one
waits for the first to end, so the harness a transition replaces is always the
one it tore down itself.

One started from inside a running one is refused, not queued: a
`RuntimeError(NESTED_TRANSITION_MESSAGE)`. "Inside" is the code the transition
runs — its `session_before_switch`, `session_shutdown`, `session_start` and
`resources_discover` handlers, an extension's `setup()` in the factory, a
surface's rebind and after-start callbacks — and the tasks those start while it
runs (a context variable carries the transition's marker). Waiting there would
wait on its own caller. A `with_session` callback is not inside: it runs once
the lock is released, so its `ctx.reload()` or `ctx.new_session()` is a
transition of its own. `dispose()` at quit is not a transition.

The lock order is `_transition`, then the harness's drain lock, never the
reverse (2026-11-03, the rebase onto #596). A `!` record written under
`AgentHarness.idle_write` (ADR-0287) holds the drain lock; a transition's
teardown — and `dispose()` at quit — waits for it in `dispose()`'s drain, so a
record that began before the teardown lands first. Nothing under
`idle_write` waits for a transition, so neither waits for the other in a
cycle; a settle that runs while a transition is between its teardown and its
install finds the harness disposed and holds the record (D4).

RPC's classic `new_session`, `switch_session`, `fork` and `clone` now count as a
lifecycle transition for the control plane while they run (`classic_gate`): a
`prompt` sent meanwhile is refused ("… unavailable while a session lifecycle
transition runs") instead of running on the harness being torn down. Below the
surfaces, a disposed harness refuses `prompt` itself (D4).

## Consequences

- After a failed rebuild every tool call goes through Guardrail, Permission and
  every extension's hooks again, or nothing runs at all.
- A `/reload` that fails no longer leaves the in-memory settings half-read, so
  a `/new` after it works.
- RPC clients see the state in `resources/reload`'s error text; a prompt, a
  `bash`, a steer or a follow-up while stopped answers `success: false` with
  `STOPPED_MESSAGE`.
- "Kept" means the session as it ran: its model, tools, tool policy, thinking
  level and settings (with any write made meanwhile).
- A reload or session change asked for while one runs waits for it; one asked
  for from inside it fails with `NESTED_TRANSITION_MESSAGE`.

## Limits

- The restore re-imports extensions from disk: an extension file edited since
  the session started runs in its edited form, and the line that follows the
  failure names what that build changed.
- A swap's restore is reload-shaped: extensions saw `session_shutdown(new)` (or
  `resume`, `fork`) and then `session_start(reload)` on the session they were
  already on.
- While stopped, an extension command still runs its handler's own code; what
  it asks of `ctx` fails on the disposed runtime ("has been disposed"), and
  nothing it asks for runs a turn or a tool (verify V8: a handler that does not
  touch `ctx` returned its text).
- A restore whose settlement fails tears nothing more down: the restored
  harness is stopped (`stop()`), its cleanups not run, and its extensions get
  no `session_shutdown` until the next transition tears it down.
- A successful RPC `resources/reload` still comes back on the launch `--model`
  rather than a `set_model` choice (older than #663; the restore keeps the
  choice, D3).
