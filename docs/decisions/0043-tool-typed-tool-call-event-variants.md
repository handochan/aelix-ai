# ADR-0043 — Tool-Typed ToolCallEvent Variants

Status: **Accepted** (Sprint 5b shipped, 2026-05-17)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## 1. Context (P-31)

Pi `coding-agent/src/core/extensions/types.ts:771-940` ships a
discriminated union of 8 tool-typed `ToolCallEvent` variants
(`BashToolCallEvent` / `ReadToolCallEvent` / `EditToolCallEvent` /
`WriteToolCallEvent` / `GrepToolCallEvent` / `FindToolCallEvent` /
`LsToolCallEvent` + `CustomToolCallEvent` fallback) plus symmetric 8
`ToolResultEvent` variants. Pi exposes `isToolCallEventType(name, event)`
runtime narrow + per-tool `isBashToolResult` etc.

## 2. Decision

- Add 8 frozen-dataclass subclasses on `ToolCallHookEvent` (7 known
  literal `tool_name` defaults + `CustomToolCallHookEvent`).
- Add symmetric 8 subclasses on `ToolResultHookEvent`.
- Add factory `make_tool_call_event(...)` / `make_tool_result_event(...)`
  that dispatches on `tool_name`. Construction sites
  (`_before_tool_call_bridge`, `_after_tool_call_bridge`) switch to the
  factory.
- Add `is_tool_call_event_type(name, event)` / `is_tool_result_event_type`
  runtime narrows (Pi parity).
- Add `BUILTIN_TOOL_NAMES = frozenset({"bash","read","edit","write","grep","find","ls"})`.

## 3. Why subclasses (not Union[type])

- Python `match event:` + `isinstance` narrows cleanly.
- Preserves `isinstance(evt, ToolCallHookEvent)` for existing handlers.
- Existing reducers (`_reducer_tool_call`, `_reducer_tool_result`) operate
  on the base class — no reducer changes.

## 4. Aelix-additive divergence (args typing)

Pi's TS narrows `input: BashToolInput` (TypedDict) via the schema
generic. Python's type system can't propagate `Static<BashSchema>` →
narrowed dict. Aelix keeps `args: dict[str, Any]` everywhere; the
discriminator is the runtime `tool_name` literal. Documented as
Aelix-additive divergence.

## 5. Backward compatibility

- `ToolCallHookEvent(...)` base constructor still works — existing tests
  pass unchanged.
- `isinstance(evt, ToolCallHookEvent)` matches all 8 subclasses.
- No new `@overload`s on `ExtensionAPI.on("tool_call", ...)` — narrowing
  happens in the handler body via `isinstance` / `is_tool_call_event_type`
  (Pi parity — Pi narrows in handlers via `isToolCallEventType` switching).

## 6. Test fixture pin

`tests/pi_parity/fixtures/pi_tool_call_event_variants_734e08e.json` —
locked 8-variant roster + Pi narrow helper names.
