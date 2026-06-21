# 0153. TUI v2 Quick-wins bundle — enriched header, user-echo lift, spinner + input affordances, CSI 2026

Status: Accepted
Date: 2026-06-21

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

The shipped TUI (ADR-0104–0132) is feature-complete against pi's interactive
surface, but the first-impression UX is thin. Concretely, four weak spots
surfaced from the user mockup and the TUI v2 overhaul roadmap
(`.omc/specs/tui-v2-overhaul-roadmap.md`):

- **WP-5 — startup header.** The banner showed only `model` + `cwd` + a generic
  `/help` hint. It did not surface the runtime the user just booted into: the
  base URL, the version, which context files loaded, how many tools/skills/hooks
  are wired, and — the user's explicit ask — *which extensions are active*.
- **WP-6 — user-echo.** Human turns were the weakest visual element: a
  monochrome `» text` with no separation, buried among colored tool cards,
  diffs, and thinking blocks. Three separate sites echoed human input with three
  slightly different inline styles.
- **WP-3 — spinner + input.** The working line showed `{spinner} {message}
  · esc to interrupt` with no elapsed-time feedback; the input editor had no
  visible prompt prefix and no empty-buffer placeholder.
- **WP-9 — synchronized rendering.** pi uses a full differential renderer to
  avoid tearing; Aelix repaints the whole bottom chrome on each scrollback
  write.

This bundle is **Phase 1 of the roadmap** and is deliberately scoped to the
cheap, high-leverage subset of each work package.

## Decision

Ship four **pure TUI-consumer** changes. No file under
`packages/aelix-agent-core` (the protected core) is touched — every value is
read through existing public/semi-public harness surfaces, `getattr`-guarded so
the headless fakes and minimal harnesses never raise.

### Feature A — enriched startup header (WP-5)

`tui/shell.py::_build_banner(harness, cwd)` re-derives everything **inside** the
function (so neither call site — fresh start nor `/resume` re-banner — changes).
The Rich panel now shows `model` / `baseurl` (only when non-empty) / `cwd` /
`version` (from `cli.config.VERSION`), then compact `[Context]` (via
`discover_context_files(cwd)` at render time → `AGENTS.md` or `none`),
`[Tools]` (count + first names, from the same `harness._action_get_all_tools()`
the `/tools` command uses), `[Skills]` (`len(harness.skills)`), `[Hooks]`
(distinct event types with a handler on `harness.hooks._handlers`), and
`[Extensions]` (comma-joined `harness.extension_runner.extensions[*].name` —
the user's "show which extensions are active" ask). Every read is exception-
suppressed; empty sections render a dim `none`.

The hint line is `'/help for commands • Ctrl+C to interrupt'`. The mockup's
`Ctrl+C×2 exit` was **rejected after verifying the binding does not exist** —
`chrome.py`'s `c-c` handler interrupts while running and clears the buffer when
idle; there is no double-Ctrl+C-to-exit. The banner advertises only what is
true.

### Feature B — user-echo visual lift (WP-6, trivial tier)

A shared helper `tui/render.py::render_user_message(text, kind="prompt")`
returns a Rich `Group` of a **leading blank line** + a **bold-cyan** echo line.
`kind="prompt"` keeps the `» ` chevron; `kind in {"steer","follow_up"}` uses a
distinct `Steering: ` / `Follow-up: ` label but the **same** visual language.
All three human-input sites route through it: the live prompt echo and the
steer/follow-up echo in `shell.py`, and the replayed-transcript echo in
`render.py`. The full-width background bubble (WP-6 medium tier) is **deferred**.

### Feature C — spinner enrichment + input affordances (WP-3 subset)

- `chrome.py` stamps a monotonic `self._run_started` on `set_running(True)`
  (cleared on `False`); `_render_working` appends a dim `({elapsed}s · esc to
  cancel)` suffix while running, ticking on the existing `refresh_interval`
  repaints. The affordance text changes `esc to interrupt` → `esc to cancel`
  (mockup parity).
- A live input prefix `❯ ` (bold cyan, via a `BeforeInput` processor) and a dim
  empty-buffer placeholder `Type your message or @path/to/file` (a small custom
  `_PlaceholderProcessor` that renders only when `document.text == ""` on line
  0). Both are pure `BufferControl` input processors — headless-safe under
  `DummyOutput`. The committed-echo chevron stays `» ` (Feature B); the stored
  `prompt='» '` ctor field remains unused.
- The `↑ N tokens` clause is **OMITTED / deferred.** The streaming event path
  carries text *deltas* (strings), not an incremental output-token / usage
  signal — usage only arrives at `message_end`. There is no cheap real source
  for a live token count, and fabricating one was explicitly out of scope.

### Feature D — CSI 2026 synchronized output (WP-9, the cheap bit)

`chrome.py::print_above` / `print_above_many` bracket the Rich print(s) with
Begin/End Synchronized Update (`\x1b[?2026h` … `\x1b[?2026l`), written through
the same `self._console.file` stream Rich uses, **inside** the `in_terminal()`
block so the scrollback write + chrome repaint paint atomically on supporting
terminals. Best-effort + exception-suppressed: unsupported terminals ignore the
unknown private CSI, and `DummyOutput` makes it a no-op in tests.

We adopt **only CSI 2026**, not pi's full differential renderer. The diff
renderer is a large architectural change with its own correctness surface; CSI
2026 captures most of the perceived smoothness for a few lines of code and zero
risk, and can coexist with a future differential renderer.

## Consequences

- The first frame now communicates the live runtime (model/base-url/version +
  active context/tools/skills/hooks/extensions), making misconfiguration
  (wrong model, missing AGENTS.md, an extension that failed to load) visible at
  a glance.
- Human turns share one visual vocabulary (blank-line separation + bold cyan)
  across live, steer/follow-up, and replay, so the conversation reads cleanly.
- The spinner gives elapsed-time feedback and a correct cancel affordance; the
  input editor has a visible prompt and a discoverability placeholder.
- Scrollback writes no longer tear on terminals that support CSI 2026.
- **Deferred:** the `↑ N tokens` working-line clause (no real streamed-usage
  source); the WP-6 full-width user-message bubble (medium tier); pi's full
  differential rendering (WP-9 large tier).
- Pure TUI-consumer: protected core untouched. Tests added under `tests/tui/`
  (banner sections + minimal-harness no-raise; `render_user_message` blank-line/
  chevron/cyan + steer/follow-up; spinner elapsed + `esc to cancel`; input
  prefix + placeholder; CSI 2026 bracket ordering).
