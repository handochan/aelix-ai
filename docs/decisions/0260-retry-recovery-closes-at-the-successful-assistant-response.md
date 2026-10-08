# 0260. Retry recovery closes at the successful assistant response

Status: Accepted
Date: 2026-10-09
GitHub: [#197](https://github.com/handochan/aelix-ai/issues/197)
Relates: ADR-0128, ADR-0130, ADR-0215, ADR-0251.
Supersedes: ADR-0128 and ADR-0215 only for successful retry completion timing.

## Context

A recovered provider response can request a tool, which then runs before another
LLM call. Aelix waited until the entire `_run()` returned to reset its retry
counter and emit `auto_retry_end(success=True)`. The TUI therefore kept showing
“Retrying” throughout successful tool work and later responses. A later provider
failure also inherited the earlier call's spent retry budget.

The regression reproduces both problems through the real harness and agent loop:
while a recovered tool waits on a release event, the recovery event is absent;
with one retry allowed, two separate failures separated by a successful tool
response leave the second failure unrecovered. Before the fix those two tests
failed and the four failure/ordinary-response controls passed.

Current Pi confirms the earlier boundary: its `message_end` handler resets the
retry counter and emits recovery on the successful assistant response, before
tools or later responses finish. Reference inspected on 2026-10-09:
[`agent-session.ts` at `ce950d78`](https://github.com/earendil-works/pi/blob/ce950d78f424dcaf9f5d6a03ce80ab141130eb1d/packages/coding-agent/src/core/agent-session.ts#L1109-L1118).
This is a reference observation under ADR-0235, not a new parity requirement.

## Decision

When a retry sequence is active, the harness MUST reset its counter and emit one
successful `auto_retry_end` immediately after a successful assistant
`message_end` has passed through the replacement hook, persistence and ordinary
subscriber notification. Subscribers of the recovery event see the reset counter.
This includes a response ending in tool use. The prompt remains busy until its
tools and remaining responses finish; a subsequent provider failure starts a
fresh retry sequence.

The decision uses the message left by the hook reducer. An `error` or `aborted`
assistant cannot signal success. A recorded OAuth/setup failure retains its
authoritative classification from ADR-0251 even if a hook replaces its message
with a successful-looking one. Both response recovery and terminal completion
exclude the recorded failure by identity, so a rewritten refusal closes as
failure. User and tool result messages cannot close the sequence.

Existing terminal failure, abort, exhausted-budget and raised-exception cleanup
remain in place. The prompt's terminal check sees a zero counter after early
recovery, so it does not emit a duplicate. No new event type or TUI-only inference
is needed: the existing subscriber clears the widget and records success when
it receives the earlier `auto_retry_end`.

Subscriber fanout still awaits listeners sequentially. Cancellation during an
async listener can stop delivery to later listeners; resetting the counter and
avoiding duplicate completion does not guarantee delivery to every subscriber.

## Verification boundaries

`tests/test_retry_recovery_197.py` holds real tool work to establish recovery
before the prompt settles, checks fresh budgets across two failures, observes
the reset counter, excludes duplicate completion, and covers errors, aborts,
hook replacement, recorded setup failures and ordinary responses. Regressions
also cover a rewritten setup refusal and actual `OAuthRefreshError` exceptions
from the auth callback (HTTP 502 then HTTP 401) whose refusal a hook rewrites.

The actual `uv run aelix` TUI was driven through a PTY with a local OpenAI-compatible
provider: a deliberately truncated response triggers the harness retry; the
recovered response executes the real bash tool; the next model response is held
open. On the resulting terminal screen “Retrying” is absent while “Working…”
and the held response remain visible. The transcript has already recorded retry
success, and the following provider request contains the bash result.

This local provider proves the controlled retry/TUI path; it is not a real-model
retry-quality claim. A separate live OpenRouter `openai/gpt-4o-mini` call returned
`RETRY_197_LIVE_OK`. Local checks were on macOS / Python 3.12; other platforms
require the PR's CI matrix.
