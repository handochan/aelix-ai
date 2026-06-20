# 0146. TUI-first v1 — Sprint 0 Quick Wins (AGENTS.md test isolation + no-model auth-guidance + flagship catalog)

Status: Accepted
Date: 2026-06-20
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (catalog data pulled from pi HEAD — see §3)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

First sprint of the **TUI-first v1** productization track (the user chose this path after the
six-dimension productization assessment). Three low-risk quick wins, each flagged across multiple
assessment dimensions. No protected `aelix-agent-core` change.

## Decisions

### 1. AGENTS.md test isolation (fixes the 3 long-standing gate failures)

`tests/cli/test_append_system_prompt.py` failed because `_build_harness_options` auto-discovers
`AGENTS.md` from `Path.cwd()` upward (`cli/agent_context.discover_context_files`), and the tests ran
from the repo root where an `AGENTS.md` exists — so the discovered content was prepended to
`options.append_system_prompt`. This was a **test-isolation** bug; the product behavior (cwd AGENTS.md
discovery, Pi `--no-context-files` gate) is correct and unchanged. Fix: `monkeypatch.chdir(tmp_path)`
in each test so discovery walks an empty temp tree (still exercising the REAL default path), plus one
positive lock-in test that an AGENTS.md present in cwd IS prepended. **This clears the only standing
gate failures — the suite is now fully green.**

### 2. No-model auth-guidance (Pi `auth-guidance.ts` port, honestly adapted)

New `cli/auth_guidance.py` ports Pi's four formatters (`getProviderLoginHelp`,
`formatNoModelsAvailableMessage`, `formatNoModelSelectedMessage`, `formatNoApiKeyFoundMessage`). The
non-interactive (`print`/`json`) dispatch now guards before a turn: an empty/unknown provider →
`format_no_model_selected_message()`; a resolvable provider with no key →
`format_no_api_key_found_message(provider)`; both print to stderr + exit 1 (Pi's `!session.model`
guard equivalent). Reuses the `ModelRegistry`/`AuthStorage` built on the agent-run path (ADR-0143).

**Honest adaptation (P0 #5 honesty principle — no false claims):** Pi's help text references
`<docs>/providers.md`, `<docs>/models.md`, and a `/login` command. Aelix has **none** of these (the
`BuiltinCommand` set has no `/login`; the docs files don't exist), so all three are DROPPED from the
emitted message in favor of what aelix actually offers: the real `/model` TUI command (Pi-verbatim
"Then use /model to select a model") and the `<PROVIDER>_API_KEY` env route
(ANTHROPIC_API_KEY/OPENAI_API_KEY/OPENROUTER_API_KEY). (A review caught a first cut that still claimed
`/login`; removed.) `format_no_models_available_message` is kept exported for API completeness but is
not yet wired (Pi has it).

### 3. Flagship model catalog refresh (from pi HEAD — documented pin divergence)

Added `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-fable-5`, and `minimax-m3` to
`models_generated.json` (the catalog previously reached only opus-4-7 / haiku-4-5 / sonnet-4-0). Entries
were ported verbatim from **pi HEAD's** `packages/ai/src/models.generated.ts` (id/name/api/provider/
baseUrl/reasoning/input/cost/contextWindow/maxTokens), under the correct provider, as a **minimal
append-only diff** (zero reformatting of the existing ~942 models).

**Documented divergence:** the model catalog is *data* that tracks pi HEAD, not the pinned SHA — the
gap inventory lists this as a since-pin ("regen, not a code port") refresh item, and a stale catalog
makes `--model <latest>` unresolvable. Only the missing **flagships** were added here; a full catalog
refresh (all since-pin model additions + price changes) is deferred to a later data-refresh task.

## Verification

- Implemented (3 disjoint-file parallel implementers) + 3-lens review + fix as a dynamic Workflow.
  15 findings, 1 confirmed non-LOW (the `/login` honesty MEDIUM) — fixed.
- Full gate: green (the 3 previously-failing `test_append_system_prompt` tests now PASS).
- Diff confined to `cli/entry.py`, new `cli/auth_guidance.py`, `models_generated.json`, and tests — no
  protected `aelix-agent-core` change.

## Next (TUI-first v1 track)

Sprint 1 — Release engineering (CI lint+test, semver/changelog, PyPI publish so `pip install aelix`
works). Then Sprint 2 (security gates), Sprint 3 (auth completeness + tool cooperative abort), Sprint 4
(core docs).
