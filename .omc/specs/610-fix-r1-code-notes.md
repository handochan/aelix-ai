# #610 fix round 1 — lane R1-code notes (2026-10-30)

Task list: `610-progress-2026-10-30.md`, "수정 라운드 1 — 목록", items F1–F15.
Findings: `610-review-r1-codex-2026-10-30.md` (Codex 1–10), `610-review-r1-verify-2026-10-30.md` (M1, m1–m6), `610-live-r1-report-2026-10-30.md` (live 3, 6, 10).

## Base

- Worktree `~/dev/aelix-ai-worktrees/claude-610`, branch `feat/610-agent-resume`, HEAD `883ded81` at start and at the end of the work (checked with `git rev-parse HEAD` before the commit).
- `env -u VIRTUAL_ENV uv run --no-sync python -c "import aelix_agents; print(aelix_agents.__file__)"` → `…/claude-610/packages/aelix-coding-agent/src/aelix_agents/__init__.py`, Python 3.12.13.
- The three #610 test files held 226 tests at `883ded81` (115 + 110 + 1); they hold 267 now (141 + 125 + 1).

## Per item

`A/` = `packages/aelix-coding-agent/src/aelix_agents/`, `T/` = `tests/agents_ext/`.

### F1 — the sweep classification

- `tests/cli/test_withdrawn_default_sweep_434.py`: `_EXPECTED` gains `("aelix_coding_agent/agents/resolver.py", "model_pin_route_moved", "resolve_route"): ("typed", 1)`.
- Classified by the module docstring's rules. `typed` = a string resolved as typed, a removed id included. The pin's strings reach the child as `--model`/`--provider` argv; a launch that names a model records no active settings seed (`cli/entry.py`'s `record_settings_seed(..., active=True)` runs only when no model was named), so the child resolves them as typed.
- The convention drives `seed` and `pick` callers, not `typed` ones. A drive test was added anyway, because it shows why the classification is safe and not just plausible:
  - `model_pin_route_moved` calls `resolve_route` only for a pin with a ROUTE. A route is pinned only for an explicitly registered provider's model, and `ModelRegistry.is_removed` never calls a registered id removed ("the route rows have it").
  - So even when the parent's own ACTIVE seed is exactly the pin's pair, and a hold took the id out, the parent lands on the pinned model.
  - Test: `test_a_resumed_childs_pin_resolves_as_typed_even_under_the_seed`.

### F2 — the ceiling is the first key's START record (Codex 1, verify M1)

- `A/child_session.py`:
  - `_Run` gains `start` (the key's FIRST `phase: "start"` line), `limits_agree` (every line of the key carries the start's `limits`) and `identity` (start, else last line). `data`/`position` stay the LAST line: the outcome and the order.
  - `_fold_runs` collects a key's lines, then builds those fields.
  - `_group_by_child` groups by `identity.child`.
  - `_lineage` reads `resumes` from `identity`. For the first run it now refuses:
    - no start on the branch → `RESUME_FIRST_RUN_MISSING`;
    - lines that disagree about `limits` → `resume_damaged("its records disagree about the limits it ran with")`.
  - `find_resume_target` reads the first run from `first.identity` (its start): `tool_call_id`, `limits`, `profile`, `task_preview`, `child.rel`.
  - `_listing` and `_listing_line` read the same.
- Existing texts are kept. A first run that has no start but has its settle used to be resumable off the settle; now it is `RESUME_FIRST_RUN_MISSING`.
- Tests:
  - `T/test_agent_resume_610.py`:
    - `test_a_later_line_that_widens_the_first_runs_limits_is_damage`: a settle copy with `granted_mode: yolo` → damaged; not listed; no process.
    - `test_the_ceiling_is_the_first_run_not_the_latest_resume`: auto-accept-edits + bash/read/write → narrowed resume (plan, read) → wide resume runs auto-accept-edits + bash/read/write. The target's and the dialog's "Earlier task" is the first task.
    - `test_what_the_first_run_was_is_read_off_its_start`: a later line naming another profile, task and rel changes nothing.
    - `test_a_first_run_whose_start_is_not_on_the_branch_is_refused`.
  - `T/test_agent_resume_tool_610.py`: `test_a_forged_later_receipt_cannot_raise_the_ceiling`. This is Codex's probe through the hook: default parent → `plan` first run, forged settle `yolo`, parent `yolo` → refused at the hook, nothing spent.
- Tests that forged a LATER line to change the first run now rewrite every line of that key in the parent file and reopen it (`_rewrite_first_run`, in both files). A later line can no longer change the first run, which is the fix. The changed tests:
  - `test_a_forged_record_is_refused_before_any_process`
  - `test_the_recorded_path_is_never_a_way_to_the_child_file`
  - `test_a_record_from_before_610_is_refused`
  - `test_a_recorded_profile_name_is_cleaned_before_it_is_echoed`
- `test_a_forked_conversation_does_not_resume_the_originals_child`: its second half now builds a copy whose START points at the copy's directory. The test still exercises the origin check, not just the rel check.

### F3 — execute-time revalidation (Codex 3, verify m4)

- `A/runtime.py`, `resume_granted`. After the existing session-id check (kept, so a switched conversation still says `RESUME_CONVERSATION_CHANGED`):
  - it runs `self.find_resume_target(target.session_id)`. That is the same method the hook calls (`extension._approve_resume`), not a second copy.
  - A string result is returned as the refusal.
  - A target that is not `_same_target` (every `ResumeTarget` field except `last_status`) → `resume_damaged(_TARGET_CHANGED)`.
  - Then `resume_refusal` runs, then `_run`. Both awaits come before the synchronous section, so the registry check and insert stay one step (comment updated).
- `A/runtime.py`, `resume_refusal`: the partial `lstat` copy of the file check is removed. The file is the lookup's job, and both callers run the lookup immediately before. `import stat` dropped.
- `A/child_session.py`:
  - `find_resume_target` derives the placement from `os.path.realpath(parent_path)`.
  - `_check_child_file` adds `os.path.normcase(os.path.realpath(path)) != os.path.normcase(path)` → `resume_damaged("its session file is reached through a link")`. It sits after the existing directory/file `lstat` checks, which keep their own texts. It also covers what `lstat` does not call a link (a Windows junction).
- Tests:
  - `test_a_file_swapped_for_another_session_after_the_hook_is_refused`
  - `test_a_directory_swapped_for_a_link_after_the_hook_is_refused` (POSIX)
  - `test_a_branch_moved_off_the_first_run_after_the_hook_is_refused` (`session.move_to(None)`)
  - `test_a_target_that_changed_under_its_id_is_refused`
  - `test_a_parent_opened_through_a_linked_directory_still_finds_its_child` (POSIX; pins the realpath derivation)
  - `test_a_child_path_with_a_parent_step_is_not_its_file` (pins the realpath check)
  - tool: `test_execute_runs_the_hooks_lookup_again` (hook approves, file replaced, `execute()` refuses).
  - Each refusal test asserts that no process ran, no budget was charged, no record was written and no registry row was left.
- Remaining gap, not closed (docs D8): between the launch's last check and the child's own `open` of `--session` (the child opens by path; `JsonlSessionStorage.open` has no `O_NOFOLLOW`).

### F4 — hard link (Codex 4)

- `A/child_session.py:_check_child_file`: `info.st_nlink > 1` → `resume_damaged("its session file has another name (a hard link)")`.
- Test: `test_a_child_file_with_a_second_name_is_refused`. POSIX only: Windows link counts were not measured here.

### F5 — cwd identity (Codex 5)

- `A/print_channel.py`: new `resume_cwd(recorded, parent_cwd)` = `resolve_child_cwd` plus `cwd != recorded` → `ResumeRefused(RESUME_CWD_GONE)`.
- One function for the hook's check (`runtime.resume_refusal`) and the launch (`resume_limits`).
- Checked: the first run records a canonical path. Every door passes `_run` what `resolve_child_cwd` returned, which is `Path.resolve()`. `fresh_limits` is unchanged.
- `RESUME_CWD_GONE` text widened (see strings).
- Tests (both POSIX):
  - `test_a_directory_replaced_by_a_link_to_a_sibling_is_refused`: rename `work`, symlink `work` → `other` → refused at the hook check and at the launch, nothing spent.
  - `test_the_recorded_cwd_is_the_real_one_so_a_linked_parent_still_resumes`: a parent cwd through a symlink records the real path, and the resume is not refused.

### F6 — the loader's header rule (Codex 8)

- `A/child_session.py` imports `_parse_header_line` (and `SessionError`) from `aelix_agent_core.session.jsonl_storage`. That is the loader's own parser, not a copy, so there is no `3` anywhere in aelix_agents. Precedent for the private cross-package import: `cli/session_labels.py` imports `_parse_jsonl_content`.
- `_check_child_file` parses the first line with it; a `SessionError` → `resume_damaged("its session header is not valid")`. An id other than the requested one is still `… does not match`.
- `_head_entries` is replaced by `_head_lines` + `_parse_line`, which split the head the way the loader does:
  - universal newlines: `\r\n`, `\r` and `\n` (the loader reads with `Path.read_text`);
  - strict UTF-8;
  - blank lines skipped.
  So "the first line" is the loader's first line.
- Tests:
  - `test_a_header_the_childs_loader_refuses_is_refused_before_anything_is_spent`, 8 cases: `version` 999, `version` "3", no timestamp, empty timestamp, no cwd, `parentSession` 7, `type` "sessions", and a `\r` inside the header line. Each case first proves `JsonlSessionStorage.open` itself raises `SessionError`; then `find_resume_target` is refused; then `resume_granted` spends nothing.
  - `test_the_header_rule_is_the_session_loaders_own`: function identity, plus the line split.

### F7 — NUL in a resume instruction (Codex 9)

- `A/tool.py:_parse_resume`: after the lone-surrogate check, `"\x00" in task` → `AgentCallError(_RESUME_TASK_HAS_NUL)`.
- Tests: `test_a_nul_in_a_resume_instruction_is_refused_by_the_parser` (exact text) and `test_a_nul_instruction_spends_nothing` (through the hook).
- The `run` path's NUL and lone surrogate are untouched: that is a separate issue (progress file).

### F8 — channel capability before admission (Codex 10)

- `A/print_channel.py`: `PrintChannel.resumes_agents = True`. The `SubagentChannel` docstring describes the optional attribute; it is not a Protocol member.
- `A/rpc_channel.py`: `RpcChannel.resumes_agents = False`. Its own `run` refusal stays as the belt.
- `A/runtime.py:resume_refusal`, first check: `getattr(self.channel, "resumes_agents", False)`, else `RESUME_UNSUPPORTED_CHANNEL`. A channel that does not say is refused. Because the check is in `resume_refusal`, the hook refuses before any dialog, and `resume_granted` refuses before `_run`.
- Test stand-ins `T/test_child_session_records.py:_Channel` and `T/test_tool_and_security.py:_RecordingChannel` declare `resumes_agents = True`.
- Tests:
  - `test_a_channel_that_cannot_resume_is_refused_before_admission`: `RpcChannel` and a stand-in that does not say; budget unchanged, no records.
  - tool: `test_a_channel_that_cannot_resume_is_refused_at_the_hook` (no dialog).
  - The belt test `test_the_rpc_channel_refuses_a_resume_plan` is unchanged and passes.

### F9 — wording (live 6)

- `A/tool.py:RESUME_GUIDANCE` made conditional. The first sentence and the `call_literal` values are unchanged.
- `A/aggregate.py:CANCEL_REPORT_HEAD`: one sentence added, worded for both single and batch reports.
- Pins updated in `T/test_stopped_child_reports_604.py`: `test_the_unfinished_template_in_full` and `test_esc_during_a_delegation_queues_its_report_for_the_next_prompt`.
- New pin: tool `test_the_cancel_report_says_the_stop_was_intended`.
- The other #604 pins use the constant and pass.

### F10 — the resume says "resuming" (live 3)

- `A/consent.py:build_disclosure_line(..., resume: bool = False)`. With `resume=True` the line is `resuming <name> at <mode>, from <source>`. The fresh output is byte-identical to before.
- Used in `A/extension.py:_grant_for` (`resume=resume is not None`) and in `A/consent.py:request_resume_consent`'s grant.
- Tests:
  - tool `test_the_disclosure_says_resuming_and_a_fresh_one_is_unchanged`;
  - pins updated in `test_an_agent_granted_edits_resumed_under_yolo_is_disclosed_at_its_own_posture` and `test_widening_a_resume_grants_edits_and_a_yolo_parent_is_told_the_ceiling`.

### F11 — the dialog names a wider first run (live 10)

- `A/consent.py:build_resume_consent_title`: when `posture_rank(target.limits.granted_mode) > posture_rank(clamped)`, the row reads `Permission: <mode> (first run: <first grant>)`. The row count is unchanged.
- Tests:
  - tool `test_the_permission_row_names_a_wider_first_run`, 5 cases, including equal → no suffix, and the 14-row body;
  - pins updated in `test_the_resume_dialog_body_in_full` and `test_the_resume_dialog_cannot_be_forged`.

### F12 — where the id is (verify m5)

- Changed so they name both places:
  - `A/tool.py`: the `id` property description, the description head's last paragraph and `_RESUME_ID_IS_A_PATH`;
  - `A/child_session.py`: `RESUME_NEEDS_ID` and `RESUME_UNKNOWN_ID`, which make the same claim.
- The two places: the `"Session:"` line, and `after "session"` in a batch member's `"did not finish"` heading. The latter is `aggregate._member_heading`'s `did not finish: <reason> · session <id>`.
- The `action` description does not say where the id is and is unchanged.
- The head is now 1598 of 2048 bytes; `test_the_contract_text_stays_inside_its_own_budget` passes.
- Test: tool `test_every_sentence_that_says_where_the_id_is_names_both_places`. It covers all five sentences, and renders a real unfinished member to show its heading carries both words.

### F13 — next action on four refusals (verify m6)

- `RESUME_NO_SESSION`, `RESUME_UNREADABLE`, `RESUME_LOCK_ERROR` and `RESUME_CONVERSATION_CHANGED` now end with " Delegate the task anew.".
- Test: tool `test_the_refusals_that_said_only_why_now_say_what_next`.
- Not changed: `RESUME_NOT_YOURS` still states only why. It was not in the list.

### F14 — the two untested properties (verify m1, m2)

- (a) `test_a_first_key_not_shaped_like_a_spawn_key_is_refused_before_any_path`. The first run's key is rewritten to `../evil` with a matching rel, and a well-formed child file waits at the traversal target. The test asserts the exact `its record's key is not a delegation's` refusal.
- (b) `test_a_resumes_registry_row_names_its_child_from_the_insert`. `on_event` receives the first progress frame, which `_run` publishes right after the registry insert and before `receipt.attach`. In that frame `resume_refusal(target) == RESUME_RUNNING`.

### F15 — `profile_source` absolute (docs check 2)

- `A/child_session.py:ChildLimits.from_record`: `profile_source` must be `_is_path` and `os.path.isabs`, else `LimitsError("profile_source is not an absolute path")`.
- `from_record` alone would have refused every resume of a user with a relative `AELIX_CODING_AGENT_DIR`: discovery's `_scan_dir` records `str(entry)` of a possibly relative directory. So a new `A/print_channel.py:profile_identity(source_path)` (= `os.path.abspath`) is what `fresh_limits` records, and `A/runtime.py:_same_identity` compares the same spelling.
- Tests:
  - `test_a_forged_limits_field_is_refused` gains `{"profile_source": "agents/scout.md"}`;
  - `test_a_relative_profile_path_is_recorded_absolute_and_still_resumes`.

## Every model-facing or user-facing string changed (old → new, verbatim)

Extracted with `ast` from `git show HEAD:<file>` and from the working tree (script output kept in this section).

### Refusals (`A/child_session.py`)

`RESUME_NEEDS_ID`
```text
OLD: Resume refused: action="resume" needs id — the Session id of an agent this conversation delegated, from the Session line of its result.
NEW: Resume refused: action="resume" needs id — the Session id of an agent this conversation delegated, as its result shows it: on its "Session:" line, or after "session" in a batch member's "did not finish" heading.
```
`RESUME_UNKNOWN_ID`
```text
OLD: Resume refused: no agent this conversation delegated has that id. Use the id on the Session line of the agent's result.
NEW: Resume refused: no agent this conversation delegated has that id. Use the id the agent's result shows: on its "Session:" line, or after "session" in a batch member's "did not finish" heading.
```
`RESUME_NO_SESSION`
```text
OLD: Resume refused: this conversation is not saved in aelix's sessions directory (--no-session, or a file opened from elsewhere), so no agent it delegated has a session to continue.
NEW: Resume refused: this conversation is not saved in aelix's sessions directory (--no-session, or a file opened from elsewhere), so no agent it delegated has a session to continue. Delegate the task anew.
```
`RESUME_UNREADABLE`
```text
OLD: Resume refused: this conversation's record could not be read, so that agent cannot be found.
NEW: Resume refused: this conversation's record could not be read, so that agent cannot be found. Delegate the task anew.
```
`RESUME_LOCK_ERROR`
```text
OLD: Resume refused: aelix could not check whether that agent's session is in use ({error}).
NEW: Resume refused: aelix could not check whether that agent's session is in use ({error}). Delegate the task anew.
```
`RESUME_CWD_GONE`
```text
OLD: Resume refused: the directory that agent ran in no longer exists, or is no longer inside this conversation's working directory. Delegate the task anew.
NEW: Resume refused: the directory that agent ran in no longer exists, was moved or replaced, or is no longer inside this conversation's working directory. Delegate the task anew.
```
`RESUME_CONVERSATION_CHANGED`
```text
OLD: Resume refused: the conversation changed before the resume could start, so that agent is not this conversation's to resume.
NEW: Resume refused: the conversation changed before the resume could start, so that agent is not this conversation's to resume. Delegate the task anew.
```
`RESUME_UNSUPPORTED_CHANNEL` (new)
```text
NEW: Resume refused: this session delegates through a channel that cannot resume an agent inside the limits it ran with. Delegate the task anew.
```

### `resume_damaged(detail)` details

Template, unchanged: `Resume refused: that agent's session record is damaged ({detail}). Delegate the task anew.`

New details:
```text
its records disagree about the limits it ran with        (F2, child_session._lineage)
its record changed before the resume could start         (F3, runtime._TARGET_CHANGED)
its session file is reached through a link               (F3, child_session._check_child_file)
its session file has another name (a hard link)          (F4, child_session._check_child_file)
its session header is not valid                          (F6, child_session._check_child_file)
```
No longer emitted, because `resume_refusal`'s file-check copy was removed:
```text
its session file is no longer a regular file             (was runtime.resume_refusal; the lookup's "its session file is not a regular file" answers instead)
```
`LimitsError` reason, shown as `its recorded limits are invalid: <reason>`:
```text
OLD: profile_source is not a path
NEW: profile_source is not an absolute path
```

### Tool (`A/tool.py`)

`id` property description
```text
OLD: action=resume only: which agent to continue — the id on the Session line of its result. Not a file path.
NEW: action=resume only: which agent to continue — the id its result shows, on its "Session:" line or after "session" in a batch member's "did not finish" heading. Not a file path.
```
`_DESCRIPTION_HEAD`, last paragraph (the rest is unchanged; 1525 → 1598 bytes of 2048)
```text
OLD: All of that is action="run", the default. action="resume" continues ONE agent that finished or was stopped: pass the 'id' on the Session line of its result, the 'profile' it ran as, and its next instruction as 'task'. It goes on in its own conversation, on the same profile and model, and its limits never widen. Resume it rather than delegating the same work again from scratch.
NEW: All of that is action="run", the default. action="resume" continues ONE agent that finished or was stopped: pass the 'id' its result shows (on its "Session:" line, or after "session" in a batch member's "did not finish" heading), the 'profile' it ran as, and its next instruction as 'task'. It goes on in its own conversation, on the same profile and model, and its limits never widen. Resume it rather than delegating the same work again from scratch.
```
`_RESUME_ID_IS_A_PATH`
```text
OLD: 'id' is the agent's Session id — the id on the Session line of its result — not a file path.
NEW: 'id' is the agent's Session id — the id its result shows, on its "Session:" line or after "session" in a batch member's "did not finish" heading — not a file path.
```
`_RESUME_TASK_HAS_NUL` (new)
```text
NEW: 'task' holds a NUL character (U+0000), which cannot be sent to the agent. Resend the instruction without it.
```
`RESUME_GUIDANCE`
```text
OLD: Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls, then continue it with agent(action="resume", profile={profile}, id={id}, task=…).
NEW: Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls; if it should go on, continue it with agent(action="resume", profile={profile}, id={id}, task=…).
```

### Cancel report (`A/aggregate.py`)

`CANCEL_REPORT_HEAD`
```text
OLD: [aelix system report — not user input] The `agent` call you made was stopped when the turn was cancelled. What it had done:
NEW: [aelix system report — not user input] The `agent` call you made was stopped when the turn was cancelled. The stop was intended: resume or repeat that work only if the user asks you to. What it had done:
```

### User-facing (`A/consent.py`)

Disclosure row: `build_disclosure_line(..., resume=True)`, used for every resume. The fresh form is unchanged.
```text
FRESH (unchanged): delegating to <name> at <mode> — <n> task(s), from <source>
RESUME (new):      resuming <name> at <mode>, from <source>
```
Resume dialog `Permission:` row, when the first run's grant ranks above the row's mode:
```text
OLD: Permission: plan
NEW: Permission: plan (first run: auto-accept-edits)
```
It is unchanged when the first grant is equal or lower.

## Live captures (this worktree, scripted provider, 80 columns)

These are copies of the earlier lane's L5/L1 drivers, repointed at this worktree and at `/tmp/610-r1-code/live/` (driven with `claude-610-live/.venv/bin/python` for `pyte`). The captures are under `/tmp/610-r1-code/live/runs/`.

L5 resume dialog, 80x50, first run widened by the human to auto-accept-edits, parent default (blank rows dropped):
```text
Resume agent 'editor5'?
Session:    57cc7e0c-ba0b-4bfd-9e10-69d203b3fc7b
Profile:    editor5 (user scope)
Source:     /tmp/610-r1-code/live/runs/l5-80x50/agent/agents/editor5.md
Directory:  /private/tmp/610-r1-code/live/runs/l5-80x50/work
Permission: plan (first run: auto-accept-edits)
Model:      s-1
Earlier task (written by the model):
Write notes5.md with one line, then report.
Next instruction (written by the model, not by you):
Add a second note in notes5b.md, then report.
───────────────────────────────────────────────────────────────────────
▸ Run read-only (plan)
  Allow file edits for this run (auto-accept-edits)
  Cancel
  (1/3)
```
L1 (yolo parent, Esc, then resume), the row while the resumed child runs:
```text
resuming writer at yolo, from /tmp/610-r1-code/live/runs/l1-openai/agent/agents/
```
- The row is still cut at 80 columns with no `…`. That is the existing status-row behaviour, kept out of this round.
- The parent's next request carried the new `CANCEL_REPORT_HEAD` sentence and the new guidance (wire-parent-2/3).
- The leftover `sleep 97` (#619) reproduced and was killed by the driver. `ps` afterwards showed nothing left.

## Commands run and their summary lines

All are Python 3.12.13 from the worktree unless stated otherwise.

Gates:
- `env -u VIRTUAL_ENV uv run --no-sync ruff check <changed .py files>` → `All checks passed!`.
- `… python scripts/check_types.py` → `OK gate: 0 errors across 303 files` and `PASS: type gate clean, narrowing spike still inverted`.
- `git diff --check` → clean.

Citations:
- `… python scripts/check_citations.py --fix` → `relocated 67 citation(s) across 19 file(s).` and `3 could NOT be relocated automatically`.
- Every `--fix` change was read with a script, which reconstructed the pre-fix files from `HEAD` plus the saved pre-fix diff and diffed them. All 111 changed line pairs in source and tests differ in digits only. `citations.lock.json` changes are key renumbering and the resulting reorder.
- Re-derived by hand:
  - `batch.py:301` and `T/test_batch_executor.py:258`: `print_channel.py:1319` (`try:`) → `:1368`, the `try:` right after `row.prompt = write_prompt_file(...)` at `:1367`;
  - `batch.py:415`: `runtime.py:1407` → `:1428`, `self._publish(child, child.stream, on_event, spawn_model=spawn_model)`. It is ambiguous because the same text is at `:1546`; `:1428` is the one right after the registry insert.
- Second `--fix` → `3 new citation(s) had no anchor; they are locked as written.`
- `--check` → `citations OK — 914 gated, none drifted.`
- After the last edit (the `_approve_resume` docstring, F3/F8 wording):
  - third `--fix` → `relocated 4 citation(s) across 4 file(s).`; the same script check gave `pairs 7 bad 0` (digits only);
  - `--check` → `citations OK — 914 gated, none drifted.`;
  - then ruff, `check_types` (PASS), and `pytest tests/test_citation_drift.py` plus the three #610 files, `test_tool_and_security`, `test_spawn_consent` and `test_consent_wiring` → `781 passed in 10.26s`.

Tests:
- `… python -m pytest -q -p no:cacheprovider tests/agents_ext` → `2498 passed, 11 skipped, 2 warnings in 76.43s`. The warnings are the two existing darwin timing notes in `test_print_channel_spawn.py`.
- `… pytest -q tests/agents tests/cli/test_withdrawn_default_sweep_434.py tests/cli/test_child_session_warning.py tests/cli/test_permission_mode_flag.py tests/test_citation_drift.py tests/providers/test_transform_messages.py` → `214 passed, 2 warnings in 7.39s`.
- Python 3.11.15 and 3.14.5 used throwaway venvs: `UV_PROJECT_ENVIRONMENT=/tmp/610-r1-code/venv3.X env -u VIRTUAL_ENV uv sync --frozen --python 3.X`. Each venv's `aelix_agents.__file__` is inside claude-610; the worktree's `.venv` was not touched. The run covered 14 files: the three #610 files plus every test file this lane changed (`test_stopped_child_reports_604`, `test_child_session_records`, `test_tool_and_security`, `test_aggregate`, `test_batch_consent`, `test_batch_executor`, `test_batch_surfaces`, `test_chain`, `test_print_channel_spawn`, `test_spawn_consent`, `cli/test_withdrawn_default_sweep_434`).
  - 3.11.15: `1300 passed, 10 skipped, 2 warnings in 29.07s`
  - 3.14.5: `1300 passed, 10 skipped, 2 warnings in 30.96s`
  - The 10 skips are `process-group / PDEATHSIG semantics are Linux`.
- The `tests/agents_ext`, 3.11 and 3.14 runs above came before the final docstring edit and its 4 citation renumbers, which change comments and docstrings only; the 781-test rerun came after.
- Not run: the full suite (the lead runs it), Windows, Linux.

## Mutation results

Driver: `/tmp/610-r1-code/mutate.py`.
- Each mutant saves the file's original bytes, applies one exact replacement, runs `T/test_agent_resume_610.py T/test_agent_resume_tool_610.py T/test_stopped_child_reports_604.py`, then restores the bytes and asserts the sha256.
- No git is used.
- After the run, every changed file's sha256 equals the pre-mutation list (`diff` empty).

Result: `SUMMARY 25/25 caught`, `RESTORED-ALL True`.

| mutant | first failing test |
|---|---|
| F2 `data = latest.identity` | `test_a_running_child_is_not_resumed_twice`; alone against `test_the_ceiling_is_the_first_run_not_the_latest_resume`: caught |
| F2 `data = first.data` (the first run's last line) | `test_what_the_first_run_was_is_read_off_its_start` (the M1 test alone does not catch this one; genuine lines agree) |
| F2 no agreement check | `test_a_later_line_that_widens_the_first_runs_limits_is_damage` |
| F2 no start check | `test_a_first_run_whose_start_is_not_on_the_branch_is_refused` |
| F3 no execute-time lookup | `test_a_child_file_gone_since_the_hook_is_refused_and_costs_nothing`; against the five F3 tests alone: `5 failed` |
| F3 no `_same_target` | `test_a_target_that_changed_under_its_id_is_refused` |
| F3 abspath derivation | `test_a_parent_opened_through_a_linked_directory_still_finds_its_child` |
| F3 no realpath check | `test_a_child_path_with_a_parent_step_is_not_its_file` |
| F4 no nlink check | `test_a_child_file_with_a_second_name_is_refused` |
| F5 no cwd identity | `test_a_directory_replaced_by_a_link_to_a_sibling_is_refused` |
| F6 pre-F6 header rule (type + id) | `…loader_refuses…[version-999]` |
| F6 `\n`-only split | `…loader_refuses…[carriage-return]` |
| F7 no NUL check | `test_a_nul_in_a_resume_instruction_is_refused_by_the_parser` |
| F8 no channel check | `test_a_channel_that_cannot_resume_is_refused_before_admission` |
| F14a `if False:` on SPAWN_KEY | `test_a_first_key_not_shaped_like_a_spawn_key_is_refused_before_any_path` |
| F14b `session_id=None` at insert | `test_a_resumes_registry_row_names_its_child_from_the_insert` |
| F15 relative `profile_source` accepted | `test_a_forged_limits_field_is_refused[change21-profile_source]` |
| F15 `profile_identity` without abspath | `test_a_relative_profile_path_is_recorded_absolute_and_still_resumes` |
| F10 hook says "delegating" | `test_an_agent_granted_edits_resumed_under_yolo_is_disclosed_at_its_own_posture` |
| F10 consent door says "delegating" | `test_widening_a_resume_grants_edits_and_a_yolo_parent_is_told_the_ceiling` |
| F11 no first-run suffix | `test_the_resume_dialog_body_in_full` |
| F9 old guidance | `test_the_cancel_report_says_the_stop_was_intended`; against the #604 file alone: `test_the_unfinished_template_in_full` |
| F9 old cancel head | `test_the_cancel_report_says_the_stop_was_intended` |
| F12 id description without the heading | `test_every_sentence_that_says_where_the_id_is_names_both_places` |
| F13 lock error without the action | `test_the_refusals_that_said_only_why_now_say_what_next` |

F1 has no product mutant: the classification table is itself the test. `test_every_resolution_call_is_classified` was the r0 failure.

## Deviations

1. **F4.** The check is `st_nlink > 1`, not `!= 1`. A platform that reports no link count (0) is no evidence of a second name. On POSIX the two are the same for an existing path. The test is POSIX-only.
2. **F3, removed copy.** `resume_refusal`'s partial `lstat` copy of the file check was removed rather than kept beside the new lookup. As a result the detail "its session file is no longer a regular file" is no longer emitted.
3. **F3, link scope.** "No link in any component from the sessions root" is implemented as the item's own example: derive from the parent's real path. Links ABOVE the parent's directory are resolved, not refused (for example `~/.aelix` on another volume). The two components below it must not be links, and the realpath equality is the single rule.
   - A parent whose own file or bucket is a link to a place that is not bucket-shaped now gets `RESUME_NO_SESSION`.
   - The resume's `--session` path is now the real path; on macOS `/tmp/…` becomes `/private/tmp/…`.
4. **F3, compared fields.** `_same_target` compares every `ResumeTarget` field but `last_status`. That is more than the item's first_key, path and limits: profile, rel, task_preview and parent_session_id too.
5. **F15, recorded spelling.** Besides requiring an absolute `profile_source` on read, the record now stores `os.path.abspath(source_path)` (`profile_identity`), and the resume compares that same spelling. Without this a relative `AELIX_CODING_AGENT_DIR` would have made every resume "damaged".
6. **Windows fixtures.** Found while doing F15, and fixed. Lane A's `_GOOD` used `cwd: "/repo"`, which `os.path.isabs` refuses on Windows Python ≥ 3.13.
   - Measured with `ntpath.isabs('/repo')`: True on 3.11.15 and 3.12.13, False on 3.13.13 and 3.14.5.
   - So at `883ded81`, `test_a_good_limits_record_round_trips` and 10 of the 24 cases of `test_a_forged_limits_field_is_refused` (the 8 `model*` ones, `profile_source`, `profile_scope`; `_pin_from_record` runs after the cwd check) would fail on the windows-latest py3.13 and py3.14 legs. This was not seen because the branch has not had CI.
   - Simulated, not run on Windows: HEAD's `child_session.py` and HEAD's fixture under py3.14.5 with `os.path.isabs = ntpath.isabs` gave `round trip: FAILS -> cwd is not an absolute path` and `cases 24 would fail 10`.
   - Now `os.path.abspath(...)`. The records pin uses `_SCOUT_SOURCE = os.path.abspath("/home/u/.aelix/agent/agents/scout.md")`, which is unchanged on POSIX.
   - Not run on Windows.
7. **F6, line splitting.** The head is also split as the loader splits it (universal newlines, strict UTF-8, blank lines skipped), not only parsed by its function.
8. **F12, extra strings.** `RESUME_NEEDS_ID` and `RESUME_UNKNOWN_ID` were changed as well, because they make the same claim.
9. **F5, existing constant.** `RESUME_CWD_GONE` was widened instead of adding a new constant.
10. **F2, missing start.** A first run with no start line on the branch is now `RESUME_FIRST_RUN_MISSING` (before: resumable off its settle).
11. **F1, extra test.** A drive test was added although the module's convention does not require one for `typed`.

## Open for the docs lane and the lead

- D11: every string above, verbatim.
- New names for ADR-0277's list:
  - `RESUME_UNSUPPORTED_CHANNEL`;
  - the `resumes_agents` channel attribute;
  - `print_channel.resume_cwd` and `print_channel.profile_identity`.
- D7: after F8, nothing on a product path refuses after admission. The PrintChannel "resume plan without limits" belt remains, and so does `RpcChannel.run`'s refusal, now behind the pre-admission check.
- D8: the remaining gap is between the launch's last check (the re-run lookup and `resume_refusal`) and the child's own open.
- F10/F11 change the TUI. The grids above are the captures; the aesthetic verdict is the lead's.
- Not measured: Windows (hard-link counts, junctions, the realpath comparison on a real Windows profile path), Linux, the full suite, a real model.
- `RESUME_NOT_YOURS` still gives no next action. It was not in F13.
