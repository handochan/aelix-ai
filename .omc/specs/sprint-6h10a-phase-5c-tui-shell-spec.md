# Sprint 6h₁₀a — Interactive TUI Shell (Phase 5c-tui, sprint 1 of ~4)

Status: DRAFT (W1 spec — pending user approval; becomes Binding on approval, do not modify after W1 closure)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — Sprint metadata

| Field | Value |
|---|---|
| Sprint id | `6h₁₀a` |
| Phase | 5c-tui, sprint 1 of ~4 |
| Workflow | ADR-0032 W0-W6 |
| Scope class | Code (NEW `tui/` subpackage + entry wiring + `[tui]` extra) + tests + 1 ADR |
| Predecessors | Phase 5b-foundation COMPLETE (Sprints 6h₉a–6h₉f; ADR-0103) |
| Owning ADR closure | ADR-0104 (NEW — Sprint 6h₁₀a closure) |
| Scope decision | **Thin shell** (user-approved 2026-05-25): inline-scrolling streaming output + prompt-toolkit input + event-pipe. Live chrome / ExtensionUIContext concrete impl / descriptor renderer / themes deferred to 6h₁₀b. |

### §0.1 — Phase 5c-tui sprint map (forward design, non-binding for later sprints)

| Sprint | Deliverable | Source |
|---|---|---|
| **6h₁₀a** (this) | Interactive TUI shell — input + streamed output rendering; replaces `NotImplementedError` | ADR-0088 Q10 (backpressure) |
| 6h₁₀b | Concrete `ExtensionUIContext` impl + Aelix widget layer + Tier-2 descriptor→Rich renderer + concrete themes + live chrome (status/footer/working-indicator) | 6h₉c §1.5; ADR-0088 §arch |
| 6h₁₀c | Inline image protocol (`term-image` / Kitty / iTerm2) | ADR-0088 Q4 |
| 6h₁₀d | pyte-based snapshot testing | ADR-0088 Q6 |

---

## §1 — Background

### §1.1 — The seam to replace

`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:246-254` currently raises:

```python
if app_mode == "interactive":
    print(
        "Error: interactive mode not implemented "
        "(Phase 5b — TUI carry-forward; see ADR-0088).",
        file=sys.stderr,
    )
    raise NotImplementedError(
        "Interactive mode is deferred to Phase 5b (ADR-0088)."
    )
```

The harness + runtime are already constructed below this block (`entry.py:295-302`):
`harness = await _harness_factory(session)`, `runtime = await create_agent_session_runtime(harness, _harness_factory, repo=repo, fs=fs)`. Sprint 6h₁₀a **reorders** entry so the interactive branch runs *after* harness/runtime construction (parity with the rpc/print branch) and dispatches to `run_tui(runtime, cwd=...)`.

### §1.2 — The TUI is a third frontend sibling

Two reference frontends already drive the same harness event stream:

- `modes/print_mode.py::run_print_mode` (`print_mode.py:98-217`) — text/JSON one-shot. **This is the structural template for `run_tui`**: signal handlers → `set_rebind_session` closure → `subscribe` → drive turns → dispose in `finally`.
- `rpc/rpc_mode.py::run_rpc_mode` (`rpc_mode.py:1778-2106`) — full duplex JSONL loop (a richer template for the input-loop + session-rebind pattern). **PROTECTED — byte-unchanged.**

The existing minimal `cli/repl.py::run_repl` (`repl.py:76-120`) supplies the **input-side** parsing precedent (`!`/`!!` bash, `/reload`, `/quit`/`/exit`, else `harness.prompt`) and the reusable `handle_user_bash(...)` helper (`repl.py:29-73`). It does **not** subscribe to events (relies on tools printing) — the TUI adds the event-pipe.

The TUI is therefore: **`run_print_mode`'s scaffolding + `run_repl`'s input parsing + a Rich rendering sink** in place of the JSONL/stdout sink.

### §1.3 — The two event layers the TUI renders (verified)

The TUI subscribes at the **harness layer** (`AgentEvent`) and renders the **streaming layer** (`AssistantMessageEvent`) carried inside `MessageUpdateEvent`.

**Harness layer — `AgentEvent`** (`packages/aelix-agent-core/src/aelix_agent_core/types.py:159-238`), each a frozen dataclass with a `type` Literal:

| Event | `type` | Key fields | TUI action |
|---|---|---|---|
| `MessageStartEvent` | `message_start` | `message` | begin assistant message block |
| `MessageUpdateEvent` | `message_update` | `message`, `assistant_message_event` | **delta-render hook** — dispatch the embedded `AssistantMessageEvent` |
| `MessageEndEvent` | `message_end` | `message` | finalize block → flush to scrollback |
| `ToolExecutionStartEvent` | `tool_execution_start` | `tool_call_id`, `tool_name`, `args` | render tool-call panel (start) |
| `ToolExecutionUpdateEvent` | `tool_execution_update` | `tool_call_id`, `partial_result`, `tool_name`, `args` | update tool panel (optional in thin shell) |
| `ToolExecutionEndEvent` | `tool_execution_end` | `tool_call_id`, `result`, `tool_name`, `is_error` | render tool result |
| `TurnStartEvent` / `TurnEndEvent` | `turn_start` / `turn_end` | `message`, `tool_results` | turn boundary (idle transition) |
| `AgentStartEvent` / `AgentEndEvent` | `agent_start` / `agent_end` | `messages` | session boundary (no-op in thin shell) |

**Streaming layer — `AssistantMessageEvent`** (`packages/aelix-ai/src/aelix_ai/streaming.py:196-377`), the 13-variant ADR-0037 union. Thin-shell renders:

| Event | `type` | Field | TUI action |
|---|---|---|---|
| `TextDeltaEvent` | `text_delta` | `delta: str` | append to live text stream |
| `TextEndEvent` | `text_end` | `content: str` | finalize text block |
| `ThinkingDeltaEvent` | `thinking_delta` | `delta: str` | append to thinking stream (dim style) |
| `ThinkingEndEvent` | `thinking_end` | `content: str` | finalize thinking block |
| `ToolCallStartEvent` / `ToolCallDeltaEvent` / `ToolCallEndEvent` | `toolcall_*` | `delta` (raw JSON), `tool_call` | tool-call argument streaming (thin shell: show on `toolcall_end` via the harness-layer `ToolExecutionStartEvent` instead — avoid double-render) |
| `AssistantDoneEvent` | `done` | `reason`, `message` | terminal success |
| `AssistantErrorEvent` | `error` | `reason` (`aborted`/`error`), `error_message` | render error panel; surface to status |
| `AssistantStartEvent` / `TextStartEvent` / `ThinkingStartEvent` / `ToolCallStartEvent` | `*_start` | — | begin sub-block (mostly no-op; renderer keys off deltas) |
| `AssistantEndEvent` | `end` | (legacy subclass of `done`) | treat as `done` |

`match event.type` is exhaustive; an unrecognised `type` is a no-op (forward-compatible — new event variants don't crash the TUI). Renderer dispatch uses the harness-layer `type` first; for `message_update` it then dispatches on `assistant_message_event.type`.

### §1.4 — Dependency model (already decided — ADR-0088 Q1)

ADR-0088 open-question Q1 is **Resolved (Sprint 6h₉a)**: `pip install aelix[tui]` extra installs `prompt-toolkit` + `rich`. No split. Sprint 6h₁₀a lands the extra on `aelix-coding-agent` (where `entry.py`/`run_tui` live):

```toml
[project.optional-dependencies]
tui = [
  "prompt-toolkit>=3.0,<4",
  "rich>=13.7,<15",
]
```

Because interactive is the **default** mode (no flags + TTY → interactive), the entry seam must degrade gracefully when the extra is absent: a guarded import raises a clean, actionable error (see §3.6) rather than a raw `ModuleNotFoundError`.

**Dev/CI install:** the test harness must install the extra so pyright + pytest resolve `prompt_toolkit` / `rich`. Sprint 6h₁₀a adds `prompt-toolkit` + `rich` to the **root dev dependency group** (or runs `uv sync --all-packages --all-extras`) so the gate environment imports them. Confirm during W2 that `uv run pyright` resolves both (both ship `py.typed`).

### §1.5 — Reference architecture (verified via aider survey)

**aider (`Aider-AI/aider`, `io.py` + `mdstream.py`) — "Option A: sequential ownership"**, the validated pattern for inline-scrolling coding-agent TUIs:

1. **prompt-toolkit `PromptSession` and Rich output never run concurrently.** `PromptSession` owns the terminal *only* while awaiting input; Rich `Console`/`Live` owns it *only* while the agent turn runs. The harness turn (`harness.prompt(...)`) runs while the prompt is **not** active → no cursor contention. This is the single most important invariant for a flicker-free thin shell.
2. **Streaming = `MarkdownStream` pattern** (`aider/mdstream.py`): a `rich.live.Live` region where
   - **stable lines** (all but the trailing `live_window` ≈ 6) are committed to scrollback via `live.console.print(...)` (permanent history),
   - **unstable trailing lines** stay in `live.update(...)` (repainted),
   - **adaptive throttle**: `min_delay = clamp(render_time * 10, 1/20s, 2s)`; deltas arriving faster than `min_delay` are coalesced (ADR-0088 Q10 "~30 FPS max" satisfied by the 1/20s floor),
   - render is done to a throwaway `Console(file=StringIO, force_terminal=True)` to compute ANSI without writing, then split,
   - `final=True` flushes all remaining lines to scrollback, clears + stops `Live`.
3. **asyncio coexistence**: `PromptSession.prompt_async()` inside the existing event loop; `patch_stdout()` only needed if a coroutine prints *while the prompt is active* — in the thin shell the turn does not overlap the prompt, so `patch_stdout` is **not** required (matches aider, which is fully synchronous). Documented as a known constraint; revisited in 6h₁₀b when the live chrome (always-on status region) is added.

**Aelix-additive divergence**: aider is synchronous; Aelix's harness is asyncio. `run_tui` therefore uses `prompt_async()` and `await harness.prompt(...)` sequentially in one coroutine — preserving the sequential-ownership invariant within an async loop.

### §1.6 — Out-of-scope (deferred)

| Item | Owner sprint | Reason |
|---|---|---|
| Concrete `ExtensionUIContext` impl + `bind_ui()` wiring | 6h₁₀b | thin shell does not bind a UI context (extensions stay headless this sprint) |
| Aelix widget layer (`Component`/`Container`/overlays) | 6h₁₀b | — |
| Tier-2 descriptor → Rich renderable mapping | 6h₁₀b | renderer does not exist (6h₉d slot was reassigned to MCP client) |
| Live chrome: persistent status line / footer / working-indicator region | 6h₁₀b | requires resolving prompt-toolkit↔Rich-Live bottom-region contention (user-deferred) |
| Concrete themes (`DARK`/`LIGHT` Rich/pt Style) | 6h₁₀b | thin shell uses Rich defaults |
| Inline images | 6h₁₀c | ADR-0088 Q4 |
| pyte snapshot tests | 6h₁₀d | ADR-0088 Q6 |
| `--resume` interactive picker | 6h₁₀b+ | `entry.py:222-231` still defers; thin shell does not add it |
| Autocomplete provider stacking, custom editor component | 6h₁₀b | ExtensionUIContext surface |
| `steer` / `follow_up` mid-turn input UX | 6h₁₀b | thin shell is one-turn-at-a-time (prompt → await idle → prompt) |

---

## §2 — Scope (deliverables)

| # | Deliverable | Type | Touches |
|---|---|---|---|
| 1 | `[tui]` optional-deps extra + root dev deps + `uv.lock` | Build | `packages/aelix-coding-agent/pyproject.toml`, root `pyproject.toml`, `uv.lock` |
| 2 | NEW `tui/stream.py` — `StreamRenderer` (aider MarkdownStream parity: adaptive throttle, scrollback split, live window) | Code | `…/aelix_coding_agent/tui/stream.py` (NEW) |
| 3 | NEW `tui/render.py` — `EventRenderer` (`AgentEvent` + `AssistantMessageEvent` → Rich dispatch) | Code | `…/tui/render.py` (NEW) |
| 4 | NEW `tui/input.py` — `build_prompt_session()` + `parse_input_line()` (slash/`!`/`!!`/`/quit`/`/reload`) | Code | `…/tui/input.py` (NEW) |
| 5 | NEW `tui/shell.py` — `run_tui(runtime, *, cwd)` scaffolding (rebind + subscribe + input loop + dispose) | Code | `…/tui/shell.py` (NEW) |
| 6 | NEW `tui/__init__.py` — exports `run_tui` | Code | `…/tui/__init__.py` (NEW) |
| 7 | `modes/__init__.py` — re-export `run_tui` (parity with `run_print_mode`/`run_rpc_mode`) | Code | `…/modes/__init__.py` |
| 8 | `cli/entry.py` — replace `NotImplementedError` block; dispatch interactive → `run_tui` | Code | `…/cli/entry.py` |
| 9 | Tests — stream/render/input units + `run_tui` headless smoke (pipe-input + DummyOutput) | Tests | `tests/tui/` (NEW) |
| 10 | qa-tester tmux interactive smoke (real terminal) | QA | manual evidence |
| 11 | ADR-0104 closure | Docs | `docs/decisions/0104-sprint-6h10a-tui-shell.md` (NEW) |

**Module layout** (`packages/aelix-coding-agent/src/aelix_coding_agent/tui/`):
```
tui/
  __init__.py     # run_tui re-export
  shell.py        # run_tui(runtime, *, cwd)  — the print_mode-parallel scaffolding
  render.py       # EventRenderer            — match event.type → Rich
  stream.py       # StreamRenderer           — MarkdownStream parity
  input.py        # build_prompt_session + parse_input_line + ParsedInput
```

---

## §3 — Per-deliverable specifications

### §3.1 — `tui/stream.py` — StreamRenderer (MarkdownStream parity)

Public surface:
```python
class StreamRenderer:
    def __init__(self, console: Console, *, live_window: int = 6,
                 min_delay: float = 1 / 20, max_delay: float = 2.0) -> None: ...
    def update(self, text: str, *, final: bool = False) -> None: ...
```
Behaviour (verbatim from aider `mdstream.py`, adapted):
- First `update` starts a `rich.live.Live(refresh_per_second=…)` bound to `console`.
- Throttle: non-final calls within `min_delay` of the last render are **skipped** (text accumulates in the caller; `StreamRenderer` re-renders the full accumulated `text` each call — the caller owns accumulation, OR `StreamRenderer` accumulates internally — **decision: caller passes full accumulated text**, matching aider).
- Render full `text` to a throwaway `Console(file=StringIO(), force_terminal=True, width=console.width)`; measure `render_time`; set `min_delay = clamp(render_time * 10, 1/20, max_delay)`.
- Split: `num_stable = len(lines)` if `final` else `max(0, len(lines) - live_window)`. New stable lines (beyond already-printed) → `live.console.print(Text.from_ansi(...))`. Update `live.update(Text.from_ansi(trailing))`.
- `final=True`: clear live (`live.update(Text(""))`), `live.stop()`, drop the `Live` ref; remaining stable already in scrollback.
- Idempotent `final` (double-final is a no-op). `update("")` before any text is a no-op.
- **No real terminal required** for tests: inject a `Console(file=StringIO(), force_terminal=True, width=80)`.

Aelix divergence from aider: thin shell renders **plain text** (`Text.from_ansi`) not Markdown (Markdown rendering is a 6h₁₀b polish item — keep the throttle/scrollback machinery, defer `rich.markdown.Markdown`). Document in docstring + ADR.

### §3.2 — `tui/render.py` — EventRenderer

```python
class EventRenderer:
    def __init__(self, console: Console) -> None: ...
    def on_agent_event(self, event: AgentEvent) -> None: ...   # subscribe sink
```
- `match event.type`:
  - `"message_start"`: reset per-message state (new `StreamRenderer` for text; thinking buffer).
  - `"message_update"`: dispatch `event.assistant_message_event` →
    - `text_delta`: append `delta` to text accum; `text_stream.update(accum)`.
    - `text_end`: `text_stream.update(content, final=True)`.
    - `thinking_delta` / `thinking_end`: dim-styled stream (separate `StreamRenderer` or a single shared one reset between sections; thin shell: render thinking with `console.print` dim, no live window needed — simplest correct option).
    - `done` / `end`: finalize any open text stream.
    - `error`: `console.print` an error panel (`reason` + `error_message`).
    - `toolcall_*`: **no-op** (tool rendering keyed off the harness-layer `tool_execution_*` events to avoid double-render).
    - others (`start`, `text_start`, `thinking_start`, `toolcall_start`): no-op.
  - `"tool_execution_start"`: `console.print` a tool header (`tool_name` + compact `args`).
  - `"tool_execution_end"`: `console.print` tool result; if `is_error`, error style.
  - `"tool_execution_update"`: no-op (thin shell).
  - `"message_end"` / `"turn_end"`: ensure open streams finalized.
  - `"agent_start"`/`"agent_end"`/`"turn_start"`: no-op.
  - unknown: no-op (forward-compatible).
- The subscribe sink is **synchronous** (`HarnessListener` accepts `Callable[[AgentEvent], Awaitable | None]`; thin shell uses the sync form, like `run_print_mode._emit`). No `await` inside the sink → no reentrancy with the turn coroutine.
- All Rich writes go to the single shared `Console` (→ scrollback). Sequential-ownership invariant (§1.5) means the prompt is not active during these writes.

### §3.3 — `tui/input.py` — input parsing + PromptSession

```python
@dataclass(frozen=True)
class ParsedInput:
    kind: Literal["prompt", "bash", "bash_transient", "reload", "quit", "empty"]
    text: str = ""

def parse_input_line(line: str) -> ParsedInput: ...   # pure, fully unit-testable

def build_prompt_session(*, history_path: str | None = None) -> PromptSession: ...
```
- `parse_input_line` mirrors `run_repl` precedence exactly: empty→`empty`; `/quit`|`/exit`→`quit`; `/reload`→`reload`; `!!cmd`→`bash_transient`; `!cmd`→`bash`; else→`prompt`. **Pure function** — the bulk of input tests hit this with zero prompt-toolkit dependency.
- `build_prompt_session`: `PromptSession(multiline=False, …)` with prompt `"» "` (parity with `run_repl`'s `"» "`). `FileHistory` when `history_path` given (e.g. `~/.aelix/history`). Enter submits (thin shell: single-line default; multi-line via `Alt+Enter` deferred to 6h₁₀b — single-line is sufficient for the thin shell and avoids the multiline keybinding surface). `placeholder` hint text optional.
- Reuse `cli.repl.handle_user_bash` for `bash`/`bash_transient` execution (no duplication).

### §3.4 — `tui/shell.py` — run_tui

```python
async def run_tui(runtime_host: AgentSessionRuntime, *, cwd: str) -> int: ...
```
Structural parity with `run_print_mode` (`print_mode.py:98-217`):
1. Signal handlers (SIGTERM/SIGHUP, non-Windows, best-effort) — same as `run_print_mode:114-131`.
2. `unsubscribe_holder` + `_rebind(new_harness)` closure that drops the prior subscription and re-subscribes the `EventRenderer.on_agent_event` sink → `runtime_host.set_rebind_session(_rebind)` (survives session swaps, like `print_mode.py:133-153`).
3. `await runtime_host.harness.bootstrap()` (once; `run_repl` precedent at `repl.py:88`).
4. Initial `_rebind(runtime_host.harness)`.
5. Input loop: `await session.prompt_async("» ")` →
   - `EOFError`/`KeyboardInterrupt` at empty prompt → exit loop;
   - `KeyboardInterrupt` while a turn is queued → `await harness.abort()` (best-effort) then continue;
   - `parse_input_line` → dispatch: `quit`→break; `reload`→`await harness.reload_resources()`; `bash`/`bash_transient`→`handle_user_bash(...)` + `console.print` output; `prompt`→`await harness.prompt(text, source="interactive")` (note `source="interactive"`); `empty`→continue.
6. `finally`: unsubscribe + remove signal handlers + `await runtime_host.dispose()` + final flush. (Same shape as `print_mode.py:203-216`.)
- Returns exit code `0` on clean exit.
- Uses `from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime` under `TYPE_CHECKING` (parity with `print_mode.py:42-45`).

### §3.5 — `tui/__init__.py` + `modes/__init__.py`

- `tui/__init__.py`: `from .shell import run_tui` + `__all__ = ["run_tui"]`.
- `modes/__init__.py`: add `from aelix_coding_agent.tui import run_tui` and extend `__all__` to `["run_print_mode", "run_rpc_mode", "run_tui"]`. (Keeps `entry.py`'s existing `from aelix_coding_agent.modes import …` import convention.)

### §3.6 — `cli/entry.py` wiring

Replace the `entry.py:245-254` interactive block. Move the interactive dispatch into the post-construction `try` (after `runtime` exists at line 302) so it parallels rpc/print. The interactive branch:

```python
if app_mode == "interactive":
    try:
        from aelix_coding_agent.modes import run_tui
    except ImportError as exc:  # [tui] extra not installed
        print(
            "Error: interactive mode requires the TUI extra. "
            "Install with: pip install 'aelix-coding-agent[tui]'  "
            f"(missing: {exc.name}).",
            file=sys.stderr,
        )
        return 1
    return await run_tui(runtime, cwd=str(Path.cwd()))
```
- The guarded import keeps `prompt-toolkit`/`rich` truly optional: a headless/server install without `[tui]` gets a clean exit-1 message instead of a stack trace.
- Stdin/file processing (`entry.py:256-274`) and `build_initial_message` are **skipped** for interactive (no piped stdin in a TTY; initial `@file`/`-m` messages for interactive are a 6h₁₀b concern — thin shell starts empty). Guard: only run the stdin/file block for `app_mode in ("print", "json")`. (rpc already skips it.) Verify `parsed.messages`/`@file` + interactive is either ignored or warned — **decision: ignore silently this sprint**, matching that the thin shell starts at an empty prompt; note as a 6h₁₀b carry-forward.
- The existing outer `finally: await runtime.dispose()` (`entry.py:326-331`) double-disposes harmlessly (`run_tui` disposes too; `dispose` is idempotent / suppressed — same as the `run_print_mode` comment at `entry.py:327`).

### §3.7 — Tests (`tests/tui/`)

| File | Coverage |
|---|---|
| `test_stream_renderer.py` | throttle skip within `min_delay`; adaptive `min_delay` clamp bounds; stable/unstable split at `live_window`; scrollback receives stable lines; `final=True` flushes + stops; double-final no-op; `update("")` no-op. Inject `Console(StringIO, force_terminal=True, width=80)`; assert on `StringIO.getvalue()`. |
| `test_event_renderer.py` | feed each `AgentEvent`/`AssistantMessageEvent` variant; assert Rich output captured to a `StringIO` Console: text_delta accumulation, thinking dim, tool start/end (incl. `is_error`), error panel, `done`/`end` finalize, unknown-type no-op (forward-compat). |
| `test_input_parsing.py` | `parse_input_line` truth table for all 6 kinds + edge cases (`!` alone, `!!` alone, `  /quit  `, whitespace, leading `!` inside prompt-only-after-strip). Pure function — no prompt-toolkit. |
| `test_run_tui_smoke.py` | headless `run_tui`: build a fake `AgentSessionRuntime` + fake `AgentHarness` (records `bootstrap`/`subscribe`/`prompt`/`abort`/`reload_resources`/`dispose`); drive input via `prompt_toolkit.input.create_pipe_input()` + `DummyOutput()`; feed `"hello\n"`, `"/reload\n"`, `"!echo hi\n"`, `"/quit\n"`; assert: subscribe called, `set_rebind_session` called, `prompt("hello", source="interactive")` called, `reload_resources` called, `dispose` called once, returns 0. |
| `test_entry_interactive_dispatch.py` | monkeypatch `run_tui` to a recording stub; assert `_async_main` with no flags + faked TTY stdin routes to `run_tui(runtime, cwd=…)`; assert missing-extra `ImportError` path prints the actionable message + returns 1. |

prompt-toolkit headless test idiom (verified-standard): `create_pipe_input()` context manager feeds bytes; `PromptSession(input=pipe, output=DummyOutput())` runs without a real TTY. Use this for `test_run_tui_smoke.py`.

Target: ~45-60 tests. All deterministic, no real terminal, no sleeps (throttle tests manipulate the internal `when` timestamp or inject a clock, not `time.sleep`).

### §3.8 — qa-tester real-terminal smoke (deliverable #10)

A `qa-tester` tmux session: launch `aelix` (no args) in a real PTY, type a prompt, observe streamed output flows into scrollback + the prompt returns, `/quit` exits cleanly, exit code 0. Evidence: captured tmux pane text. This is the manual-QA requirement (ultrawork policy) — diagnostics alone are insufficient.

### §3.9 — ADR-0104 closure

`docs/decisions/0104-sprint-6h10a-tui-shell.md`. Front-matter per ADR-0103 template (Status: Accepted / Sprint 6h₁₀a / Phase 5c-tui / W6 shipped; Date 2026-05-25; Pi pin held; top-level principle line). Required sections: Context (the seam + thin-shell scope), Decision (the 11 deliverables + module layout + aider Option-A architecture), `## Aelix-additive divergences from Pi` (async vs sync; plain-text vs Markdown this sprint; `[tui]` extra graceful-degrade), `## Reference companions` (ADR-0088 §arch + Q1/Q10, ADR-0089 NotImplementedError owner, ADR-0100 ExtensionUIContext, ADR-0103 Phase 5b complete; aider io.py/mdstream.py), `## Deferred (Phase 5c carry-forward)` (= §1.6), `## Verification` (= §5), `## Phase` (Sprint 6h₁₀a shipped; next 6h₁₀b — ExtensionUIContext concrete + widgets + descriptor renderer + live chrome).

---

## §4 — Commit split plan (W6, atomic)

| # | §  | Stage | Message subject |
|---|---|---|---|
| 1 | §A | `pyproject.toml` (coding-agent + root) + `uv.lock` | `build(coding-agent): [tui] extra (prompt-toolkit + rich) (Sprint 6h₁₀a §A)` |
| 2 | §B | `tui/stream.py` + `tests/tui/test_stream_renderer.py` | `feat(tui): StreamRenderer — aider MarkdownStream parity (Sprint 6h₁₀a §B)` |
| 3 | §C | `tui/render.py` + `tests/tui/test_event_renderer.py` | `feat(tui): EventRenderer — AgentEvent/AssistantMessageEvent → Rich (Sprint 6h₁₀a §C)` |
| 4 | §D | `tui/input.py` + `tests/tui/test_input_parsing.py` | `feat(tui): prompt-toolkit input + line parsing (Sprint 6h₁₀a §D)` |
| 5 | §E | `tui/shell.py` + `tui/__init__.py` + `modes/__init__.py` + `tests/tui/test_run_tui_smoke.py` | `feat(tui): run_tui shell — subscribe/rebind/dispose scaffolding (Sprint 6h₁₀a §E)` |
| 6 | §F | `cli/entry.py` + `tests/tui/test_entry_interactive_dispatch.py` | `feat(cli): wire interactive mode → run_tui (Sprint 6h₁₀a §F)` |
| 7 | §G | ADR-0104 | `docs: ADR-0104 TUI shell closure — Phase 5c-tui sprint 1 (Sprint 6h₁₀a §G)` |

All commit messages end with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`. **Commits are NOT pushed; do not commit until the user authorizes** (per the project's working convention — the prior sprint was "NOT committed per instruction").

---

## §5 — Verification gates (W5)

Run the project gate command (from project memory):
```
ruff check  →  clean
uv run pyright  →  exactly 8 baseline errors (all in scripts/pyright_spike.py); ZERO from tui/
uv run pytest  →  all prior tests pass + new tui/ tests pass (2540 baseline + new)
python scripts/generate_contracts_schemas.py --check  →  exit 0
git diff --stat -- <protected paths>  →  EMPTY (byte-unchanged)
```
**Protected paths (must be byte-unchanged):** `packages/aelix-coding-agent/src/aelix_coding_agent/rpc`, `packages/aelix-agent-core/src/aelix_agent_core/harness`, `packages/aelix-coding-agent/src/aelix_coding_agent/mcp`, `scripts/pyright_spike.py`, `docs/contracts`. Sprint 6h₁₀a touches none of them.

Additional gates:
- `uv sync --all-packages --all-extras` succeeds (the `[tui]` extra resolves prompt-toolkit + rich).
- Boot smoke: `aelix` (TTY) launches the shell; `/quit` exits 0. (qa-tester evidence, §3.8.)
- Graceful-degrade smoke: with `[tui]` uninstalled, interactive mode prints the actionable message + exits 1 (simulated by monkeypatching the import in `test_entry_interactive_dispatch.py`).
- New tests emit ZERO warnings under `-W error` (clean Live teardown, no orphaned asyncio tasks, pipe-input closed).

W2 implementation → W3 self-review pass → **W4 independent review** (`code-reviewer` HIGH + `test-engineer` for test adequacy; `security-reviewer-low` for the bash-passthrough path) → W5 gates → W6 commits + ADR.

---

## §6 — Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| prompt-toolkit `prompt_async` + asyncio loop interaction (double-loop, signal handlers) | Med | Model exactly on `run_print_mode` signal handling; `prompt_async` runs in the already-running loop; headless smoke test covers it |
| Rich `Live` cursor contention with the prompt | Low (thin shell) | Sequential ownership (§1.5): no `Live` active while prompt is awaiting; turn fully completes (`await harness.prompt`) before the next `prompt_async` |
| pyright cannot resolve `prompt_toolkit`/`rich` (optional extra) | Med | Add both to root dev deps; CI runs `--all-extras`; both ship `py.typed` |
| Double-dispose (entry `finally` + `run_tui` `finally`) | Low | `dispose` idempotent + suppressed (existing precedent, `entry.py:327`) |
| KeyboardInterrupt during streaming | Med | Catch in input loop → `await harness.abort()`; covered by smoke test |
| Accidental edit to a protected path | Low | §5 byte-unchanged diff gate; tui/ is a new isolated subpackage |

---

## §7 — Acceptance criteria

- [ ] `aelix` with no args in a TTY launches an interactive shell (no `NotImplementedError`).
- [ ] A user prompt drives a real turn; streamed text/thinking/tool output renders to scrollback with adaptive throttling.
- [ ] `!cmd` / `!!cmd` / `/reload` / `/quit` / `/exit` behave per `run_repl` precedent.
- [ ] Session-swap (`set_rebind_session`) keeps events flowing (parity with print/rpc).
- [ ] Clean teardown: unsubscribe + dispose; exit code 0; zero warnings under `-W error`.
- [ ] Without the `[tui]` extra: actionable error + exit 1 (no stack trace).
- [ ] All gates green (§5); protected paths byte-unchanged.
- [ ] ADR-0104 shipped; spec deferred items carried forward to 6h₁₀b.
