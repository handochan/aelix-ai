# Sprint 1 · Phase 1.2 Spec: Hooks + ExtensionAPI + Built-in Policy/Guardrail

Status: Draft
References: ADR-0002, ADR-0003, ADR-0004, ADR-0007, ADR-0008, ADR-0009
Pi reference commit: badlogic/pi-mono (main, fetched 2026-05)

## 0. Goal and non-goals

Phase 1.2는 Phase 1.1의 low-level loop 위에 **hook 시스템 + ExtensionAPI + built-in policy/guardrail extension**을 얹습니다. 결과적으로:

- `Agent`는 그대로 동작합니다 (Phase 1.1 호출자 비호환 0).
- 새 `AgentHarness`가 hook 보유자/extension 통합 책임을 가집니다.
- `agent_loop`은 기존 `before_tool_call` / `after_tool_call` callback 시그니처를 유지하되, `AgentHarness`는 그 callback 안에서 hook을 emit합니다.
- Built-in `PolicyExtension`과 `GuardrailExtension`은 hook subscriber로 동작 (core가 강제하지 않음 → ADR-0004).

**Non-goals (out of scope for 1.2):**
- Compaction / branch summary / phase machine 전체 (idle/turn만).
- UI primitives (`ctx.ui.*` — Pi의 TUI 의존).
- Session storage, jsonl persistence.
- Marketplace / discovery 정식 구현 (stub만).
- Multi-agent orchestration.

---

## A. Pi-side analysis

### A1. Hook event typing — phantom result

Pi는 declaration merging + `unique symbol`로 event type에 result type을 phantom으로 첨부합니다.

```ts
// /tmp/pi-hooks.md:11-19
declare const HookResult: unique symbol;
interface HookEvent<TType extends string, TResult = void> {
    type: TType;
    readonly [HookResult]?: TResult;
}
type ResultOf<E> = E extends { readonly [HookResult]?: infer R } ? R : void;
```

이렇게 하면 `pi.on("tool_call", handler)`에서 handler의 return type이 컴파일 타임에 `{ block?: boolean; reason?: string }`로 추론됩니다.

**Observational vs result-producing**: `observe()`는 모든 event를 보지만 return 값이 무시되고, `on(type, handler)`는 그 event의 reducer에 참여합니다 (`/tmp/pi-hooks.md:83-88`). 실제 production 구현 (`agent-harness.ts`)에서는 phantom 대신 별도 `AgentHarnessEventResultMap` map type을 쓰지만 (`/tmp/pi-harness-types.ts:692-711`), design doc 권장은 phantom입니다.

### A2. Hook registration + cleanup

`hooks.on(name, handler)`는 **unsubscribe 함수를 반환**합니다 (`/tmp/pi-hooks.md:66-70`, `/tmp/pi-harness.ts:981-994`):

```ts
on<TType>(type, handler): () => void {
    let handlers = this.handlers.get(type);
    if (!handlers) { handlers = new Set(); this.handlers.set(type, handlers); }
    handlers.add(handler);
    return () => handlers.delete(handler);
}
```

- Source metadata (`{source, cleanup}` opts)는 design doc에서 "Probably needed"로 언급 (`/tmp/pi-hooks.md:370-378`)되지만 현재 `AgentHarness` 구현엔 없음 — extension runner 쪽 `ext.handlers` Map이 그 역할을 함 (`/tmp/pi-ext-types.ts:1542`).
- Ordering: 같은 event의 handler들은 **insertion order**로 실행 (`Set` 순회) (`/tmp/pi-hooks.md:172-180`, runner `/tmp/pi-ext-runner.ts:684-712`는 extension 순회 → 각 extension의 handler 순회).
- Cleanup: 별도 `addCleanup(cleanup)` API (`/tmp/pi-hooks.md:76`, `/tmp/pi-harness.ts:99`); `clear()`/`dispose()`가 호출됨.

### A3. Reducer table (Pi 구현 그대로)

`AgentHarness.emit*` 메서드와 `ExtensionRunner.emit*` 메서드를 합쳐 정리합니다.

| Event | Result type | Aggregation rule | 참조 |
|---|---|---|---|
| `context` | `{ messages: AgentMessage[] }` | Sequential transform. 각 handler가 messages를 patch. 마지막 messages가 다음 handler 입력. (`/tmp/pi-hooks.md:170-180`, `/tmp/pi-ext-runner.ts:858-888`) |
| `before_provider_request` | `{ streamOptions?: patch }` | Sequential patch. `applyStreamOptionsPatch` 적용 (header merge, undefined deletes). (`/tmp/pi-harness.ts:250-274`) |
| `before_provider_payload` | `{ payload: unknown }` | Sequential replace. payload != undefined이면 current = result.payload. (`/tmp/pi-harness.ts:276-291`) |
| `before_agent_start` | `{ messages?, systemPrompt? }` | Collect messages from all handlers + chain systemPrompt (각 handler가 이전 chain된 prompt를 봄). (`/tmp/pi-hooks.md:200-216`, `/tmp/pi-ext-runner.ts:924-988`) |
| `tool_call` | `{ block?, reason? }` | Sequential, **early exit on `result.block === true`**. (`/tmp/pi-hooks.md:222-227`, `/tmp/pi-ext-runner.ts:806-827`). Pi runner의 `event.input`은 mutable — handler가 in-place patch 가능 (re-validation 없음). |
| `tool_result` | `{ content?, details?, isError?, terminate? }` | Sequential patch accumulation. 각 handler가 직전 patch를 봄. `terminate`는 production runner엔 없고 harness 쪽에만 존재. (`/tmp/pi-hooks.md:236-254`, `/tmp/pi-ext-runner.ts:756-804`) |
| `session_before_*` (compact/tree/switch/fork) | `{ cancel?, ... }` | Sequential, **early exit on `result.cancel === true`**, else last truthy wins. (`/tmp/pi-hooks.md:262-271`, `/tmp/pi-ext-runner.ts:692-697`) |
| `message_end` | `{ message?: AgentMessage }` | Sequential replace, **role must match original** (runner emits error else skips). (`/tmp/pi-ext-runner.ts:714-754`) |
| `input` | `{ action: continue/transform/handled }` | Sequential transform; `handled`는 short-circuit. (`/tmp/pi-ext-runner.ts:1039-1067`) |
| `user_bash` | `{ operations?, result? }` | First-truthy-wins. (`/tmp/pi-ext-runner.ts:829-856`) |
| `resources_discover` | `{ skillPaths?, promptPaths?, themePaths? }` | Aggregate all paths from all handlers; extension source 보존. (`/tmp/pi-ext-runner.ts:990-1036`) |
| 모든 lifecycle events (`agent_start`, `turn_*`, `message_*`, `tool_execution_*`, `model_select`, `queue_update`, `save_point`, `abort`, `settled`, `session_*`) | `undefined` | Observational. Return 무시. (`/tmp/pi-harness-types.ts:692-711`의 `AgentHarnessEventResultMap`은 이런 event들을 `undefined`로 둠) |

### A4. ExtensionAPI surface

`/tmp/pi-ext-types.ts:1084-1311`의 `ExtensionAPI` 인터페이스 정리:

**Subscription**: `on(event, handler)` — 27개 event overload (resources_discover, session_*, context, before_provider_request, after_provider_response, before_agent_start, agent_start/end, turn_start/end, message_start/update/end, tool_execution_*, model_select, thinking_level_select, tool_call, tool_result, user_bash, input).

**Registration (mutates `extension`)**: `registerTool`, `registerCommand`, `registerShortcut`, `registerFlag`, `registerMessageRenderer`, `registerProvider`, `unregisterProvider`.

**Flag access**: `getFlag(name)`.

**Actions (delegate to runtime, throw before bindCore)**: `sendMessage`, `sendUserMessage`, `appendEntry`, `setSessionName`, `getSessionName`, `setLabel`, `exec`, `getActiveTools`, `getAllTools`, `setActiveTools`, `getCommands`, `setModel`, `getThinkingLevel`, `setThinkingLevel`.

**Bus**: `events: EventBus` — cross-extension pub/sub.

Loader가 throwing stub으로 시작 → runner `bindCore`가 실제 구현 주입 (`/tmp/pi-ext-loader.ts:149-195`, `/tmp/pi-ext-runner.ts:266-336`).

### A5. Extension lifecycle

1. **Load**: `loadExtensions(paths)` (`/tmp/pi-ext-loader.ts:437`) → 각 extension에 대해 `createExtension()` (`/tmp/pi-ext-loader.ts:373`) + `createExtensionAPI()` (`/tmp/pi-ext-loader.ts:202`) → `await factory(api)` (`/tmp/pi-ext-loader.ts:409`).
2. **Bind**: `ExtensionRunner.bindCore(actions, contextActions, providerActions)` (`/tmp/pi-ext-runner.ts:266`) — action stub들이 실제 구현으로 교체, pending provider registration flush.
3. **bindUI / bindCommandContext** — Aelix 1.2에서 생략.
4. **Active**: `emit*()` 메서드들이 `ext.handlers.get(type)`을 순회.
5. **Stale ctx detection**: `runtime.invalidate(msg)`가 `staleMessage` 저장. `runtime.assertActive()`가 모든 ExtensionAPI 메서드 진입점에서 throw. `createContext()`의 모든 getter는 lazy property — 캡처된 ctx가 stale 검사를 거치게 함 (`/tmp/pi-ext-runner.ts:573-633`).
6. **Shutdown**: `emitSessionShutdownEvent` (`/tmp/pi-ext-runner.ts:180-189`).

### A6. AgentHarness phase machine

`AgentHarnessPhase = "idle" | "turn" | "compaction" | "branch_summary" | "retry"` (`/tmp/pi-harness-types.ts:490`).

- `idle`: 새 prompt/skill/compact/navigateTree 허용.
- `turn`: `prompt()`/`skill()`/`promptFromTemplate()` 진행 중. 새 `prompt()`는 `busy` error (`/tmp/pi-harness.ts:604`).
- `compaction`: `compact()` 진행 중; `prompt()` 거부.
- `branch_summary`: `navigateTree()` 진행 중; `prompt()` 거부.
- `retry`: declared but no occurrences in current harness.ts (제거 예정).

**Mutations queue**: `prepareNextTurn`에서 `flushPendingSessionWrites()`를 호출하여 turn boundary에서만 session write 적용 (`/tmp/pi-harness.ts:439-448`, `:459-481`). `setModel`/`setThinkingLevel` 등은 phase != idle이면 `pendingSessionWrites`에 push.

### A7. Loop ↔ harness integration

`pi-agent-core`의 `runAgentLoop`은 **hook 자체를 모름**. `AgentLoopConfig`의 callback (`beforeToolCall`, `afterToolCall`, `transformContext`, `prepareNextTurn`, `shouldStopAfterTurn`, `getSteeringMessages`, `getFollowUpMessages`)을 받을 뿐입니다 (Aelix 쪽 `agent/types.py:206`와 동일 구조).

`AgentHarness.createLoopConfig` (`/tmp/pi-harness.ts:403-452`)이 이 callback들 안에서 `this.emitHook({type: "context", ...})` 등을 호출합니다. 즉:

```
runAgentLoop -> beforeToolCall callback -> harness.emitHook("tool_call") -> handler들 -> BeforeToolCallResult 변환 반환
```

**이 분리가 핵심**입니다 — Aelix도 동일하게 `agent_loop`는 callback만 알고, `AgentHarness`가 callback ↔ hook 변환을 담당.

---

## B. Python adaptation decisions

### B1. Phantom result type → **Generic `HookEvent` Protocol with explicit registry**

**Options:**
- (a) `class HookEvent(Generic[TResult])` Protocol + `@dataclass` events declaring class-level `__result__` ClassVar.
- (b) `TypedDict` per event.
- (c) Runtime registry: `dict[str, type[Result]]`.
- (d) Pi-style phantom via `typing.cast` shenanigans.

**Recommendation: (a) + (c).** Public events are `@dataclass(frozen=True)` and inherit from `HookEvent[ResultT]`. A module-level `HOOK_RESULT_TYPES: dict[str, type]` registry exists for runtime introspection. `pi.on("tool_call", handler)`는 `Literal["tool_call"]` overload로 handler 시그니처를 type checker가 좁힙니다.

**Reasoning:** Python typing은 TS의 phantom과 1:1 매칭이 안 됨. Generic 클래스 + Literal overload가 가장 자연스러운 IDE/mypy 체험을 줌. ADR-0009 (Python-first SDK)는 외부 sound type system을 요구하진 않지만, IDE 자동완성/mypy 통과는 1차 목표. 런타임 registry는 reducer dispatch + test introspection에 유용.

### B2. Hook handler return contract → **sync OR async, `None` means "no opinion"**

**Recommendation:** Handler signature는 `Callable[[E, ExtensionContext], R | None | Awaitable[R | None]]`. Reducer는 `inspect.isawaitable(result)`로 await 여부 결정 (이미 `agent/loop.py:430-433`의 `_maybe_await` 패턴 재사용).

`None` (또는 sync handler가 implicit `None`) = "이 handler는 결과를 안 냄, 다음 handler가 봐야 함". 이는 Pi 시그니처 (`/tmp/pi-hooks.md:21-25`, `void`도 허용)와 동일.

**Trade-off:** sync/async 혼합은 thread-safety 이슈가 없으면 안전 (단일 event loop). 명시적인 "no-op" sentinel을 두면 type 명확성은 올라가지만 API가 무거워짐 — Pi가 이미 `void | undefined` 두 가지를 동등 취급하니 따라감.

### B3. Reducer registration → **built-in to `HookBus`, not pluggable**

**Options:**
- (a) Pi-style: `emit()` switch on event type. Built-in.
- (b) Pluggable: extension이 reducer를 등록.

**Recommendation: (a).** Phase 1.2의 7개 reducer (context, before_provider_request, before_provider_payload, before_agent_start, tool_call, tool_result, session_before_*) 만 built-in. Pluggable reducer는 ADR-0008 (orchestration is extension responsibility)에서 다룰 multi-agent 영역.

**Reasoning:** ADR-0002 (small kernel)와 어긋나지 않음 — reducer rule은 hook event "semantics" 일부이며 kernel이 정의해야 일관성 보장. Pluggable reducer를 허용하면 extension 마다 다른 ordering rule로 디버깅 지옥.

### B4. ExtensionAPI factory signature → **top-level `def setup(aelix)` function**

**Options:**
- (a) Top-level `def setup(aelix: ExtensionAPI) -> None | Awaitable[None]`.
- (b) Class with `def setup(self, aelix)`.
- (c) `entry_points` group.
- (d) Decorator.

**Recommendation: (a) `setup`.** 모듈을 import하면 `setup` callable을 찾고 호출. Pi의 `export default function` 1:1 대응. Async도 허용 (`inspect.iscoroutinefunction` 검사).

**Reasoning:** Pi parity (ADR-0003), Python 관용에 맞음 (`Flask`의 `create_app`, pytest plugin entry 등 유사 패턴). Class는 ceremony 추가 비용. `entry_points`는 packaging 동반 — Phase 1.2 스코프 외 (B11 참조).

### B5. Event name typing → **`Literal[...]` union + runtime validation**

**Recommendation:** `HookEventName = Literal["context", "tool_call", ...]`. `ExtensionAPI.on`은 `@overload`로 각 literal에 대응. 런타임에서 `event not in HOOK_RESULT_TYPES`이면 `KeyError("Unknown hook event: ...")` raise (Pi는 silently empty handler set 만들지만, Python에서는 typo 방어가 더 가치 있음).

**Trade-off:** Pi는 unknown event도 받아들임 (custom 확장 가능). Aelix 1.2에서는 strict하게 시작, 이후 ADR로 "custom event registration" 별도 결정 가능. Strict가 implementer/소비자에게 친절.

### B6. Reducer aggregation rules (Phase 1.2 ported verbatim)

| Aelix event | Rule (verbatim from Pi) |
|---|---|
| `context` | Sequential. 각 handler가 `ContextResult(messages=[...])` 반환 시 다음 handler에 patch된 messages 전달. 마지막 result 반환; modify된 적 없으면 `None`. |
| `before_provider_request` | Sequential patch. `StreamOptionsPatch` — header/metadata는 dict merge, `None`값은 key 삭제, 명시적 `headers=None`은 전체 클리어. |
| `before_provider_payload` | Sequential replace. result.payload != None이면 current = result.payload. |
| `before_agent_start` | Collect injected messages (모두 누적); systemPrompt는 chain (handler N이 N-1의 prompt를 봄). 변경 없으면 None. |
| `tool_call` | Sequential, **first `result.block == True` short-circuits**. block 안 했어도 last truthy result 반환 (reason 등 전달 위함). `event.args` mutability — Aelix는 `dict[str, Any]`로 mutable, in-place patch 후 다음 handler가 봄, no re-validation. |
| `tool_result` | Sequential patch accumulation. content/details/is_error/terminate 중 None 아닌 것만 적용. modified=True인 경우만 반환. |
| `session_before_*` | Sequential, `result.cancel == True`에서 short-circuit, else last truthy wins. (Phase 1.2엔 session 없으니 reducer rule만 정의해두고 사용 없음.) |
| `message_end` (Phase 1.2엔 reducer 없음; observational만) | Pi runner에서는 reducer가 있지만 Aelix 1.2 출시엔 observational로 시작. 차후 ADR-0013에서 결정. |

`__all__`에 reducer 함수 export하지 않음 (internal). Test가 직접 reducer를 호출하려면 `from aelix.harness.hooks import _reducer_context as reducer_context` 같은 private 노출 OK.

### B7. Migration of `before_tool_call` / `after_tool_call` callbacks → **(a) keep callbacks AND emit hooks**

**Options:**
- (a) `agent/loop.py`는 callback 그대로. `AgentHarness`가 callback에서 hook을 emit. 직접 `agent_loop`을 쓰는 caller는 hook 없이 callback만 사용 가능. Phase 1.1 테스트 영향 없음.
- (b) callback 제거, `AgentHarness` 경유 필수.

**Recommendation: (a).** Phase 1.1의 `tests/test_agent_loop.py`가 `before_tool_call`/`after_tool_call`을 직접 config에 주입해 검증함 (예: `tests/test_agent_loop.py`에서 사용). 이를 깨면 ADR-0002 (small kernel — loop 자체는 hook 모름)와 어긋나고 회귀 부담.

**Reasoning:**
- ADR-0008은 single agent loop가 core, orchestration이 extension이라고 함. `agent_loop`는 core. Hook system은 core/extension 경계 위에 있는 별도 layer.
- Pi가 정확히 이 분리를 함 (A7 참조).
- 직접 callback path는 advanced 사용자/테스트에 유용 — hook overhead 0.

### B8. AgentHarness vs Agent layering → **add `AgentHarness` as a new layer; `Agent` unchanged**

**Recommendation:** 
- `Agent` (Phase 1.1)는 그대로. Phase 1.1 사용자/테스트 비호환 0.
- 새 `AgentHarness` 클래스가 `agent_loop` 위에 위치. Extension hook을 보유.
- `Agent`는 1.2에서 `AgentHarness` 위에 재구현하지 **않음** — `Agent`는 hook-less "lite" path로 유지, `AgentHarness`는 hook-aware path. 두 path가 같은 `agent_loop`을 부르고, 같은 `AgentState`/`AgentContext` data model을 씀.

**Reasoning:** Pi가 `Agent` (legacy `runAgentLoop` wrapper)와 `AgentHarness` (hook + session) 둘 다 보유. Aelix 1.2에서 `Agent`를 `AgentHarness` 위에 얹으면 hook 강제됨 → ADR-0004의 "정책을 끌 수 있어야" 위반. 두 layer 유지가 더 안전. 1.3+에서 `Agent`를 `AgentHarness(extensions=[])` thin wrapper로 단일화는 별도 ADR.

### B9. Phase machine scope for Phase 1.2 → **`idle | turn` only**

**Recommendation:** `AgentHarnessPhase = Literal["idle", "turn"]`. compaction/branch_summary/retry는 Phase 1.3+.

**Reentrancy guard:** `prompt()` 진입 시 `phase != "idle"`이면 `AgentHarnessError("busy", ...)`. Hook handler가 `await harness.prompt()` 콜하면 즉시 busy. 단, `steer()`/`follow_up()`/`abort()`는 phase != idle 일 때만 허용.

**Reasoning:** 가장 작은 안전 집합. Pi가 `retry` phase를 declared but unused 한 채로 둠 — Aelix는 처음부터 안 만듦. Hook handler에서 loop 재진입은 가장 흔한 deadlock 원인이므로 1.2부터 차단.

### B10. Built-in policy + guardrail concrete behavior

**Decision:**

**PolicyExtension** — `tool_call` hook subscriber. 정책 모델:
- `allow_tools: set[str] | None` (None = all allowed)
- `deny_tools: set[str]` 
- 두 set 모두 검사. deny 우선.
- Block 시 `ToolCallResult(block=True, reason=...)`. tool result message는 `ToolResult(content=[TextContent("[blocked] tool '...' is not allowed (policy)")], is_error=True)`로 표시 (`agent/loop.py:333-348` 기존 path 그대로). `ctx.ui.confirm`이 없으니 v1.2는 **silent block** (allowlist-only, no interactive confirm).

**GuardrailExtension** — `tool_call` hook subscriber.
- Hardcoded default rules (Pi의 "rm -rf", ".env", "node_modules/" 모티브):
  - tool `bash`/`shell` 같은 이름이면 `event.args["command"]`에서 다음 패턴 검출: `^\s*rm\s+(-[rRf]+\s+|-r\s+-f\s+)`, `^\s*sudo\s+rm\s+-r`, `:(){:|:&};:`.
  - tool `write`/`edit`이면 `event.args["path"]`가 `.env`, `.env.*`, `.git/`, `node_modules/`, `__pycache__/`로 끝/시작/포함되면 block.
- Override: 생성자 `additional_patterns: list[GuardrailRule]` + `disabled_default_rules: set[str]`.

**Config shape** — 생성자 인자 (env var 아님):
```python
PolicyExtension(allow_tools=None, deny_tools={"bash"})
GuardrailExtension(disabled_default_rules=set(), additional_patterns=[])
```

settings 파일은 Phase 1.3+에서 marketplace settings ADR과 함께 결정.

**Reasoning (ADR ties):**
- ADR-0004 명시: "policy/guardrail은 lifecycle hook을 구독하는 built-in extension". 이게 정확히 그 형태.
- ADR-0007 (in-process): 추가 isolation 없음. block은 단순 return.
- `ctx.ui` 없음 → confirmation prompt 불가. allowlist + hardcoded danger pattern이 최소 안전.

### B11. Extension discovery → **stub for Phase 1.2: explicit factory list + module path list**

**Recommendation Phase 1.2:**
- `load_extensions(paths: list[str | Path | ExtensionFactory])` — path는 Python module path (`"aelix.builtin.policy"`) 또는 `.py` 파일 경로. `ExtensionFactory`는 inline factory callable.
- 자동 discovery 없음. 호출자가 명시.
- `AgentHarnessOptions(extensions=[PolicyExtension(), GuardrailExtension()])` 같은 직접 instance 전달도 지원.

**Phase 1.3+ (future ADR):**
- `~/.aelix/extensions/*.py` directory scan.
- `pyproject.toml`의 `[tool.aelix.extensions]` array.
- `importlib.metadata.entry_points(group="aelix.extensions")`.

**Reasoning:** ADR-0009 (Python-first SDK)는 packaging 형식 결정 안 함. 1.2 deliverable의 "stub is OK if the contract is clear"에 부합. `discoverAndLoadExtensions` Pi 함수 (`/tmp/pi-ext-loader.ts:575`)와 같은 file scan은 절대 경로/symlink 처리 등 곁가지 코드가 ~50 LOC. Phase 1.2의 모듈 LOC 예산을 잡아먹음.

### B12. Tool registration → **`ExtensionAPI.register_tool(AgentTool)` mutates extension state, harness collects at bind**

**Decision:**
- `pi.register_tool(tool: AgentTool)`는 Phase 1.1의 `AgentTool` (이미 정의됨 — `agent/types.py:34`)를 받음. ToolDefinition 재정의 없음.
- 내부적으로 `Extension.tools: dict[str, AgentTool]`에 저장.
- `AgentHarness.__init__(extensions=...)`이 모든 extension의 tools를 모아 `AgentState.tools`에 merge (이름 충돌 시 첫 등록 우선 — Pi `getAllRegisteredTools` 동작 `/tmp/pi-ext-runner.ts:374-384`와 동일).
- `Agent.state.tools`는 application-supplied tools + extension tools 합집합.

**Reasoning:** ADR-0003 (pi parity). Pi의 `ToolDefinition` (`/tmp/pi-ext-types.ts:426`)은 TUI render callback 등을 포함하지만 Aelix 1.2엔 UI가 없으니 그 필드는 불필요. `AgentTool` 그대로 재사용이 Phase 1.1 코드 재사용 + ADR-0002 (small kernel) 만족.

---

## C. Module layout for Phase 1.2

```
src/aelix/
├── agent/
│   ├── __init__.py          # CHANGED: re-export unchanged; harness 모듈은 별도
│   ├── agent.py             # UNCHANGED (Phase 1.1 호환성)
│   ├── loop.py              # UNCHANGED (callback path 유지)
│   ├── types.py             # UNCHANGED
│   └── default_convert.py   # UNCHANGED
├── harness/                 # NEW
│   ├── __init__.py          # public: AgentHarness, AgentHarnessError, HookBus, HookEvent, ...
│   ├── hooks.py             # HookBus + 7 reducers + HookEvent base
│   └── core.py              # AgentHarness class + phase machine
├── extensions/              # NEW
│   ├── __init__.py          # public: ExtensionAPI, ExtensionContext, Extension, load_extensions
│   ├── api.py               # ExtensionAPI + ExtensionContext + Extension dataclass + runtime
│   └── loader.py            # load_extensions(paths) + module/path resolution
└── builtin/                 # NEW
    ├── __init__.py          # public: PolicyExtension, GuardrailExtension
    ├── policy.py            # PolicyExtension setup factory + config
    └── guardrail.py         # GuardrailExtension setup factory + config + default rules
```

### `harness/hooks.py` (~250 LOC)
- **Public**: `HookEvent` (Generic[TResult] base), `HookBus`, `HookHandler` (Protocol), `HookRegistration` (NamedTuple of unsubscribe + source meta), `HookCleanup`, all event dataclasses (`ContextHookEvent`, `BeforeProviderRequestHookEvent`, `BeforeProviderPayloadHookEvent`, `BeforeAgentStartHookEvent`, `ToolCallHookEvent`, `ToolResultHookEvent`, `MessageEndHookEvent`, lifecycle observational events as needed). Result types (`ContextResult`, `ToolCallResult`, `ToolResultPatch`, `BeforeAgentStartResult`, etc.).
- **Internal**: `_reducer_context`, `_reducer_before_provider_request`, `_reducer_before_provider_payload`, `_reducer_before_agent_start`, `_reducer_tool_call`, `_reducer_tool_result`, `_reducer_session_before`, `HOOK_RESULT_TYPES` registry, `_maybe_await` (or import from agent/loop).
- **Pi correspondence**: `/tmp/pi-hooks.md` + `/tmp/pi-harness.ts` `emit*` methods + `/tmp/pi-ext-runner.ts` `emit*` methods.

### `harness/core.py` (~300 LOC)
- **Public**: `AgentHarness`, `AgentHarnessError` (with `code: Literal["busy", "invalid_state", "invalid_argument", "hook", "unknown"]`), `AgentHarnessPhase = Literal["idle", "turn"]`, `AgentHarnessOptions` (extensions, model, tools, system_prompt, get_api_key, steering_mode, follow_up_mode, queue_mode 등).
- **Internal**: `_loop_callbacks_for(harness)` (callback ↔ hook 변환), `_emit_lifecycle(event)`, `_drain_*`.
- **Pi correspondence**: `/tmp/pi-harness.ts` (AgentHarness class) — compaction/branch/session 부분 제외.

### `harness/__init__.py` (~30 LOC)
- Re-export public surface; `__all__` 정의.

### `extensions/api.py` (~250 LOC)
- **Public**: `ExtensionAPI` (Protocol or concrete class), `ExtensionContext` (dataclass + stale assertion), `Extension` (dataclass: `path, handlers, tools, name`), `ExtensionFactory = Callable[[ExtensionAPI], None | Awaitable[None]]`, `ExtensionError` (exception or dataclass).
- **Internal**: `_ExtensionRuntime` (mirrors Pi's `ExtensionRuntime`: throwing stubs → bound actions), `_create_extension_api(extension, runtime)`, `_make_throwing_stub(name)`.
- **Pi correspondence**: `/tmp/pi-ext-types.ts:1084-1311` (ExtensionAPI), `/tmp/pi-ext-types.ts:298-327` (ExtensionContext), `/tmp/pi-ext-loader.ts:149-354` (runtime + API factory).

### `extensions/loader.py` (~150 LOC)
- **Public**: `load_extensions(paths: list[str | Path | ExtensionFactory], cwd: Path | None = None) -> LoadExtensionsResult`, `load_extension_from_factory(factory, name="<inline>")`, `LoadExtensionsResult` (dataclass: extensions, errors, runtime).
- **Internal**: `_import_module_factory(module_path)`, `_resolve_file_factory(file_path)`, `_call_factory(factory, api)`.
- **Pi correspondence**: `/tmp/pi-ext-loader.ts:393-461`.

### `extensions/__init__.py` (~20 LOC)
- Re-export.

### `builtin/policy.py` (~80 LOC)
- **Public**: `PolicyExtension(allow_tools=None, deny_tools=frozenset())` — class with `__call__(self, aelix: ExtensionAPI)` so an instance is itself a factory.
- **Internal**: `_on_tool_call(self, event, ctx)`.

### `builtin/guardrail.py` (~150 LOC)
- **Public**: `GuardrailExtension(disabled_default_rules=frozenset(), additional_patterns=())`, `GuardrailRule` (dataclass: `name, applies_to_tools, predicate, reason`), `DEFAULT_GUARDRAIL_RULES`.
- **Internal**: rule predicates.

### `builtin/__init__.py` (~10 LOC)
- Re-export `PolicyExtension`, `GuardrailExtension`, `GuardrailRule`, `DEFAULT_GUARDRAIL_RULES`.

### Changes to `agent/__init__.py` (~5 LOC additions only)
- No removals. Add nothing — `harness`/`extensions`/`builtin`은 별도 package로 import.

### Changes to `agent/loop.py`
- **None.** Phase 1.1 callback path 유지.

### Changes to `agent/agent.py`
- **None.** Phase 1.1 호환성.

---

## D. Exact Python signatures

```python
# === harness/hooks.py ===

from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Protocol, TypeVar

from aelix.agent.types import AgentContext, AgentMessage
from aelix.ai.messages import (
    AssistantMessage, ImageContent, TextContent, ToolCallContent, ToolResultMessage,
)
from aelix.ai.streaming import Model
from aelix.ai.tools import ToolResult

TResult = TypeVar("TResult")

HookEventName = Literal[
    "context",
    "before_provider_request",
    "before_provider_payload",
    "before_agent_start",
    "tool_call",
    "tool_result",
    "message_end",
    "agent_start",
    "agent_end",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
]


@dataclass(frozen=True)
class HookEvent(Generic[TResult]):
    """Base class for hook events. ``TResult`` is phantom — used only for typing."""
    type: HookEventName


class HookHandler(Protocol[TResult]):
    def __call__(
        self,
        event: HookEvent[TResult],
        ctx: "ExtensionContext",  # from extensions.api
    ) -> TResult | None | Awaitable[TResult | None]: ...


HookCleanup = Callable[[], None | Awaitable[None]]


@dataclass(frozen=True)
class HookRegistration:
    event_type: HookEventName
    unsubscribe: Callable[[], None]
    source: str | None = None  # extension path; None for app-registered


# --- Event payloads ---

@dataclass(frozen=True)
class ContextHookEvent(HookEvent["ContextResult"]):
    messages: list[AgentMessage] = field(default_factory=list)
    type: HookEventName = "context"


@dataclass(frozen=True)
class ContextResult:
    messages: list[AgentMessage] | None = None


@dataclass(frozen=True)
class BeforeProviderRequestHookEvent(HookEvent["BeforeProviderRequestResult"]):
    model: Model
    type: HookEventName = "before_provider_request"
    # NOTE: streamOptions placeholder; Aelix 1.2 has minimal stream options


@dataclass(frozen=True)
class BeforeProviderRequestResult:
    headers: dict[str, str | None] | None = None
    metadata: dict[str, Any | None] | None = None


@dataclass(frozen=True)
class BeforeProviderPayloadHookEvent(HookEvent["BeforeProviderPayloadResult"]):
    model: Model
    payload: Any = None
    type: HookEventName = "before_provider_payload"


@dataclass(frozen=True)
class BeforeProviderPayloadResult:
    payload: Any = None


@dataclass(frozen=True)
class BeforeAgentStartHookEvent(HookEvent["BeforeAgentStartResult"]):
    prompt: str
    system_prompt: str
    images: list[ImageContent] | None = None
    type: HookEventName = "before_agent_start"


@dataclass(frozen=True)
class BeforeAgentStartResult:
    messages: list[AgentMessage] | None = None
    system_prompt: str | None = None


@dataclass(frozen=False)  # NOT frozen — args is intentionally mutable for in-place patching
class ToolCallHookEvent(HookEvent["ToolCallResult"]):
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    assistant_message: AssistantMessage | None = None
    context: AgentContext | None = None
    type: HookEventName = "tool_call"


@dataclass(frozen=True)
class ToolCallResult:
    block: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class ToolResultHookEvent(HookEvent["ToolResultPatch"]):
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    content: list[TextContent | ImageContent]
    details: Any = None
    is_error: bool = False
    type: HookEventName = "tool_result"


@dataclass(frozen=True)
class ToolResultPatch:
    content: list[TextContent | ImageContent] | None = None
    details: Any | None = None
    is_error: bool | None = None
    terminate: bool | None = None


@dataclass(frozen=True)
class MessageEndHookEvent(HookEvent[None]):
    message: AgentMessage
    type: HookEventName = "message_end"


# (lifecycle events: AgentStartHookEvent, AgentEndHookEvent, TurnStartHookEvent,
#  TurnEndHookEvent, MessageStartHookEvent, MessageUpdateHookEvent,
#  ToolExecutionStartHookEvent, ToolExecutionUpdateHookEvent, ToolExecutionEndHookEvent
#  — all `HookEvent[None]`, payload mirrors agent.types AgentEvent variants)


# --- The bus ---

class HookBus:
    def __init__(self, ctx_factory: Callable[[], "ExtensionContext"]) -> None: ...

    def on(
        self,
        event_type: HookEventName,
        handler: HookHandler[Any],
        *,
        source: str | None = None,
        cleanup: HookCleanup | None = None,
    ) -> Callable[[], None]:
        """Register a handler. Returns an unsubscribe callable. Cleanup runs on dispose()."""

    def observe(
        self,
        observer: Callable[[HookEvent[Any], "ExtensionContext"], None | Awaitable[None]],
    ) -> Callable[[], None]:
        """Register a read-only observer that sees every event. Return value ignored."""

    def add_cleanup(self, cleanup: HookCleanup) -> Callable[[], None]: ...

    async def emit(
        self,
        event: HookEvent[TResult],
    ) -> TResult | None:
        """Dispatch event. Returns aggregated reducer result, or None if no handlers/changes."""

    async def dispose(self) -> None:
        """Run all cleanups, clear handlers and observers."""

    def has_handlers(self, event_type: HookEventName) -> bool: ...
```

```python
# === harness/core.py ===

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aelix.agent.types import (
    AgentContext, AgentMessage, AgentState, AgentTool, QueueMode,
)
from aelix.ai.messages import TextContent, UserMessage
from aelix.ai.streaming import Model, StreamFn
from aelix.extensions.api import Extension, ExtensionAPI, ExtensionContext
from aelix.harness.hooks import HookBus


AgentHarnessPhase = Literal["idle", "turn"]


class AgentHarnessError(Exception):
    def __init__(
        self,
        code: Literal["busy", "invalid_state", "invalid_argument", "hook", "unknown"],
        message: str,
        cause: BaseException | None = None,
    ) -> None: ...


@dataclass
class AgentHarnessOptions:
    model: Model
    extensions: list[Extension] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)
    system_prompt: str = ""
    initial_messages: list[AgentMessage] = field(default_factory=list)
    convert_to_llm: Callable[..., Any] | None = None
    get_api_key: Callable[[str], Awaitable[str | None] | str | None] | None = None
    steering_mode: QueueMode = "all"
    follow_up_mode: QueueMode = "all"
    stream_fn: StreamFn | None = None


class AgentHarness:
    def __init__(self, options: AgentHarnessOptions) -> None: ...

    @property
    def phase(self) -> AgentHarnessPhase: ...

    @property
    def hooks(self) -> HookBus: ...

    @property
    def state(self) -> AgentState: ...

    @property
    def is_idle(self) -> bool: ...

    async def prompt(self, text: str) -> list[AgentMessage]: ...
    async def steer(self, text: str) -> None: ...
    async def follow_up(self, text: str) -> None: ...
    async def abort(self) -> None: ...
    async def wait_for_idle(self) -> None: ...

    def subscribe(
        self,
        listener: Callable[[Any], Awaitable[None] | None],
    ) -> Callable[[], None]:
        """Subscribe to every AgentEvent observationally (mirrors Agent.subscribe)."""

    async def dispose(self) -> None:
        """Run all extension cleanups and tear down the hook bus."""
```

```python
# === extensions/api.py ===

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from aelix.agent.types import AgentMessage, AgentTool
from aelix.ai.streaming import Model
from aelix.harness.hooks import HookEventName, HookHandler


class ExtensionContext(Protocol):
    cwd: str
    model: Model | None

    def is_idle(self) -> bool: ...
    def abort(self) -> None: ...
    def get_active_tools(self) -> list[str]: ...
    def get_system_prompt(self) -> str: ...
    # Phase 1.2: NO ui, hasUI, sessionManager, modelRegistry, etc.
    # NO compact(), navigateTree(), reload() (no session machine).


@dataclass
class Extension:
    """Container the loader populates while running an extension factory."""
    name: str
    handlers: dict[HookEventName, list[HookHandler[Any]]] = field(default_factory=dict)
    tools: dict[str, AgentTool] = field(default_factory=dict)
    flags: dict[str, "ExtensionFlag"] = field(default_factory=dict)
    cleanups: list[Callable[[], None | Awaitable[None]]] = field(default_factory=list)


@dataclass
class ExtensionFlag:
    name: str
    type: Literal["bool", "str"]
    default: bool | str | None = None
    description: str | None = None


class ExtensionAPI(Protocol):
    """The handle passed to an extension's ``setup`` function."""

    # --- Subscription ---
    def on(
        self,
        event: HookEventName,
        handler: HookHandler[Any],
        *,
        cleanup: Callable[[], None | Awaitable[None]] | None = None,
    ) -> Callable[[], None]: ...

    # --- Registration ---
    def register_tool(self, tool: AgentTool) -> None: ...
    def register_flag(
        self,
        name: str,
        *,
        type: Literal["bool", "str"],
        default: bool | str | None = None,
        description: str | None = None,
    ) -> None: ...
    def get_flag(self, name: str) -> bool | str | None: ...
    def add_cleanup(self, cleanup: Callable[[], None | Awaitable[None]]) -> Callable[[], None]: ...

    # --- Actions (throwing stubs until AgentHarness binds) ---
    def get_active_tools(self) -> list[str]: ...
    def set_active_tools(self, tool_names: list[str]) -> None: ...
    def get_system_prompt(self) -> str: ...


ExtensionFactory = Callable[[ExtensionAPI], None | Awaitable[None]]
```

```python
# === extensions/loader.py ===

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aelix.extensions.api import Extension, ExtensionFactory


@dataclass
class ExtensionLoadError:
    path: str
    error: str


@dataclass
class LoadExtensionsResult:
    extensions: list[Extension] = field(default_factory=list)
    errors: list[ExtensionLoadError] = field(default_factory=list)


async def load_extensions(
    paths: list[str | Path | ExtensionFactory],
    *,
    cwd: Path | None = None,
) -> LoadExtensionsResult:
    """Load extensions from module paths, file paths, or inline factories.

    Each path/factory becomes an Extension. Module paths use ``importlib.import_module``
    and look for a top-level ``setup`` callable. File paths are loaded via
    ``importlib.util.spec_from_file_location``. Factory callables are invoked directly.

    Failures are collected; one bad extension does not stop the others.
    """


async def load_extension_from_factory(
    factory: ExtensionFactory,
    *,
    name: str = "<inline>",
) -> Extension: ...
```

```python
# === builtin/policy.py ===

from collections.abc import Awaitable
from dataclasses import dataclass, field

from aelix.extensions.api import ExtensionAPI, ExtensionContext
from aelix.harness.hooks import ToolCallHookEvent, ToolCallResult


@dataclass
class PolicyExtension:
    allow_tools: frozenset[str] | None = None  # None = allow all
    deny_tools: frozenset[str] = field(default_factory=frozenset)

    def __call__(self, aelix: ExtensionAPI) -> None:
        """Setup function: registers ``_on_tool_call`` as ``tool_call`` handler."""
```

```python
# === builtin/guardrail.py ===

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aelix.extensions.api import ExtensionAPI, ExtensionContext
from aelix.harness.hooks import ToolCallHookEvent, ToolCallResult


@dataclass(frozen=True)
class GuardrailRule:
    name: str
    applies_to_tools: frozenset[str] | None  # None = any tool
    predicate: Callable[[ToolCallHookEvent], str | None]  # returns reason if blocked else None
    description: str = ""


DEFAULT_GUARDRAIL_RULES: tuple[GuardrailRule, ...]


@dataclass
class GuardrailExtension:
    disabled_default_rules: frozenset[str] = field(default_factory=frozenset)
    additional_patterns: tuple[GuardrailRule, ...] = ()

    def __call__(self, aelix: ExtensionAPI) -> None: ...
```

---

## D.1 Revisions from critic review (binding — overrides D where conflicting)

Critic review at `.omc/specs/sprint-1-phase-1-2-critic-review.md` (REQUEST_MAJOR_REVISIONS verdict)을 받아 다음 결정을 spec에 통합합니다. 이 섹션은 implementation의 정식 contract이며 D와 충돌 시 D.1이 이깁니다.

### D.1.1 All hook events are `frozen=True` (CR-1)
- `HookEvent`, `ContextHookEvent`, `ToolCallHookEvent`, `ToolResultHookEvent`, 그리고 모든 lifecycle event 클래스를 `@dataclass(frozen=True)`로 선언.
- `ToolCallHookEvent.args: dict[str, Any]`는 frozen 인스턴스 안의 mutable dict — Python에서 frozen은 dataclass attribute reassignment만 막고 dict 내부 mutation은 허용. 이는 Pi의 in-place args patch 모델과 호환됨.
- 모든 event class 안에 `__doc__`로 "args (or analogous mutable field) 내용 mutation은 허용; 인스턴스 자체는 frozen" 명시.

### D.1.2 Static narrowing via `@overload` (CR-2, H-7)
- `HookHandler` Protocol을 generic으로 두지 않음. 내부 type은 `Callable[[HookEvent, ExtensionContext], Any | Awaitable[Any]]`.
- `ExtensionAPI.on` 메서드는 **16개 `@overload`** (HookEventName Literal 각각에 대해 handler signature 명시) — Pi의 27개와 동일 패턴.
- `HookBus.on`도 동일 16개 overload.
- Phase 1.2 시작 전 `pyright --strict` spike 1개: `harness/hooks.py` skeleton (1 event + 1 handler + 1 overload)로 narrowing 검증. 실패 시 overload 갯수 확대 또는 design 재고. **이 spike가 W2 진입 gate.**

### D.1.3 `tool_call` reducer — block-only loop effect; reason은 observational (CR-3)
- B6 "tool_call" row 갱신: "Sequential, first `result.block == True` short-circuits. **`block == False`일 때 `reason`은 loop 동작에 영향 없음** (observational only — `BeforeToolCallResult`가 block만 검사하는 `agent/loop.py:333` 호환). Non-block reducer 결과는 hook observer를 통해서만 surface."
- Test `test_reducer_tool_call_non_block_reason_is_observational_only` 추가 (`tests/test_hooks.py`).

### D.1.4 `ExtensionContext` is a concrete class with `__getattribute__` (CR-4)
- D의 `class ExtensionContext(Protocol)` → **`class ExtensionContext` (concrete)**.
- `__init__(self, runtime: _ExtensionRuntime, *, cwd: str, model: Model | None)`.
- `__getattribute__(self, name)`이 internal attribute (`_runtime` 등) 외에는 `runtime.assert_active()` 호출 후 superclass `__getattribute__` 반환.
- `assert_active()`는 stale 시 `ExtensionError("stale", message)` raise.
- 별도 `ExtensionContextProtocol`은 두지 않음 (Phase 1.2). 향후 ADR-0015에서 UI surface 추가 시 재고.

### D.1.5 Args reference contract (CR-5)
- B7에 추가 contract: `AgentHarness._before_tool_call_bridge`가 callback의 `BeforeToolCallContext.args` reference를 그대로 `ToolCallHookEvent.args`에 전달한다. 즉:
  ```python
  hook_event = ToolCallHookEvent(
      tool_call_id=ctx.tool_call.tool_call_id,
      tool_name=ctx.tool_call.tool_name,
      args=ctx.args,  # SAME REFERENCE — defensive copy 금지
      assistant_message=ctx.assistant_message,
      context=ctx.context,
  )
  ```
- 결과: handler가 `event.args["foo"] = 1`로 mutate하면 (1) 다음 handler가 mutated dict를 보고, (2) `tool.execute(args, ...)`가 mutated dict를 사용하고, (3) `after_tool_call` callback의 `AfterToolCallContext.args`가 mutated dict를 봄.
- Tests: `test_tool_call_hook_arg_mutation_visible_to_tool_execute`, `test_tool_call_hook_arg_mutation_visible_to_after_tool_call`.

### D.1.6 `ToolResultPatch = AfterToolCallResult` alias + composition order (H-1)
- `harness/hooks.py`:
  ```python
  from aelix.agent.types import AfterToolCallResult
  ToolResultPatch = AfterToolCallResult  # alias — single source of truth
  ```
- `ToolResultHookEvent` reducer는 `AfterToolCallResult` 인스턴스를 patch chain. Composition: **hook patches가 FIRST 적용, 그 다음 application-supplied `after_tool_call` callback의 override가 적용**. 둘 다 `_apply_after_override`를 거침.
- Test: `test_hook_patch_then_app_callback_composition_order`.

### D.1.7 `LoadExtensionsResult.runtime` + `AgentHarnessOptions.runtime` (H-2, H-3)
- `LoadExtensionsResult(extensions, errors, runtime: _ExtensionRuntime)`.
- `_ExtensionRuntime`은 `extensions/api.py`에 정의된 internal class — Pi의 `ExtensionRuntime`과 동형. Throwing stubs + `bind_core(actions)` 메서드.
- `load_extensions`는 호출당 **하나의 runtime**을 생성하여 모든 extension의 ExtensionAPI에 주입. result에 함께 반환.
- `AgentHarnessOptions(runtime: _ExtensionRuntime | None = None)`. `None`이면 harness가 empty runtime 생성.
- `AgentHarness.__init__`이 `runtime.bind_core(self._actions)`를 호출 — 이후 extension의 action stubs가 실제 메서드로 동작.

### D.1.8 `load_extensions`가 instance factory를 받음 (H-4)
- `load_extensions(paths: list[str | Path | ExtensionFactory])` — `ExtensionFactory = Callable[[ExtensionAPI], None | Awaitable[None]]`. `PolicyExtension()`은 `__call__(self, aelix) -> None`를 가지므로 callable로 통과.
- B11의 "직접 instance 전달도 지원" 문구 제거. 모든 extension은 `load_extensions` 경유.
- 권장 assembly 패턴:
  ```python
  result = await load_extensions([PolicyExtension(), GuardrailExtension(), "aelix.examples.echo"])
  harness = AgentHarness(AgentHarnessOptions(
      model=..., extensions=result.extensions, runtime=result.runtime
  ))
  ```

### D.1.9 Circular import 방지 (H-5)
- `harness/hooks.py`는 `extensions/api.py`로부터 `ExtensionContext`를 `TYPE_CHECKING` guard 안에서만 import. 함수 signature에서 `"ExtensionContext"` forward-ref string으로 사용.
- 마찬가지로 `extensions/api.py`도 hook 관련 타입을 `TYPE_CHECKING` 안에서 import.
- Runtime 의존성은 `extensions/api.py` → `harness/hooks.py` 방향 일방으로 정리 (hooks는 ExtensionContext를 type level에서만 봄, runtime instance는 caller가 주입).

### D.1.10 `steer()` / `follow_up()` during idle — Pi parity (H-6)
- idle 상태에서도 `steer()`/`follow_up()` 호출 시 큐에 enqueue. 다음 `prompt()`가 drain.
- Test 이름 변경: `test_steer_when_idle_enqueues_for_next_prompt`.

### D.1.11 Phase 1.1 regression tests (H-8)
- `tests/test_agent_regression.py` 신규:
  - `test_agent_signature_unchanged` — `inspect.signature(Agent.__init__)` snapshot.
  - `test_agent_has_no_hooks_attribute` — `hasattr(Agent(), "hooks") is False` (harness import 후에도).
  - `test_existing_agent_loop_callbacks_still_work` — Phase 1.1 callback path 호출 → 같은 결과.

### D.1.12 End-to-end + concurrency tests (H-9, H-10)
- `tests/test_loop_with_hooks.py`에 추가:
  - `test_full_chain_policy_blocks_real_tool_call`
  - `test_full_chain_guardrail_blocks_rm_rf_in_real_loop`
  - `test_full_chain_two_extensions_one_patches_args_other_blocks`
- `tests/test_hooks.py`에 추가:
  - `test_handler_spawning_task_does_not_corrupt_handler_ordering`
  - `test_slow_handler_followed_by_fast_handler_completes_in_registration_order`

### D.1.13 Scope cuts and clarifications
- **M-1**: `BeforeProviderRequestHookEvent`, `BeforeProviderPayloadHookEvent`는 Phase 1.2에서 **제외**. `stream_simple`이 stub이라 reducer 호출처 없음. Phase 2 provider 작업과 함께 추가.
- **M-2**: `MessageEndHookEvent.__doc__`에 "observational only, reducer는 ADR-0013에서 결정".
- **M-3**: `HookBus.emit`이 `not self.has_handlers(event.type) and not self._observers`면 즉시 `None` 반환 (zero-handler fast path).
- **M-4**: `AgentHarness.dispose()`가 `phase != "idle"`이면 `abort()` 먼저 + `wait_for_idle()` 대기 + cleanup LIFO.
- **M-5**: `tests/test_hooks.py`에 `test_hook_event_name_literal_matches_hook_result_types_keys` 추가.
- **M-7**: Guardrail regex 강화 — `rm -fr`, `/bin/rm`, `eval "rm -rf /"` 케이스 테스트.
- **M-8**: Implementation order: builtin policy/guardrail을 W2 안 harness보다 먼저 또는 동시. harness test는 inline factory stub 사용 가능.
- **M-9**: Tool name collision — **application-supplied tools (AgentHarnessOptions.tools) wins over extension tools**. Test `test_application_supplied_tool_overrides_extension_tool_with_same_name`.

### D.1.14 Pre-implementation gate — PASSED 2026-05-17
W2 시작 전 narrowing spike (`scripts/pyright_spike.py`):
1. `# pyright: strict` file header, 2 event + 2 result + 2 overload, 2개 handler.
2. Inverse cases:
   - `api.on("badevent", handler)` → Literal['tool_call'] 위반 catch ✓
   - `api.on("tool_call", lambda x: x)` → arity 1 vs 2 mismatch catch ✓
   - `api.on("tool_call", wrong_handler)` → parameter contravariance + return type mismatch catch ✓
3. 결과: 8 errors emitted. Narrowing 완전 작동. `@overload + Literal` 전략 채택 확정.
4. spike 파일은 `scripts/pyright_spike.py`에 보존 — Phase 1.2 implementation 후 narrowing regression 확인용.

---

## E. Test plan

### `tests/test_hooks.py` (HookBus + each reducer)
- `test_emit_no_handlers_returns_none`
- `test_emit_unknown_event_raises_keyerror`
- `test_on_returns_unsubscribe_callable`
- `test_observe_sees_all_events_return_ignored`
- `test_observe_is_independent_from_on`
- `test_reducer_context_sequential_transform` — H1 patches msgs[0], H2 sees patched, final reducer return = last messages.
- `test_reducer_context_no_changes_returns_none`
- `test_reducer_before_provider_request_header_patch_merges` — explicit None deletes header key.
- `test_reducer_before_provider_request_explicit_undefined_headers_clears`
- `test_reducer_before_provider_payload_replace_chains`
- `test_reducer_before_agent_start_collects_messages_chains_prompt`
- `test_reducer_tool_call_block_short_circuits` — H1 returns None, H2 returns block=True, H3 never invoked.
- `test_reducer_tool_call_no_block_returns_last_truthy`
- `test_reducer_tool_call_args_mutation_visible_to_later_handler` — H1 mutates `event.args["foo"]=1`, H2 sees `1`.
- `test_reducer_tool_call_no_revalidation_after_mutation`
- `test_reducer_tool_result_patch_accumulation` — H1 sets content, H2 sets is_error; final has both.
- `test_reducer_tool_result_unset_fields_preserved`
- `test_reducer_session_before_cancel_short_circuits`
- `test_reducer_session_before_last_truthy_wins_no_cancel`
- `test_message_end_observational` — return ignored in v1.2.
- `test_handler_raises_propagates_as_harness_error` — Pi wraps in `AgentHarnessError("hook", ...)`.
- `test_sync_handler_and_async_handler_both_work`
- `test_cleanup_runs_on_dispose_in_lifo_order`
- `test_unsubscribe_during_emit_safe` — handler unsubscribes itself; snapshot iteration prevents skip.

### `tests/test_agent_harness.py`
- `test_idle_initial_phase`
- `test_prompt_transitions_idle_to_turn_to_idle`
- `test_prompt_when_busy_raises_busy_error`
- `test_steer_when_idle_raises_invalid_state`
- `test_steer_during_turn_queues_message`
- `test_abort_during_turn_clears_queues`
- `test_dispose_runs_all_extension_cleanups`
- `test_hook_handler_reentry_busy_raises` — handler calls `await harness.prompt()` → busy error.
- `test_subscribe_receives_lifecycle_events_in_order`
- `test_handler_ordering_within_event_matches_registration_order` — H1 registered before H2 fires first.
- `test_application_supplied_tools_merge_with_extension_tools`
- `test_name_collision_first_extension_tool_wins`

### `tests/test_extension_api.py`
- `test_on_records_handler_in_extension_state`
- `test_register_tool_records_tool`
- `test_register_flag_stores_default`
- `test_get_flag_returns_default_before_cli_override`
- `test_action_stubs_raise_before_bind` — `pi.get_active_tools()` 호출 시 NotInitialized 에러.
- `test_action_stubs_succeed_after_harness_bind`
- `test_stale_extension_api_raises_after_dispose` — harness.dispose() 후 ext API 호출이 stale error.

### `tests/test_extension_loader.py`
- `test_load_inline_factory_returns_extension`
- `test_load_module_path_finds_setup` — `aelix.builtin.policy` import → setup OK.
- `test_load_file_path_executes_setup`
- `test_async_setup_supported`
- `test_factory_raising_collected_as_error_not_thrown`
- `test_factory_missing_setup_reported_as_error`
- `test_load_multiple_extensions_preserves_order`
- `test_load_continues_on_per_extension_error`

### `tests/test_builtin_policy.py`
- `test_allow_all_when_allow_tools_none`
- `test_deny_list_blocks_named_tool`
- `test_allow_list_blocks_unlisted_tool`
- `test_deny_overrides_allow`
- `test_block_returns_tool_call_result_block_true`
- `test_block_reason_propagated_to_tool_result_message`
- `test_integration_with_harness_blocks_actual_tool_execution`

### `tests/test_builtin_guardrail.py`
- `test_default_blocks_rm_rf_in_bash_command`
- `test_default_blocks_sudo_rm_r`
- `test_default_blocks_fork_bomb`
- `test_default_blocks_write_to_dotenv`
- `test_default_blocks_write_to_node_modules`
- `test_default_blocks_write_to_git_dir`
- `test_disabled_default_rule_no_longer_blocks`
- `test_additional_pattern_blocks`
- `test_rule_predicate_returning_none_does_not_block`
- `test_rule_only_applies_to_listed_tools`
- `test_integration_with_harness_blocks_actual_bash_call`

### `tests/test_loop_with_hooks.py`
- `test_tool_call_hook_blocks_echo` — extension blocks "echo" tool; assistant sees tool_result is_error=True with reason text.
- `test_tool_result_hook_rewrites_output` — extension patches `content` to `[TextContent("[redacted]")]`.
- `test_tool_result_hook_chains_patches` — two extensions, both contribute.
- `test_before_agent_start_injects_system_message` — extension returns BeforeAgentStartResult; first turn sees injected message.
- `test_context_hook_filters_messages` — extension drops messages older than N turns.
- `test_phase_1_1_callbacks_still_work_without_harness` — direct `agent_loop` use with `before_tool_call` callback still passes.

### Edge cases (distributed across files above)
- Handler raises sync — wrapped, surfaced through `HookBus.emit` → `AgentHarnessError("hook")`.
- Handler raises async — same.
- Empty handler list for known event — returns None.
- Many handlers (50+) — ordering preserved.
- Reducer with zero registrations — short-circuits, no extension ctx created.
- Stale ctx after dispose — every ExtensionAPI/ExtensionContext method raises with the same message.
- Async vs sync handler mixed in one event — order preserved, each awaited correctly.

---

## F. ADR follow-ups

| Proposed ADR | One-line decision sketch |
|---|---|
| ADR-0011 Hook event catalogue v1 | Phase 1.2가 ship하는 16개 event type을 정식 등록; custom event registration은 추후 결정. |
| ADR-0012 Extension discovery model | `~/.aelix/extensions/*.py`, `pyproject.toml [tool.aelix.extensions]`, `importlib.metadata` entry_points 중 우선순위. Phase 1.3+에서 결정. |
| ADR-0013 message_end reducer policy | message_end가 observational vs replacement (role-preserving) reducer인지. Pi는 후자, Aelix 1.2는 전자로 시작. |
| ADR-0014 Hook error policy | handler 예외 → `errorMode: "continue" \| "throw"` 선택지. Pi는 `"continue"` default; Aelix 1.2는 `"throw"`로 시작 후 1.3에서 결정. |
| ADR-0015 ExtensionContext UI surface | UI primitives (confirm/select/notify)가 ExtensionContext에 들어가는지, 별도 UIAdapter ADR로 분리할지. |
| ADR-0016 Phase machine expansion | `compaction`/`branch_summary`/`retry` 도입 시점과 reentrancy 모델. |

---

## G. Risks and open questions

| # | Risk / open question | Confidence | Recommended escalation |
|---|---|---|---|
| R1 | **Async listener ordering vs concurrency.** Pi는 strict sequential await. Aelix도 동일. 그러나 Python `asyncio`에서 handler가 `asyncio.create_task`로 fire-and-forget할 경우 ordering 보장 깨짐. | Medium | Spec에 "handlers must not spawn background tasks that mutate harness state". Test로 시퀀스 검증. |
| R2 | **Handler error propagation default.** Pi는 `"continue"` (extension 오류 무시), Aelix 1.2는 `"throw"`로 선택 (B 결정 없음). 정해야 함. | Low | **권고**: 1.2는 `"throw"`로 시작 (디버깅 용이). 1.3에서 `errorMode` config option 추가 (ADR-0014). |
| R3 | **Stale ctx detection in Python.** Pi는 `Object.defineProperties` lazy getter로 구현. Python에서 동일을 하려면 `__getattribute__` overload 또는 dataclass-with-property. `Protocol` 기반 `ExtensionContext`는 attribute 접근 시 stale 검사가 어려움. | High | `ExtensionContext`를 Protocol이 아닌 concrete class로 두고 `__getattribute__`에서 `self._runtime.assert_active()`. Phase 1.2 implementer가 결정. |
| R4 | **`ToolCallHookEvent.args` mutability.** dataclass `frozen=False`로 두지만 다른 dataclass는 frozen=True인 비일관성. | Medium | 명시적 doc + 별도 `MutableHookEvent` mixin 고려. 1.2에선 docstring으로 충분. |
| R5 | **Phase machine entry from hook handler.** `harness.steer()`은 handler에서 안전하지만 `harness.prompt()`은 reentrancy 위반. handler 안에서 `steer()`만 허용. | Low | `prompt()` 진입에 `phase != "idle"`이면 busy raise; `steer()`은 phase != idle일 때만 허용. 명시적 처리. |
| R6 | **`abort()`이 hook emit 도중 발생.** Pi는 `AbortController.signal`을 handler 3번째 인자로 전달. Phase 1.2는 signal 인자 미지원 — `ExtensionContext`에 `signal` 속성만. handler가 signal 검사 안 하면 abort 늦어짐. | Medium | Phase 1.2 doc: "handler should check `ctx.signal.cancelled` between IO operations". Strict 강제는 1.3 ADR-0014와 함께. |
| R7 | **Extension cleanup ordering.** Pi는 Set 순회 (insertion order). Aelix는 LIFO 권고 (resource ownership pattern). | Low | LIFO 채택, 테스트로 검증. |
| R8 | **Tool name collision policy.** Pi는 first-wins (`/tmp/pi-ext-runner.ts:374-384`). 그러나 user-supplied tools가 built-in extension tools와 충돌 시 어느 것 우선? | Medium | **권고**: application-supplied tools (AgentHarnessOptions.tools)가 extension tools를 override. Test에 명시. |
| R9 | **Phantom result type 정확도.** B1의 Generic[TResult] 방식은 mypy/pyright에서 완벽히 좁혀지지 않을 수 있음 (variance, Protocol과 결합 시). | Medium | `pyright --strict`로 sample handler 시그니처가 좁혀지는지 spec 작성 후 즉시 검증. 안 되면 `@overload` heavy version으로 폴백. |
| R10 | **Phase 1.1 `Agent` 클래스 future deprecation 모호함.** B8은 `Agent`/`AgentHarness` 공존을 결정했지만 장기 방향은 모호. | Low | "Phase 1.2 ships both; convergence decision deferred to 1.3 ADR". 결정 안 함. |
| R11 | **`load_extensions` async vs sync.** Pi는 async (jiti import). Python은 `importlib.import_module`이 sync. 그러나 `setup()`이 async일 수 있으니 loader 자체는 async로 유지. | Low | 결정대로. Async loader. |
| R12 | **Pre-bind action stubs in Python.** Pi는 throwing stub. Python에서 동일하게 가능하나 `Protocol` 타입과 충돌 시 type checker가 잡아낼 수 있음. | Low | concrete class로 stub 구현; Protocol은 type-only. |

---

## H. Implementation order (for executor)

권장 순서 — 각 단계 끝에 테스트 green:

1. `harness/hooks.py` (HookBus + event classes + reducers) + `tests/test_hooks.py`.
2. `extensions/api.py` (Extension, ExtensionAPI, ExtensionContext, throwing-stub runtime) + `tests/test_extension_api.py`.
3. `extensions/loader.py` (`load_extensions` + inline factory) + `tests/test_extension_loader.py`.
4. `harness/core.py` (AgentHarness — callback-to-hook conversion, phase machine, subscribe) + `tests/test_agent_harness.py`.
5. `builtin/policy.py` + `builtin/guardrail.py` + their tests.
6. `tests/test_loop_with_hooks.py` (end-to-end integration).
7. `agent/__init__.py` / `harness/__init__.py` / `extensions/__init__.py` / `builtin/__init__.py` re-exports.
8. Smoke run: `uv run python -m aelix examples/echo` 그대로 동작 + 새 `examples/harness_echo.py` 추가하여 PolicyExtension/GuardrailExtension 동작 검증 (별도 PR도 가능).

Estimated total LOC for production code: ~1100 (hooks 250, core 300, api 250, loader 150, policy 80, guardrail 150). Tests: ~800 LOC.
