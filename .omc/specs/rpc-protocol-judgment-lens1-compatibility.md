# LENS 1 JUDGMENT — Compatibility & Obligation (A vs B vs C)

> **Written:** 2026-07-29, RPC-sprint decision phase. Tree at `main` = `da61337`. **Nothing edited.**
> Scored under ONE lens only: *what does each option break, and what promise does it violate?*
> Cost/effort, technical elegance, and strategic interop are OTHER lenses and are NOT scored here.
>
> **MEASURED** = I ran the command or opened the file at `da61337`, or fetched the pi source myself.
> **INFERRED** = reasoning on measured facts, labelled inline.
>
> Every designer claim below that carries an option's argument was **re-verified against the tree**
> or against pi source I fetched. Where a designer overstated, it is said so by name.

---

## 0. VERDICT

**C wins this lens, but only in its own §8 "C-minimal" form. A and B are close behind for
opposite reasons: A's breakage is bounded but real and self-undercounted; B's breakage is
genuinely zero but its promise is the heaviest of the three.**

| Option | Lens-1 score | One-line reason |
|---|---|---|
| **C** | **7.5** | C-minimal touches zero pin asserts, adds zero non-pi records, and makes every semantic change a *convergence* toward measured pi behaviour. But the document ships two contradictory versions, and C-**full**'s premise ("freeze = parity") is measurably false. |
| **A** | **6.0** | Bounded, enumerable, ADR-able breakage — but it is the ONLY option that actually amends the pi pin, it undercounts that amendment by 40%, it must edit the pi@734e08e fixture under a header named *"Pi fixture immutability"*, and it diverges gratuitously where converging is free. |
| **B** | **5.5** | Measured break count really is ZERO (verified). But it asks the owner to sign a **permanent** promise to maintain a wire the repo has itself documented as defective, and it concedes its own ADR-0103 compliance is "half a lawyer's reading". |

---

## 1. THE OBLIGATION LANDSCAPE — measured, not assumed

### 1.1 External obligation: **ZERO**. Verified on every clause.

MEASURED at `da61337`, all three commands run by me:

```
git tag -l                      -> 0 tags
git ls-remote --tags origin     -> empty
gh release list                 -> empty
```

- `aelix-web` **does not exist**: `gh repo view handochan/aelix-web` → `GraphQL: Could not resolve
  to a Repository`. `/workspaces` lists 8 entries, all `aelix-ai` worktrees plus `aelix-marketplace`
  — **no `aelix-web`.** (MEASURED.)
- `packages/aelix-server` is deleted from the publish set **by its own release workflow**:
  `.github/workflows/release.yml:82-87`, a step named *"Drop aelix-server artifacts (excluded from
  v1 publish set)"* running `rm -f dist/aelix_server-*.whl dist/aelix_server-*.tar.gz`.
  `packages/aelix-server/README.md:7-9` says the same in prose. (MEASURED, both re-opened.)
- **The stdio server has never run.** I reproduced the startup failure myself at `da61337`:
  ```
  $ printf '{"id":"r1","type":"get_state"}\n' | python -m aelix_coding_agent --mode rpc
  File ".../cli/entry.py", line 2160, in _async_main
      await run_rpc_mode(
  File ".../rpc/rpc_mode.py", line 1911, in run_rpc_mode
      raise RuntimeError
  RuntimeError: repo and fs must not be supplied when runtime_host is explicit — the runtime owns them
  ```
  Zero bytes reach stdout. **No user could have written a client against this path even if a
  release existed.** (MEASURED — I ran it.)

### 1.2 Public promise: a **shape**, and nothing more

Re-opened verbatim (MEASURED):

- `README.md:50-52` — *"`--print`, line-delimited `--mode json`, and a `--mode rpc` JSONL protocol
  make aelix embeddable in pipelines, CI, and evaluation loops"*.
- `docs/guides/getting-started.md:74` — `| aelix --mode rpc | headless | JSONL command/response
  protocol on stdio |`.
- The **only** `pi` mention in `README.md` is `:205-212`, inside a section headed *"License &
  attribution"*: *"Substantial portions of Aelix are a TypeScript-to-Python port of pi (reference
  commit `734e08e`), Copyright © 2025 Mario Zechner, MIT licensed."*

**No public artefact promises pi wire compatibility.** All three options preserve
*"a JSONL command/response protocol on stdio"* verbatim. **This clause does not discriminate.**

### 1.3 The obligation that actually exists is INTERNAL and self-imposed

| Artefact | What it binds | Verified |
|---|---|---|
| `docs/decisions/0103-sprint-6h9f-aelix-server.md:7-8` | *Top-level principle (binding):* **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."** | MEASURED, re-opened |
| `docs/decisions/0058-…:196-201` | *"the durable RPC boundary every multi-language client routes through"*; *"a Pi-shaped client that dispatches on the `BashResult` 4/5-key shape works against Aelix today"* | MEASURED |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` + `fixtures/pi_rpc_mode_734e08e.json` | the ONLY artefacts asserting pi compatibility of the wire | MEASURED |
| `docs/decisions/0035-error-code-taxonomy.md` | **the bar** | MEASURED, quoted below |

**The bar, verbatim (ADR-0035, re-opened):**

> **Aelix-additive divergence**: Pi has no `"aborted"` harness code at SHA `734e08e`; Aelix raises
> it from `abort()` … This is the single intentional divergence from Pi's 9-code taxonomy and is
> documented as additive (**no parity violation; consumers `except AgentHarnessError` continue to
> work**).

The test is **not** "does pi do it" — it is **"does a pi-shaped consumer still work unchanged?"**

Vocabulary counts, MEASURED by `grep -rl … docs/decisions/ | wc -l`:
`Aelix-additive` **51 files** · `aelix-original` **14** · `documented divergence` **8**.
Divergence is a well-trodden, ADR-able path in this repo. **This is not a wall; it is a form to fill in.**

### 1.4 THE INVERSION — the terminator defects are aelix regressions, and I verified it in pi source

I fetched pi at the pin (`734e08e…`) myself and read `packages/agent/src/harness/agent-harness.ts`
(995 lines):

- `:512-524` `emitRunFailure(model, error, aborted, signal)` emits the **full four-event closure**:
  `message_start → message_end → turn_end → agent_end` (the `agent_end` is at `:522`).
- `:568-575` — the agent loop is wrapped in a **bare `catch (error)`** that routes *everything*,
  abort included, into `emitRunFailure(…, abortController.signal.aborted, …)`.
- `:49-56` `createFailureMessage` sets `stopReason: aborted ? "aborted" : "error"`.

aelix, `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (MEASURED, exact lines):

- `:4287` `except asyncio.CancelledError:` … `:4293-4294` `if self._abort_requested: return []`
  — sits **inside** the same `try` and returns before the closure block can run.
- `:4298` `except AgentHarnessError as exc:` — narrower than pi's bare catch.
- `:4299` the block's own comment: *"Pi parity: synthesize failure assistant message + emit closure
  events."* **The correct code is already in the file; two paths bypass it.**

**Consequence that reorders this whole lens:** fixing the terminator is a **convergence** toward pi,
not a divergence. Any option that fixes it *outside* the harness is choosing a divergence over an
available convergence. That inverts the kickoff handoff §3's framing and it is the single most
load-bearing fact for Lens 1.

I also confirmed the pi recon's §0.2 correction directly: pi's `prompt` case at
`074-rpc-mode.ts:379-401` emits `output(success(id,"prompt"))` **only from
`preflightResult(didSucceed)`**, and on failure `.catch(e => { if (!preflightSucceeded)
output(error(id,"prompt",e.message)) })`. **There is no synthetic terminal event anywhere.** The
comment at `rpc_mode.py:316-320` that has justified this work item for six sprints is false.

---

## 2. OPTION A — "does it truly preserve parity, or merely claim to?"

**It merely claims to.** A calls itself *"`aelix rpc wire v2` — a strict superset of the pi-shaped
v1"* (§0). Measured, it is not.

### 2.1 A amends the pi pin in **5 asserts**, not the 3 it counted

MEASURED: `rpc_types.py:814` — `RPC_COMMAND_TYPES: frozenset[str] = frozenset(_RPC_COMMAND_REGISTRY.keys())`.
So A's 30th command `hello` grows `RPC_COMMAND_TYPES` **automatically**, tripping every assert that
reads it. In `tests/pi_parity/test_phase_4_4_strict_superset.py`:

| line | assert | counted by A §5.3? |
|---|---|---|
| `:61` | `pi_types == RPC_COMMAND_TYPES` | ✅ yes |
| `:89` | `len(RPC_COMMAND_TYPES) == 29` | ✅ yes |
| `:90` | `len(SUPPORTED_COMMANDS) == 29` | ✅ yes |
| `:346` | `fixture["rpc_command_count"] == len(RPC_COMMAND_TYPES) == 29` | ❌ **MISSED** |
| `:391` | `set(PI_COMMAND_FIELDS.keys()) == set(RPC_COMMAND_TYPES)` | ❌ **MISSED** |

The two missed ones are the expensive ones:

- **`:346` sits under the section header `# === §G — Pi fixture immutability ===`** (MEASURED,
  `sed -n '320,350p'`). Its docstring: *"A future PR that adds a variant must increment the fixture
  in the same change."* The fixture is `pi_rpc_mode_734e08e.json`, whose `_doc` reads
  *"Pi-pinned snapshot of RPC mode surface at SHA 734e08e"* and whose `rpc_command_count` is `29`
  (MEASURED, loaded and printed). **Option A must edit the pi snapshot** — the one artefact the repo
  declares immutable and the pin's docstring calls *"the authoritative wire surface"*.
- **`:391`** guards `PI_COMMAND_FIELDS`, documented at `:381-384` as *"Each Pi `RpcCommand` variant
  has a known field roster (`rpc-types.ts:19-69`)"*. A must add a **non-pi row to a table of pi's
  rosters**.

A's `tests/rpc/test_rpc_mode_dispatch.py:45` claim ("flows through automatically") is **correct** —
verified, it is `assert set(table.keys()) == RPC_COMMAND_TYPES`.

### 2.2 Four further divergences A creates, three of them named by A itself

1. **`server_hello`, an unsolicited first line.** pi's stream begins only in response to a command
   (verified: 4 `_output` sites in `rpc_mode.py`, none a header; pi has no greeting either). A
   measured 3 WS tests break; A calls it *"the single most contentious addition"* (§3.5).
2. **`stopReason:"cancelled"` instead of pi's `"aborted"`.** A picks ACP's vocabulary (§6.2) on the
   exact field where I measured pi's value to be free to match (`agent-harness.ts:49-56`). **This
   divergence is gratuitous** — converging costs one string literal.
3. **The terminator lands in `rpc_mode.py`, not the harness.** Forced by the "kernel is never
   edited" scoping. Result: a *second* implementation of turn closure with an explicit de-sync
   guard (A §3.2), `--mode json` and the TUI still un-terminated, and `stop_reason` still hardcoded
   `"error"` at `core.py:4302`. A's own §8.3 states this and calls the result *"worse"*.
4. **`_meta` on every server→client record** makes rpc events diverge from `--mode json` events for
   the first time, on the sprint that first writes the cross-channel parity test (A §8.4). A
   pi-shaped `.get()`-based client survives (verified: `rpc_client.py` reads by `.get()`), so this
   one **passes the ADR-0035 bar** — but it breaks aelix's own internal consistency.

### 2.3 A designer claim that is factually wrong — and I checked it because A asked me to

A §3.6 item 2 (and the wire recon §2.B it copied from) says **three** `data` payloads are snake_case
— `get_messages`, `compact`, `get_session_stats` — and proposes flipping all three to camelCase as a
probable "parity restoration". A §10.1 flags it as INFERRED and says it *"must be verified against
`rpc-mode.ts` at the pin before shipping."*

**I verified it. One of the three is wrong.**

- `get_session_stats` is **already Pi-camelCase**. `rpc_mode.py:1123-1172` is a hand-written
  `_session_stats_to_dict` emitting `sessionId` / `userMessages` / `assistantMessages` / `toolCalls`
  / `toolResults` / `totalMessages` / `tokens.{input,output,cacheRead,cacheWrite,total}` / `cost`,
  plus optional `sessionFile` / `contextUsage`. Its docstring says *"serialize `SessionStats` to the
  Pi camelCase wire shape (`agent-session.ts:212-223`)"*. I fetched pi's `agent-session.ts:209-226`
  and it matches **key for key**. `rpc_mode.py:1194-1195` calls that helper, not `asdict`.
  **Nothing to fix; A budgets a change that would be a regression.**
- `compact` (`rpc_mode.py:470` `_dataclass_to_dict(result)`) and `get_messages`
  (`rpc_mode.py:448-450`) genuinely are `asdict` passthroughs. pi returns `result` and
  `{ messages: session.messages }` verbatim (verified at `074-rpc-mode.ts:511-513`, `:614-616`), so
  camelCase is *likely* right — but A's blanket rule would make the same message object **camelCase
  in a `get_messages` response and snake_case in a `message_start` event**, a new internal
  inconsistency A does not address.

### 2.4 What A does NOT break

External users (zero), `aelix-server` source (0 lines — the WS bridge is byte-transparent,
`rpc_ws.py:115-120` / `:136`), `aelix-web` (does not exist), `README.md` / `getting-started.md`
(both stay literally true). **All three options score identically here.**

**Answer to the brief's question (b): Option A does not preserve parity. It preserves the *external*
promise (which is empty) and breaks the *internal* pin harder than any other option, while
undercounting that breakage.**

---

## 3. OPTION B — the ZERO-break claim is TRUE; the promise is the heaviest

### 3.1 The mechanism checks out

B §4.1 claims dual-speak breaks **zero** pi-parity asserts. Verified structurally:

- `RPC_COMMAND_TYPES` derives from `_RPC_COMMAND_REGISTRY` (`rpc_types.py:814`). A second grammar
  keyed on JSON-RPC `method` strings adds **no registry entry**, so `RPC_COMMAND_TYPES` stays 29 and
  `:61` / `:89` / `:90` / `:346` / `:391` all hold.
- `PI_COMMAND_FIELDS` (`:355-390`) asserts `cls.__dataclass_fields__` per command. Dual-speak edits
  **no existing dataclass**, so `:401-408` holds.
- `grep -rn "jsonrpc" --include=*.py packages/ src/` → **0 hits** (MEASURED, re-run by me). The two
  grammars are **provably disjoint on one key**, so the latch is unambiguous.

**B's headline compatibility claim is the strongest measured claim any designer made, and it holds.**

### 3.2 …and it is bought with the heaviest obligation of the three

- **B §5.5, its own words:** *"It does not [die]. Not under the current principle… Budget for two
  grammars forever; treat 'one day we delete it' as wishful."*
- **The pi half must preserve its defects**, because the fixtures pin them. B §8.3 and C §4.3 both
  say this; I verified two of the frozen defects against pi source in §4.2 below.
- **B §4.2 concedes its own principle-compliance is half a lawyer's reading**: *"a reader of
  ADR-0103 would not recognise a two-grammar server as 완전 동일하게."* I re-opened ADR-0103:7-8 and
  agree — the principle is stated as a *binding top-level principle*, not a guideline.
- **B §8.1 measures its own Tier-1 dividend at zero interop.** Under an obligation lens, a permanent
  2× maintenance promise bought for zero external compatibility gain is the worst trade on offer.

---

## 4. OPTION C — best variant, worst ambiguity

### 4.1 C-minimal (C §8) is the cleanest lens-1 proposal in the set

Zero pin asserts touched. Zero new commands. Zero new records on the wire. And critically: **every
semantic change it makes is a convergence toward measured pi behaviour** —

| C-minimal step | pi behaviour it converges to | verified |
|---|---|---|
| preflight seam on `_handle_prompt` | `session.prompt({…, preflightResult})` → `output(success(id,"prompt"))` only on success | ✅ `074-rpc-mode.ts:382-394` |
| correlated prompt error response | `.catch(e => output(error(id,"prompt",e.message)))` | ✅ `074-rpc-mode.ts:395-399` |
| kernel terminator (`core.py:4287-4298`) | `emitRunFailure` → 4-event closure incl. `agent_end`, from a **bare** `catch` | ✅ `agent-harness.ts:512-524`, `:568-575` |

Plus the one structural move nothing else offers: **`PiDialect` as a provable no-op turns "we did
not change the pi wire" from a promise into a machine-checked invariant** (golden byte comparison +
identity assertion). Under an obligation lens, converting a prose promise into a gate is exactly the
repo's own established pattern — the strict-superset pin drained `DEFERRED_COMMANDS` 7→0 across
sprints precisely because it was mechanical.

### 4.2 C-full's premise is measurably FALSE — and C says so itself (§7.3), correctly

"Freeze the pi wire for parity" freezes a wire that is **already divergent from pi**. I verified two
of C's five claimed divergences directly against pi source:

- **Busy rejection.** pi: `output(success(id,"prompt"))` fires **only** from
  `preflightResult(didSucceed=true)` (`074-rpc-mode.ts:386-393`). aelix: `RpcSuccessResponse`
  returned unconditionally at `rpc_mode.py:330`, **before** the task at `:322` runs.
  **Already divergent.**
- **`prompt` drops images.** pi: `session.prompt(command.message, { images: command.images,
  streamingBehavior: command.streamingBehavior, … })` (`074-rpc-mode.ts:382-386`). aelix:
  `harness.prompt(cmd.message, source="rpc")` at `rpc_mode.py:309` — both dropped.
  **Already divergent.**

And the pin measures **neither** — it measures the command roster and the `RpcSessionState` field
set. So C §7.3's conclusion is verified: *"the freeze buys fixture stability, not parity."*

### 4.3 The ambiguity that costs C points

The document is titled DUAL-STACK, its §5 has `RpcChannel` speak the standard dialect (C-full), and
its §8 recommends the opposite (C-minimal, `RpcChannel` on the pi dialect). **The two halves have
opposite Lens-1 profiles** — C-minimal ≈ 9, C-full ≈ 5 (same forever-2× obligation as B, plus a
*new public `--mode` value* added to a README that already advertises `--mode rpc`). A decision
taken on "Option C" without naming the variant would be undefined.

---

## 5. THE pi-PARITY OBLIGATION — is it load-bearing HERE?

**Partly, and not in the direction the kickoff handoff assumed.**

- **Load-bearing YES** for the *pin*: 5 asserts in one file + 1 immutable fixture + `PI_COMMAND_FIELDS`.
  Only Option A trips them.
- **Load-bearing NO** for the *defects*: I verified pi terminates aborted and failed runs. Fixing
  aelix's terminator is convergence. **So "keeps parity" cannot be used to argue against fixing it,
  and cannot be used to argue for fixing it in `rpc_mode.py` rather than the harness.**
- **Load-bearing NO** externally: nothing shipped, nothing promised beyond a shape.
- **The bar is ADR-0035's**, verified verbatim: *does a pi-shaped consumer still work unchanged?*
  Under that bar A's `_meta` and error `code` pass; A's `server_hello` and the `get_messages`
  casing flip are the two that a naive raw-frame reader would notice.

### 5.1 The upstream-sync epic (#54) — measured rpc churn

Epic **#54 "[epic] pi upstream 동기화 추적 — v0.74.1 → v0.80.2 (646 commits)"** exists (MEASURED,
`gh issue list`). I re-diffed the four rpc files myself at both SHAs:

| file | changed lines | substantive |
|---|---|---|
| `jsonl.ts` | **0** | none |
| `rpc-types.ts` | 10 | **one** optional field: `bash` gains `excludeFromContext?: boolean`; rest is `.js`→`.ts` import churn |
| `rpc-mode.ts` | 44 | stdout backpressure, `shutdown(exitCode, signal)`, `mode:"rpc"` in `bindExtensions`, `excludeFromContext` forwarding, and the **id-echo bugfix** `error(undefined,…)` → `error(id,…)` |
| `rpc-client.ts` | **83** | process-exit / stdin-error propagation + `rejectPendingRequests` — **client-side robustness, no wire-shape change** |

**The pi recon's §3 table omits `rpc-client.ts` entirely** even though its own §8 repro script
fetches it. Its conclusion *"the upstream-sync epic toward v0.80.2 carries essentially no rpc risk"*
**holds for the wire** (one optional field + one bugfix, exactly as claimed) but is **overstated for
the package** — there is a real ~40-net-line client port owed. This does not change the lens-1
ranking: **none of the three options makes that sync meaningfully harder**, because the delta is
additive and touches shapes none of them freeze.

---

## 6. SCORING

| Option | Score | Fatal flaw under THIS lens |
|---|---|---|
| **C** | **7.5** | The document ships two versions with opposite obligation profiles. C-**full**'s stated premise — freeze the pi wire *for parity* — is measurably false (busy-ack and images-drop are already divergences from pi, verified against pi source), so only C-minimal survives this lens and the decision must name the variant. |
| **A** | **6.0** | It is scoped by "the kernel is never edited", which forces turn closure into `rpc_mode.py` — but pi puts it in the harness (`emitRunFailure`, verified). **A therefore spends its parity budget building a divergence to avoid an available convergence**, while simultaneously being the only option that amends the pi pin (5 asserts, 2 uncounted, one requiring an edit to the pi@734e08e fixture under a header named "Pi fixture immutability"). Its self-description as "a strict superset of the pi-shaped v1" is not achieved. |
| **B** | **5.5** | Its zero-break claim is true and verified — but it is bought by asking the owner to sign a **permanent** promise to maintain a pi grammar *whose defects the fixtures pin*, for a Tier-1 dialect B itself measures as buying zero interop. B §4.2 concedes its ADR-0103 compliance is half a lawyer's reading; B §5.5 concedes the pi dialect never dies. |

**Margin:** C > A by ~1.5, A > B by ~0.5. **The gap is narrow and variant-dependent**: C-minimal vs
C-full swings C by ±2.5, which is larger than the gap between all three options. **Under this lens
the decision that matters is not A/B/C — it is (i) may the kernel be edited, and (ii) which C.**

---

## 7. WHAT THE OWNER MUST DECIDE (lens-1 framing only)

1. **May a sprint edit `harness/core.py:4287-4298` to restore a pi behaviour the file already claims
   to implement?** If YES, C-minimal is clean and A's central justification evaporates. If NO, every
   option builds a divergence, and A's should at least use pi's `"aborted"` vocabulary rather than
   ACP's `"cancelled"`.
2. **Is `RPC_COMMAND_TYPES` amendable?** Only Option A needs this. If the answer is "the pi fixture
   is immutable", Option A needs redesign (a `hello`-free handshake, e.g. an additive `_meta` on the
   existing records) — not a waiver.
3. **If C: which C?** C-minimal and C-full are different proposals under this lens.

---

## 8. UNCERTAINTIES — what I did NOT verify

1. I did not run any test suite. Pin-assert breakage under Option A is derived from
   `RPC_COMMAND_TYPES = frozenset(_RPC_COMMAND_REGISTRY.keys())` plus reading the 5 asserts, not
   from executing a mutated tree.
2. I did not enumerate which of the ~190 `tests/rpc` tests break under any option; I characterised
   the pi_parity bucket precisely and the rest not at all.
3. `CompactionResult`'s TS field casing was not fetched (only that pi returns it verbatim), so
   Option A's `compact` camelCase flip remains INFERRED — as A itself says.
4. I did not search for third-party pi rpc clients outside GitHub, so "no external consumer" is
   absence-of-evidence for that class.
5. ACP's `StopReason` value set was taken from the standards recon, not fetched.
6. Whether widening `except AgentHarnessError` at `core.py:4298` is side-effect-free was not
   assessed — it is a kernel edit needing its own blast-radius review.
