# 0104. Sprint 6h₁₀a — Interactive TUI Shell (Phase 5c-tui)

Status: Accepted (Sprint 6h₁₀a / Phase 5c-tui sprint 1 of ~4 / W6 shipped)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₁₀a is the **first sprint of Phase 5c-tui** and opens the phase that
makes the TUI a first-class consumer of the Phase 5b-foundation contracts
(ADR-0103 closed Phase 5b-foundation). It replaces the Phase 5b carry-forward
diagnostic at `cli/entry.py` —

    Error: interactive mode not implemented (Phase 5b — TUI carry-forward; see ADR-0088).
    raise NotImplementedError(...)

— with a working interactive shell built on **prompt-toolkit (input/editor) +
Rich (output rendering)** per ADR-0088. ADR-0088 Q1 had already resolved the
dependency model (`pip install aelix[tui]` optional extra) and Q10 framed the
streaming throttle ("~30 FPS max").

**Thin-shell scope** (user-approved 2026-05-25): streamed output rendering +
prompt-toolkit input + the harness event pipe. The persistent live chrome
(status line / footer / working-indicator region), the concrete
`ExtensionUIContext` implementation + Aelix widget layer, the Tier-2
descriptor→Rich renderer, and concrete themes are deferred to Sprint 6h₁₀b.

## Decision

The TUI is a **third frontend sibling** of `run_print_mode` / `run_rpc_mode`:
it drives the same `AgentHarness` and subscribes to the same `AgentEvent`
stream, rendering to Rich instead of serializing to JSONL/stdout. A new
`aelix_coding_agent/tui/` subpackage lands in five modules + entry wiring + the
`[tui]` extra:

1. **`[tui]` extra** — `aelix-coding-agent` gains
   `[project.optional-dependencies] tui = ["prompt-toolkit>=3.0,<4",
   "rich>=13.7,<15"]`; the root dev group adds both so pyright/pytest resolve
   them (both ship `py.typed`). `uv.lock` regenerated.
2. **`tui/stream.py` — `StreamRenderer`** — aider `mdstream.py` parity: a Rich
   `Live` region where stable lines are committed to scrollback
   (`live.console.print`) and only the trailing `live_window` lines are
   repainted (`live.update`); adaptive throttle `min_delay = clamp(render_time
   × 10, 1/20s, 2s)` coalesces fast deltas (ADR-0088 Q10). `auto_refresh=False`
   + manual `refresh()` → no background thread; clock is injectable for
   deterministic tests.
3. **`tui/render.py` — `EventRenderer`** — the `harness.subscribe` sink.
   `match event.type` over `AgentEvent`; `message_update` re-dispatches the
   embedded `AssistantMessageEvent` (`text_delta`→live stream, `thinking_*`→dim,
   `done`/`end`→finalize); harness-layer `tool_execution_*` render tool header +
   result. **Terminal failures are surfaced on `message_end`** by inspecting
   `message.stop_reason ∈ {"error","aborted"}` + `error_message` — the agent
   loop delivers errors as a `MessageEndEvent`, not as a streaming `error`
   re-emit (`loop.py:265-310`: `_UPDATE_EVENTS` excludes `done`/`end`/`error`),
   mirroring `run_print_mode`. Out-of-band prints (tool/thinking) defensively
   finalize any open text region first (no dependence on emission order).
   Unknown `type` → no-op (forward-compatible).
4. **`tui/input.py`** — pure `parse_input_line` (precedence parity with
   `run_repl`: `/quit`·`/exit`→quit, `/reload`→reload, `!!`→transient bash,
   `!`→bash, else→prompt) + `build_prompt_session` (single-line inline editor,
   history, injectable input/output for tests).
5. **`tui/shell.py` — `run_tui(runtime_host, *, cwd, console=, session=)`** —
   structural parity with `run_print_mode`: signal handlers (SIGTERM/SIGHUP) →
   `set_rebind_session` closure (re-subscribes the renderer across session
   swaps) → `bootstrap` → `prompt_async` input loop → dispose in `finally`. A
   **failed turn does not kill the REPL**: any exception from `harness.prompt`
   (other than `KeyboardInterrupt`, which best-effort aborts) is caught, the
   open stream finalized, the error rendered, and the loop returns to the
   prompt — parity with `run_print_mode`'s turn-loop guard.
6. **Entry wiring** — `cli/entry.py` dispatches `interactive` → `run_tui` after
   harness/runtime construction; a guarded `from ...modes import run_tui`
   prints an actionable install hint and returns exit 1 when the `[tui]` extra
   is absent. `modes/__init__.py` re-exports `run_tui` via a PEP-562
   `__getattr__` so the print/rpc/server import paths never require
   prompt-toolkit.

## Architecture (aider "Option A" — sequential ownership)

```
┌─ terminal scrollback (permanent) ── Rich Console.print + live.console.print
├─ live region (repainted live_window lines) ── StreamRenderer.live.update
└─ input region ── prompt-toolkit PromptSession.prompt_async  (active only while idle)
```

The prompt-toolkit session owns the terminal **only** while awaiting input; the
Rich renderer owns it **only** while a turn runs. `await harness.prompt(...)`
completes fully before the next `prompt_async`, so the two never contend for the
cursor — no `patch_stdout` is needed in the thin shell. This is aider's
validated pattern (`Aider-AI/aider` `io.py` + `mdstream.py`, 10+ years on
prompt-toolkit + Rich) and matches the inline-scrolling + live-bottom UX of Pi /
Claude Code / Codex.

## Aelix-additive divergences from Pi / the reference

| # | Divergence | Reference behavior | Aelix-additive behavior | Justification |
|---|---|---|---|---|
| 1 | asyncio shell | aider is fully synchronous | `run_tui` is a coroutine using `prompt_async` + `await harness.prompt` | Aelix harness is asyncio-native; sequential-ownership invariant preserved within one coroutine |
| 2 | plain text this sprint | aider renders Markdown via `MarkdownStream` | `StreamRenderer` renders `rich.text.Text` | Markdown rendering is a 6h₁₀b polish item; the throttle/scrollback machinery is identical and carries forward |
| 3 | `[tui]` optional extra w/ graceful degrade | Pi bundles its TUI | interactive (the default mode) raises a clean exit-1 hint when `[tui]` is absent | ADR-0088 Q1; keeps headless/server installs lean |
| 4 | tool rendering keyed off harness layer | — | streaming `toolcall_*` events are no-ops; `tool_execution_*` drive tool rendering | avoids double-render of the same tool call |
| 5 | one-turn-at-a-time | Pi supports mid-turn steer/follow-up UX | thin shell: prompt → await idle → prompt | `steer`/`follow_up` UX deferred to 6h₁₀b |
| 6 | `modes.run_tui` via PEP-562 `__getattr__` | — | lazy re-export | keeps prompt-toolkit off the print/rpc/server import path |

## Deferred (Phase 5c carry-forward)

| Item | Owner sprint |
|---|---|
| Concrete `ExtensionUIContext` impl + `bind_ui()` wiring + Aelix widget layer | 6h₁₀b |
| Tier-2 descriptor → Rich renderable mapping | 6h₁₀b |
| Live chrome: persistent status / footer / working-indicator region | 6h₁₀b |
| Concrete themes (Rich/pt `Style`); Markdown streaming; multi-line input; autocomplete stacking; custom editor | 6h₁₀b |
| Mid-turn `steer` / `follow_up` input UX | 6h₁₀b |
| `--resume` interactive picker; interactive `@file` / `-m` initial message | 6h₁₀b+ |
| Inline images (`term-image` / Kitty / iTerm2) | 6h₁₀c |
| pyte snapshot tests | 6h₁₀d |
| Root `aelix` console-script routing | follow-up |

### Known follow-up — canonical `aelix` command routing

qa-tester (W5 real-PTY smoke) confirmed the TUI launches and behaves correctly
(bash passthrough, transient bash, `/quit`→exit 0) via
`python -m aelix_coding_agent`. However `uv run aelix` still runs the **root
umbrella mock demo** (`src/aelix/__main__.py`, wired by root
`pyproject.toml` `[project.scripts] aelix = "aelix.__main__:main"`), which
shadows the coding-agent CLI's own `aelix = "aelix_coding_agent.cli.entry:
main_sync"`. Re-pointing the canonical `aelix` command from the demo to the
real CLI is an **outward-facing product/packaging decision** outside this
sprint's entry-seam scope; deferred per user decision (2026-05-25). The TUI is
reachable today via `python -m aelix_coding_agent`.

## Verification

- ruff clean; `uv run pyright` holds the 8-error baseline (all in
  `scripts/pyright_spike.py`) with **zero** errors from `tui/` or the touched
  `cli/entry.py` / `modes/__init__.py`.
- New `tests/tui/` suite (StreamRenderer throttle/scrollback, EventRenderer
  dispatch over every event variant, input truth table, headless `run_tui`
  smoke via `create_pipe_input` + `DummyOutput`) + updated
  `tests/cli/test_entry_router.py` interactive dispatch + missing-extra path.
  Full suite green under `-W error`.
- `python scripts/generate_contracts_schemas.py --check` exit 0.
- Protected paths byte-unchanged: `rpc/`, `harness/`, `mcp/`,
  `scripts/pyright_spike.py`, `docs/contracts/`.
- qa-tester real-PTY smoke: `aelix` (no args) launches the shell, a prompt
  drives a turn with streamed output, `/quit` exits 0.

## References

| Reference | Use |
|---|---|
| ADR-0088 (TUI library decision) | prompt-toolkit + Rich stack; Q1 (`[tui]` extra), Q10 (throttle), §"Architecture of the selected stack" |
| ADR-0089 (Phase 5a-i/ii closure) | owner of the `NotImplementedError` carry-forward this sprint replaces |
| ADR-0100 (ExtensionUIContext Protocol) | the surface 6h₁₀b will bind via `bind_ui()`; thin shell stays headless |
| ADR-0103 (aelix-server / Phase 5b-foundation COMPLETE) | the foundation this phase consumes; `run_rpc_mode`/`run_print_mode` frontend precedent |
| aider (`Aider-AI/aider` `io.py` + `mdstream.py`) | Option-A sequential ownership + MarkdownStream throttle/scrollback pattern |
| `cli/repl.py` (`run_repl` + `handle_user_bash`) | input-side precedent reused for `!`/`!!`/`/reload`/`/quit` |

## Phase

Sprint 6h₁₀a / Phase 5c-tui (shipped). Next: **Sprint 6h₁₀b** — concrete
`ExtensionUIContext` implementation + Aelix widget layer + Tier-2 descriptor
renderer + concrete themes + live chrome.
