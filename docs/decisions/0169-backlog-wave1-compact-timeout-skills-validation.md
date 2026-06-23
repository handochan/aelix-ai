# ADR-0169 — Backlog Wave 1: /compact neutral no-op (#10), bash/search timeout policy (#11), skill loading + /skills (#12), real tool-arg validation (#13)

- **Status:** Accepted
- **Date:** 2026-06-23
- **Sprint:** Wave 1 (backlog audit 2026-06-23)
- **Relates:** ADR-0142/0117 (compaction pipeline), ADR-0136/0139 (tool schema/behavior parity), ADR-0109 (agent enablement / toolset wiring), ADR-0069 (skills loader + ExtensionRunner). Backlog: GitHub issues #10/#11/#12/#13 (parents #4/#6/#3/#6).
- **Out of scope (deferred):** #9 (extension `register_command` → executable in TUI) was carved out for a dedicated architecture study (see the separate direction proposal); the loop governor #14, skill **prompt injection**, and `register_shortcut`/`register_message_renderer` dispatch remain follow-ups.

## Context

The 2026-06-23 backlog code-audit found four "rails exist, train missing" gaps that the Wave-1
sprint closes. aelix runs **everything from modest local LLMs to frontier models** — every decision
below is constrained by that range: protect against weak-model failure modes (omitted args, loosely
typed args, hangs) without locking behavior to small-LLM-only or breaking frontier use (long builds,
permissive MCP schemas).

- **#10:** `harness.compact()` *raises* `AgentHarnessError("invalid_state", "Nothing to compact")`
  for the empty/already-compacted case; the TUI `_compact_handler` caught it with a bare
  `except Exception` and painted it bold-red `✖ compact failed`, while the intended neutral branch
  (`if result is None`) was dead (compact never returns None). The compaction pipeline itself is
  healthy — the cut-point logic was re-verified to protect must-keep context (see Decision).
- **#11:** bash had **no default timeout** — `proc.wait(None)` waits forever when a model omits
  `timeout`; only Esc recovers. grep/find hard-coded a 30s subprocess timeout. pi has neither a
  default nor a cap (it assumes capable models + interactive Esc).
- **#12:** the `harness/skills.py` loader (38 tests) was fully real but **unwired**: no
  `load_skills()`/`set_skills()` caller, `--skill`/`--no-skills` parsed but unconsumed, no `/skills`
  command.
- **#13:** `validate_tool_arguments` was a NO-OP stub (`return dict(args)`); malformed tool-args were
  never structurally re-grounded, and weak-model loosely-typed args were never coerced.

## Decision

### #10 — neutral "Nothing to compact" (`tui/commands.py`, pure consumer)
`_compact_handler` now discriminates the harness's deliberate no-op signal (duck-typed on
`exc.code == "invalid_state"` and `"Nothing to compact" in str(exc)`, mirroring core's own
auto-compaction guard) and renders it **neutral yellow**; the dead `result is None` branch is removed.
Genuine failures (including the *other* `invalid_state`, "requires options.session") still render red.

**Compaction context-preservation re-verified (no change):** `KEEP_RECENT_TOKENS=20000` keeps the
recent suffix; `find_valid_cut_points` never cuts on a `toolResult` role (a tool result stays with
its call); the cut backs up over control entries so the first-kept entry is a message/compaction;
split-turn detection summarizes the prefix separately so the retained suffix keeps context; prior
compaction summaries are dropped from re-summarization; the system prompt lives in `Context`, not the
session tree, so it is never touched.

### #11 — Aelix-additive default + max bash timeout; configurable search timeout (`tools/bash.py`, `grep.py`, `find.py`, `cli/entry.py`)
An **intentional divergence from pi** (justified by the weak-model constraint), all env-overridable:

- **default 600s / cap 3600s.** The default is armed ONLY when the model omits `timeout` (or passes
  ≤0); an explicit value is always honored, clamped to the cap. `AELIX_BASH_DEFAULT_TIMEOUT=0`
  restores pi's unbounded behavior; `AELIX_BASH_MAX_TIMEOUT=0` lifts the cap (full-CI / hour-plus
  compiles). Wired via `create_all_tools` options from `_tool_options_from_env()`.
- A new **`ExecExitResult.timed_out`** flag authoritatively distinguishes a timeout-kill from an
  abort/signal-kill (both yield `exit_code=None`) — necessary because, with a default always armed,
  "was a timeout set?" can no longer infer the cause; an Esc-abort must still read as "Command
  aborted". Defaults `False` so custom `BashOperations` impls keep working.
- The timeout status is **actionable**: it tells the model to retry with a larger `timeout` (up to
  the cap). When an explicit value was clamped, the message instead states the cap was applied and
  how to lift it (rather than "retry up to" a value the model already exceeded).
- grep/find expose `AELIX_TOOL_SEARCH_TIMEOUT` (default 30s) via the same env wiring (they cannot
  hang the loop — `run_cancellable` bounds them — but a huge-repo scan can need a larger window).

### #12 — skill loading at startup + `/skills` (`cli/entry.py`, `tui/commands.py`, pure consumer)
`entry.py` loads skills ONCE (dirs are stable) and re-applies them via `harness.set_skills()` on
**every** harness build, so `/resume`/`/new`/`/fork` rebuilds never lose them; diagnostics are emitted
once. `_resolve_skill_dirs` consumes `--skill` (treated as a path, since aelix has no skill package
manager) and `--no-skills` (drop defaults, keep explicit paths). Default dirs: the global
`~/.aelix/agent/skills` plus the project-local `<cwd>/.aelix/skills` — the latter **gated behind
project-trust** (a malicious project `SKILL.md` is a prompt-injection vector once skills reach the
model, like project-local extensions/MCP). A read-only `/skills` command lists loaded skills,
flagging `disable-model-invocation` ones. **Prompt injection into the system prompt is a follow-up.**

### #13 — real coerce-then-validate (`aelix-ai/tools.py`, `aelix-agent-core/loop.py`, `aelix-ai/pyproject.toml`)
`validate_tool_arguments` now mirrors pi's `validateToolArguments` (coerce → validate) using
`jsonschema` (pinned `>=4.18,<5` — the `referencing`-based line, dropping the pre-4.18 `RefResolver`
that auto-fetched remote `$ref` = an SSRF vector):

- **Lenient, pi-faithful coercion** for the full source→scalar table (not just strings): `null→0`,
  `bool→1/0`, numeric string→number for number/integer; `null→False`, `1/0→True/False`,
  `"true"/"false"→bool` for boolean; `null→""`, `bool/number→string` for string. Booleans match
  **case-insensitively** — a deliberate aelix divergence (pi is case-sensitive) for weak models that
  emit `"True"`. Unknown keys are **preserved** (additive, never stripped). Ambiguous values are left
  unchanged so the validator surfaces the real type error.
- On failure, raises `ToolArgumentValidationError` with a structured, path-qualified, model-readable
  message echoing the (truncated) original args. `loop._prepare_tool_call` catches **only** that type
  and converts it into an `is_error` `_Immediate` tool result — the model **re-grounds**; it is never
  an uncaught crash. (Both edits land together — the call site existed but the body was a no-op.)
- **Never crashes on an arbitrary schema:** MCP-/extension-registered tools may ship empty, malformed,
  recursive, or unresolvable-`$ref` schemas. Validator construction/iteration is wrapped in a broad
  guard that degrades to pass-through (matching pi's broad catch and #13's own "malformed schema →
  pass through" contract). Empty schemas no-op.

## Verification

- Full gate: **4062 passed, 1 skipped** (from 4057 pre-sprint; +5 net new behavior tests beyond the
  ~38 added). New tests: compact neutral-vs-red (#10); bash default/clamp/disable/uncapped/real-kill +
  schema description + clamped-message (#11); env-options wiring (#11); skill-dir composition + trust
  gating + `/skills` list/empty/missing (#12); validator full coercion table + unknown-key
  preservation + required/type/nested errors + empty/malformed/recursive/unresolvable-schema
  pass-through + loop re-grounding (#13).
- A 4-lens adversarial-review workflow (correctness / weak-model-robustness / security / design-tests)
  with per-finding adversarial verification confirmed 7 findings against pi ground truth; **all were
  fixed before commit**: the coercion was broadened from string-only to pi's full table (HIGH); the
  validator guard was broadened so recursive/unresolvable-`$ref` MCP schemas pass through instead of
  crashing the turn (HIGH); the jsonschema floor was raised to 4.18 (SSRF); the clamped-timeout
  message was made honest; the case-insensitive-bool divergence was documented as intentional.

## Consequences

- `validate_tool_arguments` is now a real gate on every tool dispatch; weak models that emit loosely
  typed args dispatch anyway, and malformed args re-ground the model instead of failing ad hoc.
- bash can no longer hang the loop indefinitely on an omitted timeout, while frontier long-builds keep
  working (generous default, explicit override up to a high cap, env knobs to disable either bound).
- `jsonschema` is now a first-class dependency of `aelix-ai`.
- Protected-core touch was confined to #13's necessary edits (`aelix-ai/tools.py`,
  `aelix-agent-core/loop.py`); #10/#11/#12 are pure coding-agent changes.
