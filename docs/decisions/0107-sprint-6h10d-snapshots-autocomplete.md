# 0107. Sprint 6h₁₀d — pyte Snapshots + command-route Autocomplete (Phase 5c-tui finale)

Status: Accepted (Sprint 6h₁₀d / Phase 5c-tui sprint 4 of 4 / W5 shipped)
Date: 2026-05-26
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — consumer/test-only)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

6h₁₀a/b/c built the TUI shell, the live chrome + 27-method `ExtensionUIContext`, and the Tier-2
descriptor renderer. Two gaps remained from ADR-0105:88-89 / ADR-0106 deferrals: (1) the chrome's
**actual rendered terminal buffer** was only ever exercised through prompt-toolkit's internal
helpers under `DummyOutput` — never the real escape stream, so cursor/region/layout regressions
could pass; (2) 6h₁₀c **stored** `command-route` descriptors but never surfaced them in the input.
6h₁₀d closes both, plus validates the inline-image Unicode tier end-to-end. This is the final
Phase 5c-tui sprint.

## The decisions

### 1. command-route live autocomplete
A `DescriptorCommandCompleter` (`tui/completion.py`) reads `DescriptorRenderer.command_routes`
**live by reference** (a `get_routes` callable), so descriptors appended/removed mid-session change
the offered completions immediately. It yields `/<command>` completions only on `/`-prefixed lines,
deduped by `command`, with the description (+ optional `[keybind]`) as `display_meta`. The chrome
(`tui/chrome.py`) gained a `CompletionsMenu` Float (first in the float list, inert without a
completer), Tab (`c-i`) / `c-space` completion-trigger keybindings, and a `set_command_completer`
seam that enables `complete_while_typing` **only while the line starts with `/`** (a live
`Condition`, not a snapshot) and installs a `DummyCompleter` on `None`. `run_tui` wires the completer
from `renderer.command_routes` in `_wire_descriptors`.

**The load-bearing detail:** `_accept` (Enter) now confirms a *highlighted* completion before
falling through to submit — gated on `complete_state.current_completion is not None`, NOT merely
`complete_state is not None`. Because the while-typing auto-menu never sets `complete_index`, the
auto-popped menu never hijacks Enter; an ordinary prompt submits exactly as before. (Verified
against prompt-toolkit 3.0.52 internals by W4 review + a real-PTY qa run: Enter still submits, Tab
on `/` with zero routes is a silent no-op, no literal-tab insertion.)

### 2. pyte snapshot harness
`tests/tui/_pyte.py` renders the live `AelixChrome` `Application` to a real escape stream via an
injected `Vt100_Output(StringIO, get_size=…, term="xterm-256color", enable_cpr=True)`, then replays
it through `pyte.Stream`/`pyte.Screen` and returns `screen.display`. The snapshots assert on the
genuine terminal grid (not internal helpers).

**The headless-CPR detail:** every chrome row is gated behind `renderer_height_is_known`, which
only flips after a Cursor-Position-Report arrives — which never happens without a real TTY. The
harness feeds a **synthetic CPR** (`\x1b[10;1R`, matching prompt-toolkit's exact
`^\x1b\[\d+;\d+R$` recognizer) once `chrome.app.is_running`, then lets the now-ungated frame paint.
The row/col values are inert (the chrome is an `HSplit` of fixed `height=1` rows with no
absolute-position logic keyed off the cursor), so this is a faithful headless analogue, not a
layout bias (W4-verified). Deterministic: fixed `Size(24,80)`, injected clock, `is_running` polling
(no sleeps). Snapshots assert exact geometry (footer is the last painted row; input above it) and a
**negative** case (a removed descriptor is absent from the grid), not just substring presence.

### 3. image Unicode-tier validation
`render_image(png, max_cells, capability=UNICODE)` → a `rich-pixels` renderable → printed through a
Rich `Console` into a capture buffer → pyte shows colored cells; `capability=NONE` → the
`[image: … W×H]` placeholder. term-image graphics tiers remain **dormant** (Pillow<11, ADR-0106),
so real-PTY graphics validation stays deferred.

## Consequences
- The chrome now has real-terminal-buffer regression coverage (pyte) that `DummyOutput` tests can't
  provide. Descriptor-contributed slash commands are now usable via autocomplete.
- `pyte` added to the root dev group (pure-Python, no Pillow conflict).
- pyright holds the 8-error baseline (0 new); protected paths byte-unchanged.

### Deferred (carry-forward)
- Populated-autocomplete-dropdown **live** validation needs a Tier-1 extension emitting a
  `command-route` via `ui:list-modules` (unit-tested here; live fixture deferred).
- term-image graphics-tier real-PTY snapshots (until term-image is Pillow≥11-co-installable).
- The remaining 6h₁₀c deferrals (ActionDescriptor reverse-channel + management-modal command-trigger,
  breadcrumb `Panel.top` + agent-metric sidebar, live tool-result interception,
  `invalidate_descriptors()` re-probe, per-ext `ui_tui_trusted` gating).
- Root `aelix` console-script re-point (umbrella demo shadow) — outward-facing packaging, deferred.

## Verification (W4)
- Gate green: ruff clean; `uv run pyright` 8-baseline (0 new); **`uv run pytest` 2799+ pass / 1 skip**
  (14 new: 10 completion + ~8 snapshot incl. geometry + negative); protected paths byte-unchanged.
- **W4 code-reviewer (opus): APPROVE-WITH-NITS** (0 CRITICAL/HIGH; source-verified the `_accept`
  Enter path + the CPR-injection faithfulness). MEDIUM (snapshot assertions strengthened to exact
  geometry + a negative case) + 2 LOW (empty-command guard, same-command dedup) fixed in-sprint.
- **W4 qa-tester real-PTY: 4/4 PASS** — Enter still submits + agent streams (no `_accept`
  regression), `/`+Tab is a clean no-op, bash + `/quit` intact.

This closes Phase 5c-tui (6h₁₀a–d). Next: Phase 6 (Web UI, separate `aelix-web` repo) or backlog.
