# Sprint 6h₁₂d (TUI completeness — Sprint D) — Model / context slash commands

**Status:** DRAFT (W2, pre-grounded while Sprint C runs). From the 6h₁₂ audit (P1 #7,8,9,10,14,15).
Closure ADR = 0113. PURE-CONSUMER (`tui/commands.py` + `shell.py` + `chrome.py`); no contract change
(core.py is protected — call its EXISTING public methods; do NOT add wrappers there).

## §1 — Scope
Wire the built-in command registry (Sprint A) onto the harness APIs that already exist:
| cmd | behavior | harness API (verified public unless noted) |
|---|---|---|
| `/model [id]` | no arg → show current model; with id → switch | `current_model` (prop), `set_model(Model)` (build the `Model` via `cli.runtime_bootstrap.resolve_model(id, None)`); footer reflects via the live `model_provider` |
| `/clear` | clear the scrollback transcript | NEW `chrome.clear()` (emit `\x1b[3J\x1b[2J\x1b[H` via the same in_terminal path, or `app.renderer.clear()`) |
| `/compact [instructions]` | compact context; report before/after | `compact(custom_instructions)` → `CompactResult` |
| `/cost` | show session token/cost/context usage | `get_session_stats()` → render a small table |
| `/tools` | list tools + active state | `state.tools` / `_action_get_all_tools()` (semi-private — call directly, documented; can't wrap in protected core.py). Toggle = stretch via `set_active_tools` |
| `/mode [name]` | show / set steering mode | `set_steering_mode(mode)` (+ reflect in the footer `⏵⏵` segment) |

## §2 — Design
### §2.1 Handler-with-args evolution (`commands.py`)
`BuiltinCommand.handler` signature evolves `(ctx) -> Awaitable[None]` → `(ctx, args: str) -> Awaitable[None]`
(args = the text after the command word; `""` when none). `match_command` already isolates the word
via `slash_word`; the dispatch passes `args = text[len("/"+word):].strip()`. Update the existing
`/help` handler to the new signature (ignores args). Add handlers: `_model_handler`, `_clear_handler`,
`_compact_handler`, `_cost_handler`, `_tools_handler`, `_mode_handler`. Append the 6 commands to
`BUILTIN_COMMANDS` (so the palette + `/help` list them automatically).
### §2.2 `CommandContext` additions
Add what handlers need beyond chrome/harness/commit/cwd: nothing new structurally — handlers reach
the harness via `ctx.harness`, the chrome via `ctx.chrome`, commit via `ctx.commit`. `/model` imports
`resolve_model` from `cli.runtime_bootstrap`. `/mode` may need to update the footer mode → add a
`set_mode: Callable[[str], None] | None` to the context (run_tui wires it to update the context's
`_mode` + `_refresh_footer`), or re-read mode live in `_refresh_footer` (preferred: a `mode_provider`).
### §2.3 `chrome.py` — `clear()`
`AelixChrome.clear()` clears the terminal scrollback (printed history) without killing the chrome.
Use the in_terminal/print path to emit the clear sequence (or `self.app.renderer.clear()` if it
clears scrollback). Verify headlessly it doesn't raise under DummyOutput.
### §2.4 Dispatch (`shell.py`)
`_input_loop` already routes `/cmd` → `match_command` → handler. Pass `args` to the handler. Errors
in a handler are contained + surfaced (commit a red line), never kill the REPL.

## §3 — Constraints
- core.py / contracts / rpc / harness / mcp byte-unchanged — call EXISTING public harness methods;
  `_action_get_all_tools` called directly (documented coupling) since wrapping it would touch protected core.
- `/model` switch builds a `Model` from the id via `resolve_model` (OpenRouter-aware) — degrade with a
  clear message if resolution fails; the live `model_provider` footer reflects the new model.
- pyright 8-baseline; full suite green; descriptor/extension command-routes still merge in the palette.
- Each handler defensive (getattr-guarded harness access; headless FakeHarness lacks some methods →
  degrade with a message, don't crash).

## §4 — Test plan
- handler-args: dispatch passes the post-word args; `/model gpt-4o` → args "gpt-4o".
- `/model` no-arg shows current; with id calls set_model (fake harness records); `/compact` calls
  compact; `/cost` renders stats; `/clear` calls chrome.clear(); `/tools` lists; `/mode x` sets mode +
  footer reflects. All via fakes; headless.
- palette + /help now list the 6 new commands.

## §5 — Atomic commit plan (await authorization)
| # | message |
|---|---|
| 1 | `feat(tui): handler args + /model /clear /compact (Sprint 6h₁₂d)` → commands.py + chrome.clear + shell dispatch + tests |
| 2 | `feat(tui): /cost /tools /mode (Sprint 6h₁₂d)` → commands.py + context mode + tests |
| 3 | `docs: ADR-0113 TUI model/context commands (Sprint 6h₁₂d)` → ADR |
Trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Spec stays local.
Note: D is larger than A–C; may split into D1 (model/clear/compact) + D2 (cost/tools/mode) if review prefers.
