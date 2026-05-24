"""Pi-parity ``ExtensionUIContext`` Protocol + supporting types.

ADR-0100 (Sprint 6h₉c) — actual ExtensionUIContext closure ADR (clears
the Sprint 5a phantom "ADR-0033" reference; that ADR was a reserved
slot in ``docs/decisions/`` that was never written, hence the 0032 →
0034 numbering jump).

Pi source: ``packages/coding-agent/src/core/extensions/types.ts:124-275``
at SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016`` — 27 methods + 1
readonly ``theme`` property = 28 total members.

Concrete implementations land in Sprint 6h₁₀b (Phase 5c-tui,
prompt-toolkit + Rich + Aelix widget layer per ADR-0088 amend). Phase
5b-foundation ships the Protocol only; ``HeadlessExtensionUIContext``
(see :mod:`aelix_coding_agent.extensions.headless_ui`) is the default
binding when no TUI is attached (raises ``NotImplementedError`` per
method to surface "called too early" errors clearly).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, overload, runtime_checkable

# Forward refs to widget-protocol types in widget_protocols.py
from .widget_protocols import (
    TUI,
    AutocompleteProvider,
    Component,
    EditorComponent,
    EditorTheme,
    KeybindingsManager,
    OverlayHandle,
    OverlayOptions,
    ReadonlyFooterDataProvider,
    Theme,
)

# Type aliases mirroring Pi types.ts:96-122

NotificationKind = Literal["info", "warning", "error"]
"""Pi ``"info" | "warning" | "error"`` (types.ts:135)."""

WidgetPlacement = Literal["above_editor", "below_editor"]
"""Pi ``WidgetPlacement`` (types.ts:108).

Pi camelCase: ``"aboveEditor"``/``"belowEditor"``. Aelix uses snake_case
(PEP 8); JSON / wire round-trip preserves the Aelix snake_case form.
The Pi-parity divergence is documented in ADR-0100 §"Aelix-additive
divergences from Pi".
"""


@dataclass(frozen=True)
class ExtensionUIDialogOptions:
    """Pi ``ExtensionUIDialogOptions`` parity (types.ts:96-101).

    AbortSignal: Pi uses ``AbortSignal``; Aelix uses ``asyncio.Event``
    via the optional ``signal`` field (set when called) — caller awaits
    ``signal.wait()`` for cancellation. Same semantic, idiomatic Python.
    """

    signal: object | None = None
    """asyncio.Event-like (any object with .wait())."""
    timeout: float | None = None
    """Pi uses milliseconds (int); Aelix uses seconds (float) per asyncio idiom."""


@dataclass(frozen=True)
class ExtensionWidgetOptions:
    """Pi ``ExtensionWidgetOptions`` parity (types.ts:108-110)."""

    placement: WidgetPlacement = "above_editor"


# Pi: type TerminalInputHandler = (data: string) => { consume?: boolean; data?: string } | undefined;
@dataclass(frozen=True)
class TerminalInputResult:
    """Pi ``{ consume?, data? }`` return shape from TerminalInputHandler."""

    consume: bool = False
    data: str | None = None


TerminalInputHandler = Callable[[str], "TerminalInputResult | None"]
"""Pi ``TerminalInputHandler`` (types.ts:113).

Aelix returns ``TerminalInputResult | None`` instead of Pi's anonymous
object literal — same semantic.
"""


@dataclass(frozen=True)
class WorkingIndicatorOptions:
    """Pi ``WorkingIndicatorOptions`` parity (types.ts:116-119)."""

    frames: list[str] | None = None
    interval_ms: int | None = None


AutocompleteProviderFactory = Callable[[AutocompleteProvider], AutocompleteProvider]
"""Pi ``AutocompleteProviderFactory`` (types.ts:122)."""

EditorFactory = Callable[[TUI, EditorTheme, KeybindingsManager], EditorComponent]
"""Pi ``EditorFactory`` (types.ts:122)."""


@dataclass(frozen=True)
class ThemeInfo:
    """Pi ``{ name: string; path: string | undefined }[]`` element (types.ts:267)."""

    name: str
    path: str | None = None


@dataclass(frozen=True)
class SetThemeResult:
    """Pi ``{ success: boolean; error?: string }`` return shape (types.ts:273)."""

    success: bool
    error: str | None = None


# Custom component factory — Pi types.ts:202-208
CustomComponentFactory = Callable[
    [TUI, Theme, KeybindingsManager, Callable[[object], None]],
    "Component | Awaitable[Component]",
]
"""Pi ``CustomComponentFactory`` (types.ts:202-208).

Receives ``(tui, theme, keybindings, resolve)``; returns a
:class:`Component` synchronously OR an :class:`Awaitable` resolving to
one.
"""


@dataclass(frozen=True)
class CustomOptions:
    """Pi ``custom`` options bag (types.ts:209-216)."""

    overlay: bool = False
    overlay_options: OverlayOptions | Callable[[], OverlayOptions] | None = None
    on_handle: Callable[[OverlayHandle], None] | None = None


# Widget factory — Pi types.ts:177
WidgetFactory = Callable[[TUI, Theme], Component]
"""Pi ``WidgetFactory`` (types.ts:177)."""


# Footer factory — Pi types.ts:184-188
FooterFactory = Callable[
    [TUI, Theme, ReadonlyFooterDataProvider],
    Component,
]
"""Pi ``FooterFactory`` (types.ts:184-188)."""


# Header factory — Pi types.ts:194
HeaderFactory = Callable[[TUI, Theme], Component]
"""Pi ``HeaderFactory`` (types.ts:194)."""


# ============================================================================
# ExtensionUIContext Protocol
# ============================================================================


@runtime_checkable
class ExtensionUIContext(Protocol):
    """Pi-parity 27-method + 1 readonly property surface.

    Pi source: ``coding-agent/src/core/extensions/types.ts:124-275`` at
    SHA ``734e08e``.

    Implementations:
      - :class:`~aelix_coding_agent.extensions.headless_ui.HeadlessExtensionUIContext`
        — Phase 5b default (raises NotImplementedError per method).
      - prompt-toolkit + Rich + Aelix widget layer — Phase 5c-tui
        (Sprint 6h₁₀b).
    """

    # === Dialogs (5) ===

    async def select(
        self,
        title: str,
        options: list[str],
        opts: ExtensionUIDialogOptions | None = None,
    ) -> str | None:
        """Pi ``select(title, options, opts?)`` (types.ts:126)."""
        ...

    async def confirm(
        self,
        title: str,
        message: str,
        opts: ExtensionUIDialogOptions | None = None,
    ) -> bool:
        """Pi ``confirm(title, message, opts?)`` (types.ts:129)."""
        ...

    async def input(
        self,
        title: str,
        placeholder: str | None = None,
        opts: ExtensionUIDialogOptions | None = None,
    ) -> str | None:
        """Pi ``input(title, placeholder?, opts?)`` (types.ts:132)."""
        ...

    def notify(self, message: str, kind: NotificationKind = "info") -> None:
        """Pi ``notify(message, type?)`` (types.ts:135).

        Pi-parity divergence: Aelix renames ``type`` → ``kind`` to avoid
        shadowing the Python ``type`` built-in. ADR-0100 documents.
        """
        ...

    async def editor(
        self,
        title: str,
        prefill: str | None = None,
    ) -> str | None:
        """Pi ``editor(title, prefill?)`` — multi-line editor dialog (types.ts:221)."""
        ...

    # === Raw input (1) ===

    def on_terminal_input(self, handler: TerminalInputHandler) -> Callable[[], None]:
        """Pi ``onTerminalInput(handler)`` (types.ts:144).

        Returns an unsubscribe callable.
        """
        ...

    # === Status / working (5) ===

    def set_status(self, key: str, text: str | None) -> None:
        """Pi ``setStatus(key, text)`` (types.ts:147). ``text=None`` clears."""
        ...

    def set_working_message(self, message: str | None = None) -> None:
        """Pi ``setWorkingMessage(message?)`` (types.ts:150). No arg restores default."""
        ...

    def set_working_visible(self, visible: bool) -> None:
        """Pi ``setWorkingVisible(visible)`` (types.ts:153)."""
        ...

    def set_working_indicator(
        self,
        options: WorkingIndicatorOptions | None = None,
    ) -> None:
        """Pi ``setWorkingIndicator(options?)`` (types.ts:162).

        No arg restores default spinner. ``frames=[]`` hides;
        ``frames=["●"]`` static; custom frames rendered verbatim.
        """
        ...

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        """Pi ``setHiddenThinkingLabel(label?)`` (types.ts:165). No arg restores default."""
        ...

    # === Layout (5) ===

    @overload
    def set_widget(
        self,
        key: str,
        content: list[str] | None,
        options: ExtensionWidgetOptions | None = None,
    ) -> None: ...
    @overload
    def set_widget(
        self,
        key: str,
        content: WidgetFactory | None,
        options: ExtensionWidgetOptions | None = None,
    ) -> None: ...
    def set_widget(
        self,
        key: str,
        content: list[str] | WidgetFactory | None,
        options: ExtensionWidgetOptions | None = None,
    ) -> None:
        """Pi ``setWidget(key, content, options?)`` overload (types.ts:173-178).

        Two overloads: string-array content OR a factory. ``content=None``
        removes the widget under ``key``.
        """
        ...

    def set_footer(self, factory: FooterFactory | None) -> None:
        """Pi ``setFooter(factory)`` (types.ts:184). ``factory=None`` restores default."""
        ...

    def set_header(self, factory: HeaderFactory | None) -> None:
        """Pi ``setHeader(factory)`` (types.ts:194). ``factory=None`` restores default."""
        ...

    def set_title(self, title: str) -> None:
        """Pi ``setTitle(title)`` — set terminal window/tab title (types.ts:198)."""
        ...

    # === Custom overlays (1) ===

    async def custom(
        self,
        factory: CustomComponentFactory,
        options: CustomOptions | None = None,
    ) -> object:
        """Pi ``custom<T>(factory, options?)`` — custom component with focus (types.ts:201).

        Aelix returns ``object`` (untyped); callers should narrow. PEP
        695 Generic[T] could refine this in a future sprint.
        """
        ...

    # === Editor remote control (5) ===

    def paste_to_editor(self, text: str) -> None:
        """Pi ``pasteToEditor(text)`` (types.ts:221)."""
        ...

    def set_editor_text(self, text: str) -> None:
        """Pi ``setEditorText(text)`` (types.ts:224)."""
        ...

    def get_editor_text(self) -> str:
        """Pi ``getEditorText()`` (types.ts:227)."""
        ...

    def set_editor_component(self, factory: EditorFactory | None) -> None:
        """Pi ``setEditorComponent(factory)`` (types.ts:259). ``factory=None`` restores default."""
        ...

    def get_editor_component(self) -> EditorFactory | None:
        """Pi ``getEditorComponent()`` (types.ts:262)."""
        ...

    # === Autocomplete (1) ===

    def add_autocomplete_provider(
        self,
        factory: AutocompleteProviderFactory,
    ) -> None:
        """Pi ``addAutocompleteProvider(factory)`` (types.ts:232)."""
        ...

    # === Theme (5 methods + 1 readonly property) ===

    @property
    def theme(self) -> Theme:
        """Pi ``readonly theme: Theme`` (types.ts:265)."""
        ...

    def get_all_themes(self) -> list[ThemeInfo]:
        """Pi ``getAllThemes()`` (types.ts:268)."""
        ...

    def get_theme(self, name: str) -> Theme | None:
        """Pi ``getTheme(name)`` (types.ts:271)."""
        ...

    def set_theme(self, theme: str | Theme) -> SetThemeResult:
        """Pi ``setTheme(theme)`` (types.ts:274)."""
        ...

    def get_tools_expanded(self) -> bool:
        """Pi ``getToolsExpanded()`` (types.ts:277)."""
        ...

    def set_tools_expanded(self, expanded: bool) -> None:
        """Pi ``setToolsExpanded(expanded)`` (types.ts:280)."""
        ...


__all__ = [
    "AutocompleteProviderFactory",
    "CustomComponentFactory",
    "CustomOptions",
    "EditorFactory",
    "ExtensionUIContext",
    "ExtensionUIDialogOptions",
    "ExtensionWidgetOptions",
    "FooterFactory",
    "HeaderFactory",
    "NotificationKind",
    "SetThemeResult",
    "TerminalInputHandler",
    "TerminalInputResult",
    "ThemeInfo",
    "WidgetFactory",
    "WidgetPlacement",
    "WorkingIndicatorOptions",
]
