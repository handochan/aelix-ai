"""#701 (ADR-0292 D5) — the line a setter leaves when it ran while the model answers.

The model and the thinking level are read when a run of the model starts
(``AgentLoopConfig``): a steer, a tool result or a follow-up joins the run under
way and keeps what it started with, and a retry of the same message starts a new
run and reads the new value (Codex 701-thinking-retry). A ``/thinking`` or
``/model`` run mid-turn left its own ``thinking → high`` and then a second line,
"Applies from your next message, or a retry of this one: thinking high.", which
said the same thing twice (fix round 2). Now the setter's line says it, in one
dim line: ``thinking → high · applies from your next message (or a retry of this
one)``.

The input loop sets :data:`MID_TURN` in the task a command Entered mid-turn runs
in, so the setter, its picker and the ``/settings`` row it reaches read it where
they commit; anywhere else it is unset and the line is the idle one.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

from rich.text import Text

#: Whether the turn the running command was Entered under still runs; ``None``
#: outside a command run mid-turn.
MID_TURN: ContextVar[Callable[[], bool] | None] = ContextVar("aelix_mid_turn", default=None)

APPLIES_FROM = "applies from your next message (or a retry of this one)"


def setter_line(text: str, style: str = "green") -> Text:
    """A setter's ``thinking → …`` / ``model → …`` line.

    Idle, or once the turn it was Entered under is over, it is *text* in
    *style*. While that turn still runs it says when the change applies, dim — a
    warning (``style`` other than green) keeps its colour.
    """

    running = MID_TURN.get()
    if running is None or not running():
        return Text(text, style=style)
    return Text(f"{text} · {APPLIES_FROM}", style="dim" if style == "green" else style)


__all__ = ["APPLIES_FROM", "MID_TURN", "setter_line"]
