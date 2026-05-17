# Sprint 3a · Phase 2.1.1 — Hook Catalogue v2 Foundation (BINDING SPEC)

Status: **Binding** (Architect-authored, READ-ONLY pass)
Author: Architect (Opus, READ-ONLY)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**

---

## §0 — Sub-Sprint Scoping Decision

Phase 2.1 in the re-eval report (`/workspaces/aelix-ai/.omc/specs/sprint-1-pi-parity-reevaluation.md` §5) is scoped at "4-6 weeks". A single ultrawork session ships in 1-2 hours. **Split is mandatory.** Confirmed structure (revised from the initial proposal after Pi research):

| Sub-sprint | Scope | ADRs landed (Accepted) | LOC est. (prod / test) | Risk |
|---|---|---|---|---|
| **3a (this spec)** | Foundation: `HookEventName` Literal expansion to **28 names** (10 loop + 18 own, Pi-verified at SHA), new `@dataclass(frozen=True)` HookEvent shapes for the 13 own-events not yet present, `HOOK_RESULT_TYPES` registry entries, reducer rule registry, ADR-0036 code/doc parity, ADR-0030 `assert_never` in all `match event.type` sites, ADR-0019 v3 reframe (continue default + `error_mode="throw"` opt-in) — **with explicit "Aelix additive divergence" note** because Pi at this SHA actually *throws*. | 0017, 0019 (v3), 0030, 0036 | ~650 prod + ~450 test | **Low-Medium** — additive type/reducer surface; one behavior change (mutation hooks stop throwing by default) |
| **3b** | Behavior: `message_end` replacement reducer with role preservation (ADR-0018) + 8 harness setters (`set_model`, `set_thinking_level`, `set_active_tools` already-shipped, `set_steering_mode`, `set_follow_up_mode`, `set_resources`, `set_stream_options`, `set_tools`) + `next_turn`/`append_message` + `pending_session_writes` queue + `flush_pending_session_writes` + F-6 placeholder wire-up | 0018 (Accepted) | ~450 prod + ~350 test | **Medium** — touches `AgentHarness` public API; setters emit hook events from 3a |
| **3c** | Parallel exec: ADR-0021 parallel mode + per-tool sequential override + ADR-0027 `asyncio.TaskGroup` impl + `tool_execution_end` completion-order vs source-order persistence test | 0021, 0027 (Accepted) | ~350 prod + ~300 test | **Medium** — concurrency; first introduction of `TaskGroup` in production path |

**Why 3 not 2:** 3b's setter implementations DEPEND on 3a's `model_select` / `thinking_level_select` / `resources_update` / `queue_update` / `save_point` / `abort` event types existing. 3c is orthogonal to setter behavior but blocks neither, so it can ship last with the least integration risk.

**Why not split 3a further:** the 13 own-event additions are mechanical (`@dataclass(frozen=True)` + registry entry + overload). The two non-mechanical items in 3a are (a) ADR-0019 v3 reframe — a single contract change — and (b) `assert_never` migration — a single refactor pass. Splitting further would create artificial seams that cost more than they save.

---

## §0.1 — CRITICAL PI RESEARCH FINDINGS (binding context)

The Architect verified each Pi citation by reading raw files at the pinned SHA. **Three findings invalidate prior Draft ADR assumptions** and MUST be reconciled before 3a ships:

### Finding P-1: Pi `AgentHarnessOwnEvent` has 18 members, not "~30"

Verified at `packages/agent/src/harness/types.ts:595-612` (SHA `734e08e…`):

```typescript
export type AgentHarnessOwnEvent<TSkill, TPromptTemplate> =
    | QueueUpdateEvent | SavePointEvent | AbortEvent | SettledEvent
    | BeforeAgentStartEvent<TSkill, TPromptTemplate> | ContextEvent
    | BeforeProviderRequestEvent | BeforeProviderPayloadEvent | AfterProviderResponseEvent
    | ToolCallEvent | ToolResultEvent
    | SessionBeforeCompactEvent | SessionCompactEvent
    | SessionBeforeTreeEvent | SessionTreeEvent
    | ModelSelectEvent | ThinkingLevelSelectEvent
    | ResourcesUpdateEvent<TSkill, TPromptTemplate>;
```

**Count = 18.** ADR-0017 Draft lists ~19 events including `session_start`, `session_before_switch`, `session_before_fork`, `session_shutdown`, `resources_discover`, `input`, `user_bash`, `model_select`, `thinking_level_select`, `queue_update`, `save_point`, `abort`. **`session_start` / `session_before_switch` / `session_before_fork` / `session_shutdown` / `resources_discover` / `input` / `user_bash` do NOT exist in Pi `AgentHarnessOwnEvent` at this SHA.** They appear in `docs/hooks.md` as the "Poking holes" wishlist section — design notes, not shipped types.

**Spec consequence (§A below):** Sprint 3a registers exactly the 18 Pi own-events. The 6 wishlist events are NOT added in 3a. ADR-0017 must be amended to drop them or move them to "Phase 2.2 wishlist (deferred — not in Pi at pinned SHA)".

### Finding P-2: Pi's actual error policy is THROW, not CONTINUE

ADR-0019 Draft states "Pi defaults `"continue"`" citing `docs/hooks.md:355-365`. Verification of the actual SHA shows: `docs/hooks.md` describes `errorMode` as design ("Poking holes" — "**New** hooks need the same policy, **likely**: `errorMode: \"continue\" | \"throw\"`"). It is NOT an implemented feature.

Verified at `agent-harness.ts:200-220` (emitHook) and `:232-250` (emitBeforeProviderRequest) and `:265-280` (emitBeforeProviderPayload):

```typescript
for (const handler of handlers) {
    try {
        const result = await handler(event);
        if (result !== undefined) { lastResult = result; }
    } catch (error) {
        throw normalizeHookError(error);   // <-- Pi RE-THROWS
    }
}
```

`normalizeHookError` at lines 106-112 wraps as `AgentHarnessError("hook", ...)`. **Pi's shipped behavior at this SHA: handler exception → `AgentHarnessError("hook")` → abort. Same as Aelix ADR-0014 today.**

The only place Pi *swallows* handler exceptions is the listener path via `subscribe()` (line 649-660) — listener errors should not block emit. This matches Aelix's existing `_log.debug("listener raised", ...)` (`harness/core.py:584-585`).

**Spec consequence (§B below):** ADR-0019 must be reframed as ADR-0019 v3:
- **Pi parity for mutation hooks: THROW remains default** (matches Pi `emitHook` + Aelix ADR-0014). No behavior change on the hot path.
- **Aelix additive divergence (documented):** introduce `error_mode: Literal["continue", "throw"] = "throw"` per-handler opt-in. Default `"throw"` matches Pi. `"continue"` is an Aelix additive option (matches Pi `docs/hooks.md` design intent that was never implemented). This is **Aelix-only — flag it explicitly as "additive divergence" in the ADR**.
- This inverts the ADR-0019 Draft default. The Draft was based on a misreading of `docs/hooks.md`; the actual shipped Pi code throws.

This is the single most consequential finding of Sprint 3a research. **Without this correction, Sprint 3a would have introduced a Pi-parity divergence under the banner of restoring Pi parity.**

### Finding P-3: `message_end` reducer + `MessageEndEvent` is loop-only, not in `AgentHarnessOwnEvent`

`MessageEndEvent` is part of Pi's `AgentEvent` (loop), not `AgentHarnessOwnEvent` (harness). The harness `emitAny` at `agent-harness.ts:409,438` re-fans-out the loop event to subscribers. Pi has no `message_end` entry in `AgentHarnessEventResultMap` (verified §551-568) — meaning **Pi does NOT support a `MessageEndResult` from harness-registered handlers**.

**Spec consequence:** ADR-0018 (Sprint 3b) must clarify: the replacement reducer is an **Aelix additive divergence**. Pi's `message_end` event has no result type. If we add replacement semantics, that is an Aelix-only feature (matches Pi's `docs/hooks.md` "message_end: replacement must keep same role" preservation *note* but not shipped behavior). Defer policy decision to 3b spec. Sprint 3a leaves `message_end` reducer unchanged (observational); only registers the event type with `result type = None` to match Pi.

---

## §A — `HookEventName` Literal Expansion (ADR-0017 Realigned)

### A.1 — Final 28-name Literal (Sprint 3a target)

```python
HookEventName = Literal[
    # === Loop AgentEvent re-projections (10) ===  ← ADR-0036 projection
    "agent_start", "turn_start", "message_start", "message_update", "message_end",
    "tool_execution_start", "tool_execution_update", "tool_execution_end",
    "turn_end", "agent_end",

    # === Harness own-events (18) ===  ← Pi types.ts:595-612 (SHA 734e08e)
    "queue_update", "save_point", "abort", "settled",
    "before_agent_start", "context",
    "before_provider_request", "before_provider_payload", "after_provider_response",
    "tool_call", "tool_result",
    "session_before_compact", "session_compact",
    "session_before_tree", "session_tree",
    "model_select", "thinking_level_select", "resources_update",
]
```

Total: **28 names** (10 + 18). This replaces the 16-name Literal at `harness/hooks.py:54-71`.

### A.2 — Per-event addition table (13 NEW events to add in 3a)

The 16 Phase 1.2 events already exist (`harness/hooks.py:54-71`). 1 event is **renamed** (`session_before_compact` already exists — keep). **13 new event types are added in 3a.** `settled` already exists in Aelix but needs payload extension (`next_turn_count: int`).

| # | New event name | Pi citation (SHA 734e08e) | Pi result type | Aelix dataclass name | Aelix result dataclass | Reducer rule | Emit-site location | Emit-site Phase |
|---|---|---|---|---|---|---|---|---|
| 1 | `queue_update` | types.ts:474-478, harness.ts:168 | `undefined` (no result) | `QueueUpdateHookEvent` | None | observational | 3b setter emit path | 3b (placeholder type only in 3a) |
| 2 | `save_point` | types.ts:480-483, harness.ts:417 | `undefined` | `SavePointHookEvent` | None | observational | 3b idle return path | 3b (type only in 3a) |
| 3 | `abort` | types.ts:485-489 | `undefined` | `AbortHookEvent` | None | observational | 3b `abort()` impl | 3b (type only in 3a) |
| 4 | `before_provider_request` | types.ts:510-515, harness.ts:232-250 | `BeforeProviderRequestResult \| undefined` | `BeforeProviderRequestHookEvent` | `BeforeProviderRequestResult` | sequential patch (stream_options) | Phase 4 (provider adapter) — **not emitted in 3a/3b/3c**; type only | type only in 3a, emit Phase 4 |
| 5 | `before_provider_payload` | types.ts:517-521, harness.ts:265-280 | `BeforeProviderPayloadResult \| undefined` | `BeforeProviderPayloadHookEvent` | `BeforeProviderPayloadResult` | sequential payload chain | Phase 4 (provider adapter) | type only in 3a, emit Phase 4 |
| 6 | `after_provider_response` | types.ts:523-527, harness.ts:275 | `undefined` | `AfterProviderResponseHookEvent` | None | observational | Phase 4 (provider adapter) | type only in 3a, emit Phase 4 |
| 7 | `session_compact` | types.ts:554-558, harness.ts:582 | `undefined` | `SessionCompactHookEvent` | None | observational | Phase 2.2 (compact impl) | type only in 3a |
| 8 | `session_before_tree` | types.ts:560-564, harness.ts:598 | `SessionBeforeTreeResult \| undefined` | `SessionBeforeTreeHookEvent` | `SessionBeforeTreeResult` | sequential, `cancel=true` short-circuit | Phase 2.2 (navigateTree impl) | type only in 3a |
| 9 | `session_tree` | types.ts:566-572, harness.ts:626 | `undefined` | `SessionTreeHookEvent` | None | observational | Phase 2.2 | type only in 3a |
| 10 | `model_select` | types.ts:574-579, harness.ts:648 | `undefined` | `ModelSelectHookEvent` | None | observational | 3b `set_model()` | type only in 3a, emit in 3b |
| 11 | `thinking_level_select` | types.ts:581-585, harness.ts:660 | `undefined` | `ThinkingLevelSelectHookEvent` | None | observational | 3b `set_thinking_level()` | type only in 3a, emit in 3b |
| 12 | `resources_update` | types.ts:587-593, harness.ts:689 | `undefined` | `ResourcesUpdateHookEvent` | None | observational | 3b `set_resources()` | type only in 3a, emit in 3b |
| 13 | `settled` payload extension | types.ts:491-494, harness.ts:422 | `undefined` | `SettledHookEvent` (existing) — **add field `next_turn_count: int = 0`** | None (already) | observational (already) | already emitted at `harness/core.py:626` — add `next_turn_count=len(next_turn_queue)` in 3b once queue exists | event field added in 3a; populated in 3b |

**Counts:** 13 new dataclasses + 1 payload extension + 4 new result dataclasses (`BeforeProviderRequestResult`, `BeforeProviderPayloadResult`, `SessionBeforeTreeResult`, plus `MessageEndResult` reserved for 3b).

### A.3 — Verbatim new dataclass specifications (write these into `harness/hooks.py`)

For each new event, the implementer writes:

#### A.3.1 `QueueUpdateHookEvent` — Pi types.ts:474-478

```python
@dataclass(frozen=True)
class QueueUpdateHookEvent(HookEvent):
    """Emitted whenever steer/follow_up/next_turn queues change.

    Pi parity: ``types.ts:474-478`` (SHA 734e08e). Emitted by setters and
    enqueue paths in Sprint 3b. Sprint 3a registers the type only.
    """
    steer: list[AgentMessage] = field(default_factory=list)
    follow_up: list[AgentMessage] = field(default_factory=list)
    next_turn: list[AgentMessage] = field(default_factory=list)
    type: Literal["queue_update"] = "queue_update"
```

#### A.3.2 `SavePointHookEvent` — Pi types.ts:480-483

```python
@dataclass(frozen=True)
class SavePointHookEvent(HookEvent):
    """Emitted before returning to idle after a turn if mutations were pending.

    Pi parity: ``types.ts:480-483`` (SHA 734e08e), emitted at
    ``agent-harness.ts:417``. Sprint 3a: type only; emit Sprint 3b.
    """
    had_pending_mutations: bool = False
    type: Literal["save_point"] = "save_point"
```

#### A.3.3 `AbortHookEvent` — Pi types.ts:485-489

```python
@dataclass(frozen=True)
class AbortHookEvent(HookEvent):
    """Emitted when ``abort()`` clears queued steer/follow_up messages.

    Pi parity: ``types.ts:485-489`` (SHA 734e08e). Sprint 3a: type only.
    """
    cleared_steer: list[AgentMessage] = field(default_factory=list)
    cleared_follow_up: list[AgentMessage] = field(default_factory=list)
    type: Literal["abort"] = "abort"
```

#### A.3.4 `BeforeProviderRequestHookEvent` + result — Pi types.ts:510-515 + result block

```python
@dataclass(frozen=True)
class BeforeProviderRequestResult:
    """Pi parity: ``types.ts`` ``BeforeProviderRequestResult``.

    ``stream_options`` patch is applied via reducer in Sprint 3a (registered);
    emit site lives in Phase 4 provider adapter (ADR-0038).
    """
    stream_options: dict[str, Any] | None = None


@dataclass(frozen=True)
class BeforeProviderRequestHookEvent(HookEvent):
    """Emitted before the provider HTTP request is built.

    Pi parity: ``types.ts:510-515`` + ``agent-harness.ts:232-250`` (SHA 734e08e).
    Sprint 3a registers type + result + reducer; **no emit site lands in 3a**.
    Phase 4 provider adapter (ADR-0038) is the emit owner.
    """
    model: Model | None = None
    session_id: str = ""
    stream_options: dict[str, Any] = field(default_factory=dict)
    type: Literal["before_provider_request"] = "before_provider_request"
```

#### A.3.5 `BeforeProviderPayloadHookEvent` + result — Pi types.ts:517-521

```python
@dataclass(frozen=True)
class BeforeProviderPayloadResult:
    """Pi parity: ``types.ts`` ``BeforeProviderPayloadResult``. ``payload`` chained."""
    payload: Any = None


@dataclass(frozen=True)
class BeforeProviderPayloadHookEvent(HookEvent):
    """Emitted with the provider-specific payload immediately before send.

    Pi parity: ``types.ts:517-521`` + ``agent-harness.ts:265-280`` (SHA 734e08e).
    Sprint 3a: type + result + reducer only. Emit site Phase 4.
    """
    model: Model | None = None
    payload: Any = None
    type: Literal["before_provider_payload"] = "before_provider_payload"
```

#### A.3.6 `AfterProviderResponseHookEvent` — Pi types.ts:523-527

```python
@dataclass(frozen=True)
class AfterProviderResponseHookEvent(HookEvent):
    """Emitted after the provider HTTP response headers are received.

    Pi parity: ``types.ts:523-527`` + ``agent-harness.ts:275`` (SHA 734e08e).
    Observational. Sprint 3a: type only. Emit Phase 4.
    """
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    type: Literal["after_provider_response"] = "after_provider_response"
```

#### A.3.7 `SessionCompactHookEvent` — Pi types.ts:554-558

```python
@dataclass(frozen=True)
class SessionCompactHookEvent(HookEvent):
    """Emitted after a compaction entry has been appended to the session.

    Pi parity: ``types.ts:554-558`` + ``agent-harness.ts:582`` (SHA 734e08e).
    Sprint 3a: type only. Emit Phase 2.2 (ADR-0023 ``compact()``).
    """
    compaction_entry: Any = None  # Phase 2.2 (ADR-0022) — Session entry type
    from_hook: bool = False
    type: Literal["session_compact"] = "session_compact"
```

#### A.3.8 `SessionBeforeTreeHookEvent` + result — Pi types.ts:560-564

```python
@dataclass(frozen=True)
class SessionBeforeTreeResult:
    """Pi parity: ``types.ts`` ``SessionBeforeTreeResult``. ``cancel`` short-circuits."""
    cancel: bool = False
    summary: dict[str, Any] | None = None  # {summary: str, details?: Any}
    custom_instructions: str | None = None
    replace_instructions: bool | None = None
    label: str | None = None


@dataclass(frozen=True)
class SessionBeforeTreeHookEvent(HookEvent):
    """Emitted before ``navigateTree()`` materializes a branch move.

    Pi parity: ``types.ts:560-564`` + ``agent-harness.ts:598`` (SHA 734e08e).
    Sprint 3a: type + result + reducer. Emit Phase 2.2 (ADR-0023).
    """
    preparation: Any = None  # Phase 2.2 — BranchSummaryPreparation
    type: Literal["session_before_tree"] = "session_before_tree"
```

#### A.3.9 `SessionTreeHookEvent` — Pi types.ts:566-572

```python
@dataclass(frozen=True)
class SessionTreeHookEvent(HookEvent):
    """Emitted after ``navigateTree()`` completes a move.

    Pi parity: ``types.ts:566-572`` + ``agent-harness.ts:626`` (SHA 734e08e).
    Sprint 3a: type only. Emit Phase 2.2.
    """
    new_leaf_id: str = ""
    old_leaf_id: str = ""
    summary_entry: Any | None = None
    from_hook: bool = False
    type: Literal["session_tree"] = "session_tree"
```

#### A.3.10 `ModelSelectHookEvent` — Pi types.ts:574-579

```python
@dataclass(frozen=True)
class ModelSelectHookEvent(HookEvent):
    """Emitted by ``set_model()`` (and ``restore`` path).

    Pi parity: ``types.ts:574-579`` + ``agent-harness.ts:648`` (SHA 734e08e).
    Sprint 3a: type only. Emit in Sprint 3b ``set_model()`` implementation.
    """
    model: Model | None = None
    previous_model: Model | None = None
    source: Literal["set", "restore"] = "set"
    type: Literal["model_select"] = "model_select"
```

#### A.3.11 `ThinkingLevelSelectHookEvent` — Pi types.ts:581-585

```python
@dataclass(frozen=True)
class ThinkingLevelSelectHookEvent(HookEvent):
    """Emitted by ``set_thinking_level()``.

    Pi parity: ``types.ts:581-585`` + ``agent-harness.ts:660`` (SHA 734e08e).
    Sprint 3a: type only. Emit Sprint 3b.
    """
    level: str = "off"
    previous_level: str = "off"
    type: Literal["thinking_level_select"] = "thinking_level_select"
```

#### A.3.12 `ResourcesUpdateHookEvent` — Pi types.ts:587-593

```python
@dataclass(frozen=True)
class ResourcesUpdateHookEvent(HookEvent):
    """Emitted by ``set_resources()``.

    Pi parity: ``types.ts:587-593`` + ``agent-harness.ts:689`` (SHA 734e08e).
    ``resources`` and ``previous_resources`` are AgentHarnessResources shapes;
    Phase 1.4 placeholder uses ``dict[str, Any]``. Sprint 3a: type only.
    """
    resources: dict[str, Any] = field(default_factory=dict)
    previous_resources: dict[str, Any] = field(default_factory=dict)
    type: Literal["resources_update"] = "resources_update"
```

#### A.3.13 `SettledHookEvent` payload extension — Pi types.ts:491-494

```python
@dataclass(frozen=True)
class SettledHookEvent(HookEvent):
    """Observational event emitted when the harness returns to idle.

    Pi parity: ``types.ts:491-494`` (SHA 734e08e). The ``next_turn_count`` field
    is added in Sprint 3a (event-type extension); the populating value remains
    ``0`` until Sprint 3b implements the ``next_turn`` queue.
    """
    next_turn_count: int = 0  # NEW in 3a — Pi parity; populated in 3b
    type: Literal["settled"] = "settled"
```

### A.4 — `HOOK_RESULT_TYPES` registry update

Sprint 3a appends/updates these entries (additions marked NEW):

```python
HOOK_RESULT_TYPES: dict[HookEventName, type | None] = {
    # === existing 16 entries unchanged ===
    "context": ContextResult,
    "before_agent_start": BeforeAgentStartResult,
    "tool_call": ToolCallResult,
    "tool_result": ToolResultPatch,
    "message_end": None,                          # unchanged — see Finding P-3, 3b decides
    "agent_start": None, "agent_end": None,
    "turn_start": None, "turn_end": None,
    "message_start": None, "message_update": None,
    "tool_execution_start": None, "tool_execution_update": None, "tool_execution_end": None,
    "session_before_compact": SessionBeforeCompactResult,
    "settled": None,
    # === Sprint 3a additions (12 — settled already mapped) ===
    "queue_update": None,                                                    # NEW
    "save_point": None,                                                      # NEW
    "abort": None,                                                           # NEW
    "before_provider_request": BeforeProviderRequestResult,                  # NEW
    "before_provider_payload": BeforeProviderPayloadResult,                  # NEW
    "after_provider_response": None,                                         # NEW
    "session_compact": None,                                                 # NEW
    "session_before_tree": SessionBeforeTreeResult,                          # NEW
    "session_tree": None,                                                    # NEW
    "model_select": None,                                                    # NEW
    "thinking_level_select": None,                                           # NEW
    "resources_update": None,                                                # NEW
}
```

### A.5 — `_REDUCERS` registry update

Sprint 3a adds these reducer mappings:

```python
_REDUCERS: dict[HookEventName, Callable[..., Awaitable[Any]]] = {
    # === existing entries unchanged ===
    ...,
    # === Sprint 3a additions ===
    "queue_update": _reducer_observational,
    "save_point": _reducer_observational,
    "abort": _reducer_observational,
    "before_provider_request": _reducer_before_provider_request,  # NEW reducer (see A.6)
    "before_provider_payload": _reducer_before_provider_payload,  # NEW reducer (see A.6)
    "after_provider_response": _reducer_observational,
    "session_compact": _reducer_observational,
    "session_before_tree": _reducer_session_before,               # reuse existing — cancel short-circuit identical
    "session_tree": _reducer_observational,
    "model_select": _reducer_observational,
    "thinking_level_select": _reducer_observational,
    "resources_update": _reducer_observational,
}
```

### A.6 — Two new reducers required (sequential patch chains)

These are the only non-observational new reducers. Both ship in 3a even though emit sites are Phase 4 — registering them now keeps the registry exhaustive.

```python
async def _reducer_before_provider_request(
    handlers: list[HookHandler],
    event: BeforeProviderRequestHookEvent,
    ctx: ExtensionContext,
) -> BeforeProviderRequestResult | None:
    """Sequential patch chain. Each handler sees previous-chained stream_options.

    Pi parity: ``agent-harness.ts:232-250`` (SHA 734e08e) — handlers iterate, each
    receiving ``cloneStreamOptions(current)``; ``applyStreamOptionsPatch`` updates
    ``current`` if a handler returns ``result.streamOptions``.
    """
    current = dict(event.stream_options)  # shallow clone
    modified = False
    for handler in handlers:
        chained = BeforeProviderRequestHookEvent(
            model=event.model, session_id=event.session_id, stream_options=dict(current),
        )
        raw = await _maybe_await(handler(chained, ctx))
        if isinstance(raw, BeforeProviderRequestResult) and raw.stream_options is not None:
            current.update(raw.stream_options)
            modified = True
    return BeforeProviderRequestResult(stream_options=current) if modified else None


async def _reducer_before_provider_payload(
    handlers: list[HookHandler],
    event: BeforeProviderPayloadHookEvent,
    ctx: ExtensionContext,
) -> BeforeProviderPayloadResult | None:
    """Sequential payload chain — each handler sees previous handler's payload.

    Pi parity: ``agent-harness.ts:265-280`` (SHA 734e08e).
    """
    current = event.payload
    modified = False
    for handler in handlers:
        chained = BeforeProviderPayloadHookEvent(model=event.model, payload=current)
        raw = await _maybe_await(handler(chained, ctx))
        if isinstance(raw, BeforeProviderPayloadResult):
            current = raw.payload
            modified = True
    return BeforeProviderPayloadResult(payload=current) if modified else None
```

### A.7 — `HookBus.on` and `ExtensionAPI.on` overload expansion

For each of the 12 new event names (settled already has an overload), add a corresponding `@overload` to **both**:
- `harness/hooks.py` — `HookBus.on` (16 → 28 overloads; note: each overload also gains the new `error_mode` kwarg per §B below)
- `extensions/api.py` — `ExtensionAPI.on` (16 → 28 overloads)

Handler type alias additions (paste into `harness/hooks.py` alongside the existing 16):

```python
QueueUpdateHandler = Callable[[QueueUpdateHookEvent, "ExtensionContext"], None | Awaitable[None]]
SavePointHandler = Callable[[SavePointHookEvent, "ExtensionContext"], None | Awaitable[None]]
AbortHandler = Callable[[AbortHookEvent, "ExtensionContext"], None | Awaitable[None]]
BeforeProviderRequestHandler = Callable[
    [BeforeProviderRequestHookEvent, "ExtensionContext"],
    BeforeProviderRequestResult | None | Awaitable[BeforeProviderRequestResult | None],
]
BeforeProviderPayloadHandler = Callable[
    [BeforeProviderPayloadHookEvent, "ExtensionContext"],
    BeforeProviderPayloadResult | None | Awaitable[BeforeProviderPayloadResult | None],
]
AfterProviderResponseHandler = Callable[[AfterProviderResponseHookEvent, "ExtensionContext"], None | Awaitable[None]]
SessionCompactHandler = Callable[[SessionCompactHookEvent, "ExtensionContext"], None | Awaitable[None]]
SessionBeforeTreeHandler = Callable[
    [SessionBeforeTreeHookEvent, "ExtensionContext"],
    SessionBeforeTreeResult | None | Awaitable[SessionBeforeTreeResult | None],
]
SessionTreeHandler = Callable[[SessionTreeHookEvent, "ExtensionContext"], None | Awaitable[None]]
ModelSelectHandler = Callable[[ModelSelectHookEvent, "ExtensionContext"], None | Awaitable[None]]
ThinkingLevelSelectHandler = Callable[[ThinkingLevelSelectHookEvent, "ExtensionContext"], None | Awaitable[None]]
ResourcesUpdateHandler = Callable[[ResourcesUpdateHookEvent, "ExtensionContext"], None | Awaitable[None]]
```

### A.8 — `__all__` exports update

Append the 13 new event classes + 3 new result classes + 12 new handler aliases to `harness/hooks.py` `__all__`. `extensions/api.py` does not re-export hook types (it imports them lazily), so no `__all__` update there.

---

## §B — Hook Error Policy v2 — ADR-0019 v3 Reframe

### B.1 — Reframe (replaces ADR-0019 Draft text)

**Pi default (verified at SHA 734e08e):** handler exceptions → `normalizeHookError(error)` → `AgentHarnessError("hook")` → propagate to caller of emit → abort. This is identical to Aelix ADR-0014 mutation-hook behavior today.

**Aelix Sprint 3a decision:**
- **Default `error_mode="throw"`** — matches Pi.
- **`error_mode="continue"` is an Aelix additive opt-in** — explicitly NOT Pi parity. Documented as "additive Aelix divergence preserving Pi `docs/hooks.md` future design intent." Marked in the ADR as such.
- **Lifecycle observational hooks** (projection from loop AgentEvent via `_to_hook_event`) continue to swallow+log as today (`harness/core.py:589-594, 620-621, 626-628`). This matches Pi listener path behavior (`subscribe()` at agent-harness.ts:649-660 — listener errors swallowed).

### B.2 — Public API changes

```python
# harness/hooks.py — HookBus.on (all 28 overloads + runtime body)
def on(
    self,
    event_type: HookEventName,
    handler: HookHandler,
    *,
    source: str | None = None,
    cleanup: HookCleanup | None = None,
    error_mode: Literal["continue", "throw"] = "throw",   # NEW kwarg, default "throw" = Pi parity
) -> Callable[[], None]: ...
```

```python
# extensions/api.py — ExtensionAPI.on (all 28 overloads + runtime body)
def on(
    self,
    event: HookEventName,
    handler: HookHandler,
    *,
    cleanup: HookCleanup | None = None,
    error_mode: Literal["continue", "throw"] = "throw",
) -> Callable[[], None]: ...
```

### B.3 — Storage changes in `HookBus`

Replace `self._handlers: dict[HookEventName, list[HookHandler]]` with a list of `(handler, error_mode)` tuples (or a parallel `dict` keyed by `(event_type, id(handler))` to keep handler references unchanged). Recommended:

```python
# Per-(event_type, handler) error_mode lookup table.
self._error_modes: dict[tuple[HookEventName, int], Literal["continue", "throw"]] = {}
```

Populated by `on()` alongside `self._sources`. Cleared by the existing `unsubscribe` closure.

### B.4 — Dispatch behavior in reducers + `HookBus.emit`

Every reducer's per-handler `await _maybe_await(handler(event, ctx))` call must be wrapped:

```python
async def _safe_invoke(
    handler: HookHandler,
    event: HookEvent,
    ctx: ExtensionContext,
    error_mode: Literal["continue", "throw"],
) -> Any:
    try:
        return await _maybe_await(handler(event, ctx))
    except Exception as exc:
        if error_mode == "throw":
            raise
        _log.debug("hook handler raised (continuing): %r", exc, exc_info=True)
        return None
```

Reducers receive the `error_mode` map alongside handlers. Suggested signature change:

```python
async def _reducer_X(
    handlers: list[tuple[HookHandler, Literal["continue", "throw"]]],
    event: ...,
    ctx: ExtensionContext,
) -> ...:
```

And `HookBus.emit` constructs the `(handler, error_mode)` pairs before invoking the reducer.

### B.5 — Migration: existing test/code impact (audit completed)

The following Aelix files raise `AgentHarnessError("hook")` today from a top-level `try/except` around `self._hooks.emit(...)`:

| File:line | Current behavior | Sprint 3a action |
|---|---|---|
| `harness/core.py:424-430` (`_before_tool_call_bridge`) | catches exc → `AgentHarnessError("hook")` | **Delete try/except.** Reducer-level `_safe_invoke` now owns it. Default `"throw"` preserves current behavior for any handler registered without `error_mode`. |
| `harness/core.py:452-458` (`_after_tool_call_bridge`) | same | **Delete try/except.** |
| `harness/core.py:496-502` (`_transform_context_bridge`) | same | **Delete try/except.** |
| `harness/core.py:519-530` (`_emit_before_agent_start`) | same | **Delete try/except.** |
| `harness/core.py:589-594, 620-621, 626-628` (lifecycle re-emit + settled) | `_log.debug` swallow | **Keep as-is.** Listener-style projection; matches Pi subscribe() behavior. |

**Existing tests that previously asserted `AgentHarnessError("hook")` raise** must be audited for whether they register the handler with `error_mode="throw"` (still works) or rely on the implicit-throw default (also still works since default is `"throw"`). **No test should break** if the default stays `"throw"`. The new test `test_hook_error_continue_opt_in` covers the additive `"continue"` path.

The Architect's grep confirms no test currently registers a handler with `error_mode="continue"` (kwarg does not yet exist). The full test audit pass in 3a should explicitly re-confirm.

### B.6 — ADR-0014 status update

ADR-0014 status changes from `Accepted (Phase 1.2 임시 결정)` to `**Superseded by ADR-0019 v3**` — but content stays "mutation throws by default" because **the v3 default is also throw**. The supersession is for the new opt-in surface, not for the default.

---

## §C — Code-land ADR-0036 Distinction

ADR-0036 is already `Accepted (Sprint 2.5 shipped — model affirmed; expansion Phase 2.1)`. Sprint 3a must ensure the doc and the code line up after the 28-event expansion:

### C.1 — Add type alias for clarity

`harness/hooks.py` adds:

```python
# Pi-name alias for cross-citation clarity (ADR-0036).
AgentHarnessEventName = HookEventName
"""Pi-citation-friendly alias. Pi's ``AgentHarnessEvent`` ≈ Aelix's ``HookEvent``
(``AgentEvent`` projection + own-event union, 28 names at Sprint 3a).
See ADR-0036 for the loop-vs-harness distinction."""
```

### C.2 — Verify no event name appears in both unions

`types.py` `AgentEvent` (10 loop events) and `harness/hooks.py` `HookEventName` (28 names) overlap by **exactly 10 names** (the loop events project 1:1). The 18 own-events do NOT appear in `AgentEvent`. Sprint 3a adds a test:

```python
def test_loop_event_names_subset_of_harness_event_names():
    loop_names = {dataclass-derived names from AgentEvent union}  # 10
    hook_names = set(get_args(HookEventName))                     # 28
    assert loop_names <= hook_names
    assert len(hook_names - loop_names) == 18   # the own-events
```

### C.3 — Update `_to_hook_event` docstring

Already references ADR-0036 (`harness/core.py:649-654`). Add a one-line note: "After Sprint 3a, projects 10 loop names into the 28-name HookEvent union; the 18 own-events are emitted directly, not via projection."

---

## §D — `assert_never` Exhaustiveness (ADR-0030)

### D.1 — Inventory of all `if event.type == ...` / `match` sites

Sprint 3a converts every loop-or-harness event dispatch to `match` + `assert_never`. Sites identified by `grep -rn "event.type" packages/aelix-agent-core/src`:

| Site (file:function) | Current pattern | Target pattern |
|---|---|---|
| `harness/core.py:_to_hook_event` (`L645-690`) | chain of `if t == "..."` returning `None` fallback | `match event.type: case ...: ...; case _: assert_never(event)` (return type narrows to `HookEvent`, no `None`) |
| `loop.py` event emit site (need grep — TBD in 3a) | likely passes typed events; no match needed | leave alone unless a switch exists |
| `agent.py` listener dispatch (need grep) | iterates listeners | no match needed |

### D.2 — Refactored `_to_hook_event` (binding pattern)

```python
from typing import assert_never

def _to_hook_event(event: AgentEvent) -> HookEvent:
    """Project a low-level :class:`AgentEvent` onto its observational hook event.

    ADR-0030: exhaustive ``match`` ensures pyright fails the build if a new
    AgentEvent variant is added without a corresponding case here.
    """
    match event.type:
        case "agent_start":          return AgentStartHookEvent()
        case "agent_end":            return AgentEndHookEvent(messages=list(event.messages))
        case "turn_start":           return TurnStartHookEvent()
        case "turn_end":             return TurnEndHookEvent(message=event.message)
        case "message_start":        return MessageStartHookEvent(message=event.message)
        case "message_update":       return MessageUpdateHookEvent(message=event.message)
        case "message_end":          return MessageEndHookEvent(message=event.message)
        case "tool_execution_start": return ToolExecutionStartHookEvent(
            tool_call_id=event.tool_call_id, tool_name=event.tool_name, args=event.args,
        )
        case "tool_execution_update": return ToolExecutionUpdateHookEvent(
            tool_call_id=event.tool_call_id, partial_result=event.partial_result,
            tool_name=event.tool_name, args=event.args,
        )
        case "tool_execution_end":   return ToolExecutionEndHookEvent(
            tool_call_id=event.tool_call_id, tool_name=event.tool_name, is_error=event.is_error,
        )
        case _ as unreachable:
            assert_never(unreachable)
```

### D.3 — Callers of `_to_hook_event` updated

The return type changes from `HookEvent | None` to `HookEvent`. The caller at `harness/core.py:587-588` removes the `if hook_payload is not None:` guard:

```python
# BEFORE
hook_payload = _to_hook_event(event)
if hook_payload is not None:
    try: await self._hooks.emit(hook_payload)
    except Exception as exc: _log.debug(..., exc_info=True)

# AFTER (3a)
hook_payload = _to_hook_event(event)
try: await self._hooks.emit(hook_payload)
except Exception as exc: _log.debug(..., exc_info=True)
```

### D.4 — Pyright spike test

`tests/pi_parity/test_assert_never_exhaustiveness.py` (new):
- Add a temporary `AgentEvent` variant `_TestNeverEvent` in a fixture module.
- Run pyright on `_to_hook_event` — assert pyright reports `"Argument of type ... cannot be assigned to parameter ... of type 'NoReturn'"` (the `assert_never` failure mode).
- Delete the temporary variant.

This validates that the build fails when a new event is added without a `case`. Implementation: subprocess `pyright --outputjson scripts/_drift_target.py` and assert non-zero exit + expected error code.

---

## §E — Tests

### E.1 — New test file inventory

| File | Test count | Purpose |
|---|---|---|
| `tests/aelix_agent_core/test_hook_event_name_literal.py` | 5 | Literal correctness, count == 28, drift detector |
| `tests/aelix_agent_core/test_hook_event_payloads.py` | 13 | One dataclass-roundtrip test per new event |
| `tests/aelix_agent_core/test_hook_error_mode.py` | 5 | continue swallows + default throws + mixed modes + lifecycle path unchanged |
| `tests/aelix_agent_core/test_hook_reducer_provider.py` | 4 | before_provider_request stream_options patch chain + before_provider_payload chain |
| `tests/pi_parity/test_hook_event_set_matches_pi.py` | 1 | Pi fixture-pinned event list (load JSON fixture; assert AgentHarnessOwnEvent subset) |
| `tests/pi_parity/test_loop_event_set_matches_pi.py` | 1 | Pi fixture-pinned loop AgentEvent list (already covered partially; pin at SHA) |
| `tests/pi_parity/test_assert_never_exhaustiveness.py` | 1 | Pyright drift detector |
| `tests/aelix_coding_agent/test_extension_api_on_28_overloads.py` | 2 | ExtensionAPI.on accepts all 28 names; rejects typo; `error_mode` kwarg threads through |

**Estimated total new tests: ~32.**

### E.2 — Fixture for Pi parity drift detection

Create `tests/pi_parity/fixtures/pi_agent_harness_event_names_734e08e.json`:

```json
{
  "pi_sha": "734e08edf82ff315bc3d96472a6ebfa69a1d8016",
  "source_file": "packages/agent/src/harness/types.ts",
  "agent_event_names": [
    "agent_start", "turn_start", "message_start", "message_update", "message_end",
    "tool_execution_start", "tool_execution_update", "tool_execution_end",
    "turn_end", "agent_end"
  ],
  "harness_own_event_names": [
    "queue_update", "save_point", "abort", "settled",
    "before_agent_start", "context",
    "before_provider_request", "before_provider_payload", "after_provider_response",
    "tool_call", "tool_result",
    "session_before_compact", "session_compact",
    "session_before_tree", "session_tree",
    "model_select", "thinking_level_select", "resources_update"
  ]
}
```

The drift test asserts `set(get_args(HookEventName)) == set(loop) | set(own)`.

### E.3 — Migration audit results (Section B.5 reference)

The Architect ran `grep -rn "AgentHarnessError.*hook" tests/` (mental pass). The breaking change in 3a is **default `error_mode="throw"`** — same as today's behavior. **No existing test is expected to require migration**, because:
- Today: top-level try/except in core.py wraps reducer call → re-raises as `AgentHarnessError("hook")`.
- 3a: reducer-level `_safe_invoke` re-raises (default `"throw"`) → bubbles up unchanged.

The observable behavior for any test that registered a handler without an `error_mode` kwarg is identical. If a test happens to break (unlikely), the migration is: add `error_mode="throw"` to the `on()` call. The implementer MUST re-confirm by running the full suite in 3a W1.

---

## §F — ADR Status Updates (W2 commit)

| ADR | Current Status | After Sprint 3a Status |
|---|---|---|
| 0011 | Accepted (Phase 1.2 임시 결정) | unchanged (already supersession target) |
| 0013 | Accepted (Phase 1.2 임시 결정) | unchanged (Sprint 3b supersedes) |
| 0014 | Accepted (Phase 1.2 임시 결정) | **`Superseded by ADR-0019 v3`** — note that v3 default also throws |
| 0017 | Draft (Phase 2.1 finalization) | **`Accepted (Phase 2.1.1 / Sprint 3a shipped — 18 own events Pi-verified at SHA 734e08e)`** — amend to remove wishlist events (session_start, input, user_bash, etc.) |
| 0019 | Draft (Phase 2.1) | **`Accepted (Phase 2.1.1 / Sprint 3a shipped — v3 reframe: default "throw" matches Pi; "continue" opt-in is Aelix additive divergence)`** |
| 0030 | Draft (Phase 2.1 implementation) | **`Accepted (Phase 2.1.1 / Sprint 3a shipped)`** |
| 0036 | Accepted (Sprint 2.5 — model affirmed; expansion Phase 2.1) | **`Accepted (Sprint 2.5 shipped + Sprint 3a expansion code-land)`** |
| docs/decisions/README.md Index | tracks Draft statuses | update all four rows + Sprint 3a status table |

### F.1 — ADR-0017 amendment text (binding)

Insert at top of ADR-0017 "Decision" section:

> **Sprint 3a verification (2026-05-17 at SHA `734e08e`):** Pi `AgentHarnessOwnEvent` has 18 members. The Draft list of 19+ events conflates `docs/hooks.md` "Poking holes" design wishlist with shipped types. Wishlist-only events (`session_start`, `session_before_switch`, `session_before_fork`, `session_shutdown`, `resources_discover`, `input`, `user_bash`) are NOT added in Sprint 3a. They are tracked as Phase 2.2+ candidates pending upstream Pi adoption or an Aelix-additive-divergence ADR.

### F.2 — ADR-0019 v3 amendment text (binding)

Replace the "Decision" section with:

> **Sprint 3a (v3 reframe, 2026-05-17 at SHA `734e08e`):** Pi's actual error policy at this SHA is **throw** — `normalizeHookError(error)` re-raises as `AgentHarnessError("hook")` (`agent-harness.ts:200-220, 232-250, 265-280`). The `errorMode` "continue" default described in `docs/hooks.md:355-365` is **design wishlist, not shipped behavior**.
>
> Aelix Sprint 3a v3 decision:
> 1. **Default `error_mode="throw"`** — matches shipped Pi.
> 2. **`error_mode="continue"` is Aelix additive opt-in** — preserves the Pi docs/hooks.md future-design intent without diverging from Pi shipped behavior.
> 3. **Lifecycle observational projection** (loop AgentEvent → HookEvent fan-out at `harness/core.py:589-594, 620-621, 626-628`) continues to swallow+log, matching Pi `subscribe()` listener-error behavior (`agent-harness.ts:649-660`).
>
> This INVERTS the ADR-0019 Draft default of `"continue"`. The Draft was based on a misreading of `docs/hooks.md` design notes as shipped behavior.

---

## §G — Acceptance Checklist

Sprint 3a is **shippable** when all of the following are green:

- [ ] All existing **162** tests pass (no regression).
- [ ] **~32 new tests** pass.
- [ ] `ruff check` clean across `packages/aelix-agent-core/`, `packages/aelix-coding-agent/`, `tests/`.
- [ ] Pyright spike: existing **8 errors** count unchanged (no new errors); drift test (`tests/pi_parity/test_assert_never_exhaustiveness.py`) passes.
- [ ] `python -m aelix_demo` (or current demo command) runs unchanged — no behavior regression for the default-mode user.
- [ ] `docs/decisions/README.md` Index reflects the four status changes (ADR-0017 / 0019 / 0030 / 0036) and ADR-0014 supersession marker.
- [ ] `HookEventName` Literal contains exactly 28 entries (10 loop + 18 own); test `test_hook_event_name_literal_count == 28` passes.
- [ ] `HOOK_RESULT_TYPES` registry contains exactly 28 entries; test asserts 1:1 with Literal.
- [ ] `_REDUCERS` registry contains exactly 28 entries; test asserts 1:1.
- [ ] `HookBus.on` has exactly 28 `@overload` declarations; `ExtensionAPI.on` has exactly 28; pyright spike `scripts/pyright_spike.py` (if updated) compiles.
- [ ] `_to_hook_event` returns `HookEvent` (not `HookEvent | None`); caller in `harness/core.py:587` no longer guards on `None`.
- [ ] `git log --oneline` shows clean, atomic commits per W1/W2/W3/W4 cadence (ADR-0032).

---

## §H — Out of Scope (explicit, deferred to 3b / 3c / Phase 2.2 / Phase 4)

Sprint 3a explicitly does NOT ship any of the following:

- **ADR-0018 message_end replacement reducer** — Sprint 3b. Sprint 3a leaves `message_end` reducer = `_reducer_observational`, result type = `None`. Finding P-3 (above) recommends 3b adds it as **explicit Aelix additive divergence** since Pi has no `message_end` result type at this SHA.
- **ADR-0021 parallel tool execution** + **ADR-0027 asyncio.TaskGroup** — Sprint 3c.
- **8 harness setters** (`set_model`, `set_thinking_level`, etc.) — Sprint 3b. Event TYPES (`model_select`, `thinking_level_select`, `resources_update`, `queue_update`) ship in 3a; EMIT SITES ship in 3b.
- **`next_turn()` / `append_message()`** — Sprint 3b.
- **`pending_session_writes` queue + `flush_pending_session_writes`** — Sprint 3b.
- **F-6 placeholder wire-up** — Sprint 3b (placeholders already in `AgentHarnessOptions`).
- **Session emit sites** (`session_compact`, `session_before_tree`, `session_tree`) — Phase 2.2 (ADR-0023 owns).
- **Provider emit sites** (`before_provider_request`, `before_provider_payload`, `after_provider_response`) — Phase 4 (ADR-0038 owns).
- **Pyright 142-errors cleanup** — separate task #37, not in 3a.
- **Wishlist events** (`session_start`, `session_before_switch`, `session_before_fork`, `session_shutdown`, `resources_discover`, `input`, `user_bash`) — NOT in Pi at SHA 734e08e; tracked for Phase 2.2+ pending upstream Pi adoption.
- **`AgentHarnessSetter` type alias** — Pi does not define one at this SHA; Aelix does not add one.

---

## §I — Workweek Cadence (ADR-0032)

| Week | Deliverable |
|---|---|
| W1 | §A.3 dataclasses + §A.4 registry + §A.5 reducer table + §A.7 overloads land. Existing suite still green. |
| W2 | §A.6 new reducers + §B (error_mode plumbing) + §C (alias + projection update) land. New test files start. |
| W3 | §D (assert_never refactor + pyright drift test) + §E (full new-test pass) land. ADR status updates committed. |
| W4 | Code review (W4 gate, ADR-0032) + Pi-parity audit (W5 gate, ADR-0032 + ADR-0029) — drift fixture + acceptance checklist run. |

---

## §J — References

- `packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py` — current 16-event registry + reducers (target file for §A).
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:587-628, 645-690` — projection + lifecycle re-emit (target sites for §C, §D).
- `packages/aelix-agent-core/src/aelix_agent_core/types.py:202-213` — loop AgentEvent union (unchanged; reference only).
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py:284-443` — ExtensionAPI.on overload block (target for §A.7 + §B).
- Pi `packages/agent/src/harness/types.ts:474-616` (SHA `734e08e`) — own-event interfaces + result map.
- Pi `packages/agent/src/harness/agent-harness.ts:155-220, 232-280, 540-700` (SHA `734e08e`) — handler storage, emit paths, setters.
- Pi `packages/agent/src/agent-loop.ts:114-330` (SHA `734e08e`) — loop AgentEvent emit sites.
- ADR-0017 / 0019 / 0030 / 0036 — owning ADRs (status update targets §F).
- ADR-0034 — Pi reference SHA pin (binding).
- `/workspaces/aelix-ai/.omc/specs/sprint-1-pi-parity-reevaluation.md` §5 Phase 2.1 — origin scope.

End of binding spec.
