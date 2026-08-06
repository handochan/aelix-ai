# pi's RPC protocol — what it actually is, and how much parity obligation exists

**Written:** 2026-07-29. Repo at `main` = `da61337`.
**Companion notes:** `rpc-sprint-kickoff-handoff.md`, `rpc-sprint-recon-transport.md`,
`rpc-sprint-recon-envelope.md` (all in this directory).

Everything labelled MEASURED was fetched or opened during this recon. pi sources were fetched raw
from GitHub into a scratchpad and read directly; aelix citations were opened against `da61337`.

---

## 0. THE HEADLINE — read this before planning anything

**Both "missing terminator" defects are AELIX REGRESSIONS, not inherited from pi. aelix already
contains the port of pi's failure-closure block; two code paths bypass it. And the in-code
justification for the planned fix describes a pi behaviour that does not exist, which has sent the
work item to the wrong layer for six sprints.**

### 0.1 pi DOES terminate aborted and failed runs — aelix does not

MEASURED, `packages/agent/src/harness/agent-harness.ts` at the pin. pi wraps the whole agent loop in
an **unqualified** `catch (error)` (`:568`) and routes every failure — abort included — into
`emitRunFailure(model, error, abortController.signal.aborted, signal)` (`:570-575`). That method
emits a **complete four-event terminal sequence** (`:512-524`):

```
message_start → message_end → turn_end → agent_end   // agent_end at :522
```

with a synthetic failure message whose `stopReason` is `"aborted"` when the abort signal fired, else
`"error"` (`:49-56`). So in pi, **`agent_end` always arrives** — on success, on error, and on abort.
pi's client contract (`agent_end` is the sole terminator, 60 s timeout, `rpc-client.ts:401-438`) is
therefore *sound*, because the server guarantees the terminator.

MEASURED, aelix `harness/core.py:4298-4316`: **this exact block is already ported**, same four
events, and its own comment says *"Pi parity: synthesize failure assistant message + emit closure
events."* Two things stop it firing:

1. **`except asyncio.CancelledError: … return []` at `:4287-4295` sits *inside* the same `try` and
   returns before the closure block can ever be reached.** Abort therefore emits nothing. pi passes
   `aborted` *into* `emitRunFailure` precisely so that this path still terminates.
2. **The closure block's filter is narrowed to `except AgentHarnessError` (`:4298`)** where pi's is a
   bare `catch (error)`. Any other exception escapes without a terminator.

So the accurate defect statement is **not** "aelix never emits a terminator on failure". It is:

| Path | pi | aelix |
|---|---|---|
| success | `agent_end` | `agent_end` (`loop.py:221,260,271`) |
| `AgentHarnessError` | `agent_end`, `stopReason:"error"` | **`agent_end` — parity already holds** (`core.py:4306-4310`) |
| any other exception | `agent_end`, `stopReason:"error"` | **nothing** — filter too narrow |
| abort | `agent_end`, `stopReason:"aborted"` | **nothing** — early `return []` short-circuits |

Also divergent: aelix hardcodes `stop_reason="error"` (`core.py:4302`) and has no `aborted`
parameter, so even once the path is reached it cannot reproduce pi's `"aborted"` stop reason.

**Implication for the sprint's central fork.** ADR-0058's carry-forward says the fix waits on "a
public event-emit method to feed an `agent_end` event from outside". MEASURED: **no such API is
needed.** The harness already emits the sequence internally; the fix is to widen one `except` and
stop the abort path from returning early. That is a **kernel** edit (`harness/core.py`) — which the
kickoff handoff §3 declares forbidden — but it is a **parity restoration to behaviour the file
already claims to implement**, not new behaviour and not a divergence. The kickoff handoff's premise
that "the harness half is KERNEL and forbidden, so a server-side synthetic event in `rpc_mode` is the
only in-band fix" leads to the *wrong* layer: a synthetic event bolted into `rpc_mode.py` would
diverge from pi, while the kernel fix converges with it.

### 0.2 The "synthetic terminal event" citation is false

`rpc_mode.py:316-320` (current tree) reads:

```
# NOTE: Pi parity (`rpc-mode.ts:379-401`) also emits a synthetic
# terminal event so the client's `wait_for_idle` listener
# unblocks. The Aelix harness does not yet expose a public
# event-emit method to feed an `agent_end` event from outside;
# Sprint 6f wires that bridge per ADR-0058 carry-forward.
```

MEASURED: `rpc-mode.ts:379-401` at the pinned SHA **is** the `prompt` handler, and what it emits on
failure is `output(error(id, "prompt", e.message))` — a **correlated error RESPONSE on the
command/response channel**. It is not an event, synthetic or otherwise.
`grep -n "agent_end\|agentEnd\|synthetic" pi-rpc-mode.ts` returns **zero hits at both v0.74.1 and
v0.80.2**. pi's rpc server never synthesises a terminal event, on any path.

Consequences the owner must be told explicitly:

1. The ADR-0058 W4-m2 / P-119 carry-forward ("synthetic terminal event emission deferred to Sprint 6f
   when harness exposes event bus", `0058-phase-4-4-strict-superset-closure.md:66` and `:173-175`)
   has been chasing a phantom for six sprints. It is **not** a parity gap. Implementing it **is** a
   divergence from pi.
2. The **real, cheap, genuinely-owed** parity fix is the error response — no harness event bus, no
   kernel edit, no new event type. It is a ~6-line change in `rpc_mode.py` and it *closes* a
   divergence rather than opening one.
3. The docstring 20 lines above it (`rpc_mode.py:293-299`) is **correct** and already says so:
   *"Pi fires the prompt asynchronously and emits the response only after `preflightResult(true)`.
   Aelix has no preflight callback yet."* Two adjacent comments disagree; the wrong one is the one
   that propagated into ADR-0058 and into the sprint spec.

---

## 1. What pi's rpc-mode.ts concretely is

MEASURED. Fetched `packages/coding-agent/src/modes/rpc/{rpc-mode,rpc-types,rpc-client,jsonl}.ts` at
both `734e08edf82ff315bc3d96472a6ebfa69a1d8016` and tag `v0.80.2`.

**Pin sanity check (MEASURED):** ADR-0034 pins `734e08e…` and calls it "`main` HEAD as of
2026-05-17". The `v0.74.1` *tag* actually points at `2c708492e30a3e9577075f2145d411cd95713033`
(2026-05-16T23:32:48Z); `734e08e` is a commit ~1 h later ("chore: approve contributor mattiacerutti",
2026-05-17T00:31:05Z). **`rpc-mode.ts` is byte-identical at both** (`diff -q` clean, 754 lines), so
the pin is sound for this surface. The task brief's shorthand "v0.74.1 @ 734e08e" is loose but
harmless here.

**Stale line numbers in-tree (MEASURED, INFERRED cause):** `rpc_mode.py:1` says pi's `rpc-mode.ts` is
"492 LOC" and `:116` says the 9 case sites "live at lines 483-547". The file at the pin is **754
lines** with the `prompt` case at **379**. INFERRED: the aelix port's file-header citations were
written against a pre-pin pi and never re-anchored; individual handler citations were later corrected
piecemeal (the `:379-401` citation *is* correct for the pin). Treat any `rpc-mode.ts:NNN` citation in
`rpc_mode.py`'s module docstring as unverified.

### Shape

`pi --mode rpc` (`rpc.md:9-11`). One process, JSON over stdin/stdout, no HTTP, no framing header.

**Framing (MEASURED, `jsonl.ts:4-20` and `:21-58`):** strict JSONL, **LF-only**. pi documents why:
Node `readline` also splits on U+2028/U+2029, which are legal inside JSON strings, so readline is
declared *not protocol-compliant* (`rpc.md:30-37`). Reader strips one trailing `\r`. `jsonl.ts` is
**byte-identical between v0.74.1 and v0.80.2**.

**Three message families share one stdout fd** (`rpc-mode.ts:53-55`, all via `output()`):

| Family | Shape | Has `id`? |
|---|---|---|
| Command response | `{id?, type:"response", command, success:true, data?}` | yes, echoed |
| Command error | `{id?, type:"response", command, success:false, error:<string>}` | yes, echoed |
| Agent event | bare `AgentEvent` — `{type:"agent_start"}`, `{type:"agent_end", messages:[…]}`, … | **NO** |
| Extension UI request | `{type:"extension_ui_request", id, method, …}` | yes, but a *separate* uuid namespace |

stdin carries the 29-variant `RpcCommand` union plus `extension_ui_response`.

**id semantics (MEASURED):** `id` is **optional on every command** (`rpc-types.ts:19-69`, every
variant is `{ id?: string; … }`). If present it is echoed on the response. pi's own client always
sends one, generated as a monotonic `req_${++this.requestId}` (`rpc-client.ts:479`), with a 30 s
per-request future (`rpc-client.ts:485-488`). **Events carry no id, no turn number, no sequence** —
and `rpc.md:746` states this as an explicit design decision, verbatim:

> Events are streamed to stdout as JSON lines during agent operation. **Events do NOT include an `id`
> field (only responses do).**

**Error shape (MEASURED, `rpc-types.ts:206`, `rpc-mode.ts:68-70`, `rpc.md:1181-1203`):**

```json
{"type":"response","command":"set_model","success":false,"error":"Model not found: invalid/model"}
```

`error` is a **free-text string**. There is no code, no category, no structured payload. Parse
failures use the literal sentinel `command: "parse"` (`rpc.md:1194-1203`).

**No version field anywhere.** MEASURED: `grep -ni "version|stable|breaking|deprecat|backward|compat"`
over the entire 1412-line `rpc.md` returns **0 hits**. No protocol version on the wire, no
negotiation, no schema file.

**stdout hijack:** `takeOverStdout()` is the first statement of `runRpcMode` (`rpc-mode.ts:49`), so a
stray `console.log` from an extension cannot corrupt the record stream.

**Completion signal:** `agent_end` and nothing else. pi's own client's `waitForIdle()` and
`collectEvents()` resolve **only** on `event.type === "agent_end"`, each with a 60 s reject timeout
(`rpc-client.ts:401-416`, `:421-438`). `promptAndWait` is `collectEvents(timeout)` + `prompt()`
(`rpc-client.ts:443-447`).

---

## 2. Is the wire a PUBLIC contract, or pi's internal frontend detail?

**PUBLIC and documented — considerably more so than the repo's notes assume. But unversioned.**

MEASURED evidence *for* public:

- **`packages/coding-agent/docs/rpc.md` — 1412 lines / 35,961 bytes**, a full protocol reference:
  every command with request+response JSON, every event type in a table, the extension-UI
  sub-protocol, an Error Handling section, a Types section, **and two worked client examples — one in
  Python (`rpc.md:1320-1354`) and one in Node.js (`rpc.md:1356+`)**.
- The doc is **shipped in the published npm package**: `packages/coding-agent/package.json` `"files"`
  includes `"docs"` and `"examples"`.
- The types and the client are **exported from the package entry point**:
  `packages/coding-agent/src/index.ts:315-324` exports `RpcClient`, `runRpcMode`, and the types
  `RpcClientOptions`, `RpcCommand`, `RpcEventListener`, `RpcExtensionUIRequest`,
  `RpcExtensionUIResponse`, `RpcResponse`, `RpcSessionState`.
- Shipped worked examples: `packages/coding-agent/examples/rpc-extension-ui.ts` (pairs with
  `examples/extensions/rpc-demo.ts`), plus `test/rpc-example.ts` referenced at `rpc.md:1358`.
- `rpc.md:3` states the intent: *"useful for embedding the agent in other applications, IDEs, or
  custom UIs."*
- `rpc.md:28-37` writes **client obligations** (split on `\n` only; don't use readline). You only
  write that paragraph for consumers you don't control.

MEASURED evidence *against* a stable contract:

- **Zero** version / stability / deprecation / backward-compat language in 1412 lines (grep above).
- No JSON Schema, no `.d.ts` protocol artifact beyond the ordinary TS types, no protocol version
  field, no capability negotiation.
- `rpc.md:5` actively steers Node/TS consumers **away** from the wire toward in-process
  `AgentSession`, implying the wire is the fallback for other languages rather than the primary API.
- No external-client inventory found (no third-party clients discoverable from the repo).

**Verdict:** treat it as *"a documented public surface at HEAD, with no stability promise."* pi will
tell you what the protocol is; pi has not promised not to change it. For aelix this is the strongest
possible argument for the position ADR-0058 already took (`:196`: *"the durable RPC boundary every
multi-language client routes through"*, `:198-201`: *"a Pi-shaped client … works against Aelix
today"*) — the obligation is real, but it is aelix's own commitment, not one pi enforces.

---

## 3. Has the surface changed v0.74.1 → v0.80.2?

**Almost not at all. It is NOT a moving target.** MEASURED by direct `diff -u` of all four files.

| File | Substantive change |
|---|---|
| `jsonl.ts` | **none** — diff empty |
| `rpc-types.ts` | **one additive optional field**: `bash` gains `excludeFromContext?: boolean`. Everything else is `.js`→`.ts` import-specifier churn. |
| `rpc-mode.ts` | 5 things, listed below |
| `rpc.md` | **11 changed lines total** |

`rpc-mode.ts` substantive deltas:

1. **stdout backpressure** — imports `flushRawStdout` / `waitForRawStdoutBackpressure`; a second
   `session.agent.subscribe` awaits backpressure; `await waitForRawStdoutBackpressure()` after each
   response and each parse error. *No wire-shape change* — a flow-control fix.
2. `shutdown(exitCode, signal?)` — flushes raw stdout on exit **unless** the signal was `SIGTERM`.
3. `bindExtensions({… mode: "rpc" …})` — surfaces `ctx.mode` to extensions.
4. `bash` forwards `excludeFromContext`.
5. **A BUGFIX worth adopting:** unknown-command errors now echo the request id —
   `error(undefined, …)` → `error(id, …)` (v0.74.1 `:657` → v0.80.2 `:670`). At the pin, an unknown
   command's error response was **uncorrelatable**, so a client's request future would hang to its
   30 s timeout.

`rpc.md` deltas: the `--name` / `-n` startup flag; `estimatedTokensAfter` in `CompactionResult`;
`ctx.mode` guidance.

**Conclusion (MEASURED):** across ~6 minor versions and ~2.5 months, pi's rpc wire gained **one
optional command field** and **one id-echo bugfix**. The prompt/abort/event/error semantics, the
framing, the id model and the error shape are all unchanged. Parity here is cheap to hold, and the
upstream-sync epic toward v0.80.2 carries essentially no rpc risk.

---

## 4. Are aelix's defects INHERITED from pi, or aelix-specific? (the important question)

They split. Do **not** treat the four as one bucket.

### 4a. INHERITED, and in one case explicitly deliberate — "fixing" these IS a divergence

| Defect | pi's state | Evidence |
|---|---|---|
| **No event correlation** (no id / turn / sequence on events) | Same, and **documented as intentional** | `rpc.md:746` "Events do NOT include an `id` field (only responses do)"; `rpc-mode.ts:346-348` emits `output(event)` raw |
| **No error codes** | Same — `error` is free text | `rpc-types.ts:206`; `rpc-mode.ts:68-70`; `rpc.md:1181-1192`. aelix matches exactly at `rpc_types.py:495-499` (`error: str`, no code) |
| **No wire version** | Same — none anywhere, doc never mentions versioning | grep over `rpc.md` → 0 hits |
| **`promptAndWait` ignores the error response** (client half only) | **Same.** `promptAndWait` returns `collectEvents()`, which resolves only on `agent_end`; the error response resolves the *send* future, not the *events* promise. Harmless in pi because the server guarantees `agent_end` anyway (§0.1) | `rpc-client.ts:443-447` vs `:421-438` |

**NOT in this bucket:** "no terminator on abort" and "no terminator on failed prompt" were expected
here and are **not** inherited — see §0.1. pi terminates both. aelix's *rpc-layer* abort handler is a
faithful port (`rpc_mode.py:333-340` returns `RpcSuccessResponse(id=cmd.id, command="abort")`,
exactly pi's shape); the divergence is one layer down, in the harness.

### 4b. AELIX-SPECIFIC REGRESSIONS — pi does better, and pi *documents* the behaviour aelix violates

| Defect | pi | aelix |
|---|---|---|
| **No `agent_end` on abort** | `emitRunFailure(…, aborted=true)` → full 4-event closure incl. `agent_end`, `stopReason:"aborted"` (`agent-harness.ts:512-524`, `:568-575`, `:49-56`) | `except asyncio.CancelledError: return []` short-circuits the already-ported closure block (`harness/core.py:4287-4295`) — **KERNEL** |
| **No `agent_end` on a non-`AgentHarnessError` failure** | bare `catch (error)` (`agent-harness.ts:568`) | closure block filtered to `except AgentHarnessError` (`harness/core.py:4298`) — **KERNEL** |
| **Failed prompt emits no correlated failure** (response channel) | `.catch(e => { if (!preflightSucceeded) output(error(id,"prompt",e.message)) })` — a real `success:false` response on the correlated channel (`rpc-mode.ts:395-399` @074, `:406-410` @0802) | `_handle_prompt` prints to stderr and emits **nothing** (`rpc_mode.py:308-320`) |
| **Busy rejection acked as success** | `output(success(id,"prompt"))` fires **only** from `preflightResult(didSucceed=true)` (`rpc-mode.ts:388-393`). `rpc.md:65` — *"If the agent is streaming and no `streamingBehavior` is specified, the command returns an error."* `rpc.md:76` — *"`success: false` means the prompt was rejected before acceptance."* | returns `RpcSuccessResponse` **unconditionally, before `harness.prompt` runs at all** (`rpc_mode.py:322-330`) — a direct contradiction of pi's documented contract |
| **`prompt` drops images** | passes `images: command.images` into `session.prompt` (`rpc-mode.ts:384-385`) | `await harness.prompt(cmd.message, source="rpc")` — `cmd.images` dropped, while `_handle_steer`/`_handle_follow_up` decode them (`rpc_mode.py:309`) |
| **Unknown-command error uncorrelated** | fixed upstream in v0.80.2 (`error(id, …)`) | aelix inherits the *pinned* buggy shape; adopting the fix is free and non-breaking |

Root cause of the first two is one missing seam: **pi's `session.prompt` takes a `preflightResult`
callback; aelix's `harness.prompt` has no equivalent.** `rpc_mode.py:293-299` already says this
plainly and accurately. Everything downstream follows from that one absence.

### 4c. Not comparable

`dropped_lines` / per-line budget / `limit=`: pi's `attachJsonlLineReader` also has an unbounded
buffer and no drop counter (`jsonl.ts:21-41`) — so the *reader* gap is inherited. But `dropped_lines`
is a field on **`SubagentResult`, an aelix-original type** (ADR-0198); pi has no such envelope. This
is an aelix-internal asymmetry between its own two channels, not a pi-parity question at all.

### Bottom line for the owner

- **Fixing (b) is not a divergence — it is closing a parity gap** against behaviour pi both implements
  and documents. Cheapest, highest-value, fully justified under the existing strict-superset regime.
  **Both terminator defects are in this bucket**, and the two most important fixes are in the
  **kernel**, not in `rpc_mode.py`.
- **Fixing (a) IS a divergence.** Event ids, error codes and a wire version put aelix ahead of pi on a
  surface aelix has publicly committed to keeping pi-shaped (ADR-0058:198-201). Each needs the §5
  treatment below and explicit owner sign-off.
- The task brief's framing ("no terminator on abort/failed prompt … are aelix's gaps INHERITED?")
  resolves to: **neither is inherited.** Both are regressions against a pi behaviour aelix already
  partially ported. Fixing them is convergence, not divergence — which inverts the kickoff handoff's
  §3 conclusion that a synthetic `rpc_mode` event is "the only in-band fix".

---

## 5. The repo's established vocabulary and bar for diverging from pi

MEASURED over `docs/decisions/` at `da61337`.

### The bar: a strict-superset closure ADR + a machine closure pin

The pattern, established ADR-0039/0040/0044/0046/0050/0055 and applied at ADR-0058 / 0072 / 0074:

- Every pi surface in scope **either** has an aelix binding **or** sits in a `DEFERRED_*` allowlist
  **with a named owning ADR** (`0058:132-149`).
- A `tests/pi_parity/test_phase_*_strict_superset.py` pin asserts exhaustiveness —
  `SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` and `len(RPC_COMMAND_TYPES) == 29` (`0058:83-112`).
- **Forward-compat clause:** any PR that lands a binding **must drain the allowlist entry in the same
  PR**, mechanically enforced by the pin (`0058:146-149`).
- Pi line citations must be re-verified and corrected in the closure ADR when they drift — this has
  happened repeatedly and is treated as BLOCKING (`0072:62-64` P-258 `528-635`→`483-547`;
  `0074:128` "Line-citation correction"; `0034` Sprint-6h₄a amendment `:563-566`→`:591-594`).

**Current state of that pin for rpc:** `DEFERRED_COMMANDS: dict[str, str] = {}` (`rpc_mode.py:194`) —
the allowlist is fully drained, 29/29 supported. The command *roster* is at complete parity. Every
remaining gap is in **semantics**, which the closure pin does not measure.

### The vocabulary (measured file counts under `docs/decisions/`)

| Term | Files | Means |
|---|---|---|
| `Aelix-additive` | 51 | aelix adds something pi lacks, **without breaking a pi-shaped consumer** |
| `aelix-original` | 14 | a whole feature pi has no analogue for (ADR-0174/0175/0177/0178/0180/0181/0182/0186/0187/0188/0189/0191/0192/0193/0197) |
| `documented divergence` | 8 | behaviour differs, is known, is written down |
| `strict superset` / `strict-superset` | 28 / 24 | the governing invariant |
| `closed-with-documented-divergence` | ADR-0072 P-261 | a BLOCKING finding resolved by documenting rather than matching |

### The canonical precedent for exactly this sprint's question: ADR-0035

pi has 9 `AgentHarnessError` codes; aelix has 10. `0035-error-code-taxonomy.md`:

> **Aelix-additive divergence**: Pi has no `"aborted"` harness code at SHA `734e08e`; Aelix raises it
> from `abort()` so consumers can distinguish cooperative abort from generic error. This is the
> single intentional divergence from Pi's 9-code taxonomy and is documented as additive (**no parity
> violation; consumers `except AgentHarnessError` continue to work**).

**That parenthetical is the bar.** The test is not "does pi do it" but **"does a pi-shaped consumer
still work unchanged?"** ADR-0058 states the same test for the wire specifically (`:198-201`: a
pi-shaped client dispatching on `BashResult`'s key shape *"works against Aelix today"*).

Two other calibration points: `0072:90-91` P-266 (sync/async asymmetry — "documented Pi divergence",
tracked, never fixed) and `0072:84-86` P-259 (`queue_update` payload shape — a pre-existing
divergence carried for multiple sprints because the fix needs a storage refactor). **Divergences are
allowed to persist indefinitely as long as they are named and owned.**

### Applying the bar to this sprint's candidate changes

| Candidate | Class | Passes the ADR-0035 test? |
|---|---|---|
| prompt error response on failure | **parity restoration** | n/a — it *removes* a divergence |
| busy rejection → `success:false` | **parity restoration** | n/a — restores pi's documented contract |
| `_handle_prompt` forwards `cmd.images` | **parity restoration** | n/a |
| unknown-command id echo | **parity restoration** (adopt v0.80.2 fix) | n/a |
| widen `except AgentHarnessError` → bare `except Exception` in the closure block (`core.py:4298`) | **parity restoration**, kernel | n/a — matches pi's bare `catch (error)` |
| stop `except asyncio.CancelledError: return []` bypassing the closure block (`core.py:4287-4295`) | **parity restoration**, kernel | n/a — pi routes abort *through* `emitRunFailure` |
| `stop_reason="aborted"` on the abort closure message (`core.py:4302`) | **parity restoration** | n/a — pi `agent-harness.ts:56` |
| a NEW terminal event type after abort | Aelix-additive — **no longer needed**, superseded by the three rows above | would have passed, but it is now the *worse* option: it diverges where the kernel fix converges |
| event ids / sequence numbers | Aelix-additive | **yes if an added optional field**; contradicts an explicitly documented pi decision (`rpc.md:746`), so it needs the divergence written into the ADR by name |
| error **codes** | Aelix-additive | **yes if an added optional field** alongside the existing free-text `error`; ADR-0035 is the direct precedent |
| wire **version** field | Aelix-additive | **yes if additive and optional**; note spec §9 already asked for it and neither ADR-0198 nor the code delivered — see envelope recon |
| rpc-side per-line budget / `dropped_lines` | not a pi question | aelix-internal channel asymmetry |

---

## 6. What this changes about the sprint plan

1. **The scope fork is not the one the kickoff handoff poses.** §3 there asks "how much *product-core*
   may this sprint touch?" MEASURED: the two highest-value terminator fixes are in **neither** the
   extension nor product-core — they are in the **kernel** (`harness/core.py:4287-4298`). The real
   question for the owner is: *may a sprint edit the kernel to restore a pi behaviour the kernel
   already claims to implement?* Note the band rule's machine gate
   (`tests/agents/test_p2_band_boundaries.py`) is a **text scan for spawn behaviour**, not a
   no-kernel-edits gate, so this is a policy question, not a gate question.
   Fallback if the answer is no: the `rpc_mode.py` synthetic event still works, but it is a
   *divergence* and must be ADR'd as Aelix-additive — strictly worse than the kernel fix.
2. **Re-scope the terminator work item into three, not one.** (a) kernel: widen the `except` and stop
   the abort early-return (parity restoration); (b) `rpc_mode.py`: emit the prompt error response
   (parity restoration); (c) *nothing* — the synthetic event is no longer needed. The kickoff
   handoff's §3 table treats `rpc_mode.py:310-320` and `:333-340` as the same kind of edit; **they are
   not**, and neither is where the fix belongs.
3. **Correct the record.** `rpc_mode.py:316-320`'s pi claim is false, and it was copied into
   ADR-0058 (`:66`, `:173-175`) — including its premise that the fix waits on "a public event-emit
   method", which is not required. The correction belongs in this sprint's closure ADR, matching the
   repo's own precedent for line-citation corrections (ADR-0072 P-258, ADR-0074's correction note).
4. **Adopt the free upstream fix.** v0.80.2's `error(id, …)` for unknown commands is a one-line
   parity improvement with no divergence cost.
5. **Consider moving the pin.** ADR-0034's update policy (`:35-42`) allows a sprint to move the pin
   and requires appending to the Pin history table. The rpc surface delta to v0.80.2 is one optional
   field plus one bugfix — **the cheapest pin move this project will ever get**, and it would let the
   sprint cite live upstream rather than a 2.5-month-old SHA. INFERRED judgement: worth proposing;
   but the pin is global, so moving it re-anchors every *other* surface's citations too, and that
   cost was not measured here.
6. **The command roster is done.** `DEFERRED_COMMANDS == {}`. Do not budget for command parity work;
   budget for semantics.

---

## 7. Uncertainties / not measured

- ~~Whether pi's `session.abort()` emits `agent_end` inside `packages/agent`.~~ **RESOLVED during this
  recon — it does** (`agent-harness.ts:512-524`, `:568-575`). This flipped §4a→§4b for both terminator
  defects and produced §0.1. The note has been updated throughout; earlier drafts of §4 that called
  the abort defect "inherited" were wrong and are corrected.
- `emitRunFailure`'s exact reachability in pi for a *queued/steered* prompt (as opposed to a direct
  turn) was not traced; only the `executeTurn` path was read.
- Whether widening `except AgentHarnessError` → `except Exception` in `core.py:4298` has
  side-effects on other kernel callers was **not** assessed. It is a kernel edit and needs its own
  blast-radius review — `emit`-during-close-out already swallows exceptions (`:4312-4315`), but the
  trailing `raise` at `:4316` means callers still see the original exception, so the change is
  plausibly contained. **INFERRED, not verified.**
- No search was done for third-party pi rpc clients outside the repo, so "no external clients" is
  absence-of-evidence, not evidence-of-absence.
- The cost of moving the ADR-0034 pin for **non-rpc** surfaces is unmeasured (§6.4).
- pi's `preflightResult` callback contract lives in `agent-session.ts`, which was not fetched; the
  aelix-side seam design (what `harness.prompt` would need to accept) is therefore un-specified here.

## 8. Reproduction

```bash
S=https://raw.githubusercontent.com/earendil-works/pi
P=packages/coding-agent/src/modes/rpc
for f in rpc-mode.ts rpc-types.ts rpc-client.ts jsonl.ts; do
  curl -sS -o "074-$f"  "$S/734e08edf82ff315bc3d96472a6ebfa69a1d8016/$P/$f"
  curl -sS -o "0802-$f" "$S/v0.80.2/$P/$f"
  diff -u "074-$f" "0802-$f"
done
curl -sS -o rpc.md "$S/v0.80.2/packages/coding-agent/docs/rpc.md"
grep -ni "version\|stable\|breaking\|deprecat" rpc.md    # -> 0 hits
grep -n  "agent_end\|synthetic" 074-rpc-mode.ts          # -> 0 hits
```
