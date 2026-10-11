"""#365 request boundary: real harness/summary and SDK requests, fake credentials only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.harness.hooks import BeforeProviderRequestResult, MessageEndEventResult
from aelix_agent_core.session.branch_summarization import generate_branch_summary
from aelix_agent_core.session.compaction import _generate_summary
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.models import get_model
from aelix_ai.oauth import register_oauth_provider, unregister_oauth_provider
from aelix_ai.oauth.types import OAuthCredentials
from aelix_ai.providers._env_api_keys import ENV_API_KEYS
from aelix_ai.streaming import ModelCost
from aelix_coding_agent.cli.entry import _make_auth_callback
from aelix_coding_agent.model_registry import ProviderConfigInput, registration_route_digest

from tests.model_registry.test_provider_ownership_365 import registry, row


@pytest.fixture
def wire(monkeypatch):
    from aelix_ai import clear_providers
    from aelix_ai.providers import openai_completions, openai_responses
    from openai import AsyncOpenAI

    for key in {name for names in ENV_API_KEYS.values() for name in names}:
        monkeypatch.delenv(key, raising=False)
    for key in list(os.environ):
        if key.startswith("OPENROUTER_"):
            monkeypatch.delenv(key)
    sent = []

    def handler(request):
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        sent.append(
            {
                "host": request.url.host,
                "port": request.url.port,
                "path": request.url.path,
                "model": json.loads(request.content).get("model"),
                "tag": {
                    "A-fake": "A",
                    "B-fake": "B",
                    "override-fake": "override",
                    "env-fake": "env",
                }.get(key, "other"),
                "header": request.headers.get("x-model"),
            }
        )
        return httpx.Response(
            int(request.headers.get("x-fake-status", "401")),
            json={"error": {"message": "synthetic refusal"}},
        )

    def factory(**kw):
        return AsyncOpenAI(
            api_key=kw.get("api_key") or "",
            base_url=kw.get("base_url"),
            default_headers=kw.get("default_headers"),
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(openai_completions, "create_async_client", factory)
    monkeypatch.setattr(openai_responses, "create_async_client", factory)
    clear_providers()
    openai_completions.register_all()
    openai_responses.register_all()
    yield sent
    clear_providers()


async def send(r, model, surface):
    auth = _make_auth_callback(r)
    message = UserMessage(content=[TextContent(text="hi")])
    if surface == "prompt":
        harness = AgentHarness(AgentHarnessOptions(model=model, get_api_key_and_headers=auth))
        await harness.prompt("hi")
    elif surface == "compaction":
        await _generate_summary(
            model,
            auth,
            SimpleNamespace(previous_summary=None, messages_to_summarize=[message]),
            None,
        )
    else:
        await generate_branch_summary(
            model, auth, [SimpleNamespace(type="message", message=message)]
        )


@pytest.mark.parametrize("surface", ["prompt", "compaction", "branch"])
@pytest.mark.parametrize(
    "stale",
    [
        "builtin",
        "host",
        "api",
        "headers",
        "header-removed",
        "child-unregister",
        "child-augmentation",
        "child-switch",
    ],
)
async def test_stale_requests_send_nothing(tmp_path, wire, surface, stale, monkeypatch):
    r = await registry(tmp_path)
    selected = (
        get_model("openai", "gpt-4o-mini")
        if stale == "builtin"
        else row(headers={"Authorization": "Bearer A-fake"} if stale.startswith("header") else None)
    )
    r.register_provider(
        "openai",
        ProviderConfigInput(
            api_key="A-fake",
            models={selected.id: selected} if stale != "builtin" else {"m1": row()},
        ),
    )
    if stale.startswith("child"):
        r.require_registration_route(registration_route_digest(selected))
    if stale == "child-unregister":
        r.unregister_provider("openai")
        selected = get_model("openai", "gpt-4o-mini")
    elif stale == "child-augmentation":
        r.register_provider("openai", ProviderConfigInput(api_key="B-fake"))
    elif stale == "child-switch":
        r.register_provider(
            "openai",
            ProviderConfigInput(api_key="B-fake", models={"m1": row(), "m2": row(model_id="m2")}),
        )
        selected = r.find("openai", "m2")
    elif stale != "builtin":
        current = (
            row(base="http://127.0.0.1:36502/v1")
            if stale == "host"
            else replace(row(), api="openai-responses")
            if stale == "api"
            else row(headers={"Authorization": "Bearer B-fake"} if stale == "headers" else None)
        )
        r.register_provider("openai", ProviderConfigInput(api_key="B-fake", models={"m1": current}))

    async def no_auth_lookup(*args, **kwargs):
        pytest.fail("An ownership refusal must precede authentication lookup")

    monkeypatch.setattr(r, "_request_api_key", no_auth_lookup)
    with pytest.raises((AgentHarnessError, RuntimeError)) as caught:
        await send(r, selected, surface)
    assert "registration" in str(caught.value)
    assert wire == []


@pytest.mark.parametrize(
    "change", ["metadata", "backfill", "oauth-projection", "unowned-header", "augmentation"]
)
async def test_valid_owner_and_existing_header_controls(tmp_path, wire, change, monkeypatch):
    r = await registry(tmp_path)
    r.register_provider(
        "openai",
        ProviderConfigInput(api_key="B-fake", models={"m1": row(headers={"x-model": "live"})}),
    )
    selected = r.find("openai", "m1")
    if change == "metadata":
        selected = replace(
            selected, name="different", context_window=42, max_tokens=21, cost=ModelCost(input=42)
        )
    elif change == "backfill":
        selected = row(model_id="unlisted")
    elif change == "oauth-projection":
        register_oauth_provider(
            SimpleNamespace(
                id="openai",
                name="fake OAuth",
                get_api_key=lambda creds: "B-fake",
                modify_models=lambda models, creds: [
                    replace(m, headers={"x-model": "oauth"}) if m.provider == "openai" else m
                    for m in models
                ],
            )
        )
        await r._auth_storage.set_oauth(
            "openai", OAuthCredentials(access="B-fake", refresh="fake", expires=10**15)
        )
        r.refresh()
        selected = r.find("openai", "m1")
    elif change == "unowned-header":
        r.unregister_provider("openai")
        selected = row(headers={"Authorization": "Bearer override-fake"})
        monkeypatch.setenv("OPENAI_API_KEY", "env-fake")
    else:
        r.register_provider("openai", ProviderConfigInput(api_key="B-fake"))
        selected = row(headers={"x-model": "caller"})
    try:
        await send(r, selected, "prompt")
    finally:
        if change == "oauth-projection":
            unregister_oauth_provider("openai")
    assert len(wire) == 1
    assert wire[0]["host"] == "127.0.0.1" and wire[0]["path"] == "/v1/chat/completions"
    assert wire[0]["model"] == ("unlisted" if change == "backfill" else "m1")
    assert wire[0]["tag"] == ("override" if change == "unowned-header" else "B")


@pytest.mark.parametrize("await_at", ["before-key", "during-cascade"])
async def test_auth_await_registration_change_refuses_before_sdk(
    tmp_path, wire, monkeypatch, await_at
):
    r = await registry(tmp_path)
    r.register_provider("openai", ProviderConfigInput(api_key="A-fake", models={"m1": row()}))
    selected = r.find("openai", "m1")
    entered = asyncio.Event()
    release = asyncio.Event()
    target = r if await_at == "before-key" else r._auth_storage
    name = "_request_api_key" if await_at == "before-key" else "get_api_key_cascade"
    original = getattr(target, name)
    if await_at == "during-cascade":
        r._auth_storage.set_runtime_api_key("openai", "A-fake")

    async def delayed(*a, **kw):
        entered.set()
        await release.wait()
        return await original(*a, **kw)

    monkeypatch.setattr(target, name, delayed)
    task = asyncio.create_task(send(r, selected, "prompt"))
    await entered.wait()
    r.register_provider("openai", ProviderConfigInput(api_key="B-fake", models={"m1": row()}))
    release.set()
    with pytest.raises(AgentHarnessError, match="registration changed"):
        await task
    assert wire == []


async def test_explicit_request_header_override_is_preserved(tmp_path, wire):
    r = await registry(tmp_path)
    r.register_provider(
        "openai",
        ProviderConfigInput(api_key="B-fake", models={"m1": row(headers={"x-model": "live"})}),
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=r.find("openai", "m1"),
            get_api_key_and_headers=_make_auth_callback(r),
            stream_options={"headers": {"Authorization": "Bearer override-fake"}},
        )
    )
    await harness.prompt("hi")
    assert len(wire) == 1 and wire[0]["tag"] == "override" and wire[0]["header"] == "live"


async def test_owned_exact_hostless_model_never_uses_sdk_default(tmp_path, wire):
    r = await registry(tmp_path)
    selected = row(base="")
    r.register_provider("openai", ProviderConfigInput(api_key="A-fake", models={"m1": selected}))
    with pytest.raises(AgentHarnessError, match="registration"):
        await send(r, selected, "prompt")
    assert wire == []


@pytest.mark.parametrize("event", ["before_provider_request", "before_provider_payload"])
@pytest.mark.parametrize(
    "action",
    [
        "unregister",
        "augmentation",
        "reregister-same",
        "reset",
        "headers",
        "model-switch",
        "metadata",
        "options-header",
        "unchanged",
    ],
)
async def test_closing_auth_snapshot_after_request_hooks(
    tmp_path, wire, monkeypatch, event, action
):
    r = await registry(tmp_path)
    r.register_provider(
        "openai",
        ProviderConfigInput(
            api_key="B-fake",
            models={"m1": row(headers={"x-model": "live"}), "m2": row(model_id="m2")},
        ),
    )
    selected = r.find("openai", "m1")
    r.require_registration_route(registration_route_digest(selected))
    calls = 0
    original = r._request_api_key

    async def once(*a, **kw):
        nonlocal calls
        calls += 1
        return await original(*a, **kw)

    monkeypatch.setattr(r, "_request_api_key", once)
    harness = AgentHarness(
        AgentHarnessOptions(model=selected, get_api_key_and_headers=_make_auth_callback(r))
    )

    async def change(hook_event, ctx):
        if action == "unregister":
            r.unregister_provider("openai")
        elif action == "augmentation":
            r.register_provider("openai", ProviderConfigInput(api_key="A-fake"))
        elif action == "reregister-same":
            r.register_provider(
                "openai",
                ProviderConfigInput(
                    api_key="A-fake", models={"m1": row(headers={"x-model": "live"})}
                ),
            )
        elif action == "reset":
            r.reset()
        elif action == "headers":
            selected.headers["x-model"] = "changed"
        elif action == "model-switch":
            await harness.set_model(r.find("openai", "m2"))
        elif action == "metadata":
            await harness.set_model(
                replace(selected, name="new", context_window=42, cost=ModelCost(input=42))
            )
        elif action == "options-header" and event == "before_provider_request":
            return BeforeProviderRequestResult(
                stream_options={"headers": {"Authorization": "Bearer override-fake"}}
            )
        return None

    harness.hooks.on(event, change)
    with contextlib.suppress(AgentHarnessError):
        await harness.prompt("hi")
    assert calls == 1  # closing checks never refresh OAuth or re-run key commands
    if action in ("metadata", "options-header", "unchanged"):
        assert len(wire) == 1
        assert wire[0]["tag"] == (
            "override" if action == "options-header" and event == "before_provider_request" else "B"
        )
    else:
        assert wire == []


@pytest.mark.parametrize("surface", ["compaction", "prefix", "branch"])
async def test_summary_paths_carry_the_same_closing_validator(tmp_path, wire, surface):
    r = await registry(tmp_path)
    r.register_provider("openai", ProviderConfigInput(api_key="B-fake", models={"m1": row()}))
    model = r.find("openai", "m1")
    auth = _make_auth_callback(r)

    async def revoke_after_auth(request_model):
        snapshot = await auth(request_model)
        r.unregister_provider("openai")
        return snapshot

    message = UserMessage(content=[TextContent(text="hi")])
    with pytest.raises(RuntimeError, match="registration"):
        if surface == "compaction":
            await _generate_summary(
                model,
                revoke_after_auth,
                SimpleNamespace(previous_summary=None, messages_to_summarize=[message]),
                None,
            )
        elif surface == "prefix":
            from aelix_agent_core.session.compaction import _generate_turn_prefix_summary

            await _generate_turn_prefix_summary(model, revoke_after_auth, [message])
        else:
            await generate_branch_summary(
                model, revoke_after_auth, [SimpleNamespace(type="message", message=message)]
            )
    assert wire == []


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
@pytest.mark.parametrize("rewrite", [False, True])
async def test_closing_failure_is_fatal_after_adapter_and_message_replacement(
    tmp_path, wire, monkeypatch, api, rewrite
):
    from aelix_agent_core.harness import core as core_mod
    from aelix_ai.messages import AssistantMessage

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 0)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_MAX_ATTEMPTS", 1)
    auth_calls = 0
    events = []

    def auth(model):
        nonlocal auth_calls
        auth_calls += 1
        attempt = auth_calls
        checks = 0

        def validate(request_model):
            nonlocal checks
            checks += 1
            if attempt == 1 and checks == 2:
                raise RuntimeError("synthetic 503 closing refusal")

        return {"apiKey": "B-fake", "validate": validate}

    selected = replace(row(), api=api)
    harness = AgentHarness(AgentHarnessOptions(model=selected, get_api_key_and_headers=auth))
    harness.subscribe(
        lambda event: events.append(event.type) if event.type.startswith("auto_retry") else None
    )
    if rewrite:

        def replace_message(event, ctx):
            return MessageEndEventResult(
                message=AssistantMessage(
                    content=[], stop_reason="stop", error_message="synthetic 503 replaced message"
                )
            )

        harness.hooks.on("message_end", replace_message)
    with contextlib.suppress(AgentHarnessError):
        await harness.prompt("first")
    assert wire == [] and auth_calls == 1
    assert events == [] and harness._retry_attempt == 0
    # A new user turn has a fresh snapshot and may send. Fatal classification
    # must neither retry the refused dispatch nor poison subsequent requests.
    with contextlib.suppress(AgentHarnessError):
        await harness.prompt("second")
    assert auth_calls == 2 and len(wire) == 1
    assert wire[0]["tag"] == "B" and events == []


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
async def test_real_sdk_503_still_retries_its_request(tmp_path, wire, monkeypatch, api):
    from aelix_agent_core.harness import core as core_mod

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 0)
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_MAX_ATTEMPTS", 1)
    r = await registry(tmp_path)
    r.register_provider(
        "openai", ProviderConfigInput(api_key="B-fake", models={"m1": replace(row(), api=api)})
    )
    auth = _make_auth_callback(r)
    auth_calls = 0

    async def resolve(model):
        nonlocal auth_calls
        auth_calls += 1
        return await auth(model)

    events = []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=r.find("openai", "m1"),
            get_api_key_and_headers=resolve,
            stream_options={"headers": {"x-fake-status": "503"}},
        )
    )
    harness.subscribe(
        lambda event: events.append(event.type) if event.type.startswith("auto_retry") else None
    )
    await harness.prompt("hi")
    assert auth_calls == 2 and len(wire) == 2
    assert all(request["tag"] == "B" for request in wire)
    assert events == ["auto_retry_start", "auto_retry_end"] and harness._retry_attempt == 0
