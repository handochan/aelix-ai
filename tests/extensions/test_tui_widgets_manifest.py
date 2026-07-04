"""Issue #21 (ADR-0182) — ``contributes.tui_widgets`` manifest loader gate.

Trust gate (v1 declarative): ``[[contributes.tui_widgets]]`` requires
``capabilities.ui_tui_trusted = true`` — mirroring the Tier-4b
hooks/shell_exec gate, but firing in ``_resolve_factory`` BEFORE the entry
module is imported (data before code: a denied plugin executes NO code).
Real loader over a tmp plugin dir (the test_lazy_activation.py conventions:
marker files prove what executed).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.extensions import loader as loader_mod
from aelix_coding_agent.extensions.loader import (
    ExtensionManifestError,
    activate_pending_extension,
    discover_and_load_extensions,
)


def _manifest(
    module_name: str,
    *,
    capabilities: str,
    activation: str = "on_startup_finished = true",
) -> str:
    widgets = (
        f'tui_widgets = [{{ slot = "above_editor", factory = "{module_name}:make" }}]'
    )
    return textwrap.dedent(f"""
        [plugin]
        id = "widget-gate-plug"
        name = "Widget Gate Plugin"
        version = "0.1.0"
        description = "Declares TUI widgets"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/widget-gate-plug"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module_name}:setup"

        {capabilities}

        [activation]
        {activation}

        [contributes]
        {widgets}
    """).strip()


def _module_src(import_marker: Path, setup_marker: Path) -> str:
    return textwrap.dedent(f"""
        from pathlib import Path

        Path({str(import_marker)!r}).write_text("IMPORTED")

        def setup(aelix):
            Path({str(setup_marker)!r}).write_text("SETUP")

        def make(tui, theme):
            raise AssertionError("widget factory must not run at load time")
    """)


async def _load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_name: str,
    capabilities: str,
    activation: str = "on_startup_finished = true",
):
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "widget-gate-plug"
    pkg.mkdir(parents=True)
    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    pkg.joinpath("aelix-plugin.toml").write_text(
        _manifest(module_name, capabilities=capabilities, activation=activation),
        encoding="utf-8",
    )
    import_marker = tmp_path / "imported.marker"
    setup_marker = tmp_path / "setup.marker"
    (tmp_path / f"{module_name}.py").write_text(
        _module_src(import_marker, setup_marker), encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    loaded = await discover_and_load_extensions([], cwd=cwd, agent_dir=agent_dir)
    return loaded, import_marker, setup_marker


async def test_tui_widgets_without_ui_tui_trusted_fails_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, import_marker, setup_marker = await _load(
        tmp_path,
        monkeypatch,
        module_name="widget_gate_mod_denied",
        capabilities="",  # no [capabilities] → ui_tui_trusted defaults false
    )
    assert loaded.extensions == []
    assert len(loaded.errors) == 1
    assert "ui_tui_trusted" in loaded.errors[0].error
    # Data before code: the gate fires BEFORE the entry-module import, so a
    # denied plugin executes NOTHING — not even module top-level code
    # (review MEDIUM: the gate originally sat after the import).
    assert not import_marker.exists()
    assert not setup_marker.exists()


async def test_tui_widgets_with_ui_tui_trusted_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, import_marker, setup_marker = await _load(
        tmp_path,
        monkeypatch,
        module_name="widget_gate_mod_allowed",
        capabilities="[capabilities]\nui_tui_trusted = true",
    )
    assert loaded.errors == []
    assert import_marker.exists() and setup_marker.exists()
    (ext,) = loaded.extensions
    assert ext.manifest is not None
    (contrib,) = ext.manifest.contributes.tui_widgets
    assert contrib.slot == "above_editor"


async def test_gate_also_guards_lazy_activation_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression fence: contributes.tui_widgets forces eager today, but if
    eligibility ever regresses (simulated here), the _resolve_factory gate
    must still deny the plugin AT ACTIVATION — before its module imports."""
    monkeypatch.setattr(loader_mod, "_is_lazy_eligible", lambda _m: True)
    loaded, import_marker, setup_marker = await _load(
        tmp_path,
        monkeypatch,
        module_name="widget_gate_mod_lazy",
        capabilities="",  # gate-denied
        activation='on_command = ["gate-cmd"]',
    )
    # Eligibility was forced True → the plugin DEFERRED (no load error).
    assert loaded.errors == []
    assert "widget-gate-plug" in loaded.runtime.pending_activations
    assert not import_marker.exists()
    with pytest.raises(ExtensionManifestError, match="ui_tui_trusted"):
        await activate_pending_extension(loaded.runtime, "widget-gate-plug")
    # The gate fired before the import on the lazy path too.
    assert not import_marker.exists()
    assert not setup_marker.exists()
