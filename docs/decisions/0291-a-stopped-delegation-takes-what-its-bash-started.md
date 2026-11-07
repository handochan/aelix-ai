# 0291. A stopped delegation takes what its bash started

Status: Accepted (2026-11-06, #691)
Date: 2026-11-06
Issue: #691. Batch B6. Found by the #672/#673 verify (`.omc/specs/672-673-verify-r1-report.md`,
`672-673-verify-r2-report.md`, "PRE-EXISTING").
Narrows: ADR-0276's limit "A delegated child's `bash` grandchild can outlive a stop on macOS" (closed;
dated note there), ADR-0238's statement that the reaper's walk is empty on a host with no `/proc` and
its #220 amendment's reason for Q1 (dated notes there), ADR-0197 finding I2's "`/proc` walk" (the
walk has two sources now; dated note there), and #228's measured shape, an extension's `exec` in
flight under a delegated child on macOS (§7; dated note under ADR-0238's "One loss, worded as a
loss"; the issue itself is the owner's to close).
Relates: ADR-0277 D14 (a stop from the parent leaves the child no time to unwind, so what D14
measured holds; dated note there), ADR-0290 limit 10 (what left the tree is out of reach),
ADR-0235 (pi is a reference, not a target), #620.
pi: `earendil-works/pi` `4ffe32c6f`. `packages/coding-agent/examples/extensions/subagent/index.ts`
sends the child SIGTERM and SIGKILL 5 s later; the child's SIGTERM handler kills the detached `bash`
children it tracked (`packages/coding-agent/src/modes/print-mode.ts`, `killTrackedDetachedChildren`).
There is no descendant walk, so a pi child that is SIGKILLed leaves them.
Tests: `tests/agents_ext/test_delegated_child_orphans_691.py` (a real child, three doors, a command
the shell waits for and one it backgrounded with `(cmd &)`), and in
`tests/agents_ext/test_print_channel_spawn.py` the walk's own cases
(`test_descendant_pids_names_every_generation_deepest_first`, `test_the_macos_walk_*`,
`test_a_failed_query_costs_the_macos_walk_only_that_process_s_children`,
`test_the_walk_on_windows_neither_reads_proc_nor_loads_libproc`), the two doors on a stub child
(`test_a_cancelled_delegation_takes_the_command_its_bash_runs`,
`test_a_stopped_delegation_takes_the_command_its_bash_runs`),
`test_the_eager_abort_does_not_walk_a_reaped_child` and `test_the_reap_leg_does_not_walk_a_reaped_child`
(§3), `test_a_stopped_delegation_takes_what_its_bash_left_in_the_shells_group`,
`test_the_eager_abort_reads_the_walks_groups_first_and_kills_them_last` and
`test_the_reap_leg_hands_its_reaper_the_groups_of_its_walk` (§5),
`test_a_second_cancel_escalates_a_reaper_already_in_its_grace` and
`test_a_stop_escalates_a_reaper_already_in_its_grace` (§6); in
`tests/agents_ext/test_reaper_tree_win32.py`, `test_posix_reap_reads_the_groups_with_its_walk_and_kills_them_after_the_pids`
and `test_pids_handed_in_without_their_groups_get_no_group_looked_up` (§5),
`test_reap_escalates_on_its_event_inside_the_grace`,
`test_a_grace_leaves_no_waiter_on_the_event_however_it_ends` and
`test_a_reaper_cancelled_in_its_grace_escalates_instead_of_ending` (§6), and
`test_win32_reap_takes_no_walk_on_its_tree_arm` (§1).

## Context

Measured on `main` `679e5e5c`, macOS, a real TUI in a pty and real child processes against a scripted
model (the quit and `/agents run` rows on that commit's sources under `PYTHONPATH`): the parent
delegates, the child's `bash` runs `echo …; echo X > note.txt; sleep <marker>`, and the parent is
stopped while the `sleep` runs. `ps -axo pid,ppid,pgid,command` afterwards:

```text
TUI Esc                   left: ['68207     1 68207 sleep 601.1']
TUI Esc Esc               left: ['68638     1 68638 sleep 601.2']
TUI SIGTERM (exit 0)      left: ['68997     1 68997 sleep 601.3']
TUI /agents run, Esc      left: ['59073     1 59073 sleep 662.1']
TUI Ctrl+C, then quit     left: ['13933     1 13933 sleep 672.1']
-p SIGTERM (exit 143)     left: ['69829     1 69829 sleep 611.1']
-p SIGINT (exit -2)       left: ['70069     1 70069 sleep 611.2']
--mode json SIGTERM       left: ['70293     1 70293 sleep 611.3']
--mode rpc SIGTERM        left: ['71514     1 71514 sleep 612.1']
```

Every way a human stops a delegation, and the command outlived the parent's own exit. With a real
model (OpenRouter `openai/gpt-4o-mini`) the same: `-p` and one SIGTERM, and Esc in the TUI, each left
its `sleep` with PPID 1. On Linux (docker, `python:3.12-bookworm`) the seven rows other than
`/agents run` and the quit left nothing, before this change and after.

Why. Every one of those doors kills the child with no grace. Esc, Esc Esc, and a signal under
`-p`, json or rpc cancel the turn, which reaches `PrintChannel._eager_abort`: a SIGKILL.
`SubagentRuntime.stop`, `stop_all` and the extension teardown (quitting) reach
`reap(eager_kill=True)`: a SIGTERM and, on the same synchronous run, a SIGKILL. A signal to the TUI
aborts the turn and tears the session down, so it meets one of the two. Either way the child's own
`dispose()`, which would end its `bash` command, gets no time to. It can start: a parent descheduled
between `reap`'s SIGTERM and its SIGKILL lets the child's SIGTERM handler run (measured in the #691
review with a 20 ms pause injected there and a stand-in child: its `dispose()` ran in 5 of 5 trials,
and in 0 of 5 without the pause). Nothing below depends on whether it does. The command leads a
session of its own (`tools/bash.py` spawns with `containment_spawn_kwargs(new_session=True)`), so
the group kill on the child's group (ADR-0238's Q1) does not reach it either. The one leg that can
name it is the descendant walk (ADR-0197 finding I2), and the walk read `/proc`, which macOS does
not have.

## Decision

### 1. The walk has a macOS source

`reaper.descendant_pids` asks the kernel on macOS: `proc_listpids(PROC_PPID_ONLY, pid, …)` from
libproc, one call per process in the tree, breadth first and returned deepest first, as the `/proc`
walk is. `/proc` is still tried first, and Windows has neither source and returns `[]` as before; its
tree is the Job Object. A call that fails is no children for that process, and a libproc that cannot
be loaded is an empty walk, which is what macOS had before. A full answer is asked again at twice the
size, because the kernel fills the buffer and drops the rest without saying so.

Every door gets it: `reap`'s own snapshot (`abort_child`: stop, `stop_all`, the extension
teardown), `PrintChannel._reap` and `_eager_abort`, and the same two in `rpc_channel`. Each takes the
walk before any signal, while the child is alive, as ADR-0197 requires. Measured cost on the owner's
Mac: 0.5 ms for a child with a shell and a command under it; walking all 1095 of launchd's
descendants, a bound no real walk comes near, 12 ms. On the win32 tree arm `reap` takes no walk at
all, since the job is the descendant set and `kill_tree`'s job arm ignores one; until the #691
review it took one first and dropped it, which made the module's "the win32 arms never consult the
walk" true of `kill_tree` only.

### 2. Not a SIGTERM first

The issue offered a second direction: send the child a short SIGTERM first and let its `dispose()`
end its tools, which is pi's shape. It was measured as a scratch prototype on the base, the walk
still empty on macOS: `abort_child` and `_eager_abort` start a detached `reap` with a 1 s grace
instead of killing at once. Its rows next to this decision's, same driver, macOS:

```text
                                   SIGTERM first (base walk)   the walk (this ADR)
TUI Esc / Esc Esc / SIGTERM, -p    left: [] on all four        left: [] on all four
-p SIGTERM, parent's exit          0.31 s                      0.14 s
child session, 'Command aborted'   1 row                       0 rows
(sleep X &); sleep Y, Esc          both gone                   both gone (§5; walk alone: X left)
stub child that ignores SIGTERM    grandchild left             grandchild gone
```

Not taken:

- It changes what a stopped child leaves behind: with time to act, the child's turn unwinds
  (ADR-0290) and writes `Command aborted: …` into its session, the row above. A resume then shows
  its model that instead of ADR-0277 D14's `No result provided`, which
  `test_agent_resume_real_child_610.py` pins. That is a decision about resume, not a fix to an
  orphan.
- It reaches only what a cooperating child knows about. A child that is wedged or ignores SIGTERM
  still leaves its commands (the stub row: `test_a_cancelled_delegation_takes_the_command_its_bash_runs`
  and its `stop` sibling fail on the prototype), and that is the child the escalation exists for
  (ADR-0197).
- It costs every stop the child's cleanup, up to the grace, and the parent's exit waits for it,
  because `stop_all` joins the reapers: 0.31 s against 0.14 s for `-p`.
- The eager legs exist because a second Esc must not wait (finding B1). With a grace on the first
  Esc, the second no longer reaches the reaper at all: the turn it cancels has already let go.

The fourth row was the one thing the prototype reached that the walk alone did not: a member of the
command's own process group whose parent already exited, which the child's `bash` tool ends by group
on its way out and no walk can name. §5's group kill reaches it with none of the costs above, through
the shell that leads that group, so only while that shell still runs; in that row its `sleep Y`
keeps it running. What a shell that has already exited left there is limit 2. What a
SIGTERM first still has that this does not is the child's chance to write its own session on the way
out; whether a stop should give it that is the owner's call and its own issue.

### 3. A reaped child is not walked

`_eager_abort` and `_reap`, in both channels, walk only a child whose `returncode` is still `None`.
A cancel can land inside `_drain_after_exit`, with the child dead and reaped, and the post-EOF bound
can race the child's exit into `_reap`. Its descendants were reparented when it died, so a walk from
its pid names nothing of its own, only the children of whoever got the pid next. From
`_eager_abort` `kill_tree` would SIGKILL them; from `_reap` the snapshot was taken and then dropped,
because `reap` returns a reaped child's status before it signals anything (found in the #691 review:
a stale walk, not a kill). The `/proc` walk already had that exposure on Linux; #691 gave macOS a
walk, so the guard comes with it.

### 4. Q1 stays, and its guards blind the walk

ADR-0238's #220 amendment added the group kill after the walk because the macOS walk was empty. It
stays, for a group member the walk did not name: one the child spawned after the snapshot (on the
grace leg the snapshot precedes the SIGTERM, and a SIGTERM-ignoring child keeps working for the whole
grace), or a walk that failed. Its two guards, `test_a_non_setsid_grandchild_dies_on_the_escalation`
and `test_a_cancelled_delegation_takes_its_non_setsid_grandchild`, bit on macOS only, where the walk
was empty. Now that the walk names their grandchild there too, they blind it, so they bite on Linux
and macOS alike: deleting `reap`'s `tree.hard_kill()` or `_eager_abort`'s `kill_group_if_live` turns
each red (measured on macOS). `test_rpc_batch_cancel_reports_every_child_after_slow_pipe_cleanup`
needs a setsid helper that outlives the cancel, and blinds the walk for the same reason (measured: the
helper alive after the turn on the base, dead on #691 unblinded, alive blinded).

### 5. The groups the walked processes lead go too

After `kill_tree` has signalled every pid the walk named and the child, each walked process that
leads its own process group (`getpgid(pid) == pid`, `reaper.group_leaders`) gets one
`killpg(SIGKILL)`: on the eager legs (both `_eager_abort`s, `reap(eager_kill=True)`) and on the
escalation after a grace. The `bash` tool's shell leads its own group, and `(sleep X &); sleep Y`
leaves X there with PPID 1, where no walk can name it. X is reached through that shell, so only
while the shell runs: a shell that has exited is not walked, and nothing walked leads its group any
more (limit 2). The pgids are read in the same snapshot as
the walk, before any signal, while every pid in it was just seen alive, and never again: on the grace
leg the kill comes a grace later, and a pgid read then may be a stranger's. `reap` takes them with
its own walk; `_reap` and `_eager_abort` take them with theirs and pass them in, and a list of pids
handed to `reap` without its groups kills no group. `_drain_after_exit` passes none: its pids come
from a pipe scan of a child already dead, on the success path, where a group kill is ADR-0238's
revision-1 `close()` again. The child's own group is still Q1's (§4), with Q1's guards.

Why it reaches nothing else. A process can join only a group of its own session, and no process can
join a session it was not born in. The delegated child leads a session of its own
(`start_new_session=True`), so every session below the walk was started inside the child's tree, and
so is every member of every group named here. The number is the residual: a leader that exits and is
reaped after the read, in a group whose members have all gone too, frees it, and a new process that
took it and made itself a group leader before the kill would be signalled with its group (limit 4).

Measured on the owner's Mac and in docker (`python:3.12-bookworm`, `--init`), a stand-in child in
its own session running a `bash` in a new session, stopped through `PrintChannel._eager_abort` and
`reap(eager_kill=True)`, survivors read by marker 1.5 s later, on 679e5e5c, on #691's first commit
(the walk alone) and on this one:

```text
                                  679e5e5c     walk alone    walk + groups
(sleep X &); sleep Y, macOS       3 left       1 left (X)    0
(sleep X &); sleep Y, Linux       1 left (X)   1 left (X)    0
fork loop, macOS, stops with one  6 of 6*      6 of 26       1 of 26
fork loop, Linux, stops with one  5 of 6       18 of 26      0 of 26
nohup sleep X & exit 0            1 left       1 left        1 left on macOS
```

\* every sleep the loop had started, about 340, since the macOS walk was empty there. The fork loop
is `while :; do sleep M & sleep 0.005; done`; the survivor was each time one `sleep` in the loop
shell's group. The one left with the group kill (macOS) is most likely a `sleep` the shell was still
forking when it was killed, linked into the group after the `killpg`; not shown directly. In the real
product, macOS, scripted model: `(sleep X &); sleep Y` and Esc in the TUI, and `-p` and SIGTERM, left
the shell and both sleeps on 679e5e5c, X on the walk alone, and nothing here.

What a shell that has already exited left in its group is out of reach, whatever put it there:
nothing the walk names leads that group any more. So `cmd &`, `(cmd &)` and `nohup cmd &` from a
`bash` call that has returned are one case; `nohup` is not a different one, since it leaves the
process in its group. Measured in the #691 review with a stand-in that reaps its shell, as the `bash`
tool does, on 679e5e5c, the first commit and this one, macOS and Linux, on all three kill legs:
`(sleep X &); exit 0` and `nohup sleep X & exit 0` each left X with PPID 1, its pgid the dead shell's
pid. In the real product, macOS, scripted model: a child whose first `bash` call was `(sleep X &)` and
whose second was `sleep Y`, stopped during the second with Esc in the TUI or with `-p` and SIGTERM,
left X on the first commit and here. The table's Linux `nohup` row is not that case: its stand-in
never reaped its shell, so the walk named a zombie leader and that group went with it.

### 6. A reaper already in its grace escalates at once

`reap` read `eager_kill` once, on entry. A reaper already waiting out its grace therefore ignored the
two requests that mean "stop waiting": a `stop`, whose `abort_child` reuses a running reaper rather
than build a second, and a second cancel, whose `_eager_abort` set `row.eager_kill` and could not
reach the descendants itself once the root had died to the SIGTERM (§3). Found in the #691 review and
measured on #691's first commit by driving either channel's `_reap` directly: a `setsid` descendant
holding the child's stdout outlived the second cancel by the rest of a 1.5 s grace, and `abort_child`
returned after 1.45 s of it. 679e5e5c had the same (docker: the escalation at 1.51 s, the stop at
1.45 s; macOS: the stop at 1.45 s, and the descendant outlived the reaper altogether, its walk being
empty). Now: 0.05 s and 0.00 s, on both.

Which channel's reaper it is decides which shapes the product reaches:

- **The print channel's**, the one the bundled runtime builds, runs a grace only on the post-EOF
  bound, for a child whose pipes have both reached EOF and that does not exit. The stop is
  reachable there: through `PrintChannel.run` with the default 5 s grace, on macOS, `abort_child`
  returned after 4.70 s on the first commit and after 0.001 s here. The shape above, a root dead to
  the SIGTERM and a descendant still holding its stdout, cannot arise there, because that bound
  needs both pipes at EOF; an Esc in that grace finds the root alive, so `_eager_abort` walks it and
  ends the child and its `setsid` descendant at once (0.004 s on the first commit, 0.005 s here,
  measured the same way). That Esc still needs the event: `_eager_abort` leaves Q1 to the reaper in
  its grace, and the event is what makes that reaper run it now. Measured in the last review through
  `PrintChannel.run`, macOS, a stub child that ignores SIGTERM after double-forking a member of its
  own group with no `setsid` (PPID 1, so no walk names it): that member died 0.020 s after the Esc
  here, and with print's `_eager_abort` not setting the event it was alive 7 s later, its root dead
  by then.
- **The rpc channel's** runs from `_shutdown`, for a child that did not exit on stdin EOF, and there
  the shape above arises. `RpcChannel` has no instance in the product (limit 6), so the product does
  not reach that shape.

The row carries an `asyncio.Event`, `RunningChild.escalate`. `_eager_abort` (both channels) and
`abort_child` set it with `eager_kill`, `_reap` hands it to `reap`, and the grace races it beside the
release of the child's pipes: set, the grace ends and the escalation runs at once, with the snapshot
taken before the SIGTERM. Set before `reap` starts, it is `eager_kill`. The reaper is still detached
and shielded and still survives its own cancellation (finding B1), the snapshot still precedes any
signal, and the win32 arm escalates on it too. `_eager_abort` still leaves Q1 to a running reaper,
which now runs it at once.

### 7. #228's measured shape

#228 is the same gap for an extension's `exec` in flight under a delegated child: since #221 that
command leads a session of its own, so on macOS nothing reached it when the child was stopped. It is
the child's own child (the `exec` runs on a worker thread of the child), so the walk names it and what
it started: here `sleep A` too, a child of the shell while that shell runs (`sleep B` keeps it
running). §5 takes what a subshell of it put in the background, and an `exec` that has returned is
limit 2. Measured with a real extension, a `tool_call` hook
that awaits `api.exec("sh", ["-c", "sleep A & sleep B; :"])`, loaded into a real delegated child
through its profile's `extensions:`, scripted model, macOS: `-p` and SIGTERM, and Esc in the TUI, left
the shell and both sleeps with PPID 1 on 679e5e5c and nothing on #691 (`-p` on the first commit:
nothing). What stays is a child that nothing walks: its parent SIGKILLed on macOS (limit 3).

## Limits

1. **A process forked between the walk and the kill is missed,** and so is one forked between two of
   the walk's reads, unless it is in the group of a walked process that leads one (§5), as a shell's
   next command is. The window is the walk itself plus the kill, and on macOS the walk makes one call
   per process: a fork loop of about 170 children took 5 to 12 ms to stop. It is the race ADR-0197
   accepts for the `/proc` walk. Measured with the group kill: 1 stop in 26 left one `sleep` of a fork
   loop on macOS, and 0 in 26 on Linux (§5).
2. **What a shell that has exited left behind is not reached, nor is a new session.** §5 reaches a
   background process through the shell that ran it, so `(cmd &)` is reached only while that shell
   still runs. A `bash` call that returned after `cmd &`, `(cmd &)` or `nohup cmd &` leaves its
   helper running: the `bash` tool returns when the command does (the CHANGELOG's "A command that
   backgrounds a server now comes back when the command does", and on Windows `kill_on_close=False`
   in `tools/bash.py`, Pi #8225). Once that shell has exited, the helper has PPID 1 and its pgid is
   a dead shell's pid: nothing the walk names leads its group, and a stop leaves it, on macOS and
   Linux (measured in §5). `nohup` changes nothing here; it does not leave the group. A daemon's
   double fork with its own `setsid` is out of reach even while its shell runs: a new session, with
   no parent chain back to the child. The same on Linux (ADR-0290 limit 10).
3. **A parent that is SIGKILLed walks nothing.** On Linux `pdeathsig` sends the child SIGTERM and its
   own cleanup ends its commands; macOS has no `pdeathsig`, so the child and its commands run on
   (ADR-0197 finding I1, unchanged).

   **Amended 2026-11-07 (#721, #611 S1a; Codex round 5 finding 13): the hook rechecks its parent.**
   `PR_SET_PDEATHSIG` watches the child's parent at the moment it is armed, between `fork` and `exec`.
   A parent that died before that had already handed the child to init or to a subreaper, the signal
   was armed against that process, and the delegated child ran on. `pdeathsig_preexec()` now binds the
   spawning aelix's pid, read before the fork, and after arming the hook exits the child with
   `os._exit(143)` when `getppid()` is not that pid — compared with the pid, never with 1, which a
   subreaper whose pid is not 1 passes (codex-rs `utils/pty/src/process_group.rs:25-37`,
   `set_parent_death_signal`). It exits rather than raising SIGTERM as codex-rs does: a forked Python
   child keeps the parent's SIGTERM handler until `exec`, so the raise only trips it (measured on macOS
   3.12 under a `loop.add_signal_handler(SIGTERM)` parent: a raising hook let `sh -c 'echo EXECED'`
   exit 0 printing `EXECED`; `os._exit` ended it with 143). Measured on Linux (OrbStack docker,
   `python:3.12-slim`, `--init`), a middle process SIGKILLed while its child waits in the pre-exec
   step, then the hook: `main` let the child exec under init (PPID 1) and under a subreaper with pid 7
   (PPID 7); a recheck against 1 caught init and missed the subreaper; the recheck against the
   captured pid stopped both before `exec`, and the subreaper reaped exit 143. Today's delegation
   spawns get it, the print channel and the RPC channel, through `pdeathsig_preexec()`; the `bash`
   tool's own spawns are #611 S2's. Tests: `tests/agents_ext/test_pdeathsig_611.py` (injected
   `getppid`, on every host; the real race, Linux only).

   **Fix round 1 of #721 (Codex findings 6 and 7).** Two more things the hook did between `fork`
   and `exec`:
   - *A parent dying after the recheck was swallowed.* The child keeps the parent's SIGTERM handler
     until `exec`, so the kernel's parent-death SIGTERM only tripped it and `exec` ran. The hook now
     puts a handled SIGTERM back to its default before it arms (an ignored one stays ignored, as
     `exec` would leave it; `exec` resets a handled one anyway, so the command starts as before).
     Measured in Linux docker (`python:3.11/3.12/3.14-slim`, `--init`) with Codex's
     `postcheck_probe.py` — a middle process with an asyncio SIGTERM handler, SIGKILLed after its
     child armed and rechecked: on `ed76ea76` the child exec'd under the new parent on all three; now
     the kernel's SIGTERM ends it before Codex's own injected signal can be sent, and it never execs.
     The same with the spawn made from an event loop on a worker thread and the parent really killed
     after the recheck (the verify round's `race_r2.py`, path `loopthread`, mode `after`, rows in
     `.omc/specs/721-verify-r2-probes/docker/race-312.out` and `race-311-314.out`; under pid 1, `--init`
     and a subreaper): `ed76ea76` exec'd under PPID 1 or 7 (3.12), now it never execs on 3.11, 3.12 or
     3.14 and is reaped with -15 where the reaper is observed. Tests:
     `test_a_parent_killed_after_the_recheck_still_ends_the_child` (Linux only, the real kernel path)
     and `test_a_sigterm_after_the_recheck_ends_a_child_whose_parent_handles_sigterm` (any POSIX
     host, the signal injected).
   - *It loaded libc there.* `ctypes.CDLL("libc.so.6")` and the `prctl` lookup ran in the child,
     where a lock another parent thread held at the fork is held for ever; `fc8f3048` already did
     this, so it is older than #721. `pdeathsig_preexec()` now loads and looks it up in the spawning
     process and hands the hook the resolved function. Measured in the same images with Codex's
     `preexec_probe.py` widened to record the child's audit events (an audit hook that takes a lock a
     parent thread holds): on `ed76ea76` the child stopped at `ctypes.dlopen` and the parent hung in
     `Popen` on all three versions; now it raises no `ctypes.dlopen` and execs
     (`test_the_hook_loads_no_library_after_the_fork`).

   What stays is CPython's own: `preexec_fn` runs Python in the child, which Python documents as
   unsafe with threads in the parent. The hook still calls `signal.getsignal`/`signal.signal`, the
   `prctl` foreign function and `os.getppid` there; on 3.14 that `prctl` call raises a
   `ctypes.call_function` audit event in the child (measured in the probe above), so an audit hook
   that blocks could still stop it. No native launcher was built.
4. **The PID-recycling residual of ADR-0238 now has a macOS site too, and a group form.** §3 narrows
   it where the child is known reaped; the window the asyncio child watcher leaves stays. §5 sends a
   group what used to go to one pid, so a recycled number that is also a new group leader's would be
   signalled with its group: on the eager legs the read and the kill are microseconds to milliseconds
   apart, and on the escalation leg a grace apart, as the pids of the snapshot always were.
5. **`pipe_holder_pids` is still `/proc` only.** A process that holds a finished child's stdout is
   evicted on Linux and not on macOS; there the drain is bounded and the pumps are cancelled. Not part
   of this change.
6. **`RpcClient.stop()` walks nothing on any platform.** A command its `--mode rpc` child's `bash`
   runs survives that client's escalation, on Linux as on macOS. `RpcChannel`'s own legs walk; the
   bundled runtime does not select that channel.
   *2026-11-07 note (#360): a `RpcClient.start()` that does not return now kills its child's group
   before it re-raises (ADR-0201, `## Amendment (2026-11-07, #360)`), and that kill takes no walk
   either: the group or the job, as `stop()`'s escalation (on win32 `taskkill /T` follows live
   parent links). It is sent whether or not the root's `returncode` is in: §3 keeps a reaped root
   out of the WALK, and a reaped root's group is §4's, with §4's bound. A real `--mode rpc` child
   had nothing under it at the grace's first step (macOS, 2 runs) nor at 20, 50 and 95 ms and
   0.5, 2 and 5 s (macOS, 2 runs each, #360's verification).*
7. **Windows is unchanged and was not run here.** The win32 arms never consult the walk (§1), there
   are no groups to read there (`group_leaders` is `[]`), and §6's event ends a win32 grace as it
   does a POSIX one; `test_the_walk_on_windows_neither_reads_proc_nor_loads_libproc`,
   `test_win32_reap_takes_no_walk_on_its_tree_arm` and the win32 row of
   `test_reap_escalates_on_its_event_inside_the_grace` run there.

## Verification

After the change, on the same branch, the macOS table above has no survivor on any of its nine
rows (`ps` by marker, 1.5 s after the abort line or the exit), and the real-model `-p` SIGTERM and TUI
Esc runs left nothing. Linux docker: the seven scripted rows, before and after, left nothing. The
tests named above fail on the base on macOS (the real-process ones by their survivor) and on Linux
(the injected-libproc and reaped-walk ones), except two that pass there by design:
`test_the_macos_walk_is_empty_when_libproc_cannot_be_loaded` and
`test_the_walk_on_windows_neither_reads_proc_nor_loads_libproc` pin fallbacks, and each goes red on
its own mutant. The real-process walk cases pass on the Linux base, where `/proc` always named the
command. The first review round's §3 (`_reap`), §5 and §6 cases fail on #691's first commit too, by
their survivor, their timeout or their log, and 16 of 16 mutants of those three sections turned one
red; the cases the second review added fail there only on the new `escalate` keyword or the missing
`group_leaders`, so each is pinned by its own mutant instead, below. Of the second
review's seven mutants that left all of `tests/agents_ext/` green, the five the product can tell
apart each turn a case red now: the child's own group among the leaders, `RpcChannel._reap` without
its groups, and groups looked up from pids handed in without them (§5); the grace's `escalate` waiter
left uncancelled, and `except Exception` in place of `BaseException` in the grace, a gap older than
this issue (§6). The other two cannot change what the product does: an event already set when `reap`
starts not read as `eager_kill` (every site sets `row.eager_kill` with it, before any reaper is
built), and `group_leaders` not `[]` on win32 (the win32 walk is always `[]`). Live after the review round,
macOS, scripted model: TUI Esc, Esc Esc and SIGTERM, `-p` SIGTERM and rpc SIGTERM left nothing, and
the `(sleep X &)` and #228 rows are in §5 and §7; a child whose earlier `bash` call had already
returned after `(sleep X &)` left X, on the first commit and here (§5, limit 2). Evidence is local
and untracked: the lanes' reports and probes under `.omc/specs/691-*` and `.omc/probes/691/`.
