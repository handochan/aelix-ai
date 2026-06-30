# ADR-0173 — native gemini adapters: google (Developer API) + google-vertex (#15)

- **Status:** Accepted (Workflow A: dormant build of both adapters; un-hide = Workflow B)
- **Date:** 2026-06-30
- **Sprint:** Backlog Batch 3 — #15 remaining half (native gemini), importance-ordered Tier 5
- **Pi pin:** `earendil-works/pi@734e08e`; these adapters ported faithfully from pi `main` SHA `3d6acb37b93d2ceedfcc170b2d212c34fedbf193`.
- **Relates:** ADR-0172 (openai-responses adapter — the structural precedent + dormant→un-hide pattern), ADR-0171 (#22 register_provider replay — prerequisite). Backlog: GitHub #15 (parent #3); decision #8 (native, no litellm).

## Context

#15's remaining half = a native gemini adapter. **Pre-work verification (web + pi HEAD):** Google killed only the *gemini-cli / Code Assist "Login with Google" OAuth free path* (2026-06-18, → Antigravity); the **API-key path is alive and forward-compatible** (AI Studio mints "auth keys" by default), and pi proactively removed the dead OAuth path in v0.71.0. So the correct, supported target is the **Gemini Developer API with an API key** — exactly pi's `google` provider. (Antigravity has no sanctioned external API and reusing its OAuth is bannable — explicitly NOT used.) The owner expanded scope to also include **google-vertex** and a **catalog forward-port** this sprint.

aelix was ~70% pre-wired: 30 `google-generative-ai` + 13 `google-vertex` catalog models, `_env_api_keys` google→GEMINI_API_KEY, KnownProvider, and the signature fields (text/thinking) already existed.

## Decision

Mirror pi's `google` + `google-vertex` over the **official `google-genai` Python SDK** (the 1:1 analog of pi's `@google/genai`), via the dormant-build → one-line un-hide pattern (ADR-0172).

**Files** (all under `packages/aelix-ai/src/aelix_ai/providers/`):
- `_google_shared.py` — shared engine (convert_messages/tools, the streaming state machine, thoughtSignature helpers, thinking-family branching, map_stop_reason, usage) — reused by both adapters.
- `_google_client.py` — lazy-importing `genai.Client` factories: Developer API (`api_key`, `http_options{base_url, api_version:""}`) and Vertex (`vertexai=True` + `GOOGLE_CLOUD_API_KEY` or ADC project/location).
- `google_generative_ai.py` (api `google-generative-ai`, GEMINI_API_KEY) + `google_vertex.py` (api `google-vertex`, GOOGLE_CLOUD_API_KEY/ADC) — thin adapters; `register_all()` defined, **not called**.

**The landmine — thoughtSignature** (gemini's analog of openai-responses encrypted reasoning): an opaque base64 token attached to text/thinking/functionCall parts, stored on the assistant turn and replayed verbatim. Triple-gate on replay: **same provider AND same model AND valid base64**; retain-last-non-empty per block; never merged/moved across parts. Needs the one protected-core field `ToolCallContent.thought_signature`.

**The Python-SDK bytes pitfall (4-lens review HIGH):** the google-genai Python SDK types `Part.thought_signature` and `Blob.data` as `bytes` (it base64-**decodes** the wire value), whereas pi's JS SDK keeps a string. The engine assumed `str` → text/thinking signatures silently dropped + tool-call replay `TypeError` (str-regex on bytes). Fixed by normalizing the SDK's bytes back to the canonical base64 **string** (`base64.b64encode(...).decode("ascii")`, not `.decode()`) at the single ingest seam; the send side passes plain dicts with base64-str values, which the SDK round-trips correctly (verified against SDK 1.75.0). A real-SDK-typed round-trip test closes the blind spot that string fixtures masked.

**Decisions/divergences:** `google-genai>=1.52,<2`, lazy-imported (missing dep never breaks module import). `_google_shared` split day-one (vertex reuse). safetySettings omitted (pi parity). abort post-hoc (Python SDK has no AbortSignal binding — openai parity). No `on_response` (SDK stream exposes no raw HTTP). Token-dict usage (cost downstream). Catalog forward-ported faithfully from pi `3d6acb37` (added stable `gemini-3.5-flash`, fixed gemini-3-pro contextWindow/maxTokens, vertex curation — values verbatim). Tool-call ids `{name}_{counter}` (deterministic) vs pi's `Date.now()` — documented.

## Verification

Gate **4484 collected / 4483 passed / 0 failed / ruff clean / project-pyright 0 errors** on the 4 new files (+~120 tests). 4-lens adversarial review: 1 HIGH (bytes round-trip) + 3 LOW (regex `$`→`\Z`, vertex gemma4-leak, id shape) — all resolved. Both adapters confirmed **dormant** (no `register_all` in runtime_bootstrap).

## Follow-ups (Workflow B)

- Un-hide: add `google_generative_ai.register_all()` + `google_vertex.register_all()` to `runtime_bootstrap.register_providers()`; surface the 30 + 13 models. Apply the "never surface un-runnable" guard for **google-vertex** (hide when neither GOOGLE_CLOUD_API_KEY nor ADC project/location is resolvable) and re-examine the 2 **opencode-zen** gemini models (provider=opencode, untested Gemini-protocol-behind-zen edge).
- Live 1-turn smoke needs a real GEMINI_API_KEY (un-runnable in sandbox; 1-line `register_all()` revert if broken).
- `SimpleStreamOptions.thinking_budgets` plumbing (custom per-effort budgets) if later required.
