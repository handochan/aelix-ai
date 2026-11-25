# 0320. Pending parent steering can end a background-agent wait

Status: Accepted (2026-11-24, #913).
Date: 2026-11-24
Issue: [#913](https://github.com/handochan/aelix-ai/issues/913)
Amends: ADR-0295 and ADR-0306.

## Context

In the #913 triage probe at `9227275b`, the parent called `agent(action="wait")`
while a loopback provider held the child. TUI, RPC and negotiated ACP all
accepted steering while the wait ran. The next parent model request carried the
steer only after the child had replied. A parent that needs to change direction
can otherwise remain in the wait for its configured limit (ADR-0295).

The owner said on 2026-11-24, “wait는 agent를 기다리는거고 그 중에 아무것도 못하면 안되는것같습니다”.
The owner conditioned the choice on the comparison, “우선 이 방향이 타당한건지를 먼저 판단해주세요. 타 sota 에이전트들은 어떻게 하는지. 그랬을때도
권장 방향이맞다먄 그렇게하세요”. After that research, the lead selected
the following behavior. [#913](https://github.com/handochan/aelix-ai/issues/913)

## Decision

1. An active `agent(action="wait")` can return successfully when the parent
   harness has a pending steer. The result starts with “Wait ended early because
   input is pending.” This wording covers a user steer and an extension's
   `send_message(deliver_as="steer")`. It gives the selected jobs' statuses, the
   limit if clamped and the usual completion-notice reminder. The ordinary user message
   carries the user's text and attachments once. Follow-up keeps its
   after-task boundary. The child keeps running. The wait preserves the
   existing empty-wait streak on a steer wake. [#913 tests](../../tests/agents_ext/test_wait_steering_913.py)
2. The wait captures its parent harness, subscribes before checking the queue
   and races the subscription with job changes and the original monotonic
   deadline. It rechecks both the jobs and the queue on every wake. Abort and
   session sealing keep their existing paths. Lifting a seal wakes the wait
   if the queued steer becomes eligible. Settled or unresolved jobs and
   posted stuck attention take precedence when observed together with a steer.
   An eligible steer observed at the deadline takes precedence over an empty
   timeout. A terminal job keeps its notice-posting grace. [#913 tests](../../tests/agents_ext/test_wait_steering_913.py) · [ADR-0307](0307-a-cancelled-delegations-report-is-its-calls-result-and-a-session-change-settles-the-turn-first.md)
3. The tool batch still completes before the normal queue drain. With a child
   held and its result released later, the scripted requests and stored
   transcript in the #913 tests show the earlier tool result and user message,
   followed by one eventual child notice. The separate #913 transport probe
   observed queue admission receipts over RPC and negotiated ACP while the child
   was held. [#913 tests](../../tests/agents_ext/test_wait_steering_913.py) · [#913](https://github.com/handochan/aelix-ai/issues/913)
4. The jobs facade captures the service's change event at call time, so the
   regression's single transition to unresolved wakes the wait even before
   its coroutine starts. The facade validates its lifetime at the call, on
   first execution and on notification. [#913 tests](../../tests/agents_ext/test_wait_steering_913.py) · [Facade lifetime tests](../../tests/jobs/test_facade_611.py)

## Alternatives

Pi at `d4760131` awaits tool execution before draining steering and waits for
the inner loop to end before draining follow-up. Aelix keeps those boundaries
and deliberately adds a wake to its bounded native wait. Codex v2's
`wait_agent` pending-input test shows an early tool result and a separate
ordinary user message in the next request. This preserves role and queue
semantics that copying input into a tool result would lose.
[Pi loop](https://github.com/earendil-works/pi/blob/d4760131efe0740b9b7f7ad1f126734b96a011c0/packages/agent/src/agent-loop.ts#L279-L315) ·
[Codex test](https://github.com/openai/codex/blob/a2c17b833cab61e48f7b4acd757c3738a5914d3c/codex-rs/core/tests/suite/pending_input.rs#L757-L841)

## Consequences

With a steer aimed at the waiting parent, callers may see an earlier
successful wait result and earlier parent model request while the child still
runs. Actual delivery depends on the existing tool-batch boundary. The
standalone follow-up path and the child-directed `agent(action="send")` keep
their own delivery boundaries. [#913 tests](../../tests/agents_ext/test_wait_steering_913.py) · [ADR-0306](0306-a-running-delegated-child-is-steered-through-a-command-inbox-on-its-stdin.md)
