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

| | TUI | `-p`, `--mode json` | `--mode rpc` (and ACP over it) |
| --- | --- | --- | --- |
| `agent` `spawn`, `wait`, `status`, `stop`, `result` | yes | yes | no, until #612 |
| `bash` `run_in_background` (local shell only) | yes | yes | no, until #612 |
| `job` and `job_stop` tools | yes | yes | no, until #612 |
| `/jobs` | the panel; `/jobs list` the table | not a command | `commands/run` prints the table |
| Ctrl+B, the footer, the restored line | yes | — | — |
| Waits for its jobs before it exits | — | yes | — |

On RPC the job service is still built and `/jobs` is still a command; only the two tools
are left out, before and after `new_session` and `resources/reload`
(`tests/cli/test_job_tools_builtin_779.py::test_rpc_and_acp_register_neither_across_rebuilds_and_keep_the_jobs_command`).

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
  finished job's result waits for it to close.
- **`/jobs list`** prints the table; `/jobs <id>`, `/jobs result <id>` and `/jobs stop <id>`
  show one job, page its result and stop it.
- **The model** reads the same table with `job(action="list")`, a job with
  `job(action="status", id=…)`, and pages a finished job's output with
  `job(action="result", id=…, offset=…)`; `job_stop(id=…)` stops a background command or an
  extension's job. A delegated agent is stopped with `agent(action="stop", id=…)`:
  `job_stop` refuses one and says so, and `/jobs stop` stops either.

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
- **Over RPC and ACP** a delegated agent's limit is still the fixed 4 of `MAX_LIVE_CHILDREN`:
  the delegation job kind is not registered there until #612, so `limits.agents` and
  `maxTotal` do not apply to it (contract r2 §5.1, Codex r1 #12).

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
| macOS (SIGKILL) | Recorded only: the child, both shells and both sleepers were alive 5 s after the kill (local, 20 of 20 runs), and the case then killed them. | Recorded only: alive 5 s after the kill (20 of 20). | Recorded only: alive 5 s after the kill (10 of 10). |

On macOS, nothing stops them when aelix is killed (there is no `pdeathsig`): stop what is
left yourself, or quit aelix instead of killing it: a normal quit stops every job first.

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

The facade is bound to your extension and to the runtime it came from: it registers kinds
under your extension's name, starts, lists and stops only jobs of your kinds in the current
session, and refuses every call once a `/reload` or a session change has replaced that
runtime. A job's runner must not keep `aelix` or a context past its start for the same reason.
Your jobs stop at `/new`, `/resume`, `/fork`, quitting and `/reload`, as every job does.
