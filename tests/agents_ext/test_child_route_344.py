"""#344 / ADR-0249 (M3) — a delegated child never re-derives a user-defined route via OpenRouter.

A delegated child is a fresh ``python -m aelix_coding_agent`` that inherits the
parent's environment — ``OPENROUTER_API_KEY`` included — and loads NO extensions
by default (``inherit_extensions`` defaults to False → ``--no-extensions``). A
profile ``model: <provider>/<id>`` used to reach it as a bare ``--model``, so the
child's own cascade decided the route with less knowledge than the parent had:
an extension provider it never loaded went to OpenRouter.

The parent now spells out the route it resolved, ``--model <id> --provider
<provider>``, whenever the prefix names a provider its registry calls
user-defined (``resolver.child_model_flags``). These spawn a REAL child (the
``tests/agents_ext/test_print_channel_spawn.py`` real-child shape) against two
local recording listeners — the models.json provider's endpoint and
``OPENROUTER_BASE_URL`` — so "never an OpenRouter route" is observed on the wire,
not inferred from an argv. Fake keys only; nothing leaves 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.print_channel import PrintChannel, SpawnPlan, build_child_argv, build_child_env
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import child_model_flags
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.env_sandbox import child_env


class _Listener:
    """A local endpoint that records ``(path, model)`` and answers a 400."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.requests: list[tuple[str, str | None]] = []
        listener = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:  # quiet
                return None

            def do_POST(self) -> None:  # noqa: N802 — http.server API
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    model = json.loads(raw or b"{}").get("model")
                except ValueError:
                    model = None
                listener.requests.append((self.path, model))
                body = json.dumps({"error": {"message": f"LISTENER-{name}", "code": 400}}).encode()
                self.send_response(400)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def listeners() -> Iterator[tuple[_Listener, _Listener]]:
    endpoint, openrouter = _Listener("RP"), _Listener("OR")
    try:
        yield endpoint, openrouter
    finally:
        endpoint.close()
        openrouter.close()


async def _parent_registry(agent: Path, endpoint: _Listener) -> ModelRegistry:
    """The PARENT's registry: a models.json provider + an extension provider."""

    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "retryprobe": {
                        "api": "openai-completions",
                        "baseUrl": f"{endpoint.url}/v1",
                        "apiKey": "retryprobe-fake-literal",
                        "models": [{"id": "held-model"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    # The parent loaded this extension; the child (``--no-extensions``) will not.
    registry.register_provider(
        "extprov",
        ProviderConfigInput(
            api_key="ext-fake-literal",
            models={
                "m1": Model(
                    id="m1",
                    provider="extprov",
                    api="openai-completions",
                    base_url=f"{endpoint.url}/ext/v1",
                )
            },
        ),
    )
    return registry


def _resolved(tmp_path: Path, model: str) -> ResolvedProfile:
    path = tmp_path / "scout.md"
    path.write_text("You are a scout.", encoding="utf-8")
    profile = AgentProfile(
        name="scout",
        description="Reads things.",
        body="You are a scout. Answer briefly.",
        file_path=str(path),
        scope="user",
        tools=("read",),
        model=model,
    )
    return ResolvedProfile(
        name=profile.name, profile=profile, source_path=profile.file_path, scope=profile.scope
    )


async def _spawn(
    tmp_path: Path, model: str, listeners: tuple[_Listener, _Listener]
) -> tuple[list[str], Any]:
    endpoint, openrouter = listeners
    home = tmp_path / "home"
    agent = home / "agent"
    agent.mkdir(parents=True)
    (home / ".config").mkdir()
    registry = await _parent_registry(agent, endpoint)
    env = child_env(
        home,
        XDG_CONFIG_HOME=str(home / ".config"),
        AELIX_CODING_AGENT_DIR=str(agent),
        PI_OFFLINE="1",
        # What the parent's environment hands every child (``build_child_env``
        # copies it wholesale) — pointed at a local listener so a leak is SEEN.
        OPENROUTER_API_KEY="or-fake-literal",
        OPENROUTER_BASE_URL=f"{openrouter.url}/api/v1",
    )
    built: list[list[str]] = []

    def _argv(*args: Any, **kwargs: Any) -> list[str]:
        argv = build_child_argv(*args, **kwargs)
        built.append(argv)
        return argv

    repo = tmp_path / "repo"
    repo.mkdir()
    channel = PrintChannel(
        grace=1.0,
        model_registry=lambda: registry,
        argv_builder=_argv,
        env_builder=lambda profile: build_child_env(profile, base=env),
    )
    plan = SpawnPlan(
        id="sub-344",
        resolved=_resolved(tmp_path, model),
        task="Reply with exactly: pong",
        cwd=str(repo),
        parent_cwd=str(repo),
        permission_mode=PermissionMode.PLAN,
        timeout_ms=90_000,
    )
    result = await asyncio.wait_for(channel.run(plan), 120)
    assert len(built) == 1
    return built[0], result


def _model_flags(argv: list[str]) -> list[str]:
    out: list[str] = []
    for flag in ("--model", "--provider"):
        if flag in argv:
            out += [flag, argv[argv.index(flag) + 1]]
    return out


async def test_a_models_json_profile_model_reaches_its_endpoint_not_openrouter(
    tmp_path: Path, listeners: tuple[_Listener, _Listener]
) -> None:
    endpoint, openrouter = listeners
    argv, result = await _spawn(tmp_path, "retryprobe/held-model", listeners)
    # The wire first: on the old code the red line shows what OpenRouter received.
    assert openrouter.requests == [], "the child's turn went to OpenRouter"
    assert ("/v1/chat/completions", "held-model") in endpoint.requests
    assert _model_flags(argv) == ["--model", "held-model", "--provider", "retryprobe"]
    assert "--no-extensions" in argv
    # The listener answers 400, so the envelope is an error — naming the endpoint.
    assert result.ok is False
    assert "LISTENER-RP" in f"{result.summary}\n{result.details or ''}"


async def test_an_extension_profile_model_is_refused_by_the_child_not_sent_to_openrouter(
    tmp_path: Path, listeners: tuple[_Listener, _Listener]
) -> None:
    """The child has no extension, so it cannot reach ``extprov`` — and must say so.

    Before #344 the child re-derived ``extprov/m1`` through the inherited
    ``OPENROUTER_API_KEY`` and the prompt went to OpenRouter.
    """

    endpoint, openrouter = listeners
    argv, result = await _spawn(tmp_path, "extprov/m1", listeners)
    assert openrouter.requests == [], "the child's turn went to OpenRouter"
    assert endpoint.requests == []
    assert _model_flags(argv) == ["--model", "m1", "--provider", "extprov"]
    assert result.ok is False
    assert "extprov" in f"{result.summary}\n{result.details or ''}"


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
async def test_child_flags_split_only_user_defined_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_openrouter_key: bool
) -> None:
    """The flag rule on its own: catalogued and OpenRouter ids are left alone."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    agent = tmp_path / "agent"
    agent.mkdir()
    registry = await _parent_registry(agent, _FakeEndpoint())

    def flags(model: str | None, provider: str | None = None) -> list[str]:
        profile = AgentProfile(
            name="p",
            description="d",
            body="b",
            file_path="/p.md",
            scope="user",
            model=model,
            provider=provider,
        )
        return child_model_flags(profile, None, registry)

    assert flags("RetryProbe/held-model") == ["--model", "held-model", "--provider", "retryprobe"]
    assert flags("held-model") == ["--model", "held-model", "--provider", "retryprobe"]
    assert flags("openai/gpt-4o-mini") == ["--model", "openai/gpt-4o-mini"]
    assert flags("extprov/m1", "openrouter") == [
        "--model",
        "extprov/m1",
        "--provider",
        "openrouter",
    ]
    # No registry: exactly the profile's flags (the pre-#344 shape).
    profile = AgentProfile(
        name="p",
        description="d",
        body="b",
        file_path="/p.md",
        scope="user",
        model="retryprobe/held-model",
    )
    assert child_model_flags(profile) == ["--model", "retryprobe/held-model"]
    # A parent whose model came through without a provider is split the same way.
    parent = Model(id="retryprobe/held-model", provider="unknown")
    bare = AgentProfile(name="p", description="d", body="b", file_path="/p.md", scope="user")
    assert child_model_flags(bare, parent, registry) == [
        "--model",
        "held-model",
        "--provider",
        "retryprobe",
    ]


class _FakeEndpoint:
    url = "http://127.0.0.1:9"


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
async def test_child_flags_follow_the_route_the_parent_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_openrouter_key: bool
) -> None:
    """The child is split only when the PARENT's own resolve lands on a user-defined provider.

    Review of ``0fcc3333``: the split asked the rung-0 helper whether or not an
    OpenRouter key was set, while ``resolve_model`` ran rung 0 only with one —
    the child and the parent disagreed. The loop pins the agreement; the last
    assertion is the gateway row, which #362 / ADR-0250 (pi's order) puts on
    ``gw`` with or without the key.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "gw": {
                        "api": "openai-completions",
                        "baseUrl": "http://127.0.0.1:9/gw/v1",
                        "apiKey": "gw-fake-literal",
                        "models": [{"id": "openai/gpt-4o"}, {"id": "gw-only"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))

    for ref in ("openai/gpt-4o", "gw-only", "GW/gw-only"):
        profile = AgentProfile(
            name="p", description="d", body="b", file_path="/p.md", scope="user", model=ref
        )
        flags = child_model_flags(profile, None, registry)
        parent = resolve_model(ref, None, registry)
        pairs = dict(zip(flags[::2], flags[1::2], strict=True))
        child = resolve_model(pairs["--model"], pairs.get("--provider"), registry)
        assert (child.provider, child.id, child.base_url) == (
            parent.provider,
            parent.id,
            parent.base_url,
        ), (ref, flags)
    gateway_listed = child_model_flags(
        AgentProfile(
            name="p",
            description="d",
            body="b",
            file_path="/p.md",
            scope="user",
            model="openai/gpt-4o",
        ),
        None,
        registry,
    )
    # #362 / ADR-0250: with the key, ``gw`` is the one user-defined provider among
    # the route-authenticated raw matches; without it, the sole authenticated one
    # (its literal ``apiKey``) — pi's swap. Either way the parent lands on ``gw``,
    # which is user-defined, so the child is told so.
    assert gateway_listed == ["--model", "openai/gpt-4o", "--provider", "gw"]


async def test_a_profile_provider_is_passed_as_the_parent_matches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex second pass on ``ebfe411a`` (F1): the child got ``--provider openai``.

    A models.json ``OpenAI`` and an extension ``Groq``: the parent resolves a
    profile's ``provider: openai`` / ``provider: groq`` to them, so the child is
    told those spellings — a ``--no-extensions`` child handed ``groq`` would be
    the catalogue's ``groq``, the vendor's host. An exact spelling and one two
    of the user's providers share up to case pass unchanged.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    agent = tmp_path / "agent"
    agent.mkdir()
    custom = {
        "api": "openai-completions",
        "baseUrl": "http://127.0.0.1:9/custom/v1",
        "apiKey": "custom-fake-literal",
        "models": [{"id": "m1"}],
    }
    (agent / "models.json").write_text(
        json.dumps({"providers": {"OpenAI": custom, "Shared": custom, "SHARED": custom}}),
        encoding="utf-8",
    )
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    registry.register_provider(
        "Groq",
        ProviderConfigInput(
            api_key="ext-fake-literal",
            models={
                "m3": Model(
                    id="m3",
                    provider="Groq",
                    api="openai-completions",
                    base_url="http://127.0.0.1:9/ext/v1",
                )
            },
        ),
    )

    def flags(model: str | None, provider: str) -> list[str]:
        profile = AgentProfile(
            name="p",
            description="d",
            body="b",
            file_path="/p.md",
            scope="user",
            model=model,
            provider=provider,
        )
        return child_model_flags(profile, None, registry)

    assert flags("m1", "openai") == ["--model", "m1", "--provider", "OpenAI"]
    assert flags("m3", "groq") == ["--model", "m3", "--provider", "Groq"]
    assert flags(None, "OPENAI") == ["--provider", "OpenAI"]
    assert flags("m1", "OpenAI") == ["--model", "m1", "--provider", "OpenAI"]
    assert flags("m1", "shared") == ["--model", "m1", "--provider", "shared"]
