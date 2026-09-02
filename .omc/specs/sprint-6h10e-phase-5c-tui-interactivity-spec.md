# Sprint 6h₁₀e — TUI Tier-2 Interactivity Completion (Phase 5c-tui backlog)

**Status:** DRAFT (W2). Builds on 6h₁₀c/d (ADR-0106/0107). Closure ADR = 0108.
Completes the 6h₁₀c/d deferred interactivity — **all pure-consumer, zero contract change**
(W1 architect-verified: `ActionDescriptor` + `plugin_action` reverse-channel + `tool_name`/`command`
discriminators are already in the frozen contract; the EventBus accepts arbitrary channels).

## §1 — Scope (no-contract subset)

| § | Item | Design (W1) | Seam | Verdict |
|---|---|---|---|---|
| A | **ActionDescriptor reverse-channel** | `DescriptorRenderer.dispatch_action(action)`: if `action.confirm` set → await `ctx.confirm(msg)` (existing dialog) then `event_bus.emit("plugin_action", action.model_dump(mode="json"))`; else emit directly. Log on failure (EventBus swallows handler exc). | `descriptors.py` + `event_bus` (run_tui injects) | KEYSTONE |
| B | **live tool-result interception** | `EventRenderer` gets a live lookup `get_tool_renderer_desc: Callable[[str], envelope|None]` (reads the registry by reference, like command-routes). On `tool_execution_end`, if `event.tool_name` matches a stored `tool-renderer-desc`, render `build_tool_renderable(env, rows)` into scrollback instead of the default Text dump. **Result→rows projection**: minimal — JSON-parse `ToolResult` content into `list[dict]` for table/grid/form; `text` view uses the raw text; honor `rows_path`/`text_path` only if trivially (dotted-key) extractable, else fall back to the raw content (full JSONPath DEFERRED). | `render.py` (`on_agent_event`/`_render_tool_end`) + `shell.py` wiring + `descriptors.py` (projection helper) | PURE |
| C | **management-modal command-trigger + action dispatch** | In `_input_loop`, when a submitted line is `/command` matching a stored `management-modal`'s `command` (or a `command-route`), route to `renderer.open_modal(env)` instead of `harness.prompt`. Modal `actions` dispatch via §A. `_input_loop` gets the `DescriptorRenderer` ref. | `shell.py` `_input_loop` + `descriptors.py` lookup | PURE |
| D | **breadcrumb dedicated row** | New `chrome._breadcrumb_line` + `set_breadcrumb_line` (mirror `set_header_line`) + a gated ConditionalContainer row in the HSplit; `_recompose_breadcrumbs` targets it (frees the header line from collision). | `chrome.py` + `descriptors.py` | PURE |
| E | **agent-metric metric ROW (NOT sidebar)** | Compose ALL agent-metrics into one Rich `Columns`/horizontal strip → one `set_widget` slot (level color, label/value/delta). A true VSplit sidebar conflicts with the inline `full_screen=False` model (ADR-0105:5-8) — documented divergence; Web Phase 6 renders the true `slot:agent-metric` sidebar. | `descriptors.py` | PURE (divergence noted) |
| F | **fixture extension + real-harness QA** | A test-only Tier-1 fixture `def setup(api): api.events.on("ui:list-modules", lambda p: p.modules.append(...))` emitting command-route + status-item + toast + management-modal; a test builds a real `AgentHarness(options.extensions=[fixture])` and drives `run_tui` to assert the live probe→render→dispatch path. | `tests/` | PURE (test) |

## §2 — Out of scope / deferred
- **`entry.py` extension loading in the shipped TUI** — the interactive CLI builds `AgentHarnessOptions` with NO `extensions=` (`entry.py:154`), so extensions are test-only today. Wiring `discover_and_load_extensions` would make the descriptor system reachable in the shipped TUI, BUT enabling Tier-1 (trusted Python) extension loading in the shipped agent is a **product/security decision** — deferred to a separate, explicitly-authorized change. (This sprint proves the path via the §F real-harness test.)
- `ctx.ui.invalidate_descriptors()` live re-probe — contract-touching (adds to the `ExtensionUIContext` Protocol). Deferred.
- Full JSONPath `rows_path`/`text_path` semantics — §B does a minimal dotted-key/raw projection; full JSONPath deferred.

## §3 — Module layout
```
tui/descriptors.py  # dispatch_action (§A); tool result→rows projection (§B); breadcrumb→new setter (§D); agent-metric compose (§E)
tui/render.py       # EventRenderer: tool_execution_end → matched tool-renderer-desc (§B)
tui/chrome.py       # _breadcrumb_line + set_breadcrumb_line + gated row (§D)
tui/shell.py        # wire dispatch_action(event_bus) + EventRenderer lookup + _input_loop /command→open_modal (§A/§B/§C)
tests/tui/…         # unit tests per item + tests/tui/test_fixture_ext.py real-harness QA (§F)
```

## §4 — Constraints
- `contracts/` / `docs/contracts` / `rpc` / `harness` / `mcp` BYTE-UNCHANGED. pyright 8-baseline (0 new). Full suite green (2796 baseline + new).
- Reuse existing seams: live-by-reference lookups (the command-routes pattern), `self._spawn` for async confirm before sync emit, the `renderer_height_is_known` gate for the new breadcrumb row, headless-drive test pattern.
- `_input_loop` `/command` matching lives in the shell against the live registry — `parse_input_line` stays PURE (it's reused by `cli/repl.py`).

## §5 — Test plan
- §A: `dispatch_action` emits `plugin_action` with the action dict (fake event_bus captures); `confirm` set → confirm dialog invoked before emit; emit failure logged not raised.
- §B: `tool_execution_end` with a matching tool-renderer-desc → custom table/grid/form/text committed; no match → default; rows projection from JSON content; text view.
- §C: `/known-modal-command` → open_modal spawned (not sent to model); unknown `/x` → still prompt; a modal action → dispatch_action.
- §D: breadcrumb renders in the new row, not the header line; header+breadcrumb coexist.
- §E: multiple agent-metrics compose into one strip with level color.
- §F: real-harness fixture → probe → command-route in completer + status/toast rendered + modal openable.

## §6 — Atomic commit plan (await authorization)
| # | § | message |
|---|---|---|
| 1 | §A | `feat(tui): ActionDescriptor reverse-channel — dispatch_action via plugin_action EventBus (6h₁₀e §A)` |
| 2 | §B | `feat(tui): live tool-result interception — tool-renderer-desc on tool_execution_end (6h₁₀e §B)` |
| 3 | §C | `feat(tui): management-modal command-trigger + action dispatch (6h₁₀e §C)` |
| 4 | §D | `feat(tui): breadcrumb dedicated chrome row (6h₁₀e §D)` |
| 5 | §E | `feat(tui): agent-metric composed metric row (6h₁₀e §E)` |
| 6 | §F | `test(tui): fixture extension + real-harness descriptor QA (6h₁₀e §F)` |
| 7 | §G | `docs: ADR-0108 TUI Tier-2 interactivity closure (6h₁₀e §G)` |
Trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Spec stays local in `.omc/`.
