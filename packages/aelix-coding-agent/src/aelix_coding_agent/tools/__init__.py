"""Built-in coding tools (Sprint 5b §A, ADR-0042).

7 tools port Pi ``coding-agent/src/core/tools/`` at SHA ``734e08e``:
``bash``, ``read``, ``edit``, ``write``, ``grep``, ``find``, ``ls``.

Factory entry points (Pi parity ``core/tools/index.ts:128-176``):

- :func:`create_coding_tools` → ``[read, bash, edit, write]`` (4 mutation)
- :func:`create_read_only_tools` → ``[read, grep, find, ls]``
- :func:`create_all_tools` → ``{name: tool}`` for all 7

Each tool is an :class:`AgentTool` accepting a fresh ``(cwd, options)`` pair so
callers can swap the underlying ``Operations`` Protocol (Pi's SSH-swap surface).
"""

from __future__ import annotations

from typing import Literal

from aelix_agent_core.types import AgentTool

from aelix_coding_agent.tools.bash import (
    BashOperations,
    BashToolDetails,
    create_bash_tool,
    create_local_bash_operations,
)
from aelix_coding_agent.tools.edit import (
    EditOperations,
    EditToolDetails,
    create_edit_tool,
)
from aelix_coding_agent.tools.find import (
    FindOperations,
    FindToolDetails,
    create_find_tool,
)
from aelix_coding_agent.tools.grep import (
    GrepOperations,
    GrepToolDetails,
    create_grep_tool,
)
from aelix_coding_agent.tools.ls import (
    LsOperations,
    LsToolDetails,
    create_ls_tool,
)
from aelix_coding_agent.tools.read import (
    ReadOperations,
    ReadToolDetails,
    create_read_tool,
)
from aelix_coding_agent.tools.write import (
    WriteOperations,
    create_write_tool,
)

ToolName = Literal["read", "bash", "edit", "write", "grep", "find", "ls"]
"""Pi parity ``core/tools/index.ts:78-79`` ``ToolName`` literal union."""

ALL_TOOL_NAMES: frozenset[str] = frozenset(
    {"read", "bash", "edit", "write", "grep", "find", "ls"}
)
"""Pi parity ``allToolNames`` (``core/tools/index.ts``)."""


def create_coding_tools(
    cwd: str, options: dict | None = None
) -> list[AgentTool]:
    """Pi parity ``createCodingTools`` (``core/tools/index.ts:128-150``).

    4 mutation-capable tools: read + bash + edit + write.
    """

    opts = options or {}
    return [
        create_read_tool(cwd, opts.get("read")),
        create_bash_tool(cwd, opts.get("bash")),
        create_edit_tool(cwd, opts.get("edit")),
        create_write_tool(cwd, opts.get("write")),
    ]


def create_read_only_tools(
    cwd: str, options: dict | None = None
) -> list[AgentTool]:
    """Pi parity ``createReadOnlyTools`` (``core/tools/index.ts:152-165``)."""

    opts = options or {}
    return [
        create_read_tool(cwd, opts.get("read")),
        create_grep_tool(cwd, opts.get("grep")),
        create_find_tool(cwd, opts.get("find")),
        create_ls_tool(cwd, opts.get("ls")),
    ]


def create_all_tools(
    cwd: str, options: dict | None = None
) -> dict[str, AgentTool]:
    """Pi parity ``createAllTools`` (``core/tools/index.ts:167-176``)."""

    opts = options or {}
    return {
        "read": create_read_tool(cwd, opts.get("read")),
        "bash": create_bash_tool(cwd, opts.get("bash")),
        "edit": create_edit_tool(cwd, opts.get("edit")),
        "write": create_write_tool(cwd, opts.get("write")),
        "grep": create_grep_tool(cwd, opts.get("grep")),
        "find": create_find_tool(cwd, opts.get("find")),
        "ls": create_ls_tool(cwd, opts.get("ls")),
    }


__all__ = [
    "ALL_TOOL_NAMES",
    "BashOperations",
    "BashToolDetails",
    "EditOperations",
    "EditToolDetails",
    "FindOperations",
    "FindToolDetails",
    "GrepOperations",
    "GrepToolDetails",
    "LsOperations",
    "LsToolDetails",
    "ReadOperations",
    "ReadToolDetails",
    "ToolName",
    "WriteOperations",
    "create_all_tools",
    "create_bash_tool",
    "create_coding_tools",
    "create_edit_tool",
    "create_find_tool",
    "create_grep_tool",
    "create_local_bash_operations",
    "create_ls_tool",
    "create_read_only_tools",
    "create_read_tool",
    "create_write_tool",
]
