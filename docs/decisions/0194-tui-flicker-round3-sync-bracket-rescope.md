# 0194. TUI flicker round 3 — CSI 2026 bracket re-scope + atomic tail handoff

Status: Accepted
Date: 2026-07-14

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**
(This subsystem is aelix-native — pi's TUI is a full differential renderer with
no suspend cycle, so there is no upstream behavior to mirror; the binding
principle applies as "no user-visible artifact pi's renderer would not have".)

## Context

The user still reported per-turn flicker ("깜빡거리고 창이 흔들림") during Rich
streaming, after two prior mitigation rounds:

- **Round 1 (6h₂₄):** `_output_pump` batches consecutive commits into one
  `print_above_many` → one `in_terminal()` suspend per drain.
- **Round 2 (6h₂₄ v2 + 6h₂₅/ADR-0153 WP-9):** `live_window` 6→12 + 10 FPS tail
  floor, and a CSI 2026 Begin/End Synchronized Update bracket around the
  scrollback write.

Root-cause analysis (4-reader workflow + firsthand byte capture) found the WP-9
bracket was **mis-scoped**. `prompt_toolkit`'s `in_terminal` erases the chrome
in `__aenter__` (`renderer.erase()`, `run_in_terminal.py:96`) and repaints it in
the `__aexit__` finally (`app._redraw()`, `:110-112`) — both **outside** the
old bracket, which lived *inside* the block. A supporting terminal therefore
still painted three frames per commit batch: chrome-gone (unsynced), scrollback
write (synced), chrome-back (unsynced) — the blink. Differential byte capture
against a real `Vt100_Output` confirmed it: pre-fix order was
`erase < ?2026h < payload < ?2026l < redraw`.

A second artifact rode on the tail handoff: the pump applied tail updates
*outside* the suspend, so a stable-line commit (or the final commit + `""`
clear) painted as **two** frames — the finalized text briefly visible twice
(scrollback + the still-populated `__stream__` window) before the window
collapsed.

The user's own proposal — hide the input row while the final answer renders —
was assessed (advocate/skeptic panel) as a symptom-level aid: it shrinks the
blink area but cannot remove the blink, contradicts the mid-turn steer/queue
affordance, and is unnecessary if the frame itself becomes atomic. Deferred;
re-evaluate only if artifacts persist in live use.

## Decision

Two root fixes, no layout/UX change:

### A — re-scope the CSI 2026 bracket (chrome.py)

`print_above` / `print_above_many` now open `_sync_update(True)` **before**
`async with in_terminal()` and close in a `finally` **after** it exits. Safe
because `Application._redraw` renders synchronously (its bytes flush before
`__aexit__` returns) and terminals auto-release a stale 2026 bracket on a short
timeout (kitty/xterm.js/WezTerm ~1 s), so an exception inside cannot wedge the
screen. Post-fix byte order (verified end-to-end against a real Vt100 output
with the real `in_terminal`): `?2026h < erase < payload < chrome-redraw <
?2026l` — the whole suspend cycle is one painted frame.

Because the bracket now opens *before* joining the run-in-terminal
serialization chain, two concurrent callers (the pump + a descriptor's
fire-and-forget `print_above`, descriptors.py) can overlap brackets — and DEC
2026 is a boolean mode, so the first closer would strip the waiter's bracket
mid-suspend (adversarial-review finding). `_sync_update` is therefore
depth-counted: `h` only on 0→1, `l` only on 1→0, underflow never emits a
stray `l` — overlapping brackets merge into one synchronized span.

### B — atomic tail handoff (chrome.py + shell.py pump)

`print_above_many` grew keyword-only `apply_before_redraw: Callable[[], None]
| None` — runs inside the suspend, after the prints, before the exit repaint
(empty-batch path still runs it synchronously; it must never be silently
dropped). The pump folds the drain's **last** tail into the batch through it:

- Commits coalesce **across** interleaved tails (previously each tail split the
  batch → extra suspends). Tails are full-window replacements and nothing
  paints mid-drain, so intermediate tails are dead states; commit order is
  preserved, scrollback content is unchanged.
- The stable-line handoff and the final commit+clear now paint as **one**
  frame: text lands in scrollback and leaves the live window in the same
  repaint. The pre-existing queue-ordering guarantee ("a tail-clear may never
  paint before its text reaches scrollback", shell.py output-queue seam) now
  holds by construction.
- On flush failure the pump re-applies the tail outside the batch (`set_widget`
  is an idempotent full replacement) — preserving the old contract where a
  failed flush could not strand a stale window; the pump still never dies.

## Consequences

- On DEC-2026 terminals (VS Code/xterm.js, kitty, iTerm2 ≥3.5, WezTerm,
  Windows Terminal ≥1.18, Alacritty ≥0.13) the per-batch chrome blink is gone;
  non-supporting terminals ignore the sequences and behave exactly as before.
- `_spy_commits` in the smoke tests forwards `**kwargs` — any future wrapper
  around `print_above_many` must stay signature-transparent or the pump's
  never-die handler will silently swallow the `TypeError` and drop commits.
- New tests pin the frame order with a recording `in_terminal` stand-in
  (`?2026h < erase < writes < apply < redraw < ?2026l`), the pump's
  coalesce/last-tail/fallback contract (the fold-in is mutation-pinned: the
  fake records whether the hook was passed, so a mutant applying the tail
  after the flush fails — verified by applying and killing the mutant), the
  `finally`-closes-bracket guarantee under print/hook failure, and the depth
  counter (unit + two overlapping `print_above` tasks → one merged bracket).
  Scratchpad-verified differentially against a real Vt100 output (old code
  fails the frame-order check, new code passes).
- Remaining (accepted) motion: per-line tail growth (natural scrolling, same
  as any streaming CLI) and PT diff-render passes, which are small and not
  suspend-driven. The input-hide idea and a fixed-height stream window stay
  in the backlog as opt-in polish if live use still shows artifacts.
- Known residual (documented, not fixed here): `in_terminal.__aexit__` calls
  `renderer.reset()`, which zeroes `_min_available_height`, so the exit redraw
  renders with `renderer_height_is_known` False — the CPR-gated rows (working
  row + spacer, status, footer) are absent from the atomic frame and pop back
  one CPR round-trip later, in a render pass outside the bracket. On local
  terminals the gap (~1-5 ms) usually coalesces below the display refresh and
  is invisible; over high-latency SSH it can read as a footer blink per commit
  batch. Fixing it needs either holding the bracket across the CPR wait or
  seeding a height estimate through the reset — both riskier than the artifact;
  revisit only if live use still shows it.
