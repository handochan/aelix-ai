# 0234. Two grounds, and the colour that only works on one

Status: Accepted (2026-08-20).
Date: 2026-08-20
Relates: ADR-0228 (a colour shipped on text contrast alone, and the band vanished),
ADR-0230 §feed (the homepage deploy is also what publishes `latest-version.json`),
`docs/assets/brand/BRAND.md` (the identity ramp and its theme rule).
Repos: this one (`site/`) and `handochan/aelix-marketplace` (`index.html`).

Both published pages forced dark. They now follow `prefers-color-scheme`. Two
single-file, build-step-free pages, both already driving every colour through
`:root` custom properties, so this is a palette added — not a rewrite.

## The trap, measured first

The brand glow is unusable as light-mode text:

```
--glow    #22D3EE  vs paper #F6FAFB =  1.72:1   vs white =  1.81:1   ✗
--current #06B6D4                   =  2.31:1              2.43:1    ✗
--deep    #0E7490                   =  5.10:1              5.36:1    ✓ AA
```

The colour that carries the entire dark theme cannot be read on a light ground.
BRAND.md had already reached this conclusion for the artwork — "no single brand
colour is strong on both grounds… do not recolour a lockup to compromise" — which
is why `wordmark-light.svg` exists and uses `#06B6D4` for the cursor pixel. The
same rule is applied here to UI colour: **the light accent is the brand deep**,
and no seventh teal was invented for it.

This is the single most likely way a light theme ships broken, because it looks
fine to everyone who only checks dark.

## Parity, not a second design

Every light value was solved to the ratio its dark counterpart already measures,
so neither ground is the poor relation:

| role | dark | light |
| --- | --- | --- |
| body on ground | 12.86:1 | 12.88:1 |
| headings on ground | 18.29:1 | 17.82:1 |
| muted on ground | 6.25:1 | 6.23:1 |
| accent on ground | 10.63:1 | 5.10:1 |
| text on the accent fill | 10.36:1 | 5.10:1 |

The accent is the one row that cannot be matched, for the reason above.

The invariant that replaced "the pairs we happen to use today" is stronger and
uniform: **every foreground token clears AA on every surface token, in both
schemes.** The weakest pair anywhere is 4.59:1 (light accent on the error tint);
dark's weakest is 5.11:1. Reaching it cost one deliberate deviation — the
marketplace's amber and rose tints are a shade lighter than exact dark-band
parity would make them, so the accent stays above 4.5 on top of them.

Token names are now shared between the two properties, role for role. That was
not cosmetic: the homepage's `--paper` had been doing two jobs, strong-foreground
AND text-on-fill, and those two must move in opposite directions when the ground
flips. A single token could not have carried both.

## Two things deliberately do not move

**The primary button** is identical on either ground: a deep fill carries paper
text at 5.10:1 both ways, and it is the ground behind it that changes. Only the
hover step is scheme-dependent — dark steps up to the glow, light steps down.

**The terminal card** is dark-locked, with its own `--term-*` tokens the theme
roles cannot reach. It is a picture of a terminal running the real ANSI banner;
re-tinting it for a light page would show something Aelix never prints.

## The marketplace had no light lockup — not unused, absent

`index.html` hardcoded `assets/lockup-dark.svg`. `lockup-light.svg` was not
merely unreferenced: it was not in the repository at all, because
`brand-sync.yml`'s file list had never named it, so the drift guard could not
have told anyone. Three steps in order — name it in the list, fetch it
byte-identical from the canonical kit, serve it through `<picture>`.
`assets/mark.svg` in the footer needs no swap; it is theme-neutral by
construction and measures 5.10:1 light / 3.59:1 dark on the page ground.

The homepage's lockup is inline `<svg>`, so brand-sync cannot see it either. Its
gate is different: the CSS that swaps the word and the cursor pixel is asserted
against the bytes of `lockup-dark.svg` and `lockup-light.svg` themselves.

## What an adversarial review found in the page, not only in the gate

**A `:root` below the light block wins.** A `@media` query carries no
specificity, so a later `:root` beats the light palette for a light reader — and
the dark-locked terminal `:root` was sitting exactly there. Nothing was broken
yet, because it only declares `--term-*`; adding one role token to it (the shape
of edit its own comment invites) would have undone the light palette silently.
Both gates had merged their token maps by dictionary order, so neither could
have seen it. The block moved above the light `@media`, the maps are built in
document order, and a test now refuses any `:root` that follows the light block.

**`color-scheme: light dark` reached inside the sealed card.** `color-scheme` is
what the UA paints a scroll container's scrollbar with, and `.term` is
`overflow-x: auto`. The change that was right for the page gave a light reader a
pale scrollbar across a near-black card — a regression introduced by the same
commit that claimed the card was locked. `.term` now pins `color-scheme: dark`.

## The gates, and the one rule that makes them possible

`site/` is published straight to Pages and nothing in this repository read its
stylesheet. That is how ADR-0228 shipped a colour measuring 1.16:1. There are
now two gates — `tests/site/test_site_theme.py` and, in the other repo,
`scripts/check_theme.py` (standard library only, its own CI job).

Both rest on one rule: **every colour lives in a token, no literal outside
`:root`.** Without it a table of tokens proves nothing about what a rule paints —
`.sub { color: #C6D6DC }` is 1.42:1 on the light ground and passed every other
assertion. Making the rule absolute meant tokenising the banner's ANSI ramp as
`--term-a1`…`--term-a6`, which removed the last allowlist.

Anything with a fill or an outline is measured twice — the text on it, and the
band against the page — which is ADR-0228's lesson stated as a test rather than
as a comment.

The marketplace gate proves each of its seventeen detectors individually, by its
own message. An earlier version asked only whether the doctored page produced
*some* failure, which let a broken detector hide behind any other that fired on
the same mutation; two detectors had no control at all.

## Live verification

The static gates model the cascade. Chromium, Firefox and WebKit *are* the
cascade, and all three were asked directly, in both schemes: body ground and
text, the h1, both lockup fills, the primary button, the accent, the terminal
card's computed `color-scheme`, and the marketplace's `<img>.currentSrc`. All
agreed with the model. The `<picture>` swap was also checked on a live scheme
flip with no reload, which is the one behaviour a static reading cannot settle:
it re-selects.

This is a one-off, not a CI job. Adding Playwright and three browser downloads to
a workspace that ships an `--offline` mode is a worse trade than the reading it
would automate.

## The focus ring, raised as out of scope and then fixed on the owner's call

The marketplace's Copy button drew its ring outside its border box while `.cmd`
is `overflow: hidden` — which it needs, to clip its children to the rounded
corners. Those children sit 1px from three of those edges; the ring occupies
+2px to +4px. Measured with the button focused from the keyboard: **102 of the
ring's pixels survived, all of them in a 3px strip down the left side**, so a
keyboard user tabbing onto the page's only button saw a short line appear beside
a border that was already there.

Pre-existing, identical on both grounds, and nothing to do with colour, so it
was reported rather than folded in. The owner asked for it, and
`.cmd :focus-visible { outline-offset: -3px }` draws the ring inward instead:
416 ring pixels, a closed rectangle, both schemes. It covers the scrollable
`<pre>` in the same box too, which the UA also makes focusable so it can be
scrolled from the keyboard. The colour needed no change — the ring now lands on
`--bg-elev`, which the gate already measures (5.36:1 light, 10.06:1 dark).

## Not fixed here

The homepage still has no global `:focus-visible` rule. Pre-existing, and the
light ground neither creates nor worsens it.
