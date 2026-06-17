"""write tool — Pi parity ``coding-agent/src/core/tools/write.ts``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._file_mutation_queue import (
    with_file_mutation_queue,
)
from aelix_coding_agent.tools._path_utils import resolve_to_cwd


class WriteOperations(Protocol):
    """Pi parity ``WriteOperations`` Protocol."""

    async def write_file(self, path: str, data: bytes) -> None: ...
    async def mkdir(self, path: str) -> None: ...


class _LocalWriteOperations:
    async def write_file(self, path: str, data: bytes) -> None:
        Path(path).write_bytes(data)

    async def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)


# Pi parity: ``createWriteToolDefinition`` (``write.ts``) parameter schema +
# top-level description (Aelix behavior matches Pi here, so the description is
# verbatim).
_WRITE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to write (relative or absolute)",
        },
        "content": {
            "type": "string",
            "description": "Content to write to the file",
        },
    },
    "required": ["path", "content"],
}


def create_write_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createWriteToolDefinition`` (``write.ts:181-281``)."""

    opts = options or {}
    operations: WriteOperations = opts.get("operations") or _LocalWriteOperations()

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        raw_path = args.get("path")
        content = args.get("content")
        if not isinstance(raw_path, str) or not raw_path:
            return ToolResult(
                content=[TextContent(text="write: missing 'path'")],
                is_error=True,
            )
        if not isinstance(content, str):
            return ToolResult(
                content=[TextContent(text="write: 'content' must be str")],
                is_error=True,
            )
        absolute_path = resolve_to_cwd(raw_path, cwd)

        async def _do_write() -> ToolResult:
            parent = str(Path(absolute_path).parent)
            try:
                await operations.mkdir(parent)
                data = content.encode("utf-8")
                await operations.write_file(absolute_path, data)
            except OSError as exc:
                return ToolResult(
                    content=[TextContent(text=f"write: failed: {exc}")],
                    is_error=True,
                )
            # Pi parity: report ``content.length`` (JS UTF-16 code-unit count)
            # to the RAW user-supplied path, not the resolved absolute path.
            length = sum(1 + (ord(c) > 0xFFFF) for c in content)
            return ToolResult(
                content=[
                    TextContent(
                        text=f"Successfully wrote {length} bytes to {raw_path}"
                    )
                ],
            )

        return await with_file_mutation_queue(absolute_path, _do_write)

    return AgentTool(
        name="write",
        description=(
            "Write content to a file. Creates the file if it doesn't exist, "
            "overwrites if it does. Automatically creates parent directories."
        ),
        parameters=_WRITE_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="sequential",
    )


__all__ = ["WriteOperations", "create_write_tool"]
