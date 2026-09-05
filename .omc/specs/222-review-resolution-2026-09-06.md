# Pre-merge review resolution — #222 (`fix/222-bash-tool-process-tree`, base `main` 92aba6d)

## 1. FIX BEFORE MERGE

### F1 (BLOCKING) — the abort watcher stays armed through the post-exit drain
Covers **WIN32-1** and **REGRESS-1** (one defect, two lenses: win32 blast radius / RPC blast radius).

`/tmp/wt-222/packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py:518-530` — the drain is the innermost `finally` and the watcher-cancel `finally` is two levels out, so `_watch_signal` (`bash.py:436-443`) can fire during the drain, set `_signal_aborted`, and call `_end_the_tree`. `main` cancels+awaits the watcher *before* `await drain_task`. Measured A/B (abort at t=1.0 s into an rc-0 command that backgrounded a 20 s helper): main `exit_code=0`, elapsed 20.02 s, helper alive; branch `exit_code=None`, elapsed 1.01 s, helper killed, output lost, `is_error=True` / "Command aborted" (`bash.py:536-537`, `:813`, `:854`). This kills exactly the process `kill_on_close=False` exists to keep (on win32 `hard_kill` = `taskkill /T /F` + `TerminateJobObject`, which reaches it even through its own process group) and contradicts ADR-0238:721-724 and design §A.6 in so many words.

**Change:** swap the two `finally` blocks so the teardown order is `watcher cancel+await → drain → detach → close` (= `main`, = resolution-table row ASYNC-4; the design's only stated constraint, watcher-before-`tree.close()`, is still satisfied):

```
try:                                  # finally: tree.close()            (:531-534)
  try:                                # finally: reader.detach(); delivering = False
    try:                              # finally: await _drain_to_the_end()
      try:                            # finally: watcher_task.cancel(); await watcher_task
        exit_code = await _wait()
      except asyncio.CancelledError:  # unchanged: _mark_the_kill(); _end_the_tree(...); raise
```
i.e. move the current `:523-530` block inward of `:518-519`, leaving `:520-522` and `:531-534` where they are. Do **not** take the "document the new behaviour instead" option: it pre-decides open issue #230 in the kill direction on a leg nobody chose.

**Proof:** new case in `/tmp/wt-222/tests/tools/test_bash_tool_containment.py`, next to case 8 (`:689`) and reusing `ROOT_THAT_BACKGROUNDS_A_HELPER` (`:203`, three stdio DEVNULL) — root exits 0 after backgrounding a 2 s helper, `signal.abort()` fires ~0.3 s into the exit-path drain; assert `result.exit_code == 0`, `result.timed_out is False`, `probe_state(helper) == STATE_ALIVE` on POSIX, and (win32) helper alive after `close()`. Must be red before the reorder, green after. `tests/tools/test_bash_tool_containment.py` (14 passed) plus `tests/tools tests/process_tree` (357 passed) re-run after.

### F2 (MAJOR) — three new cases have a 0.5 s wall-clock budget smaller than pwsh startup on the gating leg
`/tmp/wt-222/tests/tools/test_bash_tool_containment.py:502` (case 4, timeout leg), `:848` (case 10), `:910` (case 11). The clock starts before the shell runs a line (`bash.py:290-291` spawns `[shell.path, flag, command]`; `bash.py:421` passes `timeout` to `proc.wait`). Measured on darwin: the same chain announces at 0.048-0.054 s under `/bin/bash` but 0.281-0.296 s under pwsh; the file's own text at `:102` and `:407` puts runner pwsh startup at **0.5-0.57 s**, and windows adds python startup + `import aelix_ai.utils._process_tree` + a `Popen` + an `os.replace` on top. Reproduced red by emulating 0.5 s of shell startup: `3 failed, 11 passed` — exactly those three ids, all with `AssertionError: the root never announced its tree — the case measured nothing`.

**Change:**
- `:502` → `timeout = 2.0 if leg == "timeout" else None`; the two `_bound(0.5)` at `:512` and `:533` become `_bound(2.0)` (the formula is `base + KILL_DRAIN + slack`, nothing else moves).
- `:910` → `timeout=2.0`; `:906` watchdog `_bound(2.0) + 5.0`.
- `:848` → replace `await asyncio.sleep(0.5)` with `await _await_pids(registrar, "a cancel during the drain")` (`:314`) plus a short settle (`await asyncio.sleep(0.2)`), so the cancel lands after the announcement rather than at a fixed wall clock — the pattern cases 2/3 already use (`:422`, `:454`).

**Proof:** the three ids stay green under the 0.5 s shell-startup emulation (`SHELL=<slow shell wrapper>`), and the full file still passes with no id lost.

### F3 (MAJOR) — CHANGELOG `Removed` entry states an importer/reference count the diff itself refutes
`/tmp/wt-222/CHANGELOG.md:308-311`: "Its last two importers … and nothing else in the tree, in the tests or in the docs referred to the shim." Measured — `git grep -n "tools._process_tree" 92aba6d` gives **three** importers (`tools/bash.py:27`, `tools/_subprocess.py:36`, `tests/tools/test_process_tree_win32.py:47-48`) and prose references at `aelix_agents/reaper.py:107`, `docs/decisions/0238-…md:93`, `SLICE-STATUS.md:39` — all three rewritten by this diff. The design was careful ("the only NON-TEST importers", design §A.1); the CHANGELOG dropped the qualifier.

**Change:** rewrite `:308-311` to "Its last three importers — the `bash` tool, the `rg`/`fd` helper and `tests/tools/test_process_tree_win32.py` — name the primitive directly now (#222); the remaining references were prose (`reaper.py`, ADR-0238, `SLICE-STATUS.md`) and moved with it."

**Proof:** `git grep -n "tools._process_tree" 92aba6d` (three importers) vs `git grep -n "tools._process_tree" HEAD` (prose only) + `scripts/check_citations.py`.

## 2. REFUTED — settled, do not re-raise

- **POSIX-1** (only `CancelledError` runs the ladder out of `await _wait()`): identical shape on `main` (`except asyncio.CancelledError` + unbounded `await drain_task`); the only in-product trigger, `RuntimeError("can't start new thread")`, is preceded by the already-belted `reader.start()` at `bash.py:386-398`; not a regression, and remeasured the tree is *not* leaked (exception arrives late, `detach`/`close` still run).
- **ASYNC-1** (`suppress(CancelledError, Exception)` around `await watcher_task` can swallow an outer cancel): block is byte-identical to `main` including the comment; measured LOST 3/3 on `main` too, and the window is statistically indistinguishable (6/1200 vs 9/1200, Fisher p≈0.6) — pre-existing wart, not this diff's.
- **ASYNC-2** (a loop-thread `KeyboardInterrupt` skips the ladder): refuted by measurement — a real SIGINT reaches `await _wait()` as `CancelledError` (`exec` frame saw `CancelledError` at 1.006 s, no survivors); the cited probe injected `KeyboardInterrupt` inside the `to_thread` worker, which no signal can do.
- **TESTS-1** (mutation C.5(d) `kill_on_close=True` survives every test): C.2-5 asserts `probe_state(helper) == STATE_ALIVE` unconditionally (`test_bash_tool_containment.py:577`) and the whole suite gates on `windows-latest`, where `close()` is not a no-op — the mutant goes red on a merge-gating leg, exactly as design `:352-353` and §J-5 say. Residue is a stale parenthetical in the design doc (see M-14).
- **DOCS-3** (CHANGELOG says pre-#222 Windows had "a plain process group"): the sentence withholds "of its own" from the Windows leg on purpose and "plain" contrasts with the new job object; a child spawned without `CREATE_NEW_PROCESS_GROUP` *is* in a plain inherited group, and `CHANGELOG.md:414-421` spells out that containment there did not work. Wording, not falsehood.
- **REGRESS-2** (`_drain_to_the_end` samples `exited_at` once, so a late kill never arms the bound): accepted by design §A.6/§H and by resolution row SCOPE-12; ADR-0238:713-724 already carries the qualification the finding asked for. (F1 fixes the *reachability* of the abort in that window; the sampling itself stays as designed.)

## 3. MINOR — verdicts

- **M-1 DOCS-1 / WIN32-4** (ADR:678-682 generalises `REAP_GRACE_SECONDS`/`INTERRUPT_REAP_SECONDS` onto `run_cancellable`, which uses a flat `wait_for(proc.wait(), 2)` at `_subprocess.py:157,169` and has no abort leg): **FIX-NOW** — split the sentence per site; the code is right, the narrative is wrong.
- **M-2 POSIX-2** (`_mark_the_kill()` stamps before `_end_the_tree`, so the kill's own cost eats the 0.1 s idle window; inert on POSIX at 1.6 ms, collapses to zero arms on win32 where `taskkill.exe` is bounded at 5 s): **FIX-NOW** — move the stamp after the ladder at all three legs (`bash.py:422/429`, `442/443`, `515/516`), matching `_process_tree.py:1574-1586`; re-run the containment file.
- **M-3 POSIX-3** (an `on_data` that raises now dies in `loop.call_exception_handler` instead of reaching the caller; unreachable in-repo — all three callers pass `chunks.append`): **FIX-NOW** as documentation only — one sentence in the `_deliver` block (`bash.py:335-360`) and the `BashOperations` Protocol stating the contract. No behaviour change pre-merge.
- **M-4 WIN32-3 / TESTS-4** (`tests/tools/test_bash_tool.py:584` leaks a 30 s pipe-holding python on the windows leg — pwsh does not exec-replace, the recording API kills nothing, `proc.kill()` reaches pwsh alone; same shape for case 12's 60 s sleeper at `test_bash_tool_containment.py:952`): **FIX-NOW** — shorten the sleep to ~2 s and/or register through the `strays` fixture; a stray on the gating leg is cheap to prevent and expensive to debug.
- **M-5 WIN32-5** (ADR:212-214 still says a `!command` shell keeps its session/tty via `process_group=0`; `!command` goes through this `exec`, i.e. `new_session=True`, and the same file's #222 amendment at :750-756 says the opposite): **FIX-NOW** — drop `!command` from that clause, leave the hook shell.
- **M-6 WIN32-6 / (dup of nothing)** (`_ReadState` docstring at `_process_tree.py:895-899` still says `exited_at` is written "ONLY by `run_contained`"): **FIX-NOW** — one sentence; the bash site writes it from three legs.
- **M-7 ASYNC-3** (`run_cancellable` has no `except BaseException` belt; a foreign exception between attach and return leaves the `rg`/`fd` child running — measured): **LATER** — pre-existing on `main`, residue is a short-lived leaked child. File with the POSIX-1/ASYNC-2 residue as one "adopt the primitive's BaseException belt at both async sites" issue.
- **M-8 ASYNC-4** (`_subprocess.py:139` suppresses `ProcessLookupError` where the primitive suppresses `OSError`; EPERM on a recycled pid would replace the `CancelledError` being re-raised): **FIX-NOW** — `contextlib.suppress(OSError)`, one word, nothing lost.
- **M-9 ASYNC-5** (`_end_the_tree` docstring's bpo-38630 rationale is refuted at the new call site — measured 200/200 that the belt does issue a second `os.kill` while a `to_thread(proc.wait)` worker holds `_waitpid_lock`; the behaviour is still safe, the stated reason is not): **FIX-NOW** — rewrite the POSIX sentence at `_process_tree.py:1165` to design §A.4's invariant.
- **M-10 TESTS-2** (case 9's "what this case did not kill" paragraph is silent on C.5(l2), and the lane's reason for it is wrong — case 9 is on the success path and never reaches `_drain_past_the_kill`; measured: injecting an assert there reddens 7 ids, not case 9): **FIX-NOW** — one honest sentence in the docstring at `tests/tools/test_bash_tool_containment.py:744`.
- **M-11 TESTS-3** (`tests/tools/test_subprocess_helper.py:429-431/438` and `:474/479` use the same value for the watchdog and the asserted elapsed, so the timing assertion can never fire): **FIX-NOW** — `+ 5.0` on the two `wait_for` timeouts, matching the sibling file.
- **M-12 DOCS-4** ("attach … in the statement after the spawn" — true at `run_cancellable`, six statements early at `bash.py:288-367`): **FIX-NOW** — say "before the first `await`" at ADR:678 and :359-360, `docs/decisions/README.md:144`, and `test_bash_tool_containment.py:936`.
- **M-13 DOCS-5** (`_process_tree.py:450-457` "#222 adds three more, all in `tools/bash.py`" — `run_cancellable`'s two legs also reach `_taskkill_tree` on the loop thread): **FIX-NOW** — five, at two files.
- **M-14 DOCS-6 / REGRESS-3** (`_start_reader` in `__all__` under a comment that says the list is what a second caller shares; nothing imports it): **FIX-NOW** — drop the entry (the design's rule was "exactly what the sites import"). Bundle the stale design parenthetical from TESTS-1 (`222-design-2026-09-05.md:353`, "C.2-7's spy asserts the attach kwargs on both" — C.2-7 asserts no kwargs) with this edit.
- **M-15 DOCS-7** (`SLICE-STATUS.md:125` "instead of the `taskkill /T` this slice's W3 shipped" — `hard_kill` still runs `taskkill /T /F` first): **FIX-NOW** — add "alone", matching `SLICE-STATUS.md:56` and ADR:361.
- **M-16 DOCS-8** (`CHANGELOG.md:454`, ADR:760, `bash.py:306` quote a git string git does not print; measured actual: "Aborting commit due to empty commit message."): **FIX-NOW** — quote the real string or drop the quotes.
- **M-17 ASYNC-1 residue** (teardown `suppress` cannot distinguish the watcher's cancellation from the turn's; ~0.5% of randomly-timed aborts return a normal `ExecExitResult`): **LATER** — file against `main`'s line, fix is `asyncio.wait([watcher_task])`. Not this branch's.
- **M-18 TESTS-1 residue** (add the two-line attach-kwarg assertion to `spy_attach` / `_AttachRecorder` so `kill_on_close=False` is pinned on POSIX too, as at the rpc/print/hooks/oauth sites): **LATER** — nice-to-have, the win32 leg already gates it.

Nothing on this list is DROP.

## 4. VERDICT: **FIX_FIRST**

Orchestrator, in order:

1. **F1** — reorder the teardown in `bash.py:518-530` (watcher cancel+await inside the drain's `finally`), and add the abort-during-exit-drain case to `tests/tools/test_bash_tool_containment.py`. Confirm the new case is red before the reorder and green after.
2. **F2** — raise case 4-timeout (`:502`) and case 11 (`:910`) to `timeout=2.0` with matching `_bound(2.0)`, and replace case 10's blind `sleep(0.5)` (`:848`) with `_await_pids` + settle. Verify green under a 0.5 s shell-startup emulation.
3. **M-2, M-8** — the two code MINORs on the kill path (`_mark_the_kill` after the ladder at all three legs; `suppress(OSError)` in `_subprocess.py:139`). Do these in the same pass as F1 so the suite is re-run once.
4. **M-4** — shorten/register the two long-lived win32 test children (`test_bash_tool.py:584`, containment case 12).
5. **F3, M-1, M-5, M-6, M-9, M-10, M-11, M-12, M-13, M-14, M-15, M-16** — the documentation and docstring pass (CHANGELOG, ADR-0238, `docs/decisions/README.md`, `SLICE-STATUS.md`, `_process_tree.py`, `bash.py`, the two test docstrings, `222-design-2026-09-05.md`), plus M-3's contract sentence.
6. Re-run the full gate from `/tmp/wt-222`: `.venv/bin/python -m pytest -p no:cacheprovider -q tests/tools tests/process_tree` (expect ≥357 passed, +1 for the new case), the caller suites (`tests/cli tests/rpc tests/agents_ext tests/tui`), `ruff`, `pyright`, `scripts/check_citations.py`, `tests/test_citation_drift.py`.
7. Push and take the **windows leg** on the amended commit — §J-8 (the announce time inside the real pwsh→python→import→spawn chain) is still unmeasured and F2 is only conditionally proven without it. Record the leg's numbers in `.omc/specs/222-progress-2026-09-05.md` §7.
8. File two follow-ups before closing: **M-7 + POSIX-1/ASYNC-2 residue** (a `BaseException` belt at both async sites) and **M-17** (cancellation-faithful `await watcher_task`); note **M-18** on the #230 issue.

Merge after step 7 is green. Do not merge on the POSIX legs alone.