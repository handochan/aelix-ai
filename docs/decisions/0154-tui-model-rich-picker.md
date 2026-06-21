# ADR-0154 — TUI `/model` rich picker (TUI v2, WP-7)

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** 6h₂₆
- **Supersedes/relates:** ADR-0113 (`/model` show + switch-by-id), ADR-0132 (`select()` widget),
  ADR-0153 (TUI v2 quick-wins). Roadmap: `.omc/specs/tui-v2-overhaul-roadmap.md` (WP-7, §D mockup).

## Context

`/model` with no argument only printed `model: {id}`; switching required knowing and typing the exact
id (`/model openai/gpt-4o`). The pi-parity target (and the user's mockup) is an interactive picker: a
searchable, numbered, provider-tagged list with a per-highlight detail footer (modality / context
window / base URL / API-key env). Both load-bearing pieces already shipped — the arrow-key/type-to-filter
`select()` widget (ADR-0132, reused by `/settings` + `/resume`) and `ModelRegistry.get_available()` (the
auth-filtered, ordered catalog that already powers `--list-models`). The gap was pure glue.

## Decision

1. **Extend `select()` with an optional `detail` panel** (`tui/context.py`): a backward-compatible
   keyword `detail: Callable[[int], list[str]] | None = None`. The callback receives the ORIGINAL option
   index of the highlighted row and returns extra lines rendered below the list. Default `None` preserves
   every existing caller (`/settings`, `/resume`, the permission prompt) unchanged. It is cosmetic and
   guarded — a raising callback never breaks the modal.
2. **Pure, testable formatting helpers** (`tui/model_picker.py`): `model_picker_labels()` (numbered
   `N. [provider] {id}` rows, `✱` on the current model, unique so index-recovery is lossless) and
   `model_detail_lines()` (modality / context-window / base-url / api-key env via the shared
   `aelix_ai.providers._env_api_keys.ENV_API_KEYS` map — the real var name, never fabricated).
3. **Interactive flow** (`model_picker.run_model_picker`, wired from `shell.py::_open_model_picker`):
   a module-level, dependency-injected async function (duck-typed `registry`/`harness` + `select`/`commit`/
   `refresh_footer` callables) so the WHOLE flow is unit-testable without the prompt-toolkit app. It builds
   labels, `await select("Select Model", labels, detail=…)`, recovers the chosen `Model` by exact-label
   index, then `harness.set_model(model)` directly (no `resolve_model` round-trip — the catalog Model is
   already fully resolved) and refreshes the footer.
   - **Registry source (W-review 6h₂₆ CRITICAL):** the `ModelRegistry` is **threaded explicitly** from
     `entry.py` (its sole owner, built once at startup) into `run_tui(model_registry=…)`. It is NOT read off
     the harness — `AgentHarness` never sets `_model_registry` (only `ExtensionContext` does, in
     `extensions/api.py`), so reading it there would make the picker *always* report "unavailable." When
     `model_registry` is `None` (headless/tests), no-arg `/model` falls back to the status print.
4. **Wiring** (`CommandContext.model_picker` + `_model_handler`): no-arg `/model` opens the picker when the
   host wired it; it falls back to the one-line status print headlessly / when no registry is attached.
   An explicit `/model <id>` skips the picker and switches directly (unchanged).

## Consequences

- Pure TUI-consumer; **no protected-core (`aelix-agent-core`) changes.**
- Graceful degradation: no registry / empty catalog / missing `set_model` / switch failure all surface a
  committed message, never crash the REPL. Empty catalog points the user at provider auth (e.g.
  `OPENROUTER_API_KEY`).
- `select(..., detail=)` is now reusable for future pickers (`/scoped-models`, etc.).
- `detail=` is an `AelixTUIContext`-only extension, deliberately NOT added to the `ExtensionUIContext`
  protocol / headless stub (extensions don't need it); callers type against the concrete context
  (W-review 6h₂₆ MEDIUM).
- The flow is extracted to `run_model_picker` (dependency-injected) so the full path — including every
  degradation branch — is unit-tested without the prompt-toolkit app (W-review 6h₂₆ HIGH).
- Deferred (roadmap): provider grouping + a `Ctrl+P` hotkey (pi's "search/group/Ctrl+P"); the core
  "pick from a searchable list with details" lands here.
