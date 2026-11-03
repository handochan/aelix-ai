# 0287. The input shows what Enter will do, and a `!` line runs during a turn

Status: Accepted (2026-11-03, #596). Option B of the #596 prototype, the owner's
choice (2026-11-02), with the owner's 2026-11-03 decisions: Esc clears the whole
input, a `!` line Entered during a turn runs, the shell colour is magenta through
a new theme role, and the footer under the input is a little dimmer. Revised the
same day by the fix round after the Codex review and the independent verify,
with three more owner decisions: a `!` block run during a turn waits in a
pending area under the stream (D6), Alt+Enter on a `!` line during a turn queues
a shell command that runs after it (D6), and the default theme's footer keeps
`grey70`, a light terminal being the `light` theme's (D8).
Date: 2026-11-03
Issue: #596. The prototype hand-back, with what pi and Claude Code do and the
three options: `.omc/specs/596-proto-report.md`; the grids the owner compared:
`.omc/specs/596-preview-page/`. The fix round's inputs:
`.omc/specs/596-impl-codex-review.md` (596-01…06) and
`.omc/specs/596-verify-report.md` (V1–V12).
Amends: ADR-0184 (the theme file's roles: two more), ADR-0119 (Enter during a
turn: a `!` line runs instead of steering), ADR-0153 (the `❯` marker: the sigil
replaces it in shell mode), ADR-0280 (the chrome is two rows taller; the draft
rule counts the input only; the menu's reserve), ADR-0219 (the input frame stops
at the render ceiling), ADR-0156 (the completion menu draws over rows of its own),
ADR-0023 (an outside writer takes the drain lock: `AgentHarness.idle_write`)
— each by a dated note.
pi: `earendil-works/pi@9c9ce8547` (2026-11-03). Claude Code: the shipped 2.1.314
binary's embedded JS, read by the prototype (nothing recalled).
Tests: `tests/tui/test_input_marks_596.py`, `tests/tui/test_shell_mid_turn_596.py`,
`tests/harness/test_idle_write_596.py`;
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
   `parse_input_line` classifies the TYPED buffer as `bash` or `bash_transient`, and
   Enter reads the same buffer (`submitted_input`), so the drawing and the submit
   path cannot disagree (`" !ls"` is a prompt in both). In shell mode the typed `!` /
   `!!` is drawn as the prompt marker in place of `❯`, in the theme's `shell` colour
   and bold. Shell mode wins over the permission colour: the permission gate never
   sees a `!` command. A bare `!` waits for "a shell command" after the marker. The
   status row says what Enter does: `! shell · Enter runs it · the model sees the
   output · Esc to clear` (`!!`: the model won't see the output). It is fitted by
   importance — the mode, then what the model sees, then the Enter clause, then
   Esc, each kept only while it still fits — so at 50 columns `!!` keeps "the model
   won't see the output" (verify V8: it was dropped first). A bare sigil runs
   nothing, so its hint makes no promise about Enter. While a `!` command runs the
   Enter clause gives way to "a ! command is already running" (D6).

2. **The sigil stays in the text.** History, `!!`, paste placeholders, Ctrl+G and
   the submit path read the buffer and are unchanged; only the drawing takes the
   sigil out (`_HideShellSigil`, positions past it shifted). The cursor is kept from
   standing in front of it, so Home and Ctrl+A stop after it, and a click that maps
   inside it is moved past it. Backspace right after the marker deletes the sigil's
   last `!`: `!!` steps down to `!`, `!` leaves shell mode with the text kept.
   A collapsed paste is expanded only after the line is classified, so a pasted
   `!` stays literal text: a paste that begins with `!` at the start of the input is
   drawn as a prompt and goes to the model as text, mid-turn (steered) and idle
   (verify V1, the blocker — mid-turn it ran every pasted line in the shell, which a
   markdown `![image](…)` would trigger), and `!` typed before a paste that begins
   with `!` runs the paste as a `!` command, recorded as drawn, not as `!!` (Codex
   596-06). The classification travels to the input loop with the expanded text
   (`SubmittedLine`), so the loop does not re-read it; a steer re-routed because the
   turn ended first is a prompt for the same reason.

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

6. **A `!` line Entered while something holds the session runs now, and the turn
   goes on.** "Holds the session" is the chrome's own turn, a turn an extension
   started (`send_message(..., trigger_turn=True)`, which never set the chrome
   running — Codex 596-01), a compaction (`/compact` too — Codex 596-05), or a line
   the input loop is still handling (any command). It is read off the harness
   (`is_idle`) as well as the chrome (`ShellLane.busy`).
   - It runs as an idle `!` does (the `user_bash` hook, then the bash operations),
     concurrently with the turn. Its block — the command line in the shell
     colour, `(running…)`, then the last three lines of its output — is drawn in
     a **pending area** under the stream and above the input chrome, pi's
     `pendingMessagesContainer` (`interactive-mode.ts:7138-7148`), at most eight
     rows, each cut to the width. Nothing of it is committed among the answer's
     lines: committed, a block split a streamed sentence and a tool call from its
     output (verify V4, measured with a real model and scripted).
   - **When nothing holds the session any more, the finished blocks move into the
     transcript and their records are written, at one settle point**, oldest
     first: the command line over all of its output, then what the model gets of
     it, so it lands after the turn's last message and reaches the model on its
     next turn. pi holds `_pendingBashMessages` the same way, "to avoid breaking
     tool_use/tool_result ordering"; it moves the blocks into its chat only when
     the next message is submitted (`flushPendingBashComponents`, `:3450`), where
     here they move with their records, when the turn ends (the owner's
     decision). The input loop settles before each line,
     right before it marks a turn running (no await in between) and after each
     turn; the lane settles itself when a turn it did not start ends
     (`wait_for_idle`), and once more when the TUI closes (Codex 596-04: a record
     held through `/compact` was lost on Ctrl+D). A block leaves the pending area
     in the same frame it lands in the transcript (the output pump applies both).
   - **Every TUI record goes through this lane, the idle input-loop `!` included**
     (Codex 596-01), so none is written while something holds the session. An
     idle `!` still prints its output alone under the line it was typed on, as
     before, unless something started while it ran; then it waits with the rest.
   - **The write holds the harness's own write order** (`AgentHarness.idle_write`,
     the lock `_drain_pending_session_writes` takes): a turn that starts while a
     record is written — an extension's, typically — waits at its entry drain and
     its user message is chained after the record. Before, it read the same leaf
     and forked the session away from the record, which then never reached the
     model (Codex 596-02, verify V12). The turn waits rather than being refused, so
     an extension's `trigger_turn` keeps its meaning.
   - A record whose session the user left (`/new`, `/resume`, `/fork`, `/clone`,
     `/import`) is dropped, and its block says so; the comparison is with the
     session of the harness in force, since a swap replaces the harness and the old
     one keeps its session (Codex 596-03, verify V2: the record was written into the
     departed session file). A `/reload` keeps the session, and the record goes
     through the rebuilt harness.
   - `!!` records nothing, here as at idle; its block is shown and moved all the
     same.
   - **One `!` command runs at a time**, as in pi (`interactive-mode.ts:3412`, "A
     bash command is already running"): Enter on another while one runs is
     refused and the draft kept, and the status row says why (verify V10). Esc
     does not cancel one (D3, Limits), so the message points at nothing to press.
   - **Alt+Enter on a `!` line during a turn queues a shell command that runs once
     the turn is over** — the owner's decision, where pi queues the text to the
     model (`handleFollowUp`, `:4571`). Its block waits in the pending area marked `(runs
     after the turn)`; the line goes to the input loop, which runs it after the
     turn (after a turn an extension runs, too) and after a `!` still running. Once
     the turn is over it is an idle `!`: its record is written as soon as it ends.
     Idle, or on a bare sigil, Alt+Enter does nothing, as for any line.

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
   sits about 9.5 L* under a `#cccccc` terminal foreground and 18.4 under
   `#e5e5e5`; the `light` theme's `grey30` (`#4e4e4e`) is 8.32:1 on white (verify
   V7 corrected 9, 19 and 8.6:1). An extension's own footer (`set_footer`) is not
   dimmed — it styles itself. Under `NO_COLOR` prompt-toolkit drops colours, so the
   footer is as before. **The default theme keeps `grey70`** (the owner, 2026-11-03,
   verify V3): it is 7.86:1 on `#1e1e1e` but 2.12:1 on white, and no single fixed
   grey reaches 4.5:1 on both. Nothing picks a theme from the terminal background,
   so a light terminal should use the `light` theme (`/settings` → Theme); the
   guide says so.

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
- The fix round, live (pty + pyte at 50x24, 80x24, 120x40, 80x24 `light`, 80x24
  `NO_COLOR`; a scripted model whose first request answers a `bash` tool call
  and whose second a long text): `!echo MID_A` showed in the pending area 0.25 s
  after Enter; `!!`, a `!` while the tool ran, a `!` still running (`(running…)`),
  a refused second `!` (draft kept, the status row saying why), an Alt+Enter
  `! … (runs after the turn)` and a mid-turn paste beginning with `!` (drawn
  `❯ [Pasted text …]`, steered) all left the transcript alone until the turn
  ended. Afterwards the transcript had the tool call next to its output and the
  answer whole, then every block in the order run; the request after the turn
  carried the four `!` records, none carried `!!`'s or the refused one's, both
  pastes reached the model as text and neither ran in the shell. Every frame fit
  the terminal (no cell past it on a screen 20 columns wider), kept the footer on
  the last row once pinned, and had the cursor on the expected cell.
  Grids: `.omc/specs/596-fix-r1-evidence/grids/`.

## Limits

- The `default` theme's `statusline` grey is tuned for dark terminals: on white it
  is 2.1:1. The default theme already is (its `tool` yellow); the `light` theme is
  the one for a light terminal. A palette-relative "a little dimmer" has no ANSI
  name.
- History stores the expanded line, so a recalled paste is classified as its text
  reads (and drawn so): a paste that began with `!` comes back as a shell line.
- The pending area shows the last three lines of a finished block's output and at
  most eight rows in all; the rest is in the transcript once the turn is over. It
  is hidden while a modal (an approval dialog) is open, which needs the rows. A
  steering echo is still committed among the answer's lines, as before #596.
- Esc during a turn interrupts the turn, not a `!` command running in it; such a
  command cannot be cancelled, as an idle `!` cannot. Quitting the TUI cancels it:
  it neither prints nor records then. A finished block still held when the TUI
  closes is recorded, unless a turn an extension started still holds the harness.
- Idle Esc in shell mode clears the input about a second after the key: Alt+Enter
  and Alt+Up begin with Esc, so prompt-toolkit waits that long for a second key
  (verify V9). The Esc interrupt during a turn waits the same way.
- Alt+Enter queues a shell command behind the turn, so it runs after the text
  follow-ups the model takes within the turn, and Alt+Up does not bring it back to
  the input (it is not in the harness's queues).
- Resizing the terminal while the completion menu is open can leave rows of it on
  the screen until the next repaint of those rows (verify V8; worse on the base).
- `@` completion still runs in shell mode and inserts a literal `@path` into the
  command. A follow-up.
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
- **Alt+Enter as pi's** (the `!` line sent to the model as a follow-up message) —
  the input drew a shell command that Alt+Enter did not run; the owner chose a
  shell command after the turn.
- **Commit a block among the answer's lines** (the first cut) — verify V4 measured
  it splitting a sentence and a tool's output; the owner chose pi's pending area.
- **Refuse a turn while a record is written** (`hold_turns`) — an extension's
  `trigger_turn` would have run as a refused turn; waiting at the drain keeps it.
- **A footer grey legible on both backgrounds** — none reaches 4.5:1 on both
  `#1e1e1e` and white; the owner kept `grey70` and the `light` theme for light
  terminals.
