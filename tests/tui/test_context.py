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


async def test_input_returns_text() -> None:
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.input("Name?"))
        await asyncio.sleep(0.05)
        pipe.send_text("alice\n")
        assert await asyncio.wait_for(fut, timeout=5) == "alice"


async def test_select_by_number() -> None:
    async with _ctx(run_app=True) as (ctx, _chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green", "blue"]))
        await asyncio.sleep(0.05)
        pipe.send_text("2")
        assert await asyncio.wait_for(fut, timeout=5) == "green"


# === review-fix coverage (ADR-0105 W4) =================================


async def _wait_float(chrome: AelixChrome, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if chrome._floats:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("modal float not shown")


async def test_select_caps_at_nine_options() -> None:
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        options = [f"o{i}" for i in range(12)]
        fut = asyncio.ensure_future(ctx.select("Pick", options))
        await _wait_float(chrome)
        pipe.send_text("9")  # key 9 → index 8 → "o8" (only first 9 bindable)
        assert await asyncio.wait_for(fut, timeout=5) == "o8"


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
    async with _footer_chrome(
        footer, model_provider=lambda: "Qwen/Qwen3.6-35B", cwd="/tmp/proj", mode="default"
    ) as (_ctx_obj, chrome):
        line = chrome._footer_line
        assert "⏵⏵ default" in line
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


async def test_select_enter_is_noop_then_number_resolves() -> None:
    # ADR-0121 M1: Enter on a numbered select must NOT resolve (no default, no
    # leak to the chrome global accept) — a number or Esc is required.
    async with _ctx(run_app=True) as (ctx, chrome, pipe):
        fut = asyncio.ensure_future(ctx.select("Pick", ["red", "green"]))
        await _wait_float(chrome)
        pipe.send_text("\n")  # Enter → no-op
        await asyncio.sleep(0.1)
        assert not fut.done()
        pipe.send_text("1")  # a real choice still resolves
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
