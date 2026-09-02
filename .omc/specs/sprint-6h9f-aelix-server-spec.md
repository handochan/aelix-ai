# Sprint 6h₉f — aelix-server (FastAPI HTTP + WS Gateway) — W1 Binding Spec

**Workflow:** ADR-0032 W0→W6. **Closure ADR:** ADR-0103.
**Phase:** 5b-foundation (FINAL sprint). **Tier:** server layer (multi-frontend, ADR-0097).
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` — held, **no Pi feature imported**.

---

## 0. Binding principle & nature

Top-level binding principle: **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**
aelix-server is **Aelix-additive** — Pi has no server daemon. Reference = ADR-0097 (multi-frontend
architecture) + Open WebUI / FastAPI self-hosting pattern. Pi pin held; zero Pi feature imported.

**Core insight (W0):** the server is a **thin WebSocket transport adapter over the existing
`run_rpc_mode`**. The JSONL RPC wire format is identical to the TUI's stdio transport — no translation
layer, no reimplementation. `run_rpc_mode(harness, *, stdin: StreamReader, stdout_write: Callable[[bytes],
None], install_signal_handlers, ...)` exposes `stdin`/`stdout_write` as injectable transport seams. The
server feeds WS frames into a `StreamReader` and drains `stdout_write` bytes to the WS.

**MCP note (W0):** `McpClientManager` is NOT yet wired into the harness lifecycle. Therefore the ADR-0101
anyio cancel-scope cross-task constraint is **NOT triggered** by this sprint. We still structure the WS
handler so any future per-connection resource is opened+closed on one task (anyio task group), and we
document the hazard for the MCP-integration sprint.

---

## 1. Goal (one sentence)

Land the `packages/aelix-server/` package: a FastAPI + uvicorn daemon exposing `WS /rpc` (full-duplex
JSONL RPC over WebSocket, reusing `run_rpc_mode` verbatim), `GET /healthz`, and `GET /schemas/{name}`
(serving `docs/contracts/*.schema.json` for cross-repo aelix-web consumption) — single-user dev mode,
no auth, no DB, localhost-bind default.

## 2. Scope decisions (binding) + deferrals (document in ADR-0103)

### Implemented this sprint
- **`WS /rpc`** — per-connection `AgentHarness` + `AgentSessionRuntime`, full-duplex via `run_rpc_mode`.
  Commands (client→server) + responses + agent events are all muxed on the one socket, exactly as the TUI
  stdio transport does. **Single-flight guard**: only ONE active `/rpc` connection at a time (see §4.3);
  a second concurrent connection is rejected with a close. This both matches the single-user dev model and
  avoids `run_rpc_mode`'s process-global `redirect_stdout(sys.stderr)` nesting across concurrent
  connections.
- **`GET /healthz`** — `{"status": "ok"}` liveness probe.
- **`GET /schemas/{name}`** — serve `docs/contracts/{name}.schema.json` with a strict allowlist regex +
  resolve-prefix guard (path-traversal safe).
- **Config via env vars** — `AELIX_SERVER_BIND` (default `127.0.0.1`), `AELIX_SERVER_PORT` (default
  `8765`), `AELIX_SERVER_CWD` (default `.`), optional `AELIX_SERVER_MODEL` / `AELIX_SERVER_PROVIDER`.
- **`aelix-server` console-script** entry point → `uvicorn.run`.

### Deferred (document the rationale in ADR-0103)
- **`WS /events`** — ADR-0097 lists it in the "Phase 5b minimal surface", but a standalone one-way event
  stream has **nothing to observe** in the per-connection-isolation model (each `/rpc` connection owns its
  own harness; there is no shared daemon session). A useful `/events` requires a session registry +
  cross-connection event fan-out — exactly the multi-session/multi-observer infrastructure ADR-0097 itself
  defers to Phase 6 ("Multi-user (Phase 6)"). Since `/rpc` **already** delivers agent events full-duplex,
  `/events` is redundant for the single-user case. **Deferred to the multi-session sprint / Phase 6.**
  This is an intentional, documented deviation from ADR-0097's listed surface — record it as ADR-0103
  divergence #1.
- **`aelix-server.toml`** config file — env-vars only in v1 (ADR-0097 allows "`aelix-server.toml` OR env
  vars"). TOML file parsing deferred; documented.
- **Auth / DB / multi-tenant / observability** — Phase 6 (ADR-0097 §"Self-hosting server model").
- **SSE `/events` alternative** — Phase 6 (ADR-0097).
- **hypercorn / HTTP-2** — Phase 6 (ADR-0097); uvicorn for dev.

## 3. Files

**New package `packages/aelix-server/`:**
- `pyproject.toml` — mirror `aelix-coding-agent/pyproject.toml` (see §6).
- `src/aelix_server/__init__.py` — package marker + `__all__` (e.g. `create_app`, `ServerConfig`).
- `src/aelix_server/config.py` — `ServerConfig` (frozen dataclass) + `ServerConfig.from_env()`.
- `src/aelix_server/app.py` — `create_app(config: ServerConfig) -> FastAPI` factory + `lifespan` +
  route registration (`/healthz`, `/schemas/{name}`, `/rpc`).
- `src/aelix_server/rpc_ws.py` — the `WS /rpc` transport bridge (`run_rpc_mode` integration).
- `src/aelix_server/schemas.py` — schema-dir resolution + `get_schema` handler + path-traversal guard.
- `src/aelix_server/main.py` — `main()` / `main_sync()` → `uvicorn.run`.

**New tests `tests/server/`:**
- `tests/server/__init__.py`
- `tests/server/test_server.py` — mirror `tests/mcp_client/` / `tests/subprocess_hooks/` structure.

**Modified:**
- root `pyproject.toml` — add `aelix-server` to `[tool.uv.sources]` (workspace) + add `httpx` to the
  `[dependency-groups] dev` list (TestClient needs httpx). Do NOT add fastapi/uvicorn to root deps —
  they live in the `aelix-server` package deps.
- `uv.lock` — regenerated by `uv sync` (transitive closure: fastapi, starlette, uvicorn, websockets,
  httptools, uvloop, h11, click, etc.). Commit the lock.
- `docs/decisions/0103-*.md` — new closure ADR.

**MUST NOT touch:** `rpc/` package (reused verbatim — `run_rpc_mode`, `_jsonl`, `rpc_types`), `harness/`,
the `mcp/` package, contracts/schemas (schema `--check` must stay exit 0), `scripts/pyright_spike.py`
(8 baseline errors preserved).

## 4. Design

### 4.1 `ServerConfig` (`config.py`)
```python
@dataclass(frozen=True)
class ServerConfig:
    bind: str = "127.0.0.1"
    port: int = 8765
    cwd: str = "."
    model: str = ""
    provider: str = ""
    schemas_dir: Path = <repo>/docs/contracts   # resolved; see note

    @classmethod
    def from_env(cls) -> ServerConfig: ...   # reads AELIX_SERVER_* with the defaults above
```
- `schemas_dir` default: locate `docs/contracts/` relative to the repo. Since the daemon may run from an
  installed wheel, prefer an env override `AELIX_SERVER_SCHEMAS_DIR`; default by walking up from `cwd`
  to find a `docs/contracts` dir, falling back to the packaged path. Keep it simple: env override OR
  `Path(config.cwd, "docs/contracts")` resolved; if absent, `/schemas` returns 404 (documented).
- `port` parsed from env with `int(...)`; invalid → raise a clear `ValueError` at startup.

### 4.2 `create_app` + lifespan (`app.py`)
```python
def create_app(config: ServerConfig | None = None) -> FastAPI:
    config = config or ServerConfig.from_env()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        app.state.rpc_active = False    # single-flight flag for /rpc
        app.state.rpc_lock = asyncio.Lock()
        yield
    app = FastAPI(title="aelix-server", lifespan=lifespan)
    # register routes (healthz, schemas, rpc ws)
    return app

app = create_app()    # module-level for `uvicorn.run("aelix_server.app:app", ...)`
```
- `GET /healthz` → `JSONResponse({"status": "ok"})`.
- Use the modern `lifespan=` (NOT deprecated `@app.on_event`).

### 4.3 `WS /rpc` transport bridge (`rpc_ws.py`) — the core

```python
async def rpc_websocket(websocket: WebSocket) -> None:
    config: ServerConfig = websocket.app.state.config
    # --- single-flight guard ---
    if websocket.app.state.rpc_active:
        await websocket.close(code=1013)   # 1013 = "try again later"
        return
    websocket.app.state.rpc_active = True
    await websocket.accept()
    try:
        # --- per-connection harness + runtime (mirror cli/entry.py:277-302) ---
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs)
        session = await repo.create(...)           # default session at config.cwd
        model = Model(id=config.model, provider=config.provider)
        async def _harness_factory(s): return AgentHarness(AgentHarnessOptions(model=model, session=s, cwd=config.cwd))
        harness = await _harness_factory(session)
        runtime = await create_agent_session_runtime(harness, _harness_factory, repo=repo, fs=fs)

        # --- transport bridge ---
        reader = asyncio.StreamReader()
        out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        def stdout_write(b: bytes) -> None:        # sync sink — run_rpc_mode calls this
            out_queue.put_nowait(b)

        async def pump_ws_to_reader() -> None:
            try:
                while True:
                    text = await websocket.receive_text()
                    reader.feed_data(text.encode() + b"\n")   # newline-frame each WS message
            except WebSocketDisconnect:
                pass
            finally:
                reader.feed_eof()                  # signals run_rpc_mode stdin EOF → clean exit

        async def drain_queue_to_ws() -> None:
            while True:
                item = await out_queue.get()
                if item is None:
                    return
                try:
                    await websocket.send_text(item.decode())
                except (WebSocketDisconnect, RuntimeError):
                    return

        async with anyio.create_task_group() as tg:
            tg.start_soon(pump_ws_to_reader)
            tg.start_soon(drain_queue_to_ws)
            # run_rpc_mode owns command dispatch + event emission; returns at stdin EOF (disconnect)
            await run_rpc_mode(
                harness, runtime_host=runtime, harness_factory=_harness_factory,
                repo=repo, fs=fs, stdin=reader, stdout_write=stdout_write,
                install_signal_handlers=False,     # server manages lifecycle, NOT signals
            )
            out_queue.put_nowait(None)             # stop the writer
            tg.cancel_scope.cancel()               # stop the ws-pump if still blocked on receive
    finally:
        websocket.app.state.rpc_active = False
```

Key correctness points (the W4/W5 focus):
- `run_rpc_mode` reads `reader` via its internal pump (`JsonlLineReader` splits on `\n`), so each WS
  message MUST be newline-terminated when fed (`text.encode() + b"\n"`). The client sends one JSON
  object per WS text message (no embedded newlines); if a client sends a frame that already ends in
  `\n`, the extra `\n` produces an empty line which `JsonlLineReader` skips — verify that empty lines are
  tolerated (they are: `json.loads("")` would raise, so confirm `run_rpc_mode`'s `_on_line` guards empty
  lines; if not, strip a trailing `\n` from `text` before re-adding). **Executor: verify `_on_line`
  empty-line handling and adapt.**
- `install_signal_handlers=False` — the daemon must not let a per-connection `run_rpc_mode` install
  process SIGTERM/SIGHUP handlers.
- `stdout_write` is synchronous (`run_rpc_mode` calls it from the agent-turn task). Never `await` in it —
  `put_nowait` onto an unbounded queue, drained by the single writer task (one sender per WebSocket).
- Per-connection harness/runtime; `run_rpc_mode` disposes `runtime_host` in its own teardown.
- anyio task group keeps reader/writer/run_rpc_mode on the connection's task tree (forward-proofs the
  future MCP same-task requirement).

### 4.4 `GET /schemas/{name}` (`schemas.py`)
```python
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
async def get_schema(name: str, request: Request):
    if not _SAFE_NAME.match(name):
        raise HTTPException(400, "invalid schema name")
    base = request.app.state.config.schemas_dir.resolve()
    path = (base / f"{name}.schema.json").resolve()
    if not str(path).startswith(str(base) + os.sep) and path != base:
        raise HTTPException(400, "invalid schema name")
    if not path.is_file():
        raise HTTPException(404, "schema not found")
    return FileResponse(path, media_type="application/json")
```
- Files in `docs/contracts/` are named `descriptor-envelope.schema.json`, `manifest.schema.json`,
  `primitives.schema.json`, `slot-taxonomy.schema.json`. So `name` = `manifest` →
  `manifest.schema.json`. (Note the hyphen in `descriptor-envelope`/`slot-taxonomy` → the regex MUST
  allow `-`.) Confirm the exact on-disk filenames and the `{name}` → filename mapping; document it.

### 4.5 `main.py` entry
```python
def main() -> None:
    config = ServerConfig.from_env()
    uvicorn.run("aelix_server.app:app", host=config.bind, port=config.port, log_level="info")
def main_sync() -> None:   # console-script target (match the aelix entry naming convention)
    main()
```
- `[project.scripts] aelix-server = "aelix_server.main:main_sync"`.

## 5. Dependencies

`packages/aelix-server/pyproject.toml` `[project] dependencies`:
- `aelix-ai`, `aelix-agent-core`, `aelix-coding-agent` (workspace)
- `fastapi>=0.115,<1`
- `uvicorn[standard]>=0.30,<1`

Root `pyproject.toml` `[dependency-groups] dev` += `httpx>=0.27` (TestClient backend).
Run `uv sync` to regenerate `uv.lock`; commit the lock in the deps commit.

## 6. Package skeleton (mirror aelix-coding-agent)
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "aelix-server"
version = "0.1.0"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
dependencies = ["aelix-ai", "aelix-agent-core", "aelix-coding-agent", "fastapi>=0.115,<1", "uvicorn[standard]>=0.30,<1"]
[project.scripts]
aelix-server = "aelix_server.main:main_sync"
[tool.uv.sources]
aelix-ai = { workspace = true }
aelix-agent-core = { workspace = true }
aelix-coding-agent = { workspace = true }
[tool.hatch.build.targets.wheel]
packages = ["src/aelix_server"]
```
Root `[tool.uv.sources]` += `aelix-server = { workspace = true }` (workspace auto-discovers
`packages/*`, but add the source entry for consistency with the other three).

## 7. Tests (`tests/server/test_server.py`)

Use `fastapi.testclient.TestClient` (sync `def test_*` — coexists with `asyncio_mode="auto"`).
Construct an app with a temp `ServerConfig` (point `schemas_dir` at the real `docs/contracts/` or a
`tmp_path` fixture with a sample schema). Mirror `tests/subprocess_hooks/` packaging.

1. **`/healthz`** → 200, `{"status": "ok"}`.
2. **`/schemas/{name}` valid** → 200, `application/json`, body is the schema JSON (use a real name like
   `manifest`, or a tmp schema). Assert it parses as JSON.
3. **`/schemas/{name}` 404** → unknown name → 404.
4. **`/schemas/{name}` traversal** → `..%2F..%2Fetc%2Fpasswd` style / `name` with `/` or `.` → 400 (the
   regex rejects). Test a few: `"../secret"`, `"a/b"`, `"a.b"` → 400 (note: FastAPI path param may not
   route a `/`; test the regex-reject names that DO route).
5. **`WS /rpc` round-trip** → `with client.websocket_connect("/rpc") as ws:` send a `get_state` command
   line (`ws.send_text('{"type":"get_state","id":"1"}')`), `ws.receive_text()` → parse → assert it's a
   `type=="response"` frame with `command=="get_state"` and `id=="1"` and `success==True`. This exercises
   the WHOLE bridge: WS→reader→run_rpc_mode→dispatch→stdout_write→queue→WS.
6. **`WS /rpc` single-flight** → open one `/rpc` ws (keep open), attempt a second `websocket_connect` →
   the second is closed (catch the close / assert it cannot complete a round-trip). (TestClient WS
   semantics: a `close(code=1013)` before `accept()` surfaces as a `WebSocketDisconnect` on the client
   `receive`. Assert accordingly.)
7. **`create_app` / `ServerConfig.from_env`** → unit: env vars parsed (monkeypatch `AELIX_SERVER_PORT`
   etc.), defaults correct, invalid port raises.
8. (If feasible) **`WS /rpc` event delivery** → send a command that triggers an agent event and assert an
   event frame (non-`response` type) arrives. If wiring a real model is too heavy for a unit test, cover
   event muxing via the get_state response path (#5) and document that full event-stream e2e needs a
   stubbed model (defer to a follow-up if it requires network/model). Prefer at least asserting the
   bridge forwards a server-initiated frame.

## 8. Gates (W3 / pre-commit)
- `ruff check` → clean (the new package src is linted).
- `uv run pyright` → **exactly 8 baseline errors** (the `scripts/pyright_spike.py` fixtures). FastAPI &
  uvicorn ship type info; if their stubs introduce new errors, constrain with targeted `cast`/annotations
  — do NOT add blanket ignores. Confirm `[tool.pyright] include` already covers `packages/*/src` (it does).
- `uv run pytest 2>&1 | tail -2` → all pass (current 2524 + new server tests), ≤ existing skips.
- `python scripts/generate_contracts_schemas.py --check` → `exit=0` (no contract/schema change).
- Manual QA: `AELIX_SERVER_PORT=8765 uv run aelix-server` boots, `curl localhost:8765/healthz` → ok
  (executor performs a brief boot smoke-check or documents why deferred to TestClient coverage).

Test/lint command (project memory):
`uv run pytest 2>&1 | tail -2 && echo "===SCHEMA===" && python scripts/generate_contracts_schemas.py --check 2>&1; echo "exit=$?"`

## 9. Commit plan (W6) — `git add <specific-paths>` only, HEREDOC msgs

Trailer EVERY commit: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
Do NOT push. Do NOT `--no-verify`. Do NOT stage `.omc/project-memory.json` / `.tmp`.

- **§A** `feat(server): aelix-server package skeleton + deps (Sprint 6h₉f §A)` — `pyproject.toml`,
  `src/aelix_server/__init__.py`, `config.py`, root `pyproject.toml` dev/sources edits, `uv.lock`.
- **§B** `feat(server): FastAPI app + healthz + schemas endpoint (Sprint 6h₉f §B)` — `app.py`,
  `schemas.py`, `main.py`.
- **§C** `feat(server): WS /rpc transport bridge over run_rpc_mode (Sprint 6h₉f §C)` — `rpc_ws.py` +
  route wiring.
- **§D** `test(server): healthz + schemas + WS /rpc round-trip + single-flight (Sprint 6h₉f §D)` —
  `tests/server/`.
- **§E** `docs: ADR-0103 aelix-server closure (Sprint 6h₉f §E)` — ADR-0103.
- **fold-ins** as W4/W5 require.

(README index row: NOT added — matching the 6h₉a-e precedent where ADR-0098-0102 added no README row;
the index has been stale since ADR-0094. Documented, not silently skipped.)

## 10. W4 / W5 emphasis (user directive: "코드 리뷰 확실히 + 레퍼런스 비교 확실히")

- **W4 code-reviewer (opus):** WS bridge correctness — no orphaned tasks on disconnect; the
  single-writer-per-WS invariant (only `drain_queue_to_ws` calls `send_*`); `feed_eof` always runs on
  disconnect so `run_rpc_mode` exits cleanly; `rpc_active` flag reset in `finally` even on exceptions;
  path-traversal guard on `/schemas` is unbypassable; `install_signal_handlers=False`; pyright 8 baseline.
- **W5 critic (opus):** **reference comparison** — (1) against ADR-0097's specified surface: confirm
  `/rpc` + `/healthz` + `/schemas` match the contract and that the `/events` deferral is justified +
  documented as a numbered divergence; (2) against the FastAPI/anyio reference: the WS handler structure
  (accept → task group → single writer) matches the official idiom; lifespan (not `on_event`); uvicorn
  programmatic launch correct; (3) confirm NO Pi behavior imported and the `rpc/`+`harness/` packages are
  byte-unchanged (`git diff`); (4) confirm the MCP cross-task hazard is documented for the future
  integration sprint even though not triggered now.
- Any deviation from ADR-0097 (notably `/events` deferral, env-only config) MUST be a numbered,
  justified divergence in ADR-0103.
