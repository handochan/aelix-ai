# ADR-0160 — WP-2 foundation: SettingsManager wiring + footer segment registry + multiselect + /statusline

Status: Accepted
Date: 2026-06-21
Supersedes: none (extends ADR-0159 footer rules)
Related: ADR-0157 (permission posture footer badge), ADR-0159 (in-flow modal slot + permission-first/steering-hidden footer), ADR-0161 (the /settings + /scoped-models consumers built on this foundation)

## Context

The coding-agent never constructed the `SettingsManager` (pi-parity-pinned in
`packages/aelix-ai/src/aelix_ai/settings/`), so its ~80 typed get/set methods were
unreachable — `/settings` exposed only 4 live-session toggles via the harness, and
there was no way to surface the persisted pi settings or configure the footer. The
footer itself was an inline segment list in `AelixTUIContext._refresh_footer` with
the ADR-0159 rules (permission badge leading + steering hidden at the default)
hard-coded into that list, so it could not be made user-configurable without
risking those security-visible invariants.

WP-2 of the TUI v2 overhaul roadmap surfaces the SettingsManager + a configurable
footer as a **pure coding-agent consumer**. This ADR lays the four shared rails;
ADR-0161 builds the `/settings` + `/scoped-models` consumers on top.

## Decision

1. **Construct + wire ONE `SettingsManager`** (`entry.py` → `run_tui` →
   `CommandContext`) via `SettingsManager.create(cwd=str(Path.cwd()),
   agent_dir=Path(get_agent_dir()))`. The explicit `agent_dir` is required: the
   `create()` default is XDG `~/.config/aelix`, which would split `settings.json`
   from the agent's `auth.json`/`mcp.json`. Construction is synchronous and
   side-effect-free on read (load errors are captured into `drain_errors`, surfaced
   as a startup warning, never raised). `run_tui`'s `finally` awaits
   `settings_manager.flush()` so fire-and-forget setter writes are durable on exit.
   This is a PURE consumer: construct via the factory + call the existing API only;
   no field is added to the pinned `Settings` dataclass (the loader silently drops
   unknown JSON, so a `status_line` field there would no-op invisibly).

2. **Footer segment registry** (`tui/footer_segments.py`): each footer segment is a
   frozen `FooterSegment(id, label, description, produce, default_enabled)` whose
   `produce` closure reads the LIVE context state. `_refresh_footer` iterates the
   registry in canonical order, gated by an enabled-set. The **ADR-0159 invariants
   live INSIDE the producers, not the enabled-set**: the permission-mode producer
   omits the badge when no provider/posture is wired (and substitutes `● default`
   on DEFAULT), and the steering producer returns `None` at the `one-at-a-time`
   default. An adversarial/empty enabled-set can therefore only HIDE a segment the
   user explicitly unchecked — it can never surface a stray badge or move the
   security-visible badge out of its leading position. The default-enabled set is
   byte-identical to the pre-ADR-0160 hard-coded footer (golden-snapshot test).

3. **`multiselect()` checkbox primitive** (`AelixTUIContext`, sibling to
   `select()`): reuses the proven `show_modal` + arrow-nav + type-to-filter +
   viewport + `<any>` + Esc/c-c scaffolding; adds Space=toggle ✓/☐, Enter=confirm
   (returning `(selected_ids, toggle_states)`), optional extra boolean toggles, and
   a live preview line. Enter/c-j/c-c are bound LOCALLY so they never leak to the
   chrome global accept/clear. The shared dependency of `/statusline` (this ADR) and
   `/scoped-models` (ADR-0161).

4. **Coding-agent-owned statusline store** (`tui/statusline_store.py`): the enabled
   segment-id set + a `use_theme_colors` flag persist at
   `get_agent_dir()/statusline.json` — NOT the pinned `Settings`. `load()` never
   raises (missing/corrupt → registry defaults, mirroring the footer-data degrade
   posture); `save()` is atomic (temp + `os.replace`, keys sorted), modeled on
   `cli/project_trust.py` `ProjectTrustStore`. The `/statusline` command
   (`tui/statusline_picker.py` `run_statusline_picker`, DI like `run_model_picker`)
   drives the multiselect over the segment registry, persists the enabled-id set in
   registry order, and repaints the footer; Esc → no write; every failure commits a
   message and returns.

## Consequences

- The footer is now user-configurable without touching the ADR-0159 security
  invariants; out-of-box rendering is unchanged (golden test).
- The SettingsManager is reachable by reference for ADR-0161 (`/settings` expansion
  + `/scoped-models`) via `CommandContext.settings_manager`.
- `use_theme_colors` is stored but not yet applied per-segment (the footer is a
  single plain joined string today) — a deliberate follow-on. Optional token/cost
  footer segments are default-OFF and read context-cached scalars
  (`set_usage_stats` on `turn_end`) so the footer producer never awaits.
- ZERO edits under `packages/aelix-ai` or `packages/aelix-agent-core`; the
  `AgentHarnessOptions.settings_manager` harness seam is deliberately left untouched
  (wiring it would require editing the protected factory).
