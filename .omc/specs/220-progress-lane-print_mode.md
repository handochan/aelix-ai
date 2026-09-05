# #220 — lane report: `print_mode` (spec §A.3, §B.3, §D.3)

Worktree `/tmp/wt-220`, branch `fix/220-agents-process-tree`, base `main` `6586f8a`.
Host: darwin 25.4.0, CPython 3.12.13, `/tmp/wt-220/.venv`.

## Files changed

- `packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py` (edited)
- `tests/cli/test_print_mode_signal_handlers.py` (NEW)

Nothing else was touched. Two out-of-lane items are filed at the bottom.

## What went in

### `print_mode.py`

1. **Module docstring.** Step 1 is now "SIGTERM / SIGHUP on POSIX, SIGBREAK on Windows — #220", and a
   new paragraph records the Pi divergence the spec's §Pi asks for: Pi registers `SIGTERM` on every
   platform and gates only `SIGHUP` off win32 (`print-mode.ts:50-66`, `origin/main` 47236c8), Pi has no
   `SIGBREAK` anywhere, and aelix follows neither — `os.kill(pid, SIGTERM)` on win32 is
   `TerminateProcess`, so a handler there would be dead code, and `CTRL_BREAK_EVENT`/`SIGBREAK` is the
   real equivalent. Legal under ADR-0235, recorded as the ADR asks.
2. **§A.3(a) — the handler block is no longer switched off wholesale on win32.** `signals_installed`,
   `break_previous`, `break_installed` and `signal_exit` are bound before the platform branch;
   `_handle_signal` moved out of the POSIX branch; the `else:` arm installs
   `signal.signal(signal.SIGBREAK, lambda sig, _frame: loop.call_soon_threadsafe(_handle_signal, sig))`
   and records `break_previous`. The `finally` gained the matching `else:` arm with the
   `break_installed and break_previous is not None` guard (not `callable(previous)` — `SIG_DFL` is an
   `int`, #202), suppressing `ValueError, RuntimeError, TypeError`.
3. **§A.3(b) — no more `sys.exit` from inside a task.** `_signal_cleanup_and_exit(runtime_host, sig,
   holder)` records `128 + sig`; `run_print_mode` reads `signal_exit["code"]` immediately before
   `return exit_code` and overrides step 7's `exit_code = 1`.

### `tests/cli/test_print_mode_signal_handlers.py` (5 cases)

The gate is built in the test, per §D.3/T8: `run_print_mode` has no stdin lever, so a mock `stream_fn`
parks on an `asyncio.Event` the file owns (`_Gate`, with a separate `started` event so a disposition is
never read before the install). `tests/rpc/test_rpc_mode_signal_handlers.py`'s stdin-EOF lever is NOT
copied.

| case | subject |
| --- | --- |
| `test_the_win32_arm_installs_a_sigbreak_handler_and_restores_it` | injected win32 (3 injections: `sys.platform`, lent `signal.SIGBREAK`, `remove_signal_handler` raising `NotImplementedError` for the stand-in only); sentinel previous; install read while running, restore read after |
| `test_a_default_sigbreak_disposition_survives_the_win32_arm` | the `SIG_DFL`-previous case (§D.3, #202's `callable(previous)` defect) |
| `test_the_posix_sigterm_disposition_survives_the_call` | POSIX `SIGTERM` disposition across the call |
| `test_a_delivered_signal_makes_run_print_mode_return_128_plus_sig` | §A.3(b) in-process, REAL delivery: `os.kill(os.getpid(), SIGTERM)` on POSIX, the installed `SIGBREAK` handler on the windows leg — 143 / 149, computed from the number delivered |
| `test_the_injected_win32_sigbreak_path_reports_128_plus_sig` | the same exit-code chain forced through the win32 arm on every leg |

An autouse `_restore_dispositions` fixture saves and restores `SIGTERM`, `SIGHUP` and the lent
`SIGUSR1` unconditionally, so a case that fails mid-install cannot poison the rest of the session
(`signal.signal` is process-global).

No `skipif` anywhere. The only platform branch is `_arm()`/`_deliver()`, and it is there because OS
delivery is the one thing injection cannot fake: `os.kill(pid, SIGTERM)` on Windows is
`TerminateProcess` of the test runner. Both legs run the case, with the lever the platform allows.

## Deviations from the spec

1. **`_signal_cleanup_and_exit` records the code BEFORE `await _safe_dispose`, not after.** §B.3's
   snippet is `await _safe_dispose(runtime_host)` then `holder["code"] = 128 + sig`. That ordering does
   not work and I measured it before changing it. `dispose()` is what ABORTS the in-flight turn, so the
   moment it starts, `run_print_mode` unparks from `harness.prompt(...)`, runs step 7 (setting
   `exit_code = 1` for the `aborted` stop_reason), runs its `finally` and returns — all while
   `_signal_cleanup_and_exit` is still inside `dispose`. Measured with the spec's order:

   ```
   $ .venv/bin/python scratchpad/probe_signal.py     # in-process SIGTERM to a gated run_print_mode
   Request aborted
   disposition before kill: <function _sighandler_noop at 0x106cf0860>
   rc = 1
   ```

   With the assignment moved before the `await`, the same probe gives `rc = 143`. The assignment is now
   deterministic: `ensure_future` runs the body to its first suspension point in one loop step, and the
   only thing that can unpark `run_print_mode` is the dispose that follows it. The reason is written
   into the docstring, with the measurement.

2. **The `finally`'s win32 restore is an `else:` on the existing `if sys.platform != "win32":`, not a
   free-standing `if sys.platform == "win32":`.** Same narrowing for pyright on both `--pythonplatform`
   values (verified: 0 errors on Windows AND Linux), and it keeps the "these two arms are the same
   fence" fact — which is the whole of W8's argument — visible in the code rather than only in a
   comment.

3. **The exit-code override reads through a local (`signal_code = signal_exit["code"]`), not the
   subscript twice.** Cosmetic; pyright narrows either way, this reads better.

4. **A separate, tighter `_INSTALL_DEADLINE = 2.0`** for "has the handler appeared yet" polls, next to
   the 10 s `_DEADLINE` for task waits. Measured reason: with 10 s everywhere, the deleted-install-arm
   mutation took **45.45 s** to report; at 2 s it takes **17.18 s**. The install runs in
   `run_print_mode`'s first loop step, before its first `await`, so 2 s is already enormous.

## Mutation checks (every one actually run)

Backup at `scratchpad/print_mode.orig.py`; the file was restored and re-verified green after each.

| # | mutation | result |
| --- | --- | --- |
| 1 | `main`'s body restored: `await _safe_dispose(...)` then `sys.exit(128 + sig)` | RED — `test_a_delivered_signal_…` and `test_the_injected_win32_…` fail; failure text is `E SystemExit: 158` at `print_mode.py:389`, i.e. the `SystemExit` escaping the loop, which is the exact #220 shape |
| 2 | `if signal_code is not None:` override disabled | RED — the same 2 cases (`1 != 143`) |
| 3 | guard weakened to `callable(break_previous)` | RED — `test_a_default_sigbreak_disposition_survives_the_win32_arm` |
| 4 | the whole win32 `signal.signal(SIGBREAK, break_previous)` restore deleted | RED — both win32 disposition cases |
| 5 | the POSIX `loop.remove_signal_handler(sig)` loop deleted | RED — `test_the_posix_sigterm_disposition_survives_the_call` |
| 6 | the entire win32 install arm deleted (back to `main`'s wholesale switch-off) | RED — all 3 win32 cases |

Every product line this lane adds has a case that goes red without it.

## Commands run, with output tails

```
$ .venv/bin/ruff check packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py \
    tests/cli/test_print_mode_signal_handlers.py
All checks passed!

$ .venv/bin/ruff format --check tests/cli/test_print_mode_signal_handlers.py
1 file already formatted
```

(`ruff format` was run on the NEW file only; `print_mode.py` was never reformatted — ADR-0152.)

```
$ .venv/bin/pyright --pythonplatform Windows packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py
0 errors, 0 warnings, 0 informations

$ .venv/bin/pyright --pythonplatform Linux packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py
0 errors, 0 warnings, 0 informations

$ .venv/bin/python scripts/check_types.py
OK  gate: 277 files analysed (floor 220)
OK  gate: 0 errors across 277 files
OK  spike: all 3 inverse assertions still error (narrowing alive)
PASS: type gate clean, narrowing spike still inverted
```

(An earlier `check_types.py` run in this lane failed with one error in
`packages/aelix-coding-agent/src/aelix_agents/reaper.py:250` — `Cannot access attribute "closed" for
class "ProcessTree"`, i.e. another lane mid-flight between the reaper and `_process_tree`. It was gone
by the final run.)

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/cli/test_print_mode.py \
    tests/cli/test_print_mode_json_contract.py tests/cli/test_pipe_robustness.py \
    tests/cli/test_print_mode_signal_handlers.py
43 passed in 9.68s          # ×3 consecutive runs: 9.66s / 9.63s / 9.67s — no flakes

$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/cli/test_print_mode_signal_handlers.py
5 passed in 0.08s

# ordering check, both directions (both files patch process-global dispositions)
$ … -q tests/rpc/test_rpc_mode_signal_handlers.py tests/cli/test_print_mode_signal_handlers.py
8 passed in 0.09s
$ … -q tests/cli/test_print_mode_signal_handlers.py tests/rpc/test_rpc_mode_signal_handlers.py
8 passed in 0.09s

$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/cli tests/agents_ext/test_envelope.py tests/pi_parity
1 failed, 2089 passed in 26.14s
    FAILED tests/cli/test_stdio_encoding_win32.py::test_server_main_sync_hardens_before_boot
    E   ModuleNotFoundError: No module named 'aelix_server'
```

That one failure is **environmental and pre-existing**, not this lane's:
`importlib.util.find_spec('aelix_server')` → `None` in this worktree's venv, and the test hard-imports
it on purpose (its own comment says `importorskip` would let the guard vanish silently, #203). Nothing
in this lane touches packaging. The orchestrator should decide whether `uv sync` in `/tmp/wt-220` needs
`aelix-server` before the full-suite step.

## Live verification — spec §H.6(B), rule 10 (this is MEASURED, not reasoned)

Script: `scratchpad/live_print_sigterm.py` (rewritten; the #202-era one no longer exists on disk).
Spawns the REAL `aelix` from `/tmp/wt-220/.venv` against `openrouter anthropic/claude-haiku-4.5` with
`--permission-mode yolo` and the task "use the bash tool to run `sleep 120`", waits 15 s, sends
**SIGTERM to the root only** (`os.kill` — the delegation reaper's leg 1), then records exit, elapsed and
survivors. Credentials were exported from `/Users/handochan/dev/aelix-ai/.env` into the environment; no
file was created in the worktree and nothing under `/Users/handochan/dev/aelix-ai` was modified.

```
spawned pid=22303 mode=text
after 15.0s alive=True
sleepers before SIGTERM: ['22303 … aelix … -p use the bash tool to run `sleep 120`', '22388 sleep 120']
SIGTERM -> exit in 0.126s, returncode=143
sleepers 1s after child exit: []
stdout bytes=0
--- stderr ---
Request aborted
--- end stderr ---
'Traceback' in stderr: False
'SystemExit' in stderr: False
'Fatal Python error' in stderr: False
'_enter_buffered_busy' in stderr: False
'0xC0000005' in stderr: False
'Task exception was never retrieved' in stderr: False
```

```
spawned pid=23233 mode=json
SIGTERM -> exit in 0.137s, returncode=143
sleepers 1s after child exit: []
stdout bytes=10391
--- stderr ---            (empty)
```

Compare `220-progress-2026-09-05.md` §1 on `main`: `SIGTERM -> exit in 0.172s, returncode=1` with a
`SystemExit(143)` traceback, a `RuntimeError: Event loop stopped before Future completed` on top of it,
and a "Task exception was never retrieved". **§A.3(b)'s 143 and the clean stderr tail are now [M] on
POSIX**, in both the mode a human uses and the mode the print channel actually spawns.

`ps` after both runs: no strays (the two live `aelix` processes on the box are the owner's own TUI
sessions from `~/.local/bin`, not these runs').

## What I could NOT verify

- **149 on Windows.** Unmeasurable here; the whole win32 arm is reached only by injection. The windows
  CI leg is the measurement, per §H.7. Both win32 cases compute `128 + <the number delivered>` rather
  than hard-coding 149 precisely so the assertion is the same code on both legs.
- **Real `CTRL_BREAK_EVENT` delivery.** Deliberately not attempted — the rpc test's "NO SELF-SIGNAL"
  reasoning applies unchanged (on the windows leg raising it would hit the whole console group). The
  win32 case invokes the handler `signal.signal` holds, which is the object CPython itself would call;
  everything downstream of that is the product's.
- **That `run_print_mode`'s 143 survives `_async_main` → `main_sync` on Windows.** Measured on POSIX
  above; on Windows it is `[U]`.
- **The `remove_signal_handler`-raises injection guards nothing today**, and the test says so rather
  than implying coverage: `print_mode`'s removal loop and its SIGBREAK restore are the two arms of the
  SAME `if sys.platform != "win32":`, so nothing can precede the restore. It is kept because that
  structural difference from `rpc_mode` is the assumption the restore rests on.

## Out of lane — for whoever owns these files

1. **`packages/aelix-coding-agent/src/aelix_agents/envelope.py:42-47` and
   `tests/agents_ext/test_envelope.py:23-33` are now describing history, not behaviour.** Both say
   "``modes/print_mode.py``'s ``_signal_cleanup_and_exit`` calls ``sys.exit(128 + sig)`` from inside a
   coroutine, so asyncio prints \[a `SystemExit(143)` traceback\] on EVERY successful kill". After
   §A.3(b) it prints nothing of the sort — measured above, stderr is `Request aborted` and nothing else.
   `_STDERR_NOISE_RE` should STAY (children built before this change, and any other task-borne
   exception, still produce the shape), but the comments need a "was true until #220" and
   `_SIGTERM_NOISE` should say it is a synthetic historical sample, not what today's child emits.
   I did not touch either file.
2. **`tests/agents_ext/test_print_channel_spawn.py:1124` and `:1297`** cite `_signal_cleanup_and_exit`
   in comments about grandchild reaping. Those claims are still true (measured: the `sleep 120` was gone
   within 1 s), so no edit is needed — flagged only so the §D.4 lane knows the citations were checked.
