# 0045. Provider Adapter Interface

Status: Accepted (Sprint 6a / Phase 4.1 shipped)

## Context

Phase 1.4 (ADR-0038) shipped `stream_simple` as a dispatch shell keyed by
`model.api`; provider adapters were deferred to Phase 4. Sprint 6a lands
the first adapter (Anthropic) and codifies the interface contract every
adapter must satisfy.

Pi parity reference: `packages/ai/src/types.ts` (SHA `734e08e`) defines
two related concepts that Aelix unifies into a single Protocol:

- `StreamFunction<TApi, TOptions>` — the raw callable Phase 1.4 shipped.
- `Provider` — an object carrying `api` + the stream function(s) +
  metadata used by `unregisterApiProviders(sourceId)`.

## Decision

**Aelix exposes a single `Provider` Protocol** under
`aelix_ai.providers._base.Provider`:

```python
@runtime_checkable
class Provider(Protocol):
    api: str
    def stream(self, model, context, options) -> AsyncIterator[AssistantMessageEvent]: ...
```

Adapters MAY additionally expose `stream_simple` for callers that want
to short-circuit a higher-level pipeline (Pi parity:
`providers/anthropic.ts` exposes both shapes). The registry dispatcher
prefers `stream_simple` when present.

### Registry API

| Function | Phase | Pi parity |
|---|---|---|
| `register_provider(api, fn)` | Phase 1.4 (kept) | bare-callable shim |
| `register_provider_object(provider, source_id=None)` | **Sprint 6a** | `registerApiProvider` |
| `unregister_provider(api)` | Phase 1.4 | by-api removal |
| `unregister_providers_by_source(source_id)` | **Sprint 6a** | `unregisterApiProviders` |
| `clear_providers()` | Phase 1.4 | `clearApiProviders` |
| `get_registered_providers()` | Phase 1.4 | snapshot |

The Phase 1.4 `register_provider(api, fn)` is preserved as a back-compat
shim that wraps the bare callable in `_BareStreamFnProvider` so the
internal dispatcher always sees a `Provider`-shaped object.

### `SimpleStreamOptions` extensions

Sprint 6a adds Pi-parity option fields:

- `cache_retention`, `transport`, `timeout_ms`, `max_retries`,
  `max_retry_delay_ms`, `reasoning`, `session_id`.
- `on_payload(payload, model) -> payload | None` — Pi `onPayload`.
- `on_response(ProviderResponse, model)` — Pi `onResponse`.
- `client` — pre-built SDK client override (used by tests + by future
  OAuth flows).

New `ProviderResponse` dataclass (Pi `ProviderResponse`):

```python
@dataclass(frozen=True)
class ProviderResponse:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
```

### Sprint 6a adapter — Anthropic

`aelix_ai.providers.anthropic.stream_anthropic` uses the official
`anthropic>=0.40,<1.0` Python SDK behind the thin
`providers/_anthropic_client.py` wrapper. The adapter:

1. Detects Anthropic OAuth bearer tokens (`sk-ant-oat…`) and raises
   `AgentHarnessError("auth", …)` — Sprint 6c lands the OAuth flow.
2. Calls `client.messages.stream(**params)` after the optional
   `on_payload` callback.
3. Surfaces the HTTP response via `on_response(ProviderResponse, model)`.
4. Translates each SDK event into the matching Aelix variant (text /
   thinking / toolcall families) per Pi `providers/anthropic.ts:506-660`.
5. Terminates with `AssistantDoneEvent` on success or
   `AssistantErrorEvent(reason in {"aborted","error"})` on failure.

The adapter registers itself via `register_all()` →
`register_provider_object(ANTHROPIC_PROVIDER, source_id="aelix-ai.builtin")`
so `unregister_providers_by_source("aelix-ai.builtin")` cleanly removes
every Aelix-shipped adapter.

## Consequences

- Third-party adapter packs can author against the stable `Provider`
  Protocol and register via `register_provider_object(...)` — no need
  for a forked dispatcher.
- The bare-callable `register_provider(api, fn)` path stays for
  pre-Sprint-6a code; new adapters SHOULD prefer the Protocol object
  path so `unregister_providers_by_source` works.
- `SimpleStreamOptions` is now closer to Pi's `StreamOptions` shape
  (still missing `temperature` / `maxTokens` — those are not part of
  Pi's `SimpleStreamOptions` either, they live on the per-provider
  payload).

## Alternatives considered

- **Abstract base class instead of Protocol**: rejected — Pi uses
  duck-typed objects with an `api` string; Protocol mirrors that
  without forcing inheritance.
- **Keep only the bare `StreamFn`**: rejected — `unregister_providers_by_source`
  requires per-entry metadata; the Protocol object carries that
  cleanly.
- **Vendor a Python-only HTTP client**: rejected — Pi uses the official
  `@anthropic-ai/sdk` for byte-level parity (cache control, retry, beta
  headers). The official `anthropic` Python SDK matches that surface;
  the `_anthropic_client.py` wrapper keeps the swap path open for
  Sprint 6c's OAuth-driven `httpx` rebuild.

## Related

- ADR-0017 — full hook event catalogue (the 3 provider events emit
  inside `_make_stream_fn`).
- ADR-0034 — Pi reference version pin.
- ADR-0037 — Streaming event union (Accepted in Sprint 6a).
- ADR-0038 — `stream_simple` dispatch shell (body shipped Sprint 6a).
- ADR-0046 — Phase 4 strict superset closure.

## Phase

Sprint 6a / Phase 4.1 (shipped). OpenAI / OpenRouter adapters land
Sprint 6b. OAuth flow + `aelix-rpc` land Sprint 6c.
