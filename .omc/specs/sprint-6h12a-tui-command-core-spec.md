# Sprint 6h₁₂a (TUI completeness — Sprint A) — Built-in command core + palette + /help + banner

**Status:** DRAFT (W2). From the 6h₁₂ TUI audit (P0 #1/2/3). Closure ADR = 0110.
All PURE-CONSUMER (`tui/` + minor `cli/`); no contract change. Reference: Claude Code / Pi / the
user's Qwen tool (`/`-palette showing `/mode`·`/model`; built-in commands are first-party, NOT
extension descriptors).

## §1 — Problem
The TUI has the descriptor/extension "rails" but no **first-party** command vocabulary:
`parse_input_line` (`input.py:41-44`) knows only `/quit`/`/exit`/`/reload`; any other `/x` is sent
to the model (`shell.py:305-326`); the only completer is descriptor-sourced (empty w/o extensions,
`completion.py:54`); no `/help`, no startup banner. This sprint adds the **built-in command core**:
a registry, palette autocomplete (built-ins ∪ descriptor routes), `/help`, a startup banner, and
"unknown command" handling. Handler-bearing commands beyond `/help` (`/model`/`/clear`/`/compact`/
`/tools`/`/mode`) are Sprint D (they need harness-API wiring); this sprint ships the infrastructure
+ the commands that work today.

## §2 — Design

### §2.1 `tui/commands.py` (NEW) — registry
- `@dataclass(frozen=True) BuiltinCommand`: `name: str` (no leading `/`), `description: str`,
  `handler: Callable[[CommandContext], Awaitable[None]] | None` (None = dispatched elsewhere, e.g.
  `parse_input_line` owns quit/exit/reload; the entry exists for palette + `/help` listing).
- `@dataclass CommandContext`: `chrome`, `harness`, `commit: Callable[[object], None]`, `cwd: str`,
  `commands: list[BuiltinCommand]` (so `/help` can list).
- `BUILTIN_COMMANDS: list[BuiltinCommand]` for Sprint A:
  - `help` — "List available commands" → handler renders the command table.
  - `quit` / `exit` — "Exit Aelix" → handler=None (parse_input_line → kind=quit).
  - `reload` — "Reload extensions + resources" → handler=None (parse_input_line → kind=reload).
  (Sprint D appends model/clear/compact/tools/mode with real handlers.)
- `build_help_renderable(commands) -> Rich renderable` — a table/panel of `/name  description`.
- `match_command(text, commands) -> BuiltinCommand | None` — parse the leading `/word` from a
  slash line and look it up (case-sensitive, exact name). PURE.

### §2.2 `tui/completion.py` (EDIT) — palette union
Extend the completer so the `/`-palette offers **built-in commands ∪ descriptor command-routes**.
Add a `builtins: list[BuiltinCommand]` (or a `get_builtins` callable) to `DescriptorCommandCompleter`
(or a thin wrapper). On a `/`-prefixed line, yield a `Completion` per matching built-in (display =
name, meta = description) AND per matching descriptor route, deduped by command name (built-in wins).

### §2.3 `tui/shell.py` (EDIT) — dispatch + banner
- Build `BUILTIN_COMMANDS` + a `CommandContext` (chrome/harness/commit/cwd) in `run_tui`; pass the
  built-ins into the completer (`set_command_completer`) alongside the descriptor routes.
- In `_input_loop`, for a `prompt`-kind line that `startswith("/")`: resolve in order —
  1. built-in registry (`match_command`): if matched + handler → `await handler(ctx)`; `continue`.
  2. descriptor management-modal (`_match_management_modal`) → `open_modal` (existing).
  3. otherwise → commit `Unknown command: /x — type /help` (do NOT send a bare `/x` to the model).
  (quit/exit/reload still handled above via `parse_input_line`; non-slash prompts unchanged.)
- **Startup banner**: after bootstrap, before the input loop, commit a banner via `output_queue`:
  `Aelix` + model id + cwd + "Type /help for commands." (Rich panel/text). Empty-state hint.

### §2.4 `cli/repl.py` (no change) / `input.py` (no change)
`parse_input_line` stays PURE (the two-layer split — parser owns quit/reload, the registry owns the
rest — is documented in `commands.py`).

## §3 — Module layout
```
tui/commands.py     # BuiltinCommand, CommandContext, BUILTIN_COMMANDS, build_help_renderable, match_command  (NEW)
tui/completion.py   # completer yields built-ins ∪ descriptor routes                                          (EDIT)
tui/shell.py        # registry + ctx build, completer wiring, /command dispatch, startup banner               (EDIT)
tests/tui/test_commands.py  # registry + match_command + /help + completer union + unknown-command + banner   (NEW/EDIT)
```

## §4 — Constraints
- Contracts/rpc/harness/mcp/docs-contracts byte-unchanged. pyright 8-baseline (0 new). Full suite green.
- The completer must still work with zero descriptor routes (built-ins always present).
- Headless-testable: registry/match/help renderable are pure; dispatch tested via a fake chrome/harness; banner content asserted.
- Reference parity: a `/`-palette that lists commands with descriptions; `/help` listing; a banner — matching Claude Code / the user's reference.

## §5 — Test plan
- `match_command("/help …", cmds)` → help; `/nope` → None; non-slash → None.
- `build_help_renderable` lists every command name+description.
- completer: `/h` → `/help`; built-ins ∪ a descriptor route both appear; dedup built-in over route.
- dispatch (fake ctx): `/help` runs handler (commits help); unknown `/x` commits the unknown-command hint (NOT sent to harness.prompt); a descriptor modal `/cmd` still opens.
- banner: contains model id + cwd + "/help".

## §6 — Atomic commit plan (await authorization)
| # | message |
|---|---|
| 1 | `feat(tui): built-in command registry + /help + palette union (Sprint 6h₁₂a)` → commands.py + completion.py + tests |
| 2 | `feat(tui): /command dispatch + startup banner in run_tui (Sprint 6h₁₂a)` → shell.py + tests |
| 3 | `docs: ADR-0110 TUI built-in command core (Sprint 6h₁₂a)` → ADR |
Trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Spec stays local.
