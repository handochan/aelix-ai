# 0023. Compaction + Branch Summary

Status: **Accepted (Sprint 4b / Phase 2.2.2 shipped)** — **AMENDED 2026-09-24 by
#321** (`## Amendment (2026-09-24, #321)` below): a cancelled call gives the
phase back, and only a turn it set.
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

1. Guard busy (raise `AgentHarnessError("busy")` if not idle).
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
9. `finally`: restore `phase = "idle"`.

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
