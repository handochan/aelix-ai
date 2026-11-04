"""#668 — a steering or follow-up line, from Enter to the model.

Enter during a turn steers a line into it and Alt+Enter queues one as a
follow-up (ADR-0119). The harness holds the line in a queue and hands it to the
model at the next turn boundary: after the answer being streamed, after a
tool's result. Its echo used to be committed at Enter, so it landed in the
middle of the streamed answer — or above a short one, which is committed whole
when it ends — while the model got it, and the session recorded it, after.

pi draws a queued line in its pending area (``Steering: …``,
``updatePendingMessagesDisplay``) and adds it to the chat on the user
``message_start`` its agent loop emits when it takes the line from the queue.
This does the same, the owner's decision (a): a queued line waits in the
pending area, above the ``!`` blocks (:mod:`.shell_lane`), and its echo is
committed on its ``message_start``, so the transcript is in the order the
session records. A line given back to the input (Alt+Up, an interrupt) or
cleared from the queue leaves the area with it.

A line is known by the message the harness queued for it, and only by that
(identity: what its ``message_start`` carries). ``steer()`` / ``follow_up()``
queue the message in their first step, before they await the ``queue_update``
hooks, so the input finds it in the queue right after that step (:meth:`taken`)
— before the turn can take it, however long a hook keeps the call open. A line
is never matched by its text: the prompt, a line of the other kind or a line
queued by an extension can carry the same words (Codex r1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from rich.text import Text

from aelix_coding_agent.tui.render import _safe_tool_output
from aelix_coding_agent.tui.shell_lane import _PENDING_MAX_ROWS, _ansi_row

QueuedKind = Literal["steer", "follow_up"]

#: Rows the queued lines may always take in the pending area; they take more
#: when the ``!`` blocks under them need fewer than the rest of its eight
#: (owner, 2026-11-05: ``max(3, 8 - rows the blocks need)``).
_QUEUED_MIN_ROWS = 3

_LABEL = {"steer": "Steering", "follow_up": "Follow-up"}
_QUEUE = {"steer": "_steering_queue", "follow_up": "_follow_up_queue"}
_KINDS: tuple[QueuedKind, ...] = ("steer", "follow_up")
#: pi's row under its queued lines (``interactive-mode.ts`` ``↳ … to edit all
#: queued messages``); the first row to go when the eight are full.
_EDIT_HINT = "↳ Alt+Up to edit all queued messages"


class PendingBlocks(Protocol):
    """The ``!`` blocks drawn under the queued lines (:class:`~.shell_lane.ShellLane`)."""

    def rows_wanted(self) -> int: ...

    def rows(self, limit: int = _PENDING_MAX_ROWS) -> list[str]: ...


@dataclass(eq=False)
class QueuedLine:
    kind: QueuedKind
    text: str
    #: The message the harness queued for the line.
    message: Any


class QueuedMessages:
    """The lines the turn has not taken yet, drawn in the pending area with the
    ``!`` blocks under them (*blocks*); *show* gets the area's rows, whether a
    spacer goes above them and which row is the hint (:meth:`compose`).
    *text_above*: whether rows with text sit above the area (the stream, a
    widget), which is when a spacer is wanted."""

    def __init__(
        self,
        *,
        current_harness: Callable[[], Any],
        commit_echo: Callable[[QueuedKind, str], None],
        show: Callable[[list[str], bool, int | None], None],
        width: Callable[[], int],
        blocks: PendingBlocks | None = None,
        text_above: Callable[[], bool] | None = None,
    ) -> None:
        self._current_harness = current_harness
        self._commit_echo = commit_echo
        self._show = show
        self._width = width
        self._blocks = blocks
        self._text_above = text_above
        self._lines: list[QueuedLine] = []

    @staticmethod
    def queued_now(harness: Any, kind: QueuedKind) -> list[Any]:
        """What *harness* holds in *kind*'s queue, before the line is queued."""

        return _queued(harness, kind)

    def taken(
        self, harness: Any, kind: QueuedKind, text: str, before: list[Any]
    ) -> QueuedLine | None:
        """``steer()`` / ``follow_up()`` ran their first step: draw the line with
        the message they queued for it, the first one in *kind*'s queue that was
        not there *before* and carries *text*.

        ``None`` when there is none: the harness refused the line before
        queueing it (a disposed harness), or something cleared it in the same
        step. It is not drawn then — the harness does not hold it.
        """

        for message in _queued(harness, kind):
            if any(message is old for old in before) or message_text(message) != text:
                continue
            if any(q.message is message for q in self._lines):
                continue
            line = QueuedLine(kind, text, message)
            self._lines.append(line)
            self.redraw()
            return line
        return None

    def delivered(self, message: Any) -> bool:
        """A user ``message_start``: commit the echo of the queued line it carries.

        ``False`` for any other user message: the prompt, which the input loop
        echoed, or a line an extension queued.
        """

        line = next((q for q in self._lines if q.message is message), None)
        if line is None:
            return False
        self._lines.remove(line)
        self._commit_echo(line.kind, line.text)
        self.redraw()
        return True

    def clear(self) -> None:
        """The queued lines went back to the input (Alt+Up, an interrupt)."""

        if self._lines:
            self._lines.clear()
            self.redraw()

    def reconcile(self, harness: Any = None) -> None:
        """Drop every line *harness* (the current one by default) no longer
        holds: an abort cleared it, or a session change replaced the harness
        without carrying its queues over (#681 carries them on ``/reload``: the
        same messages, so their lines stay). A line Esc took out of a turn
        before the model read it can wait in the next-turn queue; it stays."""

        if harness is None:
            harness = self._current_harness()
        held = [m for kind in _KINDS for m in _queued(harness, kind)]
        held += list(getattr(harness, "_next_turn_queue", None) or ())
        kept = [q for q in self._lines if any(q.message is m for m in held)]
        if len(kept) != len(self._lines):
            self._lines = kept
            self.redraw()

    def rows(self) -> list[str]:
        """The pending area's rows (:meth:`compose`, without the spacer)."""

        return self.compose()[0]

    def compose(self) -> tuple[list[str], bool, int | None]:
        """The pending area: the queued lines, then the ``!`` blocks, in eight rows.

        The lines are listed in the order the harness hands them to the model —
        every steering line, then every follow-up (pi's
        ``updatePendingMessagesDisplay``), each in the order it was queued. They
        take ``max(3, 8 - rows the blocks need)`` rows; past that, the last one
        is "… N more queued (Alt+Up edits them)". The blocks get what the lines
        leave, which is never fewer than the newest block needs (at most four
        rows, and "… N more commands above"). A row still free takes pi's
        ``Spacer(1)`` above the lines (``True``) when text sits above the area,
        and the next one pi's "↳ Alt+Up to edit all queued messages" under them
        (its index, the third item): the hint is the first row to go when the
        eight are full, and the spacer and the hint never take a row from a line
        or a block. The spacer counts in the eight; the chrome draws it in place
        of the blank row the stream ends with. The chrome draws the spacer and
        the hint only while the whole frame has a row spare for them, too: a
        row the stream above needs is not spare
        (``AelixChrome._settle_pending_room``).
        """

        lines = sorted(self._lines, key=lambda q: q.kind != "steer")
        wanted = self._blocks.rows_wanted() if self._blocks is not None else 0
        room = _PENDING_MAX_ROWS - wanted
        if wanted:
            room = max(_QUEUED_MIN_ROWS, room)
        texts = [_row(q) for q in lines]
        folded = len(texts) > room
        if folded:
            more = len(texts) - (room - 1)
            texts = texts[: room - 1]
            texts.append(Text(f"… {more} more queued (Alt+Up edits them)", style="dim"))
        blocks = (
            self._blocks.rows(_PENDING_MAX_ROWS - len(texts)) if self._blocks is not None else []
        )
        spare = _PENDING_MAX_ROWS - len(texts) - len(blocks)
        spacer = bool(texts) and spare > 0 and self._wants_spacer()
        if spacer:
            spare -= 1
        hint_row: int | None = None
        if texts and not folded and spare > 0:
            hint_row = len(texts)
            texts.append(Text(_EDIT_HINT, style="dim"))
        width = self._width_now()
        return [_ansi_row(t, width) for t in texts] + blocks, spacer, hint_row

    def redraw(self) -> None:
        """Draw the area again: a line or a ``!`` block under them changed."""

        self._show(*self.compose())

    def _wants_spacer(self) -> bool:
        if self._text_above is None:
            return False
        try:
            return bool(self._text_above())
        except Exception:  # noqa: BLE001 — a faulty source must not break the chrome
            return False

    def _width_now(self) -> int:
        try:
            return max(1, int(self._width()))
        except Exception:  # noqa: BLE001 — a faulty width source must not break the chrome
            return 80


def message_text(message: Any) -> str:
    """The text blocks of a queued user message, joined (images left out)."""

    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        return ""
    return "\n".join(
        getattr(b, "text", "") or "" for b in content if getattr(b, "type", None) == "text"
    )


def _queued(harness: Any, kind: QueuedKind) -> list[Any]:
    queue = getattr(harness, _QUEUE[kind], None)
    messages = getattr(queue, "_messages", None)
    return list(messages) if messages else []


def _row(line: QueuedLine) -> Text:
    text = _safe_tool_output(line.text).strip()
    first, _, rest = text.partition("\n")
    return Text(f"{_LABEL[line.kind]}: {first}" + (" …" if rest.strip() else ""), style="dim")


__all__ = ["PendingBlocks", "QueuedLine", "QueuedMessages", "message_text"]
