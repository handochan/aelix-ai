# 0120. Tool-Call Permission / Approval System — Phase 1 (built-in extension)

Status: Accepted (W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Reference: `@gotgenes/pi-permission-system` (most-used pi guardrail extension, pi.dev/packages)

## Context

The product vision (`docs/01-product-vision.md`, ADR-0004) mandates permission +
guardrails as a **built-in extension**, not a core gate. Until now mutating tools
(write/edit/bash) ran with ZERO confirmation. pi has no built-in policy either —
only the `beforeToolCall → {block, reason}` mechanism; the most-used community
extension `@gotgenes/pi-permission-system` (allow/deny/ask + a 4-option dialog +
ephemeral session rules) is the reference.

The infrastructure already existed in Aelix (architect-verified): the `tool_call`
hook returns `ToolCallResult(block, reason)` (first-block-wins reducer), handlers
may be `async` and are awaited, the loop synthesizes an `isError` tool result from
a block, and `AgentHarnessOptions.extensions` registers handlers. The ONLY gap was
wiring: `cli/entry.py` built the harness with no `extensions=`, so nothing loaded.

## The decisions (Phase 1 — all in non-protected `aelix-coding-agent`)

- **Built-in `PermissionExtension`** (`builtin/permission.py`, modeled on the
  existing `PolicyExtension`): subscribes to `tool_call`; gates only the mutating
  set `_BASH_TOOLS | _WRITE_TOOLS`; read-only tools → silent allow. On a gated
  call with no matching session rule it prompts via `ctx.ui.select` with the pi
  4-option dialog — `Yes` / `Yes, for this session` / `No` / `No, provide reason`
  (reason collected via `ctx.ui.input`). `Yes` = allow once; session-approve stores
  an ephemeral fnmatch **wildcard** rule (`git status --short`→`git status *`;
  `src/.env`→`src/*`); `No`/Esc/reason → `ToolCallResult(block=True, reason=…)`
  which the loop turns into an error result the model adapts to. Prompts are
  serialized with an `asyncio.Lock` (parallel-tool safety); rules cleared on
  `session_shutdown`.
- **Wiring** (`cli/entry.py`): `_build_harness_options` is now `async` and builds
  `extensions` via `load_extensions([GuardrailExtension(), PermissionExtension()])`
  (Guardrail FIRST so hard-deny patterns like `rm -rf` short-circuit via
  first-block-wins BEFORE any prompt), passing `extensions=` + `runtime=` so the
  shared runtime is the one `run_tui` binds the live UI onto (`bind_ui`).
- **Mid-turn modal feasibility — VERIFIED.** The riskiest unknown (can a modal be
  shown + awaited while a turn runs?) is confirmed: `chrome.run()` is a concurrent
  task that keeps painting/dispatching while the turn coroutine awaits the tool;
  `show_modal` resolves a future via the app task. No deadlock.
- **Headless default = allow** (user decision): when `not ctx.has_ui` (print/RPC),
  mutating tools pass (preserve non-interactive behavior); GuardrailExtension still
  hard-blocks dangerous patterns. A policy-file denylist is a Phase 2 follow-up.

## Consequences

- **Live-verified** (PTY, qwen3.6): a `write` mid-turn popped `Allow write?
  /tmp/… 1.Yes 2.Yes,for this session 3.No 4.No,provide reason`; `1` → file
  created; `3` → file NOT created + "Denied by the user." surfaced to the model,
  which adapted ("your permission settings have prevented me … grant permission").
- Vision/ADR-0004 compliant (policy as a built-in extension, not core). Zero
  protected-core changes.
- **Deferred** (Phase 2/3): persistent policy config file; a richer approval panel
  (show the diff/command); tree-sitter bash-arity; MCP/skills gating; tool-hiding
  at `before_agent_start`; an `AgentTool.mutates` capability flag (would be a
  protected-core change). Phase-1 gates by tool name (matches the guardrail convention).

## Verification

- ruff clean; pyright 0 errors on `permission.py` + `entry.py` (8-baseline overall);
  full pytest 2970 pass / 1 skip (+15 permission tests: non-mutating allow,
  headless allow, Yes / Yes-for-session no-reprompt / No / reason / Esc, wildcard
  synthesis, session_shutdown clear); protected paths byte-unchanged.
- Live PTY: approve → tool runs; deny → tool blocked + model adapts.
