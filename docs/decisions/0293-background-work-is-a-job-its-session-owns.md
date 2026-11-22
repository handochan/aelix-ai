# 0293. Background work is a job its session owns: product-core runs it, the kernel delivers its notice once, clients render it

Status: Accepted (2026-11-08, #722). Amended 2026-11-12 (#769, #749, #770): ended jobs past a
per-session bound are kept only as their records read; `JobSnapshot.processes_gone`; see the note
of that date. Amended 2026-11-12 (#778): `backgroundJobs.enabled: false` in the global settings
turns §12's gate off for a launch; see the note at the end. Noted 2026-11-17 (#776, ADR-0299):
`/jobs` opens a panel in the TUI and `/jobs list` is §10's table. **Active since #611 S3 (#779,
2026-11-17):** `aelix_coding_agent/jobs/activation.py` `ACTIVE = True`; in the kernel
`AgentHarnessOptions.notice_inbox` stays off by default and product-core turns it on while
`ACTIVE` (§12). Until then it was inert behind `ACTIVE = False`; see the note of 2026-11-17 (#779)
at the end. Amended 2026-11-19 (#810, #820, #821, #822, #825, #827): a lone surrogate never ends
a job's notice, cut briefs end at a word, a kind may name its jobs by ids of its own
(`JobIdAdapter`) and word a control on an ended job (`JobEndedControlAdapter`), and a restart
shows the run time an outcome recorded; see the note of that date.
Amended 2026-11-19 (#612, ADR-0307): `job` and `job_stop` are registered on RPC (and so under ACP)
too, and a start or a Ctrl+B adoption that its owner's end overtook is refused with a `failed`
outcome (the ended-owner epoch); see the note "(2026-11-19, ADR-0307, #612)".
Amended 2026-11-22 (#848, PR #862): the panel's `m` on an ended job asks the kind's sentence
for a message typed with no text first (`JobEndedMessageAdapter`), so an agent's names no text to
resume with; see the note of that date.
Noted 2026-11-22 (#847): after a restart the agent kind names a spawned run read back unresolved
or lost by the agent id and run key its run's own start record holds, at that restart and every
later one; see the note of that date.
Date: 2026-11-08
Issue: #722 (#611 slice S1b). Parent: #611. Design: `.omc/specs/611-design-r6-s1-contract-2026-11-07.md`
(round 6, the S1 contract), with rounds 3–5 and Codex's reviews of rounds 1, 3 and 5 beside it.
Owner decisions (2026-11-07), as recorded in #611's body: background execution is a GENERIC core
facility — the kernel holds the notice inbox and the wake boundary, product-core holds the job
service, and delegation, `bash` and extensions are its clients; waiting for the user means an
open dialog only, and a notice never closes or answers one; `-p` keeps the first failure's exit
code within one user prompt and the notice turns it caused (ADR-0268 is unchanged across user
messages); `/reload` stops background jobs, and a restart never kills — it marks a job lost or
`unresolved` and notifies; admission bounds are configurable (OQ6); a restored notice never
starts a TUI turn (OQ7); monitoring belongs in S1, in core ("코어 메인세션에서 백그라운드
잡을 모니터링하는게 혹은 확인하는 게 더 중요").
Amends: ADR-0008 (§13, the review gate replaced), ADR-0199 (d), ADR-0268 — each by a dated note.
Notes on: ADR-0198, ADR-0242, ADR-0243, ADR-0286.
pi: `earendil-works/pi` `origin/main`, `packages/durable/src/harness/inbox.ts` (a kernel inbox
placed at the `postTools` and `final` boundaries) and the durable background-subagent example.

## Context

#604 removed every delegation timeout and decided that `-p` waits for background children;
#611 asks for background delegation that the parent does not wait for, whose result comes back
exactly once. ADR-0197 forbade it because a background child had no owner to reap it and no
path to report through. Round 3 of the design moved the owner from a delegation-specific
supervisor to a generic one, and the owner chose that layering on 2026-11-07: delegation is
client 1, background `bash` client 2 (S2), an extension's work client 3. This ADR is the generic
mechanism (S1b); the delegation client is ADR-D (S1c, #723).

## Decision

### 1. Bands

- **Kernel** (`aelix_agent_core/harness/inbox.py`, inbox methods on `AgentHarness`): a
  kind-agnostic notice inbox, deterministic placement, a bounded drain per boundary,
  `prompt_notices` (a turn with `before_agent_start.source = "notice"`), and nothing else — and
  only on a harness built with `notice_inbox=True` (§12).
- **Product-core** (`aelix_coding_agent/jobs/`): the job service — kinds, states, the barrier,
  records, notices' frame, admission, liveness, reconciliation — and its surfaces: the `job`
  tool, `/jobs`, the footer segment, the print drain and the TUI waker.
- **Clients** register a kind: an adapter (how its records read and render) and a runner (what
  it does). No line of the kernel inbox, the service, the drain or the TUI jobs code names a
  kind: `tests/jobs/test_core_is_kind_agnostic_611.py` scans them (no `aelix_agents`,
  `aelix_coding_agent.agents` or `tools.bash` import, static or through `importlib`; no kind-name
  or client-wording string, also when put together from literals; no comparison of a kind with a
  string or an imported constant, no prefix test on one, no `isinstance` or `Literal`/`match` over
  kinds; no `hasattr`/`getattr` on a runner, adapter or registration; no consent symbol, no child
  argv) and proves each rule fires on a probe, verify N1's six evasions among them;
  `tests/jobs/test_synthetic_kind_611.py` runs a third kind, `test:sleeper`, registered by an
  ordinary extension, through every surface.

### 2. Kinds by name and version

A kind registers under its name and an integer version; a re-registration (an extension's
`setup` after `/reload`) is a new generation and replaces the old one. A job keeps the runner it
started with; its notice is rendered by the adapter registered at the end when there is one, and
from the body stored with its outcome when there is not
(`tests/jobs/test_generations_611.py::test_old_generation_notice_rendered_by_current_adapter_or_stored_body`).
A record whose kind is missing, or registered at a lower version than the record's, is never
interpreted: it stays `unresolved(adapter_missing|adapter_too_old)` — holding a slot and with a
restored notice saying so, nothing of the kind's own rendering made up (§6) — and is re-evaluated
when the kind registers (`::test_missing_adapter_is_unresolved_and_reevaluated_on_registration`).
A kind's name belongs to the source that registered it first (an extension's name; `""` for
product-core) for the life of the process: another source registering it is refused
(`KindTaken`). A registration made through an extension's API lives as long as that API's runtime:
once a reload or a session change invalidates it, no new job starts on it until the extension
registers again, while running jobs keep theirs. The extension facade checks the gate and that
lifetime on every call and acts only on the kinds its own extension registered
(`tests/jobs/test_facade_611.py::test_another_extension_cannot_take_a_kind_and_a_stale_facade_refuses`,
Codex #18).

### 3. Contracts and the failure table

`jobs/contract.py`. An adapter supplies `decode`, `render_notice`, `finalize_at_placement`,
`render_rows`, `output`, `recover`, `relation` and declares `KindCapabilities` (default bound,
settings key, whether `-p` waits for it, whether it notifies, its normal-exit policy, whether it
reports activity, whether the `job` tool may control it and the hint when not). A runner supplies
`run`, `request_stop`, `settle`, `helper_cleanup`, `records`. A runner keeps only its context and
what it registered: it must not call an `ExtensionAPI` or a context after `run` began, because a
`/reload` invalidates them while it may still be finishing. That is a rule for the client; the
service cannot enforce it in-process. What the service enforces is the facade's side: a facade
kept past the invalidation refuses every call (`tests/jobs/test_facade_611.py`), and a job a reload
stops finishes on its runner alone
(`tests/jobs/test_reload_unresolved_611.py::test_old_runner_never_calls_an_invalidated_api`).
`decode` reads a restart's ended job from the outcome fact joined with the kind's own records
(`{"fact": …, "records": […]}`), so a result the run left in its records is read back after a
restart (`tests/jobs/test_restart_611.py::test_a_restored_result_is_read_through_its_own_records_after_two_restarts`,
Codex #12).
`register_process` is sealed once `run` returns; a registration after the stop latch is killed at
once; `launched()` or `launch_failed()` comes before `start` returns, and a failed launch is the
starting call's error with no notice (`tests/jobs/test_runner_contract_611.py`,
`tests/jobs/test_eligibility_611.py`).

What the service does when an operation raises or times out — one case per operation in
`tests/jobs/test_adapter_failures_611.py::test_failure_transitions`. A cancel of a job's task while
its `settle` hangs is re-raised (`::test_cancellation_is_reraised_from_every_operation`; that test
cancels in `settle` only — the other operations' handlers re-raise `CancelledError` by code, not by
a test each):

| Operation | Transition |
|---|---|
| `run` | `failed` ("client error: <type>") after the barrier; before its launch, the start's error |
| `request_stop` | the service's own kill legs from the registered evidence, then the barrier |
| `request_stop` returns but `run` does not end | after the stop grace the service's own kill legs, the grace again, then `run` is cancelled and the barrier decides: `stopped`, or `unresolved` (`tests/jobs/test_reload_unresolved_611.py::test_a_reload_stops_a_runner_that_ignores_the_stop`, Codex #6) |
| `settle` | `unresolved(settle_error)`, slot kept — never `failed`, `stopped` or `lost` |
| `helper_cleanup` raises, times out or answers nothing | `cleanup.helpers = unverified`, `unresolved(cleanup_error)`, slot kept; the duty is retried on the outbox's schedule and the job completes with its run's outcome once it answers (`tests/jobs/test_reload_unresolved_611.py::test_a_failed_cleanup_is_retried_and_the_job_then_completes`, Codex #7) |
| `helper_cleanup` returns `Unverified` | the job completes; `cleanup.helpers = unverified` and the notice says so |
| `records` | no client records; outcome `client_records: "missing"` |
| `decode` | the stored body and brief stand |
| `recover`, `relation` | `unresolved(recover_error|relation_error)`, a slot held and a restored notice (§6) |
| `render_notice` | a generic body: "the job ended with status X; its client could not render a result" |
| `finalize_at_placement` | no extra line |
| `render_rows` | one generic row |
| `output` | "could not read the result: <type>" |

### 4. States and the barrier

`starting → running → (stopping) → done | failed | stopped`; `lost` only from a restart's
evidence; `unresolved` when the barrier cannot confirm the processes gone, `settle` or
`helper_cleanup` failed, or a restart cannot tell. A terminal state is recorded only after the barrier passed: the runner
settled, every registered root reaped, every process group empty, every service-owned tree's
members waited on (`tests/jobs/test_barrier_611.py::test_no_terminal_before_root_exit_after_cancel`,
Codex r1 #1). The slot is released at that moment, not when the notice goes
(`tests/jobs/test_admission_611.py::test_slot_released_on_cleanup_even_when_record_append_fails`).
A process a job registered with neither a handle nor a group is signalled only while its start
time still matches the one recorded (`jobs/evidence.py`). A stop the runner does not honour is
finished by the service (the table above); an `unresolved` job whose run has not returned gets its
kill legs again from the liveness loop and from `/jobs stop`. On process exit `shutdown` cancels a
runner as the service's own stop, so the cancelled run still goes through its barrier.
`start` holds its reservation until the job exists and its finalizer owns it: a refusal, a raise
or a cancel before that gives the slot back
(`tests/jobs/test_admission_611.py::test_a_start_cancelled_before_its_job_exists_gives_its_slot_back`,
Codex #15).

### 5. The evidence primitives

`ProcessTree.collect(policy, prior=…)` (the evidence alone), `ProcessTree.stop_and_collect(policy,
prior=…)` (the evidence, then the kill; both synchronous, never raising) and
`ProcessTree.confirm(receipt, deadline)`, used only for service-owned trees. **Every path that
signals a service-owned process collects first** — the soft leg (`CTRL_BREAK` or `SIGTERM`), the
hard leg, the kill of a process registered after the stop latch, and the normal-exit stop of what
a root left — and a later collection keeps what an earlier one held (`prior`): a member opened
before `CTRL_BREAK` stays in the receipt after the listing empties
(`tests/jobs/test_win32_stop_evidence_611.py::test_a_registration_after_the_stop_latch_collects_before_it_kills`,
`::test_a_soft_stop_collects_before_ctrl_break`, Codex #3). **win32**, in
codex-rs `terminate_and_wait`'s order: open every member the Job lists and keep it only when
`IsProcessInJob` confirms it, and only then kill (`taskkill /T /F` while the root lives,
`TerminateProcess` per handle); `confirm` waits on each handle, rescans the Job and collects any
newcomer, and calls `TerminateJobObject` last. A failed member query or open, or the deadline, is
`Unconfirmed`; an empty listing after a kill is never confirmation
(`tests/jobs/test_win32_stop_evidence_611.py`, Codex r5 #1–#2 and r3 #1, driven through an injected
`_Win32EvidenceApi`). `stop_descendants` is the normal-exit policy: the root is gone, and what is
left in the tree is collected and stopped inside the barrier. **POSIX**: the root's descendants
(`aelix_ai/utils/_process_walk.py`: `/proc` on Linux, libproc on macOS — moved down from
`aelix_agents/reaper.py`, which re-exports it and keeps its own names as its tests' seams), with
their start times and the groups they and the root lead, read at the soft leg while the root
lives; then SIGKILL deepest first — a pid walked by an earlier collection only while it is still
the process walked — and per group; `confirm` waits for every group to answer `ESRCH` and every
walked pid to be gone. A record with no tree (a bare pid or group) is walked the same way. A real
root whose helper starts a session of its own is stopped with its helper
(`tests/jobs/test_stop_evidence_posix_611.py::test_a_descendant_in_its_own_group_is_walked_and_stopped`,
both forms, Codex #4). The identity rule, with real processes: an earlier walk's pid that now
names another process gets no signal of its own, with a tree or without one, also when that
process leads a group of its own — the hard leg reads again the groups only of the earlier walk's
pids whose start time is unchanged (`::test_a_pid_walked_earlier_that_now_names_another_process_gets_no_signal`;
`::test_an_earlier_walks_pid_still_that_process_has_the_group_it_leads_read_again`;
`::test_a_later_collection_reads_groups_only_for_earlier_pids_still_the_process_walked`); one
that is still the process walked is killed by its own SIGKILL where no group reaches it
(`::test_a_pid_walked_earlier_that_is_still_that_process_is_killed_on_its_own`); and a later
collection keeps the start time an earlier one recorded
(`::test_a_later_collection_keeps_the_stamp_of_the_process_it_walked`, verify SF2).
The rule does not cover a group the earlier collection already recorded: such a group is killed
whole at the hard leg, so a number that led a recorded group, emptied, and was reused by a new group
leader is signalled with that group (measured in the last review of #722 with the reuse simulated by
the start-time stamp, macOS and Linux; already so before that round). Guarding recorded groups by
their leader's stamp is a follow-up, not this commit. Start times
come from `aelix_ai/utils/_process_identity.py` (Linux `/proc/<pid>/stat` field 22, macOS
`proc_pidinfo(PROC_PIDTBSDINFO)`, Windows `GetProcessTimes`).

### 6. Report-only restart

`JobService.bind` folds the owning session's facts once per owner and process. A job with an
outcome and an undelivered notice has its notice posted (restored, §11). A job started and not
ended is recovered by its kind from confirmed client records first
(`tests/jobs/test_records_fold_611.py::test_confirmed_client_settle_wins_over_child_file_and_evidence`),
otherwise classified from OS evidence: a recorded process alive with the same start time is
`unresolved` — also when its command line no longer shows the job's markers, which an `exec`
changes and which proves nothing gone (`tests/jobs/test_stop_evidence_posix_611.py::test_a_live_process_whose_command_line_changed_is_unresolved_not_lost`,
Codex #20); only when every recorded process is gone, or its number now belongs to a process that
started at another time, is the job `lost`; anything unreadable, another host, or no evidence is
`unresolved`. A job that registered no process is judged by the aelix process that ran it.
**Nothing found at start-up is ever signalled** (`tests/jobs/test_restart_611.py::test_restart_never_signals_anything`,
with `os.kill`/`os.killpg` trapped; `::test_live_match_is_unresolved_not_lost`,
`::test_forged_process_record_never_signals`, `::test_free_child_lock_with_live_child_is_unresolved`).
A restored `unresolved` job is an obligation (Codex #11): it holds a slot of its kind until it is
settled (`::test_a_restored_live_job_counts_against_its_kinds_bound`), the model is told through a
restored notice under the job's `…:unresolved` id (no TUI turn; posted once — a delivered one is
not posted again), and when its state came from OS evidence the liveness loop classifies it again,
report-only, so a process that has since gone makes it `lost` and frees the slot
(`::test_live_match_is_unresolved_not_lost`, verify N5). A restored `lost` or recovered job with no
`cleanup` fact, or an `unverified` one, says its helpers were not confirmed stopped
(`::test_a_restored_lost_job_without_a_cleanup_fact_says_its_helpers_were_not_confirmed`).

### 7. Facts, fold and write order

`aelix.job` custom entries, `v: 1`: `start`, `process`, `cleanup`, `supervision`, `outcome`
(`jobs/records.py`). Each record — a fact or a client's own record — is placed confirmed under an
entry id derived from its `fid` (`jr-` + 16 hex of its sha256) through the store's idempotent
`place_entry` (`Session.place_custom_entry`, ADR-0242 rule 5): an unknown outcome (#721) is settled
under the store's lock and adopted when it landed, so a retry — at once, or by the outbox later,
after a settle that itself failed — never writes a fact twice
(`tests/jobs/test_records_fold_611.py::test_a_retried_record_lands_once_after_a_failed_settle`,
Codex #13). The fold reads in file order: it groups by `job_id`, takes identity from `start` and
ignores a fact that comes before its `start` or has none, accumulates `process` by `seq`, keeps the
first valid `outcome` — one with an unknown status or a malformed fact is ignored — and ignores a
later conflicting one and an unknown `v`; a `start` of an unknown `v` or shape makes the job
unreadable (`tests/jobs/test_records_fold_611.py`,
`::test_facts_before_the_start_and_invalid_outcomes_are_ignored`, Codex #14). At the end: client records, then `cleanup`, then
`outcome` with its notice, then the notice is posted; the `start` fact is confirmed before the
launch or the job is refused. An outcome that cannot be written is retried by the liveness loop
and its notice waits for it (`tests/jobs/test_outbox_611.py`, Codex r1 #5). The delivery is the
placed entry itself, so "was it delivered" is one `get_entry`.

### 8. The inbox, placement, 16 KiB and 64 KiB

A notice is placed as a `custom_message` under `nt-` + 12 hex of `sha256(notice_id)`, with
`notice_id` in its details, before the model sees it (`SessionStorage.place_entry`, ADR-0242 rule
5): the same id is adopted, never written twice; an id held by another payload is held out of
every drain as `placement_conflict`; an unknown outcome is settled by placing the same id again;
a cancel releases the claim. Notices waiting when the user types are placed by `prompt` ahead of
the typed line, in the same request; inside a run a boundary — after each tool batch, when the
model has finished — takes them, but **never while an input the user typed is unanswered**: such
an input can still be withdrawn by an Esc (ADR-0286), whose replay would append a notice placed
after it a second time under a fresh id. A placed notice becomes the point a later withdrawal
returns to, so no withdrawal replays one — one delivery per notice in the whole file
(`tests/harness/test_notice_inbox_722.py::test_a_notice_waiting_when_the_user_types_goes_first_and_an_esc_keeps_one_delivery`,
`::test_a_notice_placed_after_an_answer_is_never_withdrawn_or_given_back`, Codex #8). An entry a
boundary adopts — its line landed and then raised — is still given to the model in that run unless
the context it was built from held it
(`::test_a_notice_whose_line_landed_then_raised_mid_run_reaches_that_run`, Codex #2;
`::test_a_notice_reposted_mid_run_that_the_runs_context_holds_is_shown_once`, verify SF3), and it
becomes the point a withdrawal returns to only when it is the leaf
(`::test_an_adopted_notice_off_the_leaf_is_not_where_an_esc_returns`). A drain
takes pending notices in arrival order, charging each its body and the most its placement line may
add (`Notice.finalize_max`, which the kernel cuts the line to), up to 64 KiB, always at least one
(`::test_a_drain_charges_each_notice_its_placement_line`, Codex #10); `prompt_notices` and
`prompt` take their batch before the run so one request never carries two drains. A write that
fails is released to the front of the queue for the next boundary
(`::test_a_failed_placement_write_keeps_the_notice_for_the_next_boundary`). Abort, an Esc
withdrawal, `give_back_queued`, `take_queued_messages` and the reload hand-over never touch the
inbox (`::test_nothing_that_clears_the_users_queues_touches_a_notice`), nothing is claimed once an
abort is asked (`::test_a_notice_is_not_claimed_once_an_abort_is_asked`), and `message_end` neither
appends a placed notice again nor counts it as the user's input (`::test_a_notice_is_placed_before_the_model_sees_it_and_written_once`,
`::test_a_notice_placed_after_an_answer_is_never_withdrawn_or_given_back`). One notice is at most
16 KiB fully framed — header, fence, next step, the not-a-reply line and the placement-time line
included: every part a client or an extension supplies has its own cap (the header with its label,
title and id 1 KiB, the kind 128 bytes, the next step 1 KiB, the placement line 512 bytes), and
the frame is measured again once it is put together; a longer body is cut at a character boundary
and the cut says how to read the rest (`tests/jobs/test_notice_611.py`,
`::test_every_supplied_part_is_capped_so_the_frame_stays_under_16k`, verify S2, Codex #9). The frame says it is not user input and that it is not the
reply to a question the model asked, and neutralises a forged header or fence in the job's text.

### 9. Admission

Settings, GLOBAL scope only (`SettingsManager.get_background_jobs_settings`, the
`get_features_agents` precedent): `backgroundJobs.limits.<settings key>` per kind and an optional
`backgroundJobs.maxTotal`, off by default. A per-kind limit is an integer from 1 to 16
(`HARD_MAX_PER_KIND`); `maxTotal`, when set, an integer from 1 to 32 (`HARD_MAX_TOTAL`). With
`maxTotal` off, nothing bounds the sum across kinds but each kind's own limit (verify N4). A value
that is not an integer in range falls back to the default with one warning per distinct value;
values are read at every reservation, so a change applies to the next one and a lowered limit
refuses new work only (`tests/jobs/test_admission_611.py`). Slots are counted by the kind's name,
which stays the same across generations; the settings key only selects the setting, so a kind
registered again under another key still counts its earlier generation's jobs
(`::test_a_kind_registered_again_under_another_settings_key_keeps_its_count`, Codex #17).
Reservations are taken in one synchronous block. `JobService.reserve(kind, wanted)` sizes a batch
to what is free before consent, and each consented `start(…, reservation=…)` takes its slot out of
that reservation instead of asking for another, so nothing after consent is refused
(`::test_a_reservation_taken_before_consent_is_handed_to_the_start`, Codex #16).

### 10. Liveness and monitoring

One service task every 5 s while any job is not terminal, or a restored `unresolved` job is
rechecked (§6). A job whose roots are all gone while its runner has not returned for 45 s
(`EXIT_SETTLE_GRACE`, above delegation's 30 s post-EOF bound) is latched, its legs run, its runner
cancelled, and the barrier makes it `failed` ("its process ended but its runner did not report")
or `unresolved`
(`tests/jobs/test_monitoring_611.py::test_dead_root_with_stuck_runner_reaches_failed_or_unresolved`;
not a tick before the 45 s, `::test_a_dead_root_gets_the_whole_exit_settle_grace`, verify V16).
No activity for 10 minutes flags it `quiet`; that never stops it and never notifies (#604: no
timeout; `::test_quiet_flag_never_kills_or_notifies`). Surfaces: a product-core footer segment —
`⚙ 2 running · 1 result waiting`, `⚙ 1 running (quiet 12m)`, `⚙ 1 unresolved`,
`⚙ 3 results waiting`, dropping the quiet detail and then shortening to `⚙ 2 · 1 waiting` at
narrow widths (`::test_indicator_text_and_narrow_widths`); `/jobs` (list, `<id>`, `result <id>`,
`stop <id>`, `send`); and two tools, `job` (`list`, `status`, `result`), whose `list` is the
`/jobs` table, and `job_stop(id)` (`::test_job_tool_list_matches_jobs`). The footer, `/jobs send`
and the TUI's wake count a notice an extension posted (`ExtensionAPI.post_notice`) as a waiting
result too (`tests/tui/test_wake_611.py::test_an_extension_notice_wakes_counts_and_is_sent`, verify
S3).

**Every surface sees only the current session's jobs** (Codex #5): `JobService.snapshot`,
`snapshots`, `stop`, `request_stop`, `output`, `/jobs`, both tools and the facade answer an id of
another session's job as unknown — holding an id is not authority over it
(`tests/jobs/test_ownership_611.py::test_an_id_of_another_sessions_job_reads_and_stops_nothing`);
a job's notice waits for its own session (`::test_a_notice_waits_for_its_own_session`, verify V15).

**The model's control goes through the permission gate** (verify S5). The gate reads one marking
per tool object (`tools/provenance.py`), so one tool cannot be read-only for `list` and gated for
`stop`. The options were: leave `job` unmarked (every read asks in `default` and is blocked in
plan); keep one read-only `job` and refuse `stop` from the model (the user stops with `/jobs
stop`); teach the gate a per-call marking that reads the arguments (a change to the gate's trusted
input for every tool); or two tools. This ADR takes **two tools**, the one that touches no
existing gate code: `job` is marked `read_only` and only reads — it runs in every posture, plan
included — and `job_stop` carries no marking, so the gate treats it as any tool aelix did not mark
(ADR-0253) and, outside `auto`'s bash classifier, as it treats `bash`: with an approval UI (the
TUI) it asks in `default`, `auto-accept-edits` and `auto`; plan blocks it on every host; `yolo`
allows it; a host with no approval UI — `-p`, json, RPC without a host approval hook — allows it
in `default`, `auto-accept-edits` and `auto`, and a delegated child blocks it there
(`::test_job_reads_without_asking_and_job_stop_is_gated_like_any_mutating_tool`,
`::test_job_stop_meets_the_gate_any_unmarked_tool_meets_in_every_posture_and_host`, verify SF1).
Auto-accept-edits and auto never allow it on their own. A kind can still
route its control elsewhere (`job_tool_control=False`: `job_stop` answers with its hint). A user's
`/jobs stop` asks nothing: it is the user's own action. This choice is to be reviewed with the PR
and again when S3 exposes the tools.

### 11. The print drain, the verdict, the waker and the dialog invariant

**Drain** (`print_mode._drain_background`), each pass in this order: a pending notice, or one
unanswered at the leaf, gets a notice turn; otherwise, while a tracked job (a kind with
`blocks_print_exit`) is unsettled, it waits for a change; otherwise it yields once and leaves only
if neither has appeared meanwhile. A job leaves "unsettled" only once its notice is posted, so
checking the inbox first cannot miss a result. It waits after a failed answer too
(`tests/cli/test_print_mode_background_611.py`), and `-p --continue` delivers restored notices
(`::test_continue_drains_restored_notices`). **On SIGINT during the drain** every job of the
session is stopped (bounded by `CLEANUP_BOUND`), then each notice the model was not given is
written out — on stderr in text mode, a header line (`aelix: interrupted — 1 background result
was not given to the model:`) and one line each; one `background_notice_undelivered` event each
on stdout in JSON mode — with no turn started for any, and the interrupt ends the run as it would
without jobs (`tests/cli/test_print_verdict_611.py::test_an_interrupt_during_the_drain_stops_the_jobs_and_flushes_without_a_turn`;
a real SIGINT to a real process exits killed by SIGINT with no sleeper left,
`::test_a_real_ctrl_c_in_the_drain_exits_as_an_interrupt_after_the_flush`, POSIX; verify S6, Codex #19). **Verdict**: within one user prompt and the
notice turns after it, the first failed answer is latched and exits 1; a later user message opens
a new scope, so across user messages ADR-0268 is unchanged; signals (128+sig), exceptions (1) and
a broken pipe (141) keep their rules (`tests/cli/test_print_verdict_611.py`; a failed notice turn
followed by a successful one exits 1, `::test_a_failed_notice_turn_then_a_successful_one_exits_1`,
verify V13). Text mode prints on stdout the last answer that did not fail — after notice turns,
that is the last notice turn's answer, not the answer to the user's prompt (verify N7). The
design's row `test_first_user_failure_then_user_success_exits_1` became `..._exits_0` on the
owner's scope.
**Waker** (`tui/jobs_ui.py`): a `NoticeWake` item in the input queue, so the input loop starts the
notice turn as it starts a typed line. Its gates hold when it is queued and again when it is
taken: the loop parked and nothing running (G1); **no dialog open** — approval, select,
confirmation or any other modal (G2); not held, disposed or stopped, and no reload or session
change running now — `AgentSessionRuntime.in_transition`, not the latest reload's
`reload_phase`, which stays `done` (or the failed step) for the rest of the session
(`tests/tui/test_wake_611.py::test_the_waker_wakes_after_a_reload_and_after_a_failed_one`, verify
B1) (G3); no Esc unwinding (G5 — on the real harness an unwinding Esc is also a running turn, so G1
holds it first; `::test_an_esc_still_unwinding_holds_the_wake`); a live result pending — a job of
this process, or an extension's notice (G7). A notice never resolves, cancels or
answers a dialog: it waits for the boundary after the answer
(`tests/tui/test_notice_never_touches_dialogs_611.py`, Claude Code #85408's shape). A notice that
arrives while `agent_end` or a compaction still runs wakes at the next park
(`tests/tui/test_wake_611.py`). **Restore** (OQ7): restored results start no turn; one line says
`◆ N background results are waiting. They go to the model with your next message, or run /jobs
send to send them now.` (for one: `◆ 1 background result is waiting. It goes …`), and `/jobs send`
forces the wake (`tests/tui/test_restore_611.py`).

### 12. Activation

`jobs/activation.py`: `ACTIVE`, `True` since #779 (the note of 2026-11-17, #779). While off, the CLI builds no service and no bridge
extension, so there is no `job` or `job_stop` tool, no `/jobs`, no footer segment, no liveness
loop, no reconciliation, no drain, no waker, and the `backgroundJobs` settings are never read
(`tests/jobs/test_synthetic_kind_611.py::test_the_gate_off_refuses_the_facade`).
`ExtensionAPI.jobs` and `ExtensionAPI.post_notice` read as absent to every probe an extension
makes: reading either raises a plain `AttributeError` with the interpreter's own message, `.name`
and `.obj`, so `hasattr` is `False`, `getattr(…, None)` is `None`, `inspect.getmembers` lists
neither, and a call fails exactly as before #722 (`JobsInactive`, the service's own refusal, is
an `AttributeError` too) (`tests/jobs/test_facade_611.py::test_with_the_gate_off_jobs_and_post_notice_read_as_absent`,
verify S1). They are not gone from the class: both are properties, so `dir(api)` and
`hasattr(ExtensionAPI, …)` name them. **The kernel inbox is off as well**:
`AgentHarnessOptions.notice_inbox` defaults to `False`, and a harness without it raises on
`post_notice`, `prompt_notices`, `add_notice_listener` and `notice_changed`, answers its readers
empty, and drains exactly the queues it drained before #722
(`tests/harness/test_notice_inbox_722.py::test_with_the_inbox_off_the_kernel_is_what_it_was`,
Codex #1); the CLI builds every harness with `notice_inbox=jobs_ext is not None`, so it is on only
while `ACTIVE` — for `-p`, RPC and the harness a `new_session` rebuilds
(`tests/cli/test_notice_inbox_wiring_722.py`, verify SF4). Tests turn the gate on by setting the
attribute (the `jobs_active` fixture) and build their harnesses with `notice_inbox=True`; a scratch
live run did so from a launcher script that sets it before calling `main_sync`. No flag,
environment variable or setting turns it on.

### 13. ADR-0008's review gate, replaced

ADR-0008's quoted gate (with its 2026-07-26 amendment) is replaced by:

> *"Product-core may own a kind-agnostic, owner-scoped job mechanism: execution ownership,
> cleanup and liveness evidence, opaque client payload storage and recovery dispatch, generic job
> records, admission and reservation, notice framing, the host drain, monitoring, a
> process-launch ledger a parent may request from a child it spawned, and a generic control and UI
> projection, including a detach key for runs whose kind declares it. The kernel may own a
> kind-agnostic notice inbox, confirmed and idempotent placement, bounded drains, and the session
> store's resolution of an append whose outcome is unknown. Registered clients — extensions, and
> product-core's own tools such as `bash` — supply execution and resource-specific relation,
> settlement, recovery, rendering and control adapters. Agent spawn implementation,
> delegation-specific identity maps, child-session and resume semantics, consent and grant policy,
> and multi-agent topology, task-list, goal and dashboard decisions remain extension policy.
> Product-core may surface a consent refusal and must never author one. Neither the kernel nor
> product-core may add a subagent lifecycle event type. No implementation branch, import, API,
> schema, payload interpretation or wording in the job mechanism or the kernel may depend on a
> particular registered client kind; a kind's labels, settings key and control hints are opaque
> strings its client supplies. Every kind's effective concurrency bound is a finite positive
> integer — its client's default or the user's setting — never above one immutable hard maximum,
> and an optional user-set total cap applies across kinds; these maxima are the expressly allowed
> host-safety invariants."*

Amendment (c) of 2026-07-26 reads, from 2026-11-08: concurrency limits are client defaults the
user may configure, under core's hard maxima; product-core declares `HARD_MAX_PER_KIND`,
`HARD_MAX_TOTAL` and the notice framing bound `NOTICE_MAX_BYTES`
(`tests/agents/test_p2_band_boundaries.py::_PRODUCT_CORE_CAP_ALLOWLIST`).

## Consequences

- A kind is ~200 lines of adapter and runner; delegation (S1c) and background `bash` (S2) plug
  in without touching the service.
- `harness/core.py`'s prompt tail and release moved, unchanged, into `_settle_runs` and
  `_release_prompt` so a notice turn settles exactly as a typed one.
- The hook bus gained no event type: the inbox's listeners (`add_notice_listener`: queued,
  placed, held, turns released, idle) are harness callbacks, not lifecycle hooks.
- Kernel additions, each inert for a caller that does not use it:
  `AgentHarnessOptions.notice_inbox` (off by default), `Session.place_custom_entry` (the
  `custom` twin of `place_custom_message`), `AgentSessionRuntime.in_transition`, and
  `aelix_ai/utils/_process_walk.py` (the walk `aelix_agents/reaper.py` now delegates to, its own
  names kept as its tests' seams).

## Limits and what S1b does not do

- **macOS**: argv is not readable for a platform binary, so a restart matches by start time alone
  there; parent-death cleanup stays a documented limit (S3).
- **POSIX walk**: the walk is not one snapshot (a process forked between two reads can be missed,
  ADR-0197's accepted race), and it names only what descends from the root while the root lives:
  a helper that leaves its group AND whose parent exited before the soft leg is not named (S2's
  subreaper and launch ledger). A group named at the soft leg is killed at the hard leg after the
  grace, so ADR-0291's group-number recycling residual has that grace as its window.

  *2026-11-10 note (#758, #611 S2a; behind `ACTIVE = False`):* the hard leg now checks a pid its
  own collection walked as it checks an earlier one (`ProcessTree._kill_posix`; on `3cab5d16` it
  was killed with no check,
  `tests/jobs/test_stop_evidence_posix_611.py::test_a_fresh_pid_whose_stamp_changed_gets_no_kill`),
  and the start time it checks against is the one the walk read with the pid's parent
  (`descendant_identities`), not one read after a pid-only walk, which recorded a replacement's
  time when the child was replaced in between (`::test_a_child_replaced_between_the_walk_and_its_stamp_gets_no_signal`,
  red on `f3e9b814`; Codex #2 on #758's first round). Every group — the root's own, a group the
  client registered, one a walked pid leads — is signalled only while its leader gives authority:
  gone, a zombie, or alive with the start time recorded for it, or for the root, the service's own
  unreaped `Process`; never a live leader whose start time was not read
  (`::test_a_null_stamp_live_leader_is_never_signalled`, red on `3cab5d16`;
  `::test_a_root_number_naming_a_live_leader_of_unknown_start_time_is_not_group_killed` and
  `::test_a_registered_group_whose_live_leader_has_no_start_time_is_not_killed`, every leg, red on
  `f3e9b814`, where the root branches still read an unknown time as "not moved";
  `::test_the_jobs_own_unreaped_root_still_gives_authority_over_its_group`, green throughout). Groups
  go before pids. The tree path and `stop_descendants` walk the root only while its number is its
  own (#731 item 2, `::test_the_tree_path_checks_the_roots_identity_before_walking`, red on
  `3cab5d16`). A gone leader's group is killed (the owner's OQ-D answer, 2026-11-10;
  `::test_a_dead_leader_group_with_no_verifiable_member_is_killed`, green before and after). Its
  residual, as the #611 S2 contract (r3 §1 #3) states it: after the leader was recorded and before
  the check, its group emptied and a stranger took the number, made it a group, started members and
  exited; the check then reads a gone leader and the stranger's members are killed. That window is
  the time since the group was recorded, not microseconds. The confirm step alone counts a zombie as
  gone — a walked pid that is one, and a group whose every member is one
  (`::test_confirm_counts_a_zombie_walked_pid_as_gone`, `::test_confirm_counts_a_zombie_only_group_as_empty`,
  and under a Linux subreaper `::test_confirm_counts_an_adopted_zombie_as_gone_under_a_subreaper`;
  red on `3cab5d16`, which waited to the deadline) — so a job is `stopped` while such a zombie waits
  for its parent to reap it. A group listing that skips a process it could not read is unknown, not
  "only zombies" (`::test_an_unreadable_member_makes_the_listing_unknown`, red on `f3e9b814`, Codex
  #7). The barrier does not wait on a recorded group whose leader's number reads as another process,
  read again on every poll (#731 item 1, `::test_confirm_does_not_wait_on_a_recorded_group_whose_leader_moved`,
  red on `3cab5d16`; `::test_a_leader_whose_number_moves_during_the_wait_is_not_waited_on`, red on
  `f3e9b814`, Codex #11); a live leader whose start time was never read is still waited on, and its
  job reads `unresolved` at the deadline (`::test_a_live_leader_whose_start_time_was_never_read_is_still_waited_on`).

  *2026-11-10 note (#719, ADR-0296):* for a delegated agent, the helper that left its group after
  its parent exited is now the launch ledger's and the subreaper's: the agent's `bash` records each
  shell's group, the run's sweep kills it with authority and kills what carries the run's marker,
  and on Linux the agent adopts such orphans and walks them at its exit. The delegation client's
  `helper_cleanup` answers `confirmed` on POSIX only when that sweep confirmed everything it
  identified (ADR-0296 §6; behind `ACTIVE`). A gone-leader ledger group follows OQ-D's rule above.
  On macOS a helper that left its group and cleared its environment, or is a platform binary out
  of its group, is still not named (ADR-0296's residuals). Other kinds are unchanged.
- **SIGINT** is handled in the drain only; a Ctrl+C during the user's own prompt ends the run as it
  did before #722, and the jobs are then stopped by the dispose's `session_shutdown`. SIGTERM does
  not flush (a parent's stop of a child).
- **Restored notices**: a restored `unresolved` job's notice goes with the user's next message or
  `/jobs send`, and `-p --continue` drains it — a notice turn for a job aelix could not judge.
- **Windows**: the evidence legs are proved against an injected api; a real Windows run waits for
  S3's acceptance.
- Delegation (`agent(action=spawn|…)`), the versioned `print_done`, background `bash`, the
  launch ledger, Ctrl+B, the rich panel and the flip are S1c, S2 and S3.

*2026-11-17 note (#777, #611 S3c PRs c1 and c2, which land as one PR — 리드 판단, the lead's call;
behind `ACTIVE = False`, run through the test launchers `tests/jobs/gated_cli.py` and
`tests/jobs/gated_tui_child.py`): the parent's death, measured.* A real `-p` run against a scripted
local model starts a background delegation, whose child blocks in a foreground `bash` sleeper, and a
background `bash` sleeper. A real TUI run, its keys written to its stdin, runs one delegation in the
foreground, and moves it into a job with Ctrl+B once its child's sleeper runs (L1a–L3a) or leaves it
in the turn (L1b–L3b). Every process a case watches writes its own pid and start time to a marker
(the two aelix interpreters through the launcher and its `sitecustomize`, each shell through its
sleeper). All are alive, with a known start time, before the kill, and each is read by identity
after it. A process counts as gone only when it is first read gone within the bound, counted from
the kill (`tests/cli/test_parent_death_611.py`; its checks are pinned against fakes in
`tests/cli/test_parent_death_checks_777.py`). The cleanup rescans the markers, so it also kills a process
that announces itself while the cleanup runs; one that announces only after the cleanup has returned is
past any rescan. On POSIX these are guards of `b6100968`'s behaviour.

| Platform | The kill | `-p` (PR c1) | A job Ctrl+B moved (PR c2) | A TUI delegation not moved |
|---|---|---|---|---|
| Linux | SIGKILL | The delegated child and the background shell asserted gone within 5 s, `pdeathsig` on each (`test_l1_…`). Linux docker (`python:3.12-slim`, `--init`), 20 of 20 runs over two rounds: the child gone 0.004–0.39 s after the kill, the background shell 0.003–0.01 s. Recorded only: the child's own shell and sleeper gone within 0.01 s (its own cleanup, ADR-0291 limit 3), and the background shell's sleeper alive at 5 s in 20 of 20 (owner OQ-C/D, 2026-11-10, "둘 다 권고대로 (권장)": `pdeathsig` on the shell only, ADR-0297). | The delegated child asserted gone within 5 s (`test_l1a_…`); docker, 20 of 20 runs: gone 0.009–0.39 s after the kill, its shell and sleeper (recorded) within 0.07 s. | The same (`test_l1b_…`); docker, 10 of 10 runs: gone 0.004–0.30 s after the kill, its shell and sleeper (recorded) within 0.07 s. |
| Windows | `TerminateProcess` | Every announced process's handle, opened before the kill with no Job handle held, unsignalled at the kill and signalled within 10 s after it, and the kill must succeed (`test_l2_…`). It proves the announced processes only, not that a Job is empty, and the delegated child also ends when the venv launcher's own job ends the interpreter, so its row does not tell which job did. At `b69890da`, in the lead's CI round (run 43998348364): passed on all four legs. At `6e3f5612` (CI run 44023793108): passed on all four legs. | The same (`test_l2a_…`). At `b69890da`: failed on all four legs, the child's shell and sleeper alive 10 s after the kill; #777's fix round 1 now spawns the print channel's child suspended (ADR-0238's 2026-11-17 note). At `6e3f5612` (CI run 44023793108): passed on all four legs. | The same (`test_l2b_…`): at `6e3f5612` (CI run 44023793108), passed on all four legs. |
| macOS | SIGKILL | Nothing about the children (`test_l3_…`). Local macOS, Python 3.12, 20 of 20 runs over two rounds: the delegated child, both shells and both sleepers all alive 5 s after the kill, then killed by the case. The documented limit: no `pdeathsig` (ADR-0291 limit 3). | Recorded only (`test_l3a_…`); local, 20 of 20 runs: the child, its shell and its sleeper all alive 5 s after the kill. | Recorded only (`test_l3b_…`); local, 10 of 10 runs: the child, its shell and its sleeper all alive 5 s after the kill. |

The 23 real-child cases of `tests/agents_ext/test_agent_background_611.py` (L4), and the launcher's two
smoke cases with them, now run on windows-latest as well: the first run of the evidence legs against a real
Job. At `b69890da` 22 of the 23 and both smoke cases passed on all four legs; the 23rd,
`test_result_rereads_the_whole_result_and_a_long_one_points_there`, failed there with WinError 206: its
own `-c` script is 41,530 characters (`len` of the script, on macOS), and its whole command line was
41,684 on macOS with this lane's interpreter path (`subprocess.list2cmdline`, recorded at the spawn),
past the 32,767 a win32 command line holds. It now runs its stub from a file; the longest command line
of the other cases was 1,748, measured the same way. At `6e3f5612` all 23 and both smoke cases
passed on all four legs (CI run 44023793108); for these cases the **Windows** bullet above is now measured
on windows-latest.

## Note (2026-11-08, ADR-0295, #723)

S1c registered the first client, delegation (ADR-0295), and needed two generic additions, both
kind-agnostic: `JobContext.register_process` returns the `ProcessRecord` it registered — the same
object the barrier reads — so a client that must kill before its `run` returns does so through
`jobs/evidence.py` on that record (evidence first, §5); and the extension facade
(`ExtensionAPI.jobs`) gained `reserve(kind, wanted)`, `start(…, reservation=…)`, `changed()`,
`snapshot`/`snapshots` (the service's reading names for `status`/`list`) and `stop(…, reason=…,
wait=…)`, each checked against the gate, the facade's lifetime and the kinds its extension
registered, as every facade call is. The versioned `aelix_print_done` line and its request,
`JobContext.protocol_env` (`JOBS_PROTOCOL_ENV`, `PRINT_DONE_EVENT` in `jobs/contract.py`), are in
the tree: a json print run writes the line when its environment asks and its own gate is on
(ADR-0198's note).

S1c's fix round 1 (2026-11-09) changed §6's restart reading, kind-agnostically (the AST scan,
`tests/jobs/test_core_is_kind_agnostic_611.py`, stays green):
- **`recovered` is a state of its own** (`JobState`, in `TERMINAL_STATES`), never shown as `done`:
  `jobs/records.py` said a recovered outcome "shows as done", and the service turned it into
  `done` at the restart and again when it read the outcome back. A kind's `recovered` is
  recorded only once every process the job recorded is confirmed gone; while one is alive or
  cannot be told gone, the job stays `unresolved` with its slot, and the liveness recheck asks the
  kind again once the evidence says gone
  (`tests/jobs/test_restart_611.py::test_a_kinds_reading_is_recovered_never_done_and_waits_for_its_process`).
- **A kind's own `unresolved` keeps its reason**: OS evidence that its processes are gone gives
  back the slot but never makes it `lost`, which §6's "a process that has since gone makes it
  `lost`" said of every restored job
  (`::test_a_kinds_own_unresolved_reason_survives_os_evidence`,
  `::test_a_kinds_own_unresolved_with_a_live_process_holds_its_slot_and_reason`).
- **`KindCapabilities.noun`**: a kind may name what it runs, `(one, many)`, and the admission
  refusal counts by it ("1 delegated agent already runs"); without one it is "N background jobs of
  this kind already run" (`tests/jobs/test_admission_611.py::test_a_kinds_noun_counts_what_runs_with_its_plural`).
- **`JOB_MARKER_ENV` and `JOB_INTERNAL_ENV`** name the variables a job hands only to a process it
  owns; the delegation client's `PrintChannel` removes them from every other child it starts (the
  `RpcChannel`, which nothing in the product constructs, does not).
- **§5's group kills read the leader first.** The evidence legs skip a group — and a root — whose
  recorded leader's number now names a process that started at another time
  (`ProcessTree.stop_and_collect(root_started=…)`; `tests/jobs/test_stop_evidence_posix_611.py::test_a_group_whose_recorded_leader_number_now_leads_another_process_gets_no_signal`),
  closing for the service the residual ADR-0238 accepts. A leader that is gone is not a moved
  number: its group is still killed and confirmed empty
  (`::test_a_group_whose_recorded_leader_is_gone_is_still_killed`).

S1c's fix round 2 (2026-11-10), kind-agnostic as well:
- **`JobSnapshot.rechecking`** says whether this process checks an `unresolved` job again. A
  restored job's `unresolved` notice, posted once at the restart, says "it keeps checking" only
  when the liveness loop will check it then, and otherwise that aelix "is not watching this job";
  a notice that said it keeps checking is not corrected if the loop later stops (ADR-0295 Limits)
  (`tests/jobs/test_restart_611.py::test_live_match_is_unresolved_not_lost`,
  `::test_a_kinds_own_unresolved_reason_survives_os_evidence`). That corrects S1b's notice too for
  the restored jobs the loop never rechecked — `unreadable`, `adapter_missing`, `recover_error`,
  `relation_error` — which said "it keeps checking"; an `adapter_missing` or `adapter_too_old` job
  is still read again when its kind registers
  (`tests/jobs/test_generations_611.py::test_missing_adapter_is_unresolved_and_reevaluated_on_registration`).
- **The `maxTotal` refusal** names no kind and calls nothing a background job, since a kind may
  count what runs in the foreground: "3 run across every kind, and settings
  backgroundJobs.maxTotal allows 3." (`tests/jobs/test_admission_611.py::test_the_total_limit_names_no_kind_and_calls_nothing_a_background_job`).

## Note (2026-11-11, ADR-0297, #760)

S2d registered the second client, background `bash` (ADR-0297), and needed three generic
additions. Each is kind-agnostic, and the AST scan stays green
(`tests/jobs/test_core_is_kind_agnostic_611.py`); with the gate off none is reachable (§12):
- **`client_start`** (Codex #12): `JobService.start(…, client_start=…)` (and the extension facade's
  `start`) writes a kind's launch data into the confirmed `start` fact as `client`, opaque, and
  every snapshot of the job carries it as `JobSnapshot.client_start` — the live job's and a
  restart's alike, so a kind reads back what it recorded instead of deriving it again in a new
  process. A start without it writes no `client` key
  (`tests/jobs/test_client_start_611.py::test_client_start_is_in_the_start_fact_and_on_every_snapshot`,
  `::test_without_client_start_the_fact_has_none_and_the_snapshot_is_empty`). r2 §5 put it in the
  restored snapshot's `payload`; a field of its own keeps `payload` the outcome's (this lane's call).
- **`restored_note(facts)`**, a required adapter method: a restart's `lost` or `unresolved` notice
  ends with the kind's one line, asked with the job's `start` fact; `None`, a raise or an answer
  that is not text adds nothing and leaves the frame whole (§3's table: as `finalize_at_placement`)
  (`::test_a_restored_lost_notice_carries_the_kinds_line`,
  `::test_a_restored_unresolved_notice_carries_the_kinds_line`,
  `::test_a_kind_with_no_line_or_a_raising_one_adds_nothing`). Delegation and the synthetic kind
  return `None`. Required rather than probed, so the service never asks an adapter what it has; an
  extension kind registered without it gets no line (the raise is caught).
- **`OutputSlice.next_offset`** (Codex #13): where a kind's next page starts, opaque. The `job`
  tool's `result` and `/jobs result` hint `next_offset` when a kind sets it and `offset +
  len(text)` otherwise (`OutputSlice.continue_at`;
  `::test_next_offset_is_what_the_job_tool_and_jobs_result_hint`). Delegation set none then, so
  its hint was characters counted over its byte pager, which ADR-0295's `agent(action="result")`
  did not share. *2026-11-12 (#770):* it sets `next_offset` now, so the hint is the byte after the
  page (`tests/jobs/test_client_start_611.py::test_delegation_pages_by_bytes`, the guard
  `test_delegation_paging_is_unchanged` renamed and turned; ADR-0295's note of that date).

## Note (2026-11-12, #769, #749)

Two kind-agnostic changes before #611 S3 opens the gate. The owner chose to do them first on
2026-11-12, selecting the option labelled "S3 전 정리 3개 먼저 (권장)" ("the three clean-ups before
S3 first (recommended)"). With the gate off neither is reachable (§12); the AST scan stays green
(`tests/jobs/test_core_is_kind_agnostic_611.py`). Fix round 1 of #769's lane (the same date)
reworked the retention after Codex's and an independent verify's first reviews; this note
describes the result.

- **Retention (#769).** The service kept every job it ran in its map for the life of the process.
  Now a session's ended jobs are kept whole up to `SETTLED_KEPT` (64); past it, the oldest that
  owes nothing is kept only as what its session's records say of it. The bound, the policy and the
  number 64 are the lead's call, not the owner's.
  - *Owes nothing*: ended, its `outcome` written (no outbox), its tasks over, no notice of it
    waiting in the current harness's inbox — its `unresolved` one as well as its final one, each
    tracked by id from its post to its placement — and in its session's file a placement entry
    for its final notice or, with none, for each `unresolved` one its facts recorded. A notice that
    waited in a harness a switch replaced is not waiting anywhere; its final one, posted again
    where its session is current, is what it owes. Its notice reads delivered once nothing of it
    waits and its current notice was placed, in whichever order they were placed; a notice the
    harness holds out of every drain (a placement conflict) before the job's last placement waits
    nowhere (`::test_a_job_is_delivered_once_both_its_notices_are_placed_whatever_their_order`,
    the final notice placed before the `unresolved` one — verify-r2 F1;
    `::test_a_held_unresolved_notice_does_not_hold_the_job`;
    `::test_an_earlier_notice_placed_does_not_deliver_a_final_one_not_yet_posted`). The check
    runs only when a notice of the job is placed: a notice held *after* the job's last placement —
    its final notice placed, then its `unresolved` one's retry held by a `PlacementConflict` —
    leaves the job `pending` (and kept whole) until a `/reload` or `/resume` rebinds its session
    and its final notice is posted and placed again (verify-r3 F1). A `PlacementConflict` needs an
    entry already under the notice's `nt-` id (12 hex of a sha256) for another notice: a 48-bit
    collision or an entry another writer placed, so this is rare; no test of this lane pins it.
    (`tests/jobs/test_retention_769.py::test_a_job_that_runs_is_unresolved_or_owes_its_notice_is_never_let_go`,
    with a bound of 0; `::test_a_job_is_not_let_go_while_its_unresolved_notice_is_owed`, a launch
    that failed after a failed cleanup — Codex's finding 2;
    `::test_a_notice_lost_with_a_replaced_harness_does_not_hold_its_job`;
    `::test_the_records_and_the_map_must_both_say_the_notice_owes_nothing`;
    `::test_a_job_whose_outcome_waits_in_the_outbox_is_never_let_go`;
    `::test_a_job_whose_task_or_escalation_still_runs_is_kept_whole`.)
  - *What is kept*: its `start` and `outcome` facts, its kind's `decode` of the outcome joined
    with its own records (as a restart reads an ended job, §6), and the two small values no record
    holds — its `elapsed` and its last `progress` — as they were. Its kind is held by name, version
    and label, not by its `Registration`, so no old generation of an extension's runtime stays
    alive through it (`::test_a_job_let_go_keeps_no_old_generation_of_its_kind_alive`, four
    generations: one runtime alive after `gc.collect()`; Codex's finding 4); its label is the
    current generation's, as a job kept whole reads it (`::test_a_job_let_go_reads_its_kinds_current_label`). Its runner, context,
    task and evidence are freed (`::test_a_job_let_go_frees_its_runner_context_and_task`, weak
    references dead after `gc.collect()`).
  - *Only a kind that reads from what it persisted*: a kind declares
    `KindCapabilities.reads_persisted` when an ended job of it reads — its rows and every page of
    its output — only from what it persisted (its records, as `decode` reads them, and the files
    they name). Only such a kind's ended jobs are let go; every other kind's stay whole, so an
    extension kind that does not declare it keeps every ended job, and the map grows past the bound
    by them (`::test_a_kind_that_did_not_declare_its_reads_persisted_stays_whole`, a kind whose
    records keep less than its later pages read — verify-r2 F2; the sleeper kind in
    `::test_a_job_that_runs_is_unresolved_or_owes_its_notice_is_never_let_go`). Background `bash`
    (its output file) and delegation (its result file) declare it.
  - *Read alike, or kept whole*: as a check of that declaration, before it lets a job go the
    service builds the job's snapshot from what it would keep and compares it with the live one,
    field by field but `payload` (a kind's live payload and its `decode` differ by design), then
    what the kind reads of each — its rows at width 1000 and the first 16 KiB page of its output.
    Later pages are not compared; the declaration stands for them. Any difference keeps the job
    whole, one past a row's tenth column or a page's twentieth byte included
    (`::test_a_difference_past_the_first_columns_or_bytes_keeps_the_job_whole`; verify-r3 F4;
    `::test_a_job_whose_read_back_would_read_otherwise_is_kept_whole`, a kind
    whose result lives only in memory — Codex's finding 1;
    `::test_rows_that_would_read_otherwise_keep_the_job_whole`;
    `::test_a_read_back_whose_facts_disagree_with_the_live_job_keeps_it_whole`;
    `::test_a_job_let_go_keeps_its_last_progress_and_its_elapsed`, rows a kind renders from its
    progress — Codex's finding 3). So does an outcome that says its kind's records did not all land
    (`client_records: missing`), whatever its run returned and even when every read is alike
    (`::test_a_job_whose_records_did_not_all_land_is_kept_whole`,
    `::test_a_job_whose_run_ended_with_no_outcome_is_kept_whole`,
    `::test_records_that_did_not_all_land_keep_a_job_whole_even_when_it_reads_alike`), a read that fails and a `decode`
    that raises; those two are tried again at the next trim
    (`::test_a_job_whose_records_cannot_be_read_back_is_kept_whole_and_tried_again`). The map can
    therefore hold more than the bound by such jobs. Only ended jobs count toward it, and one that
    cannot be read back does not count as let go
    (`::test_a_running_job_does_not_count_toward_the_bound`,
    `::test_a_job_that_cannot_be_read_back_does_not_count_as_let_go`).
  - *What a reader sees*: `/jobs` (list, detail, result, stop), `job` (list, status, result),
    `job_stop`, the footer, the `-p` drain's `unsettled` and `request_stop` give the same text
    before and after, and the snapshot the same fields but `payload`
    (`::test_every_surface_reads_a_job_it_let_go_as_before`, a synthetic kind that declares
    `reads_persisted`, a done and a failed job). What differs is only that `payload`, which an
    extension sees through the facade's
    `status`: it is its own kind's `decode`, as after a restart. A job let go keeps its place in
    `/jobs`: snapshots sort by start time, then by start order, so a tie of start times — a coarse
    clock — does not move it (`::test_jobs_that_started_at_the_same_time_keep_their_order`; Codex's
    finding 7, verify-r1 F3). The bound is per session (`::test_the_bound_is_per_session`), and
    `SETTLED_KEPT` + 6 ended jobs leave the map at 64, the oldest that owed nothing gone first
    (`::test_the_map_holds_the_bound_and_lets_go_of_the_oldest_that_owe_nothing`). The product
    kinds' own proofs are ADR-0295's and ADR-0297's notes of this date.
  - pi's chord design draws the same line — a value read synchronously must stay in memory, and
    only state with an asynchronous read is evicted (`earendil-works/pi` `a22b888a5`,
    `packages/chord/docs/decisions.md`: "Persisted singletons … stay in memory … only keyed
    persisted documents are evicted"): `snapshot()` is synchronous, so what a reader needs of a job
    let go stays as a small record read back once, not re-read on every call.
- **`JobSnapshot.processes_gone` (#749)**: whether aelix has confirmed every process the job
  recorded gone — its barrier passed, or a restart's evidence said so; false while one may live
  and when nothing was checked, as for a restored job whose records or adapter failed before its
  evidence was read (`tests/jobs/test_residuals_749.py::test_processes_gone_is_what_the_evidence_said`,
  `::test_processes_gone_follows_the_barrier_of_a_job_this_process_runs`,
  `::test_an_ended_job_a_restart_found_has_its_processes_gone`). A kind says from it, not from
  `rechecking`, whether a restored job may still run (ADR-0295's note).
- **The unresolved texts (#749).** A `settle_error` job this process ran — the liveness loop skips
  it — has a notice that says aelix "does not check this job again", not "It keeps checking", and
  `rechecking` false (`::test_a_settle_error_job_says_nothing_checks_it_again`; a `cleanup_error`
  job is rechecked and says so, `::test_a_cleanup_error_job_is_rechecked_and_says_so`, a guard
  that passes on `363accf2`). A restored job whose kind's own `unresolved` is rechecked only until
  its processes are gone gets a notice that says "it checks the job's processes until they are
  gone, and promises no later notice", which stays true once they are, when no second notice
  comes (`::test_a_kinds_own_unresolved_notice_promises_no_later_notice`); an OS-evidence
  `unresolved` keeps "it keeps checking" (`::test_an_os_evidence_unresolved_notice_still_says_it_keeps_checking`).
- **Once its processes are gone, a restored job holds no slot (#749).** A restored job whose
  processes the recheck found gone and whose session's records then failed to read stayed
  `unresolved(unreadable)` with no recheck and held its slot until aelix exited. Now its slot is
  released and the recheck stays on, so the next pass reads the records and records how it ended
  (`::test_a_failed_read_during_a_recheck_gives_the_slot_back_and_reads_again`). Once its processes
  are confirmed gone no read or adapter failure takes a slot again: a `recover` or `relation` that
  raises keeps it `unresolved` and rechecked, with no slot, until one works
  (`::test_once_its_processes_are_gone_a_failed_recover_never_takes_a_slot_back`; Codex's finding
  5), and a kind's own reason stands through a failed read
  (`::test_a_kinds_own_reason_survives_a_failed_re_read`). A read that keeps failing is tried again
  on every pass of the liveness loop, every 5 s (`LIVENESS_INTERVAL`), with no backoff; its WARNING
  is logged on the first failure and DEBUG on every later one
  (`::test_a_failed_read_warns_once_then_quietly`).

## Note (2026-11-12, #778)

**An off switch for §12 (#611 S3d).** The owner chose, on 2026-11-12 (OQ-S3-4), "전역 설정
(권장)" ("a global setting (recommended)"); the recommended option's details — the key
`backgroundJobs.enabled: false`, the global scope only, read once at start — are those of the S3
contract's first round (§8). With the gate on, that setting now turns background jobs off for a
launch; with the gate off nothing changes and the setting is not read
(`tests/jobs/test_enabled_setting_611.py::test_with_the_gate_off_the_setting_is_never_read`, a
guard of the new function only: it fails on `52bc607a`, which has no `enabled_at_start`). Base
runs below are of these tests with the product reverted: on `52bc607a` each named test fails, by
the behaviour (the setting ignored) unless it is said to fail for want of `enabled_at_start`;
the three named guards pass there.

- *The latch.* `jobs/activation.py` `enabled_at_start(settings_manager)` reads the setting once,
  as soon as the CLI's settings exist and before any extension loads (`cli/entry.py`
  `_latch_jobs_switch`, right after the settings manager is built), and `aelix status` reads it
  the same way before its own extension load (`cli/status.py`). It records `_switched_off`, so
  `is_active()` — now `ACTIVE and not _switched_off` — answers false from then on, and the
  block that builds the service keeps its `is_active()` condition. Each reader of the gate (the
  `bash` tool and its background offer, the agents extension's background actions,
  `ExtensionAPI.jobs` and `post_notice`, the facade, the print drain) reads `is_active()` when it
  runs. Measured, the gate on and the setting false against the gate off, field by field: a `-p`
  launch builds no service and no inbox and offers the model the same tools — no `job`, no
  `job_stop`, no `spawn`, no background `bash` (`::test_switched_off_a_print_run_is_the_gate_off_run`);
  `run_tui` is handed no `jobs`, so the TUI has no footer segment and no `/jobs`
  (`::test_switched_off_the_tui_gets_no_jobs_so_no_footer_and_no_jobs_command`); every global
  extension's `setup()`, in the trust-vote load as in the launch's own load, sees `is_active()`,
  `hasattr(aelix, "jobs")`, `hasattr(aelix, "post_notice")` and the `.jobs` error exactly as
  with the gate off (`::test_switched_off_every_extension_setup_sees_the_gate_off_the_trust_vote_load_too`;
  the extension probe of `::test_switched_off_an_extension_sees_jobs_and_post_notice_as_the_gate_off_shows_them`
  fails on `52bc607a` for want of `enabled_at_start`); and so does `aelix status`'s load
  (`::test_switched_off_aelix_status_loads_its_extensions_with_the_gate_off`). On `639dcdf6`, the
  first round, the switch was read only at the service condition: the trust-vote load's
  `setup()` saw `is_active()` true, `post_notice` and a `JobsInactive` `.jobs`, and `aelix
  status` never read it (verify F3, Codex C1); both tests fail there. With the setting absent or
  `true` the launch is today's and stderr says nothing about it (`::test_with_the_setting_absent_or_true_jobs_are_on_as_before`,
  a guard). The entry points swept for a read before the latch, by mode, are in
  `.omc/probes/777c0-778-782/fix-r1/entry-point-sweep-r1.txt`. Outside #778 and unchanged since
  #722: with the gate on and no setting, the trust-vote load's `setup()` sees `is_active()` true,
  `post_notice` present and `JobsInactive` on `.jobs`, because no service is built yet (the
  round-2 verify's `p778_voteload.py`, its "on" run).
- *The value.* `SettingsManager.get_background_jobs_settings()` also returns `enabled`, beside
  `limits` and `maxTotal` (`::test_the_settings_keep_their_other_keys`), and
  `get_background_jobs_enabled()` reads it, from the global scope only, as `get_features_agents`
  does: a trusted repository's `.aelix/settings.json` neither turns jobs off nor back on
  (`::test_a_project_value_is_ignored`, failing on `52bc607a` for want of `enabled_at_start`). A
  value that is not a bool counts as `true`, and a `backgroundJobs` that is not an object as
  absent, each with one warning on stderr (`::test_a_value_that_is_not_a_bool_warns_once_and_jobs_stay_on`:
  `"no"`, `0`, `null`, `"false"`; `::test_a_background_jobs_value_that_is_not_an_object_warns_once_and_jobs_stay_on`:
  `false`, a list, a string — silent on `639dcdf6`, verify F7; `aelix status` says it once too,
  `::test_aelix_status_warns_once_about_a_background_jobs_that_is_not_an_object`). It is read once: a change while
  aelix runs changes nothing until the next launch (`::test_it_is_read_once_a_change_while_running_changes_nothing`,
  failing on `52bc607a` for want of `enabled_at_start`).
- *`/settings`.* A "Background jobs" row after "Agent delegation" writes the global key and says
  it takes effect after a restart, as that row does (the lead's call, R13), and the project file
  is not written (`::test_the_settings_row_writes_the_global_key_and_applies_next_launch`). Like
  that row's `features.agents`, it writes `backgroundJobs.enabled` alone, over the object as the
  file holds it then: a limit another writer changed meanwhile stays
  (`::test_the_row_writes_only_its_key_over_what_another_writer_changed`; on `639dcdf6` the whole
  object was written from the manager's copy, verify F6). The settings writer's nested merge
  (`SettingsManager._persist_scoped_settings`) now takes a plain-object field as it takes a
  dataclass one; every other nested write is of a dataclass field, as before. The row is in the
  menu only while the gate is on (`activation.gate_on()`), so that before S3e nothing offers a
  setting nothing reads (this lane's call; `::test_with_the_gate_off_there_is_no_row`, a guard),
  and it stays in a launch the setting switched off, so jobs can be turned back on
  (`::test_switched_off_the_row_is_still_offered_so_jobs_can_be_turned_back_on`, failing on
  `52bc607a` for want of `enabled_at_start`). What the flip must update: with the gate on,
  `tests/tui/test_settings_rows.py::test_build_rows_count_and_keys` counts 25 rows and
  `::test_every_bool_row_is_dispatchable` 14 bool rows (verify r1), not 24 and 13.
- *A delegated child* reads the same global file at its own start: a parent launched with the
  setting false runs a foreground agent whose `bash` offers no `run_in_background`
  (`::test_a_delegated_child_reads_the_same_value[setting-false]`, POSIX); with the setting absent
  the same child offers it (`[setting-absent]`, a guard). Both processes are gated by
  `tests/jobs/gated_cli.py` (#777 PR c0). A child is granted built-in tools only, so `job` never
  reaches its request either way.
- *Limits.* Read at start only. Turning jobs off leaves the job records already in a session file
  (`aelix.job` facts, notice entries) where they are; what reads them then is S3e's rollback test.
  The setting reference goes into S3e's `background-jobs.md`.


## Note (2026-11-12, ADR-0298, #775)

**Adoption: a running foreground call becomes a job (#611 S3a), behind §12's gate.** The service
gains, kind-agnostic: `detach` (`jobs/detach.py` `DetachOffers`, the running calls a host's key
could move, and the host's `guard` every commit asks — none installed means every commit is
refused); `prepare_adopted` (a job built and not inserted, refused with nothing changed when the
service is closing, the kind is unknown or not `detachable`, or the session is not the one the
service serves); `adopt`/`unadopt` (synchronous insert and its undo); `give_slot` (the caller's
reservation `split(1)` into the job, the count unchanged); `run_adopted` (the job's task: its
`start` fact with the client's `client` and its `process` fact as a fact task, then `_drive`); and
`rescue_adopted` (a job task cancelled before its first step runs `_drive` again with the stop as
the service's own, so it ends `stopped` through its barrier and frees its slot). The facade exposes
them for a kind its extension registered (`offer_detach`, `detach_blocked`, `detach_refused`,
`prepare_adopted`, …). `decode`'s mapping also carries the start fact's `client_start`.
Measured on a synthetic detachable kind: `adopt` is called from a plain function, the start time
handed over is not sampled again, the facts are `start`, `process`, `cleanup`, `outcome`, and each
refusal leaves no job, no slot and no task (`tests/jobs/test_adopt_611.py`). The note on the kernel's
`AgentHarness.abort_pending` is the note of 2026-11-17 (#779) at the end.

## Note (2026-11-17, ADR-0299, #776)

**§10's surfaces: `/jobs` is the panel in the TUI, `/jobs list` the table (#611 S3b), behind §12's
gate.** A bare `/jobs` awaits `JobService.panel_hook`, which the TUI's `JobsUi` sets: an in-flow
modal (ADR-0159) listing the session's jobs, redrawn every second, with `↑↓`, `Enter`, `r`, `s s`
and `Esc`/`q` (owner OQ-S3-2, 2026-11-12). With no hook — RPC's `commands/run` — and for
`/jobs list` the command prints the table, and `job(action="list")` is the table whatever is hooked;
`-p` and json do not run `/jobs` as a command. The table itself changed: its ACTIVITY is a kind's row 1, no longer its row 0 (the title again); before TITLE gets
fewer than 20 cells AGE goes, then ACTIVITY narrows; cells are cut by terminal cells. The panel holds the waker's G2 while open (§11). Details, tests and limits: ADR-0299.

## Note (2026-11-17, #779)

**Active (#611 S3e).** `jobs/activation.py` `ACTIVE` is `True`; §12's gate-off description now
describes a rollback, and a user's off switch is the 2026-11-12 (#778) note's
`backgroundJobs.enabled: false`. The product rollback is the one line `ACTIVE = False`. The gate-off
paths keep explicit tests through the `jobs_inactive` fixture (`tests/conftest.py`), and in real
processes through a launcher that sets `ACTIVE = False` in the CLI and its delegated child
(`tests/jobs/test_gated_cli_611.py::test_with_the_gate_rolled_back_neither_process_is_gated`). With the
line applied, those all pass, and exactly the ten tests that pin the shipped value fail, by design:
`test_synthetic_kind_611.py::test_the_shipped_gate_is_on` and the nine shipped-gate twins of the
flip's gate-off pins (eight named `…shipped…`, and `test_interactive_mode_hands_run_tui_the_job_service`), in `test_agent_background_611.py`, `test_entry_router.py`,
`test_print_done_protocol_611.py`, `test_project_trust.py`, `test_gated_cli_611.py`,
`test_bash_background_611.py` (two) and `test_settings_rows.py` (two)
(`.omc/probes/779/fix-r1/rollback-applied-changed-tests.txt`: 10 failed, 749 passed over every
changed test file). A session written with jobs on — a moved job (its `client` v1 linkage, its
closing record carrying `job_id`), a placed notice and a waiting one — opened by the real CLI
(`_async_main`, `--session`, `-p` and json) with the gate rolled back or switched off: the build has
no service and no kernel inbox, exactly the user's request is sent, the placed notice is replayed
once and the waiting one is not delivered; opened on again, the waiting one goes once with the user's
message, and each notice id is on exactly one entry, the placed one never placed or sent again
(`tests/jobs/test_rollback_779.py::test_jobs_on_then_off_then_on`; contract r2 §5.5).

**`job` and `job_stop` (§10) count as built-in tools for the tool flags** (R9, the lead's call):
the run's `ToolPolicy.builtin` is `tools.BUILTIN_TOOL_NAMES`, pi's seven names and these two, so
`--no-builtin-tools` and a profile's `builtin_tools: false` lock them off, `--tools` keeps them only
when it names them, `--no-tools` turns them off, and a rebuild keeps that. `defaultTools` treats them
as extension tools, as it treats `agent` and `aelix_status`: `ALL_TOOL_NAMES` stays pi's list for
it and for everything else. `JobsExtension` registers both for the TUI, `-p` and json hosts only,
named so that a new app mode gets neither, so not on RPC and not under ACP until #612, while the
service and `/jobs` are still built there (`tests/cli/test_job_tools_builtin_779.py`, the matrix:
the TUI, `-p` with no flag, `--no-builtin-tools`, `--tools` with and without `job` and `--no-tools`,
json, a reload, RPC's `new_session`, and ACP's `resources/list` and `resources/reload`; a profile's
`builtin_tools: false` and `defaultTools` measured by the #779 verify, round 1).

**Test isolation.** With the gate on, a test that runs `_async_main` installs a service that only
the end of a whole run removes; on the flipped `bf507d59`, 79 tests of `tests/cli` left one
installed, and a later test registered its job kind in it
(`test_agent_flag_builds_profile_harness_options` then
`test_a_resume_refuses_every_key_that_would_make_it_another_run` failed one case that passes
alone). `tests/conftest.py` restores the installed service and the off switch after every test.
In the product such a service is left only by a launch that ends early — a spy in a test, or one of
`_async_main`'s error returns before its cleanup, such as an unknown `--agent` profile (Codex,
#779 review): the #779 verify found all 79 to be one or the other, with no job and no liveness loop,
and the process exits right after such a return. Covering them is not a helper's work: the
service is installed in `cli/entry.py`'s `_async_main` (its `install_service` call) and its cleanup is
that function's closing `finally`, with nine direct `return 1` between them, so it stays as it is (the lead's call).

**The kernel's `AgentHarness.abort_pending` (#775, ADR-0298; contract r4 §7).** A read-only
property, `_abort_requested or _turn_cancelled`: `abort()` sets the latch and then cancels the turn
task — the only `cancel()` call on the turn task — and an extension's `ctx.abort()` sets the latch
with no cancel (`tests/harness/test_abort_pending.py`). A cancel of the prompt task that awaits the
turn task reaches the turn task too, without `abort()`, and does not set `abort_pending` (contract
r4 errata E1; the #779 verify's probe: the turn task cancelled, `abort()` not called, both flags
false). The kernel itself reads nothing from it;
the TUI's Ctrl+B reads it to refuse a move while an abort is pending (ADR-0298, "Two checks stay as
conservative refusals").

## Amendment (2026-11-18, ADR-0306, #123)

*The control adapter named in the quoted gate exists.* `KindCapabilities.controls` declares the
verbs a kind takes, `JobService.control(job_id, verb, payload)` checks the declaration and calls
the adapter's `control`, and a kind that declares none is refused with its `control_hint`. The
agent kind declares `steer` and `wrap_up` and resolves the row of a spawned job and of a Ctrl+B'd
one (ADR-0306 D4). The service still names no kind and reads no payload. Fix round 1 (V7): the
refusal names the verb in a human's words for the two the `/jobs` surfaces send (`steer` is "a
message", `wrap_up` "a wrap-up") and writes any other as the kind supplied it; the bash kind's
hint is "/jobs stop <id> stops it (panel: s s)", read by this refusal only (`job_stop` reads a
hint only where `job_tool_control` is off).

*The macOS row of the 2026-11-17 parent-death table, re-recorded (fix round 1, F2).* Local macOS,
Python 3.12, 10 of 10 runs of each `test_l3…` recording: the delegated child, its shell and its
sleeper were gone within 0.4 s of the SIGKILL in all three columns — the child's command inbox is
a pipe from the killed aelix and reaches EOF (ADR-0306). The background `bash` shell and its
sleeper of the `-p` column were alive 5 s after the kill in 10 of 10, as before. Nothing is
asserted there still; `tests/cli/test_inbox_eof_123.py` asserts the child's end on POSIX.

*§11's drain has two more ends (fix round 1, F2 and F5).* `_drain_background` also leaves, between
passes and once more right before a notice turn, when the command inbox reached EOF (the run
exits 1) and when the run that just ended read a final steer — a wrap-up (the exit code is the
last answer's). In both it waits for no job and starts no notice turn, and the dispose stops the
session's jobs. Each is measured in ADR-0306, "Consequences", with its condition; a `-p` run with
no command inbox takes neither.

*A running job may ask for attention once (#123 D1; the owner, 2026-11-18, on the round-1 report:
"2. 이번에 넣기" — built in this change; the shape is the lead's call,
`.omc/specs/123-d1-stuck-notice-design-2026-11-18.md`).* `JobContext.attention(body, *, brief="")`
is a kind-agnostic door, and the service still names no kind and reads no payload. It frames one
notice per job with §8's frame — the status phrase "is still running", the job's running time, no
`job(action="result")` hint when the body is cut — under the id `<job>:attention`, with
`details.status == "running"` and the brief `<label> "<title>" still running: <brief>`. It answers
`False` and posts nothing once the job raised one, whatever became of it; for a job that does not
notify (`notify` off on the job or its kind); unless the job is `running` with no stop latched
and its `run` not returned — a stop the liveness loop latches and a run in its barrier both still
read `running`; and, its record `dropped`, when the current session's inbox refuses the id (one it
already knows; fix round 2, C-N: at `1991e445` the record stayed `pending` and the door answered
`True`). It is not the job's result: it never changes the job's `notice`, `notice_state`,
`owed` or `placed`. Routed through them, its placement would mark a running job `delivered`, and
in `_complete`'s window before the outcome fact `unsettled()` would read false, so a `-p` drain
could leave before the result was posted. So `result_waiting`, `notice_state`, `unsettled()`,
`extension_waiting()`, the footer and `/jobs` read the same with and without it, and
`JobSnapshot.attention_waiting` (additive, defaulted) says it waits. §11's wake counts it (G7,
`has_live_pending`) and the TUI's line for it is `◆ background notice: <brief>`; §11's drain gives
it a notice turn and goes on waiting for the job — and `unsettled()` now holds from the job's
terminal (or `unresolved`) state until its notice is posted (`_Job.completing`, set with the state
and cleared in a `finally`; fix round 2, C-R1: at `1991e445` a drain that read it while `_complete`
wrote the job's records and cleanup fact, or `_become_unresolved` its supervision fact, left before
the notice was posted; a job with no notice still settles once those writes end); §11's SIGINT flush leaves it out (it is not a
result, and the job was just stopped). `bind` posts a pending one again only while its job runs
and drops it otherwise. No fact is written, so a restart restores nothing of it, and a job #769
lets go owes nothing for one still waiting. §10's `quiet` still never notifies. Each clause:
`tests/jobs/test_attention_123.py`; the delegation kind's use: ADR-0306 D9.

## Note (2026-11-19, #810, #820, #821, #822, #825, #827)

**§8's frame and briefs.** Every cut in `jobs/notice.py` replaces a lone surrogate with U+FFFD
before it measures UTF-8 bytes (`utf8_safe`, as `jobs_client._utf8_safe` does for a child's text
since #782): a job whose title held one raised `UnicodeEncodeError` in `frame`, its drive task
died, and it ended with no `outcome` fact and no notice (#810;
`tests/jobs/test_lone_surrogate_title_810.py`, which also runs a real `-p` whose background
`bash` command holds `\udcff`, POSIX only). An extension notice's key is its identity, not display
text, so a name or id holding a lone surrogate is written apart, `ext~:` and the hex of its bytes:
with U+FFFD in it, `"id\udcff"` and `"id\ufffd"` were one notice and the second was refused as a
duplicate (Codex's review, L9). Each part's hex is of its first 128 bytes, as many as a valid key
keeps; a first version kept 64, and two ids differing only after byte 64 were one notice (lane
b7f verify round 2, N1; `test_a_surrogate_notice_key_keeps_as_many_bytes_per_part_as_a_valid_key`). `cut_brief` ends a cut brief in `…` inside its 256 bytes, and the
agent kind's brief is cut so at 160 characters, at the last whitespace (`str.isspace`) before the
cut when what that keeps measures at least two thirds of the bound (107 characters, 171 bytes),
and otherwise hard, at the bound, never inside a character (#825;
`tests/jobs/test_brief_cut_825.py`). A first version looked back 24 characters for an ASCII space
only (Codex L12, verify F2); a second ended at any whitespace however far back, and "Failed:
/very/long/path…" read "Failed:…" (lane b7f verify round 2, N2; the two-thirds rule is the
lead's call).

**§10's surfaces.** The TUI's `/jobs send` counts the results and the attention notices among
the notices still pending, read in one step: an attention notice a boundary was placing made a
waiting result read "1 notice", and `JobsCommand` asks the host's send hook before its own
result-only count, which said "No results are waiting." while only an attention notice waited
(#820; `tests/jobs/test_jobs_send_counts_820.py`, the placement paused at the session write).

The `job` tools and `/jobs` find a job by a kind's own id too (#827). `commands.find` takes an
exact job id, then a unique prefix of one, and only then asks each kind that is a
`JobIdAdapter` (`jobs/contract.py`, one optional method, `job_for(ident, jobs)`), handing it its
own jobs of the current session, newest first; an answer counts only when it is one of them, and
an id two kinds answer with different jobs is refused (`JobService.jobs_named`). Asked before the
prefix, as first built, a kind's alias redirected `job_stop` from the job a prefix named, and an
adapter could name another kind's job (Codex's review, H2). The core still names no kind
(`tests/jobs/test_core_is_kind_agnostic_611.py`). The agent kind answers from this process's
names, then from each job's own data (the agent and session ids a restart read back into its
payload, a moved run's linkage), so an agent of a run before a restart resolves too once that
run has ended (Codex M4). Until #847, a spawned run a restart read back as unresolved (its
recorded process alive, no settle record) had no read-back ids in its payload, and only its job id
named it to the `job` tools and `/jobs`, while `agent()` took its agent id (lane b7f verify round 2,
V2-F2); see the 2026-11-22 note of #847.
An id that names no job gets a pointer to `job(action="list")` in the `job` and `job_stop` tools;
`/jobs` says `no job <id> in this session` as before
(`tests/agents_ext/test_job_tool_agent_id_827.py`). Resolving the id, rather than only pointing
at `agent()`, is the implementer's call.

A control (§3.1 of ADR-0306) that reaches an ended job asks its kind for the sentence when the
kind is a `JobEndedControlAdapter` and declares the verb; otherwise the service's own "<id>
already ended: <state>" stands. The agent kind answers its door's refusal, so `/jobs wrap` and
`/jobs steer` on a finished agent say `use action="resume"` as the model is told (#822, Codex
M6). The panel's `w` `w` and `m` on an ended row ask the same `control`, and show what those two
say; they said "<id> has ended" without asking it (lane b7f verify round 2, V2-F1;
`tests/agents_ext/test_wrap_up_refusal_822.py`). The `m` half changed on 2026-11-22 (#848; see
the note of that date).

**Restart.** An ended job read back after a restart has the run time its outcome fact recorded
as its `elapsed` (`elapsed_ms` above 0; 0 is what the outcomes written with no run behind them
hold, and stays unknown): `/jobs <id>` read `elapsed —` for every such job, on `1c2a4a7c` too
(Codex M5; `test_a_restart_shows_the_run_time_the_outcome_recorded` in
`tests/agents_ext/test_moved_elapsed_821.py`).

## Note (2026-11-19, ADR-0307, #612)

- **RPC and ACP** (the Status block's "named so that a new app mode gets neither, so not on RPC and
  not under ACP until #612"): owner, 2026-11-18, r2 Q4, "전부 열기 (권장)". `JobsExtension`
  registers `job` and `job_stop` for `rpc` too, the `agent` kind's background actions and
  `bash`'s `run_in_background` are offered there, and an unknown app mode still gets none
  (`tests/cli/test_job_tools_builtin_779.py::test_rpc_and_acp_register_both_and_the_background_actions_across_rebuilds_612`,
  `tests/tools/test_bash_background_611.py::test_rpc_and_acp_advertise_and_accept_it_and_an_unknown_mode_does_not_612`).
  A host learns of a waiting notice from an `aelix_notice` frame and places it with
  `children/notices` (ADR-0307 D4, D5).
- **The ended-owner epoch** ([ADR-0307](0307-a-cancelled-delegations-report-is-its-calls-result-and-a-session-change-settles-the-turn-first.md) D7). `owner_ended` raised nothing a start already past its
  entry could see, so a start parked in its fact append while the owner ended registered after the
  stop's snapshot and ran for an ended session (Codex measured it, contract r5 §6.4). `JobService`
  now keeps `_end_epoch` and `_ended_at` (one integer per owner, never emptied): `owner_ended`
  raises the owner's epoch synchronously first, and `start` refuses at registration — no await
  between the check and the insertion — when the service is closed or the owner's epoch rose since
  its entry, writing the outcome fact `failed`, "the session ended before the job started", and
  raising `JobStartRefused`. `prepare_adopted` (Ctrl+B) refuses the same pair, comparing with the
  epoch recorded at the last `bind` (it is synchronous). `tests/jobs/test_ended_owner_epoch_612.py`:
  a start parked while its owner with no jobs ended, one parked across `shutdown`, one parked across
  a `/reload` of the same owner (the next one passes), and an adoption after the owner ended since
  the bind are refused; on `567753c0` each parked start registered its job and the adoption did
not raise (`.omc/probes/612/impl/B/onbase/c-tests.txt`); a guard, an owner ended after the start
registered stops its job, passes on both.

## Note (2026-11-22, #848)

The panel's `m` on an ended row types no text, yet it showed what `/jobs steer` answers, and the
agent kind's steer sentence ends "with this text as its next instruction" (#848). `m` now asks
`JobService.ended_message(job_id)` first: a kind that is a `JobEndedMessageAdapter`
(`jobs/contract.py`, one optional method, `ended_message(job)`) words the note for a message typed
with no text, and the agent kind answers the wrap-up's refusal (`runtime.WRAP_UP_RESUME_HINT`,
`use action="resume"` with no text to resume with). A kind without the method, one whose method
answers `None` and one whose method raises get the service's `control` for `steer`, as before, so a
`JobEndedControlAdapter` sentence for `steer` stands; nothing is delivered either way. Unlike
`ended_control`, `ended_message` is asked whether or not the kind declares `steer`. `/jobs steer
<id> <text>` and the panel's `w` `w` are unchanged, and the core still names no kind. The first
head of PR #862 asked `control` for `wrap_up` from `m` instead, which took a steer-only kind's own
sentence away and gave it the service's "<id> already ended: <state>" (review round 1, measured on
`202bbb99`). Contributed by jay23606 (PR #862).
`tests/agents_ext/test_wrap_up_refusal_822.py`
(`test_the_panels_w_w_says_what_jobs_wrap_says_and_m_names_no_text`,
`test_the_panels_m_on_an_ended_job_of_a_steer_only_kind_keeps_the_steers_words`).

## Note (2026-11-22, #847)

**A spawned run a restart reads back unresolved or lost is named by its agent id and its run key,
at that restart and every later one.** The limit in the 2026-11-19 note of #827 (a spawned run
read back as unresolved was named only by its job id) is lifted. Where the names come from was
measured first. A background spawn writes the job's `start`
fact, then its run's `aelix.child_session` start record, which holds the run key, the child
session id (the agent id) and the job id, and only then the job's `process` fact: the agent id is
fixed after the job's start and recorded with its job id before its process exists (one real
spawn through the extension, the job service and the print channel with a stub child, macOS,
Python 3.12). `runtime._run` has no platform branch between the job's start and that record, so
win32 takes the same order; that is read, not run. So the record the issue's first option asked
for already exists on every spawn path, and no new one is written. What was missing was a reader
that `job_for`, which is synchronous and reads only snapshots, could use: a run read back
`unresolved` or `lost` has an empty payload.

The agent kind's `recover`, which a restart hands the job's own records, now keeps the run key
and the child session id of that start record by job id (`DelegationJobs.read_back`, in memory)
before it reaches any verdict, and `job_for` names a job by them after its payload and its moved
run's linkage, newest first as before. A restart does not ask `recover` about a job whose outcome
is recorded: it hands the outcome fact and the same records to `decode`, which keeps the same two
ids from the same start record, whatever the outcome. Without that, a run read back `lost` had an
empty payload again at the restart after the one that recorded `lost`, and was named by its job
id only (found in #847's first verify round). The outcome fact is written by the job service,
which this change does not touch, so an outcome an older aelix recorded reads the same: measured
once by a driver, not a test, on a session whose `lost` outcome a `0436193c` restart recorded. Both keep only string values (tested below) and, as
`_names_of` does, only non-empty ones (`commands.find` refuses an empty id before it asks any
kind, so that part has no test). `decode` keeps nothing for a moved run's linkage of a version
this aelix cannot read, which it refuses before it reads any record (tested below). The core and
`jobs/contract.py` are unchanged (`job_for` stays synchronous;
`tests/jobs/test_core_is_kind_agnostic_611.py` passes). Putting the names in the
snapshot's payload would have needed the service to read them or a new adapter method, and a
`job_for` that reads the session would have changed the contract; keeping them in the kind is the
lead's call. In `tests/agents_ext/test_job_tool_agent_id_827.py`:

- `job`, `job_stop` and `/jobs` find the job by its agent id and by its run key, as `agent()` does
  (`job_stop` then answers what it answers for the job id: the agent kind's pointer to
  `agent(action="stop")`), when its recorded process is alive (its child's file locked or not) and
  when it is gone and left no answer (`lost`); each fails on `0436193c` with "no job <id> in this
  session"
  (`test_a_spawned_run_read_back_unfinished_is_named_by_its_agent_id_and_run_key`).
- With a real background child still running when its service is dropped without a shutdown, a
  second service bound to the same session reads the job back `unresolved` and takes the agent id
  (`test_a_live_spawned_childs_agent_id_names_its_job_in_the_next_service`).
- A run read back `lost` is recorded once, with an empty payload, and at the second and the third
  restart `job` status and result, `job_stop`, `/jobs` and `agent()` still take its agent id and
  its run key (`test_a_lost_runs_agent_id_names_its_job_at_every_later_restart`). With a real
  background child whose parent process ended with `os._exit` while it ran, the next service,
  opened from the file, reads the job back `unresolved` and, once the child is killed, `lost`; the
  service after that reads `lost`; both take the agent id and the run key
  (`test_a_crashed_parents_agent_is_named_by_its_agent_id_after_it_is_read_back_lost`). That is on
  macOS, where the stub child reads no command inbox and outlives its parent. In Linux docker it
  was gone 0.5 s after its parent's exit in 3 of 3 runs (`pdeathsig`), so the first service may
  read `lost` at once; the test takes either, and passes there.
- For a resumed agent whose older run ended with its parent's settle and whose newer run was still
  running, `job`, `/jobs` and `agent()` take the newer job; on `0436193c` `job` named the older one
  while `agent()` took the newer
  (`test_after_a_restart_an_agent_whose_older_run_settled_names_its_newer_unfinished_run`).
- A start record whose run key or child session id is a list or a dict, which only a hand-edited
  or corrupt file holds, reads back as on `0436193c`: `done` from its parent's settle, else
  `unresolved` from the OS evidence, and its job id names it
  (`test_a_start_record_whose_ids_are_not_strings_reads_back_as_before`).
- A run with its parent's settle whose restored outcome could not be written stays
  `unresolved(record_failed)` with an empty payload and is still named by its agent id
  (`test_a_settled_run_whose_restored_outcome_is_not_written_is_named_by_its_agent_id`).
- An id the agent kind and another kind answer with different jobs is refused as before
  (`JobService.jobs_named`; `test_an_id_a_restored_agent_and_another_kind_both_answer_is_refused`).
- A job whose records hold no start record of its run, or one with no key and no child, reads back
  as before: `unresolved` from the OS evidence and named by its job id only
  (`test_a_job_whose_records_name_no_agent_reads_back_by_its_job_id_only`). So does a job whose
  `start` fact links a moved run by a linkage version this aelix cannot read: neither the linkage's
  ids nor those of a start record with its job id are trusted, at the restart that asks `recover`
  nor at a later one that hands `decode` an outcome of the job
  (`test_a_linkage_of_an_unknown_version_names_its_job_by_its_job_id_only`).

A job whose kind a restart neither asks nor hands its outcome (its `start` fact unreadable, or its
kind not registered or older than the one that wrote it) is still named only by its job id; that
follows from the code and has no test of its own. The whole test module is skipped on win32 (its
stub children's pipes, as in `tests/agents_ext/test_detach_611.py`), so none of these tests runs
on Windows; `decode`, `recover`, `keep_read_back` and `job_for` have no platform branch, but the
spawn and cleanup code the two real-child tests also drive does.
