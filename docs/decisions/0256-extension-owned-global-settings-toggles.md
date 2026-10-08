# ADR-0256: Extension-owned global settings toggles

Status: Accepted
Date: 2026-10-08
Issue: [#403](https://github.com/handochan/aelix-ai/issues/403)

## Context

Aelix Memory needs one persistent global ON/OFF choice in `/settings`. The menu
previously enumerated only built-in `SettingsManager` fields. A memory-specific
host branch would couple the generic coding agent to an optional extension.
Pi's public extension types at
[`1cedd32724abfcb0915f76cc61b6827e2c16dbad`](https://github.com/earendil-works/pi/blob/1cedd32724abfcb0915f76cc61b6827e2c16dbad/packages/coding-agent/src/core/extensions/types.ts#L1744)
expose `getSettings`, but no setting-row registration surface. Under ADR-0235,
this is an intentional Aelix addition.

## Decision

`ExtensionAPI.register_setting(name, *, label, get_value, set_value, description="")`
MUST register one extension-owned global boolean contribution. The getter MUST
return bool and SHOULD be read-only. The synchronous or async setter MUST persist
the user's value before it completes. Registration MUST call neither callback.
The extension owns storage and cross-process consistency; core has no memory import.

The generic runner aggregates contributions under `extension:<owner>:<name>` using
its existing duck-typed boundary. The TUI MUST query its current runner each time
`/settings` opens/reopens, read the owner value, await a selected setter and confirm
the new value by rereading. It MUST keep built-in `SettingsManager` paths unchanged.
Duplicate owner/name keys use the runner's first-registration rule. Display labels
MUST be unique, including user labels equal to a generated qualified label.

Callbacks MUST reject stale runtimes before invoking the owner, including an async
callback created before invalidation and awaited afterward. The wrapper also checks
after an awaited owner completes. It cannot undo an operation already running;
extensions MUST supply any transactional/cancellation guards they need. Failures
MUST remain isolated to that row and use generic UI diagnostics rather than expose
owner exception text. Control characters in metadata are rejected at registration.

No headless menu/RPC setting mutation endpoint is added. This API supplies only
boolean rows; richer setting types and a persistence convention can follow actual
extension requirements. `register_flag` retains its existing CLI flag semantics.

The generic aggregation method authorizes a bounded change to
`packages/aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py` under
the kernel change gate. It introduces no coding-agent/memory import, owner callback
execution, persistence or consent policy and no subagent runtime surface. The
always-armed kernel independence checks remain enforced.

## Verification

`tests/extensions/test_settings_contributions.py` covers lazy registration, sync/
async persistence, disk rereading, stale/replaced/removed contributions, exact bool
validation, callback errors and adversarial label collisions. The async guard and
wrong-owner dispatch regressions both failed before their repairs.
`tests/tui/test_run_tui_extension_settings.py` drives the real `/settings` modal and
key pipeline through two fresh TUI instances with a persistent synthetic owner.
Actual terminal and installed Aelix Memory wheel verification are recorded separately.
