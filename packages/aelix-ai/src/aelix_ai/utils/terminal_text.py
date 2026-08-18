"""Make an untrusted string safe to print on a terminal — cluster [20]'s one helper.

WHY THIS EXISTS. Issue #184's reporter pasted a full GitHub "Unicorn" HTML page
because a Copilot device-flow error interpolated ``response.text`` into an
exception message that went straight to the screen. Measured on a real
``rich``-rendered pty, the consequences are not cosmetic:

* ``rich`` strips exactly five control codes — BEL, BS, VT, FF, CR. **ESC is not
  one of them**, and neither is the one-byte C1 CSI (0x9B) or NUL. So
  ``\\x1b[2J`` reaches the emulator and **erases the transcript**;
  ``\\x1b]52;c;…`` writes the user's clipboard; ``\\x1b[?1049h`` takes the screen.
* Because ``rich`` removes BEL (an OSC *terminator*) but passes ``ESC]`` (the
  *introducer*), it converts a bounded title-set into an **unterminated** OSC
  that swallows whatever follows. Stripping the introducer — i.e. all of C0 — is
  what actually helps; a targeted ``\\x07`` filter makes it worse.

WHY IT LIVES IN ``aelix_ai``. Terminal safety is a property of every string every
band prints, so the helper has to sit where all of them can reach it, and
``aelix_ai`` is the bottom of the dependency graph. It is also the only band that
can be imported from the five raw-body sites that need it
(``oauth/github_copilot``, ``oauth/openai_codex``, ``oauth/anthropic``) — the
existing copy in ``aelix_coding_agent.tui.commands`` cannot be, because
``aelix_ai`` may not import upward. Placement was measured against
``tests/agents/test_p2_band_boundaries.py`` with the gate armed: here is green,
product-core and kernel are RED.

WHY IT TAKES NO CELL WIDTH. A cell-exact bound needs ``rich.cells.cell_len``, and
``rich`` is an OPTIONAL extra (``aelix-coding-agent[tui]``) that ``aelix_ai`` does
not depend on. A stdlib approximation here would disagree with the renderer,
which is the off-by-one the drawing code already refuses to have. Cell-exact
truncation is a separate concern that belongs beside the renderer; this layer
bounds **code points and lines**, which is what stops a 55 KB body.

WHAT IT DELIBERATELY DOES NOT DO. It does not try to EXTRACT meaning — no
``<title>`` scrape, no JSON ``error_description`` rung. That would put a parser
on hostile input *inside* the security helper, the extracted text would still be
attacker-controlled and need neutering anyway, and it fails exactly where you
need it (a hostile body matches no extractor). If a caller wants a prettier
message, that composes on top: ``safe_for_terminal(describe_provider_error(exc))``.

AND WHAT IT CANNOT DO. After neutering, a hostile body can still *read* like
``✔ signed in``. Stripping controls stops a body from STEERING the terminal; it
does not stop it from LYING. Callers keep their own ``✖`` prefix and should label
quoted material as the server's words.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "Controls",
    "contains_steering_chars",
    "safe_error_for_terminal",
    "safe_for_terminal",
]


class Controls(Enum):
    """What to do with a control character."""

    DELETE = "delete"
    """Drop it. An escape sequence collapses to its inert literal (``[2J``)."""

    SPACE = "space"
    """Replace with U+0020, so a word boundary survives where one mattered."""


def _steering_code_points(*, keep_newline: bool, keep_tab: bool) -> frozenset[int]:
    """Every code point that can move a cursor, retitle a window, or lie about order.

    Assembled rather than written out so the two ``keep_*`` exemptions cannot
    drift between the strip table and :func:`contains_steering_chars`.
    """

    points = set(range(0x00, 0x20))  # C0. ESC (0x1B) is the one that matters most.
    points.add(0x7F)  # DEL
    points |= set(range(0x80, 0xA0))  # C1 — 0x9B IS CSI, 0x9D IS OSC, 0x85 is NEL.
    # BiDi overrides and isolates. Not in ANY of the nine tables this repo already
    # had, and the only class here that changes what a human READS rather than
    # what the emulator DOES: U+202E renders `trusted=false` as `trusted=true`.
    points |= {0x202A, 0x202B, 0x202C, 0x202D, 0x202E}
    points |= {0x2066, 0x2067, 0x2068, 0x2069}
    # Unicode line/paragraph separators: `str.splitlines()` breaks on these but
    # `split("\n")` does not, so anything that counts lines disagrees with
    # anything that renders them.
    points |= {0x2028, 0x2029}
    # Zero-width, MINUS the joiner. ZWSP is a homograph aid (it splits a word so
    # a human reads `github.com` in a string that is not that) and BOM mid-string
    # is junk; neither composes anything.
    #
    # 🔴 U+200D ZWJ IS DELIBERATELY ABSENT, against the obvious "strip all
    # zero-width" rule. Measured: deleting it turns `👩‍💻` into `👩💻` — two
    # glyphs where the author wrote one. ZWJ cannot move a cursor, retitle a
    # window, or reverse reading order; its only cost is that terminal width
    # arithmetic over it is inexact, and that is the RENDERER's problem, solved
    # beside the renderer where cell widths are actually known. A security helper
    # that corrupts legitimate text to make someone else's arithmetic easier has
    # made a trade it was not asked to make.
    points |= {0x200B, 0xFEFF}

    if keep_newline:
        points.discard(0x0A)
    if keep_tab:
        points.discard(0x09)
    return frozenset(points)


def contains_steering_chars(text: str, *, keep_newline: bool = False, keep_tab: bool = False) -> bool:
    """True when ``text`` holds anything :func:`safe_for_terminal` would remove.

    For callers that must REFUSE rather than sanitise — a consent prompt cannot
    quietly rewrite the thing it is asking the user to approve.
    """

    steering = _steering_code_points(keep_newline=keep_newline, keep_tab=keep_tab)
    return any(ord(ch) in steering for ch in text)


def _strip(text: str, *, controls: Controls, keep_newline: bool, keep_tab: bool) -> str:
    steering = _steering_code_points(keep_newline=keep_newline, keep_tab=keep_tab)
    replacement = " " if controls is Controls.SPACE else ""
    return "".join(replacement if ord(ch) in steering else ch for ch in text)


def safe_for_terminal(
    text: str,
    *,
    controls: Controls = Controls.DELETE,
    keep_newline: bool = False,
    keep_tab: bool = False,
    max_lines: int | None = None,
    max_chars_per_line: int | None = None,
    max_chars: int | None = None,
    marker: str = "…",
    truncation_note: bool = True,
) -> str:
    """Strip steering characters, then bound the result.

    ORDER IS LOAD-BEARING, and it is strip-then-truncate. Truncating first spends
    the budget on bytes that render as nothing, and can cut an escape sequence in
    half — leaving a *different*, still-active sequence.

    ``max_lines`` only means anything with ``keep_newline=True``; without it there
    is exactly one line by construction. ``max_chars`` is the final backstop and
    is applied last, so it bounds the note as well.

    ``truncation_note`` appends ``… (N more lines omitted)`` when whole lines were
    dropped. It is on by default because silent clipping is the failure mode:
    a reader who cannot tell that they are looking at a fragment will act on the
    fragment.

    IDEMPOTENCE, precisely: the STRIP is idempotent — no output of this function
    contains anything the next call would remove. The BOUNDS are monotone, never
    growing the text, but re-applying a *line* bound to output that already
    carries a note recomputes the note. Callers that bound at two altitudes (at
    the raise site and again at the render site) should therefore use a looser
    char bound below and the line bound above, which is what the OAuth callers do.
    """

    cleaned = _strip(text, controls=controls, keep_newline=keep_newline, keep_tab=keep_tab)

    if max_chars_per_line is not None:
        cleaned = "\n".join(
            line if len(line) <= max_chars_per_line else line[:max_chars_per_line] + marker
            for line in cleaned.split("\n")
        )

    if max_lines is not None:
        lines = cleaned.split("\n")
        if len(lines) > max_lines:
            dropped = len(lines) - max_lines
            cleaned = "\n".join(lines[:max_lines])
            if truncation_note:
                noun = "line" if dropped == 1 else "lines"
                cleaned = f"{cleaned}\n{marker} ({dropped} more {noun} omitted)"

    if max_chars is not None and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + marker

    return cleaned


def safe_error_for_terminal(text: str) -> str:
    """The bounds an error message gets on its way to a terminal.

    A FUNCTION rather than a pair of exported constants, deliberately. The band
    gate (``tests/agents/test_p2_band_boundaries.py``) forbids product-core from
    declaring cap-shaped module constants, and its own comment says why the two
    obvious dodges are wrong: renaming hides what the number is, and a leading
    underscore exempts it silently. Its stated remedy — "the numbers live where
    the band rule wants a policy number anyway" — is this. The TUI call sites
    name the intent; the magnitudes live down here.

    The three numbers, each earned by a measurement:

    * ``keep_newline=True`` — the #99 TLS remedy rendered by the SAME call site
      is five lines with an indented ``export SSL_CERT_FILE=…``. Flattening to one
      row deletes the remedy, which is the whole point of the message.
    * ``max_lines=8`` — the body #184's reporter received was 164 lines; at 80
      columns the full page is ~815 rendered rows and takes the transcript with
      it. Eight leaves the first useful lines and a status line still on screen.
    * ``max_chars_per_line=200`` — enough that a one-line JSON OAuth error
      (measured: 183 characters) passes through untouched, short enough that a
      minified HTML page cannot occupy the screen on a single line.
    """

    return safe_for_terminal(text, keep_newline=True, max_lines=8, max_chars_per_line=200)
