# Issue #197 — retry status handoff

## Baseline and final behavior

Base `main` / branch base: `e47f1101f8b7499125ff55f0ee18d8b9873d8cdf`.
Branch `fix/197-retry-status`, isolated worktree
`/Users/handochan/dev/aelix-ai-worktrees/codex-197`.
Plan: `plan-197-retry-recovery-2026-10-09.md`.
Decision: `docs/decisions/0260-retry-recovery-closes-at-the-successful-assistant-response.md`.

The successful reduced assistant response closes the retry before remaining
tools/LLM calls finish. The existing TUI recovery listener removes “Retrying”
while “Working…” remains. The counter resets before recovery subscribers run;
a later unrelated provider failure starts at attempt 1. Failure, abort and
recorded OAuth/setup classification remain intact.

The core change is isolated to the `message_end` boundary and a private
`_close_recovered_retry` helper. No input replay behavior or TUI code changes
were needed. Existing cited core statements retain their line positions;
the citation lock did not need repair.

## Verification

- `uv sync`: isolated editable workspace packages installed, Python 3.12.13.
- Before fix: `uv run pytest tests/test_retry_recovery_197.py -q -p
  no:cacheprovider` → **2 failed, 4 passed**, the intended recovery and budget
  failures.
- Final focused harness regression command:
  `uv run pytest tests/test_retry_recovery_197.py tests/test_auto_retry.py
  tests/test_agent_harness_auth_retry_379.py -q -p no:cacheprovider` → **70 passed**.
- Existing retry TUI plus focused harness slice:
  `uv run pytest tests/test_retry_recovery_197.py tests/test_auto_retry.py
  tests/test_agent_harness_auth_retry_379.py tests/tui/test_run_tui_smoke.py
  -k 'retry or recovery' -q -p no:cacheprovider` → **78 passed, 81 deselected**
  before the additional setup-record regression was added; that new test is in
  the final 70-test harness run above.
- `uv run ruff check .` → **All checks passed**.
- `uv run python scripts/check_types.py` → **287 files analyzed, 0 errors**;
  all three inverted narrowing assertions still error, type gate PASS.
- `uv run python scripts/check_citations.py --check` → **955 gated, none drifted**;
  the existing 19 ambiguous anchors remain recorded.
- `git diff --check` → exit 0.
- `uv run python .omc/specs/retry-recovery-197-probe.py` → exit 0; real
  `uv run aelix` PTY: **3 HTTP requests**, retry visible before recovery,
  **retry absent with held next response and Working visible**, success in
  transcript, production bash output present in the next model request. Raw
  stream, before/after/final terminal grids and output are retained locally in
  `.omc/specs/issue-197-evidence/`.
- Actual OAuth model:
  `uv run aelix --provider openai-codex --model gpt-5.5 --thinking off
  --no-tools --no-session --no-extensions --no-skills --no-context-files
  --offline -p 'Reply with exactly RETRY_197_LIVE_OK.'`
  → **RETRY_197_LIVE_OK**, exit 0.
- Actual OpenRouter `openai/gpt-4o-mini`, same flags/prompt, loading credentials
  from the original checkout's `.env` with `dotenv.load_dotenv` inside a local
  Python subprocess wrapper → **RETRY_197_LIVE_OK**, exit 0. Credentials were
  neither printed nor copied into this worktree.
- Root separate adversarial review: **49 existing/new retry tests passed**;
  no blocking code or contract finding.
- Initial full command: `uv run pytest -p no:cacheprovider -q` → **58 failed,
  14428 passed, 45 skipped, 3 errors** in 1013.61 s. This process inherited
  `NO_COLOR=1`, `TERM=dumb`: the 58 renderer/TUI color failures matched the
  independently reproduced baseline environment failures. Three starter-build
  fixture errors came from missing pip/hatchling in this local environment.
- Test environment repair only: `uv pip install pip hatchling` installed pip,
  hatchling and their missing dependencies into this worktree's `.venv`.
  Product source and dependency declarations were not changed.
- Extracted all **61 failed/error node IDs** from the preserved full output and
  reran them plus the final seven new regressions using the equivalent of
  `env -u NO_COLOR TERM=xterm-256color uv run --no-sync pytest
  -p no:cacheprovider -q <all 61 node IDs> tests/test_retry_recovery_197.py` →
  **68 passed in 6.13 s**. Exact node IDs are retained in
  `.omc/specs/issue-197-evidence/failed-nodes.json`, with the initial full output
  and corrected subset output beside it. This is a full initial run plus a
  corrected failed subset, **not a claim of a clean repeated full-suite run**.
  The final seven regressions also cover the helper extraction after the initial
  suite's collection; the initial suite collected the earlier six-test version.

## Next action and remaining scope

Local implementation and verification are complete. Root creates the PR and
moves the Project item to review after this lane's commit. Do not merge.
PR CI owns a fresh full-suite run in its clean environment and Windows/Linux
and other Python-version evidence; local execution proves macOS / Python 3.12.
No live external-provider error was induced, so the deterministic retry proof
and actual external-model calls have separate evidentiary scopes.

## Repository rules that mattered

The original checkout belongs to a concurrent Claude Code task. Separate
worktrees, editable environments and one issue per branch preserve that work.
TUI behavior was inspected through the actual CLI and terminal screen, and the
runtime change received actual model execution plus separate review.
ADR-0260 partially supersedes the old successful retry timing; old failure and
interruption decisions are retained and explicitly linked.

## Refuted assumptions — do not repeat

- This was not just a stale TUI repaint: the core counter stayed spent and
  incorrectly exhausted the next provider call's retry allowance.
- Waiting until the user request finishes does not identify provider recovery.
  A successful assistant tool-call response already supplies that boundary.
- Original assistant payload alone does not decide recovery: replacement hooks
  can leave an error, and recorded setup failures remain authoritative even
  when a hook makes the message look successful.
- Catalog availability does not prove OAuth model availability:
  `gpt-5.4-mini` and `gpt-5.4` both returned ChatGPT-account unsupported **400**;
  `gpt-5.5` completed the actual OAuth call.
- A success transcript row can scroll off the current terminal grid. The probe
  checks its presence in the captured transcript and reads retry/widget/working
  state from the active grid before teardown.

## Delivery refresh after concurrent main movement

Claude Code merged #192 while this batch was running. Delivery base is now `6b5ffc814041de36866f79df71c51992ec76891c`. The original checkout was not edited by this workflow. Rebase completed without conflicts.

On this new base: focused tests 49 passed; Ruff passed; type gate 287 files / 0 errors with inverse assertions intact; citation gate 955 gated / none drifted. The initial full-suite and real-model observations above belong to the initial e47f1101 base. Fresh PR CI now includes Linux and Windows on Python 3.11, 3.12 and 3.13, plus both Windows installer shells.

## PR #421 terminal predicate repair — 2026-10-09

This is a separate implementation pass after the independent review recorded in
`/Users/handochan/dev/aelix-ai-worktrees/codex-review-421/.omc/specs/handoff-review-421-2026-10-09.md`.
Tracked state was clean at `07849e8acfc7a72fe612a52e297cdc146c2f595e`; existing
untracked evidence was preserved. Atomic issue lock directory
`/Users/handochan/dev/aelix-ai/.git/codex-issue-locks/197` was acquired with owner
`/root/review_429`. No main, remote, commit, push or rebase mutation occurred.
The code/doc/test changes below remain uncommitted for a fresh independent review.

The review found an inherited terminal-path gap. A transient OAuth refresh
(HTTP 502) followed by a refusal (HTTP 401 / invalid_grant) is correctly not
retried a third time, but a hook rewriting the refusal to stop_reason=stop made
the terminal arm report `auto_retry_end(success=True)`. The early helper already
honored the setup-failure identity; the terminal arm did not.

The minimal repair adds the same authoritative identity exclusion to the
terminal success predicate. Its line count is unchanged, so existing citation
positions are retained. ADR-0260 now explicitly covers both success predicates
and bounds the unchanged sequential subscriber cancellation behavior: an awaited
listener can stop delivery to later listeners. This pass does not change fanout,
abort policy, retry budget, default refusal behavior or message commit timing.

Two regressions were added to the existing `tests/test_retry_recovery_197.py`:
one rewritten recorded setup refusal and one actual auth callback raising
production `OAuthRefreshError` objects for HTTP 502 then HTTP 401. Both must end
as failure with attempt 1, counter zero and exactly two callback/stream entries.
The callback exceptions are synthetic and deterministic; no owner's auth store
or actual OAuth server is involved.

Executed commands and results (Python 3.12.13 / pytest 9.0.3 unless specified):

- Before repair, `.venv/bin/python -m pytest tests/test_retry_recovery_197.py -k 'rebuilt_refused_setup or rebuilt_oauth_callback' -q -p no:cacheprovider`
  -> 2 failed, 7 deselected in 0.11s on the old PR code.
- Same new test file run against a separate unmodified main environment at
  `/tmp/aelix-review-421-baseline`, with explicit `-c` to its pyproject and
  `PYTHONPATH` selecting this test file -> 2 failed, 7 deselected in 0.11s.
  Thus the gap is inherited from main, not introduced by early recovery.
- After repair, the same two-test command -> 2 passed, 7 deselected in 0.06s.
- `.venv/bin/python -m pytest tests/test_retry_recovery_197.py tests/test_auto_retry.py tests/test_agent_harness_auth_retry_379.py tests/test_message_end_replacement_reducer.py tests/test_session_message_end_wiring.py tests/test_tool_result_message_events_parallel.py tests/test_tool_result_message_events_sequential.py tests/tui/test_run_tui_smoke.py -k 'retry or recovery or message_end or replacement' -q -p no:cacheprovider`
  -> 104 passed, 82 deselected in 2.12s on the final source.
- Previous independent observer inputs re-executed against this implementation:
  `.venv/bin/python -m pytest -c pyproject.toml /Users/handochan/dev/aelix-ai-worktrees/codex-review-421/.omc/specs/review-421-evidence/test_observers.py -q -p no:cacheprovider`
  -> 8 passed in 0.22s. This reuse is implementation verification, not a fresh
  independent approval of this repair.
- Separate Python 3.11.15 / pytest 9.0.3 environment:
  `/tmp/aelix-repair-421-py311/bin/python -m pytest tests/test_retry_recovery_197.py tests/test_auto_retry.py tests/test_agent_harness_auth_retry_379.py -q -p no:cacheprovider`
  -> 72 passed in 1.30s.
- `.venv/bin/ruff check packages/aelix-agent-core/src/aelix_agent_core/harness/core.py tests/test_retry_recovery_197.py`
  -> All checks passed. `git diff --check` -> exit 0.
- `uv run --no-sync python scripts/check_types.py`
  -> 287 analyzed / 0 errors; all three inverse assertions still error; PASS.
- Citation targets were inspected after the initial line-shifting draft; the
  final predicate keeps the cited blocks at their original positions.
  `.venv/bin/python scripts/check_citations.py --fix` -> relocated 0 across 0;
  `--check` -> 955 gated / none drifted, 19 ambiguous anchors retained.
- `env PYTHONPATH=. .venv/bin/python .omc/specs/issue-197-evidence/terminal-repair-live.py`
  -> actual OpenRouter `openai/gpt-4o-mini` returned
  `REVIEW_421_REPAIR_LIVE_OK`, exit 0, on the final source. Only
  OPENROUTER_API_KEY was selected from the original .env and admitted to a
  minimal child environment. Home, AELIX_CODING_AGENT_DIR and AELIX_SETTINGS_PATH
  were isolated; owner's ~/.aelix and auth.json were not accessed or changed.
  This proves an ordinary actual-model runtime call, not an external OAuth
  refusal/retry test.

Raw command outputs and the safe live wrapper are retained in the existing
`issue-197-evidence/terminal-repair-*` files. No full suite or native Windows
execution was performed in this repair pass; root owns those gates. The earlier
independent PTY result belongs to the pre-repair early-recovery input.

Next: fresh independent review of this small predicate/doc/test diff, then
root's scheduled rebase and integration. Recheck the combination with #423's
message commit change. Do not call this implementation pass self-approved.

Fresh no-author-context acceptance on final repair:361focused+6independent cases passed; actual model/tool and actualTUI recovery/ESC/quit confirmed. Originalmain/PR counterfactuals fail; final repair passes. Source lint/type/citations clean. Root applied formatter onlyto newtestfile and reran9test cases pass. Standalone#197 emits aftersession/event observation; publicstate.messages visibility beforeturnreturn is not newly guaranteed here. #320's separatelyreviewed message_end commit supplies that in their finalcombined315test integration.
