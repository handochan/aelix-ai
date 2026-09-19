# 0243. A delegated child keeps its own session, and the parent keeps the receipt

Status: Accepted (2026-09-19)
Date: 2026-09-19
Supersedes/relates: ADR-0242 (the session record rules; this is their first
consumer), ADR-0197 (the subagent-runtime seam; its one-shot argv carried
`--no-session`, **amended**), ADR-0201 (the rpc channel, which "must append
`--no-session`", **amended**), ADR-0199 (fan-out governance; a parallel or
chain call now shares one summary budget, **amended** in §(i)), ADR-0235 (pi is
a reference, not a target, so the divergences below are recorded, not argued).
Owner decision #6 in `docs/05-post-beta-direction.md` §11: agents default-on
needs, among other things, **child sessions recorded** and **only the result in
the parent's context**.
Issue: #199. Follow-up: #296 (a TUI viewer for child sessions).
Design spec: `.omc/specs/199-design-2026-09-19.md` (rev 1), with its critique
(`199-design-critique-2026-09-19.md`) and research map
(`199-surface-map-2026-09-19.md`) beside it.

A delegated child did its work, spent money, and then existed nowhere. The
parent kept a few lines of text about it, and the session's own cost figures
never saw the spend.

## What was broken on `main` (2026-09-19)

One real delegation (`docs/05` §11, "6번 확인 결과": openrouter,
`anthropic/claude-haiku-4.5`, `--permission-mode plan`, a scratch
`--session-dir`) succeeded — `agent explorer · ok · plan · $0.0110 · 5.7s` —
and left one file in the session directory, the parent's, 2,409 bytes.

1. **The child's transcript was gone.** Both channels started it with
   `--no-session`: the print channel through product-core
   `agents/resolver.py` `profile_to_argv` (the one-shot prefix), the rpc
   channel through its own flag in `build_rpc_child_argv`.
2. **The parent kept only the tool result's text.** `loop._to_tool_result_message`
   drops `details` (#168), so no spawn id, no status and no usage survived as
   data. The child's $0.0110 lived in the result's footer text and nowhere in
   the session's usage, so the footer, `/cost`, `/stats`, History and RPC
   `get_session_stats` all reported the parent's own spend only.
3. **A cancelled delegation wrote nothing, and so did `/agents run`**, on every
   path.
4. **The truncation marker was false.** A capped summary ended "Full output
   preserved in tool details." — true only until the call returned, since
   `details` is never persisted.
5. **A batch could fill the parent's context in one tool result.** Each child
   was capped at `output_cap` (51,200 bytes) and a call was not capped at all:
   8 × 51,200 bytes rendered a 410,318-byte tool result in the design
   critique's probe, `estimate_tokens` 102,579 — 92% of `openai/gpt-4o`'s
   111,616-token auto-compaction threshold, 56% of
   `anthropic/claude-haiku-4.5`'s 183,616. A tool result is never a compaction
   cut point, so that turn could not shed it.

## Decision

### 1. Where a child's session lives

- **Beside its parent, in a directory named after the parent's file:**
  `<bucket>/<stem>.jsonl` → `<bucket>/<stem>/<spawn id>.jsonl`, e.g.
  `…/2026-09-19T11-39-36-161Z_1f5ccf32-….jsonl` →
  `…/2026-09-19T11-39-36-161Z_1f5ccf32-…/sub-bbb216301009.jsonl`. Only an exact
  `.jsonl` suffix is stripped; any other name gets `.children` appended, so the
  directory never shares the parent file's name
  (`aelix_agents/child_session.py` `place_children`).
- **No picker ever sees a child.** `JsonlSessionRepo.list` and
  `find_most_recent` read only the `*.jsonl` files directly inside a bucket and
  skip directories, so `--continue`, `/resume`, the startup picker and id-prefix
  lookup never offer one — pinned with the child made the newest file under the
  root.
- **The name is the spawn id** (`sub-<12 hex>`, 22 characters with the suffix),
  not `<timestamp>_<uuid>`: the header already carries both, and the short name
  keeps a 111-character cwd under Windows' 260-character `MAX_PATH`, `.tmp`
  staging name included.
- **Every path is absolute**, resolved in the parent. A parent opened with a
  relative `--session` would otherwise hand a child that runs in a
  subdirectory a path that names nothing there.
- **A parent outside a bucket gets no child file.** The design refused only a
  parent sitting directly in the sessions root (its `<stem>/` would become a
  top-level bucket that the global `list()` scans). The extension cannot see
  `--session-dir`, so the rule it can apply is the conservative one: the
  parent's own directory must have a bucket's shape (`--…--`, the only shape
  `JsonlSessionRepo` creates). A parent the repo created always passes; a
  hand-placed one (`aelix --session /x/y.jsonl`) runs its children
  `--no-session` and its start record says `error: "parent outside a bucket"`.
- **A parent with no file** (`--no-session`, an in-memory session) runs its
  children `--no-session`, as before, and still takes the records in memory, so
  its stats include the children for the life of the process. With
  `AELIX_CODING_AGENT_SESSION_DIR` set, no file appears anywhere.
- Files are 0600 in a 0700 directory (ADR-0242's `LocalFileSystem`). **There
  is no retention policy** — sessions have none — so every delegation now keeps
  a full transcript (up to `MAX_DELEGATIONS_PER_PROMPT` = 12 per prompt through
  the model door), and deleting a parent by hand leaves its `<stem>/`
  directory behind.

### 2. The parent publishes the child's file before the child exists

`JsonlSessionStorage.create(fs, path, cwd=<child cwd>, session_id=<uuid4>,
entries=[origin])` — one atomic publish (ADR-0242 §4), so even a child that dies
before its first turn leaves a findable header and origin, and the parent knows
the path without asking the child. An existing file of that name is refused
rather than replaced. Every failure (an `OSError`, a path too long, a name
taken) is a result, never a raise: the child runs `--no-session` and the start
record says why.

- The origin is the child file's first entry, `parentId: null` (the child's
  first `get_branch()` walks to the root, and an id missing from the file would
  fail it): `aelix.child_origin` `{v: 1, key, parent: {session_id, path},
  tool_call_id, index, mode, profile, permission_mode, aelix_version}`.
- The header's `parentSession` stays unset. That field is fork lineage, and a
  delegated child is not a fork of its parent; neither `parent_id` nor
  `parent_session_path` is reused for the spawn link.
- **The child runs with `--session <absolute path>`** instead of `--no-session`
  (`profile_to_argv(…, session_path=…)`); the rpc channel swaps its own
  `--no-session` for the same flag. Nothing forwards `--session-dir`: the parent
  chose the file. `/agents show` still renders the profile's flags only — the
  session flag, like `--permission-mode`, the trust flags and `--no-agents`, is
  spawn-time state a dry run cannot know.

### 3. What the parent session records

Records exist for **admitted** spawns only, those that got a registry row.
Hook refusals, human declines, `/agents run` declines, in-`_run` refusals
(drain, live cap, budget), batch-wall and `TaskTooLarge` refusals and chain
steps that never ran spent nothing; their tool-result text already persists
them. Each record is one ADR-0242 `CustomEntry` line with everything in `data`;
`key` is the spawn id (a `tool_call_id` is one per `agent` call and is not
unique on the Google adapter across restarts).

| Record | `data` |
| --- | --- |
| `aelix.child_session` start | `{v, key, phase: "start", tool_call_id, index, mode, profile, task_preview, child, requested_model, permission_mode, aelix_version, error}` — `child` is `{session_id, path, rel}` or `null`, and `error` says why it is `null` when the parent does have a file |
| `aelix.usage` pending | `{v, key, state: "pending"}` — written right after the start, **before the child can spend** |
| `aelix.child_session` settle | every start field except `error`, then `status` (`ok` / `error` / `timeout` / `aborted` / `cancelled`), `model` and `provider` (what actually ran, from the envelope), `usage: {input, output, cache_read, cache_write, cost}`, `cost_known`, `context_tokens` (a level, never summed), `turns`, `elapsed_ms`, `exit_code`, `stop_reason`, `truncated`, `summary_bytes`, `details_bytes`, `error` (the run's own, at most 300 characters) |
| `aelix.usage` final | `{v, key, state: "final", usage, cost_known}` — the same two values the settle carries |

`tool_call_id`, `index` and `mode` are the call's: `(id, 0, "single")` for a
single delegation, `(id, member index, "parallel" | "chain")` for a batch, and
`null` for `/agents run`. `rel` is `<stem>/<spawn id>.jsonl`, relative to the
parent file's directory and always `/`-separated; a reader tries it first and
`path` second, so a parent moved together with its directory still finds its
children. A settle is a complete record, never the second half of the start
(ADR-0242: the file can end between them). A reader folds each type by `key`,
last line wins: **a start with no settle is a delegation whose outcome is
unknown, and a pending with no final is spend nobody confirmed** — neither is
a zero.

The exit paths, and what each leaves (`aelix_agents/runtime.py` `_run`):

| Exit path | Writer | Records |
| --- | --- | --- |
| ok / error / timeout / aborted / exec failure (an envelope came back) — `aborted` includes `stop_all` at `/new`, `/resume`, `/fork` and quit, which kills the child without cancelling `_run` | `_run`'s `try` | start, pending, settle (the envelope's status), final |
| an exception before or inside `channel.run` (e.g. `write_prompt_file`) | `_run`'s `finally` | start, pending, settle (`error`, `"<ExcType>: <message>"`), final — and the exception still propagates |
| cancel — Ctrl+C (`harness.abort()` cancels the turn task), or the event loop ending with the turn still running | `_run`'s `finally`, under `asyncio.shield` | start, pending, settle (`cancelled`, the partial spend priced first), final |
| the process killed mid-run | — | start, pending (outcome unknown, spend unconfirmed) |
| not admitted | — | none |

Quit can take either of the first and third rows: `stop_all` runs at
`session_shutdown` and kills the child, so the channel returns `aborted`; a turn
the shutdown cancels before the channel has returned settles `cancelled`.

How `_run` writes them, and why in that order:

- The admission block (drain, `_admit_live`, budget, spawn id, registry
  insert) stays await-free and AST-pinned, and the first progress publish stays
  right after it — `batch._member`'s "admitted" flag depends on it. The
  receipt is built there, synchronously, so it exists exactly where a row does.
- Allocation and every append sit **inside** the `try`: they await, and
  between the registry insert and the `try` a cancel would leak the row, which
  `_admit_live` would then count forever. The start and the pending are
  counted one at a time, after each append returns, so a cancel while one is
  being written leaves it to the `finally`, which writes it ahead of the settle
  (at least once — the fold is last-wins by key).
- The `finally` writes ONE settle and final for a delegation that never
  settled, built from the live stream after `apply_cost_fallback` has priced it
  (a cancel never builds an envelope, so nothing else would), as its own task
  awaited through `asyncio.shield`: a second Ctrl+C cancels the wait, not the
  write. Measured in the critique: with a shield both settles of a two-member
  batch survived a second cancel; with a lock and no shield both were lost. So
  there is **no lock**: `JsonlSessionStorage` already serialises appends and
  `LocalFileSystem` never yields inside one. The runtime holds each such task,
  and `stop_all` joins them before it returns (at most `SETTLE_JOIN_SECONDS`,
  5 s), so the write a second cancel abandoned does not outlive teardown.
- **Recording never changes a delegation.** Every append is logged at DEBUG
  and swallowed. The builders are total — byte counts use `surrogatepass`, so a
  lone surrogate in a child's text cannot raise — and if one raises anyway,
  `SpawnReceipt.bare_records` writes the settle with the status and the spend,
  every other field `null`, `cost_known: false` and `error: "the record could
  not be built"`. The one thing that propagates is the cancel itself.

**The parent session is captured once per spawn.** `AgentsExtension` gains a
`session` getter, wired in `cli/entry.py` to the runtime host's **current**
session, and `_run` reads it once at the top of its `try`; every record of that
spawn goes to that object. A settle that arrives after `/new`, `/resume` or quit
(`stop_all` runs at `session_shutdown`, and the channel drains afterwards)
therefore lands in the session that ran the child, and `/agents run` records
even before any hook has fired. The most recent hook's context is only the
fallback, read under `suppress(ExtensionError)`.

**`cost_known`** is never true for a run whose usage is incomplete
(`usage_is_complete`). A child that never began a run (`agent_start` precedes
its first model request) spent nothing, so its zeros are complete. Otherwise a
run the **parent cut** — Ctrl+C (`cancelled`), `stop`/`stop_all` (`aborted`),
the deadline (`timeout`) — is never complete: the parent stops reading at once,
a request in flight then may have been billed without its `message_end`
arriving, and not even an `agent_end` proves otherwise, because the child's own
abort path emits one without a `message_end` for the request it abandoned. Any
other run is complete only when the child's **latest** run closed on its own:
`_StreamState.run_open` is last-event-wins, so a stream cut during an
auto-retry (a second `agent_start` after an `agent_end`) or a child that died
mid-turn still reads as unfinished. What an incomplete run recorded is a floor
(`≥ $X`), priced or not. Two Codex reviews found the holes this closes: a
Ctrl+C before any usage arrived recorded zeros as a confirmed `$0`, and a first
version of the fix trusted a latched `agent_end`.
A run that ended, or never began one, is known when a `message_end` carried a
cost of its own (zero counts), when the registry fallback priced the run
(`apply_cost_fallback` now returns whether it did), when the recorded cost is
positive, or when every token counter is zero; never when the cost is not a
finite, non-negative number. In practice the fallback is the evidence: no
first-party adapter reports a cost of its own today, and openrouter did not in
the live run below.

One known limit of that rule, left as it is (see *Not done here*): one
`message_end` with a cost of its own makes the whole run known, so a run whose
adapter priced some messages and not others would read as fully priced. No
first-party adapter reports a cost at all, so that needs a third-party adapter.

### 4. Session stats count child spend exactly once

The kernel learns no delegation vocabulary (the band gate still forbids the
words under `packages/aelix-agent-core/`). It learns a **usage record**:
`aelix.usage` is spend a tool reported for work it ran outside the session's
own model calls. `AgentHarness.get_session_stats` reads the branch **once**
(for the compaction check and the records both), collects the `data` of every
`CustomEntry` whose `customType` is `aelix.usage` in root→leaf order, and
`harness/_session_stats.py` `fold_usage_records` folds it. A
`custom_message` of the same name is not a record.

What the fold reads, exactly, and nothing else:

1. `state` — only `"pending"` and `"final"`. Any other value, or a `data`
   that is not an object, is skipped and makes the cost unknown: it may be spend
   a newer writer recorded in a shape this reader predates.
2. `key` — a non-empty string groups lines, and the **last line of a key
   wins**: a line appended twice (the store is at-least-once, ADR-0242 §3)
   counts once, and a final supersedes its pending. A line without one counts
   on its own.
3. A key whose last line is `pending` adds nothing, counts in `pending`, and
   makes the cost unknown.
4. A `final` adds `usage.input` / `output` / `cache_read` / `cache_write` to the
   token flows and `usage.cost` to the cost. A count must be a whole, finite,
   non-negative number (`12.0` reads as 12, `true` and `12.5` do not); a cost a
   finite, non-negative number. A value that fails adds nothing and makes the
   cost unknown; the record's other numbers still count.
5. `cost_known` on a final — anything but `true` makes the cost unknown, and
   the priced part is still added, so the display can say "at least".
6. `v` is not consulted (ADR-0242 rule 1.3). A context level — `tokens`,
   `context_tokens`, `total` — is never summed wherever it appears: only the
   four flows are.

The fold is **added into** `SessionStats.tokens` and `cost`, so the footer,
`/cost`, `/session`, `/stats`, History (`stats-history.jsonl`) and RPC read one
total and none of them needs to know the records exist. `cost_known` becomes
`unpriced == 0 and cost_complete and tool_usage.cost_known`, and a pending run
renders as `≥ $X` through the existing `format_session_cost`. The breakdown
rides as the Aelix-only `SessionStats.tool_usage` (`ToolUsage(tokens, cost,
cost_known, runs, pending)`) and stays off the RPC wire, whose keys are
enumerated (`rpc_mode._session_stats_to_dict`, unchanged). The pi_parity pin on
Aelix-additive fields moved deliberately from `{"cost_known"}` to
`{"cost_known", "tool_usage"}`. The `/stats` session tab shows one line for it
— `Tools & delegated agents: 12.3k in / 1.1k out · $0.0110 (2 runs)`, and
`≥ $0.0110 (3 runs, 1 pending)` while a run is out — and `/cost` one row,
`tools & agents`, with the figure in the same style as its `cost (USD)` row (no
`$`; the label is short so a wide row — twelve runs, all pending, six-figure
token counts, a floor — still fits an 80-column terminal, pinned by a test;
bigger counts can still wrap). Either is a breakdown, already inside the totals above it.

- **The recorded cost is authoritative.** Stats never re-price a child's
  tokens against the static catalog, which could disagree with the
  registry-priced figure the delegation reported (models.json models, the
  model that actually ran).
- **Branch semantics, stated exactly.** The records are read over the whole
  root→leaf path, **including entries before the latest compaction** — so a
  compaction makes the parent's own figure a floor but not the tools' share —
  while the parent's messages are the post-compaction `_state.messages`. A
  `/tree` move stops counting records left off the path, as it does messages;
  `/fork` copies the records on its branch, and both sessions then point at the
  same child files.
- **Not delegation-specific.** Any writer of `aelix.usage` is counted,
  including an extension through `append_entry`: the `aelix.` namespace is a
  documented convention, not an enforced one (ADR-0242 rule 1.5). That is the
  point of naming the record for usage rather than for delegation.
- `aggregate.roll_up_usage` keeps only its display role, the batch's `[total]`
  line (where `tokens` is a level and takes the maximum); a test pins it equal
  to the kernel fold on the flows.
- **History totals rise from here on**, by the child spend they used to miss.
- Residual under-counts: a child's own compaction calls, retry attempts that
  reported no usage, a turn still in flight when a delegation times out, and
  whatever a cancelled child had not reported by the cancel — the parent stops
  reading its stream there. The last two are marked, not hidden: a run cut
  short is recorded with `cost_known: false`, so the total shows as a floor
  (§3).

### 5. The truncation marker says only what is true

`[Output truncated: N bytes omitted. The full output is recorded in the delegated
session.]` when the child has a file its parent allocated **and** the summary
came from the child's own stream (its answer or its own error message);
`[Output truncated: N bytes omitted.]` otherwise — a stderr tail or a spawn
error is not in that file. No path is ever written: it would put a home
directory and a user name into the model's context, and the parent's records
carry it already. The marker also flows into a chain's `{previous}`.

`SubagentResult` gains `output_recorded: bool = False` (additive and defaulted,
so `CONTRACT_VERSION` does not move). The batch renderer is a pure function
that re-caps summaries after the channel did; this is the only way it can know
whether its own marker may make the claim.

### 6. Owner decision (A.9 = c): a parallel or chain call shares one 64 KiB budget

`aggregate.BATCH_OUTPUT_BUDGET_BYTES = 65536`, split evenly over the members a
call renders (`member_output_budget(n) = 65536 // n`): 8 members get 8,192
bytes each, about 2,048 estimated tokens and still several times a typical
report (the owner's live delegation returned 129 characters in all).

- **A member's share is everything it says:** its summary and, when the error
  says something the summary does not, its `Error:` note — the note at most
  half the share, the summary the rest. An error the summary already is (a
  failed child's summary is its own error message, cut at `output_cap`) is not
  repeated at all, in either mode (`envelope.error_repeats_summary`). Before
  the #199 review both escaped: one 60,000-byte error message made eight
  members return 546,832 bytes and a single delegation 111,336, and eight
  20,000-byte errors beside their summaries 226,400.
- **The frame is outside the budget:** the header, each member's heading,
  truncation marker and usage line, and the `[total]` line — about 200 bytes a
  member. Measured with realistic usage lines, eight members at their cap
  render 67,193 bytes (`estimate_tokens` 16,786), eight failed with a
  60,000-byte error 67,144: the worst case per call drops about 6.1× from the
  410,318 bytes above.
- **Single mode keeps the profile's `output_cap`** (51,200 by default)
  alone.
- **Applied at render time only** (`render_batch_result`, re-capped with
  `envelope.recap_summary`, which strips the channel's own marker first and
  adds the two omitted counts, so the model is told the whole shortfall). A
  chain's `{previous}` hand-off reads each step's envelope summary, which keeps
  its own `output_cap`, so a step still passes the next one everything a single
  delegation would.
- What a member loses to the budget is in that child's file when the child has
  one, and the marker says so exactly then.
- The constant lives in `aelix_agents`: a delegation cap is the extension's
  policy, and the band gates forbid one in product-core.

Rejected: (a) keep the caps as they were (the worst case above stays); (b) a
16 KiB per-child default (a worst case of 8 × 16 KiB = 131 KB still, and
single-mode reports would truncate earlier for no gain).

### 7. Owner decision (A.10): viewing a child session is a follow-up (#296)

Recording alone meets "자식 세션이 기록될 것". A read-only view exists today:
`aelix --export <child.jsonl>` renders the transcript to HTML and writes
nothing to the session. A TUI surface is its own issue, #296, with its own live
check.

### 8. A child file is a record, not a resumable identity

Everything that bounded a child — its `--permission-mode` clamp, its narrowed
tools, `--no-agents`, the depth guard — was argv and environment, and none of it
is in the file. `aelix --session <child file>` (and `--fork` of one) would run
it as an ordinary top-level session at the user's own posture. So
`_build_session` prints one stderr line when the opened file's first entry is
`aelix.child_origin`, naming `aelix --export <path>` as the way to read it
without running it. A warning, not a refusal: reopening one is legitimate. It
is silent inside a delegation, where the child opens its own file with
`--session` and a stderr line would join the tail a failed delegation is
diagnosed from.

### 9. Found on the way: `/agents run` failed right after `/new`

`/agents run` typed after `/new`, `/resume` or `/fork` and before the new
session's first prompt failed with a stale-context error before consent was
asked: the consent gate read `getattr(ctx, "has_ui", False)` from the previous
session's hook context, which raises `ExtensionError("stale")` on every
attribute, and `getattr`'s default catches only `AttributeError`. A stale
context is now treated as no context — the state the first `/agents run` of a
fresh session was already in (the documented headless default: the clamp, no
prompt, never widened). No new authority state.

## Measured on the branch

Live, by the implementer on the uncommitted tree, from a scratch cwd with a
scratch `--session-dir`: `aelix --agents --provider openrouter --model
anthropic/claude-haiku-4.5 --permission-mode plan --mode json -p "<delegate one
explorer to read note.txt>"` — exit 0, the right answer, footer `[agent
explorer · anthropic/claude-haiku-4.5 · ok · plan · $0.0059 · 3.3s]`.

- The child file `…/<parent stem>/sub-bbb216301009.jsonl`, 0600 in a 0700
  directory: a version-3 header without `parentSession`, the
  `aelix.child_origin` record (`parentId: null`), then the child's own user,
  assistant, tool result and assistant entries.
- The parent, in order: user, assistant (tool call), start, pending, settle
  (`ok`, `anthropic/claude-haiku-4.5` via `openrouter`, `usage {input 5473,
  output 83, cache 0/0, cost 0.005888}`, `cost_known: true` from the registry
  fallback, `context_tokens 2808`, `turns 2`, `elapsed_ms 3276`), final, tool
  result, assistant.
- `--continue` resumed the parent even with the child file made the newest
  file under the root.
- `--session <child file>` printed the warning, stopped at the model gate, and
  left the file byte for byte unchanged (2,934 bytes); `--export <child file>`
  wrote a 9,073-byte page holding the child's answer.

The rpc channel has never run a real model (#123). Its session flag is pinned by
argv tests and by one scripted `RpcChannel.run` that records the argv it
builds; no rpc child, scripted or real, has ever appended to a session file.

The one end-to-end proof that a real child appends to the file its parent
published (`tests/agents_ext/test_child_session_real_child.py`) is POSIX only,
like the real-child fixture it reuses. On Windows the same path is covered in
process — argv built and parsed, then resolved and opened by the child's own
`_build_session` under a project path with a space and a non-ASCII letter —
but no real child process there has written one.

## Divergences from pi (ADR-0235: recorded, not argued)

Read from pi `origin/main` `36b60d2`'s source, not measured by running pi.

- **pi keeps no child session file either** — its subagent example spawns
  `--mode json -p --no-session` — but it keeps the child's whole transcript
  inside the parent file, in the tool result's persisted `details`. Aelix
  persists no `details` (#168) and keeps each child in its own file beside the
  parent, linked by records.
- **pi's stats sum over every entry of the file, every branch** (#6671:
  assistant usage plus tool-result `usage` plus the `usage` on `branch_summary`
  and `compaction` entries, in `getSessionStats` and the footer). Aelix reads
  the current branch, as it does for messages. pi's example sets no tool-result
  `usage`, so a pi child's cost is in no pi total at all.
- **pi v4 keeps usage in a separate ledger store.** `aelix.usage` is an
  ADR-0242 rule-1 `CustomEntry` in the same file; format 4 stays out (owner
  decision #10).
- **Ownership, not fork lineage, as pi's pico3 harness models it** (a tool
  creates the child as an owned conversation, linked from `details`, with no
  fork parent). Aelix agrees on the lineage and differs on the storage: the
  child is a separate file, and the link is the origin record plus the parent's
  start record.
- **pi caps parallel output at 50 KB per task and leaves single and chain
  uncapped** (`PER_TASK_OUTPUT_CAP`, `truncateParallelOutput`). Aelix caps every
  child at `output_cap` and every parallel or chain call at 64 KiB shared.

## Consequences

- Child transcripts accumulate beside their parents with no retention policy;
  nothing deletes sessions today and nothing here does either.
- `/fork` copies the records on its branch, so two parents can point at the
  same child files. `import_from_jsonl` copies only the parent file; its links
  may dangle.
- A copy taken while a delegation is still running — a fork at the current
  leaf, RPC `clone` — copies its start and pending, but the settle lands only
  in the session that ran the child (the copy is made before teardown's
  `stop_all`). The copy shows that run as pending, its cost as a floor, for
  good: the same class of limit as #137.
- Every total that reads `get_session_stats` — footer, `/cost`, `/session`,
  `/stats`, History, RPC — rises by the child spend of #199-era sessions.
- A kernel change: `harness/core.py` and `harness/_session_stats.py` carry an
  ADR-0243 line in `_KERNEL_CHANGE_ALLOWLIST` (`tests/agents/test_p2_band_boundaries.py`).

## Not done here

- A viewer (#296); retention or cleanup of child files.
- Persisting `details` (#168); per-call profile mixing (ADR-0199 P4); a
  production caller for the rpc channel (#123); a second writer on the parent
  file (#137).
- The footer's cost segment (off by default) prints the figure without reading
  `cost_known`, so it shows no `≥` while a run is pending — as before, for any
  floor.
- Widening "outside a bucket" to "directly in the sessions root", which needs
  `--session-dir` wired into the extension.
- The remaining `cost_known` limit in §3 (review of #199): pricing each message
  on its own needs the fallback, which prices the run as a whole from its last
  provider and model, to work per message. Also not done: keeping a cancelled
  child's stream open for a bounded moment so the in-flight usage arrives
  instead of being marked a floor — a change to the cancel path
  (`PrintChannel._stream_and_reap`, ADR-0199 §(j)).
- A real child on the windows leg (see *Measured on the branch*).
