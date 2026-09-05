# #220 — lane report: **reaper**

Worktree `/tmp/wt-220`, branch `fix/220-agents-process-tree`, base `main` `6586f8a`.
Spec: `.omc/specs/220-design-2026-09-05.md` revision 2 (§B.1, §A.2, §A.7/Q4, §C.2, §B.6, §D.1, §C.5).
Box: darwin 25.4.0, py 3.12.13, `/tmp/wt-220/.venv`.

## 1. Files changed / created

| file | what |
| --- | --- |
| `packages/aelix-coding-agent/src/aelix_agents/reaper.py` | **changed** — `TYPE_CHECKING` import of `ProcessTree`, new `_is_win32()` / `_usable()`, `kill_tree(..., *, tree=None)` win32 job arm, `reap(..., tree=None)` win32 leg 1 + Q1 POSIX group kill; module / `_kill_signal` / `pdeathsig_preexec` / `kill_tree` / `reap` docstrings |
| `packages/aelix-ai/src/aelix_ai/utils/_process_tree.py` | **changed** — public `closed` property beside `job`; three docstring edits (module `kill_on_close` per-site sentence, `_taskkill_tree` caller enumeration, the `weakref.finalize` comment). `:40-42` NOT touched (verified by `sed -n '40,43p'` after the edit: the "does not re-adopt `killpg` as a substitute" sentence is byte-identical) |
| `tests/agents_ext/test_reaper_tree_win32.py` | **new** — 7 cases (8 test ids; case 7 is parametrized `linux`/`darwin`) |
| `tests/agents_ext/test_reaper_kill_signal_win32.py` | **untouched**, green (§C.5) |

Diffstat for the two product files: `_process_tree.py` +53/-, `reaper.py` +186/-, 210 insertions / 29 deletions.

## 2. Commands run, with output tails

```
$ .venv/bin/ruff check packages/aelix-coding-agent/src/aelix_agents/reaper.py
All checks passed!

$ .venv/bin/ruff check packages/aelix-ai/src/aelix_ai/utils/_process_tree.py
All checks passed!

$ .venv/bin/ruff check tests/agents_ext/test_reaper_tree_win32.py
All checks passed!

$ .venv/bin/ruff format --check tests/agents_ext/test_reaper_tree_win32.py   # NEW file only, ADR-0152
1 file already formatted
```

(`ruff format` was run once on the new file only — it wanted one call unwrapped;
no existing file was reformatted.)

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q \
    tests/agents_ext/test_reaper_tree_win32.py tests/agents_ext/test_reaper_kill_signal_win32.py
12 passed in 0.91s

$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/process_tree tests/tools/test_process_tree_win32.py
53 passed in 1.49s

$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/agents_ext tests/agents
1686 passed, 11 skipped, 1 warning in 48.29s
  (warning: real rpc child orderly shutdown on darwin: 0.072s, returncode=0 — pre-existing)

$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/rpc
222 passed, 1 skipped in 8.54s

$ for i in 1 2 3; .venv/bin/python -m pytest -p no:cacheprovider -q tests/agents_ext/test_reaper_tree_win32.py
8 passed in 0.89s / 8 passed in 0.90s / 8 passed in 0.89s      # no flake in the timestamp assertions
```

```
$ .venv/bin/pyright --pythonplatform Windows \
    packages/aelix-coding-agent/src/aelix_agents/reaper.py \
    packages/aelix-ai/src/aelix_ai/utils/_process_tree.py
0 errors, 0 warnings, 0 informations

$ .venv/bin/python scripts/check_types.py
OK  gate: 277 files analysed (floor 220)
OK  gate: 0 errors across 277 files
OK  spike: all 3 inverse assertions still error (narrowing alive)
PASS: type gate clean, narrowing spike still inverted
```

**The three `# pyright: ignore[reportOptionalMemberAccess]` comments are load-bearing, measured.**
A copy of `reaper.py` with them stripped:

```
error: "hard_kill" is not a known attribute of "None" (reportOptionalMemberAccess)   # kill_tree's job arm
error: "soft_kill" is not a known attribute of "None" (reportOptionalMemberAccess)   # reap leg 1
error: "hard_kill" is not a known attribute of "None" (reportOptionalMemberAccess)   # reap Q1
3 errors
```

pyright does not narrow through `_usable`, exactly as §E predicted.

## 3. Mutation checks — all four performed, each restored afterwards

The file was backed up first and byte-compared after the last restore
(`diff … && echo IDENTICAL` → `IDENTICAL`), then the whole set re-run: `65 passed`.

| # | mutation | expected | measured |
| --- | --- | --- | --- |
| 1 | `reap`'s Q1 `tree.hard_kill()` → `pass` | case 7 red | **RED** — `test_posix_tree_uses_the_signal_legs_and_then_the_group[linux]` and `[darwin]`: `AssertionError: [] assert [] == ['hard_kill']`. 2 failed, 6 passed in 0.92s |
| 2 | `_usable` → `return tree is not None` | case 6 red | **RED** — `test_win32_closed_tree_falls_back_to_the_signal_legs`: `TimeoutError` out of `asyncio.wait_for(..., 5.0)` (the mutant takes the tree arm, sends no `os.kill`, so the wedged waiter's gate never opens). 1 failed, 7 passed in 5.74s |
| 3 | drop the `not eager_kill` clause from leg 1's condition | case 3 red | **RED** — `test_win32_eager_kill_skips_the_soft_leg`: `assert ['soft_kill', 'hard_kill'] == ['hard_kill']`. 1 failed, 7 passed in 0.91s |
| 4 | delete `kill_tree`'s win32 tree arm | case 4 red | **RED** — `test_win32_kill_tree_uses_the_job_and_signals_no_pids` plus cases 1, 2 and 3 (they all escalate through `kill_tree`). 4 failed, 4 passed in 15.75s |

Mutation 2 is the only one that fails as a *timeout* rather than an assertion; that is by
construction (§D.1's `asyncio.wait_for(..., 5.0)` around every case) and it is why the wrapper is
not decoration.

## 4. Deviations from the spec, with reasons

1. **§B.1's nested `if` in `reap`'s leg 1 is written as one short-circuiting condition.**
   `ruff check` fails the spec's literal shape with `SIM102` ("Use a single `if` statement instead of
   nested `if` statements"). Written as

   ```python
   if (
       not eager_kill
       and getattr(proc, "returncode", None) is None
       and not tree.soft_kill()
   ):
       eager_kill = True
   ```

   Semantics are identical — `and` short-circuits, so `soft_kill()` is still reached only when the
   run is non-eager AND the child is still alive, and the `returncode` read is still the last thing
   before the call with no `await` between (win32/W13). The clause ORDER is now the contract, and the
   comment above it says so. Mutation 3 targets the `not eager_kill` clause instead of a deleted
   `if` line; it still goes red.

2. **§D.1 case 7 says "no platform patch, POSIX"; it injects `linux`/`darwin` instead.**
   That instruction reads correctly from a POSIX box and is wrong on the `windows-latest` leg, where
   an unpatched case would take the win32 arm and assert the opposite of what the case exists for
   (the repo's inject-don't-skip rule, `tests/conftest.py`). So `_simulate_posix` patches
   `reaper.sys.platform` and lends `signal.SIGKILL` back — the same two moves the sibling
   `test_reaper_kill_signal_win32.py::test_posix_still_escalates_with_sigkill` already makes for the
   same reason. Parametrized over both POSIX platforms, matching that sibling. Consequence: 7 cases,
   8 test ids.

3. **§D.1's `_Tree.hard_kill` ends `self.gate.set()` with the gate "bound by the test".**
   Implemented as an optional `gate=` constructor argument (plus a `None` guard), because case 4 and
   case 6 construct a tree with no waiter to open. Same mechanism, no post-construction attribute
   poking.

4. **Cases 5 and 6 open their wedged waiter from the `os.kill` spy, not from the tree.**
   Both are fallback cases in which the tree never fires by design, so `_Wedged` would hang. The spy
   sets the gate when the DESCENDANT pid is signalled — i.e. when the escalation has provably
   reached `kill_tree`'s loop — which is also what makes mutation 2 fail instead of pass.

5. **`ProcessTree.job`'s neighbour, not its docstring.** §B.6's `closed` property is added verbatim in
   substance; the wording was extended by one sentence naming `reaper._usable` and the
   read-as-attribute contract.

## 5. What I could NOT verify

- **Everything win32 is injected, never executed.** No `CTRL_BREAK_EVENT`, no `taskkill.exe`, no
  `TerminateJobObject` ran on this box. `_is_win32()` was patched; the tree was a recording stub.
  The windows-latest leg (§H.7) is the measurement.
- **`ProcessTree.closed` has no test in THIS lane that goes red when the property is deleted.** My
  stub declares `closed` itself, so deleting the real property breaks nothing here — it breaks at
  the first *real* tree the reaper is handed, which lives in the print-channel lane
  (`test_print_channel_spawn.py`'s tree cases, §D.2). Flagged below.
- **Q1's live coverage.** That a real non-`setsid` descendant of a real delegation child survives
  today and dies to the group kill is spec §A.2's Darwin probe **[M]** and §H.6(A)'s live run; this
  lane only pins the dispatch.
- **Citations.** `citations.lock.json` has exactly one anchor in either of my files —
  `packages/aelix-coding-agent/src/aelix_agents/reaper.py:80-80` → `DEFAULT_GRACE_SECONDS = 5.0` —
  and my edits shift it (the `TYPE_CHECKING` block grew). It is a one-line anchor whose text is
  byte-identical, so `--fix` relocates it automatically. I did not run the §F procedure: it is
  repo-wide and belongs to the integrating step, not to a lane editing three files concurrently
  with others.
- **`reap`'s grace being bounded by pipe disconnection rather than by the child's exit** (§C.2,
  rpc/RPC-6) — stated in the docstrings, unchanged, not measured here.

## 6. Needed outside this lane (NOT done — not my files)

1. **`print_channel.py`** must pass `tree=` at all four call sites (§B.2) and own the close (§A.6
   rule 2). Nothing in `reaper.py` reads a row.
2. **`rpc_channel.py`** must set `row.tree` / `row.tree_owned` and pass `tree=row.tree` (§B.5).
3. **A test that goes red when `ProcessTree.closed` is deleted** belongs in the print-channel lane
   (§D.2's real-tree cases) or in `tests/process_tree`. My lane cannot host it: §D.1 forbids a real
   `ProcessTree` in this file, and under the global `sys.platform` patch `attach` raises
   `AttributeError: module 'ctypes' has no attribute 'WinDLL'`.
4. **`_taskkill_tree`'s rewritten docstring now names `PrintChannel._eager_abort` and
   `RpcChannel._eager_abort` as synchronous callers.** That becomes true only when those two lanes
   land their `tree=` plumbing. If either is dropped, that sentence must be trimmed to match.
5. **Citations** (§F, five steps) and the `#215` `escalation_reachable` retirement (§A.8) — other
   lanes / the integrating step.
