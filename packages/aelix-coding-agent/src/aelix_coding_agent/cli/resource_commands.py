"""Pi parity: ``_expandSkillCommand`` + the prompt-template slash commands.

The USER-INITIATED half of the skills channel (#115). Where
``cli/skills_prompt`` tells the model a skill *exists*, this is how a human
forces its full body into the conversation — and it is the other half of what
made ``format_skill_invocation`` look like dead code.

Pi sources (SHA 734e08e):

- ``coding-agent/src/core/agent-session.ts:1147-1177`` —
  ``_expandSkillCommand``: ``/skill:<name> [args]`` → the ``<skill>`` block,
  args appended after a blank line. Unknown name → pass through.
- ``coding-agent/src/modes/interactive/interactive-mode.ts:481-485`` /
  ``:498-509`` — prompt templates registered as ``/<name>``, skill commands
  registered as ``/skill:<name>`` and gated on ``getEnableSkillCommands()``.

Both expansions produce TURN TEXT (a user message), never a system-prompt
chunk. That distinction is the whole design: the catalog is always in context
and cheap, the body arrives only when it is asked for.

Aelix divergences from Pi (intentional, justified):

- **The body comes from the LOADED skill, not a re-read of the file.** Pi
  re-reads ``skill.filePath`` and strips frontmatter inline because pi's
  coding-agent ``Skill`` has no ``content`` field (``core/skills.ts:75-82``).
  Aelix's :class:`~aelix_agent_core.harness.skills.Skill` carries ``content``,
  which is exactly why
  :func:`~aelix_agent_core.harness.skills.format_skill_invocation` takes a
  ``Skill`` — so the already-ported, byte-identical formatter is used instead
  of a second frontmatter-stripping path. Consequence worth knowing: editing a
  ``SKILL.md`` mid-session needs ``/reload`` before ``/skill:<name>`` reflects
  it, where pi picks it up immediately. The trade is one fewer I/O failure mode
  and a guarantee that ``/skills``, the model-facing catalog and this expansion
  can never describe three different versions of the same skill.
- **An unknown name is NOT passed through to the model.** Pi returns the
  original text, so ``/skill:typo`` reaches the model as literal prose. Aelix
  returns :data:`None` and lets the shell's existing "Unknown command" branch
  answer, which is a deliberate, pre-existing aelix rule (``tui/shell.py``: a
  bare ``/x`` is never sent to the model). Reporting the typo beats asking the
  model to interpret it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aelix_agent_core.harness.prompt_templates import (
    PromptTemplate,
    format_prompt_template_invocation,
    parse_command_args,
)
from aelix_agent_core.harness.skills import Skill, format_skill_invocation

if TYPE_CHECKING:
    from collections.abc import Sequence

_SKILL_PREFIX = "/skill:"


def _split_command(text: str, prefix: str) -> tuple[str, str]:
    """``("<name>", "<args>")`` for ``text`` known to start with ``prefix``.

    Pi parity: ``agent-session.ts:1150-1152`` — split on the FIRST space, args
    stripped, no-space → empty args.
    """

    rest = text[len(prefix) :]
    name, _, args = rest.partition(" ")
    return name, args.strip()


def expand_resource_command(
    text: str,
    *,
    skills: Sequence[Skill] = (),
    templates: Sequence[PromptTemplate] = (),
    skill_commands_enabled: bool = True,
) -> str | None:
    """The turn text a ``/skill:<name>`` or ``/<template>`` line expands to.

    Returns :data:`None` when ``text`` is not one of these — including for an
    unknown name — so the caller's existing command chain is untouched and
    keeps ownership of the "unknown command" message.

    ``skill_commands_enabled`` is the ``enableSkillCommands`` setting. Pi gates
    only the COMMAND on it (``interactive-mode.ts:500``); the system-prompt
    catalog is unconditional there and here. That split is what the setting has
    always claimed to do, and until #115 it did nothing at all.

    The sequences are typed with the real kernel dataclasses, and the harness
    hands them over as ``list[Any]`` (``harness/core.py`` ``skills`` /
    ``prompt_templates`` properties), which satisfies that. Names are read with
    :func:`getattr` rather than attribute access only so a duck-typed test
    double needs no import; the annotation is what the type gate checks.
    """

    if not text.startswith("/"):
        return None

    if text.startswith(_SKILL_PREFIX):
        if not skill_commands_enabled:
            return None
        name, args = _split_command(text, _SKILL_PREFIX)
        for skill in skills:
            if getattr(skill, "name", None) == name:
                # Pi appends the args after a blank line; that is
                # ``format_skill_invocation``'s ``additional_instructions``
                # parameter, verbatim (``harness/skills.py``).
                return format_skill_invocation(skill, args or None)
        return None

    name, args = _split_command(text, "/")
    if not name:
        return None
    for template in templates:
        if getattr(template, "name", None) == name:
            # ``parse_command_args`` is pi's quote-aware splitter, so
            # ``/review "two words" x`` fills ``$1`` with ``two words`` rather
            # than ``"two``.
            return format_prompt_template_invocation(
                template, parse_command_args(args)
            )
    return None


__all__ = ["expand_resource_command"]
