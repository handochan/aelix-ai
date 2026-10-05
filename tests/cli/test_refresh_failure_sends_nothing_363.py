"""#363 review round 1, R1 — a stored OAuth whose refresh fails sends NOTHING.

A stored credential owns its provider (ADR-0251 §2.2; pi
``packages/ai/src/auth/resolve.ts:56-87`` and ``:142`` @ b223082bb throw
``ModelsError("oauth", "OAuth refresh failed for <id>")``). On 4cbff14e the
registry answered "no key", the CLI's auth callback read no key and no headers
as "no opinion", and the adapter read the environment itself - so with
``models.json`` re-pointing the provider at a gateway with its own ``apiKey``,
the exported vendor key went to the gateway (5dee21d1 had sent the models.json
key). Every row here records what reaches the wire through an
:class:`httpx.MockTransport` behind the real adapter, on each path that takes
the CLI's callback: a harness turn (print, json, rpc, the TUI and a delegated
child all run it), the compaction summary, the branch summary, and the real
``_async_main`` in print and json mode.

RED on 4cbff14e: each row recorded one request carrying the exported key.
Hermetic: fake keys, an isolated agent dir, no socket.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    _TurnState,
)
from aelix_agent_core.session.branch_summarization import generate_branch_summary
from aelix_agent_core.session.compaction import _generate_summary
from aelix_ai import Context, SimpleStreamOptions, clear_providers
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.oauth import AuthStorage, register_oauth_provider, unregister_oauth_provider
from aelix_ai.streaming import Model
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.entry import _make_auth_callback
from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
from aelix_coding_agent.model_registry import ModelRegistry

from tests.cli.test_launch_route_344 import _FakePipedStdin
from tests.env_sandbox import sandbox_home

_GW = "http://gw.invalid"
_EXPORTED = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
_MODEL = {"anthropic": "claude-haiku-4-5", "openai": "gpt-4o-mini"}
_TAGS = {
    "sk-exported-fake": "exported",
    "corp-gw-fake": "models.json",
    "access-fake": "oauth-access",
}


def _tag(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = raw[7:] if raw.lower().startswith("bearer ") else raw
    return _TAGS.get(token, "EMPTY" if not token else "other")


class _Wire:
    """Every request the adapters' clients send, with the credential as a tag."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(
            {
                "url": str(request.url),
                "x-api-key": _tag(request.headers.get("x-api-key")),
                "authorization": _tag(request.headers.get("authorization")),
            }
        )
        body = {"type": "error", "error": {"type": "invalid_request_error", "message": "rec"}}
        return httpx.Response(400, json=body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    """The real anthropic and openai-responses adapters over a MockTransport.

    ``api_key=None`` is passed through as the real factories pass it, so a
    client left to read the environment (the Anthropic SDK reads
    ``ANTHROPIC_API_KEY``) does so here too.
    """

    from aelix_ai.providers import anthropic as anthropic_mod
    from aelix_ai.providers import openai_responses as responses_mod
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    rec = _Wire()

    def _anthropic(**kw: Any) -> Any:
        kwargs: dict[str, Any] = {"max_retries": 0, "http_client": rec.client()}
        if kw.get("api_key") is not None:
            kwargs["api_key"] = kw["api_key"]
        if kw.get("base_url"):
            kwargs["base_url"] = kw["base_url"]
        if kw.get("default_headers"):
            kwargs["default_headers"] = kw["default_headers"]
        return AsyncAnthropic(**kwargs)

    def _openai(**kw: Any) -> Any:
        return AsyncOpenAI(
            api_key=kw.get("api_key") or "",
            base_url=kw.get("base_url"),
            default_headers=kw.get("default_headers"),
            max_retries=0,
            http_client=rec.client(),
        )

    monkeypatch.setattr(anthropic_mod, "create_async_client", _anthropic)
    monkeypatch.setattr(responses_mod, "create_async_client", _openai)
    clear_providers()
    anthropic_mod.register_all()
    responses_mod.register_all()
    yield rec
    clear_providers()


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    path = tmp_path / "agent"
    path.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(path))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(path / "settings.json"))
    (path / "settings.json").write_text(json.dumps({"retry": {"enabled": False}}), "utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return path


@pytest.fixture(params=["anthropic", "openai"])
def provider(request: pytest.FixtureRequest, agent: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A gateway with its own key, an expired stored OAuth whose refresh fails, an exported key."""

    name: str = request.param
    base = _GW if name == "anthropic" else f"{_GW}/v1"
    (agent / "models.json").write_text(
        json.dumps({"providers": {name: {"baseUrl": base, "apiKey": "corp-gw-fake"}}}), "utf-8"
    )
    (agent / "auth.json").write_text(
        json.dumps(
            {name: {"type": "oauth", "refresh": "r-fake", "access": "access-fake", "expires": 0}}
        ),
        "utf-8",
    )
    monkeypatch.setenv(_EXPORTED[name], "sk-exported-fake")

    async def _refresh_fails(_creds: Any) -> Any:
        raise RuntimeError("401 Unauthorized from the token endpoint (test)")

    register_oauth_provider(
        SimpleNamespace(  # type: ignore[arg-type]
            id=name,
            name="Probe OAuth",
            refresh_token=_refresh_fails,
            get_api_key=lambda c: c.access,
        )
    )
    yield name
    unregister_oauth_provider(name)


async def _registry(agent: Path) -> ModelRegistry:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    return registry


def _assert_refresh_error(text: str, provider: str) -> None:
    assert f"OAuth refresh failed for {provider}" in text
    assert "/login" in text


def _hi() -> Context:
    return Context(messages=[UserMessage(content=[TextContent(text="hi")])])


async def test_a_harness_turn_sends_nothing(agent: Path, provider: str, wire: _Wire) -> None:
    """The turn path print/json/rpc/TUI and a delegated child all take."""

    registry = await _registry(agent)
    model = resolve_model(_MODEL[provider], provider, registry)
    assert model.base_url.startswith(_GW)
    harness = AgentHarness(
        AgentHarnessOptions(model=model, get_api_key_and_headers=_make_auth_callback(registry))
    )
    stream = harness._make_stream_fn(lambda: _TurnState(system_prompt="", model=model))
    with pytest.raises(AgentHarnessError) as caught:
        async for _ in stream(model, _hi(), SimpleStreamOptions()):
            pass
    assert caught.value.code == "auth"
    _assert_refresh_error(str(caught.value), provider)
    assert wire.sent == []


async def test_the_compaction_summary_sends_nothing(
    agent: Path, provider: str, wire: _Wire
) -> None:
    registry = await _registry(agent)
    model = resolve_model(_MODEL[provider], provider, registry)
    prep = SimpleNamespace(
        previous_summary=None, messages_to_summarize=[UserMessage(content=[TextContent(text="hi")])]
    )
    with pytest.raises(RuntimeError) as caught:
        await _generate_summary(model, _make_auth_callback(registry), prep, None)
    _assert_refresh_error(str(caught.value), provider)
    assert wire.sent == []


async def test_the_branch_summary_sends_nothing(agent: Path, provider: str, wire: _Wire) -> None:
    registry = await _registry(agent)
    model = resolve_model(_MODEL[provider], provider, registry)
    entries = [
        SimpleNamespace(type="message", message=UserMessage(content=[TextContent(text="hi")]))
    ]
    with pytest.raises(RuntimeError) as caught:
        await generate_branch_summary(model, _make_auth_callback(registry), entries)
    _assert_refresh_error(str(caught.value), provider)
    assert wire.sent == []


@pytest.mark.parametrize("mode_flags", [["-p"], ["--mode", "json", "-p"]], ids=["print", "json"])
async def test_the_cli_sends_nothing(
    agent: Path,
    provider: str,
    wire: _Wire,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode_flags: list[str],
) -> None:
    """The real ``_async_main``: a failed run that names the refresh and /login."""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    code = await entry_mod._async_main(
        ["--no-session", "--provider", provider, "--model", _MODEL[provider], *mode_flags, "hi"]
    )
    captured = capsys.readouterr()
    assert wire.sent == []
    assert code != 0
    _assert_refresh_error(captured.out + captured.err, provider)


async def test_a_valid_stored_oauth_still_owns_the_provider(
    agent: Path, wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: an unexpired OAuth token is what goes out - not the gateway key, not the env."""

    (agent / "models.json").write_text(
        json.dumps({"providers": {"openai": {"baseUrl": f"{_GW}/v1", "apiKey": "corp-gw-fake"}}}),
        "utf-8",
    )
    (agent / "auth.json").write_text(
        json.dumps(
            {
                "openai": {
                    "type": "oauth",
                    "refresh": "r",
                    "access": "access-fake",
                    "expires": 9999999999999,
                }
            }
        ),
        "utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    register_oauth_provider(
        SimpleNamespace(id="openai", name="Probe OAuth", get_api_key=lambda c: c.access)  # type: ignore[arg-type]
    )
    try:
        registry = await _registry(agent)
        model: Model = resolve_model("gpt-4o-mini", "openai", registry)
        harness = AgentHarness(
            AgentHarnessOptions(model=model, get_api_key_and_headers=_make_auth_callback(registry))
        )
        stream = harness._make_stream_fn(lambda: _TurnState(system_prompt="", model=model))
        async for _ in stream(model, _hi(), SimpleStreamOptions()):
            pass
    finally:
        unregister_oauth_provider("openai")
    assert [s["authorization"] for s in wire.sent] == ["oauth-access"]


# ── Review round 2 ──────────────────────────────────────────────────────────


async def test_the_turn_prefix_summary_sends_nothing(
    agent: Path, provider: str, wire: _Wire
) -> None:
    """Review round 2 (verify M16): the split-turn prefix summary takes the callback too."""

    from aelix_agent_core.session.compaction import _generate_turn_prefix_summary

    registry = await _registry(agent)
    model = resolve_model(_MODEL[provider], provider, registry)
    with pytest.raises(RuntimeError) as caught:
        await _generate_turn_prefix_summary(
            model,
            _make_auth_callback(registry),
            [UserMessage(content=[TextContent(text="hi")])],
        )
    _assert_refresh_error(str(caught.value), provider)
    assert wire.sent == []


# A stored auth.json entry that gives no key owns its provider too (review round 2:
# Codex pass 2 C1 - the "!true" entry is its exact input - and verify M9). Each is
# (id, provider, entry, OAuth provider to register: None, or "empty" for one whose
# get_api_key gives ""). RED on a79861ce: every one but the expired unregistered
# OAuth sent the exported key to the gateway.
_NO_KEY = [
    ("empty-literal-openai", "openai", {"type": "api_key", "key": ""}, None),
    ("empty-literal-anthropic", "anthropic", {"type": "api_key", "key": ""}, None),
    ("helper-true-openai", "openai", {"type": "api_key", "key": "!true"}, None),
    ("helper-true-anthropic", "anthropic", {"type": "api_key", "key": "!true"}, None),
    (
        "oauth-unregistered",
        "openai",
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 9999999999999},
        None,
    ),
    (
        "oauth-unregistered-expired",
        "openai",
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 0},
        None,
    ),
    (
        "oauth-gives-no-key",
        "openai",
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 9999999999999},
        "empty",
    ),
    ("unknown-type", "openai", {"type": "weird"}, None),
]


@pytest.fixture(params=[pytest.param(c, id=c[0]) for c in _NO_KEY])
def no_key_entry(
    request: pytest.FixtureRequest, agent: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    """A gateway with its own key, an auth.json entry that gives no key, an exported key."""

    _id, name, entry, oauth = request.param
    base = _GW if name == "anthropic" else f"{_GW}/v1"
    (agent / "models.json").write_text(
        json.dumps({"providers": {name: {"baseUrl": base, "apiKey": "corp-gw-fake"}}}), "utf-8"
    )
    (agent / "auth.json").write_text(json.dumps({name: entry}), "utf-8")
    monkeypatch.setenv(_EXPORTED[name], "sk-exported-fake")
    if oauth == "empty":
        register_oauth_provider(
            SimpleNamespace(id=name, name="Probe OAuth", get_api_key=lambda _c: "")  # type: ignore[arg-type]
        )
    elif entry["type"] == "oauth":
        from aelix_ai.oauth import get_oauth_provider

        assert get_oauth_provider(name) is None  # the record's provider is unregistered
    yield name
    if oauth == "empty":
        unregister_oauth_provider(name)


def _assert_stored_error(text: str, provider: str) -> None:
    assert f"The auth.json entry for {provider} " in text
    assert "/login" in text


async def test_a_stored_entry_without_a_key_sends_nothing_on_a_turn(
    agent: Path, no_key_entry: str, wire: _Wire
) -> None:
    provider = no_key_entry
    registry = await _registry(agent)
    model = resolve_model(_MODEL[provider], provider, registry)
    assert model.base_url.startswith(_GW)
    harness = AgentHarness(
        AgentHarnessOptions(model=model, get_api_key_and_headers=_make_auth_callback(registry))
    )
    stream = harness._make_stream_fn(lambda: _TurnState(system_prompt="", model=model))
    with pytest.raises(AgentHarnessError) as caught:
        async for _ in stream(model, _hi(), SimpleStreamOptions()):
            pass
    assert caught.value.code == "auth"
    _assert_stored_error(str(caught.value), provider)
    assert wire.sent == []


async def test_a_stored_entry_without_a_key_sends_no_summary(
    agent: Path, no_key_entry: str, wire: _Wire
) -> None:
    """Compaction, the split-turn prefix summary and the branch summary."""

    from aelix_agent_core.session.compaction import _generate_turn_prefix_summary

    provider = no_key_entry
    registry = await _registry(agent)
    model = resolve_model(_MODEL[provider], provider, registry)
    callback = _make_auth_callback(registry)
    hi = UserMessage(content=[TextContent(text="hi")])
    prep = SimpleNamespace(previous_summary=None, messages_to_summarize=[hi])
    for call in (
        lambda: _generate_summary(model, callback, prep, None),
        lambda: _generate_turn_prefix_summary(model, callback, [hi]),
        lambda: generate_branch_summary(
            model, callback, [SimpleNamespace(type="message", message=hi)]
        ),
    ):
        with pytest.raises(RuntimeError) as caught:
            await call()
        _assert_stored_error(str(caught.value), provider)
    assert wire.sent == []


@pytest.mark.parametrize("mode_flags", [["-p"], ["--mode", "json", "-p"]], ids=["print", "json"])
async def test_a_stored_entry_without_a_key_fails_the_cli(
    agent: Path,
    no_key_entry: str,
    wire: _Wire,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode_flags: list[str],
) -> None:
    provider = no_key_entry
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    code = await entry_mod._async_main(
        ["--no-session", "--provider", provider, "--model", _MODEL[provider], *mode_flags, "hi"]
    )
    captured = capsys.readouterr()
    assert wire.sent == []
    assert code != 0
    _assert_stored_error(captured.out + captured.err, provider)


async def test_a_stored_helper_that_fails_names_the_entry_and_sends_nothing(
    agent: Path, wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auth.json ``api_key`` whose ``!command`` FAILS (#363 rebase onto 547099f3).

    Before, it already sent nothing, but the error was the raw
    ``CalledProcessError`` text (``Command '['/bin/sh', '-c', ...]' returned
    non-zero exit status 3.``): it named neither the entry, ``/login`` nor the
    auth.json path the guide, the CHANGELOG and ADR-0251 §2.2 promise, and it
    repeated the command line, which may carry a key. Now it is the same
    :class:`StoredCredentialError` as an entry that gives no key, with the
    helper's error chained as its cause, and ``get_api_key_for_provider`` (the
    extension API) answers ``None`` instead of raising it.
    """

    import subprocess

    from aelix_ai.oauth import StoredCredentialError

    (agent / "models.json").write_text(
        json.dumps({"providers": {"openai": {"baseUrl": f"{_GW}/v1", "apiKey": "corp-gw-fake"}}}),
        "utf-8",
    )
    (agent / "auth.json").write_text(
        json.dumps({"openai": {"type": "api_key", "key": "!exit 3"}}), "utf-8"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    model = resolve_model(_MODEL["openai"], "openai", registry)
    harness = AgentHarness(
        AgentHarnessOptions(model=model, get_api_key_and_headers=_make_auth_callback(registry))
    )
    stream = harness._make_stream_fn(lambda: _TurnState(system_prompt="", model=model))
    with pytest.raises(AgentHarnessError) as caught:
        async for _ in stream(model, _hi(), SimpleStreamOptions()):
            pass
    text = str(caught.value)
    _assert_stored_error(text, "openai")
    assert "!command failed (exit status 3)" in text
    assert str(agent / "auth.json") in text
    assert "Command '" not in text and "'-c'" not in text
    assert wire.sent == []
    with pytest.raises(StoredCredentialError) as direct:
        await storage.get_api_key_cascade("openai", include_fallback=False, stored_owns=True)
    assert isinstance(direct.value.__cause__, subprocess.CalledProcessError)
    assert await registry.get_api_key_for_provider("openai") is None
