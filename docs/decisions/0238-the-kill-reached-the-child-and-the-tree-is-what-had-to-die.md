# 0238. The kill reached the child, and the tree is what had to die

Status: Accepted (2026-09-05).
Date: 2026-09-05
Supersedes/relates: ADR-0197 (the `aelix_agents` reaper, whose finding I2 —
"a `/proc` walk and not `os.killpg`" — this ADR **reconciles rather than
reverses**: the descendant walk stays the reaper's job, and nothing here
re-adopts `killpg` as a substitute for it),
ADR-0201 (the RPC delegation channel whose `stop()` is the first adopter),
ADR-0102 (the subprocess hooks whose timeout ladder is the second),
ADR-0140 (`models.json`'s `!command` value resolver, the third),
ADR-0235 (Pi is a reference, not a target — Pi's own `rpc-client.ts` `stop()`
is still root-only with no platform branch, so the divergence below needs no
justification; Pi's `taskkill.exe` path fix is adopted because it is right).
Issue: #202 (this ADR), #207 (the two Windows `skipif`s it reactivates),
#110 (the Windows port epic it closes a leg of).
Design spec: `.omc/specs/202-design-2026-09-05.md`.

Every teardown path in this repository outside `aelix_agents/` ended a
**process**. Three of them exist to end a **tree**: a delegation the user
aborted, a subprocess hook that ran past its timeout, and a `!command` in
`models.json` that hung. What they killed was the root, and the root is
routinely the least interesting thing in the tree — a `sh` that forked a
pipeline, a `cmd.exe` that launched the real command and is now waiting on it,
an rpc child whose grandchildren are the actual work.

This is not a Windows ADR. Windows is where it is total — `start_new_session=True`
is accepted and silently ignored there (CPython names the parameter
`unused_start_new_session`), so every descendant of every one of these sites is
orphaned on every teardown. But the headline measurement was taken on the
owner's macOS box, against `main` 39549b9, on the path a user hits by pressing
escape during a delegation.

## What was actually broken, measured on `main` 39549b9

`scratchpad/orphan_probe.py` — an `RpcClient` whose child spawns one
grandchild, then `stop()`:

```
grandchild alive after stop(): True
```

`stop()` ran its full ladder — `terminate()`, poll 1 s, `kill()`, poll 5 s —
and the grandchild was still running. There is no bug in the ladder. Both rungs
are `Popen` methods, and both address a pid.

| site | spawn | teardown today | POSIX effect | Windows effect |
| --- | --- | --- | --- | --- |
| `extensions/subprocess_hooks.py:149-156, 175-183` | `create_subprocess_shell(cmd)` — no group | `terminate()` → 1 s → `kill()` → 5 s | the shell is in **our** group; only the shell is signalled. Measured: `sh -c "sleep 6 \| cat"` leaves `sh`+`sleep`+`cat` behind, while `sh -c "sleep 5"` is fine because `sh` execs and there is no pipeline | `cmd.exe /c …` gets `TerminateProcess`; the command it launched is orphaned every time |
| `rpc/rpc_client.py:416, 476, 486` | `create_subprocess_exec(..., start_new_session=True)` | `terminate()` → poll 1 s → `kill()` → poll 5 s | the child leads a session, and `terminate()`/`kill()` still hit only the child. `Popen.send_signal` also `poll()`s first (bpo-38630) and can reap behind asyncio's watcher, which then reports 255 — measured `{255: 60}` at `reaper._signal_child` | `start_new_session` ignored; `terminate()` is uncatchable; no grace ever elapses (0.047 s / 0.062 s, #207); every descendant orphaned |
| `oauth/_resolve_config.py:46-51, 80, 90` | `Popen(["sh","-c",cmd])` — no group | `kill()` on timeout / 1 MiB overflow | `sh` only; pipelines survive | MSYS `sh.exe` is an exec stub — killing it orphans the command |

Two rows that are **not** this change, and were measured to say so.
`mcp/client.py:232` is already contained: the SDK spawns stdio servers with
`start_new_session=True` (`mcp/client/stdio/__init__.py:256`) and, on Windows,
puts them in a `win32job` job with `KILL_ON_JOB_CLOSE`. And
`extensions/api.py:2044-2059`, `cli/extension_catalog.py:472`,
`tui/completion.py:250` use `subprocess.run(timeout=)`, whose CPython
implementation kills the root only and then, on Windows, follows the kill with
an **unbounded** `communicate()`. That is outside #202's grep
(`proc.terminate()`/`proc.kill()`) and is #221.

## Two facts from Pi

Pi (`earendil-works/pi`) at its real `origin/main` 47236c8, 2026-09-04. The
local clone is three months stale, and that is what made the first adversarial
review's finding against the second fact wrong; the fact was re-verified against
the real `origin/main` rather than the clone, and it stands.

- **Pi #9129.** `taskkill /F /T /PID` follows *live* parent links only. MSYS
  bash runs each pipeline stage through a short-lived subshell, so by the time
  the kill runs the leaves have a dead parent. `taskkill` kills the bash layers,
  **exits 0**, and `find`/`xargs`/`head` keep running for another 70 minutes.
  The reporter's expected fix is a Job Object with `KILL_ON_JOB_CLOSE`. Pi has
  not adopted one — there is no `JobObject` anywhere in the tree at 47236c8.
- **Pi #6596 / #8560.** `spawn("taskkill")` fails `ENOENT` when `System32` is
  not on `PATH`. Pi fixed it in `7af2d27d` (2026-08-26): `join(SystemRoot ??
  "C:\\Windows", "System32", "taskkill.exe")` in `utils/shell.ts:221` and in
  `harness/env/nodejs.ts`. Our `_taskkill_tree` used the bare name under
  `suppress(OSError)`, so the same environment gave us a **silent no-kill**
  rather than a crash. Adopted.

Pi's `rpc-client.ts` `stop()` is root-only with no platform branch at HEAD
(lines 145-167 at 47236c8; the line numbers move, the shape does not).
Nothing to port for the headline case.

## Decision

**One containment primitive, in the package everything else can import** —
`packages/aelix-ai/src/aelix_ai/utils/_process_tree.py`. `aelix-ai` is the
bottom of the import direction and `_resolve_config.py` already lives there.
`aelix_coding_agent/tools/_process_tree.py` becomes a re-export shim so
`bash.py`, `_subprocess.py` and their tests keep their imports.

**POSIX: the tree is the process group we created.** Spawn with
`start_new_session=True` (the rpc child, unchanged) or `process_group=0` (hooks
and `!command`, new). Kill with `killpg` on the pgid captured at attach.

**Windows: the tree is a Job Object.** Spawn with
`creationflags=CREATE_NEW_PROCESS_GROUP`; immediately after the spawn returns,
assign the child to a job created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
where the site asks for it. Hard kill is `taskkill /T /F` (best effort, while
the root is alive) and then `TerminateJobObject`. Soft kill is
`CTRL_BREAK_EVENT` to the child's process group; the rpc child grows a
`SIGBREAK` handler, the Windows twin of its SIGTERM one. `soft_kill` reports
whether the event was actually delivered — `GenerateConsoleCtrlEvent` fails when
there is no console to aim at — and a caller that gets `False` goes straight to
the hard kill instead of waiting out a grace no signal could end.

**The two are not symmetric, and the ADR says so instead of claiming they are.**
Children of a job member inherit membership and we set no `BREAKAWAY_OK`, so
the job reaches a descendant that made its own session *and* a descendant whose
parent has already exited — the #9129 shape. A POSIX process group reaches
neither of those two things. **Windows containment here is strictly stronger
than POSIX's.**

**`close()` is a release, not a kill.** On POSIX it does nothing at all. On
win32 it is `CloseHandle(job)`, which ends remaining members only for a tree
attached with `kill_on_close=True` — the rpc child ("one child per task"), and
not hooks or `!command`, which keep today's success-path behaviour on every
platform.

**Never `killpg` a group whose leader is not the pid we spawned.** `attach()`
verifies `os.getpgid(pid) == pid`; a caller that forgot the spawn kwargs has
its child in *our* group and gets `contained=False`, which downgrades every
kill to the root.

An earlier draft had `close()` send `killpg(SIGKILL)` — "close ends what is
left". That was measured wrong twice over. It killed a backgrounded helper that
a hook exits 0 with today, and at the rpc caller the gap between the child's
reap and `close()` is **seconds** (`rpc_channel._shutdown`: reap →
`drain(2.0)` → `stop()`), which is exactly the window in which an emptied
group's number is free for reuse. The headline test moved to the escalation
path instead, where the group and the job are what reach a grandchild on both
platforms and no success path is signalled at all.

## Windows semantics, stated plainly

Four facts that the shape above follows from, none of which are guesses.

- **`TerminateProcess` is uncatchable.** Windows `os.kill(pid, sig)` is
  `TerminateProcess(handle, sig)` for every value except `CTRL_C_EVENT` and
  `CTRL_BREAK_EVENT`. So the *cooperative* rung of a ladder is already the
  absolute one there, and no choice of signal number changes it.
- **A Windows process group is a console-event address, not a kill unit.**
  `CREATE_NEW_PROCESS_GROUP` buys exactly one thing: somewhere to aim a
  `CTRL_BREAK_EVENT`. It does not make the group killable. The job does that.
- **`os.kill(pid, 0)` is a Ctrl+C on Windows**, not a liveness probe — signal 0
  is not special-cased, so the POSIX idiom sends a real console event to a real
  process. Tests use `tests/process_probe.is_dead_or_zombie` instead.
- **`KILL_ON_JOB_CLOSE` fires on parent death too**, because the parent's exit
  closes its handle. So on win32 a `kill_on_close=True` tree dies with us; on
  POSIX only Linux `pdeathsig` does that, and only at the two `aelix_agents`
  channels. The asymmetry is real and is not worth erasing.
- **A cooperative exit is a new code path on Windows, and the first one
  crashed.** Before this ADR an rpc child there was always `TerminateProcess`'d,
  so its interpreter never reached finalization. The first `windows-latest`
  run that let it shut down on `SIGBREAK` measured the orderly path running to
  completion and then dying with `Fatal Python error: _enter_buffered_busy:
  could not acquire lock for <_io.BufferedReader name='<stdin>'> at interpreter
  shutdown, possibly due to daemon threads` (exit `0xC0000005`, 1.17 s against a
  20 s grace): #107's stdin pump thread was parked in
  `sys.stdin.buffer.readline()` holding the `BufferedReader`'s lock, which the
  finalizer needs to close `sys.stdin`. The pump now takes a descriptor and
  reads it with `os.read`, which holds no Python-level lock — and returns as
  soon as any bytes arrive, which is what `readline` had been chosen for. The
  isolated wakeup mechanism (a Python-level `SIGBREAK` handler under a parked
  proactor loop) was measured working on the same run,
  `test_soft_kill_wakes_a_child_parked_in_its_event_loop`.

The job is created through ctypes rather than `pywin32`, because `pywin32`
arrives only as a dependency of `mcp`, which `aelix-ai` must not take
(AGENTS.md §1). The struct layout was verified rather than transcribed:
`JOBOBJECT_BASIC_LIMIT_INFORMATION` 64 bytes with `LimitFlags` at offset 16,
`IO_COUNTERS` 48, `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` 144 on LP64. The
constants match the MCP SDK's `mcp/os/win32/utilities.py`, which is the same
design reached independently.

Every win32 side effect goes through one Protocol seam, and the real
implementation of it is constructible **only** when `sys.platform == "win32"`.
An injected `platform="win32"` on a POSIX box with no injected seam therefore
raises instead of quietly emitting a `SIGHUP` — signal 1 is `CTRL_BREAK_EVENT`
on Windows and `SIGHUP` here, and that is the sort of test that passes while
killing the runner's shell.

## POSIX consequences, stated plainly

**`process_group=0`, not `start_new_session=True`, at the two new sites.**
`setsid` detaches the controlling terminal, and `!command` runs credential
helpers — `gpg`, `pass`, pinentry — that open `/dev/tty`. Measured under a real
pty (`scratchpad/pty_driver.py`):

```
parent ctty check: parent HAS ctty
start_new_session= False -> HAVE_TTY | err:
start_new_session= True  -> NO_TTY   | err: sh: /dev/tty: Device not configured
```

`process_group=0` (Python ≥ 3.11) is `setpgid(0, 0)` in the child: a new group
inside the **same** session, so the tty survives and `killpg` works identically.
The rpc child keeps `start_new_session=True` — it has no terminal to want.

**A process group does not contain a descendant that called `setsid()`.** That
is not a defect of this design; it is what a process group is. Every tool child
of the rpc child is a session leader of its own (`bash.py:278`,
`_subprocess.py:78`), and so is every MCP stdio server
(`mcp/client/stdio/__init__.py:256`). A hook shell and a `!command` shell are
**not** — `process_group=0` is `setpgid(0, 0)`, which is why they keep the
session and its tty — but each leads a group of its own, so they are outside the
rpc child's group all the same. The group we now kill therefore holds the child
and its non-`setsid` descendants — for the shipped child, that is a pipe-holder
and nothing else.

This is the same fact ADR-0197 recorded as finding I2 (`reaper.py:33-38`),
where it retired the claim that a group kill reaches the child's `bash`
grandchildren. **The reconciliation is that the descendant walk stays the
reaper's job.** Nothing here re-adopts `killpg` as a substitute for it, and the
reaper is untouched by this change.

**`os.getpgid()` raises for a zombie leader on Darwin**, even while that
leader's group is alive and holding descendants. Measured on the owner's box
(`scratchpad/darwin_zombie_getpgid.py`):

```
platform: darwin
leader state: zombie  grandchild: alive
getpgid(leader) RAISED ProcessLookupError [Errno 3] No such process
after killpg(pid): grandchild -> gone
```

So "call `getpgid` at kill time and it fails safe" was false on the one
platform development happens on. `attach()` captures the pgid once, right after
the spawn, and treats `ProcessLookupError` as `pgid = pid` on the strength of
the spawn kwargs the caller passed. The pid-only `kill_process_tree` does the
same and then `killpg(pid, SIGKILL)`. Every one of its callers spawned the pid
with `start_new_session=True`, so the number it falls back to is the id of the
group that spawn asked for — but they do **not** all call while the child is
unreaped. `bash.py`'s kill paths do, before anything has waited;
`tools/_subprocess.py::run_cancellable` was measured reaching it *after*
asyncio's watcher had already reaped the child, as much as the caller's timeout
later (30 s at `grep`/`find`). That does not widen the target: a process group's
id stays pinned for as long as any member lives (POSIX.1 §3.293; Linux holds the
reference via `attach_pid(PIDTYPE_PGID)`), so either a descendant is still there
and the number is still that group's, or the group is empty and `killpg` returns
`ESRCH` with nothing to hit. What remains is the residual recorded below.

## The hazard this accepts

Killing by number is killing something that could in principle be someone else
by now. The bound is stated per site rather than designed away.

`soft_kill` is guarded by `returncode is None` at every call, with no `await`
between the read and the signal. `hard_kill` on the escalation path is
deliberately **not** guarded by root liveness: leader-dead / descendants-alive
is the exact shape the group kill exists for, and a liveness guard would skip
it. The window on a stale number is then: at the two asyncio sites (rpc, hooks)
`returncode is None` does **not** prove the pid is unreaped — asyncio's child
watcher calls `waitpid` on its own thread and the loop copies the status into
`returncode` only from a later callback (measured: a loop blocked for 1.5 s
still reads `returncode is None` for a pid the kernel has already released), so
the honest bound is that watcher-to-callback latency plus the grace the caller
then pays, and not the 50 ms of a last poll; `!command` — a synchronous `Popen`
nothing has waited on, so the zombie genuinely pins the number. Neither bound is
a target, though: a non-empty group cannot have its id recycled (the reference
above, held on Linux via `attach_pid(PIDTYPE_PGID)` and by the BSDs likewise)
and an empty one has nothing to kill. What is left — the number recycled *and*
the new holder having made itself a group leader, inside that bound — is
accepted and is recorded here rather than hidden. On win32 with a job there is
no pid hazard at all: the handle names the job.

**The assignment window.** Between `CreateProcess` and
`AssignProcessToJobObject` the child is running and anything it spawns escapes.
On the asyncio spawn that window is the transport's pipe-connection round
trips — measured on POSIX at 0.3 ms idle, 1.05 ms median and 1.65 ms max under
load, against a shell that reaches its first `fork` at about 2 ms. Unmeasured
on the proactor loop. `CREATE_SUSPENDED` cannot close it, because `Popen`
closes the thread handle before returning. The belt-and-braces is ordering:
win32 `hard_kill` runs `taskkill /T /F` **first**, while the root is still
alive, so `/T` can walk to an escapee through its live parent link, and
`TerminateJobObject` second.

## What the tests pin

The dispatch is covered without processes: an injected `platform=` and a fake
win32 seam drive both arms on every leg, with `os.kill`, `os.killpg` and
`subprocess.run` spied in every case so no real signal can leave a test
(`tests/process_tree/test_process_tree_api.py`).

The behaviour is covered with real children
(`tests/process_tree/test_process_tree_real_processes.py`,
`tests/rpc/test_rpc_client_containment.py`). The load-bearing ones:

- a descendant whose parent has already exited still dies — the #9129 shape,
  and the case `taskkill /T` alone fails on the Windows leg;
- a `setsid` descendant is reached by the job and **not** by the group, which
  pins the asymmetry above as an assertion rather than a paragraph;
- `kill_on_close` ends leftovers only where it was asked for;
- a hook that backgrounds a helper and returns 0 **keeps** the helper, on both
  platforms;
- `stop()` ends a descendant the rpc child left behind — this one fails on
  `main` on every leg.

The two `skipif`s #207 left behind are removed. Their stubs now install a
**handler** for `SIGTERM`/`SIGBREAK` that writes a breadcrumb and keeps
running, and the assertions are the breadcrumb *and* `elapsed >= grace` — so a
pass proves the soft signal was delivered and survived, not merely that some
grace elapsed into a hard kill.

Three things are not tested and are named rather than papered over: breakaway
from the job (we set no `BREAKAWAY_OK`, so it cannot happen), the assignment
window (not deterministically reproducible), and `IsProcessInJob` under a
*nested* job, which the runner may already be inside — the win32 arms also
assert `contained is True`, so a nested-job TRUE cannot mask a failed
assignment.

One measured cost, accepted: on Windows a `cmd.exe` batch job answers
CTRL_BREAK with a Y/N prompt and burns the hook's whole 1.0 s soft grace, so
`test_run_subprocess_timeout`'s worst case becomes ≈ 0.2 + 1.0 + kill ≈ 1.3 s
against its `< 2.0` bound. The bound is unchanged.

## What stays open

- **#220 — the `aelix_agents` adoption.** `print_channel.py:969`'s spawn,
  `rpc_channel.py`'s `_reap`/`_eager_abort`, `reaper.py`'s win32 arm, and
  `print_mode.py`'s handler block, which is switched off wholesale off POSIX
  and so cannot receive a `SIGBREAK`. The primitive exists after this ADR; the
  adoption is one change and it is not this one. The docstrings at those sites
  say so and name #220.
- **#221 — the `subprocess.run(timeout=)` sites**, with the unbounded
  `communicate()` after the kill on Windows.
- **#222 — `tools/bash.py` / `tools/_subprocess.py` adopting `ProcessTree`**,
  so the bash tool gets a job object instead of `taskkill` on Windows. That is
  the #9129 shape exactly, and it is the one place in the tree where the shape
  is a user-visible hang rather than a leaked process.

Until #220 lands, the print channel and the reaper still orphan on Windows, and
the README's "Platform support" says which half is contained and which is not.

## Consequences

- The three sites end trees. On POSIX that means a timed-out hook's pipeline
  and an aborted delegation's non-`setsid` descendants; on Windows it means
  every descendant of all three, which is the first time any of them was
  contained there at all.
- `!command` credential helpers keep their controlling terminal, which they
  would have lost had this used `setsid` at that site.
- `taskkill.exe` is resolved from `%SystemRoot%\System32` with a retry on the
  bare name, so a wrong `SystemRoot` degrades to today's behaviour instead of
  to silence. This is also the first time `taskkill.exe` is *executed* anywhere
  in the suite: the module it lived in shipped with a docstring saying it was
  never run.
- `#207`'s two Windows `skipif`s are reactivated and now measure a delivered,
  survived soft signal.
- The soft rung on Windows is now a console event, and a runner without a
  console cannot receive one. Five tests turn red together in that case, and
  they are named here so a red leg is triaged as "no console" rather than as
  five unrelated regressions: `test_soft_kill_is_delivered_and_survivable`,
  `test_stop_escalates_to_the_hard_kill_when_the_soft_one_is_survived`,
  `test_stop_is_bounded_by_the_documented_worst_case`,
  `test_a_cooperative_child_exits_under_the_grace`,
  `test_the_real_rpc_child_answers_the_client_it_ships_with` and its diagnostic
  twin `test_the_real_rpc_child_exits_cleanly_given_a_generous_grace`, which
  reports the child's real shutdown time as a warning and is what told the
  finalization crash apart from an undelivered event. That is a finding
  about the runner's console and not about this design — the cooperative claim
  above narrows, with the measured reason, before the branch merges rather than
  after. The timing test
  `test_stop_does_not_wait_out_the_grace_when_a_descendant_holds_the_pipes` is
  deliberately not in that list: `stop()` skips the grace outright when
  `soft_kill` reports the event was not delivered, so its bound survives a
  console-less runner.
