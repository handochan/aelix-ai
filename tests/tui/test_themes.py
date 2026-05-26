"""Tests for aelix_coding_agent.tui.themes (Sprint 6h₁₀b §A)."""

from __future__ import annotations

import pytest
from aelix_coding_agent.extensions.ext_ui import ThemeInfo
from aelix_coding_agent.tui.themes import (
    DEFAULT_THEME,
    THEMES,
    dark,
    default,
    get_theme,
    light,
    list_theme_infos,
)

_ANSI = "\x1b["


class TestFgKnownRoles:
    """fg(role, text) for known roles returns ANSI-styled string."""

    @pytest.mark.parametrize("theme", [default, dark, light])
    @pytest.mark.parametrize("role", ["assistant", "tool", "error", "dim", "accent", "thinking"])
    def test_known_role_contains_ansi(self, theme, role):
        result = theme.fg(role, "hi")
        assert _ANSI in result, f"{theme.name}.fg({role!r}, 'hi') missing ANSI escape"

    @pytest.mark.parametrize("theme", [default, dark, light])
    def test_assistant_role(self, theme):
        result = theme.fg("assistant", "hi")
        assert _ANSI in result


class TestFgUnknownRole:
    """fg(unknown_role, text) returns text unchanged (no raise, no ANSI)."""

    @pytest.mark.parametrize("theme", [default, dark, light])
    def test_unknown_role_passthrough(self, theme):
        assert theme.fg("unknown_role_xyz", "hi") == "hi"

    @pytest.mark.parametrize("theme", [default, dark, light])
    def test_empty_role_passthrough(self, theme):
        assert theme.fg("", "hello") == "hello"


class TestBold:
    @pytest.mark.parametrize("theme", [default, dark, light])
    def test_bold_wraps_with_ansi(self, theme):
        result = theme.bold("text")
        assert _ANSI in result

    @pytest.mark.parametrize("theme", [default, dark, light])
    def test_bold_contains_input(self, theme):
        result = theme.bold("mytext")
        assert "mytext" in result


class TestItalic:
    @pytest.mark.parametrize("theme", [default, dark, light])
    def test_italic_wraps_with_ansi(self, theme):
        result = theme.italic("text")
        assert _ANSI in result

    @pytest.mark.parametrize("theme", [default, dark, light])
    def test_italic_contains_input(self, theme):
        result = theme.italic("mytext")
        assert "mytext" in result


class TestRegistry:
    def test_get_theme_dark(self):
        t = get_theme("dark")
        assert t is dark

    def test_get_theme_default(self):
        t = get_theme("default")
        assert t is default

    def test_get_theme_light(self):
        t = get_theme("light")
        assert t is light

    def test_get_theme_missing_returns_none(self):
        assert get_theme("nope") is None

    def test_get_theme_empty_returns_none(self):
        assert get_theme("") is None

    def test_themes_dict_has_three_entries(self):
        assert set(THEMES) == {"default", "dark", "light"}

    def test_default_theme_is_default_instance(self):
        assert DEFAULT_THEME is default


class TestListThemeInfos:
    def test_returns_three_items(self):
        infos = list_theme_infos()
        assert len(infos) == 3

    def test_all_are_theme_info(self):
        for info in list_theme_infos():
            assert isinstance(info, ThemeInfo)

    def test_names_match(self):
        names = {info.name for info in list_theme_infos()}
        assert names == {"default", "dark", "light"}

    def test_path_is_none_for_builtins(self):
        for info in list_theme_infos():
            assert info.path is None
