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

WHAT THE ROW COSTS, measured against `main` rather than against an intermediate state of
this branch, because that is the comparison a reader of main's history needs. On main the
block is three rows and `current-dir` has one to itself: at 80 columns the path renders
whole (73 cells) and an extension status joins the LAST row — `permission-mode` /
`steering` / `pending-queued`, 50 cells — with no unbounded segment in front of it, so
nothing is clipped. Merging the rows is what put an unbounded segment on a shared height-1
row for the first time. After both ordering fixes below, the merged row is 79 cells at 80
columns: every live signal and the extension's status are complete, and the path is cut at
`📂 /workspaces/aelix-ai/p`. That is the trade the ask buys — one row of the user's
scrollback for the tail of a path their own shell prompt already shows. It is a trade, not
a free win, and the earlier framing of it here (a 95-cell row losing `⏵⏵` and `⋯ N queued`)
described a state that only ever existed inside this branch.

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
middle of a height-1 row that clips: measured at 80 columns with
``cwd=…/aelix-coding-agent/src/aelix_coding_agent`` the composed row is 106 cells
against 79 on the glass, and the two segments pushed off were ``⏵⏵`` steering and
``⋯ 3 queued``, the only live signals on the footer. The path goes last now. (The
number was written down as 95 for an unnamed cwd; re-measured with the cwd named,
it is 106. Which segments were lost was right.)
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
32/32/31/32 cells — truncates to a common 24-cell prefix, so all four rows render
the SAME string. MEASURED: **1 distinct label with the cap, 4 without it**. The
cap is gone; the column is sized from the labels and reserves 12 cells for the
numbers, which is where the state word lives.

**What the cap was buying was not empty space, and this section said it was.** The
first draft claimed the capped rows left "31 columns sitting empty while the labels
were elided". Re-measured on the same fixture at 80 columns, the capped build's
rows were **78/78/78/40 cells** — full width. The elision was buying the NUMBERS:
those rows carried ``running · read_file · 33s · 12.3k tok · $0.0372`` where these
carry ``running · read_file · 33s · 12.3k t…``. Removing the cap is a trade and
per-member cost is its price. It is still the right way round — four rows a user
cannot tell apart answer nothing at all — but the ledger belongs in the record,
and the docstring that claimed a free win now states it.

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
  functions handed back far more than the budget. The number in the comments was
  wrong on one of the two: ``truncate_cells`` does hand back all 200 000, but
  ``panel._flatten`` is capped at 8 192 by ``_MAX_INPUT_CHARS`` first, so its
  sabotage returns 8 192. The gate was still unfalsifiable; the sentence
  describing it was still not a measurement of that function.

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

## Rounds four and five: two external reviews, and the triage that made them usable

Two reviews arrived from outside this session, both written against `6ee6146` —
three commits behind. Rather than accept or argue with them, every claim was
re-measured against HEAD **and** against `main`, and sorted into four buckets. That
sort was worth more than any individual finding, because "real" and "this branch's"
turned out to be different questions for most of them:

| | | |
|---|---|---|
| **LIVE-NEW** | 9 | this branch caused it — all fixed |
| **LIVE-PRE** | 8 | real, but `main` does it too — not a gate for this branch |
| **FALSE** | 5 | did not reproduce |

**The two that mattered most were P1s and both failed toward a wrong answer.** One
malformed record — a text block holding a NUMBER — reached `str.join` and raised
`TypeError` out of `session_choice_label`, whose own docstring promises it "degrades
… never raises". The pickers call it in a loop, so ONE bad file took away the user's
whole session list, the good sessions with it, at exactly the moment they were
recovering work. And `rich.cells` turned out to be inexact twice: `set_cell_size`
mis-measures a string led by U+200D, and `cell_len` is not additive over grapheme
clusters (`❤️` is 1 by the per-character sum and 2 as a string), so the accumulate
loop written to fix the first returned 9 cells against a budget of 5.

**Three more were the same shape as ask 4's:** a feature added a constraint that
made a pre-existing disagreement start to hurt. The 20-row cap on the startup menu
was applied to a list ordered by `created_at` while every row is labelled with
last-ACTIVITY age — measured, a session used five minutes ago sat at position 25 of
25, was cut, and the footer called it "older", where `main` had listed it. Two
window reads gave up on a record larger than their window instead of re-reading it,
each with a one-byte cliff, and each failing stale (`10mo` for a session used four
minutes ago) or blank (`(no messages)`, byte-identical to an empty session, for the
folder's longest question).

**And the panel never saw the terminal.** The job-label column was sized against a
fixed 78 while the widget window paints into whatever the terminal has, so a long
label spent cells that did not exist. Rows still showing a state word, 35-cell
labels: `main` 1/4 1/4 4/4 4/4 at 30/40/50/60 columns; this branch 0/4 0/4 2/4 4/4;
with the real width plumbed through, 4/4 at every one. No constant could have fixed
it — the right number is the terminal's, which is what ADR-0219 already established
for every other surface and this one missed.

### What the sort saved

Five claims were **refuted with positive controls**, and each control is the reason
to believe the refutation. The batch-widget ownership race needs two groups open at
once; `agent` declares `execution_mode="sequential"`, and the check ran the same
probe with `execution_mode=None` to show it CAN reach two before showing that the
real mode reaches one. The UI-rebind cache staleness needs the bridge to survive a
rebind; it is rebuilt every time, shown by running the real loader twice against one
held extension — after the first control was discarded as invalid. `/reload` loading
project-local extensions before the trust gate is false because the gate is inside
discovery, not at the call site: proven over a pty with a marker file, and with a
GLOBAL marker as the control that proves the reload really did re-discover.

### And three of my own new sentences were wrong

The `_picker_frame` comment stated three things as MEASURED — that an OSC title set
the terminal's window title, that it made the top rule 32 cells against the bottom's
40, and that prompt-toolkit consumes every escape. All three are false. **The
correction was also wrong**: it replaced them with a rule-delta table produced by a
probe that pre-translated the title before computing the width, i.e. measuring a
build that does not exist — the same error as testing a gate against the wrong tree,
one call deeper. The rules are equal in every escape class and both title
placements, because both come from one number. What the sanitiser buys is control
bytes deleted; what it does not buy is now written down beside it.

The new `_cut_to_cells` docstring said `set_cell_size` is inexact "for zero-width
characters"; measured, U+200B, U+0301, U+FE0F, U+200E and U+00AD are all exact and
it is U+200D specifically. Over-broad prose is not harmless here: a reader hardening
"zero-width" would guard five characters that were never broken and might conclude
from three clean tests that the guard was pointless.

## Round six, after the merge: the two commits nobody had reviewed

The branch shipped with `b1513bf` and `f288fdf` unreviewed, so a 54-agent adversarial
pass ran against them after the merge. It returned **19 confirmed findings**. Nine
were already closed by commits made after the reviewed range — the `/resume`
collision suffix budgeted in codepoints (four separate findings, all closed by
`9472e70`), the rows losing their state word when the members disagree, and the
40-column width regression. What remained is one behaviour and a column of numbers.

**The state counts were not "never dropped", and the docstring said they were.** The
panel header appends the batch model to the head so the rows can drop it. That append
was made UNCONDITIONAL in `b1513bf`, on the reasoning that position protects the
counts: the model sits before them and everything that shortens the row takes from
the right. The closing truncation takes from the right too. MEASURED at cap 76 with
`github-copilot/gpt-5.6-codex` and three count classes, the header rendered
`… · 2 running · 1 done · 1 queu…` — a count cut mid-word, with the elapsed, the
tokens and the cost gone ahead of it. At nine members and five count classes the cut
ate `1 queued` entirely while `PANEL_MAX_ROWS` dropped the queued member's own row,
so **the word appeared nowhere in the panel** — precisely the misreading
`_queued_line`'s docstring exists to prevent. Two paragraphs of the same docstring
contradicted each other about this, one claiming the counts are never dropped and the
other correctly saying the truncation reaches them first.

Both earlier rules were wrong in opposite directions, so the third is neither: the
model is now sized against what the head and the counts leave (`_model_term`), shown
truncated when it does not fit whole, and omitted below 12 cells — at which point the
rows carry it again, which is a fallback rather than a hole. Verified on a pyte glass
at 40, 80 and 120 columns across all three arms — the header truncating the model, the
members disagreeing so the rows carry it, and the five-class batch where it is dropped.

**And the sentence I wrote for the fix was the same overclaim as the one it
replaced.** The new docstring said the counts "really are never dropped". Swept
before shipping it: the closing truncation still reaches them when the head and the
counts ALONE exceed the cap — at 76 cells, all five classes non-zero, a 5-cell
profile survives to 495 members and is cut at 4 995; a 16-cell profile name is over
at **5**. What the fix actually establishes is narrower and worth stating exactly:
the MODEL can no longer displace a count. Both remaining shapes need a batch S3 does
not produce, but the bound is arithmetic and a docstring that promises past it is
the defect this round was called to fix.

**This was caught only because the harness lied first.** The sabotage driver
restored `panel.py` from a stale backup, so the "counts intact" sweep ran against a
build with the unconditional append still in it and reported the fix failing
everywhere. The probe that found the real boundary now asserts a known-fixed input
returns the fixed answer BEFORE it measures anything — a build check, not a result
check. A restore that reports success is not evidence the tree is the tree you
think it is; the previous rule in this ADR was that a mutation must be confirmed to
have APPLIED, and its mirror is that a restore must be confirmed to have LANDED.

**And the seam that decided which surface names the model was a substring test.**
`format_panel` asked "is the model in the rendered header?" — a proxy for the
arithmetic the header had just done, and one that disagrees with it as soon as the
header shows the model truncated: the rows read that as absent and both surfaces
spend the width. It is now the same pure function, called twice. The gate that
covered this could not see it either; it swept three id lengths whose longest sat
exactly on the 32-cell ceiling, so it never asked what happens above the bound.

**The `state_first` fix from round five shipped with no gate at all.** It is the fix
for the highest-severity finding in this round, and nothing in the suite pinned it —
found by sabotage, not by reading. Gated now, with a positive control that the
members really do disagree.

**Seven numbers in the prose were wrong**, and one correction was wrong in the
useful direction. The four fan-out labels are 32/32/31/32 cells, not "32 cells
each". The 12-cell numbers reserve holds `starting`, the separator and the ellipsis
— **nothing** of the next term, where the docstring claimed a start of one. The
batch model on the rows is worth 31 cells, not 28. The merged statusline row is 129
cells, not 132, and the same file already said 129 fifty-eight lines away. The
`test_context.py` fixture publishes an extension status, so its "106 cells" is the
row without one. And the flood comment claimed `_flatten` hands back all 200 000
codepoints under a raised slack; it hands back 8 192, because `_MAX_INPUT_CHARS`
caps the input first — the sibling claim about `truncate_cells`, which has no such
cap, is the one that is true.

The useful correction is the one about empty space: the capped build's rows were
78/78/78/40 cells, not rows with room to spare, so removing the cap spends the
numbers rather than recovering waste. Per-member cost is the price of four
distinguishable labels. That is still the right trade for a panel whose purpose is
to say which member is doing what — but it is a trade, and claiming a free win hid
a real cost from whoever reads this next.

## Round seven: the round-six fix named the model nowhere

`bd86a7b` went to an adversarial review before it went to `main`, and two lenses
independently found the same thing: between roughly 40 and 70 columns the panel
named the model **nowhere**. The header dropped it (`room < _MODEL_MIN_CELLS`) and
the rows, which the docstring called "the honest fallback", printed four characters.
Measured across the review's sweep: 4 266 configurations where `bd86a7b` shows no
model prefix of 8 cells or more anywhere in the panel while `2ba3261` did, and none
the other way; in 85% of the drop cases the widest prefix any row showed was **≤4
cells**.

**The fallback never existed.** The label column has already taken the rows' width,
so the header's remaining room beats a row's at every width from 54 up, and below 52
a row has TWO cells. What the row was actually spending those cells on:

```
width 50   rows carry it   running · g…            rows drop it   running · r…
width 62   rows carry it   running · github-co…    rows drop it   running · read_file…
```

`read_file` is the answer to "what is this member doing" — the question the panel was
added to answer, and the owner's third ask. Four characters of a provider name is not
an answer to anything. So one batch model is now the header's to state or nobody's,
and the rows spend the cells on the tool. This also deletes the `shown_in_header`
seam and with it a claim that "asking it twice is cheaper than a scan", which the
review timed at **3.0-5.2× more expensive**.

**Two gates asserted things that were false.** `test_the_model_is_shown_by_somebody_at_every_id_length`
pinned "somebody names it" using the default width 78 — the one width where it holds,
while production passes `tui.width.terminal_columns`. And the ordering assertion
`header.index(shown) < header.index("1 done")` passes on a header that names no model
at all, because `str.index("")` is 0. A third, `_named_model`, searched the whole row
including the job LABEL — so a batch whose task says "migrate the
github-copilot/gpt-5.6-codex path" reported a model the row had correctly suppressed.

**And the central rule had one gate.** Reverting `_model_term` to "show the whole term
or nothing" — the rule this ADR says it replaced — left all 70 unit tests green; only
the pyte-glass test went red. The truncating branch is the fix, and nothing pinned it.

**The boundary sentence was wrong twice.** "Survives to 495 members" was replaced with
"first cut at 484", and both are samples of a scatter: the spine is the head joined to
the five counts, the counts are decimal, so their width depends on how the members
SPLIT across classes and not on how many there are. Measured, some splits of 170 are
already over the cap and some splits of 600 still fit. The clean statement is at the
other end — a 16-cell profile name is over at five members — and that is what the
docstring says now. Also corrected: the `_MODEL_MIN_CELLS` docstring illustrated its
12-cell floor with `github-copil…`, which is 13 cells and therefore a string the
constant can never produce.

## What the two rounds actually bought

Rounds six and seven are written above through their defects, because that is what
each round was called to fix. The ledger is worth stating on its own, since the
review's HIGH finding measured only the model axis and read a deliberate trade as a
pure loss — and a reader of this ADR would otherwise inherit that framing.

Swept over 6 720 configurations (widths 24-78, 2/3/4/5/9 members, 2-5 count classes,
four model ids, three profile names), `main` today against `2ba3261`, scoring how
many state-count classes the header spells in full:

| | |
|---|---|
| HEAD shows MORE complete counts | **4 298** |
| HEAD shows FEWER | **0** |
| equal | 2 422 |
| configurations with NO complete count at all | 4 300 → **1 520** |

That is what the model term was taking. Against it: below roughly 70 columns the
panel no longer names the batch model, which the residuals record and which is the
one place these two rounds are worse than what they replaced.

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
* **Below ~70 columns the panel does not name the batch model at all**, so two batches
  on different models paint a byte-identical panel there. MEASURED: an
  `anthropic/claude-opus-4-5` batch and an `anthropic/claude-haiku-4-5` batch are
  identical at 40 and 60 columns and differ from 78 up. This is the round-seven trade
  taken deliberately — the alternative measured out as four characters of a provider
  name on every row, bought with the tool name — but it is a real loss and not a
  neutral one, and it is the residual most likely to be worth revisiting.
* **How much of a 28-cell id the header shows is decided by the COUNT CLASSES, not by
  the terminal.** The panel is a 78-cell ribbon by design, so widening the terminal
  changes nothing: `github-copilot/gpt-5.6-codex` renders whole at two classes, loses
  its last cell at three (`github-copilot/gpt-5.6-cod…`), is down to
  `github-copilot/…` at four and is dropped at five — identically at 78, 120 and 200.
  A first draft of this bullet said "never shown in full at any width", which is the
  same mistake as the boundary sentence above: quoting one fixture as a ceiling.
* **The production floor is 12 cells and the tests' "does this identify the model"
  floor is 8.** Both are defensible on their own axis — 12 is what a header term needs
  to be worth a term, 8 is what a prefix needs to be attributable to an id rather than
  to prose — but a reader will notice two numbers for one question, and nothing forces
  them to move together.
* **Per-member cost is gone from the panel rows**, and the aggregate cost is dropped
  whenever the model term takes its place. Both are consequences of the label column,
  measured and accepted rather than fixed: the alternatives are a cap that makes a
  fan-out's rows identical, or a model repeated at 31 cells down the whole column. A row
  whose numbers half is 19 cells reads `running · read · 1…`; the aggregate carries the
  batch's cost whenever its own budget reaches it.
* **The row's numbers half cuts mid-token**, where `format_aggregate_status` drops whole
  trailing terms. `12s` becomes `1…`. The ellipsis marks it, so it is terse rather than
  wrong, but the two surfaces shorten by different rules and only one of them is
  written down.
* **`current_tool` is empty for as long as a child waits on its provider.** It is cleared
  on `turn_start` and set only on `tool_execution_start` (`stream.py:666`, `:673`), so the
  "what is it doing" column was blank for the whole first leg of every child turn. The job
  label does not fix that; it makes it matter less.
* **Committed scrollback is never reflowed.** A bar committed at one width keeps it across
  a resize. Those bytes belong to the host terminal — the app is not full-screen — which
  is the same guarantee every other terminal program offers.
* **In `chain` mode the job label is the UN-SUBSTITUTED template.** `{previous}` is filled
  per step at run time, so the column is a label, not the prompt the child received.
* **`truncate_cells` measures with `get_cwidth`, which scores a VS16 emoji at 1 cell**
  where `rich.cells` and `wcwidth` both say 2. Measured, a realistic mixed line —
  eight ordinary emoji among ASCII prose — comes back at 74 by the library the code
  uses and 81 by the other two; a flood of `❤️` gives 74 against 147. **I could not
  settle which matches a real terminal**: the pyte harness that arbitrates every other
  width question here is not usable for it — its ASCII control passes 20/20 but it
  already reports 19 columns for 10 Hangul syllables, so it is wrong before emoji are
  reached. Left alone on this branch rather than changed on a two-to-one vote, because
  `tui/context._visible_len` sizes the picker frame with the SAME library, so the label
  and the rule at least agree with each other today. A follow-up wants one width
  library across both, chosen against a terminal rather than against another library.
* **Three other `set_cell_size` call sites keep the U+200D inexactness**:
  `render._cap_cells`, `render._truncate_lines` and `aelix_agents/tool._squeeze`, each
  one cell over, each identical at `main`. `_squeeze` reaches the glass — a ZWJ-led
  model name gives a 77-cell usage footer against a 76-cell ceiling. The tidy fix is to
  lift `_cut_to_cells` into a shared exact truncator; this branch fixed the one site it
  broke rather than sweeping a pre-existing one.
* **Truncation cuts codepoints, not grapheme clusters.** Measured against `main` the
  branch is strictly better on every shape (ZWJ family 32/40 splits at `main` against
  25/40 here; base-plus-combining 30/40 against 0/40), but the regional-indicator case
  is unchanged and is the one where the cut shows a character the source never held: a
  lone U+1F1F0 renders as a boxed capital K, not as half a Korean flag. 19 of 40
  budgets, identically at `main` and here.
* **`_USER_ECHO_STYLE` emits SGR 30**, the indexed foreground that "bold as bright"
  remaps to bright black — at every `color_system`, with no RGB fallback. The colour was
  chosen by the owner from eight candidates rendered in their own terminal and stands;
  recorded because the *mechanism* is a fact and a future contrast complaint should
  start here rather than re-derive it.
* **`tests/` builds ~40 Rich consoles with a declared `width` and no `height`**, which a
  dumb TERM silently discards in favour of 80. Two tests in `test_event_renderer.py`
  still fail under `TERM=dumb`, exactly as they do at `main`. The two helpers this
  branch relies on declare a height; the rest is a repo-wide sweep and not this change.
