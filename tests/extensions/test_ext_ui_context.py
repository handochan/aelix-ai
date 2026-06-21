"""Sprint 6h₉c — ExtensionUIContext Protocol conformance tests.

Covers the test matrix from Sprint 6h₉c spec §3.5:

1.  Protocol runtime_checkable conformance (HeadlessExtensionUIContext
    satisfies ExtensionUIContext).
2.  Surface inventory: 27 methods + 1 readonly `theme` property = 28 members.
3-30. Per-method: headless raises NotImplementedError with the expected
    method name and "Sprint 6h₁₀b" pointer in the message.
31. ctx.ui returns HEADLESS_UI_CONTEXT by default.
32. has_ui is False for the headless default.
33. bind_ui(concrete) flips has_ui to True.
34. bind_ui(HEADLESS_UI_CONTEXT) reverts has_ui to False.
35. theme readonly property raises (property semantics).
36-37. set_widget overload signature inventory.
38. Public re-exports from aelix_coding_agent.extensions.
39. widget_protocols importable.
40. OverlayOptions 9 anchor Literal values.
41. OverlayMargin.all() factory.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from aelix_coding_agent.extensions import (
    HEADLESS_UI_CONTEXT,
    ExtensionContext,
    ExtensionUIContext,
    HeadlessExtensionUIContext,
    _ExtensionRuntime,
)
from aelix_coding_agent.extensions.ext_ui import (
    ExtensionUIDialogOptions,
    ExtensionWidgetOptions,
    WorkingIndicatorOptions,
)
from aelix_coding_agent.extensions.widget_protocols import (
    TUI,
    AutocompleteProvider,
    Component,
    Container,
    EditorComponent,
    EditorTheme,
    KeybindingsManager,
    OverlayHandle,
    OverlayMargin,
    OverlayOptions,
    ReadonlyFooterDataProvider,
    Theme,
)

# === Fixtures / helpers ===

EXPECTED_MSG_FRAGMENT = "Sprint 6h₁₀b"


def _make_ctx(runtime: _ExtensionRuntime | None = None) -> ExtensionContext:
    runtime = runtime or _ExtensionRuntime()
    return ExtensionContext(
        runtime,
        cwd="/tmp",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )


# === Test 1 — Protocol runtime_checkable conformance ===


def test_protocol_runtime_checkable() -> None:
    """HEADLESS_UI_CONTEXT must structurally satisfy ExtensionUIContext."""

    assert isinstance(HEADLESS_UI_CONTEXT, ExtensionUIContext)


# === Test 2 — Surface inventory: 27 methods + 1 readonly property = 28 ===


def test_protocol_has_27_methods_plus_theme() -> None:
    """ExtensionUIContext exposes exactly 28 public members (27 methods + theme prop)."""

    members = [name for name in dir(ExtensionUIContext) if not name.startswith("_")]
    assert len(members) == 28, f"expected 28 members, got {len(members)}: {sorted(members)}"


# === Tests 3-7 — Dialogs (5) ===


def test_headless_select_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"select.*{EXPECTED_MSG_FRAGMENT}"):
        asyncio.run(HEADLESS_UI_CONTEXT.select("t", ["a", "b"]))


def test_headless_confirm_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"confirm.*{EXPECTED_MSG_FRAGMENT}"):
        asyncio.run(HEADLESS_UI_CONTEXT.confirm("t", "m"))


def test_headless_input_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"input.*{EXPECTED_MSG_FRAGMENT}"):
        asyncio.run(HEADLESS_UI_CONTEXT.input("t"))


def test_headless_notify_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"notify.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.notify("hello")


def test_headless_editor_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"editor.*{EXPECTED_MSG_FRAGMENT}"):
        asyncio.run(HEADLESS_UI_CONTEXT.editor("title"))


# === Test 8 — Raw input (1) ===


def test_headless_on_terminal_input_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"on_terminal_input.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.on_terminal_input(lambda _data: None)


# === Tests 9-13 — Status / working (5) ===


def test_headless_set_status_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"set_status.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.set_status("key", "text")


def test_headless_set_working_message_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"set_working_message.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.set_working_message("msg")


def test_headless_set_working_visible_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"set_working_visible.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.set_working_visible(True)


def test_headless_set_working_indicator_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"set_working_indicator.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.set_working_indicator(WorkingIndicatorOptions())


def test_headless_set_hidden_thinking_label_raises() -> None:
    with pytest.raises(
        NotImplementedError,
        match=f"set_hidden_thinking_label.*{EXPECTED_MSG_FRAGMENT}",
    ):
        HEADLESS_UI_CONTEXT.set_hidden_thinking_label("label")


# === Tests 14-18 — Layout (5; set_widget exercises both overload bodies) ===


def test_headless_set_widget_string_array_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"set_widget.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.set_widget("k", ["line"], ExtensionWidgetOptions())


def test_headless_set_widget_factory_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"set_widget.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.set_widget("k", lambda _tui, _theme: None)  # type: ignore[arg-type]


def test_headless_set_footer_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"set_footer.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.set_footer(None)


def test_headless_set_header_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"set_header.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.set_header(None)


def test_headless_set_title_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"set_title.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.set_title("t")


# === Test 19 — Custom overlays (1) ===


def test_headless_custom_raises() -> None:
    async def _factory(*_args: object) -> object:  # pragma: no cover - never invoked
        return None

    with pytest.raises(NotImplementedError, match=f"custom.*{EXPECTED_MSG_FRAGMENT}"):
        asyncio.run(HEADLESS_UI_CONTEXT.custom(_factory))  # type: ignore[arg-type]


# === Tests 20-24 — Editor remote control (5) ===


def test_headless_paste_to_editor_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"paste_to_editor.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.paste_to_editor("text")


def test_headless_set_editor_text_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"set_editor_text.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.set_editor_text("text")


def test_headless_get_editor_text_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"get_editor_text.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.get_editor_text()


def test_headless_set_editor_component_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"set_editor_component.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.set_editor_component(None)


def test_headless_get_editor_component_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"get_editor_component.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.get_editor_component()


# === Test 25 — Autocomplete (1) ===


def test_headless_add_autocomplete_provider_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"add_autocomplete_provider.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.add_autocomplete_provider(lambda current: current)


# === Tests 26-30 — Theme (5 + 1 property) ===


def test_headless_theme_property_returns_default() -> None:
    # ``theme`` is the protocol's only *property* member, so an
    # ``isinstance(ctx, ExtensionUIContext)`` check under ``@runtime_checkable``
    # INVOKES its getter on Python 3.11 (3.11 probes data members via
    # ``hasattr``; 3.12 uses ``getattr_static`` and does not). It must therefore
    # return a value rather than raise — a no-op default Theme. The callable
    # theme methods (``get_theme`` / ``set_theme`` / ``get_all_themes``) still
    # raise when invoked.
    from aelix_coding_agent.extensions.widget_protocols import Theme

    assert isinstance(HEADLESS_UI_CONTEXT.theme, Theme)


def test_headless_get_all_themes_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"get_all_themes.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.get_all_themes()


def test_headless_get_theme_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"get_theme.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.get_theme("name")


def test_headless_set_theme_raises() -> None:
    with pytest.raises(NotImplementedError, match=f"set_theme.*{EXPECTED_MSG_FRAGMENT}"):
        HEADLESS_UI_CONTEXT.set_theme("name")


def test_headless_get_tools_expanded_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"get_tools_expanded.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.get_tools_expanded()


def test_headless_set_tools_expanded_raises() -> None:
    with pytest.raises(
        NotImplementedError, match=f"set_tools_expanded.*{EXPECTED_MSG_FRAGMENT}"
    ):
        HEADLESS_UI_CONTEXT.set_tools_expanded(True)


# === Tests 31-34 — ExtensionContext.ui + bind_ui semantics ===


def test_ctx_ui_returns_headless_by_default() -> None:
    """A fresh ExtensionContext exposes HEADLESS_UI_CONTEXT as ctx.ui."""

    ctx = _make_ctx()
    assert ctx.ui is HEADLESS_UI_CONTEXT


def test_has_ui_false_for_headless() -> None:
    """has_ui is False when the headless default is bound."""

    ctx = _make_ctx()
    assert ctx.has_ui is False


def test_bind_ui_flips_has_ui_to_true() -> None:
    """Binding a concrete (non-headless) impl flips has_ui to True."""

    runtime = _ExtensionRuntime()
    ctx = _make_ctx(runtime)

    class _Concrete:
        pass

    runtime.bind_ui(_Concrete())  # type: ignore[arg-type]
    assert ctx.has_ui is True


def test_unbind_ui_back_to_headless() -> None:
    """Rebinding to HEADLESS_UI_CONTEXT reverts has_ui to False."""

    runtime = _ExtensionRuntime()
    ctx = _make_ctx(runtime)

    class _Concrete:
        pass

    runtime.bind_ui(_Concrete())  # type: ignore[arg-type]
    assert ctx.has_ui is True
    runtime.bind_ui(HEADLESS_UI_CONTEXT)
    assert ctx.has_ui is False


# === Tests 35 — Theme readonly property semantics ===


def test_theme_property_does_not_raise_for_headless() -> None:
    """Accessing `.theme` on the headless returns a default Theme and must NOT
    raise — it is invoked by ``isinstance()`` under ``@runtime_checkable`` on
    Python 3.11, where raising would break structural conformance."""

    from aelix_coding_agent.extensions.widget_protocols import Theme

    assert isinstance(HEADLESS_UI_CONTEXT.theme, Theme)


# === Tests 36-37 — set_widget overload signature inventory ===


def test_set_widget_overload_signature_present() -> None:
    """HeadlessExtensionUIContext.set_widget exposes the documented parameter list."""

    sig = inspect.signature(HeadlessExtensionUIContext.set_widget)
    params = list(sig.parameters)
    assert params == ["self", "key", "content", "options"], params


def test_set_widget_overload_content_default_options() -> None:
    """The `options` parameter defaults to None per Pi parity."""

    sig = inspect.signature(HeadlessExtensionUIContext.set_widget)
    assert sig.parameters["options"].default is None


# === Test 38 — Public re-exports ===


def test_extension_uicontext_imports_publicly() -> None:
    """Top-level package re-exports ExtensionUIContext + HEADLESS_UI_CONTEXT."""

    from aelix_coding_agent.extensions import (
        HEADLESS_UI_CONTEXT as _ctx,
    )
    from aelix_coding_agent.extensions import (
        ExtensionUIContext as _proto,
    )
    from aelix_coding_agent.extensions import (
        HeadlessExtensionUIContext as _headless,
    )

    assert isinstance(_ctx, _headless)
    assert isinstance(_ctx, _proto)


# === Test 39 — widget_protocols smoke imports ===


def test_widget_protocols_importable() -> None:
    """widget_protocols re-exports the documented Protocols / dataclasses."""

    # Smoke: the imports at the top of this module succeeded; assert
    # each symbol resolved to a non-None object so the test makes the
    # import path explicit per spec §3.5 #39.
    for symbol in (
        AutocompleteProvider,
        Component,
        Container,
        EditorComponent,
        EditorTheme,
        KeybindingsManager,
        OverlayHandle,
        OverlayMargin,
        OverlayOptions,
        ReadonlyFooterDataProvider,
        Theme,
        TUI,
    ):
        assert symbol is not None


# === Test 40 — OverlayOptions 9 anchor Literal values ===


def test_overlay_options_anchor_literal_values() -> None:
    """Each of Pi's 9 OverlayAnchor values constructs without error."""

    for anchor in (
        "center",
        "top-left",
        "top-right",
        "bottom-left",
        "bottom-right",
        "top-center",
        "bottom-center",
        "left-center",
        "right-center",
    ):
        opts = OverlayOptions(anchor=anchor)  # type: ignore[arg-type]
        assert opts.anchor == anchor


# === Test 41 — OverlayMargin.all() factory ===


def test_overlay_margin_all_factory() -> None:
    """OverlayMargin.all(n) sets every side to n."""

    m = OverlayMargin.all(3)
    assert m.top == 3
    assert m.right == 3
    assert m.bottom == 3
    assert m.left == 3


# === Bonus — ExtensionUIDialogOptions dataclass smoke ===


def test_extension_ui_dialog_options_defaults() -> None:
    """ExtensionUIDialogOptions defaults to (None, None) per Pi types.ts:96-101."""

    opts = ExtensionUIDialogOptions()
    assert opts.signal is None
    assert opts.timeout is None
