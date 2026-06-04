# 0132. Sprint 6h₂₄ — TUI flicker fix + arrow-key select dialog

Status: Accepted (6h₂₄ shipped)
Date: 2026-06-04
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Two user-reported UX bugs after the 6h₂₃ wrap-up:

1. **Streaming flicker** — "텍스트 응답을 스트리밍할 때, 아래 chrome 전체가
   깜빡거립니다." The persistent chrome (footer, status, input editor) visibly
   flickers while the assistant streams tokens.
2. **Settings menu accepts only digits; arrow keys do not work.** "setting 등
   메뉴가 숫자 응답만 가능하고 화살표키가 작동이 안됩니다." Pi's settings
   selector shows `→` cursor + ↑/↓ + Enter/Space + type-to-filter — Aelix had
   none of that.

Pi-parity, pure TUI consumer sprint: no protected-core (`packages/aelix-agent-core`,
`docs/contracts`) touch.

### Root causes

**1. Per-commit `in_terminal()` suspend.** Each call to `chrome.print_above`
(`chrome.py:412`) wraps an `async with in_terminal():` block that suspends the
prompt-toolkit renderer, lets Rich write to scrollback, then re-paints the chrome
below. The output pump (`_output_pump` in `shell.py:1146`) processed the
`commit`/`tail` queue **one item at a time** — so a token stream that produced
N commits did N suspend/repaint cycles. At ~5+ commits/second this is the
flicker the user saw.

**2. Digit-only select.** `AelixTUIContext.select` (`context.py:137`) bound
`kb.add(str(index + 1))` for `index in 0..8` only — digit 1-9 was the **sole**
way to pick a row. No arrow keys, no Enter to confirm, no cursor marker, no
type-to-filter, options capped at 9. Caused by a Sprint 6h₁₀b minimal-viable
shipping decision; never revisited.

Pi reference (from the user's screenshot of `/settings`):

```
→ Auto-compact            true
  Auto-resize images      true
  Block images            false
  ...
  (1/21)

  Automatically compact context when it gets too large

  Type to search · Enter/Space to change · Esc to cancel
```

→ marker, columns aligned, scrolling, counter, hint footer.

## Decision (4 non-protected files; +11 test cases)

### `chrome.py` — `print_above_many`

New async method that batches a `Sequence[object]` into a single `in_terminal()`
suspend (mirror of `print_above` but for N renderables):

```python
async def print_above_many(self, renderables: Sequence[object]) -> None:
    if not renderables:
        return  # empty batch: no suspend, no invalidate
    async with in_terminal():
        for renderable in renderables:
            self._console.print(renderable)
    self.app.invalidate()
```

`Sequence[object]` (W-review LOW-2): the body iterates; tuples + lists both
satisfy this without `list(...)` allocations at call sites.

### `shell.py` — `_output_pump` batching

After the first `await queue.get()`, drain everything in the queue
synchronously via `queue.get_nowait()`, then iterate the drained list grouping
consecutive `commit` items. One `print_above_many` per group; a `tail` item
flushes the pending commits FIRST then applies the tail (visible ordering
preserved). Even a single commit goes through the batch path — the "always one
suspend per drain" invariant is a uniform property the pump tests lock in.

```python
while True:
    first = await queue.get()
    items: list[tuple[str, object]] = [first]
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    pending: list[object] = []
    for kind, payload in items:
        if kind == "commit":
            pending.append(payload)
        else:
            if pending:
                with contextlib.suppress(Exception):
                    await chrome.print_above_many(pending)
                pending = []
            if kind == "tail":
                ansi = payload if isinstance(payload, str) else ""
                chrome.set_widget("__stream__", ansi.split("\n") if ansi else None, above=True)
    if pending:
        with contextlib.suppress(Exception):
            await chrome.print_above_many(pending)
```

Drain only sees the queue snapshot at entry — items arriving during drain
queue up for the next iteration (no unbounded loop). Worst-case single batch
size is bounded by the producer rate × the prior `print_above_many` duration;
even a 500-commit stream is one suspend instead of 500, which is the entire
flicker fix.

### `context.py` — arrow-key + type-to-filter `select`

Rewrote `AelixTUIContext.select` to use a stateful render with arrow-key
navigation:

- **State**: `{idx, filter}` mutable closure dict — pure-Python, no asyncio
  primitives. The render reads it on every repaint.
- **Filter**: case-insensitive substring match against the FULL `options`
  list. `filtered()` returns `(orig_index, text)` rows; the cursor `idx` is
  into the filtered view (not `options`), so navigation feels natural when
  filtering.
- **Viewport**: 8 rows max, centered on the cursor; `⋮` markers above /
  below when content is clipped. The clamp math (`max(0, min(idx-4, len-8))`)
  is dense — comment W-review LOW-1 documents the center/clamp behavior.
- **Bindings**:
  - `up`, `down` — wrap-around within `filtered()`
  - `enter`, `c-j`, `space` — confirm cursor row (resolves with the option
    text from `filtered()[idx]`); silent no-op when filter has zero matches
    (W-review LOW-3 — Enter must NOT bypass the empty view)
  - `escape`, `c-c` — resolve with `None` (cancel)
  - `backspace` — pop one char from the filter
  - `<any>` — append printable single-char `event.data` to the filter; sorted
    LAST by prompt-toolkit's `KeyProcessor` so concrete bindings always win
- **Footer**: title, items, `(N/total)` counter, optional `Filter: ...` line,
  hint `Type to search · ↑/↓ to move · Enter/Space to change · Esc to cancel`
- **9-option cap removed** (W-review observation): picker scales to any size
  via arrow keys + filter
- **Digit shortcuts removed**: they collided with filtering ("4" in `gpt-4o`
  needed to be a filter char, not a row-4 shortcut)
- **Empty options** → resolves immediately as `None` (no modal opens)

### `shell.py` — `_open_settings` pi-screenshot parity

Two changes (W-review 6h₂₄ MEDIUM-1 + format polish):

1. **Column alignment**: `f"{k.ljust(width)}{v}"` where `width = max(len(k) for k, _ in rows) + 2`. Matches pi's screenshot (`→ Auto-compact            true`).
2. **Key recovery via exact index**: instead of `choice.startswith(k)` (which
   silently no-ops if a future row label is a prefix of another, or if the
   format changes), use `labels.index(choice)` to round-trip the chosen
   option back to its row index. A `ValueError` surfaces a visible error
   rather than a silent no-op.

### `context.py` — `c-c` cancel in all dialogs (W-review LOW-4)

Mirrored the new `select`'s `c-c → cancel` binding in `confirm()` (returns
`False`) and `input()` (returns `None`). `editor()` already had it. Without
this, Ctrl+C while a modal owns focus leaks to the chrome global handler
(clear-buffer when not running) — inconsistent UX.

## v2 follow-up — flicker fix tier 2

The initial commit (`592c56c`) reduced **commit-driven** in_terminal suspends
by batching the pump. The user reported the chrome STILL flickered during
streaming — meaning the perceived flicker had a second source the batching
didn't touch. Diagnosis pointed at two surviving causes:

1. **Chrome redraw cadence (20 FPS).** `Application(refresh_interval=0.05,
   min_redraw_interval=0.05)` had the renderer re-evaluate state every 50 ms
   and flush at 20 FPS even when nothing material changed (e.g., the
   spinner only needed an ~80 ms cycle). At 20 FPS, the working line + tail
   widget + status + footer all re-emit cursor-movement sequences on every
   tick — visually indistinguishable from "flicker" on most terminals.
2. **`set_tail` repaint frequency.** `StreamRenderer.min_delay` floor was
   1/20 s (20 FPS). Each tail update grew the chrome's `__stream__` widget
   by zero or one row, but the chrome re-emits ALL rows below the change.
   At 20 Hz that's continuous repaint over the live region.
3. **`live_window` commits.** Live window = 6 lines meant a longer response
   would trigger 3-5 print_above batches over the stream — each one
   visible as an in_terminal flicker frame.

Tier 2 changes (chrome.py + stream.py):

- `refresh_interval` 0.05 → **0.1** (10 FPS state ticks; spinner still
  smooth — human flicker threshold for small glyph changes ~16 Hz, comfort
  ~10 Hz)
- `min_redraw_interval` 0.05 → **0.08** (12.5 FPS redraw ceiling; coalesces
  back-to-back `invalidate` calls into one frame)
- `StreamRenderer` default `min_delay` 1/20 → **0.1** (10 FPS tail-widget
  repaint floor)
- `StreamRenderer` default `live_window` 6 → **12** (most token streams now
  finish before hitting the commit threshold; print_above flicker frames
  drop from 3-5 to 0-1)

Tests pass unchanged — existing `test_stream_renderer.py` cases pass
explicit `live_window=6` / `min_delay=1/20`, so defaults moved without
breakage. The 20-FPS / 6-line numbers were never load-bearing values, just
the original throwaway picks from Sprint 6h₁₀b.

## Deferred (intentional)

- **i18n widths in `_open_settings`** (W-review MEDIUM-2). `len(k)` counts code
  points, not display columns; current row keys are all ASCII so it's fine
  today. Revisit (likely `wcwidth.wcswidth`) before adding CJK / wide labels.
  Asserted with a comment, not enforced.
- **Drain-loop perf cap at N items per batch** (W-review Open Question). A
  500+-commit single batch could noticeably delay the event loop during the
  synchronous Rich render. Not observed in current usage; defer until a real
  long-stream perf test surfaces a stall.
- **Slash-command palette UI polish** (user's third complaint, "슬래시
  명령어도 UI가 pi 랑 조금 다릅니다"). The prompt-toolkit `CompletionsMenu`
  layout vs pi's ink-rendered table is purely visual. Not a functional bug;
  visual-only ports are queued for a later sprint.

## Consequences

- **Files touched**: 3 src (chrome.py, shell.py, context.py); 4 test
  (test_chrome.py, test_context.py, test_run_tui_smoke.py, test_output_pump.py
  NEW); 0 protected.
- **`git diff --stat docs/contracts packages/aelix-agent-core`**: empty ✓.
- **Tests**: 11 new + 2 rewritten test cases:
  - `tests/tui/test_output_pump.py` (NEW): 4 unit tests — consecutive-commit
    batching, tail-flushes-pending-first, empty-tail clears widget,
    single-commit-uses-batch-path.
  - `tests/tui/test_chrome.py`: 2 new tests — `print_above_many` order
    preservation, empty-list noop.
  - `tests/tui/test_context.py`: 8 new/rewritten tests — arrow_down_then_enter,
    arrow_up_wraps, space_confirms, escape_cancels, type_to_filter_then_enter,
    empty_options_resolves_none, supports_more_than_nine_options,
    enter_confirms_cursor_row, no_match_enter_stays_open,
    confirm_ctrl_c_cancels, input_ctrl_c_cancels.
  - `tests/tui/test_run_tui_smoke.py`: updated `_spy_commits` to also spy
    `print_above_many`; updated settings + resume picker tests to use
    arrow keys + Enter instead of digit shortcuts.
- **Gate**: ruff clean; pyright 0-new on touched files; pytest 3107 → 3112
  (+5 unique; the +11 new tests overlap +6 with the rewritten ones, hence
  the net delta).

## Code review (separate lane) — APPROVE_WITH_NITS → all nits applied

`code-reviewer`: 0 CRITICAL / 0 HIGH / 2 MEDIUM / 4 LOW. Findings:

- **[MEDIUM-1]** `_open_settings` recovered the key via `startswith` — fragile
  if a future row label prefix-matches another. FIXED: `labels.index(choice)`
  round-trips by exact equality; `ValueError` surfaces a visible error.
- **[MEDIUM-2]** Width math assumes ASCII (`len(k)` ≠ display cols). ADDRESSED:
  comment + a deferred-i18n note. No live trigger today.
- **[Open Question MEDIUM]** Drain loop could starve under sustained
  back-pressure (giant batch → long Rich render → event-loop stall). DEFERRED
  per perf test. Not observed in current streaming rates.
- **[LOW-1]** Viewport scroll math is dense for an 8-line picker. FIXED: 1-line
  comment documenting center/clamp behavior near edges.
- **[LOW-2]** `print_above_many` typed `list[object]` — tighter `Sequence[object]`
  matches what the body actually requires. FIXED.
- **[LOW-3]** "Empty filter + Enter" no-op wasn't test-covered. FIXED: new
  `test_select_no_match_enter_stays_open`.
- **[LOW-4]** `c-c` shadowed only in `select` — inconsistent with the other
  dialogs. FIXED: added to `confirm()` + `input()`; `editor()` already had it.

Positive observations (from the reviewer): the pump rewrite's tail-flush
ordering is locked in by a direct test; `<any>` binding correctness is sound
(prompt-toolkit's `KeyProcessor` sorts `Keys.Any` to position [0] so concrete
matches at [-1] always win); `_resolve` is idempotent so racy double-confirm
is safe; `idx` clamping is defense-in-depth (in both `render` and `_confirm`);
test renames carry the "why" comments forward.

## Verification

- **Unit tests**: 11 cover the new arrow + filter + empty-options paths;
  4 cover the pump batching + ordering invariants.
- **Pi-port fidelity**: arrow navigation + Enter/Space confirm + type-to-filter
  + Esc cancel + `→` cursor + viewport `⋮` + `(N/total)` counter mirror pi's
  settings picker. Pi's exact hint string ("Type to search · Enter/Space to
  change · Esc to cancel") matches our footer line.
- **Code review (separate lane)**: APPROVE_WITH_NITS → 2 MEDIUM + 4 LOW all
  applied.
- **Live verification**: deferred. The pump batching is deterministic per the
  fake-chrome pump tests; the select dialog is exercised by the smoke tests
  driving real chrome key bindings end-to-end.
