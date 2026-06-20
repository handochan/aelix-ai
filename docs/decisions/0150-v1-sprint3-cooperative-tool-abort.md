# 0150. v1 Sprint 3 — cooperative tool abort (read/edit/write/grep/find/ls) + RPC bash abort

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08e`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

Sprint 3 of the TUI-first v1 track. The planned scope was three items:

1. **auth 완성** — per-model headers + `authHeader→Bearer`.
2. **tool cooperative abort** — `read/edit/write/grep/find/ls` could not be cancelled with Esc;
   only `bash` could.
3. **RPC bash abort** — the `abort_bash` RPC command was a stub.

A recon pass established ground truth:

- **(1) Auth was already fully implemented** (ADR-0140). `model_registry.py::resolve_request_auth`
  (≈L327-339) already merges per-model headers (`model.headers` < provider request-config headers <
  per-model request headers; each value resolved through env-var / `!command` indirection via
  `resolve_headers_or_throw`) **and** applies `authHeader → Authorization: Bearer <api_key>` (erroring
  when no key). **No code change required** — verified, not re-implemented. The Sprint-3 auth item is
  closed as already-satisfied.
- **(2) Root cause of the abort gap**: `grep._try_ripgrep` / `find._try_fd` ran **blocking**
  `subprocess.run(..., timeout=30)`, which freezes the asyncio event loop so `harness.abort()`'s
  `turn_task.cancel()` → `CancelledError` is never delivered until the subprocess finishes.
  `read/write/edit/ls` ran **synchronous** leaf file I/O for the same reason. `bash` already cancelled
  (it awaits via `asyncio.to_thread`), but it **orphaned** its child on cancel — the `signal` parameter
  it accepted was ignored.
- **(3) `abort_bash()` was a stub** (`harness/core.py`): it only set `_state.bash_aborted=True`, never
  polled; `rpc_mode._handle_bash` hard-coded `cancelled: False`. An ad-hoc RPC bash runs **outside** any
  turn task, so `turn_task.cancel()` can never reach it — a real abort needs a signal carrier.

## Decision

Two mechanisms, faithful to pi's "kill the process on abort" model.

### In-turn tool abort — via the existing `CancelledError` path (non-protected, `tools/*.py` only)

- **`tools/_subprocess.py`** (new): `run_cancellable(args, *, cwd, timeout)` — spawns via
  `asyncio.create_subprocess_exec(start_new_session=True)` and awaits `communicate()` under
  `wait_for(timeout)`. On `TimeoutError` → kill process group + reap + return `None` (parity with the
  old `except TimeoutExpired: return None`). On `CancelledError` → kill group + reap + **re-raise** (no
  orphan; Esc unwinds the turn). On `FileNotFoundError` → `None` (binary-absent fallback). Decode is
  UTF-8 `errors='replace'` — a documented intentional divergence from the old strict `text=True`
  decode, closer to pi's tolerant Node decode, pinned by a regression test.
- **`grep.py` / `find.py`**: `_try_ripgrep` / `_try_fd` are now `async`, calling `run_cancellable`
  instead of `subprocess.run`; the rg/fd argv, parsing (`_relativize_rg_line` / `_relativize`),
  match-count cap, limit/overflow detection, truncation, and all notice strings are **byte-for-byte
  unchanged** on the success path. The pure-Python offline fallbacks run via `asyncio.to_thread` so
  they no longer freeze the loop (documented: a threaded scan is not mid-scan interruptible — the rare
  offline / binary-absent path).
- **`read/write/edit/ls`**: leaf blocking I/O (`read_bytes` / `write_bytes` / `mkdir` / `rglob` /
  `iterdir` / `stat` / `os.access`) wrapped in `asyncio.to_thread`. Pure responsiveness change — no
  behavior/encoding/diff/format change; `_file_mutation_queue` serialization preserved.

### bash + RPC bash abort — via a minimal `AbortSignal`

- **`tools/_abort.py`** (new): `AbortSignal` — an `asyncio.Event`-backed one-shot signal
  (`aborted` / `abort()` / `await wait()`), the canonical carrier for **RPC-boundary** abort intent
  (Aelix `asyncio.Event` convention per `ext_ui.py`).
- **`tools/bash.py`** `_LocalBashOperations.exec`: now honors the `signal` it already accepted — a
  watcher task kills the process group when the signal fires; **and** `except CancelledError` kills the
  group before re-raising (fixes the Esc-path orphan). A kill triggered by abort reports
  `exit_code=None` (parity with the timeout-kill path).
- **`harness/core.py`** (protected — surgical): a `_active_bash_signals` registry plus
  `register_bash_signal` / `unregister_bash_signal`; `abort_bash()` keeps `_state.bash_aborted=True`
  and now fires every registered signal; `abort()` (Esc) also fires them so a concurrent RPC bash is
  killed too. No import cycle (registry typed `Any`).
- **`rpc/rpc_mode.py`** `_handle_bash`: registers an `AbortSignal` before `ops.exec`, threads it in,
  unregisters in a `finally`, and reports the real `cancelled = sig.aborted`.

### Scope discipline

A mid-implementation `AbortScope` per-turn context manager (an unrequested, unwired addition) was
**removed** as slop — `AbortSignal` is the only abort primitive shipped.

## Consequences

- Esc now cancels `grep`/`find` instantly (kills the rg/fd group) and unwinds `read/write/edit/ls` at
  the next await; `bash` no longer orphans its child; RPC `abort_bash` actually kills the in-flight
  bash and reports `cancelled: true`.
- Process-group kills (`os.killpg` + `start_new_session=True`) reap grandchildren, guarded against
  `ProcessLookupError` / `PermissionError` and reaped with a bounded `wait_for(proc.wait())`.
- **Auth Sprint-3 item closed as already-satisfied** (ADR-0140) — no code change.
- Process: recon → 3 disjoint-file implementation lanes (workflow) → 4-lens adversarial review
  (abort-semantics / pi-parity-regression / protected-core safety / test-adequacy) → fix. Review =
  APPROVE-WITH-NITS, no CRITICAL/HIGH; 9 MEDIUM findings all applied (deterministic reap-after-kill,
  timeout-guarded cancel tests, real process-group-death proofs polling `/proc`, decode-divergence
  test, RPC exception-path unregister test).
- Gate: **3553 passed, 1 skipped, 0 failed** (87s; baseline 3469 → +84 abort tests). The 9 warnings
  are the documented-cosmetic asyncio transport-GC `PytestUnraisableExceptionWarning` from the
  mid-flight subprocess-cancellation tests. ruff clean on every sprint file. Pre-existing ruff nits in
  unrelated files (`extensions/command_context.py`, `test_adr0135_*`) were left untouched.

## Files

- New: `tools/_abort.py`, `tools/_subprocess.py`
- Changed: `tools/{bash,grep,find,read,write,edit,ls}.py`, `harness/core.py` (protected),
  `rpc/rpc_mode.py`
- Tests: new `test_abort_signal.py`, `test_subprocess_helper.py`, `test_tools_responsiveness.py`,
  `test_abort_lane_ab.py`, `rpc/test_rpc_mode_bash_abort.py`; updated `test_grep_tool.py` /
  `test_find_tool.py`
