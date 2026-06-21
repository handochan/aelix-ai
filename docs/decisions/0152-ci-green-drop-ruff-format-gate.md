# 0152. Make CI green for release — drop the `ruff format --check` gate, fix lint, complete the changelog

Status: Accepted
Date: 2026-06-21

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

v1 release-prep (after Sprints S0–S4) verified the documented release gate
(`RELEASING.md` step 3 + the `ci.yml` workflow). `gh run list` showed **every CI
run since Sprint 1 has failed** — CI has never been green. The local `pytest`
gate (3553 pass) was green throughout, which masked the failure: CI fails at the
**`Lint (ruff check)`** step, before format-check and pytest ever run.

Two distinct causes:

1. **`ruff check .` — 4 real lint errors on the committed tree**
   (`extensions/command_context.py`: `UP035` typing→`collections.abc`, `I001`
   import order, `F401` unused `ForkOptions`; `test_adr0135_*`: `I001`). Genuine,
   auto-fixable.
2. **`ruff format --check .` — 314 of 480 files diverge.** The codebase is
   deliberately **hand-formatted** for readability (pi-parity annotations,
   multi-line calls with arguments split for clarity). `ruff format` (opinionated,
   black-like) collapses calls that fit `line-length = 100` without a magic
   trailing comma. The S1 CI added this step but the tree was never run through
   `ruff format`, so the gate was red from day one — a Sprint 1 release-infra
   defect.

## Decision

Make CI green with the **least churn that respects the existing style**, chosen
by the maintainer:

- **Drop the `ruff format --check .` step** from `ci.yml` and from the
  `RELEASING.md` local-verify list. `ruff check` (the linter: `E/W/F/I/UP/B/SIM`)
  remains the enforced style gate. Auto-formatting 314 files would undo
  intentional hand-formatting for no correctness gain.
- **Fix the 4 `ruff check` errors** (`ruff check --fix` — import modernization,
  ordering, unused-import removal; behavior-neutral).
- **Complete the `[0.1.0]` changelog** — it pre-dated Sprints 2–4 and omitted
  Project Trust (S2) and cooperative abort (S3). Both added.
- **Fix a Python 3.11-only test failure that the now-green `ruff check` step
  revealed** (CI had died at `ruff check` in ~16 s before pytest ever ran, so the
  py3.11 break was never surfaced). The headless `ExtensionUIContext.theme`
  *property* raised `NotImplementedError`. Under `@runtime_checkable`,
  `isinstance(ctx, ExtensionUIContext)` probes data-member getters via `hasattr`
  on 3.11 (which *invokes* the property → raises) but via `getattr_static` on
  3.12 (which does not), so the raise broke structural conformance **on 3.11
  only**. Fix: the headless `theme` now returns a no-op default `Theme` (identity
  resolvers) instead of raising — semantically sound for a no-UI context, with no
  production reliance on the raise; the callable theme members
  (`get_theme`/`set_theme`/`get_all_themes`) still raise. The two "theme raises"
  tests were updated to the new contract.

`ruff format` is not removed from the repo config (`[tool.ruff.format]` stays for
anyone who opts to run it locally); it is simply no longer a CI gate.

## Consequences

- CI goes green for the first time, on **both** matrix Pythons: committed-tree
  `ruff check` passes (verified via `git ls-files '*.py' | xargs ruff check` →
  clean) and the pytest gate is green on py3.11 **and** py3.12 (3553 pass / 1
  skip / 0 fail; the py3.11 `theme` fix closed the last 2 failures that the green
  ruff step had newly exposed).
- The release path in `RELEASING.md` is now actually satisfiable end-to-end.
- Style policy is explicit: lint-enforced, format-by-convention. A future sprint
  may adopt strict `ruff format` deliberately (one large reformat commit) if the
  team chooses; until then the gate would be a false signal.

## Files

- `.github/workflows/ci.yml` (drop format-check step + note), `RELEASING.md`
  (drop format-check from local verify)
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/command_context.py`,
  `tests/test_adr0135_reasoning_state_wiring.py` (lint fixes)
- `CHANGELOG.md` (Project Trust + cooperative abort)
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/headless_ui.py`
  (headless `theme` returns a default Theme), `tests/extensions/test_ext_ui_context.py`
  (the two theme tests updated to the new contract) — the py3.11 conformance fix
