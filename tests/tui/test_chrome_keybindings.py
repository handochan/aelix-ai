"""shift+tab permission-cycle key binding tests (WP-0, ADR-0157).

Verifies ``s-tab`` (prompt-toolkit's name for the shift+tab / backtab CSI Z
sequence) fires ``on_permission_cycle`` when set, is a no-op when None, and that
the binding set does not collide with the existing bindings.
"""

from __future__ import annotations

import io

from aelix_coding_agent.tui.chrome import AelixChrome
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


def _make_chrome() -> AelixChrome:
    console = Console(file=io.StringIO(), force_terminal=True, width=80)
    return AelixChrome(console=console)


def _bindings_for(chrome: AelixChrome, key: str) -> list[object]:
    kb = chrome.app.key_bindings
    assert kb is not None
    out: list[object] = []
    for binding in kb.bindings:
        keys = tuple(getattr(k, "value", str(k)) for k in binding.keys)
        if keys == (key,):
            out.append(binding)
    return out


async def test_s_tab_fires_permission_cycle() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        calls: list[int] = []
        chrome.on_permission_cycle = lambda: calls.append(1)

        bindings = _bindings_for(chrome, "s-tab")
        assert bindings, "no binding for s-tab"
        for binding in bindings:
            binding.handler(None)  # type: ignore[arg-type]
        assert calls == [1]


async def test_permission_cycle_noop_when_unset() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        assert chrome.on_permission_cycle is None
        # Invoking the handler with no callback must not raise.
        for binding in _bindings_for(chrome, "s-tab"):
            binding.handler(None)  # type: ignore[arg-type]


async def test_s_tab_binding_is_focus_gated() -> None:
    # nit WP-0: the global s-tab binding must carry a filter so it stays inert
    # while a modal Float owns focus (otherwise shift+tab cycles the posture
    # behind an open approval dialog / picker). The binding's filter must track
    # the input-focus check.
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        bindings = _bindings_for(chrome, "s-tab")
        assert bindings
        binding = bindings[0]
        # A non-trivial filter is attached (not the always-True default).
        flt = getattr(binding, "filter", None)
        assert flt is not None
        # Default layout focus is the input window → filter True (s-tab active).
        assert chrome._input_has_focus() is True
        assert bool(flt()) is True
        # Simulate a modal owning focus: point ``_input_window`` at a Window NOT
        # in the layout, so ``has_focus`` is False (the input no longer holds
        # focus) → the filter (and thus the global s-tab binding) goes inert.
        chrome._input_window = Window(FormattedTextControl("x", focusable=True))
        assert chrome._input_has_focus() is False
        assert bool(flt()) is False


async def test_s_tab_does_not_collide_with_existing_bindings() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _make_chrome()
        kb = chrome.app.key_bindings
        assert kb is not None
        all_single = [
            tuple(getattr(k, "value", str(k)) for k in b.keys) for b in kb.bindings
        ]
        # Pre-existing single-key bindings must still be present (no clobber).
        # prompt-toolkit normalizes ``enter`` → ``c-m`` and ``c-space`` → ``c-@``.
        for expected in (
            ("c-m",),  # enter
            ("c-j",),
            ("c-t",),
            ("c-v",),
            ("c-g",),
            ("c-i",),  # tab/completion
            ("c-@",),  # c-space
            ("c-d",),
            ("c-c",),
            ("escape",),
        ):
            assert expected in all_single, expected
        # The new key is present and FREE (Tab itself is c-i, distinct).
        assert ("s-tab",) in all_single
