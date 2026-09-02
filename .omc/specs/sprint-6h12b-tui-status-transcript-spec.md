# Sprint 6h₁₂b (TUI completeness — Sprint B) — Rich footer + user-message echo + esc-to-interrupt

**Status:** DRAFT (W2). From the 6h₁₂ audit (P0 #4, #6; P1 #13). Closure ADR = 0111.
PURE-CONSUMER (`tui/` + `context`); no contract change. Reference footer:
`⏵⏵ default  ·  📂 ~/.deepsight  ·  ✱ Qwen/Qwen3.6-35B-A3B-FP8`.

## §1 — Three gaps
- **#4 footer**: `context._refresh_footer` (context.py:359-367) composes only `⎇ branch` + ext
  statuses — no mode/cwd/model. (`AelixTUIContext.__init__(chrome, footer)` has neither model nor cwd.)
- **#6 user echo**: `_input_loop` calls `harness.prompt(text)` with NO echo (shell.py) — the user's
  own message never appears in the transcript, so assistant replies have no visible question above.
- **#13 esc-to-interrupt**: interrupt is Ctrl-C only (`chrome._build_key_bindings`); no Esc binding,
  no on-screen hint. (Working line shows a bare "Working…".)

## §2 — Design

### §2.1 Footer `⏵⏵ mode · 📂 cwd · ✱ model · ⎇ branch` (`context.py`)
- `AelixTUIContext.__init__` gains optional `model_provider: Callable[[], str | None] | None = None`,
  `cwd: str | None = None`, `mode: str = "default"` (all optional → existing tests/extensions
  unaffected; the extension `footer_factory` branch is unchanged + still takes precedence).
- `_refresh_footer` default branch composes, joined by `"  ·  "`, the present segments:
  `⏵⏵ {mode}` (if mode) · `📂 {cwd_display}` (home-abbreviated to `~`; if cwd) ·
  `✱ {model}` (if `model_provider()` returns non-empty) · `⎇ {branch}` (if branch) · ext statuses.
  Each segment omitted when its source is absent → with no model/cwd it degrades to today's
  branch-only footer.
- `run_tui` (`shell.py`) wires `AelixTUIContext(chrome, footer, model_provider=lambda: (m.id if (m:=harness.current_model) else None), cwd=cwd, mode="default")`. Read live so a future `/model` (Sprint D) reflects immediately. Trigger `_refresh_footer` once after bind so the model shows at start.

### §2.2 Echo the user message (`shell.py` `_input_loop`)
Before `harness.prompt(parsed.text, ...)`, commit the user line with a role marker:
`output_queue.put_nowait(("commit", Text(f"» {parsed.text}", style="bold")))`. Only for the
model-prompt path (NOT bash/commands/empty). So the transcript reads `» <question>` then the
assistant's markdown reply.

### §2.3 esc-to-interrupt (`chrome.py`)
- Add `@kb.add("escape", filter=Condition(lambda: self._running))` → calls `self.on_interrupt()`
  (same as Ctrl-C while running). Gated on `_running` so Esc is inert when idle (doesn't interfere
  with editing / alt-key sequences when not in a turn).
- Working-line hint: `_render_working` appends `" · esc to interrupt"` (dim) when running, so the
  spinner line reads e.g. `⠴ Working… · esc to interrupt`. (Read `_render_working` for the exact
  compose; keep the existing message/spinner.)

## §3 — Module layout
```
tui/context.py   # AelixTUIContext.__init__ (+model_provider/cwd/mode) + _refresh_footer compose   (EDIT)
tui/shell.py     # wire model_provider/cwd into the context; echo user message in _input_loop        (EDIT)
tui/chrome.py    # Esc keybinding (running-gated) + esc-to-interrupt hint in _render_working          (EDIT)
tests/tui/       # footer compose (mode/cwd/model/branch + degradation); user-echo commit; esc binding fires on_interrupt only when running; working-hint
```

## §4 — Constraints
- Contracts/rpc/harness/mcp byte-unchanged. pyright 8-baseline (0 new). Full suite green.
- Footer degrades gracefully (no model/cwd → branch-only, as today) so headless tests + the
  extension footer_factory path are unaffected.
- Esc must NOT break editing when idle (running-gated) nor the completion menu (Esc closing the menu
  is prompt-toolkit default; confirm the running-gate doesn't conflict — when running there's no
  input focus anyway).
- cwd home-abbreviation via `os.path.expanduser`/`Path.home()` replace.

## §5 — Test plan
- Footer: with model_provider+cwd+mode → segments present in order (mode, 📂 cwd, ✱ model, ⎇ branch);
  model_provider None → no `✱` segment (branch-only degrade); home-abbrev (`/home/x/p` → `~/p`).
- User echo: a fake _input_loop / committer asserts `» <text>` committed before the turn (prompt path
  only; bash/command/empty paths do NOT echo).
- Esc binding: invoking the bound handler with `_running=True` calls `on_interrupt`; with
  `_running=False` it does not. Working hint present when running.

## §6 — Atomic commit plan (await authorization)
| # | message |
|---|---|
| 1 | `feat(tui): status footer — mode · cwd · model · branch (Sprint 6h₁₂b)` → context.py + shell.py wiring + tests |
| 2 | `feat(tui): echo user message + esc-to-interrupt hint/binding (Sprint 6h₁₂b)` → shell.py echo + chrome.py + tests |
| 3 | `docs: ADR-0111 TUI status + transcript fidelity (Sprint 6h₁₂b)` → ADR |
Trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Spec stays local.
