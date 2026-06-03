# 0129. Sprint 6h₂₁ — TUI `/import` + `/fork` + `/clone` + `/tree`

Status: Accepted (6h₂₁ shipped)
Date: 2026-06-03
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Audit LOW bundle (closing the remaining session-lifecycle slash commands from
the 6h₁₅ TUI pi-parity audit). pi's `BUILTIN_SLASH_COMMANDS`
(`slash-commands.ts`) ships `/import`, `/fork`, `/clone`, `/tree`. The
underlying runtime APIs were already ported in earlier sprints —
`AgentSessionRuntime.import_from_jsonl` (Sprint 6h₅b, ADR-0083),
`AgentSessionRuntime.fork` (Sprint 6h₄c, ADR-0079), and the
`parent_session_path` lineage field on `JsonlSessionMetadata` (Sprint 4b §E) —
but the TUI never wired any consumer.

This sprint is a **pure TUI consumer** sprint: 4 handlers + 4 host closures +
their wiring. No protected-core changes. Each closure delegates to the
already-tested runtime API and mirrors the proven `/resume` (ADR-0122) +
`/new` (ADR-0123) pattern: `chrome.running` guard → hot-swap via runtime →
extension-cancel check → repaint + footer refresh.

## Decision (2 files; non-protected)

### Handler core (`commands.py`)

Four new `BuiltinCommand` entries + four new `CommandContext` callback fields:

| Command | Args | CommandContext field | Pi parity |
|---|---|---|---|
| `/import <path>` | required | `import_session(path) → Awaitable[None]` | `agent-session-runtime.ts:329-364` |
| `/fork` | none | `fork_session() → Awaitable[None]` | `agent-session-runtime.ts:262-280` (`position="before"`) |
| `/clone` | none | `clone_session() → Awaitable[None]` | `agent-session-runtime.ts` (`fork` at leaf, `position="at"`) |
| `/tree` | none | `tree_action() → Awaitable[None]` | walks `parent_session_path` via `load_jsonl_session_metadata` |

Each handler defends against the missing callback (degrades with a yellow
"Foo is unavailable" `Text`) and wraps the `await` in try/except so a
runtime-side failure surfaces as `✖ {cmd} failed: {exc}` rather than
crashing the REPL — exactly mirroring the `_resume_handler` /
`_new_handler` precedents.

`_import_handler` is the only command with an arg: a missing or
whitespace-only path short-circuits with the usage hint instead of
dispatching.

### Host closures (`shell.py`)

Four closures defined inside `run_tui` (so they capture `runtime_host`,
`out_chrome`, `_commit`, `renderer`, `context`):

- **`_import_session(path)`** — guards `chrome.running` + `session is None`
  (W-review MEDIUM-1: the runtime raises `RuntimeError` with no cwd; gate
  here so the user sees a friendly yellow degrade), calls
  `runtime_host.import_from_jsonl(path)`, surfaces the cancel branch, then
  repaints via the shared `_replay_after_swap` helper.

- **`_fork_session()`** — guards `chrome.running` + `session is None`, calls
  `session.get_entries()`, walks newest-first (`reversed(entries)`) for the
  first `type=="message"` entry with `message.role == "user"`, then calls
  `runtime_host.fork(target_id, position="before")`. No user message →
  yellow "No user message to fork before." degrade. Pi parity:
  `agent-session-runtime.ts:268-273` (the user-message walk that
  `runtime.fork(position="before")` requires).

- **`_clone_session()`** — guards `chrome.running` + `session is None`, calls
  `session.get_leaf_id()` (W-review LOW-2: public seam at `session.py:128`,
  not the storage-level indirection), then `runtime_host.fork(leaf_id,
  position="at")` so the new session keeps ALL ancestor entries
  (no truncation).

- **`_tree_action()`** — walks `meta.parent_session_path` recursively through
  the repo seam (`load_jsonl_session_metadata`), caps at 64 iterations +
  uses a `seen_paths` set to guard a circular `parent_session_path`
  (corrupted file). Each row renders as `{marker} {created} · {short_id}`
  + path; a broken ancestor breaks the chain at that row instead of
  crashing the REPL.

`_replay_after_swap(banner)` is a new shared helper that does
`clear → replay(build_context().messages) → commit(banner) → refresh_footer`,
used by all three swap-commands.

### Wire-up

`run_tui` extends the `CommandContext` constructor call with the four new
fields. The runtime's rebind seam (`_rebind`) already re-subscribes the
renderer + refreshes `command_ctx.harness` on every swap, so each command
benefits automatically.

## Deferred (intentional)

- **`_replay_after_swap` reuse by `_resume_session`** — the helper structurally
  matches the `/resume` repaint sequence (`clear → replay → commit → refresh`)
  but the old site inlines the message-count in its banner. A follow-up
  refactor can collapse both, but it's cosmetic — the duplication is exactly
  5 lines (W-review LOW-1).
- **Shell smoke tests for `/import`, `/clone`, `/tree`** — the
  most-logic-dense closure (`_fork_session`, with the user-message walk)
  gets two new shell-level smoke tests; the other three are thin glue and
  covered by the handler-level dispatch tests (W-review LOW-4 partially
  addressed).
- **`/fork` argument form** — pi accepts an optional entry-id argument; v1
  always picks the most recent user message. A follow-up can add an
  entry-id arg + a `/fork [picker]` UI.

## Consequences

- **Files touched**: 2 non-protected (`commands.py`, `shell.py`); 0 protected.
- **`git diff --stat docs/contracts packages/aelix-agent-core`**: empty ✓.
- **Tests**: 14 new in `tests/tui/test_commands.py` (unavailable / invokes /
  failure-surfaces / usage / help-listing per command) + 2 new in
  `tests/tui/test_run_tui_smoke.py` (fork picks the most recent user
  message + degrades gracefully when none exists). The registry-set
  assertion in `test_sprint_a_registry_set` is updated to include the
  4 new entries. Total +16 tests.
- **Gate**: ruff clean; pyright 0-new on touched files; pytest 437 → 439
  TUI tests passing; full suite green.
- **Pi-faithful**: each closure cites the pi reference inline; the runtime
  APIs (`import_from_jsonl`, `fork`) were already byte-faithfully ported
  in earlier sprints.

## Code review (separate lane) — APPROVE → all blockers + nits applied

`code-reviewer`: 0 CRITICAL / 0 HIGH / 1 MEDIUM / 4 LOW. Findings:

- **[MEDIUM-1]** `/import` from a no-session state surfaced a raw
  `RuntimeError("import_from_jsonl requires a cwd …")` instead of the
  friendly yellow degrade the other three closures use. FIXED:
  `_import_session` now early-returns with
  `"Import is unavailable (no session)."` when `runtime_host.session is None`.
- **[LOW-1]** `_replay_after_swap` is reused by /import + /fork + /clone but
  not by `_resume_session` / `_new_session` (the original sites). DEFERRED
  as a cosmetic refactor (the duplication is 5 lines, the helper has wider
  semantics: /new uses a banner, /resume needs the message count).
- **[LOW-2]** `_clone_session` reached `session.get_storage().get_leaf_id()`
  but `Session.get_leaf_id()` is the public seam. FIXED: use the public method.
- **[LOW-3]** Inline `from rich…` + `from aelix_agent_core.session…` imports
  inside `_tree_action`. FIXED: hoisted to module top.
- **[LOW-4]** No shell-level smoke test for the new closures. PARTIALLY
  ADDRESSED: 2 new tests for `_fork_session` (the logic-dense one — the
  newest-first user-message walk). The other three remain handler-level
  only (thin glue).

## Verification

- Unit tests (`tests/tui/test_commands.py`): 14 new tests cover every
  dispatch path — unavailable / invokes-wired / failure-surfaces /
  usage-hint / help-listing.
- Smoke tests (`tests/tui/test_run_tui_smoke.py`): 2 new tests drive the
  shell end-to-end through `/fork` and assert the closure picks the most
  recent user message (`u2` in `[u1, a1, u2, a2]`) + degrades when no
  user message exists.
- Pi-port fidelity: every closure cites the pi source line inline; the
  runtime APIs were already pi-byte-faithfully ported (ADR-0079, ADR-0083).
- Code review (separate lane): see above.
- Live verification: deferred. The closures are pure consumers of runtime
  APIs that have their own integration tests in `tests/runtime/`; the
  end-to-end happy path (real JSONL file → import → swap → repaint) is
  covered by `tests/runtime/test_new_session_real.py` and
  `tests/rpc/test_rpc_mode_switch_fork_clone.py` at the runtime layer,
  with the TUI wire-up covered by the smoke tests + unit tests above.
