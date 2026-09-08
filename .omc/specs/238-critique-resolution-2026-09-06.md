# #238 design — revision 2 resolution table

Base: `.omc/specs/238-design-2026-09-06.md` (revision 1, now overwritten in place by revision 2),
main `7fa6796`, worktree `/tmp/wt-238`. Three lenses (liveness-and-cache, fd-semantics-and-tests,
docs-and-ux) filed **21** findings: 20 got a measured refuter pass and on 1 (DOCS-5) the refuter died
before returning a verdict, treated as UPHELD-pending and applied conservatively. Codex was available
again for this round; the cross-review lane's disposition is recorded per row where it moved one.

**Count: 7 UPHELD, 13 PARTIAL, 1 UPHELD-pending (refuter died), 0 wholly REFUTED.** No finding's
*topic* is dropped; what was refuted is individual sub-claims *inside* PARTIAL findings, and those are
listed at the bottom so the next round does not re-litigate them.

The revision's centre of gravity moved twice. Revision 1 was "a settings toggle plus an argv flag".
Revision 2 is that **plus a 78-cell help ceiling** — because the one sentence rev 1 counted on to
carry the whole decision to the user is measurably never rendered — **plus three forwarding hops that
no test in the design could see**, which two lenses independently proved would ship the row inert and
green. Both are the #84 defect class the row exists to avoid, arriving from opposite directions.

---

## Findings

| id | lens | sev | verdict | one-line evidence | disposition in revision 2 |
|---|---|---|---|---|---|
| DOCS-1 | docs-and-ux | BLOCKING | **UPHELD** | `_open_settings` hands `select` ONE unwrapped string; `Window.wrap_lines` defaults `False` and `_picker_frame` clamps only its *rules* to `_PICK_MAX_WIDTH` 78 — live at `tmux -x 80` / `-x 200`, the 272-cell `Render max width` help rendered as exactly 80 and 200 cells, cut mid-word, against 78-cell rules; rev 1's 337-cell help would put "Off →" at column 87 and "Applies live" at 291 | §B.5's help replaced by a **75-cell** string (measured with `context._visible_len`); §A.4's "the owner's sentence lives in the help text" struck and replaced by the measured reason; case ⑦ gains `_visible_len(row.help) <= _PICK_MAX_WIDTH`; §E gains `grep -c 'needs fd; live'` as a positive assertion; §G gains the 14-of-22 defect + ORCHESTRATOR STEP 3; §H M20. Fix (a) not (b) — wrapping inside `_open_settings` changes the modal height for all 22 rows against a bottom-truncating `_CappedContainer` (ADR-0159/0199), unmeasured, not this commit |
| DOCS-2 | docs-and-ux | BLOCKING | **UPHELD** (undercounted by one) | *"only the walk counts the ignored files toward the 20 000-path limit"* stands verbatim at README:178, README.ko:174, CHANGELOG:108, ADR-0193:167 **and twice in `completion.py`** (`_walk_enumerate` :369-370, `_enumerate_tree` :414-415); rev 1's §D only APPENDS, and its README sentence lands as the next sentence of the same paragraph; measured, `fd --no-ignore` emitted 20000/20000 and hit the cap on a 22 000-file ignored `target/` where the on arm emitted 14 | New **§D.0 "In-place corrections — these are EDITS, not appends"** with all six sites quoted old→new, plus the secondary `completion.py:404` "git's ignore rules are fd-only" header; the appended README/README.ko sentences lose their now-duplicated cap clause; the ADR correction follows the file's own `**Corrected …:**` convention (:56, :124) and carries the measured nuance that the off arm did **not** inherit the walk's lost-subtree half; §F greps for leftovers in both languages |
| LC-1 | liveness-and-cache | MAJOR | **UPHELD** | The refuter implemented §B verbatim in a sandbox, wrote §C's eleven cases, then deleted the forward inside `_build_input_completer`: **274 passed**, and ⑨ stayed green because `run_tui`'s nested `def` still contains an `ast.Call`. Widened: `tests/tui tests/pi_parity` 1975 passed, `ruff check packages/` clean (no `ARG` rules), pyright 0 errors | Resolved jointly with F1 (same defect, two lenses). §C gains cases ⑫–⑮ and four mutation rows; §C's preamble carries the measured "green while inert" paragraph; §B.4's nested-`def` comment now says the AST scan is necessary but **not sufficient** and names ⑫–⑮ as the actual guard |
| F1-wiring-untested | fd-semantics-and-tests | MAJOR | **UPHELD** (fix corrected in its own favour) | Independent sandbox: `grep -rn "_wire_descriptors\|_build_input_completer" tests/` → **0**. Three mutations (drop the kwarg at `_wire_descriptors`, at the fallback, or at all three hops leaving the nested `def`) each left **274 passed** / **2151 passed**. Its own proposed case (a) survived all three; only the two `run_tui` cases killed them, and only the `_BusExtRuntime` arm killed the descriptor mutation | F1's behavioural cases are taken over LC-1's AST-only pair, because they traverse the real hops and were measured to discriminate per hop: ⑫ builder, ⑬ `run_tui` descriptor arm, ⑭ `run_tui` fallback arm, in a new `tests/tui/test_completer_wiring.py`. LC-1's AST sweep is kept as ⑮ for the one thing the behavioural three cannot see — a *new* call site. §C Bounds → **< 300 ms**; §G records that these two functions had no coverage at all on `main` |
| LC-3 | liveness-and-cache | MAJOR | **UPHELD** (+ two corrections) | Its own refutation test found NO pull-mechanism marker in either docstring and both push assertions present. 9 rows are `live=True`; all 9 are shell-owned (6 `_apply_live_setting` branches + 3 `action` rows) — an exact 6↔6 bijection; the docstring parenthetical names **7**. Corrections: the list is stale by **two** rows, not one, and `SettingsRow.live`'s field doc goes false *whether or not* the list gains the key, so deleting rev 1's "gains it" instruction would not have fixed it | Resolved jointly with DOCS-4. §B.5's one-line "the module docstring's LIVE-row list gains it" becomes a **rewrite**: a two-mechanism PUSH/PULL split naming rows by key (drafted verbatim, both omissions repaired), the `SettingsRow.live` field doc amended, and case ⑯ machine-checks the partition against source by AST (RED on `main`: no `PUSH keys:` line exists). Two mutation rows added. LC-3's prototype `m5_partition_ast.py` is named as the implementation to lift |
| DOCS-4 | docs-and-ux | MAJOR | **UPHELD** (same defect as LC-3) | Same 7-vs-9 measurement, plus the one LC-3 missed: `_apply_live_setting`'s **opening comment** — *"Mirror a persisted dual-write row onto the LIVE session."* — goes false of its own new branch, and rev 1 drafted the branch comment but not that line. Also: `FileMentionCompleter(cwd)` is constructed inline inside `merge_completers([...])` and never bound, so the shell *could not* mirror even if it wanted to | Merged into LC-3's resolution; DOCS-4's extra edit (the `_apply_live_setting` opening comment) is added to §B.4, and its "never bound to a name" measurement is promoted into §A.3 as the reason the mirror alternative is *unavailable* rather than merely awkward. Where the two proposed docstrings differed, LC-3's machine-parseable PUSH/PULL split wins (DOCS-4's appended sentence is not parseable by a test) |
| DOCS-3 | docs-and-ux | MAJOR | **UPHELD** | §G claimed the cap hazard was documented "in the help text"; a widened grep of rev 1's help for `cap\|limit\|truncat\|20 ?000\|crowd\|spent\|budget\|quota\|max\|hazard` returned **0**, while both READMEs (which §G omitted) return 2 each. A help carrying a cap clause measures 401–474 cells | §G's bullet rewritten: documented in the **CHANGELOG, both READMEs and the ADR amendment**, deliberately not in the help, and §C ⑦ therefore pins no cap phrase. Resolved jointly with DOCS-1 as its rider demanded: DOCS-1 shortens the help to 75 cells, so the cap clause is *less* affordable, not more — the claim is struck, not delivered. The CHANGELOG's separate no-`fd` claim (which DOCS-3 correctly says is true) is kept, reworded per LC-2 |
| LC-2 | liveness-and-cache | MAJOR→MINOR | **PARTIAL** | Upheld: with no `fd` both flag values give an **identical menu** (measured at the completer level, with an fd control proving the fixture non-vacuous), and no-`fd` is first-class (`ensure_tool("fd")` is reached only by the `find` tool and returns `None` offline / on Android), so rev 1's unconditional "Applies live" over-promised. Refuted: "exactly the #84 shape" — by the repo's own measured definition (a production `ast.Call` of the getter) the design's wiring gives 1 call site against 0 on `main`, and the flag is honoured the instant `fd` appears, same session. Refuted in the fix: appending a caveat cannot work (the panel does not wrap) | The fd condition is carried, but by DOCS-1's mechanism, not LC-2's 221-char string: the 75-cell help reads *"Off → the @ menu also matches files the ignore files hide (needs fd; live)."* — condition immediately before the claim, whole string on screen at any supported width. Case ③ extended from the tree level to the **menu** level as LC-2 asked. §A.3 and the ADR gain the measured "no fd → no change until one arrives, then honoured at the next `@`" clause; the CHANGELOG says it in the file's own voice. §B.5 records why "default on" was the sentence dropped |
| F2-adr-exclude-causality | fd-semantics-and-tests | MAJOR→MINOR (+1 MAJOR test gap) | **PARTIAL** | Upheld: the clause needs a cap qualifier. **Refuted — its mechanism.** With `--exclude` gone AND the cap lifted, fd emitted 32 079, the predicate dropped 19 458, and what remained was **exactly** the walk's 12 621, symdiff empty both ways — so `_has_excluded_component` *is* what makes the sets equal and `--exclude` is the budget, the reverse of what the finding claimed. Its §A(1) sub-claim is refuted outright (§A(1) attributed the equality to `--no-ignore`, not to the predicate). Its quoted 6 801 is one draw: three consecutive runs gave symdiff 1 290 / 7 067 / 497 | The conclusion is adopted with the mechanism **inverted** relative to the finding's own fix text: §A.1, the `_fd_enumerate` docstring and the ADR amendment all say *predicate = filter, tokens = budget*, with the 32 079 / 12 621 / 20 000 numbers. The unstable draw is written as a range, never as one number. The finding's real contribution — the MAJOR half — is the test gap: case ① is strengthened from "`--exclude` count unchanged" (satisfied by 0 == 0) to **`argv.count("--exclude") == len(_EXCLUDE_DIRS)` on both arms**, and the mutation table splits one-sided from two-sided removal. §G gains the 63 % headroom figure |
| LC-4 | liveness-and-cache | MAJOR→MINOR | **PARTIAL** | Upheld: the read really does cross a thread boundary (measured: it lands on `asyncio_0`, never `MainThread`) and neither §B nor §C said so. Refuted: two of the three escapes it named are already caught — a memoizing getter and an `async def` getter both **fail case ⑩** as written; only a `get_global_settings()` deep-copy body passes it. Refuted: the 480× cost framing is immaterial (8.67 µs against a 522–5 249 µs fuzzy scan in the same call). Refuted: its proposed thread-identity assertion does **not** fire on the deepcopy body it targets, and would pin prompt_toolkit internals | The thread crossing is written into the `get_respect_gitignore` docstring and the `_get_tree` comment as a **cost** invariant, not a safety one. Case ④ stays synchronous (LC-4's async rewrite rejected, measured not to fire). New case **⑩b** — monkeypatch `get_global_settings`/`get_settings` to raise, assert the getter still answers — which the refuter measured killing the deepcopy body and passing the designed one. Mutation table records that a memoizing or `async` getter needs no new case |
| DOCS-5 | docs-and-ux | MAJOR | **UPHELD-pending** (refuter died) | §E writes `respectGitignore` into the owner's real `~/.aelix/agent/settings.json` with no backup, and `AELIX_CODING_AGENT_DIR` cannot isolate it because it moves the settings file and `get_bin_dir()` together, collapsing the check to the walk arm | Applied conservatively, the backup form rather than the env form: §E opens with `cp ~/.aelix/agent/settings.json $S/settings.before.json` + a `trap … EXIT` restore, and closes with a `diff` of before/after so the drive says what it changed. `AELIX_SETTINGS_PATH` exists (`settings/storage.py`, `settings_manager.py`) and would move only the settings file — **not adopted**, because the same file documents it as self-elevating when pointed at a repo-controlled path, and the backup form needs no such judgement |
| DOCS-7 | docs-and-ux | MINOR | **PARTIAL (adopted, widened)** | M15 (`Check for updates` is a dead row) makes a user-facing sentence false in both languages, and rev 1's ORCHESTRATOR STEP 2 body never said so | Located and named: **`README.md:38`** and **`README.ko.md:38`** ("`/settings` turns it off"). Written into §G's M15 bullet and into STEP 2's issue body with "fix the docs in the same commit". Widened beyond the finding: LC-3's adjacent measurement — `enable_skill_commands` is the same defect inverted (`get_enable_skill_commands()` is re-read inside `_input_loop`'s `while` off the SettingsManager `_open_settings` mutates, while its help, `apply_note` and source comment all say "next launch") — is folded into the same issue body, flagged as statically measured only |
| DOCS-8 | docs-and-ux | MINOR | **PARTIAL (adopted)** | The rev-1 help mixed "files the ignore files hide" with "files git ignores", and §C ⑦ pinned the second — the phrasing §A.1 had itself measured wrong | The 75-cell help uses **"files the ignore files hide"** only, and ⑦ pins that phrase plus `"Off →"` and `"needs fd"`. "Files git ignores" survives only in the README/CHANGELOG prose, where the surrounding sentences define it |
| DOCS-9 | docs-and-ux | MINOR | **PARTIAL (adopted)** | `@ menu gitignore` would be the only lowercase, symbol-initial label among 22 capitalised ones | Label is **`Gitignore in @ menu`** — 19 chars, so the column stays at 24 (M11 re-measured), capitalised like every sibling, and `select`'s case-insensitive substring filter still reaches it on `gitignore`. Changed everywhere: §B.5, §D's ADR/README/README.ko/CHANGELOG drafts, §E's expected commit line |
| LC-7 | liveness-and-cache | MINOR | **PARTIAL (adopted, by deletion)** | Rev 1's reason for widening the tree-cache bound from `> 4` to `> 8` ("the old bound held one cwd where it used to hold two") is wrong on both numbers, and the bound is unreachable in production | The widening is **dropped entirely** — no diff, no case. Re-measured here: the guard is `if len(self._tree_cache) > 4`, keyed by `str(base)`; with the flag in the key one or two cwds give at most 4 entries, so it never fires. §B.3 says so in three lines instead of changing the code |
| F4-argv-position-contradiction | fd-semantics-and-tests | MINOR | **PARTIAL (adopted)** | §B.3's prose put the token before the `--exclude` block; its snippet appended it after `--max-results`, and §C forbids index pins so the reader had no tiebreaker | The **snippet** is corrected, not the prose: the token goes right after `--color never`, keeping both the exclude block and the `--max-results` tail contiguous. Verified against the real `_fd_enumerate` argv order on `main` |
| F3-m7-timing-inverted | fd-semantics-and-tests | MINOR | **PARTIAL** | Upheld: M7's absolute numbers were stale (the tree grew). **Not reproduced here:** re-measured in this lane, 7 interleaved rounds, fd-off median **24 ms** vs walk **29 ms** — rev 1's ordering, not the finding's inversion | Settled by re-measurement rather than by adopting either version: §0 and M7 carry today's numbers **and** the disagreement, and the load-bearing prose ("flipping the toggle off is cheaper than having no fd") is deleted regardless, because the two arms are within 20 % and the ordering is load-dependent. No design claim now rests on it |
| F5-semantics-unpinned-by-any-test | fd-semantics-and-tests | MINOR | **PARTIAL (adopted)** | Every fd case goes through `_stub_fd`, whose stdout ignores argv, so ①'s "`--no-ignore` not `--no-ignore-vcs`" compares a token against the design's own belief; rev 1's "Not measured" paragraph covered only win32 | §G gains the bullet in the finding's own terms, naming M1–M4 as the only evidence for the semantics and noting that an fd version bump could invalidate it silently. Not fixable under the no-real-fd-on-CI and no-skipif rules; stated rather than papered over |
| LC-5 | liveness-and-cache | MINOR | **PARTIAL (adopted)** | §E's tmux drive cannot discriminate a keyed cache from an unkeyed one — a `C-l` and three seconds pass, so a 2 s TTL would have expired anyway | §E gains an explicit **"What this drive does NOT evidence"** paragraph: it evidences "no restart is needed"; case ⑤ is the evidence for the key. §A.3's tighter claim now cites M8 and ⑤, never §E |
| LC-6 | liveness-and-cache | MINOR | **PARTIAL (adopted)** | `diff $S/1-on.txt $S/5-on-again.txt` diffs the whole pane, which by capture 5 also carries the two green commit lines the toggles wrote — a correct run can produce a non-empty diff | §E filters both captures to the menu region (`grep '\.json'`) before diffing, and the prose says why a whole-pane diff is non-empty on a correct run |
| DOCS-6 | docs-and-ux | MINOR | **PARTIAL (adopted)** | ADR-0193's Status line is a sentence about #231's five-way falsity; appending `+ #238` to it would date #238 to 2026-09-06 and attribute #231's finding to it | §D.1 says explicitly **not** to append to that clause and drafts a second sentence instead ("Further **AMENDED 2026-09-07 by #238** …"). Verified against the real Status line at `docs/decisions/0193-…md:3-7` |

---

## Conflicts between refuters, settled by re-measurement in this lane

1. **Is the off arm faster or slower than the walk?** Rev 1's M7 said faster (16 vs 23 ms); F3
   measured it consistently slower. Re-measured here, 7 interleaved rounds on the owner's checkout:
   fd-on 11 ms, fd-off **24 ms**, walk **29 ms** — rev 1's ordering. Both lanes are right about their
   own box-load; neither ordering is a fact the design may state, so the claim is deleted and M7 now
   records the disagreement instead of a winner.
2. **What makes the off arm equal to the walk — the predicate or the `--exclude` tokens?** F2 said
   the tokens; its own probe's fourth arm (no `--exclude`, cap lifted) proved the predicate: 32 079 →
   19 458 dropped → 12 621, symdiff 0 against the walk. Adopted with the causality inverted from the
   finding's fix text, and the *conclusion* (a cap qualifier, and an absolute `--exclude` count pin)
   kept, because that is the half that can actually ship a defect.
3. **Which cases pin the forwarding — AST (LC-1) or behavioural (F1)?** Both refuters measured their
   own proposals killing the mutations. F1's three drive the real `run_tui` on both of its branches
   and were measured to discriminate per hop (⑬ fails only for the descriptor arm, ⑭ only for the
   fallback), so they are the primary; LC-1's AST sweep is kept as ⑮ for the one thing behaviour
   cannot cover — a *new* call site added later without the keyword. Neither alone is sufficient:
   F1 measured its own builder case surviving every call-site mutation.
4. **How is the help fixed — shorten (DOCS-1) or condition-first (LC-2)?** LC-2's 221-char candidate
   assumed the reader sees the first 80 columns; DOCS-1 measured that the panel is cut at the *pane*
   width with 78-cell rules, so an 80-column reader sees 80 cells of a 221-cell string and nothing
   else. DOCS-1's mechanism wins, LC-2's content requirement wins: a **75-cell** string that carries
   the fd condition and the live claim in that order. DOCS-3 is resolved against the same string
   (the cap clause is struck from §G, not added to the help), as its own rider required.
5. **Rewrite the LIVE docstring (LC-3) or append to it (DOCS-4)?** LC-3's parseable PUSH/PULL split
   wins because it is the only version case ⑯ can check, and it repairs both existing omissions;
   DOCS-4's `_apply_live_setting` opening-comment edit and its "never bound to a name" measurement
   are folded in, neither being in LC-3.

---

## Do not re-raise — sub-claims measured false in this round

- ❌ "`--exclude` is what makes the off arm equal to the walk's set" (F2's mechanism) — the predicate
  is; with the tokens gone and the cap lifted, 32 079 paths filter to exactly the walk's 12 621,
  symdiff 0. The tokens are the *budget* (12 621 of 20 000 with them, 32 079 without).
- ❌ "§A(1) conflates the predicate with the equality" (F2) — §A(1) attributed it to `--no-ignore`.
- ❌ "the off-arm/walk symmetric difference is 6 801" (F2's fix text) — one draw; three consecutive
  runs gave 1 290 / 7 067 / 497, and consecutive runs differ from *each other* by ~6 500. Never quote
  a single number for a truncated fd set.
- ❌ "the off arm is consistently slower than the walk" (F3) — not on this box: 24 ms vs 29 ms over 7
  interleaved rounds. Nor is the reverse safe to assert.
- ❌ "the toggle with no `fd` is exactly the #84 shape" (LC-2) — by the repo's own measured definition
  (a production `ast.Call` of the getter) the wiring gives 1 site against 0 on `main`, and the value
  is honoured the moment an `fd` appears in the same session. It is the *copy* that was dishonest.
- ❌ "appending a caveat to the help text fixes the over-promise" (LC-2) — the panel does not wrap;
  rev 1's live clause already started at column 291.
- ❌ "a memoizing or `async def` getter would escape §C" (LC-4) — measured, both fail case ⑩ as
  written. Only a `get_global_settings()` deep-copy body escapes, and ⑩b is what catches it.
- ❌ "a thread-identity assertion inside case ④ pins the getter's body" (LC-4) — measured, it holds
  under the deepcopy getter it was proposed to catch, and it would pin prompt_toolkit internals.
- ❌ "the deep-copy getter is a safety problem" (LC-4) — 200 concurrent read/flip rounds, 0 errors for
  both bodies; `reload()` rebinds rather than mutates. It is a cost problem, 8.67 µs vs 0.018 µs.
- ❌ "case ⑫ (the builder) is sufficient" (LC-1's and F1's own first proposal) — measured green under
  every call-site mutation; ⑬/⑭ are load-bearing and ⑮ covers new sites.
- ❌ "the `ast.Call` scanner (case ⑨) protects this row from going inert" (rev 1's §B.4 comment) —
  measured green with the keyword deleted at all three hops.
- ❌ "the LIVE-row docstring list is stale by one row" (LC-3's own filing) — by **two**:
  `hide_compaction_summary` and `render_max_width`.
- ❌ "deleting rev 1's 'the docstring list gains it' instruction would fix the falsity" (LC-3's
  framing) — `SettingsRow.live`'s field doc is a universal claim over `live=True` rows and goes false
  regardless; both sentences must be amended.
- ❌ "the cache bound must widen from `> 4` to `> 8`" (rev 1) — the bound is unreachable in
  production and rev 1's arithmetic was backwards (4 entries held four cwds, now two at both values).
- ❌ "§E step 2 cannot be verified as written" (DOCS-1's one PARTIAL half) — as literally written it
  asserts nothing, so nothing in it is falsified; its *comment* was wrong, and §E now carries a real
  assertion instead.
