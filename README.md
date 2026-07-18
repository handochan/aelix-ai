# Aelix

**Own your coding agent — in pure Python.**

Self-hosted, auditable, and extensible in the language your team already writes — on the
model budgets you already pay for.

[한국어 README →](README.ko.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

<p align="center">
  <img src="docs/assets/demo.gif" width="100%" alt="aelix demo — the agent writes its own pandas extension into my_ext.py, /reload hot-loads it without restarting, and the next prompt runs it in-process">
</p>
<p align="center"><em>The agent extends itself: it authors a pandas <code>describe_dataset</code> tool into <code>my_ext.py</code>, <code>/reload</code> hot-loads it without restarting, and the very next prompt runs it in-process. Waits are fast-forwarded.</em></p>

Aelix is an open-source coding agent written in pure Python. Read every line it runs, keep it
entirely inside your own perimeter, and extend it with plain Python functions that import your
existing stack — pandas, an internal SDK, a warehouse client — directly, in-process: the
reason data and ML teams reach for it first. And it never phones home.

---

## Why aelix

- 🐍 **Extensions are just Python.** A tool is a plain function — no plugin language, no
  out-of-process bridge. Drive the agent from a terminal, a notebook, a pipeline, or CI.
  [See the example ↓](#extensions-are-just-python--call-your-data-stack-in-process)
- 💳 **Runs on the budget you already own.** Native adapters for Anthropic, OpenAI,
  Gemini/Vertex, OpenRouter, Cloudflare, and GitHub Copilot — including the individual,
  Business, or Enterprise seat you already sign in with (usage subject to your GitHub
  agreement). Route cheap work and hard reasoning to different models from one session. No
  metered ACUs, no new vendor.
- 🔏 **Signed supply chain.** Extensions are verified with Ed25519 provenance and SHA-256
  pinning (`extension keygen | sign | trust`, fail-closed `--require-signature`), installable
  from an offline catalog. Uncommon in coding agents; native here.
- 🔍 **Auditable & self-hosted.** Fully open source, no telemetry, air-gap-ready `--offline`
  mode. Trust lives in code you can read — the answer to *"why run an agent I didn't write?"*
- 🧩 **Extensible to the core.** A small kernel where even policy, permissions, and guardrails
  are swappable built-in extensions, plus one broad `ExtensionAPI` — tools, slash commands,
  providers, message renderers, themes, and your own `/login` flow (SSO / employee-ID) — with
  live hot-reload, no restart.
- ⚙️ **Scriptable & headless.** `--print`, line-delimited `--mode json`, and a `--mode rpc`
  JSONL protocol make aelix embeddable in pipelines, CI, and evaluation loops — deterministic
  and machine-readable.

## Install

During the beta, aelix installs from GitHub Releases through a checksum-verified installer.
It bootstraps [uv](https://docs.astral.sh/uv/) if needed, verifies every wheel against the
release's `SHA256SUMS` manifest (any mismatch aborts), and installs the global `aelix`
command:

```bash
curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
```

Pin a release with `AELIX_VERSION=v0.1.0-beta.1` (recommended during the beta) and pick
extras with `AELIX_EXTRAS` — default `tui`; `tui,images` adds inline terminal image
rendering; empty (`AELIX_EXTRAS=`) installs the headless CLI only (print / json / rpc).
Once aelix is published to PyPI, `uv tool install 'aelix[tui]'` — or the `pipx` / `pip`
equivalent — will work as usual.

```bash
aelix                                            # interactive agent (TUI)
aelix --model openai/gpt-4o-mini "summarise this repo"
aelix --print "what files changed?"              # one-shot, headless
aelix --offline                                  # air-gap mode
aelix --help
```

`aelix` needs a provider credential — set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`OPENROUTER_API_KEY`, launch `aelix` and run `/login` inside the TUI (Copilot / subscription
OAuth), pass `--api-key`, or configure `~/.aelix/agent/models.json`. See the
[providers guide](docs/guides/providers-and-models.md).

## Providers

Hand-written native adapters — no litellm, no generic wrapper layer — with per-provider
behavior branches (OpenRouter and Cloudflare Workers AI ride the shared OpenAI-completions
adapter), so provider-specific details (Anthropic thinking-block replay, per-model
`/responses` vs `/chat/completions` routing, Copilot enterprise host resolution) are
preserved rather than flattened.

| Provider | Status |
|---|---|
| Anthropic (Messages) | ✅ supported |
| OpenAI (chat completions) | ✅ supported |
| OpenRouter | ✅ supported |
| GitHub Copilot (individual / Business / Enterprise) | ✅ supported |
| OpenAI Responses API | 🧪 experimental |
| Google Gemini / Vertex | 🧪 experimental |
| Cloudflare Workers AI | 🧪 experimental |

## Extensions are just Python — call your data stack in-process

An aelix extension is just a `setup(aelix)` function. There is no separate plugin language and
no out-of-process bridge, so a tool can import your existing stack and hand results straight
back to the model — this is why aelix was built for data and ML teams first:

```python
# my_ext.py  —  a data tool in ~20 lines; loads with:  aelix -e ./my_ext.py
from typing import Any
import pandas as pd                       # your own dependency, imported in-process

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_agent_core.types import AgentTool
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_ai.messages import TextContent


async def _describe(args: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
    df = pd.read_parquet(args["path"])     # or query your warehouse, call an internal SDK…
    return ToolResult(content=[TextContent(text=df.describe().to_markdown())])


def setup(aelix: ExtensionAPI) -> None:
    aelix.register_tool(AgentTool(
        name="describe_dataset",
        description="Summary statistics for a Parquet/CSV dataset.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to the dataset."}},
            "required": ["path"],
        },
        execute=_describe,
    ))
```

The same `ExtensionAPI` also registers slash commands, providers, message renderers, themes,
and a custom `/login` flow — and every extension **hot-reloads without restarting the session**.

**Embed it anywhere Python runs.** Drive the agent headlessly from a notebook, an
Airflow/Prefect/Dagster task, or a CI job:

```bash
aelix --print "profile data/train.parquet and flag columns with >5% nulls"
aelix --mode json "run the eval suite and summarise failures"   # line-delimited events
```

See [writing an extension](docs/guides/extension-authoring.md) for the full surface.

## Trust & self-hosting

Aelix is built for closed networks and customer-site deployment. `--offline` engages air-gap
mode (no tool-binary downloads, no network extension installs), the extension catalog browses
and installs without phoning home, trust uses local pins (no online revocation checks), and
`register_login_provider` lets an extension add enterprise SSO / employee-ID auth. Policy and
guardrails are enforced as built-in extensions, so every tool call and context mutation is an
observable, auditable hook event.

Distribute and verify extensions with a signed supply chain — trust that survives an
air-gapped install:

```bash
aelix extension install <path | git-url | package[==version]>   # pip-based, --offline capable
aelix extension keygen                                          # publisher Ed25519 key
aelix extension sign <artifact>                                 # detached .aelixsig
aelix extension trust add <key>                                 # trust a verification key
aelix extension install <target> --require-signature            # fail-closed provenance gate
```

## Architecture

Three packages make up the agent (a uv workspace), orchestrated by `Agent` and `AgentHarness`:

- **`aelix-ai`** — provider-agnostic messages, streaming primitives, tool definitions. No loop, no hooks.
- **`aelix-agent-core`** — the agent loop, `Agent`, `AgentHarness`, and the typed `HookBus`. No extension deps.
- **`aelix-coding-agent`** — `ExtensionAPI`, extension loader, built-in `PolicyExtension` / `GuardrailExtension`.

Design principles: small kernel + broad extension surface · policy/guardrails as built-in
extensions, not core · explicit hook bus for auditability. Full rationale in
[`docs/`](docs/README.md).

## Docs

[Getting started](docs/guides/getting-started.md) ·
[Providers & models](docs/guides/providers-and-models.md) ·
[Custom models](docs/guides/models-json.md) ·
[Writing an extension](docs/guides/extension-authoring.md) ·
[Releasing](RELEASING.md)

## Building from source (contributors)

Aelix uses [uv](https://docs.astral.sh/uv/) for environment and dependency management.

```bash
uv sync                  # create .venv and install all workspace packages
uv run pytest            # run the test suite
uv run aelix --help      # the real CLI
```

Copy `.env.example` to `.env` for live-provider credentials (the credential-free demo
`python -m aelix` needs none).

## License & attribution

[Apache-2.0](LICENSE) — permissive, with an explicit patent grant.

Substantial portions of Aelix are a TypeScript-to-Python port of
[pi](https://github.com/earendil-works/pi) (reference commit `734e08e`),
Copyright © 2025 [Mario Zechner](https://github.com/badlogic), MIT licensed. The bundled
model catalog derives from data published by [models.dev](https://models.dev) (MIT).
Full third-party license texts are preserved in [NOTICE](NOTICE) and
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md), which ship in every wheel and sdist;
the dependency inventory is recorded as a CycloneDX SBOM under [`sbom/`](sbom/).

Anthropic, OpenAI, Google Gemini, GitHub Copilot, OpenRouter, and Cloudflare are
trademarks of their respective owners; Aelix is an independent project, and names are
used only to identify the services it can connect to.
