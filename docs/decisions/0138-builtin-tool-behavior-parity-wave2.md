# 0138. Built-in Tool Behavior Parity — Wave 2 (read + edit rewrites)

Status: Accepted
Date: 2026-06-17
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Wave 2 of gap-inventory **P0 #3** — the two rewrite-heavy tools deferred from
ADR-0137: **read** and **edit**. Both were near-rewrites, so they were split out
for a focused cycle. Research was done with a parallel workflow plus a dedicated
**impact-map agent** to de-risk cross-consumer breakage before touching code; it
found the one load-bearing risk (see *Consequences*).

**Process note (honest):** the *edit-research* sub-agent runaway-looped twice
(1989 then 208 events, no convergence). Rather than re-delegate a third time, pi
`edit.ts` / `edit-diff.ts` were fetched **directly into the main context** and
the algorithm ported by hand — more reliable for a delicate, exact port. The
read-research + impact-map agents completed cleanly and were salvaged from their
transcripts after the workflow was stopped.

## Decision

### `_edit_diff.py` (new) — port of pi `edit-diff.ts`
- `fuzzy_find_text` (exact `indexOf` first, else fuzzy in normalized space);
  `normalize_for_fuzzy_match` (NFKC → per-line `rstrip` → smart single/double
  quotes, dashes, special spaces; **no lowercasing**); `count_occurrences`
  (always in fuzzy space); `apply_edits_to_normalized_content` (matches the
  ORIGINAL content; if ANY edit needs fuzzy the whole base switches to
  `normalize_for_fuzzy_match(content)`; right-to-left apply; overlap detection);
  `generate_diff_string` (line-numbered +/-/context diff with elision);
  `prepare_edit_arguments`; line-ending/BOM helpers; **verbatim pi error
  strings** (not-found / duplicate / empty-oldText / no-change / overlap, single
  + multi forms).

### `edit.py`
- `prepareArguments` (edits-as-JSON-string + legacy top-level oldText/newText);
  matches original content via `_edit_diff`; result **content is the success
  message** `Successfully replaced N block(s) in {raw_path}.` with the diff in
  `details` (not content); **no-change is now an error** (pi `getNoChangeError`);
  read/access failures → `Could not edit file: {path}. Error code: {code}.`.
- Descriptions restored to **verbatim pi** ("matched against the original file",
  "unique in the original file") now that behavior matches.

### `read.py`
- Offset is **1-indexed** (`offset=1` reads line 1; the prior 0-indexed off-by-
  one is fixed) + out-of-bounds error `Offset {n} is beyond end of file ({N}
  lines total)`; `split("\n")` (not `splitlines()`); **raw slice — no `cat -n`
  numbering**; byte cap via shared `truncate_head` with the **4-branch notice
  logic** (first-line-exceeds replaces the body; truncateHead-truncated; user-
  limit-more; none); magic-byte MIME via `util/image_detect`; **no server-side
  limit default**; `truncated` derived from `truncate_head`; `details` present
  only in the firstLineExceeds + truncated branches (pi parity). Schema offset
  description restored to "(1-indexed)"; top-level description = verbatim pi.

### `tui/render.py` — load-bearing edit-card fix
- The edit card colorization previously worked *only* because edit emitted a
  `@@` unified diff in **content**. With the diff now in `details` (pi's line-
  numbered, non-`@@` format), `_render_tool_end` reads `EditToolDetails.diff`
  from details for the edit tool and renders it with `_render_diff` (which
  colours by the +/- prefix). Without this the card would have silently
  downgraded — a regression the test suite could not catch (the diff-render
  tests use synthetic `@@` content), so a dedicated test was added.

## Consequences

- The **read offset bug is fixed** (a pi-aligned model passing `offset=1` now
  reads line 1, not line 2) and **edit matches the original file** (the headline
  P0 #3 divergence). edit content is now a success message; the diff is for the
  TUI card only.
- **HEAVY-deferred (NOT in scope, tracked):** read image resize to 2000×2000 +
  dimension note + non-vision-model note (needs Pillow + a `model` field on
  `ToolExecutionContext`); the read compact-card TUI classification. The image
  branch returns the raw base64 data-URL for now. Also still open from ADR-0137:
  ensureTool `rg`/`fd` auto-download (→ guaranteed `.gitignore`, then add it back
  to grep/find descriptions).
- `generate_diff_string` is a faithful difflib-based port (shape: prefixes,
  numbers, context elision) — not byte-identical to pi's npm `diff` lib, and
  the elision is symmetric head/tail rather than pi's boundary-aware 4-case;
  acceptable since the diff is TUI-only (details), not model-facing.

## Review (adversarial, 9 agents)

read **faithful**; edit **has_unintended_divergence**; code-review
**APPROVE_WITH_NITS**; truthfulness critic **REQUEST_CHANGES**. 5 confirmed
defects collapsed to **3 distinct fixes, ALL applied:**
1. **`generate_diff_string` context branch** (defects 1/4/5, up to HIGH) — it
   indexed the NEW line array with the OLD opcode index → phantom context /
   out-of-bounds on any line-count-changing edit. Fixed to index `old_lines`
   with the OLD offset and number context by the old line number (pi parity);
   regression test added (the suite previously had none for a line-shifting
   diff).
2. **`count_occurrences`** (defect 2, MEDIUM) — an exact-first fast path
   under-counted when an exact and a fuzzy-equivalent occurrence coexisted,
   weakening the uniqueness guard (silent edit of one of two identical matches).
   Fixed to always count in the fuzzy space; regression test added.
3. **`prepare_edit_arguments`** (defect 3, MEDIUM) — a non-parseable `edits`
   string was spread into single-char edits during the legacy fold. Fixed to
   seed only from an existing list; regression test added.

## Pi parity citations (SHA 734e08e, `packages/coding-agent/src/core/tools/`)
- `read.ts` (offset/notices/truncateHead/mime), `edit.ts` (prepareArguments /
  computeEditsDiff / success message), `edit-diff.ts` (`fuzzyFindText`,
  `normalizeForFuzzyMatch`, `applyEditsToNormalizedContent`, `countOccurrences`,
  `generateDiffString`, error helpers), `truncate.ts`.

## Tests
- `tests/tools/test_read_tool.py` — 1-indexed offset, out-of-bounds, no
  numbering, byte-cap branch-B notice, branch-C "more lines".
- `tests/tools/test_edit_tool.py` — success-message-not-diff, original-content
  matching, overlap, fuzzy, legacy + JSON-string prepareArguments, no-change
  error, + the 3 review-defect regression tests (diff context, fuzzy duplicate,
  non-list edits string).
- `tests/tui/test_event_renderer.py` — edit card surfaces + colorizes the diff
  from `details`.
- Gate: **3234 passed, 1 skipped, 0 regressions** (after the 3 defect fixes;
  `python -m pytest -q`).

## Cross-references
- ADR-0137 (Wave 1: bash/write/ls/grep/find + shared helpers). ADR-0136 (tool
  schema parity). ADR-0042 (original tool port). Closes gap-inventory **P0 #3**
  for read + edit; only the HEAVY items (image resize / ctx.model / ensureTool)
  remain.
