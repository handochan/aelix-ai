# 0276. A delegation has no time limit, and a stopped child reports what it did

Status: Accepted (2026-10-28, #604 phase 0). All of D1–D18 are decided and
implemented: D1–D7, D15 and D16 remove the time limit and give a human the ways
to end a delegation instead; D8–D14, D17 and D18 decide what a stopped or failed
child gives back to the parent model. Review round 1 (2026-10-28: child-authored
text on the new surfaces is cleaned and the Esc report fences it as data; a
cancelled batch reports a member whose own cleanup was still running; written
files are told apart by the path the child wrote; a failure whose only
diagnosis is stderr reads as no text, with that diagnosis as its error and
reason; the cancel reason no longer names Esc). Review round 2 (2026-10-28:
child text loses every whole escape sequence and every Unicode format
character, and the report's header marker and fence tags are neutralised in
any case and spacing; a channel's own error keeps the child's stderr after
it; an exception group's or a colourised traceback's reason is its exception
line; a partial answer with nothing left once cleaned is no text; a member
that ran and ended on an internal error is named as such in the report).
Date: 2026-10-28
Issue: #604 (phase 0). #610 (resume, phase 1) builds on the child session this
record hands back.
Amends: ADR-0196 (the P2-consumed `timeout_ms` field), ADR-0197 (§(j)'s
post-EOF exit wait, §(k)'s `timeout_ms` precedence and 10-minute default, the
per-prompt budget's "10 min of wall clock"), ADR-0199 (the scope's governance
item, §(d), §(f), §(j)'s timeout numbers and batch wall clock, §(k), and the
REPL's 30-minute bound in the deferred list and the consequences), ADR-0201
(turn-contract row 5 and `send_timeout_ms`), ADR-0243 (the settle statuses and
the `cost_known` paragraph, which name the deadline; §5's no-path rule is kept
and extended to the session id this record hands back). Each carries a dated
`Amended 2026-10-28 (ADR-0276, #604)` note.
Relates: ADR-0275 (RPC `agents/run` and its mutation lock), ADR-0235 (pi is the
reference: pi has no delegation time limit).

## Context

A delegated child was cut off by a wall-clock limit while it was still working,
and nothing it had done came back to the parent (#604). In the reported case the
parent model chose three minutes; the child had made 60 tool calls by then.

Until this record a delegation had three nested clocks, all aelix-original:

| Clock | Where | Value |
|---|---|---|
| tool argument `timeout_ms` | `aelix_agents/tool.py` | 1 000 – 1 800 000 ms per task; outside that the call was refused |
| profile key `timeout_ms` | `agents/profile.py` | any positive integer; a bad value dropped the whole profile |
| default | `print_channel.DEFAULT_TIMEOUT_MS` | 600 000 ms when neither was given |
| one batch call | `batch.MAX_BATCH_WALL_MS` | 1 800 000 ms, enforced by deriving each member's clock from what was left, less a 7 s kill-leg reserve; a member with under a second left was refused |

The same deadline was borrowed by waits that are not the work: the exit wait
after both pipes close (`max(remaining, 2 s)`), the RPC channel's command
timeout and its turn wait. ADR-0199 §(m) and `batch.py` documented the batch
ceiling as "the only bound on how long the user is locked out".

The owner decided on 2026-10-28 (`docs/05-post-beta-direction.md`, the #604
execution record) to remove every delegation time limit — the tool argument,
the profile key's effect, the 10-minute default and the 30-minute batch cap —
and to keep the cleanup waits, each with a bound of its own. The reference
survey (`.omc/specs/604-reference-survey-2026-10-28.md`) agrees with the
direction: pi has none (Esc only); Codex removed
`agents.job_max_runtime_seconds` and kept it as a no-op; OpenCode v2 has none
and closed the change that would have added a kill timeout; Qwen Code,
deepagents/LangGraph and OpenHands bound turns or steps, not time. Kimi CLI let
the model choose a timeout and the model always chose 120 s (#2408) — #604's
failure exactly. Gemini CLI's 10-minute default is the counterexample.

## Decision

### D1. The `agent` tool has no `timeout_ms`

The schema property is gone, and so are `MIN_TIMEOUT_MS`, `MAX_TIMEOUT_MS` and
`AgentCall.timeout_ms`. `parse_agent_call` never reads the key.

A call that still carries it — a model that learned it in an older session, a
provider replaying an old call — is **not refused**. The kernel validator
delivers unknown keys untouched, and a refusal would cost a round trip and teach
the model nothing it can act on, since there is no limit left for the value to
set. `background` stays refused: it names something that would not happen.

### D2. A profile's `timeout_ms` is accepted, warned about, and ignored

- **The field stays.** `AgentProfile.timeout_ms` is deprecated and never read.
  RPC `agents/show` returns `asdict(AgentProfile)`, so deleting the field would
  drop a key hosts already see, and the spawn-time warning needs to know the key
  was set.
- **The key stays known.** `"timeout_ms"` remains in `_KNOWN_KEYS`, so the
  generic unknown-key loop does not warn a second time about the same line.
- **The parse is lenient.** Any value loads; a profile is never dropped for this
  key. It produces exactly one `unknown_field` warning, `timeout_ms is ignored:
  delegated agents run without a time limit (#604)`, with `; its value … is not
  an integer either` appended when the value is neither an integer nor empty
  (`bool` counts as not an integer; `timeout_ms:` with no value, or `null`, gets
  the plain warning). Only a real integer is stored.
- **Spawn time says it too.** The parse-time diagnostic reaches `/agents list`
  and RPC `agents/list` only, so `runtime._run` — the one place both doors
  pass — logs one `logger.warning` per profile file whose stored `timeout_ms` is
  set. "Once" is per process and per path (a module-level set): `/new`, `/fork`,
  `/resume` and `/reload` do not repeat it. The log line never raises.

### D3. No work deadline anywhere

| File | Removed |
|---|---|
| `aelix_agents/print_channel.py` | `DEFAULT_TIMEOUT_MS`, `SpawnPlan.timeout_ms`, the plan → profile → default resolution, the deadline and the main wait's `timeout=`, the branch where the deadline fires first, `outcome="timeout"`, and the post-kill drain that only that branch reached |
| `aelix_agents/rpc_channel.py` | the resolution, the `timeout_ms`/`started` parameters of `_drive` and `_run_turn`, the remaining-budget arithmetic, and `TimeoutError → "timeout"` |
| `aelix_agents/batch.py` | `MAX_BATCH_WALL_MS`, `KILL_LEG_RESERVE_MS`, `_BATCH_BUDGET_EXHAUSTED`, `_Batch.deadline`, the member clock and its refusal, `_kill_leg_reserve_ms`, `_member`'s `steps_left` |
| `aelix_agents/runtime.py` | `timeout_ms` on `spawn_granted` and `_run`; `spawn` keeps accepting it and forwards nothing (D6) |
| `aelix_agents/extension.py` | the single-mode `timeout_ms=pending.call.timeout_ms` |

`BatchOutcome.wall_ms` stays: it is a measurement, not a bound. `_Batch.deadline`
fed only the member clock; wave admission and the posture floor never read it.

### D4. The waits that borrowed the deadline get bounds of their own

- **The exit wait after both pipes close:** `POST_EOF_EXIT_BOUND_SECONDS = 30.0`
  (`print_channel.py`). Both pumps at EOF means the child is exiting — its MCP
  and extension teardown happens before that EOF, inside the main wait — and the
  gap to `returncode` is the loop's child watcher, milliseconds. A child that
  closes its stdio and then wedges is reaped after the bound, as before, and the
  channel then passes `lingered=True` to `build_result`:
  - the stream had reached its terminator (`saw_agent_end`) with a stop reason
    that is neither `error` nor `aborted` → the answer is whole, the run stays
    `ok`, and `SubagentResult.note` says `the agent finished but did not exit
    within 30 s; it was stopped`. The reaper's exit code is not the child's
    verdict. The note is a new defaulted contract field rendered as `Note: …` —
    never as `Error:`, which every renderer prints as a failure;
  - anything less (no terminator, a terminator possibly dropped as an oversize
    line, an `error` or `aborted` stop reason) → `error`, with `the agent closed
    its output but did not exit within 30 s` (`envelope.LINGERED_REASON`), the
    run's `reason` (D10).

  A stop that lands during the wait stays `aborted`: `lingered` is read only
  beside `outcome="ok"`. The 2 s floor this replaces (`POST_EOF_EXIT_GRACE_SECONDS`,
  P2 review MEDIUM #3) is gone with the deadline it was a floor under.
- **An RPC command's answer:** `RPC_COMMAND_TIMEOUT_MS = 180_000`
  (`rpc_channel.py`), the value the real-child pins use. Left unset it would be
  the client's 30 s, which ADR-0201 rejected because a cold child spends ~25 s
  reaching its read loop. A command that times out (a `prompt` the child never
  acknowledged) is an `error`, not a time limit. The bound covers WAITING FOR
  THE ANSWER, not the write before it: a command whose stdin write cannot drain
  (a child that stopped reading) waits outside it (Limits).
- **The RPC turn:** unbounded. `RpcClient.prompt_and_wait` gains a keyword-only
  `unbounded: bool = False`; with `True` the turn's terminator is awaited through
  `_await_terminator(done, None, …)` (its `timeout_s` is now `float | None`). The
  default keeps pi's parity: `None` and `0` still mean 60 s. The wait still ends
  on the child's death (the client's death leg) and on the caller's
  cancellation.
- **The batch semaphore:** no bound. Waiting for earlier members is what it is
  for.

### D5. The bound on the work is a human, who must be able to end it

- **TUI Esc / Ctrl+C during a turn:** `_safe_abort` → `harness.abort()` →
  `turn_task.cancel()` → the channel's `_eager_abort` kills the child tree.
  Unchanged; verified by the design review.
- **`-p`:** `asyncio.run` turns SIGINT into a cancellation of the main task, and
  print mode handles SIGTERM and SIGHUP. Unchanged.
- **`stop` / `stop_all`** (session teardown, `/new`, `/resume`, `/fork`, quit):
  unchanged.
- **RPC `agents/run`** is cancellable (`commands/cancel`, `CANCELLABLE_METHODS`),
  but it holds the control plane's mutation lock while it runs (ADR-0275 §2) —
  now for as long as the delegation runs, with no clock to release it. A host
  that needs the lock back cancels the run.
- Two holes the 10-minute default used to cover are closed by D15 and D16.

#### D15. `/agents run` can be interrupted

The handler was awaited inside the REPL loop, outside the turn wrapper, so Esc
(filtered on `running`) was inert and Ctrl+C on an empty buffer only armed the
exit window: a child that stalled held the TUI until the process was killed from
outside.

`CommandContext.run_cancellable(awaitable)` is wired by `run_tui`. It runs the
work as a task, sets `running` (spinner on, Esc and Ctrl+C live), points
`on_interrupt` at **that task's cancel** instead of `_safe_abort` (there is no
turn to abort), and restores both when the work ends. An interrupt surfaces as
`CommandInterrupted` — an `Exception`, so it cannot unwind the REPL loop that
awaits the handler; a cancellation of the loop itself still propagates as a
cancellation, even when it lands together with the interrupt
(`current_task().cancelling()`). `/agents run` is the one bundled user:
cancelling `runtime.spawn` kills the child exactly as a cancelled `agent` call
does, and the handler prints `Delegation to '<profile>' stopped — the agent was
ended before it finished.` — or, refined in review, `Delegation to '<profile>'
stopped before the agent started.` when no progress snapshot ever said a child
ran (`running`, `stopped` or `done`): an interrupt during the spawn-time consent
dialog ended no agent.

`run_cancellable` is a field of the BUILT-IN commands' `CommandContext`, and only
`run_tui` sets it (it is `None` elsewhere; RPC `commands/run` is cancellable as an
operation of its own). An EXTENSION command receives an `ExtensionCommandContext`,
which has no such door, and the dispatcher awaits its handler inside the input
loop with `running` unset — so a third-party command that awaits a delegation
cannot be interrupted by Esc, as `/agents run` could not before D15, and with no
time limit it holds the TUI until its delegation ends. Interruptible delegation
from an extension command means `run_cancellable` (or its like) reaching
`ExtensionCommandContext`; that is not part of this record.

While such a command runs, `running` no longer means "a turn runs", so the
shell's steer route sends a line typed then to the input queue as the next input
— what Enter did while a handler blocked the loop — instead of to `steer()`,
where it would sit orphaned with no turn to read it.

#### D16. SIGTERM / SIGHUP end what is running first

The TUI's handler queued EOF and exited the chrome, but the input loop reads EOF
only after its current await — `harness.prompt`, for as long as a delegation in
the turn runs. It now first aborts a running turn through `_safe_abort` (Esc's
path), or interrupts a running cancellable command through its own interrupt,
and then asks for EOF as before. No handler is installed on win32, as before.

### D6. The Protocol's `spawn(timeout_ms=None)` stays, deprecated and ignored

`SubagentRuntime.spawn` keeps the keyword so a caller that passes it keeps
working; removing it would be a shape change, which `CONTRACT_VERSION` exists to
avoid. The bundled runtime accepts it and forwards nothing. `CONTRACT_VERSION`
stays 1: every contract addition — `SubagentResult.note` (D4) and the seven
fields of D8 — is defaulted and appended.

### D7. No `timeout` outcome is produced; the literal stays

No bundled channel proposes `outcome="timeout"` any more. The literal stays in
`SubagentOutcome`, and so do `CUT_BY_THE_PARENT` (`child_session.py`), the
sanitizer's `timeout` branch and every renderer's handling: settle records on
disk already say `timeout` (ADR-0243), and a third-party channel may still
propose it.

---

> **The second half of #604 phase 0.** The sections below decide what a
> stopped or failed child gives back to the parent model. The numbering follows
> the design (`.omc/specs/604-design-2026-10-28.md`, rev 2); where the
> implementation refined a rule, the section says so and why.

Until this half, a child that did not finish came back as whatever its stderr
said — for a child the parent stopped, a real child's `Request aborted` — and
its partial answer, its tool calls, the files it wrote and its session were
nowhere in what the parent's model read: the trail rode only in chain mode and
RPC `asdict`, the session was reduced to a boolean, and the status came last,
in the footer. A child cancelled by Esc came back as nothing at all.

### D8. `SubagentResult` gains `started`, `reason`, the session and the changed files

Seven defaulted fields, appended after `note`:

| Field | What it holds |
|---|---|
| `started: bool = False` | a child PROCESS existed. `runtime._run` sets it after the channel returns, from the registry row's `proc` (or a channel's own `True`) |
| `reason: str \| None` | D10 |
| `session_id: str \| None` | the receipt's child session id, filled by `_run` right after `channel.run` — one site for both channels, both doors and every batch member |
| `session_path: str \| None` | the absolute path of that file, for hosts and UIs only — **never** in model-facing text (ADR-0243 §5) |
| `changed_files`, `unconfirmed_files: tuple[str, ...]` | the written files (below) |
| `files_overflow: int` | distinct written paths past the cap |

**Refined: `started` means a process existed, not "the channel was called".** A
stop that landed before the spawn and a spawn that failed inside the channel
both return a channel envelope with no process behind it; telling the model
such a child "already did the work below" is the falsehood D18 exists to
prevent.

**The written files are collected by the reducer, apart from the trail.** The
trail cannot give a path back (clipped to 120 characters, the first 200 calls,
a byte budget). For the writer tools — `edit`, `write`, and the `create_file` /
`write_file` spellings the guardrail already polices, **imported** from
`builtin/guardrail.py` rather than re-spelled — `tool_execution_start`'s
`path` / `file_path` is recorded with its `tool_call_id`:

- **keyed by the path as the child wrote it** (kept up to 4096 characters,
  Linux's `PATH_MAX`, a memory bound); refined in review — keyed by its cleaned
  spelling, a confirmed write to `a\nb.txt` and an unfinished one to `a b.txt`
  became ONE entry listed as changed, so the unfinished write vanished and the
  file named did not exist;
- **cleaned only for display** (`stream.path_for_display`): `_strip_control`
  collapses newlines (a path is rendered one per line, so a newline in one
  could forge entries) and the shown path is clipped at 512 characters with
  `…`. Two files whose names display alike stay two entries, each with its own
  status;
- at most 64 distinct paths; past that they are counted into `files_overflow`,
  once per path as far as a bounded memory of them reaches;
- an end event with `is_error=False` makes the path **changed**, no end event
  leaves it **unconfirmed**, `is_error=True` on every call excludes it, and
  **changed beats unconfirmed** for the same path.

`_run` then resolves each path the way the child's own tool did
(`tools/_path_utils.resolve_to_cwd`: `~` and a leading `@` expanded, relative
paths joined to the CHILD's directory) and shows it relative to the PARENT's,
`/`-separated, so `agent(..., cwd="packages/api")` editing `src/app.py` reports
`packages/api/src/app.py`. Refined in three places: the parent's directory is
**resolved first**, as `resolve_child_cwd` resolves the child's (compared raw,
a symlinked checkout turns `a.py` into `../../private/…/a.py`); a path outside
the parent's directory stays **as the child wrote it** when that was absolute
or `~` (expanding `~` would spell out a home directory the child never did); a
relative path that climbed out is shown from the parent (`../x`). Two spellings
of one file merge — on the file, the raw path resolved against the child's
directory, never on how it reads — and changed still wins after the merge.

Files changed through `bash` are invisible, and so are files an MCP or extension
tool wrote (only the writer tools are read); every file list says so in a line
of its own: `files changed through bash or other tools are not listed`.

### D9. On a failure or a stop, the partial answer comes before stderr

`_select_summary` on a run that is not `ok`: the partial answer
(`state.summary`) → the final error (D17) → `(no output)`. A success keeps
answer → stderr → `(no output)`. The diagnosis the summary passes over is not
lost: D17 resolves it into `error`, which both renderers print as its own note.
The stale docstring that called the stop-path stderr "usually empty" is replaced
(it was "Request aborted" since #220). The `no_terminator` gate keeps its
existing regression tests.

Refined in review: **the stderr is no summary rung of a failure.** As the third
rung it was shown as the child's partial result — a child with no API key read
`Partial result: No API key found…` over `Error: exited with code 1`, and a child
stopped before it wrote anything read `Partial result: Request aborted`. The
stderr is the run's diagnosis, so it is resolved into `error` (D17) and from
there into the reason, and the summary says the child wrote nothing. Hosts that
read `agents/run` see the stderr in `error`, where it always belonged, and
`(no output)` in `summary`.

### D10. `build_result` writes the reason

Only for a run that is not `ok`, one line:

| Case | `reason` |
|---|---|
| `aborted`, proposed by the caller (`row.stopped`: `stop`, `stop_all`) | `stopped by the session (stop, /new, /resume, /fork or quit)` |
| `aborted`, a cancelled turn (D14) | `the turn was cancelled` — passed in by `_run` through `build_result(reason=…)` |
| `aborted`, from the child's own stop reason | `the agent's own run was aborted` |
| lingering failure (D4) | `the agent closed its output but did not exit within 30 s` — moved from `error`, which now holds the run's own diagnosis |
| `error` (and a third party's `timeout`) | the first line of the caller's error when the channel gave one, else of the resolved error (D17) — of a Python traceback or exception group, its exception line — read on the cleaned text (D11), at most 300 characters |

Refined: a failure that left nothing to quote at all — no error, no stderr, a
clean exit code, a terminator — reads `its run failed without an error
message` rather than an empty heading.

Refined in review: **the cancel reason names nobody.** It read `the turn was
cancelled (Esc)`, but an RPC `abort`, an embedder's `harness.abort()` and the
TUI's SIGTERM/SIGHUP handler (D16) cancel the turn the same way, and `_run`
cannot tell them apart. And **a traceback's reason is its exception line**: a
child that crashed on an uncaught exception was reported as "did not finish:
Traceback (most recent call last):". The rule (`envelope._reason_line`): after
the LAST `Traceback (most recent call last):` line, the first unindented
non-blank line — so a chained exception names the one that ended the run and
log lines before the traceback are skipped — or, for a tail cut mid-traceback,
the last non-blank line.

Refined in review round 2: **the text is cleaned first** (D11's rule), so a
colourised traceback (`PYTHON_COLORS`, 3.13+) neither hides its header behind
an escape sequence nor gives a reason that begins `[1;35mValueError [0m`; and
**an exception group's header counts**. A failed `asyncio.TaskGroup` prints
`+ Exception Group Traceback (most recent call last):`, which was the reason.
A top-level group's header is now a header like the plain one, its lines are
read out of their `|` margin, and the reason is the group's own exception line
(`ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`): the
group is what ended the run, as the last exception of a chain is, and its
sub-exceptions stay in the error note. A traceback nested inside the group
carries the margin and is not a top-level header.

### D11. What the model reads for a started child that did not finish

`render_subagent_result` uses this template only when `started` and not `ok`:

```
The delegated agent did not finish: <reason>
Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls first.

Partial result:
<summary, or "(it wrote no text before it stopped — see its tool calls)"; without a tool call, "(it wrote no text before it stopped)">

Error: <resolved error, at most 4 KiB; left out when the summary already says it, or when it is one line that is the reason>

Tool calls (<n>):
<the trail, 4 KiB, "… N more">

Files changed:
  a/b.py
Unconfirmed (stopped mid-write):
  d.py
(+N more files; files changed through bash or other tools are not listed)

Session: <session id>

<the dropped-tools and dropped-lines notes, as before>

[agent … footer]
```

- The file lists share 2 KiB, labels included; a path that does not fit is
  counted into `+N more` with the overflow. With tool calls but no file, the
  block reads `Files changed: none recorded (files changed through bash or other
  tools are not listed)`.
- The session's **id** only. Its path stays out of the model's context, the
  ADR-0243 §5 rule, which this record keeps rather than amends.
- Refined: **the retry line needs work to point at** — a tool call or a written
  file. A child that failed on its first request (no API key, a bad model) did
  nothing, and "it already did the work" would be as false of it as of a
  refusal (critique 5).
- Refined: when the summary **is** the final error (the fallback chain's second
  rung, the whole error or its cut), the partial result reads `(it wrote no
  text…)` and the error is printed as the error. A partial answer that merely
  quotes its error is still the child's text and is shown as written.
- Refined in review: **the `Error:` block is left out when it is one line that
  IS the reason** (`envelope.error_says_only_the_reason`). A one-line provider
  400 was the heading's reason and the note under it, the same line twice on the
  card. An error with more to say — more lines, or longer than the reason's
  300-character cap — keeps its block.
- Refined in review: **every child-authored part is cleaned**
  (`envelope.child_prose` / `child_line`). In order: every WHOLE terminal
  escape sequence is removed — CSI (`ESC [` or the C1 CSI, up to its final
  byte), OSC and the other string sequences up to their BEL or ST on the same
  line, and the remaining `ESC` forms with one final byte (`ESC 7`,
  `ESC ( B`); then CR and CRLF become a newline; then the shared `aelix_ai`
  terminal strip — the one the batch panel and the `/agents run` panel use —
  deletes what is left of C0 (newline and tab kept), DEL, C1, the BiDi
  controls and the line and paragraph separators; then every Unicode format
  character (category `Cf`) goes. Refined in review round 2: deleting only the
  ESC byte left `[2J` and `[35m` behind as text, and a hand-picked set of
  invisible characters let a word joiner, zero-width joiner, soft hyphen or
  LRM sit inside a forged header unseen. The zero-width joiner goes too, so an
  emoji sequence falls apart into its parts, and so do a Persian zero-width
  non-joiner, an Indic half-form's joiner, a flag's tag sequence and the
  visible format characters (U+0600, U+06DD) — the accepted cost on these
  failure paths (the panels and every success keep them). A sequence that
  does not complete is not matched whole: its ESC goes, with the byte after it
  when the two read as an escape (`\x1b[31` leaves `31`); an ESC before a space
  goes alone, and one directly before a letter takes that letter, as a
  terminal would. The reason,
  the session id and each written path are folded onto one line; the partial
  answer, the error, the note and the trail keep their lines. Which blocks
  appear is decided on the values as they came, with two exceptions taken on
  what the model reads (review round 2): a partial answer with nothing left
  once cleaned — only control or format characters, or blank lines — is no
  text (`envelope.says_nothing`; it rendered as an empty `Partial result:`) —
  even when the answer was cut and its kept part is what cleaned to nothing,
  so the truncation marker is not shown then (the session id still is),
  and the error is compared with the reason as both are shown. A success and
  a result that never started are not cleaned: D18 keeps them byte for byte.
- The retry sentence is phase 0's; #610 replaces it with resume guidance.

The whole stays bounded: the summary at `output_cap`, the note at 4 KiB, the
trail at 4 KiB, the files at 2 KiB, plus the fixed lines.

### D12. A failed member of a parallel call

`aggregate._member_block` changes only for a member whose child started and did
not finish. Its share of `BATCH_OUTPUT_BUDGET_BYTES` is split so no part can
starve the others (critique 9):

| Part | Share |
|---|---|
| reason + session id (the heading `[i/N status] did not finish: <reason> · session <id>`) | 300 B, the reason cut for the id |
| `Error:` note | a quarter (it was half) |
| the last five tool calls | an eighth |
| the written files | an eighth |
| the partial answer | the rest |

Each part that does not fit is cut with its count kept. When the trail itself was
cut at its byte cap the entries it kept are the FIRST calls, so the label then
says `(N, k shown)` instead of `(N, last k)`. Refined in review: the block is
cleaned like D11's — each part before it is cut to its share, so a share is not
spent on bytes that are then deleted — its blocks are decided like D11's (a
partial answer with nothing left once cleaned is no text, review round 2), and
its `Error:` line is left out when it
is one line that is the reason the heading shows WHOLE (a reason the heading cut
for the session id keeps it). The frame — the tag, the labels,
the markers the caps append, the usage line — stays outside the share, as it was;
eight members at every cap at once measure inside 64 KiB plus that frame. A
member that never started, and a success, render as before. There is no retry
line in a member block: the design keeps it to the facts. A chain's `{previous}`
is untouched and needed no change — the chain stops at the first step that is not
`ok`, so a failure template can never reach it (critique 12; said in
`batch._run_chain`).

### D13. A sentence still streaming when the child stopped is lost

`state.summary` is set at `message_end` only, so text the child was streaming
when it was stopped or killed is not in the partial answer. Phase 0 accepts this
(Limits); the child's session file holds what its harness committed.

### D14. A delegation cancelled by Esc is reported on the next turn

A cancel propagates through the `agent` tool with no `ToolResult`; on the next
turn the provider transform answers the orphaned call with "No result provided"
(critique 1). Now:

1. The extension passes a collector, `SpawnRecordMeta.envelopes` (a
   `runtime.CallEnvelopes`, keyed by member index; excluded from the record's
   equality). `_run` leaves every envelope there — the channel's when it came
   back (refined: so a batch report includes the members that finished before
   the cancel), and, in its `finally` on a cancel, one built from the live stream
   with `build_result(outcome="aborted", reason="the turn was cancelled")`, after
   the partial spend is priced and before the shielded settle append. **No
   `await`** on this path: it runs in a task being cancelled. Nothing is left for
   a spawn with no process.
2. `_execute`'s `except asyncio.CancelledError` renders what the collector holds —
   a single call's D11 text, or a batch's D12 blocks under their submitted
   positions with a line naming the positions that had no child — under
   `[aelix system report — not user input] The \`agent\` call you made was
   stopped when the turn was cancelled. What it had done:` and queues it with
   `api.send_message(text, trigger_turn=False)`, synchronously, then re-raises.
   A report that cannot be built or sent costs the report and never the cancel —
   the case that needs this is a session switched under the turn: the runner
   invalidates the extension's runtime before `dispose()` aborts the turn, so
   `send_message` raises `ExtensionError`.
3. Nothing is queued when no child had a process.

Refined in review round 2 — **a member that ran and then failed is not "never
started".** A member whose run had a process and then ended on an exception
other than the cancel (a channel that raised; the batch turns it into a
`delegation failed to start: …` envelope of its own) leaves no envelope in
the collector, and the report listed it among the positions that "had no agent
running … not started, or not reached" — false. `_run` now tells the collector,
as it drops the member's builder, whether a process existed, and the collector
keeps such a member (`CallEnvelopes.lost`); the report names it in a line of
its own after the fence — `Task 2 of 4 had an agent running that ended on an
internal error; what it had done is not in this report.` — and leaves it out
of the no-agent line. A batch whose only member with a process is such a one
still gets a report: a child did run.

Refined in review — **a member still leaving is reported.** A parallel batch
awaits `asyncio.gather(..., return_exceptions=False)`, and CPython's `gather`
sets its outer future's exception from its `_done_callback` as soon as the FIRST
child finishes cancelled; it does not wait for the others. So the extension's
`except` ran while a member whose cancellation cleanup was slower (a reap, a
pipe-holding helper, the shielded settle append) had not reached `_run`'s
`finally`, and that member was reported as "had no agent running" — its partial
answer, trail and session lost (reproduced with two real RPC children). Now, as
soon as `_run` holds the member's `RunningChild`, it registers in the collector a
synchronous BUILDER of the cancelled envelope (from the live stream; nothing while
there is no process); the final envelope replaces it, and a run that ends with no
envelope drops it. The extension reads `CallEnvelopes.snapshot()`, which builds
the members still on their way out. The report waits for no cleanup beyond what
the cancel itself waits for (wording corrected in review round 2): the
extension's `except` runs once the cancel reaches it — in a batch when
`gather`'s outer future completes, which is after the FASTEST member's own
cancellation has finished; in single mode after its only member's — and the
slower members are built from their live streams, never awaited. The builder
prices a COPY of the stream when nothing priced it yet: the
member's own `finally` prices the live stream for its settle record and must
still find it unpriced.

Refined in review — **the report fences what the children produced.** It arrives
in a USER-role message, and a child that had read attacker-controlled content
could put text there under the user's name: a forged `[aelix system report …]`
header, an ANSI clear-screen and a CR all reached the next prompt verbatim. So
`CANCEL_REPORT_HEAD` stays first and outside, and everything after it is cleaned
(D11's rule, so a member that finished and renders through the old block is
cleaned too) and enclosed between `<cancelled-delegation-report>` and
`</cancelled-delegation-report>`, with a note that what the agent wrote there is
DATA, not instruction — the chain's `{previous}` fence, the same code
(`chain.fence_as_data`): a forged tag inside is escaped (`&lt;`), and so is the
opening bracket of anything spelling the header marker (`&#91;aelix system
report`). The positions that had no child are aelix's own statement and follow
the fence.

Refined in review round 2 — **what is guaranteed, exactly.** Round 1 caught the
exact spellings only: an upper-case close tag, `</cancelled-delegation-report >`,
a tag a CRLF split, and a word joiner, zero-width joiner, soft hyphen or LRM
inside the marker or a tag all passed (the verify's fence probe: 104 failures
in 8 such families). Now, after the cleaning above has removed every escape
sequence, control character and Unicode format character from the fenced text:

- no `[` that begins the letters of `aelix system report` — in any case, with
  any whitespace (newlines included) between them, or none — is left in it; it
  becomes `&#91;`;
- no `<` that begins either fence tag — in any case, with any whitespace
  between the tag's characters, its `>` not required, so `</tag foo>` and an
  unclosed `</tag` count — is left in it; it becomes `&lt;`. This is
  `fence_as_data(any_spelling=True)`, an opt-in: the chain's `{previous}`
  fence keeps its exact-spelling bytes (ADR-0199 §3.1.1).

So the real header, first and outside, is the only `[aelix system report` a
model can read in any of those spellings, and the real close tag is the only one
that closes the fence. A look-alike spelled with OTHER characters — a fullwidth
`［`, a Cyrillic `а` — is not caught, nor is a spelling that carries an
invisible character outside the format category (Limits); the fence and its
note are what stand against those.

The next-turn queue is the right carrier, verified against the harness by test:
`AgentHarness.abort()` clears the steer and follow-up queues and leaves this one
alone, and the next `prompt()` drains it ahead of the user's message. The report
arrives as a user-role message, which is why its first words say the user did
not write it.

### D17. The final error, and the resolved error

- **`final_error`**: the reducer's new field holds the error of the LAST
  assistant `message_end`, only when its stop reason is `error`, and is reset on
  every assistant message. A 429 the harness's retry recovered from stays in
  `error_message` (last non-empty wins) and is no longer anyone's reason or note
  (critique 4); on a failure it is kept in `details` with the stream's other
  evidence.
- **The resolved error** of a run that is not `ok`, first that has a value:
  the caller's `error` → the final error → the sanitized stderr (the summary of a
  failure is never the stderr since review round 1, D9, so nothing prints it
  twice) → the exit status in words (`exited with code N`, or `killed by signal
  SIGKILL` for a negative code, from a fixed table that reads the same on every
  platform) → `STREAM_ENDED_EARLY` when the terminator is missing.
  Refined: an `aborted` run stops after the final error — its reason says who
  stopped it, its stderr is the shutdown of a process the parent killed and its
  exit code is the parent's own signal — and a lingering run skips the exit
  status, which is the reaper's (D4).
- Refined in review round 2: **a channel's own error keeps the child's
  stderr.** The caller's `error` is about the CHANNEL — the print channel's
  last line of defence reports the exception that broke it (`OverflowError:
  …`), RpcChannel a stdin that closed (`RPC server closed stdin: …`) — and
  since the stderr is no summary of a failure (D9), a child's own diagnosis
  (`No API key found…`) reached only `details`, which the model never reads.
  On a run that failed without being aborted, the sanitized stderr now follows
  the caller's error under a `stderr:` line — `<caller error>\n\nstderr:\n<tail>`
  — when it says something that error does not already contain
  (`RpcServerExited` already quotes it as `Stderr: …`). The reason stays the
  caller's error's line, so a traceback in the appended stderr cannot take it
  over; the note's 4 KiB cap cuts the stderr first, and a caller error that
  fills the cap alone leaves no room for it. A
  blank caller error (an exception with no message) gives way to the stderr.
  An aborted run keeps the rule above: its stderr is the parent's own kill
  noise. A spawn that failed has no stderr, so its error, and its rendering
  (D18), are as before.
- The single-mode `Error:` note is capped at 4 KiB (`MAX_ERROR_NOTE_BYTES`): a
  dead RPC child's `RpcServerExited` carries up to 64 KiB of stderr. The cap
  applies to the note on every result; below it, which is every refusal and
  decline, nothing changes.

### D18. A child that never started renders as it does today

`started == False` — a refusal (a draining session, the live-child cap, the
per-prompt budget), a decline, a stop before the spawn, a spawn that failed, a
batch member that never got a process — renders byte for byte as before, pinned
against a copy of the previous renderer. So does every success, including one
whose session, trail and files are all filled (critique 15).

---

## Consequences

- A delegation — single, parallel or chain, from the model or from
  `/agents run` — runs until the child finishes. Esc, Ctrl+C, `stop`, a session
  teardown or a signal ends it.
- An existing call or profile that sets `timeout_ms` keeps working and does
  nothing; a profile says so once at parse time (`/agents list`) and once per
  file at spawn time (the log).
- A child that finishes and then will not exit costs 30 s and comes back `ok`
  with a note; one that closes its pipes before finishing is an `error` that says
  why.
- A child that is stopped, cancelled or fails after it started tells the parent's
  model, first, why it did not finish, then its partial answer, its error, its
  tool calls, the files it wrote and its session id — and after Esc, on the next
  turn. A success and a child that never ran read exactly as before.
- Hosts see eight new defaulted fields in RPC `agents/run` (`note`, `started`,
  `reason`, `session_id`, `session_path`, `changed_files`, `unconfirmed_files`,
  `files_overflow`); `agents/show` keeps `timeout_ms`. A failure whose only
  diagnosis is stderr (no API key, a crash before the first line) now carries it
  in `error` and `reason`, with `(no output)` as its `summary` (D9); a failure
  the channel reports with an error of its own carries the stderr after that
  error (D17).

## Limits and unverified risks

- **`-p` has no one at the keyboard.** A child that never finishes holds a `-p`
  run until SIGINT or SIGTERM. That is the decision, not an oversight.
- **The batch semaphore is per event loop** (`batch._SEM_BY_LOOP`). A host that
  runs several delegating sessions on one loop shares four permits between them,
  so one session's long batch can starve another's without limit, where the wait
  was at most 30 minutes before. Checked in review (design critique 17):
  `aelix-server` is not such a host — its WebSocket endpoint serves one
  connection at a time (a second is closed with 1013) and builds a bare
  `AgentHarness` that loads no extensions (`rpc_ws.py`), so no `agent` tool exists
  there. A third-party host that runs several delegating sessions on one loop
  would share the permits.
- **RPC `agents/run` holds the mutation lock** for the delegation's whole life
  (D5).
- **An extension command that delegates cannot be interrupted** (D15). Extension
  commands get an `ExtensionCommandContext`, which has no `run_cancellable`, and
  are awaited in the input loop; with no time limit such a command holds the TUI
  until its delegation ends.
- **Files changed through `bash` or other tools are not listed** (D8). Nothing in
  a shell command's arguments says what it wrote, and only the writer tools are
  read; every file list states the limit.
- **A sentence still streaming when the child stopped is lost** (D13).
- **The Esc report lives in memory** (D14). A session closed and reopened with
  `--continue`, replaced by `/reload`, or switched away from (`/new`, `/resume`,
  `/fork`) before another prompt does not carry it; the settle records the
  runtime wrote for each child are in the session file either way (ADR-0243),
  and #610's resume reads those. `-p` has no next turn, so it gets no report.
  Once delivered it is part of the conversation: a reopened session shows it as
  a user message. The TUI does not draw the report when it is queued; a copy the
  user sees is #132's (the delegation panel).
- **The RPC channel has gaps of its own**, found in review, all pre-existing or
  RpcChannel-only. No product door selects `RpcChannel` (the bundled runtime
  uses `PrintChannel` everywhere); #123, which puts it into production, owns
  them:
  - a cancel during `RpcClient.start()`'s startup grace leaves a live process no
    row tracks (the row's `proc` is set only after `start()` returns), and a child
    that exits inside that grace reports `started=False` with the work it did;
  - the final reap can wait forever when a `setsid` helper holds the pipes after
    the root died (macOS has no descendant walk to find it);
  - a blocked stdin write is outside the 180 s command bound (D4 — the bound
    covers waiting for the answer, not a write that cannot drain);
  - the channel has no counterpart of D4's post-EOF exit bound.

  Recorded here in round 1 and closed in round 2: a child that died while its
  task was being written reported `RPC server closed stdin: …`, which names no
  stderr, and the stderr stayed in `details`. The verify found the same on the
  print channel's last line of defence. Both now carry the stderr after their
  own error (D17).
- **A forgery spelled with look-alike or non-format invisible characters is not
  neutralised** (D14). The marker and the tags are caught in any case and
  spacing once control and format characters are gone, not in other
  characters that read alike — a fullwidth `［`, a Cyrillic `а` — and not when
  an invisible character outside the format category sits inside them: the
  combining grapheme joiner U+034F, the variation selectors (U+FE00–FE0F,
  U+E0100–E01EF), U+180B–180F, U+17B4–17B5 and the Hangul fillers (U+115F,
  U+1160, U+3164, U+FFA0) — the round-2 delta verify's probe, 260 of 260 cases.
  Removing every Default_Ignorable_Code_Point instead of only Cf would close
  the second gap; it is left for a follow-up. The fence and its note are what
  stand against both.
- **A delegated child's `bash` grandchild can outlive a stop on macOS.** The bash
  tool runs its command in a session of its own, which the child's process-group
  kill does not reach, and macOS has no descendant walk to find it. Measured with
  Ctrl+C under `-p` (Esc in the TUI cancels through the same tree kill), and the
  same at `7e76f925` (ADR-0238's `setsid` asymmetry) — not a #604 regression.
- **What a cancelled envelope cannot see** (D14): the channel's stderr ring, the
  line assembler's dropped-line count and the exit code (the kill is still in
  flight). Its error is therefore only the stream's own final error.

## Verification

Lane-A tests: the ignored tool argument
(`test_a_call_carrying_timeout_ms_runs_and_the_value_is_ignored`), the profile
warning (`test_profile_parse.py`, RPC `agents/show`, the once-per-profile spawn
log), the main wait's armed `timeout=None` under a clock that leaps past the old
default, the lingering verdicts on a real wedged child and in `build_result`, the
RPC turn under a shrunk idle default and the command bound, the batch with no
clock, and D15/D16 through the real `run_tui` with a real child
(`tests/tui/test_interrupt_reaches_long_work_604.py`). Each new test was
mutation-checked (13 groups, 21 reverts, each red; recorded in
`.omc/specs/604-progress-2026-10-28.md`).

Lane-B tests (`tests/agents_ext/test_stopped_child_reports_604.py`, and the
rewritten rung-order, lingering and final-error pins in `test_envelope.py`):
the reducer's file collection (changed / unconfirmed / excluded, changed
winning, the 64-path cap counted once per path, newline and control
characters collapsed, the 512-character clip) and its final error; paths
from a child in another directory, under a symlinked parent and outside the
parent; the reason table, the 300-character cap and the resolved-error chain
(a recovered 429 under a later SIGKILL, a stderr-only traceback beside a
partial answer, a stop that adds no error); the D11 template byte for byte,
the session path kept out, the retry line gated on work, the 4 KiB note and
the 2 KiB file lists; a REAL child stopped by `stop_all` mid-edit through the
real print channel and a real parent session; D18 against a copy of the
previous renderer for a success carrying every new field, the refusals, a
decline and a spawn that failed inside the channel; D12 with eight members at
every cap at once; and D14 through a real `AgentHarness` and the real
extension — Esc during a single call and during a batch queues the report,
`abort()` leaves it queued, the next `prompt()` puts it ahead of the user's
message, and nothing is queued when no child had started. Each was
mutation-checked (35 reverts, each red; recorded in
`.omc/specs/604-progress-2026-10-28.md`).

Live, in the real TUI under a pty, with no model spend (a scratch provider
whose endpoint accepts and never answers, so `/agents run` waits forever on
it; `.omc/specs/604-progress-2026-10-28.md`, L6): Esc, Ctrl+C and SIGTERM each ended
a running `/agents run` — the child gone 1.18 s, 0.41 s and 0.12 s later, the
stopped line printed, the prompt back, exit 0 — and `/agents list` showed the
`timeout_ms` warning for that profile. SIGTERM with a turn parked on the same
provider exited in 0.16 s; with D16's abort reverted the same run was still
alive 30 s later.

L2, live in the real TUI with no model spend (a local OpenAI-compatible endpoint
plays both models; `.omc/specs/604-progress-2026-10-28.md`, L2): the parent's model
delegated, a real child wrote `notes.md` and then waited on its provider, and
Esc ended it — the child gone 1.1 s later, no survivor. The next prompt's
provider request carried the report as its own user message ahead of the
prompt, after the transform's "No result provided": the header, `the turn was
cancelled (Esc)` (the reason before review round 1 made it neutral),
`write(notes.md) ok`, `Files changed: notes.md`, the session id, and no scratch
path. The TUI itself showed `Operation aborted` and does not draw the queued
report. (Measured before the report was fenced; the round-1 tests below pin the
fence.)

L4, the same way: a child that wrote `notes.md` and then got a non-retryable 400
came back as a D11 card in `uv run aelix` at 120 columns with every section,
the reason first and the session id without its path (the implementation
pass's text capture, not in the progress file; long lines are clipped by the
card renderer as before).

Not run by the implementation passes: L1 (a real model past the old limits),
L3 (`-p` SIGINT), L4 at 80 columns, L7 with a real delegation inside the turn,
and any visual judgement of the TUI. The lead then ran L1, L3, L4 and L7
(`.omc/specs/604-progress-2026-10-28.md`, the lead's live checks): L1 — a real
model (OpenRouter `anthropic/claude-haiku-4.5`) under a profile with
`timeout_ms: 60000` delegated a child that ran `sleep 40` three times; it ran
129.2 s and came back `ok` ($0.0084), where the old code would have killed it
at 60 s. L3 — a process-group SIGINT during a `-p` delegation on a real model
exited 130 in 0.26 s with the child cleaned up; the child's `bash` grandchild
survived, as it does at `7e76f925` (Limits). L4 at 80 columns — the card's
lines in order and no session path; its reason line and its `Error:` line
repeated one one-line error, which review round 1 fixed. L7 — SIGTERM to the
TUI during a turn running a delegation exited 0 in 0.16 s with the child
cleaned up and no survivor.

Review round 1 (`tests/agents_ext/test_stopped_child_review_604.py`, with the
written-file and wording pins in `test_stopped_child_reports_604.py`, the
stderr-as-error pins in `test_envelope.py` and `test_print_channel_spawn.py`,
and the TUI pins in `test_agents_run_command.py` and
`test_interrupt_reaches_long_work_604.py`): the reviewers' reproductions, ported
— a hostile summary and error (ANSI, CR, a forged header and `Session:` line, a
forged close tag) come out of the report cleaned and fenced, with exactly one
header, single and batch; the same through the real harness, where the report
is a user-role message on the next prompt; two files that display alike stay
two entries; a batch member whose cleanup outlasts the first member's is in the
report while it is still leaving (a fake channel everywhere, and two real RPC
children with a pipe-holding helper on macOS); a session switched under a
delegation still ends the turn as a cancel, with no tool result and nothing
queued; a teardown cancel that meets an interrupt still cancels the input loop;
the stderr-only and traceback reasons, the duplicate `Error:` block in D11 and
D12, the neutral cancel reason, the `/agents run` line for an interrupt before
any child ran, and each cap at its literal value. 42 mutants, each red on 3.12,
6 of them run again on 3.11 — 48 runs in all — and the lead's 2 more for the
no-text line (recorded in `.omc/specs/604-progress-2026-10-28.md`).

Review round 2 (`tests/agents_ext/test_stopped_child_review2_604.py`; three
round-1 tests now expect no escape residue): one representative of each of the
eight families the verify's fence probe found — an upper-case or spaced close
tag, a tag a CRLF split, a zero-width joiner inside a tag, and a word joiner,
zero-width joiner, soft hyphen or LRM inside the marker — in every
child-authored field, single and batch, with one header and one fence; the
verify's probe itself, re-run, with no failure; whole escape sequences and
every format character removed, and the word after a stray ESC and a space
kept; the
chain fence's bytes unchanged; a finished member cleaned before its marker is
neutralised; a channel's own error with the stderr after it, the reason kept
and the stderr cut first by the note cap; the reason of an exception group
generated from a real `asyncio.TaskGroup` failure on 3.11, 3.12 and 3.13,
chained either way, and of a colourised traceback; a partial answer with
nothing left once cleaned, single and batch; the written path's 4096-character
key; and a member that ran and ended on an internal error, through the real
harness and extension. Successes, never-started results, batches of them, the
chain fence and its rendered steps were rendered from a seeded corpus before and
after the round: byte for byte the same; only the `reason` of a spawn that
failed moved, now read on the cleaned text — no renderer shows it for a child
that never started, and RPC hosts read it. Each fix's tests were
mutation-checked (recorded in `.omc/specs/604-progress-2026-10-28.md`).

Live for round 2, in the real TUI under a pty with no model spend (the L2
driver, its child's first answer made hostile — an upper-case close tag, a
close tag a CRLF split, a word joiner, zero-width joiner, soft hyphen and LRM
inside forged markers, and an ANSI colour; recorded in the progress file): Esc
ended the child with no survivor, and the next prompt's provider request
carried the report with one header, one open and one close tag, no control or
format character and no escape residue, every forgery left visible behind its
`&lt;` or `&#91;`.
