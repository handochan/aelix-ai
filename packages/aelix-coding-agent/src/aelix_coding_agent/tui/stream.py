"""Sprint 6h₁₀a (ADR-0104) — streaming output renderer for the TUI shell.

aider ``mdstream.py`` parity (``Aider-AI/aider``): a Rich :class:`~rich.live.Live`
region where **stable** lines are committed to terminal scrollback while a small
**trailing window** stays repainted, with an *adaptive throttle* so fast token
streams do not thrash the renderer (ADR-0088 Q10 — "~30 FPS max").

The caller passes the *full accumulated text* on every :meth:`StreamRenderer.update`
(matching aider's contract); the renderer diffs against what it has already
committed to scrollback so only newly-stable lines are printed once.

Thin-shell scope (Sprint 6h₁₀a): renders **plain text** (:class:`~rich.text.Text`).
Markdown rendering (:class:`~rich.markdown.Markdown`) is a Sprint 6h₁₀b polish
item — the throttle / scrollback machinery here is library-stable and carries
forward unchanged.

Test-safety: the Live region uses ``auto_refresh=False`` + manual
:meth:`~rich.live.Live.refresh` so no background thread is spawned, and the
clock is injectable (``time_fn``) so throttle behaviour is fully deterministic
under a fake clock (no ``time.sleep`` in tests).
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable

from rich.console import Console
from rich.live import Live
from rich.text import Text


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


class StreamRenderer:
    """Incremental streamed-text renderer (aider MarkdownStream parity).

    :param console: the shared output console (→ terminal scrollback).
    :param live_window: trailing lines kept in the repainted Live region.
    :param min_delay: throttle floor in seconds (``1/20`` → 20 FPS ceiling).
    :param max_delay: throttle ceiling — adaptive back-off never exceeds this.
    :param time_fn: monotonic clock; injectable for deterministic tests.
    """

    def __init__(
        self,
        console: Console,
        *,
        live_window: int = 6,
        min_delay: float = 1 / 20,
        max_delay: float = 2.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._console = console
        self._live_window = max(0, live_window)
        self._floor = min_delay
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._time = time_fn

        self._live: Live | None = None
        self._printed: list[str] = []  # lines already committed to scrollback
        self._when: float = 0.0
        self._stopped: bool = False
        # Width is snapshotted at stream start and held for the stream's
        # lifetime: the stable/unstable split below indexes by line position
        # (``_printed``), which a mid-stream re-wrap (terminal resize) would
        # otherwise corrupt. aider fixes the width the same way.
        self._width: int | None = None

    @property
    def min_delay(self) -> float:
        """Current adaptive throttle interval (seconds)."""

        return self._min_delay

    @property
    def stopped(self) -> bool:
        """``True`` once :meth:`update` has been called with ``final=True``."""

        return self._stopped

    def update(self, text: str, *, final: bool = False) -> None:
        """Render the full accumulated ``text``; ``final=True`` flushes + closes.

        Behaviour:

        - Calls arriving faster than the current adaptive ``min_delay`` are
          coalesced (skipped) — except ``final``, which always renders.
        - All but the trailing ``live_window`` lines are committed to scrollback
          exactly once; the trailing lines stay in the repainted Live region.
        - ``final=True`` flushes every remaining line to scrollback, clears and
          stops the Live region. Post-``final`` calls are no-ops (idempotent).
        - ``update("")`` before any content is a no-op.
        """

        if self._stopped:
            return  # idempotent: post-final calls are no-ops
        if self._live is None and not text:
            # No content yet. A bare final marker just closes the (empty) stream.
            if final:
                self._stopped = True
            return

        now = self._time()
        if (
            not final
            and self._live is not None
            and (now - self._when) < self._min_delay
        ):
            return  # throttle: coalesce deltas arriving faster than min_delay
        self._when = now

        if self._live is None:
            self._width = self._console.width or 80
            live = Live(
                Text(""),
                console=self._console,
                auto_refresh=False,  # no background thread → deterministic, test-safe
                transient=False,
            )
            live.start()
            self._live = live
        else:
            live = self._live

        # Render the full accumulated text to a throwaway console to compute
        # wrapped ANSI lines without writing to the real terminal, and measure
        # the render cost for the adaptive back-off (aider heuristic).
        start = self._time()
        lines = self._render_lines(text)
        render_time = self._time() - start
        self._min_delay = _clamp(render_time * 10, self._floor, self._max_delay)

        num_stable = len(lines) if final else max(0, len(lines) - self._live_window)
        new_stable = lines[len(self._printed) : num_stable]
        if new_stable:
            # ``live.console.print`` while the Live is active emits *above* the
            # live region — i.e. into permanent scrollback.
            live.console.print(Text.from_ansi("".join(new_stable)), end="")
            self._printed = lines[:num_stable]

        if final:
            live.update(Text(""))
            live.refresh()
            live.stop()
            self._live = None
            self._stopped = True
            return

        live.update(Text.from_ansi("".join(lines[num_stable:])))
        live.refresh()

    def _render_lines(self, text: str) -> list[str]:
        """Render ``text`` to ANSI lines at the console width (no real I/O)."""

        if not text:
            return []
        width = self._width or self._console.width or 80
        buf = io.StringIO()
        capture = Console(file=buf, force_terminal=True, width=width)
        capture.print(Text(text), end="")
        return buf.getvalue().splitlines(keepends=True)


__all__ = ["StreamRenderer"]
