# RPC protocol — DESIGN OPTION B: "STANDARD-NATIVE"

> **Written:** 2026-07-29, RPC sprint design phase. Repo at `main` = `da61337`. **Nothing was edited.**
> This is a design proposal, not a decision and not a plan.
>
> **Every `path:line` below was re-opened or executed against the current tree at `da61337`.**
> Line numbers quoted in the older recon notes (`rpc-sprint-recon-transport.md`,
> `rpc-sprint-recon-envelope.md`) were **not** trusted; where I cite them I re-verified first.
>
> **MEASURED** = I ran it or opened it. **INFERRED** = reasoning on top of measured facts.
> **SPEC** = quoted from a published standard (via `rpc-protocol-recon-standards.md`, which fetched them).
>
> Companion ground truth (all four read before writing):
> `rpc-protocol-recon-wire.md`, `rpc-protocol-recon-pi.md`, `rpc-protocol-recon-consumers.md`,
> `rpc-protocol-recon-standards.md`. Sprint framing: `rpc-sprint-kickoff-handoff.md`.

---

## 0. The proposal in one paragraph

Put **JSON-RPC 2.0** underneath the rpc wire as the message envelope, keep the existing NDJSON
framing byte-for-byte, and borrow **ACP's control semantics** where they fit — `initialize` with a
`protocolVersion`, *the prompt request IS the turn* (its response carries a `stopReason`),
`session/cancel` as a notification that resolves the pending prompt, and `session/request_permission`
as the server→client approval back-channel. Commands ACP has no counterpart for (24 of aelix's 29)
live in an `aelix/*` method namespace. **The pi grammar is not removed — the server speaks both,
latched per connection on the presence of the `"jsonrpc"` member.** The `RpcChannel` this sprint
owes is built as a *standard-dialect* client, which makes it structurally immune to the three
terminator defects without a kernel edit and without a synthetic event.

**Verdict on the sprint deliverable, stated up front and honestly: it ships, in changed shape, but
only if the standard dialect is cut to six methods and the channel is banked against the pi dialect
first. Option B is a strict superset of Option A's work — it does not remove one line of it.**

---

## 1. What is measurably true, and what that constrains

Restating only the facts this design turns on. Every one re-verified at `da61337`.

| # | Fact | Evidence (re-opened) |
|---|---|---|
| F1 | **`aelix --mode rpc` does not start.** It raises before emitting a byte. | **MEASURED — I re-ran it.** `entry.py:2160-2166` passes `repo=repo, fs=fs` alongside `runtime_host=runtime`; `rpc_mode.py:1910-1914` raises. Traceback names `entry.py:2160` and `rpc_mode.py:1911`. |
| F2 | The string `"jsonrpc"` appears **nowhere** in `packages/`. | MEASURED (standards recon §1; re-confirmed by absence in every file I opened). The two grammars are therefore **provably disjoint on one key**. |
| F3 | The pi command envelope is **closed**: one unknown key ⇒ whole command rejected. | `rpc_types.py:423` — `return cls(**kwargs)` on a frozen dataclass. Re-opened; the `for key, value in payload.items()` loop at `:413-422` passes unknown keys straight through to `cls(**…)`. |
| F4 | Events are `dataclasses.asdict` passthrough, snake_case, `type` last, **no id / seq / turn**. | `rpc_mode.py:258-268` (`_dataclass_to_dict`), `:271` (`_event_to_dict`), emitted at `:1927` (`_on_agent_event`) → `_output(payload)` at `:1935`; `_output` defined at `:1879`. |
| F5 | Failed prompt emits **nothing** on the wire. The in-code justification is false. | `rpc_mode.py:289-330`. The `except Exception` at `:310` prints to stderr at `:311-315`; the comment at `:316-320` still claims pi emits a synthetic terminal event. pi recon §0.2: **`grep -n "agent_end\|synthetic" pi-rpc-mode.ts` → 0 hits at both pins.** |
| F6 | Busy rejection is acked as **success before the await**. | `rpc_mode.py:322` `task = asyncio.create_task(_run())`; `:330` `return RpcSuccessResponse(id=cmd.id, command="prompt")`. |
| F7 | Abort emits no terminator, because the **kernel** short-circuits. | `rpc_mode.py:339-340` (`await harness.abort()` → success response). Kernel: `harness/core.py:4287-4295` `except asyncio.CancelledError: … return []` sits **inside** the try and returns before the closure block at `:4298-4316` can run. Re-opened; matches the pi recon exactly. |
| F8 | Nothing external consumes this wire. **0 git tags, 0 releases, `aelix-web` does not exist.** | consumers recon §0/§3, MEASURED there. `packages/aelix-server` is `rm -f`'d from the publish set at `.github/workflows/release.yml:82-87`. |
| F9 | `packages/aelix-server` is **byte-transparent** — a wire change costs it 0 source lines. | `rpc_ws.py:115-120` (receive → `rstrip("\n")` → feed) and `:136` (`send_text(item.decode("utf-8"))`). `run_rpc_mode` invoked verbatim at `:146`. |
| F10 | pi parity on the rpc surface is pinned by **6 test files, 152 `def test_`**, plus one fixture. | MEASURED `grep -c 'def test_'`: `4_3`=14, `4_4`=20, `4_9`=28, `4_11`=26, `4_12`=27, `4_13`=37. (The consumers recon's "155 collected" is the *collected* count; parametrize accounts for the delta.) |
| F11 | `tests/rpc/` = **31 files, 188 `def test_`**; 23 of the 31 files reference the response envelope. | MEASURED `ls tests/rpc | wc -l` and `grep -rc 'def test_'`. |
| F12 | No public document promises pi compatibility. The strongest promise is a **shape**: *"JSONL command/response protocol on stdio"*. | `docs/guides/getting-started.md:74` (re-opened, verbatim). `README.md:50-51` says *"a `--mode rpc` JSONL protocol"*. |
| F13 | `reduce_line` is `json.loads` + dispatch on `event.get("type")`. | `stream.py:406` (def), `:443` (`json.loads`), `:449` (`kind = event.get("type")`). A wrapper costs it **one unwrap**, not a rewrite. |
| F14 | The **only** prospective production consumer of the rpc wire is the `RpcChannel` that has not been written. | `agents/resolver.py:201` emits `["--mode","rpc"]` for `oneshot=False`; consumers recon §6.6 measured no caller. `aelix_agents/` has 17 modules (MEASURED `ls`), none rpc. |

**What F2 + F3 + F14 jointly mean, and it is the load-bearing observation of this whole note:**
the pi grammar cannot be extended forward (F3), the two grammars can be told apart by one key (F2),
and the consumer that would have to be migrated does not exist yet (F14). **If the wire is ever
going to change, this sprint is the cheapest moment it will ever be**, and dual-speak is not merely
possible — it is *trivially* decidable.

---

## 2. THE GRAMMAR

### 2.1 Framing — **unchanged**

NDJSON, LF-only, UTF-8, `ensure_ascii=False` (`_jsonl.py:31`). Not one byte of `_jsonl.py` changes.
ACP is NDJSON; MCP-stdio is NDJSON; aelix is NDJSON. The standards recon §5 argues Content-Length
framing at length and rejects it for four measured reasons (it would destroy `LineAssembler`
at `stream.py:74-155` and the `MAX_LINE_BYTES = 4 MiB` budget at `:58`). **Framing is settled and
this design does not reopen it.**

### 2.2 The three message kinds (SPEC, JSON-RPC 2.0 §4 / §4.1 / §5)

```
REQUEST       {"jsonrpc":"2.0","id":<string>,"method":<string>,"params":<object>}
NOTIFICATION  {"jsonrpc":"2.0","method":<string>,"params":<object>}          // no id, no reply
RESPONSE      {"jsonrpc":"2.0","id":<string>,"result":<any>}
              {"jsonrpc":"2.0","id":<string>,"error":{"code":<int>,"message":<string>,"data"?:<any>}}
```

Both peers may send requests (SPEC, §Overview: *"One implementation … could easily fill both roles,
even at the same time"*). **Client ids are `req_N`; server ids are `srv_N`. Disjoint namespaces, by
convention, so a mis-routed reply is diagnosable.**

Two deliberate deviations from the letter of the spec, both recorded here so they are not
discovered later:

1. **`id` MUST be a string in this dialect**, not the spec's `String|Number|Null`. Reason: MEASURED
   defect 9.4 in the wire recon — the pi server echoes a JSON *number* id verbatim
   (`rpc_mode.py:1766` guards `isinstance(payload.get("id"), str)` only on the parse-error path)
   and `rpc_client.py:510` requires `isinstance(request_id, str)` to correlate. Narrowing to string
   makes the existing correlation code correct instead of accidentally correct.
2. **Batch (§6) is NOT supported.** A top-level JSON array is answered `-32600`. Reason: ACP's schema
   does not mention batching either (standards recon §3.2), and batching interacts badly with the
   per-line drop budget. Documented, not silently unimplemented.

### 2.3 Method namespace

| Family | Methods | Source |
|---|---|---|
| **ACP-shaped, client→agent** | `initialize`, `session/new`, `session/prompt`, `session/cancel`, `session/set_mode` | ACP verbatim |
| **ACP-shaped, agent→client** | `session/request_permission` | ACP verbatim |
| **aelix-native, client→agent** | `aelix/<snake_name>` for the 24 commands ACP has no counterpart for — `aelix/get_state`, `aelix/set_model`, `aelix/cycle_model`, `aelix/get_available_models`, `aelix/set_thinking_level`, `aelix/cycle_thinking_level`, `aelix/set_steering_mode`, `aelix/set_follow_up_mode`, `aelix/compact`, `aelix/set_auto_compaction`, `aelix/set_auto_retry`, `aelix/abort_retry`, `aelix/bash`, `aelix/abort_bash`, `aelix/get_session_stats`, `aelix/export_html`, `aelix/switch_session`, `aelix/fork`, `aelix/clone`, `aelix/get_fork_messages`, `aelix/get_last_assistant_text`, `aelix/set_session_name`, `aelix/get_messages`, `aelix/get_commands` | aelix-original |
| **aelix-native, agent→client notification** | `aelix/event` | aelix-original |

JSON-RPC reserves only the `rpc.` prefix (SPEC §4), so `aelix/` and `session/` are both legal.
`steer` and `follow_up` fold into `session/prompt` via a `streamingBehavior` param (matching the
existing `RpcCommandPrompt.streaming_behavior` field at `rpc_types.py:62`); `abort` becomes
`session/cancel`. That is why the count is 24 and not 27.

### 2.4 Error codes — the taxonomy (closes D4)

SPEC §5.1 reserves `-32700/-32600/-32601/-32602/-32603` and hands `-32000…-32099` to the
implementation. LSP additionally squats `-32001`/`-32002` and `-32800…-32803`; ACP defines
`-32800 Cancelled`. **To avoid every known collision, aelix mints only in `-32010 … -32049`.**

| code | symbol | replaces (wire recon §4.2 ids) |
|---|---|---|
| `-32700` | Parse error | E1 |
| `-32600` | Invalid Request | E2, plus missing/wrong `jsonrpc` |
| `-32601` | Method not found | E4, E6 |
| `-32602` | Invalid params | E3, E5, the `images` shape `ValueError`s (`rpc_mode.py:951`, `:955`) |
| `-32603` | Internal error | E7 fallback |
| `-32010` | `busy` | **new** — today reported as `success:true` (F6) |
| `-32011` | `turnFailed` | **new** — today stderr-only (F5) |
| `-32012` | `notConfigured` | E10, E12, E13, E20 |
| `-32013` | `notFound` | E11 |
| `-32014` | `invalidState` | E9, E17 |
| `-32015` | `invalidArgument` | E8, E16, E18 |
| `-32016` | `authRequired` | ACP's `auth_required` analogue. **Its ACP integer was not measured** (standards recon uncertainty #2) — if ACP later pins one, this row moves. |
| `-32800` | `Cancelled` | **reserved to ACP; aelix never mints it in Tier 1.** |

`error.message` stays human-readable free text — *the existing strings are kept verbatim*, so E10's
U+2014 em dash and E20's stray double-backticks survive as cosmetic defects rather than becoming
migration work. `error.data.detail` carries the raw Python exception text that E7 leaks today
(`rpc_mode.py:1777-1784`), moving it out of the human-facing slot.

### 2.5 Forward compatibility — the thing the pi grammar structurally cannot have

**In the standard dialect, unknown `params` keys are IGNORED, not fatal.** This is the single most
valuable non-obvious consequence, and it is a direct inversion of F3 (`rpc_types.py:413-423`): today
`{"type":"prompt","message":"m","bogus_field":1}` kills the whole command
(MEASURED in the wire recon §2.A). Under the standard dialect the same extra key is dropped and the
prompt runs. Implementation is one line — filter `kwargs` to `cls.__dataclass_fields__` before
`cls(**kwargs)` — but it can only be done in the new dialect, because doing it in the pi dialect
would change measured pi-parity behaviour.

### 2.6 `initialize` — the handshake (closes D5)

```jsonc
// client → agent
{"jsonrpc":"2.0","id":"req_0","method":"initialize","params":{
  "protocolVersion":1,
  "clientCapabilities":{"fs":{"readTextFile":false,"writeTextFile":false},"terminal":false},
  "clientInfo":{"name":"aelix_agents.RpcChannel","version":"0.1.0b1"}}}

// agent → client
{"jsonrpc":"2.0","id":"req_0","result":{
  "protocolVersion":1,
  "agentInfo":{"name":"aelix","version":"0.1.0b1"},
  "agentCapabilities":{
    "loadSession":true,
    "promptCapabilities":{"image":true},
    "_meta":{"aelix":{"wireDialect":"standard","eventSchema":1,
                      "methods":["session/prompt","session/cancel","aelix/set_model", "…"]}}}}}
```

SPEC (ACP): *"The `initialize` request MUST include the latest protocol version the Client
supports"*; capabilities omitted MUST be treated as UNSUPPORTED; on version mismatch the client
SHOULD close. `agentCapabilities._meta.aelix.methods` is the roster discovery that §5.3 of the wire
recon proved is impossible today (*"the only available discovery is trial and error against the 29
discriminators"*).

**This is also where the wire finally gets a version** — spec §9 asked for one, ADR-0198 did not
deliver it, and the envelope recon lists it as open question #4. `eventSchema` versions the
`params.event` payload independently of `protocolVersion`, because the event payload is a kernel
dataclass shape that can drift without the control protocol changing.

---

## 3. THE FOUR REQUIRED SCENARIOS, IN BOTH GRAMMARS

Every "today" block below is what the code at `da61337` actually produces.

### 3.1 Scenario A — a prompt that fails

**Today** (`rpc_mode.py:289-330`; the terminator gap is F5):

```
→ {"id": "req_1", "type": "prompt", "message": "hello"}
← {"type":"response","command":"prompt","success":true,"id":"req_1"}
  … harness.prompt raises RuntimeError('boom') …
  (stderr)  [rpc] prompt task failed: RuntimeError('boom')
  ⟨nothing on the wire⟩
  client blocks DEFAULT_WAIT_FOR_IDLE_MS = 60_000 (rpc_client.py:87) and raises TimeoutError
```

**Standard dialect** — the reply IS the outcome, so the failure is correlated by construction:

```
→ {"jsonrpc":"2.0","id":"req_1","method":"session/prompt","params":{
     "sessionId":"s_1","prompt":[{"type":"text","text":"hello"}]}}
← {"jsonrpc":"2.0","method":"aelix/event","params":{"sessionId":"s_1","promptId":"req_1","seq":1,
     "event":{"type":"agent_start"}}}
   …
← {"jsonrpc":"2.0","id":"req_1","error":{"code":-32011,"message":"turn failed",
     "data":{"detail":"RuntimeError('boom')","stopReason":"error"}}}
```

Cost: **zero kernel lines, zero synthetic events.** JSON-RPC §5 makes exactly one reply per
id-bearing request obligatory; holding it until the turn resolves is a `rpc_mode.py` change of
roughly 15 lines. Note this is *not* what pi does — pi emits `output(error(id,"prompt",e.message))`
on the response channel from a `.catch` **after** an early success ack (pi recon §4b) — so the
standard dialect is strictly stronger here than pi, not merely equal.

### 3.2 Scenario B — an aborted turn

**Today** (`rpc_mode.py:333-340` + kernel F7):

```
→ {"id":"req_2","type":"abort"}
← {"type":"response","command":"abort","success":true,"id":"req_2"}
  ⟨the aborted turn's agent_end never arrives — harness/core.py:4294 returns [] ⟩
  wait_for_idle blocks the full 60 s
```

**Standard dialect** — ACP's cancellation rule is *normative*
(SPEC: *"the Agent **MUST** respond to the original `session/prompt` request with the `cancelled`
stop reason"*, and *"Agents must catch errors from aborted operations and return the `cancelled`
stop reason rather than error responses"*):

```
→ {"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"s_1"}}
  ⟨no reply to session/cancel — it is a NOTIFICATION, JSON-RPC §4.1⟩
← {"jsonrpc":"2.0","id":"req_1","result":{"stopReason":"cancelled"}}
```

**This is the single cleanest thing ACP gives this project.** The terminator is the *prompt's*
response, so `harness/core.py:4294`'s `return []` no longer blocks it — `_handle_prompt` sees the
coroutine return and resolves the pending request itself.

**But be precise about what is NOT fixed:** the four closure *events*
(`message_start`/`message_end`/`turn_end`/`agent_end`) are still missing, because the kernel still
short-circuits. A standard-dialect client gets a correct **terminator** and a **truncated event
sequence**. The kernel fix (pi recon §4b: widen `except AgentHarnessError` at `core.py:4298`, stop
the early `return []` at `:4294`) is **still owed as a pi-parity restoration** — Option B removes it
from the `RpcChannel`'s critical path, it does not remove it from the backlog.

### 3.3 Scenario C — a prompt rejected because the harness is busy

**Today** (F6 — the ack at `rpc_mode.py:330` precedes the raise inside the task at `:322`):

```
→ {"id":"req_3","type":"prompt","message":"second"}
← {"type":"response","command":"prompt","success":true,"id":"req_3"}   ← this is a LIE
  ⟨busy rejection surfaces on stderr only; no terminator; another 60 s⟩
```

**Standard dialect** — an early ack is *structurally impossible*, because there is nothing to ack:

```
→ {"jsonrpc":"2.0","id":"req_3","method":"session/prompt","params":{
     "sessionId":"s_1","prompt":[{"type":"text","text":"second"}]}}
← {"jsonrpc":"2.0","id":"req_3","error":{"code":-32010,"message":"agent is busy",
     "data":{"activePromptId":"req_1",
             "hint":"send session/cancel, or retry with params.streamingBehavior"}}}
```

`data.activePromptId` requires new **server-side state** (a single-slot in-flight prompt registry).
That is genuinely new machinery — the server is entirely stateless w.r.t. `id` today
(wire recon §3.5) — and it must be counted in the estimate, not waved through.

### 3.4 Scenario D — one event

**Today** (F4; MEASURED byte shape from the wire recon's probe):

```
{"tool_call_id": "tc_1", "tool_name": "Bash", "args": {"command": "ls"}, "type": "tool_execution_start"}
```

**Standard dialect, Tier 1 — payload carried VERBATIM, correlation added around it:**

```
{"jsonrpc":"2.0","method":"aelix/event","params":{
   "sessionId":"s_1","promptId":"req_1","seq":17,
   "event":{"tool_call_id":"tc_1","tool_name":"Bash","args":{"command":"ls"},
            "type":"tool_execution_start"}}}
```

Three deliberate choices:

- **`params.event` is byte-identical to `_event_to_dict(event)` today** (`rpc_mode.py:271`). The
  emitter is `_output({"jsonrpc":…,"params":{…,"event": _event_to_dict(event)}})` — one wrap. This
  is what keeps `reduce_line` (F13) a one-line unwrap instead of a rewrite, and it is what keeps
  the cross-channel parity test *cheap*, because the json channel's events and the rpc channel's
  `params.event` remain the same objects after `json.loads`.
- **`promptId`** closes D3's correlation half — the thing the pi grammar and ACP both lack.
- **`seq`** closes the half **ACP does not give you either** (standards recon §6, D3 row:
  *"✖ for sequence numbers — still stream order only"*). A monotonic per-connection counter is four
  lines and makes gap detection possible on a lossy transport.

**Tier 2 (ACP conformance profile — explicitly NOT this sprint, see §6):**

```
{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"s_1","update":{
   "sessionUpdate":"tool_call","toolCallId":"tc_1","title":"Bash","kind":"execute",
   "status":"pending","rawInput":{"command":"ls"}}}}
```

**This is where the honest cost of "real ACP" lives, and I will not hide it.** aelix has 14 kernel
event types (`aelix-agent-core/types.py:165-290`, enumerated in the wire recon §2.C); ACP's
`SessionUpdate` union has ~8 variants. `auto_retry_start`, `auto_retry_end`, `compaction_start`,
`compaction_end` have **no ACP analogue at all**. A full ACP mapping is **lossy**, is camelCase
(vs aelix's snake_case `asdict`), and must be written twice (emit + parse). That is a real
translation layer, and it is why Tier 2 is deferred rather than smuggled into Tier 1.

### 3.5 Scenario E — one server→client request (the deferred approval back-channel)

This is the direction that **does not exist today at all**. Wire recon §7.3, MEASURED: four `_output`
call sites in the whole server (`rpc_mode.py:1879`, `:1935`, `:2030`, `:2038`, `:2061`), none of them
a request; `extension_ui_response` is dropped with a bare `return` at `rpc_mode.py:2048`.

```
← {"jsonrpc":"2.0","id":"srv_1","method":"session/request_permission","params":{
     "sessionId":"s_1",
     "toolCall":{"toolCallId":"tc_2","title":"Write packages/aelix-coding-agent/…/foo.py","kind":"edit"},
     "options":[
       {"optionId":"allow_once","name":"Yes, once","kind":"allow_once"},
       {"optionId":"allow_always","name":"Yes, auto-accept edits","kind":"allow_always"},
       {"optionId":"reject_once","name":"No","kind":"reject_once"}]}}

→ {"jsonrpc":"2.0","id":"srv_1","result":{"outcome":{"outcome":"selected","optionId":"allow_once"}}}
```

SPEC (ACP): *"If the current prompt turn gets cancelled, the Client MUST respond with the
`'cancelled'` outcome."* That is the deadlock rule aelix would otherwise have had to invent — a
pending permission request plus a cancelled turn is a hang, and ACP has already specified the answer.

Four additive pieces are required, exactly the four the wire recon §7.3 enumerates: a server-side
`srv_N` id space, a server-side pending-request map, a client-side inbound-request dispatcher, and a
reply path in `RpcClient` (whose `_send` at `rpc_client.py:538-575` always mints a *new* `req_N` and
therefore cannot answer a server-chosen id).

> ### ⚠ SECURITY — this is a NEW attack surface, not a solved one
>
> `options[].name` and `toolCall.title` are **agent-supplied human-readable text destined for a
> consent dialog**, arriving from a child process across a trust boundary. Kickoff handoff §5
> invariant 1 applies **unchanged and more sharply**: P3's review demonstrated *by execution* that a
> model-supplied `cwd` containing newlines and ESC could forge the entire dialog — the human reads
> `Permission: plan` and the issued grant is `auto-accept-edits`. Both new fields **MUST** route
> through the sanitiser, and **the child MUST NOT be allowed to choose `kind`** — the parent derives
> `kind` from its own posture clamp. ACP makes this field *more* reachable by untrusted input, not
> less. Standards recon §3.3 flags the same thing independently.

---

## 4. PI PARITY — the direct confrontation

The project's binding principle is stated verbatim at `docs/decisions/0103-sprint-6h9f-aelix-server.md:7-8`
(re-opened, MEASURED):

> Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

I am not going to dodge this. **Stated plainly: a big-bang JSON-RPC migration breaks that principle
on the rpc surface, unambiguously, and no framing makes that untrue.** The only version of Option B
that survives the principle is **dual-speak**, and that is why §5 treats dual-speak as *mandatory*
rather than as a migration convenience.

### 4.1 Every place the repo asserts pi parity on the rpc surface

| # | Site | What it pins | Breaks under **big-bang**? | Breaks under **dual-speak**? |
|---|---|---|---|---|
| P1 | `tests/pi_parity/test_phase_4_4_strict_superset.py:60-61` | `set(fixture["rpc_command_types"]) == RPC_COMMAND_TYPES` | **YES** | **No** — `RPC_COMMAND_TYPES` (`rpc_types.py:814`) is untouched |
| P2 | same file `:81` | `SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` | YES | No |
| P3 | same file `:89`, `:346` | `len(RPC_COMMAND_TYPES) == 29` | YES | No |
| P4 | same file `:355-384` `PI_COMMAND_FIELDS` — **29 per-command field rosters** | every `RpcCommand*` dataclass's exact field set | **YES, 29 rows** | **No** — no existing dataclass is edited |
| P5 | same file `:251-253` | `RpcSessionState.__dataclass_fields__` == 13 fixture keys | YES | No |
| P6 | same file `:258-262` | every `RpcSessionState().to_json()` key is camelCase (`assert "_" not in key`) | YES | No |
| P7 | same file `:268-307` | 9 `extension_ui_request` methods + 3 `extension_ui_response` shapes | YES | No |
| P8 | `test_phase_4_9_strict_superset.py` (28 tests), incl. the `pi_sha == 734e08e…` assert | pin identity + handler shapes | YES | No |
| P9 | `test_phase_4_13_strict_superset.py` (37 tests) — `_handle_fork`/`_handle_clone`/`_handle_switch_session` **wire shapes** asserted directly | response `data` shapes | **YES** | No |
| P10 | `test_phase_4_3` (14), `4_11` (26), `4_12` (27) | assorted rpc surface | YES | No |
| P11 | `tests/pi_parity/fixtures/pi_rpc_mode_734e08e.json` — prose `success_shape` / `error_shape` | the pi envelope literally, in a string | **YES** | No |
| P12 | ~every dataclass docstring in `rpc_types.py` — e.g. `:57` *"Pi parity: ``rpc-types.ts:22``"*; `:488` *"Pi parity: error envelope, ``rpc-types.ts:204-205``"* | citation provenance | YES (all become misleading) | No |
| P13 | ~every handler docstring in `rpc_mode.py` — e.g. `:293` *"Pi parity: ``rpc-mode.ts:237-260``"*, `:337` *"``rpc-mode.ts:272-275``"* | citation provenance | YES | No |
| P14 | `docs/decisions/0058-…:196-201` — *"the durable RPC boundary every multi-language client routes through"*, *"a Pi-shaped client that dispatches on the `BashResult` 4/5-key shape works against Aelix today"* | a prose commitment | **YES — this sentence becomes false** | **No — it stays literally true** |
| P15 | ADR-0103:7-8 binding principle | the principle itself | **YES** | **Arguable — see §4.2** |

**Measured totals: 152 `def test_` across 6 files (F10) + 1 fixture + ~60 docstring citations + 2
ADR prose commitments. Under dual-speak, the break count is ZERO.** That is not rhetoric; it is a
consequence of the fact that dual-speak adds a second encoder/decoder pair and edits no existing
dataclass, no existing `to_json`, and no existing handler signature.

### 4.2 Does dual-speak actually satisfy the principle, or is that a lawyer's reading?

**Both, and the owner should be told which is which.**

The defensible half (INFERRED but strong): the principle says *"pi **agent**"*. Under dual-speak a
pi-shaped client that has never heard of JSON-RPC connects, sends `{"type":"prompt","message":"…"}`,
and receives byte-identical output to today — *including* the pi-shaped defects. ADR-0058's own test
(P14) still passes literally. The repo's canonical precedent, **ADR-0035**, states the bar
explicitly (quoted in the pi recon §5): the test is not *"does pi do it"* but
**"does a pi-shaped consumer still work unchanged?"** — and under dual-speak the answer is yes.

The lawyer's half, which I am flagging as such: a two-grammar server is *more* than pi. It has 14
`aelix-original` precedents in `docs/decisions/` (measured count, pi recon §5) and 8
`documented divergence` ones, so the vocabulary exists. But **a reader of ADR-0103 would not
recognise a two-grammar server as "완전 동일하게"**, and pretending otherwise would be exactly the
kind of soft-pedalling that produced the false `rpc_mode.py:316-320` comment this sprint has to go
correct. If Option B is chosen, the closure ADR must say **"this is an aelix-original divergence on
the rpc wire, with a permanently maintained pi-compatibility dialect"** — in those words — and the
owner must sign it, not infer it.

### 4.3 The one parity claim Option B *improves*

pi's own upstream position is **unrecorded** (pi recon §7; standards recon §8.3: discussion #4444
and issue #175 exist, no maintainer accept/reject). But the community answer on pi itself is
`pi-acp`, whose description is *"…spawns `pi --mode rpc`, bridging requests/events between the two"*
(standards recon §3.1). **The community's demonstrated shape on aelix's own upstream is
adapter-over-rpc, not replace-rpc.** That is evidence *against* Tier 2 being urgent — and it is also
the strongest argument for keeping the pi dialect alive forever, since a future `aelix-acp` adapter
could be written against either dialect.

---

## 5. MIGRATION — dual-speak, and exactly how it is decided

### 5.1 Big-bang is rejected, with the number attached

Big-bang cost, MEASURED: 152 pi-parity `def test_` + the fixture's prose shapes + `PI_COMMAND_FIELDS`'
29 rosters + 23 of 31 `tests/rpc` files + 5 wire asserts in `tests/server/test_server.py` + ~60
docstring citations, **and** an outright violation of ADR-0103 with nothing to hide behind.
Benefit over dual-speak: one fewer encoder. **Rejected.**

### 5.2 What detects the dialect

**The presence of the `"jsonrpc"` member on the command line.** This is not an invention — SPEC
(JSON-RPC 2.0, §Overview) says the presence of that member *is* the version signal that distinguishes
2.0 from 1.0. And it is **provably unambiguous here** because of F2: the string `"jsonrpc"` appears
nowhere in `packages/`, and the pi grammar's discriminator is `type`, which is never `"2.0"`.

```python
# rpc_mode.py :: _on_line (:2024-2073), after json.loads at :2028
# and the isinstance-dict check at :2037
dialect = "standard" if "jsonrpc" in payload else "pi"
```

### 5.3 Where the latch lives, and why there must be one

**Per-connection, latched by the first line that parses as a JSON object. Once latched, the server
emits only that dialect.**

The latch is required by the **server→client** direction, not the client→server one. Commands could
be classified per-line forever, but events cannot: emitting each event in both grammars would double
every line on the stream and break every reducer downstream. So the emit side must pick, and it must
pick before the first event.

**Is the latch always set before the first event? (INFERRED, and it needs a test.)** Events reach
`_output` only via `_on_agent_event` (`rpc_mode.py:1927`), which fires from harness activity, which
requires a `prompt`/`steer`/`follow_up` command. Blank lines return early at `rpc_mode.py:2025-2026`
without latching, which is correct. I did **not** verify that no harness event can fire from a
pre-seeded session before any command arrives — **that is a hole a conformance test must close**, and
if it turns out to be reachable, the default-unlatched dialect must be `pi`.

### 5.4 The pre-latch parse error — a real edge case with an ugly answer

If the very first line is malformed JSON, there is no dialect to answer in. Three options, and the
choice matters:

- Answer in both (two lines) — **rejected**, it corrupts a strict client.
- Stay silent — **rejected**, it turns a typo into a hang.
- **Chosen: answer pre-latch parse errors in the pi dialect** (`{"type":"response","command":"parse",…}`,
  exactly today's bytes at `rpc_mode.py:2029-2043`), and **require the standard dialect's first line
  to be a well-formed `initialize`.** A standard client MUST treat any first response lacking
  `"jsonrpc"` as a fatal handshake failure. This is documented behaviour, not an accident.

There is a second, subtler edge case worth pinning: **parse errors jump the queue.** MEASURED (wire
recon §3.6): parse-failure responses are written *synchronously* inside `_on_line` (`rpc_mode.py:2030`,
`:2038`) while a valid command's response is written at `:2061` from a detached task spawned at
`:2071`. The dialect latch does not change that, so a standard client must still correlate by id and
must not assume request order.

### 5.5 When does the pi dialect die?

**It does not. Not under the current principle.**

I want this to be the least comfortable sentence in the note, because it is the honest one. ADR-0103's
binding principle plus the 152 pi-parity test defs make the pi dialect a **permanent** surface, not a
migration ramp. Every future rpc command must then be implemented twice, tested twice, documented
twice, and kept semantically aligned — including the *defects*, because the pi dialect's defects are
what the parity fixtures pin.

The only exit is an owner decision to amend the principle. If that ever happens, retirement is one
clean PR: delete the latch, delete the pi encoder/decoder, retire the 6 pi-parity rpc files and the
fixture. **Budget for two grammars forever; treat "one day we delete it" as wishful.**

---

## 6. WHAT BREAKS, SITE BY SITE

### 6.1 `packages/aelix-server` — **zero source lines**

MEASURED (F9): the WS bridge performs no translation. `rpc_ws.py:115-120` receives text, strips one
trailing `\n`, feeds bytes to the same `JsonlLineReader`; `:136` sends each `stdout_write` byte string
as one text frame. The only JSON-ish string in the file is `"utf-8"`. A grammar change flows through
untouched, and `run_rpc_mode` is invoked verbatim at `:146`.

Two consequences worth recording:

- The single-flight guard (`rpc_ws.py:66-68`, `close(code=1013)` **before** `accept()`) means one
  connection ⇒ one latch. No cross-connection dialect leakage is possible today. If single-flight is
  ever lifted, the latch must move to per-connection state rather than module state — **note this
  now**, because `redirect_stdout(sys.stderr)` being process-global (`rpc_mode.py:1847-1849`, built
  unconditionally even when `stdout_write` is injected) is already the reason single-flight exists.
- WS delivers one frame per record, which is an *emergent* property of `_output`, not a documented
  guarantee (consumers recon §2.1). The standard dialect does not change that; it should be written
  down in the new protocol doc.

### 6.2 `tests/rpc/` — **31 files, 188 `def test_` — zero break, plus new files**

All 188 drive the pi dialect (in-process `run_rpc_mode` against a fake harness, or `RpcClient`
subclassed to swap `_build_argv` for an inline stub). Under dual-speak they all keep passing
unchanged. New work:

- `tests/rpc/test_jsonrpc_dialect.py` — envelope conformance, the error table, `initialize`, the
  ignore-unknown-params rule (§2.5), batch rejection.
- `tests/rpc/test_dialect_latch.py` — the latch, the pre-latch parse-error rule (§5.4), and the §5.3
  hole (no event may precede the latch).
- `tests/rpc/test_real_child_rpc.py` — **the real-child bed, which does not exist.** Spec §8 claims it
  does; the envelope recon measured that it does not (`tests/rpc/test_rpc_client_lifecycle.py:88`'s own
  comment: *"RpcClient configured to launch the inline stub instead of `-m aelix`"*). Budget for it
  explicitly.

### 6.3 `tests/server/` — **17 tests, 5 wire-asserting — zero break**

The 5 (`tests/server/test_server.py:126-136`, `:142-174`, `:232-247`) assert only pi-dialect response
envelope keys and keep passing. One new test: a standard-dialect `initialize`/`session/prompt`
round-trip over `WS /rpc`, proving F9 empirically rather than by reading.

### 6.4 `tests/pi_parity/` — **152 `def test_` — zero break. This is the whole point of §5.**

See the P1–P13 table. No existing dataclass, `to_json`, or handler signature is edited, so every
exhaustiveness pin, every field roster and the camelCase assert all hold. **The closure ADR must
additionally state that the standard dialect is *outside* the strict-superset pin's scope**, or a
future author will reasonably ask why `aelix/compact` is not in `RPC_COMMAND_TYPES`.

### 6.5 README and the public docs — **nothing breaks; something is missing**

MEASURED, re-opened:

- `README.md:50-51` — *"`--print`, line-delimited `--mode json`, and a `--mode rpc` JSONL protocol…"*
- `docs/guides/getting-started.md:74` — *"`aelix --mode rpc` | headless | JSONL command/response protocol on stdio"*

**JSON-RPC 2.0 over NDJSON is still "a JSONL command/response protocol on stdio". Both sentences stay
true verbatim.** The consumers recon established (MEASURED) that no public doc promises pi
compatibility at all — the only `pi` mention in `README.md:207-209` is a license/attribution
statement. So the wire change requires **zero README corrections**.

What it *does* require is the thing that is missing today: **there is no RPC protocol reference
document anywhere in the repo** (consumers recon §4, MEASURED — `docs/guides/` has 5 files, none about
rpc). A user reading only the shipped docs cannot write a client. Option B should ship
`docs/guides/rpc-protocol.md`, and `docs/contracts/` should finally get the RPC schema that
`docs/decisions/0097-multi-frontend-architecture.md:118-119` already names as part of the binding
cross-repo contract and that **was never generated**.

### 6.6 The blocking prerequisite nobody has done

**F1: `aelix --mode rpc` raises `RuntimeError` before emitting a byte** — I reproduced it. `entry.py:2160-2166`
passes `repo=repo, fs=fs` alongside `runtime_host=runtime`; `rpc_mode.py:1910-1914` rejects exactly
that combination. **No real-child test of any dialect can exist until this ~2-line fix lands.** It is
a prerequisite of Option A, Option B, and the sprint deliverable alike. It is also why the wire has
never been exercised in production and why F14 is true.

Related and measured (wire recon §9.2): `model_registry=` is passed by **neither** `entry.py:2160-2166`
**nor** `rpc_ws.py:146` — so `set_model` / `cycle_model` / `get_available_models` return E10/E12/E13
unconditionally on both live paths. Spec §8 item 3 wants `RpcChannel.set_model`. **`set_model` is dead
today on every path**; Option B must fix the wiring or the channel's `set_model` is decorative.

---

## 7. IS THIS ONE SPRINT? — no. Two, with a defined first slice.

### 7.1 Honest sizing

**Option B ⊇ Option A.** It does not remove one line of Option A's work:

- The `entry.py` fix (§6.6) is owed either way.
- The pi-dialect terminator fixes are owed either way — the pi recon §4b establishes they are
  **parity restorations against a pi behaviour aelix already partially ported**
  (`harness/core.py:4298-4316`'s own comment reads *"Pi parity: synthesize failure assistant message +
  emit closure events"*), not optional polish. Option B changes their *criticality*, not their
  existence.
- `exit_code` policy (`aelix_agents/envelope.py:203` — any non-None non-zero ⇒ failure, and an rpc
  child's returncode is a teardown artifact) and `dropped_lines` (structurally 0 on rpc — the rpc read
  path has no budget; `_jsonl.py:61-73` vs `stream.py:74-155`) are **envelope decisions untouched by
  any wire standard**. Owed either way.

So the delta of Option B over Option A is: the JSON-RPC codec, the method map, the error table,
`initialize`, the dialect latch, the prompt-is-the-turn handler, and their tests.

### 7.2 Scope estimate

| File | Change | Band |
|---|---|---|
| `cli/entry.py:2160-2166` | drop `repo=`/`fs=` (F1); pass `model_registry=` (§6.6) | product-core |
| `rpc/rpc_types.py` | **additive only** — JSON-RPC envelope dataclasses, method map, error table. ~+250. No existing dataclass touched (that is what keeps P1–P7 green) | product-core |
| `rpc/rpc_mode.py` | dialect latch in `_on_line` (`:2024-2073`), standard encoder beside `_output` (`:1879`), prompt-is-the-turn, `initialize`, in-flight prompt registry. ~+300 / −20 | **product-core — this is the kickoff §3 fork** |
| `rpc/_jsonl.py` | **unchanged** | — |
| `rpc/rpc_client.py` | sibling `JsonRpcClient` (or a dialect flag); the containment seam (`start_new_session`, `preexec_fn`, explicit `limit=`) that `start()` lacks — `create_subprocess_exec` at `:121-128` passes none of them. ~+200 | product-core (already on the band gate's spawn allowlist) |
| `aelix_agents/rpc_channel.py` | **NEW** — depth env, `--no-agents`, `AELIX_MCP_CONFIG` pop, its own argv (**never `-m aelix`**, `rpc_client.py:454-471`), injectable `argv_builder`/`env_builder` mirroring `print_channel.py`. ~+400 | extension |
| `aelix_agents/stream.py:406-468` | additive `reduce_event(state, dict)`; `reduce_line` becomes loads + unwrap + `reduce_event`. ~+20 | extension |
| `aelix_agents/envelope.py:203` | `exit_code` policy for a server that never self-exits | extension |
| tests | ~3 new rpc files, 1 real-child bed, 1 parity test, 1 server test | — |
| docs | closure ADR (divergence record + the rpc half of ADR-0198) + `docs/guides/rpc-protocol.md` + an RPC schema in `docs/contracts/` | — |

**Two sprints minimum.** Anyone quoting one sprint is quoting the codec and forgetting the real-child
bed that does not exist, the containment seam that has no home, and the `initialize` handshake that
implies a version policy nobody has written.

### 7.3 The minimum first slice that still lets `RpcChannel` + the parity test ship

Cut the standard dialect to **six methods**:

`initialize` · `session/prompt` · `session/cancel` · `aelix/set_model` · `aelix/get_state` · `aelix/get_available_models`

That is exactly what spec §8 item 3 asks the channel to expose (`prompt_and_wait` / `stop` /
`set_model`) plus the two reads a channel needs to be diagnosable. **The other 24 commands remain
reachable only via the pi dialect in slice 1** — which is fine, because F14 says nothing else consumes
this wire.

**Sequencing that banks the deliverable before the risky work starts:**

1. Fix F1 (`entry.py`, ~2 lines). Nothing is testable before this.
2. Build the real-child test bed. It does not exist and everything downstream needs it.
3. Land the pi-dialect terminator fixes (Option A's core). Owed regardless.
4. **Build `RpcChannel` + the cross-channel parity test against the pi dialect.** ← *the deliverable
   is banked here.*
5. Land the JSON-RPC codec, the error table, `initialize`, the latch, and the six methods.
6. Re-point `RpcChannel` at the standard dialect; the parity test gains a `params.event` unwrap and
   otherwise does not change.

If step 5 overruns, the sprint still closes with the deliverable shipped. **That property is the
single reason this option is defensible on schedule at all**, and it should be treated as a hard
constraint, not a nicety.

**Deferred out of slice 1, explicitly:** `session/request_permission` (§3.5) and everything ACP-Tier-2
(§3.4). The back-channel needs ADR-0197 §(i) blocker (c) — the parent TUI's missing modal arbiter —
which is a UI problem no wire standard touches, and which the kickoff handoff §5 pairs with residual
R3 for a later sprint anyway.

### 7.4 What happens to the parity test specifically

It **ships and changes shape by one line.** The envelope recon's field classification is unaffected:

- MUST-MATCH: `ok`, `status`, `summary`, `truncated`, `stop_reason`, `usage.*`, `profile`,
  `dropped_tools`, `permission_mode`.
- CANNOT-COMPARE-LITERALLY: `id`, `elapsed_ms` (rpc pays `STARTUP_GRACE_MS = 100`, `rpc_client.py:88`),
  `exit_code`, `usage.cost`, `details`, `error`, `dropped_lines`.
- **Byte parity remains impossible** either way: print uses `json.dumps` default `ensure_ascii=True`
  (`print_mode.py:161`), the rpc framer uses `ensure_ascii=False` (`_jsonl.py:31`). Value-level only.

The rpc side reads `params.event` instead of the bare object. That is the entire delta, and it is
delta *because* §3.4 chose verbatim payload carriage over ACP's `SessionUpdate` taxonomy.

---

## 8. WHY THIS IS THE WRONG CHOICE

The strongest case against this design. I believe several of these are decisive.

### 8.1 It buys **zero interop**, which was the entire premise

A standard is worth its migration cost when someone else speaks it. **Nobody speaks this.** Tier 1 is
JSON-RPC with an `aelix/*` method namespace and an `aelix/event` notification carrying snake_case
kernel dataclasses. Zed cannot drive it. JetBrains cannot drive it. No SDK targets it. The standards
recon says exactly this about bare JSON-RPC (§7.2): *"you would end up speaking a standard envelope
that no other tool actually consumes."* **Real interop lives in Tier 2, and §6/§7.3 defer Tier 2.**
So Option B pays the *full* migration cost for the *design* dividend — and the standards recon's §7
closing paragraph says you can take that dividend **for free**: adopt ACP's `StopReason` vocabulary
and JSON-RPC's `-32000…-32099` band *inside the existing pi envelope*, costing nothing today and
making a future ACP adapter nearly free. **If that is true — and I did not find a fact contradicting
it — then §2–§5 of this note are an elaborate way to buy something that was on offer for nothing.**

### 8.2 The sprint's actual blockers are semantic, and Option B front-loads the non-blockers

D1 (no terminator) and D2 (early ack) are the two defects that break the deliverable. Both are
**semantic**, both are fixed at `rpc_mode.py:309-320` and `:333-340` in roughly 15 lines, and both get
fixed **in either grammar**. The defects Option B fixes *structurally* — D4 error codes, D5 handshake,
D7 bidirectionality — are real, but **none of them blocks the `RpcChannel` or the parity test.** This
option reorders the work so that the non-blocking half comes first.

### 8.3 It creates a permanently two-grammar server, and §5.5 admits it

Under ADR-0103 the pi dialect never dies. `rpc_mode.py` is already 2142 lines carrying two *spellings*
on one fd (camelCase responses beside snake_case events — measured D6, and the boundary is not even
clean: `get_messages`/`compact`/`get_session_stats` are snake_case `data` payloads). Option B makes it
**two grammars × two spellings**, forever, with every new command implemented and tested twice — and
the pi half must preserve its *defects*, because the parity fixtures pin them. The tail risk is
specific and nasty: the two dialects drift semantically while both test suites stay green, because
nothing cross-checks them.

### 8.4 ACP's permission flow does **not** subsume the deferred back-channel

The task's premise — *"especially if ACP's permission/request flow subsumes the deferred child→parent
approval back-channel"* — is **measurably half-false**, and the half that fails is the important half:

- ACP's permission is **per-tool-call within a turn**. aelix's P2/P3 consent is **spawn-time and
  per-delegation** (standards recon §3.3, item 3). Different events. Mapping one onto the other is
  design work, not adoption.
- ACP has **no concept** of aelix's depth guard, fork-bomb prevention, band rule, posture clamp, or
  per-prompt delegation budget (standards recon §6, "Neither standard fixes…"). All of P2/P3's
  governance stays exactly where it is.
- ADR-0197 §(i) **blocker (c) — the parent TUI has no modal arbiter — survives untouched**, and it is
  the harder half. ACP gives you the message; it does not render a dialog, does not sanitise, does not
  clamp.
- ACP *adds* an attack surface (§3.5's box). P3 proved by execution that unsanitised dialog text
  forges consent. `PermissionOption.name` is a **new** untrusted field on that path.

**So the strongest single argument for Option B — "the back-channel comes for free" — is not true.**
What comes free is the *wire shape* and the cancel-precedence rule. The expensive parts were never on
the wire.

### 8.5 It optimises the wrong end of a server that has never run

**MEASURED, and I reproduced it: `aelix --mode rpc` raises before emitting one byte** (F1). The stdio
RPC server has never worked in production. `set_model`/`cycle_model`/`get_available_models` are dead on
*both* live paths for want of a `model_registry=` kwarg (§6.6). The default client argv points at a
mock demo (`rpc_client.py:454-471` → `src/aelix/__main__.py`'s mock `stream_fn`). Proposing a new
grammar for a server that has never started, whose model commands are inert, and whose only client
launches a demo, is optimising the wrong end. **Fix the ~2-line startup bug, wire the registry, build
the channel — then ask whether the grammar was ever the problem.**

### 8.6 It violates the project's stated binding principle, and the mitigation is the disease

§4.2 is honest that "dual-speak satisfies the principle" is half a lawyer's reading. A reader of
ADR-0103:7-8 would not recognise a two-grammar server as *"완전 동일하게 완벽하게"*. And the mitigation
— maintain the pi grammar forever — is *precisely* the sort of permanent complexity a
"be identical to pi" principle exists to prevent. There is a real risk this option is chosen because
JSON-RPC *sounds* more principled than a bespoke protocol, when the measured facts say the bespoke
protocol's problems are all semantic.

### 8.7 The timing argument cuts both ways

§1's F14 says *"if the wire is ever going to change, this sprint is the cheapest moment."* True. But
the mirror image is equally true and more urgent: **every day the `RpcChannel` is not written is a day
the depth-guard fork bomb, the containment seam and the `exit_code` policy stay unbuilt** — and those
are what make delegation *safe*. The kickoff handoff §4 is explicit: *"A naive `RpcChannel` spawns
children that think they are the root… the fork bomb the depth guard exists to prevent."* **Safety
work should not queue behind an envelope refactor.**

### 8.8 The honest counter-proposal, in one sentence

Do Option A now — fix `entry.py`, fix the terminator in both layers, ship the `RpcChannel` and the
parity test — and in the same closure ADR **adopt ACP's `StopReason` vocabulary
(`end_turn`/`max_tokens`/`max_turn_requests`/`refusal`/`cancelled`) and JSON-RPC's `-32000…-32099`
band as the *names* for what you build**, so that if Option B is ever chosen, the semantics already
match and only the envelope has to move.

---

## 9. UNCERTAINTIES — what I did NOT measure

1. **ACP's framing is not stated on the `protocol/overview` spec page.** NDJSON is from the GitHub
   README and the Python SDK description (standards recon uncertainty #1). HIGH confidence, not
   spec-page-verified. Confirm against the SDK transport source before committing.
2. **`auth_required`'s ACP integer** was not obtained. `-32016` in §2.4 is a placeholder.
3. **Whether ACP permits concurrent prompts on one session** was not verified (standards recon
   uncertainty #4). This interacts directly with P3's parallel mode and with §3.3's busy semantics.
4. **`agent-client-protocol` 0.11.1's transitive deps and license** were not checked. Required before
   any dependency proposal, given this repo's SBOM/OSS-compliance gates (`mcp>=1.27,<2` is the
   precedent at `packages/aelix-coding-agent/pyproject.toml:34`).
5. **The §5.3 latch hole** — that no harness event can reach `_output` before the first command
   latches the dialect — is **INFERRED from the emit path, not proven.** It needs a test, and if it is
   reachable the unlatched default must be `pi`.
6. **I did not run the test suites.** The 188 / 152 / 17 figures are `def test_` counts and `ls`
   counts, not pass counts, and the 152 differs from the consumers recon's "155 collected" because
   `--collect-only` expands `parametrize`.
7. **I did not enumerate which of the 188 `tests/rpc` tests would fail under a big-bang** — §5.1 sizes
   the bucket, not the blast radius.
8. **The 24-of-29 ACP command gap is INFERRED** from comparing two measured lists; no per-command
   mapping was attempted, and ACP's `_meta` may absorb more than §2.3 assumes.
9. **`rpc_mode.py`'s split between handler surface and lifecycle was not measured**, so "the codec is
   ~+300 lines" in §7.2 is a shape estimate, not a sized one.
10. **Whether widening `except AgentHarnessError` at `harness/core.py:4298` is side-effect-free** was
    not assessed (pi recon §7 flags the same). It is a kernel edit needing its own blast-radius review.

---

## 10. CROSS-REFERENCES

- `.omc/specs/rpc-protocol-recon-wire.md` — the as-built wire grammar. §9.1 is F1.
- `.omc/specs/rpc-protocol-recon-pi.md` — §0.1/§0.2 invert the kickoff's §3 conclusion; §5 is the
  divergence bar (ADR-0035's test).
- `.omc/specs/rpc-protocol-recon-consumers.md` — the zero-external-breakage census.
- `.omc/specs/rpc-protocol-recon-standards.md` — the defect × standard matrix; §7 is the
  counter-proposal in §8.8.
- `.omc/specs/rpc-sprint-kickoff-handoff.md` — the product-core fork and the five invariants.
- `docs/decisions/0198-print-mode-json-envelope-contract.md` — the `json` half; its *Partial
  fulfilment* names this sprint's two deliverables.
- `docs/decisions/0058-phase-4-4-strict-superset-closure.md:196-201` — the prose commitment (P14).
- `docs/decisions/0103-sprint-6h9f-aelix-server.md:7-8` — the binding principle (§4).
- `docs/decisions/0035-error-code-taxonomy.md` — the canonical divergence precedent.
