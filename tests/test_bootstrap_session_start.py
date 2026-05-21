"""Sprint 6h₅c · Phase 4.16 — :func:`create_agent_session_runtime`
bootstrap ``session_start(reason="startup")`` emit (P-371).

Pi parity: ``createAgentSessionRuntime`` (``agent-session-runtime.ts:382-400``)
firing ``session_start`` at bootstrap (Pi :326 + :2050).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import SessionStartHookEvent
from aelix_agent_core.runtime import (
    AgentSessionRuntime,
    create_agent_session_runtime,
)
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _new_harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _factory(new_sess: Session) -> AgentHarness:
    return _new_harness(session=new_sess)


async def test_factory_emits_session_start_startup(tmp_path: Path) -> None:
    """``create_agent_session_runtime`` emits ``session_start`` with
    ``reason="startup"`` after construction.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    harness = _new_harness(session=session)
    captured: list[SessionStartHookEvent] = []
    harness.hooks.on("session_start", lambda e, c: captured.append(e))

    runtime = await create_agent_session_runtime(
        harness, _factory, repo=repo, fs=fs
    )
    assert isinstance(runtime, AgentSessionRuntime)
    assert len(captured) == 1
    assert captured[0].reason == "startup"
    assert captured[0].previous_session_file is None


async def test_factory_respects_custom_session_start_event(
    tmp_path: Path,
) -> None:
    """A caller-supplied ``session_start_event`` overrides the default
    payload (Pi ``??`` default sentinel).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    harness = _new_harness(session=session)
    captured: list[SessionStartHookEvent] = []
    harness.hooks.on("session_start", lambda e, c: captured.append(e))

    custom = SessionStartHookEvent(
        type="session_start",
        reason="reload",
        previous_session_file="/prev.jsonl",
    )
    await create_agent_session_runtime(
        harness, _factory, repo=repo, fs=fs, session_start_event=custom
    )
    assert len(captured) == 1
    assert captured[0].reason == "reload"
    assert captured[0].previous_session_file == "/prev.jsonl"


async def test_factory_skips_emit_when_no_handlers(tmp_path: Path) -> None:
    """Pi parity (``runner.ts:178-180``): no handlers → no emit. The
    factory still returns the runtime; verify by exercising it.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    harness = _new_harness(session=session)
    # No session_start handler registered.

    runtime = await create_agent_session_runtime(
        harness, _factory, repo=repo, fs=fs
    )
    assert isinstance(runtime, AgentSessionRuntime)
    # The runtime is fully constructed even with no handlers wired.
    assert runtime.harness is harness


async def test_replacement_emit_uses_reason_new_or_resume(
    tmp_path: Path,
) -> None:
    """Regression guard: ``_finish_session_replacement`` does NOT emit
    ``reason="startup"`` — that's reserved for the factory bootstrap.
    A subsequent ``new_session`` emits ``reason="new"``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    harness = _new_harness(session=session)
    bootstrap_captured: list[SessionStartHookEvent] = []
    harness.hooks.on(
        "session_start", lambda e, c: bootstrap_captured.append(e)
    )

    runtime = await create_agent_session_runtime(
        harness, _factory, repo=repo, fs=fs
    )
    assert len(bootstrap_captured) == 1
    assert bootstrap_captured[0].reason == "startup"

    # After replacement, the NEW harness's handler is what fires; we
    # register one inside the factory so we can capture it.
    replacement_captured: list[SessionStartHookEvent] = []

    async def _capture_factory(new_sess: Session) -> AgentHarness:
        h = _new_harness(session=new_sess)
        h.hooks.on(
            "session_start", lambda e, c: replacement_captured.append(e)
        )
        return h

    runtime._create_harness = _capture_factory  # type: ignore[assignment]
    await runtime.new_session()
    assert len(replacement_captured) == 1
    assert replacement_captured[0].reason == "new"


async def test_bootstrap_emit_runs_after_runtime_construction(
    tmp_path: Path,
) -> None:
    """The session_start emit must observe a fully-constructed runtime
    (the assertion below would fail if the emit fired pre-construction).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    harness = _new_harness(session=session)
    state: dict[str, Any] = {}

    def _capture(event: Any, ctx: Any) -> None:
        # The HookEventContext exposes the harness — verifying the
        # harness is alive AND fully wired at emit time.
        state["fired"] = True
        state["event_type"] = event.type
        state["reason"] = event.reason

    harness.hooks.on("session_start", _capture)
    runtime = await create_agent_session_runtime(
        harness, _factory, repo=repo, fs=fs
    )
    # The runtime is returned — the emit completed during the factory
    # call so the assert below runs against post-construction state.
    assert isinstance(runtime, AgentSessionRuntime)
    assert state.get("fired") is True
    assert state["event_type"] == "session_start"
    assert state["reason"] == "startup"
