"""grep tool — Pi parity ``coding-agent/src/core/tools/grep.ts``."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._path_utils import resolve_to_cwd
from aelix_coding_agent.tools._truncate import truncate_line

_DEFAULT_LIMIT = 100
_GREP_MAX_LINE_LENGTH = 250


@dataclass(frozen=True)
class GrepToolDetails:
    """Pi parity ``GrepToolDetails``."""

    truncated: bool = False
    match_limit_reached: bool = False
    lines_truncated: int = 0


class GrepOperations(Protocol):
    """Pi parity ``GrepOperations`` Protocol."""

    async def is_directory(self, path: str) -> bool: ...
    async def read_file(self, path: str) -> bytes: ...


class _LocalGrepOperations:
    async def is_directory(self, path: str) -> bool:
        return Path(path).is_dir()

    async def read_file(self, path: str) -> bytes:
        return Path(path).read_bytes()


_GREP_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "path": {"type": "string"},
        "glob": {"type": "string"},
        "ignore_case": {"type": "boolean"},
        "literal": {"type": "boolean"},
        "context": {"type": "integer"},
        "limit": {"type": "integer"},
    },
    "required": ["pattern"],
}


def _try_ripgrep(
    pattern: str,
    base: str,
    *,
    glob: str | None,
    ignore_case: bool,
    literal: bool,
    context: int,
    limit: int,
) -> tuple[str, bool, int] | None:
    """Return (output, limit_reached, lines_truncated) or None if rg absent/failed."""

    rg = shutil.which("rg")
    if rg is None:
        return None
    cmd = [rg, "--line-number", "--no-heading", "--color=never"]
    if ignore_case:
        cmd.append("-i")
    if literal:
        cmd.append("-F")
    if context > 0:
        cmd.extend(["-C", str(context)])
    if glob:
        cmd.extend(["-g", glob])
    cmd.extend([pattern, base])
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    raw_lines = proc.stdout.splitlines()
    limit_reached = len(raw_lines) > limit
    raw_lines = raw_lines[:limit]
    lines_trimmed = 0
    out_lines: list[str] = []
    for ln in raw_lines:
        if len(ln) > _GREP_MAX_LINE_LENGTH:
            lines_trimmed += 1
            out_lines.append(truncate_line(ln, _GREP_MAX_LINE_LENGTH))
        else:
            out_lines.append(ln)
    return "\n".join(out_lines), limit_reached, lines_trimmed


def _python_grep(
    pattern: str,
    base: str,
    *,
    glob: str,
    ignore_case: bool,
    literal: bool,
    context: int,
    limit: int,
) -> tuple[str, bool, int]:
    flags = re.IGNORECASE if ignore_case else 0
    needle = re.escape(pattern) if literal else pattern
    try:
        rx = re.compile(needle, flags)
    except re.error:
        return ("", False, 0)
    out_lines: list[str] = []
    matched = 0
    lines_trimmed = 0
    base_p = Path(base)
    iterator = base_p.rglob(glob) if base_p.is_dir() else [base_p]
    for f in iterator:
        if matched >= limit:
            break
        if not f.is_file():
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if rx.search(line):
                if matched >= limit:
                    break
                start = max(0, i - context)
                end = min(len(lines), i + context + 1)
                for j in range(start, end):
                    rendered = f"{f}:{j + 1}:{lines[j]}"
                    if len(rendered) > _GREP_MAX_LINE_LENGTH:
                        lines_trimmed += 1
                        rendered = truncate_line(rendered, _GREP_MAX_LINE_LENGTH)
                    out_lines.append(rendered)
                matched += 1
    limit_reached = matched >= limit
    return "\n".join(out_lines), limit_reached, lines_trimmed


def create_grep_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createGrepToolDefinition`` (``grep.ts:122-384``).

    ``options`` is accepted for parity with the other tool factories — the
    grep tool itself takes all knobs via per-call ``args``.
    """

    _ = options  # parity-only; grep accepts knobs per-call via args.

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return ToolResult(
                content=[TextContent(text="grep: missing 'pattern'")],
                is_error=True,
            )
        raw_path = args.get("path") or cwd
        base = resolve_to_cwd(raw_path, cwd)
        # W4 MAJOR-1 fix: route a single glob value to BOTH branches.
        # Pi parity: rg `-g <glob>` is only added when the user passed one
        # (Pi forwards the optional `glob` field verbatim — no default). The
        # Python fallback needs *some* iterable; absent a user glob, fall back
        # to ``**/*`` which is rglob-equivalent to "everything".
        glob_raw = args.get("glob")
        glob_filter: str | None = glob_raw if isinstance(glob_raw, str) and glob_raw else None
        glob_for_python = glob_filter if glob_filter is not None else "**/*"
        ignore_case = bool(args.get("ignore_case", False))
        literal = bool(args.get("literal", False))
        context = int(args.get("context") or 0)
        limit = int(args.get("limit") or _DEFAULT_LIMIT)

        rg_result = _try_ripgrep(
            pattern,
            base,
            glob=glob_filter,
            ignore_case=ignore_case,
            literal=literal,
            context=context,
            limit=limit,
        )
        if rg_result is not None:
            output, limit_reached, lines_truncated = rg_result
        else:
            output, limit_reached, lines_truncated = _python_grep(
                pattern,
                base,
                glob=glob_for_python,
                ignore_case=ignore_case,
                literal=literal,
                context=context,
                limit=limit,
            )
        return ToolResult(
            content=[TextContent(text=output)],
            details=GrepToolDetails(
                truncated=limit_reached or lines_truncated > 0,
                match_limit_reached=limit_reached,
                lines_truncated=lines_truncated,
            ),
        )

    return AgentTool(
        name="grep",
        description="Search for a pattern in files (ripgrep when available).",
        parameters=_GREP_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["GrepOperations", "GrepToolDetails", "create_grep_tool"]
