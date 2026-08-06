# Aelix RPC — wire grammar (protocol specification, as-built)

> **Status:** descriptive, not normative. This document records what the code at `da61337` **does**,
> measured by opening every cited file against the current tree and by executing the server.
> There is no committed specification of this protocol anywhere in the repo; this note is the first.
>
> **Provenance of every line number:** re-opened at `da61337` (branch `main`) on 2026-07-29.
> Do not trust the line numbers in `rpc-sprint-recon-transport.md` / `rpc-sprint-recon-envelope.md`;
> several were taken at `6d7ec9a` and a few were already stale there.
>
> **MEASURED** = executed or read directly. **INFERRED** = reasoned from code without execution.
> Every claim below is labelled.

---

## 0. Executive facts a client author must know before anything else

| # | Fact | Evidence |
|---|---|---|
| 0.1 | **The stdio server does not start.** `aelix --mode rpc` raises `RuntimeError` before emitting a byte. | MEASURED, §9.1 |
| 0.2 | The wire is one-way-request-only: **the server can never initiate a request to the client.** | MEASURED, §7 |
| 0.3 | **Nothing on the wire is a version.** No handshake, no capability exchange, no schema advertisement. | MEASURED, §5 |
| 0.4 | The command envelope is **closed**: one unrecognised key ⇒ the whole command is rejected as a parse error. Forward compatibility is impossible for clients. | MEASURED, §4.3 |
| 0.5 | Events carry **no `id`, no sequence, no turn number, no timestamp**. Nothing correlates an event to the command that caused it. | MEASURED, §3.4 |
| 0.6 | One stdout stream carries **two spellings**: snake_case events and camelCase response payloads. | MEASURED, §2.4 |
| 0.7 | There is **no error code space**. Errors are free-text English strings, several of which embed Python exception `repr`s. | MEASURED, §4 |

---

## 1. FRAMING

### 1.1 Transport

Two transports carry the identical byte grammar.

| Transport | Server side | Client side |
|---|---|---|
| **stdio** — child process `--mode rpc`; commands on the child's **stdin**, responses+events on the child's **stdout** | `run_rpc_mode` (`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:1787`) | `RpcClient` (`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:73`) |
| **WebSocket** — `WS /rpc`, one text frame per JSONL line | `rpc_websocket` (`packages/aelix-server/src/aelix_server/rpc_ws.py:54`), which feeds frames into `run_rpc_mode`'s `stdin` seam and drains `stdout_write` back to the socket | none in-tree |

The WS bridge does **no translation** — `packages/aelix-server/src/aelix_server/rpc_ws.py:3-8` states it,
and `:146-153` shows `run_rpc_mode` invoked verbatim with `stdin=reader, stdout_write=stdout_write`.
Frames are normalised to exactly one trailing `\n` at `rpc_ws.py:119-120`. (MEASURED — read.)

WS is **single-flight**: a second concurrent `/rpc` connection is closed with code `1013` *before*
`accept()` (`rpc_ws.py:65-71`). (MEASURED — read.)

### 1.2 Record delimiter

- **One JSON value per line, LF (`\n`) only.** `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:68-73` splits on `"\n"` and nothing else.
- **U+2028 / U+2029 are NOT separators.** This is binding and documented as such at `_jsonl.py:5-8` and `:20-28`: payload strings may legally contain them, so a Node `readline` (or any Unicode-line-aware splitter) will corrupt records. A conforming client MUST split on `\n` only. (MEASURED — read.)
- **CRLF tolerated on input**: a single trailing `\r` is stripped per line (`_jsonl.py:56-58`). Output never emits `\r`.
- **Trailing partial line at EOF is emitted as a record** (`_jsonl.py:75-89`), after flushing the incremental UTF-8 decoder.

### 1.3 Encoding

- **UTF-8, decoded incrementally** so multi-byte sequences may straddle chunk boundaries (`_jsonl.py:52`, `:65`).
- **`ensure_ascii=False` on output.** `_jsonl.py:31`:
  ```python
  return json.dumps(value, ensure_ascii=False) + "\n"
  ```
  Non-ASCII is emitted as **raw UTF-8**, not `\uXXXX`.
  MEASURED — a probe emitting `"echo 안녕"` produced the bytes
  `{"cmd": "echo \xec\x95\x88\xeb\x85\x95"}` and the server's own em-dash error string produced `\xe2\x80\x94`.
  **This is the one asymmetry with `--mode json`**, which uses `json.dumps` at its default `ensure_ascii=True`
  (`packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py:161`). Byte-for-byte comparison
  across the two channels is therefore impossible; value-level comparison after `json.loads` is fine.
- Output is written as bytes: `serialize_json_line(obj).encode("utf-8")` (`rpc_mode.py:1880`).

### 1.4 Whitespace / blank lines

- Blank or whitespace-only input lines are **silently ignored** on both sides
  (`rpc_mode.py:2025-2026`; `rpc_client.py:499-500`). No response, no error.
- Output records contain no interior newlines (JSON escapes them) and exactly one trailing `\n`.

### 1.5 Maximum size — there is none

- **Server input:** `JsonlLineReader._buffer` is an unbounded `str` (`_jsonl.py:53`, `:61-73`). No per-line
  budget, no drop, no counter. A peer that never sends `\n` grows the server's memory without limit. (MEASURED — read.)
- **Client input:** identical reader (`_jsonl.py:104`), fed by `await stream.read(4096)` (`_jsonl.py:106`).
  Because the reader uses chunked `read()` and never `readline()`, asyncio's default 64 KiB
  `StreamReader` limit never raises — but `RpcClient.start` also passes **no explicit `limit=`**
  (`rpc_client.py:120-127`). (MEASURED — read.)
- **Output:** unbounded.
- **The only bound anywhere on this path** is the client's captured **stderr** ring:
  `RpcClient.STDERR_MAX_BYTES = 10 * 1024 * 1024`, evicted FIFO from the front (`rpc_client.py:94`, `:492-494`).

> Consequence for `SubagentResult.dropped_lines`: structurally always `0` on this channel, because
> nothing on the rpc read path can ever drop a line. (INFERRED from the absence of a budget; consistent
> with the envelope recon.)

---

## 2. THE THREE ENVELOPES

All three share the same stream. A client demultiplexes on the top-level `type` field
(`rpc_client.py:508`): `type == "response"` ⇒ envelope B; anything else ⇒ envelope C.

### 2.A COMMAND (client → server)

```
{ "type": <discriminator>, "id"?: <string>, ...<command-specific fields> }
```

- `type` — **required**, string, one of the 29 discriminators in §6. Absent or non-string ⇒ parse error.
- `id` — **optional**. See §3.
- Field **spelling is camelCase on the wire** for multi-word fields, per an explicit per-command map
  (`rpc_types.py:381-390`):

  | command | Python field | wire key |
  |---|---|---|
  | `set_model` | `model_id` | `modelId` |
  | `new_session` | `parent_session` | `parentSession` |
  | `compact` | `custom_instructions` | `customInstructions` |
  | `export_html` | `output_path` | `outputPath` |
  | `switch_session` | `session_path` | `sessionPath` |
  | `fork` | `entry_id` | `entryId` |
  | `prompt` | `streaming_behavior` | `streamingBehavior` |

  Single-word fields (`message`, `mode`, `enabled`, `command`, `name`, `level`, `provider`, `images`) are
  identical either way.
- **The parse is forgiving in one direction only:** `parse_rpc_command` (`rpc_types.py:409-423`) tries the
  camel→snake inverse map first and otherwise passes the key through, so **snake_case is ALSO accepted**
  for the seven remapped fields. MEASURED: `{"id":"a5","type":"set_model","provider":"p","model_id":"..."}`
  passed parse and reached the handler.
- **The parse is strict about unknown keys.** `cls(**kwargs)` at `rpc_types.py:423` raises `TypeError`.
  MEASURED: `{"id":"a4","type":"prompt","message":"m","bogus_field":1}` →
  `{"type":"response","command":"parse","success":false,"error":"Failed to parse command: RpcCommandPrompt.__init__() got an unexpected keyword argument 'bogus_field'","id":"a4"}`.
  **A client cannot send a field the server does not know.** Any protocol extension is therefore a hard
  break for every older server.
- **No type validation beyond arity.** `Literal[...]` annotations are not enforced at runtime — dataclasses
  do no coercion. `{"type":"set_auto_retry","enabled":"maybe"}` constructs fine. (INFERRED from
  `@dataclass(frozen=True)` with no `__post_init__`; MEASURED analogue: `id` accepted a JSON number, §3.5.)
- `RpcClient` serialises commands itself (`rpc_client.py:550-562`), dropping `None` values, and always
  places `id` first. `command_to_json` (`rpc_types.py:426-451`) is the symmetric helper: emits `type` first,
  drops `None`, applies the remap.

**Example (MEASURED, exact bytes a client sends):**
```
{"id": "req_1", "type": "prompt", "message": "hello"}\n
```

### 2.B RESPONSE (server → client)

Exactly one response per parsed command line. Two shapes, discriminated by `success`.

**Success** — `rpc_types.py:477-483`:
```
{ "type": "response", "command": <string>, "success": true, "id"?: <echo>, "data"?: <any> }
```
**Error** — `rpc_types.py:501-510`:
```
{ "type": "response", "command": <string>, "success": false, "error": <string>, "id"?: <echo> }
```

- **Key order is fixed by construction** and MEASURED:
  success = `type, command, success, id, data`; error = `type, command, success, error, id`.
  (JSON semantics do not depend on it, but golden-file tests will.)
- `id` is **omitted entirely** when the request carried none — never `null` (`rpc_types.py:479-480`, `:508-509`).
- `data` is **omitted entirely** when it is `None` (`rpc_types.py:481-482`).
  **This is a genuine ambiguity:** `cycle_model` legitimately returns "no rotation possible" as `data=None`
  (`rpc_mode.py:681-682`), and `cycle_thinking_level` does the same at `rpc_mode.py:1011-1012`.
  On the wire, "no data" and "the answer is null" are the same record.
- `command` echoes the request's `type` — **except on parse failure**, where it is the literal string
  `"parse"` regardless of what the client claimed (`rpc_mode.py:1765-1769`, `:2030-2043`).
- `error` is a **free-text string**. See §4.

**Spelling: `data` payloads are camelCase.** This is the boundary. Examples:
- `get_state` → `RpcSessionState.to_json()` (`rpc_types.py:578-595`): `thinkingLevel`, `isStreaming`,
  `isCompacting`, `steeringMode`, `followUpMode`, `sessionFile`, `sessionId`, `sessionName`,
  `autoCompactionEnabled`, `autoRetryEnabled`, `messageCount`, `pendingMessageCount`, `model`.
- `get_commands` → `RpcSlashCommand.to_json()` (`rpc_types.py:630-636`): `name`, `source`, `description`, `sourceInfo`.
- `set_model` / `cycle_model` / `get_available_models` → `_model_to_dict` (`rpc_mode.py:573`), documented at
  `rpc_mode.py:401-406` as "emit the `Model` record verbatim in camelCase".

**…but three `data` payloads are snake_case**, because they are `dataclasses.asdict` of kernel dataclasses
rather than hand-written `to_json`:
- `get_messages` → `{"messages": [_dataclass_to_dict(m) …]}` (`rpc_mode.py:448-450`)
- `compact` → `_dataclass_to_dict(result)` (`rpc_mode.py:467-470`)
- `get_session_stats` → `_session_stats_to_dict(stats)` (`rpc_mode.py:1194-1195`, helper at `:1123`)

**This is the exact camelCase/snake_case boundary, and it is not clean.** (MEASURED — read all six sites.)

### 2.C EVENT (server → client, unsolicited)

```
{ ...<kernel dataclass fields, snake_case>, "type": <event discriminator> }
```

Produced by `_event_to_dict` = `dataclasses.asdict(event)` (`rpc_mode.py:271-283`; `_dataclass_to_dict` at
`:258-268`) and written by `_output` at `rpc_mode.py:1935`. There is **no serializer, no field map, and
therefore no place to put a version or an id**.

- **`type` is the LAST key**, because `type` is declared last in every kernel event dataclass and
  `asdict` preserves declaration order. Contrast the response envelope, where `type` is first.
  MEASURED: `{"tool_call_id": "tc_1", "tool_name": "Bash", "args": {...}, "type": "tool_execution_start"}`.
- **All event field names are snake_case.** The only camelCase islands are *content-block discriminators*
  inside message payloads — `"toolCall"` alongside `"text"` / `"image"` / `"thinking"`
  (`packages/aelix-ai/src/aelix_ai/messages.py:42-93`) — which survive because they are `Literal` **values**,
  not field names.
- Before emitting, the server does a `json.dumps` serializability probe; if it fails the whole event is
  **replaced** by `{"type": <event.type>}` with every field discarded (`rpc_mode.py:1931-1934`). Silent,
  lossy, and invisible to the client. (MEASURED — read.)
- On `BrokenPipeError` the server sets `shutdown_event` and stops (`rpc_mode.py:1936-1940`).
  Any other exception in the event pipe is swallowed (`:1941-1943`).

**The 14 event `type` values** (`packages/aelix-agent-core/src/aelix_agent_core/types.py:165-290`):

| `type` | fields (snake_case) |
|---|---|
| `agent_start` | — (`:166-167`) |
| `turn_start` | — (`:171-172`) |
| `message_start` | `message` (`:176-178`) |
| `message_update` | `message`, `assistant_message_event` (`:182-185`) |
| `message_end` | `message` (`:189-191`) |
| `tool_execution_start` | `tool_call_id`, `tool_name`, `args` (`:195-199`) |
| `tool_execution_update` | `tool_call_id`, `partial_result`, `tool_name`, `args` (`:203-208`) |
| `tool_execution_end` | `tool_call_id`, `result`, `tool_name`, `is_error` (`:212-217`) |
| `turn_end` | `message`, `tool_results` (`:221-224`) |
| `agent_end` | `messages` (`:228-230`) |
| `auto_retry_start` | `attempt`, `max_attempts`, `delay_ms`, `error_message` (`:238-243`) |
| `auto_retry_end` | `success`, `attempt`, `final_error` (`:247-251`) |
| `compaction_start` | `reason` (`:260-262`) |
| `compaction_end` | `reason`, `result`, `aborted`, `will_retry`, `error_message` (`:266-272`) |

**Trap:** `tool_execution_start.args` carries the tool arguments, while the `toolCall` **content block**
inside a message carries the same data under `input` (`messages.py:42-93`). Reading the wrong key yields
`{}`, not an error.

**No session header.** `--mode json` emits a typeless first line when a session exists
(`print_mode.py:174-185`); `--mode rpc` emits **nothing** of the kind — there are exactly four `_output`
call sites in the whole server (`rpc_mode.py:1935`, `:2030`, `:2038`, `:2061`) and none is a header.
(MEASURED — `grep -n "_output(" rpc_mode.py`.)

### 2.4 The two spellings on one fd — summary

| Record class | envelope keys | payload keys |
|---|---|---|
| response | snake-ish literals `type/command/success/error/id/data` | **camelCase**, except `get_messages` / `compact` / `get_session_stats` which are snake_case |
| event | snake_case, `type` last | snake_case, except the `toolCall` literal value |

A client that assumes one spelling for the whole stream will be wrong.

---

## 3. IDENTITY (`id`)

### 3.1 What it means
`id` is a **client-allocated request correlator**, echoed verbatim by the server onto the response.
It is not a session id, not a message id, not a sequence number, and it never appears on an event.

### 3.2 Who allocates it
The **client**, exclusively. `RpcClient._send` mints `f"req_{next(self._next_id)}"` from a monotonic
`itertools.count(1)` (`rpc_client.py:104`, `:550`) — i.e. `req_1`, `req_2`, … The format is a client
convention; the server never parses or validates it. The server never mints an id.

### 3.3 Is it required?
**No.** `id` is `str | None = None` on every one of the 29 command dataclasses (e.g. `rpc_types.py:63`,
`:91`, `:108`). A command without `id` is executed normally and the response simply omits the key.
MEASURED: `{"type":"get_state"}` (no id) →
`{"type":"response","command":"get_state","success":false,"error":"…"}` — no `id` key at all.

For `RpcClient` this is fatal in practice: `_handle_stdout_line` only resolves a pending future when
`isinstance(request_id, str) and request_id in self._pending_requests` (`rpc_client.py:510`). An id-less
response is delivered to the **event listeners** instead (`:532-536`), and the originating request
(if any) hangs until its 30 s send timeout. (MEASURED — read; the id-less path is exercised by the probe.)

### 3.4 Are events correlated to anything?
**No. Nothing.** Events carry no `id`, no request id, no turn index, no sequence number, no timestamp
(§2.C). Turn completion is signalled *only* by the arrival of an event whose `type == "agent_end"`
(`rpc_client.py:395`, `:415`, `:441`). With two prompts in flight there is no way to attribute an event
to either. (MEASURED — read the full event union and the whole emit path.)

### 3.5 Duplicate / unknown / malformed id

| Case | Server behaviour | Client behaviour |
|---|---|---|
| **Duplicate id** (same `id` on two commands) | **Accepted silently.** The server is entirely stateless w.r.t. `id` — it echoes what it was given. MEASURED: two `{"id":"req_1","type":"get_state"}` lines produced two responses both carrying `"id":"req_1"`. | `_pending_requests[request_id] = future` (`rpc_client.py:560`) **overwrites** the earlier future, which is then never resolved and never cancelled → it leaks until its own 30 s timeout. (INFERRED — `RpcClient` never mints a duplicate, so this is unreachable from the in-tree client.) |
| **Unknown / stale id on a response** | n/a | Logged to the client's **stderr** as `[rpc-client] stale response id=… command=… (request already completed/timed out)` (`rpc_client.py:519-529`), then **falls through to the event listeners** (`:530-536`). A stale response is therefore delivered to code expecting events. |
| **Non-string id** (JSON number, object, …) | **Echoed verbatim with its original type.** MEASURED: `{"id":42,"type":"get_state"}` → `…,"id": 42}` — a JSON *number* on a field declared `str \| None`. No validation anywhere. | Never matches (`isinstance(request_id, str)` fails) → silently routed to event listeners with no stale-id log. |
| **id absent on a parse-failure line** | If the line parsed as a JSON object, `payload.get("id")` is echoed **only when it is a `str`** (`rpc_mode.py:1766`). If the line was not JSON at all, or not an object, no id is echoed (`:2030-2043`). MEASURED both. | — |

### 3.6 Ordering — the id is the *only* ordering guarantee, and there is none otherwise
`_on_line` spawns **one detached `asyncio.Task` per command** (`rpc_mode.py:2051-2073`), so responses
may be emitted **out of request order**. Worse, MEASURED: **parse errors jump the queue.** Parse-failure
responses are written *synchronously* inside `_on_line` (`rpc_mode.py:2030`, `:2038`) while every valid
command's response is written from a task. In the probe, two malformed lines that were **last** in the
input produced the **first** two output records. A client MUST correlate by `id` and MUST NOT assume
request order.

---

## 4. ERRORS

### 4.1 Is there a code space?
**No.** There is exactly one machine-readable bit — `success: false` — and one human-readable string,
`error`. No numeric code, no symbolic code, no category, no `data` on the error envelope, no retry hint.
`RpcClientError` (`rpc_client.py:48-58`) preserves only `command` + the verbatim message.
(MEASURED — `rpc_types.py:486-510` is the entire error type.)

### 4.2 Every way the server reports failure

| # | Trigger | `command` | `error` (exact) | Site |
|---|---|---|---|---|
| E1 | Line is not valid JSON | `"parse"` | `Failed to parse command: {json exc}` | `rpc_mode.py:2029-2035` |
| E2 | Line is valid JSON but not an object | `"parse"` | `Command payload must be a JSON object` | `rpc_mode.py:2037-2043` |
| E3 | `type` missing or non-string | `"parse"` | `Failed to parse command: RpcCommand payload missing 'type' field: {payload!r}` | `rpc_types.py:404` → `rpc_mode.py:1765-1769` |
| E4 | Unknown `type` | `"parse"` | `Failed to parse command: Unknown RpcCommand type: {type!r}` | `rpc_types.py:407` → `rpc_mode.py:1768` |
| E5 | Missing required field / unknown extra field | `"parse"` | `Failed to parse command: {TypeError from cls(**kwargs)}` — e.g. `RpcCommandPrompt.__init__() missing 1 required positional argument: 'message'` | `rpc_types.py:423` → `rpc_mode.py:1768` |
| E6 | Discriminator known but absent from the dispatch table | echoes `cmd.type` | `Unknown command: {cmd.type}` | `rpc_mode.py:1771-1776` |
| E7 | **Any** unhandled exception inside a handler | echoes `cmd.type` | `str(exc)` — a raw Python exception message | `rpc_mode.py:1777-1784` |
| E8 | `set_session_name` with empty/blank name | `set_session_name` | `Session name cannot be empty` | `rpc_mode.py:548` |
| E9 | `set_session_name` with no session attached | `set_session_name` | `set_session_name() requires options.session to be attached` | `rpc_mode.py:558` |
| E10 | `set_model` with no `ModelRegistry` | `set_model` | `set_model requires a ModelRegistry — none configured` (note: **U+2014 em dash**) | `rpc_mode.py:626` |
| E11 | `set_model` model not in the auth-filtered registry | `set_model` | `Model not found: {provider}/{model_id}` | `rpc_mode.py:642` |
| E12 | `cycle_model` with no `ModelRegistry` | `cycle_model` | `cycle_model requires a ModelRegistry — none configured` | `rpc_mode.py:673` |
| E13 | `get_available_models` with no `ModelRegistry` | `get_available_models` | `get_available_models requires a ModelRegistry — none configured` | `rpc_mode.py:734` |
| E14 | `set_steering_mode` rejected by harness | `set_steering_mode` | `str(exc)` | `rpc_mode.py:1039` |
| E15 | `set_follow_up_mode` rejected by harness | `set_follow_up_mode` | `str(exc)` | `rpc_mode.py:1056` |
| E16 | `fork` raises `ValueError` | `fork` | `str(exc)` | `rpc_mode.py:1398` |
| E17 | `clone` with no current entry (two distinct guards) | `clone` | `Cannot clone session: no current entry selected` | `rpc_mode.py:1444`, `:1451` |
| E18 | `clone` raises `ValueError` | `clone` | `str(exc)` | `rpc_mode.py:1461` |
| E19 | `compact` raises | `compact` | `str(exc)` | `rpc_mode.py:465` |
| E20 | Runtime-host command with no `AgentSessionRuntime` bound | echoes cmd type | `{cmd_type} requires an AgentSessionRuntime — none configured (pass ``runtime_host=`` to build_dispatch_table)` — note the **literal double backticks in the user-facing string** | `rpc_mode.py:1677-1683` |
| E21 | Deferred-command stub | echoes cmd type | `{cmd_type} not implemented ({owner_adr})` | `rpc_mode.py:1485-1489` — **DEAD CODE**: `DEFERRED_COMMANDS = {}` (`rpc_mode.py:194`), so this factory is never invoked. |

MEASURED end-to-end: E1, E2, E3, E4, E5, E8, E10 and E7 (via `'FakeHarness' object has no attribute 'state'`
and `Failed to read session header /nope: [Errno 2] No such file or directory: '/nope'`).
E6…E21 read directly.

### 4.3 Consequences for a client author
- **E7 is the dominant path.** Most failures surface as a raw Python exception message with no stable
  wording, no code, and occasionally an internal type name (`'FakeHarness' object has no attribute 'state'`).
  Nothing may be matched on programmatically.
- **The `command` field is the only reliable dispatch key**, and even it collapses to `"parse"` for E1–E5.
- **Two error strings contain the em dash U+2014** (E10/E12/E13/E20) — a client matching on ASCII hyphens will miss.
- Errors are also written **out of band to stderr** in at least one case that produces *no* wire record at all:
  a failed prompt task prints `[rpc] prompt task failed: {exc!r}` to stderr (`rpc_mode.py:311-315`) and emits
  **nothing** on the wire — see §8.2.

### 4.4 Failures that are NOT reported at all

| Case | What happens | Site |
|---|---|---|
| Prompt task raises after the `success: true` ack | stderr only, **no wire record**, no terminator | `rpc_mode.py:308-320` |
| `abort` cancels a live turn | `success: true` for the abort, but **no terminal event** for the aborted turn | `rpc_mode.py:333-340`; harness swallows `CancelledError` (`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:4287-4294`) |
| Prompt rejected because the harness is busy | `success: true` is emitted **before** `harness.prompt` is even awaited | `rpc_mode.py:322-330` |
| Event is not JSON-serializable | event silently replaced by `{"type": …}` | `rpc_mode.py:1931-1934` |
| Any exception in the event pipe | swallowed | `rpc_mode.py:1941-1943` |
| `extension_ui_response` received | **dropped, no response** | `rpc_mode.py:2045-2049`; MEASURED — probe line produced zero output |

---

## 5. VERSIONING

**There is nothing on the wire that is a version.** MEASURED:

- `grep -rn "version" packages/aelix-coding-agent/src/aelix_coding_agent/rpc/` → **zero hits**.
- `grep -rni "handshake\|capabilit\|protocol_version\|protocolVersion\|schema_version"` over the same
  directory → **zero hits**.
- No command initiates a handshake; §6's roster contains no `hello`, `initialize`, or `capabilities`.
- No event carries a version (`types.py:165-290`, checked field by field).
- The response envelope has exactly six possible keys (§2.B); none is a version.
- The `RpcSessionState` payload has 13 keys (`rpc_types.py:578-595`); none is a version.
- **The first byte a client sees is whatever happens first** — there is no server greeting. The stream
  begins only in response to a command or a harness event.

`CONTRACT_VERSION` exists at
`packages/aelix-coding-agent/src/aelix_coding_agent/subagent_contract.py` but is a binding-time check
on a Python object; it is never transmitted and never read off a stream. (Re-verified: the rpc package
does not import it — no `version` hit in the grep above.)

### 5.1 `docs/contracts/`
Contains four schemas — `manifest`, `descriptor-envelope`, `primitives`, `slot-taxonomy`
(`docs/contracts/README.md:17-22`). **None describes the RPC wire.** They are generated from the Pydantic
models in `packages/aelix-agent-core/src/aelix_agent_core/contracts/` and cover the extension/descriptor
layer. (MEASURED — `ls docs/contracts/` and read the README.)

### 5.2 Does `packages/aelix-server` serve an RPC schema?
**No.** `GET /schemas/{name}` (`packages/aelix-server/src/aelix_server/app.py:42`, handler at
`packages/aelix-server/src/aelix_server/schemas.py:29-42`) serves exactly
`docs/contracts/{name}.schema.json` behind an `^[A-Za-z0-9_-]+$` allowlist — i.e. only the four files
above. The server exposes the RPC **transport** at `WS /rpc` (`app.py:46`) but publishes **no description
of what flows over it**. (MEASURED — read both files.)

### 5.3 Practical effect
A client cannot negotiate, cannot detect the peer's roster, and — because the command envelope is closed
(§2.A) — cannot probe by sending an optional field. The only available discovery is **trial and error
against the 29 discriminators**, distinguishing "unknown command" from "known but broken" by string-matching
the free-text `error`.

---

## 6. THE COMMAND SURFACE

29 discriminators, enumerated at `rpc_types.py:344-374` (`_RPC_COMMAND_REGISTRY`) and pinned as
`RPC_COMMAND_TYPES` (`rpc_types.py:814`).

**All 29 are handled.** `DEFERRED_COMMANDS = {}` (`rpc_mode.py:194`) and `SUPPORTED_COMMANDS`
(`rpc_mode.py:206-252`) lists all 29. The dispatch table is built at `rpc_mode.py:1572-1633` from three
arity classes.

| # | `type` | wire params (camelCase) | success `data` | arity class | notes |
|---|---|---|---|---|---|
| 1 | `prompt` | `message`, `images?`, `streamingBehavior?` | *(omitted)* | harness | **acks before running**; `images` and `streamingBehavior` are parsed then **DISCARDED** (`rpc_mode.py:309`) |
| 2 | `steer` | `message`, `images?` | *(omitted)* | harness | decodes `images` |
| 3 | `follow_up` | `message`, `images?` | *(omitted)* | harness | decodes `images` |
| 4 | `abort` | — | *(omitted)* | harness | no terminal event for the aborted turn |
| 5 | `new_session` | `parentSession?` | `{cancelled}` | runtime-host | `rpc_mode.py:374-375` |
| 6 | `get_state` | — | `RpcSessionState` (13 camelCase keys) | harness | `rpc_mode.py:436-437` |
| 7 | `set_model` | `provider`, `modelId` | `Model` (camelCase) | harness+registry | `rpc_mode.py:646` |
| 8 | `cycle_model` | — | `{model, thinkingLevel, isScoped}` — or `data` **omitted** when ≤1 model available | harness+registry | `rpc_mode.py:681`, `:707-714` |
| 9 | `get_available_models` | — | `{models: [Model…]}` | harness+registry | `rpc_mode.py:739-740` |
| 10 | `set_thinking_level` | `level` | *(omitted)* | harness | |
| 11 | `cycle_thinking_level` | — | `{level}`, or `data` **omitted** | harness | `rpc_mode.py:1011-1018` |
| 12 | `set_steering_mode` | `mode` (`all`\|`one-at-a-time`) | *(omitted)* | harness | |
| 13 | `set_follow_up_mode` | `mode` | *(omitted)* | harness | |
| 14 | `compact` | `customInstructions?` | `CompactResult` — **snake_case** | harness | `rpc_mode.py:467-470` |
| 15 | `set_auto_compaction` | `enabled` | *(omitted)* | harness | |
| 16 | `set_auto_retry` | `enabled` | *(omitted)* | harness | |
| 17 | `abort_retry` | — | *(omitted)* | harness | |
| 18 | `bash` | `command` | `{output, exitCode, cancelled, truncated, fullOutputPath?}` — camelCase; output tail-truncated to 256 lines / 32 KiB | harness | `rpc_mode.py:506-523` |
| 19 | `abort_bash` | — | *(omitted)* | harness | |
| 20 | `get_session_stats` | — | stats — **snake_case** | harness | `rpc_mode.py:1194-1195` |
| 21 | `export_html` | `outputPath?` | `{path}` | harness | `rpc_mode.py:1220-1221` |
| 22 | `switch_session` | `sessionPath` | `{cancelled}` | runtime-host | `rpc_mode.py:1360-1361` |
| 23 | `fork` | `entryId` | `{cancelled, text?}` | runtime-host | `rpc_mode.py:1400-1404` |
| 24 | `clone` | — | `{cancelled}` — `selected_text` deliberately dropped | runtime-host | `rpc_mode.py:1463-1468` |
| 25 | `get_fork_messages` | — | `{messages: [{…}]}` | harness | `rpc_mode.py:1273-1274` |
| 26 | `get_last_assistant_text` | — | `{text}`, or `{}` (empty object, **not** omitted) when there is none | harness | `rpc_mode.py:1297-1302` |
| 27 | `set_session_name` | `name` | *(omitted)* | harness | |
| 28 | `get_messages` | — | `{messages: […]}` — **snake_case** | harness | `rpc_mode.py:448-450` |
| 29 | `get_commands` | — | `{commands: [{name, source, description?, sourceInfo?}]}` | harness | `rpc_mode.py:912-913` |

### 6.0 The `images` sub-shape (only nested object a client constructs)

`images` is a JSON array; each entry MUST be an object carrying **`mimeType` and `data`, camelCase only**
(`rpc_mode.py:925-963`). Validation is strict and raises `ValueError`, surfaced by E7:
- non-object entry → `ImageContent[{i}] must be a dict, got {type}` (`rpc_mode.py:951`)
- missing key → `ImageContent[{i}] missing required 'mimeType' or 'data' field` (`rpc_mode.py:955`)

An empty or absent `images` becomes `None` and the harness takes its text-only path (`rpc_mode.py:945-946`).
**Only `steer` and `follow_up` decode it** (`rpc_mode.py:979`, `:991`); `prompt` accepts it at parse time and
throws it away (`rpc_mode.py:309`).

### 6.1 TYPES-ONLY — declared but never handled

**`extension_ui_request`** — nine methods, fully typed at `rpc_types.py:645-756`, allowlisted at
`rpc_types.py:760-772` (`select`, `confirm`, `input`, `editor`, `notify`, `setStatus`, `setWidget`,
`setTitle`, `set_editor_text`). **Never constructed anywhere outside `rpc_types.py`.**
MEASURED: `grep -rn "extension_ui_request\|RpcExtensionUIRequest" --include=*.py packages/ tests/`
returns only `rpc_types.py`, the re-exports in `rpc/__init__.py:59-68,121-130`, and a shape-pin test at
`tests/pi_parity/test_phase_4_4_strict_superset.py:24-32,265`. The comment at `rpc_types.py:641-642`
says so outright: **"TYPES ONLY — Sprint 6d ships the wire shape."**

**`extension_ui_response`** — three shapes at `rpc_types.py:778-809`. The server **recognises and
discards** them: `rpc_mode.py:2045-2049` returns immediately on `payload.get("type") == "extension_ui_response"`.
MEASURED: the probe's `extension_ui_response` line produced **zero output records** — no response, no error,
no ack.

**`RpcSessionState.from_json`** (`rpc_types.py:597-615`) and `parse_rpc_response` (`rpc_types.py:516-543`)
are client-side only; the server never parses a response.

### 6.2 Commands whose responses are dropped or misleading

| Command | Problem | Site |
|---|---|---|
| `prompt` | `success: true` is returned **before** `harness.prompt` is awaited. A busy-phase rejection, or any later failure, is reported as a **success**, and the turn then never terminates. | `rpc_mode.py:302-330` |
| `prompt` | `images` and `streamingBehavior` are accepted by the parser and then **silently ignored** — only `harness.prompt(cmd.message, source="rpc")` is called. `steer`/`follow_up` *do* decode images. | `rpc_mode.py:309` |
| `abort` | `success: true`, but the aborted turn emits no `agent_end` — a `wait_for_idle` client blocks the full 60 s. | `rpc_mode.py:339`; `harness/core.py:4287-4294` |
| `set_model` / `cycle_model` / `get_available_models` | **Dead on the stdio path**: `entry.py:2160-2166` does not pass `model_registry=`, so all three return E10/E12/E13. Also dead on the WS path (`rpc_ws.py:146-153` likewise omits it). | MEASURED — read both call sites |
| `extension_ui_response` | dropped, no response at all | `rpc_mode.py:2045-2049` |
| any command | a response arriving after the client's 30 s timeout is delivered to the **event** listeners | `rpc_client.py:519-536` |

---

## 7. DIRECTIONALITY

### 7.1 Measurement
The server has **exactly four** write sites, found by
`grep -n "_output(" packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py`:

| line | what it writes |
|---|---|
| `1879` | the `_output` definition itself |
| `1935` | an **event** (`_on_agent_event`) |
| `2030` | an **error response** (`command="parse"`, E1) |
| `2038` | an **error response** (`command="parse"`, E2) |
| `2061` | the **response** to a dispatched command |

**None of these is a request.** There is no code path by which the server asks the client anything.

### 7.2 The client cannot answer one either
`RpcClient._handle_stdout_line` (`rpc_client.py:496-536`) has exactly two branches: `type == "response"`
(correlate by id) and everything else (broadcast to event listeners). There is no `extension_ui_request`
branch, no reply path, and `_send` (`rpc_client.py:538-581`) always allocates a **new** `req_N` id — it
cannot be made to answer an id the server chose.

### 7.3 Verdict
**The current protocol is strictly half-duplex at the request level: client → server requests only.**
Server → client traffic is limited to (a) responses correlated to a client request and (b) unsolicited,
uncorrelated events.

**The deferred approval back-channel is NOT expressible in the current protocol.** It requires
server→client request/response, which would mean:
1. Emitting `{"type":"extension_ui_request","id":…,"method":"confirm",…}` — the type exists
   (`rpc_types.py:657-666`) but has zero construction sites (§6.1).
2. Handling `{"type":"extension_ui_response","id":…,"confirmed":…}` — currently a hard `return`
   at `rpc_mode.py:2049`.
3. A **server-side** pending-request map keyed by a **server-allocated** id. None exists; the server is
   entirely stateless with respect to `id` (§3.5).
4. A client-side dispatcher for inbound requests. None exists (§7.2).

The transport itself is genuinely bidirectional and already full-duplex — an rpc child's stdin is a live
JSONL channel (`rpc_mode.py:1884-1889` + the chunked pump at `:2078-2092`), unlike the print-mode child
whose stdin is read to EOF (gated at `packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:1346`
— **re-verified at `da61337`**). So this is a **protocol** gap, not a transport gap. All four items above
are additive; none requires touching the transport.

---

## 8. TURN SEMANTICS (what a client must do to run one turn)

### 8.1 The only terminator
`agent_end`. `RpcClient.wait_for_idle` / `collect_events` / `prompt_and_wait` all resolve on
`event.get("type") == "agent_end"` and nothing else (`rpc_client.py:395`, `:415`, `:441`).
Default timeout 60 s (`DEFAULT_WAIT_FOR_IDLE_MS`, `rpc_client.py:87`).

### 8.2 The terminator is not emitted on three failure paths (MEASURED by reading; each costs a full 60 s)

| Path | Why no `agent_end` |
|---|---|
| Prompt task raises | The handler catches, prints to stderr, and emits nothing. The code documents its own gap at `rpc_mode.py:316-320` — "Sprint 6f wires that bridge" — which never happened. |
| Turn aborted | `harness.abort()` → the harness swallows `CancelledError` and returns `[]` (`harness/core.py:4287-4294`), so the loop's `AgentEndEvent` construction is never reached. |
| Prompt rejected as busy | The ack precedes the await (`rpc_mode.py:322-330`). |

### 8.3 Other client-visible constants (`rpc_client.py:86-94`)
`DEFAULT_SEND_TIMEOUT_MS = 30_000`; `DEFAULT_WAIT_FOR_IDLE_MS = 60_000`; `STARTUP_GRACE_MS = 100`;
`SHUTDOWN_SIGTERM_TIMEOUT_MS = 1_000`; `STDERR_MAX_BYTES = 10 MiB`.
`stop()` is SIGTERM → 1 s → SIGKILL → a **further bounded 5 s reap** (`rpc_client.py:179-193`), so the
worst case is ~6 s, not 1 s.

### 8.4 stdout/stderr discipline
`--mode rpc` redirects the child's `sys.stdout` into `sys.stderr` for its whole life
(`rpc_mode.py:1847-1849`) and routes RPC writes through the captured real fd (`:1853-1875`), so a stray
`print()` from a tool or extension **cannot** corrupt the JSONL stream — but it **does** land in stderr,
which is where diagnostics also go. `--mode json` declines the redirect (per the envelope recon); the
trap is inverted, not removed.

### 8.5 Termination
The server returns when **either** `shutdown_event` fires (SIGTERM/SIGHUP, or a `BrokenPipeError` on
stdout) **or** stdin reaches EOF (`rpc_mode.py:2095-2106`). Signal handlers are installed for SIGTERM
and SIGHUP only — **never SIGINT** (`rpc_mode.py:2006`). In-flight command tasks are drained before
teardown (`:2104-2106`). `entry.py:2167` then returns `0` unconditionally, so **`exit_code` carries no
information** about the run.

---

## 9. DEFECTS FOUND WHILE MEASURING

### 9.1 CRITICAL — `aelix --mode rpc` does not start (MEASURED)

`entry.py:2160-2166` calls:
```python
await run_rpc_mode(
    harness,
    runtime_host=runtime,      # non-None: built at entry.py:2091
    harness_factory=_harness_factory,
    repo=repo,                 # non-None: built at entry.py:1368
    fs=fs,                     # non-None: built at entry.py:1362
)
```
`run_rpc_mode` rejects exactly that combination at `rpc_mode.py:1906-1914`:
```python
elif repo is not None or fs is not None:
    raise RuntimeError(
        "repo and fs must not be supplied when runtime_host is explicit — the runtime owns them"
    )
```

**Reproduction (MEASURED, `da61337`):**
```
$ printf '{"id":"r1","type":"get_state"}\n' | python -m aelix_coding_agent --mode rpc
Traceback (most recent call last):
  ...
  File ".../cli/entry.py", line 2160, in _async_main
    await run_rpc_mode(
  File ".../rpc/rpc_mode.py", line 1911, in run_rpc_mode
    raise RuntimeError(
RuntimeError: repo and fs must not be supplied when runtime_host is explicit — the runtime owns them
```

Zero bytes reach stdout. **There is no working stdio RPC server in the product today.** The only live
consumer of this protocol is `packages/aelix-server`'s `WS /rpc`, which does *not* pass `repo`/`fs`
(`rpc_ws.py:146-153`) and therefore works.

This is precisely the hole the recon notes predicted from a different direction: no test in the repo
spawns a real `--mode rpc` child, so nothing caught it. Note also that `rpc_client.py:466`'s default argv
targets `-m aelix` (the mock-stream demo), not `-m aelix_coding_agent` — so even the in-tree client would
not have exercised this path.

### 9.2 HIGH — the three model commands are dead on both live paths (MEASURED)
Neither `entry.py:2160-2166` nor `rpc_ws.py:146-153` passes `model_registry=`. `build_dispatch_table`
therefore binds `None` (`rpc_mode.py:1617-1618`) and `set_model` / `cycle_model` / `get_available_models`
return E10 / E12 / E13 unconditionally. This **contradicts** `rpc-sprint-recon-transport.md`'s claim that
"entry.py's production path supplies one" — that claim is false at `da61337`.

### 9.3 MEDIUM — parse-error responses jump the queue (MEASURED, §3.6).
### 9.4 MEDIUM — non-string `id` is echoed verbatim as a non-string (MEASURED, §3.5), violating the `str | None` declaration and guaranteeing the client cannot correlate it.
### 9.5 LOW — E20's user-facing error string contains literal reStructuredText double backticks (`rpc_mode.py:1682`).
### 9.6 LOW — `_make_deferred_handler` (`rpc_mode.py:1475-1491`) is unreachable dead code (`DEFERRED_COMMANDS = {}`).

---

## 10. WHAT A THIRD-PARTY CLIENT AUTHOR MUST IMPLEMENT

1. Split the byte stream on `\n` **only**; never on U+2028/U+2029. Strip one trailing `\r`. Decode UTF-8
   incrementally across chunk boundaries. Emit the residual buffer at EOF.
2. Impose your **own** per-line byte budget. The peer imposes none in either direction.
3. Always send an `id`, always a **JSON string**, always unique. Without one, responses are
   indistinguishable from events.
4. Demultiplex on top-level `type`: `"response"` → correlate by `id`; anything else → event.
5. Expect responses **out of order**, and expect `command:"parse"` errors to arrive **before** responses to
   commands sent earlier.
6. Expect `data` and `id` keys to be **absent** rather than `null`. Treat "absent `data`" and "`data` is
   null" as the same thing — the protocol cannot distinguish them.
7. Read `data` as camelCase — **except** for `get_messages`, `compact`, `get_session_stats`, which are snake_case.
8. Read events as snake_case, with `type` as the last key, and remember the `toolCall` content-block
   discriminator is the one camelCase island.
9. Never send a field the server does not know. There is no forward compatibility and no way to detect
   the peer's version.
10. Treat `agent_end` as the sole turn terminator **and implement your own deadline**, because it is not
    emitted on abort, on prompt failure, or on busy rejection.
11. Do not derive success from the child's exit code — an rpc child never exits on its own and
    `entry.py:2167` returns `0` unconditionally.
12. Do not expect the server to ever ask you anything.

---

## 11. CROSS-REFERENCES

- `.omc/specs/rpc-sprint-kickoff-handoff.md` — sprint framing and the product-core scoping fork.
- `.omc/specs/rpc-sprint-recon-transport.md` — `RpcClient`/`rpc_mode` behaviour, 8 doc-vs-code contradictions.
- `.omc/specs/rpc-sprint-recon-envelope.md` — ADR-0198's envelope contract and the parity-test field classification.
- `docs/decisions/0198-print-mode-json-envelope-contract.md` — the `--mode json` half of the envelope contract.
- The rpc half of that record **does not exist**; this note is the measurement it would be written from.
