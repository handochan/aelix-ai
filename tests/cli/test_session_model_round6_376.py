"""#376 review round 6 — the no-adapter arm is judged on a model WITH a base URL.

Verify r5 (``.omc/probes/376-live/r5verify/``) replaced the last line of
``_runs_without_asking_auth`` (``return api is None or api in apis``) with
``return True`` and the whole suite stayed green: every no-adapter row used a
model whose ``base_url`` is ``''`` (``Model(id="model-x", provider="newlab")``,
the ``''/''`` placeholder), so the base-URL arm refused it first and the adapter
arm was never the one deciding. Live under that mutant an RPC prompt on
``mistral/mistral-large-latest`` (``api='mistral-conversations'``, base URL
``https://api.mistral.ai``) was acknowledged ``success: true``, written, and
failed with ``No provider registered for api='mistral-conversations'``.

These rows refuse a model whose base URL is real and whose ``api`` has no
adapter — through :func:`turn_refusal`, through RPC's ``_handle_prompt`` on a
bound harness, and through the real ``_async_main`` — and pin the documented
fail-open: with no adapter registered at all (an embedder that binds the check
before it registers its adapters) nothing is refused.

Hermetic: fake credentials, an isolated agent dir and home, every request sent
through ``HTTPS_PROXY`` to a local :class:`~tests.route_wire.WireRecorder`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.models import get_model
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.runtime_bootstrap import register_providers
from aelix_coding_agent.core.runnable_models import turn_refusal
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandPrompt,
    RpcErrorResponse,
    RpcSuccessResponse,
)

from tests.cli import test_session_model_record_376 as _record
from tests.cli.test_session_model_restore_376 import _settings
from tests.route_wire import WireRecorder

env = _record.env  # the sandbox fixture: no key, an isolated agent dir and home

_CORP = Model(id="corp-x", provider="corp", api="nope", base_url="https://h.invalid/v1")


def _mistral() -> Model:
    model = get_model("mistral", "mistral-large-latest")
    assert model is not None
    return model


def _hosted(model: Model) -> None:
    """The row's premise: the base-URL arm cannot be the one that refuses."""

    assert model.provider
    assert model.base_url.startswith("https://"), model.base_url
    assert "{" not in model.base_url, model.base_url


def _no_adapter(api: str) -> str:
    return f"uses the '{api}' API, which this build has no adapter for"


def _stream(sent: list[str]) -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        sent.append(model.id)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return fn


async def _messages(session: Session) -> list[Any]:
    return [e for e in await session.get_branch() if e.type == "message"]


@pytest.mark.parametrize("which", ["catalog-mistral", "custom-nope"])
def test_a_hosted_model_with_no_adapter_is_refused(which: str) -> None:
    register_providers()
    model = _mistral() if which == "catalog-mistral" else _CORP
    _hosted(model)

    said = turn_refusal(model)

    assert said is not None
    assert _no_adapter(model.api) in said, said
    assert "base URL" not in said and "No model selected" not in said, said


@pytest.mark.parametrize("which", ["catalog-mistral", "custom-nope"])
async def test_a_hosted_model_with_no_adapter_is_rejected_over_rpc(which: str) -> None:
    """RPC's ``_handle_prompt`` on a harness bound the way ``cli/entry.py``'s
    factory binds it: ``success: false``, nothing written, nothing sent."""

    register_providers()
    model = _mistral() if which == "catalog-mistral" else _CORP
    _hosted(model)
    session = Session(MemorySessionStorage())
    sent: list[str] = []
    harness = AgentHarness(
        AgentHarnessOptions(model=model, stream_fn=_stream(sent), session=session)
    )
    harness.set_prompt_check(turn_refusal)

    response = await _handle_prompt(harness, RpcCommandPrompt(message="hi", id="p1"))
    await asyncio.wait_for(
        asyncio.gather(*list(harness._pending_tasks), return_exceptions=True), 10
    )

    assert isinstance(response, RpcErrorResponse), response
    assert (response.id, response.command) == ("p1", "prompt")
    assert _no_adapter(model.api) in response.error, response.error
    assert sent == []
    assert await _messages(session) == []
    assert harness.state.messages == []
    assert harness.phase == "idle"
    await harness.dispose()


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    recorder = WireRecorder("r6")
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, recorder.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")
    yield recorder
    recorder.close()


async def test_a_keyed_mistral_prompt_through_the_real_cli_is_rejected(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: WireRecorder
) -> None:
    """The live shape verify r5 measured, through ``_async_main`` with a key
    set: the key is not what is judged, the missing adapter is. ``main`` (and
    the r5 mutant) acknowledged it, wrote the user message to the session file
    and failed with ``No provider registered for api='mistral-conversations'``."""

    monkeypatch.setenv("MISTRAL_API_KEY", "fake-mistral-key")
    _settings(env, retry={"enabled": False})
    sessions = env / "sessions"
    seen: dict[str, Any] = {}

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        live = runtime_host.harness
        seen["model"] = live.current_model
        seen["response"] = await _handle_prompt(live, RpcCommandPrompt(message="m hi", id="p1"))
        await asyncio.wait_for(
            asyncio.gather(*list(live._pending_tasks), return_exceptions=True), 30
        )
        seen["messages"] = list(live.state.messages)

    from aelix_coding_agent import modes

    monkeypatch.setattr(modes, "run_rpc_mode", drive)
    argv = ["--provider", "mistral", "--model", "mistral-large-latest"]
    assert (
        await entry_mod._async_main([*argv, "--session-dir", str(sessions), "--mode", "rpc"]) == 0
    )

    assert seen["model"].api == "mistral-conversations"
    _hosted(seen["model"])
    response = seen["response"]
    assert isinstance(response, RpcErrorResponse), response
    assert _no_adapter("mistral-conversations") in response.error, response.error
    assert seen["messages"] == []
    written = [
        json.loads(line)
        for path in sorted(sessions.rglob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [e for e in written if e.get("type") == "message"] == [], written
    assert wire.hosts() == [], wire.connects


async def test_with_no_adapter_registered_nothing_is_refused() -> None:
    """The documented fail-open (ADR-0239 decision 6; ``turn_refusal``'s
    docstring): an embedder that binds the check before registering any
    adapter is never blocked. Once the adapters are registered, the same
    harness refuses the same model."""

    from aelix_ai.api_registry import clear_providers

    clear_providers()  # tests/conftest.py restores the registry afterwards
    assert turn_refusal(_CORP) is None
    assert turn_refusal(Model(id="model-x", provider="newlab")) is None
    session = Session(MemorySessionStorage())
    sent: list[str] = []
    harness = AgentHarness(
        AgentHarnessOptions(model=_CORP, stream_fn=_stream(sent), session=session)
    )
    harness.set_prompt_check(turn_refusal)

    response = await _handle_prompt(harness, RpcCommandPrompt(message="hi", id="p1"))
    await asyncio.wait_for(
        asyncio.gather(*list(harness._pending_tasks), return_exceptions=True), 10
    )

    assert isinstance(response, RpcSuccessResponse), response
    assert sent == ["corp-x"]
    assert len(await _messages(session)) == 2

    register_providers()
    late = await _handle_prompt(harness, RpcCommandPrompt(message="again", id="p2"))

    assert isinstance(late, RpcErrorResponse), late
    assert _no_adapter("nope") in late.error, late.error
    assert sent == ["corp-x"]
    assert len(await _messages(session)) == 2
    await harness.dispose()
