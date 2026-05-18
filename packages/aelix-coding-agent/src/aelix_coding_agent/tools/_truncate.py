"""Truncation helpers (Pi parity ``core/tools/truncate.ts``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TruncationInfo:
    """Pi parity ``TruncationInfo`` payload for tool details."""

    truncated: bool = False
    original_lines: int = 0
    kept_lines: int = 0
    original_bytes: int = 0
    kept_bytes: int = 0


def truncate_tail(
    text: str, *, max_lines: int, max_bytes: int
) -> tuple[str, TruncationInfo]:
    """Keep the LAST ``max_lines`` lines, then trim to ``max_bytes`` from end."""

    lines = text.split("\n")
    original_lines = len(lines)
    original_bytes = len(text.encode("utf-8"))
    truncated = False
    if original_lines > max_lines:
        lines = lines[-max_lines:]
        truncated = True
    body = "\n".join(lines)
    encoded = body.encode("utf-8")
    if len(encoded) > max_bytes:
        body = encoded[-max_bytes:].decode("utf-8", errors="replace")
        truncated = True
    return body, TruncationInfo(
        truncated=truncated,
        original_lines=original_lines,
        kept_lines=body.count("\n") + 1 if body else 0,
        original_bytes=original_bytes,
        kept_bytes=len(body.encode("utf-8")),
    )


def truncate_head(
    text: str, *, max_lines: int, max_bytes: int
) -> tuple[str, TruncationInfo]:
    """Keep the FIRST ``max_lines`` lines and ``max_bytes`` bytes."""

    lines = text.split("\n")
    original_lines = len(lines)
    original_bytes = len(text.encode("utf-8"))
    truncated = False
    if original_lines > max_lines:
        lines = lines[:max_lines]
        truncated = True
    body = "\n".join(lines)
    encoded = body.encode("utf-8")
    if len(encoded) > max_bytes:
        body = encoded[:max_bytes].decode("utf-8", errors="replace")
        truncated = True
    return body, TruncationInfo(
        truncated=truncated,
        original_lines=original_lines,
        kept_lines=body.count("\n") + 1 if body else 0,
        original_bytes=original_bytes,
        kept_bytes=len(body.encode("utf-8")),
    )


def truncate_line(line: str, max_length: int) -> str:
    """Truncate a single line to ``max_length`` characters."""

    if len(line) <= max_length:
        return line
    return line[:max_length] + "… [truncated]"


def format_size(n: int) -> str:
    """Pi parity ``formatSize`` — human-readable byte count."""

    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


__all__ = [
    "TruncationInfo",
    "format_size",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
]
