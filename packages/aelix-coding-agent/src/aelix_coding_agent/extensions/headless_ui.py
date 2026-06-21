"""Headless ExtensionUIContext binding for Phase 5b.

Sprint 6h₉c (ADR-0100) — actual ExtensionUIContext closure ADR (clears
the Sprint 5a phantom "ADR-0033" reference; that ADR was a reserved
slot never written). This module supplies the default
``ExtensionUIContext`` binding that returns from
:attr:`ExtensionContext.ui` when no TUI is attached (e.g., RPC mode,
print mode, programmatic harness without an interactive shell).

Every method raises :exc:`NotImplementedError` with a clear pointer to
Sprint 6h₁₀b (Phase 5c-tui) where the prompt-toolkit + Rich + Aelix
widget layer binds the concrete behavior.

Extensions calling ``ctx.ui.*`` in Phase 5b will see immediate, typed,
actionable error messages instead of silent no-ops or AttributeErrors.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import overload

from .ext_ui import (
    AutocompleteProviderFactory,
    CustomComponentFactory,
    CustomOptions,
    EditorFactory,
    ExtensionUIDialogOptions,
    ExtensionWidgetOptions,
    FooterFactory,
    HeaderFactory,
    NotificationKind,
    SetThemeResult,
    TerminalInputHandler,
    ThemeInfo,
    WidgetFactory,
    WorkingIndicatorOptions,
)
from .widget_protocols import Theme

_HEADLESS_MESSAGE = (
    "ExtensionUIContext.{method} is not bound in Phase 5b-foundation; "
    "Sprint 6h₁₀b (Phase 5c-tui) supplies the prompt-toolkit + Rich + "
    "Aelix widget layer binding per ADR-0088 amend + ADR-0100. Guard "
    "early calls with `if ctx.has_ui:` (False in headless mode)."
)


def _raise_headless(method: str) -> NotImplementedError:
    return NotImplementedError(_HEADLESS_MESSAGE.format(method=method))


class HeadlessExtensionUIContext:
    """Default ``ExtensionUIContext`` binding — every method raises.

    Implements the
    :class:`~aelix_coding_agent.extensions.ext_ui.ExtensionUIContext`
    Protocol structurally (``runtime_checkable``). Instances are
    immutable; callers may freely cache the single
    :data:`HEADLESS_UI_CONTEXT` module-level singleton.
    """

    # === Dialogs (5) ===

    async def select(
        self,
        title: str,
        options: list[str],
        opts: ExtensionUIDialogOptions | None = None,
    ) -> str | None:
        raise _raise_headless("select")

    async def confirm(
        self,
        title: str,
        message: str,
        opts: ExtensionUIDialogOptions | None = None,
    ) -> bool:
        raise _raise_headless("confirm")

    async def input(
        self,
        title: str,
        placeholder: str | None = None,
        opts: ExtensionUIDialogOptions | None = None,
    ) -> str | None:
        raise _raise_headless("input")

    def notify(self, message: str, kind: NotificationKind = "info") -> None:
        raise _raise_headless("notify")

    async def editor(self, title: str, prefill: str | None = None) -> str | None:
        raise _raise_headless("editor")

    # === Raw input (1) ===

    def on_terminal_input(
        self,
        handler: TerminalInputHandler,
    ) -> Callable[[], None]:
        raise _raise_headless("on_terminal_input")

    # === Status / working (5) ===

    def set_status(self, key: str, text: str | None) -> None:
        raise _raise_headless("set_status")

    def set_working_message(self, message: str | None = None) -> None:
        raise _raise_headless("set_working_message")

    def set_working_visible(self, visible: bool) -> None:
        raise _raise_headless("set_working_visible")

    def set_working_indicator(
        self,
        options: WorkingIndicatorOptions | None = None,
    ) -> None:
        raise _raise_headless("set_working_indicator")

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        raise _raise_headless("set_hidden_thinking_label")

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
        raise _raise_headless("set_widget")

    def set_footer(self, factory: FooterFactory | None) -> None:
        raise _raise_headless("set_footer")

    def set_header(self, factory: HeaderFactory | None) -> None:
        raise _raise_headless("set_header")

    def set_title(self, title: str) -> None:
        raise _raise_headless("set_title")

    # === Custom overlays (1) ===

    async def custom(
        self,
        factory: CustomComponentFactory,
        options: CustomOptions | None = None,
    ) -> object:
        raise _raise_headless("custom")

    # === Editor remote control (5) ===

    def paste_to_editor(self, text: str) -> None:
        raise _raise_headless("paste_to_editor")

    def set_editor_text(self, text: str) -> None:
        raise _raise_headless("set_editor_text")

    def get_editor_text(self) -> str:
        raise _raise_headless("get_editor_text")

    def set_editor_component(self, factory: EditorFactory | None) -> None:
        raise _raise_headless("set_editor_component")

    def get_editor_component(self) -> EditorFactory | None:
        raise _raise_headless("get_editor_component")

    # === Autocomplete (1) ===

    def add_autocomplete_provider(
        self,
        factory: AutocompleteProviderFactory,
    ) -> None:
        raise _raise_headless("add_autocomplete_provider")

    # === Theme (5 methods + 1 readonly property) ===

    @property
    def theme(self) -> Theme:
        # Return a no-op default Theme rather than raising. ``theme`` is the
        # protocol's only *property* member, so ``isinstance(ctx,
        # ExtensionUIContext)`` under ``@runtime_checkable`` INVOKES this getter
        # on Python 3.11 (3.11 probes data members via ``hasattr`` → calls the
        # property; 3.12 does not). Raising here breaks that structural
        # conformance check on 3.11. The callable members raise only when
        # *invoked*, so they are unaffected. The default ``Theme``'s fg/bg/bold/
        # italic resolvers are identity functions — safe no-op styling for
        # headless output; ``get_theme``/``set_theme`` still raise.
        return Theme(name="headless")

    def get_all_themes(self) -> list[ThemeInfo]:
        raise _raise_headless("get_all_themes")

    def get_theme(self, name: str) -> Theme | None:
        raise _raise_headless("get_theme")

    def set_theme(self, theme: str | Theme) -> SetThemeResult:
        raise _raise_headless("set_theme")

    def get_tools_expanded(self) -> bool:
        raise _raise_headless("get_tools_expanded")

    def set_tools_expanded(self, expanded: bool) -> None:
        raise _raise_headless("set_tools_expanded")


HEADLESS_UI_CONTEXT: HeadlessExtensionUIContext = HeadlessExtensionUIContext()
"""Singleton headless instance — returned from :attr:`ExtensionContext.ui` by default."""


__all__ = [
    "HEADLESS_UI_CONTEXT",
    "HeadlessExtensionUIContext",
]
