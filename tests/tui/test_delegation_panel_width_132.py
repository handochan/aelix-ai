"""#132 review round 1, F3 on the glass: a delegation panel row is never
clipped by the chrome.

``format_panel`` bounded its rows with rich's ``cell_len``; the chrome draws
the widget with prompt_toolkit, which places each character at the column
``get_cwidth`` summed to and drops whatever lands past the window. On emoji the
two disagree, and on ``786b95e5`` a reply of sixty regional indicators lost its
tail and its closing quote with no ``…`` — the cross-review's
``chrome_probe.py``, read here off a pyte screen through the real
``AelixChrome``, as the user would see it.
"""

from __future__ import annotations

from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
from aelix_agents.panel import PANEL_WIDGET_KEY, format_panel, panel_height
from aelix_coding_agent.subagent_contract import SubagentProgress, SubagentToolActivity
from aelix_coding_agent.tui.chrome import AelixChrome

_WIDE = (
    ("regional", "🇦" * 60 + "TAIL"),
    ("skin", "🏽" * 60 + "TAIL"),
    ("mixed", "🇰🇷 flag 🇦 lone 한국어 " * 6 + "TAIL"),
)
"""Shapes prompt_toolkit draws WIDER than rich measures them. A ZWJ family and
``❤\ufe0f`` are pinned by the unit tests (``test_delegation_panel_round1_132.py``)
and not here: pyte stops drawing a write at a zero-width character that is not
a combining mark (``Screen.draw``'s ``else: break``) — MEASURED, ``"a❤\ufe0fb"``
paints ``a❤`` and ``"a👨\u200d👩b"`` paints ``a👨`` — so the screen would show a
pyte artefact, not the chrome's row."""


async def _rows_on_screen(members: int, text: str, cols: int) -> list[str]:
    snapshots: list[SubagentProgress | None] = [
        SubagentProgress(
            id=f"s{k}",
            profile="worker",
            state="running",
            model="s-1",
            elapsed_ms=11_000,
            tokens=15,
            last_text=text,
            activity=(SubagentToolActivity("bash", text + " END", "running"),),
        )
        for k in range(members)
    ]

    def build_state(chrome: AelixChrome) -> None:
        chrome.set_widget(
            PANEL_WIDGET_KEY,
            format_panel(
                snapshots,
                tasks=(text,) * members,
                width=cols,
                height=panel_height(members, terminal_rows=30),
                profile="worker",
            ),
        )

    display = await render_chrome_to_screen(rows=30, cols=cols, build_state=build_state)
    return [row.rstrip() for row in display if row.startswith("│ ")]


async def test_a_wide_reply_ends_in_its_ellipsis_and_quote_on_screen() -> None:
    for name, text in _WIDE:
        rows = await _rows_on_screen(1, text, 80)
        assert len(rows) == 5, (name, rows)
        reply = rows[4]
        assert reply.endswith("…”"), (name, reply)
        assert rows[1].endswith("…"), (name, rows[1])  # the open call, cut with its mark


async def test_wide_task_labels_keep_their_numbers_on_screen() -> None:
    for cols in (60, 80):
        for name, text in _WIDE:
            rows = await _rows_on_screen(3, text, cols)
            assert len(rows) == 7, (cols, name, rows)
            for row in rows[1::2]:
                assert row.endswith("11s · 15"), (cols, name, row)
