"""#679 — a ``/reload`` that succeeds runs at the thinking level the session ran at.

The factory builds from the launch inputs (``--thinking``, else ``off``); the
runtime puts the level back after the build, as the build that restores a session
after a failed rebuild does (ADR-0289 D1), clamped to the model the build runs.
pi's ``AgentSession.reload`` touches neither the model nor the level.
"""

from __future__ import annotations

from pathlib import Path

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import ReloadSeed
from aelix_agent_core.session import Session
from aelix_ai.streaming import Model

from tests.runtime.test_failed_rebuild_663 import MODEL, _Rig


async def test_a_reload_keeps_the_level(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    await rig.runtime.harness.set_thinking_level("high")
    seen: list[str | None] = []

    async def _start(harness: AgentHarness, _reason: str) -> None:
        seen.append(harness.state.thinking_level)

    rig.runtime.set_after_session_start(_start)
    await rig.runtime.reload()

    assert rig.runtime.harness.state.thinking_level == "high"
    # Already in place when the reload's ``session_start`` runs.
    assert seen == ["high"]
    [(_file, seed, _theme)] = rig.builds
    assert seed is not None and seed.model is MODEL and seed.restore is False


async def test_the_level_is_clamped_to_the_model_the_build_runs(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    await rig.runtime.harness.set_thinking_level("high")
    plain = Model(id="plain", provider="fixture", reasoning=False)

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        return AgentHarness(
            AgentHarnessOptions(
                model=plain, stream_fn=rig._stream, session=session, cwd=str(tmp_path)
            )
        )

    rig.runtime._create_harness = factory
    await rig.runtime.reload()

    assert rig.runtime.harness.current_model is plain
    assert rig.runtime.harness.state.thinking_level == "off"


async def test_a_reload_writes_no_level_entry(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    await rig.runtime.harness.set_thinking_level("medium")
    before = [e.type for e in await rig.session.get_entries()]

    await rig.runtime.reload()

    assert [e.type for e in await rig.session.get_entries()] == before
