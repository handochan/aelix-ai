# 0049. Message Dataclass — Provenance + Thinking + Image Split + Tool Name

Status: Accepted (Sprint 6b / Phase 4.2 / W6 shipped)

## Context

W4 code review + W5 Pi parity audit produced a 5-BLOCKING / 6-MAJOR /
13-MINOR drift roster against the Sprint 6b adapter. Four of the
blocking findings root-cause at **missing message contract fields** on
the Aelix `messages.py` dataclasses:

- **P-58 / P-67** — `ThinkingContent` dataclass missing. The OpenAI
  adapter captured the reasoning-field name during streaming but had
  no place to put the parsed thinking block, so observers saw the
  `thinking_*` events but the replay assistant message couldn't carry
  them. Anthropic's adapter shipped Sprint 6a with the same gap.
- **P-61** — `ImageContent` had only `source` (data URL or base64
  string), no separate `mime_type` / `data` fields. Adapters had to
  sniff the data-URL prefix to recover the MIME type, which fails for
  externally-hosted images and forces the wire shape into a single
  mode.
- **P-68** — `AssistantMessage` had no `api` / `provider` / `model`
  provenance trio. The Pi `_transform_messages._is_same_model` triple
  check was permanently `False` against any non-None model, so every
  cross-model rewrite path ran — even when the assistant turn was
  minted by the same model.
- **P-75** — `ToolResultMessage` had no `tool_name` field. Adapters
  that need to wrap tool results with the function name (Moonshot,
  Together, Cloudflare AI Gateway) reached into the originating
  `ToolCallContent` via `getattr` defensive overkill; orphan synthesis
  in `_transform_messages._flush_synthetic` simply dropped the field.

## Decision

Extend `packages/aelix-ai/src/aelix_ai/messages.py` with the missing
Pi-parity fields. All additions are **additive** — defaults preserve
the Sprint 6a shape so existing callers (including Sprint 6a's
Anthropic adapter) keep minting messages unchanged.

### New: `ThinkingContent`

```python
@dataclass(frozen=True)
class ThinkingContent:
    thinking: str = ""
    thinking_signature: str = ""
    redacted: bool = False
    type: Literal["thinking"] = "thinking"
```

### `AssistantMessage` provenance trio

```python
@dataclass(frozen=True)
class AssistantMessage:
    content: list[TextContent | ThinkingContent | ToolCallContent] = field(...)
    stop_reason: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] | None = None
    timestamp: float | None = None
    # NEW — Sprint 6b additive (Pi parity for same-model checks).
    api: str | None = None
    provider: str | None = None
    model: str | None = None
    role: Literal["assistant"] = "assistant"
```

The OpenAI Completions adapter populates these at the output build
site:

```python
output = AssistantMessage(
    content=list(output_content),
    stop_reason=stop_reason,
    error_message=error_message,
    api=model.api,
    provider=model.provider,
    model=model.id,
)
```

### `ImageContent` mime_type + data split

```python
@dataclass(frozen=True)
class ImageContent:
    source: str = ""        # legacy data URL or base64 payload (Sprint 6a seam)
    mime_type: str = ""     # NEW — e.g. "image/png", "image/jpeg"
    data: str = ""          # NEW — raw base64 payload (no data-URL prefix)
    type: Literal["image"] = "image"
```

Adapters MUST prefer `mime_type` + `data` when `data` is non-empty;
the `source` field is the Sprint 6a back-compat seam.

### `ToolResultMessage.tool_name`

```python
@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str = ""
    content: list[TextContent | ImageContent] = field(default_factory=list)
    is_error: bool = False
    timestamp: float | None = None
    tool_name: str = ""   # NEW — Sprint 6b additive (Pi parity)
    role: Literal["toolResult"] = "toolResult"
```

`_transform_messages._flush_synthetic` populates `tool_name` from the
originating `ToolCallContent.tool_name` on orphan-result synthesis.

## Consequences

- The `ContentBlock` type alias now includes `ThinkingContent`.
- The OpenAI adapter now appends `ThinkingContent` to `output_content`
  during streaming, replacing the Sprint-6a stop-gap "emit events but
  do not append a block" pattern.
- The Sprint 6a Anthropic adapter does NOT populate `ThinkingContent`
  on its end-of-stream output in this PR — the wiring is deferred to
  Sprint 6d per ADR-0050 §Carry-forward (cross-adapter hygiene). The
  ThinkingContent class is additive so the Anthropic adapter continues
  to work without modification.
- Same for `AssistantMessage.api/provider/model` — the Anthropic
  adapter leaves them at `None`; Sprint 6d wires the population. The
  ADR-0048 `_transform_messages._is_same_model` returns `False` for
  None-provenance assistants, matching Pi's "treat unknown provenance
  as cross-model" behavior.
- Same for `ToolResultMessage.tool_name` — the Sprint 6a Anthropic
  adapter never constructed `ToolResultMessage` so no retrofit is
  needed; only the new shared `_transform_messages` orphan path
  populates the field.

## Alternatives considered

- **Subclass `AssistantMessage` for the OpenAI variant**: rejected —
  Pi treats provenance as data, not type. Subclassing would force
  every consumer to switch over the runtime type.
- **Bag-of-strings `metadata: dict[str, Any]` for provenance**:
  rejected — Pi-shape has named columns; matching them gives type
  hints + IDE autocomplete + serialization parity without an opaque
  bag.
- **Drop `source` from `ImageContent` outright**: rejected — that
  would break Sprint 6a callers. The back-compat seam stays until
  Sprint 6d cross-adapter retrofit.

## Related

- ADR-0045 — Provider Adapter Interface.
- ADR-0047 — OpenAI Completions adapter (the first consumer of the
  new fields).
- ADR-0048 — Pi shared utilities (`_transform_messages.py` uses the
  provenance trio for `_is_same_model`).
- ADR-0050 — Phase 4.2 strict superset closure (carry-forward to
  Sprint 6d for Anthropic adapter wiring).

## Phase

Sprint 6b / Phase 4.2 / W6 (shipped — Anthropic-side population
deferred to Sprint 6d per ADR-0050 §Carry-forward).
