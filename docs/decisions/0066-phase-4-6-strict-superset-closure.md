# 0066. Phase 4.6 Strict Superset Closure

Status: Accepted (Sprint 6f / Phase 4.6 / W6 shipped)

## Context

ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055 / 0058 / 0063 established
the Aelix strict-Pi-parity-superset invariant for Phases 2.1 / 2.2 / 3 /
4.1 / 4.2 / 4.3 / 4.4 / 4.5. Each closure ADR pins a regression-guard
test under `tests/pi_parity/` that asserts every Pi-verified surface in
scope has a corresponding binding in Aelix, OR sits in a deferred
allowlist with an owning ADR.

Sprint 6f lands ModelCost / UsageCost / Usage + 5 new Model fields
(ADR-0064), the ModelRegistry runtime (ADR-0065), 7 Pi-parity helpers
in `aelix_ai.models`, a 13-model seed catalog, and the 3 RPC model
commands (`set_model` / `cycle_model` / `get_available_models`). The
W4 code review + W5 Pi parity audit produced **4 BLOCKING + 6 MAJOR +
many MINOR** drift findings; Sprint 6f W6 applied the must-fix triage
in 5 atomic commits.

Closure date: **2026-05-19**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

The Phase 4.6 strict-superset closure pin is
`tests/pi_parity/test_phase_4_6_strict_superset.py`. It asserts the
Sprint 6f roster (P-163..P-187 + W4 m1..m9 + NIT-1..NIT-6) PLUS the
cumulative invariant from ADR-0039 / 0040 / 0044 / 0046 / 0050 /
0055 / 0058 / 0063.

### Closure invariant

```python
# 7 Pi helpers exposed in aelix_ai.models:
import aelix_ai.models as m
required_helpers = {
    "EXTENDED_THINKING_LEVELS",
    "get_all_models",
    "get_models_for_provider",
    "get_model_by_id",
    "find_model_with_provider",
    "get_default_model",
    "coerce_thinking_level",
}
all(hasattr(m, name) for name in required_helpers)

# EXTENDED_THINKING_LEVELS exact 6 values (Pi parity):
m.EXTENDED_THINKING_LEVELS == ("off", "minimal", "low", "medium", "high", "max")

# ModelRegistry 14 methods + 2 factory constructors:
from aelix_coding_agent.model_registry import ModelRegistry
required_methods = {
    "create", "in_memory",                                 # 2 factories
    "get_all_models", "get_models_for_provider",
    "get_model_by_id", "find_model_with_provider",         # 4 access
    "is_using_oauth", "resolve_request_auth",
    "get_provider_config",                                 # 3 auth
    "_load_models", "reload", "get_load_error",            # 3 lifecycle
    "register_provider", "unregister_provider",            # 2 dynamic
    "get_provider_display_name", "get_model_display_name", # 2 display
}
all(hasattr(ModelRegistry, n) for n in required_methods)

# DEFERRED_COMMANDS shrinks 20 → 17 (set_model / cycle_model /
# get_available_models moved to SUPPORTED):
from aelix_coding_agent.rpc.rpc_mode import DEFERRED_COMMANDS, SUPPORTED_COMMANDS
len(DEFERRED_COMMANDS) == 17
len(SUPPORTED_COMMANDS) == 12
{"set_model", "cycle_model", "get_available_models"} <= SUPPORTED_COMMANDS
{"set_model", "cycle_model", "get_available_models"} & DEFERRED_COMMANDS == set()

# Seed catalog ≥10 models across ≥3 providers:
all_models = m.get_all_models()
len(all_models) >= 10
len({mod.provider for mod in all_models}) >= 3
# Sprint 6f₁ actual: 13 models, 3 providers.

# Model.headers field present (P-178):
from aelix_ai.streaming import Model
"headers" in {f.name for f in fields(Model)}

# Sprint 6e closure preserved (_OAUTH_DEFERRED_PROVIDERS drained):
from aelix_ai.oauth import _OAUTH_DEFERRED_PROVIDERS
_OAUTH_DEFERRED_PROVIDERS == {}

# current_model reads _state.model (P-187):
# (asserted by tests/test_harness_current_model.py — Commit 1 of Sprint 6f W6)
```

### Roster (Sprint 6f)

#### W0 binding-spec findings (P-163..P-169)

| Finding | Subject | Resolution |
|---|---|---|
| **P-163** | Pi `Model.cost` is `{input, output, cacheRead, cacheWrite}` per-million rate, not scalar | `ModelCost` dataclass ports the record (ADR-0064) |
| **P-164** | `ModelCost` distinct from `UsageCost` (rate vs resolved) | `Cost = ModelCost` alias + `UsageCost` mutable variant (ADR-0064) |
| **P-165** | Pi `Model.thinkingLevelMap` is `Record<string, string | number | null>` | `thinking_level_map: dict[str, str | int | None] | None` (ADR-0064) |
| **P-166** | Pi `Model.maxTokens` + `Model.contextWindow` are plain ints | `max_tokens: int = 0` + `context_window: int = 0` (ADR-0064) |
| **P-167** | Pi `Model.headers` is `Record<string, string> | undefined` | `headers: dict[str, str] | None = None` (ADR-0064) |
| **P-168** | Pi `Usage.cost` mutates in place during streaming | `UsageCost` is **mutable** dataclass (no frozen) (ADR-0064) |
| **P-169** | Sprint 6a/6b shipped `Cost` placeholder | `Cost = ModelCost` back-compat alias (ADR-0064) |

#### W4 / W5 W6 must-fix BLOCKING (Commit 1)

| Finding | Subject | Resolution |
|---|---|---|
| **P-170** | `cycle_model` must clamp wrap-around to `len(models) <= 1` no-op | `cycle_model` returns current model unchanged when ≤1 candidate |
| **P-171** | `cycle_model` must persist `thinking_level` through wrap | Persisted via `_state.thinking_level` write; coerced via `coerce_thinking_level` |
| **P-172** | `set_model` must enforce `has_configured_auth` before swap | Raises `OAuthRequired` error when target model's provider lacks auth |
| **P-187** | `set_current_model` writes `_state.model` directly (no override layer) | Harness binding final (ADR-0065) |

#### W4 / W5 W6 must-fix MAJOR/MINOR (Commits 2-3)

| Finding | Subject | Resolution |
|---|---|---|
| **P-174** | Seed catalog needs ≥10 models / ≥3 providers | 13 models (Anthropic + OpenAI + GitHub Copilot) shipped in `models_generated.py` |
| **P-175** | `_load_error` cleared at top of every `_load_models`; multi-provider failures newline-joined | Applied in `ModelRegistry._load_models` (ADR-0065) |
| **P-176** | `is_using_oauth` trusts AuthStorage discriminator exclusively | Dropped `get_oauth_provider` extra guard (ADR-0065) |
| **P-178 (MAJOR)** | `_model_to_dict` failed to thread `Model.headers` to RPC | Added `headers` to serializer + Model field (ADR-0064) |
| **P-180** | `auth_header` is Pi-strict bool, not str | `ResolvedRequestAuth.auth_header: bool` (ADR-0065) |
| **P-184** | `asyncio.get_event_loop()` deprecated in Python 3.12 | Migrated to `asyncio.get_running_loop()` (ADR-0065) |

#### W4 / W5 W6 closure pin (Commit 4)

| Finding | Subject | Resolution |
|---|---|---|
| **P-179** | Phase 4.6 closure pin needed | `tests/pi_parity/test_phase_4_6_strict_superset.py` shipped |
| **P-181** | Sprint 6d closure pin needs update (3 commands moved live) | `test_phase_4_4_strict_superset.py` SUPPORTED 9→12, DEFERRED 20→17 |

### What ships

- `packages/aelix-coding-agent/src/aelix_coding_agent/model_registry.py`
  (~1,000 LOC) — 14-method ModelRegistry runtime + 2 factory
  constructors + `ResolvedRequestAuth` + `ProviderConfigInput`.
- `packages/aelix-ai/src/aelix_ai/streaming.py` extensions — `ModelCost`
  + `Cost` alias + `UsageCost` + `Usage` + 5 new Model fields
  (`cost`, `thinking_level_map`, `max_tokens`, `context_window`,
  `headers`).
- `packages/aelix-ai/src/aelix_ai/models.py` — 7 Pi-parity helpers
  + `EXTENDED_THINKING_LEVELS` + `coerce_thinking_level`.
- `packages/aelix-ai/src/aelix_ai/models_generated.py` (~600 LOC) — 13
  seed Model entries with real `ModelCost` rate cards.
- `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py`
  extensions — `set_model` / `cycle_model` / `get_available_models`
  handlers + `_model_to_dict` headers wire (P-178).
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`
  extensions — `current_model` property + `set_current_model` method
  (P-187) + `has_configured_auth` (P-172).
- `tests/pi_parity/test_phase_4_6_strict_superset.py` (~250 LOC)
  closure pin.
- `tests/pi_parity/fixtures/pi_model_registry_734e08e.json` Pi parity
  fixture.
- `tests/model_registry/test_model_registry.py` +
  `test_oauth_modify_models_integration.py` (~600 LOC).
- `tests/test_models.py` + `tests/test_models_generated.py` (~350 LOC).
- `tests/rpc/test_rpc_mode_set_model.py` +
  `test_rpc_mode_cycle_model.py` +
  `test_rpc_mode_get_available_models.py` +
  `test_w6_regressions_6f.py` (Commit 1, ~700 LOC).
- `tests/test_harness_current_model.py` (Commit 1, ~140 LOC).

### Forward-compat clause

Phase 4.6 closes the **ModelRegistry runtime** + Pi `Model` field
shape: the registry exposes 14 methods, the seed catalog ships 13
models across 3 providers, and the 3 RPC model commands move from
DEFERRED → live. Any future PR that:

1. Adds a new RPC command MUST land the Aelix binding in the same PR
   (or add an entry to `DEFERRED_COMMANDS` with an owning ADR).
2. Adds a new Pi `Model` field (e.g., `compat`, `knowledgeCutoff`,
   `releaseDate` carry-forward) MUST add the closure-pin assertion
   for the new field in a successor closure ADR.
3. Extends the seed catalog MUST keep `len(get_all_models()) >= 10`
   and `len(providers) >= 3` until the full Pi catalog port lands
   (Sprint 6g).

The forward-compat clauses from ADR-0039 / 0046 / 0050 / 0055 / 0058
/ 0063 continue to apply.

## Consequences

### Carry-forward — Sprint 6g

- Full Pi `models.generated.ts` catalog port (~428 KB →
  `models_generated.json` data transfer; Sprint 6f₁ ships 13 of ~200+).
- `models.json` schema validation (TypeBox port from
  `pi/packages/agent/src/types.ts` schema literals).
- `model-resolver.ts` port (~530 LOC at Pi SHA 734e08e) — partial-id
  matching + provider auto-detect + thinking-level inheritance.
- `get_commands` RPC command (extension/skill/template aggregation).
- 16 remaining RPC commands from ADR-0058 deferred set (Sprint 6d had
  20, Sprint 6f shipped 3, leaving 17 — minus 1 for `get_commands` =
  16 unique remaining behavioral surfaces; the count tracks
  `DEFERRED_COMMANDS` exactly).
- `image-models.ts` / `image-models.generated.ts` (Pi parallel
  registry for image-generation models).
- `applyProviderConfig` for `register_provider.config.models` wiring
  — paired with the full `models.json` schema.
- `enableGitHubCopilotModel` POST automation
  (`/models/{id}/policy` per Copilot OAuth login).
- Pi `Model.compat` (per-API union for adapter dispatch).
- Pi `Model.knowledgeCutoff` + `Model.releaseDate` (ISO date strings).
- Workspace-scoped model selection — Pi `cycle_model` honors
  `isScoped: true` for workspace-pinned model lists.
- **W4 m1** — `harness.auth_storage` public exposure so default
  `ModelRegistry.create()` can resolve auth without explicit
  threading.
- **W4 m2..m7 + NIT-1..NIT-6** — code-cleanup hygiene (docstring
  depth, log-line consistency, helper inlining).
- **P-177** — Sprint 6d carry (`is_streaming` Pi alignment).
- **P-183** — `register_provider.config.models` wiring (paired with
  full `models.json` schema).
- **P-185..P-196** — INFO + clarification carry-forward.

### Immediate consequences

- Sprint 6f closes the ModelRegistry runtime: every Pi-supported
  registry surface that does not require the full catalog port has
  an Aelix binding. The deferred-allowlist invariant enforces this
  — a future PR that adds a Pi RPC command but forgets the Aelix
  binding mechanically trips the closure pin.
- The 3 RPC model commands (`set_model` / `cycle_model` /
  `get_available_models`) are live; clients can drive model
  selection via JSONL.
- The Sprint 6e `modify_models` Protocol callback (ADR-0059 / 0061)
  now has a real registry consumer. Copilot OAuth login can mutate
  the catalog via the post-login callback.
- The Pi `Model` dataclass shape gains 5 new fields with full
  serialization parity; the OpenAI + Anthropic adapters keep
  working without modification.
- `_state.model` is the single source of truth for current model
  selection (P-187); RPC and CLI paths converge on it.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6f — ModelRegistry
  runtime shipped; full catalog + model-resolver + get_commands +
  16 RPC commands deferred to Sprint 6g).
- ADR-0049 — Message dataclass provenance (amended Sprint 6f — 5
  new Model fields).
- ADR-0058 — Phase 4.4 strict superset closure (3 deferred commands
  now live; updated closure pin).
- ADR-0063 — Phase 4.5 strict superset closure (the Sprint 6e
  partition this ADR closes via ModelRegistry runtime delivery).
- ADR-0064 — Model cost + thinking + headers fields.
- ADR-0065 — ModelRegistry runtime.

## Phase

Sprint 6f / Phase 4.6 / W6 (shipped — closure pin Green; 14
ModelRegistry methods live; 13-model seed catalog live;
3 RPC model commands live; full catalog + model-resolver +
get_commands + 16 RPC commands deferred to Sprint 6g per Phase 4.6
§0).
