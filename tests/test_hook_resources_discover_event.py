"""Sprint 5a (Phase 3.1) — ``resources_discover`` reducer tests (P-26).

Pi parity (``agent-session.ts:2055-2068``): handlers contribute paths to
three buckets (skill / prompt / theme); the reducer concatenates them in
handler order, then de-duplicates within each bucket preserving first
occurrence.
"""

from __future__ import annotations

from aelix_agent_core.harness.hooks import (
    HookBus,
    ResourcesDiscoverHookEvent,
    ResourcesDiscoverResult,
)
from aelix_coding_agent.extensions.api import (
    ExtensionContext,
    _ExtensionRuntime,
)


def _make_bus() -> HookBus:
    rt = _ExtensionRuntime()
    ctx = ExtensionContext(
        rt,
        cwd="/tmp",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )
    return HookBus(ctx_factory=lambda: ctx)


async def test_resources_discover_collects_across_handlers() -> None:
    bus = _make_bus()
    bus.on(
        "resources_discover",
        lambda e, c: ResourcesDiscoverResult(  # type: ignore[arg-type]
            skill_paths=["/a/skills"], prompt_paths=["/a/prompts"]
        ),
    )
    bus.on(
        "resources_discover",
        lambda e, c: ResourcesDiscoverResult(  # type: ignore[arg-type]
            skill_paths=["/b/skills"], theme_paths=["/b/themes"]
        ),
    )
    result = await bus.emit(ResourcesDiscoverHookEvent(cwd="/tmp"))
    assert isinstance(result, ResourcesDiscoverResult)
    assert result.skill_paths == ["/a/skills", "/b/skills"]
    assert result.prompt_paths == ["/a/prompts"]
    assert result.theme_paths == ["/b/themes"]


async def test_resources_discover_dedups_preserves_first_occurrence() -> None:
    bus = _make_bus()
    bus.on(
        "resources_discover",
        lambda e, c: ResourcesDiscoverResult(skill_paths=["/x", "/y", "/x"]),  # type: ignore[arg-type]
    )
    bus.on(
        "resources_discover",
        lambda e, c: ResourcesDiscoverResult(skill_paths=["/y", "/z"]),  # type: ignore[arg-type]
    )
    result = await bus.emit(ResourcesDiscoverHookEvent())
    assert isinstance(result, ResourcesDiscoverResult)
    # Order preserved; dedup keeps the first occurrence.
    assert result.skill_paths == ["/x", "/y", "/z"]


async def test_resources_discover_no_handlers_returns_none() -> None:
    bus = _make_bus()
    result = await bus.emit(ResourcesDiscoverHookEvent(reason="reload"))
    assert result is None
