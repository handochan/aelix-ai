# Issue #197 — retry recovery plan

Base: `e47f1101f8b7499125ff55f0ee18d8b9873d8cdf`.
Branch: `fix/197-retry-status`.
Worktree: `/Users/handochan/dev/aelix-ai-worktrees/codex-197`.
Original checkout and other worktrees are not edited by this lane.

## Acceptance and implementation

1. Reproduce successful retry leaving the indicator up while subsequent work
   remains active, through the actual harness/loop rather than a mocked `_run`.
2. Confirm current Pi behavior and use the existing retry event contract.
3. Close the successful sequence at the reduced assistant `message_end`, before
   tool execution or a later LLM call; reset the counter before notification.
4. Preserve terminal failure/abort cleanup and recorded OAuth/setup decisions.
5. Verify independent later failures get independent budgets and no duplicate
   end appears when the overall prompt finishes.
6. Run local full gates and actual `uv run aelix` TUI plus actual model calls.
7. Obtain separate root adversarial review, record evidence, commit one issue.
   Root owns GitHub Project updates and the PR. Do not merge.

## Evidence

Current upstream Pi inspected at `ce950d78f424dcaf9f5d6a03ce80ab141130eb1d`
(`agent-session.ts` message-end success reset). Upstream retry issues were
queried separately; none replaces this reproduced local defect.

Initial regression execution: `uv run pytest tests/test_retry_recovery_197.py
-q -p no:cacheprovider` → **2 failed, 4 passed**. The failures were absent
recovery while the tool was held, and exhausted inherited budget on a later
provider call. Final focused verification adds a recorded-setup replacement
control, bringing this file to seven tests.

The root independently reviewed the final core/test/ADR change and ran the
retry regressions plus existing retry tests: **49 passed**, no blocking finding.

The full initial run recorded **58 failed, 14428 passed, 45 skipped, 3 errors**
under inherited `NO_COLOR=1` / `TERM=dumb` and missing test build tools. All
61 failed/error nodes and the final seven regressions passed (**68 passed**) in
the corrected terminal environment after isolated pip/hatchling installation.
The handoff records the exact distinction; PR CI will run the clean full gate.

The live TUI command and wire/screen evidence are reproduced by:

```bash
uv run python .omc/specs/retry-recovery-197-probe.py
```

It launches **`uv run aelix`** in a real PTY, injects truncation through a local
HTTP/SSE provider, executes the production bash tool, and holds a following
response. It reads the painted screen before release/teardown. This is a
controlled local provider probe, distinct from external model verification.
