# 0123. Sprint 6h₁₅ — TUI session lifecycle + thinking collapse (/new · Ctrl+T · /hotkeys · Alt+Up)

Status: Accepted (6h₁₅ shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

The TUI pi-parity audit (after the 6h₁₄ P1 bundle) confirmed the core interactive
loop is at parity and surfaced a fresh, evidence-based gap list vs pinned pi. This
sprint closes the top cohesive cluster: 2 HIGH (`/new`, thinking collapse) + 2
MEDIUM (`/hotkeys`, Alt+Up dequeue). All grounded against pi source; all in the
non-protected `aelix-coding-agent` TUI layer (protected core byte-unchanged).

## Decisions

### 1. `/new` — start a fresh session (HIGH)

pi `/new` (slash-commands.ts:34) starts a new session in-process. Aelix's runtime
already had `new_session()` (parity API) but no TUI command. `tui/commands.py`
adds `/new` → `CommandContext.new_session`; `shell.py::_new_session` mirrors
`_resume_session` (mid-turn guard → `runtime.new_session()` → `chrome.clear()` +
fresh banner), no picker/replay (the new session is empty). The `_rebind` seam
(ADR-0122) re-subscribes the renderer + refreshes `command_ctx.harness`, so
post-`/new` commands act on the new session.

### 2. Thinking-block collapse + Ctrl+T (HIGH)

pi renders thinking collapsed behind an italic `"Thinking..."` placeholder
(assistant-message.ts:101) when `hideThinkingBlock` is set; `Ctrl+T`
(app.thinking.toggle, interactive-mode.ts:3482) flips the flag, persists it, and
**rebuilds the whole chat** to retroactively toggle past blocks.

`EventRenderer.hide_thinking` (default **True** = collapsed) addresses the
reasoning-model transcript-flood complaint (qwen3.6). When collapsed,
`_flush_thinking` stashes the full reasoning in the **/expand store** and commits
a `💭 Thinking… (/expand N)` one-liner — so the reasoning stays recoverable.
Ctrl+T (chrome `c-t` → `run_tui` flips `renderer.hide_thinking` + a status line).

**pi divergence (documented):** pi's Ctrl+T re-renders past blocks by rebuilding
its component tree; Aelix's inline native scrollback is immutable once printed, so
Ctrl+T affects only SUBSEQUENT thinking blocks. The mitigation (collapsed blocks
are /expand-recoverable) means no reasoning is lost — arguably better than pi for
the inline model.

### 3. `/hotkeys` (MEDIUM)

pi `/hotkeys` (slash-commands.ts:28) shows a keybinding table in chat. Aelix adds
`/hotkeys` → a Rich table of the ACTUAL chrome bindings (`_HOTKEYS`, kept next to
the registry so it can't drift): Enter, `\`+Enter, Alt+Enter, Alt+↑, Ctrl+T, Esc,
Ctrl+C, Ctrl+D, Tab, `@path`, `!`/`!!`, emacs editor keys, history. (Aelix keeps
`/help` for commands — pi has no `/help`; the two are complementary.)

### 4. Alt+Up dequeue (MEDIUM)

pi `app.message.dequeue` (interactive-mode.ts:3665) drains the steer + follow-up
queues, joins `[...steering, ...followUp]` with `"\n\n"`, appends the current
editor text, and `setText`s the combined string. Aelix mirrors this: chrome binds
`Alt+Up` (`escape,up`) → `on_dequeue`; `shell.py` reads/clears the harness queues
and restores the text (steer first, then follow-up, `"\n\n"`-joined, current
editor text appended).

**Private-access note:** the harness exposes `pending_message_count` but no public
queue-drain, so `_dequeue` reads `harness._steering_queue._messages` /
`_follow_up_queue._messages` and calls `.clear()` — the same TUI-host private
coupling pattern as `runtime._repo` (ADR-0122), documented inline. (A public
`AgentHarness.take_pending_messages()` would be a protected-core change; deferred.)

## Consequences

- ruff clean; pyright 0 errors on the 4 changed source files (8-baseline); full
  pytest **3012 passed, 1 skipped** (+ tests: /new available/invoked/failure,
  /hotkeys table, thinking collapsed-default+/expand-recover + toggled-visible,
  Ctrl+T + Alt+Up bindings fire, run_tui /new wired + Alt+Up restores+clears).
  Protected core (`packages/aelix-agent-core`, `docs/contracts`) byte-unchanged.
- **Live-verified (PTY):** `/hotkeys` shows the shortcut table; `Ctrl+T` →
  "💭 Thinking blocks: visible"; `/new` clears + shows a fresh banner; Alt+Enter
  follow-up queues ("Follow-up: …") and Alt+Up is wired (the binding fires;
  dequeue restore+clear is deterministically covered by the smoke test). The
  collapse rendering is unit-verified (a reasoning model didn't emit a separate
  thinking channel on the sampled live turn).

## Code review (separate lane) — APPROVE-WITH-NITS → fixes applied

`code-reviewer`: 0 CRITICAL / 0 HIGH; protected core byte-unchanged; /hotkeys
table accuracy, Ctrl+T/`escape,up` non-collision, the thinking-collapse divergence,
the private-queue coupling, and the `/new` flow all verified and cleared. Fixes:

- **[MEDIUM]** Stale `/expand` ids survived a session swap (the long-lived
  `EventRenderer` store was never reset; this sprint widened it by routing
  collapsed reasoning through the same store) → after `/new`/`/resume`,
  `/expand N` could surface the PRIOR session's body. FIXED:
  `EventRenderer.reset_expand_store()` (clears store + seq + `_thinking_flushed`),
  called from the `_rebind` seam so it fires on every swap (fixes `/resume` too).
- **[LOW]** Alt+Up `_dequeue` is intentionally ungated (pi best-effort parity);
  documented that its safety against the in-turn queue drain rests on the body
  being await-free.

## Audit roadmap (remaining gaps — to be addressed next, per user)

HIGH: #3 auto-compaction trigger (flag exists, threshold trigger unwired —
ADR-0117; summarizer ready; protected-core touch). MEDIUM: #4 image paste
(Ctrl+V), #6 model picker UI (Ctrl+L/Ctrl+P), #7 `/settings` menu, #8 auto-retry
w/ countdown, #10 `/copy`. LOW: /session, /name, /tree, /fork, /clone, /import,
/share, /changelog, /scoped-models, /login·/logout, Ctrl+G external editor,
double-escape, version notifications, /skill:<name>.
