# 0193. Issue #39 + #81 — `@file` fuzzy whole-tree search + quoted mentions · large-paste collapse

Status: Accepted (shipped)
Date: 2026-07-12
Supersedes-deferred: ADR-0121 §Deferred (fd-fuzzy `@` search + quoted-path mentions)

## Context

ADR-0121 (Sprint 6h₁₄a) shipped the interactive `@file` mention completer as
**dependency-free directory-listing prefix completion, one path component at a
time**, and explicitly deferred two pi behaviours (ADR-0121 §Deferred):

- `fd`-backed **fuzzy whole-tree** search, and
- **quoted-path** (`@"path with spaces"`) mentions.

Issue #39 tracked that deferral, `blocked_by: design-decision` — whether to take
an `fd` dependency versus stay dependency-free. Issue #81 (separate, Claude-Code
inspired, Aelix-original) asked that a **large paste into the input box be
collapsed** to a compact `[Pasted text #N +M lines]` placeholder, with an
immediately-repeated identical paste revealing the raw text.

Both are in the non-protected `aelix-coding-agent` TUI layer; protected core
(`aelix-agent-core`, `docs/contracts`) is byte-unchanged.

Two design decisions were put to the owner and confirmed:

1. **#39 fuzzy strategy →** *`fd` when present + pure-Python `os.walk` fallback.*
   `fd` is only ever a speed upgrade; every user gets fuzzy matching with no hard
   binary dependency, preserving Aelix's dependency-free / air-gap posture.
2. **#81 display →** *compress the input box only.* The transcript echo and the
   model prompt both receive the full original text; only the editor shows the
   placeholder.

## Decisions

### 1. `@file` fuzzy whole-tree + quoted mentions (`tui/completion.py`)

- **Fuzzy whole-tree.** A non-trivial `@` prefix is matched as a case-insensitive
  *subsequence* against every relative path in the tree (`@comp` →
  `src/…/completion.py`), scored (contiguous runs, word/path-boundary and
  basename hits rewarded; gaps and length penalized; exact prefix strongly
  boosted), ranked, and capped at `max_results`. Empty and trailing-slash
  prefixes (`@`, `@src/`) keep the cheap one-level directory listing (fast
  drill-in, no walk); a no-fuzzy-hit prefix falls back to that listing too.
- **Enumeration.** `fd` (or Debian `fdfind`) when on PATH — fast + `.gitignore`
  aware — else a bounded `os.walk`. **No user input is ever passed to the
  subprocess** (enumerate-all, filter-in-Python): no regex/shell-injection
  surface. The enumeration is TTL-cached (`_TREE_CACHE_TTL = 2.0 s`) so
  keystroke-frequency completion shares one walk, and capped
  (`_TREE_ENUM_CAP = 20000`). A single shared `_EXCLUDE_DIRS` predicate
  (`node_modules`, `.venv`, `dist`, `build`, `.git`, …) is applied to **both**
  enumerators (and passed to `fd` as `--exclude`), so fd and walk match the SAME
  set of files regardless of `.gitignore` presence — fd never changes *which*
  files complete. `fd` output is bounded at the source with `--max-results`.
- **ThreadedCompleter.** The file completer is wrapped in
  `ThreadedCompleter` (`shell._build_input_completer`) so its fd subprocess /
  `os.walk` runs off the prompt-toolkit event-loop thread — a large monorepo or a
  stalled `fd` can no longer freeze the UI or the token stream. The cheap slash
  completer stays synchronous.
- **Quoted mentions.** `_extract_mention` is a **quote-aware left-to-right scan**:
  `@"path with spaces"` is one mention (whitespace inside the quotes does not
  terminate it), an `@` typed *inside* an open quote is a literal path char (not a
  fresh mention), and a closed quote ends the mention. A completion whose path
  contains a space — or any completion under an open `@"` — is inserted quoted;
  the quote stays open for a directory (drill continues) and closes for a file.

### 2. Large-paste collapse (`tui/chrome.py`)

- **Collapse.** An app-level `Keys.BracketedPaste` binding overrides
  prompt-toolkit's default paste handler. A paste with ≥ `_PASTE_COLLAPSE_MIN_LINES`
  (6) lines OR ≥ `_PASTE_COLLAPSE_MIN_CHARS` (1000) chars is replaced in the
  editor by a `[Pasted text #N +M lines]` placeholder; the original is held in a
  per-session registry. Small pastes insert raw. Line endings are normalized
  (`\r\n`/`\r` → `\n`) exactly as the default handler did.
- **Expand at submit.** The Enter/`_accept` and Alt+Enter/`_follow_up` handlers
  expand placeholders back to the original text before it leaves the buffer, so
  the model (and every submit sink — queue, steer, follow-up) receives the FULL
  content; only the input box was ever compressed. History stores the expanded
  text (self-contained Up-arrow recall). Expansion is a **single-pass regex** over
  the registered placeholders (longest first) so spliced-in original text is never
  re-scanned / double-expanded.
- **Reveal.** An immediately-repeated identical paste **replaces** the placeholder
  with the raw text (a true "reveal") and pops its registry entry — it must not
  leave the placeholder in the buffer, or submit would re-expand it and send the
  content twice.
- **State.** `#N` is monotonic across the session (like Claude Code). The registry
  is cleared on submit / Ctrl+C-clear / `set_editor_text`, and bounded
  (`_PASTE_REGISTRY_MAX = 100`) with eviction that only drops entries whose token
  is no longer in the live buffer (a still-visible token is never stranded).
  `get_editor_text()` expands, so the Ctrl+G external editor and Alt+Up dequeue
  operate on the real content (a user can never edit/lose an opaque token).

## Consequences

- ruff clean; pyright adds **0** new errors on the changed source (chrome.py's
  pre-existing `_PlaceholderProcessor.apply_transformation` override and shell.py's
  5 pre-existing errors are baseline). Protected core byte-unchanged.
- Full suite green — **5333 passed, 1 skipped** (+ new/updated tests:
  `test_completion.py` fuzzy across-path, empty/trailing-slash listing, fd + walk
  exclude parity, fd end-to-end + failure-fallback, quote-aware extraction,
  quoted-dir drill-in, max-results ordering, symlink no-hang; `test_paste_collapse.py`
  collapse thresholds, submit-expand, reveal-single-copy, three-paste real flow,
  get/set_editor_text, multi/nested placeholder, live-token eviction).

## Adversarial review (separate lane) — 6 lenses / 23 agents, 15 findings, all addressed

A multi-lens workflow (correctness×2, security, prompt-toolkit, integration,
test-coverage) with per-finding adversarial verification surfaced:

- **[HIGH]** "Paste again to reveal" appended raw text but left the placeholder +
  its registry entry → submit re-expanded it and sent the content TWICE. FIXED:
  reveal now REPLACES the placeholder and pops the entry.
- **[MEDIUM]** fd enumeration honoured only `.gitignore`, diverging from the walk
  fallback's `_EXCLUDE_DIRS` in a gitignore-less tree (node_modules leaked; a
  shipped test was fd-environment-coupled). FIXED: shared exclude predicate on
  both + `fd --exclude`.
- **[MEDIUM]** fuzzy enumeration ran synchronously on the event loop → UI freeze on
  a large tree / stalled fd. FIXED: `ThreadedCompleter`.
- **[MEDIUM]** Ctrl+G external editor / Alt+Up dequeue saw the opaque placeholder;
  editing it lost the paste. FIXED: `get_editor_text()` expands.
- **[LOW]** `_extract_mention` was quote-unaware (an `@` inside an open quote
  mis-parsed to a broken buffer). FIXED: quote-aware forward scan.
- **[LOW]** registry eviction could strand a still-visible token; fd stdout was
  fully buffered before the cap. FIXED: non-live-only eviction; `--max-results`.
- **[robustness]** `_expand_pastes` could double-expand a nested live token. FIXED:
  single-pass regex.
- Remaining test-coverage findings addressed with the new tests listed above.

## Deferred

- A very long *unclosed* `@"…` mention keeps whitespace-containing prose as its
  prefix (fuzzy returns nothing → an empty, invisible menu attempt per keystroke).
  Benign; not hardened further.
- Exposing the collapse thresholds as a `/settings` row (kept as module constants;
  a follow-up for the settings surface, cf. issue #84).
