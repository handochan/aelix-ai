# 0124. Sprint 6h₁₆ — TUI /copy + /session + /name

Status: Accepted (6h₁₆ shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Continuing the TUI pi-parity audit close-out (user directive: address all
applicable gaps). This sprint adds three command-surface commands pi has that
Aelix lacked — `/copy` (MEDIUM), `/session` + `/name` (LOW) — all clean,
non-protected, with ready harness/session APIs and no new dependencies.

## Decisions (all non-protected `aelix-coding-agent`)

- **OSC 52 clipboard** (`chrome.py::copy_to_clipboard`): writes the terminal-native
  `\x1b]52;c;<base64>\x07` sequence via `app.output.write_raw`+`flush` (the same
  output API `clear()` uses). No clipboard dependency, works over SSH/tmux.
  Best-effort + headless-safe (exception-suppressed; `DummyOutput` swallows).
- **`/copy`** (commands.py): `_last_assistant_text(harness)` walks `harness.messages`
  in reverse for the most recent assistant message with non-empty `TextContent`
  (tool-call-only assistant turns skipped), then `chrome.copy_to_clipboard`. pi
  `/copy` (slash-commands.ts:24) parity. Degrades with a message when there's
  nothing to copy / clipboard unavailable.
- **`/session`** (commands.py): a Rich table of `session.get_metadata()` (id, cwd)
  + `get_session_name()` + `session_file` + `get_session_stats()` (messages,
  tokens, cost). pi `/session` (slash-commands.ts:26) parity.
- **`/name [text]`** (commands.py): no-arg shows `session.get_session_name()`; an
  arg persists via `await session.append_session_name(args)` (core Session
  method). pi `/name` (slash-commands.ts:25) parity.

## Consequences

- ruff clean; pyright 0 errors on the changed source (8-baseline); full pytest
  **3019 passed, 1 skipped** (+ tests: /copy last-assistant + nothing-to-copy,
  /session info+stats, /name show + set-via-append, OSC52 byte-format,
  registry-order lock). Protected core byte-unchanged.
- **Live-verified (PTY, gpt-4o-mini):** `/name ocean-explorer` → set then show;
  `/session` → id/cwd/name/file table; `/copy` after a turn → "Copied last
  message (6 chars) to clipboard."

## Code review (separate lane) — APPROVE-WITH-NITS

`code-reviewer`: 0 CRITICAL / 0 HIGH; protected core byte-unchanged; pyright
clean; pi-parity confirmed (incl. the `/name` append → last-write-wins semantics,
verbatim pi `session.ts:118-121`); OSC 52 sequence + `_last_assistant_text` +
async session methods all verified correct. Nits: [L2] `import base64` moved to
module top (applied); [L1] `/session`'s per-section `contextlib.suppress`
(silent partial-degrade vs `/cost`'s red ✖) and [L3] no OSC 52 payload-size
guard — both consciously LEFT (pi-parity-defensible; pi guards neither, and a
missing optional section shouldn't blank the whole `/session` panel).

## Audit roadmap (remaining)

HIGH: #3 auto-compaction trigger (PROTECTED-CORE — needs explicit approval).
MEDIUM: #4 image-paste (Ctrl+V), #6 model-picker UI (Ctrl+L), #7 /settings menu,
#8 auto-retry+countdown. LOW applicable: /tree, /fork, /clone, /import, Ctrl+G
external editor, double-escape, /skill:<name>. N/A for Aelix's model (not
cloned): /changelog, /scoped-models, /login·/logout, /share.
