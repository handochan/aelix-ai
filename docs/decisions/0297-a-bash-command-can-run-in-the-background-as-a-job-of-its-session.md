# 0297. A bash command can run in the background as a job of its session

Status: Accepted (2026-11-11, #760). **Inert until #611 S3:** everything below is behind
`aelix_coding_agent/jobs/activation.py` `ACTIVE = False` (ADR-0293 §12), and with it off the
`bash` tool is the tool it was (§1).
Date: 2026-11-11
Issue: #760 (#611 slice S2d). Parent: #611. Contract: `.omc/specs/611-s2-contract-r3-2026-11-10.md`
(r3; where it says "as r2", `611-s2-contract-r2-2026-11-10.md` §5 and §7 S2d), with Codex's two
reviews of it beside it (#12, #13, #14 and N12 are this slice's).
Amends: ADR-0293 (a dated note: `client_start`, `restored_note`, `next_offset`). Notes on:
ADR-0238 (the first suspended spawn and the first `preserve_descendants`), ADR-0296 (a background
command's spawn in an armed agent's ledger).
Owner decisions this implements, quoted from the brief:
- 2026-11-07: bash is client 2, "designed together now as the second client to prove the
  contract; implemented in the slice after core + delegation". `-p`/json "waits for tracked jobs
  only". Top-level released helpers are released as today (#232). `/reload` stops background
  jobs.
- 2026-11-08: `job_stop`'s permission stays the same as `bash`'s ("지금처럼 bash와 같게 (권고)").
- 2026-11-10, OQ-B: "무인 실행에만 기본 600초 (권장)" — r3 §10's (c′).
- 2026-11-10, OQ-C: "둘 다 권고대로 (권장)" — `pdeathsig` on the shell only.
- 2026-11-10, the split: "4개로 나눠 게시 (권장)"; 2026-11-11: "네이어서 s2d 진행하세요".
The contract's or the lead's, not the owner's: a command at its limit ends `stopped` with "its time
limit" and the model gets one more turn; the 1 GiB discard; the 8 KiB tail; the default bound 4.
This lane's calls, not the owner's: the output file is named by its own random hex, not the job
id (§3); `background_host` is true for three named modes, not "everything but RPC" (§1); with the
gate off a call that carries the parameter runs in the foreground, as before, and is not refused
(§1); the launch data is a field of its own on `JobSnapshot` (ADR-0293's note). From the first
fix round (Codex's and the verifier's first reviews): what the trailing-`&` scan cannot see is
reported at the command's exit as released, not refused (§1); a drain that cannot prove a complete
read says so and the file captures for 30 s more (§3).
pi: `earendil-works/pi` `b033ade00` (`origin/main`, read 2026-11-11) has no background `bash`: its
tool tracks a shell only while the call runs (`packages/coding-agent/src/core/tools/bash.ts:136`,
`:188`), and `runInBackground` appears only in its MCP extension's server toggles. The shape here
is the job service's (ADR-0293), with the references r3 §10 read for the time limit.

## Context

ADR-0293 built a job service whose clients are delegation, `bash` and extensions; ADR-0295 made
delegation client 1. A model that wants a dev server, a watcher or a long build to keep going while
it works has had no way to say so: `bash` returns when its command does (#232), and a command that
never ends holds the turn until its timeout. #611's S2 contract designed `bash` as client 2 to prove
the service generic (r2 §5), and Codex's review of it found three holes this slice closes: a
restart could not name a command's output file (#12), the `job` tool's cursor counted characters
over a file paged by bytes (#13, N12), and nothing told the tool whether its host was RPC (#14).

## Decision

### 1. The parameter, and where it exists

`bash` lists `run_in_background` only when background jobs are on, the backend is the local one
(no `operations` option), and the host's fail-closed `background_host` answers true when the tool
is built (`tools/bash_background.py` `offer_from`). It is asked again on every call: a tool built
without the option, with a remote backend, or whose host now answers false refuses the call with
one sentence and runs nothing
(`tests/tools/test_bash_background_611.py::test_a_direct_factory_call_without_the_host_capability_refuses`,
`::test_a_remote_backend_refuses_it`, `::test_a_host_that_says_no_at_the_call_refuses`).

The CLI hands every harness it builds, the first and each rebuilt one alike, `background_host`
true for the TUI, `-p` and json only, and `unattended` false for the TUI only
(`cli/entry.py` `_tool_options_for`, called where the tools are built in `_build_harness_options`;
no branch was added there or in `_async_main`). RPC — and ACP, which drives aelix over RPC — never
lists or accepts it until #612
(`::test_rpc_and_acp_never_advertise_or_accept_it`, through `_build_harness_options` from a cwd of
its own). The sweep of every way a `bash` call is built is
`.omc/probes/760/impl/entry-point-sweep.md`: REPL `!`, TUI `!` and RPC `exec` never build the tool,
so they have no parameter.

A command that ends in a bare top-level `&` is refused: `run_in_background` already backgrounds it,
and a shell that exits at once would end the job at once (`::test_a_trailing_ampersand_is_refused`;
`::test_what_counts_as_a_trailing_ampersand` for `&&`, `|&`, quotes, `\&`, a subshell, a comment, a
line continuation after the `&`, quotes nested in `$(…)` and backticks, a backslash inside single
quotes and a backslash the input ends on). The scan reads the command the shell will run, an
embedder's `command_prefix` included. It is a scanner of this grammar, not a shell: what it cannot
see — `eval '… &'`, `bash -c '… &'`, a function — runs, and at the command's exit, when processes
are left in its process group (on win32, in its Job), the notice says they were released as a
foreground command's are (#232), so `job_stop` does not reach them
(`::test_backgrounded_work_the_scan_cannot_see_is_reported_released`). A helper that also left the
group (`setsid`) is not seen; a group that holds only zombies — an exited helper an armed agent's
subreaper has adopted and not reaped — is not reported running
(`::test_a_zombie_left_in_the_group_is_not_reported_running`, Linux docker).

The scan fails safe. The verifier compared it with bash 3.2's own parser (`declare -f` of a function
wrapping the command) over the 1,520 generated and fixed commands bash accepted: 0 false refusals
and 14 misses, every miss a `#` that bash reads mid-word — so not a comment — after a `$(…)`, a
backtick or a line continuation, which the scan takes for a comment and so does not see the `&`
behind it (`.omc/probes/760/finish/scan_diff.py`, `scan-r2-probe.json`). A miss runs the command,
and its exit reports what it left running as released. Re-run on this lane's head with the same
seed: 782 accepted, 0 false refusals, 7 misses of the same shape (`.omc/probes/760/finish/scan-head.json`). A command with a NUL byte is refused before anything is allocated
(`::test_a_nul_in_the_command_is_refused_before_anything_is_allocated`).

**With the gate off** the schema and description are the ones `create_bash_tool(cwd)` built on
`71f837eb`, and a call that carries `run_in_background: true` runs in the foreground as it did
there (`::test_the_parameter_is_absent_with_the_gate_off`, green on base and here). Refusing it
instead would have changed the tool before S3.

### 2. The kind

`tools/bash_background.py` registers kind `bash`, version 1, label `command`, settings key `bash`
(`backgroundJobs.limits.bash`), from the CLI's `if jobs_activation.is_active():` block
(`_with_bash_kind`). Its capabilities: `job_tool_control=True` (so `job_stop` stops it, gated as
`bash` is, 2026-11-08), `blocks_print_exit=True`, `notify=True`, `normal_exit="release"`,
`default_bound=4`, noun "background command". The fifth concurrent command is refused by the
service's admission with "4 background commands already run" and the setting's name, and leaves no
file behind (`::test_the_fifth_is_refused_by_admission`).

A call returns at once with the job id, its output file and how to read or stop it — and, when a
larger `timeout` was clamped, that it was, as the foreground answer says it
(`::test_the_answer_names_the_limit_and_a_clamped_one_says_so`); one notice comes when it ends
(`::test_a_background_command_returns_its_id_at_once_and_one_notice_comes_back`). The job's title
and the notice's `$` line are the model's command, not an embedder's prefix
(`::test_the_title_and_dollar_line_are_the_models_command_not_a_prefix`). `-p` and json wait for
it, and the notice gets its own turn (`::test_print_waits_for_a_background_command`, `run_print_mode`
with a scripted model; `tests/cli/test_bash_background_cli_760.py`, the real `_async_main` in `-p`
and in json, which also pins that the CLI registers the kind).

A launch that fails after anything was acquired is undone before it is reported: when the reader or
the waiter thread cannot start, or the Job, the record or the reader cannot be made, the command is
stopped through the job's evidence legs (S2a) and reaped, its pipe and file closed, and only then is
the call told it was not started; the job ends `failed` with no notice and its slot goes back once
(`::test_a_launch_that_fails_after_its_spawn_leaves_no_command_and_no_slot`, five failure points).
A failed spawn — `OSError`, or the `ValueError` a NUL gives `Popen` — closes the output file too
(`::test_every_launch_failure_closes_the_output_file`, the descriptor count unchanged after three).

### 3. Output, tail and notice

The command's stdout and stderr go as raw bytes to `<tempdir>/aelix-jobs-<user>/<16 hex>.log`: the
directory is created 0700 and refused when it is a symlink, another user's, or open to others; the
file is created `O_EXCL`/`O_NOFOLLOW`, 0600. A command that started keeps its file: aelix never
deletes it (as the foreground spill files are not); a call that was refused or whose launch failed
removes the file it had created. The name is its own random hex because the job id is minted inside
`JobService.start`, after the path has to be known. Past 1 GiB the rest is read and discarded,
with one line in the file saying so, and the notice says how many bytes went
(`::test_past_the_kept_size_the_rest_is_discarded_with_one_line`, at 1000 bytes). A write that
fails ends no read: the command runs on, and the notice says the file holds less
(`::test_a_file_that_cannot_be_written_is_said_and_the_read_goes_on`); a file that cannot be
opened fails the launch before any process exists
(`::test_an_output_file_that_cannot_be_opened_fails_the_launch_before_a_process_exists`). The
directory's mode and the file's 0600 are pinned by `::test_the_output_directory_and_file_are_this_users_alone`
(POSIX; on win32 the directory is the user's own temp directory and no owner check runs).

The reader is `_process_tree._PipeReader` writing to the file on its own thread; after the shell
exits the read drains as #232's does (EOF, or idle 0.1 s with the reader's proof, or 2 s). With EOF
or a proof that it read what the command wrote before it exited, it then detaches and reads on
keeping nothing. Past the 2 s with neither — a reader starved for that long — the outcome, the exit
record, the notice and the last page of `job(action="result")` say its output may be incomplete,
the notice never says "(no output)", and while aelix runs the file goes on capturing until a
proof, EOF or 30 s
(`::test_a_starved_reader_says_its_output_may_be_incomplete_and_the_file_still_gets_it`, the reader
held 2.5 s+; `::test_a_complete_output_is_never_called_incomplete`). The notice carries a status
line, the command's first line (≤ 200 characters), and the end of the output: the last 8 KiB read,
decoded with U+FFFD for what is not UTF-8 — a character split at its front dropped only when the
tail was cut from a longer output (`::test_a_short_invalid_output_keeps_its_leading_bytes`) —
escaped as the frame escapes and cut from the front to a 12 KiB body, so the 16 KiB frame never
cuts it; its footer names the file, its size and `job(action="result", id=…)`
(`::test_the_notice_holds_a_tail_and_fits_16_kib`, with plain, forged-header and forged-tag fill
and 6000 invalid bytes).

### 4. Paging by bytes (Codex #13, N12)

`job(action="result")` and `/jobs result` page the file by bytes: `next_offset = offset + bytes
consumed`, and a page with bytes after its offset consumes at least one. A page never ends inside a
character; when its limit is smaller than the first character it reads up to three more bytes to
complete it. Invalid bytes are consumed as U+FFFD. An incomplete character at the end of the file
is consumed as U+FFFD once the job has ended and left unconsumed while it runs, and then `more` is
false (`::test_paging_never_splits_a_multibyte_character`,
`::test_paging_over_invalid_bytes_advances_by_bytes`,
`::test_paging_from_a_nonzero_offset_advances_by_bytes`,
`::test_a_limit_smaller_than_a_code_point_still_progresses`,
`::test_a_truncated_final_sequence_is_consumed_once_the_job_ended`,
`::test_a_running_jobs_incomplete_tail_is_not_more`,
`::test_job_result_pages_a_real_jobs_file_by_bytes`). A cp1252 child's bytes read as U+FFFD, never
an error (`::test_its_output_decodes_with_replace_on_cp1252`, the portable half of the win32 case).

### 5. Control and the end

`job_stop`, `/jobs stop`, the owner's end and `/reload` stop it: the runner runs the service's
evidence legs on the record it registered (ADR-0293 §5, S2a's identity checks), and the shell and
the processes in its group are gone when the job reads `stopped` with why
(`::test_job_stop_stops_the_command_and_its_group`, `::test_owner_end_and_reload_stop_it`).

At a normal exit what the command left running is RELEASED, as a top-level foreground call's
helpers are (#232): the job ends `done` with its cleanup fact `none`, "released (#232)", and the
helper still runs (`::test_a_released_helper_survives_a_normal_exit_top_level`).

The runner's own record, `aelix.bash_job {v: 1, phase: "exit", status, exit_code, stop_reason,
bytes}`, is written by the service before the job's `cleanup` and `outcome` (ADR-0293 §7), so a
stopped command and one killed from outside stay apart: `stopped` with the stop's reason, or
`failed` with exit code `-9` and no reason, whose notice says it was killed by signal 9 "not by
aelix" (`::test_the_exit_record_tells_stopped_from_killed`).

The background shell is spawned by `_popen_background` — the spawn that adds Linux's `pdeathsig`
and win32's suspended start — through `tools/bash.py` `_spawn_shell`, and the job's `process` fact
records its group (`::test_the_background_shell_is_spawned_by_its_own_popen_with_its_group_recorded`,
through the real tool, service and runner). In an armed delegated agent (ADR-0296) that spawn is in
the ledger: its `intent` and `launch` lines with the shell's group
(`::test_in_an_armed_agent_its_spawn_is_in_the_ledger`), and the job's end retires the group it left
empty (`launch_ledger.after_call`; `::test_the_job_end_retires_its_group_in_an_armed_agents_ledger`,
the retire tick off). Its group is then among those the agent's exit cleanup sweeps (ADR-0296); that
sweep was not run against a background command here.

### 6. Restart (Codex #12)

The job's `start` fact carries `client`: `{v: 1, output: <absolute path>, command_head, cwd,
timeout}`. A restart reads it back as recorded: the restored job's snapshot carries it and
`job(action="result")` pages the original file, and a `lost` or `unresolved` restored notice ends
with "Its output, as far as it got, is in <path>."
(`::test_a_restart_before_the_exit_record_names_the_output_file`). With the exit record and no
outcome, the restart reads how it ended from the record, joined with the `start` fact's `client`,
so its notice keeps the file's path, its paging footer, the command line and the limit's seconds
(`::test_a_restart_after_the_exit_record_keeps_the_output_file_notice`); without one, the service's
OS evidence
decides, report-only: a command still running is `unresolved` and is never signalled
(`::test_restart_reports_a_live_command_unresolved_and_never_kills_it`, with `os.kill` and
`os.killpg` trapped).

### 7. The time limit (owner OQ-B, 2026-11-10)

The background branch runs before the foreground's timeout resolver. In an unattended run (`-p`,
json, a tool built with no `unattended` option) a command without `timeout` gets `bash`'s default,
600 s, from `AELIX_BASH_DEFAULT_TIMEOUT` (0 = none); an explicit `timeout` is honoured up to
`AELIX_BASH_MAX_TIMEOUT` (3600 s; 0 lifts it); the TUI applies no default. A command at its limit
is stopped through the same legs, ends `stopped` with "stopped after reaching its background time
limit", and its notice says so with the limit in seconds
(`::test_an_unattended_background_command_gets_the_bash_default_and_ends_stopped`,
`::test_an_explicit_background_timeout_is_honoured_up_to_the_max`,
`::test_the_tui_applies_no_default`, `::test_the_time_limit_notice_names_its_reason`; each with
the knob set to 1–1.5 s).

### 8. Windows (r2 §5, §8)

The shell is spawned with S2b's `CREATE_SUSPENDED` where `NtResumeProcess` resolves, attached to a
Job created `kill_on_close=True` before it is resumed, with the job's stop latch read first
(ADR-0238's note). At a normal exit `helper_cleanup` calls `preserve_descendants` and closes the
Job, so what the command left runs on; a Job that cannot be preserved is held in a process-lifetime
list, never closed early, and the job's cleanup reads `unverified`, "Windows could not release its
helpers; they end when aelix exits". A stop closes the tree its legs ended. These three branches
are pinned with a stand-in tree on every platform
(`::test_a_normal_exit_releases_through_preserve_then_close`,
`::test_a_failed_preserve_is_held_for_aelixs_lifetime_and_reads_unverified`,
`::test_a_stopped_command_closes_its_tree_without_preserving`); the real Job is windows-latest's
(Limits). A suspended launch that failed and whose end could not be confirmed hands its tree
(`SuspendedLaunchError.tree`) to the runner, which keeps it registered until the barrier confirms
and then closes it, never preserving it (`::test_a_failed_suspended_launch_hands_its_tree_to_the_runner_which_closes_it`,
an injected error over a real POSIX tree). Once the shell is reaped the runner keeps only its exit
code, and `Popen`'s own process handle is closed then rather than when the `Popen` is collected
(`::test_a_reaped_command_lets_go_of_its_popen_and_closes_its_process_handle`, a stand-in handle;
`::test_repeated_short_jobs_hold_no_process_handles` counts the real ones on windows-latest).

The job service keeps a settled job for the rest of the process, so the job keeps nothing that
holds an OS handle once it settles (#766's CI, 2026-11-12): the reader thread and its stream
when the capture ends, the sink once its file is closed (its numbers kept), the tree, the
runner's and the record's, once it is closed or held for aelix's lifetime
(`::test_a_settled_job_keeps_no_reader_sink_or_tree`). Before that, windows-latest counted 140
more handles after 20 short jobs on py3.11 and py3.12 and 20 more on py3.13 and py3.14 (PR run
43068863598). The two numbers are CPython's: up to 3.12 every lock is a kernel semaphore on
Windows (`Python/thread_nt.h` `AllocNonRecursiveMutex` → `Python/condvar.h:144` `CreateSemaphore`,
with `_PY_EMULATED_WIN_CV 1` in `Include/internal/pycore_condvar.h:49-50`, branch `3.12`), and the
job kept seven — the reader thread's started-event and thread-state locks, its pin lock, its held
pipe's lock and its stream's buffer lock (`Modules/_io/bufferedio.c:810`), the sink's and the
tree's — while its thread handle was closed at the start (`Python/thread_nt.h:202-215`). From 3.13
a `_thread.lock` is a `PyMutex` (`Modules/_threadmodule.c:714`, branch `3.13`) and the condition
variables are native (`_PY_EMULATED_WIN_CV 0`, `Include/internal/pycore_condvar.h:43-44`), so no
lock holds a handle, but a thread keeps its handle
until it is joined or its `threading.Thread` is freed (`Lib/threading.py:920`,
`Python/thread_nt.h:188-244`, `Modules/_threadmodule.c:242`): the kept reader's, one per job.
Every kernel handle the job's win32 arm opens — the Job, the suspended launch's own copy, the
members a stop's evidence opens, the `Popen` process handle — is closed once the job settles,
across five jobs on an injected kernel, on a normal exit and on a stop
(`::test_every_win32_handle_a_job_opens_is_closed_when_it_settles`). The windows-latest test
reports the count after each job's spawn, reader start, reap, tree close and settle.

### 9. Linux: `pdeathsig` on the shell only (owner OQ-C)

The shell gets `pdeathsig` (SIGTERM) with #721's original-parent recheck, through
`aelix_ai.utils._parent_death.pdeathsig_preexec`, bound to the spawning aelix's pid before the
fork; nothing below the shell gets it. So when aelix is SIGKILLed the shell ends, and **a command's
own children can outlive it** — the command's group is not signalled by anyone. On Windows the
Job's kill-on-close takes the whole tree instead (§8)
(`::test_its_root_gets_pdeathsig_with_the_original_parent_recheck`, Linux docker: a stand-in
aelix SIGKILLed, the shell gone within 5 s, the shell's `sleep` child still alive).

## Consequences

- The job service has a second client with a different shape from delegation's: a process tree it
  owns end to end, an output file, a normal exit that releases, and a time limit. Nothing in
  `jobs/` names it (`tests/jobs/test_core_is_kind_agnostic_611.py`).
- Three kind-agnostic additions, recorded in ADR-0293's note: `client_start`, `restored_note`,
  `OutputSlice.next_offset`. Delegation's paging is unchanged
  (`tests/jobs/test_client_start_611.py::test_delegation_paging_is_unchanged`).
- `create_bash_tool`'s call moved to `_BashRun.execute` unchanged before anything was added
  (ADR-0300: the factory's nested `execute` counted 19 toward the factory).

## Limits and what S2d does not do

- **Not visible**: `ACTIVE` stays `False`; S3 flips it, and the guide text below goes into
  `docs/guides/tools.md` then.
- **Windows is unmeasured here.** The real Job paths — preserve after a normal exit, a failed
  preserve held, kill-on-close when aelix dies, the suspended spawn under a real resume — have
  windows-latest tests (`::test_a_normal_exit_preserves_its_helpers`,
  `::test_a_failed_preserve_holds_and_reads_unverified`,
  `::test_aelix_dying_kills_it_through_kill_on_close`) that no run of this lane executed.
- **Linux parent death**: only the shell is signalled; its children outlive a SIGKILLed aelix
  (§9, OQ-C). macOS has no `pdeathsig`: there a SIGKILLed aelix leaves the whole command running
  (ADR-0291 limit 3's shape).
- **The trailing-`&` refusal is a scan of this grammar**, not a shell (§1): `eval`, `bash -c`, a
  function or anything else that backgrounds work is not refused; its exit reports the processes it
  left in its group as released. One that also left the group is not seen.
- **A started command's output file is never deleted** and is the user's to clean, as foreground
  spill files are; a refused or failed start removes its own.
- **An unreaped root's win32 handle** (N28): after a bounded reap that did not reap the root, its
  `Popen` handle is left open and the record keeps the live `Popen`. No test reaches it: it needs a
  root that outlives `_STOPPED_EXIT_WAIT` after its kill, which only a win32 process the kill cannot
  end produces.
- **A starved reader**: past the drain's 2 s the file goes on capturing for at most 30 s more, and
  only while aelix runs: a `-p` or json run that ends sooner — its notice turn done — stops the
  capture with it (the live stand-in row with the reader held 3 s ended at 2.9 s with an empty
  file, its notice having said the output may be incomplete). A reader starved past either drops
  what it had not read.
- A restored notice says "it keeps checking" or not by ADR-0293's rule; a restart never stops a
  command it finds running.

## Guide text for the S3 flip (draft; not in the published guide while the gate is off)

> ### Running a command in the background
>
> `bash` can run a command without waiting for it: `run_in_background: true` returns a job id at
> once, the command's output goes to a file of its own, and the model gets a notice when it ends —
> with the end of its output, the file's path and how to read the rest
> (`job(action="result", id=…)`, a page at a time). `job_stop(id=…)` or `/jobs stop` stops it;
> `/new`, `/resume`, `/fork`, quitting and `/reload` stop the session's background commands. At
> most 4 run at once (`backgroundJobs.limits.bash`). `-p` and `--mode json` wait for them. In those
> unattended runs a command without `timeout` is stopped after `AELIX_BASH_DEFAULT_TIMEOUT`
> seconds (600; 0 for none); in the TUI there is no default. A command that ends normally leaves
> what it started running, as a foreground one does, and the notice says when it left any. Not
> available over RPC or ACP yet (#612) or with a remote shell.
