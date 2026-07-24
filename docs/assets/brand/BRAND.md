# Aelix Brand Assets

Canonical identity for Aelix. Every vector here is hand-authored SVG — the
files in this directory are the source of truth; there is no external design
file. Selected over five design rounds (2026-07): 7 mark concepts, 7
refinements, 4 constructions of the founder's sketch, and 8 wordmark studies,
each judged by a three-lens panel (brand fit / craft / practicality) with
numeric geometry verification and 16 px rasterization tests.

## Inventory

| File | Use |
| --- | --- |
| `mark.svg` | The mark ("Forged Planes"): A×X in three filled planes. Transparent ground, works on light and dark. |
| `mark-mono.svg` | Single-color mark via `currentColor`. |
| `wordmark-dark.svg` | Title-case `Aelix` for dark grounds (paper letters, glow cursor pixel). |
| `wordmark-light.svg` | For light grounds (ink letters, `#06B6D4` pixel — glow fails contrast on paper). |
| `wordmark-mono.svg` | Single-color wordmark via `currentColor`, pixel included. |
| `lockup-dark.svg` / `lockup-light.svg` | Mark + wordmark, canonical composition. |
| `favicon.svg` / `favicon.ico` | The mark (ico: 16/32/48). |
| `avatar.png` | 512×512 GitHub org avatar (mark on `#0B0F14`). |
| `social-card.png` | 1280×640 social preview. |

## Construction

- **Mark**: three filled planes on a 32-unit modular grid, viewBox 256. Deep
  frame `#0E7490`; one bright strand gradient `#06B6D4 → #22D3EE` woven
  under the crossbar and over the deep leg (paint order = the helix).
- **Wordmark** ("Milled Mono v3"): filled slab polygons, straight lines only
  (no curves anywhere), 44-unit stems, a/e as exact rotational twins, six
  12-unit 45° chamfers, x on the mark's 8:5 slope. Capital A at natural
  title proportions (160×160, 2.7:1 legs, low buried crossbar). The word
  opens with a diagonal-slab A and closes with the mark-sloped x — the
  future A+x→mark merge animation is built into the geometry.
- **Grid kinship**: wordmark x-height 128 = 4 mark modules; ascender 160 = 5.
  Lockup: mark at native scale, ink-bottom on the word baseline, gap 64
  units (2 modules), apex one module above the ascender.

## Rules

- Palette: glow `#22D3EE` · current `#06B6D4` · deep `#0E7490` · ink
  `#0B1220` · ground `#0B0F14` · paper `#F6FAFB`. The ramp is inherited from
  the product's ANSI startup banner.
- One accent event in the word, ever: the 32-unit cursor pixel over the i.
  Never color the x; the mark owns the crossing and the gradient.
- Clear space: 2 mark modules (64 units) on all sides of the lockup. Below
  ~32 px lockup height, use the mark alone.
- Do not stretch, re-color, outline, add effects, or re-space. Regenerate
  raster assets from the SVGs (any renderer; cairosvg was used here).

Full exploration record (all rounds, scores, rejected candidates): see the
project's brand review artifact referenced in `docs/decisions/`.
