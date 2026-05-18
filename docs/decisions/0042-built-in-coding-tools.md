# ADR-0042 — Built-in Coding Tools + 3 Event Emit Sites + Minimal CLI Loop

Status: **Accepted** (Sprint 5b shipped, 2026-05-17)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## 1. 1st-principle invariant

Aelix Phase 3.2 is a strict Pi-parity superset of Pi's `coding-agent` tool
catalog. Every Pi-verified built-in tool ships in Aelix with the same name,
input schema, and execution semantics.

## 2. Catalogue (P-32, P-33, P-36)

| # | Pi source (SHA `734e08e`) | Aelix module | execution_mode |
|---|---|---|---|
| 1 | `core/tools/bash.ts` | `aelix_coding_agent/tools/bash.py` | sequential |
| 2 | `core/tools/read.ts` | `aelix_coding_agent/tools/read.py` | parallel |
| 3 | `core/tools/edit.ts` | `aelix_coding_agent/tools/edit.py` | sequential |
| 4 | `core/tools/write.ts` | `aelix_coding_agent/tools/write.py` | sequential |
| 5 | `core/tools/grep.ts` | `aelix_coding_agent/tools/grep.py` | parallel |
| 6 | `core/tools/find.ts` | `aelix_coding_agent/tools/find.py` | parallel |
| 7 | `core/tools/ls.ts` | `aelix_coding_agent/tools/ls.py` | parallel |

Factories (`aelix_coding_agent.tools`):

- `create_coding_tools(cwd, options?)` → `[read, bash, edit, write]`
- `create_read_only_tools(cwd, options?)` → `[read, grep, find, ls]`
- `create_all_tools(cwd, options?)` → `{name: AgentTool}` for all 7

Each tool exposes an `XxxOperations` Protocol (Pi parity SSH-swap surface)
that callers may override via `options.operations`.

## 3. 3 event emit sites (P-34 correction)

- `input` — `AgentHarness.prompt()` head, BEFORE `before_agent_start`. Pi
  parity `agent-session.ts:984-1001`. ``InputHandled`` short-circuits the
  turn; ``InputTransform`` mutates text + images.
- `user_bash` — minimal CLI at `cli/repl.py`. Pi parity
  `interactive-mode.ts:5403-5410`. `!cmd` includes output in context;
  `!!cmd` excludes.
- `resources_discover` — `AgentHarness.discover_resources()` (startup) +
  `AgentHarness.reload_resources()` (reload). Pi parity
  `agent-session.ts:2054-2076`. Gated by `has_handlers("resources_discover")`.

## 4. Minimal CLI loop

`packages/aelix-coding-agent/src/aelix_coding_agent/cli/repl.py` ships a
small `input()` + stdout REPL exercising `!/!!` parser + `/reload` +
`/quit`. Full TUI (Pi `interactive-mode.ts`, 5528 LOC) is **Phase 5**
(ADR-0033 successor).

## 5. Aelix-additive `coding_tools_extension(cwd)`

`aelix_coding_agent.builtin.coding_tools.coding_tools_extension(cwd)`
returns a single `Extension` registering all 7 tools via `ExtensionAPI`.
**Aelix-additive divergence**: Pi callers always pass tools via
`AgentHarnessOptions.tools`; this wrapper is documented as a convenience.

## 6. ExtensionCommandContext partial (P-35)

Sprint 5b lands 4 of 6:

- `wait_for_idle` → `AgentHarness.wait_for_idle`
- `fork` → `JsonlSessionRepo.fork`
- `navigate_tree` → `AgentHarness.navigate_tree`
- `reload` → `AgentHarness.reload_resources`

Deferred to Phase 5: `new_session` / `switch_session` (need
`SessionManager.replaceSession` plumbing).

## 7. Deferred allowlist

Empty for Phase 3.2. Phase 4 (provider chain triple) inherits.

## 8. Forward-compat clause

Mirrors ADR-0041 §"Forward-compat": when Phase 4/5 lands a deferred
binding, the closure pin `tests/pi_parity/test_phase_3_2_strict_superset.py`
MUST be updated in the same PR.
