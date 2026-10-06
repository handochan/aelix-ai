"""#374 — no Anthropic SDK client reads a credential from the environment.

Before: ``_anthropic_client.create_async_client`` dropped ``api_key=None``, so
the Anthropic SDK read ``ANTHROPIC_API_KEY`` (and ``ANTHROPIC_AUTH_TOKEN``, and
on 0.102 its whole default credential chain) for EVERY ``anthropic-messages``
provider. A custom gateway with no key of its own (and built-ins such as
``fireworks`` / ``minimax`` / ``vercel-ai-gateway`` with theirs unset) received
the user's Anthropic key; a header-authenticated gateway got it next to its
own header; and the OAuth and Copilot branches sent an empty ``x-api-key``
next to the bearer (#363 verify L14).

pi (``packages/ai/src/api/anthropic-messages.ts`` @ b223082bb):
``assertRequestAuth`` (:316-326, called at :612-613) fails a request with no
key and no auth header with ``No API key for provider: <p>``; ``createClient``
(:982-1078) passes ``apiKey: apiKey ?? null`` / ``authToken: null`` on a
``PiAnthropic`` subclass that never runs the SDK's credential chain;
``ANTHROPIC_AUTH_TOKEN`` is a bearer for provider ``anthropic`` only
(``providers/anthropic.ts:34-41``).

Every row records what reaches an :class:`httpx.MockTransport` behind the REAL
factory (the SDK's ``__init__`` is wrapped only to hand it the transport).
Fake keys, an isolated home, no socket.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from aelix_ai import Context, SimpleStreamOptions
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.models import get_model, get_models, get_providers
from aelix_ai.providers import _anthropic_client, _google_client
from aelix_ai.providers import anthropic as anthropic_mod
from aelix_ai.providers._anthropic_client import create_async_client
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.streaming import Model
from anthropic import AsyncAnthropic

from tests.env_sandbox import sandbox_home

_GW = "http://custom-gw.invalid"
_TAGS = {
    "sk-ant-env-fake": "ANTHROPIC_API_KEY",
    "ant-auth-token-fake": "ANTHROPIC_AUTH_TOKEN",
    "sk-ant-oat01-env-fake": "ANTHROPIC_OAUTH_TOKEN",
    "sk-ant-oat01-given-fake": "oauth",
    "gw-header-fake": "gw-header",
    "given-key-fake": "given",
    "copilot-fake": "copilot",
    "custom-hdr-fake": "ANTHROPIC_CUSTOM_HEADERS",
    "chain-token-fake": "sdk-chain",
    "fireworks-own-fake": "fireworks-own",
    "goog-env-fake": "GOOGLE_API_KEY",
    "gemini-env-fake": "GEMINI_API_KEY",
    "openai-env-fake": "OPENAI_API_KEY",
}


def _hits(headers: httpx.Headers) -> list[str]:
    """Every header (any name, every value) carrying one of the fake values."""

    return sorted(
        f"{name.lower()}={tag}"
        for name, value in headers.multi_items()
        for raw, tag in _TAGS.items()
        if raw in value
    )


def _tag(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = raw[7:] if raw.lower().startswith("bearer ") else raw
    return _TAGS.get(token, "EMPTY" if not token else "other")


class _Wire:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.hits: list[list[str]] = []
        # A non-secret header a models.json override adds (review round 3).
        self.x_team: list[str | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hits.append(_hits(request.headers))
        self.x_team.append(request.headers.get("x-team"))
        self.sent.append(
            {
                "host": request.url.host,
                "x-api-key": _tag(request.headers.get("x-api-key")),
                "has-x-api-key": "x-api-key" in request.headers,
                "authorization": _tag(request.headers.get("authorization")),
                "cf-aig-authorization": _tag(request.headers.get("cf-aig-authorization")),
            }
        )
        return httpx.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Every credential-ish variable cleared, an isolated home."""

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("ANTHROPIC_", "AELIX_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    return monkeypatch


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    rec = _Wire()
    real_init = AsyncAnthropic.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
        kwargs["max_retries"] = 0
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(AsyncAnthropic, "__init__", _init)
    return rec


def _custom(provider: str = "mygw") -> Model:
    return Model(
        id="m1",
        name="M1",
        api="anthropic-messages",
        provider=provider,
        base_url=_GW,
        reasoning=False,
        input=["text"],
        context_window=10000,
        max_tokens=1000,
    )


async def _turn(model: Model, **opts: Any) -> list[str]:
    errors: list[str] = []
    ctx = Context(messages=[UserMessage(content=[TextContent(text="hi")])])
    async for event in stream_anthropic(model, ctx, SimpleStreamOptions(**opts)):
        if getattr(event, "type", "") == "error":
            errors.append(event.error_message or "")
    return errors


def _export_all_anthropic(env: pytest.MonkeyPatch) -> None:
    env.setenv("ANTHROPIC_API_KEY", "sk-ant-env-fake")
    env.setenv("ANTHROPIC_AUTH_TOKEN", "ant-auth-token-fake")


# ===================================================================== factory


async def test_the_factory_reads_no_key_from_the_environment(env: Any, wire: _Wire) -> None:
    """No key given: no ``x-api-key``, no bearer, whatever is exported."""

    _export_all_anthropic(env)
    client = create_async_client(
        base_url=_GW, default_headers={"authorization": "Bearer gw-header-fake"}, max_retries=0
    )
    assert client.api_key is None
    assert client.auth_token is None
    with pytest.raises(Exception):  # noqa: B017 - the recorder answers 400
        await client.messages.create(
            model="m1", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
        )
    # The lower-case header alone authenticates: the SDK's case-sensitive
    # check would have refused it once no key rides along.
    assert wire.sent == [
        {
            "host": "custom-gw.invalid",
            "x-api-key": None,
            "has-x-api-key": False,
            "authorization": "gw-header",
            "cf-aig-authorization": None,
        }
    ]


async def test_the_factory_with_no_auth_at_all_sends_nothing(env: Any, wire: _Wire) -> None:
    _export_all_anthropic(env)
    client = create_async_client(base_url=_GW, max_retries=0)
    with pytest.raises(TypeError, match="Could not resolve authentication method"):
        await client.messages.create(
            model="m1", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
        )
    assert wire.sent == []


@pytest.mark.parametrize("given", ["", None])
def test_an_empty_key_is_no_key(env: Any, given: str | None) -> None:
    """``""`` (the old OAuth/Copilot blank) emits no ``x-api-key`` header (L14)."""

    _export_all_anthropic(env)
    client = create_async_client(api_key=given, base_url=_GW)
    assert client.auth_headers == {}


def test_copy_and_with_options_keep_reading_nothing(env: Any) -> None:
    """The SDK's ``copy()`` rebuilds via ``self.__class__(api_key=... or self.api_key)``."""

    _export_all_anthropic(env)
    client = create_async_client(base_url=_GW)
    for clone in (client.copy(), client.with_options(timeout=5)):
        assert clone.api_key is None
        assert clone.auth_token is None
        assert clone.auth_headers == {}
    keyed = create_async_client(api_key="given-key-fake", base_url=_GW).with_options(timeout=5)
    assert keyed.auth_headers == {"X-Api-Key": "given-key-fake"}


def test_the_sdk_credential_chain_does_not_run(env: Any, tmp_path: Path) -> None:
    """Workload identity federation env: the plain SDK binds it to the gateway."""

    token_file = tmp_path / "id-token"
    token_file.write_text("id-token-fake", "utf-8")
    env.setenv("ANTHROPIC_FEDERATION_RULE_ID", "fr-fake")
    env.setenv("ANTHROPIC_ORGANIZATION_ID", "org-fake")
    env.setenv("ANTHROPIC_IDENTITY_TOKEN_FILE", str(token_file))
    gateway = "https://custom-gw.invalid"
    # Control: the unwrapped SDK picks the federation up for a custom host.
    assert AsyncAnthropic(base_url=gateway).credentials is not None
    assert create_async_client(base_url=gateway).credentials is None


def test_given_credentials_are_sent_as_given(env: Any) -> None:
    _export_all_anthropic(env)
    assert create_async_client(api_key="given-key-fake").auth_headers == {
        "X-Api-Key": "given-key-fake"
    }
    assert create_async_client(auth_token="given-key-fake").auth_headers == {
        "Authorization": "Bearer given-key-fake"
    }


# ===================================================================== adapter


async def test_a_custom_provider_with_no_key_sends_nothing(env: Any, wire: _Wire) -> None:
    _export_all_anthropic(env)
    errors = await _turn(_custom())
    assert wire.sent == []
    assert errors == ["No API key for provider: mygw"]


def _anthropic_messages_providers() -> list[str]:
    return sorted(
        p
        for p in get_providers()
        if p != "anthropic" and any(m.api == "anthropic-messages" for m in get_models(p))
    )


@pytest.mark.parametrize("provider", _anthropic_messages_providers())
async def test_a_built_in_provider_never_gets_the_anthropic_key(
    env: Any, wire: _Wire, provider: str
) -> None:
    """Every catalog provider on ``anthropic-messages`` but ``anthropic`` itself."""

    _export_all_anthropic(env)
    env.setenv("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-env-fake")
    env.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    env.setenv("CLOUDFLARE_GATEWAY_ID", "gw")
    model = next(m for m in get_models(provider) if m.api == "anthropic-messages")
    errors = await _turn(model)
    assert wire.sent == []
    assert errors == [f"No API key for provider: {provider}"]


def test_the_parametrized_list_covers_the_known_gateways() -> None:
    providers = _anthropic_messages_providers()
    assert {"fireworks", "minimax", "vercel-ai-gateway", "github-copilot"} <= set(providers)


@pytest.mark.parametrize(
    "header",
    ["Authorization", "authorization", "x-api-key", "cf-aig-authorization"],
)
async def test_a_header_authenticated_gateway_gets_only_its_header(
    env: Any, wire: _Wire, header: str
) -> None:
    _export_all_anthropic(env)
    value = "gw-header-fake" if header.lower() == "x-api-key" else "Bearer gw-header-fake"
    await _turn(_custom(), headers={header: value})
    assert len(wire.sent) == 1
    sent = wire.sent[0]
    for name in ("x-api-key", "authorization", "cf-aig-authorization"):
        expected = "gw-header" if name == header.lower() else None
        assert sent[name] == expected, (name, sent)


async def test_a_given_key_goes_to_the_gateway_not_the_exported_one(env: Any, wire: _Wire) -> None:
    _export_all_anthropic(env)
    await _turn(_custom(), api_key="given-key-fake")
    assert [(s["x-api-key"], s["authorization"]) for s in wire.sent] == [("given", None)]


async def test_anthropic_still_reads_its_own_api_key(env: Any, wire: _Wire) -> None:
    """Unchanged: provider ``anthropic`` + ``ANTHROPIC_API_KEY`` (library use, no harness)."""

    _export_all_anthropic(env)
    await _turn(get_model("anthropic", "claude-haiku-4-5"))
    assert [(s["host"], s["x-api-key"], s["authorization"]) for s in wire.sent] == [
        ("api.anthropic.com", "ANTHROPIC_API_KEY", None)
    ]


async def test_anthropic_oauth_token_env_takes_the_oauth_branch(env: Any, wire: _Wire) -> None:
    env.setenv("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-env-fake")
    env.setenv("ANTHROPIC_API_KEY", "sk-ant-env-fake")
    await _turn(get_model("anthropic", "claude-haiku-4-5"))
    assert [(s["has-x-api-key"], s["authorization"]) for s in wire.sent] == [
        (False, "ANTHROPIC_OAUTH_TOKEN")
    ]


async def test_anthropic_auth_token_is_a_bearer_for_anthropic_only(env: Any, wire: _Wire) -> None:
    env.setenv("ANTHROPIC_AUTH_TOKEN", "ant-auth-token-fake")
    await _turn(get_model("anthropic", "claude-haiku-4-5"))
    assert [(s["has-x-api-key"], s["authorization"]) for s in wire.sent] == [
        (False, "ANTHROPIC_AUTH_TOKEN")
    ]
    wire.sent.clear()
    errors = await _turn(_custom())
    assert wire.sent == []
    assert errors == ["No API key for provider: mygw"]


async def test_anthropic_auth_token_never_joins_another_credential(env: Any, wire: _Wire) -> None:
    _export_all_anthropic(env)
    model = get_model("anthropic", "claude-haiku-4-5")
    await _turn(model)
    await _turn(model, headers={"Authorization": "Bearer gw-header-fake"})
    assert [(s["x-api-key"], s["authorization"]) for s in wire.sent] == [
        ("ANTHROPIC_API_KEY", None),
        ("ANTHROPIC_API_KEY", "gw-header"),
    ]
    wire.sent.clear()
    env.delenv("ANTHROPIC_API_KEY")
    await _turn(model, headers={"Authorization": "Bearer gw-header-fake"})
    # A header of another name: the token must not ride along as a bearer
    # (with ``Authorization`` the caller's header would mask it on the wire).
    await _turn(model, headers={"x-api-key": "gw-header-fake"})
    assert [(s["x-api-key"], s["authorization"]) for s in wire.sent] == [
        (None, "gw-header"),
        ("gw-header", None),
    ]


async def test_oauth_sends_no_empty_x_api_key(env: Any, wire: _Wire) -> None:
    """#363 verify L14: the bearer alone, no ``x-api-key: <empty>`` beside it."""

    _export_all_anthropic(env)
    model = replace(get_model("anthropic", "claude-haiku-4-5"), base_url=_GW)
    await _turn(model, api_key="sk-ant-oat01-given-fake")
    assert [(s["has-x-api-key"], s["authorization"]) for s in wire.sent] == [(False, "oauth")]


async def test_copilot_sends_no_empty_x_api_key(env: Any, wire: _Wire) -> None:
    _export_all_anthropic(env)
    model = next(m for m in get_models("github-copilot") if m.api == "anthropic-messages")
    await _turn(model, api_key="copilot-fake")
    assert [(s["has-x-api-key"], s["authorization"]) for s in wire.sent] == [(False, "copilot")]


async def test_copilot_with_no_token_but_a_header_does_not_send_bearer_none(
    env: Any, wire: _Wire
) -> None:
    model = next(m for m in get_models("github-copilot") if m.api == "anthropic-messages")
    await _turn(model, headers={"Authorization": "Bearer gw-header-fake"})
    assert [(s["has-x-api-key"], s["authorization"]) for s in wire.sent] == [(False, "gw-header")]


async def test_an_injected_client_skips_the_guard(env: Any) -> None:
    """pi: ``options.client`` is used as given; auth is the caller's business."""

    used: list[bool] = []

    class _Stub:
        class messages:  # noqa: N801
            @staticmethod
            def stream(**_kw: Any) -> Any:
                used.append(True)
                raise RuntimeError("stub reached")

    errors = await _turn(_custom(), client=_Stub())
    assert used == [True]
    assert errors and "No API key" not in errors[0]


# ============================================================ through the harness


async def _harness_turn(
    env: pytest.MonkeyPatch,
    tmp_path: Path,
    models_json: dict[str, Any],
    provider: str,
    model_id: str,
) -> list[str]:
    """One turn the way ``--mode rpc`` and the TUI take it: models.json ->
    registry -> CLI auth callback -> harness -> adapter. Returns the errors."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions, _TurnState
    from aelix_ai.oauth import AuthStorage
    from aelix_coding_agent.cli.entry import _make_auth_callback
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
    from aelix_coding_agent.model_registry import ModelRegistry

    agent = tmp_path / "agent"
    agent.mkdir()
    env.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    env.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    (agent / "auth.json").write_text("{}", "utf-8")
    (agent / "models.json").write_text(json.dumps(models_json), "utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    model = resolve_model(model_id, provider, registry)
    # A private adapter registry for this test, restored afterwards.
    env.setattr("aelix_ai.api_registry._PROVIDERS", {})
    anthropic_mod.register_all()
    harness = AgentHarness(
        AgentHarnessOptions(model=model, get_api_key_and_headers=_make_auth_callback(registry))
    )
    stream = harness._make_stream_fn(lambda: _TurnState(system_prompt="", model=model))
    errors: list[str] = []
    async for event in stream(
        model,
        Context(messages=[UserMessage(content=[TextContent(text="hi")])]),
        SimpleStreamOptions(),
    ):
        if getattr(event, "type", "") == "error":
            errors.append(event.error_message or "")
    return errors


async def test_a_harness_turn_to_a_keyless_custom_provider_sends_nothing(
    env: Any, wire: _Wire, tmp_path: Path
) -> None:
    """Registry -> CLI auth callback -> harness -> adapter (print/json/rpc/TUI path)."""

    _export_all_anthropic(env)
    m1 = {
        "id": "m1",
        "name": "M1",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 10000,
        "maxTokens": 1000,
    }
    gw = {"providers": {"mygw": {"baseUrl": _GW, "api": "anthropic-messages", "models": [m1]}}}
    errors = await _harness_turn(env, tmp_path, gw, "mygw", "m1")
    assert wire.sent == []
    assert errors == ["No API key for provider: mygw"]


# ======================================================================= google


@pytest.mark.parametrize("given", [None, ""])
def test_a_keyless_gemini_client_is_refused(env: Any, given: str | None) -> None:
    """google-genai has no 'no key' value: ``None`` and ``""`` read GOOGLE_API_KEY."""

    env.setenv("GOOGLE_API_KEY", "goog-env-fake")
    with pytest.raises(RuntimeError, match="#374"):
        _google_client.create_client(api_key=given, base_url=_GW)


def test_a_keyless_vertex_client_needs_a_project_or_location(env: Any) -> None:
    env.setenv("GOOGLE_API_KEY", "goog-env-fake")
    with pytest.raises(RuntimeError, match="#374"):
        _google_client.create_vertex_client(base_url=_GW)
    client = _google_client.create_vertex_client(project="proj-fake", location="us-central1")
    assert client._api_client.api_key is None


def test_the_module_exports_the_auth_header_check() -> None:
    assert _anthropic_client.has_auth_header({"CF-AIG-Authorization": "Bearer x"})
    assert not _anthropic_client.has_auth_header({"authorization": "   "})
    assert not _anthropic_client.has_auth_header(None)
    assert anthropic_mod.ANTHROPIC_AUTH_TOKEN_ENV == "ANTHROPIC_AUTH_TOKEN"


# ======================================== review round 1: ANTHROPIC_CUSTOM_HEADERS
#
# The SDK (0.98+) parses ``ANTHROPIC_CUSTOM_HEADERS`` into every client's
# default headers, under the caller's. It can carry ``x-api-key`` /
# ``Authorization``: on 07fdc31d a lower-case one rode next to a gateway's own
# header or key, and a canonical ``X-Api-Key`` replaced the gateway's key.
# Decision: none of it reaches a provider other than ``anthropic``.


@pytest.mark.parametrize("ambient", ["x-api-key", "X-Api-Key", "Authorization"])
@pytest.mark.parametrize(
    ("gateway_auth", "own"),
    [
        ({"headers": {"Authorization": "Bearer gw-header-fake"}}, "authorization=gw-header"),
        ({"headers": {"x-api-key": "gw-header-fake"}}, "x-api-key=gw-header"),
        ({"api_key": "given-key-fake"}, "x-api-key=given"),
    ],
    ids=["own-Authorization", "own-x-api-key", "own-apiKey"],
)
async def test_a_gateway_never_gets_ambient_custom_headers(
    env: Any, wire: _Wire, ambient: str, gateway_auth: dict[str, Any], own: str
) -> None:
    value = "Bearer custom-hdr-fake" if ambient == "Authorization" else "custom-hdr-fake"
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", f"{ambient}: {value}\nx-other: custom-hdr-fake")
    await _turn(_custom(), **gateway_auth)
    assert wire.hits == [[own]]


async def test_a_keyless_gateway_is_not_authenticated_by_ambient_custom_headers(
    env: Any, wire: _Wire
) -> None:
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Api-Key: custom-hdr-fake")
    errors = await _turn(_custom())
    assert wire.sent == []
    assert errors == ["No API key for provider: mygw"]


async def test_a_built_in_gateway_never_gets_ambient_custom_headers(env: Any, wire: _Wire) -> None:
    env.setenv("FIREWORKS_API_KEY", "fireworks-own-fake")
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Api-Key: custom-hdr-fake")
    model = next(m for m in get_models("fireworks") if m.api == "anthropic-messages")
    await _turn(replace(model, base_url=_GW))
    assert wire.hits == [["x-api-key=fireworks-own"]]


async def test_copilot_and_oauth_on_a_gateway_never_get_ambient_custom_headers(
    env: Any, wire: _Wire
) -> None:
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: custom-hdr-fake")
    copilot = next(m for m in get_models("github-copilot") if m.api == "anthropic-messages")
    await _turn(replace(copilot, base_url=_GW), api_key="copilot-fake")
    await _turn(_custom(), api_key="sk-ant-oat01-given-fake")
    assert wire.hits == [["authorization=copilot"], ["authorization=oauth"]]


@pytest.mark.parametrize(
    ("ambient", "expected"),
    [
        # Unchanged for ``anthropic``: the SDK's documented way to reach your
        # own Anthropic proxy; a canonical ``X-Api-Key`` replaces the key.
        ("X-Api-Key: custom-hdr-fake", ["x-api-key=ANTHROPIC_CUSTOM_HEADERS"]),
        (
            "Authorization: Bearer custom-hdr-fake",
            ["authorization=ANTHROPIC_CUSTOM_HEADERS", "x-api-key=ANTHROPIC_API_KEY"],
        ),
        (
            "x-api-key: custom-hdr-fake",
            ["x-api-key=ANTHROPIC_API_KEY", "x-api-key=ANTHROPIC_CUSTOM_HEADERS"],
        ),
    ],
)
async def test_anthropic_keeps_its_custom_headers(
    env: Any, wire: _Wire, ambient: str, expected: list[str]
) -> None:
    env.setenv("ANTHROPIC_API_KEY", "sk-ant-env-fake")
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", ambient)
    await _turn(replace(get_model("anthropic", "claude-haiku-4-5"), base_url=_GW))
    assert wire.hits == [expected]


async def test_the_factory_keeps_custom_headers_only_when_asked(env: Any, wire: _Wire) -> None:
    """Codex A7: the factory itself, no adapter guard in front of it."""

    env.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: custom-hdr-fake")
    bare = create_async_client(base_url=_GW, max_retries=0)
    for client in (bare, bare.copy(), bare.with_options(timeout=5)):
        with pytest.raises(TypeError, match="Could not resolve authentication method"):
            await client.messages.create(
                model="m1", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
            )
    assert wire.hits == []
    kept = create_async_client(base_url=_GW, max_retries=0, env_custom_headers=True)
    for client in (kept, kept.with_options(timeout=5)):
        with pytest.raises(Exception):  # noqa: B017 - the recorder answers 400
            await client.messages.create(
                model="m1", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
            )
    assert wire.hits == [["x-api-key=ANTHROPIC_CUSTOM_HEADERS"]] * 2


# ============================ review round 1: the two layers, on other SDK shapes
#
# ``AelixAsyncAnthropic.__init__`` has two layers: an explicit credential
# argument (``api_key=key or ""``) and storing exactly what the caller gave
# after ``super().__init__``. On the locked 0.102 each is redundant with the
# other, so the real SDK cannot pin either alone. Each row below simulates an
# SDK in the pinned range ``>=0.40,<1.0`` where one layer alone stops the leak.


async def test_layer_two_on_an_sdk_that_reads_both_variables_independently(
    env: Any, wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """anthropic 0.40-0.97: ``auth_token`` is read from the environment whenever
    it is ``None``, whatever ``api_key`` is (round-1 verify on real 0.40.0: the
    factory then sent ``ANTHROPIC_AUTH_TOKEN`` as a bearer)."""

    wired_init = AsyncAnthropic.__init__

    def _old_sdk_init(
        self: Any, *, api_key: str | None = None, auth_token: str | None = None, **kw: Any
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        if auth_token is None:
            auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        wired_init(self, api_key=api_key, auth_token=auth_token, **kw)
        self.api_key = api_key
        self.auth_token = auth_token

    monkeypatch.setattr(AsyncAnthropic, "__init__", _old_sdk_init)
    _export_all_anthropic(env)
    # Control: the simulated SDK does read both on its own.
    plain = AsyncAnthropic(base_url=_GW)
    assert (plain.api_key, plain.auth_token) == ("sk-ant-env-fake", "ant-auth-token-fake")
    client = create_async_client(
        base_url=_GW, default_headers={"authorization": "Bearer gw-header-fake"}, max_retries=0
    )
    assert (client.api_key, client.auth_token) == (None, None)
    with pytest.raises(Exception):  # noqa: B017 - the recorder answers 400
        await client.messages.create(
            model="m1", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
        )
    assert wire.hits == [["authorization=gw-header"]]
    assert [s["has-x-api-key"] for s in wire.sent] == [False]


async def test_layer_one_on_an_sdk_whose_credential_chain_runs_for_subclasses(
    env: Any, wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.98-0.102 run the default credential chain only for the base classes
    (``_is_base_client``); the explicit credential argument is what keeps it
    off if a version runs it for a subclass too. Re-assigning ``api_key`` /
    ``auth_token`` afterwards does not undo a chain credential."""

    import anthropic._client as sdk_client
    from anthropic.lib.credentials import StaticToken
    from anthropic.lib.credentials._types import CredentialResult

    monkeypatch.setattr(sdk_client, "_is_base_client", lambda _client: True)
    monkeypatch.setattr(
        sdk_client,
        "default_credentials",
        lambda **_kw: CredentialResult(provider=StaticToken("chain-token-fake")),
    )

    class _Subclass(AsyncAnthropic):
        pass

    # Control: with the patch the chain does run for a plain subclass.
    assert _Subclass(base_url=_GW).credentials is not None
    client = create_async_client(
        base_url=_GW, default_headers={"authorization": "Bearer gw-header-fake"}, max_retries=0
    )
    assert client.credentials is None
    with pytest.raises(Exception):  # noqa: B017 - the recorder answers 400
        await client.messages.create(
            model="m1", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
        )
    assert wire.hits == [["authorization=gw-header"]]


# ===================== review round 1: an explicit key reaches the wire (siblings)
#
# The sweep calls these constructions safe because they pass the resolved key
# explicitly. Codex: a mutant that drops the key in ``create_client`` passed
# every touched file and sent ``GOOGLE_API_KEY`` to a custom gateway. Each row
# exports the vendor variables and asserts only the given key is on the wire.


@pytest.fixture
def any_wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    """Every ``httpx.AsyncClient`` (google-genai, openai) answers from a recorder."""

    from google.genai import _api_client as genai_api_client

    rec = _Wire()
    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(rec.handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    monkeypatch.setattr(genai_api_client.BaseApiClient, "_use_aiohttp", lambda _self: False)
    return rec


def _sibling(api: str) -> Model:
    return replace(_custom(), api=api)


def _export_vendor_keys(env: pytest.MonkeyPatch) -> None:
    env.setenv("GOOGLE_API_KEY", "goog-env-fake")
    env.setenv("GEMINI_API_KEY", "gemini-env-fake")
    env.setenv("OPENAI_API_KEY", "openai-env-fake")
    _export_all_anthropic(env)


async def test_gemini_sends_the_given_key_not_an_exported_one(env: Any, any_wire: _Wire) -> None:
    from aelix_ai.providers.google_generative_ai import stream_google

    _export_vendor_keys(env)
    ctx = Context(messages=[UserMessage(content=[TextContent(text="hi")])])
    async for _ in stream_google(
        _sibling("google-generative-ai"), ctx, SimpleStreamOptions(api_key="given-key-fake")
    ):
        pass
    assert any_wire.hits and all(h == ["x-goog-api-key=given"] for h in any_wire.hits)


async def test_vertex_sends_the_given_key_not_an_exported_one(env: Any, any_wire: _Wire) -> None:
    from aelix_ai.providers.google_vertex import GoogleVertexOptions, stream_google_vertex

    _export_vendor_keys(env)
    ctx = Context(messages=[UserMessage(content=[TextContent(text="hi")])])
    async for _ in stream_google_vertex(
        _sibling("google-vertex"), ctx, GoogleVertexOptions(api_key="given-key-fake")
    ):
        pass
    assert any_wire.hits and all(h == ["x-goog-api-key=given"] for h in any_wire.hits)


@pytest.mark.parametrize("api", ["openai-completions", "openai-responses"])
async def test_openai_sends_the_given_key_not_an_exported_one(
    env: Any, any_wire: _Wire, api: str
) -> None:
    from aelix_ai.providers.openai_completions import stream_openai_completions
    from aelix_ai.providers.openai_responses import stream_openai_responses

    fn = stream_openai_completions if api == "openai-completions" else stream_openai_responses
    _export_vendor_keys(env)
    ctx = Context(messages=[UserMessage(content=[TextContent(text="hi")])])
    async for _ in fn(_sibling(api), ctx, SimpleStreamOptions(api_key="given-key-fake")):
        pass
    assert any_wire.hits and all(h == ["authorization=given"] for h in any_wire.hits)


async def test_a_keyless_openai_completions_turn_never_sends_openai_api_key(
    env: Any, any_wire: _Wire
) -> None:
    """``stream_openai_completions`` builds its client with ``""`` when no key
    resolved; the ``openai`` SDK treats ``""`` as given, and ``None`` would read
    ``OPENAI_API_KEY`` (round-1 fix sabotage S43 survived without this row)."""

    from aelix_ai.providers._openai_client import create_async_client as openai_factory
    from aelix_ai.providers.openai_completions import stream_openai_completions

    _export_vendor_keys(env)
    assert openai_factory(api_key=None, base_url=_GW).api_key == ""
    ctx = Context(messages=[UserMessage(content=[TextContent(text="hi")])])
    async for _ in stream_openai_completions(
        _sibling("openai-completions"), ctx, SimpleStreamOptions()
    ):
        pass
    assert any_wire.hits and all("authorization=OPENAI_API_KEY" not in h for h in any_wire.hits)


# ================================================================ review round 2
#
# Verify B2 / Codex P2: round 1 kept ``ANTHROPIC_CUSTOM_HEADERS`` for provider
# ``anthropic`` but the guard in front of the client only looked at the key and
# ``options.headers``, so a header-only Anthropic proxy (no key, its auth in the
# variable) stopped with ``No API key for provider: anthropic``; on aab1f210 it
# got one request with that header. For ``anthropic`` an auth header in the
# variable counts, in any letter case; for every other provider it never does.

_CH_AUTH = [
    "X-Api-Key: custom-hdr-fake",
    "x-api-key: custom-hdr-fake",
    "Authorization: Bearer custom-hdr-fake",
    "authorization: Bearer custom-hdr-fake",
    "CF-AIG-Authorization: Bearer custom-hdr-fake",
]


_AUTH_NAMES = ("x-api-key", "authorization", "cf-aig-authorization")


def _anthropic_proxy() -> Model:
    return replace(get_model("anthropic", "claude-haiku-4-5"), base_url=_GW)


@pytest.mark.parametrize("ambient", _CH_AUTH)
async def test_a_header_only_anthropic_proxy_is_authenticated_by_custom_headers(
    env: Any, wire: _Wire, ambient: str
) -> None:
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", f"{ambient}\nx-other: custom-hdr-fake")
    errors = await _turn(_anthropic_proxy())
    name = ambient.split(":", 1)[0].lower()
    assert wire.hits == [
        sorted([f"{name}=ANTHROPIC_CUSTOM_HEADERS", "x-other=ANTHROPIC_CUSTOM_HEADERS"])
    ]
    sent = [(s["x-api-key"], s["authorization"], s["cf-aig-authorization"]) for s in wire.sent]
    assert sent == [tuple("ANTHROPIC_CUSTOM_HEADERS" if n == name else None for n in _AUTH_NAMES)]
    assert all("No API key" not in e for e in errors)


@pytest.mark.parametrize(
    "ambient",
    ["x-other: custom-hdr-fake", "X-Api-Key:   ", "Authorization:", "no colon custom-hdr-fake"],
)
async def test_custom_headers_without_auth_do_not_authenticate_anthropic(
    env: Any, wire: _Wire, ambient: str
) -> None:
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", ambient)
    errors = await _turn(_anthropic_proxy())
    assert wire.sent == []
    assert errors == ["No API key for provider: anthropic"]


@pytest.mark.parametrize("ambient", _CH_AUTH)
@pytest.mark.parametrize("provider", ["mygw", "fireworks", "minimax"])
async def test_custom_headers_never_authenticate_another_provider(
    env: Any, wire: _Wire, ambient: str, provider: str
) -> None:
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", ambient)
    errors = await _turn(_custom(provider))
    assert wire.sent == []
    assert errors == [f"No API key for provider: {provider}"]


async def test_anthropic_auth_token_still_rides_beside_custom_headers(
    env: Any, wire: _Wire
) -> None:
    """As on aab1f210 (the SDK read both): an auth header in the variable does not
    hold ``ANTHROPIC_AUTH_TOKEN`` back; only ``options.headers`` does."""

    env.setenv("ANTHROPIC_AUTH_TOKEN", "ant-auth-token-fake")
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: custom-hdr-fake")
    await _turn(_anthropic_proxy())
    assert wire.hits == [
        ["authorization=ANTHROPIC_AUTH_TOKEN", "x-api-key=ANTHROPIC_CUSTOM_HEADERS"]
    ]


def test_the_custom_headers_parse_matches_the_sdk(env: Any) -> None:
    """``read_env_custom_headers`` parses as ``anthropic/_client.py`` does."""

    raw = " X-Api-Key :  a:b \nno colon\n\nx-other:\r\nAuthorization: Bearer t"
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", raw)
    ours = _anthropic_client.read_env_custom_headers()
    assert ours == {"X-Api-Key": "a:b", "x-other": "", "Authorization": "Bearer t"}
    sdk = AsyncAnthropic(api_key="k", base_url=_GW)
    assert {k: sdk._custom_headers[k] for k in ours} == ours
    env.delenv("ANTHROPIC_CUSTOM_HEADERS")
    assert _anthropic_client.read_env_custom_headers() == {}


# Verify B1: the OAuth branch's ``env_custom_headers`` flag was unpinned (M16,
# dropping it from the OAuth client build, passed tests/providers + tests/oauth;
# rpc then sent no custom header on W11 / W13). Both OAuth sources.


@pytest.mark.parametrize(
    ("ambient", "hit"),
    [
        ("x-other: custom-hdr-fake", "x-other=ANTHROPIC_CUSTOM_HEADERS"),
        ("X-Api-Key: custom-hdr-fake", "x-api-key=ANTHROPIC_CUSTOM_HEADERS"),
    ],
)
@pytest.mark.parametrize("source", ["stored-login", "ANTHROPIC_OAUTH_TOKEN"])
async def test_anthropic_oauth_keeps_its_custom_headers(
    env: Any, wire: _Wire, ambient: str, hit: str, source: str
) -> None:
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", ambient)
    if source == "stored-login":
        await _turn(_anthropic_proxy(), api_key="sk-ant-oat01-given-fake")
        bearer = "authorization=oauth"
    else:
        env.setenv("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-env-fake")
        await _turn(_anthropic_proxy())
        bearer = "authorization=ANTHROPIC_OAUTH_TOKEN"
    assert wire.hits == [sorted([bearer, hit])]


# Codex category 4: either an explicit project or an explicit location keeps
# google-genai from taking GOOGLE_API_KEY / GEMINI_API_KEY as a Vertex
# express-mode key (``(project or location) and env_api_key``). A guard that
# demanded both passed every changed test file.


@pytest.mark.parametrize(
    "where", [{"project": "proj-fake"}, {"location": "us-central1"}], ids=["project", "location"]
)
async def test_a_keyless_vertex_client_with_one_of_project_or_location(
    env: Any, any_wire: _Wire, where: dict[str, str]
) -> None:
    env.setenv("GOOGLE_API_KEY", "goog-env-fake")
    env.setenv("GEMINI_API_KEY", "gemini-env-fake")
    client = _google_client.create_vertex_client(
        base_url=_GW, headers={"Authorization": "Bearer gw-header-fake"}, **where
    )
    assert client._api_client.api_key is None
    with pytest.raises(Exception):  # noqa: B017 - the recorder answers 400
        await client.aio.models.generate_content(model="m1", contents="hi")
    assert any_wire.hits == [["authorization=gw-header"]]


# ================================================================ review round 3
#
# Verify B: a plausible wrong form of round 2's clause - reading
# ``ANTHROPIC_CUSTOM_HEADERS`` only when the request carries no headers of its
# own (``has_auth_header(opts.headers or <variable>)``, or ``... and not
# opts.headers and ...``) - passed every row, because each header-only row sent
# no ``options.headers``. A ``models.json`` override of ``anthropic`` that adds a
# NON-auth header (``x-team``) puts it there; the variable's auth header still
# authenticates that request, and both headers go out.


@pytest.mark.parametrize("ambient", _CH_AUTH)
async def test_a_non_auth_request_header_does_not_hide_the_custom_headers_auth(
    env: Any, wire: _Wire, ambient: str
) -> None:
    env.setenv("ANTHROPIC_CUSTOM_HEADERS", ambient)
    errors = await _turn(_anthropic_proxy(), headers={"x-team": "team-a"})
    name = ambient.split(":", 1)[0].lower()
    assert wire.hits == [[f"{name}=ANTHROPIC_CUSTOM_HEADERS"]]
    assert wire.x_team == ["team-a"]
    assert all("No API key" not in e for e in errors)


@pytest.mark.parametrize(
    "ambient", ["X-Api-Key: custom-hdr-fake", "authorization: Bearer custom-hdr-fake"]
)
async def test_rpc_header_only_anthropic_proxy_with_a_non_auth_override_header(
    env: Any, wire: _Wire, tmp_path: Path, ambient: str
) -> None:
    """The rpc / TUI turn path (verify3 Z01 / Z02): ``anthropic`` re-pointed in
    models.json with ``headers: {x-team: team-a}``, no key of any kind, its auth
    in ``ANTHROPIC_CUSTOM_HEADERS`` -> one request with both headers."""

    env.setenv("ANTHROPIC_CUSTOM_HEADERS", ambient)
    override = {"providers": {"anthropic": {"baseUrl": _GW, "headers": {"x-team": "team-a"}}}}
    errors = await _harness_turn(env, tmp_path, override, "anthropic", "claude-haiku-4-5")
    name = ambient.split(":", 1)[0].lower()
    assert wire.hits == [[f"{name}=ANTHROPIC_CUSTOM_HEADERS"]]
    assert wire.x_team == ["team-a"]
    assert [s["host"] for s in wire.sent] == ["custom-gw.invalid"]
    assert all("No API key" not in e for e in errors)
