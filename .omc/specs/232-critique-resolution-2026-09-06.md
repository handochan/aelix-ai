# #232 design — revision 2 resolution table

Base: `.omc/specs/232-design-2026-09-06.md` (revision 1, now overwritten in place by revision 2),
main `7fa6796`, worktree `/tmp/wt-232`. Three lenses (asyncio-and-drain, tests-and-legs,
product-and-docs) filed **26** findings: 18 got a measured refuter pass, and on 8 the refuter died
before returning a verdict (TL-5, TL-6, PD-1…PD-6 — treated as UPHELD-pending and applied
conservatively, except TL-6, which another refuter's measurement contradicts). Codex was still on its
limit while the lenses ran, so every lens and every refuter in this round is Claude; the Codex
cross-review lane runs on THIS revision.

**Count: 6 UPHELD, 10 PARTIAL, 2 REFUTED, 8 UPHELD-pending.** Of the eight pending, seven are applied
and one (TL-6) is rejected because AD-5's refuter measured the gating windows legs and found its premise
false. No finding's topic is dropped; what was refuted is either a whole finding (AD-5, TL-1) or
individual sub-claims *inside* PARTIAL findings, and both are listed at the bottom so the next round
does not spend itself re-litigating them.

The revision's centre of gravity moved twice. Revision 1 was "one drain rule, one cap, one helper".
Revision 2 is that **plus a second `_process_tree.py` change** — a returning `exec` now leaves a live
reader behind on the ORDINARY path, and that reader was pinning the caller's whole output (AD-3) —
**plus a landing order**: #230 and #232 edit the same ten lines and four hunks conflict, measured
(AD-6).

---

## Findings

| id | lens | sev | verdict | one-line evidence | disposition in revision 2 |
|---|---|---|---|---|---|
| AD-1 | asyncio-and-drain | BLOCKING | **UPHELD** | C.4's repair cannot be built from the knobs revision 1 named: with the existing root, `hold=OUTLIVE` makes the tail *quieter*, `task.done()` is True at the abort and `exec` is back at **0.364 s**; via `_holder_command` the case inverts (**1.388 s, `rc=None`, root gone**) and its holder is `new_session=True`, so the survival pin would be vacuous on POSIX | §C.4 replaced wholesale: the repair is a **script edit** — `ROOT_THAT_BACKGROUNDS_A_HELPER_BEHIND_A_TAIL` gains a fourth argv `tick` branching as `HOLDER` does, both call sites pass one (8b `0.05`, 8c `0.0`), and no `len(sys.argv)` default is available because `_command` appends `MARK`. `assert not task.done()` before the abort. The byte pin **changes** — "pins unchanged otherwise" struck. §C.5 gains the `tick=0.0` sentence; §H rows 12–13 say which script produced them |
| AD-2 | asyncio-and-drain | MAJOR | **UPHELD** | The success path's resume condition changes kind: a `raise` at the top of `_drain_after_the_exit` reddens **15 of 16** ids (7 on `main`, and case 9 not among them); instrumented, 6 of 14 drains end `cap/idle`, three of them success cases that could only end on EOF before | §C.7 rewritten ("stays green, but its path moves" — `eof_at_entry=True` 5/5, 0/12 queued on this shape). New **C.8** rewrites case 9's docstring paragraph, both of whose measured sentences invert. Mutation row (h) added for the trailing yield. §B's last paragraph: the yield's TEXT does not move, its STATUS does. §D.5 amends the comment above it |
| AD-3 | asyncio-and-drain | MAJOR | **PARTIAL** (defect yes, remedy no) | 5 successful `nohup`-shaped calls pinned **20 MiB** and left `readers 1→5, fds 8→12`; an 8 MiB command left 8 MiB resident (`main`: 0, because it did not return). The finding's own two-line fix is **inert** for that shape — it lives in the detached branch, which only runs when a chunk arrives | New **§B.5**: `run` caches only `has_on_chunk`, loads the callable per chunk and rebinds it to `None`; `detach` clears `self._on_chunk`. The bool is load-bearing (an in-flight chunk would otherwise re-fill the list `detach` emptied). New **C.9** in `tests/process_tree/test_pipe_reader_callbacks.py` (`_ScriptedStream(gate_before=1)`, weakref + `gc.collect`), mutation rows (i)/(j). §G risk bullet, §D.1 ADR consequence, §D.3 CHANGELOG clause, §D.5 docstrings. Measured 0 MiB after; **786 passed** under `main`'s rule with §B.5 |
| AD-4 | asyncio-and-drain | MAJOR | **PARTIAL** (half refuted) | Mutation (f) satisfies every pin §C.4 named, 3/3, at **1.023/1.025/1.025 s** against the adopted 2.030 s; the whole gate is failure-identical under it (783 passed, same 3 failed). Refuted half: a no-cap regression does NOT "also pass" — it fails after 60 s on the helper pin, with a false message | §C.4 gains `DRAIN_CAP_SECONDS <= elapsed <= _bound(…)` and `hold` drops to **5.0**; the FLOOR is the discriminator and is platform-independent. Mutation row (f) reassigned to **C.2** (1.067 s vs 2.069 s, 18 ticks vs 36) **and** C.4's new pin, with "case 4's timeout leg" struck (`_wait` raises before the stamp). §H row 13 amended, row 17 added |
| AD-5 | asyncio-and-drain | MAJOR | **REFUTED** | On the gating windows legs themselves: this case's launch chain is **0.265–0.343 s** (8 legs) and today's heavier version **0.734–0.829 s** (8 legs); re-pulled here from job 101474893576 at `7fa6796`: `backgrounded helper 0.343s`, `exit-path drain 0.812s`. `_bound(0.0)` = 7.5 s on win32 would pass a regression that always waits the 2.0 s cap | `elapsed < 1.0` **kept**, and the proposed `_bound`-shaped ceiling **not** adopted. Its two adjacent, measured observations ARE taken: §C.1 records the windows slack and the #221 sibling's bare-literal convention, and the RED-on-`main` sentence is corrected (merged with TL-2, which measured the same defect with the real case) |
| AD-6 | asyncio-and-drain | MAJOR | **UPHELD** | Both designs' §B/§D reconstructed on `7fa6796` and merged with `git merge-tree --write-tree`: **4 conflict hunks** — 1 in `_process_tree.py` (no unchanged line separates #232's `drain_until` replacement from #230's `_drain(…)` call and `abort._finish()`), 3 in ADR-0238. Keeping `_exit_drain_cap` bash-local removes 1 and leaves 3 | §G's two old bullets replaced by one: **#230 lands first**, then `git -C /tmp/wt-232 rebase fix/230-…`, hand-resolve four semantically compatible hunks, re-take §H 12–14 and C.4/C.5's warns on the merged tree (**ORCHESTRATOR STEP**). §A names the ordering where the extraction is decided; §F's baselines shift by #230's three cases. Re-measured in this lane: **§B.5 adds no fifth hunk** |
| AD-7 | asyncio-and-drain | MINOR | **PARTIAL** | Same defect as TL-4, filed smaller: the `and timeout > 0` conjunct is a real behaviour change and its docstring premise is false | Resolved once, with TL-4 (option (a): drop the guard) |
| AD-8 | asyncio-and-drain | MINOR | **PARTIAL** | `_bound(DRAIN_CAP_SECONDS)` folds `KILL_DRAIN_SECONDS` into a case where nothing is killed — 4.5 s POSIX, 9.5 s win32 for a 2.0 s cap | Adopted as a stated limitation rather than a new formula: §C.2 and §C.4 say the ceiling is a sanity bound and the FLOOR is the discriminator. A second ceiling formula for the no-kill drains is not worth a divergence from the file's one helper; recorded here so it is a decision, not an oversight |
| TL-1 | tests-and-legs | BLOCKING | **REFUTED** | The finding substituted case 5's TOTAL warn for the arm→first-byte gap; every term before the root's exit cancels. Measured gap: **8.9 ms darwin** (n=12), **1.2 ms linux**, **−32..+15 ms on the gating windows legs** (differencing case 8/8b against case 5 in the same job, 6 samples, 3 jobs; one reproduced in this lane: `0.812 − 0.343 − 0.5`). C.2/C.4 GREEN 11/11; they break at **+0.09 s** of injected tail delay | No blocking change; the ready-file handshake is **not** adopted (its stated mechanism is wrong — a chunk before the exit does not hold the drain open, since the rule arms at `max(last_chunk_at, exited_at)`). Its two cheap, measured observations are taken: §C.2 says the tail's first write is its FIRST statement and carries the gap numbers, and §G's win32 bullet says the one quantity these cases turn on IS measurable there |
| TL-2 | tests-and-legs | MAJOR | **UPHELD** | The rewritten C.1 run as a real pytest case against `main`'s blob: `Failed: … exec did not return within 7.5s` at **7.70 s** — pytest never reaches the byte assertion, because `hold=OUTLIVE` means `main`'s `await eof.wait()` never returns. The `0.535 s` / `EARLY LATE` pair is the case AS SHIPPED (re-measured GREEN at 0.540 s) | §C.1's RED-on-`main` sentence rewritten: the RED is `_bounded`, not a byte diff. Guard drops to `_bound(0.0) + 5.0` (7.70 s vs 10.68 s on a RED, identical on a GREEN); `warnings.warn` moves INTO the `finally` so a RED records both halves in one line; the clean byte pair comes from a temporary `hold=8.0` copy. Mutation row (a) restated. §H row 16 |
| TL-3 | tests-and-legs | MAJOR | **UPHELD** | The site passes `handle` only, so `attach_kwargs[0].get("kill_on_close", False)` returns the TEST's literal. With `attach`'s default flipped to `True`, the design's guard **PASSES** while a signature/bind guard fails — measured, 3 guards × 2 worlds. `_attach_posix` has no such parameter and `ProcessTree` never stores it | §C.6 replaced with `inspect.signature(real_attach).bind(pid, **kwargs); bound.apply_defaults(); assert bound.arguments["kill_on_close"] is False` — one assertion red on BOTH regressions. The false justification ("the assertion reads the effective value") deleted. "Two lines" → "three, `import inspect` included" (the module does not import it). New mutation row (k) |
| TL-4 | tests-and-legs | MAJOR | **UPHELD** (4/4 sub-claims) | `run_contained` has bare `if timeout is not None:`; the design added `and timeout > 0` on the premise that `proc.wait(0)` "can only raise" — it returns 0 in **25 µs** for an exited child (re-measured in this lane; CPython reaps with `WNOHANG` before checking the remaining time), and `run_contained(timeout=0)` takes the exit leg **40/40 under GIL load**. The guard loosens a landed cap 20× on a path with **no** `tests/process_tree` coverage | Option (a): the guard is **dropped**. §B.4's docstring rewritten to say why a non-positive timeout is deliberately not special-cased. §C.3 gains a **fifth arm** (`timeout=0.0` → `exit + 0.1`), mutation row (d′), §G's extraction bullet rewritten. §H row 18 |
| TL-5 | tests-and-legs | MAJOR | **UPHELD-pending** (refuter died) | Same claim as AD-4, filed independently: row (f)'s named killers cannot see the mutation | Adopted via AD-4's measured resolution, which is its proposed fix plus the numbers: row (f) → C.2 **and** C.4's new elapsed pin |
| TL-6 | tests-and-legs | MAJOR | **UPHELD-pending → NOT ADOPTED** | Same claim as AD-5, which was refuted on the gating legs' own warns. Its `nap` half (0.5 s → 2.0 s) would also make C.1 slower for margin it does not need (0.55 s of headroom measured) | **Rejected**, and the reason is on the record in §C.1 rather than left to the next round: `assert elapsed <= _bound(0.0)` is 7.5 s on win32, which a regression that always waits the 2.0 s cap would pass. This is the one pending finding not applied; see "conflicts settled" |
| TL-7 | tests-and-legs | MINOR | **PARTIAL** | The chatty tail makes 8b's exact-bytes pin unsatisfiable, and 8b/8c share one script | Adopted inside AD-1's §C.4 replacement (the byte pin changes; the shared script gains `tick`, 8c passes `0.0`) and AD-4's (the count assertion). Nothing separate |
| TL-8 | tests-and-legs | MINOR | **PARTIAL** | `_holder_command`'s `hold` is the ROOT's sleep, so C.2 spelled `hold=OUTLIVE` never reaches the exit drain | §C.2 now spells the call: `_holder_command(tmp_path, marker, tick=0.05, hold=0.0)`, `_registrar` at the default two fields. **Found in this lane while checking it:** that root writes nothing to stdout, so revision 1's `chunks.startswith(b"EARLY\n")` was impossible — the pin is now `0 < chunks.count(b"tick\n") < 200` |
| TL-9 | tests-and-legs | MINOR | **PARTIAL** | `_bounded(task, hold + 10.0)` at `hold=OUTLIVE` is a 70 s guard on a job with no `timeout-minutes` | Adopted via AD-4, which measured the consequence (60.04–60.09 s inside its own guard, 3/3, then a FALSE failure message): `hold` is **5.0** |
| TL-10 | tests-and-legs | MINOR | **PARTIAL** | C.3 is a pure unit test of `aelix_ai.utils._process_tree._exit_drain_cap` placed in `tests/tools/test_bash_tool.py` | Adopted: C.3 moves to `tests/process_tree/test_run_contained.py`, beside `test_the_deadline_caps_the_drain_but_keeps_one_grace` — where this arithmetic's other arms already live. §F's per-file line updated |
| PD-1 | product-and-docs | BLOCKING | **UPHELD-pending** (refuter died) | Verified by reading: the #222 `Fixed` bullet still ends "A command that exits **successfully** … is deliberately unchanged and still waits for that helper to close the pipe" — present tense, same unreleased section, ~480 lines from the new entry | §D.3 becomes two edits: (a) the new `Changed` entry, (b) that sentence rewritten IN PLACE to point at it (the file's own `92b3f35` convention for unreleased text) |
| PD-2 | product-and-docs | BLOCKING | **UPHELD-pending** (refuter died) | The drafted "up to the command's full 10-minute default" is refuted by the design's own §0: a `timeout=10` call returned at **13.939 s**, so there is no ceiling at all | Clause replaced with the measured truth in §0 and §D.3: **no ceiling at all** — 4.04 s for a 4 s helper, 13.94 s for a call that asked for 10 s, 4.04 s for one that asked for 1 s and was told it succeeded within its deadline |
| PD-3 | product-and-docs | MAJOR | **UPHELD-pending** (refuter died) | Re-measured in this lane (`rev2/probe_buffering.py`): `now` reaches the pipe at **+0.003 s**, `started` at **+4.018 s** — a backgrounded program's stdout is a pipe, hence block-buffered, so the model loses the helper's ENTIRE output, not its tail | New paragraph in §0; one sentence each in §D.3's CHANGELOG bullet and §D.1's ADR Consequences, with the workaround named (`nohup … > dev.log 2>&1 &`, or unbuffered). §A's "what the model loses" restated at full width |
| PD-4 | product-and-docs | MAJOR | **UPHELD-pending** (refuter died) | The §E criterion said neither binary would show `started`'s tail; `main` DOES capture it (same +4.018 s measurement) | §E's "Must SEE" rewritten as two lines with the discriminator explicit — `main` `now\nstarted\n` at ~4 s, branch `now\n` at ~0.1–0.3 s — plus the buffering sentence so it is not read as flaky, plus a second arm where the cut cannot be confused with buffering: `(sleep 1; echo late) & echo now` |
| PD-5 | product-and-docs | MAJOR | **UPHELD-pending** (refuter died) | The TUI's `!command` runs this exact `exec` with **no timeout at all** — the one place today's behaviour is an unbounded human-visible freeze, and the place where the 2.0 s cap becomes the only bound | One sentence in §D.3's CHANGELOG bullet (`!npm run dev &`), one clause in §D.4's README edit, and "never past the deadline you asked for" qualified to "where you asked for one". §E's rule-9 paragraph says why that leg is the interesting one |
| PD-6 | product-and-docs | MAJOR | **UPHELD-pending** (refuter died) | The per-call reader/fd cost moves from the rare kill legs to the common success path, and neither §D nor §G said so | Same remedy as AD-3's §3–4, which measured it (+1 thread, +1 fd per call, stacking 5/5; zero on `main`): §G risk bullet and §D.1's Consequences, with Pi's contrast (it destroys the stream) named |
| PD-7 | product-and-docs | MINOR | **PARTIAL** | §D enumerated paragraphs instead of prescribing a sweep, and the README sentence it named is false about both halves it mentions, not just the survivors | New **§D.6**: run the grep after §B and §D, fix every present-tense hit. Run in this lane — the hit set is exactly README.md:127, its README.ko.md twin, CHANGELOG.md:513 and the #222 bullet's closing sentence, so the enumeration is the KNOWN set and the grep makes it the whole set. `CHANGELOG.md:496` is about `run_contained` and stays TRUE — named explicitly so nobody "fixes" it |
| PD-8 | product-and-docs | MINOR | **PARTIAL** | Dating and voice: the amendment is dated 2026-09-06 although the work runs after Codex's 2026-09-07 reset, and the draft writes "pi" where this repo's CHANGELOG writes "Pi" in prose (lowercase only in `pi-ai` / `pi-parity`) | ADR amendment and the "What stays open" bullet dated **2026-09-07** (the landing date), with the decision inside attributed to the owner's 2026-09-06 choice; the CHANGELOG draft says **Pi**. The design's own title keeps its filename date, as the sibling specs do |

---

## Conflicts between refuters, settled by re-measurement in this lane

1. **Does C.1's `elapsed < 1.0` red the gating windows legs (AD-5, TL-6) or not (AD-5's refuter)?**
   Settled by the legs' own warns, re-pulled here from job 101474893576 (`main` `7fa6796`, the design's
   base): `bash exec backgrounded helper: 0.343s`, `bash exec exit-path drain: 0.812s`,
   `bash exec abort in the exit drain: 2.312s`. The flipped case pays the launch chain plus one 0.1 s
   grace — ~0.45 s at the worst observed leg, against a 1.0 s ceiling. **The literal stays** and TL-6's
   `_bound(0.0)` (7.5 s on win32) is rejected as a *weakening*.
2. **Row (f)'s killer: C.4 (revision 1), C.2 (AD-4), or C.4-with-a-new-pin (TL-5)?** Both. AD-4 measured
   C.2 at 1.067 s against its 2.0 s floor and C.4 at 1.026 s once C.4 has a floor of its own; revision 2
   credits **both**, and strikes "case 4's timeout leg", which cannot see the mutation at all (`_wait`
   raises `TimeoutExpired` before the stamp; the whole gate is failure-identical, 783 passed / same 3
   failed, either way).
3. **AD-1's `hold=OUTLIVE` for case 8b vs AD-4's `hold=5.0`.** AD-4 wins on measurement: at `OUTLIVE` the
   case's own `_bounded` guard becomes 70 s, and a dropped cap then runs 60.04–60.09 s (3/3) before
   failing on the helper pin with a message that is false about what happened. `hold=5.0` keeps the
   guard real and the failure honest; everything else in AD-1's script edit stands.
4. **Does §B.5 (AD-3's fix B) worsen the #230 collision AD-6 measured?** Re-measured here: AD-6's
   reconstruction plus fix B still yields **4 hunks** — 1 in `_process_tree.py`, 3 in ADR-0238, 0 in
   CHANGELOG. `_PipeReader.run`/`detach` is ~500 lines from `run_contained`'s exit leg and auto-merges.
   The landing order is unchanged by AD-3.
5. **C.2's spelling (TL-8) vs its pins (revision 1).** Checking TL-8's correction surfaced a second
   defect neither lens filed: `ROOT_WITH_AN_ESCAPED_HOLDER` writes **nothing** to stdout, so revision 1's
   `chunks.startswith(b"EARLY\n")` could never pass. The pin is now on the holder's own `tick\n` bytes.

---

## Do not re-raise — refuted claims and sub-claims, with the reason

1. **"C.1's `elapsed < 1.0` is below the fixed cost on the gating windows legs"** (AD-5, TL-6). The legs
   run the launch chain in 0.265–0.343 s over 8 legs; today's heavier version of the case finishes in
   0.734–0.829 s over the same 8. A `_bound()`-shaped ceiling would be 7.5 s on win32 and would let a
   2.0 s-cap regression pass.
2. **"C.1's RED on `main` is the bytes assertion, which is clock-independent"** (AD-5's own supporting
   sentence). With `hold=OUTLIVE` the case reds in `_bounded` at 7.5 s and never reaches the bytes
   (measured). The design says so now — but as a correction to §C.1, not as evidence for the ceiling.
3. **"C.2/C.4 lose a sub-0.1 s race on the gating windows leg"** (TL-1). The gap they turn on is
   arm→first-byte, not the case's total: −32..+15 ms across 6 samples from 3 windows jobs, 8.9 ms on
   darwin, 1.2 ms on linux, against a 100 ms window. Both cases were GREEN 11/11 on the prototype and
   break only at +0.09 s of injected delay.
4. **The ready-file handshake proposed with TL-1.** Its mechanism is wrong: a chunk delivered *before*
   the exit does not hold the drain open, because the rule arms at `max(last_chunk_at, exited_at)`. What
   keeps the drain armed is the tail already being inside its tick loop.
5. **"A no-cap regression also passes C.4"** (AD-4's own second half). It fails — after 60 s, on the
   helper pin, with a false message. That is worse than passing, and it is why `hold` is bounded.
6. **AD-3's proposed two-line fix** (clearing `_on_chunk` inside `run`'s detached branch). Measured
   inert for the `nohup server &` shape: that branch runs only when a chunk arrives, and a silent holder
   never writes again. Only the caller-thread release (§B.5) works. Clearing it from `detach` alone is
   equally useless while `run` caches the local.
7. **"`proc.wait(0)` can only raise"** (revision 1's own docstring, defended by AD-7 as a smaller
   point). It returns 0 in 25 µs for an already-exited child on POSIX, and win32's `_wait` maps
   `timeout <= 0` to `WaitForSingleObject(handle, 0)`, which returns `WAIT_OBJECT_0` for a signalled
   process. Reasoned from CPython source for win32; nothing is measurable there here.
8. **"`_exit_drain_cap` kept bash-local resolves the #230 overlap"** (AD-6's option (a)). Measured: it
   removes one of four conflict hunks and leaves three, all in ADR-0238.
9. **"Case 9's own delivery now rests on the trailing yield"** (an implication of AD-2). It does not —
   its drain finds `eof` already set (5/5), queues 0 callbacks in 12/12 probe calls, and the yield is
   unkillable on this shape in 45 measured runs. The yield is load-bearing on the *cap/idle* leg, which
   is what mutation row (h) says.
10. **"`_bounded` is what guards C.4 against a dropped cap"** (implicit in revision 1). It is not: the
    guard is the elapsed ceiling, and `_bounded` at `hold + 10.0` only limits how long the wrong answer
    takes to arrive.

---

## Open question carried into §G — only the owner can answer

**Should the tool say anything when it cuts a helper's output?** After this change a successful call
returns with nothing in the result indicating that a background process was still holding the pipe, and
because that output is block-buffered the model typically sees **none** of it (measured: `started`
reaches the pipe 4.018 s after the call returned). Pi says nothing either, so parity is not an argument
in either direction. The design proceeds on silence — that is the owner's choice A, and adding a notice
changes what every model sees on a path that is currently quiet — and §G carries the question with the
exact `gh issue create` **ORCHESTRATOR STEP** to open a follow-up if the answer is "yes, one line, e.g.
`[bash] a background process still held the output; N s of it was not captured`".

Two smaller items also need the owner, and both are in §G rather than decided here: whether
`run_cancellable` (the second tool spawn site, same shape, **not measured**) gets its own issue now, and
the landing order — the design says **#230 first** on measurement, but only the orchestrator can commit
to it.
