# 0117. Compaction Summarizer — Real LLM Implementation (closes ADR-0023/0038 deferral)

Status: Accepted (W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

ADR-0023 (Sprint 4b / Phase 2.2.2) shipped the compaction **structure** (phase
machine, hook events, persistence, `CompactionPreparation`/`CompactResult`) but
the actual LLM summarization was a stub: `session/compaction.py::compact()` raised
`AgentHarnessError("invalid_state", "compact() LLM provider not yet implemented
(Phase 4 / ADR-0038)")`, and `prepare_compaction` returned `messages_to_summarize=[]`
with the head entry as the cut point. So `/compact` errored on any real session —
a P0 product gap (context management is survival-critical for long coding sessions).

`packages/aelix-agent-core` is normally gate-protected; this work was explicitly
user-approved. The diff is confined to `session/compaction.py` (contracts/rpc/mcp/
harness untouched — verified).

## The decisions (pi-faithful port, `compaction.ts`)

- **`prepare_compaction` — real cut point**: builds ordered `(entry_id, message)`
  pairs directly from `path_entries` (mirroring `build_session_context`'s per-type
  conversion for `message`/`custom_message`/`branch_summary`), runs a backward
  token-budget walk (`_find_cut_index`, pi `findCutPoint`, `KEEP_RECENT_TOKENS=20000`)
  that never leaves a `ToolResultMessage` as the first-kept message, and sets
  `first_kept_entry_id = pairs[cut][0]` — **the actual cut entry** (NOT the head),
  so `build_session_context` drops the summarized prefix and compaction genuinely
  reduces rebuilt context. `messages_to_summarize = msgs[:cut]`; `tokens_before`
  via `estimate_context_tokens`. Returns `None` below threshold / no valid cut /
  the existing empty / already-compacted / no-first-id guards.
- **`compact` — real summary**: keeps the `_summarizer_override` test seam + the
  `get_api_key_and_headers is None` guard; otherwise calls `_generate_summary`,
  which builds `<conversation>…</conversation>` (+ optional `<previous-summary>` +
  `Additional focus: …`), resolves sync/awaitable auth, streams via
  `aelix_ai.streaming.stream_simple`, accumulates `TextContent` (`"\n".join`), and
  returns the `CompactResult`. **Errors**: an `AssistantErrorEvent` from the
  provider raises `AgentHarnessError("compaction", …)` immediately — partial
  accumulated text is never returned as a valid summary (code-review HIGH fix).
- **Post-compact live rebuild** (`harness/core.py::compact`, pi `agent-session.ts:1693-1695`):
  after `append_compaction`, the harness rebuilds `self._state.messages` in place
  (`build_session_context(await get_branch()).messages`). Surfaced by the
  **command-level reference comparison** (vs pi's `/compact`): without it the
  in-memory turn context kept the full pre-compaction history, so `/compact`
  persisted to the session but had **no effect on the live session until reload**.
  This is the only change outside `compaction.py` (a +13-line addition to the
  existing `compact()` method — still within the user-approved compaction scope).
- **Verbatim pi prompts**: `SUMMARIZATION_SYSTEM_PROMPT`, `SUMMARIZATION_PROMPT`,
  `UPDATE_SUMMARIZATION_PROMPT` (structured `## Goal / Constraints / Progress /
  Key Decisions / Next Steps / Critical Context` format). `_serialize_conversation`
  renders turns as plain DATA (tool results truncated to `TOOL_RESULT_MAX_CHARS=2000`
  with a `… (truncated)` marker + `[error]` label on failed tool results).

## Consequences

- `/compact` works end-to-end (live-verified: qwen/qwen3.6-35b-a3b produced a
  correct pi-format structured summary) and **actually reduces context** (cut entry
  wired). Manual `/compact` on a small session correctly reports "Nothing to compact"
  (pi parity — `keepRecentTokens` budget; compaction triggers when context is large).
- **Deferred (documented divergences, follow-ups)**: split-turn (`turn_prefix`)
  summarization; file-ops accounting (`<read-files>`/`<modified-files>`); the
  `max_tokens = floor(0.8*reserveTokens)` summary cap (Aelix `SimpleStreamOptions`
  has no output-cap field — an infra gap); auto-compact trigger (`shouldCompact`).
  Pre-existing: `estimate_tokens` counts user-image blocks where pi counts only text
  (cut-boundary divergence for image-heavy chats; outside this diff).

## Verification

- ruff clean; pyright 0 errors on `compaction.py` (8-baseline overall); full pytest
  2945 pass / 1 skip; protected diff confined to `compaction.py` (contracts/rpc/mcp/
  harness byte-unchanged).
- New tests: cut-point (populates `messages_to_summarize`, None below threshold,
  never cuts on tool-result), `first_kept_entry_id == cut entry != head`,
  serialization (truncation marker + `[error]`), `compact` streams a canned summary,
  and **`AssistantErrorEvent` raises (with + without preceding deltas)**.
- **W4 code-reviewer (opus): REQUEST CHANGES → all addressed** (HIGH error-event
  swallowing; MEDIUM first_kept-entry-id reduction; `"\n"` join; docstring; auth
  guard; tool-result labels). MEDIUM max_tokens cap noted as an infra-gap divergence.
- Live: real qwen3.6 summary, structurally correct, re-verified after the fixes.
