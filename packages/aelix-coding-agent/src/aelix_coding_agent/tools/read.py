"""read tool — Pi parity ``coding-agent/src/core/tools/read.ts`` (734e08e)."""

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
from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationInfo,
    format_size,
    truncate_head,
)
from aelix_coding_agent.util.image_detect import detect_image_mime_type_from_file


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
        # Pi parity: magic-byte sniff (``detectSupportedImageMimeTypeFromFile``),
        # not an extension map.
        return await detect_image_mime_type_from_file(path)


_READ_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to read (relative or absolute)",
        },
        "offset": {
            "type": "number",
            "description": "Line number to start reading from (1-indexed)",
        },
        "limit": {
            "type": "number",
            "description": "Maximum number of lines to read",
        },
    },
    "required": ["path"],
}


def create_read_tool(cwd: str, options: dict | None = None) -> AgentTool:
    """Pi parity ``createReadToolDefinition`` (``read.ts``)."""

    opts = options or {}
    operations: ReadOperations = opts.get("operations") or _LocalReadOperations()

    async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
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
        if not Path(path).is_file():
            return ToolResult(
                content=[TextContent(text=f"read: {path!r} is not a file")],
                is_error=True,
            )

        # --- image branch (magic-byte MIME). Resize/dimension-note/non-vision
        # handling is a HEAVY follow-up; here the raw image is returned. ---
        mime = await operations.detect_image_mime_type(path)
        if mime is not None:
            data = await operations.read_file(path)
            payload = data if isinstance(data, bytes) else str(data).encode("utf-8")
            encoded = base64.b64encode(payload).decode("ascii")
            return ToolResult(
                content=[
                    TextContent(text=f"[image {Path(path).name}]"),
                    ImageContent(source=f"data:{mime};base64,{encoded}"),
                ],
                details=ReadToolDetails(truncation=TruncationInfo(), mime_type=mime),
            )

        # --- text branch (Pi parity read.ts:282-328) ---
        data = await operations.read_file(path)
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        # Pi parity: split on "\n" ONLY (a trailing newline yields a final empty
        # element) — NOT splitlines().
        all_lines = text.split("\n")
        total_lines = len(all_lines)

        offset_arg = args.get("offset")
        start_line = max(0, int(offset_arg) - 1) if offset_arg else 0
        if start_line >= total_lines:
            return ToolResult(
                content=[
                    TextContent(
                        text=(
                            f"Offset {offset_arg} is beyond end of file "
                            f"({total_lines} lines total)"
                        )
                    )
                ],
                is_error=True,
            )

        limit_arg = args.get("limit")
        limit = int(limit_arg) if limit_arg is not None else None
        if limit is not None:
            end_line = min(start_line + limit, total_lines)
            selected = "\n".join(all_lines[start_line:end_line])
        else:
            end_line = total_lines
            selected = "\n".join(all_lines[start_line:])

        body, info = truncate_head(
            selected, max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES
        )
        start_display = start_line + 1

        # Branch A — the first selected line alone exceeds the byte limit: the
        # body is REPLACED by an actionable notice (pi firstLineExceedsLimit).
        first_line_bytes = (
            len(all_lines[start_line].encode("utf-8")) if selected else 0
        )
        if selected and first_line_bytes > DEFAULT_MAX_BYTES:
            return ToolResult(
                content=[
                    TextContent(
                        text=(
                            f"[Line {start_display} is {format_size(first_line_bytes)}, "
                            f"exceeds {format_size(DEFAULT_MAX_BYTES)} limit. Use bash: "
                            f"sed -n '{start_display}p' {raw_path} | head -c {DEFAULT_MAX_BYTES}]"
                        )
                    )
                ],
                details=ReadToolDetails(truncation=info),
            )

        # Branch B — truncateHead hit a cap (lines or bytes): append a
        # continuation notice; details carry the truncation.
        if info.truncated:
            end_display = start_line + info.kept_lines
            next_offset = end_display + 1
            if info.truncated_by == "bytes":
                notice = (
                    f"\n\n[Showing lines {start_display}-{end_display} of "
                    f"{total_lines} ({format_size(DEFAULT_MAX_BYTES)} limit). "
                    f"Use offset={next_offset} to continue.]"
                )
            else:
                notice = (
                    f"\n\n[Showing lines {start_display}-{end_display} of "
                    f"{total_lines}. Use offset={next_offset} to continue.]"
                )
            return ToolResult(
                content=[TextContent(text=body + notice)],
                details=ReadToolDetails(truncation=info),
            )

        # Branch C — the user's own limit left more lines unread (no truncateHead
        # cap): append a "N more lines" notice; pi leaves details undefined.
        if limit is not None and end_line < total_lines:
            remaining = total_lines - end_line
            next_offset = end_line + 1
            notice = (
                f"\n\n[{remaining} more lines in file. "
                f"Use offset={next_offset} to continue.]"
            )
            return ToolResult(content=[TextContent(text=body + notice)])

        # Branch D — no truncation.
        return ToolResult(content=[TextContent(text=body)])

    return AgentTool(
        name="read",
        description=(
            "Read the contents of a file. Supports text files and images "
            "(jpg, png, gif, webp). Images are sent as attachments. For text "
            "files, output is truncated to 2000 lines or 50KB (whichever is "
            "hit first). Use offset/limit for large files. When you need the "
            "full file, continue with offset until complete."
        ),
        parameters=_READ_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["ReadOperations", "ReadToolDetails", "create_read_tool"]
