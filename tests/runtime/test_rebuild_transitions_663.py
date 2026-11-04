"""#663 fix round 1 (ADR-0289 D7, D4) — transitions run one at a time, and fail closed.

Codex R01: two reloads interleaved inside the factory, the first overwrote the
second's harness without disposing it, and that orphan later ran model requests
and a tool while ``runtime.stopped`` was set. Every replacement now runs inside
``AgentSessionRuntime._transition``: a second one waits, and one started from
inside the first (a hook handler, the factory, a rebind) is refused instead of
deadlocking.

Codex R02 / verify V3: an interruption raised while the teardown ran (a cleanup
raising ``CancelledError``) left the harness disposed but not stopped, so the
next prompt reached the model. Codex R04: a restored harness whose settlement
or rebind failed was reported "kept". Codex R05 / R06 / verify V4: the restore
unioned the tools, rebuilt the policy, and kept a level its model cannot run.
Codex R09: ``with_session``'s ``ctx.reload()`` ran the legacy
``AgentHarness.reload``, which rebuilt nothing and cleared the provider registry.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.harness.tool_policy import ToolPolicy
from aelix_agent_core.runtime import (
    NESTED_TRANSITION_MESSAGE,
    STOPPED_MESSAGE,
    RebuildFailedError,
    ReloadSeed,
)
from aelix_agent_core.session import Session
from aelix_ai.api_registry import get_registered_providers, register_provider, unregister_provider
from aelix_ai.streaming import Model

from tests.runtime.test_failed_rebuild_663 import _always, _once, _Rig

INTERRUPTS = [asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit]


async def _refuses(rig: _Rig, harness: AgentHarness) -> None:
    """``harness`` runs nothing: a prompt is refused before any request, no tool runs."""

    requests, ran = rig.requests, list(rig.ran)
    with pytest.raises(AgentHarnessError) as refused:
        await harness.prompt("run it")
    assert refused.value.code == "invalid_state"
    with pytest.raises(AgentHarnessError):
        await harness.steer("steer it")
    assert (rig.requests, rig.ran) == (requests, ran)


# --- R01: one transition at a time ------------------------------------------------------


async def test_a_second_reload_waits_and_every_superseded_harness_is_disposed(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    built: list[AgentHarness] = [rig.runtime.harness]
    entered, release = asyncio.Event(), asyncio.Event()

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        if len(built) == 1:
            entered.set()
            await release.wait()
        harness = rig.harness(session)
        built.append(harness)
        return harness

    rig.runtime._create_harness = factory
    first = asyncio.create_task(rig.runtime.reload())
    await entered.wait()
    second = asyncio.create_task(rig.runtime.reload())
    for _ in range(5):
        await asyncio.sleep(0)
    # The second reload has not started: nothing of it was built or torn down.
    assert len(built) == 1 and not second.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 5)

    assert len(built) == 3
    assert rig.runtime.harness is built[2] and not built[2].disposed
    assert all(h.disposed for h in built[:2])

    # Codex R01's ending: a later reload stops. Nothing that was ever built runs.
    rig.runtime._create_harness = rig.factory
    rig.fail = _always(RuntimeError("third reload fails"))
    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()
    assert caught.value.state == "stopped"
    for harness in built:
        await _refuses(rig, harness)


async def test_a_reload_from_inside_a_session_start_handler_is_refused_not_deadlocked(
    tmp_path: Path,
) -> None:
    rig = await _Rig.create(tmp_path)
    seen: list[BaseException] = []

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        harness = rig.harness(session)

        async def on_start(_event: Any, _ctx: Any) -> None:
            try:
                await rig.runtime.reload()
            except BaseException as exc:  # noqa: BLE001 — recorded for the assert
                seen.append(exc)

        harness.hooks.on("session_start", on_start)
        return harness

    rig.runtime._create_harness = factory
    await asyncio.wait_for(rig.runtime.reload(), 5)
    [refused] = seen
    assert isinstance(refused, RuntimeError) and str(refused) == NESTED_TRANSITION_MESSAGE
    # The outer reload finished normally; the one it refused built nothing.
    assert rig.runtime.stopped is None and not rig.runtime.harness.disposed
    # And once it has ended, a reload runs again.
    await asyncio.wait_for(rig.runtime.reload(), 5)
    assert len(seen) == 2


async def test_a_reload_from_with_session_is_the_runtime_reload(tmp_path: Path) -> None:
    """Codex R09: the replaced-session context's ``reload`` is the common rebuild."""

    rig = await _Rig.create(tmp_path)

    register_provider("fixture-663-r09", rig._stream)
    try:
        swapped: list[AgentHarness] = []

        async def with_session(ctx: Any) -> None:
            swapped.append(rig.runtime.harness)
            await ctx.reload()

        await asyncio.wait_for(rig.runtime.new_session(with_session=with_session), 5)
        [after_swap] = swapped
        # The reload tore down the harness the swap built and built another one.
        assert after_swap.disposed
        assert rig.runtime.harness is not after_swap and not rig.runtime.harness.disposed
        assert [seed is not None for _f, seed, _t in rig.builds] == [False, True]
        # And left the process-wide provider registry alone.
        assert "fixture-663-r09" in get_registered_providers()
    finally:
        unregister_provider("fixture-663-r09")


# --- R02 / V3: an interruption during the teardown --------------------------------------


@pytest.mark.parametrize("kind", INTERRUPTS)
async def test_an_interruption_during_the_teardown_stops(tmp_path: Path, kind: type[BaseException]) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness

    def cleanup() -> None:
        raise kind("during cleanup")

    old.hooks.add_cleanup(cleanup)
    with pytest.raises(kind):
        await rig.runtime.reload()
    assert rig.runtime.stopped == STOPPED_MESSAGE
    assert rig.runtime.harness is old and old.turns_held == STOPPED_MESSAGE
    assert rig.builds == []
    await _refuses(rig, old)


async def test_a_cancel_in_session_shutdown_tears_nothing_down(tmp_path: Path) -> None:
    """Before the teardown begins, an interruption leaves the session as it was."""

    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness

    async def on_shutdown(_event: Any, _ctx: Any) -> None:
        raise asyncio.CancelledError

    old.hooks.on("session_shutdown", on_shutdown)
    with pytest.raises(asyncio.CancelledError):
        await rig.runtime.reload()
    assert rig.runtime.stopped is None and not old.disposed
    await old.prompt("run it")
    assert rig.ran == ["run"]


# --- R04: "kept" only once the restored harness is settled and bound --------------------


def _restore_breaks(rig: _Rig, how: str) -> dict[str, Any]:
    """Make the build that puts the session back fail after its install, at ``how``."""

    holder: dict[str, Any] = {"bound": rig.runtime.harness}
    real = rig.factory

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        harness = await real(session, reload_seed=reload_seed)
        if how == "tools" and reload_seed is not None and reload_seed.restore:

            def broken(*_a: Any, **_k: Any) -> None:
                raise RuntimeError("settle tools fail")

            harness.restore_active_tools = broken  # type: ignore[method-assign]
        return harness

    async def rebind(harness: AgentHarness, _reason: str) -> None:
        if how == "rebind":
            raise RuntimeError("rebind fail")
        holder["bound"] = harness

    async def after_start(_harness: AgentHarness, _reason: str) -> None:
        if how == "cancel":
            raise asyncio.CancelledError

    rig.runtime._create_harness = factory
    rig.runtime.set_rebind_session(rebind)
    rig.runtime.set_after_session_start(after_start)
    rig.fail = _once(RuntimeError("first build fails"))
    return holder


@pytest.mark.parametrize("how", ["tools", "rebind"])
async def test_a_restore_whose_settlement_fails_is_stopped(tmp_path: Path, how: str) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    holder = _restore_breaks(rig, how)
    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()

    err = caught.value
    assert err.state == "stopped" and str(err).endswith(STOPPED_MESSAGE)
    assert isinstance(err.restore_error, RuntimeError)
    assert rig.runtime.stopped == STOPPED_MESSAGE
    # The restored harness and the torn-down one a surface still held both refuse.
    restored = rig.runtime.harness
    assert restored is not old and holder["bound"] is old
    for harness in (restored, old):
        assert harness.disposed and harness.turns_held == STOPPED_MESSAGE
        await _refuses(rig, harness)

    # A stop ends as any other does: the next reload that works.
    rig.runtime.set_rebind_session(lambda h, _r: _bind(holder, h))
    rig.runtime._create_harness = rig.factory
    rig.fail = lambda _s, _seed: None
    await rig.runtime.reload()
    assert rig.runtime.stopped is None and restored.disposed
    await rig.runtime.harness.prompt("run it")
    assert rig.ran == ["run"]


async def _bind(holder: dict[str, Any], harness: AgentHarness) -> None:
    holder["bound"] = harness


async def test_a_cancel_in_the_restore_settlement_ends_stopped(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    _restore_breaks(rig, "cancel")
    with pytest.raises(asyncio.CancelledError):
        await rig.runtime.reload()
    assert rig.runtime.stopped == STOPPED_MESSAGE
    for harness in (rig.runtime.harness, old):
        await _refuses(rig, harness)


# --- R05 / R06 / V4: the restore puts back what ran -------------------------------------


async def test_the_restore_puts_back_the_exact_tools_and_policy(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    saved = ToolPolicy(saved_off=frozenset({"run"}))

    def harness_with_policy(session: Session) -> AgentHarness:
        built = rig.harness(session)
        built.restore_active_tools(None, policy=saved)
        return built

    rig.runtime._harness = harness_with_policy(rig.session)

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        rig.builds.append((session.session_file, reload_seed, None))
        if reload_seed is not None and not reload_seed.restore:
            raise RuntimeError("first build fails")
        return harness_with_policy(session)

    rig.runtime._create_harness = factory
    live = rig.runtime.harness
    # "run" is off by setting; this session turned it on (a panel's apply).
    assert "run" not in live._action_get_active_tools()
    live.set_tool_policy(replace(saved, saved_off=frozenset()), turn_on=["run"])
    policy_before = live.tool_policy
    active_before = live.state.active_tool_names

    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()
    assert caught.value.state == "kept"
    restored = rig.runtime.harness
    assert restored is not live
    assert restored.tool_policy == policy_before
    assert restored.state.active_tool_names == active_before
    await restored.prompt("run it")
    assert rig.ran == ["run"]


async def test_the_restore_keeps_an_empty_selection_empty(tmp_path: Path) -> None:
    """Codex R05: ``[]`` came back as ``["run"]``, and that tool ran after "kept"."""

    rig = await _Rig.create(tmp_path)
    await rig.runtime.harness.set_active_tools([])
    rig.fail = _once(RuntimeError("first build fails"))
    with pytest.raises(RebuildFailedError):
        await rig.runtime.reload()
    restored = rig.runtime.harness
    assert restored.state.active_tool_names == []
    await restored.prompt("run it")
    assert rig.ran == []


async def test_the_restored_level_is_clamped_to_the_model_it_runs(tmp_path: Path) -> None:
    """Verify V4: a level the restored build's model cannot run is not kept."""

    rig = await _Rig.create(tmp_path)
    rig.runtime.harness.state.thinking_level = "high"
    plain = Model(id="plain", provider="fixture")

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        if reload_seed is not None and not reload_seed.restore:
            raise RuntimeError("first build fails")
        return AgentHarness(
            AgentHarnessOptions(model=plain, stream_fn=rig._stream, session=session, cwd=str(rig.tmp))
        )

    rig.runtime._create_harness = factory
    with pytest.raises(RebuildFailedError):
        await rig.runtime.reload()
    assert rig.runtime.harness.current_model is plain
    assert rig.runtime.harness.state.thinking_level == "off"


# --- R08: a settings write made while the rebuild ran is not reverted -------------------


async def test_a_settings_write_made_during_a_failed_rebuild_survives_it(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        if reload_seed is not None and not reload_seed.restore:
            entered.set()
            await release.wait()
            raise RuntimeError("failed build")
        return rig.harness(session)

    rig.storage.with_lock("global", lambda _c: '{"theme": "from-file", "defaultModel": "file-model"}')
    rig.runtime._create_harness = factory
    pending = asyncio.create_task(rig.runtime.reload())
    await entered.wait()
    rig.settings.set_theme("user-write")
    await rig.settings.flush()
    release.set()
    with pytest.raises(RebuildFailedError) as caught:
        await asyncio.wait_for(pending, 5)
    assert caught.value.state == "kept"
    # The write made meanwhile stands, on disk and in memory; what the failed
    # re-read alone brought in (defaultModel) is gone again.
    assert rig.settings.get_theme() == "user-write"
    assert rig.settings.get_global_settings().theme == "user-write"
    assert rig.settings.get_default_model() is None
    assert '"user-write"' in (rig.storage.read("global") or "")
