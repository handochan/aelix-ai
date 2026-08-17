"""Phase C read off a pyte screen: the batch panel and the merged footer row.

The unit tests in ``tests/agents_ext/test_batch_surfaces.py`` and
``tests/tui/test_context.py`` assert what the formatters RETURN. These assert
what a user would SEE, and the two are not the same question for either surface:

* the panel goes through ``chrome._render_widget_lines``, which ANSI-parses the
  joined lines into a ``Window(..., dont_extend_height=True)`` with ``wrap_lines``
  left False — so a row wider than the terminal is CLIPPED, not wrapped, and the
  clip takes the right-hand end. A formatter can return four distinct labels and
  the glass can still show four identical ones;
* the merged statusline row is height-1 and clipped the same way, and what
  survives the clip is decided by segment ORDER, which is applied in
  ``_refresh_footer`` rather than in any single segment producer.

Both are ADR-0227 findings that a return-value assertion could not have caught.
"""

from __future__ import annotations

import re

from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
from aelix_agents.panel import PANEL_WIDGET_KEY, format_panel
from aelix_coding_agent.subagent_contract import SubagentProgress, SubagentState
from aelix_coding_agent.tui.chrome import AelixChrome

_MODEL = "github-copilot/gpt-5.6-codex"

# A fan-out over one verb, which is the ordinary shape of a batch and the one the
# constant column cap collapsed: 32/32/31/32 cells, differing only in the last
# word.
_JOBS = (
    "port the retry backoff to google",
    "port the retry backoff to openai",
    "port the retry backoff to azure",
    "port the retry backoff to vertex",
)
_TAILS = ("google", "openai", "azure", "vertex")
_STATE_WORDS = ("starting", "running", "done", "error", "stopped", "queued")


def _member(index: int, state: SubagentState) -> SubagentProgress:
    return SubagentProgress(
        id=f"sub-{index}",
        profile="scout",
        state=state,
        model=_MODEL,
        current_tool="read_file",
        elapsed_ms=33_000,
        tokens=12_300,
        cost=0.0372,
    )


def _snapshots() -> list[SubagentProgress | None]:
    return [_member(0, "running"), _member(1, "running"), _member(2, "done"), None]


async def _panel_on_screen(cols: int) -> tuple[list[str], list[str]]:
    """Paint the real widget; return (all rows, the panel's own rows).

    ``width=cols`` because that is what production passes: ``progress.py``
    measures the live terminal through ``tui.width.terminal_columns`` and hands it
    to ``format_panel``. Leaving it at the default would test the panel against a
    width the chrome is not painting into — the exact mismatch the parameter
    exists to close.
    """

    def build_state(chrome: AelixChrome) -> None:
        chrome.set_widget(
            PANEL_WIDGET_KEY, format_panel(_snapshots(), tasks=_JOBS, width=cols)
        )

    display = await render_chrome_to_screen(rows=24, cols=cols, build_state=build_state)
    # The gutter is the panel's own left edge and nothing else on the chrome draws
    # it, so it identifies the rows without matching the startup banner's box.
    return display, [row for row in display if "▌" in row]


async def test_the_panel_rows_stay_distinct_on_the_glass() -> None:
    """The whole point of the job column: four rows a user can tell apart.

    A constant 24-cell cap on the column returned four labels truncated to the
    same 24-cell prefix — measured, ONE distinct label for this batch. The cap is
    gone; the column is sized from the labels and reserves room for the numbers
    instead.

    THE CAP WAS NOT BUYING EMPTY SPACE, and an earlier draft of this docstring
    said it was ("the row was 47 cells on an 80-column terminal, so the elision
    bought nothing"). MEASURED on this fixture at 80 columns, the capped build's
    rows were 78/78/78/40 cells — full width. What the elision bought was the
    numbers: those rows carried ``running · read_file · 33s · 12.3k tok ·
    $0.0372`` where these carry ``running · read_file · 33s · 12.3k t…``. Four
    distinct labels are worth the tail of the numbers; a row that cannot be told
    from the row above it is not.
    """

    display, panel_rows = await _panel_on_screen(80)

    # Positive control FIRST: an empty match set would satisfy every loop below.
    assert len(panel_rows) == 5, (
        f"expected a header and four member rows, saw {len(panel_rows)}:\n"
        + "\n".join(f"{i:>2} {row!r}" for i, row in enumerate(display))
    )

    header, members = panel_rows[0], panel_rows[1:]
    for tail, row in zip(_TAILS, members, strict=True):
        assert tail in row, (tail, row)
    assert len({row.split("] ", 1)[1].split("  ")[0] for row in members}) == 4, members

    # The state survives beside the labels, because the batch model left the rows.
    for row in members:
        assert any(word in row for word in _STATE_WORDS), row

    # THE HEADER NAMES THE MODEL, POSSIBLY TRUNCATED. It is sized against what the
    # state counts leave, so a 28-cell provider-scoped id lands here as
    # ``github-copilot/gpt-5.6-cod…`` — asserting the whole id would fail on a
    # header that is doing exactly what it promises. The prefix has to be long
    # enough to identify the model, and the counts have to survive whole.
    shown = next(
        (_MODEL[:size] for size in range(len(_MODEL), 0, -1) if _MODEL[:size] in header),
        "",
    )
    assert len(shown) >= 12, (shown, header)
    for count in ("2 running", "1 done", "1 queued"):
        assert count in header, (count, header)
    for row in members:
        # 8 characters, not one: ``g`` is a prefix of this id and of half the
        # English on the screen, so a shorter floor would report the model on rows
        # that never mention it.
        assert not any(
            _MODEL[:size] in row for size in range(len(_MODEL), 7, -1)
        ), row


async def test_a_narrow_terminal_loses_the_numbers_and_keeps_the_jobs() -> None:
    """The documented degradation, on the glass rather than in a docstring.

    The widget window clips instead of wrapping, so at 60 columns an 78-cell row
    loses its right-hand end. Left-packing decides WHICH end that is: the numbers
    go and the job labels — the thing the panel was extended to show — stay.
    """

    display, panel_rows = await _panel_on_screen(60)

    assert len(panel_rows) == 5, "\n".join(repr(r) for r in display)
    members = panel_rows[1:]
    for tail, row in zip(_TAILS, members, strict=True):
        assert tail in row, (tail, row)

    # THE QUEUED ROW IS THE ONE THAT PROVES THE PACKING. Right-aligning the
    # numbers against the 78-cell ceiling made every row exactly 78 cells, so the
    # shortest content — a member that has published nothing but ``queued`` —
    # ended up against the right edge and a 60-column clip took it. Left-packed,
    # it sits directly after its label and survives. A row-length assertion cannot
    # see this: a pyte row is ALWAYS exactly ``cols`` wide.
    assert "queued" in members[-1], members[-1]
    assert members[-1].index("queued") < 50, members[-1]


async def test_the_panel_keeps_the_state_on_a_narrow_terminal() -> None:
    """The column was sized against a constant while the window paints into a
    terminal, and at 30-60 columns a long label took cells that did not exist.

    MEASURED against ``main`` on this same harness, member rows still showing a
    state word with 35-cell labels: main 1/4 1/4 4/4 4/4 at 30/40/50/60 columns,
    against 0/4 0/4 2/4 4/4 before the width was plumbed through — a regression
    this branch introduced and that no constant could fix, because the right
    number is the terminal's. With it, every row keeps its state at all four.

    The labels are what give way instead, and that is the intended order: the
    state is the fact the header cannot restate, while the job label is elided
    rather than lost. At 30 columns with 60-cell labels there is room for neither
    in full, which is a property of 30 columns and not of this layout.
    """

    for cols in (30, 40, 50, 60, 72, 80):
        _display, panel_rows = await _panel_on_screen(cols)
        assert len(panel_rows) == 5, (cols, panel_rows)
        for row in panel_rows[1:]:
            assert any(word in row for word in _STATE_WORDS), (cols, row)


async def test_the_merged_footer_row_clips_the_path_not_the_live_signals() -> None:
    """Extension statuses join the last row, and the path still goes last.

    Composing the row and then appending the extension tail to the STRING put the
    unbounded ``current-dir`` segment back in the middle, so the height-1 clip
    took the extension's own status instead of the path. Read off the glass at 80
    columns, where the composed row is wider than the terminal and something has
    to be lost.
    """

    from aelix_coding_agent.tui.context import AelixTUIContext

    from tests.tui.test_context import _FixedBranchFooter, _MultilineStore

    footer = _FixedBranchFooter("main")
    footer.set_status("lsp", "lsp: 3 diagnostics")
    nested = "/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent"

    def build_state(chrome: AelixChrome) -> None:
        AelixTUIContext(
            chrome,
            footer,
            statusline_store=_MultilineStore(
                ["permission-mode", "steering", "pending-queued", "current-dir"]
            ),  # type: ignore[arg-type]
            cwd=nested,
            permission_badge_provider=lambda: "⚠",
            mode_provider=lambda: "all",
            pending_provider=lambda: 3,
        )

    display = await render_chrome_to_screen(rows=24, cols=80, build_state=build_state)

    row_index = next(
        (i for i, row in enumerate(display) if "lsp: 3 diagnostics" in row), None
    )
    assert row_index is not None, (
        "the extension status is not on the glass at all — which is the defect "
        "this test exists for, and also what a broken detector looks like:\n"
        + "\n".join(f"{i:>2} {row!r}" for i, row in enumerate(display))
    )
    row = display[row_index]
    for live in ("⏵⏵", "3 queued"):
        assert live in row, (live, row)
    # The path is what the 80-column clip eats. MEASURED, this row renders as
    # ``⚠ · ⏵⏵ all · ⋯ 3 queued · lsp: 3 diagnostics · 📂 /workspaces/aelix-ai/p``
    # — the segment is on screen, its tail is gone, and every live signal is
    # complete in front of it.
    assert "📂 /workspaces/aelix-ai/" in row, row
    assert "aelix_coding_agent" not in row, row
    path_at = row.index("📂")
    for live in ("⏵⏵", "3 queued", "lsp: 3 diagnostics"):
        assert row.index(live) < path_at, (live, row)


def test_the_panel_emits_no_escape_the_chrome_will_not_parse() -> None:
    """The panel writes raw SGR itself, so its vocabulary is worth pinning.

    ``_render_widget_lines`` ANSI-parses the joined lines; anything outside the
    plain SGR set is an escape this module invented for a parser it does not own.
    """

    lines = format_panel(_snapshots(), tasks=_JOBS)
    escapes = {seq for line in lines for seq in re.findall(r"\x1b\[[0-9;]*[A-Za-z]", line)}
    assert escapes == {"\x1b[2m", "\x1b[0m"}, escapes
    for line in lines:
        assert "\x9b" not in line
        assert "\n" not in line
