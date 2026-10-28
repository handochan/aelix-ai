# #604 리뷰 1회차 — 독립 검증 보고 원문 (cb3eb20c, fresh Claude)

I found no blockers and no majors in #604 phase 0 at `cb3eb20c`. The full suite is green, there are 5 minor defects and a few nits, and 9 of my 53 mutants survived. Two of the survivors matter. The probes, mutation harness and logs are under `/Users/handochan/dev/aelix-ai-worktrees/claude-604-verify/.omc/probes/604-verify/` (gitignored). The tree is clean, still at `cb3eb20c`.

**(a) Defects, most severe first**

1. **Minor: a child that fails with only stderr gets a misleading reason and its stderr labelled as its own answer** (D10/D11). Code: `envelope.py:609` (stderr is skipped as the error once it became the summary) and `tool.py:929` (`summary_is_the_error` only recognises the final-error summary, not the stderr fallback).
   - Input: the most common start-up failure: no stdout, exit 1, stderr "No API key found for anthropic…".
   - Observed: `The delegated agent did not finish: exited with code 1` / `Partial result: No API key found…` / `Error: exited with code 1`.
   - Expected, by D11's own refinement: `(it wrote no text before it stopped…)` as the partial result, with the diagnosis as the error and reason.
   - Related: with a partial answer plus a traceback on stderr, the reason is `Traceback (most recent call last):`, because the reason is the first line (`envelope.py:642`).
   - Run with `uv run --no-sync python /tmp/604-verify/probe_no_key.py` and `/tmp/604-verify/probe_reason_traceback.py`.
2. **Minor: ADR-0276 `Status: Proposed`** (`0276-delegation-has-no-time-limit.md:3`, and the index bullet at `docs/decisions/README.md:40`). `docs/00-conventions.md` only allows Draft, Accepted, Deprecated and Superseded, and 0276 is the only ADR using "Proposed".
3. **Minor: the index rows for 0196 (`README.md:460`) and 0197 (`README.md:459`) do not mention the 2026-10-28 ADR-0276 amendment.** The rows for 0199, 0201 and 0243 do, and all five ADR files carry the dated notes.
4. **Minor: the RPC channel has no post-EOF bound** (D4 asymmetry), `rpc_channel.py:714` and `rpc_client.py` `_await_terminator`. The unbounded turn ends only on `agent_end`, the child's exit or a cancel. A child that closes stdout and then wedges holds the delegation until a human stops it; the print channel bounds the same case at 30 s. The RPC channel has no production caller.
5. **Minor (TUI/UX): what the user sees of the Esc report.**
   - The report is never drawn live: after Esc the TUI shows "Operation aborted", and nothing during the next prompt.
   - On reopening or `/resume` it is replayed as the user's own message: `» [aelix system report — not user input] …`, the whole report block between "delegate it" and the next prompt.
   - It is also lost on `/reload`, which rebuilds the harness and does not carry the queue. ADR-0276:460 names only `--continue`.
   - Shown by `probe_d14_replay.py`, which uses a real harness, a real JSONL session, `build_display_messages` and `EventRenderer.replay`.
6. **Nits.**
   - The cancelled-turn reason ends in "(Esc)" (`envelope.py:380`) even when RPC `abort`, `harness.abort()` or the D16 SIGTERM path caused it.
   - ADR D2 says non-integers get the "not an integer" suffix, but `timeout_ms: null` gets the plain warning (`profile.py:611`).
   - "files changed through bash are not listed" (`tool.py:851`) understates the gap: files written by MCP or extension tools are not listed either.
   - The ADR's evidence logs (`.omc/probes/604/*`) are gitignored, so they are not in the commit.
   - `/agents run` interrupted before its child spawned still prints "the agent was ended before it finished" (`commands.py:1321`).
7. **Residual, not a phase-0 defect.** A third-party extension slash command that awaits `subagents.spawn` in the TUI cannot be interrupted, because D15 only wires `/agents run`, and that wait is now unbounded. No bundled caller does this.
8. **Information for the ADR.** aelix-server builds `AgentHarness(model, session, cwd)` with no extensions and accepts one connection at a time (`rpc_ws.py:68-95`), so it never reaches delegation. The "per-loop batch semaphore" risk at ADR-0276:453 does not apply to it.

**(b) Behaviours no test pins** (grep confirmed no other test file reaches this code)
- **M51:** `_report_cancelled`'s `except Exception` (`extension.py:1213`) can become `except ValueError` and all 45 tests in the 604 file still pass. This swallow is what keeps a session switch during a delegation from turning the cancel into a tool error. During teardown the runtime is invalidated before `dispose()` aborts the turn, so `send_message` raises `ExtensionError`. `probe_stale_runtime.py` shows the current code handles it: "turn ended without an exception, queued reports: 0".
- **M49:** removing `_run_cancellable`'s `current.cancelling()` check (`shell.py:2214`) survives the D15 tests and `test_agents_run_command`.
- **M15:** dropping the `child.proc is None` guard in `_leave_cancelled_envelope` survives. That is expected, since `render_cancel_report` also filters on `started`.
- **Limit values are pinned only by the constants themselves.** Raising the 4 KiB error note (M39), the 300-character reason (M40), the 64 paths (M41), the 300 B member heading (M42) or the 2 KiB file lists (M32) tenfold passes every test. `POST_EOF_EXIT_BOUND_SECONDS` and `RPC_COMMAND_TIMEOUT_MS` are pinned.
- **M44:** the 1/8 share for a batch member's last tool calls can become 1/2 and survive. In practice this changes nothing: five calls never exceed 1 KiB.
- **No tests at all for:** a session switch during a delegation; the report's visibility to the user live or on reopen; `/agents run` with a consent dialog. For the last one, code reading says the dialog's own Esc/Ctrl+C bindings (`context.py:1159-1160`) take precedence and `show_modal` unmounts it in its `finally`.

**(c) Gates**
- **Changed tests, Python 3.12:** `uv run --no-sync pytest -q -p no:cacheprovider <33 changed test files>` → `1555 passed, 11 skipped, 2 warnings in 75.44s`.
- **Python 3.11:** `UV_PROJECT_ENVIRONMENT=/tmp/604-verify/venv311 uv sync --python 3.11 --frozen` exited 0 (3.11.15). The six listed files under `/tmp/604-verify/venv311/bin/python -m pytest -q -p no:cacheprovider` → `245 passed, 11 skipped, 2 warnings in 43.19s`.
- **Pyright for Windows:** `uv run --no-sync pyright --pythonplatform Windows <24 changed src> <2 new tests>` → `0 errors, 0 warnings, 0 informations`. The D16 tests carry the POSIX-signal skip; the D15 tests and the real-child test use Python stubs, and `_pid_is_live` has a separate win32 branch.
- **Pyright, default platform:** on the 24 changed sources → `0 errors, 0 warnings, 0 informations`.
- **Ruff:** `ruff check` on the 57 changed .py files → `All checks passed!`. `ruff format --check` lists 46 files, but every one was already unformatted at base and neither new test file is.
- **Docs:** `pytest tests/docs tests/test_docs_bundle_sync.py tests/test_citation_drift.py` → `74 passed in 4.51s`. `scripts/check_citations.py --check` → `citations OK — 914 gated, none drifted.` `cmp` shows `agent-profiles.md` and `project-trust.md` byte-identical to their bundled copies.
- **Mutations:** 53 mutants, 44 killed, 9 surviving. Each ran a targeted `pytest -x` with a fresh `PYTHONPYCACHEPREFIX` and was restored with `git checkout --`. The killed ones include the deadline, the post-EOF bound (it hung and was caught), the lingering verdict, the RPC unbounded flag and command bound, the stale final error, the summary order, the exit-status rung, the D18 gates, `started`, the cancel report, D15 rewiring, the steer route, both D16 branches, D2 and the path rules.
- **Full suite, last:** `uv run --no-sync pytest -q -p no:cacheprovider` from the worktree root → `18772 passed, 49 skipped, 114 warnings in 895.35s (0:14:55)`, exit 0, nothing failed.
- **Not run:** any live model check (L1, L3, L7), a visual TUI check with `uv run aelix`, an actual Windows run (only pyright for the Windows platform), and Codex cross-review.

**(d) Checked and sound**
- **D1–D9, D12, D13, D17, D18** conform to the design or to the refinements documented in ADR-0276.
- **D4 bounds on every branch:**
  - Main wait: no timeout.
  - Post-EOF wait: 30 s, then a bounded reap. A stop during the wait still wins as `aborted`.
  - Child exited first: bounded drain.
  - Unexpected exception: wait bounded by the grace.
  - RPC: command answers 180 s, turn unbounded; RPC `_shutdown` is bounded.
- **D14 delivery:**
  - Exactly once: one `except` per call, queued synchronously, drained by the next `prompt()`.
  - Only for a started child: two independent guards.
  - Never while a newer prompt runs: `prompt()` awaits the cancelled turn task, and RPC refuses a concurrent prompt as busy.
  - Never into another session:
    - The TUI only switches sessions when idle.
    - Every switch builds a new harness, so a queued report is discarded with the old queue.
    - RPC `new_session` invalidates the old runtime before the abort, so the report is dropped silently.
  - A batch report waits for every member: a cancelled member leaves its envelope before its first `await`, and `gather` wakes the extension only after that.
- **D15:** Esc, Ctrl+C and SIGTERM reach `/agents run`. A line typed meanwhile is queued as the next input. `running` and `on_interrupt` are restored. `run_cancellable` survives the context rebind on a session swap.
- **D16:** no signal handler is installed on win32. A missing SIGHUP and `add_signal_handler` errors are tolerated.
- **D17:** the 429 / SIGKILL / abort / lingering / third-party-timeout cases resolve correctly.
- **Hang sweep:** every entry point has a reachable stop or a bound.
  - Agent tool in a TUI turn, plus batch and chain members: Esc, Ctrl+C, SIGTERM/SIGHUP. Members parked on the semaphore can be cancelled.
  - `/agents run`: Esc, Ctrl+C, SIGTERM/SIGHUP.
  - `-p` / `--mode json`: SIGINT cancels the main task. SIGTERM/SIGHUP (SIGBREAK on Windows) dispose and abort.
  - RPC `agents/run`: `commands/cancel`, and `begin_shutdown` cancels it on disconnect.
  - RPC `prompt` that delegates: `abort`, or shutdown disposing the session.
  - RPC child channel: cancel, or a stop killing the child. The one gap is defect 4.
