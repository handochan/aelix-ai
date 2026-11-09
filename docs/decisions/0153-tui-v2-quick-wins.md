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
- *2026-11-06 note (#702):* the stamp moves on the EDGES only: `set_running(True)`
  while already running no longer restarts the turn's clock. Nothing nests them
  during a turn today — `_run_cancellable` wraps its work in `set_running(True)` and
  puts back what it found, but only `/compact` and `/agents run` go through it, and
  they run between turns (ADR-0292 refuses both mid-turn; the commands that do run
  beside a turn do not touch it) — so this guards against a nesting rather than
  fixing one that ran. The falling edge
  keeps the run's length (`AelixChrome.last_run_seconds`), and a typed TUI turn
  leaves one dim transcript line when it is over: `✓ done · 4.2s · 14:05`,
  `stopped · 4.2s · 14:05` after Esc / Ctrl+C (under the yellow "Operation
  aborted"), or the time alone after a failure (under its red `✖` line). The
  outcome is the last assistant `stop_reason` the renderer saw
  (`EventRenderer.take_run_end`), so an auto-retry that succeeded reads `done`, and
  Esc or Ctrl+C in a retry's backoff (an `auto_retry_end` with "Retry cancelled")
  reads `stopped` (Codex 702-retry-cancel); what the renderer saw is cleared as a
  typed turn starts, so a line an `input` hook handled ran no turn and leaves none
  even after an extension's turn (#674) set it (verify V8). The length is
  `tui/durations.py`'s `turn_duration` (`840ms` / `4.2s` / `42s` / `1m 12s` /
  `1h 03m`, the sub-second shape `/stats`'s latency uses); `/stats`'s two shapes moved there unchanged. Commands, extension
  turns (#674), `-p`, json and rpc leave no line. No `/settings` row turns it
  off: one dim row per typed turn, and none was asked for.
- *2026-11-09 note (#732):* the line sits one blank row under what is above it,
  in all three outcomes. Flush under the answer's last line it read as one more
  line of the answer. The owner asked for the gap and took the recommendation to
  draw it in all three outcomes ("정할 것하나는 권고대로 해주세요"): the line
  closes the turn, it does not annotate the error. `turn_end_line` returns
  `chrome.BlankRowAbove(line)`, and the chrome, which makes every scrollback
  write, draws the row only when the last write does not already end on a row
  that looks blank (`_ends_on_blank_row` renders that write again, once per
  turn). The user's echo ends on one, so a turn that committed nothing after it
  would otherwise have drawn two (the first cut always drew the row), and so
  does a command's output that ends in a newline; there the turn adds the dim
  line alone. On a console that paints colour a space with a background or
  reversed is not blank: an answer that ends in a code block ends on the block's
  padding row, spaces on its colour, and still gets its row (review round 2).
  Measured live (deepseek-v4.1-flash-1017 via OpenRouter, a 100×45 pty, the
  cells' background read from the emulator) under a plain answer, an answer
  after a `bash` call, Esc's `✖ Operation aborted` line and an answer that is
  one fenced code block: one empty, unpainted row each, where `6caaae09` drew
  none. The empty answer and the newline-ended output are pinned by
  `tests/tui/test_turn_done_line_702.py` only, and a failure's time by its
  wrapper test only (the row does not depend on the outcome).
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

## Note (2026-11-03, #596): the `❯` marker in shell mode

*Amended 2026-11-03 by [ADR-0287](0287-the-input-shows-what-enter-will-do-and-a-bang-line-runs-during-a-turn.md) D1–D2.* In shell mode (a `!` / `!!` line) the typed sigil is drawn as
the marker in place of `❯ ` — `! ` or `!! `, in the theme's `shell` colour and bold — and is taken
out of the drawn line; the text keeps it. A bare `!` shows "a shell command" where the placeholder
was. Outside shell mode the marker and the placeholder are as decided here.
