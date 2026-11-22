# 0308. A delegated child asks a human through its parent before each change

Status: Accepted (2026-11-19, #612). Decided and implemented in lane L2 of #612's contract r5
(parts B and C on `feat/612-children-host-surface`). The ACP half (aelix-acp mapping
`childApproval` to `session/request_permission`) is the aelix-acp PR's, lane L3, and is not in
this repository. What holds each decision is in "How it is held", each with the condition it was
measured under; where the build differs from r5, "Where the build differs from contract r5" says
how, and which of those the lead accepted (the lead's calls after part B, 2026-11-19). Amended
2026-11-20 (#612, lane L3): the ACP half is built by aelix-acp PR #39 (`03b9e32`) for a client
that declares `aelix/clientActions`. Run live through ACP on macOS during the lane, before its
final commit: allow-once and reject-once, a `session/cancel` of an open question, a
`children/stop` with a question open and a `workspace/trust` restart with one open; the cancel and
the restart again on the final code (`03b9e32` minus aelix-acp #37's block and one docstring). The
note below names the lane checkpoint of each row. Not run live: "Yes, for this agent", a real 300 s
or 330 s wait, a cancel during `/compact` and one before the question's task ran (unit tests only),
K13 (a native test with a scripted model), Windows, Linux and Studio.
D5's ACP bullet, D6's void rule, K13, K16 and "Not run" carry dated marks, and "Note (2026-11-20,
#612)" says what was built and measured.
Amended 2026-11-20 (#855): the #855 note records the actor in tested job-backed host stops.
Amended 2026-11-21 (#855): the #855 note also covers legacy job-stop dispatch.
Amended 2026-11-22 (#855): the runner reads the actor from the stop in flight when its context is not yet bound.
Date: 2026-11-19
Issue: #612 (delegated children on RPC and ACP hosts). Owner, 2026-11-18, r3 Q2: **"자식 승인
통로도 #612에"**; approval Q-A: **"default로 돌며 물어봄 (권장)"**; approval Q-B: **"즉시 대화창
(권장)"**. #612's body: "자식의 승인 요청은 `session/request_permission`으로 보낸다", "이벤트 펌프는
막지 않는다. 펌프 안에서 승인 응답을 기다리지 않는다".
Design: `.omc/specs/612-contract-r5-2026-11-19.md` §7 (the protocol, the gate and the clamp, the
router and the arbiter, routing per host, the defaults, what is recorded), §10.2 and §10.4
(K10–K16). The approval design's scratch halves: `.omc/probes/612/approval-design/`; the
independent review of r4's §7: `.omc/specs/612-r4-approvals-review.md` and
`.omc/probes/612/approvals-review/`. This lane's evidence: `.omc/probes/612/impl/B/` (tests on the
base, mutants, pty captures with a scripted model) and `.omc/probes/612/impl/C/live/` (the real
model, TUI and RPC).
Amends: ADR-0197 §(e) (`DEFAULT → PLAN` holds for a child without the channel; the realized table
gains a column; the Deferred back-channel is done), ADR-0199 (R-A and the deferred back-channel),
ADR-0306 D1, D2 and D8 (`--command-inbox=2`, a fourth command and the child's `approval_request`
line, `default` is `plan` only for a child without the channel). Each carries a dated note.
Relates: ADR-0307 (the host surface this channel's events ride: `aelix_child` `approval`, the hub's
`epoch`), ADR-0302 (control-v1 additions: `ui/enable.childApprovals`, the `childApproval` dialog
method), ADR-0253 (what counts as mutating), ADR-0290 (a withdrawn request's call has the cut
result), ADR-0235 (pi is the reference, not a parity target).
pi: `earendil-works/pi` `origin/main` `5a14e69c0`. Its subagent example extension spawns each child
as `pi --mode json -p --no-session` with `stdio: ["ignore", "pipe", "pipe"]`
(`packages/coding-agent/examples/extensions/subagent/index.ts:389`, `:497-501`): no channel back
to the parent, and pi's core has no permission gate of its own (permission extensions decide,
`packages/coding-agent/src/core/extensions/types.ts:539-541`). aelix diverges knowingly: its
children run behind ADR-0197's gate, and the owner chose that a `default` parent's child asks a
human instead of being clamped read-only.

## Context

A delegated child is a `--mode json -p` process with no UI. Its permission gate blocks every call
it would ask a human about (`headless_default="block"`), and ADR-0197 §(e) clamps a `default`
parent's child to `plan` so that the block reads as plan mode's model-actionable refusal. Out of the
box a delegated child was read-only. ADR-0197 deferred the per-tool back-channel with four measured
blockers (stdin read to EOF, RPC responses dropped, no modal arbiter, §(e)'s derivation); ADR-0306
gave every child a command inbox on its stdin, which removes the first, and ADR-0275's
`RpcCommandUI` with its lock-free reply route removes the second.

The owner decided (2026-11-18) that the channel is part of #612, that a `default` parent's child
runs `default` and asks before each change (Q-A), and that a background child's request opens a
dialog at once (Q-B).

## Decision

### D1. Inbox version 2: the child writes `approval_request`, the parent answers `approval`

A child launched with `--command-inbox=2` serves ADR-0306's protocol plus two lines
(`cli/args.py` `COMMAND_INBOX_APPROVALS`, `COMMAND_INBOX_VERSIONS` = `1`, `2`; any other value is
still the launch-ending diagnostic, which now ends "it serves 1 and 2"). A row is launched with
`=2` exactly when it asks its parent (D3), so every other row keeps its argv byte for byte
(`build_child_argv(asks_parent=)` passes the flag value only when true).

- **Child → parent**, on stdout through print mode's writer, in stream order:
  `{"type": "approval_request", "id": "ap-<n>", "toolCallId", "toolName", "kind", "args",
  "yesLabel", "redirectLabel"}` — the gate's own `ApprovalRequest` fields, `args` whole (a change
  is approved on its full content, #395). A line over `APPROVAL_REQUEST_MAX_BYTES` (1 MiB,
  `modes/print_inbox.py`) is not written: the call is blocked, "This change is too large to show
  for approval (<n> bytes; the limit is 1 MiB)." — the parent drops a stdout line over 4 MiB
  silently, and a guard test keeps the bound under that one.
- **Parent → child**, on the inbox: `{"type": "approval", "id", "request": "ap-<n>", "decision":
  "yes"|"yes_session"|"no"|"cancel"|"redirect", "reason"}` through `CommandWire.request` (ADR-0306's
  5 s write and 30 s acknowledgement). The child answers `{"taken": true}`, or refuses "no approval
  'ap-9' is pending" for an id that is not open; a version-1 inbox answers `approval` as an unknown
  command.
- **While it waits** the call sits in the gate's prompt branch under the gate's lock (one open
  request per child); the inbox keeps serving `steer`, `follow_up` and `set_permission_mode`; the
  child has no timeout of its own (each host's bound is the parent's). The inbox's EOF ends the run
  (exit 1) with nothing run.

### D2. The gate asks the parent, and re-checks `plan` before it records a grant

`PermissionExtension.ask_parent` is set by `cli/entry.py` for a `--command-inbox=2` launch only
(`_command_inbox` binds the inbox's `ParentApprovals.ask`; no branch was added to `_async_main`).
Branch (d) becomes "no approval UI **and** no `ask_parent`": with it the call reaches the prompt
branch under the lock and goes to `_ask_parent` beside the TUI's dialog path, with the same
`ApprovalRequest`, the same decision mapping, the 1 MiB bound, and "Not approved: <reason>." when
the parent gave a reason. **Order** (§7 review L1): after the answer the gate re-reads its posture
and a `plan` there blocks the call **before** a `yes_session` grant is recorded, so a grant made
after a tighten admits nothing (`_parent_verdict`). **And under the lock, before it reads grants**
(fix round 1, approvals review P-M2 = verify V-F4): a child gate (`ask_parent` set) whose posture is
now `plan` blocks the mutating call with plan's refusal, so a grant made **before** a tighten admits
no call that waited on the lock across it
(`test_permission_ask_parent_612.py::test_a_grant_made_before_a_tighten_admits_no_call_parked_on_the_lock_after_it`
and `::test_a_parked_call_after_a_granted_one_and_a_tighten_is_blocked_by_plan`, both failing on
`111e02d7`, where the parked call passed on the grant). Those tests drive `_on_tool_call` directly;
in a running child two calls do not wait on the lock together today, because the loop runs each
call's `before_tool_call` one at a time (`aelix_agent_core/loop.py` `_prepare_tool_call`), so the
order this guards is not one a child was measured to produce. The parent's own gate (no
`ask_parent`) is unchanged. What asks is exactly what the base blocks: under
`default` every mutating call; under `auto-accept-edits` `bash`, a write outside the project or to a
sensitive path, any tool aelix did not build (ADR-0253). `plan` still blocks before asking, `yolo`
asks nothing, the guardrail runs first. "Yes, for this agent" is the child's own session grant
(its wildcard: a command prefix, a directory, a tool); the parent's session grants never answer a
child (its keys are relative and a child may run in a subdirectory).

### D3. The clamp's `asks_parent` column (owner Q-A), fixed on the row

`posture.child_permission_mode(…, asks_parent=False)`: with `asks_parent` a `default` parent's
`inherit`, `auto` and `ask`-with-UI child runs `default`; every other cell is unchanged and every
cell stays at or below the parent (ADR-0199 §3.9):

| parent | `inherit` / `ask`+UI | `auto` | `ask` no UI | `deny` |
|---|---|---|---|---|
| `plan` | `plan` | `plan` | `plan` | `plan` |
| `default` | **`default`** (was `plan`) | **`default`** (was `plan`) | `plan` | `plan` |
| `auto-accept-edits` | `auto-accept-edits` | `auto-accept-edits` | `plan` | `plan` |
| `auto` | `auto-accept-edits` | `auto-accept-edits` | `plan` | `plan` |
| `yolo` | `yolo` | `auto-accept-edits` | `plan` | `plan` |

Without `asks_parent` it is the base table, and the project-scoped `auto` recursion passes the
column too (§7 review L2). `asks_parent` is whether the process's `PermissionExtension` has a
`child_approver` bound, read once when a spawn is planned through a getter `cli/entry.py` hands
`AgentsExtension`; it rides the grant (`SpawnGrant.asks_parent`), the plan and the row
(`RunningChild.asks_parent`), which fix the argv, the router and every later floor:
`tighten_all` tightens each row to its own floor, and a host that withdraws its approver after the
spawn leaves the child asking and the arbiter answering `cancel`. Every clamp site passes it (the
three consent doors, the extension's grant, both batch reads, `tighten_all`). The spawn dialog's
first label for a `default` clamp reads "Run and ask before each change (default)". Hooks see a
call's arguments by reference after the gate, the same limit the parent's own approvals have (K14).

### D4. The router and the arbiter

- **Router** (`aelix_agents/child_approvals.py`, one `ChildApprovals` per channel row, built beside
  its wire and carried by a Ctrl+B move as the wire is). The stdout pump diverts a line beginning
  `{"type": "approval_request"` before the reducer and the wire's correlator and calls `offer`,
  which never awaits: it starts one task (#612's body). The task builds a
  `subagent_contract.ChildApprovalRequest` (the child's profile, row id, job id, session id, the
  `agent` call that started it, its directory and the hub's `epoch`; `call_id` through
  `wire_call_id`), sets the row's note `awaiting approval: <tool>` (the `/jobs` table, the job
  status line and `Child.approval`), asks the arbiter and writes the answer on the child's inbox.
  It answers without asking, `cancel`, in three cases: the row has a recorded wrap-up ("the agent
  was asked to wrap up"); a second request while one is open ("another approval request is open");
  a line with an id it cannot read ("the approval request could not be read"). A line with no
  readable id cannot be answered: the row says "an approval request could not be read; stop it"
  (K15).
- **Arbiter** (`PermissionExtension.decide_for_child`, product core). Parent posture `plan` →
  `no` "the parent is in plan mode"; no `child_approver` → `cancel` "no one can answer approvals
  here"; otherwise it takes the gate's lock, checks `plan` again under it, and awaits
  `child_approver(request)` raced against the posture becoming `plan` (through
  `PermissionPosture.listeners`, synchronous on every `set`): if the posture wins the dialog is
  cancelled and the answer is `no` with that reason; the approver raising → `cancel` "the approval
  prompt failed (<type>)"; `plan` after the answer → `no`. The router writes with no await after
  the arbiter returns (`CommandWire.request` writes before its first suspension), so a tighten
  decided before the answer gives `no`, and one made after a `yes` follows it on the child's wire
  as its `set_permission_mode`. It is handed to `AgentsExtension` and, in `_Launch`, to each
  background runner.
- **How an open request ends** (one reason per cause; the child's model reads "Not approved:
  <reason>." unless noted):

  | Event | Parent | Child |
  |---|---|---|
  | `yes` / `yes_session` / `redirect` written | step `allowed` | re-reads its posture (`plan` → blocked), then records a `yes_session` grant and runs |
  | `no` / `cancel` written | step `denied` | "Denied by the user." / "Denied by the user (cancelled)." / "Not approved: <reason>." |
  | the parent tightens to `plan` | the arbiter answers `no`, "the parent is in plan mode", for the open request (its dialog closed) and each queued one | blocked; its own `set_permission_mode plan` follows on the same wire |
  | a wrap-up is recorded | the open request is answered `cancel`, "the agent was asked to wrap up", **then** the final steer is written; later requests of that row are answered so at once while it stays recorded; a `not_sent` take-back lifts it | reads `cancel`, then the final steer |
  | a stop (any door), Esc on the parent's call, a session swap, the row's end | no answer written yet → the dialog is withdrawn (TUI unmounted, RPC future dropped), step `withdrawn`; the answer's write had begun → step is that decision | killed (ADR-0291), or its run is over |
  | answer written, delivery `unknown` | step is the decision sent; the row says "the answer's delivery is unknown; it may have been read" (K12) | may have read it and run |
  | answer `not_sent`, or refused unwritten by a broken or closed wire | step `withdrawn`; the row says "the answer was not sent; stop or resume it"; a request that arrives while the wire is so is recorded that way at once, with no one asked (fix round 1, approvals review P-M1) | held at its gate: runs nothing |
  | a late answer | ignored (its dialog was withdrawn first) | refused "not pending" |

  No answer is written twice. An answer that cannot be delivered does not stop the child (unlike a
  tighten that cannot be applied, ADR-0306 D8): a held child runs nothing, and a child that read a
  `yes` ran what a human approved.

### D5. Who answers, per host (owner Q-B)

- **TUI**: `tui/shell.py` binds `child_approver` to the dialog runner it binds for the parent's
  own approvals (`_child_approver`). The dialog gains a header row above the child's own title —
  `<profile> agent <id> asks`, then ` · background job <id>` for a background row and ` · in
  <dir>` when the child runs in another directory than the parent (`ChildApprovalRequest.requester`;
  a steering character in it is named, never sent) — and its rows read **Yes / Yes, for this
  agent / No**. A dialog with no `requester` is byte for byte what it was. A child's dialog
  waits behind this process's own approval prompt (the gate's lock), and opens only when no modal
  at all is open — another dialog, a consent prompt, an extension's dialog or a panel such as
  `/jobs` — waiting behind it otherwise (`AelixChrome.until_no_modal`, bound in `run_tui`). That is
  the lead's call in fix round 1 on verify V-F3, which measured it mounting over any modal that
  did not hold the gate's lock; it reads #612's body: "열린 대화창이 없으면 즉시 열고, 있으면 그 뒤에 줄을
  선다". While it waits it holds the gate's lock, so this process's own prompts and other
  children's dialogs wait behind it, in order. With none open a background child's request opens
  the dialog at once, starts no turn and wakes no model (owner Q-B). For
  `CHILD_APPROVE_HOLD_SECONDS` (0.5 s) after its first paint a child's dialog takes no approving
  key — Enter on Yes, `y`, `s`, `1`, `2` and the redirect's `p` are dropped, not queued — while No
  and Esc answer at once; the parent's own dialogs are unchanged (the lead's call on approvals
  review P-L4: Q-B opens it while you may be typing). The hold starts again each time the dialog is
  back on top after a modal that mounted over it (a consent, an extension's dialog) closes: the
  chrome's `modal_generation` changes with the modal on screen, and from that change until the
  dialog's first paint on top again, and for 0.5 s from that paint, no approving key is taken (fix
  round 2, F2, the lead's call; at `510b186c` the second Enter of a double press on the covering
  dialog approved the child's call, verify r2's `probes/tui/tui_queue_and_reexpose.py reexpose`).
  Esc answers `cancel` for that child only.
  shift+tab is inert while a modal is open, so a TUI tighten cannot cross an open dialog.
- **`-p` and json**: no approver, so `asks_parent` is false at every spawn: argv `=1`, the base
  clamp and the base gate.
- **RPC**: a host opts in with `ui/enable {enabled: true, childApprovals: true}`, answered
  `{enabled: true, childApprovals: true}` (`ui_action`, moved out of `_dispatch`, ADR-0300: 26 →
  25). The plane then binds its dialog client's `child_approval` as the process's
  `child_approver`, through the `permissions=` it is given (`run_rpc_mode` and `RpcControlPlane`,
  passed by `cli/entry.py`). `childApprovals` without `enabled: true`, a plane with no permission
  extension, or a runtime with no delegation seam binds nothing and answers `false`; the plane
  never takes back another approver's binding. A request is one `extension_ui_request` with
  `method: "childApproval"` (a literal, so control-v1's `_ui_methods` sees it), `title`
  (`<requester>: <the child's title>`), `child` (`id`, `profile`, `sessionId`, `jobId`,
  `parentToolCallId`, `background`, `cwd`, `epoch`; both call ids in the `wire_call_id` form
  `aelix_child` and `children/list` carry — `parentToolCallId` since fix round 1, verify V-N1,
  `tests/agents_ext/test_child_approvals_612.py::test_the_requests_parent_call_id_is_the_one_aelix_child_carries`),
  `tool` (`callId`, `name`, `kind`, `args`) and `choices` — `yes` (allow_once), `yes_session` "Yes, for this agent" (allow_always), `no`
  (reject_once) (`rpc/control_ui.py` `CHILD_APPROVAL_CHOICES`). There is no `options` array, and any
  reply value that is not an offered choice id is a cancel, so a client that renders it as a
  generic dialog can approve nothing; so is a reply marked `cancelled`, whatever value it names
  (pinned in fix round 1, verify V-F5 U1). `choices[].kind` is a closed enum, `choices[].id` and
  `tool.kind` open. A cancel names its cause: the host's own reply (it carries the request's id) →
  "the host cancelled the request"; the dialog client closed (`begin_shutdown`) → "the host is
  gone"; anything else → "no answer from the host within 300 s" (`DIALOG_TIMEOUT_SECONDS`). A
  `ui/enable` that takes the plane's binding back (any of the cases above that bind nothing) also
  ends the plane's open `childApproval` requests as "no one can answer approvals here"
  (`RpcCommandUI.withdraw_child_approvals`): the gate's lock is released and the host's later
  reply is ignored (fix round 1, approvals review P-L3, which measured the request left open and a
  later `yes` approving; `test_child_approvals_rpc_612.py::test_turning_child_approvals_off_ends_the_open_request_as_no_one_to_answer`,
  failing on `111e02d7`). On RPC
  the parent's own calls never take the gate's lock for a dialog (`provides_tool_approval` is
  false), so a child's request and the parent's own approval can be open together (K13).
- **ACP** (the aelix-acp PR, lane L3; proposals, contract r5 §8.4): send `childApprovals: true`,
  map `childApproval` to `session/request_permission` with the nested call id
  `<child id>:<callId>` and the three options, cancel open requests on `session/cancel`, ask even in
  its Yolo mode, and answer a `childApproval` whose `child.epoch` is older than the native's current
  epoch `{cancelled: true}` without asking. aelix-acp `16d6555b`, unchanged, sends `ui/enable
  {enabled: true}` only, so its children are launched without the channel and run as before.
  *Amended 2026-11-20 (#612, lane L3): built by aelix-acp PR #39 (`03b9e32`), only for a client that
  declared `aelix/clientActions` (the lead's call, narrower than Q-A: another Ask client's child runs
  under `plan`, as before). Beyond these proposals, `session/cancel` also ends the open questions
  during a manual `/compact`; a question whose task a `session/cancel` ended before it first ran is
  answered `{cancelled: true}` once, where native had waited its 300 s; the adapter waits 330 s,
  past native's 300 s (the lead's call); and a nesting client's request keeps its nested card's
  title. The first three are held by unit tests and were not run live (Note (2026-11-20, #612)).*
- **WebSocket** (`aelix-server`): no delegation seam, so `childApprovals: false`.
- A grandchild cannot exist (`MAX_SUBAGENT_DEPTH = 1`; children run `--no-agents`); if the depth
  rises, one hop at a time.

### D6. What is recorded, and the host's void rule

The child's session holds the call and its result (the real one, or the refusal text). The parent's
session gains nothing new; the `agent` card's trail shows a refused call as failed. Grants are in
memory, so a resumed child asks again; its posture is ADR-0277's `resume_clamp` with the resuming
host's `asks_parent` (K11). On the wire the replayable record is `aelix_child`'s `approval` event
(ADR-0307 D5), `step` ∈ `asked`, `allowed`, `denied`, `withdrawn` — the withdraw signal the dialog
channel lacks: a host's dialog stays open after a withdraw and its late reply is ignored. The old
generation's frames reach a host until the rebind (ADR-0307 D4), so a `withdrawn` step emitted
during a session swap arrives; an open `childApproval` whose `child.epoch` is not the epoch of the
host's latest `children/list` or `aelix_child` frame is void, and the host closes it. aelix itself
compares no epoch: such a request's answer is ignored because the session change withdrew it first
(`ChildApprovals.withdraw` cancels the arbiter's task, which drops the dialog's future, and
`RpcCommandUI.respond` ignores an id that is no longer open) — the mechanism approvals review P-L5
traced on every stop path. *Amended 2026-11-20 (#612, lane L3): on ACP, aelix-acp PR #39 is the host
that closes it. On the child's end, a `withdrawn` or `denied` step, or a newer epoch (an
`aelix_child` frame, or a `children/list` or `children/events` reply) it answers native
`{cancelled: true}`, which native ignores, and sends ACP's `$/cancel_request {requestId}`. A client
that ignores that notification keeps its dialog open, and an answer given in it has no effect; a
question from an epoch the adapter never saw is still asked. Note (2026-11-20, #612).*

## Where the build differs from contract r5

Items 1, 3, 4, 5, 8 and 10 are among the seven deviations the lead accepted as written after part B
(the lead's call, 2026-11-19); items 2, 6, 7 and 9 are part B's implementer's calls, recorded here.

1. **An answer that ended `not_sent` records the step `withdrawn`** (r5: the step is the decision
   sent once a write began; a `not_sent` write never began). The row says "the answer was not sent;
   stop or resume it".
2. **A `refused` reply** (the child said "not pending": only a race) records the decision sent and
   clears the row's note; r5 named no step for it. A `refused` the parent's own wire gives without
   writing the line — broken (`INBOX_BROKEN`) or closed (`ended`) — is not this: since fix round 1
   it is recorded as `not_sent` (approvals review P-M1, which measured it recorded `allowed` with
   nothing written; `test_child_approvals_612.py::test_an_answer_the_wire_refuses_unwritten_is_recorded_not_sent`
   and `::test_a_request_on_a_wire_that_cannot_answer_asks_no_one_and_is_recorded_not_sent`, both
   failing on `111e02d7`).
3. **A request answered at once, without a dialog** (a recorded wrap-up, a second request, an
   unreadable one with an id) emits **no** `approval` frame: it never opened. The child's tool
   result carries the answer.
4. **The wrap-up waits for the cancel's acknowledgement** (`runtime.wrap_up` awaits
   `approvals.wrap_up()`, at most ADR-0306's 30 s) before it writes the final steer, so the order on
   the one wire is guaranteed, at the cost of that wait; an answer whose write had begun is not
   answered again.
5. **`ui/enable` without `childApprovals` unbinds the plane's own earlier binding** and answers v1's
   `{enabled}` byte for byte, without the field (r5: "absent → `childApprovals: false`").
6. **An RPC reply value that is not an offered choice** (`"redirect"`, `"Yes"`) is a cancel with
   "the host cancelled the request".
7. **`ChildApprovalRequest` lives in `subagent_contract.py`**, since product core types the RPC
   dialog with it.
8. **`APPROVAL_REQUEST_MAX_BYTES` lives in `modes/print_inbox.py`** and is a named product-core
   constant, so `tests/agents/test_p2_band_boundaries.py`'s `_PRODUCT_CORE_CAP_ALLOWLIST` names it
   with its reason (a framing bound on the child's wire, not a delegation cap).
9. **The TUI's foreground `agent` card** shows "running" while its child's dialog is open; r5 asked
   for `awaiting approval` on `/jobs` and the panel only, and they have it.
10. **`child_approval` reads no clock** (r5 §7.4: "its own monotonic clock past
    `DIALOG_TIMEOUT_SECONDS`"). An asyncio timer may fire up to its clock's resolution early
    (15.6 ms on Windows), so "elapsed ≥ the bound" would have called a timeout the host's cancel;
    the host's own reply is the one that carries the request's id.

Part B's other deviation (Ctrl+B's adoption door and the ended-owner epoch) is ADR-0307's.

## Known limits

Each is a narrowing under the lead's rule for r5 (a case outside the guarantee fails closed or keeps
the base's behaviour, and is stated).

- **K10 — no version fallback.** A child launched with `--command-inbox=2` by a parent whose
  installed code no longer accepts it (an editable checkout changed under a running parent) ends
  with the launch diagnostic: the delegation fails closed; there is no fallback to `=1`.
- **K11 — resume keeps the old ceiling.** A child first run without the channel (before #612, or
  under `-p`/json) keeps its `plan` ceiling when resumed where the channel exists (`resume_clamp`).
- **K12 — an answer of unknown delivery.** An answer whose write ended `unknown` may have been
  read: the child may run the call while the row says its delivery is unknown.
- **K13 — concurrent requests on a host.** One dialog at a time per parent holds in the TUI only.
  On RPC and ACP a child's request and the parent's own approval can be open together (with the
  aelix-acp PR, two `session/request_permission` requests in one session). *Amended 2026-11-20
  (#612, lane L3): built by aelix-acp PR #39 and tested with a real native: a parent's and a child's
  request open together, both answered
  (`tests/test_native_children.py::test_a_parents_and_a_childs_request_open_together_are_both_answered`).*
- **K14 — hooks by reference.** Hooks see a call's arguments by reference after the gate, the same
  limit the parent's own approvals have.
- **K15 — an unreadable request.** An `approval_request` line the parent cannot read and that
  carries no readable id cannot be answered: the child stays held at its gate (it runs nothing) and
  the row says so until it is stopped.
- **K16 — ACP outside a turn.** Whether ACP clients accept `session/request_permission` outside a
  prompt turn (a background child while the client is idle, owner Q-B) was not read; with the
  aelix-acp PR a client error is answered `cancelled` (fail-closed). *Amended 2026-11-20 (#612, lane
  L3): aelix-acp PR #39 waits 330 s (the lead's call), past native's 300 s, so a question nobody
  answers ends at native's bound and the child reads "no answer from the host within 300 s", not
  "the host cancelled the request" (read from native's source; a unit test with the bounds
  shortened, a real 300 s or 330 s wait not run). Native asks one child question at a time, so the
  next child's waits behind it; a question a `session/cancel` ended before its task ran is answered
  at once and no longer holds the next one for 300 s (unit tests; not run live). Live, aelix-acp's
  test client was asked while no prompt ran (the cancel row); whether Studio or AionCore accept
  such a request is still not known.*
- **Not run**: a typed `/jobs wrap` with a request open (the dialog is modal and takes the keys;
  the model's `agent(action="wrap_up")` was run instead), "Yes, for this agent" in a live run, the
  "host is gone" reason live, Windows and Linux live (macOS only; the real-child tests are POSIX
  only), and ACP. *Amended 2026-11-20 (#612, lane L3): parts of ACP were run live with aelix-acp
  PR #39 on macOS — allow-once, reject-once, a `session/cancel`, a `children/stop` and a
  `workspace/trust` restart with a question open, each at the commits the note names. Still not
  run live through ACP: "Yes, for this agent", a real 300 s or 330 s wait, a cancel during
  `/compact` and one before the question's task ran (unit tests only), K13 (a native test with a
  scripted model), and Windows, Linux and Studio (Note (2026-11-20, #612)).*

## Consequences

- In the TUI, and on an RPC host that sent `ui/enable {enabled: true, childApprovals: true}`, a
  `default` parent's child is no longer read-only: it runs `default` and asks a human before each
  change its gate would ask about. Under `-p`, json, an RPC host that did not opt in, and aelix-acp
  `16d6555b`, nothing changes: the argv is byte for byte the base's, and the child is clamped as
  before. *Amended 2026-11-20 (#612, lane L3): under aelix-acp PR #39, an Ask (`default`) session's
  child asks too when the ACP client declared `aelix/clientActions`; for any other ACP client it is
  clamped as before.*
- A background child's question interrupts you at once in the TUI, between your own turns, without
  starting one (owner Q-B), unless another dialog or a panel is open, behind which it waits; for
  its first half second it takes no approving key.
- A tighten to `plan` and a wrap-up answer for you; a stop withdraws.
- Product core gains `ask_parent`, `child_approver`, `decide_for_child`, the inbox's
  `ParentApprovals`, `RpcCommandUI.child_approval` and `ChildApprovalRequest`; the bundled
  extension gains the router. Product core still imports nothing of `aelix_agents`.
- Complexity (ruff 0.15.13, `--isolated --select C901`, re-measured in fix round 2 on `567753c0`
  and the round's head, `.omc/probes/612/fix-r2/c901.txt`): no listed score rose (`runtime._run`
  21, `RpcControlPlane._dispatch` 26 → 25, `_prompt` 12, `_async_main` 156 → 155, `run_tui` 317,
  `AgentSessionRuntime._settle_rebuilt` 19); `JobService.start` 12 → 14, `run_approval_dialog`
  12 → 14, `PermissionExtension._on_tool_call` 9 → 10, `child_permission_mode` 6 → 8,
  `_pump_stdout` 8 → 9, `AelixChrome._modal_changed` 2 → 5; no new function passes 20 (ADR-0300).

## How it is held

Tests, each failing on `567753c0` unless marked a guard (part B ran them on a detached `567753c0`
worktree with its own venv: `.omc/probes/612/impl/B/onbase/`, where five of these files fail at
import). The clamp file, measured again in fix round 1 on a detached `567753c0` with its own venv
after its base-table guard became a test of its own (verify V-N3), has 114 tests: 61 fail and 53
pass, the 53 being the 50 base-table guards and three `tighten_all` rows
(`.omc/probes/612/fix-r1/R1-b/onbase/clamp-on-567753c0.txt`; verify V-F2 had found the earlier
"57 of 61" stale at 64 tests, 61 failing):

- D1: `tests/cli/test_inbox_v2_612.py` — a real `=2` child with a scripted model: `yes` runs the
  call and the request follows its start; `no`, `cancel`, a reason and `redirect` without a label
  refuse it; "Yes, for this agent" is the child's own grant and the next such call is not asked; a
  steer is served and a late answer refused while a request is open; a tighten to `plan` while it
  waits blocks the call after a `yes`; the inbox's EOF ends the run with nothing run; a 1 MiB
  change is blocked unsent; guards: a version-1 child never asks and is blocked headless, the
  request bound stays under the parent's line bound, version 2 parses and every other version is
  still refused, a version-1 inbox answers `approval` as unknown. POSIX only.
- D2: `tests/builtin/test_permission_ask_parent_612.py` — a `default` child asks instead of
  blocking (guard: without `ask_parent` it is blocked as before); what asks is what the base
  blocks; each refusal is the child model's sentence; a `plan` set while it asked blocks a `yes`,
  records no grant, and a parked sibling does not run (the §7 review's `p1-gate-lock-posture.txt`
  shape); the arbiter: a human's answer passes, `plan` refuses unasked, no approver cancels, `plan`
  set while queued on the lock answers `no` without a dialog, `plan` set while the dialog is open
  closes it and answers `no`, `plan` after the answer answers `no`, a raising approver cancels
  naming it, a cancelled arbiter closes its dialog and releases the lock, two answers in one turn
  take the first.
- D3: `tests/agents_ext/test_approval_clamp_612.py` — the table with the column, and without it
  (the base table, a guard that passes on the base), the project-scoped `auto` recursion, the spawn dialog's label, a row that
  asks is launched `=2` and every other argv is byte for byte, `tighten_all` takes each row's own
  floor, the model doors and the runtime door carry the channel read once, a batch's later waves
  keep the grant's column and lose it when the UI goes.
- D4: `tests/agents_ext/test_child_approvals_612.py` — steps and notes; the step is the decision
  sent; no arbiter and a raising one; the pump diverts a request and keeps reducing while its
  answer pends; a second and an unreadable request are answered `cancel`; a line with no readable
  id is not answered and the row says so; `unknown` and `not_sent` are never written twice; the
  wrap-up answers `cancel` first and then writes the final steer, and its `not_sent` take-back lifts
  the at-once cancel; a stop before any answer withdraws and writes nothing; a stop after the
  answer's write began records the decision; the run's end, a second cancel of the parent's turn,
  both job runners' stops and `HostControls.stop` withdraw first; a real child launched by the
  channel runs what the arbiter approved (POSIX); the extension's `run`, `spawn` and user doors
  ask this process's arbiter.
- D5: `tests/tui/test_child_approval_dialog_612.py` (the header, its job and directory, a steering
  character named, "Yes, for this agent", a dialog without a requester byte for byte, the TUI's
  approver answering in the wire's words); `tests/rpc/test_child_approvals_rpc_612.py`
  (`ui/enable`'s matrix, another approver never taken back, nothing bound without a permission
  extension or a seam, the request's shape and three choices, any other value a cancel, the bound
  and the host gone with their own reasons, dialogs off raising so the arbiter cancels naming it,
  shutdown ending an open dialog as the host gone, the steps reaching the host as `aelix_child`
  `approval` frames); `tests/cli/test_rpc_children_plumbing_612.py` (the CLI hands RPC its
  permission extension and the agents extension its channel getter and arbiter). The control-v1
  fixtures (ADR-0302 D9): five new cases in `tests/rpc/control_v1/fixtures/ui.enable.json` (on,
  dialogs off, absent unbinds and answers `{enabled}` alone, no delegation seam on the `bare` rig,
  during a turn), three in `event.extension_ui.json` (a `childApproval` frame answered `yes`, so the
  child's call runs; cancelled, and answered with a label, so it is refused "the host cancelled the
  request"), the `approval` case in `event.aelix_child.json` and the awaiting-approval row in
  `children.list.json`; the rig's arbiter is the process's own `PermissionExtension`, given to the
  plane as `permissions=` (`tests/rpc/control_v1/rigs.py`). On `567753c0` `ui/enable
  {childApprovals: true}` answers `{"enabled": true}` and no `childApproval` frame comes
  (`.omc/probes/612/impl/C/onbase/base-fixture-answers.txt`).
- Fix round 1 (part R1-b; each fails on `111e02d7`, run on a detached `111e02d7` worktree with its
  own venv — where a test names a symbol R1-b added, the copy run there spells the base's, listed in
  `.omc/probes/612/fix-r1/R1-b/onbase/` — unless marked a guard or a pin):
  `test_child_approvals_612.py` — an answer the wire refuses unwritten, and a request on such a
  wire, are recorded `not_sent` (P-M1); a refusal the child gave still records the decision (guard);
  a kind the dialog does not draw is asked as `other` (pin, V-F5 A12).
  `test_permission_ask_parent_612.py` — the two parked-call orders of D2 (P-M2 = V-F4).
  `test_child_approval_dialog_612.py` — the 0.5 s hold for each approving key and the redirect's
  (P-L4); the hold again, before the repaint and 0.49 s after it, when the dialog is back on top,
  and the verify's double-Enter shape on the real chrome (F2, each fails on `510b186c`: the key
  approved), `modal_generation` moving only with the modal on screen; No and Esc at once, and the
  parent's own dialog at its first paint and back on top (guards); the approver
  waits for `until_no_modal`; with one modal or two stacked, the child's dialog waits and opens
  when none is left, then answers its own key (V's probe shape); a tighten while it waits answers
  `no` and leaves no waiter; `run_tui` binds `child_approver` behind the chrome's wait (V-F3, and
  V-F5 T1: removing the binding fails it). `test_child_approvals_rpc_612.py` — four ways to turn
  `childApprovals` off end the open request (P-L3); other dialogs stay open; a host answer before
  the turn-off stands (guard); a `cancelled` reply naming a choice is a cancel (pin, U1).
  `test_batch_executor.py::test_with_the_approval_channel_a_widening_also_starts_from_default`
  (V-N2; passes on `111e02d7`; on `567753c0` it fails only with a `TypeError` on the
  `asks_parent` keyword the base's `child_permission_mode` does not take, verify r2's
  `base-vn2.txt`: it pins the widening's floor here, not a behaviour the base got wrong).
- TUI pty captures at 120x40, fix round 1, with a scripted local model, a background child asking
  six seconds after its spawn (`.omc/probes/612/fix-r1/R1-b/tui/`). Head, `/jobs` open when it
  asked: the panel stayed on screen with the row `awaiting approval: bash`, no dialog; Esc closed
  the panel and the dialog appeared; an Enter sent as soon as its header was on screen left it
  open and nothing written; an Enter 1.3 s later wrote the file. Head, no panel: an Enter as soon
  as the header appeared left the dialog open, and `n` 1.3 s later refused the call. `111e02d7`:
  the dialog mounted over the open panel at once, and with no panel the same first Enter approved
  and wrote the file.
- Mutants (`.agents/skills/aelix-tests-that-bite/scripts/mutants.py`) on every product line group
  part B changed: all killed, seven of them by tests added after the first pass
  (`.omc/probes/612/impl/B/mutants/`). The one line in `run_tui` that binds `child_approver` was not
  mutated (no unit seam; the pty captures show it bound); verify round 1 removed it and no test
  failed (V-F5 T1), and since fix round 1 `test_run_tui_binds_the_childs_approver_and_it_waits_for_the_open_modal`
  fails without it. Fix round 1 (R1-b): 32 mutants on the product lines it changed, run against the
  four approval test files it touched, 31 killed (`.omc/probes/612/fix-r1/R1-b/mut/`, among them
  verify's survivors T1, U1 and A12). The survivor drops `ask_parent is not None` from D2's in-lock
  check, so it would apply to the parent's own gate too; it also survived `tests/builtin` whole. By
  reading, nothing reaches it there: the parent's own gate holds the lock only while its dialog is
  open, shift+tab is inert under a modal, and on RPC the parent's own calls never take the lock
  for a dialog (K13).
- Pythons and platforms: the fourteen files part B touched, 3.11 and 3.14 (931 passed, 6 skipped
  each), Linux docker 3.12 (933 passed, 4 skipped), the coarse Windows clock on the six timing and
  ordering files (143 passed) (`.omc/probes/612/impl/B/`).
- TUI pty captures at 120x40 with a scripted local model (`.omc/probes/612/impl/B/tui/`): the
  dialog and its header; `y` runs, `n` and Esc refuse; a spawned child asks while the parent is
  idle (the dialog at once, two parent model requests in all); `job(action="list")` shows
  `awaiting approva…`; `· in pkg/web`; two dialogs one after the other; the model's wrap-up closes
  an open dialog and the child reads the cancel before the final steer.
- Live with `openai/gpt-4.1-mini` through OpenRouter, macOS, scratch HOME, agent dir and cwd
  (`.omc/probes/612/impl/C/live/README.md`). TUI at 120x40: the header `builder agent sub-… asks`
  over `Run shell command?`, `y` wrote the file and `n` gave "Denied by the user."; a spawned child
  asked while the parent was idle and the parent's assistant messages stayed at 2 while the dialog
  was open; two spawned children's dialogs came one after the other, the second 0.7 s after `y` on
  the first; `· in pkg/web`; Esc gave "Denied by the user (cancelled)." and the parent's turn went
  on; `job(action="list")` showed `awaiting approva…`; the model's `agent(action="wrap_up")` closed
  an open dialog and the child read "Not approved: the agent was asked to wrap up." before the final
  steer. A real `--mode rpc` parent: `ui/enable` answered `{enabled: true, childApprovals: true}`;
  the request carried every field above; `yes` gave steps `asked` → `allowed` and the file; `no`
  gave `denied` and "Denied by the user."; with the bound shortened to 8 s in that process only (a
  `sitecustomize.py`), no answer gave "Not approved: no answer from the host within 8 s."; a
  request during `agents/run` was answered `yes` while that call held the lock; `permissions/set
  {mode: "plan"}` with a request open gave the step `denied` 10 ms later, the late `yes` drew
  nothing, and the child read "Not approved: the parent is in plan mode." Fix round 1 (R1-b), the
  same model and setup with a foreground child: `ui/enable {enabled: true}` sent while its request
  was open gave the step `denied` before the host's late `yes`, which drew nothing, and no file was
  written; on `111e02d7` the late `yes` gave `allowed` and the file was written
  (`.omc/probes/612/fix-r1/R1-b/live/`). Fix round 2 (F2), the same model, TUI at 120x40 with an
  `-e` extension that opens a `select` over a background child's open dialog: Enter on the select,
  then a second Enter 50 ms later, left the child's dialog open 0.4 s and 1.6 s later, `n` gave the
  child "denied" and no file was written; on `510b186c` the second Enter approved, the dialog was
  gone at 0.4 s and the child wrote the file (`.omc/probes/612/fix-r2/live/`, `head2/` and `prev1/`).

## Note (2026-11-20, #612): aelix-acp PR #39 builds the ACP half

aelix-acp PR #39 (lane L3), commit `03b9e32` (based on aelix-acp main `11fb4fe`), builds D5's ACP
bullet against this repository's `83714045`. Lines below are `handochan/aelix-acp` at `03b9e32`;
how it was tested and on what is in ADR-0307's note of the same date (macOS arm64 only).

**Who is asked.** When the ACP client declared `clientCapabilities._meta["aelix/clientActions"]`
and the native lists `ui/enable` and `children/list`, the adapter sends `ui/enable {enabled: true,
childApprovals: true}` (`session.py:118-124`, `:399-400`; `tests/test_children.py`
`UiEnableTests`). A child of an Ask (`default`) session then asks before each change, as Q-A has it
(owner, 2026-11-18: "default로 돌며 물어봄 (권장)"), and a background child's question while the
session is idle is sent at once (Q-B, "즉시 대화창 (권장)"). Keeping the questions to clients that
declared client actions is the lead's call and narrower than Q-A: for any other ACP client an Ask
session's child runs under `plan`, as before (approvals review p7, with a real native).

**How it is asked.** Each `childApproval` becomes `session/request_permission` with `toolCallId`
`<child id>:<callId>` and the offered kinds as `allow-once` "Allow once", `allow-always` "Allow for
this agent" and `reject-once` "Reject", mapped back to the choice's own id (`session.py:77-79`,
`Session.child_approval` `:1032-1092`). Only a selected, offered option approves; a cancelled
outcome (also one naming an option), a client error, a question of a cancelled turn and one from
an epoch the adapter has seen replaced are `{cancelled: true}`. The approvals review sent 11
malformed or not-offered replies to real questions and none approved; the check behind it is
pinned by a test since fix round 1 (verify round 1's surviving mutant M53, killed in round 2). It
is answered outside the session lock, so the parent's own request (the bridge) and a child's can
be open together (K13). The adapter asks
whatever its own mode: with a question open, a switch to Yolo and back left it open, and Allow then
wrote the file (approvals review p13).

**Beyond D5's proposals:**
- `session/cancel` ends every open child question also during a manual `/compact`, before its
  early return (`Session.cancel` `:1471-1479`; approvals review LOW-3;
  `VoidQuestionTests.test_session_cancel_during_a_manual_compaction_ends_it`);
- a question, or an extension's dialog, whose task a `session/cancel` ended before it first ran is
  answered `{cancelled: true}` once by the task's done-callback (`Session.answer_unstarted`
  `:760-779`); before, native got no answer and waited its 300 s, with the next child's question
  behind it (verify round 2 F2; `CancelBeforeTheDialogRanTests`, which fail on the build before,
  and verify round 3's interleavings, none answered twice);
- the adapter waits 330 s (`CHILD_QUESTION_SECONDS`, `session.py:76`; the lead's call), past
  native's 300 s (`DIALOG_TIMEOUT_SECONDS`, `rpc/control_ui.py`), so native's own bound and reason
  end an unanswered question (approvals review LOW-4;
  `VoidQuestionTests.test_native_owns_a_questions_timeout_and_the_adapter_only_backs_it_up`,
  with the bounds shortened; a real 300 s or 330 s wait was not run);
- a client that declared `aelix/nestedToolCalls` keeps its nested card's title, the tool's name;
  only a client without nesting gets native's descriptive title (`:1063-1067`; approvals review
  NIT-5).

**D6: the adapter closes a dropped question.** On the child's end, the question's `withdrawn` or
`denied` step, or a newer epoch — an `aelix_child` frame, or a `children/list` or `children/events`
reply — the adapter ends its wait, answers native `{cancelled: true}` (which native ignores) and
sends `$/cancel_request {requestId}` (`server.py:70-89`, `Session.child_progress` `:811-818`,
`observe_epoch` `:891-896`). ACP's `$/cancel_request` is a notification a client may ignore; its
dialog then stays open and an answer given in it has no effect (verify round 2's reading of ACP v1;
live, a late Allow sent after the `children/stop` and after the `workspace/trust` restart below
wrote nothing — the two rows that sent one). A question from an epoch the adapter never saw is
still asked (`VoidQuestionTests.test_a_question_from_an_epoch_never_seen_is_still_asked`).

**K13** is built and tested with a real native and a scripted model, not run live:
`tests/test_native_children.py::test_a_parents_and_a_childs_request_open_together_are_both_answered`.
**K16:** native asks one child question at a time, so one nobody answers holds the next child's for
up to native's 300 s, and a question a cancel ended before its task ran no longer does. Studio's and
AionCore's handling of a request outside a turn, and of `$/cancel_request`, was not run.

**Run live through ACP** on macOS with `openai/gpt-4.1-mini` through OpenRouter, aelix-acp's
black-box test client and a native at `83714045`. Each row names the aelix-acp commits it ran at,
which are the lane's local checkpoints, squashed into `03b9e32` and not published: `5ca9ad5`,
before both fix rounds (`.omc/probes/612/l3/impl/live/`); `8533b45`, fix round 1's product code
(`fix-r1/live/`); and `8e932ac`, whose product code is `03b9e32`'s minus aelix-acp #37's block and
one docstring (`fix-r2/live/`):
- allow-once and reject-once (`5ca9ad5`, `8533b45`): the parent's own `agent` call asked with two
  options; a child's write asked under its nested id, allow-once wrote the file and reject-once did
  not;
- a `session/cancel` of an open question (`5ca9ad5`, `8533b45`, `8e932ac`): a background child's
  question while no prompt ran, already opened by the client and held there; the child read that
  the write was not approved and the file was absent, and at `8533b45` and `8e932ac`
  `$/cancel_request` was sent (the `5ca9ad5` driver did not record it);
- `children/stop` with a question open (`8533b45`): `$/cancel_request` at once, the nested card
  `failed`, a late Allow wrote nothing;
- a `workspace/trust` restart with a question open (`8533b45`, `8e932ac`): the restart took 1.0 s
  and 1.88 s; `$/cancel_request`, the nested card `failed`, a late Allow wrote nothing.

Not run live through ACP: "Yes, for this agent", a real 300 s or 330 s wait, a cancel during
`/compact` and one before the question's task ran (unit tests only), K13 (a native test with a
scripted model), and Windows, Linux and Studio.

## Note (2026-11-20, #855) — job-backed host stops

The runner copies the latched actor (from its job context, or from the stop request in flight
when the service has not yet handed it the context), withdraws any open approval, then marks the
row stopped.
The spawned and adopted request-stop tests in `test_child_approvals_612.py` exercise this order's
effects on the row and approval. ADR-0307's #855 note covers the running-child and pre-runner
adoption cases that identify the same host in the stopped envelope and job result.

The legacy dispatch tests in ADR-0307's #855 note exercise the same call through an old-signature
facade and a forwarding job service. The existing runner stop calls still withdraw approvals.
