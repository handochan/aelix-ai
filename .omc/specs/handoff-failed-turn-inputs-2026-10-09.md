# Issue 320 handoff — failed turn inputs (2026-10-09)

## Baseline and scope

- Original reproducer base `e47f1101f8b7499125ff55f0ee18d8b9873d8cdf`.
- Current main base `6b5ffc81` after the 2026-10-09 #192 merge. Rebased version
  preserves main's Python 3.13 CI, TLS/process behavior and updated documents.
- Branch `fix/320-failed-turn-inputs`; isolated worktree
  `/Users/handochan/dev/aelix-ai-worktrees/codex-320`.
- Plan `.omc/specs/320-plan.md`; decision ADR-0258. Original checkout and
  other issue worktrees were never edited. Root owns board, push and PR.
- Current core contains #376 model refusal, #379 auth retries and #334 whole
  prompt ownership. Their contracts remain in place.

## Implemented behavior

`message_end` commits the final hook-reduced message to live state, followed
by the existing session append. Successful loop return no longer bulk-appends.
Synthetic failures use this same path, with `agent_end` taking its snapshot
after the failure commit. Completed users/assistant/tool messages survive a
later provider throw or cancellation, once.

The first `_run` receives original queued and live input identities separately
from hook-generated messages. A failed/cancelled context read restores those
inputs FIFO, ahead of late arrivals. A loop cancellation before input commit
restores only the uncommitted remainder, synchronously before cleanup awaits.
Committed inputs and retry inputs never replay. Model preflight remains before
message construction/drain, so rejected prompts write nothing.

Citation maintenance updated positional source anchors and stale comments
describing the deleted bulk append. These are comment/doc relocations; TUI
rendering code is unchanged. #367 no-session tests now expect its already
committed refused-turn input to remain in history, matching session behavior.

## Evidence

All detailed local output is under `.omc/probes/320/` (ignored artifacts).

- Initial probe `baseline.py`, `baseline.txt`: actual JSONL disk contained
  `Q,x,[error]` while live state contained only `[error]`. Context-read failure
  lost `Q2,x2`, with no provider call and empty queue.
- `tests/test_failed_turn_inputs_320.py`: **16 passed**. Includes disk/state,
  subsequent delivery counts, reducer replacements, completed tool turn,
  adapter partial error, setup exception/cancel, partial lifecycle admission,
  abort/outer cancellation, retry setup failure, and #376 refusal.
- Copied base core package with identical regression file:
  **13 failed, 3 passed** (`base-regressions.txt`). Baseline passing cases guard
  existing adapter-error, model-refusal and retry-input behavior.
- Focused related harness/retry/cancellation/reducer suites:
  **176 passed in 1.29s** (`focused.txt`).
- Affected packaging/gate/citation/context-meter/TUI smoke recheck:
  **161 passed in 17.41s** (`recheck.txt`).
- Raw `uv run pytest -q` started before the final admission refinement and
  citation/fixture corrections: **61 failed, 14431 passed, 45 skipped,
  104 warnings, 3 errors in 1003.59s** (`full-tests.txt`). It is not a clean
  full-suite claim for the final commit. The failures were 58 inherited
  `NO_COLOR=1`/`TERM=dumb` TUI cases, 2 #367 old-loss assertions, 1 citation
  gate; errors were the starter fixture choosing system Python 3.9.
- All **64 failed/error nodeids**, plus the final 16 new cases, rerun on the
  final tree with `NO_COLOR` unset and `TERM=xterm-256color`:
  **80 passed in 5.81s** (`full-failures-recheck.txt`). A local
  `uv pip install pip hatchling` gave the isolated 3.12 interpreter the builder
  tools, avoiding the old system interpreter; no dependency files changed.
- Additional raw TUI pass with NO_COLOR unset but TERM=dumb:
  **2036 passed, 10 failed**. Normalized renderer/stream/width subset:
  **193 passed**; render-width E2E **9 passed**. All terminal-related failures
  passed under the normalized environment; no production workaround added.
- `uv run ruff check .`: **All checks passed!**
- `uv run pyright`: **0 errors, 1 pre-existing warning** about SessionStorage
  generic variance. `git diff --check`: clean. New regression file passes
  `ruff format --check`; unchanged legacy formatting was retained.
- `uv run python scripts/check_citations.py --check`:
  **941 gated, none drifted**; 18 recorded ambiguous anchors are non-gating.

## Actual model execution

OpenRouter `openai/gpt-4o-mini`, with credentials read from the original .env
without printing them or changing global configuration. `live.py` used actual
provider responses and JSONL, with controlled failures:

```text
failure: controlled post-response failure for Issue 320
first real response: ['LIVE320']
failed-turn state/disk agree: True
failed-turn queue: []
second real response: ['BRAVO']
second request original input counts: 1 1
setup failure: controlled context read failure for Issue 320
setup queue: ['Also remember marker CHARLIE.', 'Remember marker DELTA.']
third real response: ['CHARLIE DELTA']
recovered setup input counts: 1 1
final state/disk agree: True
```

The production CLI, after the admission correction, also ran with actual model:
`uv run aelix --provider openrouter --model openai/gpt-4o-mini --session-dir
<isolated>/cli-sessions --no-tools --no-extensions --no-context-files --offline
-p 'Reply with exactly CLI320.'` → `CLI320`, exit **0** (`cli-live.txt`).

## Independent review

Root reviewed the implementation separately and found the admission gap:
scheduling `agent_loop` is not committing its prompts. Cancellation in
agent_start/message_start/message_end could precede the state append.
Identity tracking and restoration of the uncommitted remainder repaired it;
three adversarial lifecycle cases plus retry context failure pin the boundary.
Root subsequently reported no further blocking defect and **58 passed** for
the new regressions plus existing auto-retry tests.

## Next actions

Root should reconcile #197's neighboring message_end change in a disposable
integration worktree and run combined #197/#320 regressions. This branch is
independent from the common base; no merge is authorized here. Push/create PR
and let the remote OS/Python CI matrix supply a clean complete full-suite gate.
No Windows result or remote CI result is claimed by these local macOS checks.

## New-base rebase verification (2026-10-09)

The initial issue commit `d7c1828e` was rebased onto `6b5ffc81` without merge
conflicts. #192 does not edit the harness core. Comparing the rebased core,
new regression file and ADR-0258 against that initial commit yields no diff;
the upstream TLS/process/CI implementation and its documentation remain intact.

New-base focused command, with terminal environment normalized:

```sh
env -u NO_COLOR TERM=xterm-256color uv run --no-sync pytest \
  tests/test_failed_turn_inputs_320.py \
  tests/test_harness_next_turn_fault_injection.py \
  tests/test_harness_cancel_gives_the_phase_back.py \
  tests/test_harness_prompt_holds_the_turn_through_its_tail.py \
  tests/test_message_end_replacement_reducer.py \
  tests/test_agent_harness_auth_retry_379.py tests/test_auto_retry.py \
  tests/harness/test_overflow_recovery.py tests/test_agent_harness.py \
  tests/test_state_messages_derived.py \
  tests/harness/test_harness_turn_gate_367.py \
  tests/harness/test_abort_tears_down_the_provider_socket.py \
  tests/providers/test_tls_strict.py tests/process_tree/test_wait_released.py \
  tests/test_ci_python_matrix.py tests/packaging_gate/test_starter_scaffold.py \
  -q --tb=short
```

Result: **486 passed in 13.72s**, in `rebase-focused.txt`. Ruff passes; Pyright
has **0 errors and the same 1 existing warning**; citation check has **941
gated, none drifted**. `--fix`/`--check` reverify current source anchors after
the automatic merge. Output is in `rebase-lint.txt`, `rebase-pyright.txt`, and
`rebase-citations-before.txt`.

Root reported the earlier combined #197/#320 integration's **632 passed**;
that result belongs to the old base and is not presented as a current-main
full-suite pass. The #197 agent is creating a fresh integration worktree on
`6b5ffc81`. Root owns the force-with-lease push to existing PR #423. This
worktree performed no remote writes during the rebase.

## Refuted assumptions and limits

- Loop ownership alone makes input safe: false, demonstrated by early
  lifecycle cancellation; message-end state commit is the admission boundary.
- Restoring the entire `_run` argument is safe: false, it would repeat
  before_agent_start injection. Restoring after every failure also repeats
  provider-visible input; only uncommitted original inputs return to the queue.
- A failed no-session turn should drop user input while the same attached
  session preserves it: this was the defect, not a contract to retain.
- Bare full-suite failures here prove a production rendering regression:
  false; inherited terminal environment caused the same style/width failures,
  and normalized reruns pass.
- Unended streaming fragments are still uncommitted, while adapter-reported
  partial error messages and completed tool turns are preserved. No unfinished
  tool-call transcript is invented (Pi issue #9306).
- Storage-write failures still follow the existing logging policy. State
  retention does not claim a rejected disk write became durable.
- Earlier input/before_agent_start-hook failures retain ADR-0246's existing
  behavior; this task closes #320's later context/loop window.

Pi upstream source was read at pinned
`ce950d78f424dcaf9f5d6a03ce80ab141130eb1d`. Local Pi checkout was stale and was
not changed. Its message_end state append is a reference, not a parity mandate.

## Ordered integration refresh 2026-10-09

Independent review combinedoriginal#320 withfinalrepaired#197:315tests and21newinput/cancel/OAuth/persistence/FIFO boundaries pass;46targetedpass; realmodel2calls verified originalheads' preservedhistory, finalOAuthrepair verifiedcallbackprobes. Callerwrite-cancellationdurability andfullsubscriberfanout remainoutsideguarantee. Evidence codex-review-423/.omc/specs/handoff-review-423-2026-10-09.md.

Rootrebasedonto#421predecessor1ea65616 (includes429/428/419/422). Coreconflict preserved message_endcommit followedby197recovery;otherconflictswerecitationnumbers. Upstreamlockretainedplus previouslyvalidatedincomingnewanchors;17movedtargets readagainstactualsource thenfix/check936gatednone drifted. CombinedharnessAST matchesindependentlyvalidated315testcopy exactly (comments differ). Currentfocusedfailed-input/retry/context/abort/overflow99passed3.88s, Ruffclean,type287files0errors/spike retained. FinalcurrentheadplatformCIrequired.
