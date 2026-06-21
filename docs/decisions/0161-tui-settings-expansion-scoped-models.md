# ADR-0161 — WP-2 consumers: /settings expansion (~17 rows) + /scoped-models

Status: Accepted
Date: 2026-06-21
Supersedes: extends ADR-0125 (the original 4-row /settings)
Related: ADR-0160 (the WP-2 foundation this builds on — SettingsManager wiring + multiselect), ADR-0154 (the /model picker reused for the default-model row), ADR-0140 (the models.json `enabled_models` allow-list /scoped-models writes)

## Context

ADR-0160 constructed + threaded the pi-parity-pinned `SettingsManager` into the
TUI and added the `multiselect()` checkbox primitive. With those rails in place,
the two user-facing consumers can be built as PURE coding-agent consumers (call
the existing `get_*`/`set_*`/`flush` API only — no field added to the pinned
`Settings` dataclass):

- `/settings` shipped (ADR-0125) only 4 live-session toggles read straight off the
  harness (steering / follow-up / thinking-blocks / thinking-level). The ~80
  persisted pi settings (theme, autocomplete size, double-escape action, image
  handling, …) were unreachable.
- `/scoped-models` (pi's model allow-list) had no TUI surface at all; the
  `enabled_models` setting (ADR-0140 models.json loader) could only be edited by
  hand in `settings.json`.

## Decision

1. **Expand `/settings` to ~17 SettingsManager-backed rows** via a pure
   `tui/settings_rows.py` (`build_settings_rows` + `apply_setting`). Each row is a
   frozen `SettingsRow(key, label, kind, read, help, live, choices, int_range)`;
   `apply_setting` is the ONLY mutation point and dispatches by kind: **bool**
   flips, **enum** cycles the next ordered literal (wraps), **int** clamps via the
   setter then re-reads to surface the clamped value, **action** returns a
   `delegate` sentinel so the shell runs a host live flow. The shell driver
   (`shell._open_settings`) loops `context.select` until Esc (pi parity — several
   settings per open), recovers the row by lossless exact-label index, and
   `await settings_manager.flush()` after each change.

2. **Honest live-vs-persist split.** Five rows DUAL-WRITE (persist via
   SettingsManager AND apply to the live session): theme (`context.set_theme` +
   `sm.set_theme`), default-model (the ADR-0154 `/model` picker +
   `sm.set_default_model_and_provider`), steering / follow-up
   (`harness.set_*_mode` + `sm.set_*_mode`), thinking-level
   (`harness.cycle_thinking_level` + `sm.set_default_thinking_level`),
   thinking-blocks (`renderer.hide_thinking` + `sm.set_hide_thinking_block`). The
   remaining eleven rows are PERSIST-ONLY — grep confirms they have ZERO
   coding-agent consumers today (autocomplete size, hardware cursor, editor
   padding, quiet startup, skill commands, double-escape action, tree-filter mode,
   image auto-resize, block images, terminal progress, clear-on-shrink) — so their
   help text says "Persisted; applies next launch". `markdown.code_block_indent`
   is SKIPPED entirely: `SettingsManager` has a getter but no setter, so a row
   would be dead/unsettable UI.

3. **Add `/scoped-models`** (`tui/scoped_models.py` `run_scoped_models`, DI like
   `run_model_picker`): reads `ModelRegistry.get_available()` + the persisted
   `get_enabled_models()` allow-list, seeds the `multiselect()` with the enabled
   set (intersected with the live catalog so a stale id doesn't ghost a phantom
   checkbox), and on confirm writes `set_enabled_models(None)` when EVERY model is
   checked (the canonical "all enabled" sentinel — never pins the allow-list to
   today's catalog) else `set_enabled_models(sorted(ids))`. After `flush()` it
   re-reads `get_enabled_models()` and commits a round-trip confirmation (the
   setter mutates the merged view synchronously, so read-back is reliable before
   the disk task lands).

4. **Wiring.** Both flows register on `CommandContext` (`settings_action` already
   wired by ADR-0125; new `scoped_models_action`) + `BUILTIN_COMMANDS`
   (`scoped-models`). Both degrade with a committed yellow message — never crash
   the REPL — when their dependency is absent (no SettingsManager / no registry /
   empty catalog / setter raising).

## Consequences

- `/settings` surfaces the persisted pi settings honestly; the live rows take
  effect this session AND persist, the persist-only rows say so.
- `/scoped-models` is **global-scope only** (pi parity): `SettingsManager` exposes
  no `set_project_enabled_models`, and adding one would require a forbidden
  `aelix-ai` edit. Per-project scoping is out of scope.
- **PERSIST-ONLY at this ADR — enforcement landed later in ADR-0162.** This ADR
  shipped `/scoped-models` as a writer of the `enabled_models` allow-list; NOTHING
  consumed it (`ModelRegistry.get_available()` filtered by configured auth only),
  so the allow-list was durable but inert and the confirmation said "enforcement
  pending". **ADR-0162** wires the consumption (a `scoped_models_filter.scoped_available`
  helper at the `/model` picker + `--list-models`); the "enforcement pending"
  phrasing here is now obsolete — see ADR-0162.
- Persistence is fire-and-forget (`set_*` → asyncio task); the in-memory read-back
  is synchronous + reliable, but durability needs the next loop tick — hence the
  `flush()` after each change + in `run_tui`'s `finally`. Tests drive setters under
  a running loop (async tests / the live TUI) since `_save()` schedules
  `asyncio.ensure_future`.
- ZERO edits under `packages/aelix-ai` or `packages/aelix-agent-core` — every
  interaction is CONSTRUCT (foundation) + CALL existing methods. All new code lives
  under `packages/aelix-coding-agent/src/aelix_coding_agent/tui/`.
