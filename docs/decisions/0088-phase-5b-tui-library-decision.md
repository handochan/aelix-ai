# 0088. Phase 5b TUI Library Decision — Analysis Basis (Proposed / Deferred)

Status: Proposed (deferred to Phase 5b kickoff)
Date: 2026-05-22
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

## Open questions (to resolve at Phase 5b W0)

1. **Extras dep model** — single `pip install aelix[tui]` extra, or
   split `aelix[textual]` / `aelix[prompt-toolkit]` for the contingency?
2. **Reactive semantics** — does Pi `setState`-style imperative update
   map cleanly onto textual `reactive` watchers, or do we expose a
   shim `setState(fn)` that triggers `Widget.refresh()`?
3. **Windows support** — textual + prompt-toolkit both support Windows
   PTY; blessed does not. Confirm the Pi feature parity matrix.
4. **Kitty image protocol** — Pi renders inline images via the Kitty
   graphics protocol. textual has `textual.widgets.Static` image
   helpers; prompt-toolkit requires hand-rolling. Verify protocol
   parity at the chosen library.
5. **Theme parity** — Pi themes ship as TypeScript modules with
   `background` / `foreground` / `accent` / palette refs. Map to
   textual CSS variables vs prompt-toolkit `Style.from_dict` —
   serialization shape differs.
6. **Snapshot testing** — textual ships `pytest-textual-snapshot`;
   prompt-toolkit has no equivalent (snapshots typically use
   `pyte`-driven terminal emulation).
7. **Editor seam** — Pi `editor.ts` is a remote-control surface for
   the prompt buffer. prompt-toolkit's `Buffer` is the strongest
   match; textual's `TextArea` works but lacks the Pi command set.
8. **`OverlayOptions` shape** — Pi `ExtensionUIContext.openOverlay`
   accepts a placement union (`"center" | "top" | …`) and a focus-trap
   spec. Map onto textual `ModalScreen` vs prompt-toolkit `Float`.
9. **Theme live-update** — Pi swaps themes at runtime without a
   re-render. textual reactive watchers + CSS recompile handle this;
   prompt-toolkit needs an explicit `Application.invalidate()`.
10. **Backpressure on event stream** — the agent event stream feeds
    the TUI; both libraries are asyncio-native but their batch /
    coalesce semantics differ. Verify the JSON-mode-equivalent
    line-per-event invariant holds visually.

## Decision

**DEFERRED to Phase 5b kickoff.** This ADR is the captured analysis
basis; the actual library + extras-dep + `Component` Protocol shape
land at Phase 5b W0 with explicit user consent (interactive-mode UX is
a user-facing surface and warrants the consultation gate per
ADR-0085 §"Consultation").

Until Phase 5b lands, `aelix` without `--print` / `--mode` raises
:class:`NotImplementedError` with the stderr diagnostic pointing here.

## Consequences

- Phase 5a-i + 5a-ii ship the non-interactive CLI without taking a
  library bet that would compound the diff.
- A future contributor opening "interactive mode broken" finds this
  ADR via the stderr diagnostic and learns Phase 5b is the owner.
- The `Component`-Protocol-MUST-be-library-agnostic invariant prevents
  Phase 5b from accidentally painting the extension API into a corner.

## References

- Pi `pi-tui` (custom TUI library, ~9,000 LOC at SHA `734e08e…`)
- Pi `ExtensionUIContext` 31 methods —
  `pi/packages/coding-agent/src/extensions/types.ts`
- ADR-0089 — Sprint 6h₆ Phase 5a-i + 5a-ii closure (companion ADR
  that raises NotImplementedError pointing here)
- ADR-0083 — Runtime callback Pi parity (carry-forward consumer for
  `with_session` / `setup` / `forkFrom` callbacks that interactive
  mode will exercise)
- ADR-0085 — Phase 4.16 visual fidelity + Phase 4 closure (rich-based
  HTML export, candidate reuse target for textual + rich combo)
