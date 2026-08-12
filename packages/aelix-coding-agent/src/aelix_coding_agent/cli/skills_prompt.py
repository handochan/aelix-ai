"""Pi parity: ``packages/coding-agent/src/core/skills.ts:336-372`` (SHA 734e08e).

The MODEL-FACING half of the skills channel (#115). Pi has two skill
formatters and they are not interchangeable:

- ``formatSkillsForPrompt`` (``coding-agent/src/core/skills.ts:336``) —
  ported here. Name + description + absolute location only, wrapped in
  ``<available_skills>``, appended to the SYSTEM PROMPT. This is
  progressive disclosure: the model learns a skill *exists* and is told
  to read the file when the task matches.
- ``formatSkillInvocation`` (``agent/src/harness/skills.ts:38``) — already
  ported, verbatim, as
  :func:`aelix_agent_core.harness.skills.format_skill_invocation`. It
  emits the FULL BODY and pi delivers it as a **user turn** for an
  explicit ``/skill:<name>``, never as a system-prompt chunk.

Issue #115 was filed as "``format_skill_invocation`` has zero production
callers", which reads as though wiring that one function closes the gap.
It does not: wiring the body formatter into the system prompt would inject
every skill's entire markdown on every turn, which is the opposite of what
pi does and what the issue's own "progressive disclosure" rationale asks
for. The missing piece was this module, which had never been ported at all
(``available_skills`` had zero hits repo-wide).

Aelix divergences from Pi (intentional, justified):

- **Byte cap.** Pi bounds neither the block nor a single description.
  Aelix already diverges here for ``AGENTS.md``
  (``agent_context.py:_MAX_CONTEXT_BYTES``) and the same argument is
  stronger for skills: the description is attacker-controlled (it arrives
  with a ``git clone``) and the loader does not bound it — a 200,000-char
  description loads with only an ``invalid_metadata`` diagnostic, which is
  advisory. Truncation is in place rather than dropping the skill, because
  a skill dropped here would still be listed by ``/skills`` — re-opening
  the exact human/model split #115 exists to close.
- The read-tool gate lives in the CALLER in pi
  (``system-prompt.ts:69-73``, ``:163-165``), and it does there too — see
  ``entry._skills_catalog_visible``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# Aelix-original bound (see the module docstring). Sized to match
# ``agent_context._MAX_CONTEXT_BYTES`` so the two attacker-influenced chunks
# that share the system prompt carry the same budget.
_MAX_CATALOG_BYTES = 32_768

# Per-field bound, applied BEFORE the block budget. Without it one hostile
# skill spends the whole catalog and every honest skill after it is dropped —
# the block cap alone is not a fair-share mechanism.
_MAX_DESCRIPTION_CHARS = 1_024

# Pi parity: ``skills.ts:344-346``. Verbatim, including the leading blank
# lines — the block is joined onto the preceding chunk by the harness and pi
# relies on this string to supply its own separation.
_PREAMBLE = (
    "\n\nThe following skills provide specialized instructions for specific tasks.",
    "Use the read tool to load a skill's file when the task matches its description.",
    "When a skill file references a relative path, resolve it against the skill "
    "directory (parent of SKILL.md / dirname of the path) and use that absolute "
    "path in tool commands.",
    "",
    "<available_skills>",
)


def _escape_xml(value: str) -> str:
    """Pi parity: ``skills.ts:364-371`` — the same five entities, same order.

    Order is load-bearing: ``&`` must be replaced FIRST or the ampersands
    introduced by the later replacements get double-escaped.

    Not cosmetic. Every field here is attacker-controlled frontmatter. An
    unescaped description containing ``</available_skills>`` closes the block
    early, so the rest of the text lands in the system prompt as
    instructions rather than as data.
    """

    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def format_skills_for_prompt(skills: Iterable[object]) -> str:
    """Pi parity: ``formatSkillsForPrompt(skills)``.

    Emits, per skill, ONLY its name, description and absolute file path.
    Never the body — see the module docstring.

    Skills with ``disable_model_invocation`` are excluded (pi
    ``skills.ts:337``). Note that ``/skills`` merely *labels* those, so
    copying the display filter would advertise precisely the skills the
    author asked to hide.

    Returns ``""`` when nothing is visible, so the caller can append
    unconditionally without introducing a blank chunk (pi
    ``skills.ts:338-340``).

    Typed against ``object`` rather than ``Skill`` deliberately: the harness
    exposes skills as ``list[Any]`` (``harness/core.py:1097``) and this
    module must not force product-core to import the kernel's dataclass to
    call it.
    """

    visible = [s for s in skills if not getattr(s, "disable_model_invocation", False)]
    if not visible:
        return ""

    lines: list[str] = list(_PREAMBLE)
    budget = _MAX_CATALOG_BYTES
    for skill in visible:
        name = _escape_xml(str(getattr(skill, "name", "")))
        raw_description = str(getattr(skill, "description", ""))
        description = _escape_xml(raw_description[:_MAX_DESCRIPTION_CHARS])
        location = _escape_xml(str(getattr(skill, "file_path", "")))
        entry = (
            "  <skill>",
            f"    <name>{name}</name>",
            f"    <description>{description}</description>",
            f"    <location>{location}</location>",
            "  </skill>",
        )
        cost = len("\n".join(entry).encode("utf-8")) + 1
        if cost > budget:
            # Out of budget. Stop rather than skip-and-continue: the entries
            # are already ordered by the loader's directory precedence, so
            # continuing would silently prefer later, lower-precedence skills
            # over the one that did not fit.
            break
        budget -= cost
        lines.extend(entry)

    lines.append("</available_skills>")
    return "\n".join(lines)


def skills_catalog_visible(
    *,
    no_tools: bool,
    no_builtin_tools: bool,
    active_tools: Sequence[str] | None,
) -> bool:
    """Pi parity: the read-tool gate at ``system-prompt.ts:69-73`` / ``:163-165``.

    Pi appends the catalog only when the ``read`` tool is available, because
    the block's own instruction is *"use the read tool to load a skill's
    file"*. Telling a model to use a tool it does not have is the defect
    class ``tests/cli/test_agent_context.py`` already polices for the
    extension signpost.

    ``no_builtin_tools`` is a gate of its own: the built-in registry is the
    only source of ``read``, and that filter is applied AFTER registration
    (``entry.py`` post-build), so a name-level check cannot see it.

    Kept here rather than in ``entry`` so it is unit-testable without a
    parsed CLI, and keyword-only so a caller cannot silently transpose the
    two booleans.
    """

    if no_tools or no_builtin_tools:
        return False
    if active_tools:
        return "read" in active_tools
    return True


__all__ = ["format_skills_for_prompt", "skills_catalog_visible"]
