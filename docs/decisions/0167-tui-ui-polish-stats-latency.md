# ADR-0167 — TUI UI polish (cyan logo, aligned banner, ● tool cards, green spinner, user-echo padding) + /stats latency cards (D4)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Sprint:** 6h₃₂
- **Relates:** ADR-0164 (gradient logo), ADR-0153 (enriched header + user-echo helper), ADR-0165 (WP-8 /stats dashboard + activity tracker).

## Context

After the WP-8 tranche the user requested a focused round of TUI visual polish plus the first
deferred WP-8 follow-up (D4, the `/stats` latency cards) — the only D-item with no external
dependency:

1. The startup logo gradient still ran cyan → **purple**; the purple tail read off-brand. The user
   wanted the all-cyan ramp from `docs/assets/print_logo_cyan.py`.
2. In the startup banner, the runtime-summary values (`model` / `baseurl` / `cwd` / `version`) did
   **not** line up with the `[Context]` / `[Tools]` / … section values below them; the labels were
   full-weight; and there was no visual break between the two groups.
3. Tool-call headers used a thin `⚙` gear with the whole line one flat cyan weight.
4. The human echo (`» …`) had only a single **leading** blank line — too subtle when a turn landed
   mid-stream between colored tool cards.
5. The working-line spinner glyph was monochrome.
6. **D4:** `/stats` had no per-tool latency — the activity tracker counted calls/failures but never
   timed them.

## Decision

All changes are **pure TUI-consumer** (no protected-core). Sprint label 6h₃₂.

- **Logo (`tui/_logo.py`):** `_LOGO_GRADIENT` retargeted to the cyan ramp
  `(200,255,255) → (100,242,254) → (0,220,240) → (0,180,216) → (0,130,200) → (0,95,175)`
  (matches `print_logo_cyan.py` / `aelix-terminal-logo-cyan.ansi`). Embedded in `LOGO_ANSI` as before
  (not read from `docs/`, which is not packaged).
- **Banner (`shell.py:_build_banner`):** both the summary rows and the section rows now left-pad their
  label to a shared `label_w = 12` so **all values align in one column**. Summary labels render
  **dim** (values keep normal weight); section tags stay bold cyan. A separator sits between the two
  groups — a Rich **`Rule`** inside a `Group(meta_text, Rule, sections_text)`, **not** a hand-sized
  `"─" * n` run (which a content-sized `Panel(expand=False)` could not constrain, wrapping into an
  orphaned dash row when a row — e.g. a deep cwd — exceeded the terminal width; caught by the 6h₃₂
  adversarial review). `Rule` is sized by Rich to the resolved interior, so it can never overflow.
- **Tool cards (`render.py`):** new `render_tool_call_line(name, summary)` helper renders
  `● name(args)` — a bold-cyan **`●`** marker (replacing `⚙`), the tool **name in bold**, args in the
  plain cyan card weight. Shared by the live (`_render_tool_start`) and replayed (`replay`) paths so a
  resumed transcript matches a live turn.
- **User echo (`render.py:render_user_message`):** the echo is now fenced by a **leading AND trailing**
  blank line (was leading-only) for clear vertical separation; still bold cyan.
- **Spinner (`chrome.py:_render_working`):** the spinner glyph is wrapped in a 24-bit green SGR escape
  (`\x1b[38;2;87;224;120m`), leaving the message at the terminal default. The working line is a raw
  ANSI string (consumed via `_ansi_row` → `ANSI(...)`), so the escape renders and degrades cleanly.
- **D4 latency (`activity_tracker.py`):** `tool_execution_start` timestamps are stored by
  `tool_call_id` and paired with the matching `tool_execution_end` to accumulate per-tool
  `total_duration` / `timed_calls` (reusing the already-stamped `_last_ts` — **never** re-reading the
  clock, which a scripted test clock would desync). `ToolStat.avg_duration` +
  `ActivitySnapshot.avg_tool_seconds` expose the means; `reset()` also drops unpaired starts so a
  hot-swapped session can't pair across the boundary.
- **D4 surface (`stats_dashboard.py`):** new `_dur()` (`840ms` / `1.2s` / `45s`) + `_avg_tool_seconds()`
  (duck-typed) helpers; a `Tool latency` line on the Session and Efficiency tabs, and a per-tool
  average appended to each Efficiency leaderboard row. Untimed calls render `—` (never a misleading 0).

## Consequences

- Per-**model** latency is **not** added — there is no clean per-request timing source from the event
  stream; only per-tool latency is unambiguous (start/end paired by id). The cross-session note already
  states the history gap; per-model latency stays deferred with D3.
- `avg_duration` / `avg_tool_seconds` are API surface (tested) but the dashboard computes its own
  duck-typed averages so its `SimpleNamespace` fixtures need no properties.
- Tests: `test_event_renderer` (replay header `⚙`→`●`, user-echo 3 rows, new `render_tool_call_line`
  cases), `test_chrome` (green-glyph escape), `test_activity_tracker` (+6 latency cases),
  `test_stats_dashboard` (+5 latency cases), `test_commands` (banner version assertion made
  alignment-robust). Full gate green.
- Reviewed by a dynamic 4-lens adversarial Workflow (correctness / regressions / ux-visual / quality);
  the single confirmed finding (banner rule overflow) is fixed here via the `Rule` approach.
