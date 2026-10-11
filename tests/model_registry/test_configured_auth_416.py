"""Configured presence shares effective request sources; it never resolves auth."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.entry import _make_prompt_check
from aelix_coding_agent.cli.runtime_bootstrap import restore_fallback_message, restore_session_route
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput

from tests.cli.test_configured_auth_416 import models
from tests.cli.test_configured_auth_416 import sandbox as sandbox


async def registry(agent: Path) -> tuple[AuthStorage, ModelRegistry]:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    return storage, ModelRegistry.create(storage, str(agent / "models.json"))


def candidate(r: ModelRegistry, api: str = "anthropic-messages") -> Model:
    m = r.find("anthropic", "claude-haiku-4-5")
    assert m is not None
    return replace(m, api=api)


@pytest.mark.parametrize(
    "api,names",
    [
        ("anthropic-messages", ["authorization", "x-api-key", "cf-aig-authorization"]),
        ("openai-responses", ["authorization", "cf-aig-authorization"]),
        ("openai-completions", []),
        ("google-generative-ai", []),
        ("openai-codex-responses", []),
    ],
)
@pytest.mark.parametrize("header", ["Authorization", "X-Api-Key", "CF-AIG-Authorization", "x-team"])
async def test_each_adapter_accepts_only_its_actual_header_contract(
    sandbox: Path, api: str, names: list[str], header: str
) -> None:
    _, r = await registry(sandbox)
    m = replace(candidate(r, api), headers={header: "fake416-header"})
    assert r.has_configured_auth(m) == (header.lower() in names)
    assert not r.has_configured_auth(replace(m, headers={header: " \t "}))


async def test_provider_and_per_model_projection_matches_request_and_restore(sandbox: Path) -> None:
    model_id = "claude-haiku-4-5"
    models(
        sandbox,
        {
            "anthropic": {
                "headers": {"Authorization": "Bearer fake416-provider"},
                "modelOverrides": {model_id: {"headers": {"Authorization": " "}}},
            }
        },
    )
    _, r = await registry(sandbox)
    selected = candidate(r)
    assert not r.has_configured_auth(selected)
    auth = await r.get_api_key_and_headers(selected)
    assert auth.ok and auth.headers["Authorization"] == " "
    assert restore_session_route((selected.provider, selected.id), r) is None
    sibling = r.find("anthropic", "claude-sonnet-4-5")
    assert sibling is not None and r.has_configured_auth(sibling)
    assert restore_session_route((sibling.provider, sibling.id), r) is not None


@pytest.mark.parametrize("source", ["provider", "model", "registration"])
async def test_header_only_saved_model_uses_its_actual_candidate(
    sandbox: Path, source: str
) -> None:
    model_id = "claude-haiku-4-5"
    config: dict[str, Any] = {"baseUrl": "https://restore416.invalid"}
    headers = {"x-api-key": "fake416-restore"}
    if source == "provider":
        config["headers"] = headers
    elif source == "model":
        config["modelOverrides"] = {model_id: {"headers": headers}}
    models(sandbox, {"anthropic": config})
    _, r = await registry(sandbox)
    if source == "registration":
        r.register_provider("anthropic", ProviderConfigInput(headers=headers))
    route = restore_session_route(("anthropic", model_id), r)
    assert route is not None
    assert (route.model.provider, route.model.id, route.model.base_url) == (
        "anthropic",
        model_id,
        "https://restore416.invalid",
    )
    assert r.has_configured_auth(route.model)
    assert f"Using anthropic/{model_id}" in restore_fallback_message(
        ("gone", "missing"), route.model, r
    )
    assert _make_prompt_check(r)(route.model) is None


async def test_configured_inspection_never_refreshes_executes_or_builds_a_client(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import google.auth
    import httpx
    from aelix_ai.oauth import _resolve_config
    from aelix_ai.providers import _google_client

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("configured presence performed resolution")

    models(sandbox, {"anthropic": {"headers": {"x-api-key": "!false"}}})
    storage, r = await registry(sandbox)
    monkeypatch.setattr(_resolve_config, "_run_shell_command", forbidden)
    monkeypatch.setattr(storage, "get_api_key_cascade", forbidden)
    monkeypatch.setattr(_google_client, "create_vertex_client", forbidden)
    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", forbidden)
    assert r.has_configured_auth(candidate(r))
    vertex = r.find("google-vertex", "gemini-2.5-flash")
    assert vertex is not None
    assert not r.has_configured_auth(vertex)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fake416-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    for path in (None, "/nonexistent/416-adc.json"):
        if path:
            monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", path)
        assert r.has_configured_auth(vertex)  # ambient/no-file/missing-file intent is deferred


async def test_command_failure_stays_at_request_time(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_ai.oauth import _resolve_config

    models(sandbox, {"anthropic": {"headers": {"x-api-key": "!synthetic416"}}})
    _, r = await registry(sandbox)
    hits: list[str] = []

    def command(value: str, **kwargs: Any) -> None:
        hits.append(value)

    monkeypatch.setattr(_resolve_config, "_run_shell_command", command)
    assert r.has_configured_auth(candidate(r))
    assert hits == []
    auth = await r.get_api_key_and_headers(candidate(r))
    assert not auth.ok and "Failed to resolve" in str(auth.error)
    assert auth.retry_reason is None
    assert hits == ["synthetic416"]


async def test_login_capability_and_fallback_are_not_request_sources(sandbox: Path) -> None:
    from aelix_ai.oauth.anthropic import ANTHROPIC_OAUTH_PROVIDER

    storage, r = await registry(sandbox)
    fallback_calls: list[str] = []
    storage.set_fallback_resolver(lambda p: fallback_calls.append(p) or "fake416-fallback")
    r.register_provider("anthropic", ProviderConfigInput(oauth=ANTHROPIC_OAUTH_PROVIDER))
    assert not r.has_configured_auth(candidate(r))
    assert fallback_calls == []
    assert r.has_route_auth("anthropic")  # separate routing contract remains unchanged
    auth = await r.get_api_key_and_headers(candidate(r))
    assert auth.ok and auth.api_key is None


@pytest.mark.parametrize(
    "stored",
    [
        {"type": "api_key", "key": ""},
        {"type": "unknown416"},
        {"type": "oauth", "access": "fake416-expired", "refresh": "fake416-refresh", "expires": 0},
    ],
)
async def test_stored_record_remains_configured_even_when_invalid(
    sandbox: Path, stored: dict[str, Any]
) -> None:
    import json

    (sandbox / "auth.json").write_text(json.dumps({"own416": stored}), "utf-8")
    storage, r = await registry(sandbox)
    selected = replace(candidate(r), provider="own416")
    assert storage.has("own416")
    assert r.has_configured_auth(selected)
    auth = await r.get_api_key_and_headers(selected)
    assert not auth.ok
    assert auth.retry_reason is None


async def test_custom_stream_requirement_is_unknown_and_deferred(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_ai import api_registry

    _, r = await registry(sandbox)
    selected = candidate(r)
    assert not r.has_configured_auth(selected)
    api_registry.register_provider("anthropic-messages", lambda *args: None)  # type: ignore[arg-type]
    assert r.has_configured_auth(selected)
    assert _make_prompt_check(r)(selected) is None


@pytest.mark.parametrize("source", ["token", "custom-headers"])
async def test_anthropic_environment_auth_never_counts_for_another_provider(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    _, r = await registry(sandbox)
    if source == "token":
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fake416-token")
    else:
        monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "Authorization: Bearer fake416-header")
    assert r.has_configured_auth(candidate(r))
    assert not r.has_configured_auth(replace(candidate(r), provider="own416"))


async def test_registered_owner_headers_are_the_effective_source(sandbox: Path) -> None:
    _, r = await registry(sandbox)
    selected = candidate(r)
    owned = replace(selected, headers={"x-api-key": "fake416-owner"})
    r.register_provider("anthropic", ProviderConfigInput(models={selected.id: owned}))
    assert r.has_configured_auth(selected)  # ownership failures retain their request-time diagnosis
    assert not (await r.get_api_key_and_headers(selected)).ok
    live = candidate(r)
    assert r.has_configured_auth(live)
    auth = await r.get_api_key_and_headers(live)
    assert auth.ok and auth.headers["x-api-key"] == "fake416-owner"
    r.register_provider(
        "anthropic", ProviderConfigInput(models={selected.id: replace(owned, headers={})})
    )
    assert not r.has_configured_auth(candidate(r))  # current owner has no header
    assert not (await r.get_api_key_and_headers(owned)).ok  # stale header still cannot send
    changed = replace(owned, base_url="https://wrong416.invalid")
    assert r.has_configured_auth(changed)  # defer ownership refusal, never misdiagnose missing auth
    assert not (await r.get_api_key_and_headers(changed)).ok
