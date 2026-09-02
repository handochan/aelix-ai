# Sprint 6h₉c — ExtensionUIContext Protocol (Pi-parity 27-method surface)

Status: Binding (W1 spec; do not modify after W1 closure)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — Sprint metadata

| Field | Value |
|---|---|
| Sprint id | `6h₉c` |
| Phase | 5b-foundation, sprint 3 of ~6 |
| Workflow | ADR-0032 W0-W6 |
| Scope class | Code (Protocol + supporting types + headless stub) + tests + 1 ADR |
| Spec author | Main agent (architect READ-ONLY in current OMC profile) |
| Predecessors | Sprint 6h₉a (contracts), Sprint 6h₉b (manifest loader) |
| Owning ADR closure | ADR-0100 (NEW — Sprint 6h₉c closure; also clears the phantom "ADR-0033" reference in `api.py`) |
| ADR-0033 status | **DOES NOT EXIST** — `docs/decisions/` numbering jumps 0032 → 0034. Sprint 5a `api.py` docstrings cite "ADR-0033" as a reserved slot that was never written. Sprint 6h₉c clears the phantom reference by replacing it with ADR-0100 in code comments. |

---

## §1 — Background

### §1.1 — The "ADR-0033" phantom-placeholder (Sprint 5a heritage)

`packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py:745` currently has:

```python
@property
def ui(self) -> Any:
    """Pi ``ui: ExtensionUIContext`` — deferred to ADR-0033 (Phase 5 TUI)."""
    raise ExtensionError(
        "invalid_state",
        "ExtensionContext.ui is deferred to ADR-0033 (Phase 5 TUI); "
        "guard access with `if ctx.has_ui:`.",
    )
```

And `has_ui` is constant `False` (line 738-742).

This was a deliberate Sprint 5a deferral: the ExtensionContext could not type `ui` properly because the `ExtensionUIContext` Protocol did not exist yet.

**ADR-0033 phantom reference**: `docs/decisions/` numbering jumps 0032 → 0034. The "ADR-0033" cited in Sprint 5a docstrings was a **reserved slot that was never written**. Sprint 6h₉c writes the actual ADR (ADR-0100) and clears the phantom reference from code comments. No ADR-0033 file is created or amended in this sprint.

Sprint 6h₉c closes the gap.

### §1.2 — Pi `ExtensionUIContext` (verified at SHA `734e08e`)

`packages/coding-agent/src/core/extensions/types.ts:124-275` defines `ExtensionUIContext` with **27 distinct method names (28 signatures including `setWidget` overload)** + **1 readonly property (`theme`) = 28 total members**.

The 27 methods grouped by purpose:

| Group | Methods | Pi line range |
|---|---|---|
| Dialogs (5) | `select`, `confirm`, `input`, `notify`, `editor` | 126-141, 218 |
| Raw input (1) | `onTerminalInput` | 144 |
| Status / working (5) | `setStatus`, `setWorkingMessage`, `setWorkingVisible`, `setWorkingIndicator`, `setHiddenThinkingLabel` | 147-170 |
| Layout (5) | `setWidget` (×2 overloads), `setFooter`, `setHeader`, `setTitle` | 173-198 |
| Custom overlays (1) | `custom<T>` | 201-216 |
| Editor remote control (5) | `pasteToEditor`, `setEditorText`, `getEditorText`, `setEditorComponent`, `getEditorComponent` | 221-262 |
| Autocomplete (1) | `addAutocompleteProvider` | 232 |
| Theme (5+1) | `theme` (readonly property), `getAllThemes`, `getTheme`, `setTheme`, `getToolsExpanded`, `setToolsExpanded` | 265-275 |

Sprint 6h₉a fold-in §B (`d97712c`) already corrected the method count from "25" to "27 methods + 1 readonly property" across ADR-0088, ADR-0094, ADR-0098. The same count is binding here.

### §1.3 — Supporting types Pi defines for `ExtensionUIContext`

From `types.ts:96-122` (the types block immediately preceding `ExtensionUIContext`):

```typescript
export interface ExtensionUIDialogOptions {
    signal?: AbortSignal;
    timeout?: number;
}

export type WidgetPlacement = "aboveEditor" | "belowEditor";

export interface ExtensionWidgetOptions {
    placement?: WidgetPlacement;
}

export type TerminalInputHandler = (data: string) => { consume?: boolean; data?: string } | undefined;

export interface WorkingIndicatorOptions {
    frames?: string[];
    intervalMs?: number;
}

export type AutocompleteProviderFactory = (current: AutocompleteProvider) => AutocompleteProvider;
export type EditorFactory = (tui: TUI, theme: EditorTheme, keybindings: KeybindingsManager) => EditorComponent;
```

From `@earendil-works/pi-tui` (the TUI primitives Pi-tui exports — bundled types in `pi/packages/tui/src/index.ts`):

- `Component` interface (`render(width: number) -> string[]` + `handleInput?(data) -> void` + `invalidate()`)
- `OverlayHandle` (programmatic overlay control)
- `OverlayOptions` (9 anchors + responsive `visible`)
- `OverlayAnchor` ("center", "top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center", "left-center", "right-center")
- `OverlayMargin` (number or `{ top, right, bottom, left }`)
- `SizeValue` (number or percentage string)
- `Theme` (passed in from `theme: Theme` readonly property)
- `EditorTheme` (subset of Theme for editor border styling)
- `EditorComponent` (interface that custom editors implement)
- `KeybindingsManager` (app-level keybindings interface)
- `AutocompleteProvider` interface
- `TUI` (root TUI interface — passed to factories)

### §1.4 — Aelix-additive divergences inherited from ADR-0088 amend

Aelix Sprint 6h₉a locked **prompt-toolkit + Rich + Aelix widget layer** as the TUI stack (NOT pi-tui Python port). Therefore:

- The Aelix `Component` Protocol mirrors Pi's `Component` interface shape (`render(width: int) -> list[str]` + `handle_input(data: str) -> None` + `invalidate()`) but is implemented natively in the Aelix widget layer (Sprint 6h₁₀b) on top of Rich Renderable / prompt-toolkit Float windows.
- The Aelix `Theme` is a Pydantic-style data class (NOT the runtime mutable Pi Theme); theme switching invalidates Rich Live regions.
- The Aelix `EditorComponent` Protocol exposes the prompt-toolkit Buffer API surface needed by extension editor replacement (vim, emacs, etc.).
- The Aelix `KeybindingsManager` Protocol exposes the prompt-toolkit KeyBindings registry.
- The Aelix `TUI` Protocol is a small façade — `request_render()`, `add_input_listener(handler)` — bridging extension factories to the underlying prompt-toolkit Application.
- The Aelix `OverlayHandle` matches Pi semantics (hide/show/focus/unfocus/isFocused/setHidden/isHidden) but is implemented as prompt-toolkit Float lifecycle methods.

Sprint 6h₉c **defines the Protocol surface only**. Concrete implementations land in Sprint 6h₁₀b (Phase 5c-tui).

### §1.5 — Out-of-scope items (defer)

| Item | Owner sprint | Reason |
|---|---|---|
| Concrete `ExtensionUIContext` impl on prompt-toolkit + Rich | Sprint 6h₁₀b | Phase 5c-tui (TUI shell first) |
| `Component` / `Container` / `Box` / `Text` / etc. concrete widget classes | Sprint 6h₁₀b | Phase 5c-tui |
| IME `Focusable` + `CURSOR_MARKER` cursor positioning | Sprint 6h₁₀b | prompt-toolkit native IME handles this |
| Kitty image protocol support (`term-image` integration) | Sprint 6h₁₀c | TBD per ADR-0088 amend Q4 (deferred) |
| Snapshot testing (pyte-based) | Sprint 6h₁₀d | Phase 5c-tui |
| Tier 2 descriptor → Rich Renderable mapping | Sprint 6h₉d | next sprint |
| Tier 4 MCP + hooks | Sprint 6h₉e | Phase 5b-foundation #5 |
| `aelix-server` FastAPI HTTP+WS | Sprint 6h₉f | Phase 5b-foundation #6 |

---

## §2 — Scope

Sprint 6h₉c delivers **six deliverables** in five atomic commits (§4):

| # | Deliverable | Type | Touches |
|---|---|---|---|
| 1 | NEW `ext_ui.py` module — `ExtensionUIContext` Protocol + 10 supporting types | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/ext_ui.py` (NEW) |
| 2 | NEW `widget_protocols.py` module — `Component` / `Container` / `EditorComponent` / `KeybindingsManager` / `AutocompleteProvider` / `TUI` / `Theme` / `EditorTheme` / `OverlayHandle` / `OverlayOptions` Protocols | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/widget_protocols.py` (NEW) |
| 3 | NEW `headless_ui.py` module — `HeadlessExtensionUIContext` stub class implementing every method with `raise NotImplementedError("Sprint 6h₁₀b — prompt-toolkit + Rich impl")` | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/headless_ui.py` (NEW) |
| 4 | `api.py` integration — `ExtensionContext.ui` returns the headless stub by default; type signature changes from `Any` to `ExtensionUIContext`. `has_ui` stays `False` (the stub is "structurally present, semantically deferred"). | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` |
| 5 | Tests — Protocol conformance (HeadlessExtensionUIContext satisfies ExtensionUIContext), every method raises NotImplementedError with expected message, every method's signature matches the spec, theme readonly property works | Tests | `tests/extensions/test_ext_ui_context.py` (NEW) |
| 6 | ADR-0100 closure (NO ADR-0033 amend — it was never created; this sprint clears the phantom code-comment reference) | Docs | `docs/decisions/0100-sprint-6h9c-ext-ui-context-protocol.md` (NEW) |

---

## §3 — Per-deliverable specifications

### §3.1 — `ext_ui.py` — ExtensionUIContext Protocol + supporting types

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/ext_ui.py` (NEW)

**Required structure**:

```python
"""Pi-parity ``ExtensionUIContext`` Protocol + supporting types.

ADR-0100 (Sprint 6h₉c) — actual ExtensionUIContext closure ADR (clears the Sprint 5a phantom "ADR-0033" reference; that ADR was a reserved slot in ``docs/decisions/`` that was never written, hence the 0032 → 0034 numbering jump).

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
from typing import Literal, Protocol, runtime_checkable, overload

# Forward refs to widget-protocol types in widget_protocols.py
from .widget_protocols import (
    AutocompleteProvider,
    Component,
    EditorComponent,
    EditorTheme,
    KeybindingsManager,
    OverlayHandle,
    OverlayOptions,
    TUI,
    Theme,
)

# Type aliases mirroring Pi types.ts:96-122

NotificationKind = Literal["info", "warning", "error"]
WidgetPlacement = Literal["above_editor", "below_editor"]
# Pi camelCase: "aboveEditor"/"belowEditor". Aelix uses snake_case
# (PEP 8); JSON / wire round-trip preserves Aelix snake_case form. The
# Pi-parity divergence is documented in ADR-0100 §"Aelix-additive
# divergences from Pi".


@dataclass(frozen=True)
class ExtensionUIDialogOptions:
    """Pi ``ExtensionUIDialogOptions`` parity (types.ts:96-101).

    AbortSignal: Pi uses ``AbortSignal``; Aelix uses ``asyncio.Event``
    via the optional ``signal`` field (set when called) — caller awaits
    ``signal.wait()`` for cancellation. Same semantic, idiomatic Python.
    """
    signal: object | None = None  # asyncio.Event-like (any object with .wait())
    timeout: float | None = None  # milliseconds in Pi; seconds in Aelix (PEP 8)


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


TerminalInputHandler = Callable[[str], TerminalInputResult | None]
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
    Component | Awaitable[Component],
]


@dataclass(frozen=True)
class CustomOptions:
    """Pi ``custom`` options bag (types.ts:209-216)."""
    overlay: bool = False
    overlay_options: OverlayOptions | Callable[[], OverlayOptions] | None = None
    on_handle: Callable[[OverlayHandle], None] | None = None


# Widget factory — Pi types.ts:177
WidgetFactory = Callable[[TUI, Theme], Component]


# Footer factory — Pi types.ts:184-188
FooterFactory = Callable[
    [TUI, Theme, "ReadonlyFooterDataProvider"],  # ReadonlyFooterDataProvider TBD §3.1.1
    Component,
]


# Header factory — Pi types.ts:194
HeaderFactory = Callable[[TUI, Theme], Component]


# ============================================================================
# ExtensionUIContext Protocol
# ============================================================================


@runtime_checkable
class ExtensionUIContext(Protocol):
    """Pi-parity 27-method + 1 readonly property surface.

    Pi source: ``coding-agent/src/core/extensions/types.ts:124-275`` at
    SHA ``734e08e``.

    Implementations:
      - :class:`HeadlessExtensionUIContext` — Phase 5b default (raises
        NotImplementedError per method).
      - prompt-toolkit + Rich + Aelix widget layer — Phase 5c-tui
        (Sprint 6h₁₀b).
    """

    # === Dialogs (5) ===

    async def select(
        self,
        title: str,
        options: list[str],
        opts: ExtensionUIDialogOptions | None = None,
    ) -> str | None: ...
    """Pi ``select(title, options, opts?)`` (types.ts:126)."""

    async def confirm(
        self,
        title: str,
        message: str,
        opts: ExtensionUIDialogOptions | None = None,
    ) -> bool: ...
    """Pi ``confirm(title, message, opts?)`` (types.ts:129)."""

    async def input(
        self,
        title: str,
        placeholder: str | None = None,
        opts: ExtensionUIDialogOptions | None = None,
    ) -> str | None: ...
    """Pi ``input(title, placeholder?, opts?)`` (types.ts:132)."""

    def notify(self, message: str, kind: NotificationKind = "info") -> None: ...
    """Pi ``notify(message, type?)`` (types.ts:135).

    Pi-parity divergence: Aelix renames ``type`` → ``kind`` to avoid
    shadowing the Python ``type`` built-in. ADR-0100 documents.
    """

    async def editor(
        self,
        title: str,
        prefill: str | None = None,
    ) -> str | None: ...
    """Pi ``editor(title, prefill?)`` — multi-line editor dialog (types.ts:221)."""

    # === Raw input (1) ===

    def on_terminal_input(self, handler: TerminalInputHandler) -> Callable[[], None]: ...
    """Pi ``onTerminalInput(handler)`` (types.ts:144).

    Returns an unsubscribe callable.
    """

    # === Status / working (5) ===

    def set_status(self, key: str, text: str | None) -> None: ...
    """Pi ``setStatus(key, text)`` (types.ts:147). ``text=None`` clears."""

    def set_working_message(self, message: str | None = None) -> None: ...
    """Pi ``setWorkingMessage(message?)`` (types.ts:150). No arg restores default."""

    def set_working_visible(self, visible: bool) -> None: ...
    """Pi ``setWorkingVisible(visible)`` (types.ts:153)."""

    def set_working_indicator(
        self,
        options: WorkingIndicatorOptions | None = None,
    ) -> None: ...
    """Pi ``setWorkingIndicator(options?)`` (types.ts:162).

    No arg restores default spinner. ``frames=[]`` hides; ``frames=["●"]``
    static; custom frames rendered verbatim.
    """

    def set_hidden_thinking_label(self, label: str | None = None) -> None: ...
    """Pi ``setHiddenThinkingLabel(label?)`` (types.ts:165). No arg restores default."""

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
        """Pi ``setWidget(key, content, options?)`` overload (types.ts:173-178)."""
        ...

    def set_footer(self, factory: FooterFactory | None) -> None: ...
    """Pi ``setFooter(factory)`` (types.ts:184). ``factory=None`` restores default."""

    def set_header(self, factory: HeaderFactory | None) -> None: ...
    """Pi ``setHeader(factory)`` (types.ts:194). ``factory=None`` restores default."""

    def set_title(self, title: str) -> None: ...
    """Pi ``setTitle(title)`` — set terminal window/tab title (types.ts:198)."""

    # === Custom overlays (1) ===

    async def custom(
        self,
        factory: CustomComponentFactory,
        options: CustomOptions | None = None,
    ) -> object: ...
    """Pi ``custom<T>(factory, options?)`` — show custom component with focus (types.ts:201).

    Aelix returns ``object`` (untyped); callers should narrow. PEP 695
    Generic[T] could refine this in a future sprint.
    """

    # === Editor remote control (5) ===

    def paste_to_editor(self, text: str) -> None: ...
    """Pi ``pasteToEditor(text)`` (types.ts:221)."""

    def set_editor_text(self, text: str) -> None: ...
    """Pi ``setEditorText(text)`` (types.ts:224)."""

    def get_editor_text(self) -> str: ...
    """Pi ``getEditorText()`` (types.ts:227)."""

    def set_editor_component(self, factory: EditorFactory | None) -> None: ...
    """Pi ``setEditorComponent(factory)`` (types.ts:259). ``factory=None`` restores default."""

    def get_editor_component(self) -> EditorFactory | None: ...
    """Pi ``getEditorComponent()`` (types.ts:262)."""

    # === Autocomplete (1) ===

    def add_autocomplete_provider(
        self,
        factory: AutocompleteProviderFactory,
    ) -> None: ...
    """Pi ``addAutocompleteProvider(factory)`` (types.ts:232)."""

    # === Theme (5 methods + 1 readonly property) ===

    @property
    def theme(self) -> Theme: ...
    """Pi ``readonly theme: Theme`` (types.ts:265)."""

    def get_all_themes(self) -> list[ThemeInfo]: ...
    """Pi ``getAllThemes()`` (types.ts:268)."""

    def get_theme(self, name: str) -> Theme | None: ...
    """Pi ``getTheme(name)`` (types.ts:271)."""

    def set_theme(self, theme: str | Theme) -> SetThemeResult: ...
    """Pi ``setTheme(theme)`` (types.ts:274)."""

    def get_tools_expanded(self) -> bool: ...
    """Pi ``getToolsExpanded()`` (types.ts:277)."""

    def set_tools_expanded(self, expanded: bool) -> None: ...
    """Pi ``setToolsExpanded(expanded)`` (types.ts:280)."""


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
```

#### §3.1.1 — ReadonlyFooterDataProvider forward ref

`FooterFactory` references `ReadonlyFooterDataProvider`. This is Pi's `core/footer-data-provider.ts` runtime object (git branch + extension statuses).

For Sprint 6h₉c: declare a minimal `ReadonlyFooterDataProvider` Protocol in `widget_protocols.py` (§3.2). Concrete implementation lands in Sprint 6h₁₀b alongside the prompt-toolkit + Rich runtime.

### §3.2 — `widget_protocols.py` — TUI primitive Protocols

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/widget_protocols.py` (NEW)

**Required structure**:

```python
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
from dataclasses import dataclass
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
    fg: Callable[[str, str], str] = lambda _role, text: text  # noqa: E731
    # Background color resolver
    bg: Callable[[str, str], str] = lambda _color, text: text  # noqa: E731
    # Style modifiers
    bold: Callable[[str], str] = lambda text: text  # noqa: E731
    italic: Callable[[str], str] = lambda text: text  # noqa: E731


@dataclass(frozen=True)
class EditorTheme:
    """Pi ``EditorTheme`` — subset of Theme for editor border + autocomplete."""

    border: Callable[[str], str] = lambda text: text  # noqa: E731
    border_focused: Callable[[str], str] = lambda text: text  # noqa: E731
    autocomplete_selected: Callable[[str], str] = lambda text: text  # noqa: E731


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
```

### §3.3 — `headless_ui.py` — HeadlessExtensionUIContext stub class

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/headless_ui.py` (NEW)

**Purpose**: Default `ExtensionUIContext` binding when no TUI is attached. Every method raises `NotImplementedError` with a clear "Sprint 6h₁₀b" message so callers know exactly where the missing functionality is owned.

**Required structure**:

```python
"""Headless ExtensionUIContext binding for Phase 5b.

Sprint 6h₉c (ADR-0100) — actual ExtensionUIContext closure ADR (clears the Sprint 5a phantom "ADR-0033" reference; that ADR was a reserved slot never written). This
module supplies the default ``ExtensionUIContext`` binding that returns
from ``ExtensionContext.ui`` when no TUI is attached (e.g., RPC mode,
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

    Implements the :class:`~ext_ui.ExtensionUIContext` Protocol
    structurally (``runtime_checkable``). Instances are immutable;
    callers may freely cache the single :data:`HEADLESS_UI_CONTEXT`
    module-level singleton.
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
        raise _raise_headless("theme")

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
"""Singleton headless instance — return from :meth:`ExtensionContext.ui` by default."""


__all__ = [
    "HEADLESS_UI_CONTEXT",
    "HeadlessExtensionUIContext",
]
```

### §3.4 — `api.py` integration

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`

**Action**: replace the current `ui` property (line 744-758) to return the typed headless singleton.

**Before** (current):

```python
@property
def ui(self) -> Any:
    """Pi ``ui: ExtensionUIContext`` — deferred to ADR-0033 (Phase 5 TUI).
    ...
    """
    raise ExtensionError(
        "invalid_state",
        "ExtensionContext.ui is deferred to ADR-0033 (Phase 5 TUI); "
        "guard access with `if ctx.has_ui:`.",
    )
```

**After** (Sprint 6h₉c):

```python
@property
def ui(self) -> ExtensionUIContext:
    """Pi ``ui: ExtensionUIContext`` — ADR-0100 (Sprint 6h₉c) closure.

    (Sprint 5a code comments cited a phantom "ADR-0033" reserved slot
    that was never written; ADR-0100 is the actual closure.)

    Returns the headless singleton (``HEADLESS_UI_CONTEXT``) by default,
    which raises :exc:`NotImplementedError` per method call with a
    pointer to Sprint 6h₁₀b (Phase 5c-tui). When an AgentHarness binds a
    real TUI via its bridge wiring (Sprint 6h₁₀b), a concrete
    ``ExtensionUIContext`` implementation replaces the headless
    singleton via ``_ExtensionRuntime.bind_ui()`` (new in this sprint).

    ``has_ui`` remains ``False`` until the bridge wiring lands — the
    headless binding is "structurally present, semantically deferred"
    so static type checkers see the right surface but runtime calls fail
    fast with actionable errors.
    """
    runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
    return runtime.ui
```

**Add to `_ExtensionRuntime`** (around line 360+, after existing fields):

```python
class _ExtensionRuntime:
    # ... existing fields ...

    # Sprint 6h₉c (ADR-0100) — ExtensionUIContext binding.
    # Default: HEADLESS_UI_CONTEXT singleton (raises per method).
    # Sprint 6h₁₀b: replace via bind_ui() with concrete prompt-toolkit +
    # Rich + Aelix widget layer impl.
    _ui: ExtensionUIContext = field(default_factory=lambda: HEADLESS_UI_CONTEXT)

    @property
    def ui(self) -> ExtensionUIContext:
        return self._ui

    def bind_ui(self, ui: ExtensionUIContext) -> None:
        """Replace the headless UI with a concrete binding (Sprint 6h₁₀b)."""
        self._ui = ui
```

**Import addition** (top of api.py):

```python
from .ext_ui import ExtensionUIContext
from .headless_ui import HEADLESS_UI_CONTEXT
```

**`has_ui` stays `False`**: The headless binding doesn't count as "having UI" — the contract is "is there an interactive TUI to render to". `has_ui` flips to `True` only when `bind_ui()` is called with a non-headless binding (Sprint 6h₁₀b). Document this in the `has_ui` docstring update:

```python
@property
def has_ui(self) -> bool:
    """Pi ``hasUI`` — True only when a concrete (non-headless) TUI is bound.

    Sprint 6h₉c (ADR-0100) clarifies: ``has_ui`` reflects whether a
    concrete UI binding has been installed via ``_ExtensionRuntime.
    bind_ui()`` (Sprint 6h₁₀b). The headless default does NOT flip this
    to True — extensions should guard ``ctx.ui.*`` calls with
    ``if ctx.has_ui:`` to avoid the NotImplementedError raised by the
    headless binding in Phase 5b.

    Once Sprint 6h₁₀b lands the concrete binding via AgentHarness
    bridge wiring, ``has_ui`` flips to ``True`` and ``ctx.ui.*`` calls
    succeed.

    (Sprint 5a code comments cited "ADR-0033" as the placeholder owner;
    that ADR was a reserved slot never written, replaced by ADR-0100
    in Sprint 6h₉c.)
    """
    runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
    return runtime.ui is not HEADLESS_UI_CONTEXT
```

### §3.5 — Tests

**Location**: `tests/extensions/test_ext_ui_context.py` (NEW)

**Required test coverage**:

| # | Test | Assertion |
|---|---|---|
| 1 | `test_protocol_runtime_checkable` | `isinstance(HEADLESS_UI_CONTEXT, ExtensionUIContext) is True` |
| 2 | `test_protocol_has_27_methods_plus_theme` | enumerate ExtensionUIContext non-dunder attributes; assert count == 28 (27 methods + theme property) |
| 3 | `test_headless_select_raises` | `await headless.select(...)` raises `NotImplementedError` with `"select"` + `"Sprint 6h₁₀b"` in message |
| 4-30 | One test per remaining method (27 methods + theme property) | Same pattern as #3 |
| 31 | `test_ctx_ui_returns_headless_by_default` | `ctx.ui is HEADLESS_UI_CONTEXT` |
| 32 | `test_has_ui_false_for_headless` | `ctx.has_ui is False` |
| 33 | `test_bind_ui_flips_has_ui_to_true` | construct dummy concrete impl, call `runtime.bind_ui(concrete)`, assert `ctx.has_ui is True` |
| 34 | `test_unbind_ui_back_to_headless` | call `runtime.bind_ui(HEADLESS_UI_CONTEXT)`, assert `ctx.has_ui is False` |
| 35 | `test_theme_property_raises_for_headless` | accessing `headless.theme` raises (property semantics) |
| 36 | `test_set_widget_overload_signature_string_array` | static signature check via `inspect.signature` (parameters present) |
| 37 | `test_set_widget_overload_signature_factory` | (second overload via the runtime body) |
| 38 | `test_extension_uicontext_imports_publicly` | `from aelix_coding_agent.extensions import ExtensionUIContext, HEADLESS_UI_CONTEXT` works |
| 39 | `test_widget_protocols_importable` | smoke imports for Theme/Component/Container/TUI/OverlayOptions/etc. |
| 40 | `test_overlay_options_anchor_literal_values` | construct each of 9 anchors; assert no validation error |
| 41 | `test_overlay_margin_all_factory` | `OverlayMargin.all(2)` returns equal sides |

Target: ~40-45 tests, ~400-500 LOC. Each test minimal (single assertion). Use `pytest.raises(NotImplementedError, match="...")`.

### §3.6 — ADR-0100 closure (ADR-0033 phantom reference cleanup)

#### ADR-0100 NEW

**Location**: `docs/decisions/0100-sprint-6h9c-ext-ui-context-protocol.md`

**Mandatory front-matter** (per ADR-0093 template):

```
# 0100. Sprint 6h₉c — ExtensionUIContext Protocol (Pi-parity 27-method surface)

Status: Accepted (Sprint 6h₉c / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**
```

**Required sections**:

1. `## Context` — Sprint 6h₉c writes the actual ExtensionUIContext closure ADR. Existing `ExtensionContext.ui` raised `ExtensionError("invalid_state")` and cited a phantom "ADR-0033" (a reserved slot that was never written — `docs/decisions/` jumps 0032 → 0034). This sprint clears the phantom citation and replaces the placeholder with a typed Protocol + headless stub binding.

2. `## Decision` — Six deliverables enumerated per §2.

3. `## Surface inventory` — Table of all 27 methods + 1 readonly property with Pi line citations (use the table from §1.2 of this spec; verified at SHA `734e08e`).

4. `## Aelix-additive divergences from Pi` (per spec §10):

| # | Divergence | Pi behavior | Aelix-additive behavior | Justification |
|---|---|---|---|---|
| 1 | `notify(type)` parameter renamed to `kind` | Pi uses `type` (TS keyword OK) | Aelix uses `kind` (shadow `type` built-in avoided) | PEP 8 — `type` is Python built-in |
| 2 | `WidgetPlacement` values snake_case | Pi: `"aboveEditor"`/`"belowEditor"` | Aelix: `"above_editor"`/`"below_editor"` | PEP 8 — JSON wire round-trip preserves snake_case |
| 3 | `WorkingIndicatorOptions.interval_ms` snake_case | Pi: `intervalMs` | Aelix: `interval_ms` | PEP 8 |
| 4 | `ExtensionUIDialogOptions.signal` is `object`-like (asyncio.Event), not `AbortSignal` | Pi: `AbortSignal` | Aelix: any object with `.wait()` coroutine — idiomatic Python | asyncio.Event is Python's AbortSignal analogue |
| 5 | `ExtensionUIDialogOptions.timeout` in **seconds** (float), not milliseconds | Pi: ms (int) | Aelix: seconds (float) | PEP 8 — asyncio idiom |
| 6 | `HeadlessExtensionUIContext` default binding | Pi: per-mode provider (interactive/print/rpc) directly | Aelix: headless default + concrete binding via `bind_ui()` (Sprint 6h₁₀b) | Phase 5b/5c separation — Phase 5b ships surface, Phase 5c-tui ships impl |
| 7 | `set_widget` overload via Python `@overload` | Pi: TS overload (function declaration set) | Aelix: `typing.overload` (PEP 484) | Python equivalent |
| 8 | `TerminalInputHandler` return shape is `TerminalInputResult` dataclass | Pi: anonymous object literal | Aelix: typed `TerminalInputResult` dataclass | Python lacks anonymous object types |
| 9 | `custom<T>()` returns `object` instead of generic `T` | Pi: `Promise<T>` | Aelix: `object` (caller narrows) | PEP 695 generic Protocol future sprint |

5. `## Deferred items (Phase 5c-tui carry-forward)` — same as spec §1.5.

6. `## Pi citations` (SHA `734e08e`):
   - `packages/coding-agent/src/core/extensions/types.ts:124-275` — ExtensionUIContext
   - `packages/coding-agent/src/core/extensions/types.ts:96-122` — Supporting types
   - `packages/tui/src/tui.ts` — Component / Container / Focusable / OverlayOptions / OverlayHandle (line ranges TBD via fold-in if W5 critic verifies)
   - `packages/tui/src/index.ts` — exports list

7. `## Reference companions`:
   - ADR-0033 — Phantom (does NOT exist; was a reserved slot in `docs/decisions/` between 0032 and 0034 that was never written). Sprint 5a `api.py` docstrings cited it as the placeholder owner; ADR-0100 is the actual closure that clears the phantom reference.
   - ADR-0088 amend — TUI library choice (prompt-toolkit + Rich)
   - ADR-0094 — 4-tier extension architecture (Tier 1)
   - ADR-0096 — Manifest v1 (capabilities `ui_tui_trusted` flag)
   - ADR-0099 — Sprint 6h₉b manifest loader (manifest propagation enables Tier 1)

8. `## Verification` — Reference §5 of this spec.

9. `## Phase` — "Sprint 6h₉c / Phase 5b-foundation (shipped). Next sprint: 6h₉d — Tier 2 descriptor renderer."

#### ADR-0033 NOT created / NOT amended

ADR-0033 does **not exist** in `docs/decisions/` (the numbering jumps 0032 → 0034 — reserved slot never written). Sprint 6h₉c does NOT create a backfill ADR-0033; the phantom citation is cleared only in code comments (api.py docstrings) by replacing "ADR-0033" with "ADR-0100" inline. ADR-0100 §"Context" notes this housekeeping so future readers don't search for a non-existent file.

---

## §4 — Commit split plan (W6, 5 atomic commits)

### Commit 1 (§A) — widget_protocols.py

**Stage**:
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/widget_protocols.py` (NEW)

**Commit message** (HEREDOC):

```
feat(extensions): Aelix TUI primitive Protocols (Sprint 6h₉c §A)

Library-agnostic Protocols for TUI primitives that ExtensionUIContext
references: Component, Container, Focusable + CURSOR_MARKER, Theme,
EditorTheme, EditorComponent, KeybindingsManager, TUI, Autocomplete-
Provider, OverlayHandle, OverlayOptions (9 anchors + responsive
visibility), OverlayMargin, ReadonlyFooterDataProvider.

Concrete impl land in Sprint 6h₁₀b on prompt-toolkit + Rich + Aelix
widget layer per ADR-0088 amend (CRITICAL invariant: library-agnostic
Component Protocol preserved).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 2 (§B) — ext_ui.py (Protocol + supporting types)

**Stage**:
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/ext_ui.py` (NEW)

**Commit message** (HEREDOC):

```
feat(extensions): ExtensionUIContext Protocol (Sprint 6h₉c §B)

Pi-parity 27-method surface + 1 readonly theme property = 28 members
matching Pi types.ts:124-275 at SHA 734e08e (verified Sprint 6h₉a
fold-in §B).

Methods grouped per Pi:
- Dialogs (5): select / confirm / input / notify / editor
- Raw input (1): on_terminal_input
- Status / working (5): set_status / set_working_message /
  set_working_visible / set_working_indicator /
  set_hidden_thinking_label
- Layout (5): set_widget (×2 overloads) / set_footer / set_header /
  set_title
- Custom overlays (1): custom
- Editor remote control (5): paste_to_editor / set_editor_text /
  get_editor_text / set_editor_component / get_editor_component
- Autocomplete (1): add_autocomplete_provider
- Theme (5 + 1 prop): theme readonly / get_all_themes / get_theme /
  set_theme / get_tools_expanded / set_tools_expanded

Supporting types (Pi types.ts:96-122 parity): ExtensionUIDialogOptions,
ExtensionWidgetOptions, WidgetPlacement, TerminalInputHandler +
TerminalInputResult, WorkingIndicatorOptions, AutocompleteProvider-
Factory, EditorFactory, ThemeInfo, SetThemeResult, CustomComponent-
Factory + CustomOptions, WidgetFactory, FooterFactory, HeaderFactory.

Aelix-additive divergences documented in ADR-0100 (Sprint 6h₉c §F):
notify(kind=) rename, snake_case enums, timeout in seconds,
asyncio.Event for signal, typed TerminalInputResult, untyped custom
return (PEP 695 future).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 3 (§C) — headless_ui.py + api.py integration

**Stage**:
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/headless_ui.py` (NEW)
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`

**Commit message** (HEREDOC):

```
feat(extensions): HeadlessExtensionUIContext + ctx.ui binding (Sprint 6h₉c §C)

HeadlessExtensionUIContext: default ExtensionUIContext binding for
Phase 5b. Every method raises NotImplementedError with a clear
"Sprint 6h₁₀b" pointer so callers see immediate actionable errors
instead of silent no-ops or AttributeErrors.

ExtensionContext.ui (formerly Any + ExtensionError stub) now returns
the typed ExtensionUIContext (HEADLESS_UI_CONTEXT singleton by
default). has_ui stays False until _ExtensionRuntime.bind_ui()
replaces the headless binding with a concrete prompt-toolkit + Rich
impl (Sprint 6h₁₀b).

_ExtensionRuntime gains _ui field + bind_ui() method. Closes the
ADR-0033 phantom citation (cleared from api.py docstrings — ADR-0033 was a reserved slot in docs/decisions/ that was never written; ADR-0100 is the actual ExtensionUIContext closure).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 4 (§D) — Tests

**Stage**:
- `tests/extensions/test_ext_ui_context.py` (NEW)

**Commit message** (HEREDOC):

```
test(extensions): ExtensionUIContext Protocol conformance (Sprint 6h₉c §D)

~40 tests covering:
- Protocol runtime_checkable conformance (HeadlessExtensionUIContext
  satisfies ExtensionUIContext)
- 27 method + 1 theme property surface inventory check
- Headless raises NotImplementedError per method with expected
  message format ("Sprint 6h₁₀b" pointer)
- ExtensionContext.ui returns HEADLESS_UI_CONTEXT by default
- has_ui False for headless / True after bind_ui(concrete)
- Theme readonly property raises (property semantics)
- set_widget @overload signature inventory
- Public re-exports from aelix_coding_agent.extensions
- widget_protocols smoke imports
- OverlayOptions 9 anchor Literal validation
- OverlayMargin.all() factory

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Commit 5 (§E + §F) — ADR-0100 closure

**Stage**:
- `docs/decisions/0100-sprint-6h9c-ext-ui-context-protocol.md` (NEW)

**Note**: ADR-0033 does NOT exist (reserved slot, never written). Sprint 6h₉c does NOT backfill it; the phantom reference is cleared inline in `api.py` docstrings (commit 3 §C).

**Commit message** (HEREDOC):

```
docs: ADR-0100 ExtensionUIContext Protocol closure (Sprint 6h₉c §E)

ADR-0100 documents the 27-method + 1 readonly theme property surface
locked in Sprint 6h₉c. Includes:
- Surface inventory table with Pi line citations (types.ts:124-275)
- 9 Aelix-additive divergences (PEP 8 renames, snake_case, asyncio
  idioms, typed TerminalInputResult, headless binding pattern)
- Deferred items mapped to Sprint 6h₁₀b (Phase 5c-tui)
- ADR cross-references (0088 amend / 0094 / 0096 / 0099 reference
  companions)
- Clears Sprint 5a phantom "ADR-0033" citation (reserved slot never
  written); ADR-0100 is the actual ExtensionUIContext closure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §5 — Verification plan (W3-W5)

### W3

```sh
uv run ruff check 2>&1 | tail -3
uv run pyright 2>&1 | tail -5  # MUST show 8 baseline (zero new)
uv run pytest 2>&1 | tail -5   # baseline 2432 + ~40 new = ~2472
python scripts/generate_contracts_schemas.py --check  # exit 0
```

Expected:
- ruff: clean
- pyright: 8 baseline preserved
- pytest: 2432 baseline + ~40 new = ~2472 passed + 1 skipped
- schema --check: exit 0 (contracts package untouched)

### W4 (code-reviewer)

Focus:
- Protocol surface completeness (27 methods + theme property)
- `runtime_checkable` actually satisfied by HeadlessExtensionUIContext
- Method signatures match Pi types.ts (parameter names, defaults, return types)
- NotImplementedError messages contain "Sprint 6h₁₀b" pointer consistently
- `_ExtensionRuntime.bind_ui()` semantics (replace binding, no-op if same)
- `has_ui` reflects bound state correctly (False for headless, True for concrete)
- ADR-0100 format compliance (no ADR-0033 file is created or amended — it was always a phantom reference)
- Cross-package import direction (no circular)

### W5 (critic)

Focus:
- Pi citation accuracy (re-verify types.ts:124-275 line ranges)
- Aelix-additive divergence Pi-behavior descriptions accurate
- Locked decisions (D5 4-tier, D6 27-method, ADR-0088 library-agnostic Component) honored
- ADR-0100 supersession of ADR-0033 properly recorded
- Sprint commit boundary integrity (§A/§B/§C/§D/§E+§F atomic)
- `HeadlessExtensionUIContext` structural conformance with ExtensionUIContext (not just declaration — actual Protocol satisfaction)

### W6 — see §4

---

## §6 — User-imposed constraints (BINDING)

Identical to Sprint 6h₉a/6h₉b §6.

---

## §7 — Pi citation map

| Citation | Use site |
|---|---|
| `coding-agent/src/core/extensions/types.ts:124-275` | ExtensionUIContext 27-method + theme surface (Sprint 6h₉a fold-in §B verified) |
| `coding-agent/src/core/extensions/types.ts:96-122` | Supporting types (DialogOptions, WidgetOptions, TerminalInputHandler, WorkingIndicatorOptions, AutocompleteProviderFactory, EditorFactory) |
| `pi/packages/tui/src/index.ts` | TUI primitive exports (Component, Container, Focusable, CURSOR_MARKER, Theme, EditorTheme, etc.) — line ranges to verify at W5 |
| `pi/packages/tui/src/tui.ts` | Component interface definition + OverlayOptions + OverlayHandle |

External (non-Pi):
- PEP 484 `typing.overload` — Python overload pattern
- PEP 695 generic Protocols — future direction for `custom<T>()` typing
- ADR-0088 amend — prompt-toolkit + Rich + Aelix widget layer (library choice)

---

## §8 — Definition of Done

- [ ] All 5 commits landed (per §4, atomic, HEREDOC, Co-Authored-By)
- [ ] `uv run ruff check` clean
- [ ] `uv run pyright` 8 baseline (zero new)
- [ ] `uv run pytest` baseline + ~40 new tests pass
- [ ] `python scripts/generate_contracts_schemas.py --check` exit 0
- [ ] `ExtensionUIContext` Protocol defined with exactly 27 methods + 1 readonly `theme` property
- [ ] `HeadlessExtensionUIContext` structurally satisfies `ExtensionUIContext` (`isinstance` check)
- [ ] Every headless method raises `NotImplementedError` with "Sprint 6h₁₀b" pointer
- [ ] `ExtensionContext.ui` returns `ExtensionUIContext` (typed) — no more `Any`
- [ ] `has_ui` reflects bound state (False for headless default)
- [ ] ADR-0100 follows ADR-0093 format with 9 Aelix-additive divergences
- [ ] ADR-0033 Status: Superseded by ADR-0100
- [ ] No staged `.omc/project-memory.json` or temp files
- [ ] No push (user pushes manually)

---

## §9 — Glossary

| Term | Definition |
|---|---|
| `ExtensionUIContext` | Pi-parity 27-method + 1 readonly property Protocol for extension UI access |
| `HeadlessExtensionUIContext` | Aelix-additive default binding that raises NotImplementedError per method |
| `HEADLESS_UI_CONTEXT` | Module-level singleton instance of `HeadlessExtensionUIContext` |
| `bind_ui()` | `_ExtensionRuntime` method to replace the headless binding with a concrete impl (Sprint 6h₁₀b) |
| `Component` | Aelix Protocol mirroring Pi-tui's Component interface (`render(width) -> list[str]`) |
| `Focusable` | IME / cursor positioning marker Protocol (CJK input support) |
| `CURSOR_MARKER` | Pi APC escape sequence for cursor positioning |

---

## §10 — Aelix-additive divergences summary (also in ADR-0100)

(Verbatim from §3.6 #4 table.)

---

## §11 — Cross-ADR consistency check

| ADR | Cites in this sprint | Cited by 0100 |
|---|---|---|
| 0033 (phantom — does NOT exist) | Cleared from `api.py` docstrings (Sprint 5a phantom citation; reserved slot never written) | ✓ (§"Context" notes housekeeping) |
| 0088 amend (TUI library) | Library-agnostic Component invariant referenced | ✓ |
| 0094 (4-tier model) | Tier 1 trusted Python — this is its impl surface | ✓ |
| 0096 (manifest v1) | `capabilities.ui_tui_trusted` flag — Tier 1 marker | ✓ |
| 0098 (Sprint 6h₉a closure) | Contracts shipped + verified method count | ✓ |
| 0099 (Sprint 6h₉b closure) | Manifest propagation enables Tier 1 detection | ✓ |
| 0100 (this sprint closure) | self | self |

---

## §12 — End of spec

Spec author: Main agent (W0+W1, Sprint 6h₉c)
Spec status: Binding (do not modify)
Spec scope: Sprint 6h₉c only

W2 executor: read this spec, then execute commits 1-5 per §4 in order. Verify per §5. Honor §6 constraints. Output a sprint summary at W2 completion citing each commit SHA + verification evidence per §8 DoD.
