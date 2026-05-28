# 0128. Sprint 6h₂₀ — Auto-retry with exponential backoff (pi-faithful port)

Status: Accepted (6h₂₀ shipped — v1, UI countdown deferred to v2)
Date: 2026-05-28
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Audit MEDIUM #8 — pi's `agent-session._handleRetryableError` retries retriable
provider errors (rate-limit / 429 / 5xx / network / connection / timeout) with
exponential backoff (2s/4s/8s) before propagating the error to the user. Aelix's
loop had a stub `auto_retry_enabled` flag (default True) + `agent_loop_continue`
"retry path" plumbing in `loop.py`, but no actual retry logic. Long-running
sessions hit a provider blip and the turn died.

This sprint is the **pi-byte-faithful port** of `_isRetryableError` +
`_handleRetryableError` + `abortRetry` + the call-site wiring. User explicitly
approved this protected-core change as part of the #3·#4·#8 trio (same ADR-0117
pattern that approved the compaction summarizer port).

## Decision (3 protected files; UI subscriber deferred to v2)

### Events (`types.py`)

Two new events on the `AgentEvent` union (pi
`agent-session.ts:1948-1953`/`2456-2462`):

```python
AutoRetryStartEvent(attempt, max_attempts, delay_ms, error_message)
AutoRetryEndEvent(success, attempt, final_error)
```

UI-only — the TUI renderer subscribes to drive a countdown. Extensions don't see
them as lifecycle hooks (`emit` closure in `_run` skips the hook-bus projection;
`_to_hook_event` has explicit "unreachable" cases to satisfy
match-exhaustiveness).

### Harness (`core.py`)

Three module constants (pi `settings-manager.ts:721-727`):

```python
_AUTO_RETRY_MAX_ATTEMPTS = 3
_AUTO_RETRY_BASE_DELAY_MS = 2000
_RETRYABLE_ERROR_PATTERN = re.compile(  # verbatim pi :2421
    r"overloaded|provider.?returned.?error|rate.?limit|too many requests|"
    r"429|500|502|503|504|service.?unavailable|server.?error|internal.?error|"
    r"network.?error|connection.?error|connection.?refused|connection.?lost|"
    r"websocket.?closed|websocket.?error|other side closed|fetch failed|"
    r"upstream.?connect|reset before headers|socket hang up|ended without|"
    r"stream ended before message_stop|http2 request did not get a response|"
    r"timed? out|timeout|terminated|retry delay",
    re.IGNORECASE,
)
```

Three new methods on `AgentHarness` (after `_check_auto_compaction`):

- `_is_retryable_error(message)` — pi `:2414-2426`: returns True only when
  `stop_reason == "error"` and `error_message` matches the regex.
  Context-overflow exclusion is handled preemptively by 6h₁₈ auto-compaction
  (ADR-0126); the regex doesn't match overflow markers anyway.
- `_handle_retryable_error(message)` — pi `:2432-2506`:
  - Disabled flag → return False.
  - Increment `_retry_attempt`; if > max → emit `auto_retry_end {success:False,
    final_error}` + reset + return False.
  - `delay_ms = base * 2^(attempt-1)` → 2s / 4s / 8s.
  - Emit `auto_retry_start {attempt, max_attempts, delay_ms, error_message}`.
  - Pop the error assistant from `_state.messages` (pi `:2473-2476`).
  - `asyncio.wait_for(_retry_abort_event.wait, timeout=delay_ms/1000)` —
    `TimeoutError` means sleep completed (proceed with retry); success means
    abort fired → emit `auto_retry_end {final_error:"Retry cancelled"}` + reset
    + return False.
- `abort_retry()` (existing stub enhanced) — pi `:2511`: sets
  `_state.retry_aborted = True` (existing) AND wakes `_retry_abort_event` (new)
  so a mid-backoff sleep cancels immediately.

`_emit_to_subscribers(event)` helper mirrors `_run`'s listener-loop pattern for
out-of-loop event emission.

### Retry loop integration (`prompt()`)

After `_run` returns (pi `_processAgentEvent` after `agent_end`):

```python
while True:
    last_assistant = … (last AssistantMessage in _state.messages)
    if last_assistant is None or not _is_retryable_error(last_assistant):
        break
    did_retry = await _handle_retryable_error(last_assistant)
    if not did_retry:
        break
    # Re-run from existing context — pi's agent.continue() equivalent.
    result = await _run([], system_prompt=system_prompt)

# pi :561-567 — reset counter on terminal-success assistant.
if _retry_attempt > 0 and terminal_assistant.stop_reason != "error":
    success_attempt, _retry_attempt = _retry_attempt, 0
    emit AutoRetryEndEvent(success=True, attempt=success_attempt)

await _check_auto_compaction()  # 6h₁₈ — pi order: retry BEFORE compaction
```

Empty `prompts=[]` means `_run` doesn't append a new user message — equivalent
to pi's `agent.continue()`.

### Deferred to v2 (intentional)

- **TUI countdown UI subscriber** — pi `interactive-mode.ts:2919-2948` shows
  "Retrying (N/M) in Xs… (Esc to cancel)" via `CountdownTimer` + `Loader`. v1
  emits the events; a follow-up sprint adds the renderer/chrome subscriber. The
  feature is **functionally complete in v1** (retries actually fire, the
  user-facing turn returns the right messages, `abort_retry()` works) — just
  with silent backoff vs the live countdown.
- **Context-overflow exclusion** — pi `_isRetryableError :2419-2420` calls
  `isContextOverflow(message, contextWindow)` to short-circuit before regex.
  6h₁₈ auto-compaction handles overflow preemptively at the threshold gate, and
  the retriable regex doesn't match overflow markers ("context length exceeded"
  etc.). Belt-and-suspenders only.
- **`provider` field in retry settings** — pi `settings-manager.ts:25-29` has
  per-provider retry overrides. Aelix has no SettingsManager in core; module
  constants are global.

## Consequences

- ruff clean; pyright 0 errors on `core.py` + `types.py` + `tests/test_auto_retry.py`.
- **Tests**: 34 new in `tests/test_auto_retry.py` covering:
  - 19 retriable regex positives + 4 non-retriable + 2 non-error/no-msg
    negatives.
  - `_handle_retryable_error` — disabled, emits start + sleeps, exponential
    backoff progression (1ms/2ms/4ms with monkeypatched base), at-max emits
    failure + reset, abort mid-sleep emits cancel + reset.
  - Integration via `prompt()` — retry-then-success (counter resets, emits
    `auto_retry_end {success:True}`), max-retries-fail (3 starts + 1 failure
    end), disabled = no events.
- **Protected core**: 2 files touched in `packages/aelix-agent-core` (`core.py`
  + `types.py`); `docs/contracts` byte-unchanged.
- **No regression**: existing 23 `test_compact.py` tests still pass (auto-retry
  fires BEFORE auto-compact in `prompt()`, the order pi uses).

## Code review (separate lane) — APPROVE-WITH-NITS → all applied

`code-reviewer`: 0 CRITICAL / 0 HIGH; port fidelity byte-faithful (regex
verbatim, backoff exact, max-retries semantics correct, counter reset, pre-retry
pop, retry-before-compaction order, UI-only event routing, abort-mid-sleep wake
all verified vs pi). Findings addressed:

- **[MEDIUM-1]** `_state.retry_aborted` was set in `abort_retry()` but never
  cleared → consumers of the flag observed `True` indefinitely. FIXED:
  `_handle_retryable_error` clears it at retry entry (pi parity:
  `_retryAbortController` is recreated per retry, `agent-session.ts:2479`).
- **[MEDIUM-2]** `_run([], …)` empty-prompts contract is fragile if a future
  commit adds a non-empty assertion in `agent_loop`. FIXED: added an `assert
  any(isinstance(m, UserMessage) for m in messages)` invariant before the retry
  continue — turns the latent failure into a loud one.
- **[LOW-1, LOW-2]** Duplicate `AssistantMessage` + `inspect` function-local
  imports (already module-level). FIXED: removed; module-level used.
- **[LOW-3]** Test gap on InputHandled + busy-guard non-interaction with the
  retry loop. FIXED: added `test_input_handled_short_circuit_skips_retry_loop`
  + `test_busy_guard_does_not_trigger_retry_loop` — both assert no retry events
  fire on those short-circuit paths.

## Verification

- Pi-port fidelity: regex verbatim from pi `:2421`; backoff formula `base * 2^(n-1)`
  matches pi `:2458`; pre-retry error-message pop matches pi `:2473-2476`;
  abort-via-AbortController-equivalent matches pi `:2479-2495`.
- Code review (separate lane): see commit body for outcome.
- Live verification (deferred to v2 along with the UI subscriber): triggering a
  real retriable error requires throttling a real API key or fault-injecting at
  the adapter. The deterministic unit + integration tests cover the state
  machine end-to-end via mocked `_run`.
