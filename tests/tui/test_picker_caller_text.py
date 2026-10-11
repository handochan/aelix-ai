"""#179: caller text cannot become ANSI styles or a zero-width terminal write.

Paint the actual modal controls and serialize their actual prompt-toolkit
fragments through Vt100_Output. A genuine OSC positive control establishes
that the byte/emulator observation detects a title change. Ordinary OSC and
SOH/STX-wrapped OSC are separate fixtures; only the latter escaped the parser.
"""

from __future__ import annotations

import io
import types
from typing import Any

import pyte
import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import Layout
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.shortcuts import print_formatted_text

from tests.tui.test_select_title_held import _Dialog

_ZERO = "\x01\x1b]0;TITLE179\x07\x02"
_PAYLOADS = [
    ("\x1b[8mHIDE\x1b[0m", "^[[8mHIDE^[[0m"),
    ("\x1b[31mRED", "^[[31mRED"),
    ("\x1b]0;TITLE179\x07", "^[]0;TITLE179^G"),
    ("\x1b]0;TITLE179\x1b\\", "^[]0;TITLE179^[\\"),
    (_ZERO, "^A^[]0;TITLE179^G^B"),
    ("\x1bPq\x1b\\", "^[Pq^[\\"),
    ("\x1b[9C", "^[[9C"),
    ("\x9b31m\x9dX", "<U+009B>31m<U+009D>X"),
    ("\x00\r\t\x7f", "^@^M^I^?"),
    ("\u202e\u2066\u200b\ufeff\u2028", "<U+202E><U+2066><U+200B><U+FEFF><U+2028>"),
]


def _fragments(dialog: _Dialog) -> list[tuple[str, str]]:
    control = dialog.box["window"].content
    content = control.create_content(width=240, height=60)
    return [
        fragment
        for i in range(content.line_count)
        for fragment in [*content.get_line(i), ("", "\n")]
    ]


def _line(dialog: _Dialog, prefix: str) -> list[tuple[str, str]]:
    content = dialog.box["window"].content.create_content(width=240, height=60)
    return next(
        [fragment for fragment in content.get_line(i) if fragment[1]]
        for i in range(content.line_count)
        if "".join(fragment[1] for fragment in content.get_line(i)).startswith(prefix)
    )


def _terminal(fragments: Any) -> tuple[str, pyte.Screen]:
    stream = io.StringIO()
    output = Vt100_Output(stream, lambda: Size(rows=60, columns=240), term="xterm")
    print_formatted_text(fragments, output=output, end="")
    emitted = stream.getvalue()
    screen = pyte.Screen(240, 60)
    pyte.Stream(screen).feed(emitted)
    return emitted, screen


def _terminal_window(dialog: _Dialog) -> tuple[str, pyte.Screen]:
    stream = io.StringIO()
    output = Vt100_Output(stream, lambda: Size(rows=60, columns=240), term="xterm")
    app: Application[Any] = Application(
        layout=Layout(dialog.box["window"]), input=DummyInput(), output=output, full_screen=True
    )
    with set_app(app):
        app.renderer.render(app, app.layout)
    dialog.box["renderer"] = app.renderer
    output.flush()
    emitted = stream.getvalue()
    screen = pyte.Screen(240, 60)
    pyte.Stream(screen).feed(emitted)
    return emitted, screen


@pytest.mark.parametrize(
    "builtin_name", ["GuardrailExtension", "PermissionExtension", "StatusExtension"]
)
async def test_installed_builtin_rows_keep_owned_dim_without_ansi_names(
    monkeypatch: pytest.MonkeyPatch, builtin_name: str
) -> None:
    from aelix_coding_agent.tui.extension_manager import build_installed_lines

    user_name = "USER:" + _ZERO + "_END"
    lines = build_installed_lines(
        [
            types.SimpleNamespace(name=builtin_name, manifest=None),
            types.SimpleNamespace(name=user_name, manifest=None),
        ],
        [],
    )
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.tabbed("Extensions", [("Installed", lambda: lines)]))
    try:
        emitted, screen = _terminal_window(dialog)
        painted = "\n".join(screen.display)
        assert "^[[2m" not in painted and "^[[0m" not in painted
        assert "● " + builtin_name in painted
        assert "USER:^A^[]0;TITLE179^G^B_END" in painted
        assert "\x1b]0;TITLE179" not in emitted and screen.title == ""
        attrs = dialog.box["renderer"]._attrs_for_style
        builtin_row = _line(dialog, "  ● ")
        assert all(attrs[style].dim for style, text, *_ in builtin_row if text.strip())
        assert all(not attrs[style].hidden for style, text, *_ in builtin_row if text)
        user_row = _line(dialog, "  ✓ USER:")
        assert any(attrs[style].reverse for style, text, *_ in user_row if text == "^")
        assert not any(
            attrs[style].dim or attrs[style].hidden for style, text, *_ in user_row if text
        )
    finally:
        await dialog.close()


def _safe_render(dialog: _Dialog, expected: str) -> list[tuple[str, str]]:
    fragments = _fragments(dialog)
    text = "".join(fragment[1] for fragment in fragments)
    assert expected in text
    assert not any("[ZeroWidthEscape]" in style for style, *_ in fragments)
    assert not any("hidden" in style.split() or "ansired" in style for style, *_ in fragments)
    assert any("reverse" in style.split() for style, *_ in fragments)
    emitted, screen = _terminal(fragments)
    assert "\x1b]0;TITLE179" not in emitted
    assert screen.title == ""
    return fragments


def test_terminal_observation_has_genuine_osc_and_owned_style_positive_controls() -> None:
    # ZeroWidthEscape is deliberately an actual raw write here, outside a picker.
    emitted, screen = _terminal(to_formatted_text(ANSI(_ZERO)))
    assert "\x1b]0;TITLE179\x07" in emitted
    assert screen.title == "TITLE179"
    emitted, screen = _terminal(to_formatted_text(ANSI("\x1b[1;36mOWNED\x1b[0m")))
    assert "OWNED" in emitted and screen.title == ""
    cell = next(cell for row in screen.buffer.values() for cell in row.values() if cell.data == "O")
    assert cell.bold and cell.fg == "cyan"


@pytest.mark.parametrize(
    ("own", "surface"),
    [
        (False, "option"),
        (True, "option"),
        (False, "detail"),
        (True, "detail"),
        (False, "tab-name"),
        (False, "tab-body"),
        (False, "tab-error"),
    ],
)
async def test_delimiter_osc_never_reaches_real_renderer_bytes(
    monkeypatch: pytest.MonkeyPatch, own: bool, surface: str
) -> None:
    dialog = _Dialog(monkeypatch)
    if surface in ("option", "detail"):
        await dialog.open(
            dialog.ctx.select(
                "Choose",
                ["OPTION:" + (_ZERO if surface == "option" else "") + "_END"],
                detail=lambda i: ["DETAIL:" + (_ZERO if surface == "detail" else "") + "_END"],
                own=own,
            )
        )
    else:

        def body() -> list[str]:
            if surface == "tab-error":
                raise ValueError("ERROR:" + _ZERO + "_END")
            return ["BODY:" + (_ZERO if surface == "tab-body" else "") + "_END"]

        await dialog.open(
            dialog.ctx.tabbed(
                "Tabs", [("NAME:" + (_ZERO if surface == "tab-name" else "") + "_END", body)]
            )
        )
    try:
        emitted, screen = _terminal_window(dialog)
        assert "\x1b]0;TITLE179" not in emitted
        assert screen.title == ""
        assert "TITLE179" in "\n".join(screen.display)
    finally:
        await dialog.close()


@pytest.mark.parametrize("own", [False, True])
@pytest.mark.parametrize(("payload", "shown"), _PAYLOADS)
async def test_selected_unselected_and_detail_are_named_before_owned_styling(
    monkeypatch: pytest.MonkeyPatch, own: bool, payload: str, shown: str
) -> None:
    dialog = _Dialog(monkeypatch)
    indices: list[int] = []

    def detail(index: int) -> list[str]:
        indices.append(index)
        return ["DETAIL:" + payload + "_END"]

    first, second = "FIRST:" + payload + "_END", "SECOND:" + payload + "_END"
    await dialog.open(dialog.ctx.select("Choose", [first, second], detail=detail, own=own))
    try:
        dialog.paint(240, 60)
        fragments = _safe_render(dialog, "FIRST:" + shown)
        assert "SECOND:" + shown in "".join(f[1] for f in fragments)
        assert "DETAIL:" + shown in "".join(f[1] for f in fragments)
        selected = _line(dialog, "▸ FIRST:")[0][0]
        assert "bold" in selected and "ansicyan" in selected
        detail_style = _line(dialog, "DETAIL:")[0][0]
        assert "dim" in detail_style
        rule_style = next(style for style, text in fragments if text == "─")
        assert "ansicyan" in rule_style and "bold" not in rule_style
        dialog.press("down")
        dialog.paint(240, 60)
        _safe_render(dialog, "SECOND:" + shown)
        dialog.press("enter")
        assert await dialog.answer() == second
        assert 1 in indices
    finally:
        await dialog.close()


@pytest.mark.parametrize(("payload", "shown"), _PAYLOADS)
async def test_active_inactive_tab_name_and_body_are_safe(
    monkeypatch: pytest.MonkeyPatch, payload: str, shown: str
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(
        dialog.ctx.tabbed(
            "Tabs",
            [
                ("ACTIVE:" + payload + "_END", lambda: ["BODY:" + payload + "_END"]),
                ("INACTIVE:" + payload + "_END", lambda: ["SECOND BODY:" + payload + "_END"]),
            ],
        )
    )
    try:
        dialog.paint(240, 60)
        fragments = _safe_render(dialog, "ACTIVE:" + shown)
        text = "".join(f[1] for f in fragments)
        assert "INACTIVE:" + shown in text and "BODY:" + shown in text
        assert "bold" in next(style for style, text in fragments if text == "A")
        dialog.press("right")
        dialog.paint(240, 60)
        _safe_render(dialog, "SECOND BODY:" + shown)
    finally:
        await dialog.close()


async def test_raising_tab_callback_message_is_named_and_dim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _Dialog(monkeypatch)

    def broken() -> list[str]:
        raise ValueError("ERROR:" + _ZERO)

    await dialog.open(dialog.ctx.tabbed("Tabs", [("Broken", broken)]))
    try:
        dialog.paint(240, 60)
        fragments = _safe_render(dialog, "ERROR:^A^[]0;TITLE179^G^B")
        assert "dim" in next(style for style, text in fragments if text == "E")
    finally:
        await dialog.close()


@pytest.mark.parametrize("own", [False, True])
async def test_display_collision_filter_and_original_detail_index(
    monkeypatch: pytest.MonkeyPatch, own: bool
) -> None:
    # Named SOH and literal ^A share plain search text but remain distinct by
    # style and index. Duplicates must also retain their original detail index.
    values = ["elsewhere", "target\x01", "target^A", "target^A"]
    indices: list[int] = []
    dialog = _Dialog(monkeypatch)
    await dialog.open(
        dialog.ctx.select(
            "Choose", values, own=own, detail=lambda i: (indices.append(i), [f"index:{i}"])[1]
        )
    )
    try:
        dialog.paint(80, 20)
        dialog.type_text("target^A")
        painted = dialog.paint(80, 20)
        assert "(1/3)" in painted
        dialog.press("down")
        dialog.press("down")
        assert "index:3" in dialog.paint(80, 20)
        assert indices[-1] == 3
        dialog.press("enter")
        assert await dialog.answer() is values[3]
    finally:
        await dialog.close()


async def test_raw_cancel_identity_remains_answerable_while_title_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = "Cancel\x01"
    dialog = _Dialog(monkeypatch)
    await dialog.open(
        dialog.ctx.select(
            "\n".join(f"title:{i}" for i in range(50)),
            ["Cancel^A", cancel],
            cancel_options={cancel},
        )
    )
    try:
        assert "Enter held" in dialog.paint(80, 8)
        dialog.press("enter")
        assert not dialog.answered, "a display collision must not inherit raw cancel identity"
        dialog.press("down")
        assert "Cancel^A" in dialog.paint(80, 8)
        dialog.press("enter")
        assert await dialog.answer() is cancel
    finally:
        await dialog.close()


async def test_tab_filter_uses_display_text_and_preserves_owned_stats_dim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_coding_agent.tui.stats_dashboard import _dim

    dialog = _Dialog(monkeypatch)
    await dialog.open(
        dialog.ctx.tabbed("Tabs", [("Stats", lambda: [_dim("note\x01"), "other"])], filter_tabs={0})
    )
    try:
        dialog.paint(80, 20)
        dialog.type_text("note^A")
        dialog.paint(80, 20)
        fragments = _safe_render(dialog, "note^A")
        assert "other" not in "".join(f[1] for f in fragments)
        assert "dim" in _line(dialog, "note^A")[0][0]
        assert "bold" in next(style for style, text in _line(dialog, "Filter:") if text == "n")
    finally:
        await dialog.close()


@pytest.mark.parametrize("own", [False, True])
async def test_newlines_and_graphemes_survive_and_width_uses_display_rows(
    monkeypatch: pytest.MonkeyPatch, own: bool
) -> None:
    from aelix_coding_agent.tui.context import _visible_len

    value = "한글 e\u0301 👩‍💻\n" + "x" * 56 + "\x01"
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select("Choose", [value], own=own))
    try:
        painted = dialog.paint(100, 20)
        assert "한글 e\u0301" in painted and "👩‍💻" in painted
        assert "x" * 56 + "^A" in painted
        assert "^J" not in painted
        rules = [row for row in painted.splitlines() if row.startswith("─")]
        assert all(_visible_len(row) == 58 for row in rules), rules
        dialog.press("enter")
        assert await dialog.answer() is value
    finally:
        await dialog.close()
