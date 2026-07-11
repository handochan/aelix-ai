# ADR-0191 — `/login` custom-provider "OpenAI Responses API" protocol option

- **Status:** Accepted — LIVE (the `openai-responses` adapter is already un-hidden/registered, so this is user-visible the moment it lands).
- **Date:** 2026-07-11
- **Sprint:** Follow-up to the pi-vs-aelix custom-provider-api investigation (this session). Closes the "custom login can only reach chat/completions" gap surfaced during that review.
- **Pi pin:** `earendil-works/pi@734e08e`. The targeted adapter is pi-faithful (ADR-0172). The interactive `/login` model-fetch UX itself is **aelix-original** (WP-8) — pi has **no** interactive custom-provider login flow at all (pi's `api` field is declared out-of-band in `models.json` or via `pi.registerProvider(...)`; its `/login` only stores a key for an already-known provider and never chooses responses-vs-completions). This ADR extends that aelix-original convenience.
- **Relates:** ADR-0166 (OpenAI-compatible custom-provider model fetch — the machinery), ADR-0175 (gemini side, same `_PROTOCOL_API` wiring pattern), ADR-0172 (native `openai-responses` adapter). GitHub #36/#15 lineage, parent #3.

## Context

The `/login` "Custom provider" sub-flow lets a user register an OpenAI- / Anthropic- / Gemini-compatible endpoint by base URL + API key: it fetches `{base_url}/models`, multiselects, writes `models.json`, and reloads so the models appear in `/model`. But `_CUSTOM_PROTOCOLS` offered only three shapes, and `_PROTOCOL_API["OpenAI-compatible"]` is hardcoded to `openai-completions`. There was **no** way to register a custom provider that speaks the OpenAI **Responses API** (`openai-responses`) — even though the `openai-responses` adapter has been registered/un-hidden since ADR-0172. A user with a Responses-API endpoint (e.g. OpenAI's own gpt-5.x / o-series) had to hand-edit `models.json` (`"api": "openai-responses"`) to reach it; the wizard silently produced a `openai-completions` model instead.

This mirrors pi's model exactly (pi also requires `api` to be declared in config), but aelix already ships the interactive login convenience — so the fix is to expose the choice there too rather than leave a completions-only wizard.

## Decision

Add a fourth custom-provider protocol option in `login_wizard.py` (TUI auth wizard — **not** protected core):

1. **`_CUSTOM_PROTOCOLS`** gains `"OpenAI-compatible (Responses API)"`, placed immediately after the plain `"OpenAI-compatible"` label so the two OpenAI wire APIs sit adjacent.
2. **`_PROTOCOL_API["OpenAI-compatible (Responses API)"] = "openai-responses"`.** The existing `"OpenAI-compatible" → "openai-completions"` mapping is **unchanged** (backward-compat; "OpenAI-compatible" continues to mean chat/completions, matching the convention most gateways speak).

No other code changes are required — the shared fetch/write/register pipeline is already `api`-agnostic:

- **Model-list probe:** `_model_list_headers("openai-responses", …)` falls through to the Bearer branch (the string starts with neither `anthropic` nor `google`), so the `GET {base_url}/models` catalog probe uses `Authorization: Bearer <key>` — exactly the OpenAI shape. `_fetch_openai_model_ids` runs a single unfiltered GET (the `generateContent` capability filter and `nextPageToken` pagination are `google-*`-only).
- **Persistence:** `_write_custom_models_json` records `"api": "openai-responses"` at the provider level; the model is runnable because the adapter is registered (`runnable_models.is_runnable` → `True`), so it is not hidden from `/model`.
- **Failure modes** still degrade to the honest manual-note fallback; the stored key is never lost.

**Scope boundary.** `azure-openai-responses` and Vertex are intentionally NOT added: azure needs deployment-specific base-url/version config beyond a plain key+URL, and Vertex uses OAuth/ADC with no API-key `/models` list (see ADR-0175). Both remain manual-note / hand-authored paths.

## Verification

`python -m pytest -q tests/tui/test_login_custom_models.py tests/tui/test_login_wizard.py tests/tui/test_login_provider.py` → **60 passed**; full suite `python -m pytest -q tests/` → **5160 passed, 1 skipped** (the lone `test_rpc_client_shutdown` SIGKILL-escalation failure is a pre-existing timing flake — passes in isolation, unrelated to this diff). `ruff check` clean; CLI `pyright` on the changed source → **0 errors, 0 warnings**.

Three new tests in `test_login_custom_models.py`:
- `test_responses_protocol_registered` — the option is offered and maps to `openai-responses`; the plain OpenAI-compatible label still maps to `openai-completions`.
- `test_fetch_openai_responses_stays_bearer` — the `/v1/models` probe uses Bearer auth and a single unfiltered GET for `openai-responses`.
- `test_run_custom_openai_responses_auto_registers` — full `_run_custom` flow → schema-valid `models.json` entry with `api=openai-responses`.

Runtime check: after `register_providers()`, `openai-responses ∈ supported_apis()` and a model carrying `api="openai-responses"` passes `is_runnable`, confirming registered models appear in `/model`. httpx is monkeypatched — no network, no live key.

Separate reviewer pass (code-reviewer, distinct lane): **APPROVE**.

## Remaining follow-ups

- Live 1-turn smoke against a real Responses-API custom endpoint with a real key (sandbox-unverifiable) — folds into #61.
- Optional future clarity: annotate the plain `"OpenAI-compatible"` label as "(Chat Completions)" for symmetry. Deferred — it would churn the existing label/tests and "OpenAI-compatible" already carries the completions convention.
