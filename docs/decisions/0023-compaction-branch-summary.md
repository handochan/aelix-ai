# 0023. Compaction + Branch Summary

Status: Draft (Phase 2.2)
Supersedes (partial): ADR-0016 deferred (Phase machine expansion)

## Context

Pi `AgentHarness`는 `compact()`, `navigateTree()` 메서드를 보유하고
`session_before_compact` / `session_compact` / `session_before_tree` /
`session_tree` hook을 emit합니다. Phase machine은 `idle | turn | compaction |
branch_summary` 상태를 가집니다(`retry`는 Pi에서 declared but unused).

ADR-0016은 "compaction/branch_summary 도입 시점 미정"으로 Phase machine 확장을
deferred했습니다. 1차 원칙(Pi parity)에 따라 Phase 2.2에 명시합니다.

현재 Aelix Phase machine은 `idle | turn`만 구현합니다. `session_before_compact`
event class는 Phase 1.2에 정의되어 있으나 emit site가 없습니다.

## Decision

Phase 2.2에서 다음을 구현합니다.

### `AgentHarness.compact(custom_instructions?)`

1. `session_before_compact` hook emit (mutation reducer — `cancel=True` 반환 시 short-circuit).
2. Phase → `compaction`.
3. `pendingSessionWrites` queue flush (ADR-0022).
4. Summarization: LLM call로 현재 context 요약.
5. `session_compact` hook emit (observational).
6. Session에 `append_compaction(summary)` 기록 (ADR-0022).
7. Phase → `idle`.

### `AgentHarness.navigate_tree(target_id, options?)`

1. `session_before_tree` hook emit (mutation reducer — `cancel=True` 시 short-circuit).
2. Phase → `branch_summary`.
3. Branch summary 생성.
4. `session_tree` hook emit (observational).
5. Session에 branch 전환 기록 (ADR-0022).
6. Phase → `idle`.

### Phase machine 확장

```python
AgentPhase = Literal["idle", "turn", "compaction", "branch_summary"]
# Pi의 "retry"는 declared but unused → Aelix는 처음부터 포함하지 않음.
```

## Consequences

- ADR-0016 deferred 종료 — 이 ADR로 supersede합니다.
- `pendingSessionWrites` queue: harness busy 중 session write 큐잉, idle 전이 시 flush.
  ADR-0022 Session Manager와 함께 구현합니다.
- 모노레포(ADR-0015)에서 `packages/aelix-agent-core/harness/compaction.py` 위치.
- ADR-0017 v2 catalogue의 `session_before_compact` / `session_compact` /
  `session_before_tree` / `session_tree` emit site를 여기서 추가합니다.
- 신규 테스트:
  - `test_compact_emits_session_before_compact`
  - `test_compact_cancel_stops_compaction`
  - `test_navigate_tree_summary_short_circuits_on_cancel`
