---
name: aelix-live-check
description: Use when an aelix-ai change touches the TUI (rendering, colour, layout, keys, dialogs) or a runtime path (a provider, streaming, sessions, tool execution, delegation, signals), and the change has to be seen working for real rather than only in unit tests. Covers capturing the real TUI in a pty at 120x40 with this skill's scripts/tui_capture.py, a real-model -p run, the scratch HOME, agent dir and cwd every live run needs, loading one credential without printing it, positive controls, pinning one upstream, and what to paste into the PR.
---

# aelix-live-check

## When to use

Unit tests do not cover two kinds of change here, and both have broken often:

- **TUI changes, every time.** Rendering, colour, layout, wrapping and key handling
  are not covered by the unit tests (CONTRIBUTING, "Some paths cannot be
  unit-tested").
- **Runtime paths**: a provider, streaming, a session, tool execution, delegation,
  signals. Run them against a real model, not a mock.

A live run can also do damage, which is why it is always isolated. A yolo child
once wrote an extension file into the checkout. A real OAuth call once rewrote the
developer's `auth.json`.

## Procedure

1. **Make a scratch directory, and keep every live run inside it.**
   ```bash
   S=$(mktemp -d /tmp/live-XXXXXX)
   mkdir -p "$S/home" "$S/agent" "$S/cwd"
   export HOME="$S/home" USERPROFILE="$S/home" AELIX_CODING_AGENT_DIR="$S/agent"
   ```
   Run aelix with `cwd` = `$S/cwd`. Never give a `yolo` or `auto` run a repository
   checkout as its cwd. If the point is to see what aelix does in the checkout itself,
   keep the scratch HOME and agent dir, use the default permission mode, and send no
   prompt that uses tools. A run from a scratch cwd is not affected by this
   repository's own skills. In the checkout, `.agents/skills` makes aelix ask the
   project trust question first (ADR-0303), and with a fresh agent dir it asks every
   time: answer it with a `key:` step, or pass `--approve` for a `-p` run, which
   otherwise prints a Notice line and runs without the project skills.
2. **Load one credential, by name, without printing it.** Read only the variable
   the run needs. `set -a; . .env` exports every secret in the file to every
   child. In a git worktree, `.env` lives in the main checkout:
   ```bash
   ENVFILE="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.env"
   export OPENROUTER_API_KEY="$(sed -n 's/^OPENROUTER_API_KEY=//p' "$ENVFILE" | tr -d "\"'")"
   ```
   Do not use an OAuth provider for a live run unless the change is about OAuth. If
   you must, `touch "$S/stamp"` first, then run
   `find ~/.aelix/agent -newer "$S/stamp"` afterwards and say what changed.
3. **TUI: capture the real screen.** `scripts/tui_capture.py` in this skill runs
   `python -m aelix_coding_agent` in a fresh pty (`os.forkpty`) at 120x40,
   `TERM=xterm-256color`. It builds the child's environment itself (scratch HOME and
   agent dir, plus only the variables you name with `--key`) and answers the
   terminal's cursor-position request as a real terminal does:
   ```bash
   uv run --no-sync python .agents/skills/aelix-live-check/scripts/tui_capture.py \
     --scratch "$S/tui" --key OPENROUTER_API_KEY --dotenv "$ENVFILE" \
     --step "until:Type your message" --step "type:hello" --step key:enter \
     --step "until:done" --step "type:/quit" --step key:enter \
     --expect "done" -- --offline --provider openrouter --model <model-id>
   ```
   It saves `raw.bin` (every byte) and `screen.txt` (the final screen) and prints
   the non-empty rows. With no credentials, the first screen is a provider wizard:
   send `key:escape` first. Then read the screen yourself. The script can count;
   whether a screen looks right is a judgement it cannot make.
4. **Runtime path: a real-model `-p` run.**
   ```bash
   REPO="$(git rev-parse --show-toplevel)"     # run this before the cd
   cd "$S/cwd" && uv run --no-sync --project "$REPO" aelix \
     --offline --provider openrouter --model <model-id> --thinking off -p "<prompt>"
   ```
   Pick a small, cheap model from `aelix --list-models <pattern>`. Always pass
   `--thinking`, so two runs you compare differ only in what you mean them to.
   Use `--mode json` when you need the event stream as evidence. `--no-sync` keeps
   `uv` from resyncing a `.venv` that another suite may be using. Drop it if the
   environment is not synced yet.
5. **Check the scratch for secrets before you copy anything out of it.**
   ```bash
   grep -rlF -e "$OPENROUTER_API_KEY" -e "$(printf '%.8s' "$OPENROUTER_API_KEY")" "$S"   # must print nothing
   ```
6. **Paste into the PR** (the "How it was verified" section): the exact command,
   the model id, the screen rows or output lines that show the behaviour, the
   positive control's count, and what you did not check. For a TUI change, also say
   what you saw: colour, dim text, wrapping, blank rows, anything that jumped.

## Decision rules

- **A zero count is a failure, never a quiet pass.** Every detector needs a positive
  control: a run, or a pattern, that shows it fires when the thing happens. Use
  `--expect` and `--count` in `tui_capture.py`. `scripts/diag_tui_live.py` exits 1
  when its pattern matched nothing. `scripts/diag_retry_abort_live.py
  --no-interrupt` must show the precondition is really built.
- **A width check on pyte rows proves nothing.** pyte rows are always exactly COLS
  cells wide, and an overflow wraps instead. Measure width in-process with
  `rich.cells.cell_len` at several widths. In the pty, look for a full last cell
  followed by a column-0 continuation row.
- **A tool argument goes missing, or behaviour differs between runs on OpenRouter:**
  pin one upstream and vary one thing at a time (content shape, then schema key
  order). The upstream that served a request is in each chunk's `provider` field.
  See `references/provider-measurement.md`.
- **You need to prove where a request goes:** route https through a local recorder
  that logs the CONNECT host and answers 403. Then even a real key never leaves the
  machine. See `references/provider-measurement.md`.
- **The behaviour depends on a non-UTF-8 file name:** macOS APFS refuses such names
  (`Errno 92`). Run that part in a Linux container.
- **You drive delegation or the extension exec surface:** read
  `references/delegation-drivers.md` before you write a driver.
- **Several flags in one shell variable:** zsh passes `$FLAGS` as one argument. Put
  multi-flag invocations in a `#!/bin/bash` script or a Python driver.

## What bit this repo

- #220, 2026-09-05: a yolo child pointed at the checkout wrote
  `.aelix/extensions/spawn_sleeper.py` into it.
- #344, 2026-09-30: a lane's live `openai-codex` call refreshed OAuth and rewrote the
  developer's `~/.aelix/agent/auth.json`, although its brief said never to write
  there.
- #170: a live-window detector matched `\x1b[2;3m` while Rich emitted
  `\x1b[0;2;3m`. Real runs were written off as "proving nothing", and a wrong
  conclusion reached an ADR. This is why `diag_tui_live.py` fails on a zero count.
- #147: a retry-abort rig passed against the broken build, because the SDK's own
  retries absorbed the 5xx it fired and aelix's retry never ran. The
  `--no-interrupt` control exists for this.
- 2026-10-30, after #132: "Ionstream drops a `task` that holds newlines" reached the
  maintainer from one uncontrolled comparison. Pinned to one upstream, it dropped
  `task` 38 of 38 times whatever the content, because it emits keys only in the
  schema's declared order.
- #25 verify, 2026-11-03: "nothing wider than the terminal" rested on a pyte row
  width check, which cannot fail.
- 2026-09-22: the claim that subagents cannot check the TUI because they have no TTY
  was wrong. `os.forkpty` gives the child its own pty.

## Pointers

- `scripts/tui_capture.py` (this skill): the capture driver; `--help` lists the steps.
- `references/provider-measurement.md`: wire taps, upstream pinning, routing proofs.
- `references/delegation-drivers.md`: live drivers for delegated children and the
  extension exec surface.
- `scripts/diag_tui_live.py`: a reasoning-repaint detector with a positive control.
  It runs with cwd = the checkout and your inherited environment, and it loads every
  key in `.env`. Export the scratch HOME and agent dir before you run it, and keep to
  its tool-free default prompt.
- `scripts/diag_retry_abort_live.py`: a local holding provider and a `--no-interrupt`
  positive control.
- `CONTRIBUTING.md`, "Keep tests out of your home's `~/.aelix`".
- `aelix --help`: `--offline` skips the update check and other network fetches;
  `--no-session` keeps the session in memory.
