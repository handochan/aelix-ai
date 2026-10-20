"""#409 - a compaction's or branch summary's transient OAuth refresh failure is
retried, through the real path #379's rows run the turn on.

AuthStorage, the registry, the CLI's auth callback (``_make_auth_callback``),
the harness and the REAL Codex, Anthropic and Copilot refresh code over an
``httpx.MockTransport`` scripting the token endpoint (the rig of
``test_refresh_retry_379.py``). Before #409 the summarizers called the callback
once: the refresh's ``502`` failed ``/compact`` and ``/tree`` at once while the
turn retried it. Hermetic: fake tokens, an isolated agent dir, no socket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions, NavigateTreeOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli.entry import _make_auth_callback
from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
from aelix_coding_agent.model_registry import ModelRegistry

from tests.cli.test_refresh_retry_379 import (  # noqa: F401 - the fixtures are used by name
    _MODEL,
    _Endpoints,
    _retries,
    _store,
    agent,
    endpoints,
)

_PROVIDERS = ["openai-codex", "anthropic", "github-copilot"]


async def _harness(
    agent: Path,  # noqa: F811 - a path, not the fixture
    provider: str,
    session: Session,
) -> tuple[AgentHarness, list[Any]]:
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    harness = AgentHarness(
        AgentHarnessOptions(
            model=resolve_model(_MODEL[provider], provider, registry),
            session=session,
            get_api_key_and_headers=_make_auth_callback(registry),
        )
    )
    events: list[Any] = []
    harness.subscribe(events.append)
    return harness, events


async def _compactable() -> Session:
    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return session


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("cause", ["502", "connect"])
async def test_a_compactions_transient_refresh_failure_is_retried(
    agent: Path,  # noqa: F811 - the imported fixture
    endpoints: _Endpoints,  # noqa: F811
    provider: str,
    cause: str,
) -> None:
    _store(agent, provider, gateway_key=True)
    endpoints.plan = [cause, "200"]
    session = await _compactable()
    harness, events = await _harness(agent, provider, session)

    result = await harness.compact()

    assert "hello" in result.summary
    starts, ends = _retries(events)
    assert endpoints.token_hits == [cause, "200"]
    assert [(s.attempt, s.max_attempts) for s in starts] == [(1, 3)]
    assert f"OAuth refresh failed for {provider}" in starts[0].error_message
    assert [(e.success, e.attempt) for e in ends] == [(True, 1)]
    # Only the refreshed token reached a model endpoint: nothing was sent on the
    # stale one, the models.json key or an exported environment key meanwhile.
    assert [a["authorization"] or a["x-api-key"] for a in endpoints.model_auth] == ["refreshed"]
    assert any(e.type == "compaction" for e in await session.get_entries())


@pytest.mark.parametrize("provider", _PROVIDERS)
async def test_a_compactions_refused_refresh_fails_at_once_with_todays_message(
    agent: Path,  # noqa: F811 - the imported fixture
    endpoints: _Endpoints,  # noqa: F811
    provider: str,
) -> None:
    _store(agent, provider, gateway_key=True)
    endpoints.plan = ["401", "200"]
    harness, events = await _harness(agent, provider, await _compactable())

    with pytest.raises(RuntimeError) as caught:
        await harness.compact()

    text = str(caught.value)
    assert text.startswith(f"OAuth refresh failed for {provider}: ")
    assert text.endswith(f"Run /login to sign in to {provider} again.")
    assert endpoints.token_hits == ["401"]
    assert _retries(events) == ([], [])
    assert endpoints.model_auth == []


@pytest.mark.parametrize("provider", _PROVIDERS)
async def test_a_branch_summarys_transient_refresh_failure_is_retried(
    agent: Path,  # noqa: F811 - the imported fixture
    endpoints: _Endpoints,  # noqa: F811
    provider: str,
) -> None:
    _store(agent, provider, gateway_key=True)
    endpoints.plan = ["503", "200"]
    session = Session(MemorySessionStorage())
    root = await session.append_message(UserMessage(content=[TextContent(text="root")]))
    await session.append_message(
        AssistantMessage(content=[TextContent(text="branch a")], stop_reason="end_turn")
    )
    harness, events = await _harness(agent, provider, session)

    result = await harness.navigate_tree(root, NavigateTreeOptions(summarize=True))

    assert result.summary_entry is not None and "hello" in result.summary_entry.summary
    starts, ends = _retries(events)
    assert endpoints.token_hits == ["503", "200"]
    assert [(e.success, e.attempt) for e in ends] == [(True, 1)] and len(starts) == 1
    assert [a["authorization"] or a["x-api-key"] for a in endpoints.model_auth] == ["refreshed"]
