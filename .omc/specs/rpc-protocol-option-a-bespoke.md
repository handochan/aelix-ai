# Option A — "BESPOKE, FIXED": keep pi's wire, close the defects in place

> **Written:** 2026-07-29, RPC-sprint design phase. Tree at `main` = `da61337`. **Nothing was edited.**
> Every `path:line` below was **re-opened against `da61337`**, not copied from an older note.
> Where a background note's number had drifted, the new number is used and the drift is called out.
>
> **MEASURED** = I ran it or opened it. **INFERRED** = reasoning on top of measured facts.
> **DESIGN** = a proposal, not a fact about the tree.
>
> Companion ground truth: `rpc-protocol-recon-wire.md`, `rpc-protocol-recon-pi.md`,
> `rpc-protocol-recon-consumers.md`, `rpc-protocol-recon-standards.md`,
> `rpc-sprint-kickoff-handoff.md`, `rpc-sprint-recon-transport.md`, `rpc-sprint-recon-envelope.md`.

---

## 0. The option in one paragraph

Keep pi's JSONL envelope grammar exactly as it is. Adopt no standard. Fix the seven measured
defects **inside** that grammar by (i) adding an optional, namespaced `_meta` sidecar to every
server→client record — never to a command; (ii) adding an optional `code` symbol to the error
envelope; (iii) making the RPC server itself the **turn-closure authority**, so `agent_end` is
guaranteed on every terminated turn including abort and post-acceptance failure; (iv) turning the
fire-and-forget prompt ack into a real preflight ack that can say `success:false`; (v) adding a
**30th command, `hello`**, plus an unsolicited `server_hello` first line, as the version and
capability handshake; (vi) **relaxing the command parser to ignore unknown keys**, which is the
single change that makes every *future* protocol change cheap; and (vii) declaring — and
machine-checking — the camelCase/snake_case boundary rather than moving it. Product-core
(`rpc/*.py`) is edited. The kernel is not. Spawn/consent/cap policy stays in `aelix_agents`.

**Name:** `aelix rpc wire v2` — a strict superset of the pi-shaped v1.

---

## 1. What Option A is allowed to touch, and what it is not

| Band | Files | Verdict |
|---|---|---|
| **Kernel** (`packages/aelix-agent-core/`) | `harness/core.py`, `types.py`, `loop.py` | **OUT.** Kickoff handoff §4: "The kernel is never edited." Option A must reach its goals without it. §7 below prices exactly what that costs. |
| **product-core** (`packages/aelix-coding-agent/src/aelix_coding_agent/`) | `rpc/rpc_mode.py`, `rpc/rpc_types.py`, `rpc/rpc_client.py`, `rpc/_jsonl.py`, `cli/entry.py` | **IN**, per the owner's confirmation that RPC belongs in core. |
| **extension** (`packages/aelix-coding-agent/src/aelix_agents/`) | new `rpc_channel.py`, `runtime.py`, `envelope.py`, `stream.py` | **IN.** All spawn/consent/depth/cap policy lives here and only here. |

**The band gate stays green either way, and that is not the argument.** MEASURED:
`tests/agents/test_p2_band_boundaries.py:47-50` already lists `rpc/rpc_client.py` on
`_SPAWN_ALLOWLIST`, and `:38-39` forbids only the strings `subagent`/`aelix_agents` **in the
kernel**. Nothing in Option A adds a spawn call to a new product-core file, and nothing puts the
strings `AELIX_SUBAGENT_DEPTH`, `--no-agents`, or a delegation cap into product-core. The
containment *seam* added in §5.6 is parameters; the *values* are passed from `aelix_agents`.

---

## 2. The defect list Option A must close (all re-verified at `da61337`)

| # | Defect | Re-verified evidence |
|---|---|---|
| **D1a** | Turn aborted ⇒ no terminator | `rpc/rpc_mode.py:333-340` returns `RpcSuccessResponse` and emits nothing; the kernel swallows the cancel at `harness/core.py:4287-4294` (`except asyncio.CancelledError:` at `:4287`, `if self._abort_requested: return []` at `:4293-4294`) and so never reaches the ported pi closure block at `:4298-4316`. |
| **D1b** | Prompt task raises ⇒ no terminator | `rpc/rpc_mode.py:308-320`: `except Exception` prints `[rpc] prompt task failed: {exc!r}` to stderr and emits **nothing**. The comment at `:316-320` blames "Sprint 6f", which never happened. The kernel's closure block is filtered to `except AgentHarnessError` (`core.py:4298`), so anything else escapes with no terminator. |
| **D1c** | Busy rejection ⇒ acked as success, then no terminator | `rpc/rpc_mode.py:322-330`: `asyncio.create_task(_run())` at `:322`, `return RpcSuccessResponse(...)` at `:330`. The harness's busy guard is `if self._phase != "idle":` at `harness/core.py:1189`, raising `AgentHarnessError("busy", …)` at `:1190-1194`. |
| **D2** | Fire-and-forget ack | same site; the ack is written before the task's first step. |
| **D3** | No event correlation | `rpc/rpc_mode.py:1927-1935` emits `_event_to_dict(event)` (= `dataclasses.asdict`, `:258-268`) verbatim. No id, no turn, no seq, no timestamp. |
| **D4** | No error code space | `rpc/rpc_types.py:487-511` — `RpcErrorResponse` is `{command, error, id?}` and `to_json` at `:501-510` emits four keys. Free text only. |
| **D5** | No version / capability handshake | 29 discriminators at `rpc/rpc_types.py:344-374`; none is `hello`/`initialize`. `grep -rn "version" rpc/` → 0 hits (MEASURED, re-run). |
| **D6** | Two spellings on one fd | camelCase `data` via `_camel` (`rpc_types.py:26`) + `_COMMAND_FIELD_REMAP` (`:381-390`), **except** three payloads that are `dataclasses.asdict` and therefore snake_case: `get_messages` (`rpc_mode.py:448-450`), `compact` (`:467-470`), `get_session_stats` (`:1194-1195`). Events are snake_case with `type` last. |
| **D7** | No server→client request direction | `rpc/rpc_mode.py:2048-2049` — `if payload.get("type") == "extension_ui_response": return`. Bare drop. Four `_output` sites total (`:1935`, `:2030`, `:2038`, `:2061`); none is a request. |
| **D8** | Reader contract violated | `rpc/rpc_client.py:111-127` passes no `limit=`; `rpc/_jsonl.py:50-72` has an unbounded `str` buffer, no budget, no counter ⇒ `SubagentResult.dropped_lines` structurally always 0 on rpc. |
| **D9** | `prompt` drops `images` and `streamingBehavior` | `rpc/rpc_mode.py:309` calls `harness.prompt(cmd.message, source="rpc")` only, while `RpcCommandPrompt` declares `images` and `streaming_behavior: Literal["steer","followUp"] \| None` (`rpc_types.py:57-64`), and `_handle_steer`/`_handle_follow_up` (`:966`, `:984`) both decode images. |
| **D10** | Command envelope is closed | `rpc_types.py:423` — `cls(**kwargs)`. One unknown key ⇒ `TypeError` ⇒ `command:"parse"` error. **No client can ever add a field.** |

### 2.1 One correction to a background note

`rpc-protocol-recon-pi.md` §3 item 5 / §5 says aelix "inherits the *pinned* buggy shape" of pi's
uncorrelated unknown-command error and that adopting v0.80.2's `error(id, …)` fix is free work.
**MEASURED at `da61337`: aelix already echoes the id on both paths.** An unknown `type` goes
through `parse_rpc_command` → `ValueError` → `rpc_mode.py:1765-1769`, which echoes
`payload.get("id")` when it is a `str`; a known-but-unwired discriminator goes to
`rpc_mode.py:1771-1776`, which echoes `cmd.id`. There is nothing to adopt. Option A budgets zero
for it. (This does not change the pi note's conclusions, only its work list.)

---

## 3. The design, concretely

### 3.0 The two structural rules everything else follows from

> **R1 — The server→client direction is open; the client→server direction is closed.**
> A server may add keys to any record it writes, because both `RpcClient._handle_stdout_line`
> (`rpc_client.py:496-536`) and `parse_rpc_response` (`rpc_types.py:516-543`) read by `.get()` and
> ignore unknown keys — MEASURED. A client may **not** add keys to a command, because
> `parse_rpc_command` ends in `cls(**kwargs)` (`rpc_types.py:423`) — MEASURED.
> **Therefore: every additive change rides on a server record or on a NEW command discriminator.**
> **R2 fixes the other half.**

> **R2 — Open the command envelope, once.** Filter `kwargs` to `cls.__dataclass_fields__` before
> construction in `parse_rpc_command` (`rpc_types.py:409-423`). Unknown keys are dropped;
> **missing required fields still raise**, so E5's useful half survives. This is ~3 lines and it is
> the highest-leverage change in the whole option: it is what makes the *next* protocol revision
> cheap instead of another break. Cost: a typo'd field name degrades from
> `unexpected keyword argument 'mesage'` to `missing 1 required positional argument: 'message'` —
> still an error, slightly less pointed. **DESIGN.**

### 3.1 `_meta` — the sidecar (closes D3, and carries D1's provenance)

A single reserved key, `_meta`, added by `_output` (`rpc_mode.py:1879-1880`) to **every**
server→client record. Interior keys are **always camelCase**, because `_meta` is not part of any
record's own field space — exactly the status the `toolCall` content-block discriminator already
has (`packages/aelix-ai/src/aelix_ai/messages.py:42-93`). The name is deliberate: it maps
one-to-one onto ACP's `_meta` extension slot, so a later adapter is a rename, not a redesign
(`rpc-protocol-recon-standards.md` §3.2).

```
"_meta": {
  "seq":   <int>,              // monotonic from 0, per server process, EVERY record
  "turn":  <int>,              // present only on records belonging to a turn
  "synthetic": true,           // present only on server-synthesised terminal events
  "stopReason": "end_turn" | "error" | "cancelled" | "refusal" | "max_tokens",
  "code":  "<symbol>",         // present with stopReason on synthetic terminators
  "detail": "<string>"         // free text, diagnostics only, never matched on
}
```

`stopReason`'s value set is **ACP's `StopReason`** verbatim (`rpc-protocol-recon-standards.md`
§3.2). It costs nothing today and is an externally validated taxonomy rather than one invented in
this sprint.

`seq` is the ordering repair. §3.6 of the wire recon MEASURED that parse errors jump the queue
(they are written synchronously in `_on_line` at `rpc_mode.py:2030`/`:2038` while every valid
command's response is written from a detached task at `:2061`). `seq` is assigned at the single
write site, so it is the **emission** order and it is total. A client can finally detect a gap.

**`_meta` is NOT added inside `RpcSuccessResponse.to_json` / `RpcErrorResponse.to_json`**
(`rpc_types.py:477-483`, `:501-510`). MEASURED reason: `tests/rpc/test_rpc_types.py:114`, `:123`,
`:134` assert exact-dict equality on `to_json()`. Putting `_meta` in `_output` instead keeps those
three green and keeps the dataclasses pure. **DESIGN, with a measured constraint behind it.**

### 3.2 Turn closure — the server becomes the closure authority (closes D1a/D1b/D1c)

A `_TurnTracker` object inside `run_rpc_mode`. It has exactly three inputs and one output.

- **Input 1** — `_on_agent_event` (`rpc_mode.py:1927`) tells it every event `type` it emits. An
  `agent_end` from the kernel marks the current turn **closed**; the tracker then never
  synthesises for that turn. This is the de-sync guard: if the kernel bug at `core.py:4287-4298`
  is ever fixed, the client still sees exactly one `agent_end`.
- **Input 2** — `_handle_prompt` opens a turn (`turn += 1`) at preflight success.
- **Input 3** — `_handle_abort` marks the open turn `abort_requested`.
- **Output** — when the prompt task completes and the turn is not closed, the tracker emits a
  synthetic terminator through `_output`:

```json
{"messages": [...], "type": "agent_end",
 "_meta": {"seq": 41, "turn": 3, "synthetic": true,
           "stopReason": "cancelled", "code": "aborted"}}
```

`messages` is `list(harness.state.messages)` — `AgentHarness.state` is a public property
(`harness/core.py:918-919`, MEASURED), so no kernel edit is needed to read it.

**Ordering is made deterministic on purpose.** `_handle_abort` awaits the open turn's task
(bounded, 5 s) *before* returning its ack, so the terminator always precedes the abort response.
That is a deliberate strengthening over pi — in aelix v2, `abort`'s `success:true` means "the turn
is closed", not merely "cancel was requested". A parity test can pin an exact record order; today
it could not. **DESIGN.**

### 3.3 The prompt preflight (closes D1c + D2)

`_handle_prompt` becomes:

```python
async def _handle_prompt(harness, cmd) -> RpcResponse:
    # 1. Route, don't reject, when the caller said how (D9 half).
    if harness.phase != "idle" and cmd.streaming_behavior is not None:
        return await _route_streaming(harness, cmd)     # steer / follow_up

    task = asyncio.create_task(_run(harness, cmd, tracker))
    harness._pending_tasks.add(task); task.add_done_callback(harness._pending_tasks.discard)

    # 2. Preflight: yield exactly one loop iteration. `AgentHarness.prompt`'s busy
    #    guard is its first statement (core.py:1189) and the phase flip is
    #    synchronous before the first await (core.py:1197-1198), so after one
    #    iteration the task has either died with the busy error or accepted the turn.
    await asyncio.sleep(0)
    if task.done() and (exc := task.exception()) is not None:
        return RpcErrorResponse(id=cmd.id, command="prompt",
                                error=str(exc), code=_code_for(exc))
    turn = tracker.open(cmd.id)
    return RpcSuccessResponse(id=cmd.id, command="prompt", data={"turn": turn})
```

This is pi's `preflightResult` contract reproduced without a kernel seam. It is **correct for
back-to-back prompts too**, and that is not luck: `loop.call_soon` callbacks run in registration
order (documented CPython/asyncio behaviour), and `_on_line` registers the prompt task
(`create_task`) before the handler registers its `sleep(0)` wake-up — so by the time the handler
resumes, its own task has run. **INFERRED from the documented FIFO ready-queue property; must be
pinned by a test (§6).**

**This is Option A's most fragile load-bearing assumption. See §8.6.**

### 3.4 Error codes (closes D4)

`RpcErrorResponse` gains `code: str | None = None`; `to_json` emits it only when set. Closed
symbol set, and a mapping table recorded in the ADR so a later JSON-RPC/ACP adapter is a dict
lookup, not a redesign:

| symbol | when | future JSON-RPC int |
|---|---|---|
| `parse` | E1–E5 (`rpc_mode.py:2029-2043`, `rpc_types.py:404`/`:407`/`:423`) | `-32700` |
| `unknown_command` | E6 (`rpc_mode.py:1771-1776`) | `-32601` |
| `invalid_argument` | E8 (`:548`), image decode (`:951`, `:955`) | `-32602` |
| `invalid_state` | E9 (`:558`), E17 (`:1444`, `:1451`) | `-32003` |
| `not_configured` | E10/E12/E13 (`:626`, `:673`, `:734`), E20 (`:1677-1683`) | `-32001` |
| `not_found` | E11 (`:642`) | `-32002` |
| `busy` | the new preflight rejection | `-32000` |
| `aborted` | a command refused because the turn was cancelled | `-32800` (ACP `Cancelled`) |
| `internal` | E7 fallback (`:1777-1784`) | `-32603` |

`error` keeps its exact current free text. Nothing that reads `error` breaks. Symbols, not
integers, because this envelope has no integer tradition and a grep-able symbol survives log
triage better; the table above is the whole cost of that choice.

While here, fix E20's user-facing string, which MEASURED contains literal reStructuredText double
backticks (`rpc_mode.py:1682`), and delete `_make_deferred_handler` (`:1475-1491`), unreachable
because `DEFERRED_COMMANDS = {}` (`:194`).

### 3.5 Versioning (closes D5)

Two mechanisms, both additive.

**(a) `hello` — the 30th command.** New dataclass in `rpc_types.py`, new entry in
`_RPC_COMMAND_REGISTRY` (`:344-374`), new handler in the dispatch table (`rpc_mode.py:1572-1633`).

```
C→S  {"id":"h1","type":"hello","protocol":2,"client":"aelix_agents.RpcChannel/0.1.0"}
```

**(b) `server_hello` — the first line of the stream.** An unsolicited event-family record, emitted
once from `run_rpc_mode` before the main loop, snake_case like every other event:

```json
{"type":"server_hello","protocol":2,"min_protocol":1,
 "agent":{"name":"aelix","version":"0.1.0b1"},
 "_meta":{"seq":0}}
```

This is the single most contentious addition in Option A — pi's stream "begins only in response to
a command" (wire recon §5) — and it has a **measured** cost, see §5.2. It buys two things: an
`RpcChannel` can wait for a real readiness signal instead of `RpcClient.STARTUP_GRACE_MS = 100`'s
guess (`rpc_client.py:88`), and a client learns the version with zero round-trips.

### 3.6 The casing split (closes D6 by declaring it, then enforcing it)

Option A does **not** move the boundary; it makes it a rule and a test.

1. **Envelope keys** are the fixed literals `type`/`command`/`success`/`error`/`id`/`data`/`code`.
2. **Every response `data` payload is camelCase.** This *changes* three payloads today —
   `get_messages` (`rpc_mode.py:448-450`), `compact` (`:467-470`), `get_session_stats`
   (`:1194-1195`) — which are snake_case only because they are `dataclasses.asdict` of kernel
   dataclasses. **INFERRED, and it must be verified against pi before shipping:** pi's equivalents
   are TS interfaces and therefore camelCase, so this is most likely a *parity restoration*, not a
   divergence. If that verification fails, keep them snake_case and record the exception by name.
3. **Every event record field is snake_case**, `type` last, with the `toolCall` content-block
   Literal *value* as the one island.
4. **`_meta` is reserved, not a record field**; its interior is camelCase everywhere.
5. A new test walks every handler's `data` for `_` and every event for a capital letter. That test
   is the actual deliverable here — the rule without the gate is prose.

### 3.7 The reader contract (closes D8)

- `JsonlLineReader.__init__` gains `max_line_bytes: int | None = None` and a `dropped: int`
  counter; over-budget in-progress lines are dropped and the reader resynchronises at the next
  `\n` (`_jsonl.py:50-72`). Default `None` ⇒ **byte-identical behaviour for every existing
  caller**, including the server's own stdin reader.
- `RpcClientOptions` (`rpc_client.py:61-70`) gains `limit: int | None` and `max_line_bytes`;
  `start()` (`:111-127`) forwards `limit=` to `create_subprocess_exec`.
- `aelix_agents.RpcChannel` passes `STREAM_LIMIT_BYTES = 8 MiB` and `MAX_LINE_BYTES = 4 MiB` — the
  same constants `PrintChannel` already uses (`print_channel.py:114`, `stream.py`'s budget), so
  `dropped_lines` becomes fillable and the two channels become genuinely comparable.

### 3.8 `prompt` parity (closes D9)

Forward `cmd.images` through `_decode_images` exactly as `_handle_steer` does (`rpc_mode.py:979`),
and honour `streaming_behavior` by routing to steer/follow_up per §3.3. Both are parity
restorations against `rpc-mode.ts:384-385` and `rpc.md:65` (pi recon §4b).

---

## 4. Files touched

| File | Change | Kind |
|---|---|---|
| `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_types.py` | `code` on `RpcErrorResponse` (`:487-511`); `RpcCommandHello`; registry entry (`:344-374`); unknown-key filter in `parse_rpc_command` (`:409-423`) | modify |
| `…/rpc/rpc_mode.py` | `_meta` in `_output` (`:1879`); `_TurnTracker`; rewritten `_handle_prompt` (`:289-330`) and `_handle_abort` (`:333-340`); `_handle_hello`; `server_hello` emit; camelCase fix in 3 payloads; `code=` on ~20 `RpcErrorResponse` sites; E20 string fix (`:1682`); delete dead `_make_deferred_handler` (`:1475-1491`) | modify |
| `…/rpc/_jsonl.py` | optional per-line budget + `dropped` counter (`:50-72`) | modify |
| `…/rpc/rpc_client.py` | `limit`/`max_line_bytes`/`start_new_session`/`preexec_fn` on `RpcClientOptions` (`:62-72`), forwarded in `start()` (`:111-127`); inbound-request hook stub (§7); **fix the default argv** (`:454-471`, `-m aelix` → `-m aelix_coding_agent`) | modify |
| `…/cli/entry.py` | fix the `repo`/`fs` crash at `:2160-2166` (§5.1) | modify |
| `packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py` | **the sprint deliverable**: `RpcChannel` + its own argv/env builders + `argv_builder`/`env_builder` seams mirroring `print_channel.py:713-732` | new |
| `…/aelix_agents/runtime.py` | `channel: PrintChannel` at `:298` becomes a channel Protocol | modify |
| `…/aelix_agents/envelope.py` | `exit_code=None` policy for rpc, pinned (`:201-205`) | modify |
| `…/aelix_agents/stream.py` | `reduce_event(state, dict)` sibling so a channel holding parsed dicts need not re-serialise | additive |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` | `:52-62` set-equality → superset + an explicit aelix-additive roster; `:64-92` count 29 → 29 pi + 1 additive | modify |
| `tests/server/test_server.py` | 3 WS tests must drain `server_hello` first (§5.2) | modify |
| `tests/rpc/` | new: real-child bed, `hello` handshake, terminator-on-abort, terminator-on-failure, busy→`success:false`, casing gate, `seq` monotonicity | new |
| `tests/agents_ext/test_cross_channel_parity.py` | the sprint's second deliverable | new |
| `docs/decisions/02xx-rpc-wire-v2.md` | the protocol record ADR-0198 deferred | new |

---

## 5. Compatibility story

### 5.1 `aelix --mode rpc` is broken today, and Option A must fix it first

MEASURED, `da61337`: `entry.py:2157-2167` calls `run_rpc_mode(harness, runtime_host=runtime,
harness_factory=…, repo=repo, fs=fs)`, and `rpc_mode.py:1910-1914` raises
`RuntimeError("repo and fs must not be supplied when runtime_host is explicit — the runtime owns
them")` on exactly that combination. Zero bytes reach stdout. **There is no working stdio RPC
server in the product.** Every other item in this option is downstream of the one-line fix at
`entry.py:2164-2165`. It is also why nothing caught it: no test spawns a real `--mode rpc` child
(envelope recon; re-confirmed — `tests/rpc/test_rpc_client_lifecycle.py:88` says so in its own
comment).

### 5.2 `packages/aelix-server` — zero source lines, three test edits

MEASURED. `rpc_ws.py` is byte-transparent: `:112-138` queues whatever `stdout_write` hands it and
`await websocket.send_text(item.decode("utf-8"))` at `:136`; inbound is
`text.rstrip("\n")` + `feed_data` at `:115-120`. It contains no command name, no field name and no
envelope key. **A v1→v2 change costs it zero source lines.**

**But `server_hello` breaks three of its tests, and this is MEASURED, not hypothetical.**
`tests/server/test_server.py:129-137` does `ws.send_text(...)` then `raw = ws.receive_text()` and
asserts `frame["type"] == "response"`. With a greeting, the first frame is the greeting.
`:150-153` and `:166-174` have the same shape. Each needs one extra `receive_text()`. That is the
entire measured cost of the most contentious addition in this design — and it is also proof the
greeting is genuinely observable, which is the point.

### 5.3 The pi-parity pin

`tests/pi_parity/test_phase_4_4_strict_superset.py:52-62` asserts `set(fixture["rpc_command_types"])
== RPC_COMMAND_TYPES` — **set equality**. A 30th command requires amending it to a superset plus an
explicit, named aelix-additive roster (`{"hello"}`), and updating the three count assertions at
`:89-91` (`len(RPC_COMMAND_TYPES) == 29`, `len(SUPPORTED_COMMANDS) == 29`,
`len(DEFERRED_COMMANDS) == 0`). The union-closure assert at `:83`
(`SUPPORTED_COMMANDS | set(DEFERRED_COMMANDS) == RPC_COMMAND_TYPES`) flows through unchanged.
This is defensible under the repo's own regime — the file is named
`strict_superset`, ADR-0035 is the precedent ("no parity violation; consumers `except
AgentHarnessError` continue to work"), and `docs/decisions/` has 51 files using `Aelix-additive`
(pi recon §5). **It is still a real loss:** once the pin is a superset it stops detecting an
*accidental* addition. Mitigation: the amended pin must assert the additive set **by literal
name**, so a second unplanned command still fails.

`tests/rpc/test_rpc_mode_dispatch.py:45` (`set(table.keys()) == RPC_COMMAND_TYPES`) and `:54`
(union closure) flow through automatically. MEASURED.

### 5.4 External consumers

**None exist.** MEASURED in `rpc-protocol-recon-consumers.md` §0 and re-affirmed here: 0 git tags,
0 releases, `aelix-web` does not exist on disk or on GitHub, `aelix-server` is `rm -f`'d from the
publish set by `.github/workflows/release.yml:82-87`, and no public doc promises more than "a JSONL
command/response protocol on stdio" (`docs/guides/getting-started.md:74`). **The migration cost of
Option A is entirely in-repo test churn.**

### 5.5 How a client distinguishes an old server from a fixed one

Two independent ways, both MEASURED-shape:

**(a) Passive, zero round-trips.** Read the first record. `{"type":"server_hello"}` ⇒ v2. Anything
else, or silence until you send a command, ⇒ v1. Requires a timeout, so it is advisory.

**(b) Active, authoritative — one round-trip.** Send `hello`.

*Old server* (exact shape MEASURED from `rpc_types.py:407` → `rpc_mode.py:1765-1769`):
```json
{"type":"response","command":"parse","success":false,
 "error":"Failed to parse command: Unknown RpcCommand type: 'hello'","id":"h1"}
```
*New server:*
```json
{"type":"response","command":"hello","success":true,"id":"h1",
 "data":{"protocol":2,"minProtocol":1,
         "agent":{"name":"aelix","version":"0.1.0b1"},
         "commands":["prompt","steer",...,"hello"],
         "capabilities":{"syntheticTerminator":true,"promptPreflight":true,
                         "eventMeta":true,"errorCodes":true,"lineBudget":true,
                         "extensionUiBridge":false}},
 "_meta":{"seq":1}}
```
The discriminator is `command == "parse"` vs `command == "hello"` — unambiguous, and it works
because the old server's parse failure is *guaranteed* by the closed envelope (`rpc_types.py:423`,
MEASURED). **Option A's own §3.0/R2 relaxation does not weaken this**, because `hello` is an
unknown *discriminator*, not an unknown *field*.

### 5.6 Containment seam — parameters in core, policy in the extension

`RpcClientOptions` (`rpc_client.py:61-70` — today exactly six fields: `cli_path`, `cwd`, `env`,
`provider`, `model`, `args`) gains `start_new_session: bool = False`,
`preexec_fn: Callable | None = None`, `limit: int | None = None`.
`RpcClient.start()` (`:111-127`) forwards them. **Every value is
supplied by `aelix_agents.RpcChannel`**, which is also where `AELIX_SUBAGENT_DEPTH`, the
`AELIX_MCP_CONFIG` pop, `--no-agents` and the trust argv live — mirroring
`print_channel.py:400-420` exactly. `rpc_client._build_env` (`:474-477`) stays
`dict(os.environ) | options.env` and gains nothing. This is the line the kickoff handoff §3 drew,
and Option A does not cross it: **spec lines 275/289, which ask for depth threading inside
`rpc_client._build_env`, are explicitly rejected.**

Also fix `_build_argv` (`:454-471`): its default targets `-m aelix`, which
`src/aelix/__main__.py:131-147` shows is an `AgentHarness` with `_make_mock_stream_fn()`. And note
the correction from `rpc-protocol-recon-consumers.md` §7.1 — **`src/aelix` IS in the wheel**
(`pyproject.toml:59` `packages = ["src/aelix"]`), so that bug would ship.

---

## 6. Exact JSON, before and after

### 6.1 A failed prompt (post-acceptance failure)

**BEFORE** — MEASURED from `rpc_mode.py:302-330` + `:308-315`:
```
C→S  {"id": "req_1", "type": "prompt", "message": "refactor the parser"}
S→C  {"type":"response","command":"prompt","success":true,"id":"req_1"}
S→C  {"type":"agent_start"}
S→C  {"message":{...},"type":"message_start"}
     ── harness.prompt raises ValueError("boom") ──
     stderr only:  [rpc] prompt task failed: ValueError('boom')
     ── nothing further on stdout, ever ──
     client's prompt_and_wait burns DEFAULT_WAIT_FOR_IDLE_MS = 60_000 (rpc_client.py:87)
     then raises a timeout with no information about what happened
```

**AFTER:**
```
C→S  {"id": "req_1", "type": "prompt", "message": "refactor the parser"}
S→C  {"type":"response","command":"prompt","success":true,"id":"req_1",
      "data":{"turn":3},"_meta":{"seq":12}}
S→C  {"type":"agent_start","_meta":{"seq":13,"turn":3}}
S→C  {"message":{...},"type":"message_start","_meta":{"seq":14,"turn":3}}
     ── harness.prompt raises ValueError("boom"); the tracker sees the task
        finish with no agent_end for turn 3 ──
S→C  {"messages":[...],"type":"agent_end",
      "_meta":{"seq":15,"turn":3,"synthetic":true,
               "stopReason":"error","code":"internal",
               "detail":"ValueError('boom')"}}
```
The stderr line is kept — it is the operator's channel and costs nothing.

### 6.2 An aborted turn

**BEFORE** — MEASURED from `rpc_mode.py:333-340` + `harness/core.py:4287-4294`:
```
C→S  {"id":"req_5","type":"abort"}
S→C  {"type":"response","command":"abort","success":true,"id":"req_5"}
     ── the cancelled turn emits NOTHING. The kernel returns [] at core.py:4294
        before reaching its own ported pi closure block at :4298-4316 ──
     the original prompt's waiter burns its full 60 s
```

**AFTER:**
```
C→S  {"id":"req_5","type":"abort"}
S→C  {"messages":[...],"type":"agent_end",
      "_meta":{"seq":52,"turn":3,"synthetic":true,
               "stopReason":"cancelled","code":"aborted"}}
S→C  {"type":"response","command":"abort","success":true,"id":"req_5",
      "data":{"turn":3},"_meta":{"seq":53}}
```
Terminator **before** ack, deterministically, because `_handle_abort` awaits the turn task
(bounded) before returning (§3.2). `stopReason:"cancelled"` is ACP's spelling; note it is **not**
pi's, which is `"aborted"` on the synthetic assistant message (`agent-harness.ts:49-56`, pi recon
§0.1). That is a deliberate, named divergence in *vocabulary only* — the record type, the
`messages` payload and the client-visible completion semantics all match pi.

### 6.3 A busy rejection

**BEFORE** — MEASURED from `rpc_mode.py:322-330` + `harness/core.py:1185-1191`:
```
C→S  {"id":"req_9","type":"prompt","message":"second task"}
S→C  {"type":"response","command":"prompt","success":true,"id":"req_9"}
     ── harness.prompt raises AgentHarnessError("busy", …) inside the detached
        task; stderr gets [rpc] prompt task failed: …; the wire gets NOTHING ──
     a client that now waits for agent_end burns 60 s and then reports failure
     for a prompt that was never even started
```

**AFTER, no `streamingBehavior`:**
```
C→S  {"id":"req_9","type":"prompt","message":"second task"}
S→C  {"type":"response","command":"prompt","success":false,"id":"req_9",
      "error":"AgentHarness is busy (phase='turn'); use steer()/follow_up() while in a turn.",
      "code":"busy","_meta":{"seq":33}}
```
(pi's documented contract, restored: `rpc.md:76` — *"`success: false` means the prompt was
rejected before acceptance."*)

**AFTER, with `streamingBehavior`** — pi parity for a field aelix currently parses and throws away
(`rpc_types.py:57-64` declares it; `rpc_mode.py:309` ignores it):
```
C→S  {"id":"req_9","type":"prompt","message":"second task","streamingBehavior":"steer"}
S→C  {"type":"response","command":"prompt","success":true,"id":"req_9",
      "data":{"turn":3,"routed":"steer"},"_meta":{"seq":33}}
```

---

## 7. Is the child→parent approval back-channel expressible after this?

**Not yet — and Option A must decide that on purpose, not by omission.**

After §3, the protocol is still **half-duplex at the request level**. MEASURED, re-verified: the
server has exactly four write sites (`rpc_mode.py:1935`, `:2030`, `:2038`, `:2061`) and none is a
request; `RpcClient._handle_stdout_line` (`:496-536`) has exactly two branches and no reply path;
`_send` (`:538-581`) always mints a *new* `req_N` (`:550`) and cannot answer an id the server chose.
`extension_ui_response` is still dropped at `rpc_mode.py:2048-2049`.

### 7.1 What Option A should reserve now, because it is cheap now and expensive later

1. **Partition the id space.** Declare that server-allocated ids MUST be prefixed `srv_` and that
   client ids MUST NOT be. Today `RpcClient` mints `req_N` (`:550`) and the server mints nothing;
   the moment both mint, an unpartitioned space is a correlation bug that will be found in
   production, not in review. Cost now: one sentence in the ADR and one assertion. Cost later:
   a wire break.
2. **Stop silently dropping `extension_ui_response`.** Replace the bare `return` at
   `rpc_mode.py:2048-2049` with an error response carrying `code:"unknown_command"` — because a
   silent drop is indistinguishable from success and is exactly the failure mode this whole sprint
   exists to eliminate. MEASURED today: the probe line produces **zero output records**.
3. **Advertise it.** `capabilities.extensionUiBridge: false` in the `hello` payload, flipping to
   `true` when the bridge lands. That is the entire point of having a handshake.

### 7.2 What the missing direction costs if Option A stops there

- **Consent becomes irrevocably spawn-time.** P2/P3's model already grants a posture at spawn
  (ADR-0197/0199). Without a back-channel, a child that discovers mid-task that it needs a wider
  posture has exactly one remedy: die and be respawned with a wider grant — losing its entire
  context. Every "just ask the human" workflow is structurally impossible over rpc.
- **`fs/*` and `terminal/*`-style delegation is off the table.** A child cannot ask its parent to
  perform a privileged operation, so every privilege it will ever need must be predicted at spawn.
  That pushes postures *wider* than they need to be — a security regression driven by a protocol
  gap.
- **ADR-0197 §(i) blocker (b) stays open** and blocker (c) — the parent TUI's missing modal arbiter
  — remains untouched by any protocol work (standards recon §6 says the same of ACP).
- **The later fix is four items, all additive** (wire recon §7.3): emit `extension_ui_request`
  (types exist, `rpc_types.py:645-756`, zero construction sites); handle `extension_ui_response`
  (`rpc_types.py:778-809` exist); a server-side pending-request map keyed by a server-allocated id;
  a client-side inbound dispatcher. **None requires touching the transport** — an rpc child's stdin
  is already a live bidirectional JSONL channel (`rpc_mode.py:1884-1889` + the pump at
  `:2078-2092`), and `_read_piped_stdin` is gated to print/json at `cli/entry.py:1346`. So the cost
  of deferring is *not* a rewrite; it is (a) a second protocol revision with its own ADR and
  handshake bump, and (b) every consent decision being spawn-time-only until then.

**Honest summary:** Option A does not close D7, and it should not pretend to. What it must do is
leave the door unlocked — id partitioning, a non-silent `extension_ui_response`, and a capability
flag — so the later sprint is purely additive.

---

## 8. Why this is the wrong choice

The strongest case against Option A, made as forcefully as I can make it.

### 8.1 It spends the only free protocol break the project will ever get on a protocol nobody speaks

`rpc-protocol-recon-consumers.md` MEASURED it: zero tags, zero releases, `aelix-web` does not
exist, `aelix-server` is deleted from the publish set by its own release workflow, no public doc
promises anything beyond "a JSONL command/response protocol", and — decisively — **the only
prospective production consumer, `RpcChannel`, has not been written yet**. That is a window that
closes the day this sprint merges. `rpc-protocol-recon-standards.md` MEASURED that ACP fixes
**6 of 7** defects *normatively*, has five official SDKs, ~36 agents and ~12 editor families, is
implemented by Cursor / Copilot / Gemini CLI / Codex / Claude Agent / OpenCode, and — the single
most damning line — **already has a working adapter for pi itself** (`pi-acp`, which spawns
`pi --mode rpc`). Option A burns the cheap window on a bespoke v2 that exactly one client will ever
speak, and by the time ACP is revisited, `RpcChannel`, the rpc conformance bed and the parity test
will all be written against v2. The second migration is strictly more expensive than the first
would have been. **If the wire is going to break — and Option A does break it — the case that it
should break toward ACP instead is very strong.**

### 8.2 It hand-rolls four taxonomies that already exist, and buys review debt for each

`code`, `stopReason`, the capability handshake, and the cancel-vs-pending-request precedence rule
are all specified normatively by ACP. Option A writes them from scratch, in this repo, with this
repo's review capacity. Each is a place to be subtly wrong, and each acquires a test suite that
exists nowhere else. The `hello` handshake in particular is a genuine protocol design problem
(version floors, capability defaults, what an unknown capability means) that this option solves in
a paragraph and that ACP's `initialize` solves with a MUST-level spec plus five SDK implementations
and a "capabilities omitted ⇒ UNSUPPORTED" default rule that Option A does not even state.

### 8.3 The most important fix is in the layer Option A is forbidden to touch

`rpc-protocol-recon-pi.md` §0.1 MEASURED that both terminator defects are **kernel regressions**:
`harness/core.py:4287-4294` short-circuits abort before the already-ported pi closure block, and
`:4298`'s `except AgentHarnessError` is narrower than pi's bare `catch (error)`. **The kernel
already contains the correct code.** Option A's `_TurnTracker` is a workaround for a bug two layers
down, and it has four consequences:
- The TUI and `--mode json` **still** have no terminator on abort. Only rpc is fixed.
- `stop_reason` stays hardcoded `"error"` at `core.py:4302`, so even the kernel's working path
  cannot say `"aborted"`.
- There are now **two** implementations of turn closure that must be kept in sync. §3.2's de-sync
  guard exists precisely because Option A is *choosing to create* a double-emit hazard.
- It is a **divergence from pi's layering** where the kernel fix would have been a *convergence* —
  which inverts the repo's own strict-superset regime on the one sprint that most invokes it.

The honest framing: the right fix is two lines in the kernel; Option A writes ~80 lines in
product-core to avoid them, and the result is worse.

### 8.4 `_meta` breaks the one property the parity test was going to exploit

The envelope recon MEASURED that both channels serialise events through the *identical* function —
`print_mode._event_to_dict` (`print_mode.py:59-68`) delegates to `rpc_mode._dataclass_to_dict`
(`:258-268`) — so the event halves are shape-identical **by construction**. That is the single fact
that makes the cross-channel parity test feasible at all. §3.1 adds `_meta` to rpc events and not
to json events, **making the two channels diverge for the first time, on the same sprint the
parity test is first written.** The test must now strip a field to compare, which means the test no
longer proves what its name says. And `_output` runs once per streamed event, so a dict allocation
plus two keys per record is a real cost on the hot path.

There is a cheaper answer Option A rejects without much justification: put `seq`/`turn` **only** on
records where correlation actually matters (the terminator, the prompt response), and leave the
streaming event path byte-identical to `--mode json`. That would be a strictly better trade and
this design does not take it. *(If Option A is chosen, this is the first thing to reconsider.)*

### 8.5 Amending the pi pin costs a real gate

`test_phase_4_4_strict_superset.py:52-62` is set equality against a fixture snapshotted from
pi@`734e08e`, and the docstring calls that fixture *"the authoritative wire surface"*. Turning it
into a superset check is exactly how a pin stops being a pin. §5.3's mitigation (assert the
additive set by literal name) is real but weaker: the gate now encodes a decision instead of an
invariant, and the next person who wants a 31st command has a precedent instead of a wall.

### 8.6 The preflight rests on an unpinned kernel ordering

§3.3's `await asyncio.sleep(0)` is correct **only because** `AgentHarness.prompt`'s busy guard is
its first statement and the phase flip at `harness/core.py:1197-1198` is synchronous before the
first `await` (MEASURED at `da61337`). Nothing anywhere pins that. A future kernel refactor that
inserts one `await` above the guard silently reverts D2 — the ack goes back to being fire-and-forget
— **with no test failure**, unless the rpc layer writes a test asserting a kernel internal, which is
its own smell. Option A's answer to the fire-and-forget defect is therefore a *timing coincidence
that the sprint promotes to a contract*, whereas ACP's answer ("the `session/prompt` response **is**
the terminator") makes an early ack impossible by construction.

### 8.7 It does not fix D7, and D7 is the one the owner actually wants

ADR-0197 §(i)'s approval back-channel is the strategic goal. Option A leaves it unbuilt and hands
the next sprint a second protocol revision. ACP would have delivered
`session/request_permission` with `allow_once`/`allow_always`/`reject_once`/`reject_always` —
a vocabulary that maps almost one-to-one onto aelix's own postures — plus the normative
cancel-precedence rule that aelix would otherwise have to discover the hard way (a pending
permission request on a cancelled turn is a hang).

### 8.8 The rebuttals, stated fairly

Three of the above have real answers, and the choice turns on whether they are enough.

- **Against 8.1/8.7:** ACP's method vocabulary covers ~5 of aelix's 29 commands (standards recon
  §3.4, MEASURED counts on both sides). Adopting ACP *as the wire* means deleting or burying 24
  commands in `_meta`. The defensible ACP shape is an **adapter over** the rpc protocol — the
  `pi-acp` shape — which means **ACP does not have to be decided in this sprint at all**, because
  it changes nothing in `rpc_types.py`. If that is true, 8.1's "only free window" argument is
  overstated: the window for the *adapter* never closes.
- **Against 8.3:** the kernel is off-limits by owner policy, not by technical necessity. If the
  owner lifts that, §3.2 shrinks to a de-sync guard and most of 8.3 evaporates. **This is the
  single highest-value question to put back to the owner**, and it is a different question from the
  one the kickoff handoff §3 asks.
- **Against 8.4:** trivially fixable by scoping `_meta` to non-streaming records, at the cost of
  losing gap detection on the event stream.

---

## 9. What happens to the sprint deliverable

**It ships — but it changes shape, and it ships second, not first.**

- **`RpcChannel` ships.** Nothing in Option A blocks it; §5.1's `entry.py` fix and §5.6's
  containment seam are its preconditions and are both small. It lands in
  `packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py` with its own argv/env builders and
  the same `argv_builder`/`env_builder` seams `PrintChannel` has (`print_channel.py:713-732`), so
  it is drivable against a scripted child.
- **The parity test ships, with two declared asymmetries instead of zero.** It must now normalise
  (a) `_meta`, which exists on rpc events and not on json events — a divergence Option A creates
  (§8.4); and (b) `server_hello` as the rpc stream's first record versus `agent_start` on the json
  side for a `--no-session` subagent (envelope recon; pinned by
  `tests/cli/test_print_mode_json_contract.py:308-325`). Plus the four asymmetries it already owed:
  interleaved `{"type":"response"}` frames, the absent session header, `exit_code`, `dropped_lines`.
- **`exit_code` and `dropped_lines` both get an answer instead of a footnote.** `exit_code=None` for
  rpc — the only value `envelope.py:201-205`'s `failed` clause treats as non-evidence, MEASURED —
  and `dropped_lines` becomes genuinely fillable via §3.7. Both must be asserted by the parity test,
  not merely documented.
- **The sprint grows by roughly one third**, and the risk is that the protocol work crowds out the
  channel work. Mitigation: land in two commits with a hard ordering — **(1) protocol v2 + its
  conformance bed, (2) `RpcChannel` + parity test** — which also satisfies spec §9's "nothing parses
  child output until this ADR lands" read literally (envelope recon, contradiction #8).
- **Two ADRs, not one:** the rpc half of the envelope record that ADR-0198 deferred, and the wire-v2
  record. They can be one document; they cannot be zero.

---

## 10. Uncertainties — what I did not measure

1. **Whether pi's `get_messages` / `compact` / `get_session_stats` payloads are camelCase.** §3.6
   item 2 assumes they are (TS interfaces) and calls the fix a parity restoration. **INFERRED. Must
   be verified against `rpc-mode.ts` at the pin before shipping.** If wrong, keep them snake_case
   and record the exception.
2. **The `sleep(0)` preflight is reasoned, not executed.** I did not write and run the two-prompt
   race. §8.6 is the honest statement of the risk; a test must exist before this is believed.
3. **I did not enumerate which of the 190 `tests/rpc` tests break.** I measured only the three
   exact-dict `to_json()` asserts (`test_rpc_types.py:114`, `:123`, `:134`, which §3.1's design
   deliberately keeps green) and the three WS first-frame tests (`tests/server/test_server.py:129`,
   `:150`, `:166`). The rest is characterised, not counted.
4. **`RpcSessionState` gains no version field** in this design, though a client might reasonably
   expect one there. I chose the `hello` payload instead; the 13-field shape is pinned by
   `test_phase_4_4_strict_superset.py:246-256` and touching it is a second pin amendment for no
   gain.
5. **The `_TurnTracker`'s behaviour under P3 parallel/chain delegation is unexamined.** A single
   `turn` counter assumes one turn at a time per server process, which is true today (the harness
   busy guard enforces it) but interacts with chain mode's `steer`/`follow_up` path, which
   *enqueues* rather than rejecting. Whether a steered message opens a new `turn` is undecided here.
6. **No blast-radius assessment of the `parse_rpc_command` relaxation (§3.0/R2)** on the ~29
   command dataclasses; I asserted it preserves the missing-required-field error but did not run
   the existing parse tests against it.
