# Sprint 6h₁₂c (TUI completeness — Sprint C) — Compact tool cards (truncation + styling + per-tool headers)

**Status:** DRAFT (W2, pre-grounded while Sprint B runs). From the 6h₁₂ audit (P0 #5 + P1 #11).
Closure ADR = 0112. PURE-CONSUMER (`tui/render.py` only); no contract change.

## §1 — Problem
`_render_tool_end` (render.py:180-191) dumps the **entire** `_result_text(result)` as a single
`Text` into scrollback — a `read` of a big file or a `bash` with lots of output **floods the
transcript** (the user's "tool card 전부 보이는" complaint). `_render_tool_start` (render.py:174-178)
emits one cyan line `⚙ tool(args)` with args capped at 80 (`_compact_args`). The descriptor
tool-renderer path (`_render_with_descriptor`, render.py:193-206) must keep precedence.

## §2 — Design (render.py only)
### §2.1 Truncated, styled result body (P0 #5)
- `_truncate_lines(text, max_lines, max_line_width) -> tuple[list[str], int]` (PURE): split into
  lines, keep the first `max_lines`, return `(kept_lines, hidden_count)`. Each kept line hard-capped
  at `max_line_width` (e.g. width-2) with `…`. Default `max_lines = 12`.
- `_render_tool_end`: when no descriptor matches and `text` is non-empty, commit a **styled card**:
  the kept lines rendered dim (and red when `is_error`), then if `hidden_count > 0` a final dim line
  `… (+{hidden_count} more lines)`. Prefer a Rich `Padding`/left-bar (dim `│ ` gutter) or a subtle
  `Group` so the result reads as an attached block under the `⚙` header — not a raw dump. Keep it
  one committed renderable (ordering-safe through the output pump).
### §2.2 Per-tool header hints (P1 #11, light)
- `_render_tool_start`: keep `⚙ {tool}(...)` but make the args summary tool-aware where trivial:
  `read`/`write`/`edit` → show the `path` (+ line range if present) rather than the generic kwargs;
  `bash` → show the `command`; others → existing `_compact_args`. A small `_tool_header(tool, args)`
  helper. (Edit-diff rendering is deferred — too large for this sprint.)
- `bash` result: if the `ToolResult`/payload exposes an exit code, append it to the header or the
  card footer (`exit N`); degrade silently when absent.

## §3 — Constraints
- Descriptor `_render_with_descriptor` keeps precedence (unchanged) — a matched tool-renderer-desc
  still renders its custom view, no truncation applied there.
- Contracts/rpc/harness/mcp byte-unchanged. pyright 8-baseline. Full suite green.
- PURE helpers (`_truncate_lines`, `_tool_header`) headless-unit-testable; the commit path tested via
  the existing `commit` sink capture.
- Don't lose information silently beyond the "+N more lines" hint; a future `/expand` or scrollback
  affordance is deferred (note in ADR).

## §4 — Test plan
- `_truncate_lines`: ≤max → all kept, hidden=0; >max → first max + correct hidden count; long line
  capped with `…`.
- `_render_tool_end`: short result → full (no "+N"); long result → max lines + "+N more lines";
  error result → red; descriptor match → custom view (no truncation), unchanged.
- `_tool_header`: read→path, bash→command, generic→_compact_args; bash exit code surfaced when present.

## §5 — Atomic commit plan (await authorization)
| # | message |
|---|---|
| 1 | `feat(tui): compact tool cards — result truncation + styling + per-tool headers (Sprint 6h₁₂c)` → render.py + tests |
| 2 | `docs: ADR-0112 TUI tool-result rendering (Sprint 6h₁₂c)` → ADR |
Trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Spec stays local.
