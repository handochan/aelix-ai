"""#415 — RPC ``set_model`` / ``cycle_model`` switch the way pi's ``setModel`` / ``cycleModel`` do.

Both handlers called ``AgentHarness.set_current_model``, a bare state write: no
``model_change`` was recorded, no ``model_select`` fired, and ``set_model`` left
the thinking level where it was (``xhigh`` on a model that cannot reason, in
``get_state`` and in the session). A pick no prompt followed was therefore lost:
``set_model`` then close then ``--continue`` reopened on the model before it.
``cycle_model`` did fit the level, and recorded THAT, so its reopen came back on
the old model at the level fitted for the new one.

pi (``rpc-mode.ts:470-486`` → ``agent-session.ts:2507-2527`` ``setModel``,
``:2600-2630`` ``_cycleAvailableModel`` at ``pi@fdce48eb4``) records the model,
fits the level through ``setThinkingLevel`` and fires ``model_select``. The
handlers now switch through ``AgentHarness.set_model`` (which records and fires,
#376) and fit the level to the model the harness ends on in one helper.

The kernel rows drive the handlers over a harness with an in-memory session; the
CLI rows drive the real ``_async_main`` with the real dispatch table over a
session a ``JsonlSessionRepo`` wrote, the reopen being a second ``_async_main``.
Hermetic: fake keys, an isolated agent dir and home, no network.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent
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
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_command,
    _handle_cycle_model,
    _handle_set_model,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandCycleModel,
    RpcCommandSetModel,
    RpcErrorResponse,
    RpcSuccessResponse,
)

from tests.cli.test_session_model_restore_376 import _HAIKU, _SONNET, _ident, _session, _sessions

# m1 reasons up to ``xhigh``, m2 reasons up to ``high``, m3 does not reason.
_M1 = Model(
    id="m1",
    provider="rec",
    api="openai-completions",
    base_url="http://127.0.0.1:9/v1",
    reasoning=True,
    thinking_level_map={"xhigh": "xhigh"},
)
_M2 = Model(
    id="m2", provider="rec", api="openai-completions", base_url="http://127.0.0.1:9/v1", reasoning=True
)
_M3 = Model(id="m3", provider="rec", api="openai-completions", base_url="http://127.0.0.1:9/v1")


class _Registry:
    """``get_available`` only: what both handlers read (no credential of the
    user's own is held, so ``cycle_model``'s ADR-0250 guard keeps the pool)."""

    def __init__(self, *models: Model) -> None:
        self._models = list(models)

    def get_available(self) -> list[Model]:
        return list(self._models)


def _stream() -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return fn


def _harness(model: Model, level: str) -> tuple[AgentHarness, Session]:
    session = Session(MemorySessionStorage())
    harness = AgentHarness(
        AgentHarnessOptions(model=model, thinking_level=level, stream_fn=_stream(), session=session)
    )
    return harness, session


async def _records(session: Session) -> list[str]:
    out: list[str] = []
    for e in await session.get_entries():
        if e.type == "model_change":
            out.append(f"model {e.provider}/{e.model_id}")
        elif e.type == "thinking_level_change":
            out.append(f"level {e.thinking_level}")
    return out


def _set(model: Model) -> RpcCommandSetModel:
    return RpcCommandSetModel(provider=model.provider, model_id=model.id, id="s")


# === kernel rows: what each handler records, fires and fits ====================


async def test_set_model_records_the_pick_it_makes_idle() -> None:
    """The issue as filed: ``set_current_model`` recorded nothing."""

    h, session = _harness(_M1, "off")
    response = await _handle_set_model(h, _Registry(_M1, _M2), _set(_M2))

    assert isinstance(response, RpcSuccessResponse)
    assert h.current_model is _M2
    assert await _records(session) == ["model rec/m2"]
    await h.dispose()


async def test_cycle_model_records_the_model_it_rotates_to() -> None:
    h, session = _harness(_M1, "off")
    response = await _handle_cycle_model(h, _Registry(_M1, _M2), RpcCommandCycleModel(id="c"))

    assert isinstance(response, RpcSuccessResponse)
    assert response.data is not None and response.data["model"]["id"] == "m2"
    assert await _records(session) == ["model rec/m2"]
    await h.dispose()


async def test_both_fire_model_select_with_the_model_they_leave() -> None:
    """pi's ``_emitModelSelect`` (``agent-session.ts:2487-2499``). aelix's source
    literal has no ``"cycle"`` (``harness/hooks.py``), so a rotation says
    ``"set"`` — a divergence ADR-0239's #415 amendment records."""

    h, _session = _harness(_M1, "off")
    seen: list[tuple[str, str, str]] = []

    async def on_select(event: Any, _ctx: Any = None) -> None:
        seen.append((event.model.id, event.previous_model.id, event.source))

    h.hooks.on("model_select", on_select)  # type: ignore[arg-type]
    registry = _Registry(_M1, _M2, _M3)
    await _handle_set_model(h, registry, _set(_M2))
    await _handle_cycle_model(h, registry, RpcCommandCycleModel(id="c"))

    assert seen == [("m2", "m1", "set"), ("m3", "m2", "set")]
    await h.dispose()


async def test_set_model_fits_the_level_to_the_model_and_records_it_after_the_model() -> None:
    """``xhigh`` on m1 is ``high`` on m2 and ``off`` on m3 (pi's ``setThinkingLevel``
    clamp, ``agent-session.ts:2642-2662``); each fit is recorded after its model,
    pi's order. ``set_model`` kept ``xhigh`` throughout before."""

    h, session = _harness(_M1, "xhigh")
    registry = _Registry(_M1, _M2, _M3)

    await _handle_set_model(h, registry, _set(_M2))
    assert h.state.thinking_level == "high"
    await _handle_set_model(h, registry, _set(_M3))
    assert h.state.thinking_level == "off"

    assert await _records(session) == ["model rec/m2", "level high", "model rec/m3", "level off"]
    await h.dispose()


async def test_cycle_model_answers_the_fitted_level_and_records_it_after_the_model() -> None:
    h, session = _harness(_M1, "xhigh")
    response = await _handle_cycle_model(h, _Registry(_M1, _M2), RpcCommandCycleModel(id="c"))

    assert isinstance(response, RpcSuccessResponse)
    assert response.data is not None and response.data["thinkingLevel"] == "high"
    assert h.state.thinking_level == "high"
    assert await _records(session) == ["model rec/m2", "level high"]
    await h.dispose()


@pytest.mark.parametrize("command", ["set_model", "cycle_model"])
async def test_a_level_the_new_model_supports_is_left_alone(command: str) -> None:
    """pi's setter records and fires ``thinking_level_select`` only when the level
    changes (``isChanging``, ``agent-session.ts:2648``); this harness's setter
    fires on every call, so the handlers do not call it for a fit that changes
    nothing."""

    h, session = _harness(_M1, "high")
    fired: list[str] = []

    async def on_level(event: Any, _ctx: Any = None) -> None:
        fired.append(event.level)

    h.hooks.on("thinking_level_select", on_level)  # type: ignore[arg-type]
    registry = _Registry(_M1, _M2)
    if command == "set_model":
        await _handle_set_model(h, registry, _set(_M2))
    else:
        await _handle_cycle_model(h, registry, RpcCommandCycleModel(id="c"))

    assert h.state.thinking_level == "high"
    assert fired == []
    assert await _records(session) == ["model rec/m2"]
    await h.dispose()


async def test_the_level_fits_the_model_a_model_select_handler_moved_to() -> None:
    """A handler that answers the pick with a ``set_model`` of its own leaves the
    harness on its model (#376's rule: the record follows the live model), and
    the level is fitted to THAT one — the state pi reaches, where the handler's
    own ``setModel`` fits the level again. Fitted to the model asked for (m1,
    which has ``high``), the level would stay ``high`` on m3."""

    h, session = _harness(_M2, "high")

    async def redirect(event: Any, _ctx: Any = None) -> None:
        if event.model is _M1:
            await h.set_model(_M3)

    h.hooks.on("model_select", redirect)  # type: ignore[arg-type]
    response = await _handle_set_model(h, _Registry(_M1, _M2, _M3), _set(_M1))

    assert isinstance(response, RpcSuccessResponse)
    assert h.current_model is _M3
    assert h.state.thinking_level == "off"
    assert await _records(session) == ["model rec/m3", "level off"]
    await h.dispose()


async def test_cycle_model_answers_the_live_level_when_a_handler_moved_the_model() -> None:
    """Review round 2 (cross, category 4): ``cycle_model``'s ``thinkingLevel`` is
    the harness's level after the switch and the fit, as pi's ``cycleModel``
    answers ``this.thinkingLevel``; no row covered a rotation a ``model_select``
    handler redirected, so an answer of ``clamp_thinking_level(next model, the
    level read before the switch)`` passed every test. Here the two differ:
    ``xhigh`` on m1 rotates to m2, whose clamp is ``high``, but the handler
    moves the harness on to m3, which does not reason, so the live level is
    ``off``."""

    h, session = _harness(_M1, "xhigh")

    async def redirect(event: Any, _ctx: Any = None) -> None:
        if event.model is _M2:
            await h.set_model(_M3)

    h.hooks.on("model_select", redirect)  # type: ignore[arg-type]
    response = await _handle_cycle_model(h, _Registry(_M1, _M2, _M3), RpcCommandCycleModel(id="c"))

    assert isinstance(response, RpcSuccessResponse) and response.data is not None
    assert h.current_model is _M3
    assert h.state.thinking_level == "off"
    assert response.data["thinkingLevel"] == h.state.thinking_level
    assert await _records(session) == ["model rec/m3", "level off"]
    await h.dispose()


async def test_a_refused_pick_fails_the_command_and_records_neither_model_nor_level() -> None:
    """A ``model_select`` handler that raises fails ``set_model`` on the wire with
    its error. The model stays in force (the guide: a raise there does not undo
    the switch), and nothing is recorded — fitting the level anyway would record
    a level for a model the session never recorded, so a reopen would come back
    on the old model at the new model's level."""

    h, session = _harness(_M1, "xhigh")

    async def refuse(event: Any, _ctx: Any = None) -> None:
        if event.model is _M2:
            raise RuntimeError("no m2 here")

    h.hooks.on("model_select", refuse)  # type: ignore[arg-type]
    registry = _Registry(_M1, _M2)
    response = await _handle_command(
        h,
        {"type": "set_model", "provider": "rec", "modelId": "m2", "id": "s"},
        build_dispatch_table(registry),  # type: ignore[arg-type]
    )

    assert isinstance(response, RpcErrorResponse)
    assert "no m2 here" in response.error
    assert h.current_model is _M2
    assert h.state.thinking_level == "xhigh"
    assert await _records(session) == []
    with pytest.raises(AgentHarnessError):
        await _handle_set_model(h, registry, _set(_M2))
    await h.dispose()


# === CLI rows: set_model / cycle_model, close, --continue ======================


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``tests/cli/test_session_model_restore_376.py``'s sandbox: no key, an empty agent dir."""

    from tests.cli.test_launch_route_344 import _FakePipedStdin
    from tests.env_sandbox import sandbox_home

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_", "CLOUDFLARE_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    register_providers()
    return tmp_path


async def _rpc(env: Path, monkeypatch: pytest.MonkeyPatch, drive: Any) -> int:
    from aelix_coding_agent import modes

    monkeypatch.setattr(modes, "run_rpc_mode", drive)
    return await entry_mod._async_main(
        ["--continue", "--session-dir", _sessions(env), "--mode", "rpc"]
    )


def _model_changes(path: str) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8") as handle:
        entries = [json.loads(line) for line in handle if line.strip()]
    return [(e["provider"], e["modelId"]) for e in entries if e["type"] == "model_change"]


async def test_rpc_set_model_then_close_then_continue_reopens_on_the_pick(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's proof, through the dispatch table ``run_rpc_mode`` builds with
    the CLI's registry (#371). Before: the reopen was on haiku, the model of the
    session's last answer."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env, _HAIKU)
    answers: list[Any] = []
    seen: list[tuple[str, str]] = []

    async def pick(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any
    ) -> None:
        table = build_dispatch_table(model_registry, runtime_host=runtime_host)
        cmd = RpcCommandSetModel(provider=_SONNET[0], model_id=_SONNET[1], id="s")
        answers.append(await table["set_model"](runtime_host.harness, cmd))

    async def look(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any
    ) -> None:
        seen.append(_ident(runtime_host.harness.current_model))

    assert await _rpc(env, monkeypatch, pick) == 0
    assert await _rpc(env, monkeypatch, look) == 0

    assert [type(a) for a in answers] == [RpcSuccessResponse]
    assert seen == [_SONNET]
    assert _model_changes(path)[-1] == _SONNET


async def test_rpc_cycle_model_then_close_then_continue_reopens_on_the_next_model(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env, _HAIKU)
    rotated: list[tuple[str, str]] = []
    seen: list[tuple[str, str]] = []

    async def cycle(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any
    ) -> None:
        table = build_dispatch_table(model_registry, runtime_host=runtime_host)
        answer = await table["cycle_model"](runtime_host.harness, RpcCommandCycleModel(id="c"))
        assert isinstance(answer, RpcSuccessResponse) and answer.data is not None
        rotated.append((answer.data["model"]["provider"], answer.data["model"]["id"]))

    async def look(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any
    ) -> None:
        seen.append(_ident(runtime_host.harness.current_model))

    assert await _rpc(env, monkeypatch, cycle) == 0
    assert await _rpc(env, monkeypatch, look) == 0

    assert rotated and rotated[0] != _HAIKU
    assert seen == rotated
    assert _model_changes(path)[-1] == rotated[0]
