"""Issue #21 (ADR-0184) — themes/descriptors loader wiring.

Real loader over a tmp plugin dir (the test_lazy_activation.py conventions):
(1) ``contributes.themes`` forces EAGER load and the loaded Extension carries
its ``resolved_path`` (the plugin dir) so the theme adapter can resolve files;
(2) ``contributes.descriptors`` is reserved/inert and emits a load-time warning.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.extensions.loader import discover_and_load_extensions


def _manifest(module_name: str, *, activation: str, contributes: str) -> str:
    return textwrap.dedent(f"""
        [plugin]
        id = "wire-plug"
        name = "Wire Plugin"
        version = "0.1.0"
        description = "themes/descriptors wiring"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/wire-plug"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module_name}:setup"

        [activation]
        {activation}

        [contributes]
        {contributes}
    """).strip()


_MODULE_SRC = textwrap.dedent("""
    def setup(aelix):
        pass
""")


async def _load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_name: str,
    activation: str,
    contributes: str,
):
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "wire-plug"
    pkg.mkdir(parents=True)
    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    pkg.joinpath("aelix-plugin.toml").write_text(
        _manifest(module_name, activation=activation, contributes=contributes),
        encoding="utf-8",
    )
    (tmp_path / f"{module_name}.py").write_text(_MODULE_SRC, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    loaded = await discover_and_load_extensions([], cwd=cwd, agent_dir=agent_dir)
    return loaded, pkg


async def test_themes_force_eager_and_record_pkg_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded, pkg = await _load(
        tmp_path,
        monkeypatch,
        module_name="wire_mod_themes",
        activation='on_command = ["wire-cmd"]',  # pure on_command...
        contributes='themes = [{ path = "themes/x.toml" }]',  # ...but themes forces eager
    )
    assert loaded.errors == []
    assert loaded.runtime.pending_activations == {}  # NOT deferred
    (ext,) = loaded.extensions
    # The plugin dir is recorded so the theme adapter can resolve paths.
    assert ext.resolved_path is not None
    assert Path(ext.resolved_path).resolve() == pkg.resolve()


async def test_descriptors_declaration_warns_reserved_inert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        loaded, _pkg = await _load(
            tmp_path,
            monkeypatch,
            module_name="wire_mod_desc",
            activation="on_startup_finished = true",
            contributes='descriptors = [{ kind = "status-item", id = "s1" }]',
        )
    assert loaded.errors == []
    assert "reserved and inert" in caplog.text
    assert "ui:list-modules" in caplog.text


async def test_descriptors_warn_even_when_plugin_is_lazy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ADR-0184 review LOW: a pure-``on_command`` plugin is lazy-DEFERRED, but the
    # descriptors inert-warning must STILL fire (it is emitted before the lazy
    # ``continue``) so the author sees the runtime-emit guidance.
    with caplog.at_level(logging.WARNING):
        loaded, _pkg = await _load(
            tmp_path,
            monkeypatch,
            module_name="wire_mod_desc_lazy",
            activation='on_command = ["wire-cmd"]',  # pure on_command → lazy
            contributes='descriptors = [{ kind = "status-item", id = "s1" }]',
        )
    assert loaded.errors == []
    # The plugin WAS deferred (lazy)...
    assert loaded.runtime.pending_activations != {}
    # ...yet the reserved/inert warning still fired.
    assert "reserved and inert" in caplog.text
