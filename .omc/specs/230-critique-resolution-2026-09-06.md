# #230 design — revision 2 resolution table

Base: `.omc/specs/230-design-2026-09-06.md` (revision 1, now overwritten in place by revision 2),
main `7fa6796`, worktree `/tmp/wt-230`. Three lenses (posix-and-races, tests-and-legs,
product-and-docs) filed **21** findings: **10** got a measured refuter pass, **8** were MINOR ("not
refuted; apply if cheap"), and on **3** the refuter died before returning a verdict (PD-4/5/6 —
treated as UPHELD-pending and applied conservatively). Codex was still inside its usage limit when
the lenses ran, so every lens and every refuter in this round is Claude; the Codex cross-review lane
runs against **this** revision.

**Count: 7 UPHELD, 11 PARTIAL, 3 UPHELD-pending (refuter died), 0 wholly REFUTED.** No finding's
topic is dropped; what was refuted is individual sub-claims *inside* PARTIAL findings, and those are
listed at the bottom so the next round does not spend itself re-litigating them.

The revision's centre of gravity moved twice. Revision 1 was "disarm at the reap + end the drain".
Revision 2 is that **with a gate** (there is one path where `_end_the_tree` returns without a reap,
so "at the reap" has to mean it), **with a fourth case** (§B left the `finally`'s disarm with no
coverage anywhere in the repository), and **with two more files** — `extensions/api.py`'s docstring
and a second CHANGELOG edit — because the sentences a user or an extension author actually reads say
the opposite of what this ships.

---

## Findings

| id | lens | sev | verdict | one-line evidence | disposition in revision 2 |
|---|---|---|---|---|---|
| PD-1 | product-and-docs | BLOCKING | **UPHELD** | Both stale clauses and the new entry live in the SAME `## [Unreleased]` (lines 19–709); assembled the shipped artifact and grepped it — all five sentences survive unqualified, 294 lines apart; `grep -rln` over `.omc/specs` shows no other lane owns the #221 bullet | §D.6 becomes **two** edits: (a) the new `### Fixed` entry, (b) in-place correction of the #221 `Known behaviour changes` bullet's two "cancelled turn … end the whole tree" clauses (the repo's own `92b3f35` convention). Its FIRST half is left alone — "a helper … still survives" is what #230 makes true. §F names both edits; a flatten-and-grep check is written into D.6 |
| POSIX-RACE-2 | posix-and-races | MAJOR | **UPHELD** | `_end_the_tree` ends on `suppress(TimeoutExpired): proc.wait(timeout=reap)` and can return with the root ALIVE — its own "live ORPHAN, not a zombie"; probed across the fakes file, **1 of 10** arrivals at the §B.3 line has `returncode is None` | §B.3's disarm is **gated on `proc.returncode is not None`** and its comment rewritten (revision 1's "the tree is already dead" is false on that path, and the second `killpg` there aims at a leader this call did NOT reap). §A.6 gains the unchanged-arrival paragraph, §C.2 gains a `wedged` arm, §G gains the "one path where the disarm does not move" bullet, mutation row (i) |
| POSIX-RACE-4 | posix-and-races | MAJOR | **UPHELD** | The proposed refutation — an abort in the residue reaching a stranger or ESRCH *while the helper is alive* — was **not found in 0 of 80** landings; every landing found a non-empty group whose sole member was this run's own helper (3/3 darwin, 2/2 docker linux), and the ESRCH branch fires only when no helper is left (3/3, 2/2) | §A.5 and §D.1(b) and §B.2's comment stop calling the residue "the PID-reuse hazard": it is **the same bug, 10³–10⁴× narrower**. `ProcessTree.close`'s citation is kept for the empty-group branch only, with the ESRCH measurement and the 85.6 s pid-wrap floor beside it. §G says "narrowed, never closed". §H rows 24–26 |
| T2 | tests-and-legs | MAJOR | **UPHELD** | Ran the finding's own refutation: with §B applied and the `finally`'s `abort._finish()` deleted, `tests/process_tree` is **98 passed** and the whole repository green — no test anywhere kills the mutant; and `test_a_late_abort_after_the_run_finished_is_a_no_op` is green without it, so C.4's drafted attribution would ship a falsehood | New **C.5** `test_a_raise_before_the_reap_still_disarms_the_handle` (2 legs × 2 arms), RED 4/4 against the mutant. §B.4 rewritten — the `finally` is NOT the disarm for a spawn or attach failure (both sit above the `try`; measured `_finished is False` on both). C.4's amendment re-pointed at the REAP disarm. Mutation row (j) |
| T3 | tests-and-legs | MAJOR | **UPHELD** | A real windows-latest run in this repo: `gh run view 33959649661 --log-failed` → `assert b'done\r\nlate\r\n' == b'done\nlate\n'` and, decisively, a bare `print('x')` under `run_contained` → `assert b'x\r\n' == b'x\n'`; `create_stdio` sets `newline = NULL` under `MS_WINDOWS` | §C.3's root writes `b"done\n"` through **`sys.stdout.buffer`**, never `print`, and the pin becomes **`b"done\n" in result.stdout`** — measured, under T1's handshake `startswith` is false 8/8 (`b'hdone\n…'`) while `in` holds 8/8, safe under `PIPE_BUF`. §H rows 34–35 |
| PD-2 | product-and-docs | MAJOR | **UPHELD** (one correction) | Measured **through the real `ExtensionAPI.exec`** (its own declared `run_contained` seam): on `main` a turn cancelled 0.5 s after the root's exit killed the helper 4/4; with §B, killpg empty and helper ALIVE 4/4 — so the docstring's "a cancelled turn ends the TREE" and its `kill_on_close=False` parenthetical are both false there. Gate cost zero: all 13 `api.py` anchors end at 1670, the paragraph starts at 2096 | Scope widened by a **docstring-only** edit in `extensions/api.py` (§B opening, new §B.5, new §D.8, §F commit list). **Correction:** the docstring is not the only author-visible statement after this lands — §D.6's CHANGELOG bullet also states it — but a change record is not reference documentation and does not fix `help(aelix.exec)` |
| PD-3 | product-and-docs | MAJOR | **UPHELD** (consequence re-worded) | Exhaustive, not the three commits proposed: **6 of 6** commits that ever touched ADR-0238 also rewrote the 0238 index row, always the same single line at `@@ -141,7 +141,7 @@`; no generator and no CI job maintains it; two of those commit bodies name "decisions/README row" | §D.5 becomes six edits, the sixth being `docs/decisions/README.md`, with the appended Korean clause drafted in the row's voice. §F names the file. §D.7 gains a half-sentence so it is not read as covering the index. **Re-worded:** the row never says "#230", so the harm is that it skips this landing and keeps asserting a promise this issue is the one to make true |
| T1 | tests-and-legs | BLOCKING→**MAJOR** | **PARTIAL** | Mechanism confirmed: the drain's opening is held by ~7 ms of helper start-up against a 100 ms idle grace (15/15), and an injected 0.12 s delay makes all six of C.3's pins pass on `main` 3/3. Its supporting claims did not survive — see "do not re-raise" 4–6 | §C.3 re-anchored to the ROOT: a `chatting` handshake before the root exits, a `leaving` flag, the abort +0.2 s off that flag, plus the file's own vacuity guard (`aborted_at` non-empty **and** `< returned`). RED on `main` **12/12** across 0.00–1.00 s injected delays, GREEN on the fix 9/9, and ~0.25 s instead of ~0.53 s. §G's win32 bullet amended rather than deleted; §H rows 31–32, 39 |
| POSIX-RACE-1 | posix-and-races | MAJOR | **PARTIAL** | Mechanism upheld against the **patched** module: `KeyboardInterrupt` 0.6 s into a 2.0 s drain enters `_end_the_tree` with `proc.returncode == 0` and group-kills the helper, **3/3**, and identically with a handle passed (the ladder is `_end_the_tree`, not the handle). Reach halved — see "do not re-raise" 1 | §G gains "the other door into the same window, not closed here", with the reach corrected to ONE caller and the reason it is left alone (that ladder also keeps `rmtree` off a live `git`). §A.4's rule gains the one-sentence scope clause. §D.5 gains a second, still-open ADR bullet and the Status/Consequences wording is disciplined to say `abort()`. §H rows 27–28 |
| POSIX-RACE-3 | posix-and-races | MAJOR | **PARTIAL** | Its three factual claims hold (CPython publishes `returncode` before `wait` returns; the guard keeps 394 tests green and does not disarm the live-root kill; it does suppress the reaped-arm kill 2/2). Its characterisation and its remedy do not — see "do not re-raise" 2 | The guard is **rejected, and why is written down** (§A.5): its exclusive coverage is ~2.5 µs, and with it in place mutant (a) passes every pin C.1 and C.3 carry, 3/3. Revision 1's "It cannot be closed" absolute and its "after this it is microseconds" both deleted. New §A.5 paragraph on the **head** of the window — 0.712 ms at `timeout=None`, ≈54 ms with a timeout — carried into §D.1(b), the ADR and §G. §H rows 21–23 |
| PD-4 | product-and-docs | MAJOR | **UPHELD-pending** (refuter died) | Re-measured the mechanism here rather than trusting either side: a cancel delivered **inside** a `finally`'s await cuts it at 0.02 s (3/3); delivered **before** the `finally` it does not, and the await runs to completion 0.5011 s with the caller still receiving `CancelledError` (3/3) — which is exactly what the repo's own bash-tool case pins ("`TAIL` still arrived") | §A.7 restated on the **leg** axis, not the site axis: abort-signal leg (bash tool's watcher inert, drain runs to EOF for a listening caller) vs task-cancellation leg (both sites kill nothing; what happens to the drain depends only on the arrival instant, and clause (d) is `run_contained`'s substitute because its drain is a synchronous loop no `CancelledError` reaches). "Different delivery" deleted as a site-level explanation. §D.3 and the ADR follow. §H row 38 |
| PD-5 | product-and-docs | MAJOR | **UPHELD-pending** (refuter died) | Both extra sentences read and confirmed present: the #221 SITE-1 "…the worker returns down the normal exit path with the kill's `returncode`", and the #234 "That bound is this ADR's to carry because it also bounds #230" — the latter false twice over, since #230's window is reached from `except asyncio.CancelledError` with no signal at all | §D.5 bullet 4 lists **four** sentence-level amendments instead of two, each quoted |
| PD-6 | product-and-docs | MAJOR | **UPHELD-pending** (refuter died) | Read: the `AbortHandle` paragraph above the closing sentence says the kill "lets the worker's blocked `proc.wait` return, after which the call's ordinary exit path **drains under its idle rule**" — the exact behaviour clause (d) removes | §D.1 becomes **two** edits, (a) that paragraph and (b) the closing sentence, with the 87 B → 13 B measurement in (a) |
| POSIX-RACE-5 | posix-and-races | MINOR | **PARTIAL (adopted)** | Not refuted; same defect T6 filed | Mutation row (h)'s justification replaced: `_finish()` **is** a suspension point (it takes a lock a concurrent `abort()` holds across `hard_kill`). The verdict survives for the other reason — contention implies `aborted`, and clause (d) then ends the drain before `exited_at` is read |
| T4 | tests-and-legs | MINOR | **PARTIAL (adopted)** | Not refuted; both sibling chatty cases clear `close_on_kill` and the file's own comment names the fd hazard | §C.1 sets `spawner.close_on_kill = False`, with one sentence saying it is load-bearing (at the default the abort's `proc.kill()` EOFs the pipes, clause (a) ends the drain, and the timing pin passes on `main`) |
| T5 | tests-and-legs | MINOR | **PARTIAL (adopted, recomputed)** | Not refuted, and superseded by the cases the other findings added | Counts recomputed from the final case list: **11 new items** (C.1 ×2, C.2 ×4, C.3 ×1, C.5 ×4), **5 failed** on `main`, **109 passed** after §B — and §C now says which 6 items are deliberately green on `main` because they pin this design's boundaries rather than the issue's bug |
| T6 | tests-and-legs | MINOR | **PARTIAL (adopted)** | Not refuted; duplicate of POSIX-RACE-5 with the win32 `taskkill` bound spelled out | Resolved once, in row (h). §A.6's "not new, only moved" narrowed to "not new in TOTAL — but it also moves the drain's cap origin" |
| T7 | tests-and-legs | MINOR | **PARTIAL (adopted)** | Not refuted; the same unguarded-window defect T1 found in C.3, in the live procedure | §E anchors on a `leaving` flag the root writes immediately before exiting, records the Escape's own timestamp, and **prints `esc landed at exit +…s`**; "What must be SEEN" makes that number the precondition — without it "helper alive" is consistent with both outcomes |
| PD-7 | product-and-docs | MINOR | **PARTIAL (adopted)** | Not refuted; verified all three: the file spells it `aelix.exec(...)` at :478/:540/:668, the wait has already returned when the drain runs, and rows 3+4+9 are eight runs, not six | §D.6(a): `aelix.exec(...)`; "ends **that reading**" instead of "ends the wait"; "**eight** measured runs (six on macOS, two on Linux)" |
| PD-8 | product-and-docs | MINOR | **PARTIAL (adopted)** | Not refuted; `grep` for `abort` in `extension-authoring.md` **does** hit (a generator's `finally` "runs while an abort unwinds your generator"; "one bad extension does not abort the rest") | §D.7 states the accurate grep and what it found; the verdict (no `exec` abort semantics documented anywhere in the guide) is unchanged, and `aelix.exec` / `api.exec` appear there not at all |
| PD-9 | product-and-docs | MINOR | **PARTIAL (adopted)** | Not refuted; verified — every existing amendment date in `Status:` equals its landing commit's date (`git log --date=short`: #220/#221 2026-09-05, #222 2026-09-05, #234/#226 2026-09-06) | §D.5 dates the amendment **2026-09-07** and says why (2026-09-06 is the day the owner decided, not the day this lands); the ADR index clause uses the same date, while the 🔴 owner-decision marker inside it keeps 2026-09-06 |

---

## Conflicts between refuters, settled by re-measurement in this lane

1. **How wide is the residue?** POSIX-RACE-3 quoted revision 1's 1.48 µs median / 4.46 µs max (n=30);
   POSIX-RACE-4 measured 0.125 µs median / 0.750 µs max (n=200) and separately 0.055 ms median under
   a contending thread. Re-measured here (`rev2/residue.py`, both candidate brackets — the `_finish()`
   call alone and the whole `if abort is not None` region): **0.125 µs median, 0.083–0.209 µs, n=200,
   identical under both brackets**. So the three numbers are one load series, not a disagreement:
   idle 0.125 µs, in-situ 1.48 µs, contended 0.055 ms. Revision 2 tabulates all three and tells the
   reader to quote the contended one, because `ExtensionAPI.exec`'s aborter is the asyncio loop
   thread.
2. **Does a turn cancellation already end the bash tool's exit-path drain?** PD-4 says yes (measured,
   unverified — its refuter died); the repo's own
   `test_a_turn_cancel_in_the_watcher_teardown_keeps_the_helper_and_still_detaches` docstring says
   "`TAIL` still arrived … the cancellation does not truncate the output". Settled with
   `rev2/pd4_probe.py`: **both are true, of different arrival instants** — inside the `finally`'s
   await it is cut (0.02 s, 3/3), before the `finally` it is not (0.5011 s, 3/3). §A.7 says so, and
   PD-4's stronger unqualified claim is not adopted.
3. **Two findings both claimed mutation row "(i)".** POSIX-RACE-2's gate mutant and T2's `finally`
   mutant. Renumbered: **(i)** drops the §B.3 gate (dies in C.2 `wedged=True`), **(j)** drops the
   `finally`'s `_finish()` (dies in C.5, and nowhere else in the repository).
4. **Is C.2 as designed able to tell the candidates apart?** POSIX-RACE-2 measured that it is not —
   the reaped arm gives `sent == [False]`, one ladder, `kills == 1` under both the unconditional and
   the gated disarm. Revision 2 says that in C.2's own text and adds the `wedged` arm as the thing
   that pins the gate, GREEN on `main` and RED only against an unconditional `_finish()`.

---

## Do not re-raise — refuted sub-claims, with the reason

These were filed inside findings that are otherwise adopted. Each was measured false; the topic is
settled, and re-opening it costs a round.

1. **"The interrupt door is live at two `run_contained` call sites"** (POSIX-RACE-1). One.
   `tui/completion.py`'s fd scan is wrapped by `shell.py` in a `ThreadedCompleter`, so it runs off the
   main thread, and CPython raises `KeyboardInterrupt` on the main thread only — measured on a worker,
   the same ^C leaves `ladder == []`, `killpg == []`, helper ALIVE, rc 0 after the full drain, 3/3.
   Only `extension_catalog.py`'s clone (reached from `_cmd_discover`, which calls `fetch_all`
   synchronously on the event loop) is that site.
2. **"A `returncode is not None` guard inside `AbortHandle._kill` closes the residual armed window"**
   (POSIX-RACE-3), and **"adopt it alongside the `_finish()` move"**. Its exclusive coverage is the
   1.48 µs plus the **0.001 ms median** between the `returncode` store and `wait`'s return — ~2.5 µs
   — against a head of window it cannot see at all (0.712 ms, or ≈54 ms with a timeout: an abort 2 ms
   after an rc-0 root's exit under `timeout=10.0` still kills the helper **6/6 with the guard in**).
   And adopting it makes §C mutant (a) pass every pin C.1 and C.3 carry, 3/3. The *facts* under the
   finding are kept in §A.5; the guard is not.
3. **"~1.5 µs … 10⁵ times narrower"** (POSIX-RACE-4's own proposed wording). The contended residue is
   0.055 ms median / 0.103 ms max, so the honest ratio against a 0.105–2.004 s drain is **10³–10⁴×**.
   Revision 2 uses that everywhere and never writes 10⁵.
4. **"No pin catches a vacuous C.3 run"** (T1). Under §C.3 as literally worded — a *daemon*
   `threading.Timer` with no join — a late abort gives `sent == []` and an `IndexError`, i.e. a flaky
   **RED** on the gating leg, not a silent green. The defect is real and the guard is adopted; the
   silent-green failure mode is not what the design text produced.
5. **"Whether the window opens is a property of the runner's interpreter start-up"** (T1). The gap is
   a *difference* (helper start-up minus the root's own announce+print+finalize), so both terms move
   together: darwin at 2× oversubscription 10/10 open, docker `--cpus 0.25` 24/24 open, `--cpus 0.1`
   (root life 20× this box) still 8/8 open. Only `--cpus 0.05` (50×) closes it, 3/6.
6. **"windows-latest is a leg whose start-up cost is not measurable here"** (T1). It is bounded by
   this repo's own gating-leg record: run 33964215183 warns the sibling exited-root drain case at
   **0.718 s on win32 3.11 and 3.12** against 0.698 s darwin and 0.680/0.678 s linux — a 20 ms
   platform delta against C.3's 92 ms of margin. T1's BLOCKING severity rested on this; downgraded.
7. **"Mutation row (h)'s verdict is wrong"** (POSIX-RACE-5, T6). The verdict — inert — survives. Only
   the stated reason was false. Do not re-open the row's conclusion.
8. **"`ExtensionAPI.exec`'s docstring is the ONLY author-visible statement of these semantics after
   this lands"** (PD-2). §D.6's CHANGELOG bullet also states them. The fix is unchanged (a change
   record is not reference documentation, and it does not fix `help(aelix.exec)` or an IDE hover), but
   the claim as worded is false.
9. **"The ADR index would be left describing #230 as still open"** (PD-3). The row never names #230
   (`grep -c "#230" docs/decisions/README.md` → 0). What it actually does is skip this landing in a
   sequence that records every other one, and keep asserting the `kill_on_close=False` success-path
   promise that an armed handle falsified. Same fix, accurate consequence.
10. **Revision 1's own "It cannot be closed" and "after this it is microseconds"** (§A.5). Both false:
    two closures exist and were measured, and the post-change window is ≲54 ms, not microseconds.
    Deleted rather than softened.

---

## Open question carried into §G — only the owner can answer

Should a **^C that lands after the catalog clone's own `git` has already exited** still end that
tree? Choice A applied consistently says no — it is the same shape #230 decides for `abort()`. But at
that one live site the interrupt ladder is load-bearing for a second reason the module docstring
states by function: it ends the tree *before* `_git_clone_bytes`'s `finally: rmtree` deletes the
directory out from under a still-running `git`. A `proc.returncode is None` guard on the interrupt leg
would remove that protection. #230 decides the `abort()` path only and does not pre-empt this; §G
carries it, and no follow-up issue is filed until the owner answers.
