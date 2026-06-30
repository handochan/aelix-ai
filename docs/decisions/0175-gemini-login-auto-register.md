# ADR-0175 — gemini `/login` custom-provider auto-register (#36 gemini side)

- **Status:** Accepted — LIVE (the `google-generative-ai` adapter is already un-hidden, so this is user-visible the moment it lands).
- **Date:** 2026-06-30
- **Sprint:** Moat chain — quick win (folded in after #44). Closes the remaining gemini half of #36.
- **Pi pin:** `earendil-works/pi@734e08e`. The targeted adapter is pi-faithful (ADR-0173); the interactive `/login` model-fetch UX itself is **aelix-original** (WP-8) — pi has no interactive login model-fetch.
- **Relates:** ADR-0173 (native gemini adapters — the dependency that unblocks this), ADR-0172/0171 (#36 openai-side: stored-auth + compat-metadata already shipped). GitHub #36, parent #3.

## Context

The `/login` "Custom provider" sub-flow auto-fetches `{base_url}/models` and registers the picked models into `models.json` for OpenAI- and Anthropic-compatible endpoints, but `_PROTOCOL_API["Gemini-compatible"]` was hardcoded to `None` — gemini skipped to the honest manual-note fallback because no gemini adapter existed. ADR-0173 (commit `29362b6`) un-hid the live `google-generative-ai` provider, so the gemini path is now wireable. The shared fetch/write/register machinery is already api-agnostic; only three Gemini-specific deltas vs the OpenAI shape remained.

## Decision

Wire `Gemini-compatible → google-generative-ai` in `login_wizard.py` (TUI auth wizard — NOT protected core), handling the three Gemini Developer API deltas:

1. **`_PROTOCOL_API["Gemini-compatible"] = "google-generative-ai"`** (was `None`).
2. **Auth header** — `_model_list_headers` gains a `google` branch returning `{"x-goog-api-key": api_key}` (the google-genai SDK header) before the Bearer fallthrough; a Bearer token 401s against `generativelanguage.googleapis.com`. Placed after the `anthropic` branch; `api.startswith("google")` only matches `google-*` apis so OpenAI/Anthropic paths are byte-for-byte unchanged.
3. **Model-id prefix** — Gemini ListModels returns `name: "models/<id>"`; `_fetch_openai_model_ids` strips a leading `models/` for `google-*` apis so the registered id matches the catalog id (`gemini-2.0-flash`, not `models/gemini-2.0-flash`) and reads cleanly in `/model`.

**Scope boundary — Vertex excluded.** `google-vertex` is intentionally NOT offered: it uses OAuth/ADC (no API-key `/models` list), its base-url carries a `{location}` placeholder, and it is GCP-config-gated. `_CUSTOM_PROTOCOLS` has no Vertex option, so `google-vertex` never reaches the new `google` header/prefix branches — a Vertex endpoint stays on the manual-note fallback. (The `startswith("google")` guard would technically also match `google-vertex`, but no flow can deliver that api here.)

No dormant-build needed — the adapter is already un-hidden, so the wiring is directly user-visible. Every failure mode still degrades to the honest manual note (the stored key is never lost).

## Verification

`python3 -m pytest tests/tui/test_login_custom_models.py tests/tui/test_login_wizard.py -q` → **34 passed**; `ruff check` clean; `.venv/bin/pyright` on the changed files → **0 errors**. Two new tests: `test_fetch_gemini_compatible_headers_and_prefix` (x-goog-api-key header + `models/` strip + correct ListModels URL) and `test_run_custom_gemini_auto_registers` (full flow → schema-valid `models.json` entry with `api=google-generative-ai`). httpx is monkeypatched — no network, no live key.

## Remaining follow-ups

- Live 1-turn smoke with a real `GEMINI_API_KEY` (sandbox-unverifiable) — folded into #61.
- Optional polish: filter ListModels to `generateContent`-capable models (it also returns embedding/imagen/aqa), and follow `nextPageToken` pagination (default page size covers all current gemini models). Neither blocks v1; multiselect already lets the user deselect.
- Anthropic-side `model.compat` detection fields remain the other open #36 sub-item (tracked separately).
