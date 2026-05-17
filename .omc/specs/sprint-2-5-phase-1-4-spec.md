# Sprint 2.5 · Phase 1.4 — Pi-Parity Hygiene Spec

> Author: Architect (READ-ONLY, Opus)
> Date: 2026-05-17
> Status: **Binding** (W2 implements verbatim)
> Top-level principle: **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**
> Pi reference pin (this spec): **`badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`** (`main`, 2026-05-17). All Pi line numbers below are anchored to this SHA — see ADR-0034.

---

## 0. Why This Sprint Exists

Phase 1.3 (Sprint 2) shipped the monorepo split, F-9 (non-destructive `set_active_tools`), and F-10 (per-turn `_TurnState`). That leaves three Sprint 1 re-eval items strictly in the "shell + ADR" category — small enough to bundle into a single hygiene sprint before Phase 2.1 starts touching live behavior:

1. **`stream_simple` dispatch shell** — currently a `NotImplementedError` stub (`packages/aelix-ai/src/aelix_ai/streaming.py:110-128`). Pi's `streamSimple` is a thin dispatcher over an API registry (Pi `stream.ts:45-50`). We can ship the shell + registry now and defer real adapters to Phase 4 without changing the public surface.
2. **F-6 placeholder fields on `AgentHarnessOptions`** — Aelix's options dataclass (`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:109-132`) is missing 7 fields that exist in Pi (`packages/agent/src/harness/types.ts:558-583`). Adding them as `None`-default placeholders today makes Phase 2.1 a pure behavior wire-up rather than an API break.
3. **F-7 ADR for the loop-event vs harness-event distinction** — code already separates `AgentEvent` (10) from `HookEvent` (16), but ADR-0017 conflates them. We need an explicit ADR cementing the two-tier model before Phase 2.1 grows the harness side to ~28 events (matching Pi's `AgentHarnessEvent` union at `packages/agent/src/harness/types.ts:467-469`).

Phase 1.4 = "ship 3 shells + 5 ADRs, no behavior changes outside `stream_simple` raising a typed error". 152 existing tests remain green; +5-8 new tests cover the shell.

This is the **same pattern** ADR-0025 established for F-10: minimal shell + owning-ADR cross-reference + Phase 2.x land for the body.

---

## Section A — `stream_simple` Dispatch Shell

### A.1 Pi reference

`packages/ai/src/stream.ts:45-50`:

```typescript
export function streamSimple<TApi extends Api>(
    model: Model<TApi>,
    context: Context,
    options?: SimpleStreamOptions,
): AssistantMessageEventStream {
    const provider = resolveApiProvider(model.api);
    return provider.streamSimple(model, context, options);
}
```

`packages/ai/src/stream.ts:18-22`:

```typescript
function resolveApiProvider(api: Api) {
    const provider = getApiProvider(api);
    if (!provider) {
        throw new Error(`No API provider registered for api: ${api}`);
    }
    return provider;
}
```

`packages/ai/src/api-registry.ts` (cited via SHA `734e08e…`):
- `registerApiProvider(provider, sourceId?)` — lines 48-57
- `getApiProvider(api)` — lines 59-61
- `getApiProviders()` — lines 63-65
- `unregisterApiProviders(sourceId)` — lines 67-72
- `clearApiProviders()` — lines 74-76

### A.2 Phase 1.4 deliverables

A new module `packages/aelix-ai/src/aelix_ai/api_registry.py` plus an updated `packages/aelix-ai/src/aelix_ai/streaming.py`.

#### A.2.1 Error type (new)

```python
class StreamSimpleError(Exception):
    """Raised by ``stream_simple`` when no provider matches ``model.api``.

    Codes (Phase 1.4):
        - ``"no_provider_registered"``: ``model.api`` has no registered provider.

    Phase 4 adapters (anthropic/openai/openrouter) will populate the registry
    via ``register_provider``; until then this error is the documented UX.
    """

    def __init__(self, code: Literal["no_provider_registered"], message: str) -> None:
        super().__init__(message)
        self.code = code
```

Rationale: a typed exception (not `RuntimeError`/`NotImplementedError`) matches Pi's "named, fail-fast, before any streaming begins" pattern and lets test code assert `pytest.raises(StreamSimpleError)` rather than string-matching.

#### A.2.2 Registry API (new, in `api_registry.py`)

```python
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_ai.streaming import StreamFn

_PROVIDERS: dict[str, "StreamFn"] = {}

def register_provider(api: str, fn: "StreamFn") -> None:
    """Register a provider implementation for ``model.api == api``.

    Subsequent registrations for the same ``api`` overwrite the previous one
    (matches Pi: ``registerApiProvider`` replaces by api key).
    """
    _PROVIDERS[api] = fn

def unregister_provider(api: str) -> None:
    """Remove the provider for ``api``. No-op if absent.

    Pi equivalent: ``unregisterApiProviders(sourceId)`` (Pi keys by sourceId;
    Aelix keys by api for the Phase 1.4 shell — sourceId arrives in Phase 4
    when multiple adapters per api become real).
    """
    _PROVIDERS.pop(api, None)

def get_registered_providers() -> dict[str, "StreamFn"]:
    """Return a shallow copy of the registry. Read-only; mutating the result
    does not affect the registry."""
    return dict(_PROVIDERS)

def _resolve_provider(api: str) -> "StreamFn":
    fn = _PROVIDERS.get(api)
    if fn is None:
        from aelix_ai.streaming import StreamSimpleError
        raise StreamSimpleError(
            "no_provider_registered",
            (
                f"No provider registered for api={api!r}. "
                "Phase 4 will land Anthropic/OpenAI/OpenRouter adapters; "
                "until then, pass a mock stream_fn explicitly to the agent loop."
            ),
        )
    return fn

def _clear_providers_for_tests() -> None:
    """Test-only hook. Not part of the public API."""
    _PROVIDERS.clear()
```

#### A.2.3 `stream_simple` final signature (updated)

```python
async def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions,
) -> AsyncIterator[AssistantMessageEvent]:
    """Dispatch shell — Pi parity (``stream.ts:45-50``).

    Resolves ``model.api`` against the provider registry and delegates. Real
    adapter bodies (Anthropic, OpenAI, OpenRouter) arrive in Phase 4 under
    ``aelix_ai.providers``; for tests and demos, pass an explicit
    ``stream_fn`` into the agent loop instead.

    Raises:
        StreamSimpleError("no_provider_registered"): if ``model.api`` has no
            registered provider.
    """
    from aelix_ai.api_registry import _resolve_provider

    fn = _resolve_provider(model.api)
    async for event in fn(model, context, options):
        yield event
```

Signature stays `(model, context, options) -> AsyncIterator[AssistantMessageEvent]` — **no caller-visible change** for the existing `stream_fn` injection path. Callers that previously hit `NotImplementedError` now hit `StreamSimpleError("no_provider_registered", ...)`.

#### A.2.4 Umbrella exposure

Add to `packages/aelix-ai/src/aelix_ai/__init__.py` `__all__`:
- `StreamSimpleError`
- `register_provider`, `unregister_provider`, `get_registered_providers`

Add to `src/aelix/__init__.py`:
- `StreamSimpleError`
- `register_provider`, `unregister_provider`, `get_registered_providers`

Rationale for umbrella exposure: third-party adapter packages (e.g. a future `aelix-anthropic`) must be able to call `from aelix import register_provider` without reaching into `aelix_ai.api_registry`. This matches Pi's `index.ts` re-exporting `./providers/register-builtins.js` (Pi `packages/ai/src/index.ts:28`).

### A.3 Test plan (5 tests, all in `tests/test_stream_simple_shell.py`)

| # | Name | Asserts |
|---|------|---------|
| 1 | `test_stream_simple_no_provider_raises` | empty registry → `pytest.raises(StreamSimpleError) as ei; assert ei.value.code == "no_provider_registered"` |
| 2 | `test_stream_simple_routes_to_registered_provider` | register fake → `async for ev in stream_simple(model_with_api_x, ctx, opts): collected.append(ev)` matches what fake yielded |
| 3 | `test_unregister_provider_removes_it` | register then unregister → calling `stream_simple` raises `StreamSimpleError` |
| 4 | `test_get_registered_providers_returns_copy` | mutate returned dict; `get_registered_providers()` again does not reflect mutation |
| 5 | `test_register_overwrites_same_api` | register two fns for `"x"`; second wins; first not called |

All 5 tests use `_clear_providers_for_tests()` in `setup()` / `pytest` fixture to guarantee registry isolation. Run order independence is required (no test depends on another's registration state).

### A.4 Migration note

Phase 4 lands `packages/aelix-ai/src/aelix_ai/providers/` with `anthropic.py`, `openai.py`, `openrouter.py`. Each module exposes a `register()` function that calls `register_provider(api, _impl_stream_fn)`. A `providers/__init__.py` `register_all()` will mirror Pi's `register-builtins.ts` and be invoked from the CLI entry point — never from library code (so third-party adapters can choose what to register).

### A.5 Phase boundary

Phase 1.4 ships: shell + registry + typed error + 5 tests.
Phase 4 ships: 3 provider adapter modules + OAuth + `.env` integration + `register_all()`.

Nothing in between Phase 1.4 and Phase 4 should touch this module. If you find yourself adding a non-trivial codepath here in Phase 2.x or 3.x, file an ADR first.

---

## Section B — F-6 `AgentHarnessOptions` Placeholder Fields

### B.1 Pi reference

`packages/agent/src/harness/types.ts:558-583` (SHA `734e08e…`):

```typescript
export interface AgentHarnessOptions<
    TSkill extends Skill = Skill,
    TPromptTemplate extends PromptTemplate = PromptTemplate,
    TTool extends AgentTool = AgentTool,
> {
    env: ExecutionEnv;                                          // line 562
    session: Session;                                           // line 563
    tools?: TTool[];                                            // line 564
    resources?: AgentHarnessResources<TSkill, TPromptTemplate>; // line 565
    systemPrompt?: string | ((context: {...}) => string | Promise<string>);  // line 566-?
    getApiKeyAndHeaders?: (model: Model<any>) => Promise<...>;  // line ~571
    streamOptions?: AgentHarnessStreamOptions;                  // line ~574
    model: Model<any>;                                          // line ~575
    thinkingLevel?: ThinkingLevel;                              // line ~576
    activeToolNames?: string[];                                 // line ~577
    steeringMode?: QueueMode;                                   // line ~578
    followUpMode?: QueueMode;                                   // line ~579
}
```

### B.2 Aelix today

`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:109-132`:

```python
@dataclass
class AgentHarnessOptions:
    model: Model = field(default_factory=Model)
    extensions: list[Extension] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)
    system_prompt: str = ""
    initial_messages: list[AgentMessage] = field(default_factory=list)
    convert_to_llm: ConvertToLlmFn | None = None
    transform_context: ...
    get_api_key: Callable[[str], Awaitable[str | None] | str | None] | None = None
    steering_mode: QueueMode = "one-at-a-time"
    follow_up_mode: QueueMode = "one-at-a-time"
    stream_fn: StreamFn | None = None
    after_tool_call: Callable[..., Any] | None = None
    runtime: _ExtensionRuntime | None = None
    cwd: str = "."
```

Aelix has 14 fields. Pi has 12 declared fields (plus inherited from base via the type system). The gap is not "count parity" — it's "every Pi field with a meaningful behavior must have a name slot in Aelix today so Phase 2.1 can wire behavior without breaking constructors written in Phase 1.x".

### B.3 The 7 placeholder fields (Phase 1.4)

Append to `AgentHarnessOptions` **after** `cwd: str = "."` so existing kwargs ordering is preserved:

```python
    # === Phase 2.1 placeholders (F-6, ADR-NNNN) — no behavior in Phase 1.4 ===
    # Each field exists so Phase 2.1 can wire behavior without breaking
    # constructors written against the Phase 1.4 signature. Pi line citations
    # are relative to SHA 734e08edf82ff315bc3d96472a6ebfa69a1d8016.

    # Phase 2.2 placeholder (ADR-0022). Pi: session.ts Session interface,
    # `AgentHarnessOptions.session` at types.ts:563.
    session: Any | None = None

    # Phase 2.1 placeholder (ADR-0017). Pi: `env: ExecutionEnv`
    # at types.ts:562. ExecutionEnv carries cwd + environment vars + abort
    # controller; Phase 2.1 may merge or supersede the bare ``cwd`` field.
    env: dict[str, str] | None = None

    # Phase 2.1 placeholder (ADR-0017). Pi: `resources?:
    # AgentHarnessResources<TSkill, TPromptTemplate>` at types.ts:565.
    # Aelix uses ``list[Any]`` until the resource model is ported.
    resources: list[Any] | None = None

    # Phase 2.1 placeholder (ADR-0017). Pi: `thinkingLevel?: ThinkingLevel`
    # at types.ts:~576. Literal["off"|"low"|"medium"|"high"] in Phase 2.1.
    thinking_level: str | None = None

    # Phase 2.1 placeholder. Mirrors AgentState.active_tool_names so callers
    # can preset the active subset at harness construction. Pi:
    # `activeToolNames?: string[]` at types.ts:~577. AgentState already has
    # this field (types.py:76) per F-9.
    active_tool_names: list[str] | None = None

    # Phase 2.1 placeholder (ADR-0017). Supersedes ``get_api_key`` once
    # Phase 2.1 lands. Pi: `getApiKeyAndHeaders?: (model: Model<any>)
    # => Promise<{apiKey: string, headers?: ...}>` at types.ts:~571.
    # Returns ``(api_key, headers)`` tuple in the Aelix port.
    get_api_key_and_headers: Callable[..., Any] | None = None

    # Phase 2.1 placeholder (ADR-0017, ADR-0037). Pi: `streamOptions?:
    # AgentHarnessStreamOptions` at types.ts:~574. Used by
    # `before_provider_request` to mutate the outgoing stream call.
    stream_options: dict[str, Any] | None = None
```

### B.4 Binding rules for Phase 1.4

1. **No behavior** — `AgentHarness.__init__` MUST NOT read any of the 7 new fields. They sit on `_options` and are accessible via `self._options.<name>`, but no codepath reads them.
2. **No tests of behavior** — only constructor smoke tests (B.5).
3. **No removal of existing fields** — `get_api_key` stays alongside `get_api_key_and_headers`. Phase 2.1 ADR-0017 will deprecate `get_api_key` with a `DeprecationWarning`.
4. **Type imports** — `from typing import Any` and `from collections.abc import Callable` already exist; no new imports required.
5. **`thinking_level: str | None`** — keep as `str | None` not `Literal[...] | None` until ADR-0017 finalizes the enum. Pyright will not narrow today; that's fine for a placeholder.

### B.5 Test plan (3 tests, in `tests/test_harness_options_placeholders.py`)

| # | Name | Asserts |
|---|------|---------|
| 1 | `test_harness_options_default_placeholders_none` | construct `AgentHarnessOptions(model=Model())`; assert all 7 new fields default to `None` |
| 2 | `test_harness_options_accepts_each_placeholder` | construct with each field set explicitly (e.g. `session=object()`, `env={"X": "1"}`, `thinking_level="medium"`, etc.); no `TypeError` |
| 3 | `test_harness_does_not_read_placeholders_in_phase_1_4` | construct harness with all 7 placeholders set; run a one-turn prompt with a mock `stream_fn`; assert behavior is identical to a harness constructed without them (smoke test that they're inert) |

### B.6 Imports updated by W2

- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` — add fields only.
- `tests/test_harness_options_placeholders.py` — new file.
- No umbrella re-export changes (these fields are reached via `AgentHarnessOptions`, already exposed).

---

## Section C — F-8 `ExtensionContext.model` Python Gap

### C.1 Background

Pi declares `model: Model<TApi> | undefined` where `TApi extends Api`. Python has no equivalent of TypeScript's `<TApi extends Api>` constraint without `Generic[ApiT]` machinery, and `Model` in Aelix (`packages/aelix-ai/src/aelix_ai/streaming.py:25-38`) is intentionally non-generic because the provider distinguisher is the runtime string `model.api: str`, not a static type parameter.

### C.2 Decision: docstring (not ADR)

**Pick: in-code docstring on the field; cross-reference from `streaming.Model` class docstring.**

Justification:
- An ADR would imply this is a deliberate divergence that affects design weight. It isn't — it's a **language gap**, not a design choice. ADRs for language gaps create noise in the index.
- A field-level docstring is reachable by anyone touching `ExtensionContext.model`; an ADR is not.
- Phase 2.1 may revisit if we adopt a `Model[ApiT]` generic typed via PEP 695 `class Model[ApiT: str]:`. Until then, the docstring is the right artifact.

### C.3 Text to add

Add to `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` on the `ExtensionContext.model` field:

```python
model: Model | None = None
"""Current model bound to this harness, or ``None`` if cleared.

Pi parity note (F-8): Pi declares this field as ``Model<TApi> | undefined``
with a generic API parameter (Pi ``ExtensionContext.model``). Aelix erases
the generic and uses ``Model | None`` because the API distinguisher in
Aelix is the runtime string ``model.api`` (see ``aelix_ai.streaming.Model``),
not a static type parameter. Callers that need narrowing should
``match model.api:`` rather than rely on a static API type. Phase 2.x may
revisit with PEP 695 generics; until then this is a documented gap, not
a divergence.
"""
```

Add a cross-reference at the top of `Model` class docstring (`streaming.py:26-30`):

```python
@dataclass(frozen=True)
class Model:
    """Pi-style provider-agnostic model description.

    Note: ``api`` is a runtime string, not a static generic parameter (see
    F-8 in ``ExtensionContext.model`` docstring). Use ``match model.api:`` for
    narrowing.
    """
```

### C.4 No new tests

This is documentation only. Pyright spike count remains 8 (per Section E acceptance).

---

## Section D — Five New ADRs (Drafts)

All five ADRs live in `docs/decisions/` with their numbered filenames per the existing convention. Each begins with `Status:` and ends with a `Consequences` section.

### D.1 ADR-0034 — Pi Reference Version Pin

**File:** `docs/decisions/0034-pi-reference-version-pin.md`
**Status:** `Accepted (Sprint 2.5 shipped)`

#### Context
ADR-0003 names pi agent as primary reference but doesn't pin a version. As Pi evolves on `main`, Aelix line citations drift and parity-audit reproducibility breaks.

#### Decision
Pin Pi to a specific commit SHA per sprint. **Current pin: `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`** (`main` HEAD as of 2026-05-17, commit message "chore: approve contributor mattiacerutti").

Update policy:
1. Each new sprint that imports new Pi features MAY move the pin forward.
2. The sprint spec MUST cite the new SHA in its preamble.
3. Every ADR that quotes Pi MUST cite the SHA (either inline or by reference to this ADR's "current pin").
4. When the pin moves, the previous pin's ADR is appended to a "pin history" table here.

#### Pin history
| Sprint | Pin SHA | Date | Reason |
|--------|---------|------|--------|
| 2.5 (Phase 1.4) | `734e08edf82ff315bc3d96472a6ebfa69a1d8016` | 2026-05-17 | initial pin; spec citations anchored |

#### Consequences
- Parity audits become reproducible.
- Forward-port effort is now visible per-sprint (delta between old pin and new pin).
- Existing ADRs (0017, 0018, 0019, 0021, 0022, 0023, 0025) are silently anchored to this SHA going forward; if a quote breaks against a newer SHA, that's a Phase 2.x action item, not a Phase 1.4 bug.

---

### D.2 ADR-0035 — Error Code Taxonomy

**File:** `docs/decisions/0035-error-code-taxonomy.md`
**Status:** `Draft (Phase 1.4 shipped — Aelix subset; full taxonomy Phase 2.1+)`

#### Context
Aelix `AgentHarnessError.code` is currently `Literal["busy", "invalid_state", "invalid_argument", "hook", "unknown"]` (5 codes; `harness/core.py:102`). Pi (`packages/agent/src/harness/agent-harness.ts`, SHA `734e08e…`) uses 10 codes inferred from string-literal usage at the cited lines.

#### Pi codes (research)
| Pi code | Pi citation (SHA `734e08e…`) | Used for |
|---------|------------------------------|----------|
| `"busy"` | agent-harness.ts:369,381,391 | phase != idle when prompt/abort entered |
| `"invalid_state"` | agent-harness.ts:356,409,461 | wrong phase for compaction / navigateTree |
| `"invalid_argument"` | agent-harness.ts:292,393,549 | bad activeToolNames / branch IDs / etc. |
| `"hook"` | agent-harness.ts:282 | hook handler threw |
| `"unknown"` | agent-harness.ts:354,407 | unclassified internal |
| `"session"` | agent-harness.ts:319,543 | session persistence failure |
| `"compaction"` | agent-harness.ts:461,466,476 | compact() failure |
| `"auth"` | agent-harness.ts:458,616 | getApiKey / getApiKeyAndHeaders failure |
| `"branch_summary"` | agent-harness.ts:319,633 | navigateTree summary failure |
| `"aborted"` | agent-harness.ts:630 | cooperative abort surfaced as error |

#### Decision
Aelix today retains its 5 codes (Pi parity 1:1 for the codes that map). Add **5 placeholder codes** to the `Literal` union as Phase 2.x land-them-as-they-arrive:

| Aelix code (Phase 1.4) | Pi code | Phase when wired |
|------------------------|---------|------------------|
| `"busy"` | `"busy"` | Already wired |
| `"invalid_state"` | `"invalid_state"` | Already wired |
| `"invalid_argument"` | `"invalid_argument"` | Already wired |
| `"hook"` | `"hook"` | Already wired |
| `"unknown"` | `"unknown"` | Already wired |
| `"session"` (new) | `"session"` | Phase 2.2 (ADR-0022) |
| `"compaction"` (new) | `"compaction"` | Phase 2.2 (ADR-0023) |
| `"auth"` (new) | `"auth"` | Phase 4 (ADR-0020 / provider work) |
| `"branch_summary"` (new) | `"branch_summary"` | Phase 2.2 (ADR-0023) |
| `"aborted"` (new) | `"aborted"` | Phase 2.1 (ADR-0017) |

**Phase 1.4 work:** widen the `Literal` only. No new raises. The new codes appear in the union but no codepath emits them.

```python
class AgentHarnessError(Exception):
    def __init__(
        self,
        code: Literal[
            "busy",
            "invalid_state",
            "invalid_argument",
            "hook",
            "unknown",
            # ADR-0035 placeholders (Phase 2.x):
            "session",
            "compaction",
            "auth",
            "branch_summary",
            "aborted",
        ],
        message: str,
    ) -> None:
        ...
```

#### Consequences
- `match err.code:` exhaustive matches stay sound today (every Phase 1.4 code path still emits one of the original 5).
- ADR-0030 (assert_never) integration: when Phase 2.1 enables exhaustive checks on harness errors, the 5 new codes will need handlers or explicit "unreachable in Phase 1.4" stubs.
- Third-party error mapping (e.g. CLI exit-code translation) can now anticipate all 10 codes.

---

### D.3 ADR-0036 — Loop `AgentEvent` vs Harness `AgentHarnessEvent` Distinction (F-7)

**File:** `docs/decisions/0036-loop-event-vs-harness-event-distinction.md`
**Status:** `Accepted (Sprint 2.5 shipped — model affirmed; expansion Phase 2.1)`

#### Context
F-7 (Sprint 1 re-eval) flagged that ADR-0017 conflates two distinct concepts:
1. **Loop events** — emitted by the bare `agent_loop()` (`packages/aelix-agent-core/src/aelix_agent_core/types.py:202-213`); 10 variants, all stream-level.
2. **Harness events** — emitted by `AgentHarness` (`packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:54-71`); 16 today, ~28 in Phase 2.1.

Pi makes the same split explicitly:
- **Loop `AgentEvent`** at `packages/agent/src/types.ts:275-295` (SHA `734e08e…`): 10 variants identical to Aelix's set.
- **Harness `AgentHarnessEvent`** at `packages/agent/src/harness/types.ts:467-469`: `AgentEvent | AgentHarnessOwnEvent` (a 17-member own-event union covering lifecycle + provider + session + selector events).

Aelix code already implements the split (see `_to_hook_event` projection at `harness/core.py:633-670`), but no ADR cements the design — ADR-0017 reads as if there's one big event union.

#### Decision
Affirm the two-tier model **explicitly**:

1. **`AgentEvent`** (`aelix_agent_core.types`) is the loop's stream-level union. Listeners subscribed via `Agent.subscribe()` or `AgentHarness.subscribe()` receive these. Phase 1.4 ships unchanged at 10 events. This union mirrors Pi `AgentEvent` (`types.ts:275-295`).

2. **`HookEvent`** (`aelix_agent_core.harness.hooks`) is the harness's lifecycle/extension union. Handlers registered via `HookBus.on()` receive these. Phase 1.4 ships unchanged at 16; Phase 2.1 (ADR-0017) grows to ~28 matching Pi's `AgentHarnessOwnEvent` set.

3. **Projection** — `_to_hook_event(event: AgentEvent) -> HookEvent | None` (`harness/core.py:633-670`) is the **single, canonical** translation point. Hook-only events (e.g. `tool_call`, `tool_result`, `before_agent_start`) are emitted directly by the harness, not via projection. Loop-only events with no hook counterpart return `None`.

4. **Naming** — Aelix uses `HookEvent` where Pi uses `AgentHarnessEvent`. We retain `HookEvent` for backward compatibility with ADR-0011/0017 and the existing public API. A type alias `AgentHarnessEvent = HookEvent` MAY be added in Phase 2.1 for Pi-citation clarity (not in Phase 1.4 scope).

5. **ADR-0017 amendment** — when ADR-0017 lands as Accepted in Phase 2.1, it MUST cross-reference this ADR and explicitly state that its 28-event catalogue concerns `HookEvent`, leaving the 10-event `AgentEvent` set untouched.

#### Consequences
- Future contributors can no longer confuse "add a new event" between loop and harness — the ADR makes the question "is this stream-level or lifecycle-level?" explicit.
- ADR-0030 (assert_never) applies to **both** unions independently. Two separate exhaustive switches, not one combined one.
- The projection function gains a docstring referencing this ADR (cosmetic Phase 1.4 task).
- Pi parity tests under ADR-0029 split into `test_loop_event_set_matches_pi` and `test_hook_event_set_matches_pi_harness_own_event` (Phase 2.1+ work).

---

### D.4 ADR-0037 — Streaming Event Union (Pi Parity)

**File:** `docs/decisions/0037-streaming-event-union-pi-parity.md`
**Status:** `Draft (Phase 1.4 shell; adapter coverage Phase 4)`

#### Context
Aelix today: `AssistantMessageEvent = AssistantStartEvent | TextDeltaEvent | ToolCallDeltaEvent | AssistantEndEvent` (4 events; `streaming.py:85-87`).

Pi `AssistantMessageEvent` at `packages/ai/src/types.ts:366-378` (SHA `734e08e…`) has **12 events**:

| # | Pi type | Pi line | Aelix today |
|---|---------|---------|-------------|
| 1 | `start` | 367 | ✓ `AssistantStartEvent` |
| 2 | `text_start` | 368 | Absent |
| 3 | `text_delta` | 369 | ✓ `TextDeltaEvent` (no `contentIndex`) |
| 4 | `text_end` | 370 | Absent |
| 5 | `thinking_start` | 371 | Absent |
| 6 | `thinking_delta` | 372 | Absent |
| 7 | `thinking_end` | 373 | Absent |
| 8 | `toolcall_start` | 374 | Absent |
| 9 | `toolcall_delta` | 375 | ✓ `ToolCallDeltaEvent` (no `contentIndex`) |
| 10 | `toolcall_end` | 376 | Absent |
| 11 | `done` | 377 | Replaced by Aelix `end` (`AssistantEndEvent`) |
| 12 | `error` | 378 | Absent (Pi has typed error event) |

#### Decision

**Phase 1.4 ships the full 12-event union as type definitions, with `contentIndex` fields, and a rename of `end` → `done` with backward-compat alias.** Adapter emission coverage lands in Phase 4.

##### D.4.1 Phase 1.4 type additions

In `packages/aelix-ai/src/aelix_ai/streaming.py`:

```python
@dataclass(frozen=True)
class TextStartEvent:
    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["text_start"] = "text_start"

# TextDeltaEvent — add content_index: int = 0 field; preserve backward-compat
# by keeping current fields and giving content_index a default.

@dataclass(frozen=True)
class TextEndEvent:
    content_index: int = 0
    content: str = ""
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["text_end"] = "text_end"

@dataclass(frozen=True)
class ThinkingStartEvent:
    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["thinking_start"] = "thinking_start"

@dataclass(frozen=True)
class ThinkingDeltaEvent:
    content_index: int = 0
    delta: str = ""
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["thinking_delta"] = "thinking_delta"

@dataclass(frozen=True)
class ThinkingEndEvent:
    content_index: int = 0
    content: str = ""
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["thinking_end"] = "thinking_end"

@dataclass(frozen=True)
class ToolCallStartEvent:
    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["toolcall_start"] = "toolcall_start"

# ToolCallDeltaEvent — add content_index: int = 0; preserve existing fields.

@dataclass(frozen=True)
class ToolCallEndEvent:
    content_index: int = 0
    tool_call: ToolCallContent = field(default_factory=ToolCallContent)
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["toolcall_end"] = "toolcall_end"

@dataclass(frozen=True)
class AssistantDoneEvent:
    reason: Literal["stop", "length", "tool_use"] = "stop"
    message: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["done"] = "done"

@dataclass(frozen=True)
class AssistantErrorEvent:
    reason: Literal["aborted", "error"] = "error"
    message: AssistantMessage = field(default_factory=AssistantMessage)
    error_message: str | None = None
    type: Literal["error"] = "error"
```

The union becomes:

```python
AssistantMessageEvent = (
    AssistantStartEvent
    | TextStartEvent | TextDeltaEvent | TextEndEvent
    | ThinkingStartEvent | ThinkingDeltaEvent | ThinkingEndEvent
    | ToolCallStartEvent | ToolCallDeltaEvent | ToolCallEndEvent
    | AssistantDoneEvent | AssistantErrorEvent
    | AssistantEndEvent  # legacy alias kept for Phase 1.4; deprecated by ADR-0037
)
```

##### D.4.2 Backward-compat for `AssistantEndEvent`

Existing tests and the mock `stream_fn` flows in `tests/test_agent_loop.py` and `tests/test_agent_harness.py` emit `AssistantEndEvent`. Phase 1.4 retains `AssistantEndEvent` in the union; Phase 4 adapters MUST emit `AssistantDoneEvent` (or `AssistantErrorEvent`). Phase 5 may remove `AssistantEndEvent` with a deprecation cycle.

##### D.4.3 What does NOT change in Phase 1.4
- `MessageUpdateEvent.assistant_message_event` field continues to carry whichever variant the stream_fn produced.
- No loop logic interprets the new events. The loop today only inspects `type == "end"` semantics; that path remains valid via `AssistantEndEvent`.
- The harness `_to_hook_event` projection is unaffected.

#### Consequences
- Phase 4 adapter authors have the **exact target type set** with no surprises.
- Pyright sees 13 union members; downstream `match` statements need `case _:` exhaustive handlers (or `assert_never` per ADR-0030).
- Existing mock streams continue to work; no test rewrites in Phase 1.4 beyond ~2 sanity tests asserting the new events instantiate cleanly.
- Adds approximately 9 new class definitions to `streaming.py` — pure additive cost, no behavior risk.

#### Test plan
- 2 tests in `tests/test_streaming_events.py`: (a) each new event instantiates with defaults; (b) `AssistantMessageEvent` union includes each `type` literal.

---

### D.5 ADR-0038 — `stream_simple` Dispatch Shell Rationale (Phase 1 Boundary)

**File:** `docs/decisions/0038-stream-simple-dispatch-shell-phase-1-boundary.md`
**Status:** `Accepted (Sprint 2.5 / Phase 1.4 shipped — body lands Phase 4)`

#### Context
Phase 1 (re-evaluation §5) defines parity for `aelix-ai` as: full message types, content blocks, Model/Cost/Context, AssistantMessageEvent union, Tool/ToolResult, and a working `stream_simple`. Today's `stream_simple` is a `NotImplementedError` stub. The question: **does Phase 1 exit when the shell ships, or only when adapters ship?**

#### Decision
**Phase 1 exits at the shell.** Adapters are Phase 4 scope.

Mirrors ADR-0025's "minimal shell + owning-ADR" pattern:
- ADR-0025: `_TurnState` ships 2 fields; remaining 7 land via owning ADRs (0017/0022).
- ADR-0038: `stream_simple` ships dispatch + registry + typed error; provider bodies land via owning ADR-0020-adjacent provider work.

#### Justification
1. **Pi parity for the `aelix-ai` API surface is achievable without adapters.** Pi's `stream.ts` itself is 6 lines (`stream.ts:45-50`) — the *dispatch* is what makes `streamSimple` callable; the *adapters* are independent modules. Aelix can match the dispatch line-for-line today.
2. **Provider work is Phase 4 scope** per re-eval §5. Bundling adapters into Phase 1 collapses two phases with very different risk profiles (type-level vs network/auth/streaming) into one.
3. **Testability** — the shell is fully testable with mock `StreamFn` injections (5 tests in Section A.3). Adapters require credentialed integration tests (`tests/integration/`), a Phase 4 concern.
4. **Compounding parity** — Phase 1.4's shell unblocks Phase 2.x harness work that may want to call `stream_simple` indirectly (e.g. `before_provider_request` event). Without the shell, every Phase 2.x test that touches that codepath would need ad-hoc patching.
5. **Reversible** — if a Phase 4 provider design forces a `stream_simple` signature change, the shell is 30 lines of code to revise; no caller code today depends on Adapter-emitted events.

#### Alternatives considered
- **Ship adapters now (collapse Phase 1 + Phase 4):** rejected — 3-4 weeks of work, requires OAuth/HTTP/streaming machinery that has no other Phase 1 customer.
- **Keep `NotImplementedError`:** rejected — third-party adapter packages (Phase 4) need the registry API surface stable before they can be authored; landing the registry now lets Phase 4 ship a single PR per adapter rather than co-evolving registry+adapter.
- **Use a global `STREAM_SIMPLE_FN` module variable (no registry):** rejected — Pi's registry pattern supports multiple APIs cleanly (`anthropic-messages`, `openai-chat-completions`, `openai-responses`, etc.); the module-variable shortcut would force a redesign at Phase 4.

#### Consequences
- Phase 1 has a clean exit gate: shell + 5 tests + 5 ADRs.
- Phase 4 PRs each register a single api; no shared-module ownership conflicts.
- A user calling `stream_simple` today gets a typed, actionable error message (Section A.2.1) — not a Python `NotImplementedError`.

---

## Section E — Acceptance Checklist

The W2 implementer signs off when **every** item below passes locally and the verifier agent confirms:

- [ ] All 152 existing tests pass (`uv run pytest` from repo root): exit code 0.
- [ ] **N new tests pass**, where N = 5 (Section A) + 3 (Section B) + 2 (Section D.4) = **10 new tests**. Total tests after Phase 1.4 = **162**.
- [ ] `uv run ruff check .` clean (no errors, warnings unchanged from Phase 1.3).
- [ ] `uv run ruff format --check .` clean.
- [ ] Pyright spike (`uv run python scripts/pyright_spike.py` or equivalent) reports **8 expected errors** — no regression. ADR-0037's 9 new dataclasses must not introduce new pyright errors.
- [ ] `uv run aelix` demo unchanged (3-line echo output, no behavior delta).
- [ ] `docs/decisions/README.md` index table updated with rows for ADR-0034, 0035, 0036, 0037, 0038 (each with correct status string).
- [ ] Each ADR file present at `docs/decisions/0034-…md` … `docs/decisions/0038-…md`.
- [ ] ADR Status strings:
    - 0034: `Accepted (Sprint 2.5 shipped)`
    - 0035: `Draft (Phase 1.4 shipped — Aelix subset; full taxonomy Phase 2.1+)`
    - 0036: `Accepted (Sprint 2.5 shipped — model affirmed; expansion Phase 2.1)`
    - 0037: `Draft (Phase 1.4 shell; adapter coverage Phase 4)`
    - 0038: `Accepted (Sprint 2.5 / Phase 1.4 shipped — body lands Phase 4)`
- [ ] F-6 placeholder fields type-check: smoke test in `tests/test_harness_options_placeholders.py` (Section B.5 test #2) passes when instantiating `AgentHarnessOptions(model=Model(), session=object(), env={}, resources=[], thinking_level="medium", active_tool_names=["x"], get_api_key_and_headers=lambda m: None, stream_options={})`.
- [ ] `StreamSimpleError("no_provider_registered", "...")` is raised by `stream_simple(Model(api="anthropic"), Context(), SimpleStreamOptions())` when no provider is registered.
- [ ] Umbrella package `aelix` exposes `register_provider`, `unregister_provider`, `get_registered_providers`, `StreamSimpleError` — verify with `from aelix import register_provider, unregister_provider, get_registered_providers, StreamSimpleError`.
- [ ] `aelix_ai` package exposes the same four names — verify with `from aelix_ai import ...`.
- [ ] `_to_hook_event` projection function carries a docstring referencing ADR-0036 (one-line addition; no behavior change).
- [ ] `ExtensionContext.model` field carries the F-8 docstring per Section C.3.
- [ ] `Model` class docstring carries the F-8 cross-reference per Section C.3.
- [ ] Pi pin in spec preamble (this file) and in ADR-0034 match: `734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

---

## Section F — Out of Scope (Defer to Sprint 3 or Later)

The following are **explicitly excluded** from Phase 1.4. Any work on these items in W2 is a scope violation requiring a critic-pass blocker:

- ❌ Pyright cleanup (142 errors — task #37 backlog; Sprint 3+ scope).
- ❌ CI/CD setup (GitHub Actions, pre-commit hooks beyond what exists).
- ❌ Provider adapters (Anthropic / OpenAI / OpenRouter implementations — Phase 4).
- ❌ Any Phase 2.1 behavior wiring: parallel tool execution, `nextTurn()` / `appendMessage()`, the 8 harness setters, `before_provider_request` event, etc.
- ❌ Any Phase 2.2 behavior wiring: `Session` interface, JSONL persistence, `compact()`, `navigateTree()`.
- ❌ Removing or renaming existing `AgentHarnessOptions` fields (e.g. `get_api_key`, `cwd`). Phase 1.4 is **additive only**.
- ❌ Emitting any of the new `AgentHarnessError` codes (`session`, `compaction`, `auth`, `branch_summary`, `aborted`) — they're widened in the `Literal` only.
- ❌ Adapter implementations of the new `AssistantMessageEvent` variants — they're type-only in Phase 1.4.
- ❌ TUI / Web UI / RPC mode work (Phase 4-5).
- ❌ Marketplace / `aelix install` / `aelix login` CLI subcommands (Phase 4-5).
- ❌ Project memory or notepad refactors not directly required by this sprint.

If a Phase 1.4 PR touches any of the above, it is rejected at code review and the work is split into a follow-up PR.

---

## Appendix · Import Map for W2 (files touched)

### New files
- `packages/aelix-ai/src/aelix_ai/api_registry.py` — provider registry + `_resolve_provider`.
- `tests/test_stream_simple_shell.py` — 5 tests.
- `tests/test_harness_options_placeholders.py` — 3 tests.
- `tests/test_streaming_events.py` — 2 tests.
- `docs/decisions/0034-pi-reference-version-pin.md`
- `docs/decisions/0035-error-code-taxonomy.md`
- `docs/decisions/0036-loop-event-vs-harness-event-distinction.md`
- `docs/decisions/0037-streaming-event-union-pi-parity.md`
- `docs/decisions/0038-stream-simple-dispatch-shell-phase-1-boundary.md`

### Modified files (additive only)
- `packages/aelix-ai/src/aelix_ai/streaming.py` — implement `stream_simple` body; add `StreamSimpleError`, 9 new dataclasses, expand `AssistantMessageEvent` union, add `content_index` defaults to existing `TextDeltaEvent` / `ToolCallDeltaEvent`, update Model docstring (F-8 reference).
- `packages/aelix-ai/src/aelix_ai/__init__.py` — re-export `StreamSimpleError`, `register_provider`, `unregister_provider`, `get_registered_providers`, plus the new event classes.
- `src/aelix/__init__.py` — re-export the same four registry/error symbols (events optional; umbrella stays lean).
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` — add 7 placeholder fields on `AgentHarnessOptions` (Section B.3); add docstring on `_to_hook_event` referencing ADR-0036.
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (same file) — widen `AgentHarnessError.code` Literal to 10 entries per ADR-0035.
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` — add F-8 docstring to `ExtensionContext.model`.
- `docs/decisions/README.md` — extend the index table with 5 new ADR rows.

### Untouched (verify no diff in W4)
- `packages/aelix-agent-core/src/aelix_agent_core/loop.py`
- `packages/aelix-agent-core/src/aelix_agent_core/agent.py`
- `packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py`
- `packages/aelix-agent-core/src/aelix_agent_core/types.py`
- `packages/aelix-coding-agent/src/aelix_coding_agent/builtin/*.py`
- `packages/aelix-ai/src/aelix_ai/messages.py`
- `packages/aelix-ai/src/aelix_ai/tools.py`

---

End of binding spec. Critic review precedes W2 implementation per ADR-0032.

---

## W6 Spec Deviations

- `tests/test_stream_simple_shell.py` → shipped as `tests/test_stream_simple_dispatch.py` (W2 chose the more accurate name; spec acceptance §E updated).
- `tests/test_harness_options_placeholders.py` → shipped as `tests/test_agent_harness_options_placeholders.py` (same rationale).
- Spec §B.4 originally asked to widen `AgentHarnessError.code` Literal in Phase 1.4. Descoped via ADR-0035 Status update — owning-ADR cadence per code, matching ADR-0025 pattern.
- Spec §D.4 originally asked to land 9 new streaming event dataclasses in Phase 1.4. Descoped via ADR-0037 Status update — adapter coverage lands the body in Phase 4.
- Eager-raise wrapper added to `stream_simple` in W6 per W5 §2 finding (Pi semantic parity, not in original spec).

