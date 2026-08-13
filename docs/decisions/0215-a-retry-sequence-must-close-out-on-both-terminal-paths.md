# 0215. A retry sequence must close out on both terminal paths, and Esc must mean something once the request is in flight

Status: Accepted (2026-08-13).
Date: 2026-08-13
Relates: ADR-0128 (the auto-retry loop port), ADR-0130 (the TUI retry countdown + Esc handler swap).
ADR-0197 (the band rule — a kernel edit that adds no delegation surface is authorised by exception,
naming its ADR). ADR-0214 (the previous kernel-adjacent fix in this series).
GitHub: #147.

**Provenance.** Half parity restoration, half aelix-original, and the split is stated per change
below because it decides what is allowed to differ from pi.

---

## The problem

#147 reported that a turn cannot be interrupted while an auto-retry request is in flight: Ctrl+C
and Esc are no-ops and the TUI sits at "Working…" with no way out. Verification at `c0f905a`
reproduced that, and found a **second, worse defect underneath it** that the issue does not
mention.

### Defect A — a retry ending in a non-retryable error emits nothing, permanently breaking Esc

pi emits `auto_retry_end` on **both** terminal paths:

| path | pi |
|---|---|
| terminal success (`stopReason !== "error"`) | `agent-session.ts:671-678` |
| terminal error (`stopReason === "error" && _retryAttempt > 0`) | `agent-session.ts:1088-1096` |

Aelix ported only the first. So a retry sequence that engaged and then hit a **non-retryable**
error emitted a start with no end. Measured:

```
_run calls              : 2
auto_retry_start emitted: 1
auto_retry_end   emitted: 0
h._retry_attempt        : 1     (must be 0 between turns)
```

That silence is load-bearing, because `auto_retry_end` is the **only** event
`tui/shell.py::_end_retry_countdown` listens to, and that function holds the only line restoring
`out_chrome.on_interrupt` after `_start_retry_countdown` swapped it for a handler calling
`abort_retry()` — documented *"No-op when no retry is in flight"*. Consequences, all from one
missing emit:

1. **Esc and Ctrl+C stay wired to a no-op for the rest of the session**, including later turns
   that contain no retry at all. The stop button on an agent that runs `bash` and edits files is
   not a thing that may be losable.
2. `_retry_attempt` leaks, so the next turn's first retryable error resumes mid-sequence.
3. The "Retrying (N/M)…" widget never clears.

The triggering sequence is mundane, which is the point: a rate limit (retryable, engages the
loop) followed by an expired token (not retryable, breaks it).

### Defect B — Esc is inert for the duration of the retry request

`_start_retry_countdown` swaps the interrupt handler for the **entire** retry lifecycle, but
`abort_retry()` only wakes the backoff sleep. Once the backoff ends and the request goes out,
Esc calls a function with nothing to cancel. This is the symptom #147 actually reported.

## The decision

### 1. Emit `auto_retry_end` on the error-terminal path — parity restoration

`harness/core.py` now closes out the sequence whenever a terminal assistant exists, carrying
`success` from `stop_reason` and `final_error` from the message. This is pi's `:1088-1096` arm,
ported. The counter is reset **before** the emit so a subscriber that prompts again synchronously
cannot observe the stale value.

**Kernel edit.** `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` is already in
`_KERNEL_CHANGE_ALLOWLIST`; this ADR is the authorisation for this entry's newest change. The band
rule asks one question — *did delegation create this requirement?* It did not: the requirement is
pi parity for an event the kernel already emits on two other paths, it adds no import, no symbol,
no spawn site, no capability, no consent path and no registry, and
`test_kernel_has_no_subagent_surface` is unaffected.

### 2. Hand the interrupt back once the backoff ends — aelix-original

The countdown ticker already knows the moment the sleep completes and the request goes out, so
that is where `out_chrome.on_interrupt` is restored to the full-turn abort. Esc during the
countdown still cancels the retry (pi semantics, ADR-0130); Esc during the request now aborts the
turn.

**pi has the same gap** (`interactive-mode.ts:3345-3350` swaps `onEscape` for the whole lifecycle
and restores it only on `auto_retry_end`), so this is a deliberate divergence, not restoration.
It is justified because the alternative is a key the UI advertises and that does nothing: the
banner read `Esc to cancel` throughout. It now reads `Esc to interrupt` after the countdown, so
the label matches the action at every moment.

`_end_retry_countdown` restores the same handler idempotently, so a retry ending during the
countdown is unaffected, and a next attempt re-swaps via `_start_retry_countdown`.

### 3. `"aborted"` is not a success — found by the live gate

The terminal-success arm compared `stop_reason != "error"`, and an aborted turn's assistant
carries `stop_reason == "aborted"`. So interrupting a retry emitted `success=True` and the TUI
committed a green `✓ Retry succeeded (attempt 1)` directly beneath the user's own
`✖ Operation aborted`. The comparison predates #147 — the old success-only arm used it too — but
this change is what made the path reachable often enough to observe, and a fix that congratulates
the user for cancelling would be worse than the defect it replaces.

## How this was verified, including the rig that lied

The live gate is the point of this ADR as much as the fix, because **the first rig passed against
the broken build**.

That rig failed attempt 1 with an HTTP 500. The retry UI never appeared, the pane showed a plain
`Working…`, and Esc aborted the turn on the pre-fix build too — a clean false green. The cause was
measured, not guessed: the `openai` SDK carries `DEFAULT_MAX_RETRIES = 2` and the adapter passes
`max_retries` at client level, so **the SDK absorbs 5xx before aelix's retry loop ever sees an
error**. The two requests the stub counted were the SDK retrying itself.

The trigger that actually reaches aelix's loop is an error the SDK does *not* retry but
`_RETRYABLE_ERROR_PATTERN` does — a 200 whose SSE body ends with no `finish_reason`. With that,
the precondition is real and the control separates the builds cleanly:

```
pre-fix                                   fixed
⟳ Retrying (1/3) now…                     ⟳ Retrying (1/3) now… Esc to interrupt
⠧ Working… (2s · esc to cancel)           ✖ Operation aborted
⠇ Working… (9s · esc to cancel)   ← Esc   ✖ Retry failed: cancelled
FAIL — turn did not abort                 PASS — turn aborted
```

The lesson worth keeping: a live gate that passes proves nothing until the same gate has been run
against the unfixed build. Only the control revealed that the first rig never created the
precondition at all.

## Consequences

- Every `auto_retry_start` is answered by exactly one `auto_retry_end`. The pairing is now the
  invariant the TUI's handler restoration depends on, and it is pinned by a test that was seen
  red against the pre-fix build.
- A companion test pins the negative: a non-retryable error with **no** prior retry must emit
  nothing, or a fix that emitted on every terminal error would commit a spurious "✖ Retry failed"
  on ordinary failures.
- Not addressed here, and worth stating: an in-flight retry request is aborted by the normal turn
  abort, which is a cooperative cancel — it does not forcibly kill a socket the provider is
  holding open. #147's original repro used a proxy that HOLDS the connection; the turn now aborts,
  but a provider that never returns still ties up the request until the transport gives up.
