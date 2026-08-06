# RPC protocol — owner decision brief

> Written 2026-07-29/30 at `main` = `da61337`. Synthesis of 4 recon notes, 3 design options and 3
> adversarial lens judgments (compatibility · defect-closure · cost/sequencing), all in this
> directory. **MEASURED** = someone ran or opened it at `da61337`, and I re-verified every fact
> cited in §1 and §4 myself. **INFERRED** = reasoning on top of measured facts, labelled inline.

---

## 1. THE ANSWER

**Keep pi's bespoke JSONL wire. Do not adopt JSON-RPC or ACP as the wire — this sprint or later.
Implement "faithful parity" properly, which means *fixing* the defects, because the blocking ones
are aelix regressions against behaviour pi implements and documents. Take the standards' vocabulary
for free now; keep ACP as an out-of-process adapter option for later.**

**The single strongest reason:** the two defects that actually block this sprint — no turn
terminator, and a `success:true` ack written before the prompt is validated — are **semantic, not
structural**. They are fixed at `rpc/rpc_mode.py:309-330`, `:333-340` and
`harness/core.py:4287-4298` in roughly the same ~15-80 lines *in any envelope*. A standard envelope
does not fix either one, and Option B's own author measured its Tier-1 interop dividend at exactly
zero: JSON-RPC with an `aelix/*` method namespace carrying snake_case kernel dataclasses is a
standard nobody speaks. Zed cannot drive it; no SDK targets it. You would pay a permanent
two-grammar tax for a design dividend the standards recon shows is available for nothing — adopt
ACP's `StopReason` names and JSON-RPC's `-32000…-32099` band *inside the pi envelope* and a future
ACP adapter becomes a rename rather than a redesign.

Mechanically, this is **Option C-minimal's shape carrying Option A's defect closure**, with A's
three most expensive additions cut (the 30th `hello` command, the unsolicited `server_hello` line,
and `_meta` on the streaming event path). That combination is the only one that wins on all three
lenses: it ships the deliverable first, adds **zero new records to the wire**, amends **zero**
pi-parity pins, and still closes the terminator.

---

## 2. THE DECISIVE FACTS

### 2.1 No real user is on this wire. Nobody could be.

MEASURED, re-run by me at `da61337`: `git tag -l` → **0**; `git ls-remote --tags origin` → **empty**;
`gh release list` → empty. `aelix-web` does not exist on disk or on GitHub. `packages/aelix-server`
is `rm -f`'d out of the publish set **by its own release workflow** (`.github/workflows/release.yml:82-87`)
and is byte-transparent anyway — a wire change costs it **0 source lines**.

Stronger than that: **the stdio server has never started.** `aelix --mode rpc` raises
`RuntimeError` before emitting a byte, because `cli/entry.py:2160-2166` passes `repo=`/`fs=`
alongside an explicit `runtime_host=`, exactly the combination `rpc/rpc_mode.py:1906-1914` rejects
(reproduced by two independent judges). No user could have written a client even if a release
existed — and there is no RPC reference document in the repo at all, so they would have had to read
the source.

**The only public promise is a SHAPE**, and every option preserves it verbatim:
`docs/guides/getting-started.md:74` — *"JSONL command/response protocol on stdio"*; `README.md:50-52`
— *"a `--mode rpc` JSONL protocol"*. The only `pi` mention in `README.md` is at `:205-212`, inside a
section headed **"License & attribution"**. **No public artefact promises pi compatibility.**

The entire obligation is self-imposed: `tests/pi_parity/` + one fixture, enforcing ADR-0103:7-8's
binding principle. It is real, it is yours, and it is amendable by ADR.

### 2.2 The blocking defects are NOT inherited from pi. Fixing them is convergence.

This is the fact that inverts the kickoff handoff's §3 framing, and it was verified twice against pi
source fetched at the pin `734e08e`:

- pi wraps the whole agent loop in a **bare `catch (error)`** (`agent-harness.ts:568-575`) and routes
  every failure — **abort included** — into `emitRunFailure`, which emits the full four-event closure
  `message_start → message_end → turn_end → agent_end` (`:512-524`, `agent_end` at `:522`), with
  `stopReason: aborted ? "aborted" : "error"` (`:49-56`). **In pi, `agent_end` always arrives.**
- aelix **already contains that ported block** at `harness/core.py:4298-4316`, with its own comment
  reading *"Pi parity: synthesize failure assistant message + emit closure events."* (MEASURED by me.)
  Two lines bypass it: `except asyncio.CancelledError:` at `:4287` with `if self._abort_requested:
  return []` at `:4293-4294` **inside the same try**, and the closure filter narrowed to
  `except AgentHarnessError` at `:4298` where pi's is bare.
- Same picture on the ack: pi fires `output(success(id,"prompt"))` **only** from
  `preflightResult(didSucceed=true)` (`074-rpc-mode.ts:386-393`) and documents it
  (`rpc.md:76` — *"`success: false` means the prompt was rejected before acceptance"*). aelix returns
  `RpcSuccessResponse` unconditionally at `rpc_mode.py:330`, before the task created at `:322` runs.
- Same for images: pi forwards `images`/`streamingBehavior` into `session.prompt`
  (`074-rpc-mode.ts:382-386`); `rpc_mode.py:309` calls `harness.prompt(cmd.message, source="rpc")`
  and drops both.

**Consequence, and it is decisive:** "fix them" ≠ "diverge" under any option. It is *parity
restoration*, which the repo's own regime (ADR-0035's bar, 51 `Aelix-additive` files) already
sanctions. It also means Option A's self-description as *"keeps parity"* was backwards — A is the
only option that actually amends the pi pin, while building turn closure in `rpc_mode.py` as a
divergence to avoid a convergence pi puts in the harness.

**Correct the record while here (MEASURED):** `rpc_mode.py:316-320`'s comment claims pi
"emits a synthetic terminal event" at `rpc-mode.ts:379-401`. It does not — `grep -n "agent_end\|synthetic"`
on pi's `rpc-mode.ts` returns **0 hits at both v0.74.1 and v0.80.2**. That false citation propagated
into ADR-0058 (`:66`, `:173-175`) and has sent this work item to the wrong layer for six sprints.

### 2.3 ACP/JSON-RPC would NOT give the approval back-channel for free.

The wire half, yes. The expensive half, no — and the expensive half was never on the wire.

- ✅ JSON-RPC/ACP give the *direction* (both peers may send requests) and ACP gives a permission
  vocabulary (`allow_once`/`allow_always`/`reject_once`/`reject_always`) plus the normative
  cancel-precedence rule (a pending permission on a cancelled turn is a hang; ACP already specifies
  the answer). Real value.
- ❌ **ADR-0197 §(i) blocker (c) survives untouched**: the parent TUI has no modal arbiter. That is a
  UI/runtime problem no framing standard touches.
- ❌ **ACP's permission is per-tool-call within a turn. aelix's P2/P3 consent is spawn-time and
  per-delegation.** Different events. Mapping one onto the other is design work, not adoption.
- ❌ ACP knows nothing of the depth guard, fork-bomb prevention, band rule, posture clamp or the
  per-prompt budget. All of P2/P3's governance stays where it is.
- ⚠️ ACP *adds* attack surface. `PermissionOption.name` and `toolCall.title` are agent-supplied
  human-readable text destined for a consent dialog — kickoff §5 invariant 1 applies more sharply,
  not less, and P3's review already proved by execution that unsanitised dialog text forges consent.

**Also measured:** the transport for the back-channel already exists and is free under *every*
option. An rpc child's stdin is a live bidirectional JSONL channel (`rpc_mode.py:1884-1889` + the
pump at `:2078-2092`); `_read_piped_stdin` is gated to print/json at `cli/entry.py:1346`. The four
missing pieces (`extension_ui_request` emission, `extension_ui_response` handling, a server-side
pending map, a client-side inbound dispatcher) are all **additive** and none touches the transport.
Deferring costs a second protocol revision, not a rewrite.

**And the community's own answer on aelix's upstream is adapter-over-rpc, not replace-rpc:** `pi-acp`
speaks ACP outward and **spawns `pi --mode rpc`** inward. Since `RpcChannel` is already a subprocess
boundary, moving that adapter inside the process buys one free hop and costs a permanent product-core
seam. (MEASURED that `pi-acp` exists; INFERRED that the same shape applies to aelix, but the shape is
identical.)

### 2.4 One more measured landmine none of the three options budgeted

**Any kernel byte — including a comment — turns `tests/agents/test_p2_band_boundaries.py::test_kernel_untouched_vs_merge_base`
RED**, proven by probe: it diffs against `merge-base(origin/main, HEAD)` (`:214-226`) *and* checks
`git status --porcelain`. If the kernel terminator fix is authorised (§5, Fork 1), the band gate
needs an amendment in the same PR, and the gate stays red for the branch's whole life until it lands.

Second trap: `_CAP_NAME_RE` (`tests/agents/test_p2_band_boundaries.py:131-133`) matches
`(^|_)(MAX|MIN)_|_(CAP|LIMIT|BUDGET|CEILING|TIMEOUT|MS|BYTES|TASKS|CHILDREN|CONCURRENCY)$` as an
**exact set** over product-core. A per-line budget constant named `MAX_LINE_BYTES` or
`STREAM_LIMIT_BYTES` in `rpc/_jsonl.py` **goes red on arrival**. Use an `__init__` parameter
defaulting to `None`; the *values* come from `aelix_agents`.

---

## 3. WHERE THE JUDGES DISAGREED

Three lenses, three different winners. The disagreement is real and it is informative — do not
average it.

| Lens | A (bespoke, fixed) | B (standard/dual-speak) | C (dual-stack) |
|---|---|---|---|
| **1 — compatibility & obligation** | 6 | 5.5 | **7.5** (C-minimal only) |
| **2 — defect closure** | **8** | 6 | 3 |
| **3 — cost, blast radius, sequencing** | 7 | 5 | **8** (C-minimal only) |

**All three lenses put B last.** That is the one unanimous verdict, and it settles the owner's
question: not a standard RPC.

### Disagreement 1 — A vs C-minimal on whether the abort terminator is actually closed. (Sharpest.)

- **Lens 3 (C wins):** C-minimal creates **no divergence between the two channels**, preserving the
  measured gift that makes the parity test cheap — `modes/print_mode.py:59-68` delegates to
  `rpc_mode._dataclass_to_dict`, so both channels are shape-identical *by construction*. A adds
  `_meta` to rpc events and not json events, creating the first-ever divergence on the very sprint
  the parity test is written; the test must then strip a field to compare.
- **Lens 2 (A wins):** C-minimal has **no kernel-free abort answer at all.** Its step 3 is qualified
  *"if the owner permits a kernel edit"* with no fallback. And its other answer — a correlated prompt
  *error response* — provably does not unblock the client: MEASURED, `rpc_client.py:395/415/441`,
  `wait_for_idle`/`collect_events`/`prompt_and_wait` resolve **only** on `type == "agent_end"`, so an
  error response resolves the *send* future, not the *events* future. C's claim "after step 3 the pi
  dialect terminates correctly" is conditional on a permission the sprint did not have, and C did not
  notice the gap.
- **Resolution, and it is what §4 builds on:** the disagreement dissolves the moment the owner
  authorises the kernel fix (Fork 1). Then C-minimal's shape is correct *and* terminated. If the
  kernel stays off-limits, A's synthetic `agent_end` is the only mechanism that works — Lens 2
  verified by execution that the fire-and-forget prompt task **completes** on abort
  (`pending prompt tasks: []`), so the tracker has a real trigger, and a synthetic `agent_end`
  unblocks the *existing in-tree client* with zero client-side change. **Ship A's tracker only as the
  fallback, gated on the owner's answer.**

### Disagreement 2 — whether B's "zero pi-parity breaks" is the right ledger.

- **Lens 1** verified B's mechanism and confirmed it holds: `RPC_COMMAND_TYPES` derives from
  `_RPC_COMMAND_REGISTRY.keys()` (`rpc_types.py:814`), so a `method`-keyed grammar adds no registry
  entry and the pins at `:61/:89/:90/:346/:391` all survive; `grep -rn jsonrpc --include=*.py
  packages/` → **0 hits**, so the dialect latch is provably unambiguous.
- **But Lens 1 then ranked B *below* A anyway**, because a zero-break count is not the same as a
  small obligation: B §5.5 concedes in its own words that the pi dialect **never dies** under ADR-0103
  ("budget for two grammars forever; treat *one day we delete it* as wishful"), and B §8.3 concedes
  the pi half must **preserve its defects** because the fixtures pin them. A permanent 2× tax on a
  grammar documented as defective, for zero measured interop, is the heaviest promise on the table.
- **Lens 3** independently reached the same place by a different route: B's §7.3 sequencing is the
  **best artefact of the three** — six steps that bank `RpcChannel` + the parity test at step 4 with
  an explicit "if step 5 overruns, the sprint still closes." That sequencing idea is worth stealing
  even though the option is not.

### Disagreement 3 — is Option C one option or two?

Unanimous once stated: **C-full and C-minimal have opposite profiles.** C-full scores ~3 on lens 3
and is fatal by C's own §7.2 ("a compatibility layer for a population of zero"). C-minimal scores
7.5/8. Two lenses flagged that a decision recorded as "Option C" without naming the variant is
**undefined**. This brief recommends C-**minimal** only, and only with A's closure.

---

## 4. WHERE A JUDGE CAUGHT A DESIGNER OVERSTATING

Five findings that change the plan, not just the scoring.

1. **Option A undercounted its own pi-pin amendment by 40%.** A §5.3 prices a 30th command as
   set-equality at `:52-62` plus three counts at `:89-91`. MEASURED, re-verified by me at `da61337`:
   it is **six assert sites** — `:61`, `:89`, `:90`, `:91`, `:346`
   (`fixture["rpc_command_count"] == len(RPC_COMMAND_TYPES) == 29`) and `:391`
   (`set(PI_COMMAND_FIELDS.keys()) == set(RPC_COMMAND_TYPES)`) — **plus** a 30th field roster in the
   `PI_COMMAND_FIELDS` table at `:355`, **plus** an edit to `fixtures/pi_rpc_mode_734e08e.json`. Two
   of the sites A missed sit under a section header literally named **"§G — Pi fixture immutability."**
   → *This is why §5 recommends cutting `hello` from the sprint.*

2. **Option A's `get_session_stats` casing claim is wrong.** A §3.6/D6 says three `data` payloads are
   snake_case and proposes flipping all three as a probable parity restoration. MEASURED:
   `get_session_stats` is **already** Pi-camelCase via a hand-written `_session_stats_to_dict`
   (`rpc_mode.py:1123-1172`), matching pi's `SessionStats` (`agent-session.ts:209-226`) key for key.
   Only `compact` and `get_messages` are genuine `asdict` passthroughs. The wire recon §2.B carries
   the same error. → *Verify against pi before touching any casing; the D6 work is two payloads, not
   three, and it is not blocking.*

3. **Option B's headline "an early ack is structurally impossible" is false**, and contradicted by
   the standards recon B itself cites: *"Nothing in JSON-RPC forbids replying `{"result":{}}` before
   doing any work."* B's D2 closure comes from adopting ACP's **prompt-turn semantics** — a design
   choice, priced elsewhere by B at ~15 lines — not from the envelope. → *Confirms §1: the envelope
   is not what fixes this.*

4. **Option B asserted a zero blast radius over 625 collected tests without running one.** B §9.6:
   *"I did not run the test suites."* The claim is structurally plausible for the pin table, but
   B §7.2 simultaneously concedes `−20` lines inside `rpc_mode.py` and a dialect latch rewritten into
   `_on_line`, which is the single funnel exercised by 190 `tests/rpc` + 17 `tests/server`.

5. **Option C repeated a stale defect that does not exist.** C §4.3/§7.3 lists "unknown-command errors
   do not echo `id`" as one of five defects a pi freeze would preserve. MEASURED at `da61337`
   (`rpc_mode.py:1765-1776`): **both** the parse path and the unknown-command path echo the id.
   Option A caught this independently and budgets zero for it. C also undercounts the rpc blast radius
   (its "345 tests" vs the measured **625 collected** across `tests/rpc` 190 + `tests/pi_parity` 418 +
   `tests/server` 17) — a correction that makes C's own argument against C-full *stronger*.

One overstatement that survived scrutiny and is worth trusting: **A's `_meta`-in-`_output` design
really is safe.** Lens 3 closed the gap A admitted it had never measured — `tests/rpc` has 34 exact-dict
`== {` asserts across 14 files and 8 files that read back the emitted stream, and **the intersection
is empty**. Adding keys at the `_output` funnel breaks ≈0 existing tests. (Kept for reference; §5
still defers `_meta`, for the parity-test reason, not the test-churn reason.)

---

## 5. THE FIRST SLICE — what this sprint builds

Six steps, ordered so the two owed deliverables (`RpcChannel` + cross-channel parity test) are
**banked at step 4**, before anything contentious. Steps 5-6 are droppable without losing the sprint.

1. **Fix `cli/entry.py:2160-2166`** — drop `repo=`/`fs=` (it raises today, §2.1), and pass
   `model_registry=` so `set_model`/`cycle_model`/`get_available_models` stop returning
   "requires a ModelRegistry" on both live paths. ~3 lines. **Nothing downstream is testable before
   this.** Same fix is owed under all three options.
2. **Build the real-child rpc bed.** It does not exist — spec §8 claims it does; every `tests/rpc`
   client subclasses `RpcClient` to swap `_build_argv` for a `python -c` stub
   (`tests/rpc/test_rpc_client_lifecycle.py:88` says so in its own comment). Also fix the default
   argv at `rpc_client.py:454-471` (`-m aelix` is the mock-stream demo, and `src/aelix` **is** in the
   wheel per `pyproject.toml:59`).
3. **Close the terminator and the ack, below any seam. All parity restorations (§2.2):**
   - **Kernel (Fork 1):** stop `harness/core.py:4293-4294`'s early `return []` bypassing the closure
     block; widen `:4298`'s `except AgentHarnessError` to bare `except Exception`; add an `aborted`
     path so `stop_reason` can be `"aborted"` instead of hardcoded `"error"` at `:4302`. Budget the
     band-gate amendment (§2.4). **If refused:** fall back to A's `_TurnTracker` synthetic `agent_end`
     in `rpc_mode.py`, with A's de-sync guard (a kernel `agent_end` closes the turn so the client
     never sees two) — worse, but it works and the existing client resolves on it.
   - **`rpc_mode.py`:** A's preflight ack — `create_task` + one `await asyncio.sleep(0)`, then return
     `success:false` if the task already died. MEASURED by execution that this works today (the busy
     guard is `prompt()`'s first statement, `core.py:1189-1194`, with a synchronous phase flip before
     the first await at `:1197-1198`) — **and nothing pins that ordering, so this sprint must add the
     test that does.**
   - **`rpc_mode.py:309`:** forward `cmd.images` through `_decode_images` and honour
     `streaming_behavior` by routing to steer/follow_up. Parity restoration.
   - **`rpc_mode.py:2048`:** replace the silent `extension_ui_response` drop with a visible error
     response. A silent drop is indistinguishable from success — the exact failure mode this sprint
     exists to eliminate.
4. **Ship the deliverables on the pi dialect.** ← *bank here.*
   - `aelix_agents/rpc_channel.py`: `RpcChannel` with its **own** argv/env builders (never
     `-m aelix`), carrying `AELIX_SUBAGENT_DEPTH`, the `AELIX_MCP_CONFIG` pop, `--no-agents`, the
     trust argv, and injectable `argv_builder`/`env_builder` seams mirroring `print_channel.py`.
     Spawn policy stays in the extension; **spec lines 275/289 (depth in `rpc_client._build_env`) are
     rejected.**
   - Containment seam: `RpcClientOptions` (today exactly six fields, `rpc_client.py:61-70`) gains
     `start_new_session`, `preexec_fn`, `limit`, `max_line_bytes` — **parameters in core, values from
     the extension.** `rpc/rpc_client.py` is already on `_SPAWN_ALLOWLIST` (`:46-49`), so the gate
     stays green. Watch the `_CAP_NAME_RE` trap (§2.4).
   - `_jsonl.py`: optional per-line budget + dropped counter, default `None` ⇒ byte-identical for
     every existing caller, so `dropped_lines` becomes fillable.
   - `envelope.py:201-205`: **`exit_code = None` for rpc**, pinned by the parity test. It is the only
     value the `failed` clause treats as non-evidence, and an rpc child's returncode is a teardown
     artifact (0 or -9 depending on a 1 s race).
   - `tests/agents_ext/test_cross_channel_parity.py`: four declared asymmetries (interleaved
     `{"type":"response"}` frames, absent session header, `exit_code`, `dropped_lines`), value-level
     comparison only (`ensure_ascii` differs, `_jsonl.py:31` vs `print_mode.py:161`).
5. **Land C-minimal's inert seam.** `dialect: RpcDialect | None = None` on `run_rpc_mode`, 4 methods
   / 5 call-site edits (`:2028`, `:2030`, `:2038`, `:1935`, `:2061`), default `PiDialect` whose
   methods are identity / `resp.to_json()` / `_event_to_dict(event)`. **Prove the no-op two ways** —
   a golden byte comparison of `--mode rpc` output before/after, and an identity assertion on
   `PiDialect`. This converts "we did not change the pi wire" from prose into a test, and buys a real
   option on ACP later at ~20 lines. Plus C's gate: **a test asserting exactly one dialect is
   registered**, so a second cannot land without an ADR.
6. **Two ADRs (or one document, not zero):** the rpc half of the envelope record ADR-0198 deferred,
   and the wire record — which must also **correct** the false `rpc_mode.py:316-320` pi citation and
   ADR-0058 `:66`/`:173-175`, per the repo's own line-citation-correction precedent (ADR-0072 P-258).

### Cut from the sprint, deliberately
- **`hello` / `server_hello` / the 30th command** — the only thing that amends the pi pin (6 asserts
  + a roster + a fixture, §4.1) and the only thing that breaks `tests/server` (3 WS tests, 4 drains).
  Nothing consumes a version today. Revisit when a second consumer exists.
- **`_meta` / `seq` / `turn` on the streaming event path** — it would create the first divergence
  between the two channels on the sprint the parity test is written, and pi documents the absence of
  event ids as deliberate (`rpc.md:746`). The harness busy-guard means one turn per process, so
  correlation is not blocking. Revisit with the back-channel, when two id spaces actually coexist.
- **Error `code` symbols and the casing flip (D4/D6)** — additive, non-blocking. Record the *table*
  in the ADR (symbol → JSON-RPC integer) so the later adapter is a dict lookup; implement later.
- **Everything JSON-RPC/ACP on the wire.**

---

## 6. WHAT ONLY THE OWNER CAN DECIDE

Four genuine forks. Research cannot settle any of them.

| # | Fork | Recommended default |
|---|---|---|
| **1** | **May this sprint edit the kernel (`harness/core.py:4287-4298`) to restore a pi behaviour the file already claims to implement?** This is *not* the fork the kickoff handoff posed — it asked about product-core. Note it is a **policy** question that also carries a **gate** cost (§2.4). | **YES.** It is convergence, not divergence; it fixes the TUI and `--mode json` too, not just rpc; and it is ~2 lines against ~80 for the workaround. Requires a band-gate amendment in the same PR, and a blast-radius review of widening `except AgentHarnessError` (never assessed — `:4312-4315` already swallows emit exceptions and `:4316` re-raises, so it is *plausibly* contained; INFERRED). |
| **2** | **Does ADR-0103's "완전 동일하게" principle bind the wire's *defects*, or only its *shape*?** Everything in §5 step 3 assumes shape. If it binds bytes, the sprint cannot fix the ack or the images drop. | **Shape.** ADR-0035's bar is already the repo's own: *"no parity violation; consumers `except AgentHarnessError` continue to work."* Under it, restoring pi's documented `success:false` is not a violation — the current behaviour is. Say so by name in the closure ADR. |
| **3** | **Is the ACP adapter a future in-process dialect, or a separate out-of-process binary/extension?** This decides whether C-minimal's seam is a stepping stone or a dead end. | **Out-of-process, `pi-acp` shape.** `RpcChannel` is already a subprocess boundary so the extra hop is free, it touches zero product-core lines, and it imposes no permanent tax. Keep the seam anyway — it is 20 lines and it makes any future side-by-side comparison testable. |
| **4** | **Move the ADR-0034 pin from `734e08e` to `v0.80.2`?** MEASURED: across ~6 minor versions the rpc wire gained **one optional field** (`bash.excludeFromContext`) and one id-echo bugfix — the cheapest pin move this project will ever get. But the pin is **global**, so it re-anchors every other surface's citations, and that cost was never measured. | **No, not this sprint.** Note it as a cheap follow-up in the ADR. Do not couple a global re-anchor to a sprint that already carries a kernel edit and a gate amendment. |

---

## 7. SEQUENCING

**This sprint** (steps 1-6 of §5): `entry.py` startup fix + model-registry wiring · real-child rpc
bed · terminator + preflight-ack + images + non-silent `extension_ui_response` (all parity
restorations) · **`RpcChannel` + cross-channel parity test on the pi dialect** · the inert `PiDialect`
seam with its identity proof and one-dialect gate · two ADRs including the pi-citation correction.
If time runs out after step 4, the sprint still closes with both owed deliverables shipped and
nothing half-migrated in `main` — that property is why this ordering, not A's §9 ordering (which puts
the protocol work first and the deliverable second), is the one to plan from.

**Next sprint:** the child→parent approval back-channel — `extension_ui_request` emission, a
server-side `srv_`-prefixed id space and pending map, a client-side inbound dispatcher, and
`extension_ui_response` handling. All four are additive and none touches the transport (§2.3). Pair
it with ADR-0197 §(i) blocker (c) — the parent TUI's modal arbiter — and ADR-0197 residual R3
(options pinned visible), because they are the same code. **Every new dialog field goes through the
sanitiser; the child never chooses `kind`.**

**Later, in this order:** error `code` symbols using the recorded JSON-RPC band → the D6 casing
declaration + machine gate (verify each payload against pi first, §4.2) → wire version/capability
record if and when a second consumer exists → an **out-of-process ACP adapter** driving
`aelix --mode rpc`, which is where the interop actually lives and which never needs the wire to change
→ ADR-0034 pin move to a live upstream tag.

**Not on the roadmap:** JSON-RPC or ACP as the aelix rpc wire, in one grammar or two.
