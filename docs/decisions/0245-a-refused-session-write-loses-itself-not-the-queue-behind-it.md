# 0245. A refused session write loses itself, not the queue behind it

Status: Accepted (2026-09-20)
Date: 2026-09-20
Supersedes/relates: ADR-0242 (the JSONL write discipline — this is its harness
layer; "a failed append must not break the next append" is the rule this ADR
applies one level up, and nothing in 0242 changes), ADR-0022 (the pending-write
queue and its no-session fallback, unchanged), ADR-0235 (pi is a reference, not
a target — the divergence in §3 is recorded, not argued), ADR-0246 (#311 — the
gap this ADR's audit found in `prompt()`'s next-turn drain, closed at the two
hook awaits it named; it also corrects the remedy that section proposed, and
records the one await further along that stays open).
Issue: #301. Same class as #294, one layer above it.
Probe: `.omc/specs/301-measure.py` (the same file runs on the branch base and
on the branch). Live driver: `.omc/specs/301-live.py`. The audit in
"What this does not change" is `.omc/specs/301-audit-next-turn.py`; the abort
measurement behind §1 is `.omc/specs/301-abort-tail.py`.

`AgentHarness.flush_pending_session_writes` detached the whole queue and then
wrote it. One refusal in the middle ended the loop, and every already-detached
item behind it was gone — with no log, no event and no record anywhere.

## What was broken, measured on `91eeb12`

Eight writes queued, one of each variant; the storage refuses the
`model_change` one with `OSError(28)` — a full disk, which after ADR-0242 is a
survivable failure with the file left healed for the next append.

```
ARM 1 — flush_pending_session_writes() called directly
flush raised     : OSError(28, 'No space left on device')
landed in session: ['message', 'message']
still queued     : []
LOSS             : 7 of 8 — 1 the session actually refused, 6 COLLATERAL

ARM 2 — the production call site: a real prompt() turn_end flush
prompt() raised  : OSError(28, 'No space left on device')
save_point emits : 1 -> [(False, '<no field>')]
```

Two separate defects in one shape.

**The collateral loss.** `pending = self._pending_session_writes;
self._pending_session_writes = []` runs before the first `await`, so by the time
the second item raises, the other six exist nowhere: not on disk, not on the
queue, not in an exception. A label, a leaf move, a session rename and a
thinking-level record vanished together because a `model_change` in front of
them was refused. Nothing said so.

**The turn died with them.** The `turn_end` call site
(`harness/core.py`, the `turn_end` branch of the lifecycle projection) did not
wrap the flush, so the storage error came out of `prompt()` itself. A full disk
did not cost a record — it cost the user's turn.

**And the one signal lied.** `had_pending_mutations` is read *before* the
flush, so the close-out path re-emits `turn_end` against a queue that was
already emptied and `save_point` reports `had_pending_mutations=False`. The one
event whose whole job is to say "the pending mutations are committed now" said
there had been none.

## Decision

> **The drain attempts every item. A write the session refuses is dropped,
> reported and counted; the writes behind it still land.**

### 1. Per item, not per drain

The dispatcher moved to `_apply_pending_session_write`, and the drain wraps one
item at a time. `flush_pending_session_writes` no longer raises for a refused
write — it returns how many were lost.

Two things are deliberately *not* treated as refused writes.

**`asyncio.CancelledError` from an abort** is not a failed write and must stay
cancelled; `test_an_abort_still_cancels_the_flush_and_keeps_the_tail` pins that,
so a later widening to `except BaseException` fails rather than turning an abort
into "carry on". But re-raising alone reproduced #301 in an abort's clothing:
the queue is already detached, so the items after the cancelled one existed
nowhere. The flush now puts that un-attempted tail **back on the queue** before
re-raising. Measured, with a storage that blocks forever on one append and
`abort()` called while the `turn_end` flush sits in it:

```
before  queue after abort : []        on disk: ['session', 'message', 'message', 'message']
after   queue after abort : []        on disk: ['session', 'message', 'message', 'message', 'label', 'session_info', 'leaf']
```

Those are a real `JsonlSessionStorage` file's bytes, re-read and parsed — the
first version of this probe subclassed `MemorySessionStorage` and printed its
`get_entries()` under the label `on disk`, so this evidence was a claim about a
Python list until a cross-review pass caught it. The probe now puts the
blocking fault at the `FileSystem` seam under the real store, and `before` was
re-measured by overlaying the parent commit's `core.py` on this tree via
`PYTHONPATH` so only the drain differs.

The three records that the abort used to drop are on disk, inside the same
`prompt()` call. **Which call writes them was asserted here without being
measured, and it was wrong.** Measured since — by wrapping
`flush_pending_session_writes` and reading `sys._getframe(1).f_lineno` — the
tail is written by the **turn_end projection's flush** (`core.py:4613`), run a
second time because the abort close-out emits a synthetic `TurnEndEvent`
(`core.py:4700-4705`) after `prompt()` catches the turn task's `CancelledError`
(`core.py:4661-4662`):

```
ENTER  flush from core.py:4613  queue=['PendingCustomWrite', 'PendingLabelWrite', 'PendingSessionInfoWrite', 'PendingLeafWrite']
  RAISED CancelledError from the call at :4613; queue now=['PendingLabelWrite', 'PendingSessionInfoWrite', 'PendingLeafWrite']
ENTER  flush from core.py:4613  queue=['PendingLabelWrite', 'PendingSessionInfoWrite', 'PendingLeafWrite']
  RETURN failed=0; on disk now=['session', 'message', 'message', 'message', 'label', 'session_info', 'leaf']
ENTER  flush from core.py:4786  queue=[]
  RETURN failed=0; on disk now=['session', 'message', 'message', 'message', 'label', 'session_info', 'leaf']
```

The `finally` safety net (`core.py:4786`) does run *uncancelled*, but it
arrives to an empty queue and writes nothing. It is the **backstop**, and it is
a real one on two measured counts: delete `TurnEndEvent` from the close-out's
tuple and the net delivers the tail itself, and a cancellation that is *not* an
abort (the `raise` at `core.py:4713`) reaches it with the tail still queued.
Failing both, the next turn's `turn_end` flush drains whatever is on the queue
— measured, a later `prompt()` writes a queue seeded while idle, and the
harness accepts a new `prompt()` after an `abort()`. `dispose()` does **not**
flush, so the close-out's `turn_end`, then that `finally`, then the next turn
is the whole guarantee.

This distinction is not pedantry: anyone editing the abort close-out's
`TurnEndEvent` — a block whose own comment records that it has been rewritten
before — would otherwise have no way to know they are touching the thing that
delivers the tail. No on-disk assertion separates the two sites, so
`test_an_aborted_turn_writes_its_tail_before_prompt_returns` now also asserts
that the tail is already on disk when `save_point` fires; only the turn_end
projection emits one.

The cancelled item itself is not put back: its `append_*` was already in flight
and may have landed, and a duplicate record is worse than a missing one here.

**`AssertionError`** is re-raised too. The only one that can reach the drain is
`assert_never` in `_apply_pending_session_write` meeting a
`PendingSessionWrite` variant with no dispatcher arm. `AssertionError` is an
`Exception`, so a bare `except Exception` would have logged a bug in this file
as "session write lost: … could not be persisted" and returned it in
`failed_writes` — a programming error disguised as a full disk. It propagates
instead, which is also what makes the `finally` safety net's comment true.
Nothing in `session/` uses a bare `assert`, so no storage refusal can arrive
wearing this type.

### 2. The failed item is dropped, not requeued

The issue offered requeueing as the alternative. It is measurably wrong here.
ADR-0242 makes the store's refusals a property of the *entry*: an entry
`entry_to_json` or `json.dumps` cannot convert raises `invalid_entry` before a
byte is written, identically every time. ARM 3 of the probe puts the same
`append_custom_entry` — the public path an extension reaches through
`ExtensionAPI.appendEntry` — through three attempts:

```
same write, 3 attempts : ['SessionError/invalid_entry', ...x3]
next (different) write : appended 'c175d0cb', entries=['custom']
```

A requeued head like that never clears, and everything behind it stops
forever — every later label, leaf move and model-change record in that session.
That is strictly worse than #301: a one-time loss becomes a permanent outage.
The second line is the other half: the store promises the **next**, different
append still lands. Keep-going is the harness-layer expression of that promise.
Requeueing is not — it retries the same append rather than making the next one.

Requeueing also cannot preserve order without care, because `append_message`
keeps appending to the queue's tail during a turn while retained items would
have to go back at the head. That is not an argument against the abort path in
§1 — putting an *un-attempted* tail back retries nothing and cannot stall, and
it prepends (`self._pending_session_writes[0:0] = …`) for exactly this reason.
It is an argument against retrying the item the store just refused.

### 3. This diverges from pi, deliberately

Pi at `734e08e` (`agent-harness.ts:459-481`) loops
`while (this.pendingSessionWrites.length > 0)`, peeks `[0]` and `shift()`s only
after the await returns. A raising write therefore stays at the head with the
whole queue behind it, and the flush propagates — pi's shape is the requeue
that §2 rejects, and its stall is why. Aelix's detach-then-loop was already a
divergence; it was just an unrecorded and worse one.

Pi's own tree has since dropped `pendingSessionWrites` entirely, so there is
nothing newer upstream to follow. Per ADR-0235 this is recorded, not argued.

### 4. The failure is visible

Silence is how #301 happened, so two reports, both cheap:

- **A `WARNING` per lost write**, naming the variant, the exception and the
  running count. `WARNING` is the default threshold — the `DEBUG` this code
  used reaches nobody who has not turned logging on. `exc_info` rides on the
  **first** failure of a flush only. The tree installs no logging handler, so
  these records reach `logging.lastResort` → stderr; a disk that fills mid-turn
  refuses every queued write, and one traceback per item would paint over a
  live TUI. The variant, the exception repr and the running count are on every
  record regardless, so the second traceback carried no information the first
  one did not.
- **`SavePointHookEvent.failed_writes`**, Aelix-additive with a default of `0`
  and no pi counterpart. It is how many of the pending writes **raised out of
  their `session.append_*` call** during the flush this save point closes. A
  handler written against the pi shape is unaffected.

The `finally` safety-net flush has no `save_point`, so there the per-item
`WARNING` is the whole report; its own `except Exception` was raised from
`DEBUG` to `WARNING` because after this change the only `Exception` that can
reach it is the `assert_never` of §1 — the dispatcher itself is broken, not a
disk full.

#### `failed_writes` counts refusals, and that is all it counts

This field first said `0` means the pending mutations are committed and a
positive count means the session on disk is missing that many records. A
cross-review pass (Codex, 2026-09-21) produced a counterexample in each
direction and both reproduce — `.omc/specs/301-failed-writes-accounting.py`,
against a real `JsonlSessionStorage` file:

```
ARM 1 — a refusal, then a cancellation
save_points       : [(True, 0)]
lines on disk     : ['session', 'message', 'message', 'message', 'session_info', 'leaf']

ARM 2 — a record whose newline did not fit
save_points       : [(True, 1)]
lines on disk     : ['session', 'message', 'message', 'message', 'custom', 'session_info', 'leaf']
reloaded entries  : ['message', 'message', 'message', 'custom', 'session_info', 'leaf']
```

ARM 1 **undercounts**: the `custom` write was refused with `ENOSPC`, and then
the flush was cancelled inside the next append. The local `failed` dies with
the raise — the method re-raises rather than returning — and the close-out's
second flush counts only the requeued tail it attempts itself, so the one
`save_point` on that turn reads `failed_writes=0` with the `custom` record on
no disk. ARM 2 **overcounts**: the disk filled between that record's last byte
and its newline, so `_append_line` re-armed the healing newline and re-raised;
the next append opened a fresh line, and the record is on disk and in the
reloaded entries — while `failed_writes` calls it lost.

**Decision: narrow the wording, do not widen the payload.** Splitting the count
into "failed" and "uncertain" was considered and rejected on the mechanism, not
only on the cost of changing a payload extensions read. ARM 2's record is
uncertain *to the storage itself*: ADR-0242 re-arms that healing newline
precisely because a failed append cannot tell a fragment from a whole record.
Nothing above the storage knows more than the storage does, so **every** entry
in `failed_writes` is an "uncertain", and the split would produce two fields
with one meaning. ARM 1 is not an accounting gap a carried-over number fixes
either: the cancelled flush and the flush that emits the save point attempt
different items, and the cancelled item is deliberately in neither bucket (its
`append_*` was in flight and may have landed), so carrying a count across would
give the field a third meaning — "failures since some earlier point".

What ARM 1 *did* leave was silence, and that is fixed rather than documented:
the cancellation path now logs a `WARNING` naming how many writes had already
failed in the flush being cancelled, so the per-item records are never the only
trace of a count no `save_point` will carry.

## Consequences

- `AgentHarness.flush_pending_session_writes` returns `int` instead of `None`.
  Additive for every existing caller; both in-tree call sites ignore or use it.
- A session-storage failure no longer aborts the turn it happened in. The
  records are still lost — that is the storage layer's business — but the
  conversation continues, which is what the user has on screen either way.
- `SavePointHookEvent` gains one defaulted field.
- The drain is no longer atomic in the sense of "all or the exception", and it
  never was: it was "some, and the exception, and no way to know which some".

## What this does not change

The same *silence* — not the same shape — exists next door and is left alone on
purpose, one issue per commit:

- `session.append_message` on `message_end` (`harness/core.py`, the
  `message_end` branch) swallows a failed append at `DEBUG` and keeps going.
  #294's commit body already records it. That write is the primary path, and
  the message survives in `state.messages` and on screen, so it is a different
  failure with a different fix.
- The fire-and-forget `_pin_task` actions (`_action_set_label`,
  `_action_append_entry`, `_action_set_session_name`) log a raised task at
  `DEBUG` in the done-callback. Same family, different shape.

Neither is a detach-then-loop.

### The audit #301 asked for: one other place has the shape, and this does not fix it

> **Addressed since, by #311 / ADR-0246** (2026-09-22). The section below is
> kept as written because it is the audit #301 was asked for and the record of
> what it found. Two sentences in it did not survive contact: the shape reaches
> `_emit_before_agent_start` as well as the emit, so the window is wider than
> described here; and **"emit after :1303" is not a remedy** — it was proposed
> without being run, and running it loses the message identically
> (`.omc/specs/311-next-turn-drain.py` ARM 5, which patches this very file's
> `core.py` at `main` and re-measures). The remedy that works is the other one
> named below in parentheses: restore the queue on the raise, prepended. It
> covers the two hook awaits this section names and stops at the call to
> `_run`; the one await inside `_run` before `agent_loop` receives the prompts
> loses the same messages the same way, which ADR-0246 §"Where the guard ends"
> measures and files rather than closes.


`prompt()` detaches the next-turn queue and then awaits *before* it uses what
it detached:

```
core.py:1289-1290   drained_next = self._next_turn_queue
                    self._next_turn_queue = []
core.py:1295-1296   if drained_next:
                        await self._emit_queue_update()
core.py:1303        prompts.extend(drained_next)
```

`_emit_queue_update` (`core.py:2294-2309`) turns any `queue_update` handler
exception into a raised `AgentHarnessError`, and a handler's `error_mode`
defaults to `"throw"` (`harness/hooks.py:2082`). A throwing observer between
:1296 and :1303 therefore costs the drained messages exactly the way #301 cost
the pending writes. Measured on this branch — one message queued through
`next_turn()`, a `queue_update` handler registered afterwards that raises, then
`prompt()`:

```
queued           : ['QUEUED-BY-NEXT-TURN']
prompt() raised  : AgentHarnessError('queue_update hook handler raised: observer blew up')
queue after      : []
reached stream_fn: 0 turns
in state.messages: []
```

The message is on no queue, in no turn, and in no exception — the #301 shape,
verbatim. **It is a different queue and #301 does not fix it**: this one holds
user text for the next turn rather than session records, its remedy is to emit
after :1303 (or to restore the queue on the raise) rather than a per-item
drain, and the repo's rule is one issue per commit. Filed separately, as #311;
ADR-0246 has what it turned out to be.

The other two candidates were checked and are clean: `_MessageQueue.drain`
(`core.py:470-478`) is synchronous end to end, and `dispose`'s `_pending_tasks`
loop (`core.py:3194-3196`) already suppresses per item. Nothing in `session/`
has the shape.

## Tests

`tests/test_harness_pending_writes_fault_injection.py` — fourteen tests, the
fault injected at the storage seam where a real one arrives. **Twelve** fail on
`91eeb12` (the branch base), measured on a scratch copy of this tree with
`git show 91eeb12:…` written over *both* `harness/core.py` and
`harness/hooks.py`:

```
$ ./.venv/bin/python -m pytest \
      tests/test_harness_pending_writes_fault_injection.py -q -p no:cacheprovider
12 failed, 2 passed in 0.23s
```

Which files you revert changes the number, so it has to be said which: reverting
`core.py` alone leaves `11 failed, 3 passed`, because `hooks.py` carries
`SavePointHookEvent.failed_writes` and with it at HEAD the defaulted `0` lets
`test_save_point_on_a_healthy_turn_reports_zero_failures` pass against the base
drain. Neither reading is eight — that was the reviewer's count for the
**nine**-test version at `0425e48`, carried forward without re-measuring after
five tests were added.

The two that survive the full revert are
`test_set_model_and_set_thinking_level_queue_through_the_public_api`, which
guards the push sites rather than the drain, and
`test_a_broken_dispatcher_is_not_reported_as_a_refused_write`, which passes
because the base drain has no per-item `try` at all, so an `AssertionError`
from `assert_never` already propagated.

Reverting only the per-item loop to `except Exception` + `exc_info=True` fails
five — but four of those five are already among the twelve above, so exactly
one, `test_a_broken_dispatcher_is_not_reported_as_a_refused_write`, is
genuinely additional:

```
FAILED ...::test_an_abort_still_cancels_the_flush_and_keeps_the_tail
FAILED ...::test_a_requeued_tail_goes_in_front_of_writes_queued_since
FAILED ...::test_an_aborted_turn_writes_its_tail_before_prompt_returns
FAILED ...::test_a_broken_dispatcher_is_not_reported_as_a_refused_write
FAILED ...::test_one_traceback_per_flush_not_one_per_failure
5 failed, 9 passed
```

`test_set_model_and_set_thinking_level_queue_through_the_public_api` is
insensitive to the drain by design: it guards the other side. Every other test
here fills the queue by poking `h._pending_session_writes`, so a setter that
stopped queueing altogether would have left the file green. Measured by
sabotaging the `set_model` push site to `if False:` — it is the only test in
the file that goes red (`1 failed, 13 passed`).

## Live check

`.omc/specs/301-live.py` — a real OpenRouter turn
(`anthropic/claude-haiku-4.5`), a real `JsonlSessionStorage` file on disk, five
pending writes queued mid-turn with the `model_change` one refused by the store:

```
prompt() raised   : None
model replied     : 'Reply with exactly: okok'
storage refusals  : 1
save_point        : had_pending=True failed_writes=1
lines on disk     : ['session', 'message', 'message', 'message', 'custom', 'label', 'session_info', 'leaf']
reloaded entries  : ['message', 'message', 'message', 'custom', 'label', 'session_info', 'leaf']
```

The four writes behind the refused one are on disk and survive the reload, the
turn returned the model's reply instead of the `OSError`, and one `WARNING`
with a traceback named what was lost. This was run in a non-TTY session, so the
CLI path was exercised with `aelix --provider openrouter -p` (a clean session
file, no pending writes) and the queue path through this driver; **the TUI was
not opened** and nothing here renders, so no TUI look was owed (CLAUDE.md 9).
