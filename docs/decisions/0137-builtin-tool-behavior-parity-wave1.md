# 0137. Built-in Tool Behavior Parity — Wave 1 (bash/write/ls/grep/find + shared helpers)

Status: Accepted
Date: 2026-06-17
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Gap-inventory **P0 #3** ("tool behavior divergences") is the behavior layer
beneath ADR-0136's schema parity. A parallel 7-agent deep-research sweep
(re-fetching pi at the pin) classified the work as **43 CORE / 5 HEAVY / 9 NA**
divergences. That is too large and too risky for one commit, and two tools
(`read`, `edit`) require near-rewrites (right-to-left original-content matching,
fuzzy fallback, custom diff, `prepareArguments`; 1-indexed offset, `truncate_head`
+ continuation notices, image resize). So P0 #3 is split into waves:

- **Wave 1 (this ADR):** the 5 "mechanical" tools — `bash`, `write`, `ls`,
  `grep`, `find` — plus the shared `_truncate.py` / `_path_utils.py` helpers.
- **Wave 2 (ADR-0138, deferred):** `read` + `edit` rewrites.
- **HEAVY (deferred):** ensureTool binary auto-download, image resize,
  `ctx.model` non-vision note, bash spawn-hook plumbing.

Implementation used a parallel workflow (5 Opus implementers on disjoint tool
files; shared helpers landed centrally first), then an adversarial review
workflow (19 agents). All findings were folded back in (see *Review*).

## Decision

### Shared helpers (central)
- `_truncate.py`: exported pi constants `DEFAULT_MAX_LINES=2000`,
  `DEFAULT_MAX_BYTES=50*1024`, `GREP_MAX_LINE_LENGTH=500`; `TruncationInfo`
  gained `truncated_by` (`"lines"`/`"bytes"`) and `last_line_partial`;
  `truncate_head`/`truncate_tail` **rewritten to pi's "never returns partial
  lines" contract** — accumulate COMPLETE lines within the byte budget, never
  splitting a line or a multibyte UTF-8 char (char-safe `errors="ignore"` only
  on the single-line-exceeds edge); `truncate_line` suffix is ASCII
  `"... [truncated]"`.
- `_path_utils.py`: `expand_path` (NFC + unicode-space collapse + single leading
  `@` strip + leading `~`/`~/` home expansion) with `resolve_to_cwd` routed
  through it; `relativize_to_posix` (pi `formatPath` — POSIX-relative, basename
  fallback).

### Per-tool behavior
- **bash**: caps → 2000/50KB; full untruncated output saved to
  `<tmpdir>/pi-bash-<hex>.log` only when truncated; pi `formatOutput`
  truncation notices (`[Showing lines …]` / `[Showing last … of line N
  (line is …)]`, the latter using the **last line's** byte size); exact status
  text (`Command aborted` / `Command timed out after N seconds` /
  `Command exited with code N`); `(no output)` empty success.
- **write**: `Successfully wrote {N} bytes to {path}` where `{path}` is the RAW
  arg and `{N}` is the UTF-16 code-unit count (JS `.length`); file still written
  UTF-8.
- **ls**: case-insensitive sort (`casefold`); 50KB byte cap; entry-limit + byte
  notices; incremental limit loop (strictly-greater semantics); stat-failure
  skip; `(empty directory)` sentinel (no details); `limit` is nullish
  (`limit=0` preserved → empty listing).
- **grep**: 500-char line cap; 50KB byte cap; pi notices; `effectiveLimit =
  max(1, limit ?? 100)`; output paths relativized to the search dir; `--hidden`;
  pi `formatBlock` output format `path:N: text` (space before content; `:`
  match / `-` context markers).
- **find**: output paths relativized to POSIX-relative; fd argv
  (`--color=never --hidden --no-require-git --max-results` + full-path / `**/`
  prefix logic); `effectiveLimit` nullish; notices + 50KB cap;
  `Path not found: {base}`.

### Descriptions
bash/grep/find/ls top-level descriptions upgraded to verbatim-pi **where now
truthful** (bash temp-file + 2000/50KB; grep 500-char; find "relative to the
search directory"; ls byte cap). Option-A divergences kept: byte cap stated as
**50KB** (pi's CODE value) not pi's description literal "1024KB" (a pi doc bug);
`.gitignore` still omitted from grep/find (the pure-Python fallback doesn't
honor it — guaranteeing it is the HEAVY ensureTool item).

## Consequences

- The 5 mechanical tools now behave pi-faithfully for truncation, temp-file
  recovery, notices, path relativization, sort, and limit semantics.
- **Preserved intentional divergences** (gap-inventory line 94, not defects):
  find strict `>` (exact-limit not flagged) vs pi `>=`; bash `$SHELL`-first;
  the Python rg/fd fallbacks (no guaranteed `.gitignore`); `*ToolDetails` keep
  aelix's bool shape; grep keeps rg text-mode (not `--json`); bash abort-vs-
  timeout inferred from `exit_code is None` + timeout arg.

## Review (adversarial, 19 agents)

Both lanes **APPROVE_WITH_NITS**; per-tool parity = write/ls faithful, find
divergent-but-documented, bash/grep had real nits. **10 confirmed defects (all
LOW/MEDIUM) — ALL FIXED:**
1. (bash, M) partial-line `(line is X)` used whole-output bytes → now the last
   line's bytes (pi `getLastLineBytes`).
2. (bash, L) stale pre-Wave-1 comment → updated.
3. (ls, L) `limit=0` falsy-coalesced to 500 → nullish (preserved).
4/5/8. (ls/grep/find, M/L) `truncate_head` raw-byte-sliced (split mid-line +
   mid-UTF-8-char → U+FFFD) → rewritten whole-line + char-safe (also fixed
   `truncate_tail`).
6/7. (grep, L) output missing the space after `path:N:` and `-` context markers
   → pi `formatBlock` format on both rg and Python-fallback paths.
9. (ls, M) empty dir returned `""` → `(empty directory)` sentinel.

One reviewer claim was **corrected, not blindly applied**: the "`limit=0` should
emit a `0 entries limit reached` notice" guess was wrong — re-reading pi `ls.ts`
showed the `results.length===0 → "(empty directory)"` check runs AFTER the loop
and wins for `limit=0`. Implemented per pi source, not the reviewer's guess.

## Deferred — tracked, MUST land next

1. **Wave 2 (ADR-0138): `read` + `edit`** — read: 1-indexed offset, byte cap via
   `truncate_head` + continuation notices, drop `cat -n` numbering, `split("\n")`,
   magic-byte mime, `truncated` flag semantics; edit: right-to-left
   original-content matching, fuzzy fallback (NFKC + smart-quote/dash/space),
   overlap detection, `prepareArguments`, success-message-not-diff, pi custom
   diff format, exact error strings, raw path. After Wave 2, upgrade read's
   description (1-indexed offset + byte cap) to verbatim-pi.
2. **HEAVY**: ensureTool binary auto-download (grep `rg` / find `fd`) →
   guaranteed `.gitignore` + hidden, then add `.gitignore` back to grep/find
   descriptions; read image resize (Pillow) + `ctx.model` non-vision note; bash
   `commandPrefix`/`spawnHook`/`shellPath` plumbing; read compact-card TUI.

## Pi parity citations (SHA 734e08e, `packages/coding-agent/src/core/tools/`)
- `bash.ts` (OutputAccumulator + formatOutput + appendStatus), `truncate.ts`
  (truncateHead/Tail + constants), `write.ts`, `ls.ts`, `grep.ts` (formatBlock +
  formatPath), `find.ts`, `path-utils.ts` (expandPath + formatPath).

## Tests
- 5 tool test files updated for the new behavior + new behavior tests, incl.
  regression locks for the review's test-gaps: bash multi-line partial notice
  (exact `(line is 300B)`), ls `(empty directory)`, ls `limit=0`. Gate:
  **3221 passed, 1 skipped, 0 regressions** (`python -m pytest -q`).

## Cross-references
- ADR-0136 (tool schema parity; its deferred description-upgrades partly
  realized here). ADR-0042 (original tool port). Closes gap-inventory **P0 #3
  Wave 1**; **Wave 2 (read/edit) + HEAVY** remain tracked.
