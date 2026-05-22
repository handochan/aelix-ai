# 0098. Sprint 6h₉a — Phase 5b-foundation Lock

Status: Accepted (Sprint 6h₉a / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₉a is the **first sprint of Phase 5b-foundation**, the phase
that lays the architectural lockdown for the subsequent TUI sprints
(6h₁₀a-d) and Phase 6 Web sprints. It captured the conclusions of a
4-agent research wave (Pi-agent-dashboard / peer coding-agents / TUI
frameworks / editor-IDE extension models) plus direct Pi `pi-tui`
source survey, and locked five architectural decisions plus the
contract-layer scaffolding that those decisions consume.

The closure ADR follows the ADR-0093 convention for sprint-shipping
markers: enumerate deliverables, summarize Aelix-additive divergences,
cite Pi references at the pinned SHA, and record verification evidence.

## Decision (Sprint 6h₉a deliverables 1-8 enumerated)

| # | Deliverable | Type | Closure |
|---|---|---|---|
| 1 | ADR-0088 amend (Textual → prompt-toolkit + Rich + Aelix widget layer; Status: Proposed → Accepted) | Docs | `docs/decisions/0088-*.md` amended in Commit 6 |
| 2 | ADR-0094 NEW — Aelix Extension Architecture (4-tier model) | Docs | `docs/decisions/0094-aelix-extension-architecture-4-tier.md` (Commit 4) |
| 3 | ADR-0095 NEW — UI Descriptor Protocol (Tier 2 cross-surface wire + 8-slot taxonomy v1) | Docs | `docs/decisions/0095-ui-descriptor-protocol.md` (Commit 4) |
| 4 | ADR-0096 NEW — Aelix Plugin Manifest v1 (`aelix-plugin.toml`) | Docs | `docs/decisions/0096-manifest-v1-schema.md` (Commit 5) |
| 5 | ADR-0097 NEW — Multi-Frontend Architecture (RPC Gateway + Separate Web Repo + Self-Hosting Server Model) | Docs | `docs/decisions/0097-multi-frontend-architecture.md` (Commit 5) |
| 6 | Contracts Python package (Pydantic v2 models for manifest, descriptor, slots, primitives, api_level) | Code | `packages/aelix-agent-core/src/aelix_agent_core/contracts/` + `pydantic>=2.7,<3` dep (Commit 1) |
| 7 | Schema generation script + initial generated JSON Schemas + contracts docs README | Code+Docs | `scripts/generate_contracts_schemas.py`, `docs/contracts/*.schema.json` × 4, `docs/contracts/README.md` (Commit 2) |
| 8 | Contract validation tests | Tests | `tests/contracts/test_contracts_schema.py` + `__init__.py` — 33 tests (Commit 3) |

Plus this closure ADR (Commit 6) — `docs/decisions/0098-sprint-6h9a-phase-5b-foundation-lock.md`.

The 10 locked decisions (D1-D10) from the spec §1.3 are honored across
the deliverables:

- **D1** (TUI + Web 양쪽 1차) → ADR-0097
- **D2** (prompt-toolkit + Rich + Aelix widget layer) → ADR-0088 amend
- **D3** (별도 레포 `aelix-web`) → ADR-0097 §"Separate repo aelix-web"
- **D4** (셀프호스팅 server daemon — Open WebUI 모델) → ADR-0097 §"Self-hosting server model"
- **D5** (4-tier extension model) → ADR-0094
- **D6** (Pi parity = `ctx.ui.*` 25-method surface + tool renderer co-location) → ADR-0094 §"Pi reference"
- **D7** (Manifest `aelix-plugin.toml` + API_LEVEL + SPDX whitelist + activation events) → ADR-0096
- **D8** (Descriptor protocol forward-design) → ADR-0095
- **D9** (aelix-server skeleton — Phase 5b foundation, full implementation Sprint 6h₉f) → ADR-0097 §"aelix-server"
- **D10** (Sequencing: 5b foundation → 5c TUI → 6 Web) → ADR-0097 §"Architecture overview"

## Aelix-additive divergences summary

| ADR | Divergence | Pi behavior | Aelix-additive behavior | Justification |
|---|---|---|---|---|
| 0088 amend | TUI stack choice | Custom pi-tui library (Mario Zechner, ~9000 LOC) | prompt-toolkit + Rich + thin Aelix widget layer | Python ecosystem stability (10+ year libs), no self-built lib maintenance, aider precedent |
| 0094 | Tier 2 descriptor tier | None in core | Cross-surface JSON wire format | Phase 6 Web needs language-neutral wire; Pi-dashboard retrofitted and paid the cost |
| 0094 | Tier 3 rich React tier | N/A (Pi is TUI-only in core) | Phase 6 Web rich components | D1 dual-primary audience |
| 0094 | Tier 4 elevated to formal tier | MCP via extension, hooks via subprocess (implicit) | Formal universal extension surface | Universal pattern in coding-agent ecosystem (Claude Code, gemini-cli) |
| 0095 | 8-slot taxonomy | Pi-dashboard has 22 | 8 (subset, descriptor-only Phase 5b) | TUI-first Phase 5b; Web slots deferred to Phase 6 expansion |
| 0095 | `ui:list-modules` sync probe | None in Pi (Pi-dashboard pattern) | Adopted as cross-surface contribution discovery | Decouples descriptor declaration from extension lifecycle event coupling |
| 0096 | Manifest required | Auto-discovers `.ts` files | `aelix-plugin.toml` required | API_LEVEL versioning, capabilities declaration (Phase 6 enforcement), declarative contributes, marketplace metadata |
| 0096 | API_LEVEL | None (semver only) | `AELIX_API_LEVEL` separate from semver | Neovim API_LEVEL pattern; plugin compat tracking |
| 0096 | SPDX whitelist | Any license accepted | v1 whitelist (MIT/Apache-2.0/BSD/MPL/ISC/Unlicense) | Zed extension.toml pattern; marketplace trust baseline (Phase 6) |
| 0096 | Activation events | All extensions load eagerly | Lazy activation triggers (VS Code pattern, no `*`) | Faster startup as ecosystem grows |
| 0097 | Multi-frontend architecture | TUI only in core; Pi-dashboard separate repo | TUI + Web both first-class (D1) | User-defined dual-primary audience |
| 0097 | Separate `aelix-web` repo | Pi-dashboard separate repo precedent | Same pattern | Python+TS toolchain split, deployment artifact separation |
| 0097 | Self-hosting server | Short-lived CLI per session | Phase 6 long-running daemon (Open WebUI pattern) | Multi-user / marketplace / shared session model |

## Pi citations (SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`)

- `packages/tui/src/index.ts` — custom TUI library exports (Mario
  Zechner; ADR-0088 amend §"Why the PRIMARY recommendation was
  reversed" point 1).
- `packages/tui/package.json` — pi-tui dependency manifest (only
  `get-east-asian-width` + `marked`; ADR-0088 amend).
- `packages/coding-agent/docs/tui.md` — Component interface,
  Focusable, CURSOR_MARKER (ADR-0088 amend).
- `packages/coding-agent/docs/extensions.md` — auto-discovery paths
  (`~/.pi/agent/extensions/`, `.pi/extensions/`), `ctx.ui.custom`
  reference (ADR-0094, ADR-0096).
- `packages/coding-agent/src/extensions/types.ts:1-300` —
  `ExtensionUIContext` 25-method surface (ADR-0094 §"Pi reference").
- `packages/coding-agent/src/extensions/types.ts:300-700` —
  `ExtensionContext` (ADR-0094).
- `packages/coding-agent/src/extensions/types.ts:700+` —
  `ToolDefinition` + `ToolRenderContext` (ADR-0094).

External (non-Pi) citations:

- Pi-dashboard `packages/shared/src/dashboard-plugin/slot-types.ts`
  (22-slot reference) — Pi-dashboard repo
  `BlackBeltTechnology/pi-agent-dashboard@develop`.
- Pi-dashboard `docs/architecture.md:180-290` (`ui:list-modules` probe).
- Pi-dashboard `docs/architecture.md:221-227` (descriptor schemas).
- Pi-dashboard `packages/shared/src/dashboard-plugin/slot-registry.ts`
  (loader cycle soft-fail precedent).
- Pi-dashboard issue #32 — maintainer retrofit cost admission.

## Reference companions

- ADR-0088 (amended Sprint 6h₉a) — TUI library decision.
- ADR-0093 — Sprint 6h₇c closure (template format reference for this
  ADR).
- ADR-0094 — Aelix Extension Architecture (4-tier model).
- ADR-0095 — UI Descriptor Protocol.
- ADR-0096 — Aelix Plugin Manifest v1.
- ADR-0097 — Multi-Frontend Architecture.
- ADR-0056 — Aelix JSONL RPC (aelix-server reuses this transport).
- ADR-0083 — Runtime callback Pi parity.
- ADR-0085 — Phase 4 closure (HTML export emitter precursor).
- ADR-0089 — Phase 5a-i + 5a-ii closure (stderr diagnostic pointing to
  ADR-0088).

## Verification

- `uv run ruff check` — clean.
- `uv run pyright` — 8 baseline errors preserved (intentional fixtures
  in `scripts/pyright_spike.py`); no new errors introduced.
- `uv run pytest` — 2381 baseline + 33 new contract tests = 2414
  collected; 2413 passed + 1 skipped (1 pre-existing flake in
  `tests/rpc/test_rpc_client_shutdown.py::test_stop_escalates_to_sigkill_when_sigterm_ignored`
  passes in isolation).
- `python scripts/generate_contracts_schemas.py --check` — exit 0
  (no drift).
- `python scripts/generate_contracts_schemas.py` (re-run after
  generation) — produces no diff (idempotent).
- Smoke 1: `from aelix_agent_core.contracts import AELIX_API_LEVEL;
  print(AELIX_API_LEVEL)` → prints `1`.
- Smoke 2: `DescriptorEnvelope(kind="footer-segment",
  namespace="ns", id="x", payload=FooterSegmentPayload(text="hello"))`
  constructs and `model_dump(mode="json")` round-trips through
  `model_validate`.
- Smoke 3: `parse_manifest_toml(VALID_MANIFEST_TOML)` parses a
  representative manifest correctly, including TOML
  `[plugin.api]` / `[plugin.entry]` table flattening.
- RPC roster STAYS CLOSED at **29 supported / 0 deferred / 29 total**
  (no RPC surface changes this sprint).
- Pi pin held at `734e08e` (no advance — Sprint 6h₉a imports no new
  Pi feature beyond the pinned SHA).

## Phase

Sprint 6h₉a / Phase 5b-foundation (shipped). Subsequent sprints in
this phase: 6h₉b (extension loader), 6h₉c (ExtensionAPI Python
surface), 6h₉d (descriptor renderer), 6h₉e (MCP + hooks formal tier),
6h₉f (aelix-server skeleton).
