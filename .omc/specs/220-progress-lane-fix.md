# #220 — implementation review round 2, FIX LANE report

Worktree `/tmp/wt-220`, branch `fix/220-agents-process-tree`, base `main` `6586f8a`. Everything below was
done in `/tmp/wt-220` itself; no `git checkout/commit/stash/reset/add` was run. Probes that had to mutate
product code were applied and reverted in one process, with the file byte-compared against the original
afterwards (each such run prints `REVERTED`).

The spec's `§K` (new, appended to `.omc/specs/220-design-2026-09-05.md`) carries the same dispositions in
the #202-spec `§K` shape, plus a "Departures from this spec" list. This file adds the commands, the
outputs and the kill-path summary.

---

## 1. 🔴 KILL PATH CHANGED — the orchestrator must re-run the live checks

One product behaviour changed, on POSIX only, on the cancellation path:

| file | change |
| --- | --- |
| `reaper.py` | NEW public `kill_group_if_live(proc, tree) -> bool` (added to `__all__`). Runs `tree.hard_kill()` (POSIX `killpg(pgid, SIGKILL)`) iff **not win32**, the tree is **usable**, and `getattr(proc, "returncode", None) is None`. Returns whether it dispatched. |
| `print_channel.py` | `PrintChannel._eager_abort` now calls it after `kill_tree(...)`, **only when `row.reaper_task is None or row.reaper_task.done()`**. |

Nothing else in `reaper.py`, `print_channel.py`, `rpc_channel.py`, `print_mode.py`, `rpc_client.py` or
`_process_tree.py` changed behaviour — every other product edit in this lane is a comment or docstring.
`RpcChannel._eager_abort` was **not** given the same leg (reason in its docstring: the tree is borrowed
and `RpcClient.stop()` still owes it a hard leg).

**Why**: `posix2/P2`. `kill_tree` runs no POSIX group kill, so on macOS — where `descendant_pids` is
`[]` — a delegation cancelled before its deadline was a root-only `SIGKILL` and its non-`setsid`
grandchild survived. Ctrl+C is `turn_task.cancel()`, so that is the path a human drives, and on that path
`row.reaper_task` is `None`, i.e. nothing else ever escalates.

**Measured**, in `/tmp/wt-220` on darwin 25.4.0, with the call deleted and restored:

```
# _eager_abort's kill_group_if_live(...) call replaced by `pass`
$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/agents_ext/test_print_channel_spawn.py \
    -k cancelled_delegation_takes
E       AssertionError: pid 34362 survived 10.0s
FAILED tests/agents_ext/test_print_channel_spawn.py::test_a_cancelled_delegation_takes_its_non_setsid_grandchild
1 failed, 98 deselected in 10.16s
# restored -> passes
```

**What re-running should cover** (CLAUDE.md rule 10): a live delegation aborted with Ctrl+C / `stop`
before its deadline, on a real model, checking that a non-`setsid` grandchild of the child is gone. The
timeout path is unchanged and was already covered by `§H.6`. The TUI is untouched by this lane, so rule 9
does not newly apply.

**What is NOT covered by any test on any box**: the win32 half. `kill_group_if_live` early-returns on
win32 by construction (asserted by `test_the_eager_abort_leg_reaches_the_tree_per_platform[win32]`), so
the windows leg's exposure to this change is that assertion and nothing more.

---

## 2. Disposition of every finding

Full text in `§K` of the spec. Summary:

**Applied (BLOCKING):** `W32-1`/`SPEC-1` (same defect), `W32-3`, `posix2/P2`, `DOC-1`/`W32-8`, `MUT-1`,
`MUT-2`.

**Rejected as filed, doc half applied:** `W32-2` (both skeptics refuted the red prediction —
`KILL_ON_JOB_CLOSE` kills the grandchild on win32 regardless; the false *rationale* is fixed and Darwin
named as the arm that bites), `posix2/P1` (the proposed guard is a measured no-op on the eager path and a
regression on the grace path, where `hard_kill` is deliberately unguarded by root liveness; the false
*bound* is fixed in four places).

**Applied (SHOULD-FIX/NOTE):** `posix2/P3`, `posix2/P4`, `posix2/P5`, `W32-4`, `W32-5`/`DOC-6`, `W32-6`,
`W32-7`, `DOC-2`, `DOC-3`, `DOC-4`, `DOC-5`, `SPEC-2`, `TEST-1`, `MUT-3`, `MUT-4`, `MUT-5`, `MUT-6`,
`MUT-7`, `MUT-8`, `NOTE-1`.

**Not the fix lane's:** `PROC-1` — the #215 comment, the board move, `handoff-220-…md`, `§H.6(A)`'s live
survivor-recovery, `§H.6(C)`'s rpc driver and `§H.7`'s windows leg are session/orchestrator work.
**Carry-forward for whoever commits:** two shipped product docstrings cite
`.omc/specs/220-progress-2026-09-05.md` by path, and every `.omc/specs/220-*.md` is still `??` — they must
join the explicit `git add` pathspec of `§H.8`.

**Where a finding was right but its fix was wrong, the right fix was done instead** (all four recorded in
`§K`): `W32-3` (kept the window with a live child instead of guessing at Windows handle inheritance),
`MUT-2` (dropped the proposed `row.tree is None` assertion — `run()`'s `finally` nulls it on the success
path too, so it is green either way), `DOC-1` (dropped the proposed "pre-#220 children" rationale — parent
and child are always the same install), `W32-7` (kept the assertion hard; only the failure message gained
the discriminator).

**Departures from the spec, stated** (`§K`, "Departures"): (1) `§A.2`'s "Q1 in `reap()` only" is now "Q1
is never inside `kill_tree`"; (2) a new public name in `reaper.py`; (3) `_eager_abort`'s group kill defers
to a live reaper task — not in any finding's proposed fix, and necessary, because without it the new leg
ends the tree before the reaper's grace and silently deletes the window
`test_the_tree_is_closed_after_the_reapers_escalation_never_before` exists to measure; (4) `§D.2`'s
`_pipe_holding_daemon_stub` has no win32 pipe holder.

---

## 3. Files edited

**Product**

- `packages/aelix-coding-agent/src/aelix_agents/reaper.py` — NEW `kill_group_if_live` + `__all__`
  (kill path); module-docstring Q1 evidence repointed at the in-tree guard (`NOTE-1`); `_signal_child`'s
  false "an unreaped child cannot have its pid recycled" (`posix2/P3`); `kill_tree`'s false caller
  enumeration (`posix2/P2`); `reap`'s Q1 comment, false bound (`posix2/P1`); win32 leg-1 re-read noted as
  an equivalent mutant (`MUT-6`).
- `packages/aelix-coding-agent/src/aelix_agents/print_channel.py` — `_eager_abort`'s new guarded leg
  (kill path) + its docstring; `RunningChild.tree_owned`'s docstring (`MUT-5`).
- `packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py` — `_eager_abort` docstring states why it
  does **not** take the group kill; `row.tree_owned = False` noted as a declaration (`MUT-7`).
- `packages/aelix-coding-agent/src/aelix_agents/envelope.py` — `_STDERR_NOISE_RE`'s comment rewritten to
  past tense **and the regex kept, deliberately, as a defensive filter**, with the reason (`DOC-1`).
- `packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py` — summary line's inverted
  ordering (`DOC-3`); the falsified causal mechanism and the unsourced "measured shape" (`DOC-4`); the
  dead `scratchpad/probe_signal.py` pointer (`DOC-5`); the SIGBREAK-forwarding rationale (`MUT-4`).
- `packages/aelix-ai/src/aelix_ai/utils/_process_tree.py` — "the two asyncio sites" is now three
  (`posix2/P1`).
- `packages/aelix-coding-agent/src/aelix_agents/batch.py` — one hand-re-derived citation (`§F` step 3).

**Tests**

- `tests/agents_ext/test_print_channel_spawn.py` — `_SIGKILL` lend (`W32-1`/`SPEC-1`); `_spy_hard_kill`
  now records `(entered, returned)` and `test_sigterm_then_sigkill` warns the delta (`SPEC-2`);
  `timeout_ms` 8 000 → 30 000 for the real print child (`W32-6`); `_GRANDCHILD_BODY` + the grandchild
  test's win32 bullet corrected (`W32-2`); that test's pid capture moved into one `try/finally` and both
  cleanups use `getattr(signal, "SIGKILL", …)` (`posix2/P4`); the soft-signal case records what
  `soft_kill()` reported and says so in its message (`W32-7`) and its POSIX arm is labelled a
  fixture-integrity guard (`posix2/P5`); `_sigterm_ignoring_stub`'s `while True` bounded to 2400
  iterations (stray hygiene, see §6); `_pipe_holding_daemon_stub` rewritten (`W32-3`). **Five new
  tests**: `test_the_eager_abort_leg_reaches_the_tree_per_platform` (`MUT-1`),
  `test_the_eager_aborts_group_kill_is_withheld_from_a_reaped_child`,
  `test_the_eager_aborts_group_kill_defers_to_a_running_reaper`,
  `test_a_cancelled_delegation_takes_its_non_setsid_grandchild` (all `posix2/P2`),
  `test_an_attach_failure_degrades_instead_of_abandoning_the_child` (`MUT-2`).
- `tests/agents_ext/test_reaper_tree_win32.py` — shared `events` log and the interleaved-order assertion
  (`MUT-3`); case 7's docstring (`TEST-1`); clock-resolution slack on `>= GRACE` (`W32-4`).
- `tests/cli/test_print_mode_signal_handlers.py` — `_DECOY` and the lend moved out from under the handler
  (`MUT-4`); `_Gate`'s docstring twin (`DOC-4`).
- `tests/agents_ext/test_envelope.py` — `_SIGTERM_NOISE`'s header relabelled a historical sample
  (`DOC-1`).
- `tests/agents_ext/test_batch_executor.py` — one hand-re-derived citation (`§F` step 3).
- `tests/test_citation_drift.py` — its synthetic fixture's citation and the assertion that reads it are
  now interpolated from one pair of constants. They were separate literals and `--fix` relocated only the
  one inside the fixture, turning the gate green and the test red; see §5.

**Docs / spec**

- `docs/decisions/0238-…md` — the false `_eager_abort` enumeration and the false `reap` bound; "143 and
  149" now says which is measured and which the windows leg measures (`posix2/P1`, `posix2/P2`, `W32-5`).
- `docs/decisions/0198-…md`, `docs/decisions/0197-…md` — dated amendments beside the now-false
  present-tense SIGTERM-noise sentences; line citations into `print_mode.py` replaced by the function
  name per `§F`/F14 (`DOC-2`).
- `CHANGELOG.md` — 149 attributed to the windows leg (`W32-5`). README/README.ko unchanged: they already
  route the reader to that leg, and Korean parity is preserved.
- `.omc/specs/220-design-2026-09-05.md` — `§A.2` and `§B.1` corrected, three `§D.6` entries added, and a
  new `§K` (round-2 table + departures).
- `.omc/specs/220-progress-2026-09-05.md` — a new `§4`: the mutation lane's 34-mutation table, the six
  survivors with their disposition, and the eight round-2 mutations this lane ran (`MUT-8`).
- `citations.lock.json` — re-locked.

---

## 4. Mutations run by this lane on its own new lines

Each applied to `/tmp/wt-220`, tested, reverted, file byte-compared. All RED.

| mutation | selection | result |
| --- | --- | --- |
| `_eager_abort` drops `tree=row.tree` | `-k eager_abort` | `1 failed, 3 passed, 95 deselected in 0.16s` |
| `_eager_abort` drops the `reaper_task` deferral | `-k eager_aborts_group_kill_defers` | `1 failed, 98 deselected in 0.11s` |
| attach `except Exception` → `except ZeroDivisionError` | `-k attach_failure` | `1 failed, 98 deselected in 0.12s` |
| `kill_group_if_live` drops the `returncode` guard | `-k eager_aborts_group_kill_is_withheld` | `1 failed, 98 deselected in 0.11s` |
| `reap`'s Q1 `hard_kill` deleted | `-k non_setsid_grandchild` | `1 failed, 1 passed, 97 deselected in 60.18s` |
| `_eager_abort`'s `kill_group_if_live` call deleted | `-k cancelled_delegation_takes` | `1 failed` — `AssertionError: pid 34362 survived 10.0s` |
| Q1 moved BEFORE the walk | `tests/agents_ext/test_reaper_tree_win32.py` | `2 failed, 6 passed` — `index 1 diff: ('hard_kill',) != ('kill', 99, <Signals.SIGKILL: 9>)` |
| the SIGBREAK lambda re-reads `signal.SIGBREAK` | `tests/cli/test_print_mode_signal_handlers.py` | `1 failed, 4 passed` — `the win32 signal path returned 227, not 158` |

Also re-verified that `W32-1` is a real windows failure, by running the actual case under a plugin that
deletes `signal.SIGKILL` — **before** the fix:

```
$ PYTHONPATH=<probe> .venv/bin/python -m pytest -p no:cacheprovider -p nosigkill -q \
    tests/agents_ext/.../test_the_drain_after_exit_leg_is_the_job_on_win32_only
E  AttributeError: module 'signal' has no attribute 'SIGKILL'
packages/aelix-coding-agent/src/aelix_agents/reaper.py:301: AttributeError
1 failed, 1 passed
```

(That probe was the win32 lane's; I re-derived the raise site from the source rather than re-running it.)

---

## 5. Gate

```
$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/ruff format --check tests/agents_ext/test_reaper_tree_win32.py \
      tests/cli/test_print_mode_signal_handlers.py tests/print_mode_child.py
3 files already formatted
```

Citations, `§F`'s five steps in order:

1. `--check` — 54 drifted; **two reported "the block was EDITED, not moved"**, both
   `print_channel.py:964` (the bare `try:` `§F` names as ambiguous by construction), cited from
   `batch.py` and `tests/agents_ext/test_batch_executor.py`.
2. Hand-re-derived those two BEFORE `--fix`: `write_prompt_file` is now `print_channel.py:973` and the
   `try:` it is contrasted with is `:974`. Edited both citing sentences by hand.
3. `--fix` — `relocated 50 citation(s) across 20 file(s). 4 new citation(s) had no anchor; they are
   locked as written.` No "could NOT be relocated automatically" block.
4. `--lock`.
5. `--check` — `citations OK`. **Opened the hand-derived target and read it**: `print_channel.py:973` is
   `row.prompt = write_prompt_file(plan.resolved.name, profile.body)` and `:974` is `try:`, which is
   exactly what both citing sentences assert.

`--fix` also relocated the citation inside `tests/test_citation_drift.py`'s own synthetic fixture, whose
sibling assertion is a plain tuple the tool cannot see — turning the gate green and the test red
(`1 failed` in the first three targeted runs). Both are now interpolated from one pair of constants, which
also takes the synthetic out of the gate's sight (`850 → 848 gated`) where it always belonged.

```
$ .venv/bin/python scripts/check_citations.py --check
citations OK — 848 gated, none drifted.

$ .venv/bin/python scripts/check_types.py
OK  gate: 277 files analysed (floor 220)
OK  gate: 0 errors across 277 files
OK  spike: all 3 inverse assertions still error (narrowing alive)
PASS: type gate clean, narrowing spike still inverted

$ .venv/bin/pyright --pythonplatform Windows <7 changed product files>
0 errors, 0 warnings, 0 informations
```

Targeted set, three consecutive runs (final state):

```
2156 passed, 12 skipped, 3 warnings in 123.88s
2156 passed, 12 skipped, 3 warnings in 123.74s
2156 passed, 12 skipped, 3 warnings in 123.44s
```

with the new `§D.6`/`§H.7` numbers in the warnings summary each time:

```
UserWarning: hard_kill wall time on darwin: 0.000s
UserWarning: real print child soft-kill on darwin: 0.026s / 0.027s / 0.039s, returncode=143
```

Full suite:

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q tests
10143 passed, 12 skipped, 13 warnings in 310.64s (0:05:10)
```

---

## 6. Strays

`pgrep -f "not die politely|grandchild spawned|holding my pipes|print_child.py"` after each of the three
final targeted runs and after the full suite: **empty**.

It was NOT empty mid-lane. Four orphaned `_sigterm_ignoring_stub` children (PPID 1) from a single earlier
`pytest-1698` session were alive for 49 minutes — from `test_reap_still_escalates_after_the_signal_change`,
`test_sigterm_then_sigkill`, `test_the_soft_signal_is_delivered_and_survived` and
`test_prompt_file_unlinked_on_e3`. Five repeat runs of exactly those tests produced **zero** new strays, so
this is not a reproducible per-test leak; what made it survivable is that `_sigterm_ignoring_stub` was the
one stub in the file with an **unbounded** `while True` loop, so any session that dies before its
escalation lands leaks a process forever. Bounded to `range(2400)` (120 s), matching `_GRANDCHILD_BODY`
and every other stub here. The four were killed.

One stray is NOT mine and was left alone: `pid 22437 /tmp/wt-220/.venv/bin/python /tmp/scr220b/child3e.py`
(PPID 1, 3.5 h), a leftover probe from another refutation lane's scratch directory.

---

## 7. What this lane did not do, and what is still open

- `§H.6(A)` (live survivor-recovery for Q1), `§H.6(B)`/`(C)`, `§H.7` (the windows leg) — **not run**. The
  windows leg is still the only measurement of 149, of `IsProcessInJob`, of `CTRL_BREAK_EVENT` delivery,
  of `taskkill.exe`'s real duration, and of every `W32-*` prediction; nothing on this box executed any of
  them. All `W32-*` fixes above are reasoned from source and from POSIX emulations, and say so.
- `PROC-1`'s process items (#215 comment, board, handoff, `§H.8` pathspec).
- The macOS **cancel-path** hole is closed for the print channel only. `RpcChannel`'s equivalent is
  stated, not fixed, and `RpcChannel` is not wired into the product.
- `MUT-6`'s equivalent mutant and `MUT-5`'s unreachable-false read are recorded, not closed; no test can
  kill either without restructuring the code they live in.
