"""Sprint 6h₅a · Phase 4.14 — :class:`ExtensionRunner` HookBus bridge tests
(P-333).

Pi parity: ``ExtensionRunner.emit`` (``runner.ts:680-712``) +
``ExtensionRunner.hasHandlers``. Aelix delegates both to
:class:`HookBus` via injected callable fields wired at harness
construction (``harness/core.py:632-634``). When the bridges are unwired
(default), ``emit`` is a no-op returning ``None`` and ``has_handlers``
returns ``False`` — safe defaults for tests / harnesses that have not
wired the bus.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.harness._extension_runner import ExtensionRunner
from aelix_agent_core.harness.hooks import (
    SessionShutdownHookEvent,
)


async def test_emit_delegates_to_injected_callable() -> None:
    """``ExtensionRunner.emit(event)`` invokes the injected ``_emit``."""

    captured: list[Any] = []

    async def fake_emit(event: Any) -> str:
        captured.append(event)
        return "ok"

    def fake_has_handlers(name: str) -> bool:
        return True

    runner = ExtensionRunner(
        extensions=[],
        _emit=fake_emit,
        _has_handlers=fake_has_handlers,
    )
    event = SessionShutdownHookEvent(reason="quit")
    result = await runner.emit(event)
    assert result == "ok"
    assert captured == [event]


async def test_has_handlers_delegates_to_injected_callable() -> None:
    """``ExtensionRunner.has_handlers(name)`` invokes the injected callable."""

    calls: list[str] = []

    def fake_has_handlers(name: str) -> bool:
        calls.append(name)
        return name == "session_shutdown"

    runner = ExtensionRunner(
        extensions=[], _emit=None, _has_handlers=fake_has_handlers
    )
    assert runner.has_handlers("session_shutdown") is True  # type: ignore[arg-type]
    assert runner.has_handlers("session_start") is False  # type: ignore[arg-type]
    assert calls == ["session_shutdown", "session_start"]


async def test_emit_and_has_handlers_are_noop_when_unwired() -> None:
    """Defensive: a bare :class:`ExtensionRunner` (no bridges) is safe."""

    runner = ExtensionRunner()  # default ``_emit=None`` / ``_has_handlers=None``
    assert runner.has_handlers("session_shutdown") is False  # type: ignore[arg-type]
    assert await runner.emit(SessionShutdownHookEvent(reason="quit")) is None


async def test_dataclass_is_mutable_per_sprint_6h5b_p362() -> None:
    """Sprint 6h₅b (ADR-0083, P-362) dropped ``frozen=True`` so the
    runtime invalidate bridge can be rebound by tests. The single source
    of truth for staleness remains the ``_ExtensionRuntime`` per spec
    §J synthesis — the runner has no flag of its own to protect.
    """

    runner = ExtensionRunner()
    # Mutation must succeed now (no FrozenInstanceError).
    runner.extensions = [object()]  # type: ignore[misc]
    assert len(runner.extensions) == 1
