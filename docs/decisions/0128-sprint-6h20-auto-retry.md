# 0128. Sprint 6h₂₀ — Auto-retry with exponential backoff (pi-faithful port)

Status: Accepted (6h₂₀ shipped — v1, UI countdown deferred to v2) — amended 2026-10-14 (#408): the retry reads `settings.json`'s `retry` block — amended 2026-10-14 (#414): `520` and `524` are retried (pi's seven statuses), matched anywhere in the text as pi matches them — amended 2026-10-21 (#409): a compaction's and a branch summary's summary requests retry on the same classification, settings and events
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
  harness does not read either *(2026-10-22, #469: read now, by the same pattern -
  ADR-0126 amendment)*. *(2026-10-14, #278 / ADR-0265 Decision 9: the
  two openai adapters now read the request's `max_retry_delay_ms` - the SDK's
  own retries wait for a server-requested delay only up to it, 60 s when unset,
  openai 1.x's rule, which openai 2.52 had widened to 120 s. The settings keys
  themselves still reach no adapter; that is #470.)*

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

## Amendment (2026-10-21, #409) — compaction and branch summaries retry as the turn does

Pi reference: `earendil-works/pi@fdce48eb4` (2026-10-20): `completeSummarization`
wraps every compaction and branch-summary request in `retryAssistantCall` with the
session's `settings.retry` (`packages/coding-agent/src/core/compaction/compaction.ts:611-638`,
`branch-summarization.ts:348-351`; `_runDefaultCompaction` and
`_summarizationRetryCallbacks`, `agent-session.ts:2749-2772`, `:3745-3774`;
`retryAssistantCall` and `isRetryableAssistantError`, `packages/ai/src/utils/retry.ts:204-254`,
`:265-270`). Its streamFn resolves auth on every call, so a transient OAuth refresh
failure is retried there too.

### What was measured on `7faf4b9a`

The summarizers (`session/compaction.py` `_generate_summary`,
`_generate_turn_prefix_summary`; `session/branch_summarization.py`
`generate_branch_summary`) called `get_api_key_and_headers` and the provider once.
In-process (`.omc/probes/409-live/impl/probe_repro.py`, `probe_repro.base.out`): a
provider `502` once failed `/compact` (1 request), a split turn's compaction, a
`navigate_tree(summarize=True)` summary and the threshold compaction inside `prompt()`
(which then raised the `502` out of a turn that had succeeded); the overflow
recovery's compaction failed and the turn was not re-run; an auth callback raise
carrying #379's `retry_reason` failed `/compact` at once. A real `aelix --mode rpc`
against a local OpenAI-compatible server whose summary request answered `502` once
(`x-should-retry: false`, so each answer is one request; `rpc_probe_409.base.out`):
the rpc `compact` answered `success: false` after one summary request, and the
threshold compaction of a prompt failed the same way; no compaction entry was
written. The turn retried the same `502`.

### Decision

Each summary request - the compaction's history summary, a split turn's
turn-prefix summary (each with its own budget, as pi gives each call its own),
and a branch summary - retries on the turn's decision, read at each failure:

- **Classification.** The provider's failed message through
  `AgentHarness._is_retryable_error` - the message the turn's loop classifies for
  the same answer (an error event's `error`, or a done message whose
  `stop_reason` is `"error"`), so its `stop_reason` must be `"error"` (an aborted
  stream is never retried) and the turn's pattern reads its `error_message`, a
  context overflow excluded (review round 2; round 1 read the event's top-level
  text). An auth callback raise only when it carries a non-empty `str`
  `retry_reason` (#379: a stored OAuth refresh that failed on a transient cause);
  any other raise propagates at once, unchanged. A failed closing auth check
  (`validate`, #365) is never retried, even when an adapter turns it into an error
  event whose text reads retryable. An empty summary is not retried. The request
  records what it failed on in a `SummaryAttempt` (`session/compaction.py`); the
  decision reads that record, not the exception's text, as #379 reads the harness's
  record of a setup failure.
- **Budget and settings.** `_auto_retry_policy` and `_retry_enabled` (#408):
  `retry.enabled` (else the harness toggle), `retry.maxRetries`,
  `retry.baseDelayMs`, `retry.maxAgentDelayMs`, the `_AUTO_RETRY_*` constants as
  fallback.
- **Events.** The turn's: `auto_retry_start` before each wait, one
  `auto_retry_end` when a sequence ends (`success` true when a later request
  produced the summary; `Retry cancelled` for an aborted wait); with
  `maxRetries: 0` none. pi emits `summarization_retry_scheduled` /
  `_attempt_start` / `_finished` instead and draws them with the turn's retry
  indicator; aelix's TUI, `--mode json` and rpc already show the turn's events,
  so a summary's retry looks the same in every mode - a divergence in the wire
  names (ADR-0235). The TUI draws the retried error first while "Compacting
  context…" is up (`✖ <error>`, as the turn's failed message draws it, and as pi's
  `showError` does), then the countdown.
- **Aborts.** `abort()`, `abort_retry()` and an extension's `ctx.abort()` end a
  wait at once, and nothing is sent again after one made while a summary request
  is in flight: the summary retry's abort event is
  `AgentHarness._retry_abort_event` for the length of the summarization call.
  Under a prompt's claim (the threshold and overflow compactions) an earlier
  abort of that prompt counts too; in a public `compact()` / `navigate_tree()`
  `_abort_requested` may still describe a prompt that already returned, so it is
  not read there. The TUI's interrupt keys reach the swapped handler for the
  length of any retry countdown, running or not (`AelixChrome.interrupt_armed`):
  a manual `/compact` runs outside the `set_running` turn wrapper, where Esc was
  inert and Ctrl+C armed the exit window while the countdown said "Esc to cancel".
  A split turn's countdown stands for both of its summaries: it stays (and the
  keys stay armed) while either is in its wait (review round 2). Once a wait has
  run out, the re-sent request's line reads `⟳ Retrying (N/M) now… Esc to
  interrupt` only over a turn's own request, which Esc's `abort()` cancels; over
  a summary request it reads `⟳ Retrying (N/M) now…` - nothing cancels a summary
  request in flight (#347).
- **What a request that is not retried does** - its budget ran out, retries are
  off, the error is not retryable, or a wait was aborted: its own failure
  propagates exactly as before #409 (`AgentHarnessError("compaction")` /
  `("invalid_state")` with the provider's text, or the auth callback's raise).
- A split turn's two summaries still run concurrently; when one fails the other is
  cancelled and awaited (`_gather_summaries`) - plain `gather` left a sibling
  retrying after the compaction had failed.

Every entry point goes through `AgentHarness.compact()` or `navigate_tree()`: manual
`/compact`, rpc `compact`, an extension's `ctx.compact()`, the threshold and
overflow compactions inside `prompt()`, and an extension's `navigate_tree`. The
session-level `compact()` / `generate_branch_summary()` take an optional `retry`;
without one they send each request once, as before.

### Choices made under the decision (ADR-0235)

- **Not covered:** an abort made before the first summary request of a public
  `compact()` / `navigate_tree()` (during its `session_before_compact` hooks) is
  not seen by the retry; aelix has no compaction abort at all, so it did not stop
  the compaction before either (#347). An abort does not cancel a summary request
  in flight, for the same reason - it only prevents the next one.
- **pi's newer pattern and its quota exclusions** (#414's "Not changed") apply to
  the summaries as to the turn: the classification is the turn's.
- No gated citation moves (`check_citations.py --check`: 931 gated, none drifted):
  the new code sits at the ends of `harness/core.py`, `session/compaction.py` and
  `tui/shell.py` and of the chrome class, and the edits above a cited line keep the
  count of lines above it (lines between two edits of `session/compaction.py`
  shift by up to four, where nothing cites them).
- **pi runs a split turn's two summaries one after the other** at `fdce48eb4`
  (`compaction.ts:980-1015`: the history summary is awaited, then the prefix
  summary), so its retry indicator never holds two sequences. aelix still runs
  them concurrently (`_gather_summaries`, the `asyncio.gather` ADR-0142 §2 took from
  an older pi's `Promise.all` at `734e08ed`), each with its own budget, so its TUI
  counts the sequences that started and have not ended and the waits still ahead
  (`tui/shell.py` `_RetryWaits`). Making them sequential is a follow-up, not this
  amendment.
- **Not changed:** inside a turn, the working row of a threshold or overflow
  compaction keeps the turn's own `(Ns · esc to cancel)` hint, which predates
  #409. Esc there is the turn's `abort()`: it stops any further summary retry
  (the row for an abort made while the request was failing pins that), but the
  summary request in flight runs to its end (#347) - measured in a real TUI, the
  threshold compaction completed after that Esc (`r2/out/tui-after/threshold.screen`).

### Tests, sabotage and live runs

`tests/test_summary_retry_409.py` (35 rows: each classification, the settings,
the events and their order, each abort route during the wait and during the
request, a finished prompt's abort, the earlier abort under a prompt's claim, the
threshold and overflow compactions in `prompt()`, a split turn and its prefix
summary's done-message error, the sibling cancelled in its wait and in its retried
request, a branch summary), `tests/cli/test_summary_refresh_retry_409.py` (12:
the real registry, CLI auth callback and Codex / Anthropic / Copilot refresh code
over `httpx.MockTransport` - a refresh `502` or a refused connection retried with
only the refreshed token reaching the model, a `401` failing at once with today's
message, a branch summary) and four TUI rows in `tests/tui/test_run_tui_smoke.py`.
On `7faf4b9a` 30 of the 47 fail (`red_on_base.full.out`) and the four TUI rows fail
(`red_on_base_tui.out`); the 17 that pass are the controls - what must still fail at
once (an auth raise without a `retry_reason`, a `401`, an overflow, a failed closing
check, an empty summary, `retry.enabled: false`, the toggle, `maxRetries: 0`, an abort
during the request, a refused refresh) and the session API without a retry.
Round 1's mutations, in a throwaway worktree (`sabotage_409.py`,
`sabotage_409.out`, `sabotage_409.rerun.out`), each alone - 38 of them: the retry
not passed (harness, `compact()`, `generate_branch_summary`, each split-turn
summary), the auth raise or its `retry_reason` not recorded, any truthy
`retry_reason`, the closing check by its text, every auth raise retried, the regex
without the overflow exclusion, the provider's error not recorded (in the shared
helper; the history summary's done message; the prefix summary's done message; the
branch summary's error event), no abort check, a wait that sleeps through an abort,
the turn's wait helper (reads a finished prompt's abort), no carry of the prompt's
earlier abort, a carry without the claim, the event never released or never
registered, `retry.enabled` ignored, one retry too many, no `auto_retry_start`, no
`auto_retry_end` on success or on a cancel, plain `gather`, no doubling,
`max_attempts` not the policy's, and in the TUI each key not armed, the countdown
not arming, the hand-back or the end leaving the keys armed, the error line for
every retry, none, or unbounded. The first run turned 36 red and left two (the
cancel during a retried request, the prefix summary's done-message error); a row
was added for each and both then went red. The branch summary's done-message
record was not among them - review round 2 found it unpinned (below).

Live, with the kits in `.omc/probes/409-live/impl/`: the real `aelix --mode rpc`
(`rpc_probe_409.after.out`) answered the manual `compact` `success: true` after
`auto_retry_start (1, 3, 50)` and `auto_retry_end (true, 1)`, two summary
requests and one compaction entry; with `baseDelayMs: 30000` an rpc `abort` sent
at `auto_retry_start` ended the compact 0.03 s later (`Retry cancelled`, one
summary request); the threshold compaction of the third prompt retried the same
way and wrote its entry. The real TUI (`tui_probe_409.py`, pty + pyte;
`tui-tree/{ok,esc,turn}.screen`): `/compact` drew `✖ Error code: 502 …`, `⟳
Retrying (1/3) in 3s… Esc to cancel` over `Compacting context…`, then `✓ Retry
succeeded (attempt 1)` and the summary panel - the turn's own retry draws the same
`✖` line and countdown; Esc during the countdown drew `✖ Retry failed: Retry
cancelled` and `✖ compact failed: Error code: 502 …` with no second request; with the
chrome's arming removed (`tui-nochrome/esc.screen`) the same Esc did nothing, the
countdown ran out and the retry was sent.

### Review round 2 (2026-10-21)

The verify and cross-review passes (`.omc/probes/409-live/verify/`,
`cross-claude/`) found six things; each was reproduced on round 1's commit
before it was changed (`.omc/probes/409-live/r2/`).

1. **After a countdown the TUI promised a key that did nothing.** In a real
   `/compact` (pty, `p6_live_tui.before.inflight.screen`) the re-sent request
   showed `⟳ Retrying (1/3) now… Esc to interrupt`; Esc changed nothing and
   Ctrl+C armed the exit window. The line now makes that promise only over a turn's
   own request (`_retry_now_label`); after the fix the same run shows
   `⟳ Retrying (1/3) now…` (`p6_live_tui.after.inflight.screen`). The threshold
   compaction inside a turn (`tui-after/threshold.screen`) shows it too, and the
   turn's own retry still shows `Esc to interrupt`.
2. **A split turn's first `auto_retry_end` cleared the countdown and disarmed Esc
   while the other summary was still waiting.** Live (`p6_live_tui.before.split.screen`):
   `Retry succeeded` with no countdown, Esc pressed 3.1 s before the prefix
   summary's next request, which still went out. The TUI now counts sequences
   (opened by an attempt-1 `auto_retry_start`, closed by an `auto_retry_end`) and
   the waits still ahead (`_RetryWaits`): after the fix the countdown stays, Esc
   cancels it (`Retry cancelled`, `compact failed`, no third request;
   `p6_live_tui.after.split.screen`). Its corner - the latest countdown runs out
   while the other sequence's earlier, longer wait still runs - was measured the
   same way: before, `now… Esc to interrupt` and an Esc that let the history
   summary's third request out (`before.corner`); after, the longer wait's
   countdown takes over and Esc ends it (`after.corner`). A failed end still closes
   every countdown: a summary sequence fails only when its compaction does.
   A wait with under 50 ms left counts as over (`_RETRY_WAIT_MARGIN_S`): a
   py<=3.12 Windows clock can wake a countdown up to 15.6 ms early, and two waits
   begun together would otherwise hand the countdown back and forth. **No row pins
   that margin** - it only matters on a Windows clock that wakes early; with it
   removed (`deadline > now`) the split-turn and hand-over rows still pass on macOS
   (`T5_no_margin` survived, `.omc/probes/409-live/r2verify/out/sabotage_r2verify.out`).
   Read it as a reasoned guard, not a tested behaviour.
3. **Three wrong implementations survived every row** (verify, `sabotage.out`):
   C21, one budget shared by a split turn's two requests (the verify kit's entry
   left out the write, so it mutated nothing; with the write it was already red on
   round 1's split-turn row through the attempt numbers); B2, the branch
   summarizer's done-message error not recorded; C22, the policy read once per
   compaction. Each now has a row that goes red: both split-turn summaries retry
   their one `502` under `maxRetries: 1`; a branch summary retries a done-message
   `502`; `retry.enabled` turned off in the settings during the first wait stops the
   next retry.
4. **This amendment overstated round 1's sabotage run** (36 for 38 entries, and a
   branch done-message mutation that was never run): corrected above.
5. **The summary's decision was not the turn's for every error event** (cross F3,
   `p1_error_shape.before.out`): a `502` in pi's event shape (`error=` the message,
   no top-level `error_message`) failed the compaction at once while the turn
   retried it, and an aborted stream (`stop_reason: "aborted"`, text
   `terminated`) was retried while the turn never retries one. The summarizers
   now record the message itself and the decision runs the turn's classifier on
   it (Classification, above); the events carry that message's text. Rows run the
   turn and the compaction on the same event shape and require the same decision.
   The text a request that is not retried raises is unchanged.
6. **A 0 ms wait that does not yield survived** (cross F4, mutant `zero_no_yield`):
   rows with `retry.baseDelayMs: 0` now abort from an `auto_retry_start`
   subscriber (`abort`, `abort_retry`, `ctx.abort()`) and require one request and
   `Retry cancelled`; under the mutant the abort is lost (`p4_zero.before.out`).

Rows added: `tests/test_summary_retry_409.py` 35 → 44 (budget, branch done
message, settings read at each failure, three error-event shapes, three 0 ms
aborts); `tests/tui/test_run_tui_smoke.py` gains eight (the in-flight line in four
states, the label helper, a split turn's kept countdown, the hand-over to a longer
wait, a failed end closing every countdown), and round 1's `agent_start` hand-back
row now runs its turn inside the turn wrapper, as a turn's retry does. On round
1's commit the new rows that pin a changed behaviour fail - two core rows
(`pi-shaped-502`, `aborted`) and six TUI rows; the others pass there and go red
only under their mutation (`red_on_round1.*.out`).

Sabotage, round 2 (`sabotage_r2.py`, a throwaway worktree, each mutation alone): 61
mutations - the verify kit's 48 entries (its report counts 47) re-targeted where the
code moved, C21 with its write, plus thirteen for this round (round 1's
classification from the text, the events' text from the raise, a 0 ms wait that does
not yield, round 1's record of the top-level text, a budget shared with per-request
attempt numbers, the in-flight line always, whenever a turn runs, or never promising
Esc, an end never keeping another sequence's countdown, the open-sequence guard
dropped, every start opening a sequence, a countdown never handing over, a failed
end closing one sequence only). All 61 went red; none survived (`sabotage_r2.out`).
The 50 ms wait margin was not among them; the verify pass's own run removed it and
the rows stayed green (item 2, above).
