# Aelix

### 파이썬 데이터·ML 팀을 위한 자체호스팅 코딩 에이전트
### The self-hosted coding agent for Python data & ML teams

순수 파이썬으로 당신의 데이터 스택을 그대로 확장하고, 모든 코드를 감사하고, 당신의 perimeter
안에서만 실행하세요 — 이미 가진 모델 예산으로.
*Extend it in pure Python with your own data stack, audit every line, and run it entirely inside
your own perimeter — on the model budget you already own.*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
<!-- TODO(owner): CI · PyPI · stars 배지는 릴리즈/CI 확정 후 추가 -->

Aelix는 **파이썬 데이터·ML 팀을 위한** 오픈소스 코딩 에이전트입니다. 순수 파이썬으로 쓰였기에
확장은 **당신의 데이터·ML 스택(pandas·numpy·내부 SDK·웨어하우스 클라이언트)을 그대로
in-process로 불러** 씁니다. 그리고 프론티어 CLI(Claude Code·Codex·Gemini CLI)가 모델 벤더에
묶여 있는 것과 달리, aelix는 **당신이 소스를 직접 감사하고, perimeter 안에서 실행하며, 서명된
공급망으로 확장을 검증**합니다. Anthropic·OpenAI·Gemini·OpenRouter는 물론 **회사가 이미 산
GitHub Copilot Business/Enterprise 좌석**까지 네이티브로 쓰며, 토큰과 소스는 네트워크를
벗어나지 않습니다.

Aelix is an open-source coding agent **built for Python data and ML teams.** Because it is
written and extended in pure Python, a tool can import your existing stack — pandas, numpy, an
internal SDK, a warehouse client — directly, in-process. And where frontier CLIs are captive to
the model vendor that funds them, aelix lets you **read the source, run it inside your own
perimeter, and verify extensions with a signed supply chain** — on the model budget you already
own, including GitHub Copilot Business/Enterprise seats. No litellm indirection, no TypeScript
bridge, no phone-home.

<!-- TODO(owner): 여기에 60초 asciinema/GIF 데모 (모델 전환 + Copilot 좌석 + 커스텀 데이터 툴 hot-reload) -->
<p align="center"><em>(demo GIF — model switching · Copilot-seat consumption · a hot-reloaded Python data tool)</em></p>

---

## Why aelix

- **🐍 Python-native & data-friendly.** Written and extended in Python. Tools run in-process,
  so they can call your data/ML libraries and internal SDKs directly — and you can drive the
  agent from a notebook, a data pipeline, or CI. [Jump to the example ↓](#extend-it-in-python--built-for-data--ml-teams)
- **🧩 Extensible to the core.** A small kernel where even policy, permissions, and guardrails
  ship as *swappable built-in extensions* (not welded into core), plus one broad `ExtensionAPI`
  — custom tools, commands, providers, message renderers, themes, and your own `/login` flow
  (SSO / employee-ID) — with **live hot-reload**, no restart.
- **🔍 Auditable & self-hosted.** Fully open source, no telemetry, `--offline` mode for
  closed networks. Trust lives in code you can read — the answer to *"why run an agent I
  didn't write?"* The teams handling the most sensitive data get the strongest guarantee.
- **🔏 Signed supply chain.** Extensions are verified with Ed25519 provenance and SHA-256
  pinning (`extension keygen | sign | trust`, `--require-signature` fail-closed), installable
  from an offline catalog with no phone-home. Uncommon in coding agents; native here.
- **💳 Runs on the budget you already own.** Native adapters for Anthropic, OpenAI,
  Gemini/Vertex, OpenRouter, Cloudflare, and **GitHub Copilot — including Business/Enterprise
  seats.** Route cheap work and hard reasoning to different models from one terminal, on
  contracts your org already approved. No metered ACUs, no new vendor.
- **⚙️ Scriptable & headless.** `--print`, line-delimited `--mode json`, and a `--mode rpc`
  JSONL protocol make aelix embeddable in pipelines, CI, and eval harnesses — deterministic,
  machine-readable, pure Python.

## Install

Aelix installs as a single global `aelix` command:

```bash
uv tool install 'aelix[tui]'     # recommended (uv) — CLI + interactive TUI
pipx install 'aelix[tui]'        # or pipx
pip install 'aelix[tui]'         # or pip (add [images] for inline image rendering)
pip install aelix                # CLI + headless only (print / json / rpc)
```

```bash
aelix                                            # interactive agent (TUI)
aelix --model openai/gpt-4o-mini "summarise this repo"
aelix --print "what files changed?"              # one-shot
aelix --offline                                  # no startup network calls (air-gapped)
aelix --help
```

`aelix` needs a provider key — set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`OPENROUTER_API_KEY`, run `aelix /login` (Copilot / subscription OAuth), pass `--api-key`,
or configure `~/.aelix/agent/models.json`. See the
[providers guide](docs/guides/providers-and-models.md).

## Providers

Native adapters, hand-written per provider (not a generic wrapper), so provider-specific
behavior — Anthropic thinking-block replay, per-model `/responses` vs `/chat/completions`
routing, Copilot enterprise host resolution — is preserved rather than flattened.

| Provider | Status |
|---|---|
| Anthropic (Messages) | ✅ verified |
| OpenAI (chat completions) | ✅ verified |
| OpenRouter | ✅ verified |
| GitHub Copilot (individual / Business / Enterprise) | ✅ verified |
| OpenAI Responses API | 🧪 experimental (live-smoke tracked in #61) |
| Google Gemini / Vertex | 🧪 experimental (#61) |
| Cloudflare Workers AI | 🧪 experimental (#61) |

<!-- TODO(owner): verified/experimental 라벨을 실 키 스모크(#61) 결과로 최종 확정 -->

## Extend it in Python — built for data & ML teams

An aelix extension is just a `setup(aelix)` function. There is no separate plugin language and
no out-of-process bridge, so a tool can import your existing stack and hand results straight
back to the model:

```python
# my_ext.py  —  a data tool in ~20 lines; loads with:  aelix -e ./my_ext.py
from typing import Any
import pandas as pd                       # your data/ML libs, imported in-process

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
Airflow/Prefect/Dagster task, or a CI job — deterministic and machine-readable:

```bash
aelix --print "profile data/train.parquet and flag columns with >5% nulls"
aelix --mode json "run the eval suite and summarise failures"   # line-delimited events
```

See [writing an extension](docs/guides/extension-authoring.md) for the full surface.

## Extensions & supply-chain trust

Distribute and verify extensions with a signed supply chain — trust that survives an
air-gapped install:

```bash
aelix extension install <path | git-url | package[==version]>   # pip-based, --offline capable
aelix extension keygen                                          # publisher Ed25519 key
aelix extension sign <artifact>                                 # detached .aelixsig
aelix extension trust add <key>                                 # trust a verification key
aelix extension install <target> --require-signature            # fail-closed provenance gate
```

## Self-hosted & air-gapped

Aelix is built for closed networks and customer-site deployment: `--offline` disables all
startup network calls, the extension catalog browses and installs without phoning home, trust
uses local pins (no online CRL), and `register_login_provider` lets an extension add enterprise
SSO / employee-ID auth. Policy and guardrails are enforced as built-in extensions, so every
tool call and context mutation is an observable, auditable hook event.

## Quick start (development)

Aelix uses [uv](https://docs.astral.sh/uv/) for environment and dependency management.

```bash
uv sync                  # create .venv and install all workspace packages
uv run pytest            # run the test suite
uv run aelix --help      # the real CLI
```

Copy `.env.example` to `.env` for live-provider credentials (the credential-free demo
`python -m aelix` needs none).

## Architecture

A uv workspace of three packages, orchestrated by `Agent` and `AgentHarness` (ADR-0015):

- **`aelix-ai`** — provider-agnostic messages, streaming primitives, tool definitions. No loop, no hooks.
- **`aelix-agent-core`** — the agent loop, `Agent`, `AgentHarness`, and the typed `HookBus`. No extension deps.
- **`aelix-coding-agent`** — `ExtensionAPI`, extension loader, built-in `PolicyExtension` / `GuardrailExtension`.

Design principles: small kernel + broad extension surface · policy/guardrails as built-in
extensions, not core · explicit hook bus for auditability · standard-library baseline before
framework commitments. Full rationale in [`docs/`](docs/README.md).

## Docs

[Getting started](docs/guides/getting-started.md) ·
[Providers & models](docs/guides/providers-and-models.md) ·
[Custom models](docs/guides/models-json.md) ·
[Writing an extension](docs/guides/extension-authoring.md) ·
[Releasing](RELEASING.md)

## License & attribution

[Apache-2.0](LICENSE) — permissive with an explicit patent grant, which matters for the
enterprise / self-hosted buyers this positioning targets.

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
