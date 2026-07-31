# RPC protocol options — JUDGMENT under LENS 2: DEFECT CLOSURE

> **Written:** 2026-07-29, RPC-sprint design phase. Tree at `main` = `da61337`. **Nothing was edited.**
> Judged under ONE lens only: *does the option actually fix the measured defects, permanently and
> structurally, versus papering over them?* Cost, schedule, pi-parity politics, interop and
> maintainability are **out of scope here** and are deliberately not scored.
>
> **MEASURED** = I ran it or opened it at `da61337`. **INFERRED** = reasoning on top of measured facts.
> Every `path:line` was re-opened against the current tree; the recon notes' numbers were not trusted.
>
> Inputs judged: `rpc-protocol-option-a-bespoke.md`, `rpc-protocol-option-b-standard.md`,
> `rpc-protocol-option-c-dual.md`, against `rpc-protocol-recon-{wire,pi,consumers,standards}.md`
> and `rpc-sprint-{kickoff-handoff,recon-transport,recon-envelope}.md`.

---

## 0. VERDICT

| Option | Score (0-10, this lens only) |
|---|---|
| **A — bespoke, fixed** | **8** |
| **B — standard-native (JSON-RPC/ACP, dual-speak)** | **6** |
| **C — dual-stack / layered (as defended: "C-minimal")** | **3** |

**A wins under this lens, and the margin over B is moderate, over C large.**

The one sentence that decides it: **A is the only option that closes the abort terminator under
the sprint's stated kernel-forbidden constraint, in a record the *existing* in-tree client already
resolves on.** B's structural terminator is better-designed but lives in a dialect B itself
schedules last and declares droppable, and it never reaches the wire B ships the deliverable
against. C has no kernel-free abort answer at all.

---

## 1. MEASUREMENTS I MADE (the ground the judgment stands on)

### 1.1 EXECUTED PROBE — abort and busy, at `da61337`

Script: a real `AgentHarness` with a 5 s stream_fn, a `subscribe` listener recording event types,
driving the actual `_handle_prompt` / `_handle_abort` from `rpc/rpc_mode.py`. Output verbatim:

```
=== ABORT PROBE ===
prompt response -> {'type': 'response', 'command': 'prompt', 'success': True, 'id': 'p1'}
events before abort: ['agent_start','turn_start','message_start','message_end','message_start']
abort response  -> {'type': 'response', 'command': 'abort', 'success': True, 'id': 'a1'}
events AFTER abort: ['agent_start','turn_start','message_start','message_end','message_start']
agent_end present? False
harness phase after abort: idle
pending prompt tasks: []          # <- the fire-and-forget prompt task COMPLETED

=== BUSY PROBE ===
first  -> {'type':'response','command':'prompt','success':True,'id':'p1'}
second -> {'type':'response','command':'prompt','success':True,'id':'p2'}   # <- a LIE
agent_end present? False
after one sleep(0): task.done() = True
  exception: AgentHarnessError("AgentHarness is busy (phase='turn'); ...")
```

Four things this establishes, all MEASURED:

1. **The abort path really produces no wire-visible terminator.** No `agent_end`, no closure
   events, and `_handle_abort` returns `success: true`. The task-brief requirement is confirmed.
2. **The busy rejection really is acked `success: true`** before validation.
3. **The prompt task COMPLETES on abort** (`pending prompt tasks: []`). `harness.prompt()` returns
   normally — `_run` (`harness/core.py:4052`, the enclosing def) hits
   `except asyncio.CancelledError: if self._abort_requested: return []` at
   `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:4287-4295`, **inside** the same
   `try` whose `except AgentHarnessError` closure block at `:4298-4316` emits the four events
   including `AgentEndEvent(...)` at `:4310`. This is the fact both A's `_TurnTracker` and B's
   deferred-reply design depend on, and **it holds**.
4. **Option A's most fragile load-bearing assumption is TRUE at `da61337`**: after exactly one
   `await asyncio.sleep(0)`, the prompt task is `done()` and carries the busy `AgentHarnessError`.
   The busy guard is the first statement of `prompt()` (`core.py:1189-1194`) and the phase flip is
   synchronous before the first await (`:1197-1198`, with a comment saying so). A flags this as
   unpinned (§8.6) and is right to; but it is not speculative — it measures true today.

Abort is also confirmed to route through `self._hooks.emit(AbortHookEvent(...))` (`core.py:~1428`),
**not** through the `subscribe` listener bus that `_on_agent_event` feeds — so there is structurally
no wire record for it.

### 1.2 `extension_ui_request` is types-only; `extension_ui_response` is dropped — CONFIRMED

```
grep -rn "extension_ui_request\|RpcExtensionUIRequest" --include=*.py packages/ tests/
```
returns **only**: the 9 dataclass definitions (`rpc/rpc_types.py:646-743`), the re-exports
(`rpc/__init__.py:59-68,121-130`), and a shape-pin test. **Zero construction sites.**

The drop is at `rpc/rpc_mode.py:2045-2049`, read verbatim at `da61337`:

```python
        # Route extension UI responses to a no-op for Sprint 6d (bridge
        # deferred to Sprint 6f per ADR-0058). The wire shape is recognised
        # but not yet correlated.
        if payload.get("type") == "extension_ui_response":
            return
```

A bare `return` — no response, no error, no ack. **ADR-0197 §(i) blocker (b) confirmed verbatim.**

### 1.3 The terminator is the ONLY thing the in-tree client waits on — CONFIRMED

`rpc/rpc_client.py`: `wait_for_idle` (`:395`), `collect_events` (`:415`) and `prompt_and_wait`
(`:441`) each install a listener whose sole predicate is
`event.get("type") == "agent_end"`, with `DEFAULT_WAIT_FOR_IDLE_MS = 60_000`.

**Consequence the judgment turns on:** a *synthetic `agent_end` event* (Option A) unblocks the
existing client with **zero client-side change**. A *correlated `success:false` response* (Option C's
step 3, pi's `rpc-mode.ts:395-399` shape) does **not** — it resolves the `_send` future, not the
events future. C never notices this.

### 1.4 Option A's `_meta` sidecar is mechanically sound — CONFIRMED

- `_output` is a **single write funnel**: definition at `rpc/rpc_mode.py:1879-1880`, exactly four
  call sites (`:1935` event, `:2030`/`:2038` parse errors, `:2061` response). One injection point.
- `rpc_client._handle_stdout_line` (`:496-536`) reads exclusively via `.get()`.
- `parse_rpc_response` (`rpc/rpc_types.py:516-543`) reads exclusively via `.get()` —
  `payload.get("type")`, `.get("command")`, `.get("success")`, `.get("id")`, `.get("data")`,
  `.get("error")`. **It does NOT do `cls(**payload)`.**
  So A's rule R1 ("the server→client direction is open") holds: adding `_meta` to every server
  record does not break the in-tree client. This was A's single most load-bearing structural claim
  and it verifies.
- `harness.state` is a real public property (`core.py:918-919`), so the tracker can build
  `AgentEndEvent(messages=list(harness.state.messages))` with no kernel edit.

### 1.5 A's correction of the background notes is right; C repeats a claim that is false

`rpc/rpc_mode.py:1765-1776` (re-opened): **both** error paths echo the id — the parse path echoes
`payload.get("id")` when it is a `str`, and the unknown-command path echoes `cmd.id`.
So the pi recon's "adopt pi v0.80.2's `error(id, …)` fix" is **already done**.
- **A §2.1 caught this and budgets zero for it.** Correct.
- **C §4.3 and §7.3 both list "unknown-command errors not echoing `id`" as a measured defect** that
  the pi freeze would preserve. **False at `da61337`.** It is one of the five "knowingly frozen
  defects" C uses to build its case; one of the five does not exist.

### 1.6 The pi-parity pin is set-equality — CONFIRMED (affects A's 30th command)

`tests/pi_parity/test_phase_4_4_strict_superset.py`: `assert pi_types == RPC_COMMAND_TYPES`
(set equality against the fixture), and `assert len(RPC_COMMAND_TYPES) == 29` /
`len(SUPPORTED_COMMANDS) == 29` / `len(DEFERRED_COMMANDS) == 0`. A's `hello` command genuinely
requires amending this pin, exactly as A states. (Cost, not closure — noted, not scored here.)

### 1.7 Exact-dict assertion blast radius for A's `_meta`

`grep -rn "assert .*== {" tests/rpc/` → **30 asserts across 14 files**. Spot-checked:
`test_rpc_mode_stdin_stdout.py` and `test_rpc_mode_event_pipe.py` have **none**. The bulk are on
`response.to_json()` / on `data` payloads. A's decision to inject `_meta` at `_output` rather than
inside `to_json()` is therefore the right shape and keeps those green. A's uncertainty #3 ("I did
not enumerate which of the 190 tests break") remains honest but the shape holds.

---

## 2. THE DEFECT-BY-DEFECT MATRIX

✅ closed structurally · ◐ closed conditionally / partially · ✖ not closed · **[decl]** declared+gated
rather than fixed

| # | Defect | **A** | **B** (slice 1 as scoped) | **C** (as defended: C-minimal) |
|---|---|---|---|---|
| 1 | No terminator on **abort** | ✅ synthetic `agent_end` via `_TurnTracker`, kernel-free, ordered before the ack | ◐ ✅ in the *standard* dialect (prompt-is-the-turn); ✖ in the pi dialect, whose only named fix is the **forbidden kernel edit** | ✖ **conditional on a kernel edit the sprint forbids; no fallback** |
| 2 | No terminator on **failed prompt** (post-acceptance) | ✅ same tracker | ◐ same split | ✖ pi's `output(error(id,…))` only fires pre-acceptance; post-acceptance failure still hangs |
| 3 | No terminator on **busy rejection** | ✅ preflight → `success:false` + `code:"busy"` | ✅ standard dialect: typed `-32010`; ✖ pi dialect unspecified | ✅ preflight seam |
| 4 | `prompt` acks `success:true` **before validation** | ✅ preflight (MEASURED to work, §1.1(4)) | ✅ standard dialect (by design choice, **not** by the envelope — see §3.2) | ✅ preflight seam |
| 5 | No event **correlation or sequence** | ✅ `_meta.seq` (total, at the single write site) + `_meta.turn` | ✅ `params.promptId` + `params.seq` — standard dialect only | ✖ not addressed |
| 6 | Free-text errors, **no code space** | ✅ additive `code` symbol + explicit JSON-RPC int mapping table | ✅ integer codes, minted in `-32010…-32049` with a collision analysis vs LSP/ACP — **the best-designed of the three** | ✖ one-off `data` field at most |
| 7 | No **wire version / capability handshake** | ✅ `hello` + `server_hello` — **but this is the hand-rolled one** (§3.1) | ✅ ACP `initialize` w/ `protocolVersion` + "omitted ⇒ UNSUPPORTED" — **strictly better than A's** | ✖ §3.2 measures a handshake is blocked by the closed envelope; C never proposes relaxing it |
| 8 | **camelCase responses beside snake_case events on one fd** | **[decl]** boundary declared + machine-gated, not moved | ✖ **arguably worse**: JSON-RPC camelCase envelope + snake_case `params.event` + camelCase `result`, × two grammars | ✖ not addressed (and full-C's §5.4(ii) concedes "the envelope, not the vocabulary") |
| 9 | **No server→client request direction** (blocks ADR-0197 §(i) (b)) | ◐ deferred, **but doors unlocked**: `srv_` id partition reserved, the silent drop replaced by a visible error, `capabilities.extensionUiBridge:false` advertised | ◐ envelope makes it expressible + ACP's normative cancel-precedence rule; implementation **explicitly deferred out of slice 1** | ✖ **never engaged with at all** |
| | **Totals** | **7 ✅ / 1 decl / 1 ◐** | **4-5 ✅ (standard dialect only) / 2 ✖ / 2 ◐** | **2 ✅ / 6 ✖ / 1 ◐** |

---

## 3. WHY THE SCORES

### 3.1 A — 8/10

**What earns it.** Every terminator defect is closed by one mechanism that I verified is
mechanically available at `da61337`: the prompt task completes on abort (§1.1(3)), `harness.state`
is public (§1.4), `_output` is a single funnel (§1.4), and the existing client resolves on
`agent_end` (§1.3). So A's closure is **visible to the client that already exists**, needs **no
kernel edit**, and needs **no new client**. Under this lens that combination is decisive.

A also has the only **graceful degradation** in the field. If the `sleep(0)` preflight ever stops
working (A's own §8.6 risk, and my §1.1(4) shows it works *today* but nothing pins it), the
`_TurnTracker` still fires when the task dies — so the 60 s hang stays closed and only the ack's
truthfulness degrades. Neither B nor C has a second line of defence.

`_meta.seq` is a genuinely stronger closure of D3 than anything the standards offer: the standards
recon MEASURED that even ACP gives correlation but **not** a sequence number, and `seq` assigned at
the single write site is a *total emission order*, which is exactly what is needed given that parse
errors provably jump the queue.

**What it loses marks for.**
- **The handshake is the badly-re-invented one.** A concedes it does not even state the
  "capabilities omitted ⇒ UNSUPPORTED" default rule that ACP specifies at MUST level with five SDK
  implementations. And `server_hello` — an *unsolicited server greeting* — is a design neither pi
  nor ACP has (ACP's `initialize` is client-initiated), and A measured that it breaks three
  `tests/server` tests. This is the one place the "hand-rolled fixes re-invent a standard badly"
  penalty genuinely bites. The `code` table (symbols with an explicit int mapping) and `_meta` /
  `stopReason` (ACP's slot and vocabulary adopted verbatim) do **not** — those are borrowings, not
  re-inventions.
- **D6 is declared, not closed.** A says so plainly ("does not move the boundary; makes it a rule
  and a test"). A declared+gated boundary is materially better than an undeclared accident, but the
  defect as stated survives. And A's one substantive move here — recasting
  `get_messages`/`compact`/`get_session_stats` to camelCase — is A's own **INFERRED, unverified**
  claim (its uncertainty #1).
- **The fix is one layer above the bug.** The pi recon MEASURED, and I re-confirmed at
  `core.py:4287-4316`, that the kernel already contains the ported closure block and two lines
  bypass it. A creates a second turn-closure implementation with a deliberate de-sync guard, and
  leaves the TUI and `--mode json` still terminator-less on abort. A states this against itself
  (§8.3) — which is why it costs a point, not three.
- **D7 stays open.** A refuses to pretend otherwise, and reserves three concrete things that make
  the later work purely additive. Honest, but open.

**Fatal flaw (this lens):** none. The nearest candidate — the preflight resting on an unpinned
kernel ordering — is measured true today and has a fallback.

### 3.2 B — 6/10

**What earns it.** Per-defect, B's mechanisms are the **best-designed on offer**, and three of them
are strictly better than A's: `initialize` with normative capability defaults, integer error codes
minted with an actual collision analysis against LSP's squat and ACP's `-32800`, and
prompt-is-the-turn, which makes the terminator the reply that JSON-RPC §5 *already obliges the
server to send*. That last one is the cleanest terminator design in the field, and my probe
(§1.1(3)) confirms its premise: `harness.prompt()` returns normally on abort, so a deferred reply
resolves without touching the kernel. B is also the only note that correctly identifies that ACP
gives correlation but not sequence, and adds `seq` itself.

B is also, on the whole, **scrupulous about framing vs semantics** — §8.1, §8.2 and §8.4 are
self-demolishing and accurate, and §3.1 counts the ~15 rpc_mode lines the deferred reply costs
rather than pretending the envelope supplies it.

**Why it still loses to A under *this* lens — three measured reasons.**

1. **The structural closure never reaches the wire the deliverable is banked against.** B's own
   §7.3 sequencing banks `RpcChannel` + the parity test **against the pi dialect at step 4**, and
   lands the JSON-RPC codec at step 5, with the explicit escape hatch: *"If step 5 overruns, the
   sprint still closes with the deliverable shipped."* So in the failure mode B itself designs for,
   the shipped artefact carries **zero** of B's marginal closure.
2. **B has no named kernel-free fix for the pi dialect's abort terminator.** §3.2 is explicit:
   *"Option B removes it from the `RpcChannel`'s critical path, it does not remove it from the
   backlog."* §7.1 grounds "the pi-dialect terminator fixes" in the pi recon §4b, whose two headline
   items are the **kernel** edits at `core.py:4294` and `:4298` — which the kickoff handoff forbids
   and which B's own uncertainty #10 says were never assessed for side effects. The reference to
   "(Option A's core)" at §7.3 step 3 is the only hint of a kernel-free path, and it is one
   parenthetical with no mechanism behind it.
3. **B makes the defective wire permanent, by design.** §5.5, in B's own words: *"It does not [die].
   Not under the current principle… Budget for two grammars forever."* D3/D4/D5/D6/D7 remain live
   forever on a wire that README advertises, that 345 in-repo tests pin, and that any client may
   still speak. For D4/D5/D7 that is arguably acceptable (new clients use the new wire). For D1/D2 —
   the defects that actually break the deliverable — it is not.

**One measurable overstatement.** §3.3: *"an early ack is **structurally impossible**, because there
is nothing to ack."* That is false of the envelope. The standards recon B itself cites says so
explicitly: *"D2 — NOT FIXED. This is purely semantic… A JSON-RPC rewrite of `_handle_prompt` that
kept the `create_task` + immediate return would be a fully-conforming JSON-RPC server with the
identical bug."* B's closure of D2 comes from **adopting ACP's prompt-turn semantics**, not from
JSON-RPC. B gets this right everywhere else in the note; this one sentence is the framing-fixes-
semantics fallacy the lens asks me to penalise, and I have — but only mildly, because it is a single
sentence contradicted by B's own §2.4/§8.1/§8.2 rather than a load-bearing pretence.

**And D6 gets worse, not better.** B's §3.4 carries `params.event` verbatim snake_case inside a
camelCase JSON-RPC envelope beside camelCase `result` payloads — and B's §8.3 admits the end state
is *"two grammars × two spellings, forever."* B does not claim D6 closure; it does not get credit,
and it takes a small penalty for regression.

**Fatal flaw (this lens):** yes, one — *the only named mechanism for closing the abort terminator on
the wire B actually ships (the pi dialect, which `RpcChannel` is banked against at step 4) is the
kernel edit the sprint constraint forbids; the structural fix lives in a dialect B schedules last
and declares droppable.*

### 3.3 C — 3/10

C's §7 demolishes full-C and §8 names "C-minimal" as the variant it would defend. I score
C-minimal, because that is the proposal; full-C scores lower still (see the bottom of this section).

**What earns it.** C makes the single most accurate observation in the whole field for this lens,
and makes it twice: *"the failure is caught inside `_run()`'s `except` at `rpc_mode.py:310`, which
the dialect never sees. **A codec adapter cannot fix this.**"* (§5.1) and *"Not one of those four
blocks the `RpcChannel`. Option C adds a forever-cost to obtain features the immediate deliverable
does not need, while the blocking work is untouched"* (§7.1). C never commits the framing-fixes-
semantics fallacy. Its `PiDialect`-is-provably-inert seam and its "exactly one dialect is registered"
gate are good engineering. C also correctly measures (§7.6) that the one command where the
clean-layering story fails is `prompt` — the command the channel lives on.

**Why it scores 3 anyway.**

1. **It has no kernel-free abort answer.** C-minimal step 3 reads: *"and the kernel terminator
   (`harness/core.py:4287-4298`) **if the owner permits a kernel edit**."* There is no fallback.
   Step 4 then asserts *"after step 3 the pi dialect terminates correctly"* — a claim conditional on
   a permission the sprint does not have. C's only other answer, §5.2, is *"it must keep its own
   deadline regardless"* — a channel-side deadline, which the transport recon's own open question 1
   labels as the option that is **not honest** ("Only (a) is honest"). Under a defect-closure lens
   that is the definition of papering over.
2. **The post-acceptance-failure terminator is left open by a gap C does not see.** C's step 3
   offers "the correlated prompt error response (pi parity restoration, `rpc-mode.ts:395-399`)".
   MEASURED (§1.3): `prompt_and_wait` / `wait_for_idle` / `collect_events` resolve **only** on
   `agent_end`; a `success:false` response resolves the `_send` future, not the events future. And
   pi's own `.catch` fires that error **only when preflight did not succeed** — so post-acceptance
   failures in pi are terminated by `emitRunFailure` → `agent_end`, i.e. by the kernel path C makes
   optional. C therefore closes only the pre-acceptance half and does not notice.
3. **It closes nothing else.** D3, D4, D5, D6 are untouched by C-minimal. D5 is worse than untouched:
   C §3.2 correctly measures that a handshake is structurally blocked by the closed command envelope
   (`rpc_types.py:423`, `cls(**kwargs)`) — and then never proposes relaxing it. A's §3.0/R2
   three-line filter is the fix C's own analysis demands and C omits.
4. **D7 is never engaged with at all.** `extension_ui_response` appears exactly once in C's note — as
   a line in the pipeline diagram (`rpc_mode.py:2048`, "bare `return`"). No proposal, no reservation,
   no capability flag, no mention of ADR-0197 §(i) blocker (b). C is the only option that does not
   engage with the defect the sprint exists to unblock.
5. **One of C's five load-bearing "frozen defects" does not exist.** §1.5 above: unknown-command
   errors **do** echo the id at `da61337` (`rpc_mode.py:1771-1776`). C's §4.3 and §7.3 both assert
   otherwise. (It weakens an argument C was making *against itself*, so it does not change the
   direction of the verdict — but it is a measured overstatement.)

**Full-C is worse than C-minimal under this lens**, and C says why in its own §4.3: a frozen pi
dialect means *"maintaining a wire you have documented as defective, on purpose, indefinitely."*
That is institutionalising the defects, not closing them.

**Fatal flaw (this lens):** yes — *under the sprint's stated kernel-forbidden constraint, C-minimal
leaves the abort terminator and the post-acceptance-failure terminator open with no fallback,
closes none of D3/D4/D5/D6, and never engages with D7 at all; the seam it does land closes zero
defects by C's own admission.*

---

## 4. THINGS THE NEXT PHASE SHOULD CARRY FORWARD

Regardless of which option is chosen, these are MEASURED and load-bearing:

1. **The abort terminator has exactly two kernel-free mechanisms, and both depend on the same fact:**
   the fire-and-forget prompt task **completes** on abort (probe §1.1(3)). Either synthesise an
   `agent_end` when it completes with the turn unclosed (A), or make the prompt's *response* the
   thing that lands when it completes (B's standard dialect). A third option — a channel-side
   deadline — closes nothing.
2. **Any fix that is not an `agent_end` event does not unblock the existing client.**
   `rpc_client.py:395/415/441` resolve on `agent_end` and nothing else. A correlated `success:false`
   response is *not* a terminator for `prompt_and_wait`.
3. **`_output` (`rpc_mode.py:1879-1880`) is the single write funnel** — four call sites, all of them
   run through it. Anything that must ride every record (`seq`, a version, a `_meta`) has exactly
   one insertion point, and `parse_rpc_response` + `_handle_stdout_line` both read via `.get()`, so
   additive server→client keys are safe for the in-tree client.
4. **The `sleep(0)` preflight works at `da61337`** (probe §1.1(4)) but is pinned by nothing. If it is
   adopted, it needs a test *and* a fallback that closes the hang even when the preflight misses.
5. **Do not budget for the v0.80.2 unknown-command id-echo fix.** It is already in the tree
   (`rpc_mode.py:1765-1776`). Two of the three option notes' background material still lists it as
   owed work.
6. **The `extension_ui_response` bare `return` (`rpc_mode.py:2048-2049`) is the cheapest partial
   closure of D7 in the whole field.** Whatever option wins, replacing a silent drop with a visible
   error costs two lines and removes a failure mode that is indistinguishable from success —
   which is the exact class of defect this sprint exists to eliminate. Only A proposes it.

---

## 5. WHAT I DID NOT MEASURE

- I did not run the test suites. §1.7's "30 exact-dict asserts / 14 files" is a grep, not a
  blast-radius analysis of any specific patch.
- I did not build A's `_TurnTracker` or B's deferred-reply handler and run them; §1.1 establishes
  that the *preconditions* for both hold, not that either implementation is correct.
- I did not verify A's INFERRED claim that pi's `get_messages` / `compact` / `get_session_stats`
  payloads are camelCase (A's own uncertainty #1). D6 is scored **[decl]** for A on the basis of
  what A commits to, not on that unverified premise.
- I did not evaluate cost, schedule, pi-parity politics, interop, or maintainability. Those belong
  to other lenses and are deliberately absent from every score above.
- I did not assess whether widening `except AgentHarnessError` at `core.py:4298` is side-effect-free
  — the pi recon and Option B both flag it as an unassessed kernel edit, and I inherit that gap.
