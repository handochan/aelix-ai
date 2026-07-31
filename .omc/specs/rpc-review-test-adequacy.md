# RPC sprint plan — adversarial review, TEST-ADEQUACY lens

**Reviewer stance:** adversarial. **Base:** `da61337`. **Worktree:** `/workspaces/aelix-rpc`
(carries only the `entry.py --mode rpc` fix, verified still the sole diff at the end of this review).
**Every claim below is MEASURED** — a command plus its real output, or a source mutation plus the
test result it produced. INFERRED items are labelled.

Probe scripts and the mutation copy live under
`/tmp/claude-1000/-workspaces-aelix-ai/c1866485-a3d8-4543-a79f-44722f1631c8/scratchpad/`
(`probe/`, `mut/`). **/tmp is wiped between sessions** — the reproduction recipes are inlined below
so they can be rebuilt.

---

## Verdict

**Do not execute §4.2 as written.** One row of the plan (§4.2, the `_jsonl.py` per-line budget) is a
demonstrated *regression* that introduces a brand-new silent-failure path — the exact class of defect
the sprint exists to eliminate. Two more rows (§6.1, §6.2) rest on premises that are measurably false,
so the sprint would ship with a test suite that is green *and blind* on the abort path.

The plan's *diagnostic* half is strong: §3.2's busy-lie and abort-silence both reproduce live, §2's
kernel-fix attribution is correct and its fix works when implemented, and the two band-gate traps in
Lane K are both real. The *test* half is where it fails.

---

## §1 — FEASIBILITY OF THE PARITY TEST (assignment 1)

### 1.1 CONFIRMED: a scripted stub child satisfies BOTH channels. No real model needed.

I wrote one dual-mode stub (`probe/stub_child.py`) that builds a real `AgentHarness` with a scripted
`stream_fn` and branches on `--mode`: `run_print_mode(..., mode="json")` or `run_rpc_mode(...)`.
Both halves work.

```
$ python probe/stub_child.py --mode json          # exits 0, 8 JSONL events
{"type": "agent_start"} … {"type": "agent_end", …}

$ python probe/drive_rpc.py                       # bidirectional, child never exits
OUT: {"type": "response", "command": "prompt", "success": true, "id": "p1"}
OUT: {"type": "agent_start"} … OUT: {… "type": "agent_end"}
=== agent_end seen: True
=== child still alive: True
=== exit code: 0                                  # exits only on stdin EOF
```

Then I drove the **same task** through both and reduced both wires with the **production reducer**
(`aelix_agents.stream.reduce_line`) and `envelope.build_result` (`probe/parity.py`):

```
print lines: 8   rpc lines: 9
all 16 _StreamState fields: OK  (summary, stop_reason, input/output/cache_*/tokens/turns,
                                 provider, model, saw_agent_start, saw_agent_end, dropped_lines …)
--- SubagentResult §6.2 MUST-MATCH list ---
ok / status / summary / truncated / stop_reason / dropped_lines / permission_mode / dropped_tools: all OK
usage SAME: True
exit_code print: 0   rpc: None
```

So the plan is **right** that this is a unit-scale test, not an integration test. `reduce_line` also
tolerates the interleaved `{"type":"response",…}` envelope without corrupting state — the
"normalise the command envelopes" step in §6.2 is defensive, not load-bearing.

Boot cost is *not* a constraint either — the plan's neighbouring untracked bed
(`tests/rpc/test_rpc_real_child.py`, docstring) asserts "a cold child costs ~25 s, of which ~18.6 s
is import time" and designs "one test rather than four" around it. Measured on this machine:

```
real CLI  --mode rpc (get_state + EOF): ['1.93s', '1.44s', '2.56s']
stub child --mode rpc (EOF only):       ['1.43s', '0.73s', '0.60s']
```

**INFERRED:** the 25 s figure is stale or was measured under load; the "one test only" design
constraint it justifies should be re-derived, not inherited.

### 1.2 …but the parity test as scoped is a TAUTOLOGY on the path that matters. (CRITICAL)

§1.1 is the happy path. The sprint exists for the **abort** and **failure** paths. I drove a real
abort on each channel's own terms — print: parent `SIGTERM`s the process group (exactly what
`reaper.reap` does); rpc: parent sends `{"type":"abort"}` and the child survives — and reduced both
(`probe/abort_ab.py`, against unmodified `da61337`):

```
saw_agent_end  print=False  rpc=False   (child returncode print=0)

field            PRINT abort                RPC abort                  MUST-MATCH?
ok               False                      False                      OK
status           aborted                    aborted                    OK
summary          (no output)                (no output)                OK
truncated        False                      False                      OK
stop_reason      None                       None                       OK
dropped_lines    0                          0                          OK
permission_mode  None                       None                       OK
usage equal? True
```

**Every field on the plan's §6.2 MUST-MATCH list is equal — while both channels are broken.**
`saw_agent_end` is `False` on both, and `saw_agent_end` is not a field of `SubagentResult` at all
(`subagent_contract.py:101-135`). The `SubagentResult` form of the parity test is *structurally
incapable* of seeing the §4 turn contract: `build_result` derives `status` from the caller's
`outcome` proposal (`envelope.py:201-213`), which each channel supplies from its own process-level
bookkeeping, not from the wire.

Consequence for §6.3: reverting §4 rows 3 and 4 will **not** turn the `SubagentResult` parity test
red. The plan says the two forms of §6.2 exist because "the docs disagree"; the real reason is
stronger and should be stated as a requirement: **the event-sequence form is the only one that can
gate §4, and it must be written first.** Order them, do not merely write both.

---

## §2 — THE MUTATION GATE (assignment 2)

Method: full copy of the tree at `da61337` into `scratchpad/mut/`, run with
`PYTHONPATH=$S/packages/{aelix-coding-agent,aelix-agent-core,aelix-ai}/src`.

**Baseline** (`tests/rpc tests/harness tests/agents tests/agents_ext`):
`1 failed, 1868 passed, 2 skipped in 233.33s` — the single failure is the known §3.3 flake.

### 2.1 `_handle_prompt` mutation — IS caught (good news, and it narrows the plan's claim)

I deleted the entire task-creation block from `_handle_prompt`, leaving a handler that returns
`RpcSuccessResponse` and does nothing else:

```
FAILED tests/rpc/test_rpc_mode_event_pipe.py::test_session_events_flow_to_stdout_without_response_wrapper
FAILED tests/rpc/test_w6_regressions.py::test_handle_prompt_logs_failure_to_stderr
3 failed, 2537 passed, 2 skipped in 369.27s      (third = the known flake)
```

So the handler is not unprotected in general. The plan's claim is narrower and **CONFIRMED as
narrow**: what is uncaught is specifically the *semantics of the success envelope*. The only test
touching it, `tests/rpc/test_rpc_mode_handlers.py:76-84`
(`test_handle_prompt_returns_success_envelope`), asserts `isinstance(response, RpcSuccessResponse)`
against an **idle** harness — so the plan's preflight fix keeps it green. No conflict; but no gate
either.

### 2.2 §3.2's two defects — both reproduce live. CONFIRMED.

Against unmodified worktree source, a stub whose turn sleeps 30 s (`probe/slow_child.py`):

```
=== BUSY PROBE ===
ERR: [rpc] prompt task failed: AgentHarnessError("AgentHarness is busy (phase='turn'); …")
   response(prompt,success=True,id=p1)
   response(prompt,success=True,id=p2)      <- the lie, on the wire
   agent_start turn_start message_start message_end message_start
  agent_end present? False

=== ABORT PROBE ===
   response(prompt,success=True,id=p1)
   response(abort,success=True,id=a1)
   agent_start turn_start message_start message_end message_start
  agent_end present? False
```

The preflight is also clean to implement — **`AgentHarness.phase` (`core.py:911`) and
`AgentHarness.is_idle` (`core.py:943`) are public properties**, so §4 row 2 needs no private-attribute
read (unlike the `harness._pending_tasks` access the current handler already performs).

### 2.3 §2's abort diagnosis — CONFIRMED, after a false negative I am reporting deliberately

My first implementation of Lane K's abort fix appeared to do nothing (`saw_agent_end` still False).
That was a probe timing artifact, not a product fact. Instrumenting the branch:

```
ERR: [LANEK] turn_task created
ERR: [LANEK] CancelledError caught in _run, abort_requested=True
ERR: [LANEK] abort closure ENTERED
TYPES: [response, agent_start, turn_start, message_start, message_end, message_start,
        response, message_start, message_end, turn_end, agent_end]
```

`core.py:4287`'s `except asyncio.CancelledError` **is** reached with `_abort_requested=True`, and
emitting the four closure events there **does** put `agent_end` on the rpc wire. **Plan §2 row 4 is
correct.** (`core.py:4052-4340` is `_run`, confirmed by AST; `core.py:1178-1333` is `prompt`, and the
busy raise at `:1187-1194` is indeed before the first await.)

### 2.4 Lane K's widen is caught by NOTHING. §6.3 cannot be satisfied for rows 3–4 by existing tests.

Applying only `except AgentHarnessError` → `except Exception` at `core.py:4298`, across
`tests/harness tests/rpc tests/agents tests/agents_ext tests/cli tests/pi_parity`:

```
2 failed, 3195 passed, 2 skipped, 8 warnings in 342.31s
FAILED tests/rpc/test_rpc_client_shutdown.py::…sigkill…      <- the known flake
FAILED tests/pi_parity/test_phase_3_1_strict_superset.py::test_adr_0041_sprint_5b_closure_landed
```

The second is an artifact of my scratch copy lacking `docs/` (`FileNotFoundError:
…/docs/decisions/0042-built-in-coding-tools.md`). After `cp -r docs`: `7 passed in 0.20s`.
**Real failures from the widen: zero.**

So §6.3's instruction — "revert each of the four §4 rows independently and confirm a *named* test
fails" — is unsatisfiable for rows 3 and 4 today. Those names must be *created* by this sprint, and
per §1.2 they cannot be the `SubagentResult` parity test.

---

## §3 — DOES `test_print_mode_json_contract.py:206-253` GENERALISE? (assignment 3)

**No. Measured.** I lifted `_run_child` verbatim (`probe/reuse.py` — same `stdin=DEVNULL`, same
`LineAssembler`, same `asyncio.wait_for(gather(_pump_out, _pump_err), 90)`, same
`assert lines`) and pointed it at both modes of the identical stub:

```
json  -> 8 events, exit=0, types=['agent_start', 'turn_start', 'message_start', 'message_end']
rpc   -> TIMEOUT (child never reached EOF)
```

What must change, concretely:

| `_run_child` element | why it breaks on rpc | required change |
|---|---|---|
| `stdin=asyncio.subprocess.DEVNULL` (`:222`) | rpc's transport *is* stdin; and see §5 — DEVNULL **hangs the child forever** | `stdin=PIPE` + a writer + explicit close |
| pumps until stdout EOF (`:239-244`) | the child never exits on its own | terminate on observing `agent_end` **or** a correlated `{"type":"response",…}` for the id |
| `exit_code = await proc.wait()` (`:247`) | §4.1 makes the rpc exit code non-evidence (`exit_code=None`) | drop it from the contract, or assert only the teardown code |
| `assert lines, "the child produced no stdout"` (`:248`) | fires *after* the 90 s wait | move the liveness assertion before the drain, or it masks the hang |
| `_first`/`_types` helpers (`:255-266`) | must skip `type == "response"` envelopes | filter, per §6.2 |

The *stderr* pump, the `LineAssembler`, and `_child_env`'s hermeticity are reusable as-is. The plan's
sentence "`…:206-253` is the reusable pattern" should read "…is the reusable *stderr-drain and
framing* pattern; the process lifecycle must be rewritten."

---

## §4 — `dropped_lines` AND THE `_jsonl.py` BUDGET (assignment 4)

### 4.1 CRITICAL — the §4.2 budget silently swallows inbound RPC **commands**

`JsonlLineReader` has two production call sites, and the plan counted only one:

* `rpc_client.py:135` — `attach_jsonl_line_reader(proc_stdout, …)`, the **child's stdout** (what the
  plan is thinking of);
* **`rpc_mode.py:2075` — `reader_obj = JsonlLineReader(_on_line)`, the rpc SERVER's own stdin
  COMMAND INTAKE** (`_pump_stdin`, `:2077-2088`).

It is also re-exported publicly from `rpc/__init__.py:9-12,84`.

I implemented §4.2 exactly as specified (per-line budget + dropped counter + resync in
`_jsonl.py`), set the budget to 2048 to isolate framing from tokenizer cost, and ran the same
5000-char prompt against both trees (`probe/cmp.py`):

```
=== BASELINE da61337 (no budget) (message=5000 chars) ===
  response for id=BIG? True
  agent_end seen? True
  small get_state answered? True

=== WITH PLAN 4.2 BUDGET (2048) (message=5000 chars) ===
  response for id=BIG? False
  agent_end seen? False
  small get_state answered? True
```

The command vanishes. **No response, no error, no event, and the channel stays healthy** — so
nothing downstream even times out at the transport layer; a correlating client waits forever on
`id=BIG`. Reproduced identically at the real 4 MiB budget with a 4 MiB+1000 payload
(`probe/bigcmd.py`): `=== any response for id=BIG? False`.

This directly contradicts the plan's own §4 headline — *"a terminal event is guaranteed on every
path"*. §4.2 **creates** a path with no signal at all.

And no existing test notices: with the budget in place,
`tests/rpc/test_jsonl.py` + `tests/pi_parity/test_phase_4_4_strict_superset.py` → **`30 passed in 0.68s`**.

**Required before §4.2 can be executed:** the budget must be per-*direction*, not per-class — either
a constructor argument that the server's intake leaves unbounded, or (better) an over-budget inbound
line must produce a correlated `{"type":"response","success":false,…}`. Note the id cannot be
recovered from a dropped line, which is itself an argument for not truncating inbound commands at all.

### 4.2 HIGH — the `dropped_lines` symmetry claim cannot be delivered by the listed work

§4.2 asserts *"`dropped_lines` then becomes symmetric across channels and the parity test asserts
equality"*. It cannot, because the counter is unreachable:

```
attach_jsonl_line_reader signature: (stream, on_line) -> None
lines delivered to caller: ['{"ok":1}']            # the 3000-char line was dropped
value returned by attach_jsonl_line_reader: None
=> caller has NO handle on the JsonlLineReader, so dropped_lines is unreachable
directly-owned reader.dropped_lines: 1
```

`attach_jsonl_line_reader` (`_jsonl.py:92-110`) constructs the reader in a local at `:104` and
returns `None`; `rpc_client.py:133-135` fires it as a bare task. Lane P lists
"`_jsonl.py` — per-line budget + dropped-line counter + resync" and stops there. The plumbing
`JsonlLineReader → attach_jsonl_line_reader → RpcClient → RpcChannel → _StreamState.dropped_lines`
is **four unlisted edits**, two of them in `rpc_client.py`'s public shape.

Compare: `PrintChannel` folds it explicitly at `print_channel.py:781`
(`state.dropped_lines = assembler.dropped_lines`) with a comment saying it is done "HERE and nowhere
else". The rpc side has no equivalent hook.

### 4.3 Note — `MAX_LINE_BYTES` already exists, in the *other* band

`aelix_agents/stream.py:58` already defines `MAX_LINE_BYTES = 4 * 1024 * 1024` with a `LineAssembler`
that implements precisely the budget/counter/resync §4.2 wants. Moving an equivalent into
product-core creates two independent budgets that can drift. **INFERRED:** worth an explicit decision
in the new ADR rather than silently duplicating.

---

## §5 — NEW DEFECT, found while probing: `--mode rpc` HANGS on `/dev/null` stdin (HIGH)

Not in the plan, and it lands squarely on Lane E and §6.1.

```
$ timeout 20 python -m aelix_coding_agent --mode rpc < /dev/null
exit=124 (124 = HUNG)                      # real CLI, worktree source

$ printf '{"id":"r1","type":"get_state"}\n' | timeout 20 python -m aelix_coding_agent --mode rpc
{"type": "response", "command": "get_state", "success": true, "id": "r1", …}
exit=0
```

Same result for the stub (`< /dev/null` → 124; `: | …` closed pipe → 0). A regular/character-device
stdin never yields EOF through the `connect_read_pipe` path, so the process never reaches
`stdin_eof.set()` (`rpc_mode.py:2081-2083`) and lives forever.

Why it matters here:

* `PrintChannel` passes `stdin=asyncio.subprocess.DEVNULL` and its comment marks it **"MANDATORY"**
  (`print_channel.py`, in the `create_subprocess_exec` call). Lane E says `RpcChannel.run` should
  "mirror `PrintChannel.run`". A copied spawn line produces an immortal orphan on every failed
  delegation.
* §6.1's bed is modelled on `_run_child`, which also uses `DEVNULL` — which is exactly the 90 s
  TIMEOUT measured in §3.

This deserves its own row in §5 Lane P (a guard, or a bounded idle deadline) and an explicit
negative test.

---

## §6 — `exit_code=None` (assignment 5): LEGAL, not merely untested. CONFIRMED.

* `envelope.build_result(…, exit_code: int | None = None, …)` — `envelope.py:178`. `None` is the
  declared default.
* The failure predicate special-cases it: `(exit_code is not None and exit_code != 0)` —
  `envelope.py:203`. `None` is explicitly non-evidence, exactly as §4.1 claims.
* `SubagentResult.exit_code: int | None = None` — `subagent_contract.py:112`.
* `declined_result` already ships `exit_code=None` in production (`envelope.py:273`).
* No consumer breaks: `grep -rn "\.exit_code" packages/aelix-coding-agent/src/` returns only
  `rpc_mode.py:517`, `tools/bash.py:507,512`, `extensions/subprocess_hooks.py:302,314,351` — all on
  *bash*/*hook* outcome objects, **none on `SubagentResult`**.
* Measured end-to-end in `probe/parity.py`: `exit_code print: 0   rpc: None`, and every MUST-MATCH
  field still equal.

§4.1 is sound.

---

## §7 — Two more measured corrections

### 7.1 §3.3's flake characterisation is wrong in a way that will mislead the fix (MEDIUM)

The plan says it "fails ~40% of runs (measured 2/5)". Measured here:

```
single test, 8 consecutive runs, isolated:   1 passed ×8
whole file,  5 consecutive runs, isolated:   4 passed ×5
full sweep (tests/rpc tests/harness tests/agents tests/agents_ext): FAILED 2/2
```

**13/13 green in isolation, 2/2 red under load.** It is load/concurrency-sensitive, not
probabilistic-in-isolation. The plan's *remedy* (add a drain loop, mirroring the reliable sibling at
`:68-83`) is almost certainly right — but anyone who verifies that fix the way the plan describes
will run it in isolation, see green, and learn nothing. The verification recipe must be "run inside
the full sweep."

### 7.2 §6.2's abort case will flake unless it waits on a condition (MEDIUM)

Six runs of the identical A/B probe against the identical Lane-K-fixed tree:

```
saw_agent_end  print=True  rpc=True    ×4
saw_agent_end  print=False rpc=False   ×2      (print returncode -15)
```

33% divergence from fixed `asyncio.sleep()` waits alone. `returncode=-15` correlates with the
False runs: the print child is racing SIGTERM to flush its closure events. §6.2 says nothing about
this, and given §7.1 the sprint cannot afford a second load-sensitive timing test. The parity test
must block on the terminal condition (`agent_end` observed, or the correlated error response), never
on a sleep.

### 7.3 Lane K's two band-gate traps — both CONFIRMED empirically

* `test_kernel_untouched_vs_merge_base` (`tests/agents/test_p2_band_boundaries.py:214-241`) diffs
  `packages/aelix-agent-core` against `git merge-base origin/main HEAD` **and** greps
  `git status --porcelain` (`:237-239`) — any kernel byte, committed or not, turns it red for the
  branch's whole life. Plan is right.
* The cap-name rule is real. I appended `MAX_LINE_BYTES = 4 * 1024 * 1024` to `rpc/_jsonl.py` in the
  worktree and ran the gate:

```
FAILED tests/agents/test_p2_band_boundaries.py::test_product_core_declares_only_the_allowlisted_caps
E  … not on the allowlist … : {'MAX_LINE_BYTES': ['rpc/_jsonl.py:119']}
1 failed, 6 passed in 5.76s
```
  (worktree restored immediately; `git status --porcelain packages/` back to the single `entry.py` line.)

  **But the plan's proposed escape is wrong.** It offers "pick a non-matching name **or** add it to
  the allowlist deliberately" as co-equal options. The gate's own assertion text states the intent —
  *"the caps that bound delegation … are `aelix_agents`' policy, and a different subagent runtime
  gets to choose different ones"* — and its own docstring concedes it is "STILL A HEURISTIC, and a
  bare `FANOUT = 8` defeats it." Renaming to dodge the regex is defeating the gate, not passing it.
  Only the allowlist amendment (with the ADR as justification) is legitimate — and §4.1 above argues
  the constant may not belong in product-core at all.

---

## Required changes before execution

1. **§4.2 — BLOCK.** Do not add an unconditional per-line budget to `_jsonl.py`. It silently drops
   inbound rpc commands (§4.1, measured A/B). Re-scope to the child-stdout direction only, and give
   the inbound direction a correlated error response instead of silence.
2. **§4.2 — the `dropped_lines` symmetry claim is not deliverable** by the listed files. Add the four
   plumbing edits (`attach_jsonl_line_reader` return shape, `RpcClient` accessor, `RpcChannel` fold,
   `_StreamState` write) to Lane P/E, or drop `dropped_lines` from §6.2's MUST-MATCH list.
3. **§6.2 — reorder and re-scope.** Write the **event-sequence** parity test first and make it the
   §6.3 gate for §4 rows 3 and 4. The `SubagentResult` form is provably blind to them (§1.2) and must
   be labelled a *regression* test, not a contract gate.
4. **§6.1 — rewrite the lifecycle.** `_run_child`'s DEVNULL + read-to-EOF + `proc.wait()` model
   times out at 90 s against an rpc child (§3). Reuse only its stderr drain, framing and env
   hermeticity.
5. **NEW row — `--mode rpc` hangs on `/dev/null` stdin** (§5). Fix it, and add a negative test,
   before `RpcChannel` copies `PrintChannel`'s "MANDATORY" `stdin=DEVNULL`.
6. **§3.3 — correct the reproduction recipe** to "inside the full sweep"; 13/13 green in isolation
   (§7.1).
7. **§6.2 — forbid sleep-based waits** on the abort case; 2/6 measured divergence (§7.2).
8. **§5 Lane K trap 2 — strike "pick a non-matching name"** as an option (§7.3).

## Plan claims verified as CORRECT (so the sprint does not re-litigate them)

* §3.2 busy-lie and abort-silence — both reproduce live on unmodified source (§2.2).
* §2 row 4 (abort short-circuits before closure) — correct; the prescribed fix demonstrably puts
  `agent_end` on the wire (§2.3).
* §4.1 `exit_code=None` — legal by declaration and by predicate, with no consumer at risk (§6).
* The parity test needs **no real model** — a single dual-mode scripted stub satisfies both channels
  (§1.1).
* Both Lane K band-gate traps are real (§7.3).
* The preflight needs no private-attribute access — `harness.phase` / `harness.is_idle` are public
  (§2.2).
