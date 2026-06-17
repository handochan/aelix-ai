# 0136. Built-in Tool Schema Parity (close P0 #2 — camelCase params + per-field descriptions)

Status: Accepted
Date: 2026-06-17
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

The exhaustive pi-parity gap inventory flagged **P0 #2**: the 7 built-in tool
**schemas** diverged from pi. Concretely:

1. **snake_case parameter names** — `edit.edits[].old_text/new_text`,
   `grep.ignore_case` — vs pi's camelCase `oldText`/`newText`/`ignoreCase`. A
   model aligned to pi's schema emits camelCase; aelix's `execute()` read the
   snake_case keys, so the camelCase args **silently failed to bind**
   (`oldText` → `old_text` default `""` → "old_text not found" / multi-match).
   This is the real, model-facing parity break.
2. **No per-field `description` strings** on any parameter.
3. **Terse top-level descriptions** (e.g. edit: "Apply one or more
   (old_text, new_text) edits to a file.").
4. **No `promptSnippet`/`promptGuidelines`** — pi's per-tool system-prompt
   metadata.

The gap was hidden because aelix's own tests called aelix's snake_case keys.

Following the ADR-0135 discipline (don't guess — read pi at the pin), pi's 7
tool definitions were fetched verbatim from
`raw.githubusercontent.com/.../734e08e/packages/coding-agent/src/core/tools/`.
That revealed a tension this ADR resolves explicitly: **pi's tool descriptions
assert behaviors aelix does not yet have**, and those behaviors are tracked
under a *different* inventory item (**P0 #3 — tool behavior**), not P0 #2:

| tool  | pi description asserts                                  | aelix behavior today        |
|-------|--------------------------------------------------------|-----------------------------|
| bash  | "2000 lines or 50KB… saved to a temp file"             | 256/32KB, no temp file      |
| read  | "2000 lines or 50KB"; offset "(1-indexed)"             | 2000 lines (matches), **no byte cap**, **0-based offset** |
| grep  | "Respects .gitignore"; long lines → 500 chars          | Python fallback ignores .gitignore; 250-char cap |
| find  | "relative paths"; "Respects .gitignore"                | absolute paths; fallback ignores .gitignore |
| ls    | "sorted alphabetically… includes dotfiles"             | matches (codepoint sort)    |
| write | (matches pi exactly)                                   | matches                     |

Copying pi's descriptions verbatim would make the schema **advertise behavior
the tool lacks** — e.g. promise a temp file that doesn't exist, or tell a
pi-aligned model `offset` is 1-indexed while aelix skips line 1 (an active
bug). The user chose option **(A)**: faithful pi wording, **truthful numbers** —
no false schema claims — with all behavior changes deferred to P0 #3. The user
also chose **(가)**: defer `promptSnippet`/`promptGuidelines` to a dedicated
system-prompt-parity sprint, with both deferrals **tracked so they are
guaranteed to land** (see *Deferred*).

## Decision

Port pi's **wire schema** (the `{name, description, parameters}` the provider
actually sees) for all 7 tools. Pi-faithful, camelCase-only, truthful.

### Parameter names → camelCase (the parity break)
- `edit.edits[].old_text` → `oldText`; `new_text` → `newText` (+ nested
  `required: ["oldText", "newText"]`). `execute()` now reads `oldText`/`newText`;
  edit error strings reworded to `oldText`/`newText`.
- `grep.ignore_case` → `ignoreCase`. `execute()` reads `args["ignoreCase"]`.
- **camelCase-only** (no snake_case fallback) — exact pi parity. Verified safe:
  no internal caller reads these arg dicts by snake_case key; only the LLM and
  tests did, and the tests were migrated.

### Per-field descriptions
- Every property gains a `description`. Behavior-neutral descriptions are taken
  **verbatim** from pi (e.g. `edit.path` "Path to the file to edit (relative or
  absolute)", `grep.glob` "Filter files by glob pattern, e.g. '*.ts'…",
  `grep.ignoreCase` "Case-insensitive search (default: false)").
- Two behavior-coupled parentheticals are **dropped** to stay truthful (option
  A): pi's `read.offset` "(1-indexed)" → omitted (aelix is 0-based); pi's
  `edit.oldText` "unique in the *original* file" → "unique in the file" (aelix
  matches the running buffer). Both restored to verbatim-pi when P0 #3 flips the
  behavior.

### Numeric types → `number`
- `read.offset/limit`, `grep.context/limit`, `find.limit`, `ls.limit` changed
  from `integer` → `number` to match pi's `z.number()` JSON-schema output
  (`bash.timeout` was already `number`). Safe under aelix's no-op validator;
  ensures identical provider-side function-schema shape.

### Top-level descriptions → pi wording, truthful numbers
- **write** matches pi behavior exactly → **verbatim** pi description.
- **ls** matches pi → pi wording (caps stated as the actual default 500).
- **edit/read/bash/grep/find** follow pi's structure/wording but state aelix's
  ACTUAL caps and omit clauses for absent behavior (bash temp-file; read byte
  cap; grep/find .gitignore; find relative paths). No false claims.

## Consequences

- A pi-aligned model's tool calls now **bind correctly** against aelix (the
  camelCase break is closed) and every parameter is self-describing. This is the
  model-facing contract that matters for cross-model parity.
- **Documented (A) divergences vs verbatim-pi text** (each becomes verbatim once
  P0 #3 lands): bash cap 256/32KB (not 2000/50KB) + no temp-file clause; read
  no byte cap (line count already 2000 = matches) + offset index-base
  parenthetical omitted; grep/find no .gitignore clause; find no "relative
  paths" clause; grep 250-char line cap (pi: 500); edit "the file" (not
  "original file"). Each is annotated in the tool source next to its schema.
- **camelCase-only**: a legacy snake_case payload no longer applies (regression
  test asserts no silent write).
- Pure consumer change confined to `packages/aelix-coding-agent/.../tools/*.py`
  + tests; no protected `aelix-agent-core` touch, no behavior change.

## Deferred — tracked, MUST land next (per user directive)

These are recorded here, in the gap-inventory spec, and in session memory so
they are not lost:

1. **P0 #3 tool behavior** — the alignments that let each description become
   **verbatim-pi**: bash 2000/50KB cap + temp-file save + truncation notice;
   read 50KB byte cap + 1-indexed offset (then restore "(1-indexed)"; read's
   2000-line count already matches pi); grep/find `.gitignore` respect in the
   Python fallback + find relative paths + grep 500-char line cap (aelix
   currently 250); edit match against the **original** file (then restore
   "original file" wording); ls locale-aware sort. **When P0 #3 ships, upgrade
   the 5 adapted descriptions to verbatim pi.**
2. **`promptSnippet`/`promptGuidelines`** (option 가, separate sprint) — pi's
   per-tool system-prompt metadata. NOT part of the wire schema (never sent to
   the provider); pi consumes it when assembling the system prompt. aelix
   currently hardcodes equivalent tool guidance in
   `cli/agent_context.py:build_system_prompt`. Follow-up: add
   `prompt_snippet`/`prompt_guidelines` fields to the base `Tool` (`aelix-ai`,
   non-protected — inherited by `AgentTool` with no `aelix-agent-core` edit) +
   a dynamic tool-guidelines assembler feeding `build_system_prompt`.

## Pi parity citations (SHA 734e08e, `packages/coding-agent/src/core/tools/`)
- `edit.ts` — `oldText`/`newText`, per-field + array descriptions, top-level
  description, promptSnippet/promptGuidelines.
- `read.ts` — `offset`/`limit` "number" + "(1-indexed)" offset description.
- `write.ts` — verbatim top-level + `path`/`content` descriptions.
- `bash.ts` — `command`/`timeout` descriptions; 2000/50KB + temp-file (P0 #3).
- `grep.ts` — `ignoreCase`/`literal`/`context`/`limit` "number"; .gitignore +
  500-char line cap (`truncate.ts` `GREP_MAX_LINE_LENGTH=500`) (P0 #3).
- `truncate.ts` — shared `DEFAULT_MAX_LINES=2000`, `DEFAULT_MAX_BYTES=50KB`
  (bash + read interpolate these), `GREP_MAX_LINE_LENGTH=500`.
- `find.ts` — `pattern`/`path`/`limit`; relative paths + .gitignore (P0 #3).
- `ls.ts` — verbatim-style top-level + `path`/`limit` descriptions.

## Tests
- `tests/pi_parity/test_builtin_tool_schema_parity.py` (new) — camelCase
  property names per tool + nested `edits` items; no snake_case anywhere;
  every property (incl. nested) has a description; numeric types are `number`
  not `integer`; non-trivial top-level descriptions; functional: camelCase
  binds, legacy snake_case no longer binds (no silent write), `grep.ignoreCase`
  binds.
- `tests/tools/test_edit_tool.py`, `tests/tools/test_grep_tool.py` migrated to
  camelCase payloads.
- Gate: **3186 passed, 1 skipped, 0 regressions** (`python -m pytest -q`).

## Cross-references
- ADR-0135 (same read-pi-source → implement → test → review → merge cycle).
- ADR-0042 (original 7-tool port from pi `core/tools/`).
- ADR-0109 (`build_system_prompt` — where promptSnippet/promptGuidelines land).
- Closes gap-inventory **P0 #2**; **P0 #3** + promptSnippet/promptGuidelines
  remain explicitly tracked (`.omc/specs/pi-parity-gap-inventory.md`).
