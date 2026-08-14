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
    _MIN_RENDER_WIDTH,
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
    def __init__(self, columns: int | BaseException) -> None:
        self.app = _FakeApp(columns)


# === the reader =============================================================


@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        (80, 80),
        (100, 100),
        (_MAX_RENDER_WIDTH, _MAX_RENDER_WIDTH),
        (60, 60),
        (_MIN_RENDER_WIDTH, _MIN_RENDER_WIDTH),
    ],
)
def test_reads_the_live_terminal_width(columns: int, expected: int) -> None:
    assert terminal_columns(_FakeChrome(columns)) == expected  # type: ignore[arg-type]


def test_ultrawide_terminal_is_capped_for_readability() -> None:
    # Not a technical limit — content is WRAPPED at the cap, never truncated.
    assert terminal_columns(_FakeChrome(400)) == _MAX_RENDER_WIDTH  # type: ignore[arg-type]


def test_very_narrow_terminal_stops_at_the_floor() -> None:
    # Below the floor a bordered Panel has no room for content; we stop
    # shrinking and let the host terminal clip rather than emit a degenerate
    # frame with more border than body.
    assert terminal_columns(_FakeChrome(10)) == _MIN_RENDER_WIDTH  # type: ignore[arg-type]


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


def test_a_sub_floor_budget_is_honoured_over_the_floor() -> None:
    # The floor guards against TERMINAL shrinkage, not against an explicit
    # budget — a caller asking for 20 cells means 20.
    assert terminal_columns(_FakeChrome(200), max_width=20) == 20  # type: ignore[arg-type]


# === end-to-end: the clip this step fixes ===================================

_LONG_COMMAND = "echo ALPHA_BEGIN_MARKER_0123456789_this_is_a_long_command_tail_OMEGA_END_MARKER"
_TAIL = "OMEGA_END_MARKER"


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
    # Nothing overflows the terminal.
    assert all(len(row) <= 60 for row in display)


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
    # The width came from the LIVE terminal, not the module default.
    assert seen[0]["width"] == cols
    assert seen[0]["width"] != 80
