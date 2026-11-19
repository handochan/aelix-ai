# 0306. A running delegated child is steered through a command inbox on its stdin

Status: Accepted (2026-11-18, #123). Implemented on `a1606f58` (after #779 merged). What holds each
decision is listed in "How it is held" at the end: the tests, the mutants and the pty captures, each
with the condition it was measured under. Lane A of the two-lane contract; lane B (#612, the RPC
and ACP exposure) follows in ADR-0307 and changes nothing a host sees until it lands.
Amended 2026-11-19 (#822, #826, #828, #829): a refused wrap-up names no text and `/jobs wrap` on an
ended agent says what the model is told; plan mode's refusal in a child names its way out; what
aelix spawns with live stdio does not read the inbox; see the note of that date.
Date: 2026-11-18
Issue: #123 (intervening in a running delegated child; #604 stage 3).
Design: `.omc/specs/123-612-contract-r2-2026-11-18.md` (contract r2, Part A and §12's Lane A block),
revising r1b against the Codex review `.omc/specs/123-612-codex-review-r1.md` (H1–H9, M10–M19,
L20–L22, each disposed of in its Part D). Probes: `.omc/probes/123-612-design/` (the stdin-pipe and
in-process inbox prototypes) and `.omc/probes/123/impl/` (this implementation's).
Amends: ADR-0201 (`:292`, `:608`, `:1066-1068`: the rpc channel is not productised; `steer`/`follow_up`
reach a print child through the inbox), ADR-0276 (Limits: the rpc items are moot for the product; a
blocked stdin write is bounded here), ADR-0286 §6 (the cooperative abort is not sent), ADR-0291 (stop
is unchanged), ADR-0295 (`send` now exists, `:32`, `:391`), ADR-0199 §3.9 (`:931-933`: the tighten
window), ADR-0293 (a control adapter and `KindCapabilities.controls`), ADR-0278 D3
(`SubagentProgress.stuck` and `wrapping_up`). Each carries a dated note.
Relates: ADR-0235 (pi is the reference, not a parity mandate), ADR-0290 (a cancelled turn keeps what
ran), ADR-0296 (every door reaches `PrintChannel.run`), ADR-0298 (Ctrl+B), ADR-0299 (the `/jobs`
panel), ADR-0197 and ADR-0199 (the band rule and the posture clamp).

## Context

A delegated child was a `--mode json -p` process with its task on argv and `stdin=DEVNULL`
(`aelix_agents/print_channel.py`). Once it ran, nothing could reach it: `tool.py`'s description
said "you cannot steer it mid-run", a shift+tab in the parent changed the parent alone
(`runtime.py`: the posture is fixed at spawn), and the only intervention was `stop`, a kill. #123
asked for the 10-28 scope: steer a running child, ask it to wrap up ("정리해"), notice when it is
stuck, and honour a tightened parent posture (W5).

Two transports were weighed in contract r1b and r2. **(d)** every child `--mode rpc`: Codex's
review measured ten print-mode behaviours that mode would have to rebuild — the print drain of
the child's own jobs and notice turns, the versioned `aelix_print_done` line, the metadata
header, exit 1 on a failed answer, the 143/149 signal exits, completion by process exit (rpc's
needs a settle signal and a second detector for an oversized `agent_end`), a dispatch parked in an
input hook holding the server open after EOF, the child's own `run_in_background` offer, the
owner's "`-p`는 백그라운드 자식이 끝날 때까지 기다린다" inside the child, and the first-failure
latch (r2 §2.1, each measured by Codex or the design lane). **(e)** the print child gains a
private command inbox on its stdin behind a flag: measured in-process on the real
`run_print_mode` (r2 §14.2), a `steer` fed while the child's tool ran was acknowledged in the rpc
`response` shape and landed in the next model request after the tool result, the header first,
`aelix_print_done` last, exit 0 unchanged; EOF on the inbox aborted the turn and print mode
exited 1 with `aelix_print_done` still written. The one obstacle to (e) was one function:
`cli/entry.py`'s `_read_piped_stdin`, which read an inherited pipe as a prompt (measured on a real
child, r2 §14.1: a line written inside the 1 s window was prepended to the task).

The owner chose (e) on 2026-11-18: "(e) print 자식 + stdin 명령함 (권장)".

## Decision

**D1. The transport is the print child's stdin.** `build_child_argv(inbox=True)` appends
`--command-inbox=1` for every row, foreground and background alike — the lead's call
(`aelix_agents/print_channel.py`); the flag is hidden
(`cli/args.py`, lifted out of argv by `_take_command_inbox` before the parser's loop, so
`parse_args` gains no branch), a version this build does not serve is an error diagnostic and the
launch ends with it on stderr. A flag and not an env var, the lead's call: an env var is inherited
by the child's own `bash` and helpers and would have to be popped as `JOB_INTERNAL_ENV` is.
`PrintChannel.run` spawns with `stdin=PIPE`; the env still sets `AELIX_STDIN_TIMEOUT=1`, belt and
braces for a child launched without the flag. All of ADR-0296's doors reach `PrintChannel.run` —
a single `agent()` call, a resume, batch members, chain steps, `/agents run`, RPC `agents/run`, a
background spawn — so every child has the inbox; `RpcChannel` is untouched and still unused by
the product (the entry-point sweep is in `.omc/probes/123/impl/`). #225 (the rpc channel's
teardown residuals) and #357 (a first cancel swallowed in `RpcClient`'s and `rpc_mode`'s
teardown) are out of scope, the lead's call: both are `RpcChannel`/`RpcClient`/`run_rpc_mode`
hygiene, and between `a1606f58` and this tree those files change in comments only — a dated
note in `rpc_channel.py`, re-numbered citations in it and in `rpc/rpc_mode.py`, nothing in
`rpc/rpc_client.py` (`git diff a1606f58` on the three).

**D2. The child's side** (`modes/print_inbox.py`, new; `cli/entry.py`; `modes/print_mode.py`).
With the flag, `_read_piped_stdin(inbox=True)` answers `None` before `isatty`, on every platform,
with no readiness wait and no read. `_command_inbox` opens stdin the way `--mode rpc` does
(`rpc_mode._open_stdin_reader`: asyncio's read pipe on POSIX, the `os.read` thread pump on win32)
and `run_print_mode(inbox=)` starts one pump beside its prompt and ends it in its `finally`
before the dispose — two unconditional calls, since that function is baseline-listed at C901 58.
The pump reads JSONL lines with `JsonlLineReader`, starts one task per line in arrival order and
dispatches three commands to `runtime_host.harness`: `steer {message, final?}`, `follow_up
{message}`, `set_permission_mode {mode}`. Nothing else: no `prompt` (one process = one turn +
steer; continuing a finished child is `resume`, #610), no `abort` (D5), no `get_state`. Replies
are `{"type": "response", "command", "id", "success", "data" | "error"}`, the rpc `response`
shape, written through print mode's own stdout writer. A line that is not JSON, not an object or
without a string `id` is dropped and nothing is run; a line with a string `id` that names none of
the three, or carries a bad field, is answered `success: false` by that id; none is fatal. `final`
must be JSON `true` or `false`: any other value is such a bad field and nothing is queued (fix
round 1, F8 — at `8d7ee999` `"final": "false"` ended the run, `bool("false")`, and a command with
no id or a numeric one was run and answered `"id": null`, which the parent's wire routes to
nobody). A line that is not UTF-8 is dropped whole and the lines after it are served (fix
round 1, F7): the pump splits the bytes on LF and decodes each line by itself (`_pump_lines`),
keeping `JsonlLineReader` for the framing only. At `8d7ee999` that reader's strict decode of the
whole stream raised on such a byte and the pump took it for an EOF: the turn was aborted and the
child exited 1 (the verifier's VF4b, a real child). The product's own parent cannot write such a
line (`json.dumps` escapes non-ASCII). EOF is the death detector: the pump records it (`CommandInbox.ended`) and calls
`harness.abort()` once, and print mode reads the record before each prompt, in its drain and with
its verdict, so the run exits 1 when the EOF came before its verdict (fix round 1, F2; the moments
measured are under Consequences; an EOF written as the prompt returned exited 0, verify round 2,
`child-eof_after_prompt-head.json`). The protocol is private and versioned, and aelix-original: pi has no inbox on a print
child (its background subagents are conversations of one runtime, `packages/durable` example 22),
a knowing divergence under ADR-0235.

**D3. The parent's side** (`aelix_agents/command_wire.py`, new; `print_channel.py`; `stream.py`).
`RunningChild.wire` is a `CommandWire`, built before the spawn is awaited and attached to the
process's stdin after it. It numbers requests per row, writes one line under a **5 s** write-and-
drain bound and waits for the answer under a **30 s** acknowledgement bound
(`DEFAULT_SEND_TIMEOUT_MS`'s value). `_pump_stdout` hands lines that begin as a `response` to
the wire's correlator and everything else to the reducer as before. Outcomes are four words and
never "not delivered" (Codex M10; "three" until fix round 2, V-N2, though `not_sent` was listed): `queued` (`success: true`, with the child's `phase`),
`refused` (`success: false`), `unknown` (no answer within the bound, a write that failed after
bytes may have left, or the child ended with the request open — `_end_of_run` fails every open
request and closes the write end, after `_stream_and_reap`; a reply the child's end gave is marked
`Reply.ended` and its door names `action="resume"`, D4); `not_sent` only when the write could
not begin. A write that cannot drain marks the wire broken and later requests are refused "stop or
resume it", which closes ADR-0276 Limits item 3 on this path. Whether a queued line was CONSUMED
is read off the child's stream: the loop emits a `user` message for each drained steer and the
reducer keeps the heads of the latest eight (`_StreamState.user_texts`). The wrap-up's reading is
not a search of that window (fix round 1, F4). `runtime.wrap_up` records the request's head on
the row's stream state before it writes the line, and the first `user` message carrying that head
that is reduced after it sets a latch nothing lowers (`_StreamState.wrap_up_read`); the envelope
reads the latch. The window answered two cases wrongly, both now pinned through the reducer and
through the door: a task or a steer in the wrap-up's words, read before the request, counted as
its reading (`aborted` on a run that ended on its own answer); and with `steeringMode="all"`
eight notes drained together with the final pushed its head out (`ok` with "never reached the
model", though the kernel had read the final and its one model call had no tools). One case is
left open, by reading only: a plain steer whose first 200 characters equal the wrap-up's, queued
before the request and drained after it, sets the latch.

**D4. The doors and the verbs.** `_SubagentRuntimeImpl.steer(id, text, mode)`, `wrap_up(id,
origin)` and `tighten_all(mode)` (`runtime.py`; `_run` stays at 21). Per state (r2 §3.2): a row no
longer in the registry is refused with the resume hint ("finished or was stopped; use
action=\"resume\""), one with no process "not started yet; retry", a stopping one "stopping", a
broken inbox "stop or resume it". *The "finished in process" row, as built (fix round 1, F3; the
lead's call):* r2 §3.2 said such a line is refused by the child. It is not: print mode ends its
inbox pump before its dispose, so a line written after the child's prompt returned is not read and
not answered (`tests/cli/test_command_inbox_123.py`,
`test_a_line_written_after_the_prompt_returned_is_never_read_and_gets_no_answer`). The parent's
request stays open until the process exits — 0.345 s later in the verifier's run (VF3) and 0.275 s
in this round's (`runtime_probe.py late`, a real child against a scripted local server, macOS) —
and is then answered `unknown`, reason "the agent ended before answering; to go on, use
action=\"resume\" with this text as its next instruction" (a wrap-up's reason stops at
`action=\"resume\"`). It is `unknown` and not `refused` because a line read and not acknowledged
before the exit is possible; a send after the exit is refused with the resume hint. The model's
tool result for that `unknown` says "the agent ended before it answered, so it most likely never
read the note. To continue the agent, use action=\"resume\" and put the note in its task."; the
`unknown` of a child that is alive and silent keeps "may or may not have reached the agent … Do
not resend it" (`extension._send_text`). The model gets `agent(action="send", id, task, mode="steer" |
"follow_up")` and `agent(action="wrap_up", id)`, parsed by `_parse_send` beside `_parse_resume`
(`parse_agent_call` gains no branch), offered where the background actions are (`send` is one
verb with `mode`, the lead's call; pi's `subagent` tool has `send` with `followUp`). The human gets
`/jobs steer [--after] <id> <text>`, `/jobs wrap <id>` and the panel's `m` and `w` `w` keys; a
foreground run is steered after Ctrl+B (owner Q2: "Ctrl+B 후 /jobs steer·wrap (권장)"). They
reach a row through a **control adapter** (Codex H9): `JobService.control(job_id, verb, payload)`
checks `KindCapabilities.controls` and calls the kind's `control`; `DelegationKindAdapter.control`
resolves a spawned job's row (`DelegationRunner`) and a Ctrl+B'd one's (`AdoptedRunner`), inside
ADR-0293's "control adapters" clause — generic `/jobs` code names no delegation object, and a
bash job answers with its `control_hint`. *What that answer is (fix round 1, V7; the lead's
call):* a kind that does not declare the verb is refused in a human's words — `steer` is "a
message", `wrap_up` "a wrap-up", any other verb as the kind wrote it — then its hint
(`service.control_refusal`). For a background command `/jobs steer`, `/jobs wrap` and the
panel's `w` `w` answer "this kind of job does not take a message; /jobs stop <id> stops it
(panel: s s)" ("… a wrap-up; …" for the wrap), and the panel's `m` shows the same sentence on
its last row and stays open. At `8d7ee999` the bash kind had no hint, `w` `w` read "this kind
of job takes no wrap_up" and `m` closed the panel with `/jobs steer <id> ` in the input
(each measured in a pty on a real `sleep 30` job: this tree at 80x24 and 120x30, `8d7ee999` at
120x40 in the verifier's run, VF12). A kind whose registration is gone keeps the prefill, by
reading only, and `control` answers it.

**D5. `stop` is unchanged**, and the inbox has no `abort` (Codex H4): an rpc `abort` passes
`withdraw_unanswered=True`, which removes the original task from the reopened context — the
transcript change ADR-0291 §2 rejected and ADR-0286 §6 said #123 must choose explicitly. Chosen,
the lead's call: not sent. `RESUME_PREAMBLE` and the `No result provided` guard on resume after a stop stand.

**D6. The wrap-up is a kernel latch** (`harness/core.py`; Codex M11). `harness.steer(text,
final=True)` remembers its message; `_drain_steering` marks it consumed when a drain returns it —
at EVERY drain the loop performs, so a final queued behind an error, abort or stop-after-turn
ending stays unconsumed and the run ends as today; the next model call runs with no tools
(`_without_tools_after_final` wraps whichever stream function `_run` chose, unconditionally)
and `_request_response_is_terminal` ends the run after it. That is not always one call (fix
round 1, F6): an automatic retry of that call, and its re-run after an overflow recovery,
re-enter `_run` inside the same prompt with the flag up — they have no tools either and the run
ends after them (tool counts `[1, 0, 0]` in each, the final's call answering 503 and "prompt is
too long"; `tests/harness/test_final_steer_123.py`, guards that pass on `8d7ee999`). A final consumed cannot retract
a model call already streaming or a batch already requested. The consumed flag belongs to the run
that read the final: every run start lowers it — `prompt`, and since fix round 1 (F5)
`prompt_notices` — and the read-only `harness.final_consumed` says, between two runs, whether the
earlier one read a final. At `8d7ee999` a notice turn after a consumed final kept the flag: its
call had no tools and ended the run whatever it asked for (tool counts `[0, 0]` where `[0, 1, 1]`
is due, `tests/harness/test_final_steer_123.py`). Empty tools is the contract;
`tool_choice: none` would be a provider-dependent optimisation. What that call carries on the
wire is each adapter's business (fix round 1, V9; read from the adapters, one row each): the
Responses, Codex, Google and Anthropic adapters set `tools` only under `if tools:`
(`openai_responses.py`, `openai_codex_responses.py`, `_google_shared.py`,
`_anthropic_transforms.py`), so their request has no `tools` key; the chat-completions adapter
(`openai_completions.build_params`) leaves it out too since #815 (the port of pi PR 11891), and
sends `"tools": []` only for a model whose `compat` sets `requires_tools_with_tool_history`
(pinned from the kernel down by `tests/providers/test_openai_completions_unit.py`). Measured
through OpenRouter only (`.omc/probes/123/lead-live/provider-tools-empty/README.md`): 39
non-streaming requests on 13 (model, upstream) pairs of 6 models answered HTTP 200 with the key
filled, empty or left out. No direct upstream was run: pi issue 11843 reports a vLLM behind
LiteLLM answering `"tools": []` with HTTP 400. The kernel knows the word "final"
and nothing of who asked: the text (`command_wire.WRAP_UP_TEXT`, Gemini CLI's final warning as the
reference; since fix round 1 it names nobody — "You have been asked to stop here" — where the
parent model's wrap-up used to tell the child "The user has asked you to stop"), the origin and
the result policy stay in `aelix_agents` — the band rule, and the
kernel allowlist entry in `tests/agents/test_p2_band_boundaries.py`. pi's `steer` has no final
flag: a knowing divergence.

**D7. The wrap-up's result** (owner Q3: "aborted + 이유 + ok=False (권장)"). `build_result` gets
the row's `WrapUp` and whether the child consumed it: consumed, on a run that ended on its own,
the envelope is `status="aborted"`, `reason="wrapped up at the user's request"` for `/jobs wrap`
and the panel, `"wrapped up at the model's request"` for `agent(action="wrap_up")` (fix round 2,
V-F1), `ok=False`, the child's own text as the summary — a child saying "all done"
is still `aborted` (gemini-cli#22323 reports a wrapped-up child as GOAL); never consumed, the
envelope is the run's own with the note "a wrap-up was requested by the user but never reached
the model" ("… by the model …" for the model's); consumed on a killed run, the kill's reason wins. A request whose line was `not_sent`
is taken back from the row (fix round 1, N1; `runtime._record_wrap_up`): the row takes another
request, the panel's "wrapping up" goes and the envelope carries no note for it. Pinned on a row
whose stdin the parent had closed; a request answered `unknown` stays recorded.

**D8. W5, the permission floor.** `PermissionPosture.listeners`; `set()` notifies and `cycle()`
goes through `set()` (Codex H6: it assigned `mode` directly and a shift+tab reached nobody), so
every posture write reaches the agents extension's one listener, which calls `tighten_all` on
every runtime it holds — this build's and each background job's own. *Which writer finds a
running child (fix round 1, V1; real processes against a scripted server):* shift+tab does. RPC
`permissions/set` found none in the three states an RPC parent was measured in. During a turn,
which is when a foreground child runs, it is refused — "Runtime mutations require an idle
session", `permissions/get` still `yolo`, the child wrote its file (the verifier's VF4a,
`rpc_tighten_probe.py`). During an `agents/run` control call it is answered only when that call
returns — sent at 2.58 s, answered `plan` at 8.04 s, the same hundredth of a second as the run's
own answer, after the child had written its file under `yolo` — since both are mutating methods
behind one lock. And an idle RPC parent has no background agent job to reach: `action="spawn"`
is not in the tool's schema there ("'spawn' is not one of ['run', 'resume']"), so the write it
accepts notifies the listener and finds no row (`.omc/probes/123/fix-r1/V1-rpc-*.json`). No ACP
`session/set_mode` handler exists in this repository and ACP was not run; an adapter that drives
aelix over RPC meets these three rows. A tighten a host can send mid-run is #612's. Rank-MIN by
the authority ladder, now declared beside the enum
(`builtin/permission_mode.AUTHORITY_RANK`, read by `aelix_agents.posture` and by the child's
inbox, which refuses a widen itself: "a delegated child's posture only tightens"). The mode a row
is compared against and sent is the one a child spawned under the new posture would start at
(`child_permission_mode`'s `inherit` baseline: `default` is `plan` for a child, `auto` is
`auto-accept-edits`), so a tightened child never holds a mode a fresh one could not. By the state
of the row `tighten_all` finds: one with a process is sent `set_permission_mode`; one registered
with no process yet is sent nothing — its posture is lowered, the argv carries the tighter of the
plan's mode and the row's, and a tighten that lands while the spawn is awaited is sent once the
inbox is attached (`print_channel.tighten_late`); one that is stopping is skipped, it is being
killed. Fail closed: no reply within 30 s, `success: false`, an echo that is not the mode asked
for, or an inbox no line can be written to (broken by a write that timed out or failed) aborts the
child with the reason "permission floor could not be applied". The result reports the posture the
child ended at (fix round 1, F10; the lead's call): `SubagentResult.permission_mode`, which the
`[agent … ]` footer shows the model, is the tighter mode once the child echoed it — a stub child
launched `yolo` and tightened to `plan` ends `ok · plan` — and the launch mode for a child that
echoed none, one killed for refusing the tighten included (`yolo`). The spawn receipt, written
before the launch, keeps the launch mode. At `8d7ee999` the first case read `yolo`. A call that has its grant and no
row yet is not reached (ADR-0199's amendment of this date lists what stays open). The promise is narrowed to what the gate
can keep (Codex H5): **the tighter posture governs every approval the child makes after it
applied**; a call already approved in the same batch's preparation may still start under the old
mode, a window of one batch with no await between the last approval and the starts. No
re-validation before execution — a kernel hook that does not exist — the lead's call.

**D9. Stuck detection** (Codex M12). `_StreamState.fingerprints` (maxlen 8): one per completed
call, `(name, sha256 of the arguments, sha256 of the result's first 1 KiB, is_error)` from the raw
events, kept apart from the display window `activity`, which clips arguments and never sees a
result. `stuck_of` is pure: `repeat`, four consecutive equal entries (OpenHands' threshold); `error
loop`, three consecutive equal errors. `SubagentProgress.stuck` and `wrapping_up` are additive
and defaulted (no contract bump). One nudge through the inbox (`NUDGE_TEXT`), once per row; a
second trigger keeps the mark; never a kill; `quiet` is unchanged. Two patterns only, the lead's
call: monologue and alternation are deferred, since they need message-level state the reducer
ignores on purpose. A background agent marked stuck tells the parent model once, while it still
runs (finding D1; the owner, 2026-11-18, on the round-1 report: "2. 이번에 넣기" — built in this
change; its shape is the lead's call, `.omc/specs/123-d1-stuck-notice-design-2026-11-18.md`).
`jobs_client._job_progress`, where a spawned run and a Ctrl+B-moved run publish to their job, asks
the job's door `JobContext.attention` (ADR-0293's note of 2026-11-18) once per agent, on the first
snapshot that carries `repeat` or `error loop`, no wrap-up and the state `running` (a terminal
snapshot still carries the mark its last calls left: fix round 2, C-R2); a later trigger keeps the
mark only. When Ctrl+B's move commits, the snapshot the panel shows for the row
(`SubagentProgressBridge.latest`) goes through the job's route once, after `announce_adopted`: a
row marked at the move asks at the move, while the child runs; a mark that cleared before the move
asks nothing; a row marked after it asks at that snapshot (fix round 2, C-R2 and V-F3; at
`1991e445` a child marked in the foreground and then silent asked only after it ended, and one
whose next call returned a different result never asked). The service frames one notice per job,
id `<job>:attention`, `details.status == "running"`, its head "… is still running after …":
inside the fence the agent's latest calls — all of `SubagentProgress.activity`, at most four,
oldest first, each with its status, under "its latest calls, oldest first:" — and its last text
(fix round 2, C-R4: `activity` is ordered by start and keeps open calls, the fingerprints by
completion, so the notice names none of them as the repeated one; it quoted `activity[-1]` as
"last call"); outside it what the pattern means (`stream.STUCK_MEANING`, held against
`stuck_of`), "That can be intended when its task asks for the same call again; if so, leave it
running." (fix round 2, L1: in the lead's three real-model runs the parent wrapped up a child
whose task asked for that repetition), that it was not stopped, and
`agent(action="send"|"wrap_up"|"stop", id=…)` with the agent's id (`tests/agents_ext/test_stuck_notice_123.py`, the Ctrl+B case and the end-to-end spawn
POSIX only). It does not mention the nudge, which `_nudge_once` skips when the inbox refuses. A
foreground run raises none: its parent is inside its own `agent(action="run")` call and the human
has the panel row. Over RPC and ACP the agent kind is not registered (#612), so no delegated agent
posts one there.

**D10. The surfaces.** The `/jobs` panel's key row names `m note` and `w w wrap`, the arm hint
reads `press w again to wrap up <id>`, and a job's rows and the delegation panel show `wrapping
up` and `stuck: <pattern>`. The key row is the fullest of three that fits the panel's width, the
waiting text counted in, measured in cells (fix round 1, F11): `↑↓ select · enter detail · r
result · m note · s s stop · w w wrap · esc close` from 78 columns (118 while a result waits),
the same row without `detail`, `result` and `close` from 58 (98), and `↑↓ · enter · r · m · s s ·
w w · esc` below that. At `8d7ee999` the three labels were gone at every width and the bare row
was shown below 100 columns. A note on that row — a wrap-up's answer, `stopping <id>` — is fitted
where the row is drawn, to the cells the panel has less the waiting text's, and ends in `…` when
it is cut (fix round 1, V5). At `8d7ee999` the wrap-up's answer was sliced at 80 characters: the
82-cell "Queued the wrap-up: … it is already running." read "…already runnin" at 80 and at 120
columns. The `agent` description no longer says "you cannot steer it mid-run";
the background paragraph names `send` and `wrap_up` and, since #123 D1, says "If it looks stuck,
a notice says so while it runs." (measured: 1566 B of the 2048 B head budget, 1017 B of the 1024 B
background budget; 966 B before that sentence). For a background agent marked stuck (#123 D1,
`tests/agents_ext/test_stuck_notice_123.py`): `agent(action="status")` and `wait`'s status lines
say `stuck: <pattern>`; a `wait` ends as soon as a waited agent's notice is posted, once per notice
(a second `wait` while it is still unplaced waits), and adds "An agent marked stuck is still
running; the notice right after this call says what you can do about it."; the TUI's transcript
line for the notice is `◆ background notice: <brief>`, a result's stays `◆ background result: …`
(`tests/jobs/test_attention_123.py`; a pty at 120x40 with an extension's job and a scripted model,
`.omc/probes/123/d1/tui-screen.txt`).

## Consequences

- One behaviour for foreground, background and Ctrl+B'd children; every containment, ownership,
  cleanup and drain line stays where it was. Lane A adds no RPC method, no ACP method and no
  event shape: the inbox is a private parent-to-child protocol, `final` a kernel keyword and an
  inbox field, and `rpc/` differs from `a1606f58` in two citation line numbers of one comment
  (`rpc/rpc_mode.py`). *What an RPC host can see change (fix round 1, V3; a real `--mode rpc`
  process against a scripted server, `.omc/probes/123/fix-r1/V3-rpc-jobs-*.json`):* text only.
  `get_commands` lists `/jobs` with `steer <id> <text>, wrap <id>` added to its description.
  `/jobs steer <id> <text>` and `/jobs wrap <id>` sent as a `prompt` reach the model as text, on
  this tree and on `a1606f58`. Run through `commands/run` the command runs and answers "no job
  <id> in this session" (`a1606f58`: "no job steer in this session"), because the session held no
  agent job in each of the three states measured: idle, where the `agent` tool offers `run` and
  `resume` and no `spawn`; during an `agents/run`, where a `commands/run` is answered only after
  it returns, with "No background jobs in this session."; and during a turn, where `commands/run`
  is refused ("Runtime mutations require an idle session"). So no host steered or wrapped a child
  that way; the door it would use, `JobService.control`, is reachable through `commands/run`, and
  only the missing job keeps it shut. One `agents/run` with one tool call gave the same key sets
  on both trees for its result and its three `aelix_subagent` events. ACP was not run.
- A parent that dies closes the pipe and the child ends on EOF — the macOS death detector r1b
  wanted, without `pdeathsig`. At `8d7ee999` only the two in-turn moments held: a child in its
  drain went on waiting and answered its job's notice with a model call, and a prompt that began
  after the EOF erased the abort. Fix round 1 (F2), per moment:
  - *before the prompt* (the inbox closed first): no model request, exit 1 (in process and a real
    child, `tests/cli/test_inbox_eof_123.py`);
  - *in a tool* and *in a stream*: the abort ends the turn. A real child whose pipe-holding parent
    was SIGKILLed was gone 0.27 s (tool) and 0.29 s (stream) after the kill — macOS, a scripted
    server, one run each;
  - *in the background drain*: the drain stops on its next pass (it waits in 1 s ticks) and starts
    no notice turn, and the dispose then stops the child's own jobs. Same probe, one run with the
    child's stdout a file and one with it a pipe: gone 0.26 s and 1.28 s after the kill, no model
    request after it; the real-child tests read the child's own `bash` job gone.

  In each the run exits 1; `aelix_print_done` is still the last line in the in-process cases that
  ask for it (before the prompt, in a tool, in the drain). The #777 macOS recordings
  (`tests/cli/test_parent_death_611.py -k l3`, 10 of 10 runs each): the delegated child, its shell
  and its sleeper gone within 0.4 s of the kill; a background `bash` shell of the killed aelix
  and its sleeper still alive at 5 s (no inbox reaches them). Not measured: an EOF during a retry
  backoff or an auto-compaction inside a turn. Native Windows was not run in fix round 1: two
  guards are written with no platform skip (fix round 1, V4: the inbox over the win32 `os.read`
  pump on an `os.pipe()`, and the spawn's `stdin=PIPE` kwargs; both under "How it is held"), run
  here on macOS and in the Linux gate only, and every real-child inbox test is POSIX-only.
- A run that read a wrap-up is over (fix round 1, F5; the lead's call). Print mode reads the
  kernel's `final_consumed` and then prompts no residual message, does not wait in its drain for
  the session's own jobs and starts no notice turn; the dispose stops those jobs, as after an
  inbox EOF or a signal, and the exit code is the last answer's. Measured
  (`tests/cli/test_wrap_up_ends_the_run_123.py`), per condition:
  - *in process, a 30 s job running, one residual message*: the run returned within 8 s of the
    final's call, model calls `[1, 0]` by tool count, exit 0, the job still running when it
    returned. At `8d7ee999`: `[1, 0, 1]` and still going at 8 s — the residual message was
    prompted with its tools, then the drain waited;
  - *a real child (POSIX, a scripted server) with a background `bash` job of its own*: exit 0
    after three requests, the third with no tools, `agent_end` the last line, the job's process
    gone. At `8d7ee999`: still running 20 s after the wrap-up's call;
  - *the final arrives while the child already waits in its drain* (in process, a 1.5 s and a 30 s
    job): it is only queued — a steer is read at a turn boundary and a waiting drain has none.
    The short job's notice turn reads it (one call, no tools) and the run ends there, the long
    job still running. At `8d7ee999` the drain went on waiting for the long job.

  A limit, by reading only: with no job of the child's ending, no turn starts and nothing reads
  a final that arrived in the drain; `stop` is the door then. Starting a turn for a queued steer
  would take a kernel door this change does not add.
- A note is delivered at a turn boundary, never mid-tool; `send` cannot cut a batch already
  requested, and `stop` stays the way to.
- `/reload` stops jobs (owner 2026-11-07), so a queued line dies with the child; the session file
  holds what was consumed. A limit, stated.
- The bundled `agent-profiles.md` is 51,136 B against the 51,200 B read cap on `main` after #623/#624 (51,196 B before that rebase, after fix round 2; 51,189 B after #123 D1; 51,151 B after fix round 1). `tests/cli/test_docs_signpost.py` holds every guide under the cap (`size < 51200`), so 63 bytes of room; this section was shortened to fit when the rebase brought #623/#624's 91 bytes.
- *What an RPC host sees of an attention notice (#123 D1; one real `--mode rpc` process, a
  scripted server, an extension's job kind whose run calls `JobContext.attention`,
  `.omc/probes/123/d1/rpc-attention-head.json`):* no event when it is posted; the host's next
  `prompt` places it first, and the model's request and `get_messages` carry it as a `user`
  message before the prompt's text, with no `message_start` or `message_end` of its own; the
  session file holds it as an `aelix.job_notice` `custom_message` with `details.status ==
  "running"` and `notice_id` `<job>:attention`. The job's final notice came the same way with
  `"done"`. Nothing under `rpc/` changed. Delegation posts none there: its kind is not registered
  over RPC (#612).

## How it is held

- The child side: `tests/cli/test_command_inbox_123.py` — the flag parses and stays out of
  `--help`, an unknown version is an error diagnostic, `_read_piped_stdin(inbox=True)` reads
  nothing on darwin, linux and win32 (a stdin that raises on any read), the reader is opened only
  with the flag, and on the real `run_print_mode`: a steer fed mid-tool is in the next request
  after the tool result in the rpc `response` shape, a follow-up lands after the answer, malformed
  lines are answered by id or dropped, a line that is not UTF-8 (`b"\xff\n"`, and a whole steer
  with one bad byte in its message) is dropped while the tool is held and the steer after it is
  answered and read (F7; a character cut by a read and a CRLF line still frame, as a guard), a
  `final` of `"false"`, `"true"`, `1`, `0`, `null` or `[true]` is answered `success: false` with
  nothing queued, and five commands with no string `id` (absent, numeric, `null`, a list) are
  dropped with nothing run and no answer (F8), EOF
  aborts once and exits 1 with `aelix_print_done` last,
  `set_permission_mode` tightens, echoes and refuses a widen, a failed last answer still exits 1;
  a REAL child (`python -m aelix_coding_agent --mode json -p --command-inbox=1`, a scripted
  chat-completions server, `AELIX_STDIN_TIMEOUT=1`) makes its first request with no stdin wait
  and no stderr note and reads a steer written while its `bash` sleeps (POSIX). Fail on
  `a1606f58`: `TypeError` on `inbox=`, `AttributeError` on `command_inbox`, and the real child
  prints the "no data on piped stdin" note and never reads the line. Two guards (fix round 1,
  V4; both pass on `8d7ee999`): the inbox opened by `_command_inbox`'s win32 arm (`sys.platform`
  patched for that one call, fd 0 the read end of an `os.pipe()`, so
  `rpc_mode._thread_pumped_stdin_reader` feeds it from its thread) answers a steer written while
  the pipe is open and hands it to the harness, and the write end's close aborts the turn once
  and ends the thread, with no platform skip; and a real child whose first request is answered
  400 "Overloaded, please retry" writes two runs, exits 0 with its pipe open and silent, and the
  parent's reducer folds them to the second answer (POSIX; contract r2 §12's retry row).
- EOF (fix round 1, F2): `tests/cli/test_inbox_eof_123.py` — in process, an EOF in the drain
  ends the run within 5 s with no notice turn, exit 1 and `aelix_print_done` last, the tracked job
  left to the dispose; `_drain_background` starts no notice turn when its stop came true while it
  read the leaf; a run whose inbox has ended starts neither its prompt nor a residual message;
  and real children (POSIX): one whose inbox closes in its drain exits 1 within 20 s with no
  further model request and its own `bash` job gone, one whose pipe-holding parent is SIGKILLed
  in the drain the same, counted from the kill. Five of the seven fail on `8d7ee999` (still
  draining or still running at the bound; one model call where none is due); the two
  closed-before-the-prompt cases are guards.
- The parent side: `tests/agents_ext/test_command_wire_123.py` — the argv's last token, the
  channel's `inbox=True` and wire, the wire's ids, bounds, `not_sent`/`unknown` cases and
  close-out, a stub child whose `response` line reaches the wire and not the reducer, a request
  open at the child's end failing `unknown`, `user_texts` and `fingerprints` in the reducer,
  `stuck_of`, the envelope's three wrap-up verdicts, and the wrap-up's latch (F4: its words read
  before the request are not its reading; a real harness draining the final with eight notes). `tests/agents_ext/test_print_channel_spawn.py::test_stdin_is_the_parents_pipe_with_nothing_on_it`
  replaces the DEVNULL guard (POSIX: it probes fd 0), and
  `::test_the_spawn_hands_the_child_a_stdin_pipe_on_every_platform` reads `stdin=PIPE` in the
  spawn's kwargs on the per-platform case's three legs (win32 suspended, win32 with no resume,
  linux) with no platform skip (V4, a guard).
- The doors: `tests/agents_ext/test_intervention_doors_123.py` — a steer queued and consumed, a
  follow-up, the per-state refusals, the wrap-up's `aborted` with the child's text, the latch's
  two cases with a stub child (F4: a steer in the wrap-up's words consumed, then a final left
  unread, is `ok` with the note; a final followed by eight notes is `aborted`), a final answered
  `queued`, left unread and followed by the child's `error` end is `error` with the note "a
  wrap-up was requested by the user but never reached the model" (F9: contract r2 §4.2's
  precedence through the door, on the envelope function alone at `8d7ee999`), `tighten_all`
  on a looser and a tighter row read from what the stub child applied, the mode sent under a
  `default`, `auto` and `auto-accept-edits` parent, a tighten before the argv (in the argv), one
  inside the spawn's await (sent after the attach), one on an inbox a real write timeout broke
  (killed with the floor reason), a stopping row untouched,
  fail-closed on `refuse`/`wrong`/`silent`, `JobService.control`
  refusing a kind with no controls and another session's running job (F9: a real job, refused by
  the owner check; the unknown id beside it is refused by the lookup), the adapter reaching a spawned row
  and a Ctrl+B'd row (the detach rig).
- The actions: `tests/agents_ext/test_agent_send_123.py` — the parse and its refusals, the schema
  and budgets, and on the gated rig a note queued, a follow-up, a wrap-up ending the job `stopped`
  with an `aborted` envelope, a note to a finished agent refused with the resume hint.
- The child's end as an answer (fix round 1, F3): `tests/agents_ext/test_intervention_doors_123.py`
  — a stub child that takes one line off its stdin and exits without answering: `steer` and
  `wrap_up` return `unknown`, `ended`, with `action="resume"` in the reason, and the next send is
  refused with the resume hint; the model's words for that `Delivery` and, as a guard, for the
  alive-and-silent one; the human's sentence (`describe_delivery`) for a request open at the
  wire's close and for one made after it.
- The kernel: `tests/harness/test_final_steer_123.py` — the call after a consumed final has no tools and is the last (so its automatic retry and its overflow re-run, F6),
  a final read by a call that then errors is tool-less and the run ends on the error, a final
  queued while the failing response streams is not consumed and stays queued (F9: the leg the
  first of the two was named for), a final sent idle read at the next prompt's
  first drain, an abort withdrawing a queued final and a new prompt lowering the flag; F5: a
  notice turn after a consumed final has its tools, and `final_consumed` is up from the drain
  that read the final to the next run start. Print mode after a consumed final:
  `tests/cli/test_wrap_up_ends_the_run_123.py` (the three conditions above).
- W5: `tests/agents_ext/test_permission_floor_123.py` — `set` and `cycle` reach a listener, a
  raising listener drops nothing, a shift+tab tightens a spawned child (the stub child's own
  record: `auto-accept-edits` after yolo → auto, then `plan`) and a loosening sends nothing. Stuck: `tests/agents_ext/test_stuck_123.py` — four identical calls mark `repeat`, one
  nudge, no kill; the `quiet` guard `tests/jobs/test_monitoring_611.py` stays green. The parent's
  notice (#123 D1): `tests/jobs/test_attention_123.py` — the door's notice and its once-only
  answer, a cut body with no result hint, the accounting window (an attention notice placed while
  the job runs leaves `notice_state` `none` and `unsettled()` true until the final notice is
  posted), each gate (`notify` off on the job or the kind, a latched stop that still reads
  `running`, `stopping`, `starting`, a run in its barrier, ended), the wake and the
  `◆ background notice:` line, a rebind while running and after the end, an ended job that owes
  nothing for a waiting one, the `-p` drain's notice turn, the SIGINT flush in text and JSON;
  `tests/agents_ext/test_stuck_notice_123.py` — the kind asks once, not for a wrap-up or an
  unknown pattern, the pattern words against `stuck_of`, the frame with a 512-character last text
  and a 5,000-character call, `wait` and `status`, the description's sentence, and on POSIX a
  Ctrl+B-moved run and a spawned stub child that repeats a call. 35 mutants on the changed lines,
  35 killed (`.omc/probes/123/d1/`). Fix round 2 adds, each failing on `1991e445`: the drain held
  while `_complete` writes (with and without a notice) and while `_become_unresolved` writes; the
  `wait` lines (the stuck line after the result sentence, none for an agent that became
  `unresolved` or ended during the wait); a snapshot that is not `running` asking nothing, and a
  row marked before Ctrl+B asking at the move (POSIX); the latest calls quoted, oldest first, and
  each status in words; the L1 sentence; an id the inbox already knows; `/jobs send` counting a
  notice apart. Guards that pass there and kill a mutant: a rebind after placement, a rebind of a
  sealed or stop-latched job, the pattern words, a late tighten on a row being killed, a tighten
  on a row with a process and no inbox, an unresolved job waking the service's waiters, no
  harness or a disposed one keeping the attention record, and a held notice left out of
  `/jobs send` (`.omc/probes/123/fix-r2/`: 34 mutants on the changed lines, 31 killed; the three
  alive are equivalent on every path a test reaches — `_complete`'s `finally` notification after
  `_after_outcome`'s, an index `adopt` already bounds, and a `min` over a count no turn claims).
- The surfaces: `tests/tui/test_jobs_steer_123.py` — `/jobs steer`, `--after` and `wrap` through
  the control adapter, a kind with no controls refused in a human's words (whole), a job of the
  real bash kind answering `/jobs steer`, `/jobs wrap`, `m` and `w` `w` with the refusal and its
  hint (whole, on the painted row at 80 and 120; V7), `m` closing with the prefill, `w` `w`
  wrapping and `s` after `w` arming afresh, the key row at each width where it changes (57, 58, 77,
  78, and 97, 98, 117, 118 with the waiting text; F11), a note fitted to its row on a StringIO
  console at 80 and 120, alone and beside the waiting text, for the wrap-up's sentence and a
  wide-cell one (V5), the prefill reaching the
  input box, the rows and the painted panel (a StringIO console).
- Live, in a pty at 120x40 and 80x24 against a scripted model (`.omc/probes/123/impl/pty/`):
  delegate, Ctrl+B, `/jobs` + `m` prefilling `/jobs steer <id> `, the note queued, `w` `w` arming
  (`press w again to wrap up 549175`) and wrapping (`wrapping up · running bash · 18 tok` on the
  row), the result back `stopped` with the child's text naming the note and the wrap-up. A
  real-model run is the lead's.

## Note (2026-11-19, #822, #826, #828, #829)

- **A refused wrap-up names no text (#822).** §3.2's refusal for a row out of the registry and a
  wrap-up line written after the wire closed (`refused`) say `the agent has finished or was
  stopped; to go on, use action="resume"` (`runtime.WRAP_UP_RESUME_HINT`) in the model's answer and
  in `/jobs wrap`'s and `w` `w`'s; a steer's and a follow-up's keep "with this text as its next
  instruction". On a job that has ended, `/jobs wrap` got the service's "<id> already ended: done"
  and the panel's `w` `w` and `m` their own "<id> has ended", each before the kind was asked; the
  agent kind now words them, as its door does, and the panel asks the service's `control` as
  `/jobs wrap` and `/jobs steer` do (ADR-0293's `JobEndedControlAdapter`, note of this date;
  Codex's review, M6; lane b7f verify round 2, V2-F1).
  `tests/agents_ext/test_wrap_up_refusal_822.py`.
- **Plan mode's refusal in a child (#826).** §6 tightens a child to `plan`, and the refusal it
  then read ended in "shift+tab to exit plan mode.". A `-p`/json run serving the command inbox
  (D2's `--command-inbox`, which only ever tightens) now reads that its parent set plan mode and
  to give the plan in its answer (`permission_mode.PLAN_EXIT_CHILD`). The inbox chooses it, not
  `AELIX_SUBAGENT_DEPTH`, which an RPC or TUI process can inherit (Codex M7); ADR-0157's note of
  this date has every host (`tests/builtin/test_plan_exit_by_host_826.py`, a real `--mode json -p`
  child with the inbox included, POSIX only).
- **D2's stdin is not grep's or find's (#828).** `tools/_subprocess.run_cancellable` spawns `rg`
  and `fd` with `stdin=DEVNULL`: on `1c2a4a7c` an `rg` given no path read the inbox line, and the
  child read nothing. The extension installer's runner, which keeps aelix's stdio for pip's
  progress, passes `DEVNULL` once the inbox (or `--mode rpc`) has claimed stdin
  (`tests/tools/test_run_cancellable_stdin_828.py`; ADR-0238's note of this date).
- **Windows (#829).** `SLICE-STATUS.md` records the win32 `os.read` pump under every child's inbox
  and that the real-child inbox tests are POSIX only.
