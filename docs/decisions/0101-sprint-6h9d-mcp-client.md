# 0101. Sprint 6h₉d — MCP Client (Tier 4a)

Status: Accepted (Sprint 6h₉d / Phase 5b-foundation / W6 shipped)
Date: 2026-05-25
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₉d is the **fourth sprint of Phase 5b-foundation**. Sprint 6h₉a
(ADR-0098) shipped the `aelix-plugin.toml` v1 manifest contracts including
`McpServerContrib`; Sprint 6h₉b (ADR-0099) wired manifest detection into the
loader; Sprint 6h₉c (ADR-0100) implemented the `ExtensionUIContext` surface.
None of these connected to an actual MCP server — `McpServerContrib` was a
**declaration-only** contract awaiting a client runtime.

Sprint 6h₉d adds that runtime: a Tier 4a MCP **client** that consumes the
`McpServerContrib` declarations, opens transports (stdio / streamable-HTTP /
SSE), runs the MCP `initialize` handshake, lists tools, calls tools, and
exposes MCP tools to the Aelix agent loop as `AgentTool` instances.

This is **Aelix-additive**, not a Pi port — see *Aelix-additive
characterization* below. Per ADR-0094 §"Tier 4", Aelix elevates MCP to a
formal extension tier matching the Claude Code / gemini-cli universal pattern.
The reference basis is the MCP spec, the official `mcp` Python SDK 1.27.1, and
the Claude Code `mcpServers` client config — **not** Pi source.

## Decision

Sprint 6h₉d ships five deliverables in five atomic commits:

1. **`mcp>=1.27,<2` dependency** added to `aelix-coding-agent`. The official
   `mcp` Python SDK provides `ClientSession`, transport helpers, and the MCP
   wire types. `uv.lock` updated with the transitive closure (anyio, httpx,
   jsonschema, pydantic-settings, starlette).
2. **`McpServerConnection`** (`mcp/client.py`) — one MCP server connection:
   transport selection by `McpServerContrib.transport`, the `initialize`
   handshake, `list_tools` / `call_tool` delegation, and clean `AsyncExitStack`
   teardown. `McpConnectionError` wraps all connect/handshake/transport
   failures. Idempotent connect/disconnect; async context-manager support.
3. **MCP Tool → AgentTool adapter** (`mcp/adapter.py`) — `mcp_tool_to_agent_tool`
   / `mcp_tools_to_agent_tools` with zero-transform schema passthrough and
   `CallToolResult` → `ToolResult` mapping.
4. **`McpClientManager`** (`mcp/manager.py`) — multi-server dict keyed by name:
   `connect_all` (partial-failure-tolerant), `collect_agent_tools` (namespaced),
   `call_tool_with_retry` (HTTP/SSE backoff; stdio reactive), `disconnect_all`.
5. **Tests + this ADR** — 22 tests over a real stdio FastMCP echo server.
   Tests live in `tests/mcp_client/` (NOT `tests/mcp/` as the binding spec
   §3.5 wrote): under pytest's default `prepend` import mode, a top-level
   `tests/mcp/` package would shadow the installed `mcp` SDK during
   collection and break `import mcp.types`. The rename preserves the
   "MCP tests namespaced together" intent without the collision.

The new code lives in a `aelix_coding_agent.mcp` subpackage. All SDK imports
are absolute (`from mcp import ...`) so the local subpackage never shadows the
top-level SDK package.

## Aelix-additive characterization

Sprint 6h₉d is **entirely Aelix-additive**. W0 verified that
`earendil-works/pi@734e08e` `coding-agent/src` has **zero MCP files** — Pi has
no MCP client in core. Pi extensions are in-process TypeScript; MCP, where
present, is added via extension, not core. There is therefore **no Pi-parity
citation table** for this sprint. Instead, the *Reference map* (below)
documents the MCP spec / SDK / Claude Code basis.

The Pi pin is held at `734e08edf82ff315bc3d96472a6ebfa69a1d8016` and **no Pi
feature is consulted or imported** (Tier 4 has no Pi-core equivalent).
ADR-0094 §"Tier 4" pre-authorized this: *"Pi supports MCP via extension; Aelix
elevates to a formal tier matching the Claude Code / gemini-cli universal
pattern."*

## Tool mapping

- **Schema**: MCP `Tool.inputSchema` (JSON Schema dict) → Aelix
  `AgentTool.parameters` with **zero transform** — both are JSON Schema dicts,
  so the object is passed verbatim (identity, not a copy).
- **Invocation**: each MCP tool becomes an `AgentTool` whose `execute` closure
  matches the Aelix `ToolExecute` signature `(args, context) -> ToolResult` and
  delegates to `McpServerConnection.call_tool(tool.name, args)`.
- **Result**: `CallToolResult.content` (list of content blocks) →
  `ToolResult.content`:
  - `TextContent` → Aelix `TextContent`
  - `ImageContent` → Aelix `ImageContent` (base64 `data` + `mime_type`)
  - audio / resource_link / embedded_resource → a typed text-marker fallback
    (`[mcp:<type> content block]`) for richer Phase 6 handling — never silently
    dropped.
  - `CallToolResult.structuredContent` → `ToolResult.details`.
- **Error**: `CallToolResult.isError == True` → `ToolResult.is_error == True`.
  MCP domain errors are NOT Python exceptions — the adapter checks the flag and
  surfaces it rather than treating an error result as success.

## SDK API note (`mcp` 1.27.1, verified against the installed package)

The installed SDK exposes **camelCase** attributes on its wire types, which
differs from some MCP docs that cite snake_case. Code targets the verified
shapes:

- `Tool.inputSchema` (NOT `input_schema`)
- `InitializeResult.serverInfo` / `.protocolVersion`
- `CallToolResult.isError` / `.structuredContent` / `.content`
- `ImageContent.mimeType` / `.data`
- The HTTP transport helper is `streamablehttp_client` and yields a **3-tuple**
  `(read, write, get_session_id)`; stdio/sse yield a 2-tuple.
- `ClientSession.call_tool(read_timeout_seconds=...)` takes a
  `datetime.timedelta`, so the connection converts a float-seconds argument.
- `mcp` has no `__version__` attribute; the version *would be* read via
  `importlib.metadata.version("mcp")` if needed (Sprint 6h₉d ships no
  code path that reads the version — documented here as a gotcha only).
- `InitializeResult.protocolVersion` is typed `str | int` by the SDK
  (date-strings like "2025-06-18" in practice). `McpServerConnection.
  protocol_version` retains the SDK's `str | int | None` shape. (Sprint
  6h₉d fold-in §F note: a W5 MINOR proposed narrowing to `str | None`;
  direct SDK introspection of `InitializeResult.model_fields` confirmed
  the `str | int` union, so the narrowing was **rejected** — narrowing
  would introduce a pyright assignment error at the `init.protocolVersion`
  binding site.)

## Transport support

| Transport | Helper | Status |
|---|---|---|
| stdio | `stdio_client(StdioServerParameters)` | **primary** — local subprocess |
| streamable HTTP | `streamablehttp_client(url=...)` | **recommended** for remote |
| SSE | `sse_client(url=...)` | **deprecated** (MCP spec) — legacy servers only |

`StdioServerParameters.env`: when `McpServerContrib.env` is empty the connection
passes `env=None` so the SDK applies `get_default_environment()` (safe selective
inherit). When non-empty it merges `{**os.environ, **contrib.env}`. Passing
`env={}` would break the subprocess (no PATH) and is never done.

## Reconnect policy

Adopted from the Claude Code MCP client pattern:

- **stdio**: reactive, **no auto-reconnect**. The local subprocess is gone on a
  broken pipe; `call_tool_with_retry` makes a single attempt for stdio servers,
  wrapping any raw transport exception in `McpConnectionError` for
  error-contract consistency with the HTTP/SSE path (Sprint 6h₉d fold-in §F).
  The caller must `connect()` a fresh connection.
- **HTTP/SSE**: exponential backoff — attempt *N* sleeps
  `min(1.0 * 2**N, 32.0)` seconds, reconnecting before each retry
  (`max_attempts` default 3; Claude Code uses up to 5 attempts, 1s → 32s cap).

`McpClientManager.disconnect_all` tears down connections in **reverse (LIFO)
connect order**: each transport opens an anyio task-group cancel scope in the
manager's task, and anyio requires sibling cancel scopes opened in one task to
unwind strictly LIFO (FIFO teardown raises "exit cancel scope in a different
task").

**Same-task requirement (Sprint 6h₉d fold-in §F — W5 MINOR-1)**: the LIFO
discipline only holds because `connect_all` and `disconnect_all` run in the
**same asyncio task**. anyio's cancel-scope affinity check means a future
caller that connects in one task and disconnects in another (e.g. a background
reconnect supervisor, or a daemon connecting at startup and tearing down on a
signal-handler task) will hit the same `RuntimeError`. Sprint 6h₉d never does
this (single-task connect/disconnect), so it is correct for this scope — but
**Sprint 6h₉f (aelix-server daemon) must use anyio task groups** if it manages
MCP connection lifecycles across tasks. Flagged here so the hazard is not
rediscovered the hard way.

## Deferred items

Per Sprint 6h₉d scope (§1.4 of the binding spec):

| Item | Owner | Reason |
|---|---|---|
| MCP resources (`list_resources` / `read_resource`) | Phase 6+ | tools first |
| MCP prompts (`list_prompts` / `get_prompt`) | Phase 6+ | secondary surface |
| MCP sampling (server → client LLM) | Phase 6+ | advanced, rare |
| subprocess hooks (Tier 4b) | Sprint 6h₉e | split per user decision |
| aelix-server | Sprint 6h₉f | Phase 5b-foundation #6 |
| MCP server auth (OAuth flow) | Phase 6 | header/token passthrough only |
| Capability enforcement (`mcp_invoke`) | Phase 6 | declaration-only (ADR-0096) |

## McpServerContrib v2 note

The current `McpServerContrib` (Sprint 6h₉a, ADR-0096) carries `name` /
`transport` / `command` / `url` / `env`. It **lacks** two fields the MCP client
will eventually want:

- `args: list[str]` — stdio CLI arguments (Sprint 6h₉d always passes `args=[]`)
- `headers: dict[str, str]` — HTTP auth/custom headers (Sprint 6h₉d sends none)

Sprint 6h₉d works **within** this limitation deliberately. Adding the fields
now would require regenerating the JSON Schemas and bumping the contracts
package — out of scope here. A follow-up sprint extends `McpServerContrib` with
`args` + `headers` as **manifest schema v1.1** (a minor, **non-breaking**
addition per the ADR-0095 versioning rules). The Sprint 6h₉a contract is
**not** mutated by this sprint (constraint C-MCP2).

## References

### Reference map (NOT Pi — Tier 4 is Aelix-additive)

| Reference | Use |
|---|---|
| MCP spec (modelcontextprotocol.io) | protocol semantics, transport definitions |
| `mcp` Python SDK 1.27.1 (github.com/modelcontextprotocol/python-sdk) | `ClientSession` / `stdio_client` / `StdioServerParameters` / `streamablehttp_client` / `sse_client` / `types.Tool` / `CallToolResult` API |
| Claude Code MCP docs (code.claude.com/docs/en/mcp) | `mcpServers` config shape, reconnect backoff (5 attempts, 1s → 32s) |

### ADR cross-references

- **ADR-0094** — Aelix 4-tier extension architecture (Tier 4 Aelix-additive
  characterization).
- **ADR-0096** — manifest v1 schema (`McpServerContrib` contract).
- **ADR-0099** — Sprint 6h₉b manifest loader integration (declarations source).
- **ADR-0095** — UI descriptor protocol / manifest versioning rules
  (non-breaking minor-bump basis for the v2 `args`/`headers` note).

Pi pin `734e08edf82ff315bc3d96472a6ebfa69a1d8016` held — no Pi source consulted
or imported.

## Verification

| Gate | Result |
|---|---|
| `uv run ruff check` | clean |
| `uv run pyright` | 8 baseline preserved (mcp SDK is typed; zero new errors) |
| `uv run pytest` | 22 new MCP tests pass; baseline preserved |
| `python scripts/generate_contracts_schemas.py --check` | exit 0 (contracts untouched) |
| zombie subprocesses | none (clean `AsyncExitStack` teardown verified) |
| import-shadow guard | `import mcp` (SDK) + `from aelix_coding_agent.mcp.client import McpServerConnection` (local) both resolve |

## Phase

Sprint 6h₉d / Phase 5b-foundation (shipped). Next: Sprint 6h₉e — subprocess
hooks (Tier 4b).
