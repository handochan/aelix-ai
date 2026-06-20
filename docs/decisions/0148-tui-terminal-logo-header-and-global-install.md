# 0148. TUI Terminal-Logo Startup Header + `aelix[tui]` Global-Install UX

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (product UX — no pi-parity surface)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Two small, user-requested product items (interleaved into the TUI-first v1 track at the user's request):
the user wants `aelix` to be a **user-global command**, and the **terminal logo** shown as a header at
TUI startup. The brand image logo (JPG/SVG) is **not yet decided** and is intentionally out of scope.

## Decision

### Terminal-logo startup header (B)
`tui/_logo.py` embeds the block-art "AELIX" logo + title (`Aelix Agent Runtime`) + tagline as **module
string constants** (not a packaged data file — so it ships in the wheel with no `package-data` /
`MANIFEST.in` configuration). `_build_banner` (`tui/shell.py`) now returns a Rich `Group(logo, spacer,
panel)`: the Rich-styled block art (degrades to plain text on no-color terminals) above the existing
rounded info panel (model / cwd / `/help`). The redundant plain `"Aelix"` line was removed (the logo
header replaces it; the existing banner test still passes via the title's `Aelix`). The brand image
logo is **not referenced** anywhere in the runtime — only this text/Unicode art is shipped.

### `aelix` as a user-global command (A)
The `aelix` console script already resolves to the real CLI (ADR-0147 fixed the demo collision —
verified). The meta package now exposes `[project.optional-dependencies]` `tui` / `images` that forward
to `aelix-coding-agent[tui]` / `[tui,images]` (wheel-verified `Provides-Extra: tui/images`), so a single
isolated global install gives the full interactive experience:

```bash
uv tool install 'aelix[tui]'   # or: pipx install 'aelix[tui]'
```

The root `README.md` gained an **Install** section documenting `uv tool install` / `pipx` / `pip`, the
`[tui]`/`[images]` extras, and provider-key setup; the stale `uv run aelix # echo demo` line (now the
real CLI; the demo is `python -m aelix`) and the workspace-layout comment were corrected.

## Deferred (owner decision)

The brand image logo (`docs/assets/aelix-logo.{jpg,svg}`, generated earlier) remains **held back** —
branding is an owner decision; only the terminal art is adopted for v1.

## Verification

`uv run ruff check` clean; visual render confirms the header (block art + title + tagline + panel);
`tests/tui/test_commands.py` banner tests pass incl. the new `test_banner_includes_terminal_logo_header`;
`uv build` builds the meta with the `tui`/`images` extras; full gate green.
