# 0193. Issue #39 + #81 — `@file` fuzzy whole-tree search + quoted mentions · large-paste collapse

Status: Accepted (shipped) — **AMENDED 2026-09-06 by #231** (`## Amendment
(2026-09-06, #231)` below): the Enumeration bullet's "shared predicate on both
enumerators / same set regardless of `.gitignore`" claim was false five ways.
Further **AMENDED 2026-09-08 by #238** (`## Amendment (2026-09-08, #238)` below):
the `@` menu's ignore behaviour is now a `/settings` toggle.
Everything else in this ADR stands.
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
- **Enumeration.** `fd` (or Debian `fdfind`) when one can be found — since #231
  in `get_bin_dir()` first, then PATH — fast + `.gitignore` aware — else a
  bounded `os.walk`. **No user input is ever passed to the
  subprocess** (enumerate-all, filter-in-Python): no regex/shell-injection
  surface. The enumeration is TTL-cached (`_TREE_CACHE_TTL = 2.0 s`) so
  keystroke-frequency completion shares one walk, and capped
  (`_TREE_ENUM_CAP = 20000`). A single shared `_EXCLUDE_DIRS` predicate
  (`node_modules`, `.venv`, `dist`, `build`, `.git`, …) is applied (and passed to
  `fd` as `--exclude`). **Corrected 2026-09-06 (#231):** this bullet claimed the
  predicate was applied to *both* enumerators and that therefore "fd and walk
  match the SAME set of files regardless of `.gitignore` presence — fd never
  changes *which* files complete". Neither half was true. The predicate reached
  the `fd` output only, and the two enumerators diverged five ways; see the
  Amendment below for what #231 repaired and what it documented. `fd` output is
  bounded at the source with `--max-results`.
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
  both + `fd --exclude`. **Corrected 2026-09-06 (#231):** the leak closed, but
  "on both" did not — the predicate post-filtered fd's output only, so a FILE
  named like an excluded dir (the `.git` gitdir pointer every linked worktree
  carries) survived the walk arm until #231. See the Amendment.
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

## Amendment (2026-09-06, #231)

The Enumeration bullet claimed a shared `_EXCLUDE_DIRS` predicate made `fd` and the
walk "match the SAME set of files regardless of `.gitignore` presence — fd never
changes *which* files complete". None of that held, in five ways.

The shared predicate was applied to fd's output **only**, so a file named like an
excluded directory diverged too — the `.git` gitdir pointer file present in every
linked worktree and submodule, measured 1458 paths vs 1457 on this issue's own
worktree. Inside a git repo fd additionally hides what git ignores and the walk
does not (a tree whose `.gitignore` names one file: six paths from the walk, five
from fd). `.git/info/exclude`, nested `.gitignore` and the global `core.excludesFile`
bite the same way and, like `.gitignore` itself, only inside a repo; `.ignore` and
`.fdignore` bite anywhere, including from a parent directory of the tree being
enumerated — so outside a repo the two arms agree exactly unless one of those two is
present. It is git's ignore machinery, not the VCS's: fd lists a file named by
`.hgignore` inside an `.hg` repo. `--type f --type d` dropped every symlink fd saw.
An undecodable filename reaches prompt_toolkit as U+FFFD from fd and as a lone
surrogate from the walk. And, unnamed in #231 and measured in this lane:
`_TREE_ENUM_CAP` bounds both arms, but only the walk spends that budget on
git-ignored paths, so in a repo with a large ignored build tree the walk truncates in
`os.scandir` order and real source directories drop out of the menu entirely while fd
does not (measured: an ignored 22 000-file `target/` beside twelve real source files
→ walk 20000 paths / 2 menu rows, fd 26 paths / 12). **Corrected 2026-09-08 (#238):**
"only the walk" held only while the `@` menu had no choice. With **Gitignore in @
menu** off the fd arm spends the same budget on ignored paths — measured on a REBUILD
of the same fixture shape, whose own numbers differ from #231's (the on arm emitted 14
where #231's emitted 26/12), `fd --no-ignore` emitted 20000/20000 and hit the cap. The
second half did not transfer ON THAT REBUILD: there, and on a variant with the real
sources at depth 6, the off arm still returned all twelve real files while the walk
returned none. **Re-measured 2026-09-08 across nine fixture shapes, that is a property
of the ignored population's SHAPE, not of the off arm.** Held in ONE ignored build tree
it survives twice the cap: 40 000 ignored files, the real directory sorting after it at
depth 12, and the off arm returned all twelve in 60 of 60 runs. Spread the same volume
over 200 top-level ignored directories and the off arm drops the whole real subtree — 0
of its 22 paths, so the drill-in listing loses it too — in 38 of 40 runs (14 of 20 with
the real sources at depth 2); at 50 such directories, once in 15. The rate is
scheduling-dependent and was measured under concurrent load; the occurrence is not.
Both arms therefore lose whole unreached subtrees past the cap, and what differs is the
ORDER — `os.scandir` for the walk, fd's parallel discovery order for the off arm, which
is also why the off arm's kept set is unstable: 1556 of 20000 kept paths differed
between consecutive runs. See the #238 amendment below. Where the real tree itself
exceeds the cap both arms truncate and fd's kept set is not stable between runs (1747
of 20000 paths differed). The cap's value is unchanged; #231 documents it.

#231 keeps the decision — fd is the ignore-aware path, the walk is the
dependency-free fallback — and changes four things: the docstrings state the
divergences instead of denying them; `_has_excluded_component` is applied to **both**
arms, which is what makes the "shared predicate" half true; `--type l` closes the
symlink half (with both changes fd's set equals the walk's exactly, on a tree
carrying symlinks *and* excluded-name entries); and `_fd_binary()` looks in
`get_bin_dir()` **before** PATH — the order `ensure_tool` already gives `find`/`grep`
and Pi's `getToolPath` uses — so the fd Aelix downloads for `find` is the fd the
completer uses. That last one does not remove the divergence, it moves the
population: the walk is now the arm of a machine that never ran `find`, which is the
air-gap population this ADR wrote the fallback for. On the owner's own checkout —
nine agent worktree copies of the repo plus a session-state tree — the difference is
12618 candidate paths versus 1482; on a clean clone of the same commit it is 1457
versus 1457, because everything this repo ignores is already in the shared exclude
list. The magnitude is a property of a checkout, not of the repository, and it
drifts: the same walk measured 12606 earlier the same day. The non-UTF-8 asymmetry is
documented and left open (APFS cannot hold such a name, so whether the walk arm
renders or raises is unmeasured). The 2026-07 note "owner-confirmed: fd = speed
upgrade only" stands as a record of what was confirmed then, and is now known wrong
on both halves: fd is *slower* below roughly 5 000 paths (spawn cost), and it changes
which files complete.

## Amendment (2026-09-08, #238)

The #231 amendment left one thing open and the owner has now decided it: with the
managed `fd` resolved first, a machine that has ever run `find` gets the narrow menu
whether or not it wants one. #238 adds a `/settings` toggle — *"Gitignore in @ menu"*,
global scope, **default on** — that switches the ignore rules off for the `@` menu
only. The owner's phrasing was *"@ menu: respect .gitignore"*; the row is labelled
`Gitignore in @ menu` instead because `_open_settings` sizes the label column to the
longest label + 2 — 24 today — and a 26-character label would shift the value column of
every row (measured: 19 chars → column unchanged at 24; 26 → 28). Off, the fd arm is
asked for `--no-ignore`; the walk arm is unchanged because it never applied ignore
rules at all, which is what makes one flag enough. Measured on managed fd 10.4.2:
`--no-ignore` lifts root and nested `.gitignore`,
`.git/info/exclude`, `.ignore`, `.fdignore`, the global ignore file, and the same
files in parent directories of the enumerated tree; `--no-ignore-vcs` was rejected
because it leaves `.ignore`/`.fdignore` in force. `--exclude` is untouched by either
flag, so `_EXCLUDE_DIRS` holds on both arms of both settings — by two mechanisms that
are not interchangeable: the shared `_has_excluded_component` predicate is what makes
the two sets EQUAL (tokens removed and the cap lifted, fd's 32 394 paths filter down
to exactly the walk's 12 630, symmetric difference empty), while the `--exclude`
tokens are what keep the off arm inside `--max-results` (12 630 of a 20 000 budget
with them, 32 394 without). Removing them as "redundant with the predicate" would
truncate the off arm at 20 000, by an amount not even stable run to run (symmetric
difference against the walk 637 on one run and 6 801 on the next, the two truncated
sets differing from *each other* by 7 438). Under the cap the off arm is not an
approximation of the fallback but the same set: on the owner's checkout on
2026-09-07, 12 630 = 12 630, empty symmetric difference both ways, where the on arm
returns 1 498. That count drifts — #231 measured 1 482/12 618 the day before and a run
under an hour earlier the same evening read 1 494/14 103 — the equality does not. The default
stays ON for the reason #231 rejected `--no-ignore` for everyone: the wide arm spends
`_TREE_ENUM_CAP` on ignored paths, so a large ignored build tree can still truncate
the menu — now as a choice the user made rather than a property of their `PATH`. The
setting is read from the GLOBAL scope only, not the merged view, so the row cannot
display a project override it just failed to write. It is read through a callable on
every enumeration with the flag in the tree cache's key, so a flip is answered by the
next keystroke (unkeyed, measurably stale for a full 2 s TTL) — the first PULL-shaped
live row in `settings_rows.py`, whose `_apply_live_setting` branch is a documented
no-op because the shell does not hold the completer. Where no `fd` exists the toggle
changes nothing until one arrives, and then the chosen value is honoured at the next
`@`, no restart. Pi has no equivalent setting.
