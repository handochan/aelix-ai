# 0228. The echo bar never had a colour, and `bold` was the half that broke it

Status: Accepted (2026-08-18).
Date: 2026-08-18
Relates: ADR-0153 (the first user echo), ADR-0219 (the 120-cell render ceiling),
ADR-0224 (text-pinned citations), ADR-0227 (the five legibility asks, which shipped
this bar).
GitHub: #191. Follows #48, which shipped the bar and is closed.

The owner reported that the bar is hard to read **on some terminals**, and asked for a
cyan-based ground that is more muted — "toned down", with the text emphasised. The
complaint was not that cyan is too bright, and my first answer to it was wrong for a
reason I could have measured and did not.

## The bar had no colour to be too bright

`_USER_ECHO_STYLE` was `bold black on cyan`. Rich emits **one** thing for it —
`\x1b[1;30;46m` — and emits it identically at `truecolor`, `256`, `standard` and
`windows`. SGR 30 and 46 are palette **slots** 0 and 6. Nothing in this repository
decided what a reader sees.

The comment that shipped it argued for exactly that: *"It stays the terminal's OWN cyan
… so it tracks the user's theme."* Three paragraphs earlier the same comment rejected
`reverse` because *"it delegates half the pair to the user's theme, and it is the half
that can go wrong."* The style it was defending delegated both halves.

Measured over 24 published schemes, slot 0 on slot 6 ranges **1.67:1** (Catppuccin Latte)
to 11.41:1 (Dracula). Widened to 38 rows, 11 miss WCAG AA before `bold` is considered and
**eight of those are light schemes** — the case least likely to be checked by someone
developing on a dark one.

## `bold` is what made it unreadable rather than merely uneven

Twelve of seventeen surveyed terminals render SGR 1 combined with a basic 30–37
foreground using the **bright** slot instead:

| brightens by default | does not |
| --- | --- |
| xterm (`boldColors`, default true), urxvt (`intensityStyles`), **Konsole (not configurable)**, mintty, PuTTY, Windows Terminal (`intenseTextStyle: bright`), ConHost, iTerm2 (`Brighten Bold Text`), xterm.js and therefore VS Code, WezTerm, Tilix, the Linux console | VTE / GNOME Terminal (flipped in 0.56), Alacritty (since 0.4.2), kitty (refused upstream), foot, Ghostty |

Where it applies, `bold black` is slot 8 — a mid grey — on a cyan ground:

| scheme | as written | where bold means bright |
| --- | ---: | ---: |
| Gruvbox Dark | 4.65 | **1.16** |
| Campbell (Windows Terminal) | 6.14 | **1.42** |
| xterm | 10.61 | **2.02** |
| VS Code Dark+ | 7.51 | **2.05** |
| Tango / GNOME | 3.59 | **2.08** |
| One Dark | 5.91 | 2.55 |
| Dracula | 11.41 | 3.40 |
| Solarized Dark | 4.12 | 4.75 |

That is the reported bug. The substitution is range-guarded to slots 0–7 in every
implementation checked — xterm exempts SGR 38 by name (`!xw->sgr_38_xcolors`), VTE
range-guards, Konsole's `setIntensive()` no-ops outside the system colour space, iTerm2
comments *"Only colors 0-7 can be made bright."* `foot` with the non-default
`bold-text-in-bright=yes` blends any foreground toward white and is the one exception. So
a pinned foreground closes it — **at the depths where the foreground stays pinned.** See
"no bold" below; that qualifier turned out to matter.

## The axis I ranked seven candidates without

Contrast between the text and the ground says whether the words can be read. It says
nothing about whether the bar **separates from the transcript around it** — and
separation is the entire reason #48 exists: ADR-0227 records the turn reading as "a gap
between coloured tool cards", and the bar was added to give it its own ground. That
property is the ground against the **terminal's own background**, per scheme.

I ranked seven candidates on contrast, recommended a dark teal, and the owner chose it.
An adversarial review of the shortlist found the omission; measuring it confirmed the
recommendation was wrong:

| ground | text | band edge, dark terminals | band edge, light terminals | schemes where the band vanishes |
| --- | ---: | ---: | ---: | ---: |
| as shipped (each theme's slot 6) | theme's own | 6.24 | 3.30 | 0/25 |
| **`#0f3a3f` — my recommendation** | 11.12 | **1.33** | 11.85 | **14/25** |
| Slate veil `#8fadad` | 7.66 | 6.83 | 2.30 | 0/25 |
| **Harbour `#83b0b4` — chosen** | 7.73 | 6.90 | 2.28 | 0/25 |
| Shoal `#74b0b8` | 7.57 | 6.75 | 2.33 | 0/25 |
| Lagoon `#62b0bc` | 7.40 | 6.60 | 2.38 | 0/25 |
| Near today `#50b8c8` | 7.90 | 7.05 | 2.23 | 0/25 |

A dark ground leaves the words perfectly legible and stops the bar being a bar on the
fifteen dark terminals in the sample. **The palette-slot design never had that problem,
and it is the one thing it got right**: its ground is each theme's own slot 6 — a
saturated cyan on that theme's background — so the edge came for free. A pinned ground
has to earn it, and only the light-ink-on-light-cyan family does.

Within that family the choice is a **saturation dial with no cost**: from chroma 30 to
120 every step clears 7:1 on the text, reduces to a teal at 256, and keeps the band on all
25 schemes. Chroma 49 is a taste position, not a constraint.

## Decision

```python
_USER_ECHO_STYLE = "#04171a on #83b0b4"
```

Both ends pinned; today's polarity kept; roughly a third of the old ground's chroma.

### No `bold`, and that is this ground's doing

With a pinned foreground SGR 1 is normally free. But Rich **reduces**, and this pair
reduces to `30;47` — a basic foreground again. Add `bold` and the same twelve terminals
turn slot 0 into slot 8: measured **1.30:1** worst (VS Code Light+) against 2.82:1
without it. A dark ground would have reduced its light ink to slot 15, already bright and
immune. So the rule is not "keep bold" or "drop bold" — it is *at no depth may SGR 1
appear beside a 30–37 foreground*, and that is how it is gated.

## The other candidates, kept

Recorded so a later change does not re-derive them. Every number computed; the 16-colour
columns are the worst of the 24 schemes.

| candidate | text | ground | truecolor | 256 (cube ground) | 16-colour | tmux → 16 | chroma | verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| **Harbour — chosen** | `#04171a` | `#83b0b4` | 7.73 | 8.78 (`#87afaf`) | 2.82 | 1.67 | 49 | — |
| Slate veil | `#04171a` | `#8fadad` | 7.66 | 8.78 (`#87afaf`) | 2.82 | 1.67 | 30 | quieter dial position |
| Shoal | `#04171a` | `#74b0b8` | 7.57 | 8.78 (`#87afaf`) | 2.82 | 1.67 | 68 | louder dial position |
| Lagoon | `#04171a` | `#62b0bc` | 7.40 | 8.24 (`#5fafaf`) | 2.82 | 1.67 | 90 | louder still |
| Near today | `#04171a` | `#50b8c8` | 7.90 | 8.60 (`#5fafd7`) | **1.67** | 1.67 | 120 | not "toned down"; its ground reduces to slot 6, not slot 7 |
| Dusty cyan | `#0c2226` | `#a8c8cc` | 9.27 | 13.52 (`#afd7d7`) | 2.82 | 1.67 | 36 | light edge 1.71, 1/25 vanish |
| Cyan Mist | `#08191c` | `#badede` | 12.51 | 13.52 (`#afd7d7`) | 2.82 | 1.67 | 36 | light edge 1.38, **10/25 vanish** |
| Deep teal wash | `#eef4f4` | `#0f3a3f` | 11.12 | 7.49 (`#005f5f`) | 2.10 | **1.08** | 48 | **14/25 vanish** |
| Ink teal | `#f0f6f6` | `#0a2a2e` | 13.89 | 21.00 (`#000000`) | 3.44 | 2.82 | 36 | **15/25 vanish**; 256 ground goes black |
| Teal graphite | `#eef4f4` | `#26343a` | 11.55 | 7.49 (`#005f5f`) | 2.10 | 1.08 | 20 | **14/25 vanish** |
| Harbour slate | `#eef4f4` | `#30424e` | 9.37 | 6.39 (`#5f5f5f`) | 2.10 | 2.82 | 30 | 7/25 vanish |
| Pinned bright cyan | `#04262a` | `#22b8c4` | 6.63 | 8.11 (`#00afd7`) | 1.67 | not modelled | 162 | today's loudness, pinned |
| *as shipped*, plain | slot 0 | slot 6 | — | — | 1.67 | 1.67 | — | — |
| *as shipped*, bold-is-bright | slot 8 | slot 6 | — | — | 1.16 | 1.16 | — | — |

## What pinning does *not* buy, stated because I first claimed it did

**`standard` goes back to palette slots.** Rich reduces `#04171a on #83b0b4` to `30;47` —
slots 0 and 7 — and `standard` is reached three ordinary ways: `TERM=xterm`,
`TERM=screen`, and an unset `TERM`. Worst of the 24 schemes there is 2.82:1. That is not
a floor and it is close to the ceiling: measured over **every** slot pair Rich can emit,
the best any 16-colour pair guarantees is **3.44:1** (slot 15 on slot 0, worst case
Catppuccin Latte) — and that pair is only reachable from a ground dark enough to stop
being a band. Against the old style's 1.16 it is more than double.

**tmux reduces again, and here it lands on the old bar.** Inside tmux `TERM` is
`tmux-256color`, so Rich resolves **256 even when the outer terminal is truecolor** — the
256 row is what a large share of users actually see, which is why the ground was chosen to
stay teal there. When the outer terminal offers only sixteen colours, tmux re-picks the
nearest slot for **each end separately**. Measured against tmux 3.4 with a 16-colour
client:

```
bg cube  16 → 40   22 → 42   23 → 46   38 → 44   59 → 40   73 → 46   109 → 46   152 → 46   234-237 → 40
fg cube  15 → 37   16 → 30  188 → 37  189 → 34  195 → 36  231 → 37   253-255 → 37
```

Cube 16 → slot 0 and cube 109 → slot 6: **black on cyan, byte-for-byte the bar this
replaces.** A floor rather than a fix, but not a regression — which is what the dark
grounds could not say. Their light ink (cube 195 or 231) and teal ground (cube 23) folded
onto slots 6 and 6, or 7 and 6: **1.00–1.08:1, an invisible bar.**

**tmux forwards the old style untouched** (`30`, `46`, `1` as three separate sequences)
and downconverts a pinned one, so byte-level assertions about the combined SGR do not hold
under tmux either way.

## The colour had no gate at all

Changing the constant from two palette slots to a pinned pair left **201 tests in
`test_event_renderer.py`, 71 in `test_run_tui_smoke.py` and the pyte glass tests all
green.** `_painted_rows` is self-calibrating on the style by design — it says so — and
nothing else looked. The property that broke for real users was the one nobody asserted,
and so was the one that made my first recommendation wrong.

Added, in `test_event_renderer.py`:

- **names a colour and not a palette slot** — no 30–37 / 40–47 / 90–97 / 100–107 in the
  emitted SGR at truecolor, and both 256 indices ≥ 16 (16–255 is the fixed xterm cube;
  0–15 is theme data);
- **clears AAA where it owns both ends** — 7:1 at truecolor and 4.5:1 at 256, computed
  from the *emitted bytes*, not from the constant. 16-colour is deliberately not
  asserted: no hex can hold a floor there;
- **the ground stays a band** — ≥1.7:1 against both `#1e1e1e` and `#ffffff`. Pinned
  against the two extremes rather than a table of themes; measured, a ground that clears
  it keeps a visible band on all 25 schemes, and `#0f3a3f` (1.35:1 against `#1e1e1e`)
  fails it;
- **the 256 foreground is neutral** — `r == g == b` in the cube, the property that stops a
  16-colour reducer folding the text onto the ground's hue. Pinned as a property rather
  than as tmux's table, so it survives tmux changing the table;
- **no depth pairs SGR 1 with a brightenable foreground** — checked at all four colour
  systems, because whether the reduction lands on a basic slot depends on the ground.

And in `test_run_tui_smoke.py`, a live gate: drive `run_tui`, type a turn, replay the
chrome console's own bytes through `pyte`, and read the **cells** back. What it
deliberately does not assert is edge-to-edge coverage per row — the bar is committed to
scrollback while the chrome repaints below it, so pyte holds a screen the two writers
share; coverage is a property of the emitted stream and `_painted_rows` measures it there.

**Every unit gate measures in a fresh subprocess**, which is not fussiness. `Style._add`
is `@lru_cache`d on a value-equal key and the combined style memoises `_ansi` *without
keying on the colour system*. Render this style once at truecolor and every later console
in the process reports truecolor bytes whatever it was constructed with — measured: after
`Style.parse.cache_clear()` a fresh `Console(color_system="256")` still emitted `38;2`,
while `_make_ansi_codes(EIGHT_BIT)` on that same style returned `38;5;16;48;5;109`. A
same-process ladder asserts one depth three times and calls it three. The old style hid
this, being byte-identical at every depth.

## Corrections to the record

- **My recommendation was wrong**, and for a reason available to measurement: I ranked the
  candidates on text contrast and never on band contrast, so I recommended — and the owner
  chose — a ground that is invisible as a band on 14 of 25 schemes. Found by an
  adversarial review of the shortlist, confirmed independently. The dark family is
  recorded above as rejected rather than deleted.
- The comment's claim that `NO_COLOR` drops the bar "entirely" is **wrong about the
  bytes**. `Segment.remove_color` strips colour and keeps attributes, so an attribute
  survives on every segment including the padding. With `bold` now gone there is nothing
  left for it to keep — but the claim was wrong when written and would be wrong again the
  moment an attribute came back.
- My first pass cited Alacritty's pre-0.13 palette (`0 #1d1f21, 6 #8abeb7, 8 #666666`) as
  evidence for the bug. It is stale — 0.13 moved to base16 classic dark — and worse,
  Alacritty has had bold-is-bright **off** since 0.4.2, so that row never supported the
  hypothesis at all.
- My "One Dark" triple was a hybrid of three schemes: `0`/`6` from One Half Dark and `8`
  from Atom's `mono-3` syntax variable, which is not any shipped scheme's slot 8. The
  conclusion survives across all three real variants (1.92–2.96); the citation does not.
- I first reported the floor as 3.59:1 (Tango). Widening the sample found **1.67:1**
  without bold at all.
- `xterm` and `iTerm2` ship **light** backgrounds by default; I had counted both as dark.

## Residuals

- **`standard` and tmux-on-16-colour remain palette-dependent** and no colour choice
  changes that. They are recorded above rather than fixed.
- **The light-terminal edge is 2.28 against the old style's 3.30.** A light band on a
  white page is inherently a smaller step than on a black one; every candidate that
  improved it gave up more elsewhere.
- **`tui/themes.py` is still not consulted by this module.** Twenty other styles in
  `render.py` are hardcoded foregrounds that still inherit the terminal's ground, which is
  safe for them and is what #58 (auto light/dark) is actually about.
- **The band-edge gate is pinned against two extremes, not against themes.** It is a
  property test standing in for a population; a ground that clears 1.7:1 at both ends
  keeps a band on all 25 schemes measured, and that correspondence is evidence rather than
  proof.
