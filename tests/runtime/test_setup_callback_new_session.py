"""Sprint 6h₅b · Phase 4.15 — ``setup`` callback for ``new_session``
(ADR-0083, P-359).

Pi parity: ``agent-session-runtime.ts:226-229``. Order in
``_finish_session_replacement``: teardown → apply → **setup** → rebind →
session_start emit → with_session.

After setup, the harness rebuilds messages from
``new_session.build_context().messages`` so any ``session.append_*``
performed inside ``setup`` is reflected in the active turn context.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
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


def _make_runtime(
    harness: AgentHarness,
    repo: JsonlSessionRepo,
    fs: LocalFileSystem,
) -> AgentSessionRuntime:
    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return AgentSessionRuntime(harness, _factory, repo=repo, fs=fs)


async def test_setup_callback_invoked_with_session_manager(
    tmp_path: Path,
) -> None:
    """``setup(session_manager)`` receives the
    :class:`ReadonlySessionManager` of the NEW harness — not the OLD.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    received: list[Any] = []

    async def setup_cb(session_manager: Any) -> None:
        received.append(session_manager)

    await runtime.new_session(setup=setup_cb)
    assert len(received) == 1
    sm = received[0]
    # session_manager.get_session() returns the NEW session.
    new_session = sm.get_session()
    assert new_session is runtime.session
    assert new_session is not source


async def test_setup_runs_before_rebind(tmp_path: Path) -> None:
    """Pi parity ordering: setup runs BEFORE rebind."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    order: list[str] = []

    async def setup_cb(sm: Any) -> None:
        order.append("setup")

    async def rebind_cb(h: AgentHarness) -> None:
        order.append("rebind")

    runtime.set_rebind_session(rebind_cb)
    await runtime.new_session(setup=setup_cb)
    assert order == ["setup", "rebind"]


async def test_setup_appends_message_visible_in_rebuilt_messages(
    tmp_path: Path,
) -> None:
    """When ``setup`` calls ``session.append_message(...)`` on the NEW
    session, the harness's ``_state.messages`` reflects the appended
    message after the rebuild from ``new_session.build_context()``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    async def setup_cb(sm: Any) -> None:
        new_session: Session = sm.get_session()
        await new_session.append_message(
            UserMessage(content=[TextContent(text="injected at setup")])
        )

    await runtime.new_session(setup=setup_cb)

    msgs = runtime.harness._state.messages
    assert len(msgs) == 1
    appended = msgs[0]
    # Each message has a `content` list with TextContent.
    text_parts = [
        c.text for c in appended.content if getattr(c, "type", None) == "text"
    ]
    assert "injected at setup" in text_parts


async def test_setup_optional_when_none(tmp_path: Path) -> None:
    """No ``setup`` argument keeps the existing replace path intact."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    result = await runtime.new_session()
    assert result.cancelled is False
