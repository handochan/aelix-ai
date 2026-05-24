# 0100. Sprint 6h₉c — ExtensionUIContext Protocol (Pi-parity 27-method surface)

Status: Accepted (Sprint 6h₉c / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₉c is the **third sprint of Phase 5b-foundation**. Sprint
6h₉a (ADR-0098) shipped the `aelix-plugin.toml` v1 manifest contracts;
Sprint 6h₉b (ADR-0099) wired manifest detection into the loader.
Neither sprint addressed the Pi `ExtensionUIContext` surface — the
27-method extension-side UI access façade Pi extensions consume via
`ctx.ui.*`.

Existing `ExtensionContext.ui` in `packages/aelix-coding-agent/src/
aelix_coding_agent/extensions/api.py:744-758` raised
`ExtensionError("invalid_state")` and cited a phantom **"ADR-0033"**
as the owning closure. ADR-0033 **does not exist** — `docs/decisions/`
numbering jumps `0032 → 0034` because the slot was reserved during
Sprint 5a planning but never written. Sprint 6h₉c writes the actual
closure (this ADR, ADR-0100) and clears the phantom citation inline in
`api.py` docstrings.

The replacement design preserves the Sprint 5a "structurally
present, semantically deferred" contract:

- `ExtensionContext.ui` is typed as `ExtensionUIContext` (no more
  `Any`).
- The default binding is a singleton `HeadlessExtensionUIContext` that
  raises `NotImplementedError` per method with a clear "Sprint 6h₁₀b"
  pointer.
- `has_ui` now reflects the bound state — `False` when the headless
  singleton is bound, `True` once Sprint 6h₁₀b
  (Phase 5c-tui) installs a concrete prompt-toolkit + Rich + Aelix
  widget layer impl via `_ExtensionRuntime.bind_ui()`.
- Static type checkers see the right surface today; runtime calls
  fail fast with actionable errors instead of `AttributeError`.

Pi reference note: Sprint 6h₉a fold-in §B (`d97712c`) verified the
Pi count is **27 methods + 1 readonly `theme` property = 28 total
members** at `coding-agent/src/core/extensions/types.ts:124-275`
(SHA `734e08e`). The same inventory binds here.

## Decision (Sprint 6h₉c deliverables 1-6 enumerated)

| # | Deliverable | Type | Closure |
|---|---|---|---|
| 1 | NEW `widget_protocols.py` module — `Component` / `Container` / `Focusable` + `CURSOR_MARKER` / `Theme` / `EditorTheme` / `EditorComponent` / `KeybindingsManager` / `TUI` / `AutocompleteProvider` / `OverlayHandle` / `OverlayOptions` (9 anchors + responsive visibility) / `OverlayMargin` / `ReadonlyFooterDataProvider` Protocols | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/widget_protocols.py` (Commit 1) |
| 2 | NEW `ext_ui.py` module — `ExtensionUIContext` Protocol (27 methods + 1 readonly `theme` property = 28 members) + 16 supporting types | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/ext_ui.py` (Commit 2) |
| 3 | NEW `headless_ui.py` module — `HeadlessExtensionUIContext` class implementing every method with `raise NotImplementedError("Sprint 6h₁₀b — ...")` + module-level `HEADLESS_UI_CONTEXT` singleton | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/headless_ui.py` (Commit 3) |
| 4 | `api.py` integration — `_ExtensionRuntime._ui` field + `ui` property + `bind_ui()` method; `ExtensionContext.ui` returns the typed `ExtensionUIContext` (headless singleton by default); `has_ui` reflects bound state; ADR-0033 phantom citation cleared inline | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` (Commit 3) |
| 5 | 43 tests — Protocol conformance (`runtime_checkable`), 27 + theme surface inventory, per-method `NotImplementedError`, `ctx.ui` headless default, `has_ui` flip on `bind_ui`, `set_widget` overload signatures, OverlayOptions 9 anchors, OverlayMargin factory, widget_protocols smoke imports, public re-exports | Tests | `tests/extensions/test_ext_ui_context.py` (Commit 4) |
| 6 | This closure ADR — surface inventory, 9 Aelix-additive divergences, deferred items, Pi citations, ADR cross-references | Docs | `docs/decisions/0100-sprint-6h9c-ext-ui-context-protocol.md` (Commit 5) |

Key behavioural notes:

- **Headless default binding**: every `ctx.ui.*` call in Phase 5b
  raises `NotImplementedError` with the message
  `"ExtensionUIContext.{method} is not bound in Phase 5b-foundation; "
  "Sprint 6h₁₀b (Phase 5c-tui) supplies the prompt-toolkit + Rich + "
  "Aelix widget layer binding per ADR-0088 amend + ADR-0100. Guard "
  "early calls with `if ctx.has_ui:` (False in headless mode)."`
- **`bind_ui` idempotent semantic**: passing the same binding again
  is a no-op. Passing `HEADLESS_UI_CONTEXT` reverts to the headless
  default and flips `has_ui` back to `False`.
- **No Pydantic**: Sprint 6h₉c uses pure `typing.Protocol` +
  `@dataclass(frozen=True)`. Pydantic remains the manifest contracts'
  validation layer (Sprint 6h₉a) but is NOT used by extension surface
  types (which are runtime Protocols, not validated inputs).
- **Library-agnostic Component (ADR-0088 CRITICAL invariant)**: the
  Aelix `Component` Protocol mirrors Pi `Component` shape but does
  NOT depend on prompt-toolkit or Rich primitives. Sprint 6h₁₀b's
  concrete classes wrap library primitives behind these Protocols
  (composition, not inheritance).

## Surface inventory (Pi `types.ts:124-275` at SHA `734e08e`)

| Group | Methods | Pi line range (verified Sprint 6h₉c fold-in §C) |
|---|---|---|
| Dialogs (5) | `select`, `confirm`, `input`, `notify`, `editor` | 126, 129, 132, 135, 215 |
| Raw input (1) | `on_terminal_input` | 138 |
| Status / working (5) | `set_status`, `set_working_message`, `set_working_visible`, `set_working_indicator`, `set_hidden_thinking_label` | 141-160 |
| Layout (5) | `set_widget` (×2 overloads), `set_footer`, `set_header`, `set_title` | 163-186 |
| Custom overlays (1) | `custom` | 189-204 |
| Editor remote control (5) | `paste_to_editor`, `set_editor_text`, `get_editor_text`, `set_editor_component`, `get_editor_component` | 206-256 |
| Autocomplete (1) | `add_autocomplete_provider` | 218 |
| Theme (5 methods + 1 readonly property) | `theme` (readonly property), `get_all_themes`, `get_theme`, `set_theme`, `get_tools_expanded`, `set_tools_expanded` | 259-274 |

**Sprint 6h₉c fold-in §C note**: line ranges corrected after W5 critic
re-verified all 27 method citations against Pi at SHA `734e08e`. The
prior ranges (126-141/144/147-170/173-198/201-216/221-262/232/265-280)
were drifted by 5-15 lines and inherited from the W1 spec without
direct verification. Sprint 6h₉c fold-in §A landed the same
corrections inline in `ext_ui.py` per-method docstrings.

**Total**: 27 methods + 1 readonly `theme` property = 28 total
members.

## Aelix-additive divergences from Pi

| # | Divergence | Pi behavior | Aelix-additive behavior | Justification |
|---|---|---|---|---|
| 1 | `notify(type)` parameter renamed to `kind` | Pi uses `type` (TS keyword OK) | Aelix uses `kind` (shadowing `type` built-in avoided) | PEP 8 — `type` is a Python built-in |
| 2 | `WidgetPlacement` values snake_case | Pi: `"aboveEditor"` / `"belowEditor"` | Aelix: `"above_editor"` / `"below_editor"` | PEP 8 — JSON wire round-trip preserves snake_case |
| 3 | `WorkingIndicatorOptions.interval_ms` snake_case | Pi: `intervalMs` | Aelix: `interval_ms` | PEP 8 |
| 4 | `ExtensionUIDialogOptions.signal` is `object`-like (asyncio.Event), not `AbortSignal` | Pi: `AbortSignal` | Aelix: any object with `.wait()` coroutine — idiomatic Python | `asyncio.Event` is Python's `AbortSignal` analogue |
| 5 | `ExtensionUIDialogOptions.timeout` in **seconds** (float), not milliseconds | Pi: ms (int) | Aelix: seconds (float) | PEP 8 — asyncio idiom |
| 6 | `HeadlessExtensionUIContext` default binding + `bind_ui()` | Pi: per-mode provider (interactive/print/rpc) directly | Aelix: headless default + concrete binding via `bind_ui()` (Sprint 6h₁₀b) | Phase 5b / 5c separation — Phase 5b ships surface, Phase 5c-tui ships impl |
| 7 | `set_widget` overload via Python `@overload` | Pi: TS overload (function declaration set) | Aelix: `typing.overload` (PEP 484) | Python equivalent |
| 8 | `TerminalInputHandler` return shape is `TerminalInputResult` dataclass | Pi: anonymous object literal | Aelix: typed `TerminalInputResult` dataclass | Python lacks anonymous object types |
| 9 | `custom<T>()` returns `object` instead of generic `T` | Pi: `Promise<T>` | Aelix: `object` (caller narrows) | PEP 695 generic Protocol future sprint |
| 10 | `Component.handle_input` declared **required** on the Aelix Protocol | Pi: `handleInput?(data: string): void;` — **optional** via TS `?` marker (`pi-tui/src/tui.ts:50`) | Aelix: `def handle_input(self, data: str) -> None: ...` — required method; concrete non-interactive widgets MUST supply a stub body (`pass`) | Python `Protocol` does not cleanly support optional methods under `@runtime_checkable` — `isinstance` cannot enforce an absent attribute. Required-with-no-op-stub keeps Protocol structural conformance crisp. `invalidate` is required in both Pi (`tui.ts:62`) and Aelix; no divergence. Added Sprint 6h₉c fold-in §C per W5 critic MAJOR-4. |

All divergences are net-additive — Pi behaviour is structurally
preserved. Every Pi `ExtensionUIContext` method/property maps 1:1 to
an Aelix method/property; only naming/typing idioms differ to honor
Python conventions.

## Deferred items (Phase 5c-tui carry-forward)

| Item | Owner sprint | Reason |
|---|---|---|
| Concrete `ExtensionUIContext` impl on prompt-toolkit + Rich + Aelix widget layer | Sprint 6h₁₀b | Phase 5c-tui (TUI shell first) |
| `Component` / `Container` / `Box` / `Text` / etc. concrete widget classes | Sprint 6h₁₀b | Phase 5c-tui |
| IME `Focusable` + `CURSOR_MARKER` cursor positioning | Sprint 6h₁₀b | prompt-toolkit native IME handles this |
| Kitty image protocol support (`term-image` integration) | Sprint 6h₁₀c | TBD per ADR-0088 amend Q4 (deferred) |
| Snapshot testing (pyte-based) | Sprint 6h₁₀d | Phase 5c-tui |
| Tier 2 descriptor → Rich Renderable mapping | Sprint 6h₉d | next sprint |
| Tier 4 MCP + hooks | Sprint 6h₉e | Phase 5b-foundation #5 |
| `aelix-server` FastAPI HTTP+WS skeleton | Sprint 6h₉f | Phase 5b-foundation #6 |
| `custom<T>()` PEP 695 generic typing | Phase 6 | PEP 695 was finalised after Sprint 5a; generic Protocols + variance gating requires a follow-up sprint |
| `AgentHarness` bridge wiring to call `_ExtensionRuntime.bind_ui()` from a concrete TUI | Sprint 6h₁₀b | depends on TUI shell landing |

## Pi citations (SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`)

- `packages/coding-agent/src/core/extensions/types.ts:124-275` —
  `ExtensionUIContext` 27-method + 1 readonly `theme` property
  surface (verified Sprint 6h₉a fold-in §B).
- `packages/coding-agent/src/core/extensions/types.ts:89-118` —
  Supporting types: `ExtensionUIDialogOptions` (:89-94),
  `WidgetPlacement` (:97), `ExtensionWidgetOptions` (:100-102),
  `TerminalInputHandler` (:106), `WorkingIndicatorOptions` (:109-114),
  `AutocompleteProviderFactory` (:117), `EditorFactory` (:118). Line
  ranges verified Sprint 6h₉c fold-in §C; prior `:96-122` block start
  was off by 7.
- `packages/tui/src/tui.ts:46-65` — `Component` interface (with
  `handleInput?` optional at :50 vs Aelix-required — see divergence
  #10).
- `packages/tui/src/tui.ts:90` — `CURSOR_MARKER` literal
  (`"\x1b_pi:c\x07"` — Aelix matches byte-for-byte after fold-in §A).
- `packages/tui/src/tui.ts:111-117` — `OverlayMargin` interface
  (all sides optional in Pi).
- `packages/tui/src/tui.ts:141-169` — `OverlayOptions` interface
  (9-anchor + responsive visibility).
- `packages/tui/src/tui.ts:182-196` — `OverlayHandle` interface
  (hide / set_hidden / focus / unfocus state).
- `packages/tui/src/tui.ts:938` — `line.indexOf(CURSOR_MARKER)`
  scanning site (proves the marker is exact-byte-match contract).
- `packages/tui/src/index.ts` — exports list (`CURSOR_MARKER`, `TUI`,
  `Theme`, `EditorTheme`, `EditorComponent`, `KeybindingsManager`,
  `AutocompleteProvider`).
- `packages/coding-agent/src/core/footer-data-provider.ts` —
  `ReadonlyFooterDataProvider` runtime object (git branch +
  extension statuses).
- `packages/coding-agent/src/modes/interactive/theme/theme.ts` —
  Pi `Theme` shape (role-based fg/bg color resolvers + bold/italic
  modifiers).

External (non-Pi) references:

- PEP 484 `typing.overload` — Python overload pattern.
- PEP 695 generic Protocols — future direction for `custom<T>()`
  typing.
- ADR-0088 amend — prompt-toolkit + Rich + Aelix widget layer
  (library choice; CRITICAL invariant: library-agnostic `Component`
  Protocol).

## Reference companions

- **ADR-0033** — Phantom (does NOT exist; was a reserved slot in
  `docs/decisions/` between 0032 and 0034 that was never written).
  Sprint 5a `api.py` docstrings cited it as the placeholder owner;
  ADR-0100 is the actual closure that clears the phantom reference
  inline. No backfill ADR-0033 file is created or amended.
- ADR-0088 amend — TUI library choice (prompt-toolkit + Rich + Aelix
  widget layer; library-agnostic `Component` invariant).
- ADR-0094 — 4-tier extension architecture (Tier 1 — trusted Python
  extensions; Sprint 6h₉c ships the runtime UI surface they consume).
- ADR-0096 — Manifest v1 (`capabilities.ui_tui_trusted` flag — Tier 1
  marker; manifest declaration that the extension intends to call
  `ctx.ui.*`).
- ADR-0098 — Sprint 6h₉a closure (contracts shipped; verified method
  count of 27 + 1 property).
- ADR-0099 — Sprint 6h₉b closure (manifest propagation onto
  `Extension.manifest` enables Tier 1 capability detection that Sprint
  6h₁₀b will read to decide whether to bind a real TUI).

## Verification

- `uv run ruff check` — clean.
- `uv run pyright` — 8 baseline errors preserved (intentional fixtures
  in `scripts/pyright_spike.py`); zero new errors introduced.
- `uv run pytest` — baseline + 43 new ExtensionUIContext tests pass.
- `python scripts/generate_contracts_schemas.py --check` — exit 0
  (Sprint 6h₉c touches no contracts package files; no schema drift).
- Smoke 1: `from aelix_coding_agent.extensions import
  ExtensionUIContext, HEADLESS_UI_CONTEXT, HeadlessExtensionUIContext;
  isinstance(HEADLESS_UI_CONTEXT, ExtensionUIContext)` returns `True`
  (`runtime_checkable` Protocol structurally satisfied).
- Smoke 2: a fresh `ExtensionContext` exposes
  `ctx.ui is HEADLESS_UI_CONTEXT` and `ctx.has_ui is False`.
- Smoke 3: `_ExtensionRuntime.bind_ui(concrete)` flips
  `ctx.has_ui` to `True`; `bind_ui(HEADLESS_UI_CONTEXT)` reverts to
  `False`.
- Smoke 4: every headless method raises `NotImplementedError` whose
  message contains both the method name and the substring `"Sprint
  6h₁₀b"`.
- Pi pin held at `734e08e` (no advance — Sprint 6h₉c imports no new
  Pi feature beyond the pinned SHA).

## Phase

Sprint 6h₉c / Phase 5b-foundation (shipped). Next sprint: 6h₉d —
Tier 2 descriptor renderer (TUI Rich Renderable mapping from manifest
`contributes` declarations).
