# 0095. UI Descriptor Protocol (Tier 2 Cross-Surface Wire Format)

Status: Accepted (Sprint 6h₉a / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

ADR-0094 locks the 4-tier extension model; Tier 2 is the "cross-surface
descriptors" tier. This ADR specifies the **wire format** for Tier 2.

Why a protocol is needed: Aelix has two equal first-class audiences (TUI
+ Web, per D1). A T1 in-process widget render only works on the surface
that shares the plugin's process — the Web frontend runs in a separate
TypeScript codebase (per ADR-0097), so it cannot directly invoke Python
widget classes. The descriptor protocol is the canonical cross-surface
wire that lets the same plugin contribute UI to both TUI and Web with a
single declaration.

Forward-design (not retrofit): Pi-dashboard introduced descriptors after
the fact when its Web UI had outpaced the Pi-tui API; the retrofit
imposed coupling and scope drift (issue #32). Aelix designs the
descriptor protocol now so Phase 5b TUI extensions render unchanged on
Phase 6 Web.

## Descriptor envelope

The wire format is the `DescriptorEnvelope` Pydantic model (see
`packages/aelix-agent-core/src/aelix_agent_core/contracts/descriptor.py`):

```python
class DescriptorEnvelope(BaseModel):
    kind: DescriptorKind                # one of the 8 slots below
    namespace: str                      # ^[a-z0-9][a-z0-9-]{0,63}$ (Pi-dashboard regex)
    id: str                             # unique within (kind, namespace)
    payload: DescriptorPayload          # discriminated union by `kind`
    removed: bool = False               # True = host removes the descriptor with matching (kind, namespace, id)
```

Removal is explicit (`removed: true`); there is no implicit removal.
The host's descriptor registry is keyed by `(kind, namespace, id)` and
replacement is idempotent (re-emitting the same key overwrites the
prior payload).

A `model_validator(mode="after")` on `DescriptorEnvelope` enforces
`payload.kind == envelope.kind` to catch mis-construction at the call
site.

## 8-slot taxonomy v1

Aelix v1 ships 8 slots — an Aelix-additive subset of Pi-dashboard's 22:

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

The slot identifiers are exported as the `DescriptorKind` Literal in
`contracts/descriptor.py`. Multiplicity and payload-tier metadata are
exported as the `SLOT_MULTIPLICITY` and `SLOT_PAYLOAD_TIER` dictionaries
in `contracts/slots.py`.

## UI primitives

Descriptor payloads compose from 8 primitives (defined in
`contracts/primitives.py`):

| Primitive | Purpose | Pydantic schema (summary) |
|---|---|---|
| `text` | Plain string with optional style | `{ text: str, style: Literal["default","muted","accent","success","warning","error"] = "default" }` |
| `badge` | Inline label/value pair | `{ label: str, value: str, level: Literal["info","success","warning","error"] = "info" }` |
| `metric` | Numeric metric display | `{ label: str, value: str \| float \| int, delta: Optional[str], level: Literal[...] = "info" }` |
| `table` | Tabular data | `{ columns: list[ColumnSpec], rows: list[dict[str, Any]], actions: list[ActionDescriptor] = [] }` |
| `grid` | Card grid | `{ items: list[GridItem], item_actions: list[ActionDescriptor] = [] }` |
| `form` | Input form | `{ fields: list[FieldSpec], submit_action: ActionDescriptor, cancel_action: Optional[ActionDescriptor] }` |
| `gate` | Conditional access gate | `{ flag: str, when: dict[str, Any], on_blocked_action: Optional[ActionDescriptor] }` |
| `action` | (NOT a render primitive — see `ActionDescriptor` below) | — |

Composite types:

- `ColumnSpec`: `{ id, label, kind: Literal["text","number","boolean","datetime","badge","code"] = "text", sortable: bool = False }`
- `FieldSpec`: `{ id, label, kind: Literal["text","number","boolean","select","textarea","code","datetime"] = "text", required: bool = False, values: Optional[list[str]] = None }`
- `GridItem`: `{ id, title, subtitle: Optional[str] = None, badge: Optional[BadgePrimitive] = None }`

## ActionDescriptor

The action wire format (defined in `contracts/descriptor.py`):

```python
class ActionDescriptor(BaseModel):
    plugin_id: str       # The plugin that registered the action (host dispatches back here)
    action: str          # action name within plugin (plugin's own routing key)
    payload: dict[str, Any] = Field(default_factory=dict)
    confirm: Optional[str] = None   # if set, host shows confirm dialog with this message before dispatch
```

**CRITICAL** — function references NEVER cross the wire. The action is
a string key the plugin matches via reverse channel (the host emits a
`plugin_action` event from frontend → host → T1 plugin's registered
action handler). This is the same pattern Pi-dashboard uses with its
`IntentNode` / `ActionDescriptor` types.

## ui:list-modules synchronous-probe pattern

Contribution discovery is via a synchronous probe (Pi-dashboard
`docs/architecture.md:180-290`):

- On session start (and at any T1 `ctx.ui.invalidate_descriptors()`
  call), the host emits a synchronous `ui:list-modules` event.
- T1 extensions listen via `api.on("ui:list-modules", lambda probe:
  probe.modules.append(...))` (the Aelix Python equivalent of
  Pi-dashboard's `pi.events.on("ui:list-modules", (probe) => {
  probe.modules.push(...descriptor...) })`).
- All descriptor contributions are collected synchronously during the
  emit.
- The host partitions by `kind` and dispatches to the appropriate slot
  renderer.

Aelix-additive note: Pi itself has no `ui:list-modules` event. The
pattern is borrowed from Pi-dashboard (`docs/architecture.md:221-227`)
and applies cleanly to the cross-surface wire because the probe is
synchronous and code-free.

## Wire format guarantees

- **JSON-serializable end-to-end**: `model_dump(mode="json")` produces a
  valid JSON payload that round-trips through `model_validate`.
- **Function references NEVER cross the wire**: use `ActionDescriptor`
  for any callback semantics; the host dispatches via the reverse
  channel.
- **Removal is explicit**: only `removed: true` removes; no implicit
  TTL or session-end cleanup at the wire level.
- **Idempotent replacement**: re-emitting the same `(kind, namespace,
  id)` replaces the prior descriptor atomically.
- **Forward-compatible**: an unknown `kind` value is logged and dropped
  (not an error). This lets newer plugins emit descriptors against a
  taxonomy extension without breaking older hosts.

## TUI rendering rules

For each of the 8 kinds, the TUI host maps the payload to a Rich
Renderable (prompt-toolkit + Rich per ADR-0088). Phase 5b only locks
the schema; the renderer implementation lands in Sprint 6h₁₀c. The
binding mapping intent:

- `footer-segment` → Rich `Panel.right_segments` entry; ordering by
  emission timestamp; many per session.
- `status-item` → Rich `Status` component above the footer; level color
  styling.
- `tool-renderer-desc` → Rich `Table` for view=table, Rich `Columns`
  grid for view=grid, ad-hoc Rich form for view=form, Rich `Panel` with
  styled text for view=text.
- `command-route` → prompt-toolkit autocomplete completion entry,
  filtered by the slash command typed.
- `breadcrumb` → Rich `Panel.top` segment chain.
- `toast` → prompt-toolkit `Float` window with auto-dismiss timer.
- `management-modal` → prompt-toolkit full-screen `Container` overlay
  driven by the registered command.
- `agent-metric` → Rich `Status` cell in a sidebar Columns layout.

## Web rendering rules (Phase 6 deferred)

Phase 6 Web (separate repo `aelix-web` per ADR-0097) renders each kind
to a named React/Svelte slot. The slot identifier for Phase 6 manifest
claim equals the `kind` literal with a `slot:` prefix:

- `footer-segment` → `slot:footer-segment`
- `status-item` → `slot:status-item`
- `tool-renderer-desc` → `slot:tool-renderer-desc`
- `command-route` → `slot:command-route`
- `breadcrumb` → `slot:breadcrumb`
- `toast` → `slot:toast`
- `management-modal` → `slot:management-modal`
- `agent-metric` → `slot:agent-metric`

Full Web rendering specification lands in the Phase 6 sprint where the
frontend stack is selected (Open WebUI / SvelteKit / Next.js — deferred
per ADR-0097 §"Phase 6 deferred decisions").

## Versioning policy

The contract layer follows these rules:

- Adding a new `kind` to the taxonomy = **minor** (non-breaking). Older
  hosts log + drop unknown kinds (forward-compatibility guarantee).
- Renaming or removing a `kind` = **major** (breaking). Requires
  `AELIX_API_LEVEL` bump.
- Adding optional fields to a payload schema = **minor**. Defaults
  preserve backward compatibility.
- Renaming or removing required fields of a payload = **major**.
- `AELIX_API_LEVEL` bumps follow ADR-0096. Plugins declaring
  `[plugin.api] min_level > AELIX_API_LEVEL` MUST be rejected at load
  time.
- **Schema drift detection**: `scripts/generate_contracts_schemas.py
  --check` re-generates JSON Schemas and exits non-zero on drift. CI
  treats drift as a build failure.

## Pi-dashboard divergences

Aelix v1's 8 slots are a subset of Pi-dashboard's 22:

- Pi-dashboard's React-only slots (e.g., `sidebar-folder-section`,
  `anchored-popover`, `session-card-action-bar`) require Phase 6 Web
  and are intentionally deferred. Aelix can extend the taxonomy at
  minor-version bumps as Web use cases emerge in Phase 6.
- Pi-dashboard's `payload_tier` axis is preserved (`descriptor-only`,
  `react-or-descriptor`, `react-only`); Aelix v1 sets all 8 slots to
  `descriptor-only` because Phase 5b is TUI-first.
- The `removed: bool` field on the envelope is identical to
  Pi-dashboard's removal semantics.
- The namespace regex `^[a-z0-9][a-z0-9-]{0,63}$` matches Pi-dashboard
  verbatim.

## References

- ADR-0094 (Sprint 6h₉a) — Aelix Extension Architecture (4-tier model). T2 is this protocol's tier.
- ADR-0096 (Sprint 6h₉a) — Aelix Plugin Manifest v1. `[contributes.descriptors]` references this protocol.
- ADR-0097 (Sprint 6h₉a) — Multi-Frontend Architecture. Uses this protocol as the cross-repo wire.
- Pi-dashboard `packages/shared/src/dashboard-plugin/slot-types.ts:1-300` — 22-slot reference taxonomy.
- Pi-dashboard `packages/shared/src/dashboard-plugin/slot-registry.ts` — slot registry implementation reference.
- Pi-dashboard `docs/architecture.md:180-290` — `ui:list-modules` synchronous probe pattern.
- Pi-dashboard `docs/architecture.md:221-227` — descriptor schema design notes.
- Pi-dashboard `packages/shared/src/dashboard-plugin/intent-types.ts` — `IntentNode` + `ActionDescriptor` wire (Aelix's `ActionDescriptor` is the same shape).
