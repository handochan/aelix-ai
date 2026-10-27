## Gemini CLI and Qwen Code: how they handle subagent timeouts, background runs, resume and ACP

**What I read:** gemini-cli at `363a13119a57354f469732577518b9d6b319d1d6` (2026-10-26) and qwen-code at `32f6713ce6394b48dc384d301ee761fb9a1b5b80` (2026-10-27). Both are shallow clones under `/private/tmp/claude-501/-Users-handochan-dev-aelix-ai/c17838ee-5ac7-4f59-857e-976fe2a7ce16/scratchpad/ref-gemini-qwen/`. I only used grep, sed and cat on them; nothing was built or run. Paths below are relative to `packages/core/src` unless they start with `cli/`.

**Short version:**
- **Gemini** runs subagents synchronously in the same process, with hard limits and one 60-second "final warning" turn when a limit is hit. It has no background subagents, no resume and no way for the parent model to reach a running child. Over ACP a subagent shows up as one opaque tool call.
- **Qwen** moved far from Gemini. It has no default limits, and every stop other than success returns the reason plus the partial result. It has a full background registry that reports back with `<\task-notification>`, plus `send_message`, `task_stop` and `list_agents` tools. It can resume children after a restart from per-child sidecar files, and it shows nested child tool calls over ACP.
- **For aelix**, Qwen is the model to follow for phases 0–4. Gemini mainly adds the grace-turn idea and examples of what to avoid.

### Q1 Delegation model
- **Gemini**
  - One tool takes `{agent_name, prompt}` (`agents/agent-tool.ts:43-72`) and sends it to a local or a remote (A2A) agent (169-205).
  - Children run in-process with a fresh chat. The first user message is the session memory plus any pending user hints plus the templated query (`agents/local-executor.ts:620-680`).
  - Children get copies of the parent's tools. Agent tools are removed: "We do not allow agents to call other agents" (204-208).
  - A child must finish by calling `complete_task`, with a typed `OutputConfig{outputName, schema}` (`agents/types.ts:351-371`; `local-executor.ts:1483-1491`).
- **Qwen**
  - The `agent` tool takes `description, prompt, subagent_type, model, run_in_background, name` (a named team member), `isolation:'worktree'`, `working_dir` and `fork_*` options (`tools/agent/agent.ts:273-310`).
  - Children run in-process. There are also forks that inherit the parent's turns, team backends (tmux, iTerm, in-process), and an external ACP executor.
  - Permission mode: a permissive parent mode wins, otherwise the agent definition's mode applies, and the default is auto-edit (`agent.ts:433-441`).
  - A child finishes when the model stops calling tools. There is no `complete_task`.

### Q2 Execution limits, and what happens when one is hit
**Gemini**
- **Defaults:** 30 turns and 10 minutes (`agents/types.ts:51,56`). The built-in agents set their own: generalist 10 min / 20 turns (`generalist-agent.ts:74-75`), codebase_investigator 10 / 50, cli_help 3 / 10.
- **How the time limit works:** a `DeadlineTimer` signal is merged with the parent's signal (`local-executor.ts:595-613`), so hitting it aborts the model call or tool that is running. The timer pauses while the child waits for user approval (601-606).
- **Recovery turn:** on TIMEOUT, MAX_TURNS, or when the model stops calling tools without calling `complete_task`, the executor runs one extra turn (759-808; also on the AbortError path, 836-890).
  - The injected user message is: "You have exceeded the time limit. You have one final chance… You MUST call `complete_task` immediately with your best answer and explain that your investigation was interrupted. Do not call any other tools." (424-445)
  - This turn gets a fresh 60 s timer (`GRACE_PERIOD_MS`, line 107), combined only with the parent's signal (467-490).
  - There is no recovery turn after a user abort (ABORTED) or an error.
- **Outcome:**
  - If the recovery turn succeeds, the outcome is rewritten to GOAL (774-777). Issue #22323 is about exactly this.
  - If it fails, the parent gets only "Agent timed out after N minutes." or "Agent reached max turns limit (N)." (780-800).
- The recovery turn was added in PR #12344 (2025-11-03).

**Qwen**
- **No default limits.** The checks are `options?.maxTurns && turnCounter >= maxTurns`, and the same for minutes (`agents/runtime/agent-core.ts:1061-1070`). Limits are set per agent (`subagents/types.ts:131-163`). Forks default to 200 turns (`tools/agent/fork-subagent.ts:43`).
- **Checked only between rounds** (1052-1070, 1344-1345). A model call or tool that is already running is not cut off. The one exception is the wait between API retries, which is cut off once the deadline passes (318, 1505-1510).
- **No grace turn.** The core returns `{text: finalText, terminateMode, turnsUsed}` (1519-1527). It adds LOOP_DETECTED and SHUTDOWN outcomes (`runtime/agent-types.ts:119-134`).

**Where they diverged:** Qwen replaced "limit → kill or grace turn" with "limit → partial result plus reason". It is UNVERIFIED whether Qwen forked before #12344 (the clone is shallow).

### Q3 What the parent receives when a child stops early
**Gemini**
- On success, timeout or max turns, the parent gets `Subagent 'X' finished.\nTermination Reason: …\nResult:` plus `data:{agentId}` (`agents/local-invocation.ts:314-322`).
- On user abort it throws AbortError, so the parent model gets nothing (284-298).
- On an exception it gets `Subagent 'X' failed. Error: …` (373).
- Partial work is lost unless the grace turn succeeds.
- If the user rejects one of the child's tool calls, the child is no longer killed. It is told to "rethink… use complete_task to report your findings and the blocker" (`local-executor.ts:1335`; fix for #21761).

**Qwen**
- `composeIncompleteResult` (`agent.ts:1541-1563`) builds the result in this order:
  1. the reason line;
  2. "Partial result follows:" (or "Output captured before the failure follows:" for errors), then the child's last text;
  3. a marker pointing to the child's transcript path.
- The result is capped at 32,000 characters, and the reason line is protected so truncation cannot remove it.
- For a user cancel the text is "Agent was cancelled by the user. Partial result follows:" (5112-5124).
- `subagentTerminalReason` tells the parent whether retrying makes sense, e.g. "It ran out of time, so re-running the same call will time out again; raise … instead" (1594-1611; PR #13624).
- For a cancelled background child, `finalizeCancelled(partialResult, stats)` "so the parent model still sees whatever work the agent had captured" (`agents/background-tasks.ts:1156-1182`).

### Q4 Background children
**Gemini:** subagents cannot run in the background. The execution-method type has no agent kind (`services/executionLifecycleService.ts:12-17`). Only shell commands have `is_background`.
- Background shell completions go through an `InjectionService` with two sources, `user_steering` and `background_completion` (`config/injectionService.ts:15`).
- If the parent is idle and model steering is on, the pending text becomes a new turn (`cli/src/ui/AppContainer.tsx:2643-2660`).
- The feature request for async subagents (#27414) was closed as stale.

**Qwen:** `run_in_background`, or `background: true` in the agent's frontmatter.
- **Tool result:** "Its result arrives as a <\task-notification> for this task_id in a later turn" (`agent.ts:4719`).
- **Contract:** every registered background child emits exactly one final `<\task-notification>` (`background-tasks.ts:8-21, 139-152`). Strings from the child are XML-escaped so a child cannot forge parts of the message (198-204).
- **Delivery:**
  - Notifications queue while the parent is busy and are delivered only when it is idle, combined into one turn (`cli/src/ui/hooks/use-llm-stream.ts:7130-7180`).
  - The queue holds 20 notifications. Agent results are never evicted, and anything dropped is reported as one summary line (`agents/background-notification-queue.ts:6-34`).
- **Non-interactive `-p`:** the run waits until no background task is unfinished. On SIGINT it aborts all children and flushes their notifications, so every task_started has a matching task_notification (`cli/src/nonInteractiveCli.ts:3721-3756`).

### Q5 Messaging a running child
**Gemini**
- The parent model cannot reach a running child, because the call is synchronous.
- The user can, with the experimental model-steering feature. Typed hints are broadcast to every running local agent and placed before its next turn (`local-executor.ts:632-647, 721-745`).
- Steering over ACP is only a proposal so far (#29900, `_geminicli.com/session/steer`).

**Qwen:** `send_message{task_id, message}` (`tools/send-message.ts:14, 280-384, 550-554`) behaves by child state:

| Child state | What happens |
|---|---|
| Running | The message is queued and delivered "at the next tool-round boundary". There is no inline reply, and the parent is told not to relaunch the task while waiting. |
| Paused (restored after restart) | The child resumes with the message as its first instruction. |
| Completed | The child is revived from its live runtime or from its transcript. |
| Cancelled or failed | Error. |

- `task_stop` cancels a running child and abandons a paused one (`task-stop.ts:243`).
- Neither project has a "wrap up now" command.

### Q6 Persistence and resume
**Gemini**
- Child transcripts are recorded under the parent session (`services/chatRecordingService.ts:786-790`), and a summary is saved on success. Local children cannot be resumed.
- For remote A2A agents, `contextId` and `taskId` are kept per agent name in an in-memory map, so the next call continues the same remote task (`agents/remote-invocation.ts:154-160`).

**Qwen**
- **What is saved:** each child has a JSONL transcript plus a `.meta.json` sidecar (`agents/agent-transcript.ts:82, 118-145`). The sidecar stores:
  - status, `toolUseId`, `parentAgentId`;
  - the restrictions: `resolvedApprovalMode`, `executionAllowedTools`, `disallowedTools`, `persistedCliFlags`;
  - `workflowOwner{pid,hostname}` and `resumeCount`.
- **On restore:** background children recorded as running or completed in that session come back as `paused` (`background-agent-resume.ts:500-514`).
- **When resume is refused**, it gives an explicit reason: missing transcript, identity mismatch, working-directory mismatch, or the fork runtime is unavailable (100-113).
- **Restrictions on resume:**
  - A saved permissive permission mode is downgraded in an untrusted folder (225-242).
  - The default continuation message is "Continue working on the current task from the last completed step." (97-98).
  - A resumed fork is told that earlier tool listings in its history are obsolete (104-106).

### Q7 Lifecycle and limits
**Gemini**
- No nesting.
- A parent abort ends the child as ABORTED, with no recovery turn.
- No background children, so no orphans.
- The A2A dispatcher timeout is 30 minutes (`agents/a2a-client-manager.ts:45-47`).

**Qwen**
- **Abort and approvals:**
  - Background children have their own abort controller: "background agents survive ESC cancellation of the parent's current turn" (`agent.ts:3874-3877`).
  - Tool calls in a background child that need approval are auto-denied by default (3879-3881).
- **Cancel:** it rejects parked approvals before aborting. If the child ignores the abort, a 5-second fallback timer emits the notification anyway (`background-tasks.ts:161, 1087-1133`).
- **Shutdown:** shutdown aborts every task registry (`config/config.ts:8246-8249`).
- **Caps:**
  - 10 concurrent background agents, adjustable via `QWEN_CODE_MAX_BACKGROUND_AGENTS`.
  - Per-model slots.
  - 32 finished rows kept (`background-tasks.ts:53-56, 152`).
  - Nesting depth 5 by default, 100 at most (`config/config.ts:1850-1852`).

### Q8 How the TUI shows running children
- **Gemini:** the tool card shows the child's recent thoughts and tool calls with their status (`agents/types.ts:91-115`; `cli/src/ui/components/messages/SubagentProgressDisplay.tsx`, `SubagentGroupDisplay.tsx`). Open bug #22589: running child tool calls are dropped from the UI state.
- **Qwen:**
  - Foreground and background children share one pill and dialog (`cli/src/ui/components/background-view/BackgroundTasksPill.tsx`, `BackgroundTasksDialog.tsx`, `LiveAgentPanel.tsx`).
  - Each child keeps a 10-item activity buffer.
  - Approval requests from background children are parked and can be answered from the dialog (`background-tasks.ts:1232-1323`).

### Q9 ACP and A2A
**Gemini**
- **Visibility:** a subagent is one opaque `tool_call`. `toToolCallContent` returns null for the child's progress object, so the host never sees the child's activity (`cli/src/acp/acpUtils.ts:47-83`).
- **Approvals:** a child's permission prompts go through the policy check tagged with the subagent name (`cli/src/acp/acpSession.ts:150-160`).
- **Cancel:** `session/cancel` cancels the pending prompt (`cli/src/acp/acpRpcDispatcher.ts:189-197`).
- **End reasons:** its newer agent protocol has `completed|failed|aborted|max_turns|max_budget|max_time|refusal|elicitation` (`agent/types.ts:365-380`), mapped from the child's outcome (`agents/local-subagent-protocol.ts:45-62`).

**Qwen**
- **Nested tool calls:** a child's tool calls are normal `tool_call` / `tool_call_update` messages with `_meta:{parentToolCallId, subagentType, provenance}` (`cli/src/acp-integration/session/emitters/tool-call-emitter.ts:156-183`).
- **Fallback for simpler hosts:** a progress update on the parent's tool card marked `_meta.subagentProgress:true`, "for clients that do not support nested tool calls" (338-372).
- **Approvals:** a child's permission request goes out as `session/request_permission` with `_meta.toolName`. Cancelling it aborts the agent tool (`SubAgentTracker.ts:340-360`; `Session.ts:16655-16672`).
- **Notifications:** delivered only when no prompt is pending (`Session.ts:3772-3777`).
- **Status:** hosts read status built from the sidecars (`tasksSnapshot.ts:319-380`).
- **Open:** sending mid-turn input to external ACP children (#11513).

### Q10 Most instructive bugs
1. **Gemini #22323 (open):** a successful grace turn is reported as GOAL, which hides that the child was interrupted. The code confirms it (`local-executor.ts:774-777`). Not fixed.
2. **Qwen #13597, fixed by PR #13624 (2026-10-11):** a timeout reached the parent as a generic "execution failed", so the parent retried the same call verbatim.
   - The fix puts the reason first, adds advice on whether to retry, and protects the reason from truncation.
   - PR #14008 then cleaned up the error text.
3. **Qwen #12207 (open):** notifications were held for 12–19 minutes while a turn waited on a question to the user, then arrived in one burst; overflow dropped some. The partial fix is the capped queue that protects agent results and summarises drops.
4. **Qwen #14273 (open):** after a restore, interrupted background children sit as `paused` with nothing to wake them. Unattended sessions deadlock until someone calls `send_message`.
5. **Qwen #14410 (open):** a parallel batch of agent launches produced no result, no error and no notification, and `list_agents` was empty.
6. **Qwen #15083 (open), related to #7156:** a user prompt sent while a background child ran executed inside the child's in-process context, with the child's tool policy and model. The guard is `runOutsideAgentContext` (`use-llm-stream.ts:7170-7180`).

Also relevant:
- **Gemini:**
  - #29257: cli_help hangs exactly its 3-minute limit.
  - #24412: a timeout surfaced as AbortError and the session fell back to a Flash model. The fallback is UNVERIFIED in code.
  - #25768: users asked for 60+ minutes and 100–500 turns; declined.
  - #21761: rejecting a tool call killed the child; fixed.
- **Qwen:**
  - #8097: the parent duplicates the child's work and treats the first notification as "all done".
  - #12038: the notification format has 7 separate, unsynchronised copies of the code that builds it.

### Borrow (ranked, with aelix phase)
1. **[P0]** Qwen's incomplete-result format: reason, retry advice, "Partial result follows:", last text, transcript pointer, with the reason protected from truncation (`agent.ts:1541-1611`). For aelix, add the tool trail, child session id and files changed.
2. **[P2]** Qwen's rule that every registered child produces exactly one final notification. Keep the 5 s cancel fallback and "reject approvals before abort". Use one escaped notification format with a single builder and parser.
3. **[P1]** Qwen's sidecar fields (tool allow/deny lists, permission mode, CLI flags, owner pid and host, status, resume count), plus explicit "cannot resume" reasons and the permission-mode downgrade.
4. **[P3]** Qwen's `send_message` behaviour by child state; this maps directly to aelix's steer and follow_up.
5. **[P2]** Qwen's non-interactive wait loop, with abort-all and notification flush on SIGINT.
6. **[P3]** Gemini's final-warning turn, reused as aelix's "wrap up now": fixed wording, a short separate grace timer, only the final answer allowed, and the original stop reason kept (to avoid #22323).
7. **[P4]** Qwen's `_meta.parentToolCallId` nested tool calls with the parent-card fallback, plus Gemini's end-reason list for child end events.
8. **[P2]** A bounded notification queue that never evicts child results and reports drops; concurrency and depth caps.
9. **[P2/P3]** Tool-result wording that discourages duplicate work: "arrives as a notification… do not relaunch".

### Avoid
- Hard default time limits. Gemini's 10 min / 20 turns and cli_help's 3 min are the same failure as #604.
- Reporting a recovered child as a success (#22323).
- Throwing AbortError on cancel with nothing attached (Gemini `local-invocation.ts:284-298`).
- Delivering results only when the parent is idle (#12207). aelix's plan to send a follow-up while the parent is busy is better; it should also cover a parent that is blocked waiting on the user.
- Restoring children as paused with nothing to wake them (#14273). aelix should tell the parent model, and give `-p` a rule: auto-resume or fail loudly.
- Silent spawn failure (#14410). Register the child before the tool returns, and return an error if registration fails.
- Running children in-process. That is the cause of #15083; aelix's one-process-per-child design avoids it, so keep it.
- Broadcasting steering to every running child (Gemini). Address children by id.
- Porting Qwen's code as-is: `agent.ts` is 5,436 lines and `Session.ts` is over 16,000. Port the contracts, not the code.

### How much these references help each phase
- **P0:** High. Qwen's result format is close to a drop-in spec, and Gemini shows that a grace turn alone still loses work.
- **P1:** Medium-high. Qwen's sidecar plus its "cannot resume" reasons is nearly a full spec. Gemini only has the remote A2A context reuse.
- **P2:** High. Qwen's registry, wait loop and caps carry over, and four open Qwen bugs (#12207, #14273, #14410, #8097) give a ready list of tests. Gemini offers nothing here.
- **P3:** Medium. `send_message` maps one-to-one, and Gemini's final-warning text is usable wording for wrap-up. Neither ships a wrap-up command.
- **P4:** Medium. Qwen's nested tool-call shape is the only working example. There is no standard ACP steering yet (Gemini #29900 and Qwen #11513 are both open).
