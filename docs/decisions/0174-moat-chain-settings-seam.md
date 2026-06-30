# ADR-0174 — moat chain: settings_manager harness seam (#44) + recon-grounded sequencing

- **Status:** Accepted — #44 LIVE (dormant enabler); moat-chain sequencing recorded for #5/#24/#53/#21.
- **Date:** 2026-06-30
- **Sprint:** Moat chain (self-extensibility) — Step 1. Importance order: floor(reliability) → moat(self-extensibility) → breadth. Floor + the big breadth item (#15) are done; this opens the moat.
- **Pi pin:** `earendil-works/pi@734e08e`.
- **Relates:** ADR-0091 (SettingsManager port), ADR-0093/0087 P-380 (reload primitives — 2 steps deferred), ADR-0160/0161 (settings seam deliberately left unwired — note now **corrected**, see below), ADR-0171 (#5 Project-Trust bootstrap deferral). GitHub: #44 (this), and the chain #5 / #24 / #53 / #21 / #36 / #4.

## Context

The moat (aelix's differentiator: "ask the agent to build its own extension → it works without restart") is gated by `harness.reload()` re-discovering extensions in-session. A 7-agent read-only recon (pi @734e08e cross-checked) established the ground truth and **corrected three stale assumptions**:

1. **The #44 protected-core seam already exists.** `AgentHarnessOptions.settings_manager` (core.py:257), `__init__` threading (:545), the `settings_manager` property (:965), and the `reload()` consumer + `invalid_state` guard (:2857) all shipped in commit `4659a99`. ADR-0160's note that "wiring it would require editing the protected factory" is **stale/false** — the seam pre-existed; only the coding-agent glue was missing. So `_build_harness_options` built `AgentHarnessOptions(...)` **without** `settings_manager=`, leaving `harness.settings_manager` None in every production mode and `harness.reload()` permanently raising `invalid_state`.
2. **#24 hard-depends on #44, but #5 does NOT.** Every production harness has `settings_manager is None` → `reload()` is dead → #24 genuinely needs #44 first. But #5's Project-Trust bootstrap reaches the SettingsManager at the CLI call site (`_async_main`, where the instance is already in scope before `_resolve_project_trust`), so #5's real prerequisite is the **unbuilt `get_default_project_trust` getter**, not #44.
3. **#21 has no pi parity.** pi extensions register everything imperatively via `ExtensionAPI`; pi has **zero** declarative `contributes.*` / `on_*` activation. aelix's `Contributes`/`Activation` manifest layer is aelix-original (VS-Code-style) with no upstream oracle → not on the moat critical path.

## Decision

**Step 1 — #44 (this commit): thread the startup `SettingsManager` into the harness factory.** Pure additive coding-agent glue, no protected-core edit:
- `if TYPE_CHECKING:` import `from aelix_ai.settings import SettingsManager` (entry.py has `from __future__ import annotations`, so the new annotation is a lazy string — zero runtime cost, no circular import).
- `_build_harness_options(...)` gains a keyword-only `settings_manager: SettingsManager | None = None` (default preserves every existing/spy caller).
- The `AgentHarnessOptions(...)` constructor and the `_harness_factory` closure both forward it. The one `SettingsManager` built in `_async_main` (shared with the TUI per ADR-0160) is threaded into **every** rebuild via the same hold-the-ref pattern as `permission_ext`/`model_registry`, so `harness.settings_manager` survives `/new`, `/fork`, `/resume`.

**Dormant by design:** no production path calls `harness.reload()` yet (TUI/CLI `/reload` → `reload_resources()`), so this changes **no observable behavior** — it only makes the reload guard reachable, unblocking #24. Settings-driven `compaction.reserveTokens` / retry reads (core.py:483-492 module constants) are deliberately **left hardcoded** here; activating them is a separate gated step (a behavior change must not ride inside a pure threading PR).

**Sequencing (owner-approved this session):**
- `#44 → #36 → #4 → #5 → #24 → #53 Track A`. #36 (gemini /login) and #4 (overflow follow-ups) fold in as quick wins, each its own verified commit; #4 shares core.py with #24 so it stays a **separate** PR from the reload work.
- **#24 route = the existing P-302 `_harness_factory` rebuild**, not an in-place pi `_buildRuntime` port — `discover_and_load_extensions` already re-runs on that path, minimizing protected-core surgery; only `_previous_flag_values` round-trip is added.
- **#21 deferred** and explicitly marked aelix-original; revisited only after the imperative-path moat (#53 Track A) is proven.

## pi parity

The seam is pi-faithful: pi's `AgentSessionConfig.settingsManager` is a **required** field consumed by `_buildRuntime`/`reload`/system-prompt rebuild; pi `main.ts` constructs the AgentSession with its `settingsManager`. #44 restores exactly that wiring across aelix's agent-core / coding-agent package split (the `_build_harness_options`/`_harness_factory` indirection is aelix-original but semantically equivalent). The pre-existing core seam is the verbatim port of pi `agent-session.ts:reload` (cited in core.py:2804+).

## Verification

`python3 -m pytest tests/cli/test_agent_context.py tests/harness/test_harness_reload.py tests/cli/test_project_trust.py tests/cli/test_api_key_auth.py -q` → **79 passed**; `ruff check` clean; `.venv/bin/pyright` on the changed files → **0 errors**. Two additive tests assert (a) a passed `SettingsManager` reaches `opts.settings_manager`, (b) the default stays None. Closure scope verified: `settings_manager` (built in `_async_main`) and the nested `_harness_factory` share one enclosing scope with no intervening top-level def. Adversarial code-review (separate context, 21 tool calls incl. `reload()`-caller search to confirm dormancy) → **APPROVE, no findings**.

## Follow-ups (the chain)

- **#36** gemini `/login` auto-register (next) · **#4** overflow follow-ups (separate PR).
- **#5** port `get/set_default_project_trust` + bootstrap reorder (the real #5 prereq; not blocked by #44).
- **#24** reload round-trip via factory-rebuild (the keystone; consumes this seam) → **#53 Track A** imperative hot-reload flagship.
- **#21** declarative activation engine — deferred, aelix-original.
- rpc_ws.py harness factory also omits `settings_manager` (constructs none) — noted parity follow-up, out of #44 scope.
