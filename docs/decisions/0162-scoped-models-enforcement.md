# ADR-0162 — enabled_models enforcement: the /scoped-models allow-list now restricts the model list

Status: Accepted
Date: 2026-06-21
Related: ADR-0161 (shipped `/scoped-models` as PERSIST-ONLY — this ADR makes it enforced), ADR-0154 (the `/model` picker, the headline scoped consumer), ADR-0090 (`--list-models`, the CLI scoped consumer), ADR-0140 (the `models.json` `enabled_models` setting + `resolve_model_scope` glob semantics)

## Context

ADR-0161 (WP-2) shipped `/scoped-models`, which PERSISTS an `enabled_models`
allow-list via `SettingsManager.set_enabled_models()` (`None` = the canonical
"all enabled" sentinel). But NOTHING consumed it:
`ModelRegistry.get_available()` filters by configured auth ONLY (pi parity), and
every model-list surface (the `/model` picker, `--list-models`, RPC) read that
same unfiltered list. The allow-list was durable but **inert** — after
`/scoped-models` the user still saw the full catalog. The user's explicit ask:
make the persisted allow-list actually RESTRICT which models they see/select.

The pi-intended scope resolver `core/model_resolver.resolve_model_scope(patterns,
registry)` already existed (glob-aware via minimatch semantics — `*` does not
cross `/`) but had ZERO callers, and it internally reads
`registry.get_available()` to find matches — so pushing the scope INTO
`get_available()` would be circular (the resolver would match against the
already-scoped list) and would diverge from pi's auth-only `get_available()`.

## Decision

1. **One new pure helper — `core/scoped_models_filter.scoped_available(registry,
   settings_manager, *, warn=None)`** is the SINGLE place `enabled_models` is
   applied. It:
   - reads `settings_manager.get_enabled_models()` **LIVE on every call** (never a
     startup snapshot), so a runtime `/scoped-models` change takes effect on the
     next `/model` open / `--list-models` call with no restart;
   - `settings_manager is None` OR patterns `None` (sentinel) OR `[]` (empty list)
     → returns `registry.get_available()` unchanged;
   - a concrete non-empty allow-list → calls the EXISTING
     `resolve_model_scope(patterns, registry)`, unwraps each `ScopedModel.model`,
     and **RE-PROJECTS** the matched set back onto `get_available()` insertion
     order (resolve_model_scope returns PATTERN order; the picker `✱` marker +
     any cycle rotation assume canonical order). Identity is `(provider, id)`.
   - **EMPTY-MATCH GUARD:** a concrete list matching ZERO available models does
     NOT lock the user out — it returns the FULL list and fires
     `warn("scoped-models allow-list matched no available models — showing all")`.

   The chain is strictly one-directional: `consumer → scoped_available →
   resolve_model_scope → get_available() (UNSCOPED)`. No method
   `resolve_model_scope` depends on is made scope-aware, so there is no cycle.
   `ModelRegistry.get_available()` is NEVER modified (stays auth-only, pi parity).

2. **Scoped consumers:**
   - **`/model` picker** (`tui/model_picker.run_model_picker`, wired from
     `shell._open_model_picker`) — the HEADLINE enforcement: only enabled models
     are offered; the empty-match warning commits into the transcript.
   - **`--list-models`** (`cli/list_models.list_models`, wired from
     `cli/entry.py`) — CLI parity; `entry.py` constructs a `SettingsManager`
     (same `SettingsManager.create(cwd=…, agent_dir=get_agent_dir())` as the main
     path so both read the same `settings.json`) and passes it; the empty-match
     warning goes to stderr.

3. **Deliberately NOT scoped:**
   - **The `/scoped-models` seed** keeps seeding from the FULL auth-filtered
     `get_available()` (NOT `scoped_available`), so a DISABLED model stays visible
     + re-checkable — scoping the seed would make it permanently un-re-enableable.
   - **`get_available()` itself** — auth-only, pi parity; it is
     `resolve_model_scope`'s data source.
   - **Startup model resolution** (`resolve_cli_model` / `find_initial_model` /
     current selection) — the user's chosen/default/current model must stay usable
     even if it falls outside the allow-list (avoids a startup lockout). The
     allow-list governs the picker menu, not the active selection.

## Consequences

- **PARTIAL SCOPE (documented honestly): RPC + cycle are NOT scoped this turn.**
  The protected harness (`packages/aelix-agent-core/harness/core.py`) has NO
  model-list rotation at all (it exposes only `set_current_model` + async
  `set_model`, neither of which reads a list) — so there is no protected cycle to
  worry about. The ONLY cycle path is the coding-agent's own RPC
  `_handle_cycle_model` (+ `_handle_set_model` / `_handle_get_available_models`)
  in `rpc/rpc_mode.py`, which is editable. Those handlers thread a
  `ModelRegistry` but NOT a `SettingsManager`, and `entry.py` does not even pass a
  registry to `run_rpc_mode` today — scoping them would mean threading a
  `SettingsManager` through the RPC public signature (touching every
  `run_rpc_mode` caller). That is pure-coding-agent and CAN be done, but it is
  deferred to keep this turn focused on the user's headline ask (the TUI/CLI
  surfaces). **Therefore: an external RPC client (e.g. a Web UI) can still
  set/cycle to a disabled model — the scope is a TUI/CLI-surface guard, NOT a
  hard policy boundary.** When RPC is scoped later, `set_model` rejects a disabled
  model with the existing `Model not found` idiom and `cycle` rotates only over
  the enabled set (preserving the `len<=1` no-op).
- `resolve_model_scope` writes a per-pattern `Warning: No models match pattern …`
  to `sys.stderr` for partially-unmatched patterns; inside the TUI picker that is
  a rare stray line (the helper's own `warn` sink + empty-match degrade are the
  primary UX). Accepted; documented.
- **ZERO edits under `packages/aelix-ai` or `packages/aelix-agent-core`** — the
  helper + all wiring live under
  `packages/aelix-coding-agent/src/aelix_coding_agent/`. The protected harness has
  no model-list rotation, so cycle enforcement never required a protected edit.
- ADR-0161's "enforcement pending" confirmation phrasing is obsolete; the
  `/scoped-models` message now reads "… enabled (persisted, global scope; /model
  now restricted to these)". The module docstring documents the active effect +
  the RPC partial scope.
