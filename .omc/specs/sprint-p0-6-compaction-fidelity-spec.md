# P0 #6 — Compaction Fidelity (pi-parity) — Ground-Truth Spec

**Goal:** close the four deferred items from ADR-0117 so aelix compaction matches pi
`packages/agent/src/harness/compaction/{compaction,utils,branch-summarization}.ts`
at pin SHA `734e08e`:

1. **Entry-level cut-point + turn-boundary snap** — replace the message-list
   `_find_cut_index` with pi's entry-level `findCutPoint` / `findValidCutPoints`
   (+ `findTurnStartIndex`), with the control-entry back-up loop and correct
   `firstKeptEntryId`.
2. **Split-turn (turn-prefix) summarization** — `turn_prefix_messages` + `is_split_turn`
   threaded through `prepare_compaction` → `compact`; turn-prefix summarized with
   `TURN_PREFIX_SUMMARIZATION_PROMPT`, combined with the history summary.
3. **File-op extraction** — `<read-files>` / `<modified-files>` appended to the
   summary + stored in `details`; **same in branch summaries** (pi's
   `generateBranchSummary` also does file-ops).
4. **`max_tokens` cap** — add `max_tokens` to `SimpleStreamOptions` AND wire the
   provider adapters to honor it (else it is cosmetic — same bug class as the
   thinking no-op, ADR-0135). Summary cap = `floor(0.8 * reserveTokens)`;
   turn-prefix cap = `floor(0.5 * reserveTokens)`; branch-summary cap = `2048`.

`reserveTokens = 16384`, `keepRecentTokens = 20000`
(pi `DEFAULT_COMPACTION_SETTINGS`). aelix already has
`_AUTO_COMPACT_RESERVE_TOKENS = 16384` (core.py) and `KEEP_RECENT_TOKENS = 20000`
(compaction.py).

---

## CONSTRAINTS (read first)

- **Protected core**: `packages/aelix-agent-core/**` is gate-protected. The user has
  approved edits to the compaction subsystem (continuation of ADR-0117). Keep the
  diff **confined** to: `session/compaction.py`, `session/branch_summarization.py`,
  and *minimal* `harness/core.py` (only if strictly required). `streaming.py` lives in
  `packages/aelix-ai` (NON-protected — adding the `max_tokens` field is fine there).
  Provider adapters under `packages/aelix-ai/src/aelix_ai/providers/**` are
  NON-protected.
- **Do NOT touch** contracts / rpc / mcp / hooks shapes.
- **Idiomatic Python / snake_case** (ADR-0035). camelCase only on the JSONL wire
  (entries.py already handles that — `details` round-trips as `details`).
- **Verify, don't guess**: the `max_tokens` adapter wiring MUST be live-real, not a
  dangling field. A dedicated review lens checks this.
- Test command (full gate): `cd /workspaces/aelix-ai && timeout 1200 uv run pytest -p no:cacheprovider -q 2>&1 | tail -8`.
  During implementation, run only the targeted subset
  (`tests/session/test_compaction*`, `tests/.../providers`, branch-summary tests).

---

## AELIX MAPPING NOTES (pi → aelix)

- pi `block.name` → aelix `ToolCallContent.tool_name`; pi `block.arguments` →
  aelix `ToolCallContent.input` (a `dict[str, Any]`). File path = `input.get("path")`.
- pi `toolCall` block type → aelix `ToolCallContent` (`type == "toolCall"`).
- pi `completeSimple` (non-streaming) → aelix streams via
  `aelix_ai.streaming.stream_simple` (documented divergence, ADR-0117). Keep the
  existing event loop (`TextDeltaEvent` accumulate, `AssistantErrorEvent` raise,
  `AssistantDoneEvent` prefer terminal text). Apply `max_tokens` via the new
  `SimpleStreamOptions.max_tokens`.
- `Model.max_tokens: int = 0` (streaming.py:150). pi `model.maxTokens > 0 ?
  model.maxTokens : +Infinity`. So: `cap = floor(k * reserveTokens)`, then if
  `model.max_tokens > 0`: `cap = min(cap, model.max_tokens)`.
- Message builders (session/context.py):
  `create_compaction_summary_message(summary, tokens_before, timestamp)`,
  `create_branch_summary_message(summary, from_id, timestamp)`,
  `create_custom_message(custom_type, content, display, details, timestamp)`.
- `build_session_context(path_entries)` already honors the compaction
  `first_kept_entry_id` boundary — **do not change it**. `prepare_compaction` just
  needs to emit a correct `first_kept_entry_id`.
- entries: `CompactionEntry(... summary, first_kept_entry_id, tokens_before,
  details, from_hook)`. `details` is `Any | None` and round-trips through
  `entry_to_json`/`entry_from_json` as-is (so `{"readFiles": [...],
  "modifiedFiles": [...]}` persists). NOTE: choose ONE casing for the details dict
  and keep it consistent on read-back (pi uses `readFiles`/`modifiedFiles`).
- harness `compact()` (core.py:1256-1377) already: builds preparation → emits
  before-compact hook → calls `compaction.compact(model, get_api_key_and_headers,
  preparation, custom_instructions, _summarizer_override=…)` → `append_compaction(
  summary, first_kept_entry_id, tokens_before, details=result.details, from_hook)` →
  rebuilds `_state.messages`. Because `result.details` will now be populated, the
  file-op round-trip works with NO core.py change. Confirm this; only touch core.py
  if a signature genuinely requires it.

---

## VERBATIM PI SOURCE (SHA 734e08e)

### compaction.ts

```typescript
// DEFAULT settings
export const DEFAULT_COMPACTION_SETTINGS: CompactionSettings = {
	enabled: true,
	reserveTokens: 16384,
	keepRecentTokens: 20000,
};

interface CompactionSettings { enabled: boolean; reserveTokens: number; keepRecentTokens: number; }

interface CompactionPreparation {
	firstKeptEntryId: string;
	messagesToSummarize: AgentMessage[];
	turnPrefixMessages: AgentMessage[];
	isSplitTurn: boolean;
	tokensBefore: number;
	previousSummary?: string;
	fileOps: FileOperations;
	settings: CompactionSettings;
}
interface CompactionResult<T = unknown> { summary: string; firstKeptEntryId: string; tokensBefore: number; details?: T; }
interface CompactionDetails { readFiles: string[]; modifiedFiles: string[]; }

// --- findValidCutPoints: entry indices that are legal cut points ---
function findValidCutPoints(entries, startIndex, endIndex): number[] {
	const cutPoints = [];
	for (let i = startIndex; i < endIndex; i++) {
		const entry = entries[i];
		switch (entry.type) {
			case "message": {
				const role = entry.message.role;
				switch (role) {
					case "bashExecution":
					case "custom":
					case "branchSummary":
					case "compactionSummary":
					case "user":
					case "assistant":
						cutPoints.push(i); break;
					case "toolResult": break;   // toolResult can NEVER be a cut point
				}
				break;
			}
			case "thinking_level_change":
			case "model_change":
			case "compaction":
			case "branch_summary":
			case "custom":
			case "custom_message":
			case "label":
			case "session_info":
			case "leaf":
				break;
		}
		// trailing push: branch_summary + custom_message ARE cut points
		if (entry.type === "branch_summary" || entry.type === "custom_message") cutPoints.push(i);
	}
	return cutPoints;
}

// --- findCutPoint ---
export function findCutPoint(entries, startIndex, endIndex, keepRecentTokens): CutPointResult {
	const cutPoints = findValidCutPoints(entries, startIndex, endIndex);
	if (cutPoints.length === 0) return { firstKeptEntryIndex: startIndex, turnStartIndex: -1, isSplitTurn: false };
	let accumulatedTokens = 0;
	let cutIndex = cutPoints[0];
	for (let i = endIndex - 1; i >= startIndex; i--) {
		const entry = entries[i];
		if (entry.type !== "message") continue;
		accumulatedTokens += estimateTokens(entry.message);
		if (accumulatedTokens >= keepRecentTokens) {
			for (let c = 0; c < cutPoints.length; c++) {
				if (cutPoints[c] >= i) { cutIndex = cutPoints[c]; break; }
			}
			break;
		}
	}
	// back the cut up over control entries so the first-kept is a message/compaction
	while (cutIndex > startIndex) {
		const prevEntry = entries[cutIndex - 1];
		if (prevEntry.type === "compaction") break;
		if (prevEntry.type === "message") break;
		cutIndex--;
	}
	const cutEntry = entries[cutIndex];
	const isUserMessage = cutEntry.type === "message" && cutEntry.message.role === "user";
	const turnStartIndex = isUserMessage ? -1 : findTurnStartIndex(entries, cutIndex, startIndex);
	return { firstKeptEntryIndex: cutIndex, turnStartIndex, isSplitTurn: !isUserMessage && turnStartIndex !== -1 };
}

// --- findTurnStartIndex: walk back to the user/bashExecution msg (or branch/custom) that starts the turn ---
export function findTurnStartIndex(entries, entryIndex, startIndex): number {
	for (let i = entryIndex; i >= startIndex; i--) {
		const entry = entries[i];
		if (entry.type === "branch_summary" || entry.type === "custom_message") return i;
		if (entry.type === "message") {
			const role = entry.message.role;
			if (role === "user" || role === "bashExecution") return i;
		}
	}
	return -1;
}

// --- getMessageFromEntry (compaction's variant: include toolResult, EXCLUDE compaction) ---
function getMessageFromEntry(entry): AgentMessage | undefined {
	if (entry.type === "message") return entry.message;          // INCLUDES toolResult-role messages
	if (entry.type === "custom_message") return createCustomMessage(entry.customType, entry.content, entry.display, entry.details, entry.timestamp);
	if (entry.type === "branch_summary") return createBranchSummaryMessage(entry.summary, entry.fromId, entry.timestamp);
	if (entry.type === "compaction") return createCompactionSummaryMessage(entry.summary, entry.tokensBefore, entry.timestamp);
	return undefined;
}
function getMessageFromEntryForCompaction(entry): AgentMessage | undefined {
	if (entry.type === "compaction") return undefined;          // compaction-specific: drop compaction entries
	return getMessageFromEntry(entry);
}

// --- prepareCompaction ---
export function prepareCompaction(pathEntries, settings): Result<CompactionPreparation | undefined> {
	if (pathEntries.length === 0 || pathEntries[pathEntries.length - 1].type === "compaction") return ok(undefined);
	let prevCompactionIndex = -1;
	for (let i = pathEntries.length - 1; i >= 0; i--) { if (pathEntries[i].type === "compaction") { prevCompactionIndex = i; break; } }
	let previousSummary;
	let boundaryStart = 0;
	if (prevCompactionIndex >= 0) {
		const prevCompaction = pathEntries[prevCompactionIndex];
		previousSummary = prevCompaction.summary;
		const firstKeptEntryIndex = pathEntries.findIndex((e) => e.id === prevCompaction.firstKeptEntryId);
		boundaryStart = firstKeptEntryIndex >= 0 ? firstKeptEntryIndex : prevCompactionIndex + 1;
	}
	const boundaryEnd = pathEntries.length;
	const tokensBefore = estimateContextTokens(buildSessionContext(pathEntries).messages).tokens;
	const cutPoint = findCutPoint(pathEntries, boundaryStart, boundaryEnd, settings.keepRecentTokens);
	const firstKeptEntry = pathEntries[cutPoint.firstKeptEntryIndex];
	if (!firstKeptEntry?.id) return err(new CompactionError("invalid_session", "First kept entry has no UUID - session may need migration"));
	const firstKeptEntryId = firstKeptEntry.id;
	const historyEnd = cutPoint.isSplitTurn ? cutPoint.turnStartIndex : cutPoint.firstKeptEntryIndex;
	const messagesToSummarize = [];
	for (let i = boundaryStart; i < historyEnd; i++) { const msg = getMessageFromEntryForCompaction(pathEntries[i]); if (msg) messagesToSummarize.push(msg); }
	const turnPrefixMessages = [];
	if (cutPoint.isSplitTurn) {
		for (let i = cutPoint.turnStartIndex; i < cutPoint.firstKeptEntryIndex; i++) { const msg = getMessageFromEntryForCompaction(pathEntries[i]); if (msg) turnPrefixMessages.push(msg); }
	}
	const fileOps = extractFileOperations(messagesToSummarize, pathEntries, prevCompactionIndex);
	if (cutPoint.isSplitTurn) { for (const msg of turnPrefixMessages) extractFileOpsFromMessage(msg, fileOps); }
	return ok({ firstKeptEntryId, messagesToSummarize, turnPrefixMessages, isSplitTurn: cutPoint.isSplitTurn, tokensBefore, previousSummary, fileOps, settings });
}

// --- generateSummary (history) ---
async function generateSummary(currentMessages, model, reserveTokens, apiKey, headers, signal, customInstructions, previousSummary, thinkingLevel) {
	const maxTokens = Math.min(Math.floor(0.8 * reserveTokens), model.maxTokens > 0 ? model.maxTokens : Number.POSITIVE_INFINITY);
	let basePrompt = previousSummary ? UPDATE_SUMMARIZATION_PROMPT : SUMMARIZATION_PROMPT;
	if (customInstructions) basePrompt = `${basePrompt}\n\nAdditional focus: ${customInstructions}`;
	const conversationText = serializeConversation(convertToLlm(currentMessages));
	let promptText = `<conversation>\n${conversationText}\n</conversation>\n\n`;
	if (previousSummary) promptText += `<previous-summary>\n${previousSummary}\n</previous-summary>\n\n`;
	promptText += basePrompt;
	// completeSimple(model, {systemPrompt: SUMMARIZATION_SYSTEM_PROMPT, messages:[user(promptText)]}, {maxTokens, signal, apiKey, headers, reasoning?})
	// stopReason aborted -> err("aborted"); error -> err("summarization_failed"); else join text parts with "\n"
}

// --- generateTurnPrefixSummary ---
async function generateTurnPrefixSummary(messages, model, reserveTokens, apiKey, headers, signal, thinkingLevel) {
	const maxTokens = Math.min(Math.floor(0.5 * reserveTokens), model.maxTokens > 0 ? model.maxTokens : Number.POSITIVE_INFINITY);
	const conversationText = serializeConversation(convertToLlm(messages));
	const promptText = `<conversation>\n${conversationText}\n</conversation>\n\n${TURN_PREFIX_SUMMARIZATION_PROMPT}`;
	// completeSimple(model, {systemPrompt: SUMMARIZATION_SYSTEM_PROMPT, messages:[user(promptText)]}, {maxTokens,...})
	// returns joined text parts
}

const TURN_PREFIX_SUMMARIZATION_PROMPT = `This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Be concise. Focus on what's needed to understand the kept suffix.`;

// --- compact ---
export async function compact(preparation, model, apiKey, headers, customInstructions, signal, thinkingLevel) {
	const { firstKeptEntryId, messagesToSummarize, turnPrefixMessages, isSplitTurn, tokensBefore, previousSummary, fileOps, settings } = preparation;
	if (!firstKeptEntryId) return err(...);
	let summary;
	if (isSplitTurn && turnPrefixMessages.length > 0) {
		const [historyResult, turnPrefixResult] = await Promise.all([
			messagesToSummarize.length > 0
				? generateSummary(messagesToSummarize, model, settings.reserveTokens, apiKey, headers, signal, customInstructions, previousSummary, thinkingLevel)
				: Promise.resolve(ok("No prior history.")),
			generateTurnPrefixSummary(turnPrefixMessages, model, settings.reserveTokens, apiKey, headers, signal, thinkingLevel),
		]);
		if (!historyResult.ok) return err(historyResult.error);
		if (!turnPrefixResult.ok) return err(turnPrefixResult.error);
		summary = `${historyResult.value}\n\n---\n\n**Turn Context (split turn):**\n\n${turnPrefixResult.value}`;
	} else {
		const summaryResult = await generateSummary(messagesToSummarize, model, settings.reserveTokens, apiKey, headers, signal, customInstructions, previousSummary, thinkingLevel);
		if (!summaryResult.ok) return err(summaryResult.error);
		summary = summaryResult.value;
	}
	const { readFiles, modifiedFiles } = computeFileLists(fileOps);
	summary += formatFileOperations(readFiles, modifiedFiles);
	return ok({ summary, firstKeptEntryId, tokensBefore, details: { readFiles, modifiedFiles } });
}

// --- extractFileOperations: seed from prev compaction details, then per-message ---
function extractFileOperations(messages, entries, prevCompactionIndex): FileOperations {
	const fileOps = createFileOps();
	if (prevCompactionIndex >= 0) {
		const prevCompaction = entries[prevCompactionIndex];
		if (!prevCompaction.fromHook && prevCompaction.details) {
			const details = prevCompaction.details;
			if (Array.isArray(details.readFiles)) for (const f of details.readFiles) fileOps.read.add(f);
			if (Array.isArray(details.modifiedFiles)) for (const f of details.modifiedFiles) fileOps.edited.add(f);
		}
	}
	for (const msg of messages) extractFileOpsFromMessage(msg, fileOps);
	return fileOps;
}
```

### utils.ts

```typescript
export interface FileOperations { read: Set<string>; written: Set<string>; edited: Set<string>; }
export function createFileOps(): FileOperations { return { read: new Set(), written: new Set(), edited: new Set() }; }

export function extractFileOpsFromMessage(message, fileOps): void {
	if (message.role !== "assistant") return;
	if (!("content" in message) || !Array.isArray(message.content)) return;
	for (const block of message.content) {
		if (typeof block !== "object" || block === null) continue;
		if (!("type" in block) || block.type !== "toolCall") continue;
		if (!("arguments" in block) || !("name" in block)) continue;     // aelix: input + tool_name
		const args = block.arguments;
		if (!args) continue;
		const path = typeof args.path === "string" ? args.path : undefined;
		if (!path) continue;
		switch (block.name) {                                            // aelix: block.tool_name
			case "read":  fileOps.read.add(path); break;
			case "write": fileOps.written.add(path); break;
			case "edit":  fileOps.edited.add(path); break;
		}
	}
}

export function computeFileLists(fileOps): { readFiles: string[]; modifiedFiles: string[] } {
	const modified = new Set([...fileOps.edited, ...fileOps.written]);
	const readOnly = [...fileOps.read].filter((f) => !modified.has(f)).sort();
	const modifiedFiles = [...modified].sort();
	return { readFiles: readOnly, modifiedFiles };
}

export function formatFileOperations(readFiles, modifiedFiles): string {
	const sections = [];
	if (readFiles.length > 0) sections.push(`<read-files>\n${readFiles.join("\n")}\n</read-files>`);
	if (modifiedFiles.length > 0) sections.push(`<modified-files>\n${modifiedFiles.join("\n")}\n</modified-files>`);
	if (sections.length === 0) return "";
	return `\n\n${sections.join("\n\n")}`;
}
```

### branch-summarization.ts (file-ops parity — "same in branch summaries")

```typescript
// generateBranchSummary returns { summary, readFiles, modifiedFiles } (NOT a bare string)
// maxTokens: 2048
// const { messages, fileOps } = prepareBranchEntries(entries, contextWindow - reserveTokens);
// ... after LLM summary:
const { readFiles, modifiedFiles } = computeFileLists(fileOps);
summary += formatFileOperations(readFiles, modifiedFiles);
return ok({ summary: summary || "No summary generated", readFiles, modifiedFiles });
```

**Branch-summary scope for THIS sprint:** add file-op extraction
(`extractFileOpsFromMessage` over the branch messages) + append
`formatFileOperations(...)` to the returned summary string, and apply the
`max_tokens = 2048` cap via the new `SimpleStreamOptions.max_tokens`.
aelix `generate_branch_summary` currently returns `str` and the harness consumes a
`str` — KEEP returning `str` (append the file-ops tail to it); do NOT change the
harness contract. The full `prepareBranchEntries` token-budget backward walk is a
SEPARATE fidelity concern NOT named in P0 #6 — leave it as a documented follow-up
unless trivial.

---

## MAX_TOKENS ADAPTER WIRING (the crux — make it real, not cosmetic)

Current state (verified):
- `SimpleStreamOptions` (streaming.py:396) has NO `max_tokens` field.
- Anthropic adapter (`anthropic.py:185`, `_anthropic_transforms.py:339-346`) computes
  `max_tokens` from `model.max_tokens or 4096` and the comment EXPLICITLY says
  "aelix has no per-turn options.maxTokens plumbed into this adapter… revisit if
  options.maxTokens is ever plumbed." → it IGNORES options.max_tokens today.
- OpenAI-compat (`_openai_compat.py`) picks the field *name* (`max_tokens` vs
  `max_completion_tokens`) but the VALUE source must be checked in `openai.py`
  build_params.

Required (pi semantics `base.maxTokens = options.maxTokens ?? model.maxTokens`):
1. Add `max_tokens: int | None = None` to `SimpleStreamOptions`.
2. Anthropic adapter: when `options.max_tokens` is set, use it as the base output cap
   (replacing the `model.max_tokens or 4096` default) — i.e. pass it as
   `default_max_tokens` into `build_thinking_params`/`adjust_max_tokens_for_thinking`
   and as the request `max_tokens`. Preserve the thinking-budget carve-out math.
3. OpenAI / OpenAI-compat / OpenRouter: when `options.max_tokens` is set, emit it as
   the value of the `max_tokens` / `max_completion_tokens` field (precedence over the
   model default).
4. Add provider-level tests asserting the request payload's max-tokens value equals
   `options.max_tokens` when supplied (this is what makes the cap REAL).

If a clean adapter wiring proves larger than expected, the field + compaction usage
still land; flag any adapter NOT honoring it explicitly (no silent dangling field).

---

## DELIVERABLES / DONE CRITERIA

- `compaction.py`: entry-level `find_cut_point` + `find_valid_cut_points` +
  `find_turn_start_index`; rewritten `prepare_compaction` (boundaryStart from prev
  compaction, historyEnd, split-turn); `_get_message_from_entry_for_compaction`;
  file-op utils (`create_file_ops`/`extract_file_ops_from_message`/
  `compute_file_lists`/`format_file_operations`); `TURN_PREFIX_SUMMARIZATION_PROMPT`
  + `_generate_turn_prefix_summary`; rewritten `compact` (split-turn gather +
  file-ops append + `details={"readFiles","modifiedFiles"}`); `RESERVE_TOKENS=16384`;
  `max_tokens` applied to summary (0.8×) + turn-prefix (0.5×) stream calls.
- `branch_summarization.py`: file-op extraction + `format_file_operations` tail +
  `max_tokens=2048`.
- `streaming.py`: `SimpleStreamOptions.max_tokens`.
- provider adapters: honor `options.max_tokens`.
- Tests across all of the above (deterministic; use `_summarizer_override` /
  monkeypatched stream where an LLM would be needed; assert cut indices, split-turn
  detection, file-op tags, details round-trip, and adapter payload max-tokens).
- Full gate green; diff confined to the approved files.
