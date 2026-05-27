# 0119. TUI Steer / Queue-While-Running

Status: Accepted (W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

The harness already exposed the full steer/follow-up queue (`steer()`,
`follow_up()`, `pending_message_count`, `set_steering_mode`; default
`one-at-a-time`) but the TUI never surfaced it — input was disabled during a
turn ("rails but no train"). This is a P0 interactive-agent UX gap vs pi, whose
input editor stays live during a turn (Enter → steer mid-turn, Alt+Enter →
follow-up after turn). Steering/queuing is built into pi core (no extension);
this sprint is the **pure TUI consumer**, modeled on pi `interactive-mode.ts`.

## The problem

`shell.py::_input_loop` is serialized: it `await harness.prompt(...)` for the
whole turn, so it cannot read input during a turn — and chrome's Enter binding
was gated `filter=not self._running`. So a steering submit must **bypass the
blocked loop** and call `steer()`/`follow_up()` concurrently, mirroring the
existing `on_interrupt` callback pattern.

## The decisions (pure `tui/` consumer)

- **chrome.py**: Enter binding un-gated (fires while running). Two new callbacks
  next to `on_interrupt`: `on_steer` / `on_follow_up`. Enter while running +
  non-empty + `on_steer` wired → echo to history, call `on_steer(text)`, return
  (does NOT touch the serialized `_input_queue`); idle Enter unchanged. New
  `escape,enter` (Alt+Enter) binding → `on_follow_up` while running; idle no-op.
  Completion-confirm + `c-d`/`c-c`/esc-interrupt bindings unchanged.
- **shell.py**: wires `on_steer`/`on_follow_up` to a fire-and-forget `_enqueue`
  helper — echoes `Steering: …` / `Follow-up: …` into the transcript, runs
  `harness.steer/follow_up` via `loop.create_task` (held in a strong-ref set to
  avoid GC), surfaces failures as a red commit, and refreshes the footer. Also
  refreshes the footer on `turn_end` so the queued count drains as messages are
  consumed.
- **context.py**: `_refresh_footer` gains a `⋯ {n} queued` segment (when n>0)
  via a `pending_provider` callback wired to `harness.pending_message_count`.

## Consequences

- **Live-verified** (PTY, qwen3.6): during a multi-tool turn, typing a steering
  message + Enter echoed `Steering: …`, was injected mid-turn, and the final
  answer incorporated it — 0 errors, no crash. The `⋯ N queued` segment is
  transient under `one-at-a-time` (a steer drains on the next roundtrip);
  follow-ups (delivered after the turn) keep it visible longer.
- pi-faithful consumer pattern (Enter=steer / Alt+Enter=follow-up); harness
  unchanged.
- Deferred polish: dequeue/edit-queue keybinding (pi `handleDequeue`); a
  per-message list panel (we show a count, pi lists each line).

## Verification

- ruff clean; pyright 0 errors on chrome/shell/context; full pytest 2954 pass /
  1 skip (+5 tests: Enter-while-running steers / idle Enter still queues /
  Alt+Enter follows-up / idle Alt+Enter no-op / footer `⋯ N queued`); protected
  paths byte-unchanged (changes confined to `tui/`).
- Live PTY: mid-turn steer injected + answered.
