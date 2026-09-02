# Sprint 6h₉a — Phase 5b-foundation §0 Contract Layer + ADR Lock

Status: Binding (W1 spec; do not modify after W1 closure)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — Sprint metadata

| Field | Value |
|---|---|
| Sprint id | `6h₉a` |
| Phase | 5b-foundation, sprint 1 of ~6 |
| Workflow | ADR-0032 W0-W6 (research → spec → executor → verify → review → critic → commits) |
| Scope class | Documentation + contracts scaffolding (no business logic; no behavior change) |
| Spec author | Main agent (architect agent is READ-ONLY in current OMC profile; main agent retains exhaustive W0 context from 4-agent research + Pi direct investigation) |
| Spec convention | One-write binding — executor MUST follow verbatim; deviations require explicit user gate |
| Owning ADR closure | ADR-0098 (NEW — Sprint 6h₉a / W6 shipped marker; see §3.8) |

This sprint is the **first sprint of Phase 5b-foundation**. It locks the architectural decisions that the subsequent Phase 5b-foundation sprints (6h₉b extension loader, 6h₉c ExtensionAPI Python surface, 6h₉d descriptor renderer, 6h₉e MCP+hooks, 6h₉f aelix-server) and Phase 5c-tui sprints (6h₁₀a-d) and Phase 6-web sprints (6i_*) all consume.

---

## §1 — Background

### §1.1 — How this sprint came to exist

Between Sprint 6h₇c (ADR-0093 — A 단계 closure, P-380 reload primitives) and this sprint, an extensive **5-area research investigation** was performed:

1. **Pi TUI direct investigation** (main agent) — fetched `pi-tui` source, types.ts (1567 LOC), examples (modal-editor, message-renderer, custom-footer), pi-tui README, doc files (extensions.md, tui.md). Key discovery: **`pi-tui` is a custom imperative TUI library authored by Mario Zechner**, NOT React/Ink as previously assumed. `Component` interface = `render(width: number) -> string[]` + optional `handleInput`/`invalidate`. ExtensionUIContext = 25-method surface (set_status / set_widget / set_footer / set_header / custom overlay / set_editor_component / theme API / autocomplete provider stacking / etc.).
2. **Agent A — Pi-agent-dashboard Web UI** (`BlackBeltTechnology/pi-agent-dashboard`). Key findings: 22-slot frozen taxonomy with 3-tier payload (react-only / descriptor-only / react-or-descriptor); `ui:list-modules` synchronous-probe pattern; build-time static-import generation via Vite plugin (NOT runtime federation); IntentNode wire format with `ActionDescriptor` (no function refs cross wire). Maintainer Robert Csakany openly admits POC status (issue #32). **Lesson**: Aelix should take the descriptor-protocol forward-design that Pi-dashboard retrofitted.
3. **Agent B — Peer coding-agent TUI extension models** (Claude Code, Codex, aider, goose, opencode, crush, continue, gptme, gemini-cli). Key findings: **opencode is the only peer with declarative TUI extension API** (opentui/Solid). MCP is universal. Markdown+YAML frontmatter for skills/commands is universal. Hierarchical scopes universal. Subprocess-isolated hooks (Claude Code pattern) is robust default.
4. **Agent C — TUI framework comparison** (Textual / Rich / prompt-toolkit / Ink / Bubbletea / Ratatui). Key findings: Textual's `render_line(y)` ≈ Pi-tui `render(width)` (retained-mode + line API); textual-serve = Phase 6 web no-rewrite; Harlequin entry-points pattern is Python-idiomatic plugin distribution.
5. **Agent D — Editor/IDE extension architecture** (VS Code / Neovim / Helix / Zed / Emacs / JetBrains / Sublime). Key findings: Pi extension model = Neovim/Emacs in-process imperative (no manifest, no perms, no isolation). VS Code declarative is gold standard for Web UI extension. **Manifest schema essentials**: api_level separate from host version (Neovim), capabilities/permissions (Zed/VS Code workspace trust), activation events (VS Code, no `*`).

### §1.2 — User clarifications that finalized the architecture

Two user clarifications during plan synthesis significantly altered the framing:

1. **TUI와 Web 둘 다 1차 시민** (Claude Code CLI/Desktop 관계처럼 audience만 다름). 어느 한쪽 격하 X. → Aelix is NOT a single-surface product; both TUI and Web are equal first-class.
2. **Web UI는 별도 레포 + 셀프호스팅 서버 daemon (Open WebUI 패턴)**, NOT desktop app (Tauri/Electron) NOT textual-serve. 분석 agent가 차트 렌더링 가능한 수준의 rich UI. Aelix 생태계 진입점.
3. **TUI = prompt-toolkit + Rich + Aelix widget layer** (사용자 선택 α path). Textual 기본 full-screen이 Pi/Claude Code inline scrolling UX와 미스매치. aider 검증 패턴.

### §1.3 — Locked decisions (D1-D10)

The following decisions are LOCKED for Phase 5b/5c/6. Phase 6 진입 시점에 재검토 가능한 항목은 §1.4에서 별도 명시.

| # | Decision |
|---|---|
| **D1** | TUI와 Web 양쪽 1차 시민 (Claude Code CLI/Desktop 관계). 어느 한쪽 격하 X. |
| **D2** | TUI framework = **prompt-toolkit + Rich + Aelix 자체 minimal widget layer**. (이전 Textual 잠정 결정 폐기) |
| **D3** | Web UI는 **별도 레포 `aelix-web`** (현 레포 Python only 유지) |
| **D4** | Web UI는 **셀프호스팅 서버 모델** (Open WebUI 패턴: FastAPI daemon + Docker/Helm) |
| **D5** | **4-tier extension model**: T1 trusted in-process Python / T2 cross-surface descriptors / T3 rich React-Svelte components (Web only) / T4 MCP+subprocess hooks (universal) |
| **D6** | Pi parity = `ctx.ui.*` 25-method surface + tool renderer co-location 1:1 (Pi types.ts:1567) |
| **D7** | Manifest = `aelix-plugin.toml`. API_LEVEL Aelix version과 분리 (Neovim pattern). SPDX license whitelist (Zed pattern). Activation events 명시 (VS Code pattern, `*` 금지). |
| **D8** | Descriptor protocol **forward-design** — TUI/Web 양쪽 동일 envelope으로 렌더 (Pi가 retrofit 비용 지불, Aelix는 forward로 회피) |
| **D9** | **aelix-server** (FastAPI HTTP+WS gateway) = Phase 5b-foundation 안에 신설. Sprint 6h₉a에서는 contract만; 실 구현은 Sprint 6h₉f. Phase 5b는 single-user dev mode (no auth/no DB); Phase 6는 multi-tenant. |
| **D10** | Sequencing: 5b foundation (~6 sprint) → 5c TUI (~4 sprint) → 6 Web (~10+ sprint 별도 레포) |

### §1.4 — Decisions intentionally deferred (Phase 6 entry)

- Web frontend stack 선택 (React + Vite vs SvelteKit vs Next.js)
- Tier 3 sandbox 수준 (trusted-only vs iframe vs WASM)
- Auth 방식 (OAuth, SAML, email-password, …)
- DB 선택 (PostgreSQL vs SQLite vs hybrid)
- Marketplace 운영 방안 (자체 registry vs git-based vs hybrid)
- Desktop wrapper 여부 (Tauri/Electron)

These are explicitly NOT decided in Sprint 6h₉a. ADR-0097 records them as deferred with structural placeholder.

---

## §2 — Scope

Sprint 6h₉a delivers **eight deliverables** in six atomic commits (§4):

| # | Deliverable | Type | Touches |
|---|---|---|---|
| 1 | ADR-0088 amend (Textual → prompt-toolkit + Rich + Aelix widget layer, Status: Proposed → Accepted) | Docs | `docs/decisions/0088-*.md` |
| 2 | ADR-0094 NEW — Aelix Extension Architecture (4-tier model) | Docs | `docs/decisions/0094-aelix-extension-architecture-4-tier.md` |
| 3 | ADR-0095 NEW — UI Descriptor Protocol (Tier 2 cross-surface wire format + 8-slot taxonomy v1) | Docs | `docs/decisions/0095-ui-descriptor-protocol.md` |
| 4 | ADR-0096 NEW — Aelix Plugin Manifest v1 (`aelix-plugin.toml`) | Docs | `docs/decisions/0096-manifest-v1-schema.md` |
| 5 | ADR-0097 NEW — Multi-Frontend Architecture (RPC Gateway + Separate Web Repo + Self-Hosting Server Model) | Docs | `docs/decisions/0097-multi-frontend-architecture.md` |
| 6 | Contracts Python package (Pydantic v2 models for manifest, descriptor, slots, primitives, api_level) | Code | `packages/aelix-agent-core/src/aelix_agent_core/contracts/` (NEW), `packages/aelix-agent-core/pyproject.toml` (+ pydantic dep) |
| 7 | Schema generation script + initial generated JSON Schemas + contracts docs | Code+Docs | `scripts/generate_contracts_schemas.py` (NEW), `docs/contracts/*.schema.json` (NEW), `docs/contracts/README.md` (NEW) |
| 8 | Contract validation tests | Tests | `tests/contracts/test_contracts_schema.py` (NEW), `tests/contracts/__init__.py` (NEW) |

Plus the Sprint 6h₉a closure ADR (ADR-0098) created in the final commit per W6 protocol.

---

## §3 — Per-deliverable specifications

### §3.1 — ADR-0094 NEW: Aelix Extension Architecture (4-Tier Model)

**File**: `docs/decisions/0094-aelix-extension-architecture-4-tier.md`

**Mandatory front-matter**:

```
# 0094. Aelix Extension Architecture — 4-Tier Model

Status: Accepted (Sprint 6h₉a / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**
```

**Required sections (in order)**:

1. `## Context` — Why a 4-tier model. Reference 4-agent research conclusions. State the binding insight: Pi extension model = in-process imperative (Neovim/Emacs end of isolation spectrum), but Aelix must support both TUI and Web first-class, so a single-tier in-process model is insufficient.

2. `## Tier overview table` — One row per tier, columns: Tier #, Name, Purpose, Surface, Discovery, Trust model, Process boundary, Render path (TUI / Web / both), Pi reference, Aelix-additive divergence.

   The 4 tiers (lock these definitions):

   | T | Name | Purpose | Surface | Discovery | Trust | Process | Renders to |
   |---|---|---|---|---|---|---|---|
   | T1 | Trusted in-process Python | Pi-parity full extension API | `def extension(api: AelixAPI) -> None` factory function | Folder scan (`~/.aelix/extensions/`, `.aelix/extensions/`) + `[project.entry-points."aelix.extension"]` | "trust the user" (Pi pattern); future capability gating via manifest | In-process (no isolation) | TUI: 직접 Aelix widget. Web: trusted React slot claim (Phase 6) |
   | T2 | Cross-surface descriptors | Code-free UI contributions | JSON descriptor emit via `ctx.ui.emit_descriptor(kind, namespace, id, payload)` | T1 plugin emits at runtime via `ui:list-modules` synchronous probe OR via `[contributes.descriptors]` declaration in manifest | Same as T1 host (descriptors are static data, no executable) | In-process emit; host renders | **TUI Rich Renderable + Web React/Svelte primitive — same wire format** |
   | T3 | Rich React/Svelte components | Rich interactive Web UI | TS/JS module exporting React/Svelte/Solid component(s), claimed via Web slot manifest entry | Web manifest claim + bundle path (Phase 6 결정) | Phase 6 결정 (trusted-only / iframe sandbox / WASM) | Phase 6 결정 (in-process / iframe) | Web only (TUI는 cannot render arbitrary React) |
   | T4 | MCP + subprocess hooks | Universal peer-compatible extension | (a) MCP server (stdio/HTTP/SSE), (b) hook script (stdin JSON / stdout JSON / exit code) | `[contributes.mcp_servers]` in manifest + `[contributes.hooks]` | Subprocess process boundary | Subprocess (process isolation by default) | Both — emits descriptors or tool results consumed by T1/T2 render path |

3. `## Cross-tier composition` — Explain that a single Aelix plugin CAN opt into multiple tiers simultaneously. Worked example: an analytics extension provides (a) T1 Python tool registration + Textual widget for `/analyze` command result preview in TUI, (b) T2 descriptor for `tool-renderer-desc` so the result wire-format works on both TUI and Web, (c) T3 React Plotly chart renderer (Phase 6 — Web only, richer than T2 descriptor table), (d) T4 MCP server exposing the same analytics function for Claude Code / other agents.

4. `## Tier interaction rules` — Specify:
   - T2 descriptors are **the canonical cross-surface wire**. T1 widgets that want to render on Web MUST also provide a T2 descriptor fallback (otherwise the plugin only works in TUI).
   - T3 React components are **Web-only** and OPTIONAL. They augment T2 descriptors with richer rendering on Web; the T2 descriptor remains the fallback for both TUI and stripped-down Web modes.
   - T4 MCP/hooks NEVER render UI directly; their outputs flow into T1 or T2.
   - Plugins MUST declare which tiers they participate in via manifest `capabilities` block (§3.4).

5. `## Trust model and process boundary` — Lock the Phase 5b stance:
   - T1 trusted = "trust the user" (Pi parity). No isolation in Phase 5b. Manifest `capabilities` block declares intent (declaration-only, no enforcement v1). Workspace trust dialog gate before loading project-scoped extensions (Phase 5c).
   - T2 descriptors = code-free; renderer is host-owned; no untrusted code execution.
   - T3 = Phase 6 decision deferred (note ADR-0097 §"Phase 6 deferred"). Default conservative position: trusted-only at Phase 6 start; iframe/WASM later.
   - T4 = subprocess boundary always (MCP per-server process; hook per-invocation process).

6. `## Discovery and loading order` —
   - Discovery sources (priority order): repo `.aelix/extensions/`, user `~/.aelix/extensions/`, Python entry-points (`aelix.extension`), manifest-declared external dirs.
   - Load order: deterministic topological sort by `dependsOn` (manifest field, Phase 5b 추후 sprint). Cycle = soft-fail (cycle members get `loaded: false` per Pi-dashboard loader.ts:331-336 precedent).
   - Per-plugin try/except boundary (Harlequin pattern — Agent C report).

7. `## Pi reference and Aelix-additive divergences` — Cite Pi types.ts ExtensionAPI factory shape (`default function (pi: ExtensionAPI) => void`), Pi extensions.md auto-discovery paths (`~/.pi/agent/extensions/`, `.pi/extensions/`). Document Aelix divergences:
   - Aelix-additive: T2 descriptor tier and T3 rich-Web tier (Pi has neither in core; Pi-dashboard has descriptor but retrofitted as separate repo).
   - Aelix-additive: manifest TOML file (Pi has no manifest; Pi auto-discovers TS files).
   - Aelix-additive: API_LEVEL (Pi has no formal ABI version).
   - Aelix-additive: capabilities declaration (Pi has none; "trust the user").
   - Pi-faithful: T1 in-process trust model, factory function shape, `ctx.ui.*` surface (T1 implementation in subsequent sprints).
   - Pi-faithful: T4 MCP+hooks (Pi has both via extensions; Aelix elevates to formal tier matching Claude Code/gemini-cli universal pattern).

8. `## Consequences` — List positive and negative consequences. Positive: clear contract for each tier, plugins can opt into appropriate tier without overpaying complexity, T2 forward-design avoids Pi-dashboard's retrofit cost, T4 ensures peer-compatible (Claude Code/gemini-cli) ecosystem participation. Negative: 4 tiers = higher conceptual surface area than Pi's 1-tier model, plugin authors need to understand which tier fits their use case, T2 descriptor wire format must be designed forward without all Phase 6 Web use cases known.

9. `## References` — Cross-reference ADRs (0088 prompt-toolkit+Rich, 0095 descriptor protocol, 0096 manifest, 0097 multi-frontend). Cite Pi source files. Cite Pi-dashboard source files (slot-types.ts, slot-registry.ts, architecture.md). Cite Agent A/B/C/D research reports (Pi-dashboard / peer-agents / TUI-frameworks / editor-IDE).

---

### §3.2 — ADR-0095 NEW: UI Descriptor Protocol

**File**: `docs/decisions/0095-ui-descriptor-protocol.md`

**Mandatory front-matter** (same as ADR-0094 with title "0095. UI Descriptor Protocol (Tier 2 Cross-Surface Wire Format)").

**Required sections**:

1. `## Context` — Why Tier 2 needs a protocol. The descriptor protocol is the canonical cross-surface wire that makes Aelix's TUI and Web extension contributions interchangeable. Forward-design the wire so Phase 5b TUI extensions render unchanged on Phase 6 Web.

2. `## Descriptor envelope` — Lock the wire format:

   ```python
   class DescriptorEnvelope(BaseModel):
       kind: DescriptorKind                # see §3.2.4 (8-slot taxonomy v1)
       namespace: str                      # `^[a-z0-9][a-z0-9-]{0,63}$` (Pi-dashboard regex)
       id: str                             # unique within (kind, namespace)
       payload: DescriptorPayload          # discriminated by `kind`
       removed: bool = False               # True = host should remove the descriptor with matching (kind, namespace, id)
   ```

3. `## 8-slot taxonomy v1` — Lock these 8 slots (Aelix-additive subset of Pi-dashboard 22):

   | kind | Render site | Multiplicity | Payload primary type | TUI host | Web host (Phase 6) |
   |---|---|---|---|---|---|
   | `footer-segment` | Bottom status bar segment, right of git/model | many | text + optional icon name + optional tooltip | Rich Panel.right_segments | React StatusBar slot |
   | `status-item` | Extension status row above footer-segment | many | text + level (info/warn/error) | Rich Status component | React Status slot |
   | `tool-renderer-desc` | Inline tool call/result rendering | one per `(tool_name)` | tool match clause + view kind (table/grid/form/text) + fields | Rich Table/Tree/Panel per view kind | React ToolRenderer slot |
   | `command-route` | Slash command palette entry | one per `(command)` | command id + description + optional keybind | prompt-toolkit autocomplete | React CommandPalette slot |
   | `breadcrumb` | Top-of-content breadcrumb | many | label + optional href + optional icon | Rich Panel.top | React BreadcrumbBar slot |
   | `toast` | Transient notification | many | text + level + optional auto-dismiss ms | prompt-toolkit floating window | React Toast slot |
   | `management-modal` | Full-screen modal triggered by command | one per `(command)` | view kind (table/grid/form) + title + fields/columns + actions | prompt-toolkit full-screen overlay | React Modal slot |
   | `agent-metric` | Sidebar metric display | many | label + value + optional delta + level | Rich Status component | React MetricCard slot |

4. `## UI primitives` — Lock the 8 primitives that descriptor payloads reference:

   | Primitive | Purpose | Pydantic schema |
   |---|---|---|
   | `text` | Plain string with optional style | `{ text: str, style: Literal["default","muted","accent","success","warning","error"] = "default" }` |
   | `badge` | Inline label/value pair | `{ label: str, value: str, level: Literal["info","success","warning","error"] = "info" }` |
   | `metric` | Numeric metric display | `{ label: str, value: str \| float \| int, delta: Optional[str], level: Literal["info","success","warning","error"] = "info" }` |
   | `table` | Tabular data | `{ columns: list[ColumnSpec], rows: list[dict[str, Any]], actions: list[ActionDescriptor] = [] }` |
   | `grid` | Card grid | `{ items: list[GridItem], item_actions: list[ActionDescriptor] = [] }` |
   | `form` | Input form | `{ fields: list[FieldSpec], submit_action: ActionDescriptor, cancel_action: Optional[ActionDescriptor] }` |
   | `gate` | Conditional access gate (e.g., feature flag) | `{ flag: str, when: dict[str, Any], on_blocked_action: Optional[ActionDescriptor] }` |
   | `action` | (NOT a render primitive — see ActionDescriptor) | — |

   `ColumnSpec`: `{ id: str, label: str, kind: Literal["text","number","boolean","datetime","badge","code"] = "text", sortable: bool = False }`
   `FieldSpec`: `{ id: str, label: str, kind: Literal["text","number","boolean","select","textarea","code","datetime"] = "text", required: bool = False, values: Optional[list[str]] = None }`
   `GridItem`: `{ id: str, title: str, subtitle: Optional[str] = None, badge: Optional[BadgePrimitive] = None }`

5. `## ActionDescriptor` — Lock the action wire format:

   ```python
   class ActionDescriptor(BaseModel):
       plugin_id: str          # The plugin that registered the action (host dispatches back here)
       action: str             # action name within plugin (plugin's own routing key)
       payload: dict[str, Any] = Field(default_factory=dict)
       confirm: Optional[str] = None   # if set, host shows confirm dialog with this message before dispatch
   ```

   **CRITICAL**: function references NEVER cross the wire. The action is a string key the plugin matches via reverse channel (`plugin_action` event from frontend → host → T1 plugin's registered action handler).

6. `## ui:list-modules synchronous-probe pattern` — Lock the contribution discovery:
   - On session start (and at any T1 `ctx.ui.invalidate_descriptors()` call), the host emits a synchronous probe.
   - T1 extensions listen via `pi.events.on("ui:list-modules", (probe) => { probe.modules.push(...descriptor...) })` (Pi-dashboard pattern; Aelix Python: `api.on("ui:list-modules", lambda probe: probe.modules.append(...))`)
   - All descriptor contributions are collected synchronously during the emit.
   - Host partitions by kind, dispatches to slot renderers.
   - Cite Pi-dashboard architecture.md lines 180-290 explicitly.

7. `## Wire format guarantees` —
   - JSON-serializable end-to-end (Pydantic `model_dump(mode="json")`)
   - Function references NEVER cross wire — use ActionDescriptor (reverse channel)
   - `removed: true` is the only mechanism for removal (no implicit removal)
   - Idempotent: re-emitting the same `(kind, namespace, id)` replaces the prior descriptor
   - Forward-compatible: unknown `kind` values logged + dropped (not error)

8. `## TUI rendering rules` — For each of the 8 kinds, specify the Rich Renderable mapping rule. Defer detailed implementation to Phase 5c (Sprint 6h₁₀c). Sprint 6h₉a only locks the schema; the renderer implementation is later.

9. `## Web rendering rules (Phase 6 deferred)` — For each of the 8 kinds, name the React/Svelte slot identifier (e.g., `footer-segment` → `slot:footer-segment` in Phase 6 manifest claim). Full implementation Phase 6.

10. `## Versioning policy` — Lock these rules:
    - Adding a new `kind` to the taxonomy = minor (non-breaking).
    - Renaming or removing a `kind` = major (breaking).
    - Adding optional fields to a payload = minor (non-breaking).
    - Renaming/removing required fields of a payload = major (breaking).
    - API_LEVEL bumps follow the host (ADR-0096); plugins declaring `api.min_level` MUST be honored.
    - Schema drift detection: `scripts/generate_contracts_schemas.py` re-generates; CI MUST exit non-zero on drift (§3.7).

11. `## Pi-dashboard divergences` — Aelix 8-slot is subset of Pi-dashboard 22-slot. Aelix Phase 5b excludes React-only Pi-dashboard slots (sidebar-folder-section, anchored-popover, session-card-action-bar, etc.) since they require Phase 6 Web. Aelix Phase 6 can extend the taxonomy at minor-version bumps.

12. `## References` — ADR-0094 (4-tier), ADR-0096 (manifest references this protocol), ADR-0097 (multi-frontend uses this as the wire), Pi-dashboard slot-types.ts (line 1-300), Pi-dashboard architecture.md (lines 180-290 + 220-227), Pi-dashboard intent-types.ts.

---

### §3.3 — ADR-0096 NEW: Aelix Plugin Manifest v1

**File**: `docs/decisions/0096-manifest-v1-schema.md`

**Mandatory front-matter** (same template, title "0096. Aelix Plugin Manifest v1 (aelix-plugin.toml)").

**Required sections**:

1. `## Context` — Why a manifest. Pi has no manifest (auto-discovers `.ts` files); Aelix needs a manifest because: (a) capabilities declaration (Phase 6 enforcement gate), (b) API_LEVEL ABI versioning, (c) declarative slot contributions (faster discovery, build-time validation), (d) marketplace metadata (Phase 6), (e) cross-language extension authoring (Phase 6+ if Aelix opens to TS plugins via Tier 3).

2. `## File specification` —
   - **Location**: plugin root directory `aelix-plugin.toml`
   - **Format**: TOML 1.0 (Python `tomllib` native; pyproject.toml syntactic consistency)
   - **Encoding**: UTF-8
   - **Parser**: Pydantic v2 via `tomllib.loads()` → `PluginManifest.model_validate(...)`

3. `## Section schema (lock these)` —

   ```toml
   # aelix-plugin.toml v1

   [plugin]
   id          = "kebab-case-id"           # required, immutable, regex `^[a-z][a-z0-9-]{0,63}$`
   name        = "Display Name"            # required, free-form display
   version     = "0.1.0"                   # required, strict semver (no SHA fallback)
   description = "One-line description"    # required
   authors     = ["Author Name <email>"]   # required, non-empty list
   repository  = "https://github.com/..."  # required (Open VSX style for trust)
   license     = "MIT"                     # required, SPDX whitelist (see §3.3.5)
   homepage    = "https://..."             # optional

   [plugin.api]
   level       = 1                         # required, int — Aelix API ABI level this plugin was built against
   min_level   = 1                         # required, int — lowest level this plugin runs on (engine compatibility floor)

   [plugin.entry]
   python      = "my_pkg.extension:default"  # required when capabilities.ui_tui_trusted or .ui_descriptor or .mcp_serve

   [capabilities]
   shell_exec       = false   # bool — plugin needs shell command execution
   fs_write         = false   # bool — plugin writes to filesystem
   fs_read_user     = false   # bool — plugin reads outside cwd (user $HOME, $XDG_CONFIG_HOME, ...)
   net              = false   # bool — plugin opens network connections
   mcp_invoke       = false   # bool — plugin calls MCP servers
   ui_tui_trusted   = false   # bool — plugin provides Tier 1 TUI widgets
   ui_descriptor    = false   # bool — plugin emits Tier 2 descriptors
   ui_web_trusted   = false   # bool — plugin provides Tier 3 React/Svelte components (Phase 6 only)
   mcp_serve        = false   # bool — plugin runs as MCP server (T4)

   [activation]
   on_startup_finished = true              # bool — load after host startup completes
   on_command          = ["my-command"]    # list[str] — slash commands that trigger load
   on_tool_call        = ["my_tool"]       # list[str] — tool names that trigger load
   on_session_start    = false             # bool — load on session start event

   # NO `*` activation allowed (anti-pattern from VS Code, banned in Aelix)

   [contributes]
   commands      = [{ id = "...", description = "..." }]            # T1 commands
   tui_widgets   = [{ slot = "...", factory = "module:factory" }]   # T1 TUI Textual/Rich widget factories
   descriptors   = [{ kind = "...", id = "..." }]                   # T2 descriptors
   tools         = [{ name = "...", description = "..." }]          # T1 tools
   themes        = ["themes/dark.toml", "themes/light.toml"]        # path list
   mcp_servers   = [{ name = "...", transport = "stdio", command = "..." }]  # T4 MCP server configs
   hooks         = [{ event = "PreToolUse", command = "scripts/check.py" }]  # T4 subprocess hooks
   ```

4. `## API_LEVEL policy` —
   - Current `AELIX_API_LEVEL` = `1`.
   - Breaking changes to ANY public extension API increment.
   - Deprecation cycle: deprecated APIs warn for one minor release before removal.
   - Plugins declaring `min_level > AELIX_API_LEVEL` MUST be rejected at load time (Pydantic validator).
   - Plugins declaring `level > AELIX_API_LEVEL` MAY load with warning (forward-compat best-effort) — host MAY override via `--allow-future-api` flag.

5. `## SPDX license whitelist v1` —
   - Permitted licenses (v1): `MIT`, `Apache-2.0`, `BSD-3-Clause`, `BSD-2-Clause`, `MPL-2.0`, `ISC`, `Unlicense`, `Apache-2.0 WITH LLVM-exception`.
   - GPL family (GPL-2.0, GPL-3.0, AGPL-3.0, LGPL-2.1, LGPL-3.0): NOT in v1 whitelist (compatibility audit deferred to Phase 6).
   - Custom license string MAY be allowed via `license = "Custom (LICENSE-FILENAME.md)"` form (warning logged); strict whitelist enforcement gated by `--strict-licenses` flag (Phase 6 default true).

6. `## Capabilities declaration vs enforcement` —
   - Sprint 6h₉a / Phase 5b: capabilities are **declaration-only** (Pydantic validates the schema; runtime does NOT block based on declarations). This avoids premature lockdown while the API surface stabilizes.
   - Phase 6 enforcement: capabilities become actual capability tokens — host refuses to inject `shell.exec` / `fs.write` adapters into the plugin's `ctx` if not declared. Workspace trust dialog surfaces the capability list.
   - Aelix-additive: this declaration-vs-enforcement split mirrors VS Code's `capabilities.untrustedWorkspaces` (declarative metadata for v1) vs Zed's runtime capability check.

7. `## Activation policy` —
   - `*` activation BANNED (VS Code anti-pattern: forces always-load, slow startup).
   - Lazy load is mandatory. Plugins MUST declare at least one of: `on_startup_finished=true`, `on_command`, `on_tool_call`, `on_session_start`.
   - `on_command` triggers when slash command palette item is selected.
   - `on_tool_call` triggers when LLM (or extension) invokes a tool by name.
   - `on_session_start` triggers on every `session_start` event (Pi naming preserved).
   - Eager load (`on_startup_finished=true`) is permitted but discouraged for plugins with heavy import side-effects.

8. `## Pi divergences` — Pi has no manifest; Aelix manifest is synthesis of:
   - Neovim API_LEVEL pattern (semver-independent ABI versioning)
   - Zed SPDX whitelist + minimal contributes section
   - VS Code declarative `contributes.*` taxonomy
   - VS Code `activationEvents` lazy-loading semantics (without `*`)
   - Pi's `~/.pi/agent/extensions/` and `.pi/extensions/` auto-discovery paths translated to `~/.aelix/extensions/` and `.aelix/extensions/`
   - Open VSX-style identity (`id` + `repository` + `authors` + `license`) for marketplace trust (Phase 6)

9. `## References` — ADR-0094 (4-tier — manifest declares capabilities per tier), ADR-0095 (descriptor protocol — manifest's `[contributes.descriptors]` references it), ADR-0097 (multi-frontend — manifest's `capabilities.ui_web_trusted` is Phase 6 web flag), Pi extensions.md (auto-discovery paths reference), VS Code contribution-points docs, Neovim API_LEVEL RFC, Zed extension.toml docs.

---

### §3.4 — ADR-0097 NEW: Multi-Frontend Architecture

**File**: `docs/decisions/0097-multi-frontend-architecture.md`

**Mandatory front-matter** (template, title "0097. Multi-Frontend Architecture (RPC Gateway + Separate Web Repo + Self-Hosting Server Model)").

**Required sections**:

1. `## Context` — D1+D3+D4+D9. State: Aelix has two equal first-class user audiences (TUI = terminal/SSH/dev, Web = visual/desktop/marketplace), like Claude Code CLI vs Claude Code Desktop. Web cannot share TUI's process (different language, different deployment); requires separate repo + server daemon.

2. `## Architecture overview (small kernel + RPC + multiple frontends)` —

   ```
   Aelix Runtime (Python — small kernel)
     ├─ aelix-ai
     ├─ aelix-agent-core (+ contracts/ via Sprint 6h₉a)
     ├─ aelix-coding-agent
     └─ aelix-server (Phase 5b — Sprint 6h₉f) — FastAPI HTTP + WS gateway
        └─ JSONL RPC (ADR-0056) adapter + REST API + WebSocket event stream
   
   apps/aelix-tui (Python — Phase 5c, Sprint 6h₁₀a-d)
     └─ prompt-toolkit + Rich + Aelix widget layer (ADR-0088 amend)
   
   ┌── separate repo: aelix-web (Phase 6) ───────────────────┐
   │  apps/aelix-web (TS/React/Svelte — Phase 6 stack decision) │
   │  packages/rpc-client (TS SDK)                              │
   │  packages/plugin-runtime (Tier 3 React plugin infra)       │
   │  packages/server-extensions (auth/DB/marketplace)          │
   │  docker/ + helm/                                           │
   └────────────────────────────────────────────────────────────┘
   ```

3. `## aelix-server (Phase 5b carry-forward)` — Spec the package skeleton (full implementation: Sprint 6h₉f):
   - Location: `packages/aelix-server/`
   - Stack: FastAPI + uvicorn/hypercorn (Phase 6에서 multi-tenant 시 hypercorn 권장)
   - Endpoints (Phase 5b minimal):
     - `WS /rpc` — JSONL RPC frame stream (1 frame = 1 line, ADR-0056 reuse)
     - `WS /events` — event subscription (agent events + descriptor invalidations)
     - `GET /healthz` — liveness probe
     - `GET /schemas/{name}` — serve JSON Schemas from `docs/contracts/` (for aelix-web fetch)
   - Auth: **single-user dev mode** in Phase 5b (no auth header check, localhost-only bind by default). Phase 6 adds: OAuth/email-password/SAML/SSO decision.
   - DB: NONE in Phase 5b (in-memory state). Phase 6 adds: PostgreSQL or SQLite decision.
   - Configuration: `aelix-server.toml` or env vars (`AELIX_SERVER_BIND`, `AELIX_SERVER_PORT`, `AELIX_SERVER_AUTH_MODE`).
   - Sprint 6h₉a deliverable: ADR documentation only (no package skeleton this sprint).

4. `## Separate repo aelix-web (Phase 6)` — Lock the repo plan:
   - Repo URL: `github.com/handochan/aelix-web` (NEW, Phase 6 진입 시 생성)
   - Stack decisions (deferred to Phase 6 entry): React+Vite / SvelteKit / Next.js
   - Sandbox decision (deferred): trusted-only / iframe / WASM
   - Deployment: Docker image (Dockerfile + multi-stage build) + docker-compose.yml + Helm chart
   - License: separate from Aelix core (Phase 6 enterprise-tier optionality)
   - Cross-repo contract: JSON Schemas from `aelix-ai/docs/contracts/*.schema.json` published per Aelix release; aelix-web fetches via `GET /schemas/{name}` or copies on build.

5. `## Cross-repo contract (binding)` —
   - Aelix core is the **single source of truth** for descriptor envelope, slot taxonomy, manifest schema, RPC API
   - JSON Schemas live in `aelix-ai/docs/contracts/*.schema.json` (Sprint 6h₉a deliverable)
   - Python TypedDict + Pydantic in `aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/contracts/` (Sprint 6h₉a deliverable)
   - aelix-web fetches schemas at build OR via runtime `/schemas/{name}` endpoint
   - SemVer + API_LEVEL coordinated across repos via release notes
   - Breaking changes to contract = major version of BOTH repos coordinated

6. `## Self-hosting server model implications` —
   - Aelix runtime as long-running daemon (currently short-lived CLI — Phase 6 architectural shift)
   - Multi-user (Phase 6): user accounts, sessions per user, permissions, history
   - Deployment targets: homelab, VPS, 사내 server, SaaS
   - Compute model: Aelix runtime daemon + N web clients connecting to it OR M aelix-server instances behind load balancer (Phase 6 architecture decision)
   - Logging: structured JSON to stdout for container log capture
   - Observability: Phase 6 — Prometheus metrics, OpenTelemetry tracing (decision deferred)

7. `## Explicitly rejected approaches` (record what was considered and rejected):
   - **Tauri/Electron desktop app wrapping local CLI** — single-user, not aligned with sharing/marketplace
   - **textual-serve (same Textual code → browser)** — would give terminal-shaped web UI, cannot do charts/marketplace/rich UI
   - **Monorepo aelix-web package** — uv-managed Python + pnpm/npm 도구체인 이중화 비용
   - **Single-frontend assumption** — explicitly contradicts D1 (TUI/Web 양쪽 1차 시민)

8. `## Phase 6 deferred decisions` —
   - Web frontend stack
   - Tier 3 sandbox level
   - Auth/DB choice
   - Marketplace operating model
   - Desktop wrapper (Tauri/Electron) optionality

9. `## References` — ADR-0056 (JSONL RPC), ADR-0094 (4-tier — multi-frontend renders tier surfaces), ADR-0095 (descriptor protocol — cross-repo wire), ADR-0096 (manifest — capabilities.ui_web_trusted is Phase 6 flag), Pi-dashboard architecture (separate repo precedent), Open WebUI architecture (FastAPI + SvelteKit reference), LobeChat architecture (Next.js + plugin marketplace reference).

---

### §3.5 — ADR-0088 amend (Textual → prompt-toolkit + Rich + Aelix widget layer)

**File**: `docs/decisions/0088-phase-5b-tui-library-decision.md`

**Action**: AMEND (NOT replace). The existing ADR is a rich analysis document. Preserve the analysis (library comparison table, hybrid combinations, library-agnostic Component Protocol CRITICAL invariant, open questions). Modify ONLY:

1. **Status line**: `Status: Proposed (deferred to Phase 5b kickoff)` → `Status: Accepted (Sprint 6h₉a / W6 shipped — selection: prompt-toolkit + Rich + Aelix widget layer)`

2. **Date line**: Append `; Amended 2026-05-22 (Sprint 6h₉a)`.

3. **`## Decision` section**: Replace the "DEFERRED to Phase 5b kickoff" text with:

   ```markdown
   ## Decision

   **prompt-toolkit (input/editor) + Rich (output rendering) + Aelix self-built minimal widget layer.**

   Selection finalized at Sprint 6h₉a after the Phase 5b research wave (4-agent
   investigation + Pi direct source survey). This decision replaces the prior
   PRIMARY recommendation (textual + rich) for the reasons documented in §"Why
   the PRIMARY recommendation was reversed" below.

   The selection covers Aelix's TUI surface only. Web UI (Phase 6) is a
   separate stack decision documented in ADR-0097.
   ```

4. **NEW section after `## Decision`** (insert before `## CRITICAL invariant`):

   ```markdown
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
   ```

5. **NEW section** — insert before `## CRITICAL invariant`:

   ```markdown
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
   ```

6. **`## CRITICAL invariant` section**: PRESERVE verbatim (still binding). Update the suggested Component Protocol sketch to mention prompt-toolkit + Rich wrapper:

   Append to existing block:

   ```markdown
   The selected library composition (prompt-toolkit + Rich) is wrapped by a
   library-agnostic `Component` Protocol identical in spirit to Pi-tui's
   `Component` interface (synchronous `render(width: int) -> list[str]` +
   optional `handle_input(data: str) -> None` + `invalidate()`). Extensions
   call into this Protocol; the Protocol implementation delegates to
   prompt-toolkit / Rich. A future library swap (e.g., switching to a
   Python pi-tui port if maintenance costs justify) touches only the
   Protocol implementation, not extension authors.
   ```

7. **`## Open questions` section**: Update each item:
   - Q1 (extras dep model): **Resolved at Sprint 6h₉a** — `pip install aelix[tui]` extra installs `prompt-toolkit` + `rich`. No split.
   - Q2 (reactive semantics): **Resolved at Sprint 6h₉a** — Pi `setState`-style imperative update maps to direct `ctx.ui.set_widget(...)` re-invocation; no implicit reactive watchers. Host invalidates the relevant Rich Live region.
   - Q3 (Windows support): **Resolved at Sprint 6h₉a** — prompt-toolkit supports Windows PTY (verified via pyreadline3 / ConPTY); Rich supports Windows console. Verified parity.
   - Q4 (Kitty image protocol): **Open — Sprint 6h₁₀ Phase 5c carry-forward** — prompt-toolkit + Rich do NOT natively support Kitty/iTerm2 graphics; Aelix will use `term-image` library OR direct ANSI escape emission for inline image support. Decision in Sprint 6h₁₀c.
   - Q5 (theme parity): **Resolved at Sprint 6h₉a** — Pi theme names mapped to Rich Style + prompt-toolkit Style class via `aelix_widget_layer.theme` module (implementation Sprint 6h₁₀b).
   - Q6 (snapshot testing): **Resolved at Sprint 6h₉a** — Aelix will use `pyte`-driven terminal emulation snapshots (no built-in Textual equivalent). Implementation Sprint 6h₁₀d.
   - Q7 (editor seam): **Resolved at Sprint 6h₉a** — prompt-toolkit `Buffer` IS the editor seam. Pi `editor.ts` remote-control surface maps to direct `Buffer` API + `app.invalidate()`.
   - Q8 (OverlayOptions shape): **Resolved at Sprint 6h₉a** — Pi 9-anchor + responsive `visible` callback maps to prompt-toolkit `Float` with position + custom visibility filter (implementation Sprint 6h₁₀b).
   - Q9 (theme live-update): **Resolved at Sprint 6h₉a** — explicit `Application.invalidate()` after theme swap (prompt-toolkit standard pattern).
   - Q10 (backpressure on event stream): **Open — Sprint 6h₁₀a** — Rich Live update frequency vs token stream rate; implementation will throttle to ~30 FPS max (16-33ms intervals).

8. **`## Consequences` section**: Append:

   ```markdown
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
   ```

---

### §3.6 — Contracts Python package (Pydantic v2)

**Location**: `packages/aelix-agent-core/src/aelix_agent_core/contracts/`

**Pydantic dependency**: Add `pydantic >= 2.7, < 3` to `packages/aelix-agent-core/pyproject.toml` `[project] dependencies` (if not already present). If a top-level `pyproject.toml` or workspace lock pins a different range, harmonize.

**File-by-file specification**:

#### `__init__.py`

Exports:

```python
from .api_level import AELIX_API_LEVEL, assert_compatible
from .descriptor import (
    DescriptorEnvelope,
    DescriptorKind,
    DescriptorPayload,
    FooterSegmentPayload,
    StatusItemPayload,
    ToolRendererDescPayload,
    CommandRoutePayload,
    BreadcrumbPayload,
    ToastPayload,
    ManagementModalPayload,
    AgentMetricPayload,
    ActionDescriptor,
)
from .manifest import (
    PluginManifest,
    PluginIdentity,
    PluginApi,
    PluginEntry,
    Capabilities,
    Activation,
    Contributes,
    CommandContrib,
    TuiWidgetContrib,
    DescriptorContrib,
    ToolContrib,
    ThemeContrib,
    McpServerContrib,
    HookContrib,
    LICENSE_WHITELIST,
)
from .primitives import (
    TextPrimitive,
    BadgePrimitive,
    MetricPrimitive,
    TablePrimitive,
    GridPrimitive,
    FormPrimitive,
    GatePrimitive,
    ColumnSpec,
    FieldSpec,
    GridItem,
)
from .slots import (
    SlotKind,
    SLOT_MULTIPLICITY,
    SLOT_PAYLOAD_TIER,
)

__all__ = [...]  # explicit consolidated list
```

#### `api_level.py`

```python
"""Aelix Extension API ABI level (Neovim API_LEVEL pattern).

ADR-0096 §"API_LEVEL policy" — separate from Aelix's own semver to allow
plugin compatibility tracking across breaking changes.
"""

from __future__ import annotations

AELIX_API_LEVEL: int = 1
"""Current Aelix extension API ABI level.

Increment on breaking changes to ANY public extension API
(``ExtensionAPI``, ``ExtensionUIContext``, descriptor schema, manifest
schema). See ADR-0096 §"API_LEVEL policy" for the deprecation cycle.
"""

# Sprint 6h₉a Phase 5b-foundation: API level 1 baseline.
# This is the level emitted by ``AELIX_API_LEVEL`` and the level required by
# ``aelix-plugin.toml`` manifests authored against Phase 5b/5c contracts.


def assert_compatible(plugin_min_level: int, plugin_level: int) -> None:
    """Validate a plugin's declared API levels against the running host.

    Args:
        plugin_min_level: ``aelix-plugin.toml`` ``[plugin.api] min_level`` value.
        plugin_level: ``aelix-plugin.toml`` ``[plugin.api] level`` value.

    Raises:
        IncompatibleApiLevelError: When ``plugin_min_level >
            AELIX_API_LEVEL`` (host too old for the plugin).

    The reverse direction (``plugin_level > AELIX_API_LEVEL`` — plugin built
    for a future API) is currently accepted with a warning (forward-compat
    best-effort).
    """
    ...


class IncompatibleApiLevelError(ValueError):
    """Raised when the host's API level is below a plugin's required minimum."""
    ...


__all__ = ["AELIX_API_LEVEL", "assert_compatible", "IncompatibleApiLevelError"]
```

#### `descriptor.py`

Implement Pydantic v2 models matching §3.2.2 + §3.2.3 + §3.2.4 + §3.2.5 schemas.

Key shape:

```python
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field

DescriptorKind = Literal[
    "footer-segment",
    "status-item",
    "tool-renderer-desc",
    "command-route",
    "breadcrumb",
    "toast",
    "management-modal",
    "agent-metric",
]


class ActionDescriptor(BaseModel):
    """Wire-safe action reference (no function refs cross the wire)."""
    model_config = ConfigDict(extra="forbid")
    plugin_id: str = Field(..., pattern=r"^[a-z][a-z0-9-]{0,63}$")
    action: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    confirm: str | None = None


class FooterSegmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["footer-segment"] = "footer-segment"
    text: str
    icon: str | None = None
    tooltip: str | None = None


class StatusItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["status-item"] = "status-item"
    text: str
    level: Literal["info", "warning", "error"] = "info"


class ToolRendererDescPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tool-renderer-desc"] = "tool-renderer-desc"
    tool_name: str
    view: Literal["table", "grid", "form", "text"]
    title: str | None = None
    columns: list[dict[str, Any]] | None = None
    rows_path: str | None = None  # JSONPath into tool result for rows
    text_path: str | None = None  # JSONPath into tool result for text


class CommandRoutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["command-route"] = "command-route"
    command: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    description: str
    keybind: str | None = None


class BreadcrumbPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["breadcrumb"] = "breadcrumb"
    label: str
    href: str | None = None
    icon: str | None = None


class ToastPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["toast"] = "toast"
    text: str
    level: Literal["info", "warning", "error", "success"] = "info"
    auto_dismiss_ms: int | None = Field(default=4000, ge=0, le=60_000)


class ManagementModalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["management-modal"] = "management-modal"
    command: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    title: str
    view: Literal["table", "grid", "form"]
    fields: list[dict[str, Any]] | None = None
    columns: list[dict[str, Any]] | None = None
    actions: list[ActionDescriptor] = Field(default_factory=list)


class AgentMetricPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["agent-metric"] = "agent-metric"
    label: str
    value: str | float | int
    delta: str | None = None
    level: Literal["info", "success", "warning", "error"] = "info"


DescriptorPayload = Annotated[
    Union[
        FooterSegmentPayload,
        StatusItemPayload,
        ToolRendererDescPayload,
        CommandRoutePayload,
        BreadcrumbPayload,
        ToastPayload,
        ManagementModalPayload,
        AgentMetricPayload,
    ],
    Field(discriminator="kind"),
]


class DescriptorEnvelope(BaseModel):
    """Tier 2 cross-surface descriptor envelope (ADR-0095)."""
    model_config = ConfigDict(extra="forbid")
    kind: DescriptorKind
    namespace: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    id: str = Field(..., min_length=1, max_length=128)
    payload: DescriptorPayload
    removed: bool = False

    # Validator: payload.kind MUST match envelope.kind
    ...
```

Add a `model_validator(mode="after")` on `DescriptorEnvelope` enforcing `self.payload.kind == self.kind`.

#### `manifest.py`

Implement Pydantic v2 models per §3.3.3 schema. Key shape:

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

LICENSE_WHITELIST: frozenset[str] = frozenset({
    "MIT",
    "Apache-2.0",
    "BSD-3-Clause",
    "BSD-2-Clause",
    "MPL-2.0",
    "ISC",
    "Unlicense",
    "Apache-2.0 WITH LLVM-exception",
})


class PluginIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]{0,63}$")
    name: str = Field(..., min_length=1, max_length=128)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+(-[0-9A-Za-z-.]+)?(\+[0-9A-Za-z-.]+)?$")
    description: str = Field(..., min_length=1, max_length=512)
    authors: list[str] = Field(..., min_length=1)
    repository: str = Field(..., pattern=r"^https?://.+")
    license: str
    homepage: str | None = Field(default=None, pattern=r"^https?://.+")

    @model_validator(mode="after")
    def validate_license(self) -> "PluginIdentity":
        # Phase 5b: warn-only on unknown license (Phase 6 strict gate)
        ...
        return self


class PluginApi(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = Field(..., ge=1)
    min_level: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_ordering(self) -> "PluginApi":
        if self.min_level > self.level:
            raise ValueError("min_level must be <= level")
        return self


class PluginEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python: str | None = Field(default=None, pattern=r"^[\w.]+:\w+$")


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shell_exec: bool = False
    fs_write: bool = False
    fs_read_user: bool = False
    net: bool = False
    mcp_invoke: bool = False
    ui_tui_trusted: bool = False
    ui_descriptor: bool = False
    ui_web_trusted: bool = False
    mcp_serve: bool = False


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_startup_finished: bool = False
    on_command: list[str] = Field(default_factory=list)
    on_tool_call: list[str] = Field(default_factory=list)
    on_session_start: bool = False

    @model_validator(mode="after")
    def at_least_one(self) -> "Activation":
        has_any = (
            self.on_startup_finished
            or bool(self.on_command)
            or bool(self.on_tool_call)
            or self.on_session_start
        )
        if not has_any:
            raise ValueError("at least one activation trigger required (no `*`)")
        return self


class CommandContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(..., min_length=1)


class TuiWidgetContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: str = Field(..., min_length=1)
    factory: str = Field(..., pattern=r"^[\w.]+:\w+$")


class DescriptorContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    id: str


class ToolContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)


class ThemeContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., min_length=1)


class McpServerContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class HookContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str  # ADR-0017 hook event names (validated downstream Sprint 6h₉e)
    command: str = Field(..., min_length=1)
    timeout_ms: int = Field(default=60_000, ge=100, le=600_000)


class Contributes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commands: list[CommandContrib] = Field(default_factory=list)
    tui_widgets: list[TuiWidgetContrib] = Field(default_factory=list)
    descriptors: list[DescriptorContrib] = Field(default_factory=list)
    tools: list[ToolContrib] = Field(default_factory=list)
    themes: list[ThemeContrib] = Field(default_factory=list)
    mcp_servers: list[McpServerContrib] = Field(default_factory=list)
    hooks: list[HookContrib] = Field(default_factory=list)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plugin: PluginIdentity
    api: PluginApi = Field(..., alias="plugin.api", validation_alias=None)  # NOTE: TOML mapping done by parser, see below
    entry: PluginEntry = Field(default_factory=PluginEntry)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    activation: Activation
    contributes: Contributes = Field(default_factory=Contributes)
```

**NOTE on TOML key flattening**: TOML's `[plugin.api]` table parses as `{"plugin": {"api": {...}}}` in Python. The recommended pattern is a top-level parser helper:

```python
def parse_manifest_toml(toml_text: str) -> PluginManifest:
    raw = tomllib.loads(toml_text)
    # Flatten [plugin.api] → top-level `api`, [plugin.entry] → `entry`
    flattened = {
        "plugin": {k: v for k, v in raw.get("plugin", {}).items() if k not in {"api", "entry"}},
        "api": raw.get("plugin", {}).get("api", {}),
        "entry": raw.get("plugin", {}).get("entry", {}),
        "capabilities": raw.get("capabilities", {}),
        "activation": raw.get("activation", {}),
        "contributes": raw.get("contributes", {}),
    }
    return PluginManifest.model_validate(flattened)
```

#### `primitives.py`

Implement Pydantic v2 models for the 8 UI primitives per §3.2.4. Key shape:

```python
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class TextPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    style: Literal["default", "muted", "accent", "success", "warning", "error"] = "default"


class BadgePrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: str
    level: Literal["info", "success", "warning", "error"] = "info"


class MetricPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: str | float | int
    delta: str | None = None
    level: Literal["info", "success", "warning", "error"] = "info"


class ColumnSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    kind: Literal["text", "number", "boolean", "datetime", "badge", "code"] = "text"
    sortable: bool = False


class TablePrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[ColumnSpec]
    rows: list[dict[str, Any]]
    actions: list["ActionDescriptor"] = Field(default_factory=list)


class GridItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    subtitle: str | None = None
    badge: BadgePrimitive | None = None


class GridPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[GridItem]
    item_actions: list["ActionDescriptor"] = Field(default_factory=list)


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    kind: Literal["text", "number", "boolean", "select", "textarea", "code", "datetime"] = "text"
    required: bool = False
    values: list[str] | None = None  # for kind="select"


class FormPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[FieldSpec]
    submit_action: "ActionDescriptor"
    cancel_action: "ActionDescriptor | None" = None


class GatePrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flag: str
    when: dict[str, Any] = Field(default_factory=dict)
    on_blocked_action: "ActionDescriptor | None" = None


# Resolve forward refs at module bottom
from .descriptor import ActionDescriptor  # noqa: E402
TablePrimitive.model_rebuild()
GridPrimitive.model_rebuild()
FormPrimitive.model_rebuild()
GatePrimitive.model_rebuild()
```

#### `slots.py`

```python
"""Slot taxonomy v1 (ADR-0095) — multiplicity + payload tier tables.

The actual slot identifiers are ``DescriptorKind`` Literal values defined in
``descriptor.py``. This module exposes the metadata about each slot for
host renderers and manifest validators.
"""

from __future__ import annotations

from typing import Final, Literal

from .descriptor import DescriptorKind

SlotMultiplicity = Literal["one", "one-active", "many"]
SlotPayloadTier = Literal["descriptor-only", "react-or-descriptor", "react-only"]

SLOT_MULTIPLICITY: Final[dict[DescriptorKind, SlotMultiplicity]] = {
    "footer-segment": "many",
    "status-item": "many",
    "tool-renderer-desc": "one",          # one per (tool_name)
    "command-route": "one",               # one per (command)
    "breadcrumb": "many",
    "toast": "many",
    "management-modal": "one",            # one per (command)
    "agent-metric": "many",
}

SLOT_PAYLOAD_TIER: Final[dict[DescriptorKind, SlotPayloadTier]] = {
    # Phase 5b: all 8 v1 slots are descriptor-only (Aelix-additive: subset of
    # Pi-dashboard 22-slot, React-only slots deferred to Phase 6 expansion)
    "footer-segment": "descriptor-only",
    "status-item": "descriptor-only",
    "tool-renderer-desc": "descriptor-only",
    "command-route": "descriptor-only",
    "breadcrumb": "descriptor-only",
    "toast": "descriptor-only",
    "management-modal": "descriptor-only",
    "agent-metric": "descriptor-only",
}

SlotKind = DescriptorKind  # alias for readability

__all__ = [
    "SlotKind",
    "SlotMultiplicity",
    "SlotPayloadTier",
    "SLOT_MULTIPLICITY",
    "SLOT_PAYLOAD_TIER",
]
```

#### `packages/aelix-agent-core/pyproject.toml`

Add to `[project] dependencies`:

```toml
"pydantic>=2.7,<3",
```

If the existing dependency list is alphabetically sorted, preserve the sort.

---

### §3.7 — Schema generation script + initial generated schemas + docs/contracts README

**Location**: `scripts/generate_contracts_schemas.py`

**Purpose**: Generate JSON Schema files from Pydantic models. Idempotent. Used both for committed artifacts in `docs/contracts/` and for CI drift detection.

**Specification**:

```python
#!/usr/bin/env python3
"""Generate JSON Schemas from Aelix contract Pydantic models.

ADR-0096 §"Validation responsibility" — Pydantic v2 ``model_json_schema()`` is
the source of truth. This script emits the schemas as committed artifacts in
``docs/contracts/`` so cross-repo consumers (aelix-web Phase 6) can fetch
them without a Python dependency.

Idempotency: re-running produces no diff if models are unchanged. CI uses
``--check`` to fail on drift.

Usage:
    python scripts/generate_contracts_schemas.py            # write/update files
    python scripts/generate_contracts_schemas.py --check    # exit 1 if drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aelix_agent_core.contracts import (
    AELIX_API_LEVEL,
    DescriptorEnvelope,
    PluginManifest,
    TablePrimitive,
    GridPrimitive,
    FormPrimitive,
    BadgePrimitive,
    MetricPrimitive,
    TextPrimitive,
    GatePrimitive,
    ActionDescriptor,
    SLOT_MULTIPLICITY,
    SLOT_PAYLOAD_TIER,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "contracts"

SCHEMAS: dict[str, type] = {
    "manifest.schema.json": PluginManifest,
    "descriptor-envelope.schema.json": DescriptorEnvelope,
    "primitives.schema.json": None,  # composite — handled separately
}


def build_primitives_schema() -> dict[str, Any]:
    """Compose all 8 primitives into a single $defs schema."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Aelix UI Primitives",
        "description": (
            "ADR-0095 §UI primitives — schema for the 8 UI primitives that "
            "descriptor payloads compose."
        ),
        "$defs": {
            "TextPrimitive": TextPrimitive.model_json_schema(),
            "BadgePrimitive": BadgePrimitive.model_json_schema(),
            "MetricPrimitive": MetricPrimitive.model_json_schema(),
            "TablePrimitive": TablePrimitive.model_json_schema(),
            "GridPrimitive": GridPrimitive.model_json_schema(),
            "FormPrimitive": FormPrimitive.model_json_schema(),
            "GatePrimitive": GatePrimitive.model_json_schema(),
            "ActionDescriptor": ActionDescriptor.model_json_schema(),
        },
    }


def build_slot_taxonomy_schema() -> dict[str, Any]:
    """Static doc-style schema for the 8-slot taxonomy v1."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Aelix Slot Taxonomy v1",
        "description": (
            "ADR-0095 §8-slot taxonomy v1 — static metadata for descriptor "
            "kinds: multiplicity + payload tier. The actual descriptor schema "
            "is in descriptor-envelope.schema.json."
        ),
        "type": "object",
        "properties": {
            "api_level": {"type": "integer", "const": AELIX_API_LEVEL},
            "slots": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    slot: {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "multiplicity": {
                                "type": "string",
                                "enum": ["one", "one-active", "many"],
                            },
                            "payload_tier": {
                                "type": "string",
                                "enum": [
                                    "descriptor-only",
                                    "react-or-descriptor",
                                    "react-only",
                                ],
                            },
                        },
                        "required": ["multiplicity", "payload_tier"],
                    }
                    for slot in SLOT_MULTIPLICITY
                },
            },
        },
    }


def serialize(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit 1 on drift")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {
        "manifest.schema.json": serialize(PluginManifest.model_json_schema()),
        "descriptor-envelope.schema.json": serialize(DescriptorEnvelope.model_json_schema()),
        "primitives.schema.json": serialize(build_primitives_schema()),
        "slot-taxonomy.schema.json": serialize(build_slot_taxonomy_schema()),
    }

    drift = False
    for name, content in files.items():
        path = OUTPUT_DIR / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                print(f"[drift] {path.relative_to(OUTPUT_DIR.parent.parent)}", file=sys.stderr)
                drift = True
        else:
            path.write_text(content, encoding="utf-8")
            print(f"[wrote] {path.relative_to(OUTPUT_DIR.parent.parent)}")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
```

**Location**: `docs/contracts/README.md`

**Content** (~150-200 LOC):

```markdown
# Aelix Contracts

This directory contains the JSON Schema artifacts for Aelix's cross-surface
contract layer (ADR-0094 §"4-tier extension model", ADR-0095 §"UI Descriptor
Protocol", ADR-0096 §"Manifest v1", ADR-0097 §"Multi-Frontend Architecture").

## Source of truth

The Pydantic v2 models in
``packages/aelix-agent-core/src/aelix_agent_core/contracts/`` are the source
of truth. The JSON Schemas in this directory are **generated artifacts** —
do NOT hand-edit.

## Files

| File | Source model | Consumer |
|---|---|---|
| ``manifest.schema.json`` | ``PluginManifest`` | aelix-plugin.toml validators (host + IDE + marketplace) |
| ``descriptor-envelope.schema.json`` | ``DescriptorEnvelope`` | TUI/Web descriptor host renderers, cross-repo validation |
| ``primitives.schema.json`` | composite ($defs of 8 primitives + ActionDescriptor) | Tier 2 descriptor host renderers, Phase 6 Web slots |
| ``slot-taxonomy.schema.json`` | ``SLOT_MULTIPLICITY`` + ``SLOT_PAYLOAD_TIER`` | Slot registry validators (host + Phase 6 aelix-web) |

## Regeneration

```sh
python scripts/generate_contracts_schemas.py
```

The script is idempotent; re-running produces no diff if the Pydantic
models are unchanged.

## CI drift detection

CI runs:

```sh
python scripts/generate_contracts_schemas.py --check
```

This exits non-zero if any generated schema differs from the committed
artifact. Local fix:

```sh
python scripts/generate_contracts_schemas.py     # write current schemas
git add docs/contracts/*.schema.json
git commit -m "chore(contracts): regenerate JSON Schemas"
```

## Versioning policy

See ADR-0095 §"Versioning policy" and ADR-0096 §"API_LEVEL policy".

- Adding optional fields = minor (non-breaking).
- Renaming/removing required fields = major (breaking).
- Adding new descriptor kinds (``DescriptorKind`` Literal members) = minor.
- Renaming/removing descriptor kinds = major.
- Schema changes are accompanied by an ``AELIX_API_LEVEL`` bump for breaking
  changes.

## Cross-repo consumption

Phase 6 ``aelix-web`` (separate repo per ADR-0097) consumes these schemas:

1. **Build-time**: fetch from a pinned Aelix release tarball / npm artifact.
2. **Runtime**: ``GET /schemas/{name}`` endpoint on aelix-server (Phase 6
   multi-tenant gateway).

JSON Schema is the lingua franca; aelix-web does not need a Python
dependency.

## Current API level

Sprint 6h₉a baseline: **API level 1** (``AELIX_API_LEVEL = 1``).
```

---

### §3.8 — Contract validation tests

**Location**: `tests/contracts/test_contracts_schema.py` + `tests/contracts/__init__.py`

**Test coverage minimum**:

```python
"""Sprint 6h₉a — contract Pydantic model validation tests.

Smoke + roundtrip + edge-case tests for the contracts package. Not exhaustive
property-based testing (deferred to Phase 5b later sprints); enough to lock
the schema shape and catch obvious regressions.
"""

from __future__ import annotations

import json
import tomllib

import pytest

from aelix_agent_core.contracts import (
    AELIX_API_LEVEL,
    ActionDescriptor,
    Activation,
    BadgePrimitive,
    Capabilities,
    DescriptorEnvelope,
    FooterSegmentPayload,
    PluginManifest,
    SLOT_MULTIPLICITY,
    SLOT_PAYLOAD_TIER,
    TablePrimitive,
    TextPrimitive,
    assert_compatible,
)


# === api_level ===

def test_api_level_is_1():
    assert AELIX_API_LEVEL == 1


def test_assert_compatible_accepts_equal():
    assert_compatible(plugin_min_level=1, plugin_level=1)


def test_assert_compatible_accepts_lower_min_level():
    assert_compatible(plugin_min_level=1, plugin_level=2)


def test_assert_compatible_rejects_min_above_host():
    from aelix_agent_core.contracts.api_level import IncompatibleApiLevelError
    with pytest.raises(IncompatibleApiLevelError):
        assert_compatible(plugin_min_level=2, plugin_level=2)


# === DescriptorEnvelope ===

def test_descriptor_envelope_footer_segment_roundtrip():
    env = DescriptorEnvelope(
        kind="footer-segment",
        namespace="my-plugin",
        id="git-status",
        payload=FooterSegmentPayload(text="main", icon="git", tooltip="branch"),
    )
    dumped = env.model_dump(mode="json")
    restored = DescriptorEnvelope.model_validate(dumped)
    assert restored == env


def test_descriptor_envelope_kind_mismatch_raises():
    with pytest.raises(ValueError):
        DescriptorEnvelope(
            kind="footer-segment",
            namespace="my-plugin",
            id="x",
            # payload kind says status-item, envelope kind says footer-segment
            payload={"kind": "status-item", "text": "hello", "level": "info"},
        )


def test_descriptor_envelope_namespace_pattern():
    with pytest.raises(ValueError):
        DescriptorEnvelope(
            kind="footer-segment",
            namespace="UPPERCASE-BAD",
            id="x",
            payload=FooterSegmentPayload(text="hello"),
        )


def test_descriptor_envelope_extra_field_forbidden():
    with pytest.raises(ValueError):
        DescriptorEnvelope.model_validate({
            "kind": "footer-segment",
            "namespace": "ok",
            "id": "x",
            "payload": {"kind": "footer-segment", "text": "hello"},
            "extra_unexpected_field": "value",  # extra=forbid
        })


def test_descriptor_envelope_removed_default_false():
    env = DescriptorEnvelope(
        kind="footer-segment",
        namespace="ns",
        id="x",
        payload=FooterSegmentPayload(text="hello"),
    )
    assert env.removed is False


# === ActionDescriptor ===

def test_action_descriptor_minimal():
    a = ActionDescriptor(plugin_id="my-plugin", action="run-thing")
    assert a.payload == {}
    assert a.confirm is None


def test_action_descriptor_plugin_id_pattern():
    with pytest.raises(ValueError):
        ActionDescriptor(plugin_id="UPPERCASE", action="x")


# === PluginManifest ===

VALID_MANIFEST_TOML = """
[plugin]
id = "my-plugin"
name = "My Plugin"
version = "0.1.0"
description = "Test plugin"
authors = ["Test <test@example.com>"]
repository = "https://github.com/example/my-plugin"
license = "MIT"

[plugin.api]
level = 1
min_level = 1

[plugin.entry]
python = "my_plugin:extension"

[capabilities]
ui_descriptor = true

[activation]
on_startup_finished = true

[contributes]
commands = [{ id = "greet", description = "Say hello" }]
"""


def test_plugin_manifest_valid_toml_roundtrip():
    from aelix_agent_core.contracts.manifest import parse_manifest_toml
    m = parse_manifest_toml(VALID_MANIFEST_TOML)
    assert m.plugin.id == "my-plugin"
    assert m.api.level == 1
    assert m.api.min_level == 1
    assert m.capabilities.ui_descriptor is True
    assert m.activation.on_startup_finished is True
    assert len(m.contributes.commands) == 1
    assert m.contributes.commands[0].id == "greet"


def test_plugin_manifest_invalid_license_warns():
    # Phase 5b: warn-only; not enforced. This test pins the policy (not strict)
    ...


def test_plugin_manifest_invalid_version_rejected():
    ...


def test_plugin_manifest_no_activation_trigger_rejected():
    ...


def test_plugin_manifest_min_level_above_level_rejected():
    ...


# === slot taxonomy ===

def test_slot_taxonomy_has_8_slots():
    assert len(SLOT_MULTIPLICITY) == 8
    assert len(SLOT_PAYLOAD_TIER) == 8


def test_slot_taxonomy_keys_match():
    assert set(SLOT_MULTIPLICITY) == set(SLOT_PAYLOAD_TIER)


def test_slot_taxonomy_phase_5b_all_descriptor_only():
    # Sprint 6h₉a invariant — see ADR-0095 §"Pi-dashboard divergences"
    assert all(tier == "descriptor-only" for tier in SLOT_PAYLOAD_TIER.values())


# === primitives ===

def test_table_primitive_roundtrip():
    t = TablePrimitive(
        columns=[{"id": "col1", "label": "Column 1", "kind": "text"}],
        rows=[{"col1": "hello"}],
    )
    dumped = t.model_dump(mode="json")
    restored = TablePrimitive.model_validate(dumped)
    assert restored == t


def test_text_primitive_default_style():
    t = TextPrimitive(text="hello")
    assert t.style == "default"


def test_badge_primitive_default_level():
    b = BadgePrimitive(label="status", value="ok")
    assert b.level == "info"


# === JSON Schema generation ===

def test_descriptor_envelope_json_schema_valid():
    schema = DescriptorEnvelope.model_json_schema()
    assert "$defs" in schema or "properties" in schema
    # Smoke: no exception during generation


def test_plugin_manifest_json_schema_valid():
    schema = PluginManifest.model_json_schema()
    assert "$defs" in schema or "properties" in schema


def test_schema_generation_script_exists():
    import pathlib
    assert (pathlib.Path(__file__).parent.parent.parent / "scripts" / "generate_contracts_schemas.py").exists()
```

Total: ~30 tests covering schema validation, roundtrip, and policy invariants. Aim for ~250-300 LOC.

**`tests/contracts/__init__.py`**: empty file with `# Sprint 6h₉a — contract validation tests.\n` docstring.

---

## §4 — Commit split plan (W6, 6 atomic commits)

### Commit 1 (§A) — Contracts package skeleton

**Stage** (use `git add <paths>` explicitly):
- `packages/aelix-agent-core/src/aelix_agent_core/contracts/__init__.py`
- `packages/aelix-agent-core/src/aelix_agent_core/contracts/api_level.py`
- `packages/aelix-agent-core/src/aelix_agent_core/contracts/descriptor.py`
- `packages/aelix-agent-core/src/aelix_agent_core/contracts/manifest.py`
- `packages/aelix-agent-core/src/aelix_agent_core/contracts/primitives.py`
- `packages/aelix-agent-core/src/aelix_agent_core/contracts/slots.py`
- `packages/aelix-agent-core/pyproject.toml` (only if Pydantic dep was added)

**Commit message** (HEREDOC):

```
feat(contracts): Aelix extension contracts package skeleton (Sprint 6h₉a §A)

Pydantic v2 models for the 4-tier extension architecture contract layer:
- api_level: AELIX_API_LEVEL constant + assert_compatible helper
- descriptor: DescriptorEnvelope + 8-kind discriminated payload union
- manifest: PluginManifest matching aelix-plugin.toml v1 schema
- primitives: 8 UI primitives + ActionDescriptor wire-safe action ref
- slots: SLOT_MULTIPLICITY + SLOT_PAYLOAD_TIER metadata tables

ADR-0094/0095/0096 reference targets (ADRs land in commits 4-5).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 2 (§B) — Schema generation script + initial generated schemas + docs/contracts README

**Stage**:
- `scripts/generate_contracts_schemas.py`
- `docs/contracts/README.md`
- `docs/contracts/manifest.schema.json`
- `docs/contracts/descriptor-envelope.schema.json`
- `docs/contracts/primitives.schema.json`
- `docs/contracts/slot-taxonomy.schema.json`

**Commit message** (HEREDOC):

```
feat(contracts): JSON Schema generation script + initial schemas (Sprint 6h₉a §B)

scripts/generate_contracts_schemas.py — emits JSON Schemas from Pydantic
models in docs/contracts/. Idempotent. --check mode for CI drift detection.

Initial generated artifacts:
- manifest.schema.json (PluginManifest)
- descriptor-envelope.schema.json (DescriptorEnvelope + discriminated payload)
- primitives.schema.json (composite $defs of 8 primitives + ActionDescriptor)
- slot-taxonomy.schema.json (SLOT_MULTIPLICITY + SLOT_PAYLOAD_TIER tables)

docs/contracts/README.md documents the source-of-truth rule (Pydantic →
generated JSON Schema) and cross-repo consumption pattern (Phase 6
aelix-web fetch).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 3 (§C) — Contract validation tests

**Stage**:
- `tests/contracts/__init__.py`
- `tests/contracts/test_contracts_schema.py`

**Commit message** (HEREDOC):

```
test(contracts): Pydantic model validation tests (Sprint 6h₉a §C)

~30 tests covering:
- api_level constant + assert_compatible behavior
- DescriptorEnvelope roundtrip + kind/payload mismatch validation
- ActionDescriptor pattern enforcement
- PluginManifest TOML parsing + activation/license/version validators
- Slot taxonomy invariants (8 slots, Phase 5b descriptor-only)
- UI primitive roundtrip + default values
- JSON Schema generation smoke tests

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 4 (§D) — ADR-0094 + ADR-0095 (extension architecture + descriptor protocol)

**Stage**:
- `docs/decisions/0094-aelix-extension-architecture-4-tier.md`
- `docs/decisions/0095-ui-descriptor-protocol.md`

**Commit message** (HEREDOC):

```
docs: ADR-0094 (4-tier extension model) + ADR-0095 (descriptor protocol) — Sprint 6h₉a §D

ADR-0094 locks the 4-tier extension architecture for Aelix:
- T1 trusted in-process Python (Pi-parity factory function)
- T2 cross-surface descriptors (TUI + Web 동일 wire, forward-design)
- T3 rich React/Svelte components (Web only — Phase 6)
- T4 MCP servers + subprocess hooks (universal, peer-compatible)

ADR-0095 locks the Tier 2 wire format:
- DescriptorEnvelope { kind, namespace, id, payload, removed }
- 8-slot taxonomy v1 (subset of Pi-dashboard 22)
- 8 UI primitives + ActionDescriptor (no function refs cross wire)
- ui:list-modules synchronous-probe pattern (Pi-dashboard architecture.md)
- TUI Rich Renderable + Web React (Phase 6) mapping rules

Aelix-additive divergences from Pi documented per ADR-0093 convention.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 5 (§E) — ADR-0096 + ADR-0097 (manifest + multi-frontend)

**Stage**:
- `docs/decisions/0096-manifest-v1-schema.md`
- `docs/decisions/0097-multi-frontend-architecture.md`

**Commit message** (HEREDOC):

```
docs: ADR-0096 (manifest v1) + ADR-0097 (multi-frontend) — Sprint 6h₉a §E

ADR-0096 locks aelix-plugin.toml v1 schema:
- [plugin] identity + SPDX license whitelist (Zed pattern)
- [plugin.api] level + min_level (Neovim API_LEVEL pattern, semver-independent)
- [capabilities] declaration (Phase 5b warn-only, Phase 6 enforced)
- [activation] lazy load triggers (VS Code pattern, '*' banned)
- [contributes] declarative per-tier surfaces

ADR-0097 locks the multi-frontend architecture:
- Small kernel + RPC gateway + multiple frontends (D1: TUI + Web 양쪽 1차)
- Separate repo aelix-web (D3: 별도 레포)
- Self-hosting server daemon — Open WebUI 모델 (D4)
- aelix-server (FastAPI HTTP+WS gateway) Phase 5b skeleton (D9)
- Cross-repo contract via JSON Schemas (Sprint 6h₉a §B)
- Phase 6 deferred decisions: stack/sandbox/auth/DB/marketplace/desktop

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 6 (§F) — ADR-0088 amend + ADR-0098 closure

**Stage**:
- `docs/decisions/0088-phase-5b-tui-library-decision.md` (amended)
- `docs/decisions/0098-sprint-6h9a-phase-5b-foundation-lock.md` (NEW closure ADR)

**ADR-0098 content** (write per ADR-0093 closure pattern):

Title: `0098. Sprint 6h₉a — Phase 5b-foundation Lock`
Status: `Accepted (Sprint 6h₉a / W6 shipped)`
Sections: Context (what this sprint did), Decision (sprint deliverables 1-8 enumerated), Aelix-additive divergences summary (table), Pi citations (file:line list), Reference companions (ADR cross-refs), Verification (ruff/pyright/pytest baseline + new), Phase (Sprint 6h₉a shipped).

**Commit message** (HEREDOC):

```
docs: ADR-0088 amend (Textual → prompt-toolkit+Rich) + ADR-0098 closure — Sprint 6h₉a §F

ADR-0088 amend:
- Status: Proposed (deferred) → Accepted (Sprint 6h₉a / W6 shipped)
- Selection finalized: prompt-toolkit + Rich + Aelix self-built minimal widget layer
- §"Why the PRIMARY recommendation was reversed" documents Pi-tui custom-library
  discovery, Textual inline mode uncharted-territory risk, textual-serve
  Phase 6 moot (per ADR-0097 separate aelix-web repo + self-hosting model)
- §"Architecture of the selected stack" maps prompt-toolkit + Rich to Pi's
  ExtensionUIContext 25-method surface
- 10 open questions resolved (1 partially deferred — Kitty image protocol)
- Library-agnostic Component Protocol invariant preserved

ADR-0098 closure marks Sprint 6h₉a / W6 shipped with verification evidence
and Pi citation map.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §5 — Verification plan (W3-W5)

### W3 (verifier — evidence collection)

Run all commands; capture output:

```sh
uv run ruff check 2>&1 | tail -5
uv run pyright 2>&1 | tail -10  # MUST show 8 baseline errors (intentional fixtures in scripts/pyright_spike.py); zero new
uv run pytest 2>&1 | tail -5    # baseline + new contract tests
python scripts/generate_contracts_schemas.py --check
python scripts/generate_contracts_schemas.py  # idempotent re-run; no diff expected
```

Expected:
- ruff: clean
- pyright: 8 baseline (no new errors)
- pytest: 2380 baseline + ~30 new contract tests = ~2410 passed + 1 skipped
- schema generation: --check exits 0, regenerate produces no diff

Cross-ADR consistency check:
- ADR-0094 cites ADR-0095, ADR-0096, ADR-0097 (forward references OK)
- ADR-0095 cites ADR-0094 (parent), ADR-0096 (manifest contributes references), ADR-0097 (multi-frontend uses)
- ADR-0096 cites ADR-0094 (capabilities per tier), ADR-0095 (contributes.descriptors), ADR-0097 (ui_web_trusted)
- ADR-0097 cites ADR-0094 (renders tier surfaces), ADR-0095 (cross-repo wire), ADR-0096 (capabilities.ui_web_trusted Phase 6 flag), ADR-0056 (RPC), ADR-0083, ADR-0085
- ADR-0088 amend cites ADR-0097 (separate repo + self-hosting), ADR-0094, ADR-0095
- ADR-0098 cites all of the above + Sprint 6h₉a deliverables

### W4 (code-reviewer — severity-rated review)

Focus areas:
- Contract Pydantic models: validator correctness, `extra="forbid"` consistency, regex patterns
- Manifest TOML parser: section flattening correctness, edge cases
- Schema generation script: idempotency, drift detection
- ADR text: clarity, completeness, citation accuracy
- Sprint scope: did the commits stay atomic? any leakage across §A-§F?

Severity gates:
- MAJOR: schema validation gap that lets invalid manifest pass, ADR contradicting decision text, missing Pi citation
- MINOR: docstring inconsistency, redundant code, suboptimal Pydantic v2 idiom
- NIT: typos, formatting

### W5 (critic — Pi reference comparison + cross-ADR consistency)

Critic MUST:
- Verify Pi citation line numbers via `git ls-tree` or raw GitHub fetch against pinned SHA `734e08e`
- Confirm Pi-dashboard line citations against `BlackBeltTechnology/pi-agent-dashboard@develop`
- Cross-check ADR-0094 4-tier table against Agent A/B/C/D research reports (no drift)
- Cross-check ADR-0095 8-slot taxonomy against Pi-dashboard 22-slot subset claim
- Cross-check ADR-0096 manifest sections against Neovim API_LEVEL + Zed SPDX + VS Code activationEvents patterns
- Cross-check ADR-0097 against Open WebUI / LobeChat / Pi-dashboard reference architectures
- Cross-check ADR-0088 amend's reversal reasoning against the original §"Library options evaluated" tables

### W6 (commits) — see §4

---

## §6 — User-imposed constraints (BINDING — verbatim)

These constraints apply to ALL W2/W6 work in this sprint. Violation = sprint rework.

| # | Constraint |
|---|---|
| C1 | **DO NOT push** (user pushes manually with `! git push origin main`) |
| C2 | **DO NOT skip hooks** (`--no-verify` banned unless user explicit request, which is NOT the case this sprint) |
| C3 | **DO NOT stage** `.omc/project-memory.json` or `.omc/.project-memory.json.tmp.*` files (auto-generated, must not enter git) |
| C4 | **DO NOT modify** this spec file after W1 commit (spec is binding artifact) |
| C5 | **DO use** `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer on every commit |
| C6 | **DO use HEREDOC** for commit messages (`git commit -m "$(cat <<'EOF'\n…\nEOF\n)"` pattern) |
| C7 | **DO use** `git add <specific-paths>` — never `git add -A` or `git add .` |
| C8 | **Pi pin**: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` — no advance, no new Pi feature imported |
| C9 | UI work requires user consultation BEFORE starting (this sprint cleared via Q1-Q2-Q3 question rounds + dual-primary clarification) |
| C10 | Always do code review + Pi reference comparison thoroughly (W4 + W5 invocation required, NOT optional) |

---

## §7 — Pi citation map (canonical references for this sprint)

All Pi citations at SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`. Use `git show 734e08e:<path>` or `https://raw.githubusercontent.com/earendil-works/pi/734e08e/<path>` to verify.

| ADR | Cited Pi files / line ranges |
|---|---|
| ADR-0088 amend | `packages/tui/src/index.ts` (custom TUI exports), `packages/tui/package.json` (Mario Zechner author, deps), `packages/coding-agent/docs/tui.md` (Component interface, Focusable, CURSOR_MARKER), `packages/coding-agent/docs/extensions.md` (auto-discovery paths, ctx.ui.custom) |
| ADR-0094 | `packages/coding-agent/src/core/extensions/types.ts` (lines 1-300 ExtensionUIContext, lines 300-700 ExtensionContext, lines 700+ ToolDefinition+ToolRenderContext), `packages/coding-agent/docs/extensions.md` (lifecycle diagram, factory pattern) |
| ADR-0095 | Pi-dashboard `packages/shared/src/dashboard-plugin/slot-types.ts` (22-slot reference), Pi-dashboard `docs/architecture.md:180-290` (ui:list-modules), Pi-dashboard `docs/architecture.md:221-227` (descriptor schemas) |
| ADR-0096 | `packages/coding-agent/docs/extensions.md` (auto-discovery paths reference for Aelix translation), Pi types.ts (no manifest precedent — Aelix-additive) |
| ADR-0097 | ADR-0056 (Aelix JSONL RPC), Pi-dashboard `docs/architecture.md` (Bridge architecture), Pi-dashboard `packages/extension/` (Bridge extension reference) |

External references (not Pi):
- Neovim API_LEVEL: https://neovim.io/doc/user/api/, RFC https://github.com/neovim/neovim/pull/5535
- Zed extension.toml: https://zed.dev/docs/extensions/developing-extensions
- VS Code activationEvents: https://code.visualstudio.com/api/references/activation-events
- VS Code contribution points: https://code.visualstudio.com/api/references/contribution-points
- Harlequin entry-points pattern: https://github.com/tconbeer/harlequin/blob/main/src/harlequin/plugins.py
- Open WebUI architecture: https://github.com/open-webui/open-webui
- Aider (prompt-toolkit + Rich precedent): https://github.com/Aider-AI/aider

---

## §8 — Definition of Done

A 단계 (Sprint 6h₉a / Phase 5b-foundation) closure when ALL true:

- [ ] All 6 commits landed in main (per §4 plan, atomic, HEREDOC, Co-Authored-By)
- [ ] `uv run ruff check` clean
- [ ] `uv run pyright` 8 baseline errors preserved (zero new)
- [ ] `uv run pytest` baseline + ~30 new contract tests pass; 1 skipped
- [ ] `python scripts/generate_contracts_schemas.py --check` exits 0
- [ ] `python scripts/generate_contracts_schemas.py` re-run produces no diff (idempotent)
- [ ] All 5 ADRs (0094, 0095, 0096, 0097, 0098 + 0088 amend) follow ADR-0093 format (Status / Date / Pi pin / binding principle / Context / Decision / divergences / references / Phase)
- [ ] `docs/contracts/README.md` exists with regeneration + cross-repo consumption documentation
- [ ] `docs/contracts/*.schema.json` × 4 files exist (manifest, descriptor-envelope, primitives, slot-taxonomy)
- [ ] Pi citations verified at SHA `734e08e` (W5 critic responsibility)
- [ ] Sprint 6h₉a closure ADR-0098 marks shipping with verification evidence
- [ ] Co-Authored-By trailer on every commit
- [ ] No staged `.omc/project-memory.json` or temp files
- [ ] No push (user pushes manually)

---

## §9 — Glossary

| Term | Definition |
|---|---|
| **Aelix API ABI level** | The integer (`AELIX_API_LEVEL`) that increments on breaking changes to the public extension API surface. Separate from Aelix's own semver. Plugins declare `min_level` as their floor. |
| **Tier (in 4-tier model)** | One of T1/T2/T3/T4 — a category of extension contribution distinguished by surface (Python factory / descriptor JSON / React component / MCP+hook), trust model, and rendering target. |
| **Descriptor envelope** | The Tier 2 wire format: `{ kind, namespace, id, payload, removed }`. JSON-serializable, function-ref-free. |
| **Slot** | A named rendering site in the host UI (e.g., `footer-segment`). One of 8 in v1 taxonomy. Same slot identifier renders on both TUI and Web. |
| **Multiplicity (of a slot)** | `one` / `one-active` / `many` — how many descriptors can claim the slot simultaneously. |
| **Payload tier** | `descriptor-only` / `react-or-descriptor` / `react-only` — whether the slot's payload can be a descriptor, a React component, or both. Phase 5b: all 8 slots are descriptor-only. |
| **ActionDescriptor** | Wire-safe action reference: `{ plugin_id, action, payload, confirm? }`. Frontend dispatches `plugin_action` event with this; host routes to the plugin's registered action handler. |
| **`ui:list-modules` probe** | Synchronous event emitted by host to collect all Tier 2 descriptor contributions. Pi-dashboard pattern, Aelix-additive (Pi has no such event). |
| **Library-agnostic Component Protocol** | The Aelix Protocol that wraps prompt-toolkit + Rich primitives so extensions don't bind to library types. Preserved from ADR-0088 CRITICAL invariant. |
| **Self-hosting server** | An Aelix Web deployment where a long-running aelix-server daemon (FastAPI) serves N web clients. Multi-user. Phase 6 evolution. |
| **Manifest** | The `aelix-plugin.toml` file at a plugin's root. Declares identity, API level, capabilities, activation triggers, contributes. |
| **Aelix-additive divergence** | A deliberate deviation from Pi that exists because Aelix has architectural requirements Pi does not (Pi parity at API surface level, but the additional structure is Aelix-specific). |
| **Pi parity (binding goal)** | The top-level binding principle that Aelix's runtime behavior, API surface, and extension UX must match `earendil-works/pi` at the pinned SHA. Implementation language and internal details may differ; observable behavior must match. |

---

## §10 — Aelix-additive divergences summary (for ADR-0098 reference)

| ADR | Divergence | Pi behavior | Aelix-additive behavior | Justification |
|---|---|---|---|---|
| 0088 amend | TUI stack choice | Custom pi-tui library (Mario Zechner authored, ~9000 LOC custom) | prompt-toolkit + Rich + thin Aelix widget layer | Python ecosystem stability (10+ year libraries), no self-built library maintenance, aider precedent |
| 0094 | Tier 2 descriptor tier | Pi has no descriptor tier (extensions emit components directly) | Aelix introduces descriptors as cross-surface wire format | Phase 6 Web needs language-neutral wire format; Pi-dashboard retrofitted descriptors and paid the cost (issue #32 maintainer admission) |
| 0094 | Tier 3 rich React tier | N/A (Pi is TUI-only in core; Pi-dashboard separate repo handles web) | Aelix introduces T3 for Phase 6 Web rich components | Open WebUI / LobeChat / Claude Code Desktop equivalence (D1 dual-primary) |
| 0094 | Tier 4 MCP+hooks elevated to formal tier | Pi supports MCP via extension; hooks via subprocess (subprocess hook is implicit) | Aelix formalizes T4 as universal extension surface peer-compatible with Claude Code / gemini-cli | Universal pattern emerging in coding-agent ecosystem (Agent B report); makes Aelix attractive to extension authors who also target peer agents |
| 0095 | 8-slot taxonomy | Pi-dashboard has 22 slots | Aelix v1 has 8 slots (subset, descriptor-only) | Phase 5b is TUI-first; Pi-dashboard's react-only slots require Phase 6 Web; Aelix can extend taxonomy at minor version bump |
| 0095 | `ui:list-modules` synchronous probe | Aelix-additive (Pi has no such event) | Synchronous probe pattern from Pi-dashboard architecture | Decouples descriptor declaration from extension lifecycle event coupling; faster discovery |
| 0096 | Manifest required | Pi auto-discovers `.ts` files in `~/.pi/agent/extensions/` (no manifest) | Aelix requires `aelix-plugin.toml` | API_LEVEL versioning, capabilities declaration (Phase 6 enforcement), declarative contributes for build-time validation, marketplace metadata (Phase 6) |
| 0096 | API_LEVEL | Pi has no formal ABI level (semver only) | Aelix has `AELIX_API_LEVEL` separate from Aelix semver | Neovim API_LEVEL pattern; allows plugin compat tracking across breaking changes |
| 0096 | License SPDX whitelist | Pi accepts any license | Aelix v1 whitelist (MIT, Apache-2.0, BSD, MPL, ISC, Unlicense) | Zed extension.toml pattern; marketplace trust baseline (Phase 6) |
| 0096 | Activation events | Pi loads all extensions eagerly | Aelix has lazy activation triggers (VS Code pattern, no `*`) | Faster startup as ecosystem grows |
| 0097 | Multi-frontend architecture | Pi has TUI only in core; Pi-dashboard separate repo handles web | Aelix has TUI + Web both first-class (D1) | User-defined dual-primary audience |
| 0097 | Separate `aelix-web` repo | Pi-dashboard precedent (BlackBeltTechnology separate repo) | Aelix follows same pattern | Python+TS toolchain split, deployment artifact separation |
| 0097 | Self-hosting server | Pi is short-lived CLI per session | Aelix Phase 6 introduces long-running server daemon (Open WebUI pattern) | Multi-user / marketplace / shared session model |

---

## §11 — Cross-ADR consistency check matrix

| ADR | Cites | Cited by |
|---|---|---|
| 0088 (amend) | 0094, 0095, 0097, 0098 | 0098 |
| 0094 | 0088, 0095, 0096, 0097, 0098 (closure) | 0095, 0096, 0097, 0098 |
| 0095 | 0094, 0096, 0097 | 0094, 0096, 0097, 0098 |
| 0096 | 0094, 0095, 0097 | 0094, 0095, 0097, 0098 |
| 0097 | 0056 (RPC), 0094, 0095, 0096, 0083, 0085 | 0088 (amend), 0098 |
| 0098 (closure) | 0088, 0094, 0095, 0096, 0097, Sprint 6h₉a §0-§K | — |

Each ADR MUST cite at least the four it depends on in the chain (forward refs to NEW ADRs in the same sprint are allowed and expected).

---

## §12 — End of spec

Spec author: Main agent (W0+W1, Sprint 6h₉a)
Spec status: Binding (do not modify)
Spec scope: Sprint 6h₉a only (subsequent sprints have their own specs)

W2 executor: pick this up. Execute commits 1-6 per §4 in order. Verify per §5. Honor §6 constraints. Output a sprint summary at W2 completion citing each commit SHA + verification evidence per §8 DoD.
