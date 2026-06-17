"""find tool — Pi parity ``coding-agent/src/core/tools/find.ts``."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._path_utils import resolve_to_cwd

_DEFAULT_LIMIT = 1000


@dataclass(frozen=True)
class FindToolDetails:
    """Pi parity ``FindToolDetails``."""

    truncated: bool = False
    result_limit_reached: bool = False


class FindOperations(Protocol):
    """Pi parity ``FindOperations`` Protocol."""

    async def exists(self, path: str) -> bool: ...
    async def glob(self, base: str, pattern: str) -> list[str]: ...


class _LocalFindOperations:
    async def exists(self, path: str) -> bool:
        return Path(path).exists()

    async def glob(self, base: str, pattern: str) -> list[str]:
        return [str(p) for p in Path(base).rglob(pattern) if p.is_file()]


# Pi parity: ``createFindToolDefinition`` (``find.ts``) parameter schema +
# per-field descriptions + Pi's ``number`` type. The top-level description
# omits Pi's "relative paths" + ".gitignore" claims (Aelix returns absolute
# paths and the Python fallback does not honor .gitignore — P0 #3 behavior
# gaps tracked for follow-up).
_FIND_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": (
                "Glob pattern to match files, e.g. '*.ts', '**/*.json', or "
                "'src/**/*.spec.ts'"
            ),
        },
        "path": {
            "type": "string",
            "description": "Directory to search in (default: current directory)",
        },
        "limit": {
            "type": "number",
            "description": "Maximum number of results (default: 1000)",
        },
    },
    "required": ["pattern"],
}


def _try_fd(pattern: str, base: str, limit: int) -> tuple[list[str], bool] | None:
    """Return (results, limit_reached) or None if fd absent/failed.

    W4 MAJOR-3 fix: collect ``limit + 1`` raw lines so we can distinguish
    "exactly the limit" (no overflow) from "more than the limit" (overflow)
    BEFORE slicing.
    """

    fd = shutil.which("fd")
    if fd is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [fd, "--glob", pattern, ".", base],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    raw_lines = proc.stdout.splitlines()
    limit_reached = len(raw_lines) > limit
    return raw_lines[:limit], limit_reached


def create_find_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createFindToolDefinition`` (``find.ts:112-368``)."""

    opts = options or {}
    operations: FindOperations = opts.get("operations") or _LocalFindOperations()

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return ToolResult(
                content=[TextContent(text="find: missing 'pattern'")],
                is_error=True,
            )
        raw_path = args.get("path") or cwd
        base = resolve_to_cwd(raw_path, cwd)
        limit = int(args.get("limit") or _DEFAULT_LIMIT)
        if not await operations.exists(base):
            return ToolResult(
                content=[TextContent(text=f"find: path {base!r} does not exist")],
                is_error=True,
            )
        # W4 MAJOR-3 fix: compute truncation from collected count BEFORE
        # slicing. A result set whose size equals ``limit`` exactly is NOT
        # truncated; only strictly more than ``limit`` is. Pi parity:
        # ``relativized.length >= effectiveLimit`` in Pi works because Pi
        # only knows the post-cap length from fd's ``--max-results``; in
        # Python we collect uncapped from glob and need the strict check.
        fd_result = _try_fd(pattern, base, limit)
        if fd_result is not None:
            results, limit_reached = fd_result
        else:
            collected = await operations.glob(base, pattern)
            limit_reached = len(collected) > limit
            results = collected[:limit]
        return ToolResult(
            content=[TextContent(text="\n".join(results))],
            details=FindToolDetails(
                truncated=limit_reached, result_limit_reached=limit_reached
            ),
        )

    return AgentTool(
        name="find",
        description=(
            "Search for files by glob pattern. Returns matching file paths. "
            "Output is limited to 1000 results by default."
        ),
        parameters=_FIND_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["FindOperations", "FindToolDetails", "create_find_tool"]
