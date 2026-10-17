# 0221. Reasoning streams to a live window, and the renderer must give it back

Status: Accepted (2026-08-15). Amended 2026-10-14 (#285): a block that arrives whole commits when
it closes, and one mark keeps a block a reasoning flush already committed from printing twice.
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

## Amendment (2026-10-14, #285) — an answer block that never streamed still commits, once

Pi reference: `earendil-works/pi@3daa20280`.

### What was measured on `529b997e`

The answer half of `EventRenderer` opened its stream on the first `text_delta`, and `text_end`
committed only through a stream that was already open. A block that arrives WHOLE —
`text_start` → `text_end(content)`, no delta — never had one. `process_responses_stream` produces
exactly that when a server puts the text only in `response.output_item.done` (the slot is created
there), and an extension's stream adapter can do the same. Wire events through the real engine
into the real renderer (`.omc/probes/285-live/impl/probe_285.py`):

| wire | committed live | `replay` of the final message |
|---|---|---|
| message `output_item.done` only, then a `read` call | `● read(a.py)` | the answer, `● read(a.py)` |
| message `added` → `done`, no delta, then a `read` call | `● read(a.py)` | the answer, `● read(a.py)` |
| control: two `output_text.delta` | the answer, `● read(a.py)` | the same |
| done-only `AAAA`, then a streamed `BBBB` | `BBBB` (tail frames `AAAABBBB`, empty) | `AAAA`, `BBBB` |
| reasoning streaming, a done-only message, the reasoning's `done` last | `REASON` | `REASON`, the answer |

The text the renderer parked in `_text_accum` for the whole block was what led the next block's
live window. The real `aelix` TUI in a pty, against a local fake Responses server, showed the same
on screen: every done-only answer was missing live and present once `--session` reopened the file,
and mid-stream the second block's window read `AAAA first block, sent whole.BBBB second block,`.
Print mode, `--mode json` and `--mode rpc` showed the answer already (the first prints the final
message, the other two forward `text_end` and `message_end` as they are).

### Decision

1. **A text block is committed when it closes, whether or not it streamed.** A `text_end` that
   finds no stream open and carries text that renders to something (decision 5) opens one and
   closes it in a single final frame — the delta path's commit, so what it commits is what a
   one-delta stream and `replay` commit: the same Markdown rendering, wrapping and styles. Rows
   compare the styled spans, not only the characters, since review round 2 measured that a raw
   `Text` of the content (raw `**`, backticks, `-` bullets, an unwrapped long line) passed every
   row that compared `.plain` of one line of prose.
2. **Reasoning first**, as ADR-0115 put it for the first delta: opening that stream commits the
   buffered reasoning above it. Measured without it: reasoning still streaming when a done-only
   message overtakes it (the engine routes by `output_index`, pi #6009) committed BELOW the answer.
3. **One mark: "a reasoning flush closed the open answer stream early."** `_text_closed_early` is
   a boolean. `_flush_thinking` closes an open answer stream through
   `_finalize_text(by_flush=True)`, which sets it if that stream drew something; the next
   `text_end` that finds no stream open and whose text renders to something (decision 5) spends it
   and draws nothing; `message_start` clears it, since a turn aborted or failed between
   the flush and that late end never sends the end. A stream closed any other way — by its own
   `text_end`, `message_end`, `turn_end`, an `error` or `done` — sets nothing. This is what makes 1
   safe. openai-completions sends its end events at end-of-stream in block order, so the
   reasoning's `thinking_end` (block 0) arrives first — late: the reasoning was committed at the
   answer's first delta — and still runs `_flush_thinking`, whose first act, `_finalize_text`,
   closes the answer stream before the answer's own `text_end` (block 1) arrives. The fix the issue
   proposed, as written (commit at every stream-less `text_end`), printed that answer twice:
   `['think hard', 'the answer', 'the answer']` through the real adapter, and `COMPLETIONS-ANSWER:
   said exactly once.` on two rows of the real TUI against a fake chat-completions server. Every
   reasoning turn on an openai-completions route (OpenRouter's, for one) takes that path. Set only
   by a flush, not at every close: a block closed by its own `text_end` has no end still to come,
   and a mark it left would be spent by the next whole block of the message, which would then draw
   nothing. Measured with the mark set at every close, an in-order Responses message that streams
   and the next one arriving whole commit `['AAAA']` against `replay`'s `['AAAA', 'BBBB']` (review
   round 3's verify; a row through the real engine pins it since review round 4). And set only
   when the stream it closes drew something (review round 4): a stream whose text renders to
   nothing (a streamed `' '`, out of scope below) has nothing in scrollback to repeat, and its own
   late end renders to nothing too and so does nothing (decision 5). With decision 5's order and a
   mark set for such a stream too, the mark waited for the next stream-less end, the next whole
   block of the message, and dropped it: `['R']` against `['R', 'second']` on `replay`, where
   review round 3's cut, whose late end spent the mark, drew `second`.

   **The mark reads no `content_index`.** Review round 2 keyed it on the index (`_text_index`, the
   block the open stream rendered, and a set of blocks closed early); review round 3 measured what
   that cost. An extension adapter that wraps the built-in openai-completions adapter — as
   `examples/selfhosted` wraps its `stream_simple` — and rebuilds each `text_delta` from its text (a
   redaction, say) leaves the rebuilt event at the class default `content_index=0`, while
   `text_start` and `text_end` pass through with 1. The flush marked block 0, the answer's own end
   came as block 1, found no mark, and drew the answer again from its copy: `['think hard', 'the
   ****** answer', 'the secret answer']` where `529b997e` draws one answer, and two
   `WRAPPED-ANSWER` rows in the real TUI (`.omc/probes/285-live/r2cross-claude/`,
   `.omc/probes/285-live/r3/`). The boolean draws it once. What the index bought was out-of-order
   input only: P2 under out of scope, and one interleaved sequence of the 4000 fuzzed there, match
   `replay` with it and not with the boolean; no in-order sequence does.
4. **`_text_accum` is written only while a stream is open**, and every close clears it, so nothing
   can be left there for the next block. The separate reset at `text_start` the issue also proposed
   was not added: with this rule in place it cannot change any outcome, so no test can tell it from
   its absence; and with a stream still open — a shape no server we measured sends, though
   `process_responses_stream` passes interleaved items through — it would empty that block's live
   window.
5. **A `text_end` with no stream open whose text renders to nothing does nothing**: it draws
   nothing, and it neither spends nor sets the mark of decision 3. Opening a stream for it would
   commit the reasoning still streaming around it, and the rest of that reasoning block would then
   commit a second time, whole, at its own `thinking_end`. The test comes before the mark (review
   round 4). Looked at after it, such an end spent the mark, and the late end of the block the
   flush had closed then drew that block a second time: reasoning that finishes while message 1
   streams, then an empty or HTML-comment message 2 arriving whole, then message 1's own end,
   committed `['REASON', answer, answer]` through the real `process_responses_stream` where
   `529b997e` and `replay` draw the answer once (the round-3 cross-review's E2 and E3,
   `.omc/probes/285-live/r3cross-claude/`); in the real TUI two `E2-ANSWER` and two `E3-ANSWER`
   rows on review round 3's cut, one each now (`.omc/probes/285-live/r4/tui/`). "Renders to
   nothing" is decided the way the renderer decides it: `markdown_lines(_safe_prose(content),
   width)` is empty — the test `replay` applies to a stored block. The first cut tested only for
   the empty string, so a space, two newlines or an HTML comment, sent whole inside streaming
   reasoning, committed `['R1', 'R1 R2']` where `529b997e` and `replay` show `['R1 R2']` (review
   round 2, `.omc/probes/285-live/cross-claude/`). `content.strip()` is not enough either: an HTML
   comment and a link-reference definition are non-blank and render to nothing. Nor is
   `markdown_lines(content)` without the strip: a block made only of characters `_safe_prose`
   deletes renders to a line before the strip and to nothing after it, and that gate cut the
   reasoning around it the same way (review round 3; the four rows that pin it, BEL, RLO, ZWSP and
   a lone ESC, were added then, until which that gate passed every row). Those characters are
   exactly C0 other than tab and newline, DEL, C1, U+061C, U+200B, U+200E-U+200F, U+2028-U+202E,
   U+2066-U+2069 and U+FEFF; of the code points the strip keeps, only space, tab, newline and `>`
   render to nothing on their own (every code point U+0000-U+2FFFF scanned,
   `.omc/probes/285-live/r5/out/scan_draws_nothing.txt`). So the only whitespace that the strip
   keeps and that renders to nothing is space, tab and newline: NBSP and the other Unicode spaces
   (U+1680, U+2000-U+200A, U+202F, U+205F, U+3000) draw two rows, and ZWJ, ZWNJ, SHY and WJ
   (format characters the strip keeps) and an entity Markdown decodes after the strip
   (`&#8238;`) draw one, live and in `replay` alike
   (`.omc/probes/285-live/r5verify/space_row_counts.txt`), so such a block is drawn
   (`.omc/probes/285-live/r4/out/draws_nothing_table.txt`,
   `.omc/probes/285-live/r5/out/probe_blank_rows.txt`). Nor is blank text the only Markdown that
   renders to nothing: so do, for example, a lone `>`, a raw HTML block such as `<br>` or
   `<div></div>`, an HTML comment and a link-reference definition. The cost is one extra Markdown
   render per stream-less `text_end`, and one per flush that closes an answer stream.

### Choices made under the decision (ADR-0235)

- **The `text_end` copy still wins over the deltas.** The final message holds it, and pi renders
  the message. Where aelix cannot follow is scrollback: it commits to the host terminal's
  scrollback (ADR-0105, `full_screen=False`), which is not rewritten, so the lines of a long block
  that already left the 12-line live window stay as they streamed. Measured: a 20-line answer whose
  `done` copy differs in line 1 keeps the streamed line 1 on screen, while `replay` shows the `done`
  copy. Unchanged by this amendment.
- **pi has nothing to port here.** It re-renders the whole assistant message from the snapshot on
  every `message_update` and again at `message_end` (`interactive-mode.ts:3524-3527`, `:3560-3573`;
  `AssistantMessageComponent.updateContent`, `components/assistant-message.ts:91-175`), so it has no
  block lifecycle in which to lose a block. That is also the pi reading this ADR's header said a
  later sync should bring: pi shows reasoning the same way, re-rendering each `thinking` block of
  the snapshot as Markdown on every update.

### Rejected alternatives

- **The issue's proposal as written** — decision 3 above.
- **Not closing the answer stream on a late `thinking_end`** (a block already in `_thinking_done`),
  so the completions answer meets its own `text_end` with the stream still open. It fixes the plain
  completions order and not reasoning that resumes after answer text: openai-completions replays
  that on the same thinking index, decision 8 un-retires it, its `thinking_end` is no longer late,
  and the answer is closed early anyway. Measured, that change together with the proposal as
  written: the plain order commits the answer once, the resumed one `['R1', 'A1 A2', 'R1 R2',
  'A1 A2']`.

### Out of scope, recorded

- **Reasoning that resumes after answer text commits its first part twice** on openai-completions:
  `['R1', 'A1 A2', 'R1 R2']` live against `['R1 R2', 'A1 A2']` on `replay`, on `529b997e` and
  after this change alike — the decision-8 un-retire commits the whole block again at
  `thinking_end`. Same family (live ≠ replay), reasoning side; a follow-up, not this issue.
- **Reasoning that finishes after the message it preceded, on the Responses path.** Decision 1
  makes in-order flows match `replay`; it does not reorder blocks that arrive out of order. Two
  shapes, measured through the real `process_responses_stream` (review round 2,
  `.omc/probes/285-live/cross-claude/out/probe_shapes.txt`, rerun after the fix in
  `.omc/probes/285-live/r2/`): **A** — a reasoning delta arrives after the whole message:
  `['R1', 'THE-ANSWER', 'R1 R2']` live against `['R1 R2', 'THE-ANSWER']` on `replay` (two
  `💭 Thinking…` placeholders under `hide_thinking`); **D** — the reasoning summary arrives whole
  after the message: `['THE-ANSWER', 'REASON']` against `['REASON', 'THE-ANSWER']`. A STREAMED
  answer gives the same on `529b997e` and after this change, so this is the decision-8 family
  reached through reasoning-first (decision 2), not something the whole-block path added; on
  `529b997e` the whole answer was simply missing. Both need a reasoning item that finishes after
  the message item behind it — the out-of-order finish pi #6009 handled in pi's engine; no run
  recorded here has shown a summary delta arriving after that message's `done` (shape A). A
  follow-up, together with the openai-completions case above and the answer blocks below.
- **Answer blocks that finish out of order, on the Responses path.** The renderer has one answer
  stream, and a `text_end` that arrives while it is open closes it, whichever block that end
  belongs to (review round 2's cross-review, F1): the other block's copy replaces the open one's
  text in its final frame, and the open block's own end then arrives with no stream open. Measured
  through the real engine (`.omc/probes/285-live/r3/out/`): **P1** — message 0 streaming, message 1
  done whole, then message 0's `done`: `['BBBB…']` live on `529b997e` and on review round 2's cut,
  and now `['BBBB…', 'AAAA…']` (the late end of message 0 is drawn whole, below) against
  `['AAAA…', 'BBBB…']` on `replay`; with a 20-paragraph message 0 (P1b) message 1 is never drawn
  and the four lines of message 0 already in scrollback are drawn again with the whole block
  (`529b997e` and round 2's cut draw those four lines and nothing else).
  **P2** — reasoning that finishes after message 1 began streaming (its flush sets the mark), then
  message 2 whole, then message 1's `done`: the whole message 2 spends the mark and draws nothing,
  and message 1's late end draws it a second time, `['REASON', 'AAAA…', 'AAAA…']` against
  `['REASON', 'AAAA…', 'BBBB…']`; `529b997e` gives `['REASON', 'AAAA…']`, and the index-keyed mark
  of review round 2 matched `replay` there.
  **E1** — messages 0 and 1 announced, message 1 streaming, message 0 ending EMPTY, then message
  1's `done`: `['THE-ANSWER drawn once.', 'THE-ANSWER drawn once.']` live against one on `replay`
  and on `529b997e`, through the real engine (`.omc/probes/285-live/r4/out/probe_E_3renderers.txt`),
  and two `E1-ANSWER` rows in the real TUI, one after the `--session` reopen
  (`.omc/probes/285-live/r4/tui/`). The empty end closes message 1's stream, and message 1's own
  end then finds none and draws its block whole. Review round 4's order (decision 5) does not
  reach it: that end arrives while a stream is open. So some interleaved shapes that `529b997e`
  drew as `replay` does now draw an answer twice; the round-3 verify's seed 2069 is this one.
  A differential fuzz, the round-2 cross-review's generator run against the renderers
  (`.omc/probes/285-live/r3/fuzz/`; 4000 wire sequences: Responses in order and interleaved, the
  real completions adapter, synthetic adapters; and 2000 more with every `text_delta` rebuilt
  without its index), found no case where `529b997e` matches `replay` and review round 3's cut
  does not, every in-order Responses case matching, two interleaved cases (P1 and P2 shapes)
  committing an answer twice that review round 2's cut committed once, and the 156 rebuilt-delta
  completions cases that cut drew twice matching again. That held for that generator only: the
  round-3 cross-review's and verify's own generators found 75 of 6000 and 10 of 6000 such
  sequences, every one interleaved or with deferred ends. Review round 4 ran all three generators
  (and the cross-review's second, 8000 sequences with an all-index-0 adapter) against `529b997e`,
  review round 3's cut and this change (`.omc/probes/285-live/r4/fuzz/`). In-order sequences match
  `replay` exactly where they did on round 3's cut (every in-order Responses one). 43 of the
  6000, 55 of the 8000 and 5 of the verify's 6000 match on `529b997e` and not here (75, 98 and 10
  on round 3's cut): 94 of the cross-review's 98 and all 5 of the verify's are E1 (a block's end
  landing on another block's open stream), the other 4 a whole answer that ends before reasoning
  that precedes it (A and D). Against round 3's cut, 138 of the cross-review's sequences and 15 of
  the verify's match that did not, and 2 stop matching (seed 5885 in both of the cross-review's
  generators, an E1 shape that cut matched by accident); the lane's 6000 give what they gave. No
  server we measured sends interleaved items, but
  `process_responses_stream` routes by `output_index` (pi #6009) and passes them through. A
  follow-up with A and D: what it needs is a stream per block, or the index the deltas carry —
  which an adapter is free to leave at 0.
- **A STREAMED block that renders to nothing still cuts the reasoning around it.** Decision 5 is
  the whole-block path's rule; the delta path opens the answer stream on its first `text_delta`
  whatever that carries, as it did before this amendment. A `' '` or `'\n\n'` block streamed
  inside streaming reasoning commits `['R1', 'R1 R2']` against `['R1 R2']` on `replay`, on
  `529b997e` and after this change alike (review round 2, `.omc/probes/285-live/r2/`). A flush
  that closes such a stream sets no mark (decision 3), so the block's own end, which does nothing,
  leaves nothing pending. Same family; a follow-up.
- **Two answer blocks interleaving** (a second streaming while the first is still open) share the
  one live window; no server we measured sends that, though `process_responses_stream` passes
  interleaved items through. Both streaming (P1c) commits what `replay` does, unless one of them
  ends empty while the other streams (E1 above); one of them whole is the out-of-order case
  above.

### Verification

`tests/tui/test_text_block_without_deltas_285.py` (45 rows: the issue's three wire shapes and the
residue through the real `process_responses_stream`, a streamed message then a whole one, the
completions order through the real
adapter, the same adapter wrapped by an extension that rebuilds its deltas without their index
(as is and redacted) and that shape with bare events, the mark's lifecycle — the late end, an
index-0 adapter, an abort between the flush and the late end — the reasoning orders, nine whole
blocks that draw nothing inside streaming reasoning (empty, a space, two newlines, an HTML comment,
a mix with a link-reference definition, BEL, RLO, ZWSP, a lone ESC), the same nine arriving whole
between a flush and the late end it marked (E2 and E3), five streams that drew nothing before a
whole block (empty, a space, BEL, an HTML comment, a link-reference definition), four Markdown
blocks whose styled commit must equal a one-delta stream's and `replay`'s (a list, emphasis, a
fenced code block, a 199-cell line of forty `word` that wraps), a 99-cell line of twenty `word` at
50 and at 120 columns, and one through the real `run_tui` chrome read back by `pyte`) plus a `#177`
row in `test_render_terminal_safety_177.py` for the new commit path. Twenty-three of the forty-six
are red on `529b997e`; the other twenty-three are the control, the completions guard, the three
rebuilt-delta rows and the eighteen draw-nothing rows, which `529b997e` passes by drawing no
stream-less block at all. The three rebuilt-delta rows are red on review round 2's cut, which
passes the rest, and the nine E2 and E3 rows on review round 3's. Twenty-two sabotages, each
turning at least one row red: review round 4's three (the mark looked at before the
renders-to-nothing test, a whole block laid out at 80 columns, `StreamRenderer`'s default and the
width of every other row, and the mark set by a flush whatever the stream drew), its verify's
mutant (a flush's "drew something" tested by `.strip()`, red on the HTML-comment and
link-reference streams only, `.omc/probes/285-live/r5/out/sabotage_r5.txt`), review round 3's
six that still apply (round 2's index-keyed mark, the gate without `_safe_prose`, the mark set at
every close, no mark set by the flush, no reset per message, a late end that does not spend it;
its seventh, the mark looked at only once the stream was open, has no place in round 4's order),
and rounds 1 and 2's twelve that still apply (the proposal as written, no whole-block commit, no
reasoning-first, every
stream-less end opening a stream, no `_safe_prose` on the copy, the copy written before deciding,
an accumulator kept across a close, the first cut's non-empty gate, a `.strip()` gate, a raw
`Text` commit, plain wrapped lines, styles dropped). Live: `aelix`
in a pty against local fake servers, frames in `.omc/probes/285-live/impl/`; for round 2
(draw-nothing blocks inside streaming reasoning; a whole Markdown answer against the same answer
streamed and against the `--session` reopen, cell attributes included) in
`.omc/probes/285-live/r2/`; and for round 3 (a done-only answer, the delta control, a whole block
before a streamed one, a lead text before a tool card, the completions order, and the
rebuilt-delta extension: one `WRAPPED-ANSWER` row where round 2's cut drew two) in
`.omc/probes/285-live/r3/`; for round 4 (E1, E2 and E3 against round 3's cut, with the round-3
shapes and the completions order again) in `.omc/probes/285-live/r4/tui/`.
