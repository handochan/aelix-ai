"""``/extension new <name>`` — the surface that ASKS where an extension goes.

ISSUE #161, SHAPE 2, and it is here because shape 1 was measured and failed.

The issue offered three shapes and told us to choose on a live-model probe
rather than in advance. Shape 1 (the self-extension block asks in prose) was
built first and probed against a real model in a real interactive TUI:

    prompt              user's phrasing        asked?
    ------------------  ---------------------  -------------------------------
    pre-change          "Please add it"        no — wrote global, silently
    shipped clause      "Do the work."         no — wrote global, silently
    shipped clause      "Please add it"        no — wrote global, but SAID why
    strengthened        "Please add it"        YES — asked and stopped its turn
    strengthened        "Do the work."         no — wrote global

So prose compliance is a function of how the USER phrased their request, which
is not a property anyone can ship. The issue's own precedent said as much: a
model asked to add a tool once failed 2/2 and failed confidently.

WHAT THIS MODULE IS AND IS NOT. It is the pure half — name validation, target
resolution, and the starter file's text. The asking is a ``ctx.ui.select`` in
``tui/commands.py``, because that is where a dialog can exist; the permission
ladder cannot carry the question (``builtin/permission.py`` is allow/deny only,
it has no "somewhere else instead").

THE COMPLIANCE PROBLEM DOES NOT VANISH, it moves. A command the model never
invokes is a command that changed nothing, which is why the self-extension
block now names it — and why the block still carries the two absolute paths for
the case where the model writes directly anyway. The honest claim for shape 2
is narrower than "the model now asks": it is "the USER has a first-class way to
make the choice, and the model has a one-line way to hand it to them".
"""

from __future__ import annotations

import re
from pathlib import Path

# A file that ``importlib`` can load by a plain module name. The loader imports
# discovered files by stem, so a stem that is not an identifier is unloadable —
# refusing it here is better than writing a file that never loads.
_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# Names Python itself owns. A ``json.py`` in the extensions directory shadows
# the stdlib module for the whole process once that directory is on the path.
_RESERVED = frozenset(
    {"aelix", "aelix_coding_agent", "aelix_agent_core", "aelix_ai", "setup", "test"}
)

_MAX_NAME_CHARS = 40


def validate_extension_name(name: str) -> str | None:
    """``None`` when ``name`` is usable, else the reason it is not.

    Returns a message rather than raising: every caller is a UI that wants to
    show it, and an exception type would be a second thing to keep in sync.
    """

    candidate = name.strip()
    if not candidate:
        return "Give the extension a name: /extension new <name>"
    if len(candidate) > _MAX_NAME_CHARS:
        return f"Name is too long (max {_MAX_NAME_CHARS} characters)."
    if "/" in candidate or "\\" in candidate or candidate != Path(candidate).name:
        return "Name must be a bare module name, not a path."
    if not _NAME_RE.match(candidate):
        return (
            "Name must be a Python module name: lowercase letters, digits and "
            "underscores, not starting with a digit."
        )
    if candidate in _RESERVED:
        return f"{candidate!r} is reserved — it would shadow a real module."
    return None


def extension_targets(cwd: str, agent_dir: str) -> dict[str, Path]:
    """The two directories the loader actually scans, as ``{scope: dir}``.

    Derived from the same two places :func:`~aelix_coding_agent.cli.
    agent_context._extension_signpost` names, so the command and the prompt
    cannot drift about where extensions live. ``global`` first, matching the
    signpost's order and for the same measured reason: the project-local tier
    is trust-gated and fails SILENTLY when the project is untrusted.
    """

    return {
        "global": Path(agent_dir) / "extensions",
        "project": Path(cwd) / ".aelix" / "extensions",
    }


def starter_source(name: str) -> str:
    """The file ``/extension new`` writes.

    Deliberately a WORKING tool, not a stub with ``...`` in it: the failure
    #117 recorded was a model inventing a manifest format, and the cure was
    showing it real code. A scaffold that does not run teaches the same lesson
    badly. It mirrors ``examples/echo/echo.py``, which is the file the
    self-extension block already tells the model to read.
    """

    return f'''"""The ``{name}`` extension — created by ``/extension new {name}``.

Aelix imports this file and calls :func:`setup`. There is no manifest, no
JSON and no build step for a single-file extension like this one; edit it and
run ``/reload`` (or restart aelix) to pick the change up.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult


async def _{name}_execute(
    args: dict[str, Any], context: ToolExecutionContext
) -> ToolResult:
    text = str(args.get("text", ""))
    return ToolResult(content=[TextContent(text=f"{name} received: {{text}}")])


{name}_tool = AgentTool(
    name="{name}",
    description="Describe what this tool does — the model reads this.",
    # One line for the system prompt's "Available tools" list. Leave it out and
    # the tool still works and is still sent to the model; it just is not named
    # in the prompt's prose.
    prompt_snippet="Describe this tool in one line",
    parameters={{
        "type": "object",
        "properties": {{
            "text": {{"type": "string", "description": "Input text"}},
        }},
        "required": ["text"],
    }},
    execute=_{name}_execute,
)


def setup(aelix: Any) -> None:
    """The entry point. Aelix calls this once, in this process."""

    aelix.register_tool({name}_tool)


__all__ = ["setup", "{name}_tool"]
'''


def create_extension(target_dir: Path, name: str) -> Path:
    """Write the starter file and return its path. Refuses to overwrite.

    The refusal is the point: ``/extension new`` on an existing name is far
    more likely to be a typo than a request to discard working code, and the
    ``write`` tool's overwrite semantics are not what a *new* command should
    inherit.
    """

    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{name}.py"
    if path.exists():
        raise FileExistsError(str(path))
    path.write_text(starter_source(name), encoding="utf-8")
    return path


__all__ = [
    "create_extension",
    "extension_targets",
    "starter_source",
    "validate_extension_name",
]
