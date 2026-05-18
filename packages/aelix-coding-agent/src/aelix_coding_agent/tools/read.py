"""read tool — Pi parity ``coding-agent/src/core/tools/read.ts``."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import ImageContent, TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._path_utils import resolve_read_path
from aelix_coding_agent.tools._truncate import TruncationInfo


@dataclass(frozen=True)
class ReadToolDetails:
    """Pi parity ``ReadToolDetails``."""

    truncation: TruncationInfo
    mime_type: str | None = None


class ReadOperations(Protocol):
    """Pi parity ``ReadOperations`` Protocol."""

    async def read_file(self, path: str) -> bytes: ...
    async def access(self, path: str) -> bool: ...
    async def detect_image_mime_type(self, path: str) -> str | None: ...


class _LocalReadOperations:
    async def read_file(self, path: str) -> bytes:
        return Path(path).read_bytes()

    async def access(self, path: str) -> bool:
        return os.access(path, os.R_OK)

    async def detect_image_mime_type(self, path: str) -> str | None:
        ext = Path(path).suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext)


_READ_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer"},
        "limit": {"type": "integer"},
    },
    "required": ["path"],
}


def create_read_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createReadToolDefinition`` (``read.ts:206-363``)."""

    opts = options or {}
    operations: ReadOperations = opts.get("operations") or _LocalReadOperations()
    default_limit = int(opts.get("default_limit", 2000))

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return ToolResult(
                content=[TextContent(text="read: missing 'path'")],
                is_error=True,
            )
        path = resolve_read_path(raw_path, cwd)
        if not await operations.access(path):
            return ToolResult(
                content=[TextContent(text=f"read: cannot access {path!r}")],
                is_error=True,
            )
        p = Path(path)
        if not p.is_file():
            return ToolResult(
                content=[TextContent(text=f"read: {path!r} is not a file")],
                is_error=True,
            )
        mime = await operations.detect_image_mime_type(path)
        if mime is not None:
            data = await operations.read_file(path)
            # W4 MAJOR-2 fix: Pi parity (``read.ts`` ``buffer.toString("base64")``)
            # encodes image bytes as a ``data:<mime>;base64,...`` URL so
            # downstream multimodal LLM consumers can decode it directly.
            payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
            encoded = base64.b64encode(payload).decode("ascii")
            source = f"data:{mime};base64,{encoded}"
            return ToolResult(
                content=[
                    TextContent(text=f"[image {Path(path).name}]"),
                    ImageContent(source=source),
                ],
                details=ReadToolDetails(
                    truncation=TruncationInfo(), mime_type=mime
                ),
            )
        data = await operations.read_file(path)
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        lines = text.splitlines()
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or default_limit)
        original_lines = len(lines)
        sliced = lines[offset : offset + limit]
        truncated = (offset + limit) < original_lines or offset > 0
        # Pi parity: cat -n style line numbering.
        numbered = "\n".join(
            f"{i + offset + 1:6}\t{line}" for i, line in enumerate(sliced)
        )
        info = TruncationInfo(
            truncated=truncated,
            original_lines=original_lines,
            kept_lines=len(sliced),
        )
        return ToolResult(
            content=[TextContent(text=numbered)],
            details=ReadToolDetails(truncation=info),
        )

    return AgentTool(
        name="read",
        description="Read a file. Supports offset+limit for paging.",
        parameters=_READ_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["ReadOperations", "ReadToolDetails", "create_read_tool"]
