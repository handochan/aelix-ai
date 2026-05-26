"""Sprint 6h₁₀b §A — concrete Theme/EditorTheme instances + registry.

Three built-in themes (``default``, ``dark``, ``light``) backed by
:class:`rich.style.Style`.  Each ``fg(role, text)`` callable maps a fixed set
of semantic role names to per-theme ANSI colors; an unknown role returns *text*
unchanged (never raises).  ``bg(color, text)`` applies a background color by
name.  ``bold`` / ``italic`` wrap text with the matching Rich attribute.

Registry helpers mirror the Pi ``ExtensionUIContext`` theme API surface:
``THEMES``, ``DEFAULT_THEME``, ``get_theme(name)``, ``list_theme_infos()``.
"""

from __future__ import annotations

from rich.style import Style

from aelix_coding_agent.extensions.ext_ui import ThemeInfo
from aelix_coding_agent.extensions.widget_protocols import EditorTheme, Theme

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

# Role → color maps per theme.  Only the keys listed here receive styling;
# unknown roles fall through to the identity branch in _make_fg().
_DEFAULT_ROLES: dict[str, str] = {
    "assistant": "cyan",
    "tool": "yellow",
    "error": "red",
    "dim": "bright_black",
    "accent": "blue",
    "thinking": "magenta",
}

_DARK_ROLES: dict[str, str] = {
    "assistant": "bright_cyan",
    "tool": "bright_yellow",
    "error": "bright_red",
    "dim": "bright_black",
    "accent": "bright_blue",
    "thinking": "bright_magenta",
}

_LIGHT_ROLES: dict[str, str] = {
    "assistant": "dark_cyan",
    "tool": "dark_orange",
    "error": "dark_red",
    "dim": "grey50",
    "accent": "navy_blue",
    "thinking": "purple",
}


def _make_fg(role_map: dict[str, str]):  # noqa: ANN001
    """Return a ``(role, text) -> styled_str`` callable backed by *role_map*."""

    def fg(role: str, text: str) -> str:
        color = role_map.get(role)
        if color is None:
            return text
        return Style(color=color).render(text)

    return fg


def _make_bg():  # noqa: ANN001
    """Return a ``(color, text) -> styled_str`` callable (theme-independent)."""

    def bg(color: str, text: str) -> str:
        return Style(bgcolor=color).render(text)

    return bg


def _make_bold():  # noqa: ANN001
    def bold(text: str) -> str:
        return Style(bold=True).render(text)

    return bold


def _make_italic():  # noqa: ANN001
    def italic(text: str) -> str:
        return Style(italic=True).render(text)

    return italic


def _make_editor_theme(border_color: str, border_focused_color: str) -> EditorTheme:
    def border(text: str) -> str:
        return Style(color=border_color).render(text)

    def border_focused(text: str) -> str:
        return Style(color=border_focused_color, bold=True).render(text)

    def autocomplete_selected(text: str) -> str:
        return Style(color="black", bgcolor="cyan").render(text)

    return EditorTheme(
        border=border,
        border_focused=border_focused,
        autocomplete_selected=autocomplete_selected,
    )


# ---------------------------------------------------------------------------
# Concrete Theme instances
# ---------------------------------------------------------------------------

default: Theme = Theme(
    name="default",
    fg=_make_fg(_DEFAULT_ROLES),
    bg=_make_bg(),
    bold=_make_bold(),
    italic=_make_italic(),
)

dark: Theme = Theme(
    name="dark",
    fg=_make_fg(_DARK_ROLES),
    bg=_make_bg(),
    bold=_make_bold(),
    italic=_make_italic(),
)

light: Theme = Theme(
    name="light",
    fg=_make_fg(_LIGHT_ROLES),
    bg=_make_bg(),
    bold=_make_bold(),
    italic=_make_italic(),
)

# Matching EditorTheme per theme
default_editor: EditorTheme = _make_editor_theme("bright_black", "cyan")
dark_editor: EditorTheme = _make_editor_theme("bright_black", "bright_cyan")
light_editor: EditorTheme = _make_editor_theme("grey50", "dark_cyan")

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

THEMES: dict[str, Theme] = {
    "default": default,
    "dark": dark,
    "light": light,
}

DEFAULT_THEME: Theme = default


def get_theme(name: str) -> Theme | None:
    """Return the :class:`~aelix_coding_agent.extensions.widget_protocols.Theme`
    registered under *name*, or ``None`` if not found.
    """
    return THEMES.get(name)


def list_theme_infos() -> list[ThemeInfo]:
    """Return a :class:`~aelix_coding_agent.extensions.ext_ui.ThemeInfo` entry
    for each registered theme (``path=None`` for built-ins).
    """
    return [ThemeInfo(name=name) for name in THEMES]


__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "dark",
    "dark_editor",
    "default",
    "default_editor",
    "get_theme",
    "light",
    "light_editor",
    "list_theme_infos",
]
