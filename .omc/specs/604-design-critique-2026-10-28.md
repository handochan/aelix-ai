# #604 phase-0 design critique (rev 1), base 7e76f925

The findings are ordered by severity. Line numbers are at 7e76f925. A/ = `packages/aelix-coding-agent/src/aelix_agents/`, C/ = `.../aelix_coding_agent/`. I made no edits and ran nothing that writes.

## CRITICAL

**1. Pressing Esc gives the parent model no envelope at all. The D10 "turn was cancelled" reason can never be produced, and L2 cannot pass.** (D5, D10, L2) — CONFIRMED
- Trace:
  - TUI Esc → `_safe_abort` → `harness.abort()` → `turn_task.cancel()`.
  - `_stream_and_reap` catches `CancelledError`, calls `_eager_abort`, then `raise` (A/print_channel.py:1379-1386).
  - `_run` re-raises after writing the unsettled records (A/runtime.py:1036-1043). `_execute` has no handler (A/extension.py:1347-1358).
  - The harness adds an empty aborted message to memory only (aelix_agent_core/harness/core.py ~5440-5480).
  - On the next turn, the provider transform answers the orphaned `agent` tool_use with `"No result provided"` (aelix_ai/providers/_transform_messages.py:306).
- Result: after Esc — the human bound D5 relies on — the model gets no reason, no trail, no session and no files. It is likely to delegate the same task again from scratch, which is the exact outcome (B) exists to prevent.
- `row.stopped` is set only by `abort_child` (print_channel.py:960). `runtime.stop(id)` has no product caller: grep found none in C/tui/commands.py or C/rpc/control.py. So "(stopped by the session…)" only lands in a session that is being torn down.
- Smallest fix, pick one:
  - (a) Say plainly that phase 0 does not cover Esc. Rewrite L2 to drive `stop_all` instead, and file a follow-up.
  - (b) In `_execute`'s `except CancelledError`, build the envelope from `row.stream` and append it as a context-visible message to the parent session before re-raising.
  - Either way, delete the "turn was cancelled" branch.

## HIGH

**2. `/agents run` loses its only bound and cannot be cancelled.** (D5, D6) — CONFIRMED
- D6 says "nothing to strip there". But the 10-minute default reached this path through print_channel.py:1076-1080.
- The handler is awaited inline in the REPL loop (C/tui/shell.py:4131), calling `await runtime.spawn(resolved, task)` (C/tui/commands.py:1280).
- `set_running(True)` wraps only `harness.prompt` (shell.py:4293). So:
  - Esc is filtered out (C/tui/chrome.py:945).
  - Ctrl+C with an empty buffer only arms a double press, which calls `request_eof()`. That merely enqueues EOF (chrome.py:1345-1352), and nothing reads the queue while the handler is awaited.
  - `harness.abort()` would not reach it anyway, because there is no turn task.
- Scenario: `/agents run fixer "…"` with a child stuck on a stalled stream hangs the TUI until the user kills the process from outside.
- Fix: run the handler as a task under `set_running(True)` and send Esc to `task.cancel()`.
- RPC `agents/run` is cancellable (control.py:108), but it holds the mutation lock for its now-unbounded lifetime (ADR-0275 §2). Note that in ADR-0276.

**3. As written, D9 removes stderr and exit-status diagnoses from the model's view whenever there is a partial answer.** (D9, D10) — CONFIRMED
- `resolved_error` is `error or state.error_message or STREAM_ENDED_EARLY` (A/envelope.py:479-489). stderr reaches the model only through `_select_summary` (:288-292).
- The design's claim that "error_message and stderr go to `error`" is not true of the code. It would be a new change, and D9 does not list it.
- Scenarios:
  - (a) The child says "I'll now run the tests", then the OOM killer sends SIGKILL. That gives exit -9, no stderr and no `error_message`. The status is `error`, the error is `None`, and the D10 reason ("first line of the error") is empty.
  - (b) A stderr-only diagnosis: `print(str(exc), file=sys.stderr); exit_code = 1` at C/modes/print_mode.py:444-446, or a traceback. Today it displaces the partial. After D9 it is visible nowhere: not in the tool text, and not in the `/agents run` panel (commands.py:1060-1063).
- Fix: in `build_result`, when the run is not ok and the selected summary is not the stderr text, set `resolved_error = error or <final error> or stderr_clean or f"exited with code/signal {exit_code}"`. Give D10 that same fallback.

**4. A stale `error_message` from a retried turn becomes the reason for a different failure.** (D9, D10) — CONFIRMED
- `state.error_message` is last-non-empty-wins (A/stream.py:615-617). The docstring at envelope.py:259-276 documents that retried 429s leave it behind.
- Scenario:
  - Turn 1 gets a 429 and auto-retries.
  - Turn 2 writes partial text.
  - The child is then killed, or exits 1.
  - The reason line reads "Error code: 429…", for a rate limit that was already recovered.
  - On a `stop_all` abort, the "Error: 429" note sits under "stopped before it finished".
- Fix: track `state.final_error`, the error of the last assistant `message_end` only when its `stop_reason == "error"`, reset on every assistant message. Use it for the reason and the note; keep the stale value in `details`.

## MEDIUM

**5. The D11 and D12 failure template would wrap refusals and declines.** — CONFIRMED
- `_error_result` returns ok=False, status=`error`, summary=error=message (A/runtime.py:1334-1343). It is returned for draining, the live cap and the budget (:940-951), and also at :631 and :750. `declined_result` is returned at :655 and :744.
- All of these go through `render_subagent_result` (extension.py:1358).
- Scenario: a 5th live child. The model reads "did not finish … Do not delegate the same task again from scratch — this work was already done". That is false; the right move is to wait and retry. Never-started batch members (aggregate.py:138-142) are hit the same way.
- `session_id is None` cannot mark a refusal: a parent running `--no-session` has no `receipt.child` (runtime.py:1018-1020).
- Fix: add `started: bool` and gate both templates on it.

**6. Putting the session path in model-facing text breaks an accepted rule.** (D11) — CONFIRMED
- ADR-0243:315 and envelope.py:46-49 both say "No path is ever written: it would put a home directory … into the model's context."
- Fix: render the id only, or amend ADR-0243:315 explicitly. §3 does not list it today.

**7. Removing `AgentProfile.timeout_ms` changes RPC `agents/show`, and the warn-once has nothing to read.** (D2) — CONFIRMED
- control.py:753-754 returns `asdict(self.agents.show(name))`, and `show` returns an `AgentProfile` (agents/service.py:226). Removing the field drops the `timeout_ms` key for hosts. §5 only considers the keys `agents/run` gains.
- `ResolvedProfile` carries only name, profile, source_path and scope (subagent_contract.py:223-230). Without the field, nothing at spawn time knows the key existed, so D2's spawn-time `logger.warning` has no input.
- Fix: keep `timeout_ms` as a field that is deprecated, never read, and parsed leniently. Keep `"timeout_ms"` in `_KNOWN_KEYS` (profile.py:110); otherwise the generic loop at :311-315 emits a second warning.

**8. Changed-file paths are relative to the child's cwd and are rendered raw.** (D8, D11) — CONFIRMED
- The child can run in a different directory (`cwd=pending.cwd`, extension.py:1353 → `child_cwd` in `_run`).
- Scenario: `agent(..., cwd="packages/api")` edits `src/app.py`. The parent reads `Files changed: src/app.py` and opens the wrong file.
- Fix: in `_run`, resolve each path against `child_cwd`, then render it relative to the parent's cwd.
- Also:
  - Define which list wins when a path is both changed and unconfirmed.
  - A comma-joined list is ambiguous for paths that contain ", ". Render one path per line.
  - Route paths through `_strip_control` (it collapses newlines, which matters for forgery — stream.py:361-373) and through `safe_for_terminal` for the TUI. The #178 rule is at panel.py:202-221. "Control characters only" is weaker than both.

**9. The byte budgets do not close.** (D11, D12) — CONFIRMED
- D11 puts no byte cap on the file lists: 64×512 chars for changed plus the same again for unconfirmed. That comes on top of a 50 KiB summary, a 4 KiB trail and a 4 KiB error.
- D12 adds four items but budgets only three; files get no share. The existing `Error:` note already takes up to budget//2 (aggregate.py:240-243).
- Test 10 caps the total at 64 KiB (aggregate.py:63), and the split as written cannot meet it.
- Fix: give files a fixed share with "+N more", and fold the note into the same split.

## LOW

10. **D4 — the 30 s bound is safe, but it reports a finished run as a failure.** The child closes stdio only at process exit, so its MCP and extension teardown happens before EOF, inside the main wait. A reap after the bound turns a complete stream into `error` (exit≠0, envelope.py:433-438), and D11 then prints "did not finish … Do not delegate again" above a complete answer. Fix: if `saw_agent_end` is set and the stop reason is not error or aborted, keep the run ok and add a note. — CONFIRMED
11. **D10 mislabels a child's own abort.** `aborted` without `row.stopped` comes from the child's own stream stop reason (envelope.py:456-457), not from a cancelled turn. — CONFIRMED
12. **D12's chain rule is a no-op.** `_run_chain` stops at the first `ok=False` (A/batch.py:397-399), so `{previous}` only ever carries ok summaries. Say so; do not add failure text to it. — CONFIRMED
13. **D3 misses a pass-through.** The table omits `runtime.spawn` :607→:681. The method must keep accepting `timeout_ms` (D6, pinned by tests/agents/test_subagent_contract.py:180) but stop forwarding it. — CONFIRMED
14. **Docs and ADR form.** — CONFIRMED
    - This repo marks partial changes with `**Amended YYYY-MM-DD (ADR-xxxx, #nnn): …**` headers (ADR-0197:4-14) or `**Amended by:**` (ADR-0157:137). `Superseded by:` is a whole-document status (docs/00-conventions.md:41-48), so "Superseded markers" on sections of 0197 and 0199 do not fit the convention.
    - Missing from the update list:
      - `docs/05-post-beta-direction.md:657`
      - ADR-0199 :39, :56-57, :395, :472, :482, :818, :851, :1264
      - ADR-0197 :1076-1077, :1085, :1280, :1344
      - docstrings at runtime.py:168-171, profile.py:237, batch.py:25-30 and aggregate.py:140
      - the README "no spend ceiling" section (README.md:282-289)
    - Reserve the number 0276; parallel sessions (#610) may also need an ADR.
15. **The success pin is too weak.** test_tool_and_security.py:1058 pins a stub run with no session, trail or files, so it cannot catch new fields leaking into success output. Add an ok pin with those fields filled. — PLAUSIBLE
16. **TUI SIGTERM/SIGHUP.** The handler (shell.py:2217-2224) only sends EOF and exits the chrome, while the loop sits in `await harness.prompt` (shell.py:4296). The process and its child linger until the delegation ends, which used to be at most 10 minutes. D5's "quit calls stop_all" holds only after the turn ends. Fix: also call `harness.abort()`. — PLAUSIBLE
17. **Shared semaphore.** The batch semaphore is module-level per event loop (batch.py:420-426). A host running several sessions on one loop could now be starved without limit, where before it was at most 30 minutes. I did not check whether aelix-server does this. — PLAUSIBLE

## Checked and fine
- Esc does reach a running sequential `agent` call: `abort()` → `turn_task.cancel()`. The cancel path is synchronous `_eager_abort`, and the bounded settle uses `SETTLE_JOIN_SECONDS`.
- `-p`: `asyncio.run` (cli/entry.py:4623) turns SIGINT into a main-task cancel that reaches the turn. print_mode handles SIGTERM and SIGHUP.
- The post-kill drain (print_channel.py:1407-1416) is reachable only from the deadline branch, so it is safe to delete.
- There is no other work deadline in aelix_agents. The remaining time constants are reaper grace, drains, settle join and the panel throttle.
- `_await_terminator(done, None)` works and its death leg still ends the wait. The annotation needs `float | None`.
- `_Batch.deadline` feeds only the member clock (batch.py:471). Wave admission and `_live_floor` do not depend on it, and `wall_ms` is only a measurement.
- `outcome_records` reads named fields (child_session.py:557-570), and `CUT_BY_THE_PARENT` keeps `timeout` for old records.
- New defaulted fields at the end of `SubagentResult` are safe for positional construction and for `asdict` JSON. The 100k guard covers `details` and `tool_trail` only.
- The `/agents show` TUI view and `profile_to_argv` never read `timeout_ms`.
