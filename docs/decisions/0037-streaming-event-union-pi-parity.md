# 0037. Streaming Event Union (Pi Parity)

Status: Accepted (Sprint 6a / Phase 4.1 shipped — 12-variant union live)

## Sprint 6a amendment (2026-05-17, P-39 + P-39d)

12-variant union shipped:

- **8 new dataclasses**: `TextStartEvent`, `TextEndEvent`,
  `ThinkingStartEvent`, `ThinkingDeltaEvent`, `ThinkingEndEvent`,
  `ToolCallStartEvent`, `ToolCallEndEvent`, `AssistantErrorEvent`.
- **Rename**: `AssistantEndEvent` → `AssistantDoneEvent` (Pi `done`).
  Legacy `AssistantEndEvent` retained as **deprecated subclass** of
  `AssistantDoneEvent` so existing test mocks keep working without
  modification.
- **Spelling fix (P-39d SILENT DRIFT)**: `ToolCallDeltaEvent.type`
  Literal `"tool_call_delta"` → `"toolcall_delta"` (no underscore
  between `tool` and `call`, matches Pi exactly). Legacy `input_delta`
  attribute preserved as deprecated property aliasing the Pi-shaped
  `delta` field.
- **Field backfills**: `TextDeltaEvent` and `ToolCallDeltaEvent` gain
  `content_index: int = 0` + `partial: AssistantMessage = …` with safe
  defaults so legacy callers (1-arg `TextDeltaEvent(delta="hi")`) keep
  working.

The loop consumer (`loop.py:_stream_assistant_response`) was updated to
accept the new variants as `MessageUpdateEvent` projections, and to
accept both `"end"` (legacy alias) and `"done"` (Pi canonical) as
terminal-success markers. `"error"` is now a terminal-failure marker —
the adapter populates `message.stop_reason in {"aborted","error"}`
before emitting `AssistantErrorEvent`.

## Context

Aelix today:
`AssistantMessageEvent = AssistantStartEvent | TextDeltaEvent | ToolCallDeltaEvent | AssistantEndEvent`
(4 events; `packages/aelix-ai/src/aelix_ai/streaming.py:85-87`).

Pi `AssistantMessageEvent` at `packages/ai/src/types.ts:224-236` (SHA
`734e08e…`) has **12 events**:

| # | Pi type | Pi line | Aelix today |
|---|---------|---------|-------------|
| 1 | `start` | 367 | ✓ `AssistantStartEvent` |
| 2 | `text_start` | 368 | Absent |
| 3 | `text_delta` | 369 | ✓ `TextDeltaEvent` (no `content_index`) |
| 4 | `text_end` | 370 | Absent |
| 5 | `thinking_start` | 371 | Absent |
| 6 | `thinking_delta` | 372 | Absent |
| 7 | `thinking_end` | 373 | Absent |
| 8 | `toolcall_start` | 374 | Absent |
| 9 | `toolcall_delta` | 375 | ✓ `ToolCallDeltaEvent` (no `content_index`) |
| 10 | `toolcall_end` | 376 | Absent |
| 11 | `done` | 377 | Replaced by Aelix `end` (`AssistantEndEvent`) |
| 12 | `error` | 378 | Absent (Pi has typed error event) |

The gap is type-level only. The agent loop today only inspects the `end`
semantic; the 8 missing events are emitted by provider adapters and consumed
by UI/observation code that does not exist in Phase 1.x. Aelix can ship the
full 12-event union as type definitions today, with adapter emission landing
under Phase 4 alongside the provider implementations.

## Decision

**Phase 1.4 documents the full 12-event union as a design target.** The
implementation lands in two stages following the ADR-0025 minimal-shell
cadence:

1. **Phase 1.4 (this sprint):** ADR documents the target shape. The Phase
   1.4 W2 spec narrowed scope to ship only the dispatch shell (Section A) and
   the F-6 placeholder fields (Section B); the 9 new event dataclasses + the
   `end → done` rename in `streaming.py` are explicitly deferred to the
   next sprint so the W4 reviewer can audit shell vs. type-surface changes
   independently. The narrow Phase 1.4 PR keeps the diff reviewable.

2. **Phase 4 (or earlier hygiene sprint):** the 9 new dataclasses land
   alongside the first provider adapter that needs to emit them. `content_index`
   defaults are added to the existing `TextDeltaEvent` / `ToolCallDeltaEvent`
   in the same PR. `AssistantEndEvent` stays as a backward-compat alias for at
   least one minor release cycle to preserve existing tests and mock streams.

### Phase 4 target shape

```python
@dataclass(frozen=True)
class TextStartEvent:
    content_index: int = 0
    partial: AssistantMessage = field(default_factory=AssistantMessage)
    type: Literal["text_start"] = "text_start"

# TextDeltaEvent — add content_index: int = 0 field; preserve backward-compat.

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
    | AssistantEndEvent  # legacy alias kept; deprecation cycle Phase 5
)
```

### What does NOT change

- `MessageUpdateEvent.assistant_message_event` continues to carry whichever
  variant the `stream_fn` produced.
- No loop logic interprets the new events. The loop today only inspects
  `type == "end"` semantics; that path remains valid via `AssistantEndEvent`.
- The harness `_to_hook_event` projection is unaffected.

## Consequences

- Phase 4 adapter authors have the **exact target type set** with no surprises.
- Pyright will see a 13-member union after Phase 4; downstream `match`
  statements will need `case _:` exhaustive handlers (or `assert_never` per
  ADR-0030).
- Existing mock streams continue to work unchanged through Phase 1.4 and
  Phase 4 — `AssistantEndEvent` stays in the union.
- Adds approximately 9 new class definitions to `streaming.py` when wired —
  pure additive cost, no behavior risk.

### Scope reduction from spec §D.4

The Sprint 2.5 / Phase 1.4 spec (`.omc/specs/sprint-2-5-phase-1-4-spec.md`
§D.4) originally listed the 9 new dataclasses, the `content_index` field
additions to existing events, and the `end → done` rename as Phase 1.4
deliverables. The W2 implementation narrowed scope to ship only the
dispatch shell (Section A) and the F-6 placeholder fields (Section B);
the type-surface expansion is deferred to the Phase 4 adapter PR so the
W4 reviewer can audit shell vs. type-surface changes independently and
the Phase 4 author lands the new types alongside the adapter that needs
them. `AssistantEndEvent` stays in the union through Phase 4 as a
backward-compat alias for existing mock streams.

## Related

- ADR-0025 — Minimal-shell pattern (this ADR follows the same cadence).
- ADR-0030 — assert_never exhaustiveness (downstream consumer once Phase 4
  ships the expanded union).
- ADR-0038 — `stream_simple` dispatch shell (Phase 4 adapters emit the events
  defined here).

## Phase

Sprint 2.5 / Phase 1.4 (design documented; types land alongside Phase 4
provider adapters).
