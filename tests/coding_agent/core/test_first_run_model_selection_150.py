"""#150 — the first-run cascade must never select a model that cannot run.

``tui/shell.py`` uses ``is_runnable(current)`` as the TRIGGER for auto-selecting
a model, and ``is_runnable`` IS adapter-aware. But ``find_initial_model`` did not
apply the same predicate when it CHOSE: step 4 returned the
``DEFAULT_MODEL_PER_PROVIDER`` entry whatever its ``api`` was, and step 5's
``get_available()`` is auth-filtered, not adapter-filtered. So the code checked
runnability to decide *whether* to choose, then chose without checking.

This build registers exactly six adapter APIs. Three of the 35 provider defaults
name an api with no adapter, and for all three EVERY catalog row is on that same
api, so there is no runnable sibling inside the provider:

    amazon-bedrock          bedrock-converse-stream   (84 rows)
    azure-openai-responses  azure-openai-responses    (42 rows)
    mistral                 mistral-conversations     (28 rows)

pi (``model-resolver.ts:483-563`` @ 734e08e) applies no adapter filter and has no
``is_runnable`` equivalent — because pi SHIPS ``amazon-bedrock.ts``,
``azure-openai-responses.ts`` and ``mistral.ts``. The defaults map was ported
verbatim, so these entries are correct upstream and unrunnable here. aelix is
repairing a port, not correcting pi.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, cast

import pytest
from aelix_ai.streaming import Model
from aelix_coding_agent.core import runnable_models
from aelix_coding_agent.core.model_resolver import (
    DEFAULT_MODEL_PER_PROVIDER,
    find_initial_model,
    restore_model_from_session,
)
from aelix_coding_agent.model_registry import ModelRegistry

# The six APIs ``cli/runtime_bootstrap.register_providers`` actually registers.
REGISTERED_APIS = {
    "anthropic-messages",
    "google-generative-ai",
    "google-vertex",
    "openai-codex-responses",
    "openai-completions",
    "openai-responses",
}


@pytest.fixture(autouse=True)
def _adapters_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the registered-API set without mutating global provider state.

    ``partition_runnable`` / ``is_runnable`` deliberately fail OPEN when the set
    is empty (headless, embedders, most of the suite), so without this the filter
    under test is a no-op.
    """

    monkeypatch.setattr(
        runnable_models, "supported_apis", lambda: set(REGISTERED_APIS)
    )


def _m(provider: str, model_id: str, api: str) -> Model:
    # A non-empty base_url is required: a hostless model is independently
    # unrunnable (#98 credential-egress guard) and would confound these tests.
    return Model(
        id=model_id,
        name=model_id,
        api=api,
        provider=provider,
        base_url=f"https://{provider}.example/v1",
    )


class _StubRegistry:
    def __init__(self, available: list[Model]) -> None:
        self._available = available

    def get_all(self) -> list[Model]:
        return list(self._available)

    def get_available(self) -> list[Model]:
        return list(self._available)

    def find(self, provider: str, model_id: str) -> Model | None:
        return next(
            (
                m for m in self._available
                if m.provider == provider and m.id == model_id
            ),
            None,
        )

    def has_configured_auth(self, model: Model | None) -> bool:
        return model is not None


def _reg(models: list[Model]) -> ModelRegistry:
    return cast(ModelRegistry, _StubRegistry(models))


# ── The blocker ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refuses_the_adapterless_bedrock_default() -> None:
    """A Bedrock-only user gets a clear message, not a silently-broken model."""

    bedrock = [
        _m("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1", "bedrock-converse-stream"),
        _m("amazon-bedrock", "us.anthropic.claude-sonnet-4-v1", "bedrock-converse-stream"),
    ]
    res = await find_initial_model(model_registry=_reg(bedrock))

    # Pre-fix this returned the bedrock default, which cannot complete a turn.
    assert res.model is None
    assert res.fallback_message is not None
    assert "bedrock-converse-stream" in res.fallback_message
    assert "amazon-bedrock" in res.fallback_message
    # The message names a way out rather than just saying "no".
    assert "no adapter" in res.fallback_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "api"),
    [
        ("amazon-bedrock", "bedrock-converse-stream"),
        ("azure-openai-responses", "azure-openai-responses"),
        ("mistral", "mistral-conversations"),
    ],
)
async def test_every_adapterless_default_is_refused(provider: str, api: str) -> None:
    model = _m(provider, DEFAULT_MODEL_PER_PROVIDER[provider], api)
    res = await find_initial_model(model_registry=_reg([model]))
    assert res.model is None
    assert res.fallback_message is not None and api in res.fallback_message


@pytest.mark.asyncio
async def test_never_substitutes_an_unrelated_providers_model() -> None:
    """Silently picking something the user never authenticated is worse.

    The registry here only exposes bedrock, so there is nothing legitimate to
    fall back to; the cascade must say so rather than reach past the filter.
    """

    bedrock = [_m("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1", "bedrock-converse-stream")]
    res = await find_initial_model(model_registry=_reg(bedrock))
    assert res.model is None


@pytest.mark.asyncio
async def test_prefers_a_runnable_default_over_an_adapterless_one() -> None:
    """amazon-bedrock is FIRST in the defaults map — it must still be skipped."""

    models = [
        _m("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1", "bedrock-converse-stream"),
        _m("anthropic", "claude-opus-4-7", "anthropic-messages"),
    ]
    res = await find_initial_model(model_registry=_reg(models))
    assert res.model is not None
    assert (res.model.provider, res.model.id) == ("anthropic", "claude-opus-4-7")


@pytest.mark.asyncio
async def test_step_5_first_available_also_skips_adapterless() -> None:
    """No known-provider default matches, so this exercises the tail of step 5."""

    models = [
        _m("mistral", "some-unlisted-model", "mistral-conversations"),
        _m("custom-corp", "corp-model", "openai-completions"),
    ]
    res = await find_initial_model(model_registry=_reg(models))
    assert res.model is not None and res.model.id == "corp-model"


@pytest.mark.asyncio
async def test_unrunnable_saved_default_falls_through_to_a_runnable_model() -> None:
    """Step 3 must not hand back a saved default that can no longer run.

    Returning it would make the caller's ``is_runnable`` post-check fail and
    strand the user with no model, when a runnable one was available all along.
    """

    models = [
        _m("mistral", "devstral-medium-latest", "mistral-conversations"),
        _m("anthropic", "claude-opus-4-7", "anthropic-messages"),
    ]
    res = await find_initial_model(
        default_provider="mistral",
        default_model_id="devstral-medium-latest",
        model_registry=_reg(models),
    )
    assert res.model is not None and res.model.id == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_runnable_saved_default_is_still_honoured() -> None:
    models = [
        _m("anthropic", "claude-opus-4-7", "anthropic-messages"),
        _m("anthropic", "claude-haiku-4-5", "anthropic-messages"),
    ]
    res = await find_initial_model(
        default_provider="anthropic",
        default_model_id="claude-haiku-4-5",
        default_thinking_level="high",
        model_registry=_reg(models),
    )
    assert res.model is not None and res.model.id == "claude-haiku-4-5"
    assert res.thinking_level == "high"


@pytest.mark.asyncio
async def test_no_credentials_at_all_stays_silent() -> None:
    """Nothing available is the first-run case — the caller owns that copy."""

    res = await find_initial_model(model_registry=_reg([]))
    assert res.model is None
    assert res.fallback_message is None


@pytest.mark.asyncio
async def test_session_restore_fallback_also_skips_adapterless() -> None:
    """The identical cascade in ``restore_model_from_session`` had the same bug."""

    models = [
        _m("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1", "bedrock-converse-stream"),
        _m("anthropic", "claude-opus-4-7", "anthropic-messages"),
    ]
    res = await restore_model_from_session(
        "openai", "gone-model", None, False, _reg(models)
    )
    assert res.model is not None and res.model.id == "claude-opus-4-7"


# ── Item 2: the OpenRouter default (owner decision) ────────────────────────


def _catalog() -> dict[str, Any]:
    return json.loads((files("aelix_ai") / "models_generated.json").read_text())


def test_openrouter_default_is_gpt_5_4() -> None:
    assert DEFAULT_MODEL_PER_PROVIDER["openrouter"] == "openai/gpt-5.4"


def test_openrouter_default_matches_the_direct_openai_default() -> None:
    """The stated reason for the pick: same model either way in."""

    assert (
        DEFAULT_MODEL_PER_PROVIDER["openrouter"]
        == f"openai/{DEFAULT_MODEL_PER_PROVIDER['openai']}"
    )


def test_openrouter_default_catalog_row_is_as_verified() -> None:
    """Pin the row the owner's decision was checked against."""

    row = _catalog()["openrouter"]["openai/gpt-5.4"]
    assert row["api"] == "openai-completions"
    assert row["contextWindow"] == 1050000
    assert row["maxTokens"] == 128000
    assert row["contextWindow"] - row["maxTokens"] == 922000
    assert row["reasoning"] is True


def test_openrouter_default_is_not_the_wave_1_landmine() -> None:
    """The old default had headroom 2 and produced the first-turn 400."""

    old = _catalog()["openrouter"]["moonshotai/kimi-k2.6"]
    assert old["contextWindow"] - old["maxTokens"] == 2
    assert DEFAULT_MODEL_PER_PROVIDER["openrouter"] != "moonshotai/kimi-k2.6"


@pytest.mark.asyncio
async def test_new_openrouter_default_resolves() -> None:
    models = [
        _m("openrouter", "moonshotai/kimi-k2.6", "openai-completions"),
        _m("openrouter", "openai/gpt-5.4", "openai-completions"),
    ]
    res = await find_initial_model(model_registry=_reg(models))
    assert res.model is not None
    assert (res.model.provider, res.model.id) == ("openrouter", "openai/gpt-5.4")


# ── Catalog-wide guard ─────────────────────────────────────────────────────


def test_the_adapterless_providers_have_no_runnable_row_at_all() -> None:
    """Documents WHY the fallthrough returns None rather than a sibling model."""

    catalog = _catalog()
    for provider in ("amazon-bedrock", "azure-openai-responses", "mistral"):
        rows = catalog[provider]
        assert rows, provider
        assert not [r for r in rows.values() if r.get("api") in REGISTERED_APIS], (
            f"{provider} unexpectedly has a runnable row; the fallthrough "
            "rationale needs revisiting"
        )


def test_every_other_provider_default_is_runnable() -> None:
    """The three known-bad entries are the complete set — no silent fourth."""

    catalog = _catalog()
    broken = {
        provider
        for provider, model_id in DEFAULT_MODEL_PER_PROVIDER.items()
        if catalog.get(provider, {}).get(model_id, {}).get("api")
        not in REGISTERED_APIS
    }
    assert broken == {"amazon-bedrock", "azure-openai-responses", "mistral"}
