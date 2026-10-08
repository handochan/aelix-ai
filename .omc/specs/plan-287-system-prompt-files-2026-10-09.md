# Issue 287 implementation plan

Base: e47f1101 (isolated feat/287-system-prompt-files worktree).
Scope: https://github.com/handochan/aelix-ai/issues/287
Authoritative design: docs/decisions/0257-system-prompt-file-discovery.md.
Reference: Pi latest remote HEAD ce950d78f424dcaf9f5d6a03ce80ab141130eb1d,
read through GitHub API, with Pi issue 5049 checked closed; original Pi checkout untouched.

1. Discover global and trusted current-project SYSTEM.md / APPEND_SYSTEM.md at
   each shared prompt composition invocation; handle BOM/blank/failure safely.
2. Preserve explicit flag and profile precedence, document delegated child flags.
3. Add project trust resource predicates, loaded-source provenance and startup banner.
4. Cover composition, trust, actual harness rebuild, explicit/child flags and failures.
5. Update ADR, historical deferred items, README, guides, changelog and bundled docs.
6. Repair source citations mechanically, re-derive edited anchors manually, validate.
7. Run full gates and real OAuth requests/TUI, independent review, commit; root publishes.

Implementation is complete. Independent review approved by issue_197 agent
(67 focused and 36 composition cases plus real request evidence); no blocker.
Do not touch original /Users/handochan/dev/aelix-ai: Claude Code is working there.
No push, PR, issue comments or Project mutations from this agent; root owns delivery.
