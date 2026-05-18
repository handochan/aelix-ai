"""Sprint 5b §E — runtime ergonomics fixes."""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.session import Session
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_ai.streaming import Model


def _harness_with_session():
    sess = Session(MemorySessionStorage())
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=sess,
        )
    )


async def test_session_name_cache_returns_after_set():
    """Sync get_session_name returns the cached value after set_session_name."""

    h = _harness_with_session()
    h._action_set_session_name("aelix-sprint-5b")
    # Sync read — should reflect the new value immediately via cache.
    assert h._action_get_session_name() == "aelix-sprint-5b"
    # Drain background tasks.
    for task in list(h._pending_tasks):
        await task


async def test_pending_tasks_drain_on_dispose():
    h = _harness_with_session()
    h._action_set_session_name("first")
    h._action_set_session_name("second")
    assert len(h._pending_tasks) >= 1
    await h.dispose()
    assert len(h._pending_tasks) == 0


def test_no_running_loop_raises_invalid_state():
    """Sync extension actions outside a loop now raise instead of asyncio.run."""

    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    # No running loop in this sync test path.
    with pytest.raises(AgentHarnessError) as exc_info:
        h._action_set_thinking_level("high")
    assert exc_info.value.code == "invalid_state"
