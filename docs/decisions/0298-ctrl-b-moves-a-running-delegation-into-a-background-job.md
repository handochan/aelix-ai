# 0298. Ctrl+B moves a running foreground delegation into a background job

Status: Accepted (2026-11-12, #775; fix round 1 2026-11-17: the commit is exception-atomic, the
rescue covers a start step that fails, publication is silent until the commit is whole, a restart
resolves an agent to its newest job, the audit sees aliases and helpers, an unknown linkage stays
unresolved). **Active since #611 S3e (#779, 2026-11-17):** `aelix_coding_agent/jobs/activation.py`
`ACTIVE = True` (ADR-0293 §12). With it off — a rollback, or `backgroundJobs.enabled: false` —
Ctrl+B is prompt_toolkit's emacs `backward-char`, as it was, and every delegation is awaited as
before (`tests/tui/test_detach_key_611.py::test_without_jobs_ctrl_b_is_backward_char_as_before`).
ADR-0293's note on the kernel's `abort_pending` is its note of 2026-11-17 (#779).
Amended 2026-11-17 by ADR-0304 (#795, #796): Ctrl+B is not gated by the agents policy; a moved run keeps running (dated note at the end).
Amended 2026-11-19 (#821): a moved run's job counts its time from the run's start and shows the start its call's wall clock read; see the note of that date.
Date: 2026-11-12
Issue: #775 (#611 slice S3a). Parent: #611. Contract: `.omc/specs/611-s3-contract-r4-2026-11-12.md`
(r4, with its 정오표; where it says "as r3" or "as r2", the r3 and r2 contracts in the same
folder). Sub-issue body: `.omc/specs/611-s3-subissues/S3a-body.md`.
Notes on: ADR-0293 (`adopt`, the offers), ADR-0295 (the move, the claim, linkage v1), ADR-0296
(the kept cleanup answer), ADR-0276 D14 (unchanged), ADR-0278 (a moved call's panel group).
Owner decisions this implements, quoted from the brief:
- 2026-11-07, #611 scope: "Ctrl+B는 단일 위임 호출만 넘기며".
- 2026-11-12, OQ-S3-3: "Ctrl+B만 + 가이드에 '두 번' (권장)".
- 2026-11-12: "설계 3차 라운드 진행", chosen over taking Ctrl+B out of #611 or holding S3.

The design — moving the running coroutine with a PEP 380 delegate, the r4 narrowing, R13–R20 and
the five-issue split — is the design lane's and the lead's, not the owner's. This lane's calls, not
the owner's or the contract's: the live-wait seam is written as one wait, a claim and a second wait
rather than a loop (§2), so no touched function's Ruff C901 score rises; the offers live in a
kind-agnostic `JobService.detach` the TUI installs a guard on (§6); the claim is a `SubagentHost`
getter `resume_refusal` reads (§8); the `/hotkeys` row and its gating (§6).
The lead's calls (리드 판단), fix round 1: R14 stands as built (§5); the S3a CHANGELOG entry stays
and S3e adds no second entry for Ctrl+B; `RESUME_RUNNING` for a running background agent stays
(§8).

pi: `earendil-works/pi` `406e719a6` (`origin/main`, read 2026-11-12) has no key that moves a running
tool call to the background; `packages/coding-agent/src/core/keybindings.ts` binds no `ctrl+b`.
The gate follows opencode (the key acts only while one thing can take it) and Qwen's "exactly one".

## Context

ADR-0295 made a delegated agent a job of its session when the model asks for it (`spawn`). #611
also asks for the user's way: a long foreground `agent()` call moved to the background without
starting again. A foreground call runs inside the turn task — `agent` is a sequential tool
(`aelix_agents/tool.py`, `execution_mode="sequential"`), so the kernel awaits it inline
(`aelix_agent_core/loop.py`) — as `spawn_granted`/`resume_granted` → `_run` → `PrintChannel.run`
→ `_stream_and_reap`, holding the process, the stream, the receipt and the cleanup task in that
chain's frames. Restarting it as a `spawn` job would run the work twice; rebuilding the chain as
continuations would rewrite `_run`. r3 and r4 chose to move the suspended coroutine itself.

## Decision

### 1. Where it applies (R1')

Only a TUI `run` with one task, or a `resume`, while the `agent` job kind is registered:
`AgentsExtension._detach_call` builds a `CallDetach` only when `detach_host()` (wired in
`cli/entry.py` to `app_mode == "interactive"`) holds, the kind is registered (jobs on, not RPC or
ACP) and `call.mode == "single"`. Batches, chains, `/agents run`, `spawn`, `-p`, json, RPC and ACP
build none and await the call as before
(`tests/agents_ext/test_detach_driver_611.py::test_a_gate_off_or_non_tui_call_builds_no_delegate`).
A running resume moves as a run does and reads "the resumed agent"
(`tests/agents_ext/test_detach_611.py::test_a_running_resume_moves_too`). The sweep of every
entry point by host is `.omc/probes/775-776/impl/entry-point-sweep.md`.

`AgentsExtension._run_call` replaces the two `await runtime.…_granted(…)` call sites of
`_execute_pending`, whose Ruff C901 score stays 17: with no `CallDetach` it is `await run`; with
one, `await drive(run, call.commit)`.

### 2. The delegate and the one point (r4 §2.2, §2.3)

`aelix_agents/detach.py` `drive(inner, commit)` is a `types.coroutine` generator that hands every
yield, `send` and `throw` to `inner` in the step the task delivers it, and records the task it
runs in. `migrate_point()` yields its sentinel only inside such a task; anywhere else it returns
`False` at once (`test_detach_crossing_611.py::test_migrate_point_outside_a_driving_task_returns_false_at_once`).

The one place a run can move is `_stream_and_reap`'s live wait. A row with `detach` set waits on
`{pumps, exited, asked}`, where `asked` is the future its offer resolves. When the wait wakes for
`asked` with neither the pumps nor the exit done, the call claims its one attempt and the run awaits
`migrate_point()`, then waits on `{pumps, exited}` once more; otherwise it carries on as before.
States: `idle` → `parked` (offer made) → `claimed` → `adopted` or `refused`; `left` when the live
wait ended without a request. After `refused` the call offers nothing again.

A Ctrl+B in the same loop turn as the child's end is ignored: the run returns its result
(`test_detach_611.py::test_a_wake_that_raced_the_end_keeps_the_result`).

### 3. The commit, in order (r4 §2.4, 정오표 E6)

`AgentsExtension._adopt`, through `detach.run_ordered_commit`, synchronous, in the turn task's step
that woke for the key:

1. **validate**: `loop.get_task_factory() is None`; every frame suspended across the point is in
   the audited set (§4); the host's guard is clear (§5); the child's `returncode` is `None`, its
   start time was read at the spawn (`RunningChild.launch_started`), it is neither stopped nor
   eagerly killed; the call's reservation holds a slot. Any failure refuses and changes nothing;
2. **build** without side effects: the job (`JobService.prepare_adopted`, not inserted), its
   `ProcessRecord` (the `Process` and that start time, never sampled again), `client_start` v1,
   the `AdoptedRunner`, and the call's answer — `Detached` with its detach text (r4 §2.4 step 2);
3. **create the job's task** — `loop.create_task(_entry(box, …), context=copy_context())`, gated by
   `box.published` and not started eagerly. Its failure is the one that cannot be undone, so it
   comes first; a failure here refuses with nothing spent;
4. **publish**, each step's undo registered BEFORE the step runs, so a step that changed something
   and then raised is undone too, and every undo runs in reverse if a later step raises: the job
   inserted (`JobService.adopt`, SILENT: no listener runs), `row.adopted`, the call's progress tap
   released, the run's host progress routed to the job, the client's `DelegationJobs` entry;
5. **the slot last**: `Reservation.split(1)` moves the call's slot into the job, the count
   unchanged; `split` builds the job's reservation before it spends the call's, so a failure there
   spends nothing;
6. **open the gate**, then tell the job's listeners (`JobService.announce_adopted`, any failure
   swallowed), and return the answer built in step 2. From the gate on nothing can raise into the
   call: the run is the job's, or — any failure before it — the turn's, never both.

A refusal at the commit commits one line in the TUI: "Ctrl+B: could not move it to the background
now (<why>)." A condition already true at the key press — or a second press after a refusal — is
not a commit refusal: the key is not taken (§6), the cursor moves left and no line appears.

Measured: a task factory, a task that cannot be created, a publish step that fails, a step that
changed something and then raised, a `split` that cannot build the job's reservation, the answer's
text failing to build, and a slot spent before the key each refuse with the job not inserted, no
client entry, the gate shut, the slot unspent and the run ending in the turn with its own result;
the calls run task, adopt, client map, slot in that order; a listener first hears of the job once
the client's entry, the progress route and the slot are all in place
(`tests/agents_ext/test_detach_commit_611.py`, `tests/agents_ext/test_detach_atomic_611.py`,
POSIX). On `fb5e7654` (round 1's head) the answer was built after the gate and its failure made a
refusal while the job also drove the run; a step's undo was registered after it; `split` spent the
call's slot first; and `adopt` ran the listeners mid-commit (Codex round 1 #1 and #3, verify F1,
F4–F6).

### 4. The crossing set (Codex M1; r4 §2.5)

`audit(fn, crossing)` admits a function only when no `with`/`async with` encloses its crossing
await, the await calls the next function directly (not through `wait_for`, `shield`,
`ensure_future`, `create_task`, `gather` or `wait`), and it names no `timeout`, `timeout_at`,
`TaskGroup`, `current_task`, `ContextVar` or `copy_context`, nor reaches a `ContextVar` through its
globals or closure. Names are not enough: a global or closure cell that IS one of those callables
under another name (`from asyncio import current_task as who`, `from contextvars import ContextVar
as Make`), a local `from … import` of one, and a plain function of the same module the crossing
function calls (up to three calls deep) that reaches one — a helper that returned a `ContextVar`
token before the point — are refused too (Codex round 1 #5, verify F7;
`test_detach_crossing_611.py::test_the_audit_sees_aliases_closures_and_helpers`). The product allowlist is five functions: `spawn_granted` and `resume_granted`
(crossing `_run`), `_run` (`channel.run`), `PrintChannel.run` (`_stream_and_reap`) and
`_stream_and_reap` (`migrate_point`), all admitted
(`test_detach_crossing_611.py::test_the_product_chain_is_admitted_five_of_five`). Every commit walks
`cr_await`/`gi_yieldfrom` from the call's coroutine to `migrate_point` and refuses a frame that is
not one of them: a subclassed channel, a test channel or a wrapper is refused at run time
(`test_detach_611.py::test_a_subclassed_channel_is_refused_at_the_chain_walk`). None of
the seven modules a moved run executes names those constructs
(`::test_no_task_bound_construct_in_a_module_a_moved_run_executes`). Codex's five negative probes —
a ContextVar token, `asyncio.timeout`, a `TaskGroup`, `current_task`, `wait_for` — do not move:
four stay in their task, the timeout probe ends in its own `TimeoutError`, and on 3.11 `wait_for`
is refused by `migrate_point`'s task check before any commit
(`::test_codex_negative_probe_does_not_move`, run on 3.11, 3.12, 3.13 and 3.14). Codex's three
round-1 probes — the aliased `ContextVar`, the aliased `current_task` and the returned helper's
token — stay in their task as well, on the same four.

A timeout, a `TaskGroup` or a cancel scope outside `drive` stays with the caller. The job's task
runs in a copy of the context taken at the commit: values are copied, dynamic scope is not.

### 5. The abort state (Codex M5; r4 §3, 정오표 E1)

A turn cancel requested before the turn task steps is thrown into the delegate's await, because a
cancel overrides a resolved wait: no commit runs, and today's Esc path and its D14 report follow
(`test_detach_commit_611.py::test_esc_and_ctrl_b_in_one_loop_turn_esc_wins`, both orders, through
the real `AgentHarness.abort`). Two checks stay as conservative refusals (R20):

- `AgentHarness.abort_pending`, a new read-only kernel property, `_abort_requested or
  _turn_cancelled` (R19). `abort()` is the only place the turn task is cancelled, and it sets the
  latch first (`tests/harness/test_abort_pending.py`). An extension's `ctx.abort()`, which cancels
  nothing, refuses a move too
  (`test_detach_commit_611.py::test_a_non_cancelling_abort_refuses_the_commit`).
- `AelixChrome.interrupt_pending`, set synchronously by `note_interrupt()` at the three places the
  TUI schedules an abort — Esc and Ctrl+C (`_on_interrupt`), SIGTERM/SIGHUP (`_handle_signal`) and
  `/quit` during a turn — and cleared when the turn ends (`set_running(False)`).

`JobsUi.detach_blocked` answers for both, for a session change or reload in progress
(`in_transition`, a stopped host) and for a held or disposed harness; it is the guard every commit
asks (`JobService.detach.guard`; each reason pinned by
`test_detach_611.py::test_the_hosts_guard_names_each_reason`). Without a host there is no guard and
every commit is refused.

**R14 (리드 판단, as built).** R14 is about one handler-loop turn: an Esc whose handler ran in the
same loop turn as Ctrl+B's wins (the latch, or the cancel thrown into the delegate's await). A raw
terminal write is processed in order, one key at a time: Esc then Ctrl+B in one write ends as Esc
(the Esc's handler latches first, and the key is not taken); Ctrl+B then Esc in one write moves the
call first, because prompt_toolkit holds a trailing Esc for its 50 ms `ttimeoutlen` — the move
commits in the meantime — and the Esc then stops only the parent's turn. Keys act in the order
pressed. Measured: verify round 1 T3a and T3b (pty), and Codex round 1's `run_tui` pipe-input
probe, the move at about 2 ms and the Esc's handling at 1.05 s.

### 6. The key (r2 §3, r3 §3, r4 §3)

`AelixChrome(detach_key=True)` — `run_tui` passes `jobs is not None` — binds `c-b` before the
extension shortcuts. Its filter, read at the press: the input has focus, no modal and no completion
menu is open, no interrupt is pending, and `JobsUi.detach_ready()` holds — a turn runs, the guard
is clear, and exactly one offer is ready. Otherwise prompt_toolkit's emacs `backward-char` takes the
key. The handler asks again; when the offer is no longer taken it moves the cursor itself. An
extension's `ctrl+b` is skipped while the key is bound, with the "built-ins win" warning. `/hotkeys`
lists "Ctrl+B — Move the running delegation to the background (cursor left otherwise; twice in
tmux)" only while background jobs are on: the row and its gating are this lane's call; "twice in
tmux" is the owner's OQ-S3-3, which is about the guide. Measured through a running chrome's key
processor: one offer takes the key and the cursor stays; none, two, no host, a pending interrupt,
no input focus or an open menu move the cursor left; under a focused modal nothing happens and the
hidden input's cursor stays put, 25 times in one process (`tests/tui/test_detach_key_611.py`).

Seen in a pty at 120x40, through `tests/jobs/gated_cli.py` with a scripted chat-completions model
and a real delegated aelix child (`.omc/probes/775-776/impl/tui/`, 2026-11-12, macOS): Ctrl+B on
the running card replaced it with the detach text, the footer read `⚙ 1 running`, the parent
answered the tool result, and once the child answered one `◆ background result` line came and the
parent's notice turn read it; with nothing running, `abcd` Ctrl+B Ctrl+B `X` gave `abXcd`; typed
during the run, `ef` Ctrl+B `Y` gave `efY` (the key moved the call); Esc then Ctrl+B in one write
ended as Esc (cancelled, the panel's stop frame, no job); Ctrl+B then Esc in one write moved the
call before the Esc, whose 50 ms prefix wait let the commit run first, and the Esc then stopped the
parent's turn only; a call never moved ended as before.

### 7. After the move (r3 §2.4, §2.5)

The job's task enters `JobService.run_adopted`, which writes the job's `start` fact (with
`client`, the linkage) and its `process` fact as a fact task, then runs `_drive` like any job's.
`AdoptedRunner.run` resumes the coroutine with `drive(inner, first=True)`: `migrate_point()`
returns `True` and the live wait goes on in the job's task. The row stays in the call's
runtime registry until `_run` ends there.

- **Records.** `_run`'s two writing sites are one helper, `_close_records`: a service-owned row hands
  them to its owner as before, a moved row (`child.adopted`) to its job, and every other run
  appends them as before. The service writes them confirmed, with the job's id, before its
  `cleanup` and `outcome`; a run that handed none makes `records()` raise, so `client_records` is
  `missing`. The run's opening `start` and `pending` stay the call's
  (`test_detach_611.py::test_ctrl_b_in_the_live_wait_moves_the_run_into_a_job` pins the order).
- **Cleanup.** The run keeps its own cleanup task (ADR-0296), which now keeps its sweep's answer
  and its error (`CleanupDuty.answer`, `.error`). `AdoptedRunner.helper_cleanup` waits for that
  task under the service's bound and maps the kept answer; a task that failed makes it raise, and
  a task still running makes the service's `wait_for` bound raise, so the job stays
  `unresolved(cleanup_error)` and is asked again.
- **Stop.** `request_stop` marks the row and calls the runtime's `stop`, today's path; a moved
  run stopped through the service ends `stopped`, its child reaped and its slot freed, with one
  notice (`test_detach_611.py::test_a_moved_run_stopped_by_the_user_ends_stopped`). The owner's
  end and `/reload` reach it through the service, and the session runtime's `stop_all` reaches the
  row too.
- **Progress.** The call's tap is released at the commit; the host tap sends the moved spawn id to
  the job's `ctx.progress` and never to the statusline bridge again
  (`::test_progress_after_the_move_goes_to_the_job_not_the_call`).
- **Linkage v1.** The `start` fact's `client` is `{"v": 1, "adopted": true, "key", "child":
  {"session_id", "rel"}, "profile", "task", "tool_call_id", "posture"}`. `_start_of`, `decode`,
  `DelegationJobs.resolve` and `recover` read a moved run's start from it. An unknown `v` is
  `unresolved(adapter_decode_error)` from `recover` whether or not a settle exists, and `decode`
  raises on it, so the service keeps the job's stored brief and body (ADR-0293's decode failure);
  on `fb5e7654` a settle made it `done` (Codex round 1 #6, verify F8;
  `test_detach_611.py::test_an_unknown_linkage_version_is_unresolved_with_or_without_a_settle`).
  A resumed agent moved again has a second job: `resolve` reads the records newest first, so a
  restart names an agent's or a session's id to its LATEST job, while a job id still finds its own
  job (Codex round 1 #4, verify F2;
  `::test_an_agent_moved_twice_resolves_to_its_latest_job_after_a_restart`, through a fresh reader
  and a fresh service's reconciliation; on `fb5e7654` both named the first, finished job).
- **What the model reads.** "Moved to the background at the user's request: agent "<name>" keeps
  running at <posture> (id …; its job is …). Do not wait, sleep or poll for it: its result comes
  back to you once, as a system notice, when it finishes. Go on with other work. agent(action=
  "status", id="…") shows how it is doing." A resumed agent reads "the resumed agent". Then exactly
  one terminal-result notice, under the job's notice id, placed at a steering boundary after the
  tool result (ADR-0293 §11); posting order is not claimed.

### 8. The claim (R15)

A `spawn` job runs on a runtime of its own, and a moved run leaves the call's registry when `_run`
ends, so neither is a row `resume_refusal` sees. `DelegationJobs.live_sessions()` — the child
sessions of the session's background agents not past their barrier — is the runtime host's
`claimed` getter while the kind is registered, and `resume_refusal` (the hook's check and the
execute-time one) answers `RESUME_RUNNING` for it
(`test_detach_611.py::test_a_running_spawned_agent_is_claimed_against_resume`,
`::test_a_moved_agent_is_running_to_resume_and_resumes_once_done`). With the gate on, what
changes for a running spawned agent is the refusal's reason and wording (리드 판단: it stays): on
`c91cdc7b` its child session's writer lock refused the resume at once — "that agent's session is in
use by another process" — measured with a real child by verify round 2
(`.omc/probes/775-776/verify-r2/`, `tui/orig-wt-resume-running/`); now it reads "that agent is
already running. Wait for its result." Round 1's "a second run and a hang" came from a stub child
that takes no writer lock, and does not hold.

### 9. The rescue (Codex M2; 정오표 E2; Codex round 1 #2)

The job's first task may end before its runner drives the moved coroutine: cancelled before its
first step, or a start step of `run_adopted` (its facts task, the liveness loop) raising before
`_drive`. Nothing else would ever resume the run. `Box.entered` is set by `AdoptedRunner.run` in
the step it starts driving the run — on `fb5e7654` `_entry` set it before `run_adopted`, so a
failing start step was never rescued (verify F3) — and until then the task's done-callback
(`detach._rescue`) acts: it sets the runner's first move to a thrown `CancelledError`, and
`JobService.rescue_adopted` runs `_drive_rescued` in a new task, which tries the missing start steps
once more, logs a failure, and runs `_drive` regardless. The run takes its own cancel path
(`_stream_and_reap`'s eager abort, `_run`'s settle handed to the job, the row popped, the cleanup
duty ended), and `_drive` treats the cancel as the service's own stop, so `_finalize` runs the
barrier with the stop policy, writes `stopped` and frees the slot.

Measured with a real child (`tests/agents_ext/test_detach_rescue_611.py`, POSIX): a direct
`task.cancel()` of the first task in the commit's own step, and `_ensure_liveness` raising in the
first task and the rescue's. Not a rescue trigger, measured by verify round 1: a stop asked in the
same step never cancels the task that has not started, which then runs and ends `stopped` by the
normal path; `shutdown` cannot meet a new adoption, since `prepare_adopted` refuses once the
service is closed. A loop torn down under a task that never ran is not measured.

### 10. A call never moved (r4 §2.7)

Claimed for the measured cases only. Five scenarios — a normal end, an interrupt queued in the
finishing step (Codex N1 A), a cancel then a late producer (N1 B), two Escs in one batch, and a
second Esc 50 ms after the first — each run `direct` (no `detach_host`) and `driven` through the
real extension, job service and print channel child, and give the same result or cancellation,
the same D14 count and text, no rows or cleanup tasks left, the closing records in the parent's
file at the return and the slot back by then (`tests/agents_ext/test_detach_driver_611.py`, POSIX;
3.11, 3.12 and 3.14). The dormant future is one more member of an `asyncio.wait` that never
completes.

## Consequences

- `JobService` gains `detach` (the offers), `prepare_adopted`, `adopt`, `unadopt`, `give_slot`,
  `announce_adopted`, `run_adopted` and `rescue_adopted`; `JobsFacade` exposes them for the kind
  its extension registered. `adopt` and `unadopt` are silent, and `unadopt` gives back the job's
  place in start order, so a refused commit leaves no gap in `seq` (verify N5). A kind declares `KindCapabilities.detachable`; delegation's is now `True`.
- `RunningChild` gains `detach`, `launch_started`, `adopted` and `session_rel`; `CleanupDuty`
  `answer` and `error`. `_SubagentRuntimeImpl._run` falls from 23 to 21 (Ruff C901); no other
  touched function's score rises.
- The kernel gains one read-only property (`AgentHarness.abort_pending`); ADR-0293's note on it
  comes with S3e (contract r4 §7).

## Limits (r4 §2.9)

- **Windows.** A moved job's evidence is its root only; on win32 its notice does not certify that
  every member of the run's Job has ended (its cleanup answer says so: "unverified"). The
  real-child tests run on POSIX; win32 is #611 S3c's.
- **The runner rule.** A moved run reads `host.model_registry()` and the extension's other live
  getters until it ends — an exemption from ADR-0293's runner rule, bounded by the stop on
  `/reload` and on the owner's end.
- **The start fact.** If it never lands, a restart reads the run as a foreground run (R18).
- **Two Escs** before the task steps coalesce, as today.
- **Conservative refusals (R20).** Any task factory refuses; any pending abort refuses, an
  extension's non-cancelling `ctx.abort()` included.
- **The rescue.** If even the rescue's task cannot be created, the run's row and closing records are
  lost; the child stops at the owner's end.
- **One loop turn.** Ctrl+B and Esc in one loop turn resolve as Esc (R14).
- **One attempt per call.** A refused call offers nothing again.
- **Terminals.** tmux takes Ctrl+B as its prefix: press it twice (owner OQ-S3-3, for the guide).
  Not measured in tmux, the VS Code terminal or a Windows console.
- **The audit's reach.** It follows plain functions of the crossing function's own module, three
  calls deep (a fourth is not followed). A method called through `self` is covered only by the
  moved-module scan (§4), and that scan reads names and attributes, so an aliased import there
  (`from asyncio import current_task as who`) is not seen. Five callables `PrintChannel.run` names
  from three modules outside the seven — `prompt_file.write_prompt_file`,
  `_parent_death.pdeathsig_preexec`, and `_process_tree.ProcessTree`, `_retained_handle` and
  `containment_spawn_kwargs` — are covered by neither; a grep of those modules finds no
  task-bound construct today (verify round 2, F2).
- **The audit errs toward refusing.** Its helper walk follows every same-module function a crossing
  function names, a cleanup task's body included (`PrintChannel.run` → `open_cleanup` →
  `_clean_up` → `_bounded`), though such a body runs in a task of its own and holds nothing across
  the point. A product helper that ever tripped it would refuse every Ctrl+B, and
  `test_detach_crossing_611.py::test_the_product_chain_is_admitted_five_of_five` would turn red.

*2026-11-17 note (#777, #611 S3c PR c2, landed with PR c1 as one PR — 리드 판단, the lead's call): a
moved job's processes when its aelix dies.* The row of a moved run stays the print channel's, so the
parent-death legs are the delegation's: `pdeathsig` on Linux (ADR-0291 limit 3) and on Windows the
kill-on-close of the print channel's Job (ADR-0238). A real TUI, started through
`tests/jobs/gated_tui_child.py` with background jobs on, moves its foreground delegation with Ctrl+B
once the child's sleeper runs, and is killed once the parent's next request carries the moved call's
answer (`tests/cli/test_parent_death_611.py`, each process read by the pid and start time it
announced, and counted gone only when first read gone within the bound, counted from the kill):

| Platform | The kill | Asserted (case) | Measured |
|---|---|---|---|
| Linux | SIGKILL | the delegated child gone within 5 s (`test_l1a_…`) | Linux docker (`python:3.12-slim`, `--init`), 20 of 20 runs over two rounds: gone 0.009–0.39 s after the kill; its shell and sleeper, recorded only, gone within 0.07 s |
| Windows | `TerminateProcess` | each announced handle (the child, its shell, its sleeper; opened before the kill, no Job handle held) unsignalled at the kill and signalled within 10 s after it (`test_l2a_…`) | at `b69890da` failed on all four windows-latest legs (the lead's CI round, run 43998348364): the child's shell and sleeper alive 10 s after the kill, so the TUI launcher does run there. #777's fix round 1 spawns the print channel's child suspended, so it is in its Job before it runs (ADR-0238's 2026-11-17 note); at `6e3f5612` passed on all four legs (CI run 44023793108) |
| macOS | SIGKILL | nothing about the children (`test_l3a_…`) | local, 20 of 20 runs over two rounds: the child, its shell and its sleeper all alive 5 s after the kill, then killed by the case |

The same delegation left in the turn, not moved, is `test_l1b_…`, `test_l2b_…` and `test_l3b_…`
(ADR-0293's 2026-11-17 note). The **Windows** limit above stands: L2a reads the processes through
handles, so it measures the Job's kill-on-close, not what a moved job's notice certifies from its
root-only evidence.

## CHANGELOG

This change's `[Unreleased]` entry was written with S3a ("built and switched off", 리드 판단). S3e,
which turns the gate on, adds no second entry for Ctrl+B.

## Not measured

The move with a real model; Linux and Windows CI (the lead's and CI's); PID reuse between the spawn
and the commit; an adopted run through `/reload`'s bound (S3c2); the TUI with the gate on in a
terminal other than the pty capture this lane made.

*2026-11-17 note (#777, #611 S3c PR c2): one of these measured in-process, and one case beside them*
(real extension, job service and `PrintChannel` child; `tests/agents_ext/test_detach_acceptance_777.py`,
POSIX):
- **An adopted run through `/reload`'s bound.** The reload's stop kills and reaps the child and the
  run's cleanup is held past the bound. `/reload` returns at the bound with the job
  `unresolved(reload_timeout)`, its slot held and its row still there. The extension's runtime is
  invalidated as the rebuild does, the run is then let go, and the job ends `stopped` with its
  settle and outcome in the session it was moved in and its slot back. The bound there is 0.5 s and
  the stop and exit graces 30 s, so the run's own late end is what ends it. With the product's 10 s,
  2 s and 45 s, the stop's escalation (`JobService._escalate`: a grace, its legs, a grace, then the
  cancel) would cancel such a run at about 4 s, inside the bound; that is read from the code, not
  measured.
- **Beside the list: a stop in the step of the move.** A `request_stop` asked in the commit's last step (`give_slot`)
  does not cancel the job's task, as #775's verify round 1 measured (§9). The task runs, `_drive` takes the run,
  and the stop reaches it (no escalation). The child is reaped, the row popped, the cleanup ended,
  and `_finalize` writes `stopped` and frees the slot. The rescue proper is §9's case.

The parent's death after a move: the Limits note above. Windows CI is still the lead's.

## Note (2026-11-17, ADR-0304, #795, #796)

Ctrl+B starts nothing and resolves no profile, so the `agents` settings lists and the identity's
`agents:` do not gate it: a run whose profile is turned off after it started still moves, runs to
its end, and its notice arrives; a later `resume` of that agent is a new start and is refused
(ADR-0304 D8). Ctrl+B is still offered for a single `run` or a `resume` only
(`tests/agents_ext/test_detach_driver_611.py`
`test_a_gate_off_or_non_tui_call_builds_no_delegate`), so no batch or chain leaves the
foreground with members queued.

## Note (2026-11-19, #821)

A moved run's job counts its time from the run's start. The commit hands `prepare_adopted` the
seconds the call ran in the foreground (`ran_for`, from the row's monotonic `started_at`, the
stamp its result's `elapsed_ms` counts from), and the job's clock and `created` start that long
before the move: its attention notice (D9's "is still running after …"), its finished notice,
`agent(action="status"|"wait")`'s "for …", the `/jobs` table and `/jobs <id>`'s "started" include
the foreground time. They counted from the move, so a run moved while stuck read "after 0s". Its
last activity is still the move, so `quiet` does not fire at once on a long-running run. A
negative `ran_for` counts as none. The job's `created`, which `/jobs <id>` shows as "started", is
the wall clock the call read at its start (`RunningChild.started_wall`), not the move's wall time
less `ran_for`: a wall clock set between the start and the move moved it (Codex's review, L11).
`tests/agents_ext/test_moved_elapsed_821.py` (the delegation row on POSIX, the service rows on
every platform, on frozen clocks since fix round 1, verify F9).
