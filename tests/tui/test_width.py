"""Live terminal-width source of truth (issue #166, step 1).

Two layers:

- unit tests for :func:`terminal_columns` (the reader + its clamps);
- an END-TO-END pyte render of the approval dialog on a 60-column terminal,
  which is the only shape that can observe the bug this step fixes. The defect
  was never in the dialog's own rendering — it was prompt-toolkit CLIPPING a
  Panel that had already been rendered wider than the screen, so a test that
  only inspects ``build_approval_view``'s return value cannot see it.

``test_narrow_terminal_default_width_loses_the_command_tail`` deliberately
exercises the OLD behaviour (the un-passed ``width`` default) so the e2e
assertion below it is not vacuous: it documents exactly what regresses if the
``width=`` argument at ``shell.py``'s ``_run_approval`` is ever dropped again.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from aelix_coding_agent.tui.width import (
    _FALLBACK_RENDER_WIDTH,
    _MAX_RENDER_WIDTH,
    terminal_columns,
)

if TYPE_CHECKING:
    from aelix_coding_agent.tui.chrome import AelixChrome


class _FakeSize:
    def __init__(self, columns: int) -> None:
        self.columns = columns
        self.rows = 24


class _FakeOutput:
    def __init__(self, columns: int | BaseException) -> None:
        self._columns = columns

    def get_size(self) -> _FakeSize:
        if isinstance(self._columns, BaseException):
            raise self._columns
        return _FakeSize(self._columns)


class _FakeApp:
    def __init__(self, columns: int | BaseException) -> None:
        self.output = _FakeOutput(columns)


class _FakeChrome:
    def __init__(
        self,
        columns: int | BaseException,
        scrollback_columns: int = 0,
    ) -> None:
        self.app = _FakeApp(columns)
        # 0 = "no opinion", the degraded-console case. A real chrome reports its
        # Rich console width here, which can differ from the ptk reading.
        self.scrollback_columns = scrollback_columns


# === the reader =============================================================


@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        (80, 80),
        (100, 100),
        (_MAX_RENDER_WIDTH, _MAX_RENDER_WIDTH),
        (60, 60),
        (40, 40),
        (30, 30),
    ],
)
def test_reads_the_live_terminal_width(columns: int, expected: int) -> None:
    assert terminal_columns(_FakeChrome(columns)) == expected  # type: ignore[arg-type]


def test_ultrawide_terminal_is_capped_for_readability() -> None:
    # Not a technical limit — content is WRAPPED at the cap, never truncated.
    assert terminal_columns(_FakeChrome(400)) == _MAX_RENDER_WIDTH  # type: ignore[arg-type]


def test_a_very_narrow_terminal_is_honoured_rather_than_floored() -> None:
    """Never return more than the terminal has — there is no floor.

    The first version of this module floored at 40, which re-created the exact
    bug it exists to fix: a 30-column terminal got 40, the Panel was rendered 40
    wide into 30, and prompt-toolkit clipped it. See
    ``test_a_sub_forty_terminal_still_gets_a_closed_frame`` for the rendered
    proof.
    """

    assert terminal_columns(_FakeChrome(30)) == 30  # type: ignore[arg-type]
    assert terminal_columns(_FakeChrome(10)) == 10  # type: ignore[arg-type]
    assert terminal_columns(_FakeChrome(1)) == 1  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "chrome",
    [
        None,
        _FakeChrome(RuntimeError("output not available before run")),
        _FakeChrome(0),  # ioctl "unknown", not "no room"
        _FakeChrome(-1),
    ],
)
def test_unmeasurable_terminal_falls_back_to_the_historical_default(chrome: Any) -> None:
    assert terminal_columns(chrome) == _FALLBACK_RENDER_WIDTH


def test_caller_budget_tightens_but_never_loosens_the_terminal_bound() -> None:
    # A caller with its own budget gets it...
    assert terminal_columns(_FakeChrome(200), max_width=76) == 76  # type: ignore[arg-type]
    # ...but the real terminal still wins when it is the smaller of the two.
    assert terminal_columns(_FakeChrome(50), max_width=76) == 50  # type: ignore[arg-type]


def test_a_tiny_caller_budget_is_honoured() -> None:
    # A caller asking for 20 cells means 20.
    assert terminal_columns(_FakeChrome(200), max_width=20) == 20  # type: ignore[arg-type]


# === end-to-end: the clip this step fixes ===================================

# One unbroken token, so a WRAP splits it across rows while a CLIP removes a
# piece of it. Reassembling the frame's body rows must yield it exactly.
_TOKEN = "ALPHA_BEGIN_MARKER_0123456789_this_is_a_long_command_tail_OMEGA_END_MARKER"
_LONG_COMMAND = f"echo {_TOKEN}"
_TAIL = "OMEGA_END_MARKER"


def _frame_body_text(display: list[str]) -> str:
    """Reassemble the Panel's body, borders and wrap padding removed.

    Rows inside the frame look like ``│ <content> │``. Stripping the borders and
    concatenating with NO separator reconstructs a wrapped token verbatim — so
    ``_TOKEN in _frame_body_text(...)`` is true when the command merely wrapped
    and false when a clip took a bite out of it. (Joining raw rows instead leaves
    ``│`` between the halves, which reads as a lost tail even on a healthy frame
    — the first cut of this file failed that way at 120->70.)
    """

    out: list[str] = []
    for row in display:
        stripped = row.rstrip()
        if stripped.startswith("│") and stripped.endswith("│"):
            out.append(stripped[1:-1].strip())
    return "".join(out)


async def _render_dialog_at(cols: int, width: int | None) -> list[str]:
    from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest, run_approval_dialog
    from aelix_coding_agent.tui.overlay import show_modal

    request = ApprovalRequest("bash", {"command": _LONG_COMMAND}, "bash")

    def build_state(chrome: AelixChrome) -> None:
        # Fire-and-forget: the decision future never resolves (no key pressed);
        # we only want one painted frame. Same idiom as
        # ``test_tall_approval_dialog_renders_deny_option_on_short_terminal``.
        kwargs: dict[str, Any] = {}
        if width is not None:
            kwargs["width"] = width
        asyncio.ensure_future(
            run_approval_dialog(request=request, show_modal=show_modal, chrome=chrome, **kwargs)
        )

    return await render_chrome_to_screen(rows=24, cols=cols, build_state=build_state)


async def test_narrow_terminal_default_width_loses_the_command_tail() -> None:
    """The OLD behaviour, pinned so the fix below cannot become vacuous.

    Rendering the dialog at the historical fixed 80 into a 60-column terminal
    hands prompt-toolkit rows wider than the screen. It clips them: the right
    border disappears and the command is cut mid-token with no ellipsis, so the
    user approves a command whose tail they were never shown.
    """

    display = await _render_dialog_at(cols=60, width=80)
    joined = "\n".join(display)
    assert "Run shell command?" in joined  # the dialog really did paint
    assert _TAIL not in joined  # ...but the tail of the command did not survive


async def test_narrow_terminal_live_width_keeps_the_whole_command() -> None:
    """The fix: sized to the live terminal, the Panel wraps instead of clipping."""

    display = await _render_dialog_at(cols=60, width=terminal_columns(_FakeChrome(60)))  # type: ignore[arg-type]
    joined = "\n".join(display)
    assert "Run shell command?" in joined
    # Every character of the command is on screen (wrapped across rows).
    assert _TAIL in joined
    # And the frame is closed — the right border survives on every dialog row.
    assert "╮" in joined
    assert "╯" in joined
    # NOTE: do NOT assert ``len(row) <= cols`` here. ``pyte.Screen.display``
    # pads every row to exactly ``cols``, so such a check holds unconditionally
    # and proves nothing (the review pass caught it doing exactly that). The
    # closed frame above IS the overflow assertion: an over-wide Panel loses its
    # right border, which is what ``╮``/``╯`` detect.


@pytest.mark.parametrize("cols", [30, 36, 40])
async def test_a_sub_forty_terminal_still_gets_a_closed_frame(cols: int) -> None:
    """A terminal narrower than a comfortable Panel must still not be clipped.

    This is the regression the first cut of ``width.py`` shipped: a floor of 40
    meant a 30-column terminal was handed 40, which is the pre-fix bug exactly.
    Measured at the time through this same harness, cols=30 and cols=36 both
    came back with an unclosed frame. A cramped frame is a cosmetic problem; an
    unclosed one hides the command being approved.
    """

    display = await _render_dialog_at(cols=cols, width=terminal_columns(_FakeChrome(cols)))  # type: ignore[arg-type]
    joined = "\n".join(display)
    assert "╮" in joined, f"frame not closed at cols={cols}"
    assert "╯" in joined, f"frame not closed at cols={cols}"


# === the wiring ==============================================================
#
# The two e2e tests above pass an explicit ``width``, so they prove the
# MECHANISM and would pass on the unfixed build too. This one proves the
# WIRING: that ``run_tui`` actually hands the approval dialog a width taken from
# the live chrome instead of letting the 80 default ship. It fails on the
# unfixed build.


async def test_run_tui_sizes_the_approval_dialog_from_the_live_terminal() -> None:
    import aelix_coding_agent.tui.approval_dialog as approval_mod
    from aelix_coding_agent.tui.approval_dialog import ApprovalDecision, ApprovalRequest
    from aelix_coding_agent.tui.chrome import AelixChrome
    from aelix_coding_agent.tui.shell import run_tui
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output
    from test_run_tui_smoke import FakeHarness, FakeRuntime, _wait  # sibling module

    cols = 57  # neither the old default (80) nor a clamp boundary
    seen: list[dict[str, Any]] = []

    async def _spy(**kwargs: Any) -> ApprovalDecision:
        seen.append(kwargs)
        return ApprovalDecision.NO

    class _FakePermissionExt:
        approval_runner: Any = None

    import io

    real_runner = approval_mod.run_approval_dialog
    # shell.py imports the symbol INSIDE run_tui, so patching the module
    # attribute before the coroutine runs is what the production path resolves.
    approval_mod.run_approval_dialog = _spy  # type: ignore[assignment]
    try:
        output = Vt100_Output(
            io.StringIO(), get_size=lambda: Size(rows=24, columns=cols), term="xterm-256color"
        )
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
            runtime = FakeRuntime(FakeHarness())
            chrome = AelixChrome(pt_input=pipe, pt_output=output)
            permission_ext = _FakePermissionExt()
            task = asyncio.ensure_future(
                run_tui(
                    runtime,  # type: ignore[arg-type]
                    cwd=".",
                    chrome=chrome,
                    install_signal_handlers=False,
                    permission_ext=permission_ext,  # type: ignore[arg-type]
                )
            )
            await _wait(lambda: chrome.app.is_running)
            await _wait(lambda: permission_ext.approval_runner is not None)

            await permission_ext.approval_runner(
                ApprovalRequest("bash", {"command": _LONG_COMMAND}, "bash")
            )

            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=5)
    finally:
        approval_mod.run_approval_dialog = real_runner  # type: ignore[assignment]

    assert len(seen) == 1
    given = seen[0]["width"]
    # A CALLABLE, not a number: the dialog must be able to re-resolve the width
    # on every render, or a resize while the prompt is open clips the baked rows.
    assert callable(given), f"run_tui passed a fixed width ({given!r})"
    # ...and it resolves to the LIVE terminal, not the module default.
    assert given() == cols
    assert given() != 80


# === resize WHILE the prompt is open ========================================
#
# The review pass on the first cut caught this as a HIGH: passing the resolved
# NUMBER made the dialog strictly worse than the historical fixed 80 on any
# terminal wider than 80. The Panel was baked at open width, the resize repaint
# clipped the stale rows, and because the Panel had already wrapped, the clip
# removed a chunk from the MIDDLE of the command — leaving text that reads like
# a different command rather than a visibly truncated one.


async def _render_dialog_with_resize(start: int, end: int, *, bake: bool = False) -> list[str]:
    """Mount the dialog at *start* columns, resize to *end*, return the frame.

    ``bake=True`` reproduces the PRE-FIX wiring — the width resolved once and
    passed as a number — so the test below it can pin what that costs.
    """

    import contextlib
    import io

    import pyte
    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest, run_approval_dialog
    from aelix_coding_agent.tui.chrome import AelixChrome
    from aelix_coding_agent.tui.overlay import show_modal
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output
    from rich.console import Console

    size = {"cols": start}
    capture = io.StringIO()
    output = Vt100_Output(
        capture,
        get_size=lambda: Size(rows=24, columns=size["cols"]),
        term="xterm-256color",
        enable_cpr=True,
    )
    request = ApprovalRequest("bash", {"command": _LONG_COMMAND}, "bash")

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        chrome = AelixChrome(
            console=Console(file=io.StringIO(), force_terminal=True, width=start),
            pt_input=pipe,
            pt_output=output,
            time_fn=lambda: 0.0,
        )
        # Production wiring: the READER, not its value (shell.py's _run_approval).
        width: int | Any = terminal_columns(chrome) if bake else (lambda: terminal_columns(chrome))
        asyncio.ensure_future(
            run_approval_dialog(
                request=request,
                show_modal=show_modal,
                chrome=chrome,
                width=width,
            )
        )
        task = asyncio.create_task(chrome.run())
        try:
            for _ in range(500):
                await asyncio.sleep(0.01)
                if chrome.app.is_running:
                    break
            await asyncio.sleep(0.02)
            pipe.send_text("\x1b[10;1R")  # CPR → height known → rows paint
            await asyncio.sleep(0.05)

            size["cols"] = end  # the user narrows the window...
            capture.truncate(0)
            capture.seek(0)  # ...and we keep ONLY the post-resize frame
            chrome.app._on_resize()  # what ptk's SIGWINCH handler calls
            await asyncio.sleep(0.08)
            chrome.invalidate()
            await asyncio.sleep(0.05)
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)

    screen = pyte.Screen(end, 24)
    pyte.Stream(screen).feed(capture.getvalue())
    return list(screen.display)


@pytest.mark.parametrize(("start", "end"), [(100, 90), (100, 80), (120, 70)])
async def test_resizing_while_the_prompt_is_open_keeps_the_whole_command(
    start: int, end: int
) -> None:
    display = await _render_dialog_with_resize(start, end)
    joined = "\n".join(display)
    assert "Run shell command?" in joined, f"dialog not painted after {start}->{end}"
    assert "╮" in joined and "╯" in joined, f"frame clipped after {start}->{end}"
    # The command survives the resize intact — no bite taken out of its middle.
    assert _TOKEN in _frame_body_text(display), f"command damaged after {start}->{end}"


@pytest.mark.parametrize(("start", "end"), [(100, 80), (120, 70)])
async def test_a_baked_width_damages_the_command_on_resize(start: int, end: int) -> None:
    """The pre-fix wiring, pinned so the fix above cannot become vacuous.

    Resolving the width once and passing the NUMBER was strictly worse than the
    historical fixed 80 for any terminal wider than 80: the repaint clips rows
    that were laid out at the old width, and since the Panel had already wrapped,
    the clip removes a piece from the MIDDLE of the command.
    """

    display = await _render_dialog_with_resize(start, end, bake=True)
    joined = "\n".join(display)
    assert "Run shell command?" in joined  # the dialog really did paint
    assert "╯" not in joined  # ...but its frame was clipped open
    assert _TOKEN not in _frame_body_text(display)  # ...and the command was damaged


# === the two layers disagree ================================================
#
# Rich's Console.size lets $COLUMNS override the ioctl; prompt-toolkit's vt100
# output asks the ioctl only. The renderer lays text out at one width and the
# scrollback console re-wraps it at the other. Measured on a real 200-column pty
# with COLUMNS=80 exported: ptk 200, rich 80, and the streamed row came back at
# 79 cells — the feature silently reverting to the ribbon it exists to remove.
#
# The pyte harness cannot see this: tests/tui/_pyte.py injects
# Console(width=cols), forcing the two readings equal by construction. So this
# has to be asserted here, on the reader itself.


def test_the_scrollback_console_narrows_the_render_width() -> None:
    """min(ptk, rich) — an exported COLUMNS may only make it SMALLER."""

    chrome = _FakeChrome(200, scrollback_columns=80)
    assert terminal_columns(chrome) == 80  # type: ignore[arg-type]


def test_a_wider_scrollback_console_does_not_widen_the_render_width() -> None:
    """...and never larger: the ioctl still bounds it."""

    chrome = _FakeChrome(70, scrollback_columns=200)
    assert terminal_columns(chrome) == 70  # type: ignore[arg-type]


def test_a_console_with_no_opinion_is_ignored() -> None:
    """A degraded console reports 0, which must not collapse the width to 1."""

    assert terminal_columns(_FakeChrome(100, scrollback_columns=0)) == 100  # type: ignore[arg-type]


def test_a_raising_console_accessor_is_ignored() -> None:
    """A chrome torn down mid-read must not take the render width with it."""

    class _Raising:
        def __init__(self) -> None:
            self.app = _FakeApp(100)

        @property
        def scrollback_columns(self) -> int:
            raise RuntimeError("console torn down")

    assert terminal_columns(_Raising()) == 100  # type: ignore[arg-type]


def test_the_ceiling_still_applies_after_the_min() -> None:
    chrome = _FakeChrome(400, scrollback_columns=300)
    assert terminal_columns(chrome) == _MAX_RENDER_WIDTH  # type: ignore[arg-type]
