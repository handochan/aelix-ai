# 0022. Session Manager + JSONL Persistence

Status: Draft (Phase 2.2)

## Context

Pi `AgentHarness`는 `Session` interface를 보유하고 JSONL append-only storage로
session을 persist합니다. `appendMessage`, `appendModelChange`,
`appendThinkingLevelChange`, `appendCompaction`, `appendCustomEntry`, `appendLabel`,
`moveTo` 등의 메서드로 session 이벤트를 기록합니다.

현재 Aelix는 in-memory only입니다. agent 재시작 시 이전 session context가 유실되고,
compaction/branch_summary(ADR-0023)도 session persistence 없이 구현할 수 없습니다.

## Decision

Phase 2.2에서 `Session` interface와 `JsonlSessionRepo` 구현을 추가합니다.

### Session interface (Pi parity)

```python
class Session(Protocol):
    session_id: str

    async def append_message(self, message: Message) -> None: ...
    async def append_model_change(self, model: Model) -> None: ...
    async def append_thinking_level_change(self, level: ThinkingLevel) -> None: ...
    async def append_compaction(self, summary: str) -> None: ...
    async def append_custom_entry(self, entry: dict) -> None: ...
    async def append_label(self, label: str) -> None: ...
    async def move_to(self, target_id: str) -> None: ...
```

### 저장소 위치

- 기본: `~/.aelix/sessions/{session_id}.jsonl`
- custom path 옵션: `AgentHarnessOptions.session_dir` 필드로 지정 가능.

### JSONL 형식

각 줄은 독립 JSON object입니다.

```json
{"type": "message", "ts": "2026-05-17T00:00:00Z", "message": {...}}
{"type": "model_change", "ts": "...", "model": "claude-opus-4-7"}
{"type": "compaction", "ts": "...", "summary": "..."}
```

## Consequences

- 모노레포(ADR-0015)에서 `packages/aelix-agent-core/session/` 모듈로 위치합니다.
- `AgentHarness` constructor에 `session: Session | None = None` 추가.
  `None`이면 in-memory no-op session을 사용합니다(기존 behavior 유지).
- `pendingSessionWrites` queue를 도입합니다: harness busy(turn/compaction) 중
  session write 요청을 큐잉하고, idle 전이 시 flush합니다.
- Phase 2.2 compaction/branch_summary(ADR-0023)는 이 Session interface에 의존합니다.
- `AgentHarnessOptions` missing fields(`session`, `env`, `resources` 등)를 함께
  추가합니다(재평가 보고서 F-6).
