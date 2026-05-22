# 0094. Aelix Extension Architecture — 4-Tier Model

Status: Accepted (Sprint 6h₉a / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Phase 5b foundation requires a binding extension architecture before the
TUI sprints (6h₁₀a-d) and Phase 6 Web sprints can compose on top. A
4-agent research wave (Pi-agent-dashboard / peer coding-agents / TUI
frameworks / editor-IDE extension models) plus a direct Pi `pi-tui`
source survey concluded:

- **Pi's extension model is in-process imperative** — extensions are
  `(api: ExtensionAPI) => void` factory functions that consume a 27-method
  `ExtensionUIContext` surface (28 members including the `readonly theme`
  property) directly. Pi sits at the Neovim/Emacs end of the
  isolation spectrum: trust-the-user, no manifest, no permission tokens.
- **Aelix has two equal first-class audiences** (D1: TUI + Web 양쪽 1차
  시민) — a single-tier in-process model cannot serve the Web audience.
  Web cannot share the TUI's Python process (different language,
  different deployment surface) and requires a wire-format extension
  contract.
- **Pi-agent-dashboard retrofitted descriptors** as a separate repo and
  paid the cost in maintenance + scope drift (issue #32 maintainer
  admission). Aelix can avoid the retrofit by designing the descriptor
  protocol forward.
- **Peer coding-agents (Claude Code, gemini-cli) universally adopt MCP +
  subprocess hooks** for portable extensions. Formalizing this as a
  dedicated tier lets Aelix extensions participate in the broader
  ecosystem without dual-authoring.

The binding insight: Pi parity at the API surface (T1) PLUS an Aelix-
additive cross-surface wire (T2) PLUS a Phase 6 Web rich tier (T3) PLUS
a peer-compatible universal tier (T4) covers the four distinct
extension audiences without forcing any one of them through the wrong
ABI.

## Tier overview table

| T | Name | Purpose | Surface | Discovery | Trust | Process | Renders to |
|---|---|---|---|---|---|---|---|
| T1 | Trusted in-process Python | Pi-parity full extension API | `def extension(api: AelixAPI) -> None` factory function | Folder scan (`~/.aelix/extensions/`, `.aelix/extensions/`) + `[project.entry-points."aelix.extension"]` | "trust the user" (Pi pattern); future capability gating via manifest | In-process (no isolation) | TUI: direct Aelix widget. Web: trusted React slot claim (Phase 6) |
| T2 | Cross-surface descriptors | Code-free UI contributions | JSON descriptor emit via `ctx.ui.emit_descriptor(kind, namespace, id, payload)` | T1 plugin emits at runtime via `ui:list-modules` synchronous probe OR via `[contributes.descriptors]` declaration in manifest | Same as T1 host (descriptors are static data, no executable) | In-process emit; host renders | **TUI Rich Renderable + Web React/Svelte primitive — same wire format** |
| T3 | Rich React/Svelte components | Rich interactive Web UI | TS/JS module exporting React/Svelte/Solid component(s), claimed via Web slot manifest entry | Web manifest claim + bundle path (Phase 6 decision) | Phase 6 decision (trusted-only / iframe sandbox / WASM) | Phase 6 decision (in-process / iframe) | Web only (TUI cannot render arbitrary React) |
| T4 | MCP + subprocess hooks | Universal peer-compatible extension | (a) MCP server (stdio/HTTP/SSE), (b) hook script (stdin JSON / stdout JSON / exit code) | `[contributes.mcp_servers]` in manifest + `[contributes.hooks]` | Subprocess process boundary | Subprocess (process isolation by default) | Both — emits descriptors or tool results consumed by T1/T2 render path |

## Cross-tier composition

A single Aelix plugin CAN opt into multiple tiers simultaneously. Worked
example — an analytics extension might provide all four:

- **T1**: Python tool registration (`/analyze` command) + an in-process
  result-preview widget bound to the TUI Rich `Live` region.
- **T2**: `tool-renderer-desc` descriptor for the `/analyze` result wire
  format so the same view renders unchanged on Web (Phase 6) and on TUI
  via the host's Rich Renderable mapping.
- **T3**: A Plotly React chart renderer (Phase 6 — Web only) that
  augments the T2 descriptor with interactive zoom/pan / drill-down,
  richer than the T2 descriptor table fallback.
- **T4**: An MCP server exposing the same analytics function over
  stdio, so Claude Code / gemini-cli / other peer agents can invoke it
  with identical semantics.

The manifest's `[capabilities]` block declares which tiers the plugin
participates in (§3.4); the loader inspects the block to wire the
appropriate adapters.

## Tier interaction rules

- T2 descriptors are **the canonical cross-surface wire**. A T1 widget
  that wants to render on Web MUST also provide a T2 descriptor
  fallback; otherwise the plugin only works in TUI.
- T3 React components are **Web-only and OPTIONAL**. They augment T2
  descriptors with richer rendering on Web; the T2 descriptor remains
  the fallback for both TUI and stripped-down Web modes (e.g., when a
  user disables Tier 3 sandbox loading).
- T4 MCP servers and hooks NEVER render UI directly. Their outputs flow
  into T1 (tool registration intercepts the MCP tool call result) or T2
  (tool-renderer-desc payload references the MCP tool by name).
- Plugins MUST declare which tiers they participate in via the
  manifest `[capabilities]` block (`ui_tui_trusted`, `ui_descriptor`,
  `ui_web_trusted`, `mcp_serve`). See ADR-0096 §3.3.3.

## Trust model and process boundary

Phase 5b lock:

- **T1 trusted** = "trust the user" (Pi parity). No isolation in Phase
  5b. The manifest `[capabilities]` block declares intent
  (declaration-only, no enforcement v1). A workspace trust dialog will
  gate loading of project-scoped extensions in Phase 5c per the standard
  VS Code pattern.
- **T2 descriptors** = code-free. The renderer is host-owned; no
  untrusted code execution path exists. Descriptors are static JSON;
  they can be authored by hand without a Python toolchain.
- **T3 React** = Phase 6 decision deferred (ADR-0097 §"Phase 6 deferred
  decisions"). The default conservative position at Phase 6 start is
  trusted-only; iframe sandbox and WASM are considered for follow-on.
- **T4 MCP/hooks** = subprocess process boundary always. MCP runs as a
  per-server process with stdio/HTTP/SSE transport; hook scripts run
  per-invocation with stdin/stdout JSON contract and timeout enforcement.

## Discovery and loading order

**Discovery sources** (priority order):

1. `.aelix/extensions/` in the repo root (workspace-scoped — workspace
   trust dialog gates loading, Phase 5c).
2. `~/.aelix/extensions/` in the user home (user-scoped, always-loaded
   subject to capability check).
3. Python entry-points group `aelix.extension` (PyPI-distributed
   plugins, Harlequin pattern — Agent C report).
4. Manifest-declared external directories (Phase 5c+ — for managed
   plugin stores).

**Load order**:

- Deterministic topological sort by `dependsOn` (manifest field, Phase
  5b carry-forward sprint).
- Cycle = soft-fail: cycle members get `loaded: false` per Pi-dashboard
  `loader.ts:331-336` precedent.
- Per-plugin `try`/`except` boundary (Harlequin pattern) — a failure to
  load one plugin must not prevent any other plugin from loading.

## Pi reference and Aelix-additive divergences

Pi cited at SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`:

- `packages/coding-agent/src/core/extensions/types.ts:124-275` — `ExtensionUIContext` 27-method surface (28 members including `readonly theme: Theme`).
- `packages/coding-agent/src/core/extensions/types.ts:298+` — `ExtensionContext` class (the broader plugin context that T1 receives; class body continues beyond a fixed end line).
- `packages/coding-agent/src/core/extensions/types.ts:396` — `ToolRenderContext` (per-call render context).
- `packages/coding-agent/src/core/extensions/types.ts:426` — `ToolDefinition` (tool registration shape).
- `packages/coding-agent/docs/extensions.md` — extension lifecycle diagram and the `(api: ExtensionAPI) => void` factory pattern.
- `packages/coding-agent/docs/extensions.md` §"Auto-discovery" — `~/.pi/agent/extensions/` and `.pi/extensions/` paths (the literal sources we translated to `~/.aelix/extensions/` and `.aelix/extensions/`).

**Aelix-additive divergences**:

- T2 descriptor tier — Pi has no descriptor tier in core. Pi-dashboard
  retrofitted descriptors as a separate repo. Aelix forward-designs.
- T3 rich-Web tier — Pi has no Web rich tier (Pi-dashboard is a
  separate repo; Pi core is TUI-only). Aelix introduces T3 for Phase 6.
- Manifest TOML file — Pi has no manifest (auto-discovers `.ts` files).
  Aelix introduces `aelix-plugin.toml` (ADR-0096).
- `AELIX_API_LEVEL` — Pi has no formal ABI version (semver only). Aelix
  follows Neovim's API_LEVEL pattern.
- `[capabilities]` declaration — Pi has none ("trust the user"). Aelix
  declares for Phase 6 enforcement; Phase 5b is declaration-only.

**Pi-faithful elements**:

- T1 in-process trust model — identical to Pi.
- T1 factory function shape — `def extension(api: AelixAPI) -> None`
  mirrors Pi's `default function (api: ExtensionAPI) => void`.
- T1 `ctx.ui.*` 27-method surface (28 members including `theme` readonly
  property) — Aelix Phase 5c (Sprint 6h₁₀) implementation will mirror
  Pi's `ExtensionUIContext` method-for-method.
- T4 MCP + hooks — Pi supports both via its extension surface; Aelix
  elevates them to a formal tier matching the Claude Code / gemini-cli
  universal pattern.

## Consequences

**Positive**:

- Clear, separate contract for each extension audience. Plugin authors
  pick the tier that fits their use case without overpaying complexity.
- T2 forward-design avoids the Pi-dashboard retrofit cost (the
  cross-surface wire is part of the original architecture, not a
  retrofit after Web demand emerges).
- T4 ensures Aelix extensions participate in the broader coding-agent
  ecosystem (Claude Code / gemini-cli / aider) via MCP, without
  dual-authoring against multiple proprietary plugin SDKs.
- Pi parity is preserved at T1: a Pi user familiar with the Pi
  extension factory model can port to Aelix with minimal cognitive load.

**Negative**:

- 4 tiers = a higher conceptual surface area than Pi's 1-tier model.
  Plugin authors need to understand which tier fits their use case
  (mitigated by documentation + worked examples in the contracts
  README).
- T2 descriptor wire format must be designed forward without all Phase
  6 Web use cases known. Versioning policy (ADR-0095 §10) allows minor
  additions; major redesign would require a coordinated cross-repo
  semver bump.

## References

- ADR-0088 (amended Sprint 6h₉a) — TUI stack selection (prompt-toolkit + Rich + Aelix widget layer).
- ADR-0095 (Sprint 6h₉a) — UI Descriptor Protocol (Tier 2 cross-surface wire format).
- ADR-0096 (Sprint 6h₉a) — Aelix Plugin Manifest v1 (`aelix-plugin.toml`).
- ADR-0097 (Sprint 6h₉a) — Multi-Frontend Architecture (RPC Gateway + Separate Web Repo + Self-Hosting Server Model).
- ADR-0098 (Sprint 6h₉a closure) — Sprint 6h₉a / Phase 5b-foundation Lock.
- Pi `packages/coding-agent/src/core/extensions/types.ts` (SHA `734e08e`) — `ExtensionUIContext` (27 methods, lines 124-275) / `ExtensionContext` (lines 298+) / `ToolDefinition` (line 426) / `ToolRenderContext` (line 396) reference.
- Pi `packages/coding-agent/docs/extensions.md` (SHA `734e08e`) — lifecycle, auto-discovery.
- Pi-dashboard `packages/shared/src/dashboard-plugin/slot-types.ts` — 21-slot reference (Aelix v1 takes 8-slot mix; 6 are Pi-dashboard subset, 2 are Aelix-additive — see ADR-0095).
- Pi-dashboard `packages/dashboard-plugin-runtime/src/slot-registry.ts` — slot registry / loader precedent (cycle soft-fail).
- Pi-dashboard `packages/dashboard-plugin-runtime/src/server/loader.ts` — server-side loader / `loaded: false` cycle soft-fail precedent (`loader.ts:331-336`).
- Pi-dashboard `docs/architecture.md` — descriptor protocol and `ui:list-modules` probe pattern (ADR-0095 cites lines 180-290 + 221-227).
- Pi-dashboard issue #32 — maintainer admission of retrofit cost.
- Agent A research report — Pi-agent-dashboard 21-slot taxonomy and IntentNode wire format.
- Agent B research report — peer coding-agent extension models (Claude Code MCP+hooks, opencode declarative TUI, aider Markdown skills).
- Agent C research report — TUI framework comparison (Textual / Rich / prompt-toolkit / Ink / Bubbletea / Ratatui).
- Agent D research report — editor/IDE extension architecture (VS Code declarative, Neovim API_LEVEL, Zed SPDX whitelist, JetBrains).
