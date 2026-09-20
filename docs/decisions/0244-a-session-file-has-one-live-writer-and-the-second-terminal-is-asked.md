# 0244. A session file has one live writer, and the second terminal is asked

Status: Accepted (2026-09-20)
Date: 2026-09-20
Supersedes/relates: ADR-0242 (the JSONL write discipline — its §4
"No rewrite-on-open" keeps its decision and gets a dated **amendment**, because
half of its stated reason stops being true here), ADR-0208 (non-destructive
recovery, unchanged), ADR-0035 (a `Literal` widening is recorded by an owning
ADR — this one owns `SessionErrorCode`'s `read_only`), ADR-0091 / #46 (the two
POSIX-only `fcntl` sites, explicitly **not** migrated here), ADR-0235 (pi is a
reference, not a target, so the divergence below is recorded, not argued).
Owner decisions D1 / D2 / D3 in `.omc/specs/137-297-300-decisions-2026-09-20.md`.
Issue: #137. Same lane: #297 (ADR-0092 amendment) and #300 (ADR-0079
amendment), both of which had to land first — D1's fork path walks through
exactly the two things they broke.
Design spec: `.omc/specs/137-297-300-design-2026-09-20.md`, with the
corrections in §"Falsified" below.

Two terminals, one session, and one terminal's whole turn is gone.

## What was broken on `47dfacd`

`aelix --continue` deterministically resolves the same file for every terminal
in one directory, so two of them is not an edge case — it is what happens when
you open a second tab. Measured on the parent of this branch, three appends
from two writers:

```
every entry on disk          = ['A-turn-1', 'B-turn-1', 'A-turn-2']
lines on disk (incl. header) = 4, all parse = True
replay after reopen          = ['A-turn-1', 'A-turn-2']
```

**Nothing is torn.** Every append is one `O_APPEND` line, every line on disk is
valid JSON, and `B-turn-1` is sitting in the file. It is invisible because
`append_entry` parents each new entry onto a **process-local**
`_current_leaf_id` (`session/jsonl_storage.py`), so B hung its turn off a leaf A
had already moved past. The losing branch is an orphan no replay ever walks.

The issue says this in its own title — "flock is NOT the fix" — and it is
right: a byte-range lock on the session file's descriptor would have changed
nothing at all. What has to become impossible is the second **writer**.

## Decision

> **A session file has at most one live writer. Everything else either forks,
> or does not write.**

### 1. The second terminal is asked, not refused and not silently forked (D1)

Interactively, on the same one-shot selector the project-trust gate uses:

```
This session is already open in another terminal.
  <path>

→ Fork and continue here (a new session file, same history)
  Open read-only (view only; this terminal cannot write)
  Cancel
```

The default cursor is the only thing that varies: `--continue` / `--resume`
mean "the session I was working in here", so a fork of it is the honest
continuation; `--session <file>` named *that* file, so the cursor sits on
read-only. Esc, Ctrl-C and a missing `[tui]` extra are all cancel — the same
fail-closed rule as every other consent gate, for the same reason: "no answer"
may never be read as consent to write into a file someone else owns.

**This is a deliberate divergence from pi**, recordable under ADR-0235 rather
than arguable: pi's experimental track refuses the second opener outright
(`experimental/micro/sessions.ts`, `retries: 0`, "Micro session is already
open"), and pi mainline has no cross-process locking at all — its
`Session is already open` is a per-process `Map` (`jsonl/repo.ts`), so pi
mainline is in the same state #137 describes. Four pi issues are this exact
defect (#9596, #8848, #8177, #8334) and all four were closed by a
new-contributor bot, not by a maintainer judgement.

### 2. A non-interactive run is refused (D1's open question, resolved)

`-p`, `--mode json` and `--mode rpc` get the message and exit 1:

```
Error: this session is already open in another terminal.
  <path>
Use --fork <path> to branch it, --session <other> to pick another session,
or --no-session to run without one.
```

A process that cannot ask must not choose data loss on the user's behalf, and
must not silently scatter session files through somebody's loop. Narrower than
it sounds: contention only arises when a non-interactive run *targets an
existing session*, so a bare `-p` creates a new file and never reaches it.

RPC is the one that matters most, and the design got it wrong before this ADR.
Its transport **is** stdin/stdout JSONL, and an rpc child's stdin is already
held by a blocking reader thread — a full-screen `prompt_toolkit.Application`
there would paint ANSI into the response stream and then block forever on a
stdin nothing will ever write to. The guard is therefore not a special case in
`rpc_mode`: the prompt callback is installed **only** by the interactive TUI, so
every other surface inherits the kernel's default, which is to raise
`SessionError("storage", …)`. `_handle_command`'s existing envelope turns that
into a structured error on the wire. The site that can contend is
`_handle_switch_session` — not `_handle_fork` or `_handle_clone`, which call
`runtime_host.fork(...)` and create a new file that can never be contended.

### 3. The lock is `filelock` on a sidecar (D3)

`SessionWriterLock` (`session/session_lock.py`) over
`<session>.jsonl.lock`, acquired with a 250 ms timeout.

- **A sidecar, never the `.jsonl` itself.** On Windows filelock uses
  `LockFileEx`, whose byte-range lock is *mandatory*: locking the session file
  would make a read-only viewer's `read()` of byte 0 fail there and nowhere
  else. `.jsonl.lock` does not end in `.jsonl`, so neither `find_most_recent`
  nor `list` can see it.
- **250 ms, not one non-blocking attempt.** A quarter second absorbs the
  legitimate handoff — the other terminal running `/quit` or `/resume` at that
  instant — and on Windows it covers the gap between `UnlockFileEx` and the
  post-release `unlink`. It is imperceptible at startup and removes a class of
  wrong prompt.
- **No heartbeat, no lease, no stale reclamation.** Both backends are
  kernel-held, so `kill -9`, a crashed terminal and a closed lid all release
  them. Pinned by a cross-process test that kills the holder and re-acquires,
  with no reclamation code anywhere. pi's `stale: 2_000 / update: 1_000`
  heartbeat exists because Node has no OS lock API; Python has `fcntl.flock`
  and `LockFileEx`, so that complexity is not ported.

### 4. Read-only takes **no** lock

The decisions doc proposed `filelock.ReadWriteLock.acquire_read`. That cannot
work, for two measured reasons: `ReadWriteLock` is SQLite-backed rather than
`flock`-backed (`_read_write.py` imports `sqlite3`, and `__init__.py` sets it
to `None` when the interpreter has none), and — decisively — a reader/writer
lock excludes a concurrent writer by definition, so it would block on, or
starve, the very owner "open read-only" exists to leave alone.

So a viewer opens the file, reads it, and never writes. That is already safe:
appends are one `O_APPEND` line each (ADR-0242 §2), every line on disk parses,
and the loader already skips a torn tail (ADR-0208). The viewer sees the
session as of the moment it opened.

`ReadOnlySessionStorage` is what makes it real rather than a promise the UI
makes: `append_entry` and `set_leaf_id` raise; the other eight methods
delegate. `create_entry_id` is **not** refused — it is a pure function over the
id set and writes nothing, so refusing it would only move the error somewhere
less informative.

### 5. `SessionErrorCode` gains `read_only` (ADR-0035)

Seven codes now, six of them pi's. A caller needs to branch on "you may not
write here" rather than string-match a `storage`.

### 6. Where the pieces live

| | |
|---|---|
| Mechanism | `aelix-agent-core` — `session/session_lock.py`, `session/read_only.py`, and the lock handoff in `runtime/agent_session_runtime.py` |
| Policy (the wording, the widget, the exit code, the default cursor) | `aelix-coding-agent` — `cli/entry.py`, `tui/shell.py` |

The kernel learns only "a file can be owned, and someone else decides what to
do about it". Both injections are **setters**, not `__init__` keywords, for the
same reason `set_rebind_session` is one: the CLI does not construct
`AgentSessionRuntime` — it calls `create_agent_session_runtime`, whose
parameter list is fixed and shared. A new optional kwarg there would be
silently dropped by any caller that forgot it, which is precisely how an
injected callback ends up permanently `None` and every `/resume` refuses
instead of asking.

`JsonlSessionRepo.open()` stays lock-free: `--export` opens a session purely to
render HTML, and locking there would make `aelix --export` fail against a
session you have open. The lock is an explicit act by a caller that intends to
write, never a side effect of opening.

### 7. Delegated child sessions are locked by the same code, with no special case

A child file has one live writer by construction — the parent publishes it once
before the child exists and then appends only to its own session. Locking it
costs nothing and closes the case a human hits by opening a still-running
child with `--session <child file>`, which until now got a warning (#199) and
then silently reproduced #137.

## The dependency: `filelock`, measured

The owner asked directly what adding a dependency costs and whether it is open
source. Everything here was measured on 2026-09-20 against PyPI and the wheels
in the local uv cache, not read from a README.

### Licence — yes, open source. MIT, no ambiguity

| Evidence | Value |
|---|---|
| PyPI `info.license_expression` (PEP 639) | `MIT` |
| Trove classifier | `License :: OSI Approved :: MIT License` |
| Wheel `METADATA` | `License-Expression: MIT` · `License-File: LICENSE` |
| Wheel `dist-info/licenses/LICENSE` | the MIT text, `Copyright (c) 2025 Bernát Gábor and contributors` |
| Upstream | `tox-dev/py-filelock`, by the tox/virtualenv maintainer |

MIT is OSI-approved and permissive. The only obligation is to carry the
copyright notice and the licence text, which the wheel does for itself and the
SBOM records. Shipping MIT code inside our Apache-2.0 distribution is the
direction that works.

**No new obligation is created, and `THIRD-PARTY-NOTICES.md` is not touched.**
That file names only MPL-2.0 dependencies, because MPL-2.0 is the only licence
in our closure that requires source availability for its own files. MIT was
already the largest family in our SBOM — 33 of 67 components. filelock makes it
34 of 68.

### Weight

| | filelock |
|---|---|
| Runtime dependencies | **zero** — the wheel's `METADATA` has no `Requires-Dist` line at all, so nothing arrives behind it and there are no extras to pull |
| Wheel | `filelock-4.0.1-py3-none-any.whl`, 106,219 bytes, `Root-Is-Purelib: true` |
| Platforms | one wheel for every platform — no compiler, no per-OS build, **no `pywin32`**, which is why `portalocker` lost |
| `requires-python` | `>=3.10`, under our 3.11 floor |
| Typing | ships `py.typed`, so the pyright gate sees real types rather than `Any` |
| `uv.lock` | 79 → 80 entries (filelock was absent, transitively or otherwise) |
| SBOM | 67 → 68 components; `scripts/generate_sbom.py`'s `OVERRIDES` is untouched, because filelock publishes usable licence metadata |

`aelix-agent-core` is a required dependency of `aelix-coding-agent`, so **every
`aelix` install gets this**. It is not an extra and not optional. In the repo it
moves five things together: the manifest line, `uv.lock`, the SBOM, the kernel
allowlist's written reason, and this ADR.

### The floor is measured

This is the part the design got wrong, and it is the real cost of the
dependency.

The design's no-soft-lock argument rested on `FileLock(..., fallback_to_soft=False)`.
That keyword does not exist in the floor the design declared (`>=3.16`).
Counted in the wheels: filelock **3.29.0** contains zero occurrences of
`fallback_to_soft`, 3.32.3 and 3.32.5 contain nine, and 4.0.1 declares it at
`_api.py:672`. It landed somewhere in `(3.29.0, 3.32.3]` — 3.32.3 is dated
2026-08-13, with seventeen releases in between. `>=3.16` would have admitted
dozens of versions where the constructor call raises `TypeError` on the **first
session open**, i.e. on every `aelix` launch including `--continue` and `-p`.
`uv.lock` pins one version and hides that on CI; the floor is what binds for
`pip install aelix` and `install.sh`.

Rather than pay a five-week-old floor for one keyword, the code does not use
the keyword. It asks what the lock **became**: filelock's soft fallback swaps
`self.__class__` to `SoftFileLock`, so an `isinstance` check after the acquire
attempt detects the degradation, and cannot be broken by a keyword rename.

The floor that remains is `filelock>=3.12,<5`, and it is derived from the one
keyword this code does pass — `thread_local=`, so that the owner of a session
file is the process rather than the thread that happened to open it. Measured,
one venv per version, running the properties the lock actually depends on:

| filelock | native, not soft | a second handle contends | re-acquire after release | killed holder reclaimed | a mount without `flock` |
|---|---|---|---|---|---|
| 3.11.0 | — | — | — | — (`TypeError: __init__() got an unexpected keyword argument 'thread_local'`) | — |
| 3.12.0 · 3.12.2 · 3.12.4 · 3.13.4 · 3.16.1 · 3.18.0 · 3.20.0 | ok | ok | ok | ok | **raises `NotImplementedError`** |
| 3.25.2 · 3.29.0 · 3.32.3 · 3.32.7 · 4.0.1 | ok | ok | ok | ok | degrades to `SoftFileLock` |

**The last column is the one review added, and it corrects the paragraph above
it.** The first matrix measured four properties and never measured the
no-`flock` path, so it marked 3.12.0–3.20.0 "ok" across the board. They are
not. `_unix.py` in that range turns a `flock` `ENOSYS` into
`NotImplementedError("FileSystem does not appear to support flock")` instead
of falling back, and the `SoftFileLock` the `isinstance` check asks about only
appears in 3.25.2. Measured with `fcntl.flock` forced to answer `ENOSYS`, one
venv per version, through `SessionWriterLock` itself.

`NotImplementedError` is not an `OSError`, so it escaped `try_acquire`,
escaped `_resolve_session_ownership` and reached `main_sync`'s bare `raise`.
A user who runs `pip install aelix` into an environment that already pins
filelock 3.13.x — torch, huggingface_hub, virtualenv and tox all pull one —
and whose sessions root is on NFS-without-lockd or a FUSE/9p mount got an
uncaught traceback on **every** launch, including a bare `aelix` with a
brand-new session, because ownership is resolved unconditionally at startup.
aelix did not start at all, where this ADR promises one stderr line and a
degraded run. `try_acquire` now catches all three doors — `Timeout` into a
soft lock, `NotImplementedError`, and `OSError` — and each has a test.

**Not every `OSError` is that door, either.** The first implementation read
all of them as "this filesystem cannot lock" and degraded, which returns
*true, you own it*. A `<session>.jsonl.lock` this user cannot write — left by
a `sudo aelix`, or a sessions directory shared across accounts — arrives as
`EACCES`; filelock opens the sidecar with `O_NOFOLLOW`, so a symlinked lock
path arrives as `ELOOP`. Those are fail-**open** against a live owner, which
is the one outcome this ADR exists to make impossible. Only `ENOSYS`,
`ENOLCK`, `EOPNOTSUPP`/`ENOTSUP` and `EINVAL` degrade; anything else is
reported as "we do not own it" carrying the real `OSError`, and the CLI names
the sidecar instead of guessing at another terminal.

**Ownership is keyed on the resolved path.** `--session` takes the user's
argv, `_header_to_metadata` carries that spelling into `meta.path`, and it
arrives at the lock unchanged — so a symlink and its target were getting one
sidecar each. Measured: `alias.jsonl -> a.jsonl` produced two sidecars and
**both** locks reported `held=True`, i.e. two terminals appending to one
`.jsonl` with the guard switched on. `SessionWriterLock` derives its sidecar
from `os.path.realpath`; `session_path` still reports the spelling the caller
asked for, because that is what the user is shown.

### Two things the design asserted that are false

Both were found by measurement, and both would have shipped as failing tests.

1. **"filelock unlinks the lock file on release on both platforms."** It does
   not, on POSIX, and it must not. `UnixFileLock`'s own docstring: *"We leave
   the lock file in place after release. Unlinking a locked file on Unix splits
   waiters across inodes and breaks mutual exclusion for processes that
   coordinate via the same path."* `_unix.py`'s `_release` is unlock + close,
   with no unlink; only `_windows.py` unlinks. **So a session directory keeps
   one empty `*.jsonl.lock` beside each session it has locked, on macOS and
   Linux.** That is the correct behaviour and we do not clean up after it.

   Worse for a test: upstream has **flip-flopped**. Measured, same probe,
   same machine — 3.12.0 through 3.20.0 leave the file, 3.25.2 and 3.29.0
   remove it, 3.32.3 onwards leave it again. No test in this repo may assert
   either way about the sidecar's existence after `release()`, and none does.

2. **"`timeout=0.25` is needed because a release between a contender's
   `open()` and its `flock()` leaves a dead inode."** On POSIX a normal release
   never unlinks, so that window does not arise from a release at all. The
   timeout is kept for the two reasons in §3 above, which are the ones that are
   true.

### The risks, sorted

- **Not a risk:** stale locks after a crash. Both backends are kernel-held, and
  a test kills the holder to prove it.
- **Real, and handled:** a filesystem without `flock` (NFS without lockd, some
  FUSE mounts). filelock's default there is a stale-able `O_CREAT|O_EXCL`
  marker. We detect it, drop it, and degrade to *no ownership check plus one
  stderr line saying so* — rather than lock a user out of their own sessions
  over a mount option, or hand them a file nobody can open until they delete it
  by hand.
- **Real, and accepted:** one more package on the supply-chain surface, and
  4.0.0 is three days old at the time of writing (the lock resolves 4.0.1, and
  the floor admits the 3.x line we also measured).
- **Real, and accepted:** an empty `*.jsonl.lock` per session in the session
  directory on POSIX, as above.

## ADR-0242 §4 is amended, not reversed

Its last bullet, "No rewrite-on-open", is justified by: *a reader — a second
terminal running `--continue`, or `/resume` — can see another writer's line
mid-write; rewriting would publish a file without that line.*

Half of that premise changes here. An opener that takes the write lock is now
the file's **only** writer, so a rewrite by *that* opener could not race
anyone. The other half does not change at all: a **read-only** opener holds no
lock and the owner keeps appending underneath it, and on a degraded filesystem
there is no ownership to rely on. The decision therefore stands and the reason
is rewritten in place; ADR-0242 stays `Accepted`. Turning rewrite-on-open *on*
is a destructive operation with its own ADR-0208 tension and is out of scope.

## What the review pass corrected

Six behaviours, all measured, all with a test. The filelock ones are in "The
floor is measured" above; these are the design-level ones.

1. **The lock is taken before the file is read, not after.** `repo.open`
   snapshots the file's leaf into process-local state, and `append_entry`
   reparents every later entry onto it — so a turn the other terminal wrote
   between our read and our lock is reparented away by our next append. That
   is #137 itself, reproduced by the guard meant to prevent it, and it is
   exactly the handoff the 0.25 s `DEFAULT_ACQUIRE_TIMEOUT` was written to
   absorb: the other terminal appends its final turn and *then* releases, on
   `/new` or `/quit`. `switch_session` now locks between the header sniff and
   `repo.open`, which costs nothing. The startup path cannot reorder as
   cheaply — three flags resolve the session before ownership is a question —
   so it re-reads under the lock instead, and only for the three flags that
   open an existing file. This is also why pi's branch (#8334) pairs the lock
   with a physical tail check; we get the same guarantee from the ordering.
2. **"Open read-only" is a state of the session, not a life sentence for the
   terminal.** The refusal names `/fork`, `/resume` and `/new`; all three ran,
   all three really did hand the process a new writable locked file, and the
   next prompt was refused anyway, about a file nobody else had. The flag was
   a `bool` parameter that nothing reassigned. It is derived from the live
   storage now, so the three doors the text advertises actually open.
3. **A failed swap no longer strands the target's lock.** All four replace
   APIs took the lock, ran several awaits that can raise, and adopted it only
   if every one returned. On a raise the lock was neither released nor stored:
   kernel-held for the life of the process, with no object left to let go, and
   the natural retry was told the file was "open in another terminal" — about
   this process's own orphan. The lock is installed inside
   `_finish_session_replacement`, in the same breath as the harness swap, and
   released by the caller when the swap dies before that point.
4. **A brand-new file that is somehow owned fails closed.** `None` from
   `_writer_lock_for_new_file` meant three things, one of which was "someone
   else has it" — and the caller could not tell, so it released the old lock,
   installed nothing, and kept writing. The microsecond case (another
   terminal's `--continue` taking the newest file by mtime) now raises, with
   this process still on the session it already owns.
5. **The fork arm reports the file it moved to.** A contended `/resume`
   answered with "fork and continue" lands on a different file, but the
   contended *original* was still handed to the `session_shutdown` payload, so
   an extension keyed on `target_session_file` recorded a switch to the file
   the other terminal still owns.
6. **The read-only arm no longer writes.** `_finish_session_replacement`
   records a carried-forward thinking level, which on a read-only storage
   raises at the one point where the old harness is disposed and the new one
   is live. Guarded the same way, and for the same reason, as the startup
   path's `_seed_startup_state(read_only=…)`.

## Not done here

- **The two POSIX-only `fcntl` sites of ADR-0091 / #46** —
  `aelix_ai/oauth/auth_storage.py` and `aelix_ai/settings/storage.py`, where
  `_fcntl` is `None` on Windows and every call site returns before touching it.
  They are a separate defect in a separate package, and migrating them would
  widen this lane to `aelix-ai`. **The repo therefore knowingly holds two
  locking idioms until they are**, which is recorded here rather than
  discovered later.
- **No tail-re-read before every append.** D1 makes it unnecessary, and pi
  issue #8334 names the cost it would not pay for: two writers on one file
  hand the provider a different prefix every turn, which breaks the prompt
  cache. (One re-read does happen — once, at startup, immediately after taking
  the lock; see "What the review pass corrected" §1. That is the ordering
  guarantee, not a per-append check.)
- **No "take over the other terminal's session" fourth option.**
- **No in-session "open read-only".** The kernel implements all three answers
  and they are tested, but the TUI's in-session prompt offers only fork and
  cancel: turning a live writable REPL into read-only chrome mid-session is a
  different change, and a button that half-works is worse than no button.
- **No lock on `JsonlSessionRepo.open()`**, per §6.
