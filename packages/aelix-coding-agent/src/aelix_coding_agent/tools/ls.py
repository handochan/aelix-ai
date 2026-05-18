"""ls tool — Pi parity ``coding-agent/src/core/tools/ls.ts``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._path_utils import resolve_to_cwd

_DEFAULT_LIMIT = 500


@dataclass(frozen=True)
class LsToolDetails:
    """Pi parity ``LsToolDetails``."""

    truncated: bool = False
    entry_limit_reached: bool = False


class LsOperations(Protocol):
    """Pi parity ``LsOperations`` Protocol."""

    async def exists(self, path: str) -> bool: ...
    async def stat(self, path: str) -> Any: ...
    async def readdir(self, path: str) -> list[str]: ...


class _LocalLsOperations:
    async def exists(self, path: str) -> bool:
        return Path(path).exists()

    async def stat(self, path: str) -> Any:
        return Path(path).stat()

    async def readdir(self, path: str) -> list[str]:
        return [p.name for p in Path(path).iterdir()]


_LS_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": [],
}


def create_ls_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createLsToolDefinition`` (``ls.ts:99-227``)."""

    opts = options or {}
    operations: LsOperations = opts.get("operations") or _LocalLsOperations()

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        raw_path = args.get("path") or cwd
        base = resolve_to_cwd(raw_path, cwd)
        limit = int(args.get("limit") or _DEFAULT_LIMIT)
        if not await operations.exists(base):
            return ToolResult(
                content=[TextContent(text=f"ls: {base!r} does not exist")],
                is_error=True,
            )
        try:
            names = await operations.readdir(base)
        except OSError as exc:
            return ToolResult(
                content=[TextContent(text=f"ls: {exc}")],
                is_error=True,
            )
        base_p = Path(base)
        entries: list[str] = []
        for name in sorted(names):
            sub = base_p / name
            entries.append(f"{name}/" if sub.is_dir() else name)
        limit_reached = len(entries) >= limit
        entries = entries[:limit]
        return ToolResult(
            content=[TextContent(text="\n".join(entries))],
            details=LsToolDetails(
                truncated=limit_reached, entry_limit_reached=limit_reached
            ),
        )

    return AgentTool(
        name="ls",
        description="List entries in a directory.",
        parameters=_LS_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["LsOperations", "LsToolDetails", "create_ls_tool"]
