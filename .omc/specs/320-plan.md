# Issue 320 implementation plan (2026-10-08)

Base: e47f1101, isolated fix/320-failed-turn-inputs worktree. No original-tree edits.
Rebased 2026-10-09 onto main `6b5ffc81` (#192). Core behavior unchanged; current
base checks and the original reproducer evidence are recorded in the handoff.

1. Reproduce state/session disk divergence on thrown provider and missing inputs
   before loop ownership on Session.build_context failure.
2. Record ADR-0258: message_end commits the hook-reduced message to state and
   session together; successful return must not append twice. Completed partial
   turns survive later failures. Pre-loop setup failure restores the submitted
   live input and drained queued inputs in FIFO order, without replay after
   loop ownership. #376 model refusal remains before message construction.
3. Regression tests compare delivery, live state, reopened JSONL, replacement
   hooks, successful/retried/error/abort/cancel paths and the setup boundary.
4. Focused/full tests, lint, format, pyright. Actual-model live run with session
   and controlled failure at setup/provider boundaries. Retain exact output.
5. Independent review coordinated by root; fix findings, commit implementation,
   and handoff. Root owns push, PR, board.

Pi reference: upstream ce950d78f424dcaf9f5d6a03ce80ab141130eb1d (2026-10-08
API lookup), processEvents message_end appends event.message to state. Local
reference 6671c604 is stale; upstream source downloaded to local probes.
Pi issue 9306 warns unmatched partial tool calls can break later requests;
preserve completed message_end records, avoid inventing unended tool calls.
