# Getting Started

Status: Accepted

This guide gets you from zero to a running `aelix` agent. For provider keys and
model selection see [providers-and-models.md](providers-and-models.md); for
custom models see [models-json.md](models-json.md); for writing your own tools
see [extension-authoring.md](extension-authoring.md).

## Install

Aelix installs as a single global `aelix` command. The recommended way to get an
isolated, always-on-PATH CLI:

```bash
uv tool install 'aelix[tui]'     # recommended (uv) — CLI + interactive TUI
pipx install 'aelix[tui]'        # or pipx
```

Or into an environment with pip:

```bash
pip install 'aelix[tui]'         # CLI + interactive TUI
pip install aelix                # CLI + non-interactive (print / json / rpc) only
pip install 'aelix[images]'      # also enable inline image rendering
```

Extras:

- `tui` — the interactive terminal UI (prompt-toolkit + Rich). Needed for the
  default interactive mode; the bare `aelix` install still supports `--print`,
  `--mode json`, and `--mode rpc`.
- `images` — inline image rendering in the terminal.

## Set a provider key

`aelix` needs a provider API key. The simplest path is an environment variable:

```bash
export OPENAI_API_KEY=sk-...        # or ANTHROPIC_API_KEY / OPENROUTER_API_KEY / ...
```

See [providers-and-models.md](providers-and-models.md) for the full list of
providers and their environment variables, plus the `--api-key` flag and the
`models.json` `apiKey` field as alternatives.

## Run

```bash
aelix                                            # interactive agent (TUI)
aelix --model openai/gpt-4o-mini "summarise this repo"
aelix --print "what files changed?"              # one-shot, prints to stdout
aelix --help                                     # full flag reference
```

Model ids use the `<provider>/<model>` form (e.g. `openai/gpt-4o-mini`,
`anthropic/claude-sonnet-4-6`). List what is available with:

```bash
aelix --list-models            # all models
aelix --list-models gpt        # fuzzy-filter by pattern
```

## Modes

`aelix` runs interactively by default. The other modes are for scripting and
embedding:

| Invocation            | Mode        | Output                                   |
| --------------------- | ----------- | ---------------------------------------- |
| `aelix`               | interactive | the TUI (requires the `tui` extra)       |
| `aelix --print` / `-p`| one-shot    | the assistant response on stdout         |
| `aelix --mode json`   | headless    | line-delimited JSON event stream         |
| `aelix --mode rpc`    | headless    | JSONL command/response protocol on stdio |

`--mode text` is the default output format, not a mode selector: on a TTY
`aelix` stays interactive unless you pass `--print` (or pipe input on stdin),
which is what selects one-shot output. `--print` also opportunistically eats the
next token as the message, so `aelix -p "hello"` and `aelix --print hello` are
equivalent.

## Common flags

```bash
aelix --continue                 # continue the most recent session (-c)
aelix --resume                   # pick a previous session interactively (-r)
aelix --resume <id>              # resume a specific session by id/prefix
aelix --no-session               # in-memory session, not persisted
aelix --thinking medium          # off | minimal | low | medium | high | xhigh
aelix --append-system-prompt "Be terse."
aelix --no-context-files         # skip auto-discovered AGENTS.md context (-nc)
aelix --export session.jsonl out.html   # render a saved session file to HTML
aelix --offline                  # disable startup network operations (= PI_OFFLINE=1)
aelix @path/to/file.py "explain this"   # inline a file into the first message
```

Run `aelix --help` for the complete, authoritative list.

## Inside the interactive TUI

The TUI accepts slash commands (type `/` to see completion). Highlights:

- `/model` — switch the active model.
- `/clear` — clear the transcript.
- `/compact` — summarise and compact the context.
- `/cost` — show token usage and cost so far.
- `/tools` — list active tools.
- `/resume` — switch to another session.

Press `Esc` to interrupt a running turn (this cancels in-flight tools, including
`bash`, `grep`, `find`, `read`, `write`, `edit`, and `ls`). Press `Ctrl+G` to
edit the current input in `$VISUAL` / `$EDITOR`.

## Develop against the repo

Aelix uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management.

```bash
uv sync                  # create .venv and install all workspace packages (dev included)
uv run pytest            # run the test suite
uv run aelix --help      # the real CLI from a source checkout
```

`python -m aelix` runs a credential-free demo with a mock stream function — it
is **not** the real CLI. Use `aelix` (installed) or `uv run aelix` (from a
checkout).
