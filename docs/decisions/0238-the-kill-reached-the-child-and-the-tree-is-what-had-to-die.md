# 0238. The kill reached the child, and the tree is what had to die

Status: Accepted (2026-09-05; **#220 amendment 2026-09-05** — adopted at the four `aelix_agents` sites: the print-channel spawn, the reaper's win32 legs, `rpc_channel`'s `_reap`/`_eager_abort`, and `print_mode`'s handler block; **#221 amendment 2026-09-05** — the three `subprocess.run(timeout=)` sites adopt `run_contained`; **#222 amendment 2026-09-05** — the two tool spawn sites adopt it: `_LocalBashOperations.exec` and `run_cancellable`; **#234 amendment 2026-09-06** — the bash tool's watcher teardown awaits through `asyncio.wait`, so a cancellation of the task running `exec` is no longer swallowed there; **#226 amendment 2026-09-06** — `!command` keeps `process_group=0`, for a corrected reason, and a terminal stop is now detected and named; **#230 amendment 2026-09-08** — an `abort()` that lands after the root's reap kills nothing: the handle is finished at the reap and the abort ends the call's drain instead; **#232 amendment 2026-09-08** — the bash tool's success path drains on the idle rule under a cap instead of to EOF; **#227 amendment 2026-09-08** — on win32 a `!command` resolves a shell instead of assuming `sh`, and the win32 chain is now one primitive in `aelix_ai`; **#240 amendment 2026-09-08** — the registry path is no longer uncached: a resolved `!command` is cached per `ModelRegistry`, successes only, dropped at `/reload`, `/login` and a failed interactive turn; **#239 amendment 2026-09-08** — child output is decoded run-wise, UTF-8 strict then the console output code page, and the shells the bash tool spawns are asked for UTF-8; **#239 cross-review amendment 2026-09-09** — a buffer whose end is a byte-exact cut says so, and `exec`'s timeout branch is one; **#239 final-pass amendment 2026-09-09** — a page that decodes all 256 single bytes is offered NOTHING, not even the bad bytes inside a run, so a Western Windows box gets `errors="replace"` byte for byte and the legacy recovery is scoped to DBCS consoles; **#239 windows-leg amendment 2026-09-09** — the `cmd` UTF-8 preamble is DELETED. Its own win32-only probe, written from darwin and unrunnable there, fired on its first leg (CI run 34272507388, windows-latest, py3.11 and py3.12): `chcp 65001 >nul&` in front of an unquoted spaced executable path costs `cmd /c` rule 1 the quotes `list2cmdline` added, and `cmd` answers "is not recognized" — so "stops resolving" did NOT overstate it, and the review's own case, a QUOTED path, is refuted (four quote characters, rule 1 never applied). Rule 1 admits no prefix at all, so no spelling is safe and PowerShell is now the only family asked; a `cmd` child's console-page output goes through the decoder, which costs a Western `cmd` box the UTF-8 the deleted arm briefly bought it — a loss against THIS release's intermediate build only, since `0.1.0-beta.1` decoded those bytes `utf-8`/`errors="replace"` and marked them too; **#243 amendment 2026-09-08** — the errno allowlist that decides "not a runnable shell" moves into that same primitive, and the bash tool's spawn now uses it: a spawn that fails before the command starts is exit 127 instead of an exception out of the tool)
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
(`proc.terminate()`/`proc.kill()`) and is #221, landed 2026-09-05 in the
amendment under "Consequences" below.

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
`bash.py`, `_subprocess.py` and their tests keep their imports. (#222 deleted
it: those imports point at the primitive now — the amendment under
"Consequences".)

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
of the rpc child is a session leader of its own
(`_LocalBashOperations.exec`, `run_cancellable` — named by function because
#222 rewrote both spawns and this file's line numbers are ungated), and so is
every MCP stdio server
(`mcp/client/stdio/__init__.py:256`). A hook shell and `models.json`'s
`!command` shell are **not** — `process_group=0` is `setpgid(0, 0)`, which is
why they keep the session and its tty — but each leads a group of its own, so
they are outside the rpc child's group all the same. The group we now kill
therefore holds the child and its non-`setsid` descendants — for the shipped
child, that is a pipe-holder and nothing else.

**Two different spawns are spelled `!command`, and only one of them is in that
sentence.** The credential helper in `models.json` is the `process_group=0`
shell, on purpose, because it runs `gpg`/`pass`/pinentry
(`oauth/_resolve_config.py`). The TUI's `!command` is the
`_LocalBashOperations.exec` named above and has been a session leader all along
— `start_new_session=True` at `bash.py` predates #222 — which is why the
`stdin` paragraph of the #222 amendment below is about a child that has no
controlling terminal to lose.

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
(Both tool callers named in that paragraph are gone since #222 — the two
measurements stand as the record of why the fallback is written this way, and
`RpcClient.stop()`'s degradation is the only route left into the pid-only
entry point.)

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
then pays, and not the 50 ms of a last poll; `!command` **was** the one site
where the literal claim held — a synchronous `Popen` nothing had waited on, so
the zombie genuinely pinned the number — and since the #226 amendment it is not.
The terminal-stop detector polls `waitpid(WNOHANG|WUNTRACED)`, and that call
reaps a leader that has already exited (measured: deleting the line that hands
the status back does not restore the pin — it is the `waitpid` itself that
consumes the zombie). Where the leader was the group's last living member the
group is empty from the first poll after that exit — measured 0.055 s — until
`hard_kill` fires at the 10 s `_COMMAND_TIMEOUT`: about 9.95 s in which `killpg`
aims at a number nobody holds. Measured on both arms: `main` leaves
`claim=ERR:1 members='… Z <defunct>'` on darwin and `CLAIMED 'Z sh'` on Linux,
the branch answers `ESRCH` with an empty member list on both. Nothing is lost in
that shape — the stdout holder had already left the group, so `killpg` was
already a no-op there on `main` (the descendant survives on both arms) — and
what is gained is a window in which `killpg` could reach an *innocent* recycled
group. With any descendant left in the group it is still pinned (measured
`CLAIMED`). Neither bound is a target, though: a non-empty group cannot have its
id recycled (the reference above, held on Linux via `attach_pid(PIDTYPE_PGID)`
and by the BSDs likewise) and an empty one has nothing to kill. What is left —
the number recycled *and* the new holder having made itself a group leader,
inside that bound — is accepted and is recorded here rather than hidden. It is
small but not theoretical: measured on the author's box, pid space turns over in
about 27 minutes idle (61.7 pids/s against a `PID_MAX` of 99 999) and about
2.4 minutes under load. On win32 with a job there is
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

- **#220 — the `aelix_agents` adoption: landed 2026-09-05.** All four sites
  are converted. They are named here by function rather than by line, because
  this file's line numbers are ungated by design
  (`scripts/check_citations.py` skips `docs/decisions/`), so nothing in CI
  would notice one rotting — and this change edits the very line the original
  bullet pointed at, `print_channel.py:969`, which was the literal
  `start_new_session=True` it replaces. `PrintChannel.run`'s spawn now
  passes `containment_spawn_kwargs(new_session=True)` and attaches a
  `kill_on_close=True` tree before its first `await`; `reaper.reap` and
  `reaper.kill_tree` take that tree and drive the win32 legs with
  `soft_kill()` / `hard_kill()` instead of `os.kill`; `RpcChannel`'s `_reap`,
  `_eager_abort` and `abort_child` read the tree `RpcClient` already attached,
  carried on the registry row rather than fetched from the client; and
  `print_mode.run_print_mode` installs a `SIGBREAK` handler on Windows instead
  of switching the whole handler block off there.
- **#221 — the `subprocess.run(timeout=)` sites: landed 2026-09-05.** All
  three are converted, and they are named here by function for the reason the
  bullet above gives: `extensions/api.py`'s `ExtensionAPI.exec`,
  `cli/extension_catalog.py`'s `_default_git_runner` and `tui/completion.py`'s
  `_fd_enumerate` now call `run_contained` — the one synchronous runner in
  `_process_tree.py` — so a timeout ends the command's tree instead of its root
  and the unbounded `communicate()` after the kill on Windows is gone. What it
  cost is in the amendment under "Consequences" below.
- **#222 — the two tool spawn sites: landed 2026-09-05.** Both are converted,
  and they are named here by function for the reason the two bullets above
  give: `tools/bash.py`'s `_LocalBashOperations.exec` and
  `tools/_subprocess.py`'s `run_cancellable` now spawn with
  `containment_spawn_kwargs(new_session=True)`, attach a `ProcessTree` before
  the first `await`, and kill through it — so the bash tool gets a job object
  on Windows instead of `taskkill /T` alone.
  That is the #9129 shape exactly, and it is the one place in the tree where
  the shape is a user-visible hang rather than a leaked process. The pid-only
  `kill_process_tree` keeps one caller, `RpcClient.stop()`'s degradation;
  `bash.py`'s `_kill_group` and the `tools/_process_tree.py` re-export shim are
  gone. What it cost — and the drain bound that POSIX turned out to need — is
  in the amendment under "Consequences" below.
- **#234 — the watcher teardown's `suppress`: landed 2026-09-06.** The teardown
  #222 restored to `main`'s order kept `main`'s
  `suppress(CancelledError, Exception)` around `await watcher_task`, and that
  `await` cannot tell the watcher's own cancellation — the `cancel()` one line
  above — from a cancellation of the task running `exec`. The caller's was
  swallowed and `exec` returned an ordinary `ExecExitResult`. It awaits through
  `asyncio.wait([watcher_task])` now, which absorbs how the watcher finished as
  membership of the `done` set, and retrieves the watcher's exception in a
  `finally` around that wait — a bare `wait` would leave a watcher that failed
  *during* its own cancellation for the loop to report at GC, which `main`'s
  `await` had retrieved into the `suppress`. Measured on `main` `b94a6db`,
  **with an abort signal supplied**: 6 of 6 aimed cancels lost (one
  deterministic probe: 3 single- and 3 double-cancel rounds), and — across ten
  runs and two seeds — 48 of the 1967 randomly-timed cancels actually
  delivered to a still-running `exec` (2.4 % overall, 1.6–3.6 % per run of 300
  rounds; the other ~1000 rounds found the command already finished). After the
  fix, 0 of 380 delivered and 0 of 6 aimed. The rate is timing-dependent and
  **not seed-reproducible** — the same seed gave 3–7 lost, because the probe
  re-measures the command's median each run and scales its delay window to it.
  **The watcher, and so this window, exists only for a caller that supplies an
  abort signal** — in-product the RPC `bash` command (`rpc_mode._handle_bash`)
  and embedders. `ctx.signal` is `None` on every model-issued tool call and
  `handle_user_bash` passes `signal=None`: measured 0 of 524 requested cancels
  lost with `signal=None` against 14 of 388 with an `AbortSignal`. That bound
  is this ADR's to carry because it also bounds **this window at this site** —
  #230's window at `run_contained` is reached with no signal at all, from
  `except asyncio.CancelledError`, and is bounded by the #230 bullet below.
- **#226 — the `!command` terminal stop: landed 2026-09-06.** The site keeps
  `process_group=0`; what changed is the reason it is there and what happens
  when a helper uses the terminal it kept. `_run_shell_command`'s wait is a
  poll loop now, and `_stopped_by_the_terminal` asks `waitpid` on each poll
  whether the shell has been STOPPED by `SIGTTIN` or `SIGTTOU` — if it has, the
  tree is killed at once and the failure carries a named cause instead of ten
  silent seconds. Named here by function for the reason the bullets above give.
  What it cost — a corrected rationale, and a pgid that this site no longer
  pins — is in the amendment under "Consequences" below.
- **#230 — the abort handle finishes at the reap: landed 2026-09-08.** Named
  here by function for the reason the bullets above give. `run_contained` used
  to release its `AbortHandle` only in the call's `finally`, so between the
  root's reap and the end of the post-exit drain the handle was still armed —
  and an `abort()` landing there ran the ladder at a leader this call had
  ALREADY REAPED. On POSIX that is `killpg(SIGKILL)` at the group, whose only
  remaining member is the helper the command backgrounded on purpose: measured
  on `main` `7fa6796`, the helper died 3 times in 3 on macOS and 2 in 2 on
  docker Linux while the call still returned `returncode == 0`, and the window
  was 0.105 s for a quiet helper and 2.004 s for one that keeps writing — long
  precisely when there is a helper `kill_on_close=False` exists to keep.
  `AbortHandle._finish` now runs at the reap on both legs (on the timeout leg
  only when the root was actually reaped), and `_drain` gained a fourth
  end-condition keyed on `AbortHandle.aborted`, so an `abort()` in that window
  returns `False`, sends nothing, and ends the call's drain instead — measured,
  the call returns 0.0016–0.0076 s after the abort with the helper ALIVE 3/3 on
  macOS and 2/2 on Linux. In production that abort is `ExtensionAPI.exec`'s
  `except asyncio.CancelledError`: an ordinary Esc, ^C, or cancelled turn.
- **The interrupt leg into the same window — still open.** #230 disarmed the
  HANDLE at the reap; it did not touch `run_contained`'s `except BaseException:
  _end_the_tree(…)`, the sibling handler of the same `try` the post-exit drain
  runs in. Measured against the #230 module, a `KeyboardInterrupt` 0.6 s into a
  2.0 s drain entered that ladder with the root's `returncode` already `0` and
  group-killed a backgrounded helper, 3 times in 3, with and without a handle —
  the ladder is `_end_the_tree`, not the handle. **Reach is ONE caller,
  measured:** only `extension_catalog.py`'s clone can take a terminal ^C in this
  frame, reached from `_cmd_discover`, which calls `fetch_all` synchronously on
  the event loop — that is, on the main thread, the only thread CPython raises
  `KeyboardInterrupt` on. `tui/completion.py`'s fd scan is not that site
  (`shell.py` wraps its completer in a `ThreadedCompleter`) and
  `ExtensionAPI.exec` runs on a `to_thread` worker, where the same ^C leaves
  the ladder empty and the helper alive (3/3). It is left alone deliberately: at that one live
  site the ladder is load-bearing for a second reason — it ends the tree before
  `_git_clone_bytes`'s `finally: rmtree` deletes the directory out from under a
  still-running `git`. Whether a ^C that lands after the clone's own `git` has
  exited should still end that tree is a question for the owner; #230 decided
  the `abort()` path only.
- **#232 — the success path's drain: landed 2026-09-08.** Named by function
  like its neighbours: `tools/bash.py`'s `_LocalBashOperations.exec` stamps the
  root's own exit on the `proc.wait` worker thread and drains through
  `_drain_after_the_exit`, which is `_drain_past_the_kill` renamed and given the
  cap as a parameter, so the three kill legs and the ordinary exit share one
  loop; the cap arithmetic itself moved to `_process_tree.py`'s
  `_exit_drain_cap`, which `run_contained`'s exit leg now calls instead of
  spelling inline. `_PipeReader.run` stopped caching the caller's `on_chunk` in
  a frame local and `_PipeReader.detach` clears it, because a returning `exec`
  now commonly leaves a reader parked on a helper's pipe. What it cost is in
  the amendment under "Consequences" below. What it did NOT do: say anything in
  the result when the drain ends on the cap rather than on EOF — Pi says nothing
  either, and whether Aelix should is an open product question.
- **#227 — the `!command` shell on win32: landed 2026-09-08.** This site spawned
  `sh -c` on every platform, so on a stock Windows box the spawn failed in about
  a millisecond and the value resolved to nothing — reported as `Failed to
  resolve … from shell command:`, which blames the user's command for a shell
  that was never there. `_run_shell_command`'s single `Popen` is a loop over
  `_shell_argv_candidates` now, and the win32 chain itself moved DOWN into
  `aelix_ai.utils._shell` as `windows_command_shells`, which
  `tools/bash.py`'s `_resolve_shell_win32` now returns the first element of.
  Named here by function for the reason the bullets above give. What it cost —
  the chain, the hardening, the console flag and a cross-platform change to the
  cached trim — is in the amendment under "Consequences" below.
- **The hook shell's terminal stop — still open.**
  `extensions/subprocess_hooks.py` is this ADR's other `process_group=0` site
  and takes the same `SIGTTIN`/`SIGTTOU` stop. #226's detector is synchronous
  and that site drives its own asyncio ladder — its `ThreadedChildWatcher` reaps
  with a blocking `waitpid(pid, 0)`, which never returns on a stop — so a hook
  that touches the terminal still costs its full timeout with no named cause.
  The magnitude is larger than the site that was fixed: `HookContrib.timeout_ms`
  defaults to 60 000 ms and may be 600 000, and a hook fires per event. Recorded
  here as well as in #235, because routing it to an issue alone is the miss #226
  was born from.

The README's "Platform support" still says which half is contained and which
is not, but the split now falls elsewhere: after #220 the Windows verdict is
whole, and what remains is POSIX's — a `setsid` grandchild is outside the group
on every platform, and on a host with no `/proc` (macOS) the reaper's walk is
empty, so only the group kill of the paragraph below reaches anything there.

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
- **What #220's adoption did *not* change (amendment, 2026-09-05).** The POSIX
  cooperative leg is still `_signal_child(proc, SIGTERM)` to the root and
  nothing else; the `/proc` descendant walk is still what the escalation runs
  first and still the only thing that reaches a `setsid` grandchild; and
  `close()` is still a release that signals nothing at all on POSIX. One POSIX
  leg *was* added, and it is the only place this ADR's decision moved: after
  the walk, inside `reap()` and never inside `kill_tree`, the escalation now
  also calls `tree.hard_kill()` — `killpg(pgid, SIGKILL)`. It exists because
  `descendant_pids()` is `[]` on a host with no `/proc`, which made the macOS
  escalation a root-only `SIGKILL`: measured on the owner's box, a child that
  ignores SIGTERM and spawns a **non-`setsid`** grandchild left that grandchild
  alive after the walk-and-root kill and lost it to the group kill, and one
  spawn site reachable INSIDE a delegated child still has exactly that shape —
  `tools_manager`'s version probe (`_command_exists`), plain `subprocess.run`
  with neither `start_new_session` nor `process_group`, reached from
  `ensure_tool` by the `grep`/`find` tools inside an agent turn.
  `ExtensionAPI.exec` was the second until #221 gave it a session of its own.
  The count is scoped to what runs inside a delegated child:
  `cli/extension_install.py`'s pip runner and `tui/shell.py`'s `$EDITOR` have
  the same kwarg shape but run only from the `extension install` CLI and the
  TUI editor, never inside a delegated child, so `reap()`'s `killpg` never has
  them in its group. The group kill is an addition and not a substitution — it
  runs *after* the walk and addresses only what the walk could not name — so
  ADR-0197's finding I2 and the reconciliation at the top of this file both
  stand. It is kept OUT of `kill_tree` for a measured
  reason: `_drain_after_exit` reaches that function on a delegation's
  **success** path with a child that is already dead and already reaped, and a
  `killpg(SIGKILL)` there is the draft `close()` this ADR reverted, which killed
  helpers a hook had deliberately backgrounded and exited 0 over. The other two
  callers — `PrintChannel._eager_abort` and `RpcChannel._eager_abort` — reach it
  with a child that is usually ALIVE (the first Ctrl+C is
  `turn_task.cancel()`), and only sometimes dead (a cancel that lands inside
  that drain), so the discriminator is a `returncode` re-read at the call site,
  not a blanket rule in `kill_tree`. `PrintChannel._eager_abort` therefore takes
  the group kill too, guarded by that re-read and by "no reaper task is left to
  escalate" — without it a delegation cancelled before its deadline left a
  non-`setsid` grandchild alive on macOS (**measured**, #220 review round 2).
  `RpcChannel._eager_abort` does not: its tree is borrowed and `RpcClient.stop`
  still owes it a hard leg of its own.

  The bound on the group kill is the GROUP, not the pid, and the earlier
  drafting of this paragraph got that wrong. "`reap` owns the only
  `proc.wait()`, so the leader is unreaped" does not hold on asyncio — the child
  watcher `waitpid`s on its own thread, which this ADR already records above —
  so `reap()` and `_eager_abort` sit under the same bound as the rpc and hook
  sites: a non-empty group's id cannot be reused while any member lives, an
  empty one answers `ESRCH`, and the residual (the number recycled *and* its new
  holder having made itself a group leader) is the residual accepted below. Two further
  consequences are recorded rather than left to be re-derived. On win32 a
  **successful** delegation now ends every surviving job member, because the
  print row's tree is `kill_on_close=True` and `PrintChannel.run`'s `finally`
  closes it — deliberate, the same behaviour the rpc child has shipped with
  since #202, and on POSIX still nothing. And the parent-death question (Q6 of
  the #220 spec) needed no new decision: it is answered by the
  `KILL_ON_JOB_CLOSE` bullet under "Windows semantics, stated plainly"
  (`:151-154` of this file), which is why the print child's tree asks for
  `kill_on_close` at all — it is that child's only win32 analogue of the Linux
  `pdeathsig` its spawn already installs.
- **What #227's adoption added (amendment, 2026-09-08).** The chain, best
  first, is `$SHELL` when it names a file that exists → `sh` on `PATH` → `pwsh`
  → `powershell` → `%COMSPEC%` → `%SystemRoot%\System32\cmd.exe` → `cmd.exe`
  (the second-to-last step added by #241, order otherwise unchanged), and the
  first candidate that SPAWNS wins; a candidate that is missing, not executable,
  or **not a loadable program image** is skipped, and a chain where every
  candidate fails still answers the `None` this site answered before. `sh` sits
  at step 2 and not lower because a `!command` was written for `sh` — it was hard-coded — so a Windows box with
  an `sh` on `PATH` and no `SHELL` set (Git for Windows, MSYS2, Cygwin) keeps
  running it under the shell it was written for; a `$SHELL` that names an
  existing file still wins at step 1, because it is the user's explicit choice.
  That step is the ONE difference between the two
  callers: `_resolve_shell_win32` passes `include_posix_sh=False`, because `sh`
  is classifiable and taking it there would flip AUTO mode's dialect on every
  MSYS box, which is ADR-0237/#204's decision and not this one's. #227 is
  therefore what makes one environment variable govern two surfaces: exporting
  `$SHELL` to a real `bash.exe` picks the `!command` shell AND tells the
  permission gate to read commands with the bash grammar, and the guide states
  the coupling in the same clause as the advice. The same call site carries one
  more consequence for the bash tool: the `PATH` probes are skipped when the key
  is absent, so a `spawn_hook` that hands the tool an env with no `PATH` now
  yields `%COMSPEC%`/`cmd.exe` rather than falling back to the host `PATH` and
  finding `pwsh`.

  **#241 착지 (amendment, 2026-09-08) — 사용자가 이름을 대지 않은 후보는 절대경로일
  때만 채택한다.** `shutil.which`가 이 체인에서 빠졌다: win32에서 `path=`를 명시해도
  CWD를 먼저 뒤진다(CPython 소스 실측 — 3.11은 무조건, 3.12-3.14는
  `NeedCurrentDirectoryForExePath`가 막지 않는 한이고, **3.11은 이 저장소의 CI
  레그다**). 대신 `PATH`를 직접 순회하며 **절대경로가 아닌 컴포넌트는 건너뛴다**
  (빈 값·`.`·`relbin`·드라이브 상대 `C:foo`) — POSIX에서도 같은 구멍이라
  `!command`의 `sh`와 bash 툴의 `bash`도 같은 순회로 절대 해석한다(darwin 실측:
  `PATH=":/usr/bin:/bin"` + cwd의 `sh` → 심어 둔 파일이 실행됐다). `PATHEXT`는
  `env`에서 읽어 CPython **3.12+**의 규칙(3.12.13의 `shutil.py:1536-1551`)을 그대로
  적용한다 — 3.11에는 `rstrip('.')`이 없고 확장자를 가진 이름에 bare 이름 하나만
  쓰지만, 이 모듈이 찾는 이름(`sh`/`pwsh`/`powershell`/`bash`)은 확장자가 없어 두
  갈래가 갈라지지 않는다.
  🔴 **체인에 후보 하나가 늘었다**: `%SystemRoot%\System32\cmd.exe`(존재할 때,
  기본값 `C:\Windows` — `_process_tree.py`의 `taskkill` 해석과 같은 모양)가
  `%COMSPEC%`와 맨 bare `cmd.exe` 사이에 **무조건** 들어간다. `%COMSPEC%`가 이미 그
  경로인 stock 박스에서는 같은 프로그램이 체인에 두 번 오른다 — 의도된 것이다:
  플로어의 존재를 그것이 살아남으려는 바로 그 변수에 의존시키면 플로어가 아니고,
  대가는 존재 검사를 통과한 후보가 이미 성공했을 자리에서 fall-through하는 호출자가
  같은 spawn을 한 번 더 시도하는 것뿐이다. bare `cmd.exe`는 `[0]`가 항상 존재해야
  하므로 남는다.
  🔴 **상대 `%COMSPEC%`를 버리는 것은 CPython보다 엄격하고 의도된 divergence다**:
  `subprocess.py:1505-1529`는
  `%ComSpec%`가 **unset이거나 빈 문자열일 때만** `%SystemRoot%`를 보고, `isabs`는
  `executable=`을 고를 때만 쓴다 — 이 사이트는 셸을 **spawn하지 않고 이름만 대며**
  두 spawn 지점 모두 `lpApplicationName = None`이라 그 보호를 물려받을 수 없다.
  🔴 **`$SHELL`은 필터하지 않는다**: 사용자가 명시한 셸이고(#227이 문서화한 방법),
  위협 모델은 "내가 `cd`한 디렉터리에 공격자가 파일을 썼다"이지 "공격자가 내 환경을
  소유한다"가 아니다. `%COMSPEC%`는 이 바구니에 없다 — Aelix 설정이 아니라 stock
  Windows 변수다. 절대성 판정은 `posixpath.isabs`와, **드라이브를 요구하는**
  `ntpath.isabs`의 합집합이며 플랫폼 seam **밖**이다. 드라이브 조건은 3.13+의
  `ntpath.isabs`를 앞당겨 채택한 것이다 — 그것이 없으면 루트 없는 `\dir`가
  3.11/3.12에서만 통과하는데, 그 항목은 Windows에서 드라이브 상대이고 POSIX에서는
  `os.path.join("\dir", "sh")`가 CWD에 대해 풀리는 그냥 상대 경로다(3.12.13 실측:
  심어 둔 `./\evil/sh`가 반환됐다). 맨 `os.path.isabs`는 Windows 밖에서 posixpath라
  `C:\Windows\system32\cmd.exe`를 상대로 읽어 기존 케이스 5개를 POSIX 레그에서만
  빨갛게 만든다(실측 `5 failed, 88 passed` vs 합집합 `93 passed`). 대가: 상대 `PATH`
  항목(`.`, `node_modules/.bin`, direnv)으로 닿던 셸은 더 이상 발견되지 않는다.
  win32 동작은 대부분 여전히 **소스·문서 추론**이고 증거는 CI 레그뿐이다 — 다만
  `windows-latest`는 이제 실제 `%SystemRoot%\System32\cmd.exe`가 `!command`를
  **스폰해서 실행**하는 것과, 그 후보를 무력화했을 때(=`cmd.exe`가 없는 절대
  `%SystemRoot%`) bare `cmd.exe` 명령줄이 여전히 도는 것을 둘 다 증명한다.
  🔴 **기본값은 절대경로가 아닌 `%SystemRoot%`에도 적용된다**(브랜치 독립 리뷰 수정):
  없음·빈 값만이 아니라 상대값(`winroot`)도 `C:\Windows`로 떨어진다. 상대
  `%SystemRoot%`는 CWD에 대해 풀리는, 이 이슈가 바로 불신하는 입력이고, 이 단계
  아래에는 bare 이름밖에 없다 — 그래서 `%COMSPEC%`처럼 **버리는** 규칙을 쓸 수 없다
  (`%COMSPEC%` 아래에는 이 플로어가 있다). `_process_tree.py`의 `or`가 빈 값만 보는
  것과 갈리는 두 번째 지점이며, 그쪽은 bare `taskkill`로 재시도하므로 대가가 없다.
  이 규칙은 win32 밖에서도 실측된다 — `os.path.join(r"C:\Windows", …)`가 POSIX에서는
  상대 이름이라 기본값을 cwd 아래 심어 놓고 `exists()` 게이트를 그대로 통과시킬 수
  있고, `test_the_floor_defaults_to_c_windows_on_every_leg`가 그렇게 한다(이전에는
  win32 전용 케이스 하나뿐이라 기본값을 지워도 darwin에서 125 passed로 green이었다).

  🔴 **레그가 반증한 것 (2026-09-08, CI run 34238824791) — 다시 믿지 말 것.**
  두 가지가 실측으로 뒤집혔고 둘 다 테스트 쪽 오류였다(제품 동작은 그대로 옳다).
  (1) **`%SystemRoot%` 키를 지워도 floor에 닿지 않는다.** 지우면 기본값
  `C:\Windows`가 그 자리를 메우고 그 파일은 러너에 실재하므로,
  `"C:\Windows\System32\cmd.exe" /d /s /c "echo x"`가 bare 이름보다 **먼저**
  스폰됐다. 그것이 이 기본값의 의도다 — 변수를 지우는 것만으로 사라지는 바닥은
  바닥이 아니고, `C:\Windows`는 CWD가 흔들 수 없는 고정 시스템 경로다. bare floor를
  실행시키려면 존재하지 않는 절대 `%SystemRoot%`로 후보를 **무력화**해야 하며,
  그것은 `env=`(해석 seam, `Popen`에 넘어가지 않음)로만 말한다 — 자식에게 깨진
  `%SystemRoot%`를 물려주면 #209가 측정한 Winsock 실패(`WinError 10106`) 모양이 된다.
  (2) **`shutil.which`의 CWD 우선 탐색은 이제 추론이 아니라 실측이다.** 3.11·3.12
  양쪽에서 `shutil.which("pwsh", path=<pwsh가 든 절대 디렉터리>)`가 cwd에 심어 둔
  사본을 `'.\pwsh.EXE'`로 — **상대 경로로** — 돌려줬다. 즉 이 이슈가 막는 구멍은
  레그에서 재현됐다. 그 케이스가 빨갰던 이유는 러너가
  `NoDefaultCurrentDirectoryInExePath`를 켰기 때문이 아니라(켜지 않았다) 반환값을
  cwd에 대해 풀지 않고 절대 경로와 문자열 비교했기 때문이다. 그 컨트롤은 이제
  풀어서 비교하고, 정말로 성립하지 않는 러너에서는 **`pytest.skip`**한다 — 영구
  red보다 낫다. 그 skip이 제품 쪽 주장까지 데려가지는 않는다: 레그에서는 한 번도
  skip하지 않는 `test_win32_the_answer_does_not_depend_on_the_curdir_variable`가
  같은 답(cwd에 심어 둔 `pwsh.exe`가 아니라 `PATH` 사본)을 컨트롤 없이 단언하고,
  모든 레그에서는 `test_a_cwd_copy_loses_to_the_path_copy_on_every_leg`와 기존
  `test_a_path_component_that_is_not_absolute_contributes_nothing`이 같은 구멍의
  **`.` 컴포넌트 형태**를 단언한다. 🔴 **암묵적 prepend 자체는 POSIX에서 단언할 수
  없다** — 그렇게 하는 `which`가 없으므로 POSIX 레그는 그것으로 빨개질 수 없고,
  "every leg" 케이스가 증명하는 것은 `PATH`가 명시한 cwd 항목 쪽이다(브랜치 독립
  리뷰가 잡았다: 고치기 전 그 케이스는 pre-#241 코드에서도 green이었다).

  The three coding-agent consumers (`dialect_for_shell`,
  `is_classifiable_shell`, the PowerShell classifier's name normaliser) import
  the primitive **directly**. There is no compat re-export in `tools/bash.py`:
  the naive one is 6× `F401` plus an `I001` under this repo's own ruff config
  and `ruff check .` is a CI job, and moving `ShellConfig` down with the rest
  makes every surviving import in that file a used one — measured `All checks
  passed!`, with 88 lines out of `bash.py` against 19 back in. One import site
  per name, and no shim to drift.

  The hardening is the CALLER's, not the primitive's: `tools/bash.py` runs the
  user's interactive shell and must keep their profile, while a `!command` must
  not. `-NoProfile` is correctness — measured on PowerShell 7, a profile that
  writes to stdout is prepended to the resolved key (`profile-banner\nsk-KEY`).
  `-NonInteractive` narrows #226's win32 clause **for PowerShell's own prompts
  only**, and not by shortening a plain `Read-Host`, which `stdin=DEVNULL`
  already ends — the shape this site has had since ADR-0140, and the bash
  tool's since #222 (measured 0.652 s against a ~0.45 s no-prompt baseline).
  What it buys is the prompt TEXT staying out of the key (measured: `'give me a
  key: \nGOT:'` against `'GOT:'`), the masked read (`Read-Host
  -AsSecureString`, `Get-Credential`'s console path) that opens `CONIN$` with
  `CreateFile` and so costs the whole timeout, and the prompt WRITE to `CONOUT$`
  that redirection cannot capture. A helper that opens the console itself (git,
  ssh, gpg) is untouched.

  The win32 child also gets `CREATE_NO_WINDOW`, OR'd in **at this call site
  only**. Pi passes `windowsHide: true` at exactly this spawn and Node's default
  is false; CPython gives it for free only for `shell=True` and this site spawns
  a list argv; and before #227 the win32 spawn of `sh` failed before an image
  loaded, so the question never arose. It must not move into
  `containment_spawn_kwargs`: three of that helper's other six call sites
  (`extensions/subprocess_hooks`, `rpc/rpc_client`, and
  `aelix_agents/print_channel`, whose tree the reaper soft-kills) end their trees
  with `soft_kill()` → `ctrl_break()`, which needs the shared console this site
  never uses, so the flag there would silently demote three teardowns to hard
  kills. A shared console is also the shape this ADR rejected on POSIX, where a
  `setsid` helper was measured stealing the line the user had typed; a helper
  opening `CONIN$` on Aelix's console is that theft. The price is that a win32
  console prompt becomes certainly unanswerable — which is what the
  `-NonInteractive` clause above already says. Pi hides its configured-shell
  spawn but not its `execSync` fallback; Aelix hides every win32 candidate
  (stricter, and ADR-0235 permits it).

  `resolve_config_value`'s trim becomes `.strip()`, which is Pi's `.trim()` and
  what its own sibling resolver already did. This is a **cross-platform** change,
  not a Windows one: it also removes a leading newline and surrounding spaces
  and tabs that `rstrip("\n")` kept, so an existing POSIX user's cached value
  can change. It is here because this platform makes the bug reachable —
  measured, a command ending in CRLF cached `'sk-abc\r'` where the uncached
  resolver answered `'sk-abc'`, i.e. a bare carriage return inside an
  `Authorization` header.

  The cost is real and is not a regression only because nothing worked there
  before: `get_api_key_and_headers` is the harness's per-request callback, so a
  box that lands on PowerShell pays about half a second (pwsh 7.6.5, 455 ms
  median against `sh`'s 3.2 ms, both measured on macOS; Windows PowerShell 5.1,
  which is what a stock box actually resolves, is unmeasured and typically
  slower) — **once per registry load per distinct `!command`** since
  [#240](https://github.com/handochan/aelix-ai/issues/240) closed, and once per
  request before it, which is what #240 was filed for. Still open beside it:
  [#241](https://github.com/handochan/aelix-ai/issues/241) for
  `shutil.which`'s CWD-first search now deciding which program runs a
  credential command,
  before: `get_api_key_and_headers` is the harness's per-request callback and
  the registry path is deliberately uncached, so a box that lands on PowerShell
  pays about half a second per model turn (pwsh 7.6.5, 455 ms median against
  `sh`'s 3.2 ms; 5.1 is typically slower). Split out as
  [#240](https://github.com/handochan/aelix-ai/issues/240); **#241
  (`shutil.which`'s CWD-first search deciding which program runs a credential
  command) landed 2026-09-08 — see the amendment above**; and
  [#242](https://github.com/handochan/aelix-ai/issues/242) for
  `resolve_config_value` caching an empty value where its sibling returns
  `None`, and [#243](https://github.com/handochan/aelix-ai/issues/243) for the
  bash tool's own spawn letting a WinError 193 escape — A.9's root cause at a
  different site, pre-existing on `main`.

  **The reasoned-not-measured set, stated so it is not read as evidence.**
  Nothing on win32 was measured on the machine this landed from. `/d` and `/s`
  are read from `cmd /?`. The two quoting conventions are reasoned from the same
  place: the PowerShell family keeps a list because the .NET host CRT-parses the
  command line back into argv before PowerShell's parser sees it, so
  `list2cmdline`'s `\"` is the documented escape, while the `cmd` family gets a
  RAW command line because `cmd` implements no `\"` at all — the second is a
  deliberate divergence from `subprocess`'s default rendering. The win32 console
  behaviour of `CREATE_NO_WINDOW` is reasoned from `CreateProcess`. The
  `ERROR_BAD_EXE_FORMAT` → `ENOEXEC` mapping is read from CPython's
  `PC/errmap.h` and `Objects/exceptions.c` (the errno is resolved before
  `OSError`'s subclass table, and that table has no `ENOEXEC` and no `EINVAL`
  entry, which is why the fall-through is an errno allowlist rather than an
  exception tuple). And the profile measurement is pwsh 7 on darwin; Windows
  PowerShell 5.1, which is what a stock box actually resolves, is a different
  implementation nobody has run any of this against. The Windows evidence is CI:
  one case pins that the `sh` candidate still resolves (the leg has Git's
  `sh.exe`), and two more force a PowerShell and a `cmd.exe` argv there and read
  the value back. Concretely that is this branch's windows-latest legs
  (py3.11 and py3.12), whose UserWarning lines name the shell each of those
  three cases actually resolved.
- **A decision taken in the owner's absence, recorded here so it can be
  reversed.** #220 also stopped `print_mode`'s signal path from calling
  `sys.exit` inside a task: it records `128 + sig` and lets `run_print_mode`
  return it. That is a user-visible change on POSIX too, in a file outside
  #220's title, and it was made because without it the Windows observable #220
  exists to produce does not exist. Measured on `main` 6586f8a: a real
  `aelix -p` child sent SIGTERM exited **1** with two tracebacks — the
  `SystemExit(143)` escaped the loop mid-step and `asyncio.run`'s
  `Runner.close()` replaced it with `RuntimeError: Event loop stopped before
  Future completed` — and `TerminateJobObject(job, 1)` also reads as 1, so a
  cooperative `SIGBREAK` exit and a hard job kill would have been
  indistinguishable except by stderr tail. The code now returns 143 on POSIX
  (**measured**, `.omc/specs/220-progress-2026-09-05.md` §1) and 149 on Windows
  — the value `128 + SIGBREAK` implies, still **unmeasured**: the
  `windows-latest` leg is where it gets a number.
- **The three `subprocess.run(timeout=)` sites end trees too (amendment,
  2026-09-05).** #221 gave `_process_tree.py` one synchronous runner,
  `run_contained`, and `ExtensionAPI.exec`, `_default_git_runner` and
  `_fd_enumerate` call it. Its shape: the caller's `timeout` bounds the ROOT
  (`proc.wait(timeout=)`) and not pipe EOF, which is what CPython's `run()`
  gets wrong twice over. It waits for EOF, so a root that exited **0** while
  leaving a pipe-holder behind was reported as a timeout only after the whole
  deadline had passed — measured on `main`, `out=b'done\n'` with a
  `TimeoutExpired` at 3.3 s, and `code=124 killed=True stdout='done\n'`
  through a real model at the `exec` surface; and on Windows its post-kill
  `communicate()` has no bound at all, so the call cannot return while any
  descendant holds the pipe. After the root exits, `run_contained` drains its
  two daemon reader threads on Pi's rule: the idle timer is armed AT THE EXIT
  and re-armed by every chunk that arrives after it (`EXIT_DRAIN_SECONDS =
  0.1`, Pi's `EXIT_STDIO_GRACE_MS = 100`), because measuring idleness from the
  last chunk alone returns instantly whenever the root was quiet before
  exiting and bins the tail — measured `b'EARLY\n'` against Pi's
  `b'EARLY\nLATE0..4\n'`. The drain is capped absolutely at `DRAIN_CAP_SECONDS
  = 2.0` from the exit, which is an Aelix-only divergence (Pi drains
  unbounded) taken because `api.exec`'s DEFAULT is `timeout_ms=None`, under
  which an unbounded drain never returned — measured, 10 MB buffered at 9 s
  and still climbing. Its cost is stated rather than hidden: a DESCENDANT
  still writing at the cap has its output cut, with the root's own
  `returncode` intact — never the root's own output, which is at most a pipe
  buffer at exit and drains in milliseconds. **The cap bounds the CALLER'S
  WAIT, and the bytes stop accumulating at the return** (post-merge review
  site-exec-1): after `run_contained` returns, the two daemon readers keep
  reading — so a descendant that still holds the pipe is never stalled on a
  full one — but they DISCARD from then on, retaining at most one chunk. The
  thread and its fd still live until that holder closes the pipe — one daemon
  thread and one fd per call for as long as a holder outlives the call, which
  is accepted; what does not survive the return is the BUFFER.
  The timeout ladder is hard only —
  `tree.hard_kill()`, then `proc.kill()` as a belt, then a `proc.wait` bounded
  by `REAP_GRACE_SECONDS = 5.0` and a post-kill drain bounded by
  `KILL_DRAIN_SECONDS = 1.0` (joining the readers for the rest of the grace
  instead cost a flat 6.0 s against a 1 s timeout whenever a pipe-holder
  outside the tree survived, and bought no bytes; the idle rule costs ~1.1 s).
  **On win32 both ladders cost one `taskkill` more, and the bound says so
  (DOC-4/HC4).** `hard_kill` runs `taskkill /T /F` synchronously before
  anything else there, and `_taskkill_tree` is itself a
  `subprocess.run(timeout=5)`, so the win32 timeout path is `timeout + ≤5 s +
  REAP_GRACE_SECONDS + KILL_DRAIN_SECONDS` and the interrupt leg below is `≤5
  s + 0.25 s`, not 0.25 s. Both terms are our own `timeout=`, so the latency
  is still bounded by us and not by the command; the real number comes from
  the `windows-latest` leg's `warnings.warn` (#220 measured that `taskkill`
  rung at 0.031 s). A SECOND interrupt landing inside that `taskkill` leaves
  `terminate_job` and the `proc.kill()` belt unrun (HC7). There is no soft
  rung, for two measured reasons rather than a preference: these three sites
  have none today — CPython's `run()` sends `process.kill()` on
  `TimeoutExpired` with no SIGTERM leg, so a soft rung would be new behaviour
  to justify — and Pi's is not a bound worth copying, because `execCommand`
  guards its 5 s SIGKILL timer with `if (!proc.killed)` while Node sets
  `killed` when the signal is SENT, so the escalation never fires: a `trap ''
  TERM` child ran its full 40 s under a 1 s Pi timeout. Every one of these
  calls also carries an interrupt leg — the same ladder, bounded at
  `INTERRUPT_REAP_SECONDS = 0.25`, on ANY `BaseException` raised anywhere
  after the spawn: the wait, the drain, and (post-merge review posix-runner-1,
  measured 12/12 leaked trees when a `KeyboardInterrupt` landed before the
  readers started, 0/6 once it landed inside the covered `try`) the abort
  hand-over and both reader starts too, while `ProcessTree.attach` carries its
  own root-only belt — because `subprocess.run` kills the root on any exception and
  `Popen.__exit__` states the assumption "the SIGINT was also already sent to
  our child processes", which containment makes false: measured under a real
  pty, `^C` gave the parent a `KeyboardInterrupt` and left the contained child
  running. **Which callers that leg reaches is narrower than "every one", and
  it is measured (HC2).** It reaches `aelix extension discover --refresh`,
  whose clone runs the wait on the MAIN thread — and there only from the
  SECOND ^C, because the CLI runs under `asyncio.run` and `Runner._on_sigint`
  cancels the main task and RETURNS on the first: measured, one ^C left a
  child running to its full bound (the timeout ladder ended it) and two ran
  the interrupt ladder at 0.25 s. What it buys there is that the tree is dead
  before `_git_clone_bytes`'s `finally: shutil.rmtree` deletes the directory
  out from under a live `git`. It does NOT reach `ExtensionAPI.exec`, which
  awaits the helper on an `asyncio.to_thread` worker: CPython runs signal
  handlers on the main thread only, so at that site the leg fires only for a
  `BaseException` raised INSIDE the worker and a terminal ^C never enters the
  frame at all (measured: two ^C, ladder calls `[]`). That site is closed by
  `AbortHandle` instead — the exec paragraph below. `_taskkill_tree` was
  itself #221's shape — `capture_output=True` with a discarded result,
  followed on win32 by that same unbounded post-kill `communicate()` — so all
  three of its stdio streams are `DEVNULL` now and there is no pipe left for
  the join to wait on.

  **The session decision at these three sites is the opposite of the one taken
  above for `!command`, and it is measured.** All three spawn with
  `start_new_session=True`. `process_group=0` — this ADR's own answer at its
  new sites — was measured against a real `git clone` over ssh with an unknown
  host key: `git`, `ssh`, `sshd-session` and `sshd-auth` all went to `T` and
  **no prompt was ever printed**, for the full 60 s, because `ssh`'s
  `read_passphrase` calls `tcsetattr` and `tcsetattr` from a background process
  group raises `SIGTTOU` group-wide, just as a `/dev/tty` READ raises
  `SIGTTIN`. The pty measurement recorded above is narrower than it was read to
  be: it showed that a `process_group=0` child can *open* `/dev/tty`
  (`HAVE_TTY`), not that it can read from it. So at these sites a group inside
  the session is a silent stall, not a prompt. The other alternative — no POSIX
  group at the git site, which is the one shape that keeps an interactive first
  clone working — is ruled out by the leak #221 exists to close: `git
  remote-http`, blocked in libcurl against a server that accepts and never
  answers, outlives a `SIGKILL` aimed at `git` alone and is alive 3 s later at
  `ppid 1` in our group, and the ssh transport helper likewise, so "the remote
  helper exits on pipe EOF anyway" is false. With no controlling terminal every
  tty read fails at once with the tool's own message — `fatal: could not read
  Username for '…'`, exit 128, measured 0.08 s on darwin (the tail after that
  colon is the platform's `strerror(ENXIO)` — `Device not configured` on
  darwin, `No such device or address` on Linux — so it is not quoted here);
  `Host key verification failed.`, exit 128 at 0.62 s — which
  `_git_clone_bytes` already surfaces, while askpass programs and GUI/keychain
  helpers need no terminal and keep working. **"Non-interactive" is therefore
  about the terminal and nothing else (DOC-3/SITE-3).** An askpass program is
  still used and can still prompt in its own window — `GIT_ASKPASS` /
  `SSH_ASKPASS`, and VS Code exports `GIT_ASKPASS` unconditionally — measured,
  a contained clone under a recording askpass called it twice; a prompt nobody
  answers costs the full 60 s clone timeout before the clone fails, exactly as
  it did before #221. **What it gives up, taken in the owner's absence and
  recorded so it can be reversed:** a catalog clone is now non-interactive on
  the terminal — a first clone from a machine whose host key is unknown, or
  whose key needs a passphrase no agent holds, fails fast with git's or ssh's
  message instead of prompting, and `docs/guides/private-catalog.md` says so —
  and an extension command that expects a terminal fails at once rather than
  stopping until its timeout, while one that reaches for an askpass-style
  program can still block until that timeout. **Both sentences are POSIX
  sentences, and every surface that carries them now says so (post-merge
  review adversary-1):** on win32 there is no session to take away —
  `containment_spawn_kwargs` returns `CREATE_NEW_PROCESS_GROUP` and the job is
  the containment — so both children keep the console Aelix was started from,
  and a program that reads it directly (git reads `CONIN$`, not our stdin,
  which is `NUL` at both sites) can still prompt there; unanswered, that prompt
  costs the full `GIT_CLONE_TIMEOUT` at the clone and the command's own timeout
  at `exec`, which is exactly the stall the POSIX sentence says has been
  designed away. The guide, both CHANGELOG bullets, the row in
  `docs/decisions/README.md` and the clone's own `CatalogError` all carry the
  qualifier; Windows remains an unsupported host (README).
  **The reversal path** is not
  `process_group=0` but "no POSIX group at the git site, job on win32 only",
  and its measured cost is the `git remote-http` leak above.

  **`kill_on_close=False` at all three, and the knob the first draft of
  `run_contained` had is gone.** "A member still alive after the command exited
  is a stray by definition" is false: `git credential-cache--daemon` is spawned
  by `credential-cache.c`'s `spawn_daemon()` without waiting and without
  `setsid`, so it re-parents to init and stays IN the clone's group — measured,
  `ppid 1` with the git child's pgid. The timeout `killpg` therefore reaches it,
  which is accepted and bounded: only a daemon THIS clone started, at most 60 s
  of cached credentials, and the next `store` restarts it (measured). The
  success path must not reach it and does not, because `close()` here is a
  release exactly as this ADR's own criterion says.

  **The exec surface diverges from Pi deliberately** (ADR-0235: a divergence
  needs no ADR, so this is a record and not a justification). No SIGTERM rung,
  for the reasons above; `code=124` on a timeout and `127` on a missing binary
  where Pi returns `0` with `killed=true` and `1`; the kill is a tree kill and
  not a root kill; the command has no controlling terminal where Pi's stays in
  session; and the drain has a cap where Pi's has none. It converges where
  today's behaviour was the bug: stdin is ignored rather than inherited from the
  TUI, output is decoded tolerantly instead of raising the `UnicodeDecodeError`
  the locale codec raises today, and the drain is Pi's exit-then-idle rule
  (Pi #5303/#5753).

  **#239 pays back the cost this paragraph booked.** It read "the cost is
  legacy-codepage output that used to decode correctly" — a real cost, and one
  measured on a Korean Windows box: `"위치 줄:1 문자:14"` in CP949 reached the
  model as `��ġ ��:1 ����:14`. The decode is no longer a bare
  `errors="replace"` anywhere a child is read. It is
  `aelix_ai.utils._child_output.decode_child_output`, which splits the buffer
  into maximal non-ASCII runs and gives each one UTF-8 strict, then the console
  output code page (`os.device_encoding(1)`, then `oem`), then `replace` as the
  floor. Two rules the module's own review added keep that from being a
  half-fix: a failed run is offered the ASCII bytes that FOLLOW it, because
  cp932/936/949/950 take `0x40-0x7E` as DBCS trail bytes and the run boundary
  otherwise cuts a character in half; and a page that decodes all 256 single
  bytes gets **nothing** — neither a whole run nor the bytes UTF-8 could not
  begin at — because its accepting them is no evidence about them. The #239 CROSS-REVIEW added a third, and it lands on this
  ADR's own surface: a buffer whose END is a byte-exact CUT rather than a
  boundary the child chose says so, and a character the cut severed then stays
  U+FFFD instead of being spelled by the code page. `exec`'s TIMEOUT branch is
  such a buffer — `run_contained`'s `TimeoutExpired` carries everything the
  reader had at the deadline — so `_decode_output` now takes a `ragged_tail`
  claim and that branch sets it. The success path does not: there the child
  chose where its output ended. Off win32 the fallback list is empty by
  construction, so a buffer UTF-8 rejects takes the floor in one call and the
  site is byte-identical to what this ADR shipped. What is NOT bought back, and this is the FINAL PASS's correction
  (2026-09-09): a Western box gets nothing back at all. This paragraph read
  "on a Western box an ANSI byte decodes as its OEM character rather than as
  U+FFFD (`0xFC` is `ü` in cp1252, `³` in cp850) — accepted because ANSI and OEM
  agree in the CJK locales where this was reported", and windows-latest refuted
  the premise: the same single-byte acceptance spelled a binary `0xFF` as
  cp437's U+00A0, a NO-BREAK SPACE, so the "these bytes were lost" marker became
  INVISIBLE and `tests/test_extension_issue5_runtime_and_trust.py`'s two
  `test_exec_replaces_undecodable_bytes_*` cases went red on py3.11 and py3.12
  (CI run 34238825800). A single-byte page is now offered nothing, which buys a
  checkable invariant — **with no DBCS page in the chain the decoder is
  `errors="replace"` byte for byte**, measured over 351 exhaustive windows and
  20000 random blobs against six single-byte chains and all four cut-end claims
  (488 424 decodes, 0 differ)
  — and costs the Western legacy recovery outright: 16 accented console lines a
  cp850-writing child produced decoded 16/16 correctly under the cut of this
  decoder that still offered single-byte pages, and 16/16 as U+FFFD now. THAT
  BASELINE IS AN UNRELEASED ONE: `0.1.0-beta.1` decoded those same bytes
  `utf-8`/`errors="replace"` and marked 16 of 16 too, so a release note compares
  against no change at all (re-measured 2026-09-09). Given up because those
  same 16 come back 16/16 *confidently
  wrong* when the child writes the ANSI page instead, and on a Western box ANSI
  and OEM always differ (1252 against 850 or 437) where in the CJK locales they
  are the same number. #239 is scoped to the DBCS consoles it was reported
  from.
  Universal-newline
  translation is kept on BOTH paths; `text=True` gave it only to the success
  path.

  **A regression this change introduced at the exec site, and the handle that
  closes it (SITE-1).** Giving `api.exec`'s child a session of its own also
  took it out of the terminal's foreground process group, and the interrupt
  leg cannot stand in for that here: the call is awaited on an
  `asyncio.to_thread` worker, a POSIX signal reaches the main thread only, so
  a ^C never enters the helper's frame. Measured on the exec site's exact
  shape — a SIGINT to the process group at 1.5 s of a 30 s child — the
  interrupted parent exited in **0.02 s** on `main` (the ^C reached the child
  through the terminal and killed it) and in **28.58 s** contained without the
  handle: the command's whole remaining life, with `Runner.close`'s 300 s
  executor join and `concurrent.futures.thread._python_exit`'s unbounded join
  as the ceiling behind it. So `run_contained` takes an `AbortHandle`: the
  caller holds it, `api.exec` calls `abort()` from its `except
  asyncio.CancelledError` leg, and the handle hard-kills the tree from the
  main thread while the worker is still blocked in `proc.wait`. A cancelled
  turn — Esc, or ^C in `aelix -p` — now ends the command *while it is still
  running*, and the worker returns with the kill's `returncode`; since #230 it
  returns **without draining the tail**, because the abort also ends the drain,
  and a cancellation arriving inside the post-exit drain ends that drain and
  nothing else (the ~54 ms before the reap is publication latency, and the
  amendment at the end of this file bounds it).

  **One loss, worded as a loss.** `api.exec`'s child now leads a session of its
  own, so on a POSIX host with no `/proc` — macOS — the delegation escalation's
  group kill (Q1 above) no longer reaches an extension `exec` in flight when a
  delegation is aborted. Measured: 0 survivors today, 2 under this design (the
  command and its non-`setsid` grandchild). Linux keeps the coverage through the
  `/proc` walk and win32 through inherited job membership (**unmeasured**, read
  from source). On macOS `run_contained`'s own ladder is then that tree's only
  reaper, and with `timeout_ms=None` there is none at all once the agent process
  is SIGKILLed. [#228](https://github.com/handochan/aelix-ai/issues/228) carries it; the WeakSet-of-live-trees
  mitigation proposed in `print_mode`'s signal block is that issue's design and
  not this one's.
- **The two tool spawn sites hold a tree, and the bash tool's drain got a bound
  (amendment, 2026-09-05).** #222 converted the last two teardown paths in this
  repository that addressed a pid: `_LocalBashOperations.exec` and
  `run_cancellable`. Both spawn through
  `containment_spawn_kwargs(new_session=True)` — the `setsid` they already asked
  for, plus `CREATE_NEW_PROCESS_GROUP` on win32 — and attach a `ProcessTree`
  (`kill_on_close=False`) **before the first `await`**, so every kill leg runs
  the ladder the other sites run: `hard_kill()`, then `proc.kill()` as a belt,
  then a bounded reap. **The bound differs per site, because the legs do**:
  `exec` has three (timeout, abort, cancel) and reaps through `_end_the_tree`
  at `REAP_GRACE_SECONDS` on the timeout leg and `INTERRUPT_REAP_SECONDS` on
  the other two, matching `run_contained`; `run_cancellable` has no abort leg
  at all — nothing signals `rg`/`fd` — and its two legs keep the flat
  `wait_for(proc.wait(), 2)` they already carried, so the primitive's reap
  constants do not appear there. `finally: tree.close()` is a release, not a
  kill, at both — a command that exits 0 after backgrounding a helper keeps the
  helper, as at the hook sites.
  **On win32 the verdict for the tool children is now whole**, and this is the
  site where that mattered most: a job holds a descendant whose parent has
  already exited, which is exactly what `taskkill /T` cannot walk to (Pi #9129),
  and `exec` read stdout **to EOF** — so the MSYS pipeline whose subshells
  `taskkill` killed kept the pipe open and the tool call did not return, past
  its own timeout. That is the ADR's own "user-visible hang rather than a leaked
  process". The tense is the point: **no leg of `exec` reads to EOF any more** —
  #222 bounded the three kill legs and #232 the ordinary exit — so what the job
  buys at this site is that the descendant DIES, not that the drain ends. It is
  reasoned from CPython's and Pi's source, as every win32 claim in this file is;
  the leg's numbers go in `.omc/specs/222-progress-2026-09-05.md`.

  **The POSIX-visible half of #222 is the drain bound, and without it the issue
  would have been inert on the platform development happens on.** Measured on
  `main` 86c750d: with a `setsid` python holding the bash child's stdout,
  `exec` under a 1.0 s timeout returned at **8.02 s** — the escapee's own life,
  not ours — and at 8.02 s and 8.03 s on the abort-signal and `CancelledError`
  legs. The group kill ended `sh` and `sleep` at 1 s and then the drain waited
  7 s for the escapee's copy of the pipe. The kill still does not reach that
  escapee — a `setsid` descendant is outside the group, which is what a process
  group is, and the walk is still the reaper's job — so what changed is the
  **wait**. After whichever leg kills first writes `_ReadState.exited_at`, the
  post-kill drain runs `run_contained`'s rule rather than a fourth one written
  from scratch: the `EXIT_DRAIN_SECONDS = 0.1` idle timer re-armed from
  `max(last_chunk_at, exited_at)`, under an absolute cap of
  `exited_at + KILL_DRAIN_SECONDS = 1.0`. Esc with a surviving holder now costs
  ~0.1 s and at most 1 s. A flat cap without the idle rule would have cost a
  flat 1.0 s where `run_contained` costs ~0.1 s, and #221 already recorded that
  identical mistake costing a flat 5 s and buying zero bytes. The price is the
  one `run_contained` states: a descendant still writing after the idle window
  has its output cut. The command was killed; its bytes are a courtesy.

  **#232 decided it (amendment, 2026-09-08).** #222 left the success path's
  drain unbounded on purpose and pinned it so the follow-up would be visible;
  the owner chose Pi's rule on 2026-09-06, and the site now drains exactly as
  `run_contained` does: idle `EXIT_DRAIN_SECONDS = 0.1` from
  `max(last_chunk_at, root_exited_at)`, an absolute
  `DRAIN_CAP_SECONDS = 2.0` past the exit, and — where the caller supplied one —
  never past `start + timeout`, floored one grace past the exit. Two defects
  went with the old behaviour, not one. The reported one was the wait: a root
  that exits 0 after backgrounding a pipe-holder held `exec` for the holder's
  whole life — measured on `7fa6796`, **4.050 s** for a 4 s helper and
  **0.143 s** after. The second was that the tool's own `timeout` bounded this
  path not at all: a 13.9 s helper held a `timeout=10` call for **13.983 s**
  (**0.141 s** after), and a `timeout=1.0` call came back at **4.059 s** with
  `exit_code=0` and `timed_out=False` — telling the model it had succeeded
  inside a deadline it had missed fourfold (**0.141 s** after). Pi's rule is
  adopted with a cap and not whole, because Pi's is uncapped and a holder that
  never falls idle defeats an idle rule: a helper writing every 50 ms for 5 s
  measured **5.045 s** with `timeout=10` and **5.076 s** with no timeout at all,
  against **2.042 s** and **2.028 s** here — and a `timeout=1.0` call in the
  same shape comes back at **1.001 s**, which is the term that makes the tool's
  own knob mean anything on this path. It is the divergence from Pi that
  `DRAIN_CAP_SECONDS` already records for `run_contained`, now at a second
  site.

  What it costs, in three parts. **A helper's output written after the grace is
  cut**, exactly as `run_contained` states for its own drain — **and in practice
  that is not a tail but the whole of it**: a backgrounded program's stdout is a
  pipe, so it is block-buffered and typically flushes only when it exits
  (measured, the `python3 -c "print('started'); sleep(4)" & echo now` shape:
  `now` reaches the pipe at +0.002 s and `started` at +4.018 s), which means a
  model that backgrounds a server sees **none** of its output rather than most
  of it — redirect it (`nohup … > dev.log 2>&1 &`) or run it unbuffered.
  **And the call comes back but the reader does not**: one daemon reader thread
  and one pipe descriptor stay with the helper until it closes its end
  (measured, five successful calls each backgrounding a 60 s holder: readers
  1→5, fds 8→12, released at the holder's exit; zero on `main`, which did not
  return at all). That is kept deliberately — the thread cannot be unblocked
  (this module's stated leak, #221 §I) and a reader that stopped reading would
  wedge the helper on a full pipe, where Pi instead destroys the stream.
  **What no longer stays is the caller's OUTPUT**: the reader used to cache
  `on_chunk` in a frame local, so a reader parked on a helper's pipe pinned the
  bash tool's whole `on_data` graph — one command's entire raw output, 8 MiB per
  call — which never surfaced while a successful `exec` did not return. The
  callback is loaded per chunk and dropped by `detach` on the caller's thread
  now (measured: with the frame local restored, an 8 MiB command's `on_data`
  object is still reachable after `exec` returns and a `gc.collect()`; with the
  fix it is collected). **Nothing else in the teardown moved**:
  the watcher is still disarmed before the drain, so an abort landing in the
  window still fires into nothing.
  [#230](https://github.com/handochan/aelix-ai/issues/230) decided that same
  policy at `run_contained` — an `abort()` after the reap kills nothing — and
  this site was already there, by the watcher order #222 chose; the two agree
  on the leg axis. What this amendment changes is the window's WIDTH: at most
  2.0 s instead of the holder's lifetime.

  **The drain also moved off `asyncio.to_thread`, for a reason that outranks the
  tool call.** An abandoned `to_thread(proc.stdout.read, …)` can be neither
  cancelled nor dropped — the default executor's threads are not daemons — so
  measured, it held `asyncio.run` for the escapee's whole 30 s, with
  `THREAD_JOIN_TIMEOUT = 300` and then an unbounded `_python_exit` join behind
  it: "the tool call hangs" becomes "Aelix cannot exit". The replacement is the
  daemon `_PipeReader` #221 already paid for, given `on_chunk` / `on_eof`
  callbacks that post to the loop under `suppress(RuntimeError)` (a closed loop
  must not raise into a reader thread; `rpc_mode`'s daemon→loop pump already
  suppresses the same). Two properties are pinned rather than left to timing: a
  reader with an `on_chunk` **retains nothing**, because the callback is the
  consumer (measured on 200 MB of output — 535 MB peak RSS holding it twice
  against 285 MB holding it once), and chunks and EOF travel the **same**
  `call_soon_threadsafe` FIFO, so the waiter cannot resume before every earlier
  chunk callback has run — measured 0/4000 lost against 9/3000 to 15/250 for a
  draft that polled an `eof` flag instead. `_wait` keeps its
  `to_thread(proc.wait, timeout)`: it blocks on the **root** only, which the
  belt always ends, which is what makes that belt load-bearing for interpreter
  exit and not only for the reap.

  **`stdin` is `DEVNULL` at the bash site now, and the first half of that
  sentence is a POSIX sentence.** Pi spawns `stdio: ["ignore", "pipe", "pipe"]`;
  an earlier draft justified copying it with "the child gets `SIGTTIN` and the
  group stops", which is measurably wrong **here** — under
  `start_new_session=True` the child has no controlling terminal, so the
  kernel's background-group test never applies (measured on a real ctty pty:
  the setsid child is `Ss`, a `process_group=0` child is `T`). What was actually
  happening is worse for being invisible: the child **competed with the TUI's
  own reader for keystrokes** (measured — the child got `'secret\n'`, the TUI's
  `read` got `b''`) and could `tcsetattr` the user's terminal (measured — ECHO
  turned off, and left off after the child exited). `!command` in the TUI runs
  this exact `exec` with no timeout and no abort signal, so `!cat` never
  returned and ate the next keystroke on the way. On POSIX `DEVNULL` + `setsid`
  makes the child non-interactive: `cat` prints nothing and exits 0, `git
  commit` without `-m` fails with "Aborting commit due to empty commit
  message." (git's own wording, measured — earlier drafts of this paragraph
  paraphrased it as "no message", which git never prints). **On win32 there is
  no session to take away** — `containment_spawn_kwargs` returns only
  `CREATE_NEW_PROCESS_GROUP` — so the child keeps Aelix's console. A real stdin
  reader gets EOF from `NUL` there, but a program that reads `CONIN$` directly
  (git's credential prompts) or opens the console for a masked prompt
  (`Read-Host -AsSecureString`, `Get-Credential`) still prompts on that console
  and still costs the whole timeout, and only when `default_timeout != 0`
  (`bash.py` documents `0` as Pi's unbounded mode). That half needs a human at a
  Windows console: it is **unverified**, and the README says so rather than
  claiming it fixed. `run_cancellable` takes no `stdin=` change — `rg` and `fd`
  never read stdin — and the asymmetry is stated in that module's docstring
  rather than left to be wondered about.

  **What is accepted, listed rather than discovered later.** On win32 the bash
  child no longer sees a console Ctrl+C, as at every other containment site
  since #220; the cancel and abort legs end it, and that leg is now a job kill
  rather than a `taskkill`. On POSIX a `setsid` descendant still survives the
  kill — what changed is that it no longer hangs the tool. A detached reader
  still keeps one daemon thread and one fd alive until the holder closes the
  pipe, #221's stated leak, now at a fourth site; it keeps reading so the holder
  is never wedged on a full pipe. And every rung of the ladder runs on the
  event-loop thread: measured 1.6 ms on POSIX (two syscalls plus a reap, worst
  heartbeat gap 12 ms = the sleep quantum), while on win32 it is the ≤5 s
  `taskkill` spawn this site already ran on the loop at the same three places,
  plus the reap only when both the job kill and the `TerminateProcess` belt
  failed. The `to_thread` remedy was rejected in #220 review round 2 for opening
  an unshielded suspension point and is not re-adopted here.

  **A cancellation that lands in the teardown is delivered now (amendment,
  2026-09-06).** #234 replaced that teardown's `suppress(CancelledError,
  Exception)` + `await watcher_task` with `asyncio.wait([watcher_task])` and an
  explicit retrieval, and what the change costs is scoped **per leg**, because
  three of `_wait()`'s dispositions reach this `finally` with a live watcher and
  are not alike (a fourth — a cancellation raised out of `_wait` itself — reaches
  it unchanged, with that `except` leg on the stack, and is pinned by
  `test_bash_exec_cancel_watcher_teardown_catches_exception`).
  *This* leg ends no tree of its own — the `except asyncio.CancelledError` that
  does is around `_wait`, which has already returned by the time the teardown
  runs; on the TIMEOUT leg a tree was already ended there, before the teardown.
  The helper `kill_on_close=False` exists to keep is kept **on the normal-exit
  leg** (measured `alive_after_exec=True` there, and `False` on the timeout
  leg, where `_wait` had already killed it). The exit-path drain still belongs
  to the **normal-exit leg** only — on the timeout leg `exited_at` is set, so
  the post-kill cap runs instead (0.407 s with a 3 s escapee holding stdout) —
  but it is no longer the unbounded one this paragraph described when it was
  written: since #232 both legs run the same `_drain_after_the_exit` and differ
  only in the cap they hand it, and the 2.03 s that stood here was the holder's
  own life rather than a bound. And the drain, the `detach` and the `close` all
  still run while the cancellation propagates — pinned by
  `test_a_turn_cancel_in_the_watcher_teardown_keeps_the_helper_and_still_detaches`
  in `tests/tools/test_bash_tool_containment.py` (2.031 s on darwin when that
  case waited for the holder's tail, **0.126 s** since #232 cut it, and the
  windows leg reports its own through that case's `UserWarning`) rather than by
  a scratchpad script. The one thing the caller sees differently beyond
  receiving its cancellation at all: **a cancellation landing in the teardown
  after a timeout kill is now delivered as a cancellation instead of the
  `timed_out=True` result the tool would have rendered as a timeout report.**
  Whether an abort in this window should kill anything was
  [#230](https://github.com/handochan/aelix-ai/issues/230)'s, and #230 answered
  it: nothing, once the root has been reaped.

- **`!command` keeps the terminal — as its CONTROLLING terminal, which is a
  smaller claim than this ADR made (amendment, 2026-09-06, #226).** The #221
  amendment **above** already narrowed the pty measurement this bullet rested
  on — it showed that a `process_group=0` child can *open* `/dev/tty`, not that
  it can read from it — and #226 is that narrowing arriving at the site the
  original paragraph was written about. Measured under a real pty on macOS (the
  author's box) and on Linux/dash, the only POSIX CI leg: a `!command` helper
  that reads the terminal is STOPPED by `SIGTTIN`, and one that calls
  `tcsetattr` is STOPPED by `SIGTTOU` — **unless it blocks or ignores
  `SIGTTOU`, which POSIX permits and which was measured to succeed on the
  controlling terminal**, leaving the setting applied and nothing to detect.
  The signals are delivered to the process GROUP, so a pipeline's leader stops
  with the stage that touched the terminal, which is what makes this work on the
  gating leg at all: dash does not `exec` a lone simple command from `-c`, so
  the helper there is always the leader's child. Nothing about that is quoted
  from a platform's `strerror`, and the "a single command is `exec`'d" shorthand
  is darwin's bash 3.2 and not a POSIX fact.

  **What lands is detection, not a new decision.** `_run_shell_command` polls
  its reader in 50 ms quanta and asks `waitpid(WNOHANG|WUNTRACED)` on each one;
  a `WIFSTOPPED` whose `WSTOPSIG` is one of those two ends the tree immediately
  and names the cause. Measured 0.054 s on darwin and 0.052–0.059 s on
  Linux/dash across py3.11 and py3.12, root and non-root, against the 10 s the
  timeout used to cost — and against the real binaries, all of which leave
  `SIGTTOU` at its default and were all detected: `pinentry-tty` (0.355 s),
  `pinentry-curses` (0.05 s), `ssh`'s `readpassphrase` through `ssh-keygen -y`
  (0.051 s), `sudo` (0.051 s), and a git credential helper that uses `stty`
  (0.05 s). This amendment does **not** reopen #221: the detector lives in
  `_run_shell_command`'s wait loop and not in `run_contained`, and at the
  `git clone` and `exec` sites `setsid` already produces the tool's own message
  in 0.08 s and 0.62 s, which is better than a named stop.

  **The decision stands and its rationale is replaced, on all three legs.**
  `setsid` was rejected because it turns a visible stop into a silent theft:
  measured, a `setsid` helper that opens the terminal by path (`$GPG_TTY`,
  `/dev/ttysNNN`) waits with no job-control check at all and took the line the
  user had typed at Aelix's own prompt, where the same command under
  `process_group=0` is `T` and gets nothing. Passing no group kwarg at all was
  rejected because the signal is delivered group-wide: with Aelix backgrounded,
  Aelix and the helper were **both** stopped, still stopped at 20 s, with the
  timeout never firing (a stopped process has no thread left to notice it) and
  `SIGCONT` not recovering it — only a human typing `fg`. Handing the foreground
  to the helper (`tcsetpgrp`) was rejected by measurement rather than by
  argument: the prompt does work in print mode (rc 0, the typed line, 1.026 s,
  terminal restored), but the handover is a sub-millisecond race that needs a
  `SIGCONT` whose timing only this amendment's detector can know; an abandoned
  prompt produces no stop to detect and leaves the terminal with echo off after
  the full timeout and an uncatchable `SIGKILL`; and in the TUI it stops Aelix
  itself in 21 ms, on its own stdin read and on `prompt_toolkit`'s raw-mode
  `tcsetattr`. **The draft objection that `^C` would reach the child is wrong**
  and is recorded as wrong: measured, `^C` cancelled the prompt and the agent
  survived. So **Aelix never prompts from a `!command`**; the guide says so, and
  it is a stated divergence from Pi, which leaves the helper in Pi's own process
  group — the terminal's foreground group whenever Pi is in the foreground — so
  the helper can prompt (ADR-0235: a divergence needs no ADR, so this is a
  record and not a justification).

  **What this does not reach, said plainly.** A passphrase prompt mediated by
  `gpg-agent` — the normal `gpg` and `pass` architecture — is invisible to the
  detector, because the agent is a pre-existing daemon in its own session and it
  is the agent that forks `pinentry`, where no job-control check applies.
  Measured with gnupg 2.5.22 and pinentry 1.3.3: `!gpg -d` and `!pass show` take
  10.036–10.043 s with the detector silent, against 10.047 s on `main`; our own
  `gpg` sits `S` in its own group and the agent it auto-starts detaches to
  `ppid 1`. The honest half of that is that the pinentry does not outlive our
  kill (it is gone 35–37 ms later and echo is restored), and that
  `gpg --pinentry-mode loopback` — which reads the terminal itself — **is**
  detected, at 0.357–0.359 s. An askpass program or a GUI prompt that nobody
  answers still costs the full timeout: it never reads the terminal, so it never
  stops.

  **The probe pays for itself twice, and both are recorded.** Calling `waitpid`
  on a child that has already exited takes the status `Popen` was going to reap,
  and `Popen._try_wait` swallows the resulting `ChildProcessError` as `sts = 0`
  — measured, an `exit 7` reported as `exit 0`, a failed helper read as a
  successful one — so the status is handed back through
  `os.waitstatus_to_exitcode`. That theft is only reachable where a grandchild
  holds the pipe open past the leader's exit; `!exit 7` never reaches the probe
  at all, because its EOF and its exit land in the same tick. The same call
  consumes the zombie, which is why the `!command` sentence under "The hazard
  this accepts" no longer names this site. `os.waitid(…, WNOWAIT)` avoids both
  and reports a stop repeatedly instead of once, and it was **refused**: it does
  not exist on darwin, and since the gating POSIX leg is Linux only, a platform
  fork would leave the more dangerous path — darwin's reap-and-repair — never
  running in CI. On win32 the detector is inert, because there is no `WUNTRACED`
  and no background process group; it is also invisible to that leg's type
  checker, which is why the four POSIX-only `os` names carry
  `# pyright: ignore[reportAttributeAccessIssue]` at their sites. A helper that
  reads `CONIN$` there can still prompt on Aelix's console and still costs the
  whole timeout unanswered — **nobody has watched that happen**, exactly as
  #221 and #222 say of their own Windows halves.
- **An `abort()` after the reap kills nothing, at `run_contained` (amendment,
  2026-09-08, #230).** The policy is now stated once and holds on both sides of
  the reap: an `abort()` ends the CALL as fast as it can, and after the root has
  been reaped it ends nothing else. 🔴 **The owner decided that on 2026-09-06**,
  against the alternative of killing anyway — which is incoherent here, because
  the call has already bound `returncode = 0` and would be reporting a success
  while killing what that success deliberately left behind (measured, rc 0 with
  the helper dead, 6 times in 6). `AbortHandle._finish` therefore runs at the
  reap and not merely in the call's `finally`, and `_drain` ends on a fourth
  condition — the caller aborted — keyed on `AbortHandle.aborted` rather than on
  the disarm, so it also fires on the one leg where the disarm is skipped.
  **The two sites now agree, and the axis is the LEG rather than the site.** #222
  disarms the bash tool's abort watcher before its drain, so on the
  ABORT-SIGNAL leg that signal is inert and the drain runs to EOF for a caller
  that is still listening; `run_contained` has no such leg, its only
  handle-passing caller being `ExtensionAPI.exec`, whose abort *is* the task
  cancellation. On the TASK-CANCELLATION leg both sites kill nothing, and what
  becomes of the drain depends only on where the `CancelledError` lands —
  measured, inside the await it cuts it at 0.02 s (3/3), before the `finally` it
  does not and the drain completes (0.5011 s, 3/3, which is what
  `test_a_turn_cancel_in_the_watcher_teardown_keeps_the_helper_and_still_detaches`
  pins). `run_contained`'s drain is a synchronous loop on a worker thread that
  no `CancelledError` reaches, which is what the fourth condition substitutes
  for.
  **The window is narrowed by three to four orders of magnitude, not closed** —
  and the word "closed" is wrong for two measured reasons. Between
  `proc.wait`'s return and the disarm taking the handle's lock the residue is
  0.125 µs idle but **0.055 ms median / 0.103 ms max when another thread is
  running Python**, which the aborting thread here is; an aborter synchronised
  on the reap landed in it 17 times in 20 and killed the helper 17/17. And
  BEFORE the reap the root is dead-but-unpublished — `returncode` is stored by
  the run thread itself — for 0.712 ms at `timeout=None` and up to ≈54 ms when
  the caller passed a `timeout`, because `Popen._wait(timeout=)` polls with a
  delay doubling to a 0.05 s cap; an abort 2 ms after an rc-0 root's exit under
  `timeout=10.0` still killed the helper 6 times in 6. `ExtensionAPI.exec`
  reaches that path whenever the caller passes `timeout_ms`. What the residue
  exposes is the SAME bug and not a different hazard: the helper is alive, so
  the group is non-empty and its number cannot be recycled — `ProcessTree.close`
  states the other branch's hazard, the group EMPTY, where `killpg` answers
  ESRCH (3/3 macOS, 2/2 Linux) and a stranger would need the pid space to wrap
  inside the residue against a floor of 85.6 s on the measuring host. Closing
  the rest needs a reap the handle observes itself, or an `abort()` that tests
  tree liveness; a `returncode is not None` test in `AbortHandle.abort()`,
  returning `False` before the ladder, was measured, is sound, and was
  **rejected** — its exclusive coverage is ~2.5 µs, and with it in place
  deleting the reap disarm passes every test this change adds (3/3), which is
  two mechanisms for one fault with the tested one removed. Placement is the
  whole of it: the same test inside `_kill` suppresses the ladder but not
  `abort()`'s `True`, so those three cases fail 0/3 on `sent == [False]` while
  ladder, `kills` and the helper's survival still hold.
  **The interrupt leg is untouched** — see the "still open" bullet above; this
  amendment is about `abort()`.
  **The one cost, worded as a cost:** the fourth condition fires whenever
  `aborted` is true, including for an abort that DID kill during the wait, so a
  holder outside the tree — a `setsid` descendant, a job escapee — no longer has
  its post-kill tail drained. Measured on the branch and **not pinned by a
  test** — 87 bytes over 2.004 s become 13 bytes returned 0.0009 s after the
  abort, and the caller they would have gone to has
  unwound: through the real `ExtensionAPI.exec`, an awaiting caller sees the
  `CancelledError` and **no `ExecResult` at all** — no code, no output, no
  `killed` flag — 0.0001 s after `cancel()`, on `main` and after this change
  alike (4/4 each). The only observable difference is the helper afterwards:
  DEAD 4/4 before, ALIVE 4/4 now.
