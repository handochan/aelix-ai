# 0112. Sprint 6h₁₂c — Compact Tool Cards (result truncation + per-tool headers)

Status: Accepted (TUI completeness Sprint C / W4 shipped) — Amended 2026-09-08 (#247), 2026-10-06 (#177)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — pure tui/ consumer)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context
From the 6h₁₂ audit (P0 #5 + P1 #11). `_render_tool_end` dumped the **entire** tool result into
scrollback — a `read` of a large file or a verbose `bash` flooded the transcript (the user's
"tool card 전부 보이는" complaint). `render.py` only.

## The decisions
- **`_truncate_lines(text, max_lines, max_line_width=76)`** (PURE): keeps the first N lines,
  each capped by **terminal cells** (`rich.cells.cell_len`/`set_cell_size`) so CJK/wide chars (the
  user writes Korean) don't overflow; width 76 leaves room for the 2-cell `│ ` gutter within an
  80-col chrome. Returns `(kept, hidden)`. *(#247: the signature no longer carries a line default —
  every caller passes `max_lines=` explicitly — and the number now lives in
  `DEFAULT_TOOL_CARD_MAX_LINES`.)*
- **`_render_tool_end`** commits ONE Rich `Group` "card": `│ {line}` rows (dim; **red** when
  `is_error`), a dim `│ … (+N more lines)` footer when truncated, and a red `│ exit N` footer for a
  non-zero bash exit. The **descriptor tool-renderer path keeps full precedence** (early return,
  never truncated).
- **`_tool_header(tool_name, args)`** (PURE): `read`/`write`/`edit` show the `path` (read appends an
  `offset-limit` range); `bash` shows the `command`; else `_compact_args`. `_bash_exit_code` reads
  `result.details.exit_code` defensively (bash-only, non-zero footer).
- **Error results get a higher cap (40 vs the normal card's 5 since #247, 12 as shipped here)** so
  a Python traceback's diagnostic tail (the exception type/message at the bottom) survives
  head-truncation (W4 MEDIUM). The `offset`/`limit` coercion in `_tool_header` is
  `try/except`-guarded — unvalidated model JSON (`offset="abc"`) must not raise inside the
  start-header render (W4 MEDIUM).

## Consequences
- A large `read`/`bash` now renders a compact ~12-line card (as shipped here; 5 since #247) with
  `… (+N more lines)` instead of a full dump; tool headers show path/command; bash failures show
  `exit N`. Live-verified (`read render.py` → `… (+266 more lines)`). pyright 8-baseline; protected
  paths byte-unchanged.
- **Known (deferred)**: no `/expand` to see the full truncated output yet (the `+N` hint + the
  higher error cap mitigate); the descriptor path ignores `is_error` (pre-existing, out of scope).
  Remaining NITs (read range 0- vs 1-indexed label; `exit N` not shown for an empty-stdout failure)
  are cosmetic, deferred.

## Verification (W4)
- Gate: ruff clean; `uv run pyright` 8-baseline (0 new from render.py); full `pytest` green
  (+ truncation/header/card tests incl. CJK-width, non-numeric-offset, error-cap regressions);
  protected paths byte-unchanged.
- **W4 code-reviewer (opus): APPROVE-WITH-NITS** — verified descriptor precedence + no false-positive
  truncation on short results. 2 MEDIUM (error-traceback truncation → higher cap; non-numeric offset
  crash → guard) + 1 LOW (CJK cell-width) **fixed in-sprint**.
- **W4 qa-tester real-PTY (gpt-4o-mini): 6/6 PASS** — large read → truncated card `(+266 more
  lines)` (full dump gone); bash header shows command + `exit 2` footer; short result not
  over-truncated; normal prompt + `/quit` intact.

Next: Sprint D (model/context slash commands — `/model`·`/clear`·`/compact`·`/cost`·`/tools`·`/mode`;
spec ready at `.omc/specs/sprint-6h12d-tui-model-context-commands-spec.md`), then E (polish).

## Amendment 2026-10-06 (#177) — what a header or card writes cannot steer the terminal

**Context.** `_tool_header` returned a model-authored `path` exactly as written, and the cards
passed tool output through unchanged. `rich` is not a defence: it strips BEL, BS, VT, FF and CR,
and passes ESC, the one-byte C1 CSI (0x9B) and the BiDi overrides. Measured on `aab1f210` through
`render_tool_call_line` and `Console(force_terminal=True)`: an ST-terminated OSC 52 (a clipboard
write) and `ESC [ 2 J` reached the output bytes. A pty run of the real TUI with a scripted model
counted 4 OSC 52 writes in one turn and 4 more on `/resume`. The same was true of the tool name,
tool results, reasoning, the answer, error lines, the default custom-message rendering, extension
components, the user echo's BiDi and everything replay draws again. Probes and captures are in
`.omc/probes/177-live/impl/`.

**Decision.** The strings the transcript (`tui/render.py`) draws from the model, a tool, a
provider, an extension or a session file, live and on replay, go through
`aelix_ai.utils.terminal_text.safe_for_terminal` before they become a renderable. Those strings
are the ones this amendment lists: the tool header, tool output, reasoning, the answer, error
lines, a custom message (default rendering or a component), a `tool-renderer-desc` view, the
output of the user's own `!cmd` and the user echo replayed from a session. Each takes one of four
shapes, chosen by what the text is:

- **Header rows** (tool name, path, command, the argument summary including its keys): every
  steering character becomes a SPACE, so the words stay apart and a sequence shows as its inert
  literal (`[2J`). `render_tool_call_line` strips both halves itself, because it is the boundary
  the live and replay paths share. The name is capped at 80 cells. The path is capped at 160
  cells **from the front**, so the file name survives. Real paths are much shorter than that; the
  cap is for a 55 KB "path", which would otherwise be about 700 rows of header. A header row
  keeps U+200E, U+200F and U+061C, as tool output does.
- **Tool output** (result bodies, the edit tool's `details.diff`, the `/expand` copy of a card):
  whole escape sequences are removed first, then every remaining steering character; newlines and
  tabs survive. This is pi's rule for tool output (`getTextOutput`:
  `sanitizeBinaryOutput(stripAnsi(…))`), so a tool's colour is dropped rather than drawn. The
  sequence regex is pi's `ansiRegex` with two changes. An OSC body may not cross a newline, so a
  stray `ESC ]` can no longer hide the lines after it or change the `+N more lines` count. The
  body also stops at the next possible terminator: the lazy form is quadratic on unterminated
  introducers (measured 829 ms for 10,000 of them). `_truncate_lines` strips each kept line before
  measuring it, because a cell count over escape bytes is wrong. Tool output keeps U+200E, U+200F
  and U+061C, as the shared helper does: they are ordinary in right-to-left text a file holds.
- **Prose** (reasoning, the answer, a custom message body other than a `!cmd` record, and the
  `/expand` copy of collapsed reasoning): stripped per character, keeping newlines and tabs. The
  answer is stripped per delta, and per-character stripping commutes with concatenation, so the
  stream's committed prefix always agrees with its final frame. Prose also loses the three BiDi
  marks the shared helper keeps (U+200E, U+200F, U+061C). Markdown decodes entities, so `&#8238;` in
  an answer became a raw U+202E after every strip on the source (measured on the first version of
  this change, live tail, committed block and replay). `stream.markdown_lines` therefore removes,
  from what it rendered and before it splits lines, the code points of `stream._FORMAT_CONTROLS`:
  the BiDi overrides and isolates (U+202A–202E, U+2066–2069), the three marks, U+2028, U+2029,
  U+200B and U+FEFF. That is not every Unicode format character: U+2060, U+00AD, U+206A–206F and the
  other format characters pass, here and everywhere else `safe_for_terminal` runs. U+200D (ZWJ) is
  kept because it composes emoji.
- **Error lines**: `safe_error_for_terminal`. It keeps the first 8 lines, cuts each to 200
  characters and appends `…` where it cut, and adds one `… (N more lines omitted)` line when it
  dropped lines. The renderer's
  `_reported_error` keeps the raw text, because the shell's #189 deduplication compares it with
  `str(exc)`.

Extension components keep their SGR: `Text.from_ansi` reads it as style. The steering characters it
leaves in `.plain`, and the three BiDi marks (a component is extension prose), are then replaced
with spaces in place, so every span still covers the same characters. `from_ansi` also reads an
OSC 8 hyperlink target into `Style.link`, and rich writes that target back out verbatim. A target
with a BEL and an OSC 52 in it was a live clipboard write. A link is kept only when its target
holds no C0, C1 or DEL character and no code point of `stream._FORMAT_CONTROLS`, and its scheme is
`http`, `https`, `file` or `mailto` (in any case). Otherwise the link is dropped and the text and
colour are kept. The user echo's private C0/C1 map became `safe_for_terminal` in SPACE mode, which
also covers the BiDi overrides and isolates, U+2028/2029, U+200B and U+FEFF.

A **descriptor view** (`tool-renderer-desc`) draws two kinds of string, and
`DescriptorRenderer.build_tool_renderable` passes each through a shape the transcript hands it
(`_render_with_descriptor`). The title and the column headers (a column without a header shows its
key) are the extension's, so they are header rows. Everything the view takes from the result —
a table cell, a form field's name and value, a grid item, the text body — is tool output. The view
is handed the result body as it arrived (not the cleaned body the default card draws), because it
decodes it: its JSON keys, `rows_path` and `text_path` are looked up byte for byte. Decoding does
not make the body safe either, since it turns `\u001b` back into ESC, so the shapes are applied to
what is drawn, after every lookup. A column's `key` is matched against the row's keys exactly as
the tool wrote them, whether the JSON escapes a character (`\u200b`) or holds it literally, and a
form row is drawn entry by entry. The first form of this change cleaned the row's keys before the
lookup, and the second cleaned the body before decoding it; each blanked a column keyed with a
zero-width space and merged two form fields whose names cleaned to the same text (the second only
for the literal spelling). A `grid` draws a dict row as its `repr`, which already escapes control
characters. The chrome callers of `build_tool_renderable` (`render_tool_result`,
`open_modal`) draw with plain `str`, as before.

The output of the user's own `!cmd` is bytes a process wrote, so it takes the tool-output shape:
the live line (`EventRenderer.user_bash_output`, committed by `tui/shell.py`) and the replay of its
record (the `bash_execution` custom message, whose body is drawn in this shape rather than as
prose) alike.

**Consequences.** A coloured `git diff` is now recognised as a diff, because its hunk header no
longer starts with `ESC [ 36 m`. Tool output that used to arrive coloured now shows plain, and so
does a coloured `!cmd`, live and on `/resume` (where it used to show `[31m`). Right-to-left prose
that relied on U+200E, U+200F or U+061C to order neutral characters can show them in a different
order; the letters themselves are kept. A component's `javascript:` or custom-scheme link is shown
as plain text. A descriptor view still parses its title, its column headers and the cells of the
`table` and `form` views as rich markup, so a tool or an extension can draw a `[link=…]` whose
text differs from its target and whose scheme is not checked (the `text` body and `grid` items are
not parsed). That target cannot hold a C0, C1 or DEL character or a BiDi override or isolate, so it
cannot end the OSC 8 early; it can hold U+200E, U+200F or U+061C, which both shapes keep. It is
left for a follow-up issue. Outside the transcript, this amendment does not cover the descriptor
surfaces drawn in the chrome (status, widgets, toasts, modals and the rest of
`tui/descriptors.py`), the subagent panel (#178), the picker body (#179) or tool-result `details`
on replay (#168).
