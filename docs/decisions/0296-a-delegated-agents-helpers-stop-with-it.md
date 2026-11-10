# 0296. A delegated agent's helpers stop with it

Status: Accepted (2026-11-10, #719, #750)
Date: 2026-11-10
Issue: #719 (#611 slice S2c), with #750. Parent: #611. Contract:
`.omc/specs/611-s2-contract-r3-2026-11-10.md` (r3; where it says "as r2",
`611-s2-contract-r2-2026-11-10.md`), with Codex's two reviews of it beside it.
Narrows: ADR-0291 limit 2 (rewritten there), ADR-0290 limit 10, ADR-0293's "POSIX walk" limit,
ADR-0295 §9 and §10 (dated notes there).
Owner decisions this implements, quoted from #719:
- 2026-11-07, the decision comment: "위임 자식의 bash가 `cmd &`·`(cmd &)`·`nohup cmd &`로 떼어 놓은
  helper는 그 자식이 끝나거나 멈출 때 함께 멈춘다. 최상위 세션이 떼어 놓은 helper는 지금처럼 남긴다(#232)."
  The child's bash records the process group in a launch ledger beside the child session, right
  before and right after the spawn; the parent checks the ledger plus the delegation's env marker
  after the child ends or stops; on Linux the delegated child becomes a subreaper; on Windows the
  delegation Job's kill-on-close does the same; the macOS limits; ADR-0291 limit 2 is rewritten.
- 2026-11-10, OQ-A: "게이트 없이 적용 (권장)" — shipped ungated for foreground delegations.
- 2026-11-10, OQ-D: "둘 다 권고대로 (권장)" — a dead-leader ledger group that is not retired and has
  no verifiable member is killed, as a stated residual.
- 2026-11-10, the split: "4개로 나눠 게시 (권장)" — this is S2c, after S2a (ADR-0291's 2026-11-10
  note) and S2b.
The lead's calls, not the owner's: no zombie reaping during the run; no SIGSTOP freeze; the exit
cleanup's at most 3 walk-then-kill passes; the 2 s retire tick; `CLEANUP_BOUND` 10 s.
pi: `earendil-works/pi` `e8ec20291` (`origin/main`, read 2026-11-10). Its print mode kills the
`bash` shells it is tracking on SIGTERM (`packages/coding-agent/src/modes/print-mode.ts:64`,
`killTrackedDetachedChildren`), but a call untracks its shell when it returns
(`core/tools/bash.ts:188`), and there is no ledger, subreaper or parent-side sweep, so a finished
call's helper, or a child that is SIGKILLed, is left. aelix diverges knowingly: the owner's decision
asks for the helpers of a FINISHED call, on every door.

## Context

A delegated agent's `bash` call returns when its command does (#232), and what the command left
with `cmd &`, `(cmd &)` or `nohup cmd &` stays running: its shell has exited, so nothing a stop
walks leads its group (ADR-0291 limit 2), and a double fork with `setsid` is in a session of its
own (ADR-0290 limit 10). The #611 S2 design measured it with a stand-in agent through cancel, stop
and end: every one of 15 shape × door cases left the helper with PPID 1, on macOS and Linux (M1).
On `478ffd13` the real child shows the same: `tests/agents_ext/test_delegated_helpers_719.py::
test_a_finished_calls_helper_stops_with_the_delegation` fails on all twelve macOS rows (three shapes
× end, Esc, `stop`, `stop_all`), the helper alive 10 s after the door
(`.omc/probes/719/impl/base-fails/helpers-macos.txt`).

#750: on the cancel path `PrintChannel.run` SIGKILLed the child and nothing awaited it, so a
subprocess transport could reach a closed loop; whole-file runs of
`tests/agents_ext/test_print_channel_spawn.py` with `-W error::pytest.PytestUnraisableExceptionWarning`
failed 5 of 16 times in Linux docker on `478ffd13` (`.omc/probes/719/impl/rate-750.txt`).

## Decision

### 1. The switch, and who is armed

`print_channel._helper_tracking(row)` tracks every POSIX delegated row, foreground and background,
and none on win32: it creates the ledger and mints the run's token before the spawn, and the child's
environment gets `AELIX_LAUNCH_LEDGER=<path>` and `AELIX_DELEGATION_MARKER` with the token appended
(never replaced; an inherited ledger path is removed;
`tests/jobs/test_launch_ledger_719.py::test_the_parent_appends_its_token_and_drops_an_inherited_ledger`).
A process is ARMED only when it finds `AELIX_LAUNCH_LEDGER` at startup (`cli/entry.py` →
`tools/launch_ledger.arm_from_environment`, which also takes the variable out of its own
environment). Every door that starts a delegated child reaches `PrintChannel.run`, so each is
armed: a single `agent()` call, a resume, batch members, chain steps, `/agents run`, RPC
`agents/run` and (behind `ACTIVE`) a background spawn. `RpcChannel`, which the bundled runtime never
builds, is not (`.omc/probes/719/impl/entry-point-sweep.md`).

An unarmed process — the top-level aelix, its REPL `!`, RPC `exec` — spawns and passes the
environment byte for byte as before: `tools/bash.py::_spawn_shell` takes the old `Popen` when
`launch_ledger.writer()` is `None` (`::test_an_unarmed_command_still_inherits_the_protocol_variable`
and `tests/agents_ext/test_delegated_helpers_719.py::test_a_top_level_bash_calls_helper_is_still_released`,
both green on `478ffd13` and after).

### 2. The ledger

Beside the child session as `<stem>/<run key>.launch-ledger` (a resume's file is named by its own
new run key); with no child session file, `launch-ledger` in the per-spawn prompt directory, read by
the run's cleanup before that directory is removed. Created `O_CREAT|O_EXCL|O_NOFOLLOW`, 0600, with
an `open` line; one JSON record per line, appended. The agent writes `armed` (with whether it became
a subreaper), then for each `bash` spawn an `intent` in full before `Popen` and a `launch` with the
shell's pid, group and start time right after it, or `failed` when the spawn raised; `retired` when a
launched group answers `ESRCH`; `exit` at its end. The token is never written
(`::test_intent_is_complete_before_the_spawn`, `::test_a_spawn_that_raises_writes_failed`).

A write that fails never loses the process: the writer marks itself damaged, appends one `damaged`
line if it can, and writes nothing more
(`::test_an_intent_write_failure_still_runs_the_command_unverified`,
`::test_a_damaged_writer_writes_nothing_more`). *Fix round 1 (the lead's call; Codex 4):* a group
whose `launch` line was not published is NOT released with its call. Only the agent would know it,
and a stop kills the agent first, so a `/bin/sleep` left in that shell's group on macOS — named by
neither the ledger nor the marker — outlived the stop on `7d298a8d`. Now the writer holds such a
launch and, when its call returns, stops the group through the ledger's authority rule (its shell is
gone); a helper aelix could not record does not outlive the call that started it
(`::test_a_launch_write_failure_keeps_the_process_owned[True]`, red on `7d298a8d` on its first
assertion, "waited 5s for the unrecorded group's helper to die" — the helper outlives the call;
`[False]`, the healthy control, releases it to the ledger as before).

*Bounds (fix round 1, Codex 8).* Once 512 launches have retired or failed since the last compaction
(`COMPACT_AFTER`), the writer rewrites the file by an atomic replace as its `open` and `armed` lines,
a `compacted` line, the intents still in flight and the live launches, and drops the retired history
from memory; the writer's memory holds live launches only. Past 1,024 live groups (`ACTIVE_BOUND`) a
new launch is held as above, stopped when its call returns. The parent reads a ledger streamed, at
most 2 MiB (`READ_BOUND`) and 4,096 bytes a line, on a worker thread; past either it reads damaged
and acts on what it read. 10,000 calls through the writer's own API, each retired, left a file under
64 KiB and empty indexes (`::test_ten_thousand_retired_calls_keep_the_ledger_and_its_indexes_bounded`;
on `7d298a8d` Codex measured 1,326,731 bytes and 10,000 entries in every index for the same count;
`::test_a_ledger_past_the_read_bound_is_damaged_not_read_whole`,
`::test_a_line_longer_than_the_line_bound_reads_damaged`,
`::test_a_live_group_past_the_active_bound_is_held_for_its_calls_end`). A compaction keeps the
intents still in flight (`::test_compaction_keeps_the_intents_still_in_flight`), and a damaged
writer never compacts, which would replace its `damaged` mark with a clean file
(`::test_a_damaged_ledger_is_never_compacted`). A line that does not parse makes
the reading damaged while the lines around it are still acted on
(`::test_a_damaged_middle_line_reads_unverified`). The parent deletes a session-side ledger after a
confirmed sweep and keeps it with a `swept` line otherwise, as report-only evidence nothing kills from
(`::test_a_confirmed_sweep_deletes_the_session_ledger`, `::test_an_agent_that_crashes_reads_unverified`).

An armed agent's `bash` commands lose `AELIX_LAUNCH_LEDGER` and `AELIX_JOBS_PROTOCOL` from their
environment; `AELIX_DELEGATION_MARKER` passes through
(`::test_an_armed_agent_strips_the_ledger_and_protocol_variables`). The module is
`aelix_coding_agent/tools/launch_ledger.py`, not `jobs/` as r2 named it: the marker's name spells
"delegation", which `tests/jobs/test_core_is_kind_agnostic_611.py` forbids in the generic job code.

### 3. The agent

On Linux an armed agent calls `prctl(PR_SET_CHILD_SUBREAPER, 1)` at startup, so an orphan of its
`bash` is reparented to it rather than to init (Linux docker:
`tests/agents_ext/test_delegated_helpers_719.py::test_a_delegated_agent_is_a_subreaper_and_its_stop_takes_an_escaped_env_cleared_helper`,
the helper's PPID the agent's; red on `478ffd13`, where it is 1). It never waits for a pid it did not
spawn (the lead's call), so an adopted helper that exits stays a zombie under it until it exits
(`::test_adopted_zombies_are_held_until_the_agent_exits_and_then_gone`, red on `478ffd13`;
`::test_no_pid_the_agent_did_not_spawn_is_ever_waited`, green on `478ffd13` and after). A retire tick
every 2 s (the lead's call) and the end of every `bash` call mark the launched groups that answer
`ESRCH`; it reaps nothing.

At its end (`modes/print_mode.py::_safe_dispose`, after the session is disposed, once per process;
and, since fix round 1, from an `atexit` hook arming registers, for every exit that never reaches that
dispose — one of `cli/entry.py`'s early returns, an `Error:` route failure among them:
`tests/agents_ext/test_delegated_helpers_719.py::test_an_armed_child_that_returns_early_still_writes_its_exit_line`,
red on `7d298a8d`, no `exit` line; and only in the process that armed — a `fork` of it inherits the
hook, and its normal exit sweeps nothing and writes no `exit`:
`tests/jobs/test_launch_ledger_719.py::test_a_forked_child_of_the_agent_does_not_run_its_exit_cleanup`,
red on `7b28b44e`, the fork's exit killed the agent's live helper) it walks its own descendants and kills them — the groups walked processes lead first, each pid and
group through S2a's identity-checked legs (`HeldWalk`) — up to 3 times (the lead's call) until a pass
finds nothing alive; then the ledger's groups (§4); then this run's marker hits, never itself, its
parent or any ancestor — by NUMBER since fix round 1, read again right before the signal, whatever
start time was or was not read for it (Codex 1: on `7d298a8d` an ancestry read that failed recorded
`(self, None)` and the scanner SIGKILLed itself;
`tests/jobs/test_launch_ledger_719.py::test_the_exit_scan_never_signals_itself_when_its_own_identity_could_not_be_read`);
confirms within 2 s, a zombie counting as gone; and writes `exit`, `unverified` with why when anything
kept it from confirming (`::test_the_agents_exit_cleanup_sweeps_its_ledger_and_its_marker_and_says_what_it_found`). In Linux docker a helper forking a `setsid`, env-cleared child every 1 ms under an
armed stand-in left none (`::test_the_exit_cleanup_takes_a_child_forked_by_a_process_killed_in_its_first_pass`).
The exit scan spared the scanner and its Python parent, both holding the token
(`tests/jobs/test_launch_ledger_719.py::test_the_exit_scan_never_signals_the_agent_or_its_ancestors`).

### 4. Authority before every signal

| Target | Signalled only when |
|---|---|
| a walked pid, a group a walked pid leads | S2a's rule: its identity still matches (a pidfd on Linux, the start time elsewhere); a group before its leader, only while that leader verifies |
| a ledger group | not retired, still has a member, and its leader (the shell) is alive with the start time the `launch` line recorded, or gone, or a zombie (`::test_a_retired_group_is_never_signalled`) |
| a ledger group whose live leader's recorded start time is null or another | never; the answer names it `leader identity unknown` or `leader number moved` (`::test_a_null_stamp_live_leader_in_the_ledger_is_never_signalled`) |
| a ledger group whose leader is gone, with no verifiable member | killed (owner OQ-D; `::test_a_dead_leaders_group_with_no_verifiable_member_is_killed`, and on macOS `tests/agents_ext/test_delegated_helpers_719.py::test_macos_an_in_group_platform_binary_is_killed_by_the_oq_d_rule`) |
| a marker hit | a process of this user whose environment holds this run's exact token, started no earlier than the agent, not the scanner, its parent or an ancestor; its identity read before the environment and again after it, and the two equal (fix round 1, Codex 2: `::test_a_marker_read_belongs_to_the_identity_it_signals`); verified — on Linux held by a pidfd — before its descendants are walked, so a hit that no longer verifies contributes nothing (`::test_a_moved_marker_root_contributes_no_children`, red on `7d298a8d`: the moved root's child SIGKILLed); then it and its descendants through `HeldWalk` (`::test_the_parent_scan_matches_only_its_own_run_token`, `::test_a_marker_hit_older_than_the_agent_is_not_signalled`) |

A scan that could not list this user's processes is `unverified` (`this user's processes could not be
listed`) and keeps the ledger; on `7d298a8d` it read as an empty scan and confirmed (fix round 1,
Codex 7; `::test_a_scan_that_could_not_list_processes_is_unverified_and_keeps_the_ledger`).

### 5. The parent: one cleanup task per run (#750)

The runtime gives each row its `_cleanups` registry before `PrintChannel.run`. The channel creates
the row's ONE task and adds it to the registry in the same synchronous step, right before it awaits
the spawn — asyncio starts the OS child inside that await, while `row.proc` is still `None` — and the
task waits for the run's first end (normal, an error, the cancel handler, or a stop) before it
touches anything (Codex N8, and fix round 1's Codex 3;
`tests/agents_ext/test_run_cleanup_719.py::test_the_cleanup_is_in_the_registry_before_run_awaits_it`).
A `stop_all` that meets a spawn in flight keeps its prompt directory and fallback ledger, joins the
duty, and reports it at the bound; a `stop(id)` returns once the spawned child is stopped; a reload
keeps the older runtime while it has a duty (`::test_a_stop_all_during_a_spawn_keeps_its_directory_and_reports_the_duty`,
`::test_a_stop_during_a_spawn_returns_once_the_child_is_stopped`,
`::test_a_reload_keeps_a_runtime_whose_spawn_is_in_flight`; on `7d298a8d` the first returned with
the ledger deleted, `cleanup=None`, nothing on stderr and the child alive).
The task awaits the child's exit within the grace, sweeps the ledger and the marker
(`print_channel.sweep_helpers`, the read and the scan on a worker thread), confirms within 2 s,
waits up to 2 s (`CLEANUP_CONFIRM_SECONDS`) for the child's pipes to close — and if a holder the sweep
could not name still has them once the child is reaped, closes the parent's own read ends, leaving
the holder unsignalled (fix round 1, Codex 6:
`::test_a_pipe_holder_the_sweep_cannot_name_does_not_keep_the_transport_open`, red on `7d298a8d`:
the cleanup ended and unregistered with the transport open) — removes the prompt directory and closes
the tree after any reaper still escalating. It sweeps only after the child exited
(`::test_the_sweep_runs_after_the_child_has_exited`). `run` joins it before returning the envelope on a normal end; `stop(id)` after its abort
(`::test_stop_id_joins_the_same_cleanup`), and, when Esc already popped the row, the duty the row left
(fix round 1, Codex 5: `::test_a_stop_after_esc_joins_the_cleanup_its_row_left`); `stop_all` — which reload, quit, a session switch and a
SIGTERM to `-p` reach — through one drain shared by every concurrent caller, also when the row is
gone (`::test_stop_all_joins_a_cleanup_whose_row_is_gone`, `::test_concurrent_disposers_share_one_drain`).
Every join is shielded (`::test_a_cancelled_awaiter_does_not_cancel_the_cleanup`). Esc's cancel path
sets it going and does not wait (D14). A drain waits until the registry is empty or for
`CLEANUP_BOUND` (10 s) — per drain generation of one runtime, counted from the drain's start, which
`stop_all` reaches after its reapers; the extension drains each runtime it built with its own bound —
and a task still running then stays, a later drain joins it again, and one line says so: `aelix:
cleanup of delegated run <key> did not finish (<step>)`, exactly once on stderr in `-p` and `--mode
json`, and under the TUI only in the log at DEBUG, never on the terminal; an unverified sweep and a
failed cleanup are DEBUG lines too (fix round 1, verify F1/F2: on `7d298a8d` a WARNING with no handler
reached stderr through `logging.lastResort` — twice headless, over the TUI's frame;
`::test_a_cleanup_past_the_bound_is_kept_and_reported`,
`::test_the_unfinished_line_is_one_line_headless_and_none_on_a_tui`). The CLI's `finally` drains every runtime the
extension built before the loop closes: the extension's drain is pinned
(`::test_the_clis_last_drain_joins_an_older_builds_cleanup`), its call site in `cli/entry.py`'s
`finally` is NOT — no test drives `_async_main` through a delegation whose cleanup outlives the
dispose, and the verify rounds' mutant EN2 (that call removed) survived every run. The prompt FILE is still unlinked synchronously on every
path; the directory goes with the task, once its sweep read a ledger kept there.

#750's own case: the run's retained task — not the cancel path, which does not wait — awaits the
killed child, and the cancelled delegation's transport closes with nobody else awaiting it
(`tests/agents_ext/test_print_channel_spawn.py::test_a_cancelled_delegation_awaits_its_killed_child`,
red on `478ffd13`: open 10 s later; since fix round 1 its stub reaps its first child, so Linux walks
no zombie that ended the holder and it is red there by behaviour too, verify F7). The task's two
waits are pinned one each: its wait for the killed child's exit (`_wait_for_exit`, the verify's
mutant Q10) by `test_run_cleanup_719.py::test_the_sweep_runs_after_the_child_has_exited`, and its
release of the child's pipes (`_release_pipes`, Q11) by
`test_run_cleanup_719.py::test_a_pipe_holder_the_sweep_cannot_name_does_not_keep_the_transport_open`.
Whole-file runs of `test_print_channel_spawn.py` with
`-W error::pytest.PytestUnraisableExceptionWarning`, 16 each, Linux docker
(`.omc/probes/719/fix-r1/rate/rate-750.md`):

| Product | Test file | Runs that failed |
|---|---|---|
| `478ffd13` | its own | 5/16, an unraisable transport warning each |
| fix round 1 | its own | 0/16 |
| fix round 1 | `478ffd13`'s | 16/16 on the prompt-directory case's synchronous-removal assertion (a stated change), 5 of them also leaking that case's transport, which joins no task |
| fix round 1, the task's await of the child removed | its own | 0/16 |

So that 0/16 comes from the test file's own joins of the run's task (verify F4), not from the
product's waits, which the two tests above pin. The round-2 verify then measured the file on
`7b28b44e` in Linux docker at 1 failure in 32 runs: the leaked transport was collected in
`test_the_soft_signal_is_delivered_and_survived`, which runs before the #750 cancel cases, so its
source is not identified; the whole-file rate is not claimed fixed.

### 6. Background delegation (behind `ACTIVE`)

A service-owned row gets no per-run task; its `helper_cleanup` runs the same sweep after the barrier,
inside the service's `cleanup_bound`. It answers `confirmed` only when every target aelix identified
was signalled with authority and is confirmed gone, no launch was in flight, the ledger is whole and
armed, the agent wrote a confirmed `exit` or aelix's stop legs ended it, and on Linux it was a
subreaper; otherwise `unverified` with why, at most 16 reasons and then a count
(`tests/agents_ext/test_agent_background_ownership_611.py::test_posix_helpers_confirmed_when_the_sweep_leaves_nothing`,
`::test_posix_helpers_unverified_when_the_agent_ended_without_its_cleanup`, which replace S1's
`test_s1_reports_helpers_unverified`). `confirmed` does not cover the residuals below.

### 7. Windows

No ledger on win32: the delegation's Job (`kill_on_close=True`, no `BREAKAWAY_OK`) holds every
descendant, and the run's cleanup task closes it. #220's code comment is the only basis; never
measured. `tests/agents_ext/test_delegated_helpers_win32_719.py` (a stub child's `DETACHED_PROCESS`
helper gone after a normal end and after a stop; no ledger) runs first on windows-latest in CI.

## What users see change (POSIX, ungated)

Measured with a real `-m aelix_coding_agent` child on a scripted local model
(`.omc/probes/719/impl/live-rows-macos.md`, `live-rows-linux.md`; again after fix round 1,
`.omc/probes/719/fix-r1/live-rows-*.md`, the same rows):

1. A delegated agent's finished `bash` call's `cmd &`, `(cmd &)` and `nohup cmd &` helpers stop when
   the delegation ends and when it is stopped (Esc, `stop`, `stop_all`): all twelve rows on macOS
   and Linux; and on Linux `setsid` and env-cleared double forks too (eight rows). A delegated
   agent's `npm run dev &` ends with the delegation.
2. Linux: a delegated agent is a child subreaper; its orphaned descendants show under it in `ps`
   (the helpers' PPID in the Linux rows), and those that exit stay zombies under it until it exits.
3. An armed agent's `bash` commands lose `AELIX_LAUNCH_LEDGER` and `AELIX_JOBS_PROTOCOL`.
4. A delegation's result and slot release wait for its sweep: the child's exit within the grace,
   the scan, up to 2 s of confirm and up to 2 s for the child's pipes — at most grace + scan + 4 s
   (the code's bounds). Measured once with the stand-in rows, from the run's end to its result:
   47–50 ms on macOS, where the scan reads this user's ~800 processes, and about 1 ms in Linux
   docker, over four and five `end` rows (`.omc/probes/719/fix-r1/live-rows-macos.md`, `live-rows-linux.md`). Teardown waits for the sweeps, up to 10 s per runtime's drain after its reapers.
5. Top-level `bash`, REPL `!` and RPC `exec`: unchanged (the top-level row in both files).

## Residuals

- **S2a's R1–R3** (ADR-0291's 2026-11-10 note): a process forked between a walk and its kill; the
  check-to-signal window (macOS, and Linux groups); a walked parent's number reused between reads.
  The agent's exit passes narrow R1 at a normal end on Linux (§3); the parent's stop legs do not.
- **OQ-D's shape:** a ledger group that emptied after its last retire check (at most 2 s before, while
  the agent runs), whose number went to a new process that made itself a group leader and started
  members, is signalled.
- **Zombies:** under the subreaper, adopted helpers that exit are held until the agent exits; their
  count is bounded only by the agent's lifetime, which has no timeout (#604).
- **An agent that dies without its exit cleanup** (a crash, `os._exit`, a SIGKILL that was not
  aelix's own stop): on Linux its adopted helpers that left their group and cleared their environment
  go to init and are found by nothing; the answer reads `unverified("the agent ended without its own
  cleanup")`.
- **macOS:** no subreaper. A helper that left its group and cleared its environment is not found, on
  any door (the macOS rows; `test_macos_an_escaped_env_cleared_helper_is_the_stated_residual`, green
  on `478ffd13` and after), and neither is one that left its group and is a platform binary, whose
  environment reads empty (M4; `/bin/sleep`'s marker read `absent` in every macOS row). A parent
  SIGKILLed on macOS walks and sweeps nothing (ADR-0291 limit 3).
- **A write that fails while its call still runs, and a stop in that call:** the held group's shell is
  then alive and walked by the parent's stop leg, so S2a's R1 is what remains.
- **A pipe holder the sweep cannot name** is not signalled; once the parent closes its read ends, its
  next write to that pipe gets `EPIPE`, or a `SIGPIPE` that ends it unless it ignores or handles
  the signal.
- **Windows** is unmeasured until windows-latest runs §7's test.

## Consequences

- New: `aelix_coding_agent/tools/launch_ledger.py` (product-core: ledger, writer, authority, marker
  scan, exit cleanup) and `aelix_ai/utils/_process_env.py` (this user's pids; one variable of a
  process's environment — `/proc/<pid>/environ`, macOS `KERN_PROCARGS2`).
- `RunningChild` gained `tracking`, `channel_cleans`, `cleanup` and `cleanups`; `SubagentHost` a
  `has_ui`; `_SubagentRuntimeImpl` `_cleanups` and `drain_cleanups()`;
  `AgentsExtension` `drain_cleanups()`. Each defaults to what every caller had.
- `tools/bash.py`'s `Popen` moved into `_spawn_shell`/`_popen_shell`; `_LocalBashOperations.exec`
  gained no branch (the complexity ratchet, ADR-0300).
