# 0307. A cancelled delegation's report is its call's result, and a session change settles the turn first

*Since #612's lane L2 also: an RPC host sees and steers the delegated children (D4–D8).*

Status: Accepted (2026-11-19, #800). D1–D3 and the known limits K1–K6 are decided and implemented
here (lane L1 of #612's contract r5). Amended 2026-11-19 (#612, lane L2): D4–D8 — the `children/`
family and its admission, the `aelix_child` and `aelix_notice` events and the hub behind them,
replay within one hub generation, owner Q4's flips with the session lifetime and the ended-owner
epoch, and the aelix-acp contract (whose half is the aelix-acp PR's, lane L3, not in this
repository) — and K7–K9 are added. "Where the build differs from contract r5" lists what lane L2's
implementation changed of r5, and the lead's calls on them (2026-11-19). Amended 2026-11-20 (#612,
lane L3): D8 is built by aelix-acp PR #39 (`03b9e32`), with seven things r5 did not propose; a
children control in flight is ended when the worker's close begins, not when a restart begins; K5
and K7 are lowered, and parts of D4–D8 were run live through ACP on macOS during the lane, at
three of its local checkpoints before its final commit — "Note (2026-11-20, #612)" says which row
ran at which checkpoint, measured how, and what was not run. Noted 2026-11-22 (#852): K8 is
replaced by ADR-0317.
Amended 2026-11-22 (#857): `children/notices` also answers `placedInTurn`, every notice its turn
placed, boundary drains included; `placed` is unchanged. See the note "(2026-11-22, #857)".
Amended 2026-11-22 (ADR-0318, #836): the `children/` family gains `children/resume`, a host's resume
of a stopped agent the model delegated. See the note "(2026-11-22, #836)".
Amended 2026-11-22 (#849):
a seal kept past an interrupted swap is spent when its settle ends, so a hold that saved and
restores it puts back the gate it replaced (D3, and K4 loses that case); the note of 2026-11-22
says how it was measured. Its fix round 1 leaves a gate up that went up after a hold that ends; a
later round forgets the retired gates once no gate is up.
Date: 2026-11-19
Issue: #800 (a cancelled delegation's report does not reach an ACP client and is gone after a
reopen), taken into #612's lane by the owner (2026-11-18: "네 진행하세요"; lane b7f landed first).
#800's body records Studio's user decision of 2026-11-17: "beta.3에 들어오면 Studio 첫 Beta에
표시를 넣고, 아니면 다음 release로 넘긴다." D4–D8: #612 (delegated children on RPC and ACP
hosts). Owner, 2026-11-18: r2 Q4 **"전부 열기 (권장)"** (the background actions, the `job` tools
and `run_in_background` on RPC, with a wake for the host); r3 Q1, host resume: **"#612에서 빼기
(권장)"**; reconnect replay: **"같은 프로세스 + 영속 기록 (권장)"**.
Design: `.omc/specs/612-contract-r5-2026-11-19.md` §5 (#800), §10.1, §10.3, §10.4 and §12.3 (L1);
§3, §4, §6 and §8 (L2's D4–D8). The known limits are narrowings under the lead's rule for r5 (lead's
call, r5 brief 2026-11-19): a case outside the guarantee keeps the base's behaviour and is stated,
never silently lost. Probes: `.omc/probes/612/design-r5/` (`l1/l1_owed_probe.py`,
`swap/swap_probe.py`, `giveback/giveback_probe.py`), `.omc/probes/800/impl/`, and lane L2's
`.omc/probes/612/impl/A/`, `.omc/probes/612/impl/B/` and `.omc/probes/612/impl/C/live/`.
Amends: ADR-0290 §8 and limit 8, ADR-0276 D14 (items 2–3) and its limit "The Esc report lives in
memory", ADR-0278 D7, ADR-0081 P-340 (the teardown order), ADR-0286 (what a swap gives back) (L1);
ADR-0306 D8's RPC rows, ADR-0293, ADR-0295 and ADR-0297 (what RPC and ACP refused "until #612"),
ADR-0302 D9 (#612 is the first lane added under it), ADR-0275 §2 (its lock rules gain the
`children/` methods), ADR-0277 (the host surface still resumes nothing), ADR-0278 D3 (what a host
reads of a child's calls and text) (L2). Each carries a dated
note.
Relates: ADR-0235 (pi is the reference, not a parity target), ADR-0306 (the delegated child's
command inbox and #123's doors, which D4 exposes), ADR-0308 (the approval back-channel, whose
steps ride D5's `approval` event), ADR-0277 (resume), #612.
pi: `earendil-works/pi` `origin/main` `5a14e69c0`, `packages/coding-agent/src/core/agent-session-runtime.ts`
`teardownCurrent` (`:186-197`) and `dispose` (`:434-443`), which begin with `await this.session.abort()`.

## Context

At `6dd682cd`, when a turn was cancelled while the `agent` tool ran, the tool's
`except asyncio.CancelledError` rendered what the children had done (`render_cancel_report`) and
queued it with `api.send_message(text, trigger_turn=False)`, then re-raised the bare cancel, so the
loop wrote its default cut text as the call's result (ADR-0290 §8). The queue is in memory:

- an RPC host and an ACP client saw only the cut text as the call's result, live and after a reopen
  (contract r3 measured it, `.omc/probes/612/design-r3/p800/result-*.json`, `acp-result-base.json`;
  #800's own report from Studio is the same through aelix-acp);
- a session reopened before the next prompt had no report;
- a session change after an Esc handed the queued report back as withdrawn input (ADR-0286's
  `input_withdrawn`), so the TUI put it in the input box — measured at the base by
  `giveback/giveback_probe.py` (one `input_withdrawn` event carrying the report) and in the real TUI
  (the input box read `[Pasted text #1 +23 lines]` after Esc then `/new`; `.omc/probes/800/impl/live/`);
- a session change during a delegation (`/new` over RPC, a switch, a quit) emitted `session_shutdown`
  before anything aborted the turn: the agents extension's handler stopped the children, the `agent`
  call returned an ordinary envelope, the old harness asked its model again, and only then did
  `dispose()` abort it (ADR-0081 P-340's order). pi aborts first.

## Decision

### D1. The cancelled `agent` call's result is the report

In `_execute_pending`'s `except asyncio.CancelledError` (`aelix_agents/extension.py`), in this order:
take the envelopes' snapshot (as before), render the report inside the existing containment (a
renderer that fails gives no text), draw the Esc frame from that snapshot (`bridge.linger`, ADR-0278
D7), record the report as owed (D2), then raise
`ToolCancelled(ToolResult(content=[TextContent(text)])) from cancel` — or the bare cancel when there
is no text (no child had a process, or the renderer failed). `ToolCancelled` is a `CancelledError`
(ADR-0290 §2), so the turn ends as before and the loop writes the report as the call's error result,
to the session file. Nothing goes on the next-turn queue any more, except from a harness with no
session (an embedder's: the CLI's `--no-session` has an in-memory one): with no session to write the
result to or check it against, that call queues the report with `send_message(trigger_turn=False)` and
re-raises the bare cancel, as at the base (K1; fix round 1, Codex review C-6). `CANCEL_REPORT_HEAD` is
unchanged: "not user input" holds for a tool result too.

### D2. The owed record, and `loop.CUT_TEXT`

The report is also recorded as `OwedReport(call_id, session, text, origin, copies)` in
`AgentsExtension._owed`, keyed by the cancelled call's `toolCallId`, synchronously. `session` and
`origin` are read when the call begins (`_call_origin`): the session it runs in and that session's
leaf, after which its result is committed. At every `before_agent_start` (a prompt and a notice
turn), `_owed_messages()` checks each record in order against the session's committed branch
(`get_branch()`, the entries the next request is built from):

1. the record's session is not the live one: retired, nothing sent;
2. the first `toolResult` of the `agent` tool for `call_id` after `origin` is not `loop.CUT_TEXT`:
   retired — the carrier landed, whatever a `tool_result` or `message_end` hook made of it. An
   earlier result under a reused id, or another call's, is not the carrier (Codex review C-7);
3. a copy this record injected is on the branch: retired. A copy is placed by the session entry it
   was committed as, not by its text — its `message_start` hook reads the leaf, and the next
   `message_start` or the run's `agent_end` reads it again (`_Copy`) — so a `message_end` hook that
   rewrote the copy does not bring it back with every prompt (Codex review C-5). A copy committed as
   the branch's first entry (the leaf had been moved to the root, so no entry was before it) is
   placed too (verify round 2 F1: on `9acd9ea0` the next prompt sent it again);
4. otherwise the report goes out as a user message ahead of the prompt (the base's message, in the
   base's place) and the record stays.

Only 1–3 retire a record, so a later `before_agent_start` handler that raises, a copy whose append
fails, or an Esc that withdraws the copy leaves it for the next check. The helper returns at once when
nothing is owed. A branch that cannot be read fails the prompt before anything is sent, with a message
saying so, and every record stays (Codex review C-8: a check that read nothing injected the copy beside
a carrier the request then read). At each `agent_end` a record whose report is not placed is told,
naming the call (Codex review C-9): the report reaches the model at the next prompt in this process,
and a reopen before that has none. It is told at DEBUG, and in one line on stderr only when the run
has no UI (`ctx.has_ui`; a stale context counts as none, K4), as `runtime.py`'s `_report_unfinished` tells an unfinished cleanup: the tree
installs no logging handler, so the WARNING fix round 1 logged reached `logging.lastResort` and was
painted into the live TUI's input row (verify round 2 F3, #546). The loop's cut sentence is public as
`aelix_agent_core.loop.CUT_TEXT`; `_CUT_TEXT` stays as its alias.

### D3. A session change or a quit seals the outgoing harness and settles its turn first

`AgentSessionRuntime._teardown_current` (every reason but `reload`, which waits for idle before it)
and `AgentSessionRuntime.dispose` begin with a step 0, `_seal_and_settle`, when the outgoing harness
is not idle:

- **seal**: `harness.hold_turns(TurnSeal(...))` — no prompt and no notice turn starts on
  it from here ("the session is being replaced"; "the session is being closed" for a quit). An
  extension's `send_message(trigger_turn=True)` under this seal (a `session_shutdown` handler's) is
  queued, as it was while the turn ran at the base: it claims no prompt and runs no
  `before_agent_start` handler (Codex review C-3). A session change hands it back after the teardown
  (#681); a quit leaves it queued on the disposed harness, unsent, as the base did (verify round 2
  F6: its probe H3 gives `withdrawn=[] still_queued=[…]` for a quit on the base and on this lane). `TurnSeal` is a `str`
  subclass defined at the end of `harness/core.py`, so no line the repository cites there moves; a
  plain gate (the CLI's holds, `stop`) still runs the refused turn of #367;
- **settle**: `harness.abort_until_idle()` (the input is kept, as `dispose()` keeps it), waited for at
  most `SWAP_SETTLE_SECONDS` = 10.0 s — the first abort plus `_REABORTS` 4 × `_REABORT_GRACE_SECONDS`
  2 s, and margin — with `asyncio.wait`, outside the first `abort()` (which awaits the `abort` hook
  and the queue listeners). Past the bound the settle is left running (it only aborts that harness)
  and the teardown goes on.

Then the order is ADR-0081 P-340's: `session_shutdown`, invalidate, `harness.dispose()`. The veto
(`session_before_switch`, `session_before_fork`) runs before the teardown, so a vetoed swap never
seals or aborts. A swap or quit interrupted before the invalidation — in step 0 or inside an awaited
`session_shutdown` handler — did not happen: `_abandon` stops the settle and waits for it (at most
`SWAP_SETTLE_SECONDS`), then puts back the turn gate the harness had before the seal, so the harness
left installed takes prompts and no re-abort of that settle reaches them (Codex review C-2). A settle
still alive past that bound — an `abort` handler that swallows its cancel — keeps the seal until it
ends: meanwhile the harness refuses a prompt with the seal's reason, and when the settle ends the
gate goes back, only if it is still the seal, so a hold set since is kept (`_unseal_when_settled`;
verify round 2 F2: on `9acd9ea0` the gate went back at the bound and the settle's re-abort ended the
next prompt). The seal is spent when its settle ends, so a hold set meanwhile that saved it and
restores it after that end (`LateRouteHold.apply`) puts back the gate the seal replaced, and a hold
that ends while a seal or another hold put up after it is still up leaves that gate up (note of
2026-11-22, #849; until then this was K4's exception).

A switch to the file the outgoing session is on opens it again after the teardown and before the
install (`_finish_session_replacement(reopen=…)`, the caller's own way of opening that file), so the
installed session continues from what step 0 and `dispose()` wrote; so does an `import_from_jsonl` of
that file, when no copy was made (Codex review C-4). Files are compared resolved
(`resolve_session_path`). It is opened again only when this process may write it: a runtime that
manages writer locks must hold that file's lock (`_reopen_same_file`), so a contended file's "read
only" answer and its "fork and continue" answer are never replaced by a writable open of the file
another writer holds (Codex review C-1). A second open that fails is told — at DEBUG with the
exception, and in one stderr line only when the outgoing harness had no UI, read before the teardown
(verify round 2 F3) — and the swap goes on with the first, as at the base. This follows pi's `teardownCurrent` and `dispose`; it diverges from
aelix's earlier port of them (ADR-0081 P-340), not from pi.

### D4. The `children/` family: a host's view of the delegated children, and #123's doors (#612)

Six `aelix_control` methods, one `_families()` entry (`"children"`, ADR-0302 D9), handled in
`rpc/control_children.py` (contract r5 §3, a proposal; `children/notices` is owner Q4's wake):

| Method | Params | Answer | Admission |
| --- | --- | --- | --- |
| `children/list` | none | `{epoch, children: [Child]}` | `READ_ONLY_METHODS` (lock-free) |
| `children/events` | `id`; `afterSeq` (integer, optional: absent = from the oldest kept) | `{epoch, events: [{event, data}], nextSeq, lowSeq, gap}` | `READ_ONLY_METHODS` |
| `children/steer` | `id`; `text` (at most 100000 characters); `mode` (`steer` \| `follow_up`, optional: absent = `steer`) | `{outcome, phase, verb, ended, reason}` | `CONCURRENT_METHODS` (new) |
| `children/wrap_up` | `id` | the same, `verb` `wrap_up` | `CONCURRENT_METHODS` |
| `children/stop` | `id` | `{state}` | `CONCURRENT_METHODS` |
| `children/notices` | none | `{started, placed, reason, remaining}` | `MUTATING_METHODS` and `CANCELLABLE_METHODS` |

- **A row** (`Child`) is `{id, profile, state, end, currentTool, elapsedMs, tokens, cost, model,
  provider, activity: [{name, args, status}], lastText, stuck, wrappingUp, approval, seq, lowSeq,
  sessionId, jobId, parentToolCallId, background}`: `id` is the row id `aelix_subagent.data.id`
  carries, `sessionId` the child session id `agent(action="resume")` takes (null until the receipt
  is allocated), `jobId` set for a background row, `parentToolCallId` the `agent` call that started
  it (null for `agents/run`), `approval` `{callId, toolName}` while a request of ADR-0308 is open.
  Field names are camelCase (ADR-0305's style).
- **The doors** are #123's (ADR-0306 §3), reached through one optional seam:
  `subagent_contract.SubagentControls` (`epoch`, `list`, `events`, `steer`, `wrap_up`, `stop`,
  `subscribe`, `close`), which `aelix_agents/host_controls.py`'s `HostControls` implements over the
  build's hub (D5) and which the agents extension binds as `controls` on the runtime it binds, one
  per harness build. No member is added to `SubagentRuntime`, so a third-party runtime keeps
  binding; product core reads `getattr(runtime.subagents, "controls", None)` and imports nothing of
  `aelix_agents`. Every door is asked with `origin="user"` (a proposal): a host's request is a
  person's action in that host, so a host's wrap-up ends "wrapped up at the user's request".
- **Answers.** A delivery's `outcome` is `queued` (with `phase` `streaming` or `idle`), `unknown`
  (`ended: true` and a reason naming `action="resume"` when the child ended with the line open) or
  `not_sent`; a `refused` delivery is an error response with its reason (ADR-0302 D2.3) — "the agent
  has not started yet; retry", "the agent is stopping", "the agent's inbox is broken; stop or
  resume it", "a wrap-up was already requested for this agent", ADR-0306's resume hints for an
  ended row, "no agent '<id>' in this session" for an id this build never held. `children/stop`
  answers the state the hub holds when its door returned, or at `CHILD_STOP_ANSWER_BOUND` = 10 s
  while the stop goes on; the end is the terminal `aelix_child` `state` frame. A foreground row's
  door is `runtime.stop`; a job row's is the job service's stop, asked by the user, which returns
  before the job's stop legs, so it usually answers `running`. An open approval request is withdrawn
  first (ADR-0308; the lead's call after part A).
- **`children/notices`** is the `/jobs send` door, `JobService.send_waiting()`, then a notice turn,
  answered with `PromptNoticesResult`'s `started`, `placed`, `reason` (`null`, `held`, `disposed`,
  `busy`, `nothing`, `refused`) and **`remaining`**, the inbox's pending count after the turn.
  `placed` counts what the turn placed before its first model call, one 64 KiB drain budget; each
  boundary inside the turn claims another budget, so one call can place more than `placed` says
  (`PromptNoticesResult`'s own definition, `harness/inbox.py`). `remaining > 0` is the host's cue
  to call again. (Fix round 1, Codex C-M9, measured two 65,436-byte notices placed by one call that
  answered `placed: 1, remaining: 0`; the lead's call: one budget shared across the notice turn
  would change the kernel's boundary drain for every host's notice turn, not only RPC's, so the
  text is narrowed to what is built, and contract r5 §3.1's "one 64 KiB budget per call" does not
  hold.) It holds the lock for the turn, as `agents/run` does, and `commands/cancel` cancels it.
- **Capabilities** list the five delegation methods when the bound runtime has `controls`, and
  `children/notices` when the plane was given a job service (`jobs=`), independently: jobs on with
  agents off lists `children/notices` alone. Without `controls` the five refuse "This RPC host has
  no delegated agents"; without a job service `children/notices` refuses "This RPC host has no job
  service".
- **Admission** (contract r5 §3.4). `RpcControlPlane.handle` sends a `CONCURRENT_METHODS` request to
  `_concurrent` before the `MUTATING_METHODS` test and the lock, so it is answered while
  `agents/run` holds the lock. `_concurrent` refuses while the host closes ("The RPC host is
  shutting down") or while a control lifecycle method (`transitioning`) or a classic swap
  (`classic_swapping`) runs ("a session change is in progress; retry when it has finished"), and
  records the request with the harness **generation** that admitted it and that generation's
  `controls`, the only ones it ever calls. Interventions are not `_Operation`s, so `commands/cancel
  {}` (what aelix-acp sends on `session/cancel`) never reaches one.
- **Settled when a change begins.** `transition()` and, for a classic `CLASSIC_SWAPS` command,
  `settle_classic` await `settle_interventions()`: each admitted request is cancelled and waited for
  at most `SETTLE_GRACE_SECONDS` = 1 s. In `transition()` the settle runs inside the same shielded
  step as the replacement, so a `commands/cancel` answered `deferred` that lands during the settle
  no longer drops a command's guarded reload (fix round 1, Codex C-M2: at `111e02d7` the factory
  was never entered;
  `tests/rpc/test_children_family_612.py::test_a_deferred_cancel_landing_in_the_settle_still_lets_the_guarded_reload_finish`). A cancelled steer or wrap-up answers `unknown` with "a
  session change began before the agent answered" — the line may have been read, since nothing
  awaits before the door writes — except a wrap-up cancelled while the `cancel` of an open
  approval request waits for its acknowledgement, which answers the error "a session change
  began before this request was sent" (fix round 1; "Where the build differs", item 2); a stop
  answers the state the hub holds.
- **Fencing is not retirement.** The settle does not close the controls. At the rebind
  (`bind_progress` → `_bind_children`) the generation is raised, the old hub's subscription ends,
  the new one is subscribed and the old `controls.close()` makes every later intervention on them
  refuse "the session changed" while reads go on. A change that never replaces the harness — a
  vetoed classic `new_session`, `switch_session` or `fork`, a control branch that rejects its
  params — never rebinds, and admission resumes on the same generation. Frames the old hub emits
  during the teardown (the terminal states of the children it stopped, ADR-0308's `withdrawn`
  steps) reach the host until the rebind.
- **Every shutdown cancels them.** `begin_shutdown` cancels the interventions in flight (answering
  `unknown` with "the RPC host began shutting down before the agent answered"), and
  `run_rpc_mode`'s `finally` calls it first, so a cancelled `run_rpc_mode` cancels them too. The
  children's hub subscription outlives the other event channels there: it ends, and the controls
  are retired, only after `runtime_host.dispose()`, so the terminal `state` frames of the children
  the disposal's `session_shutdown` stops reach a host that half-closed its input and still reads
  (fix round 1, Codex C-M4: at `111e02d7` the wire held only the earlier `running` frame;
  `tests/rpc/test_children_family_612.py::test_input_eof_still_sends_the_terminal_frames_the_disposal_publishes_then_retires`).
- **`CommandWire.request` drops its correlation entry when cancelled** (contract r5 §3.4; Codex
  measured one left behind at the base) **and drains its write under `asyncio.wait`**, not
  `asyncio.wait_for`: on Python 3.11 `wait_for` returned the drain's result when a cancel landed in
  the drain's last turn (CPython gh-86296), so a settled request waited for its answer — measured
  on 3.11.15 ("NOT raised"; raised on 3.12.13 and 3.14.5, `.omc/probes/612/impl/A/wait-for-311.txt`).
  The lead's call after part A: this is a fix, not a limit.

### D5. `aelix_child` and `aelix_notice`: new event types, and the hub behind them (#612)

`aelix_subagent` is unchanged (its `event` is a closed enum, ADR-0302): every new kind of frame
rides two new types, which aelix-acp `16d6555b` drops silently (its event chain has no branch for
an unknown type), so a host on today's adapter sees what it saw. *Amended 2026-11-20 (#612, lane
L3): aelix-acp PR #39 (`03b9e32`) reads both — `aelix_child` onto the parent card and, for a client
that opts in, as nested tool calls; `aelix_notice` as `_aelix/notice` for a client that declares
`aelix/notices` (Note (2026-11-20, #612)).*

- **`{"type": "aelix_child", "event", "data": {id, seq, epoch, …}}`**, `event` closed to seven
  values (`subagent_contract.CHILD_EVENTS`): `state` (`state`, `profile`, `sessionId`, `jobId`,
  `parentToolCallId`, `background`, `wrappingUp`; on the terminal one also `end`, `outcome`,
  `reason`, `note`), `tool_start` (`callId`, `name`, `args`), `tool_end` (`callId`, `status` ∈
  `ToolEndStatus` = `ok`, `failed`, `no_result`), `text` (`text`), `stuck` (`stuck`, an open set:
  `repeat`, `error loop`, or null when cleared), `wrap_up` (`requested`: `true` when recorded,
  `false` when a `not_sent` write took it back) and `approval` (`callId`, `toolName`, `step` ∈
  `ApprovalStep` = `asked`, `allowed`, `denied`, `withdrawn`; ADR-0308). **`end`** ∈ `ChildEnd` =
  `completed` (outcome `ok`), `wrapped_up` (the `aborted` a consumed wrap-up made of an `ok` run),
  `stopped` (a stop door ran on the row), `cancelled` (the parent's turn cancel), `failed` (every
  other end: `error`, `timeout`, `declined`, an abort the child made itself, and a run that left no
  envelope, whose `outcome`, `reason` and `note` are then null). The terminal fields come from the
  envelope the runtime built, with `_wrap_up_verdict`'s precedence (`envelope.child_end`). The
  wrap-up is weighed after the run's own verdict, so a consumed one turns only a run that would be
  `ok`; the child's own abort, an error or a non-zero exit keep their status and reason and end
  `failed` (fix round 1, Codex C-M8: at `111e02d7` an `aborted` stream and an `error` stream exiting
  1 both became `wrapped_up` once the final steer was read;
  `tests/agents_ext/test_child_events_612.py::test_a_consumed_wrap_up_never_masks_the_childs_own_failure`).
- **The hub** (`aelix_agents/child_events.py` `ChildEventHub`): one per harness build, its `epoch`
  a uuid, handed once to every background run the build starts (`jobs_client._Launch.hub`), so a
  spawned or moved row feeds the same hub as a foreground one. It is fed only from
  `runtime._publish`, the sole producer of a child's progress: the snapshot's fields and the
  reducer's transition list (`_StreamState.transitions`, immutable `start`/`end` records appended
  before the display window trims). It emits only on a change. A child keeps a ring of its latest
  256 frames; an ended child keeps its ring until 64 ended rows accumulate, then leaves a tombstone
  `{id, state, end, sessionId, jobId}` (at most 1024 kept). It tracks at most 1024 open calls per
  child, and a start it cannot track (past that, or with no call id) is not emitted, so **every
  emitted `tool_start` has exactly one `tool_end`**: its own, or a synthetic `no_result` at a turn
  boundary or the run's end, in start order, before any frame of the next turn.
- **The identity frame comes at once**: at the first snapshot, at the receipt that names the child
  session, and at a Ctrl+B move that names its job, without waiting for the child to speak. The
  two later ones feed the hub only (`runtime._publish_identity`), not the other taps of
  `_publish`. A host sees **exactly one terminal `state` frame**, the one with `end`: a terminal
  state the row reaches before its final publish is held back, and a non-terminal `state` frame
  carries no `end`, `outcome`, `reason` or `note`.
- **Every projected string is capped**, control-stripped to one line with lone surrogates replaced
  by U+FFFD: 120 characters for `currentTool`, `name`, `args`, `toolName`, `model`, `provider`,
  `profile`; 512 for `text`, `lastText`, `reason`, `note`; 32 for `stuck`; `activity` holds at most
  four entries. A call id goes through `wire_call_id`: the id itself when it is at most 128 bytes
  **as the wire writes it** (JSON-escaped with `ensure_ascii=False`, UTF-8, without its quotes: a
  control character is six bytes, a quote or backslash two), encodable, and does not start with
  `h:`; otherwise `h:` and the first 32 hex digits of its sha256, so a literal short id can never
  equal another id's hashed rendering (fix round 1, Codex C-M6: at `111e02d7` the budget was the
  raw UTF-8 length, and 128 NULs were 768 bytes on the wire). The hub's open-call table is keyed by
  that wire id, so a call's raw id is not kept while it runs (Codex C-M7: 96 half-MiB ids held
  50,335,862 B at `111e02d7`). `tokens` and `elapsedMs` are integers in `[0, 2^53 − 1]` and `cost` a
  finite number, never negative, an overflow reading as the largest finite double (Codex C-M6: a
  4,001-digit count and an `Infinity` cost, which a strict JSON reader rejects, at `111e02d7`).
  **Measured** on the complete JSONL line the plane writes, with every string at its cap in 4-byte
  characters, an approval open, a 128-byte call id of 4-byte characters or of quotes, and usage
  values of `10^4000` and `inf` (`.omc/probes/612/fix-r1/R1-a/bounds-head.txt`): the largest frame,
  a terminal `state`, is 5,078 B, and a `children/list` row 9,316 B; at `111e02d7` the same build
  measured 5,718 B and 18,551 B (`bounds-base-111e02d7.txt`). The guard test
  (`tests/agents_ext/test_child_events_612.py::test_the_largest_frame_and_row_stay_inside_the_bounds_as_the_wire_writes_them`)
  holds a frame at ≤ 5 KiB and a row at ≤ 12 KiB and parses each line with no `Infinity` or `NaN`.
  `children/list` holds the live rows plus at most 64 ended ones. (Contract r5's "a frame ≤ ~4 KiB"
  is not what was measured; the lead's call after part A: state the measured bound and cut no field
  further.)
- **`{"type": "aelix_notice", "event": "queued", "data": {}}`** (`event` closed to `queued`, `data`
  empty and open) is emitted when the harness's notice inbox reports `queued`. The plane subscribes
  at each binding when it was given a job service and the harness's inbox is on, and **primes** it:
  notices already pending (a startup restores them before the listener exists) raise one frame at
  once. A placed or idle change raises none.
- **The plane subscribes through the seam**, `controls.subscribe`, not a fourth bus channel, so
  `aelix_subagent`'s three channels stay three. A background child never produces `aelix_subagent`
  (its snapshots go to its job), which keeps aelix-acp's "at most one `agent` call is open" parent
  guess right after Q4 opened `spawn`.

### D6. Replay within one hub generation of one live process (#612)

Owner, 2026-11-18: "같은 프로세스 + 영속 기록 (권장)". A hub lives for one harness build: a
`/reload`, RPC `resources/reload` and every session change build a new harness and a new hub with
a new `epoch`. `epoch` is on every frame, on `children/list` and on `children/events`, and the key
a host keeps and deduplicates by is `(epoch, id, seq)`. `children/events {id, afterSeq}` answers by
one cursor table (`ChildEventHub.events`):

| Cursor | Answer |
| --- | --- |
| `afterSeq` absent | every kept event; `gap` is `lowSeq > 1` |
| `lowSeq - 1 ≤ afterSeq < nextSeq` | the events with `seq > afterSeq` (none when current); `gap: false` |
| `afterSeq < lowSeq - 1` (older than the ring) | every kept event; `gap: true` (resync from `children/list`) |
| `afterSeq ≥ nextSeq` | error "afterSeq is ahead of this agent's events (next is N)" |
| `afterSeq < 0` | error "afterSeq must not be negative" |
| an id held only as a tombstone | error "agent '<id>'s events are no longer kept (it ended <end>)" |
| an id this hub never held (any id of another epoch included) | error "no agent '<id>' in this session" |

The hub appends to a ring and calls its subscribers in one synchronous step, the plane writes a
frame before returning, and `children/events` snapshots a ring in one step: so on the native wire,
of the frames the plane emitted from the generation it is subscribed to, every frame with `seq <
nextSeq` precedes the response and every later one follows it. Through aelix-acp the order of a
notification and a response is not promised; the host dedupes by the same key.

After a reconnect no child of before is alive: a CLI RPC EOF ends the process, a WebSocket
reconnect builds a fresh session, aelix-acp's `session/load` starts a new native. The durable
record is the parent session's messages (`get_messages`; the `agent` call's result, which for a
cancelled call is D1's report or, under K1, the next prompt's copy) and each child's own session
file, which the parent session's `aelix.child_session` receipt names (none for a child started
`--no-session`). #612 adds no method to read a child's file (contract r5's follow-up F7).

### D7. Owner Q4's flips, the session lifetime, and the ended-owner epoch (#612)

Owner, 2026-11-18, r2 Q4: "전부 열기 (권장)". In `cli/entry.py`: `JobsExtension(…, tools=…)`
registers `job` and `job_stop` for `rpc` too; `AgentsExtension(background_host=lambda: True)`, so the
`agent` tool's background actions and kind are registered on RPC; `_tool_options_for` gives `bash`
`background_host` true for `rpc`, while an unknown app mode still offers nothing. Each holds when
the CLI built a job service (`backgroundJobs.enabled` not `false`); the WebSocket host builds
none. Admission is then the job service's (`backgroundJobs.limits`, ADR-0293) on RPC as in the TUI,
and with jobs off the fixed `MAX_LIVE_CHILDREN = 4` admits. ACP drives aelix over RPC, so with a
native that has #612 it gets the same tools; aelix-acp `16d6555b` records native `e19ab55e`
(`runtime-reference.json`), which predates #612, and its client sees no `children/*` method or event
(D8), so a result reaches the ACP model at its next prompt (verify V-F6, Codex L11; by reading the
adapter at `16d6555b`, not run). *Amended 2026-11-20 (#612, lane L3): aelix-acp PR #39 pins native
`83714045`, which has #612; a client that declares `aelix/notices` is sent `_aelix/notice` and
wakes the session with `children/notices` (live through ACP during the lane, before its final
commit: one notice placed, `remaining: 0`, the model then read the background result; Note
(2026-11-20, #612)). A client
that does not declare it still gets the result at its next prompt.*

- **Posture on a host.** An idle `permissions/set` fires the agents extension's posture listener
  over every live runtime, a background job's included, so a running background child is tightened
  — asynchronously: the response can come before the child applied it, and a child that does not
  apply it within 30 s, or whose inbox cannot be written, is stopped "permission floor could not
  be applied" (ADR-0306 D8). A foreground child during a parent turn stays out of a host's reach
  (`permissions/set` needs an idle harness).
- **The ended-owner epoch** (contract r5 §6.4; Codex r4-2 7 measured a job registered after its
  owner ended at the base). `JobService.owner_ended` first raises `_end_epoch` and records it as the
  owner's (`_ended_at`, never emptied), synchronously, then takes its snapshot of the owner's live
  jobs. `start` reads the epoch at entry, before its first await, and immediately before it
  registers the job — no await between — refuses when the service is closed or the owner's epoch
  rose since: it writes the outcome fact `failed`, "the session ended before the job started"
  (`SESSION_ENDED_FIRST`), and raises `JobStartRefused`. A `/reload` of the same owner raises its
  epoch, so a start parked across the reload is refused and one begun after it passes. A start
  for the bound session that ENTERS after its owner ended — a `session_shutdown` hook that runs
  after the jobs extension's own, through its jobs facade — is refused at entry with the same
  sentence and writes nothing, until the next `bind` (fix round 1, Codex C-H1: at `111e02d7` the
  epoch it read at entry was the raised one, so a sleeper started that way was still `running`
  under the old owner after a classic `new_session`;
  `tests/jobs/test_ended_owner_epoch_612.py::test_a_session_shutdown_hook_after_the_jobs_own_cannot_start_a_job_for_the_ended_session`).
  A start from the INCOMING session's `session_start` handlers is not such a start: the CLI binds
  the service to the rebuilt harness before those handlers run (`AgentSessionRuntime.set_before_session_start`,
  after the host's rebind; it bound after them through `set_after_session_start`), so the start
  runs under the new session's owner on `resources/reload`, `new_session`, `switch_session` and
  `fork` alike (fix round 2, F1: at `510b186c` each was refused with that sentence, and at
  `111e02d7` and the base a `new_session` one ran under the ended owner;
  `tests/cli/test_session_start_job_owner_612.py`, the real CLI over RPC, which also keeps the
  `session_shutdown` start refused; the order on every swap and on a reload's failed-rebuild restore:
  `tests/runtime/test_before_session_start_612.py`; a swap's failed rebuild takes the same path, measured
  by lane verify round 3's probe only). A start from the launch's own (`startup`)
  `session_start` is still refused, "there is no session to own the job": the first bind comes
  after it, unchanged by #612 (one probe at fix round 2's head). Live, `openai/gpt-4.1-mini`
  through OpenRouter, a real `--mode rpc` process with an `-e` extension, macOS: the handler's
  starts on `new_session` and `resources/reload` ran under the new session's owner, `running`; on
  `510b186c` both were refused with that sentence (`.omc/probes/612/fix-r2/live-rpc/`).
  Ctrl+B's adoption door (`prepare_adopted`) refuses a closed service, and an owner whose epoch
  rose since the service's last `bind` (part B's deviation, below).
- **The lifetime, by stage** (contract r5 §6.4; stages 1 and 2 by reading, unchanged by #612 but
  for the epoch):

  | Stage | Trigger | What runs | Bound | Past the bound |
  | --- | --- | --- | --- | --- |
  | 0 | a swap or quit with a live turn | D3's seal and settle | `SWAP_SETTLE_SECONDS` 10 s | the swap goes on (K4) |
  | 1 | `session_shutdown` (a swap, a quit, `/reload`) | the agents extension stops its foreground rows; `JobsExtension` → `owner_ended(owner, "owner_end" \| "reload")` | `CLEANUP_BOUND` 10 s | the job reads `unresolved` (`owner_end_timeout`/`reload_timeout`), in memory and provisional: a runner that ends later writes its real outcome |
  | 2 | process exit | `JobService.shutdown()`: admission closed, stop all, wait | 10 s, cancel through the barrier, 10 s more, then `task.cancel()` | a job with no written outcome is settled by the next start's reconcile (ADR-0293) |

  Each child runs in its own session and ends on its inbox's EOF (exit 1, ADR-0306) or, on Linux,
  its parent-death signal.
- **Resumable means** what the runtime checks, and nothing is inferred from a free lock: the model's
  `agent(action="resume", id=<child session id>, profile=…, task=…)` (hosts resume through the
  model, owner r3 Q1) finds the child only if `find_resume_target` passes (a START on the parent's
  current branch, the model's own first run, valid recorded limits, the file placed as allocated, a
  loadable header, a user message) and then the runtime's checks pass, in order: the channel can
  resume; the profile, re-resolved by name, is the file the first run used and not project-scoped;
  no live row or claim in this process runs that child session; the recorded directory is still
  that directory inside the parent's; the pinned model route resolves the same registration; no
  other process holds the child file's writer lock (skipped on a filesystem that cannot lock). The
  last check does not prove the child's process exited. A child started `--no-session` is never
  resumable. #612's body, rule 2 ("`/new`·`/resume`·`/fork`·종료 때 자식은 멈추되, resume할 수
  있게 기록한다"), is met under those conditions. ACP `session/cancel` stops the turn, not the jobs.

### D8. The aelix-acp contract (built by aelix-acp PR #39, lane L3; not in this repository)

aelix-acp `16d6555b`, unchanged, drops `aelix_child` and `aelix_notice`, sends no `children/*`
method and never opts in to child approvals. The PR's proposals (contract r5 §8), which lane L3
builds against this branch once the fixtures fix the wire:

*Amended 2026-11-20 (#612, lane L3): built by aelix-acp PR #39, commit `03b9e32` (based on
aelix-acp main `11fb4fe`), against native `83714045`. The list below is r5's proposals as
written; what the build does beyond them, the fence's boundary, and K5 and K7 as measured are in
"Note (2026-11-20, #612)".*

- **Admission**: `children/list`, `children/events`, `children/steer`, `children/wrap_up` and
  `children/stop` go through `_aelix/manage` before its session-lock test, straight to the native,
  so they work during `session/prompt` and during `agents/run`; `children/notices` stays under the
  lock. A counter raised inside `Session.restart()` and `Session.close()` fences them: one that
  arrives while it is up is refused "the session is changing" before anything is written; one
  already sent when it rises answers "the session changed while this request ran; its delivery is
  unknown". *Amended 2026-11-20 (#612, lane L3): as built, one already sent is ended only when the
  worker's close begins (`raise_fence()`, the first step of `Session._close()`); a restart's earlier
  raise of the counter only refuses new ones, and an answer native gave before the close began is
  returned (verify round 2 F1; Note (2026-11-20, #612)).*
- **Events**: for a client that declared `_meta["aelix/nestedToolCalls"]`, `tool_start`/`tool_end`
  become `tool_call`/`tool_call_update` with `toolCallId = <id>:<callId>` and the parent's call as
  `_meta.parentToolCallId` (a synthetic top-level `aelix-child:<id>` for an `agents/run` child); a
  `children` map keyed `(epoch, id)` that the per-prompt clear leaves alone keeps a spawned child's
  frames under its call after the call ended, and drops an older epoch's entries. `aelix_notice` is
  forwarded as `_aelix/notice` only to a client that declared `_meta["aelix/notices"]`.
- **The notice turn**: `_aelix/manage children/notices` through a `notice_turn()` that shares the
  prompt's setup and is cancelled by `session/cancel`.
- **The child's end** on ACP is the terminal `state` frame's `end`; ACP's session `stopReason` stays
  `end_turn | cancelled`.
- **#800**: a re-abort of a cancelled turn only every 2 s (the native's `_REABORT_GRACE_SECONDS`),
  not every 50 ms poll (lowers K5).
- **Close**: wait 45 s after SIGTERM before SIGKILL for a native whose `capabilities.methods` lists
  `children/list` (K7).
- **Approvals**: ADR-0308 D5.

## Known limits

Each is a narrowing under the lead's rule for r5: outside the guarantee the behaviour is the base's.

- **K1 — the report outside the call's result.** The report is not the call's result in the file
  when a `tool_result` hook raises (one Esc is enough: the loop writes the cut text), when a second
  cancel lands in the call's `tool_result` hooks, in its result's `message_end` before the commit, or
  in its JSONL append before the bytes, or when that append fails before its bytes (told at the
  run's end, D2). The model then gets it at the next prompt in the same process as a user message, as
  before #800; a reopen in a new process before that prompt has no report. A harness with no session
  is always in this case (D1).
- **K2 — the copy is raw.** That user message is the report as rendered: a `tool_result` or
  `message_end` hook's rewrite of the call's result is not applied to it (it never was), and a hook
  that rewrites the report to exactly `loop.CUT_TEXT` is read as "not carried", so the raw copy
  follows.
- **K3 — copies of a session.** `/fork` and `/clone` (RPC `sessions/branch`) taken while a delegation
  runs copy the branch before the settle: the copy holds the `agent` call with no result; the report
  is in the outgoing session.
- **K4 — the swap's bound.** *Amended 2026-11-22 (#849).* Step 0 waits for the settle at most
  `SWAP_SETTLE_SECONDS` (10 s); past it the swap goes on in today's order. A prompt still inside a
  `before_agent_start` handler (a dialog nobody answers) holds a swap or quit in `dispose()` until the
  handler returns, as before #800; on RPC such a dialog ends at 300 s. One case past the bound is
  worse than the base, needing a teardown that outlives the bound, which no shipped extension does
  (the lead's narrow-and-finish call, #800's lane verify round 3): a run's end on a stale context
  counts as headless, so the "did not land" line goes to stderr under the TUI too, reachable only
  from an extension command that swaps mid-turn because the TUI refuses its own swaps while a turn
  runs (verify round 3 F2, `.omc/probes/800/verify-r3/probes3/test_v3_stale_ctx.py`). The other
  case it named, a hold that saves and restores the gate across the settle's end, is gone (#849).
- **K5 — ACP, one cancel, several aborts.** aelix-acp re-aborts a cancelled turn while the native
  streams (every 50 ms at `16d6555b`). A native whose cut path waits longer than that in a hook or its
  append reaches K1, and the next prompt's copy is not in the adapter's table. *Amended 2026-11-20
  (#612, lane L3): with aelix-acp PR #39 one client `session/cancel` sends one native abort at once,
  and a turn still streaming is aborted again only 2 s after the last abort (measured 0.0001 s and
  2.0185 s, verify round 2). The case narrows to a cut path that waits longer than 2 s (by reading;
  Note (2026-11-20, #612)).*
- **K6 — ACP recovery.** aelix-acp's guarantee is the recorded terminal `tool_call_update`. A
  `session/load` from an empty table projects native user and assistant messages only, and one from a
  partial table replays without the report.
- **K7 — closing a host.** aelix-acp's close is an escalation deadline (45 s on POSIX for a
  children-capable native, with the aelix-acp PR, D8): past it the native is killed; on Windows the
  worker's Job Object kill ends the native and its children at once, so no stage of D7 runs. Records
  not yet written are reconciled at the next start (ADR-0293). Neither deadline was run here.
  aelix-acp `16d6555b`, unchanged, sends SIGTERM and SIGKILL 5 s later (`wire/process.py`
  `terminate_owned`), so D7's session-end stages (up to 10 s for a turn, then 10 s for the agents
  and jobs) can be cut short under it (verify V-F6; by reading, not run). *Amended 2026-11-20 (#612,
  lane L3): with aelix-acp PR #39 the POSIX deadline is 45 s for a native that lists `children/list`
  or `children/notices`, and it was run: with a scripted model a close took 0.07–0.59 s idle, with a
  spawned child in its model call or with a background `bash` job, and a native whose session end
  takes 8 s ended in 8.08–8.63 s, where `a78db59` killed it at 5.01 s (macOS). No run reached the
  45 s, and Windows was not run (Note (2026-11-20, #612)).*
- **K8 — a slow host.** Each frame is written with a blocking write and flush; a host that stops
  reading stalls the native's event loop (its turn, its children's pumps, every handler) until it
  reads. This is how every RPC event is written; #612 adds no bound (by reading the plane's emit
  path; not measured). *Note (2026-11-22, #852): measured (a 3.0 s pause stalled the loop
  2.969 s) and replaced by ADR-0317: one writer thread writes the real stdout, the loop never
  waits on the pipe, and the turn and each command's reply wait while the host is more than 1 MiB
  behind, when the host's input is read and held, one line of it parsed after each drain to the
  mark; the `aelix_child` and `aelix_notice` frames are enqueued without a wait (ADR-0317
  limit 1).*
- **K9 — replay's reach.** `children/events` replays within one hub generation (`epoch`) of one live
  process: a `/reload`, a session change or a reconnect starts a new one, and earlier rows are not
  replayable after it (D6).
- **Not run for D4–D8**: ACP (lane L3), Windows and Linux live (the live runs were macOS only; the
  real-child tests are POSIX only), a reconnect, and the live refusals for a not-started, stopping
  or broken-inbox child (in-process tests only). *Amended 2026-11-20 (#612, lane L3): parts were
  run live through ACP on macOS during the lane, at its local checkpoints before the final commit
  `03b9e32` (the note below names them) — steer, wrap-up and stop during a prompt, a notice turn,
  the aborts per cancel and close latency, before fix round 1 changed the fence, added the close
  read-through and counted an abort the turn's poll sent while `commands/cancel` waited as the
  cancel's one; a child's card after a second prompt, on fix round 1's code; ADR-0308's approval,
  cancel, stop and restart rows, as its note says. Still not run through ACP: those first rows on
  the final product code (the unit and native tests hold it), Windows and Linux natives, Studio and
  the AionCore renderer, a reconnect, and the 45 s deadline itself (Note (2026-11-20, #612)).*

## Where the build differs from contract r5 (lane L2)

Part A's deviations 1–9 (items 1–9) and part B's deviation in step (c) (item 10). The lead's calls
after part A (2026-11-19) decided items 2, 4 and 8 as written here and had the rest recorded in this
ADR; the lead's calls after part B accepted item 10 as written. Part B's step (d) deviations are
ADR-0308's.

1. **Identity publishes feed the hub only** (`_publish_identity`): at the receipt and at a Ctrl+B
   move the hub emits the `state` frame at once, and `on_event`, the statusline and the job route
   see no extra snapshot (r5 §4.1 said "`_publish` is also called"; the frame is the same).
2. **"Before it was sent" only for a wrap-up held by an approval.** Admission, the params and the
   call into the controls run with no `await`, and a steer's door writes before its first
   suspension, so a settle's cancel lands only after the write: a cancelled steer answers `unknown`
   with the cause. A wrap-up's door has one suspension before its final steer when the row has an
   approval request open: the `cancel` it writes to that request waits for its acknowledgement
   (ADR-0308). A settle's cancel landing there closes the request (step `denied`, the row's note
   "the answer's delivery is unknown; it may have been read"), takes the wrap-up back, and answers
   the error "a session change began before this request was sent" ("the RPC host began shutting
   down before this request was sent" at shutdown), so a retry is served
   (`tests/rpc/test_children_family_612.py::test_a_wrap_up_a_vetoed_swap_cancels_before_its_steer_is_refused_unsent_and_can_be_asked_again`
   and `::test_shutdown_while_a_wrap_up_waits_on_its_approval_cancel_answers_not_sent`; fix round 1,
   verify V-F1 and Codex C-M3: at `111e02d7` the wrap-up stayed recorded with no steer written, the
   request stayed open and every later wrap-up was refused "already requested"). "Before this
   request was sent" is the final steer's: the approval `cancel` written first cannot be unsent, so
   the child may have read "the agent was asked to wrap up" while no wrap-up is recorded and none
   ends its run (fix round 2, F4, the lead's call: narrowed, no change in behaviour; verify r2's
   `codex/lifecycle_wrap2.py` printed `delivered: [('approval', 'cancel', 'the agent was asked to
   wrap up')]` with the reply's error). The door raises
   `InterventionNotSent` (`aelix_agents/command_wire.py`), a `CancelledError` whose `not_sent` the
   plane reads (product core does not import it), so the model's `agent(action="wrap_up")` cancelled by
   Esc in that wait is still a cancel, with the same clean-up
   (`tests/agents_ext/test_child_approvals_612.py::test_a_wrap_up_cancelled_while_its_approval_cancel_awaits_the_ack_is_taken_back`).
   The shutdown wording for a written line, "the RPC host began shutting down before the agent
   answered", is part A's (r5 gave none). A stop answers `{state}` from the controls' rows; if the
   row is no longer listed the cancel propagates ("Command cancelled"). A cancel that is not the
   plane's own propagates, and the plane takes back (`uncancel`) only the cancels it answers.
3. **One terminal frame**: a terminal state seen before the row's final publish is held back (the
   print channel marks `stopped` while `_run` unwinds), so a host sees exactly one terminal `state`
   frame, the one with `end`.
4. **The frame bound is the measured one** — 5,078 B for the largest complete JSONL frame and
   9,316 B for a row under the hub's caps (fix round 1's re-measure, ids budgeted as the wire
   writes them and usage values bounded; part A measured 4,983 B and 9,077 B without escaping,
   approval or usage), held at ≤ 5 KiB and ≤ 12 KiB by the guard — not r5's "≤ ~4 KiB" (D5).
5. **`children/steer` refuses more than r5 listed**: blank text, and text with a NUL or half of a
   character, which would break the inbox line (#627's `unsendable_refusal`). `mode` and `afterSeq`
   sent as `null` read as absent, as `commands/cancel`'s `requestId` does. The text bound is the
   literal `100_000` (a named constant in product core trips the band rule's cap heuristic).
6. **`children/notices` without a job service** refuses "This RPC host has no job service" (r5 named
   no text). `remaining` reads the inbox after the turn; a turn's boundaries may place more than
   `placed` counts, so two budget-sized notices can both go in one call (measured in
   `tests/rpc/test_children_family_612.py::test_children_notices_runs_one_turn_and_says_what_is_left`:
   `remaining` equals the inbox's count).
7. **A job row's stop** goes through the jobs facade, `stop(job_id, reason="user", wait=0.0)` (the
   same request as `JobService.request_stop`, plus the session-owner check the facade applies), and
   falls back to the row's own `runtime.stop` if that raises. The job reads `stopping` when the call
   returns.
8. **`CommandWire` drains under `asyncio.wait`** (D4), beyond r5's "drop the entry on cancel": a
   3.11-only gap measured here (CPython gh-86296). The lead's call: a fix, not a limit.
9. **`CHILD_EVENTS` is a seam constant** (`tests/agents/test_p2_band_boundaries.py`'s seam list was
   edited on purpose), and `tests/rpc/control_v1/test_floor.py`'s temporary set of methods served
   before their fixtures was deleted when the fixtures landed (the lead's call after part A).
10. **Ctrl+B's adoption door** (`prepare_adopted`, synchronous) compares the owner's ended epoch with
    the one recorded at the service's last `bind`, not one captured at its own entry (r5 §6.4); its
    refusal is `SESSION_ENDED_FIRST` when the service is not closed (part B, step (c)).

## Consequences

- A cancelled delegation's report is in the session file as the call's result: an RPC host's
  `get_messages` returns it live and after a relaunch with `--session`, and aelix-acp `16d6555b`,
  unchanged, sends it as the cancelled call's last `tool_call_update` and replays it on
  `session/load` (How it is held). #800 needs no aelix-acp change for this guarantee; K5 and K6 are
  aelix-acp's to lower (#612's lane L3; follow-up F2 of contract r5). *Amended 2026-11-20 (#612,
  lane L3): aelix-acp PR #39 lowers K5; K6 stays (F2).*
- After an Esc and then a session change, nothing from the report comes back as input.
- A session change or a quit during a turn costs up to 10 s more before `session_shutdown` when the
  turn will not settle, and in the case K4 names it still waits in `dispose()` as before.
- A `session_shutdown` handler now runs after the outgoing turn settled (or after the bound), and a
  `send_message(trigger_turn=True)` it makes on that harness is queued, as it was at the base while
  the turn still ran: a session change hands it back after the teardown, a quit leaves it unsent.
- A swap or quit interrupted while an `abort` handler that swallows its cancel keeps the settle alive
  leaves the harness refusing prompts, with the seal's reason, until that settle ends (D3).
- The agents extension holds one small record per cancelled delegation until a prompt confirms it
  placed; every check resolves each record it can, as the queue it replaces was drained. It reads the
  session's leaf when an `agent` call begins and, while a record is owed, at each `message_start` and
  `agent_end`; while a record is owed it reads the branch at each prompt's check and again at each
  run's end (verify round 2 F6).
- In the TUI the report is the generic error card, whose long lines are cut at the card's width with
  "…" and which `/expand` does not open (verify round 1 F9; that card predates #800): the whole text is
  in the session file and in what the model reads.
- (D4–D8) An RPC host given the CLI's services lists six more methods and receives two more event
  types; a host that does not call them, and aelix-acp `16d6555b`, see what they saw (the new types
  are dropped, `aelix_subagent` is unchanged). They are control-v1 additions under ADR-0302 D9, with
  fixtures and floor entries of their own. *Amended 2026-11-20 (#612, lane L3): aelix-acp PR #39
  (`03b9e32`) consumes them; `docs/contracts/control-v1.md` lists them in its consumed register.*
- (D7) On the CLI's RPC host, and so under ACP with a native that has #612, the model can now spawn
  a background agent and run a background `bash` command, and has `job` and `job_stop` (under
  aelix-acp `16d6555b` the client sees none of the events, and a result reaches the model at its
  next prompt); a host learns that a result waits from an
  `aelix_notice` frame and places it with `children/notices` (or its next prompt). With background
  jobs on, the RPC session's delegated agents are bounded by `backgroundJobs.limits`, as in the TUI;
  with them off, by the fixed `MAX_LIVE_CHILDREN = 4`, as before. *Amended 2026-11-20 (#612, lane
  L3): under aelix-acp PR #39 the client sees the agents on the parent's card and, when it declares
  `aelix/notices`, is told a result waits (D5's amendment).*
- (D7) A job start that a session end overtakes now fails with an outcome written, where at the base
  it registered and ran for an ended session.
- (D4) A host's `children/stop` on a job row ends the job with the job service's generic stop reason,
  "stopped by the session (stop, /new, /resume, /fork or quit)", which does not name the host
  (seen in the live run, `.omc/probes/612/impl/C/live/README.md`; not changed here).
- Follow-ups, drafted in contract r5's Appendix A and not posted: F1 `children/resume` (owner r3 Q1);
  F2 aelix-acp reconciles an empty or partial table with the native (K6); F3 the kernel projects
  `result` onto `ToolExecutionEndHookEvent` (K2); F4 an abort ends a prompt still in its input or
  `before_agent_start` handlers (K4); F5 a writer task with a bounded queue for RPC events (K8); F6
  `permissions/set {tighten: true}`; F7 a host method to read a child's session file; F8 `/fork` and
  `/clone` settle before the copy (K3); F9 one native abort per client cancel (K5); F10 the approval
  channel's version fallback (ADR-0308 K10). *Amended 2026-11-20 (#612, lane L3): aelix-acp PR #39
  builds F9 as one abort at once per cancel and a re-abort only 2 s later while the turn still
  streams (K5's amendment).*
- Complexity (ruff 0.15.13, `--isolated --select C901`; part A's `c901-diff.txt`, part B's): no
  listed score rose (`run_rpc_mode` 45, `RpcControlPlane._dispatch` 26 → 25, `runtime._run` 21,
  `_async_main` 156); `CommandWire.request` 9 → 10, `handle` 9 → 10, `capabilities` 7 → 9; no new
  function passes 20 (ADR-0300).

## How it is held

Tests (each fails on `6dd682cd` unless marked a guard; the base runs used a detached `6dd682cd`
worktree with its own venv and these test files copied in):

- D1, single and batch: `tests/agents_ext/test_stopped_child_reports_604.py`
  `::test_esc_during_a_delegation_makes_its_report_the_calls_result` (the result is the report, the
  reopened file holds it as the call's `toolResult`, the next request carries one report, as the
  `toolResult`) and `::test_esc_during_a_batch_reports_each_member_that_had_a_child`; on the base
  both find the report on the next-turn queue. Guards: `::test_nothing_is_queued_when_no_child_had_started`
  (the cut text), `tests/agents_ext/test_cancelled_report_owed_800.py::test_a_renderer_that_raises_is_the_bare_cancel`.
- D1 against the product: `tests/rpc/test_cancelled_delegation_reopen_800.py` (POSIX): a real
  `--mode rpc` parent and a real delegated child against a scripted provider; after `abort`,
  `get_messages` holds the report as the `agent` call's result, and so does a parent relaunched with
  `--session`; the parent's model was asked once. On the base: the cut text.
- D2: `tests/agents_ext/test_cancelled_report_owed_800.py` — a redacting `tool_result` hook leaves
  only the redacted result (base: plus the raw report as `user`); an annotating hook leaves one result
  and no copy (base: two); a copy whose append fails is injected again and the file then holds one
  (base: none); the record of another session injects nothing (base: the queue's `user` copy). Guards
  (pass on both): a second abort in a `tool_result` hook or in a pre-commit `message_end` hook gives
  one user copy at the next prompt and none injected at the one after; a failed prompt keeps it; a
  session-less harness gets one copy. A second abort parked in the report's own append gives one copy
  too (no base run: there the result is the cut text). `loop._CUT_TEXT is loop.CUT_TEXT`.
- Fix round 1 (Codex review C-1 to C-9, verify round 1 F2, F3), each failing on the previous head
  `deb7cdd7` by its assertion unless marked a guard (a detached `deb7cdd7` worktree with its own venv
  and these files copied in). In `tests/agents_ext/test_cancelled_report_owed_800.py`: a copy a
  `message_end` hook redacts is carried once over three prompts (C-5; there one, two, three); a
  session-less harness carries the report once after one abort (C-6; there `toolResult` and `user`)
  and keeps it across a failed prompt (C-6; there nothing); an earlier result under the reused
  `call-1`, of another tool or an earlier delegation, is not the carrier (C-7; there the report was
  lost); one abort and one failed read of the session give one report per request (C-8; there
  `toolResult` and `user`), and an unreadable branch fails the prompt in the check's words and keeps
  the record (C-8; there the read error came from the request instead); a report whose append fails
  before its bytes is told naming the call and reaches the next prompt once (C-9; there a DEBUG line;
  fix round 2 changed how it is told, below). Guards: an earlier unrelated result, a later call's result under another or the same
  id, a later delegation's result, a cancel right after the copy's write, no warning for a report that
  landed. In `tests/runtime/test_swap_settles_first_800.py`: a switch to a file another writer holds,
  answered "read only" or "fork and continue", leaves that file unchanged (C-1; there both appended to
  it); a swap cancelled inside an awaited `session_shutdown` handler leaves a harness that takes the
  next prompt (C-2; there "the session is being replaced"); a swap cancelled while the first `abort`
  hook parks cannot abort the next prompt, the hook returning on its cancel (C-2; there it did); a
  `session_shutdown` handler's `send_message(trigger_turn=True)` is queued and handed back by a new
  session, and the swap returns without running `before_agent_start` (C-3; there it waited); an `import_from_jsonl` of the current file keeps the
  report on the branch (C-4; there not in the installed session). Guards: a symlinked spelling of the
  current file (F3, R9; not on Windows, as its siblings), a switch from a session with no file (F3, R12), the gate an interrupted swap
  puts back (F3, R10), an interrupted quit, a re-open that fails, `stop` after the seal. The two #611
  tests that read the owed table read it with `getattr`, so on the base they fail on their assertion
  (verify F8).
- Fix round 2 (verify round 2 F1–F5), each failing on the previous head `9acd9ea0` by its assertion
  unless marked a guard (a detached `9acd9ea0` worktree with its own venv and these files copied in:
  7 failed, 51 passed, `.omc/probes/800/fix-r2/prev-newtests.txt`). In
  `tests/agents_ext/test_cancelled_report_owed_800.py`: after one Esc and a `navigate_tree` to the
  first user message (the leaf at the root), three prompts carry one copy and the record is retired
  (F1; there the second prompt carried `['user', 'user']`), and to the second user message (a guard)
  the same; a record whose origin could not be read finds the call's result anywhere on the branch
  (F4, a guard for V-E22); the C-9 report is told at DEBUG, with one stderr line without a UI and
  none with one (F3; there a WARNING). In `tests/runtime/test_swap_settles_first_800.py`: a new session
  or a quit cancelled while a swallowing `abort` hook parks keeps the seal, refusing a prompt
  `invalid_state`, until the settle ends, then answers the next prompt with one abort (F2; there
  `turns_held` was `None` with the settle alive); a failed re-open is told at DEBUG with the exception
  and in one stderr line without a UI (F3; there a WARNING). Guards: a hold set while the settle lives
  is kept when it ends; on a runtime that manages writer locks, a switch to a symlink of the current
  file (not on Windows) and an import of it keep the report on the branch (F4, V-RR5 and V-RI2).
- D3: `tests/runtime/test_swap_settles_first_800.py` — Esc then `new_session` gives nothing back as
  input (base: one `input_withdrawn` carrying the report); a switch to the current file during a
  delegation keeps the report on the branch, and after an append the reopened branch is
  `user, assistant, toolResult, user` (base: `user, assistant, user`); `new_session` during a
  delegation puts the report in the outgoing file with one model request (base: no report, two
  requests); an `abort` hook that parks does not hold `new_session` with the bound shortened to 0.5 s
  (returned within 1.5 s; base: still waiting); a `session_shutdown` handler finds `prompt` refused
  `invalid_state` with the seal's reason and a notice turn `held` (base: `busy`, `busy`); a quit
  during a turn settles it before `session_shutdown` (base: the handler sees `turn`); a swap cancelled
  in step 0 lifts the seal. Guards: a vetoed `new_session` neither seals nor aborts; `reload` waits
  for idle and aborts nothing.
- Mutants (`.agents/skills/aelix-tests-that-bite/scripts/mutants.py`). On the first implementation's
  lines: 28 of 29 killed by its targeted tests plus `tests/runtime` with the session-lock tests; `R2`
  (step 0 for `reload` too) survives as equivalent: `reload()` awaits `wait_for_idle()` before
  `_teardown_current("reload")` (`.omc/probes/800/impl/mutants/`). Verify round 1 found four more that
  survived it (E8, R9, R10, R12); their counterparts on the lines as fix round 1 left them are killed
  (the same ids in the set below). On the lines fix round 1 changed or
  added, 33 of 40 are killed by `tests/runtime/test_swap_settles_first_800.py`,
  `tests/agents_ext/test_cancelled_report_owed_800.py`, `tests/runtime/test_session_lock_handoff.py`
  and `tests/rpc/test_cancelled_delegation_reopen_800.py`. Seven survive, each read as equivalent or
  out of reach: `H3` (`_refuses_a_trigger` without its `gate is not None`: the `if` before it in
  `_action_send_message` has already returned for an open gate), `RA3` (no join after the settle's
  cancel: the cancel lands at the settle's next suspension, before the caller can start a prompt; the join bounds only an `abort` handler that
  swallows it), `RR3` (re-open any target: one that is not the outgoing file was not written by the
  teardown), `RC1` (the import's re-open without its own `replace(cwd=…)`: `repo.open(cwd_override=…)`
  applies it), `E11` (a copy's entry without its `known` flag: the entry is set only once `known`),
  `E19` (the check not clearing a copy whose commit was never read: that needs the close-out's
  `agent_end` itself cancelled, not constructed), `E24` (`agent_end` reading another session's
  records: a record's session is the live one at every `agent_end` that follows its cancel, by
  reading). Logs: `.omc/probes/800/fix-r1/mutants/`. Verify round 2 killed 33 of 45 on its own set,
  with three real survivors (V-RR5, V-RI2, V-E22) and V-E9 (F1); the round 2 tests kill V-RR5, V-RI2
  and V-E22 (3 of 3), and V-E9's counterparts `E1` and `E2`. On the lines fix round 2 changed or
  added, 20 of 21 are killed by the first three test files above; `E4` (a copy whose `before` is off
  the branch counted as placed) survives, read as out of reach: the leaf moves only forward between a
  copy's `message_start` and the next read in one prompt, so a copy whose entry is on the branch has
  its `before` there too. Logs: `.omc/probes/800/fix-r2/mutants/`.
- Live, from scratch HOME, agent dir and cwd, with `openai/gpt-4.1-mini` through OpenRouter: the TUI in
  a pty at 120x40 (Esc on a parallel two-task delegation draws the Esc frame below the error card
  that carries the report; the next prompt's answer names what the agents did and the screen shows no
  report line beside the prompt; `/quit` during a delegation exits 0 with the report in the file —
  24.2 s was that whole pty run, from launch through the model's delegation to the exit, and verify
  round 1 and fix round 1 measured 0.1 s and 0.0 s from `/quit`'s Enter to the exit (one task, its
  child in `bash sleep 45`) — and `--session` replays it; Esc then `/new` leaves the input box empty, where the base's box
  read `[Pasted text #1 +23 lines]`; `/new` during a turn is refused by the TUI, "/new can't run
  until the answer ends", so a TUI swap mid-delegation is a quit); an RPC parent (abort during a delegation, the report as the call's
  result, the next prompt's answer drawn from it with no user copy; `switch_session` to the current
  file during a delegation leaves the report on the branch); aelix-acp `16d6555b` unchanged with the
  scripted provider (the cancelled call's last `tool_call_update` is `failed` with the report, and
  `session/load` replays it; one client `session/cancel` sent `commands/cancel` and one native
  `abort` in 3 of 3 runs). With the scripted provider, a real `-p --mode json` parent sent SIGINT
  while its child ran `bash sleep 30` ends the `agent` call with the report and writes it to the file
  (the base: the cut text), and a real `--mode rpc` parent whose stdin closes then writes the report
  to the file (the base: neither the report nor the cut text). Rows and frames, and the per-path
  sweep: `.omc/probes/800/impl/live/`, `.omc/probes/800/impl/entry-point-sweep.md`. Fix round 1 re-ran
  the swap rows with `openai/gpt-4.1-mini` through OpenRouter (`.omc/probes/800/fix-r1/live/`): the TUI
  in a pty at 120x40, `/quit` while the child ran `sleep 45` (exit 0; the file holds
  `user, assistant, toolResult` with the report) and the same session reopened with `--session` (the
  report card, "Resumed session (3 messages)"); Esc then `/new` (the Esc frame and the report card,
  then an empty input box); RPC `switch_session` to the current file, and to its `/tmp/…` spelling of
  `/private/tmp/…`, during a delegation (the installed branch `user, assistant, toolResult` with the
  report; after the next prompt and in a relaunch, the same and the next turn); RPC `new_session`
  during a delegation (no assistant `message_start` after it; the outgoing file holds the report). The
  sweep after fix round 1: `.omc/probes/800/fix-r1/entry-point-sweep.md`. Fix round 2 re-ran verify
  round 2's K1 row (`.omc/probes/800/fix-r2/live/`): the TUI in a pty at 120x40 with
  `openai/gpt-4.1-mini` through OpenRouter and an extension whose `tool_result` hook raises for
  `agent`, Esc while the child ran `sleep 45` (the file's result is the cut text, so the record is
  unplaced at the run's end), then `/quit` (exit 0): the captured bytes hold no "did not land" and the
  input row reads `❯ Type your message or @path/to/file`, where verify round 2's capture of `9acd9ea0`
  held that line once, written into the input row. The sweep after fix round 2:
  `.omc/probes/800/fix-r2/entry-point-sweep.md`.

**D4–D8 (lane L2 of #612).** Tests, each failing on `567753c0` (the base of lane L2: b7f and #800
merged) unless marked a guard; parts A and B ran them on a detached `567753c0` worktree with its own
venv, where every new file fails at import (`ModuleNotFoundError: aelix_agents.child_events`, or the
missing `RpcControlPlane(jobs=)`), and a behaviour probe answered every `children/*` method
"Unsupported runtime control method" and left a cancelled `CommandWire.request`'s entry behind
(`.omc/probes/612/impl/A/base-behaviour.txt`, `.omc/probes/612/impl/B/onbase/`):

- D4: `tests/rpc/test_children_family_612.py` — the admission sets; list, events and the doors
  reach the controls; steer refuses what its inbox cannot take; a host with no delegated agents
  refuses and lists none of them; capabilities list each half on its own service; the
  `aelix_subagent` channels stay three (a guard); an intervention is answered while a mutation
  holds the lock; refused while a reload runs and settled (`unknown`) when it begins; a stop a
  session change interrupts answers the hub's state; the old generation's frames reach the host
  until the rebind and none after; a held classic swap refuses interventions and a vetoed one
  admits again on the same generation; `commands/cancel {}` does not reach one; shutdown and a
  cancelled `run_rpc_mode` cancel them; a settled steer leaves no correlation entry, and a cancel in
  any turn of the write raises and leaves none (on 3.11 too); a model `send` and a host steer racing
  on one row are both answered; `children/notices` runs one turn, says what is left and is cancelled
  by `commands/cancel`, leaving the harness idle. `tests/agents_ext/test_child_events_612.py`'s
  host-controls cases: refusals by state on real rows, a live stub child steered and wrapped up
  (`wrapped_up`, "… the user's request"), a spawned job's row in the build's hub with its stop going
  through the job service, the stop's 10 s bound, the job stop's fallback.
  `tests/cli/test_rpc_children_plumbing_612.py`: the launch hands RPC its job service (none with
  `backgroundJobs.enabled: false`) and its permission extension.
- D5: `tests/agents_ext/test_child_events_612.py` — the first snapshot is a `state` frame and every
  frame has `id`, `seq`, `epoch`; five open calls then `agent_end` are five starts and five
  `no_result` ends in start order; a turn boundary closes the ended turn's calls first; a start with
  no id, and one past 1024 open calls, emit neither a start nor an end; `text`, `stuck` and
  `wrap_up` frames on a change, and `requested: false` after a `not_sent` write on the wire; the
  terminal `end` for each way a run ends (ok, wrapped up, stopped, cancelled with and without a
  process, its own abort, a channel that raised); a terminal state before the final publish is held
  back; a silent child's receipt gives a `state` frame with its `sessionId` before any output, and
  a real Ctrl+B move one with its `jobId`; a 3 MiB tool name is capped and the row stays under
  12 KiB; a literal `h:` id and a 129-byte id map to different wire ids; an id is budgeted as
  the wire writes it; usage values are bounded and strict JSON; open calls are keyed by the wire
  id; the largest complete frame and row, built from 4-byte characters and escaping ids, stay
  inside 5 KiB and 12 KiB; the cursor table; the tombstones, and their bound. `tests/rpc/test_children_family_612.py`: a notice pending at subscription primes one
  `aelix_notice` and each later queued notice raises one (placed and idle raise none); a spawned
  child emits `aelix_child` and no `aelix_subagent` (POSIX).
- D7: `tests/cli/test_job_tools_builtin_779.py::test_rpc_and_acp_register_both_and_the_background_actions_across_rebuilds_612`,
  `tests/tools/test_bash_background_611.py::test_rpc_and_acp_advertise_and_accept_it_and_an_unknown_mode_does_not_612`
  (two of the three #611/#779 tests that pinned the refusal, inverted and renamed; the third,
  `tests/agents_ext/test_agent_background_611.py::test_a_host_that_answers_no_keeps_the_pre_611_tool`,
  stays as the guard for an embedder whose host answers no);
  `tests/jobs/test_ended_owner_epoch_612.py` (a start parked while its owner with no jobs ended, one
  parked across `shutdown`, one parked across a `/reload` of the same owner, one begun after the
  owner ended and before the next bind — directly and from a later `session_shutdown` hook over
  RPC — and an adoption after the owner ended since the bind are refused; a guard: an owner ended
  after the start registered stops its job); `tests/cli/test_session_start_job_owner_612.py` and
  `tests/runtime/test_before_session_start_612.py` (fix round 2, F1, above); `tests/rpc/test_rpc_background_612.py` (an idle RPC `permissions/set plan`
  tightens a running background child; POSIX; it fails on the base only through the CLI, which
  registered no `agent` kind on RPC, as its docstring says).
- The control-v1 fixtures (ADR-0302 D9): `tests/rpc/control_v1/fixtures/children.list.json`,
  `children.events.json`, `children.steer.json`, `children.wrap_up.json`, `children.stop.json`,
  `children.notices.json`, `event.aelix_child.json` and `event.aelix_notice.json` (`"since": "#612"`,
  `hosts: ["cli-rpc"]`, `consumers: []`), and new cases in `capabilities.json`, `ui.enable.json`
  and `event.extension_ui.json`: 79 cases, each passing in process (`test_contract.py`, 294 cases in
  all) and the one replay case on a real `--mode rpc` child as well (`test_real_child.py`). The
  rig's children are this product's hub, `_publish`, reducer and `HostControls` doors over rows
  whose inbox answers a scripted reply and that have no process
  (`tests/rpc/control_v1/fake_children.py`). `scripts/check_control_v1.py --update` added 6
  methods and 2 events (32 and 4), `V1_SHA256` is unchanged and `--check --base 567753c0` holds;
  `docs/contracts/control-v1.md` lists the six methods as served, not yet consumed, and both
  events as dropped by aelix-acp `16d6555b`. On `567753c0` every `children/*` case answers
  "Unsupported runtime control method" and no `aelix_child` or `aelix_notice` frame comes
  (`.omc/probes/612/impl/C/onbase/base-fixture-answers.txt`). *Amended 2026-11-20 (#612, lane L3):
  the register now lists them as consumed by aelix-acp PR #39 at `03b9e32` (each fixture's
  `consumers`); the floor and `V1_SHA256` are unchanged, and `test_consumer_list.py` fails against
  the register as it was.*
- Mutants (`.agents/skills/aelix-tests-that-bite/scripts/mutants.py`): part A, 105 distinct over
  every product file it changed, 99 killed on the first pass and the six survivors killed by tests
  added for them (`.omc/probes/612/impl/A/spec-*.json`, `log-*.json`); part B's in ADR-0308.
- Pythons and platforms (part A, `.omc/probes/612/impl/A/`): its new and touched files on 3.11, 3.12
  and 3.14 with `-W error::pytest.PytestUnraisableExceptionWarning` (157 passed each), Linux docker
  3.12 (156 passed, 1 skipped), the coarse Windows clock (80 passed).
- Live with `openai/gpt-4.1-mini` through OpenRouter, a real `--mode rpc --agents` parent over pipes,
  macOS, scratch HOME, agent dir and cwd (`.omc/probes/612/impl/C/live/README.md`, `rpc/interventions-1/`):
  the six `children/*` methods listed; the parent model spawned a background child, whose frames
  came `state` (with `jobId` and `background: true`), a `state` with its `sessionId` before any
  output, `running`, `text`, and one terminal `state` with `end`, each with `epoch` and `seq`;
  `children/events {afterSeq: 1}` replayed from `seq` 2; `children/steer` answered
  `queued`/`streaming` and `mode: follow_up` `verb: follow_up`; `children/wrap_up` answered `queued`,
  a second one was refused "already requested", and the child ended `wrapped_up`, `aborted`,
  "wrapped up at the user's request"; `children/stop` on a job row answered `{state: "running"}` and
  then a terminal frame `end: stopped`, a second `stopped`, a later steer the resume hint, an unknown
  id "no agent 'sub-nope' in this session"; two `aelix_notice` frames, and `children/notices`
  answered `{started: true, placed: 1, reason: null, remaining: 0}`, then `{started: false, placed: 0,
  reason: "nothing", remaining: 0}`. One `children/notices` sent 0.05 s before a job posted its
  notice answered `nothing`, and the `aelix_notice` frame followed: a host that follows the frames
  calls again.

## Note (2026-11-20, #612): aelix-acp PR #39 builds D8

aelix-acp PR #39 (lane L3), commit `03b9e32` (based on aelix-acp main `11fb4fe`; merged 2026-11-20
as aelix-acp main `2ff1959`), builds D8 against this repository's `83714045`, which its
`runtime-reference.json` now pins. Lines below are `handochan/aelix-acp` at `03b9e32`. The other
aelix-acp ids in this note, except the base `a78db59`, are the lane's local checkpoints, squashed
into `03b9e32` and not published: `c824d83` and `5ca9ad5` (before both fix rounds), fix round 1's
`3af0902`, `bb0c954`, `e47147b`, `8533b45`, `20f1d27` and `91c4ed1`, and `8e932ac` (after fix round
2), whose product code is `03b9e32`'s minus aelix-acp #37's Studio extension block and one
docstring. Every run named here was on macOS arm64 with a native at `83714045`: the adapter's unit
suite (287 tests at `8e932ac`, OK on Python 3.11.15, 3.12.13, 3.13.13 and 3.14.5; at `03b9e32`, OK
on 3.11 and 3.14), its native suite (`scripts/test_native.py --check-ref`: 292 tests OK at
`03b9e32`, 1 Windows-only skip), three independent verify rounds and a Codex cross-review
(`.omc/specs/612-l3-pr-body.md`, `.omc/specs/612-l3-verify-r1-report.md` to `-r3-report.md`,
evidence under `.omc/probes/612/l3/`).

**Built as D8 proposed.** `_aelix/manage` sends the five controls before its session lock and the
activity lease (`server.py:228-231`, `Server.intervene` `:245-253`, `Session.intervene`
`session.py:1218-1243`), so they are answered during `session/prompt` and `agents/run` and never
mark a running turn dispatched; `children/notices` stays under the lock and runs as a prompt turn
(`server.py:462-463`, `Session.notice_turn` `session.py:1388-1418`), whose `session/cancel` answers
`{stopReason: "cancelled"}` alone. Nested calls, the synthetic `aelix-child:<id>` card, the
`(epoch, id)` routes (64 live children, 8 retired epochs, dedupe by `(epoch, id, seq)`,
`runtime/children.py:92-197`) and `_aelix/notice` for a client that declares `aelix/notices`
(`session.py:734-741`) are as proposed; a child's end is the card's `end`, and ACP's `stopReason`
stays `end_turn | cancelled`. Held by `tests/test_native_children.py` (17 tests, a real native and a
scripted model), among them
`::test_steer_and_wrap_up_reach_a_child_during_session_prompt`,
`::test_steer_wrap_up_and_stop_during_agents_run_on_a_top_level_card` and
`::test_children_notices_after_a_session_cancel_runs_its_tools`.

**Beyond the proposals:**
1. native's epoch is also learnt from `children/list` and `children/events` replies
   (`session.py:1234-1235`, `observe_epoch` `:891-896`), so their questions of an older epoch are void
   (approvals review LOW-1; `tests/test_children.py`
   `VoidQuestionTests.test_a_children_list_reply_of_a_newer_epoch_voids_it`);
2. a child's card fields are kept across prompts (`ChildRoutes.known`, `remember`): the first build
   reset `profile` and `state` to `null` on the next frame after the user's next prompt (verify round 1
   F1, Codex C-M5; `tests/test_native_children.py::test_a_spawned_childs_card_keeps_its_profile_and_state_after_the_next_prompt`);
3. `currentTool` on the parent card's entry for every client, not only one that nests
   (`session.py:840-844`, Codex C-L7);
4. a parent call id native hashes (over 128 bytes as the wire writes it) is projected with native's
   own `wire_call_id` rule (`runtime/children.py:52-68`, `ChildRoutes.call_started`), so its child
   still finds the card (Codex C-L6; verify round 2 compared 34,320 random ids with native's, 0
   mismatches);
5. `session/load` replays a child's nested calls and the synthetic card only to a client that
   declared `aelix/nestedToolCalls` (`server.py:324-327`, `nested_only`; Codex C-M4);
6. native's stdout is read through the worker's shutdown, so its last `tool_end` and terminal
   `state` reach the client and the record, and a child still live when the worker is gone gets its
   open calls `failed` and its entry `stopped` with `_meta["aelix/child"].closedBy: "adapter"`
   (`Session.end_children`, `session.py:1094-1124`; Codex C-M3;
   `tests/test_children.py` `ShutdownTests`). With a real native, a restart's and a reload's cards
   were closed by native's own frames, and the adapter's path was not needed (verify round 2);
7. one native abort per client cancel (K5 below).

**The fence's boundary.** A counter raised in `Session.restart()` (`session.py:1143`) and by
`raise_fence()`, the first step of `Session._close()` (`:1201-1216`, `:1504-1511`), refuses a new
children control while it is up: `-32000` "the session is changing", `data.kind:
"session_changing"`. One already written is ended only when the worker's close begins:
`raise_fence()` cancels each one native has not answered by then, which answers `-33002` "the
session changed while this request ran; its delivery is unknown" (`data.cause.kind:
"session_changed"`); one native answered before then returns that answer, also when it was read
after a restart began; one whose worker died first answers `-32603`. D8's Admission bullet put the
boundary at the counter's first rise; verify round 2 F1 measured it at the close (Codex's fence
case on Python 3.11.15 and 3.12.13: trace `('adapter reads worker answer', 1, 0)`, reply `queued`;
`tests/test_children.py` `FenceOrderTests` on 3.11 to 3.14). `session/close` (aelix-acp #35) and
`sessions/close` go through the same `close()`.

**K5.** One client `session/cancel` sends one native abort at once — its own, or the turn's poll's
when that went first while `commands/cancel` waited — and a cancelled turn still streaming is aborted
again only 2 s after the last abort (`REABORT_SECONDS`, `session.py:66`, `:1447-1495`). Verify round
2 counted the aborts of one cancel over 3 s with a scripted worker: 0.0001 s and 2.0185 s (the build
before: 0.0001 s, 0.0002 s and 2.02 s). A real native whose cut path waits in a hook got 1 abort
where `a78db59` sent 2
(`tests/test_native_children.py::test_one_session_cancel_sends_one_native_abort_in_the_first_two_seconds`).
Live through ACP, three cancels mid-stream sent 1, 1 and 1 native aborts at `5ca9ad5`, which
already sent one abort per cancel with the 2 s cadence (`REABORT_SECONDS`, `abort_due()`,
`last_abort`, from `c824d83`), before fix round 1's change for an abort the turn's poll sent while
`commands/cancel` waited (`bb0c954`), and the base `a78db59` also sent 1, 1 and 1 live: that
model's cut path is fast, so the live row does not tell the two apart. The one-abort guarantee rests
on the unit tests, the native tests and the slow-cut native test above (head 1, base 2). A cut path
that waits longer than 2 s still gets the re-abort and can reach K1 (by reading; not run).

**K7.** Between SIGTERM and SIGKILL the POSIX deadline is 45 s for a native whose
`capabilities.methods` lists `children/list` or `children/notices`, 5 s otherwise; Windows's Job
Object path is unchanged (`CHILDREN_CLOSE_SECONDS`, `session.py:71`, `:1544-1547`). Measured with
a scripted model: idle, with a spawned child held in its model call and with a background `bash` job,
the native was gone 0.07–0.59 s after `sessions/close`, `session/delete` or adapter shutdown, and a
native whose session end takes 8 s ended in 8.08–8.63 s, where `a78db59` killed it at 5.01 s (verify
round 2 at `91c4ed1`; the lane's own run at `20f1d27`: 0.08–0.55 s and 8.08–8.12 s). No run
reached the 45 s, and `session/close` can now take that long.

**Run live through ACP**, with `openai/gpt-4.1-mini` through OpenRouter, aelix-acp's black-box test
client, scratch HOME, agent dir and cwd, each row at the lane's local checkpoint named
(`commands.txt` in each directory):
- at `5ca9ad5`, which already sent one abort per cancel with the 2 s cadence, before fix round 1
  changed the fence (`3af0902`), counted an abort the turn's poll sent while `commands/cancel`
  waited as the cancel's one (`bb0c954`) and added the close read-through (`e47147b`), the product
  code changing by +378/−57 lines from it to `8e932ac` (`.omc/probes/612/l3/impl/live/`):
  `children/steer`, `children/wrap_up` and `children/stop` during `session/prompt` (no "Session is
  busy"); `_aelix/notice` and then
  `children/notices` (`placed: 1, remaining: 0, stopReason: "end_turn"`, after which the model read
  the background result); 1, 1 and 1 native aborts for three cancels (K5 above); `sessions/close`
  with a child running `sleep 60` (0.68 s); and ADR-0308's approval and cancel rows;
- at `8533b45`, fix round 1's product code before fix round 2 (`fix-r1/live/`): a spawned child's
  card after a second prompt (no `null` `profile` or `state`), and ADR-0308's approval, cancel,
  stop and restart rows;
- at `8e932ac`, whose product code is `03b9e32`'s minus aelix-acp #37's block and one docstring
  (`fix-r2/live/`): only ADR-0308's cancel and restart rows.

The rows at `5ca9ad5` were not run again on the final product code; the unit and native tests hold
what fix round 1 changed there. Not run through ACP: Windows and Linux natives (aelix-acp's CI runs
them), Studio and the AionCore renderer, a reconnect, and the 45 s deadline itself.

**Decisions.** Owner: r2 Q4, "전부 열기 (권장)" (2026-11-18). The lead's calls, not the owner's: the
capability names `aelix/nestedToolCalls` and `aelix/notices`, `_aelix/notice`, the synthetic card,
the 2 s cadence and the 45 s close (r5 §8's proposals); the 45 s also for a native that lists only
`children/notices`, since background jobs need D7's session-end stages; the three answers
`-33002`, `-32000 session_changing` and `-32603`; and the adapter's 330 s wait for a child's
question, past native's 300 s (ADR-0308's note of the same date).

## Note (2026-11-22, #857): `children/notices` counts the whole turn in `placedInTurn`

D4's `children/notices` answers a new field, `placedInTurn`: every notice its turn placed, the
start's drain and each boundary's. `placed` keeps D4's meaning (the notices placed before the first
model call, one 64 KiB budget), since changing what a field means is a change under ADR-0302 D3 and
a new field is an addition; the TUI's `/jobs send`, which reads `placed == 0`, is unchanged. The
count is the harness's own, `PromptNoticesResult.placed_in_turn`: `NoticeInbox.placed_count` read
when the notice turn claims the harness and again when its run has settled (`harness/core.py`
`prompt_notices`). The lead's call between that counter and counting the inbox's `placed`
notifications in the plane: the counter is read by the harness that ran the turn, over exactly
the turn, so a listener bound to another harness, or a placement outside the turn, cannot enter it.

Measured on macOS, Python 3.12 (`tests/rpc/test_children_notices_placed_in_turn_857.py`, each test
failing at `0436193c`, where the answer had no such field): C-M9's case above, two notices 100 B
short of a budget each and one call, answers `placed: 1, placedInTurn: 2, remaining: 0`; a notice
posted while the model answered, placed by the turn's boundary, makes `placed: 1, placedInTurn: 2`;
one notice answers 1 and 1; nothing waiting answers 0 and 0; and a notice an ordinary prompt
placed before the notice turn is not counted in it. control-v1: `fixtures/children.notices.json`
gains four cases with `"since": "#857"` (nothing waits, one notice, the two budgets, and the real
child's refusal answering 0), each requiring `placedInTurn`; the #612 cases are unchanged, and so is
the v1 digest. The field reaches Studio through aelix-acp's `_aelix/manage`, which forwards the
answer whole (the control-v1 contract §3.2); no consumer reads it by name.

## Note (2026-11-22, #836): `children/resume`

The owner's r3 Q1 for #612, "#612에서 빼기 (권장)", left a host's resume out of D4; #836 adds it as
[ADR-0318](0318-an-rpc-host-resumes-a-stopped-delegated-agent-with-children-resume.md):
`children/resume {id, task}`, a seventh `children/` method. It is admitted as `agents/run` is
(`MUTATING_METHODS` and `CANCELLABLE_METHODS`, idle only, the lock held while the resumed agent runs
in the foreground), not before the lock as D4's three interventions are, and it is listed in
`capabilities.methods` only where the delegation runtime implements the seam's optional
`HostResume`. While it runs, `children/list` and `children/events` show the resumed agent as a new
row with the same `sessionId`, and `children/steer`, `children/wrap_up` and `children/stop` reach
it. Both were seen live, on macOS with `openai/gpt-4.1-mini` against a real `--mode rpc` parent, in
the lane's run and in its independent verification. In the lane's run the resumed row carried the
stopped agent's `sessionId`. In the verification, `children/steer` and `children/wrap_up` sent to a
running resumed agent were both answered `queued` (whether either changed its output was not
looked at); `children/stop` sent to a running resume (the agent's second) answered `state:
stopped`, and that resume answered `status: aborted`; and `commands/cancel` ended a resume with
"Command cancelled", its row `stopped`, `cancelled`. The lock-free path,
the fences and the notice wake are unchanged; since #853's fix round 1 a `permissions/set {tighten:
true}` that narrows is admitted before the lock too (ADR-0306's note of 2026-11-22).


## Note (2026-11-22, #849): a spent seal puts back the gate it replaced

K4's first case past the bound. An interrupted swap or quit whose settle outlives its join keeps
its seal; `_unseal_when_settled` lifts it when the settle ends, only while the gate is still that
seal. `LateRouteHold.apply` (`/agents use`, a late-route settle) saves the gate, holds the turns,
awaits and restores what it saved: when the settle ended inside that await, the callback saw the
hold and did nothing, and `apply` restored the seal. The issue's probe S4, the real `apply` called
unbound, on `0436193c`: `settles_left=0 gate_after=TurnSeal:'the session is being replaced'
prompt=refused`.

**The change** (the lead's call: the issue's first candidate). `_unseal_when_settled` marks the seal
spent when its settle has ended (`TurnSeal.spend(previous)`), and `AgentHarness.hold_turns` puts up
`_unspent(reason)`: a spent seal stands for the gate it replaced, followed through every spent seal
under it. A seal whose settle still lives is put back as itself and refuses until the settle ends.
The `turns_held is seal.gate` check stays, so a hold set while the settle lives is kept when it
ends. The two other candidates were not taken: one needs `_unseal_when_settled` to know every hold
that saved the seal, and the other changes `apply` alone, not every path that restores a gate.

**Every path that saves and restores the gate**, read in `packages/` at `0436193c`:
`LateRouteHold.apply` (`cli/runtime_bootstrap.py`; covered, and since fix round 1 it ends through
`release_turns`, below); `AgentSessionRuntime._abandon` through
`_unseal_when_settled`, which restores `previous` (covered: a `previous` that is a spent seal
resolves too); `_settle_late_route`'s `finally` (`cli/entry.py`) lifts the rebuilt harness's gate
with `None` and the launch gate is set and lifted with `None` (no restore of a saved gate);
`AgentHarness.stop` sets the gate of a harness given up on (no restore). No other `hold_turns`
caller restores a saved value.

**How it is held.** `tests/runtime/test_kept_seal_restored_849.py`: the probe S4 (the gate is `None`
and the next prompt runs to its answer, not aborted by the old settle; on `0436193c` the gate is the
seal and the prompt is refused), a gate saved while the seal was kept and restored after its
settle ended, and a seal that had replaced a hold ("held by the launch") restored after its settle
ended, which puts that hold back (on `0436193c` the seal comes back in both); a guard, passing on
both, puts the seal back while its settle lives (refused with its reason, lifted when the settle
ends), and a unit test pins `_unspent` through two spent seals. The issue's S1–S3 print what
they printed on `0436193c`, and S4 prints `gate_after=NoneType:None prompt=ran`
(`.omc/probes/b8-1122/849-851-854/849/`); `tests/runtime/test_swap_settles_first_800.py` passes whole,
`test_a_hold_set_while_the_settle_lives_is_kept_when_it_ends` among it.

**Fix round 1** (Codex review C4 and C5, verify F9). A hold's end could put up a gate whose owner
had ended, or take down one that was still live. Hold A was up when a swap sealed; the swap was
interrupted with its settle alive; hold B saved the seal; A ended while B ran, and its restore put
`None` up over B's gate; then the settle ended, and B's restore of the spent seal put back A's hold,
which nobody would lift: every prompt was refused (C4, in round 1; on `0436193c` the seal itself
came back). And a hold up before an interrupted swap that ended while the settle still lived put
`None` up over the live seal, so the next prompt reached the model and the settle's re-abort ended
it (C5, on `0436193c` too). The change (the lead's call: C5 has C4's cause and is fixed with it):
`LateRouteHold.apply` ends through `AgentHarness.release_turns(gate, restore)`. While the hold's own
gate is up it is lifted for the gate the hold saved, as before; when a gate put up after it is still
up (a seal, another hold), that gate stays and the hold's gate is retired, and `_unspent` resolves a
retired hold's gate to the gate that hold saved, as it resolves a spent seal to the gate it
replaced, through every ended gate under it. A harness with no `release_turns` gets the plain
restore. And two swaps interrupted on a turn whose model ignores their cancels do stack two seals
(verify F9; this note's test file said no path did).

Held by `tests/runtime/test_kept_seal_restored_849.py`:
`test_a_spent_seal_does_not_put_back_a_hold_that_has_ended` (C4's probe: B's gate stays up while B
runs and no gate is left after both; in round 1 A's end took B's gate down to `None`),
`test_a_hold_that_ends_under_a_live_seal_leaves_the_seal_up` (C5's: a prompt is refused with the
seal's reason until the settle ends, then the next prompt runs and is not re-aborted; in round 1 and
on `0436193c` the gate was `None` at the hold's end), and
`test_two_interrupted_swaps_stack_their_seals_and_a_hold_across_both_puts_back_neither` (the
verifier's stacked case, and one whose outer seal stays up; it passes in round 1, fails on
`0436193c` with a seal put back, and kills an `_unspent` that follows one ended gate only). Mutants
of the fix round's #849 lines: 7 of 7 killed.

**Verify round 2, N3.** The retired gates were never forgotten: one entry for every hold that
ended under another gate, for the life of the harness. `AgentHarness.hold_turns` now forgets them when the gate it puts up is none
(`AgentHarness._unretired`). Nothing can put a retired gate back from then on: a hold restores the
gate it saved only while its own gate is up (`release_turns`), a seal only while it is the gate
(`_unseal_when_settled`), and with no gate up neither is; a hold or a seal that began earlier and
ends later finds its gate gone and restores nothing. A caller that restores a saved gate with a
plain `hold_turns` of its own, of which `packages/` has none (this note's list of paths), gets it
as written after such a moment. `test_the_retired_gates_are_forgotten_once_no_gate_is_up`: kept
while a gate is still up, empty once none is, a hold retired afterwards followed as before; on
fix round 1's head the entry stays. Mutants of these lines: 5 of 5 killed; the file's other tests pass
unchanged.
