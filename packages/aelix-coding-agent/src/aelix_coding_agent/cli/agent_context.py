"""Coding-agent system prompt + AGENTS.md context discovery (Sprint 6h₁₁).

The CLI harness previously ran with an EMPTY ``system_prompt`` and zero ``tools``
— a bare chat model with no coding-agent identity (it could not read or edit
files and had no idea it was a coding agent). This module supplies:

- :func:`build_system_prompt` — the base coding-agent system prompt (identity +
  environment + tool-use guidance), injected unless ``--system-prompt`` overrides.
- :func:`discover_context_files` — auto-discovered ``AGENTS.md`` project context
  (Pi ``--no-context-files`` / ``-nc`` gate), walked from the cwd up to the
  filesystem root and appended to the system prompt.

Tools themselves are wired in :func:`aelix_coding_agent.cli.entry._build_harness_options`
via :func:`aelix_coding_agent.tools.create_all_tools`.
"""

from __future__ import annotations

import os
import platform
from datetime import UTC, datetime
from pathlib import Path

_CONTEXT_FILENAME = "AGENTS.md"
_MAX_CONTEXT_BYTES = 32_768


def build_system_prompt(cwd: str) -> str:
    """The base coding-agent system prompt (identity + environment + tools)."""

    cwd_abs = os.path.abspath(cwd)
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    return (
        "You are Aelix, an interactive CLI coding agent. You help the user with "
        "software-engineering tasks directly in their terminal and working "
        "directory.\n\n"
        "You act by USING TOOLS to inspect and modify the codebase — you do the "
        "work, you do not merely describe it. Available tools: read (read a file), "
        "write (create or overwrite a file), edit (precise string replacements), "
        "bash (run a shell command), grep (search file contents), find (find files "
        "by name), ls (list a directory).\n\n"
        "Guidelines:\n"
        "- Be concise and direct. Prefer doing over explaining; lead with results.\n"
        "- Use tools to gather context before answering questions about the code — "
        "read files and run commands rather than guessing at their contents.\n"
        "- Make the smallest change that solves the problem and match the "
        "surrounding code's style and conventions.\n"
        "- After editing, verify your work (run the relevant tests or build via "
        "bash when appropriate).\n"
        "- Never invent file paths, APIs, or command output — read or run to "
        "confirm before relying on them.\n"
        "- Be careful with destructive or irreversible shell commands; do not run "
        "them unless the intent is clear.\n\n"
        "Converging to an answer:\n"
        "- When you have gathered enough information, STOP calling tools and give "
        "your final answer directly. Tools gather context; they are not the "
        "answer.\n"
        "- Never call the same tool with the same arguments twice. If a tool "
        "already returned a result, use that result — do not re-run it hoping for "
        "something different.\n"
        "- If a request is ambiguous, answer with your best interpretation (and "
        "state the assumption) rather than looping or gathering data "
        "indefinitely. (A single clarifying lookup is fine; endless re-fetching "
        "is not.)\n"
        "- Prefer the fewest tool calls that get the job done; once you can "
        "answer, answer.\n\n"
        "Environment:\n"
        f"- Working directory: {cwd_abs}\n"
        f"- Platform: {platform.system()}\n"
        f"- Today's date: {today}\n"
    )


def discover_context_files(cwd: str) -> str:
    """Concatenate ``AGENTS.md`` files from cwd up to the filesystem root.

    Returns ``""`` when none are found. Root-most context comes first and the
    cwd-most last (nearer = more specific). Total content is capped at
    :data:`_MAX_CONTEXT_BYTES` to bound the prompt size.
    """

    here = Path(os.path.abspath(cwd))
    found: list[tuple[Path, str]] = []
    for directory in [here, *here.parents]:
        path = directory / _CONTEXT_FILENAME
        if not path.is_file():
            continue
        try:
            # ``UnicodeDecodeError`` is a ValueError, NOT an OSError — a stray
            # binary file named AGENTS.md on the walk-up path must be skipped,
            # not crash CLI startup.
            found.append((path, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    if not found:
        return ""

    found.reverse()  # root-most first, cwd-most last
    parts: list[str] = []
    total = 0
    for path, text in found:
        chunk = f"# Project context ({path})\n\n{text.strip()}\n"
        chunk_bytes = chunk.encode("utf-8")
        remaining = _MAX_CONTEXT_BYTES - total
        if len(chunk_bytes) > remaining:
            # Truncate to the remaining budget (decode-safe) rather than dropping
            # the whole chunk — a large root AGENTS.md still contributes context.
            if remaining > 0:
                parts.append(chunk_bytes[:remaining].decode("utf-8", errors="ignore"))
            break
        parts.append(chunk)
        total += len(chunk_bytes)
    return "\n".join(parts)


__all__ = ["build_system_prompt", "discover_context_files"]
