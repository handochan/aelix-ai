# 0069. Prompt-Templates + Skills + `get_commands` RPC

Status: Accepted (Sprint 6h₁ / Phase 4.8 / W6 shipped)

## Context

ADR-0058 (Sprint 6d Phase 4.4 closure) shipped 9 of 29 Pi
`RpcCommand` variants and tagged the rest as deferred via
`rpc_mode.DEFERRED_COMMANDS`. ADR-0066 (Sprint 6f Phase 4.6 closure)
drained 3 of those (model commands) leaving 12 supported / 17
deferred. Sprint 6g₁ (ADR-0067 / 0068) kept the same RPC split while
landing the model resolver + full Pi catalog underneath.

The next deferred command on the critical path is `get_commands` —
the wire that aggregates three Pi-side surfaces:

1. **Extension-registered slash commands** —
   `session.extensionRunner.getRegisteredCommands()`
   (Pi `runner.ts:512-551`).
2. **Prompt templates** — `session.promptTemplates`
   (Pi `harness/prompt-templates.ts`, ~380 LOC).
3. **Skills** — `session.resourceLoader.getSkills().skills`
   (Pi `harness/skills.ts`, ~540 LOC).

Sprint 6h₁ ships the two missing harness loaders plus the
`get_commands` handler that consumes all three. The W4 code review
(APPROVE with 4 MEDIUM + 3 LOW + 2 NITS) + W5 Pi parity audit
(**3 BLOCKING + 1 MAJOR + several MINOR/INFO** drift findings)
produced the W6 must-fix triage applied in 5 atomic commits.

Closure date: **2026-05-20**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

### Prompt-templates port

`packages/aelix-agent-core/src/aelix_agent_core/harness/prompt_templates.py`
ports Pi `harness/prompt-templates.ts:1-267` verbatim:

| Symbol | Pi source | Aelix port |
|---|---|---|
| `PromptTemplate` (interface) | `types.ts` | `PromptTemplate` (frozen dataclass) |
| `PromptTemplateDiagnostic` | `:7-16` | `PromptTemplateDiagnostic` (frozen) |
| `PromptTemplateDiagnosticCode` | Literal union | `PromptTemplateDiagnosticCode` |
| `loadPromptTemplates(env, paths)` | `:30-62` | `load_prompt_templates(paths)` |
| `loadTemplatesFromDir` | `:95-121` | `_load_templates_from_dir` (private) |
| `loadTemplateFromFile` | `:123-165` | `_load_template_from_file` (private) |
| `parseCommandArgs` | `:223-246` | `parse_command_args` |
| `substituteArgs` | `:249-262` | `substitute_args` |
| `formatPromptTemplateInvocation` | `:265-267` | `format_prompt_template_invocation` |

External-dep translation:

- Pi `yaml` npm package → PyYAML (`yaml.safe_load`).
- Pi `ExecutionEnv` abstraction → :mod:`pathlib` directly. Pi's
  `ExecutionEnv` exists for browser/Node interop; Aelix is Python-only.
- Pi line-ending normalisation (`\r\n` / `\r` → `\n`) preserved.

### Skills port

`packages/aelix-agent-core/src/aelix_agent_core/harness/skills.py`
ports Pi `harness/skills.ts:1-375` verbatim:

| Symbol | Pi source | Aelix port |
|---|---|---|
| `Skill` (interface) | `types.ts` | `Skill` (frozen dataclass) |
| `SkillDiagnostic` | `:19-28` | `SkillDiagnostic` (frozen) |
| `SkillDiagnosticCode` | Literal union | `SkillDiagnosticCode` |
| `loadSkills(env, dirs)` | `:49-75` | `load_skills(dirs)` |
| `loadSkillsFromDirInternal` | `:103-175` | `_load_skills_from_dir_internal` |
| `addIgnoreRules` | `:177-213` | `_add_ignore_rules` |
| `prefixIgnorePattern` | `:215-231` | `_prefix_ignore_pattern` |
| `loadSkillFromFile` | `:233-279` | `_load_skill_from_file` |
| `validateName` | `:281-291` | `_validate_name` |
| `validateDescription` | `:293-301` | `_validate_description` |
| `formatSkillInvocation` | `:38-41` | `format_skill_invocation` |

External-dep translation:

- Pi `ignore` npm package → `pathspec>=0.12` (`gitwildmatch` flavour).
  Mirrors `.gitignore` semantics Pi relies on, pure-Python, no
  native build.
- Pi co-located `parseFrontmatter` in each loader → extracted to
  :mod:`aelix_agent_core.harness._frontmatter` (W4 m4) so the two
  modules share one source of truth.

### `get_commands` RPC handler

`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py`
adds `_handle_get_commands` (Pi `rpc-mode.ts:622-653`) aggregating
the 3 sources in Pi insertion order:

1. **Extension commands** —
   `harness.extension_runner.get_registered_commands()` returns
   :class:`ResolvedCommand` (Pi `runner.ts:1061-1067`); wire `name` ←
   `invocation_name` (Pi-disambiguated), `source="extension"`.
2. **Prompt templates** — `harness.prompt_templates`; wire
   `name` ← `template.name`, `source="prompt"`.
3. **Skills** — `harness.skills`; wire
   `name` ← `f"skill:{skill.name}"` (Pi prefix convention),
   `source="skill"`.

Drops `get_commands` from `DEFERRED_COMMANDS` and adds to
`SUPPORTED_COMMANDS`. Counts move from 12 / 17 → **13 / 16**.

### `ExtensionRunner` + `ResolvedCommand`

`packages/aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py`
adds the read-side aggregation surface:

- `ExtensionRunner.get_registered_commands() -> list[ResolvedCommand]`
  (Pi `resolveRegisteredCommands`).
- `ResolvedCommand` (frozen dataclass) wraps a
  :class:`RegisteredCommand` with the Pi-disambiguated
  ``invocation_name`` and the owning extension's
  :class:`ExtensionSourceInfo` (forwarded at resolution time per
  P-229; the registry does NOT carry source_info on
  :class:`RegisteredCommand`).

### `ExtensionSourceInfo` Pi-shape extension

Pi `source-info.ts:1-12` `SourceInfo = {path, source, scope, origin, baseDir?}`.
Sprint 5a shipped a 3-field Aelix shape (`source`, `base_dir`,
`identifier`). Sprint 6h₁ W6 (P-225) extends to all Pi fields:

- `path: str | None = None`
- `scope: Literal["user", "project", "temporary"] = "user"`
- `origin: Literal["package", "top-level"] = "top-level"`

Defaults match Pi's "sensible fallback" so existing extension callers
continue to emit a well-formed wire shape.

### Shared `_frontmatter` parser

`aelix_agent_core.harness._frontmatter.parse_frontmatter` returns a
3-tuple `(frontmatter_dict_or_None, body, error_message_or_None)`.
Both loaders consume it (W4 m4). P-233 surfaces the
`yaml.YAMLError` text in the `parse_failed` diagnostic instead of
the generic stub message.

### Harness wire

`AgentHarness` gains three public properties + two setters
(`extension_runner` / `prompt_templates` / `skills` /
`set_prompt_templates` / `set_skills`). The harness owns the
lifetime of the registries; loader calls
(`load_prompt_templates(...)` / `load_skills(...)`) are the caller's
responsibility — matches Pi where session bootstrap populates the
attributes.

## Consequences

### Immediate

- ADR-0034 amended: Sprint 6h₁ ports prompt-templates + skills +
  `get_commands` handler with Pi disambiguation (P-224) +
  Pi-shape `sourceInfo` wire (P-225) + `ResolvedCommand` source_info
  forward (P-229). `DEFERRED_COMMANDS` 17 → 16,
  `SUPPORTED_COMMANDS` 12 → 13.
- `PyYAML>=6.0` + `pathspec>=0.12` added to
  `packages/aelix-agent-core/pyproject.toml`. Both are mature,
  pure-Python (PyYAML ships prebuilt wheels), widely used.
- `ExtensionSourceInfo` gains `path` / `scope` / `origin` Pi fields
  with sensible defaults; existing callers unchanged.
- :class:`PromptTemplate.description` / :attr:`content` default to
  `""` so callers that omit either field match Pi's "optional with
  empty default" behaviour (P-226).
- Closure pin `tests/pi_parity/test_phase_4_8_strict_superset.py`
  asserts the 3-source aggregation + Pi prefix convention + Pi name
  regex + 13/16 RPC split + every W6 fix.

### Carry-forward — Sprint 6h₂ / 6h₃ (tracked in ADR-0070)

- 16 remaining RPC commands (steer / follow_up /
  cycle_thinking_level / queue modes / auto modes / abort_bash /
  session inspection / session tree / extension UI).
- Workspace-scoped model selection (`cycle_model.isScoped: true`).
- `applyProviderConfig` for `register_provider.config.models`.
- `enableGitHubCopilotModel` POST automation.
- `image-models.ts` + `image-models.generated.ts`.
- Typed `Model.compat` discriminated union.
- `loadSourcedPromptTemplates` / `loadSourcedSkills` source-tagged
  variants (Aelix harness wires sources via
  :class:`ExtensionSourceInfo` on the caller side; Sprint 6h₁ ships
  only the bare loaders).
- W4 m3 (unbounded recursion under filesystem loops — Pi has the
  same behaviour; Aelix matches).
- W4 LOW-1..LOW-3 (cosmetic / pathspec `gitwildmatch` deprecation).
- P-228 (skill name length cap 64 — confirmed correct).
- P-230 (Windows path normalisation — Linux-target sprint).
- P-231..P-239 INFO no-drift.

## Alternatives considered

- **Co-locate `parseFrontmatter` per module per Pi**: rejected — Pi
  duplicates the function in `prompt-templates.ts` and `skills.ts`
  because TypeScript modules are cheap. In Python the cost is a
  one-import-line; the shared module is the right shape (W4 m4).
- **Carry `source_info` on `RegisteredCommand` directly**: rejected —
  Pi attaches `sourceInfo` at resolution time. Putting it on the
  registry would force every `register_command` call to thread the
  owning extension's metadata through the API. The runner already
  knows the owner; let it stay the authority (P-229).
- **Emit Aelix-flavoured `{type, identifier}` `sourceInfo`**:
  rejected — Pi's wire shape is `{path, source, scope, origin}` and
  any client parsing the JSONL stream against the Pi spec breaks
  the moment we diverge. Pi byte-for-byte parity (P-225 BLOCKING).
- **Type `disable-model-invocation` as `Any` and coerce with
  `bool(...)`**: rejected — Pi's check is strict equality with
  literal `true` (`frontmatter['disable-model-invocation'] === true`).
  YAML's `yes` / `1` / `"true"` all map to truthy values that should
  NOT flip the bit. Aelix preserves Pi's strictness via
  `disable_raw is True` (W4 m2).

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6h₁).
- ADR-0058 — Phase 4.4 closure (RPC `DEFERRED_COMMANDS` allowlist).
- ADR-0066 — Phase 4.6 closure (Sprint 6f model RPC commands).
- ADR-0068 — Phase 4.7 closure (Sprint 6g₁ model resolver).
- ADR-0070 — Phase 4.8 strict superset closure (this sprint's pin).

## Phase

Sprint 6h₁ / Phase 4.8 / W6 (shipped — prompt-templates + skills +
`get_commands` handler + ResolvedCommand disambiguation +
Pi-shape `sourceInfo` wire + shared `_frontmatter` helper +
P-224 / P-225 / P-226 / P-229 / P-233 / P-234 / W4 m2 / W4 m4 fixes).
