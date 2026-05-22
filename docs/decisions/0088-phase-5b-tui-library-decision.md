# 0088. Phase 5b TUI Library Decision — Analysis Basis (Proposed / Deferred)

Status: Accepted (Sprint 6h₉a / W6 shipped — selection: prompt-toolkit + Rich + Aelix widget layer)
Date: 2026-05-22; Amended 2026-05-22 (Sprint 6h₉a)
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₆ shipped Phase 5a-i + 5a-ii — the non-interactive CLI surface
(`--print` / `--mode text|json|rpc` / `--help` / `--version`) per
ADR-0089. The Phase 5a entry deliberately raises
:class:`NotImplementedError` for interactive mode and emits a stderr
diagnostic pointing here:

    Error: interactive mode not implemented
    (Phase 5b — TUI carry-forward; see ADR-0088).

Phase 5b (interactive TUI) ports the Pi `pi-tui` package — a custom
~9,000 LOC Rust-flavoured component tree authored in TypeScript with
its own diff/render loop, focus manager, theming, and extensible
overlay/dialog system. Pi extensions ship factory functions of shape
`(tui, theme, kb, done) => Component` and consume a **31-method
`ExtensionUIContext`** surface (modal dialogs, stateful chrome, custom
component injection, editor remote control, theme & tool state) — see
`pi/packages/coding-agent/src/extensions/types.ts` at the pinned SHA.

This ADR records the **library-selection analysis** undertaken during
Sprint 6h₆ W0 study so the Phase 5b sprint kickoff has a captured
basis rather than a fresh comparison from cold start. **No selection
is made here** — the decision lands at Phase 5b W0 with the user.

## Library options evaluated

### Single-library candidates

| Candidate | License | Async-native | Live render loop | Reactive state | Stars (proxy) | Notes |
|---|---|---|---|---|---|---|
| **textual** (Textualize) | MIT | yes | yes (compositor) | yes (reactive `Widget` + watchers) | ~28k | Modern, batteries-included; first-party `rich` integration; snapshot testing built-in |
| **rich** (Textualize) | MIT | n/a | partial (`Live`) | no | ~50k | Render primitives only — no app loop / focus / input event router |
| **prompt-toolkit** (Slenders) | BSD-3 | yes (asyncio v3) | yes | no (imperative `Buffer` + `Layout`) | ~9k | Battle-tested in IPython / pgcli / ptpython; best Buffer/editor primitives |
| **blessed** (community) | MIT | no (sync) | no (manual) | no | ~1.5k | Too low-level — pi-tui would have to be rebuilt on raw escape codes |

### Hybrid combinations evaluated

| Combo | Render layer | Input / focus | Pros | Cons |
|---|---|---|---|---|
| **textual + rich** | textual compositor (uses rich under the hood) | textual | First-party combo, modern reactive model, snapshot tests, web-render escape hatch | textual reactive model differs from Pi imperative pattern; `Component` Protocol MUST be library-agnostic to avoid lock-in |
| **prompt-toolkit + rich** | prompt-toolkit `Layout` + ad-hoc `Live` panels | prompt-toolkit `KeyBindings` | Closest to Pi imperative model; pt's `Buffer` is the strongest editor primitive in the Python ecosystem | Hand-rolled diffing for non-prompt panels; rich `Live` doesn't compose into pt layouts cleanly |
| **textual alone** | textual | textual | Single dependency, fewer abstraction seams | Loses `rich.console.Console.export_html` reuse; tool-renderer templates re-author |

## Recommendations (advisory only — Phase 5b W0 decides)

- **PRIMARY**: **textual + rich** — best extensibility ceiling, modern
  reactive model, leverages existing `rich` integration already used by
  the HTML export emitter (ADR-0083 / ADR-0085) and `_export_html`
  templates.
- **ALTERNATIVE**: **prompt-toolkit + rich** — closer to Pi's
  imperative model; choose if the Pi extension-author UX (a
  `(tui, theme, kb, done) => Component` factory that mutates state
  imperatively) cannot be cleanly mapped onto textual's reactive
  `Widget` lifecycle.
- **CONTINGENCY**: **textual alone** — drop rich for live rendering if
  the integration friction (two render layers competing for the
  terminal) outweighs the reuse benefit.

## CRITICAL invariant (binding regardless of selection)

**The `Component` Protocol MUST be library-agnostic.** Do NOT alias
`Component = textual.Widget` (or `prompt_toolkit.layout.Container`)
directly at the public extension API boundary — extensions would be
locked to the chosen library forever, and a future library swap would
be a breaking change for every extension author in the marketplace.

The library-agnostic shape (sketched, not binding):

    @runtime_checkable
    class Component(Protocol):
        def render(self) -> RenderResult: ...
        def handle_key(self, key: KeyEvent) -> bool: ...
        async def mount(self, ctx: TUIContext) -> None: ...
        async def unmount(self) -> None: ...

The selected library's widget primitive is wrapped (composition, not
inheritance) so a future library swap touches the adapter, not the
extension surface.

The selected library composition (prompt-toolkit + Rich) is wrapped by a
library-agnostic `Component` Protocol identical in spirit to Pi-tui's
`Component` interface (synchronous `render(width: int) -> list[str]` +
optional `handle_input(data: str) -> None` + `invalidate()`). Extensions
call into this Protocol; the Protocol implementation delegates to
prompt-toolkit / Rich. A future library swap (e.g., switching to a
Python pi-tui port if maintenance costs justify) touches only the
Protocol implementation, not extension authors.

## Open questions (resolved at Sprint 6h₉a unless noted)

1. **Extras dep model** — **Resolved at Sprint 6h₉a** — `pip install
   aelix[tui]` extra installs `prompt-toolkit` + `rich`. No split.
2. **Reactive semantics** — **Resolved at Sprint 6h₉a** — Pi
   `setState`-style imperative update maps to direct
   `ctx.ui.set_widget(...)` re-invocation; no implicit reactive
   watchers. Host invalidates the relevant Rich Live region.
3. **Windows support** — **Resolved at Sprint 6h₉a** — prompt-toolkit
   supports Windows PTY (verified via pyreadline3 / ConPTY); Rich
   supports Windows console. Verified parity.
4. **Kitty image protocol** — **Open — Sprint 6h₁₀ Phase 5c
   carry-forward** — prompt-toolkit + Rich do NOT natively support
   Kitty/iTerm2 graphics; Aelix will use `term-image` library OR direct
   ANSI escape emission for inline image support. Decision in Sprint
   6h₁₀c.
5. **Theme parity** — **Resolved at Sprint 6h₉a** — Pi theme names
   mapped to Rich Style + prompt-toolkit Style class via
   `aelix_widget_layer.theme` module (implementation Sprint 6h₁₀b).
6. **Snapshot testing** — **Resolved at Sprint 6h₉a** — Aelix will use
   `pyte`-driven terminal emulation snapshots (no built-in Textual
   equivalent). Implementation Sprint 6h₁₀d.
7. **Editor seam** — **Resolved at Sprint 6h₉a** — prompt-toolkit
   `Buffer` IS the editor seam. Pi `editor.ts` remote-control surface
   maps to direct `Buffer` API + `app.invalidate()`.
8. **`OverlayOptions` shape** — **Resolved at Sprint 6h₉a** — Pi
   9-anchor + responsive `visible` callback maps to prompt-toolkit
   `Float` with position + custom visibility filter (implementation
   Sprint 6h₁₀b).
9. **Theme live-update** — **Resolved at Sprint 6h₉a** — explicit
   `Application.invalidate()` after theme swap (prompt-toolkit
   standard pattern).
10. **Backpressure on event stream** — **Open — Sprint 6h₁₀a** — Rich
    Live update frequency vs token stream rate; implementation will
    throttle to ~30 FPS max (16-33ms intervals).

## Decision

**prompt-toolkit (input/editor) + Rich (output rendering) + Aelix self-built minimal widget layer.**

Selection finalized at Sprint 6h₉a after the Phase 5b research wave (4-agent
investigation + Pi direct source survey). This decision replaces the prior
PRIMARY recommendation (textual + rich) for the reasons documented in §"Why
the PRIMARY recommendation was reversed" below.

The selection covers Aelix's TUI surface only. Web UI (Phase 6) is a
separate stack decision documented in ADR-0097.

## Why the PRIMARY recommendation was reversed

The Sprint 6h₆ analysis (this ADR pre-amend) selected `textual + rich` as
PRIMARY because Textual's reactive widget model, snapshot testing, and
`textual-serve` Phase 6 web convergence story were strongest among the
candidates.

Three post-analysis findings reversed the selection:

1. **Pi-tui is NOT React/Ink** — direct investigation of
   `earendil-works/pi/packages/tui/` (Mario Zechner authored, deps:
   `get-east-asian-width` + `marked` only) confirmed `pi-tui` is a custom
   imperative TUI library with `Component.render(width: number) -> string[]`,
   differential rendering, CSI 2026 synchronized output, and CJK IME via
   `Focusable` + `CURSOR_MARKER` APC escape. It is purpose-built for an
   **inline scrolling + live bottom region** UX (channel-chat history flows
   into terminal scrollback; only the bottom region is live-rendered).
   Textual default is full-screen alternate-screen mode (vim/htop/k9s
   style) — fundamentally different UX. Pi/Claude Code/Codex CLI/aider/
   gemini-cli/gptme all use the inline pattern.

2. **Textual `inline=True` mode is uncharted territory** — added in
   Textual 0.55+ (~Feb 2024), but: limited examples, overlay/modal patterns
   diverge from full-screen mode, large-output behavior unverified,
   Textualize team's main pattern remains full-screen. Adopting Textual
   with inline mode = betting on uncommon path.

3. **`textual-serve` Phase 6 convergence is moot** — Aelix's Phase 6 Web UI
   is architected as a **separate repository (aelix-web) running as a
   self-hosting server daemon (Open WebUI pattern)** per ADR-0097. The Web
   UI must support charts (Plotly/ECharts), file previews, image galleries,
   marketplace UI — none of which textual-serve's terminal-shaped output
   satisfies. The 4-6 week saving textual-serve offered is real ONLY in a
   scenario where the web UI is acceptable as a terminal-shaped browser
   app, which Aelix's user vision rejects.

The selected stack — prompt-toolkit + Rich + Aelix widget layer — has
independent validation:

- **aider** (`Aider-AI/aider`) has run a Python coding-agent on
  prompt-toolkit + Rich for 10+ years with multi-line editor, vim/emacs
  bindings, IME support, slash commands, file completer. Direct
  architectural precedent.
- **IPython / ptpython** anchor prompt-toolkit stability (millions of
  users, 10+ years).
- **Pi's own TUI** is custom because Ink/React was inadequate; Aelix
  building a thin widget layer on prompt-toolkit + Rich is the Python
  analogue of Pi's choice (use language-native stable primitives, add
  minimal application-specific layer on top).

## Architecture of the selected stack

- **Input layer**: prompt-toolkit `PromptSession` for the inline multi-line
  editor. Buffer + Layout for autocomplete provider stacking. KeyBindings
  for vim/emacs/readline mode + extension shortcuts. Pi `Focusable` +
  CURSOR_MARKER pattern → prompt-toolkit native cursor positioning (CJK
  IME handled by prompt-toolkit).
- **Output rendering layer**: Rich Console for chat output (renders to
  terminal scrollback — main message stream). Rich Live for the bottom
  live region (footer + status + working indicator). Rich Renderable
  mapping for descriptor primitives (table → Rich Table, grid → Rich
  Columns, form → ad-hoc Rich layout, badge/metric → Rich Text with
  styling, etc.).
- **Widget layer (Aelix)**: thin façade implementing the
  `ExtensionUIContext` 25-method surface. Each method maps to a
  prompt-toolkit or Rich primitive operation. Library-agnostic
  `Component` Protocol (preserved from this ADR's CRITICAL invariant) so
  extensions don't lock to prompt-toolkit or Rich types directly.
- **Overlay layer**: prompt-toolkit Float windows for modal-style overlays
  (9 anchor positions per Pi-tui semantics). Rich Live temporary panel
  for non-modal status overlays.
- **Streaming layer**: Rich Live region updated incrementally per token.
  prompt-toolkit `app.invalidate()` triggers redraw when output state
  changes.
- **Theme layer**: Pi theme `theme.fg(name, text)` / `theme.bg(name, text)`
  mapped to Rich Style + prompt-toolkit Style class. Theme switch at
  runtime via `ctx.ui.set_theme(name)`.

**What we are NOT doing**:
- NOT porting `pi-tui` to Python directly (rejected option γ in research —
  5 sprint cost to maintain a self-built library long-term).
- NOT using Textual (rejected per §"Why the PRIMARY recommendation was
  reversed").
- NOT using Textual inline mode (uncharted).
- NOT using `blessed` (too low-level — pi-tui would have to be rebuilt on
  raw escape codes, same problem as direct pi-tui port).

## Consequences

- Phase 5a-i + 5a-ii ship the non-interactive CLI without taking a
  library bet that would compound the diff.
- A future contributor opening "interactive mode broken" finds this
  ADR via the stderr diagnostic and learns Phase 5b is the owner.
- The `Component`-Protocol-MUST-be-library-agnostic invariant prevents
  Phase 5b from accidentally painting the extension API into a corner.

## Consequences of the prompt-toolkit + Rich selection (added Sprint 6h₉a)

Positive:
- Inline scrolling + live bottom region UX matches Pi / Claude Code / Codex /
  aider / gemini-cli — minimum surprise for terminal users.
- prompt-toolkit + Rich are 10+ year mature Python libraries with millions of
  users (IPython, ptpython, pip, rich-cli output adoption).
- aider provides a direct architectural precedent of a Python coding-agent on
  this stack.
- No bet on uncharted Textual inline mode.
- Phase 6 Web UI is a separate stack (per ADR-0097), so no convergence
  advantage is lost.
- The library-agnostic `Component` Protocol (CRITICAL invariant) allows
  future library swap without breaking extension authors.

Negative:
- Aelix must build its own minimal widget layer (estimated 800-1200 LOC) for
  the ExtensionUIContext 25-method surface — Textual would have provided
  out-of-box widget primitives.
- PyPI widget ecosystem leveraged by Textual (textual-fspicker, textual-
  plotext, ...) is NOT directly reusable; Aelix must either reimplement
  equivalents on prompt-toolkit + Rich or live without them.
- Snapshot testing relies on third-party pyte (no first-party tooling).
- Streaming output requires manual throttling (no Textual reactive
  batching).

## References

- Pi `pi-tui` (custom TUI library, ~9,000 LOC at SHA `734e08e…`)
- Pi `ExtensionUIContext` 25 methods (note: original ADR text said 31; direct survey at SHA `734e08e` counted 25 methods — see ADR-0094 §"Pi reference") —
  `pi/packages/coding-agent/src/extensions/types.ts`
- ADR-0089 — Sprint 6h₆ Phase 5a-i + 5a-ii closure (companion ADR
  that raises NotImplementedError pointing here)
- ADR-0083 — Runtime callback Pi parity (carry-forward consumer for
  `with_session` / `setup` / `forkFrom` callbacks that interactive
  mode will exercise)
- ADR-0085 — Phase 4.16 visual fidelity + Phase 4 closure (rich-based
  HTML export, candidate reuse target for prompt-toolkit + Rich combo)
- ADR-0094 (Sprint 6h₉a) — Aelix Extension Architecture (4-tier model).
  T1 trusted in-process Python uses the prompt-toolkit + Rich widget layer.
- ADR-0095 (Sprint 6h₉a) — UI Descriptor Protocol. The Rich Renderable
  mapping rules consume this protocol's payload schemas.
- ADR-0097 (Sprint 6h₉a) — Multi-Frontend Architecture. Establishes the
  separate `aelix-web` repo + self-hosting server daemon that makes the
  textual-serve Phase 6 convergence argument moot.
- ADR-0098 (Sprint 6h₉a closure) — Sprint 6h₉a / Phase 5b-foundation Lock.
