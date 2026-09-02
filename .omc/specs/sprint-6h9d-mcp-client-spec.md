# Sprint 6h₉d — MCP Client (Tier 4a)

Status: Binding (W1 spec; do not modify after W1 closure)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — Sprint metadata

| Field | Value |
|---|---|
| Sprint id | `6h₉d` |
| Phase | 5b-foundation, sprint 4 of ~7 (Tier 4 split into 6h₉d MCP + 6h₉e hooks) |
| Workflow | ADR-0032 W0-W6 |
| Scope class | Code (MCP client + AgentTool adapter) + tests + 1 ADR. NEW `mcp` dependency. |
| Spec author | Main agent |
| Predecessors | 6h₉a (contracts incl. McpServerContrib), 6h₉b (manifest loader), 6h₉c (ExtensionUIContext) |
| Owning ADR closure | ADR-0101 (NEW — Sprint 6h₉d closure) |
| Reference basis | **NOT Pi** — Pi has no MCP client in core. Aelix-additive per ADR-0094 §"Tier 4". Reference = MCP spec (modelcontextprotocol.io) + official `mcp` Python SDK 1.27.1 + Claude Code MCP client config. |

---

## §1 — Background

### §1.1 — Tier 4 is Aelix-additive (W0 finding)

W0 confirmed: `earendil-works/pi@734e08e` has **zero MCP files** and **zero subprocess-hook files** in `coding-agent/src`. Pi extensions are in-process TypeScript; MCP and hooks (if any) are added via extension, not core. ADR-0094 §"Tier 4" documented this: *"Pi supports MCP via extension; Aelix elevates to a formal tier matching the Claude Code / gemini-cli universal pattern."*

Therefore Sprint 6h₉d's reference comparison is against:
- **MCP spec** (modelcontextprotocol.io) — protocol semantics
- **Official `mcp` Python SDK 1.27.1** — client API surface
- **Claude Code MCP client** (code.claude.com/docs/en/mcp) — `mcpServers` config shape, reconnect backoff

NOT against Pi source. The Pi pin is held at `734e08e` (no advance; no Pi feature imported).

### §1.2 — Existing Aelix assets

- `McpServerContrib` (Sprint 6h₉a, `contracts/manifest.py:152-158`): `name` / `transport` (Literal["stdio","http","sse"]) / `command` / `url` / `env`. Declaration shape already locked; this sprint consumes it.
- `Tool` (`aelix_ai/tools.py:84`): `name` / `description` / `parameters` (JSON-Schema dict) / `execute` (ToolExecute | None). MCP `Tool.input_schema` (JSON Schema) maps **zero-transform** to `Tool.parameters`.
- `AgentTool` (`aelix_agent_core/types.py:42`): extends `Tool` with `execution_mode`. MCP tools register as `AgentTool` instances.
- `ToolResult` / `ToolContent` (`aelix_ai/tools.py`): MCP `CallToolResult.content` (list[ContentBlock]) maps to `ToolResult`.

### §1.3 — MCP SDK facts (from W0 research; `mcp` 1.27.1)

- Package: `mcp` (PyPI), Python `>=3.10` (Aelix is 3.12 ✓). `pip install mcp` (client-only) pulls anyio, pydantic v2, httpx, jsonschema.
- Client API:
  - `from mcp import ClientSession`
  - `from mcp.client.stdio import stdio_client, StdioServerParameters`
  - `from mcp.client.streamable_http import streamable_http_client` (HTTP — recommended)
  - `from mcp.client.sse import sse_client` (SSE — deprecated, legacy only)
- `StdioServerParameters(command, args=[], env=None, cwd=None, ...)`. `env=None` → safe selective inheritance via `get_default_environment()`. **`env={}` breaks subprocess** (no PATH). Use `env=None` or `{**os.environ, **overrides}`.
- `stdio_client(params)` → async context manager yielding `(read, write)`.
- `ClientSession(read, write)` → async context manager. `await session.initialize()` handshake. `await session.list_tools()` → `.tools: list[Tool]`. `await session.call_tool(name, arguments, read_timeout_seconds)` → `CallToolResult`.
- `types.Tool`: `name`, `title`, `description`, `input_schema` (snake_case in Python; wire is `inputSchema`), `output_schema`, `annotations`.
- `CallToolResult`: `content` (list[ContentBlock]), `structured_content` (dict|None), `is_error` (bool). **`is_error=True` is NOT a Python exception** — must check explicitly.
- ContentBlock union: `TextContent` (type="text", text), `ImageContent` (type="image", data b64, mime_type), `AudioContent`, `ResourceLink`, `EmbeddedResource`.
- Multi-server: one `AsyncExitStack` per connection; keep dict of connections keyed by name.
- Reconnect: SDK does NOT auto-reconnect. stdio = local process (reactive crash detection). HTTP/SSE = manual backoff (Claude Code: 5 attempts, 1s start, doubling).

### §1.4 — Out-of-scope (defer)

| Item | Owner | Reason |
|---|---|---|
| MCP resources (`list_resources`/`read_resource`) | Phase 6 or later | Tools first; resources are secondary surface |
| MCP prompts (`list_prompts`/`get_prompt`) | Phase 6 or later | Same |
| MCP sampling (server → client LLM calls) | Phase 6+ | Advanced; rare |
| subprocess hooks (Tier 4b) | Sprint 6h₉e | split per user decision |
| aelix-server | Sprint 6h₉f | Phase 5b-foundation #6 |
| MCP server auth (OAuth flow) | Phase 6 | header/token passthrough only in 6h₉d |
| Capability enforcement (manifest `mcp_invoke`) | Phase 6 | declaration-only Phase 5b (ADR-0096) |

---

## §2 — Scope

Five deliverables, five atomic commits (§4):

| # | Deliverable | Type | Touches |
|---|---|---|---|
| 1 | `mcp>=1.27,<2` dependency added to aelix-coding-agent | Build | `packages/aelix-coding-agent/pyproject.toml`, `uv.lock` |
| 2 | `mcp_client.py` NEW — `McpServerConnection` (single-server lifecycle, transport selection, AsyncExitStack) + `McpConnectionError` | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/mcp/__init__.py` + `mcp/client.py` (NEW) |
| 3 | MCP Tool → AgentTool adapter + `McpClientManager` (multi-server dict, connect-all/shutdown-all) | Code | `packages/aelix-coding-agent/src/aelix_coding_agent/mcp/adapter.py` + `mcp/manager.py` (NEW) |
| 4 | `McpServerContrib` config → connection params + reconnect-on-failure (HTTP/SSE backoff) | Code | `mcp/manager.py` + `mcp/client.py` |
| 5 | Tests (in-memory + stdio echo mock server) + ADR-0101 | Tests + Docs | `tests/mcp/test_mcp_client.py` (NEW), `docs/decisions/0101-sprint-6h9d-mcp-client.md` (NEW) |

**Module layout note**: NEW `mcp/` subpackage inside `aelix_coding_agent`. Avoid name shadowing the `mcp` SDK package — the SDK is imported as `import mcp` / `from mcp import ...` from site-packages; the local `aelix_coding_agent.mcp` subpackage is namespaced under `aelix_coding_agent` so no collision. **Verify** at commit 2: `python -c "import mcp; from aelix_coding_agent.mcp.client import McpServerConnection"` resolves both correctly (absolute imports, no relative `mcp` shadow).

---

## §3 — Per-deliverable specifications

### §3.1 — `mcp` dependency

Add to `packages/aelix-coding-agent/pyproject.toml` `[project] dependencies`:

```toml
"mcp>=1.27,<2",
```

Preserve alphabetical sort if present. Run `uv sync` (background) to update `uv.lock`. The lockfile delta (mcp + anyio + httpx + jsonschema transitive) is a necessary consequence — stage `uv.lock` alongside (W4 MINOR-3 lesson from 6h₉b: list uv.lock explicitly).

**Import-shadow guard**: Because the local subpackage is `aelix_coding_agent.mcp`, all SDK imports MUST be absolute (`from mcp import ClientSession`, `from mcp.client.stdio import ...`). Python resolves `mcp` to site-packages (top-level) and `aelix_coding_agent.mcp` to the local subpackage — no collision as long as no code does a bare relative `from . import mcp` at the `aelix_coding_agent` package level. Add a smoke test (§3.5 test #1).

### §3.2 — `mcp/client.py` — McpServerConnection

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/mcp/client.py` (NEW) + `mcp/__init__.py` (NEW).

```python
"""MCP client connection — single-server lifecycle (Sprint 6h₉d, ADR-0101).

Aelix-additive Tier 4a (ADR-0094) — Pi has no MCP client in core.
Reference: official ``mcp`` Python SDK 1.27.1 + MCP spec + Claude Code
``mcpServers`` config. Pi pin held at 734e08e (no Pi feature imported).

This module owns one MCP server connection: transport selection by
``McpServerContrib.transport`` (stdio / http / sse), the ``initialize``
handshake, ``list_tools`` / ``call_tool`` delegation, and clean
``AsyncExitStack`` teardown.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
import mcp.types as mcp_types

from aelix_agent_core.contracts import McpServerContrib


class McpConnectionError(Exception):
    """Raised on MCP server connect / handshake / transport failure."""


class McpServerConnection:
    """One MCP server connection (stdio / http / sse).

    Lifecycle:
        conn = McpServerConnection(contrib)
        await conn.connect()           # spawn/connect + initialize handshake
        tools = await conn.list_tools()
        result = await conn.call_tool(name, args)
        await conn.disconnect()        # AsyncExitStack unwind (subprocess kill)

    Or as an async context manager:
        async with McpServerConnection(contrib) as conn:
            ...
    """

    def __init__(self, contrib: McpServerContrib) -> None:
        self._contrib = contrib
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._server_info: mcp_types.Implementation | None = None
        self._protocol_version: str | int | None = None

    @property
    def name(self) -> str:
        return self._contrib.name

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def server_info(self) -> mcp_types.Implementation | None:
        return self._server_info

    async def connect(self) -> None:
        """Open the transport, create the session, run initialize().

        Raises:
            McpConnectionError: on spawn/connect/handshake failure
                (config validation, transport error, or initialize error).
        """
        if self._session is not None:
            return  # idempotent — already connected
        try:
            read, write = await self._open_transport()
            session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            init = await session.initialize()
            self._session = session
            self._server_info = init.server_info
            self._protocol_version = init.protocol_version
        except Exception as exc:  # noqa: BLE001 — wrap all into McpConnectionError
            await self._exit_stack.aclose()
            self._session = None
            raise McpConnectionError(
                f"MCP server {self._contrib.name!r} ({self._contrib.transport}) "
                f"connect failed: {exc}"
            ) from exc

    async def _open_transport(self):
        """Select + open the transport per McpServerContrib.transport."""
        t = self._contrib.transport
        if t == "stdio":
            if not self._contrib.command:
                raise McpConnectionError(
                    f"MCP server {self._contrib.name!r}: transport=stdio "
                    "requires [contributes.mcp_servers].command"
                )
            params = StdioServerParameters(
                command=self._contrib.command,
                args=[],  # Sprint 6h₉d: McpServerContrib has no args field yet;
                          # see §3.4 — args is a deferred McpServerContrib v2 field.
                env=(
                    {**os.environ, **self._contrib.env}
                    if self._contrib.env
                    else None  # None → SDK get_default_environment() safe inherit
                ),
            )
            return await self._exit_stack.enter_async_context(stdio_client(params))
        if t == "http":
            if not self._contrib.url:
                raise McpConnectionError(
                    f"MCP server {self._contrib.name!r}: transport=http requires url"
                )
            return await self._exit_stack.enter_async_context(
                streamable_http_client(url=self._contrib.url)
            )
        if t == "sse":
            if not self._contrib.url:
                raise McpConnectionError(
                    f"MCP server {self._contrib.name!r}: transport=sse requires url"
                )
            # SSE is deprecated (MCP spec) — supported for legacy servers only.
            return await self._exit_stack.enter_async_context(
                sse_client(url=self._contrib.url)
            )
        raise McpConnectionError(
            f"MCP server {self._contrib.name!r}: unknown transport {t!r}"
        )

    async def list_tools(self) -> list[mcp_types.Tool]:
        if self._session is None:
            raise McpConnectionError(f"{self._contrib.name!r} not connected")
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        read_timeout_seconds: float | None = None,
    ) -> mcp_types.CallToolResult:
        if self._session is None:
            raise McpConnectionError(f"{self._contrib.name!r} not connected")
        return await self._session.call_tool(
            name=name,
            arguments=arguments,
            read_timeout_seconds=read_timeout_seconds,
        )

    async def disconnect(self) -> None:
        await self._exit_stack.aclose()
        self._session = None
        self._server_info = None
        self._protocol_version = None

    async def __aenter__(self) -> McpServerConnection:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()
```

`mcp/__init__.py` re-exports: `McpServerConnection`, `McpConnectionError`, `McpClientManager`, `mcp_tools_to_agent_tools`.

### §3.3 — `mcp/adapter.py` — MCP Tool → AgentTool

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/mcp/adapter.py` (NEW).

```python
"""MCP Tool → Aelix AgentTool adapter (Sprint 6h₉d, ADR-0101).

MCP ``Tool.input_schema`` (JSON Schema) maps zero-transform to Aelix
``Tool.parameters``. ``call_tool`` is wrapped in a ``Tool.execute``
closure. ``CallToolResult.content`` blocks map to Aelix ``ToolResult``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import mcp.types as mcp_types

from aelix_agent_core.types import AgentTool
from aelix_ai.tools import ToolResult  # exact import path verified at W2

if TYPE_CHECKING:
    from aelix_coding_agent.mcp.client import McpServerConnection


def _content_blocks_to_tool_result(
    result: mcp_types.CallToolResult,
) -> ToolResult:
    """Map CallToolResult content blocks → Aelix ToolResult.

    - TextContent → text content
    - ImageContent → image content (base64 + mime_type)
    - is_error=True → ToolResult with is_error flag (MCP domain errors are
      NOT Python exceptions — gotcha #5 from W0 research)
    Other block types (audio / resource_link / embedded_resource) →
    text fallback with a typed marker for Phase 6 richer handling.
    """
    ...


def mcp_tool_to_agent_tool(
    conn: "McpServerConnection",
    tool: mcp_types.Tool,
    *,
    name_prefix: str | None = None,
) -> AgentTool:
    """Wrap one MCP tool as an Aelix AgentTool.

    Args:
        conn: the live MCP connection that backs ``call_tool``.
        tool: the MCP tool descriptor (name, description, input_schema).
        name_prefix: optional namespace prefix to avoid collisions across
            servers (e.g. ``"<server>__<tool>"``). When None, the bare
            MCP tool name is used.

    The returned AgentTool's ``execute`` closure calls
    ``conn.call_tool(tool.name, args)`` and maps the result. The MCP
    ``input_schema`` is passed verbatim as ``parameters`` (zero-transform).
    """
    qualified = f"{name_prefix}__{tool.name}" if name_prefix else tool.name

    async def _execute(args: dict[str, Any], *_a: object, **_k: object) -> ToolResult:
        result = await conn.call_tool(tool.name, args)
        return _content_blocks_to_tool_result(result)

    return AgentTool(
        name=qualified,
        description=tool.description or "",
        parameters=tool.input_schema,  # zero-transform JSON Schema passthrough
        execute=_execute,
    )


def mcp_tools_to_agent_tools(
    conn: "McpServerConnection",
    tools: list[mcp_types.Tool],
    *,
    name_prefix: str | None = None,
) -> list[AgentTool]:
    return [mcp_tool_to_agent_tool(conn, t, name_prefix=name_prefix) for t in tools]
```

**W2 NOTE**: verify the exact `ToolResult` constructor shape + `Tool.execute` callable signature (`ToolExecute`) against `aelix_ai/tools.py` before finalizing the closure signature. The `_execute` signature MUST match `ToolExecute`. Adapt `_content_blocks_to_tool_result` to the real `ToolResult`/`ToolContent` dataclass fields.

### §3.4 — `mcp/manager.py` — McpClientManager + reconnect

**Location**: `packages/aelix-coding-agent/src/aelix_coding_agent/mcp/manager.py` (NEW).

```python
"""MCP multi-server manager (Sprint 6h₉d, ADR-0101).

Owns a dict of McpServerConnection keyed by server name. Connects all
declared servers, aggregates their tools as AgentTools, and tears down
cleanly. HTTP/SSE reconnect uses Claude-Code-style exponential backoff
(5 attempts, 1s start, doubling); stdio crash is reactive (no
auto-reconnect — local process).
"""
```

Required API:

```python
class McpClientManager:
    def __init__(self, contribs: list[McpServerContrib]) -> None: ...

    async def connect_all(self) -> list[McpConnectionError]:
        """Connect every declared server. Returns per-server errors
        (one bad server never aborts the others — Harlequin pattern)."""

    async def collect_agent_tools(self) -> list[AgentTool]:
        """list_tools() across all connected servers, namespaced by
        ``<server-name>__<tool-name>`` to avoid cross-server collisions."""

    async def call_tool_with_retry(
        self, server: str, tool: str, args: dict[str, Any],
        *, max_attempts: int = 3,
    ) -> mcp_types.CallToolResult:
        """For HTTP/SSE servers: reconnect-on-failure with exponential
        backoff. For stdio: single attempt (local process — reactive)."""

    async def disconnect_all(self) -> None:
        """Tear down every connection (AsyncExitStack unwind per server)."""

    @property
    def connections(self) -> dict[str, McpServerConnection]: ...
```

Reconnect backoff (HTTP/SSE only): attempt N sleeps `min(1.0 * 2**N, 32.0)` seconds before retry. stdio servers raise immediately on broken pipe (no reconnect — the subprocess is gone; caller must `connect()` a fresh connection).

**McpServerContrib v2 deferred fields** (note in ADR-0101): the current `McpServerContrib` (6h₉a) lacks `args` (stdio CLI args) and `headers` (HTTP auth). Sprint 6h₉d uses `command` with no args and no custom headers. A follow-up sprint extends `McpServerContrib` with `args: list[str]` + `headers: dict[str,str]` (manifest schema v1.1 — minor, non-breaking per ADR-0095 versioning). Document as deferred; do NOT modify the 6h₉a contract in this sprint (would require regenerating JSON Schemas + bumping the contracts package — out of scope).

### §3.5 — Tests

**Location**: `tests/mcp/test_mcp_client.py` (NEW) + `tests/mcp/__init__.py`.

Test strategy: use the `mcp` SDK's in-memory transport OR a minimal stdio echo server fixture. The SDK ships `mcp.shared.memory.create_connected_server_and_client_session` for in-memory testing (verify at W2 — exact helper name). If unavailable, write a tiny stdio echo MCP server as a test fixture (`tests/mcp/_echo_server.py`) using FastMCP.

Minimum coverage (~18 tests):

1. `test_mcp_sdk_importable` — `import mcp` + `from aelix_coding_agent.mcp.client import McpServerConnection` (import-shadow guard)
2. `test_stdio_connect_initialize` — connect to echo server, assert `conn.connected` + `server_info`
3. `test_list_tools` — echo server exposes ≥1 tool; assert returned
4. `test_call_tool_text_result` — call tool, assert TextContent mapped to ToolResult
5. `test_call_tool_is_error` — tool returns is_error=True → ToolResult.is_error (NOT exception)
6. `test_disconnect_idempotent` — double disconnect no-op
7. `test_connect_idempotent` — double connect no-op
8. `test_async_context_manager` — `async with McpServerConnection(...)` connects + disconnects
9. `test_stdio_missing_command_raises` — transport=stdio, command=None → McpConnectionError
10. `test_http_missing_url_raises` — transport=http, url=None → McpConnectionError
11. `test_unknown_transport_raises` — (construct McpServerContrib with valid transport; test the internal guard via a forged value or skip if Pydantic blocks it)
12. `test_mcp_tool_to_agent_tool_schema_passthrough` — AgentTool.parameters IS tool.input_schema (zero-transform)
13. `test_mcp_tool_namespace_prefix` — name_prefix → `<prefix>__<tool>`
14. `test_agent_tool_execute_calls_call_tool` — AgentTool.execute invokes conn.call_tool
15. `test_manager_connect_all_partial_failure` — one bad server, others still connect; errors returned
16. `test_manager_collect_agent_tools_namespaced` — tools from 2 servers namespaced distinctly
17. `test_manager_disconnect_all` — all connections torn down
18. `test_env_none_default_inherit` — McpServerContrib.env empty → StdioServerParameters env=None (assert via spy/inspection)

Use `pytest.mark.asyncio` (asyncio mode already configured per existing tests). Mock/echo server lifecycle in fixtures with proper teardown (no zombie subprocesses — gotcha #2).

### §3.6 — ADR-0101

**Location**: `docs/decisions/0101-sprint-6h9d-mcp-client.md` (NEW).

Front-matter per ADR-0093 template (Status: Accepted / Date / Pi pin / binding principle).

Required sections:
1. `## Context` — Tier 4a MCP client. Aelix-additive (Pi has no MCP core). Reference = MCP spec + `mcp` SDK 1.27.1 + Claude Code config.
2. `## Decision` — 5 deliverables. `mcp` SDK dependency. McpServerConnection + adapter + manager.
3. `## Aelix-additive characterization` — NOT a Pi port. Document the reference basis (MCP spec / SDK / Claude Code). State explicitly: Pi pin held, no Pi feature imported.
4. `## Tool mapping` — MCP `input_schema` → AgentTool `parameters` zero-transform; `call_tool` → execute closure; ContentBlock → ToolResult.
5. `## Transport support` — stdio (primary) / streamable HTTP (recommended) / SSE (deprecated, legacy only).
6. `## Reconnect policy` — stdio reactive (no auto-reconnect); HTTP/SSE exponential backoff (Claude Code pattern, 5 attempts, 1s→32s cap).
7. `## Deferred items` — McpServerContrib v2 (`args`, `headers`); resources; prompts; sampling; auth OAuth; capability enforcement (Phase 6). Per §1.4.
8. `## McpServerContrib v2 note` — current contract lacks `args`/`headers`; Sprint 6h₉d works within the limitation; follow-up minor schema bump adds them (NON-breaking per ADR-0095 versioning).
9. `## References` — MCP spec, `mcp` SDK 1.27.1, Claude Code MCP docs, ADR-0094 (Tier 4), ADR-0096 (McpServerContrib manifest), ADR-0099 (manifest loader).
10. `## Verification` — gates.
11. `## Phase` — Sprint 6h₉d / Phase 5b-foundation (shipped). Next: 6h₉e subprocess hooks (Tier 4b).

---

## §4 — Commit split plan (W6, 5 atomic commits)

1. **§A**: `pyproject.toml` + `uv.lock` (mcp dep). Commit msg: `build(deps): add mcp SDK for Tier 4 MCP client (Sprint 6h₉d §A)`.
2. **§B**: `mcp/__init__.py` + `mcp/client.py` (McpServerConnection + McpConnectionError). Commit msg: `feat(mcp): McpServerConnection single-server lifecycle (Sprint 6h₉d §B)`.
3. **§C**: `mcp/adapter.py` + `mcp/manager.py` (Tool adapter + multi-server manager + reconnect). Commit msg: `feat(mcp): MCP→AgentTool adapter + multi-server manager (Sprint 6h₉d §C)`.
4. **§D**: `tests/mcp/` (echo fixture + ~18 tests). Commit msg: `test(mcp): MCP client connection + adapter + manager tests (Sprint 6h₉d §D)`.
5. **§E**: ADR-0101. Commit msg: `docs: ADR-0101 MCP client closure (Sprint 6h₉d §E)`.

Each: HEREDOC, Co-Authored-By trailer, `git add <specific-paths>`.

---

## §5 — Verification plan (W3-W5)

### W3
```sh
uv sync  # after mcp dep added
uv run ruff check 2>&1 | tail -3
uv run pyright 2>&1 | tail -5  # 8 baseline + any mcp-SDK-typing deltas (mcp SDK is fully typed; expect zero new IF closures typed correctly)
uv run pytest 2>&1 | tail -5  # 2475 baseline + ~18 new = ~2493
python scripts/generate_contracts_schemas.py --check  # exit 0 (contracts untouched)
```
Expected: ruff clean / pyright 8 baseline (mcp SDK is typed — verify no new errors; if SDK stubs introduce noise, document) / pytest ~2493 / schema 0 drift.

### W4 (code-reviewer)
- McpServerConnection lifecycle correctness (AsyncExitStack ownership, no zombie subprocess)
- Transport selection branch coverage (stdio/http/sse + error guards)
- `env=None` vs `{}` correctness (gotcha #3)
- `is_error` check (gotcha #5 — NOT treated as success)
- Tool adapter zero-transform (parameters IS input_schema)
- Namespace collision avoidance (`<server>__<tool>`)
- Manager partial-failure containment (one bad server doesn't abort)
- Reconnect backoff math (HTTP/SSE only; stdio reactive)
- Import-shadow guard (`aelix_coding_agent.mcp` vs SDK `mcp`)
- Test teardown (no zombie processes)

### W5 (critic)
- **Reference accuracy** — NOT Pi (verify ADR-0101 correctly characterizes Aelix-additive, no false Pi citations). MCP SDK API signatures match `mcp` 1.27.1 (verify `ClientSession`/`stdio_client`/`StdioServerParameters`/`call_tool` against the actual installed package or pinned docs).
- ContentBlock mapping completeness (text/image + fallback for audio/resource)
- McpServerContrib v2 deferral correctly documented (no 6h₉a contract mutation)
- Pi pin held (no Pi feature imported — Tier 4 is Aelix-additive)
- Commit boundary integrity (§A-§E atomic)
- ADR-0101 cross-references (0094/0096/0099)

### W6 — see §4

---

## §6 — User-imposed constraints (BINDING)

Identical to prior sprints: C1 no push / C2 no skip hooks / C3 no project-memory stage / C4 no spec modify / C5 Co-Authored-By `Claude Opus 4.7 (1M context) <noreply@anthropic.com>` / C6 HEREDOC / C7 `git add <paths>` / C8 Pi pin 734e08e no advance.

Additional for this sprint:
- **C-MCP1**: NEW external dependency `mcp` is permitted (user-approved: "공식 mcp Python SDK 의존성 추가"). Stage `uv.lock` with the dep commit.
- **C-MCP2**: Do NOT modify the 6h₉a `McpServerContrib` contract (args/headers v2 deferred). Work within `command`/`url`/`env`.

---

## §7 — Reference map (NOT Pi)

| Reference | Use |
|---|---|
| MCP spec (modelcontextprotocol.io) | protocol semantics, transport definitions |
| `mcp` Python SDK 1.27.1 (github.com/modelcontextprotocol/python-sdk) | ClientSession / stdio_client / StdioServerParameters / streamable_http_client / sse_client / types.Tool / CallToolResult API |
| Claude Code MCP docs (code.claude.com/docs/en/mcp) | mcpServers config shape, reconnect backoff (5 attempts, 1s→32s) |
| ADR-0094 | Tier 4 Aelix-additive characterization |
| ADR-0096 | McpServerContrib manifest contract |

Pi pin `734e08edf82ff315bc3d96472a6ebfa69a1d8016` held — **no Pi source consulted or imported** (Tier 4 has no Pi-core equivalent).

---

## §8 — Definition of Done

- [ ] 5 commits landed (atomic, HEREDOC, Co-Authored-By)
- [ ] `mcp>=1.27,<2` in aelix-coding-agent deps; `uv.lock` updated
- [ ] `uv run ruff check` clean
- [ ] `uv run pyright` 8 baseline preserved (mcp SDK typed — document any unavoidable SDK-stub deltas if they appear)
- [ ] `uv run pytest` ~18 new MCP tests pass; baseline preserved
- [ ] `python scripts/generate_contracts_schemas.py --check` exit 0
- [ ] `McpServerConnection` connects stdio + handles http/sse transport selection
- [ ] MCP Tool → AgentTool zero-transform (parameters IS input_schema)
- [ ] `is_error` checked (not silent success)
- [ ] No zombie subprocesses in tests (clean AsyncExitStack teardown)
- [ ] ADR-0101 characterizes Aelix-additive (NOT Pi port); Pi pin held
- [ ] McpServerContrib v2 deferral documented (no 6h₉a contract mutation)
- [ ] No staged project-memory/temp files; no push

---

## §9 — Glossary

| Term | Definition |
|---|---|
| `McpServerConnection` | One MCP server connection (transport + session + AsyncExitStack lifecycle) |
| `McpClientManager` | Multi-server dict + connect-all/collect-tools/disconnect-all + reconnect |
| Tool namespacing | `<server-name>__<tool-name>` to avoid cross-server tool collisions |
| Zero-transform mapping | MCP `input_schema` → Aelix `Tool.parameters` with no conversion (both JSON Schema dicts) |
| `is_error` gotcha | MCP domain errors return `CallToolResult(is_error=True)`, NOT a Python exception |

---

## §10 — Aelix-additive characterization

Sprint 6h₉d is **entirely Aelix-additive**. Pi `coding-agent/src` has no MCP client (W0-verified: 0 files). The reference basis is the MCP spec + `mcp` SDK + Claude Code config conventions, NOT Pi source. The Pi pin is held at `734e08e` and no Pi feature is imported. ADR-0094 §"Tier 4" pre-authorized this: Aelix elevates MCP to a formal extension tier matching the Claude Code / gemini-cli universal pattern. There is therefore no Pi-parity citation table in ADR-0101 — instead a "reference map" (§7) documents the MCP spec / SDK / Claude Code basis.

---

## §11 — End of spec

Spec author: Main agent (W0+W1, Sprint 6h₉d)
Spec status: Binding (do not modify)
W2 executor: read this spec, execute commits 1-5 per §4, verify per §5, honor §6. Report SHAs + DoD evidence.
