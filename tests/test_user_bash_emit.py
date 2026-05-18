"""Sprint 5b §B.2 — ``user_bash`` event emit from minimal CLI."""

from __future__ import annotations

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import UserBashHookEvent, UserBashResult
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.repl import handle_user_bash
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)


def _harness():
    return AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"))
    )


async def test_user_bash_emits_event(tmp_path):
    captured: list[UserBashHookEvent] = []

    def h(event, ctx):
        captured.append(event)

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("user_bash", h)
    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), extensions=[ext])
    )
    await handle_user_bash(
        harness, "echo hi", exclude_from_context=False, cwd=str(tmp_path)
    )
    assert len(captured) == 1
    assert captured[0].command == "echo hi"
    assert captured[0].exclude_from_context is False


async def test_user_bash_exclude_flag(tmp_path):
    captured: list[UserBashHookEvent] = []

    def h(event, ctx):
        captured.append(event)

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("user_bash", h)
    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), extensions=[ext])
    )
    await handle_user_bash(
        harness, "echo bye", exclude_from_context=True, cwd=str(tmp_path)
    )
    assert captured[0].exclude_from_context is True


async def test_user_bash_extension_supplies_result(tmp_path):
    """Extension-supplied ``result`` short-circuits execution."""

    class _StubResult:
        output = "stub-output"

    def h(event, ctx):
        return UserBashResult(result=_StubResult())  # type: ignore[arg-type]

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("user_bash", h)
    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), extensions=[ext])
    )
    out = await handle_user_bash(
        harness, "echo would-run", exclude_from_context=False, cwd=str(tmp_path)
    )
    assert "stub-output" in out
