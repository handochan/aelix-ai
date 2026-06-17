# 0134. Phase A — Bounded pi-parity Gap Closure (MCP/extension wiring, CLI flags, core stubs, deadline guard)

Status: Accepted
Date: 2026-06-17
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

The product-launch-readiness audit found a cluster of **bounded** gaps where
subsystems were built + unit-tested but unwired ("rails but no train"), plus a
few live stubs and a stale test. Phase A closes the bounded subset — the work
whose reference behaviour is fully pinned by pi (or, for Aelix-additive pieces,
by an existing shipped pattern) and that is verifiable end-to-end. The larger,
decision-bearing gaps (provider adapters, Web UI, server hardening, permission
posture) are explicitly out of Phase A.

Each item below was researched against pi at the pin before implementation; the
six are mutually low-coupling except for a shared `cli/entry.py` assembly pass.

## Decision

1. **MCP wired into the CLI (Tier 4).** `McpClientManager` is now instantiated
   in `cli/entry.py` `_async_main` (connect once → `collect_agent_tools` →
   shared across harness rebuilds via the `_harness_factory` closure → disposed
   once in `finally`). Added `McpServerContrib.args` (`contracts/manifest.py`)
   and consumed it in `mcp/client.py` (was hardcoded `args=[]`, breaking
   npx-style stdio servers). Added `cli/config.load_mcp_server_contribs` reading
   a Claude-Code-style `mcp.json` (`$AELIX_MCP_CONFIG` → `<cwd>/.aelix/mcp.json`
   → `<agent_dir>/mcp.json`), per-entry error containment. MCP is
   **Aelix-additive** (pi has no MCP; ADR-0094/0101) — no pi behaviour to match;
   the config shape mirrors Claude Code. Server-side (aelix-server) MCP wiring
   and manifest-declared `[[contributes.mcp_servers]]` aggregation remain
   deferred.

2. **On-disk / third-party extension discovery wired.** `entry.py` now calls
   `discover_and_load_extensions` (was the hardcoded
   `load_extensions([Guardrail, Permission])`). Added `prepend` (built-ins load
   FIRST) and `no_discovery` params to the loader. `--no-extensions` disables
   auto-discovery (project-local + global + entry_points) but keeps explicit
   `--extension` paths — Pi `noExtensions` (`resource-loader.ts:395-399`). An
   **Aelix-additive security warning** prints to stderr when any on-disk
   extension loads (full user privileges; pi only warns in docs). The deferred
   security review of the load path is still owed.

3. **CLI flag wiring.** `--tools` / `--no-tools` flow into
   `AgentHarnessOptions.active_tool_names` (Pi `main.ts:369-375`); `--offline`
   sets `PI_OFFLINE` and honours a pre-set `PI_OFFLINE` as input (Pi's `||`).
   `--no-builtin-tools` and `--skill` are **honestly descoped** (left parsed,
   not silently broken): faithful `--no-builtin-tools` needs post-extension-load
   tool knowledge the option-build seam lacks, and `--skill` needs the skill
   render pipeline.

4. **Core reload stub removed (PROTECTED).** `ReplacedSessionContext.reload`
   now `await self.reload()` instead of raising `NotImplementedError` — Pi
   parity (`ctx.reload === AgentSession.reload` via the runner's reloadHandler,
   `runner.ts:664-666`). One-line behavioural change reusing the existing,
   already-tested `AgentHarness.reload` (its two Phase-5b sub-steps —
   `ResourceLoader.reload` + full `_buildRuntime`, P-380 #3/#5 — remain
   deferred inside `reload()`).

5. **Branch-summarization real LLM impl (PROTECTED).**
   `session/branch_summarization.generate_branch_summary` now performs the real
   summarization via `aelix_ai.streaming.stream_simple`, reusing the proven
   `compaction._generate_summary` flow (ADR-0117). `BRANCH_SUMMARY_PREAMBLE` and
   `BRANCH_SUMMARY_PROMPT` are copied VERBATIM from pi
   (`branch-summarization.ts:165-198`); the replace-vs-append instruction logic,
   `<conversation>` wrapping, `SUMMARIZATION_SYSTEM_PROMPT` reuse, and
   preamble-prepend are exact. The `_summarizer_override` test seam is retained.
   Bounded divergences (documented in the module): returns `str` not pi's
   `{summary, readFiles, modifiedFiles}` (no file-ops tail), no
   `prepareBranchEntries` token-budget walk, no `max_tokens` cap (same
   `SimpleStreamOptions` infra gap as compaction). `opts.replace_instructions`
   is now threaded from the `navigate_tree` call site.

6. **Deadline time-bomb → closure-regression guard.**
   `tests/pi_parity/test_phase_3_1_strict_superset.py` replaced the wall-clock
   assertion (`today <= 2026-06-14`, which made the suite RED-by-default once
   past) with an assertion of the *condition* the deadline protected: ADR-0042
   + ADR-0044 are Accepted and the 3 events are absent from `DEFERRED_ALLOWLIST`.
   Sprint 5b shipped 2026-05-17, so the deadline guarded nothing real; the new
   guard stays green while still failing if 5b is reverted (anti-vacuous
   verified). The `OMC_SKIP_DEADLINE_GUARD` env branch is removed.

## Protected-core note

Items 4 + 5 touch `packages/aelix-agent-core/` (harness/core.py,
session/branch_summarization.py) and item 1 adds one additive field to
contracts/manifest.py. All are surgical: the reload change is one line + doc;
the branch-summary change is confined to one function (+ a 1-line kwarg thread
at the call site) and copies an already-reviewed pattern; the manifest field is
additive + backward compatible. No control flow elsewhere in core changed.

## Verification

- Full gate: **3133 passed, 1 skipped, 0 regressions** (baseline was 3115 passed
  + 1 *failed* deadline test; the deadline now passes, ~+18 net new tests across
  the 6 items). One pre-existing flaky signal test
  (`test_stop_escalates_to_sigkill_when_sigterm_ignored`) can fail under full-suite
  load but passes 3/3 in isolation and is untouched by this change.
- Anti-vacuous: flipping ADR-0042 → Draft makes the closure guard FAIL.
- Ruff clean. Contracts JSON-Schema regenerated (`McpServerContrib.args`) and a
  new **content-equality drift guard** (`generate_contracts_schemas.py --check`)
  added so future model/schema desync fails the gate (root-cause fix; previously
  only file existence was asserted).

## Review outcome

Independent 4-dimension review (correctness / pi-parity / protected-core /
test-adequacy) with adversarial verification. One REQUEST_CHANGES item — the
stale `docs/contracts/manifest.schema.json` after the additive `args` field —
fixed by regeneration + the drift guard above. Cheap parity/test nits applied
(empty `branch_summary` entry matches pi; `--offline` honours `PI_OFFLINE`
input; on-disk security-warning + branch-summary error/empty-path + MCP-args
tests added). Three findings dismissed on adversarial verify as misreads
(security-warning undercount is a benign degraded-mode under-count; aborted
stream is consistent with the shipped compaction pattern; the tool-gating path
IS exercised by the harness F-9 validator).

## Deferred follow-ups (tracked, not silently dropped)

- Provider adapters for the 7 un-adapted APIs (Phase B — separate worktree).
- `--no-builtin-tools` (extension-tool-aware gating) + `--skill` (skill render
  pipeline).
- P-380 #3/#5: `ResourceLoader.reload` + full `_buildRuntime` (then wire TUI/CLI
  `/reload` to `reload()` instead of `reload_resources()`).
- aelix-server MCP lifecycle wiring; manifest `[[contributes.mcp_servers]]`
  aggregation into the CLI.
- Branch summary `BranchSummaryResult` file-ops + token-budget walk + maxTokens
  cap (gated on a `SimpleStreamOptions.max_tokens` field).
- Security review of the on-disk extension load path.

## References

- ADR-0117 — compaction summarizer (the reused `_generate_summary` pattern).
- ADR-0094 / ADR-0101 — Aelix extension architecture + MCP (Tier 4).
- ADR-0096 — plugin manifest + JSON-Schema contract (regenerated here).
- ADR-0041 / ADR-0042 / ADR-0044 — the closure the deadline guard now asserts.
- pi @ `734e08e`: `harness/compaction/branch-summarization.ts`,
  `core/resource-loader.ts`, `cli/args.ts` + `main.ts` + `core/sdk.ts`,
  `extensions/runner.ts`.
