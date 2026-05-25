# 0103. Sprint 6h₉f — aelix-server (FastAPI HTTP + WS Gateway)

Status: Accepted (Sprint 6h₉f / Phase 5b-foundation / W6 shipped — **Phase 5b-foundation COMPLETE**)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₉f is the **sixth and FINAL sprint of Phase 5b-foundation**. ADR-0097
(Sprint 6h₉a) documented the `aelix-server` contract and stated the package
skeleton "lands at Sprint 6h₉f". This sprint lands it.

aelix-server is the **server layer** of the small-kernel + RPC + multiple-frontend
architecture (ADR-0097): a FastAPI + uvicorn daemon that exposes the existing
JSONL RPC surface over WebSocket so the separate `aelix-web` repo (Phase 6) and
other network clients can drive an Aelix agent session with the same wire format
the TUI uses over stdio.

This is **Aelix-additive** — Pi has no server daemon. Reference = ADR-0097 +
Open WebUI / FastAPI self-hosting pattern + the FastAPI / Starlette / anyio /
uvicorn official idioms. Pi pin held; **zero Pi feature imported**.

## Decision

**Core insight:** the server is a **thin WebSocket transport adapter over the
existing `run_rpc_mode`** — NOT a reimplementation. `run_rpc_mode` exposes
`stdin: asyncio.StreamReader` and `stdout_write: Callable[[bytes], None]` as
injectable transport seams. The server feeds WebSocket frames into a
`StreamReader` and drains `stdout_write` bytes back to the socket. The JSONL RPC
wire format is byte-identical to the TUI's stdio transport — no translation layer.

Four atomic commits land `packages/aelix-server/`:

1. **Package skeleton + deps** — `pyproject.toml` (mirrors `aelix-coding-agent`:
   `aelix-ai` / `aelix-agent-core` / `aelix-coding-agent` workspace deps +
   `anyio>=4` + `fastapi>=0.115,<1` + `uvicorn[standard]>=0.30,<1`; console
   script `aelix-server = aelix_server.main:main_sync`). Root `pyproject.toml`
   gains the `aelix-server` workspace source + `httpx>=0.27` dev dep (TestClient
   backend). `uv.lock` regenerated.
2. **FastAPI app + HTTP endpoints** — `app.py` (`create_app` factory + modern
   `lifespan=` async context manager + route registration + module-level
   `app = create_app()`), `schemas.py` (`GET /schemas/{name}`), `main.py`
   (`uvicorn.run` entry). `config.py` (`ServerConfig` + `from_env`).
3. **WS /rpc transport bridge** — `rpc_ws.py`: single-flight guard, per-connection
   `AgentHarness` + `AgentSessionRuntime`, `anyio.create_task_group()` with a
   WS→`StreamReader` pump, a single-writer queue drainer, and `run_rpc_mode`.
4. **Tests** — `tests/server/` (17 tests, `fastapi.testclient.TestClient`).

## Endpoint surface (Phase 5b minimal)

| Endpoint | Status | Notes |
|---|---|---|
| `WS /rpc` | **shipped** | full-duplex JSONL RPC over WebSocket; commands + responses + agent events muxed on one socket (exactly as TUI stdio). Single-flight (one active connection). |
| `GET /healthz` | **shipped** | `{"status": "ok"}` liveness probe. |
| `GET /schemas/{name}` | **shipped** | serves `docs/contracts/{name}.schema.json` (regex allowlist + resolve-prefix guard; 404 when the dir is absent). |
| `WS /events` | **deferred** | see divergence #1. |

**Auth/DB:** none (single-user dev mode). **Bind:** `127.0.0.1` default. **Config:**
env vars (`AELIX_SERVER_*`).

## Architecture (the WS bridge)

```
WS client ──┐                            ┌── agent events (harness.subscribe)
            │  text frame (1 JSON/msg)   │
            ▼                            ▼
   pump_ws_to_reader            run_rpc_mode(stdout_write=put_nowait)
   feed_data(text+"\n")  ──►  asyncio.StreamReader (stdin)  ──► dispatch
   feed_eof() on disconnect          │
                                      ▼
                            asyncio.Queue ──► drain_queue_to_ws (SOLE sender)
                                              await ws.send_text()  ──► WS client
```

- **Single writer per WebSocket**: only `drain_queue_to_ws` calls `send_*`
  (Starlette WebSocket is not safe for concurrent send). `stdout_write` is the
  synchronous sink `run_rpc_mode` calls from the agent-turn task; it only does
  `queue.put_nowait` — never awaits.
- **anyio task group** holds the pump + drainer + `run_rpc_mode` on one task tree.
  This is deliberate forward-proofing for the future MCP-integration sprint (see
  *MCP cross-task hazard*).
- **`feed_eof()` in the pump's `finally`** guarantees `run_rpc_mode`'s stdin loop
  ends on disconnect → clean teardown (`run_rpc_mode` disposes the runtime in its
  own `finally`).
- **Per-connection `AgentHarness` + `AgentSessionRuntime`** — no shared mutable
  state; constructed exactly as `cli/entry.py` does for stdio RPC.
- **`install_signal_handlers=False`** — a per-connection `run_rpc_mode` must not
  install process SIGTERM/SIGHUP handlers.

## Intentional divergences from ADR-0097 / the reference

1. **`WS /events` DEFERRED.** ADR-0097 lists it in the Phase-5b minimal surface,
   but in the per-connection-isolation model a standalone event stream has
   nothing to observe (each `/rpc` connection owns its own harness; no shared
   daemon session). A useful `/events` needs a session registry + cross-connection
   event fan-out — the multi-session/multi-observer infrastructure ADR-0097 itself
   defers to Phase 6. **Source-verified**: `run_rpc_mode` already muxes session
   events onto the same JSONL sink as command responses (`rpc/rpc_mode.py`
   event subscription → `stdout_write`), so `/rpc` carries events full-duplex and
   `/events` is redundant for the single-user case. Deferred to the multi-session
   sprint / Phase 6.
2. **Config via env vars only — no `aelix-server.toml`.** ADR-0097 says
   "`aelix-server.toml` OR env vars"; env-only is conformant. TOML file parsing
   deferred.
3. **Single-flight guard on `/rpc`.** Only one active `/rpc` connection at a time;
   a second is rejected with `close(code=1013)` before `accept()`. Not in
   ADR-0097's surface. Justified: (a) matches the single-user dev model;
   (b) avoids `run_rpc_mode`'s process-global `redirect_stdout(sys.stderr)`
   nesting across concurrent connections. Lift in the multi-session / Phase-6 sprint.
4. **Env-var set delta.** ADR-0097 named `AELIX_SERVER_BIND` / `_PORT` /
   `_AUTH_MODE`. This sprint implements `_BIND` / `_PORT` plus `_CWD` / `_MODEL` /
   `_PROVIDER` / `_SCHEMAS_DIR`, and does NOT implement `_AUTH_MODE` (no auth in
   Phase 5b).
5. **`/schemas` 404 when `schemas_dir` is absent** — graceful degradation (the
   daemon may run from an installed wheel without `docs/contracts/`).
6. **MCP cross-task hazard (forward-proofing note, NOT a divergence).** ADR-0101's
   anyio cancel-scope same-task constraint is NOT triggered: `McpClientManager` is
   not yet wired into the harness lifecycle, so no MCP connections are created here.
   The WS handler keeps the pump / drainer / `run_rpc_mode` on one
   `anyio.create_task_group()` so a future per-connection MCP resource will open
   AND close on the same task. **Binding note for the MCP-integration sprint.**
7. **README index row intentionally NOT added** — matches the 6h₉a-e precedent
   (ADR-0098-0102 added none; the index has been stale since ADR-0094). Documented,
   not silently skipped.

## Phase 6 follow-ups (deferred)

| Item | Reason |
|---|---|
| `WS /events` + session registry + cross-connection fan-out | multi-observer (divergence #1) |
| Origin / `Sec-WebSocket` check on `/rpc` (CSWSH defense) | needed once bind moves off localhost; safe at `127.0.0.1` |
| `AELIX_SERVER_BIND=0.0.0.0` exposure warning | unauthenticated daemon must warn before public bind |
| Auth (`AELIX_SERVER_AUTH_MODE`) / DB / multi-tenant | ADR-0097 §"Self-hosting server model" |
| `aelix-server.toml` config file | env-only in v1 |
| hypercorn / HTTP-2 | uvicorn for dev (ADR-0097) |
| SSE `/events` alternative, observability (Prometheus/OTel) | ADR-0097 |
| Abnormal-disconnect-mid-stream e2e test (needs a stubbed model) | test-coverage debt; bridge teardown verified by inspection |

## References

### Reference map (NOT Pi — server layer is Aelix-additive)

| Reference | Use |
|---|---|
| ADR-0097 (multi-frontend architecture) | the aelix-server contract: location, stack, endpoint surface, single-user dev mode, cross-repo schema fetch |
| ADR-0056 (Aelix JSONL RPC) / ADR-0057 (RPC envelope) | the JSONL frame wire format reused verbatim over WebSocket |
| `aelix_coding_agent.rpc.run_rpc_mode` (`stdin` / `stdout_write` seams) | the transport adapter target — reused byte-unchanged |
| FastAPI / Starlette WebSockets + lifespan | `accept()` + single-writer-drains-queue idiom; modern `lifespan=` (not deprecated `@app.on_event`) |
| anyio task groups + cancellation | structured concurrency; cancel-scope same-task affinity (the MCP forward-proofing constraint) |
| uvicorn (programmatic `uvicorn.run`) | dev server launch |
| Open WebUI architecture | self-hosting FastAPI daemon precedent (ADR-0097) |

### ADR cross-references

- **ADR-0097** — multi-frontend architecture (binding aelix-server contract).
- **ADR-0056 / ADR-0057** — JSONL RPC protocol + envelope (reused transport).
- **ADR-0095 / ADR-0096** — UI descriptor protocol + manifest v1 (the
  `docs/contracts/*.schema.json` served by `/schemas`).
- **ADR-0101** — MCP client (the anyio cross-task hazard forward-proofed here).
- **ADR-0102** — subprocess hooks (preceding Phase 5b-foundation sprint).

Pi pin `734e08edf82ff315bc3d96472a6ebfa69a1d8016` held — no Pi source consulted
or imported. The reused `rpc/` / `harness/` / `mcp/` packages are byte-unchanged.

## Verification

| Gate | Result |
|---|---|
| `ruff check` | clean |
| `uv run pyright` | 8 baseline preserved (zero new; fastapi/uvicorn ship type info) |
| `uv run pytest` | 2541 passed, 1 skipped (was 2524 + 17 new server tests) |
| `python scripts/generate_contracts_schemas.py --check` | exit 0 (contracts unchanged) |
| Pi-parity non-regression | `rpc/` / `harness/` / `mcp/` byte-unchanged (W5 `git diff`) |
| boot smoke | `aelix-server` boots; `curl /healthz` → `{"status":"ok"}`; `/schemas/manifest` → 200 |
| WS bridge teardown | no orphaned tasks; `feed_eof` always runs; single-writer invariant held (W4) |
| `anyio` dependency | declared explicitly in `aelix-server` deps (W5 fold-in §F) |

## Phase

Sprint 6h₉f / Phase 5b-foundation — **shipped. Phase 5b-foundation is now
COMPLETE** (6h₉a manifest contracts → 6h₉b loader → 6h₉c ExtensionUIContext →
6h₉d MCP client → 6h₉e subprocess hooks → 6h₉f aelix-server).

**Next: Phase 5c-tui (Sprint 6h₁₀a-d)** — the concrete prompt-toolkit + Rich +
Aelix widget-layer TUI (ADR-0088 amend), which also closes the Pi-parity
carry-forwards deferred through Phase 5a (ADR-0087 P-380 `_resourceLoader.reload`
+ `_buildRuntime`; ADR-0089 P-401 `--append-system-prompt @file`; P-449
`has_bindings` 4-field UI check).
