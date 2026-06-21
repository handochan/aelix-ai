# ADR-0163 — TUI picker visual polish + cursor preservation

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** 6h₃₀
- **Relates:** ADR-0132 (select widget), ADR-0154 (detail panel), ADR-0159 (in-flow modals),
  ADR-0160/0161 (multiselect, /settings expansion). Roadmap: `.omc/specs/tui-v2-overhaul-roadmap.md`.

## Context

User feedback after running the TUI: the `select()` / `multiselect()` pickers (`/model`, `/settings`,
`/scoped-models`, `/statusline`) were visually flat — the current row was marked only by a plain `→` with no
colour, help/detail text had the same weight as options, and there was no frame delineating the open panel.
Separately, a real bug: in `/settings`, editing "Default model" opens the `/model` picker as a sub-flow, and
pressing Esc to back out **reset the `/settings` cursor to the top** instead of keeping it on "Default model".

## Decision

1. **Framed, colour-styled pickers (`tui/context.py`).** Both `select()` and `multiselect()` `render()` now
   return `ANSI(...)` instead of a plain string, via a shared `_picker_frame(title, body, hint, width)` helper:
   - bold **title**, then a dim top divider;
   - the **current row** is bold cyan with a `▸` marker (a clear colour change, not just a glyph);
   - the **counter / detail / preview / hint** are dim, so the eye lands on the selection;
   - a dim bottom divider closes the panel; dividers span the widest plain content (clamped 28–78 cols).
   Raw ANSI is used (theme-agnostic, independent of the prompt-toolkit style map) — the same Rich→ANSI
   precedent as the approval dialog (ADR-0159). The detail callback (ADR-0154) output is dimmed as-is.

2. **Cursor preservation (`select(initial_index=...)` + `tui/shell.py::_open_settings`).** `select()` gained an
   `initial_index` keyword (clamped to range) that seeds the highlighted row. The `/settings` loop now remembers
   the chosen row index across re-opens and passes it back as `initial_index`, so returning from a sub-flow
   (the `/model` picker) keeps the cursor on the row you were on. The filter starts empty, so the initial index
   maps 1:1 onto `options` at open.

## Consequences

- Pure TUI-consumer — only `tui/context.py` + `tui/shell.py`; no protected-core edits. `select()`/`multiselect()`
  signatures stay backward-compatible (`initial_index` defaults to 0), so every caller benefits with no change.
- All dialogs routed through these widgets (`/model`, `/settings`, `/scoped-models`, `/statusline`, `/resume`,
  `/thinking`) get the framed look for free.
- Tested: `initial_index` start-at-row + out-of-range clamp (regression-locks the cursor fix); the existing
  drive-by-keys select/multiselect tests still pass against the ANSI render.
