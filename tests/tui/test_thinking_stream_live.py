"""Issue #170 — reasoning through the REAL chrome, read back off a pyte screen.

The unit tests in ``test_event_renderer.py`` assert what the renderer hands its
sinks. These assert what a user would SEE: the renderer's sinks are wired to a
real :class:`AelixChrome` exactly as ``shell.py`` wires them (``_set_tail`` →
``set_widget("__stream__", …, above=True)``, shell.py:3147), the chrome paints a
frame, and the frame is read back through a terminal emulator.

That distinction is the whole point for a live WINDOW. Its failure mode is not
"the wrong string was computed" — it is "a string nobody computed again is still
on the glass", which a sink-level assertion cannot see, because the defect is the
ABSENCE of a later write.
"""

from __future__ import annotations

from typing import Any

from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
from aelix_agent_core.types import MessageStartEvent, MessageUpdateEvent, TurnEndEvent
from aelix_ai.messages import AssistantMessage
from aelix_ai.streaming import ThinkingDeltaEvent
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.render import EventRenderer

# No spaces: a pyte row is a fixed-width grid, so a marker that fits on one row
# can be matched without reassembling wrapped text.
REASONING = "REASONING_VISIBLE_MID_STREAM"


def _msg_update(stream_event: Any) -> MessageUpdateEvent:
    return MessageUpdateEvent(message=AssistantMessage(), assistant_message_event=stream_event)


async def _screen(drive: Any, *, cols: int = 90, rows: int = 24) -> tuple[list[str], list[Any]]:
    """Paint one real frame after ``drive(renderer)`` has fed the events."""

    commits: list[Any] = []

    def build_state(chrome: AelixChrome) -> None:
        renderer = EventRenderer(
            commit=commits.append,
            # Exactly shell.py:3147, minus the queue hop (the pump is a plain
            # relay; going through it would test asyncio, not the window).
            set_tail=lambda ansi: chrome.set_widget(
                "__stream__", ansi.split("\n") if ansi else None, above=True
            ),
            width=cols,
        )
        renderer.on_agent_event(MessageStartEvent(message=AssistantMessage()))
        drive(renderer)

    display = await render_chrome_to_screen(rows=rows, cols=cols, build_state=build_state)
    return display, commits


async def test_reasoning_is_on_the_glass_while_it_streams() -> None:
    """#170 itself: no end event yet, and the user can already read it."""

    def drive(r: EventRenderer) -> None:
        r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta=REASONING, content_index=0)))

    display, _commits = await _screen(drive)

    assert any(REASONING in row for row in display), "\n".join(display)


async def test_an_aborted_turn_leaves_no_reasoning_on_the_glass() -> None:
    """Esc mid-reasoning.

    Abort emits ONLY ``turn_end`` — no ``message_end``, and the cancelled adapter
    never sends ``thinking_end`` — so before the fix the last painted frame of
    reasoning stayed welded above the input box for the rest of the session,
    outliving the turn, the next prompt and ``/new``.
    """

    def drive(r: EventRenderer) -> None:
        r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta=REASONING, content_index=0)))
        r.on_agent_event(
            TurnEndEvent(message=AssistantMessage(stop_reason="aborted"), tool_results=[])
        )

    display, commits = await _screen(drive)

    assert not any(REASONING in row for row in display), "\n".join(display)
    # …and it was not merely thrown away: it reached scrollback, ahead of the
    # notice, the same way a partial ANSWER does on this path.
    assert [c.plain for c in commits] == [REASONING, "✖ Operation aborted"]


async def test_hiding_reasoning_mid_stream_clears_the_glass() -> None:
    """Ctrl+T, pressed at the moment the reasoning becomes noise.

    Before the fix the release was gated on the CURRENT setting, so the one
    window written under the old value was the one never taken down: the user
    asked for the reasoning to go away and it stayed put.
    """

    def drive(r: EventRenderer) -> None:
        r.on_agent_event(_msg_update(ThinkingDeltaEvent(delta=REASONING, content_index=0)))
        r.hide_thinking = True  # shell.py:2322, the Ctrl+T handler

    display, _commits = await _screen(drive)

    assert not any(REASONING in row for row in display), "\n".join(display)
