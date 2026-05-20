# Sprint 6h₁ · Phase 4.8 — prompt-templates + skills + `get_commands` RPC (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-20
Pi pin (ADR-0034): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint ports Pi `agent/src/harness/prompt-templates.ts` (~380 LOC) + `agent/src/harness/skills.ts` (~540 LOC) and wires the **`get_commands` RPC handler** in `rpc-mode.ts:557-590`. After Sprint 6h₁: **`DEFERRED_COMMANDS` drops `get_commands` → 13 supported / 16 deferred** (was 12/17 after Sprint 6f). Remaining 16 RPC commands + image-models + typed compat union defer to Sprint 6h₂/h₃.

---

## §0 — W0 INVESTIGATION FINDINGS (Pi drift verified at SHA 734e08e)

### P-216 — Pi `prompt-templates.ts` is ~380 LOC, no caching, YAML-frontmatter-based

Pi exports:
- `loadPromptTemplates(env, paths) -> {templates, diagnostics}` — load .md children non-recursively from directory paths; load explicit .md files
- `loadSourcedPromptTemplates(env, inputs, mapTemplate?) -> {templates, diagnostics}` — attaches source metadata
- `parseCommandArgs(input) -> string[]` — shell-style quoted argument parser
- `substituteArgs(content, args) -> string` — placeholders: `$1`, `$@`, `$ARGUMENTS`, `${@:N}`, `${@:N:L}`
- `formatPromptTemplateInvocation(template, args, prefix?) -> string` — combine template content + substituted args
- `PromptTemplateDiagnosticCode` Literal: `"file_info_failed" | "list_failed" | "read_failed" | "parse_failed"`
- `PromptTemplateDiagnostic` interface

`PromptTemplate` shape: `{name, description, content}`.
- `name` ← filename without `.md` extension
- `description` ← frontmatter `description` OR first body line (truncated to 60 chars)
- `content` ← markdown body after frontmatter

**Decision:** Aelix ports verbatim into `packages/aelix-agent-core/src/aelix_agent_core/harness/prompt_templates.py`. Uses PyYAML for frontmatter. No `ExecutionEnv` abstraction — direct `pathlib.Path` + `open()` (Pi's ExecutionEnv exists for browser interop, irrelevant to Python).

### P-217 — Pi `skills.ts` is ~540 LOC with `.gitignore` honoring + recursive SKILL.md discovery

Pi exports:
- `formatSkillInvocation(skill, additionalInstructions?) -> string`
- `loadSkills(env, dirs) -> {skills, diagnostics}` — recursive SKILL.md scan + root `.md` files
- `loadSourcedSkills(env, inputs, mapSkill?) -> {entries, diagnostics}`
- `SkillDiagnosticCode` Literal: `"file_info_failed" | "list_failed" | "read_failed" | "parse_failed" | "invalid_metadata"`

`Skill` shape: `{name, description, content, filePath, disableModelInvocation}`.

**Validation rules:**
- `name` MUST match parent directory name
- Name length constraints
- Name character set: alphanumeric + `-` + `_` + `.`
- `description` MUST be non-empty

**Ignore files honored:** `.gitignore`, `.ignore`, `.fdignore` (with relative-path prefixing).

**Decision:** Aelix uses `pathspec>=0.12` (mature pure-Python gitignore matcher). Recursive scan uses `Path.rglob("SKILL.md")` filtered by pathspec.

### P-218 — Pi `RpcSlashCommand` schema: `{name, description?, source, sourceInfo}`

Pi `rpc-types.ts:75-84` defines:
```typescript
interface RpcSlashCommand {
  name: string;
  description?: string;
  source: "extension" | "prompt" | "skill";
  sourceInfo: SourceInfo;
}
```

Aelix Sprint 6d already has `RpcSlashCommand` dataclass at `rpc/rpc_types.py` and `RpcClient.get_commands()` method at `rpc/rpc_client.py:363`. **No new types to add.**

### P-219 — Pi `get_commands` handler aggregates 3 sources

Pi `rpc-mode.ts:557-590`:
```
extension commands: session.extensionRunner.getRegisteredCommands() → name=command.invocationName, source="extension"
prompt templates:   session.promptTemplates                         → name=template.name, source="prompt"
skills:             session.resourceLoader.getSkills().skills       → name=f"skill:{skill.name}", source="skill"
```

**Decision:** Aelix `_handle_get_commands` in `rpc_mode.py`:
1. Iterate `harness.extension_runner.get_registered_commands()` (NEW Sprint 6h method on AgentHarness — surfaces commands from loaded Extensions)
2. Iterate `harness.prompt_templates` (NEW attribute — list[PromptTemplate])
3. Iterate `harness.skills` (NEW attribute — list[Skill])

The `harness.prompt_templates` and `harness.skills` are populated by the harness loader at session bootstrap. Sprint 6h₁ ships the loader hooks + the `_handle_get_commands` handler; the actual loading happens when callers invoke `harness.load_prompt_templates(paths)` / `harness.load_skills(dirs)`.

### P-220 — Aelix `ExtensionRunner.get_registered_commands()` does not exist; needs harness surface

Aelix Sprint 5a shipped `Extension` dataclass with `ExtensionAPI.register_command()` but did not expose a registry. Sprint 6h₁ adds:
- `AgentHarness.extension_runner: ExtensionRunner` (NEW class)
- `ExtensionRunner.get_registered_commands() -> list[ExtensionCommand]` — returns aggregated registered commands across all loaded Extensions

The actual command registration mechanism is unchanged (extensions still call `api.register_command(...)`). The runner just provides the read-side aggregation needed by `get_commands`.

### P-221 — `RpcSlashCommand.sourceInfo` requires `SourceInfo` shape with `path, type, owning_identifier`

Pi `SourceInfo` shape (from rpc-types.ts inspection): `{type: "extension"|"prompt"|"skill", path: string, identifier?: string}`. Aelix already has `ExtensionSourceInfo` dataclass at `extensions/api.py:481` (Pi `SourceInfo` minimal port — Sprint 5a P-27). **Decision:** Reuse `ExtensionSourceInfo` for all 3 sources (rename to `SourceInfo` alias or extend the dataclass with optional `identifier` field).

### P-222 — PyYAML + pathspec are mature Python deps with no native build

- `PyYAML>=6.0` — pure C with prebuilt wheels for all platforms
- `pathspec>=0.12` — pure Python, no compile step

**Decision:** Add both to `packages/aelix-agent-core/pyproject.toml` dependencies.

### P-223 — Arg substitution placeholders are POSIX-shell-style

Pi `substituteArgs(content, args)` handles:
- `$1`, `$2`, ... positional
- `$@` — all args joined with spaces
- `$ARGUMENTS` — same as `$@`
- `${@:N}` — args from index N to end
- `${@:N:L}` — args from index N, length L

**Decision:** Aelix port uses `re` module for placeholder matching. Test cases mirror Pi unit tests (TBD — fetch Pi test file in W2 if needed).

---

## §A — Scope (binding)

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_agent_core/harness/prompt_templates.py` (NEW — Pi port of prompt-templates.ts) | ~250 | ~180 |
| `aelix_agent_core/harness/skills.py` (NEW — Pi port of skills.ts) | ~300 | ~200 |
| `aelix_agent_core/harness/_extension_runner.py` (NEW — `ExtensionRunner.get_registered_commands()`) | ~50 | ~60 |
| `aelix_agent_core/harness/core.py` (AMEND — wire `extension_runner` / `prompt_templates` / `skills` attributes) | ~40 | ~40 |
| `aelix_coding_agent/rpc/rpc_mode.py` (AMEND — `_handle_get_commands` + drop from DEFERRED_COMMANDS) | ~60 | ~80 |
| `aelix_coding_agent/extensions/api.py` (AMEND — `ExtensionSourceInfo` add optional `identifier` field for full Pi parity) | ~10 | ~20 |
| Pi parity closure pin (`test_phase_4_8_strict_superset.py`) | — | ~80 |
| **Totals** | **~710** | **~660** |

**Total ~1,370 LOC** — fits Sprint 6c envelope.

### NOT in scope (deferred per §J)

- **16 remaining RPC commands** (queue / session tree / extension UI / auto modes / retry — Sprint 6h₂)
- **`image-models.ts` + `image-models.generated.ts`** (Sprint 6h₃)
- **Typed `Model.compat` discriminated union** (Sprint 6h₃)
- **Workspace-scoped model selection** (`cycle_model.isScoped: true` path — Sprint 6h₂)
- **Pi `ExecutionEnv` browser abstraction** (Aelix uses stdlib `pathlib`/`open()` — no Python equivalent needed)
- **`applyProviderConfig` for `register_provider.config.models`** (Sprint 6h₂)

---

## §B — `aelix_agent_core/harness/prompt_templates.py` (NEW)

Port Pi `harness/prompt-templates.ts` verbatim. Public surface:

```python
"""Pi parity: ``packages/agent/src/harness/prompt-templates.ts`` (SHA 734e08e).

Loads prompt templates from disk (`.md` files with optional YAML
frontmatter), supports shell-style arg substitution, and emits
diagnostics for failures.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import yaml  # PyYAML

PromptTemplateDiagnosticCode = Literal[
    "file_info_failed", "list_failed", "read_failed", "parse_failed",
]


@dataclass(frozen=True)
class PromptTemplate:
    """Pi parity: ``PromptTemplate`` interface."""
    name: str
    description: str
    content: str


@dataclass(frozen=True)
class PromptTemplateDiagnostic:
    """Pi parity: ``PromptTemplateDiagnostic`` interface."""
    code: PromptTemplateDiagnosticCode
    message: str
    path: str | None = None


@dataclass(frozen=True)
class LoadPromptTemplatesResult:
    templates: list[PromptTemplate] = field(default_factory=list)
    diagnostics: list[PromptTemplateDiagnostic] = field(default_factory=list)


def load_prompt_templates(paths: list[str | Path]) -> LoadPromptTemplatesResult:
    """Pi parity: ``loadPromptTemplates``.

    For each input path:
    - Directory: load direct ``.md`` children (NON-recursive).
    - File: load if `.md`, skip otherwise.
    Missing paths → diagnostic, continue.
    """


def parse_command_args(input_str: str) -> list[str]:
    """Pi parity: ``parseCommandArgs`` — shell-style quoted argument parser."""


def substitute_args(content: str, args: list[str]) -> str:
    """Pi parity: ``substituteArgs``.

    Placeholders supported:
    - ``$1``, ``$2``, ... positional
    - ``$@`` and ``$ARGUMENTS`` — all args joined with spaces
    - ``${@:N}`` — args from index N to end
    - ``${@:N:L}`` — args from index N, length L
    """


def format_prompt_template_invocation(
    template: PromptTemplate, args: list[str], prefix: str | None = None,
) -> str:
    """Pi parity: ``formatPromptTemplateInvocation``."""


__all__ = [
    "PromptTemplate", "PromptTemplateDiagnostic", "PromptTemplateDiagnosticCode",
    "LoadPromptTemplatesResult",
    "load_prompt_templates", "parse_command_args", "substitute_args",
    "format_prompt_template_invocation",
]
```

### B.1 Frontmatter parsing

YAML between `---` delimiters at file start. Use `yaml.safe_load()`. Description fallback: first non-empty body line, truncated to 60 chars (with ellipsis if truncated).

### B.2 Name derivation

`PromptTemplate.name = filename.stem` (e.g., `/tmp/templates/git-commit.md` → `name = "git-commit"`).

---

## §C — `aelix_agent_core/harness/skills.py` (NEW)

Port Pi `harness/skills.ts` verbatim.

```python
"""Pi parity: ``packages/agent/src/harness/skills.ts`` (SHA 734e08e).

Loads skills from disk via recursive SKILL.md scan + root .md files,
honors .gitignore/.ignore/.fdignore rules, validates frontmatter.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pathspec
import yaml

SkillDiagnosticCode = Literal[
    "file_info_failed", "list_failed", "read_failed",
    "parse_failed", "invalid_metadata",
]


@dataclass(frozen=True)
class Skill:
    """Pi parity: ``Skill`` interface."""
    name: str
    description: str
    content: str
    file_path: str
    disable_model_invocation: bool = False


@dataclass(frozen=True)
class SkillDiagnostic:
    code: SkillDiagnosticCode
    message: str
    path: str | None = None


@dataclass(frozen=True)
class LoadSkillsResult:
    skills: list[Skill] = field(default_factory=list)
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)


def load_skills(dirs: list[str | Path]) -> LoadSkillsResult:
    """Pi parity: ``loadSkills``.

    For each input dir:
    - Recursive scan for ``SKILL.md`` (first hit per dir wins).
    - ALSO load root ``.md`` files in the top-level directory.
    - Honor ``.gitignore`` / ``.ignore`` / ``.fdignore``.
    - Validate frontmatter: name matches parent dir, name regex
      (alphanumeric + ``-`` + ``_`` + ``.``), description non-empty.
    """


def format_skill_invocation(
    skill: Skill, additional_instructions: str | None = None,
) -> str:
    """Pi parity: ``formatSkillInvocation``."""


# Validation
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_NAME_MAX_LENGTH = 100  # Pi's Skill name length limit


def _validate_skill_name(name: str, parent_dir_name: str) -> bool:
    """Pi parity: name must match parent dir, pass regex, length OK."""


__all__ = [
    "Skill", "SkillDiagnostic", "SkillDiagnosticCode", "LoadSkillsResult",
    "load_skills", "format_skill_invocation",
]
```

### C.1 Ignore file handling

Use `pathspec.PathSpec.from_lines("gitwildmatch", patterns)` for each ignore file. Compose patterns with relative-path prefixing (so a `.gitignore` in `/foo/` applies to `/foo/**`).

### C.2 Frontmatter required fields

- `name` (must match parent directory name)
- `description` (non-empty)
- `disable-model-invocation` (optional bool, default False)

Missing/invalid → `SkillDiagnostic(code="invalid_metadata", ...)`, skip skill.

---

## §D — `aelix_agent_core/harness/_extension_runner.py` (NEW)

```python
"""ExtensionRunner — aggregation surface for loaded Extensions.

Pi parity: ``session.extensionRunner.getRegisteredCommands()`` —
returns all registered commands across loaded extensions.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_coding_agent.extensions.api import Extension, ExtensionCommand


@dataclass(frozen=True)
class ExtensionRunner:
    """Pi parity: ``ExtensionRunner`` aggregation surface."""

    extensions: list["Extension"]

    def get_registered_commands(self) -> list["ExtensionCommand"]:
        """Pi parity: ``ExtensionRunner.getRegisteredCommands()``.

        Returns all commands registered across loaded extensions.
        """
        commands: list[ExtensionCommand] = []
        for ext in self.extensions:
            commands.extend(ext.commands or [])
        return commands


__all__ = ["ExtensionRunner"]
```

---

## §E — `aelix_agent_core/harness/core.py` AMEND

Add 3 new attributes + 2 setter methods:

```python
class AgentHarness:
    # ... existing attributes ...
    extension_runner: ExtensionRunner = field(default_factory=lambda: ExtensionRunner(extensions=[]))
    prompt_templates: list[PromptTemplate] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)

    def set_prompt_templates(self, templates: list[PromptTemplate]) -> None:
        """Replace the harness's prompt template registry."""
        self.prompt_templates = templates

    def set_skills(self, skills: list[Skill]) -> None:
        """Replace the harness's skill registry."""
        self.skills = skills
```

Pi parity comment: `# Pi parity: session.promptTemplates / session.resourceLoader.getSkills() — Sprint 6h₁ ports the harness-side surface.`

---

## §F — `aelix_coding_agent/rpc/rpc_mode.py` AMEND

1. **Drop `get_commands` from `DEFERRED_COMMANDS`** (line ~93).
2. **Add `_handle_get_commands` handler** per Pi `rpc-mode.ts:557-590`:

```python
async def _handle_get_commands(
    harness: AgentHarness, registry: Any, cmd: Any,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:557-590`` ``get_commands`` handler.

    Aggregates 3 sources:
    1. Extension-registered commands (``source="extension"``)
    2. Prompt templates (``source="prompt"``)
    3. Skills (``source="skill"`` — name prefixed with ``"skill:"``)
    """

    commands: list[RpcSlashCommand] = []

    # Source 1: extension commands.
    for command in harness.extension_runner.get_registered_commands():
        commands.append(RpcSlashCommand(
            name=command.invocation_name,
            description=command.description or "",
            source="extension",
            source_info=_extension_source_info_to_rpc(command.source_info),
        ))

    # Source 2: prompt templates.
    for template in harness.prompt_templates:
        commands.append(RpcSlashCommand(
            name=template.name,
            description=template.description,
            source="prompt",
            source_info=_template_source_info(template),  # NEW helper
        ))

    # Source 3: skills (prefixed with "skill:").
    for skill in harness.skills:
        commands.append(RpcSlashCommand(
            name=f"skill:{skill.name}",
            description=skill.description,
            source="skill",
            source_info=_skill_source_info(skill),  # NEW helper
        ))

    return RpcSuccessResponse(
        id=cmd.id, command="get_commands",
        data={"commands": [_slash_command_to_dict(c) for c in commands]},
    )
```

3. **Add to `SUPPORTED_COMMANDS`** + dispatcher.

---

## §G — Tests (binding plan, ~660 LOC)

### Unit
- `tests/harness/test_prompt_templates.py` (~180 LOC):
  - `load_prompt_templates` from directory (non-recursive .md children)
  - Load explicit .md files
  - Missing path → diagnostic, continue
  - Frontmatter parsing (yaml.safe_load)
  - Name derivation (filename stem)
  - Description fallback (first body line truncated to 60 chars)
  - `parse_command_args` shell-style quoted strings
  - `substitute_args` all 5 placeholder types
  - `format_prompt_template_invocation` with + without prefix
- `tests/harness/test_skills.py` (~200 LOC):
  - `load_skills` recursive SKILL.md discovery
  - Load root .md files
  - `.gitignore` honoring
  - `.ignore` / `.fdignore` honoring
  - Name validation (matches parent dir / regex / length)
  - Description non-empty validation
  - Frontmatter parsing
  - `format_skill_invocation` with + without additional instructions
- `tests/harness/test_extension_runner.py` (~60 LOC):
  - `ExtensionRunner.get_registered_commands()` aggregates across extensions
  - Empty extension list → empty result

### Integration
- `tests/harness/test_harness_session_aggregation.py` (~40 LOC):
  - `harness.set_prompt_templates(...)` + `harness.set_skills(...)` round-trip
  - `harness.extension_runner` getter
- `tests/rpc/test_rpc_mode_get_commands.py` (~80 LOC):
  - `_handle_get_commands` with 3 sources populated → aggregated `RpcSlashCommand[]`
  - Empty sources → empty list
  - Skill names prefixed with `"skill:"`
  - All 3 source labels correct (`"extension"`, `"prompt"`, `"skill"`)

### Pi parity closure pin
- `tests/pi_parity/test_phase_4_8_strict_superset.py` (~80 LOC):
  - Assert `DEFERRED_COMMANDS` no longer contains `get_commands` (now 16 entries; was 17)
  - Assert `SUPPORTED_COMMANDS` now contains `get_commands` (now 13 entries; was 12)
  - Assert `RpcSlashCommand.source` Literal exact 3 values: `extension`/`prompt`/`skill`
  - Assert skill name prefix convention (`"skill:"`)
  - Assert prompt_templates / skills public surface (7 + 5 exports)
  - Assert Pi name validation regex matches: alphanumeric + `-` + `_` + `.`
  - Assert ignore file priority: `.gitignore` → `.ignore` → `.fdignore`

---

## §H — Dependencies

Add to `packages/aelix-agent-core/pyproject.toml`:
```toml
dependencies = [
    # ... existing ...
    "PyYAML>=6.0",
    "pathspec>=0.12",
]
```

Both deps are mature, pure-Python (PyYAML has C ext but ships prebuilt wheels), and widely used.

---

## §I — ADRs

### Amend
- **ADR-0034** — add row: "Sprint 6h₁ ported prompt-templates (~250 LOC) + skills (~300 LOC) + `get_commands` RPC handler. `DEFERRED_COMMANDS` drops `get_commands` → 16 entries; `SUPPORTED_COMMANDS` → 13 entries."

### NEW
- **ADR-0069** — `0069-prompt-templates-and-skills.md` — Pi parity port of `prompt-templates.ts` (`PromptTemplate` + 5 functions) + `skills.ts` (`Skill` + 3 functions) + harness aggregation hooks + PyYAML/pathspec deps.
- **ADR-0070** — `0070-phase-4-8-strict-superset-closure.md` — closure pin. Roster: P-216 ~ P-223. Sprint 6h₂/h₃ carry-forward enumerated.

### README
Update `docs/decisions/README.md` with 2 new ADR rows + Sprint 6h sub-table.

---

## §J — Forward-compat clause (binding)

After Sprint 6h₁:
- `DEFERRED_COMMANDS` has 16 entries (was 17). `SUPPORTED_COMMANDS` has 13 entries (was 12). Sprint 6d closure pin updated.
- `harness.prompt_templates` / `harness.skills` / `harness.extension_runner` are public attributes; callers populate via `set_prompt_templates` / `set_skills` / extension loader.
- Pi `ExecutionEnv` browser-abstraction layer is intentionally NOT ported — Aelix uses stdlib `pathlib`/`open()` directly.

Sprint 6h₂ owners (carry-forward):
- 16 remaining RPC commands (steer / follow_up / cycle_thinking_level / queue modes / auto modes / abort_bash / session inspection / session tree / extension UI)
- Workspace-scoped model selection (`cycle_model.isScoped: true`)
- `applyProviderConfig` for `register_provider.config.models`
- `enableGitHubCopilotModel` POST automation

Sprint 6h₃ owners:
- `image-models.ts` + `image-models.generated.ts`
- Typed `Model.compat` discriminated union

---

## §K — Sprint workflow (ADR-0032)

- W0 — research ✓ DONE
- W1 — this spec (binding)
- W2 — executor opus implements §B~§F
- W3 — verification
- W4 — code-reviewer opus (parallel with W5)
- W5 — architect opus Pi parity audit (parallel with W4)
- W6 — apply must-fixes + atomic commits + ADRs accepted

**Atomic commit plan (W6, 5 commits):**
1. `feat: harness — prompt_templates port (Pi parity prompt-templates.ts) + PyYAML dep (ADR-0069, P-216/P-223)`
2. `feat: harness — skills port (Pi parity skills.ts) + pathspec dep (ADR-0069, P-217)`
3. `feat: harness — ExtensionRunner aggregation + harness wire (ADR-0069, P-219/P-220)`
4. `feat: rpc — _handle_get_commands handler + drop from DEFERRED_COMMANDS (P-219)`
5. `docs: ADRs 0034 amend + NEW 0069/0070 + README + spec — Phase 4.8 closure`

---

## §L — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 1381 baseline + ~80 new ≈ 1461+; 0 fail |
| ruff check | clean |
| pyright spike | 8 errors (baseline preserved) |
| Sprint 6d closure pin | DEFERRED_COMMANDS now 16 (was 17); SUPPORTED 13 (was 12) |
| Sprint 6f closure pin | NO regressions (Pi helpers, ModelRegistry, EXTENDED_THINKING_LEVELS) |
| Sprint 6g closure pin | NO regressions (catalog 32×942, KnownProvider semantic order, DEFAULT_THINKING_LEVEL=medium) |
| Atomic commit count | exactly 5 |

---

**End of binding spec. Architect READ-ONLY until W6.**
