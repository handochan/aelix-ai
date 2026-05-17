# 0022. Session Manager + JSONL Persistence

Status: Accepted (Sprint 4a / Phase 2.2.1 shipped — Session class + SessionStorage Protocol + JsonlSessionRepo + 8-variant PendingSessionWrite + message_end wiring)

## Context

Pi `AgentHarness`는 `Session` interface를 보유하고 JSONL append-only storage로
session을 persist합니다. `appendMessage`, `appendModelChange`,
`appendThinkingLevelChange`, `appendCompaction`, `appendCustomEntry`, `appendLabel`,
`moveTo` 등의 메서드로 session 이벤트를 기록합니다.

Phase 2.2.1 이전 Aelix는 in-memory only였습니다. agent 재시작 시 이전 session
context가 유실되고, compaction/branch_summary(ADR-0023)도 session persistence
없이 구현할 수 없습니다.

## Decision

### Session class + SessionStorage Protocol (P-13 shipped)

Sprint 4a Phase 2.2.1에서 `Session` **concrete class** (17+1 methods)와
`SessionStorage` **Protocol** (10 methods) 및 `JsonlSessionRepo` 구현을 추가합니다.

`Session`은 Protocol이 아닌 **concrete class**입니다:

```python
class Session:
    session_id: str
    phase: Literal["idle", "turn"]  # Sprint 4b에서 "compaction"/"branch_summary" 추가

    async def append_message(self, message: Message) -> None: ...
    async def append_model_change(self, model: Model) -> None: ...
    async def append_thinking_level_change(self, level: ThinkingLevel) -> None: ...
    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: dict | None = None,
        from_hook: bool = False,
    ) -> None: ...
    async def append_custom_entry(self, entry: dict) -> None: ...
    async def append_label(self, label: str) -> None: ...
    async def move_to(self, target_id: str) -> None: ...
    # ... 17+1 total methods
```

`SessionStorage`는 **Protocol** (10 methods):

```python
class SessionStorage(Protocol):
    async def create(self, session_id: str) -> Session: ...
    async def load(self, session_id: str) -> Session: ...
    async def list(self) -> list[str]: ...
    # ... 10 total methods
```

`append_compaction`은 5 params: `summary`, `first_kept_entry_id`, `tokens_before`,
`details?`, `from_hook?` — Sprint 4b compaction flow에서 fully populated됩니다.

### 저장소 위치

- 기본: `~/.aelix/sessions/{session_id}.jsonl`
- custom path 옵션: `AgentHarnessOptions.session_dir` 필드로 지정 가능.

### JSONL 형식

각 줄은 독립 JSON object입니다.

```json
{"type": "message", "ts": "2026-05-17T00:00:00Z", "message": {...}}
{"type": "model_change", "ts": "...", "model": "claude-opus-4-7"}
{"type": "compaction", "ts": "...", "summary": "...", "first_kept_entry_id": "...", "tokens_before": 42}
```

### 8-variant PendingSessionWrite (3 push sites + 5 defensive arms)

Sprint 4a는 `PendingSessionWrite` union을 8 variants로 ship합니다. P-12 finding
(spec §0.2)에 따라 **push site는 오직 3개**이고 나머지 5개는 `flush_pending_session_writes`
defensive switch arm으로만 존재합니다 (Pi `flushPendingSessionWrites` parity).
실제 ship된 union (`harness/core.py:192-277`):

| Variant | Push site | Defensive flush arm only? |
|---|---|---|
| `PendingMessageWrite` | `append_message` during turn (`harness/core.py:818`) | — |
| `PendingModelChangeWrite` | `set_model` during turn (`harness/core.py:623`) | — |
| `PendingThinkingLevelChangeWrite` | `set_thinking_level` during turn (`harness/core.py:647`) | — |
| `PendingCustomWrite` | (none) | defensive arm only |
| `PendingCustomMessageWrite` | (none) | defensive arm only |
| `PendingLabelWrite` | (none) | defensive arm only |
| `PendingSessionInfoWrite` | (none) | defensive arm only |
| `PendingLeafWrite` | (none) | defensive arm only |

Pi `PendingSessionWrite`는 TS conditional type (`types.ts:492-496`)이며 9 enumerated
variants가 아닙니다 — Pi `flushPendingSessionWrites`는 **8 concrete switch arms**를
처리하지만 push site는 3개뿐입니다 (P-12). Aelix는 동일 패턴으로 8 dispatcher
arms를 ship하고, 5 unreachable arms는 synthetic injection으로 테스트됩니다
(`tests/test_session_pending_writes_integration.py`).

(Sprint 3b W4에서 `PendingActiveToolsChangeWrite`가 추가되었으나 P-11 reversal로
삭제되었습니다. §"Removed claims" 참조.)

### message_end wiring

`MessageEndHookEvent` emit 시 `session.append_message(event.message)`를 먼저
await하고 그 후 hook fan-out이 실행됩니다. Pi `agent-harness.ts:484-486` 순서와
동일합니다. ADR-0017 §"Session message_end wiring" 참조.

## Aelix-additive divergences

Pi parity를 지향하되 아래 5가지 의도적 divergence가 있습니다:

1. **`session: Session | None` 허용** — Pi에서는 session이 항상 존재하지만,
   Aelix Phase 1/2 backward compat를 위해 `AgentHarnessOptions.session`이
   `None`일 수 있습니다. `None`이면 session 호출을 건너뛰고 hook emit만 실행합니다
   (Sprint 3b 동작 유지).

2. **In-process `asyncio.Lock`** — JSONL append 시 in-process `asyncio.Lock`을
   사용합니다. Pi는 lock 없이 O_APPEND 파일 시스템 원자성에만 의존합니다.
   단일 프로세스 내 concurrent append 안전성을 위한 Aelix-additive 보강입니다.

3. **`state.messages` in-memory mirror 유지** — Sprint 4a에서 `state.messages`는
   여전히 in-memory primary source입니다. Pi는 turn마다 `session.buildContext()`
   로 rebuild합니다. Sprint 4b에서 `state.messages`를 `session.build_context().messages`
   derived view로 전환합니다(§"Sprint 4a → 4b transition plan" 참조).

4. **`SessionStorage`가 `SessionError` 직접 raise** — Pi의 `Result<T, FileError>`
   ADT는 port하지 않습니다. Python idiomatic한 exception 방식을 선택합니다.

5. **`message_end` Session-append exception swallow** — `session.append_message(event.message)`가
   message_end emit path (`harness/core.py:1135-1144`)에서 raise하면, 예외는
   `_log.debug`로 기록되고 listener 또는 loop에 propagate되지 않습니다. Pi
   `agent-harness.ts:483-510`은 예외가 `handleAgentEvent`를 escape하도록 허용합니다.
   Aelix의 swallow는 no-session-fallback resilience 패턴과 일관됩니다 (Session을
   message_end emit path의 load-bearing dependency가 아닌 best-effort persistence로
   취급). Trade-off: Session I/O 실패 시 message는 어디에도 persist되지 않지만
   observational chain은 계속 진행됩니다. Phase 2.2.2 (Sprint 4b)에서 `state.messages`가
   derived view로 flip할 때 revisit 가능.

## Removed claims (P-11 reversal)

Sprint 3b W4 MAJOR-1은 `PendingActiveToolsChangeWrite` variant를 추가했습니다.
이는 **Pi에 존재하지 않는 fabricated claim**에 근거한 결정이었습니다.

- Pi `setActiveTools` (`agent-harness.ts:875-882`)는 `pendingSessionWrites`에
  push하지 않습니다 — SHA `734e08e`에서 검증 완료.
- Sprint 3b W4 MAJOR-1 verdict는 supersede됩니다. 해당 reviewer가 fabricate한
  Pi claim으로 variant + push site를 추가했습니다.
- Sprint 4a에서 variant 및 push site를 삭제했습니다.
- `test_session_pending_writes_integration.py`가 regression guard 역할을 합니다
  — `PendingActiveToolsChangeWrite`를 재도입하면 테스트가 실패합니다.
- Pi에서 active-set restoration은 harness 생성 시 `options.activeToolNames`로
  이루어집니다. session replay 방식이 아닙니다.

## Sprint 4a → 4b transition plan (COMPLETED in Sprint 4b)

All Sprint 4a deferred items landed in Sprint 4b:
- ✅ `compact()` + `navigate_tree()` methods on AgentHarness
- ✅ Phase Literal expansion: `"idle" | "turn" | "compaction" | "branch_summary"`
- ✅ 4 `session_*` emit sites active (see ADR-0017 Sprint 4b subsection)
- ✅ `JsonlSessionRepo.fork` shipped (Pi parity `jsonl-repo.ts:103-127`)
- ✅ `state.messages` source-flip: derived from `session.build_context()` when session attached; in-memory fallback when None
- ✅ `_TurnState` extended with `messages` + `session_id` per ADR-0025

Phase 2.2 closure: ADR-0040 documents the full superset.

## Consequences

- 모노레포(ADR-0015)에서 `packages/aelix-agent-core/session/` 모듈로 위치합니다.
- `AgentHarness` constructor에 `session: Session | None = None` 추가.
  `None`이면 session wiring을 건너뜁니다(기존 behavior 유지).
- `pendingSessionWrites` queue: harness busy(turn/compaction) 중 session write
  요청을 큐잉하고, idle 전이 시 flush합니다.
- Phase 2.2 compaction/branch_summary(ADR-0023)는 이 `SessionStorage` Protocol에
  의존합니다.
- `AgentHarnessOptions` missing fields(`session`, `env`, `resources` 등)를 함께
  추가합니다(재평가 보고서 F-6).
- `SessionStorage`가 `SessionError`를 raise합니다 (Python exception 방식).
- `tests/test_session_pending_writes_integration.py`가 P-11 lockdown regression
  guard로 동작합니다.
