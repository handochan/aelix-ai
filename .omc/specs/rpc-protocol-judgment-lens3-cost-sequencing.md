# RPC protocol judgment — LENS 3: COST, BLAST RADIUS AND SEQUENCING

> **Written:** 2026-07-29. Tree at `main` = `da61337`. **Nothing was edited** (one probe edit to
> `harness/core.py` was made, measured, and reverted — see §2.3; `git status` clean after).
> Every `path:line` re-opened against `da61337`. **MEASURED** = I ran it or opened it.
> **INFERRED** = reasoning on measured facts.
>
> This note scores ONLY cost / blast radius / sequencing. Compatibility (lens 1) and
> defect-closure (lens 2) are deliberately not scored here.

---

## 0. Verdict

| Option | Lens-3 score | One line |
|---|---|---|
| **A — bespoke, fixed** | **7.0 / 10** | Ships the deliverable but sequences it **second**. Its own §10.3 never counted the `tests/rpc` blast radius — I counted it, and **A got lucky: it is near zero**. Docked instead for a pin amendment it undercounts (6 sites, not 4), for creating the parity test's divergence, and for poor back-out. |
| **B — standard-native (JSON-RPC/ACP)** | **5.0 / 10** | Its §7.3 slice plan is the single best sequencing artefact in the three notes and it is honest that it is two sprints. But it self-refutes in §8: by its own measurement the delta over A buys zero interop in slice 1 — and its load-bearing "zero break" claim was asserted without running the suite. |
| **C-minimal (§8)** | **8.0 / 10** | Smallest measured product-core delta, cheapest back-out, ships the deliverable **first** on the pi dialect, creates no channel divergence, and installs a gate. **C-full (§1-§6) scores 3.0** — it builds a compat layer for a measured population of zero. |

**Lens-3 winner: Option C, and specifically C-minimal (§8), by ~1.0 over A.** The margin narrowed
once I measured that A's biggest unquantified risk is small. What is left is entirely *back-out cost*
and *what sits in main half-done* — and on both, C-minimal wins cleanly. If the owner will not pay
for the `dialect:` seam, **A resequenced (deliverable first) is a strong second**. B is third for
this sprint — its own §8.8 counter-proposal is "do A now", which is a designer conceding the lens.

---

## 1. The measured cost baseline (all MEASURED at `da61337`)

### 1.1 The rpc surface — true size

```
packages/aelix-coding-agent/src/aelix_coding_agent/rpc/
  __init__.py      147
  _jsonl.py        117
  rpc_client.py    602
  rpc_mode.py     2142
  rpc_types.py     875
  ------------------- 3883 source lines
packages/aelix-server/src/aelix_server/rpc_ws.py   159   (whole package: 381)
```

Tests over that surface:

| Suite | files | `def test_` | **collected** |
|---|---|---|---|
| `tests/rpc/` | 30 (+`__init__`) | 188 | **190** |
| `tests/pi_parity/` (all) | 14 | 386 | **418** |
| `tests/pi_parity/` (6 rpc-touching: 4_3, 4_4, 4_9, 4_11, 4_12, 4_13) | 6 | **152** | — |
| `tests/server/` | 1 | 15 | **17** |
| `tests/agents/` | — | — | **92** |
| **rpc blast-radius total** (`tests/rpc` + `tests/pi_parity` + `tests/server`) | — | — | **625** |

`tests/rpc` = 5470 lines; the 6 rpc pi_parity files ≈ 2600 lines. **MEASURED** via
`pytest --collect-only` and `grep -c 'def test_'`.

**Corrections to the designers' numbers:**
- Option B **F10 is exactly right** (152 across those 6 files: 14+20+28+26+27+37). Its F11 ("31 files,
  188 def test_") is right on `def test_`; the collected count is 190.
- Option C §4.2's "190 collected / 155 pi_parity across 6 files" — 190 is right; **155 is wrong**,
  the 6 files collect from 152 `def test_` and the whole `tests/pi_parity` dir is 418. C's "345
  tests" total therefore under-counts the true rpc blast radius (625).
- `aelix_agents/` (the extension band) is **8941 lines across 17 modules** — the new `rpc_channel.py`
  lands here and `print_channel.py` (1114 lines) is the template it mirrors.

### 1.2 The band gate — what it actually is, and what trips it

`tests/agents/test_p2_band_boundaries.py` (499 lines, **7 tests, 7 passed at `da61337` — MEASURED**).
It is **four** distinct scans, not two, and the fourth is the one every designer under-weighted:

| Test | Mechanism | What trips it |
|---|---|---|
| `test_kernel_has_no_subagent_surface` :188 | **TEXT** scan of `aelix-agent-core/src` for `subagent` (case-insensitive) + `aelix_agents` | a kernel *comment* mentioning subagents |
| `test_kernel_untouched_vs_merge_base` :214 | `git diff` merge-base(origin/main,HEAD) + `git status --porcelain` over `packages/aelix-agent-core` | **ANY kernel byte, including a blank comment line** |
| `test_product_core_never_spawns` :247 | **AST** scan for `create_subprocess_exec`/`Popen`/`os.fork*`; `_SPAWN_ALLOWLIST` = `rpc/rpc_client.py`, `tools/` (:46-49) | a spawn call in a *new* product-core file |
| `test_product_core_never_prompts_for_spawn_consent` :304 | TEXT scan for `SpawnGrant`, `request_spawn_consent` | consent policy in product-core |
| `test_product_core_declares_only_the_allowlisted_caps` :350 | AST scan; `_CAP_NAME_RE` = `(^\|_)(MAX\|MIN)_\|_(CAP\|LIMIT\|BUDGET\|CEILING\|TIMEOUT\|MS\|BYTES\|TASKS\|CHILDREN\|CONCURRENCY)$`, **EXACT SET both directions** (:390-406) | a new cap-named module constant in product-core |
| `test_the_seam_declares_exactly_its_contract_constants_and_nothing_more` :428 | exact set on `subagent_contract.py` | any new public constant in the seam |
| `test_the_p3_cap_names_never_appear_in_product_core` :461 | AST binding scan for the 9 P3 caps | a P3 cap moving down a band |

**PROVEN, not inferred (§2.3):** appending a bare `# band-gate probe` comment to
`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` turns
`test_kernel_untouched_vs_merge_base` **RED** — `AssertionError: the kernel must not change in P2`.
Reverted immediately; tree clean.

**Consequence for sequencing, and it is the sharpest one in this note:** *any* option whose plan
includes the kernel terminator fix (`harness/core.py:4287-4298`) turns the band gate red **for the
whole life of the sprint branch**, and can only merge by amending the gate. That is a deliberate,
reviewable act — which is the gate working — but it is a schedule cost nobody priced.

**Cap-regex landmines nobody named:** a product-core constant called `MAX_LINE_BYTES`,
`STREAM_LIMIT_BYTES`, `DEFAULT_HELLO_TIMEOUT_MS`, `MAX_PROTOCOL_BYTES` or anything ending
`_MS`/`_BYTES`/`_TIMEOUT` trips :350 on arrival. Option A §3.7 (per-line budget in `_jsonl.py`) is
the exact shape that would reach for one — A's written design dodges it by using an
`__init__` parameter defaulting to `None`, but the obvious implementation does not. **This is a
one-line-of-review trap in all three options; only A's text is accidentally safe.**

### 1.3 The prerequisite all three share, and it is ~2 lines

**MEASURED — I ran it:**

```
$ echo '{"id":"a1","type":"get_state"}' | python -m aelix_coding_agent --mode rpc
...
  File ".../cli/entry.py", line 2160, in _async_main
    await run_rpc_mode(
  File ".../rpc/rpc_mode.py", line 1911, in run_rpc_mode
    raise RuntimeError(
RuntimeError: repo and fs must not be supplied when runtime_host is explicit — the runtime owns them
```

`entry.py:2157-2167` passes `runtime_host=runtime` **and** `repo=repo, fs=fs`; `rpc_mode.py:1906-1914`
rejects exactly that combination. **Zero bytes reach stdout. The stdio rpc server has never run.**

All three designers assert this (A §5.1, B F1/§6.6, C §7.4). **All three are correct.** It is the
first commit of any plan and it costs ~2 lines. It is also the reason `tests/rpc` never caught it:
every client test replaces `_build_argv` with a `python -c` stub.

---

## 2. Per-option cost, blast radius, sequencing

### 2.1 Option A — bespoke, fixed

**Files touched (from A §4, re-checked):** 5 product-core files (`rpc_types.py`, `rpc_mode.py`,
`_jsonl.py`, `rpc_client.py`, `cli/entry.py`) + 4 extension files + 1 new extension file + 2 amended
test files + ~7 new test files + 1-2 ADRs. Call it **≈20 files**.

**Blast radius A measured vs. what I measured:**

| A's claim | Verdict |
|---|---|
| "3 exact-dict `to_json()` asserts stay green because `_meta` goes in `_output`, not `to_json`" (`test_rpc_types.py:114/:123/:134`) | **CONFIRMED** — those three are `resp.to_json() == {...}` and `_output` is a different site. Good design call. |
| "`server_hello` breaks 3 WS tests at `tests/server/test_server.py:129-137, :150-153, :166-174`" | **CONFIRMED in substance, line numbers drifted.** The three are `test_rpc_round_trip` (:126), `test_rpc_single_flight` (:142), `test_rpc_active_flag_reset_after_close` (:156). All do `send_text` → `receive_text()` → assert `["type"]=="response"`. The third opens **two** connections so it needs **two** extra drains, not one. |
| "amending the pi pin = `:52-62` set-equality + 3 counts at `:89-91`" | **UNDERSTATED.** MEASURED: `:61` set-equality, `:89`, `:90`, `:91` counts, **`:346`** (`fixture["rpc_command_count"] == len(RPC_COMMAND_TYPES) == 29`) and **`:391`** (`set(PI_COMMAND_FIELDS.keys()) == set(RPC_COMMAND_TYPES)`, which forces a new 30th field roster at `:355`). Plus `tests/rpc/test_rpc_mode_dispatch.py:45`. **Six assertion sites + a roster entry, not four.** |
| §10.3 "I did not enumerate which of the 190 `tests/rpc` tests break" | **A's own honest gap — and I counted it for them. A got lucky, and it is design luck, not chance.** MEASURED: **34 `== {` exact-dict assertions across 14 of the 30 `tests/rpc` files**, and **8 files read back the emitted stream** (`test_rpc_mode_event_pipe/_rebind/_runtime_shim/_stdin_stdout/_steer_follow_up`, `test_w6_regressions`, `test_w6_regressions_6f`, `test_rpc_client_shutdown`). **The intersection of those two sets is empty**: the only `== {` in a stream-capturing file is `test_rpc_mode_runtime_shim.py:260` `DEFERRED_COMMANDS == {}`, which is unrelated. Every exact-dict assert is on `resp.to_json()` or `response.data`, never on an emitted record. Same on the parity side — `test_phase_4_13:248/:275/:290/:305` and `4_9:310` are all `response.data == {...}`. **So `_meta` in `_output` breaks ~0 existing tests, exactly as A's §3.1 design intended.** Putting it there rather than in `to_json` was the right call and it is measurably right. |

**Does the deliverable ship?** Yes, and A is straight about the ordering (§9): *"(1) protocol v2 + its
conformance bed, (2) `RpcChannel` + parity test"* — deliverable **second**. It also honestly says the
sprint "grows by roughly one third" and names the risk that protocol work crowds out channel work.

**Sliceability: good but backwards.** A's own hard ordering puts the risky half first. A better slice
exists inside A's own material and A does not offer it: `entry.py` fix → real-child bed → terminator
fixes (§3.2/§3.3) → **RpcChannel + parity test** → then `_meta`/`hello`/`server_hello`/codes. A gets
docked for shipping an ordering that banks the deliverable last.

**Half-migrated risk: MEDIUM.** A's §8.4 self-criticism is correct and material: `_meta` on rpc
events and not on json events **creates** the first divergence between the two channels *on the same
sprint the parity test is written*, so the test must strip a field to compare. If the sprint runs out
of time mid-way, main holds a wire that is half-`_meta`'d with a parity test that normalises six
asymmetries instead of four.

**Reversibility: POOR-to-MEDIUM.** Backing A out in six months means removing `_meta` from every
server record, deleting a 30th command from `RPC_COMMAND_TYPES` and re-tightening five/six parity
assertions, and un-relaxing `parse_rpc_command`. The pin amendment is the sticky part: A §8.5 admits
"once the pin is a superset it stops detecting an *accidental* addition". **You cannot un-widen a
gate; you can only re-narrow it and hope nothing slipped through meanwhile.**

**Score 7.0.** Ships. Sliceable if resequenced. Its biggest self-declared unknown turned out benign
(measured ~0 test breakage from `_meta`), which is a credit to the design; the remaining docks are
the undercounted pin amendment (six sites, not four), the deliverable landing second, the
divergence it creates for the parity test, and the one-way pin widening.

**A first slice that works, built from A's own material (this is what A should have offered):**
1. `entry.py:2160-2166` (~2 lines) — nothing is testable before it.
2. Real-child rpc bed.
3. `_TurnTracker` + preflight (§3.2/§3.3) + `prompt` images/`streamingBehavior` (§3.8) + the
   `_build_argv` `-m aelix` fix + the `RpcClientOptions` containment seam (§5.6).
4. **`RpcChannel` + cross-channel parity test — the deliverable, banked here.**
5. `_meta`, `hello`/`server_hello`, error `code`s, the casing gate, the `parse_rpc_command`
   relaxation, the pin amendment.
Steps 1-4 create **no** channel divergence and touch **no** parity pin. If step 5 slips, main holds a
clean tree. A's own §9 ordering does the opposite and should be rejected on this lens.

### 2.2 Option B — standard-native

**Files touched (B §7.2):** `cli/entry.py`, `rpc_types.py` (+~250), `rpc_mode.py` (+~300/−20),
`rpc_client.py` (+~200), new `aelix_agents/rpc_channel.py` (+~400), `stream.py`, `envelope.py`, 3+
new test files, 2 new docs. **B's §7.2 estimate is ~1150 new lines**, and B labels its own
`rpc_mode.py` figure "a shape estimate, not a sized one" (§9.9).

**The claim that carries B, and it does not survive:** *"Under dual-speak, the break count is ZERO"*
(§4.1/§4.2). **The claim is structurally plausible and I could not falsify it — but B's own §7.2
concedes `−20` lines in `rpc_mode.py` and a dialect latch inside `_on_line`.** MEASURED: `_on_line`
(`rpc_mode.py:2024-2073`) is directly exercised by 4 test files that capture `stdout_write`, and
`tests/rpc` alone is 190 tests over that path. "Zero break" is a claim about *dataclasses*, not about
`_on_line`. B never tested it — §9.6: *"I did not run the test suites."* **A designer asserting a
zero-break blast radius without running the suite is the exact failure mode this judgment exists to
catch.** Docked hard.

**What I did verify for B:**
- F2 (`grep -rn jsonrpc --include=*.py packages/` → **0 hits**): **CONFIRMED, re-run.** The two
  grammars really are decidable on one key.
- F3 (`rpc_types.py:423` `cls(**kwargs)` closes the command envelope): **CONFIRMED** by reading.
- F9 (`rpc_ws.py` byte-transparent): **CONFIRMED** — 159 lines, the only JSON-ish string is `"utf-8"`.
- F10 (152 `def test_` across 6 files): **CONFIRMED exactly.**
- F1 (`--mode rpc` raises): **CONFIRMED by execution** (§1.3).

**Does the deliverable ship?** B says yes and **B's §7.3 is the single best sequencing artefact in
the three notes** — a six-step order that banks `RpcChannel` + the parity test at **step 4**, before
the codec at step 5, with the explicit property *"if step 5 overruns, the sprint still closes with
the deliverable shipped."* That is exactly what this lens asks for and B is the only note that
volunteers it unprompted. It also states "**Two sprints minimum**" without hedging.

**But B refutes itself on cost/benefit.** §8.1: Tier 1 buys **zero interop** — "Zed cannot drive it.
JetBrains cannot drive it. No SDK targets it." Real interop is Tier 2, which §7.3 defers. §8.2: the
two defects that actually block the deliverable (D1 terminator, D2 early ack) are ~15 lines and get
fixed **in either grammar**. §8.3: the pi dialect never dies under ADR-0103, so this is
**two grammars × two spellings, forever**, and every future command is written and tested twice.
§8.8 ends with a counter-proposal that is *literally Option A*. **A design note whose §8 concedes the
lens is a design note that should not win the lens.**

**Half-migrated risk: HIGH — the highest of the three.** A partially-landed dialect latch inside
`_on_line` with a partially-populated method map is the worst thing that can sit in main: both test
suites green, two dialects silently drifting (B §8.3 names this tail risk itself).

**Reversibility: MIXED.** Additive-only code is easy to delete *if it stays additive*. The latch in
`_on_line` and the prompt-is-the-turn rewrite of `_handle_prompt` are not additive. And the
permanence is the point: §5.5 — *"It does not. Not under the current principle."* B is asking the
project to accept a **permanent** second maintenance surface for a slice-1 dividend it measures as
zero.

**Score 5.0.** Best sequencing plan, honest sizing, unverified zero-break claim, and a §8 that
argues for a different option.

### 2.3 Option C — dual-stack / layered

Two things are being scored, and they are very different.

**C-full (§1-§6): score 3.0.** ~6-9 files, "two-to-three sprints if the second dialect is real ACP",
and its own §7.2 destroys it: *"Option C's whole justification is backward compatibility for a
population of zero."* §4.2 — after this sprint the pi wire has "**345 tests and zero production
consumers**, permanently" (my count: 625, so worse than C says). §4.6 — nothing forces the old wire
to die. C-full is the only option that spends the sprint on a compatibility layer for nobody.

**C-minimal (§8): score 8.0, and it wins this lens.**

| Cost line | Verdict |
|---|---|
| `dialect:` parameter on `run_rpc_mode` + 3-5 call-site edits, ~20 lines product-core | **Call sites CONFIRMED exactly.** `_output` def at `:1879`; four call sites at `:1935`, `:2030`, `:2038`, `:2061` — `grep -n '_output('` returns exactly those five. `_on_line` at `:2024`, `json.loads` at `:2028`. C §1.1's pipeline map is **exact and still holds at `da61337`**. |
| `_handle_command` is dict-in / dataclass-out, `.to_json()` called by the caller | **CONFIRMED** — signature at `rpc_mode.py:1747-1751` is `(harness, payload: dict[str, Any], dispatch) -> RpcResponse`; `:2061` calls `response.to_json()`. So one dispatcher genuinely can serve two codecs. |
| band gate stays green (§1.5) | **CONFIRMED by construction and by running it.** A new `rpc/dialect.py` declares no cap-named constant, spawns nothing, authors no consent; `_SPAWN_ALLOWLIST` (:46-49) already lists `rpc/rpc_client.py`. Gate is 7-passed today and a codec module trips none of the four scans. |
| §8 step 3 "the kernel terminator … **if the owner permits a kernel edit**" | **THIS IS THE ONE THING C GETS WRONG ON THIS LENS.** MEASURED (§1.2): any kernel byte turns `test_kernel_untouched_vs_merge_base` RED. C treats it as an owner-permission question; it is *also* a gate-amendment question, and C does not budget the amendment. |
| §9.6 "I did not assess whether a defaulted `dialect:` parameter breaks any of the 190 tests" | Honest, and **the risk here is genuinely near-zero** — a defaulted keyword parameter whose default path is `payload` / `resp.to_json()` / `_event_to_dict(event)` is a provable no-op, and C §1.4 proposes to *prove* it two ways (golden byte compare + identity assertion). No other option offers a mechanical proof of inertness. |

**Does the deliverable ship?** **Yes, and first.** C-minimal step 4: *"Ship `RpcChannel` on the pi
dialect, because after step 3 the pi dialect terminates correctly and `stream.reduce_line` still
works unmodified."* That preserves the one measured gift the kickoff handoff identified — both
channels serialize through the same `_dataclass_to_dict` — so **the parity test keeps its measured
feasibility and never grows a second reducer.** A creates a divergence (`_meta`); B creates a
divergence (`params.event` wrap); C-minimal creates **none**.

**Half-migrated risk: LOWEST.** If the sprint runs out of time, what sits in main is: a two-line
`entry.py` fix, a no-op codec seam with an identity proof, terminator fixes below the seam, and a
`RpcChannel`. **Nothing is half-migrated because nothing is migrated.**

**Reversibility: BEST.** Backing out C-minimal in six months = delete `rpc/dialect.py`, revert 5
call-site edits, delete one gate test. No pin is widened, no wire record is amended, no `_meta` is on
the wire, `RPC_COMMAND_TYPES` is still 29, `PI_COMMAND_FIELDS` still has 29 rosters. **It is the only
option whose back-out is a `git revert` of a single commit.**

**Bonus that no other option offers:** §8 step 6 — *"a test asserting exactly one dialect is
registered"*. That is a **gate installed before it is needed**, and this repo's own record (C §4.6.3,
verified: `rpc_mode.py:316-320` still says "Sprint 6f wires that bridge") is the argument for it.

**Docks:** C's own §7 argues against C-full for six pages and only reaches C-minimal in §8, which
buries the winning proposal at the end of a 600-line note. C §4.2's "345 tests" undercounts the real
625. And C §7.6 is right that `prompt` is where "one dispatcher, two grammars" breaks — but in
C-minimal there is only one grammar, so it is moot.

---

## 3. Cross-cutting sequencing facts

1. **The first commit is the same in all three plans** and is ~2 lines: `entry.py:2160-2166`.
   Everything else is downstream. MEASURED by execution.
2. **The real-child rpc test bed does not exist and all three must build it.** Spec §8 claims it
   exists; `tests/rpc/test_rpc_client_lifecycle.py:88`'s own comment says otherwise. Budget it once,
   not per-option — it is not a differentiator.
3. **The kernel is the cheapest fix and the most expensive process.** ~2 lines at
   `harness/core.py:4287-4298` fixes the terminator for TUI, `--mode json` and rpc at once. It also
   turns the band gate red until the gate is amended. **Every option that mentions it must budget
   the gate amendment; none does.**
4. **`exit_code` and `dropped_lines` policy is owed under every option** (B §7.1 says this correctly)
   and is pure `aelix_agents/envelope.py` work — extension band, no gate exposure.
5. **Suite-green risk ranking (INFERRED from the measured counts):** C-minimal < A < B.
   C-minimal's exposure is a defaulted keyword parameter over 190 tests with a mechanical inertness
   proof. **A's `_meta` exposure measured at ~0** (§2.1) — what remains for A is 6 pin sites + a
   30th `PI_COMMAND_FIELDS` roster + 3 WS drains (4 `receive_text()` calls), i.e. **~10 mechanical
   test edits, all of them known and enumerable**. B's is a dialect latch rewritten into `_on_line`
   over the same 190 plus 152 parity tests plus 17 server tests, asserted zero-break **without
   running the suite** (B §9.6).
6. **Reversibility ranking (the tiebreaker on this lens):** C-minimal (revert one commit; no pin
   widened, no wire record amended, `RPC_COMMAND_TYPES` still 29) ≪ A (must un-widen a pin, which
   cannot be done retroactively — A §8.5 concedes it) < B (a permanent second grammar that §5.5
   says never dies) ≪ C-full.
7. **The band gate is a merge gate, not a CI-forever gate.** `test_kernel_untouched_vs_merge_base`
   compares against `merge-base(origin/main, HEAD)`, so after the sprint merges it goes green again
   — but it is red for the entire life of the branch if the kernel is touched, which is precisely
   when a reviewer would see it. Treat "kernel edit" as costing a gate amendment plus its review,
   not as free.

---

## 4. What I could not measure

- I did not run `tests/rpc` / `tests/pi_parity` / `tests/server` to green (625 collected); I only
  collected them and ran the 7-test band gate. The full-suite "7102 passed" figure is from the
  kickoff handoff at `ab624fd`, not re-measured here.
- I did not write any option's patch, so every line-count estimate in §2 is the designer's, re-checked
  against the call sites, not against a diff.
- The `_meta` exposure: I established the *intersection* of "exact-dict assert" and "reads back the
  emitted stream" is empty by grep over all 30 `tests/rpc` files and the 14 `tests/pi_parity` files,
  and I read `test_rpc_mode_event_pipe.py` in full. I did not read the other seven capture files in
  full, so a non-`== {` exact assertion (e.g. `sorted(record.keys()) == [...]`) could still exist.
  **The claim is "measured near-zero", not "proven zero".**
- Whether the sunset gate C §8.6 proposes would actually hold is a process question, not measurable.
- I did not price the *review* cost of each option, only the mechanical cost. On a sprint whose P3
  predecessor returned 18 demonstrated findings, review capacity is the real constraint and it
  favours the smallest diff — which is another point for C-minimal that I did not score.
