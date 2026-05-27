"""Sprint 6h₁₀b (ADR-0105) — AelixTUIContext: concrete ExtensionUIContext.

The prompt-toolkit + Rich implementation of the Pi-parity 27-method
``ExtensionUIContext`` surface. Backing:
- **dialogs / custom** → :func:`~aelix_coding_agent.tui.overlay.show_modal`
- **status / working / footer / header / widgets / title / editor** → :class:`AelixChrome`
- **theme** → the :mod:`~aelix_coding_agent.tui.themes` registry (built-ins only this sprint)

``run_tui`` binds an instance via ``harness.runtime.bind_ui(ctx)`` so loaded
(Tier-1 in-process) extensions can drive the UI. Manifest-contributed themes,
the Tier-2 descriptor renderer, and per-extension ``ui_tui_trusted`` gating are
deferred (ADR-0105).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from aelix_coding_agent.extensions.ext_ui import (
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
from aelix_coding_agent.extensions.widget_protocols import Component, Theme
from aelix_coding_agent.tui import themes as theme_registry
from aelix_coding_agent.tui.overlay import show_modal

if TYPE_CHECKING:
    from aelix_coding_agent.extensions.ext_ui import AutocompleteProviderFactory
    from aelix_coding_agent.tui.chrome import AelixChrome
    from aelix_coding_agent.tui.footer_data import AelixFooterData

_RENDER_WIDTH = 80  # best-effort width for factory-rendered widget lines


def _resolve(future: asyncio.Future[Any], value: Any) -> None:
    if not future.done():
        future.set_result(value)


class AelixKeybindings:
    """Minimal ``KeybindingsManager`` — a dict of action → key."""

    def __init__(self) -> None:
        self._bindings: dict[str, str] = {}

    def get_binding(self, action: str) -> str | None:
        return self._bindings.get(action)

    def set_binding(self, action: str, key: str) -> None:
        self._bindings[action] = key


class AelixTUI:
    """Minimal ``TUI`` façade passed to extension factories."""

    def __init__(self, ctx: AelixTUIContext) -> None:
        self._ctx = ctx

    def request_render(self) -> None:
        self._ctx.chrome.invalidate()

    def add_input_listener(self, handler: Callable[[str], None]) -> Callable[[], None]:
        return self._ctx.on_terminal_input(lambda data: _wrap_listener(handler, data))


def _wrap_listener(handler: Callable[[str], None], data: str) -> None:
    handler(data)


class AelixTUIContext:
    """Concrete :class:`~aelix_coding_agent.extensions.ext_ui.ExtensionUIContext`."""

    def __init__(
        self,
        chrome: AelixChrome,
        footer: AelixFooterData,
        *,
        model_provider: Callable[[], str | None] | None = None,
        cwd: str | None = None,
        mode: str = "default",
    ) -> None:
        self.chrome = chrome
        self._footer = footer
        self._model_provider = model_provider
        self._cwd = cwd
        self._mode = mode
        self._theme: Theme = theme_registry.DEFAULT_THEME
        self._tools_expanded = False
        self._hidden_thinking_label: str | None = None
        self._terminal_handlers: list[TerminalInputHandler] = []
        self._autocomplete: list[AutocompleteProviderFactory] = []
        self._editor_factory: EditorFactory | None = None
        self._footer_factory: FooterFactory | None = None
        self._notify_seq = 0
        self._tui = AelixTUI(self)
        self._kb = AelixKeybindings()
        self._refresh_footer()

    # === Dialogs (5) =======================================================

    async def select(
        self, title: str, options: list[str], opts: ExtensionUIDialogOptions | None = None
    ) -> str | None:
        shown = options[:9]  # number keys 1-9 select; only these are bindable

        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()
            for index, option in enumerate(shown):
                kb.add(str(index + 1))(
                    lambda _e, o=option: _resolve(result, o)  # type: ignore[misc]
                )
            kb.add("escape")(lambda _e: _resolve(result, None))
            lines = [title, *[f"{i + 1}. {o}" for i, o in enumerate(shown)]]
            if len(options) > len(shown):
                lines.append(f"… (+{len(options) - len(shown)} more not shown)")
            return Window(
                FormattedTextControl("\n".join(lines), focusable=True, key_bindings=kb),
                dont_extend_height=True,
            )

        return await show_modal(self.chrome, build)

    async def confirm(
        self, title: str, message: str, opts: ExtensionUIDialogOptions | None = None
    ) -> bool:
        def build(result: asyncio.Future[Any]) -> Window:
            kb = KeyBindings()
            for key in ("y", "Y"):
                kb.add(key)(lambda _e: _resolve(result, True))
            for key in ("n", "N", "escape"):
                kb.add(key)(lambda _e: _resolve(result, False))
            return Window(
                FormattedTextControl(
                    f"{title}\n{message} [y/n]", focusable=True, key_bindings=kb
                ),
                dont_extend_height=True,
            )

        return bool(await show_modal(self.chrome, build))

    async def input(
        self,
        title: str,
        placeholder: str | None = None,
        opts: ExtensionUIDialogOptions | None = None,
    ) -> str | None:
        buffer = Buffer(multiline=False)

        def build(result: asyncio.Future[Any]) -> HSplit:
            kb = KeyBindings()
            kb.add("enter")(lambda _e: _resolve(result, buffer.text))
            kb.add("escape")(lambda _e: _resolve(result, None))
            return HSplit(
                [
                    Window(FormattedTextControl(title), dont_extend_height=True),
                    Window(BufferControl(buffer, key_bindings=kb), height=Dimension(min=1)),
                ]
            )

        return await show_modal(self.chrome, build)

    def notify(self, message: str, kind: NotificationKind = "info") -> None:
        # Generation token: a stale timer must not clear a newer notification.
        self._notify_seq += 1
        token = self._notify_seq
        self.chrome.set_status("__notify__", message)
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().call_later(3.0, lambda: self._clear_notify(token))

    def _clear_notify(self, token: int) -> None:
        if token == self._notify_seq:
            self.chrome.set_status("__notify__", None)

    async def editor(self, title: str, prefill: str | None = None) -> str | None:
        buffer = Buffer(multiline=True)
        if prefill:
            buffer.text = prefill
            buffer.cursor_position = len(prefill)

        def build(result: asyncio.Future[Any]) -> HSplit:
            kb = KeyBindings()
            # Esc cancels (consistent with the other dialogs); Ctrl+S saves.
            kb.add("c-s")(lambda _e: _resolve(result, buffer.text))
            kb.add("escape")(lambda _e: _resolve(result, None))
            kb.add("c-c")(lambda _e: _resolve(result, None))
            return HSplit(
                [
                    Window(
                        FormattedTextControl(f"{title} (Ctrl+S to save, Esc to cancel)"),
                        dont_extend_height=True,
                    ),
                    Window(BufferControl(buffer, key_bindings=kb), height=Dimension(min=3)),
                ]
            )

        return await show_modal(self.chrome, build)

    # === Raw input (1) =====================================================

    def on_terminal_input(self, handler: TerminalInputHandler) -> Callable[[], None]:
        # NOTE (ADR-0105 deferred): handlers are registered + unsubscribable, but
        # raw-input *dispatch* (feeding keys to handlers + honoring
        # TerminalInputResult.consume/data) is not yet wired into the chrome key
        # processor. Registration is functional; dispatch lands in a later sprint.
        self._terminal_handlers.append(handler)

        def _unsub() -> None:
            with contextlib.suppress(ValueError):
                self._terminal_handlers.remove(handler)

        return _unsub

    # === Status / working (5) ==============================================

    def set_status(self, key: str, text: str | None) -> None:
        self.chrome.set_status(key, text)

    def set_working_message(self, message: str | None = None) -> None:
        self.chrome.set_working_message(message)

    def set_working_visible(self, visible: bool) -> None:
        self.chrome.set_working_visible(visible)

    def set_working_indicator(self, options: WorkingIndicatorOptions | None = None) -> None:
        frames = options.frames if options is not None else None
        interval = options.interval_ms if options is not None else None
        self.chrome.set_working_indicator(frames, interval)

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self._hidden_thinking_label = label

    # === Layout (5) ========================================================

    def set_widget(
        self,
        key: str,
        content: list[str] | WidgetFactory | None,
        options: ExtensionWidgetOptions | None = None,
    ) -> None:
        above = (options.placement if options else "above_editor") == "above_editor"
        if content is None:
            self.chrome.set_widget(key, None, above=above)
            return
        if callable(content):
            component = content(self._tui, self._theme)
            lines = component.render(_RENDER_WIDTH)
        else:
            lines = list(content)
        self.chrome.set_widget(key, lines, above=above)

    def set_footer(self, factory: FooterFactory | None) -> None:
        self._footer_factory = factory
        self._refresh_footer()

    def set_header(self, factory: HeaderFactory | None) -> None:
        if factory is None:
            self.chrome.set_header_line("")
            return
        component = factory(self._tui, self._theme)
        self.chrome.set_header_line("\n".join(component.render(_RENDER_WIDTH)))

    def set_title(self, title: str) -> None:
        self.chrome.set_title(title)

    # === Custom overlays (1) ===============================================

    async def custom(
        self, factory: CustomComponentFactory, options: CustomOptions | None = None
    ) -> object:
        result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        def _done(value: object) -> None:
            _resolve(result, value)

        component = factory(self._tui, self._theme, self._kb, _done)
        if inspect.isawaitable(component):
            component = await component
        comp: Component = component  # type: ignore[assignment]

        def build(_result: asyncio.Future[Any]) -> Window:
            return Window(
                FormattedTextControl(
                    lambda: ANSI("\n".join(comp.render(_RENDER_WIDTH))), focusable=True
                ),
                dont_extend_height=True,
            )

        opts = options or CustomOptions()
        ov = opts.overlay_options
        overlay_options = ov() if callable(ov) else ov  # responsive form → evaluate
        return await show_modal(
            self.chrome, build, options=overlay_options, on_handle=opts.on_handle, result=result
        )

    # === Editor remote control (5) =========================================

    def paste_to_editor(self, text: str) -> None:
        self.chrome.paste_to_editor(text)

    def set_editor_text(self, text: str) -> None:
        self.chrome.set_editor_text(text)

    def get_editor_text(self) -> str:
        return self.chrome.get_editor_text()

    def set_editor_component(self, factory: EditorFactory | None) -> None:
        self._editor_factory = factory

    def get_editor_component(self) -> EditorFactory | None:
        return self._editor_factory

    # === Autocomplete (1) ==================================================

    def add_autocomplete_provider(self, factory: AutocompleteProviderFactory) -> None:
        self._autocomplete.append(factory)

    # === Theme (5 + property) ==============================================

    @property
    def theme(self) -> Theme:
        return self._theme

    def get_all_themes(self) -> list[ThemeInfo]:
        return theme_registry.list_theme_infos()

    def get_theme(self, name: str) -> Theme | None:
        return theme_registry.get_theme(name)

    def set_theme(self, theme: str | Theme) -> SetThemeResult:
        if isinstance(theme, str):
            resolved = theme_registry.get_theme(theme)
            if resolved is None:
                return SetThemeResult(success=False, error=f"unknown theme: {theme}")
            self._theme = resolved
        else:
            self._theme = theme
        self.chrome.invalidate()
        return SetThemeResult(success=True)

    def get_tools_expanded(self) -> bool:
        return self._tools_expanded

    def set_tools_expanded(self, expanded: bool) -> None:
        self._tools_expanded = expanded

    # === internal ==========================================================

    def _refresh_footer(self) -> None:
        if self._footer_factory is not None:
            component = self._footer_factory(self._tui, self._theme, self._footer)
            self.chrome.set_footer_line("\n".join(component.render(_RENDER_WIDTH)))
            return
        branch = self._footer.get_git_branch()
        statuses = self._footer.get_extension_statuses()
        model = self._model_provider() if self._model_provider is not None else None
        segments = [
            s
            for s in (
                f"⏵⏵ {self._mode}" if self._mode else None,
                f"📂 {self._abbrev_cwd(self._cwd)}" if self._cwd else None,
                f"✱ {model}" if model else None,
                f"⎇ {branch}" if branch else None,
                *statuses.values(),
            )
            if s
        ]
        self.chrome.set_footer_line("  ·  ".join(segments))

    @staticmethod
    def _abbrev_cwd(cwd: str) -> str:
        """Home-abbreviate a path (``/home/x/p`` → ``~/p``); identity otherwise."""
        home = str(Path.home())
        if cwd == home:
            return "~"
        prefix = home.rstrip("/") + "/"
        if cwd.startswith(prefix):
            return "~/" + cwd[len(prefix):]
        return cwd


__all__ = ["AelixKeybindings", "AelixTUI", "AelixTUIContext"]
