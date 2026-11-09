# Lane rules

Every lane brief points here instead of copying a rules block. Read all of it before
you start. Where a rule and your brief disagree, the brief wins, and the lead must
say so in the brief.

## Batch facts

Your brief fills these in, in a block headed "Batch facts". If one is missing, ask
the lead before you rely on it.

- `{{base}}`: the commit your worktree was cut from.
- `{{worktree}}`, `{{branch}}`: yours. You write only there and in your scratch.
- `{{scratch}}`: your private scratch directory, for example `/tmp/<n>-<role>/`.
- `{{evidence}}`: the durable directory your key outputs are copied to.
- `{{main_checkout}}`: the main checkout, for prose files a worktree lacks
  (`AGENTS.md`, `CLAUDE.md`) and for `.env`.
- `{{others}}`: the other sessions and lanes running now, with their worktrees and
  scratch directories.
- `{{adr_numbers}}`: the ADR numbers reserved for you. Take no other.
- `{{decisions}}`: decisions you may quote as the maintainer's, verbatim and dated.
- `{{credential}}`: the one credential a live run may load, and from which file.
- `{{commit_trailer}}`: the exact trailer line every commit body ends with.

## Your return value

- **Your final message is your return value.** Ending your turn kills every
  background process you started, and nothing can wake you afterwards. Stay in the
  turn until your work is finished. Never end a turn to "wait".
- Write long evidence to files under `{{scratch}}`, and cite them by path. Keep the
  final message, or a structured return, short: plain strings, newlines escaped,
  each field under its limit.

## The repository and other sessions

- Other sessions work in this repository at the same time (`{{others}}`). Do not
  modify, check out or delete anything outside your worktree and scratch. Reading
  other trees is fine.
- In the main checkout, never run `git checkout`, `switch`, `reset` or `stash`. Never
  edit or delete another session's issue lock.
- Read prose files by their absolute path in `{{main_checkout}}`. Run gate scripts by
  their relative path inside your worktree: a script run by the main checkout's path
  gates the main checkout.
- Python: `env -u VIRTUAL_ENV uv run --frozen ...` inside your worktree. An inherited
  `VIRTUAL_ENV` may point at another checkout. Confirm once with
  `python -c "import aelix_ai; print(aelix_ai.__file__)"`: the path must be inside
  your worktree. Never run a bare `uv sync`, or a `uv run` without `--frozen`, in the
  main checkout. It resyncs a `.venv` that other sessions' suites are using.
- Take only the ADR numbers in `{{adr_numbers}}`. Prefer a dated note on an existing
  ADR when the change does not make a new decision.

## Waiting and load

- No single tool call waits longer than about 4 minutes. Give each shell call a
  timeout of 240 s or less, and poll with bounded loops that you re-issue:
  `for i in $(seq 23); do grep -q '^rc=' f && break; sleep 10; done; tail -3 f`.
  macOS has no `timeout`; use `perl -e 'alarm N; exec @ARGV' <command>`.
- Do not wait on a notification tool, and do not start something in the background
  in order to wait for its notice.
- **No full suite.** The lead runs it once. Run targeted tests and the fast gates.
- The machine runs other suites. A timing test that fails once under load is not
  evidence either way. Repeat it and record the counts.

## Processes and secrets

- Kill only PIDs you started, or processes carrying an argv marker unique to your
  run. Never kill by name pattern (`pkill -f`, `killall`).
- Delete nothing outside `{{scratch}}`. Leave nothing running at the end.
- Load only `{{credential}}`, by name, and only when a live real-model run needs it.
  Never print it. Never source a whole `.env`.
- Every live aelix run gets a scratch HOME, `AELIX_CODING_AGENT_DIR` and cwd under
  `{{scratch}}`. The one exception is a run whose point is to see what aelix does in
  the worktree itself. That run still uses a scratch HOME and agent dir, with no
  `yolo` or `auto` mode and no tool-using prompt (aelix-live-check skill).
- At the end, `grep -rl '<your credential prefix>' {{scratch}}` must print nothing.
- zsh does not split words (`set -- $s`, `${4:+...}` and `read -ra` misfire). Drive
  loops from Python.

## Commits

- Commit a checkpoint after each green step:
  `git commit -m "wip(<area>): #<n> step N - <what>"`.
- No amend, no rebase, no push, no branch deletion. The lead folds the checkpoints.
- `git add` new test files before running any gate. The citation gate and the
  HOME-sandbox test read `git ls-files`.
- If the brief asks for a final commit message, write it to the file it names.
  - The subject is one long sentence in the style of `git log -5 --format=%B
    origin/main`, saying what a user now sees, and it ends in `(#<n>)`, or `(#a, #b)`
    for an entangled group.
  - No body line may start with `#`. A later rebase strips such lines.
  - The body ends with exactly `{{commit_trailer}}`.

## Gates (before your final checkpoint)

- Run the aelix-preflight skill: `scripts/preflight.py --plan`, then the run, with
  `--pythons 3.11,3.14` for new and touched tests, `--docker` when the change or a
  touched test file is platform-specific, and `--windows-clock` for timing or
  ordering tests.
- A citation `--fix` that exits non-zero, or prints "no anchor; locked as written",
  is a stop. Read each cited line yourself.
- Windows: tests use `tmp_path`, never `Path("/abs")`. Subprocess output is decoded
  with `encoding="utf-8", errors="replace"`, never a bare `text=True`. Copy a sibling
  test's win32 marks where your test has the same shape.

## Evidence and claims

- Reproduce each defect on `{{base}}` before fixing it. Each new test must fail on
  `{{base}}`, and you say how it fails.
- Run mutants on the changed product lines (aelix-tests-that-bite skill). Report
  `killed/total` and name the survivors.
- Every sentence you add to docs, an ADR, the CHANGELOG or a commit names the test,
  measurement, ADR or issue behind it, and the condition under which it holds. Do
  not write wider than you measured.
- A TUI-visible change gets a pty capture. A runtime-path change gets one real-model
  run, scratch only (aelix-live-check skill).
- Read pi's handling first. Diverging is allowed; say why.
- Quote as the maintainer's only what `{{decisions}}` lists. Everything else is the
  lead's call or yours; label it so.
- If a fix needs a maintainer decision, or a file another lane owns, stop at that
  point and report it with the evidence. Do not do it.
- No issue comments, no board edits, no PRs, no pushes, unless your brief says
  otherwise.
