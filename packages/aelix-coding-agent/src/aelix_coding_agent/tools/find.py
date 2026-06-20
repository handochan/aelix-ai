"""find tool — Pi parity ``coding-agent/src/core/tools/find.ts``."""

from __future__ import annotations

import asyncio
import os
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
from aelix_coding_agent.tools._subprocess import run_cancellable
from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)
from aelix_coding_agent.util.tools_manager import ensure_tool

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
        # Run the blocking rglob walk in a thread so the event loop is not
        # frozen while scanning the filesystem on the offline/fd-absent path.
        # Note: unlike the fd path (run_cancellable), a cancelled task only
        # interrupts at the next await after the thread returns — the rglob
        # itself cannot be preempted mid-scan.  This is acceptable because the
        # offline fallback is rare (fd absent + no network).
        def _walk() -> list[str]:
            return [str(p) for p in Path(base).rglob(pattern) if p.is_file()]

        return await asyncio.to_thread(_walk)


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


async def _try_fd(
    pattern: str, base: str, limit: int, *, fd_path: str
) -> tuple[list[str], bool] | None:
    """Run ``fd`` Pi-faithfully; return (raw_lines, limit_reached) or None.

    ``fd_path`` is the resolved fd binary (system, cached, or auto-downloaded)
    supplied by :func:`ensure_tool` — guaranteeing fd's ``--no-require-git``
    hierarchical ``.gitignore`` respect (Pi parity).

    Pi parity ``find.ts`` default impl: invoke fd with
    ``--glob --color=never --hidden --no-require-git --max-results <limit>``
    plus the ``--full-path`` logic, then ``-- <pattern> <base>``.

    W4 MAJOR-3 deviation: we request ``--max-results limit + 1`` so we can
    distinguish "exactly the limit" (no overflow) from "more than the limit"
    (overflow) and only flag the strictly-over case as truncated. ``limit``
    of 0 is preserved verbatim (requests 1 raw line; overflow if any match).
    Returns the lines sliced back to ``limit``.

    Uses :func:`~aelix_coding_agent.tools._subprocess.run_cancellable` instead
    of the blocking ``subprocess.run`` so the asyncio event loop stays free
    during the fd call.  This allows ``harness.abort()`` / Esc to deliver
    ``CancelledError`` mid-scan and kill the fd child process immediately,
    rather than waiting for fd to finish before the cancellation is noticed.
    """

    fd = fd_path

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

    result = await run_cancellable(args, timeout=30)
    if result is None:
        return None
    stdout, _rc = result
    raw_lines = stdout.splitlines()
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

        # Pi parity ``find.ts:217``: ``await ensureTool("fd", true)`` — prefer a
        # system/cached/auto-downloaded fd (which respects ``.gitignore`` via
        # ``--no-require-git``). Aelix divergence: when fd is unavailable
        # (offline + absent), fall back to ``rglob`` instead of erroring.
        #
        # W4 MAJOR-3: compute overflow from the collected count BEFORE slicing.
        # A result set whose size equals ``limit`` exactly is NOT truncated;
        # only strictly more than ``limit`` is.
        fd_path = await ensure_tool("fd")
        fd_result = (
            await _try_fd(pattern, base, limit, fd_path=fd_path)
            if fd_path is not None
            else None
        )
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
            "relative to the search directory. Respects .gitignore. Output is "
            "truncated to 1000 results or 50KB (whichever is hit first)."
        ),
        parameters=_FIND_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["FindOperations", "FindToolDetails", "create_find_tool"]
