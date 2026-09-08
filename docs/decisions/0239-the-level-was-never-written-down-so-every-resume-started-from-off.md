# 0239. The level was never written down, so every resume started from `off`

Status: Accepted (2026-09-08)
Date: 2026-09-08
Supersedes/relates: ADR-0196 (its D6.3 thinking-level ladder, **amended** here
with a third rung), ADR-0135 (the thinking level as harness *state* — untouched;
this ADR is about the *file*), ADR-0235 (Pi is a reference, not a target, so the
three divergences below owe no argument).
Issue: #198.
Design spec: `.omc/specs/198-design-2026-09-08.md`.

`/thinking high`, `/quit`, `aelix --continue` — and you are back at `off`. Two
independent faults produced that, and either one alone was enough to reproduce
it.

## What was actually broken, measured on `main` 0985fcf

**Half 1 — an idle change was never written down.**
`AgentHarness.set_thinking_level` mutated `_state.thinking_level` and queued a
`PendingThinkingLevelChangeWrite` **only when `self._phase == "turn"`**; the
flush dispatcher was the only caller of `Session.append_thinking_level_change`
in the repo. Measured with a harness at phase `idle`:

```
state.thinking_level: high
entry types after idle set_thinking_level: []
build_context.thinking_level: off
```

Every caller a user actually reaches is idle: the `/thinking` picker, the
`/settings` row, the startup settings seed, and `/agents use`. So the session
file held **zero** record of the level. Only `/settings` appeared to survive a
relaunch, and only because the startup seed re-applied the *global*
`defaultThinkingLevel` — not because anything about that session was remembered.

**Half 2 — a session that did carry the entry was ignored on resume.**
`build_session_context` folds `thinking_level_change` last-wins into
`SessionContext.thinking_level`, and neither seam that builds a harness from a
resumed session read it. Startup (`--resume`/`--continue`/`--session`/`--fork`)
seeded `state.messages` only (#122's `_seed_startup_messages`); in-session
`/resume`, `/fork` and `/new` rebuilt `_state.messages` only. Measured: a session
carrying `thinking_level_change("high")` reported
`session context -> high 1 msgs` while the harness built over it reported
`harness after build -> off`.

The in-session half is the worse one: it rebuilds through the harness factory, so
`/resume` discarded the level set *in that very process*. Pi has no such half —
its `AgentSession` survives `switchSession` (`agent-session-runtime.ts:256` at
`pi@da840b6`), so `agent.state.thinkingLevel` carries over for free.

## Decision

**The session transcript is the record of the level.**

1. **`set_thinking_level` appends when it is not in a turn**, and only when the
   level actually changes (Pi's `isChanging`). The in-turn pending-write deferral
   is untouched, so a mid-turn change still lands after the turn's messages.
2. **The append runs after the `thinking_level_select` emit**, so a level an
   extension refuses leaves no entry. `/agents use` rolls a refused level back by
   writing `AgentState` directly, which cannot undo a session append.
3. **Both resume seams restore it** through one pure helper,
   `resolve_resumed_thinking_level(path_entries, model, *, fallback)`. Ladder:
   `--thinking`/profile > the session's entry > (unchanged) the settings-default
   seed. Both seams pass the live model, so a level the resumed model cannot do
   is **clamped**, never dropped: `xhigh` on a `high`-max model resumes at
   `high`. That ladder holds **at launch**. At the in-session seam it inverts by
   construction: `/resume` rebuilds the harness through a factory that closes
   over `parsed`, so the new harness starts at `--thinking`'s level and the
   restore then overwrites it with the target session's own. Kept, because
   `/resume <id>` is a request for *that session*, not a re-run of the command
   line; documented in `docs/guides/providers-and-models.md`.
4. **An explicit `off` restores as `off`.** An entry is a decision, not an
   absence. `build_session_context` cannot express this — its initial value *is*
   `"off"` — which is why the helper exists alongside it and returns `None` for
   "nothing recorded".
5. **The startup settings seed is told, not left to guess.** `run_tui` takes a
   derived `thinking_level_restored: bool`. ADR-0196 D6.3's guard sniffs
   `state.thinking_level` for "unset", and a restored explicit `off` is
   indistinguishable from unset by value. Without the signal, decision 1 turns
   that seed into a *writer* and overwrites a recorded `off` in the file on first
   launch — data that survives today. This amends D6.3; it does not reverse its
   "`parsed` is not threaded into `run_tui`" clause, since a derived boolean is
   not `parsed`.
6. **Both seams carry the live/flag level forward** when the target session has
   no entry of its own — in-session this is Pi's implicit behaviour, since Pi
   never rebuilds — **and record it** when it is not the kernel default `off`.
   Without the append, `/new` at `medium` produces a session whose file says
   `off` and the headline bug reproduces the next time that session is opened.
   The startup seam does the same for `cli_level`: `--thinking` is the *only*
   level source that would otherwise never be written down, so
   `aelix --thinking high` then `--continue` came back at `off` while the weaker
   `defaultThinkingLevel` — which reaches the harness through
   `set_thinking_level`, i.e. through decision 1's append — survived. A session
   that already recorded a level is not overwritten by the flag; the flag wins
   for that process only.
7. **The model is deliberately NOT restored here.** Same bug class, bigger
   radius: `set_model` has the identical turn-phase gate, and the restore belongs
   in `_build_harness_options`' provider ladder rather than a post-build
   assignment — Pi additionally requires configured auth and emits "could not
   restore model", and the #98 unrunnable-startup gate judges the model after the
   build. **Ordering note for that follow-up:** restore the model *before*
   clamping the level, or the clamp validates against the wrong model.

Rejected: *restoring inside `_build_harness_options`* — one call site for both
seams, but it cannot see the pre-swap live level (6), it also fires on `/reload`,
and it moves a kernel-shaped concern into the repo's most tangled precedence
ladder. *Calling `harness.set_thinking_level(level)` to restore* — it would fire
`thinking_level_select` at extensions for something the user did not do; both
seams assign state directly, exactly as they already do for `state.messages`.
*Gating the hook emit on "changed" too (full Pi parity)* — out-of-scope churn;
`tests/pi_parity/test_setter_emit_sites_match_pi.py` and
`tests/test_harness_setters.py` pin the unconditional emit. Only the **write** is
change-gated.

## Divergences from Pi (ADR-0235: recorded, not argued)

- **Layering.** Pi appends from its product layer
  (`coding-agent/src/core/agent-session.ts:1793-1815`); its harness
  (`agent/src/harness/agent-harness.ts:576`) only declares the setter. Aelix
  appends in the harness, where the session handle already lives.
- **Raw vs clamped.** Pi appends the *clamped* level. Aelix appends the raw one
  — this setter does not clamp today, and making it clamp is a second behaviour
  change. The restore clamps instead.
- **No `messages.length > 0` conjunct.** Pi honours a session's level only when
  the session also has messages (`sdk.ts:191,232`). Aelix restores on the entry's
  presence alone: a level set before the first prompt is still a decision.

## Consequences

- The session file grows one `thinking_level_change` per level change; per `/new`
  or `/resume` into a session with no level of its own; one the first time a
  session with no level of its own is launched under `--thinking`; and one the
  first time a session with no level of its own is opened while
  `defaultThinkingLevel` is set.
  That last one is bounded to a single entry, after which **that session
  out-ranks a later change to the global default** — accepted as "the level this
  session ran at".
- **A cancelled `/agents use` can still leave one entry.** Its `except
  BaseException` rollback writes `AgentState` directly. Decision 2 removes the
  reachable case (a refusing extension hook); a `CancelledError` landing between
  the emit and the append still leaves an entry for a rolled-back level.
  Last-wins recovers it on any later change, and a compensating append in the
  rollback would race the other way. Recorded, not guarded.
- **`ExtensionAPI.setThinkingLevel` can now fail asynchronously.** Decision 1
  puts file I/O on the idle path, and the extension binding fires the setter as
  a pinned fire-and-forget task. `_pin_task` therefore retrieves and `debug`-logs
  the task's exception instead of leaving asyncio to report
  `Task exception was never retrieved` at GC time with no caller in the
  traceback. All pinned extension-action tasks get that, not only this one.
- **`/reload` is a third rebuild seam and is untouched.**
  `AgentSessionRuntime.reload` rebuilds through `_teardown_current` + `_apply`,
  not `_finish_session_replacement`, so it still drops the level. Cheap to fix
  now that the helper exists; out of scope for #198.
- The write fires for any non-`turn` phase, including `compaction` and
  `branch_summary`. A level change cannot realistically be issued from there and
  the entry would be harmless, so it is not guarded.
- Restoration is not TUI-only: the boolean is computed upstream of the mode
  dispatch, so `--print`, `--mode json` and RPC resume at the recorded level too.
