"""Single source of truth for TUI render width (issue #166).

Before this module, ``_RENDER_WIDTH = 80`` was re-declared verbatim in four
modules with no shared import (``shell.py``, ``context.py``, ``descriptors.py``,
``approval_dialog.py``) and 78 / 76 / 72 were all arithmetically derived from it.
No ADR ever justified the 80 — it is the conventional terminal default, adopted
without a decision record.

:func:`terminal_columns` reads prompt-toolkit's output size on every call — it
holds no cache and snapshots nothing. Tracking a resize is therefore the
CALLER's responsibility, and the distinction is not academic: a caller that
resolves the width once and bakes a Panel from it is worse than a fixed 80,
because the stale rows get clipped by the repaint that the resize triggers.
Callers that stay on screen must pass the reader itself (see
``run_approval_dialog``'s ``width: int | Callable[[], int]``) so it is re-resolved
per render — the row-axis precedent is :func:`tui.overlay._modal_cap`, re-read
from ``_CappedContainer.preferred_height`` every render for exactly this reason.

No SIGWINCH handling belongs here. prompt-toolkit already registers the handler
(``Application.run_async`` → ``attach_winch_signal_handler``) and additionally
polls its output size in the background for terminals that never deliver the
signal, so a render-time read is both sufficient and the pattern already shipped.

**Two layers measure the terminal differently, so this takes the ``min()``.**
Rich's ``Console.size`` consults ``$COLUMNS`` and lets it OVERRIDE the ioctl;
prompt-toolkit's Vt100 output asks the ioctl only. With an exported ``COLUMNS``
that disagrees with the real terminal, the renderer would lay text out at the
ioctl width while the scrollback console re-wrapped those rows at the ``$COLUMNS``
width — producing ragged long/short pairs, and (measured) collapsing the whole
feature back to an 80-cell ribbon when ``COLUMNS=80`` is exported on a 200-column
terminal.

An earlier revision of this module asserted the opposite rule — "read the size
from prompt-toolkit and nowhere else" — which was wrong: the scrollback console
is a consumer this module does not control, so refusing to look at it does not
make the disagreement go away, it just hides it. The row-axis precedent is
``aelix_agents.consent._terminal_rows``, which takes ``min()`` with the written
argument that a stale environment value MAY ONLY MAKE THE MEASUREMENT SMALLER
(finding F4). The same direction-of-error argument holds here: too small merely
wastes columns, too large clips.
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
#: return sweep. Capping is a readability decision, not a technical limit.
#:
#: What the cap costs depends on the surface, and an earlier revision of this
#: comment claimed "content is WRAPPED at this width, never truncated, so
#: nothing is lost" for all of them, which was false:
#:
#: * streamed prose IS wrapped by Rich — lossless, the claim held;
#: * tool-card and diff lines are cut by ``render._truncate_lines`` with a
#:   trailing ``…`` — so on a terminal wider than the ceiling, this number is
#:   the reason a long line is elided rather than shown. The full body is
#:   recoverable with ``/expand``, but it is a truncation, not a wrap.
_MAX_RENDER_WIDTH = 120

#: Used only when the terminal size cannot be read (pre-run, headless, a
#: raising output). Matches the historical constant so behaviour is unchanged
#: on every path that could not measure anyway.
_FALLBACK_RENDER_WIDTH = 80


def terminal_columns(
    chrome: AelixChrome | None,
    *,
    max_width: int | None = None,
) -> int:
    """Live terminal columns for *chrome*, capped for readability.

    :param chrome: the running chrome; ``None`` (or one whose output cannot be
        measured) yields :data:`_FALLBACK_RENDER_WIDTH`, still capped.
    :param max_width: ceiling override — a caller's own budget, or the user's
        ``render_max_width`` setting. ``None`` means "use the built-in default",
        which is why the setting can be stored unset rather than duplicating the
        number: there is exactly one 120 in the codebase and it is below.
        A ceiling never loosens the terminal bound — the result is always ``<=``
        the real terminal width once that is readable.
    """

    if max_width is None:
        max_width = _MAX_RENDER_WIDTH
    if chrome is None:
        return _clamp(_FALLBACK_RENDER_WIDTH, max_width)
    try:
        columns = chrome.app.output.get_size().columns
    except Exception:  # noqa: BLE001 — size unavailable (pre-run) → fallback
        return _clamp(_FALLBACK_RENDER_WIDTH, max_width)
    # A zero/negative reading means "unknown" from the ioctl, not "no room".
    if columns <= 0:
        return _clamp(_FALLBACK_RENDER_WIDTH, max_width)
    # ...and the scrollback console gets a vote, because it re-wraps whatever we
    # emit. ``0`` from it means "no opinion" (degraded/absent), not "no room".
    console_columns = _scrollback_columns(chrome)
    if console_columns > 0:
        columns = min(columns, console_columns)
    return _clamp(columns, max_width)


def _scrollback_columns(chrome: AelixChrome) -> int:
    """The chrome's Rich-console width, or ``0`` when it has no opinion."""

    try:
        return int(chrome.scrollback_columns)
    except Exception:  # noqa: BLE001 — older/degraded chrome → no opinion
        return 0


def _clamp(columns: int, max_width: int) -> int:
    """The smaller of the terminal and the caller's budget, never below 1.

    The 1 is not a floor in the design sense — it is the smallest width Rich can
    render into at all. Anything wider than the terminal gets clipped by
    prompt-toolkit with no ellipsis, so there is nothing to trade off against.
    """

    return max(1, min(columns, max_width))


__all__ = ["terminal_columns"]
