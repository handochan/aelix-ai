# 0131. Sprint 6h₂₃ — TUI Ctrl+G external editor

Status: Accepted (6h₂₃ shipped)
Date: 2026-06-03
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Audit LOW item from the Sprint 6h₁₅ TUI pi-parity audit memo: "Ctrl+G
external-editor ($EDITOR subprocess)". Pi binds Ctrl+G in `interactive-mode`
to open the current input buffer in `$EDITOR` (vim/nano/emacs/VSCode CLI/…)
for long prompts that exceed the inline editor. Aelix had never wired this
keybinding.

Pure TUI consumer sprint: no protected-core touch. The runtime side is
unaffected — the editor round-trip happens entirely inside the TUI host.

## Decision (3 non-protected files; +14 test cases)

### `chrome.py`

Added `on_external_editor: Callable[[], None] | None` callback +
`@kb.add("c-g")` keybinding, mirroring the established `on_image_paste`
/ `on_thinking_toggle` / `on_dequeue` pattern from earlier sprints.

### `shell.py`

1. Module-level imports — `os`, `subprocess`, `tempfile`, and
   `from prompt_toolkit.application.run_in_terminal import in_terminal`
   hoisted to the file's import block (W-review MEDIUM-4: stdlib + a fixed
   prompt_toolkit dep don't need lazy import).

2. **`editor_open_ref: dict[str, bool] = {"open": False}`** — the gate flag
   that lives for the duration of `run_tui`. The closure mutates it; the
   input loop reads it via `command_ctx.is_editor_open`.

3. **`external_editor_tasks: set[asyncio.Task[None]]`** — strong-ref set
   keeps the fire-and-forget task alive (mirror of `context_usage_tasks` /
   `queue_tasks`).

4. **`_run_external_editor(initial)`** — the async closure:
   - Editor precedence: `$VISUAL or $EDITOR or "vi"` (POSIX convention —
     `$VISUAL` is the full-screen editor, the preferred binding when a TTY
     escape is involved; W-review MEDIUM-5).
   - `tempfile.mkstemp(prefix="aelix-edit-", suffix=".md")` — `.md` is an
     Aelix choice (long prompts are often markdown; not pi-derived);
     `delete=False` so cleanup is owned by the `finally`.
   - Writes `initial` to the temp file.
   - `async with in_terminal():` — suspends prompt-toolkit's TTY ownership
     so `$EDITOR` can paint full-screen.
   - **`await asyncio.to_thread(subprocess.run, [editor, path], check=False)`**
     — the blocking subprocess runs on a worker thread so the asyncio loop
     keeps draining (auto-retry tickers, signal handlers, backend events)
     for the minutes the user spends editing (W-review MEDIUM-1; pi gets a
     free pass because Node spawns on a different stack — CPython does not).
   - Reads back, strips exactly one trailing newline (preserves intentional
     trailing blank lines while removing the editor's auto-newline).
   - `out_chrome.set_editor_text(new_text)` — intentional overwrite of any
     concurrent input (the gate prevents new turns; pi behavior).
   - `finally`: `os.unlink(path)`, `editor_open_ref["open"] = False`.

5. **`_open_external_editor()`** — sync callback fired by the chrome
   keybinding:
   - Fire-time guard: refuses if `out_chrome.running` (a turn is in flight
     — the editor would compete for the TTY with the live model output) OR
     `editor_open_ref["open"]` (back-to-back Ctrl+G).
   - Snapshots `initial = out_chrome.get_editor_text()`.
   - Schedules `_run_external_editor` via `loop.create_task`.

6. **`is_editor_open` wired onto `command_ctx`** — a lambda over
   `editor_open_ref["open"]`. The input loop checks this each iteration.

7. **`_input_loop` gate** (W-review HIGH-1) — at the top of every loop
   iteration after `chrome.get_input()` returns, if `is_editor_open()` is
   True, the line is silently dropped. This prevents a buffered/pasted
   Enter, /quit, paste-with-newline, etc. that lands on the parent TTY while
   the editor owned it from driving a turn or escaping the session.

8. **Shutdown cleanup** (W-review MEDIUM-2) — the `finally` block cancels
   any in-flight `external_editor_tasks` so they don't leak as
   "Task exception was never retrieved" warnings on /quit.

### `commands.py`

Added `is_editor_open: Callable[[], bool] | None = None` to
`CommandContext`, plus a `/hotkeys` row for Ctrl+G ("Open the current input
in $EDITOR (vim/nano/…) for long prompts").

## Deferred (intentional)

- **Killing the editor subprocess on shutdown** — cancellation of the
  asyncio task waits for the editor to exit (the subprocess runs in
  `to_thread`); we don't `Popen.terminate()` it. The cleaner shutdown is
  acceptable since the user is mid-edit anyway.
- **`set_editor_text` conflict detection** — if a user types into the
  buffer mid-edit, the editor's result overwrites it. The gate makes this
  unobservable for an Enter (no turn fires), but raw text typed into chrome
  while in_terminal is suspended could still get buffered. Pi behavior
  parity (overwrite-on-exit); a future enhancement could compare snapshots
  and merge, but it's not worth the complexity.

## Consequences

- **Files touched**: 3 non-protected (chrome.py, shell.py, commands.py);
  0 protected.
- **`git diff --stat docs/contracts packages/aelix-agent-core`**: empty ✓.
- **Tests**: 4 new test cases:
  - `tests/tui/test_chrome.py::test_ctrl_g_fires_external_editor` —
    sends `\x07` via pipe, asserts callback fires.
  - `tests/tui/test_run_tui_smoke.py::test_run_tui_ctrl_g_external_editor_round_trips_through_subprocess`
    — monkeypatches `subprocess.run` (rewrites the temp file in place to
    simulate `:wq`) + `in_terminal` (no-op ctxmgr for headless tests).
    Asserts `chrome.get_editor_text() == "edited prompt body"` AND the
    temp file is cleaned up.
  - `tests/tui/test_run_tui_smoke.py::test_run_tui_ctrl_g_input_loop_gates_during_editor`
    — slow subprocess mock (polls a release Event); sends pasted Enter +
    `/quit\n` while editor is "open"; asserts no turn fires and run_tui
    doesn't exit (W-review HIGH-1 coverage).
  - `tests/tui/test_run_tui_smoke.py::test_run_tui_ctrl_g_blocked_while_running`
    — sets `chrome.set_running(True)` then sends Ctrl+G; asserts
    `subprocess.run` is NOT called.
- **Gate**: ruff clean; pyright 0-new on touched files; pytest 3095 →
  3100 (+5; +4 new + 1 incidental).

## Code review (separate lane) — REQUEST CHANGES → all blockers + nits applied

`code-reviewer`: 0 CRITICAL / 1 HIGH / 4 MEDIUM / 2 LOW. Findings:

- **[HIGH-1]** Race: buffered/pasted Enter could escape the editor and
  drive a turn while the closure was still applying `set_editor_text`.
  FIXED: input loop now checks `command_ctx.is_editor_open` at the top of
  every iteration and silently drops the line. New smoke test exercises
  the path.
- **[MEDIUM-1]** `subprocess.run` was blocking the entire event loop for
  the minutes the user spent editing — signal handlers, auto-retry
  tickers, backend disconnects all stalled. FIXED: wrapped in
  `await asyncio.to_thread(subprocess.run, ...)`.
- **[MEDIUM-2]** `external_editor_tasks` not cancelled in `finally`. FIXED:
  added cancellation loop alongside the existing pump_task / chrome_task
  / countdown_task cleanup.
- **[MEDIUM-3]** Inline imports inside the closure (`import os`, etc.) +
  `import os as _os` in tests for one path-existence check. FIXED: stdlib
  imports hoisted to module-level in shell.py.
- **[MEDIUM-4]** Editor precedence — Aelix had `$EDITOR or $VISUAL or vi`,
  the inverse of the POSIX convention. FIXED: now
  `$VISUAL or $EDITOR or vi`. Also added a comment marking `.md` as an
  Aelix choice (not pi-derived per the audit memo).
- **[LOW-1]** Concurrent-typing overwrite. ADDRESSED: a comment in the
  closure documents the intentional overwrite; the input-loop gate makes
  it benign for Enter (most common).
- **[LOW-2]** Test brittleness — `importlib.import_module("prompt_toolkit\
  .application.run_in_terminal")` dance to monkeypatch `in_terminal`.
  FIXED: now `in_terminal` is module-level in shell.py, so the test
  monkeypatches `aelix_coding_agent.tui.shell.in_terminal` directly.

## Verification

- Unit tests: 4 new tests cover binding fire, round-trip, input-loop gate
  (HIGH-1 race), and running-guard.
- Pi-port fidelity: the audit memo doesn't cite the pi source line for
  Ctrl+G specifically, so the closure documents that it mirrors pi's
  "open the current input in $EDITOR" semantics. POSIX `$VISUAL or
  $EDITOR or vi` precedence; `.md` extension marked as Aelix-additive.
- Code review (separate lane): REQUEST CHANGES → 1 HIGH + 4 MEDIUM + 2 LOW
  all applied.
- Live verification: deferred. The asyncio.to_thread + in_terminal +
  subprocess.run path is deterministic per the synthetic-subprocess smoke
  tests; a real $EDITOR session is verified by manual driver, not by the
  test suite.
