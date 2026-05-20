# 0068. Phase 4.7 Strict Superset Closure

Status: Accepted (Sprint 6g₁ / Phase 4.7 / W6 shipped)

## Context

ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055 / 0058 / 0063 / 0066
established the Aelix strict-Pi-parity-superset invariant for Phases
2.1 / 2.2 / 3 / 4.1 / 4.2 / 4.3 / 4.4 / 4.5 / 4.6. Each closure ADR
pins a regression-guard test under `tests/pi_parity/` that asserts
every Pi-verified surface in scope has a corresponding binding in
Aelix, OR sits in a deferred allowlist with an owning ADR.

Sprint 6g₁ lands the model resolver port (ADR-0067 — 7 functions + 3
helpers + `defaultModelPerProvider` 32-row map + `Model.compat`
passthrough), the full 942-model Pi catalog data transfer, and the
32-string `KnownProvider` Literal in Pi semantic order. The W4 code
review (APPROVE) + W5 Pi parity audit produced **1 BLOCKING + 5
MAJOR + 4 MINOR** drift findings; Sprint 6g₂ W6 applied the must-fix
triage in 5 atomic commits.

Closure date: **2026-05-20**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

The Phase 4.7 strict-superset closure pin is
`tests/pi_parity/test_phase_4_7_strict_superset.py`. It asserts the
Sprint 6g₁ + Sprint 6g₂ W6 roster (P-197..P-215) PLUS the cumulative
invariant from ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055 / 0058 /
0063 / 0066.

### Closure invariant

```python
# DEFAULT_MODEL_PER_PROVIDER has exactly 32 entries:
from aelix_coding_agent.core.model_resolver import DEFAULT_MODEL_PER_PROVIDER
len(DEFAULT_MODEL_PER_PROVIDER) == 32

# KnownProvider Literal in Pi SEMANTIC order (not alphabetical):
import typing
from aelix_ai.streaming import KnownProvider
list(typing.get_args(KnownProvider))[0:5] == [
    "amazon-bedrock", "anthropic", "google", "google-vertex", "openai"
]
len(typing.get_args(KnownProvider)) == 32

# DEFAULT_THINKING_LEVEL == "medium" (W6 P-205 BLOCKING fix):
from aelix_coding_agent.core.defaults import DEFAULT_THINKING_LEVEL
DEFAULT_THINKING_LEVEL == "medium"

# Model.compat present + default None:
from dataclasses import fields
from aelix_ai.streaming import Model
"compat" in {f.name for f in fields(Model)}
Model().compat is None

# Catalog: 32 providers, 942 models present (Sprint 6g₁ ships full Pi):
from aelix_ai.models_generated import MODELS
len(MODELS) == 32
sum(len(v) for v in MODELS.values()) == 942
"claude-opus-4-7" in MODELS["anthropic"]

# RestoreModelResult typed dataclass present (W6 P-206 fix):
from aelix_coding_agent.core.model_resolver import RestoreModelResult
import dataclasses
dataclasses.is_dataclass(RestoreModelResult)
RestoreModelResult.__dataclass_params__.frozen

# _glob_match_pi_minimatch enforces /-boundary (W6 P-207 fix):
from aelix_coding_agent.core.model_resolver import _glob_match_pi_minimatch
_glob_match_pi_minimatch("claude-sonnet-4-5", "*sonnet*")        # True (1 vs 1)
not _glob_match_pi_minimatch("anthropic/x", "*sonnet*")          # 2 vs 1 — reject

# models_generated fail-fast on missing required fields (W6 P-209 fix):
# _load_catalog raises KeyError when an entry omits a Pi-required field.

# get_compat merges catalog Model.compat (W6 P-210 wiring confirm):
from aelix_ai.providers._openai_compat import get_compat
zai_glm5v = MODELS["zai"]["glm-5v-turbo"]
get_compat(zai_glm5v).zai_tool_stream is True
```

### Roster (Sprint 6g₁ + 6g₂)

#### W0 binding-spec findings (P-197..P-204)

| Finding | Subject | Resolution |
|---|---|---|
| **P-197** | Pi `models.generated.ts` is 16,386 lines / 32 providers | JSON data transfer (ADR-0067) — 942 models / `models_generated.json` |
| **P-198** | Pi `model-resolver.ts` is 637 LOC (NOT 439 — W6 P-215 corrected), 7 public + 3 private helpers | Ported verbatim to `model_resolver.py` (ADR-0067) |
| **P-199** | `KnownProvider` is a 32-string Literal union | `aelix_ai.streaming.KnownProvider` (ADR-0067) — Pi semantic order per W6 P-208 |
| **P-200** | Pi `Model.compat` is per-API discriminated union | `Model.compat: dict[str, Any] \| None` passthrough (ADR-0064 / 0067) |
| **P-201** | Pi `isValidThinkingLevel` lives in `cli/args.ts` | Ported to `aelix_coding_agent.core.defaults.is_valid_thinking_level` |
| **P-202** | Pi `DEFAULT_THINKING_LEVEL` is `"medium"` (W6 P-205 corrected; the W1 spec draft said `"off"`) | `aelix_coding_agent.core.defaults.DEFAULT_THINKING_LEVEL = "medium"` |
| **P-203** | Sprint 6f₁ seed test still passes against full catalog | Confirmed — `>= 10` invariant holds with 942 |
| **P-204** | Import-time deserialization of 942 Models is negligible | Measured ~50 ms — closure pin samples known entries |

#### W4 code review + W5 Pi parity audit (P-205..P-215)

| Finding | Severity | Subject | Resolution |
|---|---|---|---|
| **P-205** | BLOCKING | `DEFAULT_THINKING_LEVEL` was `"off"` (Aelix) vs `"medium"` (Pi) | W6 Commit 1 — flipped to `"medium"`; 7 model_resolver callsites updated via symbol; tests + W0 fixture aligned |
| **P-206** | MAJOR | `restore_model_from_session` returned untyped `dict[str, ...]` | W6 Commit 1 — added `RestoreModelResult` frozen dataclass; 4 test sites updated to attribute access |
| **P-207** | MAJOR | `fnmatch.fnmatchcase` lets `*` cross `/` (Pi `minimatch` does not) | W6 Commit 1 — added `_glob_match_pi_minimatch` per-segment helper; 5 closure pin regressions |
| **P-208** | MAJOR | `KnownProvider` Literal was alphabetical (Pi is semantic) | W6 Commit 2 — reordered verbatim from Pi `types.ts:23-55`; closure pin asserts byte-equivalent order |
| **P-209** | MAJOR | `_load_catalog` silently defaulted missing Pi-required fields | W6 Commit 2 — fail-fast on Pi-required keys via direct `entry["…"]`; regression asserts `KeyError` on missing `name` |
| **P-210** | MAJOR | Spec §J said Sprint 6b adapter does NOT read `model.compat` (stale — `get_compat` already merges it) | W6 Commit 3 — ADR text corrected; zai/glm-5v-turbo + glm-4.5-air regressions assert catalog merge path |
| **P-211** | INFO | (verified informational, no drift) | DEFER to Sprint 6h carry-forward |
| **P-212** | INFO | (verified informational, no drift) | DEFER to Sprint 6h carry-forward |
| **P-213** | MINOR | `_DATE_SUFFIX_PATTERN` redundant closure-pin check | DEFER — vector tests already cover semantics |
| **P-214** | INFO | (verified correct) | DEFER to Sprint 6h carry-forward |
| **P-215** | MINOR | Spec §A LOC table + W0 fixture line refs stale | W6 Commit 5 — spec §A table `439 → 637`; 7 line numbers in W0 fixture corrected |

#### W4 NITs (deferred)

| NIT | Subject | Disposition |
|---|---|---|
| NIT-1 | `RestoredModelResult` (partial overlap with P-206) | Subsumed by P-206 — fixed |
| NIT-2 | `resolve_model_scope` async-without-await | DEFER — Pi parity intent (Sprint 6h documentation) |
| NIT-3 | `Model.compat` docstring duplication | DEFER — cosmetic |
| NIT-4 | `resolve_model_scope` async comment | DEFER — cosmetic |
| NIT-5 | `_DATE_SUFFIX_PATTERN` underscore | DEFER — established Sprint 6c pattern |

### Carry-forward (Sprint 6h / 6g₂ / 6g₃)

- Typed `Model.compat` discriminated union — Sprint 6g₂
- `get_commands` RPC command + prompt-templates + skills surface — Sprint 6g₂
- 16 remaining RPC commands (queue / session tree / extension UI bridge / auto modes / retry / etc.) — Sprint 6g₂
- `applyProviderConfig` for `register_provider.config.models` + `models.json` schema — Sprint 6g₂
- `enableGitHubCopilotModel` POST automation — Sprint 6g₂
- Workspace-scoped model selection (`isScoped: true` path) — Sprint 6g₂
- `image-models.ts` / `image-models.generated.ts` parallel registry — Sprint 6g₃
- `chalk`-colored CLI output — Sprint 6h or Phase 5 TUI
- `Model.knowledgeCutoff` / `Model.releaseDate` — defer until Pi types catch up
- W4 NIT-2..NIT-5 (cosmetic) — defer

## Consequences

- The Phase 4.7 strict-superset invariant is preserved cumulatively
  with Phase 2.1 / 2.2 / 3 / 4.1 / 4.2 / 4.3 / 4.4 / 4.5 / 4.6
  closure pins (each runs in CI on every commit).
- The closure pin asserts byte-equivalent `KnownProvider` order +
  `DEFAULT_THINKING_LEVEL = "medium"` + `Model.compat` field +
  `RestoreModelResult` shape + glob `/`-boundary semantics — drift
  in any of these breaks CI.
- Sprint 6h owners can rely on the resolver + catalog + compat merge
  wiring being byte-parity with Pi `734e08e` for the surface in
  scope.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6g₁).
- ADR-0064 — Model field shape (amended Sprint 6g₁ — `compat` field).
- ADR-0067 — Model-resolver port + catalog data transfer (paired).
- ADR-0066 — Phase 4.6 strict superset closure (predecessor).

## Phase

Sprint 6g₂ / Phase 4.7 / W6 (shipped — closure pin landed + 5 atomic
commits + ADR-0067/0068 NEW + ADR-0034/0064 amended + W0 fixture
line refs corrected).
