"""Single source of truth for TUI render width (issue #166).

Before this module, ``_RENDER_WIDTH = 80`` was re-declared verbatim in four
modules with no shared import (``shell.py``, ``context.py``, ``descriptors.py``,
``approval_dialog.py``) and 78 / 76 / 72 were all arithmetically derived from it.
No ADR ever justified the 80 — it is the conventional terminal default, adopted
without a decision record.

The width is read LIVE from prompt-toolkit's output, per call, mirroring the
established row-axis precedent :func:`tui.overlay._modal_cap` (which re-reads
``output.get_size().rows`` every render so a modal tracks terminal resize). No
SIGWINCH handling is needed or wanted here: prompt-toolkit already registers the
handler and re-reads its output size every frame, so polling at render time is
both sufficient and the pattern this codebase already ships.

**Read the size from prompt-toolkit and nowhere else.** Rich's ``Console.size``
consults ``$COLUMNS`` *before* the ioctl, while prompt-toolkit's Vt100 output
uses the ioctl on the stdout fd only. Under a stale exported ``$COLUMNS`` — tmux,
CI shells, anything after an ``stty`` — the two disagree, and a caller that mixes
them lays the chrome and the scrollback out at two different widths in the same
frame. That is a failure mode the old fixed 80 did not have. The rule
``aelix_agents.consent`` settled on for the row axis applies verbatim: when two
readings must be combined, take ``min()``, so a stale value can only make the
measurement smaller (never overflow the real terminal).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_coding_agent.tui.chrome import AelixChrome

# These two are module-PRIVATE on purpose, and the reason is not cosmetic.
#
# 1. It is the settled convention for TUI geometry: every one of its siblings is
#    private (``shell._RENDER_WIDTH``, ``context._PICK_MIN_WIDTH`` /
#    ``_PICK_MAX_WIDTH``, ``overlay._MODAL_MIN_HEIGHT`` / ``_MODAL_FALLBACK_CAP``,
#    ``chrome._INPUT_MAX_ROWS``, ``approval_dialog._MAX_BODY_LINES``). The only
#    public constants under ``tui/`` are data — the logo, the theme table.
# 2. This module's public surface is one FUNCTION. A caller with its own budget
#    passes ``max_width=``; nobody needs to import a number. That is what keeps
#    the ceiling overridable without making it a second source of truth.
# 3. ``tests/agents/test_p2_band_boundaries.py`` reads public UPPER_SNAKE names
#    matching ``(^|_)(MAX|MIN)_`` in product-core as candidate DELEGATION caps
#    (ADR-0197). Render geometry is not delegation policy — a different subagent
#    runtime has no opinion about terminal columns — so the honest resolution is
#    to not present it as a policy surface, NOT to amend that allowlist. Note
#    that file's own warning before renaming any of these: a name that hides
#    what the constant is defeats the gate. The underscore here is convention,
#    not camouflage; the names are otherwise unchanged.

# There is deliberately NO floor. The first version of this module had one
# (40 cells, reasoning that a bordered Panel below that has no room for
# content), and it was a self-inflicted repeat of the very bug this module
# exists to fix: on a 30-column terminal it returned 40, the approval Panel was
# rendered 40 cells wide into 30, and prompt-toolkit clipped it. Measured
# through the pyte harness at the time — cols=30 and cols=36 both came back
# with an UNCLOSED frame (no ``╮``/``╯``), identical to the pre-fix screenshot.
# A cramped 30-cell frame is ugly; a clipped one hides what it is asking about.
# Never return more than the terminal has.

#: Ceiling. Full-bleed prose on an ultrawide terminal is measurably harder to
#: read than a bounded measure — the eye loses the next line's start on the
#: return sweep. Capping is a readability decision, not a technical limit;
#: content is WRAPPED at this width, never truncated, so nothing is lost.
_MAX_RENDER_WIDTH = 120

#: Used only when the terminal size cannot be read (pre-run, headless, a
#: raising output). Matches the historical constant so behaviour is unchanged
#: on every path that could not measure anyway.
_FALLBACK_RENDER_WIDTH = 80


def terminal_columns(
    chrome: AelixChrome | None,
    *,
    max_width: int = _MAX_RENDER_WIDTH,
) -> int:
    """Live terminal columns for *chrome*, clamped to a sane render band.

    :param chrome: the running chrome; ``None`` (or one whose output cannot be
        measured) yields :data:`_FALLBACK_RENDER_WIDTH`, still clamped.
    :param max_width: ceiling override for callers with their own budget.
        Never loosens the terminal bound — the result is always ``<=`` the real
        terminal width once it is readable.
    """

    if chrome is None:
        return _clamp(_FALLBACK_RENDER_WIDTH, max_width)
    try:
        columns = chrome.app.output.get_size().columns
    except Exception:  # noqa: BLE001 — size unavailable (pre-run) → fallback
        return _clamp(_FALLBACK_RENDER_WIDTH, max_width)
    # A zero/negative reading means "unknown" from the ioctl, not "no room".
    if columns <= 0:
        return _clamp(_FALLBACK_RENDER_WIDTH, max_width)
    return _clamp(columns, max_width)


def _clamp(columns: int, max_width: int) -> int:
    """The smaller of the terminal and the caller's budget, never below 1.

    The 1 is not a floor in the design sense — it is the smallest width Rich can
    render into at all. Anything wider than the terminal gets clipped by
    prompt-toolkit with no ellipsis, so there is nothing to trade off against.
    """

    return max(1, min(columns, max_width))


__all__ = ["terminal_columns"]
