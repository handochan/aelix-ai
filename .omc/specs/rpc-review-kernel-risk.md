# RPC sprint — adversarial review of Lane K (kernel risk)

**Reviewer stance:** adversarial. **Method:** the change was BUILT, not read.
**Review worktree:** `/workspaces/aelix-ai/.claude/worktrees/wf_fb25c427-bb1-1` (base `da61337`).
**Plan under review:** `/workspaces/aelix-ai/.omc/specs/rpc-sprint-plan.md` §5 "Lane K".
**Python:** `/workspaces/aelix-ai/.venv/bin/python` (3.12.1).

Every claim below is labelled **MEASURED** (command + real output reproduced here) or
**INFERRED**. Pi ground truth read from the cached pin
`.../scratchpad/pin/agent-harness.ts` @ `734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

---

## 0. What was built

Three source states were materialised and probed side by side:

| id | what | where |
|---|---|---|
| **BASELINE** | pristine `da61337` (`git archive HEAD packages`) | `scratchpad/baseline_src/packages` |
| **LITERAL** | plan verbatim: `return []` deleted, `except AgentHarnessError` → **bare `except Exception`**, `stop_reason` conditional | (transient, in worktree) |
| **REPAIRED** | LITERAL + `except (Exception, asyncio.CancelledError)` so the abort actually reaches the block | worktree `core.py:4287-4312` |
| **VARIANT C** | REPAIRED + pi's `return [failureMessage]` instead of `raise`, and pi's EMPTY message body | `scratchpad/variantC_src/packages` |

Plus the `entry.py --mode rpc` fix (`repo=repo, fs=fs` removed from the `run_rpc_mode`
call at `cli/entry.py:2160`).

---

## VERDICT

**Lane K is NOT safe to execute as written.** Two of its three bullets are wrong:

* Bullet (2) — "widen `except AgentHarnessError` → bare `except Exception`" — **cannot
  implement bullet (1)**. `asyncio.CancelledError` is not an `Exception`. Built verbatim, the
  abort path still emits **no `agent_end`** — the sprint's headline deliverable — while
  additionally breaking every caller.
* The "re-raise preserved" instruction is a **pi divergence**, not pi convergence. pi's
  `emitRunFailure` **returns** `[failureMessage]`; it does not rethrow. Preserving the raise
  breaks the TUI, print mode, rpc mode and `AgentHarness.dispose()` — all four catch
  `Exception`, none catch `BaseException`.

Bullet (2)'s *intent* (non-`AgentHarnessError` failures must reach the closure) is sound and
**MEASURED working**. Bullet (1) and bullet (3) need a different mechanism.

The plan's Lane K **band-gate note is factually false** and the gate fails.

### Full-suite numbers (MEASURED, both runs in this worktree)

```
BASELINE  da61337, pristine :  2 failed, 7100 passed, 1 skipped, 9 warnings in 1747.81s
CHANGED   Lane K REPAIRED   :  6 failed, 7096 passed, 1 skipped, 9 warnings in  942.67s
```

The 6 CHANGED failures:

| test | attributable to Lane K? |
|---|---|
| `tests/agents/test_p2_band_boundaries.py::test_kernel_untouched_vs_merge_base` | **YES** — Finding 2 |
| `tests/test_abort_cancels_in_flight_parallel.py::test_abort_during_parallel_tool_call_cancels_turn_task` | **YES** — Finding 7 |
| `tests/test_abort_event_emit.py::test_abort_emits_abort_hook_event_with_cleared_snapshots` | **YES** — Finding 7 |
| `tests/test_abort_lane_ab.py::test_harness_abort_fires_signals_and_cancels_turn_task` | **YES** — Finding 7 |
| `tests/test_agent_harness.py::test_abort_during_turn_clears_queues` | **YES** — Finding 7 |
| `tests/agents_ext/test_print_channel_spawn.py::test_a_child_that_finishes_at_the_deadline_is_not_a_timeout` | **no** — load flake, Finding 10 |

**5 real regressions.** Note the LITERAL variant would show the same 5 while delivering
none of the fix (Finding 1).

---

## FINDING 1 — CRITICAL. The plan built verbatim does not fix the abort path at all

`asyncio.CancelledError` inherits `BaseException`, not `Exception`
(`scratchpad/probe_cancelled.py`, MEASURED):

```
python: 3.12.1
CancelledError MRO: ['CancelledError', 'BaseException', 'object']
issubclass(CancelledError, Exception)     = False
issubclass(CancelledError, BaseException) = True

--- (A) plan verbatim: inner 'except CancelledError: raise' + outer 'except Exception' ---
handlers that ran: ['inner', 'escaped-to-BaseException-level']
```

So the widened handler at `:4293` never sees the abort. Driving a **real** `AgentHarness`
through a **real** `abort()` (`scratchpad/probe_abort.py`) — MEASURED:

| | events after abort | `agent_end`? | `prompt()` returns |
|---|---|---|---|
| **BASELINE** | `agent_start, turn_start, message_start, message_end, message_start` | **False** | `RETURNED []` |
| **LITERAL (plan verbatim)** | *identical* | **False** | `RAISED CancelledError` |
| **REPAIRED** | `… message_start, message_end, turn_end, agent_end` | **True** | `RAISED CancelledError` |

**The LITERAL row is a pure regression**: it delivers none of the fix and breaks the caller
contract. The plan's own §2 table ("abort → ❌ nothing") stays ❌ after the plan is applied.

**Fix:** the outer clause must be `except (Exception, asyncio.CancelledError)` (or
`BaseException` with a re-raise guard) — pi's `catch (error)` has no BaseException/Exception
split, so a literal transliteration of `catch (error)` to `except Exception` is a mistranslation.

---

## FINDING 2 — CRITICAL. The band gate FAILS, and the plan's note about it is false

Plan, Lane K:

> **Band-gate note:** the gate is a TEXT scan for `subagent` / `aelix_agents` over the kernel
> (`tests/agents/test_p2_band_boundaries.py:39-41`). This change mentions neither. **Verify green.**

MEASURED — it is red:

```
$ PYTHONPATH=<worktree>/packages/{aelix-coding-agent,aelix-agent-core,aelix-ai}/src \
  /workspaces/aelix-ai/.venv/bin/python -m pytest -q -p no:randomly -p no:cacheprovider \
  tests/agents/test_p2_band_boundaries.py
E   AssertionError: the kernel must not change in P2:
      [' M packages/aelix-agent-core/src/aelix_agent_core/harness/core.py']
tests/agents/test_p2_band_boundaries.py:241: AssertionError
FAILED tests/agents/test_p2_band_boundaries.py::test_kernel_untouched_vs_merge_base
1 failed, 6 passed in 9.99s
```

The cited `:39-41` is the **constants block** (`_KERNEL_FORBIDDEN_CI`), not a test. The file
has **7** tests. The text scan is `test_kernel_has_no_subagent_surface` at `:188`. The plan
missed `test_kernel_untouched_vs_merge_base` at **`:214-241`**, which asserts *not one line*
of `packages/aelix-agent-core` changed — **committed vs `merge-base origin/main HEAD` AND
uncommitted via `git status --porcelain`** (`:236-238`). **Any** Lane K edit fails it by
construction; there is no wording of the change that passes.

This is a governance decision the plan never surfaces: owner decision **D1** ("the band rule
isolates delegation *policy*; core is not a sanctuary") is in direct conflict with a
**currently-armed test** that freezes the kernel outright. Lane K must either amend/retire
`test_kernel_untouched_vs_merge_base` as an explicit, reviewed decision, or not touch the
kernel. Shipping Lane K silently red is not an option — and "Verify green" invites exactly
that.

---

## FINDING 3 — CRITICAL. The re-raise breaks all three real call sites + `dispose()`

Plan Lane K bullet (2) says "re-raise preserved". Every consumer filters on `Exception`:

* TUI `tui/shell.py:2501-2506` — `try: await harness.prompt(...) except Exception as exc:`
* print `modes/print_mode.py:192,205` — inside `except BrokenPipeError: raise` / `except Exception`
* rpc `rpc/rpc_mode.py:309-315` — `async def _run(): try: await harness.prompt() except Exception`
* kernel `harness/core.py:2921-2923` — `with contextlib.suppress(Exception): await task`

`scratchpad/probe_callsites.py` replays each shape verbatim against a real harness + real
abort — MEASURED:

```
### BASELINE da61337
TUI   : ['prompt returned normally', 'finally: chrome.set_running(False)'] | REPL survived Esc? True | n/a
print : reached step8=True exit_code=0 | n/a
rpc   : task.cancelled()=False stderr=[] pending_tasks_left=0
rpc   : gather(task) -> ok

### REPAIRED (plan's Lane K)
TUI   : ['finally: chrome.set_running(False)'] | REPL survived Esc? False | ESCAPED to _input_loop: CancelledError
print : reached step8=False exit_code=0 | ESCAPED run_print_mode: CancelledError
rpc   : task.cancelled()=True stderr=[] pending_tasks_left=0
rpc   : gather(task) RAISED CancelledError
```

Consequences, each MEASURED:

1. **TUI: pressing Esc kills the REPL.** `except Exception` at `shell.py:2502` does not catch
   it, so `renderer.finalize()` never runs (partial stream never committed, live window never
   cleared), the `✖ …` banner never prints, and `CancelledError` escapes `_input_loop`. The
   comment two lines above it — *"A failed turn must not kill the REPL"* — is falsified by the
   change.
2. **print mode: step 8 is never reached.** `print_mode.py:215-228` is the branch that reads
   `state.messages[-1].stop_reason in ("error","aborted")` and sets `exit_code = 1`. That is
   the *only* consumer of an `"aborted"` stop_reason in the product, i.e. exactly what Lane K
   bullet (3) exists to feed — and the re-raise makes it dead. `reached step8=False`.
3. **rpc mode: the anti-silence breadcrumb is silenced.** `stderr=[]`. The comment at
   `rpc_mode.py:311-313` says *"W4 m2 / P-119: do NOT silently suppress"* — the change makes
   that exact suppression happen, on the sprint's own flagship path.
4. **`dispose()` now raises** (`scratchpad/probe_dispose.py`), because
   `contextlib.suppress(Exception)` at `core.py:2922` does not suppress `CancelledError`:
   ```
   == BASELINE ==  dispose(): completed normally
   == CHANGED  ==  dispose(): RAISED asyncio.exceptions.CancelledError
   ```
   Abort-then-quit is the single most common way a user ends an rpc/TUI session.

**pi does not do this.** `pin/agent-harness.ts:512-525`:

```ts
private async emitRunFailure(model, error, aborted, signal): Promise<AgentMessage[]> {
    const failureMessage = createFailureMessage(model, error, aborted);
    await this.handleAgentEvent({ type: "message_start", message: failureMessage }, signal);
    await this.handleAgentEvent({ type: "message_end",   message: failureMessage }, signal);
    await this.handleAgentEvent({ type: "turn_end", message: failureMessage, toolResults: [] }, signal);
    await this.handleAgentEvent({ type: "agent_end", messages: [failureMessage] }, signal);
    return [failureMessage];          // <-- RETURNS. Does not rethrow.
}
```

`:568-580` calls it from `catch (error)` and **returns its value**; the only rethrow is when
*failure reporting itself* fails (`catch (failureError)` → `AgentHarnessError("unknown", …)`).
So "re-raise preserved" is a **divergence from pi**, and the plan's D3 claim "Converges with
pi; does not diverge" is **OVERSTATED** on this axis.

---

## FINDING 4 — HIGH. `[error] ` with an empty payload is persisted to the session file

Question: *does an aborted turn persist a synthetic `[error] …` assistant message the user
sees on resume?* **Yes — MEASURED**, with a real `JsonlSessionRepo` session on disk
(`scratchpad/probe_session.py`):

```
### BASELINE da61337
prompt() -> []
SESSION JSONL:  {"type":"session"} {"type":"message"}                 # 1 message
RESUME REBUILD: role='user' text='please do the dangerous thing'

### REPAIRED (plan's Lane K)
prompt() RAISED CancelledError
SESSION JSONL:  {"type":"session"} {"type":"message"} {"type":"message"}   # 2 messages
RESUME REBUILD: role='user'      text='please do the dangerous thing'
                role='assistant' stop_reason='aborted' text='[error] '     # <-- persisted
```

The write path is `core.py:4175-4177` inside `emit()` — `message_end` → `session.append_message`.
The plan never mentions that emitting the closure sequence has a **persistence** side effect.

Two distinct defects here:

**4a — the message body is literally `"[error] "` with nothing after it.**
`str(asyncio.CancelledError())` is `''` (MEASURED: `probe_cancelled.py` (D) → `str() = ''`,
`args = ()`), so `f"[error] {exc}"` renders `'[error] '` and `error_message=''`. Every abort
writes that string into the transcript.

**4b — pi's failure message body is EMPTY, not `"[error] …"`.** `pin/agent-harness.ts:49-57`:
```ts
function createFailureMessage(model, error, aborted): AssistantMessage {
    return { role: "assistant", content: [{ type: "text", text: "" }], api: model.api,
             provider: model.provider, model: model.id,
             stopReason: aborted ? "aborted" : "error",
             errorMessage: error instanceof Error ? error.message : String(error), … };
}
```
pi carries the reason on `errorMessage` only, and also fills `api/provider/model/usage`.
aelix fills none of those. So on the persistence question the plan's "converges with pi" is
**half true**: pi *does* persist (`handleAgentEvent` `message_end` → `session.appendMessage`,
`:483-486`) — but persists an empty body, not a user-visible `[error] ` artifact.

**4c — the residue is fed back to the provider on the next turn.**
`scratchpad/probe_next_turn.py`, MEASURED:

```
### BASELINE           context handed to the provider on turn 2:
                          'user:turn two: what did I just ask?'
### REPAIRED           context handed to the provider on turn 2:
                          'assistant:[error] '                       # <-- leading assistant turn
                          'user:turn two: what did I just ask?'
```

The next turn now opens with an **assistant** message and no preceding user message. INFERRED
(not executed against a live provider): Anthropic's Messages API rejects a leading assistant
turn, so the first prompt after any abort can hard-fail with a 400. This should be executed
against a real key before Lane K lands.

---

## FINDING 5 — MEDIUM. `agent_end` payload diverges from pi (pre-existing, amplified)

`core.py:4306` emits `AgentEndEvent(messages=list(self._state.messages))` — the **whole**
conversation. pi emits `agent_end` with `messages: [failureMessage]` (`:522`). Pre-existing
for the `AgentHarnessError` path; Lane K extends it to abort and to every other exception,
i.e. it triples the blast radius of a divergence the plan asserts it is closing.

---

## FINDING 6 — MEDIUM. The widened handler arms the auto-retry loop with stale state

`scratchpad/probe_generic_error.py`, a stream raising `ValueError("Provider returned error:
429 rate_limit_exceeded, please retry")` — MEASURED:

| | `agent_end`? | state after | `_is_retryable_error(last)` |
|---|---|---|---|
| BASELINE | **False** | *(empty)* | False |
| REPAIRED | **True** ✅ | `stop_reason='error' text='[error] Provider returned error: 429 …'` | **True** |

The **agent_end half of bullet (2) works as advertised** — this is the one part of Lane K that
is straightforwardly correct. But because the handler still re-raises, `prompt()` never reaches
its own auto-retry loop (`core.py:1258-1290`), so the synthetic retryable message is left in
`_state.messages` for the *next* `prompt()` to scan at `:1259-1266`. INFERRED: cross-turn
contamination of the retry budget. Pre-existing in shape for `AgentHarnessError`; the widening
enlarges the class of exceptions that leave it.

---

## FINDING 7 — HIGH. 4 abort tests go red, and they are RIGHT, not stale

MEASURED, REPAIRED variant:

```
FAILED tests/test_abort_event_emit.py::test_abort_emits_abort_hook_event_with_cleared_snapshots
FAILED tests/test_abort_lane_ab.py::test_harness_abort_fires_signals_and_cancels_turn_task
FAILED tests/test_abort_cancels_in_flight_parallel.py::test_abort_during_parallel_tool_call_cancels_turn_task
FAILED tests/test_agent_harness.py::test_abort_during_turn_clears_queues
E   asyncio.exceptions.CancelledError
packages/aelix-agent-core/src/aelix_agent_core/loop.py:101: asyncio.exceptions.CancelledError
```

**Is the TEST wrong or the CHANGE wrong? The CHANGE.** None of the four asserts "no
`agent_end` on abort" — none pins the bug the sprint is fixing. All four assert the *other*
contract: `await task` on the prompt task after `abort()` completes normally
(`test_agent_harness.py:280`, `test_abort_lane_ab.py:292`,
`test_abort_cancels_in_flight_parallel.py:84`, `test_abort_event_emit.py:69`), then check
queue-clearing / signal-firing / `phase == "idle"`.

Proof that the change, not the test, is at fault — **VARIANT C** (identical except it emits
then `return [failure]` the way pi does, with pi's empty body) — MEASURED:

```
$ bash scratchpad/run_variantC_tests.sh
35 passed in 2.98s
```

and it still delivers the fix:

```
--- abort ---     agent_end? True     prompt() gave: RETURNED [AssistantMessage(… stop_reason='aborted' …)]
--- callsites --- TUI: REPL survived Esc? True | print: reached step8=True exit_code=1 | rpc: task.cancelled()=False, gather -> ok
--- dispose ---   dispose(): completed normally
--- generic ---   agent_end? True     stop_reason='error'
```

Note `print: exit_code=1` — with Variant C the `"aborted"` branch at `print_mode.py:215-228`
finally becomes live and an aborted `--print` run exits 1, which is presumably the intent.
That is a **caller-visible behaviour change of its own** and must be an explicit decision, not
a side effect.

---

## FINDING 8 — MEDIUM. Nothing in 7100 tests catches Findings 4, 5 or 6

Variant C makes the whole abort/harness set green (35 passed) while still writing
`stop_reason='aborted'` + a synthetic message into the **session file on disk** and into the
**next turn's provider context**. Neither is asserted anywhere. Per the plan's own §6.3
mutation gate, Lane K therefore ships an unobserved state/persistence change. Lane T must add,
at minimum:

* an abort test that asserts the **session JSONL content** after an abort (not just events);
* a test that asserts the next turn's `AgentContext.messages` does not begin with an
  assistant message;
* a test that asserts `prompt()` does not raise on abort (i.e. pins the contract Finding 7
  shows is currently only pinned incidentally);
* a test that asserts `dispose()` after abort completes.

---

## FINDING 9 — LOW. `--mode rpc` fix confirmed; §3.1 of the plan is CONFIRMED

MEASURED (`scratchpad/run_rpc_live.sh`), with the two kwargs removed at `cli/entry.py:2160`:

```
### WORKTREE (fix applied)
{"type": "response", "command": "get_state", "success": true, "id": "r1", "data": {"model": {…}}}
### BASELINE da61337 (no fix)
  File ".../cli/entry.py", line 2160, in _async_main
    await run_rpc_mode(
  File ".../rpc/rpc_mode.py", line 1911, in run_rpc_mode
RuntimeError: repo and fs must not be supplied when runtime_host is explicit — the runtime owns them
```

---

## FINDING 10 — LOW. §3.3 undercounts the flakes, and misses a whole second family

Plan §3.3 names **one** flake, in `tests/rpc/test_rpc_client_shutdown.py`. MEASURED across the
two full runs, **three different tests** failed non-deterministically, in **two** files:

```
BASELINE run: FAILED tests/agents_ext/test_print_channel_spawn.py::test_stop_by_id_kills_child
              FAILED tests/rpc/test_rpc_client_shutdown.py::test_stop_escalates_to_sigkill_when_sigterm_ignored
CHANGED  run: FAILED tests/agents_ext/test_print_channel_spawn.py::test_a_child_that_finishes_at_the_deadline_is_not_a_timeout
              (test_stop_escalates… PASSED this time)
```

Isolated re-runs:

* `test_stop_escalates_to_sigkill_when_sigterm_ignored` — `scratchpad/flake_check.sh`,
  6× against pristine `da61337`: **3 failed / 6 (50%)**. Plan §3.3 measured ~40%; confirmed,
  and worse than stated.
* `test_a_child_that_finishes_at_the_deadline_is_not_a_timeout` —
  `scratchpad/flake_check2.sh`, 4× pristine + 4× changed: **8/8 passed**. Load-dependent only.
* `test_stop_by_id_kills_child` — 6/6 passed in isolation. Load-dependent only.

So `tests/agents_ext/test_print_channel_spawn.py` hosts a **family** of deadline/CPU-sensitive
tests that fail differently on each loaded run. Lane T's §3.3 item covers one test in the wrong
file; without covering this family, any full-suite run of this sprint will show a red the
sprint did not cause, and the sprint will be blamed for it (exactly the risk §3.3 was written
to avoid).

---

## Answers to the six review questions

1. **What breaks?** Full suite: `6 failed, 7096 passed, 1 skipped` vs baseline
   `2 failed, 7100 passed, 1 skipped`. **5 are real regressions** — 1 band gate (Finding 2) +
   4 abort tests (Finding 7); the 6th is a pre-existing load flake (Finding 10). The band gate
   failure is a *plan error* — the plan asserts it will be green. The 4 abort tests fail
   because the **CHANGE is wrong, not the tests** — none of them pins the missing-`agent_end`
   bug; they pin "abort does not raise", and Variant C proves a pi-faithful implementation
   keeps all 35 green *while* delivering `agent_end` (Finding 7).
2. **`except Exception` vs `CancelledError`:** MEASURED — `issubclass(CancelledError,
   Exception) is False`; a bare `except Exception` catches **nothing** on the abort path, so
   the plan verbatim is a no-op for its own headline fix (Finding 1). **No double-catch is
   possible**: `probe_cancelled.py` (C) shows nested handlers each run exactly once, and the
   real run emits exactly one `agent_end`. With the plan's literal filter the abort is caught
   **zero** times by the widened handler, not twice.
3. **Does the re-raise change caller-visible behaviour?** Yes, catastrophically, at all three
   call sites plus `dispose()` — MEASURED side-by-side (Finding 3).
4. **Does it pollute session state?** **Yes — confirmed by driving a real harness with a real
   session and reading the JSONL off disk** (Finding 4). An aborted turn persists
   `role='assistant' stop_reason='aborted' text='[error] '` and a resume rebuilds it. It is
   also replayed into the next turn's provider context as a leading assistant message.
5. **Band gate:** **RED — `1 failed, 6 passed in 9.99s`**, and the plan's description of it is
   false: it names a 2-line constants block as "the gate", misses that the file holds 7 tests,
   and misses the one test (`:214`) that no Lane K edit can pass (Finding 2).
6. **Does pi append the failure to state?** pi persists it via
   `handleAgentEvent(message_end) → session.appendMessage` (`:483-486`) — so on *persistence*
   the plan converges. But pi's body is `text: ""` (`:49-57`), pi emits `agent_end` with
   `[failureMessage]` only, and pi **returns** rather than rethrows (`:525`). The plan's
   "converges with pi" is **OVERSTATED**: on the re-raise it is an outright divergence.

## What must change before Lane K is executed

1. Outer clause → `except (Exception, asyncio.CancelledError)`. `except Exception` alone is
   inert on the abort path.
2. On abort, **emit then `return [failure]`** (pi `:525`). Do not re-raise.
3. Failure body → `text=""` with the reason on `error_message` (pi `:49-57`), so the
   transcript never shows `[error] ` with an empty payload.
4. Decide `test_kernel_untouched_vs_merge_base` (`tests/agents/test_p2_band_boundaries.py:214`)
   explicitly — it is a hard kernel freeze that no wording of Lane K survives.
5. Decide, on the record, that `--print` now exits 1 on abort (`print_mode.py:215-228` goes
   live), and that an aborted turn adds a message to the session file.
6. Add the four Lane T tests in Finding 8 — today nothing observes any of it.

---

### Reproduction assets
`/tmp/claude-1000/-workspaces-aelix-ai/c1866485-a3d8-4543-a79f-44722f1631c8/scratchpad/`
(session-scoped, will be wiped): `probe_abort.py`, `probe_cancelled.py`, `probe_callsites.py`,
`probe_dispose.py`, `probe_session.py`, `probe_next_turn.py`, `probe_generic_error.py`,
`run_variantC.sh`, `run_variantC_tests.sh`, `flake_check.sh`, `run_rpc_live.sh`,
`baseline_src/`, `variantC_src/`.
Built change kept in worktree `/workspaces/aelix-ai/.claude/worktrees/wf_fb25c427-bb1-1`
(`core.py:4287-4312`, `entry.py:2160`).
