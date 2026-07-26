"""Generate the TUI startup banner art from the canonical brand SVGs.

Development-time tool — it is NOT imported at runtime and its dependencies
(``cairosvg`` is not needed; only Pillow) are not runtime dependencies of any
shipped package. It reads ``docs/assets/brand/*.svg`` and rewrites the string
constants in ``aelix_coding_agent/tui/_logo.py``, so the banner can never drift
from the brand assets.

    python scripts/generate_logo_art.py [--check]

Technique — why the art looks crisp instead of mushy:

Shrinking a vector logo onto a character grid normally averages coverage, and a
sub-cell that lands 60% covered still has to become fully on or fully off. That
decision, made after the averaging, is what smears edges. So the geometry is
snapped to the sub-cell lattice *first* — every polygon vertex is rounded to a
lattice point — and then filled with no antialiasing. Edges land exactly on
sub-cell boundaries, which is the same reason hand-drawn block art looks sharp.

Quadrant glyphs (U+2596..U+259F plus the halves and the full block) carry the
sub-cell pattern. They are Unicode 1.1 block elements, so unlike sextants or
octants there is no terminal that fails to draw them. Each cell uses a single
foreground colour and no background paint, which means a terminal that strips
colour still renders the exact silhouette rather than a field of solid blocks.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "assets" / "brand"
TARGET = ROOT / "packages/aelix-coding-agent/src/aelix_coding_agent/tui/_logo.py"

# A character cell displays about one unit wide by two tall, so an image H
# cells high spans 2*H units and its width in cells is aspect * 2 * H.
CELL_ASPECT = 2
SUB_COLS, SUB_ROWS = 2, 2  # quadrant glyphs

QUADRANT = {
    0b0000: " ", 0b0001: "▘", 0b0010: "▝", 0b0011: "▀",
    0b0100: "▖", 0b0101: "▌", 0b0110: "▞", 0b0111: "▛",
    0b1000: "▗", 0b1001: "▚", 0b1010: "▐", 0b1011: "▜",
    0b1100: "▄", 0b1101: "▙", 0b1110: "▟", 0b1111: "█",
}

CURRENT, GLOW = (6, 182, 212), (34, 211, 238)
# Gradient axis of the mark's bright strand, in mark viewBox units.
MARK_GRADIENT = (52, 224, 172, 32, CURRENT, GLOW)

BANNER_ROWS = 10          # the compact cut: 10 rows x 56 cols
NARROW_ROWS = 6           # mark alone, for terminals too narrow for the lockup
WORD_SCALE = 0.62         # wordmark width relative to its natural cell width
WORD_ROW_RATIO = 0.8      # wordmark height relative to the banner height
GAP_CELLS = 2
GRADIENT_STEPS = 8        # quantise the strand ramp; keeps the source compact


# --------------------------------------------------------------- svg parsing

def _subpaths(d: str) -> list[list[tuple[float, float]]]:
    """Split path data into subpaths (the M/L/H/V/Z subset the brand uses)."""
    cur = [0.0, 0.0]
    polys: list[list[tuple[float, float]]] = []
    poly: list[tuple[float, float]] = []
    for tok in re.finditer(r"([MLHVZmlhvz])([^MLHVZmlhvz]*)", d):
        cmd, rest = tok.group(1), tok.group(2)
        nums = [float(v) for v in re.findall(r"-?\d+\.?\d*", rest)]
        if cmd in "Mm":
            if poly:
                polys.append(poly)
                poly = []
            for i in range(0, len(nums), 2):
                cur = ([cur[0] + nums[i], cur[1] + nums[i + 1]] if cmd == "m"
                       else [nums[i], nums[i + 1]])
                poly.append((cur[0], cur[1]))
        elif cmd in "Ll":
            for i in range(0, len(nums), 2):
                cur = ([cur[0] + nums[i], cur[1] + nums[i + 1]] if cmd == "l"
                       else [nums[i], nums[i + 1]])
                poly.append((cur[0], cur[1]))
        elif cmd in "Hh":
            for x in nums:
                cur = [cur[0] + x, cur[1]] if cmd == "h" else [x, cur[1]]
                poly.append((cur[0], cur[1]))
        elif cmd in "Vv":
            for y in nums:
                cur = [cur[0], cur[1] + y] if cmd == "v" else [cur[0], y]
                poly.append((cur[0], cur[1]))
        elif cmd in "Zz" and poly:
            polys.append(poly)
            poly = []
    if poly:
        polys.append(poly)
    return polys


def _paths(svg: str) -> list[tuple[str, str, list[list[tuple[float, float]]]]]:
    """-> [(fill, fill_rule, subpaths)], inheriting ``fill`` from an enclosing
    <g> so the wordmark's grouped letters keep their colour, and carrying the
    fill-rule: the e's counter is even-odd (a hole) while the x's two crossing
    slabs are nonzero (their overlap stays solid)."""
    out = []
    group_fill: str | None = None
    for tok in re.finditer(r"<(g|/g|path)\b([^>]*)>", svg):
        tag, attrs = tok.group(1), tok.group(2)
        if tag == "g":
            f = re.search(r'fill="([^"]+)"', attrs)
            group_fill = f.group(1) if f else group_fill
            continue
        if tag == "/g":
            group_fill = None
            continue
        d = re.search(r'd="([^"]+)"', attrs)
        if not d:
            continue
        f = re.search(r'fill="([^"]+)"', attrs)
        fill = f.group(1) if f else (group_fill or "#000000")
        rule = "evenodd" if 'fill-rule="evenodd"' in attrs else "nonzero"
        out.append((fill, rule, _subpaths(d.group(1))))
    return out


# ------------------------------------------------------------- rasterisation

def snap_render(svg: str, crop, w: int, h: int, gradient=None) -> Image.Image:
    """Rasterise with every vertex rounded to the integer sub-cell lattice."""
    cx, cy, cw, ch = crop
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for fill, rule, subpaths in _paths(svg):
        combine = ((lambda a, b: a ^ b) if rule == "evenodd"
                   else (lambda a, b: a | b))
        mask = Image.new("L", (w, h), 0)
        for sp in subpaths:
            pts = [(round((x - cx) / cw * w), round((y - cy) / ch * h)) for x, y in sp]
            if len(pts) < 3:
                continue
            layer = Image.new("L", (w, h), 0)
            ImageDraw.Draw(layer).polygon(pts, fill=255)
            mask = Image.frombytes("L", (w, h), bytes(
                combine(a, b)
                for a, b in zip(mask.tobytes(), layer.tobytes(), strict=True)))
        if not mask.getbbox():
            continue
        if fill.startswith("url(") and gradient:
            gx1, gy1, gx2, gy2, c1, c2 = gradient
            dx, dy = gx2 - gx1, gy2 - gy1
            den = dx * dx + dy * dy or 1
            paint = Image.new("RGBA", (w, h))
            pp = paint.load()
            for yy in range(h):
                uy = (yy + 0.5) / h * ch + cy - gy1
                for xx in range(w):
                    ux = (xx + 0.5) / w * cw + cx - gx1
                    t = max(0.0, min(1.0, (ux * dx + uy * dy) / den))
                    t = round(t * (GRADIENT_STEPS - 1)) / (GRADIENT_STEPS - 1)
                    pp[xx, yy] = tuple(
                        round(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)) + (255,)
        else:
            paint = Image.new("RGBA", (w, h),
                              tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5)) + (255,))
        img.paste(paint, (0, 0), mask)
    return img


def to_cells(img: Image.Image) -> list[list[tuple[str, tuple[int, int, int]] | None]]:
    """One tone per cell and no background paint, so the glyph bits carry ALL
    the ink: a terminal that drops colour still shows the exact silhouette."""
    W, H = img.size
    px = img.load()
    grid = []
    for cy in range(0, H, SUB_ROWS):
        row = []
        for cx in range(0, W, SUB_COLS):
            sub = []
            for sy in range(SUB_ROWS):
                for sx in range(SUB_COLS):
                    x, y = cx + sx, cy + sy
                    p = px[x, y] if x < W and y < H else (0, 0, 0, 0)
                    sub.append(p[:3] if p[3] >= 128 else None)
            ink = [s for s in sub if s is not None]
            if not ink:
                row.append(None)
                continue
            fg = max(set(ink), key=ink.count)
            bits = sum(1 << i for i, s in enumerate(sub) if s is not None)
            row.append((QUADRANT[bits], fg))
        grid.append(row)
    return grid


def compose(rows: int, with_word: bool) -> list[list[tuple[str, tuple[int, int, int]] | None]]:
    mark_svg = (BRAND / "mark.svg").read_text()
    mark_cells = round(208 / 200 * rows * CELL_ASPECT)
    mark = to_cells(snap_render(mark_svg, (24, 28, 208, 200),
                                mark_cells * SUB_COLS, rows * SUB_ROWS,
                                gradient=MARK_GRADIENT))
    if not with_word:
        return mark

    word_svg = (BRAND / "wordmark-dark.svg").read_text()
    word_rows = max(1, round(rows * WORD_ROW_RATIO))
    word_cells = round(628 / 190 * word_rows * CELL_ASPECT * WORD_SCALE)
    word = to_cells(snap_render(word_svg, (26, 8, 628, 190),
                                word_cells * SUB_COLS, word_rows * SUB_ROWS))

    total = mark_cells + GAP_CELLS + word_cells
    grid: list[list] = [[None] * total for _ in range(rows)]
    for ry, r in enumerate(mark):
        for cx, cell in enumerate(r):
            if cx < mark_cells:
                grid[ry][cx] = cell
    off = rows - len(word)
    for ry, r in enumerate(word):
        for cx, cell in enumerate(r):
            if mark_cells + GAP_CELLS + cx < total:
                grid[off + ry][mark_cells + GAP_CELLS + cx] = cell
    return grid


# ------------------------------------------------------------------ emitters

def plain_lines(grid) -> list[str]:
    return ["".join(c[0] if c else " " for c in row).rstrip() for row in grid]


def ansi_lines(grid) -> list[str]:
    """Truecolor SGR only where the colour actually changes, then one reset per
    line — the art is a few hundred cells, so the run-length saving matters."""
    out = []
    for row in grid:
        parts: list[str] = []
        pen: tuple[int, int, int] | None = None
        trailing = 0  # blanks are only emitted once something follows them
        for cell in row:
            if cell is None:
                trailing += 1
                continue
            glyph, fg = cell
            parts.append(" " * trailing)
            trailing = 0
            if fg != pen:
                parts.append(f"\x1b[38;2;{fg[0]};{fg[1]};{fg[2]}m")
                pen = fg
            parts.append(glyph)
        out.append("".join(parts) + ("\x1b[0m" if pen else ""))
    return out


MODULE = '''"""Aelix terminal logo — the startup banner art.

GENERATED by ``scripts/generate_logo_art.py`` from ``docs/assets/brand/*.svg``;
edit the brand SVGs and re-run that script rather than editing this file. The
art is embedded as plain string constants — not a packaged data file — so it
lands in the built wheel with no ``package-data`` / ``MANIFEST.in`` config and
no runtime dependency: printing the banner is printing a string.

The glyphs are quadrant block elements (U+2596..U+259F and friends), which are
Unicode 1.1 and therefore drawn by every terminal — unlike the finer sextant
and octant mosaics, which several major terminals render as tofu. Each cell
carries one foreground colour and no background paint, so a terminal that
strips colour still shows the exact silhouette. The TUI renders this through
``Text.from_ansi``, which degrades cleanly on no-color terminals.
"""

from __future__ import annotations

# The compact lockup: the Forged Planes mark beside the Aelix wordmark.
_LOGO_LINES = (
{plain}
)

LOGO_ART = "\\n".join(_LOGO_LINES)
"""The multi-line block art, no colour (no trailing newline)."""

LOGO_ANSI = "\\n".join((
{ansi}
))
"""The block art with embedded 24-bit truecolor SGR escapes."""

# The mark alone, for terminals too narrow to fit the lockup.
_NARROW_LINES = (
{plain_narrow}
)

LOGO_ART_NARROW = "\\n".join(_NARROW_LINES)
"""The mark-only art, no colour."""

LOGO_ANSI_NARROW = "\\n".join((
{ansi_narrow}
))
"""The mark-only art with truecolor SGR escapes."""

LOGO_WIDTH = {width}
"""Columns the full lockup occupies; below this the narrow art is used."""

LOGO_NARROW_WIDTH = {narrow_width}
"""Columns the mark-only art occupies."""

LOGO_TAGLINE = "Agent Runtime \\u00b7 small kernel | extension platform | policy-first execution"
"""Positioning line under the banner. The art already spells the brand name,
so this line does not repeat it."""

LOGO_TAGLINE_NARROW = "Aelix \\u00b7 Agent Runtime"
"""Narrow-terminal caption; the mark-only art does not spell the name."""

__all__ = [
    "LOGO_ANSI",
    "LOGO_ANSI_NARROW",
    "LOGO_ART",
    "LOGO_ART_NARROW",
    "LOGO_NARROW_WIDTH",
    "LOGO_TAGLINE",
    "LOGO_TAGLINE_NARROW",
    "LOGO_WIDTH",
]
'''


def render_module() -> str:
    wide = compose(BANNER_ROWS, with_word=True)
    narrow = compose(NARROW_ROWS, with_word=False)

    def quoted(lines: list[str]) -> str:
        rows = []
        for line in lines:
            escaped = line.encode("unicode_escape").decode("ascii").replace('"', '\\"')
            rows.append(f'    "{escaped}",')
        return "\n".join(rows)

    return MODULE.format(
        plain=quoted(plain_lines(wide)),
        ansi=quoted(ansi_lines(wide)),
        plain_narrow=quoted(plain_lines(narrow)),
        ansi_narrow=quoted(ansi_lines(narrow)),
        width=max(len(r) for r in plain_lines(wide)),
        narrow_width=max(len(r) for r in plain_lines(narrow)),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the checked-in module is stale")
    args = ap.parse_args()

    generated = render_module()
    if args.check:
        current = TARGET.read_text() if TARGET.exists() else ""
        if current != generated:
            print(f"{TARGET} is stale — re-run scripts/generate_logo_art.py", file=sys.stderr)
            return 1
        print(f"{TARGET} is up to date")
        return 0

    TARGET.write_text(generated)
    print(f"wrote {TARGET} ({len(generated)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
