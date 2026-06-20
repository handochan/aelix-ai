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

LOGO_TITLE = "Aelix Agent Runtime"
"""Product title rendered under the block art (contains ``Aelix``)."""

LOGO_TAGLINE = "small kernel | extension platform | policy-first execution"
"""One-line positioning tagline rendered dim under the title."""

__all__ = ["LOGO_ART", "LOGO_TAGLINE", "LOGO_TITLE"]
