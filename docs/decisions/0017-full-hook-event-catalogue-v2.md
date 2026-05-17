# 0017. Full Hook Event Catalogue v2

Status: Accepted (Phase 2.1.1 / Sprint 3a shipped — 18 own events Pi-verified at SHA 734e08e; Sprint 3d amendment: `tool_execution_update` + tool-result message emit sites landed — Phase 2.1.4)
Supersedes: ADR-0011

## Context

ADR-0011은 Phase 1.2의 16개 event만 binding contract으로 등록했습니다. 이는
Phase 1.2 범위를 제한하기 위한 임시 결정이었습니다.

Pi `AgentHarnessEvent` union은 loop-emitted `AgentEvent` (10개)와 harness-emitted
`AgentHarnessOwnEvent` (18개)로 구성됩니다. ADR-0011은 이 구분 없이 16개를
단일 목록으로 등록했습니다.

1차 원칙(Pi parity)에 따라 모든 Pi event를 Phase 2.1에 등록해야 합니다.

## Decision

> **Sprint 3a verification (2026-05-17 at SHA `734e08e`):** Pi
> `AgentHarnessOwnEvent` has 18 members (verified at
> `packages/agent/src/harness/types.ts:595-612`). The previous Draft list of
> 19+ events conflated `docs/hooks.md` "Poking holes" design wishlist with
> shipped types. Wishlist-only events (`session_start`,
> `session_before_switch`, `session_before_fork`, `session_shutdown`,
> `resources_discover`, `input`, `user_bash`) are **NOT** added in Sprint 3a.
> They are tracked as Phase 2.2+ candidates pending upstream Pi adoption or
> an Aelix-additive-divergence ADR.

Sprint 3a (Phase 2.1.1) registers exactly the Pi-verified 28-name surface in
`HookEventName`:

### Pi-Verified Event Count (SHA `734e08e`)

| Group | Count | Source |
| --- | --- | --- |
| Loop `AgentEvent` (projections) | 10 | `packages/agent/src/types.ts:275-295` |
| Harness `AgentHarnessOwnEvent` | 18 | `packages/agent/src/harness/types.ts:595-612` |
| **Total `HookEventName` Literal** | **28** | union of the above (disjoint sets) |

### Phase 2.1.1에 추가/유지된 events (Pi-verified)

#### Loop AgentEvent re-projections (10) — ADR-0036 projection
`agent_start`, `turn_start`, `message_start`, `message_update`, `message_end`,
`tool_execution_start`, `tool_execution_update`, `tool_execution_end`,
`turn_end`, `agent_end`.

#### Harness own-events (18) — Pi `AgentHarnessOwnEvent`

| 그룹 | Event | Pi source (SHA 734e08e) |
| --- | --- | --- |
| Queue/lifecycle | `queue_update` | `types.ts:474-478` |
| Queue/lifecycle | `save_point` | `types.ts:480-483` |
| Queue/lifecycle | `abort` | `types.ts:485-489` |
| Queue/lifecycle | `settled` | `types.ts:491-494` |
| Run lifecycle | `before_agent_start` | `types.ts` BeforeAgentStartEvent |
| Run lifecycle | `context` | `types.ts` ContextEvent |
| Provider | `before_provider_request` | `types.ts:510-515` + `agent-harness.ts:232-250` |
| Provider | `before_provider_payload` | `types.ts:517-521` + `agent-harness.ts:265-280` |
| Provider | `after_provider_response` | `types.ts:523-527` + `agent-harness.ts:275` |
| Tool | `tool_call` | `types.ts` ToolCallEvent |
| Tool | `tool_result` | `types.ts` ToolResultEvent |
| Session (Phase 2.2 emit) | `session_before_compact` | `types.ts` SessionBeforeCompactEvent |
| Session (Phase 2.2 emit) | `session_compact` | `types.ts:554-558` + `agent-harness.ts:582` |
| Session (Phase 2.2 emit) | `session_before_tree` | `types.ts:560-564` + `agent-harness.ts:598` |
| Session (Phase 2.2 emit) | `session_tree` | `types.ts:566-572` + `agent-harness.ts:626` |
| Setter | `model_select` | `types.ts:574-579` + `agent-harness.ts:648` |
| Setter | `thinking_level_select` | `types.ts:581-585` + `agent-harness.ts:660` |
| Setter | `resources_update` | `types.ts:587-593` + `agent-harness.ts:689` |

Session group events는 Phase 2.1.1에 `HookEventName` Literal에 등록하되,
실제 emit site는 Phase 2.2 Session Manager 구현 시 추가합니다.
Setter group events 또한 Phase 2.1.1에 type만 등록하고, emit site는
Sprint 3b setter implementation에서 추가합니다.
Provider group events는 Phase 4 provider adapter (ADR-0038) emit owner입니다.

### Deferred to Phase 2.2+ (not in Pi at pinned SHA)

The following events appear in Pi `docs/hooks.md` "Poking holes" wishlist
section but are **not** shipped in Pi types at SHA `734e08e`. They are NOT
registered in Aelix Sprint 3a:

`session_start`, `session_before_switch`, `session_before_fork`,
`session_shutdown`, `resources_discover`, `input`, `user_bash`.

Adding any of these in Aelix before Pi ships them upstream would create an
"Aelix additive divergence" that needs an explicit ADR. They are tracked
for Phase 2.2+ pending upstream Pi adoption.

## Pi-Verified Event Payload Notes (W5 amendment)

W5 Pi-parity audit (2026-05-17 at SHA `734e08e`) cross-checked each event
payload against Pi `types.ts`. The following items are **Aelix additive
supersets** of the Pi payload — they were introduced as Phase 1.2 handler
ergonomics helpers and have never been explicitly ADR'd as divergences.
They are documented here so future Phase 2 work and any Pi-parity drift
detector see them as known additive fields rather than accidental drift.

### `tool_call` event — two additive fields

Pi `ToolCallEvent` (`types.ts:556-561`) defines exactly three fields:
`toolCallId`, `toolName`, `input`. Aelix `ToolCallHookEvent` adds:

- `assistant_message: AssistantMessage` — Phase 1.2 helper for handler
  ergonomics. Lets policy / guardrail extensions inspect the in-flight
  assistant message that produced this tool call without re-traversing
  the messages list. **Additive superset, not Pi-divergent behavior.**
- `context: AgentContext` — Phase 1.2 helper for handler ergonomics.
  Gives handlers a typed read-only view of messages / tools snapshot
  without the harness having to thread `ctx` through every payload.
  **Additive superset, not Pi-divergent behavior.**

Pi handlers receive `ctx` as a separate parameter (second positional arg in
the Aelix `HookHandler` signature). The two additive fields are redundant
with that parameter for Pi-equivalent handlers but provide a more
ergonomic single-object surface for Aelix extensions. Future Phase 2 may
revisit removal if Pi adds equivalents on the upstream payload, or graduate
this divergence into its own ADR.

### `tool_result` event — `terminate` additive field on payload

Aelix `ToolResultHookEvent` carries a `terminate: bool` field that mirrors
Pi `ToolResultPatch.terminate` (`types.ts` ToolResultPatch). In Pi the
`terminate` flag travels on the **patch** result, not the event payload —
the event itself is Pi-minimal. Aelix surfaces it on the event payload as
an additive convenience so observational extensions can read the
"terminate after this tool" decision without subscribing separately to the
patch chain. **Additive superset, not Pi-divergent behavior.**

### Known shallow-merge divergence — `before_provider_request` reducer (W5 caveat #1)

Phase 2.1.1 ships `_reducer_before_provider_request` with a naive
`dict.update` shallow merge of the `stream_options` patch returned by each
handler. Pi `applyStreamOptionsPatch` (`agent-harness.ts:96-127`)
implements a nuanced **deep merge** with `delete-on-undefined` semantics
specifically for the nested `headers` and `metadata` keys (an `undefined`
value at any nested key deletes that key rather than overwriting with
`undefined`).

This is a **known Pi-parity divergence** scoped to Phase 2.1.1 because:

1. There is no emit site for `before_provider_request` in Sprint 3a — the
   reducer is registered but never invoked (Phase 4 provider adapter
   owns the emit site per ADR-0038).
2. No existing Aelix extension relies on `headers` / `metadata` nested
   patches at this SHA.

**Phase 4 binding requirement:** the emit site landing in the provider
adapter MUST either (a) replace `_reducer_before_provider_request` with a
Pi-equivalent `applyStreamOptionsPatch` deep-merge port, or (b) deep-merge
at the emit-site before passing `stream_options` to the provider call. The
`# TODO(Phase-4)` comment above the reducer in `harness/hooks.py` carries
the tracking marker.

## Consequences

- `HookEventName` Literal은 16개에서 **28개**로 확장됩니다 (10 loop + 18 own).
- `HOOK_RESULT_TYPES` registry에 12개 신규 event 항목이 추가됩니다 (settled는 기존).
- 3개의 신규 result type (`BeforeProviderRequestResult`,
  `BeforeProviderPayloadResult`, `SessionBeforeTreeResult`)이 추가됩니다.
- `SettledHookEvent`에 `next_turn_count: int = 0` payload field가 추가되며,
  Sprint 3b의 `next_turn` queue 도입 시 populating value가 채워집니다.
- 모든 reducer rule을 Pi 그대로 port합니다. Provider chain은 sequential patch
  (`before_provider_request` stream_options merge, `before_provider_payload`
  payload chain), session_before_tree는 기존 `_reducer_session_before` 재사용.
- `AgentEventName` (loop)과 `AgentHarnessEventName` (harness own)을 코드에서
  명시적으로 구분하는 type alias가 `harness/hooks.py`에 추가됩니다 (ADR-0036).
- drift 방어 테스트: `tests/pi_parity/test_hook_event_name_literal_pi_parity.py`가
  Pi event 목록을 fixture로 pin하고 Aelix Literal과 비교합니다 (SHA `734e08e`).
- ADR-0011의 "binding contract은 16개" 조항은 이 ADR로 대체됩니다.
  이후 event 추가/제거/result type 변경은 새 ADR 또는 이 ADR의 개정으로 처리합니다.
- Sprint 3a explicitly does NOT touch `message_end` reducer (ADR-0018 / Sprint 3b
  scope). Sprint 3a leaves `message_end` reducer = `_reducer_observational`,
  result type = `None`.

## Setter emit sites landed Sprint 3b

> **P-4 correction (Sprint 3b finding, verified at SHA `734e08e`):** Sprint 3a
> incorrectly noted that `queue_update` is emitted by "setters and enqueue
> paths". Pi truth — **only enqueue paths emit `queue_update`** (`steer`,
> `followUp`, `nextTurn`, `drainQueuedMessages`, `abort`). **No setter emits
> `queue_update`.** This subsection records the corrected setter emit table
> shipped in Sprint 3b binding spec §A; the JSON drift fixture lives at
> `tests/pi_parity/fixtures/pi_setter_emit_sites_734e08e.json` and is
> exercised by `tests/pi_parity/test_setter_emit_sites_match_pi.py`.

### Pi-verified setter emit table (Sprint 3b)

| Pi method | Aelix method | Pi `agent-harness.ts` | Emits |
| --- | --- | --- | --- |
| `setModel` | `set_model` | 704-718 | `model_select` |
| `setThinkingLevel` | `set_thinking_level` | 720-733 | `thinking_level_select` |
| `setActiveTools` | `set_active_tools` | 735-741 | **none** |
| `setSteeringMode` | `set_steering_mode` | 743-745 | **none** |
| `setFollowUpMode` | `set_follow_up_mode` | 747-749 | **none** |
| `setResources` | `set_resources` | 751-760 | `resources_update` |
| `setStreamOptions` | `set_stream_options` | 762-764 | **none** |
| `setTools` | `set_tools` | 766-776 | **none** |

3 setters emit (model/thinking/resources); 5 do not (active_tools / steering /
follow_up / stream_options / tools). The `queue_update` event continues to be
emitted by `steer()`, `follow_up()`, `next_turn()`, and `abort()` only —
verified by the drift fixture above.

## Tool execution update + tool-result message events landed Sprint 3d (Phase 2.1.4)

Sprint 3d closes two pre-existing Pi-parity carry-over gaps identified by
Sprint 3c's W5 audit. After this sprint, Phase 2.1 is a strict Pi-parity
superset (Q5 acceptance — see ADR-0039 for the closure invariant).

### `tool_execution_update` emit site landed

Pi parity: `executePreparedToolCall` (`agent-loop.ts:604-639`) — `Tool.execute`
accepts a 4th `onUpdate` callback (`types.ts:357-358`), each invocation
constructs a `tool_execution_update` event and pushes the emit promise onto a
per-call `updateEvents` list, then `await Promise.all(updateEvents)` runs in
BOTH the happy path and the catch block before the final tool result is
returned to the loop.

Aelix retains its existing collapsed signature (`Tool.execute(args, ctx)`)
and extends `ToolExecutionContext` with the new `on_partial` field rather
than introducing a 3rd argument. The runtime built in `_execute_and_finalize`
mirrors Pi's drain semantics with
`asyncio.gather(*update_events, return_exceptions=False)` in both the
exception path and the normal return path. See `aelix_ai.tools` for the
`ToolPartialCallback` type alias and the documented `None` tolerance.

#### Aelix-additive — partial-emit exception containment

Pi lets hook-handler exceptions raised while emitting `tool_execution_update`
escape `executePreparedToolCall` entirely. Aelix catches them in the existing
`_execute_and_finalize` `try/except` and converts them to an `isError` tool
result. This is a **stricter-than-Pi safety net** consistent with the
`error_mode` per-handler policy from ADR-0019 v3; it is documented here as
an intentional additive containment rather than a hidden divergence so the
Pi-parity drift detector treats it as known additive behaviour.

### Tool-result message events landed (`emitToolResultMessage` port)

Pi parity: `emitToolResultMessage` (`agent-loop.ts:715-718`) emits BOTH
`message_start` and `message_end` for every tool-result message. Pi calls
this helper from the sequential branch (`:436`) and the parallel branch
(`:498`); `runLoop` does NOT emit message events for tool-result messages.

Aelix Sprint 3d adds `_emit_tool_result_message` as the single source of
truth and calls it from `_execute_tool_calls_sequential` (both immediate
and prepared branches) and from `_execute_tool_calls_parallel` Phase 3.
`_run_loop` is intentionally unchanged — adding the helper there would
double-emit.

#### Sequential ordering refactor

Pre-Sprint 3d the sequential path appended the tool-result message BEFORE
emitting `tool_execution_end`, and never emitted `message_start` /
`message_end` for tool-result messages. Sprint 3d reorders both the
immediate and prepared branches to the Pi-verified
`end → emit_helper → append` sequence (Pi `agent-loop.ts:434-438`).

The ordering matrix in ADR-0021 §E is amended in lockstep (rows 3 and 6).

### Session emit sites + payload extensions landed Sprint 4b (Phase 2.2.2)

Sprint 4b activated the 4 session_* emit sites registered as stubs in Sprint 3a, and extended their payloads to full Pi parity:

- **`session_before_compact`** (P-17): payload extended to `{preparation, branch_entries, custom_instructions, signal}` per Pi `agent-harness.ts:706-711`. Reducer returns `SessionBeforeCompactResult({cancel, compaction})` — `compaction` field lets hook substitute the LLM call entirely (P-20 per Pi `types.ts:339-342`).
- **`session_compact`** (unchanged): emitted observationally after compaction with `{compaction_entry, from_hook}`.
- **`session_before_tree`** (P-18): added `signal` field per Pi `agent-harness.ts:765`.
- **`session_tree`** (P-19): `new_leaf_id` narrowed from `str=""` to `str | None` per Pi `types.ts:303-309`.

Emit sites: `AgentHarness.compact()` (`harness/core.py`) and `AgentHarness.navigate_tree()` (`harness/core.py`).

### Session message_end wiring landed Sprint 4a (Phase 2.2.1)

- `MessageEndHookEvent` is now emitted AFTER `session.append_message(event.message)` is awaited.
- Mirrors Pi `agent-harness.ts:484-486` ordering: session persistence first, then hook fan-out.
- When `AgentHarnessOptions.session` is `None` (Phase 1/2 backward compat), the session call is skipped and only the hook emit runs (Sprint 3b behavior).
- See ADR-0022 §"Sprint 4a → 4b transition plan" for the `state.messages` source-of-truth migration plan.

## Tool execution dispatch landed Sprint 3c (Phase 2.1.3)

ADR-0021 / ADR-0027 land the parallel tool execution dispatcher (Sprint 3c).
The per-event ordering invariants under parallel dispatch are spelled out
verbatim in ADR-0021 §E (the 7-row matrix). This catalogue cross-references
that matrix as the binding source of truth for `tool_call`,
`tool_execution_start`, `tool_execution_update`, `tool_execution_end`,
`tool_result`, `message_start`/`message_end` (tool-result msg) and
`turn_end` ordering when the dispatcher selects the parallel path.

The catalogue itself remains unchanged — the same 28-event surface applies
under both sequential and parallel dispatch. Only the *order* in which
events fire differs (see ADR-0021 §E matrix).
