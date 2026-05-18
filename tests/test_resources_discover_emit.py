"""Sprint 5b §B.3 — ``resources_discover`` emit + reload path."""

from __future__ import annotations

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    ResourcesDiscoverHookEvent,
    ResourcesDiscoverResult,
)
from aelix_ai.streaming import Model
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)


async def test_discover_resources_emits_startup_reason(tmp_path):
    captured: list[ResourcesDiscoverHookEvent] = []

    def h(event, ctx):
        captured.append(event)
        return ResourcesDiscoverResult(skill_paths=[str(tmp_path / "s1")])

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("resources_discover", h)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            cwd=str(tmp_path),
        )
    )
    await harness.discover_resources()
    assert len(captured) == 1
    assert captured[0].reason == "startup"
    assert harness.state.resources["skill_paths"] == [str(tmp_path / "s1")]


async def test_reload_resources_emits_reload_reason(tmp_path):
    captured: list[ResourcesDiscoverHookEvent] = []

    def h(event, ctx):
        captured.append(event)

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("resources_discover", h)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            cwd=str(tmp_path),
        )
    )
    await harness.reload_resources()
    assert len(captured) == 1
    assert captured[0].reason == "reload"


async def test_resources_dedup_across_handlers(tmp_path):
    def h1(event, ctx):
        return ResourcesDiscoverResult(skill_paths=["/a", "/b"])

    def h2(event, ctx):
        return ResourcesDiscoverResult(skill_paths=["/b", "/c"])

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("resources_discover", h1)
    api.on("resources_discover", h2)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            cwd=str(tmp_path),
        )
    )
    await harness.discover_resources()
    assert harness.state.resources["skill_paths"] == ["/a", "/b", "/c"]


async def test_no_handlers_skips_emit():
    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"))
    )
    # Just verifies no exception is raised.
    await harness.discover_resources()
    await harness.reload_resources()
