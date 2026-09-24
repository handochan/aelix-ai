# 0023. Compaction + Branch Summary

Status: **Accepted (Sprint 4b / Phase 2.2.2 shipped)** — **AMENDED 2026-09-24 by
#321** (`## Amendment (2026-09-24, #321)` below): a cancelled call gives the
phase back, and only a turn it set — **and 2026-09-25 by #334**
(`## Amendment (2026-09-25, #334)` below): the claim spans the whole prompt.
Supersedes (partial): ADR-0016 deferred (Phase machine expansion)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Pi `AgentHarness`는 `compact()`, `navigateTree()` 메서드를 보유하고
`session_before_compact` / `session_compact` / `session_before_tree` /
`session_tree` hook을 emit합니다. Phase machine은 `idle | turn | compaction |
branch_summary` 상태를 가집니다(`retry`는 Pi에서 declared but unused).

ADR-0016은 "compaction/branch_summary 도입 시점 미정"으로 Phase machine 확장을
deferred했습니다. 1차 원칙(Pi parity)에 따라 Phase 2.2에 명시합니다.

현재 Aelix Phase machine은 `idle | turn`만 구현합니다. `session_before_compact`
event class는 Phase 1.2에 정의되어 있으나 emit site가 없습니다.

## Decision

Phase 2.2.2 (Sprint 4b)에서 다음을 구현합니다.

### `AgentHarness.compact(custom_instructions?)`

Pi signature parity (`agent-harness.ts:689-693`):

```python
async def compact(self, custom_instructions: str | None = None) -> CompactResult:
    ...
```

Phase flow:

1. Guard busy (raise `AgentHarnessError("busy")` if not idle) — unless called
   by the prompt that holds the claim, from its own tail (#334, below).
2. `self._phase = "compaction"`; clear `_idle_event`.
3. Build `CompactionPreparation` from current branch entries.
4. Emit `SessionBeforeCompactHookEvent(preparation, branch_entries,
   custom_instructions, signal)` — payload extended per P-17.
5. Hook may cancel via `SessionBeforeCompactResult(cancel=True)` OR
   substitute a `CompactResult` via `compaction=...` (P-20).
6. Otherwise call `compaction.compact()` with `self._state.model` +
   `options.get_api_key_and_headers` (P-14 — no Pi-divergent summarizer
   callback on `AgentHarnessOptions`).
7. Persist via `Session.append_compaction(summary, first_kept_entry_id,
   tokens_before, details, from_hook=...)`.
8. Emit `SessionCompactHookEvent(compaction_entry, from_hook)`.
9. `finally`: restore `phase = "idle"` — or, nested under a prompt's claim,
   `"turn"`, leaving the idle event clear (#334, below).

### `AgentHarness.navigate_tree(target_id, options?)`

Pi signature parity (`agent-harness.ts:747-750`, `types.ts:269-273`):

```python
@dataclass(frozen=True)
class NavigateTreeOptions:
    summarize: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass(frozen=True)
class NavigateTreeResult:
    cancelled: bool
    editor_text: str | None = None
    summary_entry: SummaryEntry | None = None


async def navigate_tree(
    self, target_id: str | None, options: NavigateTreeOptions | None = None,
) -> NavigateTreeResult:
    ...
```

Phase flow (Pi parity `agent-harness.ts:747-867`):

1. Guard busy → raise.
2. `phase = "branch_summary"`.
3. `target_id is None` → return `NavigateTreeResult(cancelled=False)`.
4. `old_leaf_id == target_id` → short-circuit return (Pi
   `agent-harness.ts:756`).
5. Resolve target; raise `invalid_argument` if missing.
6. `collect_entries_for_branch_summary(...)` builds entries +
   `common_ancestor_id`.
7. Emit `SessionBeforeTreeHookEvent(preparation, signal)` — P-18 payload
   extension.
8. If hook `cancel=True` → return `cancelled=True`.
9. If hook provided `summary` dict → use it (`from_hook=True`).
10. Else if `options.summarize` AND `len(entries) > 0` → call
    `generate_branch_summary` (P-14: uses `get_api_key_and_headers`).
11. Editor-branch handling for `user_message` / `custom_message` targets:
    extract text, set `new_leaf_id = target.parent_id`.
12. `Session.move_to(new_leaf_id, summary=...)`.
13. Emit `SessionTreeHookEvent(new_leaf_id, old_leaf_id, summary_entry,
    from_hook)` — `new_leaf_id` is `str | None` per P-19.
14. `finally`: `phase = "idle"`.

### Phase machine 확장

```python
AgentHarnessPhase = Literal["idle", "turn", "compaction", "branch_summary"]
# Pi의 "retry"는 declared but unused → Aelix는 처음부터 포함하지 않음.
```

## Aelix-additive divergences

Sprint 4b ships these intentional divergences from Pi at SHA `734e08e`:

1. **`"retry"` Phase Literal value omitted (P-15).** Pi `types.ts:262`
   declares 5 values including `"retry"`; the value is declared-but-unused
   at the pinned SHA. Aelix omits it; future re-introduction is a single-
   line widening of `AgentHarnessPhase`.

2. **No summarizer callbacks on `AgentHarnessOptions` (P-14).** Pi has no
   `compactSummarizer` / `branchSummarizer` field; Aelix mirrors that
   exactly. Production code calls into the provider via
   `options.get_api_key_and_headers` (Phase 4 ADR-0038 wires the real
   adapter). Sprint 4b raises `AgentHarnessError("invalid_state")` when the
   summarizer needs auth but `get_api_key_and_headers is None`.

3. **Test-only `_summarizer_override` / `_branch_summarizer_override`
   seam.** `AgentHarnessOptions` carries two underscore-prefixed callables
   used exclusively by the Sprint 4b unit tests (`test_compact.py` /
   `test_navigate_tree.py`) to inject deterministic summarizers without
   standing up a provider. Production callers MUST leave them `None`.
   Documented as Aelix-additive per the top-level Pi-parity principle.

4. **In-memory `state.messages` mirror retained when `session=None`
   (Sprint 3b backward compat).** When a `Session` is attached, the
   per-turn `_TurnState.messages` is derived from
   `session.build_context().messages` (Pi parity). When `session is None`,
   the in-memory `state.messages` remains the primary source so existing
   Sprint 3b tests + the backward-compat fallback path keep working. See
   ADR-0022 §"Aelix-additive divergences" item 3.

5. **`SessionBeforeCompactResult.reason` field retained** (Pi has only
   `{cancel?, compaction?}`). Aelix-additive convenience for surfacing the
   cancellation message on the raised `AgentHarnessError("compaction")`.
   (W4 finding #14 / Fix 1 — cancel path now raises code `"compaction"` to
   match Pi `agent-harness.ts:707-708`, not `"invalid_state"`.)

## Consequences

- ADR-0016 deferred 종료 — 이 ADR로 supersede합니다.
- ADR-0040 Phase 2.2 closure pin ensures every session_* event has an
  emit site in `harness/core.py`.
- `pendingSessionWrites` queue: harness busy 중 session write 큐잉, idle 전이 시 flush.
  ADR-0022 Session Manager와 함께 구현합니다.
- 모노레포(ADR-0015)에서 `packages/aelix-agent-core/session/compaction.py`
  + `branch_summarization.py` 위치.
- ADR-0017 v2 catalogue의 `session_before_compact` / `session_compact` /
  `session_before_tree` / `session_tree` emit site가 Sprint 4b에서 land했습니다.
- Sprint 4b 신규 테스트 (`+34 tests`, 313 → 347):
  - `tests/test_compact.py` (9 tests — happy path, cancel, P-20 hook
    substitution, no-session, no-auth, busy guard, error propagation,
    concurrent compact, payload shape)
  - `tests/test_navigate_tree.py` (8 tests — noop, editor text,
    non-user/no-summary, summarize override, cancel, hook substitute,
    invalid target, busy guard)
  - `tests/test_phase_machine.py` (5 tests — busy guards for prompt /
    compact / navigate_tree from each non-idle phase; idle restoration)
  - `tests/test_session_emit_payloads.py` (4 tests — P-17/P-18/P-19/P-20
    payload shape verification)
  - `tests/test_jsonl_repo_fork.py` (4 tests — full copy / position=before
    / position=at / invalid_fork_target)
  - `tests/test_state_messages_derived.py` (2 tests — derived from
    build_context when Session attached, fall back to state.messages when
    None)
  - `tests/pi_parity/test_phase_2_2_strict_superset.py` (2 tests — zero
    Phase 2.2 entries in DEFERRED_ALLOWLIST, all 4 emit sites present)

## Amendment (2026-09-24, #321) — a cancelled call gives the phase back, and only a turn it set

The phase flows above give the phase back in a `finally` (compact step 9,
navigate_tree step 14). `prompt()` never had that shape. It claims the phase
synchronously, before its first await, so a concurrent caller meets the busy
guard at once (the C-2 re-entrancy fix); the claim was released by `_run`'s
`finally` on a turn that got that far, and otherwise by `prompt()`'s
`except Exception`. A `CancelledError` has been a `BaseException` since
Python 3.8, so that clause never saw one, and four awaits run under the claim
before `_run`'s `try` is entered: the `input` hook, the drain's `queue_update`
emit, `before_agent_start`, and `_run`'s own `session.build_context()`, which
sits between `_run`'s phase flip and its `try`. Cancelling the `prompt()` task
at any of them left the harness at `"turn"` for good: an embedder's
`task.cancel()`, an `asyncio.wait_for` or `asyncio.timeout` running out, a
`TaskGroup` sibling failing (all four measured) — and a Ctrl+C at the command
line, which is no embedder's. `aelix -p` awaits `prompt()` inside
`asyncio.run`'s main task, which `Runner._on_sigint` cancels on the first
Ctrl+C. With an extension's `input` or `before_agent_start` handler running
there — a Python extension's or a plugin's subprocess hook — `02f98560` left
`-p` in its own cleanup (the main task's await chain, printed 2 s after the
Ctrl+C, ended in `dispose()` → `wait_for_idle()`), and the process took three
Ctrl+C to end; with this amendment it ends on the first. `aelix --mode rpc`
runs each prompt as a task of its own, which the first Ctrl+C does not reach —
the cancelled main task waits in `dispose()` for it, printed the same way on
both trees — and the second ends `asyncio.run`, whose teardown cancels every
task still pending: three Ctrl+C on `02f98560`, two now. (Typed into a
pseudo-terminal, both handlers, both kinds of extension; every run gave its
tree's count.) The TUI's Esc and Ctrl+C are key bindings that call `abort()`
(read from `tui/chrome.py` and `tui/shell.py`, not measured), and `abort()`
cannot reach that window: it cancels `_current_turn_task`, which `_run`
assigns only after `build_context()`.

`compact()` had the same defect in a different place. Step 2 flipped the phase
and then awaited the `compaction_start` emit *before* the `try` of step 9, and
`_emit_to_subscribers` awaits a subscriber that is a coroutine — so a cancel
landing there left the phase at `"compaction"` for good, including when the
thing cancelled was a `prompt()` running the threshold auto-compaction.
`navigate_tree()` does not: it enters its `try` on the line after the flip.

Measured on `02f98560` with `.omc/specs/321-cancel-probe.py` (every wait
bounded; the cancel delivered only once the coroutine is provably parked on a
handler that set an event on entry; re-measured in the cross-review round on
`a9805d03`, whose `packages/` is the same git tree as `02f98560`'s — every row
the same):

| ARM | cancelled while parked in | after the cancel | next `prompt()` |
| --- | --- | --- | --- |
| 1 | `input` hook | `phase='turn' idle_event=False` | `AgentHarnessError('busy')` |
| 2 | drain's `queue_update` emit | `phase='turn'`; queue `['QUEUED-BY-NEXT-TURN']` (#311 put it back) | `busy` — the restored message cannot ship |
| 3 | `before_agent_start` | `phase='turn' idle_event=False` | `busy` |
| 4 | `session.build_context()` in `_run` | `phase='turn' idle_event=False` | `busy` |
| 5 | (a `wait_for_idle()` parked before the cancel) | still parked after 2 s | |
| 6 | (`dispose()` after the cancel) | still parked after 2 s — it calls `abort()`, then `wait_for_idle()` | |
| 8 | `compaction_start` subscriber, `compact()` | `phase='compaction'` | `busy (phase='compaction')` |
| 9 | `compaction_start` subscriber, `prompt()`'s threshold compaction | `phase='compaction'` | |
| 10 | `session_before_tree` hook, `navigate_tree()` | `phase='idle'` — clean | |
| 14 | `before_agent_start`, cancelled by `asyncio.wait_for(…, 0.2)` / `asyncio.timeout(0.2)` / a failing `TaskGroup` sibling | `TimeoutError` / the sibling's `RuntimeError`; `phase='turn' idle_event=False` | `busy` — all three |

### What changes

1. **`prompt()` gives the phase back on every exit that is not a return** —
   `except BaseException`, phase to `"idle"` and `_idle_event.set()` together
   (a parked `wait_for_idle()` must wake), then the same exception re-raised: a
   cancellation stays a cancellation. #311's clause (ADR-0246) is nested inside
   it and runs first, so a cancel in the two awaits that clause guards — the
   drain's `queue_update` emit and `before_agent_start` — finds the drained
   `next_turn` messages already back on the queue, once, by the time the phase
   goes back; this clause never touches the queue, and neither clause awaits,
   so no other task can observe the state in between. (A cancel in the `input`
   hook comes before the drain, so the queue was never touched. A cancel in
   `_run`'s `build_context()` is past #311's region: the phase goes back, the
   drained messages do not — see below.)
2. **…but only a `"turn"` the call itself set.** `_run`'s `finally` sets the
   phase idle when `_run` returns, and `prompt()` then runs a tail — the retry
   backoff, overflow recovery, the threshold compaction check — with the
   harness looking idle. A second `prompt()` can pass the guard there and be
   mid-turn when the first is cancelled or raises. Measured, not argued: with
   `except Exception` widened to `except BaseException` and nothing else
   (probe ARM 7 on a patched `02f98560`), cancelling #1 in its backoff left
   `phase='idle' idle_event=True` under #2's live turn and a third `prompt()`
   was **ACCEPTED** on top of it — the base had refused it, because it never
   reset on a cancel at all. The base already did the same on its exception
   path (ARM 12, `02f98560` itself): #2 slips in while #1 reads the branch for
   its threshold check, #1's `compact()` raises
   `compact() requires idle harness (phase='turn')`, and the base's
   `except Exception` reset #2's phase — third `prompt()` ACCEPTED.

   So each call takes a claim (`self._turn_owner`, a fresh `object()` per call)
   with **every** flip to `"turn"` it makes, in the same step as the flip — at
   its entry, and inside `_run`, whose flip takes the claim of the `prompt()`
   it runs for (`owner`, a keyword argument with no default, passed alike by
   the first run and by each re-run `_run([])`, the retry loop's and overflow
   recovery's) — never releases it, and gives the phase back only while the
   claim is still its own, i.e. only when it made the last flip. That shape
   took two failed versions to reach, both reviewed and never merged, called
   *the first version* and *the second version* from here on. The first took
   the claim at entry only, released it in a `finally`, and reset on its own
   claim or `None` (an `or claim is None` arm); a review measured that wrong
   both ways, because a re-run sets `"turn"` again while the claim can still
   name a second call waiting in a tail of its own. The second took it at
   entry and again right before each re-run, but not at the first run's own
   flip, which comes three awaits after the entry (the `input` hook, the
   drain's `queue_update`, `before_agent_start`); an independent cross-review
   measured another call's re-run taking the claim in between, so that the
   first run failing before its `try` found the claim naming someone else and
   gave nothing back — a raise included (ARM 21r). Every row below was
   measured in the cross-review round, each tree's `core.py` first on
   `PYTHONPATH` or run from its own worktree:

   | ARM | shape | `02f98560` | the first version | the second version | now |
   | --- | --- | --- | --- | --- | --- |
   | 15 | #2 cancelled in its own (real) backoff while #1's re-run is mid-turn | `'turn'`, #3 busy | `'idle'`, #3 **ACCEPTED** | `'turn'`, #3 busy | `'turn'`, #3 busy |
   | 16 | #2's threshold `compact()` raises while #1's re-run is mid-turn | `'idle'`, #3 ACCEPTED | `'idle'`, #3 ACCEPTED | `'turn'`, #3 busy | `'turn'`, #3 busy |
   | 17r | #1's re-run raises in `build_context()`, #2 waiting in its backoff then returning | idle, next ACCEPTED | **`'turn'` for good** — `wait_for_idle()` TIMEOUT, next busy | idle, next ACCEPTED | idle, next ACCEPTED |
   | 17c | the same, cancelled | `'turn'` for good | `'turn'` for good | idle, next ACCEPTED | idle, next ACCEPTED |
   | 13c / 13r | #2 ran start to finish in #1's backoff; #1's re-run cancelled / raises in `build_context()` | `'turn'` / idle | idle / idle (via the `None` arm) | idle / idle | idle / idle (via the re-run's own claim) |
   | 21r | #2 parked in `before_agent_start` while #1's re-run runs and #1 returns; then #2's first `build_context()` raises | idle, next ACCEPTED | idle, next ACCEPTED (its entry claim, which no re-run overwrote there) | **`'turn'` for good** — `wait_for_idle()` and `dispose()` TIMEOUT, next busy | idle, next ACCEPTED |
   | 21c | the same, cancelled | `'turn'` for good | idle, next ACCEPTED | `'turn'` for good | idle, next ACCEPTED |
   | 22 | the same gap, but a third `prompt()` gets in and parks in its `input` hook, #2's first run goes mid-turn, #3 is cancelled | `'turn'`, #4 busy | `'idle'`, #4 ACCEPTED | `'idle'`, #4 **ACCEPTED** | `'turn'`, #4 busy |

   ARM 21 is the same with #2 parked in its `input` hook (21r-input) and with
   the real timed backoff (21r-real, 21c-real): the same four columns each.
   The first version's release and `None` arm are gone, and this time the
   reason is measured, not argued. The second version's record called them
   dead because `.omc/specs/321-sabotage.py` arm I put them back and none of
   the 27 tests then moved — but none of those tests built ARM 21's shape, and
   put back on the second version the `None` arm gives all five ARM 21 arms
   their phase back (#1 released the claim when it returned, so it is `None`
   when #2's first run fails). The zero was a gap in the tests, not dead code.
   With the claim taken at the flip, arm I moves none of the 31 tests and no
   outcome of any probe arm: the probe prints the same with and without it,
   bar ARM 13's diagnostic `claim=` line.
3. **`compact()` emits `compaction_start` inside the `try`.** Step 2 above now
   reads: flip the phase, clear the idle event, enter the `try`, then emit.
   `_emit_to_subscribers` swallows listener exceptions, so the only thing the
   move changes is that the `finally` now covers a `BaseException` raised in
   the emit. No `compaction_end` is emitted for it, as for every other
   cancellation of a compaction.
4. **The `input` hook's `InputHandled` return gives the phase back by the same
   rule.** It reset the phase with no owner check — on `02f98560` and in both
   earlier versions — so a second `prompt()`, let in during the first one's
   backoff and still in its `input` hook when the first one's re-run went
   mid-turn, returned `[]` and left `phase='idle'` under the live re-run; a
   third `prompt()` was ACCEPTED on top of it (ARM 24, found by the
   cross-review; the same on `02f98560`, the one-word widening and both
   earlier versions). It now resets only on its own claim: `'turn'`, the third
   refused. An `InputHandled` with nothing else in flight still gives the phase
   back (`tests/test_input_emit.py`).

### What this does not close

- **Closed by #334 (2026-09-25)** — see `## Amendment (2026-09-25, #334)` below:
  the claim now spans the whole `prompt()`, a second `prompt()` is refused
  `busy` anywhere in the tail, and none of the shapes in this bullet can be
  built any more.
- **The idle tail itself, and the two regressions this amendment makes inside
  it.** A second `prompt()` still passes the guard while the first sleeps in
  its retry backoff or runs its compaction check (ARM 7: `#1 in its backoff:
  phase='idle' idle_event=True`, and a `wait_for_idle()` called then returns
  at once), and the first one's re-run `_run([])` sets `"turn"` without
  consulting the guard. When that re-run starts while the second call is
  still in flight (in its pre-run hooks or mid-turn), or the second call's
  first run starts while the re-run is mid-turn, two calls run at once, and
  one claim cannot name both: it names the call whose flip came last.
  Measured (ARMs 18-24, all five trees of the probe's table): with no fault
  at all, the turn that ends first resets the phase under the other and a
  third `prompt()` is ACCEPTED on every tree (ARM 19); #2 cancelled in its
  `before_agent_start` under #1's live re-run is kept — `'turn'`, #3 busy,
  where the first version reset it (ARM 20).

  **ARMs 18 and 23c are regressions against `02f98560`, and this amendment
  takes them knowingly.** They are one shape: the call whose flip came last is
  cancelled before its `try` while the other call's turn is live, and gives
  the phase back under it — `'idle'`, a third `prompt()` ACCEPTED on top of the
  live turn, where `02f98560`, which never reset on a cancel, kept `'turn'`
  and refused the third. ARM 18: #1's re-run cancelled in `build_context()`
  while #2 is in flight — parked at the provider, or still in its
  `before_agent_start` or `input` hook (ARMs 18h, 18h-input); a `queue_update`
  observer is not measured. ARM 23c: #2's first run cancelled in
  `build_context()` while #1's re-run is mid-turn. A raise in either place
  gives the phase back on `02f98560` as well (ARMs 18r and 23r, via its
  `except Exception`), so the raises are not regressions against it. ARM 23c
  and 23r are also what the claim at the first run's flip gave up against the
  second version, whose claim still named #1's re-run there (`'turn'`, #3
  busy): the price of ARMs 21 and 22, a harness left busy for good and a guard
  opened under a live turn. A slow `before_agent_start` handler or subprocess
  hook widens the window. It is confined to the overlap: it needs a second
  `prompt()` let in during the first one's backoff, the two calls' runs
  starting while the other's is in flight, and a cancel landing before the
  later one's `try`. No single claim keeps every arm of that state — each
  version so far has traded some arms for others, and the probe's docstring
  has the five-tree table — and only removing the overlap fixes all of them.
  That is the idle tail's fix, not this amendment's: hold the claim across the
  retry backoff and the compaction check, as pi does (below), so the second
  `prompt()` is refused there, the overlap never forms, and neither shape can
  be built. It is its own follow-up.
- **A drained message lost in `_run`'s session read.** A cancel (or raise) in
  `_run`'s `build_context()` is past the region #311's clause guards: the
  phase now goes back, but the drained `next_turn` messages go with the turn
  (probe ARM 4q, measured on this tree: queue `[]` after the cancel, the next
  prompt's provider saw `['later']` only). That window is #320's, not this
  one's.
- **The retry bookkeeping of a cancelled backoff** (ARM 11): cancelling a
  `prompt()` in its backoff leaves `_retry_attempt` at 1 and emits no
  `auto_retry_end` (measured), so the next turn's first retryable error
  resumes mid-sequence (read from `_handle_retryable_error`, not measured).
  The phase is idle there (`_run`'s `finally` already ran), so it is not this
  defect.

### pi

At `a328aa89a`: pi's `Agent.runWithLifecycle` (`packages/agent/src/agent.ts:507-529`)
sets `activeRun` and `isStreaming` and enters `try … catch … finally
{ this.finishRun() }` with no await in between, and JavaScript has no
exception that skips a `finally` — an abort there is a signal
(`abort()`, `:341-343`), not an unwinding. `AgentSession.prompt()`
(`packages/coding-agent/src/core/agent-session.ts:1606-1761`) runs the input
handlers (`:1634`) and `emitBeforeAgentStart` (`:1703`) *before* anything is
claimed: `_isAgentRunActive` is set only in `_runAgentPrompt` (`:1468-1470`),
whose `try` starts on the next line and whose `finally` releases it through
`_emitAgentSettled()` (`:1488`, `:872`). So pi's pre-run window holds no claim
that could leak — and holds no guard either, which is why Aelix claims first
and keeps that divergence; claiming first is what obliges `prompt()` to give
the claim back on every exit. The same `try` holds the claim across every
re-run: `agent.continue()` (`:1476`, `:1481`) and the retry sleep inside
`_prepareRetry` (`:3379`, the sleep at `:3409`) all run with
`_isAgentRunActive` still true, so pi has no idle tail — a second prompt there
meets `if (this.isStreaming)` (`:1654`; the getter returns
`_isAgentRunActive`, `:1229-1230`) — and its claim never has to be re-taken.
Aelix gives the phase back at the end of every `_run` and flips it again for a
re-run, which is why its claim has to be taken again at each flip — and taken
in the same step as the flip, inside `_run`, the way pi sets
`_isAgentRunActive` on the line before its `try` (`:1470`): a claim taken
anywhere else leaves awaits between the two for another call's flip to land in
(ARM 21). pi's shape is the model for the idle-tail follow-up above. pi's
newer harness makes ownership explicit, the same shape as `_turn_owner`: a
lane's drive is released only `if (this.activeDrive === claim.drive)`
(`packages/agent/src/harness/runtime/lane.ts:978`, `:993`), an abort for an
operation that no longer owns the lane returns `OperationMismatch`
(`:1032-1037`), and a retry wait is a state of the same operation
(`"assistant.retry_wait"`, `:204`; runs are driven with `waitForRetry: true`,
`:1178`), so that claim, too, spans the re-run. pi's `compact()` claims
`_compactionAbortController` and emits `compaction_start` synchronously
(`agent-session.ts:2408-2409`; `_emit`, `:831-835`, does not await listeners)
before its `try` (`:2413`) — no suspension point, hence no window; Aelix awaits
coroutine subscribers, so the emit moved inside the `try` to buy the same
guarantee.

### Tests

`tests/test_harness_cancel_gives_the_phase_back.py`, 23 tests. With each tree's
`core.py` first on `PYTHONPATH` (re-measured in the cross-review round; the
base is `a9805d03`'s `core.py`, the same blob as `02f98560`'s): on `02f98560`,
**16 failed, 7 passed** — the seven green there by construction are the
ownership guards the base satisfied by never resetting a cancel (the trap's
cancel arm, ARM 15's and ARM 22's shapes) or by always resetting a raise (ARM
17r's shape, both raise arms of the 13 shape, and ARM 21r's); on the one-word
widening of `02f98560` (ARM 7's tree), 8 failed — both trap arms, both
re-run-ownership arms, ARM 22's and ARM 24's shapes, and the two `compact()`
tests it does not touch; on the first version, 6 failed — ARMs 15/16's shapes,
both of 17's, 22's and 24's; on the second version, 4 failed — both of ARM
21's, 22's and 24's; here, 23 passed. Three older test files
(`tests/test_auto_retry.py`, `tests/test_compact.py`,
`tests/harness/test_overflow_recovery.py`) replace `_run` wholesale with a
fake; the fakes now accept the `owner` keyword, and nothing else about them
changed. `.omc/specs/321-sabotage.py` breaks the fix twelve ways against this
file and #311's; each of A-H and J-L is caught by the test written for it, and
I is the dead code above (the measured table is in that script's docstring).

## Amendment (2026-09-25, #334) — the claim spans the whole prompt

`_run`'s `finally` set the phase to `"idle"` and the idle event when each run
ended, but `prompt()` was not done then. It still had a tail: the retry backoff
and its re-run, the `auto_retry_end` emit, overflow recovery (a compaction and a
re-run) and the threshold compaction check. For that whole tail the harness said
it was idle. Measured on `f014fb46` (#334 design probe
`.omc/specs/334-tail-probe.py`, the real backoff at a 60 s base delay, every
point reached by parking on an event, never by sleeping):

| during #1's retry backoff | `f014fb46` | now |
| --- | --- | --- |
| phase / idle event / `is_idle` / `ctx.is_idle()` | `'idle'` / set / True / True | `'turn'` / clear / False / False |
| a second `prompt()` | ACCEPTED, ran a turn of its own | `AgentHarnessError('busy')` |
| RPC `prompt` (T1) / with `streamingBehavior: "steer"` (T1s) | `RpcSuccessResponse`, provider calls 2 / the same — `streamingBehavior` ignored | `RpcErrorResponse … busy`, calls 1 / queued `['second']`, calls 1 |
| RPC `get_state.isStreaming` | False | True |
| `wait_for_idle()` | returned at once | parked until #1 returns |
| `dispose()` (T3) | returned with #1 still running: `#1 done: False; retry_aborted: False` | `#1 done: True; retry_aborted: True` |
| extension `send_message(trigger_turn=True)` (T2) | started a second prompt (provider calls 2) | `next_turn` queue `['from-ext']`, calls 1 |
| `navigate_tree()` (T6) / RPC `compact` (T7) | ran to completion / `RpcSuccessResponse (compacted)` | `busy` / `busy` |
| `set_thinking_level` (T5) / `append_message` (T11) | appended at once / **never reached the session** | queued, written by the release: `['high']` / `['first', 'appended']` |
| the overflow and threshold branch reads (T8, T9) | `phase='idle' idle_event=True` | never read with the phase idle |

Live, against OpenRouter `anthropic/claude-haiku-4.5` with a real JSONL session
and only the first call's 429 injected (`.omc/specs/334-live.py prompt`): on
`f014fb46` a second prompt sent during #1's real backoff was accepted, #1 **and**
#2 both answered `'ALPHA\nBRAVO'`, three provider calls, both re-runs carrying
both messages; now #2 is refused `busy`, #1 answers `'ALPHA'`, two calls, each
`['Reply with exactly: ALPHA']`, the JSONL holds `['Reply with exactly: ALPHA']`,
and the next prompt answers `'CHARLIE'`.

This is also where #321's regressions lived. One claim per call could not name
two calls in flight at once, so #321 kept ARMs 20 and 22 and knowingly gave up
ARMs 18/18h/18h-input and 23c (above), and ARM 19 opened the guard with no fault
at all. All of them need a second `prompt()` let in during the first one's tail.

### What changes

1. **One claim per `prompt()` call, from its entry to one release in its
   `finally`.** The entry flip is unchanged (synchronous, before the first
   await, the C-2 fix; Aelix still claims before the `input` hook and
   `before_agent_start`, where pi claims after them — #321's kept divergence).
   The claim is held across every run, the retry backoff, the `auto_retry_end`
   emit, overflow recovery and the threshold check. It is released once, in the
   `finally` that replaces #321's `except BaseException`: flush the pending
   session writes, then — in a nested `finally`, so a cancelled flush still
   releases — `_claim = None`, `_turn_state = None`, phase `"idle"`,
   `_idle_event.set()`. A return, a raise, a cancel and an `InputHandled` all
   leave through it. #311's inner clause still runs first (restore, then
   release). The busy guard stays **outside** the `try`: a refused call raises
   before it owns anything, so it releases nothing.
2. **`_run` flips nothing.** Its entry no longer sets `"turn"` or clears the
   event, and its `finally` no longer sets `"idle"` or the event. It still
   clears `_abort_requested` at entry (what an abort *before* that should do is
   #336's) and keeps its flush backstop. No defensive `"turn"` at its entry —
   that would mask a nested compaction that failed to hand `"turn"` back.
3. **The tail's compactions nest under the claim.** `compact()` gains a private
   keyword `_claim`; overflow recovery and the threshold check pass the
   prompt's claim. With the live claim (`_claim is self._claim`, not any token:
   a finished prompt's claim is refused `busy`), `compact()` accepts phase
   `"turn"`, flips to `"compaction"` as before (RPC `isStreaming` and
   `isCompacting` both true, the hooks and events unchanged), and in its
   `finally` hands `"turn"` back **leaving the idle event clear**. Every public
   caller — RPC `compact`, the extension `ctx.compact()`, the TUI `/compact` —
   passes nothing and keeps the idle guard. The `compact() requires idle
   harness` raise at the end of a successful turn (ARM 12) is gone with the gap
   that made the harness busy there.
4. **`_turn_owner` is deleted.** While the claim is held every other flipper
   refuses a non-idle phase (`prompt()`, public `compact()`, `navigate_tree()`),
   `_run` flips nothing, and the only nested flip is made by the holder and
   handed back before the holder's release runs — so at the release the phase
   is always the call's own, and an ownership check would be a tautology.
   `_claim` survives only as the nesting token. `_run`'s `owner` parameter is
   gone; the test fakes that replace `_run` no longer take it.
5. **The release flushes writes queued in the tail, and loops.** In the tail
   the phase says `"turn"` — in the backoff, the `auto_retry_end` emit and the
   closing checks' branch reads — so `set_model`, `set_thinking_level` and
   `append_message` take the mid-turn route onto the pending queue. (While a
   nested compaction itself runs the phase says `"compaction"`: there
   `append_message` still queues, `set_model` records nothing and
   `set_thinking_level` appends at once while nothing is queued or being
   drained — as on the base, which also ran the threshold compaction under
   `"compaction"` — and otherwise goes behind the queue, item 7.) With no re-run left to flush
   them, the release does, before it says idle — pi's
   order: `_runAgentPrompt`'s `finally` flushes its pending bash and custom
   messages before `_emitAgentSettled()` (`agent-session.ts:1483-1489` @
   a328aa89a). It flushes through `_drain_pending_session_writes()` (round 4;
   item 7), which loops while the queue is non-empty: a write made while the
   flush awaits a session append lands on the fresh queue, and one pass left it
   pending on an idle harness (critic probe K2 on the one-pass version: `STILL
   PENDING on an idle harness: ['PendingModelChangeWrite',
   'PendingMessageWrite']`; now `[]`, entries in order). Like `_run`'s
   backstop, these writes get no `save_point`.
6. **An abort stops the tail.** pi pairs `_isAgentRunActive` with
   `_agentRunAbortRequested`: `abort()` sets it (`agent-session.ts:2075-2080`),
   and `_runAgentPrompt` / `_handlePostAgentRun` check it after every await and
   neither retry, compact nor continue once it is set (`:1473-1480`,
   `:1497-1528`). Holding the claim through the tail made `dispose()` (abort,
   then wait for idle) wait for whatever the tail still did, so the tail now
   reads `_abort_requested` — which `_run` clears only at its entry, so after a
   run it says whether an abort came since. Four checks: `prompt()`'s retry
   loop (no backoff after one), the overflow recovery and the threshold check
   each after their branch read (no compaction, no re-run — the read is their
   only await before the summariser), and `prompt()` after the overflow
   compaction (no re-run). Round 2 dropped two more that `prompt()` made before
   calling the overflow recovery and the threshold check: removing either
   alone reddened nothing, since the checks inside do the work and the earlier
   ones only skipped the branch read. An extension's `ctx.abort()` and the
   default `ctx.shutdown()` set the same flag (`_mark_abort`, which cancels no
   task), so they now stop the retry and the closing compaction too — on
   `f014fb46` the flag was cleared at every `_run` entry and read only on a
   cancel, so a `ctx.abort()` from a hook did nothing (review probe R2, a
   `turn_end` handler's `ctx.abort()` on a retryable error: provider calls 2,
   last `end_turn` -> 1, last `error`; R3, over the threshold: summariser 1 ->
   0). The running turn itself is still not stopped. pi's `ctx.abort()` is
   `this.abort()` (`agent-session.ts:3101-3106`). An `abort_retry()` (or `abort()`) made while the
   `auto_retry_start` emit awaits an async subscriber — before the backoff's
   event exists — now counts when the event is made. Measured: `dispose()` of a
   turn in flight over the threshold called the summariser on `f014fb46` (on a
   harness `dispose()` had already torn down: `session_compact` never fired) —
   now 0 calls (critic K3); an `abort()` in the threshold branch read still
   compacted — now 0 (K4); an `abort()` in the overflow compaction still sent
   the re-run (design T10, provider calls 2 on both `f014fb46` and the design's
   prototype) — now 1; `dispose()` during an async `auto_retry_start`
   subscriber ran the whole backoff and the re-run (K5 at a 1 s base: `1.00 s`,
   calls 2 on the prototype) — now `0.00 s`, calls 1.
7. **What a cancelled release leaves queued is written in order, and is not
   lost** (round 3; Codex cross-review finding C4). A cancel while the release
   flush awaits a session append must still release (#321), and the flush
   hands back the writes it had not attempted (#301's rule), so the harness goes
   idle with them still queued. Measured on the #334 commit before this item
   (`.omc/specs/334-c4-probe.py`, Codex's shape: `medium` queued behind the
   parked append, `prompt()` cancelled, `low` set while idle, a next prompt):
   `C4 (a) after next prompt : on disk ['low', 'medium'] restored medium` — the
   newer level was appended at once and the older one after it by the next
   turn's flush, so a resume restores the older level; and `C4 (b) dispose() :
   pending ['PendingThinkingLevelChangeWrite'] on disk []`. pi has no such
   state: its flush in `_runAgentPrompt`'s `finally` is synchronous
   (`agent-session.ts:1483-1489` @ a328aa89a) and `setThinkingLevel` appends at
   once (`:2267-2268`). Now the harness writers of what the queue carries (a
   model change, a thinking level, an appended message) drain it before they
   would reach the session (`_drain_pending_session_writes`): `prompt()` at its
   entry — pi flushes its pending messages "before the new prompt"
   (`:1669-1671`) — and at its release, an idle `set_thinking_level` (which goes
   behind a non-empty queue or a drain in flight instead of appending at
   once), a public `compact()` and
   `navigate_tree()` at their entries (the write lands before the compaction
   entry, and on the branch it was made on), and `dispose()` (and with it the
   runtime's session replace). Session writers that never used the queue are
   unchanged and do not wait for it: the extension actions `set_session_name`,
   `set_label` and `append_entry`, the REPL/TUI user-bash record, the TUI
   `/name` and RPC `set_session_name` append directly, as before.
   The drain holds a lock, because the flush detaches the queue before it
   appends: an empty queue does not mean nothing is in flight. It returns
   without awaiting only when nothing is queued AND no drain runs — a prompt
   or tree navigation entering while an idle drain appends waits for it
   (round 4: with the fast path checking the queue alone, the verification's
   sabotage M3, probe P5 moved the leaf before `medium` was written and P6 put
   the next prompt's messages before `medium` and `low`; every test stayed
   green, so two now pin it). So the common path gains no cancellation point,
   and a cancel still releases (the release-cancellation tests are unchanged
   and green). After: `on disk ['medium', 'low'] restored low`; `pending [] on
   disk ['medium']`. **The release drains through the same lock** (round 4,
   verification R1). Round 3 kept an unlocked loop there on the ground that
   `prompt()`'s entry drain had waited for any drain in flight — false when that
   entry drain is CANCELLED while it waits: an idle `set_thinking_level("low")`
   drain parked on `medium`'s append (queue empty, lock held), a prompt waiting
   for it at its entry, `x` set under that prompt's claim (queued), the prompt
   cancelled — and its release wrote `x` at once: `P1 final on disk: ['x',
   'medium', 'low'] live x restored low` (on the round-3 commit `000bc8f5`,
   verification probe `p1.py`). Now the release waits for that drain, and the
   drain's loop (or the release's) writes `x` last: `['medium', 'low', 'x']
   live x restored x`, and the cancel still releases (`P1 #2 released: phase
   idle event True claim None`). The release waits for a drain in flight
   even when its own queue is empty: that drain may have detached this
   call's tail writes and still be appending them, and `prompt()` returns only
   once they are on disk (round 5, tested; with the release draining only
   when something is queued — the round-4 verification's sabotage M10 — every
   test had stayed green, and its probe H6 printed `#2 done True phase idle on
   disk ['medium', 'low']` with `x` still in flight). **The trade-off:** one
   cancel of a prompt whose release waits behind an unfinished drain no
   longer releases until that drain ends, and if the storage's append never
   returns the prompt stays in phase `"turn"` — a second cancel releases
   (round-4 verification probe H1: `H1 one cancel, drain hung: #2 done False
   phase turn event False`, then `H1 two cancels: #2 done True phase idle
   event True claim None`; on `000bc8f5` one cancel released at once, but
   wrote out of order — R1). A second cancel while the release waits for the
   lock leaves through the same inner `finally`: what is still queued stays
   queued on an idle harness, and the next drain writes it in order — nothing
   is lost or reordered. Usually that is the drain holding the lock, when its
   loop comes round (tested); but when that drain has already let the lock go
   and woken the release, a write queued before the woken release runs, and
   the release is then cancelled, it stays queued with no drain running until
   the next drain — the next `prompt()`, idle `set_thinking_level`, public
   `compact()` / `navigate_tree()` or `dispose()` — writes it (round-4
   verification probe H7: `H7 after: phase idle lock False pending ['y']`,
   then `H7 next idle setter: on disk ['medium', 'low', 'x', 'y', 'z']
   pending []`). The one new await is on this path only — with nothing queued
   and no drain running the drain returns without suspending, so the release
   is as synchronous as before (round 5 pins it by driving the coroutine by
   hand: its first `send` must finish it; a one-line `await asyncio.sleep(0)`
   at the drain's start, the second Codex review's finding 5, had left every
   test green). The write
   in flight when the cancel lands is not requeued (#301's rule: its append may
   have landed).
   **What the drain orders, and what it does not.** It orders the writes that
   go through the queue against each other and against the queue-routed
   writers above. It does not order an idle `set_thinking_level`'s *direct*
   append — the one it makes when nothing is queued and no drain runs — which
   holds no lock: see "What this does not close" (pre-existing, #314). **A
   storage contract:** a session storage must not call back into the
   harness's session-writing API from inside an append — an idle
   `set_thinking_level`, `prompt()`, a public `compact()`, `navigate_tree()`
   or `dispose()`, each of which drains first. A drain holds its lock across
   the append, so such a callback waits for a lock its own append holds, for
   ever (Codex's second review, finding 3: a storage whose `medium` append
   awaits an idle `set_thinking_level("low")` — `REENTRANT timed out: []
   queued: ['low']`). (A `set_model` or `append_message` never drains: idle,
   it writes no session entry; mid-turn, like a mid-turn
   `set_thinking_level`, it only queues.) No in-tree storage calls back; the
   JSONL and memory storages call nothing.
   Not changed, and not made worse: **#314** — a
   `set_thinking_level` parked in an async `thinking_level_select` handler
   still re-reads the phase afterwards and records its level a second time
   (the probe's `#314 shape : on disk ['high', 'low', 'high'] live low restored
   high`, identical before and after; with writes queued it now goes behind
   them, still twice); **#326** — the flush's `except AssertionError` still
   drops the un-attempted tail: the drain adds callers but no new way to reach
   that branch (only a variant with no dispatcher arm does), and `dispose()`
   logs it instead of raising.

Who meets the held turn now (the full table is in the #334 design,
`.omc/specs/334-tail-probe.py` runs every row):

| entry point in the tail | now | right? |
| --- | --- | --- |
| `prompt()` (SDK, `-p`, REPL, RPC task, extension trigger) | `busy` | pi: `if (this.isStreaming)` throws, `:1654` |
| RPC `prompt` | the existing preflight is now truthful: error response, or queued with `streamingBehavior` — no RPC code change | pi `rpc-mode.ts:394-414` |
| `wait_for_idle()`, `ctx.wait_for_idle()`, runtime `reload()` | wait for the release | pi `waitForIdle()` resolves in `_emitAgentSettled()` |
| `dispose()`, runtime `new_session`/`switch_session`/`fork` (via `dispose()`) | abort (wakes the backoff, stops the tail), then wait, then write what a cancelled release left queued (item 7) | yes |
| `navigate_tree()`, public `compact()` | `busy` | pi's manual `compact()` aborts first (`:2406-2407`) — an existing divergence, not widened |
| `steer()` / `follow_up()` / `next_turn()` | enqueue (phase-independent), unchanged | yes |
| extension `ctx.abort()`, default `ctx.shutdown()` (no CLI shutdown action) | set the abort flag: no retry, no closing compaction after the run in which it was called; the running turn is not cancelled (as before) | closer to pi, whose `ctx.abort()` is `this.abort()` (`:3101-3106`) |
| extension `send_message(trigger_turn=True)` | `next_turn` queue (Aelix's mid-turn rule) | pi steers or follows up (`:1949-1954`) — kept divergence |
| TUI | unchanged: it awaits the whole `prompt()` with the chrome "running", so a line typed during the countdown was always steered (pty capture on the final tree, `.omc/specs/334-tui-countdown.py`: `Steering: BRAVO-LINE` on the glass, request 2 = #1's re-run carrying `['Reply with exactly: ALPHA', 'BRAVO-LINE']`, 2 requests) | pi steers too (`interactive-mode.ts:3257-3263`) |

### What this does not close

- **What a turn-triggering input does while the claim is held.** An
  extension's `send_message(trigger_turn=True)` goes on the `next_turn` queue
  (Aelix's rule), where pi steers it; a steer or follow-up typed during a
  backoff that the retry then abandons (Esc, max attempts, abort), or during the
  closing compaction, waits for the next prompt — pi continues the run while
  `hasQueuedMessages()` (`:1501`, `:1525-1527`, `_runBeforeSettleBoundary`
  `:1532`). That was already the TUI's case; it is now RPC `streamingBehavior`'s
  and extension triggers' too. **#345.**
- **`settled` stays per run.** It fires inside `_run`, so also after a failed
  attempt that will be retried, and before the closing checks; pi's
  `agent_settled` fires once per prompt at the release. Changing it is a
  behaviour change for extensions — **#346**, with the next item.
- **RPC has no "prompt finished" event.** `RpcClient.wait_for_idle` /
  `prompt_and_wait` resolve on `agent_end` (`rpc_client.py`), which a retried
  attempt also emits; pi's client waits for `agent_settled`
  (`rpc-client.ts:462-472`). A client that pipelines a prompt after a failed
  attempt's `agent_end` now gets `busy` where it used to be let into an overlap.
  The in-tree delegation channel runs one prompt per child. **#346.**
- **Neither RPC `abort` nor `harness.abort()` waits for idle** — a knowing
  divergence. pi's `abort()` sets the flag, aborts retry, compaction and branch
  summary, and then awaits `waitForIdle()` (`agent-session.ts:2075-2085` @ a328aa89a);
  Aelix's `abort()` returns once it has signalled, so `await harness.abort()`
  can return while the tail's last steps still hold the claim (Codex probe,
  `abort()` while the release flush is parked: `release abort: phase turn flag
  True`), and a `prompt` sent right after an RPC `abort` response can meet them
  and get `busy`. `dispose()` is unaffected: it awaits idle itself. **#346.**
- **`abort()` does not cancel a nested compaction** (pi's calls
  `abortCompaction()`, `:2080`). With the tail guard an abort during the
  compaction still stops the re-run after it, but the summarisation itself runs
  to its end, and `dispose()` waits for it. Unchanged by #334. **#347**, with
  the next point: `_try_overflow_recovery` swallows every compaction error but
  "Nothing to compact" at `DEBUG`, so a failed overflow compaction (or one
  refused for a missing claim — sabotage S3s) is silent.
- **Code that awaits idle from inside the tail now waits for ever**: an async
  `auto_retry_*` or `compaction_*` subscriber, or a handler of a tail
  compaction's hooks, that calls `wait_for_idle()` — or `dispose()`, or the
  runtime's `new_session` / `fork` / `switch_session` / `reload`, which wait for
  idle through it. In the backoff those used to go through at once (inside the
  compaction they already deadlocked). Review probe R4 (`wait_for_idle()` in an
  async `auto_retry_start` subscriber): `f014fb46` `prompt returned; calls 2`,
  now `prompt still running after 2 s`. No in-tree caller does; embedders can.
  Named in the CHANGELOG.
- **An `abort()` before the first run starts** (the `input` /
  `before_agent_start` window) is still cleared by `_run`'s entry — #336.
  The same root leaves a second gap: a re-run's `_run` clears the flag and then
  awaits `build_context()` before its turn task exists, so an `abort()` there
  cancels nothing and the re-run still goes to the provider (`dispose()` waits
  for that whole turn). Review probes R1/R5, identical on `f014fb46` and now:
  provider calls 2 after an `abort()` in the retry re-run's / the overflow
  re-run's `build_context()`. The JSONL and memory storages do not yield there,
  so in-tree storages cannot hit it; a custom storage can. Left with **#336**.
- **The retry bookkeeping of a cancelled backoff** (ARM 11: `_retry_attempt`
  left at 1, no `auto_retry_end`) — #335. The release now resets `_turn_state`,
  which closes #335's side note (a cancel during `_run`'s `finally` flush left
  it set), not **#335** itself.
- **An idle `set_thinking_level`'s direct append is not ordered by the
  drain** (pre-existing; Codex's second review, findings 1 and 6). When
  nothing is queued and no drain runs, an idle `set_thinking_level` appends
  at once, holding no drain lock. If a storage parks that append while a
  prompt runs and queues a newer level during its turn, the prompt's release
  drain writes the newer level first and the older one lands last, so a
  resume restores the older level. Measured with the real `_run` (a
  main-loop probe, described on #314 in comment 5822607662: a
  `MemorySessionStorage` subclass that parks the `medium` append, a provider
  that sets `low` mid-turn), identically on `main` `53e80343` — whose
  `_run` `finally` flush does the same — and on this amendment: `on disk at
  the end: ['low', 'medium']  live='low'  restores='medium'`. Only a custom
  storage that yields inside an append can reach it; the JSONL and memory
  storages do not. The drain does not close it and makes it no worse; one way
  to close it is to make the idle direct append under the drain lock too.
  **#314** carries this case.
- **A hung storage append holds a cancelled prompt at its release.** The
  trade-off of item 7: a prompt cancelled while its release waits behind a
  drain whose append never returns stays in phase `"turn"` (a second cancel
  releases it, and what is still queued stays queued for the next drain).
  Before round 4 one cancel released at once and wrote out of order.
- **Storages must not call back into the session-writing API from inside an
  append** (item 7's storage contract; such a storage deadlocks on the drain
  lock). Stated, not enforced.
- **RPC back-to-back prompts in one read.** Each JSONL line gets its own
  dispatch task, so two `prompt` lines read in one chunk can both pass the
  preflight before the first task flips the phase; the second then raises
  `busy` inside its task after a success response (critic note, read, not
  measured; pre-existing). "The preflight is truthful" holds for the tail only.
  **#346.**

### pi

At `a328aa89a`, `_runAgentPrompt` (`agent-session.ts:1468-1490`) sets
`_isAgentRunActive = true` (`:1470`) and holds it across `agent.prompt()`,
every `agent.continue()` (`:1476`, `:1481`), `_handlePostAgentRun`
(`:1492-1528`: `_prepareRetry` `:3379` and its sleep `:3409`; `_checkCompaction`
`:2599`) and `_runBeforeSettleBoundary` (`:1531-1552`); only the `finally`'s
`_emitAgentSettled()` (`:1488` → `:870-872`) clears it and resolves
`waitForIdle` (`_resolveIdleWaitIfIdle`, `:860-868`). That is this amendment's
shape: the claim, the release after the flushes, `wait_for_idle()` resolving at
the release, and the abort flag read after the tail's awaits — except a
re-run's own `build_context()`, after `_run` has cleared it (above, with #336). pi's harness
declares a `"retry"` phase it never uses (P-15) and its RPC has no
`isRetrying`; Aelix adds neither — the backoff is `"turn"`. Kept divergences:
the claim is taken before the input hooks (#321); a tail compaction reports
`"compaction"` (pi: `isStreaming` and `isCompacting` both true, `:1229-1231`,
`:1293-1300` — the same on the RPC wire); `trigger_turn` queues rather than
steers; `settled` is per run.

### Tests

`tests/test_harness_prompt_holds_the_turn_through_its_tail.py`, 44 tests, and
`tests/rpc/test_rpc_prompt_during_a_retry.py`, 5. The idle event is swapped for
a recording subclass, so "no `set()` since the prompt's entry" and "exactly one
`set()`, at the release" are exact, timer-free assertions, and a
`wait_for_idle()` parked before the prompt is proven parked (#321's N3
technique). The headline test parks at seven points of the tail — the retry
backoff, the `auto_retry_end` emit, the overflow branch read and compaction,
the threshold branch read and compaction, and the release flush — and at each
asserts the phase, the clear event, no `set()` so far, the parked waiter, and
`prompt()` / `compact()` / `navigate_tree()` refused `busy` with the state
unchanged after the refusal. Every test that ends idle also asserts the
release dropped the claim and the turn state. On `f014fb46` (the new files run
with its packages first on `PYTHONPATH`): **43 failed, 4 passed** — the four
green there by construction are the cancel-in-the-backoff release, the
`set_thinking_level` arm of the settings test (appended at once on the base),
the RPC nested-compaction `get_state` (the base flipped to `"compaction"`
too) and the silent-overflow compaction's reason (the base compacted on an
idle harness); each pins a guarantee a sabotage below reddens. Item 7's six
tests (a newer idle level, the next prompt, `dispose()`, a public `compact()`,
`navigate_tree()`, and a write made while a drain is appending) are red on the
#334 commit before item 7 (its `aeea1074` packages first on `PYTHONPATH`):
**6 failed, 37 passed**, e.g. `assert ['low'] == ['medium']`, and the next
prompt's `[['first'], [], ['next'], ['answer 2'], 'medium']`. Round 4's four
(a prompt and a tree navigation entering while an idle drain appends, a prompt
cancelled at its entry drain, and a second cancel while its release waits for
the drain) pin the drain lock's two remaining edges: the two R1 tests are red
on the round-3 commit `000bc8f5` (its agent-core first on `PYTHONPATH`: **2
failed, 45 passed**, `AssertionError: the cancelled prompt's release wrote x
ahead of the drain's medium and low` — `assert ['x'] == []`) and under S19;
the two entering tests are green there (the fast path's lock check is round 3's
code) and red under S20 (the verification's M3, which had left all 146 green).
Round 5's two pin what the round-4 verification and the second Codex review
found unpinned: a release whose own queue is empty still waits for the drain
appending its write (red under S21, the verification's M10, which had left
all 150 green), and the drain's empty fast path finishes on its first `send`
when driven by hand (red under S22, which had left the #334 files green,
`47 passed`). Neither was run against an earlier commit; the red counts
above were measured on the files as they stood in their round (47 tests for
the `f014fb46` and `000bc8f5` runs, 43 for `aeea1074`).
The re-run-fails-to-start
test is red there not for a missing release (#321 gave the phase back) but
because the idle event was set twice: `['idle', 'idle']`. Sabotage (a copy of the fixed
`core.py` first on `PYTHONPATH`, these two files plus #321's, #311's,
`test_overflow_recovery.py`, `test_compact.py`, `test_auto_retry.py`,
`test_input_emit.py` — 152 tests, round 5's counts; `.omc/specs/334-sabotage.py`):

| id | break | red |
| --- | --- | --- |
| S1 | `_run`'s `finally` sets idle again | 39 — every tail point, the RPC file, the context-sharing and dispose tests, item 7's six, round 4's four, round 5's release-waits-for-the-drain test |
| S2 | the nested `finally` hands `"idle"` and sets the event | 6 — the four compaction/read points, both nesting tests |
| S-b | the nested `finally` hands `"turn"` but sets the event (critic) | the same 6 |
| S3 | the tail calls `compact()` without its claim | 10 — incl. `test_real_compact_emits_overflow_reason_and_will_retry` and the silent-overflow test |
| S3s | only the silent-overflow `compact(reason="overflow", will_retry=False)` without its claim (round 2's verification sabotage M4b, which reddened nothing then) | 1 — the silent-overflow test: without the claim the compaction is refused busy in silence and the threshold check compacts with reason `"threshold"` |
| S4 | no release flush (the release's drain call dropped) | 16 — the release-flush point, the settings test's two arms, the writes-during-the-flush test, `test_a_prompt_cancelled_in_its_release_flush_still_releases`, item 7's six, round 4's four, round 5's release-waits-for-the-drain test |
| S5 | the release does not set the event | 45 |
| S6 | the release drain outside the nested `finally` | 12 — the cancel-in-the-flush test, item 7's six, round 4's four and round 5's release-waits-for-the-drain test (they all cancel there) |
| S7 | nest on any token, not `is self._claim` | 1 — the stale-claim test |
| S8 | the busy guard inside the `try` | 9 — every tail point and both nesting tests |
| S9 | a one-pass drain (round 4: the release's loop is the drain's `while`) | 3 — the writes-during-the-flush test, the second-cancel test and round 5's release-waits-for-the-drain test (the drain in flight must loop round to `x`) |
| S10a | drop `prompt()`'s retry-loop abort check | 2 — the extension-abort-stops-the-retry test (`turn_end`, `settled`) |
| S10e | drop `prompt()`'s check after the overflow compaction | 1 — its own test |
| S10b | drop the backoff's `retry_aborted` check | 1 — the async `auto_retry_start` subscriber test |
| S10c | drop the threshold check's post-read check | 3 — the abort-in-the-read, dispose-in-flight and extension-abort-over-the-threshold tests |
| S10d | drop the overflow recovery's post-read check | 1 — its own test |
| S11 | the release does not reset `_turn_state` | 1 — the cancel-in-`_run`'s-closing-flush test (#335's side note) |
| S12 | the release does not drop `_claim` | 37 — every test that ends idle through the release's assertion |
| S13 | an idle `set_thinking_level` appends at once past a non-empty queue (C4 (a)) | 7 — the newer-level and drain-in-flight tests, round 4's four and round 5's release-waits-for-the-drain test (their precondition is an idle setter's drain in flight) |
| S14 | no drain at `prompt()`'s entry | 5 — the next-prompt test, the prompt-entering-during-a-drain test, both R1 tests, round 5's release-waits-for-the-drain test |
| S15 | no drain in `dispose()` (C4 (b)) | 1 — the dispose test |
| S16 | the drain takes no lock | 7 — the drain-in-flight test, round 4's four and round 5's two |
| S17 | no drain at a public `compact()`'s entry | 1 — the compaction test |
| S18 | no drain at `navigate_tree()`'s entry | 2 — the tree-navigation test and the navigation-entering-during-a-drain test |
| S19 | the release flushes without the drain lock (round 3's loop; verification R1) | 3 — both R1 tests and round 5's release-waits-for-the-drain test (its own queue is empty, so the unlocked loop does not wait) |
| S20 | the drain's fast path checks the queue alone, not the lock (verification M3) | 6 — both entering-during-a-drain tests, both R1 tests and round 5's two |
| S21 | the release drains only when its own queue is non-empty (round 4's verification sabotage M10, which left all 150 green) | 1 — round 5's release-waits-for-the-drain test |
| S22 | the drain suspends once (`await asyncio.sleep(0)`) before its fast path (the second Codex review's finding 5) | 1 — round 5's fast-path test |

`tests/test_harness_cancel_gives_the_phase_back.py` keeps #321's seven
single-call tests (9 cases, plus a `raise` arm for the `build_context` point:
10, of the 23 it collected on `f014fb46`) and loses the nine two-prompt tests
(14 cases): each needs a second `prompt()` accepted in the
tail, and there it is now refused — their precondition is gone, not their
guarantee; a note where they were maps each to its replacement.
`tests/harness/test_overflow_recovery.py`'s compact spy accepts `_claim`, and its
fake `_run` no longer sets the phase idle "so the subsequent real `compact()`
passes its idle busy-guard" — that comment encoded the defect.
`.omc/specs/321-cancel-probe.py` stays a record: its two-prompt ARMs (7's second
half, 15-24) now fail at #2's entry with `busy` (measured for 7, 18, 18h, 19,
23c: `AgentHarness is busy (phase='turn')`). ARM 7's last line, `#3 prompt() now :
TIMEOUT`, is not a wedge: that probe's provider parks its call 2 — #2's, when
#2 got in — and with #2 refused, call 2 is #3's, parked until the ARM ends
(`after #2 finished : phase='idle' idle_event=True`). `.omc/specs/334-arms.py`
A7, whose provider parks nothing, prints `next prompt() : ACCEPTED`. ARM 12 never sends #2: it parks
the threshold branch read that runs with the phase idle, none does now, and it
times out at its `gap_entered` wait (`321-cancel-probe.py:577`). The
`.omc/specs/334-*.py` drivers re-measure every number here: the tail probe, the
ARMs, the critic probe, the sabotage table, the live check, the TUI countdown
(`334-tui-countdown.py`) and the `-p` Ctrl+C check (`334-pty-ctrl-c.py` with
`334-park-ext.py`).
