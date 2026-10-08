"""#376 — an RPC prompt on a model no turn can run is rejected before acceptance.

Measured on ``8f7d98aa``: ``aelix --mode rpc --continue`` sat on ``''/''`` with
``api='unknown'``; a ``prompt`` was acknowledged ``success: true``, failed at the
first request with ``No provider registered for api='unknown'``, and its message
had already been written to the session (``messageCount`` 4 → 5). pi's
``prompt`` validates the model before anything is recorded
(``agent-session.ts:2032-2050`` at ``pi@1cedd3272``); the TUI and print/json
already refused on ``is_runnable`` (#189, #98). RPC now does too — a
``success: false`` response, which ``rpc.md`` defines as "rejected before
acceptance", and nothing written.

Review round 3: the question is the harness's (``AgentHarness.set_prompt_check``,
bound by the CLI's harness factory, here by :func:`_checked`), asked AFTER the
``input`` hook as pi asks it (``agent-session.ts:1993`` then ``:2032-2050``), and
the response waits for its verdict (pi's ``preflightResult``,
``rpc-mode.ts:394-412``). Round 2 asked in ``_handle_prompt`` before the hook: an
extension that handled the input itself on a keyless setup was refused, and a
model an ``input`` handler switched to was never judged.

Review round 4 (owner decision 2026-10-08): the question asks no credential —
round 3's refused auth the adapters accept without a key (headers, an auth
token, Vertex ADC). A keyless prompt is accepted and written, as on ``main``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
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
from aelix_coding_agent.cli.runtime_bootstrap import register_providers
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandPrompt,
    RpcErrorResponse,
    RpcSuccessResponse,
)


def _stream() -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return fn


async def _session(tmp_path: Path) -> Session:
    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path / "sessions"))
    return await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))


async def _types(session: Session) -> list[str]:
    return [e.type for e in await session.get_branch()]


def _checked(harness: AgentHarness) -> AgentHarness:
    """Bind the question as ``cli/entry.py``'s harness factory does."""

    from aelix_coding_agent.core.runnable_models import turn_refusal

    harness.set_prompt_check(turn_refusal)
    return harness


@pytest.mark.parametrize(
    ("model", "says"),
    [
        (Model(id="", provider=""), "No model selected."),
        (Model(id="model-x", provider="newlab"), "model-x"),
    ],
    ids=["placeholder", "unknown-api"],
)
async def test_an_unrunnable_model_is_rejected_and_nothing_is_written(
    tmp_path: Path, model: Model, says: str
) -> None:
    register_providers()
    session = await _session(tmp_path)
    harness = _checked(
        AgentHarness(AgentHarnessOptions(model=model, stream_fn=_stream(), session=session))
    )

    response = await _handle_prompt(harness, RpcCommandPrompt(message="hi", id="p1"))

    assert isinstance(response, RpcErrorResponse)
    assert (response.id, response.command) == ("p1", "prompt")
    assert says in response.error
    assert harness.phase == "idle"
    assert "message" not in await _types(session)
    assert harness.state.messages == []
    await harness.dispose()


async def test_a_runnable_model_is_accepted(tmp_path: Path) -> None:
    """The control: the gate passes a model with a registered adapter."""

    register_providers()
    model = get_model("anthropic", "claude-haiku-4-5")
    assert model is not None
    session = await _session(tmp_path)
    harness = _checked(
        AgentHarness(AgentHarnessOptions(model=model, stream_fn=_stream(), session=session))
    )

    response = await _handle_prompt(harness, RpcCommandPrompt(message="hi", id="p1"))
    await asyncio.gather(*list(harness._pending_tasks))

    assert isinstance(response, RpcSuccessResponse)
    assert "message" in await _types(session)
    await harness.dispose()


# === Review round 4 — no credential is asked (owner decision 2026-10-08) =====
#
# Round 2 added pi's other half (``hasConfiguredAuth``, ``agent-session.ts:2037-2050``)
# over ``ModelRegistry.has_configured_auth``. That predicate does not count
# request-level auth the adapters accept — a ``models.json`` / registration
# auth header, ``ANTHROPIC_CUSTOM_HEADERS``, ``ANTHROPIC_AUTH_TOKEN``, Vertex
# ADC — so it refused setups that ran on ``main``. The check is the adapter
# half only; a keyless prompt is accepted and written, as on ``main``.


@pytest.mark.parametrize("keyed", [False, True], ids=["no-key", "key"])
async def test_a_model_without_a_credential_is_accepted_as_on_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keyed: bool
) -> None:
    from aelix_ai.oauth import AuthStorage
    from aelix_coding_agent.model_registry import ModelRegistry

    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    if keyed:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    register_providers()
    model = get_model("anthropic", "claude-haiku-4-5")
    assert model is not None
    session = await _session(tmp_path)
    harness = _checked(
        AgentHarness(AgentHarnessOptions(model=model, stream_fn=_stream(), session=session))
    )
    storage = AuthStorage(path=tmp_path / "auth.json")
    await storage.load()
    harness.runtime.bind_model_registry(ModelRegistry.in_memory(storage))  # pyright: ignore[reportArgumentType]

    response = await _handle_prompt(harness, RpcCommandPrompt(message="hi", id="p1"))
    await asyncio.gather(*list(harness._pending_tasks))

    assert isinstance(response, RpcSuccessResponse), response
    assert "message" in await _types(session)
    await harness.dispose()


def test_the_check_refuses_only_a_model_that_cannot_run() -> None:
    """``turn_refusal`` takes the model alone: no registry, no credential."""

    from aelix_coding_agent.core.runnable_models import turn_refusal

    register_providers()
    haiku = get_model("anthropic", "claude-haiku-4-5")
    assert haiku is not None
    assert turn_refusal(haiku) is None
    assert str(turn_refusal(Model(id="", provider=""))).startswith("No model selected.")
    assert "model-x" in str(turn_refusal(Model(id="model-x", provider="newlab")))


async def test_a_cancelled_swap_says_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """An extension that cancels ``switch_session`` leaves the session (and the
    runtime's message, which describes it) as it was: no line for a build that
    did not happen. A swap that did happen says the runtime's message."""

    from types import SimpleNamespace

    from aelix_coding_agent.rpc.rpc_mode import _handle_switch_session
    from aelix_coding_agent.rpc.rpc_types import RpcCommandSwitchSession

    class _Runtime:
        model_fallback_message = "Could not restore model anthropic/claude-haiku-4-5"

        def __init__(self, cancelled: bool) -> None:
            self.cancelled = cancelled

        async def switch_session(self, path: str) -> object:
            return SimpleNamespace(cancelled=self.cancelled)

    await _handle_switch_session(_Runtime(True), RpcCommandSwitchSession(session_path="x"))  # type: ignore[arg-type]
    assert capsys.readouterr().err == ""
    await _handle_switch_session(_Runtime(False), RpcCommandSwitchSession(session_path="x"))  # type: ignore[arg-type]
    assert capsys.readouterr().err == (
        "Warning: Could not restore model anthropic/claude-haiku-4-5\n"
    )


# === Review round 3 — the question is asked after the ``input`` hook =========


async def _keyless(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AgentHarness, Session]:
    """A haiku harness whose registry holds no credential at all."""

    from aelix_ai.oauth import AuthStorage
    from aelix_coding_agent.model_registry import ModelRegistry

    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    register_providers()
    model = get_model("anthropic", "claude-haiku-4-5")
    assert model is not None
    session = await _session(tmp_path)
    harness = _checked(
        AgentHarness(AgentHarnessOptions(model=model, stream_fn=_stream(), session=session))
    )
    storage = AuthStorage(path=tmp_path / "auth.json")
    await storage.load()
    harness.runtime.bind_model_registry(ModelRegistry.in_memory(storage))  # pyright: ignore[reportArgumentType]
    return harness, session


async def test_an_input_an_extension_handles_needs_no_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On 8f7d98aa an ``InputHandled`` worked on a keyless setup; round 2's
    pre-hook gate refused it before the hook ran. pi runs the input handlers
    first (``agent-session.ts:1993``)."""

    from aelix_agent_core.harness.hooks import InputHandled

    harness, session = await _keyless(tmp_path, monkeypatch)
    seen: list[str] = []

    def handle(event: Any, _ctx: Any = None) -> Any:
        seen.append(event.text)
        return InputHandled()

    harness.hooks.on("input", handle)  # type: ignore[arg-type]

    response = await _handle_prompt(harness, RpcCommandPrompt(message="ping", id="p1"))
    await asyncio.gather(*list(harness._pending_tasks))

    assert isinstance(response, RpcSuccessResponse), response
    assert seen == ["ping"]
    assert "message" not in await _types(session)
    assert harness.phase == "idle"
    await harness.dispose()


async def test_a_model_an_input_handler_switches_to_is_the_one_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2 cat1, round 4's form: an ``input`` handler that switches to a
    model with no adapter had its prompt accepted and written (round 2), the
    turn failing ``No provider registered``. Judged after the hook: refused,
    nothing written."""

    harness, session = await _keyless(tmp_path, monkeypatch)
    nowhere = Model(id="model-x", provider="newlab")

    async def switch(event: Any, _ctx: Any = None) -> None:
        await harness.set_model(nowhere)

    harness.hooks.on("input", switch)  # type: ignore[arg-type]

    response = await _handle_prompt(harness, RpcCommandPrompt(message="use-newlab", id="p1"))
    await asyncio.gather(*list(harness._pending_tasks))

    assert isinstance(response, RpcErrorResponse), response
    assert "model-x" in response.error, response.error
    assert harness.current_model is nowhere
    assert "message" not in await _types(session)
    assert harness.state.messages == []
    await harness.dispose()


async def test_a_keyless_model_an_input_handler_switches_to_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: a keyless model the hook switched to runs as on ``main``."""

    harness, session = await _keyless(tmp_path, monkeypatch)
    gpt = get_model("openai", "gpt-4o")
    assert gpt is not None

    async def switch(event: Any, _ctx: Any = None) -> None:
        await harness.set_model(gpt)

    harness.hooks.on("input", switch)  # type: ignore[arg-type]

    response = await _handle_prompt(harness, RpcCommandPrompt(message="use-openai", id="p1"))
    await asyncio.gather(*list(harness._pending_tasks))

    assert isinstance(response, RpcSuccessResponse), response
    assert harness.current_model is gpt
    assert "message" in await _types(session)
    await harness.dispose()


async def test_the_response_precedes_the_turns_first_event() -> None:
    """The response now waits for the verdict; it must still reach the client
    before ``agent_start``, as it did when it was sent before the task started.
    An in-memory session, so no file I/O stands between the two."""

    from aelix_agent_core.session import MemorySessionStorage

    register_providers()
    model = get_model("anthropic", "claude-haiku-4-5")
    assert model is not None
    session = Session(MemorySessionStorage())
    harness = _checked(
        AgentHarness(AgentHarnessOptions(model=model, stream_fn=_stream(), session=session))
    )
    order: list[str] = []
    harness.subscribe(lambda event: order.append(getattr(event, "type", "?")))

    response = await _handle_prompt(harness, RpcCommandPrompt(message="hi", id="p1"))
    order.append("response")
    await asyncio.gather(*list(harness._pending_tasks))

    assert isinstance(response, RpcSuccessResponse)
    assert "agent_start" in order
    assert order.index("response") < order.index("agent_start"), order
    await harness.dispose()
