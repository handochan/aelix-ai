# 0113. Sprint 6h₁₂d — Model / Context Slash Commands

Status: Accepted (TUI completeness Sprint D / W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — pure tui/ consumer)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context
From the 6h₁₂ audit (P1 #7-10,14,15). The harness already exposed model/context capabilities; the
TUI didn't surface them. This sprint wires 6 first-party commands onto **existing public** harness
methods (core.py is protected — no wrappers added there).

## The decisions (pure `tui/` consumer)
- **Handler-args evolution**: `BuiltinCommand.handler` → `(ctx, args: str)`; `_input_loop` passes
  `args = text[len("/"+slash_word):].strip()`. `/help` ignores args.
- **6 commands** (each `getattr`/`hasattr`-guarded → degrade with a committed message, never crash
  the REPL): `/model` (show `current_model`; `/model <id>` → `resolve_model(id,None)` + `set_model`),
  `/clear` (`chrome.clear()`), `/compact [instr]` (`compact()` → before/after panel), `/cost`
  (`get_session_stats()` table), `/tools` (`_action_get_all_tools()` — semi-private, documented
  direct call), `/mode [name]` (`set_steering_mode` + footer reflection via the `set_mode` ctx
  callback). Appended to `BUILTIN_COMMANDS` (auto-listed in palette + `/help`).
- **`AelixChrome.clear()`**: writes `\x1b[3J\x1b[2J\x1b[H` via `app.output.write_raw`+flush then
  `invalidate()`; whole body `suppress(Exception)` → headless-safe (DummyOutput).
- **`/model` footer refresh + empty-provider caution** (W4 MEDIUM fixes): the footer `✱` is a cached
  string recomposed only on `_refresh_footer()`, so `/model` now calls a `refresh_footer` ctx
  callback (wired to `context._refresh_footer`) after `set_model` — the footer reflects the new model
  live. When `resolve_model` yields an empty provider (no OpenRouter key), the switch reports a
  **yellow caution** ("no provider resolved — turns may fail") instead of green success, so the
  failure isn't deferred to a confusing later point.

## Consequences
- All 6 commands work + are listed in `/help` and the `/` palette (live-verified). REPL is
  crash-safe (every handler degrades gracefully). pyright 8-baseline; protected paths byte-unchanged.
- **Known (deferred polish)**: `/tools` on a harness lacking the API says "No tools registered"
  rather than "unavailable" (LOW); `/cost` omits `context_usage` (NIT); `/compact`'s `result is None`
  branch is unreachable against the real harness (which RAISES "Nothing to compact" → rendered as a
  contained red line, not the intended yellow — acceptable graceful degradation).

## Verification (W4)
- Gate: ruff clean; `uv run pyright` 8-baseline (0 new); full `pytest` green (+ 6-handler tests,
  crash-safety matrix, handler-args, `/model` footer-refresh + empty-provider, `/clear` headless,
  `/mode` footer reflection); protected paths byte-unchanged.
- **W4 code-reviewer (opus): APPROVE-WITH-NITS** — crash-safety + dispatch consistency verified
  solid; 2 MEDIUM (`/model` footer staleness; empty-provider success) **fixed in-sprint** + tests.
- **W4 qa-tester real-PTY (gpt-4o-mini): 9/10 PASS + 1 PARTIAL→fixed** — `/help`+palette list all 6;
  `/model` shows current + switches; `/tools` lists 7; `/clear` clears scrollback; `/cost`/`/mode`
  render; `/compact` degrades gracefully; the `/model` footer-staleness PARTIAL is the MEDIUM now fixed.

This completes the audit's P0 (Sprints A,B,C) + P1 (Sprint D). Sprint E (P2 polish — thinking
collapse, @file completion, error panels, multiline input) remains, optional.
