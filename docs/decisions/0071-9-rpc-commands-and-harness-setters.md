# 0071. 9 RPC Commands + Harness Setters (Phase 4.9)

Status: Accepted (Sprint 6h₂ / Phase 4.9 / W6 shipped)
Date: 2026-05-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Sprint 6h₁ (ADR-0069/0070) closed Phase 4.8 with **13 supported / 16
deferred RPC commands**. Sprint 6h₂ ports the next 9 commands from
Pi `rpc-mode.ts` line range **483-547** at SHA `734e08e` —
`steer` / `follow_up` (queue paths with `images`),
`cycle_thinking_level`, `set_steering_mode` / `set_follow_up_mode`
(queue mode setters), `set_auto_compaction` / `set_auto_retry`
(auto-mode flags), `abort_retry` / `abort_bash` (best-effort
cancellation flags).

The W1 spec drafted line ranges as `528-635`. The Sprint 6h₂ W5 Pi
parity audit corrected this to `483-547` — the W1 grep was off by
~45 lines because the upstream file had since shifted at SHA
`734e08e`. **P-258 BLOCKING** records the correction and pins the
true line numbers in the W0 fixture
(`tests/pi_parity/fixtures/pi_rpc_9_commands_734e08e.json`).

## Decision

Wire **9** Pi `RpcCommand` discriminators through the harness with
the following architecture per W5 audit corrections:

### Harness surface additions (`harness/core.py`)

- `steer(text, *, images=None)` / `follow_up(text, *, images=None)` —
  keyword-only `images` parameter (W6 P-263 MAJOR). Existing
  positional `text` callers continue to work; the keyword-only
  marker future-proofs against silent typo bugs since this is the
  first sprint introducing the `images` argument.
- `cycle_thinking_level()` — Pi `agent-session.ts:1537-1548`
  `supportsThinking()` short-circuit (`!!this.model?.reasoning`).
  The prior Aelix `len(levels) <= 1` guard FAILED for a reasoning
  model with a degenerate `thinking_level_map` whose only non-null
  entry collapses `levels` to length 1 — Pi rotates via
  `(0+1)%1 == 0` and returns the single level, while Aelix silently
  returned `None`. **W6 P-254 BLOCKING** replaces the length guard
  with Pi's reasoning guard.
- `set_steering_mode(mode)` / `set_follow_up_mode(mode)` — runtime
  validation via `ValueError` (Pi narrows at TS compile time;
  documented as Aelix-additive defense in ADR-0072). W6 LOW-3
  switches the post-validation assignment to `typing.cast` instead
  of `# type: ignore`.
- `set_auto_compaction_enabled(enabled)` /
  `set_auto_retry_enabled(enabled)` — state-only setters; the
  retry-loop port itself defers to Sprint 6h₃ per ADR-0072.
- `abort_retry()` / `abort_bash()` — state-flag setters
  (`_state.retry_aborted` / `_state.bash_aborted`). The Pi retry
  loop + bash cancellation-token threading both defer to Sprint
  6h₃ per ADR-0072.
- `auto_compaction_enabled` / `auto_retry_enabled` — public
  properties symmetric with the setters. **W6 P-264 BLOCKING**
  adds `auto_retry_enabled` to surface the toggle through the
  RPC `get_state` wire shape.
- `_MessageQueue.set_mode(mode)` — defensive runtime check
  (**W6 P-265 BLOCKING**) so a buggy direct caller bypassing the
  harness setters trips fast rather than corrupting the queue
  dispatcher.

### State additions (`agent_core.types.AgentState`)

```python
auto_compaction_enabled: bool = True   # Pi default
auto_retry_enabled: bool = True        # Pi default
retry_aborted: bool = False            # toggled by abort_retry
bash_aborted: bool = False             # toggled by abort_bash
```

### Wire shape additions (`rpc/rpc_types.RpcSessionState`)

The `RpcSessionState` dataclass extended 12 → **13** fields by adding
`auto_retry_enabled` (default `True`). The Pi camelCase wire field is
`autoRetryEnabled`. **W6 P-264 BLOCKING** ensures the
`_handle_get_state` handler populates the field from the harness's
real source instead of leaving it absent.

### Strict `_decode_images` (W6 P-262 BLOCKING)

The Sprint 6h₂ W2 ship accepted both Pi camelCase `mimeType` and
Aelix snake_case `mime_type` and silently coerced missing fields to
empty strings. Pi `ImageContent` is strictly camelCase
(`coding-agent/src/core/agent-session.ts` TS narrow). The W6 fix
narrows to **camelCase only + required-field validation** — missing
`mimeType` / `data` fields raise `ValueError`, which the outer
dispatcher surfaces as a Pi-shape `RpcErrorResponse`. The
`ImageContent` import hoists to the module top.

### Handler dispatch (`rpc/rpc_mode.py`)

9 new handlers added to `_SUPPORTED_HANDLERS_HARNESS_ONLY` —
`steer` / `follow_up` / `cycle_thinking_level` /
`set_steering_mode` / `set_follow_up_mode` /
`set_auto_compaction` / `set_auto_retry` / `abort_retry` /
`abort_bash`. `DEFERRED_COMMANDS` shrinks 16 → **7** (the 5
session-tree + 2 session-inspection commands owned by ADR-0072).
`SUPPORTED_COMMANDS` grows 13 → **22**.

### Cycle algorithm (Pi parity at SHA 734e08e)

```python
model = self.current_model
# P-254: Pi `agent-session.ts:1539` supportsThinking() guard.
if model is None or not getattr(model, "reasoning", False):
    return None
levels = get_supported_thinking_levels(model)
if not levels:
    return None
current = self._state.thinking_level or "off"
idx = levels.index(current) if current in levels else 0
next_level = levels[(idx + 1) % len(levels)]
await self.set_thinking_level(next_level)
return next_level
```

The `len(levels) <= 1` short-circuit drops out: Pi rotates with a
single level (idx wraps), and a reasoning-capable model with a
degenerate map must return that single level (typically `"off"`)
rather than `None`.

### Line citations (W6 P-258 BLOCKING)

All harness docstrings + RPC handler docstrings + the W0 fixture
cite the audited line numbers. Each citation pairs the
`rpc-mode.ts` case site with the `coding-agent/src/core/agent-session.ts`
method site:

| Command | rpc-mode.ts | agent-session.ts |
|---|---|---|
| `steer` | 483-486 | 1181-1192 |
| `follow_up` | 488-491 | 1206-1215 |
| `cycle_thinking_level` | 486-490 | 1537-1548 |
| `set_steering_mode` | 498-501 | 1587-1592 |
| `set_follow_up_mode` | 503-506 | 1594-1599 |
| `set_auto_compaction` | 516-519 | 2026-2034 |
| `set_auto_retry` | 525-528 | 2540-2545 |
| `abort_retry` | 530-533 | 2511-2516 |
| `abort_bash` | 544-547 | 2622-2625 |

### W4 cosmetic closures

- LOW-1 — `_decode_images` strict (covered by P-262 above).
- LOW-2 — `bool(enabled)` coercion retained in `set_auto_*_enabled`
  setters as defensive (Pi RPC payload may carry a truthy non-bool
  via a buggy adapter; coercion is honest).
- LOW-3 — `typing.cast(QueueMode, mode)` in `set_steering_mode` /
  `set_follow_up_mode` instead of `# type: ignore`.
- NIT — `build_dispatch_table` docstring updated to
  "22 supported + 7 deferred = 29 total".
- NIT — deferred handler factory error string drops the
  "Sprint 6d" prefix → `f"{cmd_type} not implemented ({owner_adr})"`.
- NIT — `tests/pi_parity/test_phase_4_6_strict_superset.py:150,160`
  test functions renamed to `_by_sprint_6h_2` so the test name
  carries the lineage and the body carries the current invariant.

## Consequences

- **22 of 29** Pi `RpcCommand` discriminators are now live in the
  Aelix dispatcher.
- The harness exposes 7 new setters + 2 new public properties + 4
  new `AgentState` fields + 1 new `_MessageQueue.set_mode` helper.
- `RpcSessionState` wire surface grows 12 → 13 by adding
  `autoRetryEnabled`.
- 5 session-tree + 2 session-inspection commands remain deferred to
  Sprint 6h₃ per ADR-0072. The Pi `agent-harness.ts` retry loop +
  Pi `bash-executor` cancellation-token threading + `SettingsManager`
  disk persistence + `_throwIfExtensionCommand` / `_expandSkillCommand`
  / `expandPromptTemplate` expanders are all carry-forwards
  documented in ADR-0072.

## Verification

- `tests/pi_parity/test_phase_4_9_strict_superset.py` — 28 tests
  (8 count assertions + 9 wired + 7 deferred + 2 cycle algorithm
  + 2 mode-setter validation + 2 RPC response shape + 1
  `get_state` real auto_compaction + 1 line-number fixture +
  2 supportsThinking guard regressions + 1 message-queue set_mode
  validation + 1 keyword-only marker + 2 strict `_decode_images`
  + 1 `get_state` real auto_retry + 1 `auto_retry_enabled`
  property + 1 RPC `set_steering_mode` invalid + 1
  `RpcSessionState` wire shape).
- `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthened
  (13-field `RpcSessionState` invariant).
- `tests/pi_parity/test_phase_4_6_strict_superset.py` strengthened
  (W4 NIT renames).
- pytest: **1539 baseline + 11 new W6 regressions = 1550**.
- ruff: clean.
- pyright: 8 errors baseline preserved.

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₂ row).
- ADR-0058 — RPC mode initial dispatch (9 of 29).
- ADR-0066 — Sprint 6f W6 closure (12 → 13).
- ADR-0070 — Sprint 6h₁ W6 closure (13 → still 13; `get_commands`).
- ADR-0072 — Phase 4.9 strict-superset closure pin + Sprint 6h₃
  carry-forward roster.

## Phase

Sprint 6h₂ / Phase 4.9 / W6 (shipped).
