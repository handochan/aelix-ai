"""``models.json`` custom-model loader tests — P0 #4 (ADR-0140).

Pi parity: ``packages/coding-agent/src/core/model-registry.ts`` (SHA
``734e08e``). Covers :mod:`aelix_coding_agent.models_json` (pure helpers)
plus the :class:`ModelRegistry` integration (custom models, overrides,
auth indirection, ``getProviderAuthStatus`` sources).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model, ModelCost
from aelix_coding_agent.model_registry import ModelRegistry
from aelix_coding_agent.models_json import (
    apply_model_override,
    load_custom_models,
    merge_compat,
    merge_custom_models,
    parse_models,
    strip_json_comments,
    validate_config_semantics,
    validate_models_config,
)

# === Fixtures / helpers ========================================================


async def _ready_storage(tmp_path: Path) -> AuthStorage:
    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    return s


def _cost() -> dict[str, float]:
    return {"input": 1.0, "output": 2.0, "cacheRead": 0.5, "cacheWrite": 0.25}


def _model_def(model_id: str = "m1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": model_id,
        "reasoning": False,
        "input": ["text"],
        "cost": _cost(),
        "contextWindow": 1000,
        "maxTokens": 100,
    }
    base.update(kw)
    return base


def _write_models_json(tmp_path: Path, config: dict[str, Any] | str) -> str:
    path = tmp_path / "models.json"
    content = config if isinstance(config, str) else json.dumps(config)
    path.write_text(content, encoding="utf-8")
    return str(path)


async def _registry_with(tmp_path: Path, config: dict[str, Any] | str) -> ModelRegistry:
    s = await _ready_storage(tmp_path)
    return ModelRegistry(s, models_json_path=_write_models_json(tmp_path, config))


def _no_store(*_args: Any, **_kwargs: Any) -> None:
    """No-op callback for direct :func:`load_custom_models` unit tests."""


# === strip_json_comments =======================================================


def test_strip_line_comment() -> None:
    out = strip_json_comments('{\n  "a": 1 // trailing comment\n}')
    assert json.loads(out) == {"a": 1}


def test_strip_full_line_comment() -> None:
    out = strip_json_comments('{\n  // a comment\n  "a": 1\n}')
    assert json.loads(out) == {"a": 1}


def test_strip_trailing_comma_object() -> None:
    assert json.loads(strip_json_comments('{"a": 1,}')) == {"a": 1}


def test_strip_trailing_comma_array() -> None:
    assert json.loads(strip_json_comments('{"a": [1, 2,]}')) == {"a": [1, 2]}


def test_strip_preserves_double_slash_inside_string() -> None:
    # A ``//`` inside a JSON string is NOT a comment.
    out = strip_json_comments('{"url": "https://x.test//path"}')
    assert json.loads(out) == {"url": "https://x.test//path"}


def test_strip_preserves_comma_bracket_inside_string() -> None:
    out = strip_json_comments('{"a": "x,]y"}')
    assert json.loads(out) == {"a": "x,]y"}


# === Schema validation =========================================================


def test_schema_missing_providers() -> None:
    assert validate_models_config({}) == [("providers", "Expected required property")]


def test_schema_providers_wrong_type() -> None:
    assert validate_models_config({"providers": "nope"}) == [
        ("providers", "Expected object")
    ]


def test_schema_root_not_object() -> None:
    assert validate_models_config("nope") == [("root", "Expected object")]


def test_schema_model_missing_id() -> None:
    errors = validate_models_config({"providers": {"p": {"models": [{}]}}})
    assert ("providers.p.models.0.id", "Expected required property") in errors


def test_schema_contextwindow_wrong_type() -> None:
    errors = validate_models_config(
        {"providers": {"p": {"models": [{"id": "m", "contextWindow": "big"}]}}}
    )
    assert ("providers.p.models.0.contextWindow", "Expected number") in errors


def test_schema_cost_missing_required_fields() -> None:
    errors = validate_models_config(
        {"providers": {"p": {"models": [{"id": "m", "cost": {"input": 1}}]}}}
    )
    paths = {p for p, _ in errors}
    assert "providers.p.models.0.cost.output" in paths
    assert "providers.p.models.0.cost.cacheRead" in paths
    assert "providers.p.models.0.cost.cacheWrite" in paths


def test_schema_empty_id_string_rejected() -> None:
    errors = validate_models_config({"providers": {"p": {"models": [{"id": ""}]}}})
    assert ("providers.p.models.0.id", "Expected string length >= 1") in errors


def test_schema_input_literal_rejected() -> None:
    errors = validate_models_config(
        {"providers": {"p": {"models": [{"id": "m", "input": ["text", "audio"]}]}}}
    )
    assert ("providers.p.models.0.input.1", "Expected 'text' | 'image'") in errors


def test_schema_thinking_level_map_accepts_str_and_null() -> None:
    errors = validate_models_config(
        {
            "providers": {
                "p": {
                    "models": [
                        {"id": "m", "thinkingLevelMap": {"low": "x", "high": None}}
                    ]
                }
            }
        }
    )
    assert errors == []


def test_schema_model_override_cost_optional() -> None:
    # In an override, cost sub-fields are optional (unlike a definition).
    errors = validate_models_config(
        {"providers": {"anthropic": {"modelOverrides": {"m": {"cost": {"input": 1}}}}}}
    )
    assert errors == []


def test_schema_unknown_keys_ignored() -> None:
    # Pi's Type.Object permits extra keys.
    errors = validate_models_config(
        {"providers": {"p": {"models": [{"id": "m"}], "futureField": 1}}}
    )
    assert errors == []


def test_schema_valid_full_config() -> None:
    config = {
        "providers": {
            "myco": {
                "baseUrl": "https://api.myco.test/v1",
                "apiKey": "MYCO_KEY",
                "api": "openai-completions",
                "models": [_model_def("large", input=["text", "image"])],
            }
        }
    }
    assert validate_models_config(config) == []


# === Semantic validation =======================================================


def test_semantic_custom_provider_requires_baseurl() -> None:
    config = {"providers": {"myco": {"apiKey": "K", "models": [_model_def(api="x")]}}}
    with pytest.raises(ValueError, match='"baseUrl" is required'):
        validate_config_semantics(config)


def test_semantic_custom_provider_apikey_optional_default() -> None:
    # Pi #5953: an apiKey-less custom provider no longer errors by default —
    # auth may resolve from auth.json / OAuth / --api-key / env at request time.
    config = {
        "providers": {
            "myco": {"baseUrl": "https://x/v1", "models": [_model_def(api="x")]}
        }
    }
    validate_config_semantics(config)  # no resolver → no raise (Pi default)


def test_semantic_custom_provider_apikey_ok_with_stored_auth() -> None:
    # apiKey absent but the credential-aware resolver reports a stored
    # credential for the provider → no error (Pi #5953).
    config = {
        "providers": {
            "myco": {"baseUrl": "https://x/v1", "models": [_model_def(api="x")]}
        }
    }
    validate_config_semantics(
        config, provider_has_stored_auth=lambda provider: provider == "myco"
    )


def test_semantic_custom_provider_apikey_required_when_no_auth_path() -> None:
    # apiKey absent AND the resolver confirms no stored credential → error
    # (the "truly no auth path" case is the only one that still raises).
    config = {
        "providers": {
            "myco": {"baseUrl": "https://x/v1", "models": [_model_def(api="x")]}
        }
    }
    with pytest.raises(ValueError, match='"apiKey" is required'):
        validate_config_semantics(
            config, provider_has_stored_auth=lambda provider: False
        )


def test_semantic_empty_provider_must_specify_something() -> None:
    config = {"providers": {"myco": {}}}
    with pytest.raises(ValueError, match="must specify"):
        validate_config_semantics(config)


def test_semantic_invalid_context_window() -> None:
    config = {
        "providers": {
            "anthropic": {"models": [_model_def(api="x", contextWindow=0)]}
        }
    }
    with pytest.raises(ValueError, match="invalid contextWindow"):
        validate_config_semantics(config)


def test_semantic_builtin_provider_override_only_ok() -> None:
    # Built-in provider, no models, baseUrl override only — valid.
    validate_config_semantics({"providers": {"anthropic": {"baseUrl": "https://p/v1"}}})


# === merge_compat / apply_model_override / merge_custom_models ==================


def test_merge_compat_none_override_returns_base() -> None:
    base = {"supportsStore": True}
    assert merge_compat(base, None) is base


def test_merge_compat_shallow_and_nested_routing() -> None:
    base = {
        "supportsStore": True,
        "openRouterRouting": {"sort": "price", "only": ["a"]},
    }
    override = {
        "supportsStore": False,
        "openRouterRouting": {"only": ["b"], "ignore": ["c"]},
    }
    merged = merge_compat(base, override)
    assert merged["supportsStore"] is False
    # Nested routing is deep-merged (override wins on key collisions).
    assert merged["openRouterRouting"] == {
        "sort": "price",
        "only": ["b"],
        "ignore": ["c"],
    }


def _model(**kw: Any) -> Model:
    base: dict[str, Any] = {
        "id": "m",
        "name": "M",
        "api": "openai-completions",
        "provider": "p",
        "base_url": "https://x/v1",
        "reasoning": False,
        "input": ["text"],
        "cost": ModelCost(input=1, output=2, cache_read=3, cache_write=4),
        "context_window": 1000,
        "max_tokens": 100,
    }
    base.update(kw)
    return Model(**base)


def test_apply_model_override_fields() -> None:
    model = _model()
    out = apply_model_override(
        model,
        {"name": "New", "maxTokens": 555, "reasoning": True, "cost": {"input": 9}},
    )
    assert out.name == "New"
    assert out.max_tokens == 555
    assert out.reasoning is True
    # cost: overridden input, others fall back to the base model.
    assert out.cost.input == 9
    assert out.cost.output == 2
    # untouched field preserved
    assert out.context_window == 1000


def test_apply_model_override_thinking_level_map_merges() -> None:
    model = _model(thinking_level_map={"low": "a", "high": "b"})
    out = apply_model_override(model, {"thinkingLevelMap": {"high": "c", "xhigh": "d"}})
    assert out.thinking_level_map == {"low": "a", "high": "c", "xhigh": "d"}


def test_merge_custom_models_custom_wins_on_conflict() -> None:
    built_in = [_model(provider="p", id="a"), _model(provider="p", id="b")]
    custom = [_model(provider="p", id="a", name="OVERRIDDEN")]
    merged = merge_custom_models(built_in, custom)
    assert len(merged) == 2
    a = next(m for m in merged if m.id == "a")
    assert a.name == "OVERRIDDEN"


def test_merge_custom_models_appends_new() -> None:
    built_in = [_model(provider="p", id="a")]
    custom = [_model(provider="q", id="z")]
    merged = merge_custom_models(built_in, custom)
    assert {(m.provider, m.id) for m in merged} == {("p", "a"), ("q", "z")}


# === load_custom_models (error paths) ==========================================


def test_load_nonexistent_path_returns_empty(tmp_path: Path) -> None:
    result = load_custom_models(
        str(tmp_path / "nope.json"),
        store_provider_request_config=_no_store,
        store_model_headers=_no_store,
    )
    assert result.models == []
    assert result.error is None


def test_load_invalid_json_reports_parse_error(tmp_path: Path) -> None:
    path = _write_models_json(tmp_path, "{not json")
    result = load_custom_models(
        path, store_provider_request_config=_no_store, store_model_headers=_no_store
    )
    assert result.error is not None
    assert "Failed to parse models.json" in result.error


def test_load_schema_error_reports_invalid_schema(tmp_path: Path) -> None:
    path = _write_models_json(tmp_path, {"providers": {"p": {"models": [{}]}}})
    result = load_custom_models(
        path, store_provider_request_config=_no_store, store_model_headers=_no_store
    )
    assert result.error is not None
    assert "Invalid models.json schema" in result.error
    assert "providers.p.models.0.id" in result.error


def test_load_semantic_error_reports_load_failure(tmp_path: Path) -> None:
    path = _write_models_json(
        tmp_path, {"providers": {"myco": {"models": [_model_def(api="x")]}}}
    )
    result = load_custom_models(
        path, store_provider_request_config=_no_store, store_model_headers=_no_store
    )
    assert result.error is not None
    assert "Failed to load models.json" in result.error


def test_load_apikeyless_custom_provider_is_pi_faithful(tmp_path: Path) -> None:
    # Review LOW (B2): the production caller deliberately does NOT pass
    # ``provider_has_stored_auth`` — Pi's ``validateConfig`` has no apiKey/stored-
    # auth check (coding-agent/src/core/model-registry.ts:531), so a custom
    # provider with baseUrl + models but NO apiKey and NO stored credentials must
    # LOAD WITHOUT ERROR (missing-auth surfaces later, never aborting the file).
    path = _write_models_json(
        tmp_path,
        {
            "providers": {
                "myco": {
                    "baseUrl": "https://myco.test/v1",
                    "models": [_model_def(api="openai-completions")],
                }
            }
        },
    )
    result = load_custom_models(
        path, store_provider_request_config=_no_store, store_model_headers=_no_store
    )
    assert result.error is None  # no friendly-error abort (Pi parity)
    assert [m.id for m in result.models] == ["m1"]  # model still loads


# === ModelRegistry integration =================================================


async def test_custom_model_appears_in_catalog(tmp_path: Path) -> None:
    config = {
        "providers": {
            "myco": {
                "baseUrl": "https://api.myco.test/v1",
                "apiKey": "MYCO_KEY",
                "api": "openai-completions",
                "models": [_model_def("large", name="MyCo Large", reasoning=True)],
            }
        }
    }
    r = await _registry_with(tmp_path, config)
    assert r.get_error() is None
    model = r.find("myco", "large")
    assert model is not None
    assert model.name == "MyCo Large"
    assert model.base_url == "https://api.myco.test/v1"
    assert model.api == "openai-completions"
    assert model.reasoning is True
    # Built-ins still present.
    assert r.find("anthropic", "claude-sonnet-4-5") is not None


async def test_custom_model_uses_builtin_defaults_for_api(tmp_path: Path) -> None:
    # No api/baseUrl on the model or provider — falls back to the built-in
    # anthropic defaults (provider is a built-in).
    config = {
        "providers": {"anthropic": {"models": [_model_def("my-claude")]}}
    }
    r = await _registry_with(tmp_path, config)
    model = r.find("anthropic", "my-claude")
    assert model is not None
    builtin = r.find("anthropic", "claude-sonnet-4-5")
    assert builtin is not None
    assert model.api == builtin.api
    assert model.base_url == builtin.base_url


async def test_model_override_applied_to_builtin(tmp_path: Path) -> None:
    config = {
        "providers": {
            "anthropic": {
                "modelOverrides": {
                    "claude-sonnet-4-5": {"maxTokens": 99999, "name": "Custom Sonnet"}
                }
            }
        }
    }
    r = await _registry_with(tmp_path, config)
    model = r.find("anthropic", "claude-sonnet-4-5")
    assert model is not None
    assert model.max_tokens == 99999
    assert model.name == "Custom Sonnet"


async def test_provider_baseurl_override_applied_to_builtins(tmp_path: Path) -> None:
    config = {"providers": {"anthropic": {"baseUrl": "https://proxy.test/v1"}}}
    r = await _registry_with(tmp_path, config)
    model = r.find("anthropic", "claude-sonnet-4-5")
    assert model is not None
    assert model.base_url == "https://proxy.test/v1"


async def test_invalid_models_json_keeps_builtins_and_surfaces_error(
    tmp_path: Path,
) -> None:
    r = await _registry_with(tmp_path, "{not valid json")
    assert r.get_error() is not None
    assert "Failed to parse models.json" in r.get_error()
    # Built-ins still load despite the bad file.
    assert r.find("anthropic", "claude-sonnet-4-5") is not None


async def test_comments_and_trailing_commas_in_real_file(tmp_path: Path) -> None:
    raw = """{
  // my custom provider
  "providers": {
    "myco": {
      "baseUrl": "https://api.myco.test/v1",
      "apiKey": "MYCO_KEY",
      "api": "openai-completions",
      "models": [
        { "id": "large", "reasoning": false, "input": ["text"],
          "cost": {"input": 1, "output": 2, "cacheRead": 0, "cacheWrite": 0},
          "contextWindow": 1000, "maxTokens": 100, },
      ],
    },
  },
}"""
    r = await _registry_with(tmp_path, raw)
    assert r.get_error() is None
    assert r.find("myco", "large") is not None


# === Auth indirection ==========================================================


async def _myco_registry(tmp_path: Path, **provider_extra: Any) -> ModelRegistry:
    provider: dict[str, Any] = {
        "baseUrl": "https://api.myco.test/v1",
        "apiKey": "MYCO_KEY",
        "api": "openai-completions",
        "models": [_model_def("m1")],
    }
    provider.update(provider_extra)
    return await _registry_with(tmp_path, {"providers": {"myco": provider}})


async def test_apikey_literal_resolves(tmp_path: Path) -> None:
    # No env var named MYCO_KEY → literal value is the key (Pi behavior).
    r = await _myco_registry(tmp_path)
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.ok is True
    assert resolved.api_key == "MYCO_KEY"


async def test_apikey_env_var_indirection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYCO_API_KEY", "sk-from-env")
    r = await _myco_registry(tmp_path, apiKey="MYCO_API_KEY")
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.api_key == "sk-from-env"


async def test_apikey_command_indirection(tmp_path: Path) -> None:
    r = await _myco_registry(tmp_path, apiKey="!printf sk-from-cmd")
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.ok is True
    assert resolved.api_key == "sk-from-cmd"


async def test_auth_header_attaches_bearer(tmp_path: Path) -> None:
    r = await _myco_registry(tmp_path, apiKey="SECRET_LITERAL", authHeader=True)
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.ok is True
    assert resolved.headers.get("Authorization") == "Bearer SECRET_LITERAL"


async def test_provider_headers_merged(tmp_path: Path) -> None:
    r = await _myco_registry(tmp_path, headers={"X-Org": "org1"})
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.headers.get("X-Org") == "org1"


async def test_per_model_headers_merged(tmp_path: Path) -> None:
    config = {
        "providers": {
            "myco": {
                "baseUrl": "https://x/v1",
                "apiKey": "K",
                "api": "openai-completions",
                "models": [_model_def("m1", headers={"X-Model": "mv"})],
            }
        }
    }
    r = await _registry_with(tmp_path, config)
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.headers.get("X-Model") == "mv"


async def test_header_value_env_indirection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORG_HEADER_ENV", "org-secret")
    r = await _myco_registry(tmp_path, headers={"X-Org": "ORG_HEADER_ENV"})
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.headers.get("X-Org") == "org-secret"


async def test_auth_header_without_key_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``authHeader`` set but NO apiKey resolvable (built-in override with a
    # header, no anthropic credential) → ok=False "No API key found". (An
    # apiKey that *fails to resolve* throws "Failed to resolve" earlier —
    # see test_failed_command_reports_resolution_error.)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    config = {
        "providers": {"anthropic": {"authHeader": True, "headers": {"X-H": "v"}}}
    }
    r = await _registry_with(tmp_path, config)
    model = r.find("anthropic", "claude-sonnet-4-5")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.ok is False
    assert resolved.error is not None
    assert "No API key found" in resolved.error


async def test_failed_command_reports_resolution_error(tmp_path: Path) -> None:
    # A non-authHeader apiKey command that produces no output → the
    # resolve_or_throw path surfaces a "Failed to resolve" error.
    r = await _myco_registry(tmp_path, apiKey="!false")
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.ok is False
    assert resolved.error is not None
    assert "Failed to resolve" in resolved.error


# === has_configured_auth / get_api_key_for_provider / display name =============


async def test_has_configured_auth_via_models_json(tmp_path: Path) -> None:
    r = await _myco_registry(tmp_path)
    model = r.find("myco", "m1")
    assert model is not None
    assert r.has_configured_auth(model) is True
    # And the custom model is therefore "available".
    assert any(m.id == "m1" and m.provider == "myco" for m in r.get_available())


async def test_get_api_key_for_provider_via_models_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYCO_API_KEY", "sk-xyz")
    r = await _myco_registry(tmp_path, apiKey="MYCO_API_KEY")
    assert await r.get_api_key_for_provider("myco") == "sk-xyz"


async def test_display_name_from_provider_name(tmp_path: Path) -> None:
    # ``name`` flows to display via a dynamically-registered provider; the
    # models.json provider ``name`` is not a display source in Pi, so this
    # exercises the register_provider path.
    from aelix_coding_agent.model_registry import ProviderConfigInput

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    r.register_provider("myco", ProviderConfigInput(name="My Company"))
    assert r.get_provider_display_name("myco") == "My Company"


# === get_provider_auth_status sources ==========================================


async def test_auth_status_models_json_command(tmp_path: Path) -> None:
    r = await _myco_registry(tmp_path, apiKey="!echo hi")
    status = await r.get_provider_auth_status("myco")
    assert status.configured is True
    assert status.source == "models_json_command"


async def test_auth_status_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYCO_ENV_KEY", "secret")
    r = await _myco_registry(tmp_path, apiKey="MYCO_ENV_KEY")
    status = await r.get_provider_auth_status("myco")
    assert status.configured is True
    assert status.source == "environment"
    assert status.label == "MYCO_ENV_KEY"


async def test_auth_status_models_json_key(tmp_path: Path) -> None:
    # A literal key that is neither a ``!command`` nor a set env var.
    r = await _myco_registry(tmp_path, apiKey="literal-not-an-env-name-xyz")
    status = await r.get_provider_auth_status("myco")
    assert status.configured is True
    assert status.source == "models_json_key"


# === refresh / reload ==========================================================


async def test_refresh_rebuilds_request_configs(tmp_path: Path) -> None:
    # First load: a custom provider whose model carries a per-model header.
    first = {
        "providers": {
            "myco": {
                "baseUrl": "https://x/v1",
                "apiKey": "K",
                "api": "openai-completions",
                "models": [_model_def("m1", headers={"X-Model": "mv"})],
            }
        }
    }
    r = await _registry_with(tmp_path, first)
    model = r.find("myco", "m1")
    assert model is not None
    assert r.has_configured_auth(model) is True
    # The per-model header map carries it pre-refresh.
    assert r._model_request_headers.get("myco:m1") == {"X-Model": "mv"}
    pre = await r.get_api_key_and_headers(model)
    assert pre.headers.get("X-Model") == "mv"

    # Overwrite the SAME path to drop myco + add an anthropic baseUrl
    # override, then refresh.
    _write_models_json(
        tmp_path, {"providers": {"anthropic": {"baseUrl": "https://p/v1"}}}
    )
    r.refresh()

    # The custom provider is gone AND its request-config maps were cleared.
    assert r.find("myco", "m1") is None
    assert "myco:m1" not in r._model_request_headers
    assert "myco" not in r._provider_request_configs
    # The NEW override actually took effect.
    anthropic = r.find("anthropic", "claude-sonnet-4-5")
    assert anthropic is not None
    assert anthropic.base_url == "https://p/v1"
    assert r.get_error() is None


# === Review hardening (ADR-0140 adversarial review) ============================


async def test_cascade_key_wins_over_models_json_apikey(tmp_path: Path) -> None:
    """The AuthStorage cascade key takes precedence; the models.json
    ``apiKey`` is only the fallback (Pi ``cascade ?? providerConfig.apiKey``).
    """

    s = await _ready_storage(tmp_path)
    s.set_runtime_api_key("myco", "from-cascade")
    config = {
        "providers": {
            "myco": {
                "baseUrl": "https://api.myco.test/v1",
                "apiKey": "from-models-json",
                "api": "openai-completions",
                "models": [_model_def("m1")],
            }
        }
    }
    r = ModelRegistry(s, models_json_path=_write_models_json(tmp_path, config))
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.ok is True
    assert resolved.api_key == "from-cascade"  # NOT "from-models-json"


async def test_per_model_headers_win_over_provider_on_collision(
    tmp_path: Path,
) -> None:
    # Header merge precedence: model.headers < provider < per-model. A
    # shared key resolves to the per-model value (last spread wins).
    config = {
        "providers": {
            "myco": {
                "baseUrl": "https://x/v1",
                "apiKey": "K",
                "api": "openai-completions",
                "headers": {"X-Shared": "provider"},
                "models": [_model_def("m1", headers={"X-Shared": "model"})],
            }
        }
    }
    r = await _registry_with(tmp_path, config)
    model = r.find("myco", "m1")
    assert model is not None
    resolved = await r.get_api_key_and_headers(model)
    assert resolved.headers.get("X-Shared") == "model"


def test_apply_model_override_input_context_compat_and_null_base_tlm() -> None:
    model = _model(compat={"supportsStore": True})
    out = apply_model_override(
        model,
        {
            "input": ["text", "image"],
            "contextWindow": 4096,
            "compat": {"supportsReasoningEffort": True},
        },
    )
    assert out.input == ["text", "image"]
    assert out.context_window == 4096
    # compat merged onto the base.
    assert out.compat == {"supportsStore": True, "supportsReasoningEffort": True}

    # Null-base thinkingLevelMap branch: ``model.thinking_level_map or {}``.
    base_none = _model(thinking_level_map=None)
    out2 = apply_model_override(base_none, {"thinkingLevelMap": {"high": "x"}})
    assert out2.thinking_level_map == {"high": "x"}


def test_semantic_invalid_max_tokens() -> None:
    config = {
        "providers": {"anthropic": {"models": [_model_def(api="x", maxTokens=0)]}}
    }
    with pytest.raises(ValueError, match="invalid maxTokens"):
        validate_config_semantics(config)


def test_semantic_no_api_specified_for_custom_provider() -> None:
    # Non-built-in provider with baseUrl+apiKey but a model lacking api at
    # both provider and model level.
    config = {
        "providers": {
            "myco": {
                "baseUrl": "https://x/v1",
                "apiKey": "K",
                "models": [{"id": "m1", "reasoning": False}],
            }
        }
    }
    with pytest.raises(ValueError, match='no "api" specified'):
        validate_config_semantics(config)


def test_schema_bool_is_not_a_number() -> None:
    # isinstance(True, int) is True in Python — the validator must still
    # reject a bool where a number is required.
    errors = validate_models_config(
        {"providers": {"p": {"models": [{"id": "m", "contextWindow": True}]}}}
    )
    assert ("providers.p.models.0.contextWindow", "Expected number") in errors


def test_schema_non_string_header_value_rejected() -> None:
    errors = validate_models_config({"providers": {"p": {"headers": {"X": 5}}}})
    assert ("providers.p.headers.X", "Expected string") in errors


def test_schema_authheader_non_bool_and_models_not_list() -> None:
    errors = validate_models_config(
        {"providers": {"p": {"authHeader": "yes", "models": {}}}}
    )
    paths = {p for p, _ in errors}
    assert "providers.p.authHeader" in paths
    assert "providers.p.models" in paths


def test_parse_models_skips_model_with_no_resolvable_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A built-in provider whose built-in list is empty → get_built_in_defaults
    # returns None; a model_def with no api at any level is skipped (Pi
    # ``if (!api) continue``).
    import aelix_coding_agent.models_json as mj

    real_get_models = mj.get_models

    def _fake_get_models(provider: str) -> list[Model]:
        if provider == "anthropic":
            return []  # force get_built_in_defaults -> None
        return real_get_models(provider)

    monkeypatch.setattr(mj, "get_models", _fake_get_models)
    config = {
        "providers": {
            "anthropic": {"models": [{"id": "m1", "reasoning": False}]}
        }
    }
    out = parse_models(config, _no_store)
    assert all(m.id != "m1" for m in out)


def test_parse_models_skips_model_with_no_resolvable_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # api resolvable (model-level) but baseUrl not → skipped.
    import aelix_coding_agent.models_json as mj

    real_get_models = mj.get_models

    def _fake_get_models(provider: str) -> list[Model]:
        if provider == "anthropic":
            return []
        return real_get_models(provider)

    monkeypatch.setattr(mj, "get_models", _fake_get_models)
    config = {
        "providers": {
            "anthropic": {
                "models": [{"id": "m1", "api": "openai-completions", "reasoning": False}]
            }
        }
    }
    out = parse_models(config, _no_store)
    assert all(m.id != "m1" for m in out)
