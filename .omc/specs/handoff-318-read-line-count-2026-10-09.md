# Issue 318: physical read line counts

## Baseline and plan

- Base: `e47f1101f8b7499125ff55f0ee18d8b9873d8cdf` (`origin/main`, refreshed before isolated checkout).
- Branch: `fix/318-read-line-count`; tree: `/Users/handochan/dev/aelix-ai-worktrees/codex-318`.
- Scope authority: <https://github.com/handochan/aelix-ai/issues/318>.
- Preserve raw LF-only indexing and returned bytes; share the existing truncation counting rule for all model-facing totals and EOF.
- No persistence/API/architecture decision changed; no new ADR needed.

## Implemented and verified

- Notices count terminated, unterminated and CRLF lines correctly; offset N+1 fails after the last real line.
- Empty default reads remain successful; explicit offset 1 on an empty file reports zero lines.
- Source and bundled getting-started guide synchronized.
- Before fix: new regression probe produced 10 failures / 5 passes; after: read/helper gate 63 passed.
- Final focused gate: `uv run pytest -q tests/cli/test_docs_signpost.py tests/tools/test_read_line_counts_318.py tests/tools/test_read_tool.py tests/tools/test_the_newline_at_the_end_is_not_a_line.py` → 78 passed.
- `uv run pytest -q tests/packaging_gate/test_docs_bundle.py` → 6 passed.
- `uv run ruff check .` → All checks passed.
- `uv run python scripts/check_types.py` → 287 files, 0 errors, all 3 inverse spike assertions alive.
- Independent reviewer issue_197 measured 340 LF/CR/U+2028 EOF cases and found stale source citations. Repaired all five via `scripts/check_citations.py --fix`; `--check` now reports 954 gated, none drifted (19 existing ambiguous anchors).
- Actual `uv run aelix --provider openai-codex --model gpt-5.5 --mode json --no-session --tools read --approve -p ...`: model made two real read calls. Results: `[1 more lines in file. Use offset=3 to continue.]`, `Offset 4 is beyond end of file (3 lines total)`. Exact JSONL/probe is local `318-live.jsonl` / `318-live.py`; credentials are not included.
- Full local suite: `uv run pytest -p no:cacheprovider -q` → 58 failed, 14437 passed, 45 skipped, 3 errors in 969.79s. The inherited `NO_COLOR=1` / `TERM=dumb` disabled terminal color/width assertions; scaffold builder selected system Python 3.9 because the worktree venv lacked pip/hatchling.
- Baseline `e47f1101` renderer comparison reproduced the same 18 failures; with `NO_COLOR` unset and `TERM=xterm-256color`, both baseline and this branch passed all 136 renderer tests.
- Installed pip/hatchling only in this isolated venv, then reran all 61 failed/error node ids using `uv run --no-sync pytest -p no:cacheprovider -q` with the corrected terminal environment → 61 passed in 8.18s. No production/dependency-file change was made for this local environment issue.
- Final type gate again reports 287 files / 0 errors. Independent review approved after citation repair. macOS Python 3.12 local results are bounded evidence; the complete Windows/Linux matrix is measured by PR CI.

## Next

Implementation and independent review are complete. Create its PR and move Project 1 to In review; collect the full remote CI result. Do not merge without owner authorization. Preserve the original checkout and Claude worktrees.

## Disproved assumptions

- Fixing `_truncate.py` alone does not repair the read tool's own totals/EOF guard.
- `splitlines()` is unsuitable: it changes the raw LF-only slicing contract.
- Correct test behavior is insufficient for this repository's documentation gate: line shifts invalidate source citations and the lock.

## Delivery refresh after concurrent main movement

Claude Code merged #192 while this batch was running. Delivery base is now `6b5ffc814041de36866f79df71c51992ec76891c`. The original checkout was not edited by this workflow. Rebase completed without conflicts.

On this new base: focused tests 84 passed; Ruff passed; type gate 287 files / 0 errors with inverse assertions intact; citation gate 954 gated / none drifted. The initial full-suite and real-model observations above belong to the initial e47f1101 base. Fresh PR CI now includes Linux and Windows on Python 3.11, 3.12 and 3.13, plus both Windows installer shells.
