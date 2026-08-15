# 0219. Render width follows the terminal, under a readability ceiling the user can move

Status: Accepted (2026-08-14).
Date: 2026-08-14
Relates: ADR-0105 (Sprint 6h₁₀b, where `_RENDER_WIDTH = 80` entered the tree — see
"The 80 had no decision record" below), ADR-0112 (Sprint 6h₁₂c, which derived the 76-cell
tool-card cap FROM that 80), ADR-0157 (WP-0, the purpose-built approval dialog whose Panel
this changes), ADR-0159 (the dialog's height cap and its fixed-height options window),
ADR-0194 (the CSI 2026 bracket around `print_above`, which is why committed scrollback is
the host terminal's and not ours), ADR-0182 (`_RENDER_WIDTH` as the "best-effort width" for
manifest widgets — deliberately NOT changed here).
GitHub: #166.
Pi: **no citation offered.** The local pi copy under this workspace is a partial fetch with
no TUI sources, so pi's render-width behaviour was not read. This ADR therefore makes no
parity or divergence claim; see "Provenance" below.

## Provenance

**Aelix-original, and stated narrowly.** The surface being changed is the aelix TUI chrome —
`tui/shell.py`, `tui/render.py`, `tui/stream.py`, `tui/approval_dialog.py` — which ADR-0104 /
ADR-0105 built on prompt-toolkit + Rich as aelix's own implementation. Whether pi's TUI sizes
its output from the terminal is **unknown to this ADR**: it was not measured, and a claim
either way would be the kind of recalled-not-read provenance ADR-0218 was written to stop.
If a later sync reads pi's TUI and finds a rule there, that rule supersedes this one and this
ADR should be amended rather than quietly diverged from.

## Context

### The 80 had no decision record

`_RENDER_WIDTH = 80` entered in the commit for Sprint 6h₁₀b. **ADR-0105 contains the word
"width" zero times.** The number was never defended; it is the conventional terminal default,
adopted in passing. It then became the arithmetic base for three more constants:

| Derived | Where | Recorded as |
|---|---|---|
| 78 | `tui/context.py` `_PICK_MAX_WIDTH` | picker frame bound |
| 76 | `tui/render.py` `_truncate_lines` | ADR-0112: "leaves room for the 2-cell `│ ` gutter within an 80-col chrome" |
| 72 | `aelix_agents` batch preview | ADR-0199: "one visible row at 80 columns" |

Only ADR-0112 and ADR-0199 state a derivation, and both take the 80 as given. The single
in-code acknowledgement that it was a guess is the comment at `tui/context.py:60`,
"best-effort width for factory-rendered widget lines".

### What was actually wrong

Not the number. **The layering.** The paint surfaces already adapted and always had — the
scrollback console is a bare `Console()` (`tui/chrome.py`) that re-reads the terminal on every
print, the whole prompt-toolkit chrome takes live width from the layout engine each frame, and
`overlay._modal_cap` had been re-reading `output.get_size().rows` for the row axis since the
modal shipped. Content was hard-wrapped and space-padded to exactly 80 cells *before* any of
that measured the terminal.

Measured live in a pty against a real model, on a 200-column terminal:

```
 112 | » <the user's prompt>                     <- user echo, adaptive
 161 | <the thinking block>                      <- adaptive
  79 | The sea breathes in slow, ancient rhythms, its surface shimmering with silver
  80 | shore, folding over themselves in white cascades before drawing back, whispering
```

Two layers, one frame. On a **60**-column terminal the same padding was re-wrapped as content
by the adaptive console, producing a short orphan line after every full one (54/26/56/24/60).

And on the security-visible surface: at 60 columns the fixed-80 approval Panel was clipped by
prompt-toolkit with no right border and no ellipsis, so a user approved a shell command whose
tail they were never shown.

## Decision

1. **One live reader.** `tui/width.py::terminal_columns(chrome, *, max_width=None)` reads
   prompt-toolkit's output size per call, holds no cache, and snapshots nothing. The four
   independent `_RENDER_WIDTH = 80` declarations in `shell.py` / `context.py` / `descriptors.py`
   / `approval_dialog.py` are no longer the source for the streamed-text and approval paths.
   (`context.py` and `descriptors.py` keep theirs for extension-component surfaces — ADR-0182's
   scope, deliberately untouched.)

2. **`min(prompt-toolkit, Rich)`, never one alone.** Rich's `Console.size` lets `$COLUMNS`
   *override* the ioctl; prompt-toolkit's vt100 output asks the ioctl only. Under an exported
   `COLUMNS` that disagrees with the real terminal, laying out at the ioctl width while the
   scrollback console re-wraps at the other produces ragged output — measured, a 200-column
   terminal with `COLUMNS=80` collapsed the whole feature back to a 79-cell row. The row-axis
   precedent is `aelix_agents/consent.py::_terminal_rows`, whose finding F4 wrote down the
   direction-of-error argument: a stale environment value **may only make the measurement
   smaller**. Too small wastes columns; too large clips.

3. **Tracking a resize is the caller's job, and callers that stay on screen take the reader.**
   `run_approval_dialog`'s `width` is `int | Callable[[], int]` and its body is rebuilt when the
   resolved width changes. Passing the resolved *number* instead was measurably worse than the
   old fixed 80 on any terminal wider than 80: the Panel was baked at open width, the resize
   repaint clipped the stale rows, and because it had already wrapped, the clip removed a chunk
   from the **middle** of the command — leaving text that reads like a different command rather
   than a visibly truncated one.

4. **A readability ceiling, default 120, exposed as `render_max_width` (60–240).** Semantics are
   `min(terminal, ceiling)`. This is a product decision, not a technical limit: full-bleed prose
   on an ultrawide terminal is harder to read than a bounded measure. Unset stores **no number**,
   so the default exists in exactly one place — writing 120 into the settings layer as well would
   reproduce the `_LABEL_BUDGET` / `_PICK_MAX_WIDTH` shape this codebase already carries once, two
   copies held in agreement by a single test assertion.

5. **No floor.** An early revision clamped to `[40, 120]`, which re-created the very clip this
   work removes: a 30-column terminal was handed 40 and prompt-toolkit clipped it (measured —
   cols=30 and cols=36 both returned an unclosed frame). There is no trade-off to make. A cramped
   30-cell frame is a cosmetic problem; a clipped one hides what it is asking about.

6. **Overflow is not one rule — it depends on the surface.**

   | Surface | Mechanism | Rule |
   |---|---|---|
   | approval dialog | ptk float, `wrap_lines=False` → **CLIPS** | must never exceed the terminal |
   | tool card / diff | bare `Text` on the adaptive console → **WRAPS** | may exceed; costs a row, loses nothing |

   So the card cap only ever *widens* from the historical 76. Deriving it from the terminal in
   both directions deleted output that main displayed: on a 60-column terminal a 63-cell line
   survived at 76 and was cut to `...T…` at `60 - 4 = 56`.

7. **No SIGWINCH handling in aelix.** prompt-toolkit registers the handler and additionally
   polls its output size for terminals that never deliver the signal. Reading at render time is
   both sufficient and the pattern `overlay._modal_cap` already shipped.

## Consequences

- **Committed scrollback is never reflowed, and cannot be.** The application is
  `full_screen=False`; those bytes belong to the host terminal. A resize fixes future output
  only — the same guarantee every other terminal program offers. This is stated rather than
  worked around.
- **A resize takes effect from the next assistant message.** `_new_stream()` already ran once
  per message, so the re-measure point existed and was re-reading an immutable field; nothing
  new is plumbed. The uncommitted live tail is not chased — that is the trap that turns a
  ten-line change into a rewrite.
- **The measurement unit is now consistent.** `_compact_args` and the bash tool header capped at
  80 **codepoints** while `_truncate_lines` next door measured **cells**, so a Hangul summary
  passing the check could be 160 cells. Shipping a dynamic width over a codepoint cap would have
  made the CJK case drift further apart at every terminal size, so the two were unified here.
- **The suite could not previously observe any of this.** In the venv `sys.stdout.isatty()` is
  False and `shutil.get_terminal_size()` returns Python's own fallback of **80** — the number
  under test — and no test opened a PTY. `tests/tui/_pyte.py` gained `render_shell_to_screen`,
  which drives the real `run_tui`, and the defect was pinned as `xfail(strict=True)` before the
  fix so it could not land quietly.

## Alternatives rejected

- **Keep 80 and document it.** Rejected: the audit found no reader who wanted it, and the two
  measured failure modes (dead columns at 200, orphan lines at 60) are both user-visible.
- **Use the raw terminal width with no ceiling.** Rejected: measurably worse reading experience
  on ultrawide terminals than the bug being fixed.
- **Store the ceiling's default in settings.** Rejected — a second copy of 120; see decision 4.
- **Make the constants public and amend `_PRODUCT_CORE_CAP_ALLOWLIST`.** Rejected. Public
  UPPER_SNAKE `MAX_`/`MIN_` names in product-core read as delegation caps to
  `tests/agents/test_p2_band_boundaries.py` (ADR-0197). Render geometry is not delegation policy
  — a different subagent runtime has no opinion about terminal columns — so the honest resolution
  is to not present it as a policy surface. Every sibling TUI geometry constant is already
  module-private; the module's public surface is one function, and a caller with its own budget
  passes `max_width=`.
- **Add a SIGWINCH handler.** Rejected; see decision 7.

## Out of scope

`tui/context.py` and `tui/descriptors.py` still flatten extension components at their own
`_RENDER_WIDTH = 80` (ADR-0182's surface). The chrome footer's narrow-terminal behaviour is a
separate defect of the opposite shape — its Windows are built without `wrap_lines`, so a narrow
terminal silently drops the model, the context meter and the git branch with no ellipsis. Both
want their own issues.
