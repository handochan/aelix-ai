# 0105. Sprint 6h₁₀b — Concrete ExtensionUIContext + Live Chrome (Phase 5c-tui)

Status: Accepted (Sprint 6h₁₀b / Phase 5c-tui sprint 2 of ~4 / W6 shipped)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₁₀a (ADR-0104) shipped the thin TUI shell on aider "Option A" sequential
ownership and left `ExtensionContext.ui` **headless** (every `ctx.ui.*` raised).
Sprint 6h₁₀b lands the concrete prompt-toolkit + Rich implementation of the
Pi-parity 27-method `ExtensionUIContext` surface, an Aelix widget layer, the
**persistent live chrome**, `bind_ui` wiring, and concrete themes — making the
TUI a real first-class consumer of the extension UI contract (ADR-0100).

## The architecture decision (the crux)

**A persistent live chrome is incompatible with 6h₁₀a's `prompt_async` model.**
`PromptSession.prompt_async` exits the Application on every Enter, so the chrome
(status/footer/spinner) cannot stay live during a turn. 6h₁₀b therefore replaces
`prompt_async` with a **single long-running `Application(full_screen=False)`**
that runs the entire session (prompt-toolkit's own `ProgressBar` pattern):

```
┌─ scrollback (committed output, scrolls up) ── Rich render → chrome.print_above (in_terminal)
├─ stream widget (in-progress window) ─────────  StreamRenderer set_tail → chrome widget
├─ input editor (prompt-toolkit Buffer) ───────  always live
├─ working spinner (refresh_interval ~20 FPS) ─  ConditionalContainer(is_running)
├─ status line (ext set_status segments) ──────  renderer_height_is_known gate
└─ footer (git branch + ext statuses) ─────────  renderer_height_is_known gate
```

Source-verified (prompt-toolkit 3.0.52): `full_screen=False` keeps native
scrollback while pinning the chrome at the terminal's bottom edge; `in_terminal`
is the canonical "print above a running app" protocol (validated headless by the
6h₁₀b spike — it does not hang under `create_pipe_input` + `DummyOutput`);
`refresh_interval` drives the spinner with no busy loop; `renderer_height_is_known`
gates chrome rows until the first CPR (no flicker).

**Rich/prompt-toolkit seam (binding):** prompt-toolkit owns the terminal +
chrome; **Rich is the rendering engine** (markdown/syntax/panels → ANSI). Rich
`Live` is NOT used (it contends with the prompt-toolkit renderer for the single
bottom region + cursor). The 6h₁₀a `StreamRenderer` window/throttle logic is kept
but made **sink-based**: stable lines → `commit` (the output pump → `print_above`),
the trailing window → `set_tail` (the chrome stream widget). A synchronous
renderer + an async output pump (decoupled by a queue) preserves ordering across
the sync harness-subscribe boundary.

## Decision (deliverables)

New `aelix_coding_agent/tui/` modules + rework (9 atomic units §A–§I):
- `themes.py` — concrete `default`/`dark`/`light` Theme/EditorTheme (Rich Style) + registry.
- `widgets.py` — `LinesComponent`/`RichComponent`/`VStack` (Component/Container).
- `footer_data.py` — `AelixFooterData` (git branch from `.git/HEAD` + status store).
- `chrome.py` — `AelixChrome`: long-running Application, input queue + `get_input`,
  `print_above` (in_terminal), status/footer/spinner/widget setters, floats/focus.
- `overlay.py` — `make_float` (9 anchors), `AelixOverlayHandle`, `show_modal` runner.
- `context.py` — `AelixTUIContext`: concrete 27-method `ExtensionUIContext`
  (dialogs→overlay, status/working/footer/header/widget→chrome, custom→overlay,
  editor-remote→Buffer, theme→registry) + `AelixTUI`/`AelixKeybindings` facades.
- `render.py`/`stream.py` — reworked to the commit/set_tail sink model (no Rich Live).
- `shell.py` — `run_tui` drives the chrome, binds the UI, runs the output pump.

## Aelix-additive divergences / decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | `prompt_async` → long-running `Application` | persistent chrome requires a continuously-running app (§"crux") |
| 2 | Rich `Live` dropped; streaming via `in_terminal` + chrome stream widget | Live contends with the pt renderer; window/throttle logic preserved sink-based |
| 3 | UI bound **globally** on the shared `_ExtensionRuntime` | all in-process loaded extensions are Tier-1 trusted; per-extension `ui_tui_trusted` gating deferred |
| 4 | **Built-in themes only** (`default`/`dark`/`light`) | manifest `ThemeContrib` has no `name` field — touching it would change `docs/contracts` (protected); deferred. **Zero contract change.** |
| 5 | input via `asyncio.Queue` (not a Future) | robust to input before/after `get_input`; loop-agnostic construction |
| 6 | output pump (queue → `print_above`) | decouples the sync harness-subscribe sink from async `in_terminal`; preserves order |
| 7 | dialogs minimal-but-functional (confirm y/n, select by number, input/editor Buffer) | full arrow-key widgets are polish; surface + behavior complete |

## Deferred (Phase 5c carry-forward)

| Item | Owner |
|---|---|
| Tier-2 descriptor → Rich renderable mapping (8 `DescriptorKind`) | 6h₁₀c / dedicated |
| Manifest-contributed themes (`ThemeContrib.path`) + any `manifest.py`/contract change | later (contract-touching) |
| Per-extension `ui_tui_trusted` capability gating | later |
| `on_terminal_input` raw-input routing; autocomplete provider wiring; custom editor-component swap | later |
| Markdown rendering of streamed text (plain text this sprint) | later |
| Inline images (`term-image`/Kitty/iTerm2) | 6h₁₀c |
| pyte snapshot tests | 6h₁₀d |
| Root `aelix` console-script routing (ADR-0104 follow-up) | follow-up |

## Verification

- ruff clean; `uv run pyright` holds the 8-error baseline (all in
  `scripts/pyright_spike.py`), **zero from `tui/`**.
- `tests/tui/` 172 pass; full project gate green (no global `-W error`; the
  pre-existing `-W error` warnings in `tests/tools` are unrelated). Headless TUI
  tests use `create_pipe_input` + `DummyOutput` + `create_app_session` (the
  validated spike pattern) — no real terminal.
- `python scripts/generate_contracts_schemas.py --check` exit 0; **protected
  paths byte-unchanged** (`rpc`/`harness`/`mcp`/`pyright_spike.py`/`docs/contracts`).
- W4 independent review (code-reviewer HIGH + test-engineer); qa-tester real-PTY
  chrome smoke.

### W4 review hardening (applied)

- **Commit/tail ordering**: commit + live-tail both flow through one tagged
  `output_queue` applied by the pump in order, so the final tail-clear can never
  race ahead of the async (`in_terminal`) commit flush.
- **Signal teardown**: SIGTERM/SIGHUP request a clean shutdown (`chrome.request_eof()`
  + `chrome.exit()`) so `run_tui`'s `finally` performs full teardown — no more
  `sys.exit` from a detached task that bypassed UI-unbind / unsubscribe / dispose.
- **Spinner**: advances by a monotonic clock (honors `interval_ms`), not render
  cadence (injectable for tests).
- **Dialogs**: `select` caps to the 9 bindable options (+"more" note); `editor`
  uses Esc=cancel / Ctrl+S=save (consistent with the other dialogs); `notify`
  uses a generation token so a stale timer can't clear a newer message; modal
  `non_capturing` overlays no longer steal focus.
- **`on_terminal_input`**: registration + unsubscribe are functional; raw-input
  *dispatch* (consume/data) is explicitly deferred (not a silent no-op).
- chrome single-row regions strip newlines; input history is recorded; dead
  `render_to_ansi` removed.

## References

| Reference | Use |
|---|---|
| ADR-0104 (6h₁₀a thin shell) | the shell this reworks; StreamRenderer/EventRenderer origin |
| ADR-0100 (ExtensionUIContext Protocol) | the 27-method surface this implements; `bind_ui` seam |
| ADR-0088 (TUI library) | prompt-toolkit + Rich; `[tui]` extra |
| ADR-0094/0096 (4-tier extension / manifest) | `ui_tui_trusted` capability, `TuiWidgetContrib` |
| prompt-toolkit 3.0.52 `ProgressBar` / `in_terminal` / `Application(full_screen=False)` | the persistent-chrome + print-above architecture (source-verified) |

## Phase

Sprint 6h₁₀b / Phase 5c-tui (shipped). Next: **Sprint 6h₁₀c** — Tier-2 descriptor
renderer + inline images (or as sequenced), then 6h₁₀d pyte snapshots.
