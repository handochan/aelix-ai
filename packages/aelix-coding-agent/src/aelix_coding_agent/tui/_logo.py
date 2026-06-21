"""Aelix terminal logo — the block-art header shown at TUI startup.

Only this text/Unicode block art is part of the runtime; the brand image logo
(JPG/SVG) is intentionally NOT shipped or referenced here (it is undecided).
The art is embedded as a module string constant — not a packaged data file —
so it lands in the built wheel with no ``package-data`` / ``MANIFEST.in``
configuration. The TUI styles it with Rich, so it degrades cleanly on
no-color terminals.
"""

from __future__ import annotations

# Block-art "AELIX" (UTF-8 box-drawing). Trailing whitespace is stripped per
# line (right-side padding is invisible) so the source stays ruff-clean; the
# leading spaces are significant (they center the glyphs).
_LOGO_LINES = (
    "  █████╗ ███████╗██╗     ██╗██╗  ██╗",
    " ██╔══██╗██╔════╝██║     ██║╚██╗██╔╝",
    " ███████║█████╗  ██║     ██║ ╚███╔╝",
    " ██╔══██║██╔══╝  ██║     ██║ ██╔██╗",
    " ██║  ██║███████╗███████╗██║██╔╝ ██╗",
    " ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚═╝  ╚═╝",
)

LOGO_ART = "\n".join(_LOGO_LINES)
"""The multi-line block-art logo (no trailing newline)."""

# Sprint 6h₃₁ (ADR-0164) — per-line 24-bit truecolor gradient (cyan → blue →
# purple), reproducing ``docs/assets/aelix-terminal-logo.ansi``. The colour
# stops are embedded as code (NOT the docs/ asset file, which is not packaged)
# so the gradient ships in the wheel; ``Text.from_ansi`` renders the SGR escapes
# and downgrades cleanly on no-color terminals.
_LOGO_GRADIENT = (
    (0, 242, 254),
    (0, 204, 255),
    (0, 153, 255),
    (51, 102, 255),
    (102, 51, 255),
    (153, 0, 255),
)
LOGO_ANSI = "\n".join(
    f"\x1b[38;2;{r};{g};{b}m{line}\x1b[0m"
    for line, (r, g, b) in zip(_LOGO_LINES, _LOGO_GRADIENT, strict=True)
)
"""The block-art logo with an embedded per-line truecolor gradient (24-bit)."""

LOGO_TITLE = "Aelix Agent Runtime"
"""Product title rendered under the block art (contains ``Aelix``)."""

LOGO_TAGLINE = "small kernel | extension platform | policy-first execution"
"""One-line positioning tagline rendered dim under the title."""

__all__ = ["LOGO_ANSI", "LOGO_ART", "LOGO_TAGLINE", "LOGO_TITLE"]
