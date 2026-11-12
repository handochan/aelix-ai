# 0295. A delegated agent can run in the background as a job of its session

Status: Accepted (2026-11-08, #723). Amended 2026-11-12 (#749, #770, #769): the restored and
unresolved texts, `result`'s refusals, paging by bytes through `job(action="result")`, a
background agent's result written to a 0600 file beside its child session and read from there once
the job service lets the job go; see the note of that date. Amended 2026-11-12 (#782): a lone
surrogate in a background agent's text is replaced by U+FFFD where it enters the client; see the
note at the end. **Inert until #611 S3:** everything below is behind
`aelix_coding_agent/jobs/activation.py` `ACTIVE = False` (ADR-0293 §12), and nothing of it is
reachable from the product before S3 sets it.
Date: 2026-11-08
Issue: #723 (#611 slice S1c). Parent: #611. Design: `.omc/specs/611-design-r6-s1-contract-2026-11-07.md`
(round 6, the S1 contract: §2.2's A rows, §2.3, §2.4, §2.10), with rounds 3–5 and Codex's reviews
of rounds 1, 3 and 5 beside it.
Supersedes: ADR-0197's prohibition of background delegation ("a background child has no owner
to reap it and no channel to report through") — the job service of ADR-0293 is that owner and
that channel. Dated notes on ADR-0197, ADR-0199, ADR-0243, ADR-0276, ADR-0277, ADR-0198,
ADR-0238 and ADR-0293.
Owner decisions this implements, as recorded in #611's body and the lead's brief:
- #604: no delegation timeout; stopped children are recorded for resume, not kept alive; `-p`
  waits for background children; one `agent` tool with an `action` parameter.
- 2026-11-07: a generic core facility with delegation as a client; a delegated child's detached
  helpers are stopped with that child (S2, #719 — reported `Unverified` until then); admission is
  configurable; every "recommended" item confirmed, among them the wait bounds (30 s minimum,
  120 s default, 30 min maximum, the third empty wait refused), the `result` action, the 16 KiB
  framed cap pointing to `agent(action="result")` and the child session file, single-task
  `spawn`, and background actions refused on RPC/ACP until #612.
pi: `earendil-works/pi` `origin/main`, `packages/durable/test/examples/22-subagent-background.ts`
(one `subagent` tool with `spawn`/`send`/`stop`/`status`; each answer reported back once as a
follow-up input marked "no reply needed"). aelix diverges knowingly: its report is ADR-0293's
framed notice, its children are processes, not conversations of one harness, and it has no
`send` (#123).

## Context

ADR-0293 (S1b, #722) built a generic job service: kinds that clients register, a barrier before
any terminal state, confirmed facts, report-only restarts, admission from global settings, one
notice per job placed by the kernel. It named delegation as client 1 and registered none. Until
now an `agent()` call blocks its parent's turn until the child ends, and `background` was refused
(`tool._BACKGROUND_REFUSED`, `runtime._BACKGROUND_REJECTED`) because nothing could own a child that
outlived the call.

## Decision

### 1. The client

`aelix_agents/jobs_client.py` registers the kind `agent`, version 1, settings key `agents`
(`backgroundJobs.limits.agents`), through `ExtensionAPI.jobs` in `AgentsExtension.__call__`, once
per harness build (a `/reload` registers a new generation). Its capabilities: background,
not detachable (Ctrl+B is S3), output, `job_tool_control=False` with the hint
`use agent(action="status"|"wait"|"stop"|"result", id=…)`, default bound 4
(`runtime.MAX_LIVE_CHILDREN`), `-p` waits for it, it notifies, `normal_exit="stop_descendants"`,
activity from the child's stdout events. Registration happens only while jobs are active and the
host is not RPC (`AgentsExtension.background_host`, wired in `cli/entry.py` to
`app_mode != "rpc"`): with the gate off, or on an RPC/ACP host, the `agent` tool's schema and
description are what they were before #611, key for key, and `/agents run` reserves nothing
(`tests/agents_ext/test_agent_background_611.py::test_with_the_gate_off_the_tool_is_what_it_was`,
`::test_an_rpc_host_refuses_the_background_actions_until_612`).

### 2. The actions

Where the kind is registered, `action` also takes `spawn`, `wait`, `status`, `stop` and `result`
(`tool.agent_tool_parameters`; `timeout` and `offset` are appended after every key a model writes
before them, the #634 order rule; `required` is empty, and the parser asks for `profile` of every
action that starts an agent). A paragraph of 627 bytes is appended to the description under its own
budget, `BACKGROUND_HEAD_MAX_BYTES = 1024`
(`::test_the_background_paragraph_stays_inside_its_own_budget_and_names_every_action`).

- **`spawn`** takes ONE task (`::test_spawn_takes_one_task_and_nothing_else`) and refuses a
  `background` key rather than ignore it (`::test_spawn_refuses_a_background_key`, verify N5). It
  is consented exactly as a single `run` is and charged against the prompt's delegation budget
  once the service admitted it: a start the service refused costs nothing, and a launch that was
  tried and failed costs one, as `_run` charges a child that died at once (ADR-0199 §3.5.2.1: never
  refunded). Every hook of one assistant message runs before any execute, so two spawns there both
  find room for one; the execute checks again, and the second is refused
  (`::test_a_spawn_is_charged_against_the_prompt_budget`,
  `::test_a_spawn_is_charged_once_admitted_and_two_in_one_message_meet_the_belt`,
  `::test_a_launch_failure_is_the_calls_error_and_no_notice_follows`, verify N3). It
  returns once the child has
  LAUNCHED, with the agent's id — its child session id, or its spawn key when the parent has no
  session file (`::test_agent_id_session_or_key`, Codex r1 #15) — and the job's id. A launch that
  fails is the call's own error, no notice follows, and the slot comes back
  (`::test_a_launch_failure_is_the_calls_error_and_no_notice_follows`).
- **`wait`** waits up to `timeout` seconds — 30 to 1800, default 120; a value outside is clamped
  and the result says so (`::test_wait_bounds`). It never stops an agent and never returns its
  result: the notice is the one delivery, and the wait gives it up to 2 s to be posted so it is
  placed right after the wait's own result. The third wait in a row that ends with nothing
  finished, in one prompt, is refused; a new prompt or a wait that saw an agent end starts the
  count again, and a notice turn does not — it is not a prompt
  (`::test_wait_never_stops_the_agent_and_the_third_empty_wait_is_refused`,
  `::test_a_notice_turn_keeps_the_prompts_empty_wait_count`, Codex #10). An `unresolved` agent
  ends a wait at once — waiting cannot change it — and the wait says aelix cannot confirm how it
  ended (`::test_a_wait_on_an_unresolved_agent_returns_at_once`); an agent that ended gives its
  notice up to 2 s to be posted (`::test_a_wait_that_saw_an_agent_end_gives_its_notice_time_to_be_posted`).
  An Esc cancels the wait, not the agent (`::test_esc_on_a_wait_cancels_the_wait_not_the_agent`).
- **`status`** — one agent or every agent of the session; `stop` — the job service's stop, which
  ends `stopped` only after the barrier confirmed the process gone; it needs an id
  (`::test_stop_and_result_need_an_id`). The notice carries what the agent had said, and its
  result names who stopped it — `stopped by you (agent(action="stop"))`, `/jobs stop`, `/reload`,
  the session's end — not the foreground `stop, /new, …` wording
  (`::test_stop_goes_through_the_barrier_and_its_partial_result_comes_back`, verify N4). Both
  answer after the notice was delivered, and a `stop` then says its result was already given
  (`::test_status_and_wait_after_delivery`, `::test_stop_after_delivery_says_its_result_was_given`,
  verify S2). After a restart, `stop` of an agent the restart found unfinished stops nothing
  (a restart never kills) and `result` reads nothing, and both say so. They promise a notice
  only while aelix still checks the job (`JobSnapshot.rechecking`): for an OS-evidence
  `unresolved` the liveness loop posts one once the process is gone (verify-r3's
  `os-unresolved-after-gone` probe); with `rechecking` false they promise none
  (`tests/agents_ext/test_child_recovery_611.py::test_after_a_restart_stop_and_result_say_only_what_is_true`,
  verify S1).
- **`result`** re-reads a finished agent's whole result — the text a foreground call would have
  returned — in pages of 16 KiB with the next offset named
  (`tests/agents_ext/test_agent_background_611.py::test_result_rereads_the_whole_result_and_a_long_one_points_there`). It is a read: it
  acknowledges nothing. After a restart it follows §8's rules.

Every id is resolved within the current session only: an id from another session reads, waits on
and stops nothing (`::test_another_sessions_agent_is_not_found`, Codex r1 #5). The ids accepted
are the agent id, the spawn key and the job id a notice shows; after a restart the job id
resolves through the service's snapshot of the current session
(`tests/agents_ext/test_child_recovery_611.py::test_a_notices_job_id_resolves_after_a_restart`,
Codex #7), and the notice's next step says the job id in its header names the same agent as the
id it gives (`tests/agents_ext/test_agent_background_611.py::test_a_cut_notice_keeps_both_pointers_in_an_uncut_footer`, verify N1).

### 3. Consent and the reservation before it

While the kind is registered, every live delegated agent — foreground or background, the model's
or `/agents run`'s — counts against the service's admission for `agent` (ADR-0199 (d) as amended
by ADR-0293 §9). A call reserves `F = min(MAX_CONCURRENCY = 4, tasks, free[, total free])` in the
`tool_call` hook, BEFORE its dialog — one for a `spawn` and for a `chain`, which runs one child at
a time (verify N2); at `F = 0` it is refused there, and no dialog is shown. The refusal counts
what runs by the kind's own noun (`KindCapabilities.noun`), whatever door the call came through:
"Delegation refused: 1 delegated agent already runs; this limit is set in settings
backgroundJobs.limits.agents. Stop one or wait; do not retry."
(`tests/jobs/test_admission_611.py::test_a_kinds_noun_counts_what_runs_with_its_plural`,
`tests/agents_ext/test_agent_background_611.py::test_no_free_slot_refuses_before_any_dialog`,
verify S3). The total across kinds, when a user sets `backgroundJobs.maxTotal`, names no kind
and calls nothing a background job: "3 run across every kind, and settings backgroundJobs.maxTotal
allows 3."
(`tests/jobs/test_admission_611.py::test_the_total_limit_names_no_kind_and_calls_nothing_a_background_job`,
verify-r2 N5). A batch then runs under a semaphore of its own, sized to `F`
(`batch._Batch.semaphore`), and nothing it was consented for is refused afterwards
(`::test_batch_sized_to_free_slots_never_refuses_after_consent`, Codex r1 #17). A `run` or
`resume` holds its slots while its children live and gives them back when the call ends
(`::test_a_foreground_run_holds_its_slot_while_it_lives`); a `spawn` hands its one slot to the
job, which releases it on confirmed cleanup. `/agents run` reserves its slot before its dialog too
(`::test_agents_run_takes_its_slot_too`). Every terminal path gives a call's slots back once
(Codex #8; each test reads the admission's count after the path, and all but the first also
count its give-backs against what was reserved): another extension's `tool_call` hook
blocking the approved call — its `tool_execution_end` releases them, the handler registered only
while the kind is
(`::test_a_call_another_extensions_hook_blocked_gives_its_slot_back`, through the real harness);
a denied or cancelled dialog (`::test_a_denied_dialog_and_a_cancelled_one_give_their_slots_back`);
approvals `execute()` never spent, at the next prompt and at the session's end
(`::test_approved_calls_never_run_give_their_slots_back_at_the_next_prompt_and_at_shutdown`); a
`resume`, refused before its dialog when nothing is free
(`::test_a_resume_takes_its_slot_before_its_dialog_and_gives_it_back`); a runner that crashes
before or after its launch (`::test_a_runner_that_crashes_gives_its_slot_back_once`). With the
gate off the per-session registry count (`MAX_LIVE_CHILDREN`) and the per-loop batch semaphore
decide, as before.

### 4. The run, and what the job owns

A background run is today's run — `runtime._run`, the print channel, the reaper — with three
differences.

- **It keeps only what it was handed** (ADR-0293 §3). The spawn reads the parent's directory,
  tools, exclusions, context-file flag, thinking level, model, model registry and session ONCE
  into a `_Launch`; the run gets a runtime of its own over a host that answers only those values
  (`jobs_client.frozen_host`) and a copy of the session's channel with its two live getters
  replaced (`PrintChannel.frozen`). Its progress goes to the job, never to the extension's
  statusline bridge. A `/reload` that invalidates the extension while the child runs changes
  nothing it reads (`::test_old_runner_never_calls_an_invalidated_api`).
- **Its row is service-owned** (`RunningChild.owner`, `print_channel.CleanupOwner`). The prompt
  directory and the process with its tree are handed to the job as soon as each exists; the
  channel's `finally` neither removes the one nor closes the other, and the session's own
  `stop_all` never reaches the row; nor does `stop_all` of the runner's own runtime, which holds
  the row (`tests/agents_ext/test_agent_background_ownership_611.py::test_stop_all_of_the_runners_own_runtime_leaves_the_directory_to_the_job`, Codex
  #13). The service removes the directory after its barrier and the cleanup duty, and the runner
  closes the tree there
  (`tests/agents_ext/test_agent_background_ownership_611.py::test_service_owned_directory_survives_channel_and_stop_all_until_helper_cleanup`,
  contract row 7's delegation half) — and only once the barrier confirmed: a barrier that did not
  leaves the tree, its win32 Job and its legs to the service's recheck, whose confirmed cleanup
  then closes it and reads `confirmed` on win32
  (`::test_an_unconfirmed_barrier_leaves_the_tree_open_and_the_recheck_closes_it`,
  `::test_win32_a_confirmed_cleanup_after_a_failed_one_reads_confirmed` on the injected seam,
  Codex #2). Every kill the channel or the reaper would make for such a row — `_eager_abort`,
  `abort_child`, the post-EOF `_reap`, the pipe-holder kill after exit — goes through the job's
  evidence legs on the SAME record the barrier confirms against (`JobContext.register_process`
  now returns it), so nothing signals before the evidence is collected
  (`::test_eager_abort_of_an_owned_row_goes_through_its_owner`,
  `::test_reap_of_an_owned_row_signals_nothing_itself`,
  `::test_reap_of_an_owned_row_escalates_through_the_owner`,
  `::test_abort_child_of_an_owned_row_goes_through_its_owner`); after the owner's hard leg, only
  the squatters the inode walk names on a dead root's pipes are signalled by pid, with no tree
  (`::test_an_owned_rows_post_exit_drain_ends_its_pipe_holder_through_the_owner`). A child whose
  tree did not attach is registered by its own process group
  (`::test_a_child_whose_tree_did_not_attach_is_registered_by_its_own_group`). The runner's own
  legs stop it without the service's escalation
  (`tests/agents_ext/test_agent_background_611.py::test_the_runners_own_soft_leg_stops_the_agent_without_the_services_escalation`). Legacy rows
  keep every one of those paths unchanged.
- **No leg signals a number that moved.** Every `kill`/`killpg` sent for an owned row on `stop`
  and on `/reload` was recorded with the child ignoring SIGTERM, so the runner's soft and hard legs
  and the service's escalation all ran; once the child was reaped its number was made to read as
  another process's, leading a group of its own. No leg signalled it or its group again
  (`tests/agents_ext/test_agent_background_ownership_611.py::test_no_signal_reaches_a_reused_pid_or_a_group_another_process_leads`, both paths). The
  evidence legs read the leader's start time before a group kill and skip a group whose recorded
  leader's number now names a process that started at another time
  (`tests/jobs/test_stop_evidence_posix_611.py::test_a_group_whose_recorded_leader_number_now_leads_another_process_gets_no_signal`):
  for the job service's callers this closes the residual ADR-0238 accepts (a number recycled
  and its new holder made a group leader). Gone is not moved: a group whose recorded leader exited
  on its own — reaped, or a zombie — still gets its `killpg` from `stop_descendants` and from the
  stop legs, with or without a tree, and the barrier confirms it empty
  (`::test_a_group_whose_recorded_leader_is_gone_is_still_killed`, real processes, POSIX); a
  tree's root that leads no group gets its own SIGKILL only while its number is still its own
  (`::test_a_root_that_leads_no_group_is_killed_only_while_its_number_is_its_own`).
- **Its group is stopped when it ends** (`normal_exit="stop_descendants"`): what the child left in
  its own process group is killed and confirmed inside the barrier, before the job's terminal
  state (`tests/agents_ext/test_agent_background_ownership_611.py::test_a_finished_agents_group_is_stopped_before_its_job_ends`, contract row 2 for this
  kind). A shutdown stops a running agent through the same barrier and records its partial
  result (`::test_a_shutdown_stops_a_running_agent_through_the_barrier`).

### 5. Records: the `job_id` link and confirmed appends

A background run's `aelix.child_session` start and settle carry `job_id`; every other run's
records are byte for byte what they were. Its opening records (start, pending) are placed once
each under an id derived from them and CONFIRMED before the launch; one that does not land refuses
the launch. Its closing records (settle, final) are not appended by `_run` but handed to the job,
which writes them confirmed BEFORE its own `cleanup` and `outcome` facts (ADR-0293 §7) — so a
restart finds the parent's settle by `job_id`
(`tests/agents_ext/test_agent_background_611.py::test_spawn_returns_once_launched_and_one_notice_comes_back`
pins the order: job start, child start, pending, process, child settle, final, cleanup, outcome).

### 6. The versioned `print_done`

A background run's child is launched with `AELIX_JOBS_PROTOCOL=1` and the job's `AELIX_JOB`
marker (`JobContext.protocol_env`, `env_marker`); every other child `PrintChannel` starts has both
removed, an inherited value included (the `RpcChannel`, which nothing in the product constructs,
removes neither; ADR-0198's note)
(`tests/agents_ext/test_agent_background_611.py::test_the_child_is_asked_for_print_done_and_a_foreground_one_is_not`,
Codex #1). A `--mode json` run with that variable writes `{"type": "aelix_print_done"}` after its
drain, before it exits — only while its OWN jobs gate is on as well, so with `ACTIVE = False` a
run's stream is what it was even when the variable was inherited — and nothing else writes it
(`tests/cli/test_print_done_protocol_611.py::test_json_mode_writes_print_done_last_when_asked`,
`::test_with_the_gate_off_an_inherited_request_changes_nothing`). For such a run only, a child
that closed its latest run and then lingered past the 30 s post-EOF bound counts as finished only
with that line
(`tests/agents_ext/test_latest_run_611.py::test_protocol_run_lingering_without_print_done_fails`,
`::test_protocol_run_lingering_after_print_done_is_finished`); a legacy run is judged as before,
with or without it (`::test_legacy_linger_is_judged_as_before_with_or_without_the_line`, Codex r5
finding 6). The line is read strictly (Codex #11): exactly the producer's `{"type": …}`, only
after the latest run closed, and a later `agent_start` takes it back
(`::test_an_earlier_runs_print_done_never_vouches_for_a_later_run`,
`::test_a_print_done_of_another_shape_is_not_the_marker`,
`::test_a_print_done_inside_an_open_run_is_not_the_marker`).

### 7. The notice

The body is the text a foreground call returns for the same envelope (`render_subagent_result`),
inside ADR-0293's frame. A body whose ESCAPED size (`jobs/notice.escape_body`, which lengthens a
forged tag or header) is over 12 KiB (`NOTICE_BODY_MAX_BYTES`) is cut at a character boundary with
the rest pointed at `agent(action="result", id=…)` and the child's session file, so the frame never
cuts it again and the fully framed notice stays within 16 KiB. Its next step — which the frame
never cuts — names both, `agent(action="result", id=…)` and the child's session by its id, and
says the job id in the header names the same agent
(`tests/agents_ext/test_agent_background_611.py::test_result_rereads_the_whole_result_and_a_long_one_points_there`,
`::test_a_cut_notice_keeps_both_pointers_in_an_uncut_footer` for a forged header, a forged tag
and plain text, Codex #12). At placement the kind adds the resume hint only when the branch then
current passes that resume's own record rules — its first run's START on it, runs that chain to
it, the model's, with valid limits (`child_session.resume_lineage_refusal`, steps 2–5 of
`find_resume_target`) — and says it cannot be resumed from here when the start is not on it
(`::test_resume_hint_checked_against_current_branch`, Codex r1 #14;
`::test_a_branch_holding_only_the_settle_gets_no_resume_hint`, Codex #9).

### 8. Conservative recovery

A child's session file records no run boundaries, so a restart that finds a job started and not
ended:
- calls it `done`, `failed` or `stopped` only from the parent's own confirmed settle for that job
  (`tests/agents_ext/test_child_recovery_611.py::test_confirmed_client_settle_wins_over_the_child_file`,
  `::test_a_settle_that_did_not_finish_is_not_done`);
- otherwise reads the child's file for its last answer, reported `recovered` — never `done`, on
  every surface: `status`, `wait` and `stop` show the state `recovered`, and `result` and the
  notice lead with "Its last answer, read from its session file after aelix restarted; aelix could
  not confirm it finished." (`jobs_client.RECOVERED_CAVEAT`), after a second restart too, when the
  file is read again under the same rules
  (`::test_child_file_never_yields_done_without_parent_settle`,
  `::test_a_restart_records_recovered_not_done_and_result_reads_it`, verify B1, Codex #4 and #6).
  It is recorded only once every process the job recorded is confirmed gone: a recorded root that
  is alive, or cannot be told gone, keeps the job `unresolved` with its supervision and its slot,
  nothing signalled, and the liveness recheck records `recovered` once the evidence says gone
  (`::test_a_live_recorded_root_is_never_recovered_and_keeps_its_slot`). It is read only when the
  run is the LATEST run of that file: a later start for the same child session anywhere in the
  parent's records makes it `unresolved(cannot_bind_run)`, and that reason survives the
  service's own reconciliation — OS evidence never turns it into `lost`
  (`::test_recovery_refuses_a_run_that_is_not_the_latest_of_its_child_file`,
  `::test_cannot_bind_run_survives_the_service`, Codex #5). Once its recorded processes are
  confirmed gone its slot comes back and the liveness loop no longer checks it
  (`JobSnapshot.rechecking` is false), so `result`, `stop` and `job result` say aelix is not
  watching it and promise no notice, and so does its restored notice when the processes were
  already gone at the restart. While a recorded process may live they say aelix keeps checking;
  if it dies later, the one notice the restart posted still said so and no second notice follows
  (verify-r3's `test_kind_unresolved_live_then_gone` probe; see Limits)
  (`::test_a_kinds_own_unresolved_with_its_processes_gone_promises_no_check_and_no_notice`,
  `::test_after_a_restart_stop_and_result_say_only_what_is_true`, verify-r2 SF2);
- leaves it to the service's OS evidence when something holds the child file's writer lock or the
  file has no answer yet (`::test_a_held_child_lock_is_left_to_os_evidence`,
  `::test_no_answer_yet_is_left_to_os_evidence`,
  `::test_a_restart_with_no_settle_and_no_answer_is_judged_by_evidence`).
The child file is derived and checked as a resume's is (ADR-0277), never read off the record, and
checked and read through ONE open descriptor — its inode still the name's, one link, its header
and origin this child's — so a file swapped in under the name before or after the check is
refused or never read (`::test_result_reads_the_file_it_checked_never_one_swapped_in`, Codex #3).
The descriptor's own checks decide two cases the name's cannot see: a descriptor opened on a copy
swapped in and out again, its header and origin intact, is refused by its inode
(`::test_a_descriptor_opened_on_a_file_swapped_out_again_is_refused`), and a second name made
after the name's check is refused by the descriptor's link count
(`::test_a_second_name_made_after_the_names_check_is_refused_by_the_descriptors`). `result` states
a refused file as a read — "Its result cannot be read: its session record is damaged (its session
file was replaced while it was read)." — not with a resume's "Resume refused: …" (the same tests,
verify-r2 N7).
`agent(action="result")` after a restart follows the same rules
(`::test_result_after_a_restart_follows_the_same_rules`), and never reads the child's file for an
ended agent with no confirmed end that no restart read as `recovered`
(`::test_result_never_reads_the_child_file_without_a_confirmed_end`).

### 9. Budget, disclosure, helpers

- **A notice turn is not a prompt.** The turn a background result starts
  (`before_agent_start.source == "notice"`) keeps the per-prompt delegation budget of the user's
  prompt that caused it; otherwise an agent's result could buy the next twelve delegations and
  theirs the next (`tests/agents_ext/test_agent_background_611.py::test_a_notice_turn_keeps_the_prompts_delegation_budget`).
- **The disclosure row** (#196) of a `spawn` under a yolo parent is written before the child exists
  and taken down when the call returns; from then on the footer's jobs segment and `/jobs` show
  the agent running, and the call's result names its posture.
- **Helpers.** On POSIX the cleanup duty answered `unverified` until #719 — a helper that left the
  agent's process group was not tracked — and the notice said so (S1's
  `test_s1_reports_helpers_unverified`, replaced 2026-11-10). *Note 2026-11-10 (#719, ADR-0296 §6):*
  it now runs the launch ledger's sweep after the barrier, inside `cleanup_bound`, and answers
  `confirmed` only when every target aelix identified is confirmed gone, no launch was in flight,
  the ledger is whole and armed, the agent cleaned up after itself or aelix's stop legs ended it,
  and on Linux it was a subreaper; otherwise `unverified` with why
  (`tests/agents_ext/test_agent_background_ownership_611.py::test_posix_helpers_confirmed_when_the_sweep_leaves_nothing`,
  `::test_posix_helpers_unverified_when_the_agent_ended_without_its_cleanup`).
  On win32 it answers `confirmed` when the barrier confirmed the child's Job empty — whether a Job
  existed is read before the close that clears it — and `unverified` without a Job; before the
  barrier confirms it answers `unverified` and closes nothing.

### 10. D14 and the cleanup interface

ADR-0276 D14's synchronous Esc report is unchanged for foreground calls; an Esc does not reach a
background agent at all. A background stop goes through the job's barrier. The runner implements
ADR-0293's cleanup-obligation interface (`register_directory`, `helper_cleanup`, the `cleanup`
fact); S2 fills the helper half with the launch ledger and the subreaper.

*Note 2026-11-10 (#719, #750, ADR-0296 §5):* D14's synchronous Esc report is still unchanged. A
foreground run now owns ONE cleanup task, created at its first end — the cancel handler on Esc —
and published to the runtime's registry in the same synchronous step; Esc does not wait for it,
and `stop`, `stop_all` and the CLI's last drain join it. A service-owned row gets none: its sweep is
`helper_cleanup`'s, as above.

## Consequences

- Delegation plugs into the job service through kind-agnostic additions (the AST scan,
  `tests/jobs/test_core_is_kind_agnostic_611.py`, stays green): `JobContext.register_process`
  returns the record it registered; `ExtensionAPI.jobs` (the facade) gained `reserve`, a
  `reservation=` on `start`, `changed`, `snapshot`/`snapshots` and a `reason=`/`wait=` on `stop`;
  and fix round 1 (2026-11-09) changed the service's restart reading (dated note on ADR-0293):
  `recovered` is a job state of its own, terminal and never shown as `done`, recorded only once
  every recorded process is confirmed gone; a kind's own `unresolved` keeps its reason and OS
  evidence never makes it `lost`; the liveness recheck asks the kind again once the evidence says
  gone (`tests/jobs/test_restart_611.py::test_a_kinds_reading_is_recovered_never_done_and_waits_for_its_process`,
  `::test_a_kinds_own_unresolved_reason_survives_os_evidence`,
  `::test_a_kinds_own_unresolved_with_a_live_process_holds_its_slot_and_reason`); fix round 2
  (2026-11-10) added `JobSnapshot.rechecking`, and a restored `unresolved` notice says "it keeps
  checking" only while the liveness loop does
  (`::test_live_match_is_unresolved_not_lost`, `::test_a_kinds_own_unresolved_reason_survives_os_evidence`). Also generic:
  `KindCapabilities.noun` for the admission refusal; `JOB_MARKER_ENV` and `JOB_INTERNAL_ENV` in
  `jobs/contract.py`; and the evidence legs' leader check before a group kill (`ProcessTree.
  stop_and_collect(root_started=…)`, §4).
- `AgentCall` gained `timeout` and `offset`; `PendingSpawn` a `reservation`; `SubagentHost` a
  `reserve`; `SpawnReceipt` a `job_id` and a `writer`; `_StreamState` a `print_done`;
  `build_result` a `print_done_required`; `reap` an `owner`. Each defaults to what every caller had.

## Limits and what S1c does not do

- **Not visible**: `ACTIVE` stays `False`; S3 flips it.
- **Windows**: the real Job-object evidence legs a background child's barrier runs are proven
  against an injected api only (ADR-0293 §Limits); the tests that start a real background child
  are POSIX-only until S3's Windows acceptance.
- **Foreground `run`/`resume`** stay on today's path (S3 makes a single-task run a detachable
  job). A background `resume` (D16), `send` (#123), RPC/ACP background actions (#612) and the
  rich panel are not here.
- The spawn's consent dialog is the single-`run` dialog; it does not say "in the background".
- `wait` gives a finished agent's notice up to 2 s to be posted; a notice posted later is placed
  at a later boundary.
- **The gate-off tests the contract lists for rewrite stay as they are until the S3 gate flip**
  (verify S5): `tests/agents_ext/test_print_channel_spawn.py::test_background_true_is_rejected_in_p2`,
  `tests/agents_ext/test_tool_and_security.py::test_background_true_is_rejected_always`, the
  resume refusal of `background`
  (`tests/agents_ext/test_agent_resume_tool_610.py::test_a_resume_refuses_every_key_that_would_make_it_another_run`)
  and the description budget tests. With `ACTIVE = False` each still pins what a user meets today:
  the tool refuses `background` with ADR-0197's sentence, and the seam's `spawn(background=True)`
  — the door `/agents run` and RPC `agents/run` use — still refuses
  (`runtime._BACKGROUND_REJECTED`, ADR-0197's note); the background paragraph has its own budget
  test. S3 rewrites them when it turns the gate on.
- **A restart stops nothing it finds.** An agent the restart found unfinished keeps running until
  it ends on its own; `stop` cannot reach it from the new process.
- **Helpers** on POSIX were `unverified` until S2; since #719 they are swept and confirmed or
  `unverified` with why (§9's note), and ADR-0296's residuals stand, the macOS ones among them.
- **Restored and unresolved texts still overstate in four shapes** (verify-r3, all behind the
  gate; to be closed before S3 flips it). *Closed 2026-11-12 (#749): the note below.*
  - a kind's own `unresolved(cannot_bind_run)` with a live process promises that what aelix can
    read of its end comes as a notice, but that read is always empty and no notice comes; and the
    restart's one notice keeps saying "it keeps checking" after the process dies (§8);
  - with `rechecking` false the texts drop "it may still be running" even for `recover_error`,
    `relation_error`, `adapter_*` and `unreadable`, which were never checked against the OS;
  - a `settle_error` job this process runs has a notice saying "It keeps checking" while the
    liveness loop skips it (ADR-0293's text since #722; delegation's settle cannot raise, so only
    a timeout reaches it);
  - a refusal code can reach the model raw ("Its result cannot be read: no_answer.").
- **A transient read failure during a recheck** leaves the job `unresolved(unreadable)` with no
  recheck, and its slot stays held for the rest of the process (verify-r3's
  `test_unreadable_on_recheck` probe; the same on `fa742f77`). *Closed 2026-11-12 (#749): the
  note below.*
- **An answer with a lone surrogate** (an unpaired UTF-16 half in the agent's text) still breaks
  its job, as on `5390b254`: the job's drive task dies with `UnicodeEncodeError` in the notice's
  brief (`jobs/notice.cut_brief`), so no `outcome` fact is written and no notice is posted, and
  `agent(action="result")` raises the same error in `jobs_client.page`. The result file refuses
  such a text (the note below), which no longer hides this as `c06128c8`'s failed write did
  (verify-r3 F3, its `test_zz_v3_surrogate.py` probe). A follow-up issue, which the lead drafts,
  carries it; it is behind the gate like the rest. *Closed 2026-11-12 (#782): the note of that
  date below.*

## Note (2026-11-12, #749, #770, #769)

The job-service residuals closed before #611 S3 opens the gate. The owner chose to do them first
on 2026-11-12, selecting the option labelled "S3 전 정리 3개 먼저 (권장)" ("the three clean-ups
before S3 first (recommended)"). All of it is behind `ACTIVE = False`. Each test below fails on
`363accf2` unless it is named a guard; fix round 1 of #769's lane (the same date) added the ones
that also name `5390b254`, the lane's first head, which each fails.

**The four shapes of the Limits, and two more of the same kind (#749).** Whether an agent may
still run is now read from `JobSnapshot.processes_gone` (ADR-0293's note), not from `rechecking`;
whether a notice can come, from `rechecking` and the agent's reason.
- A kind's own `unresolved(cannot_bind_run)` with a live process: `result`, `stop` and `job
  result` say it may still be running, that aelix checks its processes until they are gone, and
  that a later run of this agent wrote to the same session file, so aelix cannot read this run's
  end from it and no notice is promised; the restored notice says "it checks the job's processes
  until they are gone, and promises no later notice". Once the process is gone the texts say its
  processes are gone and aelix is not watching it, and no second notice comes
  (`tests/agents_ext/test_restored_texts_749.py::test_cannot_bind_run_with_a_live_process_promises_no_notice`).
- With `rechecking` false and nothing checked against the OS — `recover_error`,
  `relation_error`, `adapter_too_old` — they say it may still be running
  (`::test_a_state_never_checked_against_the_os_still_says_it_may_be_running`). `adapter_missing`
  and `unreadable` take the same branch (`processes_gone` false); that test does not reach them.
- An agent this process runs whose runner's settle failed (`settle_error`, which a stop-legs
  timeout alone reaches) is not rechecked: `result` and `stop` say aelix cannot tell how it ended,
  does not check it again and no notice of its end is coming, not "its result arrives as a
  notice" (`::test_a_settle_error_agent_promises_no_notice`). The service's notice says the same
  (ADR-0293's note). `job(action="result")` and `/jobs result` of an agent that has no result
  yet now give the same state-aware sentence as `result` — they said "It is still running; its
  result arrives as a notice." in every such state, `settle_error` included
  (`::test_job_result_of_a_settle_error_agent_with_no_text_promises_no_notice`, fails on
  `5390b254`; verify-r1 F1, Codex's finding 6): "It is running; its result arrives as a notice
  when it finishes." for a running agent, and for an `unresolved` one aelix rechecks — a failed
  cleanup, retried — "It is unresolved; its result arrives as a notice when it finishes."
  (`::test_a_running_agent_with_no_text_is_said_to_be_running_on_every_result_surface`,
  `::test_an_unresolved_agent_that_is_rechecked_is_said_to_come_back_as_a_notice`, the second a
  guard for `result` and `stop`).
- `result` says why it read no answer in a sentence, never the code: "Its result cannot be read:
  its session file holds no answer from it." (`::test_no_answer_reads_as_a_sentence`); every code
  `recover_run_answer` returns has one (`::test_every_code_recover_run_answer_returns_has_a_sentence`,
  an AST scan of its `return`s that finds the seven), and `cannot_bind_run` reads "a later run of
  this agent wrote to the same session file, so its last answer there may be that run's"
  (`tests/agents_ext/test_child_recovery_611.py::test_result_after_a_restart_follows_the_same_rules`).
- A failed read of the parent's records during a recheck, its process gone: its slot comes back
  at once and the recheck stays on; meanwhile `result` says its processes are gone and aelix reads
  its records again; the next pass records it `lost` with its notice
  (`::test_a_failed_read_during_a_recheck_gives_the_slot_back_and_says_what_is_true`). For
  `cannot_bind_run` in that window the texts say its processes are gone and do not say aelix
  checks them (`::test_cannot_bind_run_whose_process_is_gone_and_read_failed_does_not_say_aelix_checks_it`,
  a guard on `5390b254`; on `363accf2` it fails, there being no `processes_gone`).
- verify-r3's surviving mutants: `_READ_REFUSALS`' three rows and the `os.open` failure branch of
  `_checked_answer` are each pinned by an exact text — a file gone, forked, never started
  (`::test_a_gone_child_file_reads_as_gone`, `::test_a_forked_child_file_reads_as_the_other_conversations`,
  `::test_a_child_that_never_started_reads_so`, guards that pass on `363accf2`); `rechecking` of a
  job this process runs is read by `result` and `stop` and pinned both ways
  (`tests/jobs/test_residuals_749.py::test_a_settle_error_job_says_nothing_checks_it_again`, and
  `::test_a_cleanup_error_job_is_rechecked_and_says_so`, a guard that passes on `363accf2`).

**Paging by bytes (#770).** `jobs_client.page` sets `OutputSlice.next_offset` to the byte after
the page, by ADR-0297 §4's rules: a page with bytes after its offset takes at least one character,
whole, when its limit is smaller; it never ends inside a character; `more` is false at the end
(`tests/jobs/test_delegation_paging_770.py::test_page_takes_whole_characters_and_names_the_next_byte`).
So `job(action="result")` and `/jobs result`, which pass `continue_at` back, read a multibyte
result to its end with every byte once, as `agent(action="result")` already did
(`::test_the_job_tool_reads_a_multibyte_result_to_its_end_each_byte_once`,
`::test_jobs_result_and_agent_result_read_the_same_pages`). Before, the hint was `offset +
len(text)` — characters — and each page after the first began inside bytes already read. The
guard `test_delegation_paging_is_unchanged` that pinned the old hint is now
`tests/jobs/test_client_start_611.py::test_delegation_pages_by_bytes`.

**An agent the job service lets go, and its result file (#769).** The owner decided on
2026-11-12, selecting the option labelled "결과를 파일로 저장 (권장)" ("save the result to a file
(recommended)"), that a background agent's whole result goes to a file and is read from there once
the job is let go, as `bash`'s output file is.
- *The file.* When a background run that launched returns, its whole result — the text a
  foreground call would have returned — is written to `<children dir>/<run key>.result`: the
  directory its parent's child session files live in (§8's `place_children`) and the run's own
  spawn key, so a resume of the same child file gets a file of its own. It is created 0600
  through a temporary file and `os.replace`, never through a link, and the run records
  `aelix.agent_result` `{v, key, bytes, sha256}` with its job's other records
  (`aelix_agents/result_file.py`;
  `tests/agents_ext/test_agent_result_file_769.py::test_it_is_this_users_alone`, POSIX,
  `::test_it_is_written_whole_and_read_back`, `::test_a_write_that_fails_leaves_nothing_and_says_none`).
  A run with no child session file — a parent outside a sessions bucket, or with no session file —
  writes none, and such an agent stays whole: a run's prompt directory is the job's own and is
  removed at its end, so it cannot hold the file (this lane's call).
- *Its lifetime.* It lives with the session, as the child session file does: nothing in aelix
  removes it, and removing the session's children directory removes it.
- *Reading it.* Its path is derived from the parent's session file and the recorded key, never
  read off the record (§8's rule for the child file), and it is read through one descriptor: not
  a link, one name, the size and sha256 the run recorded
  (`::test_a_file_that_is_gone_or_changed_is_said_so`, `::test_a_link_in_its_place_is_never_followed`,
  POSIX). `job(action="result")`, `/jobs result` and `agent(action="result")` read the live
  result while the job service keeps the job whole, and the file once it is let go — all three at
  once, before any garbage collection frees the runner that held the text
  (`::test_result_reads_an_agent_let_go_from_its_file_before_any_collection`, collection off;
  verify-r2 F7). The runner sits in reference cycles, so it and its text go at the next
  collection. `DelegationJobs.info` is not bounded: it keeps one entry per background agent this
  process ran and never removes one, but an entry is small — its ids, key, profile, session path
  and status, no task and no text (`::test_the_clients_entry_of_an_agent_let_go_is_small`: under
  1,000 characters for a 20,000-character task; verify-r2 F3). The delegation kind declares
  `reads_persisted` (ADR-0293's note). All three read every page byte for byte
  as before, and `status`, `stop`, `wait` and the generic surfaces the same text
  (`tests/agents_ext/test_agent_retention_769.py::test_an_agent_let_go_reads_as_before_from_its_file_and_holds_no_text`,
  POSIX, a stub child, a 45 KB multibyte result: weak reference to the runner dead after
  `gc.collect()`). A file that is gone or changed is said — "Its result cannot be read: its result
  file no longer exists." — on all three, never replaced by the child's last answer
  (`::test_a_missing_result_file_is_said_and_never_replaced_by_the_childs_answer`). A run whose file
  could not be written is kept whole: its read-back could not match (ADR-0293's comparison;
  `::test_a_run_whose_result_file_could_not_be_written_is_kept_whole`).
- *After a restart.* `agent(action="result")` of an agent that settled and wrote a file reads
  its whole result from it, no longer the child's last answer under "Its last answer, read from
  its session file after a restart" (`::test_after_a_restart_result_reads_the_runs_file`). So do
  `job(action="result")` and `/jobs result`, which said "This agent's result is not held in memory
  after a restart" before (the same test; verify-r2 F5). An agent whose run wrote no file reads as
  §8 says.
- *The file itself.* A text that cannot be encoded (a lone surrogate) writes no file
  (`tests/agents_ext/test_agent_result_file_769.py::test_a_text_that_cannot_be_encoded_writes_nothing_and_says_none`;
  verify-r2 F4) — though the job itself still breaks on such a text elsewhere (Limits; since
  #782 the delegation client hands it none, the note below). The file is opened without waiting, so a FIFO in its place is refused at once as
  not a regular file (`::test_a_fifo_in_its_place_is_refused_at_once`, POSIX; verify-r2 F8). The
  child-session reader (§8) opens with the same flags as before and still waits on a FIFO; this
  lane did not touch it.

## Note (2026-11-12, #782)

**A lone surrogate in a background agent's answer (Limits).** A lone surrogate (`"\ud83d"`, half
an emoji) is a legal `str` that a child's JSON stream and its session file carry, and no UTF-8
encoder writes it. Measured on `52bc607a` by this issue's lane: a FOREGROUND agent keeps it in the
`agent` tool's result and nothing raises, and a JSON round trip of that result (`ensure_ascii`, as
the session file writes it) gives it back equal; through the gated launcher (#777) against a
stand-in OpenAI-compatible server, the parent model's tool result read `before  after` for a
child that answered `before \ud83d after` — the openai-completions adapter drops it
(`providers/_sanitize_unicode.py`, pi's `sanitizeSurrogates`; the Google adapter calls it too, read
from the code, not measured). The Anthropic adapter does not: the round-1 verify measured a
foreground tool result holding `\ud83d` end the parent's turn with `UnicodeEncodeError` before any
request is sent, the same on `52bc607a` and after this change (its
`probes/p782_anthropic_fg.py`); that is a separate, pre-existing defect, for a follow-up issue
the lead drafts, and this note does not fix it. A BACKGROUND agent's text is cut by UTF-8
bytes for the notice's brief and body and paged by bytes, and its result file refused it, so its
job's drive task died in `jobs/notice.cut_brief` with no `outcome` fact and no notice, and
`agent(action="result")` raised in `jobs_client.page`; through the launcher, `-p` printed the
traceback, exited 0 and never gave the parent model a notice turn.

Now the delegation client replaces each lone surrogate with U+FFFD where a child's text enters it:
the run's rendered result and its brief, its progress (the tool it runs and its last line), and
an answer read from the child's file after a restart — by `recover` and by `result`
(`jobs_client._utf8_safe`). Everything downstream — the brief and the notice, rows, paging, the
result file — gets text it can encode, so the job layer is unchanged. Replaced, not dropped, as
`sendable()` replaces it in a chain step's `{previous}` (ADR-0277), so the reader sees that a
character was there; a foreground agent's model on openai-completions reads nothing in its place.
Through the launcher the same child's background run now ends in a notice turn whose notice reads
`before \ufffd after`. The narrowest place that makes every surface work, and this lane's call.

A tool NAME holding one is a second path, found by Codex in the first round (C3): the envelope's
tool-trail byte count (`envelope.render_tool_trail`) encoded strictly and raised, before the result
text reached `_utf8_safe`, so such a background job ended `failed` ("client error:
UnicodeEncodeError") and a foreground call raised the same error. The count now uses
`surrogatepass`, as every other byte count in `envelope.py` does, so the foreground call returns
its envelope as for any other name.

On `52bc607a` (these tests with `_utf8_safe` stubbed as the identity, as the file cannot import
it there) the first two cases wait for an outcome and a notice that never come; the restart case
gets a notice, but the service's generic fallback, because the kind's `render_notice` raised; the
settled-run case raises in `page`; the helper guard fails. The two tool-name cases fail on
`639dcdf6`, this change's first round.

- The job ends `done` with one `outcome` fact and exactly one notice, its brief and body carrying
  U+FFFD (`tests/agents_ext/test_agent_lone_surrogate_782.py::test_the_job_ends_with_its_outcome_and_exactly_one_notice`, POSIX).
- `agent(action="result")`, `job(action="result")` and `/jobs result` page the same text, each
  byte once, over a 50 KB answer with a half on every line; the result file holds that text byte
  for byte, and after the job service lets the job go the three read it from the file page for
  page as before (`::test_every_result_surface_reads_the_same_text_and_its_file_byte_for_byte_after_a_let_go`, POSIX).
- After a restart, an answer recovered from the child's file reaches its notice and `result`
  (`::test_a_restart_that_recovers_the_answer_from_the_childs_file_gives_it_once`), and `result`
  of a settled run that wrote no file reads the child's last answer
  (`::test_result_after_a_restart_reads_a_settled_runs_answer_from_the_childs_file`).
- A tool name and a progress line holding one read U+FFFD in the job's progress and its row, and
  the job ends `done` with one notice
  (`::test_a_tool_name_and_a_progress_line_with_a_half_are_whole_and_the_job_ends_done`, POSIX);
  a foreground call whose child names such a tool returns its result
  (`::test_a_foreground_call_whose_child_names_a_tool_with_a_half_returns`, POSIX). A progress
  update with no tool or no line yet keeps `None` there
  (`::test_progress_with_no_tool_or_no_line_yet_reads_none`, POSIX), and the trail's budget is
  UTF-8 bytes, a half counted as its three
  (`::test_the_trail_is_bounded_by_utf8_bytes_with_a_half_counted_as_three`); both pass on
  `8f07404f`, the second round, and pin its lines.
- Only lone surrogates change: NUL, controls, non-BMP characters and U+FFFD itself are kept
  (`::test_only_lone_surrogates_are_replaced`).
- *Not changed.* The kind-agnostic job layer still encodes what any kind hands it: an extension
  kind whose brief or notice body holds a lone surrogate is not covered by this change (not
  measured).
