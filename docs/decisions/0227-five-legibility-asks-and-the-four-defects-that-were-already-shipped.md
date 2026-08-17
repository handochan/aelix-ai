# 0227. Five legibility asks, and the four defects that were already shipped

Status: Accepted (2026-08-16).
Date: 2026-08-16
Relates: ADR-0153 (the first user echo), ADR-0159 (the statusline badge invariant),
ADR-0163 (the picker frame), ADR-0194 (the synchronized-update bracket),
ADR-0199 (the batch surfaces S10, and the modal's bottom truncation),
ADR-0213 (the pyright gate), ADR-0219 (the 120-cell render ceiling),
ADR-0224 (text-pinned citations).
GitHub: #48.
Pi: **read, at v0.79.9, which is NOT the pin.** ADR-0034 pins `v0.74.1` / `734e08e`;
ADR-0178 records the pi repo going private. Every pi citation below is labelled 0.79.9
and may postdate the pin.

The owner reported five things about the TUI that were hard to read. Four of the five
were what they looked like. The fifth was not, and neither were three of my own
premises about how to fix them.

## What was asked, and what each turned out to be

**A panel does not separate from the transcript around it.** Measured cause: the twelve
in-flow modals — `/settings`, `/model`, `/thinking`, `/login`, `/logout`, `/trust`,
`/resume`, `/scoped-models`, `/statusline`, `/stats`, `/extension`, spawn consent — are one
function, `tui/context.py`'s `_picker_frame`, and its top and bottom dividers carried
`_PICK_DIM`. That is SGR 2, the faintest attribute a terminal has, on the one element
whose entire job is to say where the panel starts and stops. The rule is now cyan and a
single-row title rides it.

**The user's own turn does not stand out from the agent's work.** ADR-0153 gave the echo
a leading blank and bold cyan; a later sprint added a trailing blank because one was
"too subtle when the turn landed mid-stream". Both rounds bought separation with NEGATIVE
space, which is what a human turn was already made of. It now has its own ground: a
full-width bar. The colour was chosen by the owner from eight candidates rendered in
their terminal, because no measurement here substitutes for their background and theme.

**The multi-agent panel does not say what each agent is doing.** True, and it could not:
one `agent()` call is one profile and one model across N tasks (ADR-0199 S3), so every
per-member field on a snapshot is IDENTICAL across the batch. `model` led each row and
the comment justifying that said so out loud — "it is the same for every member of a
one-profile batch, so the column is naturally uniform-width". A column identical on every
row identifies nothing. The submitted task text is the only differing fact and it stopped
at `extension.py`.

**The multiline statusline is three rows where two would do.** Confirmed and merged, with
the permission badge still leading its row (ADR-0159's invariant).

**The resume list does not say what a session contains.** Confirmed. Both pickers rendered
`{created} · {short-id}` — an identifier, not a description — from two separate copies of
the same function, one of which had no tests at all.

## Three of my own premises did not survive measurement

**"Show the LAST user message", which is what was asked for.** Measured over the 224 real
sessions in this repo's session folder: sessions END on a short follow-up, so the twenty
most recently active ones close with `source .venv/bin/activate`, `docs`, `hi`, or
nothing. The first turn states the task. pi agrees — its row is
`session.name ?? session.firstMessage`
(`modes/interactive/components/session-selector.js:368-373`, 0.79.9). The last message is
not dropped: it renders in the `/resume` detail panel, for the row the cursor is on, which
`select(detail=...)` already evaluates lazily per render.

**"The relative time comes from the file's mtime."** pi's `modified` is the newest MESSAGE
timestamp, with `stats.mtime` only as the last-resort fallback
(`core/session-manager.js:412-418`, 0.79.9). mtime moves for reasons that are not
conversation activity. aelix's JSONL already carries a per-entry `timestamp`, readable
from the tail.

**"Previewing every session will be too slow, so it must be lazy."** Measured across those
224 sessions: first message by head scan 156 ms (0.70 ms/file), last message by a 128 KB
tail window 27 ms, newest entry timestamp 20 ms. pi needs a concurrency-10 loader and a
progress bar only because `buildSessionInfo` streams every file end to end for a message
count; nothing here reads a whole file.

## Four defects found on the way, none of them in the asks

**The user's bar was wider than the prose it sits above.** Shipped in this same branch and
found by re-reading the plumbing rather than by anything failing. `Padding` resolves its
width at PRINT time from the console it is handed, and `chrome._console` is a bare
`Console` — raw terminal columns — while every other renderer is laid out against
`terminal_columns(...)`, capped at 120 by ADR-0219. Measured on the emitted SGR: 40 / 80 /
120 / **200** cells at those console widths.

**`panel._flatten` bounded rows in codepoints, not cells.** A Hangul syllable and an emoji
occupy two terminal columns and one character. Measured, eight members with `current_tool`
set to `"한" * 60`: every row came out at 78 characters — inside the ceiling, nothing
dropped — and 125 CELLS, nine list entries wrapping into SEVENTEEN screen rows at 80
columns. `PANEL_MAX_ROWS` is not layout: the widget is the one surface with no downstream
bound, and `current_tool` is set from the CHILD's own stdout JSON. `tool.py` had already
fixed its own half of this (its finding M1) against `rich.cells`, and `tool.py` IMPORTS
`_flatten` from `panel.py` — the fix was half-applied.

**The startup `--resume` menu printed every session in the folder** — 224 lines for this
repo, scrolling the prompt away — and its range check was `1 <= choice <= len(sessions)`,
so a number past the end of that scroll opened a session the user was never shown.

**An over-wide picker title would have overrun the new rule.** `width` is clamped to 78
while the title is not bounded at all — `ExtensionUIContext.select` takes whatever an
extension passes — so a long label riding the rule makes the top rule overrun the bottom
one by its own excess: 204 cells against 40, measured. It falls back to its own line.

## The gates were wrong three times, in three different shapes

Every one was caught by sabotage and none by review, and they are recorded together
because the shapes do not resemble each other.

**A gate that asserted a no-op.** The over-wide-title test above began life gating a
`max(0, ...)` clamp. Removing the clamp left it GREEN, because `"─" * -164` and `"─" * 0`
are both `""` — the clamp was standing in front of nothing. Looking at what it was
standing in front of is what found the real defect. The clamp stays, now saying plainly
that it catches no live case.

**A gate whose input was outside the observable band.** The wide-character panel test
first used `"한" * 60` and three of four sabotages stayed green: at that length the row is
long enough in CHARACTERS to trip the OUTER truncation, which is cell-accurate and quietly
repaired the breach the inner check let through. Measured against the sabotaged build, the
worst row is at 37 syllables, 115 cells. The test now sweeps 2..59. Separately, the
aggregate's append-while-it-fits join is invisible to a width assertion at all — the final
cut clamps whatever it admits — and what it actually buys is that the drop is a WHOLE
segment: measured at four syllables, the fixed build ends `· 1 queued` at 72 cells and the
sabotaged one appends `999s` and slices it to `· 99…`.

**A gate whose fixture never reached the code path.** The session-label test claiming to
pin a structural role check used an assistant turn saying the word `"user"`. `json.dumps`
escapes those quotes to `\"user\"`, so the byte prefilter never matched the line and the
role check was never reached; deleting the check left it green. Replaced with a tool call
carrying `"role": "user"` in its ARGUMENTS — the shape every session that edits this code
actually contains.

A fourth, adjacent: two sabotages on the bar's width only became RED after this work grew
two more tests. `width=None` means unbounded and the unit tests need it, so "a caller
forgot" was silent by construction — dropping `width=` from the steer echo and from replay
left the ENTIRE suite green.

## The instruments were wrong before the code was, four times

A `Constrain` wrapper broke two test flatteners that fell through to `str()` and reported
`<rich.constrain.Constrain object at 0x…>` for renderers that were correct. A new smoke
test spied only `print_above` while the output pump batches through `print_above_many`, so
it timed out rather than failed. Its fixture carried a trailing space that
`parse_input_line` strips, so the wait it timed out on was not the one under test. And the
live panel probe's first run matched the startup banner's `╭────╮` and reported three
failures about a box this work does not touch.

Each was found because the probe was built to report why it found nothing, rather than to
report nothing.

## An adversarial review found twenty-four more, and eight were mine

The branch was reviewed before merging by 33 agents over six independent lenses,
each finding then handed to a skeptic whose job was to REFUTE it. 27 claims, 24
survived. Recording them here rather than quietly fixing them, because the
pattern in them is the useful part: **the changes above replaced bounds that
worked with bounds that read better.**

**Two cell-width caps were not caps at all.** ``cell_len`` and ``get_cwidth``
both return 0 for a combining mark, so a string made of them has no width and a
cell budget never fires. MEASURED against the shipped branch: 200 000 combining
acutes went into ``panel._flatten`` at a limit of 78 and 200 000 came out, and the
same into ``session_labels.truncate_cells`` at 74. The commit above congratulates
itself for replacing a character count with a cell count; what it actually did on
that input was replace a hard cap with none. Both now bound in BOTH units, and
the codepoint backstop is deliberately SLACK rather than equal to the budget —
setting it equal silently removed the ``…`` from every ordinary truncation, which
a pre-existing test caught immediately.

**A new module was written without C1.** ``session_labels._clean`` was ported from
pi's ``/[\x00-\x1f\x7f]/g`` and kept its exact set. ``\x9b`` IS a CSI — the
one-byte spelling of ``\x1b[`` — which ``panel._CONTROL_KILL`` in this same repo
already carries, with a docstring saying why. A session JSONL is
attacker-influenceable, so the label was an escape-injection path.

**The user echo shipped the same hole.** ``Text`` does not interpret escapes but
does not remove them either. MEASURED at width 40 with a pasted
``error: \x1b[31mFAILED\x1b[0m in test_x``: 23 of 40 cells painted and 17 not, the
user's own reset ending the bar mid-row, and the raw escape reaching the glass.
Pasting coloured build output into a coding agent is ordinary input; the same text
is also persisted and replayed by ``/resume``. (This paragraph said "17 painted"
in three places — here, ``render.py`` and a commit message — until the round-three
review re-ran it. 17 is the complement.)

**And the picker title.** ``ExtensionUIContext.select`` takes whatever an
extension passes, and an OSC title both SET THE TERMINAL'S WINDOW TITLE and made
the top rule 32 visible cells against the bottom rule's 40 — because
``_visible_len`` strips SGR only while prompt-toolkit consumes every escape. The
title is sanitised now; the BODY deliberately is not, because those rows are this
module's own styling.

**Ask 1 broke itself in ask 5.** ``/resume`` sized its rows from the terminal
while ``_picker_frame`` clamps its rule to 78: measured, a 120-column terminal put
114-cell rows inside a 78-cell rule, so the session list hung 36 columns past the
coloured boundary this branch added in order to make that boundary visible.

**The panel row was padded to the ceiling, and the widget window clips.** Over
20 000 composed rows the observed width set was literally ``[78]``. ``Window`` is
built without ``wrap_lines``, so a row wider than the terminal is CUT, and the cut
takes the right-hand end: at 72 columns a queued member rendered as
``▌ [4/4] write the ADR`` with the word ``queued`` gone, where main's left-packed
rows read ``[4/4] queued`` intact down to 30. The column is now sized from the
LABELS — which are fixed for the life of a group — and the row then ends. The same
budget-sharing also made four distinct job labels converge to a common prefix the
moment work started, because the label's share shrank as the numbers grew.

**Two more, smaller.** Appending the model to a head documented as "never
dropped" did not shorten the head, it truncated the state COUNTS — at a
31-character model id the count it reached first was ``1 failed``. And merging
``current-dir`` into the permission row put the only UNBOUNDED segment in the
middle of a height-1 row that clips: measured at 80 columns with a nested cwd the
row is 95 cells and the two segments pushed off were ``⏵⏵`` steering and
``⋯ N queued``, the only live signals on the footer. The path goes last now.
(Both of these needed a second correction; see the round-three section.)

**The citation gate agreed with a lie.** Nine citations naming ``_picker_frame``
carried an anchor locked to a line inside ``_filter_line``, so ``--fix`` would have
moved a wrong pointer to new coordinates — verbatim the failure ADR-0224 measured
at 24% of the corpus. Those nine were re-derived BY HAND from the prose; the rest,
whose anchors were correct, mechanically.

## Round three: the fixes' fixes, and five gates that gated nothing

Two review lenses ran late — one that sabotages every new test in a throwaway copy
of the sources, and one that re-measures every number in the prose. Between them
they found a product REGRESSION that survived two rounds of review, five gates that
could not fail, and four sentences that were simply not true.

**The job column's cap made every row of a fan-out read the same.** A constant
24-cell ceiling was chosen from one measurement at a 40-column terminal, where it
let 2 of 4 rows keep a full state word against 0 of 4 at 28 and above. What was
never measured is its cost at every other width. A batch fanned out over one verb
— ``port the retry backoff to google`` / ``… openai`` / ``… azure`` / ``… vertex``,
32 cells each — truncates to a common 24-cell prefix, so all four rows render the
SAME string. MEASURED: **1 distinct label with the cap, 4 without it**, on an
80-column terminal where 31 columns sat empty while the labels were elided. The
cap is gone; the column is sized from the labels and reserves 12 cells for the
numbers, which is where the state word lives.

**And the reason the state was scarce was not the labels.** Chasing the cap turned
up the real consumer: with one QUEUED member the header's model term lands at 77
cells against a cap of 76, so the previous round's "only if the substance still
fits" guard dropped it — and the panel's only fallback is to let every row carry
the model again, spending 28 cells on a string identical down the whole column to
save 31 in the header. The model is appended unconditionally now. Position is what
protects it: it sits between the profile and the counts, and everything that
shortens that row takes from the right, so the truncation reaches the COUNTS
first — which the panel's own rows already state, member by member.

**Both codepoint backstops returned one cell more than they promised.** With four
codepoints per cell — a base character carrying three combining marks — the slice
can land exactly on the budget, and both functions then appended their ellipsis
OUTSIDE it. End to end that produced a 79-cell row inside the 78-cell picker rule,
verbatim the overhang the branch added the rule to make visible. A sweep over
every budget from 1 to 89 now runs in both test files.

**Five of the eighteen new gates could not fail.** Each in a different shape, and
the shapes are the point:

* a bound with **no coverage at all** — deleting ``_MAX_INPUT_CHARS`` outright left
  1602 tests green, because the test fed ``"x" * 4_000_000``: a whitespace-free
  ASCII run is the cheapest input there is, and its 12 ms sat 17× under a 200 ms
  threshold. Counted rather than timed now, on a non-ASCII flood, with an EQUALITY
  against the literal bound so a broken instrument fails as loudly as an absent one;
* a **fixture that never reached the code path** — 200 000 combining marks are
  1.2 MB of JSON-escaped bytes against a 64 KB head window, so the reader gave up
  mid-record and the row under test was the constant ``(no messages)``, identical
  in both arms. The count is now 10 000, and the first assertion is that the
  fallback string is absent;
* an **input outside the observable band** — 14-cell labels against a 23-cell and a
  24-cell budget, both wider than every label, so nothing was ever truncated and
  the arm under test passed unchanged;
* an assertion on **the last token a truncation would ever reach** — ``1 failed`` is
  the second of four counts and the cut comes from the right, so it was true in
  both arms at every id length; and the companion cell bound was ``78 <= 78``;
* two bounds **expressed in terms of the constant they constrain** — with
  ``_ZERO_WIDTH_SLACK`` raised to 100 000 both flood tests still passed while the
  functions handed back all 200 000 codepoints.

**And an ordering invariant that held in the tuple and not on the screen.** The
gate for "``current-dir`` goes last" read ``_MULTILINE_ROWS[1]`` and never built a
context. Extension statuses are not registry segments; they join the last row
AFTER it is composed, so appending them to the joined string put the unbounded path
back in the middle. Measured on the glass at 80 columns, what the clip took was the
extension's own status, entirely. They are inserted before the path now, and the
gate renders a row.

**Phase C now has a live-glass file.** ``tests/tui/test_phase_c_live_glass.py``
paints the panel and the merged footer through the real chrome and reads them back
off a pyte screen, because both surfaces are decided by a CLIP rather than by a
return value: a formatter can return four distinct labels and the terminal can
still show four identical ones. Eighteen sabotages — fourteen against the source,
four against the glass — all RED.

One of those eighteen was reported GREEN the first time and the mutation had not
applied: a ``str.replace`` missed because the function it targeted had gained a
docstring. That is the same failure as a probe whose pattern never matches — zero
looks identical to "no defect" — so the harness now asserts every mutation
actually changed the file before it draws a conclusion.

**The control strip took the tab as well as the escapes.** ``\t`` is C0, so the
map that closed the escape hole mapped it to a space — and every nesting level of
a tab-indented paste (a Makefile, Go, tab-indented C) rendered as ONE column. The
loss is permanent rather than cosmetic: the echo is replayed from the session file
on every ``/resume``. Neither harm the strip exists for is a property of a tab.
MEASURED at width 60: Rich expands it to its 8-column stop BEFORE writing, so the
spaces are painted inside the background run, no raw ``\t`` reaches the terminal
in either arm, and every bar row is still exactly the full width. It is spared
now, alongside the newline and for the same reason.

**One of my own probes reported a false finding**, again. ``verify_round2.py``
hard-coded the pre-fix example string, so it reported 29 cells against a cap of 24
where the code already said 21: measuring the wrong build, which is the same
mistake as trusting a gate that never ran against the broken one.

## Rejected alternatives

**Putting the batch model into `format_aggregate_status`'s own output.** That string is
ALSO the shared height-1 statusline row, bounded at `AGGREGATE_MAX_CHARS = 78`, and S10's
own worked example is already 74 of those characters — four of headroom against a
fourteen-character term. The panel passes `extra_head` instead, so the statusline is
byte-identical and the two surfaces still cannot state a DIFFERENT fact.

**A second header row for the panel, or a third rule on the picker.** Row COUNT is the
measured flicker path: `chrome.py:676-688` records an attempt at gating widget rows that
was REVERTED for making flicker worse, and the panel shares its window with the live
thinking stream. The picker is worse — it does not scroll, it BOTTOM-TRUNCATES, and
ADR-0199 measured that at eight members the hint, the closing rule, the counter and the
last option (`build_options` guarantees it is `Cancel`) are simply not drawn. Both changes
here move row count down or not at all.

**Inlining a multi-row title into the rule.** The spawn-consent dialog passes a NINE-row
title and `aelix_agents/consent.py` writes its height budget down as
`title_rows + option_rows + 4`. Nine rows cannot ride a rule and quietly changing that
arithmetic is how `Cancel` goes off screen.

**A `.gitignore` rule for the pytest output file this branch accidentally committed.** A
rule matching the one name I happened to pick reads as a guard and stops nothing. The
artifact belongs in the scratchpad; the file was removed from the branch's history.

## Residuals

* **`_picker_frame` is content-sized, never terminal-sized.** It does not import
  `tui/width.py`, so at 40 columns today's rule is already clipped and the hint loses
  `· Esc cancel`. Pre-existing and not made worse — the rule is exactly as wide as it was
  — but it is the natural next change and it is not in this one.
* **Four modal APIs bypass the frame entirely**: `ctx.confirm`, `ctx.input`, `ctx.editor`
  and `ctx.custom` never call `_picker_frame`, so they gained no rule.
* **`PANEL_ROW_MAX_CHARS` is a fixed 78**, so the batch panel stays a 78-cell ribbon on a
  200-column screen. The same is true of the aggregate, deliberately, because that one
  shares a row.
* **`current_tool` is empty for as long as a child waits on its provider.** It is cleared
  on `turn_start` and set only on `tool_execution_start` (`stream.py:666`, `:673`), so the
  "what is it doing" column was blank for the whole first leg of every child turn. The job
  label does not fix that; it makes it matter less.
* **Committed scrollback is never reflowed.** A bar committed at one width keeps it across
  a resize. Those bytes belong to the host terminal — the app is not full-screen — which
  is the same guarantee every other terminal program offers.
* **In `chain` mode the job label is the UN-SUBSTITUTED template.** `{previous}` is filled
  per step at run time, so the column is a label, not the prompt the child received.
