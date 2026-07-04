# ADR-0183 — #62: custom-message renderer via a display-tier context split

- **Status:** Accepted — **LIVE**.
- **Date:** 2026-07-04
- **Sprint:** #20/#21 follow-up — the renderer half descoped from W1 with evidence (ADR-0181).
- **Pi pin:** `earendil-works/pi@734e08e`. Pi-faithful in observable behavior; the MECHANISM diverges deliberately (see Decision).
- **Relates:** ADR-0181 (W1 evidence: `register_message_renderer` had zero consumers; `build_context()` discarded `custom_type`), ADR-0022 (the original eager-flatten divergence this refines), ADR-0182 (the `get_shortcuts`/live-read idioms reused). GitHub #62 (closes), #20 (renderer half).

## Context — premise correction

Issue #62 prescribed an **entry-stream replay refactor** ("iterate raw entries, updating the replay callsites"), premised on recon that pi had nothing better. Fresh pi recon at the pin corrected that: pi's `buildSessionContext` re-materializes every `custom_message` entry as a **first-class `role:"custom"` message** (`createCustomMessage`, messages.ts:123-138 — customType/display/details carried verbatim), renders its TUI from that rich built context (`renderSessionContext` → `addMessageToChat` `case "custom"`, interactive-mode.ts:3123/3029-3037), and flattens **only at the LLM boundary** via `convertToLlm` (messages.ts:148-195: `custom` → `role:"user"` with content passed through). Aelix's `create_custom_message` had fused the two tiers — eager-flatten to `UserMessage` at build time (ADR-0022 divergence) — which is byte-identical to pi's LLM-tier output but starves the TUI of `custom_type`. The renderer contract also came back concrete: `MessageRenderer = (message: CustomMessage, options: {expanded}, theme) => Component | undefined`; lookup first-extension-wins (`getMessageRenderer`, runner.ts:502-510, no collision warning); `display=false` short-circuits BEFORE lookup; renderer exceptions and `undefined` fall through **silently** to a default `[customType]`-labeled rendering (custom-message.ts:58-97).

## Decision

**Two-tier split with the LLM tier untouched.** A consumer sweep showed every existing `build_context().messages` consumer (turn-start state rebuild, compaction summarizer, runtime session swap, print-mode) is LLM-tier; only the TUI replay wants rich messages. So instead of pi's rich-context + convert-at-agent-boundary (which would push a new `role:"custom"` through every `_state.messages` heuristic, token estimation, and provider adapter), aelix keeps `build_session_context` **byte-identical** and adds a parallel display derivation — observably equivalent to pi on both tiers, mechanism documented here:

- **`session/context.py`:** `CustomMessage` (frozen, `role="custom"`, pi messages.ts:46-53 shape) + `create_display_custom_message` (pi `createCustomMessage` faithful) + `select_display_entries` — the compaction-boundary survivor selection EXTRACTED from `build_session_context` (chosen compaction leads as the summary marker; non-chosen compaction entries filtered; behavior-identical refactor) — + `build_display_messages` = same boundary, same summary wrapping, but `custom_message` stays rich. Display output must never feed the LLM pipeline.
- **`ExtensionRunner.get_message_renderer(custom_type)`** (agent-core): pi runner.ts:502-510 mirror — first-wins in load order, read live per call, duck-typed, **no collision warning** (pi has none; contrast `get_shortcuts`).
- **`EventRenderer`:** `replay` gains the `role == "custom"` branch — the `display` gate fires BEFORE any lookup; `_render_custom` tries the late-bound `render_custom_message` hook (the `get_tool_renderer_desc` idiom) and on `None`/raise falls through **silently** (pi custom-message.ts:68-70) to the default: bold `[custom_type]` label + plain content text. **Divergence:** pi draws a themed box + markdown and re-invokes the renderer on expand-toggle; aelix scrollback is plain-text-first and static-per-replay (the thinking-toggle divergence class, ADR-0123).
- **`shell.py`:** `_render_custom_message` closure (live runner lookup → `renderer(msg, MessageRenderOptions(expanded=ctx.get_tools_expanded()), ctx.theme)` → `Component.render(80)` → `Text.from_ansi`; never-raises) wired onto the renderer; `_display_messages(session)` feeds both replay callsites from `get_branch()` + `build_display_messages`, **degrading** to the old `build_context().messages` path when a session (test fakes, alt backends) lacks `get_branch`.
- **`extensions/api.py`:** `MessageRenderOptions` dataclass; `MessageRenderer` docstring now specifies the pi call convention (`(message, options, theme) → Component | None`) — the de-facto public extension contract.

**Behavior changes (intended, pi-correct):** a `display=False` custom message no longer renders in replay (it used to leak as a `»` user echo); a `display=True` one renders under its `[custom_type]` label (or its extension renderer) instead of masquerading as user input. Live emission stays out of scope — pi has no `custom_message` push site (`PendingCustomMessageWrite` note, harness/core.py) and no `AgentEvent` variant exists; pi's live `message_start role:"custom"` render path is consumer-side only.

## Consequences

- `register_message_renderer` has its first consumer — the extension surface from Sprint 5a is now end-to-end: register → `/resume`·`/fork`·`/clone`·`/import` replay dispatches by `custom_type`.
- The renderer is consulted ONLY in the interactive TUI (pi parity: RPC/print/export never consult messageRenderers).
- No unregister/replace API, no renderer error surfacing, no collision diagnostics — all pi-parity absences, documented not invented.
- `select_display_entries` is the single source for the compaction boundary; the themes/descriptors follow-ups and any future export path can reuse it.
- **Gate:** pytest 4610 pass / 1 skip (+16 vs the post-#21-W2 4594 baseline) · ruff clean · pyright 8 pre-existing `scripts/pyright_spike.py` errors only.

## Adversarial review

A 4-lens adversarial review workflow (correctness / pi-parity / consistency / test-adequacy) was launched but its subagent fleet was exhausted by session/model rate limits twice before returning findings; the review was completed **inline** instead. Verified:

- **Refactor equivalence (correctness).** `select_display_entries` is proven behavior-identical to the pre-#62 `build_session_context` boundary by a **golden test** (`test_build_session_context_byte_identical_to_pre_refactor`) that reimplements the old algorithm as a reference and compares message text + types across five scenarios: no-compaction, single compaction, TWO compactions (earlier dropped), `first_kept_entry_id` matching nothing, and compaction-first. The only mechanical change — filtering `type != "compaction"` in the extraction vs relying on the old `_append_message` silently dropping compaction entries — is equivalent (both drop them).
- **pi-parity.** Signature (`(message, options, theme) → Component | None`), first-wins lookup with no collision warning (`runner.ts:502-510`), `display` gate before lookup (`interactive-mode.ts:3029-3037`), and silent fallback on `None`/raise (`custom-message.ts:58-97`) all match the pinned pi sources. `get_message_renderer`'s `is not None` vs pi's truthy `if (renderer)` is a non-issue (renderers are callables ⇒ always truthy; `is not None` is the correct miss-check for `dict.get`).
- **Consistency.** Silent-swallow (pi) vs the "every skip/failure logs" convention (ADR-0181) resolved: the FALLBACK stays silent (a bad renderer must not break replay) but the exception is recorded at DEBUG for plugin-dev diagnosis (no per-message warning spam). Package boundaries hold: `get_message_renderer` is duck-typed (no coding-agent import into agent-core); `CustomMessage` lives in agent-core `session/context.py` and shell.py's cross-package import follows the existing `build_session_context` precedent.
- **Test adequacy.** Added the golden equivalence test + a non-Component/raise fallback test; the degrade path (a session without `get_branch`) is already exercised by the pre-existing `/resume` smoke tests (their `_ResumeSession` fake exposes only `build_context`).

No CONFIRMED defects required code changes beyond the DEBUG-log consistency fix and the two added tests.
