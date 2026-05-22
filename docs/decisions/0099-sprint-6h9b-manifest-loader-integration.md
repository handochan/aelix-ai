# 0099. Sprint 6h₉b — Manifest v1 Loader Integration

Status: Accepted (Sprint 6h₉b / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₉b is the **second sprint of Phase 5b-foundation**. Sprint
6h₉a (ADR-0098) shipped the `aelix-plugin.toml` v1 manifest contracts
(Pydantic models in `packages/aelix-agent-core/src/aelix_agent_core/
contracts/manifest.py`) and generated JSON Schemas, but the loader
never saw them: a directory containing only `aelix-plugin.toml` would
have been **silently ignored** by Sprint 5a's
`_resolve_extension_entries` which checked only
`pyproject.toml [tool.aelix]` and `__init__.py`.

Sprint 6h₉b closes that gap by wiring manifest detection into the
existing 4-tier `discover_and_load_extensions` infrastructure
(Sprint 5a, ADR-0028 / ADR-0041). The integration is
**augmentation, NOT replacement**:

- Legacy `pyproject.toml [tool.aelix] extensions = [...]` keeps working
  (package-internal entry-list use case, Aelix mirror of Pi
  `package.json "pi.extensions"`).
- Legacy `__init__.py` fallback keeps working (single-file extension,
  Aelix mirror of Pi `index.ts/index.js`).
- `aelix-plugin.toml` becomes the **NEW preferred** discovery path
  (full manifest with identity, capabilities, activation, contributes).
- When multiple are present, **`aelix-plugin.toml` wins** (it carries
  the most information).

Pi reference note: Pi has **NO manifest** — Pi extensions are
TypeScript `.ts` files in `~/.pi/agent/extensions/` discovered by file
scan. Aelix's manifest is wholly Aelix-additive per ADR-0096
§"Pi divergences"; Sprint 6h₉b adds Aelix-additive surface only — no
Pi-parity invariant is violated.

## Decision (Sprint 6h₉b deliverables 1-5 enumerated)

| # | Deliverable | Type | Closure |
|---|---|---|---|
| 1 | `Extension.manifest: PluginManifest \| None` field added to the dataclass | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` (Commit 1) |
| 2 | `_resolve_extension_entries` augmented to detect `aelix-plugin.toml` first; new `_load_manifest_from_dir` helper + `_ManifestEntry` internal carrier + `ExtensionManifestError` exception | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` (Commit 2) |
| 3 | `_factory_from_module` accepts `"module:callable"` colon form; `_resolve_factory` / `_invoke_factory` propagate manifest to `Extension`; API_LEVEL `min_level` gate (REJECT) + `level` warn (LOAD); license whitelist warn (Phase 5b warn-only) | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` (Commit 3) |
| 4 | 14 manifest-loader tests (`tests/extensions/test_manifest_v1_loader.py`) | Tests | (Commit 4) |
| 5 | This closure ADR | Docs | `docs/decisions/0099-sprint-6h9b-manifest-loader-integration.md` (Commit 4) |

Key behavioural notes:

- `aelix-plugin.toml` is checked **FIRST** in the resolver. The legacy
  `pyproject.toml [tool.aelix]` and `__init__.py` chains still resolve
  when no manifest exists.
- `module:callable` colon form is the manifest's `[plugin.entry] python`
  syntax (e.g., `"my_plugin.ext:default"`). The legacy bare-module form
  (e.g., `"my_plugin.ext"`) still resolves to top-level `setup`.
- API_LEVEL `min_level > AELIX_API_LEVEL` rejects the plugin via
  `ExtensionManifestError` → surfaced as `ExtensionLoadError`.
  `level > AELIX_API_LEVEL` (forward-compat) emits a warning and loads
  the plugin best-effort.
- License whitelist (`LICENSE_WHITELIST`, ADR-0096 §"SPDX license
  whitelist v1") is **warn-only in Phase 5b**. A non-whitelisted
  license emits a warning but does not block load. Phase 6 will gate
  strict via `--strict-licenses` (ADR-0096 §3.3.5).
- Capabilities / activation / contributes are **parsed and stored on
  `Extension.manifest` but NOT yet wired to runtime** — wiring lives
  in Sprint 6h₉c/d/e/f (see §"Deferred items").

## Aelix-additive divergences from Pi

| # | Divergence | Pi behavior | Aelix-additive behavior | Justification |
|---|---|---|---|---|
| 1 | `aelix-plugin.toml` detection | Pi has no manifest concept | Aelix `_resolve_extension_entries` checks manifest first | Sprint 6h₉a contracts (ADR-0096) — capabilities declaration, API_LEVEL versioning, declarative contributes |
| 2 | `module:callable` colon form | Pi uses TypeScript default exports (`export default ...`) | Aelix `_factory_from_module` parses colon form | TOML manifest needs explicit callable name; Python lacks a "default export" convention |
| 3 | API_LEVEL gate | Pi has no formal ABI versioning | Aelix rejects when `min_level > AELIX_API_LEVEL`; warns when `level > AELIX_API_LEVEL` | Neovim API_LEVEL pattern — plugin compat tracking across breaking changes |
| 4 | License whitelist warn | Pi accepts any license | Aelix warns on non-SPDX-whitelisted entries (Phase 5b warn-only) | Zed `extension.toml` precedent; Phase 6 will strict-gate via `--strict-licenses` |
| 5 | `ExtensionManifestError` exception class | Pi surfaces TS errors directly | Aelix has a typed manifest-failure exception | Cleaner per-plugin try/except routing; never aborts the wave |
| 6 | Manifest propagation to `Extension` | Pi `Extension` has no manifest field | Aelix `Extension.manifest: PluginManifest \| None` | Required for Sprint 6h₉c/d/e/f runtime consumers to read declared capabilities/activation/contributes |
| 7 | `_ManifestEntry` internal carrier | Pi loader passes raw paths only | Aelix internal carrier flows manifest + pkg_dir through `_resolve_factory` / `_invoke_factory` | Keeps the public `discover_and_load_extensions` signature unchanged while threading manifest metadata internally |

All divergences are net-additive — Pi behaviour is unchanged. A plugin
directory with no manifest (only `pyproject.toml [tool.aelix]` or
`__init__.py`) loads identically to Sprint 5a.

## Deferred items (Phase 5b/5c/6 carry-forward)

Per Sprint 6h₉b spec §1.4 verbatim:

| Item | Owner sprint | Reason |
|---|---|---|
| Tier 1 `ExtensionUIContext` 27-method runtime implementation | Sprint 6h₉c | Phase 5b-foundation #3 |
| Tier 2 descriptor renderer (TUI Rich Renderable mapping) | Sprint 6h₉d | Phase 5b-foundation #4 |
| Tier 4 MCP client + subprocess hooks | Sprint 6h₉e | Phase 5b-foundation #5 |
| `aelix-server` FastAPI HTTP+WS skeleton | Sprint 6h₉f | Phase 5b-foundation #6 |
| **Lazy-load enforcement** (activation events actually gating extension load until trigger fires) | Phase 5c or later | Larger architectural change; current eager-load behaviour preserved |
| **Capability enforcement** (refusing to inject adapters for undeclared capabilities) | Phase 6 | ADR-0096 §3.3.6 declaration vs enforcement split |
| **Strict license enforcement** (`--strict-licenses` flag) | Phase 6 | ADR-0096 §3.3.5 — Phase 6 default true |
| `pyproject.toml [tool.aelix]` deprecation | TBD | No deprecation in Sprint 6h₉b; both manifest forms coexist |
| Phase 6 capabilities for Web (`ui_web_trusted`) implementation | Phase 6 | Manifest accepts the flag now, but no consumer in Phase 5b |

## Pi citations (SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`)

- `packages/coding-agent/src/core/extensions/loader.ts:575-621` —
  `discoverAndLoadExtensions` (already mirrored by Sprint 5a;
  unchanged this sprint — manifest detection plugs into the existing
  per-directory hook).
- `packages/coding-agent/src/core/extensions/loader.ts:454-479` —
  `resolveExtensionEntries` (already mirrored; **augmented** this
  sprint with the `aelix-plugin.toml` priority-one branch).
- `packages/coding-agent/src/core/extensions/loader.ts:481-518` —
  `discoverExtensionsInDir` (already mirrored; the inner per-plugin
  try/except now also contains `ExtensionManifestError`).
- `packages/coding-agent/src/core/extensions/loader.ts:437` —
  per-plugin try/except containment (Pi parity: one bad extension
  never aborts the wave; manifest parse failures honor this invariant).
- `packages/coding-agent/docs/extensions.md` §"Auto-discovery" — base
  paths reference (Aelix translation: `cwd/.aelix/extensions/`,
  `~/.aelix/extensions/`).

External (non-Pi) references:

- Neovim API_LEVEL — https://neovim.io/doc/user/api/ (informed
  manifest API_LEVEL design per ADR-0096).
- Harlequin entry-points + per-plugin try/except —
  https://github.com/tconbeer/harlequin/blob/main/src/harlequin/plugins.py
  (Aelix `entry_points` group `aelix.extensions` retains this pattern).
- Zed `extension.toml` — SPDX license whitelist precedent (Phase 6
  strict-gating motivation).

## Reference companions

- ADR-0028 — original extension loader decision (Sprint 5a baseline).
- ADR-0041 — Sprint 5a discovery enhancement (4-tier directory scan +
  Aelix-additive entry_points).
- ADR-0094 — Aelix Extension Architecture (4-tier model — Sprint 6h₉b
  implements the manifest-driven discovery surface for Tier 1).
- ADR-0096 — Aelix Plugin Manifest v1 schema (THE input contract for
  this sprint).
- ADR-0098 — Sprint 6h₉a closure (contracts package shipped; Sprint
  6h₉b consumes it).

## Verification

- `uv run ruff check` — clean.
- `uv run pyright` — 8 baseline errors preserved (intentional fixtures
  in `scripts/pyright_spike.py`); zero new errors introduced.
- `uv run pytest` — 2417 baseline + 14 new manifest-loader tests =
  2431 passed + 1 skipped (the "1 skipped" is the pre-existing
  `pytest.skip` marker unrelated to this sprint).
- `python scripts/generate_contracts_schemas.py --check` — exit 0
  (Sprint 6h₉b touches no contracts package files; no schema drift).
- Smoke 1: `from aelix_coding_agent.extensions.api import Extension;
  Extension(name="x").manifest` returns `None` (legacy default
  preserved).
- Smoke 2: a plugin dir containing both `aelix-plugin.toml` and
  `pyproject.toml [tool.aelix]` loads with `extension.manifest is not
  None` (manifest path wins).
- Smoke 3: a manifest with `min_level = 99` fails to load with an
  `ExtensionLoadError` carrying `"API_LEVEL"` in the message; the
  per-plugin try/except contains the failure so other plugins in the
  same wave still load.
- Pi pin held at `734e08e` (no advance — Sprint 6h₉b imports no new
  Pi feature beyond the pinned SHA).

## Phase

Sprint 6h₉b / Phase 5b-foundation (shipped). Next sprint: 6h₉c —
ExtensionAPI Python surface (27-method `ctx.ui.*` Protocol; Sprint
6h₉c consumes `Extension.manifest.capabilities` to decide which UI
adapters to inject).
