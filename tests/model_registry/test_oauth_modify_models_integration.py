"""OAuth modify_models callback integration — Sprint 6f W2 (ADR-0065 §H).

Pi parity: ``model-registry.ts::loadModels`` step 3 invokes
``oauthProvider.modifyModels(models, creds)`` for every registered OAuth
provider with live credentials. Sprint 6e wired Copilot's
``modify_models`` for base-URL injection on Copilot-routed models;
Sprint 6f₁ surfaces that wiring through the new
:class:`ModelRegistry`.

This test inserts a faux Copilot OAuth credential into AuthStorage and
patches the module-level ``_PROVIDER_MODELS`` registry to add a fake
Copilot-routed model so we can prove the ``modify_models`` callback
fires + the base_url lands.
"""

from __future__ import annotations

from pathlib import Path

from aelix_ai import models as aelix_models
from aelix_ai.oauth import AuthStorage
from aelix_ai.oauth.github_copilot import GITHUB_COPILOT_OAUTH_ID
from aelix_ai.oauth.types import OAuthCredentials
from aelix_ai.streaming import Model, ModelCost
from aelix_coding_agent.model_registry import ModelRegistry


async def _ready_storage(tmp_path: Path) -> AuthStorage:
    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    return s


async def test_modify_models_invoked_for_copilot_credentials(
    tmp_path: Path,
) -> None:
    """Copilot's ``modify_models`` injects ``base_url`` for github-copilot
    models when valid OAuth credentials are present.
    """

    fake_copilot_model = Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5 (Copilot)",
        provider=GITHUB_COPILOT_OAUTH_ID,
        api="anthropic-messages",
        cost=ModelCost(input=3.0, output=15.0),
        max_tokens=64000,
        context_window=200000,
        reasoning=True,
        input=["text", "image"],
    )
    # Patch the module-level provider→model index so get_providers /
    # get_models surface the faux Copilot entry. We restore the original
    # snapshot in the finally branch.
    original = aelix_models._PROVIDER_MODELS.get(GITHUB_COPILOT_OAUTH_ID)
    aelix_models._PROVIDER_MODELS[GITHUB_COPILOT_OAUTH_ID] = {
        fake_copilot_model.id: fake_copilot_model
    }
    try:
        s = await _ready_storage(tmp_path)
        # Token MUST encode ``proxy-ep=`` so the Copilot modify_models
        # callback can extract the API base URL.
        creds = OAuthCredentials(
            refresh="ghr_refresh",
            access=(
                "tid=test;exp=9999999999;"
                "proxy-ep=proxy.individual.githubcopilot.com;"
            ),
            expires=10**12,
            extra={},
        )
        await s.set_oauth(GITHUB_COPILOT_OAUTH_ID, creds)

        # ModelRegistry construction triggers ``_load_models`` which
        # invokes ``modify_models`` for each registered OAuth provider
        # with live credentials.
        r = ModelRegistry.in_memory(s)
        copilot_models = [
            m for m in r.get_all() if m.provider == GITHUB_COPILOT_OAUTH_ID
        ]
        assert copilot_models, "Faux Copilot model should be in the catalog"
        modified = copilot_models[0]
        # Pi parity: ``base_url`` injected from ``proxy-ep=``.
        assert modified.base_url == "https://api.individual.githubcopilot.com"
    finally:
        if original is None:
            aelix_models._PROVIDER_MODELS.pop(GITHUB_COPILOT_OAUTH_ID, None)
        else:
            aelix_models._PROVIDER_MODELS[GITHUB_COPILOT_OAUTH_ID] = original


async def test_modify_models_not_invoked_without_credentials(
    tmp_path: Path,
) -> None:
    """Without an OAuth credential, ``modify_models`` is skipped → no
    base_url injection.
    """

    fake_copilot_model = Model(
        id="probe-model",
        name="Probe",
        provider=GITHUB_COPILOT_OAUTH_ID,
        api="anthropic-messages",
    )
    original = aelix_models._PROVIDER_MODELS.get(GITHUB_COPILOT_OAUTH_ID)
    aelix_models._PROVIDER_MODELS[GITHUB_COPILOT_OAUTH_ID] = {
        fake_copilot_model.id: fake_copilot_model
    }
    try:
        s = await _ready_storage(tmp_path)  # no credentials
        r = ModelRegistry.in_memory(s)
        copilot_models = [
            m for m in r.get_all() if m.provider == GITHUB_COPILOT_OAUTH_ID
        ]
        assert copilot_models
        # Without creds, modify_models is NOT called — base_url stays empty.
        assert copilot_models[0].base_url == ""
    finally:
        if original is None:
            aelix_models._PROVIDER_MODELS.pop(GITHUB_COPILOT_OAUTH_ID, None)
        else:
            aelix_models._PROVIDER_MODELS[GITHUB_COPILOT_OAUTH_ID] = original


def test_modify_models_is_dataclass_replace_not_mutation() -> None:
    """Pi parity: ``modify_models`` returns a new list, doesn't mutate originals.

    Sprint 6e W6 (P-145/P-146) hardens this — the test catches accidental
    in-place mutation of the catalog Model objects.
    """

    from aelix_ai.oauth.github_copilot import _modify_copilot_models

    original = Model(
        id="x",
        provider=GITHUB_COPILOT_OAUTH_ID,
        api="anthropic-messages",
        base_url="",
    )
    creds = OAuthCredentials(
        refresh="r",
        access="proxy-ep=proxy.example.githubcopilot.com",
        expires=10**12,
        extra={},
    )
    out = _modify_copilot_models([original], creds)
    # Original is untouched (frozen dataclass would raise; here we check
    # the contract).
    assert original.base_url == ""
    # New model in returned list has base_url injected.
    assert out[0].base_url == "https://api.example.githubcopilot.com"
    # And the new model is a different instance (dataclasses.replace).
    assert out[0] is not original
