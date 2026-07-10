"""GitHub Copilot models route via chat/completions, never the Responses API.

Regression fix: GitHub Copilot's API proxy is a chat/completions gateway and
does NOT serve the OpenAI Responses API — verified live: `POST {copilot}/responses`
returns HTTP 400 `unsupported_api_for_model` ("model X is not supported via
Responses API") for EVERY model, including ones that return 200 on
`/chat/completions` (gpt-4o, gpt-4.1). The pi catalog nonetheless marked 7
copilot models (gpt-5-mini, gpt-5.2, gpt-5.2-codex, gpt-5.3-codex, gpt-5.4,
gpt-5.4-mini, gpt-5.5) as `openai-responses`; #15 un-hid them, routing them to
`/responses` → hard failure (individual hosts: 400; enterprise hosts: httpx
"Connection error"). models.py coerces every github-copilot `openai-responses`
model to `openai-completions` at catalog load.
"""

from __future__ import annotations

from aelix_ai.models import get_model, get_models

_COERCED_IDS = [
    "gpt-5-mini",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.3-codex",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.5",
]


def test_no_copilot_model_uses_the_responses_api() -> None:
    """Not a single github-copilot catalog model may be `openai-responses`."""

    offenders = [m.id for m in get_models("github-copilot") if m.api == "openai-responses"]
    assert offenders == [], f"copilot models still on the Responses API: {offenders}"


def test_copilot_gpt5_models_coerced_to_completions() -> None:
    """The 7 previously-`openai-responses` copilot models now route via completions."""

    for mid in _COERCED_IDS:
        m = get_model("github-copilot", mid)
        assert m is not None, f"{mid} missing from copilot catalog"
        assert m.api == "openai-completions", f"{mid} api={m.api!r}, expected openai-completions"


def test_coercion_preserves_model_fields() -> None:
    """Coercion only rewrites `api` — base_url / headers / reasoning survive."""

    m = get_model("github-copilot", "gpt-5.4")
    assert m is not None
    assert m.base_url == "https://api.individual.githubcopilot.com"
    assert m.headers  # static COPILOT_HEADERS preserved
    assert m.reasoning is True


def test_other_providers_responses_models_unchanged() -> None:
    """The coercion is scoped to github-copilot — real Responses providers keep it."""

    openai_gpt5 = get_model("openai", "gpt-5.4")
    assert openai_gpt5 is not None
    assert openai_gpt5.api == "openai-responses"  # OpenAI genuinely serves Responses
    azure = get_models("azure-openai-responses")
    assert azure and all(m.api == "azure-openai-responses" for m in azure)
