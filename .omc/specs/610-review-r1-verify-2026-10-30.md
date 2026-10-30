# #610 verification report: worktree claude-610-verify at 54cd7f61 (diff f3e76fbb..54cd7f61)

I found no product defect that blocks the change. The only Major is a missing test for a core D9 rule: the code is correct today, but nothing would catch a regression. The worktree is clean (`git status --short` is empty) and HEAD is still 54cd7f61. Probe scripts are in /tmp/610-verify/.

## Blockers

None found.

## Majors

**M1. Nothing tests D9's rule that the ceiling is the first run's limits, not the latest resume's.**
- **Location:** `packages/aelix-coding-agent/src/aelix_agents/child_session.py:1327` (`data = first.data`).
- **Mutation:** change it to `data = latest.data`, then run the three #610 test files (`env -u VIRTUAL_ENV uv run --no-sync python /tmp/610-verify/mutate.py`). Result: `["ceiling-is-latest-not-first", "SURVIVED", "226 passed in 3.02s"]`.
- **Behaviour today (correct):** `/tmp/610-verify/probe_first_vs_latest.py` runs three steps:
  - first run at `auto-accept-edits` with bash, read and write;
  - resume 1 under a narrowed parent, which runs at `plan` with read only;
  - resume 2 under a wide parent, which runs at `auto-accept-edits` with `('bash','read','write')`. The target preview is `'look at the thing'`.
- **Behaviour under the mutant:**
  - `target ceiling: plan ('read',) preview: 'narrow step'`;
  - `resume2: True plan ('read',)`.

  The agent stays narrowed for good. The dialog's "Earlier task" also shows the previous resume's instruction instead of the first task.
- **Fix:** add a test that runs first run → narrowed resume → wide resume.

## Minors

**m1. The `SPAWN_KEY` check has no test.**
- **Location:** `child_session.py:1336`.
- **Mutation:** `if False:` → `"spawn-key-unchecked", "SURVIVED", "226 passed"`.
- Without the check, a forged first-run key goes straight into `os.path.join(placement.directory, name)` (lines 1344–1352), so `..` in the key leaves the parent's directory. The `rel` comparison cannot stop it, because the key and the `rel` come from the same record.
- What remains is the header, origin and user-message checks, which only demand a crafted file at the target. So this is defence in depth. I reasoned about the traversal from the code; I did not exploit it.

**m2. `RunningChild.session_id` being set at the registry insert has no test.**
- **Location:** `runtime.py:1370`.
- **Mutation:** `session_id=None` → `SURVIVED, 226 passed`. `receipt.attach` sets it anyway at `runtime.py:1448`.
- The gap between the insert and the attach is reachable only by concurrent resumes, which wait for #611. ADR D12 states this as a property.

**m3. A batch member is resumed at its grant, which can be wider than the posture it actually ran at.**
- **Probe:** `spawn_granted(grant=AUTO_ACCEPT, permission_floor=PLAN)` gives `member ran at: plan recorded granted: auto-accept-edits`. A resume with an `AUTO_ACCEPT` grant then gives `resume ran at: auto-accept-edits`.
- This is by design (ADR D8, critique m3), and the resume is still no wider than a fresh spawn now. It does fail the literal "no wider than its first run" criterion, and CHANGELOG overclaims it (see Doc mismatches).

**m4. Leftover time-of-check/time-of-use gap between the hook and the launch.**
- The header, origin and user-message checks run only at the hook (`_check_child_file`). `resume_refusal` (`runtime.py:991-1051`) repeats only identity, live row, lstat, cwd, route and lock, even though a dialog can stay open for minutes.
- The child opens `--session` through `JsonlSessionStorage.open` (`jsonl_storage.py:613`). That open has no `O_NOFOLLOW` on any OS, so a symlink swapped in after the belt's lstat is followed, and the transcript is appended to the link's target.
- Exploiting this needs a local writer in the sessions directory. I did not run a live swap.

**m5. The model is told the id is on "the Session line of its result", but an unfinished batch member has no Session line.**
- The text appears at `tool.py:224`, `tool.py:489` and in the description head.
- An unfinished batch member carries the id only as `· session <id>` in its heading (`aggregate.py:357`), with no resume guidance.

**m6. Several refusals say why but not what to do next:** `RESUME_NO_SESSION`, `RESUME_UNREADABLE`, `RESUME_LOCK_ERROR`, `RESUME_CONVERSATION_CHANGED`.

## Doc mismatches

- **`CHANGELOG.md:32`** says "never with more authority than its first run had". That is false for m3. ADR-0277's Consequences says it precisely: "on or below what its first run was granted". The index row's "first run's posture as a ceiling" is ambiguous in the same way.
- **ADR-0277:582** says Windows follows a symlink swap because it lacks `O_NOFOLLOW`. That holds for the parent's header read, but the child's own open follows a swap on POSIX too (m4).
- **ADR-0277 D11** says "Nothing refuses a resume after `_run`'s admission". Two channel belts do refuse after admission, which costs budget and writes start, pending and settle records:
  - `rpc_channel.py:357`;
  - the `PrintChannel` check for a resume plan with no limits.

  Both are unreachable on product paths, so this is a precision issue only.
- **`agent-profiles.md:319`** (both copies) says the result shows `Session: <id>` whether the agent finished, stopped or failed. A failed batch member does not (m5).
- **Docs subagent:** I handed item 8 to a subagent. It did not deliver a report, even after two requests, so everything under item 8 below comes from my own checks.

## Checked and OK

**Gates**
- `ruff check .`: All checks passed.
- `check_citations.py --check`: 914 gated, none drifted.
- `check_types.py`: 0 errors in 303 files.

**Tests**
- On Python 3.12.13 (macOS), `tests/agents_ext`, `tests/agents`, `test_child_session_warning`, `test_permission_mode_flag`, `test_tool_usage_stats`, `test_transform_messages` and `test_citation_drift`: 2712 passed, 11 skipped.
- On 3.11.15 and 3.14.5, in separate venvs under /tmp, the three new files plus the #604 reports file and `test_child_session_records`: 337 passed on each.

**Item 10 (regressions)**
- A fresh spawn's argv is byte-identical between f3e76fbb and HEAD across 1,296 combinations of profile and parent state. These include 264 route pins, 432 `--thinking` flags and 144 `--no-tools` cases (`probe_argv_equiv.py`).
- The changes to the #604 test pins are updates to the new expected output, not weakened checks.

**Item 1 (never wider), driven through the real hook**
- A YOLO parent (TUI and `-p`) gives `plan`.
- A parent widened with yolo, all tools, `-nc` dropped, a new model and a new thinking level gives a resume that keeps `plan`, `grep,read`, `-nc`, `openai/gpt-5` and `high`.
- A narrowed parent gives a narrower resume.
- A profile edited to add tools still gives `[read]`.
- A profile edited to `approval_mode: auto` under an `auto-accept-edits` parent gives `plan`.
- A first run under yolo with the parent now at default gives `plan`.
- A project-scope profile shadowing the name gives `RESUME_PROFILE_CHANGED`.

**Item 2 (doors)**
- `resume_granted` is reached only from `_execute`.
- `/agents run` (`commands.py:1320`) and RPC (`control.py:796`) go through `spawn`, not resume.
- The parser refuses `tasks` and `mode` on a resume, so batches cannot carry one.
- `RpcChannel` refuses a resume plan.
- An extension calling `api.subagents` still hits the runtime floor.

**Item 3 (untrusted records and files)**
- Every limits field is validated.
- The child file path is derived, never read from the record.
- The `rel` comparison, origin parent, origin key, header id, symlinked file and symlinked directory checks all have tests that fail when the check is mutated away.

**Item 4 (refusals and concurrency)**
- Refusals land before any process, budget charge or record (tests fail under mutation).
- The `agent` tool is `execution_mode="sequential"`, so two resumes in one turn run hook then execute one at a time. There is no in-process concurrent resume.
- The lock probe never blocks: 0.0001–0.0004 s. It reports contention from another process as `IN_USE` and a symlinked sidecar as `LOCK_ERROR` ("Too many levels of symbolic links").

**Item 5 (dangling tool calls)**
- The real-child test passes.
- Mutations to the transform text and to `is_error` are both caught.
- The #604 trail labels an unfinished call "(no result)".

**Item 6 (cost)**
- A real child against a mock SSE server: each of three runs reports 10 in / 5 out with `turns=1`, so no history is replayed.
- The parent's `get_session_stats` gives runs=3, pending=0, 30/15 tokens.

**Item 7 (rendering)**
- The description head is 1525/2048 bytes, the preamble is 290 bytes, and the resume budget is 64512.
- The Session line appears on single successes and on ok members.
- The resume guidance replaces the old line only when there is a session; the print condition is unchanged.
- No path appears in the refusal texts.

**Item 8 (docs, my own checks)**
- These match the code: 1525/2048, 64512, 290 bytes, 13/14 dialog rows, the D13 preamble quote, the `RESUME_GUIDANCE` text, the D3 paragraph, the listing caps 10/2048/80/40, 2 MiB scan, and all 18 cited `RESUME_*` names exist.
- The two guide copies are byte-identical (`cmp`).

**Item 9 (mutations)**
- 30 mutants, 27 caught. They cover:
  - the ceiling in the pre-filter, the dialog and the runtime floor;
  - each intersection field and the model and thinking pins;
  - path and forgery checks;
  - the hook and belt refusals and the lock probe;
  - a reused key, a write to the child file, and an unrecorded `resumes`;
  - the transform.

**Lane notes**
- The lone-surrogate `UnicodeEncodeError` on a `run` already exists at f3e76fbb.

## Not measured

- The TUI in `uv run aelix`: the resume dialog at 80 columns, the tool card for `action` and `id`, and the Session line.
- A run against a real model.
- The `-p` restart flow.
- Windows.
- Python 3.13 for the new tests.
- The full suite.
- A parent session opened read-only that resumes.

## Addendum (2026-10-30, after the main report)

Sent to the lead as an idle notification after the report above, once the docs subagent's report had arrived. Copied verbatim; line numbers refer to `54cd7f61`.

The docs subagent's report came in after my main report, so I re-checked its claims myself and sent the lead an addendum. It adds five documentation mismatches, all wording only. My Blockers and Majors stay the same: no blocker, and M1 (nothing tests that the ceiling comes from the first run) is still the most serious finding. The worktree is still clean at 54cd7f61.

The added mismatches, most misleading first:

1. **`CHANGELOG.md:38`** says "`tasks`, `mode`, `cwd` and `background` are refused on a resume". The code refuses only values that would change the run:
   - accepted: `mode:"single"`, `tasks:[]`, `background:false`;
   - refused: `cwd:""`, `mode:"parallel"`.

   ADR D1 states the rule exactly.
2. **ADR-0277:279** says `profile_source` must be an absolute path. `ChildLimits.from_record` does not check that (`child_session.py:310`): a relative `profile_source` is accepted, while a relative `cwd` is refused. It has no security effect, because a relative source never matches the resolved profile, so the resume is refused with `RESUME_PROFILE_CHANGED`. Either the doc or the check should change.
3. **ADR-0277:529-530 and the decisions README row for 0277** say the argv is built only from the recorded limits. That overstates it:
   - `--permission-mode` comes from `_tighten(grant.mode, floor)` at `runtime.py:1399`;
   - the trust flags, `--no-agents`, `--session` and the task do not come from the limits either.

   ADR D8 says it correctly: the limits supply the tools, context files, thinking level and model flags.
4. **ADR-0277:424-425** says the `RESUME_*` refusal sentences live in `child_session` and the runtime. All 18 are in `child_session.py`; `runtime.py` defines only `RESUME_PREAMBLE` and `RESUME_PREAMBLE_RESERVE`.
5. **ADR-0277:191-192** misquotes ADR-0243 §3. The meaning is the same, but the wording is not verbatim.

Its finding about the guide (`agent-profiles.md:318-324` describing single-mode behaviour as if it held for batch members) is the same as my m5.

One claim neither of us could confirm: ADR-0277:732-733 says lane B ran its files on Python 3.11, 3.13 and 3.14, but `610-lane-b-notes.md` does not say so. I ran the new tests on 3.11 and 3.14 only.
