# 0019. Hook Error Policy v3 — `"throw"` Default (Pi parity) + per-handler `"continue"` opt-in

Status: Accepted (Phase 2.1.1 / Sprint 3a shipped — v3 reframe)
Supersedes: ADR-0014

## Context

ADR-0014는 hook을 두 분류로 나누어 예외 처리를 달리했습니다: mutation hook 예외는
`AgentHarnessError("hook")`로 raise + loop abort, lifecycle observational 예외는
swallow + `_log.debug`. 이는 Phase 1.2 임시 결정이었습니다.

ADR-0019 Draft (2026-04)는 Pi가 모든 hook에 `"continue"` default를 사용한다고
주장했습니다. 근거는 `packages/agent/docs/hooks.md:355-365`의 다음 문장이었습니다:
"errorMode: 'continue' | 'throw'" — 모든 hook에 'continue' default 적용.

**Sprint 3a research (2026-05-17 at SHA `734e08e`)에서 이 Draft 주장이 잘못되었음을
검증했습니다.** Pi `docs/hooks.md`의 해당 섹션은 "Poking holes" — **shipped 기능이
아니라 미래 설계 wishlist**입니다. Pi가 SHA `734e08e`에서 실제로 출시하는 행동은
정반대입니다.

### Pi의 실제 shipped 행동 (SHA `734e08e`)

Verified at `agent-harness.ts:200-220` (emitHook), `:232-250` (emitBeforeProviderRequest),
`:265-280` (emitBeforeProviderPayload):

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

`normalizeHookError` at lines 106-112 wraps as `AgentHarnessError("hook", ...)`.
**Pi's shipped behavior at this SHA: handler exception → `AgentHarnessError("hook")`
→ abort. 이는 Aelix ADR-0014의 mutation-hook 행동과 동일합니다.**

Pi가 *swallow*하는 유일한 경로는 listener path (`subscribe()` at
`agent-harness.ts:649-660`) — listener 예외는 emit을 막지 않습니다. 이는 Aelix의
기존 `_log.debug("listener raised", ...)` (`harness/core.py:584-585`) 행동과
동일합니다.

## Decision

> **Sprint 3a (v3 reframe, 2026-05-17 at SHA `734e08e`):** Pi의 실제 error policy는
> **throw**입니다 — `normalizeHookError(error)` re-raises as
> `AgentHarnessError("hook")` (`agent-harness.ts:200-220, 232-250, 265-280`).
> `docs/hooks.md:355-365`의 `"continue"` default는 **design wishlist이지 shipped
> behavior가 아닙니다**.
>
> Aelix Sprint 3a v3 decision:
> 1. **Default `error_mode="throw"`** — shipped Pi와 동일.
> 2. **`error_mode="continue"`는 Aelix additive opt-in** — Pi `docs/hooks.md`의
>    future-design intent를 보존하되 Pi shipped behavior와 분기하지 않음.
> 3. **Lifecycle observational projection** (loop AgentEvent → HookEvent fan-out at
>    `harness/core.py:589-594, 620-621, 626-628`)은 swallow+log을 유지합니다.
>    Pi `subscribe()` listener-error behavior (`agent-harness.ts:649-660`)와 동일.
>
> 이 v3 reframe은 ADR-0019 Draft의 `"continue"` default를 **inverts**합니다. Draft는
> `docs/hooks.md` design notes를 shipped behavior로 잘못 읽은 결과였습니다.

### Default: `"throw"` (Pi parity)

모든 hook handler 예외는 reducer 내부 `_safe_invoke`에서 re-raise되어
`HookBus.emit`을 통해 호출 측으로 propagate됩니다. Aelix 호출 측 (harness bridges)
은 기존 `AgentHarnessError("hook")` wrapping을 그대로 유지합니다.

```python
async def _safe_invoke(handler, event, ctx, error_mode):
    try:
        return await _maybe_await(handler(event, ctx))
    except Exception as exc:
        if error_mode == "throw":
            raise
        _log.debug("hook handler raised (continuing): %r", exc, exc_info=True)
        return None
```

### per-handler opt-in: `error_mode="continue"` (Aelix additive divergence)

`error_mode="continue"`를 명시한 handler는 예외가 발생하면 `_log.debug` 후 swallow,
reducer는 다음 handler로 진행합니다. 이는 **Aelix-only additive feature**이며,
"Aelix additive divergence preserving Pi docs/hooks.md future-design intent"로
분류됩니다.

```python
pi.on("tool_call", handler, error_mode="continue")
harness.hooks.on("context", handler, error_mode="continue")
```

### 기존 테스트 호환 (P-2 reframe)

Default가 `"throw"`이므로 ADR-0014 Phase 1.2 행동과 동일합니다. 기존 mutation hook
throw 테스트 (`test_handler_raises_propagates_as_harness_error`)는 **변경 없이
계속 통과**합니다. 모든 162개 Sprint 2 baseline tests가 Sprint 3a 이후에도
통과합니다.

### Aelix additive divergence: lifecycle observational projection (W5 amendment)

W5 Pi-parity audit (2026-05-17 at SHA `734e08e`) verified that Pi's
`emitOwn` / `emitAny` at `agent-harness.ts:211-228` **re-throws ALL handler
exceptions** — both reducer-style mutation hooks AND `subscribe()` listeners.
Pi `subscribe()` (`agent-harness.ts:649-660`) is a separate listener channel,
not an exception-swallowing emit path.

Aelix `harness/core.py:589-612` currently **swallows + `_log.debug`** any
exception raised when fanning the loop `AgentEvent` out to the hook bus as
observational lifecycle events. This is an **Aelix additive divergence**
preserving Phase 1.2 listener stability (a faulty observer cannot break the
agent loop) — it is **NOT** Pi-parity behavior at this SHA.

The divergence is intentionally retained in Sprint 3a because:

1. Phase 1.2 listeners (`agent_start`, `turn_start`, message/tool lifecycle
   events) were designed under "observer cannot break the loop" semantics
   and the contract has been advertised to early Aelix consumers.
2. Pi emit sites for the harness-own events (`queue_update`, `abort`,
   `save_point`, etc.) do not land until Sprint 3b — so a full Pi-parity
   swap of the projection error policy is premature in Sprint 3a.

The divergence MUST be re-reviewed in Sprint 3b when the queue/lifecycle
emit sites for `queue_update`, `abort`, and `save_point` are wired. At that
point the choices are:

- (a) flip to Pi `emitOwn` re-throw semantics for harness-own projections
  (full Pi parity, breaking change for Phase 1.2 listener consumers), or
- (b) keep the swallow as an explicit additive divergence ADR (then this
  section graduates into its own dedicated ADR).

## Consequences

- `HookBus.on` 시그니처에 `error_mode: Literal["continue", "throw"] = "throw"` 추가
  (default = Pi parity).
- `ExtensionAPI.on` 동일한 kwarg 추가.
- `HookBus` 내부 dispatch는 per-handler `error_mode` map (`_error_modes`)을 통해
  분기하는 `_safe_invoke` 헬퍼를 사용합니다.
- 기존 mutation hook 호출부 (`_transform_context_bridge`, `_before_tool_call_bridge`,
  `_after_tool_call_bridge`, `_emit_before_agent_start`)의 `try/except` wrapping은
  default `"throw"` 행동을 보존하기 위해 그대로 유지됩니다.
- 신규 테스트: `tests/test_hook_error_mode_v3.py`
  - `test_default_error_mode_is_throw_pi_parity` — default propagation
  - `test_error_mode_continue_swallows_and_chain_continues` — continue 행동
  - `test_mixed_error_modes_*` — 같은 event에서 throw / continue 혼합
  - `test_continue_error_logged_via_aelix_hook_logger` — DEBUG log 검증
- 기존 테스트는 갱신 불필요 (default가 Pi와 동일하므로).
- ADR-0014는 `Superseded by ADR-0019 v3`로 변경되지만 default 행동은 동일합니다
  ("mutation throws" — Pi parity). 차이는 새로 추가된 `"continue"` opt-in입니다.
- Aelix 1차 원칙(Pi parity 회복) + 2차 원칙(디버깅 강점) 모두 충족합니다.

## Sprint 3d cross-link — partial-emit containment in `_execute_and_finalize`

Sprint 3d (ADR-0017 amendment) adds an Aelix-additive containment in
`_execute_and_finalize` where hook-handler exceptions raised during
`tool_execution_update` partial-emit are caught by the outer try/except and
converted to an `isError` tool result. This is consistent with the
per-handler `error_mode` opt-in policy documented above: by default the
handler raise propagates to `_execute_and_finalize` where containment
applies (the failing tool surfaces an `isError` result instead of tearing
down the in-flight batch); handlers wanting Pi-strict bare-throw can
register with `error_mode="throw"` (the default) and accept the
`isError` conversion as the local termination contract for that tool. No
change to the `HookBus`-level dispatch policy — containment is purely a
loop-side defensive boundary around the partial-emit fan-out so a
misbehaving observer cannot cancel sibling tools mid-batch.
