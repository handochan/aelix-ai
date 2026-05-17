# Sprint 3b · Phase 2.1.2 — Behavior: Setters + nextTurn/appendMessage + pendingSessionWrites (BINDING SPEC)

Status: **Binding** (Architect-authored, READ-ONLY pass)
Author: Architect (Opus, READ-ONLY)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**

---

## §0 — STEP 1: P-3 INVESTIGATION (Pi `message_end` semantics — VERIFIED at SHA 734e08e)

### Findings (each Pi citation verified at the pinned SHA)

**Q1: Does Pi emit `message_end` at all (loop or harness)?**
Yes — but **only as a loop AgentEvent re-fanned-out** to harness subscribers. Pi `agent-harness.ts:434-438`:

```typescript
if (event.type === "message_end") {
    await this.session.appendMessage(event.message);
    await this.emitAny(event, signal);
    return;
}
```

`emitAny` (`agent-harness.ts:216-222`) iterates the wildcard subscriber set, awaits each listener, but **does not collect any return value**. It is a fire-and-forget broadcast — semantically identical to Pi's `subscribe()` channel.

**Q2: Does Pi have any hook that lets handlers replace an assistant message?**
**No.** `AgentHarnessEventResultMap` at `types.ts:632-650` contains exactly these entries with non-`undefined` result types: `before_agent_start`, `context`, `before_provider_request`, `before_provider_payload`, `tool_call`, `tool_result`, `session_before_compact`, `session_before_tree`. **There is no `message_end` entry, no `MessageEndResult` interface, and no `messages_end` reducer in `agent-harness.ts`.**

The only "replace this message" mechanism in Pi is `context` (`agent-harness.ts:377-380`), which rewrites the **input** message list before each turn — not the assistant message that just finished streaming.

**Q3: Does Pi `context` hook serve the role we attributed to `message_end`?**
**Partially yes, and that is sufficient.** Pi's actual flow when an extension wants to rewrite an emitted assistant message is:
1. Observe via `subscribe()` or `on("message_end", …)` (read-only broadcast).
2. To replace the model output in the **next** turn's context, the `context` hook (`ContextResult.messages`) returns a patched list before the next LLM call.
3. Persisted messages can be edited via `Session.appendCustomMessageEntry` / `navigateTree` flows (Phase 2.2 territory).

There is no in-place mid-loop assistant-message replacement reducer in Pi at this SHA.

### P-3 VERDICT: **Option B** — keep `MessageEndHookEvent` **observational** and deprecate ADR-0018

**Rationale:**
1. **Pi parity wins (top-level principle):** Pi has no `MessageEndResult`. Adding one would be Aelix-additive divergence, justifiable only with concrete demand.
2. **Sprint 3a finding P-3 explicitly flagged this** and deferred resolution to 3b.
3. **Zero present demand:** Searching Aelix code for `MessageEndResult`, `_reducer_message_end`, or message-replacement intent returns nothing.
4. **Pi's `context` hook + Phase 2.2 `session_*` hooks cover the legitimate use cases.**
5. **Status quo costs nothing:** `MessageEndHookEvent` already exists from Phase 1.2; reducer is already `_reducer_observational`; result type is already `None`. **Sprint 3b ships zero behavior changes for this.**

**Rejected alternatives:**
- **Option A (Aelix additive replacement reducer):** rejected — no consumer asks for it AND Pi explicitly designed away from it.
- **Option C (port a different Pi mechanism):** there is no different Pi mechanism — `context` already exists and is sufficient.

**ADR-0018 disposition:** Reframe from `Draft (Phase 2.1)` → **`Deprecated — superseded by Sprint 3b P-3 verdict; Pi has no message_end reducer at SHA 734e08e`**. Keep the file for historical context with a 2-paragraph "Why this never shipped" addendum citing this spec §0 and Sprint 3a §H. ADR-0013's `Superseded by ADR-0018` line is amended to `Superseded by Sprint 3b P-3 verdict (ADR-0018 deprecated); message_end remains observational`.

---

## §A — 8 Harness Setters

Each setter ships in `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` as a public `async def` method on `AgentHarness` (Pi parity — every setter in Pi is `async`).

### A.1 `set_model(model: Model)` — Pi `setModel` (`agent-harness.ts:704-718`)

```python
async def set_model(self, model: Model) -> None:
```

- **Pi line range:** `agent-harness.ts:704-718`.
- **State mutation:** `self._state.model = model`. **Persistence:** Pi calls `session.appendModelChange` when idle, else pushes `{type:"model_change", provider, modelId}` onto `pendingSessionWrites`. Aelix Sprint 3b implements **same branch** using §C in-memory queue.
- **Emit event:** `ModelSelectHookEvent(model=model, previous_model=previous, source="set")`. `source="restore"` reserved for Phase 2.2.
- **Tests (3):** roundtrip + previous-snapshot; idle vs turn pending-write; handler-raise → AgentHarnessError("hook").

### A.2 `set_thinking_level(level: str)` — Pi `setThinkingLevel` (`agent-harness.ts:720-733`)

```python
async def set_thinking_level(self, level: str) -> None:
```

- **State mutation:** `self._state.thinking_level = level`.
- **Persistence:** idle → eventual `session.appendThinkingLevelChange` (Phase 2.2); turn → `PendingThinkingLevelChangeWrite`.
- **Emit event:** `ThinkingLevelSelectHookEvent(level=level, previous_level=previous)`.
- **Tests (3):** mutation + emit; turn-time pending; handler-raise propagation.

### A.3 `set_active_tools(names: list[str])` — already exists privately

```python
async def set_active_tools(self, tool_names: list[str]) -> None:
```

- **Pi line range:** `agent-harness.ts:735-741`.
- **State mutation:** F-9 non-destructive contract.
- **Emit event:** **none** — Pi `setActiveTools` does **NOT** emit any event. Aelix MUST NOT add one.
- **Public exposure:** Add `async def set_active_tools(self, tool_names): self._action_set_active_tools(tool_names)`. Keep `_action_set_active_tools` for `ExtensionRuntimeActions` (sync action table; async migration is Phase 2.2+).
- **Tests (3):** public↔private equivalence; emit-zero-events Pi parity guard; invalid-arg no mutation.

### A.4 `set_steering_mode(mode: QueueMode)` — Pi `setSteeringMode` (`agent-harness.ts:743-745`)

```python
async def set_steering_mode(self, mode: QueueMode) -> None:
```

- **State mutation:** `self._steering_queue.mode = mode`.
- **Emit event:** **none**.
- **Tests (2):** mode flip behavior; no-event Pi parity.

### A.5 `set_follow_up_mode(mode: QueueMode)` — Pi `setFollowUpMode` (`agent-harness.ts:747-749`)

Mirror of A.4 on `self._follow_up_queue`. No emit. Tests (2).

### A.6 `set_resources(resources: dict[str, Any])` — Pi `setResources` (`agent-harness.ts:751-760`)

```python
async def set_resources(self, resources: dict[str, Any]) -> None:
```

- **State mutation:** new `AgentState.resources: dict[str, Any] = field(default_factory=dict)`. Mutate via `self._state.resources = dict(resources)` (shallow clone Sprint 3b; deep clone Phase 2.2).
- **Emit event:** `ResourcesUpdateHookEvent(resources=current, previous_resources=previous)` with snapshot clones.
- **Tests (3):** roundtrip + clone semantics; emit-payload-isolation; handler-raise propagation.

### A.7 `set_stream_options(stream_options: dict[str, Any])` — Pi `setStreamOptions` (`agent-harness.ts:762-764`)

```python
async def set_stream_options(self, stream_options: dict[str, Any]) -> None:
```

- **State mutation:** new `AgentState.stream_options: dict[str, Any] = field(default_factory=dict)`. Mutate via `self._state.stream_options = dict(stream_options)`. Add `# TODO(Phase-4): deep-clone headers/metadata`.
- **Emit event:** **none**.
- **Tests (2):** roundtrip; no-event Pi parity.

### A.8 `set_tools(tools, active_tool_names=None)` — Pi `setTools` (`agent-harness.ts:766-776`)

```python
async def set_tools(
    self,
    tools: list[AgentTool],
    active_tool_names: list[str] | None = None,
) -> None:
```

- **Critical Pi behavior:** atomic replace + re-validate active set against new tools BEFORE mutation. On validation failure, throw without partial mutation.
- **State mutation:** validate proposed names → atomic mutate `self._state.tools` + `self._state.active_tool_names`.
- **Emit event:** **none**.
- **Tests (3):** atomic replace; failure-no-partial-mutation; preserves active filter when names omitted.

### Setter summary table

| # | Aelix method | Pi `agent-harness.ts` | Emit event | Pending-write entry |
|---|---|---|---|---|
| 1 | `set_model` | 704-718 | `model_select` | `PendingModelChangeWrite` |
| 2 | `set_thinking_level` | 720-733 | `thinking_level_select` | `PendingThinkingLevelChangeWrite` |
| 3 | `set_active_tools` | 735-741 | **none** | none |
| 4 | `set_steering_mode` | 743-745 | **none** | none |
| 5 | `set_follow_up_mode` | 747-749 | **none** | none |
| 6 | `set_resources` | 751-760 | `resources_update` | none |
| 7 | `set_stream_options` | 762-764 | **none** | none |
| 8 | `set_tools` | 766-776 | **none** | none |

**P-4 finding (ADR-0017 amendment trigger):** Sprint 3a registered `queue_update` as emitted by "setters and enqueue paths". Verified Pi truth: ONLY enqueue paths emit `queue_update` (`steer`, `followUp`, `nextTurn`, `drainQueuedMessages`, `abort`). **No setter emits `queue_update`.** §G.1 amends ADR-0017 to remove the "setter emit" claim.

---

## §B — `next_turn` / `append_message`

### B.1 `next_turn(text, *, images=None)` — Pi `nextTurn` (`agent-harness.ts:572-575`)

```python
async def next_turn(
    self,
    text: str | None = None,
    *,
    images: list[ImageContent] | None = None,
) -> None:
```

- Pi takes `(text, options?)` — always wraps text into a `UserMessage`.
- Push to `self._next_turn_queue: list[AgentMessage]`. Emit `QueueUpdateHookEvent(steer=..., follow_up=..., next_turn=...)` with snapshot copies.
- **Always legal regardless of phase.** Drained at start of NEXT `prompt()` (prepended to new user message per Pi `executeTurn` L466-472).
- **Settled extension:** `SettledHookEvent.next_turn_count` (Sprint 3a Type-only) finally populated in `_run` finally block.
- **Tests (3):** idle enqueue + emit; drained-at-next-prompt; settled.next_turn_count populated.

### B.2 `append_message(message)` — Pi `appendMessage` (`agent-harness.ts:575-582`)

```python
async def append_message(self, message: AgentMessage) -> None:
```

- Idle → append to `self._state.messages` directly. Turn → push `PendingMessageWrite`.
- **Emit event:** **none**.
- **Tests (3):** idle direct-append; turn pending-write; no-event Pi parity.

---

## §C — `pending_session_writes` Queue Design

### C.1 New module surface in `harness/core.py`

```python
@dataclass(frozen=True)
class PendingMessageWrite:
    message: AgentMessage
    type: Literal["message"] = "message"

@dataclass(frozen=True)
class PendingModelChangeWrite:
    provider: str
    model_id: str
    type: Literal["model_change"] = "model_change"

@dataclass(frozen=True)
class PendingThinkingLevelChangeWrite:
    thinking_level: str
    type: Literal["thinking_level_change"] = "thinking_level_change"

PendingSessionWrite = (
    PendingMessageWrite
    | PendingModelChangeWrite
    | PendingThinkingLevelChangeWrite
)
```

The 3 variants Pi creates from setter/append paths at this SHA. Other 6 Pi variants (custom/custom_message/label/session_info/leaf) are Phase 2.2.

### C.2 Storage + flush

```python
self._pending_session_writes: list[PendingSessionWrite] = []
self._next_turn_queue: list[AgentMessage] = []
```

```python
async def flush_pending_session_writes(self) -> None:
    """Drain FIFO. Sprint 3b: PendingMessageWrite → state.messages append;
    model_change / thinking_level_change drop (state already mutated; Phase 2.2
    Session ADR-0022 replaces with real persistence path).
    Pi parity: ``agent-harness.ts:414-432``.
    """
```

### C.3 Flush trigger sites

1. **`turn_end` handling** in `emit` callback inside `_run`: after AgentEvent `turn_end`, capture `had_pending`, call `flush_pending_session_writes`, emit `SavePointHookEvent(had_pending_mutations=had_pending)`. Finally populates `save_point`.
2. **`_run` finally block:** guarantee flush on crash too.
3. **Public `flush_pending_session_writes()`** on `AgentHarness` for tests + future Session.

### C.4 Tests (4)

1. appended message visible next turn
2. turn_end → save_point with had_pending_mutations=True
3. clean turn → save_point had_pending_mutations=False
4. cleared on run failure

---

## §D — F-6 Placeholder Wire-up

| Field | Sprint 3b | Wiring |
|---|---|---|
| `session` | inert | Phase 2.2 (ADR-0022) |
| `env` | inert | `ExecutionEnv` ADR TBD; update comment |
| `resources` | **wire** | type fix `dict[str, Any] \| None`; flow into `AgentState.resources` |
| `thinking_level` | **wire** | flow into `AgentState.thinking_level` |
| `active_tool_names` | **wire** | flow via F-9 validator path |
| `get_api_key_and_headers` | inert | Phase 4 (ADR-0038 provider) |
| `stream_options` | **wire** | flow into `AgentState.stream_options` |

### D.1 Test migration

`tests/test_agent_harness_options_placeholders.py`: 2 → 6 tests. Add 4 wired-field flow assertions.

---

## §E — `message_end` Resolution (P-3)

Per §0 verdict (Option B):

1. **Code:** zero changes.
2. **ADR-0018:** status → `Deprecated`. Add §"Why this was rejected in Sprint 3b" citing §0 evidence.
3. **ADR-0013:** amend supersede line to "Superseded by Sprint 3b P-3 verdict (ADR-0018 deprecated); message_end remains observational; Pi has no replacement reducer at SHA 734e08e."
4. **docs/decisions/README.md:** flip ADR-0018 row to `Deprecated`; remove from "Draft ADR / Target Phase" table.
5. **No additive divergence ADR created.** Pi parity wins.

---

## §F — Test Plan

### F.1 New test files

| File | Test count | Coverage |
|---|---|---|
| `tests/test_harness_setters.py` | 21 | 8 setters × 2-3 tests |
| `tests/test_harness_next_turn.py` | 6 | next_turn + append_message |
| `tests/test_harness_pending_writes.py` | 4 | §C.4 |
| `tests/test_f6_placeholder_wire_up.py` | 6 | §D.1 |
| `tests/pi_parity/test_setter_emit_sites_match_pi.py` | 1 | fixture-pinned drift |
| `tests/test_message_end_remains_observational.py` | 2 | P-3 pin |

**Estimated +40 tests; 195 → ~235.**

### F.2 Migration impact

- `tests/test_set_active_tools.py` — add async wrapper test; keep 4 existing.
- `tests/test_agent_harness.py` — no break expected (purely additive).
- Existing `tests/test_agent_harness_options_placeholders.py` 2 → 6 per §D.1.

### F.3 Pi-parity drift fixture

`tests/pi_parity/fixtures/pi_setter_emit_sites_734e08e.json`:

```json
{
  "pi_sha": "734e08edf82ff315bc3d96472a6ebfa69a1d8016",
  "setters": {
    "setModel": {"lines": "704-718", "emits": ["model_select"]},
    "setThinkingLevel": {"lines": "720-733", "emits": ["thinking_level_select"]},
    "setActiveTools": {"lines": "735-741", "emits": []},
    "setSteeringMode": {"lines": "743-745", "emits": []},
    "setFollowUpMode": {"lines": "747-749", "emits": []},
    "setResources": {"lines": "751-760", "emits": ["resources_update"]},
    "setStreamOptions": {"lines": "762-764", "emits": []},
    "setTools": {"lines": "766-776", "emits": []}
  }
}
```

### F.4 `set_active_tools` async/sync coexistence (P-5)

- Keep `_action_set_active_tools` (sync) for `ExtensionRuntimeActions.set_active_tools`.
- Add public `async def set_active_tools` wrapper on `AgentHarness`.
- `ExtensionAPI.set_active_tools` stays sync.
- Future ADR may unify (Phase 2.2+).

---

## §G — ADR Amendments

| ADR | Sprint 3a status | After Sprint 3b status |
|---|---|---|
| 0013 | `Superseded by ADR-0018` | `Superseded by Sprint 3b P-3 verdict` |
| 0017 | `Accepted (Sprint 3a / Phase 2.1.1 shipped)` | add §"Setter emit sites landed Sprint 3b": correct P-4 (setters don't emit queue_update; only enqueue paths do) |
| 0018 | `Draft (Phase 2.1)` | **`Deprecated`** + §"Why this was rejected in Sprint 3b" |
| README.md index | ADR-0018 Draft | flip to `Deprecated`; remove from Draft target table |

**No NEW ADRs in Sprint 3b** (faithful Pi parity throughout).

---

## §H — Verification Checklist

- All 195 existing tests pass (no regression)
- ~40 new tests pass (~235 total)
- ruff clean
- pyright spike: 8 errors (no regression)
- demo unchanged
- F-6 wired fields flow into AgentState (4 new assertions)
- Pi-parity drift fixture test passes
- `_pending_session_writes` empty after every completed turn
- ADR amendments per §G
- `SettledHookEvent.next_turn_count` populated
- `save_point` emitted at every turn_end boundary

---

## §I — Out of Scope (deferred)

- ADR-0021 / 0027 parallel tool execution (Sprint 3c)
- Session JSONL persistence ADR-0022 (Phase 2.2)
- Session compact / branch_summary / navigateTree ADR-0023 (Phase 2.2)
- `get_api_key_and_headers` wiring (Phase 4 ADR-0038)
- `env` field correction (Phase 2.2 / Phase 4)
- Aelix-additive `MessageEndResult` (rejected §0)
- Async migration of `ExtensionRuntimeActions.set_active_tools` (Phase 2.2+)
- Deep clone of `stream_options.headers/metadata` (Phase 4)

---

## §J — References

- Pi `agent-harness.ts` @ SHA 734e08e: L43-47 createUserMessage; L172 pendingSessionWrites; L414-432 flushPendingSessionWrites; L434-450 handleAgentEvent; L466-472 nextTurnQueue drain; L572-575 nextTurn; L575-582 appendMessage; L704-718 setModel; L720-733 setThinkingLevel; L735-741 setActiveTools; L743-745 setSteeringMode; L747-749 setFollowUpMode; L751-760 setResources; L762-764 setStreamOptions; L766-776 setTools. (W6 F-3b-1 correction — prior spec cited 691-756 range which was off-by-13.)
- Pi `types.ts` @ SHA 734e08e: L461-464 PendingSessionWrite; L466-470 QueueUpdateEvent; L472-474 SavePointEvent; L481-483 SettledEvent; L554-579 ModelSelect/ThinkingLevelSelect/ResourcesUpdate; L632-650 AgentHarnessEventResultMap (P-3 evidence).
- Aelix `harness/core.py`: L93 phase; L109-145 AgentHarnessOptions (placeholders); L222-227 AgentState init; L306-337 prompt; L350-355 abort; L406-415 `_action_set_active_tools` (F-9); L548-651 `_run`; L644 SettledHookEvent emit site.
- Aelix `types.py`: L67-78 AgentState; L73-78 active_tool_names + thinking_level; L38 QueueMode.
- Aelix `harness/hooks.py`: L378-388 MessageEndHookEvent; L473-484 QueueUpdateHookEvent; L487-496 SavePointHookEvent; L592-616 ModelSelect/ThinkingLevelSelect; L619-630 ResourcesUpdate; L633-643 SettledHookEvent.
- ADR-0017 (Accepted Sprint 3a) — §"Setter emit sites landed Sprint 3b" target.
- ADR-0018 (Draft → Deprecated by §E) — owning historical record.
- ADR-0013 (Phase 1.2 임시) — supersede line updated.
- Sprint 3a spec — origin of P-3 mandate.

End of binding spec.
