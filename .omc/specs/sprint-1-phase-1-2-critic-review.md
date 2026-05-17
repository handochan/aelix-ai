# Critic Review — Sprint 1 · Phase 1.2 Spec

Reviewer: oh-my-claudecode:critic (opus)
Date: 2026-05-17
Verdict: **REQUEST_MAJOR_REVISIONS**

## 1. Verdict summary

Spec의 골격은 좋지만 (A1~A7의 Pi reverse-engineering, B8의 layering 결정, F의 deferred ADR 정리), 다음 5개 CRITICAL이 implementation을 막습니다.

## 2. Critical issues (must fix before implementation)

### CR-1. `@dataclass(frozen=False)` `ToolCallHookEvent`이 `frozen=True` `HookEvent`를 상속 → Python `TypeError` at class creation
- 위치: Spec D 393–476
- 사실: Python은 `cannot inherit non-frozen dataclass from a frozen one`을 raise. `harness/hooks.py` import 자체 실패.
- 권장 fix: **모든 hook event를 `frozen=True`로 통일**. args mutation은 dict 자체의 mutability에 의존 (dataclass instance는 frozen, dict 객체는 mutable).

### CR-2. `HookHandler(Protocol[TResult])` does not narrow — Pi-parity static typing 안 됨
- 위치: D 399–404 + B1 142
- 사실: `Protocol[T]`는 invariant. handler narrowing은 `Protocol` generic이 아니라 27개 `@overload` 선언이 책임짐.
- 권장 fix: handler 내부 type은 `Callable[[HookEvent, ExtensionContext], Any]`. `ExtensionAPI.on`과 `HookBus.on`에 16개 `@overload` 선언으로 narrowing. R9를 gate화: 30-line pyright spike 먼저.

### CR-3. `tool_call` reducer의 `reason`은 block=True에만 효과 — "last truthy 반환" 약속은 silent drop
- 위치: B6 row "tool_call"
- 사실: bridge code (`_apply_after_override` in `agent/loop.py:333`)가 `decision.block`만 검사. `block=False` + `reason="audit"`은 loop에 보이지 않음.
- 권장 fix: B6 명시: "last-truthy reducer return은 observational only; `block=True`만 loop 동작에 영향. `reason`은 block 시에만 surface." Test `test_reducer_tool_call_non_block_reason_is_observational_only` 추가.

### CR-4. R3의 fix가 D와 모순 — `ExtensionContext`는 Protocol인데 R3는 `__getattribute__` 권장
- 위치: D 636 (`class ExtensionContext(Protocol)`) vs R3
- 사실: Protocol에 `__getattribute__` override 무의미 (사용자가 instantiate 안 함).
- 권장 fix: D에서 **`ExtensionContext`를 concrete class로 변경**, `__getattribute__`에서 stale 검사 강제.

### CR-5. Callback↔hook bridge가 in-place `args` mutation을 loop에 propagate하는 contract 미정
- 위치: A7, B7, `src/aelix/agent/loop.py:323-394`
- 사실: 현재 코드는 `args = await validate_tool_arguments(tool, dict(tc.input))` (shallow copy). bridge가 `ToolCallHookEvent(args=before_ctx.args, ...)` 같은 reference 동일성을 보장해야 mutation이 보임. 무명시.
- 권장 fix: B7에 명시 — "The `ToolCallHookEvent.args` 참조는 `tool.execute()` 및 `after_tool_call` callback에 전달되는 `dict`와 same reference. Defensive copying는 Pi parity 깨짐." Tests:
  - `test_tool_call_hook_arg_mutation_visible_to_tool_execute`
  - `test_tool_call_hook_arg_mutation_visible_to_after_tool_call`

## 3. High-Priority issues

### H-1. Patch surface 2개 (`AfterToolCallResult` vs `ToolResultPatch`) 통합 미정
- 권장 fix: `harness/hooks.py`에서 `from aelix.agent.types import AfterToolCallResult as ToolResultPatch` alias. Composition order 명시 (hook patches FIRST, app callback AFTER).

### H-2. `LoadExtensionsResult`에 `runtime` 필드 누락
- 권장 fix: `LoadExtensionsResult(extensions, errors, runtime: _ExtensionRuntime)` 추가. Pi: one runtime per `load_extensions` call.

### H-3. `AgentHarnessOptions`에 `runtime` 필드 누락 → bind 불가
- 권장 fix: `runtime: _ExtensionRuntime | None = None` 추가. None이면 harness가 empty runtime 생성.

### H-4. `load_extensions(paths)`가 `Extension` instance 직접 받지 못함 — `PolicyExtension()` instance가 factory와 동일 분기로 통합 필요
- 권장 fix: B11 "직접 instance 전달" 제거. `await load_extensions([PolicyExtension(), GuardrailExtension()])` 같이 factory list 항상 사용. `ExtensionFactory`는 callable이므로 instance도 받음 (`PolicyExtension`은 `__call__` 보유).

### H-5. `harness/hooks.py` ↔ `extensions/api.py` 순환 import
- 권장 fix: `TYPE_CHECKING` guard로 forward-ref. C section에 명시.

### H-6. `steer()` / `follow_up()` during idle behavior 미정
- 결정: **Pi parity — idle 시 큐에 enqueue, 다음 prompt가 봄**. Test `test_steer_when_idle_raises_invalid_state` 이름은 잘못 — `test_steer_when_idle_enqueues_for_next_prompt`로 변경.

### H-7. `HookEvent[TResult]` subclass의 forward-ref string은 narrowing 불가
- 권장 fix: `class ContextHookEvent(HookEvent[ContextResult])` (string 제거). `from __future__ import annotations` 이미 있음. pyright spike 먼저.

### H-8. Phase 1.1 `Agent` regression test 없음
- 권장 fix: `tests/test_agent_regression.py`에 `test_agent_signature_unchanged`, `test_agent_has_no_hooks_attribute_after_harness_import` 추가.

### H-9. End-to-end integration test 부족
- 권장 fix: `test_loop_with_hooks.py`에 추가:
  - `test_full_chain_policy_blocks_real_tool_call`
  - `test_full_chain_guardrail_blocks_rm_rf_in_real_loop`
  - `test_full_chain_two_extensions_one_patches_args_other_blocks`

### H-10. Concurrency / ordering 테스트 부족
- 권장 fix: `tests/test_hooks.py`에 추가:
  - `test_handler_spawning_task_does_not_corrupt_handler_ordering`
  - `test_slow_handler_followed_by_fast_handler_completes_in_registration_order`

## 4. Medium-priority issues

- **M-1**: `BeforeProviderRequestHookEvent` Phase 1.2 scope에서 제외 (stream_simple stub이라 reducer 호출처 없음). Phase 2 provider 작업과 함께 추가.
- **M-2**: `MessageEndHookEvent` docstring에 "observational only, reducer는 ADR-0013에서 결정" 명시.
- **M-3**: `HookBus.emit`에 `has_handlers + observers` 빈 케이스 short-circuit.
- **M-4**: `AgentHarness.dispose()` during turn = `abort()` 먼저 + `wait_for_idle()` 대기 + cleanup LIFO.
- **M-5**: `HookEventName` Literal과 `HOOK_RESULT_TYPES` registry sync 검증 테스트 `test_hook_event_name_literal_matches_hook_result_types_keys`.
- **M-6**: Pi `event.input` → Aelix `event.args` 의도적 rename — B6에 명시.
- **M-7**: Guardrail regex 강화: `rm -fr`, 절대 경로 `/bin/rm`, eval-wrapped 케이스 테스트 추가.
- **M-8**: Implementation order 단계 4 (harness tests)와 5 (builtin)의 의존 — builtin 먼저 또는 harness test에 inline stub.
- **M-9**: R8 (tool name collision)을 결정으로 — application-supplied wins. Test 추가.

## 5. Low-priority nits

- N-2: `convert_to_llm` 타입을 `agent/types.py:214`와 동일하게.
- N-5: `HookCleanup` 타입 narrow.

## 6. Strengths

- A1~A7 Pi reverse-engineering 정확.
- B8 layering 결정 적절 (ADR-0004 정합).
- B10 silent block 결정 (no `ctx.ui` 현실 반영).
- F deferred ADR 표 proactive.

## 7. Recommended next step

5 CR + 핵심 H (H-1, H-2, H-3, H-4, H-5)를 spec에 통합한 뒤 implementation 시작. 나머지 H/M는 implementer prompt에 첨부.

## 8. Risk re-rating

- R3 → CRITICAL (was High)
- R4 → CRITICAL (was Medium)
- R9 → HIGH gate (was Medium)
- R8 → HIGH (was Medium)
- **New R13**: callback↔hook arg-mutation contract — HIGH
- **New R14**: 순환 import — MEDIUM (TYPE_CHECKING fix)
- **New R15**: ToolResultPatch vs AfterToolCallResult — HIGH

## 9. Open questions

- `AgentHarness.subscribe()` listener가 `AgentEvent`(10) vs wider `AgentHarnessEvent`(queue_update, settled 포함)?
- `GuardrailExtension.additional_patterns`가 default rule 동명 override or additive?
- `before_provider_payload` Phase 1.2 reachable? (stream_simple stub이라 no)
- `register_flag`/`get_flag`가 CLI parser 없이 어떻게 wire?
