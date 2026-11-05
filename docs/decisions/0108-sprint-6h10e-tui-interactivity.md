# 0108. Sprint 6h₁₀e — TUI Tier-2 Interactivity Completion (Phase 5c-tui backlog)

Status: Accepted (Sprint 6h₁₀e / Phase 5c-tui backlog / W5 shipped)
Date: 2026-05-26
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — consumer-only)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

6h₁₀c rendered the 8 descriptor kinds; 6h₁₀d added pyte snapshots + command-route autocomplete.
Both deliberately deferred the **interactivity** (ADR-0106/0107 deferred lists): the
`ActionDescriptor` reverse-channel, management-modal triggering + action dispatch, live
tool-result interception, and the breadcrumb/agent-metric dedicated regions. W1 architect analysis
confirmed **all of it is pure-consumer** — the `ActionDescriptor` wire format + the `plugin_action`
reverse-channel intent + the `tool_name`/`command` discriminators are already in the byte-frozen
contract, and the `EventBus` accepts arbitrary channel strings. 6h₁₀e completes the achievable
no-contract subset.

## The decisions

### §A ActionDescriptor reverse-channel (keystone)
`DescriptorRenderer.dispatch_action(action)`: when `action.confirm` is set + a confirm callable is
wired, `self._spawn` an async path that awaits the existing `ctx.confirm` dialog and emits only on
accept; otherwise emit synchronously. The wire payload is `action.model_dump(mode="json")` on
`event_bus.emit("plugin_action", …)` (ADR-0095:108-112). Plugins listen via
`api.events.on("plugin_action", …)`. Emit/confirm failures are contained + logged (the EventBus
already swallows handler exceptions; this guards the emit call + `model_dump` itself). `run_tui`
injects `event_bus` + `context.confirm` into the renderer.

### §C management-modal command-trigger + action dispatch
In `_input_loop`, a submitted `prompt`-kind `/<command>` line is matched (shell-side, against the
live `registry.by_kind("management-modal")` — `parse_input_line` stays PURE) and, on a hit, opens
the modal via `open_modal` instead of sending to the model. **`open_modal` wires the modal's
`actions`**: `_build_modal_keybindings` binds number keys `1`–`9` each to `dispatch_action(action)`
+ close (Esc/Ctrl-C close without dispatching), and `_render_action_hints` renders a `[1] <action>`
hint line — so the §A reverse-channel is reachable from the live modal. Builtins (`/quit`//exit//
reload) short-circuit first; an unknown `/foo` (no matching modal) falls through to the model
unchanged (qa-verified — no prompt regression).

*2026-11-05 note (#671):* "reachable from the live modal" was not true. None of the modal's
windows could take focus, so `show_modal`'s focus raised, `AelixChrome.focus` swallowed it, and
the keys stayed with the input line the modal hides: no binding above ever ran, the modal could
not be closed, and what was typed under it went to the model on Enter (the tests called the
handlers directly). The content window is focusable now, and Enter is taken and does nothing, as
in the confirm dialog. A number key closes the modal with its action, and `open_modal` dispatches
it once the modal is down: an action with `confirm` opens its dialog in the same single slot
(ADR-0159), and the closing modal's unmount would take that dialog down with it. `AelixChrome.focus`
logs a failure (at debug: nothing configures logging, and a warning was written over the TUI's
own drawing) and says whether it took, and `show_modal` refuses (`ValueError`) a capturing
modal whose content has no window that can take focus, instead of waiting on a result no key
can set — checked before it is mounted, so the modal already open in the slot stays as it was
(it was replaced first, and a confirm under it was left with nothing to answer it). The hint
line ends in `[Esc] close`, with or without actions (the owner's call: every pi dialog shows its
cancel key), and breaks between hints at the terminal's width. The chrome's own bindings are
the Application's, so they reached the input under any capturing modal for a key the modal did
not take (and beat a modal's `<any>`): Alt+Enter queued the hidden line for the model, Ctrl+D
quit, Tab and Ctrl+Space completed in it, Alt+Up, Ctrl+G and Ctrl+V filled it, a paste went in
as the input's placeholder, and under a modal without its own Enter or Ctrl+C those submitted,
cleared or quit. Every one that reads or edits the input, submits, queues or quits is off while
a capturing modal is open (Alt+Enter and Alt+Up are taken and dropped: unbound, the pair would
run as an Esc that closes the modal), except Ctrl+C. Esc, Ctrl+T and Ctrl+C stay, and a modal
that takes the key wins: Esc and Ctrl+T touch no input, a running turn's Ctrl+C stops it, and an
idle one arms and then quits as over an empty input, whatever the hidden input holds — it is
neither read nor cleared (fix round 2, verify S1: gated off, a `custom()` overlay opened while
idle, which takes no key, left no way out; Ctrl+C twice, Ctrl+D, Esc and typing did nothing, and
aelix had to be killed, where 6aed3863 quit on Ctrl+C twice). pi goes further — every key goes
to the focused component — but a `custom()` overlay here takes no key at all, and Esc or Ctrl+C
is how a turn under it is stopped, Ctrl+C twice how aelix is left.

### §B live tool-result interception
`EventRenderer` gains a late-bound `get_tool_renderer_desc` lookup (reads the live registry by
reference, mirroring the command-routes pattern). On `tool_execution_end`, a stored
`tool-renderer-desc` matching `event.tool_name` renders the custom view
(`build_tool_renderable`) into scrollback; **any miss / lookup / build / parse failure falls through
to the unchanged default Text dump** (no regression to normal tool rendering). A minimal
`project_tool_result` projects the `ToolResult` content into rows: JSON-parse for table/grid/form,
raw text for `text`, honoring `rows_path`/`text_path` as a simple **dotted-key** lookup only — full
JSONPath DEFERRED.

### §D breadcrumb dedicated row
A new `chrome._breadcrumb_line` + `set_breadcrumb_line` + a gated ConditionalContainer row (cloning
the header-row CPR/non-empty gate pattern), freeing the `set_header` factory line from the prior
collision. `_recompose_breadcrumbs` targets the new setter.

### §E agent-metric metric ROW (not a sidebar)
All `agent-metric` descriptors compose into ONE Rich `Columns` strip (`label: value (delta)`, level
color) in a single widget slot; removal recomposes (clears the slot when none remain). **A true
VSplit sidebar (ADR-0095:186) conflicts with the inline `full_screen=False` chrome** (ADR-0105) —
deliberate TUI divergence; the Web surface (Phase 6) renders the true `slot:agent-metric` sidebar.

### §F fixture extension + real-harness QA
A Tier-1 fixture `setup(api)` subscribes to `ui:list-modules` and emits command-route + status-item
+ toast + management-modal; a test builds a REAL `AgentHarness(options.extensions=[fixture])` and
drives `run_tui` headlessly, asserting the live probe→render→completer→modal path. This proves the
end-to-end path without enabling extension loading in the shipped CLI (see deferred).

## Consequences
- The descriptor system is now fully interactive in the TUI consumer: extensions can drive modals +
  receive `plugin_action` callbacks, tool results get custom renderers, breadcrumbs/metrics have
  their own chrome regions. pyright holds the 8-error baseline; protected paths byte-unchanged.

### Deferred
- **`entry.py` extension loading in the shipped TUI** — the interactive CLI builds the harness with
  NO `extensions=`, so the descriptor system is reachable only via tests today. Wiring
  `discover_and_load_extensions` would make it live, BUT enabling Tier-1 (trusted Python) extension
  loading in the shipped agent is a **product/security decision** — deferred to a separate,
  explicitly-authorized change. (§F proves the path via a real-harness test.)
- `ctx.ui.invalidate_descriptors()` live re-probe — contract-touching (`ExtensionUIContext` Protocol
  + `AELIX_API_LEVEL`). Deferred.
- Full JSONPath `rows_path`/`text_path` (§B does dotted-key + raw fallback).
- NIT/LOW carry-forward (non-blocking, W4): `/ <command>` slash-space match leniency; theoretical
  double-render only if `_commit` itself raises (`put_nowait` makes this near-impossible).
- agent-metric true sidebar (Phase 6 Web `slot:agent-metric`).

## Verification (W4)
- Gate green: ruff clean; `uv run pyright` 8-baseline (0 new); **`uv run pytest` 2821 pass / 1 skip**
  (incl. modal-action dispatch + projection + breadcrumb/metric + §F real-harness tests); schema
  `--check` exit 0; protected paths byte-unchanged.
- **W4 code-reviewer (opus): APPROVE-WITH-NITS** (after a REQUEST-CHANGES→fix cycle: the §A↔§C
  keystone wiring gap was caught + fixed + re-ACK'd; LOW split-guard fixed). Verified input-routing
  has no prompt regression + tool-result fall-through is safe.
- **W4 qa-tester real-PTY: 4/4 PASS** — normal prompts stream, unknown `/slashcommands` still reach
  the model (no §C regression), bash + `/quit` intact.

Phase 5c-tui interactivity backlog complete. Next candidate: `entry.py` extension-loading (a
product/security decision) or Phase 6 (Web UI).
