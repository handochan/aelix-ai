# Sprint 6b · Phase 4.2 — OpenAI Completions Adapter + OpenRouter Routing (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-18
Pi pin (ADR-0034 amended): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint adds the **second** runtime adapter (`openai-completions`) to the Provider framework shipped in Sprint 6a (ADR-0045), bringing Aelix to **2 of 9** Pi `KnownApi` adapters with full streaming-event parity.

---

## §0 — W0 INVESTIGATION FINDINGS (Pi drift verified at SHA 734e08e)

### P-47 — Sprint 6a sub-sprint LOC estimate was off

Sprint 6a §0 sub-sprint split projected:
> | **6b** | OpenAI + OpenRouter adapters | ~450 prod + ~400 test |

W0 measurement against Pi:
- `providers/openai-completions.ts` = **1,074 lines** (40 KB)
- `providers/transform-messages.ts` = **218 lines** (shared, needed)
- `providers/simple-options.ts` = **51 lines** (shared, partially needed)
- `env-api-keys.ts` = **210 lines** (needed for `getEnvApiKey`)
- `providers/cloudflare.ts` = **~50 lines** (compat dep, optional)
- `providers/github-copilot-headers.ts` = **~40 lines** (compat dep, optional)

Realistic Python port = **~1,300 prod LOC + ~700 test LOC**. **The 450/400 estimate was 2.5× too low.**

**Resolution:** Sprint 6b ships full Pi parity for `openai-completions` core + transforms + env. Compat zoo (cloudflare, copilot, vercel-gateway, opencode, zai, moonshot) lands in follow-ups per §J forward-compat clause.

### P-48 — OpenRouter is NOT a separate KnownApi

W0 confirmed: Pi has **9 KnownApi values** (`types.ts:7-16`); OpenRouter is **not one of them**. OpenRouter is served via:
- `api: "openai-completions"` (the adapter)
- `provider: "openrouter"` (model metadata)
- `baseUrl: "https://openrouter.ai/api/v1"` (Model config)
- Auto-detected `thinkingFormat: "openrouter"` (compat)
- `OPENROUTER_API_KEY` env var

**Decision:** Sprint 6b ships ONE adapter (`openai_completions`). OpenRouter "support" = compat auto-detection wired inside the adapter's `_detect_compat`. **No second registration call.** This honors Pi parity strictly.

### P-49 — Pi KnownApi cardinality drift in ADR-0034

ADR-0034 implicitly framed Aelix as Pi-parity per-API. Sprint 6a registered 1 of 9. Sprint 6b registers 1 more. The remaining 7 (`openai-responses`, `azure-openai-responses`, `openai-codex-responses`, `mistral-conversations`, `google-generative-ai`, `google-vertex`, `bedrock-converse-stream`) are not in scope; **ADR-0034 amendment** in §I lists explicit deferred adapter map.

### P-50 — `transformMessages` is cross-provider shared infrastructure

Pi `providers/transform-messages.ts` is shared between **anthropic, openai-completions, openai-responses, google, etc.** — handles:
- Cross-model text-conversion of `thinking` blocks (drops same-model encrypted thinking when crossing models)
- `tool_call_id` normalization (provider-agnostic)
- Orphan tool-call synthesis (insert synthetic `"No result provided"` tool results before user messages)
- Non-vision image downgrade (replace `ImageContent` with placeholder text)

Sprint 6a's `_anthropic_transforms.py::transform_messages` does **none of this** — it just converts Aelix `Message` → Anthropic SDK shape. The Pi `transformMessages` runs BEFORE the per-adapter shape transform.

**Decision:** Sprint 6b ports Pi `transformMessages` into a NEW shared module `providers/_transform_messages.py` and routes the OpenAI adapter through it. **Sprint 6a's anthropic adapter is NOT retrofit in 6b** — that's deferred to Sprint 6d (cross-adapter hygiene). The deferred work is captured as **P-50-followup**.

### P-51 — `mapStopReason` divergence between Pi providers

Anthropic `stop_reason` values: `end_turn | stop_sequence | max_tokens | tool_use | pause_turn | refusal`.
OpenAI `finish_reason` values: `stop | end | length | function_call | tool_calls | content_filter | network_error | null`.

Pi's per-adapter `mapStopReason` translates both to the same Aelix `StopReason` taxonomy (`"stop" | "length" | "tool_use" | "end_turn" | "error" | "aborted"`). Sprint 6a's anthropic adapter ships its own; Sprint 6b ships OpenAI's. **No shared helper.**

### P-52 — `convertMessages` mutates `params` cache_control side-effect-style

Pi `convertMessages` returns a `ChatCompletionMessageParam[]`. Then `applyAnthropicCacheControl(messages, params.tools, cacheControl)` **mutates** the returned list (and the tools list) in-place to inject `cache_control: {type:'ephemeral'}` blocks on the last text part of system/user/assistant messages and the last tool.

Python port: **return-by-value mutation is acceptable** for parity. Test it.

### P-53 — Streaming SSE iteration order

Pi `streamOpenAICompletions` emits events in this order:
1. `start` (with empty `output`)
2. **Lazy block_start** — `text_start | thinking_start | toolcall_start` (first time that block type appears in deltas)
3. **Per-chunk deltas** — `text_delta | thinking_delta | toolcall_delta` (multiple)
4. **End-of-stream**: iterate `blocks` array, call `finishBlock` for each → emit `text_end | thinking_end | toolcall_end`
5. `done` (with full `output`)

On error: emit `error` event (no `done`).

This matches Pi's anthropic adapter event order. **Critical:** `toolcall_end` carries the parsed `arguments` dict (incremental `partialArgs` discarded).

### P-54 — `parseStreamingJson` is a Pi utility (`utils/json-parse.ts`)

Used by Pi to lenient-parse incomplete streamed `tool_call.function.arguments` strings into dicts as deltas arrive. Pi behavior: returns partial dict for incomplete JSON, full dict when complete.

Python port: `_streaming_json.py` helper, ~30 lines. Lenient mode = catch `JSONDecodeError` and try to close braces/brackets heuristically.

### P-55 — `sanitizeSurrogates` is a Pi utility (`utils/sanitize-unicode.ts`)

Used by Pi to strip lone Unicode surrogate code points from user content before sending to the OpenAI API (which rejects them). Sprint 6b port: `_sanitize_unicode.py`, ~10 lines.

### P-56 — `headersToRecord` is a Pi utility (`utils/headers.ts`)

Pi converts a Fetch `Headers` object into a `Record<string,string>` for the `onResponse` callback. Python equivalent: `dict(httpx_response.headers)` — built-in. **No port needed.**

---

## §A — Scope (binding)

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `_env_api_keys.py` (port `env-api-keys.ts`) | ~120 | ~80 |
| `_transform_messages.py` (port `transform-messages.ts`) | ~180 | ~120 |
| `_sanitize_unicode.py` | ~10 | ~20 |
| `_streaming_json.py` | ~40 | ~60 |
| `_openai_client.py` (thin SDK wrapper) | ~40 | ~30 |
| `_openai_compat.py` (compat detection + helpers) | ~180 | ~140 |
| `openai_completions.py` (main adapter) | ~600 | ~250 |
| Pi parity closure test pin | — | ~80 |
| **Totals** | **~1,170** | **~780** |

**Total ~1,950 LOC** = ~2.5× Sprint 6a estimate but aligned with Pi reality.

### NOT in scope (deferred)
- `openai-responses` adapter (Pi 30 KB, separate sprint)
- `openai-codex-responses` adapter (Pi 43 KB, separate sprint)
- `azure-openai-responses` (Pi 9 KB, separate sprint)
- `mistral-conversations` (Pi 20 KB, separate sprint)
- `google-*` adapters (Pi 40 KB, separate sprint)
- `bedrock-converse-stream` (Pi 32 KB, separate sprint)
- OAuth flows (Sprint 6c — ADR-0020)
- `cloudflare-ai-gateway` compat (deferred to 6d compat zoo)
- `github-copilot` compat (deferred to 6d compat zoo)
- `vercel-ai-gateway` compat (deferred to 6d compat zoo)
- Retrofit Sprint 6a Anthropic adapter onto `_transform_messages.py` (deferred to 6d cross-adapter hygiene)

---

## §B — `providers/_env_api_keys.py` (NEW)

Port Pi `packages/ai/src/env-api-keys.ts` verbatim. Python signature:

```python
ENV_API_KEYS: dict[str, list[str]] = {
    "github-copilot": ["COPILOT_GITHUB_TOKEN"],
    "anthropic": ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "azure-openai-responses": ["AZURE_OPENAI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "google": ["GEMINI_API_KEY"],
    "google-vertex": ["GOOGLE_CLOUD_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "cerebras": ["CEREBRAS_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "vercel-ai-gateway": ["AI_GATEWAY_API_KEY"],
    "zai": ["ZAI_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "moonshotai": ["MOONSHOT_API_KEY"],
    "moonshotai-cn": ["MOONSHOT_API_KEY"],
    "huggingface": ["HF_TOKEN"],
    "fireworks": ["FIREWORKS_API_KEY"],
    "together": ["TOGETHER_API_KEY"],
    "opencode": ["OPENCODE_API_KEY"],
    "opencode-go": ["OPENCODE_API_KEY"],
    "kimi-coding": ["KIMI_API_KEY"],
    "cloudflare-workers-ai": ["CLOUDFLARE_API_KEY"],
    "cloudflare-ai-gateway": ["CLOUDFLARE_API_KEY"],
    "minimax": ["MINIMAX_API_KEY"],
    "minimax-cn": ["MINIMAX_CN_API_KEY"],
    "xiaomi": ["XIAOMI_API_KEY"],
    "xiaomi-token-plan-cn": ["XIAOMI_TOKEN_PLAN_CN_API_KEY"],
    "xiaomi-token-plan-ams": ["XIAOMI_TOKEN_PLAN_AMS_API_KEY"],
    "xiaomi-token-plan-sgp": ["XIAOMI_TOKEN_PLAN_SGP_API_KEY"],
}

def find_env_keys(provider: str) -> list[str] | None: ...
def get_env_api_key(provider: str) -> str | None: ...
```

`google-vertex` ADC + `amazon-bedrock` ambient-credential branches are **out of scope** (they belong to those adapters; Sprint 6b lands the dict + 2 lookup helpers only).

---

## §C — `providers/_transform_messages.py` (NEW)

Port Pi `transform-messages.ts` (218 lines). Function signature:

```python
def transform_messages(
    messages: list[Message],
    model: Model,
    *,
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None = None,
) -> list[Message]:
    """Pi parity: providers/transform-messages.ts."""
```

Behaviors (Pi parity):
1. **`_downgrade_unsupported_images`** — when `"image" not in model.input`, replace ImageContent with placeholder text in user + tool_result messages.
2. **Same-model detection** — `same_model = msg.provider == model.provider AND msg.api == model.api AND msg.model == model.id`.
3. **Thinking block transform**:
   - `redacted` thinking: keep if same-model, drop if cross-model.
   - Same-model with `thinking_signature`: keep block intact (even if `thinking` empty).
   - Empty thinking: drop.
   - Same-model: keep block.
   - Cross-model: convert to plain `TextContent`.
4. **Tool call ID normalization** — when `normalize_tool_call_id` provided and not same-model, remap `ToolCallContent.tool_call_id`, build `tool_call_id_map` and rewrite subsequent `ToolResultMessage.tool_call_id` accordingly. Drop `thought_signature` when cross-model.
5. **Orphan tool call synthesis** — second pass: scan messages, track unresolved tool calls per assistant turn, insert synthetic `ToolResultMessage(content=[TextContent("No result provided")], is_error=True)` before user messages (or end of conversation).
6. **Skip errored/aborted assistant messages** — `stop_reason in {"error", "aborted"}` → drop entire turn.

---

## §D — `providers/_sanitize_unicode.py` + `_streaming_json.py` (NEW)

`_sanitize_unicode.py`:
```python
def sanitize_surrogates(text: str) -> str:
    """Strip lone Unicode surrogate code points (U+D800..U+DFFF).
    Pi parity: utils/sanitize-unicode.ts.
    """
    return "".join(c for c in text if not (0xD800 <= ord(c) <= 0xDFFF))
```

`_streaming_json.py`:
```python
def parse_streaming_json(partial: str | None) -> dict[str, Any]:
    """Lenient incremental JSON parse.

    Pi parity: utils/json-parse.ts. Returns {} for None/empty.
    Tries strict json.loads first; on failure attempts to close
    open braces/brackets and re-parse. Returns last successfully
    parsed dict or {} if no recovery possible.
    """
```

Tests cover: empty, partial `{`, partial `{"a"`, partial `{"a": "b`, partial `{"a": [1, 2`, full valid.

---

## §E — `providers/_openai_client.py` (NEW)

Thin SDK wrapper, mirrors `_anthropic_client.py`:

```python
from openai import AsyncOpenAI

def create_async_client(
    *,
    api_key: str,
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> AsyncOpenAI:
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncOpenAI(**kwargs)
```

**Dependency add:** `openai>=1.50,<2.0` in `packages/aelix-ai/pyproject.toml`. (Already a transitive dep but pin it directly.)

---

## §F — `providers/_openai_compat.py` (NEW)

Port Pi `detectCompat` + `getCompat` + `ResolvedOpenAICompletionsCompat` shape.

```python
@dataclass(frozen=True)
class OpenAICompletionsCompat:
    supports_store: bool = True
    supports_developer_role: bool = True
    supports_reasoning_effort: bool = True
    supports_usage_in_streaming: bool = True
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_completion_tokens"
    requires_tool_result_name: bool = False
    requires_assistant_after_tool_result: bool = False
    requires_thinking_as_text: bool = False
    requires_reasoning_content_on_assistant_messages: bool = False
    thinking_format: Literal["openai", "openrouter", "together", "deepseek", "zai", "qwen", "qwen-chat-template"] = "openai"
    open_router_routing: dict[str, Any] = field(default_factory=dict)
    vercel_gateway_routing: dict[str, Any] = field(default_factory=dict)
    zai_tool_stream: bool = False
    supports_strict_mode: bool = True
    cache_control_format: Literal["anthropic"] | None = None
    send_session_affinity_headers: bool = False
    supports_long_cache_retention: bool = True
```

`detect_compat(model)` and `get_compat(model)` mirror Pi line-by-line. URL substrings checked verbatim from §0 P-47 fixture.

**Sprint 6b scope — full compat detection ships for:**
- openai (default)
- openrouter
- groq (no special compat, just env var)
- deepseek
- xai (Grok)
- zai
- together
- moonshot / moonshot-cn
- cerebras
- chutes (URL-detected)
- opencode

**Deferred to Sprint 6d (compat zoo wrap-up):**
- cloudflare-workers-ai (needs `_cloudflare.py` helpers)
- cloudflare-ai-gateway (needs `cf-aig-authorization` header rewrite)
- github-copilot (needs `_copilot_headers.py` with dynamic vision-input headers)
- vercel-ai-gateway (needs `providerOptions.gateway` injection)

---

## §G — `providers/openai_completions.py` (NEW — main adapter)

File layout (mirrors Pi `openai-completions.ts:110-397`):

```python
"""OpenAI Chat Completions adapter — Sprint 6b (ADR-0045 §F + ADR-0047).

Pi parity: providers/openai-completions.ts:1-1074 (SHA 734e08e).
"""

OPENAI_COMPLETIONS_API: str = "openai-completions"
BUILTIN_SOURCE_ID: str = "aelix-ai.builtin"


@dataclass(frozen=True)
class OpenAICompletionsOptions(SimpleStreamOptions):
    """Pi parity: OpenAICompletionsOptions (openai-completions.ts:76-79)."""
    tool_choice: ToolChoice | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None


async def stream_openai_completions(
    model: Model,
    context: Context,
    options: OpenAICompletionsOptions | SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: streamOpenAICompletions (openai-completions.ts:110-397)."""
    ...


async def stream_simple_openai_completions(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: streamSimpleOpenAICompletions (openai-completions.ts:399-418)."""
    ...


OPENAI_COMPLETIONS_PROVIDER = _OpenAICompletionsProvider()


def register_all() -> None:
    """Register the OpenAI Completions adapter with the global registry.

    Idempotent — safe to call multiple times. The Anthropic adapter
    registered in Sprint 6a is NOT touched.
    """
    register_provider_object(OPENAI_COMPLETIONS_PROVIDER, source_id=BUILTIN_SOURCE_ID)
```

### G.1 Streaming event mapping (Pi parity, P-53)

| Pi SDK event | Aelix `AssistantMessageEvent` |
|---|---|
| `start` (synthesized) | `AssistantStartEvent(partial=empty AssistantMessage)` |
| `choice.delta.content` (first time) | `TextStartEvent(content_index=N, partial=output)` |
| `choice.delta.content` (subsequent) | `TextDeltaEvent(content_index=N, delta=str, partial=output)` |
| `choice.delta.reasoning_content` (first time) | `ThinkingStartEvent(content_index=N, partial=output)` |
| `choice.delta.reasoning_content` (subsequent) | `ThinkingDeltaEvent(content_index=N, delta=str, partial=output)` |
| `choice.delta.tool_calls[i]` (first time per i) | `ToolCallStartEvent(content_index=N, partial=output)` |
| `choice.delta.tool_calls[i].function.arguments` | `ToolCallDeltaEvent(content_index=N, delta=str, partial=output)` |
| End of stream — per block | `TextEndEvent` / `ThinkingEndEvent` / `ToolCallEndEvent` |
| `choice.finish_reason` mapped | `AssistantDoneEvent(reason, message)` |
| Exception in body | `AssistantErrorEvent(reason="aborted"|"error", error=output)` |

### G.2 `_make_stream_fn` integration (Sprint 6a contract)

The 3 emit sites already live in `harness/core.py::_make_stream_fn`:
- `before_provider_request` — fires before the OpenAI SDK call
- `before_provider_payload` — receives mutated `params` from the adapter's `options.on_payload(params, model)` callback
- `after_provider_response` — receives `ProviderResponse(status, headers)` from `options.on_response(...)` callback

The Sprint 6b adapter MUST call these callbacks at the same lifecycle points as Pi (between `buildParams` and `client.chat.completions.create`, then after `await client.chat.completions.create(...).withResponse()`). See Pi `:143-155`.

### G.3 `mapStopReason` (Pi parity)

```python
def _map_stop_reason(reason: str | None) -> tuple[str, str | None]:
    """Pi parity: mapStopReason (openai-completions.ts:955-979)."""
    if reason is None:
        return ("stop", None)
    if reason in ("stop", "end"):
        return ("stop", None)
    if reason == "length":
        return ("length", None)
    if reason in ("function_call", "tool_calls"):
        return ("toolUse", None)
    if reason == "content_filter":
        return ("error", "Provider finish_reason: content_filter")
    if reason == "network_error":
        return ("error", "Provider finish_reason: network_error")
    return ("error", f"Provider finish_reason: {reason}")
```

### G.4 Error handling (Pi parity, openai-completions.ts:380-393)

On exception in stream body:
1. Strip scratch buffer fields from all blocks (`index`, `partial_args`, `stream_index`).
2. Set `output.stop_reason = "aborted" if signal_aborted else "error"`.
3. Set `output.error_message = str(error)`. If error has `error.metadata.raw` (OpenRouter shape), append it.
4. Emit `AssistantErrorEvent(reason=output.stop_reason, error=output)`.
5. End stream — NO `AssistantDoneEvent`.

---

## §H — Tests (binding plan, ~780 LOC)

### Unit
- `tests/providers/test_env_api_keys.py` — every entry in the 30-row provider→envvar table; `os.environ` patching.
- `tests/providers/test_transform_messages.py` — Pi-parity cases:
  - Cross-model thinking text-conversion
  - Same-model thinking signature preservation
  - Redacted thinking same/cross-model split
  - Image downgrade for non-vision models
  - Tool call ID normalization with map propagation to subsequent tool results
  - Orphan tool call synthesis before user message + at end of conversation
  - Errored/aborted assistant message skipped entirely
- `tests/providers/test_sanitize_unicode.py` — lone surrogates stripped; valid BMP + non-BMP kept.
- `tests/providers/test_streaming_json.py` — incremental JSON: `""`, `"{"`, `"{"a"`, `"{"a":"b"`, `"{"a":"b"}"`, malformed.
- `tests/providers/test_openai_compat.py` — every detected provider/URL → expected compat field overrides.
- `tests/providers/test_openai_completions_unit.py` — `_map_stop_reason` all cases; `build_params` for openai/openrouter/deepseek thinking formats; cache_control injection for `anthropic/*` openrouter models; `convert_tools` with strict mode toggle.

### Integration (with fake SDK)
- `tests/providers/test_openai_completions_streaming.py` — fake `AsyncOpenAI.chat.completions.create` returning canned SSE chunks:
  - Text-only response → `start, text_start, text_delta+, text_end, done`
  - Tool-call response → `start, toolcall_start, toolcall_delta+, toolcall_end, done`
  - Mixed text + tool calls → all events in correct order
  - Reasoning content → `thinking_start, thinking_delta+, thinking_end`
  - Error mid-stream → `error` (no `done`)
  - Aborted signal → `error` with `reason="aborted"`
- `tests/providers/test_openai_completions_emits.py` — verify the 3 provider hook events fire via `_make_stream_fn` (calls into harness):
  - `before_provider_request` payload sane
  - `before_provider_payload` mutation honored (modify `temperature`)
  - `after_provider_response` carries `(status, headers)`
- `tests/providers/test_openai_completions_openrouter.py` — model with `provider="openrouter"`, `base_url="https://openrouter.ai/api/v1"` → compat detection: `thinking_format == "openrouter"`, `OPENROUTER_API_KEY` env var picked up, params include `reasoning: {effort: ...}` when `reasoning_effort` set.

### Pi parity closure pin
- `tests/pi_parity/test_phase_4_2_strict_superset.py` — assert:
  - 2 of 9 Pi `KnownApi` values now registered (`anthropic-messages` + `openai-completions`)
  - The 7 other apis sit in `PHASE_4_2_DEFERRED_APIS` with owning ADR (this spec)
  - `_env_api_keys.ENV_API_KEYS` keyset ⊇ Pi env-keys mapping at SHA 734e08e
  - `_openai_compat.OpenAICompletionsCompat` field set ⊇ Pi `ResolvedOpenAICompletionsCompat` (17 fields)

---

## §I — ADRs

### ADR-0034 amendment (NEW row)
Add row: "Sprint 6b registered `openai-completions`. 7 apis remain deferred per ADR-0047 §J."

### ADR-0045 amendment (status update)
Add §F.2 note: "Sprint 6b shipped OpenAI Completions adapter; Anthropic adapter NOT retrofit onto `_transform_messages.py` (deferred to Sprint 6d cross-adapter hygiene per P-50-followup)."

### ADR-0047 — NEW: OpenAI Completions adapter + compat detection
File: `docs/decisions/0047-openai-completions-adapter.md`
Sections: Context (Phase 4 emit catalogue closed in 6a, 6b ships 2nd adapter); Decision (port `openai-completions.ts` + transforms + env + sanitize + streaming_json); Consequences (OpenRouter "for free" via baseUrl detection; 7 apis deferred; LOC drift owned).

### ADR-0048 — NEW: Pi shared utilities ported (`_transform_messages.py`, `_sanitize_unicode.py`, `_streaming_json.py`, `_env_api_keys.py`)
File: `docs/decisions/0048-pi-shared-utilities.md`
Captures the shared-utility boundary and the deferred retrofit of Anthropic onto `_transform_messages.py`.

### ADR-0049 — NEW: Phase 4.2 strict superset closure
File: `docs/decisions/0049-phase-4-2-strict-superset-closure.md`
Mirror ADR-0046 pattern. Roster: P-47 ~ P-56. Closure invariant: 2 of 9 apis live + 7 apis owned by `PHASE_4_2_DEFERRED_APIS` allowlist + 4 compat targets in `COMPAT_DEFERRED_ALLOWLIST` (cloudflare, copilot, vercel, opencode).

---

## §J — Forward-compat clause (binding)

After Sprint 6b:
- The 7 deferred `KnownApi` adapters (`openai-responses`, `azure-openai-responses`, `openai-codex-responses`, `mistral-conversations`, `google-generative-ai`, `google-vertex`, `bedrock-converse-stream`) MUST be tracked in `PHASE_4_2_DEFERRED_APIS = {...}` with owning ADR-0049.
- The 4 deferred compat targets (`cloudflare-workers-ai`, `cloudflare-ai-gateway`, `github-copilot`, `vercel-ai-gateway`) MUST be tracked in `COMPAT_DEFERRED_ALLOWLIST = {...}` with owning ADR-0049.
- Any future PR that lands an adapter MUST drop it from `PHASE_4_2_DEFERRED_APIS` in the same PR (enforced by closure pin).
- Pi `register-builtins.ts` lazy-loading mechanism is **explicitly out of scope** — Aelix uses eager `register_all()` per Sprint 6a precedent.

---

## §K — Sprint workflow (ADR-0032)

- W0 — research (this section's findings)
- W1 — this spec (binding)
- W2 — executor opus implements §B~§G
- W3 — verification (pytest + ruff + pyright spike — preserve 8-error baseline)
- W4 — code-reviewer opus (parallel with W5)
- W5 — architect opus Pi parity audit (parallel with W4)
- W6 — apply must-fixes + atomic commits + ADRs accepted

**Atomic commit plan (W6):**
1. `feat: providers — _env_api_keys + _sanitize_unicode + _streaming_json port`
2. `feat: providers — _transform_messages port (Pi shared infra)`
3. `feat: providers — OpenAI Completions adapter + _openai_compat + _openai_client (ADR-0047)`
4. `test: Sprint 6b — N new tests + 1 Pi-parity fixture + Phase 4.2 closure pin`
5. `docs: ADRs 0034 amend + 0045 amend + NEW ADR-0047/0048/0049 + README + spec`

---

## §L — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 644 + ~80 new = 720+ pass; 0 fail |
| ruff check | clean |
| pyright spike | 8 errors (baseline preserved) |
| Pi parity closure | `PHASE_4_2_DEFERRED_APIS` populated; 2 of 9 apis live |
| Atomic commit count | exactly 5 |

---

**End of binding spec. Architect READ-ONLY until W6.**
