# 0130. Sprint 6h₂₂ — TUI auto-retry UI countdown subscriber

Status: Accepted (6h₂₂ shipped — v2 polish closing the Sprint 6h₂₀ deferral)
Date: 2026-06-03
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Sprint 6h₂₀ (ADR-0128) ported pi's `_handleRetryableError` into protected core
and emitted `AutoRetryStartEvent` / `AutoRetryEndEvent` for the TUI to render a
countdown. The events fired correctly but had **no consumer** — retries
happened silently. ADR-0128 explicitly deferred the UI subscriber to v2.

This sprint closes that deferral. Pi reference: `interactive-mode.ts:2919-2948`
— pi's `CountdownTimer + Loader` shows
`"Retrying (N/M) in Xs… (Esc to cancel)"` while the harness sleeps in
`_handle_retryable_error`. Pi's Esc binding during countdown calls
`session.abortRetry()` (NOT `session.abort()` — the latter tears down the
whole turn).

Pure TUI consumer sprint: no protected-core touch.

## Decision (1 file; non-protected)

### `shell.py` additions

1. **Module-level constant** `_RETRY_WIDGET_KEY = "__auto_retry__"` — the
   chrome widget slot for the countdown overlay (W-review LOW-4: hoisted so
   tests + docs reference one canonical name).

2. **`retry_countdown_ref: dict[str, asyncio.Task[None] | None]`** — holds the
   live ticker task ref. Dict (not nonlocal) so the closures mutate cleanly.

3. **`_tick_retry_countdown(attempt, max_attempts, delay_ms)`** — async
   per-second ticker:
   - Captures the spawning task; exits early if `retry_countdown_ref["task"]
     is not current` (W-review MEDIUM-1: defends back-to-back start races
     where the prior ticker's stale write could clobber the new label after
     `cancel()` schedules but before it lands).
   - Defensive coerce `delay_ms` to a non-negative `int|float` (W-review
     LOW-2 — a malformed event with `delay_ms=None` would crash the ticker
     via `None / 1000.0`).
   - Each loop: write the chrome widget with
     `"⟳ Retrying (N/M) in Xs… Esc to cancel"`, sleep `min(1.0, remaining)`,
     decrement.
   - On sleep end: write a "now…" placeholder bridge (pi has this implicitly
     via the `Loader` spinner; Aelix has to engineer it).
   - `except Exception: return` (W-review MEDIUM-2: the prior
     `try/except CancelledError: raise` was a dead no-op; the real risk is
     a stray widget/sleep crash being swallowed by `loop.create_task`'s
     "Task exception was never retrieved" log-only fate).

4. **`_start_retry_countdown(event)`** — cancels any prior ticker (cooperative
   `cancel()`, ticker exits at the next supersession check), swaps
   `out_chrome.on_interrupt = _on_retry_interrupt` (pi parity: Esc during
   countdown → `abort_retry`), schedules a new ticker via
   `loop.create_task`.

5. **`_end_retry_countdown(event)`** — idempotent for stray ends (W-review
   HIGH): cancels ticker, clears widget, restores
   `out_chrome.on_interrupt = _on_interrupt`. If no active retry was in
   progress (`had_active is False`), returns BEFORE committing a misleading
   "✖ Retry failed" line. Otherwise commits a transcript Text:
   - `"✓ Retry succeeded (attempt N)"` on `success=True`
   - `"✖ Retry failed: {reason}"` on `success=False` (reason from
     `event.final_error` or `"cancelled"`).

6. **`_on_retry_interrupt`** — Esc handler installed during countdown. Calls
   `harness.abort_retry()` synchronously (per ADR-0128 enhanced `abort_retry`
   sets the flag + wakes `_retry_abort_event`). Pi parity.

7. **`_on_agent_event` dispatch** — `auto_retry_start` →
   `_start_retry_countdown`, `auto_retry_end` → `_end_retry_countdown`. The
   renderer is called first (no-op for these events — marked listener-only at
   ADR-0128).

8. **`finally` cleanup** — cancels `retry_countdown_ref["task"]` if a /quit /
   signal lands mid-backoff, so the ticker doesn't outlive `run_tui`.

### Pi parity verification

- Widget label format mirrors pi's `CountdownTimer` output.
- Esc swap routes to `abort_retry()` (sync, sets `_state.retry_aborted` +
  wakes the sleep event per ADR-0128 enhanced `abort_retry`), NOT `abort()`.
- Transcript commit on terminal-success / terminal-fail matches pi's
  post-retry feedback.
- Back-to-back `auto_retry_start` (attempts 1 → 2 → 3 without intervening
  `auto_retry_end`) is the normal multi-attempt harness contract — verified
  via `core.py:1487-1565`. The self-supersession check makes this race-clean.

## Deferred (intentional)

- **Spinner glyph cycling** — pi's `Loader` shows a `⠋⠙⠹…` braille spinner
  during the "now…" bridge. Aelix shows a static `⟳`. Cosmetic; a future
  refactor could animate it through the same ticker loop.
- **Live tick assertion in tests** — testing the per-second decrement
  deterministically requires either monkeypatching `asyncio.sleep` or a
  real ≥1s wait. The 5 new smoke tests assert appearance / label refresh /
  idempotency / shutdown / handler swap; per-second decrement is implicit in
  the visible label change between the start and "now…" bridge.

## Consequences

- **Files touched**: 1 non-protected (`shell.py`); 0 protected.
- **`git diff --stat docs/contracts packages/aelix-agent-core`**: empty ✓.
- **Tests**: 5 new in `tests/tui/test_run_tui_smoke.py`:
  - `test_run_tui_auto_retry_countdown_shows_and_clears_widget`
  - `test_run_tui_auto_retry_esc_calls_abort_retry_not_abort`
  - `test_run_tui_auto_retry_back_to_back_starts_cancel_prior_ticker`
  - `test_run_tui_auto_retry_end_without_prior_start_is_idempotent` (HIGH)
  - `test_run_tui_auto_retry_shutdown_cancels_ticker_mid_backoff` (LOW-3)
- **Gate**: ruff clean; pyright 0-new on touched files; pytest 3093 → 3095
  (+2 net after baseline subtraction; +5 new auto-retry tests; 1 pre-existing
  flaky unrelated RPC test).
- **Pi-faithful**: every block cites the pi reference inline; widget format,
  Esc → `abort_retry` routing, transcript commit on terminal events.

## Code review (separate lane) — REQUEST CHANGES → all blockers + nits applied

`code-reviewer`: 0 CRITICAL / 1 HIGH / 2 MEDIUM / 3 LOW. Findings:

- **[HIGH]** `_end_retry_countdown` committed a misleading "✖ Retry failed"
  line on a stray `auto_retry_end` without a prior `auto_retry_start`.
  FIXED: added `had_active = task is not None` guard; chrome invariants
  (widget cleared + handler restored) apply idempotently, but the commit
  skips entirely when `not had_active`. New test
  `test_run_tui_auto_retry_end_without_prior_start_is_idempotent`.
- **[MEDIUM-1]** Cancel-without-await race: the prior ticker could clobber
  the new ticker's label between `cancel()` and the CancelledError landing.
  FIXED: `_tick_retry_countdown` captures `current = asyncio.current_task()`
  and exits if `retry_countdown_ref["task"] is not current` (self-supersession
  before each `set_widget` call). The back-to-back smoke test still passes.
- **[MEDIUM-2]** `try/except asyncio.CancelledError: raise` was dead code
  that hid the real risk of a silent ticker crash. FIXED: swapped to
  `except Exception: return` so a stray widget/sleep crash is logged-and-
  return rather than swallowed by `loop.create_task`. CancelledError is
  BaseException → not caught → propagates as the cooperative cancellation
  it is.
- **[LOW-2]** `delay_ms=None` would crash the ticker. FIXED: defensive
  coerce at the top of `_tick_retry_countdown`.
- **[LOW-4]** `_RETRY_WIDGET_KEY` was function-local. FIXED: hoisted to
  module-level.
- **[LOW-1]** `loop = asyncio.get_running_loop()` future-proofing. NO ACTION:
  not a bug today; only relevant if the harness moves to a worker thread.
- **[LOW-3]** Test gaps. PARTIALLY ADDRESSED: 2 new tests
  (idempotent-end + shutdown-mid-backoff); per-second decrement assertion
  deferred (see "Deferred" above).

## Verification

- Unit tests: 5 new smoke tests cover the full state machine — appearance,
  label refresh, idempotent end, shutdown cancellation, Esc handler swap +
  restoration. All deterministic (no real wall-clock dependencies).
- Pi-port fidelity: every block cites `interactive-mode.ts:2919-2948`. The
  widget label format, Esc → `abort_retry` routing, and transcript commit
  on terminal events match pi's countdown behavior.
- Code review (separate lane): REQUEST CHANGES → all 1 HIGH + 2 MEDIUM + 2
  LOW applied (LOW-1 future-proofing only; LOW-3 partial — 2 of 3 test
  gaps closed).
- Live verification: deferred (would require throttling a real provider).
  The synthetic-event smoke tests exercise the full subscriber state
  machine end-to-end via the harness `subscribe` seam.
