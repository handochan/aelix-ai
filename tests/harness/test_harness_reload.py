"""Sprint 6h₇b §E (Phase 5a-iii-β, ADR-0091) —
:meth:`AgentHarness.reload` + :attr:`AgentHarness.settings_manager`.

Pi parity: ``agent-session.ts:reload`` (SHA ``734e08e``).

Three invariants locked here:

  - :meth:`reload` raises :class:`AgentHarnessError` with code
    ``"invalid_state"`` when no settings manager is attached.
  - :attr:`settings_manager` returns the instance supplied via
    :class:`AgentHarnessOptions`, or ``None`` when omitted.
  - :meth:`reload` delegates to ``settings_manager.reload()`` — an
    in-memory settings manager reflects storage changes after reload.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _new_harness(
    settings_manager: SettingsManager | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            settings_manager=settings_manager,
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reload_raises_invalid_state_when_no_settings_manager() -> None:
    """reload() without a settings_manager → AgentHarnessError("invalid_state")."""

    harness = _new_harness(settings_manager=None)

    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.reload()

    err = exc_info.value
    assert err.code == "invalid_state"
    assert "settings_manager" in str(err)


async def test_settings_manager_property_returns_attached_instance() -> None:
    """settings_manager property returns the exact object supplied at construction."""

    sm = SettingsManager.in_memory()
    harness = _new_harness(settings_manager=sm)

    assert harness.settings_manager is sm


async def test_settings_manager_property_none_when_omitted() -> None:
    """settings_manager property returns None when not supplied."""

    harness = _new_harness(settings_manager=None)

    assert harness.settings_manager is None


async def test_reload_delegates_to_settings_manager_and_refreshes() -> None:
    """reload() calls settings_manager.reload() — observable via in-memory storage.

    Sequence:
    1. Construct InMemorySettingsStorage with global content = {"theme": "dark"}.
    2. Build SettingsManager.from_storage(storage) and harness wrapping it.
    3. Overwrite storage global content to {"theme": "light"} (simulates external edit).
    4. Call harness.reload() — must delegate to settings_manager.reload().
    5. Assert sm.get_theme() == "light".
    """

    storage = InMemorySettingsStorage()
    # Seed initial global settings (theme = dark).
    storage.with_lock("global", lambda _: json.dumps({"theme": "dark"}))

    sm = SettingsManager.from_storage(storage)
    harness = _new_harness(settings_manager=sm)

    # Simulate external change: overwrite storage to theme = light.
    storage.with_lock("global", lambda _: json.dumps({"theme": "light"}))

    # reload() must pick up the new value.
    await harness.reload()

    assert sm.get_theme() == "light"
