# 0142. Compaction Fidelity — Entry-Level Cut-Point + Split-Turn + File-Ops + max_tokens Cap

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Gap-inventory **P0 #6**: ADR-0117 shipped the compaction summarizer but
deferred four fidelity items. `compaction.py` cut at the **message-list**
level (`_find_cut_index`) rather than pi's **entry-level** `findCutPoint` /
`findValidCutPoints`, so there was no turn-boundary snap and the
`firstKeptEntryId` could diverge; split-turn (turn-prefix) summarization was
absent; file-operation extraction (`<read-files>` / `<modified-files>`) was
missing (in compaction **and** branch summaries); and there was no `max_tokens`
output cap because `SimpleStreamOptions` had no such field.

**Process note:** the pi sources (`compaction.ts`, `utils.ts`,
`branch-summarization.ts`) were fetched verbatim at the pin and captured in
`.omc/specs/sprint-p0-6-compaction-fidelity-spec.md` (local, untracked) before
any edit. Implementation + 5-lens adversarial review (pi-fidelity, correctness,
max_tokens-real, scope-regression, test-adequacy) were run as a dynamic
multi-agent workflow; the review's 4 confirmed non-LOW findings were all
test-adequacy gaps (closed with additive tests, no production-code change).

## Decision

### 1. Entry-level cut-point + turn-boundary snap (`session/compaction.py`)

- `find_valid_cut_points(entries, start, end)` — entry indices that are legal
  cuts: any `message` whose role is **not** `toolResult`, plus `branch_summary`
  / `custom_message` control entries.
- `find_cut_point(...)` — backward token walk over message entries; once
  accumulated `estimate_tokens` ≥ `KEEP_RECENT_TOKENS` (20000), snap to the
  first valid cut point ≥ that index, then back the cut up over control entries
  so the first-kept entry is a `message`/`compaction`. Returns
  `(first_kept_entry_index, turn_start_index, is_split_turn)`.
- `find_turn_start_index(...)` — backward to the `user`/`bashExecution` message
  (or `branch_summary`/`custom_message`) that starts the turn.
- `prepare_compaction` rewritten: `boundary_start` = the prior compaction's
  `first_kept_entry_id` index (fallback `prev_index + 1`); `history_end` =
  `turn_start_index` on a split turn else the cut; `messages_to_summarize` and
  `turn_prefix_messages` built via `_get_message_from_entry_for_compaction`
  (INCLUDES `toolResult` messages, EXCLUDES prior `compaction` entries).

### 2. Split-turn summarization (`compact`)

On a split turn with a non-empty turn prefix, the history prefix and the turn
prefix are summarized **concurrently** (`asyncio.gather`) and combined with the
verbatim separator `\n\n---\n\n**Turn Context (split turn):**\n\n`. An empty
history substitutes `"No prior history."` (pi's `Promise.resolve(ok(...))`).
Turn-prefix uses `TURN_PREFIX_SUMMARIZATION_PROMPT`.

### 3. File-op extraction (compaction **and** branch summaries)

- `utils` port: `FileOperations{read,written,edited}`, `create_file_ops`,
  `extract_file_ops_from_message` (reads `ToolCallContent.tool_name` +
  `input["path"]`; `read`→read, `write`→written, `edit`→edited),
  `compute_file_lists` (modified = written ∪ edited; read-only = read − modified;
  both sorted), `format_file_operations` (`<read-files>` / `<modified-files>`
  tail, `\n\n`-joined, prefixed with `\n\n`).
- `compact` appends the tail to the summary and stores
  `details={"readFiles": …, "modifiedFiles": …}` (camelCase = wire/pi parity).
  `_extract_file_operations` seeds from the prior **non-hook** compaction's
  `details` so references carry across consecutive compactions.
- `branch_summarization.generate_branch_summary` extracts file ops over the
  branch messages and appends the same tail (still returns `str` — the harness
  contract is unchanged).

### 4. Real `max_tokens` cap (NOT cosmetic)

`SimpleStreamOptions.max_tokens: int | None` added (`aelix_ai/streaming.py`).
The cap is `floor(0.8 × reserveTokens)` for the summary, `floor(0.5 ×
reserveTokens)` for the turn prefix, and `2048` for branch summaries
(`reserveTokens = 16384`), clamped by `model.max_tokens` when > 0 — pi's
`Math.min(floor(k·reserve), model.maxTokens>0 ? model.maxTokens : +Inf)`.

The field is **wired through to the provider request payload** (the whole point
— a dangling field would repeat the thinking-no-op class of bug, ADR-0135):

- **Anthropic** (`anthropic.py` / `_anthropic_transforms.py`): pi
  `base.maxTokens = options.maxTokens ?? model.maxTokens`. When
  `options.max_tokens > 0` it becomes the `default_max_tokens` base fed to the
  thinking-budget math (`base_max` = override, `model_clamp` = model cap); the
  thinking carve-out is preserved.
- **OpenAI/OpenRouter** (`openai_completions.py`): when `options.max_tokens > 0`
  it is emitted as `max_tokens`/`max_completion_tokens` with precedence over the
  model default and **bypasses** the ADR-0114 context-window guard (an explicit,
  intentionally-small cap, never the full window). `_coerce_options` carries the
  field through the widen so `build_params` sees it.

## Divergences from pi (documented, not silently dropped)

- `details` dict keys are camelCase (`readFiles`/`modifiedFiles`) for wire/pi
  parity; `details` is now always populated (pi's is optional).
- `prepare_compaction` adds an additive early-return when both message lists are
  empty → the harness reports "Nothing to compact" for a degenerate empty-prefix
  case pi would still attempt (safer; never summarizes nothing).
- Missing first-kept UUID returns `None` (→ "Nothing to compact") instead of pi's
  `CompactionError("invalid_session")` — avoids a hard error on un-migrated
  sessions.
- `branch_summary` path omits pi's `summary || "No summary generated"` literal
  fallback (the preamble already guarantees a non-empty body).
- The `bashExecution` role arms in `find_valid_cut_points`/`find_turn_start_index`
  are inert — aelix has no such message role (bash output is a `toolResult`).
- `prepareBranchEntries` token-budget backward walk for branch summaries is a
  separate fidelity concern NOT named in P0 #6 — deferred.

## Verification

- Full gate: **3406 passed, 1 skipped**; the only 3 failures
  (`tests/cli/test_append_system_prompt.py`) are the pre-existing AGENTS.md
  cwd-coupling baseline (documented in ADR-0140/0141 work), unrelated to this
  diff (zero references to any touched module). ruff clean.
- New deterministic tests: `tests/session/test_compaction_fidelity.py`,
  `tests/providers/test_max_tokens_cap.py`, plus branch-summary file-op +
  2048-cap coverage in `tests/test_navigate_tree.py`. The adapter payload
  max-tokens tests were proven load-bearing via mutation testing.
- Diff confined to `session/compaction.py`, `session/branch_summarization.py`,
  the two provider adapters, `streaming.py`, and tests. `harness/core.py`
  `compact()` round-trips `result.details` into `append_compaction` **unchanged**
  (no core change required). `build_session_context` still drops the summarized
  prefix given the new entry-level `first_kept_entry_id`.
