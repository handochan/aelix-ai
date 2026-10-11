"""#365: explicit registrations own their effective model rows and auth projection."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import (
    _route_universe,
    resolve_route,
    restore_session_route,
)
from aelix_coding_agent.core.model_argument import resolve_model_argument
from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_coding_agent.model_registry import (
    ModelRegistry,
    ProviderConfigInput,
    registration_route_digest,
)


def row(
    provider: str = "openai", model_id: str = "m1", base: str = "http://127.0.0.1:36501/v1", **kw
) -> Model:
    return Model(id=model_id, provider=provider, api="openai-completions", base_url=base, **kw)


async def registry(tmp_path: Path, providers=None) -> ModelRegistry:
    storage = AuthStorage(tmp_path / "auth.json")
    await storage.load()
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"providers": providers or {}}), encoding="utf-8")
    return ModelRegistry.create(storage, str(path))


@pytest.mark.parametrize("provider", ["openai", "openrouter", "own"])
@pytest.mark.parametrize("empty", [False, True])
async def test_replace_and_empty_across_accessors_and_launch(
    tmp_path, provider, empty, monkeypatch
):
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    r = await registry(tmp_path)
    before_other = [(m.provider, m.id) for m in r.get_all() if m.provider != provider]
    r.register_provider(
        provider,
        ProviderConfigInput(
            api_key="extension-fake", models={} if empty else {"m1": row(provider)}
        ),
    )
    assert [(m.provider, m.id) for m in r.get_all() if m.provider != provider] == before_other
    assert [m.id for m in r.get_all() if m.provider == provider] == ([] if empty else ["m1"])
    assert r.get_registration_models(provider) is not None
    assert [m.id for m in _route_universe(r) if m.provider == provider] == ([] if empty else ["m1"])
    assert r.find(provider, "gpt-4o-mini") is None
    assert [m.id for m in r.get_available() if m.provider == provider] == ([] if empty else ["m1"])
    for reference, explicit in [
        ("unlisted", provider),
        (f"{provider}/unlisted", None),
        (f"{provider}/m1", None),
    ]:
        selected = resolve_route(reference, explicit, r, typed_key=True).model
        assert selected.provider == provider
        assert selected.api == ("unknown" if empty else "openai-completions")
        assert selected.base_url == ("" if empty else row().base_url)
    restored = restore_session_route((provider, "unlisted"), r)
    assert (restored is None) == empty


async def test_json_is_replaced_but_provider_headers_merge_and_false_clears(tmp_path):
    r = await registry(
        tmp_path,
        {
            "openai": {
                "baseUrl": "http://127.0.0.1:36503/v1",
                "apiKey": "json-fake",
                "headers": {"x-json": "j", "x-collision": "json"},
                "authHeader": True,
                "modelOverrides": {"m1": {"headers": {"x-old-model": "old"}}},
                "models": [{"id": "json-only"}],
            }
        },
    )
    r.register_provider(
        "openai",
        ProviderConfigInput(
            models={"m1": row()}, headers={"x-ext": "e", "x-collision": "ext"}, auth_header=False
        ),
    )
    assert [m.id for m in r.get_all() if m.provider == "openai"] == ["m1"]
    selected = resolve_route("gpt-4o-mini", "openai", r).model
    assert selected.base_url == row().base_url
    auth = await r.get_api_key_and_headers(r.find("openai", "m1"))
    assert auth.ok and auth.api_key == "json-fake"
    assert auth.headers == {"x-json": "j", "x-collision": "ext", "x-ext": "e"}


@pytest.mark.parametrize("auth_header", [None, False, True])
async def test_composed_key_headers_and_explicit_auth_switch(tmp_path, monkeypatch, auth_header):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-fake")
    r = await registry(
        tmp_path,
        {
            "openai": {
                "apiKey": "json-fake",
                "headers": {"x-json": "j", "x-collision": "json"},
                "authHeader": True,
            }
        },
    )
    r.register_provider(
        "openai",
        ProviderConfigInput(
            api_key="extension-fake",
            headers={"x-ext": "e", "x-collision": "ext"},
            auth_header=auth_header,
            models={"m1": row()},
        ),
    )
    auth = await r.get_api_key_and_headers(r.find("openai", "m1"))
    assert auth.ok and auth.api_key == "extension-fake"
    assert auth.headers == {
        "x-json": "j",
        "x-ext": "e",
        "x-collision": "ext",
        **({"Authorization": "Bearer extension-fake"} if auth_header is not False else {}),
    }
    assert (await r.get_provider_auth_status("openai")).source == "models_json_key"
    r._auth_storage.set_runtime_api_key("openai", "runtime-fake")
    assert (await r.get_api_key_and_headers(r.find("openai", "m1"))).api_key == "runtime-fake"
    assert (await r.get_provider_auth_status("openai")).source == "runtime"


async def test_lifecycle_full_config_and_input_snapshots(tmp_path):
    r = await registry(tmp_path)
    original = row("foreign", headers={"x-model": "original"}, compat={"nested": {"enabled": True}})
    cfg = ProviderConfigInput(
        api_key="A-fake", headers={"x-provider": "original"}, models={"m1": original}
    )
    apis = []

    def setup(api):
        apis.append(api)
        api.register_provider("openai", cfg)

    loaded = await discover_and_load_extensions([setup], cwd=tmp_path, no_discovery=True)
    cfg.models.clear()
    cfg.headers["x-provider"] = "mutated"
    original.headers["x-model"] = "mutated"
    original.compat["nested"]["enabled"] = False
    loaded.runtime.bind_model_registry(r)
    live = r.find("openai", "m1")
    assert live is not None and live.provider == "openai"
    assert original.provider == "foreign"
    assert live.headers == {"x-model": "original"} and live.compat["nested"]["enabled"]
    assert r.get_registered_providers()["openai"].headers == {"x-provider": "original"}
    got = r.get_registered_providers()
    got["openai"].models.clear()
    assert r.find("openai", "m1") is not None
    # Post-bind API fan-out and its replay are both coherent full replacement.
    apis[0].register_provider(
        "openai", ProviderConfigInput(api_key="B-fake", models={"m2": row(model_id="m2")})
    )
    loaded.runtime.bind_model_registry(r)
    assert r.find("openai", "m1") is None
    assert (await r.get_api_key_and_headers(r.find("openai", "m2"))).headers == {}
    r.register_provider("openai", ProviderConfigInput(models={}))
    for reload in (r.reset, r.refresh):
        reload()
        assert r.get_registration_models("openai") == []
    r.register_provider("openai", ProviderConfigInput(api_key="augmentation-fake"))
    assert r.get_registration_models("openai") is None and r.find("openai", "gpt-4o-mini")
    r.unregister_provider("openai")
    assert "openai" not in r.get_registered_providers() and r.find("openai", "gpt-4o-mini")


@pytest.mark.parametrize("split", ["api", "host", "empty"])
async def test_backfill_refuses_split_or_hostless_owner(tmp_path, split):
    first = row()
    second = (
        replace(first, id="m2", api="anthropic-messages")
        if split == "api"
        else row(model_id="m2", base="http://127.0.0.1:36502/v1")
    )
    if split == "empty":
        first = second = replace(first, base_url="")
    r = await registry(tmp_path)
    r.register_provider(
        "openai", ProviderConfigInput(api_key="fake", models={"a": first, "b": second})
    )
    selected = resolve_route("unlisted", "openai", r).model
    assert selected.api == "unknown" or not selected.base_url
    assert not (await r.get_api_key_and_headers(selected)).ok


async def test_model_argument_pool_and_scoped_exclusion(tmp_path):
    r = await registry(tmp_path)
    r.register_provider(
        "openai",
        ProviderConfigInput(api_key="fake", models={"m1": row(), "m2": row(model_id="m2")}),
    )
    own = await resolve_model_argument("openai/gpt-4o-mini", registry=r)
    assert own.model.base_url == row().base_url

    class Scoped:
        def get_all(self):
            return r.get_all()

        def get_available(self):
            return [r.find("openai", "m1")]

        def get_user_defined_providers(self):
            return r.get_user_defined_providers()

        def has_route_auth(self, *a, **kw):
            return True

    result = await resolve_model_argument("openai/m2", registry=Scoped())
    assert result.model is None and "excludes" in result.error


@pytest.mark.parametrize("previous", [False, True])
@pytest.mark.parametrize("provider", ["openai", "own"])
@pytest.mark.parametrize("phase", ["queued", "live"])
async def test_api_bind_load_failure_never_reopens_an_old_route(
    tmp_path, monkeypatch, previous, provider, phase
):
    from aelix_coding_agent import model_registry as mod
    from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

    r = await registry(tmp_path)
    if previous:
        r.register_provider(
            provider, ProviderConfigInput(api_key="A-fake", models={"m1": row(provider)})
        )
    runtime = _ExtensionRuntime()
    if phase == "live":
        runtime.bind_model_registry(r)
    api = ExtensionAPI(Extension(name="owner"), runtime)

    def fail_load(*a, **kw):
        raise RuntimeError("synthetic model load failure")

    monkeypatch.setattr(mod, "load_custom_models", fail_load)
    api.register_provider(
        provider,
        ProviderConfigInput(
            api_key="B-fake", models={"m1": row(provider, base="http://127.0.0.1:36502/v1")}
        ),
    )
    runtime.bind_model_registry(r)  # actual replay contains the loader exception
    assert r.get_registration_models(provider) == []
    assert [m for m in r.get_all() if m.provider == provider] == []
    assert provider in r.get_user_defined_providers()
    selected = resolve_route(f"{provider}/m1", None, r).model
    assert selected.provider == provider and selected.api == "unknown"
    assert not (await r.get_api_key_and_headers(row(provider))).ok


@pytest.mark.parametrize("action", ["augmentation", "unregister"])
async def test_failed_release_of_ownership_drops_the_previous_projection(
    tmp_path, monkeypatch, action
):
    from aelix_coding_agent import model_registry as mod
    from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

    r = await registry(tmp_path)
    r.register_provider("openai", ProviderConfigInput(api_key="A-fake", models={"m1": row()}))
    runtime = _ExtensionRuntime()
    runtime.bind_model_registry(r)
    api = ExtensionAPI(Extension(name="owner"), runtime)

    def fail_load(*a, **kw):
        raise RuntimeError("synthetic model load failure")

    monkeypatch.setattr(mod, "load_custom_models", fail_load)
    if action == "augmentation":
        api.register_provider("openai", ProviderConfigInput(api_key="B-fake"))
    else:
        api.unregister_provider("openai")
    runtime.bind_model_registry(r)
    assert r.get_registration_models("openai") is None
    assert r.find("openai", "m1") is None
    assert [m for m in r.get_all() if m.provider == "openai"] == []
    # None retains the generic caller-Model contract: this is a publication
    # safety check, not a new denial policy for ordinary augmentation.
    assert (await r.get_api_key_and_headers(row())).ok


@pytest.mark.parametrize("action", ["unregister", "augmentation", "empty", "wrong-host", "refresh"])
async def test_delegated_requirement_is_sticky(tmp_path, action):
    r = await registry(tmp_path)
    r.register_provider("openai", ProviderConfigInput(api_key="fake", models={"m1": row()}))
    selected = r.find("openai", "m1")
    r.require_registration_route(registration_route_digest(selected))
    if action == "unregister":
        r.unregister_provider("openai")
    elif action == "augmentation":
        r.register_provider("openai", ProviderConfigInput(api_key="new-fake"))
    elif action == "empty":
        r.register_provider("openai", ProviderConfigInput(models={}))
    elif action == "wrong-host":
        r.register_provider(
            "openai", ProviderConfigInput(models={"m1": row(base="http://127.0.0.1:36502/v1")})
        )
    else:
        r.reset()
        r.refresh()
    auth = await r.get_api_key_and_headers(selected)
    assert auth.ok == (action == "refresh")
    if not auth.ok:
        assert auth.api_key is None and not auth.headers and auth.retry_reason is None
