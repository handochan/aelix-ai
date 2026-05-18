# 0048. Pi Shared Utilities Ported (`_transform_messages.py`, `_sanitize_unicode.py`, `_streaming_json.py`, `_env_api_keys.py`)

Status: Accepted (Sprint 6b / Phase 4.2 shipped)

## Context

Pi's `packages/ai/src/providers/transform-messages.ts` (218 lines @ SHA
734e08e) is **shared cross-provider infrastructure** — it runs BEFORE
any per-adapter shape transform and is consumed by anthropic /
openai-completions / openai-responses / google / etc. The transforms
handle:

1. Non-vision image downgrade (replace `ImageContent` with placeholder
   text when `"image" not in model.input`).
2. Same-model detection (compare `assistant.api/provider/model` to the
   target model).
3. Thinking block transform (drop encrypted cross-model thinking,
   convert plain cross-model thinking to text, preserve same-model
   signed thinking).
4. Tool call ID normalization with map propagation onto subsequent
   `ToolResultMessage.tool_call_id`.
5. Orphan tool call synthesis — insert synthetic `"No result provided"`
   tool results before the next user message or at end of conversation.
6. Skip errored/aborted assistant turns from the replay.

Sprint 6a's `_anthropic_transforms.py::transform_messages` performs
**none** of this — it only converts Aelix `Message` → Anthropic SDK
shape. The Pi shared layer is structurally distinct from the per-adapter
layer.

Pi also exposes three small utilities consumed by `openai-completions`
(and future adapters):

- `utils/sanitize-unicode.ts::sanitizeSurrogates` (strip lone Unicode
  surrogate code points before sending to the OpenAI API).
- `utils/json-parse.ts::parseStreamingJson` (lenient incremental JSON
  parse for streamed `tool_call.function.arguments`).
- `env-api-keys.ts::getEnvApiKey` + `findEnvKeys` (per-provider env
  var lookup table — 30 rows at this pin).

## Decision

Port the four Pi utility modules into shared infrastructure inside
`packages/aelix-ai/src/aelix_ai/providers/`:

| File | Pi source | Sprint 6b status |
|---|---|---|
| `_transform_messages.py` | `transform-messages.ts` (218 LOC) | **shipped** (OpenAI adapter routes through it) |
| `_sanitize_unicode.py` | `utils/sanitize-unicode.ts` (10 LOC) | **shipped** |
| `_streaming_json.py` | `utils/json-parse.ts` (~30 LOC) | **shipped** |
| `_env_api_keys.py` | `env-api-keys.ts` (210 LOC, 30-row table) | **shipped** (Vertex ADC + Bedrock branches deferred per owning adapter) |

### `_transform_messages.transform_messages(messages, model, *, normalize_tool_call_id)`

Python signature:

```python
def transform_messages(
    messages: list[Message],
    model: Model,
    *,
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None = None,
) -> list[Message]: ...
```

Behaviors mirror Pi byte-for-byte (W0 P-50 verified against the SHA
734e08e source).

### Sprint 6b W6 amendments

- The Sprint 6b `_is_same_model` helper now reads `assistant.api` /
  `provider` / `model` directly off the dataclass — the new provenance
  trio added in ADR-0049 made the `getattr` defensive overkill
  redundant (P-68 follow-through).
- `_flush_synthetic` populates the synthetic `ToolResultMessage` with
  the originating `ToolCallContent.tool_name` (P-75 follow-through).

## Consequences

### Deferred work — Sprint 6d cross-adapter hygiene (P-50-followup)

Sprint 6a's Anthropic adapter is **not** retrofit onto
`_transform_messages.py` in this PR. The Anthropic adapter today still
goes through `_anthropic_transforms.transform_messages` which does NOT
do image downgrades / cross-model thinking handling / orphan synthesis.

The retrofit is captured as:

- **P-50-followup** in ADR-0050 §Carry-forward — Sprint 6d will route
  the Anthropic adapter through `_transform_messages.py` and delete
  `_anthropic_transforms.transform_messages`, keeping only the SDK
  shape helpers.
- The ADR-0049 dataclass extensions are additive — Anthropic's adapter
  continues to mint `AssistantMessage` with `api=None / provider=None /
  model=None` until Sprint 6d wires the population at the build site.

### Deferred work — Vertex ADC + Bedrock credentials

The `_env_api_keys.py` table omits the Google Vertex Application
Default Credentials branch and the Amazon Bedrock AWS credential
discovery branch. These belong to the owning adapter and ship when
those adapters do (per the binding spec §B).

## Related

- ADR-0045 — Provider Adapter Interface.
- ADR-0047 — OpenAI Completions adapter.
- ADR-0049 — Message dataclass extensions (provenance trio + `ThinkingContent`).
- ADR-0050 — Phase 4.2 strict superset closure (P-50-followup tracked).

## Phase

Sprint 6b / Phase 4.2 (shipped — Anthropic retrofit deferred to Sprint
6d per ADR-0050 §Carry-forward).
