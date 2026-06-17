"""find tool — Pi parity ``coding-agent/src/core/tools/find.ts``."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._path_utils import (
    relativize_to_posix,
    resolve_to_cwd,
)
from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)

_DEFAULT_LIMIT = 1000
# Pi parity: ``truncateHead(rawOutput, { maxLines: Number.MAX_SAFE_INTEGER })``
# — only the byte cap bounds find output (line cap is effectively disabled).
_MAX_LINES = 2**53 - 1


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
# per-field descriptions + Pi's ``number`` type.
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
    """Run ``fd`` Pi-faithfully; return (raw_lines, limit_reached) or None.

    Pi parity ``find.ts`` default impl: invoke fd with
    ``--glob --color=never --hidden --no-require-git --max-results <limit>``
    plus the ``--full-path`` logic, then ``-- <pattern> <base>``.

    W4 MAJOR-3 deviation: we request ``--max-results limit + 1`` so we can
    distinguish "exactly the limit" (no overflow) from "more than the limit"
    (overflow) and only flag the strictly-over case as truncated. ``limit``
    of 0 is preserved verbatim (requests 1 raw line; overflow if any match).
    Returns the lines sliced back to ``limit``.
    """

    fd = shutil.which("fd")
    if fd is None:
        return None

    # Pi parity fd argv. Request limit + 1 to detect overflow before slicing.
    args = [
        fd,
        "--glob",
        "--color=never",
        "--hidden",
        "--no-require-git",
        "--max-results",
        str(limit + 1),
    ]

    # fd --glob matches the basename unless --full-path is set; in --full-path
    # mode it matches the absolute candidate path, so a path-containing pattern
    # like 'src/**/*.spec.ts' needs a leading '**/' to match anything.
    effective_pattern = pattern
    if "/" in pattern:
        args.append("--full-path")
        if (
            not pattern.startswith("/")
            and not pattern.startswith("**/")
            and pattern != "**"
        ):
            effective_pattern = f"**/{pattern}"
    args += ["--", effective_pattern, base]

    try:
        proc = subprocess.run(  # noqa: S603
            args,
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


def _relativize(line: str, base: str) -> str | None:
    """Pi parity per-line relativization (``find.ts`` close handler).

    Strips a trailing ``\\r``, trims, skips blanks. If the line starts with
    ``base`` strip ``base`` + separator; otherwise relativize to POSIX.
    Preserves a trailing slash that the original line carried, and converts
    OS separators to ``/``. Returns ``None`` for blank lines.
    """

    stripped = line.rstrip("\r").strip()
    if not stripped:
        return None
    had_trailing_slash = stripped.endswith("/") or stripped.endswith("\\")
    if stripped.startswith(base):
        relative_path = stripped[len(base) + 1 :]
    else:
        relative_path = relativize_to_posix(stripped, base)
    relative_path = relative_path.replace(os.sep, "/")
    if had_trailing_slash and not relative_path.endswith("/"):
        relative_path += "/"
    return relative_path


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
        # Pi parity: ``effectiveLimit = limit ?? DEFAULT_LIMIT``. ``is None``
        # preserves a caller-supplied ``limit=0`` (no clamp to the default).
        raw_limit = args.get("limit")
        limit = _DEFAULT_LIMIT if raw_limit is None else int(raw_limit)
        if not await operations.exists(base):
            return ToolResult(
                content=[TextContent(text=f"Path not found: {base}")],
                is_error=True,
            )

        # W4 MAJOR-3: compute overflow from the collected count BEFORE slicing.
        # A result set whose size equals ``limit`` exactly is NOT truncated;
        # only strictly more than ``limit`` is.
        fd_result = _try_fd(pattern, base, limit)
        if fd_result is not None:
            raw_lines, limit_reached = fd_result
        else:
            collected = await operations.glob(base, pattern)
            limit_reached = len(collected) > limit
            raw_lines = collected[:limit]

        relativized: list[str] = []
        for raw_line in raw_lines:
            rel = _relativize(raw_line, base)
            if rel is not None:
                relativized.append(rel)

        if not relativized:
            return ToolResult(
                content=[TextContent(text="No files found matching pattern")],
            )

        raw_output = "\n".join(relativized)
        body, info = truncate_head(
            raw_output, max_lines=_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES
        )
        result_output = body

        notices: list[str] = []
        if limit_reached:
            notices.append(
                f"{limit} results limit reached. "
                f"Use limit={limit * 2} for more, or refine pattern"
            )
        if info.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        if notices:
            result_output += f"\n\n[{'. '.join(notices)}]"

        return ToolResult(
            content=[TextContent(text=result_output)],
            details=FindToolDetails(
                truncated=limit_reached or info.truncated,
                result_limit_reached=limit_reached,
            ),
        )

    return AgentTool(
        name="find",
        description=(
            "Search for files by glob pattern. Returns matching file paths "
            "relative to the search directory. Output is truncated to 1000 "
            "results or 50KB (whichever is hit first)."
        ),
        parameters=_FIND_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["FindOperations", "FindToolDetails", "create_find_tool"]
