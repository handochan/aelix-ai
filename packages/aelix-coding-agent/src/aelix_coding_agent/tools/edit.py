"""edit tool — Pi parity ``coding-agent/src/core/tools/edit.ts``."""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._file_mutation_queue import (
    with_file_mutation_queue,
)
from aelix_coding_agent.tools._path_utils import resolve_to_cwd


@dataclass(frozen=True)
class EditToolDetails:
    """Pi parity ``EditToolDetails``."""

    diff: str
    first_changed_line: int


class EditOperations(Protocol):
    """Pi parity ``EditOperations`` Protocol."""

    async def read_file(self, path: str) -> bytes: ...
    async def write_file(self, path: str, data: bytes) -> None: ...
    async def access(self, path: str) -> bool: ...


class _LocalEditOperations:
    async def read_file(self, path: str) -> bytes:
        return Path(path).read_bytes()

    async def write_file(self, path: str, data: bytes) -> None:
        Path(path).write_bytes(data)

    async def access(self, path: str) -> bool:
        return os.access(path, os.R_OK | os.W_OK)


_EDIT_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["old_text", "new_text"],
            },
        },
    },
    "required": ["path", "edits"],
}


def _detect_line_ending(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def create_edit_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createEditToolDefinition`` (``edit.ts:288-487``)."""

    opts = options or {}
    operations: EditOperations = opts.get("operations") or _LocalEditOperations()

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        raw_path = args.get("path")
        edits = args.get("edits")
        if not isinstance(raw_path, str) or not raw_path:
            return ToolResult(
                content=[TextContent(text="edit: missing 'path'")],
                is_error=True,
            )
        if not isinstance(edits, list) or not edits:
            return ToolResult(
                content=[TextContent(text="edit: 'edits' must be a non-empty list")],
                is_error=True,
            )
        path = resolve_to_cwd(raw_path, cwd)

        async def _do_edit() -> ToolResult:
            try:
                data = await operations.read_file(path)
            except (FileNotFoundError, OSError) as exc:
                return ToolResult(
                    content=[TextContent(text=f"edit: read failed: {exc}")],
                    is_error=True,
                )
            # Pi parity: detect BOM + line endings.
            bom = b""
            if data.startswith(b"\xef\xbb\xbf"):
                bom = b"\xef\xbb\xbf"
                data = data[3:]
            text = data.decode("utf-8", errors="replace")
            line_ending = _detect_line_ending(text)
            original = text
            new_text = text
            for i, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    return ToolResult(
                        content=[TextContent(text=f"edit: edits[{i}] not a dict")],
                        is_error=True,
                    )
                old = edit.get("old_text", "")
                new = edit.get("new_text", "")
                if not isinstance(old, str) or not isinstance(new, str):
                    return ToolResult(
                        content=[
                            TextContent(text=f"edit: edits[{i}] old/new must be str")
                        ],
                        is_error=True,
                    )
                # Pi parity: each old_text must appear EXACTLY once.
                count = new_text.count(old)
                if count == 0:
                    return ToolResult(
                        content=[
                            TextContent(text=f"edit: edits[{i}] old_text not found")
                        ],
                        is_error=True,
                    )
                if count > 1:
                    return ToolResult(
                        content=[
                            TextContent(
                                text=(
                                    f"edit: edits[{i}] old_text matches {count} times "
                                    "— it must be unique"
                                )
                            )
                        ],
                        is_error=True,
                    )
                new_text = new_text.replace(old, new, 1)
            if new_text == original:
                return ToolResult(
                    content=[TextContent(text="edit: no changes")],
                    details=EditToolDetails(diff="", first_changed_line=-1),
                )
            # Restore line endings + BOM.
            if line_ending != "\n":
                new_text = new_text.replace("\n", line_ending)
            out_bytes = bom + new_text.encode("utf-8")
            try:
                await operations.write_file(path, out_bytes)
            except OSError as exc:
                return ToolResult(
                    content=[TextContent(text=f"edit: write failed: {exc}")],
                    is_error=True,
                )
            diff = "\n".join(
                difflib.unified_diff(
                    original.splitlines(),
                    new_text.replace(line_ending, "\n").splitlines(),
                    fromfile=path,
                    tofile=path,
                    lineterm="",
                )
            )
            # First changed line: walk in parallel until divergence.
            first_changed = -1
            old_lines = original.splitlines()
            new_lines = new_text.replace(line_ending, "\n").splitlines()
            for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=False)):
                if a != b:
                    first_changed = i + 1
                    break
            if first_changed < 0 and len(old_lines) != len(new_lines):
                first_changed = min(len(old_lines), len(new_lines)) + 1
            return ToolResult(
                content=[TextContent(text=diff)],
                details=EditToolDetails(
                    diff=diff, first_changed_line=first_changed
                ),
            )

        return await with_file_mutation_queue(path, _do_edit)

    return AgentTool(
        name="edit",
        description=(
            "Apply one or more (old_text, new_text) edits to a file. "
            "Each old_text must be unique."
        ),
        parameters=_EDIT_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="sequential",
    )


__all__ = ["EditOperations", "EditToolDetails", "create_edit_tool"]
