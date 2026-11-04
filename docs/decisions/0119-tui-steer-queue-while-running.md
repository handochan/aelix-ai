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

## Note (2026-11-03, #596): a `!` line Entered during a turn runs instead of steering

*Amended 2026-11-03 by [ADR-0287](0287-the-input-shows-what-enter-will-do-and-a-bang-line-runs-during-a-turn.md) D6.* Enter while running steers every line **except** one
`parse_input_line` would run as a shell command: `!cmd` / `!!cmd` goes to `on_shell`, which runs
it at once and lets the turn go on (pi checks for `!` before its streaming-steer branch). What the
model gets of a `!` command is recorded once nothing runs, so it reaches the model on its next
turn. Alt+Enter is unchanged: it queues any line, a `!` one included, as a follow-up message.

## Note (2026-11-04, #668): the echo lands where the turn takes the line

*Amended 2026-11-04 (#668, the owner's decision (a)).* `_enqueue` echoed `Steering: …` /
`Follow-up: …` into the transcript at Enter. The harness hands a queued line to the model at the
next turn boundary, so the echo landed in the middle of a streamed answer (between the rows
already committed and the live tail) or above a short one, while the model got it, and the session
recorded it, after the answer (measured: `.omc/specs/508-596-followups-evidence/i2/`). Now, as pi
does, the line is drawn in the pending area under the stream (`Steering: …`, ADR-0287 D6) until
its user `message_start` — the loop taking it from the queue — and its echo is committed then. A
line Alt+Up or an interrupt gives back to the input leaves the area with it. This also closes the
deferred "per-message list panel": each queued line is listed, steering lines first as the model
gets them, in the rows the pending area's eight leave (at least three, then "… N more queued"),
with pi's "↳ Alt+Up to edit all queued messages" under them when a row is free (ADR-0287 D6), and
the footer keeps its `⋯ N queued` count.
