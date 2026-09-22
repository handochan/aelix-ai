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

    ``truncated`` means CONTENT was dropped. A trailing line terminator is not
    content — see :func:`split_lines_for_counting` — so text whose only byte
    over ``max_bytes`` is its final ``"\\n"`` comes back whole and untruncated
    with ``kept_bytes == original_bytes - 1``. That is the one case where the
    two byte counts differ without a cut; everywhere else ``kept_bytes`` is the
    body's and ``original_bytes`` is the input's.

    ``last_line_partial`` (tail only) says the body opens mid-line: nothing
    whole fit the byte budget, so the FIRST kept line is a fragment of the line
    it came from. Its 1-based index in the original is
    ``original_lines - kept_lines + 1`` — which is the last line, and so pi's
    ``lastLinePartial``, whenever ``kept_lines`` is 1.
    """

    truncated: bool = False
    truncated_by: Literal["lines", "bytes"] | None = None
    last_line_partial: bool = False
    original_lines: int = 0
    kept_lines: int = 0
    original_bytes: int = 0
    kept_bytes: int = 0


def split_lines_for_counting(text: str) -> list[str]:
    """Split ``text`` into the lines a reader would count.

    Pi parity ``splitLinesForCounting`` (``core/tools/truncate.ts:47-56``): a
    line terminator ENDS a line rather than beginning another, so the empty
    element ``str.split("\\n")`` leaves behind after a trailing newline is not
    a line and is dropped, and an empty string is no lines at all. The count is
    then ``wc -l`` for text that ends in a newline and ``wc -l`` + 1 for text
    whose last line is unterminated.

    Exactly ONE element is dropped, because exactly one terminator produces it:
    ``"a\\n\\n"`` is two lines, the second one blank, the same as ``wc -l`` says.

    Without this the phantom element IS a line to everything downstream, and
    it costs 0 bytes — so it is the one line that fits a byte budget, and the
    real line above it is dropped whole (#309: ``print('x'*1000000)`` reached
    the model as 0 bytes of body and a notice).
    """

    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def _untruncated(
    original_lines: int, original_bytes: int, kept_bytes: int | None = None
) -> TruncationInfo:
    return TruncationInfo(
        truncated=False,
        original_lines=original_lines,
        kept_lines=original_lines,
        original_bytes=original_bytes,
        kept_bytes=original_bytes if kept_bytes is None else kept_bytes,
    )


def _fits_but_for_the_terminator(
    text: str, *, original_lines: int, original_bytes: int, max_lines: int, max_bytes: int
) -> bool:
    """Is the trailing line terminator the ONLY thing over the byte budget?

    Then nothing of the content is dropped and nothing is announced: the body
    is the text without that one byte. Dropping it rather than restoring it is
    the tighter reading of the cap — a body already at ``max_bytes`` has no
    room for a line ending that carries nothing — and it is what keeps
    ``"x" * 50KB + "\\n"`` from either blowing the cap by a byte or acquiring a
    ``[Showing lines 1-1 of 1]`` notice over an output that arrived whole.
    """

    return (
        original_lines <= max_lines
        and original_bytes - 1 <= max_bytes
        and text.endswith("\n")
    )


def truncate_tail(
    text: str, *, max_lines: int, max_bytes: int
) -> tuple[str, TruncationInfo]:
    """Keep the LAST ``max_lines`` lines within ``max_bytes``.

    Pi parity ``truncateTail``: accumulates COMPLETE lines from the end — never
    splits a line (or a multibyte UTF-8 char) — dropping whole leading lines to
    fit the byte budget. Only when NOTHING whole fits is a fragment kept (the
    tail's last ``max_bytes``, decoded char-safe) and ``last_line_partial`` set.

    "Nothing whole fits" is not the same question as "did any line fit": an
    empty line costs 0 bytes, so it always fits, and a budget spent entirely on
    blank lines has bought nothing (#309 — ``print(big); print()`` kept one
    newline out of a megabyte). The lines are counted by
    :func:`split_lines_for_counting`, so the newline every ``print`` ends with
    is not one of them.
    """

    lines = split_lines_for_counting(text)
    original_lines = len(lines)
    original_bytes = len(text.encode("utf-8"))
    if original_lines <= max_lines and original_bytes <= max_bytes:
        return text, _untruncated(original_lines, original_bytes)
    if _fits_but_for_the_terminator(
        text,
        original_lines=original_lines,
        original_bytes=original_bytes,
        max_lines=max_lines,
        max_bytes=max_bytes,
    ):
        return text[:-1], _untruncated(
            original_lines, original_bytes, original_bytes - 1
        )

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
    if byte_trunc and not any(kept):
        # The byte budget bought no content: either no line fit at all, or the
        # only ones that did were blank. Keep the tail's last max_bytes
        # instead, decoded char-safe (errors="ignore" drops the leading partial
        # char), and split it back into the lines it stands for. For a tail
        # that is one over-long line this IS the old single-line branch —
        # ``"\n".join`` of one element is that element.
        joined = "\n".join(tail).encode("utf-8")
        kept = (
            joined[max(len(joined) - max_bytes, 0) :]
            .decode("utf-8", errors="ignore")
            .split("\n")
        )
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

    Counting is :func:`split_lines_for_counting`'s, same as the tail's, so a
    file that ends in a newline no longer reports one line more than it has —
    which is what made ``read`` call a file of exactly ``max_lines`` lines
    truncated and answer "[Showing lines 1-2000 of 2001. Use offset=2001 to
    continue.]" about an offset with nothing behind it (#309). Only the
    truncated/not verdict in that sentence was this function's: the ``2001``
    printed inside the notice is ``read``'s own ``total_lines``
    (``tools/read.py:195``, still a raw ``split("\\n")``, pi parity
    ``read.ts:130``), so every file ``read`` DOES still truncate is still
    announced as one line longer than it is, disagreeing by one with the
    ``original_lines`` this returns in the same ``ToolResult``.

    What is deliberately NOT mirrored from the tail is its "the budget bought
    no content" fallback. A leading blank line above an over-long one starves
    the head budget the same way, but a head truncation hands the caller a
    place to continue from — ``read``'s ``Use offset=N``, and from there its
    first-line-exceeds-the-limit branch names the line and how to read it —
    while a tail truncation is the end of the output and has nowhere to send
    anyone. Keeping a fragment here would trade an actionable empty body for a
    body that silently drops the rest of a line the next offset then skips.
    """

    lines = split_lines_for_counting(text)
    original_lines = len(lines)
    original_bytes = len(text.encode("utf-8"))
    if original_lines <= max_lines and original_bytes <= max_bytes:
        return text, _untruncated(original_lines, original_bytes)
    if _fits_but_for_the_terminator(
        text,
        original_lines=original_lines,
        original_bytes=original_bytes,
        max_lines=max_lines,
        max_bytes=max_bytes,
    ):
        return text[:-1], _untruncated(
            original_lines, original_bytes, original_bytes - 1
        )

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
    "split_lines_for_counting",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
]
