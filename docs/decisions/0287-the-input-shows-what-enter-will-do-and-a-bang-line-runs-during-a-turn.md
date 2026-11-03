# 0287. The input shows what Enter will do, and a `!` line runs during a turn

Status: Accepted (2026-11-03, #596). Option B of the #596 prototype, the owner's
choice (2026-11-02), with the owner's 2026-11-03 decisions: Esc clears the whole
input, a `!` line Entered during a turn runs, the shell colour is magenta through
a new theme role, and the footer under the input is a little dimmer.
Date: 2026-11-03
Issue: #596. The prototype hand-back, with what pi and Claude Code do and the
three options: `.omc/specs/596-proto-report.md`; the grids the owner compared:
`.omc/specs/596-preview-page/`.
Amends: ADR-0184 (the theme file's roles: two more), ADR-0119 (Enter during a
turn: a `!` line runs instead of steering), ADR-0153 (the `❯` marker: the sigil
replaces it in shell mode), ADR-0280 (the chrome is two rows taller; the draft
rule counts the input only; the menu's reserve), ADR-0219 (the input frame stops
at the render ceiling), ADR-0156 (the completion menu draws over rows of its own)
— each by a dated note.
pi: `earendil-works/pi@9c9ce8547` (2026-11-03). Claude Code: the shipped 2.1.314
binary's embedded JS, read by the prototype (nothing recalled).
Tests: `tests/tui/test_input_marks_596.py`, `tests/tui/test_shell_mid_turn_596.py`;
the rows the frame moves in `tests/tui/test_bottom_pin_632.py`,
`tests/tui/test_input_box_height.py` and `tests/tui/test_input_hidden_on_modal.py`.

## Context

The input gave no sign of what Enter would do with a line. `@src/foo.py` read like
any other word, although the completer treats it as a file reference, and `!ls`
looked like a prompt until Enter ran it in the user's shell. Before #596 a `!` line
typed while a turn ran was not run at all: `_accept` handed it to `on_steer`, and
the model got `!ls` as text.

pi derives a bash mode from the text (`interactive-mode.ts:3186`), colours its
editor's border in it, clears the whole editor on Esc in it (`:3136`, after the
streaming check at `:3132`), and runs a `!` line before its streaming-steer branch
(`:3408` before `:3439`). A record made while streaming waits in
`_pendingBashMessages` until the run ends (`agent-session.ts:3973-3976`, flushed by
`_flushPendingBashMessages` at `:4006`), "to avoid breaking tool_use/tool_result
ordering". pi does not colour `@` references. Claude Code turns a leading `!` into
the mode itself: `!` in its `bashBorder` colour replaces the prompt marker, and the
prompt has a line above and below it.

## Decision

1. **Shell mode is what Enter would run.** The input is in shell mode when
   `parse_input_line` classifies the buffer as `bash` or `bash_transient`, so the
   drawing and the submit path cannot disagree (`" !ls"` is a prompt in both). In
   shell mode the typed `!` / `!!` is drawn as the prompt marker in place of `❯`, in
   the theme's `shell` colour and bold. Shell mode wins over the permission colour:
   the permission gate never sees a `!` command. A bare `!` waits for "a shell
   command" after the marker. The status row says what Enter does: `! shell · Enter
   runs it · the model sees the output · Esc to clear` (`!!`: the model won't see
   it), dropped from the least needed until it fits.

2. **The sigil stays in the text.** History, `!!`, paste placeholders, Ctrl+G and
   the submit path read the buffer and are unchanged; only the drawing takes the
   sigil out (`_HideShellSigil`, positions past it shifted). The cursor is kept from
   standing in front of it, so Home and Ctrl+A stop after it, and a click that maps
   inside it is moved past it. Backspace right after the marker deletes the sigil's
   last `!`: `!!` steps down to `!`, `!` leaves shell mode with the text kept.

3. **Esc in shell mode clears the whole input** while nothing runs — pi's, not
   Claude Code's, which removes only the `!`. It is the clear idle Ctrl+C gives a
   non-empty line. During a turn Esc interrupts it, as before and as pi's does
   (streaming is checked first). The three Esc bindings — the completion menu, the
   interrupt, this clear — have mutually exclusive filters; the clear also needs the
   input to hold focus, so a modal's keys never reach it.

4. **An `@path` reference takes the theme's `accent`, by its shape alone** — the
   completer's grammar (an `@` at a line start or after whitespace, to the next
   whitespace, or a quoted `@"…"`; `foo@bar` is not one, a bare `@` is left alone).
   Nothing is looked up while drawing. References are not coloured in shell mode:
   the shell gets `@path` as literal text. Under `NO_COLOR` they are underlined.

5. **A line above and below the input.** The theme's `dim` normally, its `shell`
   colour in shell mode; under `NO_COLOR`, where that colour is dropped, the lines
   are heavy (`━`) in shell mode. The lines and the input stop at the render ceiling
   (ADR-0219: `min(terminal, render_max_width)`, 120 by default), as the transcript's
   user echo does, so the frame and the text inside it agree on a wide terminal. The
   cap is a container that hands its child a narrower `WritePosition`
   (`_CappedWidth`); the prototype's `VSplit` with a filler hung prompt-toolkit's
   width division.

6. **A `!` line Entered during a turn runs now, and the turn goes on.**
   - It runs as an idle `!` does (the `user_bash` hook, then the bash operations),
     concurrently with the turn. While it runs the status row says so; when it ends
     its command line and output are committed together, headed in the shell colour
     with when the model will see it. Committed apart, the answer streaming
     meanwhile landed between them (measured with a real model: 1.4 KB of story).
   - **What the model gets of a `!` command is recorded only once nothing runs**
     — no turn, no command (`/compact`), no extension's turn — so it lands after
     the turn's last message and reaches the model on its next turn. Recorded
     mid-turn, a plain append to the live message list and the session could land
     between a tool call and its result: pi's reason, pi's deferral.
     `handle_user_bash` is split into `run_user_bash` and `record_user_bash` for
     this; its own callers still read a line only between turns.
   - `!!` records nothing, here as at idle.
   - The input loop settles the held records right before it marks a turn running,
     with no await in between, and after each turn; a lock makes "nothing runs"
     hold for the whole write. A record whose session the user left meanwhile
     (`/new`, `/resume`) is dropped.
   - A `!` line during a running command runs now too, and is held the same way.
     Alt+Enter is unchanged: it queues the line as a follow-up message, as pi's
     does.

7. **Two theme roles join ADR-0184's six**, `shell` and `statusline`:

   | Role | `default` | `dark` | `light` | A theme file without it |
   |---|---|---|---|---|
   | `shell` | `magenta` | `bright_magenta` | `medium_violet_red` | magenta (the mode must show) |
   | `statusline` | `grey70` | `grey70` | `grey30` | no colour (its footer as it was) |

   Magenta is Claude Code's `bashBorder` family and the colour Aelix's replayed
   `[bash_execution]` label already has; it avoids the permission modes' yellow,
   cyan, red and green and the accent's blue. pi's green-teal would clash with
   `auto` mode's green.

8. **The footer under the input is a little dimmer.** The theme's `statusline`
   colour is the footer Window's base style, not wrapped around its text: a segment
   that colours itself (the permission badge) keeps its colour, and the `\x1b[0m`
   after it falls back to the base instead of to full-strength default. It is not
   the faint attribute (SGR 2): terminals commonly draw that at about half the
   text's strength (recalled, not measured here — pyte does not model it), which is
   the size of step pi's `dim` footer and Claude Code's take, more than "a little";
   and a faint footer would not show in the grids at all. `grey70` (`#b2b2b2`)
   sits about 9 L* under a `#cccccc` terminal foreground and 19 under `#e5e5e5`; the
   `light` theme's `grey30` (`#4e4e4e`) is 8.6:1 on white. An extension's own
   footer (`set_footer`) is not dimmed — it styles itself. Under `NO_COLOR`
   prompt-toolkit drops colours, so the footer is as before.

9. **The chrome is two rows taller.** On 24 rows, pinned, the input sits on row 19
   with its lines on 18 and 20, where it sat on 20. The bottom pin's draft rule
   (ADR-0280 D1, "a draft counts as one row") leaves the two lines in the chrome
   and only the draft's rows past the first out.

10. **The completion menu has rows of its own** (#508 verify V5, which option B made
    worse). It was a Float hanging under the cursor; a Float adds no height to the inline
    app, so pinned on 24 rows it covered the four rows under the input — the frame line,
    the status row with the shell hint, the footer — showing four entries and no counter.
    The layout now reserves the menu's height while it shows (`_MenuReserve`, at most 8
    rows) and the Float is drawn exactly over those rows (`_ReservedMenuFloat`), still
    under the token being completed. Once the chrome is pinned the reserve is above the
    input: the menu opens upward, the input keeps its row, and the transcript scrolls up by
    the menu's height, as it does for a modal (ADR-0159); after it closes those rows are
    the band ADR-0280 leaves, filled by the next print. Before the chrome is pinned the
    reserve is below the input: a dropdown, with the status row and footer moved down into
    the empty rows. Pin detection leaves the reserve out, as it leaves a modal out.

## Consequences

- Every frame of the live checks — 50x24, 80x24, 120x40, 160x30, 80x24 `light`,
  80x24 `NO_COLOR`; empty, references, `!ls -la`, `!!`, a bare `!`, Backspace and
  Ctrl+A at the sigil, Esc, a long and a multi-line shell line, the completion menu,
  a quoted reference and an email, history recall, Ctrl+G, a paste placeholder, a
  `!` and a `!!` during a scripted turn — fits the terminal, keeps the chrome on the
  last row once pinned, and has the cursor visible on the cell expected.
- With OpenRouter `anthropic/claude-haiku-4.5` streaming a 500-word answer,
  `!echo MIDTURN` printed its output 0.25 s after Enter with the turn still working;
  nothing was steered; the session reads user, assistant, the `bash_execution`
  record, the next prompt; the next answer was `MIDTURN`.
- A 3-line draft under an 8-row reserve now gets 2 rows and scrolls (it needs 9).
- The completion menu, measured at 80x24 and 50x24 pinned with 13 `/sk` entries, 5 `/ex`
  and `/e`, and in shell mode: 8 rows (13 entries) or its full height, directly above the
  frame line; the input on row 19, the status row on 22 and the footer on 23 untouched.
  At 120x50 before the chrome pins: a dropdown under the input, the footer pushed down.

## Limits

- The `default` theme's `statusline` grey is tuned for dark terminals: on white it
  is 2.1:1. The default theme already is (its `tool` yellow); the `light` theme is
  the one for a light terminal. A palette-relative "a little dimmer" has no ANSI
  name.
- A paste placeholder at the very start of the input whose text begins with `!`
  runs as a shell command at submit (as before #596) but is not drawn as shell
  mode: the drawing reads the buffer, the submit path the expanded text.
- A `!` block committed during a turn lands between two lines of the streaming
  answer, as a steering echo does. pi keeps its block in a pending area under the
  stream until the run ends.
- Esc during a turn interrupts the turn, not a `!` command running in it; such a
  command cannot be cancelled, as an idle `!` cannot. Quitting the TUI cancels it.
- `@` completion still runs in shell mode and inserts a literal `@path` into the
  command. A follow-up.
- Alt+Enter on a `!` line during a turn sends it to the model as a follow-up
  message, as pi's does.
- Opening the completion menu on a pinned screen scrolls the last rows of the transcript
  into the scrollback, and closing it leaves a blank band above the input until the next
  print, as a modal does. Its `(n/N)` counter still shows only once the list fits or is
  scrolled to its end (ADR-0156).
- Wrapped continuation rows of the input start at column 0, as before.

## Alternatives

- **Option A** (the marker recoloured, no lines) — the weakest cue. **Option C**
  (pi's frame, the sigil visible after `❯`) — the owner chose B.
- **Esc removing only the sigil** (Claude Code, the prototype) — the owner chose
  pi's clear.
- **Keep steering a `!` line during a turn** (the prototype said so on screen) — the
  owner chose pi's order.
- **Show a mid-turn `!` output to the model in the running turn.** It would mean
  splicing a message into the run's list mid-flight, between a tool call and its
  result at worst; steering already exists for "tell the model now".
- **SGR 2 or `bright_black` for the footer** — both a large step, not "a little".
- **A `VSplit` with a filler to cap the input** — hung prompt-toolkit's layout in
  the prototype.
