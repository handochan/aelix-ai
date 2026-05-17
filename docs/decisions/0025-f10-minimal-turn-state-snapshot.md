# 0025. F-10 Minimal Turn-State Snapshot Rationale

Status: Accepted (Phase 1.3 shipped)

## Context

Pi `AgentHarnessTurnState` (`agent-harness.ts:148-162`)는 9-field snapshot을
보유합니다:

```
messages, resources, streamOptions, sessionId,
systemPrompt, model, thinkingLevel, tools, activeTools
```

Aelix Phase 1.3 `_TurnState` (`harness/core.py:135-146`)는 2-field만 포함합니다:

```python
systemPrompt: str | None
model: str | None
```

차이 = 7 fields. Sprint 1 재평가(F-10)가 "chained-prompt leak" 문제를 식별했고,
Phase 1.3은 이를 해소하기 위해 `_TurnState`를 도입했습니다. 그러나 Pi의
9-field 전체를 즉시 채우지 않은 이유에 대한 명시적 기록이 없었습니다.

## Decision

Phase 1.3 `_TurnState`는 **의도적 minimal snapshot**입니다.

Sprint 1 재평가 F-10이 명시한 "chained-prompt leak" 하나만 fix하는 것이 목표였습니다.
나머지 7 fields는 해당 기능의 owning ADR이 land할 때 함께 확장합니다:

| Field | 확장 시점 | Owning ADR |
| --- | --- | --- |
| `tools` / `activeTools` | Phase 2.1 (8 setters land) | ADR-0017 |
| `streamOptions` | Phase 2.1 (`before_provider_request` land) | ADR-0017 |
| `resources` | Phase 2.1 (`resources_update` land) | ADR-0017 |
| `thinkingLevel` | Phase 2.1 (`thinking_level_select` land) | ADR-0017 |
| `messages` | Phase 2.2 (Session land) | ADR-0022 |
| `sessionId` | Phase 2.2 (Session land) | ADR-0022 |

- `messages` / `session_id` extension: **Sprint 4b owner** — Sprint 4a wires `AgentState.session_id` but defers `_TurnState.messages` from `session.build_context()` to Sprint 4b (compaction-driven reconstruction is what makes this meaningful).

이 결정은 Pi-parity superset을 목표로 하되, partial-aligned 상태로 출발하는
incremental approach를 선택합니다.

## Consequences

- Phase 1.3 코드는 단순하게 유지됩니다. `_TurnState`는 2-field로 충분합니다.
- Pi-parity superset이 아닌 partial-aligned 상태임을 명시합니다. 이는 버그가
  아니라 의도된 incremental 구현입니다.
- 향후 contributor가 `_TurnState`에 field를 추가할 때는 위 표의 owning ADR을
  cross-reference해야 합니다. ADR 없는 field 추가는 허용하지 않습니다.
- ADR-0027 (TaskGroup parallel tool execution) / ADR-0028 (entry_points
  auto-discovery) 같은 Aelix-only 개선과는 별개 트랙입니다.
- Pi의 9-field snapshot 완성은 ADR-0017 + ADR-0022 land 이후 자연스럽게
  달성됩니다. 별도 마이그레이션 sprint는 불필요합니다.
