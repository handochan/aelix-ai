"""Sprint 5b §F — wire 4 throwing stubs from Sprint 5a."""

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
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)


def _harness_with_session():
    sess = Session(MemorySessionStorage())
    return AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), session=sess)
    )


async def test_append_entry_writes_custom_entry():
    h = _harness_with_session()
    h._action_append_entry("aelix.test", {"k": "v"})
    # Let the pinned task settle.
    for task in list(h._pending_tasks):
        await task
    entries = await h._session.get_entries()
    assert any(
        getattr(e, "custom_type", None) == "aelix.test" for e in entries
    )


async def test_append_entry_no_session_raises():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    with pytest.raises(AgentHarnessError) as exc_info:
        h._action_append_entry("x")
    assert exc_info.value.code == "invalid_state"


async def test_send_message_next_turn_enqueues():
    h = _harness_with_session()
    h._action_send_message("hello", deliver_as="next_turn")
    assert len(h._next_turn_queue) == 1
    for task in list(h._pending_tasks):
        await task


async def test_get_commands_enumerates_registered():
    ext = Extension(name="myext")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.register_command("greet", handler=lambda: None, description="say hi")
    h = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), extensions=[ext])
    )
    cmds = h._action_get_commands()
    assert any(
        c.name == "greet" and c.source == "myext" for c in cmds
    )
