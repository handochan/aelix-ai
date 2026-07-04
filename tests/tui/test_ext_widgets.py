"""Issue #21 (ADR-0182) — manifest ``contributes.tui_widgets`` adapter tests.

Drives :func:`apply_manifest_widgets` against a REAL ``AelixTUIContext`` +
``AelixChrome`` (headless pipe input + DummyOutput, the test_context.py
conventions), real ``Extension`` objects carrying real parsed
``PluginManifest``s, and real widget-factory modules imported off
``sys.path``. Module names are unique per test — ``sys.modules`` caches by
name, so a shared name would leak the FIRST test's module body everywhere.
"""

from __future__ import annotations

import io
import logging
import textwrap
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_agent_core.contracts import PluginManifest, parse_manifest_toml
from aelix_coding_agent.extensions.api import Extension
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.ext_widgets import apply_manifest_widgets
from aelix_coding_agent.tui.footer_data import AelixFooterData
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


@asynccontextmanager
async def _ctx() -> AsyncGenerator[tuple[AelixTUIContext, AelixChrome]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        _ = pipe
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        yield ctx, chrome


def _manifest(widgets_toml: str) -> PluginManifest:
    return parse_manifest_toml(
        textwrap.dedent(f"""
            [plugin]
            id = "widget-plug"
            name = "Widget Plugin"
            version = "0.1.0"
            description = "Declares TUI widgets"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/widget-plug"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [plugin.entry]
            python = "widget_plug_mod:setup"

            [capabilities]
            ui_tui_trusted = true

            [activation]
            on_startup_finished = true

            [contributes]
            {widgets_toml}
        """).strip()
    )


def _ext(name: str, widgets_toml: str) -> Extension:
    return Extension(name=name, manifest=_manifest(widgets_toml))


_WIDGET_MODULE = textwrap.dedent("""
    class _Widget:
        def __init__(self, lines):
            self._lines = list(lines)

        def render(self, width):
            return list(self._lines)

        def handle_input(self, data):
            pass

        def invalidate(self):
            pass


    def make_above(tui, theme):
        return _Widget(["above-line"])


    def make_below(tui, theme):
        return _Widget(["below-line"])


    def boom(tui, theme):
        raise RuntimeError("boom")
""")


def _write_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, *, src: str = _WIDGET_MODULE
) -> None:
    (tmp_path / f"{name}.py").write_text(src, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))


async def test_widgets_painted_above_and_below(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(tmp_path, monkeypatch, "widget_mod_paint")
    ext = _ext(
        "plug",
        'tui_widgets = ['
        '{ slot = "above_editor", factory = "widget_mod_paint:make_above" }, '
        '{ slot = "below_editor", factory = "widget_mod_paint:make_below" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, chrome):
        apply_manifest_widgets(SimpleNamespace(extensions=[ext]), ctx, applied)
        assert "above-line" in str(chrome._render_widgets_above())
        assert "below-line" in str(chrome._render_widgets_below())
    assert applied == {
        "ext:plug:tui_widgets[0]": "above_editor",
        "ext:plug:tui_widgets[1]": "below_editor",
    }


async def test_unknown_slot_skipped_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_module(tmp_path, monkeypatch, "widget_mod_slot")
    ext = _ext(
        "plug",
        'tui_widgets = [{ slot = "sidebar", factory = "widget_mod_slot:make_above" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, chrome):
        with caplog.at_level(logging.WARNING):
            apply_manifest_widgets(SimpleNamespace(extensions=[ext]), ctx, applied)
        assert "above-line" not in str(chrome._render_widgets_above())
    assert applied == {}
    assert "unknown slot" in caplog.text


async def test_unresolvable_factory_skipped_others_survive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_module(tmp_path, monkeypatch, "widget_mod_resolve")
    ext = _ext(
        "plug",
        'tui_widgets = ['
        '{ slot = "above_editor", factory = "no_such_module_xyz:make" }, '
        '{ slot = "above_editor", factory = "widget_mod_resolve:make_above" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, chrome):
        with caplog.at_level(logging.WARNING):
            apply_manifest_widgets(SimpleNamespace(extensions=[ext]), ctx, applied)
        assert "above-line" in str(chrome._render_widgets_above())
    assert applied == {"ext:plug:tui_widgets[1]": "above_editor"}
    assert "failed to resolve" in caplog.text


async def test_factory_raising_at_render_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_module(tmp_path, monkeypatch, "widget_mod_boom")
    ext = _ext(
        "plug",
        'tui_widgets = [{ slot = "above_editor", factory = "widget_mod_boom:boom" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, _chrome):
        with caplog.at_level(logging.WARNING):
            apply_manifest_widgets(SimpleNamespace(extensions=[ext]), ctx, applied)
    assert applied == {}
    assert "raised while rendering" in caplog.text


async def test_stale_widgets_unpainted_on_reapply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A /reload that drops a plugin must un-paint its widgets (reconcile)."""
    _write_module(tmp_path, monkeypatch, "widget_mod_stale")
    ext = _ext(
        "plug",
        'tui_widgets = [{ slot = "above_editor", factory = "widget_mod_stale:make_above" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, chrome):
        apply_manifest_widgets(SimpleNamespace(extensions=[ext]), ctx, applied)
        assert "above-line" in str(chrome._render_widgets_above())
        apply_manifest_widgets(SimpleNamespace(extensions=[]), ctx, applied)
        assert "above-line" not in str(chrome._render_widgets_above())
    assert applied == {}


async def test_extension_without_manifest_ignored() -> None:
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, _chrome):
        apply_manifest_widgets(
            SimpleNamespace(extensions=[Extension(name="imperative")]), ctx, applied
        )
    assert applied == {}


async def test_pending_shell_skipped_without_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The defensive fence: a pending lazy shell's widget factory must NOT be
    resolved (resolution is an import — the plugin has not activated)."""
    marker = tmp_path / "imported.marker"
    src = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('IMPORTED')\n" + _WIDGET_MODULE
    )
    _write_module(tmp_path, monkeypatch, "widget_mod_pending", src=src)
    ext = _ext(
        "plug",
        'tui_widgets = [{ slot = "above_editor", factory = "widget_mod_pending:make_above" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, _chrome):
        with caplog.at_level(logging.WARNING):
            apply_manifest_widgets(
                SimpleNamespace(extensions=[ext]), ctx, applied, pending=("plug",)
            )
    assert not marker.exists()
    assert applied == {}
    assert "pending lazy activation" in caplog.text


async def test_runner_none_unpaints_stale() -> None:
    """Duck-typed runner=None (e.g. a fake harness without a runner) still
    reconciles: previously painted keys are removed."""
    applied = {"ext:gone:tui_widgets[0]": "above_editor"}
    async with _ctx() as (ctx, chrome):
        ctx.set_widget("ext:gone:tui_widgets[0]", ["stale-line"])
        assert "stale-line" in str(chrome._render_widgets_above())
        apply_manifest_widgets(None, ctx, applied)
        assert "stale-line" not in str(chrome._render_widgets_above())
    assert applied == {}


async def test_slot_change_unpaints_old_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review MEDIUM (live-reproduced): the chrome keeps one dict PER
    placement, so a slot that moves between re-applies (plugin edit +
    /reload) must pop the OLD placement's entry — and removal after the move
    must leave nothing behind."""
    _write_module(tmp_path, monkeypatch, "widget_mod_move")
    above = _ext(
        "plug",
        'tui_widgets = [{ slot = "above_editor", factory = "widget_mod_move:make_above" }]',
    )
    below = _ext(
        "plug",
        'tui_widgets = [{ slot = "below_editor", factory = "widget_mod_move:make_above" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, chrome):
        apply_manifest_widgets(SimpleNamespace(extensions=[above]), ctx, applied)
        assert "above-line" in str(chrome._render_widgets_above())
        apply_manifest_widgets(SimpleNamespace(extensions=[below]), ctx, applied)
        # Moved: painted below, and the old above entry is GONE.
        assert "above-line" in str(chrome._render_widgets_below())
        assert "above-line" not in str(chrome._render_widgets_above())
        assert applied == {"ext:plug:tui_widgets[0]": "below_editor"}
        # Removal after the move leaves nothing behind (no permanent leak).
        apply_manifest_widgets(SimpleNamespace(extensions=[]), ctx, applied)
        assert "above-line" not in str(chrome._render_widgets_below())
    assert applied == {}


async def test_same_name_extensions_first_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two loaded extensions sharing a name collide on the widget key —
    FIRST wins (the get_shortcuts convention), the loser is skipped with a
    warning; no dual-paint across placements."""
    _write_module(tmp_path, monkeypatch, "widget_mod_dup")
    first = _ext(
        "plug",
        'tui_widgets = [{ slot = "above_editor", factory = "widget_mod_dup:make_above" }]',
    )
    second = _ext(
        "plug",
        'tui_widgets = [{ slot = "below_editor", factory = "widget_mod_dup:make_below" }]',
    )
    applied: dict[str, str] = {}
    async with _ctx() as (ctx, chrome):
        with caplog.at_level(logging.WARNING):
            apply_manifest_widgets(
                SimpleNamespace(extensions=[first, second]), ctx, applied
            )
        assert "above-line" in str(chrome._render_widgets_above())
        assert "below-line" not in str(chrome._render_widgets_below())
    assert applied == {"ext:plug:tui_widgets[0]": "above_editor"}
    assert "already applied by an earlier extension" in caplog.text


async def test_raising_extensions_property_still_reconciles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A runner whose ``extensions`` property raises must not escape (the
    module-level never-raises contract) — and stale widgets still un-paint."""

    class _BadRunner:
        @property
        def extensions(self) -> list[object]:
            raise RuntimeError("runner exploded")

    applied = {"ext:gone:tui_widgets[0]": "above_editor"}
    async with _ctx() as (ctx, chrome):
        ctx.set_widget("ext:gone:tui_widgets[0]", ["stale-line"])
        with caplog.at_level(logging.WARNING):
            apply_manifest_widgets(_BadRunner(), ctx, applied)
        assert "stale-line" not in str(chrome._render_widgets_above())
    assert applied == {}
    assert "extension runner unreadable" in caplog.text
