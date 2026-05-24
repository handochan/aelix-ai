"""Aelix TUI primitive Protocols (Sprint 6h₉c).

These are framework-agnostic Protocols that the extension surface
(``ExtensionUIContext``) references. Concrete implementations land in
Sprint 6h₁₀b on top of prompt-toolkit + Rich (ADR-0088 amend).

Pi parity note: these Protocols mirror Pi's TypeScript interfaces in
``pi/packages/tui/src/`` (Mario Zechner's ``pi-tui`` library) at SHA
``734e08e``. The Aelix Protocols are library-agnostic; Sprint 6h₁₀b's
concrete classes wrap prompt-toolkit / Rich primitives behind these
Protocols (composition, not inheritance — ADR-0088 CRITICAL invariant).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# === Component — render(width) -> list[str] + optional input/invalidate ===


@runtime_checkable
class Component(Protocol):
    """Pi ``Component`` interface (``pi-tui/src/tui.ts``).

    Implementations return raw ANSI string lines per width. Optional
    ``handle_input`` consumes raw terminal input data when the component
    has focus. ``invalidate()`` clears any cached render state.
    """

    def render(self, width: int) -> list[str]: ...

    def handle_input(self, data: str) -> None: ...
    """Optional. No-op default for non-interactive components."""

    def invalidate(self) -> None: ...
    """Optional. No-op default when no cache exists."""


@runtime_checkable
class Container(Component, Protocol):
    """Pi ``Container`` (``pi-tui/src/tui.ts``) — Component with children.

    Children are stacked vertically by default; subclasses customize
    layout via overridden ``render``.
    """

    def add_child(self, child: Component) -> None: ...
    def remove_child(self, child: Component) -> None: ...
    def clear(self) -> None: ...


# === Focusable — IME / cursor positioning marker ===


@runtime_checkable
class Focusable(Protocol):
    """Pi ``Focusable`` interface for CJK IME cursor positioning.

    Implementations expose a ``focused: bool`` attribute the host TUI
    sets when focus changes. The host scans rendered output for
    :data:`CURSOR_MARKER` (a zero-width APC escape) to position the
    hardware terminal cursor for IME candidate windows.

    Aelix-additive divergence: prompt-toolkit handles IME natively via
    its Buffer cursor API, so :data:`CURSOR_MARKER` is documented but
    not the primary mechanism in the Aelix concrete implementation;
    the Protocol exists for Pi-parity in extensions that bring their
    own cursor management.
    """

    focused: bool


CURSOR_MARKER: str = "\x1b_C\x1b\\"
"""Pi ``CURSOR_MARKER`` APC escape sequence."""


# === Theme + EditorTheme ===


def _identity_role(_role: str, text: str) -> str:
    return text


def _identity_text(text: str) -> str:
    return text


@dataclass(frozen=True)
class Theme:
    """Aelix Theme — passed to extensions as ``ctx.ui.theme`` readonly.

    Mirrors Pi ``Theme`` shape (``coding-agent/src/modes/interactive/
    theme/theme.ts``) at the field surface needed by extensions:
    foreground colors by named role, background colors, bold/italic.

    Sprint 6h₉c: data shape only. Sprint 6h₁₀b binds concrete prompt-
    toolkit Style + Rich Style equivalents.
    """

    name: str
    # Foreground color resolver: takes (role_name, text) -> styled string
    fg: Callable[[str, str], str] = field(default=_identity_role)
    # Background color resolver
    bg: Callable[[str, str], str] = field(default=_identity_role)
    # Style modifiers
    bold: Callable[[str], str] = field(default=_identity_text)
    italic: Callable[[str], str] = field(default=_identity_text)


@dataclass(frozen=True)
class EditorTheme:
    """Pi ``EditorTheme`` — subset of Theme for editor border + autocomplete."""

    border: Callable[[str], str] = field(default=_identity_text)
    border_focused: Callable[[str], str] = field(default=_identity_text)
    autocomplete_selected: Callable[[str], str] = field(default=_identity_text)


# === EditorComponent + KeybindingsManager + TUI ===


@runtime_checkable
class EditorComponent(Component, Protocol):
    """Pi ``EditorComponent`` — extension-replaceable input editor."""

    def get_text(self) -> str: ...
    def set_text(self, text: str) -> None: ...


@runtime_checkable
class KeybindingsManager(Protocol):
    """Pi ``KeybindingsManager`` — app-level keybinding registry."""

    def get_binding(self, action: str) -> str | None: ...
    def set_binding(self, action: str, key: str) -> None: ...


@runtime_checkable
class TUI(Protocol):
    """Pi ``TUI`` — root TUI façade passed to extension factories."""

    def request_render(self) -> None: ...
    def add_input_listener(
        self,
        handler: Callable[[str], None],
    ) -> Callable[[], None]: ...


# === Autocomplete ===


@runtime_checkable
class AutocompleteProvider(Protocol):
    """Pi ``AutocompleteProvider``."""

    def get_suggestions(
        self,
        buffer_text: str,
        cursor_position: int,
    ) -> list[str]: ...


# === Overlay primitives ===


OverlayAnchor = Literal[
    "center",
    "top-left", "top-right",
    "bottom-left", "bottom-right",
    "top-center", "bottom-center",
    "left-center", "right-center",
]
"""Pi ``OverlayAnchor`` — 9 positions."""

SizeValue = int | str
"""Pi ``SizeValue`` — int (cells) or percentage str like ``"50%"``."""


@dataclass(frozen=True)
class OverlayMargin:
    """Pi ``OverlayMargin`` — uniform int or per-side."""

    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0

    @classmethod
    def all(cls, value: int) -> OverlayMargin:
        return cls(top=value, right=value, bottom=value, left=value)


@dataclass(frozen=True)
class OverlayOptions:
    """Pi ``OverlayOptions`` — anchor + sizing + responsive visibility.

    Pi source: ``pi-tui/src/tui.ts`` (verified at SHA ``734e08e``).
    """

    width: SizeValue | None = None
    min_width: int | None = None
    max_height: SizeValue | None = None
    anchor: OverlayAnchor = "center"
    offset_x: int = 0
    offset_y: int = 0
    row: SizeValue | None = None
    col: SizeValue | None = None
    margin: OverlayMargin | None = None
    visible: Callable[[int, int], bool] | None = None
    """Called per-frame: ``visible(term_width, term_height) -> bool``."""
    non_capturing: bool = False
    """If True, overlay does not auto-focus on show."""


@runtime_checkable
class OverlayHandle(Protocol):
    """Pi ``OverlayHandle`` — programmatic overlay control."""

    def hide(self) -> None: ...
    def set_hidden(self, hidden: bool) -> None: ...
    def is_hidden(self) -> bool: ...
    def focus(self) -> None: ...
    def unfocus(self) -> None: ...
    def is_focused(self) -> bool: ...


# === Footer data provider (forward ref from ext_ui.FooterFactory) ===


@runtime_checkable
class ReadonlyFooterDataProvider(Protocol):
    """Pi ``ReadonlyFooterDataProvider`` (``core/footer-data-provider.ts``).

    Read-only access to data the host owns but extensions need for
    custom footers: git branch (live), extension statuses (from
    setStatus calls).
    """

    def get_git_branch(self) -> str | None: ...
    def get_extension_statuses(self) -> dict[str, str]: ...
    def on_branch_change(self, callback: Callable[[], None]) -> Callable[[], None]: ...


__all__ = [
    "CURSOR_MARKER",
    "AutocompleteProvider",
    "Component",
    "Container",
    "EditorComponent",
    "EditorTheme",
    "Focusable",
    "KeybindingsManager",
    "OverlayAnchor",
    "OverlayHandle",
    "OverlayMargin",
    "OverlayOptions",
    "ReadonlyFooterDataProvider",
    "SizeValue",
    "TUI",
    "Theme",
]
