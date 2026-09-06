# #234 design — revision 2 resolution table

Base: `.omc/specs/234-design-2026-09-06.md` (revision 1), `main` `b94a6db`. Four lenses filed 13
findings; every one survived its refuter. **7 UPHELD** (2 BLOCKING, 5 MAJOR) and **6 PARTIAL**
(MINOR, "apply if cheap" — all applied), **0 REFUTED**. Two refuters went further than the finding
they were sent to check and disproved the finding's *own proposed remedy*; those are marked
"UPHELD+" and are the reason revision 2 is a larger change than the critique asked for.

Revision 1 was measurably wrong about four things: the retrieval line (ASYNC-1), the timeout leg
(ASYNC-2), who can reach this window (SCOPE-1, SCOPE-2), and the loss numbers (SCOPE-4). The first
three were **already written into `/tmp/wt-234`'s `bash.py`** by an implementer lane while the
critique ran — this revision is an edit to the working tree, not only to the spec.

---

## The findings

| id | lens | sev | verdict | one-line evidence | disposition |
| --- | --- | --- | --- | --- | --- |
| **ASYNC-1** | asyncio-semantics | BLOCKING | **UPHELD+** | `Task.cancel()` clears `_log_traceback` only for an *already-failed* watcher; a foreign `signal.wait()` failing *during* its own cancel fails after that `cancel()` and `Future.set_exception` re-arms it — measured `False→False→True` on 3.11.15/3.12.13/3.13.13 and at the real site, where bare `wait` REPORTS what `main` retrieved. The critique's own remedy (retrieval *after* the wait) still leaks under a concurrent cancel. | §A bullet 3 rewritten: the retrieval is **adopted**, in a `finally`. §B's two false comment sentences deleted. §C.2 re-aimed and given the two arms that discriminate. §G's first risk restated. |
| **ASYNC-2** | asyncio-semantics | MAJOR | **UPHELD** | Three dispositions reach the teardown, not one. TIMEOUT leg (root sleeps 30 s, `timeout=0.3`): `main` returns `exit_code=None, timed_out=True` at 0.305 s, the fix **CANCELLED** at 0.310 s — the timeout label is discarded. Helper `alive_after_exec` `True` on NORMAL, **`False`** on TIMEOUT. ABORT leg measured **SAME** both sides. | §A gains the three-leg table; §B's comment says "the root is gone by now (it exited, or the timeout leg killed it)"; §D.2's blast-radius sentences scoped per leg; §D.3 widened; §C.1 parametrised over both changing legs. |
| **TESTS-1** | tests-determinism | MAJOR | **UPHELD** | Instrumented teardown over `tests/tools`: 9 executions of the changed line, **1** propagating cancel — C.1's `echo ok`, no tree, no helper, no holder. #230's alternative policy (`_end_the_tree` on that cancel) leaves the post-commit suite **262-green**. The proposed real-children case: FIX 2.036 s pass, `main` DID NOT RAISE, that mutation `helper … 'gone' == 'alive'`. | New **C.4** in `test_bash_tool_containment.py` (`=== 8c ===`), named as a second RED-first proof; the ADR Consequences numbers now cite it instead of a scratchpad script; the #230 mutation added to the table with the warning not to use an unconditional `_end_the_tree`. |
| **SCOPE-1** | scope-and-docs | BLOCKING | **UPHELD** | Live TUI in tmux: 21/21 `!command` execs `signal=None`, `watcher_created=false`; a model-issued bash call in a real TUI turn likewise. `!sleep 5` + Esc at ~1 s → 5.010 s, `exit_code=0`, nothing printed. §E step (c) could only ever report "no loss seen", before and after. | §E rewritten: step (c) and the "the Esc path is the only way a human reaches this window" sentence deleted; step (a)'s expected observation **corrected**, not softened; the true statement of reach added; §G gains the Reach bullet. |
| **SCOPE-2** | scope-and-docs | BLOCKING | **UPHELD** | A/B on `main`, 3 seeds × 300 rounds: `signal=None` **0 LOST of 524** requested cancels; `signal=AbortSignal` **14 of 388**. `repl.py:62` hard-codes `signal=None`; `agent_loop` is called with no `signal=` at both production sites; the repo documents the dead `ctx.signal` twice. | §B/§D.1 say "an EXTERNAL cancellation of the task running `exec`", not "the turn's"; ADR bullet and Consequences retitled and given the reachability paragraph; **CHANGELOG rewritten contributor-scoped** (the old headline promised a TUI behaviour change measurement does not support); §0/§H rows labelled "abort signal supplied". |
| **SCOPE-3** | scope-and-docs | MAJOR | **UPHELD** | §B shifts `bash.py` past two locked citations. `--check` after revision 2's §B (+36): **2 drifted, exit 1** (`tool.py:581` `bash.py:950`→986, `permission.py:678` `793-795`→829-831); after §B+§D.1 (+40): →990 and →833-835. `tests/test_citation_drift.py` **1 failed** in that state. `--fix` converges (`488 anchors / 851 gated`) and puts 4 files in the commit. | §F reordered — `--fix` **first**, after *both* source edits, then `--check`, then the drift test, then pytest; §F's commit sentence names `citations.lock.json` + the two citing product files and says why; §H row 14 replaced with the measured result; §G notes the next lander must re-run `--fix` after the rebase. |
| **SCOPE-4** | scope-and-docs | MAJOR | **UPHELD** | Ten runs of the design's own probe, two seeds: LOST **3–7** per 300 rounds (48/1967 requested = 2.44 %). The design's "9 of 192 (4.69 %)" never recurred and is above the whole observed range; `hi = median(exec)×1.6` is re-measured every run, so the seed cannot pin the outcome. `probe_a` runs **six** rounds, all RED — the shipped comment's "3/3" is the #222 review's number. "600 aborts" is 600 *rounds* = 380 requested + 6 aimed. | §B comment, ADR bullet and CHANGELOG re-denominated ("of the cancels actually delivered to a still-running `exec`"); "3/3"→"6 of 6"; "600"→"380"; §0 gains the not-seed-reproducible sentence; §G gains the sampling risk. Numbers stay **in** the comment — house style carries measurements there (the refuter's correction to the finding). |
| **ASYNC-3** | asyncio-semantics | MINOR | PARTIAL | The cited "3 double-cancel rounds" cancel twice inside one synchronous stretch: one cancellation requested twice, not a second cancel at a second suspension point. | §G's second-cancel bullet says exactly what was measured and names the suspension point that would matter (the outer `finally`'s drain), with C.4 as the case in that shape. |
| **TESTS-2** | tests-determinism | MINOR | PARTIAL | C.1's inline hook exercises only the `fut_waiter.cancel()`→`True` delivery path; the `→False` / `_must_cancel` path is deterministic via a DOUBLE `loop.call_soon` (a single one still lands on `asyncio.wait`'s pending waiter — measured post-implementation) and equally lost on `main`. | Third parametrisation added to C.1, corrected in pre-merge review to `after_completion` (double hop) with a `delivered_via` branch assert. |
| **TESTS-3** | tests-determinism | MINOR | PARTIAL | C.1 cannot tell §B from the near-miss the issue's TO DO invites (drop the `suppress`, keep a bare `await`): in C.1's shape the watcher ends CANCELLED, so a bare `await` re-raises the *watcher's* cancellation and the task ends cancelled too. | The mutation table now says which cases kill that variant (`test_bash_exec_abort_with_process_already_gone_still_returns` and C.2), so it stops reading as though C.1 pinned the choice of `asyncio.wait`. |
| **TESTS-4** | tests-determinism | MINOR | PARTIAL | C.2's blindness control is order-sensitive: run before the `exec` call it dirties the shared handler buffer and the green half fails on `main` and on the fix alike. | §C.2 states the ordering explicitly ("the control runs LAST") and requires it as a comment in the case. |
| **TESTS-5** | tests-determinism | MINOR | PARTIAL | `await asyncio.wait([t])` always yields a loop turn, including for an already-done `t`, where `await t` does not suspend at all (`yielded_to_loop` `False`→`True`). | §A states it as the one scheduling change no case covers, with the ABORT row as the evidence that it is inert. |
| **SCOPE-5** | scope-and-docs | MINOR | PARTIAL | §F ended at "read the `UserWarning` out of the `-q` log"; the two rules that actually end an issue here (board + docs before close; ff-merge + handoff) were unrepresented, and §G created a follow-up with no owner or ordering. | §F gains the closure step; the OAuth follow-up bullet in §G is now "file it before the session handoff". |

---

## Conflicts between refuters, settled

- **Is the RPC dispatch task cancellable from outside?** SCOPE-1 said no ("`rpc_mode` gathers
  `pending_command_tasks` rather than cancelling them" — read, not measured). SCOPE-2 **measured**
  that `asyncio.gather(..., return_exceptions=True)` still propagates an *outer* cancel to its
  children (`child.cancelled()=True`); `return_exceptions=True` only stops a child's *failure* from
  cancelling siblings. **SCOPE-2 wins**; §G carries its version, and SCOPE-1's parenthetical is on the
  do-not-re-raise list.
- **Which loss numbers go into the permanent surfaces?** SCOPE-4's aggregate is from the design's own
  `probe_b_random.py` (ten runs, 48/1967 = 2.44 %); SCOPE-2's is from `probe_ab_signal.py` (14/388
  with a signal, 0/524 without). They are different probes and do not conflict — SCOPE-4's aggregate
  is the headline rate and SCOPE-2's A/B is the reach contrast. Both are in §0 and §H, each labelled
  with its probe.

## Re-measured in this synthesis lane (isolated copy, `…/scratchpad/234/synth/`)

Because §B grew in revision 2, three of the refuters' numbers no longer describe the block being
shipped and were taken again. The worktree was **not** touched (`git status --porcelain` empty, `bash.py`
still `7c5e0e94…`); everything ran in a `tar`-copy with `PYTHONPATH` shadowing the editable `.pth`.

| what | result |
| --- | --- |
| the four shapes × three watcher behaviours × cancel on/off, stdlib, **3.11.15 and 3.12.13** | `main` silent; bare `wait` REPORTED in both `during-raises` arms; retrieval-after REPORTED under a concurrent cancel; **the `finally` form silent in all six** and still lets the cancel out. `watcher_task.done()` was `True` at the retrieval in every arm — the guard is against `InvalidStateError`, not something measured to happen |
| the same matrix at the **real site** (four module copies of `bash.py`), 3.12.13 | identical verdicts through `_LocalBashOperations.exec`; `during-clean turn=True`: `main` `exit_code=0` (RED) vs all three fix shapes CANCELLED |
| citations, **revision 2's §B** | +36 (§B) / +40 (§B+§D.1); 2 drifted, exit 1; `--fix` → `relocated 2 citation(s) across 2 file(s)`, `488 anchors / 851 gated`, then `citations OK — 851 gated, none drifted` |
| `pytest tests/tools tests/pi_parity -q` with revision 2's §B applied | **683 passed**, 17.96 s and 18.33 s (two runs, pycache purged) — the gap TESTS-1 explicitly left open |
| `ruff check` (0.15.13, `B` selected) / pyright 1.1.409 on the revision-2 file | `All checks passed!` / `filesAnalyzed: 1, errorCount: 0, warningCount: 0` |

Not measured here: the windows leg; `tests/test_citation_drift.py` in the isolated copy (it needs
`git ls-files`, so the `--check`/`--fix` result above is the proxy — SCOPE-3 ran the test itself in
`/tmp/wt-234` and got 1 failed → 22 passed).

---

## Do not re-raise

No finding was refuted, but the refuters disproved eleven *sub-claims* — several of them the design's
own. Each cost a lane; none should cost another.

1. **"The retrieval line is dead code — refuted by measurement" (revision 1 §A).** The measurement
   covers the `raise before` ordering only. `Task.cancel()` cannot clear a flag `Future.set_exception`
   has not set yet. Do not re-derive the rejection from that row.
2. **"`Task.cancel()` clears `_log_traceback` even on an already-failed task, so nothing is reported
   at GC" (revision 1 §B's comment, already in the worktree).** First clause true, second false.
3. **Retrieval written *after* `await asyncio.wait(...)`** — ASYNC-1's own proposed fix. It never runs
   when the caller's cancellation propagates out of the wait, i.e. in the one window #234 exists for.
   Only the `finally` form (or `gather(..., return_exceptions=True)`) is silent in all six arms.
4. **`asyncio.gather(watcher_task, return_exceptions=True)` is wrong.** It is not — it is measurably
   equivalent on correctness. Revision 2 rejects it on cost only (it cancels its children; it returns
   a value to discard). If a later lane prefers it, that is a defensible swap, not a bug fix.
5. **"In this window the root has already exited."** True of the NORMAL leg only. The TIMEOUT leg also
   reaches this `finally`, and there a tree was already ended and a helper already killed.
6. **"No tree is ended / the helper survives, before and after."** Measured false on the timeout leg
   (`alive_after_exec=False`). Scope every such sentence to a leg.
7. **"The unbounded exit-path drain runs here (2.03 s for a 2 s holder)."** Normal-exit leg only; the
   timeout leg has `exited_at` set and takes the bounded `_drain_past_the_kill` (0.407 s with a 3 s
   escapee). ASYNC-2's own first probe could not see this — its root exited immediately, so its
   "TIMEOUT leg" row was not reliably the timeout leg. Use a root that sleeps 30 s.
8. **"The TUI's `!command` is this `exec`, therefore Esc reaches this window."** The premise is true
   and the conclusion does not follow: `handle_user_bash` passes `signal=None`, so no watcher exists
   and the changed `finally` body is skipped. Esc during a `!command` does nothing at all (measured
   5.010 s vs 5.011 s). Same for a model-issued bash call: `ctx.signal` is always `None`.
9. **"No in-product path cancels the RPC dispatch task from outside" (SCOPE-1's parenthetical).**
   Refuted by measurement: `gather(..., return_exceptions=True)` propagates an outer cancel.
10. **"9 of 300 / 4 of 300 / 3-of-3 aimed / none of 600."** Wrong denominators and wrong counts.
    `probe_b_random.py` prints `cancel_requested` and `already_done` separately; `probe_a` runs six
    rounds; "600" was rounds, of which 380 requested a cancel. And the seed does **not** reproduce the
    outcome — LOST was 3–7 across ten runs of the same seed on this host.
11. **"Drop the numbers from the source comment; the ADR is where dated measurements belong"**
    (SCOPE-4's own proposed remedy). Refuted from the file: `bash.py`'s comments routinely carry
    measurements ("20/20 trials, both returned `-9`", "14.9 GB against 0 MB after 2 s"). Fix the
    numbers, keep them in the comment.
12. **An unconditional `_end_the_tree` before the wait as the mutation probe for C.4.** It also
    reddens case 8b, for a reason unrelated to this window (measured, 2 failed) — it proves nothing.
    The discriminator is `except asyncio.CancelledError: _end_the_tree(...); _mark_the_kill(); raise`
    around the new `await`.
13. **"C.1 pins nothing" / "zero real-children coverage" (TESTS-1 as filed).** Overstated on both
    counts: C.1 does pin the *propagation* (the `BaseException` belt reddens it and nothing else) and
    it does spawn a real child. What is unpinned is the ADR's *Consequences* paragraph, and what C.1
    lacks is a real **tree** — helper plus pipe holder. That is C.4's job.
