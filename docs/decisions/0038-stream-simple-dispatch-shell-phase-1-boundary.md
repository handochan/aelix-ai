# 0038. `stream_simple` Dispatch Shell — Phase 1 Boundary

Status: Accepted (Sprint 2.5 / Phase 1.4 shipped — body lands Phase 4)

## Context

Phase 1 (re-evaluation §5) defines parity for `aelix-ai` as: full message
types, content blocks, Model/Cost/Context, AssistantMessageEvent union,
Tool/ToolResult, and a working `stream_simple`. Through Phase 1.3 the
`stream_simple` implementation was a `NotImplementedError` stub
(`packages/aelix-ai/src/aelix_ai/streaming.py:110-128`). The open question
the re-eval surfaced: **does Phase 1 exit when the shell ships, or only when
adapters ship?**

## Decision

**Phase 1 exits at the shell.** Adapters are Phase 4 scope.

This mirrors ADR-0025's "minimal shell + owning-ADR" pattern:

- ADR-0025: `_TurnState` ships 2 fields; remaining 7 land via owning ADRs
  (0017 / 0022).
- ADR-0038: `stream_simple` ships dispatch + registry + typed error;
  provider bodies land via owning ADR-0020-adjacent provider work.

### What Phase 1.4 ships

1. `aelix_ai.api_registry` module with `register_provider`,
   `unregister_provider`, `get_registered_providers`, `clear_providers`,
   and internal `_resolve_provider`.
2. `aelix_ai.streaming.StreamSimpleError` — typed exception with `code:
   Literal["no_provider_registered"]`.
3. `aelix_ai.streaming.stream_simple` becomes the dispatch shell:
   resolve the api → delegate to the registered `StreamFn` → yield events.
4. Umbrella `aelix` re-exports the four registry symbols + `StreamSimpleError`.
5. 6 tests covering: no-provider raise, route, unregister, snapshot copy,
   overwrite-on-reregister, and clear.

### What Phase 4 ships (out of scope here)

- `aelix_ai.providers.anthropic`, `aelix_ai.providers.openai`,
  `aelix_ai.providers.openrouter` adapter modules.
- `register_all()` helper for CLI bootstrap.
- OAuth / `.env` integration for provider auth.
- Provider-level integration tests (under `tests/integration/`).

## Justification

1. **Pi parity for the `aelix-ai` API surface is achievable without
   adapters.** Pi's `stream.ts` is itself 6 lines (`stream.ts:45-50`) — the
   *dispatch* is what makes `streamSimple` callable; the *adapters* are
   independent modules. Aelix matches the dispatch line-for-line today.
2. **Provider work is Phase 4 scope** per re-eval §5. Bundling adapters into
   Phase 1 collapses two phases with very different risk profiles (type-level
   vs network/auth/streaming) into one.
3. **Testability** — the shell is fully testable with mock `StreamFn`
   injections. Adapters require credentialed integration tests, a Phase 4
   concern.
4. **Compounding parity** — Phase 1.4's shell unblocks Phase 2.x harness
   work that may want to call `stream_simple` indirectly (e.g.
   `before_provider_request` event). Without the shell, every Phase 2.x test
   that touches that codepath would need ad-hoc patching.
5. **Reversible** — if a Phase 4 provider design forces a `stream_simple`
   signature change, the shell is 30 lines of code to revise; no caller code
   today depends on adapter-emitted events.

## Alternatives considered

- **Ship adapters now (collapse Phase 1 + Phase 4):** rejected — 3-4 weeks
  of work, requires OAuth/HTTP/streaming machinery that has no other Phase 1
  customer.
- **Keep `NotImplementedError`:** rejected — third-party adapter packages
  (Phase 4) need the registry API surface stable before they can be authored;
  landing the registry now lets Phase 4 ship a single PR per adapter rather
  than co-evolving registry + adapter.
- **Use a global `STREAM_SIMPLE_FN` module variable (no registry):**
  rejected — Pi's registry pattern supports multiple APIs cleanly
  (`anthropic-messages`, `openai-chat-completions`, `openai-responses`, etc.);
  the module-variable shortcut would force a redesign at Phase 4.

## Consequences

- Phase 1 has a clean exit gate: shell + tests + ADRs.
- Phase 4 PRs each register a single api; no shared-module ownership
  conflicts.
- A user calling `stream_simple` today gets a typed, actionable error
  message — not a Python `NotImplementedError`.
- Third-party adapter packages can begin authoring against the stable
  registry surface immediately, before Aelix's first-party adapters land.
- Phase 4 will add `unregister_providers_by_source(source_id)` to match
  Pi `unregisterApiProviders(sourceId)`. The current `unregister_provider(api)`
  is a Phase 1.4 single-key convenience; the Pi-parity batch-by-source
  delete is owned by the Phase 4 provider-pack ADR.
- `stream_simple` raises `StreamSimpleError` eagerly at call-time (matches
  Pi `stream.ts:42-46`), not lazily at first `__anext__`.

## Related

- ADR-0017 — Full Hook Event Catalogue v2 (Phase 2.x may consume
  `stream_simple` via `before_provider_request`).
- ADR-0020 — RPC Mode (Phase 4 lands alongside the provider work).
- ADR-0025 — Minimal-shell pattern (this ADR is a direct sibling).
- ADR-0037 — Streaming event union expansion (Phase 4 adapters emit the
  expanded union).

## Phase

Sprint 2.5 / Phase 1.4 (shell shipped; provider adapters Phase 4).
