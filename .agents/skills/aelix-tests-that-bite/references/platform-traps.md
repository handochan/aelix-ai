# Platform and Python-version traps

CI runs every test on ubuntu-latest and windows-latest, each with Python 3.11,
3.12, 3.13 and 3.14 (`tests/test_ci_python_matrix.py` holds the matrix). Every trap
below was green on macOS and in Linux docker, then failed in CI, and each cost at
least one CI round. Each entry gives the symptom, the rule, and the issue where it
happened.

## Windows

### The clock ties at about 15.6 ms (Python 3.12 and older), #260 and #313

`time.monotonic()` on Windows with CPython 3.11 or 3.12 is GetTickCount64: whole
milliseconds, stepping about every 15.6 ms. Two readings in one tick are equal.
- #260: a drain accepted a reader's "look" as after the exit with
  `looked_at >= pin_after`. On Windows a look taken before the pin, in the same tick,
  passed, and the proof read "caught up" while bytes were still in the pipe.
- #313: a 0.2 s timer read 0.187 s.
- A test precondition went red the same way: `time.sleep(0.3)` followed by "at least
  0.3 s passed" read 0.296 s across two coarse readings.

Rule: when code orders two instants, ask what a tie means, and test the tie.
Compare the code's own clock readings, not wall-clock slack. Reproduce locally with
the coarse-clock plugin (see the skill). `sleep(0.01)` can be a single loop turn on
that clock, so repeated cancels can merge. Space them by more than a tick, and bound
them by a condition, not a count.

### `text=True` decodes cp1252, #333

`subprocess.run(..., text=True)` with no `encoding` decodes with the locale codec,
cp1252 on the runner. A byte cp1252 lacks (`0x81`, inside `₁`) killed the reader
thread, so `stdout` was None and `ast.parse(None)` raised. Rule: every subprocess
that reads text passes `encoding="utf-8", errors="replace"`.

### Paths inside `str(list)` double their backslashes, #330 and #131

The repr of an argv list doubles every backslash, so a single-backslash Windows path
is never a substring of `str(args)`. `json.dumps` doubles them too. Rule: identify a
process by its pid, never by path text in its argv, and assert on parsed JSON, never
on dumped text.

### File names and path semantics, #131

Five rows failed on Windows only:
- a fixture directory with `:` in its name (illegal, WinError 123);
- `trusted ` against `trusted` (a trailing space is stripped, WinError 183);
- `link/../x` expected to follow the symlink (Windows collapses `..` lexically, and so
  does `Path.resolve()`);
- `str(path) in json.dumps(...)`;
- the uv panic below.

Rule: a fixture that needs `:*?<>|"`, a trailing dot or space, or `..` after a
symlink is POSIX-only. Keep the classification or argv assertion on every platform,
and skip only the on-disk part, with a reason.

### uv before 0.11.27 panicked on `git+file:///C:/...`, #131 and #393

astral-sh/uv#19887. CI then pinned uv 0.11.14, so the Windows legs could not install
a `git+file` source at all. #393 moved the pin to 0.12.23 (`ci.yml`, the "Install uv"
step), so CI no longer hits it. A user on an older uv still can.

### The type gate is per platform, #392

Both Windows legs failed `check_types.py` before any test ran. pyright on win32 does
not know `os.O_NOFOLLOW`, `os.O_DIRECTORY`, `os.fchmod` or `os.getuid`. A branch
guarded only by `hasattr(os, ...)` is still analysed. `sys.platform != "win32"` is
narrowed. Check with `pyright --pythonplatform Windows <files>`; preflight's
`pyright-windows` gate does this.

### Four Windows-only failures in one PR, #434

The local full suite was green, and all four Windows legs failed on exactly these:
- `str(path.relative_to(root)).split("/src/")` raised IndexError (use `.as_posix()`);
- a store test asserted mode `0o600` (Windows reports `0o666`);
- a cross-process `fcntl` lock test had no `skipif(sys.platform == "win32")`.

### `Path("/abs")` is not absolute, #511 (PR #639)

On Windows `Path("/abs/x").is_absolute()` is False, because it has no drive. Code
that joins a non-absolute path to the cwd turns it into `C:\abs\x`. Three verifiers
had run `pyright --pythonplatform Windows`, which never sees path semantics. Rule:
use a `tmp_path` directory as the "absolute" fixture.

### Environment values over 32,767 characters raise, #656

`monkeypatch.setenv` of a longer value raises `ValueError` on Windows. Keep
environment payloads short; force the failure you want to test instead.

### No `$SHELL`, so the bash tool picks PowerShell, #688

windows-latest has no `$SHELL`, so `tools.bash._resolve_shell` chose PowerShell,
whose dialect makes the permission gate ask. Gate-level tests that asserted `auto`
ALLOW for `git status` failed 9 tests on 4 legs. Rule: pin `_resolve_shell` to
`ShellConfig("/bin/bash", "-c")` in such tests, as
`tests/builtin/test_permission_shell_competence.py` does.
`tests/builtin/test_permission.py` documents the trap (#216).

### Getting a number back from the Windows leg

The leg's `-q` log has no per-test names or durations. A `warnings.warn(f"...
{elapsed:.3f}s")` in the test shows up in the warnings summary. Exit code 1 is
ambiguous there: an uncaught exception at interpreter exit and
`TerminateJobObject(job, 1)` both read as 1. Put the child's stderr tail in every
real-child assertion message.

### Copy the sibling's mark

A `skipif(sys.platform == "win32")` on a sibling test means a new test of the same
shape fails on Windows too. #721: `tests/agents_ext/test_print_channel_spawn.py` runs
its post-EOF linger cases (a stub child closes fd 1 and 2, then sleeps) on POSIX
only. A new case of that shape in `tests/agents_ext/test_latest_run_611.py` ran
without the mark and missed its 30 s bound on every windows-latest leg, 3.11 to
3.14, until it copied the mark. Copy the mark, or measure the shape on Windows
first.

## Python versions

### 3.11's `asyncio.wait_for` adds loop turns, #334

On 3.11 `wait_for` wraps the awaitable in an extra task. On 3.12 it is built on
`asyncio.timeout`. A test helper started a prompt, awaited
`wait_for(parked.wait(), ...)`, and only then filled `holder["waiter"]`. On 3.11 the
code under test ran through its turn during those awaits and read the empty holder:
`KeyError: 'waiter'`, on ubuntu py3.11 only. Rule: assign everything a callback reads
before the first await, and run new ordering tests once on 3.11.

### 3.14's `asyncio.shield` logs a cancelled gather, #604 (PR #615)

When a shield's outer side ends first, 3.14 attaches a logger to the inner future.
It skips futures that are cancelled. A `gather` whose child is cancelled ends with
`set_exception(CancelledError())` instead, so it is logged: "CancelledError
exception in shielded future". In the TUI, prompt_toolkit then printed "Press ENTER
to continue..." and swallowed the user's next line. Rule: to wait without
cancelling, use `asyncio.wait({fut}, timeout=...)`, not `wait_for(shield(fut))`,
when `fut` is a gather or any future that can end in an exception state. Shielding a
Task is fine. Regression pattern: install a recording
`loop.set_exception_handler`, cancel, sleep briefly, assert nothing was reported
(`test_a_cancelled_run_reports_nothing_to_the_loops_exception_handler` in
`tests/agents_ext/test_print_channel_spawn.py`). A green Windows py3.14 leg proves
nothing about this one.

### 3.14 bounds recursion by stack size, #646 (PR #656)

`json.loads("[" * 100_000)` raised `RecursionError` on macOS for 3.11 to 3.14, but on
ubuntu py3.14 it read to the end and raised `JSONDecodeError`, because the stack
there is larger. Rule: to test a `RecursionError` branch, force it (monkeypatch the
module's loader to raise), and keep a separate real-input test that asserts only
that nothing escapes.

## Linux only

### Older `@linux_only` tests in a file you touched, #721 (PR #728)

macOS skips them, so a local run never executes them. Run every touched test file
whole in a Linux container (preflight `--docker`), not only your new tests.

### Non-UTF-8 file names

macOS APFS refuses a file name that is not UTF-8 (`Errno 92`), so a path whose `str`
holds surrogate escapes (`\udc80` to `\udcff`) cannot be made locally. On Linux it
can (#627). Any check that judges a path by text rules needs such a case, in a
container. Judge paths by `os.fsencode` and NUL, not by text rules.
