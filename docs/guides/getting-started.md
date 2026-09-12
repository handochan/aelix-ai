# Getting Started

Status: Accepted

This guide gets you from zero to a running `aelix` agent. For provider keys and
model selection see [providers-and-models.md](providers-and-models.md); for
custom models see [models-json.md](models-json.md); for writing your own tools
see [extension-authoring.md](extension-authoring.md).

## Install

Aelix installs as a single global `aelix` command. The recommended path during
the beta is the checksum-verified installer, which takes it from GitHub
Releases:

```bash
curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
```

That script bootstraps [uv](https://docs.astral.sh/uv/) if you do not have it,
downloads the release wheels, verifies each one against the release's
`SHA256SUMS` manifest (any mismatch aborts), and installs `aelix` pinned to the
exact version the manifest named. Third-party dependencies resolve from PyPI as
usual.

If `aelix` is not on your `PATH` afterwards, the installer runs
`uv tool update-shell` for you — that appends uv's tool bin to your shell's rc
file, idempotently. It cannot fix the shell it is running in, so open a new
terminal.

Three environment variables configure it:

- `AELIX_VERSION` — pin an exact release tag, copied from the
  [Releases page](https://github.com/handochan/aelix-ai/releases). Recommended
  during the beta. Without it the installer resolves the newest release from the
  GitHub API.
- `AELIX_EXTRAS` — which extras to install. Default `tui`.
- `AELIX_PYTHON` — the interpreter uv builds the tool environment on, as a uv
  Python request. Default `>=3.11,<3.14` — the range the pinned OpenAI SDK
  survives. It is **not** "the range CI runs": CI runs 3.11 and 3.12, and 3.13
  is in the range because the suite passes on it, not because anything gates it
  (#192 adds 3.13 to the matrix).
  `uv tool install` reads neither `.python-version` nor `uv.lock`; left to
  itself it takes the newest interpreter on the machine, and on Python 3.14
  `openai<2.0` raises `'typing.Union' object has no attribute
  '__discriminator__'` in the middle of a turn (#262, #263). Override it if you
  need a specific one, e.g. `AELIX_PYTHON=3.12`.

  Unlike `AELIX_EXTRAS`, **an empty value is not "no constraint"** — it falls
  back to the default. `uv tool install --python ""` does not fail; uv ignores
  the empty request and goes back to the newest interpreter, so an empty
  `AELIX_PYTHON` would silently undo the thing the knob is for. To genuinely
  widen the range, say what you mean: `AELIX_PYTHON='>=3.11'`.

  **The knob only covers this script.** `uv tool install aelix@latest` — which
  `uv tool upgrade aelix` suggests by itself — rebuilds the environment on the
  newest Python on the machine and today lands on 3.14. Measured: it overwrites
  an environment this installer had correctly built on 3.13. Upgrade by
  re-running the install line.

Pass them **through the pipe**, not in front of `curl`. A `VAR=x curl … | sh`
prefix sets the variable for `curl` only; the `sh` on the other side of the pipe
never sees it and the installer silently falls back to its default:

```bash
AELIX_VERSION=vX.Y.Z-beta.N AELIX_EXTRAS=tui \
  sh -c "$(curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh)"
```

Extras:

- `tui` — the interactive terminal UI (prompt-toolkit + Rich). Needed for the
  default interactive mode; the bare install (`AELIX_EXTRAS=`) still supports
  `--print`, `--mode json`, and `--mode rpc`.

`tui` is the only extra.

> **`pip install aelix` is a trap below `0.1.0b2`.** Until that version the four
> distribution names hold exactly one file each: a metadata-only `0.0.0a0`
> release published to reserve them (checked against pypi.org on 2026-09-09 —
> `aelix` lists `0.0.0a0` and nothing else). pip and uv both take the newest
> candidate when *every* candidate is a pre-release, so `pip install aelix`,
> `pipx install aelix` and `uv tool install aelix` all resolve that placeholder.
> They do not fail, which is the problem: each prints a success message and
> installs nothing runnable. `uv tool install` is worse than a no-op — finding no
> entry points in the placeholder it removes the working `aelix` you were trying
> to upgrade.
>
> **From `0.1.0b2` on those commands resolve the real thing.** ADR-0240 removed
> the workflow gate that kept pre-release tags off PyPI, so `v0.1.0-beta.2`
> publishes `0.1.0b2` to all four names, and the same "newest pre-release wins"
> rule that used to pick the placeholder now picks the real release. The
> installer above stays the recommended path either way: it is the only one that
> checks the wheels against the release's `SHA256SUMS`.

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

### Windows

The repository root carries an `install.ps1` that mirrors `install.sh` step for
step — same release download, same SHA256SUMS gate, same version-pinned `uv`
install ([#106](https://github.com/handochan/aelix-ai/issues/106)):

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"
```

That line takes the newest release, and every Windows fix on this page landed in
`v0.1.0-beta.2` — on anything older it installs a build without them.

It takes the same knobs as `install.sh`, read from the environment:
`AELIX_VERSION`, `AELIX_EXTRAS`, `AELIX_REPO`, `AELIX_PYTHON`, `UV_VERSION`,
`GITHUB_TOKEN`.
Set them with `$env:` **before** the pipe — `iex` runs the script in the current
session, so it sees them (measured on PowerShell 7.6.5):

```powershell
$env:AELIX_VERSION = 'vX.Y.Z-beta.N'
irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex
```

One knob differs from POSIX: `AELIX_EXTRAS=` cannot select the bare CLI here.
Assigning `''` to a Windows environment variable deletes it, so an empty value is
indistinguishable from unset and falls back to `tui`. For the bare CLI, run
`uv tool install --force --find-links <dir> aelix` yourself.

**What is measured, and by whom.** CI runs the full test suite on
`windows-latest` under Python 3.11 and 3.12, and that leg gates every branch;
it also runs `install.ps1` end to end there, under both pwsh 7 and Windows
PowerShell 5.1 (the `install.ps1 e2e (pwsh)` / `install.ps1 e2e (powershell)`
jobs in `.github/workflows/ci.yml`). CI drives the **checked-out** script
through the same `| iex` path; the fetch from raw.githubusercontent.com and the
`-ExecutionPolicy Bypass` flag above are not part of that measurement. What that
job asserts is the launcher and `aelix --version`, and it says so in its own
header (`.github/scripts/assert-install-ps1.ps1`): no release is gated on a
Windows runtime check.

On a real Windows host the maintainer checked this release by hand on
2026-09-09: a `bash` tool call that prints Korean renders as Korean rather than
mojibake, the model correctly reports it is on PowerShell and writes PowerShell
syntax instead of `&&`, and the TUI paints (footer, `/thinking` picker, tool
cards). That is **one person, one machine, one locale**, on top of the CI leg —
which is what "beta" means here, not a claim of broad coverage. The README's
*Platform support* section is the canonical statement; `SLICE-STATUS.md` at the
repository root tracks the remaining Windows gaps.

Three of this release's Windows fixes have no human witness at all — CI and
source only. Nobody has watched a `pwsh.exe` planted in the working directory
lose to `PATH` ([#241](https://github.com/handochan/aelix-ai/issues/241)), the
`!command` credential cache do its caching
([#240](https://github.com/handochan/aelix-ai/issues/240)), or a shell that will
not load come back as exit 127
([#243](https://github.com/handochan/aelix-ai/issues/243)).

Two things are known to still bite. A program that opens the Windows console
**directly** — a git credential prompt, `Read-Host -AsSecureString`,
`Get-Credential` — can still put a prompt there that nothing answers, and burn a
command's whole timeout; nothing in this release changed that. And the upgrade
path itself is unexercised: the previous beta had no Windows story, so
"installed, notified, upgraded" cannot be tried until a second release exists.
WSL2 remains a fine alternative — install there exactly as you would on Linux.

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
                                 # (the level is remembered per session: --continue
                                 #  and --resume come back at the level you left)
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
