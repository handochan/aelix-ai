# 0102. Sprint 6h₉e — Subprocess Hooks (Tier 4b)

Status: Accepted (Sprint 6h₉e / Phase 5b-foundation / W6 shipped)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₉e is the **fifth sprint of Phase 5b-foundation**. Sprint 6h₉a
(ADR-0098) shipped the `aelix-plugin.toml` v1 manifest contracts including
`HookContrib` (`event` / `command` / `timeout_ms`) as a **declaration-only**
contract; its `event` field carried the comment *"validated downstream Sprint
6h₉e"*. Sprint 6h₉d (ADR-0101) shipped the Tier 4a MCP client and explicitly
deferred subprocess hooks (Tier 4b) to this sprint.

Sprint 6h₉e adds the runtime that consumes `[[contributes.hooks]]`: each
declared hook registers an in-process handler on the existing `HookBus` that,
when its event fires, spawns the declared shell command, passes a
Claude-Code-style JSON envelope on stdin, and maps the command's stdout-JSON /
stderr / exit-code back to the matching Aelix hook result type — under a
declarative `shell_exec` trust gate.

This is **Aelix-additive**, not a Pi port — see *Aelix-additive
characterization* below.

## Decision

Sprint 6h₉e ships four atomic commits (plus one review fold-in):

1. **`subprocess_hooks.py`** (`aelix_coding_agent.extensions`) — the dispatch
   core: `SubprocessHookError`, `HookSubprocessOutcome`, the
   `SUBPROCESS_HOOK_EVENTS` allowlist (8 events) with an import-time
   `SUBPROCESS_HOOK_EVENTS <= set(HOOK_RESULT_TYPES)` invariant,
   `run_hook_subprocess` (shell-form spawn + timeout teardown),
   `serialize_hook_event` (stdin envelope), `parse_hook_output` (stdout/exit
   mapping), `make_subprocess_handler` (never-raises handler factory), and
   `validate_subprocess_hook_event`.
2. **Loader wiring** (`extensions/loader.py`) — `_invoke_factory` gains the
   `shell_exec` trust gate + per-`HookContrib` event validation + `api.on(...)`
   registration with `error_mode="continue"`; `_resolve_factory` gains
   **hooks-only plugin support** via a module-level `_noop_factory` (a manifest
   declaring `[[contributes.hooks]]` but no `[plugin.entry] python` now loads —
   previously rejected, see *Hooks-only plugin support*).
3. **`manifest.py`** — `HookContrib.event` comment only: dropped *"validated
   downstream Sprint 6h₉e"*, now points here. **No schema field change** —
   the contracts JSON Schema `--check` stays exit 0.
4. **Tests + this ADR** — 27 tests in `tests/subprocess_hooks/` over real
   `/bin/sh` hook scripts (spawn / timeout / exit-code / JSON-parse / serialize
   / validate / trust-gate / hooks-only-load / `AELIX_PROJECT_DIR` injection /
   handler fail-open / e2e reducer composition).

The subprocess lane is a **second, separate lane** layered on top of the
in-process hook bus via a normal `api.on(...)` registration. It does **not**
modify `HookBus`, `_REDUCERS`, `HOOK_RESULT_TYPES`, or any `core.py` emit
site — confirmed byte-unchanged by W5 `git diff`.

## Aelix-additive characterization

Sprint 6h₉e is **entirely Aelix-additive**. W0 verified that
`earendil-works/pi@734e08e` has **no subprocess hook lane** — Pi extensions are
in-process TypeScript callbacks on `AgentHarnessEvent`/`ExtensionRunner`, with
no shell-out-on-event mechanism in core. There is therefore **no Pi-parity
citation table** for this sprint. The reference standard is the **Claude Code
hook system** (code.claude.com/docs/en/hooks). Pi parity is unaffected: this
sprint imports **zero** Pi behavior, and the Pi-parity in-process `HookBus`
(ADR-0017) is byte-unchanged.

ADR-0094 §"Tier 4" pre-authorized this: Tier 4 elevates the universal
Claude-Code-compatible extension surface (MCP servers + subprocess hooks) to a
formal Aelix tier with no Pi-core equivalent.

## Wire protocol (Claude Code reference)

**stdin envelope — snake_case (Aelix WRITES it):** common keys
`hook_event_name`, `session_id`, `cwd`; event-specific keys per the table in
*Serialization* below. Casing is load-bearing.

**stdout control JSON — camelCase (Aelix READS it):** `continue`,
`stopReason`, `suppressOutput`, `systemMessage`, top-level `decision` /
`reason`, and `hookSpecificOutput.{hookEventName, permissionDecision,
permissionDecisionReason, additionalContext}`.

**Exit codes:**
- `0` → parse stdout JSON for the control object.
- `2` → **blocking** (stdout ignored; stderr is the block reason).
- other non-zero → **non-blocking error** (logged; execution continues).

**Fail-open vs fail-closed boundary (matches Claude Code):** spawn failure,
**timeout**, invalid JSON, non-dict JSON, and non-`{0,2}` exit codes are all
**non-blocking** (handler returns `None` = allow). The ONLY fail-closed paths
are an explicit `exit 2` or `permissionDecision: "deny"` / `decision: "block"`
on a `tool_call` event. Verified against the live Claude Code hook spec (W5):
CC treats hook timeouts and exit-1 as non-blocking; only exit-2 and explicit
deny decisions block.

### Serialization (`serialize_hook_event`)

| event.type | event-specific stdin keys |
|---|---|
| `tool_call` (+ 8 typed `*ToolCallHookEvent` variants) | `tool_name`, `tool_use_id`, `tool_input` (= `event.args`) |
| `tool_result` | `tool_name`, `tool_use_id`, `tool_input`, `is_error` |
| `input` | `prompt` (= `event.text`), `source` |
| `user_bash` | `command`, `cwd`, `exclude_from_context` |
| `session_start` | `reason`, `previous_session_file` |
| `session_shutdown` | `reason`, `target_session_file` |
| `before_agent_start` | `prompt`, `system_prompt` |
| `agent_end` | (common only) |

Dispatch is by `event.type` (a string), NOT `isinstance` — so the 8 typed
`*ToolCallHookEvent` variants (`bash`/`read`/`edit`/`write`/`grep`/`find`/`ls`
+ `Custom`) all route through the `tool_call` branch. The caller serializes via
`json.dumps(payload, default=str)`, so a non-JSON-serializable arg degrades to
its `str()` rather than raising.

### Output mapping (`parse_hook_output`)

Only `tool_call` is **actionable** in v1: `exit 2` →
`ToolCallResult(block=True, reason=stderr)`; or `exit 0` with
`hookSpecificOutput.permissionDecision == "deny"` or top-level
`decision == "block"` → `ToolCallResult(block=True, reason=…)`. Every other
event and every other decision (`allow`/`ask`, `additionalContext`, …) is
**observational** in v1 (the subprocess runs, the result is logged, the handler
returns `None`). The widening path is documented under *Deferred items*.

## Event allowlist

`SUBPROCESS_HOOK_EVENTS` (8 of the 35 ADR-0017 events) bounds which events a
subprocess hook may bind to:

```
before_agent_start  input  tool_call  tool_result
user_bash  session_start  session_shutdown  agent_end
```

Two reasons: (1) these are the clean Claude-Code analogs (UserPromptSubmit /
PreToolUse / PostToolUse / SessionStart / SessionEnd / Stop); (2) it prevents a
**performance footgun** — binding a subprocess to a high-frequency streaming
event (`message_update`, `tool_execution_update`) would spawn a process per
update. Claude Code has no equivalent allowlist because its event set already
excludes streaming projections. `validate_subprocess_hook_event` rejects both
events unknown to `HOOK_RESULT_TYPES` AND known-but-non-allowlisted events
(e.g. `message_update`), each with a distinct `ExtensionManifestError`.

## Trust gate

The v1 gate is **declarative**: a manifest declaring `[[contributes.hooks]]`
MUST set `capabilities.shell_exec = true`. `Capabilities.shell_exec` defaults
to `False`, so the gate is **fail-secure** — an omitted capability rejects the
plugin. The gate in `_invoke_factory` raises `ExtensionManifestError` **before**
any `api.on(...)` wiring, contained by the loader's per-entry `try/except` and
surfaced as an `ExtensionLoadError` (not a crash). Subprocess *spawning* only
happens later at event-emit time, long after the gate; **registration** is the
choke point and it is gated.

An **interactive / runtime workspace-trust prompt** ("trust this workspace's
hooks?") is deferred to **Phase 5c-tui** — there is no UI in Phase 5b-foundation.

## Hooks-only plugin support

Sprint 6h₉a's manifest validator
(`validate_entry_python_required_for_python_capabilities`) requires
`entry.python` only for `ui_tui_trusted` / `ui_descriptor` / `mcp_serve` — NOT
for hooks. But `_resolve_factory` (pre-6h₉e) rejected *every* `_ManifestEntry`
with `entry.python is None`. That inconsistency made a pure-shell plugin (the
defining Tier 4b / Claude-Code shape: hooks + `shell_exec`, no Python code)
unloadable. Sprint 6h₉e closes it: a `_ManifestEntry` with no `entry.python`
but a non-empty `contributes.hooks` now resolves to a module-level
`_noop_factory`, so `_invoke_factory` still builds the `Extension` (with the
manifest attached) and wires the subprocess hooks. A manifest with neither
`entry.python` nor hooks is still rejected.

## Intentional Aelix-vs-Claude-Code divergences

1. **Env var name** — Aelix sets `AELIX_PROJECT_DIR` (when `cwd` is set), not
   CC's `$CLAUDE_PROJECT_DIR`; an Aelix-additive analog. CC's
   `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA` are not set in v1 (deferred).
2. **Declaration surface** — hooks are declared in the plugin manifest
   `[[contributes.hooks]]`, not a `settings.json` `hooks` block. No
   matcher / regex / `if`-filter layer; the `HookContrib.event` name is the
   sole selector.
3. **Event-name vocabulary** — Aelix uses ADR-0017 snake_case event names
   (`tool_call`, `tool_result`, `before_agent_start`, `input`, `user_bash`,
   `session_start`, `session_shutdown`, `agent_end`), not CC PascalCase
   (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, …). The envelope's
   `hook_event_name` carries the Aelix snake_case value.
4. **Event allowlist** — 8 events of 35, to avoid the per-update spawn footgun.
   CC has no equivalent concept.
5. **Hook type** — v1 supports only `type: "command"` (shell `sh -c` form, via
   `asyncio.create_subprocess_shell` since `HookContrib.command` is a single
   string). CC additionally supports `http` / `mcp_tool` / `prompt` / `agent`
   hooks — all deferred.
6. **Actionable scope** — v1 acts ONLY on `tool_call` blocking. All other
   events and all of `allow`/`ask`, `updatedInput`/`updatedToolOutput`,
   `additionalContext` are observational. CC acts on far more.
7. **`session_id` semantics** — Aelix emits the session *file path* (or `""`)
   under `session_id`; CC's `session_id` is an opaque id with a separate
   `transcript_path`. Best-effort, documented limitation.
8. **`tool_result` payload** — Aelix sends `tool_input` (= args) + `is_error`;
   it does NOT send CC's `tool_response` (tool output). Observational-only in
   v1, so a PostToolUse-analog hook cannot yet read the result content.
9. **Per-tool matching** — `HookContrib` has no matcher field, so a `tool_call`
   hook fires for ALL tool calls. Tool-name matching is a deferred
   `HookContrib` v2 field.
10. **Handler attribution** — the W1 spec prescribed `api.on(source=…)`, but
    the runtime `ExtensionAPI.on` (`api.py`) has no `source` kwarg (only
    `HookBus.on` does). The wiring omits it; attribution is preserved because
    the harness sets `source = extension.name` (= plugin id) automatically when
    it wires every extension handler into the `HookBus` (`core.py`).

## Citation accuracy notes

- **CC stdout fields** — PreToolUse rewrites tool args via `updatedInput`;
  PostToolUse can replace output via `updatedToolOutput` and inject context via
  `additionalContext`. v1 code acts on **none** of these (observational), so the
  distinction has zero code impact; recorded here for accuracy.
- **10k stdout cap** — `run_hook_subprocess` caps captured stdout at 10,000
  chars before JSON parse. This aligns with Claude Code's documented ~10k
  hook-output limit and is applied as an Aelix safety bound; the code comment
  was softened from an unqualified "CC parity" claim accordingly.
- **ADR-0033** — confirmed (W5) to be a phantom slot: no `0033-*.md` file
  exists; ADR-0102 is the correct next number after ADR-0101. (The README
  planning tables still list a 0033 *draft* row for the long-deferred
  ExtensionContext UI surface, which ADR-0100 actually closed — a pre-existing
  index artifact, out of scope here.)

## Deferred items

| Item | Owner | Reason |
|---|---|---|
| Interactive workspace-trust prompt | Phase 5c-tui | no UI in foundation |
| Actionable `tool_result` / `input` / `additionalContext` / `updatedInput` | Phase 5c+ | v1 = `tool_call` block only |
| `http` / `mcp_tool` / `prompt` / `agent` hook types | Phase 6 | command-form first |
| Per-`tool_name` matcher on `HookContrib` | manifest v1.1 | non-breaking field add |
| `tool_response` in `tool_result` envelope | Phase 5c+ | needs result payload plumbing |
| `session_id` as opaque id + separate `transcript_path` | Phase 5c+ | best-effort file path in v1 |
| `CLAUDE_PLUGIN_ROOT` analog env vars | Phase 6 | plugin packaging surface |

## References

### Reference map (NOT Pi — Tier 4 is Aelix-additive)

| Reference | Use |
|---|---|
| Claude Code hooks (code.claude.com/docs/en/hooks) | event set, stdin/stdout JSON shapes, exit-code 0/2/other semantics, fail-open boundary, ~10k output cap |
| `aelix_coding_agent.rpc.rpc_client` (`start`/`stop`) | subprocess spawn + SIGTERM→wait→SIGKILL→bounded-wait teardown pattern reused for the timeout path |
| `asyncio.create_subprocess_shell` + `communicate(input=…)` under `wait_for` | shell-form (`sh -c`) dispatch with stdin payload + bounded timeout |

### ADR cross-references

- **ADR-0094** — Aelix 4-tier extension architecture (Tier 4b = subprocess
  hooks, Aelix-additive characterization).
- **ADR-0096** — manifest v1 schema (`HookContrib` / `Capabilities.shell_exec`).
- **ADR-0098** — Sprint 6h₉a (`HookContrib` declaration-only contract source).
- **ADR-0099** — Sprint 6h₉b manifest loader integration (`_resolve_factory` /
  `_invoke_factory` extended here).
- **ADR-0017** — in-process hook event catalogue (the 35-name `HOOK_RESULT_TYPES`
  registry the allowlist subsets; byte-unchanged this sprint).
- **ADR-0019** — per-handler `error_mode` (`"continue"` used for the
  fail-open subprocess registration).
- **ADR-0101** — Sprint 6h₉d MCP client (Tier 4a; deferred Tier 4b here).

Pi pin `734e08edf82ff315bc3d96472a6ebfa69a1d8016` held — no Pi source consulted
or imported.

## Verification

| Gate | Result |
|---|---|
| `ruff check` | clean |
| `uv run pyright` | 8 baseline preserved (zero new errors) |
| `uv run pytest` | 2524 passed, 1 skipped (was 2497 + 1; +27 new) |
| `python scripts/generate_contracts_schemas.py --check` | exit 0 (`HookContrib` schema unchanged) |
| Pi-parity non-regression | `harness/hooks.py` / `harness/core.py` / reducers / `HOOK_RESULT_TYPES` byte-unchanged (W5 `git diff`) |
| orphan processes | none — timeout teardown reaps the child (terminate→wait→kill→bounded-wait); the benign `PytestUnraisableExceptionWarning: Event loop is closed` is the known CPython asyncio subprocess `__del__` artifact, not a leak |
| trust gate | fail-secure — `shell_exec` defaults `False`; gate raises before any wiring |

## Phase

Sprint 6h₉e / Phase 5b-foundation (shipped). Next: **Sprint 6h₉f — aelix-server**
(FastAPI HTTP+WS gateway) — the final Phase 5b-foundation sprint. ⚠️ Per
ADR-0101 §"Same-task requirement", 6h₉f MUST use anyio task groups if it manages
MCP connection lifecycles across tasks.
