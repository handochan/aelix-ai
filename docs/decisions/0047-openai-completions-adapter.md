# 0047. OpenAI Completions Adapter (+ Compat Detection)

Status: Accepted (Sprint 6b / Phase 4.2 shipped)

## Context

Sprint 6a (ADR-0045) shipped the `Provider` Protocol + the first
runtime adapter (Anthropic). Phase 4 closes only when Aelix can carry
real provider traffic for the Pi-known apis — Sprint 6b lands the
**second** of nine: `openai-completions`.

Pi parity reference pin (ADR-0034 amended): `earendil-works/pi @
734e08edf82ff315bc3d96472a6ebfa69a1d8016`. The Pi adapter at
`packages/ai/src/providers/openai-completions.ts` is **1,074 lines**
plus shared utilities; W0 measurement determined the original Sprint 6a
estimate (~450 LOC) was 2.5× too low (see P-47 in the binding spec).

Sprint 6b W6 amendments after code review (ADR-0050 / W6 roster):

- The adapter previously returned `"tool_use"` from `_map_stop_reason`;
  P-57 verifies Pi returns `"toolUse"` (camelCase). The Aelix agent
  loop only compares against `"error" | "aborted"` so the spelling
  change is invisible to terminal-detection — every downstream
  comparison was already neutral.
- `_open_stream` previously called `client.chat.completions.create(params)`
  positionally and read `response.status_code` / `.headers` directly off
  the iterator. The real openai>=1.50 SDK exposes
  `client.chat.completions.with_raw_response.create(**params,
  **request_options)` which returns a wrapper whose `.parse()` is the
  `AsyncStream` and whose `.http_response` is the underlying httpx
  response. C-1 / P-60 fix lands the keyword-arg call and the
  `raw.http_response.status_code` / `.headers` read path.
- The OpenRouter routing block at `build_params` previously read
  `model.compat.openRouterRouting` directly; the merged compat already
  carries the field, and `get_compat`'s `_pick` was extended to accept
  both snake_case and Pi-style camelCase keys on dict overrides
  (M-1 / P-59).
- `_normalize_tool_call_id` clamps to 40 chars for every provider
  (M-6); the OpenAI wire format enforces the limit regardless of
  upstream.
- `stream_simple_openai_completions` is now a **sync factory** that
  raises auth errors eagerly (P-62, Pi parity with `streamSimple`).
- `convert_tools` reads `tool.parameters` only — the Anthropic
  `input_schema` fallback was dropped (P-63 anti-leak).

## Decision

Port `providers/openai-completions.ts` + 6 shared helpers + 1 main
adapter into `packages/aelix-ai/src/aelix_ai/providers/`:

| File | Pi parity source |
|---|---|
| `openai_completions.py` | `providers/openai-completions.ts` |
| `_openai_compat.py` | `detectCompat` / `getCompat` / 17-field compat dataclass |
| `_openai_client.py` | `createClient` — thin `openai>=1.50` SDK wrapper |
| `_transform_messages.py` | `providers/transform-messages.ts` (shared infra; see ADR-0048) |
| `_env_api_keys.py` | `env-api-keys.ts` |
| `_sanitize_unicode.py` | `utils/sanitize-unicode.ts` |
| `_streaming_json.py` | `utils/json-parse.ts` |

`stream_openai_completions(model, context, options)` is the canonical
entry point; `stream_simple_openai_completions` is the Pi-shape sync
factory that resolves the env API key + clamps the reasoning level
eagerly, then returns the async generator.

Adapter registration follows the Sprint 6a precedent:
`register_all()` → `register_provider_object(OPENAI_COMPLETIONS_PROVIDER,
source_id="aelix-ai.builtin")` so
`unregister_providers_by_source("aelix-ai.builtin")` cleanly removes
every Aelix-shipped adapter.

### OpenRouter is NOT a separate adapter (P-48)

Pi has **9 KnownApi values** at this pin; OpenRouter is not one of
them. OpenRouter traffic flows through `openai-completions` via
`provider="openrouter"` + `base_url="https://openrouter.ai/api/v1"`
+ auto-detected `thinking_format == "openrouter"` (compat) +
`OPENROUTER_API_KEY` env var. Sprint 6b ships **one** adapter
registration; the OpenRouter compat detection lives inside
`_detect_compat`.

### Streaming event order (Pi parity, P-53)

```
start (partial=empty)
text_start / thinking_start / toolcall_start  (lazy, on first delta)
text_delta / thinking_delta / toolcall_delta  (per chunk)
text_end / thinking_end / toolcall_end        (per block at end-of-stream)
done (reason in {"stop"|"length"|"toolUse"}, message=full output)
```

On exception in the stream body: `error` is emitted with
`reason in {"aborted"|"error"}` and NO `done` event follows.

### `mapStopReason` (Pi parity, P-57)

```python
def _map_stop_reason(reason: str | None) -> tuple[str, str | None]:
    if reason is None or reason == "null":
        return ("stop", None)
    if reason in ("stop", "end"):
        return ("stop", None)
    if reason == "length":
        return ("length", None)
    if reason in ("function_call", "tool_calls"):
        return ("toolUse", None)   # Pi camelCase
    if reason == "content_filter":
        return ("error", "Provider finish_reason: content_filter")
    if reason == "network_error":
        return ("error", "Provider finish_reason: network_error")
    return ("error", f"Provider finish_reason: {reason}")
```

## Consequences

- Aelix is now 2 of 9 Pi `KnownApi` adapters live. ADR-0034 amendment
  carries the cardinality note.
- OpenRouter "support" lands for free via baseUrl detection — no second
  registration call honors Pi parity strictly.
- The Anthropic adapter shipped in Sprint 6a is **not** retrofit onto
  `_transform_messages.py` in this PR; the deferred work is captured as
  P-50-followup under ADR-0048 / ADR-0050 §Carry-forward.
- 7 deferred `KnownApi` adapters (openai-responses,
  openai-codex-responses, azure-openai-responses, mistral-conversations,
  google-generative-ai, google-vertex, bedrock-converse-stream) live in
  `PHASE_4_2_DEFERRED_APIS` with owning ADR-0050.
- 4 deferred compat targets (cloudflare-workers-ai,
  cloudflare-ai-gateway, github-copilot, vercel-ai-gateway) live in
  `COMPAT_DEFERRED_ALLOWLIST` plus 2 thinking-format detection paths
  (qwen / qwen-chat-template) — owning ADR-0050 §J.
- `openai>=1.50,<2.0` becomes a direct dependency of `aelix-ai`.

## Alternatives considered

- **Vendor an HTTP client instead of the official SDK**: rejected — Pi
  uses `@openai/openai` for byte-level parity (retry, headers, beta
  features). The Python SDK matches the surface.
- **Lazy-load adapters via Pi `register-builtins.ts`**: rejected —
  Aelix uses eager `register_all()` per Sprint 6a precedent. Pi's lazy
  mechanism is owned by `aelix-ai.builtin` source_id removal and is
  not in scope.
- **Build a thin facade over the Pi TypeScript adapter**: rejected — Pi
  parity binding requires a native Python port; the Sprint 6b investment
  is the durable solution.

## Related

- ADR-0034 — Pi reference version pin (amended to add 2-of-9 adapter
  note).
- ADR-0037 — Streaming event union (the 12-variant union the adapter
  emits).
- ADR-0045 — Provider Adapter Interface (the Protocol this adapter
  satisfies).
- ADR-0048 — Pi shared utilities (`_transform_messages.py` +
  `_sanitize_unicode.py` + `_streaming_json.py` + `_env_api_keys.py`).
- ADR-0049 — Message dataclass extensions (additive `ThinkingContent`
  / `AssistantMessage.api|provider|model` / `ImageContent.mime_type|data`
  / `ToolResultMessage.tool_name`).
- ADR-0050 — Phase 4.2 strict superset closure.

## Phase

Sprint 6b / Phase 4.2 (shipped — 2 of 9 KnownApi adapters live; 7
deferred under `PHASE_4_2_DEFERRED_APIS`).
