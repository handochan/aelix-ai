# ADR-0178 — #5 Project-Trust bootstrap: defaultProjectTrust setting + extension-vote wiring

- **Status:** Accepted — LIVE.
- **Date:** 2026-06-30
- **Sprint:** Moat completion — the remaining "extension dev usable" enabler (DEFERRED in Batch 1 / ADR-0171). Wires the trust safety story for the now-live self-extension loop (#53 Track A / ADR-0177).
- **Pi pin:** `earendil-works/pi@734e08e` (now PRIVATE — ground truth = in-repo faithful ports + ADR citations). Ported from pi `settings-manager.ts:884-892` (getDefaultProjectTrust / setDefaultProjectTrust) + `main.ts:637-643` (resolveProjectTrust call).
- **Relates:** ADR-0171 (#5 bootstrap deferral + the orchestrator landing), ADR-0174 (#44 settings seam — corrected "#5 not hard-blocked by #44"). GitHub #5 (parent #3).

## Context

The Project-Trust orchestrator `resolve_project_trusted` (cli/project_trust.py) was already built + tested: its `project_trust` extension-vote event (step 3) and `defaultProjectTrust` branch (step 5) fire WHEN the caller threads `extensions=` / `default_project_trust=`. But the production bootstrap caller `_resolve_project_trust` (entry.py) omitted both, so in the shipped CLI the event never fired and the default was always `"ask"`. Two gaps: (a) aelix had **no** `defaultProjectTrust` setting at all; (b) the bootstrap didn't load the vote surface or read the default.

## Decision

**1. Settings port** (aelix-ai, pi-pinned, protected). Both edits are mandatory together — the serializer is map-driven (`_settings_to_json_dict` iterates `SETTINGS_PY_TO_JSON`; the read path iterates the auto-derived inverse), so a field without a map entry silently never persists:
- `types.py`: `DefaultProjectTrust = Literal["ask","always","never"]`; `Settings.default_project_trust: DefaultProjectTrust | None` (top-level, default None); `SETTINGS_PY_TO_JSON["default_project_trust"]="defaultProjectTrust"`; `__all__`.
- `settings_manager.py`: `get_default_project_trust()` (default `"ask"`) + `set_default_project_trust()` (global-scope write).

**🔒 SECURITY — global-scope-only read (the load-bearing decision).** `get_default_project_trust()` reads `self._global_settings`, **NOT** the merged `self._settings` that every other getter uses. aelix loads a project's `.aelix/settings.json` ungated (project can override global via `deep_merge_settings`); a merged read would let an untrusted project ship `{"defaultProjectTrust":"always"}` and **self-elevate to trusted**, defeating the entire gate. pi makes this a global-only setting for exactly this reason. Locked by a test asserting a project-scope `defaultProjectTrust:"always"` does NOT change the getter (still `"ask"`) while `get_project_settings()` confirms the value WAS loaded.

**2. Bootstrap reorder** (coding-agent glue, entry.py). Before `_resolve_project_trust`, load the **user/global-only** vote surface via `discover_and_load_extensions(..., no_project_local=True)` with **no `prepend` built-ins**, then thread `extensions=` + `default_project_trust=settings_manager.get_default_project_trust()` + an `on_extension_error` stderr sink into `resolve_project_trusted`.

**🔒 SECURITY — the vote-load is user/global ONLY** (`no_project_local=True`): an untrusted `cwd/.aelix/extensions/*.py` is NEVER `exec_module`'d before the gate. Built-ins are excluded (Guardrail/permission have no `project_trust` handler and the held-ref `permission_ext` must be instantiated exactly once, by the factory).

**De-dup = OPTION B (throwaway vote runtime)** — chosen over OPTION A (reuse the vote list in the factory's first build):
- The vote-load's fresh `_ExtensionRuntime` is bound to nothing, so a vote extension's `register_provider` only **queues** onto a discarded runtime and is never **applied** (only the factory's runtime gets `bind_model_registry`). No double-registration.
- Cost: user/global `setup()` side-effects run twice (vote-load + factory). Documented; OPTION A (pi's "load once") is a deferred efficiency refinement — it would collide with the `_build_harness_options`/`_harness_factory` region (kept solely owned by the #24 follow-ups, avoiding entry.py contention).

## pi parity

`get/set_default_project_trust` mirror pi `settings-manager.ts:884-892` (default "ask", global-only setter). The bootstrap mirrors pi `main.ts:637-643` (resolveProjectTrust fed both `defaultProjectTrust` and `extensionsResult`). The `DefaultProjectTrust` Literal already existed verbatim in aelix at `project_trust.py:87`; the new `aelix_ai.settings.DefaultProjectTrust` is a structurally-identical sibling (no cross-package coupling — aelix-ai must not import coding-agent). aelix-original deviation: the throwaway vote-load (pi loads extensionsResult once) — the de-dup OPTION B documented above.

## Adversarial review + fix applied

Two parallel reviewers (security-lens trust boundary + correctness/regression lens):

- **Security: SOUND — LOW risk, 0 CRITICAL/HIGH/MED.** All six attack vectors defended with evidence: self-elevation via project `settings.json` defeated (getter reads `_global_settings` only; no other reader reaches the trust decision); the vote-load is provably user/global-only (`no_project_local=True` skips tier-1 `exec_module`; `settings.json.extensions` is not wired to the loader; `--no-extensions` honored); the gate still precedes all project-local MCP + extension execution; a vote handler can only yes/no-vote the current cwd. 4 LOW defense-in-depth notes only.
- **Correctness: COMMENT — 0 CRITICAL/HIGH;** map-driven persistence/backward-compat/throwaway-register_provider/types all CLEAN. Two MED perf/lifecycle findings on the startup hot path; **MED #1 applied:**
  - **MED #1 (applied):** the vote-load ran on EVERY startup, but its votes are consulted only when `override is None AND has_trust_requiring_project_resources(cwd)` (step 3) — so the common case (no `.aelix/extensions`/`.aelix/mcp.json`) paid for a full user/global extension load + a second `setup()` that was then discarded. Now gated on that exact predicate (behavior-identical; the orchestrator's own step-2 check). This removes the double-load from the common path AND shrinks the MED #2 surface to the rare resources-present case.
  - **MED #2 (documented residual):** when the vote-load DOES run, its throwaway `Extension` instances are never disposed (no per-extension teardown lifecycle), so a `setup()` that acquires a resource holds it through the subsequent real factory `setup()`. Acceptable for v1 (the MED #1 gate makes this rare); the full fix is de-dup OPTION A (load once, reuse) — a deferred follow-up.

## Verification

`python3 -m pytest tests/ -q` → **4532 passed / 0 failed / 1 skipped** (+4); `ruff check` clean; authoritative whole-project `.venv/bin/pyright` → only the 8 pre-existing intentional `scripts/pyright_spike.py` errors (**0 new**). New tests: settings default `"ask"`, set persists to GLOBAL scope only, **the global-scope-only security test**, and a bootstrap spy asserting `_resolve_project_trust` threads `extensions=`/`default_project_trust=`/`on_extension_error=`. The orchestrator's event/default behavior remains covered by `test_extension_issue5_runtime_and_trust.py`; the vote-load gate reuses the already-tested `has_trust_requiring_project_resources` predicate.

## Follow-ups

- De-dup OPTION A (reuse the vote-loaded user/global set in the factory's first build → load once, dispose-free, fixes MED #2) — deferred (avoids #24-follow-up entry.py contention).
- `ctx.ui.select` inside the `project_trust` event (let a vote handler prompt) — ADR-0171 follow-up.
