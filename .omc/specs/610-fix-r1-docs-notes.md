# #610 fix round 1 — lane R1-docs notes (2026-10-30)

Task list: `610-progress-2026-10-30.md`, "문서 (레인 R1-docs, R1-code 뒤)", items D1–D12.
The code these docs describe: `0b4bb4cf` (lane R1-code), read through `git show HEAD`, `610-fix-r1-code-notes.md` and the code itself.

## Base

- Worktree `~/dev/aelix-ai-worktrees/claude-610`, branch `feat/610-agent-resume`.
- HEAD `0b4bb4cf` at the start and right before the commit (`git rev-parse HEAD` → `0b4bb4cf6ff713aca4119c97715fda98d52a92c3` both times).
- `env -u VIRTUAL_ENV uv run --no-sync python -c "import aelix_agents; print(aelix_agents.__file__)"` → `…/claude-610/packages/aelix-coding-agent/src/aelix_agents/__init__.py`, Python 3.12.13.
- The main checkout `~/dev/aelix-ai` was not edited: `git -C ~/dev/aelix-ai status --short | grep -v '^??'` printed nothing.
- No code or test file was changed. Files changed:
  - `docs/decisions/0277-…md`
  - `docs/decisions/0243-…md` and `docs/decisions/0276-…md` (the dated #610 notes only)
  - `docs/decisions/README.md` (the 0277 row only)
  - `docs/guides/agent-profiles.md` and its bundled copy
  - `CHANGELOG.md` (the #610 entry only)
- Every hunk sits in a #610 passage (`git diff -U0 … | grep '^@@'`).

## Strings: read from the code, not from memory

Every quoted model-facing string was printed from the code with `/tmp/610-r1-docs/strings.py` (imports the constants and prints `repr`), then copied. The output, verbatim, for the ones the docs quote:

```text
cs.RESUME_CONVERSATION_CHANGED = "Resume refused: the conversation changed before the resume could start, so that agent is not this conversation's to resume. Delegate the task anew."
cs.RESUME_CWD_GONE = "Resume refused: the directory that agent ran in no longer exists, was moved or replaced, or is no longer inside this conversation's working directory. Delegate the task anew."
cs.RESUME_DAMAGED = "Resume refused: that agent's session record is damaged ({detail}). Delegate the task anew."
cs.RESUME_LOCK_ERROR = "Resume refused: aelix could not check whether that agent's session is in use ({error}). Delegate the task anew."
cs.RESUME_NEEDS_ID = 'Resume refused: action="resume" needs id — the Session id of an agent this conversation delegated, as its result shows it: on its "Session:" line, or after "session" in a batch member\'s "did not finish" heading.'
cs.RESUME_NO_SESSION = "Resume refused: this conversation is not saved in aelix's sessions directory (--no-session, or a file opened from elsewhere), so no agent it delegated has a session to continue. Delegate the task anew."
cs.RESUME_UNKNOWN_ID = 'Resume refused: no agent this conversation delegated has that id. Use the id the agent\'s result shows: on its "Session:" line, or after "session" in a batch member\'s "did not finish" heading.'
cs.RESUME_UNREADABLE = "Resume refused: this conversation's record could not be read, so that agent cannot be found. Delegate the task anew."
cs.RESUME_UNSUPPORTED_CHANNEL = 'Resume refused: this session delegates through a channel that cannot resume an agent inside the limits it ran with. Delegate the task anew.'
tool.RESUME_GUIDANCE = 'Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls; if it should go on, continue it with agent(action="resume", profile={profile}, id={id}, task=…).'
tool._RESUME_TASK_HAS_NUL = "'task' holds a NUL character (U+0000), which cannot be sent to the agent. Resend the instruction without it."
tool._RESUME_ID_IS_A_PATH = '\'id\' is the agent\'s Session id — the id its result shows, on its "Session:" line or after "session" in a batch member\'s "did not finish" heading — not a file path.'
aggregate.CANCEL_REPORT_HEAD = '[aelix system report — not user input] The `agent` call you made was stopped when the turn was cancelled. The stop was intended: resume or repeat that work only if the user asks you to. What it had done:'
runtime._TARGET_CHANGED = 'its record changed before the resume could start'
head bytes: 1598
prop id: 'action=resume only: which agent to continue — the id its result shows, on its "Session:" line or after "session" in a batch member\'s "did not finish" heading. Not a file path.'
```

The `_DESCRIPTION_HEAD` last paragraph quoted in ADR-0277 D3 was copied from the same output.

Other values were read from the code as well:

- The resume disclosure, `resuming {name} at {clamped.value}, from {source}`, from `consent.build_disclosure_line`.
- The `Permission:` row suffix `f" (first run: {…first_grant.value})"`, from `consent.build_resume_consent_title`.
- The six damage details, from `child_session._lineage`, `child_session._check_child_file` and `runtime._TARGET_CHANGED`.
- The loader's header rule, from `jsonl_storage._parse_header_line`:
  - `type == "session"`, `version == 3`;
  - a non-empty `id`, `timestamp` and `cwd`;
  - `parentSession` null or a string.

Numbers rechecked by import:

- preamble plus separator: 290 bytes;
- the description head: 1 598 bytes;
- `RESUME_TASK_MAX_BYTES`: 64 512;
- `LISTED_AGENTS`: 10; `LISTING_BYTES`: 2 048;
- `CHILD_SCAN_BYTES`: 2 097 152.

Facts probed rather than read:

- `parse_agent_call({"profile": "scout", "task": "keep\x00going"})` parses (a `run` takes a NUL). The same with `action="resume"` raises the NUL refusal. A `run` task with a lone surrogate raises `UnicodeEncodeError` (`/tmp/610-r1-docs/run_nul_probe.py`).
- At `f3e76fbb`, `tool.py` and `chain.py` contain no NUL handling (`git show f3e76fbb:… | grep 'x00\|NUL\|null byte'` → nothing).
- `child_model_pin(profile without model, parent_model=None)` → `ModelPin(None, None, None)` and no flags; `ChildLimits.to_record` writes that as `model: null` (`/tmp/610-r1-docs/pin_probe.py`).
- `get_agent_dir()` returns a relative `AELIX_CODING_AGENT_DIR` as given (no `abspath`), and discovery's `_scan_dir` records `str(entry)` under it. So a relative agent dir gives a relative `source_path`, the F15 rationale.

## Per item

### D1 — CHANGELOG refusal list

- Was: "`tasks`, `mode`, `cwd` and `background` are refused on a resume".
- Now: "A resume refuses a non-empty `tasks`, a `mode` other than `single`, any `cwd` and `background: true`; a `run` refuses an `id`; …".
- Checked against `tool._parse_resume`:
  - `tasks` is refused unless `_absent` (`null`, `""`, `[]`);
  - `mode` is accepted when `None` or `"single"`;
  - `cwd` is refused when `is not None`;
  - `background` is refused when truthy.
- ADR-0277's D11 table uses the exact spellings ("a `tasks` that is not absent", "a truthy `background`"), as D1 there does.

### D2 — "never with more authority than its first run had"

- CHANGELOG: "never with more authority than its first run was granted, nor more than a new delegation would get now". The consent bullet: "with the posture its first run was granted as a ceiling".
- Index row: "the posture its first run was granted as a ceiling …: it is never wider than its first run was granted, nor than a new delegation now".
- ADR-0277 D10 gains a paragraph:
  - the ceiling is the GRANT, not the posture that ran (critique m3);
  - the two differ only for a batch member the live floor tightened (ADR-0199 §(k));
  - every resume is also capped by what a new delegation gets now.
- D10's "one that first ran at `plan` may not" → "one whose first run was granted only `plan` may not".
- ADR-0243 header note: "a resume runs inside those limits" → "inside the first run's limits".

### D3 — "the only source of its argv"

- Index row: "decided once before the run is admitted; the child's tools, context files, thinking level and model flags come from them alone, its `--permission-mode` from the grant".
- ADR-0277 Consequences: the same, plus "the rest of it — the session flag, the profile's prompt file, skills and extensions, the trust flags, `--no-agents`, the task — is built as for any child".
- ADR-0277 D8, the `SpawnPlan` bullet gains the same sentence ("The limits are not the whole argv: …").
- Checked in the code:
  - `PrintChannel.run` → `limited_profile(profile, limits)` (tools, context_files, thinking) and `model_pin=limits.model`;
  - `permission_mode=plan.permission_mode`, which `_run` sets to `_tighten(grant.mode, permission_floor)`;
  - `profile_to_argv`/`profile_to_flags` for the session flag, prompt file, skills and extensions;
  - `child_trust_argv`, then `--no-agents` (`build_child_argv`).

### D4 — the guide: where the id is, where the guidance is

- Both copies, "Resuming an agent", first paragraph:
  - A single result has the `Session: <id>` line, whether it finished or stopped or failed after it started.
  - In a parallel or chain call, an ok member has it in its block. A member that did not finish has it in its heading (`… did not finish: <reason> · session <id>`).
  - The guidance ("check … and, if it should go on, … resume it", the call spelled out) is in a single result only; a batch member gets none.
- Also added from F9: after Esc, the report says the stop was intended and to resume or repeat only if you ask.

### D5 — ADR-0277: where the RESUME_* sentences live

- D11's "Refined" paragraph now says: the `RESUME_*` sentences live in `child_session`. The runtime holds the preamble (`RESUME_PREAMBLE`) and one damage detail (`_TARGET_CHANGED`).
- Checked: `grep -n "^RESUME_\|^_RESUME…" A/*.py`. Every `RESUME_*` refusal is in `child_session.py`. The runtime has `RESUME_PREAMBLE`, `_RESUME_INSTRUCTION_HEAD` and `RESUME_PREAMBLE_RESERVE`.

### D6 — ADR-0277's quote of ADR-0243 §3

- Was: `This amends ADR-0243 §3, where "a reader tries `rel` first and `path` second"`. ADR-0243 §3 actually reads "a reader tries it first and `path` second", so that was a made-up quote.
- Now a paraphrase, with no quotation marks: "This amends ADR-0243 §3, whose reader tries the record's `rel` first and its `path` second."

### D7 — "nothing refuses after admission"

- ADR-0277 D11 heading: "Refused before a process, and never after admission" → "Refused before a process, and before admission". D11 is still D11; `grep` found no citation of the old heading text.
- Body: "No product path refuses a resume after `_run`'s admission". Two belts remain below it, and no product path reaches either:
  - `PrintChannel.run`'s limits-less resume plan (`_run` always passes limits);
  - `RpcChannel.run` (refused before admission now, D8).
- Refined note: the second belt was reachable by a runtime handed an `RpcChannel` (F8, Codex 10).
- D8's `RpcChannel` bullet gains the F8 refinement:
  - `resumes_agents` (`True` on `PrintChannel`, `False` on `RpcChannel`), described on `SubagentChannel`, not a Protocol member;
  - `getattr(..., False)`;
  - `RESUME_UNSUPPORTED_CHANNEL` at the hook and in `resume_granted`.
- Consequences: a channel may declare `resumes_agents`; a runtime whose channel does not is refused before admission.

### D8 — the Windows/O_NOFOLLOW sentence and the gap

- The Windows bullet in "Not checked" now says:
  - without `O_NOFOLLOW`, Windows makes the PARENT's own head read follow a link swapped in after the checks;
  - the child's open follows one on every OS (limit 15);
  - hard-link count, junctions and the `realpath` comparison were not run on Windows;
  - the writer-lock probe was macOS-only;
  - the hard-link, link and real-child tests are POSIX-only.
- New limit 15: the child opens its file by path after the last check (`resume_granted`'s re-run lookup). `JsonlSessionStorage.open` has no `O_NOFOLLOW` (grep: none in `jsonl_storage.py`).

### D9 — Limits 11–15 (1–10 untouched; code cites "limit 2" and "limit 3")

An intro gives the round's threat model, from the progress file's 판정 근거.

- **11 (a, Codex 2):** the posture is read when the call is approved (the hook's `_grant_for`), as for a new single delegation. A shift+tab before `_execute` is not re-read. Only a batch's later members re-read it (ADR-0199 §(k); `batch._live_floor`). Checked: `permission_floor` is passed only by `batch.py:_live_floor` and by `resume_granted`.
- **12 (b, Codex 6):** a provider only the profile's own extension registers has no route pin. The endpoint is that extension's registration at each run. Limit 4's reasoning. The #362 history is from `resolver._pin_route`'s docstring.
- **13 (c, Codex 7):** thinking is pinned only when the first run passed `--thinking`. Same for `model: null` (a profile with no model of its own under a parent whose model could not be read).
  - D9 table thinking row: "pinned … A first run that passed no `--thinking` recorded `null` … (limit 13)".
  - D8's `ChildLimits` bullet: `thinking` (null when none passed) and `model` (null when no model flag).
  - Consequences, index, guide and CHANGELOG fixed where they said "model and thinking level pinned/kept".
  - The guide's sentence states the profile-with-own-model case against its existing thinking section ("(above)").
- **14 (d, live 1, #619):** on macOS a cut `bash` command can outlive the child (Esc or SIGKILL). The resume does not wait for it, and the preamble says to check effects. The live report says the leftover `sleep 99` was alive at the resume.
- **15 (e):** see D8.

### D10 — the guide: `-p` and the posture

- A new paragraph, **The posture is the lower of the two.**
- A `-p` run without `--permission-mode` gives a new delegation `plan`, so it resumes read-only even after a `yolo` first run. Give the second `-p` run the same `--permission-mode` as the first.
- The source is live L3 (run 2 had no `--permission-mode`; the child had `--permission-mode plan`). In the code, `child_permission_mode` folds DEFAULT into PLAN, and `resume_clamp` takes the rank minimum.
- CHANGELOG's consent bullet says the same in one sentence.

### D11 — every string F9–F13 changed

- ADR-0277:
  - D3: quoted paragraph and `id` text (F12); head 1 525 → 1 598 bytes.
  - D4: guidance block (F9); Refined note with the cancel head verbatim (double-backtick code span, because it contains backticks).
  - D10: `Permission:` row `[ (first run: <first grant>)]` (F11); "resuming" disclosure (F10), replacing the old "there is no 'resuming' wording".
  - D11 table: new `RESUME_UNSUPPORTED_CHANNEL` row; directory row "gone, moved or replaced, or outside"; damage row lists every new detail; parser row adds NUL.
  - D11: every changed `RESUME_*` sentence is listed verbatim; the dropped detail `its session file is no longer a regular file` and the `LimitsError` reason `profile_source is not an absolute path` are noted.
  - D1 (F7): the NUL refusal text.
  - D5 (F2), D6 (F3, F4, F6), D8 (F8, F15) and D9 (F5) are Refined notes in the same style ("Refined in review round 1 (Fn, finding)").
- ADR-0276:
  - Header note names D14.
  - D11 note quotes the new `RESUME_GUIDANCE`.
  - A new dated note under D14 quotes the new `CANCEL_REPORT_HEAD`. The original D14 item 2 text is left as written, as an accepted ADR's body.
- ADR-0243:
  - §3 note: the resume reader derives from the parent's real path, and reads a first run off its start; a disagreeing line is damage (F2, F3).
  - Header note: the same in one clause.
- Guide: the `resuming …` line (only for an agent that may write, per `disclosure_is_required`); the `Permission: plan (first run: auto-accept-edits)` row; the refusal list (link or hard link; directory moved or replaced); "checked again right before the agent starts" (F3).
- CHANGELOG: the same strings, the Esc report sentence, and the RPC channel "refused before anything is spent" (F8).

### D12 — Verification

- Opening line: tests are under `tests/agents_ext/` unless a path is given (F1's tests are in `tests/cli/`).
- Each D bullet gains "Round 1:" with the F1–F15 tests, each mapped to its file:

  ```text
  F1 D9; F2, F3 (lookup), F4, F6, F14a D5–D7; F3 (execute) D11/D12; F5 D9;
  F7, F12 D1/D3; F8, F15 D8; F9 D2/D4; F10, F11 D10; F13, F14b D11/D12
  ```

  The four tests R1-code rewrote (`_rewrite_first_run`) are named under F2.
- Mutation paragraph: R1-code's 25/25, each mutant named by what it removes (cross-checked against the 25 rows of its table).
- "What the lanes ran":
  - lane B's 3.11.15/3.13.13/3.14.5 runs, 800 passed each, now point to `610-progress-2026-10-30.md` ("구현"), as D12 asks;
  - R1-code's runs: `tests/agents_ext` 2 498 passed, 11 skipped on 3.12.13; 14 files, 1 300 passed, 10 skipped on 3.11.15 and 3.14.5.
- "### Live (to be filled by the lead)" is untouched. No review section was added; the lead fills both.
- Header: the progress file, the F-numbering and both fix notes are named.
- "Found on the way" adds R1-code's Windows py≥3.13 fixture fix (simulated, not run on Windows).
- The `run` lone-surrogate bullet adds the `run` NUL case (probed above; left to a separate issue).

## Commands and outputs

All from the worktree, Python 3.12.13.

**Bundled guide:**

- `env -u VIRTUAL_ENV uv run --no-sync python scripts/sync_bundled_docs.py` → `copied agent-profiles.md` / `12 guide(s) bundled — 1 updated, 0 removed.`
- That was run after each guide edit. The final run: `1 updated`, then `cmp docs/guides/agent-profiles.md packages/aelix-coding-agent/src/aelix_coding_agent/docs/agent-profiles.md` → identical.

**Ruff:**

- `… ruff check <the 7 changed files>` → `warning: No Python files found under the given path(s)` / `All checks passed!`. Ruff has nothing to lint here, because no .py file changed.

**Type gate:**

- `… python scripts/check_types.py` → `OK  gate: 0 errors across 303 files` / `PASS: type gate clean, narrowing spike still inverted`. Run twice, the second time on the final docs.

**Citations** (three rounds, the last after the last edit):

- `git diff > pre.diff`, then `… check_citations.py --fix` → `relocated 0 citation(s) across 0 file(s).` / `locked 540 anchor(s) over 914 gated citation(s) …`.
- `git diff > post.diff; cmp pre.diff post.diff` → identical. `--fix` changed nothing; `citations.lock.json` sha256 was unchanged (`d9b26500…`).
- `--check` → `citations OK — 914 gated, none drifted.`
- No anchor needed re-deriving by hand.
- `git diff --check` → clean.

**Tests** (final tree):

- `pytest -q tests/docs tests/test_docs_bundle_sync.py tests/cli/test_docs_signpost.py tests/cli/test_docs_command.py tests/packaging_gate/test_docs_bundle.py tests/agents_ext/test_yolo_disclosure_copy.py tests/test_citation_drift.py tests/cli/test_catalog_relative_source_131.py tests/packaging_gate/test_build_hygiene.py tests/test_ci_hang_guard_429.py` → `586 passed, 20 skipped in 24.22s`.
  - Baseline before any edit, for the first seven paths: `119 passed in 7.89s`.
- CHANGELOG readers: `grep -rl CHANGELOG tests/ --include='*.py'` lists 22 files. Four open `CHANGELOG.md`: `test_yolo_disclosure_copy`, `test_catalog_relative_source_131`, `test_build_hygiene` and `test_ci_hang_guard_429`; they are in the run above. The other 18 only mention it in docstrings.
  - Those 18, run once (before the last two wording edits, which touched only the guide's consent paragraph and one CHANGELOG sentence): `1625 passed, 11 skipped, 41 warnings in 85.07s`.
- After the very last edit (one CHANGELOG sentence):
  - the four CHANGELOG readers and `tests/test_citation_drift.py` → `501 passed, 20 skipped in 19.70s`;
  - `check_citations.py --check` → `citations OK — 914 gated, none drifted.`;
  - `git diff --check` → clean;
  - `cmp` of the two guide copies → identical.

**Cited test names:**

- `/tmp/610-r1-docs/check_test_names.py` (every `test_*` name in a doc has a `def` under `tests/`, or is a test file's stem):
  - ADR-0277 → `names cited: 135; missing defs: 0`;
  - ADR-0243 → 4/0;
  - ADR-0276 → 10/0;
  - CHANGELOG → 17/0.
- `/tmp/610-r1-docs/map_tests.py` maps the 124 names in ADR-0277's Verification to their files: `124 OK, 0 missing`.
  - Those outside the two #610 files are in the files the ADR names: `test_stopped_child_reports_604.py`, `test_tool_and_security.py`, `test_events_and_statusline.py`, `test_agent_resume_real_child_610.py`, `tests/cli/test_withdrawn_default_sweep_434.py`.

### A command that went wrong, and what was done

- The first run of the CHANGELOG readers was `files=$(grep -rl CHANGELOG tests/ --include=*.py …)`. zsh treats the unquoted `*.py` as a glob, so it printed `no matches found` and `files` came out empty.
- `pytest $files` then started with no paths, which is the whole suite. It was moved to the background at the 240 s tool timeout.
- It was stopped with `TaskStop` as soon as that was seen.
- Afterwards:
  - `ps -axo … | grep pytest` showed no pytest process;
  - no process from this worktree was left;
  - `git diff` was byte-identical to before the run (`cmp`).
- The run was then redone with `--include='*.py'` quoted, file list first.
- No result from the aborted run is used anywhere.

## Open for the lead

- `ChildLimits.from_record`'s comment (R1-code) says `fresh_limits` records a `source_path` "which discovery always makes absolute". `get_agent_dir()` keeps a relative `AELIX_CODING_AGENT_DIR` relative, which is why `profile_identity` exists (the code notes say so too). Behaviour is right; the comment is not. That is a code docstring, outside this lane.
- ADR-0277 "### Live", and any review-round section, are the lead's.
- The D11 heading text changed: "never after admission" → "before admission". The number is the same.
- Not checked: the TUI rendering of the new strings (R1-code's grids are in its notes), Windows, Linux, the full suite.
