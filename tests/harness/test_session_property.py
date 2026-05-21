"""Sprint 6h₅d §E (P-384 / MINOR-3 carry-forward from ADR-0086) —
:attr:`AgentHarness.session` public read-through property.

Pi parity: ``runtimeHost.session`` (``agent-session-runtime.ts:83-85``).
The property replaces 6 ``harness._session`` private-attribute reaches
across :class:`AgentSessionRuntime`, the factory bootstrap, the
``set_session_name`` RPC handler, and the REPL ``user_bash`` path.

These tests lock the two observable invariants:

  - When a :class:`Session` is bound, :attr:`AgentHarness.session`
    returns the same instance (no copy, no proxy).
  - When no session is bound, the property returns :data:`None`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
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


async def test_harness_session_property_returns_attached_session(
    tmp_path: Path,
) -> None:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    harness = _new_harness(session=session)

    assert harness.session is session


async def test_harness_session_property_none_when_unattached() -> None:
    harness = _new_harness(session=None)

    assert harness.session is None
