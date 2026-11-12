# 0201. The RPC delegation channel, and the turn contract it needs

Status: Accepted (2026-07-31) — owner-ratified protocol fork (D2), owner-ratified
band reading (D1), owner-ratified kernel scope (D3/D4). Design record that lands
**after** the implementation, deliberately: see *Why this record was written last*.
**Amended 2026-10-28 (ADR-0276, #604): a delegation has no budget. Row 5 of the
turn contract no longer happens on the delegation channel, `send_timeout_ms` is
`RPC_COMMAND_TIMEOUT_MS = 180_000` rather than the delegation budget, and the
turn is awaited with no limit (`prompt_and_wait(unbounded=True)`) — see the
dated notes under the contract table and the seam table.**
**Amended 2026-09-19 (ADR-0243, #199): the channel's own session flag is
`--session <path>` when the parent allocated a child file — see the note under
*Two argv/env decisions that are not obvious*.**
**Amended 2026-09-30 (#351): a cancellation of the caller is never swallowed by
the wait that races the terminator against the child's death — see
`## Amendment (2026-09-30, #351)`.**
**Amended 2026-10-21 (ADR-0268, #410): a `--mode json` child whose model errored
now exits 1, not 0 — see the notes under the turn contract and under the channel
comparison table.**
**Amended 2026-11-07 (#360): once `RpcClient.start()` has spawned its child, it owns
it until it returns, and every other way it ends — a cancel inside its 100 ms grace
above all, a root that exited first included — kills the child's process group (its
job on Windows, checked with an injected API only; on POSIX the root alone, through the
transport, when that kill raises) and releases the client before it raises — but for
CPython's own pipe setup failing after the fork (the root only), a spawn still
unfinished 5 s after a cancel (ended when it finishes; if its root was reaped first,
nothing is signalled and what it left in its group runs on), and, on CPython 3.11 and 3.12, an `asyncio.run` returning within a spawn's first
loop turns, which never exits (CPython gh-103847; the base too). Until it returns, a
second `start()` and every command are refused, and `stop()` asks it to give up — see
`## Amendment (2026-11-07, #360)`.**
Date: 2026-07-31
Builds on: ADR-0197 (the subagent-runtime seam and the 3-band rule this ADR
re-reads rather than amends), ADR-0198 (**this ADR delivers the `rpc` half its
*Partial fulfilment* section named as a follow-up, and the cross-channel parity
test with it**), ADR-0199 (the fan-out governance whose `permission_floor` is why
one-child-per-task is a security property and not a preference).
Relates: ADR-0056 (RPC JSONL framing — the LF-only discipline the per-direction
budget below had to preserve), ADR-0057 (RPC types & envelope), ADR-0058
(**whose `P-119 / W4 m2` carry-forward this sprint retired as a phantom** — see
`9e3f096`), ADR-0002 (small kernel — one authorised file, one hunk).
Implementation: `d4a920e`, `9e3f096`, `9d960d3`, `0eb5204`.
Working log (measurements, landmines, what was refuted): `.omc/specs/rpc-sprint-log.md`.
Pi pin: `earendil-works/pi@734e08e`.

> **Numbering note.** This record was written as ADR-0200 and renumbered to 0201 at merge
> time: `main` had independently landed ADR-0200 (catalog fetch classification and the
> installer backend, W1-A) while this sprint ran on its own branch. The commit messages on
> `9d960d3` / `0eb5204` / `e54dc8b` predate the rename and still say 0200; this file, the
> index and the band gate are the authority.

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적
목표입니다."** This record therefore separates, for every behaviour it describes,
*ported from pi*, *restored to pi*, and **aelix-original**. That separation is the
main thing it exists to write down: the sprint began by discovering that a
citation nobody had checked had sent a work item to the wrong layer for six
sprints, and the cure for that is not more prose but labelled provenance.

**Anchor convention.** Symbol names are preferred over `file:line`. The line
citations in this repository have already rotted — `batch.py` cites
`runtime.py:478-490` for a block now at `705-721`, and six files cite
`runtime.py:481` for `_new_id`, now at `719`. Anchors here are evidence for a
decision, not a maintenance contract.

---

## Why this record was written last

ADR-0058 claimed `rpc-mode.ts:379-401` "emits a synthetic terminal event".
Independently re-measured at the pin: `grep -nE "agent_end|synthetic"` over that
file returns **zero hits**, at both the pinned and the current upstream revision.
The claim was false in every clause, it propagated into an ADR, and it sent the
missing turn terminator to the wrong layer for six sprints.

So this record was written **after** the code, and every claim in it is either a
measurement or a citation to a test that fails when the claim stops being true.
Writing it first would have reproduced the exact failure mode the sprint had just
finished retiring.

---

## Scope

**In.** The rpc half of the channel-envelope contract; the turn contract a
delegating parent needs; `RpcChannel` as the second implementation of the channel
seam; the seam itself; the kernel's abort terminator; and the band-gate decisions
all of that required.

**Out.** Any user-facing way to *select* the rpc channel — see *Deferred*.

---

## The owner decisions

### D1 — RPC belongs in product-core, fully implemented

The sprint's kickoff framed its central question as *"how much product-core may
this sprint touch?"* and offered a ladder from "zero delta" upward. **The owner
rejected the framing, not an option:**

> "rpc는 원래 패리티 충실하게 코어에 구현되는게 맞는거죠? 그리고 모든 기능들도
> 충실이 구현되어야 하구요."

The correction is exact and worth preserving, because the ladder was a
misreading of ADR-0197. The 3-band rule forbids **delegation/spawn *policy***
leaking into product-core — depth threading, `--no-agents`, consent, per-prompt
caps. It does **not** make product-core a sanctuary. P1/P2/P3 shipped a zero
product-core delta because all three sprints' work *was* policy, not because core
was untouchable.

So the rpc turn contract, the missing terminators, `_handle_prompt` dropping
`cmd.images`, the absent reader `limit=` and the absent child-death detection are
**core's own unpaid debt**, and they were fixed in core. What stays in
`aelix_agents` is unchanged: depth, `--no-agents`, consent, caps, argv/env policy.

> **Correction (2026-07-31, after the fact).** The `cmd.images` half of that
> sentence was true of the rpc layer and **false of the outcome**. `_handle_prompt`
> does now forward `cmd.images` to `harness.prompt` — but `harness.prompt` built
> its `UserMessage` from the text alone and dropped them, so the image still never
> reached the model. Fixed separately in the kernel, as parity restoration: pi
> builds every user message through one `createUserMessage(text, images)` helper
> and uses it for `prompt`, `steer`, `followUp` and `nextTurn` alike.
>
> **The lesson is worth more than the fix.** The sprint's own guard asserted the
> FORWARDING — that `prompt` was called with `images=` — and stayed green while
> the feature was broken, which is precisely the "a test that passes under
> mutation pins nothing" failure this record complains about elsewhere. A
> forwarding assertion is not a delivery assertion. The replacement asserts at the
> `stream_fn` boundary, i.e. at what the provider actually saw.

The measurable consequence of D1 is the shape of every seam in *§The seams*
below: product-core got the **mechanism** and the extension kept the **decision**.

### D2 — Keep the pi-shaped JSONL wire. No JSON-RPC, no ACP

Researched as a first-class fork, because it decides what the channel and the
parity test are built *against*. Four ground-truth agents, three independent
option designs, three adversarial judges; notes at
`.omc/specs/rpc-protocol-{recon,option,judgment}-*.md`, brief at
`.omc/specs/rpc-protocol-decision-brief.md`.

The decisive finding: **the blocking defects are semantic, not structural.** They
cost the same ~15–80 lines in any envelope, and a standard envelope fixes none of
them. The standard-protocol option's own author measured its interop dividend at
**zero** — JSON-RPC carrying snake_case kernel dataclasses under an `aelix/*`
namespace is a standard nobody speaks. External obligation is also zero: no tags,
no releases, no `aelix-web`, and `aelix-server` is `rm -f`'d from the publish set
by `release.yml`.

Borrowed *inside* the pi envelope so a future adapter is a rename rather than a
redesign: ACP's `StopReason` vocabulary, and JSON-RPC's `-32000…-32099` error band.

### D3 — Fix the kernel parity regressions. D4 — no `dialect:` seam

D3 authorises exactly one kernel file, `harness/core.py`, for exactly one thing:
an aborted turn emitted **no terminal event at all**, so every `agent_end`-waiting
parent hung for its full 60 s. D4 refuses an abstraction with one implementation.

---

## The turn contract

Six ways a delegated turn ends. The first five are the ones the plan had; **the
sixth is the one it did not, and it is the one that costs the most.**

| # | How the turn ends | What the parent observes |
|---|---|---|
| 1 | Normal completion | `agent_end`, after the real `message_end` |
| 2 | The model errored | `message_end` with `stop_reason: "error"`, then `agent_end`, **exit code 0** |
| 3 | The turn was aborted | `turn_end` + `agent_end` carrying the REAL message, `stop_reason: "aborted"` |
| 4 | The command was refused before the turn started | a correlated `success: false` response, and **no** terminator |
| 5 | The caller's budget expired | nothing; the parent's own deadline fires |
| 6 | **The child process died** | **nothing at all, until the fix below** |

*Amended 2026-10-28 by [ADR-0276](0276-delegation-has-no-time-limit.md) ([#604](https://github.com/handochan/aelix-ai/issues/604)): the
delegation channel has no budget, so row 5 no longer happens there: the turn is
awaited through `prompt_and_wait(unbounded=True)` and ends on rows 1-4, on row 6's
death leg, or on the caller's cancellation. `RpcClient`'s own default (pi's 60 s
for `None`/`0`) is unchanged for every other caller (ADR-0276 D4).*

*Amended 2026-10-21 by [ADR-0268](0268-json-mode-exits-1-when-the-last-answer-failed.md)
([#410](https://github.com/handochan/aelix-ai/issues/410)): row 2's **exit code 0**
holds for an rpc child (its exit code is its server's end, not the turn's) and for a
`--mode json` child from an older release. A `--mode json` child from this release
exits 1 for it, still with empty stderr; the events are unchanged.*

**Row 3 is the kernel change (D3), and its shape is not the obvious one.** pi's
*primary* abort path is `agent-loop.ts:196-199`: emit `turn_end` + `agent_end` on
the real streamed message, then **return normally**. `emitRunFailure` is the
fallback for a genuinely *thrown* error. Routing an abort into the existing
four-event closure block would emit a synthetic `message_start` + `message_end`
**on top of the real `message_start` that already went out** — measured live with
65 `message_update`s already streamed. pi never produces that shape, and the
synthetic message persists into the session file with the literal body
`"[error] "`, because `str(CancelledError()) == ""`.

Two more things about row 3 that cost time to learn and are cheap to write down:

* `asyncio.CancelledError` is a **`BaseException`**. "Widen the filter to bare
  `except Exception`" delivers *none* of the fix — built verbatim, `agent_end` was
  still absent with an event list byte-identical to baseline.
* The outer filter must **not** be widened to `BaseException`. Abort is handled in
  the inner handler and returns first; the four consumers that catch this
  (`tui/shell.py`, `modes/print_mode.py`, `rpc/rpc_mode.py`, `harness/core.py`'s
  own `dispose`) all filter on `Exception`, and a re-raise makes Esc kill the REPL.

**Row 4 is why the channel opts into strictness.** MEASURED at the pin: pi's
`send` (`rpc-client.ts:474`) returns the raw response, and the throw lives in the
separate `getData` (`:506-508`) — so `prompt` / `steer` / `abort` call `send`
alone and **discard** a server-side refusal. aelix ports that asymmetry
faithfully, and it is a trap for an unattended caller: the server's busy preflight
refuses a second `prompt` with exactly this shape, and a caller that discards it
waits for a terminator that is never coming. Hence
`RpcClientOptions.raise_on_command_error`, **default off to stay faithful**, and
`RpcChannel` turns it on.

**Row 6 is aelix-original.** pi has no child-exit observation after `start()`:
its whole 515-line client has two `.on(` calls — stderr `data`, and an `exit`
handler *inside* `stop()`. Its only liveness check is the same one-shot 100 ms
grace aelix already ported. MEASURED without the fix: a child exiting at 400 ms
left `prompt_and_wait(timeout_ms=5000)` blocked for **5.02 s** and then raised a
bare `TimeoutError()` with empty args, while `proc.returncode == 7` had been
readable the entire time. At the idle default that is 60 s; at a delegation budget
it was ten minutes — and with no budget since ADR-0276 (#604), the death leg is
what ends a delegation's turn wait on a dead child at all. Every reason a real child dies — a bad `--tools` name (measured:
an uncaught traceback in rpc mode, so the child dies *before the transport
exists*), no API key, an import error, an OOM kill — lands outside the 100 ms
window.

The two legs of row 6 are also **normalised to one exception**. Before: a command
issued to an already-dead child raised `RuntimeError` in 0.00 s off the broken
stdin pipe, while one issued moments earlier blocked the full budget and raised
`TimeoutError`. One fact about the world, two unrecognisably different failures,
and a caller would only ever discover whichever it hit first.

## Amendment (2026-09-30, #351) — a cancel of the caller is never swallowed by the race against the child's death

Row 6 is implemented by `RpcClient._await_terminator`, which every wait on a
child goes through: `wait_for_idle`, `collect_events`, `prompt_and_wait`'s own
`agent_end` wait, and `_send` — every command, including the `prompt` that
`prompt_and_wait` sends first. It races `done` against a `death` task built
from `self._exited.wait()`, then cancels death and joins it in a `finally`.

**The rule: a cancellation of the caller always propagates, in every loop
turn.** The caller's cancel beats a `done` that has already resolved. That is
no new semantics: one turn earlier (the wait's waiter resolved, the caller not
yet resumed) `Task.cancel()` sets `_must_cancel` and the `CancelledError`
already came out of the wait. So a command the child has already answered —
a `prompt` it accepted — can come back to a cancelled caller as
`CancelledError`.

**What broke it.** The join was `with contextlib.suppress(BaseException): await
death`. While the caller is suspended on death, `Task.cancel()` of the caller
is handed to death (its `_fut_waiter`, already cancelling) and `_must_cancel`
stays False. The caller then gets death's `CancelledError` back at the
`await`, the `suppress` eats it, and the helper sees `done.done()` and returns
normally with `cancelling()` still 1. Positions, as loop turns after the
callback that resolves `done` (pure-asyncio probe, identical on 3.11.15,
3.12.13, 3.13 and 3.14, C and pure-Python `Task`):

| turn | where the caller is | fbead6e0 | now |
|---|---|---|---|
| −1, 0 | parked on the wait's waiter | propagates | propagates |
| 1 | waiter resolved, caller not resumed | propagates (`_must_cancel`) | propagates |
| 2, and inside death's own cancellation step | suspended in the join | **swallowed** | propagates |
| 3 | death finished, caller not resumed | **swallowed** | propagates |
| 4 | fbead6e0: past the `finally`; now: the join's waiter resolved, caller not resumed | propagates at the next `await` | propagates from the helper |
| 5+ | past the `finally` | propagates at the next `await` | same |

The fix's extra suspended turn on the `done` leg is row 4: the real helper on
a bare client, cancel issued k turns after `done` resolves (3.12.13 and
3.11.15), now reads `0–4 RAISED`, `5–6` delivered at the caller's next `await`.

The TIMEOUT leg runs the same cleanup after the wait's own timer fires, and
there the lost cancel did not look like a normal return: the helper fell
through to its own `TimeoutError` with `cancelling()` still 1, so a cancelled
call was **reported as a timeout**. Real helper on a bare client, cancel
issued k turns after the timer fires (3.12.13): fbead6e0 `k=0` raised, `k=1–2`
`TimeoutError` with the cancel lost, `k=3–4` `TimeoutError` with the cancel
delivered at the caller's next `await`; now `k=0–3` raise `CancelledError` and
`k=4–5` are the `TimeoutError`-then-next-`await` case.

**What it cost** (a *task* cancel: Ctrl+C, `asyncio.timeout()`, a
`TaskGroup`; `runtime.stop()` / `stop_all()` kill the child and end the wait
through the death leg, so they were not affected — measured with a
`call_soon` chain started inside the stdout pump's step right after the
`prompt` answer resolves, `runtime.stop()` issued 0–9 turns later: every
offset `status=aborted rc=-15`, child reaped, 1 start / 1 end, on fbead6e0 and
after the fix alike). For any `RpcClient` caller, the call returned normally in
that turn as if nothing had been cancelled, and an `asyncio.timeout()` expiring
there was lost; a cancel landing as the call's own budget expired came back as
that call's `TimeoutError`. For an rpc delegation (reachable only
programmatically — no setting selects this channel, #123), that last one meant
`_run_turn`'s `except TimeoutError` returned a `timeout` envelope for a
delegation that had been cancelled; it now propagates the cancel (by code
reading; the client-level turn is pinned by the timeout-leg test below). The
production turn was `_send`'s wait for the child's answer to `prompt`:

- **a child whose turn ends** was kept working to the end of its turn, and the
  delegation then returned its result as an `ok` envelope as if it had never
  been cancelled — the Ctrl+C lost entirely. Live on fbead6e0's helper against
  OpenRouter `anthropic/claude-haiku-4.5`: `outcome=RETURNED ok=True status=ok
  exit_code=0 summary='pong' ... landed=True cancelling=1 child_rc=0`;
- **only a turn that never ends** waited out the budget with the child alive:
  probe4 (a cancel at the flood child's first reduced line) hung 12 of 12 runs
  on the lane and 5 of 12 on the main loop's run, every hang at exactly 292
  snapshots, the swallow at `_send`'s `response to 'prompt'`.

**The fix** is the #234 shape (`tools/bash.py`, ADR-0238): `death.cancel()`
then `await asyncio.wait([death])`. `wait` keeps death's own cancellation
inside death (as membership of its done set) and lets the caller's out,
because a caller cancel now cancels `wait`'s private waiter, which nobody else
shares. No retrieval is needed, unlike #234: death is `Event.wait()`, which in
practice only returns or is cancelled. The cost is suspended loop turns before
the helper returns — one more on the `done` and timeout legs, two on the death
leg, where base's `await death` never suspended (what that does to a late
`agent_end` is the next paragraph). Not awaiting death at all was rejected: it
is swallow-free too, but leaves death pending past the return and gives a
cancel nowhere to land in the cleanup, so the tests below could not tell
"arrived after the call" from "lost".

**Which leg wins is decided after the join, and the death leg's two new turns
are a margin for a completed turn — kept on purpose** (Codex's cross-review,
C2). The helper answers `done` if it has resolved by the time the join ends,
even when the child's death ended the race. fbead6e0 decided before any
suspension on the death leg; now an `agent_end` handled up to three loop
callbacks after `_exited` is set still returns where fbead6e0 reported
`RpcServerExited` from two on (Codex's `probe_late_event.py`, 3.11.15 and
3.12.13 alike: `parent offset=2 outcome=RpcServerExited` / `head offset=2
outcome=RETURN`, same at 3; both report the death from 4). So the helper's old
"first wins" was never exact and is no longer its summary. Kept, rather than
restoring the old decision point with a snapshot, because:

- an `agent_end` the parent reads after it has seen the exit was written
  *before* the child exited — a dead process writes nothing — so returning is
  the truer answer, and it is the rule the helper already applied in the same
  tick ("a completed turn is a completed turn");
- it changes nothing measured in production. A real child that writes
  `agent_end` and exits at once, the parent in `wait_for_idle`: `agent_end` was
  never still unread when `_exited` was set — 0 of 300 runs for each helper in
  each of four shapes (no flood / 400 × 4 KB lines ahead of it, the exit poll
  at the product's 50 ms / every loop turn; 3.12.13), so fbead6e0's helper and
  this one returned in all 2 400. A positive control that holds `agent_end`
  back 0.2 s reads `late=True` and `RpcServerExited` 20 of 20 on both.

`test_an_agent_end_handled_before_the_helper_returns_beats_the_observed_death`
pins both edges (offsets 0–3 return, 4–5 report the death; red at 2 and 3 with
fbead6e0's helper), so moving the margin is a decision, not a side effect.

**Measured after.** probe4: 12 of 12 ended in 0.052–0.063 s, 8 of them at the
292-snapshot turn that used to swallow. Live (same model): `L3 ...
outcome=CancelledError took=0.35s landed=True cancelling=1 child_rc=-9
alive=False rows=[] starts=1 ends=1 survivors=0`.

**Tests.** `tests/rpc/_terminator_seam.py` lands the cancel with no clock: an
`_exited` stand-in whose `wait()` builds the target's death task and cancels
the target from inside death's own cancellation step, i.e. while the target is
in the join. `tests/rpc/test_rpc_client_terminator_cancel.py` covers the five
call sites and `wait_for_idle`'s timeout leg (red on fbead6e0: every call site
returned normally with `cancelling=1`, the timeout leg raised its own
`TimeoutError`); `test_a_delegation_cancelled_in_the_prompt_answer_turn_ends_and_reaps_its_child`
in `tests/agents_ext/test_rpc_channel.py` covers the delegation for both child
shapes (red: an `ok=True summary='the answer'` envelope, and a named 20 s
bound hit). `test_a_cancelled_delegation_publishes_no_phantom_delegations`
now cancels at the first reduced line instead of after `sleep(1.0)`, the
event precondition #351 had ruled out.

Codex's cross-review found two wrong joins that passed every cancel case —
`await asyncio.sleep(0)` in place of the join, and `await death` re-raising
its `CancelledError` whenever `current_task().cancelling() > 0` — so two more
cases pin what the join must do when nobody cancels the caller, on the done
and timeout legs of `wait_for_idle`, `prompt_and_wait` and `_send`:
`test_the_cleanup_waits_for_death_to_finish_before_the_call_returns` (a death
that needs three more turns once cancelled must be finished when the call
returns — red under the `sleep(0)` join) and
`test_an_earlier_handled_cancellation_does_not_resurface_in_a_later_call` (a
caller entering with `cancelling() == 1` from a cancel it already handled
gets its answer, or its own `TimeoutError` — red under the conditional join).

**Not closed here** (same shape or same effect, filed as #357–#361, none on
the #351 turn):

- `RpcClient._teardown_tasks` joins its three tasks with
  `suppress(BaseException): await task`. On the delegation-cancel path the
  cancel is already in flight when it runs, so it changes nothing there; a
  first cancel landing in a *finished* turn's teardown is swallowed and the
  delegation returns its complete `ok` envelope. The pumps can fail with a
  foreign exception, so the fix there needs the #234 retrieval. → #357.
- `rpc/rpc_mode.py` `pump_task.cancel()` + suppressed `await pump_task` in
  `run_rpc_mode`'s `finally`. Reachable by an outer cancel of the task running
  `run_rpc_mode` — `asyncio.run`'s SIGINT handling in `aelix --mode rpc` (the
  mode's own handlers — SIGTERM/SIGHUP, SIGBREAK on win32 — only set the
  shutdown event), or the aelix-server websocket's task group. On the cancel
  path the first cancel is already propagating through the `finally` and
  survives it; only a first cancel landing in the join after a normal
  shutdown/EOF is lost.
  Cost: a process already shutting down finishes its teardown and returns
  normally (exit 0 rather than the interrupt); in the websocket handler
  `run_rpc_mode` returns as at EOF and the handler runs its own EOF teardown
  (`rpc_ws.py`: `out_queue.put_nowait(None)`, `tg.cancel_scope.cancel()`). Low.
  → #357.
- the oauth `manual_task.cancel()` joins in `openai_codex.py` and
  `anthropic.py` (a Ctrl+C in the turn the browser callback won is ignored and
  the login completes), AND the plain re-await of a manual task nobody
  cancelled (`openai_codex.py` ~366, `anthropic.py` ~297), in one turn. A
  caller cancel that lands while that task is still pending is forwarded to
  it, `_run_manual` catches it into `manual_error`, and the caller re-raises
  it. One that lands after the paste completed the task and before the caller
  resumes (turn 1 of the table above) finds its waiter already done, is
  thrown into the suppressed `await manual_task` and eaten: the login returns
  its credentials with `cancelling()` still raised. Measured by the #351
  round-2 verification on the real `login_anthropic`
  (`k=0 ... outcome=RETURNED creds.access='AT' cancelling=1`; a cancel while
  the task is pending: `k=-1 ... outcome=CancelledError`); `openai_codex.py`
  is the same code (a replica of its lines: `0:LOST` on 3.11.15 and
  3.12.13). Both oauth sites → #358.
- the four `suppress(asyncio.CancelledError, Exception)` joins in the TUI's exit
  `finally` (`tui/shell.py`: the retry countdown, external-editor and history
  tasks, then pump and chrome). Reachable only by an outer cancel of the task
  running the TUI — a SIGINT that reaches `asyncio.run` (SIGTERM/SIGHUP are
  loop handlers that request EOF, and prompt_toolkit reads Ctrl+C as a key
  while it holds the terminal; not measured whether a SIGINT can arrive
  outside that). Every one of them runs after the input loop has already
  ended, so the cost is the same as `run_rpc_mode`'s: an exit already under
  way completes and returns normally. Low; listed in #357.
- `mcp/client.py`'s `_reset_transport` swallows too but `connect()` already
  re-raises from a `cancelling()` comparison.
- Not the same shape but widened by this fix: on `_send`'s death leg,
  `_watch_for_exit` sets `_exited` and `_fail_pending` sets the pending future's
  exception in one step; a caller cancel landing before `done.result()` leaves
  that exception unretrieved, which asyncio logs at GC. fbead6e0 had that window
  for 2 turns, the fix's two suspended death-leg turns make it 4 (pure-asyncio
  replica, 3.11 and 3.12). It needs a child death and a cancel within ~4 turns,
  and costs one log line; a `done.exception()` retrieval on the raise path
  would close it. → #357.
- On CPython 3.11 only, `asyncio.wait_for` itself loses a cancel landing in
  the turns right after its inner awaitable completes (`except CancelledError:
  if fut.done(): return fut.result()`): `RpcClient._await_exit` (every `stop()`),
  the `start()` grace, and `PrintChannel`'s post-EOF exit wait and its error /
  timeout / drain legs — the one of these reachable in the default product.
  → #359.
- A cancel inside `start()`'s 100 ms grace orphans the child: `RpcChannel.run`
  re-raises without `client.stop()`, and `row.proc` is not yet set. → #360.
  *Closed 2026-11-07, in `start()` itself, with the exceptions it lists:
  `## Amendment (2026-11-07, #360)`.*

**pi.** JS promises cannot be cancelled, so neither the caller-cancel
semantics nor death's cleanup exist in pi: this helper, its defect and its fix
are aelix-original, and no pi behaviour is diverged from. One correction to
row 6 above, which was true at this ADR's pin 734e08e: pi has since added
child-exit observation — `childProcess.once("exit", …)` →
`rejectPendingRequests` (`packages/coding-agent/src/modes/rpc/rpc-client.ts:107-111`
@ 1ff5b6fdd) and an `exitError` check at the top of `send` (`:562`), from
e007fcd0d "fix(rpc): reject pending requests on child process exit". So
`_send`'s death leg now has a pi counterpart (aelix's `_fail_pending` in
`_watch_for_exit`); pi's `waitForIdle` / `collectEvents` still have none.
pi 92e8d4f02 ("stop RpcClient skipping listeners on unsubscribe") fixes a
defect aelix does not have: `_handle_stdout_line` already fans out over
`list(self._event_listeners)`. On another axis, not taken up here: pi's
`waitForIdle` / `collectEvents` resolve on `agent_settled` (`:479`, `:501`;
e9fa5a68a), and e473b5cd8 has `prompt` return a disposition (`:198`) so a
caller skips the wait when it is `"handled"` and no run started. aelix's
`wait_for_idle` / `prompt_and_wait` wait for `agent_end` and have no such
escape; whether an aelix rpc child can accept a `prompt` without an
`agent_end` — which would cost a delegation its whole budget — is unmeasured
(#361, which also records the stricter alternative to C2's margin: wait for
the stdout pump's EOF on the death leg before deciding).

## Amendment (2026-11-07, #360) — a `start()` that does not return takes its child with it

**The defect.** `RpcClient.start()` spawns the child, attaches its tree, starts
the pumps and the exit watcher, and then waits out pi's 100 ms grace to see whether
the child dies at once. A cancel landing in that grace raised out of `start` and
stopped nothing. Its only caller in the product, `RpcChannel.run`, re-raised it,
and `row.proc` is set only after `start` returns, so `stop`, `stop_all` and session
teardown had no process to reach: the orphan ADR-0197 forbids. Measured on
`fc8f3048` by pid, with a driver that cancels the task awaiting `start` either from
the exit watcher's first step (inside the shipped 100 ms grace) or, with the grace
made long, once the child has written its tree's pids (`ps` 1.5 s after the driver
exited; scratch HOME and cwd; no model call, the child is cancelled before it is
given a prompt):

```text
                                     base                                    this amendment
RpcClient, real `--mode rpc` child   alive 3 s / 10 s after the cancel,      dead 10 s after the cancel,
                                     `client.process` still set; gone 1.5 s  `client.process` None
                                     after the driver exited (macOS 2/2,     (macOS 2/2, Linux 2/2)
                                     Linux 1/1)
RpcClient, scripted child that       child and grandchild alive with PPID 1  no survivor
starts a grandchild, reads no stdin  after the driver exited (macOS 4/4,     (macOS 2/2, Linux 2/2)
                                     Linux 3/3)
runtime + RpcChannel, real child     alive at the CancelledError with        dead before the CancelledError
                                     `list() == []`, and when `stop_all`     reached the caller
                                     returned; exited 1 by itself 0.41-0.46 s (macOS 2/2, Linux 2/2)
                                     later on macOS (4 runs) and 0.44-0.63 s
                                     on Linux (3), its prompt file removed by
                                     `run`'s `finally`
```

The right-hand column is the final change, re-measured with the same driver (macOS
3.12.13, Linux 3.12.14 in docker).

**Decision: `start()` owns what it spawned until it returns, and ends it on every
other way out.** The issue offered two layers — every cancel path in the channel
runs `stop()`, or the channel fills `row.proc` before the grace. A third was taken:
whatever ends `start` after the spawn ends the child first
(`RpcClient._abandon_start`), then re-raises.

- Until `start` returns the channel holds nothing it would stop (`RpcChannel`
  publishes the process only afterwards). `RpcClient` is importable by hosts
  (`aelix_coding_agent.rpc`); a channel-side fix would leave every other caller with
  the defect. A host can still read `client.process` while `start` runs — it stays
  readable, as introspection — but a host's `stop()` is routed to `start` (below), so
  `start` is the only code that ends the child on this path.
- **A hard kill, with no soft stage.** `stop()` asks first so a child that has been
  working can finish and unwind. This one has been given nothing: since this
  amendment `_send` refuses every command until `start` returns (on the base a
  command sent inside the grace was answered, Codex's `host_half_started`; now it
  raises `RuntimeError`, pinned by
  `test_a_command_sent_before_start_returned_is_refused_and_never_written`). The kill
  is the tree's `hard_kill()` — `killpg(SIGKILL)` on the child's own group on POSIX,
  `taskkill /T /F` and `TerminateJobObject` on win32 — or, when `attach` itself
  raised, `kill_process_tree(pid)`. It goes out before the method's first `await`,
  so a second cancel cannot keep it from landing (pinned by a cancel on every loop
  turn of the abandon, which an `await` placed before the kill turns red); the
  release (`_teardown_tasks`, transport, `tree.close()`, `_proc = None`) is the
  `finally` of the whole abandon.
- **Not guarded by the root's `returncode`.** A first draft of this amendment
  skipped the kill when the root's status was already in, as the premature-exit
  branch did. Codex measured a same-group grandchild still running after both — a
  root that exited in the grace, then a cancel; and a plain premature exit — on 3.11,
  3.12 and 3.14, and verify saw it with PPID 1 on macOS and Linux. The group is now
  killed either way. `killpg` on that number
  is `stop()`'s escalation argument (ADR-0238's #220 amendment, ADR-0291 §4): a
  non-empty group's id cannot be reused while any member lives, an empty one answers
  `ESRCH`, and the residual — the group empty, its number recycled, and the new holder
  a group leader — is the one ADR-0238 accepts. On win32 the escalation is unguarded
  too: the job holds the members, and the root's handle, which the transport keeps in
  its `subprocess` extra, keeps the pid from being reused (read from CPython's source,
  not run on Windows). The pid-only kill, for an `attach` that raised and in the late
  spawn's done-callback (below), has no tree that checked `contained` behind it, so it
  keeps the guard (a plain `killpg` on the spawn's own group would also reach what a
  reaped root left; not taken, see open item 3).
- **The premature exit takes the same path.** `RpcServerExited` is raised inside the
  guarded block, so the dead child's group is killed and its transport closed. Before,
  that branch closed the tree and nothing else: the transport stayed open
  (`ResourceWarning: unclosed transport` and "Event loop is closed" at teardown, macOS
  and Linux, verify) and the grandchild ran on (Codex).
- **The spawn is shielded.** `create_subprocess_exec` forks in its first step and then
  connects the pipes over a few loop turns; a cancel landing there ran CPython's own
  cleanup, which kills the root (`Popen.kill`) and left a same-group grandchild running
  (Codex, 3.11, 3.12 and 3.14). The spawn now runs as its own task under
  `asyncio.shield`; a cancel is answered by waiting for it — absorbing further
  cancels, for at most `SPAWN_SETTLE_SECONDS` (5 s) — then attaching the tree late and
  ending the child like any other abandon, then re-raising. At the bound `start`
  raises and a done-callback on the spawn ends the child when the spawn finishes: the
  pid-only kill, sent only while the root is unreaped, then the transport's close. A
  draft without that guard signalled a pid asyncio had already reaped, on macOS and
  Linux, 3.11, 3.12 and 3.14 (verify's second round), and the pid-only kill routes a
  reused pid that is not a group leader to a plain `kill`.
- **A kill that raises still releases.** The kill catches `OSError` only, so in a
  first draft a host's audit hook refusing `killpg` (Codex) replaced the cancel and
  left the child, the transport and all three tasks. Now the cleanup error is recorded, the 5 s wait is
  skipped (nothing was sent to wait for), the release runs, the original failure stays
  what propagates, and the cleanup error rides on it as a note and goes to the loop's
  exception handler. Closing the transport is then what ends the root, through
  asyncio's `Popen.kill`.
- **A cancel that lands in the abandon is not lost.** `_teardown_tasks` joins with
  `suppress(BaseException)` (#357's shape), so on a non-cancel abandon — a premature
  exit, an `attach` that raised — a cancel landing in those joins was eaten and the
  original error came out. The abandon compares `Task.cancelling()` before and after,
  and raises `CancelledError` when it grew and the original failure was not a cancel.
- **While `start` runs (Codex 3 and 4, verify S1).** `_starting` is set before the
  first `await`: two `start()` calls in one loop turn both passed the `_proc is None`
  check and both spawned, the second overwriting the first's process and tasks. Now
  the second raises "already started". A `stop()` issued while `start` runs is a
  request: it sends the same hard kill, `start` sees `_stopping` and abandons with
  `RpcClientError("RPC server stopped before start() returned")`, and `stop` returns
  once `start` has ended. A first draft let `stop` run its own teardown of the same
  child: verify measured it sitting out both its bounds (6.00 s, macOS and Linux)
  waiting for an exit event whose watcher the abandon had cancelled, and Codex measured
  it then tearing down a restarted client's tasks and clearing its fields.
- **A release frees the child it was handed and no newer one.** `_release` and
  `_teardown_tasks` touch the client's fields only while they still hold that child and
  its tasks, so a late releaser of one child — two `stop()` calls, the second finishing
  after a restart — closes only what it was handed.
- **Commands while stopping.** `_send` refuses a command once `stop` has begun (`stop`
  has already failed every pending request with "RPC server stopped"). A command whose
  `drain()` is blocked on a full pipe is not woken by that failure; when the kill then
  breaks the pipe, `_send` now raises the failure already set on the request — reading
  it is what retires the future — instead of `RpcServerExited("RPC server closed
  stdin")` plus asyncio's "Future exception was never retrieved" (Codex 9, a 2 MB
  prompt to a child that reads nothing).
- **Task creation that raises closes its coroutine** (`_own_task`; Codex 10: "coroutine
  … was never awaited" per refused pump, reader or watcher; the spawn's own task is the
  same shape).
- `RpcChannel.run`'s `except asyncio.CancelledError: raise` stays as it is; its comment
  now names `start`'s two exceptions.

Not taken: a channel-side `stop()` on the cancel path (the host gap above, and
`stop()`'s soft first stage leaves a non-`setsid` descendant of a child that takes the
SIGTERM — ADR-0238, review posix2/F2); filling `row.proc` before the grace on its own
(it needs a spawn callback on `RpcClientOptions`, and a cancel in the grace would still
need a kill somewhere); verify's one-line fix for the `stop()` stall (set `_exited` when
the abandon's wait sees the exit) — it leaves the stall whenever a second cancel cuts that
wait short, which is Codex's sequence, and leaves two teardowns of one child.

**Entry-point sweep.** Callers of `RpcClient.start()`: `RpcChannel.run` (the one
product caller; no setting selects the channel, #123), any host through the public
`aelix_coding_agent.rpc.RpcClient`, and the tests. Nothing else in `packages/` or `src/`
constructs an `RpcClient`. Every await and raising statement from the spawn to the
normal return, then `stop`, `_release`, `_abandon_start`, `_send`'s write and
`RpcChannel.run` — "test" names a case in the files under **Tests**, "driver" a row
measured as described under **Measured**, "reasoned" neither:

```text
where                                     what ends it                          now
start: guard, argv/env, kwargs            raises before any spawn               nothing to end (test: 2 concurrent starts)
_own_task(create_subprocess_exec)         host task factory refuses             coroutine closed, nothing forked (test)
await shield(spawn)                       spawn's own exception before fork     re-raised, nothing forked (existing channel test)
                                          a cancel, then that exception         the cancel raised, nothing forked (test)
                                          cancel (any number) while it runs     wait <= 5 s, late attach, abandon (tests, driver)
                                            ... while CPython connects pipes    the same (test)
                                          still running at the 5 s bound        raises; done-callback kills group (test)
                                            ... root reaped before it lands     no signal sent (test); group left - open
                                          CPython pipe setup fails after fork   ROOT ONLY (driver: grandchild alive) - open
                                          late attach raises                    reported (test); pid-only kill (shared, test)
                                          asyncio.run exits in its 1st turns    3.11/3.12: never exits (driver) - open
attach (normal path)                      raises                                pid-only kill, released (test)
pipes check / JsonlLineReader             raise                                 group kill, released (driver)
_own_task(pump / stderr / watcher)        factory refuses                       coroutine closed, group kill (test, driver)
grace wait_for                            cancel / second cancel / storm        group kill before 1st await (tests, driver)
                                          3.11 wait_for loses the cancel        #359's; child still ended (reasoned)
_stopping check                           a stop() during start                 RpcClientError, group kill (test, driver)
RpcServerExited (premature exit)          root exited in the grace              group kill, transport closed (test, driver)
  its stderr snapshot                     raises                                released (driver)
_abandon_start: kill                      raises non-OSError                    no wait, released, both kept (test, driver)
  wait_for_exit(5.0)                      cancelled                             release in finally (test, driver)
  _release -> _teardown_tasks             swallows a cancel                     CancelledError raised (test)
  _release: transport / tree.close        raises                                fields cleared in finally, reported (reasoned)
start: finally                            -                                     _starting cleared, waiters woken
stop() while starting                     -                                     hard kill; returns once start ended (tests, driver)
  before the tree exists                  -                                     request only; start skips the grace (test)
  its settled.wait()                      cancelled                             stop raises; start still ends it (reasoned)
stop() running: soft/hard waits           cancelled                             release skipped - unchanged, see below
_release / _teardown_tasks, late          a restart in between                  newer child untouched (tests)
_send                                     while starting / stopping             refused (tests)
  write / drain                           pipe breaks under a stop              the stop's error, retrieved (test)
                                          cancelled                             popped (test), answer retrieved (reasoned)
RpcChannel.run: start() raises            cancel / any error                    start already ended it (tests, driver)
  start returned -> row.proc              -                                     no await between them
  row.stopped -> abort_child              cancelled                             child killed, client unreleased - #225
  _drive -> _shutdown                     first cancel after a finished turn    child left running - #225
runtime.stop / stop_all in the grace      no cancel                             returns before the child dies - open
```

**Stays open.**

1. **CPython's own pipe setup failing after the fork.** `create_subprocess_exec` then
   closes the transport itself (root-only `Popen.kill`) and raises, so `start` never
   gets a process to end. Codex's probe (an injected failure in the stdlib's pipe
   connection after the child wrote its pids) still leaves the same-group grandchild
   running on the final change: macOS 3.11.15, 3.12.13, 3.14.5 and Linux 3.12.14. Not
   reachable without an injected fault or fd/memory exhaustion; a real `--mode rpc`
   child had nothing under it at any of 20, 50, 95 ms and 0.5, 2, 5 s (verify, macOS,
   2 of 2 each).
2. **An `asyncio.run` that returns within the first loop turns of a spawn never exits,
   on CPython 3.11 and 3.12** — on the base as on the final change; a CPython limit this
   amendment leaves as it found it. `asyncio.run` cancels every task, CPython's own
   `_connect_pipes` task among them, and CPython's cleanup of the cancelled
   `create_subprocess_exec` then awaits `transp._wait()`, which a transport whose pipes
   never all connected does not resolve. CPython gh-103847 fixed that wait; the fix is
   in 3.13.13 and 3.14.5 here and not in 3.11.15 or 3.12.13. Before the hang that
   cleanup has killed the root, and in no hung case was anything left running (the
   child had not started anything yet). On the final change `start` itself raises at
   the 5 s bound and only CPython's `create_subprocess_exec` task stays pending; on the
   base `start` stays pending. Measured with `main` returning K loop turns (K = 0-9 and
   12) or K ms (1, 3, 10, 40) after calling `start` on a scripted child that starts a
   grandchild; "hangs" is still running 7 or 8 s later, "left" is alive 1 s after the
   process exited (macOS; verify measured 3.12 the same, K = 2 still hanging at 90 s):

   ```text
               base                                        final change
   3.11.15     hangs at K = 0-6, 1 and 3 ms; clean at      hangs at K = 1-7 and 1 ms, at 3 ms in some
                                                           runs (1 of 3 in verify's third); clean at
               K = 7; child and grandchild left at         K = 0, 8, 9, 12, 10 and 40 ms
               K = 8, 9, 12, 10 and 40 ms
   3.12.13     hangs at K = 0-6 and 1 ms; clean at K = 7;  hangs at K = 1-7 and 1 ms; clean at
               child and grandchild left at K = 8, 9, 12,  K = 0, 8, 9, 12, 3, 10 and 40 ms
               3, 10 and 40 ms
   3.14.5      never hangs; child and grandchild left at   never hangs; nothing left at any K
               K = 8, 9, 12, 10 and 40 ms
   ```

   Linux 3.11.15, 3.12.14 and 3.14.5 (docker; the base at K = 0, 1, 7, 8, 12 and 40 ms,
   the final change at K = 1, 7, 8, 12, 1 and 40 ms) gave the same rows, except that
   1 ms did not hang on 3.12.14. The window is a few loop turns, so its millisecond edge
   depends on the machine (verify's 3.12 run hung at 3 ms on the final change). Closing it on 3.11 and 3.12
   would mean cancelling the spawn task once more at the bound when something other
   than `start` cancelled it (it is shielded, so only an outside cancel-all can), which
   ends CPython's wait; tried on a copy, it turned K = 1, 4, 7 and 1 ms into an exit
   after 5.3 s with nothing left (3.12.13). Not taken here.
3. **A spawn still unfinished 5 s after the cancel** outlives `start` until it finishes;
   the done-callback then kills the group (pinned with a 0.05 s bound). What is left
   of a spawn at that point is a pipe connection; it has not been seen to take longer
   than a few loop turns. The callback's kill is the pid-only one and keeps the guard,
   so when the root has exited and been reaped by the time the spawn lands, nothing is
   signalled (pinned) and anything the root left in its group runs on.
4. **A `stop` or `stop_all` that lands inside the grace returns before the child
   dies.** Both read `row.proc is None` there, so `abort_child` only marks the row;
   `run` kills the child when the grace ends (`if row.stopped: abort_child(...)`).
   Measured from the exit watcher's first step, unchanged by this amendment: on the
   base `stop_all` returned with the child alive and it died 0.15-0.17 s later (macOS
   3 runs, Linux 3), and `stop` 0.10-0.17 s (verify: macOS 3, Linux 3, the 0.17 s its
   second round's on Linux); on the final change `stop_all` 0.10 and 0.15 s (macOS, real
   child) and 0.15 and 0.16 s (Linux, real child), 0.16 s for each of `stop` and
   `stop_all` (macOS, stub), and 0.17 s for `stop` and 0.10 s for `stop_all` (Linux,
   stub, verify's second round). It is not an
   orphan — `run` still holds it, and a `run` cancelled in what is left of the grace
   takes this amendment's path. Closing it means putting the process on the row before
   the grace, which is ADR-0276's other half of the same gap ("a child that exits
   inside that grace reports `started=False`") and #123's, as ADR-0276 records.
5. **`RpcChannel.run` after `start` returned: #225.** That issue's first item is this
   shape, measured by Codex on the final change too (3.11, 3.12, 3.14, Linux): a first
   cancel in a finished turn's `_shutdown` exit wait leaves the child running, the
   client unreleased and the row gone; a cancel inside `abort_child` between `start()`
   returning and `_drive` kills the child through the shielded reaper but never
   reaches `client.stop()`, so the transport and, on win32, the job handle are
   released only at GC. It is #225's, not this issue's.
6. **A descendant that left the child's group on POSIX** (its own `setsid`) is not
   reached, as with `stop()` (ADR-0291 limit 6). Nothing here takes a descendant
   snapshot (`descendant_pids`); on win32 `taskkill /T` does follow live parent links.
7. **Windows** was not run. The injected-API tests pin the call order: `taskkill`,
   `TerminateJobObject`, `CloseHandle`, no `CTRL_BREAK_EVENT`; and, when the job is
   refused (`attach` swallows that `OSError` into an uncontained tree), `taskkill /T`
   alone. That fallback reaches only processes whose parent is still alive, and a
   descendant created before the job assignment — on a cancelled spawn, before the late
   attach — is not in the job either. Closing it means creating the child suspended
   (or with `PROC_THREAD_ATTRIBUTE_JOB_LIST`) so the job is assigned before it runs,
   failing closed when no job can be had, and native tests for each; out of reach here.
   *2026-11-12 note (#772): on windows-latest, in 5 of 12 py3.13 runs,
   `test_a_premature_exit_ends_the_group_and_closes_the_transport` found the grandchild
   alive after its 5 s poll; the root-dead sibling, with the same attach, did not fail in
   those runs. The cause is inferred, not measured on Windows: the runner's
   `sys.executable` is CPython's venv launcher (uv copies `venvlauncher.exe` into
   `.venv\Scripts\python.exe`; `PC/venvlauncher.c` creates the interpreter at once, not
   suspended), and once the root is dead `taskkill /T` has nothing to walk. Since #772,
   `start` creates the child with `ProcessTree.suspend_flags()`'s `CREATE_SUSPENDED` and
   attaches it with `suspended=True`: assigned, then resumed. That holds on win32 where
   `NtResumeProcess` resolves; elsewhere the spawn is unchanged. It fails closed: a job,
   an assignment or a resume that fails ends the child and `start` raises
   `SuspendedLaunchError`. Pinned on every host by
   `tests/rpc/test_rpc_client_containment.py::test_the_rpc_child_is_spawned_suspended_and_its_attach_resumes_it`
   (red on `52bc607a`). The windows-only cases
   `test_on_win32_what_the_child_starts_at_once_is_born_in_its_job` (8 spawns,
   `IsProcessInJob` for the child and grandchild) and
   `test_on_win32_a_cancel_inside_the_spawn_ends_a_child_that_never_ran` run only on CI.
   Six cases now spawn the child running (`_unsuspended`), the shape a host without
   `NtResumeProcess` takes: the five that hold the spawn and need the child to run
   during the hold, and the injected-API case, whose api does not resume.
   `RpcClient.start` and `stop` still wait for the root only, not for the job's other
   members.*
8. **`stop()` itself is unchanged once `start` has returned.** A cancel inside its soft
   or hard wait skips its release (the fields stay set, `_stopping` stays true, a
   second `stop()` finishes it), and two concurrent `stop()` calls both send their
   signals; only their releases are per-child now.
9. The 3.11 `wait_for` loss in the grace stays #359's: the cancel is lost, `start`
   raises `RpcServerExited` for the child that died, and that child's group is still
   killed and the client released.
10. A second cancel that cuts the abandon's wait short closes the transport before the
   child is reaped, so asyncio's debug mode logs "Close running child process: kill"
   (macOS, 2 of the 99 sweep cases) and Linux can log "exit status already read: will
   report returncode 255" (verify, 3 of 7 runs). The child is already signalled; the
   lines are cosmetic. The cancel inside the spawn no longer produces the first of them
   (17 sweep cases on the first draft), since CPython's cleanup no longer runs there.

**Tests.** `tests/rpc/test_rpc_client_start_cancel_360.py` (29) and
`tests/agents_ext/test_rpc_channel_start_cancel_360.py` (2). They prove each row they
name by pid (`tests.process_probe`, polled for up to 5 s after `start` raised — the
root's exit is awaited inside `start`, the group's other members are signalled before
it and not awaited), by the client's fields and tasks, and by the transport's
`is_closing()`; the win32 ones prove call order only. On `fc8f3048` all 31 fail (macOS
3.12.13 and Linux 3.12.14). On a first draft of this amendment the 16 added in its
second round fail (macOS and Linux), and 22 of the 29 client tests in all (macOS). On
the second draft one fails, the late spawn whose root was already reaped. The third round's other
changes pin second-draft behaviour no test held (verify's second round): the modified
`stop()`-during-`start` test pins a stop that returned at once or sent a soft kill, and
the five added tests pin a stop landing in the spawn that waited out the grace, a
refused late attach left unreported, a cancelled command left pending, a failed spawn
replacing the cancel, and a cancel landing in CPython's own pipe connection. All 31 pass on the final change under `-X dev` on macOS
3.11.15, 3.12.13 and 3.14.5 and on Linux 3.11.15, 3.12.14 and 3.14.5, and so do the rpc
client and channel suites beside them. 21 mutants of the change, and 9 more in its
third round (verify's survivors N5, N6, N12, N16, N18 and N23, the done-callback's
guard removed and inverted, the spawn unshielded), each turn at least one of them red.

**Measured** (drivers, not tests; the right-hand column of the table above is the
first). verify's 99-case sweep (`time`, `turn`, `multi`, `seam`, `exc`, `exit`, `twice`,
`stoprace`, `stoprace2`, `chtime`, `chstop`, `cmddur`; macOS 3.12.13) on the second
draft, whose code the third round changed only in the done-callback past the 5 s bound,
which no sweep case reaches: no survivor in any of the 93 cases where `start` was cancelled or failed, and
every client whose state the case records released. The five survivors are all after a
`start` that returned and a normal `stop()` whose soft stage took the root (ADR-0238
posix2/F2, unchanged). `stop()` racing an abandon took 0.006-0.053 s (9 shapes). verify's
second-round probes on the final change, macOS and Linux, 3.11, 3.12 and 3.14: a late
spawn whose root was already reaped is sent no signal; a `stop()` landing in the spawn
returns only after `start` has ended, both pids gone; a `stop()` in the grace of a child
that ignores `SIGTERM` returns in 0.016-0.046 s with both pids gone. Codex's probes, on
macOS 3.11.15, 3.12.13 and 3.14.5 on the final change and on Linux 3.11.15, 3.12.14 and
3.14.5 on the second draft (verify's second round): dead-root
cancel, premature exit, cancel inside the spawn and both channel paths — grandchild
gone; concurrent starts — one spawn, the second refused; `stop` racing an abandon with
a restart — 0.104-0.113 s, the restarted child kept; a refused `killpg` — the cancel
kept, the client released; injected setup failures — no "never awaited" warning; the
premature exit — transport closed before the loop closed; a command inside the grace —
refused. Its backpressured form now needs a started client and is a test. Two of
Codex's probes still show their defect: open items 1 and 5.

**pi.** pi's `start` waits its grace on `await new Promise((resolve) =>
setTimeout(resolve, 100))` (`packages/coding-agent/src/modes/rpc/rpc-client.ts:133`
@ 085c84e98), and a JS promise cannot be cancelled, so pi has no such path. pi's
`stop` is root-only (`kill("SIGTERM")`, then `kill("SIGKILL")` after 1 s). Two
lifecycle rules here diverge from pi on purpose, and a third differs too: pi's
`this.process` guard (`:75`)
already refuses a concurrent start because its `spawn` is synchronous, where
`create_subprocess_exec` suspends; and pi's `send` (`:556-561`) accepts a command as
soon as the process exists, where here `start` may still end that child; and pi's
`stop` SIGTERMs the root without waiting for `start`, where here `stop()` during `start`
asks it to give up and returns once it has (the third read from pi's source, not run).

**Not run.** Windows (the injected tests only; CI runs the real-process cases there).
A real model — no provider or session path is touched, and the child is cancelled
before it is given a prompt. Python 3.13.

---

## The rpc half of the envelope contract

`--mode rpc` and `--mode json` serialise the **same** kernel dataclasses through
`dataclasses.asdict`, so the event shapes are identical and there is no field
mapping. What differs is everything around them.

| Field | `--mode json` (ADR-0198) | `--mode rpc` |
|---|---|---|
| `exit_code` | the child's status; it exits when done | the child exits when **told**, so the status describes teardown, not the turn |
| terminator | pipe EOF *and* `agent_end` | `agent_end` alone; the pipes outlive the turn |
| `dropped_lines` | `LineAssembler`'s per-line budget | opt-in per-direction budget, republished by the client |
| `stderr_tail` | a 64 KiB ring | the client's capture, capped to the same 64 KiB by the channel |
| `elapsed_ms` | from `run()` entry | the same, and it additionally covers a handshake |

**`exit_code` must not be trusted on either channel, and on rpc it means less.**
`build_result` already tightens a proposed `ok` when the stream disagrees — a
child whose model errored exits 0 with empty stderr while the stream carries
`stop_reason: "error"` (row 2). On rpc the exit code additionally cannot describe
the turn at all, because the turn ends while the process lives.

*Amended 2026-10-21 by ADR-0268 (#410): the `--mode json` child of this release
exits 1 for row 2, so its exit code now agrees with the stream there. The rule
above stands: a child from an older release still exits 0, and the rpc channel's
exit code still describes teardown.*

**`dropped_lines`: the budget is per-direction, and that is a correctness
requirement rather than a tidiness one.** `rpc/_jsonl.py` serves **both**
directions — the client reads a child's stdout with it, the server reads its own
stdin with it. MEASURED with an unconditional budget: a 5000-char command at a
2048 budget **vanished with no signal at all**; the server emits no error response
for a line it never saw, so the client's request future never resolves and the
caller eats the full 30 s send timeout. That is the exact class of silent-failure
defect this sprint exists to eliminate.

So the budget is a **constructor argument defaulting to `None`**, and the server's
intake does not opt in. **Binding for anyone adding one later: a budget on the
intake direction must emit an explicit over-budget error response, never a silent
drop.**

One more thing the budget had to learn: a check on the un-terminated tail alone
enforces **nothing** for any line shorter than the read chunk. Measured — a
500-char line at a 64-byte budget sailed straight through, because its terminator
arrived in the same chunk and the check was never reached.

---

## `RpcChannel`: one child per task

The kickoff called for a "long-lived" channel, and the obvious reading — one child
answering many delegations — **cannot be built**. Three measured blockers:

1. **The 29-command rpc surface cannot re-target a live child.** There is no
   `set_permission_mode`, `set_system_prompt`, `set_tools` or `set_cwd`. Every
   `SpawnPlan` field that varies per delegation — the resolved profile, the
   consent-resolved posture, the contained cwd — is startup-only.

   **This is a security consequence.** ADR-0199 §3.9's `permission_floor` exists
   so a batch's later waves honour a posture the human tightened *after* wave 1
   started. On a reused child there is no command to tighten with, so the floor
   would be silently inert while `SubagentResult.permission_mode` reported a
   posture the child was not running under.

2. **The runtime deregisters a child that outlives its delegation.** MEASURED
   against the real `_SubagentRuntimeImpl`: a child surviving `run()` was
   invisible to `list` / `status` / `stop` / `stop_all` and to teardown —
   verbatim the orphan ADR-0197 forbids and that `stop_all`'s own docstring says
   P3 already measured and fixed once.

3. **`set_model` — the one mutable knob, and the last argument for reuse — does
   not work on a spawned child at all.** `cli/entry.py`'s rpc branch passes no
   model registry, so the child answers `set_model` / `cycle_model` /
   `get_available_models` with "no registry configured". The model must come from
   the profile's `--model` / `--provider`.

**What the channel therefore buys over `PrintChannel`, stated honestly:** a live,
bidirectional command channel to a *running* turn — an `abort` the child
acknowledges rather than a signal, `steer` and `follow_up` at turn boundaries, and
a correlated request/response surface for a future chain mode. On a strict
one-shot path it buys none of that and costs the whole containment list. That is a
real cost and this record does not hide it.

### Two argv/env decisions that are not obvious

* **The task goes over the wire, not on the argv.** `profile_to_argv(oneshot=False)`
  *silently drops* its `task` argument. An author "fixing" the builder to pass it
  would change nothing while believing otherwise — and would put the user's prompt
  on a command line visible to every process on the box.
* **`--no-session` must be appended by the channel.** The oneshot prefix supplies
  it; the rpc prefix does not. MEASURED: without it the child writes a real
  `~/.aelix/sessions/…jsonl` even with `AELIX_CODING_AGENT_DIR` pointed elsewhere,
  and under ADR-0199's `MAX_DELEGATIONS_PER_PROMPT = 12` that is twelve session
  files per prompt in the user's `/resume` picker that they never started.

  > **Amendment (2026-09-19, ADR-0243).** The channel still appends the session
  > flag itself, but the flag is now `--session <absolute path>` — the file the
  > parent published for this child beside its own, in a directory no picker
  > scans — and `--no-session` only when there is none
  > (`build_rpc_child_argv(…, session_path=…)`). The reason above still holds
  > for the fallback. This half is pinned by argv tests and by one scripted
  > `RpcChannel.run` that records the argv it builds; no rpc child, scripted or
  > real, has ever appended to a session file, and the channel has not run a
  > real model (#123).
* **`AELIX_STDIN_TIMEOUT` is deleted, not set.** It is inert on this path today
  (`entry.py` reads piped stdin only for print/json), but here stdin is the
  **transport**, so the two ways of being wrong are not symmetric: if that flag
  ever became mode-agnostic, a one-second stdin timeout would tear the command
  channel down a second after the child booted.

---

## The seams (D1 in practice)

| Seam (product-core) | The decision it carries (extension) |
|---|---|
| `RpcClientOptions.argv` | the complete command line; the shipped default points at the umbrella package's **mock-echo demo**, not the CLI |
| `RpcClientOptions.env_base` | a complete environment, because `env` can only add or overwrite — a DELETION was inexpressible, which made the mandatory `AELIX_MCP_CONFIG` pop impossible and every child would fan out its own copy of every configured MCP server |
| `RpcClientOptions.preexec_fn` | the parent-death signal, whose implementation lives in band 3 and which product-core may not import |
| `RpcClientOptions.raise_on_command_error` | row 4 above |
| `RpcClientOptions.send_timeout_ms` | a cold real child is ~25 s to its read loop, ~18.6 s of it import time; pi's 30 s default made that a coin flip. *Amended 2026-10-28 (ADR-0276, #604): the channel sets `RPC_COMMAND_TIMEOUT_MS = 180_000`, a bound on a command's answer, not the delegation budget it used to be; a command that times out is an `error`* |
| `RpcClient.process` | the reaper, the descendant walk and the registry row all take a `Process`, and none can be built from the public surface otherwise |

`start_new_session=True` is **not** a seam — it is unconditional, because without
it the child joins the parent's process group and one Ctrl+C SIGINTs every rpc
child at once, with nothing to convert that into a response.

### `SubagentChannel`

Measured, not designed: an AST walk over `runtime.py` and `extension.py` finds
exactly **one** attribute read on the channel object — `run`. The suite already
injected bare duck-typed stand-ins, so declaring the Protocol was a
zero-behaviour-change replacement for two concrete annotations.

**The contract is wider than the signature, and this is the first place it is
written down:**

* `run` must **not raise** except on cancellation — the runtime's call site is
  `try/finally` with no `except`, so an escape reaches the harness's tool
  dispatcher on the single-task door.
* `run` must leave `child.state` **terminal** before returning.
* `run` may mutate `child.stream` in place; that is what the progress taps publish.
* `child.proc` must hold a **real process**, because `stop` / `stop_all` /
  teardown bypass the channel entirely and hand it to the reaper.

---

## The cross-channel parity test (ADR-0198's other follow-up)

One scripted agent body, run through a `--mode json -p` one-shot reading its task
from argv and a `--mode rpc` server reading it off the wire, must produce the same
`SubagentResult`. **No model and no network**: the events are scripted, so what is
under test is the channel.

Three forms: the happy path; a failing agent (`stop_reason: "error"` with exit 0,
which is the case that proves neither channel trusts the exit status alone); and
tool narrowing, because `dropped_tools` is a security-relevant field.

Excluded from the comparison, deliberately: `id` (minted per delegation),
`elapsed_ms`, `exit_code` (see the table above), and `details` (carries stderr,
which the two children write differently).

---

## Band-gate decisions

* **`_KERNEL_CHANGE_ALLOWLIST`** gains nothing here; `harness/core.py` was already
  its single entry, added by `d4a920e` with the reasoning quoted in place. The
  gate is a freeze-by-exception, not a blanket allowlist, so every other kernel
  file still fires.
* **`_SPAWN_ALLOWLIST`** already exempted `rpc/rpc_client.py` wholesale, which is
  why the containment kwargs needed no gate change. A spawn in a **new**
  product-core file would still be red by construction, and that remains correct.
* **`_PRODUCT_CORE_CAP_ALLOWLIST` was not amended, and that was a design choice.**
  Every idiomatic name for a framing budget trips `_CAP_NAME_RE`, and the
  tempting workarounds are both wrong: a name that *hides* what the constant is
  defeats a gate that exists to catch exactly that drift, and a leading underscore
  exempts it silently. So the budget and the reader limit are **parameters with
  `None` defaults**, letting band 3 supply the numbers
  (`stream.MAX_LINE_BYTES`, `print_channel.STREAM_LIMIT_BYTES`). The policy number
  stays where the band rule wants it and no new core constant exists to argue about.

---

## What is NOT fixed, and why

**`agent_end` carries no correlation id — no session event does.** Two overlapping
turns on one client cannot be told apart, and a stale terminator resolves the
wrong waiter. MEASURED: a delegation returned another's answer, another's token
usage and another's terminator, with `ok=True`, in a fifth of its own runtime.

**pi has the identical defect** (`rpc-client.ts:429-432`, `:458-468`); it was
simply unreachable there, because pi never ran two delegations through one client.
ADR-0199's `MAX_DELEGATIONS_PER_PROMPT = 12` is what makes it reachable here.

**It is not solvable on the client side.** The abandoned turn emits `agent_start`
*and* `agent_end` after the second waiter subscribes, so no epoch or pairing can
separate them. The two fixes that do work are both elsewhere and both in place:
the server's busy preflight refuses the second `prompt` outright, and one child
per task means two delegations never share a client. **Binding: do not add a
client-side "fix" that only appears to work.**

---

## Consequences

- ADR-0198's *Partial fulfilment* is closed: the rpc half of the envelope contract
  and the cross-channel parity test both exist.
- ADR-0058's `P-119 / W4 m2` carry-forward is retired as a phantom (`9e3f096`).
- `print_channel.py` no longer claims to be the only file in aelix that spawns a
  subagent. It was true and now is not; both spawners are in this extension and
  product-core still contains no line of either.
- `RpcClient` is, for the first time, usable against a real child: its shipped
  default argv pointed at a mock-echo demo, and every test overrode it.

## Deferred, with the reason

- **No user-facing way to select the rpc channel.** It is reachable
  programmatically (`AgentsExtension(channel=…)`) and by tests. A selector needs a
  setting whose read is **GLOBAL-scope-only**, the same property
  `get_features_agents` has and states as a security property — "a merged read
  would let any cloned repo switch delegation on from its own
  `.aelix/settings.json`". A merged read here would let a cloned repo choose the
  channel, which is the same self-elevation defeat onto a channel the user did not
  pick. **Owner decision; half-wiring it is worse than not wiring it.**
- ~~**`tests/pi_parity` fixtures pin FALSE pi facts.**~~ **CLOSED** — audited and
  removed. All 22 pinned files were fetched and counted: **24 of the 27 claims
  were false**, off by as much as +5885 lines, and two fixtures contradicted each
  other about the same file at the same SHA. Only three were right, two of which
  were the only two anybody had ever measured. `pi_file_loc` is gone from all
  seven fixtures along with both "guards", which compared the fixture to
  hardcoded constants and therefore never read pi at all;
  `test_no_fixture_carries_unverifiable_loc_metadata` now asserts its absence.
  Line counts were never a parity property — the SHA, the command surface and
  the wire shapes are, and those have real assertions.
- **`steer` / `follow_up` for chain mode.** `prompt` rejects on busy; `steer` and
  `follow_up` enqueue. That is the turn-boundary design for `{previous}`
  substitution and it wants its own decision.
