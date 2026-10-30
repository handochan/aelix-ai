# 0277. A delegated agent can be resumed on its own session, inside the limits it ran with

Status: Accepted (2026-10-30, #610). All of D1–D16 are decided and
implemented. Review round 1 (2026-10-30: the ceiling is read off the first
run's start record, and a key whose records disagree about it is refused; the
target is looked up again at execute time and must be the same one; the child
file must have one name, no link below the parent's directory, a header the
session loader would open, and a recorded cwd that is still itself; a NUL in
the instruction and a channel that cannot resume are refused before admission;
the Esc report says to resume only if the user asks, and the resume guidance
is conditional; a resume's disclosure row says "resuming", and its dialog
names the first run's grant). Review round 2 (2026-10-30: the child file is
derived as the first run placed it, so a link at or above the parent's
directory resumes again; tests catch the five mutants that had survived on the
forged-record paths; `RESUME_NOT_YOURS` gives a next step). A last check
(review round 3) found no blocker or major; the listing's order and status
word are now held to the latest run by a test, and limits 17 and 18 record
the rest.
Date: 2026-10-30
Issue: #610 (phase 1 of the #604 delegation track). Design:
`.omc/specs/610-design-2026-10-30.md` (rev 2), with its critique
(`610-design-critique-2026-10-30.md`), its code map
(`610-surface-map-2026-10-30.md`) and the implementation lanes' notes
(`610-lane-a-notes.md`, `610-lane-b-notes.md`) beside it. Review rounds 1 and
2, their fix lists (F1–F15 and G1–G4, the `#610 Fn` and `Gn` references in the
code) and the measurements the lanes reported are in
`610-progress-2026-10-30.md`. The reviews' and the live checks' own records
are beside it (`610-review-r1-codex-2026-10-30.md`,
`610-review-r1-verify-2026-10-30.md`, `610-review-r2-verify-2026-10-30.md`,
`610-live-r1-report-2026-10-30.md`, `610-live-r2-report-2026-10-30.md`), and so
are the fix lanes' notes (`610-fix-r1-code-notes.md`,
`610-fix-r1-docs-notes.md`, `610-fix-r2-code-notes.md`,
`610-fix-r2-docs-notes.md`).
Amends: ADR-0243 (§3's record table gains `limits` and `resumes`, and the
resume reader takes neither `rel` nor `path` as a location, nor a first run's
last line for what that run was; §8, a child file is now resumable by the
parent session that delegated it), ADR-0276 (D11's last bullet, the retry
sentence; D14, the cancel report's head; D18, a success's bytes). Each carries
a dated `Amended 2026-10-30 (ADR-0277, #610)` note.
Relates: #604 / ADR-0276 (phase 0: the stopped child's report, and the session
id a resume takes), #611 (background delegation and `wait`/`status`/`stop`/
`send`; its `status` is where D7's listing goes next), #612 (resume through RPC,
ACP, `/agents run` and the `SubagentRuntime` Protocol), #296 (a viewer for
child sessions).

## Context

ADR-0276 made a stopped or failed child report what it did, its session id
included, and told the parent's model not to delegate the same task again from
scratch. It gave the model no way to go on instead: a new delegation started
from nothing, and the child's own conversation — its file (ADR-0243) — could
only be opened by a human. The owner decided on 2026-10-28 (the #604 record in
`docs/05-post-beta-direction.md`) that stopped children are recorded so they can
be resumed, and that delegation stays one `agent` tool with an `action`.

What stood in the way:

- **Nothing that bounded a child was recorded.** `PrintChannel.run` computed the
  child's tools, context files and thinking level from the parent's live state
  and put them on the argv, and the model flags were read from the parent's live
  model and registry when the argv was built — after the receipt had already
  named a model. ADR-0243 §8 called a child file "a record, not a resumable
  identity" for exactly this reason.
- **No product code read the receipts.** ADR-0243 wrote them for the stats fold.
- **The parent's session file is all that stands between a record and a child's
  argv.** The design's first revision stored the model flags as argv. The
  critique (B2) put forged ones where that list would sit and parsed the child's
  command line: `-e /tmp/evil.py` (an explicit `-e` still loads under
  `--no-extensions`), a second `--session` (the last one wins) and `--api-key`
  were all taken as the child's own flags.
- **The reference survey** (`.omc/specs/604-reference-survey-2026-10-28.md` §2
  "1단계 — resume", §5 tests 5–7): every project that rebuilt a resumed agent
  from the caller's settings got it wrong (Codex #26718 and #36433 switched the
  model and the reasoning, OpenHands rebuilt it from the caller's
  `subagent_type`, OpenCode switched the agent on continue); limits come back
  from the record, intersected with the parent's authority now; a resume that
  cannot run says why; and a tool call cut mid-run is an error, never
  "cancelled + success" (deepagents #5546).

pi, the reference (ADR-0235), has nothing to follow on this exact surface. Its
coding-agent subagent example (`origin/main` `84576c70c`) still runs the child
with `--mode json -p --no-session`, so there is no child conversation to go back
to. Its `packages/durable` example 22, which the survey read for the background
phase (#611), keeps each subagent as a conversation of its own and continues it
with `send`, addressed by a name the main agent chose. Here the child is a
separate process whose bounds were argv, which is why they have to be recorded
first.

## Decision

The numbering is the design's (rev 2), so the `#610 Dn` references in the code
resolve here. Where the implementation changed a point, the section says
"Refined" and why; where review round 1 changed it again, "Refined in review
round 1", with the fix (`Fn`) and the finding it answers (Codex review n, the
verify's Mn or mn, a live check); where review round 2 did, "Refined in review
round 2", with the fix (`Gn`) and the round-2 verify's mn.

### D1. `action` and `id` on the one `agent` tool

- `tool.AgentAction` is `Literal["run", "resume"]`, and `tool._ACTIONS` is its
  one spelling, read by both the schema's `enum` and the parser (`_MODES`' rule).
- The schema gains `action` (`enum ["run", "resume"]`, `default "run"`) and `id`
  (a string). `required` stays `["profile"]`: a resume names its profile too, and
  the hook only COMPARES it with the profile the agent ran as. The agent is never
  rebuilt from the caller's value (the OpenHands defect, the other way round),
  and a wrong id meets a mismatched profile before it meets a dialog.
- `parse_agent_call` reads `action` first. Absent or `null` is `run`. `""` and
  every value outside `_ACTIONS` are refused (`action='x' is not an action. Use
  one of: run, resume.`): before #610 an unknown key was delivered untouched and
  ignored, so a model that asked to resume got a fresh delegation and believed
  it had resumed.
- **`run`** parses every call without an `id` exactly as before. A `run` whose
  `id` is not absent is refused (`'id' is only for action='resume'. …`). Absent
  is `_absent`'s rule — `null`, `""` and `[]` — because a provider that fills in
  every declared property sends `id: ""` on every plain delegation.
- **`resume`** (`_parse_resume`) needs `profile` and `task`, and refuses every
  key that would make it another run: `tasks` (when not absent), a `mode` other
  than `null` or `"single"`, any `cwd` that is not `null`, and a truthy
  `background`. Refined: `cwd: ""` is refused too, as a `run` refuses it. The
  `id` must be a string. A missing or blank id is NOT refused here: the hook
  answers it with the agents that can be resumed (D7), which a parser refusal
  cannot carry. A path-shaped id is refused — one holding `/` or `\`, or a
  `.jsonl` name in any case — because no model text carries a session path
  (ADR-0243 §5). Refined: the design's separate absolute-path test is gone,
  since every absolute path holds a separator.
- The instruction is held to `tool.RESUME_TASK_MAX_BYTES` — `MAX_TASK_BYTES`
  (65 536) less `runtime.RESUME_PREAMBLE_RESERVE` (1 024), so 64 512 UTF-8 bytes
  — and the refusal states that number (critique m7: a model told "64 KiB" about
  an instruction the hook then refused would have nothing to act on). Refined:
  an instruction holding a lone surrogate is refused here (`'task' holds half of
  a character …`). It is a legal `str` that a call's JSON can carry, and it
  cannot be encoded into the child's argv, so it would otherwise fail at
  `create_subprocess_exec` — after the budget and the records. Refined in
  review round 1 (F7, Codex review 9): so is an instruction holding a NUL,
  U+0000 (`'task' holds a NUL character (U+0000), which cannot be sent to the
  agent. Resend the instruction without it.`). UTF-8 encodes it, but no argv
  can carry it: `create_subprocess_exec` raised `embedded null byte` after one
  delegation of budget and four records had been spent.
- `AgentCall` gains `action="run"` and `id=None`. A resume is
  `tasks=(instruction,)`, `mode="single"`, `cwd=None`, so every reader of the
  single shape reads it correctly. `PendingSpawn` gains `resume`, the target the
  hook found and checked (D12).

### D2. The id is the child's session id, and a success shows it

The model already saw `Session: <child session id>` on a stopped child
(ADR-0276 D11). That id is the child file's header id, and it does not change
however often the child is resumed. The spawn key (`sub-…`) is new for every run
and is never shown.

- A single **success** with a session now reads `<summary>`, its notes, then
  `Session: <id>`, then the `[agent …]` footer (`render_subagent_result`).
- An **ok member** of a parallel or chain call gets `Session: <id>` on a line of
  its own above its usage line, outside its byte share (`aggregate._member_block`;
  critique m2: the member's first line is `[i/N ok] <body>`, and an id appended
  to it would read as part of the child's text). A failed member is unchanged:
  its heading carries `· session <id>` (ADR-0276 D12).
- A result with no session (a parent with no file, a child file that could not
  be allocated) gains nothing and renders byte for byte as before. The path
  stays out of the model's text (ADR-0243 §5).

This amends ADR-0276 D18 by that one line.

### D3. The description

`_DESCRIPTION_HEAD` loses "There is no conversation:" — false once an agent can
be resumed into the conversation it had — and gains a last paragraph:

> All of that is action="run", the default. action="resume" continues ONE agent
> that finished or was stopped: pass the 'id' its result shows (on its
> "Session:" line, or after "session" in a batch member's "did not finish"
> heading), the 'profile' it ran as, and its next instruction as 'task'. It goes
> on in its own conversation, on the same profile and model, and its limits never
> widen. Resume it rather than delegating the same work again from scratch.

The head is 1 598 of its 2 048 bytes (it was 1 170). The `task` property adds
"With action=resume it is instead the NEXT instruction for that agent, which
keeps its own conversation."; `id` says it is "the id its result shows, on its
"Session:" line or after "session" in a batch member's "did not finish"
heading. Not a file path."

Refined in review round 1 (F12, verify m5): the paragraph and `id` said only
"the id on the Session line of its result", and a batch member that did not
finish has no such line — its id is in its heading, `[i/N status] did not
finish: <reason> · session <id>` (ADR-0276 D12). Every sentence that says where
the id is now names both places: the paragraph, `id`, the parser's
`_RESUME_ID_IS_A_PATH`, and `RESUME_NEEDS_ID` and `RESUME_UNKNOWN_ID` (D11). The
head was 1 525 bytes before.

### D4. A stopped single result says how to resume it

`tool.retry_guidance` puts `tool.RESUME_GUIDANCE` where ADR-0276 D11's retry
sentence was, for a child that has a session:

```
Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls; if it should go on, continue it with agent(action="resume", profile="<profile>", id="<id>", task=…).
```

Both values go through `tool.call_literal`: folded onto one line
(`envelope.child_line`), lone surrogates dropped, JSON-quoted — the profile is a
filename stem and may hold a newline or a quote (critique m1). A child with no
session keeps the phase-0 sentence (`DO_NOT_REDELEGATE`), and the print
condition — work to point at — is unchanged. The single-mode Esc report
(ADR-0276 D14) renders through the same template and carries the same line. A
batch member's block still has none (`_unfinished_member_block` keeps to the
facts); its `· session` and the description carry the rest. This amends
ADR-0276 D11's last bullet.

Refined in review round 1 (F9, live check 6): the sentence was an order —
"Check the changed files and its tool calls, then continue it" — and is now
conditional, "if it should go on". Under `-p`, a real model given the line
after its agent was killed resumed the agent in the same run although its
prompt said to call the tool once. A stop by Esc is the user's own decision,
so the report of a cancelled call (ADR-0276 D14) now says so before anything
it fences: `aggregate.CANCEL_REPORT_HEAD` reads ``[aelix system report — not
user input] The `agent` call you made was stopped when the turn was
cancelled. The stop was intended: resume or repeat that work only if the user
asks you to. What it had done:``, worded for a single call's report and a
batch's alike. This amends ADR-0276 D14's head too.

### D5. The receipts' first reader, and whose agents may be resumed

`child_session.find_resume_target` reads the parent's CURRENT branch
(`session.get_branch()`, the range the stats read; records before a compaction
are on it), collects the `aelix.child_session` records by key — a record whose
`v` is not 1 is ignored — and groups the runs by the child SESSION they ran in.
A child session has one first run, the one that allocated its file
(`resumes: null`), and any number of resumes, each naming it. No first run on
this branch is a refusal (`RESUME_FIRST_RUN_MISSING`); two, or a resume naming
another key, are damage.

Refined in review round 1 (F2; Codex review 1, verify M1): a run is read from
two of its lines. How it ENDED is its key's last line, ADR-0243's fold (last
line wins) — the status the listing shows. What it WAS — its profile, its task,
its child file, its `resumes` and its limits — is its key's first
`phase: "start"` line, written before the child existed (`_Run.identity`), and
the grouping reads the child from there too. Read last-line-wins, one settle
appended under the first run's key with `granted_mode: yolo` resumed a `plan`
agent at `yolo`; and nothing tested that the ceiling is the first run's rather
than the latest resume's. So:

- a first run whose start is not on this branch is `RESUME_FIRST_RUN_MISSING`
  (it was resumable off its settle);
- a first run whose lines disagree about its `limits` is damage (`its records
  disagree about the limits it ran with`). A genuine run's lines always agree:
  its start — whether `open` wrote it or `_run`'s `finally` did — and its
  settle copy one receipt.

Only a delegation the MODEL made can be resumed: its record has a
`tool_call_id`. `/agents run` and RPC `agents/run` record `tool_call_id: null`.
Their task was typed by a person and never shown to the model, and the dialog's
"written by the model" would be false of it (critique m4): `RESUME_NOT_YOURS`.

### D6. The child file is derived, and ownership is read off the child's own file

- **The location is never read off a record** (critique B2). The file is
  `place_children(<the parent's file, as its session names it now>).directory
  / <first key>.jsonl`, as `SpawnReceipt._allocate` placed the first run's
  file (refined in review round 2, below). A parent with no file, or one
  outside a bucket (where allocation makes no child file either), is refused
  (`RESUME_NO_SESSION`). The first key must be `sub-<12 hex>`
  (`child_session.SPAWN_KEY`), before any path is built from it. The record's
  `rel` is only compared with the derived one: under another stem it is a
  fork's copy of the original parent's record (`RESUME_FORKED`), otherwise it
  is damage. The record's absolute `path` is never used. This amends ADR-0243
  §3, whose reader tries the record's `rel` first and its `path` second.
- `lstat` first: a missing file is `RESUME_FILE_GONE`; a symlinked file or
  `<stem>/` directory, or a file that is not regular, is damage. The file is then
  opened with `O_NOFOLLOW` where the platform has it and checked regular on the
  descriptor, and only its first `CHILD_SCAN_BYTES` (2 MiB) are read. A torn line
  stays a placeholder, so the header and the origin keep their positions.
- Refined in review round 1, two more refusals before the read, both damage:
  - **a link below the parent's directory** (F3, Codex review 3), in either of
    the two components this module makes there: the children's directory and
    the file. After their `lstat`s, the file's `realpath` must be the
    `realpath` of the parent's directory with those two names appended (`its
    session file is reached through a link`), which also catches what `lstat`
    does not call a link, such as a Windows junction, and a `..`. That is the
    comparison since review round 2 (G1, below). Round 1 derived the placement
    from `os.path.realpath` of the parent's file and required the file's path
    to be its own `realpath`;
  - **a second name** (F4, Codex review 4): `st_nlink > 1`, `its session file
    has another name (a hard link)`. `SessionWriterLock` locks a path, so
    another name for the same bytes is another lock: a resume went through a
    hard link while a writer held the other name's lock, and the child wrote
    the shared file.
- Refined in review round 2 (G1, the round-2 verify's m1): the file is placed
  as allocation placed it. Round 1's lookup took `os.path.realpath` of the
  parent's file where `_allocate` had taken its `os.path.abspath`, and the two
  disagree wherever a link sits at or above the parent's level. A parent file that is a
  link to another file of its bucket was refused as a fork (`RESUME_FORKED`), a
  bucket linked to a directory whose name is not bucket-shaped as unsaved
  (`RESUME_NO_SESSION`), and a linked sessions root or bucket resumed under the
  resolved spelling rather than the `--session` its first run had; `883ded81`
  had resumed all three. Now a link at or above the parent's directory — a
  linked sessions root or bucket, a parent file that is itself a link — is the
  user's own setup and is taken as allocation took it: a parent opened by the
  path its first run had launches the resume with that run's `--session`, byte
  for byte. A parent reopened through another spelling of a link above its
  bucket finds its child under the new spelling. A parent reopened under
  another NAME of its own file — first as the link `p.jsonl`, later as its
  target `real.jsonl` — derives `real/` while its children are under `p/`, and
  is refused `RESUME_FORKED`, as at `883ded81`. No sentence or damage detail
  changed; which setups reach which refusal did.
- In that head: the header is one the session loader would open, and names the
  id that was asked for. Refined in review round 1 (F6, Codex review 8): it is
  parsed by the loader's own `jsonl_storage._parse_header_line` — `type`
  `session`, `version` 3, a non-empty `id`, `timestamp` and `cwd`, and a
  `parentSession` that is a string, null or absent — from the head split into
  lines as the loader splits it (universal newlines, strict UTF-8, blank lines
  skipped), and a header it refuses is `its session header is not valid`. A
  header at `version` 999 passed the old check (`type` and `id` only), and the
  child exited 1 after a delegation of budget and four records had been spent.
  The other checks: the second entry is `aelix.child_origin` with the first
  run's key; the origin's `parent.session_id` is THIS parent's session id; and
  at least one user message follows. A parent reopened with `--continue` or
  `/resume` passes the origin check. A fork does not (`RESUME_FORKED`): a fork copies the receipts, not the
  children, which stay under, and belong to, the original. A file with only the
  header and the origin is a child that never received its task (critique m5:
  `RESUME_NEVER_STARTED`).

### D7. The listing

A resume with no id, or with an id no run on this branch ran in, is refused with
`RESUME_NEEDS_ID` or `RESUME_UNKNOWN_ID` followed by the agents this conversation
can resume. A delegation cut by `/new`, `/resume`, `/fork` or quit left the model
a call with no result ("No result provided"), so the model never saw its id; the
record survived (owner decision 2), and this is how it reaches the model.

- Built from the records alone (`resumable_agents`, `_listing`). No candidate's
  file is opened (critique cut), so a listed agent can still be refused once it
  is chosen; the chosen one is checked in full.
- Model-door runs with recorded `limits` only, whose runs chain to one first run
  read off its start (D5), newest first (by its latest run's place on the
  branch), at most `LISTED_AGENTS` (10) inside `LISTING_BYTES` (2 048, the head
  and the overflow line included); the rest are counted
  (`- … and N more not shown`).
- One line each, `- <id> (<profile>, <status>): "<task>"`: the first run's
  profile (at most 40 characters) and task preview (at most 80), from its
  start, through `child_line`, lone surrogates dropped, whitespace collapsed,
  the task JSON-quoted. The status is the latest run's on this branch — `finished`,
  `stopped` (aborted, cancelled, an old `timeout`), `failed`, or `unknown` (a
  start with no settle). An id that is not id-shaped is not listed.
- Nothing is announced on the next turn by itself (limit 10); #611's `status`
  takes the listing over.

### D8. What bounded a child is decided once, recorded, and is the argv

- `child_session.ChildLimits` (frozen) holds **values, never argv fragments**:
  `granted_mode` (the consented `SpawnGrant.mode`, before a batch member's live
  floor tightened it — the resume ceiling, critique m3; what actually ran stays
  the record's top-level `permission_mode`), `tools`, `context_files`,
  `thinking` (the level passed as `--thinking`, or null when the run passed
  none — limit 13), `model` (`resolver.ModelPin`: `model`, `provider`, `route`;
  recorded as null when the run passed no model flag), `cwd`, `profile_source`
  and `profile_scope`.
- `print_channel.fresh_limits` is THE computation for a new delegation:
  `narrow_tools`, `narrow_context_files`, `inherit_thinking` and
  `resolver.child_model_pin` (`child_model_flags`' decision, read back as
  values), in the order the channel used. `runtime._run` calls it **above the
  admission block** (critique M4): the model pin reads the live registry and can
  raise, and a raise between the registry insert and the `try` would leak the
  row — four such leaks and `MAX_LIVE_CHILDREN` refuses every delegation for the
  rest of the session. Refined: the parent's model is read guarded
  (`_parent_model`: a getter that raises costs the child its inheritance, never
  its spawn), the registry unguarded (an unpinned user-defined route reached
  OpenRouter, #344). A failure while the limits are computed now fails the spawn
  before any row, budget or record exists; before, it raised inside the channel
  after the start and the pending were written.
- `SpawnPlan` carries `limits`, `dropped_tools` and `resumes`. `PrintChannel.run`
  builds the tools, context files, thinking level and model flags from
  `plan.limits` and nothing else (`limited_profile`,
  `build_child_argv(model_pin=…)`); a plan built without them — the channel
  tests build such plans — gets them from the same `fresh_limits`.
  `resolver.model_pin_flags` is the one place a pin becomes argv, and it can emit
  `--model`, `--provider` and `--require-registration-route` and nothing else;
  all three take the next token as their value (`REQUIRED_VALUE_FLAGS`).
  `_cancelled_envelope` reports `plan.dropped_tools`. The status row and the
  receipt's `requested_model` read the model off the limits, so the id shown is
  the id launched. The limits are not the whole argv: `--permission-mode` is the
  plan's, the grant as `_run` tightened it (D10), and the rest — the session
  flag, the profile's prompt file, skills and extensions, the trust flags,
  `--no-agents` and the task — is built as for any child.
- This closes the window between the receipt and the argv: a `/model` that landed
  after the receipt named a model and before the argv read it again. It does not
  close the window in `spawn()` between `/agents run`'s dialog and the launch
  (critique M4 corrected the first revision's claim).
- **Recorded on every run** (critique m8: one shape). Every start, and so every
  settle, carries `limits` (`ChildLimits.to_record`: lists, not tuples, ADR-0242
  rule 1.7) and `resumes`. `v` stays 1. The child's `aelix.child_origin` is
  unchanged.
- **Read back exactly** (`ChildLimits.from_record`), because the parent's file is
  all that stands between a forged record and a child's argv. `granted_mode` must
  be a `PermissionMode`; `tools` distinct built-in tool names, never `agent`;
  `context_files` a bool; `thinking` a `VALID_THINKING_LEVELS` level or null;
  `model` null or `{model, provider, route}` exactly, whose names, where set,
  hold no leading `-`, no whitespace and no character of a Unicode `C` category
  (at most 256 characters), and whose `route` is a 64-hex digest that comes with
  both names; `cwd` an absolute path with no control character (at most 4 096);
  `profile_source` such a path or null; `profile_scope` a short string. A refusal
  names the field and never the value (`resume_damaged`). Refined: **a field
  this aelix does not know is refused**, not ignored as ADR-0242 rule 1.6 would
  ignore an unknown record key — a later aelix may add a field to bound the child
  further, and a reader that dropped it would resume the child wider than it ran.
  Refined in review round 1 (F15, the docs check): `profile_source` is held to
  the absolute-path rule `cwd` is. `fresh_limits` records it as
  `print_channel.profile_identity` spells it (`os.path.abspath`), and
  `runtime._same_identity` compares that spelling, because discovery keeps a
  relative `AELIX_CODING_AGENT_DIR`'s paths relative and every resume of such a
  user's agents would otherwise have read as damage.
- **`RpcChannel` refuses a resume plan** (`plan.resumes` set) with an error
  envelope and no process: it recomputes a child's limits from the plan's parent
  state and never reads `plan.limits` (critique M4). It has no product caller
  (#123). `PrintChannel` refuses a resume plan that carries no limits, as a belt.
  Refined in review round 1 (F8, Codex review 10): `RpcChannel`'s refusal comes
  after `_run`'s admission, so a runtime handed an `RpcChannel` charged the
  budget and wrote a run's records before it was refused. A channel now says
  whether it can resume with a class attribute, `resumes_agents` — `True` on
  `PrintChannel`, `False` on `RpcChannel` — which `SubagentChannel` describes
  and which is not a Protocol member, since a fresh delegation never reads it.
  `runtime.resume_refusal` asks it first (`getattr(..., False)`, so a channel
  that does not say is refused): `RESUME_UNSUPPORTED_CHANNEL`, at the hook
  before any dialog and in `resume_granted` before `_run`. Both channels'
  refusals stay below it as belts (D11).

### D9. A resume runs on its first run's limits ∩ what a new delegation gets now

The ceiling is the **first** run's `limits`, not the last resume's: the issue
asks for the limits the agent was delegated with, and a resume narrowed because
the parent was briefly in `plan` must not narrow the agent for good. They are
read off the first run's start record (D5).
`print_channel.resume_limits` intersects them, field by field, with
`fresh_limits` for the parent as it is now:

| Item | A resume gets | Refused when |
|---|---|---|
| profile | the recorded name, re-resolved on the model door (`allow_project=False`); the call's `profile` must be that name | it is gone, renamed, project-scoped, or resolves to another file than the recorded `profile_source`, both spelled by `print_channel.profile_identity`; a first run recorded with no source cannot be matched (`runtime._same_identity`, `RESUME_PROFILE_CHANGED`) |
| posture | `posture.resume_clamp(granted_mode, now)`, the rank minimum, applied where D10 says; "now" is read when the call is approved (limit 11) | — |
| tools | the recorded list, in its order, less what a new delegation would not get now; none left is `--no-tools` | — |
| context files | on only if both say on | — |
| thinking | the recorded level, pinned: it is not an authority. A first run that passed no `--thinking` recorded `null`, and then the resumed child resolves its own level, as a new delegation of that profile does (limit 13) | — |
| model | the recorded pin, through `model_pin_flags` — never re-derived from a parent whose `/model` moved (Codex #26718, #36433). A pin with no route — a provider only the profile's own extension registers — reaches whatever that extension registers now (limit 12) | the pinned route no longer resolves the same explicit registration (`resolver.model_pin_route_moved`, asked of the parent's registry before anything is spent — critique m6: otherwise the child refuses its first turn after the consent and the budget), `RESUME_ROUTE_MOVED` |
| cwd | the recorded one — still that directory, and inside the parent's (`print_channel.resume_cwd`, the one function the hook's check and the launch share) | it is gone, moved or replaced, or no longer inside the parent's directory, `RESUME_CWD_GONE`. Refined in review round 1 (F5, Codex review 5): containment alone is not identity — a directory renamed, with a link to a sibling left under its old name, is still inside the parent, and the resumed child ran in the sibling. The first run records the resolved path (`resolve_child_cwd`'s `Path.resolve()`), so a recorded directory that no longer resolves to itself is refused |
| trust flags, `--no-agents`, depth | computed now, or constant, as for any child | — |
| system prompt, skills, extensions, output cap | the profile file as it is now (its identity is its path, and a new delegation gets the same body) | — |

The route check is an early refusal, not the guard: when the parent cannot
answer it (no registry, or one that raises) it does not refuse, and the digest
stays on the child's argv, where the child enforces it itself. It installs no
requirement on the parent's own registry.

The dropped-tools note names the recorded tools the intersection took away. A
record from before #610 has no `limits` and is refused
(`RESUME_BEFORE_LIMITS`); nothing is guessed.

### D10. Consent as for a new delegation, with the first grant as a ceiling in three places

One function, `posture.resume_clamp(ceiling, live)` — refined: it lives in
`posture.py`, beside `_RANK`, where `consent.py` can import it without a cycle —
is applied:

1. in the model door's pre-filter, `extension._grant_for(…, resume=target)`,
   which caps `want` before anything reads it. That branch returns a grant
   without reaching the consent door whenever nobody needs asking — every `yolo`
   parent, every read-only spawn — and a ceiling kept in the dialog alone
   resumed a `plan` agent under a `yolo` parent with a `yolo` grant (critique
   B1, probed). The disclosure row is composed from the capped mode;
2. in `consent.request_resume_consent`, right after its own clamp and above its
   no-UI return, which is all `-p` ever reaches. Refined: a sibling of
   `request_spawn_consent`, not a parameter on it. That function's parameter set
   is pinned (`test_request_spawn_consent_takes_no_memo_parameter`) and its body
   is the fresh-spawn dialog. The sibling shares `_may_widen`,
   `consent_is_required`, `build_options`, `_decide`, `_ask` and the lock;
3. as `resume_granted`'s `permission_floor`, which `_run` applies with
   `_tighten` — the belt.

**The ceiling is in the widening predicate** (critique M1). `consent._may_widen`
gains constraint 7 — with a ceiling, widening to `auto-accept-edits` needs a
ceiling at or above it — and `consent_is_required` passes the ceiling through.
With a ceiling below `auto-accept-edits` there is nothing to widen and no write
authority to confirm, so there is no dialog: one that offered only "Run
read-only (plan)" and "Cancel" is the shape the 2026-07-27 owner amendment
removed. An agent a human let edit may be let edit
again; one whose first run was granted only `plan` may not. `ceiling=None`,
which is every fresh spawn, changes nothing. The ceiling is the model door's
alone and is not on the Protocol.

The ceiling is the GRANT, not the posture the first run actually ran at (D8,
critique m3). The two differ only for a batch member the live floor tightened
(ADR-0199 §(k)): such an agent can be resumed up to its grant, which is still no
wider than what a new delegation gets now, since every resume is also capped
by that.

The dialog body (`consent.build_resume_consent_title`) is 13 rows, 14 with a
model:

```
Resume agent '<name>'?

Session:    <child session id>
Profile:    <name> (<scope> scope)
Source:     <the profile file, middle-elided>
Directory:  <the recorded directory, whole>
Permission: <resume_clamp(first grant, the clamp now)>[ (first run: <first grant>)]
Model:      <the recorded pin's --model>          (only when there is one)

Earlier task (written by the model):
<the FIRST run's task as its record kept it>

Next instruction (written by the model, not by you):
<the whole instruction>
```

Every value is sanitised as in `build_consent_title`; the id and the earlier task
come off the parent's file, so they are as attacker-reachable as the directory.
"Written by the model" is true of the earlier task because only the model's
delegations can be resumed (D5). Refined: the earlier task ends in `…` when it is
200 characters long, where the record cuts it (one of exactly 200 is marked
too), and reads `(not recorded)` when it is empty. The options are
`build_options(clamped, may_widen=…)`.

Refined in review round 1 (F11, live check 10): when the first run's grant ranks
above the mode on the `Permission:` row, the same row says so —
`Permission: plan (first run: auto-accept-edits)`. Before, the only sign that
the first run had more was the widening option. A grant equal to or below the
row's mode adds nothing, and the row count is unchanged.

The `yolo` disclosure row is `build_disclosure_line`'s, with the capped mode.
Refined in review round 1 (F10, live check 3): for a resume it reads
`resuming <name> at <mode>, from <source>` (`resume=True`, from the pre-filter
and from `request_resume_consent` alike), with no task count. It read
`delegating to <name> at <mode> — 1 task, from <source>`, which reads as a new
delegation. A new delegation's row is unchanged.

### D11. Refused before a process, and before admission

Every refusal that can be known at the `tool_call` hook is taken there, before a
dialog, the budget or a record. `AgentsExtension._approve_resume`, in order:
the target (`runtime.find_resume_target`, D5–D7); the call's `profile` is the
recorded one; that name re-resolves on the model door; `runtime.resume_refusal`;
one delegation of budget left; the grant (D10). Then `_execute` re-resolves the
profile and compares its file with the grant's, as it does for a `run`, and
`runtime.resume_granted` checks once more, before `_run`, a dialog having
possibly stood open for minutes:

1. the parent's session is still the one the target was found in
   (`RESUME_CONVERSATION_CHANGED`);
2. the hook's own lookup, `find_resume_target`, runs again — the same
   function, not a copy of its checks — and must find the same target: every
   field but the latest run's status (`runtime._same_target`). Whatever it
   refuses now is refused with its own sentence; a different target under the
   same id is `resume_damaged("its record changed before the resume could
   start")`;
3. `resume_refusal` still passes.

Refined in review round 1 (F3; Codex review 3, verify m4): the check before
`_run` used to repeat `resume_refusal` alone, whose file check was the file's
type. After the hook, a file swapped for another valid session (the resumed
child read that session's messages), a `<stem>/` directory swapped for a link
out of the bucket, and a branch moved off the first run's record all resumed.
Each is refused at step 2 now, before anything is spent. What is left is the
time between this last check and the child's own open of its file (limit 15).

No product path refuses a resume after `_run`'s admission (critique M5), where a
refusal would already have charged the budget and written a start, a pending and
a settle for a run that never was. The one check inside `_run` — the directory,
in `resume_limits` — sits above the admission block. Two belts sit below it,
and no product path reaches either: `PrintChannel.run` refuses a resume plan
that carries no limits (`_run` always passes them), and `RpcChannel.run`
refuses every resume plan (a runtime on `RpcChannel` is refused before
admission, D8). Refined in review round 1 (F8, Codex review 10): the second was
reachable by a runtime handed an `RpcChannel`.

`resume_refusal` asks what the records cannot answer — this runtime, this
parent, this moment — synchronously and in order: whether this runtime's
channel can resume (`resumes_agents`, D8); the profile's identity (D9); whether
a live registry row runs this child session; the directory, still that
directory and inside the parent's (`print_channel.resume_cwd`, D9); the route;
the writer lock (D12). Refined in review round 1 (F3): it no longer looks at the
file. What the file must be is the lookup's, which both callers run immediately
before it; the copy here checked only that it was still a regular file, which
is how the check before `_run` missed a file swapped for another session's. A
file deleted after the hook is still refused before anything is spent, by the
lookup (`RESUME_FILE_GONE`): launched, the child would die "No session matching"
after the budget was spent.

| Case | Answer (each one complete sentence, none naming a path) |
|---|---|
| no id; an id no run on this branch ran in | `RESUME_NEEDS_ID` / `RESUME_UNKNOWN_ID`, then the listing (D7) |
| a path-shaped id; a `tasks` that is not absent, a `mode` other than `single`, any `cwd` or a truthy `background` on a resume; an `id` on a run; an unknown or empty action; an instruction over 64 512 bytes, or holding a lone surrogate or a NUL | the parser's own sentence (D1) |
| the parent has no file, or none inside a bucket, by the path its session names (D6) | `RESUME_NO_SESSION` |
| the parent's branch cannot be read | `RESUME_UNREADABLE` |
| not a model-door delegation | `RESUME_NOT_YOURS` |
| no first run on this branch, or none whose start is on it (D5) | `RESUME_FIRST_RUN_MISSING` |
| a record from before #610 | `RESUME_BEFORE_LIMITS` |
| the file is gone | `RESUME_FILE_GONE` |
| a fork: the record's `rel` under another stem, or an origin naming another parent | `RESUME_FORKED` |
| no user message in the child's file | `RESUME_NEVER_STARTED` |
| limits that do not validate, or lines of the first run that disagree about them; runs that do not chain; a first key not shaped like a spawn key; a link below the parent's directory — the children's directory or the file (D6) — or a second name for the file; a header the session loader would refuse, or one naming another session; an origin that does not match; a target, found again before the launch, that is not the approved one | `resume_damaged(<what, in the module's own words>)` |
| another profile in the call | `Resume refused: that agent ran as profile "<recorded>", and it is resumed under that profile only. …` (`extension._not_its_profile`, the recorded name through `call_literal`) |
| this session delegates through a channel that cannot resume (`resumes_agents` not true) | `RESUME_UNSUPPORTED_CHANNEL` |
| the profile cannot be re-found, or is another file | `RESUME_PROFILE_CHANGED`, whatever discovery's own error said |
| the route moved | `RESUME_ROUTE_MOVED` |
| the directory is gone, moved or replaced, or outside | `RESUME_CWD_GONE` |
| a live row in this process runs it | `RESUME_RUNNING` |
| another process holds its writer lock — the child still running, or a human's `aelix --session` | `RESUME_IN_USE` |
| the lock cannot be checked | `RESUME_LOCK_ERROR`, with the `OSError`'s own words |
| the conversation changed before the spawn | `RESUME_CONVERSATION_CHANGED` |
| depth, budget, a decline, a grant that is not consented | as for a `run` |

Refined: the `RESUME_*` sentences live in `child_session`. The runtime holds the
preamble (`RESUME_PREAMBLE`, D13) and one damage detail (`_TARGET_CHANGED`,
step 2 above), and the profile mismatch is beside the hook in `extension.py`.
Product-core's resolver answers `model_pin_route_moved` with a bool and carries
no refusal text.

Refined in review round 1 (F13, verify m6): `RESUME_NO_SESSION`,
`RESUME_UNREADABLE`, `RESUME_LOCK_ERROR` and `RESUME_CONVERSATION_CHANGED`
said why and not what to do next. Each now ends `Delegate the task anew.`, as
most refusals do; `RESUME_RUNNING` and `RESUME_IN_USE` say to wait, and
`RESUME_FORKED` where to resume. `RESUME_NOT_YOURS` was left saying only why,
until review round 2 (G4, below). With F12 (D3) and F5 (D9), the sentences
that changed in review round 1 read:

- `RESUME_NEEDS_ID`: `Resume refused: action="resume" needs id — the Session id
  of an agent this conversation delegated, as its result shows it: on its
  "Session:" line, or after "session" in a batch member's "did not finish"
  heading.`
- `RESUME_UNKNOWN_ID`: `Resume refused: no agent this conversation delegated
  has that id. Use the id the agent's result shows: on its "Session:" line, or
  after "session" in a batch member's "did not finish" heading.`
- `RESUME_NO_SESSION`: `Resume refused: this conversation is not saved in
  aelix's sessions directory (--no-session, or a file opened from elsewhere), so
  no agent it delegated has a session to continue. Delegate the task anew.`
- `RESUME_UNREADABLE`: `Resume refused: this conversation's record could not be
  read, so that agent cannot be found. Delegate the task anew.`
- `RESUME_LOCK_ERROR`: `Resume refused: aelix could not check whether that
  agent's session is in use ({error}). Delegate the task anew.`
- `RESUME_CWD_GONE`: `Resume refused: the directory that agent ran in no longer
  exists, was moved or replaced, or is no longer inside this conversation's
  working directory. Delegate the task anew.`
- `RESUME_CONVERSATION_CHANGED`: `Resume refused: the conversation changed
  before the resume could start, so that agent is not this conversation's to
  resume. Delegate the task anew.`
- `RESUME_UNSUPPORTED_CHANNEL` (new): `Resume refused: this session delegates
  through a channel that cannot resume an agent inside the limits it ran with.
  Delegate the task anew.`

Refined in review round 2 (G4, the round-2 verify's m4): `RESUME_NOT_YOURS`
ends with the same next step. It is the only model-facing sentence review round
2 changed:

- `RESUME_NOT_YOURS`: `Resume refused: that agent was not delegated by you —
  the user started it (/agents run) or a client did — and only an agent you
  delegated can be resumed. Delegate the task anew.`

`resume_damaged`'s template is unchanged: `Resume refused: that agent's session
record is damaged ({detail}). Delegate the task anew.` The details review round
1 added are `its records disagree about the limits it ran with` (D5), `its
session file is reached through a link`, `its session file has another name (a
hard link)`, `its session header is not valid` (D6) and `its record changed
before the resume could start` (step 2). `its session file is no longer a
regular file`, the detail of the file check `resume_refusal` dropped, is no
longer given; the lookup's `its session file is not a regular file` answers in
its place. `LimitsError`'s reason for `profile_source` reads `profile_source is
not an absolute path` (D8).

### D12. The door, the busy check, and an attached receipt

- `_SubagentRuntimeImpl.resume_granted(grant, resolved, target, instruction, *,
  on_event, record)` sits beside `spawn_granted`, off the `SubagentRuntime`
  Protocol, so `CONTRACT_VERSION` stays 1; so do `find_resume_target`,
  `resumable_agents` and `resume_refusal`. Refined: it takes `resolved`, the
  profile `_execute` re-resolved, because `_run` needs the profile's body,
  skills, extensions and output cap; its identity is checked against
  `target.limits.profile_source`. A grant that is not consented fails closed.
- **Busy is the registry row plus the writer lock** (critique M5). A row carries
  the child session it runs (`RunningChild.session_id`: set at the insert for a
  resume, at the allocation for a new delegation). The lock is probed with
  `SessionWriterLock(path, timeout=0)` — `try_acquire`'s default would block for
  a quarter second — and released at once. Contention is `RESUME_IN_USE`, a
  sidecar that cannot be used is `RESUME_LOCK_ERROR`, and a filesystem that
  cannot lock is no refusal (the child's own lock degrades the same way). This is
  check-then-act: the child's own lock is the guard, and a child that loses a
  race exits 1 with "already open in another terminal". No busy state outlives
  `_run` — Esc kills a child with no reaper, and a dying child holds its lock
  until it is gone.
- `_run` is shared, with three differences:
  - a **new spawn key** for the run (D15);
  - the receipt is **attached**, not allocated (`SpawnReceipt.attach`), because
    `_allocate` refuses a name that is taken. The start and the pending go to the
    parent under the new key, counted in `opening_attempted` as `open` counts
    them (critique m8). The start's `child` is the target file, `resumes` the
    first key, `limits` this run's, `mode` `"single"`, and `task_preview` the
    instruction, not the preamble. **Nothing is written to the child's file**: the
    child holds it and appends its own transcript, and in pi a record slipped in
    between a dangling tool call and the next prompt made every later turn fail
    with 400 (#8476, #8502);
  - the plan's `session_path` is the target file, its `limits` D9's, and its task
    `runtime.resume_task(instruction)`.

### D13. One preamble for every resume

The child's task is `runtime.RESUME_PREAMBLE`, a blank line, `Next instruction:`
and the instruction:

> This continues your earlier work in this session. If your previous run stopped
> before it finished, pick up where you left off and do not repeat work that is
> already done. A tool call that shows no result may or may not have run — check
> its effect before relying on it.

The same words whatever the last status. The status is what THIS branch
recorded, and another `/tree` branch may have resumed the child since (limit 2),
so a line chosen by status could tell the child something false about its own
transcript (critique M3). "May or may not have run", not "did not complete": the
parallel tool path drops the results of siblings that did finish when it is
cancelled (critique m7). The wording follows OpenCode's and Qwen Code's. The
preamble and its separator are 290 bytes, and a module-level check — not an
`assert`, which `python -O` strips — raises at import if they ever outgrow
`RESUME_PREAMBLE_RESERVE`.

### D14. A tool call cut mid-run is answered by the provider transform

A child stopped while a tool runs leaves its file ending in
`assistant(toolCall)`, with no result and no abort marker; a line a SIGKILL tore
is skipped and healed on the next open. On the resumed child's next request the
provider transform (`_transform_messages`) puts
`ToolResultMessage("No result provided", is_error=True)` ahead of the new user
message — an error, never "cancelled + success", the bug deepagents #5546
fixed. Measured with a real child on `openai-completions` (Verification): the
request is `user`, `assistant(tool_calls call_sleep_1)`,
`tool(call_sleep_1, "No result provided")`, `user("Task: <preamble>…")`, and no
tool result is written to the child's file.
All six built-in adapters go through the transform; a wire with an error flag
(anthropic's `is_error`, google's `{"error": …}`) carries it as an error, the
openai family as that text, and D13's preamble covers the text-only wires. The
transform is not changed. Limits 3 and 5 are the gaps.

### D15. A run is a key, and a resume is counted with its delegation

Every run gets a key of its own, and `resumes` links it to the first. The stats
fold is last-line-wins per key (ADR-0243 §4), so reusing the first key would
erase the first run's spend. With a new key the footer, `/cost`, `/stats`,
History and RPC `get_session_stats` add both runs, and `runs` counts the resume.
A resume's usage is its own stream's: the input tokens of the history it re-sent
are in it; the child's own compaction calls are not (limit 6).

### D16. The model's `agent` tool is the only door

`/agents run`, RPC `agents/run`, ACP and the `SubagentRuntime` Protocol are
unchanged (#612). `-p` takes the same tool path and resumes with no dialog, at
the intersected posture. A child that `/agents run` or an RPC client started is
not resumable (D5).

## Consequences

- The model can continue an agent it delegated — finished, or stopped or failed
  after it started — in the agent's own conversation and session file instead of
  starting the work again. Every result with a session names its id, and a
  stopped single result says how.
- A resumed agent runs on or below what its first run was granted, item by item,
  and on or below what a new delegation would get when the resume is approved,
  whatever the parent did since: a `yolo` parent, a `-p` run and a wider
  `--tools` do not widen it. Its model is the one it ran with, and so is its
  thinking level when its first run passed one (limit 13).
- Every delegation's start and settle grow by a `limits` object and a `resumes`
  key. An aelix that predates them ignores them (ADR-0242 rule 1.6). Records
  written before #610 read as they did and cannot be resumed.
- What bounds a new child is decided before the run is admitted, by the same
  functions as before, and the argv's tools, context files, thinking level and
  model flags come from that decision alone. Its `--permission-mode` is the
  grant, as `_run` tightens it (for a resume, by the first run's grant), and
  the rest of it — the session flag, the profile's prompt file, skills and
  extensions, the trust flags, `--no-agents`, the task — is built as for any
  child. A host whose model getter raises still spawns, without inheritance; a
  failure while the limits are computed fails the spawn before anything is
  recorded.
- A success with a session carries one more line, `Session: <id>`. The
  description head is 1 598 of its 2 048 bytes.
- The report of a cancelled `agent` call says the stop was intended and tells
  the model to resume or repeat the work only if the user asks (D4).
- Hosts see no new RPC method and no Protocol change. A channel may declare
  `resumes_agents`; a runtime whose channel does not — `RpcChannel` says
  `False` — is refused a resume before it is admitted. `/agents run`'s own
  results are unchanged.
- A human who opens a child file with `aelix --session` still gets an ordinary
  session at their own posture, and the ADR-0243 §8 warning, unchanged.
- The lock probe creates the child file's `.lock` sidecar if the child had not
  already; on POSIX it stays, by `session_lock`'s design, as the one every
  running child creates does.

## Limits and unverified risks

The numbers are the design's (§12); the code cites them.

1. **A forked conversation does not resume** (D6). The fork keeps the receipts,
   which point at the same files, but the children belong to the original, and
   the model is told to resume them there or to delegate anew. Copying a child
   into the fork is not done.
2. **Two `/tree` branches of one session can resume the same child in turn, and
   a human's `aelix --session <child file>` continuation is invisible to the
   parent** (critique M3). The child's file only grows — each run appends under
   the child's own lock — and the child knows everything it did; what goes stale
   is the parent model's view on the other branch. D13's preamble does not depend
   on the status for this reason. A fingerprint check (the file's last entry id
   in each settle) was not added: Esc's SIGKILL races the settle, so it would
   refuse falsely.
3. **An adapter an extension registers (`register_api_adapter`) gets the raw
   messages** and never sees the transform's "No result provided" (D14): a
   resumed child on such an adapter sees a tool call with no result at all.
4. **A profile body edited in place is what a resume runs.** The identity is the
   file, and a new delegation would get the same body.
5. **The parallel tool path drops finished siblings' results when it is
   cancelled** (the kernel's existing behaviour), so a resumed child sees calls
   that did run as "No result provided". The preamble tells it to check effects.
6. **A child's own compaction calls are in no total** (ADR-0243, as before).
7. **The child of a parent killed with `kill -9` can outlive it on macOS and
   hold its lock** — macOS has no parent-death signal (the README's "Delegation's
   cleanup is not equal on every OS"; the related macOS cleanup gap is #619).
   Until it ends, a resume is refused `RESUME_IN_USE`.
8. **The model door only, for the model's own delegations** (D5, D16). No human
   command and no host surface resumes (#612).
9. **Delegations recorded before #610 cannot be resumed** (D9): their limits
   were never recorded and are not guessed.
10. **A delegation cut by `/new`, `/resume`, `/fork` or quit is not announced on
    the next turn.** The model finds it in the listing (D7), which #611's
    `status` takes over.

Added in review round 1. The round's threat model: whoever can write the
parent's session file or its sessions directory already has the user's
authority, so the boundary that matters is "no wider than a new delegation
now" (D9); what could be closed cheaply was closed (D5, D6, D11), and what is
left is listed here.

11. **The parent's posture is read when the call is approved** (Codex review 2),
    as for a new single delegation: the hook's `_grant_for` reads it once, and a
    shift+tab between the approval and the launch is not read again. A resume
    approved under `yolo` runs at `yolo` (within its first grant) even if the
    parent moved to `plan` before `_execute`. Only a batch's later members read
    the live posture again (ADR-0199 §(k)), and a resume is never a batch.
12. **A provider only the profile's own extension registers has no route pin**
    (Codex review 6). The parent does not load that extension, so its registry
    does not know the provider and no digest is recorded — pinning what the
    parent resolved instead sent such a child to OpenRouter (#362). The model
    and provider names are pinned; the endpoint they reach is that extension's
    registration at each run, as it is for a new delegation. The reasoning of
    limit 4: the extension is part of the profile as it is now.
13. **The thinking level is pinned only when the first run passed `--thinking`**
    (Codex review 7). `inherit_thinking` gives a child the parent's level only
    when its profile names neither `model` nor `provider`, so that it runs on
    the parent's model. A profile that names its own model or provider and no
    `thinking:` passes none: its limits record `thinking: null`, and each of its
    runs, a resume included, starts at the `defaultThinkingLevel` of the
    child's global settings as they are at that launch, clamped to its model,
    as a new delegation of it would. Likewise a first run that passed no
    `--model` — a profile with no model of its own under a parent whose model
    could not be read — records `model: null`, and a resume runs the child's
    own model cascade again.
14. **On macOS, a `bash` command the child was running can outlive the child**
    when Esc stops it or a SIGKILL kills it (#619, live check 1). A resume does
    not wait for it — in the live check the leftover `sleep` still ran when the
    parent resumed the child — and the preamble (D13) tells the child to check
    a cut call's effect before relying on it.
15. **The child opens its file by path, after the last check** (verify m4,
    Codex review 3). `resume_granted` runs the lookup again right before `_run`
    (D11), and the launch passes the checked path as `--session`; the child
    process then opens it by path, and `JsonlSessionStorage.open` has no
    `O_NOFOLLOW` on any OS. The file or the children's directory swapped for a
    link, or the file for another session, between that last check and the
    child's open is followed. Closing it would take the child checking what it
    opened. A link at or above the parent's directory is followed at every
    step, the first run's included: it is the user's own (D6, G1).

Added in review round 2:

16. **A resume's "Tools not granted" note names only what the intersection
    takes away now** (the round-2 live check, its item 8). It is D9's
    dropped-tools note: the recorded tools a new delegation would not get now.
    A tool the profile lists that the first run was not given is outside the
    ceiling rather than taken away now, so a resume's result does not name it:
    the first run's result named the ones the profile listed then, and its
    reason, "it holds no tool you do not hold", would not be the reason now. In
    the live re-check at `c6e766bd` (`l4_not_widened.py widen`), the resume ran
    with 2 of the profile's 5 tools and its result had no such note; the first
    run's result had one, as round 1 captured it: `Tools not granted to this
    agent: bash, grep, write (it holds no tool you do not hold).`
17. **A parent reopened under another name of its own file is told its agent
    belongs to a fork** (the final verify's m2). Opened first as the link
    `p.jsonl` and later as its target `real.jsonl` — or the other way round
    through a link made later, under a hard-link name, or as `P.jsonl` for
    `p.jsonl` on a case-insensitive filesystem — the parent derives a
    children's directory its first run did not use (D6), and the resume is
    refused with `RESUME_FORKED`: "Resume refused: that agent belongs to the
    conversation this one was forked from. Resume it there, or delegate the
    task anew." The next step is right and nothing is widened, but the
    conversation was not forked. The setup needs a second name for the
    parent's own file; `883ded81` refused the same four cases.
18. **A parent file named `..jsonl` or `...jsonl` puts its children outside a
    children's directory** (the final verify's m3). `place_children`, which
    #610 does not change, strips `.jsonl` and gets the stem `.` or `..`, so the
    first run's child file lands in the bucket itself or in the sessions root,
    which its docstring says cannot happen, and a resume of it is refused as
    `its session file is reached through a link` though there is no link
    (`883ded81` resumed both). It needs a parent file named by hand, and the
    placement is the `run` path's, which this record keeps as it was.

Not checked, or found on the way and left as it is:

- **Windows is unmeasured.** It has no `O_NOFOLLOW`, so the parent's own read of
  the child file's head follows a link swapped in after the checks before it
  (the child's open follows one on every OS: limit 15). The hard-link count,
  junctions and the `realpath` comparison (D6) were not run there — a junction
  as the children's directory is only simulated, on macOS, by an `lstat` that
  follows links (G1); the writer-lock probe was measured on macOS only
  (filelock 4.0.1); and the hard-link, link and real-child tests are
  POSIX-only.
- **Found on the way and fixed (tests only):** lane A's limits fixture used
  `cwd: "/repo"`, which `os.path.isabs` refuses on Windows from Python 3.13
  (`ntpath.isabs('/repo')` is `True` on 3.11.15 and 3.12.13, `False` on 3.13.13
  and 3.14.5). `test_a_good_limits_record_round_trips` and 10 cases of
  `test_a_forged_limits_field_is_refused` would have failed on the windows
  py3.13 and py3.14 legs; the fixtures now use `os.path.abspath`. Simulated with
  `ntpath.isabs` on macOS, not run on Windows.
- **A parent opened read-only** (ADR-0244) that resumes was not checked; the
  critique expects its record appends to be swallowed, as a delegation's are.
- **A `run` task holding a lone surrogate** raises `UnicodeEncodeError` out of
  `parse_agent_call` (`chain.check_task_size` encodes strictly) instead of a
  refusal the hook blocks with, and **one holding a NUL** passes the parser and
  fails at `create_subprocess_exec` (`embedded null byte`) after the budget and
  the records. Both predate #610 (they are in `f3e76fbb`) and are left to a
  separate issue; a resume instruction is refused cleanly for both (D1).

## Verification

Tests, under `tests/agents_ext/` unless a path is given. Each bullet names the
first lanes' tests, then what the fixes of review round 1 (`Fn`) and review
round 2 (`Gn`) added or changed, as D1–D16 name them.

- **D1, D3** — `test_agent_resume_tool_610.py`:
  `test_the_schema_and_the_parser_agree_on_the_action_set`,
  `test_a_call_with_no_action_and_no_id_parses_as_it_did_before_610`,
  `test_an_unknown_or_empty_action_is_refused_and_nothing_runs`,
  `test_a_run_that_carries_an_id_is_refused_and_nothing_runs`,
  `test_a_resume_refuses_every_key_that_would_make_it_another_run`,
  `test_a_resume_takes_the_absent_spellings_of_those_keys`,
  `test_a_resume_id_is_never_a_path`,
  `test_a_resume_with_no_id_is_left_for_the_hook_to_answer`,
  `test_a_resume_id_is_a_string_and_is_trimmed`,
  `test_a_resume_needs_the_profile_and_the_next_instruction`,
  `test_a_resume_instruction_has_its_own_budget_and_its_refusal_states_it`,
  `test_an_instruction_that_cannot_be_sent_is_refused_before_anything_is_spent`;
  and `test_tool_and_security.py`'s `test_the_contract_text_stays_inside_its_own_budget`,
  which now pins `action="<a>"` in the head for every action. Round 1:
  `test_a_nul_in_a_resume_instruction_is_refused_by_the_parser` and
  `test_a_nul_instruction_spends_nothing` (F7);
  `test_every_sentence_that_says_where_the_id_is_names_both_places` (F12: the
  five sentences, and a real unfinished member's heading carrying both words).
- **D2, D4** — `test_an_ok_batch_member_names_its_session_on_a_line_of_its_own`,
  `test_the_resume_guidance_puts_one_clean_argument_where_each_value_goes`,
  `test_esc_during_a_resume_reports_it_as_a_single_run`; and in
  `test_stopped_child_reports_604.py`, `test_the_unfinished_template_in_full`,
  `test_the_retry_guidance_needs_work_to_point_at`,
  `test_a_success_carrying_every_new_field_renders_as_before_but_for_its_session`
  (with no session it is still the pre-#604 renderer's text, byte for byte),
  `test_a_success_through_the_runtime_renders_as_before` and both Esc reports;
  `test_refusals_and_declines_render_exactly_as_before` is unchanged. Round 1:
  `test_the_cancel_report_says_the_stop_was_intended` (F9), and the new
  guidance and head pinned in `test_the_unfinished_template_in_full` and
  `test_esc_during_a_delegation_queues_its_report_for_the_next_prompt`.
- **D5–D7** — `test_agent_resume_610.py`:
  `test_a_delegated_and_started_child_is_found`,
  `test_a_missing_or_unknown_id_is_refused_with_the_listing`,
  `test_an_agents_run_child_is_not_the_models_to_resume`,
  `test_a_record_from_before_610_is_refused`,
  `test_runs_that_do_not_chain_to_one_first_run_are_refused`,
  `test_a_forked_conversation_does_not_resume_the_originals_child`,
  `test_a_parent_with_no_session_file_has_nothing_to_resume`,
  `test_a_child_that_never_started_is_refused`,
  `test_a_damaged_child_file_is_refused`, `test_a_symlinked_child_file_is_refused`,
  `test_the_recorded_path_is_never_a_way_to_the_child_file`, and the listing's
  `test_the_listing_is_newest_first_bounded_and_counts_the_rest`,
  `test_the_listing_stays_inside_its_byte_budget`,
  `test_the_listing_cleans_what_a_record_says`,
  `test_the_listing_says_how_each_agents_last_run_ended`; through the hook,
  `test_a_missing_or_unknown_id_is_answered_with_what_could_be_resumed` and
  `test_an_agent_that_never_started_is_refused_at_the_hook`. Round 1:
  - F2: `test_a_later_line_that_widens_the_first_runs_limits_is_damage`
    (refused, not listed, no process);
    `test_the_ceiling_is_the_first_run_not_the_latest_resume` (a first run at
    `auto-accept-edits` with bash, read and write, a resume narrowed to `plan`
    and read, then a wide resume back at `auto-accept-edits` with all three,
    the dialog's earlier task the first one; verify M1);
    `test_what_the_first_run_was_is_read_off_its_start`;
    `test_a_first_run_whose_start_is_not_on_the_branch_is_refused`; through
    the hook, `test_a_forged_later_receipt_cannot_raise_the_ceiling` (Codex
    review 1's probe). Four tests that forged a later line to change the first
    run now rewrite every line of its key:
    `test_a_forged_record_is_refused_before_any_process`,
    `test_the_recorded_path_is_never_a_way_to_the_child_file`,
    `test_a_record_from_before_610_is_refused` and
    `test_a_recorded_profile_name_is_cleaned_before_it_is_echoed`;
  - F3: `test_a_parent_opened_through_a_linked_directory_still_finds_its_child`
    (POSIX) and `test_a_child_path_with_a_parent_step_is_not_its_file`; F4:
    `test_a_child_file_with_a_second_name_is_refused` (POSIX);
  - F6: `test_a_header_the_childs_loader_refuses_is_refused_before_anything_is_spent`
    (eight headers, each first shown to make `JsonlSessionStorage.open` raise)
    and `test_the_header_rule_is_the_session_loaders_own` (the same function,
    and the same line split);
  - F14: `test_a_first_key_not_shaped_like_a_spawn_key_is_refused_before_any_path`
    (verify m1).

  Round 2:
  - G1 (the round-2 verify's m1), all POSIX. Through the real hook and
    `execute()`, in `test_agent_resume_tool_610.py`:
    `test_a_link_at_or_above_the_parents_directory_resumes_on_the_first_runs_session`
    (a linked sessions root, a parent file that is a link, a bucket linked to a
    bucket-shaped name and to a plain one: each resumes, and the resume's
    `--session` is the first run's string) and
    `test_a_link_below_the_parents_directory_is_refused_at_the_hook_and_at_execute`
    (under a linked bucket, the children's directory or the file swapped for a
    link: refused at the hook, or at execute once the hook had approved, with
    nothing spent). In this file:
    `test_a_link_lstat_does_not_report_in_the_childrens_directory_is_refused`
    (a junction, simulated by an `lstat` that follows links), and
    `test_a_parent_opened_through_a_linked_directory_still_finds_its_child`,
    which now finds the child under the parent's linked spelling, its
    `realpath` being the first run's file;
  - G2 (m2): `test_a_later_line_moves_neither_the_target_nor_the_listing` (a
    later line of the first run's key, its limits agreeing, that names another
    child session, another `resumes`, another profile or another task moves
    neither the target nor the listing).
- **D8** — `test_every_start_and_settle_records_the_limits_the_argv_was_built_from`,
  `test_a_forged_limits_field_is_refused`,
  `test_a_limits_object_missing_a_field_is_refused`,
  `test_a_good_limits_record_round_trips`,
  `test_a_model_pin_emits_exactly_the_flags_the_decision_made`,
  `test_a_registration_route_pin_round_trips_too`,
  `test_a_resumed_childs_argv_carries_no_recorded_string_as_a_flag`,
  `test_a_forged_record_is_refused_before_any_process`,
  `test_limits_that_cannot_be_computed_leak_no_registry_row`,
  `test_the_rpc_channel_refuses_a_resume_plan`,
  `test_the_print_channel_refuses_a_resume_plan_without_limits`,
  `test_the_print_channel_launches_a_resume_from_its_limits`; the record-key
  pins in `test_child_session_records.py` (`_IDENTITY_KEYS` gains `limits` and
  `resumes`); three argv-builder spies that now read `model_pin`
  (`test_host_late_binding.py`, two in `test_print_channel_spawn.py`); and
  `test_events_and_statusline.py`'s
  `test_a_host_whose_model_getter_raises_still_spawns`, which pins the guarded
  model read. Round 1: `test_a_channel_that_cannot_resume_is_refused_before_admission`
  (`RpcChannel`, and a stand-in that does not say; the budget unchanged, no
  record) and, through the hook,
  `test_a_channel_that_cannot_resume_is_refused_at_the_hook` (F8);
  `test_a_relative_profile_path_is_recorded_absolute_and_still_resumes`, and a
  relative `profile_source` case in `test_a_forged_limits_field_is_refused`
  (F15).
- **D9** — `test_resume_clamp_is_the_rank_minimum`,
  `test_resume_limits_takes_the_narrower_side_of_every_authority`,
  `test_a_parent_that_narrowed_resumes_the_child_narrower`,
  `test_a_parent_that_widened_does_not_widen_the_resumed_child`,
  `test_a_first_run_a_human_widened_resumes_up_to_that_grant_and_no_further`,
  `test_the_resumed_child_keeps_its_model_and_thinking_after_the_parent_moved`,
  `test_a_directory_gone_or_outside_the_parent_is_refused`,
  `test_a_profile_that_is_not_the_first_runs_is_refused`,
  `test_a_moved_registration_route_is_refused_before_anything_is_spent`; through
  the hook, `test_a_parent_that_narrowed_resumes_the_agent_narrower`,
  `test_a_resume_under_another_profile_names_the_one_the_agent_ran_as`,
  `test_a_recorded_profile_name_is_cleaned_before_it_is_echoed`,
  `test_a_profile_that_is_gone_or_now_shadowed_is_refused`. Round 1:
  `test_a_directory_replaced_by_a_link_to_a_sibling_is_refused` (at the hook's
  check and at the launch) and
  `test_the_recorded_cwd_is_the_real_one_so_a_linked_parent_still_resumes`
  (F5, both POSIX); in `tests/cli/test_withdrawn_default_sweep_434.py`,
  `test_every_resolution_call_is_classified` — the one failure of review round
  1's full-suite run: `model_pin_route_moved`'s `resolve_route` call was
  unclassified, and is `typed` — and
  `test_a_resumed_childs_pin_resolves_as_typed_even_under_the_seed` (F1).
- **D10** — `test_a_plan_agent_resumed_under_a_yolo_parent_runs_at_plan` (TUI and
  `-p`: the grant, the argv and the record say `plan`, while a fresh delegation
  under the same parent gets `yolo`),
  `test_an_agent_granted_edits_resumed_under_yolo_is_disclosed_at_its_own_posture`,
  `test_an_agent_a_human_let_edit_may_be_let_edit_again_and_no_further`,
  `test_a_plan_agent_of_a_declaring_profile_resumes_with_no_dialog`,
  `test_the_pre_filter_asks_the_same_predicate_with_the_ceiling`,
  `test_the_ceiling_only_ever_takes_the_widening_rung_away` (constraint 7 over
  every parent, approval mode, scope and UI, with no ceiling and each ceiling),
  `test_the_resume_dialog_body_in_full`, `test_the_resume_dialog_cannot_be_forged`,
  `test_the_earlier_task_says_when_the_record_cut_it_or_has_none`,
  `test_with_no_ui_a_resume_is_never_asked_and_never_above_its_ceiling`,
  `test_the_resume_dialog_offers_widening_only_up_to_the_ceiling`,
  `test_widening_a_resume_grants_edits_and_a_yolo_parent_is_told_the_ceiling`.
  Round 1: `test_the_disclosure_says_resuming_and_a_fresh_one_is_unchanged`
  (F10); `test_the_permission_row_names_a_wider_first_run` (F11: five cases,
  an equal grant adding nothing, and the 14-row body); the new wording is
  pinned in `test_an_agent_granted_edits_resumed_under_yolo_is_disclosed_at_its_own_posture`,
  `test_widening_a_resume_grants_edits_and_a_yolo_parent_is_told_the_ceiling`,
  `test_the_resume_dialog_body_in_full` and `test_the_resume_dialog_cannot_be_forged`.
- **D11, D12** — `test_a_child_file_gone_since_the_hook_is_refused_and_costs_nothing`,
  `test_a_running_child_is_not_resumed_twice`,
  `test_a_session_another_process_holds_is_refused`,
  `test_a_lock_that_cannot_be_checked_says_why_not_that_it_is_in_use`,
  `test_a_conversation_switched_before_the_spawn_is_refused`,
  `test_a_grant_that_is_not_consented_is_refused`,
  `test_a_resume_refused_inside_run_charges_and_records_nothing`,
  `test_a_resume_is_a_new_key_naming_the_first_and_writes_nothing_into_the_child`
  (the child file byte for byte unchanged),
  `test_the_attach_counts_its_opening_records_like_open`; through the hook,
  `test_a_resume_continues_the_agent_on_its_own_session`,
  `test_what_resume_refusal_finds_is_refused_before_any_dialog`,
  `test_a_resume_over_the_prompt_budget_is_refused_before_the_dialog`,
  `test_a_declined_resume_runs_nothing`. Round 1:
  `test_a_file_swapped_for_another_session_after_the_hook_is_refused`,
  `test_a_directory_swapped_for_a_link_after_the_hook_is_refused` (POSIX),
  `test_a_branch_moved_off_the_first_run_after_the_hook_is_refused`
  (`session.move_to(None)`), `test_a_target_that_changed_under_its_id_is_refused`
  and, through the hook and `execute()`,
  `test_execute_runs_the_hooks_lookup_again` (F3) — each asserting that no
  process ran, no budget was charged and no record was written;
  `test_the_refusals_that_said_only_why_now_say_what_next` (F13);
  `test_a_resumes_registry_row_names_its_child_from_the_insert` (F14, verify
  m2). Round 2: `test_a_record_that_changed_what_the_dialog_showed_is_refused_at_the_launch`
  (G2: after the target was found, every line of the first run rewritten to
  name another profile or another earlier task — refused before `_run`, with
  nothing spent; it launched under a `_same_target` that compared only the
  key, the path and the limits); `test_the_refusals_that_said_only_why_now_say_what_next`
  now holds `RESUME_NOT_YOURS` too and pins its sentence (G4).
- **D13** — `test_the_resume_task_is_the_preamble_then_the_instruction`.
- **D14** — `test_agent_resume_real_child_610.py`,
  `test_a_resumed_child_sees_its_stopped_call_as_no_result_never_as_success`
  (POSIX only): a real parent session in a bucket, the real runtime and
  `PrintChannel`, a real child on `openai-completions` against a local SSE
  server that records each request; the call is cancelled while the child's
  `bash` runs, then found and resumed. The resumed request's roles are user,
  assistant, tool, user; the tool message answers `call_sleep_1` with "No result
  provided"; the last user message is the preamble then the instruction; and the
  child's file holds no tool result.
- **D15** — `test_a_resume_adds_to_the_delegations_cost_and_counts_as_a_run`
  (the reopened parent's `get_session_stats`: two runs, none pending, both runs'
  tokens and cost).

Mutation checks, recorded in the lanes' notes. Lane A, each restored after: no
floor in `resume_granted` → 2 failures; no `rel` check → 2; `open()` in place of
`attach()` → 4; the transform's "No result provided" changed to "ok" → the
real-child test fails on the wire assertion. Lane B: 20 mutants, all killed (run
before the lone-surrogate refusal was added, which has a test of its own) —
the ceiling taken out of `_grant_for`, of `_may_widen`, of the pre-filter's
`consent_is_required` (it survived until
`test_the_pre_filter_asks_the_same_predicate_with_the_ceiling` was added) and of
`request_resume_consent`; the profile mismatch unchecked; a `run` with an `id`
ignored; an unknown action run; the resume budget at the full task limit; a
resume `cwd` allowed; a path id allowed; no `Session:` line on a success or on an
ok member; the old guidance sentence everywhere; the guidance profile
unsanitised; `resume_refusal` or the budget skipped at the hook; a resume
dispatched as a fresh spawn; the dialog preview unsanitised; no cut marker; a
resume sent to the fresh dialog. Review round 1's code lane: 25 mutants, all
caught, each file restored from its saved bytes and checked by sha256
(`610-fix-r1-code-notes.md`, "Mutation results") — the first run read from
the latest run or from its own last line, no agreement check, no start check;
no lookup at execute time, no `_same_target`; the placement from `abspath`
(the rule again since G1), no `realpath` check, no link-count check; no cwd
identity; the old header rule, and a split on `\n` alone; no NUL check; no
channel check; the spawn-key check off; `session_id=None` at the insert; a
relative `profile_source` accepted, or recorded without `abspath`;
"delegating" at either disclosure door; no first-run suffix; the old guidance;
the old cancel head; the `id` description without the heading; the lock
refusal without its next step. Review round 2's code lane ran the round-2
verifier's 39 mutants again over the verifier's test files (343 tests by then),
with the two lines G1 rewrote given new patterns and six more added: 45, all
caught, each file restored from its saved bytes and checked by sha256
(`610-fix-r2-code-notes.md`, "Mutation run"). Among them are the five that had
survived review round 2 — the child session grouped by a run's last line,
first runs picked by the last line's `resumes`, the listing's profile and
preview taken from the last line, and `_same_target` comparing only the key,
the path and the limits — and G1's, G2's and G4's own: the resolved placement
(`c6e766bd`'s line), the file's `realpath` compared with its own path
(`c6e766bd`'s rule), the comparison built from the children's directory's
`realpath`, no `lstat` of the children's directory, no symlink check of the
file, `_lineage`'s chain checked by the last line's `resumes`, and
`RESUME_NOT_YOURS` without its next step.

Measured by lane A on macOS: `SessionWriterLock(timeout=0)` (filelock 4.0.1)
sees a second instance in the same process as contention (`False`, no error,
0.0 s), and a symlinked sidecar gives `OSError(ELOOP)` on `.error`.

What the lanes ran: their targeted files only. Lane A ran on CPython 3.12.13
(macOS; its notes). Lane B also ran its seven touched and related files on
3.11.15, 3.13.13 and 3.14.5, 800 passed on each — reported in its result and
recorded in `610-progress-2026-10-30.md` ("구현"), not in its notes. Review
round 1's code lane ran `tests/agents_ext` on 3.12.13 (2 498 passed, 11
skipped) and its 14 changed and related test files on 3.11.15 and 3.14.5
(1 300 passed, 10 skipped, on each; `610-fix-r1-code-notes.md`); its docs lane
ran the documentation and CHANGELOG tests (586 passed, 20 skipped;
`610-fix-r1-docs-notes.md`). Review round 2's code lane ran the three #610
files — 282 test cases, 267 before it — on 3.12.13, 3.11.15 and 3.14.5 (282
passed on each), `tests/agents_ext` and `tests/agents` on 3.12.13 (2 637
passed, 11 skipped), and the round-2 verifier's false-refusal probes as its
oracle (21 passed, their three link cases resumed;
`610-fix-r2-code-notes.md`); its docs lane ran the same documentation and
CHANGELOG tests (586 passed, 20 skipped; `610-fix-r2-docs-notes.md`). Not run
by the lanes: the full suite (each review round ran it, below), Windows,
Linux, the CI legs.

### Review round 1 (`54cd7f61`)

Four detached worktrees at that commit, run at once
(`610-progress-2026-10-30.md`, "리뷰 1회차"):

- **The full suite (r0)**, CPython 3.12.13: `1 failed, 19092 passed, 49
  skipped, 114 warnings in 865.74s`. The failure was
  `tests/cli/test_withdrawn_default_sweep_434.py::test_every_resolution_call_is_classified`:
  `model_pin_route_moved`'s `resolve_route` call was not in its table, a test
  no lane had run (F1).
- **Codex** (`codex exec -s workspace-write`, 255k tokens, ended within its
  usage limit): blocker 0, major 8, minor 2
  (`610-review-r1-codex-2026-10-30.md`). Its probes assert the
  counterexamples, 23 passed (`610-review-r1-codex-probes.py.txt`, output in
  `610-review-r1-codex-probe-output.txt`). What it found correct: a new
  spawn's argv byte-identical to `f3e76fbb` over 972 combinations; of two
  overlapping resumes, one admitted and the budget charged once (the `agent`
  tool is `execution_mode="sequential"`); every built-in adapter's transform
  giving a cut call `No result provided` as an error; a real child's first
  run and resume counted as `runs=2`, with no double count.
- **An independent check by a fresh Claude**: blocker 0, major 1 (M1: no test
  held the ceiling to the first run rather than the latest resume — the mutant
  `data = latest.data` survived), minor 6, and documentation mismatches
  (`610-review-r1-verify-2026-10-30.md`). It measured `tests/agents_ext`,
  `tests/agents` and related files at 2 712 passed, 11 skipped (3.12), the new
  files on 3.11.15 and 3.14.5 at 337 passed on each, a new spawn's argv
  identical to `f3e76fbb` over 1 296 combinations, and 27 of 30 mutants
  caught.
- **The live checks L1–L5** (Live, below).

Fix round 1 answered it: F1–F15 in the code (`0b4bb4cf`), D1–D12 in the
documents (`c6e766bd`).

### Review round 2 (`c6e766bd`)

Three detached worktrees at that commit, run at once; Codex took part in round
1 only, by the owner's rule (`610-progress-2026-10-30.md`, "리뷰 2회차"):

- **The full suite (r1)**, CPython 3.12.13 (`env -u VIRTUAL_ENV uv run
  --no-sync pytest -p no:cacheprovider -q -rfE`): `19135 passed, 49 skipped,
  114 warnings in 854.20s`, exit 0.
- **A check of the delta `883ded81..c6e766bd` by a fresh Claude**: blocker 0,
  major 0, minor 4 (`610-review-r2-verify-2026-10-30.md`; its probes are kept
  in the main checkout's `.omc/specs/610-review-r2-verify-probes/`,
  untracked):
  - m1, a regression from `883ded81`: a parent file that is a link, or a
    bucket linked to a directory whose name is not bucket-shaped, had every
    resume refused, with a sentence that did not fit (D6; G1);
  - m2: 5 of its 39 mutants survived, all on F2's and F3's forged-record
    paths, none of them widening a resume (G2);
  - m3: the comment on `profile_source` in `ChildLimits.from_record` was
    wrong (G3);
  - m4: `RESUME_NOT_YOURS` gave no next step (G4).

  What it found correct: F1–F15 as listed, and 34 of its 39 mutants caught;
  one resume door (`resume_granted` has one caller, in `extension.py`), with
  F3 and F8 at the hook and at execute alike; Codex's nine counterexamples of
  round 1 refused, with nothing spent; no false "records disagree" over
  records the real hook and `execute()` wrote (its probes, 22 passed);
  resumes under `/tmp`, `/var/folders`, a linked HOME, a relative agent
  directory and a linked parent cwd, the recorded cwd always the real one; the
  `run` path's argv byte-identical across `f3e76fbb`, `883ded81` and
  `c6e766bd` over 20 000 sampled combinations of 16 dimensions (5 599 distinct
  argvs), and, compared with `f3e76fbb` only, through `/agents run` and RPC
  over 5 000; the documents' quotes
  matching the code, and the guide's two copies identical; and `pyright
  --pythonplatform Windows` reporting 0 errors in product files.
- **The live re-check**: 13 resumes, none refused (Live, below).

The lead's verdict on the live re-check: nothing to fix. The model's
behaviour is recorded under Live, and the resume result's missing "Tools not
granted" note is D9's design (limit 16). Fix round 2 answered the four
minors: G1–G4 in the code (`00708c39`), E1–E4 in the documents.

### Review round 3 (`b5b98267`)

The last check; by the owner's rule there is no third fix round, and the lead
closes what it finds narrowly (`610-progress-2026-10-30.md`, "리뷰 3회차"):

- **A check of the delta `9cdf76d0..b5b98267` by a fresh Claude**: blocker 0,
  major 0, minor 3, and two wording nits (`610-review-r3-verify-2026-10-30.md`):
  - m1: nothing held the listing's status word and its newest-first order to
    the latest run — `_last_status(first.data)` and `return first, first`
    each left `tests/agents_ext` and `tests/agents` passing. The lead added
    `test_the_listing_orders_and_words_each_agent_by_its_latest_run`, which
    fails on both;
  - m2 and m3: limits 17 and 18;
  - the nits: D6's "at the parent's level" now reads "at or above", and
    review round 2's 5 000 `/agents run` and RPC combinations are said to be
    compared with `f3e76fbb` only.

  What it found correct: G1 over 11 setups at or above the parent's
  directory, each under an unresolved `/tmp` and `/var/folders` root — 22 of
  22 resume with the first run's `--session` string (52 passed; 53 on 3.11.15
  and 3.14.5, with a check of the tree imported); 24 of 24 links below the
  parent's directory refused at the hook and at execute, with nothing spent;
  round 2's oracle, 21 passed; a real child resumed twice; the writer lock
  seen through either spelling; 50 of 54 mutants caught; G4 pinned and quoted
  verbatim; the `run` path's argv identical to `f3e76fbb` over 20 000
  combinations through `spawn_granted` and 5 000 through `spawn`; every number
  and quote in the Status paragraph, the review sections and Live matching
  its record; and the three #610 files at 282 passed, `tests/agents_ext` and
  `tests/agents` at 2 637 passed, 11 skipped.
- **The full suite** at `b5b98267`, CPython 3.12.13: `19150 passed, 49
  skipped, 114 warnings in 848.51s`.
- **A live smoke** at `b5b98267`, scripted provider, run by the lead with
  round 2's drivers (kept in `.omc/specs/610-live-drivers/r3/`): L1 on the
  OpenAI wire — Esc stopped the child in 1.2 s, the resume ran on the first
  run's `--session`, and the parent finished; L3 with SIGTERM — the child
  exited 143, the parent restarted with `-p --session`, and the child resumed
  on the same file (7, then 9 lines).

### Live

Two rounds: round 1 at `54cd7f61`, in review round 1
(`610-live-r1-report-2026-10-30.md`), and round 2 at `c6e766bd`, after fix
round 1 (`610-live-r2-report-2026-10-30.md`). A subagent ran the drivers and
captured what they saw; the lead judged it. The drivers start `uv run
--no-sync --project <worktree> aelix …` from a worktree at that commit, with
HOME and `AELIX_CODING_AGENT_DIR` under each run's own folder and a scratch
cwd, and capture the TUI's screen as text grids. The scripted provider is
#604's mock, which tells the parent from the child by whether the `agent` tool
is offered. The real model is `anthropic/claude-haiku-4.5` on OpenRouter, for
the parent and the child, with a pass-through tap on `httpx.AsyncClient.send`
(`tap/sitecustomize.py`, on `PYTHONPATH`) that writes POST bodies, tagged by
process and depth; neither round's captures hold the key (both reports
checked). The drivers and their output are in the main checkout's
`.omc/specs/610-live-drivers/` (untracked), round 2's under `r2/`. No live
check ran after fix round 2.

**What #610 decides.** Each check's criterion held in both rounds. Round 1's
findings on wording — the guidance read as an order, the status line said
"delegating" for a resume, the dialog hid a wider first grant (live items 6,
3 and 10) — became F9, F10 and F11, and round 2 saw each in place.

- **L1 — TUI, scripted provider, Esc, then resume** (80x50, a `yolo` parent):
  `l1_tui_esc_resume.py openai` and `l1_tui_esc_resume.py anthropic`. The
  parent is `aelix --agents --provider scripted --model s-1 --permission-mode
  yolo`, the profile `writer` with tools `[write, bash]`. The child wrote
  `notes.md`, then ran `bash("sleep 97")`; Esc removed it (in 1.19 s at
  `54cd7f61`, 1.2 s at `c6e766bd`); the scripted parent then resumed it, with
  no dialog. The resumed child's request on the OpenAI wire
  (`wire-child-3.json`, round 1):

  ```
  user "Task: write notes.md, then run the slow step"
  assistant tool_calls write(notes.md) id call_write_1
  tool call_write_1 "Successfully wrote 21 bytes to notes.md"
  assistant tool_calls bash {"command": "sleep 97"} id call_bash_1
  tool call_bash_1 "No result provided"
  user "Task: This continues your earlier work in this session. … check its effect before relying on it.\n\nNext instruction:\nFinish the slow step if it still needs doing, then reply DONE."
  ```

  The Anthropic wire carries the cut call as
  `{"type":"tool_result","tool_use_id":"toolu_bash_1","content":[{"type":"text","text":"No result provided"}],"is_error":true}`.
  The child file holds both runs and no tool result for the cut call. The
  receipts are the first run's start and `cancelled` settle, then the
  resume's start and `ok` settle under a key of its own whose `resumes` names
  the first, both naming the same child file, the resume's `task_preview` its
  instruction. The resumed child ran on the same `--session`, with `--tools
  bash,write` and `--permission-mode yolo`. There is one write: the scripted
  child cannot choose to repeat it, which L2 checks with a real model. At
  `c6e766bd` the report the parent's model got after Esc
  (`wire-parent-2.json`; the Anthropic text is identical) reads (F9):

  ```
  [aelix system report — not user input] The `agent` call you made was stopped when the turn was cancelled. The stop was intended: resume or repeat that work only if the user asks you to. What it had done:

  <cancelled-delegation-report>
  The delegated agent did not finish: the turn was cancelled
  Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls; if it should go on, continue it with agent(action="resume", profile="writer", id="83d70462-6867-4f71-95e4-38a95930a8fb", task=…).
  …
  write(notes.md) ok
  bash(sleep 97) (no result)
  …
  Session: 83d70462-6867-4f71-95e4-38a95930a8fb
  [agent writer · s-1 · aborted · yolo · 2 turns · 24 in / 6 out · 3.6s]
  ```

  and the status line read `delegating to writer at yolo — 1 task, from
  /tmp/610-live-r1/runs/l1-openai/agen` for the first run and `resuming writer
  at yolo, from /tmp/610-live-r1/runs/l1-openai/agent/agents/write` for the
  resume (F10; both cut at column 80, below). The resumed child's `--session`
  read `/private/tmp/610-live-r1/runs/l1-openai/home/.aelix/sessions/…/sub-79e0d1349fa8.jsonl`:
  the resolved spelling F3's derivation gave, where at `54cd7f61` the lookup
  still derived the file as allocation did (L4's resume there: `--session
  /tmp/610-live/runs/l4-widen/…`). G1 restored allocation's spelling (D6);
  the tests and the oracle probe check it (Verification), and no live run has
  since.
- **L2 — a real model, TUI, Esc, then resume.** The parent is `aelix --agents
  --provider openrouter --model anthropic/claude-haiku-4.5 --permission-mode
  yolo` (as round 2 records it; its flow is round 1's with one turn added).
  The first prompt delegates three steps once to `worker2` — write
  step1.txt, `bash sleep 47`, write step3.txt — and Esc is pressed while
  `sleep 47` runs. Round 1 (`l2_real_tui.py`) then asked "Continue that agent
  so it finishes its remaining steps, then tell me what it reported.", and
  haiku called
  `{"action": "resume", "profile": "worker2", "id": "8db68c0b-db47-444e-9dd8-a7bd589b83c0", "task": "Continue: Step 3 remains incomplete. Use the write tool to create step3.txt containing the line three. Then reply DONE."}`.
  The resumed child's first request was its original task, the write of
  step1.txt, `Successfully wrote 3 bytes to step1.txt`, the `bash` call of
  `sleep 47`, `No result provided`, then the preamble and the instruction. It
  made one call, the write of step3.txt, and said "DONE."; it did not rewrite
  step1.txt (its mtime is before the Esc) or run the sleep again. Its argv had
  `--tools bash,ls,read,write` and `--permission-mode yolo`. Round 2
  (`l2_real_tui_neutral.py`) put one turn, "what happened?", between the Esc
  and the request to continue; the parent answered it in text, with no tool
  call and no child (Model behaviour, below). On the request it resumed the
  agent with
  `"task": "Continue from Step 3: use the write tool to create step3.txt containing the line three. Then reply DONE."`;
  the resumed child's first request had round 1's shape, and the child ran
  `ls`, wrote step3.txt and said "DONE", step1.txt's mtime unchanged and the
  sleep not run again. This is the issue's done criterion, and it held in both
  rounds.
- **L3 — `-p` twice, scripted provider** (`l3_p_twice.py TERM`). Run 1 is
  `aelix --agents --provider scripted --model s-1 --permission-mode yolo -p
  "DELEGATE-IT …"`, and the child gets SIGTERM during `sleep 98`; run 2 is
  `aelix --agents --provider scripted --model s-1 -p --session <parent file>
  "RESUME-IT continue that agent and finish"`. Run 1's result as the parent's
  model got it at `54cd7f61`:

  ```
  The delegated agent did not finish: exited with code 143
  Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls, then continue it with agent(action="resume", profile="writer3", id="9e43bfef-9a61-45a7-b22e-4f584a7215ee", task=…).
  …
  Session: 9e43bfef-9a61-45a7-b22e-4f584a7215ee
  [agent writer3 · s-1 · error · yolo · 2 turns · 24 in / 6 out · 2.0s]
  ```

  At `c6e766bd` its guidance reads `… Check the changed files and its tool
  calls; if it should go on, continue it with agent(action="resume",
  profile="writer3", id="3555d4ba-…", task=…).` Run 2 resumed the child
  through the receipts after the restart, on the same file, which grew from 7
  to 9 lines; the request had L1's shape; the child ran at `--permission-mode
  plan` (round 2: `--tools bash,write --permission-mode plan`) and returned
  `[agent writer3 · s-1 · ok · plan · …]`. Run 2 named no `--permission-mode`,
  so a new delegation would get `plan`, and so did the resume — D9's
  intersection, which the guide spells out for `-p` ("The posture is the lower
  of the two"). With SIGKILL (`l3_p_twice.py KILL`) the result said `killed by
  signal SIGKILL`, with the same guidance, and the child was resumed as well
  (round 2: its file grew from 7 to 9 lines).
- **L4 — not widened**, read off `ps -axww` (`l4_not_widened.py widen`,
  `l4_not_widened.py narrow`, `l4_third.py`). Round 2's table; round 1 saw the
  same:

  | Run | Parent flags | Child argv |
  |---|---|---|
  | widen 1 | `--tools read,ls,agent` | `--tools ls,read --permission-mode plan` |
  | widen 2, resume | `--permission-mode yolo --tools read,ls,grep,write,bash,agent` | `--tools ls,read --permission-mode plan` |
  | widen 2, fresh control | same | `--tools bash,grep,ls,read,write --permission-mode yolo` |
  | narrow 1 | `--permission-mode yolo` | five tools, `yolo` |
  | narrow 2, resume | `--permission-mode plan` | five tools, `plan` (no fresh control in this direction) |
  | l4_third, resume | `--permission-mode yolo` | five tools, `yolo` |

  Widen 1 ran at the default posture. Round 1's whole argv of the widen
  resume:

  ```
  python -m aelix_coding_agent --mode json -p --session /tmp/610-live/runs/l4-widen/home/.aelix/sessions/--private-tmp-610-live-runs-l4-widen-work--/2026-10-29T18-39-20-579Z_101fd45f-…/sub-017bc0bca24d.jsonl --model s-1 --provider scripted --tools ls,read --no-extensions --append-system-prompt-file …/prompt-scout4.md Task: This continues … Look once more and report. --permission-mode plan --no-agents
  ```

  `l4_third`'s start receipts read `yolo`, `plan`, `yolo`: the resume
  narrowed to `plan` in between did not lower the ceiling (D9: the first run's
  limits, not the last resume's).
- **L5 — the resume dialog in the TUI at 80 columns** (CLAUDE.md rule 9):
  `l5_dialog.py 50`, `l5_dialog.py 24` and `l5_dialog.py 50 auto`, and in
  round 2 `l5_tall_fresh.py 50` and `l5_tall_fresh.py 24` beside them. The
  profile `editor5` has `approval_mode: ask` (or `auto`) and tools `[write,
  read, ls]`; the parent runs at the default posture; "Allow file edits" is
  chosen in the fresh dialog and again in the resume dialog. Round 1's resume
  dialog at 80x50, before any key:

  ```
  Resume agent 'editor5'?

  Session:    f537cfc0-72c1-455f-8f75-10ab8c71141f
  Profile:    editor5 (user scope)
  Source:     /tmp/610-live/runs/l5-80x50/agent/agents/editor5.md
  Directory:  /private/tmp/610-live/runs/l5-80x50/work
  Permission: plan
  Model:      s-1

  Earlier task (written by the model):
  Write notes5.md with one line, then report.

  Next instruction (written by the model, not by you):
  Add a second note in notes5b.md, then report.
  ───────────────────────────────────────────────────────────────
  ▸ Run read-only (plan)
    Allow file edits for this run (auto-accept-edits)
    Cancel
    (1/3)
  ───────────────────────────────────────────────────────────────
  ↑/↓ move · type to filter · Enter select · Esc cancel
  ```

  The body is 14 rows with the model row (D10). Nothing in it but the
  widening option said that the first run had more; at `c6e766bd` the row
  reads `Permission: plan (first run: auto-accept-edits)` at 80x50, at 80x24
  and with `approval_mode: auto` (F11), the fresh dialog still reads
  `Permission: plan`, and every other row is round 1's. Answered, the resumed
  child ran with `--tools ls,read,write --permission-mode auto-accept-edits`
  and wrote notes5b.md; in round 1 the receipts gave both runs `granted_mode:
  auto-accept-edits`.

**Model behaviour, which #610 does not decide.** The lead's verdict after
round 2: nothing in #610 to fix, and the behaviour recorded here.

- **A killed child resumed unasked** (`l3_real.py`: `-p` twice with haiku,
  `yolo` in both runs). At `54cd7f61`, after the killed child's result, haiku
  resumed the agent in the same run, though the prompt said "Call the agent
  tool exactly once"; the guidance then read "Check the changed files and its
  tool calls, then continue it". F9 made it conditional, and at `c6e766bd`
  haiku still wrote "Now I'll resume the agent to complete Step 3:" and
  resumed in the same run. The cancel report's new head covers Esc only; a
  kill gets only the conditional guidance. After an Esc, round 2's prompt that
  asked for nothing ("what happened?", L2) got no resume:

  ```
  The agent has a session ID and can be resumed to complete Step 3. However, you
  haven't asked me to resume it — you only asked what happened. …
  ```

  That reply repeats the rule back to the user and mentions an id the user
  cannot see.
- **A cut `sleep` said to have finished.** The resumed children only checked
  files with `ls`, then said so anyway — at `c6e766bd`, L2's "Good, step1.txt
  exists. The sleep 47 command completed.", and `l3_real`'s "Step 1 and 2
  completed successfully.", later "The sleep 45 command has completed (it
  finished in the previous session)" — and `l3_real`'s final answer to the
  user read "2. Completed the sleep 45 command". At `54cd7f61` the L2 parent's
  summary told the user "✓ Ran sleep 47 and waited for it to finish"; at
  `c6e766bd` it did not repeat the claim. In both rounds the parent's resume
  instruction skipped step 2 ("Continue from Step 3: …" at `c6e766bd`), and in
  `l3_real`'s run 2 at `c6e766bd` it said "Wait for the sleep 45 command to
  complete", with no sleep running. The preamble (D13) already tells a resumed
  child that a tool call showing no result may or may not have run.

**Existing behaviour that #610 does not change.** Seen in both rounds; the
lead's verdict both times was to leave it.

- **The tool card cuts a resume's arguments**: `● agent(action='resume',
  id='84a6ffb2-632f-47c6-9b89-d25554d639a4',` and, on the next row,
  `profile='writer', t…)`. `_compact_args` in `tui/render.py` caps the
  arguments at 80 cells, and the 36-character id uses up the room. Under a
  `yolo` parent there is no dialog, so the user does not see the instruction at
  all. The TUI's core does not know the `agent` tool; it is a follow-up
  candidate.
- **The status line's notice row is cut at column 80 with no `…`**, a fresh
  delegation's as well as a resume's; F10's row stops inside the file name,
  `…/agents/write` for `writer.md` (the whole row is 84 characters) and
  `…/agents/worker` for `worker2.md`. The status bar's last field is cut at
  80 columns too (`🧠 of`, `✱ anthropic/claude-`).
- **At 80x24 a 21-row dialog loses its bottom rule and hint row**, a fresh one
  as well as a resume (round 2's L5; a 367-character task wraps a fresh
  dialog's into 5 rows):

  | Dialog | Rows | Shown at 80x24 |
  |---|---|---|
  | fresh, task on 1 row | 17 | 17 |
  | resume | 21 | 19: bottom rule and hint gone |
  | fresh, task on 5 rows | 21 | 19: bottom rule and hint gone |

  Both 21-row dialogs end, at 80x24:

  ```
    Cancel
    (1/3)
  ⠴ Working… (2s · esc to cancel)
  ```

  The options still show and the answer works. Round 1 had compared the
  resume dialog with a 17-row fresh one only, and took the loss for the
  resume's.
- **#619: a `bash` command outlives its child on macOS** (limit 14). Esc left
  `sleep 97`, `sleep 96` and `sleep 47` running with ppid 1; SIGKILL left
  `sleep 99`, which was still alive when run 2 resumed the child; SIGTERM left
  nothing (round 2, as in round 1). The resume was not refused. The drivers
  killed each leftover.
- **After Esc the screen shows only `✖ Operation aborted`.** The agent's id
  and the resume guidance go to the model only, with the next prompt (#132's
  follow-up).
- **Consecutive user-role messages on the Anthropic wire.** The resumed
  child's request carries the cut call's `tool_result` and then the preamble
  as a user message of its own (round 1); after Esc, the parent's next request
  carries a `tool_result`, the cancel report and the new prompt as three
  user-role messages in a row (round 2). The lead judged it the provider
  transform's existing shape, the one a new prompt after any Esc has.

Not checked live: Windows and Linux, and anything after fix round 2. Still to
run at the commit that merges: the full suite and the CI legs.
