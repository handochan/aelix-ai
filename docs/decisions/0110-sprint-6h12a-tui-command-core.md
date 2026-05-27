# 0110. Sprint 6h₁₂a — TUI Built-in Command Core (palette + /help + banner)

Status: Accepted (TUI completeness Sprint A / W5 shipped)
Date: 2026-05-26
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — pure tui/ consumer)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

Live testing surfaced that the TUI had the descriptor/extension command **rails**
(`DescriptorCommandCompleter`, `_match_management_modal`) but **no first-party command vocabulary**:
`parse_input_line` knew only `/quit`/`/exit`/`/reload`; any other `/x` was sent to the model; the
only completer was descriptor-sourced (empty without extensions); and there was no `/help` or
startup banner. A comprehensive TUI audit (6h₁₂ W1) catalogued 24 gaps (P0–P2); this sprint (A)
ships the **P0 command core** — the rest follow in Sprints B–E.

## The decisions (all pure `tui/` consumer)

- **`tui/commands.py` (NEW)**: a frozen `BuiltinCommand` registry (`BUILTIN_COMMANDS` = `/help`
  with a handler + `/quit`/`/exit`/`/reload` as metadata-only entries, since `parse_input_line`
  dispatches those), a `CommandContext` (chrome/harness/commit/cwd/commands), `build_help_renderable`
  (a Rich panel table), and pure `slash_word` / `match_command` lookups.
- **`tui/completion.py`**: the `/`-palette now offers **built-ins ∪ descriptor command-routes**,
  deduped by name with the built-in winning (a descriptor can't shadow `/help`). Built-ins listed
  first; live descriptor routes preserved; works with zero extensions.
- **`tui/shell.py`**: `run_tui` builds the registry + `CommandContext`, wires the union completer
  (with a built-ins-only fallback in headless mode), commits a **startup banner**
  (`Aelix` · model id · cwd · "Type /help for commands.") after bootstrap, and in `_input_loop`
  dispatches a `prompt`-kind `/`-line in order: built-in handler → descriptor management-modal →
  else an "Unknown command: /x — type /help" hint (a `/x` is **not** sent to the model).
- **`input.py`**: `parse_input_line` now matches `/quit`/`/exit`/`/reload` on the **first token**
  (trailing args ignored) so `/reload now` resolves to reload rather than mis-reporting "Unknown
  command" — fixing a W4 review MEDIUM. `slash_word` is shared by `match_command` and the shell's
  unknown-command label so they cannot drift.

## Consequences
- The user's loudest complaint is resolved: a working `/`-palette, `/help`, a startup banner, and
  no more leaking `/x` to the model. Verified live (see below).
- The handler-bearing commands beyond `/help` (`/model`/`/clear`/`/compact`/`/tools`/`/mode`) are
  Sprint D (they need harness-API wiring); the registry + palette make them drop-in.
- pyright holds the 8-error baseline; protected paths byte-unchanged.

### Known limitation (deferred)
- A prompt that legitimately **starts with `/`** (e.g. a path/regex question) is intercepted as an
  unknown command and not sent to the model — matching Claude-Code-class reference behavior. A
  documented escape hatch (e.g. leading-space / doubled-slash to force model-send) is a follow-up.
- Two-layer split: `parse_input_line` owns quit/exit/reload dispatch; the registry owns the rest +
  all command metadata. `cli/repl.py` keeps its own inline precedence copy (not shared) — a future
  consolidation could remove that duplication.

## Verification (W4)
- Gate: ruff clean; `uv run pyright` 8-baseline (0 new); full `pytest` green (+ ~22 new tests:
  commands registry/match/help, completer union/dedup, dispatch, banner, args-tolerant parse);
  protected paths byte-unchanged.
- **W4 code-reviewer (opus): APPROVE-WITH-NITS** (0 CRITICAL/HIGH; dispatch traced clean across 20
  input variants — no prompt swallowed). MEDIUM `/reload now` mislabel **fixed** (first-token parse);
  slash-prefix blocking documented as a known limitation; the slash-word parse DRY'd.
- **W4 qa-tester real-PTY (gpt-4o-mini): 6/6 PASS** — banner (Aelix·model·cwd·/help) renders;
  `/help` lists commands; the `/`-palette fires automatically on `/` listing all built-ins; unknown
  `/x` shows the hint (not sent to the model); a normal prompt still streams; `/quit` exits cleanly.

Next: Sprint B (footer mode·cwd·model + user-message echo + esc-to-interrupt), then C (tool cards),
D (model/context commands), E (polish).
