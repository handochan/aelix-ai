"""One-line session labels for the two resume pickers.

There are two pickers and they cannot share a widget: ``--resume`` prints a
numbered list to stderr before the TUI exists, while ``/resume`` opens a
prompt-toolkit modal. They had, however, drifted into two copies of the same
LABEL function — ``cli/entry.py`` ``_resume_choice_label`` and
``tui/shell.py`` ``_format_session_choice``, the latter with no tests at all —
and both rendered ``{created} · {short-id}``, which says nothing about what the
session contains. This module is the single format for both.

pi builds its row from ``session.name ?? session.firstMessage`` plus a relative
age (``modes/interactive/components/session-selector.js:368-373``), where
``modified`` is the newest MESSAGE timestamp in the file and the file's mtime is
only the last-resort fallback (``core/session-manager.js:412-418``). aelix has
no session names, so the first user message carries the label alone.

WHY THE FIRST MESSAGE, when the last one is what was asked for: measured across
the 224 real sessions in this repo's session folder, the last user turn is
usually not what the session was about, because a session ends on a short
follow-up. Of the twenty most recently active, EIGHTEEN end in a bare shell line,
a one-line follow-up like "이어서 진행하세요", or no user text at all; the two
that do not are synthetic benchmark prompts. The first user turn states the task.
The last message is not dropped: :func:`session_detail_lines` shows it for the
row the cursor is on, which ``select(detail=...)`` evaluates lazily per render.

COST, measured over those same 224 sessions (0.70 / 0.12 / 0.09 ms per file):

===========================================================  ========
first user message — head scan, stops at the first hit         156 ms
last user message — 128 KB tail window                          27 ms
newest entry timestamp — 128 KB tail window                     20 ms
===========================================================  ========

which is why this needs neither pi's concurrency-10 loader nor its progress
bar: those exist because ``buildSessionInfo`` streams every file end to end to
count messages. Nothing here reads a whole file.

LAYOUT DEVIATION FROM pi: pi right-aligns the age against a known component
width. aelix's :meth:`AelixTUIContext.select` sizes its frame to the widest row
and has no width to align against, so the age leads the row instead — which
aligns for free (it is padded to a fixed cell count) and keeps the message
starting at one column. The information is identical.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

#: The head window that :func:`first_user_message` scans. The first user turn is
#: the second or third line of a session file (the header is line one), so this
#: is enormous headroom; it exists only to bound a pathological first line.
_HEAD_WINDOW = 64 * 1024

#: The tail window for :func:`last_user_message`. MEASURED: a 32 KB window missed
#: the last user turn in 34 of 224 real sessions (a long assistant turn plus its
#: tool results can easily exceed it), 128 KB missed 6. The miss degrades to no
#: preview, never to a wrong one.
_TAIL_WINDOW = 128 * 1024

#: The tail window for :func:`last_activity`. Only the final line is wanted — but
#: "the final line" is not small. An assistant turn carrying a tool result is one
#: JSONL record, and a ``read`` of a large file puts the whole file in it, so an
#: 8 KB window lands mid-record and finds no timestamp at all. That miss is
#: SILENT and it degrades in the stale direction: the age falls back to
#: ``created_at``, so a session worked in four hours ago is listed as ``12h`` with
#: nothing to say it was guessed. Sized to match :data:`_TAIL_WINDOW`, which was
#: itself measured against the real record sizes in this repo's session folder.
_ACTIVITY_WINDOW = 128 * 1024

#: Cell width of the age COLUMN ALONE. The two-space separator that follows it is
#: added by :func:`session_choice_label` on top of this, not counted inside it —
#: four cells holds ``59m`` and ``12mo`` right-aligned.
_AGE_CELLS = 4

#: How many codepoints per cell :func:`truncate_cells` will carry before it stops
#: measuring and starts slicing. Only zero-width input can reach it — one visible
#: codepoint is at least one cell — so it bounds the pathological case without
#: taking the ellipsis away from the ordinary one.
_ZERO_WIDTH_SLACK = 4

_NO_MESSAGES = "(no messages)"


def _text_of(message: dict[str, Any]) -> str:
    """The joined ``text`` blocks of a message payload (pi ``extractTextContent``)."""

    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, (list, tuple)):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return " ".join(p for p in parts if p).strip()


def _user_text(raw: bytes) -> str | None:
    """The user text of one JSONL line, or :data:`None` if it is not a user message.

    The caller pre-filters on the ``"user"`` substring, which is a cheap way to
    skip the assistant/tool lines that dominate a session file. That prefilter
    admits false positives (an assistant turn that merely mentions the word), so
    the role is re-checked structurally here — the substring decides what to
    PARSE, never what to return.
    """

    try:
        record = json.loads(raw)
    except Exception:
        return None  # a window edge cuts a line in half; that is expected
    if not isinstance(record, dict):
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    return _text_of(message) or None


def _read_head(path: str, window: int) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(window)


def _read_tail(path: str, window: int) -> bytes:
    """The final ``window`` bytes, with a partial leading line dropped."""

    with open(path, "rb") as handle:
        if os.fstat(handle.fileno()).st_size <= window:
            return handle.read()
        handle.seek(-window, os.SEEK_END)
        blob = handle.read()
    newline = blob.find(b"\n")
    return blob[newline + 1 :] if newline >= 0 else b""


def first_user_message(path: str) -> str | None:
    """The first user message in ``path``, or :data:`None`.

    Never raises: a missing, unreadable, or malformed session file yields
    :data:`None` so a picker still lists the session by its age.
    """

    try:
        blob = _read_head(path, _HEAD_WINDOW)
    except OSError:
        return None
    for line in blob.split(b"\n"):
        if b'"user"' not in line:
            continue
        text = _user_text(line)
        if text:
            return text
    return None


def last_user_message(path: str) -> str | None:
    """The last user message within the tail window, or :data:`None`. Never raises."""

    try:
        blob = _read_tail(path, _TAIL_WINDOW)
    except OSError:
        return None
    for line in reversed(blob.split(b"\n")):
        if b'"user"' not in line:
            continue
        text = _user_text(line)
        if text:
            return text
    return None


def last_activity(path: str) -> str | None:
    """The newest entry ``timestamp`` in ``path`` (pi ``modified``). Never raises.

    Read from the tail rather than computed from every message, because entries
    are appended in time order — pi takes a ``Math.max`` over all of them only
    because it is already streaming the file for the message count.
    """

    try:
        blob = _read_tail(path, _ACTIVITY_WINDOW)
    except OSError:
        return None
    for line in reversed(blob.split(b"\n")):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        stamp = record.get("timestamp")
        if isinstance(stamp, str) and stamp:
            return stamp
    return None


def _parse_iso(stamp: str | None) -> float | None:
    """Epoch seconds for an ISO-8601 stamp, or :data:`None` if it will not parse."""

    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def format_age(when: float | None, now: float) -> str:
    """``now`` / ``5m`` / ``3h`` / ``2d`` / ``1w`` / ``4mo`` / ``1y``.

    A verbatim port of pi ``formatSessionDate``
    (``modes/interactive/components/session-selector.js:22-40``), thresholds
    included.

    A FUTURE ``when`` renders ``now`` rather than a negative age — a clock skew
    must not produce ``-3m``. An UNKNOWN one renders ``?``, which is a different
    thing and deliberately so: a session whose age could not be read must not be
    indistinguishable from one touched a second ago.
    """

    if when is None:
        return "?"
    minutes = int(max(0.0, now - when) // 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 7:
        return f"{days}d"
    if days < 30:
        return f"{days // 7}w"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def session_age(meta: object, now: float) -> str:
    """The relative age of ``meta``: last activity, then created_at, then mtime.

    pi's fallback order (``core/session-manager.js:412-418``), with mtime last
    because it moves for reasons that are not conversation activity.
    """

    path = getattr(meta, "path", "") or ""
    when = _parse_iso(last_activity(path)) if path else None
    if when is None:
        when = _parse_iso(getattr(meta, "created_at", "") or "")
    if when is None and path:
        try:
            when = os.stat(path).st_mtime
        except OSError:
            when = None
    return format_age(when, now)


def _clean(text: str) -> str:
    """Collapse C0, DEL **and C1** to spaces; then collapse whitespace runs.

    pi's own filter is ``/[\\x00-\\x1f\\x7f]/g``, which is where this started, and
    it is not enough here. **U+009B IS A CSI** — the one-byte spelling of
    ``\\x1b[`` — and prompt-toolkit's ANSI parser honours it, so a session file
    carrying it paints colour into the ``/resume`` picker and into the stderr
    startup menu on any terminal that decodes 8-bit controls. The set is the same
    one ``aelix_agents/panel._CONTROL_KILL`` uses, and that module's docstring
    already spells out why C1 belongs in it; this module was written without it.

    A session JSONL is attacker-influenceable — a user can be handed a repository
    whose sessions folder came with it — so the label is untrusted text, not the
    user's own prose.

    The whitespace collapse is what keeps the one-row-per-session contract both
    pickers depend on: a pasted multi-line prompt would otherwise be many rows.
    """

    cleaned = "".join(
        " " if ch < " " or ch == "\x7f" or "\x80" <= ch <= "\x9f" else ch
        for ch in text
    )
    return " ".join(cleaned.split())


def truncate_cells(text: str, cells: int, *, marker: str = "…") -> str:
    """Truncate ``text`` to ``cells`` TERMINAL COLUMNS, appending ``…`` if cut.

    Columns, not codepoints: a Hangul or CJK character occupies two cells, so
    slicing by length would overrun a picker frame by roughly half its width on
    a Korean prompt — the same measure ``tui/context.py`` sizes its dividers by.
    Imported lazily because this module is reachable from the headless CLI path,
    which has no other reason to load prompt-toolkit.

    BOTH UNITS, BECAUSE A CELL COUNT IS NOT A BOUND. ``get_cwidth`` returns 0 for
    a combining mark, so a string made of them has no width and the early return
    hands it straight back. MEASURED on the cells-only version: 200 000 combining
    acutes in, 200 000 out, against a budget of 74. That label reaches a picker
    row and a stderr menu line; the repaint it causes is measured in tens of
    seconds and the key handler cannot run to cancel it. The codepoint cap runs
    first and the cell loop narrows what survives it.
    """

    from prompt_toolkit.utils import get_cwidth

    if cells <= 0:
        return ""
    # Zero-width backstop, deliberately SLACK rather than equal to ``cells``. A
    # slice at exactly the budget made every over-long ASCII label return without
    # its ``…`` — the cell loop below never ran, because the sliced text already
    # measured inside the budget. Slack keeps the cut marked while still bounding
    # a string that is all combining marks. A codepoint slice never splits a
    # codepoint; it can orphan a mark from its base, which is cosmetic, and this
    # arm is only reached by input no session legitimately contains.
    clipped = len(text) > cells * _ZERO_WIDTH_SLACK
    if clipped:
        text = text[: cells * _ZERO_WIDTH_SLACK]
    if get_cwidth(text) <= cells:
        return text + marker if clipped else text
    budget = cells - get_cwidth(marker)  # room for the marker, if there is one
    out: list[str] = []
    used = 0
    for ch in text:
        width = get_cwidth(ch)
        if used + width > budget:
            break
        out.append(ch)
        used += width
    return "".join(out) + marker


def session_choice_label(meta: object, now: float, *, width: int = 78) -> str:
    """``  2h  <first user message>`` — one scannable row per session.

    ``width`` is the total cell budget for the row; the message is truncated to
    what is left after the age column. Fully defensive: an odd metadata shape or
    an unreadable file degrades to the age and ``(no messages)``, never raises.
    """

    age = session_age(meta, now)
    path = getattr(meta, "path", "") or ""
    message = (first_user_message(path) if path else None) or _NO_MESSAGES
    room = max(10, width - _AGE_CELLS - 2)
    return f"{age:>{_AGE_CELLS}}  {truncate_cells(_clean(message), room)}"


def short_field(value: str, cells: int) -> str:
    """A header-derived identifier, cleaned and capped, for display.

    ``created_at`` and ``id`` come off the session file's first line, so they are
    the same untrusted text the message body is — and the ``--resume`` startup
    menu prints to STDERR, which has no ANSI parser in front of it and hands the
    bytes straight to the terminal.

    NO ELLIPSIS. These are identifiers, not prose: the eight characters are a
    usable prefix for ``--resume <id>``, and spending one of them on a ``…`` makes
    them seven. Cleaning this by routing it through the prose truncator turned
    ``abcdef12`` into ``abcdef1…``, which two existing tests caught immediately.
    """

    return truncate_cells(_clean(value), cells, marker="")


def session_detail_lines(meta: object, *, width: int = 78) -> list[str]:
    """The per-highlight detail rows for ``/resume`` (``select(detail=...)``).

    Where the label answers "what was this session about", these answer "where
    did it get to" — the last user turn, plus the identity needed to correlate
    the session with a file on disk. Only ever built for the highlighted row.

    Takes no ``now``: the age already leads the label, and repeating it here as
    an absolute timestamp is the point — ``created_at`` is what a user greps the
    session folder by.
    """

    lines: list[str] = []
    path = getattr(meta, "path", "") or ""
    last = last_user_message(path) if path else None
    if last:
        lines.append(f"  last: {truncate_cells(_clean(last), max(10, width - 8))}")
    # THE TRAILER IS HEADER-DERIVED AND THEREFORE UNTRUSTED TOO. ``created_at``
    # and ``id`` are read straight out of the session file's first line, so a
    # supplied sessions folder controls them exactly as it controls the message
    # text above — and the first version of this cleaned the message halves and
    # not these. Measured: an ``id`` of ``\x1b[31mID\x1b[0m`` reached the picker
    # and the stderr menu with the ESC intact.
    started = short_field((getattr(meta, "created_at", "") or "").replace("T", " "), 16)
    short_id = short_field(getattr(meta, "id", "") or "", 8)
    trailer = "  ·  ".join(part for part in (started, short_id) if part)
    if trailer:
        lines.append(f"  {trailer}")
    return lines


__all__ = [
    "first_user_message",
    "format_age",
    "last_activity",
    "last_user_message",
    "session_age",
    "session_choice_label",
    "session_detail_lines",
    "short_field",
    "truncate_cells",
]
