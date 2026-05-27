# 0112. Sprint 6h₁₂c — Compact Tool Cards (result truncation + per-tool headers)

Status: Accepted (TUI completeness Sprint C / W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — pure tui/ consumer)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context
From the 6h₁₂ audit (P0 #5 + P1 #11). `_render_tool_end` dumped the **entire** tool result into
scrollback — a `read` of a large file or a verbose `bash` flooded the transcript (the user's
"tool card 전부 보이는" complaint). `render.py` only.

## The decisions
- **`_truncate_lines(text, max_lines=12, max_line_width=76)`** (PURE): keeps the first N lines,
  each capped by **terminal cells** (`rich.cells.cell_len`/`set_cell_size`) so CJK/wide chars (the
  user writes Korean) don't overflow; width 76 leaves room for the 2-cell `│ ` gutter within an
  80-col chrome. Returns `(kept, hidden)`.
- **`_render_tool_end`** commits ONE Rich `Group` "card": `│ {line}` rows (dim; **red** when
  `is_error`), a dim `│ … (+N more lines)` footer when truncated, and a red `│ exit N` footer for a
  non-zero bash exit. The **descriptor tool-renderer path keeps full precedence** (early return,
  never truncated).
- **`_tool_header(tool_name, args)`** (PURE): `read`/`write`/`edit` show the `path` (read appends an
  `offset-limit` range); `bash` shows the `command`; else `_compact_args`. `_bash_exit_code` reads
  `result.details.exit_code` defensively (bash-only, non-zero footer).
- **Error results get a higher cap (40 vs 12 lines)** so a Python traceback's diagnostic tail (the
  exception type/message at the bottom) survives head-truncation (W4 MEDIUM). The `offset`/`limit`
  coercion in `_tool_header` is `try/except`-guarded — unvalidated model JSON (`offset="abc"`) must
  not raise inside the start-header render (W4 MEDIUM).

## Consequences
- A large `read`/`bash` now renders a compact ~12-line card with `… (+N more lines)` instead of a
  full dump; tool headers show path/command; bash failures show `exit N`. Live-verified
  (`read render.py` → `… (+266 more lines)`). pyright 8-baseline; protected paths byte-unchanged.
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
