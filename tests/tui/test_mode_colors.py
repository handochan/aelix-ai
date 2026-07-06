"""#66 item 6 — the permission mode drives BOTH the footer badge colour and the
❯ prompt colour from ONE source (``permission_mode.MODE_META.badge_style``).

Covers the pure style→SGR mapper (footer_segments), the module-level badge/
prompt helpers (shell), and the chrome ❯ prefix reading its live provider.
"""

from __future__ import annotations

import io

from aelix_coding_agent.builtin.permission_mode import (
    MODE_META,
    PermissionMode,
    PermissionPosture,
)
from aelix_coding_agent.tui.chrome import (
    _DEFAULT_PROMPT_STYLE,
    AelixChrome,
    _ModePrompt,
)
from aelix_coding_agent.tui.footer_segments import sgr_wrap, style_to_sgr
from aelix_coding_agent.tui.shell import _mode_badge_ansi, _mode_prompt_style
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

# === style → SGR mapper (footer_segments, pure) =============================


def test_style_to_sgr_maps_badge_palette() -> None:
    assert style_to_sgr("yellow") == "\x1b[33m"
    assert style_to_sgr("cyan") == "\x1b[36m"
    assert style_to_sgr("green") == "\x1b[32m"
    assert style_to_sgr("bold red") == "\x1b[1;31m"
    # empty / fully-unmapped tokens → no colour (never raises).
    assert style_to_sgr("") == ""
    assert style_to_sgr("fg:gray italic") == ""


def test_sgr_wrap_wraps_and_resets() -> None:
    assert sgr_wrap("hi", "yellow") == "\x1b[33mhi\x1b[0m"
    assert sgr_wrap("hi", "") == "hi"  # unmapped → pass-through, no reset


# === footer badge colour (shell, per posture) ===============================


def test_mode_badge_ansi_colours_non_default_modes() -> None:
    for mode in (
        PermissionMode.AUTO_ACCEPT,
        PermissionMode.PLAN,
        PermissionMode.YOLO,
        PermissionMode.AUTO,
    ):
        meta = MODE_META[mode]
        badge = _mode_badge_ansi(PermissionPosture(mode=mode))
        assert badge == sgr_wrap(meta.badge_text, meta.badge_style)
        assert style_to_sgr(meta.badge_style) in badge  # coloured
        assert meta.badge_text in badge  # keeps the glyph label


def test_mode_badge_ansi_none_on_default_and_no_posture() -> None:
    assert _mode_badge_ansi(PermissionPosture(mode=PermissionMode.DEFAULT)) is None
    assert _mode_badge_ansi(None) is None


def test_mode_badge_ansi_changes_with_mode() -> None:
    posture = PermissionPosture(mode=PermissionMode.DEFAULT)
    assert _mode_badge_ansi(posture) is None
    posture.cycle()  # DEFAULT → AUTO_ACCEPT (yellow)
    yellow = _mode_badge_ansi(posture)
    posture.cycle()  # AUTO_ACCEPT → PLAN (cyan)
    cyan = _mode_badge_ansi(posture)
    assert yellow is not None and cyan is not None
    assert yellow != cyan
    assert "\x1b[33m" in yellow  # yellow SGR
    assert "\x1b[36m" in cyan  # cyan SGR


# === ❯ prompt style (shell, per posture) ====================================


def test_mode_prompt_style_follows_mode() -> None:
    assert _mode_prompt_style(None) == _DEFAULT_PROMPT_STYLE
    assert (
        _mode_prompt_style(PermissionPosture(mode=PermissionMode.DEFAULT))
        == _DEFAULT_PROMPT_STYLE
    )
    assert (
        _mode_prompt_style(PermissionPosture(mode=PermissionMode.AUTO_ACCEPT))
        == "class:aelix.prompt bold yellow"
    )
    assert (
        _mode_prompt_style(PermissionPosture(mode=PermissionMode.PLAN))
        == "class:aelix.prompt bold cyan"
    )
    # yolo badge_style is "bold red" — the bold is not duplicated.
    assert (
        _mode_prompt_style(PermissionPosture(mode=PermissionMode.YOLO))
        == "class:aelix.prompt bold red"
    )
    assert (
        _mode_prompt_style(PermissionPosture(mode=PermissionMode.AUTO))
        == "class:aelix.prompt bold green"
    )


# === chrome ❯ prefix reads the live provider ================================


def test_mode_prompt_prefix_reads_provider_live() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        control = chrome._input_window.content  # type: ignore[union-attr]
        prefix = next(
            p for p in control.input_processors if isinstance(p, _ModePrompt)
        )

        # No provider wired → neutral bold cyan; the ❯ glyph text is unchanged
        # (so the placeholder + prefix-wiring tests keep passing).
        assert prefix.style == _DEFAULT_PROMPT_STYLE
        assert "❯" in str(prefix.text)

        # A live provider recolours on every render (re-read each access).
        chrome.prompt_style_provider = lambda: "class:aelix.prompt bold yellow"
        assert prefix.style == "class:aelix.prompt bold yellow"
        chrome.prompt_style_provider = lambda: "class:aelix.prompt bold green"
        assert prefix.style == "class:aelix.prompt bold green"

        # A faulty provider must not break input — falls back to the default.
        def _boom() -> str:
            raise RuntimeError("boom")

        chrome.prompt_style_provider = _boom
        assert prefix.style == _DEFAULT_PROMPT_STYLE
