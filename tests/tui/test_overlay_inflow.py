"""Sprint 6h₂₈ (ADR-0159) — in-flow modal slot: no-clip, height-bound, focus.

These tests encode the root-cause fix for the user-reported modal clipping: a
captured modal (``/model`` picker, ``/settings``, the WP-0 approval dialog, …)
mounts in the chrome's in-flow HSplit slot ABOVE the input instead of a centered
``Float``. A Float never contributes to the non-fullscreen app's rendered height,
so a tall modal overflowed below the terminal edge and clipped; the in-flow slot
makes the body's preferred height GROW to include the modal (the renderer then
allocates the taller region, capped at terminal rows, and the terminal scrolls
prior output up), so the whole modal renders.

Driven headlessly under ``create_app_session`` + ``create_pipe_input`` +
``DummyOutput`` (the established 6h₁₀b pattern). ``DummyOutput.get_size()``
returns a real size (40 rows), so the height-cap math is testable.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.overlay import show_modal
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import DummyOutput
from rich.console import Console


@asynccontextmanager
async def _chrome(
    *, run_app: bool = True
) -> AsyncGenerator[tuple[AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        task = asyncio.create_task(chrome.run()) if run_app else None
        try:
            yield chrome, pipe
        finally:
            if task is not None:
                chrome.exit()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, timeout=3)


def _body(chrome: AelixChrome):
    """The body HSplit (the FloatContainer's content)."""
    return chrome.app.layout.container.content


def _tall_window(lines: int) -> Window:
    text = "\n".join(f"line {i}" for i in range(lines))
    return Window(
        FormattedTextControl(text, focusable=True), dont_extend_height=True
    )


# === root-cause regression: grow-upward, no clip =========================


async def test_modal_grows_body_preferred_height() -> None:
    # The core no-clip regression: mounting a 30-line modal makes the body's
    # preferred height jump from the idle value to a value that fills (and would
    # scroll) the terminal — proving the modal is no longer constrained to a
    # Float that ignores body height.
    async with _chrome(run_app=False) as (chrome, _pipe):
        body = _body(chrome)
        idle = body.preferred_height(80, 24).preferred
        assert idle < 24  # idle chrome owns only a few rows

        chrome.mount_modal(_tall_window(30))
        grown = body.preferred_height(80, 24).preferred
        assert grown >= 24  # the modal grew the body to fill the terminal
        assert grown > idle

        chrome.unmount_modal()
        # back to the idle footprint (the slot collapses to 0 rows)
        assert body.preferred_height(80, 24).preferred == idle


async def test_idle_slot_contributes_zero_rows() -> None:
    # With no modal, the slot placeholder is 0 rows — no idle gap in the chrome.
    async with _chrome(run_app=False) as (chrome, _pipe):
        assert chrome.is_modal_open() is False
        assert chrome._render_modal_slot() is chrome._modal_placeholder
        ph = chrome._modal_placeholder
        assert ph.preferred_height(80, 24).preferred == 0


# === height-bounded: a pathologically tall modal cannot overflow =========


async def test_modal_is_height_bounded_to_terminal() -> None:
    # A 100-line modal must be capped well under the terminal height so the input
    # + footer always stay visible (DummyOutput reports 40 rows).
    async with _chrome(run_app=True) as (chrome, _pipe):
        rows = chrome.app.output.get_size().rows

        def build(result: asyncio.Future) -> Window:
            kb = KeyBindings()
            kb.add("escape")(lambda _e: result.set_result(None))
            text = "\n".join(f"row {i}" for i in range(100))
            return Window(
                FormattedTextControl(text, focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        fut = asyncio.ensure_future(show_modal(chrome, build))
        await asyncio.sleep(0.05)
        assert chrome.is_modal_open() is True
        slot = chrome._render_modal_slot()
        capped = slot.preferred_height(80, rows).preferred
        # bounded under the terminal (reserve for input + status + footer)
        assert capped < rows
        assert capped <= rows - 1
        # and the whole body never exceeds the terminal rows
        assert _body(chrome).preferred_height(80, rows).preferred <= rows
        chrome.exit()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(fut, timeout=3)


# === in-flow mount + focus handoff/restore ===============================


async def test_modal_mounts_in_flow_and_holds_focus() -> None:
    async with _chrome(run_app=True) as (chrome, pipe):
        captured: dict[str, object] = {}

        def build(result: asyncio.Future) -> Window:
            kb = KeyBindings()
            kb.add("escape")(lambda _e: result.set_result("done"))
            win = Window(
                FormattedTextControl("modal", focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )
            captured["win"] = win
            return win

        fut = asyncio.ensure_future(show_modal(chrome, build))
        await asyncio.sleep(0.05)
        # mounted in the in-flow slot + the modal window holds layout focus
        assert chrome.is_modal_open() is True
        assert chrome.app.layout.has_focus(captured["win"]) is True
        # not added to the Float list (only the completions float remains)
        assert chrome._floats == [chrome._completions_float]

        # resolve → unmounted + focus restored to the input editor
        pipe.send_text("\x1b")  # Escape
        result = await asyncio.wait_for(fut, timeout=5)
        assert result == "done"
        assert chrome.is_modal_open() is False
        assert chrome.app.layout.has_focus(chrome._input_window) is True


# === AelixOverlayHandle parity (hide blanks the in-flow slot) =============


async def test_handle_hide_blanks_inflow_slot() -> None:
    # hide()/set_hidden() still toggle the shared ``hidden`` dict (now wrapping an
    # in-flow ConditionalContainer): hiding collapses the modal's rendered rows to
    # zero while it stays mounted, then resolution unmounts it.
    from aelix_coding_agent.tui.overlay import AelixOverlayHandle

    async with _chrome(run_app=True) as (chrome, pipe):
        handle_box: dict[str, AelixOverlayHandle] = {}

        def build(result: asyncio.Future) -> Window:
            kb = KeyBindings()
            kb.add("escape")(lambda _e: result.set_result(None))
            text = "\n".join(f"line {i}" for i in range(20))
            return Window(
                FormattedTextControl(text, focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        fut = asyncio.ensure_future(
            show_modal(chrome, build, on_handle=lambda h: handle_box.__setitem__("h", h))
        )
        await asyncio.sleep(0.05)
        body = _body(chrome)
        rows = chrome.app.output.get_size().rows
        visible = body.preferred_height(80, rows).preferred
        handle_box["h"].hide()
        await asyncio.sleep(0.02)
        hidden = body.preferred_height(80, rows).preferred
        assert hidden < visible  # the ConditionalContainer filter blanks the rows
        pipe.send_text("\x1b")  # Escape → resolve + unmount
        await asyncio.wait_for(fut, timeout=5)
        assert chrome.is_modal_open() is False


# === non-capturing overlays keep the Float path (review LOW) ==============


async def test_non_capturing_overlay_uses_float_not_inflow_slot() -> None:
    # A non-capturing overlay (e.g. a descriptor toast) must NOT clobber the
    # single in-flow modal slot and must NOT steal focus from the input — it
    # floats over the body so it can coexist with a concurrently-open picker.
    from aelix_coding_agent.extensions.widget_protocols import OverlayOptions

    async with _chrome(run_app=True) as (chrome, _pipe):
        def build(result: asyncio.Future) -> Window:
            kb = KeyBindings()
            kb.add("escape")(lambda _e: result.set_result(None))
            return Window(
                FormattedTextControl("toast", focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        fut = asyncio.ensure_future(
            show_modal(chrome, build, options=OverlayOptions(non_capturing=True))
        )
        await asyncio.sleep(0.05)
        # NOT mounted in the in-flow slot (slot stays empty)
        assert chrome.is_modal_open() is False
        # added as a Float (alongside the permanent completions float)
        assert len(chrome._floats) == 2
        # the input editor keeps focus (overlay does not steal it)
        assert chrome.app.layout.has_focus(chrome._input_window) is True

        chrome.exit()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(fut, timeout=3)
        # the Float is removed on resolution / teardown
        assert chrome._floats == [chrome._completions_float]


async def test_non_capturing_overlay_coexists_with_capturing_modal() -> None:
    # The single-modal invariant must not silently drop a non-capturing overlay
    # when a capturing modal is open (and vice versa): they live on independent
    # layers (Float vs in-flow slot), so both stay live concurrently.
    from aelix_coding_agent.extensions.widget_protocols import OverlayOptions

    async with _chrome(run_app=True) as (chrome, _pipe):
        def build_capturing(result: asyncio.Future) -> Window:
            kb = KeyBindings()
            kb.add("escape")(lambda _e: result.set_result("picker"))
            return Window(
                FormattedTextControl("picker", focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        def build_toast(result: asyncio.Future) -> Window:
            kb = KeyBindings()
            kb.add("c-q")(lambda _e: result.set_result("toast"))
            return Window(
                FormattedTextControl("toast", focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        picker = asyncio.ensure_future(show_modal(chrome, build_capturing))
        await asyncio.sleep(0.02)
        toast = asyncio.ensure_future(
            show_modal(chrome, build_toast, options=OverlayOptions(non_capturing=True))
        )
        await asyncio.sleep(0.05)
        # the capturing picker still occupies the in-flow slot (not clobbered)
        assert chrome.is_modal_open() is True
        # the toast lives as an additional Float
        assert len(chrome._floats) == 2

        chrome.exit()
        for fut in (picker, toast):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(fut, timeout=3)


# === reserve grows with the live input height (review LOW) ================


async def test_modal_cap_shrinks_when_input_is_multiline() -> None:
    # A multi-line input buffer grows the editor (Dimension min=1,max=10), so the
    # reserve below a modal must grow with it — a near-cap modal + a tall input
    # must not push the footer off the terminal. The cap tightens accordingly.
    from aelix_coding_agent.tui.overlay import _modal_cap

    async with _chrome(run_app=True) as (chrome, _pipe):
        idle_cap = _modal_cap(chrome, None)
        chrome.buffer.text = "\n".join(f"queued line {i}" for i in range(8))
        await asyncio.sleep(0.02)
        tall_cap = _modal_cap(chrome, None)
        assert tall_cap < idle_cap  # reserve grew with the editor → cap shrank


# === fallback cap when output size is unreadable (nit) ====================


async def test_modal_cap_falls_back_when_get_size_raises(monkeypatch) -> None:
    # Defensive branch: if the output size can't be read, the cap falls back to a
    # usable floor rather than crashing the render.
    from aelix_coding_agent.tui import overlay as overlay_mod
    from aelix_coding_agent.tui.overlay import _modal_cap

    async with _chrome(run_app=True) as (chrome, _pipe):
        def _raise() -> object:
            raise RuntimeError("no size")

        monkeypatch.setattr(chrome.app.output, "get_size", _raise)
        cap = _modal_cap(chrome, None)
        # falls back to the documented floor (fallback cap, reserve-adjusted)
        assert cap >= overlay_mod._MODAL_MIN_HEIGHT
        assert cap == overlay_mod._MODAL_FALLBACK_CAP + overlay_mod._MODAL_RESERVE_ROWS - overlay_mod._reserve_rows(chrome)


# === end-to-end render: tall approval dialog still shows the deny option ====


async def test_tall_approval_dialog_renders_deny_option_on_short_terminal() -> None:
    # The user-reported bug, reproduced end-to-end: a mutating-tool approval whose
    # diff body is far taller than a short terminal must STILL render the deny
    # ("No") option row — the security-critical case. The fixed-height options
    # window pinned outside the height cap (HIGH fix) keeps it visible.
    from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest, run_approval_dialog
    from aelix_coding_agent.tui.overlay import show_modal

    big = "\n".join(f"new line {i}" for i in range(200))
    request = ApprovalRequest("write", {"path": "/tmp/x.py", "content": big}, "write")

    def build_state(chrome: AelixChrome) -> None:
        # Fire-and-forget the dialog so the chrome mounts + paints the modal; the
        # result future never resolves (no key pressed) — we only inspect a frame.
        asyncio.ensure_future(
            run_approval_dialog(request=request, show_modal=show_modal, chrome=chrome)
        )

    display = await render_chrome_to_screen(rows=20, cols=80, build_state=build_state)
    joined = "\n".join(display)
    # the deny option row is on screen despite the 200-line body + 20-row terminal
    assert "No" in joined
    assert "Enter to confirm" in joined or "Esc to deny" in joined
