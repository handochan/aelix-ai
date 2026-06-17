"""edit tool — Pi parity ``coding-agent/src/core/tools/edit.ts`` (734e08e)."""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._edit_diff import (
    EditError,
    apply_edits_to_normalized_content,
    detect_line_ending,
    generate_diff_string,
    normalize_to_lf,
    prepare_edit_arguments,
    restore_line_endings,
    strip_bom,
)
from aelix_coding_agent.tools._file_mutation_queue import (
    with_file_mutation_queue,
)
from aelix_coding_agent.tools._path_utils import resolve_to_cwd


@dataclass(frozen=True)
class EditToolDetails:
    """Pi parity ``EditToolDetails`` — diff + first-changed line for the TUI
    edit card (NOT sent to the model; the model gets the success message)."""

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


# Pi parity: ``createEditToolDefinition`` (``edit.ts``) parameter schema —
# camelCase field names + per-field descriptions, VERBATIM pi text. Edits are
# now matched against the ORIGINAL file content (P0 #3 / ADR-0138), so pi's
# "matched against the original file" wording is restored.
_EDIT_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to edit (relative or absolute)",
        },
        "edits": {
            "type": "array",
            "description": (
                "One or more targeted replacements. Each edit is matched "
                "against the original file, not incrementally. Do not include "
                "overlapping or nested edits. If two changes touch the same "
                "block or nearby lines, merge them into one edit instead."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "oldText": {
                        "type": "string",
                        "description": (
                            "Exact text for one targeted replacement. It must "
                            "be unique in the original file and must not "
                            "overlap with any other edits[].oldText in the "
                            "same call."
                        ),
                    },
                    "newText": {
                        "type": "string",
                        "description": "Replacement text for this targeted edit.",
                    },
                },
                "required": ["oldText", "newText"],
            },
        },
    },
    "required": ["path", "edits"],
}


def create_edit_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createEditToolDefinition`` (``edit.ts``)."""

    opts = options or {}
    operations: EditOperations = opts.get("operations") or _LocalEditOperations()

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        # Pi parity ``prepareArguments``: coerce edits-as-JSON-string + fold
        # legacy top-level oldText/newText into edits.
        args = prepare_edit_arguments(args)
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
        for i, edit in enumerate(edits):
            if (
                not isinstance(edit, dict)
                or not isinstance(edit.get("oldText"), str)
                or not isinstance(edit.get("newText"), str)
            ):
                return ToolResult(
                    content=[
                        TextContent(
                            text=f"edit: edits[{i}] must have str oldText/newText"
                        )
                    ],
                    is_error=True,
                )
        path = resolve_to_cwd(raw_path, cwd)

        async def _do_edit() -> ToolResult:
            # Pi parity ``computeEditsDiff``: access/read failures surface as
            # ``Could not edit file: {path}. Error code: {code}.`` (raw path).
            try:
                data = await operations.read_file(path)
            except OSError as exc:
                code = errno.errorcode.get(exc.errno, str(exc.errno or exc))
                return ToolResult(
                    content=[
                        TextContent(
                            text=f"Could not edit file: {raw_path}. Error code: {code}."
                        )
                    ],
                    is_error=True,
                )
            text = data.decode("utf-8", errors="replace")
            bom, content = strip_bom(text)
            line_ending = detect_line_ending(content)
            normalized = normalize_to_lf(content)
            try:
                base_content, new_content = apply_edits_to_normalized_content(
                    normalized, edits, raw_path
                )
            except EditError as exc:
                return ToolResult(
                    content=[TextContent(text=str(exc))],
                    is_error=True,
                )
            out_text = bom + restore_line_endings(new_content, line_ending)
            try:
                await operations.write_file(path, out_text.encode("utf-8"))
            except OSError as exc:
                code = errno.errorcode.get(exc.errno, str(exc.errno or exc))
                return ToolResult(
                    content=[
                        TextContent(
                            text=f"Could not edit file: {raw_path}. Error code: {code}."
                        )
                    ],
                    is_error=True,
                )
            diff, first_changed = generate_diff_string(base_content, new_content)
            # Pi parity: result CONTENT is the success message; the diff lives
            # only in ``details`` (the TUI edit card reads it from there).
            return ToolResult(
                content=[
                    TextContent(
                        text=f"Successfully replaced {len(edits)} block(s) in {raw_path}."
                    )
                ],
                details=EditToolDetails(diff=diff, first_changed_line=first_changed),
            )

        return await with_file_mutation_queue(path, _do_edit)

    return AgentTool(
        name="edit",
        description=(
            "Edit a single file using exact text replacement. Every "
            "edits[].oldText must match a unique, non-overlapping region of "
            "the original file. If two changes affect the same block or nearby "
            "lines, merge them into one edit instead of emitting overlapping "
            "edits. Do not include large unchanged regions just to connect "
            "distant changes."
        ),
        parameters=_EDIT_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="sequential",
    )


__all__ = ["EditOperations", "EditToolDetails", "create_edit_tool"]
