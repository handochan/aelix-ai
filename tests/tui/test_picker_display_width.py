"""Track S3 — picker frames are sized in terminal COLUMNS, not codepoints.

``_picker_frame`` draws its dividers ``content_width`` cells wide. Measuring
content with ``len()`` counted a Hangul syllable as 1 while the terminal
gives it 2, so every CJK row in ``/context``, ``/model``, ``/extension`` and
the stats picker under-spanned its frame by roughly half.
"""

from __future__ import annotations

from aelix_coding_agent.tui.context import _visible_len


def test_hangul_counts_two_columns_per_syllable() -> None:
    assert _visible_len("한국어") == 6


def test_ascii_is_unchanged() -> None:
    assert _visible_len("abc") == 3


def test_mixed_cjk_and_ascii() -> None:
    # 10 Hangul syllables (20 cells) + 2 spaces.
    assert _visible_len("한국어 확장 설명입니다") == 22


def test_ansi_wrapped_hangul_measures_visible_width_only() -> None:
    """ANSI stripping and width measurement have to compose."""

    assert _visible_len("\x1b[2m한국어\x1b[0m") == 6
    assert _visible_len("\x1b[1;36m한국어 확장\x1b[0m") == 11


def test_ansi_wrapped_ascii_still_ignores_escapes() -> None:
    assert _visible_len("\x1b[1mabc\x1b[0m") == 3


def test_empty_string() -> None:
    assert _visible_len("") == 0


def test_frame_divider_spans_a_hangul_row() -> None:
    """The end-to-end property: the divider is at least as wide as the row.

    This is the visible symptom — a divider shorter than the text it is
    supposed to underline.
    """

    from aelix_coding_agent.tui.context import _picker_frame

    row = "한국어 확장 설명입니다"
    frame = _picker_frame("제목", [row], "힌트", _visible_len(row))
    divider = next(
        line for line in frame.value.split("\n") if "─" in line
    )
    assert _visible_len(divider) >= _visible_len(row)


# === GitHub #48 ask 1 — the rule is coloured, and the title rides it ==========


def _rows(title: str, body: list[str], hint: str, width: int) -> list[str]:
    from aelix_coding_agent.tui.context import _picker_frame

    return _picker_frame(title, body, hint, width).value.split("\n")


_BODY = ["  Theme", "  Statusline", "\x1b[2m  (1/2)\x1b[0m"]
_HINT = "↑/↓ move · Esc cancel"
_NINE_ROW_TITLE = "\n".join(f"consent line {i}" for i in range(9))


def test_the_rule_is_not_dim() -> None:
    """The owner's report was that an open panel does not separate from the
    transcript around it. The measured cause was that the rule carried SGR 2 —
    the faintest attribute a terminal has — on the one element whose entire job
    is to say where the panel starts and stops."""

    from aelix_coding_agent.tui.context import _PICK_DIM, _PICK_RULE

    rule = _rows("Settings", _BODY, _HINT, 40)[-2]
    assert _PICK_RULE in rule
    assert _PICK_DIM not in rule


def test_the_rule_is_not_the_highlighted_rows_style() -> None:
    """A rule in ``_PICK_SEL``'s bold cyan would give the eye two winners.

    The cursor row has to stay the single brightest thing on screen, so this
    pins the two APART rather than merely pinning the rule's colour — the
    tempting edit is to reuse the bold-cyan constant that is already there.
    """

    from aelix_coding_agent.tui.context import _PICK_RULE, _PICK_SEL

    assert _PICK_RULE != _PICK_SEL
    assert _PICK_SEL not in _rows("Settings", _BODY, _HINT, 40)[-2]


def test_a_single_row_title_rides_the_top_rule() -> None:
    rows = _rows("Settings", _BODY, _HINT, 40)
    assert "Settings" in rows[0]
    assert rows[0].count("─") >= 3  # a rule, not a bare label
    assert rows[1] == "  Theme"  # nothing between the title and the body


def test_riding_the_rule_costs_a_row_rather_than_adding_one() -> None:
    """The modal BOTTOM-TRUNCATES — it does not scroll — so row count may only
    move in the safe direction. ADR-0199 measured that at eight members the hint,
    the closing rule, the counter and ``Cancel`` are simply not drawn."""

    assert len(_rows("Settings", _BODY, _HINT, 40)) == len(_BODY) + 3


def test_a_multi_row_title_keeps_its_own_line() -> None:
    """Nine rows cannot ride a rule, and the spawn-consent dialog passes nine.

    ``aelix_agents/consent.py`` writes its height budget down as
    ``title_rows + option_rows + 4`` and ``test_batch_consent.py`` gates it, so
    this arm changing shape is how ``Cancel`` goes off screen at N >= 5.
    """

    rows = _rows(_NINE_ROW_TITLE, _BODY, _HINT, 40)
    assert len(rows) == 9 + 1 + len(_BODY) + 1 + 1
    assert "consent line 0" in rows[0]
    assert set(rows[9].replace("\x1b[36m", "").replace("\x1b[0m", "")) == {"─"}


def test_the_top_rule_is_exactly_as_wide_as_the_bottom_one() -> None:
    """Including a Hangul title, whose label is twice its length in columns.

    A top rule disagreeing with the bottom one by half its label would be worse
    than the dim rule it replaces — the frame would read as broken, not quiet.
    """

    for title in ("Settings", "설정", "권한 모드를 고르세요"):
        for width in (30, 52, 90):
            rows = _rows(title, _BODY, _HINT, width)
            assert _visible_len(rows[0]) == _visible_len(rows[-2]), (title, width)


def test_a_title_too_wide_for_the_rule_does_not_ride_it() -> None:
    """A label only rides the rule if it LEAVES one.

    ``width`` is clamped to ``_PICK_MAX_WIDTH`` and the title is not bounded at
    all — ``ExtensionUIContext.select`` takes whatever an extension passes — so
    an over-wide label riding the rule makes the TOP rule overrun the bottom one
    by its own excess: 204 cells against 40, measured. On its own line it
    overflows the way it already does, which is a clipped title rather than a
    frame that looks broken.

    This test replaced one that claimed to gate the ``max(0, …)`` clamp. It did
    not: ``"─" * -164`` and ``"─" * 0`` are both ``""``, so removing the clamp
    changed nothing and the sabotage stayed green.
    """

    rows = _rows("x" * 200, _BODY, _HINT, 40)
    assert rows[1] == _rows("", _BODY, _HINT, 40)[1]  # a bare rule under the title
    assert _visible_len(rows[-2]) == 40
    assert "─" not in rows[0]  # the title kept its own line


def test_a_title_that_only_just_fits_still_rides_the_rule() -> None:
    """The boundary of the rule above, from the other side — so a condition that
    rejected every title would pass that test and fail this one."""

    title = "x" * (40 - 6)
    rows = _rows(title, _BODY, _HINT, 40)
    assert title in rows[0]
    assert _visible_len(rows[0]) == _visible_len(rows[-2]) == 40


def test_an_empty_title_keeps_the_bare_rule() -> None:
    """``select`` is not the only caller and a title is not guaranteed. An empty
    label riding a rule would draw ``──  ──────``, a gap with nothing in it."""

    rows = _rows("", _BODY, _HINT, 40)
    assert _visible_len(rows[0]) == 0  # the old empty-bold line
    assert set(rows[1].replace("\x1b[36m", "").replace("\x1b[0m", "")) == {"─"}


# === review finding: the title is a caller's string, and callers include extensions


def test_a_control_character_in_the_title_never_reaches_the_frame() -> None:
    """``ExtensionUIContext.select`` takes whatever an extension passes.

    MEASURED with an OSC title (``Set\x1b]0;pwned\x07tings``): the escape reached
    the output — which SETS THE TERMINAL'S WINDOW TITLE — and made the top rule 32
    visible cells against the bottom rule's 40, because ``_visible_len`` strips
    SGR only while prompt-toolkit's ANSI parser consumes every escape.

    The BODY is deliberately not sanitised: those rows arrive already styled by
    this module (``_PICK_SEL`` on the cursor row, dim on the counter).
    """

    rows = _rows("Set\x1b]0;pwned\x07tings", _BODY, _HINT, 40)
    joined = "".join(rows)
    assert "\x1b]" not in joined
    assert "\x07" not in joined
    assert "\x9b" not in joined
    assert _visible_len(rows[0]) == _visible_len(rows[-2]) == 40
    assert "Settings" in rows[0].replace(" ", "") or "Set" in rows[0]


def test_a_c1_csi_in_the_title_is_removed() -> None:
    """``\x9b`` is the one-byte CSI and prompt-toolkit honours it."""

    rows = _rows("before\x9b31mafter", _BODY, _HINT, 40)
    assert "\x9b" not in "".join(rows)
    assert _visible_len(rows[0]) == _visible_len(rows[-2])


def test_sanitising_the_title_does_not_flatten_a_multi_row_one() -> None:
    """The newline survives: the spawn-consent dialog's title is nine rows and its
    height budget is gated on that."""

    rows = _rows(_NINE_ROW_TITLE, _BODY, _HINT, 40)
    assert len(rows) == 9 + 1 + len(_BODY) + 1 + 1


def test_the_title_sanitiser_removes_escapes_and_leaves_the_frame_square() -> None:
    """Exactly what the map buys, on the glass — no more and no less.

    The comment this pins carried a wrong measurement twice, so each claim here is
    one the harness re-derives.

    IT REMOVES THE CONTROL BYTES, so no escape SEQUENCE can be assembled from a
    caller's title. What remains is the payload's printable tail as ordinary text
    (``[31m``, ``]0;PWNED``) — visible junk in a label is a caller getting what it
    asked for; a live escape in this frame is not.

    IT DOES NOT MAKE THE RULES AGREE, because they already do: both come from the
    one ``width`` number, so they are equal in every escape class and in BOTH
    title placements (riding the rule and on its own line). Measured +0
    throughout. An earlier draft reported "32 cells against 40"; that does not
    reproduce in either direction.

    AND NO OSC EVER SET A WINDOW TITLE, before the map or after. The control at
    the end proves the emulator would have noticed if one had.
    """

    import pyte
    from aelix_coding_agent.tui.context import _picker_frame, _visible_len
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    hint = "↑/↓ move · type to filter · Enter select · Esc cancel"
    body = ["  option one"]

    def paint(row: str) -> str:
        return "".join(text for _style, text, *_ in to_formatted_text(ANSI(row)))

    for base in (
        "Resume session",  # short: the title rides the top rule
        "Choose a session to resume from this project folder and its history",
    ):
        for name, title in {
            "plain": base,
            "sgr": base + "\x1b[31m",
            "osc": base + "\x1b]0;PWNED\x07",
            "csi": base + "\x1b[9A\x1b[40D",
            "c1": base + "\x9b31m",
            "dcs": base + "\x1bPq\x1b\\",
        }.items():
            width = max(_visible_len(title), _visible_len(hint), _visible_len(body[0]))
            rows = _picker_frame(title, body, hint, width).value.split("\n")
            joined = "".join(rows)
            # No control byte survives, so nothing downstream can parse one.
            for control in ("\x1b", "\x9b", "\x07"):
                assert control not in paint(joined), (base[:12], name, control)
            # The two rules agree. Which row holds the top one depends on whether
            # the title rode it, so find it rather than assume an index.
            rules = [r for r in rows if set(paint(r).strip()) <= {"─"} and "─" in paint(r)]
            top = rules[0] if rules else rows[1]
            assert len(paint(top)) == len(paint(rows[-2])), (
                base[:12], name, len(paint(top)), len(paint(rows[-2]))
            )

    # CONTROL: the emulator used above genuinely reads OSC titles, so "no title was
    # set" is a measurement and not a blind spot.
    screen = pyte.Screen(80, 10)
    pyte.Stream(screen).feed("hi\x1b]0;REAL\x07")
    assert screen.title == "REAL"
    screen = pyte.Screen(80, 10)
    frame = _picker_frame("Settings\x1b]0;PWNED\x07", body, hint, 40)
    pyte.Stream(screen).feed(paint(frame.value))
    assert screen.title == ""
