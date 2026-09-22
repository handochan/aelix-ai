# 0246. A drained queue goes back when the turn never started

Status: Accepted (2026-09-22)
Date: 2026-09-22
Supersedes/relates: ADR-0245 (the same shape one queue over — this ADR closes
the gap 0245's own audit found, left open and named, at the two hook awaits
0245 described, and measures the one further along that stays open; it also
corrects the remedy that section proposed, which was never tested and does not
work.
Nothing about the session-write queue changes), ADR-0022 (the next_turn
queue's lifetime and its no-session fallback, unchanged), ADR-0235 (pi is a
reference, not a target).
Issue: #311. The other half of #301's audit.
Probe: `.omc/specs/311-next-turn-drain.py` — nine arms. 1-4 are the defect and
the fix; ARM 5 reads the base file with `git show`, so it keeps measuring the
rejected alternative after this lands; 6, 7, 7b and 8 are the limits of what
the fix claims, added when review found the first draft of this ADR claiming
more than the code does. Live driver: `.omc/specs/311-live.py`, which runs on
the branch and, by `PYTHONPATH` overlay, on the base. The four sabotage arms
quoted below are `.omc/specs/311-sabotage.py`.

`AgentHarness.prompt()` took the next-turn queue away from the harness before
it had anywhere else to put it, and then awaited two hooks. Either one
throwing left the user's queued text in a dead local.

## What was broken, measured on `fad2e28`

```
core.py:1289-1290   drained_next = self._next_turn_queue
                    self._next_turn_queue = []
core.py:1295-1296   if drained_next:
                        await self._emit_queue_update()          # can raise
core.py:1299        injected = await self._emit_before_agent_start(text)  # can raise
core.py:1303        prompts.extend(drained_next)
core.py:1310        result = await self._run(prompts, ...)       # first place they are held again
```

Both awaits convert a handler exception into a raised `AgentHarnessError`
(`_emit_queue_update` at `core.py:2325-2340`, `_emit_before_agent_start` at
`core.py:4229-4244`; both numbers are this tree's — the block above is `main`'s), and a handler's `error_mode` defaults to `"throw"`
(`harness/hooks.py:2493-2500`). Between :1290 and :1310 the drained messages live
in the local `drained_next` and nowhere else, so either raise unwinds the
frame and takes them with it.

ARM 1 — one message queued through `next_turn()`, a `queue_update` observer
registered afterwards that raises, then `prompt()`:

```
queue after      : []
reached stream_fn: []
in state.messages: []
VERDICT lost: True
```

ARM 2 is the same with the fault on `before_agent_start` instead, and loses it
identically. **The window is wider than #311 said**: the issue named only the
`queue_update` emit, and a remedy scoped to that call would have left the
second await open.

ARM 4 states the cost in the only terms that matter: after the raise, the next
`prompt()` reaches the provider with `['second prompt']`. The user typed two
things and the model was asked one of them, with no error naming the other.

The live driver says the same on the real path — a real OpenRouter model, a
real `JsonlSessionStorage` file (2026-09-22, `anthropic/claude-haiku-4.5`).
Base first, then the branch:

```
queue after the raise: []                  | ['Also reply with exactly: BRAVO']
model replied        : 'ECHO'              | 'seed\n\nBRAVO\nECHO'
user lines on disk   : ['seed',            | ['seed',
                        'Reply … ECHO']    |  'Also reply with exactly: BRAVO',
                                           |  'Reply … ECHO']
```

On the base the sentence is on no queue, in no request and in no file. Nothing
on disk records that it was ever asked.

## Decision

**A queue detached before an await goes back on the raise, in front of
whatever arrived meanwhile — and only while the turn provably has not started.**

```python
drained_next = self._next_turn_queue
self._next_turn_queue = []
try:
    ...                                   # both hook emits, building `prompts`
except BaseException:
    self._next_turn_queue = drained_next + self._next_turn_queue
    raise
result = await self._run(prompts, system_prompt=system_prompt)
```

Three things in that are choices, and each was measured.

### The guard covers the region, not the two calls

Scoping it to `_emit_queue_update` would fix ARM 1 and leave ARM 2 losing the
same message through `before_agent_start`. Naming both calls instead would
work today and quietly stop working the first time someone adds a third await
to that gap. The invariant is positional — *a local is the only thing holding
these messages, so every await over which it is the only holder is the same
defect* — so the guard is written positionally. Where that region has to stop
is a separate question, and the answer is not the flattering one; see "Where
the guard ends" below.

### Prepended, not appended

`next_turn()` is legal from inside the very handlers these awaits run, so at
the moment of the raise the queue can already hold a message strictly newer
than everything in `drained_next`. ARM 3 measures exactly that: a
`before_agent_start` handler that enqueues and then raises leaves
`['ARRIVED-DURING-AWAIT']` on the queue with the drained message gone.
Appending the restored messages behind it would hand the model the user's two
sentences in the wrong order. Same call, same reason, as #301's abort tail
(ADR-0245 §1).

### `BaseException`, because a cancel is this loss in other clothing

A `CancelledError` arriving on one of those awaits takes the messages exactly
as a hook exception does, and `abort()` clears the steer and follow_up queues
while deliberately leaving `_next_turn_queue` alone (`core.py:1544-1545`), so
eating them would contradict that design. Catching `Exception` leaves that arm
open — measured: sabotaging the clause to `except Exception` reddens
`test_a_cancelled_drain_hands_the_messages_back` and nothing else.

Who actually delivers that cancel is *not* `abort()`, which is what this
section first said. `abort()` cancels `_current_turn_task`
(`core.py:1547-1549`) and nothing else, and that attribute is assigned inside
`_run` — after this window. ARM 7b calls `abort()` with `prompt()` parked on
the `before_agent_start` await and measures the consequence: none.

```
_current_turn_task: None
prompt() after abort(): still awaiting the hook
VERDICT abort() cannot reach this window: True
```

The cancel the clause is for comes from whoever cancels the `prompt()` task
itself — an embedder, or loop shutdown. Nothing in the tree does: the RPC
prompt task is pinned and *awaited*, never cancelled (`rpc_mode.py:404-411`,
`core.py:3245-3248`), and the TUI awaits `harness.prompt()` inline
(`tui/shell.py:4002`). So this arm protects an SDK embedder, which is the same
audience `next_turn()` itself has, and the ADR should not have claimed a
product path it does not have.

## The alternative, rejected by measurement

ADR-0245 proposed "emit after :1303" as this queue's remedy. It does not work,
and saying so is the point of ARM 5, which patches the base file and re-runs
ARM 1 against it:

```
main as-is        : VERDICT lost: True
main + emit moved : VERDICT lost: True
```

`prompts` is a local as well. Moving the emit below `prompts.extend(...)` moves
the raise from one line where the messages are unrecoverable to another line
where they are equally unrecoverable; nothing is held anywhere the harness can
find it until `agent_loop` has the list. The emit's *position* was never the
defect — its ability to raise at all was, and that is a property of every await
in the gap.

Moving the emit past `_run` would close ARM 1's half of the window (ARM 2's
await stays where it is), and is rejected for a different reason: it would
change what the event means. `queue_update` fires at the drain so observers
repaint an emptied queue at the moment it empties (pi
`executeTurn` L487); past `_run` it would arrive after the whole turn, which is
what `settled`'s `next_turn_count` already reports. Two events for one fact,
one of them a turn late.

## Where the guard ends, and the window that stays open

The guard ends at the **call** to `_run`. That is the last point where this
frame can *prove* the turn never started — it is not a line past which the
messages are safe, and the first draft of this section, the code comment and
the commit message all claimed the second thing ("unrecoverable until `_run`
receives them"). That was false, and this ADR contradicted itself on it: the
rejected-alternative section above already says the true version — *nothing is
held anywhere the harness can find it until `agent_loop` has the list*.

Between the call and `agent_loop` there is exactly one await on the
straight-line path, and the shipped product takes it. `_run` begins at
`core.py:4450` and `agent_loop(prompts, ...)` is at `core.py:4682-4683`; the
only await in between — found by walking `_run`'s AST and skipping nested
`def`s, not by reading down the page — is `await self._session.build_context()`
at `core.py:4466`, reached whenever a session is attached — which the CLI
always does (`cli/entry.py:1571-1573`); even `--no-session` swaps the storage
for `MemorySessionStorage` rather than dropping the `Session`
(`cli/entry.py:430-431`). `Session.build_context`
(`session/session.py:149-154`) goes through `get_branch` → `get_leaf_id()` /
`get_path_to_root()`, and the shipped `JsonlSessionStorage` raises
`SessionError` from both when its entry index does not resolve
(`jsonl_storage.py:660-666`, `:703-721`); `SessionStorage` is a public
Protocol, so an embedder's storage can raise anything at all.

ARM 6 puts such a session in front of the same drained message:

```
prompt() raised  : RuntimeError(session storage unavailable)
queue after      : []
reached stream_fn: []
in state.messages: []
VERDICT drained message lost: True
VERDICT live user message lost too: True
```

The first three lines are ARM 1's, verbatim. **Same defect, different trigger,
and this change does not close it.** The last line is why it is not closed by
widening this guard: that window takes the turn's *whole input* with it, the
live user message included, so the remedy is a decision about what a turn that
dies before it starts owes the user — not a decision about this queue.
Widening the guard over `await self._run(...)` is certainly not it: sabotage
ARM D does exactly that and reddens
`test_a_turn_that_fails_after_the_drain_does_not_requeue` (`1 failed, 7
passed`), because past `agent_loop` the messages have been handed over and
re-queueing them sends the user's sentence twice. Filed as a follow-up.

What *is* true, and what the far-edge test actually asserts, is the statement
about `agent_loop` rather than about `_run`: a turn whose provider raises was
still handed `['Q', 'x']` at `stream_fn`. Such a turn also loses its user
messages from live state — `state.messages` ends up holding only `['[error]
provider blew up']`, measured on the base and on the branch alike — which is
again a different defect, again filed rather than silently inherited.

## What this does not change

- **The happy path.** The restore fires only on the raise. Sabotaging it to a
  `finally` reddens `test_a_successful_prompt_still_empties_the_queue` and
  `test_a_turn_that_fails_after_the_drain_does_not_requeue`, and nothing else.
- **Who fills this queue.** Two push sites, and #301's commit is on record
  getting its own equivalent list wrong, so this one was read rather than
  assumed: `AgentHarness.next_turn()` (`core.py:3042`) and
  `_action_send_message()` (`core.py:4065`), the latter reached by
  `ExtensionAPI.send_message` / `send_user_message` and by the
  `ReplacedSessionContext` handle. `next_turn()` has **no in-tree production
  caller** — it is an embedder/SDK surface — so in the shipped product this
  queue is filled by extensions.
- **`_emit_queue_update` itself**, which still raises. Whether a lifecycle
  observer should be able to fail a turn at all is ADR-0030's question, not
  this one's.
- **What an observer last saw.** The emit fires at the drain, so the snapshot
  an extension is holding is the *empty* queue; the restore puts the messages
  back without emitting again. ARM 8: snapshots `[['QUEUED-BY-NEXT-TURN'],
  []]`, queue in fact `['QUEUED-BY-NEXT-TURN']`. A queue indicator painted off
  this event therefore shows nothing queued until the next enqueue or the next
  turn's drain corrects it. Re-emitting inside the `except` was not done here
  because that emit can raise too, and a restore that can fail on its way out
  is worse than a stale indicator — but the disagreement is real and it is this
  change that introduces it (on the base the snapshot and the queue agree,
  both empty), so an extension author has to know that `queue_update` is not a
  complete account of what the queue holds.
- **That a restored message can actually be sent.** After a *cancel* it
  cannot, today. `prompt()`'s phase reset is `except Exception`
  (`core.py:1457`), which a `CancelledError` walks past, so `_phase` stays
  `"turn"` and the busy guard (`core.py:1225-1228`) refuses every later
  `prompt()`. ARM 7: queue `['QUEUED-BY-NEXT-TURN']`, phase `'turn'`, next
  `prompt()` → `AgentHarnessError(AgentHarness is busy (phase='turn'))`. The
  base wedges identically (queue `[]`, phase `'turn'`), so this change neither
  causes it nor fixes it, and `test_a_cancelled_drain_hands_the_messages_back`
  stops one assertion short of it on purpose: the queue is this ADR's subject,
  the phase machine is ADR-0023's. Filed as a follow-up.

## The audit, redone

`core.py` was audited for the same detach-then-await shape by an AST walk over
this tree: every `local = self._attr` whose next statement resets or clears
that same attribute, then a check for an `await` in the statements that follow
before the local is consumed. It returns **eight** sites. What follows is those
eight minus `:1289-1290`, which this ADR fixes, plus two shapes that walk
cannot see and that were read by hand — `dispose`'s loop and the `pop()` sites,
where nothing is bound at all. So the claim is bounded: *this shape, in this
file*, not an exhaustive account of every way the harness can lose something.
Each is clean, and now clean for a stated reason:

- **`_MessageQueue.drain` (`core.py:470-478`)** is synchronous, but its result
  *is* held across awaits by the consumer — `loop.py:187` drains steering
  messages and `loop.py:194` then awaits `emit(TurnStartEvent())` before using
  them. That emit cannot raise from a handler: the closure swallows every
  listener and hook-bus exception (`core.py:4604-4610`, `:4624-4633`). The one
  `BaseException` that can arrive there is an abort's `CancelledError`, and
  `abort()` clears those two queues by design.
- **`flush_pending_session_writes` (`core.py:3180-3191`)** is #301's, already
  per-item and already requeueing its tail on cancel.
- **`dispose`'s `_pending_tasks` loop (`core.py:3245-3248`)** iterates a copy
  and clears only after every await.
- **`abort()` (`core.py:1541-1545`)** snapshots the two queues and clears
  them, but the snapshot's only consumer is the event constructed on the next
  line, and the messages are meant to be dropped.
- **The three `state.messages.pop()` sites (`core.py:1996`, `:2025`, `:2132`)**
  discard a trailing error assistant. The popped value is not bound and the
  session copy survives, by the comments' own account.
- **The three `_retry_attempt` resets (`core.py:1414-1429`, `:2105-2106`,
  `:2150-2151`)** are the only sites the walk returns that the first draft of
  this section did not mention, and they *do* hold a detached value across an
  `await self._emit_to_subscribers(...)`. They are not this defect's family and
  are left alone: what is detached is a display-only attempt counter, and the
  attribute is already at the value the code intends it to end on — the reset
  to `0` is deliberately placed *before* the emit so a subscriber that prompts
  again synchronously cannot read a stale counter (`core.py:1427-1428`). A
  raise there loses a number that fills an event field; nothing a user wrote is
  on the floor. Recorded here so that the next reader of this section finds
  them described rather than absent.

## What was not measured

No TUI look: this change is in-process queue bookkeeping and `tui/` is
untouched. The live driver covers the runtime path CLAUDE.md §10 asks for.
Concurrency beyond the single-task interleavings the probe creates — two event
loops, or a `next_turn()` racing the restore from a thread — was not measured;
the harness is single-loop by construction and nothing here widens that.

The live run *was* repeated on the final tree, and against a real `fad2e28`
checkout rather than an overlay of `main`'s `core.py` — the numbers in the
commit message are that re-run, not the first version's. What is still not
measured: Codex cross-review (CLAUDE.md §8) was started on this lane and cut
off by a ChatGPT usage limit while it was still reading the code, so it
produced no findings either way — this change has had no second model's eyes
on it. Nor has the Windows leg. ARM 6's session failure was
measured with a stub session rather than a corrupted JSONL file; the stub is
the general case (`SessionStorage` is a Protocol), and no attempt was made to
provoke a real `SessionError` on disk.
