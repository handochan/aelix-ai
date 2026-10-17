# 0128. Sprint 6h₂₀ — Auto-retry with exponential backoff (pi-faithful port)

Status: Accepted (6h₂₀ shipped — v1, UI countdown deferred to v2) — amended 2026-10-14 (#408): the retry reads `settings.json`'s `retry` block — amended 2026-10-14 (#414): `520` and `524` are retried (pi's seven statuses), matched anywhere in the text as pi matches them
Superseded by: [ADR-0260](0260-retry-recovery-closes-at-the-successful-assistant-response.md)
for successful retry completion timing only; classification, backoff and failure cleanup remain accepted.
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

## Amendment (2026-10-14, #408) — the retry reads `settings.json`'s `retry` block

Pi reference: `earendil-works/pi@3daa20280`.

### What was measured on `7d163232`

The "module constants" decision above (and its deferral of a settings manager:
"Aelix has no SettingsManager in core") outlived its premise. Since ADR-0091 the
CLI builds one `SettingsManager` and since issue 44 it reaches every harness it
builds (`cli/entry.py` `_build_harness_options`, `settings_manager=`), for the
TUI, `-p`, `--mode json` and `--mode rpc` alike, and a delegated agent's child
is the same CLI reading the same file. Nothing read `retry` from it:

- in-process (`AgentHarness` with `SettingsManager.in_memory`, every attempt a
  `502`): `{"retry": {"enabled": false}}`, `{"maxRetries": 1, "baseDelayMs": 10}`
  and `{"maxRetries": 0}` all emitted `auto_retry_start` `(1, 3, 2000)`, then
  `(2, 3, 4000)`, and `auto_retry_enabled` stayed `True`;
- real `aelix -p` against a local always-`502` server: 12 model POSTs and
  20-24 s for each of the three settings files (4 harness attempts, each three
  POSTs because the OpenAI SDK retries twice itself - `retry.provider.*`, below);
- real `aelix --mode rpc`: `get_state` `autoRetryEnabled: true` under
  `retry.enabled: false`, and `set_auto_retry` left the file untouched.

Kits and outputs: `.omc/probes/408-live/impl/`.

### Decision

pi's `_prepareRetry` (`agent-session.ts:3792-3830`) reads
`settingsManager.getRetrySettings()` at every retry; aelix now does the same in
`_handle_retryable_error` through `_auto_retry_policy`:

- `retry.enabled` — `false` ends the turn on the first failure. pi's
  `autoRetryEnabled` getter and `setAutoRetryEnabled` setter ARE the setting
  (`:3846-3856`): `AgentHarness.auto_retry_enabled` returns the settings value
  when the settings carry a boolean there, and `set_auto_retry_enabled` (rpc
  `set_auto_retry`) also calls `SettingsManager.set_retry_enabled`, which writes
  the GLOBAL `settings.json` - the toggle outlasts the session, as in pi. A
  project `retry.enabled` still wins in the merged view, as in pi.
  `AgentState.auto_retry_enabled` remains the toggle for a harness without a
  settings manager (and for settings that do not say).
- `retry.maxRetries` — retries AFTER the first attempt (`attempt > maxRetries`
  stops), so `0` sends once. With `0` no `auto_retry_end` is emitted (no
  sequence started); pi's final end needs `_retryAttempt > 0` (`:1964`). Before
  this the path was unreachable (the constant was 3) and would have emitted an
  end with `attempt: 0`.
- `retry.baseDelayMs` and `retry.maxAgentDelayMs` — `delay = min(base *
  2^(attempt-1), maxAgentDelayMs)` (pi `retryDelayMs`,
  `packages/ai/src/utils/retry.ts:129-133`). The cap is new here: with the
  default 3 retries it was never reached (2/4/8 s), with `maxRetries` now
  configurable it is (the 6th wait). `RetrySettings` gains
  `max_agent_delay_ms` (`maxAgentDelayMs`, default 60000 in
  `get_retry_settings()`).
- Read at each decision, so `/reload` (or any settings change the manager
  holds) applies to the next retry - including mid-sequence: a `retry.enabled`
  turned off while a sequence runs stops it, and the #147 arm closes it with one
  `auto_retry_end {success: false}`.
- **Fallbacks.** A value the settings do not carry falls back to the module
  constant `_AUTO_RETRY_MAX_ATTEMPTS` / `_AUTO_RETRY_BASE_DELAY_MS` /
  `_AUTO_RETRY_MAX_DELAY_MS`, read at that moment, not to
  `get_retry_settings()`'s literal defaults - the values are the same in
  production, and tests that write `{}` to `settings.json` and monkeypatch the
  constants keep governing the retry instead of waiting real 2/4/8 s.

### Choices made under the decision (ADR-0235)

- **Wrong-typed values are ignored, not raised.** The settings loader keeps
  whatever JSON the file holds; a string, boolean, `null`, list or non-finite
  number in `maxRetries` / `baseDelayMs` / `maxAgentDelayMs`, a non-boolean
  `enabled`, or a `retry` that is not an object falls back as if unset. pi would
  coerce some of these (JS `attempt > "5"`); raising inside a turn would be
  worse than either. A negative number counts as `0` (what pi ends up doing).
  A fractional `maxRetries` is floored (same stop as pi's comparison, an
  integer `maxAttempts` on the wire).
- **The delay is rounded to whole milliseconds, half to even** (Python
  `round`: a `baseDelayMs` of 1.5 waits 2, 3, 6 ms; 2.5 waits 2, 5, 10 ms).
  pi's `retryDelayMs` sends a non-integer product to the cap (its
  `Number.isSafeInteger` overflow guard), so a fractional `baseDelayMs` waits
  60 s there. Not reproduced. Like pi's, the delay never exceeds
  `Number.MAX_SAFE_INTEGER` (2^53 - 1).
- **The `aelix-server` WebSocket harness gets no settings manager.** Its
  harness factory (`rpc_ws.py`) builds a bare harness from the server's own
  config; pi has no server. Giving it one would make a server read (and rpc
  `set_auto_retry` write) the settings file of whatever account runs it - a
  deployment decision, not a parity one. It keeps the constants and the
  in-memory toggle.
- **Not covered:** `retry.provider.*` (`timeoutMs`, `maxRetries`,
  `maxRetryDelayMs` - pi passes them into every request's stream options,
  `sdk.ts:344-352`; aelix's adapters keep their SDK defaults, so an OpenAI-SDK
  provider still tries each attempt three times), the compaction and
  branch-summary retries (#409), and the `compaction.*` settings, which the
  harness does not read either.

### Tests and sabotage

`tests/test_auto_retry_settings_408.py` (29: each key, the cap, the fallbacks
read at decision time, a settings change between prompts and mid-sequence, an
edited file after a settings reload, wrong types, the toggle's precedence and
its persistence to the file, the project override) and
`tests/cli/test_retry_settings_408.py` (3: the real `_async_main` in json mode
with `settings.json` `enabled: false`, `maxRetries`/`baseDelayMs`, and `{}`).
20 of the 32 fail on `7d163232`; the 12 that pass are the controls (no settings
manager, `{}`, and the wrong-type rows, which expect the defaults). Nineteen
mutations of the fix (`.omc/probes/408-live/impl/sabotage_408.py`), each alone -
the decision, the budget, the delay, the cap, the event's `max_attempts`, the
`maxRetries: 0` end, the getter, the setter's write, literal instead of
read-now fallbacks, a policy cached at first read, no type check, no negative
clamp, no non-finite filter, `maxAgentDelayMs` unmapped, toggle-and-setting,
round instead of floor, truthy `enabled`, `get_retry_settings` without the cap -
each turn at least one row red; unmutated, 133 pass.

The new helpers sit at the end of `harness/core.py` and the edited methods keep
their line counts: about a hundred gated citations name lines of that file
(55 locked ranges), and a first version that inserted the helpers next to the
constants moved 94 of them, plus one into `settings_manager.py`, across 44 files.

### Review round 2 (2026-10-14)

Settings can now ask for what the constants never did, and three paths broke
on it (kits and outputs: `.omc/probes/408-live/r2/`):

- **A 0 ms wait lost an abort.** `retry.baseDelayMs: 0`, a negative base or
  `maxAgentDelayMs: 0` give a 0 ms wait, and `asyncio.wait_for(event.wait(),
  timeout=0)` cancels the waiter before it runs and raises `TimeoutError` even
  when the #334 code has already set the event. An `abort_retry()` or
  `abort()` made while `auto_retry_start` was delivered was lost and the turn
  sent again: 3 attempts at `maxRetries: 2`, `baseDelayMs: 0`, against 1 at
  `baseDelayMs: 1`. `_retry_backoff_aborted` now reads the event before and
  after the wait instead of inferring the abort from how `wait_for` ended,
  yields once at a 0 ms wait (an abort already scheduled lands, as in a 1 ms
  wait), and then reads the turn's abort flag as pi re-checks
  `_agentRunAbortRequested` after `_prepareRetry` (`agent-session.ts:1954-1957`),
  so an extension's `ctx.abort()` during the backoff, which then set only that
  flag, cancelled the retry too - but only once the whole wait had run (round 3
  below makes it wake the wait). Every abort row: 1 attempt, one `auto_retry_end
  {success: false, final_error: "Retry cancelled"}`, on Python 3.11 and 3.12.
- **rpc `set_auto_retry` on a `retry` that is not an object.** With a global
  `{"retry": false}` (or `true`, `"off"`, `[]`, `3`)
  `SettingsManager.set_retry_enabled` raised `AttributeError`; rpc answered
  `success: false` with the harness toggle already flipped (the parent
  answered `success: true`). pi's `setRetryEnabled` (`settings-manager.ts:897-904`)
  replaces only a falsy `retry` (`false`) with `{}` and sets a property on `[]`
  silently; `true`, `"off"` and `3` throw a `TypeError` (strict mode). The read
  path here ignores all five, so the write path now agrees: it replaces each
  with `{"enabled": ...}` (on disk as well, other keys kept). The harness
  writes the setting FIRST and sets its toggle after, so an error raised before
  the write is queued leaves both where they were and `success: false` is true.
  The file itself is written by a background task after the answer: a write
  that fails on disk (a read-only directory) is not reported to rpc, which has
  already answered `success: true` - pi also answers success there - and the
  value holds for that session only (round 3).
- **`OverflowError` past 1024 retries.** `base * 2**(n-1)` with a float base
  (JSON `1.0`) raised inside the turn once `2**(n-1)` no longer fit a float.
  `delay_ms` uses `math.ldexp` and takes a product too large for a float as
  the cap; `{"maxRetries": 1100, "baseDelayMs": 1.0, "maxAgentDelayMs": 0}`
  now runs its 1101 attempts.
- The guide named pi's camelCase event keys; aelix's wire is snake_case
  (`max_attempts`, `delay_ms`, `error_message`; `final_error`), now named in
  the guide and pinned by `tests/cli/test_retry_settings_408.py`.

Rows added to `tests/test_auto_retry_settings_408.py` (67 tests with the
first round's) and pinned spellings in the CLI file (3): 30 fail on the
round-1 commit. Thirteen mutations, each alone, turn rows red on 3.11 and
3.12 (`.omc/probes/408-live/r2/kit/sabotage_r2.py`), including the round-1
`wait_for`, no yield at 0 ms, no flag re-check, the round-1 product, no
rounding, truncation, half-up rounding, an unclamped negative cap, the
`is None`-only write and toggle-before-write.

### Review round 3 (2026-10-14)

An independent cross-review of round 2 (kits and outputs:
`.omc/probes/408-live/r2cross-claude/`, `.omc/probes/408-live/r3/`):

- **An extension's `ctx.abort()` waited out the backoff.** It is `_mark_abort`,
  which set only `_abort_requested`; `_retry_backoff_aborted` reads that flag
  after the wait, so the retry was cancelled only when the whole wait had run
  (`baseDelayMs: 5000`, `ctx.abort()` 100 ms in: the turn ended 4900 ms after
  the abort). In pi `ctx.abort()` is `this.abort()`, whose `abortRetry()` wakes
  the sleep (`agent-session.ts:2464-2473`, `:3472-3478`). `_mark_abort` now also
  sets the retry's abort event while a backoff is in progress: the same run
  returns 0 ms after the abort, 1 attempt, `Retry cancelled`.
- **The setter's harness toggle was untested.** pi's setter writes the setting
  only; here the harness toggle is set too, and it is what answers when a
  trusted project's `"retry": false` replaces the global block in the merged
  view. A row pins it (`set_auto_retry false` there: `autoRetryEnabled` false
  and 1 attempt).
- **The TUI countdown showed `in 0s`.** The ticker printed `int(remaining)`;
  a 500 ms wait read `in 0s` and 1.5 s `in 1s`. It rounds up now, as pi's
  `CountdownTimer` (`countdown-timer.ts:18`, `Math.ceil`): `in 1s`, `in 2s`.
- **Text.** Round 2's "pi throws there too" holds for three of five shapes
  (above). The guide said a wrong-typed value always gives the default; across
  scopes `deep_merge_settings` skips a project `null`, so the global value
  applies (pi skips only `undefined`), while a project string or list replaces
  it. A delegated child reads the global file's values only: `child_trust_argv`
  passes `--no-approve` whenever `.aelix/settings.json` exists. The settings
  write is backgrounded, so an unwritable file still gets `success: true`.

Rows: `ctx.abort()` 100 ms into a 300 ms and a 60 s wait (1 attempt, `Retry
cancelled`, the turn over within 10 s), `set_auto_retry false` over a trusted
project's `"retry": false`, and the countdown's first label at 500, 1000 and
1500 ms. On the round-2 commit the 60 s row and the 500 and 1500 ms labels
fail, and the 300 ms row fails once the flag is read only before the wait.
With the fix that mutant is equivalent - every abort made during the wait now
sets the event as well; only a `ctx.abort()` made while `auto_retry_start` is
still delivered, before the event exists, sets the flag alone (round 4) - so
the mutants that now matter are a `_mark_abort` that
sets only the flag (the 60 s row; with the before-only read, both rows), a
wait that is not woken early (60 s), the pi-literal setter (the trusted
project row) - each red on Python 3.12 and 3.11 - and `int` (500 and 1500 ms)
and `round` (500 ms), red on 3.12.

### Review round 4 (2026-10-14)

The round-3 verify passed and measured one narrow gap (kits and outputs:
`.omc/probes/408-live/r3verify/`, `.omc/probes/408-live/r4/`):

- **A `ctx.abort()` made while `auto_retry_start` is delivered waited out the
  backoff.** An async subscriber still holds the emit, the retry's abort event
  does not exist yet, so `_mark_abort` sets only the flag, and
  `_retry_backoff_aborted` read the flag only after the wait: at
  `baseDelayMs: 3000` the turn returned 3041 ms after the abort (1 attempt,
  `Retry cancelled`), while `abort()` and `abort_retry()` in the same window
  ended it in about 40 ms through #334's `retry_aborted`. The flag is now read
  before the wait as well as after it: the same run returns 38 ms after the
  abort on 3.12 and 40 ms on 3.11. This goes slightly beyond pi, whose emit is
  sync, so a listener's `abort()` there sleeps the full delay before the
  `:1954` check.

Row: `ctx.abort()` from an async `auto_retry_start` subscriber at a 3 s and a
60 s wait (the event is still `None` at the abort; 1 attempt, `Retry
cancelled`, the turn returns within 1.5 s of the abort on the loop clock and
within `wait_for` 10 s). Both fail on the round-3 commit and with the pre-wait
read of the flag removed, on Python 3.12 and 3.11. The round-3 mutant that read
the flag only before the wait is now part of the product (it reads it before
and after).

## Amendment (2026-10-14, #414) — `520` and `524` are retried, matched as pi matches them

Pi reference: `earendil-works/pi@3daa20280` (`packages/ai/src/utils/retry.ts`,
`RETRYABLE_PROVIDER_ERROR_PATTERN` and `isRetryableAssistantError`, which pi's
`_isRetryableError` calls, `agent-session.ts:3736-3743`).

### What was measured on `529b997e`

A real `aelix --mode json` against a local server (fake keys, `retry.baseDelayMs: 10`;
kits and outputs: `.omc/probes/414-live/impl/`, `logs/base_retry.out`). That server sent
`x-should-retry: false` with every error, so the SDKs' own retries were off and each
answer below is one request:

- anthropic answering `520` then `200`: one model request, no `auto_retry_start`, the
  turn ended on `Error code: 520 - …`; the same for `524`, and for an openai-completions
  provider answering `520`. pi's pattern has `520` and `524`, and #379 had already given the
  OAuth refresh retry pi's seven statuses (ADR-0251 §12.5 item 3); the model-error pattern
  had five.
- a `400` whose body reads `max_tokens: 25000 > 8192, which is the maximum allowed number of
  output tokens …` was retried (`auto_retry_start (1, 3, 10)`): its `25000` holds `500`.

With the SDKs' retries on - a plain `520` with no such header, as a real gateway sends it -
the Anthropic and OpenAI SDKs first send the request twice more themselves (their default
`max_retries` of 2, which covers any status of 500 or above). Measured by review round 1
(`.omc/probes/414-live/verify/logs/probe_retry.out`): `529b997e` made three requests (`520`,
`520`, `520`) and ended the turn, for anthropic and an openai-completions provider.

### Decision

The statuses are pi's seven: `429`, `500`, `502`, `503`, `504`, `520`, `524`, matched as pi
matches them - as bare alternatives of the case-insensitive pattern, anywhere in the error
text (`…|429|500|502|503|504|520|524|…`). The rest of the pattern is unchanged, and so is
#408's settings-driven policy (a `520` follows `retry.maxRetries`/`baseDelayMs`/`enabled`
like a `502`). After, the real CLI (`.omc/probes/414-live/r2/kit/probe_retry.py`,
`r2/logs/probe_retry.out`): a plain `520` or `524` makes the SDK's three requests, then the
harness retries, `auto_retry_start (1, 3, 10)`, and the `200` answers, `auto_retry_end
(True, 1)` - for anthropic and an openai-completions provider; with `x-should-retry:
false`, one request, the retry and the `200`.

**Digits that contain a status make an error retryable, as in pi and as on `529b997e`.**
`max_tokens: 25000` holds `500`, Anthropic's `messages.520: …` names a message index, and a
request id, a duration or a token count can hold any of the seven. Each such `400` is sent
again until the budget runs out - measured with the real CLI (`r2/logs/probe_retry.out`):
the `max_tokens: 25000` `400`, the `messages.520` `400`, and a `400` naming
`req_8f524e1c`, `15240 ms` and `15200 tokens` each made 4 requests, `auto_retry_start`
`(1, 3, 10)`, `(2, 3, 20)`, `(3, 3, 40)`, `auto_retry_end (False, 3)`. Whether aelix should
stop retrying these is a follow-up issue, not this change.

### Review round 2: round 1's whole-number rule is dropped

Review round 1's version matched a status only as a whole number (no ASCII letter, digit
or `_` next to it), a divergence from pi. Its review showed the rule neither held what it
claimed nor matched pi: `messages.520`, `messages[524]`, `1,520 tokens`, `0.524 s` and
`req-520-abc` still retried (`r2/logs/probe_retry.out`: round 1's CLI sent the
`max_tokens: 25000` and the request-id `400`s once but the `messages.520` one four times),
and seven other boundary forms passed all its rows. Review round 2 narrowed the change to pi's semantics; the
digits question is the follow-up above. The statement that a "token count, duration or
request id holding `520`/`524` is not retried" is withdrawn everywhere it was made.

### Not changed (follow-up)

pi's pattern has grown since this ADR's pin: `server_busy`, `servers are currently busy`,
`currently experiencing high demand`, `model is at capacity`, `exceeded request buffer
limit while retrying upstream`, `getaddrinfo`, `ENOTFOUND`, `EAI_AGAIN`, `socket connection
was closed`, `stream ended before a terminal response event`, `pending stream has been
canceled`, the retry-guidance texts (`you can retry your request`, …), `ResourceExhausted`
and `subscription_sharing_usage_unavailable` / `_user_unavailable`; and it tests a
non-retryable pattern first (quota and billing: `insufficient_quota`, `quota exceeded`,
`billing`, `GoUsageLimitError`, `subscription_sharing_usage_limit_exceeded`, …), so a `429`
for an exhausted quota is not retried there - aelix retries it three times. The OpenAI
Codex adapter's own transport retry (`openai_codex_responses._RETRYABLE_STATUS`, before
the stream opens) is a separate layer and keeps its set.

### Tests and sabotage

`tests/test_auto_retry_520_524_414.py` (46 rows): the texts each SDK writes for each status
(13), the digit-holding texts pi retries (17, among them review round 1's `messages.520`,
`messages[524]`, `1,520 tokens`, `0.524 s`, `req-520-abc` and the inputs its surviving
boundary forms differ on), other statuses not retried (8), the harness classifier, the
`retry` settings policy with `520`/`524`, `retry.enabled: false`, the `25000` turn retried
to the end of its budget, and the real anthropic adapter over `httpx.MockTransport`
answering `520` then `200`. On `08379e16` (round 1) 14 fail: the 12 digit-holding texts its
rule did not retry and the two `25000` rows (`r2/logs/red_on_08379e16.out`). Mutations in a
throwaway worktree (`r2/kit/sabotage_r2.py`, `r2/logs/sabotage_r2.out`; 1008 passed
unmutated over the auth, retry, loop and renderer rows): no `520` 16 red, no `524` 10,
`529b997e`'s five 26, round 1's whole-number rule 14, digit-only boundaries 8, `\b` 15, the
lookbehind alone 12, the lookahead alone 11, a `[0-9a-z]` class without `_` 13; 0 survive.
