# 0122. Sprint 6h₁₄b — /resume session picker (in-process hot-swap + transcript replay)

Status: Accepted (6h₁₄b shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

`/resume` was the last item of the "A. P1 TUI" bundle and the only one needing a
new class of work: an in-process session **hot-swap** plus **transcript replay**.
The `--resume` startup picker was explicitly deferred in Sprint 6h₈ (entry.py,
ADR-0088); this closes the deferral for the in-session command (the startup flag
can reuse the same flow later).

Pi reference (`interactive-mode.ts` at 734e08e): `/resume` is an in-session slash
command (and a startup flag) that overlays a `SessionSelectorComponent`; on
select it calls `runtimeHost.switchSession(path)` — an **in-process** swap (tear
down the current `AgentSession`, open the target `.jsonl`, build a fresh runtime,
hot-swap, fire `finishSessionReplacement`) — then `renderCurrentSessionState()`
repaints the transcript. No process restart.

Aelix already had the mechanism (architect-verified): `JsonlSessionRepo.list()` /
`.open()`; `AgentSessionRuntime.switch_session(path)` (parity body at
`agent_session_runtime.py:514`) which calls `_rebind_session(new_harness)` at
`:480`; and `run_tui`'s `_rebind` (registered via `set_rebind_session`) already
re-subscribes the `EventRenderer` to the new harness. The gaps were purely in the
TUI: no `/resume` command, no picker, no transcript replay, and the command
context held a stale harness after a swap.

## Decisions (all in non-protected `aelix-coding-agent`)

- **`/resume` command** (`tui/commands.py`): registered built-in; the handler
  delegates to a host-wired `CommandContext.resume_session` coroutine and degrades
  with a committed message when unavailable / on failure (never crashes the REPL).
- **The flow** (`shell.py::_resume_session`): `repo.list(JsonlSessionListOptions(
  cwd=cwd))` (newest-first), exclude the active session
  (`runtime_host.session.session_file`), present a picker via `context.select`
  with `{created} · {short-id}` labels (a label→metadata map handles selection),
  then `runtime_host.switch_session(path)`. On success: `chrome.clear()` +
  `renderer.replay(messages)` + a `↻ Resumed session (N messages)` line.
- **Transcript replay** (`render.py::EventRenderer.replay`): pi
  `renderCurrentSessionState` parity. **Reads the PERSISTED branch**
  (`build_session_context(await session.get_branch()).messages`) — NOT the
  in-memory `harness._state.messages`, which is empty right after a swap (rebuilt
  lazily on the next turn). Reuses the live helpers (`_tool_header`,
  `_render_tool_end`) so a resumed transcript looks identical to a streamed one,
  and truncated tool cards are stored so `/expand` works on them too. Renders
  user (`» text`), assistant (thinking dim-italic + text + `⚙` tool-call headers
  + terminal-error line), and toolResult (the result card).
- **Stale-harness fix** (`shell.py::_rebind`): `command_ctx.harness = new_harness`
  on every rebind, so `/model`, `/compact`, `/cost`, … act on the resumed session
  rather than the swapped-out one. This covers `/resume` and any future
  new/fork swap through the single rebind seam.

## Consequences

- **Live-verified (PTY, gpt-4o-mini), end-to-end:** created a session whose turn
  replied `ZEBRA`, quit, relaunched, `/resume` → picker listed sessions with
  timestamps + short ids (active session excluded) → selecting it cleared the
  screen and **replayed** `» reply…ZEBRA` / `ZEBRA` / `↻ Resumed session (2
  messages)`; a follow-up "what word did you say?" answered `ZEBRA`, proving the
  resumed session is **functionally live** (the harness rebuilds working context
  from the branch on the next turn), not just a visual repaint.
- ruff clean; pyright 0 errors on the changed source (8-baseline); full pytest
  **3000 passed, 1 skipped** (+ tests: render replay user/assistant/tool +
  terminal-error + empty; commands /resume unavailable/invoked/failure; smoke
  /resume degrades-without-repo + REPL survives). Protected core byte-unchanged.

## Code review (separate lane) — APPROVE-WITH-NITS → fixes applied

`code-reviewer`: 0 CRITICAL / 0 HIGH; all six focus areas confirmed correct
(`command_ctx.harness` rebind mutation sufficient + no other stale refs; replay
reads the persisted branch correctly; `_render_tool_end` reuse safe — `.details`
absent → `None`; picker exclusion/cancel/empty sound; `switch_session` raises
before any half-swap; `clear()`→replay has no race since `/resume` runs inline in
the serialized `_input_loop`). Protected core byte-unchanged. Findings addressed:

- **[M1]** `_resume_session` orchestration was untested. FIXED: added smoke tests
  driving the real run_tui + a fake repo/session/switch_session — happy path
  (list cwd-scoped → exclude active → pick #1 → `switch_session("/s/new.jsonl")`)
  and empty-choices (no switch); plus the existing no-repo degrade.
- **[M2]** Inline `build_session_context(await session.get_branch())` duplicated
  `Session.build_context()`. FIXED → `await session.build_context()` (dropped the
  import).
- **[M3]** No explicit mid-turn guard. FIXED: `if out_chrome.running: …return`
  with a comment (the serialized `_input_loop` already prevents mid-turn dispatch,
  and a mid-turn "/resume" routes to steer, not the command — belt-and-braces).
- **[L1]** `runtime_host._repo` private access — documented with a comment
  (no public accessor on the protected-core runtime; degrades if absent).
- **[L2]** active-session-when-`session_file`-None / **[L3]** stale queued output
  pre-`clear()` / **[L4]** label-suffix cosmetics — accepted as-is (negligible /
  latent at an idle prompt).

## Deferred

- **Picker shows the 9 most-recent sessions** (the `ctx.ui.select` modal binds
  number keys 1-9). pi has a full scrollable picker with search + threaded/recent/
  relevance sorts + rename/delete + "all folders" scope. A richer custom picker
  (`ctx.ui.custom`) is a future enhancement; most-recent-9 covers the common case.
- Startup `--resume` flag wiring (reuse `_resume_session`'s list+pick); session
  rename/delete; cross-folder scope toggle.
- Session metadata has no title / message-count (pi shows first-message + count);
  the label uses `{created} · {short-id}`.
