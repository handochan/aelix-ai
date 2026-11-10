# Independent verify of #{{issue}} at {{head sha}}

<!--
Brief for a fresh-context verifier. In Claude Code, the aelix-verifier agent
(.claude/agents/aelix-verifier.md) carries the procedure, and this block is its input.
Do NOT include the implementer's report, summary or conclusions, or the lead's
opinion of the change. A verifier anchored on the lane's claims re-runs the lane's
evidence instead of finding what the lane missed.
-->

- **Issues:** #{{n}} {{, #m}}. Read each with `gh issue view <n> --comments`. Their
  acceptance checks are the contract.
- **Head:** `{{head sha}}` on `{{branch}}`, readable in `{{lane worktree}}`. Do not
  edit or commit there.
- **Base:** `{{base sha}}`. A base worktree with its own venv is at `{{base worktree}}`
  (or make one: `git worktree add --detach <dir> {{base sha}}` and
  `uv sync --frozen --all-packages` inside it).
- **Round:** {{"round 1, full" | "round N, delta only: range-diff <old>..<new>"}}
- **Scratch:** `{{/tmp/<n>-verify/}}`, for your probes, copies and command output.
  Your report is your final message, not a file: subagents' report-file writes have
  been refused (`references/claude-code.md`).
- **Known, accepted trade-offs** (decided, do not re-argue): {{list, or "none"}}.
- **Others running now (do not touch):** {{sessions and worktrees}}.
- **Credential for live runs:** `{{NAME}}` from `{{file}}`, by name only.

Return, as your final message, the verdict (READY or NOT READY), the findings tagged
must-fix, should-fix or nit (each with a reproducer path, the command and its
output), and what you did not measure.
