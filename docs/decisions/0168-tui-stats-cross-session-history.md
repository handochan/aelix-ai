# ADR-0168 — Cross-session `/stats` history (WP-8 D3)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Sprint:** 6h₃₃
- **Relates:** ADR-0165 (WP-8 `/stats` dashboard + activity tracker), ADR-0167 (D4 latency), ADR-0160 (`statusline_store` persistence pattern).

## Context

The WP-8 `/stats` dashboard (ADR-0165) and its D4 latency cards (ADR-0167) read the
TUI-side `SessionActivityTracker`, which is **live-only** — it resets on a session swap and
is lost on exit. Every per-session tab therefore carried an honest footer admitting the
cross-session views (heatmap / token trend / project table) were "omitted — no history
retained". D3 is the WP-8 follow-up that closes that gap: persist the data and render those
views. It is the last WP-8 D-item with no external dependency (D1/D5/D6/D8 need
subsystems/protected-core).

## Decision

Pure TUI-consumer (no protected-core). Sprint 6h₃₃.

- **Store (`tui/stats_history.py`, NEW):** `StatsHistoryStore` — an append-only JSONL file at
  `get_agent_dir()/stats-history.jsonl` (the same agent-dir + atomic-write posture as
  `statusline_store.py` / `ProjectTrustStore`). `append(fields)` stamps a wall-clock `ts` (via
  an injected `clock=time.time`) and is **best-effort — never raises** (losing a history row is
  acceptable; crashing the turn loop is not). `load()` parses lines **tolerantly** (skips a
  half-written final line, non-dict JSON, or a row with an uncoercible field type). `prune(keep)`
  atomically rewrites keeping the last `keep` rows (temp + `os.replace`). `StatsHistoryRecord` is
  a frozen dataclass; `.tokens` = `input + output` (throughput, excludes cache reads).
- **Recording (`shell.py`):** the store is constructed in `run_tui` and **pruned once at startup**
  (cap `_HISTORY_MAX_RECORDS = 5000`) so the file can't grow without bound. On **every
  `turn_end`** a guarded async `_record_history()` is spawned (same strong-ref task-set pattern as
  the context-usage refresh): it awaits `harness.get_session_stats()` (authoritative
  tokens/cost/`session_id`) and reads `tracker.snapshot()` (tool counts / turns / `tool_seconds` =
  Σ `per_tool.total_duration`), then appends a **cumulative** row. A stats failure simply skips the
  row. The in-flight task is cancelled at shutdown (avoids an unretrieved-exception warning).
- **History tab (`stats_dashboard.py`):** `build_history_tab(records, *, hour_of=_local_hour)` —
  rows are cumulative, so it **collapses to the latest row per `session_id`** for the per-project
  aggregate table (sessions / tokens / cost / tool calls, busiest-first, top 10); a per-session
  **token-trend** bar list (recent sessions, oldest→newest); and a **24-hour activity heatmap** in
  4 six-hour blocks with a 9-level light→dark ramp. `hour_of` is injected so the heatmap's local
  bucketing is deterministic under test. `run_stats` gained an optional `history_getter`; when
  wired it appends a 4th **History** tab, late-bound and **guarded** (a store failure renders the
  empty-history one-liner, never crashing the modal). The per-session tabs' footer note now points
  to the History tab instead of claiming "no history retained".

## Consequences

- One small JSONL append per turn (off the hot path, via a guarded async task). Startup prune
  bounds the file at 5000 rows. Agent-dir is tmp-isolated in TUI tests (autouse conftest), so the
  per-turn append never pollutes a real dir.
- Cumulative-per-turn rows give a true per-turn time series (the heatmap reads ALL rows) while the
  project table reads the latest-per-session collapse — no double-counting.
- Per-**model** latency / trend is still deferred (no clean per-request timing source); D3 delivers
  per-project + per-session + by-hour, which the persisted rows support unambiguously.
- Reviewed by a dynamic 4-lens adversarial Workflow (correctness / regressions / ux-visual /
  quality) with skeptic verification.
