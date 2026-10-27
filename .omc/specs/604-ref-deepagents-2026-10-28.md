## deepagents + LangGraph + Agent Protocol: reference report for aelix delegation phases 0–4

**SHAs read** (shallow clones under `.../scratchpad/ref-deepagents/`): deepagents `3413029245643eb9915b506def3e4817af548f0e` (2026-10-27), langgraph `858891aaa164ebdacf33b8f5e7f88b38b9132211` (2026-10-26), agent-protocol `eb5b840fce51029aa4984553646ec4c121ee3fcf`, deepagentsjs `bf83c27fd322a56082590c3426bbcea9014a5b6e`. deepagentsjs adds nothing the Python version lacks. Its `completion_callback.ts` is deprecated in favour of the parent-notifier.
**Path abbreviations:** DA=`deepagents/libs/deepagents/deepagents/middleware/`, LG=`langgraph/libs/langgraph/langgraph/`, SDK=`langgraph/libs/sdk-py/langgraph_sdk/`, DC=`deepagents/libs/code/deepagents_code/`.

### Q1 Delegation model
- **Sync `task` tool**, schema `{description, subagent_type}`. Unknown keys are rejected, because models were putting instructions under an invented `prompt` key (DA`subagents.py:455-490`).
  - It runs **in-process** as a nested graph: `await subagent.ainvoke(subagent_state, config)` (DA`subagents.py:868-896`). There is no subprocess.
  - Context in isolated mode: only `HumanMessage(description)` plus parent state minus excluded keys (messages, todos, skills, rubric…) (`:404-436`, `:834-835`). Shared keys like `files` flow both ways.
  - `mode="fork"` (experimental) passes the parent history with the dangling tool-call AIMessage popped, plus a preamble (`:366-396`). A fork that calls `task` again is refused (`:79`, `:842`).
  - Tools, permissions and `interrupt_on` are inherited unless the spec overrides them (`graph.py:722-806`). A `general-purpose` subagent is always available (`subagents.py:521-527`).
- **Async subagents** use separate tools: `start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks` (DA`async_subagents.py:255-301`).
  - Each task is a **run on an Agent Protocol server**, remote or same-deployment ASGI. `task_id == thread_id`.
  - The child's thread metadata stores `subagent_name`, `parent_thread_id` and `parent_assistant_id` (`:387-402`).

### Q2 Waiting vs execution limits
- The SDK has **no wall-clock limit on subagents**, only a step limit.
  - A deep agent binds `recursion_limit: 9_999` (`graph.py:1021-1024`). LangGraph's default is 10007 (LG`_internal/_config.py:40-42`).
  - At the limit LangGraph raises `GraphRecursionError` (LG`pregel/main.py:3094-3102`). That is a hard error, not a graceful stop.
- **Graceful variant:** the `RemainingSteps` managed value (LG`managed/is_last_step.py:18-24`). The prebuilt agent answers "Sorry, need more steps…" when fewer than 2 steps remain (`prebuilt/chat_agent_executor.py:432-439`).
- **New cooperative drain** (PR #7274, merged 2026-04-29):
  - `RunControl.request_drain()` is a single-attribute flag (LG`runtime.py:79-104`), checked at each superstep boundary (LG`pregel/_loop.py:683-689`).
  - It raises `GraphDrained`: "checkpoint is saved and the run can be resumed later" (LG`errors.py:55-65`).
  - Nested graphs inherit the parent's control (LG`pregel/main.py:2970`), so draining the parent also drains in-process subagents.
- **CLI limits:** opt-in `--max-turns` and `--timeout` (hard cancel, exit code 124), headless only (DC`main.py:2765-2783`).
  - A core time-budget middleware was declined (#4670). "subagent controls" for tokens/cost/time is still open (#6265).
- **Status reads never kill a child.** Each per-task status pull is capped at 5 s and skipped on timeout (DA`async_subagents.py:1482`, `:1670-1675`).

### Q3 Partial results
- **Sync `task` on success:** returns the last *non-empty* AIMessage text, walking back past Anthropic's trailing empty `end_turn` message, or the `structured_response` JSON, plus changed state keys (DA`subagents.py:747-787`).
- **Sync `task` on failure:** the exception propagates with **no partial output**. ToolNode's default handler re-raises everything except validation errors (`prebuilt/tool_node.py:391-399`), so the parent run fails too.
- **Async check:** success gives final content blocks; error gives `run.error` only; cancelled gives status only (DA`async_subagents.py:925-945`).
  - Notifications describe errors by exception type only, because messages "can carry internals" (`:587-589`).
  - Results are clipped at 80k characters with a pointer to `check_async_task` (`:454`, `:581-584`).
  - Work is recovered by **continuing the thread**, not by returning it: a follow-up "sees the full conversation history" (`:1188-1191`).
- **Dangling tool calls:** `PatchToolCallsMiddleware` adds `ToolMessage(status="error")` with "did not complete - no result was recorded. It may have been cancelled or interrupted." (DA`patch_tool_calls.py:17-52`). This is the behaviour #5546 asked for, instead of the old "user cancelled" + success.
- LangGraph #5672 (open, 52 comments): cancelling loses streamed state that was never checkpointed.

### Q4 Background
- `start_async_task` returns immediately (DA`async_subagents.py:809-850`). Completion is reported two ways:
  - **Child pushes, but only to an idle parent.** `_ParentNotifierMiddleware` reports once per run, from the top-level graph only, on `aafter_agent` (success) or on a model-call exception (error) (DA`_parent_notifier.py:30-95`).
    - `_awake_parent` starts a run on the parent thread only if its status is in `{idle, error}`, with `multitask_strategy="reject"` (`:622-646`).
    - It never wakes a parent that is waiting for a human, since that would cancel the approval. A 409 (parent became busy) or 404 (parent gone) is logged and dropped.
  - **A busy parent pulls.** `abefore_agent` and `abefore_model` poll unfinished tasks with `asyncio.gather` and inject notifications (`:1601-1624`, `:1644-1668`).
    - `aafter_model`: after a final answer it polls once more, and on news returns `jump_to: "model"` for another turn ("the tasks themselves bound how many there are") (`:1630-1642`).
    - Deduplication is by status/run_id comparison and `_record_notification` (`:1677-1697`).
- **Notification framing:**
  - It is a user-role message headed `[SYSTEM NOTIFICATION - NOT USER INPUT]` with `<\task-notification>` XML.
  - Child text is HTML-escaped so it cannot close the tags.
  - Each status gets a next-step hint (`:501-551`, `:576-578`).
  - The message is marked with `additional_kwargs["deepagents_notification"]` (`:458-466`, `:1489-1492`).
  - Notifications only work on the async path; with sync `invoke` only the tools work (`:1522-1527`).
- **Join/wait:**
  - SDK `runs.join` and `runs.join_stream(cancel_on_disconnect=False, last_event_id=…)` (SDK`_async/runs.py:1070-1130`).
  - Agent Protocol `GET /runs/{id}/wait`. Waiting never cancels the run.

### Q5 Intervention
- `update_async_task` calls `runs.create(same thread, multitask_strategy=…)` (DA`async_subagents.py:120-131`, `:1162-1199`):
  - `"interrupt"` (default): cancel the current run and restart with history.
  - `"enqueue"`: queue behind the current run. If that run pauses for a human, the request is dropped.
  - `superseded_run_ids` lets a stale notification be rewritten as "not the result of the follow-up" (`:522-541`).
  - Only one queued follow-up per task per model turn is allowed (`:1072-1090`).
- **LangGraph enums:**
  - `MultitaskStrategy`: reject / interrupt / rollback / enqueue (SDK`schema.py:81-88`).
  - `CancelAction`: interrupt, or rollback, which also deletes the run's checkpoints (`:137-142`).
- **There is no true steer.** LangGraph #9046 (open, opened by a maintainer):
  - interrupt and rollback lose the in-flight step and orphan tool calls; enqueue waits for the whole run.
  - The deepagents CLI keeps typed messages in a client-side `_pending_messages` deque until the agent is idle (DC`app.py:4572`).
  - The Agent Protocol streaming spec already defines `input.inject {namespace, message}`, and a `run.start` that injects into an active run (`streaming/protocol.cddl:463-470`, `:1045-1060`). Whether any server implements it is UNVERIFIED; langgraph-api is closed source.
- **`interrupt()` / `Command(resume=…)`:** on resume the node re-runs from its start, and resume values are matched by order (LG`types.py:903-925`).
  - A child waiting on a human shows status `waiting`. The parent cannot answer it; the user answers on the child's thread (`:294-301`).
  - Resuming an interrupted async task at the exact point is still open (#3008).
- **Cancel:** cancels the tracked run, then earlier runs newest-first so a queued run does not start (`:1212-1224`, `:1274-1296`).

### Q6 Persistence & resume
- **Checkpointing:**
  - A checkpoint is written per superstep, with durability `sync | async | exit` (LG`types.py:98-104`).
  - Each task's writes are saved as it finishes (LG`pregel/_loop.py:461-557`). On resume those writes are reapplied, and only unfinished tasks re-run from the node start (`:1000-1016`).
  - The prebuilt agent dispatches each tool call as its own `Send` (`chat_agent_executor.py:846-850`). So after a crash, finished tool calls are not re-run and in-flight ones re-run (at-least-once).
  - #8039 (open): under `durability="sync"`, the order of `put_writes` and `put` is not enforced, so post-crash replay vs re-execution depends on the host.
- **After cancel (not crash):** the next run's `before_agent` patches the dangling tool calls.
- **Subgraph checkpointer setting:** None inherits the parent's, True is persistent, False is none (LG`types.py:108-114`).
- **Restrictions are not stored in the checkpoint; they are rebuilt.**
  - Sync subagents are compiled from their spec (`graph.py:722-806`).
  - Async follow-ups reuse `assistant_id=spec["graph_id"]` on the same thread (`async_subagents.py:1177-1183`).
  - ACP `load_session` requires a session metadata marker and refuses a different cwd (`libs/acp/deepagents_acp/server.py:394-420`).

### Q7 Lifecycle & cancellation
- **ACP cancel** is a flag checked only between stream chunks (`server.py:508-510`, `:1004-1027`). A long tool that emits nothing cannot be interrupted promptly.
- **CLI cancel:** `runs.cancel(wait=True, action="interrupt")` under `asyncio.wait_for`, gathered over all active runs (DC`client/remote_client.py:1472-1495`).
- **`_join_task_deferring_cancellation`** (DC`_async.py:7-24`): waits for the task to settle, keeps the first `CancelledError`, and re-raises it afterwards. This is the pattern behind #7133 (cancel was acknowledged before subagent cleanup finished).
- **Cancellation bugs in LangGraph:**
  - #5682: cancelling `astream` does not stop a subgraph `ainvoke`d inside a node.
  - #8302: `abort()` does not stop subgraphs.
  - #6726: ToolNode's `except Exception` misses `CancelledError`, leaving tool calls without results (INVALID_CHAT_HISTORY).
- **Orphans:** I found no lease, heartbeat, or cancel-on-parent-exit code for async children (negative finding, UNVERIFIED).
- **Cost:** subagent token usage is dropped (#6556). The CLI rebuilds cost itself from callbacks.

### Q8 Supervision UI
- The deepagents CLI is a Textual client talking to a local agent server process (`libs/code/ARCHITECTURE.md:14-26`).
- Past threads are browsed and resumed with `/threads` / `/resume` and `-r/--resume`.
- It streams with `subgraphs=True` and keeps streamed text per namespace (DC`tui/textual_adapter.py:493-562`, `:2225`).
- A docked fan-out panel shows js_eval subagents as running/done/error/cancelled, with control characters stripped from LLM-authored strings (DC`tui/widgets/subagent_panel.py:1-46`).
- LangGraph Studio: not examined (UNVERIFIED).

### Q9 Host/protocol exposure
- **Agent Protocol REST** (`agent-protocol/openapi.json`):
  - threads: CRUD, search, history, copy.
  - runs: `POST /runs` (background), `/runs/stream`, `/runs/wait`, `GET /runs/{id}`, `/runs/{id}/wait`, `/runs/{id}/stream`, `POST /runs/{id}/cancel?wait&action=interrupt|rollback`.
  - `RunCreate` fields: `on_disconnect` (**default `cancel`**, or `continue`), `on_completion` (delete/keep), `if_not_exists`, `webhook`.
  - Thread status: idle / busy / interrupted / error.
  - Agent Protocol has no `multitask_strategy`; that is a LangGraph API extension.
- **Streaming protocol** (`streaming/protocol.cddl`):
  - `POST /threads/{id}/commands` with `run.start`, `input.respond`, `input.inject`, `state.get`, `state.listCheckpoints`, `state.fork`.
  - Subscriptions filter by namespace prefix and depth, and can reconnect.
  - `agent.getTree` gives a per-namespace status: started / running / completed / failed / interrupted (`:646-672`).
  - Lifecycle `started` events carry `cause: {type:"toolCall", toolCallId}`, which ties a subagent namespace to the parent's tool call (`:694-740`).
- **SDK:** `stream_resumable` and `Last-Event-ID` resume a dropped stream (SDK`_async/runs.py:427-470`, `:1107-1130`).

### Q10 Bugs worth learning from
1. **deepagents #5546** (closed 2026-08-17): synthetic results for dangling tool calls claimed the user cancelled them and were marked success. Say "no result recorded" and use error status.
2. **deepagents #6234** (open): cancel records `cancelled` for a run that had already finished, and the terminal-status short-circuit then pins the wrong status. Per #6234, the example server's cancelled task also later overwrote the thread with `success` (#5401). Only the child's exit path should write a terminal status.
3. **deepagents #7133** (closed 2026-10-21): cancel was acknowledged before subagent cleanup finished. Await teardown before reporting cancelled.
4. **LangGraph #9046** (open): steer modelled as cancel + restart loses the in-flight step and orphans tool calls. Steering needs its own boundary-insert primitive.
5. **LangGraph #5672 / #6726** (open): cancel loses streamed-but-unsaved state, and `CancelledError` skips the code that records results. Write a terminal record on `BaseException`, then re-raise.
6. **deepagents #4033** (open): a subagent's human-approval interrupts don't route to the parent tool call in ACP.

## Borrow (ranked)
1. **Phase 2 – notification delivery.** Wake an idle parent only, reject if it became busy. A busy parent pulls before each model call and re-loops after its final answer. Deduplicate by (task, run, status). Frame as "SYSTEM NOTIFICATION – NOT USER INPUT" with escaped child text, a per-status next-step hint, and a metadata marker. Never wake a parent waiting on approval.
   - `_notification_content`, `_notification_header`, `_escape`, `_clip`, `_apull` (gather + per-task `wait_for`) and `_record_notification` port almost directly to asyncio.
2. **Phase 0/1 – `PatchToolCallsMiddleware`.** About 35 lines; apply to a stopped child session before resuming it.
3. **Phase 2 – `AsyncTask` record kept in parent state** (survives compaction). Statuses pending / running / waiting / success / error / cancelled / timeout, a terminal set, and `superseded_run_ids`.
4. **Phase 0/2 – `_join_task_deferring_cancellation`.** Direct port for awaiting child process exit when cancelled.
5. **Phase 2/3 – drain flag** in the style of `RunControl`. Checked at the turn boundary, inherited by nested runs, ends in a saved and resumable "drained" state. Use it for quit/switch. For "wrap up now", inject a message first, then drain.
6. **Phase 3 – interrupt vs enqueue update strategies**, with rewriting of superseded notifications and the one-follow-up-per-turn guard.
7. **Phase 4 – Agent Protocol shapes:**
   - namespace tree plus `toolCallId` cause, mapping child session ↔ parent tool call;
   - `input.inject`;
   - the `run.start` start / resume / inject state machine;
   - `cancel?wait&action`;
   - `on_disconnect=continue`;
   - `Last-Event-ID` reconnect.
8. **Phase 0 – result extraction:** walk back past an empty final message. Describe errors by type only. Clip with a pointer to a status tool.

## Avoid
- Letting a child's exception propagate through the delegation tool. Under sync `task` it kills the parent and returns nothing, which is the #604 failure.
- A recursion or step limit that raises; use a graceful final turn instead.
- Cancel as a flag polled between stream events (deepagents' ACP server).
- The canceller writing the terminal status.
- Steer implemented as cancel + restart.
- Agent Protocol's `on_disconnect` default of `cancel` for background runs.
- Unframed notifications; models mistook them for user answers.
- Wake-ups that cancel a pending approval.
- Status features that silently work only on the async path.
- Losing subagent cost data (#6556).

## Per-phase value
- **Phase 0:** moderate de-risk. The dangling-call patch, result extraction and error sanitising port directly. deepagents itself returns no partial output, so the tool-trail design is aelix's own.
- **Phase 1:** moderate. "Resume = new run on the same thread with history; restrictions rebuilt from the spec" is validated. Exact-point resume is still open upstream (#3008).
- **Phase 2:** high. `AsyncSubAgentMiddleware` is close to a complete, battle-tested design; swap HTTP runs for local child processes.
- **Phase 3:** low-medium. The enqueue/interrupt strategies are reusable, but nobody upstream has a true steer (#9046). The drain primitive covers the "stop at boundary" half.
- **Phase 4:** medium. The Agent Protocol REST API and streaming spec give proven shapes. deepagents' own ACP adapter is weak (flag-polled cancel, subagent namespaces ignored, #4033).
