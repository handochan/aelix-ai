# 0307. A cancelled delegation's report is its call's result, and a session change settles the turn first

Status: Accepted (2026-11-19, #800). D1–D3 and the known limits K1–K6 are decided and implemented
here (lane L1 of #612's contract r5). #612's lane L2 adds D4–D8 (the RPC and ACP exposure of
delegated children) and K7–K9 to this record; until then nothing below changes a wire shape.
Date: 2026-11-19
Issue: #800 (a cancelled delegation's report does not reach an ACP client and is gone after a
reopen), taken into #612's lane by the owner (2026-11-18: "네 진행하세요"; lane b7f landed first).
#800's body records Studio's user decision of 2026-11-17: "beta.3에 들어오면 Studio 첫 Beta에
표시를 넣고, 아니면 다음 release로 넘긴다."
Design: `.omc/specs/612-contract-r5-2026-11-19.md` §5 (#800), §10.1, §10.3, §10.4 and §12.3 (L1).
The known limits are narrowings under the lead's rule for r5 (lead's call, r5 brief 2026-11-19): a
case outside the guarantee keeps the base's behaviour and is stated, never silently lost. Probes:
`.omc/probes/612/design-r5/` (`l1/l1_owed_probe.py`, `swap/swap_probe.py`,
`giveback/giveback_probe.py`) and this lane's `.omc/probes/800/impl/`.
Amends: ADR-0290 §8 and limit 8, ADR-0276 D14 (items 2–3) and its limit "The Esc report lives in
memory", ADR-0278 D7, ADR-0081 P-340 (the teardown order), ADR-0286 (what a swap gives back). Each
carries a dated note.
Relates: ADR-0235 (pi is the reference, not a parity target), ADR-0306 (the delegated child's
command inbox; unchanged), ADR-0277 (resume; unchanged), #612.
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
next prompt). A hold that saves and restores the gate across the settle's end is the exception (K4).

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
- **K4 — the swap's bound.** Step 0 waits for the settle at most `SWAP_SETTLE_SECONDS` (10 s); past it
  the swap goes on in today's order. A prompt still inside a `before_agent_start` handler (a dialog
  nobody answers) holds a swap or quit in `dispose()` until the handler returns, as before #800; on
  RPC such a dialog ends at 300 s. Two cases past the bound are worse than the base, both needing an
  `abort` handler that swallows its cancel or a teardown that outlives the bound, which no shipped
  extension does (the lead's narrow-and-finish call, lane verify round 3): a hold that saves and
  restores the gate across the settle's end (`LateRouteHold.apply`: `/agents use`, a late-route settle)
  restores the kept seal after `_unseal_when_settled` ran, and the harness refuses prompts until the
  next swap (verify round 3 F1, `.omc/probes/800/verify-r3/probes3/test_v3_seal.py::test_s4`); and a
  run's end on a stale context counts as headless, so the "did not land" line goes to stderr under the
  TUI too, reachable only from an extension command that swaps mid-turn because the TUI refuses its own
  swaps while a turn runs (verify round 3 F2, `probes3/test_v3_stale_ctx.py`).
- **K5 — ACP, one cancel, several aborts.** aelix-acp re-aborts a cancelled turn while the native
  streams (every 50 ms at `16d6555b`). A native whose cut path waits longer than that in a hook or its
  append reaches K1, and the next prompt's copy is not in the adapter's table.
- **K6 — ACP recovery.** aelix-acp's guarantee is the recorded terminal `tool_call_update`. A
  `session/load` from an empty table projects native user and assistant messages only, and one from a
  partial table replays without the report.

## Consequences

- A cancelled delegation's report is in the session file as the call's result: an RPC host's
  `get_messages` returns it live and after a relaunch with `--session`, and aelix-acp `16d6555b`,
  unchanged, sends it as the cancelled call's last `tool_call_update` and replays it on
  `session/load` (How it is held). #800 needs no aelix-acp change for this guarantee; K5 and K6 are
  aelix-acp's to lower (#612's lane L3; follow-up F2 of contract r5).
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
