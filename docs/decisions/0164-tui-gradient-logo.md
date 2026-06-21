# ADR-0164 — Gradient terminal logo in the startup header

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** 6h₃₁
- **Relates:** ADR-0148 (logo header), ADR-0153 (enriched header).

## Context

The startup header rendered the AELIX block art in flat **bold cyan**. `docs/assets/aelix-terminal-logo.ansi`
defines a cyan → blue → purple per-line **24-bit truecolor gradient**; the user asked the header to use it.

## Decision

Embed the gradient in `tui/_logo.py` as `LOGO_ANSI`, built from the existing `_LOGO_LINES` + six truecolor
stops (`(0,242,254) → (0,204,255) → (0,153,255) → (51,102,255) → (102,51,255) → (153,0,255)`) — **not** by
reading the `docs/` asset at runtime (`docs/` is not packaged into the wheel). `_build_banner` renders it via
`Text.from_ansi(LOGO_ANSI)` with no `style=` override so the embedded SGR escapes show. Title stays bold,
tagline dim.

## Consequences

- Pure TUI-consumer; no protected-core. The gradient ships in the wheel (embedded, no `package-data`).
- `Text.from_ansi` / Rich downgrades cleanly on no-color terminals.
- `LOGO_ART` (plain) is retained for any non-colored consumer; banner tests still pass (glyphs/title/tagline
  unchanged — only the colour escapes differ).
