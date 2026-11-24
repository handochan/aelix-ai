# 0124. Sprint 6h₁₆ — TUI /copy + /session + /name

Status: Accepted (6h₁₆ shipped). Amended 2026-11-24 (#911) for unnamed sessions and lookup errors.
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

## Note (2026-11-24, #911)

On base `9227275b`, the 120×40 TUI showed `foo` in both commands after a
rename and reopen. Both resume pickers showed `unnamed first prompt` for the
unnamed conversation, while `/name` printed `(unset)` and `/session` omitted
its name row. When the probe made the name getter raise, `/session` dropped
the row and `/name` printed the exception.

For an unnamed session, both commands label the first displayable user text
on the active branch as a first prompt preview. They show `(unset)` when
that branch has no displayable user text. The JSONL reopen tests in
`tests/tui/test_commands.py` cover a sibling branch, a BEL-only message and
a 70,000-character extension entry. The lookup-error tests and the 120×40
error probe cover a failing name getter.

Pi at `d4760131efe0740b9b7f7ad1f126734b96a011c0` reads the latest saved name
in both commands and uses the first user message as the picker fallback.
Aelix's two lookup commands read the active branch of the open session.
The resume pickers derive their labels from session-file metadata. [Pi commands](https://github.com/earendil-works/pi/blob/d4760131efe0740b9b7f7ad1f126734b96a011c0/packages/coding-agent/src/modes/interactive/interactive-mode.ts#L6919) · [Pi picker](https://github.com/earendil-works/pi/blob/d4760131efe0740b9b7f7ad1f126734b96a011c0/packages/coding-agent/src/modes/interactive/components/session-selector.ts#L474)
