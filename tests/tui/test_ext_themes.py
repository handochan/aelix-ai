"""Issue #21 (ADR-0184) — manifest ``contributes.themes`` adapter tests.

Drives :func:`apply_manifest_themes` + the ``tui.themes`` registry additions
against real ``Extension`` objects carrying real parsed manifests and real
theme files on disk. The registry is a process-global (pi ``registeredThemes``
parity), so each test clears it via the autouse fixture.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_agent_core.contracts import PluginManifest, parse_manifest_toml
from aelix_coding_agent.extensions.api import Extension
from aelix_coding_agent.tui import themes as theme_registry
from aelix_coding_agent.tui.ext_themes import apply_manifest_themes


@pytest.fixture(autouse=True)
def _clear_registry() -> object:
    theme_registry.register_themes([])
    yield
    theme_registry.register_themes([])


def _theme_file(pkg: Path, rel: str, *, name: str, roles: str = "") -> None:
    body = f'name = "{name}"\n'
    if roles:
        body += "[roles]\n" + roles
    dest = pkg / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")


def _manifest(themes_toml: str) -> PluginManifest:
    return parse_manifest_toml(
        textwrap.dedent(f"""
            [plugin]
            id = "theme-plug"
            name = "Theme Plugin"
            version = "0.1.0"
            description = "Ships a theme"
            authors = ["Test <test@example.com>"]
            repository = "https://github.com/example/theme-plug"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [plugin.entry]
            python = "theme_plug_mod:setup"

            [activation]
            on_startup_finished = true

            [contributes]
            {themes_toml}
        """).strip()
    )


def _ext(name: str, pkg_dir: Path | None, themes_toml: str) -> Extension:
    ext = Extension(name=name, manifest=_manifest(themes_toml))
    ext.resolved_path = str(pkg_dir) if pkg_dir is not None else None
    return ext


def test_build_theme_from_data_valid_and_invalid_colors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        theme = theme_registry.build_theme_from_data(
            "t", {"assistant": "cyan", "error": "notacolor", "bogus": "red"}
        )
    assert theme.name == "t"
    # Valid color styles; invalid + unknown roles fall through to identity.
    assert theme.fg("assistant", "hi") != "hi"  # styled
    assert theme.fg("error", "hi") == "hi"  # invalid color dropped → identity
    assert "invalid color" in caplog.text
    assert "unknown role" in caplog.text


def test_apply_registers_theme_and_picker_sees_it(tmp_path: Path) -> None:
    _theme_file(tmp_path, "themes/solar.toml", name="solarized", roles='accent = "green"\n')
    ext = _ext("plug", tmp_path, 'themes = [{ path = "themes/solar.toml" }]')
    apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("solarized") is not None
    assert "solarized" in theme_registry.all_theme_names()
    # Built-ins still present and listed first.
    assert theme_registry.all_theme_names()[:3] == ["default", "dark", "light"]


def test_reconcile_drops_removed_plugin_theme(tmp_path: Path) -> None:
    _theme_file(tmp_path, "t.toml", name="ephemeral")
    ext = _ext("plug", tmp_path, 'themes = [{ path = "t.toml" }]')
    apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("ephemeral") is not None
    # A rebind onto an extension set WITHOUT the plugin un-registers it.
    apply_manifest_themes(SimpleNamespace(extensions=[]))
    assert theme_registry.get_theme("ephemeral") is None


def test_builtin_name_collision_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _theme_file(tmp_path, "d.toml", name="dark", roles='error = "green"\n')
    ext = _ext("plug", tmp_path, 'themes = [{ path = "d.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    # The built-in 'dark' is untouched (not overwritten by the plugin's).
    assert theme_registry.get_theme("dark") is theme_registry.dark
    assert "shadows a built-in" in caplog.text


def test_path_traversal_escape_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    outside = tmp_path / "outside.toml"
    outside.write_text('name = "evil"\n', encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ext = _ext("plug", pkg, 'themes = [{ path = "../outside.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("evil") is None
    assert "escapes the plugin directory" in caplog.text


def test_missing_file_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ext = _ext("plug", tmp_path, 'themes = [{ path = "nope.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert "not found" in caplog.text


def test_malformed_toml_and_missing_name_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "bad.toml").write_text("this is = = not toml", encoding="utf-8")
    (tmp_path / "noname.toml").write_text('[roles]\nerror = "red"\n', encoding="utf-8")
    ext = _ext(
        "plug",
        tmp_path,
        'themes = [{ path = "bad.toml" }, { path = "noname.toml" }]',
    )
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert "malformed TOML" in caplog.text
    assert "no top-level string 'name'" in caplog.text
    assert theme_registry.all_theme_names() == ["default", "dark", "light"]


def test_no_resolved_path_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ext = _ext("plug", None, 'themes = [{ path = "t.toml" }]')  # resolved_path None
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert "no plugin directory recorded" in caplog.text


def test_pending_shell_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _theme_file(tmp_path, "t.toml", name="lazy")
    ext = _ext("plug", tmp_path, 'themes = [{ path = "t.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]), pending=("plug",))
    assert theme_registry.get_theme("lazy") is None
    assert "pending lazy activation" in caplog.text


def test_runner_none_clears_registry(tmp_path: Path) -> None:
    _theme_file(tmp_path, "t.toml", name="keep")
    ext = _ext("plug", tmp_path, 'themes = [{ path = "t.toml" }]')
    apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("keep") is not None
    apply_manifest_themes(None)
    assert theme_registry.get_theme("keep") is None


def test_first_wins_same_name_across_extensions(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    _theme_file(a, "t.toml", name="dup", roles='accent = "green"\n')
    _theme_file(b, "t.toml", name="dup", roles='accent = "red"\n')
    e1 = _ext("first", a, 'themes = [{ path = "t.toml" }]')
    e2 = _ext("second", b, 'themes = [{ path = "t.toml" }]')
    apply_manifest_themes(SimpleNamespace(extensions=[e1, e2]))
    # First registration wins; only one 'dup' in the registry.
    assert theme_registry.all_theme_names().count("dup") == 1
    # ...and it is the FIRST ext's theme (green accent), not the second's (red).
    # A last-wins regression would also yield count==1, so assert the identity.
    won = theme_registry.get_theme("dup")
    assert won is not None
    green = theme_registry.build_theme_from_data("dup", {"accent": "green"})
    red = theme_registry.build_theme_from_data("dup", {"accent": "red"})
    assert won.fg("accent", "x") == green.fg("accent", "x")
    assert won.fg("accent", "x") != red.fg("accent", "x")


def test_oversize_theme_file_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ADR-0184 review MED: the size cap must reject a file OVER the limit — and
    # (per the fix) via stat() BEFORE reading it into memory, so a multi-GB file
    # can't OOM startup. We only assert the observable outcome (skipped + warned).
    from aelix_coding_agent.tui.ext_themes import _MAX_THEME_BYTES

    big = tmp_path / "big.toml"
    big.write_bytes(b'name = "huge"\n# ' + b"x" * (_MAX_THEME_BYTES + 16))
    ext = _ext("plug", tmp_path, 'themes = [{ path = "big.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("huge") is None
    assert "exceeds" in caplog.text


def test_absolute_path_escape_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ADR-0184 review HIGH (test gap): the traversal fence was only proven for
    # relative '../'. An ABSOLUTE path outside the plugin dir must be rejected.
    outside = tmp_path / "outside.toml"
    outside.write_text('name = "evil"\n', encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    ext = _ext("plug", pkg, f'themes = [{{ path = "{outside}" }}]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("evil") is None
    assert "escapes the plugin directory" in caplog.text


def test_symlink_escape_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ADR-0184 review HIGH (test gap): a symlink INSIDE the plugin dir pointing
    # to an otherwise-valid theme file OUTSIDE it must not register — resolve()
    # canonicalizes the link target so the fence sees the real (outside) path.
    real = tmp_path / "real.toml"
    real.write_text('name = "linked"\n', encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "link.toml").symlink_to(real)
    ext = _ext("plug", pkg, 'themes = [{ path = "link.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("linked") is None
    assert "escapes the plugin directory" in caplog.text


def test_control_char_theme_name_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ADR-0184 review LOW: the theme name reaches the /settings picker
    # unvalidated. An SGR/escape-laden name must be rejected (not just colors).
    (tmp_path / "t.toml").write_text(
        'name = "\\u001b[32mevil\\u001b[0m"\n', encoding="utf-8"
    )
    ext = _ext("plug", tmp_path, 'themes = [{ path = "t.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert all("evil" not in n for n in theme_registry.all_theme_names())
    assert "control chars" in caplog.text


def test_good_theme_survives_bad_sibling(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # ADR-0184 review MED (test gap): a bad contrib must not abort a GOOD
    # sibling in the same manifest (never-raises-per-contrib, proven positively).
    (tmp_path / "bad.toml").write_text("this is = = not toml", encoding="utf-8")
    _theme_file(tmp_path, "good.toml", name="survivor", roles='accent = "green"\n')
    ext = _ext(
        "plug", tmp_path, 'themes = [{ path = "bad.toml" }, { path = "good.toml" }]'
    )
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("survivor") is not None
    assert "malformed TOML" in caplog.text


def test_build_theme_from_data_non_string_color_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ADR-0184 review LOW (test gap): a non-string color (TOML int) is dropped
    # to the identity branch, not crashed on.
    with caplog.at_level(logging.WARNING):
        theme = theme_registry.build_theme_from_data("t", {"accent": 123})  # type: ignore[dict-item]
    assert theme.fg("accent", "hi") == "hi"
    assert "not a string" in caplog.text


def test_roles_not_a_table_uses_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A ``roles`` that isn't a table degrades to an empty (identity) theme with a
    # warning, rather than skipping or crashing.
    (tmp_path / "t.toml").write_text('name = "flat"\nroles = "nope"\n', encoding="utf-8")
    ext = _ext("plug", tmp_path, 'themes = [{ path = "t.toml" }]')
    with caplog.at_level(logging.WARNING):
        apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("flat") is not None
    assert "not a table" in caplog.text


def test_runner_extensions_property_raises_clears(tmp_path: Path) -> None:
    # A runner whose ``.extensions`` raises must be swallowed (clear the set),
    # not propagate out of the reconcile.
    class _Raises:
        @property
        def extensions(self) -> list[object]:
            raise RuntimeError("boom")

    _theme_file(tmp_path, "t.toml", name="keep")
    ext = _ext("plug", tmp_path, 'themes = [{ path = "t.toml" }]')
    apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    assert theme_registry.get_theme("keep") is not None
    apply_manifest_themes(_Raises())  # must not raise
    assert theme_registry.get_theme("keep") is None


def test_list_theme_infos_registered_has_resolved_path(tmp_path: Path) -> None:
    # ADR-0184 review NIT: ThemeInfo.path for a registered theme is the
    # RESOLVED, fenced target (not the raw '..'-joinable string).
    _theme_file(tmp_path, "themes/s.toml", name="withpath", roles='accent = "green"\n')
    ext = _ext("plug", tmp_path, 'themes = [{ path = "themes/s.toml" }]')
    apply_manifest_themes(SimpleNamespace(extensions=[ext]))
    infos = {i.name: i for i in theme_registry.list_theme_infos()}
    assert infos["withpath"].path == str((tmp_path / "themes" / "s.toml").resolve())
    assert infos["default"].path is None  # built-ins carry no path
