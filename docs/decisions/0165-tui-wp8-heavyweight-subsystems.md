# ADR-0165 — WP-8 heavyweight TUI subsystems (/login, /logout, /stats, /extension, /context, multi-line statusline)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Sprint:** WP-8
- **Relates:** ADR-0154 (/model picker + select detail), ADR-0155 (/mcp, /context v1, /thinking),
  ADR-0159 (in-flow modal slot), ADR-0160 (footer-segment registry + statusline store),
  ADR-0161 (settings expansion + scoped-models), ADR-0120 (permission/approval modal).

## Context

The TUI v2 roadmap's WP-8 ("heavyweight subsystems") was the last unbuilt tranche: a `/login`
provider wizard, `/stats` usage dashboard, `/extension` manager, an enriched `/context`, and a
multi-line statusline. Per the roadmap recon these were a "rails exist, train missing" set — the
harness already exposed the backing APIs (`AuthStorage`, the OAuth registry, `SessionStats`,
`McpClientManager`, the loaded `Extension` list, the footer-segment registry); the gap was
pure-TUI-consumer UI. The user endorsed the full additive scope including OAuth and the multi-line
statusline. The per-subagent activity line is **excluded** — agent lifecycle events carry empty
payloads today (genuinely blocked, not a consumer gap).

## Decision

All work is pure TUI-consumer (`tui/**` + `cli/entry.py`); `aelix-ai` / `aelix-agent-core` are
untouched. Each feature follows the shipped idiom: a dependency-injected DI module + a thin
`commands.py` handler + an `_open_*` flow in `shell.py::run_tui` + a `CommandContext` field, with
managers threaded from `entry.py`.

- **`/login` + `/logout`** (`tui/login_wizard.py`) — a multi-step wizard over the existing
  `AuthStorage` + OAuth registry. Method select → OAuth (the three built-in providers via
  `auth_storage.login(id, callbacks)`, the `OAuthLoginCallbacks` bundle mapped onto the existing
  dialogs + best-effort `webbrowser.open`), API-key (`set_api_key` over the `ENV_API_KEYS`
  providers), or Custom provider (protocol/base-url/id + key, with an honest note that the model
  still needs models.json to be selectable). The shared `AuthStorage` is now threaded into
  `run_tui`, so a stored key reaches model resolution with no reload. **Secrets are masked** via a
  new `input(password=True)` (`PasswordProcessor`).
- **`/stats`** (`tui/activity_tracker.py` + `tui/stats_dashboard.py`) — a TUI-side
  `SessionActivityTracker` accumulates from the live agent-event stream (tool calls/failures,
  per-model tokens, turns, wall time); the dashboard is a 3-tab viewer (Session / Activity /
  Efficiency) over the tracker snapshot + `SessionStats`. Cross-session heatmap/trend are honestly
  omitted (no history retained).
- **`/extension`** (`tui/extension_manager.py`) — a 3-tab viewer (Installed / Discover / Sources).
  Installed lists the discovered `Extension`s (manifest name/version) + MCP servers; the built-in
  safety extensions (Guardrail/Permission) are shown separately, not as user plugins. Discover /
  Sources are honest-empty (no marketplace).
- **`/context`** (`tui/context_usage.py` + the in-place `_context_handler`) — keeps the measured
  Used/Free/autocompact-buffer table and adds a heuristic estimated per-category composition
  (system prompt / tools / memory / messages), clearly labelled as an estimate. Falls back to the
  bound model's static `context_window` so the panel is useful on session open.
- **Multi-line statusline** (`tui/chrome.py`, `tui/context.py`, `tui/overlay.py`,
  `tui/statusline_store.py`, `statusline_picker.py`) — an opt-in grouped multi-row footer (mockup A).
  The footer row is made multi-line-capable (only the footer preserves `\n`; header/breadcrumb still
  collapse), the modal reserve grows with `footer_line_count()` so a tall footer never clips a modal,
  and the toggle is a persisted `StatuslineConfig.multiline` flag surfaced in `/statusline`
  (default OFF — the single-line footer is byte-unchanged).
- **`AelixTUIContext.tabbed`** — a new reusable framed tabbed-viewer modal (Tab/←→ switch, Esc/q/Enter
  close, guarded per-tab render), the shared shell for `/stats` + `/extension`. Built like `select()`
  (control-level key bindings) so no key leaks to the chrome's global accept.

## Consequences

- ruff clean; full pytest green (3971 passed / 1 skipped); live tmux smoke 7/7 (all commands render,
  tabs switch, the multi-line footer renders without clipping the prompt, `/login` aborts cleanly).
- An adversarial multi-agent review caught defects the green gate missed and they were fixed: the
  multi-line toggle was initially unreachable + `/statusline` save wiped the persisted flag (the
  `multiselect` `extra_toggles` now accept a `(key, label, initial)` triple that round-trips the
  stored state); the API key was echoed in plaintext (now masked); per-model request counts were
  inflated; and `tabbed()` leaked Enter to the chrome global accept (now consumed).
- No protected-core change; no permission/trust posture change. `multiselect.extra_toggles` is the
  only widened public dialog signature (back-compatible — the 2-tuple form still works).

## Deferred / not implemented (resume after the blocking dependency lands)

These were deliberately left out or shipped degraded because the backing data/subsystem does not
exist yet. Each is to be (re)implemented once its dependency is in place — none is abandoned.

| # | Item | Status today | Blocking dependency | Resume plan |
|---|------|--------------|---------------------|-------------|
| D1 | **Per-subagent activity line** (statusline mockup A: `○ Explore: … ▶ Ns · Nk tokens`, plus "N local agents") | Not built | The **subagent-lifecycle subsystem** — agent lifecycle events (`agent_start/agent_progress/agent_end`) carry **empty payloads** today (`tui/types.py`). | When events carry `{name,status,tool,elapsed,tokens}`: add a chrome widget row + a footer row in the grouped multi-line statusline composer. |
| ~~D2~~ | ~~auto-mode statusline segment~~ — **CORRECTION (ADR-0166): this was a doc error, NOT deferred** | ✅ **Implemented** (ADR-0158): `PermissionMode.AUTO` + the tree-sitter bash classifier + the shift+tab cycle + the `🤖 auto` footer badge all ship. | — | Done. Only the mockup's verbose wording differs from the live `🤖 auto` badge (cosmetic). |
| D3 | **/stats cross-session analytics** — Activity heatmap, token-trend braille chart, Sessions·Duration·Projects table | Session-scoped only (current run) | **Per-turn time-series persisted across sessions** (nothing retained today). | Add a cross-session stats store (jsonl under the agent dir) written on `turn_end`; the Activity tab reads it. |
| D4 | **/stats latency metrics** — per-tool / avg / per-model latency cards | Not shown (tracker records calls/failures, not durations) | None external — pure TUI-side; `ToolStat`/`ActivitySnapshot` need duration fields + start/end pairing in `SessionActivityTracker`. | **Low-effort follow-up:** pair `tool_execution_start`→`tool_execution_end` timestamps, add `duration` fields, render the cards. |
| D5 | **/extension Discover + Sources tabs + runtime enable/disable** | Honest-empty tabs; Installed is read-only | An **extension marketplace/registry** + a **runtime enable/disable API** (extensions load once at startup). | When a registry + toggle API exist, populate Discover/Sources and wire Space=enable/disable on the Installed tab. |
| D6 | **/context exact per-category breakdown** | Heuristic estimate (`ceil(len/4)`); Skills category omitted | Core `ContextUsage` carries a single total only — exact per-category measurement is an **aelix-agent-core (protected)** change. | When core emits a measured per-category split, swap the estimator for the real numbers (keep the estimator as fallback). |
| D7 | **/login custom-provider model wiring** | ✅ **Resolved for OpenAI-compatible** (ADR-0166): the wizard fetches `{base_url}/models` → multiselect → writes models.json → reloads → appears in `/model`. Anthropic/Gemini-compatible still show the manual note. | (Anthropic: no `/models` list; Gemini: no adapter — see D8) | Done for the common case. Extend to Anthropic when a model-list source exists. |
| D8 | **OpenAI Responses API adapter** (`openai-responses`) | Not built — 81 catalog models (OpenAI + GitHub-Copilot gpt-5.x) declare this API; selecting one is now **hidden/guarded** (ADR-0166) with a clear message instead of a cryptic failure | The Responses adapter is **protected-core** (`aelix_ai.providers`) — only `openai-completions` + `anthropic-messages` ship today | Add a Responses adapter in `aelix_ai.providers` + register it in `cli/runtime_bootstrap.register_providers`; the guard then auto-unhides those models. |

Known minor (refinement, not blocked): the /stats Efficiency cache-hit % is provider-inconsistent
(understates for OpenAI, whose `input` already includes cached tokens) — left as a labelled estimate.
