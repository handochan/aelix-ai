# ADR-0166 — WP-8 follow-ups: runnable-API guard, custom-provider model fetch, auto-mode doc fix

- **Status:** Accepted
- **Date:** 2026-06-22
- **Sprint:** WP-8 (follow-up)
- **Relates:** ADR-0165 (WP-8 subsystems), ADR-0158 (tree-sitter auto-mode classifier),
  ADR-0140 (models.json loader), ADR-0162 (scoped-models enforcement).

## Context

After WP-8 shipped (ADR-0165) the user reported three things from real use:

1. **GitHub Copilot OAuth → unusable model.** `/login` → OAuth → Copilot signed in fine, then
   selecting `gpt-5.4-mini` and sending a message failed with the cryptic
   `✖ No provider registered for api='openai-responses'`. Root cause: the bundled catalog has **81
   models on the `openai-responses` API** (OpenAI + Copilot gpt-5.x), but `aelix_ai` ships **no
   Responses adapter** — only `openai-completions` + `anthropic-messages` register
   (`cli/runtime_bootstrap.register_providers`). `/login` merely exposed this by letting users
   authenticate to Copilot. The Responses adapter is **protected-core** work (`aelix_ai.providers`).
2. **"auto mode" already exists.** The ADR-0165 deferred list (D2) wrongly called auto-mode unbuilt.
   It is implemented (ADR-0158): `PermissionMode.AUTO`, the tree-sitter bash classifier, the
   shift+tab cycle, and the `🤖 auto` footer badge all ship. D2 was a documentation error.
3. **Custom provider had no model step.** The custom-provider `/login` path stored a key then stopped
   ("add the model via models.json"). The user asked it to fetch the endpoint's model list and
   register them.

4. **`/login` was inert at runtime (THE real cause of #1's "No API key").** Diagnosing #1 surfaced a
   deeper bug: `entry.py` wired the harness `get_api_key_and_headers` auth callback **only when
   `--api-key` was passed** (`if parsed.api_key is not None:`). On a normal launch it was `None`, so
   the harness resolved keys from **environment variables only** — never from `auth.json` (`/login`)
   or a `models.json` provider `apiKey`. Built-in providers were masked (they usually have an env
   var); the custom `openwebui` provider (no env var) exposed it. So `/login` (API-key AND OAuth) was
   effectively a no-op at runtime without `--api-key`.

## Decision

All pure TUI/CLI-consumer (`aelix_ai`/`aelix_agent_core` untouched).

- **Runnable-API guard** (`core/runnable_models.py`): a model is offered/switchable only if its
  `model.api` has a registered adapter (`aelix_ai.api_registry.get_registered_providers()`). The
  `/model` picker (`tui/model_picker.py`) now **hides** unrunnable models (with a dim "(N hidden —
  API not supported)" line) and **guards** the selection; `/model <id>` (`_model_handler`) guards the
  explicit path. Both surface an actionable message ("model X uses the 'openai-responses' API, which
  this build has no adapter for; supported: …") instead of the cryptic runtime error. When the
  registry is unpopulated (headless/tests) nothing is filtered (never over-filter).
- **Custom-provider model fetch** (`tui/login_wizard.py`): for an **OpenAI-compatible** custom
  provider, after base-URL + key the wizard fetches `GET {base_url}/models`, lets the user multiselect
  which to add, writes a **schema-valid** entry to `models.json` (provider `api: openai-completions`,
  `baseUrl`, `models:[{id}]`; the **key stays in `auth.json`**, never written to models.json), then
  reloads the registry so they appear in `/model` immediately. Anthropic/Gemini-compatible keep the
  honest "add via models.json" note (no `/models` list endpoint / no adapter). This resolves D7 for
  the common case.
- **Auth callback wired unconditionally** (`entry.py`): `get_api_key_and_headers =
  _make_auth_callback(model_registry)` now runs on every launch (the `--api-key` block only adds the
  runtime-override layer on top). This is what makes `/login`-stored credentials (auth.json) and
  custom `models.json` provider keys actually resolve at runtime. `_make_auth_callback` returns "no
  opinion" (`None`) for providers with no stored key, so env-only providers keep working via the
  adapter's `get_env_api_key` fallback — no regression. Regression test:
  `test_entry_router.py::test_auth_callback_wired_without_api_key`.
- **auto-mode doc fix**: corrected — auto-mode is implemented (ADR-0158).

## Consequences

- ruff clean; full pytest green. New tests: `test_runnable_models.py` (7), `test_login_custom_models.py`
  (8). Copilot now usable for its **chat-completions** models (gpt-4o, …) and **Claude-via-Copilot**;
  gpt-5.x (Responses) is hidden with a clear reason rather than failing cryptically.
- **Still deferred (protected-core):** the **OpenAI Responses API adapter** (`openai-responses`) — see
  ADR-0165 §Deferred (D8). Until it lands in `aelix_ai.providers`, all Responses-API models
  (OpenAI/Copilot gpt-5.x) remain non-runnable (now gracefully hidden/guarded).
- No protected-core change; no permission/trust change.
