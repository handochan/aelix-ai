"""``safe_for_terminal`` — what a hostile server body may and may not do to a terminal.

WHAT DRIVES THE THRESHOLDS. Issue #184's reporter pasted GitHub's "Unicorn" 502
page because the Copilot device flow interpolated ``response.text`` into an
exception that went straight to the screen. The recorded body is ~55 KB / 164
lines; at 80 columns that is ~815 rendered rows, which scrolls the whole
transcript away on any real terminal.

WHY ``rich`` IS NOT THE DEFENCE, and why these tests do not rely on it. Measured:
``rich`` strips exactly five control codes — BEL, BS, VT, FF, CR. ESC survives.
The one-byte C1 CSI (0x9B) survives. NUL survives. And because it removes BEL (an
OSC *terminator*) while passing ``ESC]`` (the *introducer*), it converts a bounded
title-set into an unterminated sequence that eats what follows. So the tests below
assert on this helper's output directly rather than on anything rich does.

EVERY "is it gone?" ASSERTION HAS A POSITIVE CONTROL. A test that checks a
character is absent passes trivially if the character was never there, and a
zero that means "the payload was wrong" has already shipped in this repo as a
false clean bill of health. So each hostile case asserts
:func:`contains_steering_chars` on the INPUT first.
"""

from __future__ import annotations

import pytest
from aelix_ai.utils.terminal_text import (
    Controls,
    contains_steering_chars,
    safe_for_terminal,
)

#: One string carrying every family the helper claims to handle. Named parts so a
#: failure says which class regressed.
_ESC_ERASE = "\x1b[2J"  # erases the screen — measured to remove the transcript
_C1_CSI = "\x9b31m"  # one-byte CSI; absent from all nine tables this repo had
_OSC_TITLE = "\x1b]0;PWNED\x07"  # rich eats the BEL and leaves this unterminated
_OSC_CLIPBOARD = "\x1b]52;c;aGk=\x1b\\"  # writes the user's clipboard
_ALT_SCREEN = "\x1b[?1049h"  # takes the screen
_RLO = "‮trusted=false‬"  # renders reversed
_ZWSP = "git​hub.com"  # a homograph aid
HOSTILE = f"502: {_ESC_ERASE}{_C1_CSI}{_OSC_TITLE}{_OSC_CLIPBOARD}{_ALT_SCREEN}{_RLO}{_ZWSP}"


def test_the_payload_is_actually_hostile() -> None:
    """The positive control for every removal assertion in this file."""

    assert contains_steering_chars(HOSTILE)
    for name, ch in [
        ("ESC", "\x1b"),
        ("C1 CSI", "\x9b"),
        ("BEL", "\x07"),
        ("RLO", "‮"),
        ("ZWSP", "​"),
    ]:
        assert ch in HOSTILE, name


def test_nothing_that_can_steer_a_terminal_survives() -> None:
    """SABOTAGE: drop C1 from the table (the shape eight of nine prior copies had).

    ``\\x9b`` IS CSI on its own — one byte, no ESC — so a table that only kills
    ``\\x1b`` leaves a working escape. This must go RED.
    """

    out = safe_for_terminal(HOSTILE)

    for ch in ("\x1b", "\x9b", "\x07", "\x00", "\x7f", "‮", "‬", "​", "﻿"):
        assert ch not in out, repr(ch)
    # The sequence collapses to its inert literal rather than vanishing, so an
    # operator can still see that the server sent something odd.
    assert "[2J" in out


def test_bidi_overrides_are_removed_because_they_change_what_a_human_reads() -> None:
    """The one class here that attacks the READER, not the emulator.

    U+202E renders ``trusted=false`` as ``trusted=true``. No prior table in this
    repo covered it — measured across all nine.
    """

    assert contains_steering_chars(_RLO)
    out = safe_for_terminal(_RLO)
    assert out == "trusted=false"


def test_the_zero_width_joiner_is_deliberately_kept() -> None:
    """SABOTAGE: add U+200D to the kill set — the obvious "strip all zero-width" rule.

    Measured: that turns ``👩‍💻`` into ``👩💻`` and ``👨‍👩‍👧‍👦`` into four separate
    people. ZWJ cannot move a cursor, retitle a window, or reverse reading order;
    it only makes terminal width arithmetic inexact, which is the renderer's
    problem to solve where cell widths are known. This must go RED.
    """

    for joined in ("👩‍💻", "🏳️‍🌈", "👨‍👩‍👧‍👦"):
        assert safe_for_terminal(joined) == joined


@pytest.mark.parametrize(
    "text",
    [
        "파일을 찾을 수 없습니다: 設定ファイル",
        "école naïve",
        "❤️ 🎉",
        "server said: {\"error\": \"expired_token\"}",
    ],
)
def test_legitimate_text_is_returned_unchanged(text: str) -> None:
    """A sanitiser that eats real error messages gets turned off by whoever owns the call."""

    assert safe_for_terminal(text) == text


def test_newline_and_tab_are_opt_in_because_both_have_burned_a_caller() -> None:
    """``\\n`` blows up row counts; deleting ``\\t`` once collapsed Makefile indentation."""

    assert safe_for_terminal("a\nb") == "ab"
    assert safe_for_terminal("a\nb", keep_newline=True) == "a\nb"
    assert safe_for_terminal("a\tb") == "ab"
    assert safe_for_terminal("a\tb", keep_tab=True) == "a\tb"


def test_space_mode_keeps_the_word_boundary() -> None:
    """Family B's policy, as a parameter rather than a second implementation."""

    assert safe_for_terminal("a\x1bb", controls=Controls.SPACE) == "a b"


# ── bounds ───────────────────────────────────────────────────────────────────


def test_a_55kb_body_is_bounded_to_something_a_terminal_can_hold() -> None:
    """The #184 symptom, at the measured scale.

    SABOTAGE: drop ``max_lines`` handling. 164 lines reach the screen and this
    must go RED — the control-strip alone does NOT fix #184, it produces one
    54,743-character line.
    """

    body = "<!DOCTYPE html>\n" + "\n".join("x" * 400 for _ in range(163))
    raw = f"502 Bad Gateway: {body}"
    assert len(raw) > 50_000

    out = safe_for_terminal(raw, keep_newline=True, max_lines=8, max_chars_per_line=200)

    assert out.count("\n") + 1 <= 9  # 8 content lines + the note
    assert len(out) < 2_000
    assert all(len(line) <= 201 for line in out.split("\n"))


def test_truncation_is_announced_and_counts_what_it_dropped() -> None:
    """SABOTAGE: set ``truncation_note=False`` as the default.

    Silent clipping is the failure: a reader who cannot tell they are looking at
    a fragment acts on the fragment. This must go RED.
    """

    out = safe_for_terminal("\n".join(str(i) for i in range(20)), keep_newline=True, max_lines=3)

    assert out.startswith("0\n1\n2")
    assert "17 more lines omitted" in out


def test_the_note_says_line_not_lines_when_it_dropped_one() -> None:
    out = safe_for_terminal("a\nb", keep_newline=True, max_lines=1)
    assert "1 more line omitted" in out


def test_a_body_that_already_fits_is_left_completely_alone() -> None:
    """The common case. A 183-byte OAuth JSON error must survive intact."""

    body = '{\n  "error": "expired_token",\n  "error_description": "The device code expired."\n}'
    out = safe_for_terminal(body, keep_newline=True, max_lines=8, max_chars_per_line=200)
    assert out == body


def test_max_chars_is_the_final_backstop() -> None:
    out = safe_for_terminal("x" * 100, max_chars=10)
    assert out == "x" * 10 + "…"


# ── properties the callers rely on ───────────────────────────────────────────


def test_the_strip_is_idempotent() -> None:
    """Callers bound at two altitudes (raise site and render site); the strip must not drift."""

    for text in (HOSTILE, "a\x9bb", "‮x‬", "파일 👩‍💻", "a\nb"):
        once = safe_for_terminal(text, keep_newline=True)
        assert safe_for_terminal(once, keep_newline=True) == once


def test_bounds_never_grow_the_text() -> None:
    """Monotone, so double application at two altitudes cannot inflate a payload."""

    for text in ("short", "a\nb\nc", "x" * 500, HOSTILE):
        out = safe_for_terminal(text, keep_newline=True, max_lines=8, max_chars_per_line=200)
        assert len(out) <= len(text) + len("… (999 more lines omitted)")


def test_contains_steering_chars_agrees_with_what_the_strip_removes() -> None:
    """The two must not drift — a consent prompt REFUSES on this predicate.

    SABOTAGE: give ``contains_steering_chars`` its own literal table. Any
    divergence (C1, BiDi, the ``keep_*`` exemptions) must go RED.
    """

    for text in (HOSTILE, "a\x9bb", "⁦x⁩", "a b", "plain", "a\nb", "a\tb"):
        for keep_newline in (False, True):
            for keep_tab in (False, True):
                flagged = contains_steering_chars(
                    text, keep_newline=keep_newline, keep_tab=keep_tab
                )
                changed = (
                    safe_for_terminal(text, keep_newline=keep_newline, keep_tab=keep_tab) != text
                )
                assert flagged == changed, (text, keep_newline, keep_tab)


def test_the_tls_remedy_from_issue_99_survives_the_default_error_bounds() -> None:
    """A real multi-line message the SAME render site prints — it must not be flattened.

    ``describe_provider_error``'s untrusted-issuer hint is deliberately 5 lines
    with an indented ``export SSL_CERT_FILE=…``. A one-row helper deletes the
    remedy outright, which is why this layer takes a LINE bound and not a
    flatten-to-one-row contract.
    """

    import ssl

    from aelix_ai.providers._error_hints import describe_provider_error

    hint = describe_provider_error(
        ssl.SSLCertVerificationError(
            "certificate verify failed: unable to get local issuer certificate"
        )
    )
    assert hint.count("\n") >= 2, "guard: the remedy is supposed to be multi-line"

    out = safe_for_terminal(hint, keep_newline=True, max_lines=8, max_chars_per_line=200)
    assert "SSL_CERT_FILE" in out
    assert out.count("\n") == hint.count("\n")
