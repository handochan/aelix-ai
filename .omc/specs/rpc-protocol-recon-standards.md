# Standard-protocol recon — JSON-RPC 2.0 / ACP / MCP vs aelix's measured rpc defects

> **Written:** 2026-07-29, during the RPC sprint recon phase. **Nothing was edited.**
> Every `path:line` below was opened against **`da61337`** (current `main`). The line numbers
> quoted in `rpc-sprint-recon-transport.md` / `rpc-sprint-recon-envelope.md` (taken against
> `6d7ec9a`) were **re-verified**, not trusted — where they moved, the new number is used and
> the drift is called out.
>
> **MEASURED** = I ran it or opened it. **INFERRED** = reasoning on top of measured facts.
> **SPEC** = quoted from the published standard.

---

## 0. The one-paragraph verdict

**ACP (Agent Client Protocol) is the only standard here that is a real candidate for aelix's
control protocol, and it is a strong one** — it is JSON-RPC 2.0 over newline-delimited stdio,
purpose-built for "editor spawns agent, agent asks permission back", and it fixes **six of
aelix's seven measured defects normatively**, including the child→parent approval back-channel
that ADR-0197 §(i) lists as deferred. **Bare JSON-RPC 2.0 fixes only two of the seven
structurally** — error codes and bidirectionality — and would break pi wire parity for zero
interop payoff; if the wire is going to break, it should break for ACP, not for bare JSON-RPC.
**MCP is a category error as a control protocol** (tools *into* an agent, not control *of* an
agent) and — measured — aelix's MCP layer is **not** hand-rolled JSON-RPC that could be
harvested: it is a 500-line wrapper over the official `mcp` SDK, and that SDK's session layer
is type-locked to MCP's own message unions, so its reuse value in lines-of-code is
approximately **zero**. The real, non-zero MCP value is precedent: it proves a JSON-RPC stdio
dependency already integrates cleanly in this tree.

---

## 1. Baseline: what aelix's wire actually is today (MEASURED, re-verified at `da61337`)

aelix's rpc protocol is a **bespoke, pi-ported JSONL protocol**, not JSON-RPC.

| Property | Evidence |
|---|---|
| Framing = newline-delimited JSON, LF-only by deliberate design (payloads may contain U+2028/U+2029) | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:1-31` — `serialize_json_line` is `json.dumps(value, ensure_ascii=False) + "\n"` at `:31` |
| Reader is chunked `read(4096)`, splits on `"\n"`, strips a trailing `\r` | `rpc/_jsonl.py:61-73` (`feed`) and `:92-110` (`attach_jsonl_line_reader`) |
| **The string `"jsonrpc"` appears nowhere in `packages/`** | `grep -rn "jsonrpc" --include=*.py packages/` → **0 hits** (MEASURED) |
| **The strings `protocol_version` / `protocolVersion` / `initialize` / `handshake` / `capabilit` appear nowhere in `rpc/`** | `grep -rn 'protocol_version\|protocolVersion\|initialize\|handshake\|capabilit' rpc/*.py` → **0 hits** (MEASURED) |
| Command envelope = `{type, id?, …}`; response envelope = `{type:"response", command, success, data?/error?, id?}` | `rpc/rpc_types.py:57-64` (command), `:477-483` (success), `:501-510` (error) |
| Errors are **free strings, no code** | `rpc/rpc_types.py:495-496` — `command: str` / `error: str`. There is no `code` field anywhere. |
| `id` is **optional** on both command (`str \| None = None`, `rpc_types.py:63`) and response (`:497`); `to_json` omits it when `None` (`:479-480`, `:508-509`) | The client always generates one (`rpc/rpc_client.py:550` → `f"req_{next(self._next_id)}"`), so it is optional-by-schema, mandatory-by-convention |
| 29 commands in the union | `rpc/rpc_types.py:309-338` (union) and `:344-374` (`_RPC_COMMAND_REGISTRY`) — counted: **29** |
| Events carry **no id, no turn number, no sequence** — bare `dataclasses.asdict` | `rpc/rpc_mode.py:258-268` (`_dataclass_to_dict`), `:271` (`_event_to_dict`), emitted at `:1927-1943` via `_on_agent_event` |
| Both channels share that serializer | `modes/print_mode.py:59-68` imports and delegates to `rpc_mode._dataclass_to_dict` |

### The seven defects, re-verified

| # | Defect | Re-verified evidence at `da61337` |
|---|---|---|
| **D1** | No terminator on abort / failed prompt / busy rejection | Failed prompt: `rpc/rpc_mode.py:309-320` — the `except` prints to stderr and the comment at `:316-320` still says *"Sprint 6f wires that bridge per ADR-0058 carry-forward"*. Abort: `rpc/rpc_mode.py:333-340` — `await harness.abort()` then `return RpcSuccessResponse(...)`, no event. Cost: `rpc_client.py:87` `DEFAULT_WAIT_FOR_IDLE_MS = 60_000`, consumed by `prompt_and_wait` at `:435-441` whose only completion predicate is `event.get("type") == "agent_end"` (`:439-441`). |
| **D2** | Fire-and-forget prompt acks success before validation | `rpc/rpc_mode.py:289-330` — `asyncio.create_task(_run())` at `:322`, `return RpcSuccessResponse(id=cmd.id, command="prompt")` at `:330`. The task has not started when the ack is written. |
| **D3** | No event correlation | `rpc/rpc_mode.py:1927-1943` — `_on_agent_event` emits `_event_to_dict(event)` verbatim. No sessionId, no turnId, no seq. `id` correlation exists **only** for command→response (`rpc_client.py:508-518`). |
| **D4** | No error codes, only free strings | `rpc/rpc_types.py:495-496,501-510`. 20 `RpcErrorResponse(` construction sites in `rpc_mode.py`, every one carrying a prose string. |
| **D5** | No version / capability handshake | The zero-hit grep above. There is no `initialize`-equivalent command in the 29. |
| **D6** | camelCase responses + snake_case events on one fd | `rpc/rpc_types.py:26` `_camel()`; per-command remap `_COMMAND_FIELD_REMAP` at `:379+`; vs `dataclasses.asdict` of snake_case kernel dataclasses at `rpc_mode.py:258-268`. Same stdout fd. |
| **D7** | No server→client request direction | `rpc/rpc_mode.py:2046-2049` — `if payload.get("type") == "extension_ui_response": return` (a bare drop, with a comment saying the bridge is "deferred to Sprint 6f per ADR-0058"). No `extension_ui_request` is ever emitted. **Drift note:** the P3 recon cited `:2045-2049`; at `da61337` the discriminator line is `:2048`. |

---

## 2. JSON-RPC 2.0 — the base spec

**SPEC** (https://www.jsonrpc.org/specification):

- **§4 Request**: `jsonrpc` MUST be exactly `"2.0"`; `method` (names starting `rpc.` reserved);
  `params` (by-position array or by-name object); `id` established by the client.
- **§4.1 Notification**: *"A Notification is a Request object without an 'id' member."* The
  server **must not** reply, and *"Notifications are not confirmable by definition… the client
  would not be aware of any errors."*
- **§5 Response**: `jsonrpc`, exactly one of `result` / `error`, and `id` echoing the request
  (or `null` on parse error). *"Either the result member or error member MUST be included, but
  both members MUST NOT be included."*
- **§5.1 Error object**: `{code: integer, message: string, data?: any}` with the reserved table
  **-32700** Parse error, **-32600** Invalid Request, **-32601** Method not found, **-32602**
  Invalid params, **-32603** Internal error, and **-32000 … -32099** reserved for
  implementation-defined server errors.
- **§6 Batch**: array in, array out; notifications produce no member; *"responses may be
  returned in any order; the Client SHOULD match contexts by id."*
- **Bidirectionality**: *"One implementation of this specification could easily fill both
  [Client and Server] roles, even at the same time, to other different clients or the same
  client."*
- **Transport / framing / ordering**: *"It is transport agnostic in that the concepts can be
  used within the same process, over sockets, over http, or in many various message passing
  environments."* **No framing, ordering or delivery requirements are specified at all.**
- **Versioning**: the only version signal is the presence of the `"jsonrpc"` member (2.0 has
  it, 1.0 does not). **There is no capability-negotiation mechanism in the spec.**

### Defect-by-defect, ruthlessly

**D1 (no terminator) — HALF-FIXED, and only if you make a semantic choice.**
JSON-RPC gives you exactly one structural gift here: §5 makes a reply to an `id`-bearing
request **obligatory and exactly-once**. That is real — it is the guarantee aelix's
`prompt`/`abort` path currently lacks. But **JSON-RPC has no concept of a turn.** Whether the
`prompt` reply means "accepted" or "the turn is over" is entirely yours to decide. If you model
`prompt` as aelix does today (early ack + a separate `agent_end` event), JSON-RPC changes
nothing and you still hang 60 s. **JSON-RPC supplies the mechanism; it does not supply the
semantics.** ACP is exactly the choice of semantics on top of that mechanism (§3).

**D2 (early ack) — NOT FIXED. This is purely semantic.**
Nothing in JSON-RPC forbids replying `{"result":{}}` before doing any work. A JSON-RPC
rewrite of `_handle_prompt` that kept the `create_task` + immediate return would be a
fully-conforming JSON-RPC server with the identical bug. The *only* thing JSON-RPC adds is that
a busy rejection becomes **expressible** as a typed error (D4) — but expressibility is not the
defect; the defect is that the reply is written before the outcome is known.

**D3 (no event correlation) — NOT FIXED at the framing layer.**
Events would be **notifications**, and §4.1 says a notification has no `id`. JSON-RPC provides
**no** stream id, no turn id, no sequence number, and explicitly no ordering guarantee (§6
even tells you batch responses may arrive out of order). Correlation for an event stream is a
`params` field you design yourself. This is precisely what ACP does — `SessionNotification`
carries `sessionId` in `params` — and it is an **application-layer** decision, not something
the base spec hands you.
*Honest sub-note:* even ACP gives you **correlation but not a sequence number**. Ordering on
stdio is stream order, in both designs.

**D4 (no error codes) — FIXED, structurally.**
§5.1 mandates an integer `code`. Adopting JSON-RPC would make `error: str` (`rpc_types.py:496`)
non-conforming and force a numeric taxonomy, with -32601/-32602 handed to you for free
(unknown command / bad params — two of aelix's 20 error strings today). The **-32000…-32099**
range is exactly where aelix-specific codes (busy, no-model, aborted, permission-denied) belong.
You still have to design the taxonomy; the spec only forces you to have one.
*Prior art for the shape:* LSP added its own reserved band **-32899 … -32800** (`RequestCancelled`
-32800, `ContentModified` -32801, `ServerCancelled` -32802, `RequestFailed` -32803,
`ServerNotInitialized` -32002, `UnknownErrorCode` -32001).

**D5 (no handshake) — NOT FIXED. Fully orthogonal.**
The spec has no capability negotiation and says so. Every protocol built on JSON-RPC invents
its own: LSP has `initialize`/`ClientCapabilities`, MCP has `initialize`/`protocolVersion`,
ACP has `initialize`/`protocolVersion`+`agentCapabilities`. **JSON-RPC gives you nothing here.**

**D6 (mixed casing) — HALF-FIXED, and the half it fixes is the smaller half.**
JSON-RPC does fix the **outer** envelope: `jsonrpc`/`method`/`params`/`id`/`result`/`error`
become the universal shape, and aelix's ad-hoc `{type:"response",…}`-beside-a-bare-event
dichotomy disappears — every line on the fd becomes a JSON-RPC message with a `method`
discriminator. That is genuine. But the **contents** of `params`/`result` are entirely yours,
so snake_case event payloads sitting beside camelCase results remain perfectly legal. **The
defect as measured (`_camel` at `rpc_types.py:26` vs `dataclasses.asdict` at
`rpc_mode.py:258-268`) survives a JSON-RPC migration untouched** unless you separately decide
on one spelling.

**D7 (no server→client requests) — FIXED, structurally, and this is JSON-RPC's best offer.**
The spec explicitly blesses both peers filling both roles simultaneously, each with its own
`id` space. That is exactly the missing direction. And the transport half is **already there**
in aelix: the rpc child attaches its own stdin reader (`rpc_mode.py`'s `connect_read_pipe`
path), so blocker (a) from ADR-0197 §(i) — *"the child's stdin reads to EOF"* — is print-mode
only, as the transport recon already established (`cli/entry.py:1346-1347` gates
`_read_piped_stdin` on `app_mode in ("print","json")`).
**Honest limit:** JSON-RPC gives you the *wire* for the back-channel. ADR-0197 §(i) blocker (c)
— the parent TUI has no modal arbiter — is a UI/runtime problem no framing standard touches.

**JSON-RPC 2.0 scorecard: 2 fixed (D4, D7), 2 half (D1, D6), 3 untouched (D2, D3, D5).**

---

## 3. ACP — Agent Client Protocol (agentclientprotocol.com, Zed Industries)

### 3.1 Maturity and who implements it (MEASURED via web)

- Open standard, **first published by Zed Industries, August 2025**. Apache-2.0.
  GitHub `agentclientprotocol/agent-client-protocol`, **~3.8k stars**. No
  "experimental/unstable" banner on the README.
- **Current stable protocol version = `1`** (an integer, negotiated at `initialize`).
- **Official SDKs: TypeScript, Rust, Python, Kotlin (JVM), Java.**
  Python: `agent-client-protocol` on PyPI, **0.11.1, released 2026-07-27** (two days before
  this note), `requires-python >=3.10,<3.15`. Generated Pydantic models in `acp.schema` +
  asyncio stdio JSON-RPC plumbing + async base classes for both the agent and client roles.
  → aelix is `requires-python >=3.11` (`pyproject.toml:10`, all five packages) — **compatible**.
- **Clients (the editor side), verbatim from the site:** Anycode, Chrome ACP, Emacs
  (agent-shell.el), **JetBrains**, Neovim (CodeCompanion / agentic.nvim / avante.nvim /
  hermes.nvim), Obsidian, Pulsar, Qt Creator, Unity, **Visual Studio Code** (three separate
  extensions), **Zed**.
- **Agents, verbatim (36 listed):** AgentPool, Augment Code, AutoDev, Blackbox AI, Bub,
  **Claude Agent**, Cline, **Codex CLI**, Code Assistant, Construct, crow-cli, **Cursor**,
  Docker cagent, fast-agent, Factory Droid, fount, **Gemini CLI**, **GitHub Copilot**, Goose,
  Hermes Agent, **Junie (JetBrains)**, Kimi CLI, Kiro CLI, Minion Code, Mistral Vibe, OpenClaw,
  **OpenCode**, OpenHands, **Pi (via `pi-acp` adapter)**, Poolside, Qoder CLI, Qwen Code,
  siGit Code, Stakpak, stdio Bus, VT Code.

> **⚠ The single most decision-relevant line in this whole note:**
> **`Pi` is on that list**, via the third-party `pi-acp` adapter — and that adapter's own
> description is *"communicates ACP JSON-RPC 2.0 over stdio to an ACP client (e.g. Zed) and
> **spawns `pi --mode rpc`**, bridging requests/events between the two."*
> aelix is a Python port of pi and has the **same** `--mode rpc`. The community has already
> demonstrated, on aelix's own upstream, that ACP is best applied as an **adapter layered over
> the existing rpc mode**, not as a replacement for it.
> (Upstream pi itself: discussion #4444 and issue #175 exist; **no maintainer accept/reject is
> recorded** — INFERRED status: community-adapter-only, no native support.)

### 3.2 The mechanics that matter to aelix (SPEC, quoted)

**Transport & framing.** JSON-RPC 2.0 over the agent subprocess's stdin/stdout, **newline-
delimited JSON**, editor spawns the agent. Two message kinds only: *"Methods (Request-response
pairs) and Notifications (One-way messages)."*
*(Confidence note: the `protocol/overview` page itself does not state the framing; this is from
the GitHub README/SDK docs and the Python SDK description. Treat "NDJSON" as HIGH but not
spec-page-verified.)*

**Direction map** (verbatim method names):
- **Client → Agent:** `initialize`, `authenticate`, `session/new`, `session/load`,
  `session/prompt`, `session/cancel`, `session/set_mode`, `logout`.
- **Agent → Client:** `session/update`, **`session/request_permission`**, `fs/read_text_file`,
  `fs/write_text_file`, `terminal/create`, `terminal/output`, `terminal/release`,
  `terminal/wait_for_exit`, `terminal/kill`, `elicitation/create`, `elicitation/complete`.

**The turn is a request.** *"Turn ends and the Agent sends the `session/prompt` response with a
stop reason."* `StopReason` ∈ **`end_turn` | `max_tokens` | `max_turn_requests` | `refusal` |
`cancelled`**.

**Cancellation is normative.** The client sends `session/cancel` (a notification); *"the Agent
**MUST** respond to the original `session/prompt` request with the `cancelled` stop reason"*,
and *"Agents must catch errors from aborted operations and return the `cancelled` stop reason
rather than error responses."*

**Handshake.** *"The `initialize` request **MUST** include the latest protocol version the
Client supports."* Agent answers with the same version if supported, else its own latest. *"If
the Client does not support the version specified by the Agent … the Client **SHOULD** close
the connection and inform the user."* Capabilities: `clientCapabilities` (fs, terminal,
elicitation) / `agentCapabilities` (loadSession, promptCapabilities, mcpCapabilities, auth) +
optional `clientInfo`/`agentInfo`, and *"Clients and Agents **MUST** treat all capabilities
omitted in the `initialize` request as UNSUPPORTED."*

**Permissions (the back-channel).** `session/request_permission(sessionId, toolCall, options[])`
where each option is `{optionId, name, kind}` and `kind` ∈ **`allow_once` | `allow_always` |
`reject_once` | `reject_always`**. Client responds with an outcome that is either
`selected{optionId}` or `cancelled`; *"If the current prompt turn gets cancelled, the Client
**MUST** respond with the `'cancelled'` outcome."*

**Session modes** = permission postures. `session/new` returns available modes + a current
mode; `session/set_mode` changes it; the agent notifies via a `current_mode_update`
`session/update`. Documented examples are literally `"ask"` (*"Request permission before making
any changes"*), `"architect"`, `"code"` — and the mode-escalation permission prompt's options
are *"Yes, and auto-accept all actions"* (`allow_always`) / *"Yes, and manually accept actions"*
(`allow_once`) / *"No, stay in architect mode"* (`reject_once`).

**Errors.** JSON-RPC's reserved table, plus ACP's own **`-32800` Cancelled** (for
`$/cancel_request`) and an `auth_required` error on `session/new`. There is an `_meta` extension
field throughout: *"reserved by ACP to allow clients and agents to attach additional
metadata … Implementations MUST NOT make assumptions about values at these keys."*
Batching is not mentioned in the schema.

### 3.3 Would ACP's permission flow give aelix the deferred back-channel "for free"?

**The wire: yes, essentially completely.** The shape is an uncanny match for aelix's own
consent model — `allow_once` / `allow_always` / `reject_once` / `reject_always` is aelix's
ask-vs-auto-accept-edits distinction, `session/set_mode` is aelix's permission posture, and the
cancel-precedence rule (*"MUST respond with the `cancelled` outcome"*) is precisely the
deadlock rule aelix would otherwise have to invent from scratch — a pending permission request
with a cancelled turn is a hang, and ACP has already specified the answer. `fs/*` and
`terminal/*` additionally hand you child→parent tool delegation, which aelix has not even
scoped.

**Not free, honestly:**
1. **ADR-0197 §(i) blocker (c) survives intact.** The parent TUI has no modal arbiter. ACP tells
   you the message shape; it does not render a dialog, does not sanitise interpolated fields,
   and does not clamp the posture. **Invariant 1 of the kickoff handoff §5 (every dialog field
   goes through the sanitiser — P3 proved a model-supplied `cwd` could forge the whole dialog)
   applies unchanged to an ACP-sourced `PermissionOption.name`**, which is agent-supplied text
   destined for a human-readable label. ACP makes that field *more* reachable by untrusted
   input, not less. This is a **new** attack surface to sanitise, not a solved one.
2. ACP has no concept of aelix's **depth guard / fork-bomb prevention** or its band rule. Spawn
   policy stays exactly where P2 put it.
3. ACP's permission model is **per-tool-call within a turn**. aelix's P2/P3 consent is
   **spawn-time** and per-delegation. They are not the same event, and mapping one onto the
   other is design work, not adoption.

### 3.4 What would it cost aelix to speak ACP?

| Cost | Assessment |
|---|---|
| Framing | **Zero.** ACP is NDJSON; `_jsonl.py:31`/`:61-73` already is NDJSON. |
| Dependency | One new dep (`agent-client-protocol>=0.11`), or hand-roll. Precedent exists (`mcp>=1.27,<2` at `packages/aelix-coding-agent/pyproject.toml:34`). |
| Bidirectional plumbing | The child already has a live bidirectional stdin (`rpc_mode.py` `connect_read_pipe`); the client already has id-correlated futures (`rpc_client.py:105`, `:508-518`, `:550-575`). Both halves exist. |
| **Command-vocabulary mismatch — the real cost** | ACP covers `prompt`/`cancel`/`new`/`load`/`set_mode`/`authenticate`. **aelix has 29 rpc commands** (`rpc_types.py:309-338`): `cycle_model`, `set_thinking_level`, `compact`, `set_auto_compaction`, `abort_retry`, `bash`, `abort_bash`, `export_html`, `fork`, `clone`, `get_fork_messages`, `switch_session`, `get_session_stats`, … **ACP has no counterpart for ~24 of them.** They would have to live in `_meta` or in a private extension namespace. **Anyone proposing ACP as a *replacement* for `rpc_mode` is proposing to delete 24 of 29 commands.** (INFERRED from the two command lists; MEASURED counts on both sides.) |
| **Event-shape mapping** | aelix's events are `dataclasses.asdict` of **snake_case** kernel dataclasses (`rpc_mode.py:258-268`), deliberately zero-transform. ACP's schema is camelCase throughout (`sessionId`, `stopReason`, `toolCallId`, `protocolVersion`). ACP requires a **real translation layer**, not a passthrough — and it must be written twice (emit + parse) or generated. |
| **pi parity** | Every `rpc_types.py` docstring cites `rpc-types.ts:NN`. Changing the wire is an explicit aelix-original divergence from the pin at `734e08e`. The project has done this before (ADR-0191), but it is an owner decision, not a technical one. |

**Cheap shape (INFERRED, and it is the `pi-acp` shape):** ACP as a **separate adapter module**
that speaks ACP outward and drives the existing rpc protocol inward. Zero change to
`rpc_types.py`, zero pi-parity divergence, and the payoff is that an aelix child becomes
instantly drivable by Zed / JetBrains / VS Code / Neovim.

---

## 4. MCP — read the code, then read the layer

### 4.1 Is aelix's MCP hand-rolled JSON-RPC, or an SDK? (MEASURED — it is an SDK)

**It is a thin wrapper over the official `mcp` Python SDK. There is no hand-rolled JSON-RPC in
this repo at all.**

- `packages/aelix-coding-agent/src/aelix_coding_agent/mcp/` is **500 lines total**:
  `__init__.py` 30, `adapter.py` 111, `client.py` 215, `manager.py` 144 (MEASURED via `wc -l`).
- `mcp/client.py:28-33` imports `mcp.types`, `mcp.ClientSession`, `mcp.client.sse.sse_client`,
  `mcp.client.stdio.stdio_client`, `mcp.client.streamable_http.streamablehttp_client`.
  The entire protocol is `await session.initialize()` (`client.py:107`),
  `session.list_tools()` (`:179`), `session.call_tool(...)` (`:197-201`).
- `mcp/adapter.py` is a pure **type mapping**: MCP `Tool.inputSchema` → aelix `Tool.parameters`
  zero-transform (`adapter.py:98`), `CallToolResult.content` → aelix `ToolResult`
  (`adapter.py:29-63`).
- `mcp/manager.py` is multi-server lifecycle + reconnect backoff. **No wire code.**
- Dependency: **`"mcp>=1.27,<2"` at `packages/aelix-coding-agent/pyproject.toml:34`** — a
  **hard, non-optional** `[project] dependencies` entry, not an extra. Installed: **1.27.1**.
- `grep -rn "jsonrpc" --include=*.py packages/` → **0 hits.**

### 4.2 Quantifying the "reusable machinery" hypothesis — it is largely FALSE

The task brief's premise was *"if aelix already contains working JSON-RPC 2.0 machinery, that
machinery is reusable and the team already knows it."* Measured:

- **aelix contains none.** Not one line of JSON-RPC encode/decode is authored in this repo.
  The `mcp/` package never constructs a `JSONRPCRequest`.
- **The SDK's session layer is not re-targetable.** `mcp/shared/session.py:37-42` declares
  `SendRequestT = TypeVar("SendRequestT", ClientRequest, ServerRequest)` — a **value-constrained**
  TypeVar over MCP's own message unions, not a `bound=`. `BaseSession`
  (`session.py:162-…`) is therefore generic **in name only**: it cannot be parameterised over
  aelix's `RpcCommand` / `RpcResponse`. Its machinery is real and good — id-correlated
  `_response_streams` (`:179`), an `_in_flight` request-responder map (`:181`), progress
  callbacks (`:182`, `:408-417`), a `_receive_loop` (`:351`), `CancelledNotification` handling —
  but **none of it is importable for a non-MCP protocol.**
- **Reusable lines of code ≈ 0.** (MEASURED reasoning from the TypeVar constraint.)

**What IS genuinely reusable (and worth something):**
1. **Precedent.** A JSON-RPC-over-NDJSON-stdio dependency (`mcp`) already ships in the wheel,
   already survives the packaging/SBOM/OSS-compliance gates (see the CycloneDX SBOM work), and
   already handles subprocess-stdio lifecycle in-tree. Adding `agent-client-protocol` is a
   **known-shaped** change, not a novel one.
2. **Familiarity, mildly.** The team has read `initialize`-handshake semantics and camelCase-vs-
   snake_case SDK gotchas at close range — `mcp/client.py:12-18` and `:109` document exactly
   the camelCase surprise (`InitializeResult.serverInfo` / `.protocolVersion`) that an ACP
   adoption would hit again. That is transferable judgement, not transferable code.

### 4.3 Is MCP a candidate at all? **No. Category error. State it plainly.**

MCP models **capabilities flowing INTO an agent** — tools, resources, prompts. It has:
no turn, no `StopReason`, no prompt lifecycle, no cancellation-of-a-turn, no notion of one
agent driving another. In MCP's own topology **the agent is the client and the tool server is
the server** — the exact inverse of aelix's parent-drives-child relationship. The nearest
analogues (`sampling/createMessage`, server→client, "host, please run an LLM completion for
me"; and `elicitation/create`, "host, please ask the user something") are single-shot
primitives, not an agent-control surface.

Using MCP to control an aelix child would mean modelling "run a coding turn" as a tool call and
"the turn ended" as a tool result — losing streaming events, stop reasons, steering,
follow-up, abort, and every one of the 29 commands. **MCP is not a candidate for this sprint's
problem.** Its value here is exclusively the §4.2 precedent.

---

## 5. Framing: Content-Length vs newline-delimited

| Standard | Framing | Citation |
|---|---|---|
| **JSON-RPC 2.0** | **None specified.** *"It is transport agnostic…"* — no framing, ordering or delivery requirements at all. | jsonrpc.org spec, Overview |
| **LSP 3.17** | **`Content-Length: <n>\r\n` header block, `\r\n\r\n`, then the JSON body.** `Content-Type` defaults to `application/vscode-jsonrpc; charset=utf-8`. Headers are ASCII. | LSP Base Protocol |
| **MCP (stdio)** | **Newline-delimited.** *"Messages are delimited by newlines, and **MUST NOT** contain embedded newlines."* Plus *"The server **MUST NOT** write anything to its `stdout` that is not a valid MCP message"* and *"The server **MAY** write UTF-8 strings to its standard error for logging."* | MCP 2025-06-18 Transports §stdio |
| **ACP** | **Newline-delimited JSON over stdio**, editor spawns agent as subprocess. | GitHub README / Python SDK docs (see confidence note in §3.2) |
| **aelix today** | **Newline-delimited, LF-only.** `json.dumps(..., ensure_ascii=False) + "\n"` | `rpc/_jsonl.py:31` |

### Does the choice matter for a stdio agent that also emits a high-rate event stream?

**Yes, and it points at NDJSON. Four measured reasons specific to aelix:**

1. **Content-Length destroys the existing consumer.** `aelix_agents/stream.py`'s `reduce_line`
   takes a **`str` line**; `LineAssembler` (`stream.py:138-155`) is a line splitter with a
   4 MiB per-line drop budget; the entire real-child conformance bed
   (`tests/cli/test_print_mode_json_contract.py:206-253`) pumps lines. A header-framed stream
   would require replacing all of it. NDJSON costs **zero** — both real candidates already use
   it and `_jsonl.py` already implements it.
2. **Resynchronisation.** NDJSON recovers from junk at the next `\n`; a corrupted or missing
   `Content-Length` desynchronises a header-framed reader **permanently**. This matters here
   concretely: rpc mode redirects the child's `sys.stdout` into `sys.stderr` for its whole life
   (`rpc/rpc_mode.py:1846-1849`, pi's `takeOverStdout`) **precisely because** a stray
   extension/MCP `print()` would corrupt the stream — but `--mode json` deliberately does
   **not** do that redirect, and it survives, because NDJSON resyncs. Header framing would make
   the json channel's current tolerance impossible.
3. **Cost per event.** Content-Length requires computing the encoded byte length before the
   write — an extra encode pass on every event on a high-rate stream. NDJSON is one
   `json.dumps` + `"\n"`. Small, but it is a per-event cost on the hot path and it buys nothing.
4. **Debuggability.** `tail -f | jq` / `grep` on the raw stream works on NDJSON and does not on
   header-framed. Given that stderr is `envelope._select_summary`'s fallback rung and operators
   read these streams, this is not cosmetic.

**The one honest argument for Content-Length** is that a declared length removes per-line size
ambiguity, making the 4 MiB budget unnecessary. But ADR-0198 D6 already solved that with an
explicit `limit=` + chunked reads + a drop-and-resync budget, and `_jsonl.py:92-110` already
reads in 4096-byte chunks rather than `readline()`, so asyncio's 64 KiB `readline` ceiling
never fires. **The problem Content-Length solves is already solved.**

**Also note:** LF-only framing is a *deliberate, documented* aelix invariant
(`_jsonl.py:5-8`: payloads MAY contain U+2028/U+2029, so a Node-`readline`-style splitter would
corrupt records). MCP's *"MUST NOT contain embedded newlines"* is compatible with that. Any
adopted standard must not silently reintroduce a U+2028-splitting reader.

---

## 6. THE MATRIX — defect × standard

Legend: **✅ FIXED** structurally/normatively · **◐ HALF** (mechanism given, semantics still
yours) · **✖ NOT FIXED** (orthogonal to the standard) · **n/a** (wrong layer).

| # | aelix defect | JSON-RPC 2.0 (bare) | ACP | MCP | LSP framing |
|---|---|---|---|---|---|
| **D1** | No terminator on abort / failed prompt / busy rejection | **◐** §5 forces exactly one reply per `id`-bearing request — but there is no "turn", so binding the reply to turn-end is your call | **✅** `session/prompt` **is** the turn; response carries `StopReason`; on cancel the agent **MUST** answer `cancelled` | n/a — no turn concept | ✖ framing only |
| **D2** | Fire-and-forget prompt acks success before validation | **✖** nothing forbids an early `result`; the identical bug is fully conforming | **✅** the response *is* the terminator, so an early ack is impossible by construction; a busy/invalid prompt is a JSON-RPC error response | n/a | ✖ |
| **D3** | No event correlation (no turn id / sequence) | **✖** notifications have no `id` (§4.1); no seq, no ordering guarantee. You design it in `params` | **✅ for correlation** — `SessionNotification{sessionId, update}` + turn bounded by the request id. **✖ for sequence numbers** — still stream order only | n/a | ✖ |
| **D4** | No error codes, only free strings | **✅** §5.1 mandates integer `code`; -32601/-32602 free; -32000…-32099 for yours | **✅** inherits it, adds `-32800 Cancelled` and `auth_required` | (SDK inherits it — but wrong layer) | ✖ |
| **D5** | No version / capability handshake on the wire | **✖** the spec has **no** negotiation mechanism at all | **✅** `initialize` w/ integer `protocolVersion` (stable = 1), `clientCapabilities`/`agentCapabilities`, omitted ⇒ UNSUPPORTED, mismatch ⇒ client SHOULD close | (MCP has one — but wrong layer) | ✖ |
| **D6** | camelCase responses + snake_case events on one fd | **◐** unifies the **outer** envelope only; `params`/`result` spelling stays a project convention, so the measured defect survives | **✅ by fiat** — the ACP schema is camelCase throughout. **Cost:** aelix's snake_case `asdict` passthrough becomes a real mapping layer | n/a | ✖ |
| **D7** | No server→client request direction (blocks approval back-channel) | **✅ wire** — *"could easily fill both roles… at the same time"*; transport half already exists in aelix | **✅ wire + semantics** — `session/request_permission` with `allow_once`/`allow_always`/`reject_once`/`reject_always` and a normative cancel-precedence rule | n/a (inverse topology) | ✖ |
| — | **Totals** | 2 ✅ / 2 ◐ / 3 ✖ | **6 ✅ / 0 ◐ / 1 partial (D3 seq)** | **category error** | **framing only** |

**Neither standard fixes, for either candidate:**
- ADR-0197 §(i) **blocker (c)** — the parent TUI has no modal arbiter.
- The **sanitiser invariant** (kickoff §5.1). ACP's `PermissionOption.name` is agent-supplied
  human-readable text — a **new** field that must go through the sanitiser.
- The **modal bottom-truncation** bound (kickoff §5.2).
- **Depth guard / fork-bomb prevention / band rule / posture clamp / per-prompt budget** —
  entirely aelix-original policy, invisible to every standard here.
- `exit_code` semantics for a server that never self-terminates, and `dropped_lines` on rpc.
  These are **envelope** decisions (`aelix_agents/envelope.py`), untouched by any wire standard.

---

## 7. Blunt verdict

**Real candidates:**

1. **ACP — the only serious one, and it is genuinely good.** It is the exact shape of aelix's
   problem, it is 11 months old with 3.8k stars, five official SDKs, ~36 agents and ~12 editor
   families, and Cursor / Copilot / Gemini CLI / Codex / Claude Agent / OpenCode all speak it.
   It fixes 6 of 7 defects **normatively**, and it fixes the one aelix cares most about (the
   approval back-channel) with a permission vocabulary that maps almost one-to-one onto aelix's
   own postures. **Its precedent is on aelix's own upstream**: `pi-acp` speaks ACP outward and
   drives `pi --mode rpc` inward.
   **But it cannot replace `rpc_mode`.** ACP's vocabulary covers ~5 of aelix's 29 commands. Any
   proposal to swap the rpc protocol for ACP is a proposal to delete 24 commands or bury them
   in `_meta`. The defensible shape is **ACP as an adapter layered over the rpc protocol**, in
   an extension, exactly as `pi-acp` does — which also means **ACP does not have to be decided
   in this sprint**, because it changes nothing about `rpc_types.py`.

2. **Bare JSON-RPC 2.0 — a weak candidate, and mostly a trap.** It fixes D4 and D7 structurally
   and half-fixes D1 and D6. It does **nothing** for D2, D3 or D5 — the three defects most
   people assume a "standard protocol" would solve. Adopting it means re-spelling 29 commands
   and breaking pi wire parity for **zero interop payoff**: you would end up speaking a standard
   envelope that no other tool actually consumes. **If the wire is going to break, break it for
   ACP.** The one legitimate use of the bare spec is as a **design vocabulary**: steal §5.1's
   integer error codes and the -32000…-32099 band for D4, and steal the "both peers may send
   requests" model for D7, **without** adopting the envelope.

**Category errors — say no clearly:**

3. **MCP.** Wrong layer, inverse topology, no turn concept. Not a candidate. And the specific
   hope that aelix harbours reusable in-house JSON-RPC machinery is **measurably false**: there
   are zero `"jsonrpc"` occurrences in `packages/`, the `mcp/` package is 500 lines of SDK
   wrapper, and `mcp.shared.session.BaseSession`'s TypeVars are **value-constrained to MCP's own
   unions** (`session.py:37-42`), so it cannot be re-targeted. Reusable LOC ≈ 0. The real
   MCP dividend is precedent — a JSON-RPC/NDJSON/stdio dependency already ships in this wheel
   and already clears the packaging and OSS-compliance gates.

4. **LSP-style Content-Length framing.** Not a candidate for this system. Both real candidates
   use NDJSON, aelix already uses NDJSON, and header framing would break `LineAssembler`,
   `reduce_line`, the entire real-child conformance bed, and the resync tolerance that lets
   `--mode json` survive without a `takeOverStdout` redirect. It solves a per-line-size problem
   that ADR-0198 D6 already solved.

**What this means for the sprint in front of you (INFERRED, offered as a recommendation not a
decision):** none of these standards removes the sprint's actual work. D1 and D2 — the
terminator and the early ack — are the sprint's blocking defects, and they are **semantic**:
they get fixed at `rpc/rpc_mode.py:309-320` and `:333-340` whether the envelope is pi-JSONL,
JSON-RPC, or ACP. **Fix them in-band now** (the kickoff §3 fork), and **note in the resulting
ADR that ACP's `StopReason` set — `end_turn` / `max_tokens` / `max_turn_requests` / `refusal` /
`cancelled` — is a ready-made, externally-validated taxonomy for the synthetic terminator you
are about to invent.** Adopting that vocabulary costs nothing today and makes an ACP adapter
nearly free later. Likewise, when D4 is eventually addressed, use JSON-RPC's -32000…-32099 band
rather than inventing a parallel scheme. That is how you take the standards' *design* dividend
without paying their *migration* cost in this sprint.

---

## 8. Uncertainties / things I could not measure

1. **ACP's framing is not stated on the `protocol/overview` spec page** I fetched. NDJSON is
   from the GitHub README, the Python SDK description and secondary sources. HIGH confidence,
   not spec-page-verified. Anyone acting on it should confirm against the JSON schema or the
   SDK transport source.
2. **`auth_required`'s numeric error code** is referenced by the ACP docs but the exact integer
   was not visible in the page I fetched.
3. **Upstream pi's official ACP position is unrecorded.** Discussion #4444 and issue #175 exist;
   no maintainer accept/reject/defer is documented. Whether pi adopts ACP natively would change
   the pi-parity calculus materially.
4. **Whether ACP's `session/update` guarantees ordering across concurrent turns** — I did not
   verify whether ACP permits concurrent prompts on one session. If it does not, that is another
   semantic constraint aelix would inherit (and one that interacts with P3's parallel mode).
5. **`agent-client-protocol` 0.11.1's transitive dependency set and license** were not visible
   on the PyPI page I fetched — required before any dependency proposal, given the OSS/SBOM
   gates this project already maintains.
6. **The 24-of-29 command-gap count is INFERRED** by comparing two measured lists (aelix's 29 at
   `rpc_types.py:309-338`; ACP's method list from `protocol/overview`). I did not attempt a
   per-command mapping; `_meta` may absorb more than I assume.
7. **I did not measure how much of `rpc_mode.py`'s 2142 lines is command-handler surface** vs
   lifecycle, so "an ACP adapter is cheap" is a shape argument, not a sized estimate.

---

## Sources

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [Agent Client Protocol — Overview](https://agentclientprotocol.com/protocol/overview)
- [ACP — Prompt Turn](https://agentclientprotocol.com/protocol/prompt-turn)
- [ACP — Initialization](https://agentclientprotocol.com/protocol/initialization)
- [ACP — Tool Calls / request_permission](https://agentclientprotocol.com/protocol/tool-calls)
- [ACP — Session Modes](https://agentclientprotocol.com/protocol/session-modes)
- [ACP — Schema](https://agentclientprotocol.com/protocol/schema)
- [ACP — Clients](https://agentclientprotocol.com/overview/clients) · [Agents](https://agentclientprotocol.com/overview/agents)
- [agentclientprotocol/agent-client-protocol (GitHub)](https://github.com/agentclientprotocol/agent-client-protocol)
- [agent-client-protocol (PyPI)](https://pypi.org/project/agent-client-protocol/)
- [MCP 2025-06-18 — Transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [LSP 3.17 Specification — Base Protocol](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)
- [svkozak/pi-acp — ACP adapter for pi](https://github.com/svkozak/pi-acp) · [earendil-works/pi discussion #4444](https://github.com/earendil-works/pi/discussions/4444)
