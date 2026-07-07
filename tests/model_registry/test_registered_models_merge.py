"""Issue #77 Gap B — ``register_provider(ProviderConfigInput(models=...))`` merges
the registered provider's catalog models into ``/model``, plus the public
``get_registered_providers`` enumeration accessor.

Before this change ``ProviderConfigInput.models`` was dead code (auth wiring
only), so an extension-registered provider added ZERO ``/model`` rows.
"""

from __future__ import annotations

from pathlib import Path

from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput


async def _reg(tmp_path: Path) -> ModelRegistry:
    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    return ModelRegistry.in_memory(s)


async def test_registered_models_appear_in_catalog(tmp_path: Path) -> None:
    r = await _reg(tmp_path)
    before = len(r.get_all())
    r.register_provider(
        "telnaut",
        ProviderConfigInput(
            api_key="k",
            models={"telnaut-1": Model(id="telnaut-1", api="openai-completions")},
        ),
    )
    pairs = {(m.provider, m.id) for m in r.get_all()}
    assert ("telnaut", "telnaut-1") in pairs
    assert len(r.get_all()) == before + 1


async def test_registered_model_provider_stamped_from_name(tmp_path: Path) -> None:
    # A model left at the default provider ("unknown") is stamped with the
    # registration name so (provider, id) dedup + has_configured_auth line up.
    r = await _reg(tmp_path)
    r.register_provider(
        "telnaut",
        ProviderConfigInput(
            api_key="k", models={"m1": Model(id="m1", api="openai-completions")}
        ),
    )
    telnaut = [m for m in r.get_all() if m.provider == "telnaut"]
    assert [m.id for m in telnaut] == ["m1"]


async def test_registered_model_explicit_provider_preserved(tmp_path: Path) -> None:
    r = await _reg(tmp_path)
    r.register_provider(
        "telnaut",
        ProviderConfigInput(
            api_key="k",
            models={
                "m1": Model(id="m1", provider="telnaut", api="openai-completions")
            },
        ),
    )
    assert any(m.provider == "telnaut" and m.id == "m1" for m in r.get_all())


async def test_merge_is_idempotent_across_reregister(tmp_path: Path) -> None:
    r = await _reg(tmp_path)
    cfg = ProviderConfigInput(
        api_key="k", models={"m1": Model(id="m1", api="openai-completions")}
    )
    r.register_provider("telnaut", cfg)
    r.register_provider("telnaut", cfg)  # re-register → _load_models re-runs
    telnaut = [m for m in r.get_all() if m.provider == "telnaut"]
    assert len(telnaut) == 1  # deduped on (provider, id)


async def test_register_provider_without_models_adds_no_rows(tmp_path: Path) -> None:
    r = await _reg(tmp_path)
    before = len(r.get_all())
    r.register_provider("telnaut", ProviderConfigInput(api_key="k"))  # auth-only
    assert len(r.get_all()) == before


async def test_registered_model_with_key_is_available(tmp_path: Path) -> None:
    # api_key in the config satisfies has_configured_auth → the merged model is
    # not just in get_all() but also in get_available().
    r = await _reg(tmp_path)
    r.register_provider(
        "telnaut",
        ProviderConfigInput(
            api_key="k", models={"m1": Model(id="m1", api="openai-completions")}
        ),
    )
    assert any(
        m.provider == "telnaut" and m.id == "m1" for m in r.get_available()
    )


async def test_get_registered_providers_returns_copy(tmp_path: Path) -> None:
    r = await _reg(tmp_path)
    r.register_provider("telnaut", ProviderConfigInput(api_key="k"))
    got = r.get_registered_providers()
    assert "telnaut" in got
    got.pop("telnaut")  # mutating the returned copy...
    assert "telnaut" in r.get_registered_providers()  # ...never touches the live registry
