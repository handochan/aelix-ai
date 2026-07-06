"""Sprint 6h₁₀b (ADR-0105) — AelixTUIContext conformance + dialog tests.

Drives the concrete ExtensionUIContext headlessly (pipe input + DummyOutput
under create_app_session). Conformance: satisfies the Protocol and no method
raises NotImplementedError. Dialogs: driven by feeding keys to the modal Float.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from aelix_coding_agent.extensions.ext_ui import ExtensionUIContext
from aelix_coding_agent.extensions.widget_protocols import Theme
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.widgets import LinesComponent
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


@asynccontextmanager
async def _ctx(
    *, run_app: bool
) -> AsyncGenerator[tuple[AelixTUIContext, AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        task = asyncio.create_task(chrome.run()) if run_app else None
        try:
            yield ctx, chrome, pipe
        finally:
            if task is not None:
                chrome.exit()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, timeout=3)


# === conformance =======================================================


async def test_satisfies_extension_ui_context_protocol() -> None:
    async with _ctx(run_app=False) as (ctx, _chrome, _pipe):
        assert isinstance(ctx, ExtensionUIContext)


async def test_non_dialog_methods_do_not_raise() -> None:
    async with _ctx(run_app=False) as (ctx, chrome, _pipe):
        ctx.set_status("git", "main")
        assert chrome._status["git"] == "main"
        ctx.set_working_message("busy")
        ctx.set_working_visible(True)
        ctx.set_working_indicator(None)
        ctx.set_hidden_thinking_label("…")
        ctx.set_title("aelix")
        ctx.set_widget("w", ["hi"])
        ctx.set_header(None)
        ctx.set_footer(None)
        ctx.set_tools_expanded(True)
        assert ctx.get_tools_expanded() is True
        ctx.paste_to_editor("x")
        assert "x" in ctx.get_editor_text()
        ctx.set_editor_text("hello")
        assert ctx.get_editor_text() == "hello"
        assert ctx.get_editor_component() is None
        unsub = ctx.on_terminal_input(lambda _d: None)
        unsub()
        ctx.add_autocomplete_provider(lambda current: current)
        ctx.notify("hi there")
        assert chrome._status["__notify__"] == "hi there"


async def test_footer_shows_pending_queued_segment() -> None:
    # Sprint 6h₁₂e — the footer shows "⋯ N queued" when pending_provider returns
    # a positive count, and omits the segment when it returns 0.
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        pending = {"n": 2}
        ctx = AelixTUIContext(
            chrome,
            AelixFooterData(cwd="."),
            pending_provider=lambda: pending["n"],
        )
        assert "⋯ 2 queued" in chrome._footer_line
        # drains to 0 → segment omitted
        pending["n"] = 0
        ctx._refresh_footer()
        assert "queued" not in chrome._footer_line


async def test_footer_permission_badge_segment() -> None:
    # WP-0 (ADR-0157) + ADR-0159 — the permission posture badge is the LEADING
    # footer segment, shown at ALL times when a posture is wired: a glyph badge
    # (✎/⏸/⚠/🤖) for non-DEFAULT modes and a neutral "● default" label on
    # DEFAULT (provider returns None). Distinct from the ⏵⏵ steering segment.
    from aelix_coding_agent.builtin.permission_mode import DEFAULT_BADGE

    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        badge = {"v": None}
        ctx = AelixTUIContext(
            chrome,
            AelixFooterData(cwd="."),
            mode_provider=lambda: "all",  # steering segment present (non-default)
            permission_badge_provider=lambda: badge["v"],
        )
        # DEFAULT → the neutral "● default" label shows as the LEADING segment,
        # and the steering ⏵⏵ segment (switched to "all") shows after it.
        line = chrome._footer_line
        assert DEFAULT_BADGE in line
        assert "⏵⏵ all" in line
        assert "⏸" not in line and "⚠" not in line
        # The permission badge is leftmost (before the steering segment).
        assert line.index(DEFAULT_BADGE) < line.index("⏵⏵ all")
        # Set the plan badge → its glyph appears (leading), separate from ⏵⏵.
        badge["v"] = "⏸ plan"
        ctx._refresh_footer()
        line = chrome._footer_line
        assert "⏸ plan" in line
        assert DEFAULT_BADGE not in line  # live badge replaces the default label
        assert "⏵⏵ all" in line  # steering segment unaffected
        assert line.index("⏸ plan") < line.index("⏵⏵ all")  # badge leftmost
        # yolo badge
        badge["v"] = "⚠ yolo"
        ctx._refresh_footer()
        assert "⚠ yolo" in chrome._footer_line


async def test_footer_no_permission_provider_omits_badge() -> None:
    # ADR-0159 — headless / no-posture wiring (provider is None) degrades: no
    # permission badge segment at all, and it must not crash.
    from aelix_coding_agent.builtin.permission_mode import DEFAULT_BADGE

    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        AelixTUIContext(chrome, AelixFooterData(cwd="."))  # no providers wired
        assert DEFAULT_BADGE not in chrome._footer_line
        assert "●" not in chrome._footer_line


async def test_footer_hides_steering_when_one_at_a_time() -> None:
    # ADR-0159 — the steering ⏵⏵ segment is hidden at the default
    # "one-at-a-time"; it surfaces only when switched to "all".
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        mode = {"v": "one-at-a-time"}
        ctx = AelixTUIContext(
            chrome, AelixFooterData(cwd="."), mode_provider=lambda: mode["v"]
        )
        assert "⏵⏵" not in chrome._footer_line  # hidden at the default value
        mode["v"] = "all"
        ctx._refresh_footer()
        assert "⏵⏵ all" in chrome._footer_line  # surfaces when switched


async def test_theme_methods() -> None:
    async with _ctx(run_app=False) as (ctx, _chrome, _pipe):
        assert isinstance(ctx.theme, Theme)
        infos = ctx.get_all_themes()
        assert any(i.name == "dark" for i in infos)
        assert ctx.get_theme("dark") is not None
        assert ctx.get_theme("nope") is None
        ok = ctx.set_theme("dark")
        assert ok.success is True and ctx.theme.name == "dark"
        bad = ctx.set_theme("nope")
        assert bad.success is False and bad.error


# === dialogs (app-driven) ==============================================


async def test_confirm_yes() -> None:
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.confirm("Quit?", "Are you sure?"))
        await asyncio.sleep(0.05)
        pipe.send_text("y")
        assert await asyncio.wait_for(fut, timeout=5) is True


async def test_confirm_no_via_escape() -> None:
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.confirm("Quit?", "Sure?"))
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")  # Escape
        assert await asyncio.wait_for(fut, timeout=5) is False


async def test_confirm_ctrl_c_cancels() -> None:
    # Sprint 6h₂₄ W-review LOW-4: c-c cancels the confirm dialog (consistent
    # with select/editor); previously leaked to the chrome global handler.
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.confirm("Quit?", "Sure?"))
        await asyncio.sleep(0.05)
        pipe.send_text("\x03")  # Ctrl+C
        assert await asyncio.wait_for(fut, timeout=5) is False


async def test_input_ctrl_c_cancels() -> None:
    # Sprint 6h₂₄ W-review LOW-4: c-c cancels the input dialog.
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.input("Name?"))
        await asyncio.sleep(0.05)
        pipe.send_text("\x03")  # Ctrl+C
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_input_returns_text() -> None:
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.input("Name?"))
        await asyncio.sleep(0.05)
        pipe.send_text("alice\n")
        assert await asyncio.wait_for(fut, timeout=5) == "alice"


async def test_select_arrow_down_then_enter() -> None:
    # Sprint 6h₂₄: arrow-key + Enter is the canonical confirm path now (digit
    # shortcuts dropped — they collided with type-to-filter, e.g. filtering
    # for "gpt-4" needed "4" to be a filter char, not a select-row-4 shortcut).
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green", "blue"]))
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b[B")  # Down — moves cursor to "green"
        pipe.send_text("\r")  # Enter — confirm
        assert await asyncio.wait_for(fut, timeout=5) == "green"


async def test_select_arrow_up_wraps() -> None:
    # Sprint 6h₂₄: ↑ at the top wraps to the last item (pi parity).
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green", "blue"]))
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b[A")  # Up — wraps from idx 0 to last ("blue")
        pipe.send_text("\r")
        assert await asyncio.wait_for(fut, timeout=5) == "blue"


async def test_select_space_confirms() -> None:
    # Sprint 6h₂₄: pi's hint reads "Enter/Space to change" — Space confirms
    # too. ALSO documents that Space cannot be used as a filter char (the
    # caller must accept that constraint — single-word options only).
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green", "blue"]))
        await asyncio.sleep(0.05)
        pipe.send_text(" ")
        assert await asyncio.wait_for(fut, timeout=5) == "red"


async def test_select_escape_cancels() -> None:
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green", "blue"]))
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")  # Escape
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_select_type_to_filter_then_enter() -> None:
    # Sprint 6h₂₄: typing narrows the visible list; Enter confirms the
    # cursor row within the FILTERED view. Here "g" filters to just
    # "green", so the only-remaining row is what Enter resolves.
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green", "blue"]))
        await asyncio.sleep(0.05)
        pipe.send_text("g")
        pipe.send_text("\r")
        assert await asyncio.wait_for(fut, timeout=5) == "green"


async def test_select_with_detail_resolves_normally() -> None:
    # Sprint 6h₂₆ (ADR-0154): passing the optional detail callback must not change
    # navigation/confirm — the picker still resolves the highlighted row. (Detail
    # CONTENT is unit-tested in test_model_picker.py; whether the headless harness
    # repaints to invoke the callback is not asserted here.)
    def detail(i: int) -> list[str]:
        return [f"detail-{i}"]

    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.select("Pick", ["red", "green", "blue"], detail=detail)
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b[B")  # Down → highlight "green"
        pipe.send_text("\r")  # Enter → confirm
        assert await asyncio.wait_for(fut, timeout=5) == "green"


async def test_select_detail_exception_does_not_break_modal() -> None:
    # A raising detail callback is cosmetic-only and must never break the picker.
    def detail(_i: int) -> list[str]:
        raise RuntimeError("boom")

    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green"], detail=detail))
        await asyncio.sleep(0.05)
        pipe.send_text("\r")  # Enter still resolves the highlighted row
        assert await asyncio.wait_for(fut, timeout=5) == "red"


async def test_select_initial_index_starts_at_given_row() -> None:
    # Sprint 6h₃₀ (ADR-0163): initial_index restores the cursor so /settings keeps
    # your row after returning from a sub-picker (e.g. the /model selector) instead
    # of snapping to the top. Enter immediately confirms the highlighted row.
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.select("Pick", ["red", "green", "blue"], initial_index=2)
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\r")  # Enter — confirm the initially-highlighted row
        assert await asyncio.wait_for(fut, timeout=5) == "blue"


async def test_select_initial_index_out_of_range_is_clamped() -> None:
    # Sprint 6h₃₀ (ADR-0163): an out-of-range initial_index clamps to the last row.
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.select("Pick", ["red", "green"], initial_index=99)
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\r")
        assert await asyncio.wait_for(fut, timeout=5) == "green"


async def test_select_empty_options_resolves_none() -> None:
    # Sprint 6h₂₄: zero options → no modal opens, immediate None.
    async with _ctx(run_app=False) as (ctx, _chrome, _pipe):
        assert await ctx.select("Pick", []) is None


async def test_select_no_match_enter_stays_open() -> None:
    # Sprint 6h₂₄ W-review LOW-3: when the filter yields zero matches, Enter
    # must be a no-op (NOT resolve with the first un-filtered item). Otherwise
    # a stale cursor + a "no matches" view could silently confirm a hidden row.
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green", "blue"]))
        await asyncio.sleep(0.05)
        pipe.send_text("zzz")  # filter matches nothing
        await asyncio.sleep(0.05)
        pipe.send_text("\r")  # Enter → must not resolve
        await asyncio.sleep(0.1)
        assert not fut.done()
        pipe.send_text("\x1b")  # Esc → cancel cleanly
        assert await asyncio.wait_for(fut, timeout=5) is None


# === review-fix coverage (ADR-0105 W4) =================================


async def _wait_float(chrome: AelixChrome, *, timeout: float = 3.0) -> None:
    # Sprint 6h₂₈ (ADR-0159): captured modals mount in the in-flow slot, not the
    # Float list — wait on ``is_modal_open()`` rather than ``chrome._floats``.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if chrome.is_modal_open():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("modal not mounted")


async def test_select_supports_more_than_nine_options() -> None:
    # Sprint 6h₂₄: the 9-option cap is gone — arrow keys (or type-to-filter)
    # handle any list size. Here we filter for "o11" → only "o11" remains →
    # Enter confirms. Documents that the picker scales past 9 items.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        options = [f"o{i}" for i in range(12)]
        fut = asyncio.ensure_future(ctx.select("Pick", options))
        await _wait_float(chrome)
        pipe.send_text("o11")
        pipe.send_text("\r")
        assert await asyncio.wait_for(fut, timeout=5) == "o11"


async def test_editor_ctrl_s_saves() -> None:
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.editor("Edit", prefill="hello"))
        await _wait_float(chrome)
        pipe.send_text("\x13")  # Ctrl+S → save
        assert await asyncio.wait_for(fut, timeout=5) == "hello"


async def test_editor_escape_cancels() -> None:
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.editor("Edit", prefill="hi"))
        await _wait_float(chrome)
        pipe.send_text("\x1b")  # Esc → cancel (consistent with other dialogs)
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_custom_resolves_via_done_callback() -> None:
    def factory(tui, theme, kb, done):
        done("custom-result")
        return LinesComponent(["custom widget"])

    async with _ctx(run_app=False) as (ctx, _chrome, _pipe):
        result = await asyncio.wait_for(ctx.custom(factory), timeout=5)
    assert result == "custom-result"


async def test_custom_awaitable_factory() -> None:
    async def factory(tui, theme, kb, done):
        done("async-result")
        return LinesComponent(["w"])

    async with _ctx(run_app=False) as (ctx, _chrome, _pipe):
        result = await asyncio.wait_for(ctx.custom(factory), timeout=5)
    assert result == "async-result"


async def test_set_widget_factory_path() -> None:
    async with _ctx(run_app=False) as (ctx, chrome, _pipe):
        ctx.set_widget("w", lambda tui, theme: LinesComponent(["factory-line"]))
        assert "factory-line" in str(chrome._render_widgets_above())


async def test_notify_token_prevents_premature_clear() -> None:
    async with _ctx(run_app=False) as (ctx, chrome, _pipe):
        ctx.notify("first")
        ctx.notify("second")
        assert chrome._status["__notify__"] == "second"
        ctx._clear_notify(1)  # stale timer must NOT clear the newer notification
        assert chrome._status["__notify__"] == "second"
        ctx._clear_notify(2)  # current token clears it
        assert "__notify__" not in chrome._status


# === Sprint 6h₁₂b — status footer compose ==============================


class _FixedBranchFooter(AelixFooterData):
    """AelixFooterData with a deterministic branch (no real .git lookup)."""

    def __init__(self, branch: str | None) -> None:
        super().__init__(cwd=".")
        self._branch = branch

    def get_git_branch(self) -> str | None:
        return self._branch


@asynccontextmanager
async def _footer_chrome(
    footer: AelixFooterData,
    *,
    model_provider=None,
    cwd=None,
    mode: str = "default",
) -> AsyncGenerator[tuple[AelixTUIContext, AelixChrome]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(
            chrome, footer, model_provider=model_provider, cwd=cwd, mode=mode
        )
        yield ctx, chrome


async def test_footer_composes_mode_cwd_model_branch_in_order() -> None:
    footer = _FixedBranchFooter("main")
    # Use a NON-default steering mode ("all"): the ⏵⏵ segment is HIDDEN at the
    # "one-at-a-time" sentinel (and the legacy "default" string) per ADR-0159, so
    # it only surfaces — and so is only orderable here — when steering is "all".
    async with _footer_chrome(
        footer, model_provider=lambda: "Qwen/Qwen3.6-35B", cwd="/tmp/proj", mode="all"
    ) as (_ctx_obj, chrome):
        line = chrome._footer_line
        assert "⏵⏵ all" in line
        assert "📂 /tmp/proj" in line
        assert "✱ Qwen/Qwen3.6-35B" in line
        assert "⎇ main" in line
        # order: mode → cwd → model → branch
        assert (
            line.index("⏵⏵")
            < line.index("📂")
            < line.index("✱")
            < line.index("⎇")
        )
        assert "  ·  " in line  # segments joined by the bullet separator


@pytest.mark.parametrize("mode", ["default", "one-at-a-time", ""])
async def test_footer_hides_steering_segment_for_default_modes(mode: str) -> None:
    # ADR-0159 (review MEDIUM): the ⏵⏵ steering segment is omitted for the
    # "one-at-a-time" sentinel, the legacy "default" string (which is NOT a real
    # steering mode), and the empty fallback — so the footer never shows a stray,
    # misleading "⏵⏵ default".
    footer = _FixedBranchFooter("main")
    async with _footer_chrome(footer, model_provider=None, mode=mode) as (_ctx_obj, chrome):
        assert "⏵⏵" not in chrome._footer_line


async def test_footer_hides_steering_segment_when_provider_returns_none() -> None:
    # When the mode_provider returns None (harness lacks steering_mode), the
    # footer must fall back to the hidden "one-at-a-time" sentinel — never a stray
    # "⏵⏵ default" (ADR-0159, review MEDIUM).
    footer = _FixedBranchFooter("main")
    console = Console(file=io.StringIO(), force_terminal=True, width=200)
    chrome = AelixChrome(console=console)
    ctx = AelixTUIContext(chrome, footer, mode_provider=lambda: None)
    ctx._refresh_footer()
    assert "⏵⏵" not in chrome._footer_line


async def test_footer_omits_model_segment_when_provider_returns_none() -> None:
    footer = _FixedBranchFooter("main")
    async with _footer_chrome(footer, model_provider=lambda: None) as (_ctx_obj, chrome):
        line = chrome._footer_line
        assert "✱" not in line  # no model → segment dropped
        assert "⎇ main" in line


async def test_footer_degrades_to_branch_only_with_no_sources() -> None:
    footer = _FixedBranchFooter("dev")
    # No model_provider, no cwd, empty mode → only the branch survives.
    async with _footer_chrome(footer, model_provider=None, cwd=None, mode="") as (
        _ctx_obj,
        chrome,
    ):
        assert chrome._footer_line == "⎇ dev"


async def test_footer_home_abbreviates_cwd() -> None:
    from pathlib import Path

    footer = _FixedBranchFooter(None)
    home = str(Path.home())
    async with _footer_chrome(footer, cwd=f"{home}/proj", mode="") as (_ctx_obj, chrome):
        assert "📂 ~/proj" in chrome._footer_line


# === Sprint 6h₁₄a (ADR-0121 W-review) — modal Enter routing ================


async def test_editor_enter_inserts_newline_then_ctrl_s_saves() -> None:
    # ADR-0121 M2: the multiline editor must insert a newline on Enter (was
    # leaking to the chrome global accept and losing the line break).
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.editor("Edit"))
        await _wait_float(chrome)
        pipe.send_text("a\nb\x13")  # 'a', Enter(→newline), 'b', Ctrl+S to save
        assert await asyncio.wait_for(fut, timeout=5) == "a\nb"


async def test_select_enter_confirms_cursor_row() -> None:
    # Sprint 6h₂₄: Enter now CONFIRMS the cursor row (was: no-op). idx defaults
    # to 0 so the first option is the default. The chrome-leak prevention that
    # motivated the old no-op behavior (ADR-0121 M1) is preserved — Enter is
    # still bound at the modal layer, just to a confirm rather than a no-op.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green"]))
        await _wait_float(chrome)
        pipe.send_text("\n")
        assert await asyncio.wait_for(fut, timeout=5) == "red"


async def test_confirm_enter_is_noop_then_y_resolves() -> None:
    # ADR-0121 M1: Enter on a confirm must NOT auto-answer — explicit y/n only.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.confirm("Sure?", "really?"))
        await _wait_float(chrome)
        pipe.send_text("\n")  # Enter → no-op (never auto-approves)
        await asyncio.sleep(0.1)
        assert not fut.done()
        pipe.send_text("y")
        assert await asyncio.wait_for(fut, timeout=5) is True


# === WP-8 (the shared tabbed() primitive) ==================================


async def test_tabbed_empty_returns_immediately() -> None:
    # Empty tabs must return without mounting a modal (spec).
    async with _ctx(run_app=True) as (ctx, chrome, _pipe):
        result = await asyncio.wait_for(ctx.tabbed("T", []), timeout=5)
        assert result is None
        assert not chrome.is_modal_open()


async def test_tabbed_escape_closes() -> None:
    # Esc closes the viewer and tabbed() resolves to None.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed("T", [("A", lambda: ["a-body"]), ("B", lambda: ["b-body"])])
        )
        await _wait_float(chrome)
        pipe.send_text("\x1b")  # Escape
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_tabbed_q_closes() -> None:
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.tabbed("T", [("A", lambda: ["a"])]))
        await _wait_float(chrome)
        pipe.send_text("q")  # q closes
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_tabbed_ctrl_c_closes() -> None:
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.tabbed("T", [("A", lambda: ["a"])]))
        await _wait_float(chrome)
        pipe.send_text("\x03")  # Ctrl+C
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_tabbed_tab_key_advances_active_tab() -> None:
    # Tab moves to the next tab; the active tab's render() is what shows. Verify
    # by checking which render closure fired (recorded into a shared list).
    rendered: list[str] = []

    def _render(name: str) -> list[str]:
        rendered.append(name)
        return [f"body-{name}"]

    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed(
                "T",
                [("A", lambda: _render("A")), ("B", lambda: _render("B"))],
            )
        )
        await _wait_float(chrome)
        await asyncio.sleep(0.1)  # let the first paint fire
        # First render shows tab A (index 0).
        assert "A" in rendered
        rendered.clear()
        pipe.send_text("\t")  # Tab → advance to B
        await asyncio.sleep(0.15)
        assert "B" in rendered  # the second tab's render fired
        pipe.send_text("\x1b")
        await asyncio.wait_for(fut, timeout=5)


async def test_tabbed_left_arrow_wraps_to_last() -> None:
    # ← (prev) from the first tab wraps to the last (spec: both directions wrap).
    rendered: list[str] = []

    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed(
                "T",
                [
                    ("A", lambda: (rendered.append("A"), ["a"])[1]),
                    ("B", lambda: (rendered.append("B"), ["b"])[1]),
                    ("C", lambda: (rendered.append("C"), ["c"])[1]),
                ],
            )
        )
        await _wait_float(chrome)
        await asyncio.sleep(0.1)  # let the first paint fire
        rendered.clear()
        pipe.send_text("\x1b[D")  # Left → wraps from A to the last tab (C)
        await asyncio.sleep(0.15)
        assert "C" in rendered
        pipe.send_text("\x1b")
        await asyncio.wait_for(fut, timeout=5)


async def test_tabbed_raising_tab_does_not_break_modal() -> None:
    # A render() that raises shows an error line, never crashes the modal: the
    # modal stays open and Esc still closes it.
    def _boom() -> list[str]:
        raise RuntimeError("kaboom")

    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.tabbed("T", [("Bad", _boom)]))
        await _wait_float(chrome)
        await asyncio.sleep(0.05)
        assert chrome.is_modal_open()  # survived the raising render
        pipe.send_text("\x1b")
        assert await asyncio.wait_for(fut, timeout=5) is None


# === WP-8 (Feature 5 — multi-line statusline composer) =====================


def _multiline_ctx(
    chrome: AelixChrome,
    footer: AelixFooterData,
    *,
    multiline: bool,
    **kwargs,
) -> AelixTUIContext:
    from aelix_coding_agent.tui.statusline_store import StatuslineConfig, StatuslineStore

    class _MemStore(StatuslineStore):
        def __init__(self, cfg: StatuslineConfig) -> None:
            self._cfg = cfg

        def load(self) -> StatuslineConfig:  # type: ignore[override]
            return self._cfg

    # All default-on segments enabled so the row grouping is exercised.
    from aelix_coding_agent.tui.footer_segments import default_enabled_ids_from_spec

    store = _MemStore(
        StatuslineConfig(enabled=default_enabled_ids_from_spec(), multiline=multiline)
    )
    return AelixTUIContext(chrome, footer, statusline_store=store, **kwargs)


async def test_footer_single_line_default_is_one_row() -> None:
    # Default (multiline=False) keeps the single ``  ·  ``-joined row (no \n).
    footer = AelixFooterData(cwd="/tmp/proj")
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        _multiline_ctx(
            chrome,
            footer,
            multiline=False,
            model_provider=lambda: "openai/gpt-4o-mini",
            cwd="/tmp/proj",
        )
        assert chrome.footer_line_count() == 1
        assert "\n" not in chrome._footer_line
        assert "✱ openai/gpt-4o-mini" in chrome._footer_line


async def test_footer_multiline_groups_into_rows() -> None:
    # Multiline composes the mockup-A grouped rows: model on row 1, cwd on
    # row 2 — so model and cwd land on DIFFERENT lines (a \n between them).
    footer = AelixFooterData(cwd="/tmp/proj")
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        _multiline_ctx(
            chrome,
            footer,
            multiline=True,
            model_provider=lambda: "openai/gpt-4o-mini",
            cwd="/tmp/proj",
        )
        rows = chrome._footer_line.split("\n")
        assert chrome.footer_line_count() >= 2
        # model + cwd are in different rows.
        model_row = next(i for i, r in enumerate(rows) if "✱ openai/gpt-4o-mini" in r)
        cwd_row = next(i for i, r in enumerate(rows) if "📂" in r)
        assert model_row != cwd_row


async def test_footer_multiline_omits_empty_rows(tmp_path) -> None:
    # With NO model and NO cwd, the model/cwd rows vanish — empty rows are not
    # emitted (the permission/steering row is also empty here → footer is empty).
    # The footer points at a non-git tmp dir so the git-branch segment is empty.
    footer = AelixFooterData(cwd=str(tmp_path))
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        _multiline_ctx(
            chrome,
            footer,
            multiline=True,
            model_provider=lambda: None,
            cwd=None,
        )
        # No enabled segment produces a value → footer is empty (count floors 1).
        assert chrome._footer_line == ""
        assert chrome.footer_line_count() == 1


async def test_footer_multiline_toggle_applies_live() -> None:
    # Flipping the persisted flag + calling _refresh_footer applies live: the
    # same enabled set collapses from N rows to 1.
    from aelix_coding_agent.tui.footer_segments import default_enabled_ids_from_spec
    from aelix_coding_agent.tui.statusline_store import StatuslineConfig, StatuslineStore

    cfg = StatuslineConfig(
        enabled=default_enabled_ids_from_spec(), multiline=True
    )

    class _MemStore(StatuslineStore):
        def __init__(self) -> None:
            pass

        def load(self) -> StatuslineConfig:  # type: ignore[override]
            return cfg

    footer = AelixFooterData(cwd="/tmp/proj")
    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(
            chrome,
            footer,
            statusline_store=_MemStore(),
            model_provider=lambda: "m",
            cwd="/tmp/proj",
        )
        assert chrome.footer_line_count() >= 2  # multi-line at construction
        cfg.multiline = False
        ctx._refresh_footer()
        assert chrome.footer_line_count() == 1  # collapsed live


# === Issue #65 (ADR-0188) — tabbed() in-tab type-to-filter keybinding path ===


async def test_tabbed_q_yields_to_filter_on_filterable_tab() -> None:
    # On a FILTERABLE tab 'q' is a printable filter char, NOT a close key: after
    # typing, the future stays pending (q was absorbed). Only Esc / Ctrl-C close.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed("T", [("Discover", lambda: ["fmt", "lint"])], filter_tabs={0})
        )
        await _wait_float(chrome)
        pipe.send_text("ab")  # printable → into the live filter
        pipe.send_text("q")  # 'q' yields to the filter here (does NOT close)
        await asyncio.sleep(0.15)
        assert not fut.done()  # still open — 'q' was absorbed, not a close
        pipe.send_text("\x1b")  # Esc closes on a filterable tab
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_tabbed_q_closes_on_non_filterable_tab() -> None:
    # A tab NOT in filter_tabs keeps 'q' as its historical close binding: here
    # filter_tabs={5} means tab 0 is read-only, so 'q' resolves the future.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed("T", [("Discover", lambda: ["fmt", "lint"])], filter_tabs={5})
        )
        await _wait_float(chrome)
        pipe.send_text("q")  # tab 0 not filterable → 'q' closes
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_tabbed_printable_absorbed_on_filterable_tab() -> None:
    # A generic printable char on a filterable tab appends to the filter and must
    # not close the modal; Esc still closes cleanly afterward.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed("T", [("Discover", lambda: ["fmt", "lint"])], filter_tabs={0})
        )
        await _wait_float(chrome)
        pipe.send_text("z")  # printable → into the filter (absorbed, not a close)
        await asyncio.sleep(0.15)
        assert not fut.done()
        pipe.send_text("\x1b")  # Esc closes
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_tabbed_backspace_on_filterable_tab_then_escape_closes() -> None:
    # Backspace edits the live filter on a filterable tab; it must not crash and
    # must not close. "\x7f" is the DEL byte prompt_toolkit maps to Keys.ControlH,
    # which the "backspace" alias binds to — the same sequence a terminal sends.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed("T", [("Discover", lambda: ["fmt", "lint"])], filter_tabs={0})
        )
        await _wait_float(chrome)
        pipe.send_text("a")  # seed the filter
        pipe.send_text("\x7f")  # backspace → pops the filter (no crash, no close)
        await asyncio.sleep(0.15)
        assert chrome.is_modal_open()  # survived the backspace edit
        assert not fut.done()
        pipe.send_text("\x1b")  # Esc closes
        assert await asyncio.wait_for(fut, timeout=5) is None


# === GitHub #66 item 4 — the typed filter VALUE renders bright (label dim) ======


def test_filter_line_value_is_bright_cyan_label_stays_dim() -> None:
    from aelix_coding_agent.tui.context import (
        _PICK_DIM,
        _PICK_FILTER,
        _PICK_RST,
        _filter_line,
    )

    line = _filter_line("gpt-4")
    # The "Filter:" label is dim; the typed value is a bright-cyan run.
    assert line.startswith(f"{_PICK_DIM}Filter: {_PICK_RST}")
    assert f"{_PICK_FILTER}gpt-4{_PICK_RST}" in line
    # The bright value is NOT wrapped in the faint (dim) SGR.
    assert f"{_PICK_DIM}gpt-4" not in line


def test_filter_line_empty_placeholder_stays_fully_dim() -> None:
    from aelix_coding_agent.tui.context import _PICK_FILTER, _filter_line

    line = _filter_line("", "(type to filter)")
    # A placeholder is a hint, not typed input → no bright run at all.
    assert _PICK_FILTER not in line
    assert "(type to filter)" in line


def test_filter_counter_suffix_brightens_only_the_value() -> None:
    from aelix_coding_agent.tui.context import (
        _PICK_DIM,
        _PICK_FILTER,
        _PICK_RST,
        _filter_counter_suffix,
    )

    suffix = _filter_counter_suffix("qwen")
    # The "Filter:" label carries no explicit style (it inherits the counter's
    # dim wrap); the value RESETS that inherited dim first (else it renders
    # bold+cyan+faint) and then applies the bright-cyan SGR (#66 review fix).
    assert suffix == f"  ·  Filter: {_PICK_RST}{_PICK_FILTER}qwen{_PICK_RST}"
    assert _PICK_DIM not in suffix  # never self-dims — the caller's line does


async def test_tabbed_tab_switch_works_while_filter_active() -> None:
    # With an active filter on a filterable tab, Tab still advances the active tab
    # (the filter resets on switch): the second tab's render fires. Verify via the
    # render-closure side-effect list, then Esc closes.
    rendered: list[str] = []

    def _render(name: str) -> list[str]:
        rendered.append(name)
        return [f"body-{name}"]

    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed(
                "T",
                [("A", lambda: _render("A")), ("B", lambda: _render("B"))],
                filter_tabs={0, 1},
            )
        )
        await _wait_float(chrome)
        await asyncio.sleep(0.1)  # let the first paint fire
        assert "A" in rendered  # tab A rendered first
        pipe.send_text("fmt")  # type into tab 0's live filter
        await asyncio.sleep(0.1)
        rendered.clear()
        pipe.send_text("\t")  # Tab → advance to B despite the active filter
        await asyncio.sleep(0.15)
        assert "B" in rendered  # the second tab's render fired
        pipe.send_text("\x1b")  # Esc closes
        assert await asyncio.wait_for(fut, timeout=5) is None


# === GitHub #66 item 3 — fill_screen pickers fill the terminal-bounded modal ===


async def test_select_fill_screen_expands_modal_slot_to_cap() -> None:
    # With fill_screen a SHORT select fills the capped region: the modal slot's
    # preferred height equals the terminal-bounded cap (blank space below).
    from aelix_coding_agent.tui.overlay import _modal_cap

    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.select("Pick", ["red", "green"], fill_screen=True)
        )
        await _wait_float(chrome)
        rows = chrome.app.output.get_size().rows
        cap = _modal_cap(chrome, None)
        filled = chrome._render_modal_slot().preferred_height(80, rows).preferred
        assert filled == cap
        pipe.send_text("\r")  # Enter still resolves the highlighted row
        assert await asyncio.wait_for(fut, timeout=5) == "red"


async def test_select_without_fill_hugs_short_content_below_cap() -> None:
    # The default (fill_screen=False) keeps the natural height — a short list
    # reports fewer rows than the cap, so chat stays visible beneath it.
    from aelix_coding_agent.tui.overlay import _modal_cap

    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green"]))
        await _wait_float(chrome)
        rows = chrome.app.output.get_size().rows
        cap = _modal_cap(chrome, None)
        natural = chrome._render_modal_slot().preferred_height(80, rows).preferred
        assert 0 < natural < cap  # short modal, well under the cap
        pipe.send_text("\x1b")
        await asyncio.wait_for(fut, timeout=5)


async def test_tabbed_fill_screen_still_resolves() -> None:
    # Threading fill_screen through tabbed() must not change key handling.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.tabbed("T", [("A", lambda: ["a"])], fill_screen=True)
        )
        await _wait_float(chrome)
        pipe.send_text("\x1b")  # Esc closes
        assert await asyncio.wait_for(fut, timeout=5) is None


async def test_multiselect_fill_screen_still_resolves() -> None:
    # fill_screen threaded through multiselect() leaves toggle/confirm intact.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(
            ctx.multiselect(
                "Pick",
                [("a", "A", "desc-a"), ("b", "B", "desc-b")],
                selected=set(),
                fill_screen=True,
            )
        )
        await _wait_float(chrome)
        pipe.send_text(" ")  # Space toggles the highlighted row ("a")
        pipe.send_text("\n")  # Enter confirms the selection
        result = await asyncio.wait_for(fut, timeout=5)
        assert result is not None
        chosen, _toggles = result
        assert chosen == {"a"}
