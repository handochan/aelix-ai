"""Truncation helpers (Pi parity ``core/tools/truncate.ts``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Pi parity ``core/tools/truncate.ts`` shared constants (SHA 734e08e):
# ``DEFAULT_MAX_LINES``/``DEFAULT_MAX_BYTES`` feed the OutputAccumulator used by
# bash/read/grep/find/ls; ``GREP_MAX_LINE_LENGTH`` is grep's per-line cap.
DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


@dataclass(frozen=True)
class TruncationInfo:
    """Pi parity ``TruncationInfo`` payload for tool details.

    ``truncated_by`` reports WHICH cap bound (``"lines"`` or ``"bytes"``) so
    callers can phrase a pi-faithful truncation notice; ``None`` when not
    truncated. Byte truncation (applied last, the tighter cut) wins when both
    caps fire.
    """

    truncated: bool = False
    truncated_by: Literal["lines", "bytes"] | None = None
    last_line_partial: bool = False
    original_lines: int = 0
    kept_lines: int = 0
    original_bytes: int = 0
    kept_bytes: int = 0


def _untruncated(original_lines: int, original_bytes: int) -> TruncationInfo:
    return TruncationInfo(
        truncated=False,
        original_lines=original_lines,
        kept_lines=original_lines,
        original_bytes=original_bytes,
        kept_bytes=original_bytes,
    )


def truncate_tail(
    text: str, *, max_lines: int, max_bytes: int
) -> tuple[str, TruncationInfo]:
    """Keep the LAST ``max_lines`` lines within ``max_bytes``.

    Pi parity ``truncateTail``: accumulates COMPLETE lines from the end — never
    splits a line (or a multibyte UTF-8 char) — dropping whole leading lines to
    fit the byte budget. Only when a SINGLE last line alone exceeds ``max_bytes``
    is it kept partial (its last ``max_bytes``, decoded char-safe) and
    ``last_line_partial`` set.
    """

    lines = text.split("\n")
    original_lines = len(lines)
    original_bytes = len(text.encode("utf-8"))
    if original_lines <= max_lines and original_bytes <= max_bytes:
        return text, _untruncated(original_lines, original_bytes)

    line_trunc = original_lines > max_lines
    tail = lines[-max_lines:] if line_trunc else lines
    kept: list[str] = []
    running = 0
    byte_trunc = False
    last_line_partial = False
    for idx in range(len(tail) - 1, -1, -1):
        add = len(tail[idx].encode("utf-8")) + (1 if kept else 0)
        if running + add > max_bytes:
            byte_trunc = True
            break
        kept.insert(0, tail[idx])
        running += add
    if not kept:
        # A single line exceeds the byte budget: keep its last max_bytes,
        # decoded char-safe (errors="ignore" drops the partial leading char).
        kept = [tail[-1].encode("utf-8")[-max_bytes:].decode("utf-8", errors="ignore")]
        byte_trunc = True
        last_line_partial = True
    body = "\n".join(kept)
    return body, TruncationInfo(
        truncated=True,
        truncated_by="bytes" if byte_trunc else ("lines" if line_trunc else None),
        last_line_partial=last_line_partial,
        original_lines=original_lines,
        kept_lines=len(kept),
        original_bytes=original_bytes,
        kept_bytes=len(body.encode("utf-8")),
    )


def truncate_head(
    text: str, *, max_lines: int, max_bytes: int
) -> tuple[str, TruncationInfo]:
    """Keep the FIRST ``max_lines`` lines within ``max_bytes``.

    Pi parity ``truncateHead``: accumulates COMPLETE lines from the start —
    never splits a line (or a multibyte UTF-8 char) — stopping before a line
    that would exceed the byte budget. Only when the FIRST line alone exceeds
    ``max_bytes`` is it kept partial (its first ``max_bytes``, char-safe).
    """

    lines = text.split("\n")
    original_lines = len(lines)
    original_bytes = len(text.encode("utf-8"))
    if original_lines <= max_lines and original_bytes <= max_bytes:
        return text, _untruncated(original_lines, original_bytes)

    line_budget = min(max_lines, original_lines)
    kept: list[str] = []
    running = 0
    byte_trunc = False
    for i in range(line_budget):
        add = len(lines[i].encode("utf-8")) + (1 if kept else 0)
        if running + add > max_bytes:
            byte_trunc = True
            break
        kept.append(lines[i])
        running += add
    if not kept:
        kept = [lines[0].encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")]
        byte_trunc = True
    line_trunc = (not byte_trunc) and len(kept) < original_lines
    body = "\n".join(kept)
    return body, TruncationInfo(
        truncated=True,
        truncated_by="bytes" if byte_trunc else ("lines" if line_trunc else None),
        original_lines=original_lines,
        kept_lines=len(kept),
        original_bytes=original_bytes,
        kept_bytes=len(body.encode("utf-8")),
    )


def truncate_line(line: str, max_length: int) -> str:
    """Truncate a single line to ``max_length`` characters."""

    if len(line) <= max_length:
        return line
    # Pi parity: three ASCII dots, not a unicode ellipsis (``truncate.ts``).
    return line[:max_length] + "... [truncated]"


def format_size(n: int) -> str:
    """Pi parity ``formatSize`` — human-readable byte count."""

    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "TruncationInfo",
    "format_size",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
]
