# Pre-existing findings surfaced by the ADR-0227 review, 2026-08-18

Two external reviews of the `feat/tui-visual-separation` branch produced 28 claims.
Each was re-measured against the branch HEAD **and** against `main` (`6503d9a`) and
sorted:

| | | |
|---|---|---|
| LIVE-NEW | 11 | the branch caused it — all fixed on the branch |
| LIVE-PRE | 8 | real, and `main` does it too |
| FALSE | 5 | did not reproduce |
| FIXED | 4 | already closed by an earlier round of the branch |

This file is the LIVE-PRE eight. None of them is a reason to hold the branch — every
one is byte-identical or measurably better at HEAD than at `main` — and none should
be lost either. Each entry carries the measurement that established it, so a reader
does not have to re-derive the claim before deciding whether to act.

Ordered by how close the harm is to a user, not by mechanism. All eight are filed as
GitHub issues #176-#183 and are on Project #1.

---

## 1. [#176] The single-line statusline clips extension statuses, and it is the default

`tui/context.py::_refresh_footer`'s single-line branch is
`parts = [...registry order...]; parts.extend(ext_statuses)`, and
`footer_segments.py`'s `_SEGMENT_SPEC` puts `current-dir` **4th of 11** — so the one
unbounded segment sits in front of every extension status.

MEASURED at 80 columns with one extension publishing, the composed row is 159 cells:

```
⚠  ·  ⏵⏵ all  ·  ⋯ 3 queued  ·  📂 /workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent  ·  ✱ gpt-5.6-luna  ·  ⎇ main  ·  lsp: 3 diagnostics
```

→ 80 on the glass, extension status gone. With four extensions, all four are lost.
The output string is **byte-identical** at HEAD, at `6ee6146` and at `main`.

This is the most user-relevant item in the list because single-line is the DEFAULT
mode; the multi-line path is opt-in and was fixed on the branch.

**Fix**: apply the same `_UNBOUNDED_TAIL_SEGMENTS` insertion the multi-line branch
now uses — insert `ext_statuses` in front of the first id in that set — so both
layouts keep one promise instead of two.

---

## 2. [#177] Tool-call headers and tool results pass control characters to the terminal

`tui/render.py::_tool_header` returns `args.get("path")` for `read`/`write`/`edit`
with no cell cap and no control stripping, and `render_tool_call_line` appends it to
a Rich `Text`. `_truncate_lines` caps lines and width but does not strip controls
either, so ANSI in a tool RESULT survives.

The path is a model-authored tool-call argument. `git diff 6503d9a..HEAD -- .../tui/render.py`
shows the branch did not touch either function.

**Fix**: one shared single-line sanitiser applied at the render boundary. See item 8
— the same helper closes several of these.

---

## 3. [#178] The tool card and the single-agent statusline do not sanitise child strings

`aelix_agents/panel.py::_child_line` (used by `format_card`) interpolates
`progress.profile`, `progress.state` and `progress.current_tool` raw;
`aelix_agents/progress.py::format_status_row` appends `current_tool` raw. The batch
WIDGET path routes the same fields through `panel._flatten`, so the surfaces
disagree with each other.

`_child_line`'s docstring says it is "P2's `extension._partial_text`, VERBATIM" — the
byte-identity is intentional and is what makes this pre-existing rather than an
oversight. Any fix has to decide whether that contract still binds.

---

## 4. [#179] The picker body takes caller strings; only the title is sanitised

`_picker_frame` sanitises `title`. `select()` interpolates the caller's OPTION
strings into body rows, `tabbed()` does the same for tab names and bodies, and
`detail` lines likewise. `_visible_len` strips SGR only
(`\x1b\[[0-9;]*m`), so OSC, DCS, CSI-non-`m` and one-byte C1 are neither removed nor
counted — the width arithmetic is wrong as well as the output.

The branch's own comment now says this out loud rather than claiming the body is
"this module's own styling", which was the half-truth that hid it.

Unchanged from `main`.

---

## 5. [#180] `set_cell_size` keeps its U+200D inexactness at three call sites

`rich.cells.set_cell_size` is not exact for a string beginning with U+200D ZERO
WIDTH JOINER: an odd number of leading joiners undershoots by one, an even number of
two or more overshoots by one. Swept, U+200B / U+0301 / U+FE0F / U+200E / U+00AD are
all exact — it is ZWJ specifically.

| site | main | HEAD |
|---|---|---|
| `panel._flatten` | n/a | fixed by `_cut_to_cells` |
| `render._cap_cells` | 41 vs 40 | 41 vs 40 |
| `render._truncate_lines` | 77 vs 76 | 77 vs 76 |
| `aelix_agents/tool._squeeze` | 41 vs 40 | 41 vs 40 |

`_squeeze` reaches the glass: a ZWJ-led model name gives a 77-cell usage footer
against `USAGE_LINE_MAX_CELLS = 76`, identically at `main`.

**Fix**: lift `panel._cut_to_cells` into a shared exact truncator and point the three
remaining sites at it, so the repo has one truncator rather than one fixed copy.

---

## 6. [#181] Truncation cuts codepoints, and a regional indicator becomes a different letter

Splitting cuts out of 40 budgets, `panel._flatten`:

| shape | main | HEAD |
|---|---|---|
| ZWJ family | 32/40 | 25/40 |
| VS16 heart | 19/40 | 19/40 |
| RI flag | 19/40 | 19/40 |
| base + 3 combining | 30/40 | 0/40 |

HEAD is better or equal everywhere, so this is not a branch regression. The RI case
is the one worth an issue on its own merits: a lone U+1F1F0 does not render as half a
Korean flag, it renders as a boxed capital **K** — the user is shown a character the
source never contained.

`rich.cells.split_graphemes` is **not** a usable oracle here: it does not implement
UAX#29 GB12/13 and reports `🇰🇷` as two clusters. A fix needs an explicit RI-pair
predicate.

---

## 7. [#182] The extension custom renderer is pinned to 80 columns

`tui/shell.py` has `_RENDER_WIDTH = 80` and passes it to
`component_to_text(component, _RENDER_WIDTH)` for extension custom message
components, while the ordinary `EventRenderer` uses a dynamic width (ADR-0219). So on
a 40- or 120-column terminal the extension's output is a different width from the
prose around it.

Unchanged from `main`. The branch extended the dynamic width to the batch panel;
this is the remaining surface that never got it.

---

## 8. [#183] The `--resume` startup menu cannot fit a terminal under ~56 columns

`cli/entry.py` computes `width = max(40, cols - 8) - 10`, so the label never shrinks
below 30 cells however narrow the terminal is, and the menu's fixed trailer —
`Enter a number, or press Enter to start a new session: ` — is 55 cells at every
revision.

At 40 columns HEAD emits 47-cell rows and `main` emits 41-cell rows; both wrap. At 60
columns and above HEAD is clean at every width tested (60/80/100/120/200 → widest
line 59/79/99/119/199).

HEAD is 6 cells worse than `main` at 40 columns because its label carries content
rather than an identifier; the mechanism — a hard 40-column floor plus an unbounded
fixed trailer — predates the branch and a sub-56-column terminal cannot render this
menu on any revision.

---

## Also worth recording: what did NOT reproduce

Five claims were refuted, each with a positive control that is the reason to believe
the refutation rather than the claim:

* **`/reload` loads project-local extensions before the trust gate** — false. The
  gate is inside discovery, not at the call site: `_build_harness_options` passes
  `no_project_local=not project_trusted`, and the factory `runtime_host.reload()`
  re-invokes captures the same bool. Proven over a pty with a marker file written at
  module import, with a GLOBAL marker as the control proving the reload really did
  re-discover.
* **Concurrent batch groups race over the widget owner** — unreachable. `agent`
  declares `execution_mode="sequential"`; the control ran the same probe with
  `execution_mode=None` and reached two concurrent groups, then the real mode reached
  one.
* **UI rebind leaves a stale panel cache** — the bridge is rebuilt on every harness
  build; shown by running the real loader twice against one held extension, after the
  first control was discarded as invalid.
* **Lone surrogates raise at the terminal write** — every sink is non-strict.
  prompt-toolkit encodes with `"replace"`; stderr is `backslashreplace`.
* **`NO_COLOR` hides the user echo bar** — it loses the cyan ground and keeps every
  character; the eight test failures the reviewer attributed to colour are a
  `TERM=dumb` harness issue, and `NO_COLOR=1` alone leaves the file at 126/126.

## And one question I could not answer

`cli/session_labels.truncate_cells` measures with `prompt_toolkit.get_cwidth`, which
scores a VS16 emoji at **1** cell where `rich.cells` and `wcwidth` both say **2**. A
realistic mixed line — eight ordinary emoji among ASCII prose — is 74 cells by the
library the code uses and 81 by the other two.

I could not settle which matches a real terminal. The pyte harness that arbitrates
every other width question in this work is not usable for it: its ASCII control
passes 20/20 but it already reports **19** columns for 10 Hangul syllables, so it is
wrong before emoji are reached. Left unchanged rather than decided on a two-to-one
vote between libraries — and `tui/context._visible_len` sizes the picker frame with
the same library, so the label and the rule at least agree with each other today.

Whoever picks this up should measure against a terminal, not against another library.
