# 0221. Reasoning streams to a live window, and the renderer must give it back

Status: Accepted (2026-08-15).
Date: 2026-08-15
Relates: ADR-0104 / ADR-0105 (Sprint 6h₁₀a/b, where `StreamRenderer` and the sink-based live
region were built — this reuses that window for a second producer), ADR-0115 (why a thinking
block is committed BEFORE the text that follows it rather than when its end event lands),
ADR-0123 (Sprint 6h₁₅, `hideThinkingBlock` and the `💭 Thinking… (/expand N)` placeholder),
ADR-0121 (the `/expand` store the collapsed placeholder routes through), ADR-0194 (the CSI 2026
bracket around `print_above`, which is why committed scrollback is the host terminal's),
ADR-0219 (the render width this window is rendered at).
GitHub: #170 (reasoning arrived all at once), #169 (a second thinking block in one message was
dropped).
Pi: **no citation offered.** The local pi copy under this workspace is a partial fetch with no
TUI sources, so pi's reasoning-display behaviour was not read. This ADR makes no parity or
divergence claim — the same stance, for the same reason, as ADR-0219.

## Provenance

**Aelix-original, stated narrowly.** The surface is the aelix TUI — `tui/render.py`,
`tui/stream.py` — which ADR-0104 / ADR-0105 built on prompt-toolkit + Rich as aelix's own
implementation. Whether pi streams reasoning is **unknown to this ADR**. ADR-0218 exists
because recalled-not-read provenance had shipped before; a later sync that reads pi's TUI
supersedes this and should amend it rather than quietly diverge.

## Context

### The data was already streaming; the renderer was holding it

Every shipping adapter emits `ThinkingDeltaEvent` in real time — anthropic, google,
openai-responses, openai-completions. The renderer was the only thing in the path that did
not act on them:

```python
elif sev.type == "thinking_delta":
    self._thinking_accum += sev.delta      # accumulate, render nothing
```

On a slow reasoning model the user watched a spinner with nothing behind it and then received
the whole block at once. Separately (#169), block identity was a single boolean
(`_thinking_flushed`) that conflated "already printed THIS block" with "already printed ANY
block", so a second block in one message — think → tool → think — was silently dropped.

### A live window is a resource, and the first cut only took it

Streaming landed in `5834acd`. A five-lens adversarial review of that commit returned nine
findings; all nine survived independent verification, and four were the SAME shape:
`_push_thinking_tail` **acquires** the live window and nothing **releases** it.

That shape is worth naming, because it is why a green suite did not see any of them. The
failure is not "the wrong string was computed" — it is "a string nobody computed again is
still on the glass". A sink-level assertion cannot observe it: the defect is the ABSENCE of a
later write, and every existing test asserted the presence of an earlier one.

Measured on `5834acd`, one `ThinkingDeltaEvent` then each terminal path, the last value handed
to `set_tail` was **still the reasoning** after: `turn_end(aborted)`, `message_end(error)`, the
stream `error` arm, `finalize()`, a following `message_start`, and `hide_thinking` flipped
mid-block. Through the real chrome under pyte the reasoning was on row 0, one row above
`❯ Type your message`.

### And it was quadratic

Every throttled frame re-rendered the entire accumulator to keep 12 lines. Measured at width
80: 1KB → 0.6ms, 50KB → 24ms (905 lines rendered, 12 used), 150KB → **226ms**. The floor is a
wall-clock 10 FPS, so frame COUNT does not fall as frames get dearer: total work is
O(duration × size). The harness calls the subscriber synchronously (`harness/core.py:1994`),
so this ran on the prompt-toolkit loop. The answer path has had an adaptive governor since
6h₂₄ (`stream.py:155-158`); the reasoning path copied its FLOOR and not its governor.

## Decision

1. **Reasoning streams to the live TAIL only.** The block is still committed to scrollback
   exactly once, when it closes, so history is byte-identical to before. Reasoning is
   ephemeral scaffolding: you want to watch it happen and then have one tidy block, not a
   progressive dribble of committed lines.
2. **Plain dim-italic, not markdown.** Reasoning is prose. The committed form is
   `Text(text, style="dim italic")`; rendering the live form through `Markdown` would restyle
   `**` and `#` and make the same text look like two different things. Hence `plain_lines`
   beside `markdown_lines` rather than a reuse of it.
3. **Block identity is `content_index`.** It is what distinguishes one block from the next on
   the wire, and it is what the renderer had never read (#169). Idempotency is per block, kept
   in `_thinking_done`.
4. **One acquire, one release.** `_push_thinking_tail` takes the window;
   `_clear_thinking_tail` gives it back, and every path that ends a turn calls it —
   `message_end`, `turn_end`, the stream `error` arm, `finalize()`, plus `_reset_message_state`
   as belt and braces.
5. **The release is gated on "a tail was painted", never on the current `hide_thinking`.**
   The tail was written under whatever the flag was when the deltas arrived, and the user can
   flip it mid-block. `hide_thinking` is therefore a **property** whose setter takes the window
   down on the ON transition — Ctrl+T is most naturally pressed WHILE reasoning scrolls by, and
   all three writers (startup seed, `/settings`, Ctrl+T) inherit the behaviour without
   remembering to.
6. **Partial reasoning is COMMITTED on abort and error, not discarded.** `_finalize_text` one
   line earlier already commits a partial ANSWER on that same branch; the two halves of one
   turn must not disagree about whether partial output survives.
7. **The answer owns the live window while it streams.** Both renderers write the same
   last-writer-wins sink. Reasoning arriving mid-answer is committed when its block closes, not
   painted.
8. **A reopened `content_index` un-retires itself.** `openai-completions` allocates one
   thinking index per message and replays reasoning that resumes after answer text on it; a
   permanent latch dropped that continuation. The per-message reset of `_thinking_done`
   therefore covers exactly one remaining shape — a block delivered as `thinking_end` ALONE,
   with no deltas to trigger the un-retire.
9. **The window renders from a bounded slice.** Rich wraps each newline-separated line
   independently, so a slice that begins at a line boundary renders byte-identically from there
   on: take `width × (window + 2)` characters, doubled, then resynchronise to the first
   boundary in the first half. Measured 12/12 lines identical to the full render at widths
   40/60/80/120/240 for 10/50/150KB blocks. 226ms → 5.9ms per frame at 150KB.
10. **The governor comes along anyway.** With the slice it should never leave the floor — which
    is the point. It bounds any future renderer that is not O(1) in block size, rather than
    being a second copy of the same fix. Floor 0.1s, ceiling 2.0s, matching `StreamRenderer`.

## Consequences

- **Scrollback is unchanged.** Every pre-existing commit assertion in the suite stays true;
  only the live region is new.
- **Abort now leaves a trace of the reasoning**, where before this whole arc it left none.
  That is a behaviour change, chosen deliberately under decision 6.
- **`hide_thinking` is no longer a plain attribute.** Assigning it can now emit a sink write.
  Turning it OFF deliberately does nothing — only the ON transition releases.
- **The rate is pinned by a test with a literal, not by one derived from the constant.**
  A derived assertion follows the constant anywhere someone moves it; "tuned the floor to 10ms
  for snappiness" is the chrome-thrash regression the floor exists to prevent.
- **Tests assert a bound on the INPUT to `plain_lines`, not a wall-clock number**, so the
  quadratic guard stays deterministic on a loaded CI box.

## What was verified, and what was not

- **11 sabotages, one per fix, each caught by the test that names it.** One of them mattered
  beyond bookkeeping: it showed that decision 8's un-retire **masks** the per-message reset, so
  the test written to pin that reset did not. Both the test and the comment were narrowed to
  the claim that survives (the deltas-free shape).
- **Live, through the real chrome:** three tests wire the renderer's sinks to a real
  `AelixChrome` exactly as `shell.py` does, paint, and read the frame back through a terminal
  emulator. All three fail on the parent commit — the two negative ones with `assert not True`,
  i.e. the reasoning really was on the glass.
- **Live, against a real model** (openai/gpt-5.5 via openrouter, 90×24 pty): reasoning streams
  (7–12 dim-italic repaints per turn, reasoning tokens repeating — the signature of repeated
  tail repaints), a completed turn leaves a clean live region, and a Ctrl+C abort leaves
  `✖ Operation aborted` with nothing pinned.
- **Live, against a real model, in the exact failing window** (deepseek/deepseek-r1 via
  openrouter, 90×24 pty, Ctrl+C 25s after submit, reasoning still streaming and no answer text
  yet — 379 tail repaints before the interrupt):

  | build | final painted screen |
  |---|---|
  | `5834acd` (pre-fix) | `✖ Operation aborted` on row 9, **reasoning still painted on rows 11–20**, directly above the input box |
  | this ADR's code | reasoning committed on rows 11–19, notice on row 20, **live region clean** |

  This paragraph replaces a "NOT verified" note that was **wrong, and wrong through my own
  instrument**. The probe counted `\x1b[2;3m`; Rich emits the reasoning style as `\x1b[0;2;3m`
  here (reset-prefixed), so the counter under-reported, several genuinely-in-window runs were
  labelled vacuous, and the conclusion drawn from them — "this window is unreachable on a live
  provider" — was an artefact. Two things were both true and got conflated: `openai/gpt-5.5`
  really does finish reasoning too fast for a fixed-delay interrupt, and the counter that was
  supposed to detect exactly that was broken. A slow-reasoning model plus a corrected pattern
  reaches the window on the first attempt.

  Kept as a caution, since the failure mode generalises: a probe's own detector needs a positive
  control. A counter that reads zero looks identical whether the event did not happen or the
  pattern did not match, and "nothing to strand" is the reading that quietly excuses a gap.

## Rejected alternatives

- **Committing reasoning progressively** rather than to a tail — would make every existing
  commit assertion in the suite a lie, and leaves history full of scaffolding.
- **Rendering reasoning as markdown** for consistency with the answer — reasoning is not
  markdown, and the committed form is plain, so this makes one block look like two things.
- **Discarding partial reasoning on abort** — cheaper, but inconsistent with the partial answer
  committed one line earlier on the same branch.
- **Letting reasoning win the live window mid-answer** — the flip is unreadable, and the
  reasoning that caused it was then dropped by the latch.
- **Clearing the tail on every `hide_thinking` assignment** instead of on the ON transition —
  turning the setting off would emit a spurious clear.
- **Deriving the throttle assertion from `_THINKING_TAIL_MIN_DELAY`** — green for any value of
  the constant, which is the gap the review found.

## Out of scope

- The replay path's remaining divergences from live rendering (enumerated in `replay`'s own
  docstring) are untouched here.
- #168 (`toolResult` loses `details`) is open by design and unrelated.
- `_THINKING_LIVE_WINDOW = 12` is inherited from `StreamRenderer`'s live window and was not
  re-derived.
