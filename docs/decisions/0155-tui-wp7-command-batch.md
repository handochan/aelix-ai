# ADR-0155 — TUI WP-7 command batch: `/thinking` picker + `/hooks` + `/mcp` + `/context`

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** 6h₂₇
- **Supersedes/relates:** ADR-0154 (`/model` rich picker — the gold-standard DI template mirrored here),
  ADR-0132 (`select()` widget), ADR-0125 (`/settings` thinking-level cycle), ADR-0101 (MCP manager),
  ADR-0142 (compaction reserve). Roadmap: `.omc/specs/tui-v2-overhaul-roadmap.md` (WP-7/WP-8).

## Context

Four TUI consumer-layer commands were missing or under-built relative to backing APIs that already exist:

- `/thinking` (no-arg) only printed the current level; pi-parity is an interactive level picker.
- There was no `/hooks` viewer (registered hook handlers), no `/mcp` viewer (MCP server status), and no
  `/context` panel (context-window usage + compaction thresholds).

All backing surfaces exist; the gap was pure glue. This sprint is strictly pure-TUI-consumer — no edits to
`packages/aelix-agent-core` (protected core) or `packages/aelix-ai/src`. Every backing read goes through an
existing public/semi-public surface with `getattr`/`hasattr` guards.

## Decision

1. **`/thinking` no-arg picker** (`tui/thinking_picker.py`, new). Pure helper `thinking_picker_labels()`
   (numbered `N. {level}` rows, `✱` on the current level, unique so index-recovery is lossless) +
   dependency-injected `run_thinking_picker()` (duck-typed `harness` + `select`/`commit`). Enumerates levels
   via `aelix_ai.models.get_supported_thinking_levels(current_model)`, sets via async
   `harness.set_thinking_level`. `cycle_thinking_level()` advances one step and cannot power a picker, so the
   flow calls the enumerate + set APIs directly (both public, already exercised by core). Wired from
   `shell.py::_open_thinking_picker` into the new `CommandContext.thinking_picker` field; the no-arg
   `_thinking_handler` branch awaits it. The `/thinking <level>` typed-set path is unchanged. The existing
   `BuiltinCommand("thinking", …)` row stays (description refreshed to "Show, pick, or set the reasoning
   level"). **pi-parity.**

2. **`/hooks` read-only viewer** (`commands.py::_hooks_handler`, no new module — exact clone of
   `_tools_handler`). Reads `harness.hooks` (public `@property` → `HookBus`) → `HookBus._handlers`
   (semi-private event→handlers map, same coupling tier as `_action_get_all_tools`). Renders event→count for
   every event with ≥1 handler (the 35-event union is mostly empty → noise). Read-only — the panel notes
   "edit settings.json to change". No shell wiring / no `CommandContext` field. **aelix-additive.**

3. **`/mcp` server-status viewer — DEGRADED** (`tui/mcp_viewer.py`, new + wiring). `run_mcp_viewer()` (DI:
   duck-typed `manager` + `commit`) renders each declared server's transport+endpoint, connected state, and
   tool count. The tool count is the only async I/O (one `conn.list_tools()` per CONNECTED server, bounded by
   `asyncio.wait_for`; a slow/hung/erroring server shows `?` but the row still renders). It is a point-in-time
   SNAPSHOT — no live re-poll / reconnect (fine for v1 per the mockup). **The reduction vs a fully-live
   mockup is exactly this:** snapshot, not live. Wiring mirrors the `model_registry` seam — `entry.py` already
   builds `mcp_manager` (entry.py:744-750) and now threads it into `run_tui` (new keyword-only param), which
   wires `shell.py::_open_mcp_status` into the new `CommandContext.mcp_status` field. The harness does NOT
   expose the manager, hence the explicit thread. **aelix-additive.**

4. **`/context` panel — DEGRADED** (`commands.py::_context_handler` + pure `_context_bar`, no new module —
   read-only panel like `/cost`). Reads `harness.get_session_stats().context_usage` (`ContextUsage`:
   `tokens|None`, `context_window`, `percent|None`). Renders context window / used (+percent) / free /
   autocompact-buffer / "compacts at" threshold + a 3-segment colored bar. The reserve is read-only from
   `aelix_agent_core.harness.core._AUTO_COMPACT_RESERVE_TOKENS` (=16384), guarded so a missing symbol degrades
   to the documented constant; threshold = `context_window - reserve` (matches core's `shouldCompact`).
   **The reduction vs the mockup:** the usage-by-category section is OMITTED — grep-confirmed NO per-category
   breakdown exists anywhere (`ContextUsage` carries a single `tokens` total; `SessionStatsTokens` splits
   input/output/cache, which is usage accounting, not system-vs-tools-vs-messages context categories). A real
   per-category panel needs core instrumentation that does not exist, so it is honestly omitted. `tokens=None`
   (post-compaction-no-usage sentinel) and `context_usage=None` (no model bound) both degrade to a committed
   message. **claude/qwen-additive.**

Every handler degrades with a committed message (never crashes the REPL) on a headless / `FakeHarness` /
missing-subsystem path, matching the established defensive contract.

## Consequences

- New commands `/thinking` (picker), `/hooks`, `/mcp`, `/context` surface automatically in `/help` and the
  autocomplete palette (both enumerate `BUILTIN_COMMANDS`).
- `run_tui` gains a `mcp_manager` keyword-only param; `entry.py` threads it; the CLI router test stub +
  assertion updated. No protected-core change.
- Tests: `tests/tui/test_thinking_picker.py` (8) + `tests/tui/test_mcp_viewer.py` (6) + `/thinking`, `/hooks`,
  `/mcp`, `/context` handler-routing + pure-helper + degradation tests in `tests/tui/test_commands.py` + a
  `run_tui` WP-7 wiring/degrade smoke test + the CLI router `mcp_manager`-threading assertion.

## Alternatives considered

- **`/mcp` live re-poll / reconnect button** — rejected for v1; a snapshot meets the mockup and avoids a
  background poller + reconnect UX. Revisit if users need live status.
- **`/context` per-category breakdown** — rejected; no backing instrumentation. Adding it would require
  protected-core changes, out of scope for a pure-consumer sprint.
- **Driving `/thinking` from `cycle_thinking_level`** — rejected; it advances one step and cannot enumerate a
  pickable set. The enumerate + set APIs are public and used directly instead.
