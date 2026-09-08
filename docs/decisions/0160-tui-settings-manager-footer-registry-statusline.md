# ADR-0160 — WP-2 foundation: SettingsManager wiring + footer segment registry + multiselect + /statusline

Status: Accepted (shipped) — **AMENDED 2026-09-08 by #248** (`## Amendment
(2026-09-08, #248)` below): the default-enabled set is no longer byte-identical
to the pre-ADR-0160 footer. Everything else in this ADR stands.
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
   *(That last sentence is the 2026-06-21 record; see the amendment below — it no
   longer holds.)*

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
  invariants; out-of-box rendering is unchanged (golden test). *(The
  out-of-box-unchanged half is the 2026-06-21 record; see the amendment below.)*
- The SettingsManager is reachable by reference for ADR-0161 (`/settings` expansion
  + `/scoped-models`) via `CommandContext.settings_manager`.
- `use_theme_colors` is stored but not yet applied per-segment (the footer is a
  single plain joined string today) — a deliberate follow-on. Optional token/cost
  footer segments are default-OFF and read context-cached scalars
  (`set_usage_stats` on `turn_end`) so the footer producer never awaits.
- ZERO edits under `packages/aelix-ai` or `packages/aelix-agent-core`; the
  `AgentHarnessOptions.settings_manager` harness seam is deliberately left untouched
  (wiring it would require editing the protected factory).

## Amendment (2026-09-08, #248)

Decision 2's closing sentence — *"the default-enabled set is byte-identical to
the pre-ADR-0160 hard-coded footer"* — no longer holds. The `thinking-level`
segment (shipped 2026-08-07, default-OFF) is **default-ON** and sits immediately
after `model`, before `context-remaining`.

The byte-identical guarantee was a **migration** property, not a design goal: it
existed so that extracting the inline footer list into a registry changed nothing
a user could see. It expired the moment a new segment was worth showing by
default. What ADR-0160 actually protects is unchanged and is what the tests still
assert: the ADR-0159 invariants live INSIDE the producers, so the adversarial /
empty enabled-set cases still cannot surface a stray badge, move the
security-visible one, or reveal steering at its default. The
`thinking-level` producer's own omit-when-no-provider rule is why a headless
footer is byte-unchanged by this amendment.

Position: `thinking-level` follows `model` in `_SEGMENT_SPEC` and in
`AelixTUIContext._MULTILINE_ROWS[0]`, which already paired the two. The single
line and the multi-line block still order `git-branch` and `context-remaining`
differently; #248 did not touch that, so only the `model → thinking-level`
adjacency is shared. The move — rather than flipping the bool at the registry
tail — is load-bearing: measured on a pyte glass at 80 and at 100 columns, the
row is clipped (not wrapped) and a tail segment did not render at all.

Persistence consequence. `StatuslineStore.load()` takes an existing file's
`enabled` list **verbatim**, so the new default reaches fresh installs and users
with no `statusline.json` — not users who have saved `/statusline` even once.
`_VERSION` stays **1** and no migration is written, for two reasons: the
persisted shape records the enabled set and never the option set the picker
showed, so it cannot distinguish *"never saw this option"* from *"unchecked it"*
(and the segment has been available since 2026-08-07, so some saved files are a
deliberate no); and `load()` is called twice per footer repaint across 20
`_refresh_footer()` call sites — 23 counting the three that repaint through an
injected `refresh_footer()` callable (`grep -rn "refresh_footer()" packages/`) —
so a migration written from `load()` would put a disk write on the repaint path
of a function whose contract is "NEVER raises".

Render order is registry order (`AelixTUIContext._refresh_footer`), not the
persisted list order. So a saved file that **already** enables `thinking-level`
does see the segment move out of the row's tail to after the model — the one
behaviour change #248 reaches existing users with, pinned by
`tests/tui/test_footer_segments.py::test_a_saved_store_that_already_enables_it_gets_the_new_position`.
