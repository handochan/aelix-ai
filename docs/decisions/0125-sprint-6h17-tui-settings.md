# 0125. Sprint 6h₁₇ — TUI /settings live-toggle menu

Status: Accepted (6h₁₇ shipped)
Date: 2026-05-28
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Continuing the TUI pi-parity audit close-out. pi's `/settings` (slash-commands.ts:18,
the first registered command) opens a persisted settings menu with ~24 toggles.
Aelix has **no SettingsManager** in the coding-agent layer, so a full persisted
clone is out of scope; this ships the pi-faithful, achievable subset: a
**session-scoped live-toggle menu** over the options that are actually settable
on the running harness/renderer. Non-protected; reuses already-verified setters.

## Decisions (all non-protected `aelix-coding-agent`)

- **`/settings`** (commands.py) → `CommandContext.settings_action`, wired in
  `shell.py::_open_settings`. A 1-level `context.select` menu showing the current
  value of four options; picking a row TOGGLES (2-value) or CYCLES (thinking
  level) it, then `context._refresh_footer()`:
  - **Steering mode** — `set_steering_mode` (one-at-a-time ⇄ all); footer ⏵⏵
    segment reflects it live.
  - **Follow-up mode** — `set_follow_up_mode` (one-at-a-time ⇄ all). NEW: there
    was no other TUI way to set this.
  - **Thinking blocks** — toggles the renderer's `hide_thinking` flag (same state
    as Ctrl+T, ADR-0123).
  - **Thinking level** — `set_thinking_level`, cycles off→low→medium→high.
- **Auto-compaction intentionally OMITTED.** Its `auto_compaction_enabled` flag is
  settable, but the threshold *trigger* is unwired (audit #3) — a toggle for a
  no-op would mislead. It returns to /settings once #3 lands.
- **Fits the `ctx.select` 9-cap** (4 fixed options) — unlike a model picker over
  hundreds of OpenRouter models, which is why #6 was not chosen here.

## Built-in vs descriptor name clash

`/settings` is now a built-in; the input loop dispatches built-ins BEFORE
descriptor management-modals (ADR-0110: built-in wins on a name clash). So a
descriptor that registered a `/settings` modal is shadowed — correct, documented
behavior. Two tests that used `/settings` as their *example* descriptor-modal
command were renamed to non-built-in names (`/deploy`, `/panel`); the descriptor
management-modal feature itself is unchanged.

## Consequences

- ruff clean; pyright 0 errors on the changed source (8-baseline); full pytest
  green (+ tests: /settings delegation available/invoked/failure; orchestration
  smoke — select #1 toggles steering to "all", select #4 cycles thinking off→low;
  registry-order lock; the two renamed descriptor-modal tests). Protected core
  byte-unchanged.
- **Live-verified (PTY, gpt-4o-mini):** `/settings` → menu of 4 rows with current
  values ("Thinking blocks: hidden" confirms the 6h₁₅ default); #1 → "steering
  mode → all" + footer "⏵⏵ all"; #4 → "thinking level → low".

## Code review (separate lane) — APPROVE-WITH-NITS → fixes applied

`code-reviewer`: 0 CRITICAL / 0 HIGH; protected core byte-unchanged; pyright clean;
the auto-compaction exclusion, the built-in-shadows-descriptor handling (+ test
renames), private `_state.thinking_level` access, and footer-refresh all verified
correct. The two MEDIUMs shared one root fix:

- **[M1]** the hardcoded `["off","low","medium","high"]` cycle diverged from the
  canonical model-aware cycle (would advance a non-reasoning model to a level it
  can't honor; omitted `minimal`/`xhigh`). **[M2]** `set_thinking_level("off")`
  persisted the literal `"off"` vs the codebase's `None` sentinel. FIXED: the
  thinking-level row now delegates to `harness.cycle_thinking_level()` — model-aware
  supported-levels rotation, no-op (→ "no thinking levels to cycle") on a
  non-reasoning model, canonical off/None handling. Full pi-parity.
- **[LOW]** footer-refresh is a no-op for the non-steering options (no segment —
  documented); stale-display race not reachable (modal blocks the loop). No change.

## Audit roadmap (remaining)

HIGH: #3 auto-compaction trigger (PROTECTED-CORE — needs explicit approval; would
also re-enable an auto-compaction row in /settings). MEDIUM: #4 image-paste
(Ctrl+V, needs a clipboard-image read), #6 model-picker (needs a real fuzzy
picker, not the 9-cap select), #8 auto-retry+countdown. LOW applicable: /tree,
/fork, /clone, /import, Ctrl+G, double-escape, /skill:<name>.
