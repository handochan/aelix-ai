# 0280. The chrome stays on the last rows once the transcript fills the screen

Status: Accepted (2026-10-31, #632). Option A of the #632 design, the owner's
choice; D1–D6 are decided and implemented. Revised before merge (review round
1, 2026-10-31: a Codex review and an independent verifier): D1 counts a draft
as one row and waits for a fresh CPR answer after clear() or a resize; D3's
wait is bounded and a missing answer is a failure; D4 holds an open modal too,
for at most 0.1 s; D5 clears through `Renderer.clear`; the numbers and Limits
are re-measured. Round 2 (2026-10-31, the round-1 verifier): D3 ends the hold
and repaints when it stops waiting, judges a late answer by the queries it
waited on, and treats a resize as starting the renderer over; Limits rewritten
from the verifier's measurements.
Date: 2026-10-31
Issue: #632. The progress note — the rounds, their gates, the grids before and
after and the residuals: `.omc/specs/632-progress-2026-10-31.md`.
Amends: ADR-0159 (the in-flow modal slot: where the chrome sits after a modal
closes), ADR-0194 (the CSI 2026 bracket: what it holds while the chrome is
pinned, and its "known residual"). Leaves #84's `clear_on_shrink` inert (D6).

## Context

The chrome is an inline `Application(full_screen=False)`. prompt-toolkit 3.0.52
reserves `max(rows below the app's first row at the last CPR, the last screen's
height, the preferred height)` rows (`renderer.py`, `Renderer.render`) and moves
the app's first row down only when a line is printed above it (`in_terminal` →
`renderer.erase()`). The body `HSplit` lays its children out from the top, so
rows the chrome reserved and no longer needs are left empty below the footer.

An in-flow modal (ADR-0159) taller than the rows below the app scrolls the
terminal up. When it closes, the chrome stays where the modal was. Measured on
`e19ab55e` with the real TUI in a pty, the screen first filled by a 40-line
reply (rows are input/status/footer, empty rows below the footer in brackets):

| | 80x24 | 80x30 | 120x40 |
|---|---|---|---|
| before the modal | 21/23/24 | 27/29/30 | 37/39/40 |
| `/model` closed | 12/14/15 (9) | 18/20/21 (9) | 28/30/31 (9) |
| `/settings` closed | 8/10/11 (13) | 14/16/17 (13) | 24/26/27 (13) |
| `/scoped-models` closed | 15/17/18 (6) | 21/23/24 (6) | 31/33/34 (6) |
| `/extension` closed | 9/11/12 (12) | 15/17/18 (12) | 25/27/28 (12) |
| delegation consent answered | 10/12/13 (11) | 16/18/19 (11) | 26/28/29 (11) |
| approval at the end of a streamed reply | 15/17/18 (6) | 21/23/24 (6) | 31/33/34 (6) |

The next turn takes back only as many rows as it prints: after a three-line
reply `/settings` still left 4 empty rows. The same mechanism leaves two rows
when the working row and its spacer go away at the end of a turn.

pi has the same default. Its `/model` and other selectors replace the editor in
the layout, and `setClearOnShrink(false)` — "empty rows remain" — is the
default (`packages/tui/src/tui.ts`). With `clearOnShrink` on, pi redraws
everything: `\x1b[2J\x1b[H\x1b[3J` and every line again
(`tui-main-screen.ts`, `fullRender(true)`). pi can, because it holds every
transcript line. aelix's transcript is Rich output in the terminal's own
scrollback. The only transcript aelix can draw again is the session's, and
replaying it wipes the banner and every slash-command output and differs from
the live render (`EventRenderer.replay`'s docstring). Prototyped (B1), it fixed
only the modals that close between turns and cost 5–7 KB per close; pi's users
report that wipe as a bug (pi #10030, #9240). Erasing the reserved rows and
measuring again (B2) changes nothing: the erase returns to the app's first row,
which is where the chrome already is.

## Decisions

### D1. Once the chrome reaches the bottom, it is pinned there

After every render (`after_render`), with no modal open, the chrome is pinned
when the rows above the app's first row plus the chrome's own height fill the
screen — the transcript has reached the bottom (`AelixChrome._note_render`). The
input counts as one row there: a tall draft alone reaches the bottom of a
screen the transcript has not filled (80x34, a 12-line draft, measured in round
1), and cleared, it would have left the chrome pinned below a 10-row band. The
height must come from a fresh CPR answer: prompt-toolkit applies every answer
to the current geometry and keeps unanswered queries across `Renderer.reset`,
so after `clear()` or a resize the answer to a query asked before them arrives
first and describes a screen that is gone. Pinned from it, an emptied screen
kept its input and footer on rows 21 and 24 for good (Codex, round 1). The
answers come back in the order asked, so `AelixChrome._arm_fresh_cpr` notes the
newest query, and the pin waits for its answer — or, when a wait that gave up
dropped that query unanswered, for the next one a print asks.
From then on `_BottomPad`, the first child of the body `HSplit`, takes every
spare row. Its dimension is `preferred=0, max=<all of them>`: it takes only rows
no other child wants, so it never makes the reserved region taller and cannot
scroll the terminal itself. The empty rows a modal leaves are now a band between
the transcript and the input; the next `print_above` writes into that band, from
the app's first row, as before. Nothing is printed to make room, so nothing
blank reaches the scrollback.

### D2. A screen the transcript has not filled lays out as before

The pin is not set while a modal is open. A modal that scrolls a short
transcript (a fresh session at 120x40) would otherwise pin the chrome to the
bottom of a half-empty screen. Until the screen is full the chrome follows the
transcript and the rows below it stay empty, as before #632.

### D3. While pinned, the CSI 2026 bracket holds the CPR answer, up to 0.1 s

ADR-0194's known residual: `in_terminal`'s exit repaint runs before the CPR
answer, with the height unknown, and the answer's repaint lands after `?2026l`.
For a pinned chrome that first frame is also in the wrong place: drawn under the
printed lines, it jumps to the last rows one round trip later.
`AelixChrome._settle_pinned` therefore waits, inside the bracket, for the answer
and repaints at once with `Application._redraw` — private, and the call
`in_terminal` itself makes. A terminal with 2026 paints one frame per print: the
whole chrome, on the last rows. Not pinned, nothing changes.

The wait is at most `_CPR_HOLD_LIMIT` (0.1 s). The output pump waits on the
print, so an unbounded wait stalled the output for the round trip; past 0.1 s
the answer comes from a remote link. The settle then ends the hold (D4) and
repaints, still inside the bracket, so the bracket closes on the frame
prompt-toolkit draws before an answer — the chrome under the printed lines,
without the CPR-gated rows — as before #632; prompt-toolkit repaints when the
answer comes. Left held, a 2026 terminal painted a frame with no chrome row
for the rest of the round trip: 102, 510 and 1007 ms per turn with answers 0.1,
0.5 and 1.2 s late (round-1 verifier); now 0 at each. The wait itself
(`Renderer.wait_for_cpr_responses`) goes on in the background and gives up
after a second, returning normally. A clear(), a resize or a later print since
makes it moot (`_restart_seq`; two prints at once otherwise read each other's
query as a missing answer).

The owner accepted the private call on one condition: every failure falls back
to today's behaviour. A failure is a wait or repaint that raises, or no answer
at all: a query the settle waited on that the background wait gave up on
(Codex, round 1: before, that left the chrome pinned with the input on row 9
and no footer). Whether the answer came is read from those queries, not from
the live queue — a resize's own query, still open when the print's answer
arrived, read as a missing answer and turned the pin off for the session
(round-1 verifier: CPR 0.5 s late, 80x24 resized to 80x30 right after a print;
a later `/settings` close was back at 14/16/17 with 13 empty rows; now
27/29/30 with none). Any failure drops the pin for the session
(`_pin_failed`), and the chrome lays out as before #632.

### D4. Without 2026, the chrome is held off the screen instead of jumping

A terminal without 2026 (macOS Terminal.app) paints every state. While a band is
open, the repaint before the CPR answer draws no chrome row
(`AelixChrome._hold_for_cpr` gates every row the CPR gate does not: the input,
the widgets above and below it — the stream tail, a delegation panel — and the
modal slot, since a dialog open during a print jumped from row 19 to row 8 and
back otherwise), so the chrome goes from the last rows to absent to the last
rows, instead of being drawn up in the band and then jumping down. It applies
only with a band open; a print with the chrome already on the last rows
repaints as it did before #632.

After a print the hold lasts until the answer and never longer than
`_CPR_HOLD_LIMIT`: the settle ends it then and repaints (D3), and on any
failure the chrome is drawn as before #632. A resize is not a print: its
repaint is prompt-toolkit's, outside any 2026 bracket, and a CPR answer does
not trigger a redraw, so with a band open the held frame stays until the next
redraw (`min_redraw_interval`, 0.08 s; the 0.1 s refresh) — on both terminal
kinds (Limits). The limit is 0.1 s, measured (pty, 80x24, the turn after a
close, time with no chrome row at all; the answer delayed by the driver):

| CPR answer | before #632 | limit 0.5 s | limit 0.1 s |
|---|---|---|---|
| at once | 1 ms | 7 ms | 7 ms |
| 0.1 s late | 1 ms | 211 ms | 103 ms |
| 0.5 s late | 1 ms | 1013 ms | 108 ms |
| 1.2 s late | 1 ms | 533 ms | 112 ms |
| stops after the pin | 1 ms | 555 ms | 108 ms |

A local answer ends the hold long before 0.1 s, so the limit never cuts one;
past it the absence no longer grows with the round trip.

### D5. `clear()` starts the renderer over

`/clear`, `/new` and `/resume` call `AelixChrome.clear()`, which wrote
`\x1b[3J\x1b[2J\x1b[H` raw. The renderer kept the erased screen as its last
frame and its old first row, so its next frame wrote only the cells that
differed, from the old cursor: the placeholder without its `❯` prefix and no
footer (the "garbled first frame"), and the pin would be set again from the
old numbers. `clear()` now unpins and clears through `Renderer.clear()`, which
erases, clears the screen and homes the cursor through the output and asks
where it is; the scrollback's `\x1b[3J` is still written raw, first, where the
output speaks VT. On prompt-toolkit's legacy `Win32Output` raw ANSI is printed
as text and moves nothing, so a reset after a raw clear drew the prompt twice
(Codex, round 1); there `Renderer.clear` runs the console API, whose
`erase_screen` clears the whole buffer, history included, and nothing is
written raw. The pin then waits for the answer to the new query (D1).

### D6. `clear_on_shrink` (#84) stays inert

The pin needs no setting. The `/settings` row stays "Saved but not yet wired";
its help no longer says "Clear scrollback when the terminal shrinks", which was
neither pi's meaning (a redraw when the content shrinks) nor what aelix does.
It now names the key's origin and says it has no effect.

## Consequences

Measured on the final tree with the same pty driver as the Context table, and
re-measured with the round-1 verifier's driver (63 scenarios, including CPR
answers delayed, stopped and never sent):

- Every modal above, at 80x24, 80x30 and 120x40, closes to 21/23/24,
  27/29/30 and 37/39/40 with no empty row below the footer. So does the
  consent dialog after its delegation ends, and the approval after a stream
  once its turn ends; so does a dialog opened while output streams, a second
  dialog opened into a band, and 80x9, 80x8 and 80x6.
- A resize while a modal is open ends on the new last rows: 80x30→80x24
  21/23/24, 120x40→90x30 27/29/30, 80x24→80x30 27/29/30 with a 20-row band
  above.
- A screen not full yet — a fresh 120x40 session, or a 12-line draft cleared
  in a fresh 80x34 one — lays out as on `e19ab55e`.
- The first frame after `/clear` is the whole chrome at the top (2/4/5); on
  `e19ab55e` it had no footer.
- On a 2026 terminal no painted frame lacks the footer once pinned while the
  CPR answer comes within 0.1 s: 0, against 2 per turn and 11 in a 30-line
  stream on `e19ab55e`. The input row no longer moves between rows 21 and 23
  while a stream commits. With later answers no painted frame lacks every
  chrome row (Limits).
- Bytes (two drivers, the #632 one and the round-1 verifier's): a
  slash-command dialog's close writes 10–14% fewer, the consent dialog's
  6.6–11% fewer; the approval's close after a stream writes 4–23% more, as
  much as the window catches of the prints that follow it. The turn after a
  close writes 8–17% more (6–7% with a 40-line reply), and a 30-line stream
  4–7% more (17 456 → 18 257 at 80x24 on the round-2 tree), with the same 11
  brackets and 11 CPR requests. No `\x1b[2K`, `\x1b[2J` or `\x1b[3J` in any
  close, next-turn or stream window, before or after. The last streamed line
  reaches the screen at the same time (3.2–3.4 s).
- Scrollback: no transcript line lost or doubled in any run, a six-screen
  session included. Runs of blank rows appear only on screen, in the band. A
  40-line reply after a close leaves none in the scrollback.
- No CPR at all (`PROMPT_TOOLKIT_NO_CPR=1`, or a terminal that never answers):
  never pinned, identical to `e19ab55e`.
- Windows: every prompt-toolkit Windows output reports no CPR and reads the
  rows below the cursor synchronously, so the height is known in
  `in_terminal`'s own repaint. D3 returns at once (`responds_to_cpr` is
  False), and D4 never holds, because no CPR is outstanding. D1, D2 and D5 run
  as on POSIX; on the legacy `Win32Output` D5 clears through the console API.
  `tests/tui/test_bottom_pin_632.py` drives both shapes: a vt100 output that
  answers like `Win32Output.get_rows_below_cursor_position`, and one that also
  prints the chrome's raw writes as text, as `Win32Output.write_raw` does.
  `pyright --pythonplatform Windows` is clean on the changed files. Not run on a
  real Windows console.
- Tests: `tests/tui/test_bottom_pin_632.py`, 27 cases, drives the real chrome at
  80x24, 80x30 and 120x40 against a vt100 output replayed through pyte, every
  CPR answered from the replayed cursor — at once, late, or never, under the
  test's control: the pin, a screen not yet full, a tall draft, resizes, the
  bracket (the input never drawn in the band, in any state a terminal could
  show), a late answer with the frames before it, the hold running out on an
  advancing clock, an answer past the limit, a missing answer, two prints at
  once, a stale answer after `clear()`, a query dropped unanswered by a wait
  that gave up, a resize during a query, a print under an open dialog, both
  Windows shapes, a raising `_redraw`, `clear()` and the first frame after it,
  #84's help text, the frame a 2026 terminal paints when the answer is late, a
  resize while a late answer is outstanding, and the 0.1 s limit itself. The
  surviving mutants are under Limits.

## Limits

Measured in a pty against a pyte screen, 80x24, the turn after a `/model`
close unless said; "without 2026" is every state a terminal could show, "2026"
what a CSI 2026 terminal paints.

- **Past 0.1 s, every print first loses the chrome, then draws it in the band.**
  The hold keeps the whole chrome off — the input, the status and footer, the
  stream tail, a delegation panel, an open dialog — for about 0.1 s per print,
  on a terminal without 2026 (a 2026 terminal never paints a print's hold: the
  settle repaints before the bracket closes). Then the frame
  before the answer is drawn for the rest of the round trip, on both kinds: the
  chrome, or an open dialog, up in the band under the printed lines, without
  the status and footer, as before #632.

  | CPR answer | no chrome row, without 2026 | chrome in the band, without 2026 | no chrome row, 2026 |
  |---|---|---|---|
  | at once | 7 ms | 0 | 0 |
  | 0.1 s late | 102 ms | 0 | 0 |
  | 0.5 s late | 103 ms | 403 ms | 0 |
  | 1.2 s late | 103 ms | 905 ms | 0 |
  | stops after the pin | 102 ms | 904 ms | 0 |

  Four prints under an open dialog with answers 0.3 s late: the dialog was off
  the screen 400 ms longer than with instant answers without 2026 (not at all
  longer with 2026), and up in the band for 974 ms on both. With instant
  answers it never left its rows.
- **A resize with a band open paints no chrome row on both terminal kinds,**
  until the next redraw. prompt-toolkit's resize repaint has no 2026 bracket and
  runs before the resize's CPR answer, so it is held; the answer does not
  trigger a redraw, and the chrome comes back at the next one
  (`min_redraw_interval`, 0.08 s). Measured, an idle 80x24 → 80x30 resize: 82 ms
  with an instant answer, 162 ms with answers 0.5 s late (final delta
  verifier). It predates round 2.
- **After a resize, a terminal that has stopped answering is caught one print
  later.** The resize's `_restart_seq` step makes the earlier print's
  background check moot, so the missing answer is found by the next print's
  settle, which drops the pin then (final delta verifier: still pinned after
  the resize and a dialog close, dropped within 1.4 s of the next print). The step
  stays: it keeps a settle that outlived a resize from repainting on the old
  geometry. Reading the queries waited on (`_answered(asked)`) alone also fixes
  the round-2 resize defect.
- **A slow answer delays a reply's first text by up to the round trip.** The
  print that echoes the prompt waits up to 0.1 s for its answer, and the
  output pump waits on it; a reply whose first text arrives in that time is
  folded into the next commit, which prompt-toolkit's `in_terminal` holds until
  the answer. This applies to every pinned turn whose first text arrives within
  0.1 s of Enter, not only the turn after a close (steady turns at 0.5 s: 30 →
  503 ms without 2026). Time from Enter to the first reply text, `e19ab55e` →
  now, the range over two measurements (the #632 lane's and the final delta
  verifier's, on the round-2 tree):

  | CPR answer | without 2026 | 2026 |
  |---|---|---|
  | at once | 8–10 → 9 ms | 9–26 → 10–36 ms |
  | 0.1 s late | 34–35 → 103 ms | 105–117 → 204–275 ms |
  | 0.5 s late | 27–32 → 505 ms | 505–532 → 607–611 ms |
  | 1.2 s late | 27–31 → 1006–1008 ms | 1014–1018 → 1108–1191 ms |

  The 2026 column moves with the machine's load (the verifier's runs were at a
  load average of about 14); the column without 2026, and "up to one round
  trip", reproduced in both.

  A first token later than 0.1 s is not delayed (0.4 s first token, answers 0.5
  s late: line 1 at 444 against 443 ms, round-1 verifier). A 30-line stream's
  last line lands at the same time (3.2–3.4 s with instant answers).
- **The bracket is held while the event loop is busy.** The wait yields to the
  rest of the turn, so a turn's synchronous start-up keeps the bracket open:
  the first prompt of a pinned session held it 286–341 ms with an instant
  answer (round-0 verifier). prompt-toolkit's own `in_terminal` waits for an
  outstanding answer at its entry inside the bracket, as before #632.
- **A dropped bracket paints the held frame.** A terminal that releases a stale
  2026 bracket before the settle's 0.1 s are up paints what is there: with a
  band open, the held frame, which has no chrome row.
- **`clear()` before the session's first CPR answer arms the old query.** Until
  a first answer, prompt-toolkit asks no second query while one is pending, so
  `_arm_fresh_cpr` notes the old one; an answer to it, describing the screen
  before `clear()`, can then set the pin. Only possible within the first round
  trip of a session.
- **A private API.** `Application._redraw` is not public, and the settle and the
  fresh-answer check read `Renderer._waiting_for_cpr_futures`. prompt-toolkit is
  pinned `>=3.0,<4`; a release that moves `_redraw` or the queue drops the pin
  (D3) or turns the check off rather than break the TUI.
- **Pinned is sticky until `clear()`.** A terminal made taller puts the chrome
  on the new last rows, with a band above, where it used to leave rows empty
  below.
- **`_note_render` measures the layout after every render while unpinned** — a
  tall terminal, and after `/clear`, `/new` or `/resume` until the screen
  refills: one `preferred_height` pass, a median of 65 µs against 746 µs for
  the render itself (round-0 verifier, live TUI); once pinned, 1 µs.
- **Mutants that survive, each a guard another one covers.** Of 24 mutants of
  the implementation 20 fail `tests/tui/test_bottom_pin_632.py`. The four that
  survive drop: `clear()`'s `_restart_seq` step (its unpin makes a settle moot
  already); a resize's `_restart_seq` step and the late check's reading of the
  queries it waited on (each alone fixes the round-2 resize defect; removing
  both fails the resize test); the later-print check in `_settle_is_moot` (the
  queries waited on were answered, so the overlap no longer looks like a
  failure; the final delta verifier's C3d). The verifiers' own mutants left
  more:
  - no re-arm on a resize (round 1, C1e): no live case found that needs it;
  - `_writes_vt` always true (round 1, C2b): on a legacy console the stray
    `ESC[3J` is wiped by `erase_screen` at once;
  - `asked = []` (final delta, M1d): with no queries recorded the check reads
    the live queue, and the resize's `_restart_seq` step covers the case where
    that went wrong;
  - `_answered([])` true (final delta, M1f): no test reaches the branch where
    no query was outstanding, which a CPR-capable terminal reaches only when
    prompt-toolkit asked none;
  - the timeout branch repainting even when moot (final delta, M2e): benign,
    `Application._redraw` does nothing while an `in_terminal` block runs.

## Rejected

- **Put the chrome back on the row it had before the modal** (A-alt, an anchor
  row). In a fresh session the consent dialog's close left a 10-row band in
  the middle of the screen, and it needs a CPR wait in `show_modal`. An anchor
  alone (A1) missed the approval after a stream: it was taken inside a region
  the stream tail had already grown.
- **pi's `clearOnShrink` — clear and replay the session (B1).** Covered above:
  it wipes the scrollback and fixes only modals that close between turns.
- **Erase the reserved rows and measure again (B2).** No effect: the chrome is
  already at the app's first row.
- **The alternate screen for a modal.** The transcript would disappear while
  an approval asks about it, and output printed while the modal is open would
  be lost when the main screen comes back.
