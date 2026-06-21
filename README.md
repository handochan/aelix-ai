# Aelix

Aelix는 Python 기반의 에이전트 런타임으로, 확장 가능한 에이전트 플랫폼을 구축·조합·운용하기 위한 도구입니다.

Aelix is a Python-based agent runtime for building, composing, and operating
extensions as an extensible agent platform.

The project is organised as a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/)
containing three packages, orchestrated by `Agent` and `AgentHarness` (see ADR-0015):

- `aelix-ai` — provider-agnostic message types, streaming primitives, and tool definitions. No agent loop, no hook bus.
- `aelix-agent-core` — the low-level agent loop, stateful `Agent` class, `AgentHarness`, and the `HookBus`. Core runtime with no extension dependencies.
- `aelix-coding-agent` — `ExtensionAPI` surface, extension loader, built-in `PolicyExtension`/`GuardrailExtension`, and example tools.

This keeps the core runtime lightweight while leaving room for policy
enforcement, customer-site deployments, offline packs, and specialised agent
systems.

## Workspace layout

```text
aelix-ai/
├── pyproject.toml                    # workspace anchor + `aelix` umbrella package
├── src/aelix/                        # umbrella re-export package
├── packages/
│   ├── aelix-ai/                     # AI primitives (messages, tools, streaming)
│   ├── aelix-agent-core/             # agent loop + harness + hook bus
│   └── aelix-coding-agent/           # extensions + built-ins + examples
└── tests/                            # shared test suite
```

See ADR-0015 for the full package boundary rationale.

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

Then run it from anywhere:

```bash
aelix                                            # interactive agent (TUI)
aelix --model openai/gpt-4o-mini "summarise this repo"
aelix --print "what files changed?"              # one-shot
aelix --help
```

`aelix` needs a provider key — set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`OPENROUTER_API_KEY` (or pass `--api-key`, or configure a key in
`~/.aelix/agent/models.json`). The [providers guide](docs/guides/providers-and-models.md)
has the full list. To publish a release, see [RELEASING.md](RELEASING.md).

**User guides:** [getting started](docs/guides/getting-started.md) ·
[providers & models](docs/guides/providers-and-models.md) ·
[custom models](docs/guides/models-json.md) ·
[writing an extension](docs/guides/extension-authoring.md).

## Quick Start

Aelix uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management.

```bash
uv sync                  # create .venv and install all workspace packages (dev included)
uv run pytest            # run the test suite
uv run aelix --help      # the real CLI (`python -m aelix` runs the credential-free demo)
```

For live LLM tests (Phase 2+), copy `.env.example` to `.env` and fill in your
provider credentials. Phase 1 runs with a mock stream function and does not
require any API keys.

## Architecture

```text
aelix-agent-core
  Agent                   — stateful wrapper around the low-level loop
  AgentHarness            — hook-aware orchestrator; wires extensions into the loop
  HookBus                 — typed hook event / result bus (before_agent_start, tool_call, …)
  agent_loop              — low-level async turn runner

aelix-coding-agent
  ExtensionAPI            — façade an extension factory receives (setup(aelix) → None)
  Extension loader        — resolves factories, module paths, file paths
  PolicyExtension         — allowlist/denylist enforcement via tool_call hook
  GuardrailExtension      — content safety via tool_call hook
  examples/echo           — minimal demo tool

aelix-ai
  Messages / streaming    — AgentMessage, AssistantMessage, UserMessage, StreamFn, …
  Tool types              — Tool, AgentTool, ToolResult
```

Design notes and evolving requirements are maintained in [`docs/`](docs/README.md).

## Extension Packs

Extension factories are callables that receive an `ExtensionAPI` handle and
register tools, hook handlers, and flags. The loader resolves inline factories,
dotted module paths, and file paths, collecting errors per-extension without
aborting the batch (see ADR-0004 and ADR-0007).

## Design Principles

- Small kernel, broad extension surface.
- `aelix-agent-core` declares no dependency on `aelix-coding-agent`; the
  harness uses a lazy local import to break the runtime cycle.
- Policy and guardrails enforced by built-in extensions, not by core.
- Explicit hook bus for auditability — every tool call and context mutation
  is an observable event.
- Standard-library baseline before framework commitments.
