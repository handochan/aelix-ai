# ADR-0157 — Permission posture engine (shift+tab modes) + purpose-built approval dialog

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** WP-0 (TUI v2 overhaul roadmap)
- **Supersedes/relates:** ADR-0004 (`GuardrailExtension` — the non-bypassable regex floor),
  ADR-0120 (`PermissionExtension` Phase 1 — the 4-option gate this builds on), ADR-0149
  (Project Trust — a DISTINCT load-time gate, not conflated here), ADR-0105 (`AelixTUIContext`
  dialogs + `show_modal`), ADR-0153 (footer segments). Roadmap:
  `.omc/specs/tui-v2-overhaul-roadmap.md`. Companion: ADR-0158 (tree-sitter AUTO classifier).

## Context

The `PermissionExtension` (ADR-0120) gated the mutating tool set behind a 4-option dialog, but
it had no runtime posture: every mutating call always prompted (interactive) or always allowed
(headless). There was no Claude-Code-style mode control (auto-accept-edits / plan / yolo), the
extension was instantiated **anonymously** in `cli/entry.py`
(`prepend=[GuardrailExtension(), PermissionExtension()]`) so no held reference existed to mutate
posture at runtime or to survive `/resume` / `/new` / `/fork` harness rebuilds, and the prompt
reused the **generic** filterable `select()` — which showed a nonsensical "Type to search" hint
on a yes/no, truncated the command to 120 chars, and offered no diff preview.

## Decision

A small **pure posture engine** plus a **held-reference** threading fix plus a **purpose-built**
approval dialog, all confined to `packages/aelix-coding-agent` (`builtin/`, `cli/entry.py`,
`tui/`). Protected `packages/aelix-agent-core` and `packages/aelix-ai/src` are untouched.

1. **`builtin/permission_mode.py`** (pure, no prompt-toolkit) — `PermissionMode` str-enum
   (`default` / `auto-accept-edits` / `plan` / `yolo` / `auto`), a mutable `PermissionPosture`
   holder (`get` / `set` / `cycle`), `CYCLE_ORDER`, and `MODE_META` (distinct footer glyphs
   `✎`/`⏸`/`⚠`/`🤖` that never reuse steering's `⏵⏵`; DEFAULT shows no badge).

2. **`builtin/permission.py`** — a `posture` field (`default_factory=PermissionPosture` keeps
   zero-arg construction + existing tests green) and an `approval_runner` DI slot. `_on_tool_call`
   branches per posture in security-critical order: PLAN blocks ALL mutating tools **even
   headless** (the check is placed ABOVE the read-only short-circuit and the `not has_ui` ALLOW
   branch); read-only stays allowed under PLAN; YOLO skips the prompt for mutating; AUTO_ACCEPT
   auto-allows writes but still prompts bash; AUTO routes bash through the classifier
   (ADR-0158); DEFAULT prompts. The `asyncio.Lock`, re-check-inside-lock, and fail-safe
   deny-on-UI-error are retained.

3. **`cli/entry.py`** — build ONE `PermissionPosture` + ONE `PermissionExtension(posture=…)` in
   `_async_main`, thread the held instance into `_build_harness_options` (`permission_ext` param)
   and through `_harness_factory` (closure capture) so posture + `_session_allows` survive every
   rebuild, and pass `permission_ext` + `permission_posture` into `run_tui`.

4. **`tui/chrome.py`** — an `on_permission_cycle` slot fired by an `s-tab` binding (prompt-toolkit's
   name for the shift+tab / backtab CSI Z sequence; the literal `"backtab"` is NOT a valid pt key
   name and raises at binding time). `s-tab` is FREE (Tab is `c-i`).

5. **`tui/approval_dialog.py`** — `ApprovalRequest` / `ApprovalDecision` + a pure
   `build_approval_view` (bordered Rich Panel → ANSI; FULL untruncated bash command; write/edit
   diffs via `render._render_diff`, edit synthesized from `edits[].oldText/newText` with a verbatim
   fallback, never a file read) + a DI `run_approval_dialog` (4 static rows, ↑/↓ + Enter +
   digits 1-4 + `y`/`s`/`n`/`r` mnemonics + Esc/Ctrl+C = deny; NO type-to-filter, NO truncation).
   The generic `AelixTUIContext.select` is left untouched so `/settings` / `/resume` / `/model` /
   `/thinking` do not regress.

6. **`tui/context.py` / `tui/shell.py`** — a `permission_badge_provider` ctor param renders a
   SEPARATE footer segment (own glyph, omitted on DEFAULT) distinct from `⏵⏵ {steering}`;
   `shell.py` wires `_cycle_permission` (posture.cycle() + toast + footer repaint), the
   `approval_runner`, and the optional `/permissions` slash command.

## Security decisions

- **YOLO bypasses the prompt, NOT the Guardrail floor.** `GuardrailExtension` runs FIRST via the
  prepend order (first-block-wins), structurally independent of posture, so `rm -rf` / fork-bomb /
  `.env`|`.git` writes STILL hard-deny in YOLO. Pinned with a code comment + regression tests. No
  `--yolo-no-guardrail` escape hatch.
- **PLAN denies mutations even headless** — the check precedes the `not has_ui` ALLOW branch, so the
  guarantee holds on print/json/rpc. PLAN keeps read-only allowed (investigation).
- **Default posture = DEFAULT** (never auto-accept/yolo). No boot-into-yolo CLI flag (deferred).
  Headless ALLOW for DEFAULT preserved (no regression); shift+tab cannot fire without a chrome.
- **Fail-safe everywhere:** UI-error → deny; Esc/Ctrl+C/unknown decision → deny; classifier
  error/has_error/import failure → ASK; off-cycle posture → DEFAULT. Never silent-allow.
- **Hold-the-ref is a security requirement** — a fresh per-rebuild extension would reset posture +
  lose `_session_allows` mid-session.
- **Naming disambiguation** — the permission concept never uses the bare word `mode` in symbols
  (uses `PermissionMode`/`PermissionPosture`/`posture`) or a footer glyph colliding with `⏵⏵`.

## Consequences

shift+tab cycling, a distinct posture footer badge, and a purpose-built approval dialog ship; the
headless/RPC path is no more permissive than before (PLAN is strictly stricter). All edits are pure
TUI/CLI consumers + the one builtin gate; protected core is untouched. Tests: posture engine,
per-mode gate matrix (incl. headless), YOLO-still-guarded regression, hold-the-ref preservation,
the approval-dialog builders + DI runner, and the s-tab binding.

## Note (2026-07-26) — the posture ladder gains a SECOND consumer

**Added by:** ADR-0197 (subagent-runtime seam & the bundled `aelix-agents`
extension, P2). Nothing above is changed; this note records a new consumer of the
posture engine and one property of it that must not be misread.

Until P2 the ladder had exactly one consumer: the human at this TUI, cycling with
shift+tab. P2 adds a second — **a child process whose posture is COMPUTED by the
parent** (`aelix_agents/posture.py::child_permission_mode`) and handed to it as
`--permission-mode <value>` at spawn time. The rule is a clamp over the same
total order this ADR established (`plan < default < auto-accept-edits < auto <
yolo`): a child is never given a mode looser than the parent's, and a `default`
result is tightened to `plan`, because "DEFAULT" in a child means *prompt* and a
child has no prompt.

Three consequences worth stating here rather than only in ADR-0197:

1. **A widened per-spawn grant is NOT a posture change on the parent.** The
   spawn-time consent dialog (ADR-0197 §(i)) may raise one child's posture to at
   most `auto-accept-edits`; the parent's own `PermissionPosture` object is never
   mutated by it, the footer badge never changes, and nothing is persisted. The
   ceiling is deliberate: `auto` and `yolo` are never offered by any spawn dialog,
   so **bash in a child is still gated by the child's own ladder.**
2. **This ADR's "Headless ALLOW for DEFAULT preserved (no regression)" still
   holds for every existing user.** P2 adds a `headless_default` field to
   `PermissionExtension` that flips the `not ctx.has_ui` branch to
   block-with-reason — but *only* when `subagent_depth() > 0`, i.e. only inside a
   delegated child. Flipping it for all headless runs would be a behaviour change
   for every `-p` user and needs its own ADR.
3. **That floor is NOT the guarantee, and the code comment says so.** The
   AUTO_ACCEPT write short-circuit at `builtin/permission.py:348-353` returns
   ~30 lines *above* the `not ctx.has_ui` branch at `:382-383`, so a child running
   under AUTO_ACCEPT never reaches the floor. The guarantee is the clamped posture
   the child is launched with. A regression test in P2 documents this ordering
   explicitly so the floor is never again mistaken for the boundary.

One security-relevant change lands inside this ADR's own gate: **`.aelix` joins
`_SENSITIVE_DIR_COMPONENTS`** (`permission.py:219`). Measured before the change,
`_is_auto_allowable_write` returned `True` for `.aelix/agents/*.md`,
`.aelix/extensions/*.py`, `.aelix/mcp.json` and `.aelix/settings.json` — the exact
files a later run executes. Under AUTO_ACCEPT and AUTO those writes now fall
through to this ADR's 4-option prompt instead of being silently accepted. It is a
user-visible behaviour change for interactive AUTO_ACCEPT users (CHANGELOG'd), and
it is a hard prerequisite for allowing any spawn-time widening at all.
