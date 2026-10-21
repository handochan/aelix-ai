"""#380 — the start-up selector wraps its body at word boundaries, as pi does.

``cli/entry.py``'s ``_prompt_one_shot_select`` draws the project-trust gate,
the project-agent confirmation (ADR-0196) and the session-in-use question
(#137) before ``run_tui`` exists. It handed prompt-toolkit one string and let
``Window(wrap_lines=True)`` wrap it, which wraps by cell: measured on main
``27fa21c5`` in a pty at 120 columns the trust body read ``… on your m`` /
``achine`` and ``… apply .`` / ``aelix/settings.json``
(``.omc/probes/b4-b-live/380/before/fresh.120.0_prompt.screen``). pi draws the
same question with ``ExtensionSelectorComponent``, whose ``Text`` wraps with
``wrapTextWithAnsi``: after a space, and a word wider than the row cut where
the row is full. ``_one_shot_rows`` now cuts every line with ``tui/context.py``'s
``_title_rows`` (the approval prompt's ``_cut``) at the width of each paint.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

pytest.importorskip("prompt_toolkit")

import pyte  # noqa: E402
from aelix_coding_agent.cli import entry as entry_mod  # noqa: E402
from aelix_coding_agent.cli.project_trust import (  # noqa: E402
    format_project_trust_prompt,
    project_trust_options,
)
from aelix_coding_agent.tui.width import cells_by_cluster  # noqa: E402

from tests.event_waits import wait_until  # noqa: E402

_CWD = Path("/private/tmp/b4-b-work/380/live/tmp/cwd-fresh-120-shy12cof")
_BODY = format_project_trust_prompt(_CWD)
_PARAGRAPH = _BODY.split("\n")[3]


def _paragraph_rows(rendered: str) -> list[str]:
    """The rows the risk paragraph was cut into (the path above it may wrap too)."""

    rows = rendered.split("\n")
    start = next(i for i, row in enumerate(rows) if row.startswith("This allows Aelix"))
    out: list[str] = []
    for row in rows[start:]:
        if not row:
            break
        out.append(row)
    return out


@pytest.mark.parametrize("width", [120, 80, 40])
def test_the_trust_body_breaks_only_after_a_space(width: int) -> None:
    rendered = entry_mod._one_shot_rows(_BODY, project_trust_options(_CWD), 0, width)
    rows = _paragraph_rows(rendered)
    assert len(rows) > 1, rows
    # Nothing is dropped and nothing is added: the rows ARE the paragraph.
    assert "".join(rows) == _PARAGRAPH
    for row in rows[:-1]:
        # Every break is after a space, so no word is split across two rows.
        assert row.endswith(" "), (width, row)
    for row in rendered.split("\n"):
        assert cells_by_cluster(row) <= width, (width, row)


def test_the_measured_mid_word_breaks_are_gone_at_120() -> None:
    rendered = entry_mod._one_shot_rows(_BODY, project_trust_options(_CWD), 0, 120)
    rows = rendered.split("\n")
    assert not [r for r in rows if r.endswith("on your m") or r.endswith("apply .")], rows
    assert any(r.startswith("machine — to load") for r in rows), rows
    assert any(r.startswith(".aelix/settings.json, which") for r in rows), rows


def test_a_word_wider_than_the_row_is_cut_where_the_row_is_full() -> None:
    # The session-in-use question's shape (#137): the path is the actionable half.
    path = "/a/" + "very-long-directory-name/" * 6 + "s.jsonl"
    rendered = entry_mod._one_shot_rows(f"Busy.\n  {path}\n", ["Cancel"], 0, 40)
    rows = rendered.split("\n")
    path_rows: list[str] = []
    for row in rows[1:]:
        if not row:
            break
        path_rows.append(row)
    assert "".join(path_rows) == f"  {path}", path_rows
    # The indent ends a row of its own (pi's wrapSingleLine does the same: the
    # whitespace token is pushed before a too-long word is broken); then the
    # path fills each row to the edge.
    assert path_rows[0] == "  "
    assert [cells_by_cluster(r) for r in path_rows[1:-1]] == [40] * (len(path_rows) - 2)


def test_rows_that_fit_are_drawn_as_before() -> None:
    options = project_trust_options(_CWD)
    old = "\n".join(
        [
            _BODY,
            "",
            *(f"{'→ ' if i == 1 else '  '}{label}" for i, label in enumerate(options)),
            "",
            "↑/↓ to move · Enter to choose · Esc to cancel",
        ]
    )
    assert entry_mod._one_shot_rows(_BODY, options, 1, 400) == old


def test_an_escape_in_a_path_is_drawn_by_name_not_obeyed() -> None:
    body = format_project_trust_prompt(Path("/tmp/evil\x1b[8mhidden"))
    rendered = entry_mod._one_shot_rows(body, ["Trust"], 0, 120)
    assert "\x1b[8m" not in rendered
    assert "\x1b[7m^[\x1b[27m[8mhidden" in rendered


async def _paint(cols: int, rows: int = 40) -> tuple[str | None, list[str]]:
    """Run the real one-shot widget at *cols* x *rows*; replay it through pyte."""

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output

    capture = io.StringIO()
    output = Vt100_Output(
        capture,
        get_size=lambda: Size(rows=rows, columns=cols),
        term="xterm-256color",
        enable_cpr=False,
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        task = asyncio.ensure_future(
            entry_mod._prompt_one_shot_select(_BODY, project_trust_options(_CWD))
        )
        await wait_until(
            lambda: "Esc to cancel" in capture.getvalue(), what="the selector's first paint"
        )
        pipe.send_text("\x1b[B\r")
        label = await asyncio.wait_for(task, timeout=30)
    screen = pyte.Screen(cols, rows)
    pyte.Stream(screen).feed(capture.getvalue())
    return label, [line.rstrip() for line in screen.display]


@pytest.mark.parametrize("cols", [120, 80])
async def test_the_real_selector_paints_whole_words(cols: int) -> None:
    label, screen = await _paint(cols)
    assert label == project_trust_options(_CWD)[1]
    text = "\n".join(screen)
    start = next(i for i, row in enumerate(screen) if row.startswith("This allows Aelix"))
    painted: list[str] = []
    for row in screen[start:]:
        if not row:
            break
        painted.append(row)
    # pyte drops the trailing space of each row; the words are whole on screen.
    assert " ".join(painted).split() == _PARAGRAPH.split(), text
    assert "Trust parent folder (" in text
    assert "↑/↓ to move · Enter to choose · Esc to cancel" in text


async def test_each_paint_cuts_at_the_width_the_output_has_then(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The width is read from the app's output on EVERY paint, so a terminal
    resized while the question is open gets its rows cut again at the new width.

    Review round 2 (verify, blocking): the rows above paint at 120 and 80, where
    a cut at a fixed 78 columns is also whole words, so ``_render`` reading a
    fixed 78, or the process terminal's width (``shutil.get_terminal_size``),
    passed all of them. Here the output is 120 wide for the first paint and 50
    for the next one, and ``COLUMNS`` says 97, so neither wrong source can
    produce the rows a 50-column paint must show.
    """

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output

    monkeypatch.setenv("COLUMNS", "97")
    monkeypatch.setenv("LINES", "40")
    cols = {"now": 120}
    capture = io.StringIO()
    output = Vt100_Output(
        capture,
        get_size=lambda: Size(rows=40, columns=cols["now"]),
        term="xterm-256color",
        enable_cpr=False,
    )
    options = project_trust_options(_CWD)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        task = asyncio.ensure_future(entry_mod._prompt_one_shot_select(_BODY, options))
        await wait_until(
            lambda: "Esc to cancel" in capture.getvalue(), what="the selector's first paint"
        )
        first = capture.getvalue()
        cols["now"] = 50
        pipe.send_text("\x1b[B")  # a key, so the selector paints again
        await wait_until(
            lambda: "Esc to cancel" in capture.getvalue()[len(first) :],
            what="the selector's paint after the resize",
        )
        second = capture.getvalue()[len(first) :]
        pipe.send_text("\r")
        label = await asyncio.wait_for(task, timeout=30)
    assert label == options[1]

    def painted(stream: str, width: int) -> list[str]:
        screen = pyte.Screen(width, 40)
        pyte.Stream(screen).feed(stream)
        return _paragraph_rows("\n".join(line.rstrip() for line in screen.display))

    def expected(width: int, idx: int) -> list[str]:
        # pyte drops the space each row ends with; the cut is otherwise the same.
        rows = entry_mod._one_shot_rows(_BODY, options, idx, width)
        return [row.rstrip() for row in _paragraph_rows(rows)]

    assert painted(first, 120) == expected(120, 0)
    after = painted(second, 50)
    assert after == expected(50, 1), after
    assert max(cells_by_cluster(row) for row in after) <= 50


async def test_a_tui_that_cannot_be_imported_denies_before_the_app_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_one_shot_rows`` draws with ``tui/context.py``; when that import fails
    the selector answers ``None`` (every caller's deny) instead of raising out
    of the first paint."""

    import sys

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    monkeypatch.setitem(sys.modules, "aelix_coding_agent.tui.context", None)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text("\r")
        label = await asyncio.wait_for(
            entry_mod._prompt_one_shot_select(_BODY, ["Trust"]), timeout=30
        )
    assert label is None
