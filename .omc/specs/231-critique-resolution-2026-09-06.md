# #231 design — revision 2 resolution table

Base: `.omc/specs/231-design-2026-09-06.md` (revision 1, now overwritten in place by revision 2),
main `b94a6db`, worktree `/tmp/wt-231`. Three lenses (product-and-pi, tests-and-seams, docs) filed
**19** findings: 10 got a measured refuter pass, 5 were MINOR ("not refuted; apply if cheap"), and
on 4 the refuter died before returning a verdict (DOCS-3/4/5/6 — treated as UPHELD-pending and
applied conservatively). Codex unavailable, so every lens and every refuter in this round is Claude.

**Count: 9 UPHELD, 6 PARTIAL, 4 UPHELD-pending (refuter died), 0 wholly REFUTED.** No finding's
topic is dropped; what was refuted is individual sub-claims *inside* PARTIAL findings, and those are
listed at the bottom so the next round does not spend itself re-litigating them.

The revision's centre of gravity moved: revision 1 was "truthful docstrings + resolve the managed
fd". Revision 2 is that **plus two one-line code changes**, because two of the sentences revision 1
proposed to write were themselves false, and the cheapest way to write a true one was to make the
code true.

---

## Findings

| id | lens | sev | verdict | one-line evidence | disposition in revision 2 |
|---|---|---|---|---|---|
| PRODPI-1 | product-and-pi | BLOCKING | **UPHELD** (+ broader than filed) | `_enumerate_tree` post-filters fd only and `_walk_enumerate` prunes *dirnames*, so an excluded-name FILE diverges with no git repo in sight: symdiff `['.git','build','src/dist']`; `/tmp/wt-{231,226,234}` each walk 1458 vs fd 1457, the path being the `.git` pointer file | New **code change** §A.4: `_has_excluded_component` applied to whichever arm answered (one line in `_enumerate_tree`). §0 gains divergence #1; §B drafts the repaired `_has_excluded_component` docstring; §C case 6 (RED on main); ADR + CHANGELOG say the predicate is *repaired*, not preserved. Cost measured 3.9 ms / 12618 paths |
| PRODPI-2 | product-and-pi | BLOCKING | **UPHELD** | §E ran the TUI with cwd `/tmp/wt-231`, where all 35 `git status --ignored` entries are already in `_EXCLUDE_DIRS`; both arms' menus came out byte-identical (`diff` exit 0) so both "Must SEE" boxes tick on `main` too, and arm 2's expectation can never occur | §E rewritten: cwd is the owner's main checkout, branch code via `/tmp/wt-231/.venv/bin/aelix`; `C-l` before typing (without it the 50-row capture silently truncates); the pass condition is a **non-empty** `diff` of the two captures; both arms' expected rows replaced with the ones measured through the real TUI; `@settingsjson` added as the sharper observable; one sentence says why the worktree cannot host the check |
| DOCS-1 | docs | BLOCKING | **UPHELD** | A clean clone of `b94a6db` gives walk 1457 = fd 1457, symdiff **0**, unchanged after `uv sync` and a pytest run; `/tmp/pi-src` 1867 = 1867; the owner's 11136 walk-only paths are 81 % `.claude/worktrees` + `.omc` state, zero build artifacts | The figure leaves the README entirely (nothing reproducible replaces it). §0, §D-ADR and §D-CHANGELOG keep it **attributed** ("the owner's working copy") with the clean-clone control beside it and the drift noted (12606 → 12618 in one day). §A.2's "large visible improvement" justification struck. New §H rows M5c–M5f |
| DOCS-2 | docs | BLOCKING | **UPHELD** (one correction) | Outside a git repo fd still applies `.ignore` and `.fdignore` — including from a **parent** directory of the base — while the whole gitignore family (root, nested, `.git/info/exclude`, global `core.excludesFile`) goes inert; the design said "outside a git repo none of them do" | §A's rejected-(b) bullet, the ADR amendment and §C case 7's comment all restated on the narrower true fact; §H M3 split into M3/M3b. **Correction to the finding's own fix:** the global `core.excludesFile` is gitignore-family and must NOT be listed with `.ignore`/`.fdignore` as "applies anywhere" (measured) |
| PRODPI-3 | product-and-pi | MAJOR | **UPHELD** | With a fake `fd` on PATH and the managed copy present, `get_tool_path('fd')` (what `find` runs) and the design's PATH-first snippet return **different binaries**; Pi's `getToolPath` checks its own dir first, and the README sentence cited as the rationale is already contradicted by that resolver | §A.2/§B invert to **managed-bin-dir first, then PATH**; the README quotation is deleted as rationale. §B records why not `get_tool_path` itself (its PATH arm is an unbounded `subprocess.run([cmd,'--version'])` — 5333.9 ms against a sleeping PATH `fd`, on a path whose own spawn is capped at 2 s). §C cases 1/3 inverted, case 5 added as a drift pin (`_fd_binary() == get_tool_path('fd')`) |
| PRODPI-4 | product-and-pi | MAJOR | **UPHELD** (one correction) | Ignored 22 000-file `target/` beside twelve real source files: walk 20000 paths, ten of twelve real files gone, `@` menu **2 rows** vs fd's **12**; silent, and truncating is *faster*, so there is no latency signal either | New divergence §0.5, written into the `_walk_enumerate` and `_enumerate_tree` docstrings, the ADR, the README and the CHANGELOG; §C case 9 via `monkeypatch.setattr(completion,'_TREE_ENUM_CAP',8)`, asserting a strict subset and never *which* paths are lost. `_TREE_ENUM_CAP` struck from §G "not in scope". **Correction:** do not write "fd does not truncate" — `--max-results` bounds fd too, and its kept set is nondeterministic (1747/20000 differed between runs) |
| PRODPI-5 | product-and-pi | MAJOR | **UPHELD** (same defect as DOCS-1) | Same clean-clone control, plus the composition: `.claude` 10250 of 12618 (81 %), `.omc` 871; **nine** worktree copies, not three, one of them created by this very session | Resolved once with DOCS-1. Where the two proposed wordings differed, DOCS-1's wins for the README (no figure at all) and PRODPI-5's wins for §0/§H (attribution + control rows). PRODPI-5's own "8.5× on a checkout that carries build output" is **not** adopted: measured, `dist/`+`build/` add 0 |
| SEAMS-1 | tests-and-seams | MAJOR | **UPHELD** | `tests/tui/conftest.py`'s autouse `_isolate_agent_dir` sets `AELIX_CODING_AGENT_DIR`, which `get_agent_dir()` prefers over `Path.home()`: with the §B body installed, **17 calls → 17 bin-dir lookups → 0 hits**, and exporting the env var for the run does not change it | §C's "cases start spawning the real fd" paragraph replaced by the measured "why the suite cannot see the managed fd". §F relabels the `PATH=…` line as a smoke, not a gate on the new branch (17 PATH hits, 0 bin-dir lookups; unmodified `main` spawns the same 17 real `fd`s there). §H M10 retitled, M10b added. §G's "CI never exercises the fd arm" bullet rewritten to name what each case does cover |
| SEAMS-3 | tests-and-seams | MAJOR | **UPHELD** (fix corrected) | The comment shipped inside `_fd_binary` asserted a cycle that does not exist: hoisting the import imports cleanly from 12 entry orders and leaves `tests/tui` at 1534 passed; the same hoist in `util/shell_env.py` raises `ImportError … circular import`. And a module-level `from … import get_bin_dir` makes §C's monkeypatch **silently ineffective** | The import stays lazy but the comment now gives the measured reason (**the test seam**, M8b), explicitly denies a cycle here, and names `shell_env`'s real one. §H M8 amended, M8b added. §B adds why `get_bin_dir()` and not `get_shell_env()`. The `# noqa: PLC0415` is dropped (PL is not in this repo's ruff `select`) |
| DOCS-3 | docs | MAJOR | **UPHELD-pending** (refuter died) | `docs/decisions/README.md`'s 0193 row repeats the false sentence verbatim; #220/#221/#222 each moved the index row in the same commit — verified today, the row and issue #133 both exist as described | §D adds `docs/decisions/README.md`: correct the parenthetical, append the #231 landing the way the 0238 row records its follow-ons. The row's "owner-confirmed: fd = speed upgrade only" **stays as history** with `(2026-07; see the #231 amendment)` — an explicit decision, not left to the implementer |
| DOCS-4 | docs | MAJOR | **UPHELD-pending** (refuter died) | The drafted CHANGELOG asserted the walk renders a lone surrogate while §H M6 records the case could not be created (APFS `OSError 92`) and M6b records that encoding that str *raises* | CHANGELOG hedged to what is known (fd's `�` is measured; what the walk arm does is untested because no filesystem here can hold the name); the same wording in the `_enumerate_tree` docstring; §G's follow-up now says the open question is "renders differently, **or raises**" |
| DOCS-5 | docs | MAJOR | **UPHELD-pending** (refuter died) | Revision 1 committed to rewriting five docstrings and drafted one line of one of them — and three of the contract sentences it *did* write were wrong (DOCS-1/2/4), i.e. a measured error rate for writing this contract from memory | §B now drafts the load-bearing sentence of each docstring, each tied to the §H row that backs it, and pins the parts the implementer must not compress. A docstring-truth case is still not a test — §F instead schedules the three mutations whose cases are green on `main` |
| DOCS-6 | docs | MAJOR | **UPHELD-pending** (refuter died) | Re-measured here: fd in an `.hg` repo with `.hgignore: hidden-by-hg.md` **lists** `hidden-by-hg.md` — it is git's ignore machinery, not "your VCS", and `_EXCLUDE_DIRS` contains `.hg`/`.svn` so a reader has every reason to read it literally | README/README.ko say "the files git ignores"; "that is the one place the two differ" deleted everywhere (three differences survive). §H M12 added |
| SEAMS-2 | tests-and-seams | MAJOR→ | **PARTIAL** | Upheld: the seam was never named, `HOME` is defeated by the autouse fixture, and `monkeypatch.setattr(completion_mod,'get_bin_dir',…)` raises `AttributeError` under a function-local import. Refuted: four seams work, not two; the case cannot pass vacuously; the exec-bit is already in the design; the exec "hazard" degrades gracefully (`OSError 8` → walk) | §C's bounds paragraph names one seam — `monkeypatch.setattr(aelix_coding_agent.cli.config, "get_bin_dir", …)` — and says why it serves all five cases (case 4 needs `get_bin_dir` to *raise*, and no env value can). The rest of the finding is not adopted; see "do not re-raise" |
| SEAMS-5 | tests-and-seams | MINOR | **PARTIAL (adopted)** | On win32 `shutil.which` searches the process CWD before `path=` (3.11 unconditionally; 3.12 under `NeedCurrentDirectoryForExePath`), so the new negative assertions are sensitive to pytest's invocation dir — which the design claims to have eliminated | One sentence in §C's bounds: cases 1–3 `monkeypatch.chdir` into an empty dir. Its own correct half is kept too — neither 3.11 nor 3.12 matches a bare `fd` on win32, so case 2's `fd`/`fd.exe` arms hold on every leg. Recorded under "not measured" |
| PRODPI-6 | product-and-pi | MINOR | **PARTIAL (adopted)** | `grep` ensures `rg`, not `fd`; only `find` populates the bin dir with `fd`, so "the copy Aelix downloads for `find`/`grep`" also mis-sizes the remaining walk population | "for the `find` tool" everywhere in user-facing prose (the resolution *order* is still "the order `find` and `grep` use", which is accurate). The CHANGELOG gains the within-session consequence: after your first `find` fetches an fd, the menu can narrow at the next TTL expiry |
| PRODPI-7 | product-and-pi | MINOR | **PARTIAL (adopted)** | Read in `FileMentionCompleter`: `_list_directory` handles a `/`-containing prefix and is also the no-fuzzy-hit fallback, so an ignored file stays reachable by typing its directory | README/CHANGELOG say "stops **fuzzy-matching** the files git ignores (you can still reach them by typing the directory: `@build/`)". The Pi divergence gets its one clause in §G: Pi's argv is `--type f --type d --follow --hidden --exclude .git` — no `--type l`, and it follows symlinked dirs |
| SEAMS-4 | tests-and-seams | MINOR | **PARTIAL (adopted)** | Three §C cases are green on `main` and their value rested on mutation claims §H never recorded running; case 4's title asserted something about fd that its stub cannot show | §F schedules the three mutations by hand before the commit and says the table's mutation column is a reading, not a run. Case 4 renamed `test_the_walk_offers_git_ignored_files_that_the_stubbed_fd_hides` (now case 7) — the name says where the fd side comes from |
| DOCS-7 | docs | MINOR | **PARTIAL (adopted)** | `gh issue view 133` → OPEN, "completion dropdown overdraws the status bar" — the same defect §G told the implementer to file | §G points at **#133** and says to attach the §E capture there; "File it" removed, so no duplicate lands on a public repo |

---

## Conflicts between refuters, settled by re-measurement in this lane

1. **Does `--type l` make the arms "exactly" equal?** Revision 1's M4 said yes; PRODPI-1 showed that
   was a property of a tree with no excluded-name entries. Re-measured on a tree carrying symlinks
   *and* a `.git` file, a `build` file and a `dist` symlink: `--type l` alone → **False**;
   `--type l` **plus** the §A.4 walk filter → both arms
   `['broken','dirlink','filelink','loop','real','real/thing.py']`, **equal**. Both changes are
   needed for the claim, and revision 2 says so (M4b).
2. **Where does the walk's filter go — inside `_walk_enumerate`, or after it?** Measured with the
   cap lowered to 8: filtering after the cap means an excluded-name FILE still costs a slot. Chosen
   anyway, so `_has_excluded_component` remains *one* predicate at *one* site (its docstring's whole
   claim), with the cost written into `_walk_enumerate`'s docstring. Directory pruning — the part
   that matters for volume — is unchanged.
3. **Is fd "only ever a speed upgrade"?** DOCS-1 measured fd *slower* than the walk on every
   checkout but the owner's. Re-measured here under nine-lane load: `/tmp/wt-231` walk 13.6 ms vs fd
   46.9; `/tmp/pi-src` 21.4 vs 42.5; owner's checkout 53.0 vs 21.2 — same ordering as DOCS-1's quiet
   numbers, so the module header's claim falls on measurement, not on one noisy run.
4. **README figure: attribute it (PRODPI-5) or delete it (DOCS-1)?** Deleted from the README —
   nothing in this repository gives a reader a reproducible number — and attributed everywhere else
   (§0, ADR, CHANGELOG, §H), which is PRODPI-5's remedy applied where it is defensible.

---

## Do not re-raise — refuted sub-claims, with the reason

These were filed inside findings that are otherwise adopted. Each was measured false; the topic is
settled, and re-opening it costs a round.

1. **"Only two seams reach `get_bin_dir()`"** (SEAMS-2). Four do: `setenv AELIX_CODING_AGENT_DIR`,
   `setattr` on `cli.config`, `delenv` the fixture's var + `setenv HOME`, and staging into
   `tmp_path/"agent"/"bin"` with no seam at all (the fixture already points there).
2. **"Case 1 can accidentally pass while pinning nothing it staged"** (SEAMS-2). With the seam set
   and nothing staged, `_fd_binary()` is `None`; a non-executable staged `fd` is also `None`.
3. **"The staged fake `fd` must never reach `_enumerate_tree`"** (SEAMS-2). Executing the empty file
   raises `OSError [Errno 8]`, which `_fd_enumerate` catches, returning `None` → the walk answers
   with the correct list. Tidiness, not a hazard. (The exec-bit requirement it also raises was
   already in revision 1 and already measured, M7.)
4. **"fd does not truncate"** (PRODPI-4's own proposed wording). `--max-results 20000` bounds fd
   too, and when it bites fd's kept set is *not* reproducible: 1747 of 20000 paths differed between
   consecutive runs. The honest asymmetry is how the budget is *spent*, not who truncates.
5. **"`dist/` + `build/` make a checkout diverge"** (PRODPI-5's proposed README phrasing). Measured:
   60 files of build output added **0** to the gap, because both names are already in
   `_EXCLUDE_DIRS`.
6. **"The global `core.excludesFile` applies outside a git repo"** (implied by DOCS-2's proposed
   fix). It does not — it is gitignore-family and goes inert with the rest; only `.ignore` and
   `.fdignore` survive outside a repo.
7. **"`shutil.which('fd', path=…)` might match a bare `fd` on win32"** (the half SEAMS-5 checked and
   cleared). Neither 3.11 nor 3.12 does; case 2's `fd`/`fd.exe` arms are safe on every leg.
8. **The README sentence "copies already on your `PATH` are preferred"** is not evidence for
   PATH-first resolution (PRODPI-3). It is true as download-avoidance and false as an ordering
   claim, since `get_tool_path` prefers the managed copy. Revision 2 stops citing it; whether to
   qualify the sentence itself is left to a follow-up, not to #231.

---

## Open question carried into §G — only the owner can answer

(b′) makes the `@` menu **narrower on the owner's own machine**, and the owner is the one user here
whose checkout has an ignored population worth hiding. The design proceeds on "narrower is right"
(that is what fd users on every other machine already get, and what ADR-0193 intended), but the
durable answer may be a `/settings` toggle — "`@` menu: respect .gitignore" — rather than a silent
narrowing. §G carries it as the one open question, with `--no-ignore` named as the one-token
reversal if the owner disagrees after the §E live check.
