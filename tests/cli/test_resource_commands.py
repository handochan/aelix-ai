"""#115 — ``/skill:<name>`` and ``/<prompt-template>`` expansion.

The user-initiated half of the resource channel. ``format_skill_invocation``
and ``format_prompt_template_invocation`` were both fully ported and had ZERO
production callers; this is the code path that calls them, so these tests are
what stops either becoming dead again.

Expansion is a pure text→text function on purpose — the TUI seam around it
(``tui/shell.py``) is a two-line "use this instead of the typed line", and
everything worth asserting lives here where no terminal is required.
"""

from __future__ import annotations

from aelix_agent_core.harness.prompt_templates import PromptTemplate
from aelix_agent_core.harness.skills import Skill
from aelix_coding_agent.cli.resource_commands import expand_resource_command

_SKILL = Skill(
    name="review",
    description="Review code.",
    content="BODY_MARKER do the review",
    file_path="/abs/skills/review/SKILL.md",
)
_HIDDEN = Skill(
    name="secret",
    description="Hidden from the catalog.",
    content="HIDDEN_BODY",
    file_path="/abs/skills/secret/SKILL.md",
    disable_model_invocation=True,
)
_TEMPLATE = PromptTemplate(
    name="greet",
    description="Greet someone.",
    content="TEMPLATE_MARKER greet $1 and mention $2. All: $ARGUMENTS",
)


def _expand(text: str, **kw: object) -> str | None:
    return expand_resource_command(
        text, skills=[_SKILL, _HIDDEN], templates=[_TEMPLATE], **kw
    )


# === /skill:<name> =========================================================


def test_skill_command_expands_to_the_full_body() -> None:
    """The BODY, unlike the system-prompt catalog which carries only metadata.

    That split is the whole point of #115: the catalog is cheap and always
    present, the body arrives only when asked for.
    """

    out = _expand("/skill:review")
    assert out is not None
    assert out.startswith('<skill name="review" location="/abs/skills/review/SKILL.md">')
    assert "BODY_MARKER do the review" in out
    assert out.endswith("</skill>")


def test_skill_command_appends_args_after_a_blank_line() -> None:
    """Pi ``agent-session.ts:1160`` — ``args ? f"{block}\\n\\n{args}" : block``."""

    out = _expand("/skill:review focus on the parser")
    assert out is not None
    assert out.endswith("</skill>\n\nfocus on the parser")


def test_a_hidden_skill_is_still_invocable_by_name() -> None:
    """``disable-model-invocation`` hides a skill from the CATALOG, not from the
    user (pi ``docs/skills.md:149``: "Users must use ``/skill:name``"). Filtering
    it here too would leave it with no way in at all."""

    out = _expand("/skill:secret")
    assert out is not None
    assert "HIDDEN_BODY" in out


def test_unknown_skill_returns_none_rather_than_passing_through() -> None:
    """A deliberate aelix divergence: pi returns the original text, so
    ``/skill:typo`` reaches the model as prose. Aelix's shell already answers
    an unrecognised ``/x`` with "Unknown command" and never prompts the model,
    and reporting the typo beats asking the model to interpret it."""

    assert _expand("/skill:nosuchskill") is None


def test_skill_commands_can_be_switched_off() -> None:
    """``enableSkillCommands`` — the setting that advertised a feature which did
    not exist until #115. Pi gates only the COMMAND on it
    (``interactive-mode.ts:500``); the catalog stays unconditional."""

    assert _expand("/skill:review", skill_commands_enabled=False) is None


def test_the_setting_does_not_also_disable_prompt_templates() -> None:
    """The two families share one expansion function, so it would be easy to
    hang both off one flag. Only skills are gated."""

    assert _expand("/greet Ada", skill_commands_enabled=False) is not None


# === /<prompt-template> ====================================================


def test_template_command_substitutes_positional_args() -> None:
    out = _expand("/greet Ada quantum")
    assert out == "TEMPLATE_MARKER greet Ada and mention quantum. All: Ada quantum"


def test_template_args_are_quote_aware() -> None:
    """``parse_command_args`` is pi's quote-aware splitter — without it,
    ``$1`` would be ``"two`` rather than ``two words``."""

    out = _expand('/greet "two words" x')
    assert out is not None
    assert "greet two words and mention x" in out


def test_missing_positional_args_become_empty_not_literal() -> None:
    """Pi ``substituteArgs``: ``args[n-1] ?? ""``."""

    out = _expand("/greet")
    assert out == "TEMPLATE_MARKER greet  and mention . All: "


def test_unknown_template_returns_none() -> None:
    assert _expand("/nosuchtemplate") is None


# === not a resource command ================================================


def test_plain_text_is_never_expanded() -> None:
    assert _expand("just a message") is None
    assert _expand("mention /greet in passing") is None


def test_a_bare_slash_is_not_a_command() -> None:
    """``"/".partition(" ")`` yields an empty name; without the guard that would
    match a template whose name is the empty string."""

    assert _expand("/") is None
    assert _expand("/ ") is None
