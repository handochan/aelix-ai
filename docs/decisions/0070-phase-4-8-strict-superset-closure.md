# 0070. Phase 4.8 Strict Superset Closure

Status: Accepted (Sprint 6h₁ / Phase 4.8 / W6 shipped)

## Context

ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055 / 0058 / 0063 / 0066 /
0068 established the Aelix strict-Pi-parity-superset invariant for
Phases 2.1 / 2.2 / 3 / 4.1 / 4.2 / 4.3 / 4.4 / 4.5 / 4.6 / 4.7. Each
closure ADR pins a regression-guard test under `tests/pi_parity/`
that asserts every Pi-verified surface in scope has a corresponding
binding in Aelix, OR sits in a deferred allowlist with an owning
ADR.

Sprint 6h₁ lands the prompt-templates + skills harness loaders +
`get_commands` RPC handler (ADR-0069). The W4 code review (APPROVE
with 4 MEDIUM + 3 LOW + 2 NITS) + W5 Pi parity audit produced
**3 BLOCKING + 1 MAJOR + several MINOR/INFO** drift findings;
Sprint 6h₁ W6 applied the must-fix triage in 5 atomic commits.

Closure date: **2026-05-20**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

The Phase 4.8 strict-superset closure pin is
`tests/pi_parity/test_phase_4_8_strict_superset.py`. It asserts the
Sprint 6h₁ W2 + W6 roster (P-216..P-244) PLUS the cumulative
invariant from ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055 / 0058 /
0063 / 0066 / 0068.

### Closure invariant

```python
# get_commands moved from deferred → supported (Sprint 6h₁ Phase 4.8):
from aelix_coding_agent.rpc.rpc_mode import DEFERRED_COMMANDS, SUPPORTED_COMMANDS
"get_commands" in SUPPORTED_COMMANDS
"get_commands" not in DEFERRED_COMMANDS
len(SUPPORTED_COMMANDS) == 13
len(DEFERRED_COMMANDS) == 16

# Prompt-templates surface (P-216):
from aelix_agent_core.harness.prompt_templates import (
    PromptTemplate, PromptTemplateDiagnostic, PromptTemplateDiagnosticCode,
    LoadPromptTemplatesResult,
    load_prompt_templates, parse_command_args, substitute_args,
    format_prompt_template_invocation,
)

# Skills surface (P-217):
from aelix_agent_core.harness.skills import (
    Skill, SkillDiagnostic, SkillDiagnosticCode, LoadSkillsResult,
    load_skills, format_skill_invocation,
)

# ExtensionRunner aggregation surface (P-220):
from aelix_agent_core.harness._extension_runner import ExtensionRunner, ResolvedCommand
runner = ExtensionRunner()
runner.get_registered_commands() == []

# ResolvedCommand carries disambiguated invocation_name + source_info
# (W6 P-224 / P-229 BLOCKING):
ResolvedCommand.__dataclass_fields__.keys() == {"command", "invocation_name", "source_info"}

# ExtensionSourceInfo carries Pi {path, scope, origin} (W6 P-225 BLOCKING):
from aelix_coding_agent.extensions.api import ExtensionSourceInfo
info = ExtensionSourceInfo(source="project")
info.scope == "user"
info.origin == "top-level"

# PromptTemplate description default "" (W6 P-226):
from aelix_agent_core.harness.prompt_templates import PromptTemplate
PromptTemplate(name="bare").description == ""

# Shared _frontmatter parser (W6 W4 m4):
from aelix_agent_core.harness import _frontmatter
callable(_frontmatter.parse_frontmatter)

# Pi name regex (W6 P-227 fixture correction):
from aelix_agent_core.harness.skills import _NAME_REGEX
_NAME_REGEX.pattern == r"^[a-z0-9-]+$"
```

### Roster (Sprint 6h₁ W2 + W6)

#### W0 binding-spec findings (P-216..P-223)

| Finding | Subject | Resolution |
|---|---|---|
| **P-216** | Pi `prompt-templates.ts` is ~380 LOC | Ported verbatim to `prompt_templates.py` (ADR-0069) |
| **P-217** | Pi `skills.ts` is ~540 LOC with `.gitignore`/`.ignore`/`.fdignore` | Ported verbatim to `skills.py` (ADR-0069) — `pathspec` for ignore matching |
| **P-218** | Pi `RpcSlashCommand = {name, description?, source, sourceInfo}` | Existing `RpcSlashCommand` dataclass already matches (Sprint 6d) |
| **P-219** | Pi `get_commands` aggregates 3 sources | `_handle_get_commands` ports the handler verbatim |
| **P-220** | `ExtensionRunner.get_registered_commands()` did not exist | Added (Sprint 6h₁ W2) |
| **P-221** | `ExtensionSourceInfo.identifier` optional | Added Sprint 6h₁ W2 (W6 P-225 also adds `path` / `scope` / `origin`) |
| **P-222** | PyYAML + pathspec mature pure-Python deps | Added to `aelix-agent-core` pyproject |
| **P-223** | Arg substitution placeholders (`$1`, `$@`, `$ARGUMENTS`, `${@:N}`, `${@:N:L}`) | All 5 ported in `substitute_args` |

#### W4 code review + W5 Pi parity audit (P-224..P-244)

| Finding | Severity | Subject | Resolution |
|---|---|---|---|
| **P-224** | BLOCKING | `ExtensionRunner.get_registered_commands()` missing Pi disambiguation | Returns :class:`ResolvedCommand` with `invocation_name` Pi-disambiguated via `{name}:{N}` |
| **P-225** | BLOCKING | `RpcSlashCommand.sourceInfo` wire shape Pi `{path, source, scope, origin}` ≠ Aelix `{type, identifier}` | Rewrote 3 source synthesisers in `rpc_mode.py` to emit Pi shape; extended `ExtensionSourceInfo` with `path` / `scope` / `origin` |
| **P-229** | BLOCKING | `RegisteredCommand.source_info` field missing | :class:`ResolvedCommand` forwards owning extension's `source_info` at resolution time |
| **P-226** | MAJOR | `PromptTemplate.description` non-optional default | `description: str = ""` + `content: str = ""` |
| **P-227** | MINOR | Skill name regex fixture text wrong | Fixed `pi_get_commands_734e08e.json` to match actual Pi regex |
| **P-233** | MINOR | YAML parse error message swallowed | `_frontmatter.parse_frontmatter` returns 3-tuple with error text; both loaders surface it in `parse_failed` diagnostic |
| **P-234** | MINOR | Case-insensitive `.md` extension strip | `name.lower().endswith(".md")` in `_load_template_from_file` |
| **W4 m1** | LOW | `_prompt_template_source_info` returns None silently | Covered by P-225 — synthesiser now emits Pi-shape with fallback fields |
| **W4 m2** | LOW | Tautological test | Replaced with real sentinel: integer `1` is truthy but `is True` is False |
| **W4 m4** | LOW | Duplicated `_parse_frontmatter` | Extracted to `aelix_agent_core.harness._frontmatter` |
| **W4 LOW-1..LOW-3** | NIT | Identifier vs source dir / imports / pathspec deprecation | Deferred (cosmetic / future pathspec 0.13 cutover) |
| **W4 NIT-1..NIT-2** | NIT | Cosmetic | Deferred |

### Carry-forward — Sprint 6h₂ / 6h₃

| Item | Owner |
|---|---|
| 16 remaining RPC commands (steer / follow_up / cycle_thinking_level / queue modes / auto modes / abort_bash / session inspection / session tree / extension UI bridge) | Sprint 6h₂ |
| Workspace-scoped model selection (`cycle_model.isScoped: true` path) | Sprint 6h₂ |
| `applyProviderConfig` for `register_provider.config.models` | Sprint 6h₂ |
| `enableGitHubCopilotModel` POST automation | Sprint 6h₂ |
| `loadSourcedPromptTemplates` / `loadSourcedSkills` source-tagged variants | Sprint 6h₂ |
| `image-models.ts` + `image-models.generated.ts` parallel image registry | Sprint 6h₃ |
| Typed `Model.compat` discriminated union (`OpenAICompletionsCompat \| OpenAICodexResponsesCompat \| …`) | Sprint 6h₃ |
| `chalk`-colored CLI output | Phase 5 TUI |
| W4 m3 (unbounded recursion under filesystem loops — Pi has the same behaviour; Aelix matches) | Tracked / no action |
| W4 LOW-3 (pathspec `gitwildmatch` deprecation — switch to `gitignore` flavour when pathspec 0.13 lands) | Tracked |
| P-228 (skill name length cap 64 — confirmed correct, no change) | Closed |
| P-230 (Windows path normalisation — Linux-target sprint) | Tracked |
| P-235 (auto-bootstrap deferred — documented) | Sprint 6h₂ |
| P-240 (ignore-matcher per-frame rebuild — functionally equivalent to Pi) | Closed |
| P-244 (YAML 1.1 vs 1.2 boolean drift — surface only on sentinel words) | Tracked |
| P-231 / P-232 / P-236 / P-237 / P-238 / P-239 INFO no drift | Closed |

## Consequences

- ADR-0034 amended with the Sprint 6h₁ row (this sprint's pin SHA
  reference + 13 supported / 16 deferred / 29 total RPC envelope).
- Sprint 6d (Phase 4.4) + Sprint 6f (Phase 4.6) closure pins
  strengthened to reflect the new 13/16 split (was 12/17). Both pins
  already documented the Sprint 6h₁ delta during W2.
- `tests/pi_parity/test_phase_4_8_strict_superset.py` adds 22+ new
  regressions covering every W6 fix; tightens the Pi parity wall so
  the next sprint's drift trips mechanically.
- ADR-0069 documents the Pi parity port + the design decisions on
  shared `_frontmatter` helper, `ResolvedCommand` attaches
  `source_info` at resolution time, and Aelix divergences
  (`ExecutionEnv` skip, `loadSourcedPromptTemplates` deferral).
- Sprint 6h₂ inherits the 16-RPC backlog + workspace-scoped model
  selection + `applyProviderConfig` + `loadSourced*` variants.

## Alternatives considered

- **Split the 5 atomic commits into 6 (separating `_frontmatter` from
  the loaders)**: rejected — `_frontmatter` is consumed by both
  loaders in the same drop; landing it separately would require a
  no-op commit. The spec's 5-commit shape keeps the relationship
  visible.
- **Treat W4 m3 (unbounded recursion) as in-scope**: rejected — Pi
  has the same behaviour; matching Pi is the goal. Filesystem loops
  on extension/skill discovery are exotic enough that the cost of
  a stack-bounded re-implementation is unjustified before a real
  bug report.
- **Land prompt-templates / skills loaders without `get_commands`**:
  rejected — the spec's stated goal is to drain `get_commands` from
  the deferred set, and the two loaders are dead code without the
  consuming RPC handler.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6h₁).
- ADR-0058 — Phase 4.4 closure (RPC `DEFERRED_COMMANDS` allowlist).
- ADR-0066 — Phase 4.6 closure (Sprint 6f model RPC commands).
- ADR-0068 — Phase 4.7 closure (predecessor).
- ADR-0069 — Prompt-templates + skills + `get_commands` port.

## Phase

Sprint 6h₁ / Phase 4.8 / W6 (shipped — Phase 4.8 strict superset
closure pin + P-216..P-244 roster + 22 new W6 regression tests).
