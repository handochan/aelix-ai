# 0121. Sprint 6h₁₄a — TUI P1 input/output polish (multiline · @file · /expand · context-meter audit)

Status: Accepted (6h₁₄a shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

The TUI completeness pass (6h₁₂ A–E, ADR-0110…0116) and the steer/permission
sprints (ADR-0119/0120) closed P0. The remaining P1 list from the 6h₁₂ audit
covered input/output affordances that are pi-faithful refinements rather than new
subsystems. This sprint ships four of them. Each was grounded against the pinned
pi source before implementation (`packages/tui/src/keybindings.ts`,
`packages/tui/src/autocomplete.ts`, `packages/coding-agent/src/cli/file-processor.ts`,
`packages/coding-agent/src/modes/interactive/interactive-mode.ts`).

All changes are in the non-protected `aelix-coding-agent` TUI layer; protected
core (`aelix-agent-core`, `docs/contracts`) is byte-unchanged.

## Decisions

### 1. Multiline input (`tui/chrome.py`)

The input `Buffer` is now `multiline=True` and the input window grows to 10 rows
(`Dimension(min=1, max=10)`) then scrolls. Two consequences:

- **Bracketed multi-line paste keeps its line breaks** (was mangled/submitted
  per-line under `multiline=False`) — the main real-world multiline need.
- **Manual newline = backslash-continuation.** A draft ending in an ODD number of
  trailing backslashes + Enter consumes one `\` and inserts a newline instead of
  submitting; `\\` is a literal backslash and submits. This is the same idiom
  Claude Code uses and is terminal-independent.

**pi divergence (documented):** pi binds `tui.input.newLine = shift+enter` /
`tui.input.submit = enter` (`packages/tui/src/keybindings.ts`). prompt-toolkit
3.0.52 **cannot distinguish Shift+Enter from Enter** — the Shift+Enter CSI-u
sequence (`\x1b[27;2;13~`) and Ctrl+Enter (`\x1b[27;5;13~`) both decode to
`Keys.ControlM` (== plain Enter). Likewise both CR (`\r`→`c-m`) and LF (`\n`→`c-j`)
are delivered as "Enter" here (the pipe-input tests submit with `\n`). So Enter
*and* Ctrl+J both submit (bound together to the accept handler), and the explicit
newline is the backslash idiom — the achievable equivalent of pi's Shift+Enter.
pi's `app.message.followUp = alt+enter` matches Aelix's existing Alt+Enter
follow-up binding exactly (ADR-0119): no collision.

### 2. `@file` mention completer (`tui/completion.py` `FileMentionCompleter`)

Typing `@` at the start of a whitespace-delimited token (anywhere in the line)
now offers filesystem path completions; selecting one inserts the path text
(`@src/foo.py`), directories carrying a trailing `/` so the user can drill in.

**Critical pi-parity correction:** an earlier scoping assumption was that an
`@mention` would expand the file *content* into the prompt. Per pi at this SHA
(`packages/tui/src/autocomplete.ts` `extractAtPrefix`/`applyCompletion`), the
interactive `@` is **purely an autocomplete affordance** — it inserts a path
string that is submitted as plain text; the file content is NOT inlined. Content
inlining (`<file>…</file>`) exists only for the CLI `@file` *argument* path
(`cli/file-processor.ts`), not in-session mentions. So Aelix's completer inserts
the path and the model reads the file with its own tools — matching pi.

**pi divergence (documented):** pi uses the `fd` binary for fuzzy whole-tree
search; Aelix does dependency-free directory-listing prefix completion, one path
component at a time (capped at `max_results`, dotfiles hidden unless a leading dot
is typed). Quoted-path (`@"…"`) handling is not ported.

The completer is merged with the slash/descriptor completer
(`shell._build_input_completer` via `merge_completers`); each sub-completer is
inert outside its own trigger. `complete_while_typing` now fires for a `/` slash
command OR an `@` token (`completion.wants_completion`), so ordinary prose still
types uninterrupted.

### 3. `/expand N` (`tui/render.py` + `tui/commands.py`)

ADR-0112 truncates tool-result cards (12 lines, 40 for errors/diffs). The full
body was discarded. Now a truncated card retains its full text under a sequential
id and surfaces it on the elision footer (`… (+K more lines · /expand N)`).
`/expand N` re-prints the full body in a panel. The store is bounded
(`_expand_max=100`, oldest evicted) so a long session can't grow it without limit.
Only truncated cards get an id (that's when `/expand` is useful); non-truncated
and descriptor-rendered cards are unchanged. This is an Aelix-native affordance
for our ADR-0112 truncation (pi has no `/expand`).

### 4. Context-meter token% — AUDIT ONLY (no code change)

The footer `◔ N% · used/window` was audited. The percentage is
`(estimate_context_tokens(messages) / context_window) * 100`, computed by
`harness._get_context_usage_safe()` — which is the **verbatim pi algorithm**
(`agent-session.ts:2946-2990`, ADR-0085). The math is correct and the heuristic
token estimate matches pi's. Replacing the heuristic with a real tokenizer would
*diverge* from pi (the primary parity goal), so it is deliberately NOT done. No
change shipped for this item; it is verified pi-parity.

## Consequences

- ruff clean; pyright 0 errors on the changed TUI source (8-baseline overall);
  full pytest green — **2993 passed, 1 skipped** (+ new tests: chrome
  backslash-continuation/double-backslash; commands /expand
  availability/usage/unknown/success; event-renderer expand-store bounded-eviction
  + diff-hint + no-id-when-not-truncated; completion @file
  listing/drill/dotfiles/mid-line/max-results + `wants_completion`; context modal
  editor-newline + select/confirm Enter-noop).
- Protected core (`packages/aelix-agent-core`, `docs/contracts`) byte-unchanged.
- **Live-verified (PTY, gpt-4o-mini):** `@RE` → `README.md` completion dropdown;
  `foo\`+Enter → newline (input grows to 2 rows, not submitted); a `read` of a
  40-line file → 12-line card + `… (+28 more lines · /expand 1)`; `/expand 1`
  reprinted the full 40 lines in a panel; footer context meter `◔ 0% · 0/128K`;
  `/help` lists `/expand`.

## Code review (separate lane) — APPROVE-WITH-NITS → fixes applied

`code-reviewer` (empirical prompt-toolkit 3.0.52 study): 0 CRITICAL / 0 HIGH;
protected core byte-unchanged; pi-parity claims independently confirmed (esp.
@file inserts path only, no content read). Findings addressed:

- **[M1]** A focused select/confirm modal that didn't bind Enter let a leaked
  Enter bubble to the chrome's global accept (verified benign — empty string
  dropped, permission NOT bypassed — but a UX papercut). FIXED: `select`/`confirm`
  now consume Enter (CR+LF) as a deliberate no-op (a confirm is never auto-answered
  by a stray Enter; a numbered select requires a digit or Esc).
- **[M2]** The multiline `editor()` modal lost newlines (Enter leaked). FIXED:
  `editor` binds Enter (CR+LF) → insert newline; Ctrl+S saves, Esc cancels.
- **[L1]** `/expand` diff-hint used a falsy `if expand_id` (0 == None). FIXED →
  `is not None`.
- **[L2]** `FileMentionCompleter` will list dirs above cwd (`@../`). Left as a
  conscious decision (user's own session, no content read, pi does whole-tree).
- **[L3]** mid-text trailing-`\`+Enter submits with a dangling backslash; **[L4]**
  diff path truncates twice (negligible). Both accepted as-is.

## Deferred

- `fd`-backed fuzzy `@` search + quoted-path mentions (kept dependency-free).
- `/resume` session picker → Sprint 6h₁₄b (ADR-0122): bigger (in-process session
  hot-swap via `switch_session` + transcript replay).
- A real tokenizer for the context meter (would break pi-parity).
