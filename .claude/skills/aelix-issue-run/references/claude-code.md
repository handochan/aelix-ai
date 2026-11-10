# Claude Code specifics

Everything here applies only when lanes run as Claude Code subagents: Workflow
`agent()` calls, or background agents started with the Agent tool. Each rule comes from
a lane that lost work, idled for hours, or collided with another writer.

## Return values and waiting

- **The final message is the return value.** A lane that ends its turn to "wait"
  kills every background process it started (2026-11-04: the suite died at 5%, the
  second commit was never made, and the report was one sentence). Every brief says:
  stay in the turn until everything you started has finished.
- **Cap each wait at about 4 minutes.** Subagent calls are cached for 5 minutes. A
  wait longer than that rewrites the lane's whole context on its next call. A 30-day
  measurement (2026-09-21 to 2026-10-21) put 21% of all spend on such rewrites, 72% of
  them while polling the full suite. Poll with a bounded loop in the foreground, and
  re-issue it.
- **No Monitor in lanes.** Two lanes waited on notices that never arrived, for 6 and
  10.5 hours (2026-11-03/04). If a lane's last message says it is waiting, check its
  transcript's mtime. If that is more than about 10 minutes old, read the watched
  output yourself.
- **Keep the full suite out of implement and fix lanes.** The lead runs it, once per
  round, and never two suites on one machine at once.

## Workflow scripts

`templates/workflow.js` is a skeleton with these rules built in.

- **Name the model on every `agent()` call.** Omitting it inherits the session's
  model. Keep verify and critic stages on the strongest model you allow, and if a
  retry fell back to a smaller one, say so in the result.
- **`agent()` returns null** on a terminal API error (a 529 overload), on a session
  limit, when the user skips the agent, and when the StructuredOutput call fails to
  parse. `parallel()` keeps the nulls. Filter them, and throw a clear error when a
  stage yields nothing.
- **A 529 is worth a retry; a session limit is not.** On 2026-09-08 every retry of
  every agent failed within seconds after the limit was hit. Stop, and after the reset
  rerun with `Workflow({scriptPath, resumeFromRunId})`. Completed agents replay from
  the cache, and only the failed ones run live.
- **A lane that died mid-write leaves partial edits.** Before resuming, audit the
  worktree: `git status --short`, `py_compile` each owned file, run the lane's tests
  once, and grep for each deliverable by name. Patch only that lane's prompt in the
  persisted script with a preface ("a previous attempt died mid-way; present: ...;
  measured now: ...; finish only what is missing"), then resume. Backticks inside a JS
  template literal must be escaped.
- **Keep structured returns small.** A 7.9 KB StructuredOutput failed to parse five
  times (#363, 2026-10-06), and the lane's review stage never ran. Ask for plain
  strings with newlines escaped, each field capped, and long evidence in a file cited
  by path. Recovery: the work is usually intact in the worktree. The raw attempt is in
  the agent's transcript, truncated.
- **Size the fan-out.** Two 11-issue workflows at about 10 agents per issue hit two
  session limits in one afternoon. Budget one implementer, one verifier and one
  cross-review per lane, plus a fixer only when there are findings.

## Talking to running lanes: don't

- **Never SendMessage a Workflow lane, finished or running.** It resumes the agent
  from its transcript as a second writer in the same worktree (2026-09-06 and
  2026-11-06). TaskStop on that id stops only the copy; a workflow-owned lane is out
  of its reach. If a copy was spawned, stop it, then check the original's life by its
  transcript's mtime and by `git log` and `git status` in the worktree. Put mid-run
  scope changes in the issue body (lanes reread it) or in the next round's prompt.
- **A background Agent-tool lane** shows `idle` when it is ready to collect. Only a
  task notification means it finished. Never send a running lane "wrap up and report":
  that truncates its work. Once it has returned, a SendMessage resumes it. Use that to
  hand it a result it was waiting for, and never while it still runs.

## Transcripts, hand-backs and scratch

- Lane transcripts live under the session's project directory in `~/.claude/projects/`:
  `<session>/subagents/workflows/<run>/agent-<id>.jsonl` for Workflow lanes, and
  `<session>/subagents/agent-<id>.jsonl` for Agent-tool lanes and resumed copies. If a
  lane returned nothing, read its last assistant `text` and `tool_use` entries there.
- **Persist every hand-back** to the repository's durable notes directory when it
  arrives, together with any later addendum from the same agent. A hand-back may come
  as a SendMessage tool call, not as the final text. Take it from that call's input.
- **Subagents may be refused when they write report files.** Ask for the report as the
  final message, or as a structured field with a path to an evidence file in the
  lane's own scratch.
- **The scratchpad is per session, not per agent.** Two parallel lanes writing
  `msg.txt` there overwrote each other (2026-09-22). Give each lane `/tmp/<n>-<role>/`
  and say the shared scratchpad is off limits for writes.
- **Subagents can drive the TUI.** `os.forkpty` gives the child its own pty, so a
  subagent with no TTY can still capture the TUI (aelix-live-check). Delegate the
  capture. Keep the judgement of whether it looks right.

## The permission classifier

In auto mode, an action class can be refused whatever the conversation said:

- A compound `git fetch && git merge --ff-only ... && git push origin main` was
  denied, and the same commands as separate calls went through. Issue merge and push
  as single commands.
- Amending or force-pushing a pushed branch is refused. Update a pushed PR branch with
  a merge commit (aelix-land). A lane's local `git commit --amend` was refused too: a
  brief is not the maintainer's consent. Leave it uncommitted, record
  `git stash create` under a ref, and hand the amend to the lead.
- A remote branch delete, or a change to environment or branch protection, may need
  the maintainer to run it. Hand them one short line to run, and record it in the
  handoff. A `!` command whose output never appears in the chat did not run. Re-read
  the remote state.
- Issue comments nobody asked for are refused. Keep them as drafts and ask.
