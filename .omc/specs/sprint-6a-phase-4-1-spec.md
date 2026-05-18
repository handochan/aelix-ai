# Sprint 6a · Phase 4.1 — Provider Adapter Framework + Anthropic + 3 Deferred Emit Sites (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint closes the DEFERRED_ALLOWLIST to **ZERO** — full Pi parity for emit-site catalogue.

---

## §0 — P-37 ~ P-43 INVESTIGATION (Pi drift verified at SHA 734e08e)

### P-37 — ADR-0034 repo slug correction
ADR-0034 cites `badlogic/pi-mono` but actual canonical repo at pinned SHA is **`earendil-works/pi`**. Same SHA. **Amend ADR-0034.**

### P-38 — Pi Anthropic adapter uses official SDK
Pi `providers/anthropic.ts:1-8` uses `@anthropic-ai/sdk` package. **Decision:** Aelix uses official `anthropic>=0.40,<1.0` Python SDK for byte-level Pi parity. Wrapped via thin `providers/_anthropic_client.py` for future `httpx` swap.

### P-39 — 12 AssistantMessageEvent variants (Pi types.ts:347-359)
ADR-0037 9-class plan is **off by one**. Actual delta:
- **8 net new dataclasses**: text_start, text_end, thinking_start/delta/end, toolcall_start, toolcall_end, error
- **1 rename**: `AssistantEndEvent` → `AssistantDoneEvent` (Pi `done`)
- **1 alias kept**: `AssistantEndEvent` as deprecated subclass for back-compat
- **1 spelling fix (P-39d SILENT DRIFT)**: Aelix `tool_call_delta` → Pi `toolcall_delta` (no underscore between `tool` and `call`)
- **2 field backfills**: TextDeltaEvent + ToolCallDeltaEvent add `content_index: int = 0`, `partial: AssistantMessage = ...`

### P-40 — All 3 emit sites in single `createStreamFn` closure
Pi `agent-harness.ts:358-389` `createStreamFn`:
- `before_provider_request` — emit before SDK call (`:366`)
- `before_provider_payload` — SDK `onPayload` callback (`:373`, Anthropic `:489-492`)
- `after_provider_response` — SDK `onResponse` callback (`:374-380`, Anthropic `:499`)

### P-41 — Pi `applyStreamOptionsPatch` deep-merge (ADR-0017 known gap)
Pi `agent-harness.ts:89-129` does deep-merge with **delete-on-undefined** for `headers`/`metadata` keys. Aelix `harness/hooks.py:1304` `dict.update` is shallow. Fix required.

### P-42 — AgentHarnessError code widening
Pi `agent/src/harness/types.ts:211-220`: **9 codes** (`busy|invalid_state|invalid_argument|session|hook|auth|compaction|branch_summary|unknown`). Aelix `aborted` is **Aelix-additive divergence** (Pi has none). Sprint 6a adds **+1 code: `"auth"`**. Literal at `core.py:139` is also stale — needs cleanup.

### P-42b — `network_error`/`tool_error`/`provider_auth` NOT in Pi
Spec preamble cited these — NOT Pi parity. **DROP from scope.** SDK exceptions map to `"auth"` (401/403) or surface as `AssistantErrorEvent`.

### P-43 — `getApiKeyAndHeaders` shape
Pi `types.ts:808-811`: `(model) => Promise<{apiKey?, headers?} | undefined>`. Aelix wires F-6 placeholder.

### Sub-sprint split (CONFIRMED)
| Sub-sprint | Scope | LOC est |
|---|---|---|
| **6a (this)** | Provider Protocol + Anthropic adapter + 3 emit sites + 8 new + rename variants + deep-merge fix + `"auth"` error code | ~750 prod + ~700 test |
| 6b | OpenAI + OpenRouter adapters | ~450 prod + ~400 test |
| 6c | OAuth + RPC mode (ADR-0020) + secrets ADR | ~700 prod + ~500 test |

**6a closes DEFERRED_ALLOWLIST to zero.**

---

## §A — Provider Adapter Framework (NEW ADR-0045)

New package: `packages/aelix-ai/src/aelix_ai/providers/`

### A.1 `providers/_base.py`
```python
class Provider(Protocol):
    api: str
    async def stream(self, model, context, options): ...
    async def stream_simple(self, model, context, options): ...
```

### A.2 `api_registry.py` extensions
- `register_provider_object(provider: Provider, source_id: str | None = None)`
- `unregister_providers_by_source(source_id: str)` — Pi `unregisterApiProviders` parity
- Keep `register_provider(api, fn)` as Phase 1.4 back-compat shim

### A.3 `SimpleStreamOptions` extensions in `streaming.py`
Add: `cache_retention`, `transport`, `timeout_ms`, `max_retries`, `max_retry_delay_ms`, `on_payload`, `on_response`, `reasoning`.

New `ProviderResponse`:
```python
@dataclass(frozen=True)
class ProviderResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
```

---

## §B — Anthropic Adapter

File: `packages/aelix-ai/src/aelix_ai/providers/anthropic.py`

**Dependency:** Add `anthropic>=0.40,<1.0` to `packages/aelix-ai/pyproject.toml`.

**Adapter shape** (mirrors Pi `providers/anthropic.ts:428-687`):

```python
ANTHROPIC_API: str = "anthropic-messages"  # Pi KnownApi

async def stream_anthropic(model, context, options=None) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: providers/anthropic.ts:428-687."""
    output = AssistantMessage(...)
    try:
        client = options.client or _create_client(model, options)
        params = _build_params(model, context, is_oauth=_is_oauth_token(...), options=options)

        # emit before_provider_payload (via on_payload callback)
        if options.on_payload:
            next_params = await _maybe_await(options.on_payload(params, model))
            if next_params is not None:
                params = next_params

        response = await client.messages.create(**params, stream=True).asResponse()

        # emit after_provider_response (via on_response callback)
        if options.on_response:
            await _maybe_await(options.on_response(
                ProviderResponse(response.status, dict(response.headers)), model
            ))

        yield AssistantStartEvent(partial=output)

        async for raw_event in _iterate_anthropic_events(response, options.signal):
            # Pi anthropic.ts:506-660 case-by-case translation:
            # message_start → update output.usage
            # content_block_start (text/thinking/tool_use) → push *_start, append block
            # content_block_delta (text/thinking/input_json) → push *_delta
            # content_block_stop → push *_end
            # message_delta → update stop_reason + usage
            ...

        if options.signal and options.signal.aborted:
            raise RuntimeError("Request was aborted")
        if output.stop_reason in ("aborted", "error"):
            raise RuntimeError("An unknown error occurred")

        yield AssistantDoneEvent(reason=output.stop_reason, message=output)

    except Exception as exc:
        output.stop_reason = "aborted" if (options.signal and options.signal.aborted) else "error"
        output.error_message = str(exc)
        yield AssistantErrorEvent(reason=output.stop_reason, error=output)


ANTHROPIC_PROVIDER = AnthropicProviderImpl(
    api=ANTHROPIC_API,
    stream=stream_anthropic,
    stream_simple=stream_simple_anthropic,
)
```

**Helper module:** `providers/_anthropic_transforms.py` ports `transformMessages`, `buildParams`, `convertContentBlocks`, `mapStopReason`.

**OAuth detection:** `_is_oauth_token(api_key) -> bool` — port Pi `:769` (`startswith("sk-ant-oat")`); 6a rejects OAuth with `AgentHarnessError("auth", "OAuth not yet supported — Sprint 6c")`.

`register_all()` → `register_provider_object(ANTHROPIC_PROVIDER, source_id="aelix-ai.builtin")`.

---

## §C — 12 AssistantMessageEvent Variants (ADR-0037 land)

Edit `packages/aelix-ai/src/aelix_ai/streaming.py`:

### C.1 Add 8 new dataclasses (P-39 verified)
- `TextStartEvent(content_index, partial, type="text_start")`
- `TextEndEvent(content_index, content, partial, type="text_end")`
- `ThinkingStartEvent(content_index, partial, type="thinking_start")`
- `ThinkingDeltaEvent(content_index, delta, partial, type="thinking_delta")`
- `ThinkingEndEvent(content_index, content, partial, type="thinking_end")`
- `ToolCallStartEvent(content_index, partial, type="toolcall_start")`
- `ToolCallEndEvent(content_index, tool_call, partial, type="toolcall_end")`
- `AssistantErrorEvent(reason, error, error_message=None, type="error")`

### C.2 Backfill existing dataclasses (additive, defaults preserve back-compat)
- `TextDeltaEvent`: add `content_index: int = 0`, `partial: AssistantMessage = field(default_factory=AssistantMessage)`
- `ToolCallDeltaEvent`: add `content_index: int = 0`, `partial: AssistantMessage = ...`; rename `input_delta` → `delta` (keep `input_delta` as deprecated property); change `type` Literal `"tool_call_delta"` → `"toolcall_delta"` (P-39d Pi parity)

### C.3 Rename + alias (P-39a)
- Add `AssistantDoneEvent(reason: Literal["stop","length","tool_use"], message, type="done")`
- Keep `AssistantEndEvent` as **deprecated subclass** of `AssistantDoneEvent` (type Literal `"end"`) for legacy test mocks

### C.4 Union update
```python
AssistantMessageEvent = (
    AssistantStartEvent
    | TextStartEvent | TextDeltaEvent | TextEndEvent
    | ThinkingStartEvent | ThinkingDeltaEvent | ThinkingEndEvent
    | ToolCallStartEvent | ToolCallDeltaEvent | ToolCallEndEvent
    | AssistantDoneEvent | AssistantErrorEvent
    | AssistantEndEvent  # legacy
)
```

### C.5 Loop consumer update (`loop.py:257-285`)
- Accept both `"end"` and `"done"` as terminal
- Accept `"error"` as terminal (`AssistantErrorEvent` → final message stop_reason in `{"aborted","error"}`)
- Accept new `_start`/`_end`/`thinking_*`/`toolcall_*` as `MessageUpdateEvent` projections
- Replace `"tool_call_delta"` spelling with `"toolcall_delta"` (P-39d)

### C.6 Umbrella exports
Update `aelix_ai/__init__.py` + `src/aelix/__init__.py` with 9 new symbols.

---

## §D — 3 Deferred Event Emit Sites

In `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`:

### D.1 Replace direct agent_loop call at `:1989-1995`
```python
# If options.stream_fn injected → use it (preserves Sprint 1-5 test mock behavior)
# Else → use self._make_stream_fn(get_turn_state) (Phase 4 production path)
```

### D.2 New `_make_stream_fn(self, get_turn_state)` private helper

Per P-40 (Pi `createStreamFn`):
```python
def _make_stream_fn(self, get_turn_state) -> StreamFn:
    async def stream_fn(model, context, options):
        turn_state = get_turn_state()

        # Resolve auth (raises AgentHarnessError("auth") on failure)
        auth = None
        if self._options.get_api_key_and_headers is not None:
            try:
                auth = await _maybe_await(self._options.get_api_key_and_headers(model))
            except Exception as exc:
                raise AgentHarnessError("auth", f"get_api_key_and_headers failed: {exc}") from exc
            if auth is None or (not auth.get("apiKey") and not auth.get("headers")):
                raise AgentHarnessError("auth", "Provider requires apiKey or headers")

        # Build snapshot (mirrors Pi snapshotOptions)
        snapshot = SimpleStreamOptions(
            api_key=(auth or {}).get("apiKey"),
            headers=_merge_headers(turn_state.stream_options.get("headers"), (auth or {}).get("headers")),
            metadata=dict(turn_state.stream_options.get("metadata", {})),
            signal=options.signal,
        )

        # Emit 1: before_provider_request → chained patch
        request_opts = await self._emit_before_provider_request(model, turn_state.session_id, snapshot)

        # Emit 2 + 3 wired via callbacks
        async def _on_payload(params, m):
            return await self._emit_before_provider_payload(model, params)

        async def _on_response(response, m):
            await self._emit_after_provider_response(model, response.status, response.headers)

        opts_with_callbacks = replace(request_opts, on_payload=_on_payload, on_response=_on_response)
        return await stream_simple(model, context, opts_with_callbacks)

    return stream_fn
```

### D.3 `_TurnState` extension
Add `stream_options: dict[str, Any]` field; clone at turn start from `self._state.stream_options` (Pi parity).

---

## §E — Deep-merge Fix (P-41)

Replace `_reducer_before_provider_request` body at `harness/hooks.py:1266-1306`:

```python
def _apply_stream_options_patch(base: dict, patch: dict | None) -> dict | None:
    """Pi parity agent-harness.ts:89-129 — deep-merge with delete-on-undefined."""
    if patch is None:
        return base

    result = dict(base)  # shallow clone top level

    for key in ("transport", "timeoutMs", "maxRetries", "maxRetryDelayMs", "cacheRetention"):
        if key in patch:
            result[key] = patch[key]

    if "headers" in patch:
        if patch["headers"] is None:
            result["headers"] = None
        else:
            headers = dict(result.get("headers") or {})
            for k, v in patch["headers"].items():
                if v is None:
                    headers.pop(k, None)
                else:
                    headers[k] = v
            result["headers"] = headers if headers else None

    if "metadata" in patch:
        if patch["metadata"] is None:
            result["metadata"] = None
        else:
            metadata = dict(result.get("metadata") or {})
            for k, v in patch["metadata"].items():
                if v is None:
                    metadata.pop(k, None)
                else:
                    metadata[k] = v
            result["metadata"] = metadata if metadata else None

    return result if result else None
```

Remove `TODO(Phase-4)` comment.

---

## §F — AgentHarnessError Widening (P-42)

Edit `harness/core.py:139`:
```python
code: Literal[
    "busy", "invalid_state", "invalid_argument",
    "hook", "unknown", "compaction",
    "session", "branch_summary", "aborted",  # already raised — Literal stale
    "auth",  # NEW Sprint 6a
],
```

Raise `AgentHarnessError("auth", ...)` in `_make_stream_fn` per D.2 above.

**ADR-0035 amendment:** mark `aborted` as Aelix-additive (Pi 9 codes parity).

---

## §G — Tests (~80 new tests + 3 fixtures)

| File | Tests |
|---|---|
| `tests/providers/test_anthropic_stream_events.py` | 15 (Pi event translations + abort + OAuth raise) |
| `tests/providers/test_anthropic_auth.py` | 8 (API-key/OAuth detection/options.client/error mapping) |
| `tests/providers/test_anthropic_payload_callback.py` | 4 (on_payload mutation, on_response observation) |
| `tests/test_api_registry_provider_protocol.py` | 6 (Provider object register, unregister by source, back-compat shim) |
| `tests/test_streaming_12_variants.py` | 12 (each event roundtrip + legacy AssistantEndEvent) |
| `tests/test_apply_stream_options_patch.py` | 12 (P-41 deep-merge cases) |
| `tests/test_before_provider_request_emit.py` | 5 |
| `tests/test_before_provider_payload_emit.py` | 5 |
| `tests/test_after_provider_response_emit.py` | 4 |
| `tests/test_agent_harness_auth_error.py` | 5 |
| `tests/pi_parity/test_phase_4_strict_superset.py` | Closure pin |

**Fixtures** under `tests/pi_parity/fixtures/`:
- `pi_assistant_message_events_734e08e.json` — 12 variants × field shape
- `pi_anthropic_sse_capture_734e08e.json` — recorded SSE transcript
- `pi_apply_stream_options_patch_734e08e.json` — 12 patch triples

---

## §H — ADR Amendments + NEW ADRs

| ADR | Change |
|---|---|
| ADR-0034 | Amend — repo slug `badlogic/pi-mono` → `earendil-works/pi` (P-37) |
| ADR-0037 | Draft → **Accepted** — 8 dataclasses + rename + spelling fix |
| ADR-0038 | Amend — "Body shipped Sprint 6a" |
| ADR-0035 | Draft → **Accepted** — 10 codes (9 Pi + 1 Aelix-additive `aborted`) |
| **NEW ADR-0045** | Provider Adapter Interface (Protocol + register/unregister by source) |
| **NEW ADR-0046** | Phase 4 Strict Superset Closure (mirrors ADR-0039/0040/0044) |

---

## §I — Acceptance Checklist

- [ ] `aelix-ai` adds `anthropic>=0.40` dep
- [ ] `providers/anthropic.py` ships + `register_all()` wires it
- [ ] 12 AssistantMessageEvent variants exist + legacy AssistantEndEvent still works
- [ ] `loop.py` accepts `done`/`end`/`error` terminals + `toolcall_delta` spelling
- [ ] 3 provider hook events emit in production `_make_stream_fn`
- [ ] `_apply_stream_options_patch` deep-merge replaces shallow merge
- [ ] `AgentHarnessError` Literal = 10 codes; `"auth"` raised in `_make_stream_fn`
- [ ] `DEFERRED_ALLOWLIST` is **empty `{}`** after 3 entries drop
- [ ] `test_phase_4_strict_superset.py` closure pin Green
- [ ] All Sprint 5b tests still Green
- [ ] Pyright clean

---

## §J — Out of Scope

- OpenAI / OpenRouter adapters (6b)
- OAuth token flow + claude.ai login (6c)
- `aelix-rpc` package (ADR-0020 — 6c)
- Secrets storage ADR (6c)
- Anthropic SDK retries override
- Cost ledger telemetry (Phase 5)

---

## §K — Implementation Order

1. §C 12 variants + back-compat aliases + loop consumer update
2. §A Provider framework + register_provider_object/unregister_by_source
3. §B Anthropic adapter (body, transforms, OAuth detection stub)
4. §E Deep-merge fix (P-41)
5. §D 3 emit sites + `_make_stream_fn` closure
6. §F AgentHarnessError widening (`auth` + Literal cleanup)
7. §G tests
8. §H ADR amendments + 0045 + 0046

End of binding spec.
