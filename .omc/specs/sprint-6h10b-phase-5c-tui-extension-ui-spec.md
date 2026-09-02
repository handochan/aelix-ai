# Sprint 6h₁₀b — Concrete ExtensionUIContext + Live Chrome (Phase 5c-tui, sprint 2 of ~4)

Status: DRAFT (W1 spec — pending user approval; becomes Binding on approval, do not modify after W1 closure)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — Sprint metadata

| Field | Value |
|---|---|
| Sprint id | `6h₁₀b` |
| Phase | 5c-tui, sprint 2 of ~4 |
| Workflow | ADR-0032 W0-W6 |
| Scope class | Code (concrete ExtensionUIContext + widget layer + live chrome + bind_ui + themes; reworks `tui/shell.py`) + tests + 1 ADR |
| Predecessors | Sprint 6h₁₀a (thin TUI shell — ADR-0104) |
| Owning ADR closure | ADR-0105 (NEW) |
| Scope decision | **Full ExtensionUIContext + live chrome** (user-approved 2026-05-25). Tier-2 descriptor→Rich renderer, inline images, pyte snapshots remain deferred. |

---

## §1 — Background

### §1.1 — What 6h₁₀a left for here

ADR-0104 (Sprint 6h₁₀a) shipped the thin TUI shell on **aider "Option A" sequential ownership**: `PromptSession.prompt_async` owns the terminal only while awaiting input; Rich renders streamed output only while a turn runs; they never overlap. The `ExtensionContext.ui` binding stayed **headless** (every `ctx.ui.*` raises `NotImplementedError`). Deferred to here:

- Concrete `ExtensionUIContext` (27 methods + `theme` property) on prompt-toolkit + Rich.
- Aelix widget layer (`Component` / `Container` / overlay) concrete classes.
- **Live chrome**: persistent status line + footer + working/spinner indicator + input editor, pinned at the bottom and visible during *both* idle input *and* streaming output.
- `bind_ui()` wiring so loaded extensions get the real UI.
- Concrete themes (at least `default` / `dark` / `light`).

### §1.2 — The architecture decision (W0 — the crux)

**A persistent live chrome is incompatible with the 6h₁₀a `prompt_async` model.** `PromptSession.prompt_async` runs a `full_screen=False` Application that **exits on every Enter** (`prompt.py` `~is_done` gate hides the bottom toolbar and the app returns). There is no way to keep status/footer/spinner live *during a turn* while `prompt_async` is the input mechanism — between turns there is simply no running Application.

**Decision (source-verified against prompt-toolkit 3.0.52):** Sprint 6h₁₀b replaces `prompt_async` with a **single long-running `Application(full_screen=False)`** that runs for the entire session. This is prompt-toolkit's own `ProgressBar` pattern (`shortcuts/progress_bar/base.py`): a persistent inline Application whose chrome stays live while work runs, with output flushed *above* it.

| Mechanism | Source | Role in 6h₁₀b |
|---|---|---|
| `Application(full_screen=False)` | `application.py:194`, `renderer.py:601-648` | inline (no alt-screen); chat history scrolls into native scrollback; chrome pinned at the terminal's bottom edge via CPR-measured `_min_available_height` |
| `in_terminal()` async CM | `application/run_in_terminal.py` | the canonical "print above a running app" protocol — suspends the renderer, lets Rich write into scrollback, repaints chrome below. Replaces 6h₁₀a's direct `console.print` |
| `refresh_interval=0.05` + `min_redraw_interval=0.05` | `application.py:205,545-557` | drives the spinner at ~20 FPS with no busy loop; coalesces streaming invalidations |
| `renderer_height_is_known` filter | `renderer.py:440-452` | gates chrome containers until the first CPR arrives (avoids first-render flicker; same guard `PromptSession.bottom_toolbar` uses) |
| Rich → `ANSI` formatted-text bridge | `prompt_toolkit.formatted_text.ANSI` | Rich renders status/footer segments to ANSI strings → wrapped in a `FormattedTextControl`. Rich stays the *rendering engine*; prompt-toolkit owns *terminal layout + ownership* |

**Rich/prompt-toolkit seam (binding):** prompt-toolkit owns the chrome + terminal; Rich is the rendering engine. Streamed turn output is rendered by Rich and flushed via `in_terminal`; chrome segments are Rich-rendered-to-ANSI inside `FormattedTextControl`. Rich `Live` is **NOT** used for the live region in 6h₁₀b (it contends with the pt renderer) — the 6h₁₀a `StreamRenderer` is adapted to flush through `in_terminal` instead of owning a `Live`.

**Consequence:** `tui/shell.py` is reworked. The 6h₁₀a `prompt_async` input loop → an explicit key-binding-driven input `Buffer` on the long-running Application; `run_tui`'s loop → the Application's run + a turn-dispatch coroutine. The 6h₁₀a `StreamRenderer` throttle/scrollback-split machinery and `EventRenderer` dispatch **carry forward** — only the output sink changes (`console.print` → `await chrome.print_above(...)`).

### §1.3 — bind_ui wiring (verified path)

`run_tui` reaches the extension UI binding via:
```
runtime_host.harness.runtime.bind_ui(concrete_ui)
```
- `AgentSessionRuntime.harness` (`agent_session_runtime.py:232`) → `AgentHarness.runtime` (`harness/core.py:739`) → `_ExtensionRuntime.bind_ui(ui)` (`api.py:448`).
- `has_ui` (`api.py:791`) is an identity check: `runtime.ui is not HEADLESS_UI_CONTEXT`.
- **Lifecycle point**: between `harness.bootstrap()` and the first `_rebind`/input in `run_tui` (`shell.py:105-106`), so extensions activating on `session_start` already see a live UI. Binding *after* the first activation would let them hit the headless raise.

### §1.4 — Open decisions resolved in this spec

| # | Open question (from exploration) | Resolution (this sprint) |
|---|---|---|
| 1 | All extensions share one `_ExtensionRuntime._ui`; do we gate the real UI to `ui_tui_trusted` extensions only? | **Bind the real UI globally** on the shared runtime for 6h₁₀b — every in-process loaded extension is already Tier-1 trusted Python. Per-extension `ui_tui_trusted` *capability gating* (proxy/raise for non-declaring extensions) is deferred to a later sprint and noted in ADR-0105. |
| 2 | `ThemeContrib` has only `path` (no `name`); `get_theme(name)` resolution + does the contract need a `name` field? | **Defer manifest-contributed themes.** 6h₁₀b ships **built-in concrete themes only** (`default`/`dark`/`light`); `get_all_themes`/`get_theme`/`set_theme` operate over the built-in registry. `ThemeContrib` loading is deferred → **no change to `docs/contracts` (protected) or `manifest.py`**. |
| 3 | `HeadlessExtensionUIContext.theme` raises; concrete `theme` must not. | The concrete `AelixTUIContext.theme` returns the active `Theme` (never raises). |
| 4 | Multi-line input vs 6h₁₀a single-line | The long-running Application's input `Buffer` supports multi-line (Enter submits on a complete line; `Alt+Enter`/continuation for explicit newline — final keybinding spelling decided in W2, documented in ADR-0105). |

### §1.5 — Out-of-scope (deferred)

| Item | Owner sprint |
|---|---|
| Tier-2 descriptor → Rich renderable mapping (8 `DescriptorKind`) | 6h₁₀c (or dedicated) |
| Manifest-contributed themes (`ThemeContrib.path` loading) + any `manifest.py`/contract change | later (contract-touching) |
| Per-extension `ui_tui_trusted` capability gating (proxy/raise for non-declaring exts) | later |
| Inline images (`term-image` / Kitty / iTerm2) | 6h₁₀c |
| pyte snapshot tests | 6h₁₀d |
| Markdown rendering of streamed assistant text (6h₁₀a ships plain text; can fold in here if low-risk, else defer) | 6h₁₀b stretch / later |
| Autocomplete provider stacking beyond a basic slash/path completer | later |
| `--resume` interactive picker | later |

---

## §2 — Scope (deliverables)

New subpackage `aelix_coding_agent/tui/` additions + rework:

| # | Deliverable | Type | Module |
|---|---|---|---|
| 1 | `widgets.py` — concrete `Component`/`Container` (+ a `LinesComponent` wrapping `list[str]`, a `RichComponent` wrapping a Rich renderable) satisfying `widget_protocols` | Code | `tui/widgets.py` (NEW) |
| 2 | `themes.py` — concrete `Theme`/`EditorTheme` instances (`default`/`dark`/`light`) backed by Rich `Style` + prompt-toolkit `Style`; a theme registry | Code | `tui/themes.py` (NEW) |
| 3 | `chrome.py` — `AelixChrome`: the long-running `Application(full_screen=False)` (input Buffer + status/footer/spinner windows), `print_above(renderable)` via `in_terminal`, `get_input()` via key-binding Future, spinner on `refresh_interval` | Code | `tui/chrome.py` (NEW) |
| 4 | `overlay.py` — prompt-toolkit `Float`-based overlay (9 anchors per `OverlayOptions`) + `OverlayHandle` impl (hide/show/focus/…) | Code | `tui/overlay.py` (NEW) |
| 5 | `context.py` — `AelixTUIContext`: concrete `ExtensionUIContext` (all 27 methods + `theme`) delegating to `AelixChrome`/overlay/theme registry. Dialogs via overlay; status/working/footer/header via chrome; editor remote-control via the input Buffer; theme via registry | Code | `tui/context.py` (NEW) |
| 6 | `footer_data.py` — `ReadonlyFooterDataProvider` impl (git branch + extension statuses from `set_status`) | Code | `tui/footer_data.py` (NEW) |
| 7 | `shell.py` rework — `run_tui` drives `AelixChrome` (long-running Application) instead of `prompt_async`; builds `AelixTUIContext`; calls `runtime.bind_ui(ctx)` post-bootstrap; routes `EventRenderer` output through `chrome.print_above`. Preserve the 6h₁₀a lifecycle (rebind/dispose/signal-handler opt-out) + input parsing (`parse_input_line`) + bash passthrough | Code | `tui/shell.py` |
| 8 | `render.py` adaptation — `EventRenderer`/`StreamRenderer` flush via `chrome.print_above` (injected sink) instead of owning a Rich `Live`; throttle/scrollback-split logic preserved | Code | `tui/render.py`, `tui/stream.py` |
| 9 | Tests — context conformance (isinstance ExtensionUIContext; all 27 methods callable, no NotImplementedError), chrome headless smoke (pipe input + DummyOutput; status/footer/spinner state; print_above ordering), overlay anchors, themes, bind_ui flips has_ui, run_tui integration smoke | Tests | `tests/tui/` |
| 10 | ADR-0105 closure | Docs | `docs/decisions/0105-sprint-6h10b-extension-ui-and-chrome.md` |

**Module layout:**
```
tui/
  __init__.py        # run_tui (+ maybe AelixTUIContext export)
  shell.py           # run_tui — reworked: drives AelixChrome
  chrome.py          # AelixChrome — long-running Application + live regions      (NEW)
  context.py         # AelixTUIContext — concrete ExtensionUIContext (27 methods) (NEW)
  widgets.py         # concrete Component/Container                               (NEW)
  overlay.py         # Float-based overlay + OverlayHandle                        (NEW)
  themes.py          # concrete Theme/EditorTheme + registry                      (NEW)
  footer_data.py     # ReadonlyFooterDataProvider                                 (NEW)
  render.py          # EventRenderer — output sink → chrome.print_above           (adapt)
  stream.py          # StreamRenderer — flush via injected sink                   (adapt)
  input.py           # parse_input_line (unchanged) + keybindings for the Buffer  (adapt)
```

---

## §3 — Per-deliverable specifications (summary; full method table in W1 fold-in)

### §3.1 — `chrome.py` — `AelixChrome` (the live bottom region)
- `Application(full_screen=False, refresh_interval=0.05, min_redraw_interval=0.05)`; `Layout(HSplit([...]))`:
  - `FloatContainer(HSplit([Window(BufferControl(input_buffer))]), floats=[CompletionsMenu float])` — multi-line input editor.
  - spinner `Window(FormattedTextControl(spinner), height=1, filter=is_running & renderer_height_is_known)`.
  - status `Window(FormattedTextControl(status), height=1, filter=renderer_height_is_known)`.
  - footer `Window(FormattedTextControl(footer), height=1, filter=renderer_height_is_known)`.
- `async print_above(renderable) -> None`: `async with in_terminal(): self._console.print(renderable)`; then `app.invalidate()`. **Single flush per coalesced StreamRenderer render** (reuse the 6h₁₀a `min_delay` throttle so we don't pay a CPR round-trip per token).
- `async get_input() -> str`: awaits a Future resolved by the Enter key-binding (submits `input_buffer.text`, resets buffer). EOF (Ctrl+D) / KeyboardInterrupt (Ctrl+C) bindings mirror 6h₁₀a semantics (exit / clear-line, abort mid-turn).
- State setters used by `AelixTUIContext`: `set_status(key, text)`, `set_footer_factory`, `set_working(message, visible)`, `set_working_indicator(frames, interval)`, `set_title`, dynamic-widget slots (`set_widget(key, content, placement)`) via a `DynamicContainer` above/below the editor.
- Spinner advances a frame index inside its `FormattedTextControl` callable (driven by `refresh_interval`; idle = no CPU).

### §3.2 — `context.py` — `AelixTUIContext` (27 methods + theme)
Concrete `ExtensionUIContext`. Group → backing:
- **Dialogs (5)** `select`/`confirm`/`input`/`editor` → modal overlay (`overlay.py`) awaiting a Future; `notify` → transient toast via chrome (auto-dismiss).
- **Raw input (1)** `on_terminal_input` → register handler on the chrome's key processor; returns unsubscribe.
- **Status/working (5)** → `AelixChrome` setters.
- **Layout (5)** `set_widget` (list[str] | factory) / `set_footer` / `set_header` / `set_title` → chrome dynamic slots.
- **Custom overlays (1)** `custom` → build component via factory, show in overlay, return result Future; `OverlayHandle` via `on_handle`.
- **Editor remote control (5)** `paste_to_editor`/`set_editor_text`/`get_editor_text`/`set_editor_component`/`get_editor_component` → the chrome input `Buffer` (+ a replaceable editor-component seam).
- **Autocomplete (1)** `add_autocomplete_provider` → push onto the chrome completer stack.
- **Theme (5+prop)** `theme` property + `get_all_themes`/`get_theme`/`set_theme`/`get_tools_expanded`/`set_tools_expanded` → `themes.py` registry (built-ins only this sprint). `set_theme` invalidates the Application.
- Pi-parity divergences (snake_case, seconds, asyncio.Event signal) carry forward from ADR-0100; documented in ADR-0105.

### §3.3 — `themes.py`, `widgets.py`, `overlay.py`, `footer_data.py`
- `themes.py`: `default`/`dark`/`light` `Theme` (fg/bg/bold/italic backed by Rich `Style`), matching `EditorTheme`; `THEMES` registry + `get/list/set`. Non-raising.
- `widgets.py`: `LinesComponent(list[str])` + `RichComponent(renderable)` implementing `Component.render(width)->list[str]`; a minimal `Container`.
- `overlay.py`: map `OverlayOptions` (9 anchors + sizing + responsive `visible`) → prompt-toolkit `Float` placement; `OverlayHandle` (hide/set_hidden/is_hidden/focus/unfocus/is_focused) over the Float lifecycle.
- `footer_data.py`: `ReadonlyFooterDataProvider` — git branch (cwd) + extension statuses (mirrors `set_status` store) + `on_branch_change`.

### §3.4 — `shell.py` rework
`run_tui(runtime_host, *, cwd, chrome=None, install_signal_handlers=True)`:
1. Build `AelixChrome` (or injected for tests) + `AelixTUIContext(chrome, theme_registry, footer_data)`.
2. Signal handlers + `set_rebind_session` (carry forward).
3. `await harness.bootstrap()` → `runtime_host.harness.runtime.bind_ui(ctx)` → `_rebind`.
4. `EventRenderer` output sink = `chrome.print_above` (injected); subscribe.
5. Run `chrome.app.run_async()` as the session loop; an input-dispatch coroutine awaits `chrome.get_input()` → `parse_input_line` → quit/reload/bash/prompt (same precedence as 6h₁₀a), driving `harness.prompt` with the turn running while the chrome stays live.
6. `finally`: unbind UI (rebind headless), unsubscribe, remove signals, dispose.
- **Failed-turn survival** + **KeyboardInterrupt→abort** carry forward from 6h₁₀a.

---

## §4 — Commit split plan (W6, atomic) — provisional

| # | § | Subject |
|---|---|---|
| 1 | §A | `feat(tui): concrete themes — Theme/EditorTheme + registry (6h₁₀b §A)` |
| 2 | §B | `feat(tui): concrete widgets — Component/Container (6h₁₀b §B)` |
| 3 | §C | `feat(tui): overlay — Float-based OverlayHandle + 9 anchors (6h₁₀b §C)` |
| 4 | §D | `feat(tui): AelixChrome — long-running Application + live regions (6h₁₀b §D)` |
| 5 | §E | `feat(tui): ReadonlyFooterDataProvider (6h₁₀b §E)` |
| 6 | §F | `feat(tui): AelixTUIContext — concrete ExtensionUIContext 27-method surface (6h₁₀b §F)` |
| 7 | §G | `refactor(tui): EventRenderer/StreamRenderer flush via chrome.print_above (6h₁₀b §G)` |
| 8 | §H | `feat(tui): run_tui drives AelixChrome + bind_ui wiring (6h₁₀b §H)` |
| 9 | §I | `docs: ADR-0105 ExtensionUIContext + live chrome closure (6h₁₀b §I)` |

Co-author trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` (repo convention). **Not committed/pushed until user authorizes.**

---

## §5 — Verification gates (W5)
- ruff clean; `uv run pyright` holds the 8-error baseline (0 from `tui/`).
- New + adapted `tests/tui/` pass; full suite stays green (2608 baseline + new). Headless: `create_pipe_input` + `DummyOutput` + `create_app_session`; **no real TTY, no sleeps** (drive the Application via pipe input; assert chrome state + `print_above` ordering).
- `python scripts/generate_contracts_schemas.py --check` exit 0; **protected paths byte-unchanged** (`rpc`/`harness`/`mcp`/`pyright_spike.py`/**`docs/contracts`** — note: NOT changing `manifest.py`/contracts is a hard constraint, see §1.4 #2).
- qa-tester real-PTY: launch, observe persistent status/footer/spinner during a turn, an extension `ctx.ui.set_status(...)` reflects in the footer, `/quit` exits 0.
- W4 independent review (code-reviewer HIGH + test-engineer); manual QA of the chrome.

## §6 — Risk register
| Risk | Likelihood | Mitigation |
|---|---|---|
| Long-running Application ↔ Rich `in_terminal` cursor coordination | Med-High | `in_terminal` is the canonical protocol (renderer suspended during write); never run Rich `Live` concurrently; flush once per coalesced render |
| Reworking 6h₁₀a `prompt_async` loses PromptSession features (history/multiline/vi) | Med | Reimplement on the Buffer (history via `Buffer(history=...)`, multiline keybindings); covered by tests + qa |
| First-render CPR flicker | Low | `renderer_height_is_known` gate on chrome containers |
| 27-method surface scope creep | Med | Table-driven impl; dialogs/overlay share one modal primitive; editor-component replacement is a thin seam |
| Accidental contract/protected-path change (ThemeContrib) | Low | Built-in themes only this sprint; no `manifest.py` edit (§1.4 #2) |
| bind_ui lifecycle ordering (extensions hit headless) | Med | Bind between bootstrap and first activation (§1.3); test asserts `has_ui` True before first turn |

## §7 — Acceptance criteria
- [ ] `AelixTUIContext` satisfies `ExtensionUIContext` (runtime_checkable); all 27 methods + `theme` work without `NotImplementedError`.
- [ ] Persistent chrome: status/footer/spinner visible during *both* idle input and a running turn; output scrolls into scrollback above the pinned chrome.
- [ ] `bind_ui` makes `ctx.has_ui` True; an extension calling `ctx.ui.set_status(...)` updates the footer; headless restored on dispose.
- [ ] 6h₁₀a behaviors preserved: `!`/`!!`/`/reload`/`/quit`, session-rebind, failed-turn survival, clean dispose, exit 0.
- [ ] All gates green (§5); protected paths + contracts byte-unchanged; built-in themes only.
- [ ] ADR-0105 shipped; deferred items (descriptor renderer, manifest themes, per-ext gating, images, snapshots) carried forward.
