"""#363 / ADR-0251 — the launch model is the model ``/model`` composes.

A static catalog hit at launch now carries everything ``models.json`` composes
onto a built-in: the provider ``baseUrl`` and ``compat`` (``merge_compat``) and
the ``modelOverrides`` entry - the same function builds ``/model``'s registry
copy (``models_json.compose_built_in_model``). The catalog ``api`` stays
(ADR-0249 decision 3), and an exported ``OPENROUTER_BASE_URL`` still beats
``providers.openrouter.baseUrl``. Before #363 only the host moved (#344), so a
gateway that needs ``supportsDeveloperRole: false`` worked from ``/model`` and
not at launch, and ``modelOverrides`` on any built-in were ignored at launch.

Real :class:`ModelRegistry` over a ``tmp_path`` models.json; fake keys; no socket.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import resolve_model, resolve_route
from aelix_coding_agent.model_registry import ModelRegistry

_CORP = "http://127.0.0.1:9/corp-gw/v1"

_PROVIDERS: dict[str, Any] = {
    "openai": {
        "baseUrl": _CORP,
        "apiKey": "corp-gateway-fake",
        "compat": {"supportsDeveloperRole": False},
        "modelOverrides": {"gpt-4o-mini": {"contextWindow": 1234}},
    },
    # Not re-pointed: modelOverrides alone.
    "anthropic": {
        "modelOverrides": {"claude-haiku-4-5": {"name": "Haiku (mine)", "contextWindow": 4321}}
    },
}


@pytest.fixture
def scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith("OPENROUTER_"):
            monkeypatch.delenv(name)
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    return agent


async def _registry(agent: Path, providers: dict[str, Any]) -> ModelRegistry:
    (agent / "models.json").write_text(json.dumps({"providers": providers}), encoding="utf-8")
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    return registry


@pytest.mark.parametrize(
    ("model_flag", "provider_flag"),
    [("openai/gpt-4o-mini", None), ("gpt-4o-mini", "openai")],
    ids=["prefix", "named-provider"],
)
async def test_a_re_pointed_built_in_launches_with_compat_and_overrides(
    scrubbed: Path, model_flag: str, provider_flag: str | None
) -> None:
    """RED on 5dee21d1: ``compat=None`` and ``context_window=128000`` at launch."""

    registry = await _registry(scrubbed, _PROVIDERS)
    model = resolve_model(model_flag, provider_flag, registry)
    assert (model.provider, model.id, model.base_url) == ("openai", "gpt-4o-mini", _CORP)
    assert model.api == "openai-responses"  # the catalog's
    assert model.compat == {"supportsDeveloperRole": False}
    assert model.context_window == 1234
    assert model == registry.find("openai", "gpt-4o-mini")  # /model's copy, field by field


async def test_model_overrides_apply_at_launch_on_a_provider_nobody_re_pointed(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED on 5dee21d1: ``context_window=200000``, the catalog name."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    registry = await _registry(scrubbed, _PROVIDERS)
    model = resolve_model("anthropic/claude-haiku-4-5", None, registry)
    assert (model.provider, model.base_url) == ("anthropic", "https://api.anthropic.com")
    assert (model.context_window, model.name) == (4321, "Haiku (mine)")
    assert model == registry.find("anthropic", "claude-haiku-4-5")
    assert "anthropic" not in registry.get_user_defined_providers()


async def test_a_bare_id_route_is_composed_too(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A route found in the registry's universe goes through ``_launch_shape``."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    registry = await _registry(scrubbed, _PROVIDERS)
    route = resolve_route("claude-haiku-4-5", None, registry)
    assert route.model.provider == "anthropic"
    assert route.model.context_window == 4321


async def test_the_catalog_api_is_still_pinned(scrubbed: Path) -> None:
    """A models.json ``models`` entry redefining a catalog id with another api.

    ``/model``'s copy is the custom one; the launch keeps the catalog's ``api``
    (``test_resolve_model_catalog_hit_wins_over_registry``, ADR-0249 decision 3).
    """

    providers = {
        "openai": {
            "baseUrl": _CORP,
            "apiKey": "corp-gateway-fake",
            "compat": {"supportsDeveloperRole": False},
            "modelOverrides": {"gpt-4o-mini": {"contextWindow": 1234}},
            "models": [{"id": "gpt-4o-mini", "api": "openai-completions"}],
        }
    }
    registry = await _registry(scrubbed, providers)
    found = registry.find("openai", "gpt-4o-mini")
    assert found is not None and found.api == "openai-completions"
    model = resolve_model("openai/gpt-4o-mini", None, registry)
    assert model.api == "openai-responses"
    assert model.base_url == _CORP
    # Not the registry copy (another api): the catalog entry, composed - not the
    # host-only adoption (review round 1: both survived every row before).
    assert model.compat == {"supportsDeveloperRole": False}
    assert model.context_window == 1234


async def test_openrouter_base_url_from_the_env_still_wins(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = {
        "openrouter": {
            "baseUrl": "http://127.0.0.1:9/models-json-or",
            "modelOverrides": {"openai/gpt-4o-mini": {"contextWindow": 999}},
        }
    }
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9/or")
    registry = await _registry(scrubbed, providers)
    model = resolve_model("openrouter/openai/gpt-4o-mini", None, registry)
    assert (model.provider, model.id) == ("openrouter", "openai/gpt-4o-mini")
    assert model.base_url == "http://127.0.0.1:9/or"
    assert model.context_window == 999


class _HostOnlyRegistry:
    """A duck-typed registry from before #363: ``get_base_url_override`` only."""

    def find(self, provider: str, model_id: str) -> Model | None:
        return None

    def get_all(self) -> list[Model]:
        return []

    def get_base_url_override(self, provider: str) -> str | None:
        return _CORP if provider == "openai" else None


class _RaisingRegistry(_HostOnlyRegistry):
    def compose_built_in(self, model: Model) -> Model:
        raise RuntimeError("compose failed (test)")


class _ApiChangingRegistry(_HostOnlyRegistry):
    def compose_built_in(self, model: Model) -> Model:
        return replace(model, api="openai-completions", base_url=_CORP)


def test_a_registry_without_compose_keeps_host_only_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    model = resolve_model("gpt-4o-mini", "openai", _HostOnlyRegistry())
    assert (model.base_url, model.api, model.compat) == (_CORP, "openai-responses", None)


@pytest.mark.parametrize(
    "registry", [_RaisingRegistry(), _ApiChangingRegistry()], ids=["raises", "changes-api"]
)
def test_a_failing_or_api_changing_compose_falls_back_to_host_only(
    monkeypatch: pytest.MonkeyPatch, registry: Any
) -> None:
    """Never back to the vendor's host: the #344 adoption still applies."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    model = resolve_model("gpt-4o-mini", "openai", registry)
    assert (model.base_url, model.api, model.compat) == (_CORP, "openai-responses", None)


# ── Review round 1 (R3, R4) ─────────────────────────────────────────────────


async def test_provider_compat_merges_into_the_catalog_compat(scrubbed: Path) -> None:
    """``merge_compat``, not a replacement (review round 1, R3/V13).

    ``github-copilot/gpt-4o``'s catalog compat carries ``supportsStore: False``
    and ``supportsReasoningEffort: False``; a provider compat naming only
    ``supportsDeveloperRole`` keeps both, at launch and in ``/model``. A compose
    that replaced the compat passed the whole suite on 4cbff14e (the only
    launch-compat row had a catalog compat of None).
    """

    expected = {
        "supportsStore": False,
        "supportsDeveloperRole": False,
        "supportsReasoningEffort": False,
    }
    registry = await _registry(
        scrubbed, {"github-copilot": {"compat": {"supportsDeveloperRole": False}}}
    )
    copy = registry.find("github-copilot", "gpt-4o")
    assert copy is not None and copy.compat == expected
    model = resolve_model("github-copilot/gpt-4o", None, registry)
    assert model.compat == expected
    assert model == copy


async def test_every_overridden_field_reaches_the_launch(scrubbed: Path) -> None:
    """Codex review round 1, cat 4: a launch that kept the catalog cost and
    reasoning passed every #363 row on 4cbff14e. Each overridden field is asserted.
    """

    from aelix_ai.streaming import ModelCost

    providers = {
        "openai": {
            "modelOverrides": {
                "gpt-5-mini": {
                    "reasoning": False,
                    "cost": {"input": 99, "output": 98, "cacheRead": 97, "cacheWrite": 96},
                    "name": "Mine",
                    "contextWindow": 4321,
                }
            }
        }
    }
    registry = await _registry(scrubbed, providers)
    model = resolve_model("openai/gpt-5-mini", None, registry)
    assert model.reasoning is False
    assert model.cost == ModelCost(input=99, output=98, cache_read=97, cache_write=96)
    assert model.name == "Mine"
    assert model.context_window == 4321
    assert model.api == "openai-responses"
    assert model == registry.find("openai", "gpt-5-mini")


async def test_an_oauth_modify_models_change_reaches_the_launch(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review round 1, cat 2 (R4): the launch is ``/model``'s registry copy.

    An OAuth provider's ``modify_models`` runs after the models.json composition
    in ``_load_models``. RED on 4cbff14e (and 5dee21d1): the launch had the
    catalog name, window, compat and headers while ``/model`` had the hook's.
    """

    from types import SimpleNamespace

    from aelix_ai.oauth import register_oauth_provider, unregister_oauth_provider

    def _modify(models: list[Model], _creds: Any) -> list[Model]:
        return [
            replace(
                m,
                name="OAuth account model",
                context_window=8765,
                compat={"supportsDeveloperRole": False},
                headers={"x-oauth-model": "h"},
            )
            if (m.provider, m.id) == ("openai", "gpt-4o-mini")
            else m
            for m in models
        ]

    register_oauth_provider(
        SimpleNamespace(  # type: ignore[arg-type]
            id="openai", name="Probe OAuth", modify_models=_modify, get_api_key=lambda c: c.access
        )
    )
    try:
        (scrubbed / "models.json").write_text(
            json.dumps({"providers": {"openai": {"baseUrl": _CORP}}}), encoding="utf-8"
        )
        (scrubbed / "auth.json").write_text(
            json.dumps(
                {
                    "openai": {
                        "type": "oauth",
                        "refresh": "r",
                        "access": "a",
                        "expires": 9999999999999,
                    }
                }
            ),
            encoding="utf-8",
        )
        storage = AuthStorage(scrubbed / "auth.json")
        await storage.load()
        registry = ModelRegistry.create(storage, str(scrubbed / "models.json"))
        copy = registry.find("openai", "gpt-4o-mini")
        assert copy is not None and copy.name == "OAuth account model"
        for flag, provider in (("openai/gpt-4o-mini", None), ("gpt-4o-mini", "openai")):
            model = resolve_model(flag, provider, registry)
            assert model == copy
            assert (model.name, model.context_window, model.base_url) == (
                "OAuth account model",
                8765,
                _CORP,
            )
            assert model.compat == {"supportsDeveloperRole": False}
            assert model.headers == {"x-oauth-model": "h"}
    finally:
        unregister_oauth_provider("openai")


# ── Review round 2: the launch EQUALS /model's registry copy ───────────────

# Built-ins with a provider compat (+ a baseUrl for some) and a modelOverrides
# entry that moves every field an override can move. The override values are
# derived from the catalog so each one differs from it.
_EQUALITY_CASES = [
    ("openai", "gpt-5-mini", {"baseUrl": _CORP, "compat": {"supportsDeveloperRole": False}}),
    ("anthropic", "claude-haiku-4-5", {"compat": {"probeFlag": True}}),
    ("github-copilot", "gpt-4o", {"compat": {"supportsDeveloperRole": True}}),
    ("openrouter", "openai/gpt-4o-mini", {"baseUrl": "http://127.0.0.1:9/or-gw/v1"}),
    ("google", "gemini-2.5-flash", {"compat": {"probeFlag": True}}),
]
_OVERRIDDEN = {
    "name",
    "reasoning",
    "input",
    "cost",
    "context_window",
    "max_tokens",
    "thinking_level_map",
    "compat",
}


def _every_field_override(catalog: Model) -> dict[str, Any]:
    return {
        "name": f"{catalog.name} (mine)",
        "reasoning": not catalog.reasoning,
        "input": ["text"] if catalog.input != ["text"] else ["text", "image"],
        "contextWindow": catalog.context_window + 7,
        "maxTokens": catalog.max_tokens + 5,
        "cost": {"input": 91, "output": 92, "cacheRead": 93, "cacheWrite": 94},
        "thinkingLevelMap": {"minimal": "low"},
        "compat": {"probeOverride": 1},
    }


@pytest.mark.parametrize(
    ("provider", "model_id", "provider_entry"),
    _EQUALITY_CASES,
    ids=[c[0] for c in _EQUALITY_CASES],
)
async def test_the_launch_is_the_registry_copy_on_every_field(
    scrubbed: Path, provider: str, model_id: str, provider_entry: dict[str, Any]
) -> None:
    """Review round 2 (Codex pass 2 C5; round 1's cost/reasoning mutant).

    Dataclass equality, not a field list: a launch that put back ONE catalog field
    after composing (Codex: ``replace(composed, max_tokens=model.max_tokens)``)
    passed every changed test on a79861ce. The registry copy must differ from the
    catalog on every overridden field, so a mutant restoring any of them is red.
    """

    from aelix_ai.models import get_model

    catalog = get_model(provider, model_id)
    assert catalog is not None
    entry = {**provider_entry, "modelOverrides": {model_id: _every_field_override(catalog)}}
    registry = await _registry(scrubbed, {provider: entry})
    copy = registry.find(provider, model_id)
    assert copy is not None
    moved = {f.name for f in fields(Model) if getattr(copy, f.name) != getattr(catalog, f.name)}
    assert moved >= _OVERRIDDEN
    assert ("base_url" in moved) == ("baseUrl" in provider_entry)
    for flag, provider_flag in ((f"{provider}/{model_id}", None), (model_id, provider)):
        model = resolve_model(flag, provider_flag, registry)
        assert model == copy, {
            f.name: (getattr(model, f.name), getattr(copy, f.name))
            for f in fields(Model)
            if getattr(model, f.name) != getattr(copy, f.name)
        }


async def test_an_oauth_hook_header_is_part_of_the_equality(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Model.headers`` moves only through an OAuth ``modify_models``; equality covers it."""

    from types import SimpleNamespace

    from aelix_ai.models import get_model
    from aelix_ai.oauth import register_oauth_provider, unregister_oauth_provider

    catalog = get_model("openai", "gpt-5-mini")
    assert catalog is not None

    def _modify(models: list[Model], _creds: Any) -> list[Model]:
        return [
            replace(m, headers={"x-oauth-model": "h"}) if m.id == "gpt-5-mini" else m
            for m in models
        ]

    register_oauth_provider(
        SimpleNamespace(  # type: ignore[arg-type]
            id="openai", name="Probe OAuth", modify_models=_modify, get_api_key=lambda c: c.access
        )
    )
    try:
        (scrubbed / "models.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "openai": {
                            "baseUrl": _CORP,
                            "modelOverrides": {"gpt-5-mini": _every_field_override(catalog)},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (scrubbed / "auth.json").write_text(
            json.dumps(
                {
                    "openai": {
                        "type": "oauth",
                        "refresh": "r",
                        "access": "a",
                        "expires": 9999999999999,
                    }
                }
            ),
            encoding="utf-8",
        )
        storage = AuthStorage(scrubbed / "auth.json")
        await storage.load()
        registry = ModelRegistry.create(storage, str(scrubbed / "models.json"))
        copy = registry.find("openai", "gpt-5-mini")
        assert copy is not None and copy.headers == {"x-oauth-model": "h"}
        assert copy.max_tokens == catalog.max_tokens + 5
        assert resolve_model("openai/gpt-5-mini", None, registry) == copy
    finally:
        unregister_oauth_provider("openai")


async def test_openrouter_base_url_reaches_the_launch_and_the_model_copy_alike(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0251 §2.7 as amended by §11 (#375): an exported ``OPENROUTER_BASE_URL``
    beats ``providers.openrouter.baseUrl`` on the launch AND on ``/model``'s registry
    copy, so the two are equal, field for field. Before #375 the copy kept the
    ``models.json`` host (Codex pass 2 C3) and this row pinned that difference.
    """

    from aelix_ai.models import get_model

    catalog = get_model("openrouter", "openai/gpt-4o-mini")
    assert catalog is not None
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9/or-env/v1")
    entry = {
        "baseUrl": "http://127.0.0.1:9/or-gw/v1",
        "modelOverrides": {"openai/gpt-4o-mini": _every_field_override(catalog)},
    }
    registry = await _registry(scrubbed, {"openrouter": entry})
    copy = registry.find("openrouter", "openai/gpt-4o-mini")
    assert copy is not None and copy.base_url == "http://127.0.0.1:9/or-env/v1"
    model = resolve_model("openrouter/openai/gpt-4o-mini", None, registry)
    assert model == copy
    monkeypatch.delenv("OPENROUTER_BASE_URL")
    unset = registry.find("openrouter", "openai/gpt-4o-mini")
    assert unset is not None and unset.base_url == "http://127.0.0.1:9/or-gw/v1"
    assert resolve_model("openrouter/openai/gpt-4o-mini", None, registry) == unset


async def test_the_composition_moves_no_late_or_not_found_decision_and_keeps_the_typed_key(
    scrubbed: Path,
) -> None:
    """Rebase onto 547099f3: #367's late re-resolve, #370's not-found, ``--api-key``.

    #367's :class:`LateRoute` re-resolves the launch inputs and builds its hold from
    the resolved model's ``(id, provider)``; #370 ends a string no provider places
    under ``--api-key`` on a not-found placeholder; the launch then attaches the
    typed key to the launch model's provider. The composition keeps ``provider``,
    ``id`` and ``api`` (only the fields ``/model`` composes move), so each decision
    is the same with and without a ``models.json`` that composes the launch model,
    and the typed key is still step 1 of the key order on the composed model.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import (
        late_registered_route,
        user_defined_providers,
    )
    from aelix_coding_agent.model_registry import ProviderConfigInput

    answers = []
    for providers in ({}, _PROVIDERS):
        registry = await _registry(scrubbed, providers)
        launch = user_defined_providers(registry)  # what the first build saw
        registry.register_provider("sessext", ProviderConfigInput(api_key="ext-fake"))
        route = resolve_route("openai/gpt-4o-mini", None, registry)
        not_found = resolve_route("newlab/model-x", None, registry, typed_key=True)
        registry._auth_storage.set_runtime_api_key("openai", "typed-fake")
        auth = await registry.get_api_key_and_headers(route.model)
        answers.append(
            (
                (route.model.provider, route.model.id, route.model.api, route.error),
                late_registered_route(
                    "openai/gpt-4o-mini", None, registry, launch_providers=launch
                ),
                late_registered_route("sessext/m1", None, registry, launch_providers=launch),
                (not_found.model.provider, not_found.model.id, not_found.model.api),
                str(not_found.error),
                (auth.ok, auth.api_key),
            )
        )
        if providers:
            assert route.model.context_window == 1234  # composed, not the catalog's 128000
    assert answers[0] == answers[1]
    plain = answers[0]
    assert plain[1] is None and plain[2] is not None and "names provider 'sessext'" in plain[2]
    assert plain[3] == ("newlab", "model-x", "unknown") and "not found" in plain[4]
    assert plain[5] == (True, "typed-fake")
