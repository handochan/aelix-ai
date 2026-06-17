# 0097. Multi-Frontend Architecture (RPC Gateway + Separate Web Repo + Self-Hosting Server Model)

Status: Accepted (Sprint 6h₉a / Phase 5b-foundation / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₉a W0 locked the following user-clarified decisions:

- **D1**: Aelix has TWO equal first-class user audiences (TUI =
  terminal/SSH/dev, Web = visual/desktop/marketplace), analogous to
  Claude Code CLI vs Claude Code Desktop. Neither is downgraded to
  "secondary".
- **D3**: Web UI lives in a **separate repository** (`aelix-web`).
  The current `aelix-ai` repo stays Python-only.
- **D4**: Web UI is a **self-hosting server daemon** (Open WebUI
  pattern: FastAPI daemon + Docker / Helm), NOT a Tauri/Electron
  desktop wrapper, NOT a textual-serve terminal-shaped web app.
- **D9**: **aelix-server** (FastAPI HTTP + WS gateway) is born inside
  Phase 5b-foundation. Sprint 6h₉a documents the contract only; the
  package skeleton lands at Sprint 6h₉f. Phase 5b is single-user dev
  mode (no auth, no DB); Phase 6 multi-tenant adds auth + DB + scaling.

Web cannot share the TUI's Python process — different language,
different deployment topology, different update cadence. This forces a
small-kernel + RPC + multiple-frontends architecture.

## Architecture overview (small kernel + RPC + multiple frontends)

> **Amendment (2026-06-16, ADR-0133).** The TUI shipped **inside**
> `aelix-coding-agent` as `aelix_coding_agent/tui/` (+ `[tui]` extra),
> not as a separate `apps/aelix-tui`. pi keeps its chat UI inside
> `packages/coding-agent` likewise; pi's `packages/tui` is a generic
> toolkit whose role Aelix fills with prompt-toolkit + Rich (ADR-0088).
> Read the `apps/aelix-tui` line below as `aelix-coding-agent/tui/`.

```
Aelix Runtime (Python — small kernel)
  ├─ aelix-ai
  ├─ aelix-agent-core (+ contracts/ via Sprint 6h₉a)
  ├─ aelix-coding-agent
  │    └─ tui/ (Phase 5c — prompt-toolkit + Rich + Aelix widget layer; [tui] extra)
  └─ aelix-server (Phase 5b — Sprint 6h₉f) — FastAPI HTTP + WS gateway
     └─ JSONL RPC (ADR-0056) adapter + REST API + WebSocket event stream

# (was: "apps/aelix-tui (Python — Phase 5c)" — see ADR-0133; the TUI is
#  aelix_coding_agent/tui/, not a standalone package)

┌── separate repo: aelix-web (Phase 6) ───────────────────┐
│  apps/aelix-web (TS/React/Svelte — Phase 6 stack decision) │
│  packages/rpc-client (TS SDK)                              │
│  packages/plugin-runtime (Tier 3 React plugin infra)       │
│  packages/server-extensions (auth/DB/marketplace)          │
│  docker/ + helm/                                           │
└────────────────────────────────────────────────────────────┘
```

The kernel is intentionally small: agent loop + hook bus + extension
runner + settings + JSONL RPC. UI surfaces (TUI, Web, MCP, hooks) are
peripherals that connect via the same RPC/event surface.

## aelix-server (Phase 5b carry-forward)

Sprint 6h₉a documents the package skeleton; the package itself lands at
Sprint 6h₉f.

- **Location**: `packages/aelix-server/` (new package in the
  `aelix-ai` workspace).
- **Stack**: FastAPI + uvicorn (Phase 5b dev); hypercorn recommended
  for Phase 6 multi-tenant deployment (asyncio HTTP/2 + WebSocket
  multiplexing).
- **Endpoints** (Phase 5b minimal surface):
  - `WS /rpc` — JSONL RPC frame stream (1 frame = 1 line, ADR-0056
    reuse). Same wire format the TUI uses for its in-process JSONL
    transport — no translation layer.
  - `WS /events` — event subscription (agent events + descriptor
    invalidations). Server-Sent-Events alternative may be added at
    Phase 6 for one-way clients.
  - `GET /healthz` — liveness probe.
  - `GET /schemas/{name}` — serve JSON Schemas from `docs/contracts/`
    (for cross-repo aelix-web build/runtime fetch).
- **Auth**: **single-user dev mode** in Phase 5b. No auth header check;
  default bind is localhost-only. Phase 6 adds an auth decision
  (OAuth / email-password / SAML / SSO — deferred).
- **DB**: NONE in Phase 5b (in-memory state). Phase 6 adds the DB
  decision (PostgreSQL / SQLite / hybrid — deferred).
- **Configuration**: `aelix-server.toml` or env vars
  (`AELIX_SERVER_BIND`, `AELIX_SERVER_PORT`, `AELIX_SERVER_AUTH_MODE`).
- **Sprint 6h₉a deliverable**: ADR documentation only. No package
  skeleton this sprint.

## Separate repo aelix-web (Phase 6)

Phase 6 entry creates a new repository for the Web frontend:

- **Repo URL**: `github.com/handochan/aelix-web` (created at Phase 6
  entry; not yet existing).
- **Stack decisions (deferred to Phase 6 entry)**:
  - React + Vite vs SvelteKit vs Next.js
  - Sandbox decision (trusted-only / iframe / WASM for Tier 3)
- **Deployment**: Docker image (Dockerfile + multi-stage build) +
  docker-compose.yml + Helm chart. The deployment artifact is owned by
  the `aelix-web` repo; `aelix-ai` only exposes the runtime daemon.
- **License**: separate from Aelix core (allows Phase 6 enterprise-tier
  optionality without entangling the core).
- **Cross-repo contract**: JSON Schemas from
  `aelix-ai/docs/contracts/*.schema.json` are published per Aelix
  release; `aelix-web` fetches them via `GET /schemas/{name}` at
  runtime OR copies them into its build at release time. SemVer +
  `AELIX_API_LEVEL` coordinate the two repos.

## Cross-repo contract (binding)

- **Aelix core is the single source of truth** for descriptor
  envelope, slot taxonomy, manifest schema, RPC API.
- **JSON Schemas live in** `aelix-ai/docs/contracts/*.schema.json`
  (Sprint 6h₉a deliverable, ADR-0095).
- **Python Pydantic models live in**
  `aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/contracts/`
  (Sprint 6h₉a deliverable, ADR-0095 / ADR-0096).
- **`aelix-web` fetches schemas** at build time (pinned to an Aelix
  release tag) OR via runtime `GET /schemas/{name}` (Phase 6 dynamic
  schema loading for hot-reload).
- **Coordinated SemVer**: a breaking change to the contract layer
  bumps the major version of BOTH repos in lockstep, with `AELIX_API_LEVEL`
  incrementing in the same release.

## Self-hosting server model implications

The shift from short-lived CLI per-session to a long-running daemon is
the central Phase 6 architectural shift:

- **Daemon lifetime**: aelix-server runs as a process that outlives
  individual sessions. State persists across user connections.
- **Multi-user (Phase 6)**: user accounts, sessions per user,
  per-user permissions, history persistence.
- **Deployment targets**: homelab, VPS, corporate internal server,
  SaaS-style hosted service.
- **Compute model options** (Phase 6 decision):
  - Single aelix-server daemon + N web clients connecting to it
    (single-tenant per process).
  - M aelix-server instances behind a load balancer (multi-tenant
    scale).
- **Logging**: structured JSON to stdout for container log capture
  (Twelve-Factor pattern).
- **Observability** (Phase 6 deferred): Prometheus metrics,
  OpenTelemetry tracing.

## Explicitly rejected approaches

Recording the explicitly considered + rejected alternatives so a future
contributor doesn't re-litigate:

- **Tauri / Electron desktop app wrapping local CLI** — single-user,
  not aligned with the sharing / marketplace audience (D1 — Web is a
  first-class audience including hosted scenarios). Rejected.
- **textual-serve (same Textual code → browser)** — would yield a
  terminal-shaped web UI. Cannot do charts (Plotly / ECharts), file
  previews, image galleries, rich marketplace UI. The 4-6 week saving
  textual-serve offered is real ONLY in a scenario where a
  terminal-shaped browser app is acceptable, which the user vision
  explicitly rejects. Also see ADR-0088 amend §"Why the PRIMARY
  recommendation was reversed" point 3. Rejected.
- **Monorepo aelix-web package** (TypeScript inside the Python repo) —
  uv-managed Python + pnpm/npm toolchain dual-management cost; release
  cadences differ. Rejected.
- **Single-frontend assumption** — explicitly contradicts D1 (TUI +
  Web 양쪽 1차 시민). Rejected.

## Phase 6 deferred decisions

The following are intentionally NOT decided in Sprint 6h₉a; each is
recorded here so Phase 6 entry has the full decision agenda:

- Web frontend stack (React + Vite / SvelteKit / Next.js / something
  else)
- Tier 3 sandbox level (trusted-only / iframe / WASM)
- Auth mechanism (OAuth / email-password / SAML / SSO)
- DB choice (PostgreSQL / SQLite / hybrid)
- Marketplace operating model (self-hosted registry / git-based
  distribution / hybrid)
- Desktop wrapper (Tauri / Electron) optionality

ADR-0094 §"Trust model and process boundary" notes the conservative
default position (T3 trusted-only at Phase 6 start).

## References

- ADR-0056 — Aelix JSONL RPC. aelix-server reuses this transport
  unchanged.
- ADR-0083 — Runtime callback Pi parity. The callback model bridges
  TUI and Web through the same kernel surface.
- ADR-0085 — Phase 4.16 visual fidelity + Phase 4 closure. The Rich
  HTML export emitter is a precursor for cross-surface rendering.
- ADR-0094 (Sprint 6h₉a) — Aelix Extension Architecture (4-tier
  model). Multi-frontend renders tier surfaces.
- ADR-0095 (Sprint 6h₉a) — UI Descriptor Protocol. The cross-repo
  wire format.
- ADR-0096 (Sprint 6h₉a) — Aelix Plugin Manifest v1.
  `capabilities.ui_web_trusted` is the Phase 6 web flag.
- Pi-dashboard `docs/architecture.md` — Bridge architecture
  (Pi-dashboard's separate-repo precedent and `pi-bridge` extension
  reference).
- Pi-dashboard `packages/extension/` — bridge extension reference.
- Open WebUI architecture — https://github.com/open-webui/open-webui
  (FastAPI + SvelteKit reference).
- LobeChat architecture — https://github.com/lobehub/lobe-chat (Next.js
  + plugin marketplace reference).
- Aider — https://github.com/Aider-AI/aider (prompt-toolkit + Rich
  precedent for the TUI side; informs but does not directly affect this
  ADR).
