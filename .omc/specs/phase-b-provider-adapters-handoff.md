# Phase B — Implement the 7 Un-Adapted Provider APIs ("pi 완전 구현")

> Self-contained handoff for a fresh Claude Code session with zero prior context. You will implement, in a dedicated git worktree, runtime adapters for the 7 provider APIs that currently have NO adapter — so their ~288 catalog models stream instead of raising `StreamSimpleError("no_provider_registered")`. Goal: match pi (the TS reference) behavior. Runs IN PARALLEL with Phase A (which edits `aelix-coding-agent` entry/mcp/core — fully disjoint from your files).

All facts below were verified against live Aelix source at the cited `file:line` and against the openai Python SDK 1.109.1 installed in this repo. Pi reference is pinned at `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

---

## 1. Mission & scope

### What exists today
The repo registers exactly TWO runtime adapters:
- `anthropic-messages` → `packages/aelix-ai/src/aelix_ai/providers/anthropic.py` (`register_all`, ANTHROPIC_API line 70)
- `openai-completions` → `packages/aelix-ai/src/aelix_ai/providers/openai_completions.py` (`register_all` line 1360, OPENAI_COMPLETIONS_API line 77)

Both are wired in `packages/aelix-coding-agent/src/aelix_coding_agent/cli/runtime_bootstrap.py` `register_providers()` (lines 59-63):
```python
def register_providers() -> None:
    _openai.register_all()
    _anthropic.register_all()
```

### What you implement (the 7 APIs, EXACT catalog counts — verified from `models_generated.json`)
| Priority | api id | models | provider(s) | new SDK dep? |
|---|---|---|---|---|
| 1 | `openai-responses` | **81** | openai(42), opencode(16), cloudflare-ai-gateway(16), github-copilot(7) | reuse installed `openai` |
| 2 | `azure-openai-responses` | **42** | azure-openai-responses(42) | reuse `openai` (AsyncAzureOpenAI) |
| 3 | `mistral-conversations` | **28** | mistral(28) | reuse `openai` against base_url (recommended) |
| 4 | `google-generative-ai` | **30** | google(28), opencode(2) | **add `google-genai`** |
| 5 | `google-vertex` | **13** | google-vertex(13) | `google-genai` (vertexai=True) |
| 6 | `openai-codex-responses` | **10** | openai-codex(10) | raw `httpx` SSE (already installed) |
| 7 | `bedrock-converse-stream` | **84** | amazon-bedrock(84) | **add `boto3`** (or `aioboto3`) |

**Total = exactly 288 models.** (Confirmed: `81+42+10+30+13+84+28=288`.)

### Honest completion bar — "code-complete" vs "live-verified"
- **Code-complete** = adapter + unit tests pass; the catalog model dispatches to your adapter instead of raising `no_provider_registered`. The pure-transform layers (message/tool conversion, stream-event mapping, build_params, cost calc, thinking-level clamp) are FULLY unit-testable offline with faked SDK clients/canned events — do this for every API.
- **Live-verified** = a real network round-trip with real creds produced text + a tool call (+ thinking where supported) and populated usage/cost.

**RULE: Do NOT claim "done"/"완벽"/"pi 완전 구현" on a provider until it is live-verified. For any API you cannot live-verify (no creds), mark it explicitly `LIVE-PENDING` in your final report and in the ADR — code-complete is a real, citable milestone, but it is not "done".** This mirrors the ADR-0114 lesson where `**kwargs` test fakes masked a real SDK-rejection bug; offline tests alone are insufficient proof.

---

## 2. Isolation & setup

### Create your worktree (branch from current `main`, NOT from Phase A)
```bash
cd /workspaces/aelix-ai
git fetch origin
git worktree add ../aelix-phaseB -b phaseB-providers   # branches from current HEAD (main)
cd ../aelix-phaseB
# install deps into the worktree venv (the repo uses uv workspace; pyproject testpaths=["tests"], asyncio_mode="auto")
uv sync   # or: python -m venv .venv && .venv/bin/pip install -e packages/aelix-ai ...
```

### The disjoint file set — Phase B touches ONLY `packages/aelix-ai/`
You will create/edit ONLY:
- `packages/aelix-ai/src/aelix_ai/providers/` — new adapter modules + helpers
- `packages/aelix-ai/src/aelix_ai/providers/_env_api_keys.py` — add the deferred Vertex-ADC + Bedrock branches
- `packages/aelix-ai/pyproject.toml` — add `google-genai` + `boto3` deps
- `tests/providers/` — new unit tests
- `docs/adr/` — a new ADR (next free number; latest is ADR-0132)

### The ONE integration touchpoint to watch (shared with Phase A)
`packages/aelix-coding-agent/src/aelix_coding_agent/cli/runtime_bootstrap.py` `register_providers()` (lines 59-63). This file is in `aelix-coding-agent` (Phase A's package). You MUST add your `register_all()` calls here, but it is a 2-line insertion in a function Phase A is unlikely to touch. **Strategy: make this edit LAST, in a single tiny commit, so the merge is trivial.** If Phase A also edited this file, the conflict is a hand-mergeable adjacent-line addition.

There is also an optional codex auth bridge (see §4.6) that may touch the coding-agent model resolver / stream-fn wiring — coordinate or defer codex if that risks a conflict.

### Merge + combined gate at the end
```bash
# from the phaseB worktree, after both phases land
cd /workspaces/aelix-ai            # main checkout
git checkout main && git pull
git merge phaseA-<branch>          # Phase A first (or whichever lands first)
git merge phaseB-providers
# resolve runtime_bootstrap.py register_providers() if both added lines (keep all register_all() calls)
cd /workspaces/aelix-ai && python -m pytest tests/ -q   # combined gate
git worktree remove ../aelix-phaseB
```

---

## 3. Aelix adapter architecture

### 3.1 The contract — `Provider` Protocol
`packages/aelix-ai/src/aelix_ai/providers/_base.py:31-51` (verified):
```python
@runtime_checkable
class Provider(Protocol):
    api: str   # the ONLY required attr besides stream; it is the registry key
    def stream(self, model: Model, context: Context,
               options: SimpleStreamOptions) -> AsyncIterator[AssistantMessageEvent]: ...
    # OPTIONAL: stream_simple(self, model, context, options) -> AsyncIterator[...]
```
- `stream`/`stream_simple` are **plain methods that RETURN an AsyncIterator** (they call a module-level `async def` generator). They are NOT themselves `async def`.
- `source_id: str | None = None` is conventional (both concrete adapters declare it). `register_provider_object` sets it opportunistically.
- The dispatcher prefers `stream_simple` when present, else `stream` (`api_registry.py` `_resolve_provider`).

### 3.2 Registry + the EXACT raise site to route away from
`packages/aelix-ai/src/aelix_ai/api_registry.py` (verified):
- Module dict `_PROVIDERS: dict[str, Provider]` (line 43).
- `register_provider_object(provider, source_id=None)` (line 60): suppresses AttributeError/TypeError when setting `provider.source_id`, then `_PROVIDERS[provider.api] = provider`. Idempotent / overwrites by api key.
- `_resolve_provider(api)` does `_PROVIDERS.get(api)`; **when `None` it raises at line 149**:
```python
provider = _PROVIDERS.get(api)
if provider is None:
    from aelix_ai.streaming import StreamSimpleError
    raise StreamSimpleError(
        "no_provider_registered",
        f"No provider registered for api={api!r}. ...")
```
Resolution is EAGER: `aelix_ai.streaming.stream_simple(model, context, options)` calls `_resolve_provider(model.api)` at call time (before streaming), so the error surfaces on `await stream_simple(...)`, not on first `__anext__`. **You do NOT edit this raise site. You make it stop firing for an api by registering a Provider whose `.api` equals that api string.**

### 3.3 Wiring a new api (3 facts)
1. Set `provider.api` to the EXACT `model.api` string (e.g. `"openai-responses"`).
2. `register_provider_object(provider, source_id=BUILTIN_SOURCE_ID)` where `BUILTIN_SOURCE_ID = "aelix-ai.builtin"` (shared across all built-ins so one `unregister_providers_by_source("aelix-ai.builtin")` removes them all — anthropic.py line 76, openai_completions.py line 83).
3. Call your `register_all()` from `runtime_bootstrap.register_providers()` (the §2 touchpoint).

### 3.4 Streaming contract — emitted events
Event classes live in `packages/aelix-ai/src/aelix_ai/streaming.py` (verified line numbers): `AssistantStartEvent` (196), `TextStartEvent`(204)/`TextDeltaEvent`(213)/`TextEndEvent`(227), `ThinkingStartEvent`(237)/`ThinkingDeltaEvent`(246)/`ThinkingEndEvent`(256), `ToolCallStartEvent`(266)/`ToolCallDeltaEvent`(275)/`ToolCallEndEvent`(304), `AssistantDoneEvent`(314), `AssistantErrorEvent`(332). `AssistantEndEvent`(347) is a DEPRECATED subclass — do NOT emit it. `ProviderResponse`(384), `SimpleStreamOptions`(396), `StreamFn`(431), `StreamSimpleError`(437).

Required sequence (matched by both existing adapters):
1. `AssistantStartEvent(partial=AssistantMessage(content=[]))` — exactly once, FIRST.
2. Per content block in arrival order: `*StartEvent` → N× `*DeltaEvent` → `*EndEvent` (Text / Thinking / ToolCall families).
3. TERMINAL, exactly one of:
   - SUCCESS `AssistantDoneEvent(reason, message)`, `reason ∈ {"stop","length","toolUse"}` (**camelCase `toolUse`**), `message` = fully-assembled `AssistantMessage`.
   - FAILURE/ABORT `AssistantErrorEvent(reason, error, error_message)`, `reason ∈ {"aborted","error"}`. On error emit ONLY this.

Conventions (verified against openai_completions.py):
- `content_index` = position in the running `output_content` list (use `len(output_content)` at append time).
- `partial` on every event = `AssistantMessage(content=list(output_content))` snapshot.
- Content blocks are FROZEN dataclasses (`messages.py`): `TextContent`(33), `ImageContent`(39), `ThinkingContent`(56, field `thinking_signature: str` line 68), `ToolCallContent`(74, fields `tool_call_id`(75)/`tool_name`(76)/`input: dict`(77)). To "extend" a block, REPLACE the element in `output_content[i]` with a new instance — never mutate. The tool-delta type literal is `"toolcall_delta"` (no underscore between tool/call).
- Final `AssistantMessage` populates: `content, stop_reason, error_message, usage, api, provider, model`. `stop_reason` carries pi spelling (`toolUse`/`length`/`stop`/`error`/`aborted`).
- USAGE: build a dict with keys `input, output, input_tokens, output_tokens, total_tokens, cache_read` (see `_usage_to_dict`, openai_completions.py:846) and set on `AssistantMessage.usage` so the context meter + `/cost` work.
- ERROR PATTERN: wrap the whole generator body in `try/except Exception`; on exception build an error `AssistantMessage` snapshot and `yield AssistantErrorEvent(...)`. Never raise out of the generator EXCEPT an `_AuthError`-style sentinel (see auth).
- ABORT: check `opts.signal is not None and getattr(opts.signal, "aborted", False)`; treat as `reason="aborted"`.

### 3.5 Auth wiring — honor BOTH paths
PATH 1 (harness-injected, production agent loop): `AgentHarness._make_stream_fn` (`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`) calls `options.get_api_key_and_headers(model)` (line 3445), builds `SimpleStreamOptions(api_key=..., headers=..., ...)`, calls `stream_simple(...)`. So your adapter receives the resolved key via `options.api_key`.

PATH 2 (env fallback, used by the CLI path which wires NO auth callback): mirror pi `apiKey || getEnvApiKey(provider) || ""`:
```python
from aelix_ai.providers._env_api_keys import get_env_api_key
api_key = opts.api_key or get_env_api_key(model.provider) or ""
```
`get_env_api_key` reads `ENV_API_KEYS` in `providers/_env_api_keys.py` (verified map): `openai→OPENAI_API_KEY`, `azure-openai-responses→AZURE_OPENAI_API_KEY`, `google→GEMINI_API_KEY`, `google-vertex→GOOGLE_CLOUD_API_KEY`, `mistral→MISTRAL_API_KEY`. **`amazon-bedrock` and `openai-codex` are NOT in the map.** The module docstring (lines 16-21) explicitly DEFERS the Vertex-ADC branch and the entire amazon-bedrock branch to the adapters — you must add them (§4.3, §4.2-Google).

AUTH-FAILURE UX: the harness catches a module-private `_AuthError` from the anthropic adapter and converts it to `AgentHarnessError("auth", ...)` (`core.py:3518-3525`, verified — it imports `from aelix_ai.providers.anthropic import _AuthError`). For OpenAI-family the env-fallback `RuntimeError("No API key for provider: ...")` is sufficient. If you need OAuth-failure UX for codex, either raise the existing `aelix_ai.providers.anthropic._AuthError` (already caught) or surface as `AssistantErrorEvent`.

### 3.6 New-adapter module layout (both existing adapters follow exactly)
1. `XXX_API: str = "<api-id>"` (must equal `model.api`).
2. `BUILTIN_SOURCE_ID: str = "aelix-ai.builtin"`.
3. module-level `async def stream_xxx(model, context, options=None) -> AsyncIterator[AssistantMessageEvent]` — the real body.
4. `class _XxxProvider:` with `api`, `source_id`, `stream`, `stream_simple` delegating to the module functions.
5. `XXX_PROVIDER: Provider = _XxxProvider()`.
6. `def register_all() -> None: register_provider_object(XXX_PROVIDER, source_id=BUILTIN_SOURCE_ID)`.
7. `__all__` exporting the API constant, the singleton, `register_all`, `stream_xxx`, and helpers tests need (`build_params`, `convert_messages`, …).

Skeleton (copy openai_completions.py structure):
```python
"""<Provider> adapter — Phase B. Pi parity: providers/<provider>.ts (SHA 734e08e)."""
from __future__ import annotations
import inspect
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from aelix_ai.api_registry import register_provider_object
from aelix_ai.messages import AssistantMessage, TextContent, ThinkingContent, ToolCallContent
from aelix_ai.providers._env_api_keys import get_env_api_key
from aelix_ai.streaming import (
    AssistantDoneEvent, AssistantErrorEvent, AssistantMessageEvent, AssistantStartEvent,
    Context, Model, ProviderResponse, SimpleStreamOptions,
    TextDeltaEvent, TextEndEvent, TextStartEvent,
    ThinkingDeltaEvent, ThinkingEndEvent, ThinkingStartEvent,
    ToolCallDeltaEvent, ToolCallEndEvent, ToolCallStartEvent,
)
if TYPE_CHECKING:
    from aelix_ai.providers._base import Provider

OPENAI_RESPONSES_API: str = "openai-responses"
BUILTIN_SOURCE_ID: str = "aelix-ai.builtin"

async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value

def build_params(model: Model, context: Context, options: SimpleStreamOptions) -> dict[str, Any]:
    raise NotImplementedError  # port pi buildParams; honor base_url/headers/compat/reasoning + ADR-0114 cap guard

async def stream_openai_responses(model, context, options=None):
    opts = options if options is not None else SimpleStreamOptions()
    output_content: list[Any] = []
    stop_reason = "stop"; error_message = None; captured_usage = None
    try:
        api_key = opts.api_key or get_env_api_key(model.provider) or ""
        client = opts.client or _create_client(api_key=api_key, base_url=model.base_url or None,
                  default_headers={**(opts.headers or {}), **(model.headers or {})} or None,
                  timeout_ms=opts.timeout_ms, max_retries=opts.max_retries)
        params = build_params(model, context, opts)
        if opts.on_payload is not None:
            nxt = await _maybe_await(opts.on_payload(params, model))
            if nxt is not None: params = nxt
        sdk_stream, raw = await _open_stream(client, params)
        if opts.on_response is not None:
            http = getattr(raw, "http_response", raw)
            await _maybe_await(opts.on_response(ProviderResponse(
                status=int(getattr(http, "status_code", 200) or 200),
                headers={str(k): str(v) for k, v in dict(getattr(http, "headers", {}) or {}).items()}), model))
        yield AssistantStartEvent(partial=AssistantMessage(content=list(output_content)))
        async for raw_event in sdk_stream:
            ...  # translate -> *Start/*Delta/*End; mutate output_content (replace frozen blocks); capture usage/stop
        if opts.signal is not None and getattr(opts.signal, "aborted", False):
            raise RuntimeError("Request was aborted")
        if stop_reason == "error":
            raise RuntimeError(error_message or "Provider returned an error stop reason")
        output = AssistantMessage(content=list(output_content), stop_reason=stop_reason,
                 error_message=error_message, usage=captured_usage,
                 api=model.api, provider=model.provider, model=model.id)
        done_reason = ("toolUse" if stop_reason == "toolUse" else
                       "length" if stop_reason == "length" else "stop")
        yield AssistantDoneEvent(reason=done_reason, message=output)
    except Exception as exc:  # noqa: BLE001
        aborted = bool(opts.signal is not None and getattr(opts.signal, "aborted", False))
        reason = "aborted" if aborted else "error"
        err = str(exc) or type(exc).__name__
        yield AssistantErrorEvent(reason=reason,
            error=AssistantMessage(content=list(output_content), stop_reason=reason,
                error_message=err, api=model.api, provider=model.provider, model=model.id),
            error_message=err)

class _OpenAIResponsesProvider:
    api: str = OPENAI_RESPONSES_API
    source_id: str | None = None
    def stream(self, model, context, options): return stream_openai_responses(model, context, options)
    def stream_simple(self, model, context, options): return stream_openai_responses(model, context, options)

OPENAI_RESPONSES_PROVIDER: Provider = _OpenAIResponsesProvider()
def register_all() -> None:
    register_provider_object(OPENAI_RESPONSES_PROVIDER, source_id=BUILTIN_SOURCE_ID)
__all__ = ["OPENAI_RESPONSES_API", "OPENAI_RESPONSES_PROVIDER", "build_params", "register_all", "stream_openai_responses"]
```

Existing helpers to REUSE (verified present in `providers/`): `_streaming_json.py` (`parse_streaming_json`), `_sanitize_unicode.py` (`sanitize_surrogates`), `_transform_messages.py` (`transform_messages`), `_openai_compat.py` (compat dataclass), `_openai_client.py`, `_anthropic_client.py`. Reference adapters to copy: **`anthropic.py`** (503 LOC, simplest SDK-driven template — best for Google/Mistral/Bedrock with typed event streams) and **`openai_completions.py`** (1390 LOC, fullest — env fallback line 945, `build_params` line 612 with the ADR-0114 cap guard at 660-672, `_relocate_extra_body_params` line 818, `_usage_to_dict` line 846, `_open_stream` via `with_raw_response`, `stream_simple_openai_completions` eager factory line 1295).

---

## 4. Per-API implementation specs

### 4.1 OpenAI Responses family (`openai-responses`, `azure-openai-responses`, `openai-codex-responses`)

**Pi files** (`packages/ai/src/providers/`): `openai-responses-shared.ts` (551 LOC — shared core), `openai-responses.ts` (295), `azure-openai-responses.ts` (281), `openai-codex-responses.ts` (1373). Plus `utils/hash.ts` (shortHash), `simple-options.ts`, `models.ts` (calculateCost, clampThinkingLevel). Fetch e.g.:
```bash
gh api repos/earendil-works/pi/contents/packages/ai/src/providers/openai-responses-shared.ts?ref=734e08edf82ff315bc3d96472a6ebfa69a1d8016 --jq .content | base64 -d
```

**SDK fact (verified, openai 1.109.1):** `AsyncOpenAI().responses.create(...)` natively accepts: `include, input, instructions, max_output_tokens, model, parallel_tool_calls, prompt_cache_key, reasoning, service_tier, store, stream, temperature, tool_choice, tools, extra_headers, extra_body, timeout`. **`prompt_cache_retention` is NOT a native kwarg → it MUST go in `extra_body`.** Build a Responses-specific extra_body allowlist (the completions `_EXTRA_BODY_PARAM_KEYS` is for chat.completions — do not reuse blindly). `AsyncAzureOpenAI(api_version=, azure_endpoint=or base_url=)` exists.

**Request shape (Responses, NOT chat-completions):** params = `{model: model.id, input: <ResponseInput list>, stream: true, store: false, prompt_cache_key: session_id, max_output_tokens?, temperature?, service_tier?, tools?, reasoning?{effort,summary}, include?:["reasoning.encrypted_content"]}`. `input` is an ORDERED list of items (not role/content dicts):
- system/developer prompt: `{role, content: str}` — role = `"developer"` when `model.reasoning` else `"system"` (NO compat gate here; OpenRouter is NOT a Responses provider so ADR-0118 does not apply).
- user: `{role:"user", content:[{type:"input_text",text} | {type:"input_image",detail:"auto",image_url:"data:<mime>;base64,<data>"}]}`.
- assistant text: `{type:"message",role:"assistant",content:[{type:"output_text",text,annotations:[]}],status:"completed",id:<≤64ch>}`.
- assistant reasoning: `JSON.parse(thinkingSignature)` back into a `{type:"reasoning",...}` item (the encrypted_content replay).
- assistant tool call: `{type:"function_call",id:<fc_…>,call_id,name,arguments:json.dumps(args)}`.
- tool result: `{type:"function_call_output",call_id,output}`.
Tool-call ids use composite `"call_id|item_id"` pipe format; itemId must start with `fc_` (foreign calls hashed via `fc_+shortHash`, ≤64 chars). Cross-model assistant tool calls drop the fc_ itemId to avoid OpenAI reasoning-pairing validation.

**Tools (convert_responses_tools):** FLAT shape `[{type:"function", name, description, parameters:<JSON schema>, strict:<bool|null>}]` (name/description/parameters at TOP level, NOT nested under `function`). Default `strict=false`; codex uses `strict=null`.

**Stream (process_responses_stream):** consumes the SDK `ResponseStreamEvent` async iterator. Map: `response.created`→capture id; `response.output_item.added` (reasoning/message/function_call)→open thinking/text/toolCall block + emit `*_start`; `response.reasoning_summary_part.added/.done` + `reasoning_summary_text.delta` + `reasoning_text.delta`→thinking_delta (summary appends `"\n\n"` on part.done); `response.output_text.delta`/`refusal.delta`→text_delta; `response.function_call_arguments.delta`→append to partialJson, `parse_streaming_json`, toolcall_delta; `...arguments.done`→final args; `response.output_item.done`→finalize block + `*_end` (reasoning thinkingSignature = `json.dumps(item)`); `response.completed`→usage (`input = input_tokens - cached_tokens`, output, cache_read = `input_tokens_details.cached_tokens`, total) + calculateCost + map status→stop_reason (completed→stop, incomplete→length, failed/cancelled→error) and UPGRADE stop→toolUse when a toolCall block exists; `error` event→raise `Error Code <code>: <message>`.

**Reasoning:** gated on `model.reasoning`. With effort/summary: `reasoning={effort: model.thinking_level_map[effort] ?? effort (default "medium"), summary: reasoning_summary or "auto"}` AND `include=["reasoning.encrypted_content"]`. Reasoning model but no effort: `reasoning={effort: thinking_level_map.off ?? "none"}` (skip for github-copilot; skip when `thinking_level_map.off is None`). `xhigh`→clamp to `high`.

**Auth:** `openai-responses` = `OPENAI_API_KEY` (in map). github-copilot = `COPILOT_GITHUB_TOKEN` + dynamic copilot headers; cloudflare = `cf-aig-authorization` Bearer. session_id → `session_id` + `x-client-request-id` headers when `compat.send_session_id_header`.

**Azure:** model field = DEPLOYMENT NAME (from azure_deployment_name option | `AZURE_OPENAI_DEPLOYMENT_NAME_MAP` env | model.id); base url via `AZURE_OPENAI_BASE_URL` | `AZURE_OPENAI_RESOURCE_NAME` (→ `https://<res>.openai.azure.com/openai/v1`) | model.base_url; `AZURE_OPENAI_API_VERSION` default `"v1"`. Use `openai.AsyncAzureOpenAI(api_version=, azure_endpoint= or base_url=)`. NO `store`/`prompt_cache_retention`. Reuse the SAME shared converters + process_responses_stream. `AZURE_OPENAI_API_KEY` already in map.

**Codex:** OAuth ONLY (NO api-key env; `openai-codex` NOT in map). Bespoke RequestBody (NOT the SDK): `{model, store:false, stream:true, instructions: system_prompt or "You are a helpful assistant." (system prompt goes in `instructions`, convert_responses_messages called with includeSystemPrompt=false), input, text:{verbosity:"low"}, include:["reasoning.encrypted_content"], prompt_cache_key, tool_choice:"auto", parallel_tool_calls:true, reasoning?, service_tier?}`. Tools `strict=null`. Base url default `https://chatgpt.com/backend-api` → `/codex/responses`. Headers: `Authorization: Bearer <token>`, `chatgpt-account-id`, `originator: "aelix"`, `OpenAI-Beta: responses=experimental`, `accept: text/event-stream`. **Token + account-id helpers ALREADY EXIST**: `aelix_ai/oauth/openai_codex.py` `_get_account_id` (line 148), `_decode_jwt_payload` (line 121), `OPENAI_CODEX_OAUTH_ID="openai-codex"` (line 65); and `AuthStorage.get_oauth_api_key(provider_id)` (auth_storage.py line 331). **Ship SSE-first** via `httpx.AsyncClient` streaming (port pi `parseSSE`: split on `b"\n\n"`, strip `data:`, skip `[DONE]`, json.parse each; `mapCodexEvents` normalizes `response.done`/`response.incomplete`→`response.completed`, `error`/`response.failed`→error, then feed the SAME process_responses_stream). DEFER the WebSocket transport (pi falls back to SSE on WS failure anyway). Add `MAX_RETRIES=3` exponential backoff honoring retry-after.

**Python porting gotchas (Responses):** (1) `prompt_cache_retention` → extra_body (others are native). (2) Apply the ADR-0114 `max_output_tokens >= context_window → omit` guard (verified pattern at openai_completions.py:660-672). (3) Port `shortHash` FAITHFULLY with 32-bit wraparound (`h & 0xFFFFFFFF`, `Math.imul` = `(a*b) & 0xFFFFFFFF` via int32 emulation) + base36 of the unsigned `>>>0` results — naive Python ints diverge and break tool-call id pairing; add a byte-parity test against pi sample outputs. (4) `thinkingSignature` for reasoning = `json.dumps(item)` stored in `ThinkingContent.thinking_signature` (str). (5) Replace frozen content blocks, don't mutate.

**Implementation outline:**
1. `providers/_hash.py` — port `shortHash` + base36; parity test.
2. `providers/_openai_responses_shared.py` — `convert_responses_messages`, `convert_responses_tools`, `process_responses_stream`, `map_stop_reason`, encode/parse_text_signature. Reuse `parse_streaming_json` + `sanitize_surrogates`.
3. Responses extra_body relocation helper (at least `prompt_cache_retention`) + cap guard.
4. `providers/openai_responses.py` — `_create_client` (reuse `_openai_client`), `build_params` (input via shared converter, store=False, prompt_cache_key, reasoning+include, max_output_tokens guarded, tools), `stream_openai_responses` over `client.responses.with_raw_response.create(**params)`, `stream_simple_openai_responses` eager factory, `_OpenAIResponsesProvider(api="openai-responses")` + `register_all`.
5. `providers/azure_openai_responses.py` — `AsyncAzureOpenAI` client, deployment-name map, base-url/api_version resolution, reuse shared converters; `api="azure-openai-responses"`.
6. `providers/openai_codex_responses.py` (SSE-first) — build_request_body, resolve_codex_url, account-id via existing oauth helper, httpx SSE + retry, `parse_sse` + `map_codex_events`→process_responses_stream; `api="openai-codex-responses"`; key from `opts.api_key` (error if missing). Optionally bridge `AuthStorage.get_oauth_api_key("openai-codex")` in the coding-agent stream-fn setup (coordinate with Phase A or defer).

### 4.2 Google (`google-generative-ai`, `google-vertex`)

**Pi files:** `providers/google.ts` (501), `google-vertex.ts` (568), `google-shared.ts` (350), `env-api-keys.ts` (ADC branch), `simple-options.ts`.

**SDK:** ADD `google-genai` (NOT installed — verified `import google.genai` fails). `from google import genai`; `genai.Client(api_key=...)` or `genai.Client(vertexai=True, project=, location=)`. Stream: `async for chunk in await client.aio.models.generate_content_stream(model=, contents=, config=)`. Config is `genai.types.GenerateContentConfig` (snake_case: `max_output_tokens, system_instruction, thinking_config=types.ThinkingConfig(include_thoughts=, thinking_budget=, thinking_level=), tool_config, temperature`). The SDK is STRICT — put ALL gen params inside the typed config; do NOT splat unknown kwargs (ADR-0114 echo). `http_options=types.HttpOptions(base_url=, api_version=, headers=)` for base_url/headers.

**Request:** both APIs build the same `GenerateContentParameters` via shared converters. `{model: model.id, contents: Content[], config}`. Roles: user→`"user"`, assistant→`"model"`, toolResult→`"user"` with a `functionResponse` part. NO system/developer role — system prompt goes to `config.system_instruction`. So the ADR-0118 developer/system issue does NOT apply. user string→`[{text}]`; multimodal→`{text}` or `{inlineData:{mimeType,data}}`. assistant text→`{text, thoughtSignature?}`; thinking→`{thought:true, text, thoughtSignature?}` ONLY when `msg.provider==model.provider AND msg.model==model.id` (else demote to plain `{text}`); toolCall→`{functionCall:{name,args,id?}, thoughtSignature?}`. toolResult→`{functionResponse:{name, response:{output|error: text}, parts?:[images], id?}}`; CONSECUTIVE toolResults MERGE into the prior user turn (Cloud Code Assist requirement). `id` ONLY when `requiresToolCallId(model.id)` (id startsWith `claude-` or `gpt-oss-`) — pure-Gemini omits functionCall.id/functionResponse.id. Tool-call ids normalized to `[a-zA-Z0-9_-]`, ≤64 chars.

**Tools (convert_tools):** `[{functionDeclarations:[{name, description, parametersJsonSchema: tool.parameters}]}]` (default uses `parametersJsonSchema`). `toolConfig.functionCallingConfig.mode` set ONLY when tools present AND `options.toolChoice` set (auto→AUTO, none→NONE, any→ANY).

**Stream:** per chunk: capture `chunk.responseId`; iterate `chunk.candidates[0].content.parts`; `finishReason`→map_stop_reason; `usageMetadata`→usage. Maintain ONE currentBlock (text|thinking), flip when `isThinkingPart(part)` (= `part.thought is True`) changes — emit `*_end` for old then `*_start` for new. `part.text`→text/thinking delta. `part.functionCall`→close any open block, then emit toolcall_start + ONE toolcall_delta (`delta=json.dumps(args)`) + toolcall_end together (Gemini delivers COMPLETE tool calls — args already a dict, do NOT accumulate partial json). After loop flush open block. `stopReason` FORCED to `toolUse` whenever any toolCall block exists. `thoughtSignature` can ride on ANY part — retain per-block (keep last non-empty) for replay; only replay when same provider+model.

**Reasoning:** `clamp_thinking_level(model, reasoning)`; off→coerce. Two strategies by family: (A) Gemini-3 pro/flash + Gemma-4 use `thinking_config.thinking_level` (MINIMAL/LOW/MEDIUM/HIGH; gemini-3-pro collapses minimal/low→LOW, medium/high→HIGH); (B) Gemini-2.5 use `thinking_budget` integer (2.5-pro: 128/2048/8192/32768; default -1 dynamic). When enabled & model.reasoning: `thinking_config={include_thoughts:True, (thinking_level|thinking_budget)}`. Disabled-thinking: gemini-3-pro CANNOT disable (uses LOW), gemini-3-flash/gemma4→MINIMAL without include_thoughts, gemini-2.x→thinking_budget:0.

**Auth:** google = `options.api_key or get_env_api_key("google") (GEMINI_API_KEY) or ""`; throw `No API key for provider: ...` if empty. Vertex = TWO paths: (1) `GOOGLE_CLOUD_API_KEY` (in map) → `genai.Client(vertexai=True, api_key=, api_version="v1", http_options=)` (reject sentinel `gcp-vertex-credentials` + placeholder `^<[^>]+>$`); (2) ADC (no key) → `genai.Client(vertexai=True, project=resolveProject(), location=resolveLocation(), api_version="v1")`. `resolveProject` = `options.project or GOOGLE_CLOUD_PROJECT or GCLOUD_PROJECT` (throw if missing); `resolveLocation` = `options.location or GOOGLE_CLOUD_LOCATION` (throw if missing). **You MUST extend `_env_api_keys.py get_env_api_key` with the deferred ADC branch** (port pi env-api-keys.ts:166-181): add `has_vertex_adc_credentials()` returning True when `GOOGLE_APPLICATION_CREDENTIALS`-or-`~/.config/gcloud/application_default_credentials.json` exists AND project AND location set; then `get_env_api_key("google-vertex")` returns sentinel `"<authenticated>"` in that case. Token minting is delegated to the SDK.

**Python gotchas (Google):** thoughtSignature is base64 TYPE_BYTES (validate len%4==0 + base64 regex before replay). Gemini delivers complete tool calls. ToolResult merge is REQUIRED. `max_output_tokens` set ONLY when `options.maxTokens` provided (do NOT default to context_window). Abort: Python SDK has no AbortSignal — check `options.signal.aborted` around the async-for. usage: `input = promptTokenCount - cachedContentTokenCount`, `output = candidatesTokenCount + thoughtsTokenCount`, `cache_read = cachedContentTokenCount`. `ToolCallContent` fields are `tool_call_id/tool_name/input` (NOT id/name/arguments). Reuse `sanitize_surrogates`.

**Implementation outline:** `providers/_google_shared.py` (convert_messages, convert_tools, map_tool_choice, map_stop_reason, is_thinking_part, retain_thought_signature, requires_tool_call_id), `providers/_google_thinking.py` (family helpers + budget/level tables), `providers/google.py` (`GOOGLE_API="google-generative-ai"`), `providers/google_vertex.py` (`GOOGLE_VERTEX_API="google-vertex"`, share a `_stream_google_genai(client, params, model, options)` core to avoid duplicating the ~150-line event loop), extend `_env_api_keys.py` for ADC. Add `google-genai` to pyproject.

### 4.3 AWS Bedrock (`bedrock-converse-stream`, 84 models — largest)

**Pi files:** `providers/amazon-bedrock.ts` (943 — entire provider), `bedrock-provider.ts`, `register-builtins.ts:76-394`, `env-api-keys.ts:183-203`, `simple-options.ts:3-41`, `transform-messages.ts`.

**SDK:** ADD `boto3` (NOT installed — verified). botocore `converse_stream` is a BLOCKING generator → drive via `asyncio.to_thread`/`run_in_executor` pumping into an `asyncio.Queue` so the agent loop keeps draining (mirror the ADR-0131 $EDITOR pattern), OR use `aioboto3` for native async-for. boto3 gives region/profile/credential chain + SigV4 for free.

**Request (ConverseStream):** `{modelId: model.id, messages, system, inferenceConfig:{maxTokens?,temperature?}, toolConfig, additionalModelRequestFields, requestMetadata?}`. messages = `[{role: USER|ASSISTANT, content: ContentBlock[]}]`. ContentBlock variants: `{text}`, `{image:{source:{bytes},format}}` (raw BYTES via `base64.b64decode`, NOT base64 string), `{toolUse:{toolUseId,name,input}}` (input = parsed object), `{toolResult:{toolUseId,content:[{text}|{image}],status:SUCCESS|ERROR}}`, `{reasoningContent:{reasoningText:{text,signature?}}}`, `{cachePoint:{type:DEFAULT,ttl?}}`. system = `[{text}, optional {cachePoint}]`. CONSECUTIVE toolResult messages MUST merge into ONE user message. SKIP empty assistant content arrays + empty text/thinking blocks. Tool inputSchema = `{json: tool.parameters}`. Region: `options.region or AWS_REGION or AWS_DEFAULT_REGION`; endpoint pinned to model.base_url only when no standard `bedrock-runtime.<region>.amazonaws.com` host or no region+profile; fallback us-east-1 only when no profile.

**Stream (AWS SDK event stream, NOT SSE):** `for item in response["stream"]` — tagged union, switch on present key. `messageStart`→AssistantStartEvent. `contentBlockStart`→only toolUse emits start (Block{id=toolUseId, name, partialJson="", index=contentBlockIndex})→toolcall_start. `contentBlockDelta`: `delta.text`→lazily create text block (Bedrock sends NO text start)+text_start then text_delta; `delta.toolUse.input`→append partialJson, `parse_streaming_json`, toolcall_delta; `delta.reasoningContent`→lazily create thinking block+thinking_start, append text→thinking_delta, accumulate `.signature` into thinkingSignature (NOT streamed). `contentBlockStop`→find block by wire `.index`, finalize, `*_end` (toolCall: final parse_streaming_json). `messageStop`→stop_reason=map_stop_reason. `metadata`→usage from `metadata.usage.{inputTokens,outputTokens,cacheReadInputTokens,cacheWriteInputTokens,totalTokens}`+calculateCost. Exception items (`internalServerException` etc.)→raise. **CRITICAL: keep TWO indices — a wire→block lookup (mutable bookkeeping dict, do NOT mutate frozen `ToolCallContent`) AND the array-position `content_index` emitted on events.** On error: strip scratch, stop_reason=error|aborted, errorMessage=format_bedrock_error, AssistantErrorEvent.

**Tools (convert_tool_config):** undefined if no tools or toolChoice==none. Each→`{toolSpec:{name, description, inputSchema:{json: tool.parameters}}}`. toolChoice: auto→`{auto:{}}`, any→`{any:{}}`, {tool,name}→`{tool:{name}}`. toolUseId normalize on outbound: replace non-`[a-zA-Z0-9_-]` with `_`, ≤64 chars.

**Reasoning (additionalModelRequestFields):** ONLY when `options.reasoning AND model.reasoning AND isAnthropicClaudeModel` (id/name contains anthropic.claude / anthropic/claude / claude). (1) ADAPTIVE for opus-4-6/4-7/sonnet-4-6: `{thinking:{type:"adaptive",display?}, output_config:{effort}}`. (2) NON-adaptive: `{thinking:{type:"enabled", budget_tokens, display?}}` budgets `{minimal:1024,low:2048,medium:8192,high:16384,xhigh→high}`; also adds `anthropic_beta:["interleaved-thinking-2025-05-14"]` unless `interleavedThinking===false`. `display = thinkingDisplay ?? "summarized"`, OMITTED on GovCloud. Non-Claude→undefined. OUTBOUND replay: only Anthropic gets reasoningText.signature; without signature FALL BACK to plain `{text}` (Bedrock rejects unsigned reasoning replay).

**Auth (AWS default chain, NO single key):** treat amazon-bedrock as configured if ANY of `AWS_PROFILE | (AWS_ACCESS_KEY_ID && AWS_SECRET_ACCESS_KEY) | AWS_BEARER_TOKEN_BEDROCK | AWS_CONTAINER_CREDENTIALS_RELATIVE_URI | AWS_CONTAINER_CREDENTIALS_FULL_URI | AWS_WEB_IDENTITY_TOKEN_FILE`. **You MUST add this branch to `_env_api_keys.py`** (deferred per its docstring). Bearer path: `bearerToken = options.bearerToken or AWS_BEARER_TOKEN_BEDROCK`; if set and `AWS_BEDROCK_SKIP_AUTH!="1"`→use bearer (botocore `token=` + `authSchemePreference=["httpBearerAuth"]`, or httpx Authorization: Bearer fallback if botocore too old). `AWS_BEDROCK_SKIP_AUTH=="1"`→dummy creds. Also handle `AWS_BEDROCK_FORCE_HTTP1`, `AWS_BEDROCK_FORCE_CACHE`, `PI_CACHE_RETENTION`.

**Python gotchas (Bedrock):** additionalModelRequestFields is a NATIVE converse param (pass directly, no extra_body). Role naming N/A (system is top-level). image: `base64.b64decode` to raw bytes. Use parallel mutable bookkeeping (frozen content has no index/partialJson). Exception mapping: botocore raises `ClientError`/EventStream errors → map error_code (InternalServerException/ModelStreamErrorException/ValidationException/ThrottlingException/ServiceUnavailableException) to pi human-readable prefixes (Internal server error / Model stream error / Validation error / Throttling error / Service unavailable) — downstream retry matches `server.?error`/`service.?unavailable`. Port `adjustMaxTokensForThinking` + consider Bedrock 400 on maxTokens over the model ceiling.

**Implementation outline:** `providers/_bedrock_client.py` (region/profile/endpoint/bearer resolution + GovCloud detection), extend `_env_api_keys.py` (AWS branch), port `simple-options` helpers (buildBaseOptions, clampReasoning, adjustMaxTokensForThinking) into `providers/_simple_options.py` (or reuse), `providers/amazon_bedrock.py` (convert_messages, build_system_prompt, convert_tool_config, build_additional_model_request_fields, map_stop_reason, model predicates, the stream generator with blocking→async pump, format_bedrock_error, stream_simple branching). `api="bedrock-converse-stream"`. Add `boto3` to pyproject.

### 4.4 Mistral (`mistral-conversations`, 28 models)

**Pi files:** `providers/mistral.ts` (634), `env-api-keys.ts:113`, `simple-options.ts`, `transform-messages.ts`, `utils/hash.ts`, `utils/json-parse.ts`, `utils/sanitize-unicode.ts`.

**SDK (recommended):** REUSE the installed `openai` `AsyncOpenAI` against `base_url = model.base_url + "/v1"` (Mistral HTTP is OpenAI-compatible chat-completions) — avoids a new `mistralai` dep (NOT installed) and inherits ADR-0114 `_relocate_extra_body_params`.

**Request:** POST `{base}/v1/chat/completions`, `stream:true`. `{model: model.id, stream:true, messages:[...]}`. Optional: tools, temperature, `max_tokens` (snake on the wire), tool_choice, and reasoning params. System prepended as `{role:"system", content: sanitize_surrogates(system_prompt)}` — Mistral uses `"system"` NOT `"developer"`. user→bare string or parts `[{type:"text"|"image_url"}]` where `image_url` is a **bare data-URL STRING** `data:<mime>;base64,<data>` (NOT `{url:...}`). assistant→`{role:"assistant", content:[{type:"text"|"thinking"}], tool_calls:[{id,type:"function",function:{name,arguments:json.dumps(args)}}]}` (thinking re-serialized as `{type:"thinking", thinking:[{type:"text",text}]}`). tool result→`{role:"tool", tool_call_id, name, content:[{type:"text",text}, optional {type:"image_url",image_url}]}`. Header `x-affinity: session_id` when `options.session_id` set and no explicit x-affinity.

**Tools:** `[{type:"function", function:{name, description, parameters, strict:false}}]`. tool_choice supports `"auto"|"none"|"any"|"required"` (SUPERSET of OpenAI — preserve literals) or `{type:"function", function:{name}}`.

**Stream:** iterate SDK chunks. `delta.content` may be a STRING **OR a LIST** of `{type:"text"|"thinking"}` items (KEY divergence from OpenAI). thinking item carries `thinking: [{type:"text",text}]` — concat → thinking_* events. `delta.tool_calls`→lazy toolcall_start; arguments delta is a string appended to partialArgs + parse_streaming_json → toolcall_delta; toolcall_end at end. `finish_reason`: `model_length`→length, `tool_calls`→toolUse, stop→stop, length→length, error→error. usage: `input=promptTokens, output=completionTokens, cache_read/write=0`.

**Reasoning:** `clamp_thinking_level`; off→undefined. `usesReasoningEffort` TRUE only for ids `mistral-small-2603`, `mistral-small-latest`, `mistral-medium-3.5`→`reasoning_effort = thinking_level_map.get(level) or "high"` (enum is only `"none"|"high"`). Else (magistral-*, mistral-medium-2604/latest)→`prompt_mode:"reasoning"`. INBOUND reasoning arrives as `{type:"thinking", thinking:[{type:"text",text}]}` content items (NOT a `reasoning_content` field — the openai_completions REASONING_FIELDS scan will MISS it).

**Auth:** `MISTRAL_API_KEY` (in map). Throw `No API key for provider: mistral` eagerly in the sync `stream_simple` factory.

**Python gotchas (Mistral):** `prompt_mode`/`reasoning_effort` are NOT native OpenAI kwargs → route through `extra_body` (snake_case `prompt_mode`); the OpenAI SDK rejects unknown top-level kwargs (ADR-0114). Do NOT reuse OpenAI reasoning_effort validation (enum differs). Mistral uses `"system"` (do NOT apply supports_developer_role). maxTokens==contextWindow on nearly every model (e.g. devstral-2512 262144/262144) → apply ADR-0114 omit guard or 400. image_url is a bare string. **Tool-call ids MUST be exactly 9 alphanumeric chars** — OpenAI's 40-char normalizer is insufficient; port `deriveMistralToolCallId` (strip non-alnum; if attempt 0 and already len 9 use as-is; else `shortHash(seed)` slice(0,9)) with a per-stream collision-safe idMap/reverseMap. Pass it to `transform_messages` as the normalize callback.

**Implementation outline:** `providers/mistral.py` reusing `_openai_client` against Mistral base_url; `MistralOptions(SimpleStreamOptions)` (tool_choice incl 'any'/'required', reasoning_effort, prompt_mode); port the id normalizer; `build_params` (system role, image bare data-URL, content-array thinking, max_tokens omit guard, reasoning via extra_body); `consume_chat_stream` (content string OR list); `stream_simple_mistral` eager factory; `_MistralProvider(api="mistral-conversations")` + register_all.

---

## 5. Implementation order + counts

Do them in THIS order (first-party OpenAI first; reuse compounds):
1. **`openai-responses` (81)** — first-party OpenAI (gpt-4.1/4o/5/o-series), single `OPENAI_API_KEY`, builds the shared Responses core reused by #2 and #6. Reuses installed `openai` SDK.
2. **`azure-openai-responses` (42)** — thinnest (281 LOC pi); ~95% reuse of #1's shared core; swap to `AsyncAzureOpenAI` + deployment routing.
3. **`mistral-conversations` (28)** — self-contained, single `MISTRAL_API_KEY`, reuse `openai` SDK against base_url, easy live test.
4. **`google-generative-ai` (30)** — easiest Google creds (free `GEMINI_API_KEY`); builds the google-shared converters reused by #5. Add `google-genai`.
5. **`google-vertex` (13)** — reuses #4's converters; only new work = ADC/project/location auth branch in `_env_api_keys.py`.
6. **`openai-codex-responses` (10)** — reuses #1 converters but adds the largest/custom SSE transport + ChatGPT-OAuth. High effort for 10 models; do after the cheap Responses wins. SSE-only first.
7. **`bedrock-converse-stream` (84)** — highest count but most isolated/complex: new `boto3` dep, SigV4, AWS multi-cred matrix, non-OpenAI Converse shape, hardest creds. No reuse from earlier steps.

---

## 6. Verification gate

### Unit tests (NO creds — do these for EVERY api)
Tests live in `/workspaces/aelix-ai/tests/providers/` (existing examples: `test_openai_completions_unit.py`, `test_openai_completions_streaming.py`, `test_openai_completions_emits.py`). Config: `pyproject.toml` `testpaths=["tests"]`, `asyncio_mode="auto"`. Run:
```bash
cd /workspaces/aelix-ai && python -m pytest tests/providers/ -q          # provider gate
cd /workspaces/aelix-ai && python -m pytest tests/ -q                    # full gate (must stay green)
```
Per adapter add:
- `build_params` shape parity (request fields, ADR-0114 cap omission, reasoning routing — for Responses verify prompt_cache_retention lands in extra_body; for Mistral verify prompt_mode/reasoning_effort in extra_body + 9-char tool ids; for Google verify everything inside the typed config).
- stream-event mapping using CANNED SDK event sequences / a fake async iterator (text, reasoning summary+raw, tool call, completed/usage, error). **Do NOT use `**kwargs` fakes** — they masked the ADR-0114 bug; assert the exact kwargs the SDK receives.
- `shortHash` byte-parity test (Responses/Mistral).
- A registry-dispatch smoke test: register the adapter, build a catalog `Model` for that api, assert `stream_simple` no longer raises `StreamSimpleError("no_provider_registered")`.
- cost/usage population from the completed/metadata event.

### LIVE-credential tests (per provider — the real bar)
| api | env vars |
|---|---|
| openai-responses | `OPENAI_API_KEY` (model e.g. `gpt-4o-mini` or `gpt-5`) |
| azure-openai-responses | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_BASE_URL` or `AZURE_OPENAI_RESOURCE_NAME` + deployment id + `AZURE_OPENAI_API_VERSION` |
| mistral-conversations | `MISTRAL_API_KEY` (e.g. `codestral-latest`) |
| google-generative-ai | `GEMINI_API_KEY` (e.g. `gemini-2.5-flash`) |
| google-vertex | `GOOGLE_CLOUD_API_KEY` OR (`GOOGLE_APPLICATION_CREDENTIALS`/ADC + `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION`) |
| openai-codex-responses | ChatGPT Plus/Pro OAuth token via the codex login flow (`oauth/openai_codex.py`) |
| bedrock-converse-stream | `AWS_PROFILE` or (`AWS_ACCESS_KEY_ID`+`AWS_SECRET_ACCESS_KEY`) or `AWS_BEARER_TOKEN_BEDROCK`, + `AWS_REGION`, + Bedrock model access enabled |

Live check = real round-trip producing TEXT + a TOOL CALL (+ thinking where supported) and populated usage/cost. Easiest live wins: GEMINI_API_KEY, MISTRAL_API_KEY, OPENAI_API_KEY. Hardest: Azure (provisioned resource), codex (ChatGPT OAuth), bedrock (AWS matrix).

**RULE (repeat): do NOT claim "done"/"완벽" on a provider until live-verified. Mark any un-verifiable provider `LIVE-PENDING` in your final report + ADR.**

---

## 7. Definition of done + Risks/gotchas

### Definition of done
1. All 7 adapter modules exist under `packages/aelix-ai/src/aelix_ai/providers/`, each following the §3.6 layout.
2. `_env_api_keys.py` extended with the Vertex-ADC sentinel branch and the AWS amazon-bedrock branch.
3. `pyproject.toml` adds `google-genai` and `boto3`; `uv sync` succeeds.
4. All 7 `register_all()` calls added to `runtime_bootstrap.register_providers()` (the §2 touchpoint, as a final small commit).
5. Unit tests added under `tests/providers/`; `python -m pytest tests/` is GREEN (no regressions).
6. For each of the 288 catalog models' apis, dispatch no longer raises `no_provider_registered` (covered by per-api smoke tests).
7. A new ADR (next free number after ADR-0132) documents the work, the new deps, and per-provider live-verified vs LIVE-PENDING status.
8. Final report HONESTLY states which providers are live-verified and which are code-complete/LIVE-PENDING.

### Risks / gotchas (ADR-0114 lessons + graceful degrade)
- **ADR-0114 (the big one):** the Python SDKs REJECT unknown kwargs. (a) openai `responses.create` rejects `prompt_cache_retention` → extra_body. (b) Mistral via openai SDK rejects `prompt_mode`/`reasoning_effort` → extra_body. (c) google-genai rejects extra top-level kwargs → everything in the typed `GenerateContentConfig`. (d) bedrock additionalModelRequestFields is native (no relocation). **Test fakes must assert exact kwargs — `**kwargs` fakes hide these bugs.**
- **maxTokens==contextWindow 400s:** apply the verified `cap_is_meaningful = not (context_window and max_tokens >= context_window)` guard (openai_completions.py:660-672) for Responses (`max_output_tokens`) and Mistral. Google/Bedrock: only set the cap when explicitly provided; do not default to context_window.
- **shortHash divergence:** naive Python ints diverge from pi's 32-bit Math.imul/base36; port faithfully or tool-call id pairing breaks (Responses + Mistral). Byte-parity test mandatory.
- **Frozen content dataclasses:** REPLACE elements in `output_content`, never mutate. Keep separate mutable bookkeeping for Bedrock wire-index ↔ block lookup.
- **Tool-call id rules differ per provider:** Responses `fc_`+pipe composite; Mistral exactly 9 alnum; Google omit for native Gemini / include for claude-/gpt-oss-; Bedrock `[a-zA-Z0-9_-]` ≤64.
- **Graceful degrade (do NOT hard-crash):** for any still-unsupported model/feature within an api (e.g. a model whose thinking family you don't yet handle, or a missing optional cred), prefer emitting an `AssistantErrorEvent` with a clear message over raising out of the generator — the only exception is the `_AuthError`-style sentinel the harness explicitly catches (`core.py:3518`). The whole point of Phase B is to replace a hard `StreamSimpleError("no_provider_registered")` crash with a real adapter that degrades gracefully.
- **Codex/WebSocket:** ship SSE-only; WS is pi's optional path with SSE fallback. Defer.
- **Bedrock blocking SDK:** botocore `converse_stream` is sync — pump via `asyncio.to_thread`+`asyncio.Queue` (ADR-0131 pattern) or use `aioboto3`, so the agent loop keeps draining.
- **Merge isolation:** Phase B is confined to `packages/aelix-ai/` + the 2-line `register_providers()` insertion + new tests/ADR. Make the `runtime_bootstrap.py` edit LAST and tiny to keep the Phase A merge trivial.

### Quick fetch recipe for pi reference
```bash
gh api repos/earendil-works/pi/git/trees/734e08edf82ff315bc3d96472a6ebfa69a1d8016?recursive=1 \
  --jq '.tree[].path' | grep '^packages/ai/src/providers/'
gh api "repos/earendil-works/pi/contents/packages/ai/src/providers/<file>.ts?ref=734e08edf82ff315bc3d96472a6ebfa69a1d8016" --jq .content | base64 -d
```