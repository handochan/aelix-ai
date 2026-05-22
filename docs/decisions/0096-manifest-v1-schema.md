# 0096. Aelix Plugin Manifest v1 (`aelix-plugin.toml`)

Status: Accepted (Sprint 6h₉a / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Pi has no manifest file — it auto-discovers `.ts` files in
`~/.pi/agent/extensions/` and `.pi/extensions/`. Aelix needs a manifest
for five reasons that Pi's "trust-the-user, no manifest" model does not
address:

1. **Capabilities declaration** — ADR-0094 4-tier model requires plugins
   to declare which tiers they participate in (`ui_tui_trusted`,
   `ui_descriptor`, `ui_web_trusted`, `mcp_serve`, etc.). Phase 6 will
   enforce capabilities as actual permission tokens (workspace trust
   dialog + injection gate); Phase 5b uses declarations for loader
   wiring + documentation.
2. **API_LEVEL ABI versioning** — Pi's lack of an explicit ABI version
   forces the maintainer to backport API changes manually. Aelix
   follows Neovim's API_LEVEL pattern: an integer that increments on
   breaking changes to the public extension API, independent of
   Aelix's own semver. Plugins declare both `level` (built against)
   and `min_level` (engine compatibility floor).
3. **Declarative slot contributions** — `[contributes.*]` blocks let
   the loader validate at build/install time which slots a plugin
   targets, rather than discovering at runtime via emit calls. Faster
   discovery and IDE-friendly schema.
4. **Marketplace metadata** — Phase 6 marketplace UI needs identity
   (id, name, version, authors, repository, license, homepage). Manifest
   carries it.
5. **Cross-language extension authoring** — Phase 6+ may open Aelix to
   TypeScript plugins via Tier 3. A language-neutral manifest is the
   natural bridge.

## File specification

- **Location**: plugin root directory, filename `aelix-plugin.toml`.
- **Format**: TOML 1.0 (Python `tomllib` native; pyproject.toml
  syntactic consistency).
- **Encoding**: UTF-8.
- **Parser**: Pydantic v2 via `tomllib.loads()` →
  `PluginManifest.model_validate(...)`. See
  `parse_manifest_toml()` helper in
  `packages/aelix-agent-core/src/aelix_agent_core/contracts/manifest.py`.

## Section schema (lock these)

```toml
# aelix-plugin.toml v1

[plugin]
id          = "kebab-case-id"           # required, immutable, regex `^[a-z][a-z0-9-]{0,63}$`
name        = "Display Name"            # required, free-form display
version     = "0.1.0"                   # required, strict semver (no SHA fallback)
description = "One-line description"    # required
authors     = ["Author Name <email>"]   # required, non-empty list
repository  = "https://github.com/..."  # required (Open VSX style for trust)
license     = "MIT"                     # required, SPDX whitelist (see §"SPDX license whitelist v1")
homepage    = "https://..."             # optional

[plugin.api]
level       = 1                         # required, int — Aelix API ABI level this plugin was built against
min_level   = 1                         # required, int — lowest level this plugin runs on (engine compatibility floor)

[plugin.entry]
python      = "my_pkg.extension:default"  # required when capabilities.ui_tui_trusted or .ui_descriptor or .mcp_serve

[capabilities]
shell_exec       = false   # bool — plugin needs shell command execution
fs_write         = false   # bool — plugin writes to filesystem
fs_read_user     = false   # bool — plugin reads outside cwd (user $HOME, $XDG_CONFIG_HOME, ...)
net              = false   # bool — plugin opens network connections
mcp_invoke       = false   # bool — plugin calls MCP servers
ui_tui_trusted   = false   # bool — plugin provides Tier 1 TUI widgets
ui_descriptor    = false   # bool — plugin emits Tier 2 descriptors
ui_web_trusted   = false   # bool — plugin provides Tier 3 React/Svelte components (Phase 6 only)
mcp_serve        = false   # bool — plugin runs as MCP server (T4)

[activation]
on_startup_finished = true              # bool — load after host startup completes
on_command          = ["my-command"]    # list[str] — slash commands that trigger load
on_tool_call        = ["my_tool"]       # list[str] — tool names that trigger load
on_session_start    = false             # bool — load on session start event

# NO `*` activation allowed (anti-pattern from VS Code, banned in Aelix)

[contributes]
commands      = [{ id = "...", description = "..." }]            # T1 commands
tui_widgets   = [{ slot = "...", factory = "module:factory" }]   # T1 TUI Textual/Rich widget factories
descriptors   = [{ kind = "...", id = "..." }]                   # T2 descriptors
tools         = [{ name = "...", description = "..." }]          # T1 tools
themes        = ["themes/dark.toml", "themes/light.toml"]        # path list
mcp_servers   = [{ name = "...", transport = "stdio", command = "..." }]  # T4 MCP server configs
hooks         = [{ event = "PreToolUse", command = "scripts/check.py" }]  # T4 subprocess hooks
```

The Pydantic v2 models matching this schema live in
`packages/aelix-agent-core/src/aelix_agent_core/contracts/manifest.py`.
The JSON Schema artifact lives at `docs/contracts/manifest.schema.json`.

## API_LEVEL policy

- Current `AELIX_API_LEVEL` = **1** (Sprint 6h₉a baseline).
- Breaking changes to ANY public extension API
  (`ExtensionAPI`, `ExtensionUIContext`, descriptor schema, manifest
  schema) increment `AELIX_API_LEVEL` by 1.
- **Deprecation cycle**: deprecated APIs warn for one minor release
  before removal.
- **Rejection rule**: plugins declaring `min_level > AELIX_API_LEVEL`
  MUST be rejected at load time (Pydantic validator enforces ordering;
  host loader enforces compatibility floor).
- **Forward-compat acceptance**: plugins declaring `level >
  AELIX_API_LEVEL` MAY load with a warning (forward-compat
  best-effort). The host MAY override via `--allow-future-api` flag in
  the future.
- See `contracts/api_level.py` for the `assert_compatible()` helper.

## SPDX license whitelist v1

Permitted licenses (v1):

- `MIT`
- `Apache-2.0`
- `BSD-3-Clause`
- `BSD-2-Clause`
- `MPL-2.0`
- `ISC`
- `Unlicense`
- `Apache-2.0 WITH LLVM-exception`

GPL family (GPL-2.0, GPL-3.0, AGPL-3.0, LGPL-2.1, LGPL-3.0) is **NOT**
in v1 whitelist; compatibility audit deferred to Phase 6.

Custom license string MAY be accepted via `license = "Custom
(LICENSE-FILENAME.md)"` form (warning logged). Strict whitelist
enforcement is gated by a `--strict-licenses` flag; Phase 6 default
will be true.

Phase 5b posture: Pydantic accepts unknown license strings; the host
loader emits a warning. Phase 6 will flip to refusal under
`--strict-licenses`.

The frozen set is exported as `LICENSE_WHITELIST` from
`contracts/manifest.py`.

## Capabilities declaration vs enforcement

- **Sprint 6h₉a / Phase 5b**: capabilities are **declaration-only**.
  Pydantic validates the schema; the runtime does NOT block plugin
  behavior based on declarations. This avoids premature lockdown while
  the API surface stabilizes.
- **Phase 6 enforcement**: capabilities become actual capability tokens.
  The host refuses to inject `shell.exec` / `fs.write` adapters into the
  plugin's `ctx` unless declared. The workspace trust dialog surfaces
  the capability list before loading project-scoped extensions.
- **Aelix-additive design**: the declaration-vs-enforcement split
  mirrors VS Code's `capabilities.untrustedWorkspaces` (declarative
  metadata for v1) vs Zed's runtime capability check (enforcement at
  every adapter call site).

## Activation policy

- `*` activation is **BANNED** (VS Code anti-pattern; forces
  always-load, blows up startup time as the ecosystem grows).
- **Lazy load is mandatory**. Plugins MUST declare at least one of:
  - `on_startup_finished=true`
  - `on_command` (non-empty list)
  - `on_tool_call` (non-empty list)
  - `on_session_start=true`
- `on_command` triggers when a slash command palette item is selected.
- `on_tool_call` triggers when the LLM (or another extension) invokes a
  tool by name.
- `on_session_start` triggers on every `session_start` event (Pi naming
  preserved).
- Eager load (`on_startup_finished=true`) is permitted but discouraged
  for plugins with heavy import side-effects.

The `Activation.at_least_one()` model validator enforces the
"at least one trigger" rule. See `contracts/manifest.py`.

## Pi divergences

Pi has no manifest. The Aelix manifest is a synthesis of patterns from
adjacent ecosystems:

- **Neovim API_LEVEL pattern** — semver-independent ABI versioning
  (https://neovim.io/doc/user/api/, RFC PR #5535).
- **Zed `extension.toml`** — SPDX whitelist + minimal `[contributes]`
  section (https://zed.dev/docs/extensions/developing-extensions).
- **VS Code declarative `contributes.*` taxonomy** — declarative
  contributions for IDE-friendly discovery
  (https://code.visualstudio.com/api/references/contribution-points).
- **VS Code `activationEvents` lazy-loading semantics** — without `*`
  (https://code.visualstudio.com/api/references/activation-events).
- **Pi auto-discovery paths translated** — `~/.pi/agent/extensions/` and
  `.pi/extensions/` become `~/.aelix/extensions/` and
  `.aelix/extensions/`. The Aelix manifest sits inside each plugin
  directory.
- **Open VSX-style identity** — `id` + `repository` + `authors` +
  `license` for marketplace trust (Phase 6).

## References

- ADR-0094 (Sprint 6h₉a) — Aelix Extension Architecture (4-tier model). Manifest declares capabilities per tier.
- ADR-0095 (Sprint 6h₉a) — UI Descriptor Protocol. Manifest's `[contributes.descriptors]` references the protocol's `kind` literals.
- ADR-0097 (Sprint 6h₉a) — Multi-Frontend Architecture. Manifest's `capabilities.ui_web_trusted` is the Phase 6 web flag.
- Pi `packages/coding-agent/docs/extensions.md` (SHA `734e08e`) — auto-discovery paths reference (Aelix translates the paths but uses a manifest instead of `.ts` auto-discovery).
- Pi `packages/coding-agent/src/core/extensions/types.ts` (SHA `734e08e`) — no manifest precedent in Pi; the manifest is wholly Aelix-additive.
- Neovim API_LEVEL RFC — https://github.com/neovim/neovim/pull/5535
- Zed extension.toml docs — https://zed.dev/docs/extensions/developing-extensions
- VS Code contribution points — https://code.visualstudio.com/api/references/contribution-points
- VS Code activation events — https://code.visualstudio.com/api/references/activation-events
- Harlequin entry-points pattern — https://github.com/tconbeer/harlequin/blob/main/src/harlequin/plugins.py
