# 0278. One delegation panel for every `agent` call, two rows per child

Status: Accepted (2026-10-30, #132). D1–D8 are decided and implemented.
Amended 2026-10-30 (review round 1, #132): D3 (the `recent` window keeps an
open call, a whitespace-only reply keeps the last reply), D4 (both width
measures, the header drop order, a disagreeing model in the one-row layout,
the gap and the reserved numbers column), D7 (the resume hint needs a session;
the frame refits on a resize; a refused frame is not kept), D8 (what the RPC
UI traffic is).
Amended 2026-10-30 (review round 2, #132): D3 (the window holds 256 calls),
D4 (a header fills greedily and no term moves with the clock or the state
word; the reserve never cuts a label that fits; a cut costs a logarithmic
number of measures), D7 (the Esc frame's fixed phrases are whole or absent),
D8 (`format_card` and `/agents run`'s row did change in round 1), Limits.
Amended 2026-10-30 (round-2 delta review, #132): D4 (what still re-cuts a
label, a header choice past a reserve moves back, the frame times at the
widths the panel is drawn at), Limits (five residuals left for a follow-up).
No code changed.
Date: 2026-10-30
Issue: #132 (absorbs #26). Design (final, owner-approved layout A):
`.omc/specs/132-design-2026-10-30.md`; the measured BEFORE state:
`.omc/specs/132-baseline-2026-10-30.md`; the implementation notes, the gate
outputs and the live grids: `.omc/specs/132-progress-2026-10-30.md`.
Supersedes: ADR-0199 S10's "a single delegation keeps P2's surfaces exactly"
for the live UI, and §(l)'s surfaces 1 and 3 as drawn surfaces (D1, D2, D4);
ADR-0165's note on row D1 — the per-subagent activity line that landed as one
statusline segment (#26) — for a child of the model's `agent` call (D2);
ADR-0227's batch-panel layout (the job-label column, the model term the rows
took back) (D4). Each carries a dated `Amended 2026-10-30 (ADR-0278, #132)`
note.
Amends: ADR-0276 D14's "a copy the user sees is #132's" — it is the Esc frame
(D7).
Relates: ADR-0194 (the chrome's flicker path, untouched here), ADR-0219 (the
120-cell render ceiling the panel now shares), ADR-0231 (the disclosure row,
unchanged), ADR-0235 (pi is a reference, not a target).
Pi: `packages/coding-agent/examples/extensions/subagent/index.ts`
(`git show origin/main:`), read for `formatToolCall` — `bash` as `$ command`,
the file tools with their path. Aelix renders a call from the trail's own
primary argument instead of a per-tool table (`stream._render_args`), so a new
tool is never shown as nothing.

## Context

Measured on `main` `9d1517a8` in a real TUI (pty + pyte, a scripted provider,
a `yolo` parent; the baseline file has every grid):

- A single delegation showed no progress at 80 or 120 columns. Its per-child
  statusline segment was joined after the yolo disclosure row on the one-row
  status line and cut off at the right edge; it was whole only at 240.
- A batch's aggregate statusline segment was pushed off the same way. Its
  `▌` panel was a 78-cell ribbon at every width, with a numbers column of
  about 12 cells (`running · b…`).
- Rows changed only when a child wrote a line: `sleep 12` read `2s` for nine
  seconds while the parent's own `Working…` ticked.
- `current_tool` stayed on a finished tool until the next `turn_start`, and
  fast calls (`read`, `write`) were never on screen.
- Esc blanked the panel; the user saw `✖ Operation aborted` and nothing of what
  the children had done.

The owner's scope (comment on #132, 2026-10-28): one panel for one child or
many; per child the running tool and its argument, its recent calls with their
outcome, its last reply, elapsed and idle time, tokens, model. Layout A was
chosen on 2026-10-30 from three rendered options.

## Decisions

### D1. Every delegation the model's `agent` tool makes gets the panel

Single, parallel, chain and resume, one child included:
`AgentsExtension._execute` opens a group for every call
(`panel.PANEL_MIN_CHILDREN` is 1), passing the call's tasks, its mode, whether
it is a resume and its profile. `/agents run` opens no group and keeps its
per-child statusline row (`progress.format_status_row`) — byte for byte
except where a field holds a lone surrogate or emoji cut for width (D8).

### D2. No statusline row for a grouped child, and no aggregate row

The panel header says what the aggregate said. A grouped child writes no
`subagent:<id>` row and a group writes no `subagent:group:<id>` row
(`format_aggregate_status` stays public and unchanged; nothing draws it). The
yolo disclosure row (ADR-0231) is unchanged.

### D3. `SubagentProgress` gains `activity` and `last_text`, additively

`activity: tuple[SubagentToolActivity, ...] = ()` — the child's last four tool
calls, oldest first, each `SubagentToolActivity(name, args="", status)` with
`SubagentToolStatus = Literal["running", "ok", "failed", "no_result"]`; and
`last_text: str | None = None` — its latest text-bearing reply on one line,
at most 512 characters. Both defaulted, so `CONTRACT_VERSION` stays 1 (the
precedent `model`/`provider` set). The RPC host forwards a fixed field list
(`rpc/control.py`), so the `aelix_subagent` event does not carry them (#612
owns that). Both are child-authored: every renderer flattens them.

- The reducer keeps a bounded `_StreamState.recent` that EVERY
  `tool_execution_start` feeds — apart from the trail, which stops at 200 and
  would freeze "recent" — sharing the trail's `ToolCall` objects while the
  trail has room; `_close_trail_entry` closes the match in both. It holds the
  latest four calls that are no longer open, by start order, PLUS every call
  still open in the current turn of an open run, at most 256 in all (review
  round 1: a `deque(maxlen=4)` dropped a slow
  `grep` as four parallel `read` calls finished after it, and the panel said
  `thinking` while the child was executing; review round 2: at 32 the window
  turned the 33rd parallel call away for good, and the panel said `thinking`
  once the others had ended). `activity` is at most four, in start order, the
  running calls first and then the latest of the others.
- An open call is `running` only while it is in the child's current turn (each
  `ToolCall` records `_StreamState.turn_starts` when it opened), the run is
  open, and the child is live; otherwise its end will never come and it is
  `no_result`. The projection is `stream.activity_of`, called by the sole
  producer `runtime._publish`, the one place that knows whether the child is
  live.
- `last_text` is computed once, where `summary` is set (`message_end`), never
  per publish. A message whose text is only whitespace leaves it as it was,
  and a reply cut by the 4 096-character work bound ends in `…` like one cut
  at 512.
- There is no idle field: the runtime publishes after every stdout line, so the
  time since a snapshot arrived is the time since the child wrote anything. The
  bridge stamps arrivals.

### D4. Layout A

Pure functions in `panel.py`, raw SGR from a fixed set of seven (dim, reset,
dim italic, red, green, yellow, cyan), every untrusted part flattened on its
own (a lone surrogate becomes U+FFFD), widths in cells — by the LARGER of
rich's `cell_len` and prompt_toolkit's `get_cwidth` sum (review round 1): the
widget is drawn by prompt_toolkit, which drops whatever lands past the window,
and the two disagree on regional indicators, skin-tone modifiers, ZWJ
sequences (prompt_toolkit wider) and `❤️` (rich wider). The gutter is `│ `
(dim), the transcript tool card's own,
so the panel reads as the live body of the `● agent(…)` header above it. The
width is the live terminal's, `tui.width.terminal_columns` (the transcript's
measure, 120 at most), re-read on every frame.

One child — five rows:

```
│ ● agent worker · s-1 · running                     idle 7s · 1m12s · 1.5k tok
│   ✓ read notes.txt
│   ✗ $ pytest -q tests/test_retry.py
│   ▶ $ sleep 12 · idle 7s
│   “Read the notes; one test fails, re-running the slow step.”
```

Two to four — a header and two rows each:

```
│ 3 agents · worker · s-1 · 2 running · 1 done                    14s · 0.4k tok
│ ● 1  Read notes.txt and wait six seconds, then report.              10s · 0.4k
│      ✓ ▶ $ sleep 6
│ ● 2  Wait ten seconds, echo bee, then report.                       14s · 0.4k
│      ✓✓ thinking · idle 6s · “Waiting ten seconds before echoing bee.”
│ ✓ 3  Write c-out.txt, wait fourteen seconds, then report.           14s · 0.4k
│      ✓✓✓ “Wrote c-out.txt and waited fourteen seconds.”
```

Five or more, or when `1 + 2N` exceeds a third of the terminal's rows (never
less than five): one row each, the labels in one column so the marks line up.
`PANEL_MAX_ROWS = 9` stays the hard ceiling.

State glyphs: `○` waiting (dim), `●` starting/running/thinking (cyan), `✓` done
(green), `✗` failed (red), `■` stopped (yellow). Call marks: `✓` ok, `✗`
failed, `■` no result, `▶` running. A call is `$ <command>` for `bash` and
`<name> <primary argument>` otherwise; the call open now (or the one a stop
cut) keeps its row however many calls finished after it. `idle Ns` from five
seconds. A chain's
header is `chain of N`, its `{previous}` reads `‹previous›`, and a step not yet
reached says `waiting for step k`. When members disagree on the model each row
names its own, in both layouts — in the one-row layout before the elapsed time
while every row still leaves its tail 12 cells, and it is the first right-hand
term to go; when they agree the header names it if it fits, else nowhere — no
row repeats an agreeing model.
A label that must be cut is cut in the MIDDLE (from 24 cells), so a fan-out
over one verb keeps the word that differs. The header's time is the oldest
member's age for a batch and the sum of the steps' ages for a chain (whose
steps run one after another — measured live, the oldest step read 9 s on a
call 15 s old).

Refined in implementation, for a panel that does not jitter: the two-row
layout's numbers share ONE right-hand column sized from every member's
numbers; the one-row layout's label column is sized from the labels and the
width only, never from what the members are doing.

Refined in review round 1:

- **Header drop order.** When a header is short of room it drops, one at a
  time: the cost, the idle time, the tokens, then the model, then the
  profile — and the elapsed time LAST, because it is the liveness signal the
  1 s tick moves (measured before: at 56–61 columns the one-child header kept
  `anthropic/claude-haiku-4.5` and dropped the time; at 40 columns the batch
  header kept the profile and dropped it). A batch's counts are never dropped;
  they are cut only when nothing else is left. *Superseded in review round 2
  (below): the order was strict and a number was judged at its width now.*
- **At least two cells** between a row's left side and its right-hand numbers,
  in every layout (one read as a run of text: `…then report. 3s · 15`).
- **A reserved numbers column.** The two-row layout's shared column is sized
  for every elapsed time and token count at five cells (`1m00s`, `12.3k`), so
  a label keeps its cut through `9s` → `10s` → `1m00s` and `99` → `0.1k`;
  only while that still leaves the labels their whole width or the 24 cells a
  middle cut needs — otherwise the column is as wide as the numbers are, and
  labels re-cut when one gains a digit. *Refined in review round 2 (below): a
  label that fits beside the numbers as they are is not cut for the reserve;
  what still re-cuts one is listed there and under Limits.*

Refined in review round 2:

- **A header fills greedily, and nothing on it moves with the clock.** The
  header takes its terms highest priority first — the elapsed time, the
  profile, the model, the tokens, the idle time, the cost — and keeps each
  one that fits beside those already kept, so a term that does not fit no
  longer takes the lower ones with it: at 60 columns a long model id goes and
  the tokens stay, `● agent worker · running … 6s · 15 tok` (measured live
  before: `● agent worker · running` and 32 blank cells before
  `6s`). The elapsed time still goes last: a header that cannot show it shows
  no other optional term. Every term is judged at a width that does not move —
  each number at its reserve (`1m00s`, `idle 1m00s`, `12.3k tok`), the state
  word at its widest (`thinking`), and the idle time and the tokens holding
  their place before they first appear — so no term comes or goes at 9 s →
  10 s → 1m00s, as the child goes quiet, or as it moves between `running` and
  `thinking`. This reverses round 1's "a number is judged at the width it has
  now": on a 60-column batch header `15 tok` showed at 10 s and went at
  1m00s. The batch header fills the same way; its counts are always drawn.
  Past a reserve — `10m00s`, `100.0k tok` — the choice can move, and it
  moves back once the number is inside its reserve again, as `1h00m` and
  `1.0M tok` are. Measured at `8f212cff` on the one-child header at 20–140
  columns: at `10m00s` the choice moved at 11 widths and came
  back at all 11 at `1h00m` (at 34 columns the profile went, came back, and
  went again at `10h00m`); at `100.0k tok` it moved at 7 widths and came back
  at all 7 at `1.0M tok` (at 46 columns the cost took the tokens' place in
  between).
- **The reserve never cuts a label that fits.** A two-row member's label is
  laid out beside the numbers as they are now when it fits there whole, and
  beside the reserved column otherwise. Measured live before: at 80 columns
  and `6s · 15` the reserve cut `[TAG:C] Write c-out.txt, wait four…seconds,
  then report.` with nine blank cells beside it. A label that is cut is cut at
  the reserve whatever its neighbours do, so the clock alone never re-cuts it
  while the times stay under ten minutes, and a whole one is cut at most
  once, when the numbers grow into it. Measured at `8f212cff` (three tasks,
  30–120 columns, 0 s to 9m59s): a label changed only by that
  one cut, or below 46 columns, where the narrow-terminal rule above — which
  is unchanged — lays the labels out against the numbers as they are, by
  design. The column still changes width for other reasons, and a label is
  re-cut when it does: a member's first token count appearing, a token count
  that falls, members coming to agree on a model, and a number past its
  reserve. Each is under Limits, with its measure.
- **A cut costs a logarithmic number of measures.** A cut placed by the
  per-character sum that is still too wide by the whole-string measure (a
  `❤️` is one cell by the sum and two as a string) is shortened by bisection,
  not one character at a time: measured, eight members with 64 KiB `❤️`
  tasks took 430 ms a frame, and the 1 s tick drew one every second. The
  flattened labels are cached per task, since a group's tasks do not change.
  Measured after, eight distinct 64 KiB tasks of ZWJ, VS16, combining-mark,
  regional-indicator, skin-tone or CJK text, at the widths the panel is drawn
  at — the bridge reads `tui.width.terminal_columns`, which caps it at 120
  (`_MAX_RENDER_WIDTH`) — 40 to 120 columns: at most 13.1 ms for a group's
  first frame and 5.4 ms for every frame after it (the worst of six runs of
  the round-2 delta review's `perf_adr.py`; at 300 columns, a width the
  panel is never given, it measured 18.3 and 11.7 ms). Nothing drawn changes:
  320 000 random cuts of ASCII, CJK and emoji text, 3 600 random frames and
  9 000 labels came out byte for byte as before.

### D5. The height is fixed for the life of a group

`progress.SubagentProgressBridge.begin_group` decides the row count once
(`panel.panel_height`: from N and the terminal's rows at that moment) and
every frame of the group — the Esc frame included — has exactly that many
rows. A child's first tool, first reply or end never changes the height; a
resize changes the width only.

### D6. Redraws are throttled and ticked, never drawn from the publish path

Every snapshot is recorded and stamped, then one redraw is scheduled no sooner
than 250 ms after the previous one (`loop.call_later`), so N chatty children
cost at most four panel writes a second; while any member is live a 1 s tick
redraws, so elapsed and idle move while a child is silent. Writes still go
through the bridge's line-tuple dedup. The scheduler and the clock are
injectable; with no running loop the bridge draws at once. Ending a group and
`clear()` cancel every handle. `chrome.py` is not touched.

### D7. Esc leaves the children's record on screen until the next prompt

On a cancel the extension hands the bridge each started member's envelope —
the same `CallEnvelopes.snapshot()` the model's report (ADR-0276 D14) is built
from — and the bridge draws a final frame at the group's height: a member still
live reads stopped (`■`), and so does one whose envelope says the cancel ended
it, its cut call reads `no result`, and its changed files are named.

```
│ ■ agent worker · stopped — the turn was cancelled                  2 turns · 3s
│   ✓ write notes.md
│   ■ $ sleep 97 · no result
│   changed: notes.md
│   Ask to continue it and the model can resume it.
```

A batch's header reads `3 agents · stopped — the turn was cancelled · ask to
continue them`, a member that never started reads `not started`. Each of the
frame's fixed phrases — `— the turn was cancelled`, the hint, the one-child
block's last row, a cut call's `· no result` — is shown whole or not at all,
never cut mid-word (review round 2; measured live before, at 60 columns:
`… · stopped — the turn was cancelled · ask to cont…`): the hint goes first,
and a hint that fits without `— the turn was cancelled` is shown. The resume
hint appears only when the child (or, in a batch, at least one member) has a
session to resume; without one — a `--no-session` parent's children have
none — the last row reads `It has no session, so it cannot be resumed.` and a
batch's header `· no session to resume` (review round 1). The frame stays
until the next `before_agent_start`, a new group, or `clear()` (session
switch, teardown). While it stays, a 1 s watch redraws it when the terminal's
width changes — and only then — at the same height (review round 1: after a
resize the chrome clipped the old frame with no `…`). `linger` reports whether
the Esc frame itself reached the UI; when it did not (an RPC client that
could not encode it), the group ends and the stale running frame is blanked.
A teardown's `clear()` runs before the harness aborts the
turn, so the frame for a cancelled turn is never drawn onto the next session.
A normal finish still clears the panel — the result card is the record — and a
call with no member that ever published draws nothing.

### D8. Not changed

`chrome.py`, `shell.py`, `render.py`; `format_card`'s layout and
`PartialThrottle` (the `on_partial` card reaches the model, not the screen);
the `subagent_*` event channels; the `aelix_subagent` RPC event's field list;
the disclosure row; `/agents run`'s row layout.

`format_card` and `/agents run`'s status row DO print two things differently
since review round 1, because they share `_flatten` and `_cut_to_cells` with
the panel (found by the round-2 review, measured on 117 outputs): a lone
surrogate in a field prints as `�` (U+FFFD) instead of being passed through
for the encoder to reject, and a field cut for width that holds emoji is cut
shorter — it is now no wider than its bound by prompt_toolkit's measure too:
a status row whose model is 60 regional indicators went from 68 cells by rich
and 107 by prompt_toolkit to 48 and 67, and one whose tool name mixes `❤️`,
flags, skin tones and Hangul from 103 and 141 to 79 and 103. 15 of the 117
outputs changed, all of them a surrogate or a cut emoji field; a field that
fits, and every ASCII or CJK field, prints as before. Round 2 changes neither
surface further (the same 117 outputs, identical).

The RPC UI traffic DOES change, in one place (review round 1, measured by the
Codex cross-review): an RPC client that enabled UI requests (`ui/enable`) and
runs a command that awaits a model delegation — the only way a model
delegation runs inside an RPC command window; an ordinary RPC `prompt` is
headless and sends no UI requests before or after — now receives `setWidget`
requests carrying the panel's rows (ANSI SGR included, at most four a
second with the 1 s tick counted in, and a final `null`) instead of one
`setStatus` row per child. `/agents run` from RPC still sends its `setStatus` rows, byte for
byte except as the paragraph above says.

## Consequences

- The model door's children are visible at every width: measured in the live
  check (progress file), the one-child block at 80 and 60 columns, a batch at
  80 and 60, a chain, and the Esc frame.
- The panel takes rows a single delegation never took: five above `Working…`.
  It is fixed for the call, so it does not move the input or the status rows
  while the call runs.
- `PANEL_ROW_MAX_CHARS` (78) is now only the per-field bound of the card and
  the per-child status row; a panel row is bounded by the terminal.

## Limits

- **Past 252 calls open at once** a child's newer calls are not added to the
  panel's window (the older open calls keep their places), and one turned
  away is not added later: if every call the window holds ends while it is
  still running, the panel says `thinking`. No bounded record can avoid that
  for every stream — naming an open call needs its id, and a child can open
  more calls than any bound and end the ones kept first — so the bound is set
  where a model's parallel batch, a handful, does not reach it.
- **A bridge with no UI when a group opens** fixes the group's height from a
  24-row terminal (`panel_height`'s default), since it has no terminal to
  measure; if a UI binds during the call, the panel keeps that height — a
  four-member batch draws one row each (five rows), not two (nine).
- **A lingering frame's width is checked once a second**, so for up to a
  second after a resize the old frame can be clipped.
- **An agreeing batch's model is not named when the header cannot afford it**
  (five count classes at 80 columns, a provider-scoped id at 50). The rows do
  not take it back; ADR-0227's fallback belonged to the job-label layout.
- **A `grep` call shows its path, not its pattern**, when it has both: the
  trail's primary argument prefers `path` (`stream._ARG_PREFERENCE`).
- **Glyph widths assume one cell.** `●`, `■`, `▶` and `○` are East Asian
  ambiguous; a terminal set to draw ambiguous characters wide would draw them
  two cells wide and the right edge of a full row would wrap. The chrome's own
  `●` tool marker has the same exposure.
- **Text still streaming is not the last reply** (`message_update` stays
  ignored, as in D13 of ADR-0276).
- **The Esc frame stays through slash commands**: only a prompt dismisses it.

Found by the round-2 delta review (2026-10-30, every one low; measured at
`8f212cff` with its probes — the progress file's "Round-2 delta review and
finish") and left for a follow-up, since fix rounds were capped at two:

- **A label goes back to whole when the numbers shrink, and is cut again
  when they grow.** It is laid out against the numbers as they are when it
  fits there whole, and they can shrink: a token count is a context level
  and falls after a compaction — at 77 columns, three members at 99 → 1.5k →
  99 → 1.5k tokens drew task C whole, cut, whole, cut on four frames — and
  when members come to agree on a model, the per-row model column goes (at
  80 columns `[TAG:C] Write…then report.` came back whole; the runtime names
  the model it asked for until a child's first reply names the one it ran,
  so two names for one model can converge — code reading). A number past its
  reserve re-cuts every cut label too, and again on the way back: at 79
  columns task 1 at `10m00s` and `1h00m`, at 78 at `100.0k` and `1.0M`.
- **A member's first token count re-cuts the labels.** The header's tokens
  hold their place before they appear; the two-row numbers column does not,
  so a member's first count widens it by eight cells (` · ` and a five-cell
  reserve): at 80 columns `Port the retry
  backoff in the provider…dapter we ship, then report` at 3 s became `Port
  the retry backoff in the prov…we ship, then report` at 4 s and `· 15`.
- **A stopped call counts its `■ ` twice** (`_now` measures the room after
  adding the mark, then takes two more cells), so in a batch's Esc frame
  `· no result` is dropped two columns early — at 33–34 columns, where
  measuring once leaves the call 12–13 cells — and a cut stopped call ends
  two cells short of the edge (37 of 39). A running call on a member's
  second row is cut two cells short the same way (78 of 80).
- **The Esc frame's member rows still reserve five cells for an elapsed time
  that no longer ticks**: in the one-row layout `· no result` is dropped at
  46–48 columns only because of it (measured with the reserve set to 0: it
  stays), and in the two-row layout a cut label is cut three cells shorter
  than the room (60 columns, `4s` laid out in `1m00s`'s room).
- **A call id has no length bound** but a line's (`stream.MAX_LINE_BYTES`,
  4 MiB), and the 256-call window keeps the id of every call it holds: 1 MiB
  ids from a hostile child kept 252 MiB alive (28 MiB at round 1's 32),
  beyond what the trail's 200 entries hold; projecting the window takes
  26 µs a publish (5 µs at 32). Clipping `call_id` where it enters the
  reducer would bound it.

## Rejected

- *A one-row table (layout B) and three rows per child (layout C)* — rendered
  for the owner, who chose A.
- *A per-child idle field on the contract* — the arrival time of the last
  snapshot is the same fact, and it costs no contract change.
- *Drawing from the publish path with a time check* — the publish path is the
  child's stdout pump; the redraw is scheduled instead (D6).
