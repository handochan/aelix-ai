"""Sprint 6h₁₀a (ADR-0104) / 6h₁₀b (ADR-0105) — streamed-text windowing.

aider ``mdstream.py`` parity: **stable** lines are committed to scrollback while
a small **trailing window** stays live, with an *adaptive throttle* so fast token
streams do not thrash the renderer (ADR-0088 Q10 — "~30 FPS max").

Sprint 6h₁₀b rework (ADR-0105): the live region is now owned by the
prompt-toolkit chrome (a continuously-running ``Application``), so Rich ``Live``
can no longer drive it (they would both fight for the terminal's one bottom
region + cursor). ``StreamRenderer`` is therefore **sink-based**: it keeps the
window/throttle logic but emits through two injected sinks —

- ``commit(ansi)`` — newly-stable ANSI lines → scrollback (the chrome's
  ``print_above`` pump in production; a list in tests).
- ``set_tail(ansi)`` — the live trailing window → the chrome stream widget
  (``""`` clears it on ``final``).

Both sinks are synchronous (called from the synchronous harness subscribe sink);
the async ``in_terminal`` flush happens in the chrome output pump, decoupled via
a queue so ordering is preserved.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


#: #177 — BiDi and format controls that may not reach the terminal from PROSE:
#: U+202A-202E, U+2066-2069, the marks U+200E/200F/061C (the shared helper keeps
#: the marks), U+2028/2029, U+200B and U+FEFF. Markdown DECODES ``&#8238;`` into
#: a raw U+202E after every strip that ran on the source (measured on 33b6c43f:
#: live tail, committed block and replay), so :func:`markdown_lines` drops them
#: from what it rendered. U+200D (ZWJ) is kept: it composes ``👩‍💻``.
_FORMAT_CONTROLS = dict.fromkeys(
    [*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C]
    + [0x2028, 0x2029, 0x200B, 0xFEFF]
)


def drop_format_controls(text: str) -> str:
    """*text* without :data:`_FORMAT_CONTROLS`.

    Per CHARACTER, so it commutes with concatenation the way the streamed
    answer needs (``render._safe_prose``), and it never touches an escape
    sequence, so it is safe to run over rendered ANSI.
    """

    return text.translate(_FORMAT_CONTROLS)


_FORMAT_CONTROLS_AS_SPACE = dict.fromkeys(_FORMAT_CONTROLS, " ")


def space_format_controls(text: str) -> str:
    """*text* with each of :data:`_FORMAT_CONTROLS` replaced by a SPACE.

    For text whose LENGTH must not change: an extension component's ``.plain``,
    under style spans that index into it (``render.component_to_text``).
    """

    return text.translate(_FORMAT_CONTROLS_AS_SPACE)


def plain_lines(text: str, width: int, *, style: str = "") -> list[str]:
    """Render *text* as PLAIN styled lines wrapped at *width* (no markdown).

    The reasoning counterpart to :func:`markdown_lines`. Thinking is not
    markdown: it is emitted as prose, it is shown dim italic, and running it
    through a Markdown renderer would restyle it (headings, bullets, code spans)
    and lose that. Kept next to its sibling so the two rendering modes are one
    read apart rather than one in ``stream.py`` and one improvised elsewhere.
    """

    if not text:
        return []
    buf = io.StringIO()
    # ``legacy_windows=False`` pinned as in ``approval_dialog._panel_to_ansi``
    # (issue #206): rich auto-detects that flag ONCE off the process stdout
    # (``rich/console.py:562-577``) and a missing Windows VT bit then rewrites
    # ROUNDED boxes to SQUARE anywhere in the process. This console only ever
    # writes into a ``StringIO`` whose ANSI is re-parsed downstream
    # (``render.py``'s ``Text.from_ansi`` → ``chrome.print_above``), so pinning
    # is free here. HONEST SCOPE: nothing on THIS path draws a substituted box
    # today — measured, rich renders markdown tables with ``box.SIMPLE``, which
    # is not in ``LEGACY_WINDOWS_SUBSTITUTIONS``. It is here so all four buffer
    # consoles carry one rule and a future Panel cannot regress silently.
    # NOT applied to ``chrome.py``'s live console, which writes to the real
    # terminal and would then emit raw ANSI at a host that cannot read it.
    Console(file=buf, force_terminal=True, width=width, legacy_windows=False).print(
        Text(text, style=style), end=""
    )
    return buf.getvalue().splitlines(keepends=True)


def markdown_lines(text: str, width: int) -> list[str]:
    """Markdown-render *text* at *width* into ANSI lines (``keepends=True``).

    The single place this project turns assistant prose into styled output.
    Extracted from :meth:`StreamRenderer._render_lines` for issue #164, where
    ``EventRenderer.replay`` was committing the raw markdown SOURCE — literal
    ``**bold**`` and literal backticks — while claiming a resumed transcript
    "looks identical to a freshly-streamed one".

    Returns ``[]`` for input that renders to nothing. That is not the same as
    empty input: ``<!-- hidden -->``, a link-reference definition and a footnote
    definition are all non-blank yet produce zero rendered lines, and the live
    path commits nothing for them (``stream.py``'s ``if new_stable:`` never
    fires). A caller that guards on ``text.strip()`` instead of on this return
    value emits a stray blank line the live path does not.
    """

    if not text:
        return []
    buf = io.StringIO()
    # ``legacy_windows=False`` pinned for the reason given in
    # :func:`plain_lines` above (issue #206) — same honest scope: markdown
    # tables render with ``box.SIMPLE`` today, so this is the one-rule pin,
    # not a fix for a box that currently downgrades.
    Console(file=buf, force_terminal=True, width=width, legacy_windows=False).print(
        Markdown(text), end=""
    )
    # #177 — AFTER rendering, because rendering is where ``&#8238;`` becomes
    # U+202E (see :data:`_FORMAT_CONTROLS`), and BEFORE ``splitlines``, which
    # breaks on U+2028/2029 and would otherwise count a decoded ``&#8232;`` as a
    # line of its own. The live tail, the committed lines and the replay all
    # come out of this one function.
    return drop_format_controls(buf.getvalue()).splitlines(keepends=True)


class StreamRenderer:
    """Windowed streamed-text renderer (sink-based; no Rich ``Live``).

    :param commit: sync sink for newly-stable ANSI line text (→ scrollback).
    :param set_tail: sync sink for the live trailing-window ANSI text (→ chrome).
    :param width: render width (snapshotted for the stream's lifetime).
    :param live_window: trailing lines kept live instead of committed.
    :param min_delay/max_delay: adaptive throttle bounds (seconds).
    :param time_fn: monotonic clock; injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        commit: Callable[[str], None],
        set_tail: Callable[[str], None],
        width: int = 80,
        live_window: int = 12,
        min_delay: float = 0.1,
        max_delay: float = 2.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        # Sprint 6h₂₄ v2 — flicker fix tier 2. Defaults changed:
        # - ``live_window`` 6 → 12: a stable-line commit triggers an
        #   ``in_terminal`` suspend on the chrome (flicker frame); doubling the
        #   window halves how often that happens during a long stream — by the
        #   time text reaches 12 visible lines the assistant is usually winding
        #   down anyway, so most streams now commit zero or one batch instead
        #   of three to five.
        # - ``min_delay`` 1/20 → 0.1 (20 FPS → 10 FPS floor): the floor caps
        #   how often ``set_tail`` repaints the chrome's stream widget. Above
        #   the human flicker threshold (~16 Hz) the eye perceives the
        #   widget-growth + cursor-jitter as continuous "thrash" rather than a
        #   smooth update. 10 FPS still feels live for prose; below that the
        #   adaptive bump (render_time × 10) raises the gap further on slow
        #   markdown renders.
        self._commit = commit
        self._set_tail = set_tail
        self._width = max(1, width)
        self._live_window = max(0, live_window)
        self._floor = min_delay
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._time = time_fn

        self._printed: list[str] = []  # lines already committed to scrollback
        self._when: float = 0.0
        self._started: bool = False
        self._stopped: bool = False

    @property
    def min_delay(self) -> float:
        return self._min_delay

    @property
    def stopped(self) -> bool:
        return self._stopped

    def update(self, text: str, *, final: bool = False) -> None:
        """Render the full accumulated ``text``; ``final=True`` flushes + closes."""

        if self._stopped:
            return
        if not self._started and not text:
            if final:
                self._stopped = True
            return

        now = self._time()
        if not final and self._started and (now - self._when) < self._min_delay:
            return  # throttle: coalesce deltas arriving faster than min_delay
        self._when = now
        self._started = True

        start = self._time()
        lines = self._render_lines(text)
        render_time = self._time() - start
        self._min_delay = _clamp(render_time * 10, self._floor, self._max_delay)

        num_stable = len(lines) if final else max(0, len(lines) - self._live_window)
        new_stable = lines[len(self._printed) : num_stable]
        if new_stable:
            self._commit("".join(new_stable))
            self._printed = lines[:num_stable]

        if final:
            self._set_tail("")
            self._stopped = True
            return
        self._set_tail("".join(lines[num_stable:]))

    def _render_lines(self, text: str) -> list[str]:
        # aider mdstream parity: the FULL accumulated text is re-rendered each
        # delta; only the stable prefix above the live window is committed, so
        # partial-markdown volatility (unclosed fences, forming tables) rides in
        # the live window.
        return markdown_lines(text, self._width)


__all__ = [
    "StreamRenderer",
    "drop_format_controls",
    "markdown_lines",
    "plain_lines",
    "space_format_controls",
]
