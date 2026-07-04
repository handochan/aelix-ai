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

import logging

from rich.style import Style

from aelix_coding_agent.extensions.ext_ui import ThemeInfo
from aelix_coding_agent.extensions.widget_protocols import EditorTheme, Theme

logger = logging.getLogger(__name__)

# The semantic roles a theme file may color (issue #21 themes, ADR-0184). Only
# these keys are honored by ``_make_fg``; unknown keys are inert. Kept as the
# single source so the file loader and the built-ins agree.
THEME_ROLES: tuple[str, ...] = (
    "assistant",
    "tool",
    "error",
    "dim",
    "accent",
    "thinking",
)

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

# Issue #21 themes (ADR-0184) — manifest/extension-contributed themes, kept
# SEPARATE from the built-in ``THEMES`` so built-ins always win a name
# collision and the registered set can be replaced wholesale (pi
# ``setRegisteredThemes``, theme.ts) on each ``_rebind`` reconcile without
# touching the built-ins. ``_REGISTERED_PATHS`` mirrors it for ``ThemeInfo``.
_REGISTERED: dict[str, Theme] = {}
_REGISTERED_PATHS: dict[str, str | None] = {}


def build_theme_from_data(
    name: str, roles: dict[str, str]
) -> Theme:
    """Build a :class:`Theme` from a name + a ``{role: color}`` map.

    Only :data:`THEME_ROLES` keys are honored; a color that Rich cannot parse
    is dropped (with a warning) so it can never raise mid-transcript-render —
    the role then falls through to the identity branch of ``_make_fg``. Unknown
    role keys are ignored (forward-compat). Reuses the built-in style factories
    so a file theme is byte-for-byte the same shape as ``default``/``dark``.
    """

    clean: dict[str, str] = {}
    for role, color in roles.items():
        if role not in THEME_ROLES:
            logger.warning(
                "theme %r: unknown role %r ignored (known: %s)",
                name,
                role,
                "/".join(THEME_ROLES),
            )
            continue
        if not isinstance(color, str):
            logger.warning("theme %r: role %r color is not a string; ignored", name, role)
            continue
        try:
            Style(color=color).render("x")  # validate: bad color raises HERE, not at render
        except Exception:  # noqa: BLE001 — a bad color must not brick the theme
            logger.warning("theme %r: role %r has invalid color %r; ignored", name, role, color)
            continue
        clean[role] = color
    return Theme(
        name=name,
        fg=_make_fg(clean),
        bg=_make_bg(),
        bold=_make_bold(),
        italic=_make_italic(),
    )


def register_themes(themes: list[tuple[Theme, str | None]]) -> None:
    """Replace the registered (non-built-in) theme set (pi ``setRegisteredThemes``).

    Called by the manifest adapter on every ``_rebind`` with the FULL current
    list, so a removed plugin's themes vanish (wholesale replace = reconcile).
    Built-ins always win a name collision (skipped with a warning); among
    registered themes, FIRST registration wins (load order = priority, the
    ``get_shortcuts`` convention). Each item is ``(theme, source_path)``.
    """

    _REGISTERED.clear()
    _REGISTERED_PATHS.clear()
    for theme, path in themes:
        name = theme.name
        if name in THEMES:
            logger.warning(
                "extension theme %r skipped: shadows a built-in theme", name
            )
            continue
        if name in _REGISTERED:
            logger.warning(
                "extension theme %r skipped: already registered by an earlier extension",
                name,
            )
            continue
        _REGISTERED[name] = theme
        _REGISTERED_PATHS[name] = path


def get_theme(name: str) -> Theme | None:
    """Return the :class:`~aelix_coding_agent.extensions.widget_protocols.Theme`
    under *name* — built-ins first, then registered — or ``None`` if not found.
    """
    return THEMES.get(name) or _REGISTERED.get(name)


def all_theme_names() -> list[str]:
    """Built-in + registered theme names (built-ins first, deduped) — the
    source the ``/settings`` theme picker enumerates so manifest themes appear.
    """
    names = list(THEMES)
    names.extend(n for n in _REGISTERED if n not in THEMES)
    return names


def list_theme_infos() -> list[ThemeInfo]:
    """A :class:`~aelix_coding_agent.extensions.ext_ui.ThemeInfo` per theme
    (``path`` = the theme file for registered themes, ``None`` for built-ins).
    """
    infos = [ThemeInfo(name=name) for name in THEMES]
    infos.extend(
        ThemeInfo(name=name, path=_REGISTERED_PATHS.get(name))
        for name in _REGISTERED
        if name not in THEMES
    )
    return infos


__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "THEME_ROLES",
    "all_theme_names",
    "build_theme_from_data",
    "dark",
    "dark_editor",
    "default",
    "default_editor",
    "get_theme",
    "light",
    "light_editor",
    "list_theme_infos",
    "register_themes",
]
