# 0048. Pi Shared Utilities Ported (`_transform_messages.py`, `_sanitize_unicode.py`, `_streaming_json.py`, `_env_api_keys.py`)

Status: Accepted (Sprint 6b / Phase 4.2 shipped). Amended 2026-11-25 (#791): the Anthropic adapter calls `sanitize_surrogates`, which now joins a split surrogate pair.

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

## Note (2026-11-25, #791): the Anthropic adapter sanitizes, and a split pair is kept

The `anthropic-messages` adapter called `sanitize_surrogates` nowhere. The SDK
encodes the request body as UTF-8, so one lone surrogate in a tool result, a
user or assistant text, the system prompt, a thinking block or a tool call's
arguments ended the turn in `UnicodeEncodeError` before the request left
(measured on `6ef1a4b6` by the seven cases of
`test_a_lone_half_is_dropped_and_the_request_reaches_the_transport`, signed
and unsigned thinking apart). The text stays in the history that every later
request of the session replays.

`_anthropic_transforms.py` now sanitizes the text of user, assistant and
tool-result blocks, the system prompt, thinking text (signed, or sent as text
when it has no signature) and a tool call's arguments, keys and nested strings
included. Pi's `anthropic-messages.ts` (read at `c11a73bfb`) sanitizes all of
these but the arguments, and sends a signed block's text sanitized with its
signature unchanged; the adapter does the same. A signature and a redacted
block's `data` are opaque and sent as they are, as in pi. The arguments are a
divergence: pi sends them as they are, and in JavaScript `JSON.stringify` escapes a lone half, so pi's
request is still built. Here the stream parser (`json.loads`) turns a lone
escape in a model's arguments into a lone surrogate, and the SDK cannot encode
the body. Tool names and tool definitions are sent as they are, as in pi: a
name is an identifier, and removing a half could turn it into another tool's
name. A definition is the tool author's text, and an MCP server's cannot carry
a lone half at all: `mcp` 1.30.0 refuses to parse a `tools/list` reply that
holds one (`JSONRPCMessage.model_validate_json`, which its stdio, SSE and
Streamable HTTP clients use).
`tests/providers/test_anthropic_lone_surrogates_791.py` sends each case through
the adapter's own client to an `httpx.MockTransport` that records the body and
refuses the connection.

`sanitize_surrogates` used to drop every code point in U+D800..U+DFFF. A
Python string can hold a high half followed by its low half as two code points,
when the halves arrived as separate escapes. Pi's regex keeps such a pair,
because in JavaScript the pair is the character. The helper now applies pi's
regex and joins each pair that is left into the one code point it encodes. Its
output matches pi's regex, run on UTF-16 code units, for all 1,555 strings of up
to four pieces drawn from a letter, an emoji and four surrogate halves
(`tests/providers/test_sanitize_unicode.py`; the test's reference was checked
against node running pi's regex on the same 1,555). The OpenAI Completions,
OpenAI Responses, Codex and Google adapters call the same helper, so they too
now keep a split pair as the character where they dropped it. That is measured
on the helper only; their own tests were not extended.
