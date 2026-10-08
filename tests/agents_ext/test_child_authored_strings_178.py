"""#178 — the tool card and the per-child status row print child-authored strings
through the same sanitiser and width bound as the batch widget.

TWO HALVES, BOTH PINNED, because the card's old contract was "P2's
``_partial_text`` VERBATIM" and the fix re-decides it rather than quietly
breaking it (ADR-0199 §(l), amendment 2026-10-08):

* IDENTITY — a clean field renders byte for byte as P2's formatter rendered it.
  The expected strings are built by :func:`_p2_card` / :func:`_p2_status`, a
  restatement of the pre-#178 formatters, rather than imported, so deleting the
  sanitiser cannot make these rows pass by tautology.
* SANITISED — a field carrying C0, DEL, C1, an ESC sequence, CR or a BiDi
  override renders with none of them, and only that field differs.

Where a renderer is involved the assertion is on the BYTES it writes
(``Console(force_terminal=True)``), not on ``Text.plain`` — Rich passes ESC
through, so a plain-text assertion would read a clean string while the terminal
obeyed the escape.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from aelix_agents import panel as panel_module
from aelix_agents.panel import (
    PANEL_ROW_MAX_CHARS,
    PartialThrottle,
    format_card,
    format_panel,
)
from aelix_agents.progress import (
    SubagentProgressBridge,
    format_status_row,
    status_key,
)
from aelix_agents.tool import render_subagent_result
from aelix_ai.messages import TextContent
from aelix_ai.utils.terminal_text import contains_steering_chars
from aelix_coding_agent.subagent_contract import SubagentProgress, SubagentResult
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from tests.agents_ext.test_events_and_statusline import _Api, _Ui

# === the reference: what P2 printed, restated ================================


def _p2_line(progress: SubagentProgress) -> str:
    """``extension._partial_text`` as P2 shipped it, and ``_child_line`` until #178."""

    tool = f" · {progress.current_tool}" if progress.current_tool else ""
    return (
        f"agent {progress.profile} [{progress.state}]{tool} · "
        f"{progress.elapsed_ms / 1000:.0f}s"
    )


def _p2_status(progress: SubagentProgress) -> str:
    """``format_status_row`` until #178, for a snapshot with no model, tokens or cost."""

    parts = [f"agent {progress.profile}"]
    if progress.current_tool:
        parts.append(progress.current_tool)
    elif progress.state == "starting":
        parts.append("starting")
    parts.append(f"{progress.elapsed_ms / 1000:.0f}s")
    return " · ".join(parts)


def _snap(index: int = 0, **kwargs: Any) -> SubagentProgress:
    base: dict[str, Any] = {
        "id": f"sub-{index}",
        "profile": "scout",
        "state": "running",
        "elapsed_ms": 4_200,
    }
    base.update(kwargs)
    return SubagentProgress(**base)


# Clean input: every shape a real child produces. Hangul and a ZWJ emoji are
# here on purpose — wide and zero-width characters are NOT steering characters,
# and a sanitiser that touched them would break identity for a tool name in the
# user's own language. aelix's own runtime only ever publishes a profile's
# validated ``name:`` field (``^[a-z0-9-]+$``, at most 64); the non-ASCII
# profiles below stand for one handed over by another ``SubagentRuntime``,
# which the contract allows.
_CLEAN_TOOLS = (
    None,
    "read",
    "bash",
    "edit",
    "write",
    "grep",
    "find",
    "ls",
    "agent",
    "mcp__github__create_issue",
    "읽기",
    "x" * PANEL_ROW_MAX_CHARS,
)
_CLEAN_PROFILES = (
    "scout",
    "code-reviewer",
    "explorer_v2",
    "리뷰어",
    "👩‍💻dev",
    "a" * PANEL_ROW_MAX_CHARS,
)
_STATES = ("starting", "running", "done", "error", "stopped")

# Hostile input, one class per row. Each one is something a terminal obeys or a
# reader misreads; the comment says which.
_HOSTILE_PIECES = {
    "esc-clear": "\x1b[2J",  # erase the screen
    "esc-sgr": "\x1b[31m",  # repaint what follows
    "esc-hide": "\x1b[8m",  # SGR 8: conceal what follows
    "osc52": "\x1b]52;c;SGVsbG8=\x07",  # write the clipboard
    "zero-width-escape": "\x01\x1b]52;c;SGVsbG8=\x07\x02",  # prompt_toolkit raw passthrough
    "cr": "\r",  # overwrite the row in place
    "lf": "\n",  # add a row
    "nul": "\x00",
    "bel": "\x07",
    "bs": "\x08",
    "del": "\x7f",
    "c1-csi": "\x9b2J",  # one-byte CSI
    "c1-osc": "\x9d0;title\x9c",  # one-byte OSC / ST
    "c1-nel": "\x85",
    "bidi-rlo": "‮",  # renders the rest right-to-left
    "bidi-lre": "‪",
    "bidi-pdf": "‬",
    "bidi-rli": "⁧",
    "bidi-pdi": "⁩",
    "line-separator": " ",
    "zwsp": "​",
}


def _steers(text: str) -> bool:
    """Anything ``terminal_text`` would remove, newline excepted — a multi-row
    card separates its rows with ``\\n`` and that one is ours."""

    return contains_steering_chars(text, keep_newline=True)


# === IDENTITY: a clean card is P2's, byte for byte ===========================


@pytest.mark.parametrize("tool", _CLEAN_TOOLS)
@pytest.mark.parametrize("profile", _CLEAN_PROFILES)
def test_a_clean_single_child_card_is_p2s_byte_for_byte(
    profile: str, tool: str | None
) -> None:
    snapshot = _snap(profile=profile, current_tool=tool)
    assert format_card([snapshot]) == _p2_line(snapshot)


@pytest.mark.parametrize("state", _STATES)
def test_every_state_literal_renders_unchanged(state: str) -> None:
    snapshot = _snap(state=state, current_tool="read")
    assert format_card([snapshot]) == _p2_line(snapshot)


def test_a_clean_batch_card_is_p2s_lines_with_their_index() -> None:
    members = [
        _snap(0, current_tool="read", profile="리뷰어"),
        None,
        _snap(2, state="done", profile="리뷰어"),
    ]
    assert format_card(members).split("\n") == [
        f"[1/3] {_p2_line(members[0])}",  # type: ignore[arg-type]
        "[2/3] agent 리뷰어 [queued]",
        f"[3/3] {_p2_line(members[2])}",  # type: ignore[arg-type]
    ]


@pytest.mark.parametrize("tool", _CLEAN_TOOLS)
@pytest.mark.parametrize("profile", _CLEAN_PROFILES)
def test_a_clean_status_row_is_unchanged(profile: str, tool: str | None) -> None:
    snapshot = _snap(profile=profile, current_tool=tool)
    assert format_status_row(snapshot) == _p2_status(snapshot)


def test_a_starting_child_with_no_tool_still_says_starting() -> None:
    snapshot = _snap(state="starting", current_tool=None, elapsed_ms=0)
    assert format_status_row(snapshot) == "agent scout · starting · 0s"


# === SANITISED: a hostile field loses exactly what a terminal would obey ======


@pytest.mark.parametrize("name", sorted(_HOSTILE_PIECES))
@pytest.mark.parametrize("field", ["profile", "state", "current_tool"])
def test_no_steering_character_survives_onto_the_card(field: str, name: str) -> None:
    piece = _HOSTILE_PIECES[name]
    hostile = {
        "profile": f"scout{piece}x",
        "state": f"running{piece}x",
        "current_tool": f"read{piece}FAKE",
    }[field]
    fields: dict[str, Any] = {field: hostile}
    if field != "current_tool":
        fields["current_tool"] = "read"
    snapshot = _snap(**fields)

    single = format_card([snapshot])
    batch = format_card([snapshot, None, snapshot])

    assert not _steers(single), repr(single)
    assert "\n" not in single
    assert not _steers(batch), repr(batch)
    # Three members, three rows: a child's newline cannot add one.
    assert batch.count("\n") == 2
    # And the queued row is named with the batch's profile, also flattened.
    assert batch.split("\n")[1].startswith("[2/3] ")


@pytest.mark.parametrize("name", sorted(_HOSTILE_PIECES))
@pytest.mark.parametrize("field", ["profile", "current_tool"])
def test_no_steering_character_survives_onto_the_status_row(field: str, name: str) -> None:
    piece = _HOSTILE_PIECES[name]
    hostile = {"profile": f"scout{piece}x", "current_tool": f"read{piece}FAKE"}[field]
    fields: dict[str, Any] = {field: hostile}
    row = format_status_row(_snap(**fields))
    assert not contains_steering_chars(row), repr(row)


def test_only_the_hostile_field_differs_from_p2() -> None:
    """The sanitiser deletes; it does not reformat the line around it."""

    snapshot = _snap(current_tool="read\x1b[31mFAKE")
    assert format_card([snapshot]) == "agent scout [running] · read[31mFAKE · 4s"
    assert format_status_row(snapshot) == "agent scout · read[31mFAKE · 4s"


_NON_ASCII_SPACES = {
    "nbsp": "\u00a0",
    "narrow-nbsp": "\u202f",
    "em-space": "\u2003",
    "ideographic-space": "\u3000",
}


@pytest.mark.parametrize("name", sorted(_NON_ASCII_SPACES))
def test_a_non_ascii_space_is_shown_as_one_ascii_space(name: str) -> None:
    """Review round 2: the identity half does NOT cover these. ``_flatten``'s
    whitespace collapse is ``str.split``, which splits on every Unicode space, so
    a no-break, typographic or ideographic space prints as one U+0020 - which
    the ADR, the guide and the CHANGELOG now say. Pinned so a change to that
    collapse has to change those three sentences too."""

    space = _NON_ASCII_SPACES[name]
    snapshot = _snap(profile=f"리뷰{space}어", current_tool=f"read{space}{space}file")

    assert format_card([snapshot]) == "agent 리뷰 어 [running] · read file · 4s"
    assert format_status_row(snapshot) == "agent 리뷰 어 · read file · 4s"
    assert format_card([snapshot]) != _p2_line(snapshot)


def test_an_escape_sequence_is_defanged_not_removed() -> None:
    """The docs say exactly this: the ESC (or one-byte C1 introducer) and the BEL
    terminator go, and the rest of the sequence stays on the row as inert text."""

    snapshot = _snap(current_tool="read\x1b]52;c;SGVsbG8=\x07\x9b2Jx")

    assert format_card([snapshot]) == "agent scout [running] · read]52;c;SGVsbG8=2Jx · 4s"
    assert format_status_row(snapshot) == "agent scout · read]52;c;SGVsbG8=2Jx · 4s"


@pytest.mark.parametrize(
    ("fields", "card", "row"),
    [
        ({"profile": None}, "agent None [running] · 4s", "agent None · 4s"),
        ({"current_tool": 7}, "agent scout [running] · 7 · 4s", "agent scout · 7 · 4s"),
        ({"current_tool": 0}, "agent scout [running] · 4s", "agent scout · 4s"),
        ({"profile": 3, "state": None}, "agent 3 [None] · 4s", "agent 3 · 4s"),
    ],
    ids=["profile-none", "tool-int", "tool-zero", "profile-int-state-none"],
)
def test_a_non_str_field_prints_as_p2_printed_it(
    fields: dict[str, Any], card: str, row: str
) -> None:
    """Review round 2: ``_flatten`` calls ``.strip()``, so a ``None`` or an ``int``
    raised ``AttributeError`` where P2's f-string printed ``agent None``. The
    contract types these as ``str``; another runtime may still fill it, and the
    taps run under ``suppress``, so the cost was a silently missing frame.
    ``str()`` first, as P2 did - and a falsy tool still drops its term. (P2's
    status row raised ``TypeError`` on an ``int`` tool in its ``" · ".join``;
    that one now prints, which is why only the card is compared to P2.)"""

    snapshot = _snap(**fields)

    assert format_card([snapshot]) == card == _p2_line(snapshot)
    assert format_status_row(snapshot) == row
    # The queued row names the batch by the first published member's profile,
    # and P2's rule for it was truthiness: a None profile queues as "[queued]".
    profile = fields.get("profile", "scout")
    queued = f"[2/2] agent {profile} [queued]" if profile else "[2/2] [queued]"
    assert format_card([snapshot, None]).split("\n")[1] == queued


def test_the_issues_measured_input_comes_out_clean() -> None:
    """The exact snapshot #178 measured on ``8f7d98aa``."""

    p = SubagentProgress(
        id="1",
        profile="scout\x1b[2J",
        state="running",
        current_tool="read\r\x1b[31mFAKE",
        elapsed_ms=1000,
    )
    assert format_card([p, p]) == (
        "[1/2] agent scout[2J [running] · read [31mFAKE · 1s\n"
        "[2/2] agent scout[2J [running] · read [31mFAKE · 1s"
    )


def test_a_tool_name_made_only_of_controls_drops_the_term() -> None:
    """Gated on the FLATTENED value, so no empty ``·  ·`` is left behind."""

    snapshot = _snap(current_tool="\x1b\x07‮")
    assert format_card([snapshot]) == "agent scout [running] · 4s"
    assert format_status_row(snapshot) == "agent scout · 4s"


_CONTROL_BESIDE_SPACE = {
    "inner": ("a \x1b b", "a b"),
    "leading": ("\x1b read", "read"),
    "trailing": ("read \x07", "read"),
    "bidi-inner": ("a \u202e b", "a b"),
    "zwsp-inner": ("a \u200b b", "a b"),
    "two-controls": ("x\x1b \x1b y", "x y"),
}


@pytest.mark.parametrize("name", sorted(_CONTROL_BESIDE_SPACE))
def test_a_deleted_control_leaves_no_extra_space(name: str) -> None:
    """Review round 3: ``_flatten`` collapsed whitespace BEFORE deleting
    controls, so a deleted control between two spaces, or at either end, left a
    double, leading or trailing space — MEASURED on ``bf37350f``: ``"a \\x1b b"``
    printed ``"a  b"``. It collapses again after the strip now, which is what
    makes the docs' "every run becomes one space, leading and trailing go" true
    for this input too."""

    raw, shown = _CONTROL_BESIDE_SPACE[name]
    snapshot = _snap(current_tool=raw)
    assert format_card([snapshot]) == f"agent scout [running] · {shown} · 4s"
    assert format_status_row(snapshot) == f"agent scout · {shown} · 4s"


def test_controls_and_a_space_drop_the_tool_term_too() -> None:
    """``"\\x01 \\x02"`` kept a one-space term (``·   ·``) on ``bf37350f``."""

    snapshot = _snap(current_tool="\x01 \x02")
    assert format_card([snapshot]) == "agent scout [running] · 4s"
    assert format_status_row(snapshot) == "agent scout · 4s"


def test_fits_includes_the_zero_width_backstop() -> None:
    """Review round 3: "clean" includes at most ``limit * _ZERO_WIDTH_SLACK``
    code points (312 at 78 cells), not only at most 78 cells. A field of one
    cell is identity at 312 code points and cut at 313."""

    budget = PANEL_ROW_MAX_CHARS * panel_module._ZERO_WIDTH_SLACK
    assert budget == 312
    at = "e" + "\u0301" * (budget - 1)
    over = at + "\u0301"
    assert cell_len(over) == 1

    fits = _snap(current_tool=at)
    assert format_card([fits]) == _p2_line(fits)
    assert format_status_row(fits) == _p2_status(fits)

    clipped = _snap(current_tool=over)
    assert format_card([clipped]) != _p2_line(clipped)
    assert format_card([clipped]).endswith("… · 4s")
    assert format_status_row(clipped).endswith("… · 4s")


def test_a_field_is_bounded_at_the_widgets_own_per_field_width() -> None:
    """The same bound ``_panel_row`` gives ``current_tool`` — 78 cells — and in
    CELLS, so a wide-character name cannot buy twice its width."""

    for tool in ("x" * 5000, "한" * 5000, "́" * 400_000):
        line = format_card([_snap(current_tool=tool)])
        drawn = line.removeprefix("agent scout [running] · ").removesuffix(" · 4s")
        assert cell_len(drawn) <= PANEL_ROW_MAX_CHARS, cell_len(drawn)
        row = format_status_row(_snap(current_tool=tool))
        drawn = row.removeprefix("agent scout · ").removesuffix(" · 4s")
        assert cell_len(drawn) <= PANEL_ROW_MAX_CHARS, cell_len(drawn)


def test_the_card_and_the_widget_give_the_same_field_the_same_answer() -> None:
    """One child, three surfaces, ONE spelling of its tool name — the defect
    was that the widget flattened what the card printed raw."""

    tool = "read\x1b[31mFAKE‮evil\x9b2J"
    snapshot = _snap(current_tool=tool)
    flat = panel_module._flatten(tool, limit=PANEL_ROW_MAX_CHARS)

    assert f"· {flat} ·" in format_card([snapshot])
    assert f"· {flat} ·" in format_status_row(snapshot)
    assert any(f"· {flat} ·" in row for row in format_panel([snapshot, snapshot]))


def test_the_widget_now_strips_bidi_overrides_too() -> None:
    """The widget's old C0/DEL/C1 table let U+202E through — measured on
    ``8f7d98aa`` — because it was a copy, not the shared helper."""

    rows = format_panel([_snap(current_tool="read‮evil"), _snap(1)])
    assert not any(_steers(row.replace("\x1b[2m", "").replace("\x1b[0m", "")) for row in rows)


def test_the_result_footer_strips_bidi_overrides_too() -> None:
    """``tool._usage_field`` imports the same ``_flatten``, so the footer gains
    the BiDi half with no edit of its own."""

    result = SubagentResult(
        id="sub-1",
        profile="scout",
        ok=True,
        status="ok",
        summary="done",
        model="gpt‮5",
    )
    block = render_subagent_result(result).content[0]
    assert isinstance(block, TextContent)
    footer = block.text.rsplit("\n", 1)[-1]
    assert "‮" not in footer
    assert "gpt5" in footer


# === the paths that actually carry these strings =============================


def test_the_throttle_emits_a_sanitised_card() -> None:
    """``PartialThrottle.record`` is what ``extension._execute`` hands to
    ``ctx.on_partial``; the card it emits is the one under test, not a copy."""

    throttle = PartialThrottle(1)
    text = throttle.record(0, _snap(current_tool="read\x1b]52;c;SGVsbG8=\x07"))
    assert text is not None
    assert not _steers(text)


def test_the_real_bridge_writes_a_sanitised_status_row() -> None:
    """Driven through ``SubagentProgressBridge.__call__``, the tap the runtime
    actually calls, into a UI fake that records what ``set_status`` received."""

    ui = _Ui()
    bridge = SubagentProgressBridge(_Api(ui))
    bridge(_snap(profile="scout\n\x1b[2J", current_tool="read\x01\x1b]52;c;SGVsbG8=\x07\x02"))

    assert ui.writes, "the bridge wrote no row; the assertions below would be vacuous"
    key, text = ui.writes[-1]
    assert key == status_key("sub-0")
    assert text is not None
    assert not contains_steering_chars(text), repr(text)


def _terminal_bytes(text: str) -> str:
    """What Rich writes to a real terminal for ``text``.

    ``color_system=None`` so Rich emits no SGR of its own: any ESC in the output
    came from the input, which is the whole question.
    """

    buffer = io.StringIO()
    console = Console(
        file=buffer, force_terminal=True, color_system=None, width=200, legacy_windows=False
    )
    console.print(Text(text), end="")
    return buffer.getvalue()


@pytest.mark.parametrize("name", sorted(_HOSTILE_PIECES))
def test_the_cards_terminal_bytes_carry_no_child_escape(name: str) -> None:
    piece = _HOSTILE_PIECES[name]
    card = format_card(
        [_snap(profile=f"scout{piece}", current_tool=f"read{piece}FAKE"), None]
    )
    written = _terminal_bytes(card)

    # The positive control: the renderer is live and the card reached it.
    assert "agent scout" in written
    # Rich itself drops BEL, BS, VT, FF and CR, so for those pieces this byte
    # row is green on ``8f7d98aa`` too and the unit rows above are what pin them.
    # ESC, C1, NUL, DEL, the BiDi controls and ZWSP it writes through.
    assert not contains_steering_chars(written, keep_newline=True), repr(written)
    assert written.count("\n") == 1
