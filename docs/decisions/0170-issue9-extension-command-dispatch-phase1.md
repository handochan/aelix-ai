# ADR-0170 — Issue #9 Phase 1: executable extension /commands via CommandDispatchService

- **Status:** Accepted
- **Date:** 2026-06-23
- **Sprint:** Issue #9 (architecture study → Phase 1)
- **Relates:** ADR-0069 (skills loader + ExtensionRunner aggregation), ADR-0042 (ExtensionCommandContext), ADR-0110 (built-in command core), ADR-0143 (extensions-api / protected-core budget). Backlog: GitHub #9 (parent #5).
- **Supersedes the overclaim** in `docs/guides/extension-authoring.md` ("`/hello` becomes available") which was false until this change.

## Context

Extensions can `register_command(name, handler=…)`, and `ExtensionRunner.get_registered_commands()`
disambiguates + lists them — but nothing ever **executed** one. The TUI built its command vocabulary
from `BUILTIN_COMMANDS` only, so a typed `/hello` hit "Unknown command"; RPC only *listed* commands.
The shipped echo example and the authoring guide both overclaimed that `/hello` worked.

A dedicated architecture study (3-research → 3-design → synthesis) chose, against the user's stated
priority (*optimize for future extension development; TUI + Web/RPC both first-class; ignore work-size*),
a **single execution authority** every surface routes through — mirroring pi, where command execution
is centralized in `AgentSession.prompt → _tryExecuteExtensionCommand` (mode-agnostic), not in the TUI.

Pivotal finding: `ExtensionCommandContext` was **already fully built** (`extensions/command_context.py`,
6 lifecycle methods delegating to `AgentSessionRuntime`) — its docstring even says "Constructed by the
CLI command dispatcher", which did not exist. So the missing pieces were just a dispatcher + a way to
*construct* that context.

## Decision

Phase 1 ships TUI execution + autocomplete + the docs fix. RPC/print execution is **Phase 2**;
descriptor rich-output, `register_shortcut`/`register_message_renderer` dispatch, the
steer/follow-up guard, and collision diagnostics are **Phase 3**; multi-agent is **Phase 4**.

### Coding-agent: `CommandDispatchService` (`extensions/command_dispatch.py`, new)
The surface-agnostic executor — the lowest common ancestor of the TUI input loop, `rpc_mode`, and
`print_mode` (all already hold the runtime host + `harness.extension_runner` + a bound UI). It owns
ALL of: name/args split, `get_command` lookup, context construction, handler invocation, error
routing, and a tri-state result — so the surfaces can never drift. Pi semantics replicated:

- name/args split on the **first space**; `args` is the **raw** remainder (pi `slice(spaceIndex+1)`).
- handler called `handler(args, ctx)`; pi **ignores** the return.
- a thrown handler is **caught + reported via `emit_error`** and STILL counts as `HANDLED` (it never
  falls through to the model).
- a lookup **miss** → `NOT_A_COMMAND` (the caller falls through to the model / "unknown command").

`CommandSurfaceBindings(emit_text, emit_error)` is the per-surface output seam; `ctx.ui.*` is the
separately-bound, handler-driven channel.

**Intentional Aelix divergences:** (1) a non-empty **str** return is rendered via
`bindings.emit_text` (TUI → scrollback) — NOT `ctx.ui.notify`, which is a 3-second transient toast and
would flash a command's output away; the shipped echo example returns a greeting, so the shim keeps it
visible. (2) The dispatcher lives in the coding-agent layer, not the harness (pi runs it inside
`session.prompt`) — aelix deliberately keeps slash-handling out of the harness.

### Protected core: two small additive edits
1. `ExtensionRunner.get_command(invocation_name)` (`_extension_runner.py`) — pi `getCommand`; matches
   the disambiguated `invocation_name` (so the `{name}:N` collision suffix is honored).
2. `AgentHarness.make_command_context(*, repo, session_runtime)` (`core.py`) — the Pi
   `createCommandContext` construction site. `_make_context` was refactored to extract
   `_make_context_kwargs()`, so the hook context and the command context share **one** closure
   assembly (no drift). Construction lives here because the closures (`_action_get_active_tools`,
   `_mark_abort`, …) are harness internals; the harness already imports the coding-agent
   `ExtensionContext` at runtime, so importing `ExtensionCommandContext` the same way is no new
   layering. The bound UI flows through `self._runtime.ui` (the surface's `bind_ui` target).

### TUI wiring (`tui/shell.py`, `tui/completion.py`)
`run_tui` constructs the service with a **live** harness provider
(`lambda: runtime_host.harness`) so it survives `/resume`·`/new`·`/fork` rebinds, plus
`repo`/`session_runtime` for the handler's `ctx.fork`/`new_session`/`switch_session`. The input loop
calls `dispatch.try_execute` **after** the built-in `match_command` miss and **before** the
descriptor-modal / unknown-command fallback — so a built-in always wins a name collision (pi parity)
and only `NOT_A_COMMAND` falls through. `DescriptorCommandCompleter` gains a `get_ext_commands` source
(yielded after built-ins + descriptor routes, deduped via `seen`).

## Verification

- Full gate: **4087 passed, 1 skipped** (from 4062; +25). New tests: dispatch service (split / args /
  str-shim / async / throw-handled / miss-fallthrough / missing-ctx / repo-threading / list); runner
  `get_command` (by-name / miss / collision suffix); real `make_command_context` (type / 6 methods /
  bound-ui / repo+runtime threading); completer (ext offered / built-in-wins / descriptor-wins /
  absent-when-unset); run_tui end-to-end (runs-not-prompts with raw args / built-in-wins / throw
  survives). The `_make_context_kwargs` refactor caused **zero** regression across every hook-emit test.
- A 4-lens adversarial review (correctness / pi-parity / security-robustness / tests) with per-finding
  adversarial verification confirmed **3 findings, all fixed before commit**: (MEDIUM) after a
  `/resume`·`/new`·`/fork` swap the new harness's runtime defaulted to the **headless** UI, so a
  command's `ctx.ui.*` silently failed — `_rebind` now re-binds the live TUI ui onto the new runtime
  (this also fixes hooks/descriptors going headless post-swap); (LOW) `try_execute`'s resolution phase
  ran outside the guard, so a faulty registry could escape into the un-guarded input loop — the
  resolution is now wrapped too, and `try_execute` is contractually **never-raises**; (NIT) the
  str-return shim is capped at 100KB. Each fix has a regression test.

## Consequences

- Extension `/commands` now execute in the TUI with a real `ExtensionCommandContext` (UI prompts via
  `ctx.ui`, session control via `ctx.fork`/`new_session`/…). The authoring guide now matches reality.
- The `CommandDispatchService` is the reusable seam Phase 2 (RPC `run_command`) and print mode plug
  into — no logic re-implementation.
- Protected-core surface grew by exactly two additive methods; no existing behavior changed (the
  `_make_context` refactor is behavior-preserving).
