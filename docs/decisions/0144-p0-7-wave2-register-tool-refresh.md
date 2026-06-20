# 0144. P0 #7 Wave 2 — `register_tool` Refresh (runtime action + harness binding)

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Gap-inventory **P0 #7 item 3**, Wave 2 of the recon's 3-wave split (Wave 1 = ADR-0143). Pi's
`registerTool` (`loader.ts:217-225`) calls `runtime.refreshTools()` after storing the tool so a tool
registered by an extension/hook becomes live + active. Aelix `register_tool` only stored the tool —
the harness tool registry was never rebuilt, so a dynamically-registered tool never reached the model.

## Decision

### `extensions/api.py` (unprotected)

- Add `refresh_tools: Callable[[], None]` to `ExtensionRuntimeActions`.
- `_default_actions()` seeds `refresh_tools=lambda: None` — a **NO-OP default**, NOT a `_make_throwing_stub`.
  This is the one place aelix's throwing-stub convention deliberately diverges, matching pi's pre-bind
  stub `refreshTools: () => {}` (`loader.ts:171`) so `register_tool` is valid during extension load
  before `bind_core` installs the real implementation. Documented at both sites.
- `register_tool` now runs in pi order (`loader.ts:217-225`): `assert_active()` (Wave 1) → store →
  `self._runtime.actions.refresh_tools()`.

### `harness/core.py` (PROTECTED — user-approved P0 #7 work; minimal diff)

- Bind `refresh_tools=self._refresh_extension_tools` in the `_RuntimeActions(...)` block.
- New `_refresh_extension_tools()` implements pi `_refreshToolRegistry` no-options path
  (`agent-session.ts:2238-2326`): snapshot previous registry names + previous active names
  (`_action_get_active_tools`, which materializes `None` ⇒ all registered); rebuild `_state.tools` via
  `_rebuild_tool_registry`; compute next active = previous active ∪ newly-registered (names absent
  before the rebuild — pi's `else if (!options?.activeToolNames)` auto-activate branch, which fires
  because `register_tool` passes no options); filter to names still in the registry (pi's
  `filter(isAllowedTool)` — aelix has no allowlist so all pass) with order-preserving dedup; assign
  `_state.active_tool_names` **directly** (NOT `_action_set_active_tools`/`set_tools`, whose validators
  raise on stale names mid-rebuild). pi always materializes the active list, so `None`→explicit-list is
  pi-faithful and observably identical (the `None` case already enumerated every tool in step 1).

## Recon correction (verified against pi)

The recon spec's item-3 test plan claimed "with an explicit `active_tool_names` filter, the new tool is
NOT auto-activated." That is **wrong** vs pi: `register_tool` calls `refreshTools()` with no options, so
newly-registered tools are added on top of the existing active set. A discriminating test
(`test_register_tool_refresh_auto_activates_over_explicit_filter`: filter `["a"]` over registry
`[a,b,c]` + `register_tool` → active `{a,c}`) proves the pi-correct behavior; the recon claim was not
followed.

## Known LOW divergence (non-observable)

After a refresh, `active_tool_names` is re-ordered into registry order (aelix sources previous active
from `_action_get_active_tools`, which iterates `_state.tools`), whereas pi preserves the stored active
array order. **Not observable to the model:** the per-turn `AgentContext.tools` is always built by
iterating `_state.tools` (registry order) filtered by the active *set* (membership, order-independent),
so the model-facing tool order is registry order regardless. `active_tool_names` functions as a pure
membership set in aelix's observable behavior.

## Verification

- Implemented + 4-lens adversarial review (pi-fidelity / correctness / scope / test-adequacy); 13
  findings, **0 confirmed non-LOW** (all pi-fidelity/correctness confirmations + the non-observable
  ordering note). Fix phase skipped (nothing actionable).
- Full gate: **3439 passed, 1 skipped** (+5 tests); only the 3 pre-existing `test_append_system_prompt`
  AGENTS.md cwd-coupling failures remain (unrelated). ruff clean.
- Diff confined to `extensions/api.py`, `harness/core.py`, and tests; the protected change is exactly
  the binding line + the one new method.
