# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The published distribution set is released in lock-step at a single shared
version: `aelix-ai`, `aelix-agent-core`, `aelix-coding-agent`, and the `aelix`
umbrella meta-package. (`aelix-server`, the Web-UI daemon, is deferred to a
later release and is not part of this publish set.)

## [Unreleased]

### Added

- **Beta / pre-release track** — pre-releases (beta/rc/alpha) are cut as
  **GitHub Releases only** and installed via a checksum-verified `install.sh`
  one-liner (`uv`-based, wheels verified against a published `SHA256SUMS`
  manifest); PyPI publishing is reserved for GA. The `release.yml` workflow now
  attaches a `SHA256SUMS` manifest to each Release and skips the PyPI `publish`
  job for hyphenated (pre-release) tags. First beta version: `0.1.0b1`
  (tag `v0.1.0-beta.1`). See `RELEASING.md` → *Beta / pre-release track*.

## [0.1.0] - 2026-06-20

Initial public release of the Aelix agent runtime — a pi-faithful, Python-native
agent platform.

### Added

- **Agent runtime (`aelix-agent-core`)** — stateful `Agent`, hook-aware
  `AgentHarness`, typed `HookBus`, and the low-level async agent loop.
- **AI primitives (`aelix-ai`)** — provider-agnostic message, streaming, and
  tool types with pi-ai parity.
- **Providers** — Anthropic and OpenAI-compatible backends (incl. OpenRouter),
  with reasoning/thinking wiring, custom-model loading from `models.json`, and
  config-value auth indirection (env-var / command).
- **Built-in tools** — bash, read, write, edit, ls, grep, and find, with
  pi-parity schemas and behavior (including image read/resize and `rg`/`fd`
  acquisition).
- **Compaction** — context summarization with entry-level cut-points,
  split-turn handling, file-op preservation, and a token cap.
- **Extensions API (`aelix-coding-agent`)** — 4-tier extension architecture,
  extension loader, built-in policy/guardrail extensions, runtime tool
  registration, and example tools.
- **Project Trust** — running in an untrusted directory gates project-local
  extensions (`.aelix/extensions/`) and MCP servers (`.aelix/mcp.json`) behind a
  trust prompt with on-disk persistence; deny-by-default in headless mode.
- **Cooperative abort** — `Esc` cancels in-flight tools (bash, grep, find, read,
  write, edit, ls) without orphaning processes, and the RPC `abort_bash` kills
  the running shell.
- **TUI** — an interactive terminal shell (optional `[tui]` extra) with slash
  commands, streaming Markdown output, compact tool cards, a status footer and
  context meter, steer/queue, session resume/fork, and an external-editor
  binding. Inline image rendering via the optional `[images]` extra.
- **CLI** — the real `aelix` command (session, fork, export, and model flags)
  plus a headless RPC mode and OAuth credential management.
- **Release engineering** — CI (ruff + pytest on Python 3.11 / 3.12) and a
  tag-triggered PyPI publish workflow using Trusted Publishing (OIDC).

[Unreleased]: https://github.com/handochan/aelix-ai/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/handochan/aelix-ai/releases/tag/v0.1.0
