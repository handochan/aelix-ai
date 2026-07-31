# RPC sprint plan — PREMISE FALSIFICATION review

**Reviewer role:** adversarial premise falsifier. **Date:** 2026-07-31.
**Plan under review:** `.omc/specs/rpc-sprint-plan.md`
**Trees:** main `/workspaces/aelix-ai` @ `da61337`; worktree `/workspaces/aelix-rpc` @ branch
`feat/rpc-channel-and-protocol`.
**pi ground truth:** fetched independently from `raw.githubusercontent.com` at
`734e08edf82ff315bc3d96472a6ebfa69a1d8016` ("the pin") and at tag `v0.80.2`. I did not take any
recon note's word for a pi fact.

Everything below is labelled **MEASURED** (command + real output, or source line opened in the
current tree) or **INFERRED**.

**Verdict up front:** the plan's headline finding (claim 1) is *correct and well-supported*. Claim 3
is correct externally but its consumer census is incomplete. Claims 2, 4 and 5 are each wrong in a
way that changes work. And the plan's own core mechanism (§4 "preflight before `create_task`") is
**mechanically impossible in Python** — proved below against the real `AgentHarness`. Two of the
plan's scoped deliverables (§6.1 real-child bed, §3.1 entry.py) are already done in the worktree,
and one wall behind the entry.py fix is still standing.

---

## CLAIM 1 — CONFIRMED, in full

> *"pi's rpc-mode.ts emits a correlated error RESPONSE on prompt failure, not a synthetic event, and
> grep for `agent_end|synthetic` returns 0 hits at both v0.74.1 and v0.80.2."*

**MEASURED.**

```
$ grep -nE "agent_end|synthetic" pin/rpc-mode.ts   ; echo exit=$?
exit=1                                  # 0 hits, 754 lines
$ grep -nE "agent_end|synthetic" v0802/rpc-mode.ts ; echo exit=$?
exit=1                                  # 0 hits, 774 lines
$ grep -niE "agent_?-?end|synth" <both> ; echo exit=$?
exit=1                                  # also 0 — not hiding behind camelCase
```

pi's prompt handler, at the pin (`packages/coding-agent/src/modes/rpc/rpc-mode.ts`):

```
379   case "prompt": {
382     let preflightSucceeded = false;
388       preflightResult: (didSucceed) => {
390         preflightSucceeded = true;
391         output(success(id, "prompt"));
396     if (!preflightSucceeded) {
397       output(error(id, "prompt", e.message));
```

Events go out unwrapped and untransformed — `rpc-mode.ts:346-347`
`unsubscribe = session.subscribe((event) => { output(event); })`. I confirmed aelix matches: the
live wire carries bare `{"type": "agent_start"}`, not `{"type":"event","event":{…}}`.

So the NOTE at `rpc_mode.py:316-320` is false in every clause, as the plan says. **MEASURED** — the
NOTE text is present verbatim at those exact lines in the current tree, and `rpc_mode.py:309`
(`await harness.prompt(cmd.message, source="rpc")`) does drop `cmd.images`. The stale docstring
claims are also real: `rpc_mode.py:1` says "(492 LOC)" and `:116` says "483-547"; the file at the
pin is 754 lines with `prompt` at 379.

`grep -ciE "version|breaking|compat"` over pi's `rpc.md` → **0** at both refs. (Nit: the plan calls
it "1412-line"; that is v0.80.2. The pin's is 1407.)

### 1b — but the plan MISREADS the guard it just discovered (HIGH)

pi emits the error response **only if preflight did not succeed** (`:396`). The plan's §4 table
row 5 says:

> `| prompt task raises after start | correlated error response (pi rpc-mode.ts:395-399) | rpc_mode._handle_prompt |`

and §5 Lane P says *"correlated error response on task failure"* — both unguarded. Implemented
literally, a mid-turn failure emits a **second `{"type":"response", … "id":"p1"}` for an id that
already received `success:true`**. MEASURED that the ack really does go out first, on a live child:

```
[ 25050.8ms] {"type": "response", "command": "prompt", "success": true, "id": "p1"}
```

The plan's own prose in §4 states the correct rule ("a correlated error response for anything
*rejected before the turn begins*, and `agent_end` for anything that *started*"). The table row and
the lane bullet contradict it. Fix the table, not the prose.

---

## CLAIM 2 — OVERSTATED: it describes pi's *exceptional* abort path, not its primary one

> *"an unqualified catch at agent-harness.ts:568 routes everything including abort into
> emitRunFailure, which emits message_start → message_end → turn_end → agent_end at :512-524"*

The **literal citations are all correct** (MEASURED, `pin/agent-harness.ts`):

| plan cite | actual |
|---|---|
| `:568` unqualified catch | `568   } catch (error) {` ✅ |
| routes into `emitRunFailure` | `570 return await this.emitRunFailure(` ✅ |
| `aborted` flag | `573   abortController.signal.aborted,` ✅ |
| `:512-524` four emits | `512 private async emitRunFailure(`, `519` message_start, `520` message_end, `521` turn_end, `522` agent_end ✅ |
| stopReason | `49-56 createFailureMessage … stopReason: aborted ? "aborted" : "error"` ✅ |
| emitters don't short-circuit on an aborted signal | `emitOwn` `:211-219` / `emitAny` `:221-229` never test `signal.aborted` ✅ |

**But that catch is only reached if `runAgentLoop` THROWS, and pi's normal abort does not throw.**
MEASURED at `pin/agent-loop.ts`:

```
196   if (message.stopReason === "error" || message.stopReason === "aborted") {
197     await emit({ type: "turn_end", message, toolResults: [] });
198     await emit({ type: "agent_end", messages: newMessages });
199     return;                     // <- RETURNS NORMALLY. No exception.
```

and `streamAssistantResponse` turns an aborted provider stream into an ordinary terminal message
(`:340-350`: on `"done"`/`"error"` it calls `response.result()` and emits `message_end`).

So pi's **primary** abort emission is:

```
message_start (REAL, from the stream)  → message_update* → message_end (REAL, stopReason "aborted")
  → turn_end → agent_end
```

not four synthetic events. `emitRunFailure` is the fallback for a genuinely thrown error.

**Why this changes Lane K.** aelix aborts by `task.cancel()` → `CancelledError` (`core.py:1416-1419`
→ `:4287`), so its loop is torn down mid-await and *cannot* emit its own `turn_end`/`agent_end` the
way pi's does. Routing `CancelledError` into the existing `except AgentHarnessError` closure block
(`core.py:4298-4316`) therefore emits a **synthetic `message_start` + `message_end` for a
`"[error] …"` text message, on top of the real assistant `message_start` that already went out.**
MEASURED on the live wire — 65 `message_update`s had streamed against an open assistant
`message_start` at the moment abort landed. pi never produces that shape.

D3's claim *"Converges with pi; does not diverge"* is therefore true only for *"an `agent_end`
exists"*. At the message level it is a new divergence. Lane K must decide explicitly: emit only
`turn_end` + `agent_end` (closer to pi, requires splitting the closure block), or accept a
documented aelix-specific synthetic-message shape.

### 2b — NEW, unlisted divergence: pi's `abort()` awaits idle; aelix's does not (HIGH)

MEASURED. pi `agent-harness.ts:936-961` `abort()` → `949 await this.waitForIdle();` → `965
waitForIdle() { await this.runPromise; }`, and `rpc-mode.ts:413-416` returns `success(id,"abort")`
only *after* that await. **In pi the abort ack is ordered after the terminator.**

aelix `core.py:1388-1441` `abort()` has no idle wait at all — it cancels the task, fires bash
signals, emits `AbortHookEvent` + `queue_update`, and returns.

MEASURED live consequence: the abort ack came back in **8 ms**, mid-stream.

```
[ 40048.0ms] --> SEND {"id": "a1", "type": "abort"}
[ 40056.0ms] RESP  cmd=abort id=a1 ok=True
```

Even after Lane K lands, `agent_end` will arrive **after** the abort ack, the reverse of pi. The
plan's §4 "turn contract" states no ordering, so an `RpcChannel` that treats the abort ack as
"turn over" will be wrong. This belongs in the contract explicitly.

---

## CLAIM 3 — external obligation CONFIRMED zero; the consumer census is INCOMPLETE

**MEASURED, all re-run by me:**

| check | result |
|---|---|
| `git tag -l \| wc -l` | `0` |
| `git ls-remote --tags origin` | empty |
| `gh release list` | empty, `exit=0`, authed as `handochan` |
| `aelix-web` exists? | no — only doc references (`docs/contracts/README.md:62`, ADR-0133) |
| `release.yml:82-89` drops aelix-server | ✅ `rm -f dist/aelix_server-*.whl dist/aelix_server-*.tar.gz` |
| `grep -rniE rpc /workspaces/aelix-marketplace` | 0 hits |
| `grep -rniE rpc .github/ scripts/ Makefile* docker* Dockerfile*` | 0 hits |
| does the TUI drive the wire? | **no** — only prose in `tui/shell.py:328`, `tui/scoped_models.py:33` |

**But two in-repo consumers are missing from the plan entirely:**

1. **`packages/aelix-server/src/aelix_server/rpc_ws.py:146` calls `run_rpc_mode`** — a full-duplex
   WS bridge, covered by four live tests in `tests/server/test_server.py` (`test_rpc_round_trip`,
   `test_rpc_single_flight`, `test_rpc_active_flag_reset_after_close`,
   `test_rpc_forwards_server_initiated_frame`). Lane P edits `run_rpc_mode`. aelix-server appears
   in the plan only as evidence that obligation is zero — never in §5, §7 or §8. It is excluded
   from the *publish set*, not from the *repo*, and it is the only production caller of
   `run_rpc_mode` besides `entry.py`. (Coupling risk is low — those tests assert `get_state` round
   trips — but "unlisted" is how regressions get in.)
2. **`packages/aelix-coding-agent/src/aelix_coding_agent/agents/resolver.py:200-202`** already
   produces `["--mode", "rpc"]` as the non-oneshot delegation argv, and it is *already consumed* by
   `aelix_agents/print_channel.py:68` via `profile_to_argv`. Lane E says `RpcChannel` gets "its
   **own** argv". That is two argv producers for the same channel; the plan does not say which
   wins or whether `/agents show`'s dry-run rendering (resolver's stated purpose) must keep
   matching what actually spawns. That was the whole point of the dry run.

**Net:** D2's "the interop consumer does not exist yet" holds for *external* consumers. The plan
should say "no external consumer" and then name the two internal ones.

---

## CLAIM 4 — D2's CALL is defensible; D2's JUSTIFICATION is false as written

> *"every defect this sprint owes is semantic, and framing standards do not fix semantics"*

The strongest case against this, stated fairly:

The defect list (lens-2 §2) has **nine** entries. The claim holds for D1–D4 (the terminator
defects) — no envelope emits `agent_end` on abort, so the plan is right that adopting JSON-RPC 2.0
would not have fixed the thing this sprint exists to fix. **That reasoning is sound and I do not
dispute the decision.**

It does **not** hold for D5–D9, which are envelope defects a framing standard fixes *structurally*:

- **D6 (free-text errors, no code space) — JSON-RPC 2.0 fixes this by construction**, and it is
  load-bearing for *this sprint's own headline deliverable*. MEASURED, `rpc_types.py:487-510`:

  ```
  487  class RpcErrorResponse:
  495      command: str
  496      error: str
  497      id: str | None = None
  498      success: Literal[False] = False
  499      type: Literal["response"] = "response"
  ```

  **There is no `code` field.** The plan's §4 turn contract routes the *busy* rejection — its
  central new signal — through this envelope. So the only way an `RpcChannel` can tell "busy" from
  "no ModelRegistry" from "parse failure" is to string-match English prose, today literally
  `"AgentHarness is busy (phase='turn'); use steer()/follow_up() while in a turn."`
  (`core.py:1190-1194`). JSON-RPC 2.0 mandates `error: {code:int, message, data}`. That is a
  framing standard fixing a defect the sprint is actively creating a new dependency on.
- **D5 (no event correlation/sequence)** — ACP/JSON-RPC `params.promptId`/`seq` is an envelope fix.
- **D7 (version/capability handshake)** — ACP `initialize` + `protocolVersion` is an envelope fix.
- **D9 (server→client request direction)** — JSON-RPC is bidirectional by construction.

**And the scope accounting has a hole.** §8 defers only D7 ("a wire-level version field") and D9
(the back-channel). **D5 and D6 appear nowhere** — not in §5's work breakdown, not in §8's
out-of-scope list. Two measured defects silently vanish between the judgment phase and the plan.

**Ask:** keep D2. Rewrite its rationale to *"the terminator defects (D1–D4) are semantic and are
what this sprint owes; D5/D6 are genuine envelope defects that a standard would fix and are
DEFERRED, not dismissed"*, and add D5/D6 to §8 so the ADR records them.

---

## CLAIM 5 — the entry.py fix is real, but there is a second wall in the same call

**MEASURED live**, driving a genuine `python -m aelix_coding_agent --mode rpc` child in
`/workspaces/aelix-rpc` over stdio JSONL (driver: `scratchpad/drive_rpc.py`).

| exercise | result |
|---|---|
| boot + `get_state` | ✅ correlated `success:true` |
| unknown command type | ✅ `{"command":"parse","success":false,"id":"r2"}` — id echoed |
| raw non-JSON line | ✅ parse error, **server survives** |
| second/third command on the same child | ✅ |
| `set_model` | ❌ **`"set_model requires a ModelRegistry — none configured"`** |
| real prompt (OpenRouter) | ✅ `agent_start, turn_start, message_start, message_end, message_start, 65× message_update` |
| second prompt during the turn (busy) | ❌ **`success:true` — a lie** |
| `abort` | ❌ **no terminator at all** |
| EOF | ✅ `returncode=0`, clean |

### 5a — SECOND WALL: three RPC commands are dead in `--mode rpc` (HIGH)

`entry.py:2160-2166` still does not pass `model_registry=`. The registry **exists** and is in scope
— it is built at `entry.py:1388` (`model_registry = ModelRegistry.create(auth_storage)`) and is
threaded into `run_tui` at `:2140`. It is simply not threaded into `run_rpc_mode`.

Consequence, MEASURED on the live wire:

```
{"type":"response","command":"set_model","success":false,
 "error":"set_model requires a ModelRegistry — none configured","id":"r3"}
```

Same guard kills `cycle_model` (`rpc_mode.py:673`) and `get_available_models` (`:734`).
`aelix-server/rpc_ws.py:146` omits it too.

This is the **same species of bug as §3.1, at the same call site, in the same three-kwarg
argument list** — and the plan marks that line "✅ **DONE** (§3.1)". `--mode rpc` is not fixed; it
is *bootable*. Three of its documented commands are inert.

### 5b — abort leaves the message bracket permanently open (MEASURED)

Full tail of the sequence after `abort`:

```
… 65× message_update …
[ 40048.0ms] --> SEND {"id":"a1","type":"abort"}
[ 40056.0ms] RESP  cmd=abort id=a1 ok=True
[ 60075.4ms] --> EOF          # 20 seconds of total silence in between
```

No `message_end`, no `turn_end`, no `agent_end`. The plan says "no terminator"; it is worse than
that — **the assistant `message_start` is never closed**, so a client's message state machine is
corrupted, not merely un-signalled. §4's contract should state the bracket invariant, not only the
`agent_end` invariant.

### 5c — busy lie reproduced on a real child (MEASURED — confirms §3.2)

```
SEQUENCE: … 'response:prompt:p1:True', agent_start, turn_start, message_start, message_end,
          'response:prompt:p2:True',  <- p2 NEVER RAN
stderr:   [rpc] prompt task failed: AgentHarnessError("AgentHarness is busy (phase='turn'); …")
```

The plan's §3.2 probe was in-process; this is the real binary. Confirmed.

### 5d — cold start is 6–18 s, not `STARTUP_GRACE_MS` (MEDIUM)

MEASURED (`scratchpad/timeit.py`, cwd `/workspaces/aelix-rpc`):

```
rpc cold (EOF)                12.07s
rpc cold (EOF) again           6.23s
import entry only              6.03s
rpc --version                  4.43s
```

and time-to-first-response on the live driver was **13.1 s / 15.6 s / 17.9 s** across three runs.

`rpc_client.py:88 STARTUP_GRACE_MS = 100` is **not** a readiness wait — `:142-146` only uses it to
detect an early exit. The real budget is `DEFAULT_SEND_TIMEOUT_MS = 30_000` (`:86`), which the
first command must share with a 6–18 s boot. §6.2 justifies `elapsed_ms` incomparability with
*"rpc additionally pays `STARTUP_GRACE_MS`"* — that is off by two orders of magnitude, and the
parity test's time budget is sized from it.

---

## THE LOAD-BEARING MECHANISM IS IMPOSSIBLE — §4 / §5's "preflight before `create_task`" (CRITICAL)

§4 row 2: error response *"emitted by preflight **before** the task is created"*.
§5 Lane P: *"preflight **before** `create_task`"*.
§3.2's supporting reasoning: *"`harness.prompt` raises `AgentHarnessError("busy", …)` **synchronously
before its first await** (`core.py:1189-1194`), so a preflight check placed *before* `create_task`
reproduces pi's `preflightResult(true)` exactly."*

`core.py:1189-1194` **is** before the first await — MEASURED, that part is true. But
`AgentHarness.prompt` is `async def`. Calling it constructs a coroutine and executes **zero bytes**
of its body. Proved against the real class, not a toy (`scratchpad/preflight_real.py`):

```
$ .venv/bin/python preflight_real.py
phase = turn
--- plan §4: 'preflight ... BEFORE the task is created' ---
  -> harness.prompt(...) returned WITHOUT raising: coroutine
--- only create_task + >=1 loop step surfaces it ---
  after ONE sleep(0): done = True | exc = AgentHarnessError("AgentHarness is busy (phase='turn'); …")
```

The only ways to get a pre-ack rejection are:

- **(a)** `create_task` **then** step the loop once (`await asyncio.sleep(0)`) and inspect the task —
  i.e. *after* `create_task`, which is what lens-2 §1.1(4) actually measured, and which the plan's
  own §7 does not list as a landmine even though it depends on `prompt` keeping zero awaits before
  the guard forever;
- **(b)** duplicate the phase check inside `rpc_mode` — a second source of truth for the private
  `AgentHarness._phase`;
- **(c)** refactor `AgentHarness.prompt` into a sync-checking wrapper returning an awaitable — a
  kernel API change, unmentioned.

The plan picks none of them, and §6.3's mutation gate (*"revert each of the four §4 rows
independently and confirm a named test fails"*) **cannot be satisfied for row 2 as written**,
because row 2 describes code that cannot exist. This is the single change that must be made before
execution.

---

## §6.1's PREMISE IS FALSE — the real-child bed ALREADY EXISTS AND ALREADY PASSES (HIGH)

> §6.1: *"The real-child bed (spec §8's premise was false — it must be BUILT)"*

**MEASURED.** `/workspaces/aelix-rpc/tests/rpc/test_rpc_real_child.py` — 218 lines, **untracked** —
already spawns a genuine `python -m aelix_coding_agent --mode rpc`, pumps both pipes concurrently
with chunked reads (its docstring cites ADR-0198 D6), speaks JSONL, and asserts five phases
(boot+`get_state`, many commands on one process, correlated unknown-command failure, malformed-line
survival).

```
$ PYTHONPATH=… pytest -q tests/rpc/test_rpc_real_child.py -p no:randomly
.                                                                        [100%]
1 passed in 10.20s
```

**Mutation gate run (the plan's own §6.3 standard), with guaranteed restore:** re-inserted
`repo=repo, fs=fs` at `entry.py:2164-2165` →

```
E   File ".../cli/entry.py", line 2160, in _async_main
E     await run_rpc_mode(
E   File ".../rpc/rpc_mode.py", line 1911, in run_rpc_mode
E     raise RuntimeError(
E RuntimeError: repo and fs must not be supplied when runtime_host is explicit — the runtime owns them
tests/rpc/test_rpc_real_child.py:159: Failed
FAILED test_rpc_child_boots_and_serves_a_command_sequence
1 failed in 120.98s (0:02:00)
```

Worktree verified restored (`git diff --stat` → 1 file, 2 deletions; the fix is back).

So: **the bed exists, it works, and it demonstrably catches §3.1's bug.** Lane T should *harden*
it, not rebuild it. Two concrete weaknesses it has:

- **it detects the bug by hanging for 120 s**, not by noticing the child died. `_BOOT_TIMEOUT_S =
  120.0`; `RpcChild.request` never polls `self._proc.returncode`. Clean = 10.2 s, mutated = 121 s.
  Adding a returncode race to `_wait()` turns a 2-minute hang into a 1-second failure.
- it asserts nothing about `prompt`, `abort`, `agent_end`, or the busy path — i.e. nothing about
  the four rows §4 actually delivers.

Two related plan/handoff inaccuracies:

- §3.1 says *"the **three** that spawn a process override `_build_argv` with a `python -c` stub."*
  MEASURED **four** files spawn (`test_rpc_client_timeout.py`, `test_rpc_client_shutdown.py`,
  `test_rpc_client_lifecycle.py`, `test_rpc_real_child.py`) and the fourth is precisely the bed the
  plan says does not exist.
- The kickoff framing that the worktree *"carries ONE change"* is wrong:
  `git status --porcelain` → ` M …/cli/entry.py` **and** `?? tests/rpc/test_rpc_real_child.py`.

---

## §3.3 FLAKE — CONFIRMED but UNDERSTATED

> *"fails ~40% of runs (measured 2/5)"*

**MEASURED — 10 consecutive runs**, `-p no:randomly`, correct worktree `PYTHONPATH`:

```
FAILED, FAILED, passed, passed, FAILED, FAILED, FAILED, FAILED, FAILED, passed
```

**7 failures / 10 = 70%.** Fixing it is even more clearly mandatory than the plan argues.

---

## Plan claims I checked and found SOUND (no objection)

All **MEASURED** against `/workspaces/aelix-rpc` unless noted.

| plan claim | verdict |
|---|---|
| `rpc_mode.py:316-320` false NOTE exists verbatim | ✅ |
| `rpc_mode.py:309` drops `cmd.images` | ✅ |
| `rpc_mode.py:1` "492 LOC", `:116` "483-547" stale | ✅ (pin: 754 lines, prompt at 379) |
| `core.py:4287-4295` abort `return []`; `:4298` `except AgentHarnessError` | ✅ exact |
| `core.py:1189-1194` busy raise precedes any await | ✅ (but see the preflight section) |
| `envelope.py:201-205` treats non-None non-zero `exit_code` as failure | ✅ — so §4.1's `exit_code=None` is correct |
| `_jsonl.py:61-73` has no per-line budget/counter | ✅ |
| `entry.py:1346` `if app_mode in ("print","json")` gates `_read_piped_stdin` | ✅ — §8's blocker-(a) reasoning holds |
| `src/aelix/__main__.py:142-147` mock `stream_fn` — never `-m aelix` | ✅ |
| band gate is a text scan for `subagent`/`aelix_agents` | ✅ `tests/agents/test_p2_band_boundaries.py:39-40` (plan says 39-41) |
| `release.yml:82-87` drops aelix-server from publish | ✅ (:82-89) |
| pi `rpc.md` has no version/breaking/compat language | ✅ 0 hits at both refs |
| print/json and rpc emit **identical flat event shapes** | ✅ — I captured both. Lane E's `reduce_event` premise **holds** |
| print mode emits a typeless session header, rpc emits none | ✅ `{"id":…,"created_at":…}` |

One hypothesis I raised and then **withdrew**: I suspected aelix's dispatcher silently swallowed
handler exceptions where pi wires them back as correlated errors. It does not —
`rpc_mode.py:1777-1784` has a broad `except Exception` returning `RpcErrorResponse`. Parity holds.
Likewise, aelix's non-serialized `create_task` dispatch (`rpc_mode.py:2071`) with synchronous parse
errors jumping the queue (`:2030`) is **matched by pi** (`rpc-mode.ts:688-696, 745` — `void
handleInputLine(line)`), so it is not a divergence. It *is* worth a note for §6.2: response
ordering is nondeterministic on both sides, so the parity test must compare by `id`, never by
position. I MEASURED the reordering (a parse error for the 3rd line arriving before the response to
the 1st).

---

## What must change before execution

1. **§4 row 2 / §5 Lane P — replace "preflight before `create_task`"** with a mechanism that can
   exist. Name it and state the invariant it depends on.
2. **§4 row 5 / Lane P — add pi's `!preflightSucceeded` guard**, or the sprint ships two responses
   for one id.
3. **Lane K — decide the abort event *shape*,** now that pi's primary abort path is known not to
   use `emitRunFailure`. Do not describe it as pure convergence.
4. **§4 — add the abort-ack ordering rule** (aelix acks before the terminator; pi after).
5. **Thread `model_registry=` into `entry.py:2160` (and `rpc_ws.py:146`)** and un-mark `cli/entry.py`
   as DONE. Add `set_model`/`cycle_model`/`get_available_models` to the real-child bed.
6. **§6.1 — rewrite from "must be BUILT" to "exists untracked; commit + harden".** Add the
   returncode race so the regression fails in ~1 s instead of 121 s.
7. **§2/§3 — add aelix-server and `agents/resolver.py:201` to the consumer census**, and reconcile
   the two argv producers.
8. **D2 — rewrite the rationale**; add D5 and D6 to §8 as explicit deferrals.
9. **§6.2 — resize the time budget** (6–18 s boot, not 100 ms) and require id-based comparison.
10. **§3.3 — restate the flake at 70%.**

---

## Artifacts

- pi sources at both refs, driver, probes: `/tmp/claude-1000/-workspaces-aelix-ai/c1866485-a3d8-4543-a79f-44722f1631c8/scratchpad/`
  (`pin/`, `v0802/`, `drive_rpc.py`, `s1.py`–`s5.py`, `preflight_real.py`, `timeit.py`, `mutate.sh`)
  — **`/tmp` is wiped between sessions; this note is the durable record.**
- `/workspaces/aelix-rpc` left exactly as found: ` M cli/entry.py` (the 2-line fix) +
  `?? tests/rpc/test_rpc_real_child.py`.
- Live probes cost a small number of OpenRouter calls against `deepseek/deepseek-v4-flash`.
