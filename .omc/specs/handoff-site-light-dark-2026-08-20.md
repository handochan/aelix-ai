# Handoff — homepage + marketplace: follow the reader's colour scheme

**Goal.** `handochan.github.io/aelix-ai/` and `handochan.github.io/aelix-marketplace/`
render light or dark from `prefers-color-scheme` instead of forcing dark.

Written 2026-08-20, from `main` `0d8c3fb`. Everything below was measured in this
worktree, not recalled.

---

## Where the two sites are

| | path | deploy |
| --- | --- | --- |
| Homepage | `/workspaces/aelix-ai/site/index.html` (single file, 11 KB) | `.github/workflows/pages.yml`, `path: site`, triggers on `site/**` |
| Marketplace | `/workspaces/aelix-marketplace/index.html` (single file) | its own `.github/workflows/pages.yml`, `.nojekyll` at repo root |

Both are hand-written single files. No build step, no framework, no CSS bundle.

## The good news: both are already token-based

This is a palette addition, not a rewrite. Each has one `:root` block of custom
properties and every rule reads through them.

**Homepage** (`site/index.html:19-31`) — `--ground` `--card` `--line` `--paper`
`--ink` `--tx` `--tx-dim` `--glow` `--current` `--deep` `--mono`.

**Marketplace** (`index.html:26-48`) — `--bg` `--bg-elev` `--bg-code` `--fg`
`--fg-strong` `--fg-muted` `--border` `--border-strong` `--accent` `--accent-fg`
`--chip-bg` `--chip-fg` `--advisory-bg/fg/border` `--error-bg/fg/border`
`--focus` `--mono`.

⚠️ **The two sites use DIFFERENT NAMES for the SAME VALUES.** `--ground` and
`--bg` are both `#0B0F14`; `--tx` and `--fg` are both `#C6D6DC`; `--glow` and
`--accent` are both `#22D3EE`. Decide up front whether to unify the names as
part of this work or keep them separate — doing it halfway leaves two palettes
that drift.

Both currently hardcode dark: `html { color-scheme: dark; }` (homepage `:33`)
and `color-scheme: dark` inside `:root` (marketplace `:27`). Those become
`light dark` so form controls, scrollbars and the initial paint follow too.

## 🔴 The measured trap: the accent cannot survive on white

The brand cyan is unusable as light-mode text. Measured (WCAG contrast vs `#FFFFFF`):

```
--glow    #22D3EE  vs #FFF =  1.81:1   ✗
--current #06B6D4  vs #FFF =  2.43:1   ✗
--deep    #0E7490  vs #FFF =  5.36:1   ✓ AA body text
```

So the light palette's accent is `--deep` (or darker), NOT `--glow`. Reusing the
dark accent is the single most likely way this ships broken, because it will
look fine to anyone who only checks dark.

Baseline to match at the other end (current dark, already AA):
`--glow` 10.63:1, `--tx` 12.86:1, `--tx-dim` 6.25:1 against `--ground`.

The marketplace already documents its measured ratios in a comment above
`:root` (`index.html:24-26`). **Extend that comment with the light numbers** —
that comment is the existing standard here, and leaving it dark-only would make
it quietly false.

## 🔴 The marketplace has no light lockup, and the sync will not give it one

`index.html:386` is `<img class="lockup" src="assets/lockup-dark.svg">` — a
hardcoded dark-mode lockup that will sit wrong on a light page.

`assets/` currently holds exactly: `favicon.svg`, `lockup-dark.svg`, `mark.svg`,
`social-card.png`. And `.github/workflows/brand-sync.yml:39` enumerates the
files it pulls:

```
for f in favicon.svg mark.svg lockup-dark.svg social-card.png; do
```

`lockup-light.svg` exists upstream in `aelix-ai/docs/assets/brand/` (alongside
`lockup-dark.svg`, `wordmark-light.svg`, `wordmark-dark.svg`, `mark-mono.svg`)
but is **not in that list**, so it is not in the marketplace repo at all. Three
steps, in order:

1. add `lockup-light.svg` to the `brand-sync.yml` list,
2. fetch it into `assets/` (the workflow prints the exact `curl` line on drift),
3. swap the `<img>` for a `<picture>` with
   `<source media="(prefers-color-scheme: light)" srcset="assets/lockup-light.svg">`.

Also check `assets/mark.svg` (`index.html:483`) — if it is not theme-neutral,
`mark-mono.svg` exists upstream.

The homepage does NOT have this problem: its lockup is an **inline `<svg>`**
(`site/index.html:110`, `viewBox="0 0 952 272"`) whose fill is
`url(#strand)` — a document gradient. It themes from CSS with no `<picture>`.
But the gradient stops are the same cyans measured above, so they need a light
variant or the mark washes out on white.

## Approach that fits what is already there

Keep dark as the `:root` default (both sites already are — no flash for the
majority) and override under `@media (prefers-color-scheme: light)`. That is
the smallest diff and keeps the documented dark ratios where they are.

## Do not claim done until

- [ ] Both ends measured, not just light. Every token pair gets a number.
- [ ] 🔴 **Anything with its own background is measured TWICE** — text-on-element
      AND element-vs-page. A band that reads fine but vanishes into the page is
      the failure mode this project has already shipped once; see
      `docs/decisions/0228-*` where a recommendation made on text contrast alone
      was wrong and the band disappeared in 14 of 25 schemes.
- [ ] Light checked with an actual light OS/browser setting, not only by editing
      the media query — `color-scheme` affects form controls and scrollbars that
      a forced media query does not exercise.
- [ ] The marketplace's `:root` contrast comment carries the light numbers too.
- [ ] `social-card.png` / OG image still reads (it is one image for both modes).
- [ ] Homepage: `site/**` is the Pages trigger path, so the deploy fires; the
      marketplace deploys from its own workflow — two separate PRs, two repos.

## Not in scope, but adjacent

`site/latest-version.json` must be published to Pages before the beta tag, and
the homepage deploy is what publishes it. If this work ships first, that
blocker clears as a side effect — verify it rather than assuming.
