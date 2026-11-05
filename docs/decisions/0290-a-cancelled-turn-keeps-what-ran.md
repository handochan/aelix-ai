# 0290. A cancelled turn keeps what ran

Status: Accepted (2026-11-04, #672, #673). **Corrected 2026-11-06 by #698:** a raised cancel count alone stops
nothing (§1, §3, limits 14, 15, 17, 19, 20, 21, 22).
Date: 2026-11-04
Issue: #672, #673, one site. Design and the owner's decision (2026-11-04):
`.omc/specs/672-673-design-2026-11-04.md`. Batch B6, P1.
Narrows: ADR-0276 D14 and ADR-0277 D14 and limit 5 (a cancelled call no longer goes without a
result in a turn that gets to unwind; dated notes there), ADR-0027 (the wait is `asyncio.wait` over
held tasks now; its decision stands).
Relates: ADR-0286 (what already ran stays, the owner's final decision behind this; its rule counts
a committed result as an answer), ADR-0145 (`message_end` replacement), ADR-0235 (pi is a reference,
not a target), ADR-0021 and ADR-0027 (the parallel path), #654, #133, pi#7053, pi#9986.
pi: `earendil-works/pi` `0c7c74198`. Same outcome, a different mechanism (§4): its abort is a signal
its tools honour, `packages/coding-agent/src/core/tools/bash.ts:370-375` throws `Command aborted`
with the output so far and `packages/agent/src/agent-loop.ts:855-863` makes that a result.
Tests: `tests/harness/test_finished_tools_keep_results_672.py`,
`tests/harness/test_cut_call_gets_a_result_673.py`,
`tests/harness/test_the_way_out_of_a_cancel_672_673.py`, `tests/harness/test_stale_cancel_count_698.py`,
`tests/tui/test_interrupt_with_a_stale_count_698.py`, with `tests/harness/_cancelled_turn.py`.

## Context

Measured on `origin/main` `e0b7a53b`, a real JSONL session and real tools, Esc as `abort()`
(`.omc/probes/654/followups/a_parallel_batch.py`, `b_bash_killed.py`, `recheck-e0b7a53b/`):

```text
parallel batch, read + ls finished, grep running:
  next request: result(c1) "No result provided", result(c2) "No result provided", result(c3) "No result provided"
bash running:
  partial.txt exists, process killed; next request: result(c1) "No result provided"; no tool_execution_end
```

The screen had the `read` result; the file did not. `abort()` is `turn_task.cancel()`, and
`CancelledError` skipped `_execute_and_finalize`'s `except Exception` and came up through
`asyncio.gather`, which gives nothing back when it is cancelled, past Phase 3, the one place results
become messages. So the model was shown a successful `read` as failed, could not tell a killed
command from one that never began, and had no output from either. ADR-0286's "what already ran
stays" did not reach any of this: #654 changed the input, not the loop.

## Decision

### 1. The rule

Cancellation is a cancel of the task a call runs in: Esc, Ctrl+C, RPC `abort`, `dispose()`, a
signal, an outer task cancel. All of them, because a record of what ran is true whoever stopped the
turn. A tool that ran stays in context with its result, and the model can tell a call that was cut
from one that never began.

What says a cancel came is not an exception anyone saw, and it is not the task's own count either.
*Corrected 2026-11-06 (#698).* This read "the task's own count, `asyncio.current_task().cancelling()`", and
that killed turns nobody cancelled: on 3.11 and 3.12 a stdlib `TaskGroup` whose child fails after its body
leaves the count of the task it ran on one up (fixed in 3.13), as aiohttp <= 3.10.5 and async-timeout <= 4.0.2
do on a timeout. A sequential tool, a `tool_call` hook or a listener runs on the turn's task, so catching such
a failure made the loop raise `CancelledError`, `_abort_requested` was false, and the TUI exited with a
traceback. A rise cannot tell a stale value from a swallowed cancel; #685 measured the same in its loader. So
**a rise in the count alone stops nothing**, and a turn stops for one of three things:

1. A `CancelledError` is delivered to the loop and goes on up.
2. Whoever cancelled the turn says so, through `AgentLoopConfig.cancel_requested`. The harness's answer is
   that `abort()` cancelled the turn's task, or that the count of the `prompt()` task rose since the turn's
   task was made. That task is parked at `await turn_task` for the whole turn, so nothing can leave a stale
   value on it, and a cancel of it reaches the turn's task through asyncio's own hand-over. What an `input` or
   `before_agent_start` hook left on it comes before the read, and so does a cancel one of them swallowed
   (limit 20). An extension's `ctx.abort()` cancels nothing and is not part of this, as it was not (#545): every
   call runs and the next request goes out.
3. The loop caught a delivered cancel where it goes on for the records (§3, §5), or cancelled a call itself,
   and remembers the task, for that run of the loop: a run nested on the same task, under a tool, a hook or a
   listener, starts with no record and at its end puts back what it found when it began. So a record the outer
   run had survives it, and one the outer run's wait writes while it is inside does not (limit 22). A
   `CancelledError` it catches counts as a cancel if the task's count rose across the await it came out of.

Every read of a count that is left compares with a value read before the wait it asks about, and none compares
with zero. In `loop.py`, 3's own, a call's first step and the wait's second cancel (§3, §5). In `harness/core.py`,
the one in 2, the close-out and `_stopped_by_abort`. In the coding agent, `_run_cancellable` in `tui/shell.py` and
`ModelCatalogRefresher.refresh` in `model_refresh.py`, with the `CancelledError` in hand, both absolute until #698:
what an extension's hook left on the TUI's input task made Esc on `/agents run` end the TUI with a traceback, and a
tool waiting on a shared refresh that ended cancelled stopped its turn. `refresh` gives no result only for a shared
run that itself ended cancelled with no rise, because a cancel already pending at its read shows no rise either: Ctrl+C in
the sync code ahead of its wait in `aelix update --models`. `McpServerConnection.connect` in
`mcp/client.py`, across the connect. `_end_if_cancelled_since` in `extensions/loader.py`, across a load step, where a
rise alone ends nothing (#685).

So:

- A `CancelledError` a tool raises with no cancel on its task is a tool bug, for instance awaiting a
  future someone else cancelled, and not a cancel of this turn. The call gets
  `Tool 'x' raised CancelledError` like any other exception and the turn goes on, siblings included.
  A `ToolCancelled` raised that way keeps its result, as an error. Before this a tool raising it by
  itself ended the turn with `prompt()` raising it, which exited the TUI with a traceback, and on the
  parallel path it left a sibling waiting for a cancel nobody had sent.
- A cancel a tool, a hook or a listener catches and swallows still stops a harness's turn once it has begun
  (limit 19 for anything but a harness, limit 20 for before it begins). A sequential call that returns out of a cancel keeps the result it returned, and no later
  call starts: a write after Esc used to run. `loop.py` reads 2 and 3 above before each model request, after
  each update of a stream, before a batch starts, after each prepare and at each call's commit on the
  sequential path, at each call of Phase 1 and at each call's first step on the parallel path, and at the end
  of a batch. What `harness/core.py` swallows on its own, a listener's or a hook's exception, did not change.

| Call state at the cancel | Result the next request carries |
|---|---|
| Finished, in a sequential or a parallel batch | Its real result, unchanged |
| Started and cut | An error result saying it was stopped and may have partly run, plus what the tool produced so far if it can say |
| Never started (not prepared yet, a later call of a sequential batch, or cancelled before its first step) | Nothing written; the wire transform's `No result provided`, which now means this or limit 13 |
| Already finalized as immediate (blocked, unknown tool) in Phase 1 of a parallel batch | Its real result |
| Caught the cancel and returned, or raised something else out of it | What it returned, or `Tool 'x' raised: …`; the batch stops after it all the same |
| Still running in a parallel batch at the second cancel | The default text, whatever it does later |
| Its hook failed with no cancel, siblings committed | Nothing written; `No result provided` (limit 13) |

Not-started calls stay `No result provided`, as pi's do (pi#9986 discusses them). The transform's
text stops being ambiguous once cut calls say what they are.

### 2. What a tool can say

`aelix_ai.tools.ToolCancelled(result)` is a `CancelledError` carrying a `ToolResult`. A tool that can
say what it did catches the cancel and raises it. It is still a cancellation, so nothing on the way
up swallows it, and the loop records its result as an error whatever the tool set, before any hook
reads it. A tool that says nothing, or a `ToolCancelled` with no `ToolResult` in it, gets
`Cancelled: the user stopped the turn while this tool ran, so it may have partly run.`

`bash` says what it printed. Around `operations.exec` it catches the cancel, after `exec` has ended
the process tree it can reach (limit 10), and builds the result from `chunks` and the status
`Command aborted: the user stopped the turn while it ran, so it may have partly run.` Synchronous,
since an await there invites the next cancel. Output that cannot be assembled, a full disk for one,
leaves the bare cancel and the default text; nothing may replace the cancel. `Command aborted` alone
stays for an `exec` that returned `exit_code=None`, pinned by `test_bash_abort_status`. The other
built-ins let the cancel through and get the default.

Up to 4 MiB that is the exit path's assembly and truncation (`_assemble_output`), byte for byte. Past
that the exit path's way is not an option here: Esc is the way out of a flood, and on `yes` for 0.25 s
decoding and splitting 750-880 MB stalled the loop 3.46-3.74 s, where no second Esc could land, and took
8.5-10 GB against the base's 1.16 GB. So `_assemble_cut_output` reads only the last four `max_bytes`, where
the kept lines come from anyway, and hands the chunks as they came to a daemon thread that writes the
full-output file. The total line count does not exist on this path: `original_lines` is 0 and the notice
says the lines were not counted (limit 11).

### 3. The loop

`_execute_and_finalize` catches `CancelledError` from `execute` without raising, so that the caller can
end and commit first. Pending update events are drained with `return_exceptions=True` and no late
`on_partial` gets through. A cut result is an error before `after_tool_call` is built, because a
redactor may handle only errors, and it still runs on one, because a redaction hook must see partial
output before it reaches the context. If that hook raises through to the loop or is cancelled, the
result is the default text with no tool output and no details, never the unredacted one (limit 22 has no result at
all in its place). The same holds
for a call that finished and a cancel landing in its hook, also when the hook catches that cancel and
raises something else: nothing has redacted that output either. A `tool_result` hook registered with
`error_mode="continue"` that raises never reaches the loop, so it leaves the output as it is (limit 9).

**Sequential.** A cut call emits `tool_execution_end` and its result message, then raises
`CancelledError`, so later calls stay unstarted. A finished call whose three steps a cancel lands in
runs them to the end first.

**Parallel.** A task per call is held, because what a call ended with has to be readable off it, and
`_wait_for_calls` waits on them with `asyncio.wait` and not `gather`. A `gather` hands each repeated
cancel to its children and never wakes its caller while one is still running, so no number of cancels
could have ended a wait for a tool that ignores them. Here each lands in the wait, which hands it on.
A cancel that comes ahead of a task's first step is read there, so that call never begins: the wait hands a
cancel on one loop turn after it lands, which `gather` did not. What it reads is a rise in the turn's count since
the line that makes the tasks, with no await from there to the wait the turn then parks in, or `cancel_requested`
for one swallowed in Phase 1 (#698). The exception is a cancel a Phase 1 hook sends its own task with no await after
it: that is under the line's read, so the calls begin and take it at their first await (limit 14). The wait notes each
call it cancels and each cancel that lands in it. Children
do not raise for a cut: `_run` emits `tool_execution_end` and returns `_Finalized`. Phase 3 then runs as
ever, every entry known to be finalized in source order, through `message_end`, which is how a result
reaches state and session and how a replacement applies. Then the cancel goes on. An immediate joins the
list before its end event, so a cancel in that event does not cost it.

**A failure in one call.** An `AgentHarnessError` from a child, a `tool_result` hook that failed with no
cancel, used to close the turn at once with nothing committed and the siblings still running, one of them
emitting `tool_execution_end` after `agent_end`. Now the wait cancels the siblings, as a cancel does, every
finalized result commits in source order, and only then is the error raised. The call that failed has no
result, as on the sequential path (limit 13). This is a harness failure: a tool's error is a result and
reaches none of it, so pi's rule that a tool error never cancels a sibling stands (ADR-0021, P-7).

### 4. Not the signal route

Option (b) of #673 passes a signal through the harness and lets each tool end itself, as pi does. It
needs every tool to honour it, and the cancel stays as the backstop anyway. Ours is a different
mechanism for the same behaviour, and pi's tools are not a parity target (ADR-0235). `cancel_requested` (§1,
#698) is the read half of that signal: pi's loop has no count to go stale, it reads `signal?.aborted`
(`packages/agent/src/agent-loop.ts:585`, `:623`, `:630`, `:651` @ `55a5c6914`), and nothing here reaches a tool.

### 5. A second cancel

`_commit_tool_result` is where the way out stays both complete and leavable. A cancel landing in one
result's `message_end` skips that result, and the rest still commit. So Esc Esc leaves: a hook parked
there cannot hold the turn, at the price of the result it held. Retrying is not an option, since nothing
can tell whether a cancelled sink got as far as the commit, and running it again would write the result
twice. `message_start` commits nothing, so a cancel landing there costs nothing: `message_end` still runs.
A cut call's hook or update drain takes the next cancel the same way. `abort()`'s repeat `cancel()` is
unchanged: it is the way out of a stuck tool.

**A tool that ignores the cancel.** The first cancel waits, because the records are the point. A second
(the count two above where it stood as the wait began, or a further cancel) is handed on too and, 50 ms later,
stops waiting: a call still running gets the default text, its end
event if it has not had one, and its commit in source order, and the cancel goes on. It ends whenever it
ends and goes nowhere: no hook, no event, no state write, and what it raises is read and logged. The
sequential path has no such wait and nothing beside the tool whose record it could cost, so there every
cancel reaches the tool, as ever (limit 1).

**The close-out.** Each emit of the aborted `turn_end` and `agent_end` runs in a task held as
`_current_turn_task`, because `abort()` otherwise aims at the finished loop task and a listener parked
there could be reached by no number of presses. A cancel from `abort()` ends that emit and the next event
goes out. A cancel of `prompt()` itself shows on its own task's count, and it propagates. Each task publishes
itself on its own first line: a cancel before a task's first step ends a coroutine that never ran, so that
event would reach nobody, and an abort landing before that line aims at a finished task and does nothing.

**One signal.** A person can press again, a supervisor sends one SIGTERM. `abort_until_idle()` aborts, and
again after 2 s while the harness is not idle, four times at most, and `dispose()` and the TUI's signal
handler both go through it. A re-abort lands in whatever is still unwinding, so the wait is also what a tool's
async cleanup has, and the base let that finish: 250 ms here cut a 0.6 s cleanup. 2 s fits inside the delegation
reaper's `DEFAULT_GRACE_SECONDS`, 5 s, which leaves a child two re-aborts before its SIGKILL. No `--grace` flag exists,
and only tests set `grace=` on `reap` and the channels. `RpcClient.stop()` is shorter (limit 12).
After that `dispose()` waits as it always did: what no cancel can unblock is what the delegation reaper's grace
and escalation are for (#220). The result a hook held may be lost (limit 12).

A sink's failure on the way out is logged and does not replace the cancel, and neither does a failure in a
call. Without a cancel a sink's failure propagates as it always did. Logs here are DEBUG: the tree installs
no logging handler, so a WARNING reaches `logging.lastResort` and paints over a live TUI.

### 6. The barrier stays

Pi#7053's fixes write each result as its tool ends. That would also cover a process kill, but file
order would become completion order, and the file is what the model is sent. Not done: the order the
model asked for is the order it reads, and a kill stays a limit.

`transform_messages` holds that order on the wire as well: a batch that needs a synthetic
`No result provided` reads in the assistant's call order, where it used to read the real results and then
the synthetic ones, so `[c1, c2, c3]` with only `c2` kept went out `[c2, c1, c3]`. Retaining an immediate is
what exposed this. Only a batch with a hole moves, one with none goes out as it arrived, and `tests/pi_parity`
pins neither. Pi appends the synthetic results after the real ones (`transform-messages.ts`); this is a known
divergence (ADR-0235).

### 7. With ADR-0286

Results committed on the way out run before `_run`'s abort close-out. The tracker counts each as an
answer, so input before them stays, and no steer can commit after them, because `abort()` has cleared
the queue. A cut call's result counts like any other: the assistant message that asked for it had
already answered the input. `agent_end.messages` carries them, and the close-out still emits one
`turn_end(aborted)` and so one abort line, below the cards.

### 8. The `agent` tool

Its `except asyncio.CancelledError` stays (ADR-0276 D14): it queues the report of what the children
did and re-raises the bare cancel, so the call gets the default text. They agree. The result says it
was stopped and may have partly run; the report, a user-role message on the next turn, says what it
did, and is the only one of the two that holds the children's text. Stopping a delegated child is a
SIGKILL (`print_channel._eager_abort`, `abort_child`), so its file ends as it did: a call with no
result, `No result provided` on its resume (ADR-0277 D14), which is why `RESUME_PREAMBLE` stays.

## Limits

1. **A tool that ignores cancellation holds a parallel turn for one Esc, and a sequential turn for all
   of them.** Before this a parallel batch returned at the first child to end cancelled and left the
   others running behind the turn; now the first cancel waits for them, since their records are the point,
   and the second stops waiting. The straggler reads as stopped even if it goes on to finish, and whatever
   it does runs on behind the turn. Sequentially nothing else runs and every further Esc reaches the tool
   again, as it always did, which is pi#7053's remaining symptom. A failure in a call (§3) holds the same way
   until one Esc.
2. **A process that dies has no way out.** SIGKILL runs nothing, and a crash between a tool's end and
   Phase 3 likewise loses that result (§6).
3. **A cancel landing while a hook or listener holds a result's `message_end` loses that result,** because
   nothing can tell whether the sink got as far as the commit. On the way out that is the second cancel; in
   an ordinary commit it can be the first. Each held result takes its own cancel, so a hook parked on every
   result takes that many presses. A cancel in `message_start` costs nothing, though a parked one takes a press.
4. **A batch cut in Phase 1 before any call started writes only immediates,** and a call the cancel
   reached before its first step has a `tool_execution_start` with no end, as any not-started call
   does. It never ran.
5. **A finished call whose `tool_result` hook a cancel landed in reads as stopped.** Its output never
   reached a redaction hook, so the default text stands in, and "may have partly run" undersells it.
6. **The text says the user stopped the turn** on a SIGTERM, `dispose()` or an outer cancel too. The
   record is true; who is not always.
7. **`turn_end(aborted).tool_results` stays empty.** The results travel on `message_end`, as they do
   everywhere else; a hook reading only that list does not see them.
8. **Only `bash` says what it printed.** `grep`, `find`, an MCP tool and an extension's get the default
   text unless they raise `ToolCancelled`, and output they had produced is gone.
9. **A `tool_result` hook registered with `error_mode="continue"` that raises leaves the output as it is,**
   as for a finished call. `HookBus` swallows the failure, so the loop never hears of it. The in-tree users of
   that mode on this event are subprocess hooks, which observe. A redactor must not register that way.
10. **A `setsid` grandchild outlives the cut,** by the process tree's design, on the base as well. `exec` ends
    the group it started, and what left that group is out of reach. The record says the command was stopped; a
    daemon it started may still run.
11. **A cut flood has no line count, and its file comes after its result.** Over 4 MiB `original_lines` is 0,
    and the notice says the lines were not counted. The file holds the raw bytes, not the decoded text the exit
    path writes. It is still being written when the result goes out, for 0.16-0.58 s after a 900 MB flood. A process
    that ends first leaves it short, and a disk that fills leaves it short with only a DEBUG line saying so. Being
    the complete output, it can take gigabytes in the temp directory, as an exit path's does.
12. **One SIGTERM gives what is unwinding 2 s, then cuts it.** A tool's async cleanup longer than that stops where it
    stands, at 2.12-2.20 s for a 4 s one, and the base let it finish. A hook parked on the way out ends the process in
    2.12-2.19 s against the base's 0.13-0.19 s, and the result it held may be lost: a `message_end` hook's is lost, a
    `tool_result` hook's call gets the default text. `RpcClient.stop()` escalates at 1 s, ahead of the re-abort, so a
    child held by such a hook exits -9 in 1.04 s there, where the base's never reached the hook; `RpcChannel` is not
    wired into the product. More held results than `abort_until_idle()` has rounds leave it waiting until the next signal.
13. **A call whose hook fails with no cancel has no result,** so it goes out as `No result provided` beside its
    siblings' results, with a `tool_execution_start` and no end, as on the sequential path. The turn itself ended in
    the failure's error message, and nothing may show that call's unredacted output.
14. **A tool cancelling its own task with `.cancel()`** is not told apart. On the parallel path it reads as cut with
    the default text and the turn goes on; sequentially that is the turn's task and the turn ends, with `prompt()`
    raising it, which exits the TUI with a traceback, as it did. A parallel call a failure cut reads the default text
    too, though no user stopped it (limit 6). *2026-11-06 (#698):* the same holds for a tool that leaves a stale count
    and raises `CancelledError` itself in that one call, since the rise is across that call
    (`test_a_tool_leaving_a_stale_count_and_raising_the_error_itself_reads_as_cut`). With the stale count from anything
    before the call, it is a tool error and the turn goes on. **A hook that cancels its own task ahead of a tool** is a
    limit too, and no longer ends the turn before the tool begins. Everything in the tree stops a turn through `abort()`
    or a cancel of `prompt()`'s task, as `asyncio.run` sends on SIGINT, and a task cancelling itself from a hook is
    neither: neither the harness nor the record knows of it until it is delivered, and the count it raised is under
    every read after it. So it depends on the next await. If the hook
    awaits, it lands there, no call begins and `prompt()` raises. If not, on the sequential path it lands at the next
    await after the hook, wherever that is. A tool that awaits reads `Tool 'x' raised CancelledError`, the later calls
    run and the next request goes out. A tool that does not keeps its result and the cancel moves on: a later call that
    awaits reads the same error, the next request goes out and `prompt()` returns; a listener that suspends takes it, the
    catch it comes out to sees no rise and the cancel is gone, so the later calls keep their results, the next request goes
    out and `prompt()` returns; past the batch's last call it is the next request's first await, and `prompt()` raises
    (`.omc/probes/698/verify-r2/l14two.py`). It stays a limit for the reason above: nothing in the tree does this, and
    all that could show it is the count, which a stale value raises just the same. On the parallel path every prepared
    call begins, those ahead of the hook's too (§3): one that awaits reads the default text, one that does
    not keeps its result, and `prompt()` raises with no further request. On the base `prompt()` raised and no call began,
    in all of these (`test_a_hook_cancelling_its_own_task_ahead_of_a_tool_reads_as_the_tools_error`,
    `test_a_hook_cancelling_its_own_task_ends_the_turn_where_it_is_next_delivered`,
    `test_a_hook_cancelling_its_own_task_lets_the_batch_begin`).
15. **A tool that calls `uncancel()` takes a cancel back only from a host that does not say it sent one.** *Corrected
    2026-11-06 (#698).* Under the harness it takes nothing back, since `abort()` and a cancel of `prompt()` are not read
    off the turn's count: a sequential batch stops after the call, `uncancel(); return` keeps what it returned and
    `uncancel(); raise` reads the default text, and a parallel batch stops as it did
    (`test_a_tool_calling_uncancel_takes_no_cancel_back_from_the_harness`, on `abort()` and on a cancel of `prompt()`). Under
    `Agent` or a direct `agent_loop` (limit 19) a sequential batch goes on, the next call starts and the next request
    goes out, and `uncancel(); raise` reads `Tool 'x' raised CancelledError`
    (`test_agent_a_tool_calling_uncancel_takes_the_cancel_back`).
16. **A straggler keeps the process from ending until a second signal:** `asyncio.run`'s teardown waits for it, so
    `/exit`, Ctrl+D and one SIGTERM all wait. The base has the same shape; every parallel batch can reach it now (§5).
17. *Gone 2026-11-06 (#698).* `aelix_agent_core.Agent` runs the loop in the caller's task, and a host that swallowed
    a cancel without `uncancel()` got `CancelledError` on its next prompt, with no request sent. Nothing reads that
    count now, and the prompt answers (`test_agent_a_stale_count_on_the_callers_task_before_the_run_stops_nothing`,
    `test_agent_a_cancel_the_loop_caught_does_not_outlive_its_run`). Limit 19 is what replaced it.
18. **An async subscriber that suspends in the close-out can cost the listeners behind it a terminator.**
    A cancel ends that emit, as §5 says, so a sync listener after it can miss `turn_end` or
    `agent_end`. The print, TUI and RPC subscribers are plain functions, so only an SDK embedder with two
    subscribers meets this. An outer cancel inside the close-out propagates with 0, 1 or 2 terminators out;
    nothing in the repository sends one in that window.
19. **`Agent` and a direct `agent_loop` caller stop on what is delivered to the loop, and on nothing else** (#698). They
    pass no `cancel_requested`, so anything running on the caller's task that swallows a cancel goes on, on both paths:
    the batch goes on and the next request goes out, the count being all that is left of it, and it stops nothing. That
    includes an unknown tool's `tool_execution_end`, which no call's task runs. On the sequential path every tool, hook and
    listener is on the caller's task. On the parallel path what runs on a call's own task, the tool, its `after_tool_call`
    hook or its `tool_execution_end` listener, stops the batch, because the wait hands the cancel on. Fourteen places on
    each path (`.omc/probes/698/verify-r2/limit19.py`) read this way under `Agent` and under a direct `agent_loop`, and all
    28 read under `Agent` as they did before #672 (`3290c330`), where the base stopped each
    (`test_agent_a_swallowed_cancel_stops_only_where_the_loop_saw_it`,
    `test_agent_a_tool_swallowing_a_cancel_on_the_sequential_path_goes_undetected`). A cancel that reaches the loop's own
    catch (the wait, an update drain, `after_tool_call`, `tool_execution_end`, `message_start`, `message_end`) with nobody
    swallowing it is remembered and stops the batch, also when something on the way out runs a nested `Agent`
    (`test_agent_a_cancel_the_loop_caught_on_its_way_is_remembered`,
    `test_a_nested_run_does_not_erase_a_cancel_the_outer_run_caught`). A direct `agent_loop` caller that wants what the
    harness has passes its own `cancel_requested`; `Agent` has no option for it. Under the harness the same goes for a
    `.cancel()` of its private turn task instead of `abort()`, which nothing in the tree does, tests aside.
20. **A cancel of `prompt()` that an `input` or `before_agent_start` hook swallows before the turn begins goes on,** as on
    the base. Those hooks run on `prompt()`'s task, ahead of §1's read in 2, which has to come after them or a stale count
    they left would read as a cancel. So the request goes out and `prompt()` returns, on a plain cancel and on an
    `asyncio.timeout` around it (`test_a_cancel_of_prompt_a_hook_swallows_before_the_turn_begins_goes_on`).
21. **`-p` with a stale count on its main task ends a SIGINT with rc=1 and a `CancelledError` traceback,** not
    `KeyboardInterrupt`'s rc=-2, as on the base. An extension's `input` or `before_agent_start` hook can leave the count
    there, and `asyncio.Runner.run` raises `KeyboardInterrupt` only when `uncancel()` comes to 0. SIGTERM is not affected:
    rc=143 and `Request aborted`.
22. **A parallel call running a nested `Agent` can lose the mark the wait wrote for it** (#698). The wait cuts the call,
    `_cancel_calls` marks the call's task with the nested run inside, and that run's `finally` puts back what it found
    there, which is nothing (§1, 3). If the tool then catches what comes out and returns raw output, and its
    `after_tool_call` hook (a harness's `tool_result` hook) raises, nothing says the call was cut, and the hook's error
    comes up instead of the withheld default text. Under `Agent` the caller sees `CancelledError`. Under a harness's failure path, a sibling's `tool_result`
    hook having failed, it sees `AgentHarnessError`. The call has no result and no end event, and nothing unredacted
    reaches the context: not state, not the session file, and the next request reads `No result provided` as in limit 13.
    An `abort()` is answered by `cancel_requested`, so the call reads the default text. Through `00ed87b0` it read the
    default text in all three (`test_a_nested_run_in_a_parallel_call_drops_the_waits_mark_and_commits_no_output`,
    `.omc/probes/698/verify-r2/h3b.py`).

## Verification

Regression: 49 cases in `test_finished_tools_keep_results_672.py`, 105 in
`test_cut_call_gets_a_result_673.py` and 80 in `test_the_way_out_of_a_cancel_672_673.py`, every row of §1 on
both paths, with and without a session, on `abort`, `abort(withdraw_unanswered=True)`, `dispose()` and an outer
`task.cancel()`, read at the wire (`transform_messages`), in live state and in the reopened JSONL, on Python 3.11,
3.12 and 3.14. A cancel walks a batch one loop turn at a time, with triggers of their own for a cancel queued ahead of
the tasks' first steps and a call cancelling the turn from inside itself. A second abort walks the rest of the way out
the same way, on both paths and over the whole close-out, the offsets counted off that very run
(`test_a_second_abort_at_any_loop_turn_costs_neither_terminator`): the walk above stops at the batch, which is how a
close-out emit reachable before its first step, losing `turn_end` or `agent_end` at one offset each, got past it. The
first implementation's forty mutations:
36 failed a test, three survived and got one (a cancel swallowed in a redaction hook, one swallowed in an ordinary
commit, a further cancel not handed on), and the fourth, `ragged_tail=False` on the assembly, needs a stubbed fallback
chain to show off win32: `tests/util/test_child_output_sites.py` has one and kills it. The review round's 51
mutations of its load-bearing lines all failed a test, after six that survived the first pass got one or, being
redundant, were removed from the code. `test_esc_while_a_tool_runs_withdraws_nothing` (ADR-0286) and
`test_a_session_switched_under_a_delegation_still_ends_the_turn_as_a_cancel` (ADR-0276) changed: each pinned the
missing result. `test_agent_resume_real_child_610.py` did not, its child being SIGKILLed.

The evidence stays local and untracked, as ADR-0286's does, so a fresh clone does not have it: `.omc/probes/672-673/`
(grids, session JSONL, drivers, the mutation script and its output, and each review's).

#698, on `c7daf997`. Before the fix, Python 3.12, the real TUI in a pty and `aelix -p`, an extension loaded with `-e` whose
sequential tool or `tool_call` hook catches a failed `TaskGroup` and no key pressed: `-p` rc=1 with one request, the TUI
exit 1 with a traceback (`.omc/probes/stale-count-product/`). After it, on 3.11 and 3.12, those and the same tool on the
parallel path answer in both, and the next line in the TUI answers too. The real TUI on 3.11 and 3.12 against a scripted
server, and on 3.12 against OpenRouter `openai/gpt-4o-mini`, with the count at 1 after that tool: Esc in a sequential tool
that swallows it stops the turn with one abort line, the tool's log reads count 2, no call or request follows, and on the
next line the model says the tool returned `cleanup-done`. In `test_stale_cancel_count_698.py` the rows on the `stale` fixture
run with a real `TaskGroup` (stale below 3.13), a simulation stale on every version, and aiohttp's shape; those on
`stale_or_not` with no stale count, the real `TaskGroup` and the simulation; and the limit rows with the one shape they name.
The file and `test_interrupt_with_a_stale_count_698.py` pass on 3.11, 3.12, 3.14 and under the coarse clock; the three test
files named above and the `tests/pi_parity` suite pass with no test changed, a real swallowed Esc included.

A verify pass and a cross-review of that found what the fix round then closed, on 3.11, 3.12 and 3.14. The real TUI on 3.12
against a scripted server: an extension's sequential tool or `tool_call` hook running a nested `Agent` under `asyncio.timeout`
gave a traceback and exit 1 where the base answered, and answers now; Esc on `/agents run` after an `input` hook left the count
at 1 on `Task-1` gave a traceback and exit 1, on the base too, and now reads `Delegation to 'explorer' stopped`, and the next
line answers. 69 mutations of the lines #698 changed, run against those files, the three older ones and the #604 interrupt
tests: 68 failed a test. The one left passes no `requested` to the `tool_execution_end` of an abandoned call, and it changes nothing: a call is abandoned
only on the wait's second cancel, whose first act is to record the turn, and that branch, instrumented, read the record set on
all 7 hits across `tests/harness`.

A second verify pass found one regression of that round in code. `refresh` read a cancel pending at its baseline as no rise,
so `aelix update --models` with Ctrl+C in the sync code ahead of the wait printed `no answer within 15 s` and exited 1. Through
the real `main_sync`, with `raise_signal` in a wrapped `ModelRegistry.create`, on 3.11, 3.12 and 3.14: `KeyboardInterrupt` now.
Eleven mutations of that clause, of the update drain's baseline and of the restore behind limit 22 all failed a test.

Live on OpenRouter `openai/gpt-4o-mini`, the real TUI in a pty, a scratch cwd and an isolated agent dir:

- `read` and a slow `-e` tool (no `execution_mode`) called together, Esc with `PARROT-7731 is the codeword`
  on screen and the turn still working: `✖ Operation aborted` 1.13 s later, once, the slow tool's card
  saying `Cancelled: …`. The file held `read`'s text with `is_error=False` and the slow tool's cut result, in
  source order. Asked to quote what `read` returned, the model quoted the file and said `slow_check` was
  cancelled. `--continue` replayed both, and asked for the codeword it answered `PARROT-7731`.
- `echo STARTED654 > started.txt; echo OUT654; sleep 45; echo DONE > finished.txt`, Esc: the card read
  `OUT654`, then `Command aborted: the user stopped the turn while it ran, so it may have partly run.`, then
  the abort line, once. `finished.txt` absent, no `sleep 45` left. Asked what happened, the model said the
  command was aborted while running and had printed `OUT654`. `--continue` replayed the same card.
- A real delegation, a child on `sleep 173` after writing `note.txt`, Esc on the parent: the parent's call
  read `Cancelled: …` once, the next prompt carried the report once, no `No result provided` on the path. The
  model said the call was stopped, had written `note.txt` and had not finished its command. The child's file
  ends in its `bash` call with no result, as ADR-0277 D14 measured.
- A real RPC process, `abort` during `read` + the slow tool, then during `bash`: `tool_execution_start` and
  `tool_execution_end` paired once each, results before `turn_end` and `agent_end`, `get_messages` holding
  both results and the aborted marker, and the next answer naming the codeword and the cancel.
- Beyond the design's four: SIGTERM to `aelix -p` during `bash` exited 143 with the cut result in the file,
  and Esc Esc and Ctrl+C Ctrl+C 150 ms apart during `bash` each ended the turn with one abort line and the
  result in the file. Rerun after the review round: 0.32 s and 0.25 s, the tree gone, and the model said the
  command did not finish and quoted what it printed.

After the review round, with a scripted model and the real tools, against `e0b7a53b` where it says so:

- `yes` through `bash`, Esc, in process: loop stall 0.003 s at 0.25 s of flood (906 MB) and 0.003 s at 0.5 s (1.8 GB),
  the base 0.002 s and 0.003 s; peak RSS 1.18 GB and 2.31 GB, the base 0.98 GB and 2.27 GB (a flood's size varies by
  run); the file complete 0.58 s and 0.83 s later. In the real TUI: abort line at 1.18 s against the base's 1.17 s, peak
  RSS 5.22 GB against 5.29 GB, and the next prompt answered. Before this round the TUI case never drew the line, killed at
  a 14 GB guard.
- One SIGTERM with a `tool_result`, `tool_execution_end` or `message_end` hook parked on the cut `bash` call, in `-p`,
  `--mode json` and `--mode rpc`, nine runs at the 250 ms wait: exit 143, 143 and 0 in 0.40-0.49 s, no traceback. The base
  in the same nine: the same codes in 0.13-0.19 s, its hooks never reached. The TUI with the `tool_result` hook: exit 0 in
  0.43 s against 0.15 s. At 2 s, below.
- A tool raising `CancelledError` by itself in the TUI: the card reads `Tool 'selfc' raised CancelledError`, the model
  answers, the TUI lives. The base exits with a traceback.
- A parallel tool ignoring cancels in the TUI: no abort line 1.5 s after one Esc, the line 1.2 s after the second, the
  card and the file reading `Cancelled: …`, and the next prompt answered. A `turn_end` hook parked on the aborted
  `turn_end`: another Esc ends the turn with one abort line.

After the second review round, the same way:

- Two RPC `abort` commands 0-2 ms apart during a slow tool: `agent_end` out 150 of 150, as on the base; 140 of 150 before
  the emit task published itself. A tool with a 150 ms cleanup in the real TUI, Ctrl+C Ctrl+C with the gap on that 150 ms:
  the abort line once and both hooks in 10 of 10, 3 of 10 before. A slow tool with Ctrl+C Ctrl+C 0.5-3 ms apart: the abort
  line in 40 of 40, 30 of 90 before and 39 of 40 on the base.
- One SIGTERM at the 2 s wait, a hook parked on each of `tool_result`, `tool_execution_end`, `message_end`, `turn_end` and
  `agent_end` × `-p`, `--mode json`, `--mode rpc` and the TUI, twenty runs: exit 143, 143, 0 and 0 in 2.12-2.19 s, no
  traceback, no tree left. A tool with an async cleanup in the same four modes: 0.6 s finishes and exits in 0.73-0.78 s,
  the base 0.75-0.77 s; 1.5 s finishes in 1.64-1.70 s; 4 s is cut, exit in 2.12-2.20 s.
- `RpcClient.stop()` on a real child: a 0.6 s cleanup finishes and the child exits 0 in 0.77 s, the base 0.80 s; a
  `tool_result` hook parked on the cut call goes to the escalation, -9 in 1.04 s, where the base exits 0 in 0.12 s.
