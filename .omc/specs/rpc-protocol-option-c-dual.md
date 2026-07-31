# Option C — DUAL-STACK / LAYERED

> **Written:** 2026-07-29, RPC-sprint design phase. Repo at `main` = `da61337`.
> **Nothing was edited.** Every `path:line` below was **re-opened against `da61337`**, not copied
> from an older note. Where I re-verified a number quoted in a recon note, I say so.
>
> **MEASURED** = I ran the command or opened the file at `da61337`.
> **INFERRED** = reasoning on top of measured facts, labelled inline.
>
> **Companion ground truth (read these first):** `rpc-protocol-recon-wire.md`,
> `rpc-protocol-recon-pi.md`, `rpc-protocol-recon-consumers.md`, `rpc-protocol-recon-standards.md`,
> `rpc-sprint-kickoff-handoff.md`.

---

## 0. The option in one paragraph

Keep `--mode rpc` **byte-frozen** as the pi dialect, for the pi-parity fixture pins and for anything
that already depends on it. Introduce a **second, explicitly versioned dialect** behind a new
`--mode` value, selected by CLI flag, sharing **one process, one transport, one dispatch table and
all 29 handlers**. The seam is a new injectable `dialect:` parameter on `run_rpc_mode` — a *record*
codec, not the existing *byte* seams. The subagent `RpcChannel`, `aelix-server`/`aelix-web`, and any
third party speak the new dialect; the pi dialect survives only to satisfy `tests/pi_parity/*`.

**And I will argue in §7 that the full version of this is the wrong call, for six measured reasons —
the strongest being that the population depending on the pi wire is provably zero, so Option C
builds a compatibility layer for nobody at the single cheapest moment the wire will ever be
changeable.** §8 names the one variant of Option C I would actually defend.

---

## 1. Where the seam lives — measured, not assumed

### 1.1 The pipeline as built

`run_rpc_mode` is defined at `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:1787`
and runs to `:2137`. Inside it (all MEASURED, `grep -n` + read):

```
bytes ── JsonlLineReader(_on_line)                     rpc_mode.py:2075
      ── _on_line(line: str)                           rpc_mode.py:2024
         ├─ json.loads(line) → payload: dict           rpc_mode.py:2028
         ├─ parse-error writes (SYNCHRONOUS)           rpc_mode.py:2030, :2038
         ├─ extension_ui_response → bare `return`      rpc_mode.py:2048
         └─ asyncio.create_task(_dispatch())           rpc_mode.py:2051, :2072
            ├─ _handle_command(harness, payload, dispatch) -> RpcResponse
            │                                          call :2057, def :1747
            │  ├─ parse_rpc_command(payload) -> RpcCommand   :1759 (rpc_types.py:392)
            │  └─ dispatch[cmd.type](harness, cmd)           :1770-1776
            └─ _output(response.to_json())             rpc_mode.py:2061

events ── harness.subscribe(_on_agent_event)           rpc_mode.py:1974 (def :1927)
       └─ _event_to_dict(event) = dataclasses.asdict   rpc_mode.py:271 / :258-268
          └─ _output(payload)                          rpc_mode.py:1935

_output(obj) = write_sink(serialize_json_line(obj).encode("utf-8"))
                                                       rpc_mode.py:1879-1880
```

The four `_output` call sites are `:1935`, `:2030`, `:2038`, `:2061`, plus the definition at `:1879`
— **re-verified at `da61337`; the wire recon's §7.1 table is exact and still holds.**

### 1.2 The one fact that makes Option C structurally possible

**`_handle_command` is dict-in, dataclass-out.** Its signature (`rpc_mode.py:1747-1751`) is
`(harness, payload: dict[str, Any], dispatch) -> RpcResponse`, and it returns
`RpcSuccessResponse` / `RpcErrorResponse` **objects**. `.to_json()` is called by the *caller*, at
`:2061`. (MEASURED.)

The dispatch table's values are `Callable[[Any, Any], Awaitable[RpcResponse]]`
(`build_dispatch_table`, `rpc_mode.py:1572-1633`) — they take a **parsed dataclass** and return a
**dataclass**. Not one of the 29 handlers touches a wire spelling, a byte, or an envelope key.

**Therefore: one dispatcher genuinely can serve two grammars without forking the handlers.** The
grammars differ in exactly two places — how a line becomes an `RpcCommand`, and how an `RpcResponse`
or a harness event becomes a line. Both are outside every handler. (MEASURED for 28 of 29 commands;
`prompt` is the exception and it is the one that matters — see §5.1 and §7.6.)

### 1.3 The existing transport seams are the WRONG place for the adapter

`run_rpc_mode` already exposes `stdin: asyncio.StreamReader | None` and
`stdout_write: Callable[[bytes], None] | None` (`rpc_mode.py:1795-1796`), and that is exactly how
`aelix-server` reuses it — `packages/aelix-server/src/aelix_server/rpc_ws.py:146-153` passes
`stdin=reader, stdout_write=stdout_write` and translates nothing (`rpc_ws.py:106-138`; the only
JSON-ish string in the file is `"utf-8"`). (MEASURED.)

**Those are BYTE seams. A dialect adapter needs a RECORD seam.** Three measured reasons the
`stdout_write` seam cannot carry the dialect:

1. **It has already lost the type.** `_output` receives a **dict**, because `:2061` calls
   `response.to_json()` first and `:1935` passes an already-`asdict`-ed event. An adapter at
   `stdout_write` would have to *re-derive* "is this a response or an event?" by sniffing
   `payload.get("type") == "response"` — reconstructing information the caller had two frames up
   and discarded. That is the fragile "sniff your own output" pattern, and it breaks the moment a
   `data` payload legitimately contains `{"type": "response"}`.
2. **It has already lost the correlation.** JSON-RPC and ACP both need the originating request id on
   the response. At `_output` the id is inside the dict, but the *command that produced it* is gone.
   For the standard dialect's `session/prompt`-as-a-turn semantics (§5.1) the adapter needs the
   originating payload, which only `_dispatch` (`:2051-2062`) still holds.
3. **It cannot suppress or synthesize.** A dialect that maps 14 aelix events onto a smaller
   `session/update` set must be able to drop records; one that has to emit a terminator the pi
   grammar never emits must be able to originate records. A `bytes -> None` sink can do neither
   without parsing back what it was just handed.

### 1.4 The proposed seam — a `dialect:` parameter, ~15 lines of product-core

```python
# NEW module: rpc/dialect.py  (product-core, additive)
class RpcDialect(Protocol):
    def bind(self, emit: Callable[[dict[str, Any]], None]) -> None: ...
    def decode(self, payload: dict[str, Any]) -> dict[str, Any] | None: ...
    def encode_response(
        self, request: dict[str, Any] | None, resp: RpcResponse
    ) -> dict[str, Any] | None: ...
    def encode_event(self, event: Any) -> dict[str, Any] | None: ...
```

Three call-site edits inside `run_rpc_mode`, and nothing else:

| Site (current) | Becomes |
|---|---|
| `rpc_mode.py:2028` `payload = json.loads(line)` … then straight to `_dispatch` | `payload = dialect.decode(payload)`; `if payload is None: return` |
| `rpc_mode.py:2061` `_output(response.to_json())` | `rec = dialect.encode_response(payload, response)`; `if rec is not None: _output(rec)` |
| `rpc_mode.py:1935` `_output(payload)` (event) | `rec = dialect.encode_event(event)`; `if rec is not None: _output(rec)` |

plus `dialect.bind(_output)` immediately after `_output` is defined (`:1880`), so the dialect can
originate records asynchronously — which scenario 1 (§5.1) proves it must.

The default is `PiDialect`, whose three methods are literally `payload`, `resp.to_json()`,
`_event_to_dict(event)` — **a provable no-op**. That is the parity guarantee, and it is testable two
ways: (a) a golden byte-comparison of `--mode rpc` output before/after, (b) an identity assertion on
`PiDialect` itself. Byte-freezing `--mode rpc` becomes a property you can *prove*, not a promise.

**Parse errors (`:2030`, `:2038`) deliberately stay outside the dialect** in the minimal design.
That is a real wart: the standard dialect must report a parse failure as `-32700`, so those two
sites need `dialect.encode_parse_error(...)` too, which is a fourth method and a fourth edit. I am
counting it: **4 methods, 5 call-site edits, ~20 lines in `rpc_mode.py`.**

### 1.5 Product-core cost, stated against the band gate

`rpc/rpc_mode.py` and a new `rpc/dialect.py` are **product-core**
(`PRODUCT_CORE_SRC` = `packages/aelix-coding-agent/src`, `tests/agents/test_p2_band_boundaries.py:32`).
The machine gate is a **text scan for spawn calls, spawn consent tokens and cap constants**
(`:247`, `:304`, `:350`, `:461`), not a no-edit gate. A codec module declares no cap, spawns nothing,
and authors no consent, so **the gate stays green.** (MEASURED — read the gate's four product-core
tests and its `_SPAWN_ALLOWLIST` at `:46-49`, which already lists `rpc/rpc_client.py`.)

So Option C's product-core delta is a **policy** question, exactly as the kickoff handoff §3 frames
it — not a gate question.

---

## 2. Can one dispatcher serve two grammars? — yes, with one measured exception

**Yes for 28 of 29 commands.** The dispatch table is grammar-independent by construction (§1.2), and
`build_dispatch_table` is already called with injected collaborators through three arity-adapters
(`_bind_registry` `:1639`, `_bind_runtime_host` `:1652`, `_make_missing_runtime_handler` `:1667`).
A second grammar adds a **fourth** adapter layer conceptually, but zero new handler bodies.

**No for `prompt`, and `prompt` is the command the RpcChannel lives on.** `_handle_prompt`
(`rpc_mode.py:288-330`) does this, MEASURED:

```python
task = asyncio.create_task(_run())          # :322
harness._pending_tasks.add(task)            # :327
return RpcSuccessResponse(id=cmd.id, command="prompt")   # :330
```

The ack at `:330` is written **before** `harness.prompt` at `:309` is ever awaited. In the standard
dialect the response **is** the turn terminator, so the handler must not return until the turn
resolves. That is not a serialization difference; it is a different handler. Either
`_handle_prompt` grows a dialect-aware branch (a fork, the one thing Option C promised to avoid), or
it grows a **preflight seam** — pi's model, `rpc-mode.ts:388-393` per the pi recon §4b, where
`output(success(id,"prompt"))` fires only from `preflightResult(didSucceed=true)`.

**The preflight seam is the right answer and it is not a dialect feature.** It is a
parity-restoration owed regardless of Option C (pi recon §4b, §5). Adding it makes both dialects
correct. Not adding it leaves both dialects broken. **Option C neither helps nor hurts here — which
is the theme of §7.1.**

---

## 3. How the dialect is selected — CLI flag, and specifically a new `--mode` value

### 3.1 The argument for a flag

**Measured constraints:**

- `ModeLiteral = Literal["text", "json", "rpc"]` (`cli/args.py:31`) and
  `VALID_MODES = ("text", "json", "rpc")` (`cli/args.py:46`). A fourth value is a 2-line change plus
  the validator.
- `entry.py:2157` is a single `if app_mode == "rpc":` branch calling `run_rpc_mode` at `:2160-2166`.
  Threading a `dialect=` argument into it is one line.
- **The one consumer that matters already builds the argv.**
  `packages/aelix-coding-agent/src/aelix_coding_agent/agents/resolver.py:201` emits
  `["--mode", "rpc"]` for the `oneshot=False` path. (MEASURED — that is the exact line, and the
  consumer census §6.6 confirms **no caller reaches it today**.) The parent chooses the child's whole
  command line. **There is no discovery problem for the subagent RpcChannel; there is only a string
  to change.**

### 3.2 Why NOT a handshake on the same fd

Two measured blockers, and they are decisive:

1. **The command envelope is closed.** `parse_rpc_command` ends in `cls(**kwargs)`
   (`rpc_types.py:423`), so a dataclass `TypeError` rejects **any** unknown key. The wire recon
   MEASURED it end-to-end: `{"id":"a4","type":"prompt","message":"m","bogus_field":1}` →
   `"Failed to parse command: RpcCommandPrompt.__init__() got an unexpected keyword argument
   'bogus_field'"`. **A client cannot probe by sending an optional field.** So a handshake must be a
   new `type` discriminator.
2. **A new discriminator breaks the parity pin.**
   `tests/pi_parity/test_phase_4_4_strict_superset.py:52-62` asserts
   `set(fixture["rpc_command_types"]) == RPC_COMMAND_TYPES`, and `:64-92` asserts
   `len(RPC_COMMAND_TYPES) == 29` / `len(SUPPORTED_COMMANDS) == 29` / `len(DEFERRED_COMMANDS) == 0`.
   (MEASURED via the consumer census §6.2, re-verified against `rpc_mode.py:194` `DEFERRED_COMMANDS = {}`
   and `rpc_types.py:814` `RPC_COMMAND_TYPES`.) Adding a 30th discriminator to the pi dialect **is
   exactly the parity break Option C exists to avoid.** You would have to special-case `initialize`
   *before* `parse_rpc_command` — which is content sniffing wearing a handshake's clothes.

3. **Third blocker, and it is the subtle one: the first byte is not a command.** There is no server
   greeting (wire recon §5, MEASURED: no `_output` site is a header). `harness.subscribe` is
   installed at `rpc_mode.py:1974`, and the stdin pump task is created at `:2092` — **after**. So the
   server can legally emit an event *before* the first command line arrives. A negotiated dialect
   means either buffering all events until negotiation completes, or emitting pi-dialect events
   before the handshake and standard ones after — a **mid-stream dialect switch**, strictly worse
   than two modes.

### 3.3 Why NOT content sniffing

Sniffing on `"jsonrpc": "2.0"` is technically unambiguous — the string appears nowhere in
`packages/` today (`grep -rn "jsonrpc" --include=*.py packages/` → **0 hits**, MEASURED by the
standards recon §1, re-run here: still 0). But:

- It inherits §3.2's blocker 3 verbatim: events may precede the first command, so there is an
  unavoidable "what dialect are the events until we know?" hole.
- It makes the wire a **permanent union type**. You can never remove the old grammar because any
  client may still send it — and you cannot even *measure* who does, because sniffing is silent by
  design. Sniffing is the one selection mechanism that guarantees §6's "what kills the old wire"
  answer is **nothing, ever**.

### 3.4 Why a distinct `--mode` value rather than `--mode rpc --dialect=std`

- **Measured:** mode is already the routing axis. `entry.py:112`, `:119`, `:125-126` route on it, and
  `entry.py:1313-1316` carries a `@file`-incompatibility guard keyed on the mode string. A sub-flag
  creates a **second** axis that every existing mode-keyed guard silently ignores.
- **Design (INFERRED, but load-bearing):** a distinct mode value is visible in `ps`, in the rendered
  child argv the consent dialog shows the human, and in the P3 registry/reaper rows. Kickoff §5.1 is
  emphatic that everything reaching a consent dialog is security-relevant; an audit surface that says
  `--mode acp` is strictly better than one that says `--mode rpc` and hides the dialect three flags
  later.

**Selection verdict: a new `--mode` value. Named for what it is, not for its novelty** — `--mode acp`
if the dialect is ACP, which per §7.7 is the only version worth the tax.

---

## 4. The cost of two wires existing forever — the brutal section

Each tax below has a measured anchor. I am not softening any of them.

### 4.1 Every new command becomes a 2× decision, forever

Adding a command today is: dataclass in `rpc_types.py`, entry in `_RPC_COMMAND_REGISTRY`
(`rpc_types.py:344-374`), a handler, an entry in `SUPPORTED_COMMANDS` (`rpc_mode.py:206-252`), and a
pin update. Under Option C it is all of that **plus** "does the standard dialect expose it?", plus a
mapping, plus a second test. And the pin at `test_phase_4_4_strict_superset.py:64-92` asserts exact
counts, so the question **cannot be forgotten** — it will be asked every single time, forever. That
is the good half and the bad half of the same fact.

### 4.2 The pi wire becomes a museum with a maintenance contract

MEASURED (consumer census §6.1-6.2): `tests/rpc` = **190** collected tests, `tests/pi_parity` = **155**
across 6 rpc-touching files. MEASURED (§6.6): the **only prospective production consumer of the rpc
wire is the `RpcChannel` this sprint has not written yet.**

Under Option C, `RpcChannel` speaks the *other* dialect (§5). So after this sprint the pi wire has
**345 tests and zero production consumers**, permanently. Every one of those tests must keep passing,
every refactor must keep them green, and none of them protects a user.

### 4.3 Bug-fix asymmetry — the corrosive one

Fixes *below* the dialect seam (`_handle_prompt`, `harness/core.py`) reach both dialects. Fixes
*above* it must be made twice — and for the pi dialect, **some of them may not be made at all**,
because the fixtures pin the bytes. Concretely, these MEASURED defects would be knowingly frozen:

| Defect | Site | Why the freeze keeps it |
|---|---|---|
| Non-string `id` echoed verbatim as a non-string | `rpc_mode.py:1766` guards only the parse path; the success/error echo does not | fixing it changes bytes |
| Parse errors jump the response queue (written synchronously at `:2030`/`:2038` while responses come from tasks at `:2051-2062`) | structural | fixing it changes ordering |
| `prompt` silently drops `cmd.images` while `steer`/`follow_up` decode them | `rpc_mode.py:309` vs `:979`, `:991` | fixing it changes behaviour |
| Busy rejection acked as `success: true` | `rpc_mode.py:322-330` | fixing it changes bytes |
| Unknown-command error does not echo `id` (fixed upstream in pi v0.80.2) | `rpc_mode.py:1771-1776` | adopting the fix changes bytes |

**You would be maintaining a wire you have documented as defective, on purpose, indefinitely.** That
is a genuinely corrosive engineering state and it is the tax nobody budgets for.

### 4.4 Shared process-global state that neither dialect owns

MEASURED: `contextlib.redirect_stdout(sys.stderr)` is constructed unconditionally at
`rpc_mode.py:1846-1849` and entered at `:2096`, **even when `stdout_write` is injected** — which is
precisely why `aelix-server` must be single-flight (`rpc_ws.py:65-71`, `close(code=1013)` *before*
`accept()`). Two dialects share this one global. Any dialect-specific stdout discipline (ACP and MCP
both normatively forbid non-protocol bytes on stdout) is enforced in a place neither dialect owns, so
a change made for one silently affects the other.

### 4.5 Documentation debt, starting from a measured zero

MEASURED (consumer census §4): **there is no RPC protocol reference document in this repo at all.**
`docs/guides/` has 5 files, none about rpc; `docs/contracts/` has 4 schemas, none of them the wire.
The strongest public promise anywhere is `docs/guides/getting-started.md:74` — *"JSONL
command/response protocol on stdio"*.

Today aelix documents **zero** wires. Option C requires documenting **two**, plus a "which one do you
want?" decision page. INFERRED, but the base rate is not encouraging: this repo has shipped 199 ADRs
and zero protocol references. The realistic outcome is that the new dialect gets documented and the
pi dialect stays tribal knowledge — which is fine right up until someone has to fix it.

### 4.6 What would actually force the old one to die?

"We'll delete it later" is a lie unless something makes keeping it impossible. The measured
candidates, honestly ranked:

1. **The pi-parity regime being retired.** The *only* thing holding the pi dialect in place is
   `tests/pi_parity/*` + `tests/pi_parity/fixtures/pi_rpc_mode_734e08e.json` (consumer census §6.2 —
   these are "the ONLY artefacts in the entire repository that assert pi compatibility of the wire").
   That is self-imposed and revisable by ADR. The most likely external trigger is **pi upstream
   adopting ACP natively** — and the standards recon §8.3 MEASURED that pi's position is
   **unrecorded** (discussion #4444 and issue #175 exist; no maintainer accept/reject/defer). So
   Option C's "the old wire will die eventually" is a **bet on a decision nobody has made, in a repo
   aelix does not control.**
2. **A release closes the window permanently.** MEASURED: `git tag -l` → 0 tags,
   `git ls-remote --tags origin` → empty, `gh release list` → empty (consumer census §0). Deleting
   the pi wire costs **zero** external breakage today. But `README.md:50-52` and
   `README.ko.md:52-54` already advertise `--mode rpc` to users, and the beta-release track is the
   active near-term goal. **The day v0.1.0 ships, `--mode rpc` becomes a public promise and Option C
   is permanent by construction.** The deletion window is measured in weeks, not sprints.
3. **A machine-enforced sunset, landed in the same PR.** The only mechanism actually under the
   owner's control: a test that goes red on a date, or on a condition (e.g. "fails if the pi dialect
   still has zero production consumers after `<date>`"). The repo has demonstrated it can hold
   machine gates — the strict-superset pin drained `DEFERRED_COMMANDS` from 7 to 0 across sprints
   6h₂→6h₄c (`rpc_mode.py:194`, docstring at `:1583-1595`), and the band gate holds today. But it has
   also demonstrated the opposite on prose alone: **ADR-0058's carry-forward has said "Sprint 6f
   wires that bridge" for six sprints and the comment is still sitting at `rpc_mode.py:316-320`**
   (MEASURED — re-opened, still there at `da61337`).

**Honest answer: nothing that exists today will kill the old wire. Option C is defensible only if
the sunset gate lands in the same PR as the second dialect — and the owner must accept that shipping
a release before the sunset date makes the gate unenforceable.**

---

## 5. The three scenarios

For each: what happens today on the pi wire (MEASURED), what the standard dialect does, and which
dialect `RpcChannel` speaks.

### 5.1 Failed prompt

**Today (pi dialect):** `_handle_prompt` writes `{"type":"response","command":"prompt",
"success":true,"id":"req_1"}` at `rpc_mode.py:330`, **before** `harness.prompt` at `:309` is
awaited. The detached task later raises, prints `[rpc] prompt task failed: {exc!r}` to stderr
(`:311-315`), and emits **nothing on the wire** (`:316-320` is the comment promising a bridge that
never landed). No terminator. `RpcClient.prompt_and_wait` resolves only on `agent_end`
(`rpc_client.py:441`, MEASURED via the wire recon §8.1) and blocks the full
`DEFAULT_WAIT_FOR_IDLE_MS = 60_000` (`rpc_client.py:87`).

**Standard dialect:** `session/prompt` is a **request**, so its response IS the terminator. A failure
returns `{"jsonrpc":"2.0","id":1,"error":{"code":-32000,...}}` or a `result` carrying a stop reason.
ACP's `StopReason` set — `end_turn | max_tokens | max_turn_requests | refusal | cancelled` — is the
externally-validated taxonomy the standards recon §7 recommends adopting regardless.

**The honest wrinkle:** the failure is caught *inside* `_run()`'s `except` at `rpc_mode.py:310`,
which the dialect never sees. **A codec adapter cannot fix this.** The standard dialect requires a
preflight seam on `_handle_prompt` (§2), or `dialect.bind(emit)` plus explicit plumbing of the
originating id into `_run()`. Either way the fix is in the handler, below the seam.

**RpcChannel speaks: standard.** A 60 s dead wait per failed delegation is unaffordable when P3's
parallel mode can have several children in flight against a per-prompt budget.

### 5.2 Aborted turn

**Today (pi dialect):** `_handle_abort` (`rpc_mode.py:333-340`) awaits `harness.abort()` and returns
`RpcSuccessResponse(command="abort")`. Per the pi recon §0.1 (MEASURED there against
`harness/core.py:4287-4295`), the harness swallows the `CancelledError` and returns `[]` *before*
the already-ported four-event closure block at `:4298-4316` can run — so **no `agent_end`**. Another
60 s dead wait. The pi recon's headline stands: this is an **aelix regression**, not inherited; pi
routes abort *through* `emitRunFailure` and always emits `agent_end` with `stopReason:"aborted"`.

**Standard dialect:** `session/cancel` is a notification and the *original* `session/prompt` request
MUST be answered with `stopReason: "cancelled"` — normative in ACP, including the deadlock rule for
a pending permission request when a turn is cancelled.

**The honest wrinkle, again:** the missing terminator is a **kernel early-return**, not a
serialization choice. Fixing `harness/core.py` fixes both dialects; not fixing it leaves both broken
no matter how standard the envelope is.

**RpcChannel speaks: standard**, and `RpcChannel.stop()` maps to `session/cancel`. But it must keep
its own deadline regardless, because the load-bearing change is out of the dialect's hands.

### 5.3 Busy rejection

**Today (pi dialect):** the ack at `:330` precedes the await at `:309`, so a prompt rejected because
the harness is streaming returns `success: true` and then nothing. **pi returns `success:false`
here and documents it** (`rpc.md:65`, `:76`, MEASURED by the pi recon §4b). So the pi dialect is
**already divergent from pi on this exact path** — see §7.3.

**Standard dialect:** a typed error in JSON-RPC's implementation-defined band (`-32000…-32099`,
standards recon §2), machine-distinguishable from "the turn ran and failed".

**Why this scenario earns the dialect the most:** P3's governance charges the delegation budget
**per child** (kickoff §5.4). "Rejected before acceptance" and "accepted, then failed" must be
distinguishable to charge correctly, and today they are literally the same bytes.

**RpcChannel speaks: standard.**

### 5.4 Why RpcChannel speaks the standard dialect — and what that costs the parity test

**Why standard, stated plainly:** `agents/resolver.py:201` is the only line that changes, the parent
owns the child's whole argv, there is no legacy client, and — decisively — the consumer census §6.6
MEASURED that `RpcChannel` is the wire's *only* prospective production consumer. **If `RpcChannel`
spoke pi, Option C would have produced two wires, neither of which is load-bearing for anything but
tests.**

**What it costs — and this is the concrete damage to THIS sprint's deliverable.** The kickoff
handoff's good news is MEASURED and real: both channels serialize through the same function —
`modes/print_mode.py:59-68` imports `rpc_mode._dataclass_to_dict` explicitly *"so the wire shape
stays consistent across `rpc` and `json` modes"*. That is why `aelix_agents/stream.py:406`
`reduce_line` works unmodified on rpc output.

**A genuinely standard second dialect destroys that gift.** `reduce_line` reads `event.get("type")`
and snake_case kernel fields (`stream.py:406-436`); JSON-RPC/ACP wraps everything in
`{"jsonrpc","method","params"}` and camelCases it. Two outcomes, and the sprint must pick one:

- **(i) A second reducer.** The parity test then proves two *reducers* agree — a stronger test, and
  materially more work.
- **(ii) A standard envelope over verbatim aelix payloads:**
  `{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":…,"update":<the exact snake_case
  event dict>}}`. `reduce_line` needs one unwrap line and nothing else.

**(ii) is obviously right, and it is also an admission:** the "standard" dialect is then standard in
its **envelope only**, and its payloads stay aelix-private. That is exactly the D6 half-fix the
standards recon §2 called out — JSON-RPC unifies the outer envelope; `params` spelling stays your
project's convention. **Option C buys the envelope, not the vocabulary.**

---

## 6. What happens to the sprint deliverable

**It changes shape and partially slips. Stated without hedging.**

| Deliverable | Under full Option C |
|---|---|
| Long-lived `RpcChannel` | **Ships, but against a wire that must be specified first.** The adapter is small (~20 lines in `rpc_mode.py` + a dialect module + one token in `resolver.py:201`); the *specification* of the second dialect is the real work. |
| Cross-channel parity test | **Changes shape.** No longer "the same events through two transports" but "two serializations reduced to the same `SubagentResult`". Stronger and more expensive. Under §5.4(ii) it stays tractable; under (i) it grows a second reducer. |
| Terminator / early-ack fixes | **Unchanged and still blocking.** Option C substitutes for none of them. |
| Real-child test bed | **Unchanged and still missing.** Kickoff §3 MEASURED that `tests/rpc/*` stubs `_build_argv` and never spawns a real child; §9.1 of the wire recon MEASURED that `aelix --mode rpc` **raises before emitting a byte** (`entry.py:2160-2166` passes `repo`/`fs` alongside an explicit `runtime_host`, rejected at `rpc_mode.py:1906-1914`). That must be fixed for *either* dialect. |

**Scope estimate (INFERRED from the sites above, not from a written patch):** ~6-9 files touched —
`rpc/rpc_mode.py`, new `rpc/dialect.py`, new `rpc/dialect_std.py`, `cli/args.py`, `cli/entry.py`,
`agents/resolver.py`, new `aelix_agents/rpc_channel.py`, plus the parity test and a `PiDialect`
identity test. Two-to-three sprints if the second dialect is real ACP (the standards recon lists 7
unmeasured ACP items, including `agent-client-protocol` 0.11.1's transitive deps and license, which
this repo's SBOM/OSS-compliance gates require **before** any dependency proposal). **One sprint only
if the second dialect is deliberately minimal — and §7.7 explains why minimal is the worst of all
worlds.**

---

## 7. Why this is the wrong choice

The strongest case I can build against my own option. I believe most of it.

### 7.1 It pays a permanent structural cost to solve problems that are not on the wire

MEASURED, from the standards recon's defect matrix: of the seven defects, the two that **block this
sprint** are D1 (no terminator) and D2 (early ack). Both are fixed at `rpc_mode.py:308-330`,
`:333-340`, and `harness/core.py:4287-4298` — **all below any dialect seam**. A second dialect fixes
D4 (error codes), D5 (handshake), D6 (spelling) and D7 (bidirectionality). **Not one of those four
blocks the `RpcChannel`.** Option C adds a forever-cost to obtain features the immediate deliverable
does not need, while the blocking work is untouched.

### 7.2 The premise "something already depends on the pi wire" is measurably false

The consumer census §0 measured every clause: **0 git tags, 0 releases, empty `ls-remote --tags`**;
`aelix-web` does not exist on disk or on GitHub and its framework is undecided
(`docs/decisions/0097-multi-frontend-architecture.md:100-101`); `aelix-server` is `rm -f`'d from the
publish set **by its own release workflow** (`.github/workflows/release.yml:82-87`) and is
byte-transparent anyway (a wire change costs it **0 source lines**); the TUI never touches rpc (3
prose comments); no extension touches rpc; `aelix_agents` uses `--mode json`.

**The entire installed base of the pi wire is 345 in-repo tests that this repo wrote and can
amend.** Option C's whole justification is backward compatibility for a population of zero — and it
spends the single cheapest moment the wire will ever be changeable (before `RpcChannel` exists,
before the first tag) building a compatibility layer instead of changing the wire.

### 7.3 Freezing the pi dialect freezes a wire that is not actually at parity

MEASURED divergences that Option C would preserve **in the name of parity**: busy rejection acked as
success where pi documents `success:false` (`rpc.md:65`/`:76` vs `rpc_mode.py:322-330`); `prompt`
dropping `cmd.images` where pi forwards them (`rpc_mode.py:309` vs `rpc-mode.ts:384-385`);
unknown-command errors not echoing `id` (pi fixed this in v0.80.2); non-string `id` echoed verbatim;
parse errors jumping the response queue.

The pi-parity pin does not measure **any** of these — it measures the command roster
(`test_phase_4_4_strict_superset.py:52-92`) and the `RpcSessionState` field set (`:246-263`).
**So the freeze buys fixture stability, not parity.** "Keep pi's wire exactly as-is for parity" is,
measured, "keep five known defects exactly as-is so a fixture keeps matching."

### 7.4 The wire being frozen has never successfully run as a stdio server

MEASURED, wire recon §9.1, reproduced there at `da61337`: `aelix --mode rpc` raises
`RuntimeError: repo and fs must not be supplied when runtime_host is explicit` before emitting a
byte — `entry.py:2160-2166` passes exactly the combination `rpc_mode.py:1906-1914` rejects. Zero
bytes reach stdout. The only live consumer of the protocol is `aelix-server`'s `WS /rpc`, which is
unpublished.

**Preserving byte-compatibility with a code path that raises on line one is not a compatibility
argument.** It is an argument about test fixtures, and it should be stated that way.

### 7.5 The deletion window is measured in weeks and then the door locks

§4.6: nothing under aelix's control forces the pi dialect to die. The one likely external trigger
(pi adopting ACP) is **unrecorded upstream**. Meanwhile `README.md:50-52` already advertises
`--mode rpc` and the beta-release track is active. **After the first tag, Option C is permanent by
construction and the sunset gate becomes unenforceable.** Every "we'll delete the old one later" in
this repo's history has a comparable record: `rpc_mode.py:316-320` still says *"Sprint 6f wires that
bridge"* six sprints later.

### 7.6 "One dispatcher, two grammars" is true for 28 commands and false for the one that matters

§2 measured it: `prompt` is the command whose *semantics* differ between grammars — early-ack in pi,
reply-at-turn-end in the standard dialect. `_handle_prompt` (`rpc_mode.py:288-330`) must either fork
or grow a preflight seam. **The very first command `RpcChannel` uses is the one that breaks the
clean-layering story**, and the clean-layering story is Option C's entire structural argument.

### 7.7 There is a cheaper structure that gets the same interop, and it is measured precedent

The standards recon §3.1 MEASURED it: **`pi-acp` already exists** — a third-party adapter that speaks
ACP outward and **spawns `pi --mode rpc`** inward, on aelix's own upstream. Applied here, the standard
surface lives **outside the aelix process** (a separate binary, or an extension), touching **zero**
product-core lines and imposing **zero** permanent tax on `rpc_mode.py`.

Option C moves that adapter *inside* the process. What does that buy? One subprocess hop — and
`RpcChannel` is already a subprocess boundary, so the hop is free either way. **Option C is `pi-acp`
with the same functionality, a permanent product-core seam, and a second wire to maintain forever.**

And the fallback ("ship a minimal JSON-RPC-shaped dialect now, complete ACP later", §6) is the worst
version: you have then invented a **third** protocol — not pi's, not ACP's — and paid the dual-stack
tax for **zero** interop dividend until a later sprint that may never come.

### 7.8 What survives the attack

Two things, honestly:

- **The seam itself is good and is worth landing regardless.** A `dialect:` parameter whose default
  is a provable no-op turns "we did not change the pi wire" from a promise into a test. That is
  valuable under *every* option, including "replace the wire" — because it is exactly the thing that
  makes a replacement testable side-by-side.
- **Option C decouples the schedule.** The sprint can ship the terminator fixes and `RpcChannel` on
  the pi wire now, and a standard dialect can land later behind the same seam without a second
  migration.

Both arguments support landing **the seam**. Neither supports landing **the second wire** this
sprint.

---

## 8. The variant I would actually defend — "C-minimal"

**Land the seam. Do not land the second dialect.**

1. Add `dialect: RpcDialect | None = None` to `run_rpc_mode` (`rpc_mode.py:1787-1798`) and the
   four/five call-site edits of §1.4. Default `PiDialect`, methods that are identity /
   `resp.to_json()` / `_event_to_dict(event)`.
2. Prove the no-op two ways: a golden byte comparison of `--mode rpc` output before/after, and an
   identity assertion on `PiDialect`.
3. **Fix the semantics below the seam** — the preflight seam on `_handle_prompt`, the correlated
   prompt error response (pi parity restoration, `rpc-mode.ts:395-399`), and the kernel terminator
   (`harness/core.py:4287-4298`) if the owner permits a kernel edit. These are what the sprint
   actually owes.
4. Ship `RpcChannel` on the **pi dialect**, because after step 3 the pi dialect terminates correctly
   and `stream.reduce_line` still works unmodified (`print_mode.py:59-68`). The parity test keeps its
   measured feasibility and does not grow a second reducer.
5. Fix `entry.py:2160-2166` so `--mode rpc` starts at all, and build the real-child bed.
6. **Add a gate: a test asserting exactly one dialect is registered.** It goes red the day someone
   adds a second without an ADR. That is the sunset mechanism from §4.6(3), installed *before* it is
   needed rather than promised.

`RpcChannel` speaks the pi dialect in C-minimal — the inversion of §5, and it is honest: once the
terminator and the early ack are fixed, **every reason `RpcChannel` needed the standard dialect
evaporates except typed busy-rejection**, and that can be a `data` field on the existing error
envelope (ADR-0035 is the direct precedent for an additive divergence that keeps pi-shaped consumers
working).

**Cost:** ~20 lines of product-core, one new small module, one new gate. **Buys:** a proven-inert
seam, a real option on ACP later, and the sprint's actual deliverable on time.
**Gives up:** the standard wire this sprint — which §7.2 argues is cheaper to build *after* the
consumer exists, not before, because the consumer is the thing that tells you which of the 29
commands the standard surface actually needs.

---

## 9. Uncertainties — what I did not measure

1. **I did not write or run the seam patch.** The "~20 lines / 5 call sites" figure is counted from
   the four `_output` sites plus `:2028`, not from a diff. INFERRED.
2. **I did not measure how much of `rpc_mode.py`'s 2142 lines is handler surface vs lifecycle**, so
   "the handlers do not fork" is verified by *signature shape* (`build_dispatch_table:1572-1633` and
   `_handle_command:1747`), not by reading all 29 handler bodies. I read `_handle_prompt`,
   `_handle_abort`, and the three adapter factories in full.
3. **I did not verify that `PiDialect` is byte-inert under a golden test**, because I wrote no code.
   The claim is that it is *provable*, not that it is proven.
4. **ACP's framing is not spec-page-verified** (standards recon §8.1 flags this). Everything in §5
   that cites ACP inherits that caveat.
5. **The `stream.reduce_line` breakage under a standard dialect (§5.4) is INFERRED** from reading
   `stream.py:406-436` and the JSON-RPC envelope shape; I did not construct a wrapped record and feed
   it through the reducer.
6. **I did not assess whether `run_rpc_mode` growing a `dialect:` parameter breaks any of the 190
   `tests/rpc` tests** — they call it with keyword arguments and a defaulted parameter is additive,
   but I did not run them.
7. **The band-gate verdict (§1.5) is from reading the gate's four product-core tests**, not from
   running the gate against a hypothetical `rpc/dialect.py`.
