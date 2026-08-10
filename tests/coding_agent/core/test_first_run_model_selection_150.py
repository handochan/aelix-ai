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


# ── The refusal MESSAGE must not assume the reason ─────────────────────────
#
# The first version of ``_unrunnable_message`` hardcoded "no adapter". But
# ``is_runnable`` also blocks models for MISSING CONFIG on APIs that ARE
# registered, and measured live with only a cloudflare-ai-gateway credential the
# first-run line read:
#
#   the 35 model(s) your credentials unlock (cloudflare-ai-gateway) all use the
#   anthropic-messages, openai-completions, openai-responses API, which this
#   build has no adapter for (supported: anthropic-messages, …,
#   openai-completions, openai-responses)
#
# — the same APIs named unsupported and supported in one sentence. Versus main
# that is an accuracy regression: main's line was vague but true.


def _cloudflare_row() -> Model:
    """A real cloudflare-ai-gateway shape: SUPPORTED api, templated base_url."""

    return Model(
        id="claude-3-5-haiku",
        name="claude-3-5-haiku",
        api="anthropic-messages",
        provider="cloudflare-ai-gateway",
        base_url=(
            "https://gateway.ai.cloudflare.com/v1/{CLOUDFLARE_ACCOUNT_ID}"
            "/{CLOUDFLARE_GATEWAY_ID}/compat"
        ),
    )


@pytest.fixture
def _no_provider_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_GATEWAY_ID",
        "GOOGLE_CLOUD_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.asyncio
async def test_config_missing_models_are_not_reported_as_adapterless(
    _no_provider_config: None,
) -> None:
    """The regression this section exists for."""

    res = await find_initial_model(model_registry=_reg([_cloudflare_row()]))
    assert res.model is None
    message = res.fallback_message or ""
    assert "no adapter" not in message, message
    assert "CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_GATEWAY_ID" in message
    assert "cloudflare-ai-gateway" in message


@pytest.mark.asyncio
async def test_the_message_never_calls_a_supported_api_unsupported(
    _no_provider_config: None,
) -> None:
    """The self-contradiction guard, stated as the invariant it is."""

    blocked = [_cloudflare_row()]
    res = await find_initial_model(model_registry=_reg(blocked))
    message = res.fallback_message or ""
    if "has no adapter for" in message:
        named = message.split("has no adapter for")[0]
        for api in REGISTERED_APIS:
            assert api not in named, (
                f"{api} IS registered but the message calls it unsupported: "
                f"{message}"
            )


@pytest.mark.asyncio
async def test_vertex_without_gcp_auth_names_the_gcp_variables(
    _no_provider_config: None,
) -> None:
    """``google-vertex`` IS a registered api — the blocker is GCP config."""

    vertex = Model(
        id="gemini-3.1-pro-preview",
        name="gemini-3.1-pro-preview",
        api="google-vertex",
        provider="google-vertex",
        base_url="https://{location}-aiplatform.googleapis.com",
    )
    res = await find_initial_model(model_registry=_reg([vertex]))
    message = res.fallback_message or ""
    assert "no adapter" not in message, message
    assert "GOOGLE_CLOUD_API_KEY" in message
    assert "GOOGLE_CLOUD_LOCATION" in message


@pytest.mark.asyncio
async def test_a_genuinely_adapterless_api_still_says_no_adapter() -> None:
    """The other branch: azure-openai-responses has no adapter in this build.

    Its rows ALSO declare an empty ``baseUrl``, so a naive delegation would tell
    the user to set a base URL — useless advice when the adapter does not exist.
    ``blocked_reason`` classifies adapterlessness first for exactly this row.
    """

    azure = Model(
        id="gpt-5.4",
        name="gpt-5.4",
        api="azure-openai-responses",
        provider="azure-openai-responses",
        base_url="",
    )
    res = await find_initial_model(model_registry=_reg([azure]))
    message = res.fallback_message or ""
    assert "azure-openai-responses API, which this build has no adapter for" in message
    assert "declares no base URL" not in message


@pytest.mark.asyncio
async def test_mixed_reasons_are_reported_separately(
    _no_provider_config: None,
) -> None:
    """A user with two credentials gets both obstacles, each named correctly."""

    bedrock = _m("amazon-bedrock", "nova", "bedrock-converse-stream")
    res = await find_initial_model(
        model_registry=_reg([bedrock, _cloudflare_row()])
    )
    message = res.fallback_message or ""
    assert "bedrock-converse-stream API, which this build has no adapter" in message
    assert "CLOUDFLARE_ACCOUNT_ID" in message
    assert "1 use the bedrock-converse-stream" in message
    assert "1 need setup first" in message


def test_blocked_reason_splits_the_five_cases() -> None:
    """The classifier the message groups on, exercised directly."""

    assert runnable_models.blocked_reason(
        _m("amazon-bedrock", "nova", "bedrock-converse-stream")
    ) == runnable_models.BLOCKED_NO_ADAPTER
    assert runnable_models.blocked_reason(
        _cloudflare_row()
    ) == runnable_models.BLOCKED_CONFIG_MISSING
    assert runnable_models.blocked_reason(
        Model(id="x", name="x", api="unknown", provider="typo", base_url="")
    ) == runnable_models.BLOCKED_UNRESOLVED_API
    assert runnable_models.blocked_reason(
        Model(id="x", name="x", api="anthropic-messages", provider="ext", base_url="")
    ) == runnable_models.BLOCKED_NO_HOST
    # A runnable model has no reason at all.
    assert runnable_models.blocked_reason(
        _m("openai", "gpt-5.4", "openai-completions")
    ) == ""


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


def test_exactly_three_defaults_name_an_adapterless_api() -> None:
    """Narrow by design: this compares ``api`` against the registered set ONLY.

    It proves the *adapterless* population and nothing else. It does NOT prove
    which defaults are runnable — ``is_runnable`` blocks for missing config too,
    which is why the test below runs the real predicate instead of this proxy.
    Renamed from ``test_every_other_provider_default_is_runnable``, which claimed
    a result this comparison cannot reach.
    """

    catalog = _catalog()
    adapterless = {
        provider
        for provider, model_id in DEFAULT_MODEL_PER_PROVIDER.items()
        if catalog.get(provider, {}).get(model_id, {}).get("api")
        not in REGISTERED_APIS
    }
    assert adapterless == {"amazon-bedrock", "azure-openai-responses", "mistral"}


def test_the_real_predicate_over_every_default_pins_six_unrunnable(
    _no_provider_config: None,
) -> None:
    """Run ``is_runnable`` itself over all 35 defaults and pin the whole set.

    Measured with the six adapters registered and provider config scrubbed from
    the environment. SIX defaults are unrunnable, not three: the three whose api
    has no adapter, plus three more whose api IS registered but whose required
    configuration is absent —

        google-vertex          google-vertex        no GCP auth resolvable
        cloudflare-workers-ai  openai-completions   {CLOUDFLARE_ACCOUNT_ID}
        cloudflare-ai-gateway  openai-completions   + {CLOUDFLARE_GATEWAY_ID}

    The last three are recoverable (set the env vars and they run), which is
    exactly why the refusal message must not call them adapterless. The first
    three are not recoverable in this build at all.
    """

    catalog = _catalog()
    unrunnable = {}
    for provider, model_id in DEFAULT_MODEL_PER_PROVIDER.items():
        row = catalog.get(provider, {}).get(model_id)
        assert row is not None, f"{provider}/{model_id} is not in the catalog"
        model = Model(
            id=model_id,
            name=model_id,
            api=row.get("api") or "unknown",
            provider=provider,
            base_url=row.get("baseUrl") or "",
        )
        if not runnable_models.is_runnable(model):
            unrunnable[provider] = runnable_models.blocked_reason(model)

    assert unrunnable == {
        "amazon-bedrock": runnable_models.BLOCKED_NO_ADAPTER,
        "azure-openai-responses": runnable_models.BLOCKED_NO_ADAPTER,
        "mistral": runnable_models.BLOCKED_NO_ADAPTER,
        "google-vertex": runnable_models.BLOCKED_VERTEX_CONFIG,
        "cloudflare-workers-ai": runnable_models.BLOCKED_CONFIG_MISSING,
        "cloudflare-ai-gateway": runnable_models.BLOCKED_CONFIG_MISSING,
    }
    assert len(DEFAULT_MODEL_PER_PROVIDER) == 35


def test_the_three_config_blocked_defaults_recover_once_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the split is real: config-missing is a different KIND of blocked."""

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    catalog = _catalog()
    for provider in (
        "cloudflare-workers-ai",
        "cloudflare-ai-gateway",
        "google-vertex",
    ):
        model_id = DEFAULT_MODEL_PER_PROVIDER[provider]
        row = catalog[provider][model_id]
        model = Model(
            id=model_id, name=model_id, api=row["api"], provider=provider,
            base_url=row.get("baseUrl") or "",
        )
        assert runnable_models.is_runnable(model), provider

    for provider in ("amazon-bedrock", "azure-openai-responses", "mistral"):
        model_id = DEFAULT_MODEL_PER_PROVIDER[provider]
        row = catalog[provider][model_id]
        model = Model(
            id=model_id, name=model_id, api=row["api"], provider=provider,
            base_url=row.get("baseUrl") or "",
        )
        assert not runnable_models.is_runnable(model), (
            f"{provider} is adapterless; no configuration can fix it"
        )
