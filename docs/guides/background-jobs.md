# Background jobs

Status: Accepted

A background job is work the model started and did not wait for: a delegated agent it
ran with `agent(action="spawn")`, a `bash` command it ran with `run_in_background: true`,
a running delegation you moved there with Ctrl+B, or an extension's own kind of job. A
job belongs to the session that started it. When it ends, its result comes back to the
model once, as a notice, and the session goes on from there.

Background jobs are on in this build (#779). The two clients are described where their
tools are: [agent-profiles.md](agent-profiles.md#background-delegation) for delegation
and [tools.md](tools.md#running-a-command-in-the-background) for `bash`. This page is
what they share: where you see jobs, how their results come back, the settings, and what
happens to a job when aelix itself dies. The design is
[ADR-0293](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0293-background-work-is-a-job-its-session-owns.md).

## Where they run

| | TUI | `-p`, `--mode json` | `--mode rpc` |
| --- | --- | --- | --- |
| `agent` `spawn`, `wait`, `status`, `stop`, `result` | yes | yes | yes, since #612 |
| `bash` `run_in_background` (local shell only) | yes | yes | yes, since #612 |
| `job` and `job_stop` tools | yes | yes | yes, since #612 |
| `/jobs` | the panel; `/jobs list` the table | not a command | `commands/run` prints the table |
| Ctrl+B, the footer, the restored line | yes | — | — |
| Waits for its jobs before it exits | — | yes | — |
| `children/*` methods, `aelix_child` and `aelix_notice` | — | — | yes ([below](#over-rpc-and-acp)) |

RPC got the tools and the background actions with #612 (owner, 2026-11-18: "전부 열기 (권장)"),
before and after `new_session` and `resources/reload`
(`tests/cli/test_job_tools_builtin_779.py::test_rpc_and_acp_register_both_and_the_background_actions_across_rebuilds_612`).
The `--mode rpc` column is the CLI's RPC host with delegation and background jobs on; the
WebSocket host (`aelix-server`) builds neither service, so it has none of these. ACP is
[below](#over-rpc-and-acp).

For the tool flags, `job` and `job_stop` count as built-in tools (#779, R9 — the lead's
call): `--no-builtin-tools` and a profile's `builtin_tools: false` turn them off, `--tools`
turns them on only when it names them, and `--no-tools` turns them off with the rest; a
reload keeps that (`tests/cli/test_job_tools_builtin_779.py`). The `defaultTools` setting
treats them as extension tools, as it treats `agent` and `aelix_status`: a list of
built-in names there leaves them on.

## Seeing them

- **The footer** shows a segment while the session has jobs: `⚙ 2 running · 1 result waiting`,
  `⚙ 1 running (quiet 12m)` when a job has said nothing for a while, `⚙ 1 unresolved`.
- **`/jobs`** opens a panel in place of the input line and redraws it every second: `↑↓`
  select a job, `Enter` writes its detail above the panel, `r` closes the panel and pages its
  result into the transcript, `s` `s` stops it, and `Esc`, `q` or `Ctrl+C` close the panel
  without interrupting a running turn (owner OQ-S3-2, 2026-11-12: "↑↓·Enter·r·s s·Esc/q (권장)";
  [ADR-0299](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0299-jobs-opens-a-background-jobs-panel.md)).
  The second `s` stops only the job the first one armed, within 3 s. While the panel is open a
  finished job's result waits for it to close. Two more keys reach a running delegated agent
  (#123, [ADR-0306](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0306-a-running-delegated-child-is-steered-through-a-command-inbox-on-its-stdin.md)):
  `m` closes the panel with `/jobs steer <id> ` in the input for you to finish the note, and
  `w` `w` asks the agent to wrap up, armed exactly as `s` `s` is (`press w again to wrap up
  <id>`). A background command takes neither: `m` and `w` `w` leave the panel open and its
  last row reads
  `this kind of job does not take a message; /jobs stop <id> stops it (panel: s s)`
  (`… a wrap-up; …` after `w` `w`), which is also what `/jobs steer` and `/jobs wrap` answer
  for it (`tests/tui/test_jobs_steer_123.py`).
- **`/jobs list`** prints the table; `/jobs <id>`, `/jobs result <id>` and `/jobs stop <id>`
  show one job, page its result and stop it. **`/jobs steer <id> <text>`** sends a running
  delegated agent a note it reads before its next model call, after the tool calls it is
  already running; `/jobs steer --after <id> <text>` lands it after its current answer instead;
  **`/jobs wrap <id>`** asks it to finish now — one more reply with no tools, then its result
  comes back `stopped`, with what it said (#123). All three run mid-turn, as every extension
  command does, and all three reach a run moved with Ctrl+B as well as one started with
  `agent(action="spawn")` (owner, 2026-11-18: "Ctrl+B 후 /jobs steer·wrap (권장)";
  `tests/agents_ext/test_intervention_doors_123.py`). The answer is one line — `Queued the
  note: …`, `Not taken: …`, `Not sent: …` or `Unknown whether the note arrived: …` — and never
  "not delivered": a note the agent acknowledged may still die with it if it ends before its
  next turn.
- **The model** reads the same table with `job(action="list")`, a job with
  `job(action="status", id=…)`, and pages a finished job's output with
  `job(action="result", id=…, offset=…)`; `job_stop(id=…)` stops a background command or an
  extension's job. A delegated agent is stopped with `agent(action="stop", id=…)`:
  `job_stop` refuses one and says so, and `/jobs stop` stops either. A running delegated agent
  takes a note with `agent(action="send", id=…, task=…)` and a wrap-up with
  `agent(action="wrap_up", id=…)` (#123; `docs/guides/agent-profiles.md`, "Steering a running
  agent"). The `job` tools and `/jobs` take a background agent's own id as well as its job id,
  for one this aelix process started or moved and, once it has ended, for one a restart read
  back; a job id or a unique prefix of one is matched first, and the agent id only when neither
  matches (#827, `tests/agents_ext/test_job_tool_agent_id_827.py`). In the `job` and `job_stop`
  tools an id that names no job answers
  `no job <id> in this session; job(action="list") lists this session's jobs and their ids`;
  `/jobs` answers `no job <id> in this session`.

## How a result comes back

When a job ends, aelix gives the model one notice: what the job said last, cut to 16 KiB,
and how to read the rest. The notice is placed at the next boundary — after the answer
the model is writing, never inside it — and is placed once, even across a restart
(ADR-0293 §8).

- **In the TUI** an idle session starts a turn for it. It never starts one while a dialog
  is open: an approval, a selection or a confirmation waits for your answer first, and the
  notice waits for the turn after it (`tests/tui/test_notice_never_touches_dialogs_611.py`).
- **`-p` and `--mode json`** wait for the jobs they started before they exit (owner,
  2026-10-28, #604: "`-p`는 백그라운드 자식이 끝날 때까지 기다린다."): the notices that are
  waiting go to the model in a notice turn — several that are waiting together go in one turn —
  and the run exits once nothing is left running. The exit code is the first failed
  answer's among your prompt and the notice turns after it (`tests/cli/test_print_verdict_611.py`).
  `Ctrl+C` while it waits stops every job and writes out each result the model was not given —
  on stderr in text mode, one `background_notice_undelivered` event each in JSON mode — and
  starts no turn for them (ADR-0293 §11).
- **A delegated agent that looks stuck** — running in the background, spawned or moved with
  Ctrl+B, and marked `stuck: repeat` (the same tool call with the same result four times in a
  row) or `stuck: error loop` (the same failing call three times in a row) — leaves the model
  one notice while it still runs (#123): its header says the agent "is still running"; inside the
  data fence are its latest calls (up to four, oldest first, each with its status) and its last
  text; after it come what the pattern means, that this can be intended when its task asks for
  the same call again (then leave it running), that it was not stopped, and
  `agent(action="send"|"wrap_up"|"stop", id=…)` with its id
  (`tests/agents_ext/test_stuck_notice_123.py`). A run moved with Ctrl+B asks at the move when
  its panel row shows the mark then, and otherwise at the first snapshot marked after the move; a
  mark that cleared before the move asks nothing. It is not the agent's result, which still comes
  back once, after it, when the agent ends: the footer and `/jobs` do not count it (`/jobs send`
  names it a notice beside the results it sends, counting both among the notices still waiting,
  and in the TUI sends it when it waits alone, #820), `-p` gives it a notice turn and goes on waiting for the agent until its
  result is posted, and `Ctrl+C` in that wait does not write it out. The TUI starts a turn for it
  when idle and its line reads `◆ background notice: …`; `agent(action="wait")` returns as soon
  as it is posted, once, while the agent runs, and adds its line after the result sentences, and
  `agent(action="status")` says `stuck: <pattern>` of a running agent
  (`tests/jobs/test_attention_123.py`). A foreground agent gets none: its panel row shows the
  mark.
- **A run moved with Ctrl+B** counts its time from its start, not from the move: its notices'
  "after …", `agent(action="status")`'s "for …" and the `/jobs` table include the time it ran in
  the foreground, and `/jobs <id>`'s "started" is the wall-clock time the call started at (#821,
  `tests/agents_ext/test_moved_elapsed_821.py`). After a restart, `/jobs <id>` of an ended job
  shows the run time its outcome recorded.
- **The `◆ background result` line** and an ended agent's `agent(action="status")` row show
  the first line of what it said: an agent's is cut at 160 characters, the whole line at 256
  bytes, and a cut one ends in `…`: at the last whitespace before the cut when that keeps at
  least two thirds of the bound (107 characters, 171 bytes), otherwise at the bound itself, so a
  long path or URL after a short word is cut inside it rather than dropped (#825,
  `tests/jobs/test_brief_cut_825.py`).
- **Over RPC** a waiting notice raises an `aelix_notice` frame, and the host places it with
  `children/notices` or with its next prompt ([Over RPC and ACP](#over-rpc-and-acp)).
- **A session you open again** that has results nobody gave the model starts no turn for them.
  The TUI says `◆ 1 background result is waiting. It goes to the model with your next message,
  or run /jobs send to send it now.` (`tests/tui/test_restore_611.py`); `-p --continue` delivers
  them in its own run.

## When jobs stop

`/new`, `/resume`, `/fork`, quitting and `/reload` stop the session's jobs (owner, 2026-11-07,
#611: "`/reload`는 작업을 멈추고"). `/reload` waits up to 10 s for them; one it cannot confirm
stopped in that time is shown `unresolved` and finishes in the background
(`tests/jobs/test_reload_unresolved_611.py`). A restart of aelix never kills anything: a job it
finds still running from the last process is reported, `unresolved` or `lost`, and its notice
says so (`tests/jobs/test_restart_611.py`).

## Settings

All of them are read from your **global** settings file (`~/.aelix/agent/settings.json`) only;
a repository's `.aelix/settings.json` cannot change them.

```json
{
  "backgroundJobs": {
    "enabled": true,
    "limits": { "agents": 4, "bash": 4 },
    "maxTotal": null
  }
}
```

| Key | Default | What it does |
| --- | --- | --- |
| `enabled` | `true` | `false` turns background jobs off for the next launch (owner OQ-S3-4, 2026-11-12: "전역 설정 (권장)"). |
| `limits.agents` | 4 | How many delegated agents run at once — foreground and background — in the TUI, `-p` and json. An integer from 1 to 16. |
| `limits.bash` | 4 | How many background `bash` commands run at once. An integer from 1 to 16. |
| `maxTotal` | off (`null`) | A bound across every kind, an extension's included. An integer from 1 to 32. |

- **`enabled: false`** is read once, when aelix starts, before any extension loads: no job
  service, no `job` tool, no `spawn`, no background `bash`, no footer segment, no `/jobs`, and a
  delegated child reads the same file. `/settings` has a "Background jobs" row that writes it;
  it takes effect the next time you launch aelix. A value that is not `true` or `false` counts as
  `true`, with one warning (`tests/jobs/test_enabled_setting_611.py`).
- **`limits` and `maxTotal`** are read each time a job asks for a slot, so a change applies to
  the next one and never stops a running job. A value outside its range falls back to the
  default with one warning (`tests/jobs/test_admission_611.py`). A refused start says which
  setting refused it.
- **Over `--mode rpc`** `limits.agents` and `maxTotal` apply as in the TUI since #612: the
  delegation job kind is registered there now
  ([ADR-0307](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0307-a-cancelled-delegations-report-is-its-calls-result-and-a-session-change-settles-the-turn-first.md) D7).

## If aelix itself dies

A job's processes are aelix's children. What is left of them when aelix is killed — not
quit, killed — depends on the platform. This is what #777 measured
(ADR-0293's 2026-11-17 note, `tests/cli/test_parent_death_611.py`): a real `-p` run with a
background delegation and a background `bash` sleeper, and a real TUI with one delegation moved
by Ctrl+B or left in the turn, each killed.

| Platform | `-p`: background delegation and background `bash` | A delegation Ctrl+B moved | A TUI delegation not moved |
| --- | --- | --- | --- |
| Linux (SIGKILL) | The delegated child and the background shell are gone within 5 s (`pdeathsig`; Linux docker, 20 of 20 runs). Recorded only: the shell's own command was alive 5 s after the kill, in 20 of 20 runs; only the shell is signalled (owner OQ-C/D, 2026-11-10: "둘 다 권고대로 (권장)"). | The child is gone within 5 s (20 of 20 runs). | The child is gone within 5 s (10 of 10 runs). |
| Windows (`TerminateProcess`) | Every process the case watched ends within 10 s (CI run 44023793108, all four Python versions). That is the processes it watched, not a proof that nothing else is left. | The same. | The same. |
| macOS (SIGKILL) | Recorded only (local, 10 of 10 runs, 2026-11-18): the delegated child, its shell and its sleeper were gone within 0.4 s of the kill; the background `bash` shell and its sleeper were alive 5 s after it, and the case then killed them. Before #123 all five were alive (20 of 20). | Recorded only: the child, its shell and its sleeper gone within 0.4 s (10 of 10). Before #123: alive 5 s after the kill (20 of 20). | Recorded only: the same, gone within 0.4 s (10 of 10). Before #123: alive (10 of 10). |

On macOS there is no `pdeathsig`. A delegated child ends because its command inbox, a pipe
from aelix, closes when aelix dies (#123, ADR-0306); nothing stops a background `bash` and what
it runs. Stop what is left yourself, or quit aelix instead of killing it: a normal quit stops
every job first.

## Over RPC and ACP

Since #612 (owner, 2026-11-18: "전부 열기 (권장)") the CLI's RPC host (`aelix --mode rpc`, with
delegation and background jobs on) gets what the TUI gets: the model can spawn a background agent,
run a background `bash` command and use `job` and `job_stop`
([ADR-0307](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0307-a-cancelled-delegations-report-is-its-calls-result-and-a-session-change-settles-the-turn-first.md) D7).
The host sees and steers the delegated agents with six `aelix_control` methods, listed in
`capabilities.methods` only where served: the first five when delegation is on, `children/notices`
when background jobs are on.

| Method | What it does |
| --- | --- |
| `children/list` | `{epoch, children}`: every running delegated agent and up to 64 that ended, foreground and background, each with its state, end, current tool, last calls and text, stuck and wrap-up marks, open approval, `sessionId`, `jobId` and the `agent` call that started it. Answered during a turn. |
| `children/events {id, afterSeq}` | that agent's kept events after `afterSeq`, all of them without it: `{epoch, events, nextSeq, lowSeq, gap}`. Answered during a turn. |
| `children/steer {id, text, mode}` | a note, as `/jobs steer`; `mode: "follow_up"` lands it after the agent's current answer. Answers `queued`, `unknown` or `not_sent`; a refusal is an error that names the state. |
| `children/wrap_up {id}` | asks the agent to finish now, as `/jobs wrap`: it ends "wrapped up at the user's request". |
| `children/stop {id}` | stops the agent and answers the state it has when the stop returned, or after 10 s; its last `state` event is its end. |
| `children/notices` | places the notices that wait for the model, in one notice turn: `{started, placed, reason, remaining}`. |

The three that act on an agent are answered even while `agents/run` holds the session, are
refused while a session change runs ("a session change is in progress; retry when it has
finished"), and one in flight when a session change begins ends `unknown`: the agent may have read
it (`tests/rpc/test_children_family_612.py`). The exception is a wrap-up still waiting for the agent
to take the `cancel` of its open approval request: it is answered with the error "a session change
began before this request was sent", the wrap-up is not kept, and you can ask again. The agent may
still have read that `cancel`, "the agent was asked to wrap up".

**Events.** `aelix_child` carries one agent's changes: `state` (first, then on each change of its
state, its session or its job; the last one has `end` — `completed`, `wrapped_up`, `stopped`,
`cancelled` or `failed` — with the outcome and reason), `tool_start` and `tool_end` (every start has
exactly one end; `no_result` for a call its turn or its run ended without), `text`, `stuck`,
`wrap_up`, and `approval` ([agent-approvals.md](agent-approvals.md)). Every event carries `id`,
`seq` and `epoch`; each string is cut to one line, 120 characters for a name or an argument and 512
for a text, and the largest event measured with every string at its cap is under 5 KiB
(`tests/agents_ext/test_child_events_612.py`). `aelix_notice` (`event: "queued"`) says a notice
waits for the model; a session that starts with notices already waiting sends one at once. Call
`children/notices` while the session is idle, and again while `remaining` is above 0, or prompt,
which places them first. `placed` counts the notices placed before the turn's first model call
(at most 64 KiB); the turn can place more between its model calls, so read `remaining`, not
`placed`, to know what still waits. `aelix_subagent` is unchanged, and a
background agent never sends one.

**Replay.** Each agent keeps its latest 256 events. `epoch` names the session's current build: a
`/reload`, `resources/reload` or a session change starts a new one, and the events of an earlier
one cannot be replayed (owner, 2026-11-18: "같은 프로세스 + 영속 기록 (권장)"). Keep `(epoch, id,
seq)` and drop duplicates; an answer with `gap: true` means events were lost, so read
`children/list` again. After a reconnect no agent of before is running: what remains is the parent
session's messages (`get_messages`; the `agent` call's result says what it did) and each agent's
own session file, which its `sessionId` names.

**When a session ends** — a session change, quitting, `/reload`, or EOF on an RPC host's input — a
running turn is stopped first and given at most 10 s to settle (#800), then the session's agents and
jobs are stopped and given 10 s more ([When jobs stop](#when-jobs-stop)). A job started for a
session that has ended is refused, "the session ended before the job started"
(`tests/jobs/test_ended_owner_epoch_612.py`). One an extension starts from a `session_start`
handler after a `/reload` or a session change is the new session's
(`tests/cli/test_session_start_job_owner_612.py`, measured over RPC).

**Resuming a stopped agent** goes through the model; a host has no method for it (owner,
2026-11-18: "#612에서 빼기 (권장)"): `agent(action="resume", id=<its sessionId>, profile=…, task=…)`.
It resumes when the agent's start is on the session's current branch and was the model's own, its
records and file are as they were written, its profile is the same file and not a project one,
nothing in this process runs it, its folder is still inside yours, its model still resolves the
same way, and no other process holds its file's lock — a free lock does not prove the agent's
process exited, and a filesystem that cannot lock skips that check
([agent-profiles.md](agent-profiles.md#resuming-an-agent)). An agent started with `--no-session`
cannot be resumed.

**A cancelled delegation.** When a turn is cancelled while an `agent` call runs, the call's result
is the report of what the agents did, so `get_messages` returns it, also after a relaunch with
`--session` (#800). If a second cancel lands while that result is being written, the report reaches
the model at the next prompt in the same process instead, as a message, and a session reopened in
a new process before that prompt does not have it.

**Asking you.** A host that sends `ui/enable {"enabled": true, "childApprovals": true}` is asked a
`default` parent's child's questions ([agent-approvals.md](agent-approvals.md)).

This was run live with a real model on macOS against a real `--mode rpc` parent.

**ACP.** aelix-acp runs `aelix --mode rpc` as its native, so with a native that has #612 the ACP
model gets what an RPC model gets — `agent(action="spawn")` and the other background actions,
`job`, `job_stop` and `bash`'s `run_in_background`. aelix-acp PR #39 (`03b9e32`) pins aelix-ai
`83714045`, which has #612, and brings the rest to the client:

- every client sees each agent the model delegated to on the `agent` card that started it — its
  last text, `stuck`, wrapping up, the tool it runs, and how it ended — also after that call ended
  and after later prompts; a client that declares `aelix/nestedToolCalls` also gets each agent's
  own calls as nested tool calls, and an `/agents run` agent as a card of its own;
- `_aelix/manage children/list`, `children/events`, `children/steer`, `children/wrap_up` and
  `children/stop` are answered while a prompt runs;
- a client that declares `aelix/notices` is sent `_aelix/notice` when a result waits, and wakes the
  session with `_aelix/manage children/notices` while it is idle; without it, a background result
  reaches the model at its next prompt. `_aelix/notice` is not sent once the client is gone, nor
  after a `session/load` replaced the session while the old native is being ended;
- the adapter gives a native that serves these 45 s on POSIX between SIGTERM and SIGKILL, so the
  stages of [When a session ends](#over-rpc-and-acp) — up to 10 s for a turn, then 10 s for the
  agents and jobs — can finish (a native whose session end takes 8 s ended in about 8.1–8.6 s, where
  the adapter before killed it at 5 s); `session/close` can therefore take up to 45 s. Windows ends
  the native with its Job Object, as before.

This was run live with a real model on macOS through aelix-acp's test client, during the lane and
before its final commit: steer, wrap-up and stop during a prompt, a notice and then
`children/notices`, and closing with a running agent, before a fix round changed the fence, added
the close's read-through and counted an abort the turn's poll sent while `commands/cancel` waited
as the cancel's one; an agent's card after a second prompt, after that fix round. Those rows were
not run again on the final code, which aelix-acp's unit and native tests hold, and the close times
were measured with a scripted model; ADR-0307's note of 2026-11-20 has the measurements and each
row's lane checkpoint. Not run: Windows and Linux natives under ACP, Studio as the client, and the
45 s deadline itself. Under aelix-acp `16d6555b`, whose recorded native (`e19ab55e`) predates #612,
the client sees no agent rows, events or notices, and the close kills the native 5 s after SIGTERM.

## For extensions

An extension's `ExtensionAPI` (`aelix` in `setup(aelix)`, see
[extension-authoring.md](extension-authoring.md)) has two members that reach the job
service. They exist only while background jobs are on: with them off — a user's
`backgroundJobs.enabled: false`, or a build with the gate off — reading either raises the
interpreter's own `AttributeError`, so `hasattr(aelix, "jobs")` is `False` and
`getattr(aelix, "jobs", None)` is `None`, as before #722
(`tests/jobs/test_facade_611.py`,
`tests/jobs/test_enabled_setting_611.py::test_switched_off_an_extension_sees_jobs_and_post_notice_as_the_gate_off_shows_them`).
Check before you use them.

**`post_notice(notice_id, body, *, title="", brief="")`** leaves the model a notice, framed
as background output, once per `notice_id` in a session; it answers `False` when that id was
already posted or there is no session to post to. It is delivered the way a job's notice is:
at the next boundary, never inside an answer and never while a dialog is open; in the TUI an
idle session starts a turn for it (`tests/tui/test_wake_611.py::test_an_extension_notice_wakes_counts_and_is_sent`).
Its id is your extension's name and `notice_id`, so another extension cannot collide with it.
A lone surrogate in a notice's title, label, kind, brief or body reaches the model as U+FFFD,
and two `notice_id`s that differ only there stay two notices
(#810, `tests/jobs/test_lone_surrogate_title_810.py`).

**A running job of your kind can ask for attention once:** its runner's context has
`attention(body, *, brief="")` (`JobContext` in `aelix_coding_agent/jobs/contract.py`; #123).
It posts one notice framed as the job's, saying the job "is still running", with the id
`<job id>:attention` and `details.status == "running"` — the line a host reading
`aelix.job_notice` entries will see. It answers `False` and posts nothing a second time, for a
job that does not notify, and for a job that is starting, asked to stop or whose `run`
returned. It is not the job's result: `JobSnapshot.result_waiting` and `notice_state` do not
count it, `JobSnapshot.attention_waiting` does, and nothing of it is restored after a restart
(`tests/jobs/test_attention_123.py`). Over RPC it is placed ahead of the host's next prompt as a
`user` message, as a result's notice is; since #612 the host is told of it by an `aelix_notice`
frame, as of every queued notice, and can place it with `children/notices` (read in the code,
not measured for this notice).

**`jobs`** lets you run your own kind of job, which the footer, `/jobs` and the `job` tool
then show beside the built-in ones:

| Member | Purpose |
| --- | --- |
| `register_kind(adapter, runner_factory)` | Register a kind, in `setup()`. A `/reload` runs `setup()` again, which registers it again: a new generation of the same kind. |
| `reserve(kind, wanted=1)` | Take up to `wanted` slots before you ask the user anything; hand the reservation to `start`. Refused with `JobStartRefused`, whose message names the setting, when none is free. |
| `start(kind, *, title, params=None, notify=None, reservation=None, client_start=None)` | Start a job for the current session; its id. `client_start` is your launch data, kept in the job's records and handed back on its snapshots. |
| `list()` / `status(job_id)` | Snapshots (`JobSnapshot`) of your kinds' jobs in the current session. |
| `stop(job_id, *, reason="user", wait=None)` | Stop one of them; its snapshot. |
| `changed()` | Wait for the next change to any job. |
| `offer_detach(kind, offer)` | Offer a running call of a `detachable` kind to the TUI's Ctrl+B; returns the callable that takes the offer back. |

The adapter is the `JobKindAdapter` protocol in
`aelix_coding_agent/jobs/contract.py`: the kind's `name`, `version`, `label`,
`capabilities` (`KindCapabilities`: `default_bound`, `notify`, `detachable`, …),
`settings_key` — the user's `backgroundJobs.limits.<settings_key>` bounds it — and how its
outcome is decoded, rendered as a notice and as panel rows, paged, and recovered after a
restart. A synthetic kind that implements all of it is
[`tests/jobs/_sleeper.py`](https://github.com/handochan/aelix-ai/blob/main/tests/jobs/_sleeper.py).
A kind whose jobs also have ids of its own adds `job_for(ident, jobs)` (the `JobIdAdapter`
protocol, #827): when `ident` is neither a job id nor a unique prefix of one, the `job` tools and
`/jobs` hand it its own jobs of the current session, newest first, use its answer only when it is
one of them, and refuse an `ident` two kinds answer with different jobs. A kind that declares
`controls` can also add `ended_control(job, verb)` (`JobEndedControlAdapter`, #822): the
sentence `/jobs steer|wrap` and the panel show for a job of the kind that has ended, in place of
`<id> already ended: <state>`. The panel's `m` types no text, so a kind can add `ended_message(job)`
(`JobEndedMessageAdapter`, #848) for the sentence it shows instead; a kind without it gets the steer's
`ended_control` sentence.

The facade is bound to your extension and to the runtime it came from: it registers kinds
under your extension's name, starts, lists and stops only jobs of your kinds in the current
session, and refuses every call once a `/reload` or a session change has replaced that
runtime. A job's runner must not keep `aelix` or a context past its start for the same reason.
Your jobs stop at `/new`, `/resume`, `/fork`, quitting and `/reload`, as every job does.

When a CLI-RPC end reports `status:"stored"`, use its entry coordinates to
read back the `agent` result on that entry's own path after writer settlement. Placed job notices retain their
custom entries outside the three message roles in the projection (ADR-0309 D1,
D3 and D4).
