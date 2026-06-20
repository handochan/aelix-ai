"""ls tool — Pi parity ``coding-agent/src/core/tools/ls.ts``."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._path_utils import resolve_to_cwd
from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    format_size,
    truncate_head,
)

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
        return await asyncio.to_thread(Path(path).exists)

    async def stat(self, path: str) -> Any:
        return await asyncio.to_thread(Path(path).stat)

    async def readdir(self, path: str) -> list[str]:
        return await asyncio.to_thread(lambda: [p.name for p in Path(path).iterdir()])


# Pi parity: ``createLsToolDefinition`` (``ls.ts``) parameter schema +
# per-field descriptions + Pi's ``number`` type. Aelix's ls behavior matches
# Pi (alphabetical sort, '/' dir suffix, dotfiles included), so the top-level
# description follows Pi's wording.
_LS_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Directory to list (default: current directory)",
        },
        "limit": {
            "type": "number",
            "description": "Maximum number of entries to return (default: 500)",
        },
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
        # Pi parity: ``limit ?? DEFAULT_LIMIT`` (nullish, not falsy) — an
        # explicit ``limit=0`` is preserved (yields an empty listing), NOT
        # coalesced to the default.
        raw_limit = args.get("limit")
        limit = int(raw_limit) if raw_limit is not None else _DEFAULT_LIMIT
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
        # Pi parity (``ls.ts``): case-insensitive sort
        # (``a.toLowerCase().localeCompare(b.toLowerCase())``) with a stable
        # secondary key for deterministic ordering of case-variant names.
        sorted_names = sorted(names, key=lambda n: (n.casefold(), n))
        # Pi parity: build results incrementally, breaking at the entry limit
        # BEFORE the per-entry stat call, and skipping (``continue``) entries we
        # cannot stat so they do not count toward the limit.
        results: list[str] = []
        entry_limit_reached = False
        for name in sorted_names:
            if len(results) >= limit:
                entry_limit_reached = True
                break
            sub = base_p / name
            try:
                is_dir = await asyncio.to_thread(sub.is_dir)
            except OSError:
                # Skip entries we cannot stat.
                continue
            results.append(f"{name}/" if is_dir else name)
        # Pi parity (``ls.ts``): empty result (truly empty dir, or limit=0)
        # short-circuits with the sentinel message and no details, BEFORE the
        # byte cap / notice assembly.
        if not results:
            return ToolResult(
                content=[TextContent(text="(empty directory)")],
                details=None,
            )
        # Pi parity: cap assembled output to ``DEFAULT_MAX_BYTES`` (50KB) with an
        # effectively unbounded line cap; the description's '1024KB' is a pi doc
        # bug — the code value is 50KB.
        output, truncation = truncate_head(
            "\n".join(results),
            max_lines=sys.maxsize,
            max_bytes=DEFAULT_MAX_BYTES,
        )
        # Pi parity: append actionable notices (entry-limit first, then byte
        # limit) joined with '. ' inside a single ``\n\n[...]`` block.
        notices: list[str] = []
        if entry_limit_reached:
            notices.append(
                f"{limit} entries limit reached. Use limit={limit * 2} for more"
            )
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"
        return ToolResult(
            content=[TextContent(text=output)],
            details=LsToolDetails(
                truncated=truncation.truncated,
                entry_limit_reached=entry_limit_reached,
            ),
        )

    return AgentTool(
        name="ls",
        description=(
            "List directory contents. Returns entries sorted alphabetically, "
            "with a '/' suffix for directories. Includes dotfiles. Output is "
            "truncated to 500 entries or 50KB (whichever is hit first)."
        ),
        parameters=_LS_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["LsOperations", "LsToolDetails", "create_ls_tool"]
