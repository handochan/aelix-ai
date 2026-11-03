# 0286. Esc withdraws the input the model never answered

Status: Accepted (2026-11-03, #654)
Date: 2026-11-03
Issue: #654. Design and the owner's four decisions (2026-11-03):
`.omc/specs/654-design-2026-11-03.md`. Batch B6, P1.
Narrows: ADR-0258 (its D1 and "delivered inputs remain historical context" hold for
a plain `abort()` and an outer cancel; a withdrawing abort is the exception), and
ADR-0239 decision 8 (`/clone` and RPC `clone` ask the path for a conversation, §7).
Relates: ADR-0023 (prompt lifetime, the abort flag), ADR-0128 (the retry wait),
ADR-0145 (`message_end` replacement), ADR-0242 (one write, every prefix a session),
ADR-0264 §4.7 (the first aelix-only hook event), ADR-0276 (a stopped child),
ADR-0277 (its resume), #320, #336, #545, #571, #123.
pi: `earendil-works/pi@9c9ce8547`, deliberately not followed on the context (below).
Tests: `tests/harness/test_withdraw_unanswered_input_654.py`,
`tests/tui/test_esc_gives_input_back_654.py`, `tests/rpc/test_rpc_mode_handlers.py`,
`tests/cli/test_session_start_model_413.py`.

## Context

Measured on `origin/main` `74f9214c`, with a real JSONL session and a scripted
provider (`.omc/probes/654/probe.py`). What the request after an Esc carried:

```text
before output : next request = [CANCELLED, NEW]                disk = [CANCELLED]
mid text      : next request = [CANCELLED, NEW]                (partial text never persisted)
after a tool  : next request = [CANCELLED, call, result, NEW]
```

So a message the user had just cancelled stayed in front of the model for the rest of
the session, reopening it included, and the input box was empty. ADR-0258 decided the
commit on purpose, to stop a *failed* turn losing input, and it does not tell an Esc
from any other way out. The TUI's Esc called only `abort()`, which also cleared the
steer and follow-up queues: lines queued during the turn were lost outright.

Pi keeps the user message on abort too; it skips only the aborted assistant. This is a
deliberate divergence (ADR-0235). Pi's `restoreQueuedMessagesToEditor({abort: true})`
(`interactive-mode.ts`) is followed for the queue.

## Decision

The owner's decisions, final: what already ran stays; input the model never answered
leaves context and returns to the input box; this applies to Esc and Ctrl+C in the TUI
and to RPC `abort`, and an outer task cancel keeps today's preservation.

### 1. The rule

**On a withdrawing abort, every input this prompt committed since its last answer leaves
context.** An answer is a committed `AssistantMessage` whose `stop_reason` is not `error` or
`aborted`, or a committed tool result. It and everything before it stay.

An earlier prompt that ended in an error the user saw, or that a plain abort kept, stays.
That is not a gap: a cross-review read the rule as reaching back over it (Codex finding 4)
and is rejected, because this Esc cancels this prompt, and a turn the user already watched
end is not what they are taking back.

| Esc moment | Result |
|---|---|
| before any output, mid-stream, during an in-flight retry attempt | whole prompt withdrawn |
| TUI Esc in the retry countdown | whole prompt withdrawn, the failed answer off the path with it |
| tool running, or tool done and next response pending | nothing withdrawn |
| `[.., tool result, STEER]` pending | STEER withdrawn, the rest stays |
| `[.., answer, FOLLOW_UP]` pending | FOLLOW_UP withdrawn, the rest stays |
| after a compaction inside this prompt committed (overflow recovery) | nothing withdrawn (limit 1) |
| during that compaction's summary, before it commits | whole prompt withdrawn, no compaction |

### 2. The API defaults to keeping

`abort(*, withdraw_unanswered=False)` and `abort_retry(*, withdraw_unanswered=False)`.
`dispose()` calls `abort()`: SIGTERM, quit and a stopped delegation child (killed by
`abort_child`, reaching `dispose` through its signal handler) must keep the input,
because nobody is left to give it back to and a resume needs it. The test ADR-0258
pinned this with passes unchanged.

Passing True: the TUI's Esc and Ctrl+C (`_on_interrupt`, in-flight retry request
included), its countdown Esc (`_on_retry_interrupt`) and RPC `abort`. **Not** the TUI's
SIGTERM/SIGHUP handler, although it aborts through the same `_safe_abort`: the editor
goes with the process. RPC `abort_retry` means "stop retrying" and stays False (#571 owns
that path), as does an extension's `ctx.abort()` (#545).

The last `abort()` wins: it assigns the flag, so a plain abort after a stale request (an
`abort_retry` made with no wait running) keeps the input. `abort_retry()` left False changes
nothing, since `abort()` calls it too and "stop retrying" does not undo an earlier Esc.

### 3. A leaf move, not a new entry type

Pi's `context_edit` entry was rejected: a new entry type needs every path consumer
taught about it and breaks downgrade (#383). `Session.move_to` already writes one
`leaf` line and survives reopen, so build_context, stats, replay, `get_messages` and
compaction exclude the input for free.

`Session.withdraw_tail(target_leaf, is_withdrawn)` returns the leaf to `target_leaf` and
appends every other entry the path held after it again, in order, on fresh ids. Those are
a turn's neighbours: a custom entry or a model change flushed between two attempts.
`target_leaf=None` is legal, the next append starts a new root. Live state applies the
same test, so a message written between attempts with no session stays too.

### 4. One tracker per prompt

`_UnansweredInputs` is created just before the prompt's first `_run` and dropped at its
release. In `emit`'s `message_end` branch, before the append's await (a cancel can land
inside it), an answer empties `committed`, a failed answer changes nothing, and anything
else joins `committed` as an `(original, final)` pair. After an answer's append,
`safe_leaf` follows it, so what stays is never written twice. The flag
`_withdraw_requested` is cleared at `_run` entry beside `_abort_requested` and shares its
fate: a request made while idle cannot touch the next prompt.

A live input still uncommitted (a cancel in `agent_start`, `message_start` or the reducer)
is taken out of the uncommitted list, or `_run`'s `finally` would requeue it and it would
be sent later anyway. Drained `next_turn` messages go back to the front of their queue in
commit order, committed or not, since they were not typed in this prompt and a `!cmd`
result must reach the model once. A `before_agent_start` message is dropped: the next
prompt generates it again (ADR-0258 D4).

The call sites are `_run`'s abort close-out, before the terminator, so `agent_end` never
carries withdrawn input; and `prompt()`'s tail, for an abort made between runs (the
countdown, one that stopped overflow recovery). The tail's check sits before the threshold
compaction, which must not summarise it, and again at the release, for a raise or an abort
during that check. The empty in-memory aborted marker stays: `print_mode` reads it.

An Esc during the summary of a compaction inside the prompt comes before the barrier.
`compact()` checks once the summary is back, before `append_compaction`: under the prompt's
claim, with a withdraw requested and input still in the tracker, it raises
`_CompactionCancelled` and commits nothing. `compaction_end` reads aborted, overflow recovery
ends as it does for a cancelled compaction, and the tail withdraws. A threshold compaction
after an answer has nothing in the tracker and goes on.

### 5. An event, because no context change goes unobserved

`InputWithdrawnEvent(messages, prompt_withdrawn)`, `type: "input_withdrawn"`, in the
`AgentEvent` union, with a 1:1 `InputWithdrawnHookEvent`: AGENTS.md's kernel invariant
requires it. Emitted only when something was withdrawn. `messages` is what the caller may
give back, in commit order and as the objects queued (before any `message_end`
replacement): the live prompt first when `prompt_withdrawn`, then steer and follow-up user
messages that committed, then those the abort cleared from the two queues. Not injected or
drained ones.

The cleared lines go on the prompt's event, which then goes out even if nothing committed.
With no prompt alive (idle, or a public compaction) `abort()` emits it itself, ahead of the
`abort` hook, whose raise must not lose them. An abort made before `_run` (limit 2), or one
that lands after the prompt's last check, leaves them for the prompt's release. Whichever
site emits takes the list, so each line goes once. `AbortHookEvent` is unchanged.

On a turn's abort it comes before that turn's `turn_end` and `agent_end`. Between runs it
comes after them, since `agent_end` has gone out. RPC forwards it untransformed, like every
other event; nothing in the RPC command roster changes, and clients ignore a type they do
not know (the control-v1 envelope of ADR-0275 is untouched). The event is aelix-only, the
second after `model_catalog_refresh`.

### 6. The TUI gives it back

`_on_interrupt` and, in the countdown, `_on_retry_interrupt` first take the steer and
follow-up queues' text into the input box (they share `_dequeue`'s body, not its "No queued
messages" notice), then abort. The `input_withdrawn` handler puts the withdrawn text ahead of
whatever the box holds, so the order reads prompt, steers, queued lines, draft. A `/skill:` or
template prompt comes back as typed, not expanded: `_input_loop` keeps the raw line around
`harness.prompt`. Since these lines left the queues first, the event carries none of them and
nothing arrives twice.

The line submitted from the box comes back as the box read when Enter went down. `_accept`
keeps that reading and the paste entries it names beside the registry until the turn ends,
and `give_back_text` restores them for a text equal to the line: words typed around a paste
stay visible with the placeholder in its place, a long typed line stays text, and a `/skill:`
line is the same case. Any other text as large as a collapsed paste (`_should_collapse_paste`),
a queued line, Alt+Up's or an extension's, has no such reading and comes back as one
placeholder through `_register_paste`, typed words inside it. Submit expands both byte for
byte, and a draft keeps its placeholders.

An abort's give-back stamps the chrome. Within `_CTRL_C_EXIT_WINDOW` (2 s) of it, Ctrl+C on an
idle box does nothing rather than clear it, so a double press that outlasts the abort keeps
the text; it does not arm the exit timer, and after the window Ctrl+C clears as before (#66).
Alt+Up is a key pressed on purpose and does not stamp.

The abort line gains the fact, once: `✖ Operation aborted. Your message is back in the
input; the model will not see it.` The countdown has no such line left to join, so the same
sentence follows `Retry failed: Retry cancelled`, printed by the event. A latch in
`note_input_returned`, reset on `agent_start`, holds it to once per run: an Esc during the
overflow summary reaches it for the queued lines and again for the withdrawn prompt. The user's
block stays in scrollback.

### 7. `/clone` asks the path

`/clone` and RPC `clone` ask for a conversation on the branch, not in the file, narrowing
ADR-0239 decision 8. A session whose only message was withdrawn has a leaf and that message
behind it; asked of the file, it cloned an empty session and switched to it. A leaf moved back
by hand is the same state and is refused too, where pi, reading `fileEntries`, clones it.

## Limits

1. **The compaction barrier.** After a compaction committed inside the prompt the summary
   may hold the input and the kept tail cannot be moved behind it, so nothing is withdrawn.
2. **The #336 window.** An abort made before `_run` (in a hook) is still discarded with
   its flag and the turn runs, as a plain abort is today. The window includes `_run`'s own
   `build_context` await, before the turn task exists (`harness/core.py:5473`, Codex finding
   1): the request still goes out, and the input stays. Queued lines an Esc gives back in
   that window can come back with no sentence if the previous run already said it, because
   the notice's latch resets on `agent_start`.
3. **A steer between drain and commit.** Pre-existing: a steer the loop has drained and
   not yet committed is lost on cancel. It is neither in the queue nor in the event.
4. **The file keeps the text.** The withdrawn entry stays on the branch left behind and on
   disk: the `/resume` list's preview and `last:` line can still show it, and RPC
   `get_fork_messages` lists it. Forking there is sound: the text comes back and the context
   lacks it. This removes it from context, not from storage.
5. **Hosts must handle `input_withdrawn`** to give the text back. One that does not loses it
   from view, though the file keeps it (limit 4). RPC `get_messages` after an abort reads the
   aborted marker without the input.
6. **Delegation stop keeps input** only because it never passes True. #123 must choose
   explicitly if it ever sends RPC `abort` to a child.
7. **An extension's turn.** A `trigger_turn` prompt withdrawn by a countdown Esc puts the
   extension's text in the input box, since no typed line exists for it.
8. **The `abort` hook's lists on a TUI Esc.** The TUI empties the steer and follow-up queues
   before it aborts, in pi's order, so that hook's `cleared_steer` and `cleared_follow_up` are
   empty on an Esc that gave them back. Nothing in the product reads them. RPC `abort` still
   fills them, and those lines also travel on `input_withdrawn`.
9. **An adapter that turns the cancel into something else.** One that raises, or sends an
   aborted error event, instead of letting `CancelledError` through does not reach `_run`'s
   abort close-out, so `agent_end` goes out before `input_withdrawn` and still holds the input
   (`harness/core.py:5813`, Codex finding 3). No shipping adapter does. The event is the final
   word.
10. **An extension's turn does not arm the keys.** `trigger_turn` runs with the chrome not
    running and `interrupt_armed` false, so Esc and Ctrl+C do not reach it in the TUI
    (`tui/chrome.py:1033`). Pre-existing (Codex finding 6).
11. **`withdraw_tail` is not atomic on a suspending `FileSystem`.** An append that straddles
    its leaf line can put the input back. `LocalFileSystem`, the only one shipped, has a
    synchronous `append_file` and cannot reach it (`session/session.py:330`).
12. **The aborted marker counts in stats.** `/session` and RPC stats count the empty in-memory
    marker until the session is reopened, so `messages` can read one more than the path holds.
13. **An Esc during an overflow summary does not interrupt it (#347).** The spinner stays and
    the text returns when the summary does; an Esc in a hook before it still spends the call.
14. **The countdown with nothing withdrawn says nothing.** Queued lines given back there print
    no sentence; they just appear in the box.
15. **A Ctrl+C on text typed within the 2 s window does nothing,** and shows nothing.

## Verification

Regression: 58 cases in the harness file (every row of §1, with and without a session,
checking next request, live state, reopened JSONL and queue), 26 in the TUI file, 3 in the RPC
file, 2 more in `tests/cli/test_session_start_model_413.py` for §7, on Python 3.11, 3.12 and
3.14. The event-name gates went from 37 names to 38. Thirty mutations of the first pass each
fail a test; three survived that pass and got one (a failed answer alone is not input, a
replaced follow-up, a turn nobody typed). Twenty-four more for review round 1 each fail a test;
one survived (queued lines after a barrier said too late) and got one. Ten for round 2 each fail
a test; one survived (the abort line not counting as having said it) and got one.

The evidence stays local and untracked, as ADR-0258's `.omc/probes/320/` does, so a fresh clone
does not have it: under `.omc/probes/654/`, `impl-evidence/`, `fix-r1/`, `verify-r1/`, `verify-r2/`
and `fix-r2/`, with the Codex probes in `.omc/specs/654-codex-probes/`.

Live on OpenRouter `openai/gpt-4o-mini`, the real TUI in a pty, a scratch cwd and an isolated
agent dir (`.omc/probes/654/impl-evidence/`):

- Esc mid-stream after `The secret word is PINEAPPLE…`: the text came back in the input and
  the notice printed. Asked `What secret word did I give you? Reply NONE if none.` the model
  answered `NONE`. The file: `user(PINEAPPLE) <- leaf -> user(What secret…)`, one line with
  the word, off the path.
- `--continue` on that session: the word is absent from the replay, `Did I tell you any secret
  word…` answered `NO`.
- `echo MARKER654 > marker.txt` through `bash`, Esc on the next response: plain `✖ Operation
  aborted`, the input box empty, call and result on the path; asked which file it created, the
  model answered `marker.txt`.
- Beyond the three the design required: a prompt, a queued line and a typed line all came back
  in order; RPC `abort` gave `input_withdrawn` before `turn_end`, `get_messages` held the marker
  alone and the next answer was `NONE`; and against a local server answering `503` (no real
  model), Esc in the countdown printed `Retry failed: Retry cancelled`, then the sentence, with
  the line back in the box.

Review round 1, the same way, under `.omc/probes/654/fix-r1/`:

- A double Ctrl+C mid-stream on `openai/gpt-4o-mini`, 150 ms apart, and two more presses after
  the abort had ended: the text stayed in the box, no exit hint. Past the window Ctrl+C cleared
  it. Asked for the word, the model answered `NONE`.
- A 200-line paste, Esc, on the same model: the box read `[Pasted text #2 +201 lines]` with the
  footer in place, and the resubmitted message on the path was byte-equal; the model echoed its
  first and last lines. A scripted server read the same bytes off the wire, a draft's own
  placeholder included.
- A real RPC process on the same model, prompt, steer, follow-up, abort: one `input_withdrawn`
  naming all three, before `turn_end`, and `clone` refused. An idle steer and abort named the
  line with `prompt_withdrawn` false; a second abort said nothing. The next answer was `NONE`.
- Against a local server answering `503` (no real model), Esc in the countdown with two queued
  lines and a draft: the box read prompt, steer, follow-up, draft, the footer lost its `2 queued`,
  the notice printed once, and the next request held only the next line. A double press in the
  countdown kept the text.

Review round 2, the same way, under `.omc/probes/654/fix-r2/`:

- Words around a 200-line paste on `openai/gpt-4o-mini`, `앞에 친 글: [Pasted text #1 +200 lines] 뒤
  끝 …`, Esc mid-stream, the abort line about a second later with the answer still running: the
  box read as it had, nothing stayed on the path, and the resubmitted message in the session
  JSONL was byte-equal (6552 characters). A typed line of 1340 characters came back as text.
- Against a local server: an overflow, the summary held, a steer and a follow-up queued, Esc, the
  summary released. The sentence printed once, with no compaction entry, and the next run's Esc
  printed it again for that run.
