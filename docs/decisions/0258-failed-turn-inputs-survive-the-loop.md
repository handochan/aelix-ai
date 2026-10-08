# 0258. Failed turn inputs survive the loop

Status: Accepted
Date: 2026-10-09
Issue: #320
Relates: ADR-0023 (prompt lifetime and cancellation), ADR-0246 (pre-loop queue
restore), ADR-0145 (message_end replacement reducer), ADR-0247 (failed-turn replay).

## Context

Measured on `e47f1101`, with a real JSONL session and a provider that throws:

```text
provider saw: ['Q', 'x']
state: ['[error] provider blew up']
queue: []
reopened disk: ['Q', 'x', '[error] provider blew up']
```

The loop emits `message_end` for the queued input and live input before calling
the provider. The session persists each event, but the harness appended the
loop's entire return only after success. A thrown exception discarded that
return, so live state disagreed with the resumed session. Completed assistant
and tool messages from an earlier iteration were lost in the same way.

When `Session.build_context()` throws, the loop has not received the inputs
yet. ADR-0246's restoration stops at the `_run` call, so both the drained queue
and live input were lost, with no provider call and no disk record.

Upstream Pi at `ce950d78f424dcaf9f5d6a03ce80ab141130eb1d` appends each
`message_end` to state in `packages/agent/src/agent.ts:575-578`. This is a
reference for the commit boundary, rather than a new parity requirement.

## Decision

1. **`message_end` commits the reduced message to live state.** Run the hook
   replacement reducer first, append its final message to state, then attempt
   to append the same message to the session. Provider failure, a later failed
   tool iteration, and outer cancellation MUST preserve completed messages.
   Session-write failures retain their existing logging policy; this change
   does not claim a failed storage write succeeded.
2. **Loop success does not append messages again.** The loop return still
   carries each replacement for the caller and loop context, but state already
   owns the message. Synthetic failure messages use this same commit path.
   Failure `agent_end` takes its state snapshot after `message_end`, including
   a replacement made by a hook. Existing abort handling still appends its
   empty in-memory aborted marker without a disk message.
3. **Restore inputs until their message-end commit.** The first
   `_run` receives `pending_inputs` containing drained messages and the live
   user message. If context construction raises or is cancelled, it prepends
   those inputs to `next_turn`, ahead of anything queued during the await.
   The original exception/cancellation propagates and the prompt's existing
   claim releases normally. A later prompt delivers them once. The loop can
   also be cancelled in `agent_start`, `message_start`, or the `message_end`
   reducer before it commits an input. Track original input identities and
   remove each only after its state commit. On any loop exit, synchronously
   restore the uncommitted remainder before cleanup awaits. Thus a queued
   input already committed before cancellation stays in history, while an
   uncommitted live input returns to the queue; neither is duplicated.
4. **Do not restore generated hook messages.** `before_agent_start` runs again
   for the later prompt. Restoring its injected messages would duplicate them.
   Images and transformed user input stay on the original message objects.
   Retry runs pass no pending inputs: their original inputs are already
   committed, so a context-read failure during a retry does not requeue them.
5. **Model preflight keeps its existing earlier boundary.** ADR-0239/#376
   refuses an unrunnable model after the input hook and before constructing or
   detaching any inputs. A refused prompt writes nothing and leaves queued
   messages intact.

## Consequences and limits

Provider failures now leave the same completed transcript in live state and
JSONL. Delivered inputs remain historical context; they are never queued again
merely because the turn failed. A setup failure restores unsent input instead.

No session format, public method, provider, or TUI renderer changes. Hooks and
subscribers retain their existing event order. A `message_end` hook sees state
before that message's commit; subscribers see its final commit.

Unended streaming fragments still have no `message_end` and are not invented
as completed transcript entries. Adapter-reported partial error messages do
have `message_end` and are preserved. Pi issue
[9306](https://github.com/earendil-works/pi/issues/9306) records why blindly
persisting unfinished tool calls can make the next request invalid.

This closes ADR-0246's measured `build_context` window. Scheduling the loop
alone is not an input commit, as cancellation probes at its early lifecycle
awaits demonstrate. The older guard still does not wrap the whole `_run`:
doing that indiscriminately requeues already delivered inputs. Earlier
`input`/`before_agent_start` failures keep their existing
ADR-0246 behavior, and storage durability remains the storage contract.

## Verification

`tests/test_failed_turn_inputs_320.py` checks provider delivery and retained
messages with and without JSONL, a reopened session, a subsequent request,
hook replacements on success and synthetic failure, completed tool turns,
adapter-reported partial errors, FIFO restoration after exception/cancellation,
hook injection without replay, provider-time abort/cancellation, and #376
preflight. Adversarial cancellation at the three lifecycle admission seams
checks partial input commitment, and a retry's context failure checks that
already committed inputs do not return to the queue. Existing retry/compaction
tests retain their assertions; their
private `_run` doubles accept the new pending-input argument.

Local commands and live-model output are retained in the issue's isolated
worktree under `.omc/probes/320/` and recorded in its handoff.

The 16 regression cases pass on the fix; a copied `e47f1101` core package
produces **13 failed, 3 passed** with the identical file. The passing baseline
cases guard existing adapter-error handling, model refusal, and retry input
ownership rather than the newly repaired paths.

Actual-model validation used OpenRouter `openai/gpt-4o-mini`, real JSONL
storage, and controlled failures. A real `LIVE320` response followed by a
thrown close-out retained the inputs and completed answer in both stores.
The next real response was `BRAVO`, and each original input appeared once in
the request. A context-read failure restored `CHARLIE` and `DELTA`; the next
real request contained each once and answered `CHARLIE DELTA`. Final state
and reopened disk agreed. The production CLI also returned `CLI320` with
exit code 0. These checks use a real model; the injected failure is controlled,
not a claim that an external network failure happened during the check.
