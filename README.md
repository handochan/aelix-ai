<p align="center">
  <img src="https://raw.githubusercontent.com/handochan/aelix-ai/main/docs/assets/brand/lockup-stacked.png" width="360" alt="Aelix — the A×X mark above the Aelix wordmark">
</p>

<p align="center">
  <strong>Your own agent world — built on the Python ecosystem.</strong>
</p>

<p align="center">
  <a href="README.ko.md">한국어 README →</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
</p>

Aelix is a small core. The plugins and extensions are the ecosystem, and an extension is
just a Python function — so the stack you already work in becomes the agent's toolbox.
Self-hosted, auditable, and running on the model budgets you already pay for.

<p align="center">
  <img src="docs/assets/demo.gif" width="100%" alt="Aelix demo — the agent writes a DuckDB query tool into my_ext.py, /reload hot-loads it without restarting, and the next prompt runs it in-process against a Parquet file">
</p>
<p align="center"><em>The agent extends itself: it authors a <code>duckdb_query</code> tool into <code>my_ext.py</code>, <code>/reload</code> hot-loads it without restarting, and the very next prompt runs it in-process. Idle waits are cut and the recording plays at 2×.</em></p>

---

## What Aelix is

An agent runtime and extension platform in pure Python. It ships today as a terminal coding
agent — its first workload, not its boundary. Read every line it runs, keep it entirely
inside your own perimeter, and extend it with plain Python functions that import your
existing stack — DuckDB, an internal SDK, a warehouse client — directly, in-process.

It sends nothing about you anywhere; there is no telemetry. It does make a few requests for
itself, and they are the whole list: a once-a-day release check (interactive sessions only —
headless runs never check, and `/settings` → **Check for updates** turns it off from the
next launch), the first-use `ripgrep`/`fd` download, and the extension-catalog fetch.
`--offline` turns those off.

## Install

Aelix installs from GitHub Releases through a checksum-verified installer. It
bootstraps [uv](https://docs.astral.sh/uv/) if needed, verifies every Aelix wheel
against the release's `SHA256SUMS` manifest, and installs the global `aelix`
command pinned to the version that manifest named.

```bash
# macOS, Linux
curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
```

```powershell
# Windows — EXPERIMENTAL, and needs v0.1.0-beta.2 or newer (PowerShell 5.1+)
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"
```

`v0.1.0-beta.2` is the release every Windows fix landed in, and the installer
takes the newest release — so on an older one that line installs a build where
none of this works. Scope and evidence: [Platform support](#platform-support).

Both read the same environment variables: `AELIX_VERSION` pins a release tag
(`vX.Y.Z-beta.N`), `AELIX_EXTRAS` picks extras (default `tui`; empty installs the
headless CLI, POSIX only), `AELIX_PYTHON` requests the interpreter (default
`>=3.11,<3.14`, the range the pinned OpenAI SDK survives — left alone,
`uv tool install` takes the newest interpreter on the machine, and on 3.14 the
OpenAI Responses path dies mid-turn), `GITHUB_TOKEN` lifts the anonymous GitHub API rate limit. Each has to reach the shell on the *far* side of the pipe, which is what
makes the obvious spelling wrong on both platforms — `VAR=x curl … | sh` sets it
for `curl` alone, and from a PowerShell prompt `powershell -c "$env:VAR=…"` is
expanded by the outer shell before the child sees it (measured on pwsh 7.6). The
[getting-started guide](docs/guides/getting-started.md#windows) gives the form
that works on each. Re-run the same line to upgrade; on a `PATH` miss both
installers now run `uv tool update-shell` for you instead of printing it.
**Upgrade through that line, not through `uv tool install aelix@latest`** — uv
suggests the latter itself, and it rebuilds the environment on the newest Python
on the machine, which today means 3.14 and a mid-turn crash (#262). Measured: it
overwrites a correct 3.13 install.
`uv tool uninstall aelix` removes it.

> **PyPI carries a placeholder until `0.1.0b2`.** Today all four names hold a
> metadata-only `0.0.0a0` reservation, so `pip install aelix` **exits 0 and
> installs nothing runnable** — no `aelix` command, and `import aelix` raises
> `ModuleNotFoundError` — while `uv tool install aelix@latest` **deletes an
> existing install**. From `0.1.0b2` on that trap is closed: every candidate on
> the index is a pre-release, and pip and uv both take the newest one in that
> case, so `uv tool install 'aelix[tui]'` and `pipx install aelix` resolve the
> real thing ([ADR-0240](docs/decisions/0240-a-pre-release-tag-publishes-to-pypi-too.md)).
> The installer above stays the recommended path — it is the only one that checks
> the release's `SHA256SUMS` — and an install it made upgrades by re-running it,
> not by `uv tool upgrade`.

## Platform support

macOS, Linux and Windows. Windows became usable in `v0.1.0-beta.2` and has the
thinnest evidence of the three: CI runs the full suite on `ubuntu-latest` and
`windows-latest` under Python 3.11 and 3.12 and runs `install.ps1` end to end
under both pwsh and Windows PowerShell 5.1, and on 2026-09-09 one person drove
this candidate on one Windows machine — a `bash` call's Korean output came back
as Korean, the model reported it was on PowerShell and used PowerShell syntax
instead of `&&`, and the TUI drew correctly on a Windows console. That is the
whole of it: one person, one machine, one locale, plus a green leg. No
long-running use, no second machine, no third locale, and three of this release's
Windows fixes are argued from source and CI alone.

A program that opens the Windows console directly — a git credential prompt,
`Read-Host -AsSecureString`, `Get-Credential` — can still prompt there and burn a
command's whole timeout. Nothing in this release touches that, and no test on the
leg reaches it.

Which fix is measured how, and what is still open:
[getting-started → Windows](docs/guides/getting-started.md#windows) and
[`SLICE-STATUS.md`](SLICE-STATUS.md); the port is tracked in
[#110](https://github.com/handochan/aelix-ai/issues/110).

## Quick start

```bash
aelix                                            # interactive agent (TUI)
aelix --print "what changed in this repo?"       # one-shot, headless
aelix --model anthropic/claude-haiku-4-5 "summarise this repo"
aelix status                                     # trust, extensions, TLS — starts no session
aelix docs                                       # the guides, bundled in the wheel
```

Aelix needs a provider credential: set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`OPENROUTER_API_KEY`, run `/login` inside the TUI (Copilot / subscription OAuth), pass
`--api-key`, or configure `~/.aelix/agent/models.json`. See the
[providers guide](docs/guides/providers-and-models.md).

On first use of `grep` or `find`, Aelix downloads `ripgrep` and `fd` into
`~/.aelix/agent/bin` so both honour `.gitignore`. These are the only binaries fetched at
runtime, `--offline` skips them, and copies already on your `PATH` are preferred.
The `@` file menu uses that same `fd` when it can find one, so it stops offering the
files git ignores — type the directory to reach them anyway (`@target/`), or turn
`/settings` → **Gitignore in @ menu** off. Without an `fd` it falls back to a directory
walk that applies no ignore rules. Either enumerator stops after 20 000 paths, and with
ignore rules off a big ignored build tree can eat that budget before your own
directories are reached.

## Why Aelix

- 🐍 **Extensions are just Python.** A tool is a plain function — no plugin language, no
  out-of-process bridge. The same `ExtensionAPI` registers tools, slash commands, providers,
  renderers, themes and your own `/login` flow, and every extension hot-reloads without a
  restart. Even policy and guardrails are swappable built-in extensions.
- 💳 **Runs on the budget you already own.** Anthropic, OpenAI, OpenRouter, Gemini/Vertex,
  Cloudflare and the GitHub Copilot seat you already sign in with. Route cheap work and hard
  reasoning to different models in one session. No metered credits, no new vendor.
- 🔍 **Auditable and self-hosted.** Fully open source, no telemetry, built for closed
  networks. `--offline` turns off the requests Aelix makes for itself. Trust lives in code you
  can read — the answer to *"why run an agent I didn't write?"*
- ⚙️ **Scriptable and headless.** `--print`, line-delimited `--mode json`, and a `--mode rpc`
  JSONL protocol make Aelix embeddable in pipelines, CI and evaluation loops.

## Extensions are just Python — query your data stack in-process

An extension is a `setup(aelix)` function. No plugin language, no bridge, so a tool imports
your existing stack and hands results straight back to the model:

```python
# my_ext.py  —  loads with:  aelix -e ./my_ext.py
from typing import Any

import duckdb                              # your own dependency, imported in-process

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_agent_core.types import AgentTool
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_ai.messages import TextContent


async def _query(args: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
    # DuckDB reads Parquet/CSV/JSON in place — no load step, no copy.
    rel = duckdb.sql(args["sql"]).limit(args.get("limit", 20))
    return ToolResult(content=[TextContent(text=str(rel))])


def setup(aelix: ExtensionAPI) -> None:
    aelix.register_tool(AgentTool(
        name="duckdb_query",
        description="Run DuckDB SQL straight against Parquet/CSV/JSON files. No load step.",
        parameters={
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT … FROM 'data/*.parquet'"},
                "limit": {"type": "integer", "description": "Max rows returned (default 20)."},
            },
            "required": ["sql"],
        },
        execute=_query,
    ))
```

**Embed it anywhere Python runs** — a notebook, an Airflow/Prefect/Dagster task, a CI job:

```bash
aelix --print "which channels in data/orders.parquet have missing churn scores?"
aelix --mode json "run the eval suite and summarise failures"   # line-delimited events
```

See [writing an extension](docs/guides/extension-authoring.md) for the full surface, and the
[Aelix Marketplace](https://handochan.github.io/aelix-marketplace/) — the catalog Aelix reads
by default. It ships empty for the beta and is open for submissions.

## Providers

Adapters are hand-written and keyed by **wire protocol**, not by vendor — no litellm, no
generic wrapper layer — so provider-specific behaviour (Anthropic thinking-block replay,
per-model `/responses` vs `/chat/completions` routing, Copilot enterprise host resolution)
is preserved rather than flattened. Six adapters exist, and catalog providers ride them:
OpenRouter and Cloudflare Workers AI both run on `openai-completions`, while every bundled
OpenAI model runs on `openai-responses`.

| Provider                               | Runs on               | Status          |
| -------------------------------------- | --------------------- | --------------- |
| Anthropic                              | `anthropic-messages`  | ✅ supported    |
| OpenRouter                             | `openai-completions`  | ✅ supported    |
| GitHub Copilot (individual / Business) | mixed                 | ✅ supported    |
| OpenAI                                 | `openai-responses`    | 🧪 experimental |
| GitHub Copilot (Enterprise)            | mixed                 | 🧪 not live-tested |
| Google Gemini / Vertex                 | `google-*`            | 🧪 experimental |
| Cloudflare Workers AI                  | `openai-completions`  | 🧪 experimental |

Every bundled OpenAI model routes through `openai-responses`, so choosing one puts you on the
experimental row; the `openai-completions` adapter's real traffic is OpenRouter, Cloudflare and
other OpenAI-compatible hosts.

Copilot Enterprise is implemented and unit-tested — host resolution, domain prompting,
persistence — but has never been exercised against a live Enterprise seat.

Three catalogued providers have **no adapter in this build** (`amazon-bedrock`,
`azure-openai-responses`, `mistral`). The `/model` picker hides them and dispatch refuses them
with a message naming the protocols it does support — but `--list-models` will still print them
if you set their API key. See the [providers guide](docs/guides/providers-and-models.md).

## Trust and self-hosting

Built for closed networks and customer-site deployment. `--offline` turns off the outbound
calls Aelix makes for itself — the `rg`/`fd` download, the catalog fetch, index-less extension
installs, the update check — but not the provider you configured, so a closed network still
needs a reachable or self-hosted model endpoint. Two gaps worth knowing: `--offline` before an
`extension` subcommand is not seen (pass it after), and a `git+https://` install target
proceeds regardless. Policy and guardrails run as built-in
extensions, so every tool call and context mutation is an auditable hook event.

Built extension artifacts carry a signed supply chain that survives an air-gapped install —
`aelix extension keygen | sign | trust add`, and `install --require-signature` is fail-closed.
It covers artifacts installed by path or from an index; a `git+` target and an editable
directory are **refused** under that flag rather than verified. It is also **not** on by
default: no first-party keys are provisioned yet, so without the flag a missing signature is
accepted on first use.

One input is deliberately **not** gated: an `AGENTS.md` found between your working directory
and the filesystem root is read into the system prompt whether or not you trusted the project,
and its text then goes to your configured provider. `--no-context-files` turns that off. See
[SECURITY.md](SECURITY.md) and the [project trust guide](docs/guides/project-trust.md).

## Known limitations (beta)

Six things worth knowing before you point Aelix at something that matters.

**A run has no spend ceiling.** No iteration cap, no duplicate-call detection, no
cumulative token or cost budget — a model that keeps calling tools keeps costing
money until it finishes or you stop it. `Esc`, the 600 s `bash` *default* timeout
(an explicit per-call value is honoured up to an hour) and automatic compaction are
real backstops; none of them bounds spend
([#14](https://github.com/handochan/aelix-ai/issues/14),
[#6](https://github.com/handochan/aelix-ai/issues/6),
[#52](https://github.com/handochan/aelix-ai/issues/52)).

**Headless mode auto-approves mutating tools, and the two safety nets only see
built-in ones.** `--print`, `--mode json` and `--mode rpc` have no terminal for an
approval dialog, so `write`, `edit` and `bash` run without asking; and both
backstops match a fixed list of built-in tool names, so a tool from an MCP server,
a skill or a third-party extension reaches neither `GuardrailExtension` nor the
`--permission-mode plan` block. Give a headless run a container or a checkout you
can throw away ([#188](https://github.com/handochan/aelix-ai/issues/188)).

**One session, one terminal.** Session JSONL is append-only and nothing locks it,
so opening the same session twice makes one terminal's work a branch that no
`--resume` walks — valid on disk, gone from the transcript
([#137](https://github.com/handochan/aelix-ai/issues/137)).

**Transcripts keep everything, forever, unredacted.** Every prompt, tool argument
and tool result is written verbatim; there is no scrubbing pass. They are
owner-only on macOS and Linux (`0600` inside `0700`), and Windows has no mode-bit
equivalent for Aelix to set — either way the exposure is to whatever copies your
home directory: backups, sync clients, support bundles
([#138](https://github.com/handochan/aelix-ai/issues/138)).

**A successful `bash` call can silently lose the tail of its output.** With the
reader thread starved across the command's exit, up to ~64 KiB can be dropped from
the end — `exit_code` is 0, nothing says anything went missing, and the tail is the
part the model is shown. It did not reproduce in ~3,300 ordinary rounds
([#260](https://github.com/handochan/aelix-ai/issues/260)).

**Delegation's spend is invisible to `/cost`, and its cleanup is not equal on every
OS.** A child's tokens never enter the parent's session — read each delegation's
own footer — and a headless parent consents to spawns on its own. If Aelix itself
dies, Linux's `PR_SET_PDEATHSIG` and a Windows job object's `kill_on_close` end the
child; macOS has neither, and a grandchild that made its own session escapes the
process group that is all the containment there is
([#110](https://github.com/handochan/aelix-ai/issues/110)).

## Architecture

Three packages (a uv workspace), orchestrated by `Agent` and `AgentHarness`:

- **`aelix-ai`** — provider-agnostic messages, streaming primitives, tool definitions. No loop, no hooks.
- **`aelix-agent-core`** — the agent loop, `Agent`, `AgentHarness`, and the typed `HookBus`. No extension deps.
- **`aelix-coding-agent`** — `ExtensionAPI`, extension loader, built-in `PolicyExtension` / `GuardrailExtension`.

Small kernel, broad extension surface; policy and guardrails as built-in extensions rather
than core; an explicit hook bus for auditability. Full rationale in [`docs/`](docs/README.md).

## Docs

[Getting started](docs/guides/getting-started.md) ·
[Providers & models](docs/guides/providers-and-models.md) ·
[Custom models](docs/guides/models-json.md) ·
[Agent profiles](docs/guides/agent-profiles.md) ·
[Writing an extension](docs/guides/extension-authoring.md) ·
[Project trust](docs/guides/project-trust.md) ·
[Private catalog](docs/guides/private-catalog.md) ·
[Releasing](RELEASING.md)

Every guide except `RELEASING.md` ships inside the wheel, so an installed machine reads them
with no network and no checkout — `aelix docs`, `aelix docs project-trust`,
`aelix docs --search register_tool`.

[Homepage →](https://handochan.github.io/aelix-ai/) ·
[Extension catalog →](https://handochan.github.io/aelix-marketplace/)

## Building from source

```bash
uv sync                  # create .venv and install all workspace packages
uv run pytest            # run the test suite
uv run aelix --help      # the real CLI
```

Copy `.env.example` to `.env` for live-provider credentials. A `.env` is admitted for provider
credentials and a short list of provider-configuration names, and nothing else — it is read
before Aelix knows whether it trusts the directory. That admission list is narrow but it is not
nothing: a cloned repo's `.env` **can** supply the API key your session then runs on, so the
prompts go to the attacker's account. What it cannot do is execute a program, relocate the
credentials store, widen its own gate, or move a provider off its real host
([ADR-0203](docs/decisions/0203-dotenv-admission-control.md)). CA bundles, SDK knobs and base
URLs belong in your shell.

## License & attribution

[Apache-2.0](LICENSE), with an explicit patent grant. The **name and logo** are separate from
the code licence, and [TRADEMARK.md](TRADEMARK.md) grants more than Apache-2.0 §6 does:
describing your work as built on, compatible with, or an extension of Aelix needs no
permission, and neither does naming your package `aelix-<something>`.

Substantial portions of Aelix are a TypeScript-to-Python port of
[pi](https://github.com/earendil-works/pi) (reference commit `734e08e`), Copyright © 2025
[Mario Zechner](https://github.com/badlogic), MIT licensed. The bundled model catalog derives
from [models.dev](https://models.dev) (MIT). Full third-party licence texts ship in every
wheel and sdist ([NOTICE](NOTICE), [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)); the
dependency inventory is a CycloneDX SBOM under [`sbom/`](sbom/).

Anthropic, OpenAI, Google Gemini, GitHub Copilot, OpenRouter and Cloudflare are trademarks of
their respective owners; Aelix is an independent project, and names are used only to identify
the services it can connect to.
