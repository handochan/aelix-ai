"""grep tool — Pi parity ``coding-agent/src/core/tools/grep.ts``."""

from __future__ import annotations

import os
import re
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
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line,
)

_DEFAULT_LIMIT = 100
# Pi parity: ``truncateHead`` is called with ``maxLines: Number.MAX_SAFE_INTEGER``
# so the 50KB byte cap (``DEFAULT_MAX_BYTES``) is the only bound that fires.
_NO_LINE_CAP = 1 << 62


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


# Pi parity: ``createGrepToolDefinition`` (``grep.ts``) parameter schema —
# camelCase ``ignoreCase`` + per-field descriptions + Pi's ``number`` types.
# The top-level description omits Pi's ".gitignore" claim (Aelix's pure-Python
# fallback does not honor .gitignore — a P0 #3 behavior gap) and states the
# actual 250-char line cap.
_GREP_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Search pattern (regex or literal string)",
        },
        "path": {
            "type": "string",
            "description": "Directory or file to search (default: current directory)",
        },
        "glob": {
            "type": "string",
            "description": "Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'",
        },
        "ignoreCase": {
            "type": "boolean",
            "description": "Case-insensitive search (default: false)",
        },
        "literal": {
            "type": "boolean",
            "description": (
                "Treat pattern as literal string instead of regex "
                "(default: false)"
            ),
        },
        "context": {
            "type": "number",
            "description": (
                "Number of lines to show before and after each match "
                "(default: 0)"
            ),
        },
        "limit": {
            "type": "number",
            "description": "Maximum number of matches to return (default: 100)",
        },
    },
    "required": ["pattern"],
}


# rg text-mode remainder after the path: ``<sep>lineno<sep>content`` (``:`` for
# match lines, ``-`` for context). Pi inserts a single space before content
# (``:N: text`` / ``-N- text``); the digit guard avoids mangling odd lines.
_RG_REMAINDER_RE = re.compile(r"^([:-])(\d+)([:-])(.*)$", re.DOTALL)


def _space_after_lineno(remainder: str) -> str:
    """Pi parity ``formatBlock``: add the space before content in an rg
    ``<sep>lineno<sep>content`` remainder. Returns it unchanged if it doesn't
    match (e.g. ``--`` group separators or unexpected shapes)."""

    m = _RG_REMAINDER_RE.match(remainder)
    if not m:
        return remainder
    return f"{m.group(1)}{m.group(2)}{m.group(3)} {m.group(4)}"


def _relativize_rg_line(line: str, base: str, *, is_directory: bool) -> str:
    """Pi parity ``formatPath`` applied to a single rg text-mode output line.

    rg text-mode lines are ``<abspath>:<lineno>:<content>`` (or ``-`` separated
    for context lines); ``--`` group separators carry no path. When ``base`` is
    a directory, strip the ``base`` prefix so the path is relative+POSIX (no
    ``./``); when ``base`` is a file (or the match is outside ``base``), fall
    back to the basename — matching pi's ``path.relative`` / ``path.basename``.
    """

    if line == "--":
        return line
    # rg emits ``path:lineno:content`` for matches and ``path-lineno-content``
    # for context lines. Split off the leading absolute path on the first
    # separator that follows the (possibly ``:``-containing on Windows) path.
    for sep in (":", "-"):
        idx = line.find(sep)
        if idx <= 0:
            continue
        candidate = line[:idx]
        # Only treat the prefix as a path when it actually points under/at base.
        if is_directory:
            if candidate == base or candidate.startswith(base + "/"):
                rel = relativize_to_posix(candidate, base)
                return rel + _space_after_lineno(line[idx:])
        else:
            if candidate == base:
                return os.path.basename(candidate) + _space_after_lineno(line[idx:])
    return line


def _try_ripgrep(
    pattern: str,
    base: str,
    *,
    glob: str | None,
    ignore_case: bool,
    literal: bool,
    context: int,
    limit: int,
    is_directory: bool,
) -> tuple[str, bool, int] | None:
    """Return (output, limit_reached, lines_truncated) or None if rg absent/failed."""

    rg = shutil.which("rg")
    if rg is None:
        return None
    # Pi parity ``grep.ts`` argv: ``--hidden`` searches dotfiles; rg's default
    # ``.gitignore`` respect is intentionally kept (pi does NOT pass --no-ignore).
    cmd = [rg, "--line-number", "--no-heading", "--color=never", "--hidden"]
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
        rel = _relativize_rg_line(ln, base, is_directory=is_directory)
        if len(rel) > GREP_MAX_LINE_LENGTH:
            lines_trimmed += 1
            out_lines.append(truncate_line(rel, GREP_MAX_LINE_LENGTH))
        else:
            out_lines.append(rel)
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
    is_directory: bool,
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
        # Pi parity ``formatPath``: relative+POSIX under a directory base,
        # basename when the search path is a single file.
        display_path = (
            relativize_to_posix(str(f), base)
            if is_directory
            else os.path.basename(str(f))
        )
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if rx.search(line):
                if matched >= limit:
                    break
                start = max(0, i - context)
                end = min(len(lines), i + context + 1)
                for j in range(start, end):
                    # Pi parity ``formatBlock``: the MATCH line uses ``:``
                    # separators, CONTEXT lines use ``-``; content is preceded
                    # by a single space (``path:N: text`` / ``path-N- text``).
                    sep = ":" if j == i else "-"
                    rendered = f"{display_path}{sep}{j + 1}{sep} {lines[j]}"
                    if len(rendered) > GREP_MAX_LINE_LENGTH:
                        lines_trimmed += 1
                        rendered = truncate_line(rendered, GREP_MAX_LINE_LENGTH)
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
        ignore_case = bool(args.get("ignoreCase", False))
        literal = bool(args.get("literal", False))
        context = int(args.get("context") or 0)
        # Pi parity: ``effectiveLimit = Math.max(1, limit ?? DEFAULT_LIMIT)``.
        # ``is None`` (not ``or``) so an explicit ``limit=0`` still maps via the
        # ``max(1, …)`` floor rather than being coerced back to the default.
        limit_arg = args.get("limit")
        effective_limit = max(1, limit_arg if limit_arg is not None else _DEFAULT_LIMIT)
        # Pi parity ``formatPath``: relativize against the search path only when
        # it is a directory; a single-file search uses the basename.
        is_directory = Path(base).is_dir()

        rg_result = _try_ripgrep(
            pattern,
            base,
            glob=glob_filter,
            ignore_case=ignore_case,
            literal=literal,
            context=context,
            limit=effective_limit,
            is_directory=is_directory,
        )
        if rg_result is not None:
            raw_output, limit_reached, lines_truncated = rg_result
        else:
            raw_output, limit_reached, lines_truncated = _python_grep(
                pattern,
                base,
                glob=glob_for_python,
                ignore_case=ignore_case,
                literal=literal,
                context=context,
                limit=effective_limit,
                is_directory=is_directory,
            )

        # Pi parity: ``truncateHead(rawOutput, { maxLines: MAX_SAFE_INTEGER })``
        # so only the 50KB byte cap can bind.
        output, truncation = truncate_head(
            raw_output, max_lines=_NO_LINE_CAP, max_bytes=DEFAULT_MAX_BYTES
        )

        # Pi parity notice ordering: matches-limit, then bytes-limit, then
        # lines-truncated — joined by ``. `` inside one ``[…]`` block.
        notices: list[str] = []
        if limit_reached:
            notices.append(
                f"{effective_limit} matches limit reached. "
                f"Use limit={effective_limit * 2} for more, or refine pattern"
            )
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
        if lines_truncated:
            notices.append(
                f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. "
                "Use read tool to see full lines"
            )
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"

        return ToolResult(
            content=[TextContent(text=output)],
            details=GrepToolDetails(
                truncated=limit_reached or truncation.truncated or lines_truncated > 0,
                match_limit_reached=limit_reached,
                lines_truncated=lines_truncated,
            ),
        )

    return AgentTool(
        name="grep",
        description=(
            "Search file contents for a pattern. Returns matching lines with "
            "file paths and line numbers. Output is truncated to 100 matches "
            "or 50KB (whichever is hit first). Long lines are truncated to 500 "
            "characters."
        ),
        parameters=_GREP_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="parallel",
    )


__all__ = ["GrepOperations", "GrepToolDetails", "create_grep_tool"]
