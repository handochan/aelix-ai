"""#379 — a stored OAuth refresh that failed on a transient cause is retried, as pi retries it.

The owner's decision (2026-10-06, ADR-0251 §4/§7): a refresh whose token
endpoint answered a status pi's retry pattern names (``429``, ``500``,
``502``-``504``, ``520``, ``524``) or could not be reached is retried by the
turn's auto-retry, with its budget, backoff and ``auto_retry_enabled``; a
``400``/``401``/``403``, a ``501``/``505`` and every other
:class:`StoredCredentialError` fail at once with the message they always had;
a refused refresh inside a retry sequence ends the sequence as a provider's
non-retryable error does (review round 2); and while the stored login exists,
the request never falls back to the environment or the ``models.json`` key
(#363, "a stored credential owns its provider"). pi: ``lazyStream`` turns the refresh's error
into an error assistant message (``packages/ai/src/api/lazy.ts:46-60`` @
1cedd3272) and ``isRetryableAssistantError`` retries ``502`` and ``fetch
failed`` but not ``401``/``403`` (``.omc/probes/363-live/fix3/pi/``).

On 8f7d98aa the refresh's raise became ``AgentHarnessError("auth")`` before any
assistant message existed, so the auto-retry never saw it: one token request,
no retry, the turn failed. Every row here runs the real path - AuthStorage, the
registry, the CLI's auth callback, the harness - with the REAL Codex, Anthropic
and Copilot refresh code over an ``httpx.MockTransport`` that scripts the token
endpoint's answers and records each model request's credential as a tag.

Hermetic: fake tokens, an isolated agent dir, no socket.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai import clear_providers
from aelix_ai.messages import AssistantMessage
from aelix_ai.oauth import AuthStorage, anthropic, openai_codex
from aelix_ai.utils.terminal_text import contains_steering_chars
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.entry import _make_auth_callback
from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
from aelix_coding_agent.model_registry import ModelRegistry

from tests.cli.test_launch_route_344 import _FakePipedStdin
from tests.env_sandbox import sandbox_home

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _jwt(tag: str) -> str:
    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-fake"}, "tag": tag}
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


_CODEX_STALE, _CODEX_FRESH = _jwt("stale"), _jwt("fresh")
_ANT_STALE, _ANT_FRESH = "sk-ant-oat01-stale-fake", "sk-ant-oat01-fresh-fake"
_GH_FRESH = "copilot-fresh-fake"
_TAGS = {
    _CODEX_STALE: "stale",
    _CODEX_FRESH: "refreshed",
    _ANT_STALE: "stale",
    _ANT_FRESH: "refreshed",
    _GH_FRESH: "refreshed",
    "gh-stored-fake": "stale",
    "sk-ant-env-fake": "ENV",
    "sk-ant-oauth-env-fake": "ENV",
    "sk-openai-env-fake": "ENV",
    "gh-env-fake": "ENV",
    "corp-gw-fake": "models.json",
}
_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
_TOKEN_URLS = {
    "openai-codex": openai_codex.TOKEN_URL,
    "anthropic": anthropic.TOKEN_URL,
    "github-copilot": _COPILOT_TOKEN_URL,
}
_MODEL = {
    "openai-codex": "gpt-5.1-codex-mini",
    "anthropic": "claude-haiku-4-5",
    "github-copilot": "claude-haiku-4.5",
}
#: A ``502`` page with the families #186 bounds, at the front where a cut keeps them.
_HOSTILE_502 = "\x1b[2J\x1b]52;c;cHduZWQ=\x07\x1b[?1049h<html>Bad Gateway</html>" + "B" * 4000


def _tag(raw: str | None) -> str | None:
    if raw is None:
        return None
    token = raw[7:] if raw.lower().startswith("bearer ") else raw
    return _TAGS.get(token, "EMPTY" if not token else "other")


def _codex_sse(model: str) -> bytes:
    item = {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "hello", "annotations": []}],
    }
    resp = {
        "id": "resp_fake",
        "object": "response",
        "status": "completed",
        "model": model,
        "output": [item],
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    }
    events = [
        {"type": "response.created", "response": {"id": "resp_fake"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
        {
            "type": "response.output_text.delta",
            "item_id": "msg_fake",
            "output_index": 0,
            "content_index": 0,
            "delta": "hello",
        },
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": resp},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _anthropic_sse(model: str) -> bytes:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


class _NotGzip(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"not-gzip"


class _CutShort(httpx.AsyncByteStream):
    """Review round 4 (Codex cat1): the status and headers arrived, then the body broke -
    ``{`` and EOF before ``Content-Length: 100`` (httpcore's own text), or a read timeout."""

    def __init__(self, error: type[httpx.TransportError]) -> None:
        self._error = error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{"
        raise self._error(
            "peer closed connection without sending complete message body"
            " (received 1 bytes, expected 100)"
        )


class _Endpoints:
    """The token endpoints (scripted) and the model endpoints (recorded)."""

    def __init__(self) -> None:
        self.plan: list[str] = ["200"]
        self.token_hits: list[str] = []
        self.model_auth: list[dict[str, str | None]] = []
        #: The model endpoints' answers, popped per request (empty: 200).
        self.model_plan: list[str] = []
        #: Run when the token endpoint answers (another process's logout, say).
        self.on_token: Any = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in _TOKEN_URLS.values():
            answer = self.plan.pop(0) if len(self.plan) > 1 else self.plan[0]
            self.token_hits.append(answer)
            if self.on_token is not None:
                self.on_token(answer)
            if answer == "connect":
                raise httpx.ConnectError("All connection attempts failed", request=request)
            if answer == "drop":
                raise httpx.RemoteProtocolError(
                    "Server disconnected without sending a response.", request=request
                )
            # Review round 3 (Codex cat4): the rest of httpx.NetworkError.
            if answer == "write":
                raise httpx.WriteError("Connection reset by peer", request=request)
            if answer == "close":
                raise httpx.CloseError("Connection closed", request=request)
            # Review round 4 (Codex mutant): the request could not be written in time.
            if answer == "wtimeout":
                raise httpx.WriteTimeout("write timed out", request=request)
            # Review round 4 (Codex cat1): the status arrived, then the body broke.
            if answer.startswith(("tr", "rt")):
                error = httpx.RemoteProtocolError if answer.startswith("tr") else httpx.ReadTimeout
                return httpx.Response(
                    int(answer[2:]),
                    headers={"content-type": "application/json", "content-length": "100"},
                    stream=_CutShort(error),
                )
            # Review round 4: the same status with an EMPTY body - the text an unreadable one gets.
            if answer.startswith("e") and answer[1:].isdigit():
                return httpx.Response(int(answer[1:]), content=b"")
            # Review round 3 (Codex cat1): a status whose gzip body is not gzip.
            if answer.startswith("gz"):
                return httpx.Response(
                    int(answer[2:]),
                    headers={"content-encoding": "gzip", "content-type": "application/json"},
                    stream=_NotGzip(),
                )
            # Review round 3 (Codex cat1): a refusal whose body names 502.
            if answer == "401-502":
                return httpx.Response(401, json={"error": "invalid_grant", "id": "502"})
            if answer == "hostile502":
                return httpx.Response(502, content=_HOSTILE_502.encode(), headers={"content-type": "text/html"})
            status = int(answer)
            if status != 200:
                return httpx.Response(status, json={"error": "invalid_grant" if status < 500 else "unavailable"})
            if url == _COPILOT_TOKEN_URL:
                return httpx.Response(200, json={"token": _GH_FRESH, "expires_at": 9_999_999_999})
            access = _ANT_FRESH if url == anthropic.TOKEN_URL else _CODEX_FRESH
            return httpx.Response(200, json={"access_token": access, "refresh_token": "r-next", "expires_in": 3600})
        self.model_auth.append(
            {
                "authorization": _tag(request.headers.get("authorization")),
                "x-api-key": _tag(request.headers.get("x-api-key")),
            }
        )
        if self.model_plan and (status := int(self.model_plan.pop(0))) != 200:
            return httpx.Response(status, json={"error": {"type": "api_error", "message": "upstream bad gateway"}})
        body = json.loads(request.content or b"{}")
        if url.endswith("/codex/responses"):
            return httpx.Response(200, content=_codex_sse(body.get("model", "")), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, content=_anthropic_sse(body.get("model", "")), headers={"content-type": "text/event-stream"})


@pytest.fixture
def endpoints(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Endpoints]:
    """Every ``httpx.AsyncClient`` (the OAuth refreshes, the Codex adapter) and the
    Anthropic SDK's client route to one recorder."""

    from aelix_ai.providers import anthropic as anthropic_mod
    from aelix_ai.providers import openai_codex_responses as codex_mod
    from anthropic import AsyncAnthropic

    rec = _Endpoints()

    class _Routed(_REAL_ASYNC_CLIENT):  # a real subclass: the SDKs isinstance-check it
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(rec.handler)
            super().__init__(*args, **kwargs)

    def _anthropic(**kw: Any) -> Any:
        kwargs: dict[str, Any] = {"max_retries": 0, "http_client": _Routed()}
        for name in ("api_key", "auth_token", "base_url", "default_headers"):
            if kw.get(name) is not None:
                kwargs[name] = kw[name]
        return AsyncAnthropic(**kwargs)

    monkeypatch.setattr(anthropic_mod, "create_async_client", _anthropic)
    monkeypatch.setattr(httpx, "AsyncClient", _Routed)
    clear_providers()
    anthropic_mod.register_all()
    codex_mod.register_all()
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
    (path / "settings.json").write_text("{}", "utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    # Every environment key the cascade or an adapter could read, exported: none may be sent.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-fake")
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oauth-env-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-env-fake")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "gh-env-fake")
    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    return path


def _store(agent: Path, provider: str, *, gateway_key: bool = False) -> None:
    """An EXPIRED stored OAuth for ``provider`` (and optionally a models.json key)."""

    stale = {
        "openai-codex": {"access": _CODEX_STALE, "accountId": "acct-fake"},
        "anthropic": {"access": _ANT_STALE},
        "github-copilot": {"access": "tid=stale-fake", "refresh": "gh-stored-fake"},
    }[provider]
    entry = {"type": "oauth", "refresh": "r-fake", "expires": 0, **stale}
    (agent / "auth.json").write_text(json.dumps({provider: entry}), "utf-8")
    providers: dict[str, Any] = {}
    if gateway_key:
        providers[provider] = {"apiKey": "corp-gw-fake"}
    (agent / "models.json").write_text(json.dumps({"providers": providers}), "utf-8")


async def _harness(agent: Path, provider: str) -> tuple[AgentHarness, list[Any]]:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    model = resolve_model(_MODEL[provider], provider, registry)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=model,
            session=Session(MemorySessionStorage()),
            get_api_key_and_headers=_make_auth_callback(registry),
        )
    )
    harness._state.auto_compaction_enabled = False
    events: list[Any] = []
    harness.subscribe(events.append)
    return harness, events


def _retries(events: list[Any]) -> tuple[list[AutoRetryStartEvent], list[AutoRetryEndEvent]]:
    starts = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    ends = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    return starts, ends


def _last_assistant(harness: AgentHarness) -> AssistantMessage:
    return next(m for m in reversed(harness._state.messages) if isinstance(m, AssistantMessage))


_PROVIDERS = ["openai-codex", "anthropic", "github-copilot"]


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize(
    "cause",
    [
        *("502", "503", "500", "504", "520", "524", "429"),
        *("connect", "drop", "write", "close", "wtimeout"),
        # A status that arrived decides, whatever its body does (rounds 3 and 4).
        *("gz502", "tr502", "rt502", "tr429"),
    ],
)
async def test_a_transient_refresh_failure_is_retried_and_sends_the_refreshed_token(
    agent: Path, endpoints: _Endpoints, provider: str, cause: str
) -> None:
    _store(agent, provider, gateway_key=True)
    endpoints.plan = [cause, "200"]
    harness, events = await _harness(agent, provider)

    await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == [cause, "200"]
    assert [(s.attempt, s.max_attempts) for s in starts] == [(1, 3)]
    assert f"OAuth refresh failed for {provider}" in starts[0].error_message
    # Review round 2: the turn retries it, so it does not send the user to /login (pi's text).
    assert "/login" not in starts[0].error_message
    assert [(e.success, e.attempt) for e in ends] == [(True, 1)]
    # Only the refreshed token ever reached a model endpoint: not the stale one,
    # not the models.json key, not an exported environment key.
    assert [a["authorization"] or a["x-api-key"] for a in endpoints.model_auth] == ["refreshed"]
    assert _last_assistant(harness).stop_reason not in ("error", "aborted")


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("status", ["400", "401", "403", "501", "505"])
async def test_a_refused_refresh_fails_at_once_with_todays_message(
    agent: Path, endpoints: _Endpoints, provider: str, status: str
) -> None:
    _store(agent, provider, gateway_key=True)
    endpoints.plan = [status, "200"]
    harness, events = await _harness(agent, provider)

    with pytest.raises(AgentHarnessError) as caught:
        await harness.prompt("hi")

    assert caught.value.code == "auth"
    text = str(caught.value)
    assert text.startswith(f"get_api_key_and_headers failed: OAuth refresh failed for {provider}: ")
    assert text.endswith(f"Run /login to sign in to {provider} again.")
    assert status in text
    assert endpoints.token_hits == [status]
    assert _retries(events) == ([], [])
    assert endpoints.model_auth == []


@pytest.mark.parametrize("provider", _PROVIDERS)
async def test_the_retry_budget_is_the_turns_and_nothing_falls_back(
    agent: Path, endpoints: _Endpoints, provider: str
) -> None:
    """Always 502: the first attempt and three retries (2s/4s/8s at the real base delay), then the
    refresh's error ends the turn - and no request went out on any other credential."""

    _store(agent, provider, gateway_key=True)
    endpoints.plan = ["502"]
    harness, events = await _harness(agent, provider)

    await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == ["502"] * 4
    assert [s.attempt for s in starts] == [1, 2, 3]
    assert [s.delay_ms for s in starts] == [1, 2, 4]  # base * 2^(n-1), the same backoff
    assert [(e.success, e.attempt) for e in ends] == [(False, 3)]
    assert f"OAuth refresh failed for {provider}" in (ends[0].final_error or "")
    assert endpoints.model_auth == []
    last = _last_assistant(harness)
    assert last.stop_reason == "error"
    assert last.error_message is not None
    assert last.error_message.startswith(f"OAuth refresh failed for {provider}: ")
    assert "502" in last.error_message


async def test_retry_disabled_ends_the_turn_on_the_first_failure(
    agent: Path, endpoints: _Endpoints
) -> None:
    """``auto_retry_enabled`` (rpc ``set_auto_retry``) governs this retry as it does every other."""

    _store(agent, "openai-codex")
    endpoints.plan = ["502", "200"]
    harness, events = await _harness(agent, "openai-codex")
    harness.set_auto_retry_enabled(False)

    await harness.prompt("hi")

    assert endpoints.token_hits == ["502"]
    assert _retries(events) == ([], [])
    last = _last_assistant(harness)
    assert last.stop_reason == "error"
    assert last.error_message is not None
    assert last.error_message.startswith("OAuth refresh failed for openai-codex: ")
    assert endpoints.model_auth == []


async def test_a_hostile_502_page_stays_bounded_and_inert(
    agent: Path, endpoints: _Endpoints
) -> None:
    """#186 still applies: the retried message quotes the page, it does not carry it."""

    _store(agent, "openai-codex")
    endpoints.plan = ["hostile502", "200"]
    harness, events = await _harness(agent, "openai-codex")

    await harness.prompt("hi")

    starts, _ends = _retries(events)
    assert len(starts) == 1
    shown = starts[0].error_message
    assert contains_steering_chars(_HOSTILE_502), "positive control"
    assert not contains_steering_chars(shown)
    assert "Bad Gateway" in shown
    assert len(shown) < 700, len(shown)  # one quotation (<= 513) plus aelix's own words
    assert [a["authorization"] for a in endpoints.model_auth] == ["refreshed"]


async def test_a_stored_credential_error_is_still_not_retried(
    agent: Path, endpoints: _Endpoints
) -> None:
    """Control: an empty-resolving stored api_key (``!true``) fails at once, as on 8f7d98aa."""

    (agent / "auth.json").write_text(json.dumps({"anthropic": {"type": "api_key", "key": "!true"}}), "utf-8")
    (agent / "models.json").write_text(json.dumps({"providers": {}}), "utf-8")
    harness, events = await _harness(agent, "anthropic")

    with pytest.raises(AgentHarnessError) as caught:
        await harness.prompt("hi")

    assert caught.value.code == "auth"
    assert "The auth.json entry for anthropic " in str(caught.value)
    assert _retries(events) == ([], [])
    assert endpoints.model_auth == []


@pytest.mark.parametrize(("plan", "retried"), [(["502", "200"], True), (["401"], False)])
async def test_the_registry_says_why_a_retry_may_succeed(
    agent: Path, endpoints: _Endpoints, plan: list[str], retried: bool
) -> None:
    for provider in _PROVIDERS:
        _store(agent, provider)
        endpoints.plan = list(plan)
        storage = AuthStorage(agent / "auth.json")
        await storage.load()
        registry = ModelRegistry.create(storage, str(agent / "models.json"))
        auth = await registry.get_api_key_and_headers(resolve_model(_MODEL[provider], provider, registry))
        assert auth.ok is False
        assert (auth.retry_reason is not None) is retried, (provider, auth)
        assert auth.api_key is None


@pytest.mark.parametrize(
    ("plan", "code", "retried"),
    [(["502", "200"], 0, True), (["401"], 1, False)],
    ids=["502-then-200", "401"],
)
async def test_the_cli_in_json_mode(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plan: list[str],
    code: int,
    retried: bool,
) -> None:
    """The real ``_async_main``: json shows ``auto_retry_start``/``auto_retry_end`` as for a provider 502."""

    _store(agent, "openai-codex")
    endpoints.plan = list(plan)
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    rc = await entry_mod._async_main(
        ["--no-session", "--provider", "openai-codex", "--model", _MODEL["openai-codex"], "--mode", "json", "-p", "hi"]
    )
    out = capsys.readouterr()
    types = [json.loads(line).get("type") for line in out.out.splitlines() if line.startswith("{")]
    assert rc == code
    assert types.count("auto_retry_start") == (1 if retried else 0)
    assert types.count("auto_retry_end") == (1 if retried else 0)
    assert endpoints.token_hits == plan
    if not retried:
        assert "OAuth refresh failed for openai-codex" in out.err
        assert endpoints.model_auth == []
    else:
        assert [a["authorization"] for a in endpoints.model_auth] == ["refreshed"]



# === review round 2 ==========================================================


async def _harness_and_storage(agent: Path, provider: str) -> tuple[AgentHarness, list[Any], AuthStorage]:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    model = resolve_model(_MODEL[provider], provider, registry)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=model,
            session=Session(MemorySessionStorage()),
            get_api_key_and_headers=_make_auth_callback(registry),
        )
    )
    harness._state.auto_compaction_enabled = False
    events: list[Any] = []
    harness.subscribe(events.append)
    return harness, events, storage


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("refusal", ["401", "400"])
async def test_a_refused_refresh_inside_a_retry_closes_the_sequence(
    agent: Path, endpoints: _Endpoints, provider: str, refusal: str
) -> None:
    """[502, 401]: a refresh token rotated by a 502'd refresh whose answer was lost gets
    invalid_grant next. The sequence ends as for a provider's non-retryable error: one
    auto_retry_end(False, 1) whose text is today's refused message, byte for byte, the
    counter at 0, nothing sent."""

    _store(agent, provider, gateway_key=True)
    endpoints.plan = ["502", refusal]
    harness, events = await _harness(agent, provider)

    await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == ["502", refusal]
    assert [s.attempt for s in starts] == [1]
    assert [(e.success, e.attempt) for e in ends] == [(False, 1)]
    final = ends[0].final_error or ""
    assert final.startswith(f"get_api_key_and_headers failed: OAuth refresh failed for {provider}: ")
    assert final.endswith(f"Run /login to sign in to {provider} again.")
    assert harness._retry_attempt == 0
    assert _last_assistant(harness).error_message == final
    assert endpoints.model_auth == []


async def test_after_a_closed_sequence_the_next_turn_gets_the_full_budget(
    agent: Path, endpoints: _Endpoints
) -> None:
    """Codex's cat3 row on the real path: [502, 401], the user logs in again, and the next
    turn's model answers 502 three times then 200 - it still succeeds (attempts 1, 2, 3).
    On a46092fd the counter stayed at 1, the turn started at attempt 2 and failed."""

    _store(agent, "anthropic")
    endpoints.plan = ["502", "401"]
    harness, events, storage = await _harness_and_storage(agent, "anthropic")
    await harness.prompt("first")
    assert endpoints.token_hits == ["502", "401"]

    relogin = {"type": "oauth", "refresh": "r-relogin", "access": _ANT_FRESH, "expires": 9_999_999_999_999}
    (agent / "auth.json").write_text(json.dumps({"anthropic": relogin}), "utf-8")
    await storage.load()
    events.clear()
    endpoints.model_plan = ["502", "502", "502", "200"]

    await harness.prompt("second")

    starts, ends = _retries(events)
    assert [s.attempt for s in starts] == [1, 2, 3]
    assert [(e.success, e.attempt) for e in ends] == [(True, 3)]
    assert [a["authorization"] or a["x-api-key"] for a in endpoints.model_auth] == ["refreshed"] * 4
    assert _last_assistant(harness).stop_reason not in ("error", "aborted")


async def test_a_logout_during_the_failing_refresh_sends_what_a_new_turn_would(
    agent: Path, endpoints: _Endpoints
) -> None:
    """Pinned as it is (Codex r1 cat1, kept; shape B of review round 3): another process
    empties auth.json while the refresh request whose answer is the 502 is under way. The
    failed refresh re-reads the store (P-142), finds no login, and the retry resolves the
    order without it - here the exported ANTHROPIC_API_KEY, as a new turn after that logout
    does - and no second refresh is made."""

    _store(agent, "anthropic")
    endpoints.plan = ["502"]

    def logout(_answer: str) -> None:
        (agent / "auth.json").write_text("{}", "utf-8")

    endpoints.on_token = logout
    harness, events = await _harness(agent, "anthropic")

    await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == ["502"]
    assert [s.attempt for s in starts] == [1]
    assert [(e.success, e.attempt) for e in ends] == [(True, 1)]
    assert [a["x-api-key"] for a in endpoints.model_auth] == ["ENV"]


async def test_the_cli_in_json_mode_after_a_refused_retry(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real ``_async_main``, [502, 401]: the sequence closes on the wire (one start, one
    end with success false), and every assistant message_end has its message_start."""

    _store(agent, "openai-codex")
    endpoints.plan = ["502", "401"]
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    rc = await entry_mod._async_main(
        ["--no-session", "--provider", "openai-codex", "--model", _MODEL["openai-codex"], "--mode", "json", "-p", "hi"]
    )
    out = capsys.readouterr()
    lines = [json.loads(line) for line in out.out.splitlines() if line.startswith("{")]
    types = [e.get("type") for e in lines]
    assert types.count("auto_retry_start") == 1
    ends = [e for e in lines if e.get("type") == "auto_retry_end"]
    assert [(e.get("success"), e.get("attempt")) for e in ends] == [(False, 1)]
    assistant = [e["type"] for e in lines if e.get("type") in ("message_start", "message_end")
                 and (e.get("message") or {}).get("role") == "assistant"]
    assert assistant == ["message_start", "message_end"] * 2
    assert endpoints.model_auth == []
    # What --mode json does for every turn that ends on an error message (a model's 400
    # after a 502 included): rc 0, the error in the last message_end (print_mode.py text
    # branch only sets 1). A refusal on the FIRST attempt still raises: rc 1, as before.
    assert rc == 0, (rc, out.err)


# === review round 3 ==========================================================


@pytest.mark.parametrize("provider", ["anthropic", "openai-codex"])
async def test_a_logout_while_a_retry_waits_ends_the_retry_and_sends_nothing(
    agent: Path, endpoints: _Endpoints, provider: str
) -> None:
    """Shape A (verify r2's blocking item): auth.json emptied during the backoff, after the
    502 was handled. The retry's refresh re-reads the store under its lock, finds no login
    and gives no key; the in-memory entry is still the expired login, so the cascade raises
    the stored-entry error: no request, no fall to the next key, and the retry ends with that
    text (it still says to remove an entry that is already gone - a follow-up). The next turn
    reads the store afresh and uses the next key in the order (anthropic: the exported one)."""

    _store(agent, provider)
    endpoints.plan = ["502"]
    harness, events = await _harness(agent, provider)

    def logout_on_retry_start(event: Any) -> None:
        if isinstance(event, AutoRetryStartEvent):
            (agent / "auth.json").write_text("{}", "utf-8")

    harness.subscribe(logout_on_retry_start)
    await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == ["502"]
    assert [s.attempt for s in starts] == [1]
    assert [(e.success, e.attempt) for e in ends] == [(False, 1)]
    final = ends[0].final_error or ""
    assert final.startswith(
        f"get_api_key_and_headers failed: The auth.json entry for {provider} is an OAuth login that gave no key."
    )
    assert endpoints.model_auth == []
    assert harness._retry_attempt == 0

    if provider == "anthropic":
        events.clear()
        await harness.prompt("again")
        assert endpoints.token_hits == ["502"]
        assert [a["x-api-key"] for a in endpoints.model_auth] == ["ENV"]
        assert _retries(events) == ([], [])


async def test_a_logout_during_the_failing_codex_refresh_sends_nothing(
    agent: Path, endpoints: _Endpoints
) -> None:
    """Shape B for openai-codex: the order without the login has nothing a Codex request can
    use, so the retry sends nothing and ends with the adapter's own sign-in error."""

    _store(agent, "openai-codex")
    endpoints.plan = ["502"]
    endpoints.on_token = lambda _answer: (agent / "auth.json").write_text("{}", "utf-8")
    harness, events = await _harness(agent, "openai-codex")

    with pytest.raises(RuntimeError, match="No OAuth token for openai-codex"):
        await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == ["502"]
    assert [s.attempt for s in starts] == [1]
    assert [(e.success, e.attempt) for e in ends] == [(False, 1)]
    assert endpoints.model_auth == []
    assert harness._retry_attempt == 0


def _json_round_trip_hook(harness: AgentHarness) -> None:
    """Codex r2 cat1: an extension that rebuilds error_message through JSON (same text, a
    plain str) and returns the message as its replacement."""

    import dataclasses

    from aelix_agent_core.harness.hooks import MessageEndEventResult

    async def round_trip(event: Any, _ctx: Any) -> Any:
        message = event.message
        if isinstance(message, AssistantMessage) and message.error_message:
            text = json.loads(json.dumps(message.error_message))
            return MessageEndEventResult(message=dataclasses.replace(message, error_message=text))
        return None

    harness.hooks.on("message_end", round_trip)


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("hook", [False, True], ids=["no-hook", "json-hook"])
async def test_a_refusal_naming_502_is_not_retried_whatever_a_hook_does(
    agent: Path, endpoints: _Endpoints, provider: str, hook: bool
) -> None:
    """Codex r2 cat1, counterexample 1: [502, 401 whose body names 502]. With the hook,
    7b0207bb read the plain-str text, matched '502' and retried the refusal to success."""

    _store(agent, provider)
    endpoints.plan = ["502", "401-502", "200"]
    harness, events = await _harness(agent, provider)
    if hook:
        _json_round_trip_hook(harness)

    await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == ["502", "401-502"]
    assert [s.attempt for s in starts] == [1]
    assert [(e.success, e.attempt) for e in ends] == [(False, 1)]
    assert endpoints.model_auth == []


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("cause", ["drop", "connect"])
async def test_a_disconnect_is_retried_whatever_a_hook_does(
    agent: Path, endpoints: _Endpoints, provider: str, cause: str
) -> None:
    """Codex r2 cat1, counterexample 2: with the hook, 7b0207bb read httpx's text, which
    matches no pattern, and did not retry the disconnect at all."""

    _store(agent, provider)
    endpoints.plan = [cause, "200"]
    harness, events = await _harness(agent, provider)
    _json_round_trip_hook(harness)

    await harness.prompt("hi")

    starts, ends = _retries(events)
    assert endpoints.token_hits == [cause, "200"]
    assert [s.attempt for s in starts] == [1]
    assert [(e.success, e.attempt) for e in ends] == [(True, 1)]
    assert [a["authorization"] or a["x-api-key"] for a in endpoints.model_auth] == ["refreshed"]


@pytest.mark.parametrize("provider", _PROVIDERS)
async def test_a_2xx_whose_body_cannot_be_decoded_still_fails_at_once(
    agent: Path, endpoints: _Endpoints, provider: str
) -> None:
    """Control for the gz502 rows: a 200 whose body is not gzip carries no status to keep
    and is not a token - one refresh, no retry, today's refused shape (the /login hint)."""

    _store(agent, provider)
    endpoints.plan = ["gz200"]
    harness, events = await _harness(agent, provider)

    with pytest.raises(AgentHarnessError) as caught:
        await harness.prompt("hi")

    assert endpoints.token_hits == ["gz200"]
    assert _retries(events) == ([], [])
    assert str(caught.value).endswith(f"Run /login to sign in to {provider} again.")


# === review round 4: once a non-2xx status arrived, that status decides ============


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize(
    ("cause", "plain"),
    [
        # Codex r4 cat1: on e946bf53 a cut-short 401/501 read as a dropped connection and the
        # REFUSED refresh was retried - the next 200 signed the user back in.
        ("tr401", "e401"),
        ("rt401", "e401"),
        ("tr501", "e501"),
        ("tr400", "e400"),
        # Codex r4 cat2: a broken-gzip refusal's text is the empty-body refusal's, byte for byte.
        ("gz401", "e401"),
        ("gz403", "e403"),
    ],
)
async def test_a_refusal_whose_body_cannot_be_read_is_refused_with_the_empty_body_text(
    agent: Path, endpoints: _Endpoints, provider: str, cause: str, plain: str
) -> None:
    texts: list[str] = []
    for answer in (cause, plain):
        _store(agent, provider, gateway_key=True)
        endpoints.plan = [answer, "200"]
        endpoints.token_hits.clear()
        harness, events = await _harness(agent, provider)

        with pytest.raises(AgentHarnessError) as caught:
            await harness.prompt("hi")

        assert caught.value.code == "auth"
        assert endpoints.token_hits == [answer]
        assert _retries(events) == ([], [])
        assert endpoints.model_auth == []
        texts.append(str(caught.value))
    assert texts[0] == texts[1]
    assert texts[0].endswith(f"Run /login to sign in to {provider} again.")
    assert plain[1:] in texts[0]
    assert "peer closed" not in texts[0]
    assert "decompress" not in texts[0]
