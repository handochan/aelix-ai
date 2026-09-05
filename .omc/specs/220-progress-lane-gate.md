# #220 — lane report: **gate** (integration verification)

Worktree `/tmp/wt-220`, branch `fix/220-agents-process-tree`, base `main` `6586f8a`.
Spec: `.omc/specs/220-design-2026-09-05.md` revision 2 (§F, §E, §H.1-5).
Box: darwin 25.4.0, py 3.12.13, `/tmp/wt-220/.venv`.

Read first: all five lane reports (`220-progress-lane-{reaper,print_channel,print_mode,rpc,docs}.md`) and
`220-progress-2026-09-05.md`. Their `needs_other_lane` items are tracked in §7 below, each with a verdict.

## 1. Files this lane changed

| file | what | authority |
| --- | --- | --- |
| `citations.lock.json` | rewritten by `check_citations.py --fix` then `--lock` | §F steps 2/4 |
| 20 files touched by `--fix` (see §3) | relocated citation line numbers only | §F step 2 |
| `packages/aelix-coding-agent/src/aelix_agents/batch.py:327` | hand re-derivation `:931` → `:964` | §F step 3 |
| `tests/agents_ext/test_batch_executor.py:260` | hand re-derivation `:931` → `:964` | §F step 3 |
| `packages/aelix-coding-agent/src/aelix_agents/print_channel.py:1001` | hand re-derivation `print_mode.py:114-131` → `:131-190` | §F step 3 |
| `tests/agents_ext/test_print_channel_spawn.py:580` | hand re-derivation `print_mode.py:114-131` → `:131-190` | §F step 3 |
| `tests/test_citation_drift.py:441` | one-line fix, `(158, 165)` → `(217, 224)` — a consequence of my own `--fix` (§4) | one-line obvious error |

No product behaviour was changed by this lane. `_process_tree.py` was mutated and restored
byte-identically for the §6 mutation check (`diff` verified).

## 2. Step 1 — ruff

```
$ /tmp/wt-220/.venv/bin/ruff check .
All checks passed!                                     (exit 0)

$ /tmp/wt-220/.venv/bin/ruff format --check tests/agents_ext/test_reaper_tree_win32.py \
      tests/cli/test_print_mode_signal_handlers.py tests/print_mode_child.py
3 files already formatted                              (exit 0)
```

NEW files (`git status --porcelain` `??`), excluding `.omc/specs/`: exactly those three.
`ruff format --check` was NOT run on any pre-existing file (ADR-0152).

Re-run after every edit this lane made: `ruff check .` → `All checks passed!` again.

## 3. Step 2 — citations, §F's five steps exactly

### Step 1 — `--check` BEFORE `--fix` (the reliable signal)

```
$ .venv/bin/python scripts/check_citations.py --check          (exit 1)
55 drifted, 2 unlocked. Run `python scripts/check_citations.py --fix` to relocate what can be relocated.
```

**Every "the block was EDITED, not moved" line, recorded before `--fix` — exactly four, all four
predicted by §F:**

| citing site | target | anchor text |
| --- | --- | --- |
| `packages/aelix-coding-agent/src/aelix_agents/batch.py:327` | `print_channel.py:931` | `try:` |
| `packages/aelix-coding-agent/src/aelix_agents/print_channel.py:1001` | `modes/print_mode.py:114-131` | `# === Pi step 1 — signal handlers (non-Windows) ===…` |
| `tests/agents_ext/test_batch_executor.py:260` | `print_channel.py:931` | `try:` |
| `tests/agents_ext/test_print_channel_spawn.py:580` | `modes/print_mode.py:114-131` | same header block |

Two unlocked (NEW) citations, both from the rpc lane, both locked as written by step 4:
`rpc_channel.py:517` → `rpc_client.py:637-642`, `rpc_channel.py:780` → `rpc_client.py:610`.

§F's two other predicted stuck anchors did **not** appear, and the reason is checkable:

- `print_channel.py:970` (the one-line ``# ``None`` on Windows, where subprocess REJECTS the``) is cited
  ONLY from `.omc/specs/` files, which are ungated by design (`scripts/check_citations.py:36-39`). It is
  in the lock as an anchor but nothing gated cites it, so no drift is reported. The print-channel lane's
  worry about keeping it byte-identical was therefore moot — but it DID keep it (the line is intact, now
  at `:1018`).
- `runtime.py:868` is cited from `batch.py:439`; `runtime.py`'s only edits are citation relocations
  above that line's anchor text, and the anchor did not drift.

### Step 2 — `--fix`

```
$ .venv/bin/python scripts/check_citations.py --fix             (exit 1)
relocated 51 citation(s) across 20 file(s).

4 could NOT be relocated automatically — re-derive by hand:
  packages/aelix-coding-agent/src/aelix_agents/batch.py:327: `print_channel.py:931`
      cited: try:
  packages/aelix-coding-agent/src/aelix_agents/print_channel.py:1001: `modes/print_mode.py:114-131`
      cited: # === Pi step 1 — signal handlers (non-Windows) ============================
  tests/agents_ext/test_batch_executor.py:260: `print_channel.py:931`
      cited: try:
  tests/agents_ext/test_print_channel_spawn.py:580: `modes/print_mode.py:114-131`
      cited: # === Pi step 1 — signal handlers (non-Windows) ============================

2 new citation(s) had no anchor; they are locked as written.
locked 486 anchor(s) over 850 gated citations (32 ungated, 0 out of range).
```

The `--fix` stuck list is IDENTICAL to the step-1 EDITED list. Neither is a superset of the other, so
§F's warning about `--fix`'s exit code conflating the two did not bite here — but step 1's list is what
this lane worked from, per the procedure.

### Step 3 — hand re-derivation of the four (this is the step `--lock` would have papered over)

**Target A — `print_channel.py:931`, the bare `try:` of `PrintChannel.run`.** The citing claim, identical
in both sites, is *"``PrintChannel.run`` writes the prompt file OUTSIDE its own ``try``
(``print_channel.py:963`` vs ``:931``)"*. `--fix` relocated the FIRST half of that pair automatically
(`:930` → `:963`, `row.prompt = write_prompt_file(...)`) and left the second half stuck, because a bare
`try:` has 11 identical hits. Read in the new tree:

```
963:        row.prompt = write_prompt_file(plan.resolved.name, profile.body)
964:        try:
965:            if row.stopped:
```

→ **`:931` → `:964`.** The claim is unchanged and still true: the write is one line above the `try:` that
`run`'s own error handling opens.

**Target B — `modes/print_mode.py:114-131`, the Pi-step-1 signal-handler block.** The citing claim, again
identical in both sites, is *"neither parent (`tui/shell.py:1874-1891`) nor child
(`modes/print_mode.py:114-131`) installs a SIGINT handler"*. On `main` the anchor was the section header
through the end of the POSIX install loop — i.e. **the complete set of signals this child installs
handlers for** (verified against `git show main:…print_mode.py | sed -n '114,131p'`: header at 114,
`signals_installed` at 115, `if sys.platform != "win32":` at 116, POSIX loop to `pass` at 131). §B.3 moved
`_handle_signal` out of the branch and added the win32 arm, so that 18-line sequence cannot reappear.

The equivalent construct in the new tree is the same section, now spanning both arms:

```
131:    # === Pi step 1 — signal handlers (POSIX: SIGTERM/SIGHUP; win32: SIGBREAK) ==
132:    signals_installed: list[int] = []
…
151:    if sys.platform != "win32":
152:        for sig_name in ("SIGTERM", "SIGHUP"):
…
163:    else:
…
181:        try:
182:            break_previous = signal.signal(
183:                signal.SIGBREAK,
…
190:            pass
191:
192:    # === Pi steps 2-3 — rebind closure + subscribe ============================
```

→ **`114-131` → `131-190`.** The citing SENTENCE is untouched and still true: the child now installs
SIGTERM/SIGHUP (POSIX) and SIGBREAK (win32), and still no SIGINT handler. §F said "relocate the range
only"; that is what was done.

### Step 4 — `--lock`

```
$ .venv/bin/python scripts/check_citations.py --lock             (exit 0)
locked 486 anchor(s) over 850 gated citation(s) (32 ungated, 0 out of range).
```

### Step 5 — `--check`, then open each hand-derived target and READ it

```
$ .venv/bin/python scripts/check_citations.py --check            (exit 0)
citations OK — 850 gated, none drifted.
```

That green is green by construction (§F says so), so both hand-derived targets were opened at their new
ranges and read — the two listings quoted in step 3 above ARE that read, taken from the post-`--lock`
tree. Lock entries confirmed:

```
packages/…/print_channel.py:964-964        → ["try:"]
packages/…/modes/print_mode.py:131-190     → 8 lines (MAX_ANCHOR_LINES=8, scripts/check_citations.py:253):
    first: "# === Pi step 1 — signal handlers (POSIX: SIGTERM/SIGHUP; win32: SIGBREAK) =="
    last : "break_installed = False"
```

### Two auto-relocations spot-checked by hand (both anchor on a duplicated `except asyncio.CancelledError:`)

The text-pinned relocation is mechanically sound, but two anchors are 8-line blocks beginning with a line
that occurs many times, which is the shape that CAN land on the wrong construct. Both were opened:

- `rpc_channel.py:387` → `print_channel.py:1106-1113` → **`1224-1231`** (the rpc lane flagged this as
  needing a look, not an assumption). The new range is `except asyncio.CancelledError:` /
  `self._eager_abort(proc, row)` / … / `pumps.cancel()` / `raise` — exactly the "cancelling its pumps next
  to its own `_eager_abort`" the citing comment describes. **Correct.**
- `batch.py:311` → `print_channel.py:1218-1220` → **`1363-1365`**. The citing text names the construct
  inline (``_reap``'s ``except CancelledError: self._eager_abort(proc, row); raise``); the new range is
  literally that. **Correct.**

## 4. Step 4 — the targeted suite, ×3

**First run FAILED, once, with a real failure caused by this lane's own `--fix`:**

```
FAILED tests/test_citation_drift.py::test_a_continuation_does_not_steal_a_file_named_after_it
>       assert (158, 165) in {(c.start, c.end) for c in found}
E       assert (158, 165) in {(217, 224), (314, 315)}
tests/test_citation_drift.py:441: AssertionError
1 failed, 2064 passed, 12 skipped, 2 warnings in 93.92s
```

**Diagnosis.** That test builds a SYNTHETIC docstring `body` containing a literal
`` ``modes/print_mode.py:158-165`` `` and asserts the parser finds it. The literal lives in the real file,
so `check_citations.py` treats it as a real citation into the real `print_mode.py` — and `print_mode.py`'s
`if stdout_dead["v"]:` block moved 158-165 → 217-224 (the print_mode lane's edits). `--fix` rewrote the
fixture at `:410`; the assertion at `:441` is a Python tuple, not a citation, so nothing rewrote it and
the two halves of the same fixture disagreed.

**Fixed** — one line, in a file this lane's own `--fix` had already touched, `(158, 165)` → `(217, 224)`.
The assertion still guards what its comment says it guards ("the full citation on its own line is still
picked up, so this is not passing by scanning nothing"): the tuple must match the fixture, and the
fixture is now 217-224.

**Note for the fix lane / a follow-up (NOT fixed here):** this test is structurally fragile — its fixture
is a gated citation into a live file, so `--fix` will silently desynchronise it from its own assertion
every time `print_mode.py` moves. A fixture pointing at a path the gate does not resolve (or asserting
`found`'s single full-citation range rather than a literal) would not have this failure mode. Out of my
lane; recorded.

**Three clean runs after the fix, identical:**

```
$ for i in 1 2 3; do .venv/bin/python -m pytest -p no:cacheprovider -q \
    tests/agents_ext tests/agents tests/rpc tests/cli/test_print_mode.py \
    tests/cli/test_print_mode_json_contract.py tests/cli/test_print_mode_signal_handlers.py \
    tests/cli/test_pipe_robustness.py tests/process_tree tests/tools/test_process_tree_win32.py \
    tests/pi_parity/test_phase_4_4_strict_superset.py tests/test_citation_drift.py --durations=12; done

RUN 1 exit=0   2065 passed, 12 skipped, 2 warnings in 93.57s (0:01:33)
RUN 2 exit=0   2065 passed, 12 skipped, 2 warnings in 93.61s (0:01:33)
RUN 3 exit=0   2065 passed, 12 skipped, 2 warnings in 93.56s (0:01:33)
```

**No flakes.** Identical pass/skip counts and wall time across all three; no test id varied.
Baseline for comparison (`220-progress` §0, `main` 6586f8a, a smaller path set): 1991 passed, 12 skipped.

The 12 skips are pre-existing (`main` skipped the same 12).

**The two `UserWarning`s are the measurements the spec asked to surface in the `-q` log** (`warnings.warn`
per the Windows-CI diagnostics rule), and the first is §H.6(B)'s POSIX half arriving in the suite:

```
tests/agents_ext/test_print_channel_spawn.py:3417: UserWarning:
    real print child soft-kill on darwin: 0.041s, returncode=143      ← §A.3(b), MEASURED
tests/agents/test_rpc_sprint_pins.py:378: UserWarning:
    real rpc child orderly shutdown on darwin: 0.086s, returncode=0
```

`returncode=143` is the number `220-progress` §1 measured as **1 with two tracebacks** on `main`. §A.3(b)
landed and is observable on this box.

Slowest 12 (run 3):

```
8.05s  test_print_channel_spawn.py::test_a_real_print_child_reports_the_signal_exit_code
6.59s  test_rpc_channel.py::test_a_turn_that_never_terminates_times_out_and_keeps_the_partial
5.52s  test_print_channel_spawn.py::test_a_wedged_child_that_closed_its_stdio_still_times_out
5.19s  test_rpc_channel.py::test_a_child_that_ignores_stdin_eof_is_reaped
5.18s  test_rpc_channel.py::test_the_reaper_the_channel_builds_carries_the_rows_tree
5.00s  test_pipe_robustness.py::test_argv_prompt_shortens_the_wait_on_a_silent_pipe
4.01s  test_print_channel_spawn.py::test_the_soft_signal_is_delivered_and_survived
3.01s  test_print_channel_spawn.py::test_the_tree_is_closed_after_the_reapers_escalation_never_before
2.56s  test_rpc_channel.py::test_a_timed_out_delegation_reports_what_arrived_during_the_drain
2.02s  test_print_channel_spawn.py::test_a_non_setsid_grandchild_dies_on_the_escalation
2.01s  test_print_channel_spawn.py::test_sigterm_then_sigkill
2.00s  test_pipe_robustness.py::test_slow_producer_with_argv_prompt_is_never_silently_dropped
```

## 5. Step 3 — type gates

```
$ .venv/bin/python scripts/check_types.py
OK  gate: 277 files analysed (floor 220)
OK  gate: 0 errors across 277 files
OK  spike: all 3 inverse assertions still error (narrowing alive)
PASS: type gate clean, narrowing spike still inverted
```

```
$ .venv/bin/pyright --pythonplatform Windows \
    packages/aelix-ai/src/aelix_ai/utils/_process_tree.py \
    packages/aelix-coding-agent/src/aelix_agents/{batch,chain,consent,envelope,print_channel,reaper,rpc_channel,runtime,stream}.py \
    packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py \
    packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py
0 errors, 0 warnings, 0 informations
```

Every changed product `.py` (12 files, the full `packages/**` set from `git status`) is covered. Six of
them (`chain`, `consent`, `envelope`, `runtime`, `stream`, `batch`) changed only by citation relocation;
they were typed anyway rather than assumed.

## 6. Mutation checks performed by THIS lane

This lane owns no product lines, so its mutation duty is the **cross-lane gap the lanes could not close
themselves**. One was named explicitly (reaper lane §5: *"`ProcessTree.closed` has no test in THIS lane
that goes red when the property is deleted … it breaks at the first real tree the reaper is handed, which
lives in the print-channel lane"*). It was tested for real.

| # | mutation | result |
| --- | --- | --- |
| 1 | **`ProcessTree.closed` property deleted outright** (`_process_tree.py:525-538`, the whole `@property` + docstring + `return self._closed`) | **RED — 11 failed, 1842 passed** over `tests/agents_ext tests/process_tree tests/rpc` |

The 11:

```
test_print_channel_spawn.py::test_a_wedged_child_that_closed_its_stdio_still_times_out
test_print_channel_spawn.py::test_reap_still_escalates_after_the_signal_change
test_print_channel_spawn.py::test_sigterm_then_sigkill
test_print_channel_spawn.py::test_a_non_setsid_grandchild_dies_on_the_escalation
test_print_channel_spawn.py::test_the_tree_is_closed_after_the_reapers_escalation_never_before
test_rpc_channel.py::test_the_rpc_row_carries_the_clients_tree
test_rpc_channel.py::test_stop_reaches_the_clients_tree_on_win32
test_rpc_channel.py::test_a_pre_created_reaper_task_still_kills_through_the_tree
test_rpc_channel.py::test_the_channel_never_closes_the_clients_tree
test_rpc_channel.py::test_the_reaper_the_channel_builds_carries_the_rows_tree
test_rpc_channel.py::test_a_cancelled_delegation_kills_through_the_tree
```

So §A.6 rule 4's `closed` property is load-bearing across two other lanes' real-tree beds, and the reaper
lane's flagged gap is closed. `reaper._usable` reading `tree.closed` as an ATTRIBUTE rather than
`getattr(..., False)` (§B.1, the fail-loudly requirement) is what makes the deletion an `AttributeError`
instead of a silent POSIX-arm fallback — which is the property the spec argued for and it holds.

**Restored byte-identically**, verified: `diff <snapshot> <file>` → no output, and `def closed` present
once. The three clean suite runs in §4 were taken AFTER the restore.

## 7. Every lane's `needs_other_lane`, adjudicated

| from | item | verdict |
| --- | --- | --- |
| reaper | `print_channel.py` must pass `tree=` at four sites + own the close | **LANDED** — `print_channel.py:1017` `containment_spawn_kwargs`, `:1050` `attach`, `:1062/:1065` `tree_owned`, `:1092` "THE SINGLE OWNER … §A.6 rule 2", `:1106` the `row.tree_owned` guard |
| reaper | `rpc_channel.py` must set `row.tree`/`tree_owned` and pass `tree=row.tree` | **LANDED** — `:529-530` on the row, `:755` in `_reap`, `:792` in `_eager_abort` |
| reaper | a test that goes red when `ProcessTree.closed` is deleted | **EXISTS** — 11 of them, §6 |
| reaper | `_taskkill_tree`'s docstring names `PrintChannel._eager_abort` / `RpcChannel._eager_abort` as synchronous callers | **TRUE NOW** — both lanes landed the plumbing (row above); no trim needed |
| reaper, print_channel, rpc | citations (§F five steps) | **DONE**, §3 |
| print_channel | `print_channel.py:970` / `:967` / `:931` citation handling | **DONE** — `:931`→`:964` by hand; `print_mode.py:114-131`→`:131-190` by hand; `:970` needed nothing (only ungated `.omc/specs/` cites it) and the line is byte-identical |
| print_channel | `tests/print_mode_child.py` is a NEW `tests/` module — re-check anything enumerating `tests/*.py` | **CHECKED** — `tests/packaging_gate/` and `tests/test_citation_drift.py` both green in all three runs; the file is `ruff format --check` clean and carries no citations |
| rpc | stale `rpc_channel.py:387` → `print_channel.py:1106-1113` | **RELOCATED to `:1224-1231` and hand-verified correct**, §3 |
| rpc | `rpc_client.py` anchors below `:347` shifted +18 | **RELOCATED** by `--fix`; `rpc_client.py:1073`→`:1091`, `:989-992`→`:1007-1010`, none stuck |
| docs | "whether the four sites actually landed" — the ADR/README/CHANGELOG sentences depend on it | **ALL FOUR LANDED**, evidence in the four rows above plus `print_mode.py:183 signal.SIGBREAK`. The docs lane's three sentences may stand |
| docs | `print_channel.py:973` and `reaper.py:222`/`:371` still future-tense "#220" | **NOW PAST TENSE** — `grep -rn "is #220, not this\|adopting it here is #220\|Until #220 lands" packages/` → no hits. `print_mode.py:35` reads "Until #220 this whole step was switched off", which is past tense and correct |
| docs | #215 comment, board move, `handoff-220-2026-09-05.md` | **NOT DONE** — process artefacts, outside every lane; §H.6/§H.7/§H.8 remain too |
| print_mode | `envelope.py:42-47` / `test_envelope.py:23-33` now describe history as present behaviour | **CONFIRMED WRONG, NOT FIXED** — see §8 |

## 8. Findings — reported, not fixed (outside this lane)

1. **`envelope.py:42-51` and `tests/agents_ext/test_envelope.py:23-33` state a falsehood in the present
   tense.** Both say `_signal_cleanup_and_exit` *"calls `sys.exit(128 + sig)` from inside a coroutine, so
   asyncio prints 'Task exception was never retrieved' plus a `SystemExit(143)` traceback on **every
   single cooperative kill**"*. After §A.3(b) it does not — measured in this very suite, the real child
   returns 143 with a clean tail. Read and confirmed against the current tree; both files' only diffs in
   this branch are citation relocations, so the comments are untouched. The print_mode lane raised it and
   correctly did not touch them. `_STDERR_NOISE_RE` itself should STAY (older children and any other
   task-borne exception still produce the shape); it is the two comments and `_SIGTERM_NOISE`'s framing
   that need a "was true until #220 / synthetic historical sample". **Recommend the fix lane take it.**
2. **`tests/test_citation_drift.py::test_a_continuation_does_not_steal_a_file_named_after_it` is
   structurally fragile** — see §4. Fixed for today; the shape will recur.
3. **`print_channel.py:970`'s byte-identical requirement (§F, §B.2) is unnecessary** as things stand: the
   anchor exists in the lock but nothing GATED cites it. Worth noting so a future change is not
   constrained by a rule that no gate enforces.

## 9. Step 5 — strays

`pgrep -fl "sleep 1|python.*stub|aelix" | grep -v pgrep`, before and after the three runs and the
mutation run. **Output byte-identical, same PIDs both times — zero survivors from the suite:**

```
5167, 9547, 13543, 15043, 20955, 84318   npm exec @upstash/context7-mcp      (the box's own MCP servers)
78989, 96039                             ~/.local/share/uv/tools/aelix/…/aelix (the owner's installed TUI)
```

None is a test child: no `-m aelix_coding_agent`, no `sleep`, no stub interpreter, and every PID predates
this lane's first command.

## 10. Step 6 — tree state

```
$ git -C /tmp/wt-220 status --porcelain
 M CHANGELOG.md
 M README.ko.md
 M README.md
 M SLICE-STATUS.md
 M citations.lock.json
 M docs/decisions/0238-the-kill-reached-the-child-and-the-tree-is-what-had-to-die.md
 M docs/decisions/README.md
 M packages/aelix-ai/src/aelix_ai/utils/_process_tree.py
 M packages/aelix-coding-agent/src/aelix_agents/batch.py
 M packages/aelix-coding-agent/src/aelix_agents/chain.py
 M packages/aelix-coding-agent/src/aelix_agents/consent.py
 M packages/aelix-coding-agent/src/aelix_agents/envelope.py
 M packages/aelix-coding-agent/src/aelix_agents/print_channel.py
 M packages/aelix-coding-agent/src/aelix_agents/reaper.py
 M packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py
 M packages/aelix-coding-agent/src/aelix_agents/runtime.py
 M packages/aelix-coding-agent/src/aelix_agents/stream.py
 M packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py
 M packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py
 M tests/agents_ext/test_batch_consent.py
 M tests/agents_ext/test_batch_executor.py
 M tests/agents_ext/test_chain.py
 M tests/agents_ext/test_child_argv_contract.py
 M tests/agents_ext/test_envelope.py
 M tests/agents_ext/test_print_channel_spawn.py
 M tests/agents_ext/test_reduce_consumes_real_print_mode_output.py
 M tests/agents_ext/test_rpc_channel.py
 M tests/agents_ext/test_spawn_consent.py
 M tests/agents_ext/test_stream_reduce.py
 M tests/cli/test_print_mode_json_contract.py
 M tests/conftest.py
 M tests/test_citation_drift.py
?? .omc/specs/220-design-2026-09-05.md
?? .omc/specs/220-progress-2026-09-05.md
?? .omc/specs/220-progress-lane-docs.md
?? .omc/specs/220-progress-lane-gate.md
?? .omc/specs/220-progress-lane-print_channel.md
?? .omc/specs/220-progress-lane-print_mode.md
?? .omc/specs/220-progress-lane-rpc.md
?? .omc/specs/220-progress-lane-reaper.md
?? tests/agents_ext/test_reaper_tree_win32.py
?? tests/cli/test_print_mode_signal_handlers.py
?? tests/print_mode_child.py
```

```
$ git -C /tmp/wt-220 diff --stat
 CHANGELOG.md                                       |   20 +-
 README.ko.md                                       |   24 +-
 README.md                                          |   28 +-
 SLICE-STATUS.md                                    |   50 +-
 citations.lock.json                                |  101 +-
 ...ed-the-child-and-the-tree-is-what-had-to-die.md |   79 +-
 docs/decisions/README.md                           |    2 +-
 .../aelix-ai/src/aelix_ai/utils/_process_tree.py   |   53 +-
 .../aelix-coding-agent/src/aelix_agents/batch.py   |   16 +-
 .../aelix-coding-agent/src/aelix_agents/chain.py   |    4 +-
 .../aelix-coding-agent/src/aelix_agents/consent.py |    2 +-
 .../src/aelix_agents/envelope.py                   |    2 +-
 .../src/aelix_agents/print_channel.py              |  192 +++-
 .../aelix-coding-agent/src/aelix_agents/reaper.py  |  188 +++-
 .../src/aelix_agents/rpc_channel.py                |   55 +-
 .../aelix-coding-agent/src/aelix_agents/runtime.py |    8 +-
 .../aelix-coding-agent/src/aelix_agents/stream.py  |    6 +-
 .../src/aelix_coding_agent/modes/print_mode.py     |  156 ++-
 .../src/aelix_coding_agent/rpc/rpc_client.py       |   18 +
 tests/agents_ext/test_batch_consent.py             |    2 +-
 tests/agents_ext/test_batch_executor.py            |   20 +-
 tests/agents_ext/test_chain.py                     |    2 +-
 tests/agents_ext/test_child_argv_contract.py       |    2 +-
 tests/agents_ext/test_envelope.py                  |    2 +-
 tests/agents_ext/test_print_channel_spawn.py       | 1018 ++++++++++++++++++--
 tests/agents_ext/test_reduce_consumes_real_print_mode_output.py |  4 +-
 tests/agents_ext/test_rpc_channel.py               |  401 ++++++++
 tests/agents_ext/test_spawn_consent.py             |    2 +-
 tests/agents_ext/test_stream_reduce.py             |    6 +-
 tests/cli/test_print_mode_json_contract.py         |  120 +--
 tests/conftest.py                                  |    2 +-
 tests/test_citation_drift.py                       |    4 +-
 32 files changed, 2218 insertions(+), 371 deletions(-)
```

Nine of the 32 modified files (`chain`, `consent`, `envelope`, `runtime`, `stream`, `test_batch_consent`,
`test_chain`, `test_child_argv_contract`, `test_spawn_consent`, plus `conftest`, `test_stream_reduce`,
`test_reduce_consumes_real_print_mode_output`) are citation-relocation-only; they are in the diff because
of §F step 2, not because a lane edited them. `git add` pathspec (§H.8) must include `citations.lock.json`
and all of them.

## 11. What this lane could NOT verify

- **Everything win32.** Nothing on this box executed `CTRL_BREAK_EVENT`, `taskkill.exe`,
  `TerminateJobObject` or `IsProcessInJob`. Every win32 assertion in the suite runs against injected
  platform / recording stubs and proves DISPATCH only. §H.7's `windows-latest` leg is the measurement,
  and 149, the `hard_kill` wall time, the soft-leg latency and job membership are all still **[U]**.
- **§H.6(A)** — Q1's live survivor-recovery (a real delegation's non-`setsid` grandchild dying to the
  group kill). The "before" half is measured on `main` (`220-progress` §2: the grandchild SURVIVED); the
  "after" half under `/tmp/wt-220/.venv` was NOT run by this lane. Q1's coverage therefore remains
  site-identified **[M]** / survivor-recovery **[U]**, exactly as §J's owner's-absence decision 3
  anticipated.
- **§H.6(C)** — the rpc driver script (`RpcChannel` is not wired into the product).
- **§H.0 / §H.8** — the Codex review debt record, the #215 comment, the board move, the
  `handoff-220-2026-09-05.md`, and the commit. None is this lane's.
- (§H.5's full suite WAS run — see §12; it is no longer an unverified item.)
- **`ProcessTree.attach` raising in the product** and `_attach_win32` falling back — inherited unverified
  from the print-channel lane; not constructible here.


## 12. Step §H.5 — the full suite, and a worktree environment gap it exposed

Not in this lane's numbered brief, but spec §H.5 asks for it, so it was run.

**First attempt — collection error, and it is NOT this branch's code:**

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q
tests/server/test_server.py:26: in <module>
    from aelix_server import ServerConfig, create_app
E   ModuleNotFoundError: No module named 'aelix_server'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

**Second attempt, `--ignore=tests/server` — the same cause, one test deeper:**

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q --ignore=tests/server
tests/cli/test_stdio_encoding_win32.py:449: ModuleNotFoundError: No module named 'aelix_server'
FAILED tests/cli/test_stdio_encoding_win32.py::test_server_main_sync_hardens_before_boot
1 failed, 10119 passed, 12 skipped, 12 warnings in 289.74s (0:04:49)
```

**Diagnosis — an environment gap in the worktree recipe, reproducible on `main`.**

- Both files are byte-identical to `main`: `git diff --stat main -- tests/cli/test_stdio_encoding_win32.py
  tests/server/` is empty. So is `git diff --stat main -- pyproject.toml packages/aelix-server uv.lock`.
- `aelix-server` is a `[tool.uv.workspace]` member (`pyproject.toml:196`) and has a
  `[tool.uv.sources]` entry (`:202`), **but it is a dependency of neither the root project
  (`:31-35`) nor the `dev` group (`:205-217`)**. A plain `uv sync` therefore does not install it — and
  `uv sync` is exactly what CLAUDE.md rule 3's worktree recipe runs
  (`git worktree add … && (cd /tmp/wt-<n> && uv sync)`).
- Measured: in `/tmp/wt-220/.venv`, `import aelix_server` → `ModuleNotFoundError`; in the main checkout's
  `/Users/handochan/dev/aelix-ai/.venv`, it imports. `uv sync` in the worktree resolved 79 packages,
  changed nothing, and left it still missing; **`uv sync --all-packages` installed it**
  (`+ aelix-server==0.1.0b1 (from file:///private/tmp/wt-220/packages/aelix-server)` plus fastapi,
  uvloop, httptools, watchfiles, annotated-doc).
- The failing test's own comment is the thing that is wrong: *"aelix-server is a workspace member and is
  **always installed**, so importorskip here would let the guard vanish silently — the failure mode issue
  #203 is about"* (`tests/cli/test_stdio_encoding_win32.py:445-447`). "Always installed" is false for
  every `uv sync`-created worktree, which is how this repo does parallel work.

**Third attempt, after `uv sync --all-packages` — clean:**

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q
10137 passed, 12 skipped, 12 warnings in 288.94s (0:04:48)          (exit 0)
```

Zero failures, zero errors across the whole tree.

**Re-verification after the venv changed** (the sync pulled in `uvloop`, which could in principle change
asyncio behaviour). All four gates re-run on the new venv:

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q <the §4 targeted set>
2065 passed, 12 skipped, 2 warnings in 93.49s (0:01:33)     — identical to runs 1-3
$ .venv/bin/ruff check .                       All checks passed!
$ .venv/bin/python scripts/check_types.py      PASS: type gate clean, narrowing spike still inverted
$ .venv/bin/python scripts/check_citations.py --check
                                               citations OK — 850 gated, none drifted.
```

`pgrep` after everything: the same eight PIDs as §9, unchanged. No strays.

**Recorded for the fix lane / the handoff, NOT fixed here (it is not #220's):** either add `aelix-server`
to the `dev` dependency group so `uv sync` installs it, or change CLAUDE.md rule 3's worktree recipe to
`uv sync --all-packages`. Until one of those happens, **every fresh worktree in this repo cannot run the
full suite**, and the two tests that notice fail in a way that looks like a code defect. The tracked tree
was not touched to work around it; only the worktree's `.venv` was synced.
