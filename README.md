# Aelix

Aelix is a Python-based agent runtime for building, composing, and operating
extensions as an extensible agent platform.

The project is intentionally organized around a small runtime kernel:

- `AgentRuntime` owns execution, lifecycle, and extension routing.
- `Extension` defines the extension contract.
- `ExtensionRegistry` keeps installed extensions discoverable.
- `MarketplaceIndex` reads extension pack metadata without executing code.
- Built-in `policy` and `guardrail` extensions enforce permission and safety decisions via lifecycle hooks (see ADR-0004).

This keeps the core runtime lightweight while leaving room for a marketplace,
customer-site deployments, offline packs, policy enforcement, and specialized
agent systems.

## Quick Start

Aelix uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management.

```bash
uv sync                  # create .venv and install dependencies (dev included)
uv run pytest            # run the test suite
uv run aelix             # run the built-in demo extension
```

For live LLM tests (Phase 2+), copy `.env.example` to `.env` and fill in your
provider credentials. Phase 1.1 runs with a mock stream function and does not
require any API keys.

## Architecture

```text
Aelix Runtime
  Kernel
    AgentRuntime
    ExecutionContext
    Lifecycle hooks
  Extensions
    Extension protocol
    ExtensionRegistry
    ExtensionResult
  Built-in Extensions
    policy
    guardrail
  Marketplace
    Multi-source index (npm, git, internal)
    Source-specific metadata
  SDK
    Helpers for pack authors
```

Design notes and evolving requirements are maintained in [`docs/`](docs/README.md).

## Extension Packs

Extension packs are distributed via marketplace indexes (npm registry, git
repositories, or internal custom indexes). Each index source is responsible for
exposing normalized pack metadata that can be inspected, audited, signed, and
approved before any extension code runs (see ADR-0005).

Pack metadata always carries the same shape (id, name, version, description,
entrypoint, permissions); the wire format depends on the source.

## Design Principles

- Small kernel, broad extension surface.
- Multi-source marketplace with normalized metadata (npm, git, internal).
- Policy and guardrails enforced by built-in extensions, not by core.
- Explicit execution context for auditability.
- Standard-library baseline before framework commitments.
