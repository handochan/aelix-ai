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

# Sprint 6h₃₁ (ADR-0164) — per-line 24-bit truecolor gradient, reproducing
# ``docs/assets/aelix-terminal-logo-cyan.ansi``. A sleek all-cyan ramp (icy
# bright cyan → vibrant cyan → deep ocean blue), matching ``print_logo_cyan.py``
# — NOT the earlier cyan→purple ramp (the purple tail read as off-brand). The
# colour stops are embedded as code (NOT the docs/ asset file, which is not
# packaged) so the gradient ships in the wheel; ``Text.from_ansi`` renders the
# SGR escapes and downgrades cleanly on no-color terminals.
_LOGO_GRADIENT = (
    (200, 255, 255),  # icy bright cyan
    (100, 242, 254),  # bright sky cyan
    (0, 220, 240),  # pure vibrant cyan
    (0, 180, 216),  # sleek tealish cyan
    (0, 130, 200),  # electric blue-teal
    (0, 95, 175),  # deep ocean blue
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
