# Issue 287 handoff — 2026-10-09

## Baseline and canonical plan

- Current base main SHA: 6b5ffc814041de36866f79df71c51992ec76891c.
- Original validation base: e47f1101f8b7499125ff55f0ee18d8b9873d8cdf.
  Main moved during parallel work; the single issue commit rebased cleanly onto
  6b5ffc81 with no conflicts. The full raw run below predates that rebase.
- Worktree: /Users/handochan/dev/aelix-ai-worktrees/codex-287.
- Branch: feat/287-system-prompt-files.
- Plan: .omc/specs/plan-287-system-prompt-files-2026-10-09.md.
- Decision: docs/decisions/0257-system-prompt-file-discovery.md.
- Pi remote HEAD checked read-only: ce950d78f424dcaf9f5d6a03ce80ab141130eb1d;
  source loaded through GitHub API, local /Users/handochan/dev/pi unchanged.

## Implemented

Trust-aware per-build SYSTEM.md / APPEND_SYSTEM.md discovery, explicit/profile
precedence, append composition before AGENTS context, loaded-source banner
paths, failure/BOM/blank handling and documented child explicit-flag behavior.
Kernel behavior unchanged. 32 focused discovery cases; default-global loader
test isolation; deliberate product-core size-bound allowlist entry. README in
both languages, changelog, guides, ADR and historical deferred items updated.
Bundled guides synchronized; shifted code citations repaired as required.

## Verification and evidence

- uv sync: local editable packages point to this isolated worktree.
- uv run ruff check .: All checks passed (.omc/probes/287/ruff-final.log).
- uv run pyright: 0 errors, 1 existing SessionStorage Protocol variance warning
  (.omc/probes/287/pyright-final.log).
- uv run ruff format --check for the new loader/test: 2 files already formatted.
  Only changed hunks in existing entry/shell were formatted; wholesale baseline
  formatting was deliberately reverted because it introduced unrelated changes.
- uv run python scripts/check_citations.py --check: 953 gated, none drifted,
  19 existing ambiguous anchors (.omc/probes/287/citations-final.log).
- uv run python scripts/sync_bundled_docs.py: 9 guides, 0 pending updates.
- Final focused tests run with NO_COLOR unset and TERM=xterm-256color:
  .omc/probes/287/focused-final.log. Includes discovery/trust, actual prompt
  composition, profile switches, banner/context, citation lock, bundled docs
  and a real wheel's guide-link gate: **214 passed in 8.54s**.
- Full `uv run pytest -q` completed: **59 failed, 14,453 passed, 45 skipped,
  104 warnings, 3 errors in 904.40s** (.omc/probes/287/full-pytest-final.log).
  This is the raw result, not a full pass. 58 failures came from inherited
  NO_COLOR=1 / TERM=dumb (root independently reproduced them on baseline);
  three errors came from scaffold builds falling back to system Python3.9.
  Installed pip26.2.1 and hatchling1.32.4 in this worktree's .venv with
  `uv pip install pip hatchling`, then used `uv run --no-sync` so sync does
  not remove those build helpers. The remaining failure was the explicit
  router test double refusing the new prompt_file_paths callback; its named
  signature was extended, and the test now supplies isolated SYSTEM.md and
  asserts the callback delivers that actual loaded path. No production change.
- All **62 original failed/error nodes passed in 5.13s** when rerun with
  NO_COLOR unset, TERM=xterm-256color and the corrected test double / build env.
  Exact nodes: .omc/probes/287/failed-nodeids.txt; complete output:
  .omc/probes/287/corrected-all-failures-final.log. Rerun command was
  `uv run --no-sync pytest -q <the 62 listed node IDs>` in that corrected env.
- Entire router test module: **34 passed in 1.54s** in the corrected env
  (.omc/probes/287/router-final.log). Final ruff, pyright and citation gate
  rechecked after that test-only fix with the same results stated above.
- After rebasing onto 6b5ffc81, the final corrected-env focused gate (all the
  previous focused modules plus the router module) passed **248 tests in
  8.54s** (.omc/probes/287/focused-rebased.log). Ruff remained clean, pyright
  stayed at 0 errors/1 baseline warning, citations remained 953/none drifted,
  and bundled docs had zero pending updates. No new-base full run is claimed;
  root's fresh CI covers the full suite on current main including Python3.13.

Live verification used copied OAuth auth in a private temporary global directory;
no real global settings/trust/profile files were edited. All captured request
files contain ONLY model and instructions, never authorization headers/tokens.

- openai-codex / gpt-5.5 real print run returned 42; instructions begins global
  SYSTEM marker and contains global APPEND. global-live-requests.jsonl and
  global-live-output.txt under .omc/probes/287/.
- Real default-deny and --approve print runs returned 42. Captured instructions
  prove denial selects global files, approval selects project files, and the
  append precedes AGENTS in both. default-deny-requests.jsonl / output.txt and
  approved-project-requests.jsonl / output.txt.
- Direct uv run aelix TUI startup visibly lists SYSTEM and APPEND paths in the
  Context row. Real TUI turns returned 42. Editing files then /reload changed
  request V1 -> V2; editing again then persisted-session /new changed V2 -> V3.
  tui-live-requests.jsonl and tui-persisted.typescript preserve evidence.
- A real OAuth/gpt-5.5 print request was repeated after the rebase and returned
  42. It still begins with the global SYSTEM marker under default headless
  denial, places global APPEND before AGENTS and excludes project SYSTEM.
  rebased-global-live-requests.jsonl / rebased-global-live-output.txt.

Independent review: issue_197 agent approved the change, checked trust,
precedence, child flags, rebuilds and live provenance; 67 focused + 36
composition cases passed. Root received approval. No unresolved product defect.

## Start here / subsequent order

Root published the initial commit 57bf404b to start CI, then coordinated the
verified router-test-only fix and final handoff. Main moved to 6b5ffc81, so
root superseded its temporary uncommitted-delta request with an instruction to
amend and rebase locally, preserving one issue commit. That rebase is complete;
root owns independent inspection and --force-with-lease push on this task-owned
branch, updates the PR's final local evidence and Project, and watches fresh
CI including the issue's own Windows/Python3.13 legs. Do not merge without
user authorization. Root owns all PR/issue/Project writes; this agent did none.
Preserve all worktrees: original checkout has active Claude Code work.

## Rules that mattered

Only this isolated tree was edited. Kernel dependency boundaries held. TUI was
run directly and runtime changes were verified against a real model. Source
citation --fix relocated 146 citations in 44 files; four edited anchors were
read and corrected manually. One shell comment anchor had a corrected nested
source citation and was explicitly re-locked in place; see citation-fix*.log
and citation-edited-anchor-lock.log. Guides linking ADRs use absolute GitHub
URLs because relative ../decisions paths break installed wheel users.

## Disproved assumptions — do not believe again

- The local Pi HEAD was latest: it was 6671c604, remote was ce950d78.
- Existing files can be fully Ruff-formatted without unrelated changes: baseline
  contains formatting differences; use changed-range formatting.
- Public MAX_PROMPT_FILE_BYTES needs no structural gate entry: the exact-set
  product-core cap gate requires deliberate non-delegation justification.
- The default test global directory is harmless: a developer's SYSTEM file
  would now influence general harness tests, so the default loader is isolated.
- A copied relative ADR guide link works after packaging: the wheel excludes
  docs/decisions; use an absolute URL and run the real-wheel docs gate.
- /new can be validated with --no-session: baseline memory session has no cwd
  and refuses /new. A real persisted isolated session validates the path.
- NO_COLOR=1 / TERM=dumb establishes renderer correctness: root measured
  baseline failures from those inherited environment values; correct both.
- A live TUI pass proves every strict router double accepts a new kwarg: the
  real TUI worked, but the router signature double intentionally rejected
  prompt_file_paths. Its explicit signature and a provenance assertion were
  updated; do not replace this guard with permissive **kwargs.

## 2026-10-09 fresh review repair — loader boundary

Independent review found a P2 race in the discovery reader: checking a pathname
with `stat()` did not constrain the later `Path.read_text()`. Concurrent growth
was accepted above 1 MiB, and substituting a FIFO after the check froze both
SYSTEM.md and APPEND_SYSTEM.md discovery. The original cap/nonregular claims
were therefore too strong; do not use the earlier happy-path results as proof
that these races were covered.

Repair remains in this issue's author tree at uncommitted base `51b9c2d5`.
The loader opens once with nonblocking mode where supported and binary mode on
Windows, validates that descriptor using `fstat`, and reads at most the byte
limit plus one byte before UTF-8/BOM decoding. Growth beyond the cap is rejected.
The loader retains descriptor ownership and closes it in `finally`, including
`fdopen` failure. CRLF/CR normalize to LF, preserving the previous reader.
Missing files and dangling symlinks still fall through to global; selected blank,
unreadable, nonregular, invalid UTF-8 or oversized project files still mask it.

Regression evidence is `.omc/specs/287-loader-race-repair-evidence/`:

- Old-code run `pytest -q tests/cli/test_system_prompt_files.py -k descriptor_race`:
  **4 failed** — two actual growth schedules and two FIFO subprocess timeouts.
  Fixed loader passes these inputs. Tests also cover post-open pathname swap,
  real `os.pipe()` descriptor rejection on all platforms, descriptor cleanup
  through fstat/fdopen/read/decode/size failure and success, absent O_NONBLOCK,
  exact byte/BOM/UTF-8 limits and universal newlines. Timing uses explicit check
  and open boundaries, not sleeps or a probabilistic filesystem race.
- Final corrected-terminal focused command recorded in `focused.txt`:
  **427 passed in 9.51s**. Includes discovery, project trust, prompt flags,
  live prompt rebuilding, profile overlay, child trust, runtime lifecycle,
  RPC/CLI Windows shims, TUI provenance and citation regressions.
- `uv run ruff check .`: **All checks passed**. `uv run pyright`: **0 errors**,
  one inherited SessionStorage covariance warning.
- Citation `--check` → `--fix` → `--check`: **953 gated, none drifted** before
  and after. `sync_bundled_docs.py --check`: **9 guides, 0 updated, 0 removed**.
- Full suite and native Windows were **not run** in this repair pass. Windows
  shims and portable pipe cases are local evidence, not native Windows proof.
- Fixed-code live smoke: OpenRouter `openai/gpt-4.1-mini`, own scratch global
  agent/settings/auth paths. Print runs with project denial and approval both
  exited **0**, response **REVIEW_OK**; actual request system bodies confirm
  global versus project selection and append before AGENTS. Actual TUI showed
  global SYSTEM → APPEND_SYSTEM → AGENTS paths, streamed **REVIEW_OK**, and
  exited **0** via `/quit`. Captured only system text, no headers or credentials;
  parsed only the required OPENROUTER_API_KEY from .env without printing or
  persisting it. The owner's auth store was untouched.

Implementation pass is complete; independent acceptance review belongs to the
parent's fresh verification pass. No commit, push, remote edit, merge or main
write was performed by the repair agent. Product source was frozen after the
focused/lint/type/citation checks; subsequent work only records evidence.

## Independent final byte-bound acceptance

No-author-context verifier found BufferedReader read-ahead violated the physicalcap+1claim even though oversizedpromptcontent wasrefused. Rootswitchedonly to unbufferedbinaryread withremaining-budget short-readloop; newrealFDoffset regressions2pass (oldbufferedrepair2fail,4096vs17 on16bytecap). Finalfrozenrecheck:206focusedpass,96/96I/Omatrix,8/8short-read/device-refusal,4/4FIFO old/new comparisons; UTF8/BOM/CRLFsplit handling, missing/dangling/masking/trust andFDcleanup preserved. NoTUIdepsbarevenv loader/CLIhelp works. Finalsource/test hashes match verifier; source/gates clean withoneinheritedtypewarning. Verifier's current-main synthesis needs citation integration work; rootwilldoactualtargets beforefix. FullplatformCI onorderedfinalhead required.

## Final ordered base and root live check

Rebasedonto#423predecessor763e02d7 (allpriororderedissuesincluded). SevenconflictswereindependentADRindexrowsorcomment/citationnumbers; both0258/0257rowsretained. Actualcitationtargets inspected;defaultfalseprofileclause narrowedto itsactualfieldline199ratherthanrelockinganediteddocblock;934gatednone drifted. Currentbranchlintclean,type288files0errors/inverse spike preserved,212combinedprompt/profile/childtrust/session/retry/boundarytests passed2.59s. RootdirectlyexecuteduvrunTUI onthefinalstack inisolateddirs withknownSYSTEM/APPENDfixtures andone.envkey:menus/Tab/actualOpenrouter4ominiLIVE_OK,/quitexit0in0.141s;sourceprovenance/runtimeconstruction preserved. Earlierliveattemptisnotusedasfinalintegrationevidencebecausecheckoutrebasedconcurrently;this finalrerunranafterallproductwritesceased. PlatformfullCI requiredbeforemerge.

FreshClaude finalsource-onlyreview foundno mainloaderboundarydefect; one inheritedmissingcandidateedge(ENOTDIRparent.aelix file) contradictedmissingfallback wording. RoothandlesENOTDIRlikeENOENTandaddedbothfilenameactualfile/globalfallback/no-warningregressions. All55loader tests pass; no trust/byte/descriptorbehaviorchanged. This finaltinyedgewillgetfreshCI withtheamendedhead;firstpostedheadisnotfinalvalidation.

Finalcombinedfullsuiteat432(old420parent) had15389pass25skipand1obsoleteexpectedopen-listassertion insettings-onlytrustpositivecontrol:descriptorloaderattemptedmissingSYSTEM/APPENDaftertrustgranted. Rootupdatedonlythetesttopreserveexactsettingspositivecontrol/knownresourceallowlist/zerodenialattempts.102provider-trust+loadercasespass; independentfinaldelta checkrequested. Noadditionalproductionchange; finalplatformCI/combinedsuitewillrecheckthishead.
