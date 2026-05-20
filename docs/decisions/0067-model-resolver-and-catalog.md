# 0067. Model Resolver Port + Full Pi Catalog Data Transfer

Status: Accepted (Sprint 6g₁ / Phase 4.7 / W6 shipped)

## Context

ADR-0064 / 0065 closed the per-model field shape + ModelRegistry
runtime layers in Sprint 6f. The Sprint 6f₁ seed catalog
(`models_generated.py`, 13 models / 3 providers) and `find_model_with_provider`
deliberately deferred two next-layer Pi surfaces:

1. **`coding-agent/src/core/model-resolver.ts`** — 637 LOC of resolution
   policy: 7 public functions (`findExactModelReferenceMatch`,
   `parseModelPattern`, `resolveModelScope`, `resolveCliModel`,
   `findInitialModel`, `restoreModelFromSession`, plus the
   `defaultModelPerProvider` 32-row map) + 3 internal helpers
   (`isAlias`, `tryMatchModel`, `buildFallbackModel`). This layer is
   what threads CLI flags / saved-session state / glob-pattern scoping
   onto the registry.
2. **`packages/ai/src/models.generated.ts`** — the FULL 16,386-line
   Pi catalog: 32 providers, **942 models**, each with 11–13 fields
   including the optional per-API `compat` discriminated union.

Sprint 6g₁ binding spec §0 produced 8 W0 findings (P-197..P-204).
W4 code review + W5 Pi parity audit produced **1 BLOCKING + 5 MAJOR +
4 MINOR** drift findings — applied in Sprint 6g₂ W6.

## Decision

### Model resolver port

`packages/aelix-coding-agent/src/aelix_coding_agent/core/model_resolver.py`
ports Pi `model-resolver.ts:1-637` verbatim:

| Symbol | Pi source | Aelix port |
|---|---|---|
| `defaultModelPerProvider` (32 rows) | `:14-47` | `DEFAULT_MODEL_PER_PROVIDER` |
| `ScopedModel` | `:49-53` | `ScopedModel` (frozen dataclass) |
| `isAlias` | `:59-66` | `_is_alias` |
| `findExactModelReferenceMatch` | `:73-115` | `find_exact_model_reference_match` |
| `tryMatchModel` | `:121-151` | `_try_match_model` (private) |
| `ParsedModelResult` | `:153-158` | `ParsedModelResult` (frozen) |
| `buildFallbackModel` | `:160-174` | `_build_fallback_model` (private) |
| `parseModelPattern` | `:189-242` | `parse_model_pattern` |
| `resolveModelScope` | `:255-313` (async) | `resolve_model_scope` (async) |
| `ResolveCliModelResult` | `:315-324` | `ResolveCliModelResult` (frozen) |
| `resolveCliModel` | `:337-467` | `resolve_cli_model` |
| `InitialModelResult` | `:469-473` | `InitialModelResult` (frozen) |
| `findInitialModel` | `:483-563` (async) | `find_initial_model` (async) |
| `restoreModelFromSession` return shape | `:574,599,629,636` | `RestoreModelResult` (frozen, W6 P-206) |
| `restoreModelFromSession` | `:568-637` (async) | `restore_model_from_session` (async) |

External-dep translation:

- Pi `minimatch(haystack, glob, {nocase: true})` → `_glob_match_pi_minimatch`
  (W6 P-207 — per-segment `fnmatch.fnmatchcase` after `.casefold()`,
  rejects mismatched segment counts so `*` cannot cross `/`).
- Pi `isValidThinkingLevel` → `aelix_coding_agent.core.defaults.is_valid_thinking_level`.
- Pi `DEFAULT_THINKING_LEVEL` → `aelix_coding_agent.core.defaults.DEFAULT_THINKING_LEVEL`
  (`"medium"`, W6 P-205 — was incorrectly `"off"` in the W1 spec draft).
- Pi `chalk` → plain text via `sys.stderr` / `sys.stdout`. Sprint 6h
  or Phase 5 TUI wires the colored variant.
- Pi `process.exit(1)` → `sys.exit(1)` in the CLI failure branch.
- Pi `ModelRegistry` → `aelix_coding_agent.model_registry.ModelRegistry`.

### Full Pi catalog data transfer

`packages/aelix-ai/src/aelix_ai/models_generated.json` ships the full
Pi catalog data (32 providers / 942 models / 11–13 fields each).
`models_generated.py` loads + deserializes into
`dict[str, dict[str, Model]]` at module import. The Sprint 6f₁ 13-model
seed is REPLACED.

Sprint 6g₂ W6 P-209 hardening: Pi-required fields (`id`, `name`, `api`,
`provider`, `baseUrl`, `reasoning`, `input`, `contextWindow`,
`maxTokens`) raise `KeyError` if missing. Genuinely optional Pi fields
(`thinkingLevelMap`, `headers`, `compat`) keep `.get(...)`.

### `KnownProvider` Literal

`aelix_ai.streaming.KnownProvider` ports Pi `types.ts:23-55` — a
32-string Literal union. Sprint 6g₂ W6 P-208 reordered the values to
match Pi's semantic grouping (first-party → OpenAI family → community
providers → self-hosted → Xiaomi family), NOT alphabetical. Closure
pin `test_known_provider_literal_order_matches_pi_types_ts` locks the
order against future drift.

### `Model.compat` passthrough

`aelix_ai.streaming.Model.compat: dict[str, Any] | None = None`
mirrors Pi's `Model.compat?: OpenAICompletionsCompat | …`
discriminated union as a dict passthrough. Sprint 6g₂ types it
properly.

**W6 P-210 wiring confirmation**:
`aelix_ai.providers._openai_compat.get_compat` (shipped Sprint 6b)
already merges `getattr(model, "compat", None)` onto the
URL/provider-detected baseline. The Sprint 6f₁ seed never populated
the field — so the override path was unreachable in production.
Sprint 6g₁ ships the full Pi catalog (which includes catalog-supplied
compat dicts on zai / vercel-ai-gateway models), so this merge path
fires for the first time on real entries. The earlier Sprint 6g₁ spec
§J text claiming "Sprint 6b OpenAI adapter does NOT read model.compat
yet" was stale at Sprint 6g₁ ship and is corrected here. Regression
coverage: `tests/providers/test_openai_compat_with_catalog.py`.

## Consequences

### Immediate

- ADR-0034 amended: Sprint 6g₁ ports model-resolver (637 LOC, 7
  functions + 3 helpers) + full 942-model JSON catalog (32 providers)
  + `KnownProvider` Literal (Pi semantic order, P-208 fix) +
  `Model.compat` passthrough (`_openai_compat.get_compat` merge
  confirmed wired, P-210). `DEFAULT_THINKING_LEVEL = "medium"` per
  Pi (P-205 fix).
- ADR-0064 amended: Sprint 6g₁ adds `compat` as the 6th additive
  Model field — total field count now 6 (`cost` / `thinking_level_map`
  / `max_tokens` / `context_window` / `headers` / `compat`).
- Sprint 6f₁ seed catalog (`>= 10 models`) closure pin still passes
  against the full 942-model catalog.
- `_openai_compat` baseline detection unchanged — catalog-supplied
  compat overrides flow through the existing Sprint 6b merge seam.

### Carry-forward — Sprint 6h / 6g₂ / 6g₃ (tracked in ADR-0068)

- Typed `Model.compat` discriminated union
  (`OpenAICompletionsCompat | OpenAICodexResponsesCompat | …`).
- `get_commands` RPC command + prompt-templates + skills surface.
- 16 remaining RPC commands (queue / session tree / extension UI
  bridge / auto modes / retry / etc.).
- `applyProviderConfig` for `register_provider.config.models`.
- `enableGitHubCopilotModel` POST automation.
- Workspace-scoped model selection (`isScoped: true` path).
- `image-models.ts` / `image-models.generated.ts` parallel registry.
- `chalk`-colored CLI output (Sprint 6h or Phase 5 TUI).
- `Model.knowledgeCutoff` / `Model.releaseDate` (Pi-untyped runtime
  additions — defer until Pi types catch up).

## Alternatives considered

- **Hand-port the 16,386-line `models.generated.ts` to Python dict
  literals**: rejected — TS object-literal syntax + per-entry
  `satisfies Model<"…">` type-cast comments would balloon to ~25 KLOC
  of generated Python. JSON is the right artifact format and Python
  `json.load` round-trips byte-equivalent.
- **Add a `wcmatch` dependency for `/`-boundary glob matching**:
  rejected — the segment-wise approach (P-207) is ~10 LOC of stdlib
  fnmatch with zero new dependency surface, and the semantics are
  identical for the patterns Pi cares about.
- **Type `Model.compat` as the discriminated union now**: rejected —
  requires the `OpenAICompletionsCompat | OpenAICodexResponsesCompat
  | …` union to be ported in full first. Sprint 6g₁ ships the
  passthrough; Sprint 6g₂ types it.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6g₁).
- ADR-0049 — Message dataclass shape (amended).
- ADR-0064 — Model field shape (amended — 6th field).
- ADR-0065 — ModelRegistry runtime (the resolver's consumer).
- ADR-0066 — Phase 4.6 closure (predecessor).
- ADR-0068 — Phase 4.7 strict superset closure (Sprint 6g₂ W6).

## Phase

Sprint 6g₁ / Phase 4.7 / W6 (shipped — model-resolver + full catalog
+ `KnownProvider` semantic order + `Model.compat` passthrough +
P-205/P-206/P-207/P-208/P-209/P-210 W6 fixes).
