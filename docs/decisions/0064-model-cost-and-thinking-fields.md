# 0064. Model Cost + Thinking + Headers Fields

Status: Accepted (Sprint 6f / Phase 4.6 / W6 shipped)

## Context

ADR-0049 ports the Pi `Message` dataclass family with provenance + thinking
+ image-split + tool-name fields. Sprint 6f closes the next layer — the Pi
`Model` dataclass at `pi/packages/agent/src/types.ts` exposes per-model
**rate / capacity / API-shape** metadata that the runtime ModelRegistry
(ADR-0065) hands back to adapters, the RPC `set_model` / `cycle_model` /
`get_available_models` commands, and the harness `current_model`
property.

Sprint 6a + 6b shipped only `Model.id` / `provider` / `api` / `base_url`
+ a stub `cost` placeholder. The Sprint 6f W0 binding spec produced 6
field-shape findings (P-163..P-169) plus the W6 P-178 wire-fix:

- **P-163 / P-164** — Pi `Model.cost` is a `{input, output, cacheRead,
  cacheWrite}` rate-per-million record, not a scalar. Adapters need it
  to compute per-message `UsageCost`.
- **P-165** — Pi `Model.thinkingLevelMap` is a free-form
  `Record<string, string | number | null>` mapping extended-thinking
  levels (`off` / `low` / `medium` / `high` / `max` plus provider-
  specific aliases) to provider-shape values.
- **P-166** — Pi `Model.maxTokens` + `Model.contextWindow` are
  plain integer fields read by the harness for trimming / budget.
- **P-167** — Pi `Model.headers` is `Record<string, string> | undefined`
  for provider-supplied per-request HTTP headers (e.g., Anthropic
  `anthropic-beta` flags, Copilot `editor-version`). Sprint 6f W6
  reclassified as **MAJOR (P-178)** when the rpc_mode `_model_to_dict`
  failed to thread it through.
- **P-168** — Pi `Model.cost` is a per-million **rate**; Pi `Usage.cost`
  is the **resolved** per-message cost. They share four key names but
  are NOT the same type — and Pi mutates `Usage.cost` in place (its
  `Message.toolResult.usage` aggregates costs as cache events arrive).
- **P-169** — Sprint 6a/6b shipped a placeholder named `Cost` for the
  per-million rate. Sprint 6f promotes the real type to `ModelCost`
  and keeps `Cost = ModelCost` as a back-compat alias.

## Decision

Extend `packages/aelix-ai/src/aelix_ai/streaming.py` (Model dataclass
home) + `packages/aelix-ai/src/aelix_ai/models.py` with the Pi-parity
fields. All additions are **additive** — default values preserve the
Sprint 6b shape so existing adapters keep building Model instances
unchanged.

### `ModelCost` (per-million rate; frozen)

```python
@dataclass(frozen=True)
class ModelCost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
```

`ModelCost` is **frozen** because per-model rate cards do not mutate at
runtime — they are catalog data. Mutation would silently corrupt the
shared registry.

### `Cost = ModelCost` back-compat alias

```python
Cost = ModelCost  # Sprint 6a/6b alias — do not use in new code.
```

Sprint 6a/6b callers (the Anthropic + OpenAI adapter test fixtures)
imported `Cost` directly. Keeping the alias prevents a flag-day rename
across the adapter packages; new code MUST import `ModelCost`.

### `UsageCost` (resolved per-message cost; MUTABLE)

```python
@dataclass
class UsageCost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
```

`UsageCost` is **mutable** by design — Pi mirrors this with `Usage.cost`
mutating as cache-read / cache-write deltas stream in
(`pi/packages/agent/src/streaming.ts` updates the same record across
events within one assistant turn). A frozen variant would force the
adapter to mint a new record per event, breaking the in-place update
contract.

### `Usage` dataclass

```python
@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: UsageCost = field(default_factory=UsageCost)
```

Per-message token + cost reporting. Mutable to mirror Pi.

### `Model` field additions

```python
@dataclass(frozen=True)
class Model:
    id: str = ""
    provider: str = ""
    api: KnownApi | str = ""
    base_url: str = ""
    # NEW — Sprint 6f Pi-parity fields:
    cost: ModelCost = field(default_factory=ModelCost)
    thinking_level_map: dict[str, str | int | None] | None = None
    max_tokens: int = 0
    context_window: int = 0
    headers: dict[str, str] | None = None
    # ... existing Sprint 6a/6b fields preserved ...
```

`thinking_level_map` accepts `str | int | None` values to cover Pi's
union (Anthropic uses string budget tokens, OpenAI uses integer
budget tokens, GitHub Copilot uses `null` for "passthrough").

`headers` accepts `dict[str, str] | None`. When non-None, the adapter
merges it into every request to that model. The RPC `_model_to_dict`
serializer threads it through verbatim (P-178 wire).

### `aelix_ai.models` 7 helpers

The Phase 4.6 §0 7-helper port lives in `models.py`:

1. `EXTENDED_THINKING_LEVELS` — frozen 6-value tuple
   (`off` / `minimal` / `low` / `medium` / `high` / `max`).
2. `get_all_models()` — registry access.
3. `get_models_for_provider(provider_id)` — provider filter.
4. `get_model_by_id(model_id)` — single-model lookup.
5. `find_model_with_provider(model_id)` — `(model, provider)` tuple
   resolver, partial-id matching deferred to Sprint 6g.
6. `get_default_model()` — Pi's "first model in catalog" rule.
7. `coerce_thinking_level(level)` — clamps to `EXTENDED_THINKING_LEVELS`
   with `off` fallback. Used by `cycle_model` (P-171/P-182).

## Consequences

### Immediate

- ADR-0049 amended: Sprint 6f adds **5 fields total** to Model (`cost`,
  `thinking_level_map`, `max_tokens`, `context_window`, `headers`).
- Sprint 6a/6b adapters keep working — the `Cost = ModelCost` alias is
  the seam. New code uses `ModelCost`.
- The Sprint 6b OpenAI adapter and the Sprint 6a Anthropic adapter do
  NOT populate `headers` / `thinking_level_map` / real `cost` rate
  cards in this PR — wiring is deferred to Sprint 6g paired with the
  full catalog port.
- The 13-model seed catalog (`models_generated.py`) ships real
  `ModelCost` rate cards for Anthropic Claude 4.5/4.6 + OpenAI GPT-5
  + GitHub Copilot models so the registry round-trips correctly.

### Carry-forward — Sprint 6g

- Pi `Model.compat` field (per-API union type for adapter dispatch).
- Pi `Model.knowledgeCutoff` (ISO date string).
- Pi `Model.releaseDate` (ISO date string).
- Full 428 KB Pi `models.generated.ts` → `models_generated.json` data
  transfer (Sprint 6f₁ ships 13; full catalog has ~200+).
- `image-models.ts` / `image-models.generated.ts` (Pi parallel registry
  for image-generation models).

## Alternatives considered

- **Single `Cost` frozen dataclass for both rate and resolved cost**:
  rejected — Pi mutates `Usage.cost` in place. A frozen variant would
  break the streaming update path.
- **Use `decimal.Decimal` for cost fields**: rejected — Pi uses
  JS `number` (float64). Float matches Pi byte-for-byte at the
  precision Pi exposes. Adapters that need decimal precision can
  wrap.
- **Make `Model.headers` a `dict[str, str]` with empty default**:
  rejected — `None` distinguishes "no provider-supplied headers" from
  "empty headers object", which matters for cache lookups and
  request-merge semantics.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6f).
- ADR-0049 — Message dataclass provenance + thinking (amended Sprint
  6f — 5 new Model fields).
- ADR-0065 — ModelRegistry runtime (the consumer of these fields).
- ADR-0066 — Phase 4.6 strict superset closure.

## Phase

Sprint 6f / Phase 4.6 / W6 (shipped — Model field shape final;
`compat` / `knowledgeCutoff` / `releaseDate` Pi fields deferred to
Sprint 6g per Phase 4.6 §0).
