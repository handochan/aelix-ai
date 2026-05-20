"""Sprint 6h₁ / Phase 4.8 §G closure pin (ADR-0069 / ADR-0070).

Pi parity invariant: ``get_commands`` is wired to the harness's
extension / prompt-template / skill surfaces. ``DEFERRED_COMMANDS``
drops from 17 → 16; ``SUPPORTED_COMMANDS`` rises from 12 → 13.

Closure date: **2026-05-20**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Roster (W0 + W4 + W5 propositions covered):

- W0:
  - P-216 — prompt-templates surface (5 functions + 2 types)
  - P-217 — skills surface (3 functions + 2 types) + Pi name regex
  - P-218 — RpcSlashCommand shape
  - P-219 — get_commands handler aggregation (3 sources)
  - P-220 — ExtensionRunner.get_registered_commands()
  - P-221 — ExtensionSourceInfo.identifier optional field
  - P-222 — PyYAML + pathspec deps
  - P-223 — Arg substitution placeholders
- W4 / W5 (W6 applied):
  - P-224 BLOCKING — :class:`ResolvedCommand` with Pi disambiguation suffix
  - P-225 BLOCKING — Pi-shape ``sourceInfo`` wire ``{path, source, scope, origin}``
  - P-226 MAJOR — :class:`PromptTemplate.description` non-optional default fix
  - P-227 MINOR — fixture text matches actual Pi regex
  - P-229 BLOCKING — :class:`ResolvedCommand` forwards owning extension's source_info
  - P-233 MINOR — YAML parse error message surfaced in diagnostic
  - P-234 MINOR — case-insensitive ``.md`` extension strip
  - W4 m2 — disable-model-invocation sentinel test (integer ``1`` is not ``True``)
  - W4 m4 — shared ``_frontmatter`` parser between prompt_templates + skills
"""

from __future__ import annotations

import json
from pathlib import Path

from aelix_agent_core.harness import prompt_templates as pt_mod
from aelix_agent_core.harness import skills as sk_mod
from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_agent_core.harness.prompt_templates import (
    PromptTemplate,
    PromptTemplateDiagnostic,
    format_prompt_template_invocation,
    load_prompt_templates,
    parse_command_args,
    substitute_args,
)
from aelix_agent_core.harness.skills import (
    Skill,
    SkillDiagnostic,
    format_skill_invocation,
    load_skills,
)
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
)
from aelix_coding_agent.rpc.rpc_types import (
    RPC_COMMAND_TYPES,
    RpcSlashCommand,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads((_FIXTURES / "pi_get_commands_734e08e.json").read_text())


# === §A — Deferred / Supported counts move (P-219) ============================


def test_get_commands_is_no_longer_deferred() -> None:
    """Sprint 6h₁: ``get_commands`` moved out of DEFERRED_COMMANDS."""

    assert "get_commands" not in DEFERRED_COMMANDS


def test_get_commands_is_supported() -> None:
    """Sprint 6h₁: ``get_commands`` is in SUPPORTED_COMMANDS."""

    assert "get_commands" in SUPPORTED_COMMANDS


def test_supported_count_is_thirteen() -> None:
    """Sprint 6h₁: 12 → 13 supported.

    Sprint 6h₂ (ADR-0071 / P-245~P-253) wires 9 more → 13 → 22 supported.
    Sprint 6h₃ (ADR-0073 / P-268~P-274) wires 2 more → 22 → 24 supported.
    Sprint 6h₄a (ADR-0075 / P-293~P-298) wires 2 more → 24 → 26 supported.
    Closure pin retains the original name; the assertion follows the
    live count so future drift trips mechanically.
    """

    assert len(SUPPORTED_COMMANDS) == 26


def test_deferred_count_is_sixteen() -> None:
    """Sprint 6h₁: 17 → 16 deferred.

    Sprint 6h₂ (ADR-0071 / P-245~P-253) drops 9 more → 16 → 7 deferred.
    Sprint 6h₃ (ADR-0073 / P-268~P-274) drops 2 more → 7 → 5 deferred.
    Sprint 6h₄a (ADR-0075 / P-293~P-298) drops 2 more → 5 → 3 deferred.
    """

    assert len(DEFERRED_COMMANDS) == 3


def test_supported_plus_deferred_still_covers_pi() -> None:
    """The 29-command Pi RpcCommand discriminator set is unchanged."""

    assert SUPPORTED_COMMANDS.isdisjoint(set(DEFERRED_COMMANDS.keys()))
    assert SUPPORTED_COMMANDS | set(DEFERRED_COMMANDS.keys()) == RPC_COMMAND_TYPES
    assert len(RPC_COMMAND_TYPES) == 29


# === §B — RpcSlashCommand source Literal (P-218) ==============================


def test_rpc_slash_command_source_literal_is_three_values() -> None:
    """Pi parity: ``source: 'extension' | 'prompt' | 'skill'``."""

    import typing

    hints = typing.get_type_hints(RpcSlashCommand)
    source_type = hints["source"]
    # Literal types expose their args via ``typing.get_args``.
    args = set(typing.get_args(source_type))
    assert args == {"extension", "prompt", "skill"}


# === §C — Prompt-templates public surface (P-216) =============================


def test_prompt_templates_exports_match_pi_surface() -> None:
    """Pi parity: 5 functions + 2 type aliases + 2 dataclasses + 1 result."""

    public = set(pt_mod.__all__)
    expected = {
        "load_prompt_templates",
        "parse_command_args",
        "substitute_args",
        "format_prompt_template_invocation",
        "PromptTemplate",
        "PromptTemplateDiagnostic",
        "PromptTemplateDiagnosticCode",
        "LoadPromptTemplatesResult",
    }
    assert expected <= public


def test_prompt_template_dataclass_shape() -> None:
    """Pi parity: ``{name, description, content}``."""

    fields = set(PromptTemplate.__dataclass_fields__.keys())
    assert fields == {"name", "description", "content"}


def test_prompt_template_diagnostic_code_literal_values() -> None:
    """Pi parity: ``"file_info_failed" | "list_failed" | "read_failed" | "parse_failed"``."""

    import typing

    args = set(typing.get_args(pt_mod.PromptTemplateDiagnosticCode))
    assert args == {
        "file_info_failed",
        "list_failed",
        "read_failed",
        "parse_failed",
    }


# === §D — Skills public surface (P-217) =======================================


def test_skills_exports_match_pi_surface() -> None:
    """Pi parity: 3 functions + 2 type aliases + 2 dataclasses + 1 result."""

    public = set(sk_mod.__all__)
    expected = {
        "load_skills",
        "format_skill_invocation",
        "Skill",
        "SkillDiagnostic",
        "SkillDiagnosticCode",
        "LoadSkillsResult",
    }
    assert expected <= public


def test_skill_dataclass_shape() -> None:
    """Pi parity: ``{name, description, content, file_path, disable_model_invocation}``."""

    fields = set(Skill.__dataclass_fields__.keys())
    assert fields == {
        "name",
        "description",
        "content",
        "file_path",
        "disable_model_invocation",
    }


def test_skill_diagnostic_code_literal_values() -> None:
    """Pi parity: 5 diagnostic codes including ``invalid_metadata``."""

    import typing

    args = set(typing.get_args(sk_mod.SkillDiagnosticCode))
    assert args == {
        "file_info_failed",
        "list_failed",
        "read_failed",
        "parse_failed",
        "invalid_metadata",
    }


def test_skill_name_regex_matches_pi() -> None:
    """Pi parity: ``^[a-z0-9-]+$`` (lowercase + digits + hyphens only).

    Pi ``skills.ts:285``. Spec text said "alphanumeric + ``-`` + ``_`` +
    ``.``" but Pi reality is stricter; ADR-0069 follows Pi byte-for-byte.
    """

    pattern = sk_mod._NAME_REGEX
    assert pattern.match("good-name")
    assert pattern.match("alpha123")
    assert not pattern.match("BadCase")
    assert not pattern.match("under_score")
    assert not pattern.match("with.dot")


def test_skill_name_length_limit() -> None:
    """Pi parity: ``MAX_NAME_LENGTH = 64``."""

    assert sk_mod._MAX_NAME_LENGTH == 64


def test_skill_description_length_limit() -> None:
    """Pi parity: ``MAX_DESCRIPTION_LENGTH = 1024``."""

    assert sk_mod._MAX_DESCRIPTION_LENGTH == 1024


def test_ignore_file_priority_matches_pi() -> None:
    """Pi parity: ``.gitignore`` → ``.ignore`` → ``.fdignore`` (order)."""

    assert sk_mod._IGNORE_FILE_NAMES == (".gitignore", ".ignore", ".fdignore")


# === §E — ExtensionRunner surface (P-220) =====================================


def test_extension_runner_has_get_registered_commands() -> None:
    runner = ExtensionRunner(extensions=[])
    assert callable(runner.get_registered_commands)
    assert runner.get_registered_commands() == []


# === §F — ExtensionSourceInfo.identifier (P-221) ==============================


def test_extension_source_info_has_identifier_field() -> None:
    """Pi parity: ``SourceInfo.identifier`` optional field."""

    from aelix_coding_agent.extensions.api import ExtensionSourceInfo

    fields = set(ExtensionSourceInfo.__dataclass_fields__.keys())
    assert "identifier" in fields


# === §G — Arg substitution placeholders (P-223) ===============================


def test_arg_substitution_supports_all_pi_placeholders() -> None:
    """Pi parity: ``$1``, ``$@``, ``$ARGUMENTS``, ``${@:N}``, ``${@:N:L}``."""

    args = ["a", "b", "c", "d"]
    assert substitute_args("$1", args) == "a"
    assert substitute_args("$@", args) == "a b c d"
    assert substitute_args("$ARGUMENTS", args) == "a b c d"
    assert substitute_args("${@:2}", args) == "b c d"
    assert substitute_args("${@:2:2}", args) == "b c"


def test_parse_command_args_is_shell_quote_aware() -> None:
    assert parse_command_args('"a b" c') == ["a b", "c"]
    assert parse_command_args("'a b' c") == ["a b", "c"]


# === §H — Skill name prefix convention (P-219) ================================


def test_skill_invocation_format_matches_pi_wire() -> None:
    """Pi parity: ``<skill name="..." location="...">`` shape."""

    skill = Skill(
        name="ex",
        description="d",
        content="C",
        file_path="/p/ex/SKILL.md",
    )
    out = format_skill_invocation(skill)
    assert '<skill name="ex" location="/p/ex/SKILL.md">' in out
    assert "References are relative to /p/ex." in out


def test_format_prompt_template_invocation_with_args() -> None:
    """Pi parity: ``substituteArgs(template.content, args)``."""

    tpl = PromptTemplate(name="t", description="d", content="hi $1")
    assert format_prompt_template_invocation(tpl, ["world"]) == "hi world"


# === §I — Pi fixture immutability =============================================


def test_pi_sha_pinned_to_phase_4_8_baseline() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_pi_fixture_get_commands_aggregation_matches_implementation() -> None:
    """Pi fixture pinned the 3-source aggregation shape; our handler
    must agree.
    """

    fixture = _load_fixture()
    agg = fixture["get_commands_handler_aggregation"]
    assert agg["name_prefix_convention"].startswith("Skills are prefixed with 'skill:'")
    assert agg["extension_commands"]["source"].endswith("getRegisteredCommands()")
    assert agg["prompt_templates"]["source"] == "session.promptTemplates"
    assert agg["skills"]["source"].endswith("getSkills().skills")


# === §J — Loader smoke ========================================================


def test_load_prompt_templates_smoke(tmp_path: Path) -> None:
    """End-to-end smoke: load a template + assert the diagnostic class
    is the expected type."""

    (tmp_path / "x.md").write_text("---\ndescription: ok\n---\nbody")
    result = load_prompt_templates([tmp_path])
    assert len(result.templates) == 1
    assert all(isinstance(d, PromptTemplateDiagnostic) for d in result.diagnostics)


def test_load_skills_smoke(tmp_path: Path) -> None:
    """End-to-end smoke: load a skill + assert diagnostic class."""

    parent = tmp_path / "ok-skill"
    parent.mkdir()
    (parent / "SKILL.md").write_text(
        "---\nname: ok-skill\ndescription: ok\n---\nbody"
    )
    result = load_skills([tmp_path])
    assert len(result.skills) == 1
    assert all(isinstance(d, SkillDiagnostic) for d in result.diagnostics)


# === §K — W6 must-fix closure pins ============================================
#
# Sprint 6h₁ W6 applied 3 BLOCKING + 1 MAJOR + 4 MINOR + 2 W4 fixes from
# the W4 code review and W5 Pi parity audit. The regressions below assert
# the wire shapes Pi requires so future drift trips mechanically.


def test_resolved_command_carries_disambiguation_and_source_info() -> None:
    """Sprint 6h₁ W6 P-224 / P-229 BLOCKING closure.

    :meth:`ExtensionRunner.get_registered_commands` returns
    :class:`ResolvedCommand` with ``invocation_name`` (disambiguated via
    Pi ``{name}:{N}``) and ``source_info`` (forwarded from the owning
    extension).
    """

    from aelix_agent_core.harness._extension_runner import ResolvedCommand
    from aelix_coding_agent.extensions.api import (
        Extension,
        ExtensionSourceInfo,
        RegisteredCommand,
    )

    src = ExtensionSourceInfo(
        source="project", base_dir="/p", identifier="a-ext"
    )
    ext_a = Extension(name="a-ext", source_info=src)
    ext_a.commands["deploy"] = RegisteredCommand(
        name="deploy",
        handler=lambda **_: None,
        description="A",
        source="a-ext",
    )
    ext_b = Extension(name="b-ext")
    ext_b.commands["deploy"] = RegisteredCommand(
        name="deploy",
        handler=lambda **_: None,
        description="B",
        source="b-ext",
    )
    runner = ExtensionRunner(extensions=[ext_a, ext_b])
    resolved = runner.get_registered_commands()
    assert all(isinstance(r, ResolvedCommand) for r in resolved)
    assert [r.invocation_name for r in resolved] == ["deploy", "deploy:1"]
    assert resolved[0].source_info is src
    assert resolved[1].source_info is None


def test_extension_source_info_carries_pi_wire_shape_fields() -> None:
    """Sprint 6h₁ W6 P-225 BLOCKING closure.

    Pi ``source-info.ts:1-12`` defines ``{path, source, scope, origin,
    baseDir?}``. The :class:`ExtensionSourceInfo` dataclass now carries
    every Pi field (``scope`` defaults to ``"user"``, ``origin`` defaults
    to ``"top-level"``).
    """

    from aelix_coding_agent.extensions.api import ExtensionSourceInfo

    fields = set(ExtensionSourceInfo.__dataclass_fields__.keys())
    assert {"path", "scope", "origin"} <= fields
    # Defaults: scope user, origin top-level.
    info = ExtensionSourceInfo(source="project")
    assert info.scope == "user"
    assert info.origin == "top-level"


def test_get_commands_source_info_emits_pi_wire_shape() -> None:
    """Sprint 6h₁ W6 P-225 BLOCKING closure.

    The ``_handle_get_commands`` wire ``sourceInfo`` is the Pi
    ``{path, source, scope, origin}`` (plus optional ``baseDir``) shape
    for every source. No legacy ``{type, identifier}`` dict.
    """

    from aelix_agent_core.harness._extension_runner import ResolvedCommand
    from aelix_coding_agent.extensions.api import (
        ExtensionSourceInfo,
        RegisteredCommand,
    )
    from aelix_coding_agent.rpc.rpc_mode import (
        _prompt_template_source_info,
        _registered_command_source_info,
        _skill_source_info,
    )

    # Extension synthesiser.
    cmd = RegisteredCommand(
        name="x", handler=lambda **_: None, description=None, source="e"
    )
    resolved = ResolvedCommand(
        command=cmd,
        invocation_name="x",
        source_info=ExtensionSourceInfo(
            source="project", base_dir="/b", identifier="e", path="/b"
        ),
    )
    ext_payload = _registered_command_source_info(resolved)
    assert set(ext_payload.keys()) >= {"path", "source", "scope", "origin"}
    assert ext_payload["scope"] == "user"
    assert ext_payload["origin"] == "top-level"
    assert ext_payload["baseDir"] == "/b"

    # Skill synthesiser.
    skill = Skill(
        name="s",
        description="d",
        content="c",
        file_path="/p/s/SKILL.md",
    )
    skill_payload = _skill_source_info(skill)
    assert skill_payload["path"] == "/p/s/SKILL.md"
    assert skill_payload["scope"] == "user"
    assert skill_payload["origin"] == "top-level"

    # Prompt template synthesiser — bare PromptTemplate has no file path
    # field but the synthesiser still emits the Pi-shape fallback.
    tpl = PromptTemplate(name="t", description="d", content="c")
    tpl_payload = _prompt_template_source_info(tpl)
    assert tpl_payload["scope"] == "user"
    assert tpl_payload["origin"] == "top-level"


def test_prompt_template_description_has_empty_default() -> None:
    """Sprint 6h₁ W6 P-226 MAJOR closure: ``description`` optional with ``""`` default."""

    tpl = PromptTemplate(name="bare")
    assert tpl.description == ""
    assert tpl.content == ""


def test_frontmatter_parser_is_shared_module() -> None:
    """Sprint 6h₁ W6 W4 m4 closure: shared :mod:`_frontmatter` helper."""

    from aelix_agent_core.harness import _frontmatter

    assert callable(_frontmatter.parse_frontmatter)
    # 3-tuple return shape (data, body, error).
    result = _frontmatter.parse_frontmatter("---\nname: ok\n---\nbody")
    assert result == ({"name": "ok"}, "body", None)


def test_yaml_parse_failure_surfaces_error_message(tmp_path: Path) -> None:
    """Sprint 6h₁ W6 P-233 closure: diagnostic message includes YAML error."""

    file = tmp_path / "broken.md"
    file.write_text("---\n: this is :: not valid yaml: [oops\n---\nbody")
    result = load_prompt_templates([file])
    failures = [d for d in result.diagnostics if d.code == "parse_failed"]
    assert failures
    assert failures[0].message != "failed to parse YAML frontmatter"
    assert "failed to parse YAML frontmatter" in failures[0].message


def test_fixture_p227_text_matches_actual_pi_regex() -> None:
    """Sprint 6h₁ W6 P-227 closure: fixture metadata text reflects the Pi
    regex ``^[a-z0-9-]+$`` (lowercase + digits + hyphens only) — NOT the
    overly-permissive ``alphanumeric + - + _ + .`` claim from the W1
    spec draft.
    """

    fixture = _load_fixture()
    rules = fixture["skills_surface"]["validation"]
    assert any("lowercase a-z, 0-9, hyphens only" in r for r in rules)
    # The misleading legacy text MUST NOT reappear.
    assert not any("alphanumeric + - + _ + ." in r for r in rules)
