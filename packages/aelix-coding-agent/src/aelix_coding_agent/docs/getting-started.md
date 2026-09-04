# Getting Started

Status: Accepted

This guide gets you from zero to a running `aelix` agent. For provider keys and
model selection see [providers-and-models.md](providers-and-models.md); for
custom models see [models-json.md](models-json.md); for writing your own tools
see [extension-authoring.md](extension-authoring.md).

## Install

Aelix installs as a single global `aelix` command. During the beta it comes from
GitHub Releases via the checksum-verified installer:

```bash
curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
```

That script bootstraps [uv](https://docs.astral.sh/uv/) if you do not have it,
downloads the release wheels, verifies each one against the release's
`SHA256SUMS` manifest (any mismatch aborts), and installs `aelix` pinned to the
exact version the manifest named. Third-party dependencies resolve from PyPI as
usual.

Two environment variables configure it:

- `AELIX_VERSION` — pin an exact release tag, e.g. `v0.1.0-beta.1`. Recommended
  during the beta. Without it the installer resolves the newest release from the
  GitHub API.
- `AELIX_EXTRAS` — which extras to install. Default `tui`.

```bash
AELIX_VERSION=v0.1.0-beta.1 AELIX_EXTRAS=tui \
  sh -c "$(curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh)"
```

Extras:

- `tui` — the interactive terminal UI (prompt-toolkit + Rich). Needed for the
  default interactive mode; the bare install (`AELIX_EXTRAS=`) still supports
  `--print`, `--mode json`, and `--mode rpc`.

`tui` is the only extra.

> **PyPI carries a placeholder until GA — use the installer above.** Do not
> reach for `pip install aelix`, `pipx install aelix` or
> `uv tool install aelix` yet. They do not fail, which is exactly the problem:
> the distribution names are reserved by a deliberate 1.3 kB metadata-only
> `0.0.0a0` release, so each command prints a success message and installs
> nothing runnable — check afterwards and there is no `aelix` on your `PATH`.
> For the whole beta, real Aelix ships only through GitHub Releases and the
> checksum-verified installer above. The placeholder is a pre-release, so it can
> never outrank a real one: when the first GA release is published,
> `uv tool install 'aelix[tui]'` — or the `pipx` / `pip` equivalent — resolves it
> and works as usual, with no change needed here.

### Upgrading and uninstalling

Re-run the same `curl … | sh` line to upgrade: it resolves the newest release and
re-runs the whole checksum-verified install, and `uv tool install --force` makes
that idempotent.

```bash
uv tool uninstall aelix          # remove it again
```

If you are behind a shared IP (CI, corporate NAT), the anonymous GitHub API limit
of 60 requests/hour can make the release lookup fail. Set `GITHUB_TOKEN`, or pin
`AELIX_VERSION` — a pinned tag skips that API call entirely.

### Windows (experimental)

Windows is **not a supported platform** — Linux and macOS are. The test suite
passes on `windows-latest` and that leg gates CI as of 2026-09-04, but that is
a claim about the suite, not about the agent: nothing beyond the suite is
verified there (see the README's Platform support section for what is not).
The repository root carries an `install.ps1` that mirrors `install.sh` step for
step (same release download, same SHA256SUMS gate, same `uv` install), and it
has never been executed by CI ([#106](https://github.com/handochan/aelix-ai/issues/106)):

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"
```

Known gaps are tracked in `SLICE-STATUS.md` at the repository root. If you want
Aelix on Windows today, WSL2 is the path that actually works: install there
exactly as you would on Linux.

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
aelix --offline                  # skip the rg/fd download + catalog fetch (= PI_OFFLINE=1)
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
