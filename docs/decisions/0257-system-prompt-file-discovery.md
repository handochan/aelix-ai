# ADR-0257: Discover system prompt files at each harness build

Status: Accepted

Date: 2026-10-09

Issue: [#287](https://github.com/handochan/aelix-ai/issues/287)

## Context

Users can already supply literal or file-based system prompt flags and agent
profiles. They need persistent customization without repeating flags. Pi's
current reference (`ce950d78f424dcaf9f5d6a03ce80ab141130eb1d`, checked 2026-10-08)
discovers `SYSTEM.md` and `APPEND_SYSTEM.md` in the trusted project directory
before the global agent directory (`core/resource-loader.ts:653-672,1209-1234`).
Pi #5049 proposed replacing only the role preamble and is closed; this issue
preserves the existing full replacement semantics.

## Decision

All implementation stays in `aelix-coding-agent`. The kernel continues to receive
an assembled prompt and its existing rebuild callback.

1. Aelix MUST look for `<cwd>/.aelix/SYSTEM.md` and `APPEND_SYSTEM.md`, then
   `<agent_dir>/SYSTEM.md` and `APPEND_SYSTEM.md`. `agent_dir` respects
   `AELIX_CODING_AGENT_DIR`, defaulting to `~/.aelix/agent`. Discovery is restricted
   to the current directory; it does not walk project ancestors.
2. Base precedence MUST be explicit `--system-prompt` / `--system-prompt-file`
   (their existing literal-over-file rule), profile `system_prompt: replace`,
   trusted project `SYSTEM.md`, global `SYSTEM.md`, built-in prompt. A discovered
   `SYSTEM.md` replaces the entire generated body, including tool guidance and
   extension/documentation signposts. Project context and skills still append.
3. Explicit `--append-system-prompt` or `--append-system-prompt-file` MUST suppress
   `APPEND_SYSTEM.md` discovery, including an explicitly empty append. Otherwise,
   Aelix selects one file: trusted project before global. A profile with
   `system_prompt: append` is a separate identity chunk and MUST NOT suppress
   discovery. Composition is base → profile append → explicit appends OR the
   discovered append file → AGENTS context → skills catalog, preserving ADR-0217.
4. Project files MUST engage the existing Project Trust gate even when they are
   the only local resources. Untrusted project candidates MUST NOT be read.
   Global files and explicit prompt flags retain their user-choice semantics.
   `--no-context-files` only controls AGENTS context, not these prompt files.
5. Discovery MUST run through both shared composition helpers on each factory
   build and live prompt rebuild. `/new`, `/fork`, `/resume`, `/reload` and
   `/agents use` therefore observe edits. Discovery MUST NOT mutate the explicit
   append accumulator, so successive rebuilds cannot duplicate chunks.
6. Files MUST be UTF-8; a leading BOM is removed. Blank or whitespace-only files
   contribute nothing. Selecting an empty `SYSTEM.md` restores the built-in
   body instead of falling through to the global file. An empty append masks
   the global append too. Read failures warn on stderr and continue without
   that file; they also mask lower-priority candidates. Directories, devices and
   FIFOs are refused before reading, as are files reported above 1 MiB by the
   descriptor check. Discovered Markdown does not undergo the profile-frontmatter
   stripping used by explicit flags.
   The loader MUST open once with nonblocking semantics where supported,
   validate the opened descriptor, and read at most 1 MiB plus one byte before
   decoding. Growth after validation is rejected by the read bound; pathname
   replacement cannot substitute another object. CRLF and CR normalize to LF,
   preserving the former text reader's universal-newline behavior. Missing
   candidates, including dangling symlinks, continue to the next candidate;
   selected empty or failed candidates retain the masking behavior above.
7. The startup banner MUST show only successfully loaded, nonempty discovered
   source paths, ordered SYSTEM → APPEND_SYSTEM → AGENTS. Provenance is produced
   during composition and explicitly passed to the TUI; rendering does not
   read prompt files again. Paths and warnings MUST remove terminal controls.
8. Delegated processes use their existing explicit profile prompt-file flags.
   A replace profile overrides discovered SYSTEM but still discovers APPEND.
   An append profile permits discovered SYSTEM but its explicit append flag
   suppresses discovered APPEND. Project discovery in either case is subject to
   the child's inherited trust decision. This intentional difference from the
   in-process profile overlay preserves the child's explicit CLI contract.
9. File names are spelled exactly `SYSTEM.md` / `APPEND_SYSTEM.md`. Discovery
   uses normal filesystem lookup: lowercase alternatives work only on a
   case-insensitive filesystem. No additional lowercase search is performed.

## Consequences

Users can set language, tone and workflow through a persistent file. Replacing
the generated prompt also means maintaining their own tool instructions; use
APPEND_SYSTEM.md to preserve those instructions. File edits become effective
when the prompt rebuilds, not while a provider request is already in flight.

This supersedes the deferred automatic-file-discovery portions of ADR-0034,
ADR-0090 and ADR-0149. Their remaining decisions are unchanged. Partial role
replacement, new child flags, ancestor search and general ResourceLoader
unification are outside this decision.
